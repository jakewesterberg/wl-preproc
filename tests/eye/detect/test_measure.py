import numpy as np
import pytest

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.measure import (
    MICROSACCADE_MAX_DEG, classify, measure,
)


def _ramp(n=20, fs_hz=500.0, vx=100.0, vy=0.0):
    t = np.arange(n) / fs_hz
    return np.column_stack([vx * t, vy * t])


def test_amplitude_is_the_displacement_across_the_interval():
    """Endpoint-to-endpoint, NOT path length: a saccade's amplitude is where
    the eye ended up relative to where it started, and path length would count
    any wobble on the way as extra amplitude."""
    gaze = np.column_stack([[0.0, 1.0, 3.0, 2.0, 4.0], np.zeros(5)])
    velocity = np.zeros((5, 2))

    result = measure(gaze, velocity, start=0, stop=5, fs_hz=500.0)

    assert result.amplitude_deg == pytest.approx(4.0)


def test_amplitude_is_euclidean_across_both_axes():
    gaze = np.array([[0.0, 0.0], [3.0, 4.0]])
    result = measure(gaze, np.zeros((2, 2)), start=0, stop=2, fs_hz=500.0)
    assert result.amplitude_deg == pytest.approx(5.0)


def test_peak_velocity_is_the_maximum_speed_inside_the_interval_only():
    """Bounded to the interval: a faster sample just outside it belongs to a
    different event, and letting it leak in would inflate the main sequence
    that vigor is measured against (design spec section 6.5)."""
    velocity = np.zeros((10, 2))
    velocity[2] = [50.0, 0.0]
    velocity[4] = [80.0, 60.0]      # speed 100, inside
    velocity[8] = [400.0, 0.0]      # outside

    result = measure(np.zeros((10, 2)), velocity, start=1, stop=6, fs_hz=500.0)

    assert result.peak_velocity_deg_s == pytest.approx(100.0)


def test_duration_counts_samples_not_endpoints():
    """`stop` is exclusive, so a 6-sample event at 500 Hz lasts 12 ms."""
    result = measure(np.zeros((20, 2)), np.zeros((20, 2)), start=4, stop=10, fs_hz=500.0)
    assert result.duration_s == pytest.approx(6 / 500.0)


def test_classify_splits_at_the_threshold_and_the_boundary_is_a_saccade():
    """At-or-above is a saccade. Stated because a boundary convention nobody
    writes down is one every reimplementation gets to choose differently."""
    assert classify(0.4, MICROSACCADE_MAX_DEG) is Label.MICROSACCADE
    assert classify(0.999, MICROSACCADE_MAX_DEG) is Label.MICROSACCADE
    assert classify(1.0, MICROSACCADE_MAX_DEG) is Label.SACCADE
    assert classify(12.0, MICROSACCADE_MAX_DEG) is Label.SACCADE


def test_the_threshold_default_is_the_conventional_one_degree():
    assert MICROSACCADE_MAX_DEG == 1.0
