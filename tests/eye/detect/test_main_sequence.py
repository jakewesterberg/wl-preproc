"""The main sequence and vigor, as pure functions (design spec
`2026-10-07-main-sequence-design.md` sections 4 and 5): which saccades a fit
takes, the session's fit and its guard, a group's gain, and a session's vigor
against earlier sessions."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from wl_preproc.eye.detect.main_sequence import (
    DEFAULT_MAIN_SEQUENCE_PARAMS,
    Curve,
    MainSequenceParams,
    SessionFit,
    fit_session,
    gain,
    selected,
    vigor,
)

PARAMS = DEFAULT_MAIN_SEQUENCE_PARAMS
PLANTED = Curve(v_max_deg_s=400.0, saturation_deg=5.0)


def _planted(n, low, high, *, seed, noise=0.05, curve=PLANTED, scale=1.0):
    """`n` saccades with sizes uniform in `[low, high)` and peak speeds on
    `curve` times `scale`, each off it by `noise` (a fraction, Gaussian)."""
    rng = np.random.default_rng(seed)
    amplitude = rng.uniform(low, high, n)
    return amplitude, scale * curve(amplitude) * (1.0 + rng.normal(0.0, noise, n))


def _history(*curves_and_ranges):
    return [SessionFit(curve=curve, n_saccades=200, amplitude_min_deg=low, amplitude_max_deg=high,
                       v_max_se_deg_s=1.0, saturation_se_deg=0.1, r_squared=0.9, reason="")
            for curve, (low, high) in curves_and_ranges]


def test_the_defaults_are_the_specs():
    assert dataclasses.asdict(PARAMS) == {
        "fit_form": "saturating_exponential", "min_amplitude_deg": 1.0, "max_duration_ms": 150.0,
        "min_session_saccades": 100, "min_amplitude_ratio": 3.0, "max_relative_se": 0.5,
        "min_group_saccades": 30,
    }


def test_an_unknown_fit_form_is_refused():
    with pytest.raises(ValueError, match="power_law"):
        dataclasses.replace(PARAMS, fit_form="power_law")


def test_selection_takes_saccadic_runs_from_the_floor_up_to_the_ceiling():
    """By size, not label: a 1 deg `microsaccade` is taken (section 4.1)."""
    labels = np.array(["saccade", "microsaccade", "fixation", "saccade", "saccade", "saccade", "pso"])
    amplitude = np.array([1.0, 1.5, 3.0, 0.99, np.nan, 2.0, 2.0])
    n_samples = np.array([10, 10, 10, 10, 10, 75, 10])
    assert selected(labels, amplitude, n_samples, 500.0, PARAMS).tolist() == [
        True, True, False, False, False, True, False]
    assert not selected(labels, amplitude, n_samples + 1, 500.0, PARAMS)[5]  # 152 ms


def test_a_session_fit_recovers_a_planted_curve():
    amplitude, peak = _planted(400, 1.0, 12.0, seed=1)
    fit = fit_session(amplitude, peak, PARAMS)
    assert fit.reason == ""
    assert fit.curve.v_max_deg_s == pytest.approx(400.0, rel=0.05)
    assert fit.curve.saturation_deg == pytest.approx(5.0, rel=0.10)
    assert fit.n_saccades == 400
    assert (fit.amplitude_min_deg, fit.amplitude_max_deg) == (amplitude.min(), amplitude.max())
    assert 0.0 < fit.v_max_se_deg_s < 0.05 * 400.0
    assert 0.0 < fit.saturation_se_deg < 0.10 * 5.0
    assert fit.r_squared > 0.8


def test_too_few_saccades_are_refused_naming_both_counts():
    amplitude, peak = _planted(99, 1.0, 12.0, seed=2)
    fit = fit_session(amplitude, peak, PARAMS)
    assert fit.curve is None
    assert fit.reason == "99 saccades; a session fit needs at least 100"
    assert fit.n_saccades == 99
    # Counted before the range is judged (section 4.2's order).
    amplitude, peak = _planted(50, 6.0, 9.0, seed=2)
    assert fit_session(amplitude, peak, PARAMS).reason == "50 saccades; a session fit needs at least 100"


def test_saccades_spanning_6_to_9_deg_are_refused_naming_their_range():
    """The saccade-detection spec's own section 10 fixture: fitted, they give
    a plausible V_max that means nothing (this spec's section 1.4)."""
    amplitude, peak = _planted(300, 6.0, 9.0, seed=3)
    fit = fit_session(amplitude, peak, PARAMS)
    assert fit.curve is None
    low, high = np.percentile(amplitude, [10, 90])
    assert fit.reason == (f"the middle 80% of their sizes spans {low:.2f}-{high:.2f} deg, a factor of "
                          f"{high / low:.2f}; a session fit needs 3")
    assert (fit.amplitude_min_deg, fit.amplitude_max_deg) == (amplitude.min(), amplitude.max())


def test_one_stray_large_saccade_does_not_pass_the_range_check():
    amplitude, peak = _planted(150, 1.0, 2.0, seed=4)
    amplitude, peak = np.append(amplitude, 20.0), np.append(peak, PLANTED(20.0))
    fit = fit_session(amplitude, peak, PARAMS)
    assert fit.curve is None
    assert fit.reason.startswith("the middle 80% of their sizes spans")


def test_a_fit_that_does_not_determine_its_parameters_is_refused():
    """Peak speed in proportion to size, with no saturation in range: V_max
    and C grow together without bound, each known to about 90% (amendment
    1)."""
    rng = np.random.default_rng(9)
    amplitude = np.linspace(1.0, 12.0, 200)
    fit = fit_session(amplitude, 30.0 * amplitude * (1.0 + rng.normal(0.0, 0.05, 200)), PARAMS)
    assert fit.curve is None
    assert fit.reason.startswith("the fit did not determine V_max and C: V_max ")
    assert fit.reason.endswith("; a session fit needs each known to within 50%")
    assert len(fit.reason) <= 255


def test_peak_speeds_that_do_not_vary_are_refused():
    """No curve can be judged against no variation, and r-squared would be
    minus infinity, which no column can hold."""
    fit = fit_session(np.linspace(1.0, 12.0, 200), np.full(200, 300.0), PARAMS)
    assert (fit.curve, fit.reason) == (None, "their peak speeds do not vary")


def test_a_fit_that_does_not_converge_is_refused(monkeypatch):
    """SciPy's own failure, stood in for: no input reliably makes its
    trust-region solver give up."""
    import scipy.optimize

    def gives_up(*_args, **_kwargs):
        raise RuntimeError("Optimal parameters not found: the maximum number of function evaluations is exceeded.")

    monkeypatch.setattr(scipy.optimize, "curve_fit", gives_up)
    amplitude, peak = _planted(200, 1.0, 12.0, seed=10)
    fit = fit_session(amplitude, peak, PARAMS)
    assert fit.curve is None
    assert fit.reason == ("the fit did not converge: Optimal parameters not found: the maximum number of "
                          "function evaluations is exceeded.")


def test_a_gain_recovers_a_planted_speed_up():
    amplitude, peak = _planted(60, 1.0, 10.0, seed=5, noise=0.02, scale=1.1)
    result = gain(amplitude, peak, PLANTED, PARAMS)
    assert result.reason == ""
    assert result.gain == pytest.approx(1.1, abs=0.01)
    assert result.n_saccades == 60
    assert (result.amplitude_min_deg, result.amplitude_max_deg) == (amplitude.min(), amplitude.max())


def test_a_gain_is_a_median_so_a_few_outliers_do_not_move_it():
    amplitude, peak = _planted(40, 1.0, 10.0, seed=11, noise=0.0)
    peak[:5] *= 3.0
    assert gain(amplitude, peak, PLANTED, PARAMS).gain == pytest.approx(1.0)


def test_a_gain_on_too_few_saccades_is_refused():
    amplitude, peak = _planted(29, 1.0, 10.0, seed=6)
    result = gain(amplitude, peak, PLANTED, PARAMS)
    assert result.gain is None
    assert result.reason == "29 saccades; a gain needs at least 30"


def test_a_gain_on_no_saccades_is_refused_without_a_range():
    result = gain(np.array([]), np.array([]), PLANTED, PARAMS)
    assert (result.gain, result.n_saccades, result.amplitude_min_deg, result.amplitude_max_deg) == (
        None, 0, None, None)
    assert result.reason == "0 saccades; a gain needs at least 30"


def test_vigor_compares_each_saccade_with_the_earlier_sessions_that_cover_its_size():
    """Two earlier sessions twice as fast, fitted only up to 5 deg, and one
    as fast, fitted to 12: at 8 deg only the third covers, so vigor is 100%.
    Extrapolating the other two would give 50%."""
    history = _history((Curve(800.0, 5.0), (1.0, 5.0)), (Curve(800.0, 5.0), (1.0, 5.0)), (PLANTED, (1.0, 12.0)))
    amplitude = np.full(40, 8.0)
    result = vigor(amplitude, PLANTED(amplitude), history, PARAMS)
    assert result.reason == ""
    assert result.value == pytest.approx(1.0)
    assert (result.n_saccades, result.n_history) == (40, 3)


def test_vigor_is_the_median_ratio_against_the_earlier_sessions_median():
    history = _history((PLANTED, (1.0, 12.0)), (Curve(440.0, 5.0), (1.0, 12.0)), (Curve(480.0, 5.0), (1.0, 12.0)))
    amplitude, peak = _planted(50, 1.0, 10.0, seed=7, noise=0.0, scale=0.9 * 1.1)
    peak[:5] *= 3.0  # a few outliers do not move a median
    result = vigor(amplitude, peak, history, PARAMS)
    assert result.value == pytest.approx(0.9)


def test_vigor_needs_three_earlier_sessions():
    history = _history((PLANTED, (1.0, 12.0)), (PLANTED, (1.0, 12.0)))
    amplitude, peak = _planted(50, 1.0, 10.0, seed=8)
    result = vigor(amplitude, peak, history, PARAMS)
    assert result.value is None
    assert result.reason == "no history yet (2 earlier)"


def test_vigor_needs_enough_saccades_the_history_covers():
    history = _history(*[(PLANTED, (1.0, 5.0))] * 3)
    amplitude = np.concatenate([np.full(40, 8.0), np.full(10, 3.0)])
    result = vigor(amplitude, PLANTED(amplitude), history, PARAMS)
    assert result.value is None
    assert result.reason == "too few saccades (10)"
