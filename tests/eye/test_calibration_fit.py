import numpy as np
import pytest

from wl_preproc.eye.calibration import (
    DegenerateGeometry,
    apply_affine,
    fit_affine,
)


def _known_map(raw):
    """gx = 0.05*dx + 0.002*dy + 1.0 ; gy = -0.001*dx + 0.06*dy - 0.5"""
    return np.column_stack([
        0.05 * raw[:, 0] + 0.002 * raw[:, 1] + 1.0,
        -0.001 * raw[:, 0] + 0.06 * raw[:, 1] - 0.5,
    ])


def test_it_recovers_a_known_affine():
    raw = np.array([[-100.0, -80.0], [100.0, -80.0], [0.0, 90.0], [60.0, 40.0]])
    fitted = fit_affine(raw, _known_map(raw))

    assert apply_affine(fitted, raw) == pytest.approx(_known_map(raw), abs=1e-9)


def test_a_single_target_location_is_refused():
    """THE load-bearing safety property. Six parameters need three
    non-collinear points; given one, least squares still returns a minimum-norm
    solution that looks like a calibration and means nothing.

    A session whose only fixation is central must get no map, not a plausible
    one.
    """
    raw = np.array([[10.0, 10.0], [10.4, 9.6], [9.7, 10.2], [10.1, 10.1]])
    target = np.zeros((4, 2))

    with pytest.raises(DegenerateGeometry, match="spread"):
        fit_affine(raw, target)


def test_collinear_targets_are_refused():
    """Three points on a line constrain the map along it and nothing across
    it -- underdetermined in exactly the direction a horizontal-only task
    would produce."""
    raw = np.array([[-100.0, 0.0], [0.0, 0.0], [100.0, 0.0], [50.0, 0.0]])
    target = np.array([[-5.0, 0.0], [0.0, 0.0], [5.0, 0.0], [2.5, 0.0]])

    with pytest.raises(DegenerateGeometry):
        fit_affine(raw, target)


def test_too_few_points_is_refused_with_its_own_diagnostic():
    """NOT "...before conditioning is computed" (this test's original name):
    that framing was untrue. At n=2, conditioning IS computed, and computes to
    exactly 0 regardless of which two points are given -- any 2-point array is
    collinear after mean-centring by construction (a point and its own mirror
    image around their shared mean always lie on one line through the
    origin), so the conditioning branch would independently refuse this input
    too. `match="at least"` used to pass either way, since both raise sites
    contain that phrase ("needs at least 3..." / "...at least one
    direction..."); grepping wl_preproc/eye/calibration.py confirms
    "six-parameter affine needs" appears only in the _MIN_POINTS message, so
    matching it pins which branch actually fired.
    """
    raw = np.array([[0.0, 0.0], [1.0, 1.0]])
    with pytest.raises(DegenerateGeometry, match="six-parameter affine needs"):
        fit_affine(raw, np.zeros((2, 2)))


def test_an_empty_array_is_refused_not_crashed():
    """The _MIN_POINTS guard's actual, verified value. Called `_conditioning`
    directly on a zero-row target: it raises `IndexError: index 0 is out of
    bounds for axis 0 with size 0` indexing `singular[0]` on an empty SVD,
    not a `DegenerateGeometry` -- so without this guard running first,
    `fit_affine` would crash an operator with a NumPy internals error instead
    of stating that a session recorded no fixations at all.
    """
    with pytest.raises(DegenerateGeometry, match="six-parameter affine needs"):
        fit_affine(np.zeros((0, 2)), np.zeros((0, 2)))
