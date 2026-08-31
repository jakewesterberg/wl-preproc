import numpy as np
import pytest

from wl_preproc.eye.detect.velocity import velocity


def test_a_constant_gaze_has_zero_velocity():
    gaze = np.tile([3.0, -2.0], (20, 1))
    assert velocity(gaze, 500.0) == pytest.approx(np.zeros((20, 2)), abs=1e-12)


def test_a_linear_ramp_recovers_its_own_slope():
    """5 deg/s in x and -3 deg/s in y, sampled at 500 Hz. Interior samples must
    recover exactly; the estimator's own window is what makes the first two and
    last two samples different, which the next test pins."""
    fs_hz = 500.0
    t = np.arange(50) / fs_hz
    gaze = np.column_stack([5.0 * t, -3.0 * t])

    result = velocity(gaze, fs_hz)

    assert result[2:-2, 0] == pytest.approx(5.0, abs=1e-9)
    assert result[2:-2, 1] == pytest.approx(-3.0, abs=1e-9)


def test_the_two_samples_at_each_edge_are_zero_not_wrong():
    """The five-point estimator has no window at the edges. Zero is stated
    rather than extrapolated: a fabricated edge velocity would be indis-
    tinguishable from a real one to every detector downstream, and a saccade
    detected in the first two samples of a recording is an artifact."""
    fs_hz = 500.0
    t = np.arange(30) / fs_hz
    gaze = np.column_stack([100.0 * t, 100.0 * t])

    result = velocity(gaze, fs_hz)

    assert result[:2] == pytest.approx(np.zeros((2, 2)))
    assert result[-2:] == pytest.approx(np.zeros((2, 2)))


def test_the_rate_scales_the_result():
    """A velocity in degrees per SECOND must double when the same samples are
    declared to have arrived twice as fast."""
    gaze = np.column_stack([np.arange(20) * 0.1, np.zeros(20)])

    slow = velocity(gaze, 500.0)
    fast = velocity(gaze, 1000.0)

    assert fast[2:-2] == pytest.approx(2.0 * slow[2:-2])


def test_a_trace_shorter_than_the_window_is_all_zero_not_an_error():
    """A four-sample recording cannot support a five-point estimator. Returning
    zeros lets a caller proceed and find nothing, rather than raising from deep
    inside a daemon pass."""
    assert velocity(np.zeros((4, 2)), 500.0) == pytest.approx(np.zeros((4, 2)))
