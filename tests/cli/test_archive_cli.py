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
    published = nas_root / f"{session_dir.name}.zarr"
    assert row["archive_path"] == str(published.relative_to(nas_root))
    assert row["compressed_bytes"] > 0
    assert len(row["manifest_digest"]) == 64  # blake3 hex


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

    code = main(["tape-manifest", "--prefix", prefix])
    out = capsys.readouterr().out

    assert code == 0
    assert "no sessions" not in out.lower()
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


def test_report_names_a_verified_archive_as_clear_to_the_rig(landed, prefix):
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

    body = build_report(session_dir.parent, prefix=prefix)

    section = _section(body, "Sessions whose rig may clear its copy")
    assert key["subject"] in section
    assert "vault" in section
    assert "cold" in section


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

    section = _section(body, "Unreclaimed sessions")
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

    section = _section(body, "Unreclaimed sessions")
    assert key["subject"] not in section
