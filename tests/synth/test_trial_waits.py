"""An animal-paced wait before each trial (part 3b-2, decision 7)."""

from __future__ import annotations

import pytest

from wl_preproc.synth.recipe import CI_RECIPE, RECIPES, MontageSpec, animal_paced_waits
from wl_preproc.synth.timeline import build_timeline


def test_without_waits_trials_run_back_to_back():
    for name, recipe in RECIPES.items():
        if name == "ni-intan":
            continue
        assert recipe.trial_waits_s == ()
        trials = build_timeline(recipe).trials
        assert all(later.start_s == earlier.end_s for earlier, later in zip(trials, trials[1:]))


def test_each_trial_starts_after_its_wait():
    waits = (0.5, 1.25, 0.2, 2.0)
    recipe = CI_RECIPE.model_copy(update={"trial_waits_s": waits,
                                          "montages": (MontageSpec(start_s=0.0, end_s=15.0 + sum(waits)),)})
    truth = build_timeline(recipe)
    plain = build_timeline(CI_RECIPE)
    for position, (trial, before) in enumerate(zip(truth.trials, plain.trials, strict=True)):
        assert trial.start_s == pytest.approx(before.start_s + sum(waits[: position + 1]))
        assert trial.end_s - trial.start_s == pytest.approx(before.end_s - before.start_s)
    assert recipe.duration_s == pytest.approx(15.0 + sum(waits))


def test_waits_must_name_every_trial():
    with pytest.raises(ValueError, match="trial_waits_s names 2 trials, and the session has 4"):
        CI_RECIPE.model_copy(update={"trial_waits_s": (1.0, 1.0)})._coherent()


def test_a_wait_cannot_be_negative():
    with pytest.raises(ValueError, match="trial_waits_s holds a negative wait"):
        CI_RECIPE.model_copy(update={"trial_waits_s": (1.0, -0.1, 1.0, 1.0)})._coherent()


def test_animal_paced_waits_are_irregular_and_repeatable():
    waits = animal_paced_waits(40, 1.0, 7)
    assert waits == animal_paced_waits(40, 1.0, 7)
    assert min(waits) >= 0.2 and len(set(waits)) > 30


def test_the_ni_intan_recipe_records_both_without_the_sync_box():
    recipe = RECIPES["ni-intan"]
    assert recipe.systems == ("spikeglx", "rhs") and len(recipe.trial_waits_s) == 4
