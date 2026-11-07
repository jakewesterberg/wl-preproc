"""What the rig's record says of each run and block beyond its task and
calibration (design spec `2026-11-07-rig-record-fields-design.md` section 5)."""

from __future__ import annotations

from wl_preproc.events.rigruns import BlockEnd, ConditionCount, RigRun, RigRuns, Shortfall


def _run(number=1, index=0, **fields):
    carries = {"stopped_because", "stop_kind", *fields}
    if "rig_trials" in fields:
        carries.add("rig_trials")
    return RigRun(number, "visual_search", "done", "completed", index=index, **fields, carries=frozenset(carries))


def _file(*numbers):
    runs = [{"run_number": number} for number in numbers]
    blocks = [{"block_number": 10 + number, "run_number": number} for number in numbers]
    return runs, blocks


def test_a_run_column_is_written_only_where_some_run_carries_its_field():
    """A record from before C2b gains its runs' stop kind and sentence (long
    in the listing, missing from the file) and nothing else."""
    from wl_preproc.nwb.run_record import run_record

    runs, blocks = _file(1, 2)
    old = RigRuns(runs=(RigRun(1, "rf_map", "done", "completed", index=0,
                               carries=frozenset({"stopped_because", "stop_kind"})),), problems=())
    tables, notes = run_record(runs, blocks, old, {})
    assert runs[0]["record"] == {"stop_kind": "completed", "stopped_because": "done"}
    assert runs[1]["record"] == {"stop_kind": "", "stopped_because": ""}
    assert (runs[0]["plan"], runs[0]["end_reason"], "record" in blocks[0]) == (None, None, False)
    assert tables == {"unopened_blocks": [], "shortfalls": [], "condition_counts": []} and notes == []


def test_a_planned_runs_columns_and_its_blocks_ends():
    """Each block end joins the measured block whose number it names; a trial
    index becomes the trial's trial_id through the run's own lines."""
    from wl_preproc.nwb.run_record import run_record

    runs, blocks = _file(1)
    rig = _run(plan="training", end_reason="plan_done", rig_trials=2, presentations=3, task_hash="sha256:ab",
               seed=7, plan_form={"class": "Plan"},
               block_ends=(BlockEnd("easy", "rule", None, 2, 11, 0, 1, varied={"hold": []}),
                           BlockEnd("hard", "next", "member", 2, None, 1, None)))
    place = {(0, 0): 101, (0, 1): 102, (0, 2): 103}
    tables, notes = run_record(runs, blocks, RigRuns(runs=(rig,), problems=()), place)
    record = runs[0]["record"]
    assert (record["plan"], record["end_reason"], record["rig_trials"], record["rig_presentations"]) == (
        "training", "plan_done", 2, 3)
    assert (record["task_hash"], record["seed"], record["plan_form"]) == ("sha256:ab", "7", '{"class": "Plan"}')
    assert (runs[0]["plan"], runs[0]["end_reason"], blocks[0]["ended"]) == ("training", "plan_done", "rule")
    assert blocks[0]["record"] == {"ended": "rule", "ended_by": "", "after_trial": 103, "position": 0,
                                   "passed_at": 102, "varied": '{"hold": []}', "planned": "", "counts": ""}
    assert tables["unopened_blocks"] == [{"run_number": 1, "block": "hard", "ended": "next", "ended_by": "member",
                                          "after_trial": 103, "position": 1}]
    assert notes == []


def test_what_each_run_owed_is_two_tables():
    from wl_preproc.nwb.run_record import run_record

    runs, blocks = _file(1)
    rig = _run(shortfalls=(Shortfall("easy", "c1", 2, "cap", True, 0, None),),
               conditions=(ConditionCount("easy", "c1", 20, 23, 15, 5, 12, 0, 3, 2, None, 3, 0),))
    tables, _notes = run_record(runs, blocks, RigRuns(runs=(rig,), problems=()), {(0, 0): 101})
    assert tables["shortfalls"] == [{"run_number": 1, "block": "easy", "condition": "c1", "trials": 2,
                                     "because": "cap", "possible_avoidance": 1, "after_trial": 101, "by_kind": ""}]
    (count,) = tables["condition_counts"]
    assert (count["trials"], count["presentations"], count["right_first"], count["possible_avoidance"]) == (
        20, 23, 12, -1)


def test_a_disagreement_with_the_stream_is_a_note_never_a_refusal():
    """A block end naming a block the stream never opened in its run; a
    measured block no end names; a presentation count that is not the run's
    lines; a trial index no line of the run names."""
    from wl_preproc.nwb.run_record import run_record

    runs, blocks = _file(1)
    blocks.append({"block_number": 12, "run_number": 1})
    rig = _run(presentations=5, block_ends=(BlockEnd("easy", "rule", None, 9, 11, 0, None),
                                            BlockEnd("hard", "time", None, None, 99, 1, None)))
    tables, notes = run_record(runs, blocks, RigRuns(runs=(rig,), problems=()), {(0, 0): 101, (0, 1): 102})
    assert blocks[0]["record"]["after_trial"] == -1 and blocks[1]["record"]["ended"] == ""
    assert notes == [
        "run 1: the rig's record ends block 99, which the recording never opened in it, so that end is on no block",
        "run 1: block 12 has no end in the rig's record",
        "run 1: the rig's end row counts 5 presentation(s) and its record holds 2 trial line(s)",
        "1 trial index(es) in the rig's run rows name no trial line of their run, so they are -1",
    ]
