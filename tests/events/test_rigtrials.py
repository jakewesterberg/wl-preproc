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
        RigTrial(index=0, outcome="correct", block="main", condition="contrast-50", params={"contrast": 0.5}),
        RigTrial(index=2, outcome="correct", block="main", condition="contrast-50", params={"contrast": 0.25}),
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


def test_a_record_that_numbers_trials_within_each_run_is_not_read(tmp_path):
    """Since wl-xcon's slice b3a-1 a session holds several runs: each line names
    its run, and each run counts its trials from 0. The stream's TRIAL_NUMBER
    is numbered across the session (wl-xcon XC-155), so a per-run index is not
    its join key -- index 1 here is unique, yet it is the second run's second
    trial, not the session's second. No line is read, and the problem says why."""
    from wl_preproc.events.rigtrials import read_rig_trials

    rows = [json.dumps({**json.loads(_line(index)), "run": run}) for run, index in ((0, 0), (1, 0), (1, 1))]
    record = read_rig_trials(_record(tmp_path, rows), "pico")
    assert record.trials == ()
    assert len(record.problems) == 1 and "within each run" in record.problems[0]
