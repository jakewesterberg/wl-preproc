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


def test_too_few_points_is_refused_before_conditioning_is_computed():
    raw = np.array([[0.0, 0.0], [1.0, 1.0]])
    with pytest.raises(DegenerateGeometry, match="at least"):
        fit_affine(raw, np.zeros((2, 2)))
