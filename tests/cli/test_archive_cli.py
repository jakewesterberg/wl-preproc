"""wlpp archive, reclaim, hold, tape-manifest -- and report.py's two new
sections, which read the same rows for a different purpose (design spec
section 3.2).

**Why every test below actually invokes a command.** The brief's own
`--help` test only proves four names appear in help text, which is silent on
whether any of the four commands do anything -- exactly the shape task-9's
brief warns has passed seven times in this plan without exercising the
feature it named. A `hold` that writes no row, or a `tape-manifest` that
prints an empty manifest when a verified artifact exists, would pass a
`--help` test cleanly. Every other test here inserts or reads a real row
through a real `main([...])` call, against the real MySQL container
`tests/conftest.py` starts, so a dispatch branch that silently no-ops fails
one of these instead.

**Session fixtures land through the real `scan_once`, not a hand-rolled
insert.** `archive`, `reclaim` and `hold` all resolve `--session` off the
session's own manifest (`_session_key_from_dir` in `cli/main.py`) into the
`(subject, session_datetime)` a database row is keyed on, and `ArchiveArtifact`
/ `ReclamationHold` both carry `-> pipeline.Session` -- so every test needs a
real `pipeline.Session` row for the exact key the manifest resolves to before
any of the four commands can write anything. Landing through `scan_once`
(mirroring `tests/cli/test_report.py`'s own `scanned` fixture) gets that row
the same way production will, rather than this file inventing a second way to
derive a session key that could quietly drift from `manifest_session_key`'s.
"""

from __future__ import annotations

import datetime
import re
from unittest.mock import patch

import pytest

from wl_preproc.archive.verify import _expected_digests
from wl_preproc.cli.main import main
from wl_preproc.cli.report import build_report
from wl_preproc.contracts.paths import DONE_MARKER_FILENAME
from wl_preproc.ingest.watcher import scan_once
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def test_every_new_command_is_reachable(capsys):
    assert main(["--help"]) == 0
    helptext = capsys.readouterr().out
    for command in ("archive", "reclaim", "hold", "tape-manifest"):
        assert command in helptext, command


@pytest.fixture
def landed(tmp_path, dj_conn, prefix):
    """Factory: a real CI_RECIPE-shaped session, landed via real `scan_once`
    under a caller-chosen subject. Returns `(session_dir, key)`.

    `dj_conn`/`prefix` are session-scoped (`tests/conftest.py`) and shared by
    the whole suite, so CI_RECIPE's own fixed `subject="pico"` would collide
    with another test's row under the identical key -- `tests/cli/
    test_report.py`'s own `scanned` fixture documents the same trap and the
    same fix: every caller below names its own subject.
    """

    def _land(subject: str):
        # A root PER SUBJECT, not one shared `tmp_path / "scratch"`:
        # CI_RECIPE's `session_id` ("2027-03-14_01") is constant regardless
        # of subject, so a test landing two sessions (`test_tape_manifest_
        # lists_a_verified_session_and_excludes_an_unverified_one`) under one
        # shared root would have its second `generate_session` call
        # overwrite the first session's directory in place -- found by
        # running this fixture with a shared root: the second landed
        # session's manifest silently replaced the first's on disk, and the
        # `archive` command run against the first session's own `session_dir`
        # then archived the SECOND session's data under the first's path.
        root = tmp_path / f"scratch-{subject}"
        root.mkdir(exist_ok=True)
        recipe = CI_RECIPE.model_copy(update={"subject": subject})
        generate_session(root, recipe)
        session_dir = root / recipe.session_id
        scan_once(root, prefix=prefix)

        from wl_preproc.contracts.manifest import SessionManifest
        from wl_preproc.contracts.paths import MANIFEST_FILENAME
        from wl_preproc.ingest.landing import manifest_session_key

        manifest = SessionManifest.from_yaml(
            (session_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        key = manifest_session_key(manifest)
        return session_dir, key

    return _land


def _corrupt_a_done_marker(session_dir):
    """Flip one byte of one file's recorded blake3, the same fault
    `tests/archive/test_stage.py::test_the_sentinel_is_absent_when_
    verification_fails` uses, so `archive_session`'s verification genuinely
    fails rather than this test asserting a failure path it never reaches."""
    marker = next(session_dir.rglob(DONE_MARKER_FILENAME))
    text = marker.read_text(encoding="utf-8")
    marker.write_text(text.replace("blake3: ", "blake3: 0"), encoding="utf-8")


def _digest_of_published_content(store_dir):
    """`archive.store.manifest_digest`'s own algorithm, with the sentinel
    (`archive/stage.py::SENTINEL_NAME`) excluded.

    Not the same thing as calling `manifest_digest` directly on a published
    artifact -- confirmed empirically while writing this, before trusting
    it: `archive_session`'s own publish order is compress, verify, publish,
    confirm, THEN sentinel (`archive/stage.py`'s own module docstring), so
    `write_store` computes and locks in the digest this test compares
    against BEFORE the sentinel file exists anywhere. A real published
    directory therefore always has exactly one more file than whatever the
    recorded digest was computed over, and a bare `manifest_digest(published)`
    call disagrees with the recorded value on every session, archived
    correctly or not -- reproduced directly: a probe session's row digest,
    a bare re-hash of the same published directory, and a re-hash
    excluding the sentinel came back as three different-looking numbers,
    the first and third identical and the second not. This is not a defect
    in `archive/stage.py` to work around quietly: `SENTINEL_NAME`'s own
    purpose is to be written last, after the artifact is already known
    good, so it is process metadata about the archive attempt, not session
    data the digest was ever meant to cover.
    """
    import blake3 as _blake3

    from wl_preproc.archive.stage import SENTINEL_NAME
    from wl_preproc.contracts.done import blake3_file

    digest = _blake3.blake3()
    for path in sorted(
        p for p in store_dir.rglob("*") if p.is_file() and p.name != SENTINEL_NAME
    ):
        digest.update(str(path.relative_to(store_dir)).encode("utf-8"))
        digest.update(blake3_file(path).encode("ascii"))
    return digest.hexdigest()


def _section(body: str, heading: str) -> str:
    """The slice of the report under one `##` heading, and nothing else.

    Duplicated from `tests/cli/test_report.py`'s own helper of the same name
    rather than imported: this repository's test layout is deliberately
    `__init__.py`-free (see this project's own CLAUDE.md), so test files do
    not import fixtures or helpers from one another, and inventing a shared
    conftest helper for five lines is more machinery than it is worth.
    """
    marker = f"\n## {heading}"
    assert marker in body, f"no section headed {heading!r} in:\n{body}"
    return body.split(marker, 1)[1].split("\n## ", 1)[0]


# -- wlpp archive --------------------------------------------------------


def test_archive_writes_the_artifact_row_with_a_nas_relative_path(landed, prefix):
    from wl_preproc.schema import archive

    session_dir, key = landed("arcw1")
    nas_root = session_dir.parent.parent / "nas"

    code = main(
        [
            "archive",
            "--session",
            str(session_dir),
            "--nas-root",
            str(nas_root),
            "--host",
            "vault",
            "--share",
            "cold",
            "--prefix",
            prefix,
        ]
    )

    assert code == 0
    rows = (archive.ArchiveArtifact & key).to_dicts()
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["archive_host"] == "vault"
    assert row["archive_share"] == "cold"
    # Relative to the NAS share, not an absolute local path -- Controller
    # ruling C: "the triple exists so another agent can open the file from
    # elsewhere." An absolute path here would be unusable from any other
    # machine, and would also silently start with "/", which the assertion
    # below catches.
    assert not row["archive_path"].startswith("/")
    # Namespaced by subject (`nas_root/<subject>/<session>.zarr`), not bare
    # `nas_root/<session>.zarr` -- review found that a bare session-id path
    # collides silently between two subjects sharing one session id (a real,
    # reachable state; nothing enforces session ids are subject-scoped --
    # see test_two_subjects_sharing_a_session_id_do_not_collide_on_the_nas
    # below). `archive_path` is still relative to the SHARE root, just with
    # subject as its own leading component.
    published = nas_root / key["subject"] / f"{session_dir.name}.zarr"
    assert row["archive_path"] == str(published.relative_to(nas_root))
    assert row["compressed_bytes"] > 0
    assert len(row["manifest_digest"]) == 64  # blake3 hex

    # Ruling C's central prohibition, pinned directly rather than merely by
    # format: "it cannot re-derive [codec, clevel, compressed_bytes and
    # manifest_digest] by calling write_store a second time -- Blosc's
    # multi-threaded compression is not reproducible... so a second call
    # yields a different, equally valid digest that no longer matches the
    # artifact on the NAS" (archive/stage.py's own `ArchiveOutcome.store`
    # comment). A bare `len(...) == 64` format check cannot tell a reused
    # digest from a freshly (and wrongly) recomputed one -- both are 64 hex
    # characters -- so this compares the row's digest against the digest of
    # the bytes ACTUALLY published to the NAS, computed independently here.
    # A recompute-via-a-second-write_store-call mutation would almost
    # certainly produce a digest that does not match what is really on disk
    # (probed directly: see the report's fix-round section).
    assert row["codec"] == "zstd"
    assert row["clevel"] == 5
    assert row["manifest_digest"] == _digest_of_published_content(published)


def test_archive_prints_verified_and_writes_one_verification_row_per_file(landed, prefix, capsys):
    from wl_preproc.schema import archive

    session_dir, key = landed("arcw2")
    nas_root = session_dir.parent.parent / "nas"
    expected = _expected_digests(session_dir)

    code = main(
        [
            "archive",
            "--session",
            str(session_dir),
            "--nas-root",
            str(nas_root),
            "--host",
            "vault",
            "--share",
            "cold",
            "--prefix",
            prefix,
        ]
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "verified" in out
    assert "NOT verified" not in out
    assert "MISMATCH" not in out

    rows = (archive.ArchiveVerification & key).to_dicts()
    assert len(rows) == len(expected), (len(rows), len(expected))
    assert all(row["matched"] == 1 for row in rows)
    assert {row["relative_path"] for row in rows} == set(expected)


def test_archive_writes_no_rows_when_verification_fails(landed, prefix, capsys):
    from wl_preproc.schema import archive

    session_dir, key = landed("arcw3")
    nas_root = session_dir.parent.parent / "nas"
    _corrupt_a_done_marker(session_dir)

    code = main(
        [
            "archive",
            "--session",
            str(session_dir),
            "--nas-root",
            str(nas_root),
            "--host",
            "vault",
            "--share",
            "cold",
            "--prefix",
            prefix,
        ]
    )
    out = capsys.readouterr().out

    assert code == 1
    assert "MISMATCH" in out
    assert "NOT verified" in out
    assert len(archive.ArchiveArtifact & key) == 0
    assert len(archive.ArchiveVerification & key) == 0


def test_archiving_the_same_session_twice_succeeds_and_the_digest_matches_the_nas(landed, prefix):
    """Review round, CRITICAL. Ruling C's own reasoning ("a second call
    yields a different, equally valid digest that no longer matches the
    artifact on the NAS", `archive/stage.py`'s `ArchiveOutcome.store`
    comment) only holds if the SECOND run's row describes what is actually
    on the NAS once it finishes -- and before this fix it did not.
    DataJoint declares every foreign key `ON DELETE RESTRICT` (`datajoint/
    declare.py`), so a plain `insert1(replace=True)` on `ArchiveArtifact`
    (MySQL's `REPLACE INTO` is DELETE-then-INSERT) raised `IntegrityError`
    while its own `ArchiveVerification` children from the FIRST run still
    existed -- reproduced directly against a real MySQL container before
    this fix, with `archive_session` having ALREADY re-published a new
    artifact to the NAS by the time the crash happened, leaving the
    database recording the first run's digest for bytes that no longer
    existed."""
    from wl_preproc.schema import archive

    session_dir, key = landed("arcrr1")
    nas_root = session_dir.parent.parent / "nas"
    archive_args = [
        "archive",
        "--session",
        str(session_dir),
        "--nas-root",
        str(nas_root),
        "--host",
        "vault",
        "--share",
        "cold",
        "--prefix",
        prefix,
    ]

    assert main(archive_args) == 0
    # Must not raise, and must not leave a stale row -- this is the call
    # that crashed with IntegrityError before this fix.
    assert main(archive_args) == 0

    rows = (archive.ArchiveArtifact & key).to_dicts()
    assert len(rows) == 1, rows
    published = nas_root / key["subject"] / f"{session_dir.name}.zarr"
    assert rows[0]["manifest_digest"] == _digest_of_published_content(published)
    # The child rows survive the re-run too (proving the delete-then-insert
    # path actually completed, not merely that the parent row exists).
    expected = _expected_digests(session_dir)
    assert len(archive.ArchiveVerification & key) == len(expected)


def test_a_failed_rearchive_invalidates_the_prior_good_row(landed, prefix):
    """Review round, Important. `archive_session` publishes to the NAS
    UNCONDITIONALLY, even on a verification failure (`archive/stage.py`:
    the rmtree-and-copytree publish step runs before the confirm/
    all_matched check, not after it) -- so a session archived successfully
    once, then re-archived from a now-corrupted source, has its GOOD NAS
    artifact overwritten by the bad one regardless of the second run's own
    outcome. A stale `ArchiveArtifact` row surviving that would keep telling
    `tape-manifest` and the report's rig-may-clear section this session's
    archive is still verified, when what is actually on the NAS no longer
    is."""
    from wl_preproc.schema import archive

    session_dir, key = landed("arcrr2")
    nas_root = session_dir.parent.parent / "nas"
    archive_args = [
        "archive",
        "--session",
        str(session_dir),
        "--nas-root",
        str(nas_root),
        "--host",
        "vault",
        "--share",
        "cold",
        "--prefix",
        prefix,
    ]

    assert main(archive_args) == 0
    assert len(archive.ArchiveArtifact & key) == 1

    _corrupt_a_done_marker(session_dir)
    code2 = main(archive_args)

    assert code2 == 1
    assert len(archive.ArchiveArtifact & key) == 0
    assert len(archive.ArchiveVerification & key) == 0


def test_a_raising_publish_leaves_no_stale_row_and_drops_off_rig_may_clear(landed, prefix):
    """The BLOCKING finding (Task 10 whole-branch review). Its sibling above,
    `test_a_failed_rearchive_invalidates_the_prior_good_row`, covers the
    CLEAN failure -- `archive_session` returns normally with `all_matched=
    False`. That is not the failure this design most expects. Between
    `rmtree(published)` and `archive_session`'s return sit `copytree` of a
    whole session to a NAS and `manifest_digest(published)` -- exactly the
    calls that raise on a full share, a dropped mount, a permission fault,
    an IO error. Before this fix, a raise there meant `record_archive_
    outcome` -- which used to own the delete -- was never called at all, so
    a PRIOR good row survived describing bytes that might no longer exist.
    `_verified_archives` (`cli/report.py`) reads only that row, never the
    filesystem, so the stale row kept telling "Sessions whose rig may clear
    its copy" and `wlpp tape-manifest` this session's archive was still
    verified -- design spec section 3.2's own channel for telling a rig it
    is safe to delete its only other copy of an irreplaceable recording,
    asserting something the pipeline never actually confirmed.

    Fixed two ways, both exercised here: `archive_session` now invalidates
    the prior row itself, immediately before it starts mutating the NAS
    (`archive/stage.py`'s own comment) -- so no row survives at all, checked
    below. And `_verified_archives` now requires the completion sentinel be
    confirmed present on the NAS (BLOCKING fix 2) -- so even if a row DID
    survive some other way this file's own imagination has not covered, an
    un-sentineled artifact still could not read as verified. Together: the
    DB and the disk have to agree before anything tells a human to delete
    data.
    """
    from wl_preproc.schema import archive

    session_dir, key = landed("rawpub1")
    nas_root = session_dir.parent.parent / "nas"
    archive_args = [
        "archive",
        "--session",
        str(session_dir),
        "--nas-root",
        str(nas_root),
        "--host",
        "vault",
        "--share",
        "cold",
        "--prefix",
        prefix,
    ]

    # A real, good archive first -- the row this bug left stale.
    assert main(archive_args) == 0
    assert len(archive.ArchiveArtifact & key) == 1

    # Re-archive, but the NAS-side confirm raises mid-publish -- patched
    # where `archive_session` actually looks it up (`wl_preproc.archive.
    # stage.manifest_digest`, imported there from `archive.store`), NOT
    # `archive.store.manifest_digest` itself: `write_store`'s OWN internal
    # call to it (computing `StoreResult.manifest_digest` from the LOCAL
    # scratch copy, before anything below even reaches the NAS) resolves
    # through `store.py`'s own module globals and must keep succeeding, or
    # this test would never reach the publish step it exists to probe.
    # `cli/main.py`'s `archive` dispatch has no `try` around `archive_
    # session` (named explicitly in the finding), so this propagates.
    with patch(
        "wl_preproc.archive.stage.manifest_digest",
        side_effect=OSError("simulated NAS fault"),
    ):
        with pytest.raises(OSError):
            main(archive_args)

    assert len(archive.ArchiveArtifact & key) == 0, (
        "a raise mid-publish must not leave the prior row in place -- "
        "'no row' must always honestly mean 'not archived'"
    )
    assert len(archive.ArchiveVerification & key) == 0

    body = build_report(session_dir.parent, prefix=prefix, nas_root=nas_root)
    section = _section(body, "Sessions whose rig may clear its copy")
    assert key["subject"] not in section


def test_two_subjects_sharing_a_session_id_do_not_collide_on_the_nas(landed, prefix):
    """Review round, Important. `archive_session`'s own publish path
    (`archive/stage.py`) is a bare `SessionId` -- date plus index
    (`wl_sync.session`), carrying no subject -- so two different subjects
    recorded under the identical session id (a real, reachable state:
    nothing anywhere enforces that session ids are subject-scoped)
    published to the SAME NAS path before this fix, the second silently
    `rmtree`-ing and replacing the first's bytes while both `ArchiveArtifact`
    rows recorded that same path. CI_RECIPE's own `session_id` is fixed, so
    the `landed` fixture already gives every subject the identical id --
    this test needs no special setup beyond landing two."""
    from wl_preproc.schema import archive

    session_dir_a, key_a = landed("subA")
    session_dir_b, key_b = landed("subB")
    assert session_dir_a.name == session_dir_b.name  # same session_id, different subjects

    nas_root = session_dir_a.parent.parent / "nas"
    for session_dir in (session_dir_a, session_dir_b):
        code = main(
            [
                "archive",
                "--session",
                str(session_dir),
                "--nas-root",
                str(nas_root),
                "--host",
                "vault",
                "--share",
                "cold",
                "--prefix",
                prefix,
            ]
        )
        assert code == 0

    row_a = (archive.ArchiveArtifact & key_a).to_dicts()[0]
    row_b = (archive.ArchiveArtifact & key_b).to_dicts()[0]
    assert row_a["archive_path"] != row_b["archive_path"]

    published_a = nas_root / "subA" / f"{session_dir_a.name}.zarr"
    published_b = nas_root / "subB" / f"{session_dir_b.name}.zarr"
    assert published_a.exists()
    assert published_b.exists()
    assert _digest_of_published_content(published_a) == row_a["manifest_digest"]
    assert _digest_of_published_content(published_b) == row_b["manifest_digest"]


# -- wlpp reclaim ---------------------------------------------------------


def test_reclaim_defaults_to_a_dry_run_and_frees_nothing(landed, prefix):
    from wl_preproc.schema import archive

    session_dir, key = landed("rclmc1")

    code = main(["reclaim", "--session", str(session_dir), "--prefix", prefix])

    assert code == 0
    assert session_dir.exists()
    assert len(archive.ScratchReclamation & key) == 0


def test_reclaim_dry_run_says_so(landed, prefix, capsys):
    session_dir, _key = landed("rclmc2")

    main(["reclaim", "--session", str(session_dir), "--prefix", prefix])
    out = capsys.readouterr().out.lower()

    assert "dry run" in out


def test_reclaim_refuses_a_mismatched_confirmation(landed, prefix, capsys):
    session_dir, _key = landed("rclmc3")

    code = main(
        [
            "reclaim",
            "--session",
            str(session_dir),
            "--no-dry-run",
            "--confirm",
            "not-the-session-path",
            "--prefix",
            prefix,
        ]
    )
    out = capsys.readouterr().out.lower()

    assert code == 2
    assert "refusing" in out


def test_reclaim_never_frees_even_when_confirmed(landed, prefix, capsys):
    """Controller ruling A: reclaim previews and deletes nothing in this
    build -- deliberately, because rehydration is not in this plan. This is
    the test that would fail if a future edit wired a real delete back in:
    the session directory must still exist, and no `ScratchReclamation` row
    may appear, even down the --no-dry-run --confirm path."""
    from wl_preproc.schema import archive

    session_dir, key = landed("rclmc4")

    code = main(
        [
            "reclaim",
            "--session",
            str(session_dir),
            "--no-dry-run",
            "--confirm",
            str(session_dir),
            "--prefix",
            prefix,
        ]
    )
    out = capsys.readouterr().out.lower()

    assert code == 0
    assert session_dir.exists()
    assert len(archive.ScratchReclamation & key) == 0
    assert "refusing" not in out
    # Ruling A: "say why -- that rehydration lands first."
    assert "rehydration" in out


def test_reclaim_prints_every_condition_not_just_the_blocked_ones(landed, prefix, capsys):
    """Design spec section 5.2: a NAMED LIST, not a verdict. A session with
    no archive at all blocks on `artifact_present` -- but
    `no_pending_paramset_or_warm_copy` (hardcoded True today, design spec
    section 5.2's deliberate incompleteness) must still be printed, or an
    operator reading this preview cannot tell "passes" from "was never
    evaluated"."""
    session_dir, _key = landed("rclmc5")

    main(["reclaim", "--session", str(session_dir), "--prefix", prefix])
    out = capsys.readouterr().out

    assert "artifact_present" in out
    assert "no_pending_paramset_or_warm_copy" in out


# -- wlpp hold --------------------------------------------------------------


def test_hold_inserts_a_reclamation_hold_row(landed, prefix):
    from wl_preproc.schema import archive

    session_dir, key = landed("hld1")

    code = main(
        [
            "hold",
            "--session",
            str(session_dir),
            "--verdict",
            "hold",
            "--actor",
            "jake",
            "--reason",
            "investigating a mismatch",
            "--prefix",
            prefix,
        ]
    )

    assert code == 0
    rows = (archive.ReclamationHold & key).to_dicts()
    assert len(rows) == 1, rows
    assert rows[0]["actor"] == "jake"
    assert rows[0]["verdict"] == "hold"
    assert rows[0]["reason"] == "investigating a mismatch"


def test_hold_records_a_force_verdict_too(landed, prefix):
    """Both enum values are real, reachable production states
    (`schema/archive.py`: "a human blocking OR FORCING reclamation") -- a
    CLI that only ever wrote 'hold' regardless of --verdict would still pass
    the test above."""
    from wl_preproc.schema import archive

    session_dir, key = landed("hld2")

    code = main(
        [
            "hold",
            "--session",
            str(session_dir),
            "--verdict",
            "force",
            "--actor",
            "jake",
            "--reason",
            "cleared for reclaim",
            "--prefix",
            prefix,
        ]
    )

    assert code == 0
    rows = (archive.ReclamationHold & key).to_dicts()
    assert len(rows) == 1, rows
    assert rows[0]["verdict"] == "force"


# -- wlpp tape-manifest -----------------------------------------------------


def _archive_and_verify_directly(key, *, n_files: int):
    """An `ArchiveArtifact` row plus `n_files` verified `ArchiveVerification`
    children, inserted straight into the tables rather than through `wlpp
    archive` -- so `n_files=0` can construct the "artifact exists, nothing
    verified yet" state the real CLI never produces on its own (it only ever
    writes both together), which is exactly the trap Controller ruling D
    names: "A session with no verification rows is not staged." Mirrors
    `tests/archive/test_reclaim.py`'s own `_archive_and_verify` helper."""
    from wl_preproc.schema import archive

    archive.ArchiveArtifact.insert1(
        {
            **key,
            "archive_host": "vault",
            "archive_share": "cold",
            "archive_path": f"{key['subject']}/session.zarr",
            "codec": "zstd",
            "clevel": 5,
            "compressed_bytes": 2048,
            "manifest_digest": "e" * 64,
            "compressed_at": datetime.datetime(2027, 5, 1, 10, 0),
        }
    )
    for i in range(n_files):
        archive.ArchiveVerification.insert1(
            {
                **key,
                "relative_path": f"file{i}.bin",
                "expected_blake3": f"exp{i}",
                "actual_blake3": f"exp{i}",
                "matched": 1,
                "verified_at": datetime.datetime(2027, 5, 1, 10, 5),
            }
        )


def test_a_db_row_with_no_sentinel_on_disk_is_not_verified(landed, prefix):
    """BLOCKING fix 2, isolated from fix 1. `test_a_raising_publish_leaves_
    no_stale_row_and_drops_off_rig_may_clear` above proves the invalidation
    fix (fix 1) already keeps a STALE row from surviving a raising publish
    -- but that alone does not prove fix 2 (the sentinel requirement) is
    doing any work of its own, since a session with no row at all is
    trivially excluded either way. This session's row is complete by every
    DB-only measure `_archive_and_verify_directly(n_files=1)` can produce --
    an `ArchiveArtifact` row, one matching `ArchiveVerification` child, the
    same shape the OLD `_verified_archives` (rows only) would have called
    verified -- and `archive_session` never ran for it at all, so fix 1's
    invalidation has no reason to have touched it. What excludes it now is
    specifically that nothing ever wrote `SENTINEL_NAME` under its
    `archive_path` on the (real, if empty) NAS root."""
    from wl_preproc.schema import archive

    session_dir, key = landed("nosent1")
    nas_root = session_dir.parent.parent / "nas"
    # Explicit, not relied on from an earlier `main(["archive", ...])` call
    # in this same test the way other callers of `_archive_and_verify_
    # directly` get it for free: this test never runs a real archive, so
    # nothing else in its own body activates the schema first.
    archive.activate(prefix=prefix)
    _archive_and_verify_directly(key, n_files=1)
    assert len(archive.ArchiveVerification & key) == 1  # the DB half is genuinely complete

    body = build_report(session_dir.parent, prefix=prefix, nas_root=nas_root)

    section = _section(body, "Sessions whose rig may clear its copy")
    assert key["subject"] not in section


def test_tape_manifest_lists_a_verified_session_and_excludes_an_unverified_one(landed, prefix, capsys):
    session_dir, verified_key = landed("tpm1")
    _, unverified_key = landed("tpm2")

    nas_root = session_dir.parent.parent / "nas"
    main(
        [
            "archive",
            "--session",
            str(session_dir),
            "--nas-root",
            str(nas_root),
            "--host",
            "vault",
            "--share",
            "cold",
            "--prefix",
            prefix,
        ]
    )
    # An artifact row with NO verification children -- "artifact exists,
    # nothing verified yet" -- which must never read as staged.
    _archive_and_verify_directly(unverified_key, n_files=0)
    # Cleared here, not left to accumulate: `wlpp archive` above ALSO prints
    # "archived: tpm1 @ ..." to stdout, which itself contains `verified_key
    # ["subject"]` -- found in review (Task 10 whole-branch pass) while
    # threading `--nas-root` through this test: `capsys.readouterr()` was
    # previously called only once, after BOTH commands, so the assertions
    # below passed on the `archive` command's own leftover output even when
    # `tape-manifest` itself printed nothing relevant -- proven directly by
    # reading `out`'s repr before this fix, not merely suspected.
    capsys.readouterr()

    # `--nas-root` is what makes `_verified_archives`' sentinel check
    # (Task 10 whole-branch review, BLOCKING fix 2) actually confirm this
    # session rather than fail closed with "not checked" -- the real
    # sentinel `wlpp archive` wrote above lives under this exact root.
    code = main(["tape-manifest", "--nas-root", str(nas_root), "--prefix", prefix])
    out = capsys.readouterr().out

    assert code == 0
    assert "no sessions" not in out.lower()
    assert "not checked" not in out
    assert verified_key["subject"] in out
    assert unverified_key["subject"] not in out


# -- report.py: two new sections --------------------------------------------


def _timing(key, prefix, *, tier: str):
    """A `TimingProvenance` row pinning `tier`. Mirrors `tests/archive/
    test_reclaim.py`'s own `_timing` helper -- see its docstring for why a
    direct insert into this `dj.Computed` table is an established pattern in
    this codebase, not a novelty."""
    from wl_preproc.schema import timebase

    timebase.activate(prefix=prefix)
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


def test_report_counts_a_landed_but_never_archived_session_separately(landed, prefix):
    """Review round, Important: a landed session that has never been
    archived at all must not read the same as a fine, fully-reclaimable
    one -- `build_report`'s own module docstring states the principle one
    level up: "a missing section and an empty one must never render
    identically". Nothing archives automatically in this build (Task 10,
    not this one), so a session landed via `landed()` alone -- no `wlpp
    archive` call -- is exactly this state, and the section must say so
    rather than silently reading "0"."""
    session_dir, key = landed("rptnv1")

    body = build_report(session_dir.parent, prefix=prefix)

    section = _section(body, "Archived sessions blocked from reclamation")
    assert "never been archived" in section
    # At least the one session this test itself landed -- other tests in
    # this shared-database suite may also contribute landed-but-unarchived
    # sessions, so this checks presence and a nonzero count, not an exact
    # number tied to this test's own isolation.
    assert re.search(r"\d+ landed session\(s\) have never been archived", section)


def test_report_names_a_verified_archive_as_clear_to_the_rig(landed, prefix):
    """`nas_root` passed to `build_report` -- required now (Task 10
    whole-branch review, BLOCKING fix 2): `_verified_archives` confirms the
    completion sentinel on the NAS itself, not just DB rows, and fails
    closed to "not checked" without it.

    Filtered to the subject's own line before checking "vault"/"cold",
    matching the sibling test below (`test_report_names_the_blocking_
    condition_for_an_unreclaimed_session`) rather than searching the whole
    section body (cheap correction, Task 10 whole-branch review): this
    shared-database suite has FOUR other tests in this file that also
    archive with these identical `--host vault --share cold` values under
    this same session-scoped `prefix`, so a bare `"vault" in section` proves
    nothing about THIS row specifically -- it would pass even if this
    test's own session never appeared in the section at all, so long as any
    other verified session did.
    """
    session_dir, key = landed("rptcl1")
    nas_root = session_dir.parent.parent / "nas"
    main(
        [
            "archive",
            "--session",
            str(session_dir),
            "--nas-root",
            str(nas_root),
            "--host",
            "vault",
            "--share",
            "cold",
            "--prefix",
            prefix,
        ]
    )

    body = build_report(session_dir.parent, prefix=prefix, nas_root=nas_root)

    section = _section(body, "Sessions whose rig may clear its copy")
    line = [ln for ln in section.splitlines() if key["subject"] in ln]
    assert len(line) == 1, section
    assert "vault" in line[0]
    assert "cold" in line[0]


def test_report_names_the_blocking_condition_for_an_unreclaimed_session(landed, prefix):
    """A session that is fully archived and verified, but whose timing has
    not been populated yet, is a real reachable state (`TimingProvenance.
    key_source` is sessions with an `Ingestion` row, populated separately --
    `tests/archive/test_reclaim.py::test_no_timing_provenance_row_reports_
    no_tier_resolved`). It must block on `not_tier_d`, named, not merely
    vanish or block on something else."""
    session_dir, key = landed("rptub1")
    nas_root = session_dir.parent.parent / "nas"
    main(
        [
            "archive",
            "--session",
            str(session_dir),
            "--nas-root",
            str(nas_root),
            "--host",
            "vault",
            "--share",
            "cold",
            "--prefix",
            prefix,
        ]
    )
    # Deliberately no _timing() call: TimingProvenance stays empty for this
    # session, so not_tier_d is the one condition that cannot pass.

    body = build_report(session_dir.parent, prefix=prefix)

    section = _section(body, "Archived sessions blocked from reclamation")
    line = [ln for ln in section.splitlines() if key["subject"] in ln]
    assert len(line) == 1, section
    assert "not_tier_d" in line[0]


def test_report_omits_a_fully_reclaimable_session_from_unreclaimed(landed, prefix):
    session_dir, key = landed("rptok1")
    nas_root = session_dir.parent.parent / "nas"
    main(
        [
            "archive",
            "--session",
            str(session_dir),
            "--nas-root",
            str(nas_root),
            "--host",
            "vault",
            "--share",
            "cold",
            "--prefix",
            prefix,
        ]
    )
    _timing(key, prefix, tier="A")

    body = build_report(session_dir.parent, prefix=prefix)

    section = _section(body, "Archived sessions blocked from reclamation")
    assert key["subject"] not in section
