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
    segment,
    split_prior,
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
