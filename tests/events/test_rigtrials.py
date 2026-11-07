"""The rig's own trial record, `xcon/trials.jsonl` (design spec
`2026-09-29-nwb-publishing-design.md` section 2.1)."""

from __future__ import annotations

import json


def _record(tmp_path, lines):
    (tmp_path / "xcon").mkdir()
    (tmp_path / "xcon" / "trials.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


def _line(index, subject="pico", condition="contrast-50", **params):
    return json.dumps({"index": index, "subject": subject, "outcome": "correct", "block": "main",
                       "condition": condition, "params": params or {"contrast": 0.5}})


def test_each_line_is_one_of_this_subjects_trials(tmp_path):
    """wl-xcon's `Recorder.trial` writes one JSON object per trial, with the
    subject on every line because two animals share one day's directory."""
    from wl_preproc.events.rigtrials import RigTrial, read_rig_trials

    session = _record(tmp_path, [_line(0), _line(1, subject="other"), _line(2, contrast=0.25)])
    record = read_rig_trials(session, "pico")
    assert record.trials == (
        RigTrial(number=0, index=0, outcome="correct", block="main", condition="contrast-50",
                 params={"contrast": 0.5}),
        RigTrial(number=2, index=2, outcome="correct", block="main", condition="contrast-50",
                 params={"contrast": 0.25}),
    )
    assert record.problems == ()


def test_a_bad_line_is_reported_by_number_and_left_out(tmp_path):
    from wl_preproc.events.rigtrials import read_rig_trials

    bad_index = json.dumps({"index": "7", "subject": "pico", "outcome": "correct", "block": "main",
                            "condition": "c", "params": {}})
    session = _record(tmp_path, [_line(0), "not json", json.dumps({"index": 1}), bad_index, _line(3)])
    record = read_rig_trials(session, "pico")
    assert [trial.index for trial in record.trials] == [0, 3]
    assert [problem.split(":")[0] for problem in record.problems] == ["line 2", "line 3", "line 4"]


def test_a_session_without_a_record_has_none(tmp_path):
    from wl_preproc.events.rigtrials import read_rig_trials

    assert read_rig_trials(tmp_path, "pico") is None


def _line155(trial_number, run, index, **fields):
    """A line as wl-xcon writes it since XC-155: its run, its index within the
    run, and its number across the session."""
    return json.dumps({**json.loads(_line(index, **fields)), "run": run, "trial_number": trial_number})


def test_a_line_since_xc155_is_keyed_by_its_trial_number(tmp_path):
    """wl-xcon XC-155: each line's `trial_number` counts from 1 across the
    session and equals the stream's TRIAL_NUMBER, while `index` restarts in
    each run. The number is the key, so the second run's first trial is 3,
    not 0 (design spec `2026-10-01-runs-and-trials-design.md` section 3.1)."""
    from wl_preproc.events.rigtrials import read_rig_trials

    rows = [_line155(1, 0, 0), _line155(2, 0, 1), _line155(3, 1, 0)]
    record = read_rig_trials(_record(tmp_path, rows), "pico")
    assert [(trial.number, trial.index) for trial in record.trials] == [(1, 0), (2, 1), (3, 0)]
    assert record.problems == ()


def test_a_line_naming_a_run_without_its_number_is_left_out(tmp_path):
    """A run-numbered line with no `trial_number` has no session-wide key,
    so it joins nothing and the record says why; the lines that carry the
    number are still read. A number that is not an integer is a problem too."""
    from wl_preproc.events.rigtrials import read_rig_trials

    rows = [json.dumps({**json.loads(_line(0)), "run": 0}), _line155(2, 0, 1),
            json.dumps({**json.loads(_line155(3, 0, 2)), "trial_number": "3"})]
    record = read_rig_trials(_record(tmp_path, rows), "pico")
    assert [trial.number for trial in record.trials] == [2]
    assert [problem.split(":")[0] for problem in record.problems] == ["line 1", "line 3"]
    assert "trial_number" in record.problems[0]


def _line_c3(trial_number, index, **fields):
    """A line as wl-xcon writes it since its engine builds C2a, C2b and C3
    (`wl_xcon/record.py::Recorder.trial`), with the fluid it commanded."""
    return json.dumps({**json.loads(_line155(trial_number, 0, index)), "counting": "counted", "shown": True,
                       "repeat_of": None, "at_once": False, "previous": "contrast-50", "drummed": False,
                       "drumming": None, "aids": [], "structure": "default", "overridden": [], "drawn": {},
                       "live": [], "eased": [], "planned": None, "fluid_ml": 0.12, **fields})


def test_a_line_since_c2a_says_what_the_trial_paid_and_where_it_sits(tmp_path):
    """Design spec `2026-11-07-rig-record-fields-design.md` section 3: C2a's
    count and sequence, C2b's structure and overrides, C3's draws, a person's
    holds, eased settings and planned place, and the fluid commanded. What it
    paid is not its answer: a wrong answer under drumming is `owed`."""
    from wl_preproc.events.rigtrials import Planned, read_rig_trials

    rows = [_line_c3(1, 0),
            _line_c3(2, 1, counting="owed", repeat_of=0, at_once=True, drummed=True, drumming="block",
                     aids=["drumming", "clear"], overridden=["contrast"], live=["contrast", "hold"],
                     eased=["contrast"], drawn={"hold": "task", "jitter": 7},
                     planned={"cycle": 0, "position": 1, "lead_in": False}),
            _line_c3(3, 2, planned={"run": 2, "switch": True, "value": {"class": "Disc", "fields": {}}})]
    first, second, third = read_rig_trials(_record(tmp_path, rows), "pico").trials
    assert (first.run, first.counting, first.shown, first.repeat_of, first.at_once, first.previous) == (
        0, "counted", True, None, False, "contrast-50")
    assert (first.structure, first.fluid_ml, first.drawn, first.planned) == ("default", 0.12, {}, None)
    assert (second.counting, second.repeat_of, second.at_once, second.drummed, second.drumming) == (
        "owed", 0, True, True, "block")
    assert (second.aids, second.overridden, second.live, second.eased) == (
        ("drumming", "clear"), ("contrast",), ("contrast", "hold"), ("contrast",))
    assert second.drawn == {"hold": "task", "jitter": 7}
    assert second.planned == Planned(cycle=0, position=1, lead_in=False)
    assert third.planned == Planned(run=2, switch=True, value={"class": "Disc", "fields": {}})
    assert "planned" in third.carries and "counting" in third.carries


def test_a_line_before_c2a_carries_none_of_them(tmp_path):
    """A record written before the builds reads exactly as before."""
    from wl_preproc.events.rigtrials import read_rig_trials

    (trial,) = read_rig_trials(_record(tmp_path, [_line155(1, 0, 0)]), "pico").trials
    assert trial.carries == frozenset()
    assert (trial.counting, trial.shown, trial.aids, trial.drawn, trial.planned, trial.fluid_ml) == (
        None, None, (), None, None, None)


def test_a_field_it_cannot_read_is_none_said_once_and_its_line_kept(tmp_path):
    """Read, never repaired (section 2): a value of the wrong type is read as
    none and reported once per field, with how many lines; the line stays."""
    from wl_preproc.events.rigtrials import read_rig_trials

    rows = [_line_c3(1, 0, shown="yes"), _line_c3(2, 1, shown=1, aids="clear"),
            _line_c3(3, 2, planned={"cycle": "0"}, drawn={"hold": None}, fluid_ml=float("nan"))]
    record = read_rig_trials(_record(tmp_path, rows), "pico")
    assert [trial.number for trial in record.trials] == [1, 2, 3]
    assert [trial.shown for trial in record.trials] == [None, None, True]
    assert (record.trials[1].aids, record.trials[2].planned, record.trials[2].drawn,
            record.trials[2].fluid_ml) == ((), None, None, None)
    assert record.problems == (
        "1 trial line: `aids` is not a list of text, so it is read as none",
        "1 trial line: `drawn` is not an object naming each drawn setting's source, so it is read as none",
        "1 trial line: `fluid_ml` is not a finite number or null, so it is read as none",
        "1 trial line: `planned` is not a planned place, so it is read as none",
        "2 trial lines: `shown` is not true, false or null, so it is read as none",
    )


_FIX = {"name": "fix", "kind": "show", "frame": 1, "off": 61, "changed": None, "drawn": True, "unresolved": None,
        "eccentricity": 0.0, "direction": None, "x": 0.0, "y": 0.0}
_TARGET = {"name": "target", "kind": "update", "frame": 97, "off": None, "changed": ["opacity"], "drawn": True,
           "unresolved": None, "eccentricity": 27.23631347517073, "direction": 45.0, "x": 20.0, "y": 20.0}


def test_each_stimulus_a_trial_showed_is_read_with_where_and_when(tmp_path):
    """wl-xcon's XC-399 (design spec `2026-11-07-rig-record-fields-design.md`
    section 4): a `Show` or `Update` per entry, its frames, its cyclopean
    position and true angles; an array's objects and target; a per-eye
    stimulus's two positions. The polar angle is read from `direction` today
    and from `polar` once wl-xcon renames it."""
    from wl_preproc.events.rigtrials import Place, read_rig_trials

    search = {**_FIX, "name": "search", "frame": 85, "off": None, "target": 1,
              "objects": [{"eccentricity": 8.0, "direction": 0.0, "x": 8.0, "y": 0.0},
                          {"eccentricity": 8.0, "polar": 180.0, "x": -8.0, "y": 0.0}]}
    eyes = {**_FIX, "name": "dot", "eccentricity": None, "direction": None, "x": None, "y": None,
            "left": {"eccentricity": 1.0, "direction": 0.0, "x": 1.0, "y": 0.0,
                     "objects": [{"eccentricity": 2.0, "direction": 0.0, "x": 2.0, "y": 0.0}]},
            "right": {"eccentricity": 1.0, "direction": 180.0, "x": -1.0, "y": 0.0}}
    rows = [_line155(1, 0, 0, stimuli=None)]
    rows[0] = json.dumps({**json.loads(rows[0]), "stimuli": [_FIX, _TARGET, search, {**_TARGET, "polar": 46.0}, eyes]})
    (trial,) = read_rig_trials(_record(tmp_path, rows), "pico").trials
    fix, target, array, renamed, dot = trial.stimuli
    assert (fix.name, fix.kind, fix.frame, fix.off, fix.changed, fix.drawn) == ("fix", "show", 1, 61, (), True)
    assert fix.at == Place(x=0.0, y=0.0, eccentricity=0.0, polar=None)
    assert (target.kind, target.off, target.changed, target.at.polar) == ("update", None, ("opacity",), 45.0)
    assert array.target == 1 and array.objects == (Place(8.0, 0.0, 8.0, 0.0), Place(-8.0, 0.0, 8.0, 180.0))
    assert renamed.at.polar == 46.0
    assert dot.at == Place() and dot.left["x"] == 1.0 and dot.right["direction"] == 180.0
    assert dot.eye_objects == (("left", (Place(2.0, 0.0, 2.0, 0.0),)),)
    assert "stimuli" in trial.carries


def test_a_stimulus_field_it_cannot_read_is_none_and_said_by_its_path(tmp_path):
    from wl_preproc.events.rigtrials import read_rig_trials

    row = json.loads(_line155(1, 0, 0))
    rows = [json.dumps({**row, "stimuli": [{**_FIX, "frame": "1", "x": "0"}]}),
            json.dumps({**row, "trial_number": 2, "index": 1, "stimuli": "none"})]
    record = read_rig_trials(_record(tmp_path, rows), "pico")
    first, second = record.trials
    assert (first.stimuli[0].frame, first.stimuli[0].at.x, first.stimuli[0].at.y) == (None, None, 0.0)
    assert second.stimuli is None
    assert record.problems == (
        "1 trial line: `stimuli` is not a list of objects or null, so it is read as none",
        "1 trial line: `stimuli[].frame` is not a whole number or null, so it is read as none",
        "1 trial line: `stimuli[].x` is not a finite number or null, so it is read as none",
    )
