"""Does what arrived match what was sent?

rsync verifies in flight, not at rest. A file that transferred correctly and
then met a bad block on the scratch array passes every check the transfer tool
makes, and ingest is the last moment where catching that is cheap.
"""

from __future__ import annotations

import os

import pytest
from wl_sync.session import SessionId

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.verify import Integrity, verify_session
from wl_preproc.synth.recipe import CI_RECIPE, STIM_RECIPE
from wl_preproc.synth.session import generate_session


@pytest.fixture
def session(tmp_path):
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, SessionId.parse(CI_RECIPE.session_id))
    manifest = SessionManifest.from_yaml(layout.manifest_path.read_text())
    return layout, manifest


def test_an_untouched_session_verifies(session):
    layout, manifest = session
    integrity, mismatches = verify_session(layout, manifest)

    assert integrity is Integrity.VERIFIED
    assert mismatches == []


def test_a_truncated_file_is_caught(session):
    """The pathology the generator already injects, exercised end to end."""
    layout, manifest = session
    target = layout.system_dir("spikeglx") / f"{CI_RECIPE.session_id}_imec0.ap.bin"
    original = target.read_bytes()
    target.write_bytes(original[: len(original) // 2])

    integrity, mismatches = verify_session(layout, manifest)

    assert mismatches
    assert {m.problem for m in mismatches} == {"size"}
    assert mismatches[0].system == "spikeglx"


def test_a_same_size_corruption_is_caught_by_the_hash(session):
    """Size alone would pass this. It is the case that justifies hashing at all
    rather than comparing sizes and calling it verified."""
    layout, manifest = session
    target = layout.system_dir("spikeglx") / f"{CI_RECIPE.session_id}_imec0.ap.meta"
    payload = bytearray(target.read_bytes())
    payload[0] ^= 0xFF
    target.write_bytes(bytes(payload))

    _, mismatches = verify_session(layout, manifest)

    assert [m.problem for m in mismatches] == ["blake3"]


def test_a_file_declared_but_absent_is_caught(session):
    layout, manifest = session
    (layout.system_dir("spikeglx") / f"{CI_RECIPE.session_id}_imec0.ap.meta").unlink()

    _, mismatches = verify_session(layout, manifest)

    assert [m.problem for m in mismatches] == ["missing"]


def test_an_empty_marker_yields_declared_only_rather_than_verified(session):
    """Spec section 5.2. The record must never claim a check that did not run."""
    layout, manifest = session
    for system in CI_RECIPE.systems:
        layout.done_marker(system).write_text("")

    integrity, mismatches = verify_session(layout, manifest)

    assert integrity is Integrity.DECLARED_ONLY
    assert mismatches == []


def test_disabling_verification_says_skipped_not_verified(session):
    layout, manifest = session
    integrity, mismatches = verify_session(layout, manifest, enabled=False)

    assert integrity is Integrity.SKIPPED
    assert mismatches == []


def test_one_empty_marker_among_several_downgrades_the_whole_session(session):
    """A session is verified only if everything in it was. Reporting VERIFIED
    when one system carried no integrity data would be the record claiming more
    than was checked."""
    layout, manifest = session
    layout.done_marker("bcam").write_text("")

    integrity, _ = verify_session(layout, manifest)

    assert integrity is Integrity.DECLARED_ONLY


# --- Beyond the brief: the filesystem calls verify_session makes on a file it
# has already located (candidate.is_file(), .stat(), and blake3_file's
# open-and-read) are raw or EACCES-exposed, per the defect class closed across
# sentinel.py and discover.py in the two rounds before this one. verify_session
# runs only on complete sessions, so the race window is smaller than
# discover.py's, but a permissions fault is still entirely possible, and the
# two tests below prove it produces a Mismatch rather than aborting the scan.
# Both use real os.chmod, never monkeypatch: on Python 3.13, glob.py captures
# os.scandir as a staticmethod at import time, so a Path-method patch can
# silently miss the walk it means to test, per this task's own instructions.


@pytest.fixture
def stim_session(tmp_path):
    """STIM_RECIPE (syncbox, rhs) is the only recipe with a nested
    subdirectory: rhs writes its files one level below the DONE marker that
    declares them (`rhs/<session_id>_rhs/*.dat`, `rhs/DONE`). That layout is
    exactly what the directory-permission test below needs: denying execute
    on the nested directory blocks every file inside it without also
    blocking the sibling DONE marker one level up."""
    generate_session(tmp_path, STIM_RECIPE)
    layout = SessionLayout(tmp_path, SessionId.parse(STIM_RECIPE.session_id))
    manifest = SessionManifest.from_yaml(layout.manifest_path.read_text())
    return layout, manifest


def test_a_permission_fault_opening_a_file_is_reported_not_raised(session):
    """POSIX stat() only needs execute permission on the containing
    directory, not read permission on the file itself, so chmod-ing a file
    to 0o000 leaves candidate.is_file() and candidate.stat() both
    succeeding — confirmed empirically before writing this test — and
    isolates blake3_file's open()+read(), the guard this test exists to
    exercise. Restoring permissions happens in `finally`, before any
    assertion, so a failing assertion here can never leave behind a file
    later tests or the tmp_path cleanup cannot touch."""
    layout, manifest = session
    target = layout.system_dir("spikeglx") / f"{CI_RECIPE.session_id}_imec0.ap.meta"
    original_mode = target.stat().st_mode
    os.chmod(target, 0o000)
    try:
        integrity, mismatches = verify_session(layout, manifest)
    finally:
        os.chmod(target, original_mode)

    assert integrity is Integrity.VERIFIED
    assert [m.problem for m in mismatches] == ["unreadable"]
    assert mismatches[0].system == "spikeglx"


def test_a_permission_fault_reaching_a_nested_file_is_reported_not_raised(stim_session):
    """Companion to the test above, isolating the other guarded call:
    candidate.is_file() itself raising, rather than succeeding and letting
    blake3_file fail later. Denying execute on the nested `<session_id>_rhs`
    directory blocks path resolution into every file below it — confirmed
    empirically that is_file() raises PermissionError there while a sibling
    file outside the directory stays fully readable, which is exactly the
    DONE marker's situation. Every file rhs declared should be affected, not
    just one, so this also checks the full count rather than only the set of
    problem strings."""
    layout, manifest = stim_session
    rhs_dir = layout.system_dir("rhs")
    nested = rhs_dir / f"{STIM_RECIPE.session_id}_rhs"
    declared_file_count = len(list(nested.iterdir()))
    original_mode = nested.stat().st_mode
    os.chmod(nested, 0o000)
    try:
        integrity, mismatches = verify_session(layout, manifest)
    finally:
        os.chmod(nested, original_mode)

    assert integrity is Integrity.VERIFIED
    assert len(mismatches) == declared_file_count
    assert {m.problem for m in mismatches} == {"unreadable"}
    assert all(m.system == "rhs" for m in mismatches)
