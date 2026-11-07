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
    assert record.runs == (RigRun(1, "fixation_detection", "every block is finished", "completed", start_at=0.0,
                                  end_at=1.0),
                           RigRun(2, "rf_map", "stopped by jw", "operator", start_at=0.0, end_at=1.0))
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
    assert [problem.split(":")[0] for problem in record.problems] == ["runs.jsonl line 2", "runs.jsonl line 3",
                                                                   "runs.jsonl line 4"]


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
    assert record.runs == (RigRun(1, "rf_map", "every block is finished", "completed", start_at=0.0, end_at=1.0),)
    assert record.problems == ("runs.jsonl line 3: run 0 has a second start row; the first is kept",
                               "runs.jsonl line 4: run 0 has a second end row; the first is kept")


def test_a_runs_calibration_is_its_start_rows(tmp_path):
    """wl-xcon's engine build B: each start row names the display calibration
    the run ran against (design spec `2026-10-14-session-kind-and-warnings-design.md`
    section 1); a row written before it names none."""
    from wl_preproc.events.rigruns import read_rig_runs

    rows = [_start(0, "rf_map", calibration="rig-a-2026-10-01"), _end(0, "every block is finished", "completed"),
            _start(1, "rf_map")]
    runs = read_rig_runs(_record(tmp_path, rows), "pico").runs
    assert [run.calibration for run in runs] == ["rig-a-2026-10-01", None]


def test_each_of_wl_xcons_runs_maps_to_its_number(tmp_path):
    """A warning names its run as wl-xcon counts it, from 0; the run's number
    is the start row's `run_in_session`, or that `run` plus one before it."""
    from wl_preproc.events.rigruns import read_rig_runs

    rows = [_start(0, "rf_map", run_in_session=7), _end(0, "every block is finished", "completed"),
            _start(1, "rf_map")]
    assert read_rig_runs(_record(tmp_path, rows), "pico").run_numbers == {0: 7, 1: 2}


def test_a_run_without_its_start_row_maps_to_no_number(tmp_path):
    """A warning joins a run only through its start row (design spec
    `2026-10-14-session-kind-and-warnings-design.md` section 2): a run whose
    start line was lost keeps its stop reason by `run` + 1, but that guess is
    not a number to join a warning by, since wl-xcon's numbers part from it
    once a session resumes (the session kind's deferred minor 3)."""
    from wl_preproc.events.rigruns import read_rig_runs

    rows = [_start(0, "rf_map"), _end(0, "every block is finished", "completed"),
            _end(1, "stopped by jw", "operator")]
    record = read_rig_runs(_record(tmp_path, rows), "pico")
    assert [run.number for run in record.runs] == [1, 2]
    assert record.run_numbers == {0: 1}


def test_a_runs_instants_are_its_rows_on_the_rigs_clock(tmp_path):
    """Each row's `at`, the rig's anchored wall clock, which the training
    records' clock fit matches to the run's codes (design spec
    `2026-10-18-training-records-and-backup-clock-design.md` section 3.3); an
    instant that is not a finite number is none."""
    from wl_preproc.events.rigruns import read_rig_runs

    rows = [_start(0, "rf_map", at=1773478800.5), _end(0, "every block is finished", "completed"),
            _start(1, "rf_map", at="soon"), json.dumps({"event": "end", "run": 1, "stopped_because": "stopped by jw"})]
    runs = read_rig_runs(_record(tmp_path, rows), "pico").runs
    assert [(run.start_at, run.end_at) for run in runs] == [(1773478800.5, 1.0), (None, None)]


def test_since_c2b_a_run_names_its_plan_and_how_each_block_ended(tmp_path):
    """Design spec `2026-11-07-rig-record-fields-design.md` section 5, in
    the shape `wl_xcon/taskd.py`'s two `run_row` calls write: the start row's
    plan, plan as declared, task hash, seed, draws, factorial and direction
    settings; the end row's counts, end reason, block ends, shortfalls and
    per-condition counts."""
    from wl_preproc.events.rigruns import BlockEnd, ConditionCount, Shortfall, read_rig_runs

    start = _start(0, "visual_search", plan="training", plan_form={"class": "Plan", "fields": {}},
                   versions={"task": "sha256:ab12", "allocation": "a.yaml"}, seed=1234,
                   draws={"hold": {"class": "Uniform", "fields": {"high": 0.8, "low": 0.2, "repeat": "fresh"}}},
                   generated=[], directions=["target_dir"])
    end = json.dumps({**json.loads(_end(0, "its plan's last block ended", "completed")), "trials": 40,
                      "presentations": 43, "end_reason": "plan_done",
                      "block_ends": [{"block": "easy", "ended": "rule", "by": None, "after_trial": 19,
                                      "block_in_session": 1, "position": 0, "passed_at": None,
                                      "varied": {"hold": [{"by": "draw"}]}, "planned": None, "counts": {}},
                                     {"block": "hard", "ended": "next", "by": {"kind": "member", "name": "jw"},
                                      "after_trial": 42, "block_in_session": None, "position": 1,
                                      "passed_at": None}],
                      "shortfalls": [{"block": "easy", "condition": "c1", "trials": 2, "because": "cap",
                                      "possible_avoidance": True, "after_trial": 17, "by": None}],
                      "conditions": [{"block": "easy", "condition": "c1", "trials": 20, "presentations": 23,
                                      "right": 15, "wrong": 5, "right_first": 12, "owed": 0, "repeats": 3,
                                      "moved_past": 2, "possible_avoidance": True, "drummed": 3, "failing": 0}]})
    (run,) = read_rig_runs(_record(tmp_path, [start, end]), "pico").runs
    assert (run.plan, run.plan_form, run.task_hash, run.seed, run.directions) == (
        "training", {"class": "Plan", "fields": {}}, "sha256:ab12", 1234, ("target_dir",))
    assert run.draws["hold"]["class"] == "Uniform" and run.generated == []
    assert (run.rig_trials, run.presentations, run.end_reason, run.index) == (40, 43, "plan_done", 0)
    assert run.block_ends == (
        BlockEnd("easy", "rule", None, 19, 1, 0, None, varied={"hold": [{"by": "draw"}]}, planned=None, counts={}),
        BlockEnd("hard", "next", "member", 42, None, 1, None))
    assert run.shortfalls == (Shortfall("easy", "c1", 2, "cap", True, 17, None),)
    assert run.conditions == (ConditionCount("easy", "c1", 20, 23, 15, 5, 12, 0, 3, 2, True, 3, 0),)
    assert run.carries >= {"plan", "task_hash", "seed", "rig_trials", "block_ends", "stop_kind"}


def _start_before_c2a(run, task_version):
    """A start row as wl-xcon wrote it before its engine build C2a
    (`taskd.py`'s `run_row("start", ...)`, read at its `main` of
    2026-10-16): the allocation and the task's version, the planned count,
    the seed, the blocks and the values it started with."""
    return _start(run, "rf_map", calibration=None, allocation="tasks/allocation.py",
                  versions={"task": task_version, "allocation": "tasks/allocation.py"}, trials=40, seed=7,
                  blocks=None, layers={"task": {}, "run": {}}, resolved={}, bounded={})


def _end_before_c2a(run):
    """An end row as wl-xcon wrote it before C2a: why it stopped, the trials
    it ran, its blocks and whether its code went out."""
    return json.dumps({**json.loads(_end(run, "every block is finished", "completed")), "trials": 38,
                       "blocks_run": ["main"], "strobed": True})


def test_rows_before_c2a_carry_what_wl_xcon_wrote_before_it(tmp_path):
    """Every start row since runs.jsonl began (wl-xcon's `8855083`,
    2026-09-29) names the task's version and the seed, and every end row the
    trials it ran: those are read; none of C2a's, C2b's or C3's fields is
    (the final review's I2)."""
    from wl_preproc.events.rigruns import read_rig_runs

    rows = [_start_before_c2a(0, "sha256:5d1f"), _end_before_c2a(0)]
    record = read_rig_runs(_record(tmp_path, rows), "pico")
    (run,) = record.runs
    assert run.carries == {"stopped_because", "stop_kind", "task_hash", "seed", "rig_trials"}
    assert (run.task_hash, run.seed, run.rig_trials) == ("sha256:5d1f", 7, 38)
    assert (run.plan, run.block_ends, run.shortfalls, run.presentations, run.end_reason) == (None, (), (), None, None)
    assert record.problems == ()


def test_a_task_named_by_its_path_has_no_content_hash(tmp_path):
    """From 2026-09-29 until its engine build C1 (`ef23b25`, 2026-10-11)
    wl-xcon's `versions.task` was the task's path; only a content hash,
    `sha256:` and its digest (`declarations.content_hash`), is published as
    one, and a path is not a problem to report (the final review's I2)."""
    from wl_preproc.events.rigruns import read_rig_runs

    rows = [_start_before_c2a(0, "tasks/fixation_detection.py"), _end_before_c2a(0)]
    record = read_rig_runs(_record(tmp_path, rows), "pico")
    (run,) = record.runs
    assert (run.task_hash, "task_hash" in run.carries, record.problems) == (None, False, ())


def test_a_run_field_it_cannot_read_is_none_and_said_by_its_path(tmp_path):
    from wl_preproc.events.rigruns import read_rig_runs

    end = json.dumps({**json.loads(_end(0, "done", "completed")), "presentations": "43",
                      "block_ends": [{"block": "easy", "ended": 3, "after_trial": 1.5}]})
    record = read_rig_runs(_record(tmp_path, [_start(0, "rf_map", seed="1234"), end]), "pico")
    (run,) = record.runs
    assert (run.seed, run.presentations, run.block_ends[0].ended, run.block_ends[0].after_trial) == (
        None, None, None, None)
    assert record.problems == (
        "1 run row: `block_ends[].after_trial` is not a whole number or null, so it is read as none",
        "1 run row: `block_ends[].ended` is not text or null, so it is read as none",
        "1 run row: `presentations` is not a whole number or null, so it is read as none",
        "1 run row: `seed` is not a whole number or null, so it is read as none",
    )
