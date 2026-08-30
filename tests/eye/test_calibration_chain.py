import numpy as np
import pytest

from wl_preproc.eye.calibration import (
    AffineMap,
    Calibration,
    CalibrationSource,
    MAX_VALIDATION_ERROR_DEG,
    apply_affine,
    read_monkeylogic_map,
    resolve_calibration,
    validate_map,
)

GOOD = AffineMap(a=(0.05, 0.0, 0.0, 0.0, 0.05, 0.0), n_points=4, conditioning=0.9)

# A single central fixation, repeated with jitter -- Task 5's own degenerate
# fixture (test_calibration_fit.py's test_a_single_target_location_is_refused).
# fit_affine refuses this (conditioning 0, targets coincident at the origin),
# so every resolve_calibration test below that wants step 1 to fail reuses it.
_DEGENERATE_RAW = np.array([[10.0, 10.0], [10.4, 9.6], [9.7, 10.2], [10.1, 10.1]])
_DEGENERATE_TARGET = np.zeros((4, 2))

# A map that lands the degenerate fixation's raw cluster exactly on its
# target (all-zero coefficients: every input maps to (0, 0)). Used where a
# fallback candidate must validate with a DIFFERENT, LOWER error than GOOD's
# (~0.71 degrees against the fixture above), so a precedence test cannot pass
# by accident if the implementation were to pick the lowest-error candidate
# instead of the first one in chain order.
_ZERO_MAP = AffineMap(a=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0), n_points=6, conditioning=0.7)


def test_one_point_cannot_fit_a_map_but_can_falsify_one():
    """The asymmetry the whole chain rests on (design spec section 3.5).

    A single central fixation cannot constrain six parameters -- Task 5 refuses
    it. It is entirely adequate to TEST a candidate map: apply it and see where
    the target lands.
    """
    raw = np.array([[0.0, 0.0]])
    target = np.array([[0.0, 0.0]])

    assert validate_map(GOOD, raw, target) == pytest.approx(0.0, abs=1e-9)


def test_a_map_from_the_wrong_input_space_fails_validation_enormously():
    """MonkeyLogic's map is in whatever space MonkeyLogic receives -- pixels
    over the OpenIrisDPI UDP path, volts over ACCESIO, and both may be in use.
    We do not need to know which: a volts-to-degrees map fed pixel differences
    misses by an enormous margin and the chain falls through.
    """
    volts_map = AffineMap(a=(4000.0, 0.0, 0.0, 0.0, 4000.0, 0.0), n_points=4, conditioning=0.9)
    raw = np.array([[10.0, 10.0]])
    target = np.array([[0.5, 0.5]])

    assert validate_map(volts_map, raw, target) > MAX_VALIDATION_ERROR_DEG


def test_a_drifted_map_is_rejected_by_the_same_check():
    drifted = AffineMap(a=(0.05, 0.0, 8.0, 0.0, 0.05, 0.0), n_points=4, conditioning=0.9)
    raw = np.array([[0.0, 0.0]])
    target = np.array([[0.0, 0.0]])

    assert validate_map(drifted, raw, target) > MAX_VALIDATION_ERROR_DEG


def test_validate_map_computes_the_rms_error_in_degrees():
    """A concrete, hand-computed expected value -- not just "near zero" or
    "far above threshold" like the other validate_map tests -- so a formula
    bug (mean instead of RMS, or per-axis instead of per-point) cannot hide
    behind those two qualitative buckets.

    Identity map: predicted == raw. Point 0 misses its target by (3, 4), a
    3-4-5 right triangle -- squared distance 25. Point 1 lands exactly on
    target -- squared distance 0. Mean of {25, 0} is 12.5; RMS error is
    sqrt(12.5).
    """
    identity = AffineMap(a=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0))
    raw = np.array([[3.0, 4.0], [0.0, 0.0]])
    target = np.zeros((2, 2))

    assert validate_map(identity, raw, target) == pytest.approx(12.5 ** 0.5)


def test_the_source_enum_names_all_four_steps():
    assert [s.value for s in CalibrationSource] == [
        "fitted", "monkeylogic", "carried_forward", "refused"
    ]


def test_apply_affine_matches_the_documented_parameter_order_for_an_asymmetric_map():
    """Controller ruling A (task-7 brief, carried from Task 5's review):
    test_it_recovers_a_known_affine is a fit-then-apply round trip, which only
    proves fit_affine and apply_affine agree with EACH OTHER -- a consistent
    a01/a10 transposition in both would still round-trip clean.

    This task makes that gap dangerous: resolve_calibration constructs
    AffineMap directly from MonkeyLogic and carried-forward sources, so
    apply_affine's contract with those maps rests on AffineMap.a's documented
    order -- (a00, a01, b0, a10, a11, b1) -- alone. Build a map DIRECTLY (not
    via fit_affine) with deliberately asymmetric a01/a10 so a transposition
    would change the result, and check against an independently assembled
    matrix multiply, not apply_affine's own element-wise formula.
    """
    a00, a01, b0, a10, a11, b1 = 2.0, 3.0, 5.0, 7.0, 11.0, 13.0
    map_ = AffineMap(a=(a00, a01, b0, a10, a11, b1), n_points=4, conditioning=0.8)
    raw = np.array([[1.0, 0.0], [0.0, 1.0], [2.0, -3.0], [-4.0, 5.0]])

    a_matrix = np.array([[a00, a01], [a10, a11]])
    b_vector = np.array([b0, b1])
    expected = raw @ a_matrix.T + b_vector

    assert apply_affine(map_, raw) == pytest.approx(expected)


def test_affine_map_equality_is_unusable_by_design():
    """Controller ruling B (task-7 brief) says two AffineMaps built with the
    defaults never compare equal, because `conditioning` defaults to nan and
    nan != nan. Checked directly, that is NOT what happens: a dataclass
    default is evaluated once, at class definition, and reused for every
    instance that does not override it, so two such AffineMaps share the
    exact same `float("nan")` OBJECT. list/tuple equality -- which is what
    the dataclass-generated `__eq__` reduces to -- short-circuits
    per-element on `is` before falling back to `==` (a long-standing CPython
    optimisation for sequence types), so that shared identity makes the pair
    compare EQUAL without nan's own "never equal to itself" behaviour ever
    being exercised. Passing a fresh `float("nan")` explicitly to each side
    avoids the shared object and restores the naive nan behaviour.

    The ruling's bottom line survives anyway, just for a sharper reason than
    stated: `==` on AffineMap returns True or False depending on an invisible
    object-identity coincidence in how each side happened to be constructed,
    not on whether two maps are the same calibration. That inconsistency --
    not a reliable "always False" -- is what makes it unusable for "is this
    the same map I already tried", and why resolve_calibration must do that
    on `a` alone, or on identity, as the ruling says.
    """
    same_defaults_a = AffineMap(a=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0))
    same_defaults_b = AffineMap(a=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0))
    assert same_defaults_a == same_defaults_b  # equal only by a shared-default-object coincidence

    fresh_nan_a = AffineMap(a=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0), conditioning=float("nan"))
    fresh_nan_b = AffineMap(a=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0), conditioning=float("nan"))
    assert fresh_nan_a != fresh_nan_b  # same `a`, "same" calibration in every sense that matters -- yet not equal


def test_calibration_fields_are_positional_in_the_documented_order():
    """Pins the field order the brief's Produces line documents: source,
    map_, validation_error_deg, reason, carried_from. resolve_calibration
    constructs Calibration positionally throughout, so a reordering here
    would silently misassign, say, a reason string to carried_from.
    """
    cal = Calibration(CalibrationSource.REFUSED, None, None, "why", "src")

    assert cal.source == CalibrationSource.REFUSED
    assert cal.map_ is None
    assert cal.validation_error_deg is None
    assert cal.reason == "why"
    assert cal.carried_from == "src"


def test_fitted_wins_even_when_fallback_candidates_also_validate():
    """Step 1 of design spec section 3.5's table. Well-conditioned geometry
    means fit_affine succeeds -- and it must be PREFERRED even when a
    validating MonkeyLogic candidate and a validating carried-forward
    candidate are both on offer, not merely returned because it was the only
    option. Pins the table's order, not just "some result came back".
    """
    raw = np.array([[-100.0, -80.0], [100.0, -80.0], [0.0, 90.0], [60.0, 40.0]])
    target = np.column_stack([0.05 * raw[:, 0], 0.05 * raw[:, 1]])

    result = resolve_calibration(raw, target, GOOD, (GOOD, "2026-08-20_subjA"))

    assert result.source == CalibrationSource.FITTED
    assert result.reason == ""
    assert result.carried_from is None
    assert apply_affine(result.map_, raw) == pytest.approx(target, abs=1e-6)


def test_monkeylogic_wins_over_carried_forward_when_both_validate():
    """Step 2 precedes step 3 (design spec section 3.5: "MonkeyLogic's
    precedes carry-forward because it comes from the SAME session"). The
    carried candidate here (_ZERO_MAP) validates with a LOWER error than the
    MonkeyLogic candidate (GOOD) against this fixture, so a bug that selects
    by lowest error rather than chain order would pick carried-forward --
    and this test would catch it.
    """
    result = resolve_calibration(_DEGENERATE_RAW, _DEGENERATE_TARGET, GOOD, (_ZERO_MAP, "2026-08-20_subjA"))

    assert result.source == CalibrationSource.MONKEYLOGIC
    assert result.map_ is GOOD
    assert result.carried_from is None
    assert result.validation_error_deg == pytest.approx(
        validate_map(GOOD, _DEGENERATE_RAW, _DEGENERATE_TARGET)
    )


def test_a_monkeylogic_map_that_fails_validation_falls_through_to_carried_forward():
    """Step 2's OWN selection logic must reject a bad candidate, not merely
    be skipped -- so this uses a monkeylogic map that fails validation for a
    concrete, checkable reason (wrong input space) rather than simply being
    absent, and a distinct, valid carried candidate that step 3 then picks up.
    """
    volts_map = AffineMap(a=(4000.0, 0.0, 0.0, 0.0, 4000.0, 0.0), n_points=4, conditioning=0.9)

    result = resolve_calibration(
        _DEGENERATE_RAW, _DEGENERATE_TARGET, volts_map, (GOOD, "2026-08-19_subjA")
    )

    assert result.source == CalibrationSource.CARRIED_FORWARD
    assert result.map_ is GOOD
    assert result.carried_from == "2026-08-19_subjA"


def test_carried_forward_records_its_origin_session():
    """Step 3's own selection logic: a validating carried map is accepted AND
    its origin session is recorded -- design spec section 3.5's "the source
    session and the time delta are recorded, so a borrowed map is never
    mistaken for a fitted one."
    """
    result = resolve_calibration(_DEGENERATE_RAW, _DEGENERATE_TARGET, None, (GOOD, "2026-08-20_subjA"))

    assert result.source == CalibrationSource.CARRIED_FORWARD
    assert result.map_ is GOOD
    assert result.carried_from == "2026-08-20_subjA"
    assert result.validation_error_deg == pytest.approx(
        validate_map(GOOD, _DEGENERATE_RAW, _DEGENERATE_TARGET)
    )


def test_a_carried_forward_map_that_fails_validation_is_refused():
    """Step 3's OWN selection logic must reject a bad candidate too -- not
    just fall through to step 2 having already failed, but genuinely check
    this candidate and find it wanting.
    """
    drifted = AffineMap(a=(0.05, 0.0, 8.0, 0.0, 0.05, 0.0), n_points=4, conditioning=0.9)

    result = resolve_calibration(_DEGENERATE_RAW, _DEGENERATE_TARGET, None, (drifted, "2026-08-20_subjA"))

    assert result.source == CalibrationSource.REFUSED
    assert result.map_ is None
    assert result.validation_error_deg is None
    assert result.carried_from is None


def test_refused_reason_names_the_degenerate_geometry_when_no_fallback_validates():
    """Step 4: the reason must say WHY, not just that nothing worked -- design
    spec section 3.5's "must never be indistinguishable from one that
    calibrated badly". Checks for both halves of the composed message so a
    mutation that drops either the original DegenerateGeometry text or the
    "no fallback" suffix is caught.
    """
    result = resolve_calibration(_DEGENERATE_RAW, _DEGENERATE_TARGET, None, None)

    assert result.source == CalibrationSource.REFUSED
    assert result.map_ is None
    assert result.validation_error_deg is None
    assert "collinear or coincident" in result.reason
    assert "no fallback map validated" in result.reason


def test_an_empty_fixation_epoch_is_refused_without_naming_a_target():
    """A session with no fixation epoch at all cannot even be TESTED, let
    alone fit -- a distinct diagnostic from "geometry was degenerate", pinned
    with an exact match so a mutation that instead falls through to
    fit_affine's own (different) empty-input message is caught.
    """
    raw = np.zeros((0, 2))
    target = np.zeros((0, 2))

    result = resolve_calibration(raw, target, None, None)

    assert result.source == CalibrationSource.REFUSED
    assert result.map_ is None
    assert result.validation_error_deg is None
    assert result.reason == "no fixation epoch named a target position"


def test_read_monkeylogic_map_is_none_when_there_is_nothing_to_read(tmp_path):
    """Both having no candidate path at all, and a path that does not exist
    (read_calibration's own absence handling, design spec section 4.5), are
    ordinary nothing-to-offer outcomes -- not errors, and not distinguished
    from each other.
    """
    assert read_monkeylogic_map(None) is None
    assert read_monkeylogic_map(tmp_path / "nope.bhv2") is None


def test_read_monkeylogic_map_catches_an_unreadable_file_rather_than_raising(tmp_path):
    """Controller ruling D (task-7 brief): read_calibration's second outcome
    -- a `.bhv2` that exists but cannot be structurally walked -- raises
    Bhv2Unreadable. That must not propagate out of the fallback chain's step
    2: a corrupt log is a fact about MonkeyLogic's own recording, not about
    whether this reader still has something else to offer.

    Same truncated-buffer shape tests/eye/test_bhv2.py's own
    test_a_truncated_file_raises_rather_than_returning_absence uses to prove
    read_calibration raises Bhv2Unreadable for it.
    """
    corrupt = tmp_path / "corrupt.bhv2"
    corrupt.write_bytes(b"\x04\x00\x00\x00test\xff\xff")

    assert read_monkeylogic_map(corrupt) is None


def test_a_corrupt_bhv2_still_lets_the_chain_reach_carried_forward(tmp_path):
    """Controller ruling D's own required test, end to end: "Test that a
    raising `.bhv2` still lets the chain reach step 3." Uses the real
    catching path (read_monkeylogic_map on an actual corrupt file on disk),
    not a pre-resolved None standing in for it -- if the try/except around
    read_calibration were removed, this raises instead of reaching
    CARRIED_FORWARD, and the test fails for that reason, not a weaker one.
    """
    corrupt = tmp_path / "corrupt.bhv2"
    corrupt.write_bytes(b"\x04\x00\x00\x00test\xff\xff")

    monkeylogic = read_monkeylogic_map(corrupt)
    result = resolve_calibration(
        _DEGENERATE_RAW, _DEGENERATE_TARGET, monkeylogic, (GOOD, "2026-08-20_subjA")
    )

    assert result.source == CalibrationSource.CARRIED_FORWARD
    assert result.carried_from == "2026-08-20_subjA"
