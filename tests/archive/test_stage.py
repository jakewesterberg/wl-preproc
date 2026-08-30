from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest

from wl_preproc.archive.stage import SENTINEL_NAME, ArchiveOutcome, archive_session, record_archive_outcome
from wl_preproc.archive.store import StoreResult
from wl_preproc.archive.verify import FileVerdict
from wl_preproc.contracts.paths import DONE_MARKER_FILENAME
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def test_a_verified_session_gets_a_sentinel(tmp_path, dj_conn, prefix):
    """`archive_session` now touches the database itself (it invalidates any
    stale `ArchiveArtifact` row for `key` before it starts mutating the NAS
    -- see its own comment for why), so this test needs a real connection
    where it did not before. `key`'s `subject` is a throwaway made-up value,
    not landed through `scan_once` anywhere: the invalidation is a bare
    `& key` restriction, which needs no `pipeline.Session` row to exist for
    the key it is restricting on (there is nothing to delete either way, on
    a fresh key), and this test's own claim is about the SENTINEL, not
    about database bookkeeping -- `tests/cli/test_archive_cli.py` already
    covers the full landed-session path end to end.
    """
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    key = {"subject": "stgsnt1", "session_datetime": datetime.datetime(2027, 3, 14, 9, 0)}
    outcome = archive_session(session, tmp_path / "nas", key, prefix=prefix)

    assert outcome.all_matched
    assert (outcome.artifact_path / SENTINEL_NAME).exists()
    # Mirrors the survives-a-failure assertion in the test below: on success,
    # scratch is reclaimed. Computed the same way archive_session computes
    # it -- a private detail with no public accessor, duplicated here rather
    # than exposed from the function just to make a test's job easier.
    scratch = session.parent / f".{session.name}.archiving"
    assert not scratch.exists()


def test_the_sentinel_is_absent_when_verification_fails(tmp_path, dj_conn, prefix):
    """Its whole purpose is telling a prober whole from partial. A sentinel on
    an unverified artifact is worse than none -- wl.works reads `complete` and
    believes it.

    Scratch must survive this path too, and that is pinned below rather than
    left as an unasserted side effect: a confirm-caught transfer corruption
    needs the pre-transfer copy on scratch to show which side -- scratch or
    the NAS -- is actually wrong, and losing it means re-running a
    compression the design flags as heavy on a real session (spec section 10
    item 5). Without this assertion, moving `rmtree(scratch)` back outside
    the `all_matched` gate in stage.py would still pass every other test in
    this file."""
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    marker = next(session.rglob(DONE_MARKER_FILENAME))
    text = marker.read_text(encoding="utf-8")
    # YAML, not JSON: `blake3: <hex>` on its own line.
    marker.write_text(text.replace("blake3: ", "blake3: 0"), encoding="utf-8")

    key = {"subject": "stgsnt2", "session_datetime": datetime.datetime(2027, 3, 14, 9, 0)}
    outcome = archive_session(session, tmp_path / "nas", key, prefix=prefix)
    assert not outcome.all_matched
    assert not (outcome.artifact_path / SENTINEL_NAME).exists()
    # Computed the same way archive_session computes it -- see the comment
    # in test_a_verified_session_gets_a_sentinel above.
    scratch = session.parent / f".{session.name}.archiving"
    assert scratch.exists()


def test_a_failing_verification_insert_rolls_back_the_artifact_row(tmp_path, dj_conn, prefix):
    """Task 10 whole-branch review, cheap correction: `record_archive_
    outcome`'s two inserts now run inside one transaction. Without it, an
    `ArchiveArtifact` insert that succeeds followed by a failing
    `ArchiveVerification` batch left a parent row with NO children --
    `cli/report.py::_verified_archives`' own `len(verifications) == 0`
    guard correctly reads that as "not verified" (so no false rig-may-clear
    claim results), but `daemon.py`'s `_archive_stage_keys()` subtracts
    `ArchiveArtifact` alone, not "`ArchiveArtifact` with verified
    children" -- so the session then left that key source permanently,
    never retried by any later daemon pass. The same never-retried trap
    the row-survival BLOCKING finding names, reached a different way.

    No real `archive_session` call: this is `record_archive_outcome`'s own
    claim, isolated from compression, using a hand-built `ArchiveOutcome`
    the way `daemon.py`'s own docstring already describes it as taking one.
    """
    from wl_preproc.schema import archive, pipeline

    key = {"subject": "txnprb1", "session_datetime": datetime.datetime(2027, 3, 14, 9, 0)}
    # `ArchiveArtifact -> pipeline.Session` is a real foreign key, so the
    # insert this test drives needs a real parent row to reference -- unlike
    # the sentinel tests above, which never insert at all, only `.delete()`
    # (harmless against a key with no parent).
    archive.activate(prefix=prefix)
    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": key["subject"],
            "sex": "U",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    pipeline.Session.insert1(key, skip_duplicates=True)
    nas_root = tmp_path / "nas"
    outcome = ArchiveOutcome(
        artifact_path=nas_root / "txnprb1" / "session.zarr",
        verdicts=[
            FileVerdict(relative_path="a.bin", expected="a" * 64, actual="a" * 64, matched=True)
        ],
        all_matched=True,
        store=StoreResult(
            path=tmp_path / "whatever.zarr",
            codec="zstd",
            clevel=5,
            compressed_bytes=1024,
            manifest_digest="a" * 64,
        ),
    )

    with patch(
        "wl_preproc.schema.archive.ArchiveVerification.insert",
        side_effect=RuntimeError("simulated insert failure"),
    ):
        with pytest.raises(RuntimeError):
            record_archive_outcome(key, outcome, nas_root, "vault", "cold", prefix=prefix)

    assert len(archive.ArchiveArtifact & key) == 0, (
        "the transaction must roll back the ArchiveArtifact insert too, not "
        "leave a parent row with zero children behind"
    )
