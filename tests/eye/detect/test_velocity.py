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


def test_exactly_five_samples_computes_one_interior_value():
    """Five samples is the shortest trace that supports one interior velocity
    computation. The threshold `< 2 * _HALF_WINDOW + 1` must be strict: a
    5-sample trace has interior = slice(2, 3), which yields exactly one
    computed value at index 2. Mutating < to <= would silently return all
    zeros instead."""
    fs_hz = 500.0
    # Linear ramp: 10 deg/s in x
    t = np.arange(5) / fs_hz
    gaze = np.column_stack([10.0 * t, np.zeros(5)])

    result = velocity(gaze, fs_hz)

    # Indices 0, 1, 3, 4 should be zero (no window)
    assert result[0] == pytest.approx([0.0, 0.0])
    assert result[1] == pytest.approx([0.0, 0.0])
    assert result[3] == pytest.approx([0.0, 0.0])
    assert result[4] == pytest.approx([0.0, 0.0])
    # Index 2 should be the computed value: 10 deg/s
    assert result[2, 0] == pytest.approx(10.0, abs=1e-9)
    assert result[2, 1] == pytest.approx(0.0, abs=1e-12)


def test_non_linear_input_verifies_the_formula():
    """The five-point estimator must be validated on non-linear input. A linear
    ramp has constant slope everywhere, so multiple formulas (e.g., a scaled
    two-point difference) could pass that test while being wrong on curved
    traces. This test uses cubic-in-x input (which has non-zero third derivative)
    and verifies the explicit Engbert & Kliegl formula:
    (x[n+2] + x[n+1] - x[n-1] - x[n-2]) / (6 * dt). The cubic is essential to
    discriminate this formula from wrong alternatives that happen to agree on
    lower-order polynomials.
    """
    fs_hz = 1000.0
    dt = 1.0 / fs_hz
    # Cubic: x(t) = t^3, y(t) = 0. Derivative is 3*t^2, so velocity is 3*t^2 deg/s.
    t = np.arange(20) / fs_hz
    gaze = np.column_stack([t**3, np.zeros(20)])

    result = velocity(gaze, fs_hz)

    # Compute expected velocity independently using the formula
    # velocity = (gaze[n+2] + gaze[n+1] - gaze[n-1] - gaze[n-2]) * (fs_hz / 6)
    expected = np.zeros((20, 2))
    for n in range(2, 18):  # Interior samples where the window exists
        x_diff = gaze[n + 2, 0] + gaze[n + 1, 0] - gaze[n - 1, 0] - gaze[n - 2, 0]
        expected[n, 0] = x_diff * (fs_hz / 6.0)

    # Compare interior samples only
    assert result[2:18, 0] == pytest.approx(expected[2:18, 0], abs=1e-9)
    assert result[2:18, 1] == pytest.approx(expected[2:18, 1], abs=1e-9)
