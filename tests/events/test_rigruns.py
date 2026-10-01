"""The rig's own run record, `xcon/runs.jsonl` (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 2.3)."""

from __future__ import annotations

import json


def _record(tmp_path, rows, config=None):
    (tmp_path / "xcon").mkdir()
    (tmp_path / "xcon" / "runs.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    if config is not None:
        (tmp_path / "xcon" / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return tmp_path


def _start(run, task, **fields):
    return json.dumps({"event": "start", "run": run, "at": 0.0, "task": task, **fields})


def _end(run, stopped_because, stop_kind):
    return json.dumps({"event": "end", "run": run, "at": 1.0, "stopped_because": stopped_because,
                       "stop_kind": stop_kind})


def test_a_run_is_its_start_row_and_its_end_row(tmp_path):
    """wl-xcon's `record.py::run_row`: the start row names the task, the end
    row why the run stopped. Before `run_in_session`, a run's number is its
    0-based `run` plus one."""
    from wl_preproc.events.rigruns import RigRun, read_rig_runs

    rows = [_start(0, "fixation_detection"), _end(0, "every block is finished", "completed"),
            _start(1, "rf_map"), _end(1, "stopped by jw", "operator")]
    record = read_rig_runs(_record(tmp_path, rows), "pico")
    assert record.runs == (RigRun(1, "fixation_detection", "every block is finished", "completed"),
                           RigRun(2, "rf_map", "stopped by jw", "operator"))
    assert record.problems == ()


def test_run_in_session_is_the_number_when_the_start_row_carries_it(tmp_path):
    """wl-xcon's session-levels change adds `run_in_session` to the start row
    only; its end row is matched to it through the shared `run`."""
    from wl_preproc.events.rigruns import read_rig_runs

    rows = [_start(0, "rf_map", run_in_session=7), _end(0, "every block is finished", "completed")]
    (run,) = read_rig_runs(_record(tmp_path, rows), "pico").runs
    assert (run.number, run.task, run.stop_kind) == (7, "rf_map", "completed")


def test_a_run_whose_process_was_killed_has_no_stop_reason(tmp_path):
    """wl-xcon writes a run's end row on every way out, a fault included
    (`stop_kind` "fault"); only a killed process leaves none."""
    from wl_preproc.events.rigruns import read_rig_runs

    (run,) = read_rig_runs(_record(tmp_path, [_start(0, "rf_map")]), "pico").runs
    assert (run.stopped_because, run.stop_kind) == (None, None)


def test_a_row_it_cannot_read_is_reported_by_line_and_left_out(tmp_path):
    from wl_preproc.events.rigruns import read_rig_runs

    rows = [_start(0, "rf_map"), "{not json", json.dumps({"event": "start"}), json.dumps({"event": "pause", "run": 0})]
    record = read_rig_runs(_record(tmp_path, rows), "pico")
    assert [run.number for run in record.runs] == [1]
    assert [problem.split(":")[0] for problem in record.problems] == ["line 2", "line 3", "line 4"]


def test_a_record_of_another_animal_is_not_read(tmp_path):
    """Its rows carry no subject; its `config.json` does."""
    from wl_preproc.events.rigruns import read_rig_runs

    record = read_rig_runs(_record(tmp_path, [_start(0, "rf_map")], config={"subject": "other"}), "pico")
    assert record.runs == ()
    assert len(record.problems) == 1 and "'other'" in record.problems[0]


def test_a_session_without_a_record_has_none(tmp_path):
    from wl_preproc.events.rigruns import read_rig_runs

    assert read_rig_runs(tmp_path, "pico") is None


def test_a_run_named_twice_keeps_its_first_rows_and_says_so(tmp_path):
    """The requester's decision 2, for the rig's own record: the first is
    listed (final review M3)."""
    from wl_preproc.events.rigruns import RigRun, read_rig_runs

    rows = [_start(0, "rf_map"), _end(0, "every block is finished", "completed"),
            _start(0, "fixation"), _end(0, "stopped by jw", "operator")]
    record = read_rig_runs(_record(tmp_path, rows), "pico")
    assert record.runs == (RigRun(1, "rf_map", "every block is finished", "completed"),)
    assert record.problems == ("line 3: run 0 has a second start row; the first is kept",
                               "line 4: run 0 has a second end row; the first is kept")
