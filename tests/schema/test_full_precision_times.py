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
    with pytest.raises(core.MissingTimeRows, match=r"fptimes 2025-09-02 09:00:00: 1 of 1 trial") as raised:
        core.measured_trials([_A, _B])
    # the remedy that works: the daemon never re-runs a session's event stage (the final review's Important 2)
    assert "populate_session" in str(raised.value) and "re-run of the event stage" not in str(raised.value)
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


_C = {"subject": "fptimes", "session_datetime": datetime.datetime(2025, 9, 3, 9, 0)}
_RUN_START = 43_000.1234  # float32 stores it, and a block 0.1 ms after it, alike as 43000.125


@pytest.fixture(scope="module")
def run_c(two_sessions):
    from wl_preproc.schema import core

    _session(_C)
    core.Run.insert1({**_C, "run_number": 1, "task_type": 0, "run_start_time": _RUN_START, "run_stop_time": 43_190.0,
                      "closed": 1}, skip_duplicates=True)
    return [{"run_number": 1, "start_s": _RUN_START, "end_s": 43_190.0}]


def test_a_trial_on_its_run_start_and_a_block_just_after_are_in_the_run_in_the_file_and_the_listing(run_c):
    """Against the stored float32 copy both read 43000.125; with the measured value but a run start still rounded
    to float32, the block, 1.5 ms before 43000.125, would fall out of its run. Either half alone fails here."""
    from wl_preproc.listing.entry import session_entry
    from wl_preproc.nwb import gather
    from wl_preproc.schema import core, pipeline

    core.insert_blocks([{**_C, "block_id": 1, "start_s": _RUN_START + 0.0001, "stop_s": 43_150.0}])
    core.insert_trials([{**_C, "trial_id": 1, "trial_type": None, "start_s": _RUN_START,
                         "stop_s": _RUN_START + 0.02}])
    pipeline.trial.BlockTrial.insert1({**_C, "block_id": 1, "trial_id": 1}, allow_direct_insert=True,
                                      skip_duplicates=True)
    (block,) = gather._blocks(run_c, _C)
    (trial,) = gather._trials(run_c, _C)
    assert (block["run_number"], block["start_s"]) == (1, _RUN_START + 0.0001)
    assert (trial["run_number"], trial["start_s"], trial["stop_s"]) == (1, _RUN_START, _RUN_START + 0.02)
    (run,) = session_entry(_C)["runs"]
    assert [(b["block_number"], b["start_s"]) for b in run["blocks"]] == [(1, _RUN_START + 0.0001)]


def test_a_trial_with_no_time_row_fails_its_coverage(run_c):
    from wl_preproc.schema import core, coverage, pipeline

    pipeline.trial.Trial.insert1({**_C, "trial_id": 9, "trial_start_time": 43_100.0, "trial_stop_time": 43_101.0},
                                 allow_direct_insert=True, skip_duplicates=True)
    with pytest.raises(core.MissingTimeRows, match=r"fptimes 2025-09-03 09:00:00.*trial 9") as raised:
        coverage.TrialCoverage().make({**_C, "trial_id": 9, "system": "syncbox"})
    assert "populate_session" in str(raised.value) and "errored" in str(raised.value)
