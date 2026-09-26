"""REMoDNaV's pieces, each pinned to the rule design spec
`2026-09-26-remodnav-design.md` section 1 states and -- where the oracle is
installed -- to the oracle's own function in `remodnav/clf.py` 1.1.2.

Pure numpy: nothing here imports `wl_preproc.schema`, so the 3.13
cross-check runs all of it.
"""

from __future__ import annotations

import inspect
from dataclasses import replace

import numpy as np
import pytest

from wl_preproc.eye.detect.remodnav import (
    _MAD_C,
    DEFAULT_REMODNAV_PARAMS,
    _mad,
    _offset,
    _onset,
    _runs_above,
    _thresholds,
)


def _clf():
    return pytest.importorskip("remodnav").clf


# -- Spec 1.1: the adaptive threshold ------------------------------------------


def test_the_mad_is_statsmodels_sigma_scaled_mad():
    """`clf.py` 311 calls `statsmodels.robust.scale.mad`, which divides by
    0.6745. A raw MAD would put every threshold at 0.6745 of the oracle's."""
    scale = pytest.importorskip("statsmodels.robust.scale")
    values = np.random.default_rng(1).gamma(2.0, 5.0, 5000)

    assert inspect.signature(scale.mad).parameters["c"].default == _MAD_C
    assert _mad(values) == scale.mad(values)


def test_thresholds_on_a_known_distribution():
    # 1..5 about a median of 3: median |deviation| is 1, so MAD = 1 / 0.6745.
    speeds = np.tile([1.0, 2.0, 3.0, 4.0, 5.0], 20)

    result = _thresholds(speeds, DEFAULT_REMODNAV_PARAMS)

    assert result.peak == pytest.approx(3.0 + 2 * 5.0 / _MAD_C)
    assert result.onset == pytest.approx(3.0 + 5.0 / _MAD_C)


def test_the_iteration_keeps_its_last_value_at_the_cap():
    speeds = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 50.0])

    # First pass, all six below 300: median 3.5, median |deviation| 1.5.
    once = _thresholds(speeds, replace(DEFAULT_REMODNAV_PARAMS, max_iterations=1))
    assert once.peak == pytest.approx(3.5 + 10.0 * 1.5 / _MAD_C)
    assert once.onset == pytest.approx(3.5 + 5.0 * 1.5 / _MAD_C)

    # Second pass drops 50 (above 25.7): median 3, median |deviation| 1.
    settled = _thresholds(speeds, DEFAULT_REMODNAV_PARAMS)
    assert settled.peak == pytest.approx(3.0 + 10.0 / _MAD_C)


def test_no_speed_below_the_start_means_no_threshold():
    assert _thresholds(np.full(100, 400.0), DEFAULT_REMODNAV_PARAMS) is None
    assert _thresholds(np.full(100, np.nan), DEFAULT_REMODNAV_PARAMS) is None


def test_a_set_the_iteration_empties_means_no_threshold():
    """A constant 10: the first pass lands exactly on 10, and nothing is below
    10. The oracle returns NaN here, which detects nothing (spec 1.1)."""
    assert _thresholds(np.full(100, 10.0), DEFAULT_REMODNAV_PARAMS) is None


def test_a_threshold_run_to_zero_keeps_the_previous_one():
    """`clf.py` 320-324: all-zero speeds put the new threshold at 0, and the
    oracle keeps the start value instead."""
    result = _thresholds(np.zeros(100), DEFAULT_REMODNAV_PARAMS)

    assert (result.peak, result.onset) == (300.0, 0.0)


def test_thresholds_match_the_oracle_exactly():
    clf = _clf()
    classifier = clf.EyegazeClassifier(px2deg=1.0, sampling_rate=500.0)
    rng = np.random.default_rng(7)
    for _ in range(20):
        speeds = np.concatenate(
            [rng.gamma(2.0, 4.0, 2000), rng.uniform(100.0, 600.0, int(rng.integers(5, 60)))]
        )
        rng.shuffle(speeds)

        ours = _thresholds(speeds, DEFAULT_REMODNAV_PARAMS)

        assert (ours.peak, ours.onset) == classifier.get_adaptive_saccade_velocity_velthresh(speeds)


# -- Runs above a threshold (clf.py 41-83) -------------------------------------


def test_runs_open_above_and_close_below_the_threshold():
    runs = _runs_above(np.array([0.0, 5.0, 6.0, 0.0, 7.0, 0.0]), 4.0)

    assert [(r.start, r.stop) for r in runs] == [(1, 3), (4, 5)]
    # The weight includes the closing sample, as clf.py 72-75 sums it.
    assert [r.weight for r in runs] == [11.0, 7.0]


def test_a_sample_at_the_threshold_neither_opens_nor_closes_a_run():
    runs = _runs_above(np.array([0.0, 5.0, 4.0, 5.0, 0.0]), 4.0)

    assert [(r.start, r.stop) for r in runs] == [(1, 4)]
    assert _runs_above(np.array([0.0, 4.0, 0.0]), 4.0) == []


def test_a_missing_sample_does_not_close_a_run():
    runs = _runs_above(np.array([0.0, 5.0, np.nan, 5.0, 0.0]), 4.0)

    assert [(r.start, r.stop, r.weight) for r in runs] == [(1, 4, 10.0)]


def test_a_run_open_at_the_end_is_closed_there_even_from_sample_zero():
    """Spec 2.2's two fixes. The oracle drops the first run (`if sac_on:` is
    false for 0) and closes the second one sample short."""
    assert [(r.start, r.stop) for r in _runs_above(np.array([30.0, 40.0, 50.0]), 20.0)] == [(0, 3)]
    assert [(r.start, r.stop) for r in _runs_above(np.array([1.0, 40.0, 50.0]), 20.0)] == [(1, 3)]


def test_runs_match_the_oracle_away_from_the_two_fixed_edges():
    clf = _clf()
    rng = np.random.default_rng(11)
    for _ in range(50):
        values = rng.gamma(2.0, 10.0, 400)
        values[rng.integers(0, 400, 5)] = np.nan
        values[[0, -1]] = 0.0  # starts and ends below: spec 2.2's edges never reached

        ours = _runs_above(values, 30.0)
        theirs = clf.find_peaks(values, 30.0)

        assert [(r.start, r.stop) for r in ours] == [(s, e) for s, e, _ in theirs]
        assert [r.weight for r in ours] == [float(v.sum()) for _, _, v in theirs]


# -- Spec 1.3: onset and offset, as coded --------------------------------------


def test_the_onset_stops_at_the_first_sample_below_threshold_not_the_minimum():
    """Spec 1.3's worked example: the preceding minimum is at 1; the oracle's
    onset rule stops at 3, and so does this one."""
    assert _onset(np.array([5.0, 3.0, 4.0, 10.0, 50.0, 100.0]), 4, 20.0) == 3


def test_the_offset_walks_on_to_the_local_minimum():
    assert _offset(np.array([100.0, 50.0, 10.0, 4.0, 3.0, 5.0]), 1, 20.0) == 4


def test_the_searches_stop_at_the_ends_of_the_array():
    speeds = np.array([50.0, 50.0, 50.0])

    assert _onset(speeds, 2, 20.0) == 0
    assert _offset(speeds, 0, 20.0) == 2
    # A run closed at the array's end (spec 2.2) starts its search there.
    assert _offset(speeds, 3, 20.0) == 3


def test_onset_and_offset_match_the_oracle():
    clf = _clf()
    rng = np.random.default_rng(13)
    for _ in range(200):
        speeds = rng.gamma(2.0, 10.0, 60)
        speeds[rng.integers(0, 60, 3)] = np.nan
        start = int(rng.integers(0, 60))
        threshold = float(rng.uniform(5.0, 40.0))

        assert _onset(speeds, start, threshold) == clf.find_movement_onsetidx(speeds, start, threshold)
        assert _offset(speeds, start, threshold) == clf.find_movement_offsetidx(speeds, start, threshold)
