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

import numpy as np
import pytest
import zarr

from wl_preproc.archive.store import write_store
from wl_preproc.archive.verify import reconstruct, verify_store
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
