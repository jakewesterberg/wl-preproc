"""True visual angle between gaze positions in per-axis degrees (design spec
`2026-10-30-true-angle-saccades-design.md` section 2): wl-xcon's definitions,
to the formula, so its online detector and eye records match these."""

import math

import numpy as np
import pytest

from wl_preproc.eye.detect import geometry


def _angle(a, b):
    return float(geometry.true_angle_deg(geometry.directions(np.array(a)), geometry.directions(np.array(b))))


@pytest.mark.parametrize(("start", "end", "expected"), [
    ((20.0, 0.0), (20.0, 2.0), 1.879),
    ((30.0, 0.0), (30.0, 2.0), 1.732),
    ((30.0, 0.0), (30.0, 1.12), 0.970),
])
def test_the_numbers_both_sides_checked(start, end, expected):
    """Section 1: a 2-degree step up is 1.879 degrees of true angle from
    (20, 0) and 1.732 from (30, 0); 1.12 up from (30, 0) is under the cut."""
    assert _angle(start, end) == pytest.approx(expected, abs=5e-4)


def test_at_the_centre_a_step_along_an_axis_is_its_own_size():
    assert _angle((0.0, 0.0), (8.0, 0.0)) == pytest.approx(8.0, rel=1e-12)
    assert _angle((0.0, 0.0), (0.0, -8.0)) == pytest.approx(8.0, rel=1e-12)


def test_a_direction_points_ahead_and_is_a_unit_vector():
    d = geometry.directions(np.array([[0.0, 0.0], [30.0, -20.0], [-45.0, 10.0]]))
    np.testing.assert_allclose(np.linalg.norm(d, axis=-1), 1.0, rtol=1e-15)
    assert (d[:, 2] > 0).all()
    np.testing.assert_allclose(d[0], [0.0, 0.0, 1.0])
    # The same direction as normalize(tan x, tan y, 1), the definition.
    tan = np.array([math.tan(math.radians(30.0)), math.tan(math.radians(-20.0)), 1.0])
    np.testing.assert_allclose(d[1], tan / np.linalg.norm(tan), rtol=1e-15)


def test_eccentricity_and_polar_angle():
    """Polar angle counter-clockwise from rightward, in [0, 360): straight
    down is 270, never -90 (amendment 1)."""
    e, theta = geometry.eccentricity_polar_deg(np.array([[10.0, 0.0], [0.0, 10.0], [-10.0, 0.0], [0.0, -10.0]]))
    np.testing.assert_allclose(e, 10.0, rtol=1e-14)
    np.testing.assert_allclose(theta, [0.0, 90.0, 180.0, 270.0], atol=1e-12)
    e, _ = geometry.eccentricity_polar_deg(np.array([[30.0, 30.0]]))
    expected = math.degrees(math.atan(math.hypot(math.tan(math.radians(30.0)), math.tan(math.radians(30.0)))))
    assert float(e[0]) == pytest.approx(expected, rel=1e-14)


def test_the_polar_angle_is_undefined_at_the_centre():
    """'Empty at center': NaN, never 0, which would say rightward."""
    e, theta = geometry.eccentricity_polar_deg(np.array([[0.0, 0.0], [np.nan, 1.0]]))
    assert float(e[0]) == 0.0
    assert math.isnan(float(theta[0]))
    assert math.isnan(float(e[1])) and math.isnan(float(theta[1]))


def test_an_angle_is_wrapped_into_0_to_360():
    """atan2's result mod 360, and 0 where that rounds to 360: -1e-15 mod
    360 is exactly 360.0 in float64. Never -0.0, which a JSON file would
    write as such."""
    wrapped = geometry.wrap_360_deg(np.array([-90.0, -1e-15, -0.0, 0.0, 180.0, -180.0, 359.5]))
    np.testing.assert_array_equal(wrapped, [270.0, 0.0, 0.0, 0.0, 180.0, 180.0, 359.5])
    assert not np.signbit(wrapped).any()


def test_the_local_frame_keeps_distances_from_its_centre_and_points_right_and_up_at_the_centre():
    c = geometry.directions(np.array([25.0, -10.0]))
    for point in ([27.0, -10.0], [25.0, -6.0], [20.0, -14.0]):
        v = geometry.directions(np.array(point))
        lx, ly = geometry.local_coordinates_deg(v, c)
        assert math.hypot(float(lx), float(ly)) == pytest.approx(_angle((25.0, -10.0), point), rel=1e-12)
    centre = geometry.directions(np.array([0.0, 0.0]))
    lx, ly = geometry.local_coordinates_deg(geometry.directions(np.array([3.0, 0.0])), centre)
    assert (float(lx), float(ly)) == pytest.approx((3.0, 0.0), abs=1e-12)
    lx, ly = geometry.local_coordinates_deg(geometry.directions(np.array([0.0, 3.0])), centre)
    assert (float(lx), float(ly)) == pytest.approx((0.0, 3.0), abs=1e-12)


def test_a_direction_about_itself_is_the_origin():
    """To rounding: a unit vector's dot with itself can round below 1."""
    for xy in ([30.0, 12.0], [-7.3, 41.9], [0.0, 0.0]):
        c = geometry.directions(np.array(xy))
        lx, ly = geometry.local_coordinates_deg(c, c)
        assert math.hypot(float(lx), float(ly)) < 1e-12


def test_each_direction_may_have_its_own_centre():
    """Broadcast: rows of `v` about rows of `c`, as one at a time."""
    c = geometry.directions(np.array([[10.0, 5.0], [-20.0, 30.0]]))
    v = geometry.directions(np.array([[11.0, 6.0], [-21.0, 28.0]]))
    lx, ly = geometry.local_coordinates_deg(v, c)
    for i in range(2):
        one = geometry.local_coordinates_deg(v[i], c[i])
        assert (float(lx[i]), float(ly[i])) == (float(one[0]), float(one[1]))


def test_a_step_straight_up_off_centre_points_up_in_the_eyes_frame():
    """The widening's 'true direction in the eye's frame at its start': a
    step up from (30, 0) is 90 degrees, as at the centre."""
    start = geometry.directions(np.array([30.0, 0.0]))
    lx, ly = geometry.local_coordinates_deg(geometry.directions(np.array([30.0, 2.0])), start)
    assert float(geometry.wrap_360_deg(np.degrees(np.arctan2(ly, lx)))) == pytest.approx(90.0, abs=1e-9)
