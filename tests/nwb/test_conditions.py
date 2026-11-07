"""What actually ran, by condition and stimulus settings (design spec
`2026-09-29-nwb-publishing-design.md` sections 2.1 and 2.2)."""

from __future__ import annotations

import math


def _trial(trial_id, start_s, outcome="correct"):
    return {"trial_id": trial_id, "start_s": start_s, "stop_s": start_s + 1.0, "outcome": outcome}


def _rig(index, condition, **params):
    from wl_preproc.events.rigtrials import RigTrial

    return RigTrial(number=index, index=index, outcome="correct", block="main", condition=condition, params=params)


def test_trials_join_the_rig_record_by_trial_number_only():
    """A trial number the record repeats, or never names, joins nothing, and
    the notes say so: no guessing."""
    from wl_preproc.events.rigtrials import RigRecord
    from wl_preproc.nwb.conditions import join

    trials = [_trial(1, 0.0), _trial(2, 1.0), _trial(3, 2.0)]
    record = RigRecord(trials=(_rig(1, "a"), _rig(2, "b"), _rig(2, "b"), _rig(9, "z")),
                       problems=("line 5: not JSON",))
    matched, notes = join(trials, record)
    assert list(matched) == [1]
    assert notes == ["line 5: not JSON", "1 trial number(s) appear more than once in the rig record",
                     "2 trial(s) have no single line in the rig record"]
    assert join(trials, None) == ({}, ["no rig trial record (xcon/trials.jsonl)"])


def test_a_trials_code_is_the_one_condition_event_inside_it():
    from wl_preproc.nwb.conditions import stream_codes

    trials = [_trial(1, 0.0), _trial(2, 1.0), _trial(3, 2.0)]
    events = [{"time_s": 0.5, "event_type": "CONDITION", "condition": "7"},
              {"time_s": 1.2, "event_type": "CONDITION", "condition": "3"},
              {"time_s": 1.4, "event_type": "CONDITION", "condition": "4"},
              {"time_s": 2.5, "event_type": "TRIAL_START", "condition": None}]
    assert stream_codes(trials, events) == {1: 7}


def test_a_blocks_conditions_are_what_ran_by_settings():
    """Constant settings are the condition's; a number that varied within it
    is a range, anything else its distinct values; counts are by outcome."""
    from wl_preproc.nwb.conditions import block_conditions

    trials = [_trial(1, 0.0), _trial(2, 1.0, outcome="error"), _trial(3, 2.0), _trial(4, 3.0)]
    matched = {1: _rig(1, "c50", contrast=0.5, hold=0.3, xy=[0, 5]),
               2: _rig(2, "c50", contrast=0.5, hold=0.35, xy=[0, 5]),
               3: _rig(3, "c25", contrast=0.25, side="left"),
               4: _rig(4, "c25", contrast=0.25, side="right")}
    assert block_conditions(trials, matched, {1: 7, 2: 7}) == [
        {"name": "c25", "code": None, "settings": {"contrast": 0.25},
         "varying": {"side": {"values": ["left", "right"], "n_distinct": 2}},
         "trials": {"total": 2, "by_outcome": {"correct": 2}}},
        {"name": "c50", "code": 7, "settings": {"contrast": 0.5, "xy": [0, 5]},
         "varying": {"hold": {"min": 0.3, "max": 0.35}},
         "trials": {"total": 2, "by_outcome": {"correct": 1, "error": 1}}},
    ]


def test_the_join_is_on_the_lines_number_not_its_index():
    """Since XC-155 a line's index restarts in each run; only its number is
    the stream's TRIAL_NUMBER (design spec `2026-10-01-runs-and-trials-design.md`
    section 3.1)."""
    from wl_preproc.events.rigtrials import RigRecord, RigTrial
    from wl_preproc.nwb.conditions import join

    line = RigTrial(number=7, index=0, outcome="correct", block="main", condition="a", params={})
    matched, notes = join([_trial(7, 0.0), _trial(0, 1.0)], RigRecord(trials=(line,), problems=()))
    assert matched == {7: line}
    assert notes == ["1 trial(s) have no single line in the rig record"]


def test_without_the_rig_record_a_condition_is_its_stream_number():
    from wl_preproc.nwb.conditions import block_conditions

    trials = [_trial(1, 0.0), _trial(2, 1.0), _trial(3, 2.0)]
    assert block_conditions(trials, {}, {1: 7, 2: 7}) == [
        {"name": None, "code": 7, "settings": None, "varying": None,
         "trials": {"total": 2, "by_outcome": {"correct": 2}}},
    ]


def test_the_trials_table_gets_the_condition_and_each_varying_setting():
    from wl_preproc.nwb.conditions import trial_columns

    trials = [_trial(1, 0.0), _trial(2, 1.0), _trial(3, 2.0), _trial(4, 3.0)]
    matched = {1: _rig(1, "c50", contrast=0.5, hold=0.3, xy=[0, 5], fixed=1),
               2: _rig(2, "c25", contrast=0.25, hold=0.3, xy=[0, 6], fixed=1),
               3: _rig(3, "c50", contrast=0.5, xy=[0, 5], fixed=1)}
    conditions, settings = trial_columns(trials, matched, {4: 9})
    assert conditions == ["c50", "c25", "c50", "9"]
    assert sorted(settings) == ["contrast", "xy"]
    assert settings["contrast"][:3] == [0.5, 0.25, 0.5] and math.isnan(settings["contrast"][3])
    assert settings["xy"] == ["[0, 5]", "[0, 6]", "[0, 5]", ""]


def test_a_setting_whose_type_differs_between_trials_is_text():
    """Review Focus 3 (the plan): the rig's parameters are an untyped dict,
    so one setting can be a number on one trial and text on the next. It is
    written as JSON text and summarised by its distinct values, never a
    crash and never a numeric column with holes."""
    from wl_preproc.nwb.conditions import block_conditions, trial_columns

    trials = [_trial(1, 0.0), _trial(2, 1.0)]
    matched = {1: _rig(1, "c", target=5.0), 2: _rig(2, "c", target="left")}
    _conditions, settings = trial_columns(trials, matched, {})
    assert settings["target"] == ["5.0", '"left"']
    (entry,) = block_conditions(trials, matched, {})
    assert entry["varying"] == {"target": {"values": [5.0, "left"], "n_distinct": 2}}


def _c3(index, condition, drawn=None, live=(), eased=(), **params):
    """A line as wl-xcon's engine build C3 writes it: each drawn setting's
    source, the settings a person held, and those that kept it out of moving
    up."""
    from wl_preproc.events.rigtrials import RigTrial

    return RigTrial(number=index, index=index, outcome="correct", block="main", condition=condition, params=params,
                    drawn=drawn or {}, live=tuple(live), eased=tuple(eased),
                    carries=frozenset({"drawn", "live", "eased"}))


def test_a_conditions_own_values_are_kept_apart_from_drawn_and_person_held_ones():
    """Design spec `2026-11-07-rig-record-fields-design.md` section 6, the
    requester's decision of 2026-11-07: a setting drawn on a trial, or held
    there by a person, is not the condition's own on that trial. A person's
    value on every trial no longer reads as the condition's constant."""
    from wl_preproc.nwb.conditions import block_conditions

    trials = [_trial(number, float(number)) for number in (1, 2, 3, 4)]
    matched = {1: _c3(1, "c", contrast=0.5, hold=0.3, size=2.0, drawn={"hold": "task"}, live=["size"]),
               2: _c3(2, "c", contrast=0.5, hold=0.6, size=2.0, drawn={"hold": 4}, live=["size"]),
               3: _c3(3, "c", contrast=0.25, hold=0.4, size=2.0, drawn={"hold": "task"}, live=["size", "contrast"],
                      eased=["contrast"]),
               4: _c3(4, "c", contrast=0.5, hold=0.5, size=2.0, drawn={"hold": "task"}, live=["size"])}
    (entry,) = block_conditions(trials, matched, {})
    assert entry["settings"] == {"contrast": 0.5} and entry["varying"] == {}
    assert entry["drawn"] == {"hold": {"sources": [4, "task"], "min": 0.3, "max": 0.6}}
    assert entry["live"] == {"contrast": {"trials": 1, "min": 0.25, "max": 0.25},
                             "size": {"trials": 4, "min": 2.0, "max": 2.0}}
    assert entry["trials"] == {"total": 4, "by_outcome": {"correct": 4}, "eased": 1}


def test_a_direction_is_summarised_as_the_smallest_arc_holding_it():
    """A -45 to 45 degree draw, written 315 to 45, is that arc, never 0 to
    360; a value given as -90 is 270."""
    from wl_preproc.nwb.conditions import block_conditions

    trials = [_trial(number, float(number)) for number in (1, 2, 3)]
    matched = {1: _c3(1, "c", target_dir=330.0, drawn={"target_dir": "block"}),
               2: _c3(2, "c", target_dir=40.0, drawn={"target_dir": "block"}),
               3: _c3(3, "c", target_dir=10.0, drawn={"target_dir": "block"})}
    (entry,) = block_conditions(trials, matched, {}, frozenset({"target_dir"}))
    assert entry["drawn"]["target_dir"] == {"sources": ["block"], "from": 330.0, "to": 40.0}
    held = {1: _c3(1, "c", dir=-90.0), 2: _c3(2, "c", dir=80.0)}
    (entry,) = block_conditions(trials[:2], held, {}, frozenset({"dir"}))
    assert entry["varying"]["dir"] == {"from": 270.0, "to": 80.0}


def test_the_arc_leaves_out_the_widest_gap():
    """Section 6's ruling: the circle less its widest gap between neighbours,
    the first from 0 degrees where gaps tie; one value is its own arc."""
    from wl_preproc.nwb.conditions import arc

    assert arc([10.0]) == {"from": 10.0, "to": 10.0}
    assert arc([350.0, 20.0, 5.0]) == {"from": 350.0, "to": 20.0}
    assert arc([0.0, 90.0, 180.0, 270.0]) == {"from": 90.0, "to": 0.0}
    assert arc([100.0, 200.0]) == {"from": 100.0, "to": 200.0}
