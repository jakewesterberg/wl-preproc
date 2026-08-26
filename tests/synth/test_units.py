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
