"""Raw Purkinje geometry to degrees of visual angle.

**The feature is P1 - P4.** Both Purkinje images move together under
translation of the eye or camera; their difference cancels it and isolates
rotation. Measured on the reference recording: `corr(P1, P4)` is +0.923 in x
and +0.682 in y -- the shared translational component the difference removes.

**The map is affine, six parameters per eye.** Not scale-plus-offset, because
the camera is never perfectly aligned to the eye's axes and the cross-terms are
real. Not a polynomial, because parent design spec section 7.2 makes gaze
canonical and computed once, and puts revisability in detection instead.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# The smallest ratio of the target constellation's minor to major spread that
# still constrains both axes. A single fixation point gives ~0; a horizontal-only
# task gives ~0; a proper calibration grid gives ~1.
MIN_CONDITIONING = 0.05

_MIN_POINTS = 3


class DegenerateGeometry(ValueError):
    """The target positions cannot constrain six parameters."""


@dataclass(frozen=True, slots=True)
class AffineMap:
    """`[gx, gy] = A @ [dx, dy] + b`, flattened as (a00, a01, b0, a10, a11, b1).

    `n_points` and `conditioning` describe how THIS package fit `a` -- they
    default to `0` and `nan` because a map borrowed from elsewhere (Task 7's
    MonkeyLogic and carried-forward sources) was never fit by `fit_affine`
    here and has no such history to report; fabricating a point count or a
    conditioning score for it would claim evidence that does not exist.
    """

    a: tuple[float, float, float, float, float, float]
    n_points: int = 0
    conditioning: float = float("nan")


def _conditioning(target_xy: np.ndarray) -> float:
    """How well the target constellation spans two dimensions.

    Singular values of the mean-centred positions: their ratio is 1 for a
    circular spread and 0 for points on a line or on top of each other. This is
    a property of the TARGETS, not of the raw signal -- a well-spread raw
    cloud from a single target location is noise, not information.
    """
    centred = target_xy - target_xy.mean(axis=0)
    singular = np.linalg.svd(centred, compute_uv=False)
    if singular[0] <= 0:
        return 0.0
    return float(singular[-1] / singular[0])


def fit_affine(raw_xy: np.ndarray, target_xy: np.ndarray) -> AffineMap:
    """Least squares, after refusing geometry that cannot constrain the fit."""
    if raw_xy.shape[0] < _MIN_POINTS:
        raise DegenerateGeometry(
            f"{raw_xy.shape[0]} points; a six-parameter affine needs at least "
            f"{_MIN_POINTS} non-collinear target positions"
        )

    conditioning = _conditioning(target_xy)
    if conditioning < MIN_CONDITIONING:
        raise DegenerateGeometry(
            f"target spread {conditioning:.4f} is below {MIN_CONDITIONING}: the "
            "targets are collinear or coincident, so a fit would be "
            "underdetermined in at least one direction and least squares would "
            "return a minimum-norm solution that looks like a calibration"
        )

    design = np.column_stack([raw_xy, np.ones(raw_xy.shape[0])])
    solution, *_ = np.linalg.lstsq(design, target_xy, rcond=None)
    return AffineMap(
        a=(
            float(solution[0, 0]), float(solution[1, 0]), float(solution[2, 0]),
            float(solution[0, 1]), float(solution[1, 1]), float(solution[2, 1]),
        ),
        n_points=int(raw_xy.shape[0]),
        conditioning=conditioning,
    )


def apply_affine(map_: AffineMap, raw_xy: np.ndarray) -> np.ndarray:
    """Degrees of visual angle for each raw (dx, dy)."""
    a00, a01, b0, a10, a11, b1 = map_.a
    return np.column_stack([
        a00 * raw_xy[:, 0] + a01 * raw_xy[:, 1] + b0,
        a10 * raw_xy[:, 0] + a11 * raw_xy[:, 1] + b1,
    ])
