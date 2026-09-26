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

from wl_preproc.eye.detect.labels import Label, Run
from wl_preproc.eye.detect.remodnav import (
    _MAD_C,
    DEFAULT_REMODNAV_PARAMS,
    Signals,
    _Samples,
    _candidate_speed,
    _context_window,
    _dilate,
    _fixation_or_pursuit,
    _mad,
    _odd_samples,
    _offset,
    _onset,
    _periods,
    _pieces,
    _piece_saccades,
    _runs_above,
    _saccades,
    _stretches,
    _thresholds,
    classify,
    detect_remodnav,
    shared_speed,
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


# -- Spec 2.1 and 1.2: samples, context, saccades, PSOs -------------------------


def _pattern(n):
    """A deterministic, non-constant baseline speed (1, 2, 3, 4, 5, ...):
    each value is 20% of samples, so the median is 3 and the median
    |deviation| is 1, each with a 10-point margin no single bump can tip.
    Thresholds settle at 3 + 10 / 0.6745 ~= 17.83 (peak) and
    3 + 5 / 0.6745 ~= 10.41 (onset). A constant baseline would empty the
    iteration (spec 1.1)."""
    return np.resize(np.array([1.0, 2.0, 3.0, 4.0, 5.0]), n)


def _bump(speed, centre, peak, half_width):
    """A triangular velocity peak added in place."""
    span = np.arange(centre - half_width, centre + half_width + 1)
    speed[span] += peak * (1.0 - np.abs(span - centre) / (half_width + 1))
    return speed


def _signals(speed, fs_hz=500.0):
    """Positions that integrate `speed` along x, so every amplitude agrees
    with the speeds that produced it."""
    x = np.concatenate([[0.0], np.cumsum(speed[1:]) / fs_hz])
    return Signals(x=x, y=np.zeros_like(x), speed=speed, candidate_speed=speed.copy())


def test_durations_round_to_samples_at_the_rigs_real_rate():
    """Spec 2.1: truncation at 498.55 Hz would make these 4, 19, 19 and 498."""
    samples = _Samples.at(DEFAULT_REMODNAV_PARAMS, 498.55)

    assert (samples.min_saccade, samples.min_intersaccade, samples.max_pso, samples.context) == (5, 20, 20, 499)
    exact = _Samples.at(DEFAULT_REMODNAV_PARAMS, 500.0)
    assert (exact.min_saccade, exact.min_intersaccade, exact.max_pso, exact.context) == (5, 20, 20, 500)
    assert exact.max_saccades_per_sample == 2.0 / 500.0


def test_the_context_window_is_anchored_on_the_run_start():
    """`clf.py` 433-438 (spec 1.2): half the window before the run's start,
    the rest after it, clipped to the range being classified."""
    assert _context_window(1000, 1010, 500, 0, 10_000) == (750, 1260)
    assert _context_window(100, 110, 500, 0, 10_000) == (0, 510)
    assert _context_window(9900, 9910, 500, 0, 10_000) == (9650, 10_000)


def _piece_saccades_of(speed, params=DEFAULT_REMODNAV_PARAMS):
    samples = _Samples.at(params, 500.0)
    return _saccades(_signals(speed), 0, speed.size, None, None, samples, params)


def test_a_saccade_too_close_to_another_is_rejected():
    close = _bump(_bump(_pattern(3000), 1000, 300.0, 8), 1035, 250.0, 8)  # 70 ms apart, edges 18 samples
    apart = _bump(_bump(_pattern(3000), 1000, 300.0, 8), 1060, 250.0, 8)  # 120 ms apart, edges 44 samples

    assert [r.label for r in _piece_saccades_of(close)].count(Label.SACCADE) == 1
    assert [r.label for r in _piece_saccades_of(apart)].count(Label.SACCADE) == 2


def test_the_frequency_cap_stops_the_major_pass():
    """`clf.py` 508-512: stop once the count exceeds the cap times the
    duration. 0.5 Hz over 10 s is 5, so the sixth acceptance stops it."""
    speed = _pattern(5000)
    for centre in range(300, 5000, 450):
        _bump(speed, centre, 300.0, 8)
    params = replace(DEFAULT_REMODNAV_PARAMS, max_initial_saccade_freq_hz=0.5)
    samples = _Samples.at(params, 500.0)
    overall = _thresholds(speed, params)

    found = _saccades(_signals(speed), 0, speed.size, _runs_above(speed, overall.peak),
                      samples.context, samples, params)

    assert [r.label for r in found].count(Label.SACCADE) == 6


def test_a_wobble_after_a_saccade_is_a_pso_that_starts_where_it_stops():
    speed = _bump(_bump(_pattern(3000), 1000, 300.0, 8), 1016, 60.0, 3)

    found = _piece_saccades_of(speed)

    saccade = next(r for r in found if r.label is Label.SACCADE)
    pso = next(r for r in found if r.label is Label.PSO)
    assert pso.start == saccade.stop
    assert pso.stop - pso.start <= _Samples.at(DEFAULT_REMODNAV_PARAMS, 500.0).max_pso


def test_a_pso_larger_than_its_saccade_is_dropped():
    """`clf.py` 496: kept only while its amplitude is below its saccade's."""
    speed = _bump(_bump(_pattern(3000), 1000, 300.0, 8), 1016, 60.0, 3)
    base = _signals(speed)
    x = base.x.copy()
    x[1016:] += 20.0  # a step at the wobble's centre: its endpoints now lie 20 deg apart
    signals = Signals(x=x, y=base.y, speed=base.speed, candidate_speed=base.candidate_speed)
    samples = _Samples.at(DEFAULT_REMODNAV_PARAMS, 500.0)

    found = _saccades(signals, 0, speed.size, None, None, samples, DEFAULT_REMODNAV_PARAMS)

    assert Label.PSO not in [r.label for r in found]


def test_a_missing_speed_inside_the_window_drops_the_pso():
    speed = _bump(_bump(_pattern(3000), 1000, 300.0, 8), 1016, 60.0, 3)
    speed[1016] = np.nan

    assert Label.PSO not in [r.label for r in _piece_saccades_of(speed)]


def test_a_long_wobble_is_cut_at_the_pso_window():
    # Rises from 1020, clear of the saccade (which ends by ~1012), and is
    # still rising where the 20-sample window ends.
    speed = _bump(_bump(_pattern(3000), 1000, 300.0, 8), 1045, 60.0, 25)

    psos = [r for r in _piece_saccades_of(speed) if r.label is Label.PSO]

    assert psos
    assert all(p.stop - p.start <= _Samples.at(DEFAULT_REMODNAV_PARAMS, 500.0).max_pso for p in psos)


def _oracle_kind(label):
    return {"SACC": Label.SACCADE, "HPSO": Label.PSO, "LPSO": Label.PSO}[label]


@pytest.mark.parametrize("fs_hz, seed", [(500.0, 1), (1000.0, 2)])
def test_the_major_pass_matches_the_oracle(fs_hz, seed):
    from tests.eye.detect._remodnav_traces import gaze_trace, oracle_run, signals_from_oracle

    remodnav = pytest.importorskip("remodnav")
    classifier, preprocessed, _events = oracle_run(remodnav, gaze_trace(fs_hz, seed), fs_hz)
    signals = signals_from_oracle(preprocessed)
    samples = _Samples.at(DEFAULT_REMODNAV_PARAMS, fs_hz)
    overall = _thresholds(signals.candidate_speed, DEFAULT_REMODNAV_PARAMS)
    n = signals.speed.size

    ours = _saccades(signals, 0, n, _runs_above(signals.candidate_speed, overall.peak),
                     samples.context, samples, DEFAULT_REMODNAV_PARAMS)
    peaks = remodnav.clf.find_peaks(preprocessed["med_vel"], overall.peak)
    theirs = list(classifier._detect_saccades(peaks, preprocessed, 0, n,
                                              context=classifier.sac_context_winlen))

    assert [(r.start, r.stop, r.label) for r in ours] == [
        (e["start_time"], e["end_time"], _oracle_kind(e["label"])) for e in theirs
    ]


@pytest.mark.parametrize("fs_hz, seed", [(500.0, 3), (1000.0, 4)])
def test_a_piece_search_matches_the_oracle(fs_hz, seed):
    from tests.eye.detect._remodnav_traces import (
        gaze_trace, intersaccadic_windows, oracle_run, signals_from_oracle,
    )

    remodnav = pytest.importorskip("remodnav")
    classifier, preprocessed, events = oracle_run(remodnav, gaze_trace(fs_hz, seed), fs_hz)
    signals = signals_from_oracle(preprocessed)
    samples = _Samples.at(DEFAULT_REMODNAV_PARAMS, fs_hz)
    windows = intersaccadic_windows(events, fs_hz)
    assert len(windows) >= 5  # the fixture's premise
    for start, end in windows[:5]:
        ours = _saccades(signals, start, end, None, None, samples, DEFAULT_REMODNAV_PARAMS)
        theirs = list(classifier._detect_saccades(None, preprocessed, start, end, context=None))

        assert [(r.start, r.stop, r.label) for r in ours] == [
            (e["start_time"], e["end_time"], _oracle_kind(e["label"])) for e in theirs
        ]


# -- Spec 1.5: intersaccadic periods and pieces ---------------------------------


def test_periods_run_from_each_saccade_or_its_pso_to_the_next_saccade():
    events = [Run(10, 20, Label.SACCADE), Run(20, 25, Label.PSO), Run(50, 60, Label.SACCADE)]

    assert _periods(events, 0, 100) == [(0, 10), (25, 50), (60, 100)]
    assert _periods([Run(10, 20, Label.SACCADE), Run(90, 100, Label.SACCADE)], 0, 100) == [(0, 10), (20, 90)]
    assert _periods([], 0, 100) == [(0, 100)]
    assert _periods([Run(0, 20, Label.SACCADE)], 0, 100) == [(20, 100)]


def test_pieces_split_at_missing_positions():
    x = np.zeros(20)
    x[5:8] = np.nan

    assert _pieces(x, 0, 20) == [(0, 5), (8, 20)]
    assert _pieces(x, 6, 6) == []


def test_a_saccade_near_a_piece_edge_is_dropped_with_its_pso():
    """`clf.py` 639-649: within `min_intersaccade` of either edge, rejected,
    and the PSO that follows it goes too. Bump centres 25/41 (not the
    original 30/46, which under `_pattern`'s new baseline finds the saccade
    onset at sample 21 -- outside `min_intersaccade` (20) of the piece's
    edge, so it survived unfiltered): measured onset 16, well inside 20."""
    speed = _bump(_bump(_pattern(600), 25, 300.0, 8), 41, 60.0, 3)
    samples = _Samples.at(DEFAULT_REMODNAV_PARAMS, 500.0)

    assert _piece_saccades(_signals(speed), 0, 600, samples, DEFAULT_REMODNAV_PARAMS) == []


def test_a_piece_too_short_to_search_finds_no_saccade():
    speed = _bump(_pattern(60), 30, 300.0, 8)
    samples = _Samples.at(DEFAULT_REMODNAV_PARAMS, 500.0)

    assert _piece_saccades(_signals(speed), 0, 60, samples, DEFAULT_REMODNAV_PARAMS) == []


def test_the_recursion_finds_a_small_saccade_the_recording_wide_threshold_misses():
    """REMoDNaV's point (spec 1.5): a noisy stretch lifts the recording-wide
    threshold above a small saccade, and the quiet piece between two large
    ones finds it with a threshold of its own."""
    rng = np.random.default_rng(5)
    speed = _pattern(10_000)
    speed[3500:] = rng.gamma(2.0, 15.0, 6500)
    for centre, peak in [(1000, 400.0), (2000, 60.0), (3000, 400.0)]:
        _bump(speed, centre, peak, 8)
    signals = _signals(speed)
    assert _thresholds(signals.candidate_speed, DEFAULT_REMODNAV_PARAMS).peak > 60.0  # the premise

    runs = classify(signals, 500.0, DEFAULT_REMODNAV_PARAMS, shared_speed)

    assert any(r.label is Label.SACCADE and r.start <= 2000 < r.stop for r in runs)


# -- Spec 1.6: fixation or pursuit ---------------------------------------------


def test_stretches_meet_at_the_midpoint_of_the_gap_between_them():
    samples = _Samples.at(DEFAULT_REMODNAV_PARAMS, 500.0)  # both minima 20
    marked = np.zeros(100, dtype=bool)
    marked[30:70] = True

    assert _stretches(marked, 100, samples) == [(False, 0, 29), (True, 29, 69), (False, 69, 100)]


def test_a_short_stretch_is_dropped_and_its_neighbours_merge():
    samples = _Samples.at(DEFAULT_REMODNAV_PARAMS, 500.0)
    marked = np.zeros(100, dtype=bool)
    marked[40:45] = True

    assert _stretches(marked, 100, samples) == [(False, 0, 100)]


def test_the_boundary_spans_two_dropped_stretches_of_opposite_type():
    """Not in the brief verbatim: added because `_stretches meet at the
    midpoint...`'s own fixture can never exercise `int(gap / 2)` for a gap
    other than 1 -- raw stretches from one `np.diff` are always exactly
    adjacent (`following[1] - stretch[2] == 1`, so `int(.../2)` is 0 either
    way) unless a stretch between two survivors was dropped for being too
    short. Dropping exactly one middle stretch still leaves same-type
    neighbours (raw stretches strictly alternate), so this drops two in a
    row -- one of each type -- to leave differently-typed survivors 11
    samples apart. Measured: boundary 29 (24 + int(11 / 2)); the `-> 0`
    mutation gives 24, which this catches and `test_stretches_meet_...`
    (gap always 1 there) cannot."""
    samples = _Samples.at(DEFAULT_REMODNAV_PARAMS, 500.0)  # both minima 20
    marked = np.zeros(65, dtype=bool)
    marked[25:30] = True  # 5 samples: dropped (< 20)
    marked[35:65] = True  # 30 samples: survives

    assert _stretches(marked, 65, samples) == [(False, 0, 29), (True, 29, 65)]


def test_a_slow_ramp_is_pursuit_and_stillness_is_fixation():
    fs = 500.0
    ramp = 10.0 * np.arange(500) / fs
    x = np.concatenate([np.zeros(250), ramp, np.full(250, ramp[-1])])
    x += np.random.default_rng(3).normal(0.0, 0.005, x.size)
    signals = Signals(x=x, y=np.zeros_like(x), speed=np.zeros_like(x), candidate_speed=np.zeros_like(x))
    samples = _Samples.at(DEFAULT_REMODNAV_PARAMS, fs)

    runs = _fixation_or_pursuit(signals, 0, x.size, fs, samples, DEFAULT_REMODNAV_PARAMS, shared_speed)

    pursuit = [r for r in runs if r.label is Label.PURSUIT]
    covered = sum(max(0, min(r.stop, 750) - max(r.start, 250)) for r in pursuit)
    assert covered >= 0.8 * 500
    assert all(r.label is Label.FIXATION for r in runs if r.stop <= 200 or r.start >= 800)


def test_a_piece_shorter_than_a_fixation_emits_nothing():
    samples = _Samples.at(DEFAULT_REMODNAV_PARAMS, 500.0)
    signals = _signals(_pattern(10))

    assert _fixation_or_pursuit(signals, 0, 10, 500.0, samples, DEFAULT_REMODNAV_PARAMS, shared_speed) == []


# Seed 7 at 1000 Hz fails this test's own premise, not the comparison: the
# oracle's major pass swallows the whole planted pursuit ramp into one
# SACC event (measured 8002-9503, almost exactly PURSUIT_S's 8000-9500),
# so no intersaccadic window ever covers it and `chosen` falls back to the
# recording's first two windows instead. That is the fixture's own RNG
# draw, not a rule this implementation gets wrong -- most seeds in this
# range hit the same swallow (measured over seeds 1-39; only 24, 26 and 37
# keep the ramp separate). 24 does, and its comparison matches the oracle.
@pytest.mark.parametrize("fs_hz, seed", [(500.0, 6), (1000.0, 24)])
def test_fixation_or_pursuit_matches_the_oracle(fs_hz, seed):
    from tests.eye.detect._remodnav_traces import (
        PURSUIT_S, gaze_trace, intersaccadic_windows, oracle_run, signals_from_oracle, two_point_speed,
    )

    remodnav = pytest.importorskip("remodnav")
    classifier, preprocessed, events = oracle_run(remodnav, gaze_trace(fs_hz, seed), fs_hz)
    signals = signals_from_oracle(preprocessed)
    samples = _Samples.at(DEFAULT_REMODNAV_PARAMS, fs_hz)
    kinds = {"FIXA": Label.FIXATION, "PURS": Label.PURSUIT}
    windows = intersaccadic_windows(events, fs_hz)
    middle = int(round(sum(PURSUIT_S) / 2 * fs_hz))
    chosen = [w for w in windows if w[0] <= middle < w[1]] + windows[:2]
    assert chosen and chosen[0][0] <= middle < chosen[0][1]  # the pursuit is covered
    for start, end in chosen:
        ours = _fixation_or_pursuit(signals, start, end, fs_hz, samples, DEFAULT_REMODNAV_PARAMS,
                                    two_point_speed)
        theirs = list(classifier._fix_or_pursuit(preprocessed, start, end))

        assert [(r.start, r.stop, r.label) for r in ours] == [
            (e["start_time"], e["end_time"], kinds[e["label"]]) for e in theirs
        ]


# -- classify, and the Review Focus edge cases ---------------------------------


def test_classify_returns_disjoint_sorted_runs_in_the_declared_vocabulary():
    from tests.eye.detect._remodnav_traces import gaze_trace
    from wl_preproc.eye.detect.velocity import velocity

    xy = gaze_trace(500.0, 8)
    v = velocity(xy, 500.0)
    speed = np.hypot(v[:, 0], v[:, 1])
    signals = Signals(x=xy[:, 0], y=xy[:, 1], speed=speed, candidate_speed=speed)

    runs = classify(signals, 500.0, DEFAULT_REMODNAV_PARAMS, shared_speed)

    assert {r.label for r in runs} <= {Label.SACCADE, Label.PSO, Label.FIXATION, Label.PURSUIT}
    assert all(a.stop <= b.start for a, b in zip(runs, runs[1:]))


def test_a_trace_shorter_than_every_window_classifies_without_error():
    """Review Focus 2."""
    for n in (1, 5, 30):
        signals = _signals(_pattern(n))

        runs = classify(signals, 500.0, DEFAULT_REMODNAV_PARAMS, shared_speed)

        assert all(0 <= r.start < r.stop <= n for r in runs)


def test_a_perfectly_still_trace_is_all_fixation():
    """Review Focus 3: a synthetic hold has zero speed, so the median and MAD
    are 0 -- no saccade may be invented."""
    zeros = np.zeros(2000)
    signals = Signals(x=zeros.copy(), y=zeros.copy(), speed=zeros.copy(), candidate_speed=zeros.copy())

    runs = classify(signals, 500.0, DEFAULT_REMODNAV_PARAMS, shared_speed)

    assert {r.label for r in runs} == {Label.FIXATION}


def test_a_saccade_at_the_very_end_is_closed_there():
    """Review Focus 5: still moving at the last sample."""
    speed = _pattern(3000)
    speed[-10:] += 300.0

    runs = classify(_signals(speed), 500.0, DEFAULT_REMODNAV_PARAMS, shared_speed)

    assert all(r.stop <= 3000 for r in runs)


# -- Spec 3: the production signals, and the registered detector ---------------


def test_the_median_window_is_odd():
    assert (_odd_samples(50.0, 500.0), _odd_samples(50.0, 1000.0), _odd_samples(50.0, 498.55)) == (25, 51, 25)


def test_dilation_reaches_both_ways_at_any_length():
    mask = np.zeros(10, dtype=bool)
    mask[5] = True

    assert np.flatnonzero(_dilate(mask, 2)).tolist() == [3, 4, 5, 6, 7]
    assert _dilate(np.array([True]), 50).tolist() == [True]


def test_the_candidate_speed_is_missing_within_reach_of_an_unusable_sample():
    """Spec 3 item 2: a filtered sample whose window touches an unusable one
    is unusable, and so is any speed the shared estimator's +-2 reach draws
    from one. At 500 Hz the window is 25, so the reach is 12 + 2 = 14."""
    from scipy.ndimage import median_filter

    from tests.eye.detect._remodnav_traces import gaze_trace

    gaze = np.nan_to_num(gaze_trace(500.0, 9)[:400])
    usable = np.ones(400, dtype=bool)
    usable[200] = False

    candidate = _candidate_speed(gaze, usable, 500.0, DEFAULT_REMODNAV_PARAMS)

    assert np.isnan(candidate[186:215]).all()
    assert not np.isnan(candidate[[185, 215]]).any()
    filtered = np.column_stack([median_filter(gaze[:, k], size=25) for k in (0, 1)])
    assert np.array_equal(candidate[20:180], shared_speed(filtered, 500.0)[20:180])


def test_what_the_mask_withholds_never_reaches_the_candidate_speed_even_as_nan():
    """Spec 3 item 2's "blink positions never reach the candidate speed",
    for a withheld position that is NaN as well as one that is finite.
    scipy's 1-D median filter keeps a running median, and a NaN inside it
    corrupts windows well past its own: a 100-sample NaN block changed 76
    filtered samples, up to 88 past the block's end, at width 25 (measured on
    scipy 1.17.1 before `_candidate_speed` replaced withheld positions)."""
    from tests.eye.detect._remodnav_traces import gaze_trace

    finite = np.nan_to_num(gaze_trace(500.0, 9)[:2000])
    usable = np.ones(2000, dtype=bool)
    usable[1200:1300] = False
    missing = finite.copy()
    missing[1200:1300] = np.nan

    assert np.array_equal(
        _candidate_speed(missing, usable, 500.0, DEFAULT_REMODNAV_PARAMS),
        _candidate_speed(finite, usable, 500.0, DEFAULT_REMODNAV_PARAMS),
        equal_nan=True,
    )


def _available(usable):
    return np.array([None if ok else Label.INVALID for ok in usable], dtype=object)


@pytest.mark.parametrize("unusable_fraction", [0.1, 0.9])
def test_no_run_contains_an_unusable_sample(unusable_fraction):
    """Review Focus 1: lost tracking in blocks, up to 90% of the recording."""
    from tests.eye.detect._remodnav_traces import gaze_trace
    from wl_preproc.eye.detect.velocity import velocity

    rng = np.random.default_rng(10)
    gaze = gaze_trace(500.0, 10)
    usable = ~np.isnan(gaze[:, 0])
    while (~usable).mean() < unusable_fraction:
        start = int(rng.integers(0, gaze.shape[0]))
        usable[start:start + int(rng.integers(20, 400))] = False
    gaze = np.nan_to_num(gaze)

    runs = detect_remodnav(gaze, velocity(gaze, 500.0), _available(usable), 500.0, DEFAULT_REMODNAV_PARAMS)

    assert all(usable[r.start:r.stop].all() for r in runs)


def test_an_all_unusable_trace_yields_nothing():
    gaze = np.zeros((500, 2))

    assert detect_remodnav(gaze, gaze.copy(), _available(np.zeros(500, dtype=bool)), 500.0,
                           DEFAULT_REMODNAV_PARAMS) == []


def test_the_detector_runs_at_the_rigs_real_rate():
    """Review Focus 4: 498.55 Hz, the reference recording's measured rate."""
    from tests.eye.detect._remodnav_traces import gaze_trace
    from wl_preproc.eye.detect.velocity import velocity

    gaze = gaze_trace(498.55, 11)
    usable = ~np.isnan(gaze[:, 0])
    gaze = np.nan_to_num(gaze)

    runs = detect_remodnav(gaze, velocity(gaze, 498.55), _available(usable), 498.55, DEFAULT_REMODNAV_PARAMS)

    assert {Label.SACCADE, Label.FIXATION} <= {r.label for r in runs}
