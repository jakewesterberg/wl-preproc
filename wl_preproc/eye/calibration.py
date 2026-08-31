"""Raw Purkinje geometry to degrees of visual angle.

**The feature is P1 - P4.** Both Purkinje images move together under
translation of the eye or camera; their difference cancels it and isolates
rotation. Measured on the reference recording: `corr(P1, P4)` is +0.923 in x
and +0.682 in y -- the shared translational component the difference removes.

**The map is affine, six parameters per eye.** Not scale-plus-offset, because
the camera is never perfectly aligned to the eye's axes and the cross-terms are
real. Not a polynomial, because parent design spec section 7.2 makes gaze
canonical and computed once, and puts revisability in detection instead.

**Degenerate geometry falls through a validated chain, not a bare refusal.**
Section 3.5's asymmetry -- one point cannot fit six parameters but is entirely
adequate to test one -- is why `resolve_calibration` exists: MonkeyLogic's own
calibration and one carried forward from another session both get a real
chance, but only if they explain THIS session's own fixation, not merely by
being offered.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

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


# How far a candidate map may place the session's own fixation before it is
# rejected. Generous relative to a good calibration's residual (well under a
# degree) and far below the error a wrong-input-space map produces: ~56,568
# degrees for the volts-fed-as-pixels case in
# `test_a_map_from_the_wrong_input_space_fails_validation_enormously` -- two
# to three orders of magnitude past this threshold, not merely "hundreds".
MAX_VALIDATION_ERROR_DEG = 3.0


class CalibrationSource(StrEnum):
    FITTED = "fitted"
    MONKEYLOGIC = "monkeylogic"
    CARRIED_FORWARD = "carried_forward"
    REFUSED = "refused"


def validate_map(map_: AffineMap, raw_xy: np.ndarray, target_xy: np.ndarray) -> float:
    """RMS error in degrees when `map_` is applied to this session's own points.

    **One point cannot fit six parameters but is entirely adequate to test
    them.** That asymmetry is what makes borrowing a calibration safe rather
    than blind, and it is why a session too degenerate to fit is not
    automatically a session with no gaze.
    """
    predicted = apply_affine(map_, raw_xy)
    return float(np.sqrt(np.mean(np.sum((predicted - target_xy) ** 2, axis=1))))


@dataclass(frozen=True, slots=True)
class Calibration:
    """What this session's gaze will be computed from, or the fact that
    nothing validated.

    `carried_from` names the source session only for
    `CalibrationSource.CARRIED_FORWARD` -- every other source's map either
    belongs to this session (`FITTED`, `MONKEYLOGIC`) or does not exist
    (`REFUSED`) -- so a borrowed map is never mistaken for a fitted one
    (design spec section 3.5).
    """

    source: CalibrationSource
    map_: AffineMap | None
    validation_error_deg: float | None
    reason: str
    carried_from: str | None = None


def read_monkeylogic_map(bhv2_path: str | Path | None) -> AffineMap | None:
    """The fallback chain's step-2 candidate: MonkeyLogic's own calibration,
    read from `.bhv2` (design spec section 4.5) and converted at Task 6's own
    boundary, `as_affine_map` -- not reassembled here from `Bhv2Calibration`'s
    fields (Controller ruling C, task-7 brief). Task 6 owns what counts as a
    usable six-number calibration, including the fact that a real Origin &
    Gain calibration is probably far more than six numbers and gets declined
    there rather than mis-assembled here.

    `read_calibration` has three outcomes (Controller ruling D), which
    collapse to two here. Absent (`Bhv2Calibration(present=False, a=None,
    ...)`) carries `a=None`, and `as_affine_map` turns that into `None` -- an
    ordinary nothing-to-offer, ranked no differently from a candidate that
    was never offered. Present-but-no-usable-calibration is NOT the same
    state, even though it collapses to the same result: `bhv2.py` computes
    `present = a is not None`, so this case is `present=True` with a
    non-six-element `a` (Raw Signal's own two numbers, say) -- `as_affine_map`
    declines it on its length check, not on `a=None`. Both still reach `None`
    from this function, which is the only fact this module needs. Unparseable
    is different in kind: it raises `Bhv2Unreadable`. A corrupt `.bhv2` is a
    fact about MonkeyLogic's own recording, not about whether this session
    still has a carried-forward calibration to fall back on, so it is caught
    here rather than left to deny step 3 -- design spec section 4.5: "a
    missing or unreadable `.bhv2` is not an error."

    The import below is local, not at module scope: `wl_preproc.eye.bhv2`
    imports `AffineMap` from this module (its own module docstring), and a
    top-level import here would close the cycle. Verified directly against
    this pair of files: a module-level version of this same import raises
    `ImportError: cannot import name ... from partially initialized module`
    regardless of which of the two a caller happens to import first, because
    whichever module starts the cycle is still sitting at its own top-level
    import statement -- before either `AffineMap` or these three bhv2 names
    are defined -- when the other module reaches back for it. A deferred
    import, run only once this function is actually called, sidesteps that:
    by then neither module is mid-load.
    """
    if bhv2_path is None:
        return None

    from wl_preproc.eye.bhv2 import Bhv2Unreadable, as_affine_map, read_calibration

    try:
        cal = read_calibration(bhv2_path)
    except Bhv2Unreadable:
        return None
    return as_affine_map(cal)


def resolve_calibration(
    raw_xy: np.ndarray,
    target_xy: np.ndarray,
    monkeylogic: AffineMap | None,
    carried: tuple[AffineMap, str] | None,
) -> Calibration:
    """Design spec section 3.5's four steps, in order.

    **Every candidate is validated against this session's own points before
    being accepted**, including a map that came from MonkeyLogic and one
    carried forward from another session. That is what makes borrowing safe:
    one point cannot fit six parameters but is entirely adequate to falsify a
    candidate.

    `monkeylogic` arrives already resolved to `AffineMap | None` -- ordinarily
    via `read_monkeylogic_map` -- rather than as a `.bhv2` path, so this
    function never touches the filesystem itself and stays testable against
    hand-built candidates alone.
    """
    if raw_xy.shape[0] == 0:
        return Calibration(
            CalibrationSource.REFUSED, None, None,
            "no fixation epoch named a target position",
        )

    try:
        fitted = fit_affine(raw_xy, target_xy)
    except DegenerateGeometry as exc:
        degenerate_reason = str(exc)
    else:
        return Calibration(
            CalibrationSource.FITTED, fitted,
            validate_map(fitted, raw_xy, target_xy), "",
        )

    # MonkeyLogic's precedes carry-forward: it comes from the SAME session and
    # is the map the animal was actually held to, since a gaze-contingent task
    # cannot define a fixation window without one.
    for source, candidate, origin in (
        (CalibrationSource.MONKEYLOGIC, monkeylogic, None),
        (CalibrationSource.CARRIED_FORWARD,
         carried[0] if carried else None,
         carried[1] if carried else None),
    ):
        if candidate is None:
            continue
        error = validate_map(candidate, raw_xy, target_xy)
        if error <= MAX_VALIDATION_ERROR_DEG:
            return Calibration(source, candidate, error, "", origin)

    return Calibration(
        CalibrationSource.REFUSED, None, None,
        f"{degenerate_reason}; no fallback map validated",
    )
