import pytest
from pydantic import ValidationError

from wl_preproc.contracts.events import TaskTypeCode
from wl_preproc.synth.recipe import BENCHMARK_RECIPE, CI_RECIPE, BlockSpec, MontageSpec, SessionRecipe


def test_duration_is_derived_from_blocks():
    recipe = SessionRecipe(
        session_id="2027-03-14_01",
        subject="pico",
        rig="rig-a",
        systems=("syncbox", "spikeglx"),
        blocks=(
            BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=2, trial_duration_s=3.0),
            BlockSpec(task_type=TaskTypeCode.RESTING_DARK, n_trials=1, trial_duration_s=4.0),
        ),
        montages=(MontageSpec(start_s=0.0, end_s=10.0),),
        n_ap_channels=4,
        ap_sample_rate_hz=30_000.0,
        seed=1,
    )
    assert recipe.duration_s == pytest.approx(10.0)


def test_syncbox_must_be_present():
    """The sync box is at every session — a recipe without it is not a session."""
    with pytest.raises(ValidationError):
        SessionRecipe(
            session_id="2027-03-14_01", subject="pico", rig="rig-a",
            systems=("spikeglx",),
            blocks=(BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=1, trial_duration_s=3.0),),
            montages=(MontageSpec(start_s=0.0, end_s=3.0),),
            n_ap_channels=4, ap_sample_rate_hz=30_000.0, seed=1,
        )


def test_montages_must_cover_the_session():
    """A gap in montage coverage would leave blocks belonging to no montage."""
    with pytest.raises(ValidationError) as exc:
        SessionRecipe(
            session_id="2027-03-14_01", subject="pico", rig="rig-a",
            systems=("syncbox",),
            blocks=(BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=2, trial_duration_s=3.0),),
            montages=(MontageSpec(start_s=0.0, end_s=3.0),),
            n_ap_channels=4, ap_sample_rate_hz=30_000.0, seed=1,
        )
    assert "cover" in str(exc.value)


def test_a_zero_trial_block_is_refused():
    """A zero-trial block has zero nominal duration, but `timeline.py` still
    emits its `BLOCK_START` payload and its `BLOCK_END` -- so its MEASURED
    boundary lands several code-word slots away from `(0.0, 0.0)`, and the
    block after it starts displaced too. Traced: nominal `(0.0, 0.0)` measures
    `(0.001, 0.005)`, with the next block's start pushed to `0.006` -- six
    slots, three times `BLOCK_AGREEMENT_TOLERANCE_FLOOR_S`, i.e. a tier-D
    quarantine for a session where nothing is actually wrong.

    `events/agreement.py`'s `_BLOCK_START_MAX_SLOTS` derives its one-slot
    bound assuming every block has at least one trial. Its comment used to
    claim the refusal already happened downstream in `classify_coverage`,
    which was never called on the measured block at all; the refusal lives
    here instead, at the only point a zero-trial block can be described.
    """
    with pytest.raises(ValidationError) as exc:
        BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=0, trial_duration_s=3.0)
    assert "greater than or equal to 1" in str(exc.value)


def test_ci_recipe_is_small_enough_for_ci():
    """A CI fixture that takes minutes or gigabytes will be deleted by whoever
    inherits it. Keep it tiny by construction."""
    assert CI_RECIPE.duration_s <= 30.0
    assert CI_RECIPE.n_ap_channels <= 8


def test_benchmark_recipe_is_realistic():
    """The P6000 benchmark needs a realistic probe, not a toy."""
    assert BENCHMARK_RECIPE.n_ap_channels == 384
    assert BENCHMARK_RECIPE.ap_sample_rate_hz == 30_000.0


def test_recipes_are_frozen():
    with pytest.raises(ValidationError):
        CI_RECIPE.seed = 99


def test_channels_default_to_empty_and_the_recipe_names_nothing():
    """A recipe is device-neutral, so it must not carry one vendor's naming.

    This object used to resolve unset channels to Intan's Port A convention, and
    the consequence was live: CI_RECIPE is a SpikeGLX/bcam recipe with no `rhs`
    system, and it answered A-000..A-003 anyway. The default now lives in
    write_rhs_header, which is the code that owns that convention; write_spikeglx
    names AP0..APn and never consults this field.
    """
    from wl_preproc.synth.recipe import CI_RECIPE

    assert CI_RECIPE.channels == ()
    assert not hasattr(CI_RECIPE, "resolved_channels")


def test_explicit_channels_are_stored_unchanged():
    """`channels` is the override an emitter reads, so it must survive verbatim —
    including a naming convention that is nobody's default."""
    from wl_preproc.synth.recipe import CI_RECIPE, ChannelSpec

    named = tuple(
        ChannelSpec(name=f"B-{i:03d}", impedance_ohms=2.5e6) for i in range(4)
    )
    recipe = CI_RECIPE.model_copy(update={"channels": named})
    assert recipe.channels == named
    assert [c.impedance_ohms for c in recipe.channels] == [2.5e6] * 4


def test_channel_count_must_match_the_channel_number():
    """A recipe whose declared channels disagree with n_ap_channels would emit a
    header describing a different array than amplifier.dat actually contains."""
    import pytest
    from pydantic import ValidationError

    from wl_preproc.synth.recipe import (
        BlockSpec,
        ChannelSpec,
        MontageSpec,
        SessionRecipe,
    )
    from wl_preproc.contracts.events import TaskTypeCode

    with pytest.raises(ValidationError):
        SessionRecipe(
            session_id="2027-03-14_09",
            subject="pico",
            rig="rig-a",
            systems=("syncbox", "rhs"),
            blocks=(
                BlockSpec(
                    task_type=TaskTypeCode.RF_MAP, n_trials=1, trial_duration_s=3.0
                ),
            ),
            montages=(MontageSpec(start_s=0.0, end_s=3.0),),
            n_ap_channels=4,
            ap_sample_rate_hz=30_000.0,
            seed=1,
            channels=(ChannelSpec(name="A-000"),),  # 1 channel, 4 declared
        )


def test_impedance_must_be_positive():
    import pytest
    from pydantic import ValidationError

    from wl_preproc.synth.recipe import ChannelSpec

    with pytest.raises(ValidationError):
        ChannelSpec(name="A-000", impedance_ohms=-1.0)
