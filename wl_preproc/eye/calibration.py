"""Raw Purkinje geometry to degrees of visual angle.

**The feature is P1 - P4.** Both Purkinje images move together under
translation of the eye or camera; their difference cancels it and isolates
rotation. Measured on the reference recording: `corr(P1, P4)` is +0.923 in x
and +0.682 in y -- the shared translational component the difference removes.

**The map is a ladder, not one shape.** Second-order first, affine where the
geometry cannot constrain twelve parameters. OpenIrisDPI's own authors state
the P1-P4 nonlinearity is real -- "caused by the curvature in the lens and
cornea" -- and that "much of this non-linear mapping can be accounted for by
including a second-order polynomial term"; their tutorial notebook's own
first-order model is identical to this project's previous affine, so the
upgrade is three additional basis columns and not a different formulation.
See the second-order design spec's section 0.

**Degenerate geometry falls through a validated chain, not a bare refusal.**
Section 3.5's asymmetry -- one point cannot fit six parameters but is entirely
adequate to test one -- is why `resolve_calibration` exists: the calibration
in use online during acquisition and one carried forward from another session
both get a real chance, but only if they explain THIS session's own fixation,
not merely by being offered.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np


class CalibrationModel(StrEnum):
    """What shape a calibration is -- a different question from whose it is,
    which is `CalibrationSource`'s. Conflating the two is the available
    mistake here: the affine tier is this session's OWN map in a simpler
    shape, not a fifth source."""

    AFFINE = "affine"
    SECOND_ORDER = "second_order"


def basis(points: np.ndarray, model: CalibrationModel) -> np.ndarray:
    """The design matrix for `model`, one row per point.

    `[1, dx, dy]` and `[1, dx, dy, dx**2, dy**2, dx*dy]` -- OpenIrisDPI's own
    tutorial notebook uses exactly these, and its first-order case is
    identical to this project's previous affine. See the design spec's
    section 0.

    Named `points` rather than `raw_xy` because it is called on both: on the
    raw Purkinje vector to build the matrix least squares inverts, and on the
    TARGET positions to measure whether the constellation constrains the
    model at all (`_conditioning`, below).
    """
    dx, dy = points[:, 0], points[:, 1]
    cols = [np.ones(len(points)), dx, dy]
    if model is CalibrationModel.SECOND_ORDER:
        cols += [dx * dx, dy * dy, dx * dy]
    return np.column_stack(cols)


def n_terms(model: CalibrationModel) -> int:
    """How many coefficients `model` has per axis."""
    return 6 if model is CalibrationModel.SECOND_ORDER else 3


# Derived from `n_terms`, not restated: k unknowns need k equations, and each
# target position supplies exactly one per axis. Writing the counts out as
# literals would put the same fact in two places and let them drift.
_MIN_POINTS = {model: n_terms(model) for model in CalibrationModel}

# Measured on real constellations with this module's own `_conditioning`
# (see tests/eye/test_calibration_fit.py, which re-measures each):
#
#   constellation      affine   second_order
#   3x3 grid           1.0000   0.2277
#   ring of 8          1.0000   0.0000
#   ring, off-origin   1.0000   0.0000
#   plus, 5 points     1.0000   0.2361
#   4 spread           0.8646   0.2893
#   collinear          0.0000   0.0000
#   one target only    0.0000   0.0000
#
# Good geometry scores 0.23-0.29 on the quadratic basis and 0.86-1.00 on the
# affine one; degenerate geometry scores 0.0000 on both. These thresholds sit
# with margin either side. Per model because 0.05 was measured against a
# 3-point affine and means something different on a six-term basis.
MIN_CONDITIONING = {
    CalibrationModel.AFFINE: 0.05,
    CalibrationModel.SECOND_ORDER: 0.10,
}


class DegenerateGeometry(ValueError):
    """The target positions cannot constrain this model's parameters."""


@dataclass(frozen=True, slots=True)
class CalibrationMap:
    """One coefficient tuple per gaze axis, in `basis(_, model)` column order.

    **Two tuples, not one flat one, deliberately.** The eye plan's review
    demonstrated that a *consistent* transposition between fit and apply
    passes a round-trip test while violating the documented parameter order.
    Applying is `column_stack([basis @ x, basis @ y])`: there is no reshape,
    so there is no axis to transpose. A structural fix beats a test someone
    has to remember to write.

    `n_points` and `conditioning` describe how THIS package fit the
    coefficients -- they default to `0` and `nan` because a map borrowed from
    elsewhere (the `online` and carried-forward sources) was never fit by
    `fit_map` here and has no such history to report; fabricating a point
    count or a conditioning score for it would claim evidence that does not
    exist.
    """

    model: CalibrationModel
    x: tuple[float, ...]
    y: tuple[float, ...]
    n_points: int = 0
    conditioning: float = float("nan")

    def __post_init__(self) -> None:
        """A tuple whose length does not match its own model is a
        mis-assembled map, and this is the one place it can be caught for
        free. The type exists to make a reshape unnecessary; this is what
        stops someone reintroducing one by packing twelve numbers into a
        map labelled `affine`."""
        expected = n_terms(self.model)
        if len(self.x) != expected or len(self.y) != expected:
            raise ValueError(
                f"{self.model.value} takes {expected} coefficients per axis, "
                f"got {len(self.x)} for x and {len(self.y)} for y"
            )


def _conditioning(points: np.ndarray, model: CalibrationModel) -> float:
    """How well a constellation constrains `model`: the smallest over largest
    singular value of its MEAN-CENTRED, COLUMN-NORMALISED basis expansion.

    Three properties, each load-bearing and each measured.

    **Model-specific**, via the basis expansion. Eight targets on a ring
    constrain an affine perfectly (1.0000) and a quadratic not at all
    (0.0000), since points on a circle satisfy `x**2 + y**2 = r**2` and the
    two quadratic columns collapse onto the constant one. The
    bare-positions measure this replaces scored that ring 1.0000 and would
    have passed a minimum-norm quadratic straight through.

    **Translation-invariant**, via the centring -- which the measure this
    replaces also had, and which is not optional once a quadratic basis is
    involved. Far from the origin `t**2` is approximately `c**2 + 2*c*t`, so
    the square columns become near-linear combinations of the constant and
    linear ones and an ordinary constellation reads as degenerate for no
    reason but where the screen origin sits. Measured: a 3x3 grid whose
    targets span 4 degrees centred 2.3 degrees off-axis scores 0.0404
    uncentred and 0.1966 centred, against a 0.10 threshold -- a false refusal
    of geometry that constrains the model perfectly well. Centring costs
    nothing in detection: a ring off the origin, and one sampled unevenly so
    its centroid is not even the circle's centre, both still score exactly
    0.0000, because a conic stays a conic under translation.

    **Scale-invariant**, via the column normalisation. The raw columns run
    `1`, `~100`, `~10,000`; unnormalised, a perfect 3x3 grid scores 4.95e-05
    and units dominate the measure entirely. The fit itself runs on the
    unscaled, uncentred design matrix; only this diagnostic is transformed.

    **Callers pass the TARGET constellation, not the raw signal** -- see
    `fit_map`. A well-spread raw cloud from a single target location is
    noise, not information: measured, a raw cloud straddling the sensor
    origin from one fixation location scores 0.9838 here while least squares
    returns all-zero coefficients, a "calibration" mapping every gaze sample
    in the session onto one point. The same case measured on the targets
    scores exactly 0.0000, because identical target rows leave nothing at all
    after centring.
    """
    design = basis(points - points.mean(axis=0), model)
    norms = np.linalg.norm(design, axis=0)
    norms[norms == 0] = 1.0
    singular = np.linalg.svd(design / norms, compute_uv=False)
    return 0.0 if singular[0] <= 0 else float(singular[-1] / singular[0])


def fit_map(
    raw_xy: np.ndarray, target_xy: np.ndarray, model: CalibrationModel
) -> CalibrationMap:
    """Least squares, after refusing geometry that cannot constrain `model`.

    **Point count is checked before conditioning, because conditioning
    cannot detect under-determination at all.** Four spread targets against a
    six-term basis give a 4x6 design whose SVD returns four singular values;
    their ratio is structurally blind to the two missing dimensions and reads
    a healthy 0.2787. Only the count catches that case.
    """
    if raw_xy.shape[0] != target_xy.shape[0]:
        raise ValueError(
            f"{raw_xy.shape[0]} raw points against {target_xy.shape[0]} target "
            "positions; they are paired per fixation window and a mismatch "
            "means the pairing was lost, not that one of them is short"
        )

    if raw_xy.shape[0] < _MIN_POINTS[model]:
        raise DegenerateGeometry(
            f"{raw_xy.shape[0]} points; the {model.value} model needs at least "
            f"{_MIN_POINTS[model]} target positions"
        )

    conditioning = _conditioning(target_xy, model)
    if conditioning < MIN_CONDITIONING[model]:
        # Kept under ~215 characters deliberately. `schema/eye.py` stores
        # this in a `varchar(255)` and appends both "; no fallback map
        # validated" and its own coverage note; at 261 characters -- measured
        # -- `_bounded_reason` cut the "no fallback" half off entirely, which
        # is the half a report reader needs most.
        raise DegenerateGeometry(
            f"target spread {conditioning:.4f} is below "
            f"{MIN_CONDITIONING[model]} for the {model.value} model: "
            "collinear, coincident or conic targets leave the fit "
            "underdetermined, and least squares would return a minimum-norm "
            "solution that looks like a calibration"
        )

    design = basis(raw_xy, model)
    solution, *_ = np.linalg.lstsq(design, target_xy, rcond=None)
    return CalibrationMap(
        model=model,
        x=tuple(float(value) for value in solution[:, 0]),
        y=tuple(float(value) for value in solution[:, 1]),
        n_points=int(raw_xy.shape[0]),
        conditioning=conditioning,
    )


def apply_map(map_: CalibrationMap, raw_xy: np.ndarray) -> np.ndarray:
    """Degrees of visual angle for each raw (dx, dy)."""
    design = basis(raw_xy, map_.model)
    return np.column_stack([design @ np.array(map_.x), design @ np.array(map_.y)])

# How far a candidate map may place the session's own fixation before it is
# rejected. Generous relative to a good calibration's residual (well under a
# degree) and far below the error a wrong-input-space map produces: ~56,568
# degrees for the volts-fed-as-pixels case in
# `test_a_map_from_the_wrong_input_space_fails_validation_enormously` -- two
# to three orders of magnitude past this threshold, not merely "hundreds".
MAX_VALIDATION_ERROR_DEG = 3.0


class CalibrationSource(StrEnum):
    """WHOSE map this is -- a different question from what shape it is,
    which is `CalibrationModel`'s.

    `ONLINE` is named for its ROLE, not for a vendor: it is the calibration
    that was in use during acquisition, as opposed to our offline fit -- the
    map the animal was actually held to, which is why it outranks
    carry-forward. The behavioural control system will change, and whatever
    replaces MonkeyLogic will also save a calibration. Format-specific things
    keep format names (`eye/bhv2.py` reads a genuinely MonkeyLogic binary and
    says so); role-specific things get role names.

    **Both fitted rungs are `FITTED`.** An affine-tier fit is still this
    session's own map from this session's own targets, in a simpler shape.
    Making it a separate source would answer "whose map" with a statement
    about geometry, and the two questions would stop being separable.
    """

    FITTED = "fitted"
    ONLINE = "online"
    CARRIED_FORWARD = "carried_forward"
    REFUSED = "refused"


def validate_map(map_: CalibrationMap, raw_xy: np.ndarray, target_xy: np.ndarray) -> float:
    """RMS error in degrees when `map_` is applied to this session's own points.

    **One point cannot fit six parameters but is entirely adequate to test
    them.** That asymmetry is what makes borrowing a calibration safe rather
    than blind, and it is why a session too degenerate to fit is not
    automatically a session with no gaze.
    """
    predicted = apply_map(map_, raw_xy)
    return float(np.sqrt(np.mean(np.sum((predicted - target_xy) ** 2, axis=1))))


@dataclass(frozen=True, slots=True)
class Calibration:
    """What this session's gaze will be computed from, or the fact that
    nothing validated.

    `carried_from` names the source session only for
    `CalibrationSource.CARRIED_FORWARD` -- every other source's map either
    belongs to this session (`FITTED`, `ONLINE`) or does not exist
    (`REFUSED`) -- so a borrowed map is never mistaken for a fitted one
    (design spec section 3.5).
    """

    source: CalibrationSource
    map_: CalibrationMap | None
    validation_error_deg: float | None
    reason: str
    carried_from: str | None = None


def read_online_map(bhv2_path: str | Path | None) -> CalibrationMap | None:
    """The fallback chain's step-3 candidate: the map in use ONLINE during
    acquisition.

    Named for the role, not the vendor -- but today that role has exactly one
    reader, MonkeyLogic's `.bhv2`, and this function is where the two meet.
    A second control system adds a reader beside `bhv2.py` and a branch here;
    it touches neither `resolve_calibration`, the schema, nor the report.

    Read from `.bhv2` (design spec section 4.5) and converted at Task 6's own
    boundary, `as_calibration_map` -- not reassembled here from `Bhv2Calibration`'s
    fields (Controller ruling C, task-7 brief). Task 6 owns what counts as a
    usable six-number calibration, including the fact that a real Origin &
    Gain calibration is probably far more than six numbers and gets declined
    there rather than mis-assembled here.

    `read_calibration` has three outcomes (Controller ruling D), which
    collapse to two here. Absent (`Bhv2Calibration(present=False, a=None,
    ...)`) carries `a=None`, and `as_calibration_map` turns that into `None` -- an
    ordinary nothing-to-offer, ranked no differently from a candidate that
    was never offered. Present-but-no-usable-calibration is NOT the same
    state, even though it collapses to the same result: `bhv2.py` computes
    `present = a is not None`, so this case is `present=True` with a
    non-six-element `a` (Raw Signal's own two numbers, say) -- `as_calibration_map`
    declines it on its length check, not on `a=None`. Both still reach `None`
    from this function, which is the only fact this module needs. Unparseable
    is different in kind: it raises `Bhv2Unreadable`. A corrupt `.bhv2` is a
    fact about MonkeyLogic's own recording, not about whether this session
    still has a carried-forward calibration to fall back on, so it is caught
    here rather than left to deny step 3 -- design spec section 4.5: "a
    missing or unreadable `.bhv2` is not an error."

    The import below is local, not at module scope: `wl_preproc.eye.bhv2`
    imports `CalibrationMap` from this module (its own module docstring), and a
    top-level import here would close the cycle. Verified directly against
    this pair of files: a module-level version of this same import raises
    `ImportError: cannot import name ... from partially initialized module`
    regardless of which of the two a caller happens to import first, because
    whichever module starts the cycle is still sitting at its own top-level
    import statement -- before either `CalibrationMap` or these three bhv2 names
    are defined -- when the other module reaches back for it. A deferred
    import, run only once this function is actually called, sidesteps that:
    by then neither module is mid-load.
    """
    if bhv2_path is None:
        return None

    from wl_preproc.eye.bhv2 import Bhv2Unreadable, as_calibration_map, read_calibration

    try:
        cal = read_calibration(bhv2_path)
    except Bhv2Unreadable:
        return None
    return as_calibration_map(cal)


def resolve_calibration(
    raw_xy: np.ndarray,
    target_xy: np.ndarray,
    online: CalibrationMap | None,
    carried: tuple[CalibrationMap, str] | None,
) -> Calibration:
    """Design spec section 3.5's four steps, in order.

    **Every candidate is validated against this session's own points before
    being accepted**, including a map that came from MonkeyLogic and one
    carried forward from another session. That is what makes borrowing safe:
    one point cannot fit six parameters but is entirely adequate to falsify a
    candidate.

    `online` arrives already resolved to `CalibrationMap | None` --
    ordinarily via `read_online_map` -- rather than as a `.bhv2` path, so the
    vendor boundary lives in `bhv2.py` alone: this function never touches the
    filesystem, and supporting a second control system does not reach it.
    """
    if raw_xy.shape[0] == 0:
        return Calibration(
            CalibrationSource.REFUSED, None, None,
            "no fixation epoch named a target position",
        )

    # **The model ladder.** Second-order first, affine where the geometry
    # cannot constrain twelve parameters -- both this session's own map from
    # its own targets, so both `FITTED`. More sessions will land on the
    # affine rung than currently fit at all, since twelve parameters is a
    # harder bar than six; that is the ladder working, and
    # `calibration_model` is what tells an operator which rung a session
    # reached.
    #
    # `degenerate_reason` ends holding the AFFINE failure, deliberately: the
    # chain only reaches a refusal when the lower rung failed too, so the
    # affine message is the binding constraint. It names its own model
    # ("...for the affine model"), so a reader can see the ladder descended
    # without both messages being stored.
    for model in (CalibrationModel.SECOND_ORDER, CalibrationModel.AFFINE):
        try:
            fitted = fit_map(raw_xy, target_xy, model)
        except DegenerateGeometry as exc:
            degenerate_reason = str(exc)
        else:
            return Calibration(
                CalibrationSource.FITTED, fitted,
                validate_map(fitted, raw_xy, target_xy), "",
            )

    # The online map precedes carry-forward: it comes from the SAME session
    # and is the map the animal was actually held to, since a gaze-contingent
    # task cannot define a fixation window without one.
    for source, candidate, origin in (
        (CalibrationSource.ONLINE, online, None),
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
