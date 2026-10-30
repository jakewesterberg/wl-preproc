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


# -- The local-frame velocity (design spec `2026-10-30-true-angle-saccades-design.md` section 5) --

from wl_preproc.eye.detect import geometry  # noqa: E402
from wl_preproc.eye.detect.velocity import local_velocity  # noqa: E402


def _great_circle(start_xy, toward, speed_deg_s, n, fs_hz):
    """Per-axis gaze of a direction moving along a great circle at a constant
    true speed, toward `toward` (a 3-vector): Rodrigues' rotation of the
    start direction about the axis perpendicular to both, read back as
    per-axis degrees, `(atan(dx/dz), atan(dy/dz))`. The axis must be
    perpendicular to the start direction, or the path is a small circle and
    its speed `speed_deg_s` times the sine of the angle between them."""
    d0 = geometry.directions(np.array(start_xy))
    k = np.cross(d0, np.asarray(toward, dtype=float))
    k = k / np.linalg.norm(k)
    out = []
    for i in range(n):
        a = np.radians(speed_deg_s * i / fs_hz)
        d = d0 * np.cos(a) + np.cross(k, d0) * np.sin(a) + k * (k @ d0) * (1 - np.cos(a))
        out.append([np.degrees(np.arctan(d[0] / d[2])), np.degrees(np.arctan(d[1] / d[2]))])
    return np.array(out)


def test_a_constant_true_speed_reads_as_that_speed_anywhere():
    """Section 11: a sweep at constant true speed along a great circle, off
    centre, reads as that speed; the per-axis velocity does not."""
    fs_hz = 500.0
    gaze = _great_circle((25.0, 15.0), (0.3, 1.0, 0.2), 200.0, 40, fs_hz)
    speed = np.hypot(*local_velocity(gaze, fs_hz)[2:-2].T)
    np.testing.assert_allclose(speed, 200.0, rtol=1e-9)
    per_axis = np.hypot(*velocity(gaze, fs_hz)[2:-2].T)
    assert np.abs(per_axis - 200.0).max() > 2.0


def test_at_the_centre_the_local_velocity_is_the_per_axis_one():
    t = np.arange(30) / 500.0
    gaze = np.column_stack([0.5 * t, -0.3 * t])
    np.testing.assert_allclose(local_velocity(gaze, 500.0), velocity(gaze, 500.0), rtol=1e-6, atol=1e-9)


def test_the_local_velocity_s_edges_are_zero_and_a_short_trace_is_all_zero():
    gaze = _great_circle((10.0, 0.0), (0.0, 1.0, 0.0), 100.0, 12, 500.0)
    v = local_velocity(gaze, 500.0)
    assert (v[:2] == 0).all() and (v[-2:] == 0).all()
    assert (local_velocity(gaze[:4], 500.0) == 0).all()


def test_a_non_finite_position_propagates_into_the_windows_that_read_it():
    gaze = _great_circle((10.0, 0.0), (0.0, 1.0, 0.0), 100.0, 12, 500.0)
    gaze[6] = np.nan
    v = local_velocity(gaze, 500.0)
    assert np.isnan(v[4:9]).all()
    assert np.isfinite(v[2:4]).all() and np.isfinite(v[9:10]).all()
