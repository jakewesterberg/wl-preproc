"""`wl_preproc/eye/expcontroller.py`: wl-expcontroller's own online
calibration log, read rather than mis-assembled or guessed at.

Written as raw YAML text, not built through `yaml.safe_dump`, so each fixture
doubles as a literal example of the contract wl-expcontroller writes against
(`expcontroller.py`'s own module docstring: "the field list above is the
contract") -- the same reason `tests/eye/test_bhv2.py` hand-packs its `.bhv2`
bytes rather than reusing this reader's own encoder.

Per-eye structure (review round 1, correcting the original single-record
contract this file tested before): `mapping_version`/`raw_definition`/
`targets` are file-wide; `model`/`coefficients`/`conditioning`/
`rms_residual_deg` live under `left:`/`right:`, each optional.
"""

from __future__ import annotations

from pathlib import Path

from wl_preproc.eye.calibration import CalibrationMap, CalibrationModel, OnlineCalibration
from wl_preproc.eye.expcontroller import read_expcontroller_map

# Two eyes, deliberately DIFFERENT coefficients -- so a test comparing
# `.left` against `.right` cannot pass by accident the way it could if both
# eyes happened to share one fixture's numbers.
_GOOD_TWO_EYE = """
mapping_version: 1
raw_definition: "CR1 - CR4"
targets:
  - [0.0, 0.0]
  - [5.0, 0.0]
  - [0.0, 5.0]
left:
  model: affine
  coefficients:
    x: [0.1, 0.02, 0.003]
    y: [-0.1, 0.004, 0.021]
  conditioning: 0.87
  rms_residual_deg: 0.31
right:
  model: affine
  coefficients:
    x: [0.5, 0.06, 0.007]
    y: [-0.5, 0.008, 0.025]
  conditioning: 0.79
  rms_residual_deg: 0.28
"""

_LEFT_MAP = CalibrationMap(model=CalibrationModel.AFFINE, x=(0.1, 0.02, 0.003), y=(-0.1, 0.004, 0.021))
_RIGHT_MAP = CalibrationMap(model=CalibrationModel.AFFINE, x=(0.5, 0.06, 0.007), y=(-0.5, 0.008, 0.025))

_GOOD_SECOND_ORDER_LEFT_ONLY = """
mapping_version: 1
raw_definition: "CR1 - CR4"
targets:
  - [0.0, 0.0]
  - [5.0, 0.0]
  - [0.0, 5.0]
  - [-5.0, 0.0]
  - [0.0, -5.0]
  - [3.0, 3.0]
left:
  model: second_order
  coefficients:
    x: [0.1, 0.02, 0.003, 0.0004, 0.0005, 0.0006]
    y: [-0.1, 0.004, 0.021, 0.0007, 0.0008, 0.0009]
  conditioning: 0.24
  rms_residual_deg: 0.19
"""


def _same_map(actual, expected) -> bool:
    """Compare the fields a reader actually sets, never the whole dataclass.

    `CalibrationMap.conditioning` defaults to `float("nan")`, and `27917b4`
    established what that does to `==`: on 3.11 the generated `__eq__` builds a
    tuple and `tuple.__eq__` checks `is` before `==`, so two maps sharing the
    one default nan OBJECT compare equal; on 3.13 it compares field by field
    and reaches `float.__eq__`, where nan is never equal to itself. A test
    asserting `==` between two default-conditioning maps therefore passes on
    3.11 and fails on 3.13 -- which is exactly how CI went red for a day after
    the eye merge (`e7c8ea4`), and how these seven tests failed on 3.13 while
    every local run was green.

    A reader parsing an online calibration sets `model`, `x` and `y` and leaves
    `n_points`/`conditioning` at their defaults deliberately, because it did
    not fit the map and has no evidence for either. Those three fields are the
    whole of what a round-trip test is entitled to assert.
    """
    return (
        actual is not None
        and actual.model == expected.model
        and actual.x == expected.x
        and actual.y == expected.y
    )


def test_the_field_comparison_helper_distinguishes_two_different_maps():
    """`_same_map` is what every round-trip assertion in this file rests on,
    so it gets its own test.

    Without this, mutating the helper's body to `return True` leaves all
    twelve tests in this file passing while none of them checks a parsed value
    -- measured, not supposed. That is the same "a test that cannot fail"
    shape this subsystem's review kept finding, and a boolean-returning helper
    is the easiest way to reintroduce it: the assertion reads as a comparison
    but is only as strong as the function behind it.
    """
    assert _same_map(_LEFT_MAP, _LEFT_MAP)
    assert not _same_map(_LEFT_MAP, _RIGHT_MAP)
    assert not _same_map(None, _LEFT_MAP)
    assert not _same_map(
        _LEFT_MAP, CalibrationMap(model=CalibrationModel.AFFINE, x=_LEFT_MAP.x, y=(9.0, 9.0, 9.0))
    )


def _write(tmp_path: Path, text: str, name: str = "calibration.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def test_a_valid_two_eye_file_yields_a_different_map_per_eye(tmp_path):
    """The primary path, and the review round 1 fix in one test: `left` and
    `right` are read INDEPENDENTLY and come back as the two DIFFERENT
    `CalibrationMap`s their own records describe -- not one shared map
    applied to both, and not either eye's numbers bleeding into the other's.
    No reordering, since this module's own contract states `basis()` column
    order rather than inheriting a vendor's differing convention the way
    `eye/bhv2.py::as_calibration_map` must.
    """
    result = read_expcontroller_map(_write(tmp_path, _GOOD_TWO_EYE))

    assert _same_map(result.left, _LEFT_MAP)
    assert _same_map(result.right, _RIGHT_MAP)
    assert result.left != result.right


def test_a_valid_second_order_file_round_trips_to_the_right_calibration_map(tmp_path):
    """The ladder's other rung: six coefficients per axis, same contract,
    exercised here for a single eye (`right` simply absent -- see
    `test_a_single_eye_file_leaves_the_other_eye_without_a_candidate` for
    that absence proven as its own behaviour, not incidental here)."""
    result = read_expcontroller_map(_write(tmp_path, _GOOD_SECOND_ORDER_LEFT_ONLY))

    assert _same_map(
        result.left,
        CalibrationMap(
            model=CalibrationModel.SECOND_ORDER,
            x=(0.1, 0.02, 0.003, 0.0004, 0.0005, 0.0006),
            y=(-0.1, 0.004, 0.021, 0.0007, 0.0008, 0.0009),
        ),
    )
    assert result.right is None


def test_a_borrowed_map_carries_no_fabricated_fit_history(tmp_path):
    """`CalibrationMap`'s own docstring: `n_points`/`conditioning` "describe
    how THIS package fit the coefficients" and must stay at their `0`/`nan`
    defaults for every borrowed source, `online` included, even though THIS
    file's own per-eye `conditioning` fields (0.87/0.79, above) state real
    numbers -- wl-expcontroller's own fit quality, not this package's.
    Stuffing either into `CalibrationMap.conditioning` would "claim evidence
    that does not exist", the exact fabrication `CalibrationMap`'s docstring
    names. Checked for BOTH eyes, not just one -- the per-eye split does not
    get to skip this rule for either side.
    """
    result = read_expcontroller_map(_write(tmp_path, _GOOD_TWO_EYE))

    for one_eye in (result.left, result.right):
        assert one_eye.n_points == 0
        assert one_eye.conditioning != one_eye.conditioning  # nan


def test_a_single_eye_file_leaves_the_other_eye_without_a_candidate(tmp_path):
    """"A file offering a map for only one eye is fine" (review round 1):
    the ABSENT eye is not borrowed from its sibling, not filled in with a
    copy, and not treated as a whole-file decline -- it is simply `None`,
    the identical "nothing to offer" signal `resolve_calibration` already
    treats a never-offered candidate as.
    """
    result = read_expcontroller_map(_write(tmp_path, _GOOD_SECOND_ORDER_LEFT_ONLY))

    assert result.left is not None
    assert result.right is None


def test_an_unknown_model_declines_only_that_eye(tmp_path):
    """Per-eye independence, proven where it matters most: `right`'s own
    `model` is unrecognised, but `left` is untouched by it and still comes
    back as a real, usable map -- not the whole file declining because ONE
    side of it was bad. Refuse rather than guess, the way `as_calibration_
    map` declines a wrong-length `a` (HANDOVER-wl-expcontroller.md Ask 1),
    scoped to the one side that actually earned the refusal.
    """
    bad = _GOOD_TWO_EYE.replace("model: affine\n  coefficients:\n    x: [0.5", "model: cubic\n  coefficients:\n    x: [0.5")

    result = read_expcontroller_map(_write(tmp_path, bad))

    assert _same_map(result.left, _LEFT_MAP)
    assert result.right is None


def test_a_coefficient_count_disagreeing_with_its_model_declines_only_that_eye(tmp_path):
    """The other named refusal (HANDOVER-wl-expcontroller.md Ask 1):
    `n_terms(model)` disagrees with what is actually there -- here, `right`
    declares `affine` (3 terms) but carries a second-order-shaped `x`.
    Declined via the identical check `CalibrationMap.__post_init__` already
    applies to every map this package constructs (`_Coefficients`'s own
    docstring), not a second, hand-rolled length comparison -- and, again,
    scoped to `right` alone: `left` is unaffected.
    """
    bad = _GOOD_TWO_EYE.replace(
        "x: [0.5, 0.06, 0.007]", "x: [0.5, 0.06, 0.007, 0.0008, 0.0009, 0.0010]"
    )

    result = read_expcontroller_map(_write(tmp_path, bad))

    assert _same_map(result.left, _LEFT_MAP)
    assert result.right is None


def test_an_unexpected_field_inside_one_eye_record_declines_only_that_eye(tmp_path):
    """`_EyeRecord`'s own `extra="forbid"` is per-eye too: a stray field
    under `right:` (a typo in a future wl-expcontroller writer, say) must
    not cost `left` its own perfectly good map."""
    bad = _GOOD_TWO_EYE.replace(
        "right:\n  model: affine", "right:\n  model: affine\n  extra_field: 1"
    )

    result = read_expcontroller_map(_write(tmp_path, bad))

    assert _same_map(result.left, _LEFT_MAP)
    assert result.right is None


def test_a_malformed_file_is_declined_rather_than_raising(tmp_path):
    """Not valid YAML at all -- `yaml.YAMLError`, caught and declined, never
    propagated. `read_online_map` (and every step above it) must never see
    an exception from a broken sidecar file. A file-wide failure, unlike the
    per-eye ones above: there is no shared envelope to even find `left`/
    `right` inside, so the whole file declines to bare `None`."""
    path = _write(tmp_path, "{not: valid: yaml: [")

    assert read_expcontroller_map(path) is None


def test_a_file_missing_a_required_file_wide_field_is_declined(tmp_path):
    """"The field list above is the contract" cuts both ways at the
    file-wide level: `mapping_version`/`raw_definition`/`targets` are all
    required, `extra="forbid"` on `_ExpcontrollerCalibration`. Here
    `mapping_version` is simply absent -- and unlike a per-eye field, there
    is no partial outcome to preserve: the whole file declines."""
    lines = [
        line for line in _GOOD_TWO_EYE.strip().splitlines() if not line.startswith("mapping_version")
    ]

    assert read_expcontroller_map(_write(tmp_path, "\n".join(lines))) is None


def test_a_file_with_an_unexpected_file_wide_field_is_declined(tmp_path):
    """The other half of "the field list above is the contract" at the
    file-wide level: an unexpected top-level field is refused rather than
    silently ignored, so a typo in a future wl-expcontroller writer
    (`raw_defintion:`, say) fails loudly as a decline instead of silently
    dropping the real `raw_definition` this reader still checks."""
    bad = _GOOD_TWO_EYE + "\nextra_field: 1\n"

    assert read_expcontroller_map(_write(tmp_path, bad)) is None


def test_a_raw_definition_that_does_not_match_is_declined(tmp_path):
    """Checked, not merely stored (`expcontroller.py`'s own module
    docstring): coefficients fit against a raw vector other than `CR1 - CR4`
    would be silently misapplied to `eye/gaze.py::purkinje_vector`'s
    CR1-CR4 difference if accepted anyway. Not one of
    HANDOVER-wl-expcontroller.md Ask 1's two named refusal cases, but the
    identical "refuse rather than guess" principle applied to a third way a
    file can claim more than this reader can verify. File-wide, so both
    eyes decline together -- `raw_definition` is one fact about the whole
    file, not either eye's own record (module docstring).
    """
    bad = _GOOD_TWO_EYE.replace('raw_definition: "CR1 - CR4"', 'raw_definition: "CR2 - CR4"')

    assert read_expcontroller_map(_write(tmp_path, bad)) is None


def test_absence_is_an_ordinary_skip(tmp_path):
    """No file at `path` at all -- the same "not an error" outcome design
    spec section 4.5 states for `.bhv2`, generalised: `read_online_map`
    never even reaches this function for a session with no expcontroller
    log at all (`_find_expcontroller_log` returns `None` first), but this
    function is just as forgiving if it is ever asked about a path directly.
    """
    assert read_expcontroller_map(tmp_path / "nope.yaml") is None
