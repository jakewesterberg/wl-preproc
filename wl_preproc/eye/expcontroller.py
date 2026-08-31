"""wl-expcontroller's own online calibration log -- the second reader
`eye/calibration.py::read_online_map` was always going to need.

**Why this module exists.** `CalibrationSource.ONLINE`'s own docstring
(`eye/calibration.py`) named the problem before it was real: "The
behavioural control system will change, and whatever replaces MonkeyLogic
will also save a calibration." Under wl-expcontroller's ADR-0005, MonkeyLogic
is not deployed at all -- HANDOVER-wl-expcontroller.md's Ask 1 -- so `.bhv2`
never exists and `read_online_map`'s only reader (`eye/bhv2.py`) never has
anything to open. This module is the second reader `schema/eye.py::
_find_expcontroller_log`'s own docstring already reserved a glob for. Format-
specific things keep format names (`bhv2.py`'s own docstring states that
principle); this one is named for the controller because, unlike `.bhv2`,
the format below has no vendor name of its own to borrow -- it is defined by
this module, for this one cross-repo contract.

**They write the file, we read it.** A small YAML file, one per session, at
`<session>/expcontroller/*.yaml` (`contracts.paths.EXPCONTROLLER_DIRNAME`;
`schema/eye.py::_find_expcontroller_log` finds it). Wire format is YAML, not
JSON, matching every other file this pipeline reads that a DIFFERENT repository
writes: `contracts/sidecar.py` (the FLIR behaviour-camera project's own
sidecar), `contracts/done.py` (a transfer's own completion marker),
`contracts/manifest.py` (`session_manifest.yaml`). All three use a pydantic
model with `extra="forbid"` over a `yaml.safe_load` -- refuse an unrecognised
field rather than silently ignore it -- and this module follows the same
shape rather than inventing a fourth pattern for a fourth cross-repo file.
`pyyaml` and `pydantic` are both already hard dependencies (`pyproject.toml`),
so this costs nothing new.

**The field list is the contract** (HANDOVER-wl-expcontroller.md Ask 1), now
split across two levels rather than one flat record -- review round 1: "The
YAML format carries coefficients per eye. Read them per eye." `mapping_version`,
`raw_definition` and `targets` (degrees) are FILE-WIDE facts: one calibration
run, one raw-vector definition, one target constellation, regardless of how
many eyes it produced a usable fit for. `model`, `coefficients` (`x`/`y`, in
`basis()` column order), `conditioning` and `rms_residual_deg` are PER EYE,
each living under an optional `left:`/`right:` key -- they are properties of
ONE eye's own fit against ONE eye's own raw vector, and nothing about them is
shared just because the file that carries them is. `_ExpcontrollerCalibration`
declares the three file-wide fields and nothing else at its own level
(`extra="forbid"`); `_EyeRecord` declares the four per-eye fields and nothing
else, independently, for whichever of `left`/`right` is present. A file
missing a file-wide field, or carrying an unexpected one at either level, is
declined; see "Per eye, independently", below, for what a bad or absent EYE
record does instead.

**Per eye, independently -- not one gate for both.** A file offering a
usable map for only one eye is an ordinary, valid outcome ("a file offering
a map for only one eye is fine", review round 1) -- the tracking that
produced it may simply have been better on one side this session. So `left`
and `right` are validated SEPARATELY, each against its own copy of
`_EyeRecord`, and a failure in one (an unknown `model`, a coefficient count
that disagrees with it) declines only that eye's own candidate rather than
the whole file: `_ExpcontrollerCalibration.left`/`.right` are typed as loose
`dict[str, Any] | None`, not `_EyeRecord | None`, specifically so that a
malformed `right` cannot make pydantic refuse to construct the outer model
at all and take a perfectly good `left` down with it. `_eye_map`, below, is
where each side's own `_EyeRecord.model_validate` actually runs, isolated by
construction rather than by a `try` a future edit could accidentally widen
to cover both sides at once.

**Coefficients arrive pre-ordered.** `eye/bhv2.py::as_calibration_map`
re-orders MonkeyLogic's own flat six numbers into `basis()` column order at
that vendor boundary, because MonkeyLogic's own convention differs from ours
and neither side controls the other's format. Here the direction is
reversed: this module defines the wire format, so it simply specifies
`basis()` column order as part of the contract (the field list above says so
directly) and wl-expcontroller writes to it -- no re-ordering step exists
here because none is needed.

**`raw_definition` is checked, not merely stored.** The brief lists it as a
field to read; this module also refuses a file whose stated value is not
`"CR1 - CR4"` -- the same feature `eye/calibration.py`'s own module docstring
names ("The feature is P1 - P4", P1/P4 being ohDPI's own names for the
Purkinje images `eye/gaze.py::purkinje_vector` reads as `CR1`/`CR4`). A file
is a set of coefficients fit against SOME raw vector; if wl-expcontroller
ever changed which channels it fits against, coefficients honestly labelled
for that change would be silently misapplied to `purkinje_vector`'s CR1-CR4
difference if this reader trusted them anyway. Not one of the two refusal
cases the brief calls out by name (unknown `model`, wrong coefficient count)
but the identical principle applied to a third way a file can claim more
than this reader can verify. File-wide rather than per eye: both eyes' raw
vectors are read off the same two-column CR1-CR4 convention
(`purkinje_vector`'s own `eye` argument only ever changes WHICH file columns
are read, `LeftCR1X` versus `RightCR1X`, never the CR1-CR4 shape itself), so
one statement of the formula covers both.

**`mapping_version` is read and required, not yet interpreted.** Every
sibling cross-repo file above carries a `schema_version` int for exactly this
reason: so a future format change is a fact a reader can act on rather than
a guess it has to make. This field fills that role under the name the
contract gives it. Nothing here enforces a specific value, because nothing
in the brief specifies what a mismatch should mean -- inventing that rule
would be exactly the guessing this module otherwise refuses to do. A future
version of this format that needs to branch on it can; the per-eye split
above is already such a change, made within version 1 rather than deferred
to a version 2 this field was originally reserved for -- review round 1
found the single-record v1 this reservation assumed was itself the wrong
call, not merely something to defer correcting.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from wl_preproc.eye.calibration import CalibrationMap, CalibrationModel, OnlineCalibration

# The one raw feature this reader accepts coefficients against -- ASCII
# hyphen-minus with single spaces either side, matching both the brief's own
# rendering and `eye/calibration.py`'s module docstring ("The feature is
# P1 - P4").
_RAW_DEFINITION = "CR1 - CR4"


class _Coefficients(BaseModel):
    """One axis pair, in `basis()` column order. Length is not checked here
    -- `CalibrationMap.__post_init__` already checks a coefficient tuple
    against its own model's `n_terms`, and repeating that comparison here
    would put the same fact in two places for no reason (`EyeCalibration`'s
    own `calibration_model` column comment names this "the defect this
    repository names most often"). `_eye_map`, below, reuses that existing
    check by constructing a real `CalibrationMap` and catching the
    `ValueError` it raises on a mismatch, rather than re-deriving it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    x: list[float]
    y: list[float]


class _EyeRecord(BaseModel):
    """One eye's own calibration: which model it reached, its coefficients,
    and how wl-expcontroller judged its own fit. Validated from an already
    isolated raw `dict` (`_ExpcontrollerCalibration.left`/`.right`'s own
    loose typing, module docstring's "Per eye, independently"), never as a
    nested field of that outer model directly -- the isolation is what lets
    a bad `right` decline only `right` rather than taking a good `left`
    down with it when the outer model is built.

    `conditioning`/`rms_residual_deg` are required and typed here, matching
    "the field list is the contract", but never read again once this class
    validates: they do not become part of the `CalibrationMap` `_eye_map`
    returns. `CalibrationMap`'s own docstring is explicit that `n_points`
    and `conditioning` "describe how THIS package fit the coefficients" and
    default to `0`/`nan` for every borrowed source, `online` included,
    because "fabricating a point count or a conditioning score for it would
    claim evidence that does not exist" -- true here exactly as it is for
    `eye/bhv2.py::as_calibration_map`, whose own `CalibrationMap(...)` calls
    likewise never pass either. wl-expcontroller's own `conditioning`/
    `rms_residual_deg` describe how THEY fit THEIR coefficients for THIS
    eye, a different fact this reader has no column to misreport it into.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    coefficients: _Coefficients
    conditioning: float
    rms_residual_deg: float

    @field_validator("model")
    @classmethod
    def _model_is_known(cls, value: str) -> str:
        try:
            CalibrationModel(value)
        except ValueError as exc:
            known = ", ".join(member.value for member in CalibrationModel)
            raise ValueError(f"model {value!r} is not one of: {known}") from exc
        return value


class _ExpcontrollerCalibration(BaseModel):
    """The file's own three FILE-WIDE fields (module docstring), plus
    `left`/`right` -- each an already-isolated raw `dict` or absent,
    deliberately NOT typed as `_EyeRecord | None` here. Typing them as
    `_EyeRecord` at this level would make pydantic validate both eyes as
    one nested operation: a malformed `right` would raise for the WHOLE
    model, refusing a perfectly good `left` along with it. Kept as `dict`
    so each side can be handed to its own, independent `_EyeRecord.model_validate`
    call in `_eye_map` -- see the module docstring's "Per
    eye, independently" for why that independence is the point, not an
    implementation detail.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mapping_version: int
    raw_definition: str
    targets: list[tuple[float, float]]
    left: dict[str, Any] | None = None
    right: dict[str, Any] | None = None

    @field_validator("raw_definition")
    @classmethod
    def _raw_definition_matches(cls, value: str) -> str:
        if value != _RAW_DEFINITION:
            raise ValueError(
                f"raw_definition {value!r} does not match {_RAW_DEFINITION!r}; "
                "coefficients fit against a different raw vector would be "
                "silently misapplied to purkinje_vector's CR1-CR4 difference "
                "if accepted anyway"
            )
        return value


def _eye_map(record: dict[str, Any] | None) -> CalibrationMap | None:
    """One eye's own record as a borrowed `CalibrationMap`, independently of
    whatever its sibling eye's own record does. `None` in, `None` out --
    the eye simply was not offered (module docstring's "Per eye,
    independently"). A present record that fails ITS OWN validation --
    `_EyeRecord`'s `ValidationError` (a `ValueError` subclass, confirmed
    elsewhere in this codebase -- `responder/handler.py`'s own docstring)
    for an unknown `model` or a missing/extra field, or
    `CalibrationMap.__post_init__`'s own `ValueError` for a coefficient count that
    disagrees with `model` (not re-derived here -- see `_Coefficients`'s
    own docstring) -- also declines to `None` rather than raising, so one
    eye's bad record can never surface as an exception out of
    `read_expcontroller_map` for a session whose OTHER eye might be fine.
    """
    if record is None:
        return None

    try:
        parsed = _EyeRecord.model_validate(record)
    except ValueError:
        return None

    try:
        return CalibrationMap(
            model=CalibrationModel(parsed.model),
            x=tuple(parsed.coefficients.x),
            y=tuple(parsed.coefficients.y),
        )
    except ValueError:
        return None


def read_expcontroller_map(path: str | Path) -> OnlineCalibration | None:
    """The file at `path` as an `OnlineCalibration`, or `None`.

    **`None` means the file itself could not be read at all** -- missing,
    unparseable YAML, or missing/misshapen at the FILE-WIDE level
    (`mapping_version`, `raw_definition`, `targets`). That is a fact about
    the file as a whole and both eyes decline together, exactly as this
    function already declined as a whole before per-eye reading existed.
    A file that reads fine at that level but offers a usable record for
    only one eye is NOT this case: it returns a real `OnlineCalibration`
    with one field populated and the other `None` -- "a file offering a map
    for only one eye is fine" (review round 1) -- so absence at the FILE
    level and absence for ONE EYE are different facts, deliberately not
    collapsed onto each other the way `Bhv2Calibration.present` and
    `as_calibration_map`'s own length check are kept apart in `bhv2.py` for
    the same reason: they are different facts even when downstream code
    reacts to them the same way.

    Declines (never raises) on `OSError` (missing file, a permissions
    fault, ...), `yaml.YAMLError` (not valid YAML), `TypeError` (verified
    directly: `Path(None)` raises `TypeError: expected str, bytes or os.PathLike object, not NoneType`,
    so a caller that ignores this function's own `str | Path` signature and
    passes `None` anyway declines rather than crashing), or `ValueError`
    (`pydantic.ValidationError` is a `ValueError` subclass -- the
    `raw_definition` field validator above, and every ordinary pydantic
    shape failure at the file-wide level: a missing field, an extra one,
    `targets` holding something that will not parse as a list of pairs).
    Per-eye failures are handled separately, inside `_eye_map`, and never
    reach this function's own `except` at all.
    """
    try:
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        cal = _ExpcontrollerCalibration.model_validate(payload)
    except (OSError, yaml.YAMLError, TypeError, ValueError):
        return None

    return OnlineCalibration(left=_eye_map(cal.left), right=_eye_map(cal.right))
