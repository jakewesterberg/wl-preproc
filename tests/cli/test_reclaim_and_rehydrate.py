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
    leaving `Ingestion` rows whose files are gone. The daemon now skips a
    freed session until it is rehydrated (`daemon.run_once`, via
    `archive/scratch.py::currently_freed`; the requester's decision of
    2026-09-26), so a later module's `run_once()` would no longer fail on
    them -- but several tests here plant rows by hand, including an
    `Ingestion.session_dir` rewritten to a relative path, and deleting each
    test's `pipeline.Session` row (which cascades to everything keyed on it)
    keeps the shared database free of states production never produces.

    *Until 2026-09-26 this docstring also recorded, as production behaviour,
    what the daemon did to a freed session: the event stage errored on every
    pass, job-table stages errored once and stayed parked after
    rehydration, and an absence-tolerant stage could write false rows. That
    was true when written; `tests/schema/test_daemon_skips_freed_sessions.py`
    pins that none of it happens now.*
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


def _timing(key, prefix, *, tier: str, unfitted=()):
    """A `TimingProvenance` row pinning `tier`, and a `SystemTimebase` clock
    fit for every `core.AcquisitionSystem` the session landed with except those
    named in `unfitted`. Mirrors `tests/cli/test_archive_cli.py::_timing` (and,
    through it, `tests/archive/test_reclaim.py::_timing`, whose docstring says
    why a direct insert into a `dj.Computed` table is an established pattern
    here). Landing alone never populates either, and without both the safety
    condition `timing_resolved` blocks every reclamation, forced or not: a
    system with no fit is one whose timing stage failed or has not run, and
    running it after the session is freed would record `no_recording` for a
    device that recorded."""
    from wl_preproc.schema import core, timebase

    timebase.activate(prefix=prefix)
    # The real clock-fit stage, not a planted row -- see
    # `tests/cli/test_archive_cli.py::_timing` for why a planted "fitted" row
    # is a key a later `daemon.run_once()` fails on.
    for system in (core.AcquisitionSystem & key).to_arrays("system"):
        if system in unfitted:
            continue
        timebase.SystemTimebase.populate({**key, "system": system})
    timebase.TimingProvenance.insert1(
        {
            **key,
            "tier": tier,
            "n_barcodes_emitted": 100,
            "n_systems_aligned": 1,
            "n_segments": 1,
            "n_rejected_segments": 0,
            "worst_residual_us": 1.0,
            "worst_drift_ppm": 0.5,
            "pending_inputs": "",
            "n_full_code_records": 1,
            "n_strobe_witnesses": 0,
            "decode_errors": 0,
        },
        allow_direct_insert=True,
    )


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
    """Landed, archived, timed and forced: everything a real reclamation
    needs before Phase 3, when `canonical_nwb_present` fails for every
    session. The tier-A `TimingProvenance` row and a clock fit for every
    system (`_timing`) stand in for the timing stages having run on the
    session's real files, which `timing_resolved` requires and no force
    overrides."""
    session_dir, key = landed(subject)
    nas_root = _archive(session_dir, prefix)
    _timing(key, prefix, tier="A")
    _verdict(key, "force", hour=11, prefix=prefix)
    return session_dir, key, nas_root


def _untouched(session_dir, key, before):
    from wl_preproc.schema import archive

    assert _files(session_dir) == before
    assert len(archive.ScratchReclamation & key) == 0
    assert not _staging(session_dir, ".reclaiming").exists()


def _commit_fails_after_the_body():
    """A context manager under which a transaction's COMMIT fails after its
    body has run -- the rename done, the insert rolled back -- by having
    `commit_transaction` roll back and raise, which is what a commit that
    fails does. The cheaper substitutes (failing the insert, failing the
    rename) never reach this point. Scoped to the call under test, not a
    `monkeypatch`, so the fixture's own teardown commits normally."""
    from datajoint.connection import Connection

    real_cancel = Connection.cancel_transaction

    def commit(self):
        real_cancel(self)
        raise RuntimeError("simulated commit failure")

    return patch.object(Connection, "commit_transaction", commit)


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
    _timing(key, prefix, tier="A")
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    refusal = _refusal(capsys.readouterr().out)
    assert refusal == "refusing: blocked on: canonical_nwb_present"
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


def test_a_force_does_not_free_a_session_whose_timing_has_not_run(landed, prefix, capsys):
    """The whole-branch review's Critical finding, through the CLI. Landed,
    archived and forced, but no `TimingProvenance` row: freeing it now would
    let the timebase stages compute on an absent directory, which they read
    as "no recording" and record as a permanent tier D. A force overrides
    judgement, and this is not judgement."""
    session_dir, key = landed("rhnotime")
    nas_root = _archive(session_dir, prefix)
    _verdict(key, "force", hour=11, prefix=prefix)
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    refusal = _refusal(capsys.readouterr().out)
    assert "timing_resolved" in refusal
    assert "not_tier_d" not in refusal  # overridden by the force; safety is what blocks
    _untouched(session_dir, key, before)


def test_a_force_does_not_free_a_session_with_an_unfitted_system(landed, prefix, capsys):
    """The residual the fix wave's re-review reproduced through the CLI: a
    `TimingProvenance` row -- written even when one system's clock-fit key
    failed or crashed -- is not enough. `bcam` has no `SystemTimebase` row
    here; freeing the session would let that key run later on an absent
    directory and record `no_recording` for a camera that recorded."""
    session_dir, key = landed("rhunfit")
    nas_root = _archive(session_dir, prefix)
    _timing(key, prefix, tier="D", unfitted=("bcam",))
    _verdict(key, "force", hour=11, prefix=prefix)
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    out = capsys.readouterr().out
    assert _refusal(out) == "refusing: blocked on: timing_resolved"
    assert "bcam" in out  # the preview names the system with no fit
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

    capsys.readouterr()
    with patch(
        "wl_preproc.archive.scratch.shutil.rmtree",
        side_effect=OSError("simulated removal failure"),
    ):
        assert _reclaim(session_dir, nas_root, prefix) == 1

    leftover = _staging(session_dir, ".reclaiming")
    # A sentence saying what is on disk, not a bare traceback (the
    # rehydration handoff's parked follow-up 2).
    out = capsys.readouterr().out
    assert "failed part-way" in out
    assert f"{leftover} is left over" in out
    assert len(archive.ScratchReclamation & key) == 1
    assert not session_dir.exists()
    assert (leftover / session_dir.name).is_dir()

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
    assert "re-archive with `wlpp archive` first" in refusal
    _untouched(session_dir, key, before)


def test_reclaim_refuses_a_file_changed_after_archiving(landed, prefix, capsys):
    """The same hole, reached by editing a file the archive already holds
    rather than adding a new one. Built by hand rather than through `_ready`:
    the extra file has to exist BEFORE `_archive` runs, so the archive's own
    copy of it is the original, unedited bytes."""
    session_dir, key = landed("rhedit")
    (session_dir / "notes.txt").write_text("original\n")
    nas_root = _archive(session_dir, prefix)
    _timing(key, prefix, tier="A")
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


def test_reclaim_refuses_a_same_size_resent_file(landed, prefix, capsys):
    """Whole-branch review, finding I1. A rig-checksummed file re-sent at the
    same size, with its DONE marker updated to the new digest: the size
    check cannot see it, and the proof rebuilds the archive to the digests
    recorded at archive time, which the archive still matches. Freeing it
    would make rehydration restore the OLD bytes. The markers on scratch no
    longer list what was recorded, and that is what refuses."""
    from wl_preproc.archive.verify import _expected_digests
    from wl_preproc.contracts.done import blake3_file
    from wl_preproc.contracts.paths import DONE_MARKER_FILENAME

    session_dir, key, nas_root = _ready(landed, "rhresnd", prefix)
    relative, old = sorted(_expected_digests(session_dir).items())[0]
    victim = session_dir / relative
    data = bytearray(victim.read_bytes())
    data[len(data) // 2] ^= 0xFF
    victim.write_bytes(bytes(data))
    new = blake3_file(victim)
    markers = [m for m in session_dir.rglob(DONE_MARKER_FILENAME) if old in m.read_text()]
    assert len(markers) == 1
    markers[0].write_text(markers[0].read_text().replace(old, new))
    assert len(new) == len(old)  # so the marker keeps its size too
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    refusal = _refusal(capsys.readouterr().out)
    assert "DONE markers on scratch no longer list what was archived" in refusal
    assert "re-archive with `wlpp archive` first" in refusal
    _untouched(session_dir, key, before)


def test_reclaim_refuses_a_same_size_edit_to_an_unchecksummed_file(landed, prefix, capsys):
    """Whole-branch review, finding I1's other half. A file no DONE marker
    names -- so the proof never rebuilt it -- overwritten after archiving
    with different bytes of the same length. Only hashing it against the
    archive's copy can see that."""
    session_dir, key = landed("rhsame")
    (session_dir / "notes.txt").write_text("original\n")
    nas_root = _archive(session_dir, prefix)
    _timing(key, prefix, tier="A")
    _verdict(key, "force", hour=11, prefix=prefix)
    (session_dir / "notes.txt").write_text("ORIGINAL\n")
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    refusal = _refusal(capsys.readouterr().out)
    assert "notes.txt" in refusal
    assert "differ from the archive's copy" in refusal
    assert "re-archive with `wlpp archive` first" in refusal
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


def test_a_failed_record_leaves_the_session_where_it_was(landed, prefix, capsys):
    """The row and the rename are one transaction: an insert that fails means
    the rename never happens, and the empty staging directory is removed."""
    session_dir, key, nas_root = _ready(landed, "rhtxn", prefix)
    before = _files(session_dir)

    with patch(
        "wl_preproc.schema.archive.ScratchReclamation.insert1",
        side_effect=RuntimeError("simulated insert failure"),
    ):
        assert _reclaim(session_dir, nas_root, prefix) == 1

    out = capsys.readouterr().out
    assert f"{session_dir} is in place; no staging directory is left over" in out
    _untouched(session_dir, key, before)


def test_a_failed_rename_rolls_back_the_record(landed, prefix, capsys):
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
        assert _reclaim(session_dir, nas_root, prefix) == 1

    assert "is in place; no staging directory is left over" in capsys.readouterr().out
    _untouched(session_dir, key, before)


def test_a_commit_failing_after_the_rename_puts_the_session_back(landed, prefix, capsys):
    """The row rolled back while the session left its path: freed with no
    record, which the daemon (skipping only sessions recorded as freed) would
    then read. `free_session` moves it back, so disk and records agree."""
    session_dir, key, nas_root = _ready(landed, "rhcmt1", prefix)
    before = _files(session_dir)
    capsys.readouterr()

    with _commit_fails_after_the_body():
        assert _reclaim(session_dir, nas_root, prefix) == 1

    assert "is in place; no staging directory is left over" in capsys.readouterr().out
    _untouched(session_dir, key, before)


def test_the_daemon_never_frees_a_session():
    """Ruling 1: a person frees scratch, for now. A source scan in the shape
    of `tests/schema/test_guardrails.py`'s own, because the rule is about
    what the daemon's code must never contain.

    The daemon may import exactly one name from `archive/scratch.py`:
    `currently_freed`, the read-only lookup it uses to SKIP freed sessions
    (the requester's decision of 2026-09-26). Anything else from that module
    -- above all `free_session` -- would be the daemon reaching for deletion.
    This used to forbid the module outright, which was right until the daemon
    had a reason to read from it."""
    import re

    import wl_preproc.daemon as daemon

    source = Path(daemon.__file__).read_text(encoding="utf-8")
    assert "free_session" not in source
    imported = re.findall(r"from wl_preproc\.archive\.scratch import ([^\n]+)", source)
    assert imported == ["currently_freed"], imported
    assert "archive.scratch." not in source  # no attribute access around the import


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
    _timing(key, prefix, tier="A")
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


def test_not_restored_names_a_partial_copy_the_cleanup_could_not_remove(
    landed, prefix, capsys
):
    """The staging directory is removed with `ignore_errors=True`, which can
    fail silently; the message must say what is actually left, not assume
    (the rehydration handoff's parked follow-up 3)."""
    from wl_preproc.schema import archive

    session_dir, key, nas_root, _ = _reclaimed(landed, "rhleftc", prefix)
    row = (archive.ArchiveVerification & key).to_dicts()[0]
    archive.ArchiveVerification.update1({
        **key, "relative_path": row["relative_path"], "expected_blake3": "0" * 64,
    })
    capsys.readouterr()

    with patch("wl_preproc.archive.rehydrate.shutil.rmtree"):  # the removal silently does nothing
        assert _rehydrate(session_dir, nas_root, prefix) == 1

    leftover = _staging(session_dir, ".rehydrating")
    out = capsys.readouterr().out
    assert "NOT restored" in out
    assert f"{leftover} is left over" in out
    assert "no staging directory is left over" not in out
    # And nothing was restored or recorded: the failure is a check, before
    # the transaction.
    assert not os.path.lexists(session_dir)
    assert len(archive.ScratchRehydration & key) == 0

    shutil.rmtree(leftover)


def test_a_commit_failing_after_the_rename_says_restored_but_not_recorded(
    landed, prefix, capsys
):
    """Every check passed and the files are in place; only the record failed.
    Reported as exactly that -- not as a failed restore -- with the steps that
    get it recorded."""
    from wl_preproc.schema import archive

    session_dir, key, nas_root, pristine = _reclaimed(landed, "rhcmt2", prefix)
    capsys.readouterr()

    with _commit_fails_after_the_body():
        assert _rehydrate(session_dir, nas_root, prefix) == 1

    out = capsys.readouterr().out
    assert f"restored but NOT recorded: {session_dir}" in out
    assert "move that directory OUT of the scratch root" in out
    assert _files(session_dir) == pristine
    assert len(archive.ScratchRehydration & key) == 0
    assert not _staging(session_dir, ".rehydrating").exists()


def test_a_failure_part_way_through_leaves_nothing(landed, prefix, capsys):
    from wl_preproc.archive import rehydrate as rehydrate_module

    session_dir, key, nas_root, _ = _reclaimed(landed, "rhpart", prefix)
    real = rehydrate_module._write
    calls = []

    def flaky(store, relative, target_root):
        calls.append(relative)
        if len(calls) == 3:
            raise OSError("simulated write failure")
        return real(store, relative, target_root)

    capsys.readouterr()
    with patch.object(rehydrate_module, "_write", flaky):
        assert _rehydrate(session_dir, nas_root, prefix) == 1

    out = capsys.readouterr().out
    assert "failed part-way; the NAS artifact is untouched" in out
    assert f"{session_dir} is not present; no staging directory is left over" in out
    _nothing_restored(session_dir, key)


def test_a_failed_rename_leaves_no_rehydration_record(landed, prefix, capsys):
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
        assert _rehydrate(session_dir, nas_root, prefix) == 1

    assert "no staging directory is left over" in capsys.readouterr().out
    _nothing_restored(session_dir, key)


def test_a_failure_creating_the_staged_session_leaves_no_leftover(landed, prefix, capsys):
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
        assert _rehydrate(session_dir, nas_root, prefix) == 1

    assert "no staging directory is left over" in capsys.readouterr().out
    _nothing_restored(session_dir, key)


# -- The rehydration handoff's parked follow-ups 4-6 ---------------------------


def test_a_hold_can_be_recorded_on_a_freed_session(landed, prefix, capsys):
    """Follow-up 6: `wlpp hold` read the session's key from its manifest, which
    a freed session no longer has on scratch, and crashed. It now finds the
    session by the path ingest recorded, as `wlpp rehydrate` does -- a hold or
    force on a freed session is meaningful, since the latest verdict still
    governs once it is rehydrated."""
    from wl_preproc.schema import archive

    session_dir, key, _nas_root, _ = _reclaimed(landed, "rhholdf", prefix)

    code = main(["hold", "--session", str(session_dir), "--verdict", "hold",
                 "--actor", "tester", "--reason", "keep it off scratch", "--prefix", prefix])

    assert code == 0
    # By its reason, not as "the latest": this file's `_verdict` stamps its
    # force in 2027, later than the real now `wlpp hold` records.
    rows = (archive.ReclamationHold & key & {"reason": "keep it off scratch"}).to_dicts()
    assert [row["verdict"] for row in rows] == ["hold"]


def test_one_subject_twice_at_one_path_is_chosen_by_session_datetime(
    dj_conn, prefix, tmp_path, capsys
):
    """Review of follow-up 4: two sessions of ONE subject recorded at one path
    (the same session id, landed and freed twice) are not told apart by
    `--subject`, so the refusal names `--session-datetime` too, and that
    chooses -- here through `wlpp hold`, whose freed-session path shares
    `session_for_path` with `wlpp rehydrate`. An aware value, copied with its
    offset, names the same session as the naive UTC one stored."""
    from wl_preproc.schema import archive, ingest, pipeline

    ingest.activate(prefix=prefix)
    archive.activate(prefix=prefix)
    subject = "rhtwice"
    path = tmp_path / "gone" / "2027-03-14_01"
    first = datetime.datetime(2027, 3, 14, 9, 0)
    second = datetime.datetime(2027, 3, 15, 9, 0)
    pipeline.subject.Subject.insert1(
        {"subject": subject, "sex": "M", "subject_birth_date": datetime.date(2020, 1, 1),
         "subject_description": ""},
        skip_duplicates=True,
    )
    try:
        for when in (first, second):
            pipeline.Session.insert1({"subject": subject, "session_datetime": when})
            ingest.Ingestion.insert1({
                "subject": subject, "session_datetime": when,
                "ingested_at": when, "session_dir": str(path), "integrity": "verified",
                "topology": {}, "manifest_hash": "blake3:test",
            })

        def hold(*extra):
            return main(["hold", "--session", str(path), "--verdict", "hold",
                         "--actor", "tester", "--reason", "twice", "--prefix", prefix,
                         "--subject", subject, *extra])

        assert hold() == 1
        refusal = _refusal(capsys.readouterr().out)
        assert "2 landed sessions were recorded at" in refusal
        assert "--session-datetime" in refusal

        assert hold("--session-datetime", "2027-03-15T11:00:00+02:00") == 0
        held = (archive.ReclamationHold & {"subject": subject, "reason": "twice"}).to_dicts()
        assert [row["session_datetime"] for row in held] == [second]

        assert hold("--session-datetime", "2027-03-16 09:00:00") == 1
        assert "no landed session matching" in _refusal(capsys.readouterr().out)
    finally:
        (pipeline.Session & {"subject": subject}).delete(prompt=False)


def test_a_hold_on_an_unrecorded_missing_path_is_refused(dj_conn, prefix, tmp_path, capsys):
    code = main(["hold", "--session", str(tmp_path / "nowhere" / "2027-03-14_01"),
                 "--verdict", "hold", "--actor", "tester", "--reason", "r", "--prefix", prefix])

    assert code == 1
    assert "no landed session was recorded at" in _refusal(capsys.readouterr().out)


def test_every_restored_file_and_the_final_rename_are_flushed_to_disk(landed, prefix):
    """Follow-up 5: the files and the directory entry the rename makes reach
    the disk before the rehydration is recorded, so a power loss after the
    commit cannot leave a session recorded as restored with truncated files."""
    from wl_preproc.archive import rehydrate as rehydrate_module

    session_dir, _key, nas_root, pristine = _reclaimed(landed, "rhfsync", prefix)
    real_file, real_directory = rehydrate_module._fsync_file, rehydrate_module._fsync_directory
    files, directories = [], []

    def counting_file(handle):
        files.append(handle.name)
        return real_file(handle)

    def counting_directory(path):
        directories.append(path)
        return real_directory(path)

    # The module's two hooks, not `os.fsync`: that is one process-wide
    # function, and patching it would count whatever else flushes meanwhile.
    with (
        patch.object(rehydrate_module, "_fsync_file", counting_file),
        patch.object(rehydrate_module, "_fsync_directory", counting_directory),
    ):
        assert _rehydrate(session_dir, nas_root, prefix) == 0

    assert len(files) == len(pristine)
    assert directories == [session_dir.parent]


def test_a_directory_flush_failing_after_the_rename_says_restored_but_not_recorded(
    landed, prefix, capsys
):
    """The directory flush runs after the rename and before the commit, so its
    failure is the commit-failure case: files in place, no row, reported as
    exactly that."""
    from wl_preproc.archive import rehydrate as rehydrate_module
    from wl_preproc.schema import archive

    session_dir, key, nas_root, pristine = _reclaimed(landed, "rhdirfs", prefix)
    capsys.readouterr()

    def failing(path):
        raise OSError("simulated directory fsync failure")

    with patch.object(rehydrate_module, "_fsync_directory", failing):
        assert _rehydrate(session_dir, nas_root, prefix) == 1

    out = capsys.readouterr().out
    assert f"restored but NOT recorded: {session_dir}" in out
    assert _files(session_dir) == pristine
    assert len(archive.ScratchRehydration & key) == 0
    assert not _staging(session_dir, ".rehydrating").exists()


def test_two_sessions_recorded_at_one_path_are_named_and_chosen_by_subject(
    landed, prefix, capsys
):
    """Follow-up 4. Freeing makes a recorded path reusable: here a second
    session with the same id lands where a freed one was, and is freed too.
    Rehydrating that path is then ambiguous; the refusal names both sessions
    and `--subject` chooses."""
    from wl_preproc.contracts.manifest import SessionManifest
    from wl_preproc.contracts.paths import MANIFEST_FILENAME
    from wl_preproc.ingest.landing import manifest_session_key
    from wl_preproc.ingest.watcher import scan_once
    from wl_preproc.schema import pipeline
    from wl_preproc.synth.recipe import CI_RECIPE
    from wl_preproc.synth.session import generate_session

    first_dir, _first_key, nas_root, first_files = _reclaimed(landed, "rhambA", prefix)
    root = first_dir.parent
    try:
        second = CI_RECIPE.model_copy(update={"subject": "rhambB"})
        generate_session(root, second)
        scan_once(root, prefix=prefix)
        second_key = manifest_session_key(SessionManifest.from_yaml(
            (first_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
        ))
        assert second_key["subject"] == "rhambB"
        _archive(first_dir, prefix)
        _timing(second_key, prefix, tier="A")
        _verdict(second_key, "force", hour=11, prefix=prefix)
        assert _reclaim(first_dir, nas_root, prefix) == 0
        capsys.readouterr()

        assert _rehydrate(first_dir, nas_root, prefix) == 1

        refusal = _refusal(capsys.readouterr().out)
        assert "2 landed sessions were recorded at" in refusal
        assert "rhambA" in refusal and "rhambB" in refusal
        assert "--subject" in refusal

        code = main(["rehydrate", "--session", str(first_dir), "--nas-root", str(nas_root),
                     "--subject", "rhambA", "--prefix", prefix])

        assert code == 0
        assert _files(first_dir) == first_files
    finally:
        (pipeline.Session & {"subject": "rhambB"}).delete(prompt=False)
