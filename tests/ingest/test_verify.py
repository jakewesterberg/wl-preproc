"""Does what arrived match what was sent?

rsync verifies in flight, not at rest. A file that transferred correctly and
then met a bad block on the scratch array passes every check the transfer tool
makes, and ingest is the last moment where catching that is cheap.
"""

from __future__ import annotations

import os

import pytest
import yaml
from wl_sync.session import SessionId

from wl_preproc.contracts.done import blake3_file
from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.sentinel import MarkerState, read_marker
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


@pytest.mark.parametrize("emptied_system", ["bcam", "syncbox"])
def test_one_empty_marker_among_several_downgrades_the_whole_session(session, emptied_system):
    """A session is verified only if everything in it was. Reporting VERIFIED
    when one system carried no integrity data would be the record claiming more
    than was checked.

    Parametrized over both ends of verify_session's `sorted(topology.items())`
    iteration for CI_RECIPE: sorted alphabetically that is `bcam, spikeglx,
    syncbox`, so "bcam" empties the *first* system checked and "syncbox" the
    *last*. The first-only version of this test could pass by accident of
    `saw_empty` merely being set early and never revisited — "syncbox" is the
    late-iteration case that actually needs `saw_empty` to still be True after
    two unrelated systems were processed first.

    A size mismatch is planted on spikeglx — the system in between the two
    parametrized values in iteration order — so this also proves the other
    direction: a mismatch already appended to the list before `saw_empty` is
    ever set (the "bcam" case, where the empty marker is seen *first*) is not
    lost, and a mismatch appended *after* `saw_empty` is already set (the
    "syncbox" case) is not suppressed either. `saw_empty` is consulted exactly
    once, after the loop finishes, so neither direction should be able to
    interfere with the mismatch list at all — this is the test that would
    catch it if a future edit made that untrue."""
    layout, manifest = session
    target = layout.system_dir("spikeglx") / f"{CI_RECIPE.session_id}_imec0.ap.bin"
    original = target.read_bytes()
    target.write_bytes(original[: len(original) // 2])
    layout.done_marker(emptied_system).write_text("")

    integrity, mismatches = verify_session(layout, manifest)

    assert integrity is Integrity.DECLARED_ONLY
    assert [m.problem for m in mismatches] == ["size"]
    assert mismatches[0].system == "spikeglx"


# --- Beyond the brief: `entry.path` is joined onto system_dir unchecked
# (`system_dir / entry.path`), so a marker declaring a `..` or absolute path
# would otherwise have this function read, hash, and report VERIFIED on a file
# outside the session tree entirely. The real fix is a field_validator on
# FileEntry.path (contracts/done.py) that makes such a FileEntry impossible to
# construct through pydantic — but the marker on disk is written by an
# external transfer process, not by this codebase, so what needs proving here
# is what actually happens when one shows up anyway: the whole marker fails to
# parse, which read_marker's existing broad `except Exception` already turns
# into INVALID.
#
# Building the test below surfaced a second, independent bug, pre-existing in
# the brief's own code and unrelated to path containment: discover_topology
# maps a declared system's INVALID marker to SystemState.PENDING — the same
# state an ABSENT marker gets, because discover.py's own job (spec section 7)
# has no reason to tell "not finished yet" apart from "finished and wrote
# something unreadable." verify_session's loop skipped PENDING outright, so
# before the fix in verify.py, THIS test failed: the session came back
# VERIFIED with an empty mismatch list rather than DECLARED_ONLY, silently
# treating an unparseable marker as if it were simply pending transfer. The
# dedicated regression test right after this one reproduces that bug in
# isolation, with a plain corrupt-YAML marker and no path involved at all, so
# the fix is proven independently of the containment scenario that happened
# to surface it.


@pytest.mark.parametrize(
    "malicious_path", ["../../../etc/passwd", "/etc/passwd"], ids=["traversal", "absolute"]
)
def test_a_marker_declaring_an_escaping_path_is_rejected_before_any_file_is_read(
    session, malicious_path
):
    """End-to-end companion to contracts/test_done.py's two contract-level
    rejection tests: this proves what verify_session actually does when a
    marker on disk (not a FileEntry built in Python) carries one of the two
    shapes. `read_marker` is asserted directly, not just inferred from
    verify_session's return value, because "no file is read" is a claim about
    marker.files never being reached at all -- and INVALID is exactly the
    state that makes that true, one level below verify_session itself."""
    layout, manifest = session
    marker_path = layout.done_marker("spikeglx")
    raw = yaml.safe_load(marker_path.read_text())
    assert raw["files"], "the generator must have declared at least one real file"
    raw["files"][0]["path"] = malicious_path
    marker_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    assert read_marker(layout, "spikeglx")[0] is MarkerState.INVALID

    integrity, mismatches = verify_session(layout, manifest)

    assert integrity is Integrity.DECLARED_ONLY
    assert mismatches == []


def test_a_corrupt_marker_downgrades_rather_than_silently_verifying(session):
    """Isolated regression test for the PENDING/INVALID gap described above,
    using the same corruption `test_sentinel.py`'s pre-existing
    `test_a_corrupt_marker_is_invalid_rather_than_absent` uses (bad YAML, no
    path involved), to prove the fix independently of path containment."""
    layout, manifest = session
    layout.done_marker("spikeglx").write_text("{{{ not yaml")

    integrity, mismatches = verify_session(layout, manifest)

    assert integrity is Integrity.DECLARED_ONLY
    assert mismatches == []


# --- Beyond the brief, round 2: FileEntry.path's contract-level validator
# (contracts/done.py) rejects `..` and absolute strings, but it sees a bare
# string with no filesystem access and no knowledge of any particular
# system_dir -- it cannot see a *symlink* planted inside a real system
# directory whose target, once resolved, is outside the tree, even though the
# declared path itself is clean and relative. This transfer runs as
# `rsync -a`, which preserves source-side symlinks verbatim unless
# `--safe-links` is passed, so a leftover convenience link on a rig -- pointing
# at a shared calibration file, an old mount, anything -- reaches this
# function with no malice at all, the same accident shape as the reversed
# `relpath` call that justified the string-level check in the first place.
# Both tests below use a real symlink, not a monkeypatch: the whole point is
# what the real filesystem does when asked to resolve one.


def test_a_symlink_escaping_the_system_directory_is_caught(session, tmp_path):
    """The declared path is clean, relative, and `..`-free -- it would pass
    FileEntry.path's own validator -- but the file it names is a symlink
    pointing entirely outside the session tree. The planted target's size and
    hash are made to match what gets declared exactly, so if the containment
    check did not run, or ran after the size/hash comparison instead of
    before it, this would read VERIFIED with an empty mismatch list, the
    exact silent failure mode the review reproduced against the pre-fix
    code."""
    layout, manifest = session
    system_dir = layout.system_dir("spikeglx")

    outside = tmp_path / "outside_the_session_tree.dat"
    payload = b"planted entirely outside system_dir, on purpose"
    outside.write_bytes(payload)

    link = system_dir / "sneaky.dat"
    link.symlink_to(outside)
    expected_resolved = link.resolve()
    assert not expected_resolved.is_relative_to(system_dir.resolve()), (
        "test setup bug: the symlink must actually resolve outside system_dir"
    )

    marker_path = layout.done_marker("spikeglx")
    raw = yaml.safe_load(marker_path.read_text())
    raw["files"].append(
        {"path": "sneaky.dat", "bytes": len(payload), "blake3": blake3_file(outside)}
    )
    marker_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    integrity, mismatches = verify_session(layout, manifest)

    assert integrity is Integrity.VERIFIED
    assert len(mismatches) == 1
    assert mismatches[0].system == "spikeglx"
    assert mismatches[0].path == "sneaky.dat"
    assert mismatches[0].problem == f"escapes_system_dir: {expected_resolved}"


def test_a_symlink_loop_is_reported_not_raised(session):
    """Found while implementing the escape check above, not asked for: proving
    resolve() is safe to call unconditionally meant checking what it does on
    every input it might realistically see, and a self-referential symlink
    pair is one of them. `Path.resolve()`'s default strict=False swallows
    OSError internally (confirmed by reading posixpath._joinrealpath's
    source, not assumed) for a missing or permission-denied path -- but a
    genuine symlink loop is the one case pathlib deliberately reports instead
    of swallowing, and it does so as RuntimeError, not OSError. Confirmed
    empirically to be a live 3.11-vs-3.13 gap the same shape as the rglob one
    discover.py already documents: on this project's 3.11 floor, a two-symlink
    loop makes resolve() raise RuntimeError, uncaught by a bare
    `except OSError` -- which would abort this entire scan over one broken
    symlink, not just misreport the one entry. On 3.13 the same loop raises
    nothing and resolve() returns the unresolved path, which falls through to
    is_file() correctly returning False. The assertion below only pins what
    is true on both: no exception escapes, and exactly one mismatch is
    reported for the one broken entry -- not the exact problem string, which
    is version-dependent by the above."""
    layout, manifest = session
    system_dir = layout.system_dir("spikeglx")

    loop_a = system_dir / "loop_a.dat"
    loop_b = system_dir / "loop_b.dat"
    loop_a.symlink_to(loop_b)
    loop_b.symlink_to(loop_a)

    marker_path = layout.done_marker("spikeglx")
    raw = yaml.safe_load(marker_path.read_text())
    raw["files"].append({"path": "loop_a.dat", "bytes": 0, "blake3": "0" * 64})
    marker_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    integrity, mismatches = verify_session(layout, manifest)

    assert integrity is Integrity.VERIFIED
    assert len(mismatches) == 1
    assert mismatches[0].system == "spikeglx"
    assert mismatches[0].path == "loop_a.dat"
    assert mismatches[0].problem == "missing" or mismatches[0].problem.startswith(
        "unreadable: Symlink loop"
    )


def test_a_non_encodable_path_still_reports_missing_rather_than_crashing(session):
    """A regression this round's own reordering created and then had to close,
    found while checking the two edge cases the round-3 review named as
    harmless and told me not to fix: an empty-string path (unaffected --
    still resolves to system_dir itself, still falls through to "missing")
    and a NUL-byte path, which was *not* unaffected. Before this round,
    candidate.is_file() ran first, and pathlib.Path.is_file has its own
    `except ValueError: return False` for a non-encodable path -- confirmed
    from source. candidate.resolve() now runs first and has no equivalent
    catch -- also confirmed from source, then empirically, since resolve()
    raises ValueError directly for the identical input. Run against the
    pre-ValueError-clause code, this exact scenario raised uncaught out of
    verify_session, silently turning a case the review explicitly signed off
    on as harmless into a crash, purely as a side effect of the ordering this
    round introduces for an unrelated reason. This test exists so that
    regression cannot return unnoticed."""
    layout, manifest = session
    marker_path = layout.done_marker("spikeglx")
    raw = yaml.safe_load(marker_path.read_text())
    raw["files"].append({"path": "bad\x00name.dat", "bytes": 0, "blake3": "0" * 64})
    marker_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    integrity, mismatches = verify_session(layout, manifest)

    assert integrity is Integrity.VERIFIED
    assert [m.problem for m in mismatches] == ["missing"]
    assert mismatches[0].system == "spikeglx"
    assert mismatches[0].path == "bad\x00name.dat"


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
    later tests or the tmp_path cleanup cannot touch.

    The expected `problem` string is asserted exactly, not by prefix or
    substring: `verify_session` folds `str(exc)` into it precisely so a
    human triaging a quarantined session (Task 8) can tell an EACCES
    permissions fault from an EIO bad block without re-deriving anything, and
    an assertion that only checked a prefix would not notice if that text
    silently went missing or malformed. The expected value is captured by
    independently provoking the identical open() failure here in the test
    (same path, same denied mode) rather than hand-transcribing OS-specific
    error wording, so the assertion stays exact without being a guess about
    what a given platform's strerror text looks like."""
    layout, manifest = session
    target = layout.system_dir("spikeglx") / f"{CI_RECIPE.session_id}_imec0.ap.meta"
    original_mode = target.stat().st_mode
    os.chmod(target, 0o000)
    try:
        try:
            target.open("rb")
            pytest.fail("expected chmod 0o000 to make this file unreadable")
        except OSError as exc:
            expected_problem = f"unreadable: {exc}"

        integrity, mismatches = verify_session(layout, manifest)
    finally:
        os.chmod(target, original_mode)

    assert integrity is Integrity.VERIFIED
    assert [m.problem for m in mismatches] == [expected_problem]
    assert mismatches[0].system == "spikeglx"


def test_a_permission_fault_reaching_a_nested_file_is_reported_not_raised(stim_session):
    """Companion to the test above, isolating the other guarded call:
    candidate.is_file() itself raising, rather than succeeding and letting
    blake3_file fail later. Denying execute on the nested `<session_id>_rhs`
    directory blocks path resolution into every file below it — confirmed
    empirically that is_file() raises PermissionError there while a sibling
    file outside the directory stays fully readable, which is exactly the
    DONE marker's situation. Every file rhs declared should be affected, not
    just one, so this also checks the full count, not only the set of
    problem strings.

    As above, each expected `problem` is captured by independently
    provoking the same is_file() failure per filename while still under the
    chmod, so every mismatch can be asserted exactly rather than by
    substring — five different files means five different `filename=`
    values embedded in the OSError text, so a single shared expected string
    would not do."""
    layout, manifest = stim_session
    rhs_dir = layout.system_dir("rhs")
    nested = rhs_dir / f"{STIM_RECIPE.session_id}_rhs"
    declared_names = sorted(p.name for p in nested.iterdir())
    original_mode = nested.stat().st_mode
    os.chmod(nested, 0o000)
    try:
        expected_by_name = {}
        for name in declared_names:
            try:
                (nested / name).is_file()
                pytest.fail(f"expected chmod 0o000 on {nested} to make {name} unreadable")
            except OSError as exc:
                expected_by_name[name] = f"unreadable: {exc}"

        integrity, mismatches = verify_session(layout, manifest)
    finally:
        os.chmod(nested, original_mode)

    assert integrity is Integrity.VERIFIED
    assert len(mismatches) == len(declared_names)
    assert all(m.system == "rhs" for m in mismatches)
    actual_by_name = {m.path.rsplit("/", 1)[-1]: m.problem for m in mismatches}
    assert actual_by_name == expected_by_name
