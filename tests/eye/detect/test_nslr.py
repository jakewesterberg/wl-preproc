"""NSLR-HMM's stages, each held to the reference (gated on
`WLPP_NSLR_REFERENCE`) and to the rule design spec
`2026-09-27-nslr-design.md` section 1 states (ungated). Pure numpy:
nothing here imports `wl_preproc.schema`."""

from __future__ import annotations

import numpy as np
import pytest

from tests.eye.detect._nslr_reference import reference
from tests.eye.detect._nslr_traces import overshoot_trace
from tests.eye.detect._remodnav_traces import gaze_trace
from wl_preproc.eye.detect.nslr import (
    DEFAULT_NSLR_PARAMS,
    continuous_fit,
    decode,
    features,
    fit_pieces,
    segment,
    split_prior,
    transition_matrix,
)

#: Short traces, because the reference is slow: (fs_hz, seed, seconds).
CASES = [(500.0, 4, 3.0), (500.0, 12, 3.0), (1000.0, 1, 3.0)]


def _trace(fs_hz, seed, seconds=3.0):
    xy = overshoot_trace(fs_hz, seed, seconds)
    return np.arange(len(xy)) / fs_hz, xy


def _start_noise(xy):
    return np.std(xy, axis=0) + DEFAULT_NSLR_PARAMS.structural_error_deg


# -- Spec 1.2: the split prior ------------------------------------------------


@pytest.mark.parametrize("noise_mean", [0.05, 0.13, 0.4])
def test_the_split_prior_matches_the_reference_exactly(noise_mean):
    slow_nslr, _ = reference()
    ours = split_prior(noise_mean, DEFAULT_NSLR_PARAMS)
    theirs = slow_nslr.gaze_split(noise_mean)
    for dt in (0.002, 0.001, 0.0020000000000000018):
        assert ours(dt) == float(theirs(np.array([dt]))[0])


# -- Spec 1.1: segmentation --------------------------------------------------------


@pytest.mark.parametrize("fs_hz, seed, seconds", CASES)
def test_segmentation_matches_the_reference_exactly(fs_hz, seed, seconds):
    slow_nslr, _ = reference()
    ts, xy = _trace(fs_hz, seed, seconds)
    noise = _start_noise(xy)
    ours = segment(ts, xy, noise, split_prior(np.mean(noise), DEFAULT_NSLR_PARAMS))
    theirs = slow_nslr.nslr_segments(ts, xy, noise, slow_nslr.gaze_split(np.mean(noise)))
    assert ours == theirs


def test_without_greedy_continuity_the_segmentation_differs():
    """The null for spec 1.1's central rule: a child fitting its own intercept
    is not NSLR, and the comparison above must be able to see that."""
    ts, xy = _trace(500.0, 4)
    noise = _start_noise(xy)
    split = split_prior(np.mean(noise), DEFAULT_NSLR_PARAMS)
    assert segment(ts, xy, noise, split) != segment(ts, xy, noise, split, _continuity=False)


def test_splits_cover_the_trace():
    ts, xy = _trace(500.0, 17)
    noise = _start_noise(xy)
    splits = segment(ts, xy, noise, split_prior(np.mean(noise), DEFAULT_NSLR_PARAMS))
    assert splits[0] == 0 and splits[-1] == len(ts)
    assert all(a < b for a, b in zip(splits, splits[1:]))




# -- Spec 1.3: the continuous fit ------------------------------------------------


@pytest.mark.parametrize("fs_hz, seed, seconds", CASES)
def test_the_continuous_fit_matches_the_reference_bit_for_bit(fs_hz, seed, seconds):
    slow_nslr, _ = reference()
    ts, xy = _trace(fs_hz, seed, seconds)
    noise = _start_noise(xy)
    splits = slow_nslr.nslr_segments(ts, xy, noise, slow_nslr.gaze_split(np.mean(noise)))
    ours = continuous_fit(ts, xy, splits)
    theirs = slow_nslr.segmented_linear_fit(ts, xy, splits)
    assert len(ours) == len(theirs)
    assert all(np.array_equal(np.asarray(a), np.asarray(b)) for a, b in zip(ours, theirs))


def test_the_continuous_fit_is_exact_on_a_polyline_in_its_own_geometry():
    """Segment k's line runs from its FIRST sample to its LAST (`slow_nslr.py`
    75-110), while the endpoint times `fit_pieces` reports put each shared
    endpoint at the NEXT segment's first sample (151-156) -- one sample apart.
    Measured while planning: a polyline built that way is fitted exactly."""
    ts = np.arange(300) / 500.0
    x = np.empty(300)
    x[:150] = np.linspace(0.0, 3.0, 150)
    x[150:] = np.linspace(3.0, 1.5, 150)
    ends = continuous_fit(ts, np.column_stack([x, 2.0 * x]), [0, 150, 300])
    assert np.allclose(np.asarray(ends)[:, 0], [0.0, 3.0, 1.5], atol=1e-9)


# -- Spec 1.4: the noise estimate --------------------------------------------------


@pytest.mark.parametrize("fs_hz, seed, seconds", CASES)
def test_one_piece_matches_the_references_fit_gaze(fs_hz, seed, seconds):
    slow_nslr, _ = reference()
    ts, xy = _trace(fs_hz, seed, seconds)
    ours = fit_pieces([(ts, xy)], DEFAULT_NSLR_PARAMS)
    theirs = slow_nslr.fit_gaze(ts, xy)
    piece = ours.pieces[0]
    assert [s.i for s in theirs.segments] == [(piece.splits[k], piece.splits[k + 1])
                                             for k in range(len(piece.splits) - 1)]
    assert all(np.array_equal(np.asarray(a), np.asarray(b)) for a, b in zip(theirs.x, piece.endpoints))
    assert np.array_equal(theirs.t, piece.times)
    assert not ours.capped


def test_the_noise_is_one_estimate_pooled_over_every_piece():
    ts, xy = _trace(500.0, 12, 4.0)
    half = len(ts) // 2
    pooled = fit_pieces([(ts[:half], xy[:half]), (ts[:len(ts) - half], xy[half:])], DEFAULT_NSLR_PARAMS)
    assert pooled.noise.shape == (2,)
    assert [p.splits[-1] for p in pooled.pieces] == [half, len(ts) - half]


def test_a_three_piece_line_is_split_exactly_at_its_knots():
    """Through the noise estimate, as the pipeline runs it: the structural
    error keeps the split prior sane. (Handed a bare 0.01 deg noise, the split
    prior -- which grows as 1/noise -- splits at every sample; measured while
    planning.) Planning measured [0, 200, 400, 600] on seeds 0-2."""
    fs = 500.0
    ts = np.arange(600) / fs
    x = np.piecewise(ts, [ts < 0.4, (ts >= 0.4) & (ts < 0.8), ts >= 0.8],
                     [lambda t: 0.0 * t, lambda t: 40.0 * (t - 0.4), lambda t: 16.0 + 0.0 * t])
    for seed in range(3):
        xy = np.column_stack([x, np.zeros_like(x)]) + np.random.default_rng(seed).normal(0, 0.01, (600, 2))
        assert fit_pieces([(ts, xy)], DEFAULT_NSLR_PARAMS).pieces[0].splits == [0, 200, 400, 600]


def test_the_noise_guard_stops_and_reports():
    """Review Focus 5: the reference loops until a noise pair recurs exactly,
    and nothing guarantees it will; the guard keeps the last pass."""
    from dataclasses import replace

    ts, xy = _trace(500.0, 4)
    fit = fit_pieces([(ts, xy)], replace(DEFAULT_NSLR_PARAMS, max_noise_passes=1))
    assert fit.passes == 1 and fit.capped


# -- Spec 1.5 and 1.6: features and the decode ---------------------------------------


@pytest.mark.parametrize("fs_hz, seed, seconds", CASES)
def test_features_and_decode_match_the_reference_exactly(fs_hz, seed, seconds):
    slow_nslr, nslr_hmm = reference()
    ts, xy = _trace(fs_hz, seed, seconds)
    theirs_fit = slow_nslr.fit_gaze(ts, xy)
    piece = fit_pieces([(ts, xy)], DEFAULT_NSLR_PARAMS).pieces[0]
    ours = features(piece)
    assert ours == list(nslr_hmm.segment_features(theirs_fit.segments))
    theirs_path = list(nslr_hmm.classify_segments(theirs_fit.segments))
    assert [state + 1 for state in decode(ours, DEFAULT_NSLR_PARAMS)] == theirs_path


def test_the_transition_matrix_is_the_references():
    _, nslr_hmm = reference()
    assert np.array_equal(transition_matrix(), nslr_hmm.GazeTransitionModel)


def test_forbidden_transitions_are_never_taken():
    """Spec 1.6: fixation->PSO, PSO->saccade and pursuit->PSO."""
    rng = np.random.default_rng(3)
    feats = [(float(rng.uniform(-1, 3)), float(rng.normal(0, 2))) for _ in range(400)]
    path = decode(feats, DEFAULT_NSLR_PARAMS)
    pairs = set(zip(path, path[1:]))
    assert not pairs & {(0, 2), (2, 1), (3, 2)}


def test_a_zero_speed_segment_turns_to_zero():
    from wl_preproc.eye.detect.nslr import PieceFit

    piece = PieceFit(splits=[0, 10, 20, 30], times=np.array([0.0, 0.02, 0.04, 0.058]),
                     endpoints=[np.array([0.0, 0.0]), np.array([1.0, 0.0]),
                                np.array([1.0, 0.0]), np.array([2.0, 0.0])])
    f = features(piece)
    assert f[0][1] == 0.0                      # no previous direction
    assert f[1][0] == np.log10(1e-6)           # zero speed, clipped
    assert f[1][1] == 0.0 and f[2][1] == 0.0   # NaN direction, then NaN cosine


# -- Spec 3: pieces, and the registered detector -------------------------------------


def _available(usable):
    from wl_preproc.eye.detect.labels import Label

    return np.array([None if ok else Label.INVALID for ok in usable], dtype=object)


@pytest.mark.parametrize("unusable_fraction", [0.1, 0.9])
def test_no_run_contains_an_unusable_sample(unusable_fraction):
    """Review Focus 1."""
    from wl_preproc.eye.detect.nslr import detect_nslr

    rng = np.random.default_rng(10)
    gaze = gaze_trace(500.0, 10, duration_s=8.0)
    usable = ~np.isnan(gaze[:, 0])
    while (~usable).mean() < unusable_fraction:
        s = int(rng.integers(0, len(gaze)))
        usable[s:s + int(rng.integers(20, 300))] = False
    gaze = np.nan_to_num(gaze)

    runs = detect_nslr(gaze, np.zeros_like(gaze), _available(usable), 500.0, DEFAULT_NSLR_PARAMS)

    assert runs
    assert all(usable[r.start:r.stop].all() for r in runs)
    assert all(a.stop <= b.start for a, b in zip(runs, runs[1:]))


def test_pieces_of_one_two_and_zero_samples():
    """Review Focus 2: a one-sample piece is unlabelled, a two-sample piece is
    labelled, and no usable sample at all is no runs."""
    from wl_preproc.eye.detect.nslr import detect_nslr

    gaze = np.zeros((7, 2))
    gaze[:, 0] = [0.0, 1.0, 0.0, 2.0, 2.1, 0.0, 0.0]
    usable = np.array([False, True, False, True, True, False, False])

    runs = detect_nslr(gaze, np.zeros_like(gaze), _available(usable), 500.0, DEFAULT_NSLR_PARAMS)

    covered = {i for r in runs for i in range(r.start, r.stop)}
    assert covered == {3, 4}
    assert detect_nslr(gaze, np.zeros_like(gaze), _available(np.zeros(7, bool)), 500.0,
                       DEFAULT_NSLR_PARAMS) == []


def test_the_shared_velocity_is_ignored():
    """Spec 3: NSLR never differentiates, so the velocity argument changes nothing."""
    from wl_preproc.eye.detect.nslr import detect_nslr

    gaze = np.nan_to_num(gaze_trace(500.0, 11, duration_s=4.0))
    usable = np.ones(len(gaze), bool)
    a = detect_nslr(gaze, np.zeros_like(gaze), _available(usable), 500.0, DEFAULT_NSLR_PARAMS)
    b = detect_nslr(gaze, np.full_like(gaze, 123.0), _available(usable), 500.0, DEFAULT_NSLR_PARAMS)
    assert a == b


def test_the_detector_runs_at_the_rigs_real_rate():
    """Review Focus 4."""
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.nslr import detect_nslr

    gaze = gaze_trace(498.55, 12, duration_s=6.0)
    usable = ~np.isnan(gaze[:, 0])
    runs = detect_nslr(np.nan_to_num(gaze), np.zeros_like(gaze), _available(usable), 498.55,
                       DEFAULT_NSLR_PARAMS)
    assert {r.label for r in runs} <= {Label.SACCADE, Label.PSO, Label.FIXATION, Label.PURSUIT}
    assert Label.SACCADE in {r.label for r in runs}
