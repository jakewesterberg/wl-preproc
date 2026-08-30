"""`wlpp daemon`'s archival stage: archive exactly the sessions design spec
section 3.1 says to, and nothing else.

Design spec section 3.1 (2026-08-27 note): "Archival is triggered as soon as
ingest verification passes, before anything else touches the session."
Controller ruling A settles WHERE this attaches -- `wl_preproc/daemon.py`,
not `wl_preproc/ingest/watcher.py` -- because `archive_session` compresses a
whole session (an hour, per this task's own brief) and hooking it into
`scan_once` would hang `wlpp ingest` for that long with no output, sitting
under `_scan_one`'s exception boundary besides, which would misdiagnose a
full NAS or a transient IO fault as a defect in the session's own files.

`archive_session` is mocked in every test below rather than run for real:
these tests are about the TRIGGER decision -- which sessions get handed to
it -- not about the hour of compression itself.

**Every assertion below inspects `call_args_list` for THIS test's own
`session_dir`, never the bare `.called` flag.** `tests/conftest.py`'s
`dj_conn`/`prefix` fixtures are session-scoped: this suite runs one MySQL
database, under one prefix, for its whole process, with no truncation
between tests or files. `daemon.run_once`'s archival stage has no
per-session filter -- by design, it sweeps every verified, unarchived
session in `ingest.Ingestion` -- so by the time any one test here runs,
OTHER files' fixtures may already have landed other verified-but-unarchived
sessions into that same shared table. A bare `assert not archived.called`
on a negative test would then be hostage to whatever else the shared suite
happened to land first, which is exactly the shape this task's own brief
warns against: "a test asserting `archive_session` 'was called' proves
nothing about whether it is called *only* on verified sessions." Checking
`call_args_list` for this test's own `session_dir` specifically sidesteps
that regardless of what else the shared database holds.

Every test lands its session under a dedicated subject, never CI_RECIPE's
own `subject="pico"` -- `tests/ingest/test_watcher.py`'s own
`_use_dedicated_subject` documents the identical collision this avoids.
Mirrored here rather than imported: this repository's test layout is
deliberately `__init__.py`-free (this project's own CLAUDE.md), so test
files do not share helpers.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import patch

from wl_preproc import daemon
from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import DONE_MARKER_FILENAME, MANIFEST_FILENAME
from wl_preproc.ingest.landing import manifest_session_key
from wl_preproc.ingest.watcher import scan_once
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def _corrupt_a_done_marker(session_dir: Path) -> None:
    """Disagree the declared byte count with the real file's size, so
    `ingest/verify.py`'s "a size mismatch is decisive and cheap" check finds
    it before hashing ever runs -- the same corruption this task's own brief
    specifies for its third test."""
    marker = next(session_dir.rglob(DONE_MARKER_FILENAME))
    marker.write_text(
        marker.read_text(encoding="utf-8").replace("bytes: ", "bytes: 1"), encoding="utf-8"
    )


def _empty_a_done_marker(session_dir: Path) -> None:
    """Empty exactly one system's DONE marker. `ingest/verify.py`'s own
    docstring: "Returns DECLARED_ONLY if *any* system's marker was empty,
    because a session is verified only if everything in it was" --
    `tests/ingest/test_verify.py::test_an_empty_marker_yields_declared_only_
    rather_than_verified` confirms the mechanism this borrows; unlike that
    test this empties only one system, proving the SESSION-wide downgrade
    from a single empty marker, not merely the case where every marker is
    empty."""
    marker = next(session_dir.rglob(DONE_MARKER_FILENAME))
    marker.write_text("", encoding="utf-8")


def _land(tmp_path: Path, prefix: str, subject: str, *, verify: bool = True, mutate=None):
    """A dedicated-subject CI_RECIPE session, landed through the real
    `scan_once`. Returns `(session_dir, key)`.

    A root PER SUBJECT (`tmp_path / f"scratch-{subject}"`), not one shared
    `tmp_path` -- mirrors `tests/cli/test_archive_cli.py`'s own `landed`
    fixture, which documents the identical need: a shared root would let a
    second call's `generate_session` overwrite the first session's directory
    in place, since CI_RECIPE's `session_id` is fixed regardless of subject.

    `mutate`, if given, runs on `session_dir` after the session is written
    and before `scan_once` -- the one hook point every landing-time fault
    (a corrupted DONE marker, an emptied one) needs, applied before the
    watcher ever reads the directory.
    """
    root = tmp_path / f"scratch-{subject}"
    root.mkdir(exist_ok=True)
    recipe = CI_RECIPE.model_copy(update={"subject": subject})
    generate_session(root, recipe)
    session_dir = root / recipe.session_id

    if mutate is not None:
        mutate(session_dir)

    scan_once(root, prefix=prefix, verify=verify)

    manifest = SessionManifest.from_yaml(
        (session_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    key = manifest_session_key(manifest)
    return session_dir, key


def _called_session_dirs(mock) -> list[Path]:
    """Every `session_dir` -- the stage's own first positional argument to
    `archive_session` -- some call actually used. See this module's own
    docstring for why every assertion below checks this rather than the bare
    `.called` flag."""
    return [call.args[0] for call in mock.call_args_list]


def test_a_verified_session_is_handed_to_archival(tmp_path, dj_conn, prefix):
    """Design spec section 3.1: archival runs as soon as ingest verification
    passes, before anything else touches the session -- not after
    processing."""
    session_dir, _key = _land(tmp_path, prefix, "trig1", verify=True)

    with patch("wl_preproc.daemon.archive_session") as archived:
        # `all_matched = False` keeps `record_archive_outcome` on its
        # delete-only path (no insert), so this test needs no realistic
        # `StoreResult` -- it is exercising the TRIGGER, not the bookkeeping
        # `tests/cli/test_archive_cli.py` already covers end to end.
        archived.return_value.all_matched = False
        archived.return_value.verdicts = []
        daemon.run_once(prefix=prefix, nas_root=tmp_path / "nas", host="vault", share="cold")

    assert session_dir in _called_session_dirs(archived)


def test_a_session_ingested_with_no_verify_is_not_archived(tmp_path, dj_conn, prefix):
    """`--no-verify` makes `ingest/verify.py` return `Integrity.SKIPPED` with
    an EMPTY mismatch list, so a check that branched on the mismatch list
    alone could not tell it apart from a genuine pass. The trigger must
    branch on the recorded integrity value, not on the session having
    landed -- design spec section 3.1's 2026-08-27 note.

    It matters because reconstruction reproduces identical bytes for any
    valid factorization (section 4), so an unverified session with a torn
    `time.dat` produces a silently wrong shape in an artifact that verifies
    clean.
    """
    session_dir, _key = _land(tmp_path, prefix, "trig2", verify=False)

    with patch("wl_preproc.daemon.archive_session") as archived:
        daemon.run_once(prefix=prefix, nas_root=tmp_path / "nas", host="vault", share="cold")

    assert session_dir not in _called_session_dirs(archived)


def test_a_declared_only_session_is_not_archived(tmp_path, dj_conn, prefix):
    """Controller ruling B: `integrity` is a THREE-value enum
    (`schema/ingest.py`: `enum('verified','declared_only','skipped')`), and a
    test that only covers `verified` and `skipped` leaves `declared_only`
    unexercised -- ruling B's own words. A negative check
    (`integrity != "skipped"`) would silently admit this state alongside a
    genuine pass; equality against `"verified"` excludes it by construction,
    which is what this test pins. One system's marker is emptied rather than
    corrupted: an empty marker parses cleanly (no mismatch, so the session
    still lands) but declares nothing was actually checked for that system,
    which is the `declared_only` state itself -- `ingest/verify.py`'s own
    "a session is verified only if everything in it was".
    """
    session_dir, _key = _land(
        tmp_path, prefix, "trig5", verify=True, mutate=_empty_a_done_marker
    )

    with patch("wl_preproc.daemon.archive_session") as archived:
        daemon.run_once(prefix=prefix, nas_root=tmp_path / "nas", host="vault", share="cold")

    assert session_dir not in _called_session_dirs(archived)


def test_a_quarantined_session_is_not_archived(tmp_path, dj_conn, prefix):
    """Verification failing is exactly when NOT to spend an hour compressing
    -- but that claim is true BY CONSTRUCTION here, not because of anything
    this task's own trigger logic decides. A quarantined session never gets
    an `ingest.Ingestion` row at all (`ingest/watcher.py`'s
    `_evaluate_session` returns before `landing.land_session` ever runs), so
    it cannot appear in `_archive_stage_keys()`'s query regardless of what
    `_archive_stage` does with it -- no change to THIS task's own code would
    make this test fail; only a change to `watcher.py`'s quarantine logic
    itself would, a different subsystem with its own tests
    (`tests/ingest/test_watcher.py::
    test_a_corrupted_file_quarantines_as_checksum_mismatch` is where that
    guarantee actually lives, and is what `_corrupt_a_done_marker` below
    mirrors).

    What this test earns its place for instead: end-to-end confidence with
    NOTHING mocked but `archive_session` itself -- a real `generate_session`,
    a real `scan_once` (so the corruption genuinely routes through
    `verify_session` and `landing.quarantine` rather than this test assuming
    it would), and a real `daemon.run_once` pass. Confirmed directly, not
    assumed: re-running this with the corruption removed flips the
    assertion (the same session then gets archived) -- see this task's own
    report for that probe -- which is what shows the fixture is doing real
    work rather than this test passing for a reason unrelated to its own
    setup.
    """
    session_dir, _key = _land(
        tmp_path, prefix, "trig3", verify=True, mutate=_corrupt_a_done_marker
    )

    with patch("wl_preproc.daemon.archive_session") as archived:
        daemon.run_once(prefix=prefix, nas_root=tmp_path / "nas", host="vault", share="cold")

    assert session_dir not in _called_session_dirs(archived)


def test_an_already_archived_session_is_not_archived_again(tmp_path, dj_conn, prefix):
    """`run_once`'s archival stage must reach a steady state like every other
    stage (`tests/schema/test_daemon.py::test_run_once_reports_what_it_did`'s
    own "steady state is stable" principle) -- otherwise every pass would
    re-spend the hour `archive_session` costs on a session already safely on
    the NAS, forever. Proven directly against a real `ArchiveArtifact` row
    (inserted the same minimal way `tests/cli/test_archive_cli.py`'s own
    `_archive_and_verify_directly` helper does, not through a mocked
    `archive_session` call, which would need a realistic `StoreResult` this
    test has no reason to fabricate) rather than assumed from the query's
    shape.
    """
    from wl_preproc.schema import archive as archive_schema

    session_dir, key = _land(tmp_path, prefix, "trig4", verify=True)

    archive_schema.activate(prefix=prefix)
    archive_schema.ArchiveArtifact.insert1(
        {
            **key,
            "archive_host": "vault",
            "archive_share": "cold",
            "archive_path": f"{key['subject']}/{session_dir.name}.zarr",
            "codec": "zstd",
            "clevel": 5,
            "compressed_bytes": 2048,
            "manifest_digest": "e" * 64,
            "compressed_at": datetime.datetime(2027, 5, 1, 10, 0),
        }
    )

    with patch("wl_preproc.daemon.archive_session") as archived:
        daemon.run_once(prefix=prefix, nas_root=tmp_path / "nas", host="vault", share="cold")

    assert session_dir not in _called_session_dirs(archived)
