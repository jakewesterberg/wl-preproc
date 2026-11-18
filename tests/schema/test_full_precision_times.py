"""Trial and block times stored as measured, in `core.TrialTime` and `core.BlockTime` (design spec
`2026-11-17-full-precision-trial-times-design.md`). element-event keeps a float32 copy, 3.9 ms a step from 9.1 hours
on; these tests go through the database at 12-hour magnitudes, as `test_float_times_round_trip.py` does. Subject
checked unclaimed across `tests/` on 2026-11-17."""

from __future__ import annotations

import datetime

import pytest

_A = {"subject": "fptimes", "session_datetime": datetime.datetime(2025, 9, 1, 9, 0)}
_B = {"subject": "fptimes", "session_datetime": datetime.datetime(2025, 9, 2, 9, 0)}
_LATE = 43_199.9876  # float32 stores 43199.98828125, 0.68 ms off


def _session(key):
    from wl_preproc.schema import ingest, pipeline

    pipeline.Session.insert1(key, skip_duplicates=True)
    ingest.Ingestion.insert1({**key, "ingested_at": key["session_datetime"] + datetime.timedelta(hours=13),
                              "session_dir": f"/landing/{key['session_datetime']:%Y-%m-%d}_01",
                              "integrity": "verified", "topology": {"syncbox": "present"},
                              "manifest_hash": "blake3:test"}, skip_duplicates=True)
    pipeline.event.BehaviorRecording.insert1({**key, "recording_start_time": key["session_datetime"]},
                                             skip_duplicates=True)


@pytest.fixture(scope="module")
def two_sessions(dj_conn, prefix):
    from wl_preproc import daemon
    from wl_preproc.schema import pipeline

    daemon.activate_all(prefix=prefix)
    pipeline.lab.Lab.insert1({"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
                             skip_duplicates=True)
    pipeline.subject.Subject.insert1({"subject": "fptimes", "sex": "U",
                                      "subject_birth_date": datetime.date(2020, 1, 1), "subject_description": ""},
                                     skip_duplicates=True)
    _session(_A)
    _session(_B)


def test_a_trial_and_block_12_hours_in_read_back_as_measured(two_sessions):
    from wl_preproc.schema import core

    core.insert_blocks([{**_A, "block_id": 1, "start_s": _LATE, "stop_s": _LATE + 0.0003}])
    core.insert_trials([{**_A, "trial_id": 1, "trial_type": None, "start_s": _LATE, "stop_s": _LATE + 0.0002}])
    (block,) = core.measured_blocks(_A)
    (trial,) = core.measured_trials(_A)
    assert (block["block_id"], block["start_s"], block["stop_s"]) == (1, _LATE, _LATE + 0.0003)
    assert (trial["trial_id"], trial["start_s"], trial["stop_s"], trial["trial_type"]) == (
        1, _LATE, _LATE + 0.0002, None)


def test_a_trial_with_no_time_row_raises_naming_its_session(two_sessions):
    """element-event's row alone, as a run killed between the two inserts leaves it (ingest is not one
    transaction)."""
    from wl_preproc.schema import core, pipeline

    pipeline.trial.Trial.insert1({**_B, "trial_id": 7, "trial_start_time": 1.0, "trial_stop_time": 2.0},
                                 allow_direct_insert=True, skip_duplicates=True)
    with pytest.raises(core.MissingTimeRows, match=r"fptimes 2025-09-02 09:00:00: 1 of 1 trial"):
        core.measured_trials([_A, _B])
    assert [row["trial_id"] for row in core.measured_trials(_A)] == [1]


def test_a_block_with_no_time_row_raises_too(two_sessions):
    from wl_preproc.schema import core, pipeline

    pipeline.trial.Block.insert1({**_B, "block_id": 3, "block_start_time": 1.0, "block_stop_time": 2.0},
                                 allow_direct_insert=True, skip_duplicates=True)
    with pytest.raises(core.MissingTimeRows, match=r"fptimes 2025-09-02 09:00:00: 1 of 1 block"):
        core.measured_blocks(_B)


def test_a_rerun_fills_the_missing_time_rows(two_sessions):
    from wl_preproc.schema import core

    core.insert_trials([{**_B, "trial_id": 7, "trial_type": None, "start_s": 1.0, "stop_s": 2.0}])
    core.insert_blocks([{**_B, "block_id": 3, "start_s": 1.0, "stop_s": 2.0}])
    assert [(row["trial_id"], row["start_s"]) for row in core.measured_trials(_B)] == [(7, 1.0)]
    assert [(row["block_id"], row["stop_s"]) for row in core.measured_blocks(_B)] == [(3, 2.0)]
