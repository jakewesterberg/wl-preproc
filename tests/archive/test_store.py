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
    which reading either file whole (`np.fromfile`, `read_bytes`) cannot."""
    import tracemalloc

    from wl_preproc.archive import store

    monkeypatch.setattr(store, "_CHUNK_SAMPLES", 4096)
    monkeypatch.setattr(store, "_VERBATIM_CHUNK_BYTES", 64 * 1024, raising=False)
    session = _big_session(
        tmp_path, n_samples=2_500_000, n_channels=4, verbatim_bytes=20_000_000
    )

    tracemalloc.start()
    try:
        write_store(session, tmp_path / "out")
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 8 * _MIB, f"peak {peak / _MIB:.1f} MiB while writing 2 x ~20 MB files"


def test_a_streamed_store_rebuilds_every_file_byte_for_byte(tmp_path, monkeypatch):
    """Chunk by chunk in, chunk by chunk out, including each file's partial
    final chunk -- and the chunk shapes are the ones asked for."""
    import blake3

    from wl_preproc.archive import store
    from wl_preproc.archive.verify import verify_against

    monkeypatch.setattr(store, "_CHUNK_SAMPLES", 4096)
    monkeypatch.setattr(store, "_VERBATIM_CHUNK_BYTES", 64 * 1024, raising=False)
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
