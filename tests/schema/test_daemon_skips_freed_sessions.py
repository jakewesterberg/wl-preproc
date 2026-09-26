"""The daemon leaves a freed session alone until it is rehydrated.

Decided by the requester on 2026-09-26: while a session's scratch copy is
freed (its latest `ScratchReclamation` is newer than its latest
`ScratchRehydration`), `daemon.run_once()` does not attempt it at all -- no
stage reads its absent directory, so nothing errors, nothing false is written,
and a later session landing at the same path is never read in its place.
Once it is rehydrated, the next pass picks it up with no manual step.
"""

from __future__ import annotations

import datetime
import shutil

import pytest


def _session_row(prefix, subject):
    from wl_preproc.schema import archive, pipeline

    archive.activate(prefix=prefix)
    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": subject,
            "sex": "U",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    key = {"subject": subject, "session_datetime": datetime.datetime(2027, 5, 1, 9, 0)}
    pipeline.Session.insert1(key, skip_duplicates=True)
    return key


def _freed(key, hour, prefix):
    from wl_preproc.schema import archive

    archive.activate(prefix=prefix)
    archive.ScratchReclamation.insert1(
        {**key, "reclaimed_at": datetime.datetime(2027, 5, 2, hour, 0), "bytes_freed": 1}
    )


def _restored(key, hour, prefix):
    from wl_preproc.schema import archive

    archive.activate(prefix=prefix)
    archive.ScratchRehydration.insert1(
        {**key, "rehydrated_at": datetime.datetime(2027, 5, 2, hour, 0), "bytes_written": 1}
    )


def _is_freed(key, prefix):
    from wl_preproc.archive.scratch import currently_freed

    return key in currently_freed(prefix=prefix)


def test_a_session_never_freed_is_not_freed(dj_conn, prefix):
    key = _session_row(prefix, "frdnone")
    assert not _is_freed(key, prefix)


def test_a_freed_session_is_freed(dj_conn, prefix):
    key = _session_row(prefix, "frdonce")
    _freed(key, 10, prefix)
    assert _is_freed(key, prefix)


def test_a_rehydrated_session_is_not_freed(dj_conn, prefix):
    key = _session_row(prefix, "frdback")
    _freed(key, 10, prefix)
    _restored(key, 11, prefix)
    assert not _is_freed(key, prefix)


def test_a_session_freed_again_after_rehydration_is_freed(dj_conn, prefix):
    key = _session_row(prefix, "frdtwice")
    _freed(key, 10, prefix)
    _restored(key, 11, prefix)
    _freed(key, 12, prefix)
    assert _is_freed(key, prefix)


def test_every_daemon_stage_keys_its_work_by_session(dj_conn, prefix):
    """The skip restricts each stage's work to sessions not currently freed.
    That is only safe for a stage whose `key_source` carries the session key:
    restricting a stage keyed on anything else by "not these sessions" would
    either do nothing or, through `dj.Not`, exclude its every key. Pinned here
    so a future stage that is not per-session fails a test instead of being
    silently skipped whole."""
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    for table in daemon._computed_tables():
        assert {"subject", "session_datetime"} <= set(table.key_source.primary_key), (
            table.__name__
        )


@pytest.fixture
def landed_ci(tmp_path, dj_conn, prefix):
    """A real CI_RECIPE session landed through `scan_once`, under a caller's
    subject, deleted from the shared database at teardown (the reason
    `tests/cli/test_reclaim_and_rehydrate.py::landed` gives)."""
    from wl_preproc.contracts.manifest import SessionManifest
    from wl_preproc.contracts.paths import MANIFEST_FILENAME
    from wl_preproc.ingest.landing import manifest_session_key
    from wl_preproc.ingest.watcher import scan_once
    from wl_preproc.schema import pipeline
    from wl_preproc.synth.recipe import CI_RECIPE
    from wl_preproc.synth.session import generate_session

    subjects = []

    def _land(subject):
        subjects.append(subject)
        root = tmp_path / f"scratch-{subject}"
        root.mkdir()
        recipe = CI_RECIPE.model_copy(update={"subject": subject})
        generate_session(root, recipe)
        session_dir = root / recipe.session_id
        scan_once(root, prefix=prefix)
        manifest = SessionManifest.from_yaml(
            (session_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        return session_dir, manifest_session_key(manifest)

    yield _land
    for subject in subjects:
        (pipeline.Session & {"subject": subject}).delete(prompt=False)


def test_the_daemon_skips_a_freed_session_and_resumes_it_once_rehydrated(
    landed_ci, prefix, tmp_path
):
    """Freed before any stage ran -- the case that used to make the timebase
    stages record `no_recording` and the event stage error on every pass.
    The freeing is simulated the way `free_session` leaves things: a
    `ScratchReclamation` row and no directory at the recorded path."""
    from wl_preproc import daemon
    from wl_preproc.schema import pipeline, timebase

    session_dir, key = landed_ci("dskfree")
    pristine = tmp_path / "pristine"
    shutil.copytree(session_dir, pristine)
    _freed(key, 10, prefix)
    shutil.rmtree(session_dir)

    report = daemon.run_once(prefix=prefix)

    assert [e for e in report["errors"] if "dskfree" in e] == []
    assert len(timebase.SystemTimebase & key) == 0
    assert len(timebase.TimingProvenance & key) == 0
    assert len(pipeline.event.BehaviorRecording & key) == 0

    shutil.copytree(pristine, session_dir)
    _restored(key, 11, prefix)

    report = daemon.run_once(prefix=prefix)

    assert [e for e in report["errors"] if "dskfree" in e] == []
    assert len(timebase.SystemTimebase & key) > 0
    assert len(timebase.TimingProvenance & key) == 1
    assert len(pipeline.event.BehaviorRecording & key) == 1
