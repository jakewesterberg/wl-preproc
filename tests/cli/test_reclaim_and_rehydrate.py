"""`wlpp reclaim` deleting and `wlpp rehydrate` restoring, end to end through
`main([...])` against the real MySQL container (2026-09-26 rehydration design).

Every refusal test asserts that nothing changed -- the session's files, the
NAS copy, the tables -- because "a refusal changes nothing" is the property
an operator relies on when they read "refusing:" and walk away.
"""

from __future__ import annotations

import datetime
import os
import shutil
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from wl_preproc.cli.main import main


@pytest.fixture
def landed(landed, prefix):
    """`tests/cli/conftest.py`'s `landed`, plus a teardown that deletes every
    session this test landed.

    Needed here and nowhere else in `tests/cli/`: these tests FREE sessions,
    leaving `Ingestion` rows whose files are gone, and `tests/schema/`'s
    `daemon.run_once()` assertions sweep the whole shared database and would
    re-attempt those sessions' never-populated stages against missing files
    (the rule `tests/cli/test_consensus_report.py::_plant_pair` states: never
    leave a key a later module's `run_once()` fails on). Deleting the
    `pipeline.Session` row cascades to everything keyed on it.

    **This is production behaviour too, not only test pollution** (Task 5
    review, finding I3; controller ruling: record it here rather than change
    the daemon). A session freed before every stage that will ever want it
    has actually populated is this exact same state for real, not only under
    this fixture's teardown. `daemon.run_once()` goes on sweeping it going
    forward: the event stage never reserves a `~jobs` row at all
    (`daemon.py::reap_stale_jobs`'s own docstring -- "it never calls
    `.populate()`"), so it re-attempts and re-errors on the freed session
    EVERY single pass, forever. Any OTHER, Computed/Imported stage that had
    not yet run for this session when it was freed errors ONCE against the
    now-missing files and lands at `status='error'` in its own `~jobs` table
    -- and `_populate_distributed` draws only from `jobs.pending`
    (`reap_stale_jobs`'s docstring again), so that key is not retried on any
    later pass, including one after rehydration restores the files, until
    someone clears that job error by hand. Deleting the row here is a choice
    available to a test tearing down its own fixture; it is not available to
    the real pipeline, which is exactly why it is written out here rather
    than left for a future reader to discover the hard way.
    """
    from wl_preproc.schema import pipeline

    subjects = []

    def _land(subject):
        subjects.append(subject)
        return landed(subject)

    yield _land
    for subject in subjects:
        (pipeline.Session & {"subject": subject}).delete(prompt=False)


def _archive(session_dir, prefix):
    nas_root = session_dir.parent.parent / "nas"
    code = main(["archive", "--session", str(session_dir), "--nas-root", str(nas_root),
                 "--host", "vault", "--share", "cold", "--prefix", prefix])
    assert code == 0
    return nas_root


def _verdict(key, verdict, *, hour, prefix):
    """A `ReclamationHold` row at a stated hour, inserted directly: `wlpp hold`
    stamps `now()`, and two verdicts inside one second would collide on the
    table's key."""
    from wl_preproc.schema import archive

    archive.activate(prefix=prefix)
    archive.ReclamationHold.insert1({
        **key,
        "held_at": datetime.datetime(2027, 5, 1, hour, 0),
        "actor": "tester",
        "verdict": verdict,
        "reason": "test",
    })


def _reclaim(session_dir, nas_root, prefix):
    return main(["reclaim", "--session", str(session_dir), "--no-dry-run",
                 "--confirm", str(session_dir), "--nas-root", str(nas_root), "--prefix", prefix])


def _rehydrate(session_dir, nas_root, prefix):
    return main(["rehydrate", "--session", str(session_dir), "--nas-root", str(nas_root),
                 "--prefix", prefix])


def _published(nas_root, key, session_dir):
    return nas_root / key["subject"] / f"{session_dir.name}.zarr"


def _corrupt_a_chunk(artifact):
    victim = next(
        p for p in sorted((artifact / "streams").rglob("*"))
        if p.is_file() and not p.name.startswith(".")
    )
    victim.write_bytes(b"\x00" * victim.stat().st_size)


def _files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _staging(session_dir, suffix):
    return session_dir.parent / f".{session_dir.name}{suffix}"


def _refusal(out):
    """The one `refusing:` line. Asserting on it, not on the whole output,
    matters: the preview above it prints every condition's NAME whether it
    passed or not, so `"no_hold" in out` would pass even if the refusal
    blamed something else."""
    lines = [ln for ln in out.splitlines() if ln.startswith("refusing:")]
    assert len(lines) == 1, out
    return lines[0]


def _ready(landed, subject, prefix):
    """Landed, archived and forced: everything a real reclamation needs
    before Phase 3, when `canonical_nwb_present` fails for every session."""
    session_dir, key = landed(subject)
    nas_root = _archive(session_dir, prefix)
    _verdict(key, "force", hour=11, prefix=prefix)
    return session_dir, key, nas_root


def _untouched(session_dir, key, before):
    from wl_preproc.schema import archive

    assert _files(session_dir) == before
    assert len(archive.ScratchReclamation & key) == 0
    assert not _staging(session_dir, ".reclaiming").exists()


# -- wlpp reclaim --------------------------------------------------------------


def test_a_forced_verified_session_is_freed_and_recorded(landed, prefix):
    from wl_preproc.schema import archive

    session_dir, key, nas_root = _ready(landed, "rhfree1", prefix)
    size = sum(len(b) for b in _files(session_dir).values())

    assert _reclaim(session_dir, nas_root, prefix) == 0

    assert not session_dir.exists()
    assert not _staging(session_dir, ".reclaiming").exists()
    rows = (archive.ScratchReclamation & key).to_dicts()
    assert [r["bytes_freed"] for r in rows] == [size]
    assert _published(nas_root, key, session_dir).exists()  # the archive is untouched


def test_reclaim_without_nas_root_refuses_and_frees_nothing(landed, prefix, capsys):
    session_dir, key, _nas_root = _ready(landed, "rhnonas", prefix)
    before = _files(session_dir)
    capsys.readouterr()

    code = main(["reclaim", "--session", str(session_dir), "--no-dry-run",
                 "--confirm", str(session_dir), "--prefix", prefix])

    assert code == 2
    assert "--nas-root" in capsys.readouterr().out
    _untouched(session_dir, key, before)


def test_reclaim_refuses_an_unforced_session_blocked_on_the_nwb(landed, prefix, capsys):
    session_dir, key = landed("rhnwb")
    nas_root = _archive(session_dir, prefix)
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    refusal = _refusal(capsys.readouterr().out)
    assert refusal.startswith("refusing: blocked on:")
    assert "canonical_nwb_present" in refusal
    _untouched(session_dir, key, before)


def test_a_force_does_not_free_a_session_with_no_artifact(landed, prefix, capsys):
    session_dir, key = landed("rhnoart")
    _verdict(key, "force", hour=11, prefix=prefix)
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, session_dir.parent.parent / "nas", prefix) == 1

    refusal = _refusal(capsys.readouterr().out)
    assert "artifact_present" in refusal
    assert "every_file_verified" in refusal
    _untouched(session_dir, key, before)


def test_a_hold_after_the_force_blocks_freeing(landed, prefix, capsys):
    session_dir, key, nas_root = _ready(landed, "rhhold", prefix)
    _verdict(key, "hold", hour=12, prefix=prefix)
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    assert "no_hold" in _refusal(capsys.readouterr().out)
    _untouched(session_dir, key, before)


def test_reclaim_refuses_a_missing_sentinel(landed, prefix, capsys):
    from wl_preproc.archive.stage import SENTINEL_NAME

    session_dir, key, nas_root = _ready(landed, "rhnosnt", prefix)
    sentinel = _published(nas_root, key, session_dir) / SENTINEL_NAME
    sentinel.unlink()
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    assert f"no completion sentinel at {sentinel}" in capsys.readouterr().out
    _untouched(session_dir, key, before)


def test_reclaim_refuses_a_nas_copy_that_changed_since_archiving(landed, prefix, capsys):
    session_dir, key, nas_root = _ready(landed, "rhchg", prefix)
    _corrupt_a_chunk(_published(nas_root, key, session_dir))
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    assert "has changed since it was archived" in capsys.readouterr().out
    _untouched(session_dir, key, before)


def test_reclaim_refuses_a_file_that_no_longer_rebuilds_even_when_the_digest_matches(
    landed, prefix, capsys
):
    """Section 3's third check, reached through the CLI: the recorded digest
    is patched to the damaged tree's, so only the rebuild can refuse."""
    from wl_preproc.archive.stage import SENTINEL_NAME
    from wl_preproc.archive.store import manifest_digest
    from wl_preproc.schema import archive

    session_dir, key, nas_root = _ready(landed, "rhrbld", prefix)
    published = _published(nas_root, key, session_dir)
    _corrupt_a_chunk(published)
    archive.ArchiveArtifact.update1({
        **key,
        "manifest_digest": manifest_digest(published, exclude=frozenset({SENTINEL_NAME})),
    })
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    assert "did not rebuild to the rig's digest" in capsys.readouterr().out
    _untouched(session_dir, key, before)


def test_reclaim_refuses_a_leftover_staging_directory(landed, prefix, capsys):
    from wl_preproc.schema import archive

    session_dir, key, nas_root = _ready(landed, "rhleft1", prefix)
    leftover = _staging(session_dir, ".rehydrating")
    leftover.mkdir()
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    out = capsys.readouterr().out
    assert "left over" in out
    assert str(leftover) in out
    assert _files(session_dir) == before
    assert len(archive.ScratchReclamation & key) == 0


def test_an_interrupted_removal_is_named_by_the_next_reclaim(landed, prefix, capsys):
    """Fix round 1, finding 1. `os.rename` succeeds and the transaction
    commits, but the final `shutil.rmtree` of the now-empty staging
    directory fails -- an interrupted removal, not an interrupted commit.
    The NEXT `wlpp reclaim` must name the leftover staging directory rather
    than being masked by the `is_dir()` "already reclaimed?" guard, which
    used to run first."""
    from wl_preproc.schema import archive

    session_dir, key, nas_root = _ready(landed, "rhrmtree", prefix)

    with patch(
        "wl_preproc.archive.scratch.shutil.rmtree",
        side_effect=OSError("simulated removal failure"),
    ):
        with pytest.raises(OSError):
            _reclaim(session_dir, nas_root, prefix)

    assert len(archive.ScratchReclamation & key) == 1
    assert not session_dir.exists()
    leftover = _staging(session_dir, ".reclaiming")
    assert (leftover / session_dir.name).is_dir()
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1
    assert str(leftover) in _refusal(capsys.readouterr().out)

    shutil.rmtree(leftover)


def test_reclaim_refuses_a_file_added_after_archiving(landed, prefix, capsys):
    """Controller ruling, Task 5 review: the proof compares the NAS copy with
    recorded values, never scratch with the NAS -- a file dropped onto
    scratch after archiving would otherwise be deleted having never been
    archived at all."""
    session_dir, key, nas_root = _ready(landed, "rhlate", prefix)
    (session_dir / "late-notes.txt").write_text("added after archiving\n")
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    refusal = _refusal(capsys.readouterr().out)
    assert "late-notes.txt" in refusal
    assert "not in the archive" in refusal
    _untouched(session_dir, key, before)


def test_reclaim_refuses_a_file_changed_after_archiving(landed, prefix, capsys):
    """The same hole, reached by editing a file the archive already holds
    rather than adding a new one. Built by hand rather than through `_ready`:
    the extra file has to exist BEFORE `_archive` runs, so the archive's own
    copy of it is the original, unedited bytes."""
    session_dir, key = landed("rhedit")
    (session_dir / "notes.txt").write_text("original\n")
    nas_root = _archive(session_dir, prefix)
    _verdict(key, "force", hour=11, prefix=prefix)
    with (session_dir / "notes.txt").open("a") as handle:
        handle.write("more\n")
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    refusal = _refusal(capsys.readouterr().out)
    assert "notes.txt" in refusal
    assert "not in the archive" in refusal
    _untouched(session_dir, key, before)


def test_reclaim_refuses_a_symlinked_spelling_of_the_session(landed, prefix, capsys):
    """Review Focus 1. Rehydration restores to the RECORDED path; freeing a
    copy reached another way would leave nothing to restore it to."""
    session_dir, key, nas_root = _ready(landed, "rhalias", prefix)
    alias = session_dir.parent.parent / "alias"
    alias.symlink_to(session_dir.parent, target_is_directory=True)
    aliased = alias / session_dir.name
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(aliased, nas_root, prefix) == 1

    out = capsys.readouterr().out
    assert "is not this session's recorded directory" in out
    assert str(session_dir) in out
    _untouched(session_dir, key, before)


def test_a_trailing_slash_names_the_same_session(landed, prefix):
    """Review Focus 1's other half: `Path` normalises a trailing slash, so it
    is the recorded path and must not be refused."""
    session_dir, _key, nas_root = _ready(landed, "rhslash", prefix)
    spelled = str(session_dir) + "/"

    code = main(["reclaim", "--session", spelled, "--no-dry-run", "--confirm", spelled,
                 "--nas-root", str(nas_root), "--prefix", prefix])

    assert code == 0
    assert not session_dir.exists()


def test_reclaiming_an_already_reclaimed_session_refuses_cleanly(landed, prefix, capsys):
    """Review Focus 4: not a FileNotFoundError out of the manifest read."""
    session_dir, _key, nas_root = _ready(landed, "rhtwice", prefix)
    assert _reclaim(session_dir, nas_root, prefix) == 0
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    out = capsys.readouterr().out
    assert "already reclaimed" in out
    assert "wlpp rehydrate" in out


def test_a_failed_record_leaves_the_session_where_it_was(landed, prefix):
    """The row and the rename are one transaction: an insert that fails means
    the rename never happens, and the empty staging directory is removed."""
    session_dir, key, nas_root = _ready(landed, "rhtxn", prefix)
    before = _files(session_dir)

    with patch(
        "wl_preproc.schema.archive.ScratchReclamation.insert1",
        side_effect=RuntimeError("simulated insert failure"),
    ):
        with pytest.raises(RuntimeError, match="simulated insert failure"):
            _reclaim(session_dir, nas_root, prefix)

    _untouched(session_dir, key, before)


def test_a_failed_rename_rolls_back_the_record(landed, prefix):
    """The sibling this needs, and the one the test above cannot stand in
    for: that test makes the INSERT fail, which would still pass even with
    the transaction removed entirely, since the insert never reaches the
    database either way. This makes the RENAME fail instead, after the
    insert has already gone through -- only the transaction rolling that
    insert back, on the rename's own failure, proves the guard."""
    session_dir, key, nas_root = _ready(landed, "rhrenm", prefix)
    before = _files(session_dir)

    with patch(
        "wl_preproc.archive.scratch.os.rename",
        side_effect=OSError("simulated rename failure"),
    ):
        with pytest.raises(OSError):
            _reclaim(session_dir, nas_root, prefix)

    _untouched(session_dir, key, before)


def test_the_daemon_never_frees_a_session():
    """Ruling 1: a person frees scratch, for now. A source scan in the shape
    of `tests/schema/test_guardrails.py`'s own, because the rule is about
    what the daemon's code must never contain."""
    import wl_preproc.daemon as daemon

    source = Path(daemon.__file__).read_text(encoding="utf-8")
    assert "free_session" not in source
    assert "archive.scratch" not in source


# -- wlpp rehydrate ------------------------------------------------------------


def _reclaimed(landed, subject, prefix):
    session_dir, key, nas_root = _ready(landed, subject, prefix)
    pristine = _files(session_dir)
    assert _reclaim(session_dir, nas_root, prefix) == 0
    return session_dir, key, nas_root, pristine


def _nothing_restored(session_dir, key):
    from wl_preproc.schema import archive

    assert not os.path.lexists(session_dir)
    assert not _staging(session_dir, ".rehydrating").exists()
    assert len(archive.ScratchRehydration & key) == 0


def test_a_session_survives_reclaim_and_rehydrate_byte_for_byte(landed, prefix):
    from wl_preproc.ingest.watcher import Outcome, scan_once
    from wl_preproc.schema import archive

    session_dir, key = landed("rhround")
    # Review Focus 3: a file no DONE marker names, beyond the manifest and
    # the markers. Archived verbatim; only the manifest digest proves it.
    (session_dir / "operator-notes.txt").write_text("probe 2 drifted at 01:12\n", encoding="utf-8")
    pristine = _files(session_dir)
    nas_root = _archive(session_dir, prefix)
    _verdict(key, "force", hour=11, prefix=prefix)

    assert _reclaim(session_dir, nas_root, prefix) == 0
    assert not session_dir.exists()

    assert _rehydrate(session_dir, nas_root, prefix) == 0

    assert _files(session_dir) == pristine
    rows = (archive.ScratchRehydration & key).to_dicts()
    assert [r["bytes_written"] for r in rows] == [sum(len(b) for b in pristine.values())]
    assert not _staging(session_dir, ".rehydrating").exists()
    # The watcher sees the manifest it landed, and does nothing.
    # `outcomes` is keyed by `str(session_dir)` (`ingest/watcher.py::scan_once`
    # -- confirmed against every other passing use of this dict in the suite,
    # e.g. `tests/cli/test_report.py`'s own `session_dir = str(root / ...)`),
    # never by the `Path` `landed` returns: a `Path` key can never equal a
    # `str` key (different hash, `PurePath.__eq__` returns `NotImplemented`
    # for a non-`PurePath`), so indexing with the bare `Path` here raises
    # `KeyError` unconditionally -- confirmed empirically -- regardless of
    # whether the watcher actually saw the session. This is a bug in the
    # lookup, not in `rehydrate_session` or the watcher.
    assert scan_once(session_dir.parent, prefix=prefix).outcomes[str(session_dir)] is Outcome.ALREADY

    time.sleep(1.1)  # `reclaimed_at` is a whole-second datetime, and MySQL rounds
    assert _reclaim(session_dir, nas_root, prefix) == 0
    assert len(archive.ScratchReclamation & key) == 2


def test_rehydrate_refuses_an_existing_destination(landed, prefix, capsys):
    from wl_preproc.schema import archive

    session_dir, key, nas_root = _ready(landed, "rhexist", prefix)
    before = _files(session_dir)
    capsys.readouterr()

    assert _rehydrate(session_dir, nas_root, prefix) == 1

    assert "already exists" in capsys.readouterr().out
    assert _files(session_dir) == before
    assert len(archive.ScratchRehydration & key) == 0


def test_rehydrate_refuses_a_leftover_staging_directory(landed, prefix, capsys):
    from wl_preproc.schema import archive

    session_dir, key, nas_root, _ = _reclaimed(landed, "rhleft2", prefix)
    _staging(session_dir, ".reclaiming").mkdir()
    capsys.readouterr()

    assert _rehydrate(session_dir, nas_root, prefix) == 1

    assert "left over" in capsys.readouterr().out
    assert not session_dir.exists()
    assert len(archive.ScratchRehydration & key) == 0


def test_rehydrate_refuses_a_nas_copy_that_changed_before_writing_anything(landed, prefix, capsys):
    session_dir, key, nas_root, _ = _reclaimed(landed, "rhchg2", prefix)
    _corrupt_a_chunk(_published(nas_root, key, session_dir))
    capsys.readouterr()

    assert _rehydrate(session_dir, nas_root, prefix) == 1

    assert "has changed since it was archived" in capsys.readouterr().out
    _nothing_restored(session_dir, key)


def test_rehydrate_refuses_a_missing_nas_mount_and_names_the_path(landed, prefix, capsys):
    """Review Focus 2."""
    session_dir, key, _nas_root, _ = _reclaimed(landed, "rhmount", prefix)
    missing = session_dir.parent.parent / "not-mounted"
    capsys.readouterr()

    assert _rehydrate(session_dir, missing, prefix) == 1

    out = capsys.readouterr().out
    assert "no completion sentinel at" in out
    assert str(missing) in out
    _nothing_restored(session_dir, key)


def test_rehydrate_names_the_full_path_when_nas_root_is_one_level_too_deep(landed, prefix, capsys):
    """Review Focus 5: the subject directory given as the share root doubles
    the subject in the path; the operator must be able to see that."""
    session_dir, key, nas_root, _ = _reclaimed(landed, "rhdeep", prefix)
    too_deep = nas_root / key["subject"]
    capsys.readouterr()

    assert _rehydrate(session_dir, too_deep, prefix) == 1

    doubled = too_deep / key["subject"] / f"{session_dir.name}.zarr"
    assert str(doubled) in capsys.readouterr().out
    _nothing_restored(session_dir, key)


def test_rehydrate_refuses_when_there_is_no_room(landed, prefix, capsys):
    session_dir, key, nas_root, _ = _reclaimed(landed, "rhroom", prefix)
    capsys.readouterr()

    with patch("wl_preproc.archive.rehydrate.headroom_after", return_value=False):
        assert _rehydrate(session_dir, nas_root, prefix) == 1

    assert "below the scratch floor" in capsys.readouterr().out
    _nothing_restored(session_dir, key)


def test_rehydrate_refuses_when_no_rig_digests_are_recorded(landed, prefix, capsys):
    """Ruling, reviewer Minor 6: 'nothing to check a restore against' must
    never read as proven -- the identical rule `verify.py::_expected_digests`
    and `proof.py::prove_artifact` already apply at archive time and
    preflight, now applied to rehydration's own post-preflight reference
    digests too. `ArchiveVerification` rows are deleted directly rather than
    reproduced through a session with no files: `_expected_digests` itself
    already refuses to archive a session with no DONE marker entries, so an
    empty `ArchiveVerification` set is only reachable by editing the
    recorded rows, not by any real session shape."""
    from wl_preproc.schema import archive

    session_dir, key, nas_root, _ = _reclaimed(landed, "rhnodig", prefix)
    (archive.ArchiveVerification & key).delete(prompt=False)
    capsys.readouterr()

    assert _rehydrate(session_dir, nas_root, prefix) == 1

    assert "nothing to check a restore against" in capsys.readouterr().out
    _nothing_restored(session_dir, key)


def test_rehydrate_refuses_an_unrecorded_path(dj_conn, prefix, tmp_path, capsys):
    nowhere = tmp_path / "nowhere" / "2027-03-14_01"

    assert _rehydrate(nowhere, tmp_path / "nas", prefix) == 1

    assert "no landed session was recorded at" in capsys.readouterr().out
    assert not (tmp_path / "nowhere").exists()


def test_rehydrate_refuses_a_relative_recorded_path(landed, prefix, capsys):
    from wl_preproc.schema import ingest

    session_dir, key, nas_root, _ = _reclaimed(landed, "rhrel", prefix)
    relative = "rhrel-relative-root/2027-03-14_01"
    ingest.Ingestion.update1({**key, "session_dir": relative})
    capsys.readouterr()

    assert _rehydrate(relative, nas_root, prefix) == 1

    assert "is relative" in capsys.readouterr().out
    assert not os.path.lexists("rhrel-relative-root")


def test_rehydrate_refuses_a_differently_cased_spelling_of_the_path(landed, prefix, capsys):
    """MySQL's default server collation (utf8mb4_0900_ai_ci) matches
    `session_dir` case- and accent-insensitively, so the query alone cannot
    tell a differently spelled path from the recorded one -- confirmed
    directly: `WHERE session_dir = '/SCRATCH/Root/2027-03-14_01'` returns the
    stored row '/scratch/root/2027-03-14_01'. Session ids like
    "2027-03-14_01" carry no letters, so upper-casing the NAME (the brief's
    first-choice construction) changes nothing; swapcasing the PARENT path's
    last component instead is guaranteed to differ in case from the recorded
    path, since `landed`'s own scratch roots are named "scratch-<subject>",
    all lowercase letters."""
    session_dir, key, nas_root, _ = _reclaimed(landed, "rhcase", prefix)
    variant = session_dir.parent.with_name(session_dir.parent.name.swapcase()) / session_dir.name
    assert variant != session_dir
    capsys.readouterr()

    assert _rehydrate(variant, nas_root, prefix) == 1

    assert "no landed session was recorded at" in capsys.readouterr().out
    _nothing_restored(session_dir, key)


def test_rehydrate_refuses_when_the_scratch_root_is_gone(landed, prefix, capsys):
    session_dir, key, nas_root, _ = _reclaimed(landed, "rhgone", prefix)
    root = session_dir.parent
    root.rename(root.with_name(root.name + "-moved"))
    capsys.readouterr()

    assert _rehydrate(session_dir, nas_root, prefix) == 1

    assert "does not exist" in capsys.readouterr().out
    assert not root.exists()  # not silently recreated


def test_a_rebuilt_file_that_disagrees_with_the_rig_is_not_restored(landed, prefix, capsys):
    from wl_preproc.schema import archive

    session_dir, key, nas_root, _ = _reclaimed(landed, "rhmism", prefix)
    row = (archive.ArchiveVerification & key).to_dicts()[0]
    archive.ArchiveVerification.update1({
        **key, "relative_path": row["relative_path"], "expected_blake3": "0" * 64,
    })
    capsys.readouterr()

    assert _rehydrate(session_dir, nas_root, prefix) == 1

    out = capsys.readouterr().out
    assert f"MISMATCH {row['relative_path']}" in out
    assert "NOT restored" in out
    _nothing_restored(session_dir, key)


def test_a_failure_part_way_through_leaves_nothing(landed, prefix):
    from wl_preproc.archive import rehydrate as rehydrate_module

    session_dir, key, nas_root, _ = _reclaimed(landed, "rhpart", prefix)
    real = rehydrate_module._write
    calls = []

    def flaky(store, relative, target_root):
        calls.append(relative)
        if len(calls) == 3:
            raise OSError("simulated write failure")
        return real(store, relative, target_root)

    with patch.object(rehydrate_module, "_write", flaky):
        with pytest.raises(OSError, match="simulated write failure"):
            _rehydrate(session_dir, nas_root, prefix)

    _nothing_restored(session_dir, key)


def test_a_failed_rename_leaves_no_rehydration_record(landed, prefix):
    """Pins the one-transaction rule (2026-09-26 rehydration design, section
    5): the `ScratchRehydration` insert and the rename that puts the session
    back at its recorded path succeed or fail together, exactly like
    `free_session`'s own reclaim-side transaction
    (`test_a_failed_rename_rolls_back_the_record` above)."""
    session_dir, key, nas_root, _ = _reclaimed(landed, "rhrenm2", prefix)

    with patch(
        "wl_preproc.archive.rehydrate.os.rename",
        side_effect=OSError("simulated rename failure"),
    ):
        with pytest.raises(OSError):
            _rehydrate(session_dir, nas_root, prefix)

    _nothing_restored(session_dir, key)


def test_a_failure_creating_the_staged_session_leaves_no_leftover(landed, prefix):
    """Round 2 review: `target.mkdir()` sits INSIDE the try/finally now, as
    its first statement, not beside `staging.mkdir()` -- a fault creating it
    must reach the existing cleanup, or the (empty) staging directory this
    call itself created is left behind for `refuse_leftovers` to block every
    later rehydrate of this session on, by hand."""
    session_dir, key, nas_root, _ = _reclaimed(landed, "rhmkdir", prefix)
    real = Path.mkdir

    def flaky(self, *args, **kwargs):
        # Only the staged session itself, never `staging.mkdir()` (whose own
        # name is the `.<name>.rehydrating` directory, not `session_dir`'s
        # name) and never any per-file directory `_write` creates under
        # `target` (which this fault is never reached in time to exercise
        # anyway, since `target.mkdir()` is the very first statement inside
        # the try).
        if self.parent.name.endswith(".rehydrating") and self.name == session_dir.name:
            raise OSError("simulated mkdir failure")
        return real(self, *args, **kwargs)

    with patch.object(Path, "mkdir", autospec=True, side_effect=flaky):
        with pytest.raises(OSError, match="simulated mkdir failure"):
            _rehydrate(session_dir, nas_root, prefix)

    _nothing_restored(session_dir, key)
