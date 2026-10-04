"""What a canonical file leaves out because it lies in no measured run, said
in its description (Plan B's final review, M3). A run whose `RUN_START` was
lost takes its trials and blocks out of every file; `GET /sessions` flags the
blocks `block_outside_runs`, and the file now says so too. Subject and date
checked unclaimed across `tests/` on 2026-10-04."""

from __future__ import annotations

import datetime

import pytest

_SESSION = {"subject": "outrun1", "session_datetime": datetime.datetime(2025, 8, 10, 9, 0)}


@pytest.fixture(scope="module")
def session(dj_conn, prefix):
    """Montage 0 is [0, 1000). Run 1 is [10, 50), holding block 1 and trial 1.
    Block 2 and trial 2 start at 60 s, inside the window and in no run; trial
    3 starts at 2000 s, outside the window."""
    from wl_preproc import daemon
    from wl_preproc.schema import core, pipeline

    daemon.activate_all(prefix=prefix)
    pipeline.lab.Lab.insert1({"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
                             skip_duplicates=True)
    pipeline.subject.Subject.insert1({"subject": _SESSION["subject"], "sex": "U",
                                      "subject_birth_date": datetime.date(2020, 1, 1), "subject_description": ""},
                                     skip_duplicates=True)
    pipeline.Session.insert1(_SESSION, skip_duplicates=True)
    pipeline.event.BehaviorRecording.insert1({**_SESSION, "recording_start_time": _SESSION["session_datetime"]},
                                             skip_duplicates=True)
    core.Montage.insert1({**_SESSION, "montage_id": 0, "start_s": 0.0, "end_s": 1000.0}, skip_duplicates=True)
    core.Run.insert1({**_SESSION, "run_number": 1, "task_type": 0, "run_start_time": 10.0, "run_stop_time": 50.0,
                      "closed": 1}, skip_duplicates=True)
    for block_id, start in ((1, 10.5), (2, 60.0)):
        pipeline.trial.Block.insert1({**_SESSION, "block_id": block_id, "block_start_time": start,
                                      "block_stop_time": start + 5.0}, allow_direct_insert=True, skip_duplicates=True)
    for trial_id, start in ((1, 11.0), (2, 61.0), (3, 2000.0)):
        pipeline.trial.Trial.insert1({**_SESSION, "trial_id": trial_id, "trial_start_time": start,
                                      "trial_stop_time": start + 1.0}, allow_direct_insert=True,
                                     skip_duplicates=True)
    return {**_SESSION, "montage_id": 0, "activation_id": 0}


def test_a_canonical_says_what_lies_in_its_window_but_in_no_run(session):
    from wl_preproc.nwb.gather import _outside_runs_notes

    assert _outside_runs_notes(session, _SESSION, "canonical") == [
        "1 trial(s) and 1 block(s) start in montage 0's window but in no measured run, so the file leaves them "
        "out: their run's RUN_START was lost, or they were strobed outside a run"]


def test_a_derivative_claims_only_its_runs_and_says_nothing(session):
    from wl_preproc.nwb.gather import _outside_runs_notes

    assert _outside_runs_notes(session, _SESSION, "derivative") == []
