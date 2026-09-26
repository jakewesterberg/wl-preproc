import shutil

import numpy as np
import zarr

from wl_preproc.archive.layout import bulk_streams
from wl_preproc.archive.store import manifest_digest, write_store
from wl_preproc.contracts.paths import DONE_MARKER_FILENAME
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def test_every_bulk_stream_becomes_an_array(tmp_path):
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    result = write_store(session, tmp_path / "out")

    root = zarr.open(str(result.path), mode="r")
    expected = {s.path.name for s in bulk_streams(session)}
    assert set(root["streams"].array_keys()) == expected


def test_a_non_stream_file_is_stored_verbatim(tmp_path):
    """Verbatim, not re-encoded: the manifest and the DONE markers are what
    verification reads its reference digests out of."""
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    result = write_store(session, tmp_path / "out")

    original = next(session.rglob(DONE_MARKER_FILENAME))
    relative = str(original.relative_to(session))
    root = zarr.open(str(result.path), mode="r")
    stored = bytes(root["verbatim"][relative][:])
    assert stored == original.read_bytes()


def test_the_store_is_smaller_than_the_session(tmp_path):
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    raw = sum(p.stat().st_size for p in session.rglob("*") if p.is_file())
    result = write_store(session, tmp_path / "out")
    assert result.compressed_bytes < raw
    assert result.codec == "zstd"


def test_the_manifest_digest_is_stable_across_a_copy(tmp_path):
    """This is design spec section 3's confirm step: manifest_digest is
    recomputed against the *same bytes* after a plain copy, never against a
    second independent compression -- numcodecs.Blosc's compressed output is
    not reproducible across separate write_store() calls (see
    manifest_digest's own docstring, and design spec section 10 item 4), so
    that stronger property is deliberately not asserted here."""
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    result = write_store(session, tmp_path / "out")

    copy_path = tmp_path / "copy" / result.path.name
    shutil.copytree(result.path, copy_path)

    assert manifest_digest(copy_path) == result.manifest_digest
    assert manifest_digest(copy_path) == manifest_digest(result.path)


def test_a_changed_byte_changes_the_manifest_digest(tmp_path):
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    result = write_store(session, tmp_path / "out")
    before = manifest_digest(result.path)

    victim = next(p for p in sorted(result.path.rglob("*")) if p.is_file())
    victim.write_bytes(victim.read_bytes() + b"\x00")
    assert manifest_digest(result.path) != before


# -- Streaming: the writer never holds a whole file ---------------------------

_MIB = 1 << 20


def _big_session(root, *, n_samples, n_channels, verbatim_bytes):
    """A session with one SpikeGLX-shaped bulk stream and one large verbatim
    file, both deliberately NOT a whole number of the (shrunk) chunk sizes, so
    the last chunk of each is partial."""
    session = root / "big"
    system = session / "spikeglx"
    system.mkdir(parents=True)
    rng = np.random.default_rng(0)
    samples = rng.integers(-2000, 2000, size=(n_samples, n_channels), dtype=np.int16)
    samples.tofile(system / "run_g0_t0.imec0.ap.bin")
    (system / "run_g0_t0.imec0.ap.meta").write_text(
        f"nSavedChans={n_channels}\n", encoding="utf-8"
    )
    (session / "stim.dat").write_bytes(rng.bytes(verbatim_bytes))
    return session


def test_writing_a_store_never_holds_a_whole_file(tmp_path, monkeypatch):
    """A two-hour Neuropixels 1.0 AP file is ~166 GB. Chunk sizes are shrunk
    here so a ~20 MB stream and a ~20 MB verbatim file each span hundreds of
    chunks; the Python-heap peak while writing must stay within a few chunks,
    which reading either file whole (`np.fromfile`, `read_bytes`) cannot.

    `manifest_digest` is stubbed out because its own 4 MiB read buffer
    (`contracts/done.py::blake3_file`) would otherwise be most of the peak,
    and a change to that unrelated buffer should not fail this test. What is
    resident is not visible to `tracemalloc`; the writer reads through one
    reused buffer rather than a memory map so that it is bounded too."""
    import tracemalloc

    from wl_preproc.archive import store

    monkeypatch.setattr(store, "_CHUNK_SAMPLES", 4096)
    monkeypatch.setattr(store, "_VERBATIM_CHUNK_BYTES", 64 * 1024)
    monkeypatch.setattr(store, "manifest_digest", lambda path, exclude=frozenset(): "0" * 64)
    session = _big_session(
        tmp_path, n_samples=2_500_000, n_channels=4, verbatim_bytes=20_000_000
    )

    tracemalloc.start()
    try:
        write_store(session, tmp_path / "out")
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 2 * _MIB, f"peak {peak / _MIB:.1f} MiB while writing 2 x ~20 MB files"


def _stat_lying_about(monkeypatch, name, delta):
    """Make `Path.stat()` report `name` as `delta` bytes larger than it is --
    the state a file is in when it shrinks (delta > 0) or grows (delta < 0)
    between the writer sizing it and reading it."""
    import os
    from pathlib import Path

    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        st = real_stat(self, *args, **kwargs)
        if self.name != name:
            return st
        fields = list(st)
        fields[6] = st.st_size + delta  # st_size
        return os.stat_result(fields)

    monkeypatch.setattr(Path, "stat", stat)


def test_a_verbatim_file_that_shrinks_mid_write_is_an_error_not_a_zero_filled_tail(
    tmp_path, monkeypatch
):
    """Found in review: a file that shrank after being sized was stored with
    its missing tail read back as zeros, silently -- bytes the file never had
    -- and a file no DONE marker names is never checked against a rig digest."""
    import pytest

    session = tmp_path / "s"
    session.mkdir()
    (session / "notes.txt").write_bytes(b"x" * 300_001)
    _stat_lying_about(monkeypatch, "notes.txt", +1000)

    with pytest.raises(OSError, match="shrank"):
        write_store(session, tmp_path / "out")


def test_a_verbatim_file_that_grows_mid_write_is_an_error(tmp_path, monkeypatch):
    import pytest

    session = tmp_path / "s"
    session.mkdir()
    (session / "notes.txt").write_bytes(b"x" * 300_001)
    _stat_lying_about(monkeypatch, "notes.txt", -1000)

    with pytest.raises(OSError, match="grew"):
        write_store(session, tmp_path / "out")


def test_a_stream_that_shrinks_mid_write_is_an_error_not_a_crash(tmp_path, monkeypatch):
    """Found in review: with a memory map, a stream that shrank (or an I/O
    fault) killed the whole process with SIGBUS, past the daemon's
    per-session `except Exception`. Reading through a buffer makes it an
    ordinary `OSError`, caught per session."""
    import pytest

    session = _big_session(tmp_path, n_samples=10_000, n_channels=4, verbatim_bytes=10)
    # Two whole samples more than the file holds, so the layout still divides.
    _stat_lying_about(monkeypatch, "run_g0_t0.imec0.ap.bin", +2 * 4 * 2)

    with pytest.raises(OSError, match="shrank"):
        write_store(session, tmp_path / "out")


def test_a_very_wide_stream_gets_shorter_chunks():
    """A chunk is rows x channels x 2 bytes, and Blosc refuses a buffer over
    2**31 - 1 bytes: at 2**20 rows, 1024 channels is exactly 2**31. Rows are
    capped so no chunk exceeds `_MAX_CHUNK_BYTES` (1 GiB, half the ceiling),
    which is met above 512 channels; Neuropixels' 385 keep the full 2**20.
    Asserted against Blosc's own limit, not only the module's constant, so a
    wrong constant fails here."""
    from wl_preproc.archive import store

    blosc_limit = 2**31 - 1
    assert store._MAX_CHUNK_BYTES < blosc_limit
    assert store._stream_chunk_rows(385) == store._CHUNK_SAMPLES
    assert store._stream_chunk_rows(512) == store._CHUNK_SAMPLES
    assert store._stream_chunk_rows(513) < store._CHUNK_SAMPLES
    for channels in (513, 1024, 4096):
        assert store._stream_chunk_rows(channels) * channels * 2 < blosc_limit


def test_a_streamed_store_rebuilds_every_file_byte_for_byte(tmp_path, monkeypatch):
    """Chunk by chunk in, chunk by chunk out, including each file's partial
    final chunk -- and the chunk shapes are the ones asked for."""
    import blake3

    from wl_preproc.archive import store
    from wl_preproc.archive.verify import verify_against

    monkeypatch.setattr(store, "_CHUNK_SAMPLES", 4096)
    monkeypatch.setattr(store, "_VERBATIM_CHUNK_BYTES", 64 * 1024)
    session = _big_session(tmp_path, n_samples=50_001, n_channels=3, verbatim_bytes=300_001)

    result = write_store(session, tmp_path / "out")

    expected = {
        str(p.relative_to(session)): blake3.blake3(p.read_bytes()).hexdigest()
        for p in session.rglob("*")
        if p.is_file()
    }
    verdicts = verify_against(result.path, expected)
    assert all(v.matched for v in verdicts), [v for v in verdicts if not v.matched]
    root = zarr.open(str(result.path), mode="r")
    assert root["streams"]["run_g0_t0.imec0.ap.bin"].chunks == (4096, 3)
    assert root["verbatim"]["stim.dat"].chunks == (64 * 1024,)


def test_empty_files_and_empty_streams_are_stored(tmp_path):
    """Zero bytes is a real file: an empty verbatim file and a stream with no
    samples both round-trip to zero bytes."""
    from wl_preproc.archive.verify import reconstruct

    session = tmp_path / "empty"
    system = session / "spikeglx"
    system.mkdir(parents=True)
    (system / "run_g0_t0.imec0.ap.bin").write_bytes(b"")
    (system / "run_g0_t0.imec0.ap.meta").write_text("nSavedChans=4\n", encoding="utf-8")
    (session / "notes.txt").write_bytes(b"")

    result = write_store(session, tmp_path / "out")

    assert reconstruct(result.path, "spikeglx/run_g0_t0.imec0.ap.bin") == b""
    assert reconstruct(result.path, "notes.txt") == b""
