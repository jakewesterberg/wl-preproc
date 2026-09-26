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
