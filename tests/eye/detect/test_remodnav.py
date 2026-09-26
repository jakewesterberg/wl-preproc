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

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.remodnav import (
    _MAD_C,
    DEFAULT_REMODNAV_PARAMS,
    Signals,
    _Samples,
    _context_window,
    _mad,
    _offset,
    _onset,
    _runs_above,
    _saccades,
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


# -- Spec 2.1 and 1.2: samples, context, saccades, PSOs -------------------------


def _pattern(n):
    """A deterministic, non-constant baseline speed (1, 2, 3, 2, ...): median
    2 and median |deviation| 0.5, so thresholds settle at 2 + 10 * 0.741 and
    2 + 5 * 0.741. A constant baseline would empty the iteration (spec 1.1)."""
    return np.resize(np.array([1.0, 2.0, 3.0, 2.0]), n)


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
    # `_pattern`'s baseline sample count splits exactly 1500/1500 between the
    # zero- and one-deviation classes over any 3000-sample stretch. A bump
    # centred on an EVEN sample removes an unbalanced number of each class
    # from that split (its 17-sample span is not a multiple of the pattern's
    # period of 4) and tips `_thresholds`' iteration to a degenerate
    # peak/onset of 1.0 -- measured for centre 1060, and for every even centre
    # from 1050 to 1074 tried while diagnosing this. An odd centre keeps the
    # split even and the thresholds at their expected 9.41/5.71.
    close = _bump(_bump(_pattern(3000), 1000, 300.0, 8), 1035, 250.0, 8)  # 70 ms apart, edges 18 samples
    apart = _bump(_bump(_pattern(3000), 1000, 300.0, 8), 1061, 250.0, 8)  # 122 ms apart, edges 44 samples

    assert [r.label for r in _piece_saccades_of(close)].count(Label.SACCADE) == 1
    assert [r.label for r in _piece_saccades_of(apart)].count(Label.SACCADE) == 2


def test_the_frequency_cap_stops_the_major_pass():
    """`clf.py` 508-512: stop once the count exceeds the cap times the
    duration. 0.5 Hz over 10 s is 5, so the sixth acceptance stops it."""
    speed = _pattern(5000)
    # Odd centres, not `range(300, ...)`'s even ones: see the note above
    # `test_a_saccade_too_close_to_another_is_rejected` -- an even centre
    # here collapses `_thresholds` to a degenerate 1.0/1.0 for the same
    # reason, merging every bump into one all-spanning candidate.
    for centre in range(301, 5000, 450):
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
