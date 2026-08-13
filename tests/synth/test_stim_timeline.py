# tests/synth/test_stim_timeline.py
import pytest

from wl_preproc.contracts.events import TaskTypeCode
from wl_preproc.synth.recipe import STIM_RECIPE, BlockSpec
from wl_preproc.synth.timeline import build_timeline


def test_stim_events_are_planted_per_trial():
    truth = build_timeline(STIM_RECIPE)
    assert len(truth.stim_events) == 4 * 2


def test_no_stim_when_the_block_asks_for_none():
    recipe = STIM_RECIPE.model_copy(
        update={
            "blocks": (
                BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=4, trial_duration_s=3.0),
            )
        }
    )
    assert build_timeline(recipe).stim_events == ()


def test_every_stim_falls_inside_its_trial():
    truth = build_timeline(STIM_RECIPE)
    windows = [(t.start_s, t.end_s) for t in truth.trials]
    for event in truth.stim_events:
        assert any(
            start <= event.onset_s and event.onset_s + event.duration_s <= end
            for start, end in windows
        )


def test_stim_events_are_ordered_and_do_not_overlap():
    truth = build_timeline(STIM_RECIPE)
    events = sorted(truth.stim_events, key=lambda e: e.onset_s)
    for earlier, later in zip(events, events[1:]):
        assert earlier.onset_s + earlier.duration_s <= later.onset_s


def test_stim_channels_are_valid():
    truth = build_timeline(STIM_RECIPE)
    assert all(0 <= e.channel < STIM_RECIPE.n_ap_channels for e in truth.stim_events)


def test_magnitudes_fit_the_eight_bit_field():
    truth = build_timeline(STIM_RECIPE)
    assert all(0 < e.magnitude <= 255 for e in truth.stim_events)


def test_planting_is_deterministic():
    assert build_timeline(STIM_RECIPE) == build_timeline(STIM_RECIPE)
