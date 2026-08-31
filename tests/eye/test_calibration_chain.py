from pathlib import Path

import numpy as np
import pytest

from wl_preproc.eye.calibration import (
    Calibration,
    CalibrationMap,
    CalibrationModel,
    CalibrationSource,
    MAX_VALIDATION_ERROR_DEG,
    apply_map,
    read_online_map,
    resolve_calibration,
    validate_map,
)

_AFFINE = CalibrationModel.AFFINE

GOOD = CalibrationMap(
    model=_AFFINE, x=(0.0, 0.05, 0.0), y=(0.0, 0.0, 0.05), n_points=4, conditioning=0.9
)

# A single central fixation, repeated with jitter -- Task 5's own degenerate
# fixture (test_calibration_fit.py's test_a_single_target_location_is_refused).
# fit_map refuses this (conditioning 0, targets coincident at the origin),
# so every resolve_calibration test below that wants step 1 to fail reuses it.
_DEGENERATE_RAW = np.array([[10.0, 10.0], [10.4, 9.6], [9.7, 10.2], [10.1, 10.1]])
_DEGENERATE_TARGET = np.zeros((4, 2))

# A map that lands the degenerate fixation's raw cluster exactly on its
# target (all-zero coefficients: every input maps to (0, 0)). Used where a
# fallback candidate must validate with a DIFFERENT, LOWER error than GOOD's
# (~0.71 degrees against the fixture above), so a precedence test cannot pass
# by accident if the implementation were to pick the lowest-error candidate
# instead of the first one in chain order.
_ZERO_MAP = CalibrationMap(
    model=_AFFINE, x=(0.0, 0.0, 0.0), y=(0.0, 0.0, 0.0), n_points=6, conditioning=0.7
)


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

    Pins the order of magnitude, not just "exceeds the threshold" --
    `MAX_VALIDATION_ERROR_DEG`'s own comment now cites this case's measured
    error, and only an assertion on the magnitude keeps that comment honest.
    `test_a_drifted_map_is_rejected_by_the_same_check` below also exceeds
    `MAX_VALIDATION_ERROR_DEG` (error 8.0), so `> MAX_VALIDATION_ERROR_DEG`
    alone cannot tell "wrong input space" apart from "merely drifted" --
    the stronger bound below can.
    """
    volts_map = CalibrationMap(
        model=_AFFINE, x=(0.0, 4000.0, 0.0), y=(0.0, 0.0, 4000.0),
        n_points=4, conditioning=0.9,
    )
    raw = np.array([[10.0, 10.0]])
    target = np.array([[0.5, 0.5]])

    error = validate_map(volts_map, raw, target)
    assert error > MAX_VALIDATION_ERROR_DEG
    assert error > 10_000  # measured ~56,568; an order of magnitude, not a tight pin


def test_a_drifted_map_is_rejected_by_the_same_check():
    drifted = CalibrationMap(
        model=_AFFINE, x=(8.0, 0.05, 0.0), y=(0.0, 0.0, 0.05),
        n_points=4, conditioning=0.9,
    )
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
    identity = CalibrationMap(model=_AFFINE, x=(0.0, 1.0, 0.0), y=(0.0, 0.0, 1.0))
    raw = np.array([[3.0, 4.0], [0.0, 0.0]])
    target = np.zeros((2, 2))

    assert validate_map(identity, raw, target) == pytest.approx(12.5 ** 0.5)


def test_the_source_enum_names_all_four_steps():
    assert [s.value for s in CalibrationSource] == [
        "fitted", "online", "carried_forward", "refused"
    ]


def test_apply_map_matches_a_matrix_multiply_for_an_asymmetric_affine():
    """The affine rung still has to agree with the matrix form everyone
    outside this module thinks in.

    `CalibrationMap` stores `[1, dx, dy]` coefficients per axis, so an affine
    map's `(a00, a01, b0, a10, a11, b1)` is spread across two tuples in a
    different order. This checks the whole round trip against an
    independently assembled `raw @ A.T + b` -- not against `apply_map`'s own
    formula -- with deliberately asymmetric off-diagonal terms, so a mix-up
    between the `dx` and `dy` columns of either axis changes the result.

    `tests/eye/test_calibration_fit.py::test_apply_map_never_transposes_its_
    two_axes` pins the same property on the second-order rung, where there is
    no matrix form to compare against.
    """
    a00, a01, b0, a10, a11, b1 = 2.0, 3.0, 5.0, 7.0, 11.0, 13.0
    map_ = CalibrationMap(
        model=_AFFINE, x=(b0, a00, a01), y=(b1, a10, a11), n_points=4, conditioning=0.8
    )
    raw = np.array([[1.0, 0.0], [0.0, 1.0], [2.0, -3.0], [-4.0, 5.0]])

    a_matrix = np.array([[a00, a01], [a10, a11]])
    b_vector = np.array([b0, b1])
    expected = raw @ a_matrix.T + b_vector

    assert apply_map(map_, raw) == pytest.approx(expected)


def test_calibration_map_equality_is_unusable_by_design():
    """Controller ruling B (task-7 brief) says two maps built with the defaults
    never compare equal, because `conditioning` defaults to nan and nan != nan.

    **The ruling's bottom line holds. Two successive explanations of WHY did
    not, and the second one broke CI.** The ruling's own reason is wrong: a
    dataclass default is evaluated once, at class definition, so two maps that
    do not override `conditioning` share the exact same `float("nan")` OBJECT,
    and nan's "never equal to itself" is never reached. This test then asserted
    the opposite outcome -- that they compare EQUAL, through the identity
    short-circuit -- which was true on 3.11 and is FALSE on 3.13.

    Measured by disassembling the generated `__eq__` on both:

      3.11  BUILD_TUPLE, BUILD_TUPLE, one COMPARE_OP -- a tuple comparison,
            which short-circuits per element on identity, so the shared nan
            object makes the pair EQUAL.
      3.13  one COMPARE_OP per field, no tuple built -- `self.conditioning ==
            other.conditioning` reaches `float.__eq__` directly, which has no
            identity fast path, so the same pair is UNEQUAL.

    Both interpreters still say `(nan,) == (nan,)` is True for a shared object;
    what changed is that dataclasses stopped going through tuple comparison.
    This repository supports both (`pyproject.toml`: 3.11 is wl-sync's floor,
    3.13 is what Fedora ships on the preprocessing server), and CI runs both --
    which is the only reason this was caught, since the local suite runs on
    3.11 alone.

    **So the answer itself is version-dependent, and that is the ruling stated
    more strongly than the ruling stated it**: `==` on a `CalibrationMap`
    answers a question about float identity and interpreter version, never
    about whether two maps are the same calibration. This test therefore pins
    only what is true on both, which is enough to make the point.

    Nothing in `wl_preproc/` compares maps with `==` -- verified by sweep --
    so the 3.13 change alters no production behaviour. `resolve_calibration`
    does it on identity, as the ruling requires, and the chain tests above
    assert `result.map_ is GOOD` rather than `==`.
    """
    fields = {"model": _AFFINE, "x": (0.0, 1.0, 0.0), "y": (0.0, 0.0, 1.0), "n_points": 4}

    # A map whose `conditioning` is a real number equals an identical one --
    # on both interpreters, since every field then compares by value.
    assert CalibrationMap(**fields, conditioning=0.9) == CalibrationMap(**fields, conditioning=0.9)

    # The same calibration in every sense that matters, differing only in that
    # its `conditioning` is nan -- which is exactly what a BORROWED map's is,
    # by `CalibrationMap`'s own documented default -- does not.
    fresh_nan_a = CalibrationMap(**fields, conditioning=float("nan"))
    fresh_nan_b = CalibrationMap(**fields, conditioning=float("nan"))
    assert fresh_nan_a != fresh_nan_b

    # So `==` turns on whether one field happens to be nan, which is invisible
    # at the call site. What identifies a map is its model and coefficients,
    # and those compare correctly whatever `conditioning` holds.
    assert fresh_nan_a.model is fresh_nan_b.model
    assert (fresh_nan_a.x, fresh_nan_a.y) == (fresh_nan_b.x, fresh_nan_b.y)


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
    means fit_map succeeds -- and it must be PREFERRED even when a
    validating MonkeyLogic candidate and a validating carried-forward
    candidate are both on offer, not merely returned because it was the only
    option. Pins the table's order, not just "some result came back".
    """
    raw = np.array([[-100.0, -80.0], [100.0, -80.0], [0.0, 90.0], [60.0, 40.0]])
    target = np.column_stack([0.05 * raw[:, 0], 0.05 * raw[:, 1]])

    result = resolve_calibration(raw, target, GOOD, (GOOD, "2026-08-20_subjA"))

    assert result.source == CalibrationSource.FITTED
    # Four points cannot constrain twelve parameters, so this lands on the
    # affine rung -- still `fitted`, since it is this session's own map from
    # its own targets, only in a simpler shape.
    assert result.map_.model is CalibrationModel.AFFINE
    assert result.reason == ""
    assert result.carried_from is None
    assert apply_map(result.map_, raw) == pytest.approx(target, abs=1e-6)


def test_the_online_map_wins_over_carried_forward_when_both_validate():
    """The online map precedes carry-forward (design spec section 3.5:
    it "comes from the SAME session"). The carried candidate here (_ZERO_MAP)
    validates with a LOWER error than the online candidate (GOOD) against
    this fixture, so a bug that selects
    by lowest error rather than chain order would pick carried-forward --
    and this test would catch it.
    """
    result = resolve_calibration(_DEGENERATE_RAW, _DEGENERATE_TARGET, GOOD, (_ZERO_MAP, "2026-08-20_subjA"))

    assert result.source == CalibrationSource.ONLINE
    assert result.map_ is GOOD
    assert result.carried_from is None
    assert result.validation_error_deg == pytest.approx(
        validate_map(GOOD, _DEGENERATE_RAW, _DEGENERATE_TARGET)
    )


def test_an_online_map_that_fails_validation_falls_through_to_carried_forward():
    """The online step's OWN selection logic must reject a bad candidate,
    not merely be skipped -- so this uses an online map that fails validation for a
    concrete, checkable reason (wrong input space) rather than simply being
    absent, and a distinct, valid carried candidate that step 3 then picks up.
    """
    volts_map = CalibrationMap(
        model=_AFFINE, x=(0.0, 4000.0, 0.0), y=(0.0, 0.0, 4000.0),
        n_points=4, conditioning=0.9,
    )

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
    drifted = CalibrationMap(
        model=_AFFINE, x=(8.0, 0.05, 0.0), y=(0.0, 0.0, 0.05),
        n_points=4, conditioning=0.9,
    )

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
    assert "collinear, coincident or conic targets" in result.reason
    assert "no fallback map validated" in result.reason


def test_an_empty_fixation_epoch_is_refused_without_naming_a_target():
    """A session with no fixation epoch at all cannot even be TESTED, let
    alone fit -- a distinct diagnostic from "geometry was degenerate", pinned
    with an exact match so a mutation that instead falls through to
    fit_map's own (different) empty-input message is caught.
    """
    raw = np.zeros((0, 2))
    target = np.zeros((0, 2))

    result = resolve_calibration(raw, target, None, None)

    assert result.source == CalibrationSource.REFUSED
    assert result.map_ is None
    assert result.validation_error_deg is None
    assert result.reason == "no fixation epoch named a target position"


def test_read_online_map_is_none_when_there_is_nothing_to_read(tmp_path):
    """Both having no candidate path at all, and a path that does not exist
    (read_calibration's own absence handling, design spec section 4.5), are
    ordinary nothing-to-offer outcomes -- not errors, and not distinguished
    from each other.
    """
    assert read_online_map(None) is None
    assert read_online_map(tmp_path / "nope.bhv2") is None


def test_read_online_map_catches_an_unreadable_file_rather_than_raising(tmp_path):
    """Controller ruling D (task-7 brief): read_calibration's second outcome
    -- a `.bhv2` that exists but cannot be structurally walked -- raises
    Bhv2Unreadable. That must not propagate out of the fallback chain's
    online step: a corrupt log is a fact about MonkeyLogic's own recording, not about
    whether this reader still has something else to offer.

    Same truncated-buffer shape tests/eye/test_bhv2.py's own
    test_a_truncated_file_raises_rather_than_returning_absence uses to prove
    read_calibration raises Bhv2Unreadable for it.
    """
    corrupt = tmp_path / "corrupt.bhv2"
    corrupt.write_bytes(b"\x04\x00\x00\x00test\xff\xff")

    assert read_online_map(corrupt) is None


def test_a_corrupt_bhv2_still_lets_the_chain_reach_carried_forward(tmp_path):
    """Controller ruling D's own required test, end to end: "Test that a
    raising `.bhv2` still lets the chain reach step 3." Uses the real
    catching path (read_online_map on an actual corrupt file on disk),
    not a pre-resolved None standing in for it -- if the try/except around
    read_calibration were removed, this raises instead of reaching
    CARRIED_FORWARD, and the test fails for that reason, not a weaker one.
    """
    corrupt = tmp_path / "corrupt.bhv2"
    corrupt.write_bytes(b"\x04\x00\x00\x00test\xff\xff")

    online = read_online_map(corrupt)
    result = resolve_calibration(
        _DEGENERATE_RAW, _DEGENERATE_TARGET, online, (GOOD, "2026-08-20_subjA")
    )

    assert result.source == CalibrationSource.CARRIED_FORWARD
    assert result.carried_from == "2026-08-20_subjA"


# --- The model ladder -------------------------------------------------------
#
# `basis(_, SECOND_ORDER)` needs six well-spread targets; a 3x3 grid supplies
# nine, and eight on a ring supply none at all for the quadratic terms while
# constraining an affine perfectly. Both constellations are measured in
# tests/eye/test_calibration_fit.py.

_GRID_RAW = np.array([[x, y] for x in (-100.0, 0.0, 100.0) for y in (-75.0, 0.0, 75.0)])
_RING_RAW = np.column_stack([
    100 * np.cos(np.arange(8) * 2 * np.pi / 8),
    100 * np.sin(np.arange(8) * 2 * np.pi / 8),
])


def _quadratic_target(raw: np.ndarray) -> np.ndarray:
    """A genuinely second-order truth, written longhand. If the target were
    only affine, second-order would still fit it -- the extra terms would
    come back ~0 -- and the test could not tell the rungs apart by residual."""
    dx, dy = raw[:, 0], raw[:, 1]
    return np.column_stack([
        0.05 * dx + 0.002 * dy + 1e-5 * dx * dx,
        0.06 * dy - 0.001 * dx + 1.5e-5 * dy * dy,
    ])


def test_a_well_conditioned_session_reaches_the_second_order_rung():
    """Rung 1 of the ladder, and it must be PREFERRED over a validating
    online candidate and a validating carried-forward candidate both on
    offer -- not merely returned because nothing else was."""
    target = _quadratic_target(_GRID_RAW)

    result = resolve_calibration(_GRID_RAW, target, GOOD, (GOOD, "2026-08-20_subjA"))

    assert result.source == CalibrationSource.FITTED
    assert result.map_.model is CalibrationModel.SECOND_ORDER
    assert result.carried_from is None
    assert apply_map(result.map_, _GRID_RAW) == pytest.approx(target, abs=1e-9)


def test_a_ring_session_falls_to_the_affine_rung_rather_than_borrowing():
    """THE ladder test. Eight targets on a ring are an ordinary saccade-task
    geometry: they cannot constrain a quadratic (the two square columns
    collapse onto the constant one) but constrain an affine perfectly.

    The session must therefore land on `fitted`/`affine` -- its OWN map -- and
    not fall through to a borrowed one. Both fallback candidates are on offer
    and both would validate, so a chain that dropped the affine rung would
    return `ONLINE` here and this test would catch it.
    """
    target = np.column_stack([0.05 * _RING_RAW[:, 0], 0.05 * _RING_RAW[:, 1]])

    result = resolve_calibration(_RING_RAW, target, GOOD, (GOOD, "2026-08-20_subjA"))

    assert result.source == CalibrationSource.FITTED
    assert result.map_.model is CalibrationModel.AFFINE
    assert result.carried_from is None
    assert apply_map(result.map_, _RING_RAW) == pytest.approx(target, abs=1e-9)


def test_validation_rejects_a_wrong_space_map_at_both_models():
    """Validation is model-agnostic, and that is the strongest evidence the
    two axes are orthogonal: it applies a candidate to this session's own
    fixation and measures where the target lands, never inspecting the map's
    shape. A second-order map wrong in its quadratic terms fails exactly as a
    wrong-space affine already does, which is what lets section 3.5's borrow
    chain survive this upgrade untouched.
    """
    raw = np.array([[10.0, 10.0]])
    target = np.array([[0.5, 0.5]])

    volts_affine = CalibrationMap(
        model=_AFFINE, x=(0.0, 4000.0, 0.0), y=(0.0, 0.0, 4000.0)
    )
    volts_quadratic = CalibrationMap(
        model=CalibrationModel.SECOND_ORDER,
        x=(0.0, 4000.0, 0.0, 0.0, 0.0, 0.0),
        y=(0.0, 0.0, 4000.0, 0.0, 0.0, 0.0),
    )

    for candidate in (volts_affine, volts_quadratic):
        error = validate_map(candidate, raw, target)
        assert error > MAX_VALIDATION_ERROR_DEG
        assert error > 10_000  # measured ~56,568 at both models

    # And the chain refuses both, rather than accepting one shape over the
    # other on anything but its measured error.
    for candidate in (volts_affine, volts_quadratic):
        result = resolve_calibration(_DEGENERATE_RAW, _DEGENERATE_TARGET, candidate, None)
        assert result.source == CalibrationSource.REFUSED


def test_no_monkeylogic_role_name_survives_in_the_package():
    """The rename is a role/format split, not a search-and-replace, so this
    sweeps for the three ROLE spellings and leaves format prose alone.

    `eye/bhv2.py` reads a genuinely MonkeyLogic binary and keeps the vendor
    name throughout, including three `monkeylogic.nimh.nih.gov` URLs -- none
    of which is a role name. What must not survive anywhere is the identifier
    `read_monkeylogic_map`, the enum member `MONKEYLOGIC`, and the quoted
    `calibration_source` value, because each of those answers "whose map is
    this" with a vendor's name and would have to change again the next time
    the control system does.

    A package-wide sweep rather than a per-file check, for the reason this
    subsystem's own handoff records: a correction applied in one file
    survived in its sibling four separate times, so grep the mechanism, not
    the file.
    """
    package = Path(__file__).resolve().parent.parent.parent / "wl_preproc"
    role_spellings = ("read_monkeylogic_map", "MONKEYLOGIC", "'monkeylogic'", '"monkeylogic"')

    offenders = {}
    for path in sorted(package.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        hits = [name for name in role_spellings if name in text]
        if hits:
            offenders[str(path.relative_to(package))] = hits

    assert offenders == {}
