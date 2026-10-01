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
