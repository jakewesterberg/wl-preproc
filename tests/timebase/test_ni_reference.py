"""Part 3b-3, amendments 54-55: under the NI's clock, the NI's own barcodes are
the session's barcode reference, and their [first, last] is the covered span."""

from __future__ import annotations

import pytest


def _ni_clock(tmp_path, **update):
    from wl_preproc.synth.recipe import NI_CLOCK_RECIPE
    from wl_preproc.synth.session import generate_session
    from wl_preproc.timebase.clock import Clock

    recipe = NI_CLOCK_RECIPE.model_copy(update=update)
    truth = generate_session(tmp_path, recipe)
    return recipe, truth, Clock(source="nidq", session_dir=tmp_path / recipe.session_id)


@pytest.fixture(scope="module")
def partial(tmp_path_factory):
    return _ni_clock(tmp_path_factory.mktemp("partial"),
                     systems=("syncbox", "spikeglx", "ohdpi", "bcam"), syncbox_stop_s=9.0)


def test_the_ni_reference_is_the_nis_own_barcodes(partial):
    from wl_preproc.synth.spikeglx import NIDQ_SAMPLE_RATE_HZ, SPIKEGLX_PRE_ROLL_S
    from wl_preproc.synth.timeline import apply_drift

    recipe, truth, clock = partial
    drift = dict(recipe.system_drift_ppm).get("spikeglx", recipe.drift_ppm)
    reference = clock.barcode_reference()
    assert reference
    assert set(reference) <= {value for value, _t in truth.barcodes}
    for value, t in truth.barcodes:
        if value in reference:
            assert reference[value] == pytest.approx(apply_drift(t, drift) + SPIKEGLX_PRE_ROLL_S,
                                                     abs=1 / NIDQ_SAMPLE_RATE_HZ)


def test_the_covered_span_runs_from_the_first_to_the_last(partial):
    _recipe, _truth, clock = partial
    reference = clock.barcode_reference()
    assert clock.covered_span() == (min(reference.values()), max(reference.values()))


def test_with_no_barcode_on_the_ni_the_reference_is_empty(tmp_path):
    _recipe, _truth, clock = _ni_clock(tmp_path)
    assert clock.barcode_reference() == {}
    assert clock.covered_span() is None


def test_under_the_sync_box_the_span_is_none(tmp_path):
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session
    from wl_preproc.timebase.clock import Clock

    recipe = RECIPES["ci"]
    generate_session(tmp_path, recipe)
    assert Clock(source="syncbox", session_dir=tmp_path / recipe.session_id).covered_span() is None
