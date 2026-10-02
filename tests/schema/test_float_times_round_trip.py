"""Block and trial times hours into a session, written to the database and
read back (Plan B's final review, C1). element-event stores them as MySQL
FLOAT, and MySQL's text protocol, the one DataJoint reads through, returns a
FLOAT to six significant digits: 18000.124 comes back as 18000.1, before a
`RUN_START` at 18000.1234. `tests/nwb/test_helpers.py`'s containment test
models the stored value as float32 and so cannot see this; these tests go
through the database. Subject and date checked unclaimed across `tests/` on
2026-10-02."""

from __future__ import annotations

import datetime

import numpy as np
import pytest

_SESSION = {"subject": "flt32", "session_datetime": datetime.datetime(2025, 8, 9, 9, 0)}
_RUN_START, _RUN_STOP = 18000.1234, 18050.1234
# 0.6 ms after RUN_START, as amendment 11 describes; and a 20 ms trial, an
# abort, which six significant digits make zero seconds long.
_BLOCK_START, _BLOCK_STOP = 18000.124, 18040.0
_SHORT_TRIAL = (18000.124, 18000.144)


@pytest.fixture(scope="module")
def hours_in(dj_conn, prefix):
    from wl_preproc import daemon
    from wl_preproc.schema import core, ingest, pipeline

    daemon.activate_all(prefix=prefix)
    pipeline.lab.Lab.insert1({"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
                             skip_duplicates=True)
    pipeline.subject.Subject.insert1({"subject": _SESSION["subject"], "sex": "U",
                                      "subject_birth_date": datetime.date(2020, 1, 1), "subject_description": ""},
                                     skip_duplicates=True)
    pipeline.Session.insert1(_SESSION, skip_duplicates=True)
    ingest.Ingestion.insert1({**_SESSION, "ingested_at": datetime.datetime(2025, 8, 9, 19, 0),
                              "session_dir": "/landing/2025-08-09_01", "integrity": "verified",
                              "topology": {"syncbox": "present"}, "manifest_hash": "blake3:test"},
                             skip_duplicates=True)
    pipeline.event.BehaviorRecording.insert1({**_SESSION, "recording_start_time": _SESSION["session_datetime"]},
                                             skip_duplicates=True)
    core.Run.insert1({**_SESSION, "run_number": 1, "task_type": 0, "run_start_time": _RUN_START,
                      "run_stop_time": _RUN_STOP, "closed": 1}, skip_duplicates=True)
    pipeline.trial.Block.insert1({**_SESSION, "block_id": 1, "block_start_time": _BLOCK_START,
                                  "block_stop_time": _BLOCK_STOP}, allow_direct_insert=True, skip_duplicates=True)
    pipeline.trial.Trial.insert1({**_SESSION, "trial_id": 1, "trial_start_time": _SHORT_TRIAL[0],
                                  "trial_stop_time": _SHORT_TRIAL[1]}, allow_direct_insert=True,
                                 skip_duplicates=True)
    pipeline.trial.BlockTrial.insert1({**_SESSION, "block_id": 1, "trial_id": 1}, allow_direct_insert=True,
                                      skip_duplicates=True)
    return [{"run_number": 1, "start_s": _RUN_START, "end_s": _RUN_STOP}]


def test_a_block_and_trial_just_after_an_hours_in_run_start_are_in_its_file(hours_in):
    """Neither is dropped from the NWB file, and each keeps the time stored,
    not one rounded to 0.1 s."""
    from wl_preproc.nwb.gather import _blocks, _trials

    (block,) = _blocks(hours_in, _SESSION)
    assert (block["block_number"], block["run_number"], block["block_in_run"]) == (1, 1, 1)
    assert block["start_s"] == float(np.float32(_BLOCK_START))
    (trial,) = _trials(hours_in, _SESSION)
    assert (trial["trial_id"], trial["run_number"], trial["block_id"]) == (1, 1, 1)
    assert (trial["start_s"], trial["stop_s"]) == tuple(float(np.float32(t)) for t in _SHORT_TRIAL)


def test_the_listing_finds_the_block_in_its_run(hours_in):
    from wl_preproc.listing.entry import session_entry

    entry = session_entry(_SESSION)
    (run,) = entry["runs"]
    assert [block["block_number"] for block in run["blocks"]] == [1]
    assert "block_outside_runs" not in {flag["code"] for flag in entry["flags"]}


def test_a_short_trial_hours_in_has_its_coverage_computed(hours_in):
    """20 ms read as 18000.1 to 18000.1 is no length at all, and
    `classify_coverage` refuses it: the stage would fail on every pass."""
    from wl_preproc.schema import core, coverage

    core.AcquisitionSystem.insert1({**_SESSION, "system": "syncbox"}, skip_duplicates=True)
    core.Segment.insert1({**_SESSION, "system": "syncbox", "segment_barcode": 1, "file_path": "syncbox/a.bin",
                          "start_s": 0.0, "end_s": 30000.0, "n_samples": 1, "first_sample": 0, "offset_s": 0.0,
                          "residual_us": 0.0, "n_barcodes": 1, "n_frame_gaps": 0, "n_frames_missing": 0,
                          "n_barcodes_dropped": 0}, allow_direct_insert=True, skip_duplicates=True)
    coverage.TrialCoverage.populate(_SESSION)
    row = (coverage.TrialCoverage & _SESSION & {"trial_id": 1, "system": "syncbox"}).fetch1()
    assert row["coverage"] == "full"
    assert row["covered_s"] == pytest.approx(float(np.float32(_SHORT_TRIAL[1])) - float(np.float32(_SHORT_TRIAL[0])))
