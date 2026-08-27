import numpy as np
import pytest

from wl_preproc.ephys.geometry import electrode_rows
from wl_preproc.synth.units import REFRACTORY_S, place_units, spike_train


def test_units_sit_within_the_span_of_the_recorded_sites():
    """A unit outside the recorded span contributes nothing to any channel, so
    it would be ground truth nothing could recover -- worse than absent,
    because a recall metric would score it as a miss."""
    sites = electrode_rows("NP1032")[:64]
    units = place_units(sites, n_units=8, rng=np.random.default_rng(0))

    ys = [s["y_coord"] for s in sites]
    assert len(units) == 8
    assert {u.unit_id for u in units} == set(range(8))
    for unit in units:
        assert min(ys) <= unit.y_um <= max(ys)


def test_two_units_never_share_an_identity():
    sites = electrode_rows("NP1032")[:64]
    units = place_units(sites, n_units=12, rng=np.random.default_rng(1))
    assert len({u.unit_id for u in units}) == 12


def test_a_spike_train_honours_a_refractory_period():
    """A Poisson train without one produces inter-spike intervals no neuron can
    make, and ISI-violation metrics computed against it are meaningless -- the
    fixture would be scoring the pipeline against impossible data."""
    sites = electrode_rows("NP1032")[:64]
    unit = place_units(sites, n_units=1, rng=np.random.default_rng(2))[0]
    times = spike_train(unit, duration_s=30.0, rng=np.random.default_rng(3))

    assert len(times) > 10
    assert np.all(np.diff(times) >= REFRACTORY_S)


def test_a_spike_train_is_reproducible_from_its_seed():
    sites = electrode_rows("NP1032")[:64]
    unit = place_units(sites, n_units=1, rng=np.random.default_rng(4))[0]
    first = spike_train(unit, duration_s=10.0, rng=np.random.default_rng(5))
    second = spike_train(unit, duration_s=10.0, rng=np.random.default_rng(5))
    assert first == second


# --- Which frame a unit is placed in. --------------------------------------
#
# Placement used `recipe.probe_part_number` unconditionally, so an RHS-only
# recipe got units positioned against Neuropixels geometry and then rendered
# against a linear Intan array. On STIM_RECIPE that put all three units inside
# the top 16 um of an array spanning 150 um. Design spec section 11 item 7.


def test_an_rhs_only_recipe_places_units_against_the_intan_array():
    """Not the probe table: an RHS session records through a linear headstage,
    and `place_units`' own contract is that units are bounded by the sites the
    session actually records."""
    from wl_preproc.synth.recipe import STIM_RECIPE
    from wl_preproc.synth.units import linear_sites, recording_sites

    assert recording_sites(STIM_RECIPE) == linear_sites(STIM_RECIPE.n_ap_channels)


def test_a_spikeglx_recipe_still_places_units_against_the_probe():
    from wl_preproc.ephys.geometry import electrode_rows
    from wl_preproc.synth.recipe import SPATIAL_RECIPE
    from wl_preproc.synth.units import recording_sites

    expected = electrode_rows(SPATIAL_RECIPE.probe_part_number)[
        : SPATIAL_RECIPE.n_ap_channels
    ]
    assert recording_sites(SPATIAL_RECIPE) == expected


def test_units_on_both_ephys_systems_is_refused_rather_than_guessed():
    """Two probes in different brain locations record two different
    populations. One `truth.units` cannot express that, and silently picking
    one system's frame would put the other system's units somewhere arbitrary
    -- which is the fault this whole change exists to remove."""
    import pytest

    from wl_preproc.synth.recipe import DRIFT_RECIPE
    from wl_preproc.synth.units import TwoProbePopulationsUnsupported, recording_sites

    both = DRIFT_RECIPE.model_copy(update={"n_units": 3})
    assert {"spikeglx", "rhs"} <= set(both.systems)
    with pytest.raises(TwoProbePopulationsUnsupported):
        recording_sites(both)


def test_planted_units_span_the_intan_array_rather_than_its_top_edge():
    """The live symptom. Before this, STIM_RECIPE's units were drawn from
    NP1000's first four sites (y spanning 0-20 um) and rendered on an array
    spanning 0-150 um, so every one landed in the top 16 um."""
    from wl_preproc.synth.recipe import STIM_RECIPE
    from wl_preproc.synth.timeline import build_timeline
    from wl_preproc.synth.units import linear_sites

    truth = build_timeline(STIM_RECIPE)
    sites = linear_sites(STIM_RECIPE.n_ap_channels)
    span = max(s["y_coord"] for s in sites) - min(s["y_coord"] for s in sites)

    ys = [u.y_um for u in truth.units]
    assert truth.units
    assert min(ys) >= 0.0 and max(ys) <= span
    # Not all crammed against one end of an array they are supposed to cover.
    assert max(ys) - min(ys) > span / 4
