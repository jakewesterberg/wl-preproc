"""The task-file reader seam."""

from __future__ import annotations

import json

import pytest

from wl_preproc.events import taskfile


def test_the_synthetic_reader_returns_trials_by_id(tmp_path):
    path = tmp_path / "task.json"
    path.write_text(
        json.dumps(
            {
                "format": "synthetic-task-file",
                "version": 1,
                "trials": [
                    {"trial_id": 1, "block_id": 1, "start_s": 0.0, "end_s": 1.0,
                     "condition": 2, "reward_ms": 120, "outcome": "correct"},
                    {"trial_id": 2, "block_id": 1, "start_s": 1.0, "end_s": 2.0,
                     "condition": 3, "reward_ms": 120, "outcome": "error"},
                ],
            }
        )
    )
    trials = taskfile.SyntheticTaskFileReader().trials(path)
    assert [t.trial_id for t in trials] == [1, 2]
    assert [t.outcome for t in trials] == ["correct", "error"]
    assert trials[0].condition == 2


def test_an_unknown_format_is_refused_rather_than_guessed(tmp_path):
    """The behavioural stack is deliberately unchosen (spec section 4.2), so a
    second format WILL arrive. Reading an unrecognised one on a best-effort
    basis would silently produce a trial list that disagrees with the codes --
    and the disagreement is supposed to be a hard failure, not a merge."""
    path = tmp_path / "task.json"
    path.write_text(json.dumps({"format": "monkeylogic-bhv2", "version": 1, "trials": []}))

    with pytest.raises(taskfile.UnsupportedTaskFile, match="monkeylogic-bhv2"):
        taskfile.SyntheticTaskFileReader().trials(path)
