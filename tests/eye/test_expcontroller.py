"""`wl_preproc/eye/expcontroller.py`: wl-expcontroller's own online
calibration log, read rather than mis-assembled or guessed at.

Written as raw YAML text, not built through `yaml.safe_dump`, so each fixture
doubles as a literal example of the contract wl-expcontroller writes against
(`expcontroller.py`'s own module docstring: "the field list above is the
contract") -- the same reason `tests/eye/test_bhv2.py` hand-packs its `.bhv2`
bytes rather than reusing this reader's own encoder.
"""

from __future__ import annotations

from pathlib import Path

from wl_preproc.eye.calibration import CalibrationMap, CalibrationModel
from wl_preproc.eye.expcontroller import read_expcontroller_map

_GOOD_AFFINE = """
mapping_version: 1
model: affine
coefficients:
  x: [0.1, 0.02, 0.003]
  y: [-0.1, 0.004, 0.021]
raw_definition: "CR1 - CR4"
targets:
  - [0.0, 0.0]
  - [5.0, 0.0]
  - [0.0, 5.0]
conditioning: 0.87
rms_residual_deg: 0.31
"""

_GOOD_SECOND_ORDER = """
mapping_version: 1
model: second_order
coefficients:
  x: [0.1, 0.02, 0.003, 0.0004, 0.0005, 0.0006]
  y: [-0.1, 0.004, 0.021, 0.0007, 0.0008, 0.0009]
raw_definition: "CR1 - CR4"
targets:
  - [0.0, 0.0]
  - [5.0, 0.0]
  - [0.0, 5.0]
  - [-5.0, 0.0]
  - [0.0, -5.0]
  - [3.0, 3.0]
conditioning: 0.24
rms_residual_deg: 0.19
"""


def _write(tmp_path: Path, text: str, name: str = "calibration.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def test_a_valid_affine_file_round_trips_to_the_right_calibration_map(tmp_path):
    """The primary path: a well-formed file becomes exactly the
    `CalibrationMap` its own `coefficients` describe -- no reordering, since
    this module's own contract states `basis()` column order rather than
    inheriting a vendor's differing convention the way `eye/bhv2.py::
    as_calibration_map` must.
    """
    result = read_expcontroller_map(_write(tmp_path, _GOOD_AFFINE))

    assert result == CalibrationMap(
        model=CalibrationModel.AFFINE, x=(0.1, 0.02, 0.003), y=(-0.1, 0.004, 0.021)
    )


def test_a_valid_second_order_file_round_trips_to_the_right_calibration_map(tmp_path):
    """The ladder's other rung: six coefficients per axis, same contract."""
    result = read_expcontroller_map(_write(tmp_path, _GOOD_SECOND_ORDER))

    assert result == CalibrationMap(
        model=CalibrationModel.SECOND_ORDER,
        x=(0.1, 0.02, 0.003, 0.0004, 0.0005, 0.0006),
        y=(-0.1, 0.004, 0.021, 0.0007, 0.0008, 0.0009),
    )


def test_a_borrowed_map_carries_no_fabricated_fit_history(tmp_path):
    """`CalibrationMap`'s own docstring: `n_points`/`conditioning` "describe
    how THIS package fit the coefficients" and must stay at their `0`/`nan`
    defaults for every borrowed source, `online` included, even though THIS
    file's own `conditioning` field (0.87, above) states a real number --
    wl-expcontroller's own fit quality, not this package's. Stuffing it into
    `CalibrationMap.conditioning` would "claim evidence that does not
    exist", the exact fabrication `CalibrationMap`'s docstring names.
    """
    result = read_expcontroller_map(_write(tmp_path, _GOOD_AFFINE))

    assert result.n_points == 0
    assert result.conditioning != result.conditioning  # nan


def test_a_file_naming_an_unknown_model_is_declined(tmp_path):
    """Refuse rather than guess: a `model` `CalibrationModel` does not have
    is declined, the way `as_calibration_map` declines a wrong-length `a`
    (HANDOVER-wl-expcontroller.md Ask 1)."""
    bad = _GOOD_AFFINE.replace("model: affine", "model: cubic")

    assert read_expcontroller_map(_write(tmp_path, bad)) is None


def test_a_coefficient_count_disagreeing_with_its_model_is_declined(tmp_path):
    """The other named refusal: `n_terms(model)` disagrees with what is
    actually there -- here, an affine file (3 terms) carrying a
    second-order-shaped `x`. Declined via the identical check
    `CalibrationMap.__post_init__` already applies to every map this
    package constructs, not a second, hand-rolled length comparison."""
    bad = _GOOD_AFFINE.replace(
        "x: [0.1, 0.02, 0.003]", "x: [0.1, 0.02, 0.003, 0.0004, 0.0005, 0.0006]"
    )

    assert read_expcontroller_map(_write(tmp_path, bad)) is None


def test_a_malformed_file_is_declined_rather_than_raising(tmp_path):
    """Not valid YAML at all -- `yaml.YAMLError`, caught and declined, never
    propagated. `read_online_map` (and every step above it) must never see
    an exception from a broken sidecar file."""
    path = _write(tmp_path, "{not: valid: yaml: [")

    assert read_expcontroller_map(path) is None


def test_a_file_missing_a_required_field_is_declined(tmp_path):
    """"The field list above is the contract" cuts both ways: every one of
    the seven fields is required, `extra="forbid"` on `_ExpcontrollerCalibration`.
    Here `mapping_version` is simply absent."""
    lines = [
        line for line in _GOOD_AFFINE.strip().splitlines() if not line.startswith("mapping_version")
    ]

    assert read_expcontroller_map(_write(tmp_path, "\n".join(lines))) is None


def test_a_file_with_an_unexpected_field_is_declined(tmp_path):
    """The other half of "the field list above is the contract": an eighth
    field is refused rather than silently ignored, so a typo in a future
    wl-expcontroller writer (`raw_defintion:`, say) fails loudly as a
    decline instead of silently dropping the real `raw_definition` this
    reader still checks."""
    bad = _GOOD_AFFINE + "\nextra_field: 1\n"

    assert read_expcontroller_map(_write(tmp_path, bad)) is None


def test_a_raw_definition_that_does_not_match_is_declined(tmp_path):
    """Checked, not merely stored (`expcontroller.py`'s own module
    docstring): coefficients fit against a raw vector other than `CR1 - CR4`
    would be silently misapplied to `eye/gaze.py::purkinje_vector`'s
    CR1-CR4 difference if accepted anyway. Not one of
    HANDOVER-wl-expcontroller.md Ask 1's two named refusal cases, but the
    identical "refuse rather than guess" principle applied to a third way a
    file can claim more than this reader can verify.
    """
    bad = _GOOD_AFFINE.replace('raw_definition: "CR1 - CR4"', 'raw_definition: "CR2 - CR4"')

    assert read_expcontroller_map(_write(tmp_path, bad)) is None


def test_absence_is_an_ordinary_skip(tmp_path):
    """No file at `path` at all -- the same "not an error" outcome design
    spec section 4.5 states for `.bhv2`, generalised: `read_online_map`
    never even reaches this function for a session with no expcontroller
    log at all (`_find_expcontroller_log` returns `None` first), but this
    function is just as forgiving if it is ever asked about a path directly.
    """
    assert read_expcontroller_map(tmp_path / "nope.yaml") is None
