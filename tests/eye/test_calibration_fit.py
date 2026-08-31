"""The basis, the per-model guards, and what each one is blind to.

Every conditioning figure quoted below was measured against this file's own
constellations with `.venv/bin/python`, not copied from the design spec: the
spec's own unnormalised figure (4.95e-05 for a 3x3 grid) holds only at a
~100-unit constellation and is 7.92e-03 at a degrees-scale one, and a number
that changes with the scale it was measured at has to be re-measured at the
scale it is cited at.
"""

import numpy as np
import pytest

from wl_preproc.eye.calibration import (
    MIN_CONDITIONING,
    CalibrationMap,
    CalibrationModel,
    DegenerateGeometry,
    _conditioning,
    apply_map,
    basis,
    fit_map,
    n_terms,
)

AFFINE = CalibrationModel.AFFINE
SECOND_ORDER = CalibrationModel.SECOND_ORDER

# Raw Purkinje units (P1 - P4), the scale a real recording's own CR1/CR4
# difference works out to -- not degrees. Conditioning is scale-invariant
# once the columns are normalised (measured: this grid scores 0.2277 on the
# quadratic basis at +-8/+-6 and at +-200/+-150 alike), so the scale matters
# only to the unnormalised figure the normalisation test below pins.
GRID = np.array([[x, y] for x in (-100.0, 0.0, 100.0) for y in (-75.0, 0.0, 75.0)])

_RING_ANGLES = np.arange(8) * 2 * np.pi / 8
RING = np.column_stack([100 * np.cos(_RING_ANGLES), 100 * np.sin(_RING_ANGLES)])

SPREAD = np.array([[-100.0, -75.0], [100.0, -75.0], [0.0, 75.0], [60.0, 40.0]])

# gx = 1.0 + 0.05*dx + 0.002*dy + 1e-5*dx^2 - 2e-6*dy^2 + 3e-6*dx*dy
_X_COEFFS = (1.0, 0.05, 0.002, 1e-5, -2e-6, 3e-6)
# Deliberately NOT a mirror of the x row: an x/y transposition anywhere in
# fit or apply has to change the numbers, which a symmetric map would hide.
_Y_COEFFS = (-0.5, -0.001, 0.06, 4e-6, 1.5e-5, -2e-6)


def _second_order(points: np.ndarray, x_coeffs, y_coeffs) -> np.ndarray:
    """The polynomial written out longhand, independent of `basis`.

    Deliberately not `basis(points, SECOND_ORDER) @ coeffs`: a test that
    builds its expectation from the function under test agrees with it by
    construction, which is the exact defect the ohDPI reader shipped (the
    fixture generator and the reader agreed about a format neither had seen).
    Reordering `basis`'s columns must break the round-trip tests below, and
    it only can if the expectation is spelled out separately.
    """
    dx, dy = points[:, 0], points[:, 1]
    return np.column_stack([
        x_coeffs[0] + x_coeffs[1] * dx + x_coeffs[2] * dy
        + x_coeffs[3] * dx * dx + x_coeffs[4] * dy * dy + x_coeffs[5] * dx * dy,
        y_coeffs[0] + y_coeffs[1] * dx + y_coeffs[2] * dy
        + y_coeffs[3] * dx * dx + y_coeffs[4] * dy * dy + y_coeffs[5] * dx * dy,
    ])


def _affine(points: np.ndarray, x_coeffs, y_coeffs) -> np.ndarray:
    """Likewise for `[1, dx, dy]`, longhand."""
    dx, dy = points[:, 0], points[:, 1]
    return np.column_stack([
        x_coeffs[0] + x_coeffs[1] * dx + x_coeffs[2] * dy,
        y_coeffs[0] + y_coeffs[1] * dx + y_coeffs[2] * dy,
    ])


def test_it_recovers_a_known_second_order_map():
    """Not merely "a map came back": the twelve coefficients themselves.

    Design spec section 6 names why this assertion has to be numerical --
    the previous plan shipped a suite in which gutting the whole
    session-time-to-row alignment left every test green, because nothing
    asserted a fitted map was correct rather than present.
    """
    target = _second_order(GRID, _X_COEFFS, _Y_COEFFS)

    fitted = fit_map(GRID, target, SECOND_ORDER)

    assert fitted.model is SECOND_ORDER
    assert fitted.x == pytest.approx(_X_COEFFS, rel=1e-6, abs=1e-12)
    assert fitted.y == pytest.approx(_Y_COEFFS, rel=1e-6, abs=1e-12)
    assert apply_map(fitted, GRID) == pytest.approx(target, abs=1e-9)
    assert fitted.n_points == 9


def test_it_recovers_a_known_affine_map():
    """The lower rung is the same code path with three fewer basis columns."""
    target = _affine(GRID, _X_COEFFS[:3], _Y_COEFFS[:3])

    fitted = fit_map(GRID, target, AFFINE)

    assert fitted.model is AFFINE
    assert fitted.x == pytest.approx(_X_COEFFS[:3], rel=1e-9)
    assert fitted.y == pytest.approx(_Y_COEFFS[:3], rel=1e-9)
    assert apply_map(fitted, GRID) == pytest.approx(target, abs=1e-9)


def test_a_ring_of_eight_is_refused_for_second_order():
    """THE test of this spec (design spec section 6: "if one test survives
    from this spec, it is this one").

    Eight targets on a circle satisfy `tx^2 + ty^2 = r^2`, so the constant,
    `dx^2` and `dy^2` columns are linearly dependent and the quadratic design
    matrix is rank 5 of 6. Measured here: exactly 0.0000. A ring is an
    ordinary saccade-task geometry, and the conditioning check this replaces
    -- the singular ratio of the mean-centred target positions -- scored it a
    perfect 1.0000 and would have passed a minimum-norm quadratic straight
    through.
    """
    target = _affine(RING, _X_COEFFS[:3], _Y_COEFFS[:3])
    assert _conditioning(basis(target, SECOND_ORDER)) == pytest.approx(0.0, abs=1e-12)

    with pytest.raises(DegenerateGeometry, match="target spread"):
        fit_map(RING, target, SECOND_ORDER)


def test_a_ring_of_eight_still_fits_an_affine():
    """The same eight points constrain six parameters perfectly. This is why
    the affine tier exists rather than the ring simply losing its session."""
    target = _affine(RING, _X_COEFFS[:3], _Y_COEFFS[:3])

    fitted = fit_map(RING, target, AFFINE)

    assert fitted.model is AFFINE
    assert fitted.conditioning > MIN_CONDITIONING[AFFINE]
    assert apply_map(fitted, RING) == pytest.approx(target, abs=1e-9)


def test_four_spread_targets_are_refused_on_point_count_not_conditioning():
    """The case conditioning is structurally blind to.

    Four points against six unknowns is underdetermined outright, but the
    design matrix is 4x6, its SVD returns only four singular values, and
    their ratio cannot see the two missing dimensions: measured 0.2296 here,
    against a 0.10 threshold -- comfortably "healthy". Only the point count
    catches it, which is why it is checked first.
    """
    target = _affine(SPREAD, _X_COEFFS[:3], _Y_COEFFS[:3])
    healthy_looking = _conditioning(basis(target, SECOND_ORDER))
    assert healthy_looking > MIN_CONDITIONING[SECOND_ORDER]

    with pytest.raises(DegenerateGeometry, match="needs at least"):
        fit_map(SPREAD, target, SECOND_ORDER)


def test_four_spread_targets_still_fit_an_affine():
    target = _affine(SPREAD, _X_COEFFS[:3], _Y_COEFFS[:3])

    fitted = fit_map(SPREAD, target, AFFINE)

    assert fitted.model is AFFINE
    assert apply_map(fitted, SPREAD) == pytest.approx(target, abs=1e-9)


def test_a_perfect_grid_passes_because_the_measure_normalises_its_columns():
    """Remove the column normalisation in `_conditioning` and this fails.

    The quadratic basis columns of these targets run `1`, `~5`, `~25`, so
    units dominate the singular ratio. Measured on this grid's own targets:
    1.74e-02 unnormalised against a 0.10 threshold -- refused -- versus
    0.2129 normalised. The unnormalised figure is scale-dependent (the design
    spec quotes 4.95e-05, which reproduces at a ~100-unit constellation;
    these targets are in degrees, so the same grid reads three orders of
    magnitude higher) and the normalised one is not, which is the whole
    reason the diagnostic scales its columns and the fit does not.
    """
    target = _second_order(GRID, _X_COEFFS, _Y_COEFFS)

    design = basis(target, SECOND_ORDER)
    unnormalised = np.linalg.svd(design, compute_uv=False)
    assert unnormalised[-1] / unnormalised[0] < MIN_CONDITIONING[SECOND_ORDER]

    fitted = fit_map(GRID, target, SECOND_ORDER)
    assert fitted.conditioning > MIN_CONDITIONING[SECOND_ORDER]


def test_apply_map_never_transposes_its_two_axes():
    """Pinned though structurally impossible (design spec section 3).

    A map is two tuples, so applying it has no reshape and therefore no axis
    to transpose. The eye plan's own review showed a *consistent*
    transposition between fit and apply passing a round-trip test while
    violating the documented parameter order -- so this asserts against a map
    built DIRECTLY, never fitted, with x and y that cannot be swapped
    unnoticed.
    """
    map_ = CalibrationMap(
        model=SECOND_ORDER,
        x=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
        y=(-7.0, -8.0, -9.0, -10.0, -11.0, -12.0),
    )
    points = np.array([[2.0, 5.0], [-3.0, 1.0]])

    dx, dy = points[:, 0], points[:, 1]
    expected = np.column_stack([
        1.0 + 2.0 * dx + 3.0 * dy + 4.0 * dx * dx + 5.0 * dy * dy + 6.0 * dx * dy,
        -7.0 - 8.0 * dx - 9.0 * dy - 10.0 * dx * dx - 11.0 * dy * dy - 12.0 * dx * dy,
    ])

    assert apply_map(map_, points) == pytest.approx(expected)


def test_a_single_target_location_is_refused_at_both_models():
    """THE load-bearing safety property, and the reason conditioning is
    measured on the TARGETS rather than on the raw design matrix.

    A well-spread raw cloud from one target location is noise, not
    information. Measured directly: for a raw cloud straddling the sensor
    origin, `_conditioning(basis(raw, AFFINE))` is 0.857 -- passing the 0.05
    threshold comfortably -- while least squares returns all-zero
    coefficients, a "calibration" mapping every gaze sample in the session
    onto the single point (0, 0). Measured on the targets the same case
    scores exactly 0.0000, because identical target rows make all three
    normalised basis columns the same column.
    """
    raw = np.array([[-6.0, 4.0], [7.0, -5.0], [-3.0, -8.0], [5.0, 9.0], [1.0, 2.0], [-2.0, 6.0]])
    target = np.zeros((6, 2))

    assert _conditioning(basis(raw, AFFINE)) > MIN_CONDITIONING[AFFINE]

    for model in (SECOND_ORDER, AFFINE):
        with pytest.raises(DegenerateGeometry, match="target spread"):
            fit_map(raw, target, model)


def test_collinear_targets_are_refused_at_both_models():
    """Three points on a line constrain the map along it and nothing across
    it -- underdetermined in exactly the direction a horizontal-only task
    would produce."""
    raw = np.array([[-100.0, 0.0], [0.0, 0.0], [100.0, 0.0], [50.0, 0.0],
                    [-50.0, 0.0], [25.0, 0.0]])
    target = np.array([[-5.0, 0.0], [0.0, 0.0], [5.0, 0.0], [2.5, 0.0],
                       [-2.5, 0.0], [1.25, 0.0]])

    for model in (SECOND_ORDER, AFFINE):
        with pytest.raises(DegenerateGeometry, match="target spread"):
            fit_map(raw, target, model)


def test_too_few_points_is_refused_with_its_own_diagnostic():
    """Each model states its own count, and `match` pins which branch fired.

    The conditioning message says "target spread" and this one says "needs at
    least"; neither phrase appears in the other. That matters here for a
    reason this file learned the hard way: at n=2 conditioning IS computed and
    computes to 0 regardless of the points given (any two rows are collinear
    after centring), so a `match` both branches satisfy would pass whichever
    one actually ran.
    """
    with pytest.raises(DegenerateGeometry, match="the second_order model needs at least 6"):
        fit_map(GRID[:5], _affine(GRID[:5], _X_COEFFS[:3], _Y_COEFFS[:3]), SECOND_ORDER)

    with pytest.raises(DegenerateGeometry, match="the affine model needs at least 3"):
        fit_map(GRID[:2], _affine(GRID[:2], _X_COEFFS[:3], _Y_COEFFS[:3]), AFFINE)


def test_an_empty_array_is_refused_not_crashed():
    """The point-count guard's actual, verified value. `_conditioning` on a
    zero-row design raises `IndexError: index 0 is out of bounds for axis 0
    with size 0` indexing `singular[0]` on an empty SVD -- so without this
    guard running first, a session that recorded no fixations at all would
    reach an operator as a NumPy internals error rather than as a stated
    refusal."""
    for model in (SECOND_ORDER, AFFINE):
        with pytest.raises(DegenerateGeometry, match="needs at least"):
            fit_map(np.zeros((0, 2)), np.zeros((0, 2)), model)


def test_the_basis_is_the_notebook_s_own_two_models():
    """`[1, dx, dy]` and `[1, dx, dy, dx^2, dy^2, dx*dy]`, in that order.

    OpenIrisDPI's own tutorial notebook uses exactly these, and its
    first-order case is identical to this project's previous affine (design
    spec section 0). Column order is a documented interface -- the schema
    names one column per term -- so it is asserted rather than left to the
    round-trip tests, which a consistent reordering would survive.
    """
    points = np.array([[3.0, -4.0]])

    assert basis(points, AFFINE).tolist() == [[1.0, 3.0, -4.0]]
    assert basis(points, SECOND_ORDER).tolist() == [[1.0, 3.0, -4.0, 9.0, 16.0, -12.0]]
    assert n_terms(AFFINE) == 3
    assert n_terms(SECOND_ORDER) == 6


def test_a_map_whose_coefficients_do_not_match_its_model_is_refused():
    """The structural guard behind "no reshape anywhere": a six-term tuple
    labelled `affine` is a mis-assembled map, and the only place that can be
    caught for free is where it is built."""
    with pytest.raises(ValueError, match="affine"):
        CalibrationMap(model=AFFINE, x=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0), y=(1.0, 2.0, 3.0))
