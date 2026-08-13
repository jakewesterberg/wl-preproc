# tests/synth/test_stim_timeline.py
import pytest
from pydantic import ValidationError

from wl_preproc.contracts.events import TaskTypeCode
from wl_preproc.synth.recipe import STIM_RECIPE, BlockSpec, MontageSpec, SessionRecipe
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


def test_trial_too_short_for_stim_guard_bands_is_rejected():
    """trial_duration_s=0.05 <= 2 * STIM_GUARD_S leaves no room for even one
    guard band, let alone two — span goes negative and a pulse would land
    before its own trial starts."""
    with pytest.raises(ValidationError):
        SessionRecipe(
            session_id="2027-03-14_04",
            subject="pico",
            rig="rig-a",
            systems=("syncbox", "rhs"),
            blocks=(
                BlockSpec(
                    task_type=TaskTypeCode.RF_MAP,
                    n_trials=1,
                    trial_duration_s=0.05,
                    stim_per_trial=1,
                ),
            ),
            montages=(MontageSpec(start_s=0.0, end_s=0.05),),
            n_ap_channels=4,
            ap_sample_rate_hz=30_000.0,
            seed=7,
        )


def test_too_many_pulses_per_trial_is_rejected():
    """10,000 pulses spread across a 2.9s span after guard bands packs them
    ~0.00029s apart, tighter than STIM_PULSE_DURATION_S=0.0005s — adjacent
    pulses would overlap."""
    with pytest.raises(ValidationError):
        SessionRecipe(
            session_id="2027-03-14_05",
            subject="pico",
            rig="rig-a",
            systems=("syncbox", "rhs"),
            blocks=(
                BlockSpec(
                    task_type=TaskTypeCode.RF_MAP,
                    n_trials=1,
                    trial_duration_s=3.0,
                    stim_per_trial=10_000,
                ),
            ),
            montages=(MontageSpec(start_s=0.0, end_s=3.0),),
            n_ap_channels=4,
            ap_sample_rate_hz=30_000.0,
            seed=7,
        )


def test_negative_stim_per_trial_is_rejected():
    with pytest.raises(ValidationError):
        SessionRecipe(
            session_id="2027-03-14_06",
            subject="pico",
            rig="rig-a",
            systems=("syncbox", "rhs"),
            blocks=(
                BlockSpec(
                    task_type=TaskTypeCode.RF_MAP,
                    n_trials=1,
                    trial_duration_s=3.0,
                    stim_per_trial=-1,
                ),
            ),
            montages=(MontageSpec(start_s=0.0, end_s=3.0),),
            n_ap_channels=4,
            ap_sample_rate_hz=30_000.0,
            seed=7,
        )


def test_stim_recipe_still_satisfies_its_own_geometry_constraints():
    """A regression guard for STIM_RECIPE itself: re-validates it from raw
    data rather than relying on the module having imported successfully, so a
    future edit to STIM_GUARD_S, STIM_PULSE_DURATION_S, or STIM_RECIPE's own
    trial_duration_s / stim_per_trial that breaks the invariant fails here by
    name instead of as an opaque collection-time ImportError."""
    assert SessionRecipe.model_validate(STIM_RECIPE.model_dump()) == STIM_RECIPE
