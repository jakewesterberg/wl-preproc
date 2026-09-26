"""Byte reconstruction against the rig's own digest.

Named `test_verify_reconstruction` rather than `test_verify`: `tests/ingest/`
already has a `test_verify.py` (for `ingest/verify.py`, a different module that
verifies at rest -- rehashes what landed against the DONE marker, no
reconstruction involved), the test directories are not packages, and pytest
cannot tell two same-named modules apart -- it fails collection for the WHOLE
suite rather than for the one file. The brief named this `test_verify.py`; see
`tests/timebase/test_coverage_rules.py` for the same rename for the same
reason.
"""

import blake3
import numpy as np
import pytest
import zarr

from wl_preproc.archive.verify import (
    hash_reconstruction,
    iter_reconstruct,
    reconstruct,
    stored_paths,
    stored_size,
    verify_against,
    verify_store,
)
from wl_preproc.archive.store import write_store
from wl_preproc.contracts.paths import DONE_MARKER_FILENAME
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def _archived(tmp_path):
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    return session, write_store(session, tmp_path / "out")


def test_every_file_reconstructs_to_its_original_bytes(tmp_path):
    session, result = _archived(tmp_path)
    verdicts = verify_store(result.path, session)
    assert verdicts
    assert all(v.matched for v in verdicts), [v for v in verdicts if not v.matched]


def test_reconstruction_is_byte_identical_not_merely_equal_samples(tmp_path):
    session, result = _archived(tmp_path)
    original = next(session.rglob("*_imec0.ap.bin"))
    rebuilt = reconstruct(result.path, str(original.relative_to(session)))
    assert rebuilt == original.read_bytes()


def test_a_corrupted_artifact_fails_verification(tmp_path):
    """A test that only ever sees a good artifact proves the happy path and
    nothing about the guard."""
    session, result = _archived(tmp_path)
    victim = next(
        p for p in sorted((result.path / "streams").rglob("*")) if p.is_file()
    )
    victim.write_bytes(b"\x00" * victim.stat().st_size)

    verdicts = verify_store(result.path, session)
    assert any(not v.matched for v in verdicts)


def test_a_corrupted_artifacts_verdict_explains_why(tmp_path):
    """The previous test only asserts `matched=False`. A verdict on a
    reconstruction crash and a verdict on a genuine hash disagreement both
    look like `matched=False, actual=<some string>` unless the crash case is
    pinned to something recognisable on purpose -- `verify_store`'s own
    docstring claims `actual` carries that diagnostic, and nothing before
    this test checked the claim. Without a test, the
    `"error reconstructing file"` convention is just prose: reword it or
    drop it and nothing would notice, and a future caller trying to branch
    on it would have no guarantee it still says what the docstring says."""
    session, result = _archived(tmp_path)
    victim = next(
        p for p in sorted((result.path / "streams").rglob("*")) if p.is_file()
    )
    victim.write_bytes(b"\x00" * victim.stat().st_size)

    verdicts = verify_store(result.path, session)
    crashed = [v for v in verdicts if not v.matched]
    assert crashed
    assert all("error reconstructing file" in v.actual for v in crashed)


def test_a_transposed_reconstruction_is_caught(tmp_path):
    """The case comparing samples to samples cannot catch: identical values,
    wrong layout. Rewrite one array transposed and assert the digest differs."""
    session, result = _archived(tmp_path)
    original = next(session.rglob("*_imec0.ap.bin"))
    relative = str(original.relative_to(session))

    root = zarr.open(str(result.path), mode="a")
    name = original.name
    data = root["streams"][name][:]
    del root["streams"][name]
    swapped = root["streams"].create_dataset(name, data=np.ascontiguousarray(data[:, ::-1]))
    swapped.attrs["source"] = relative

    assert reconstruct(result.path, relative) != original.read_bytes()
    assert any(not v.matched for v in verify_store(result.path, session))


def test_the_roundtrip_holds_for_intan_too(tmp_path):
    """Design spec section 9: the roundtrip "must run on every emitted system,
    not only SpikeGLX". Intan reaches `layout.py` by a different route -- its
    channel count is derived from time.dat rather than read from a sidecar -- so
    SpikeGLX passing says nothing about it."""
    from wl_preproc.synth.recipe import STIM_RECIPE

    generate_session(tmp_path / "in", STIM_RECIPE)
    session = tmp_path / "in" / STIM_RECIPE.session_id
    result = write_store(session, tmp_path / "out")

    verdicts = verify_store(result.path, session)
    assert verdicts
    assert all(v.matched for v in verdicts), [v for v in verdicts if not v.matched]

    amplifier = next(session.rglob("amplifier.dat"))
    rebuilt = reconstruct(result.path, str(amplifier.relative_to(session)))
    assert rebuilt == amplifier.read_bytes()


def test_a_missing_done_marker_entry_is_an_error_not_a_pass(tmp_path):
    """No reference digest must never read as 'verified'."""
    session, result = _archived(tmp_path)
    for marker in session.rglob(DONE_MARKER_FILENAME):
        marker.unlink()
    with pytest.raises(ValueError):
        verify_store(result.path, session)


def _hand_built_store(path):
    """A store whose chunking the test controls: `write_store` leaves verbatim
    chunking to zarr, so the only way to make a verbatim file span several
    chunks, with a partial last one, is to write the store by hand."""
    root = zarr.open(str(path), mode="w")
    data = np.arange(10 * 3, dtype="<i2").reshape(10, 3)
    streams = root.create_group("streams")
    streams.create_dataset("x.ap.bin", data=data, chunks=(3, 3))
    streams["x.ap.bin"].attrs["source"] = "sys/x.ap.bin"
    text = b"abcdefghijklmnopqrstuvwxyz0123456789"  # 36 bytes
    verbatim = root.create_group("verbatim")
    verbatim.create_dataset("sys/notes.txt", data=np.frombuffer(text, dtype=np.uint8), chunks=(7,))
    verbatim.create_dataset("empty.txt", data=np.frombuffer(b"", dtype=np.uint8))
    return data.tobytes(), text


def test_a_stream_rebuilds_chunk_by_chunk_including_a_final_partial_chunk(tmp_path):
    stream_bytes, _ = _hand_built_store(tmp_path / "s.zarr")
    blocks = list(iter_reconstruct(tmp_path / "s.zarr", "sys/x.ap.bin"))
    assert len(blocks) == 4  # 10 rows in chunks of 3: 3 + 3 + 3 + 1
    assert b"".join(blocks) == stream_bytes


def test_a_verbatim_file_rebuilds_chunk_by_chunk(tmp_path):
    _, text = _hand_built_store(tmp_path / "s.zarr")
    blocks = list(iter_reconstruct(tmp_path / "s.zarr", "sys/notes.txt"))
    assert len(blocks) == 6  # 36 bytes in chunks of 7: five whole, one of 1
    assert b"".join(blocks) == text


def test_a_zero_length_file_rebuilds_to_zero_bytes(tmp_path):
    _hand_built_store(tmp_path / "s.zarr")
    assert reconstruct(tmp_path / "s.zarr", "empty.txt") == b""


def test_the_chunked_hash_equals_blake3_of_the_whole_file(tmp_path):
    session, result = _archived(tmp_path)
    for path in sorted(p for p in session.rglob("*") if p.is_file()):
        relative = str(path.relative_to(session))
        whole = blake3.blake3(path.read_bytes()).hexdigest()
        assert hash_reconstruction(result.path, relative) == whole, relative


def test_stored_paths_lists_every_file_the_session_had(tmp_path):
    session, result = _archived(tmp_path)
    expected = sorted(str(p.relative_to(session)) for p in session.rglob("*") if p.is_file())
    assert stored_paths(result.path) == expected


def test_stored_size_is_the_sessions_size_without_decompressing(tmp_path):
    session, result = _archived(tmp_path)
    assert stored_size(result.path) == sum(
        p.stat().st_size for p in session.rglob("*") if p.is_file()
    )


def test_a_missing_path_is_a_verdict_not_a_crash(tmp_path):
    """`iter_reconstruct` is a generator and raises lazily; `verify_against`
    must iterate inside its own `try`, or this escapes as a `KeyError`."""
    _session, result = _archived(tmp_path)
    verdicts = verify_against(result.path, {"no/such/file": "0" * 64})
    assert len(verdicts) == 1
    assert not verdicts[0].matched
    assert "error reconstructing file: KeyError" in verdicts[0].actual
