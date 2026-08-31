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

**The field list is the contract** (HANDOVER-wl-expcontroller.md Ask 1):
`mapping_version`, `model`, `coefficients` (`x`/`y`, in `basis()` column
order), `raw_definition`, `targets` (degrees), `conditioning`,
`rms_residual_deg`. `_ExpcontrollerCalibration` below declares exactly these
seven and nothing else (`extra="forbid"`): a file missing one, or carrying an
eighth, is declined rather than partially trusted.

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
than this reader can verify.

**`mapping_version` is read and required, not yet interpreted.** Every
sibling cross-repo file above carries a `schema_version` int for exactly this
reason: so a future format change is a fact a reader can act on rather than
a guess it has to make. This field fills that role under the name the
contract gives it. Nothing here enforces a specific value, because nothing
in the brief specifies what a mismatch should mean -- inventing that rule
would be exactly the guessing this module otherwise refuses to do. A future
version of this format that needs to branch on it can.

**No per-eye split, like the reader beside it.** `schema/eye.py::
EyeCalibration.make()` already accepts this for `.bhv2`: "one map for the
whole session (Task 6's reader has no per-eye split), tried identically for
both eyes" -- MonkeyLogic's own Origin & Gain calibration is not per-eye, and
`read_online_map`'s contract (one path in, one `CalibrationMap` out, called
once per session) is built around that shape. wl-expcontroller's own fitting
process is genuinely per-eye ("We fit your basis to your raw vector",
HANDOVER-wl-expcontroller.md Ask 1) -- richer than MonkeyLogic's, not
poorer -- but surfacing that through `read_online_map` would mean calling it
per eye, which reaches into `EyeCalibration.make()`'s calling code and
contradicts the brief's own "nothing above either changes" for this task.
This module's contract therefore asks wl-expcontroller for ONE calibration
record per file -- whichever eye it represents is theirs to decide, exactly
the choice MonkeyLogic's single Origin & Gain map already made for them
today. This is a real, named limitation, not a silent one: it costs nothing
relative to today (MonkeyLogic's online source was never per-eye either),
and `mapping_version` is exactly the seam a later version of this format
would use to carry two records and let `read_online_map`'s per-session
contract be revisited on purpose, rather than by accretion.

**What this does NOT establish, stated rather than hidden the way
`eye/bhv2.py::as_calibration_map` states its own unverified twelve-number
layout.** `resolve_calibration` validates every borrowed map against each
eye's own fixation before accepting it, which BOUNDS the cost of a map
written for the wrong eye -- it cannot be silently accepted as a correct
calibration for an eye it was never fit to. But whether it reliably FAILS
that validation, rather than sometimes landing under `MAX_VALIDATION_ERROR_DEG`
by chance, depends on how far apart the two eyes' own raw P1-P4 vectors
actually sit on this lab's rigs -- a quantity nobody has measured. The one
test that demonstrates a genuine over-threshold rejection,
`test_a_map_from_the_wrong_input_space_fails_validation_enormously`, is a
units mismatch (volts fed as pixels, ~56,568 degrees) two to three orders of
magnitude past the threshold -- evidence about a wrong INPUT SPACE, not
about the much smaller displacement two eyes on the same head plausibly
produce. This is the one claim in this section nothing has measured.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from wl_preproc.eye.calibration import CalibrationMap, CalibrationModel

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
    repository names most often"). `read_expcontroller_map` reuses that
    existing check by constructing a real `CalibrationMap` and catching the
    `ValueError` it raises on a mismatch, rather than re-deriving it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    x: list[float]
    y: list[float]


class _ExpcontrollerCalibration(BaseModel):
    """The file's own seven fields (HANDOVER-wl-expcontroller.md Ask 1),
    structurally validated. `model` and `raw_definition` are checked here,
    at the field level, because each is a self-contained fact about one
    value; the coefficient-count check is NOT here for the reason
    `_Coefficients` gives.

    `targets`, `conditioning` and `rms_residual_deg` are required and typed,
    matching "the field list above is the contract", but never read again
    past this class: they do not become part of the `CalibrationMap` this
    module returns. `CalibrationMap`'s own docstring is explicit that
    `n_points` and `conditioning` "describe how THIS package fit the
    coefficients" and default to `0`/`nan` for every borrowed source,
    `online` included, because "fabricating a point count or a conditioning
    score for it would claim evidence that does not exist" -- true here
    exactly as it is for `eye/bhv2.py::as_calibration_map`, whose own
    `CalibrationMap(...)` calls likewise never pass either. wl-expcontroller's
    own `conditioning`/`rms_residual_deg` describe how THEY fit THEIR
    coefficients, a different fact this reader has no column to misreport it
    into.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mapping_version: int
    model: str
    coefficients: _Coefficients
    raw_definition: str
    targets: list[tuple[float, float]]
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


def read_expcontroller_map(path: str | Path) -> CalibrationMap | None:
    """The file at `path` as a borrowed `CalibrationMap`, or `None`.

    **Every failure declines; none raises**, unlike `eye/bhv2.py::
    read_calibration` (which raises `Bhv2Unreadable` for a present-but-
    unwalkable file -- a distinction that format needs because a corrupt
    `.bhv2` and an absent one are different facts about MonkeyLogic's own
    recording). This format has no equivalent second outcome to preserve:
    HANDOVER-wl-expcontroller.md Ask 1 asks for exactly one boundary --
    usable, or declined -- and `read_online_map` already treats a `None`
    from either reader as an ordinary nothing-to-offer, so a raised
    exception here would only be caught one frame up for no benefit.

    Two passes, deliberately not one. The first reads and structurally
    validates the file -- I/O, YAML syntax, the seven-field envelope, the
    known-`model` and matching-`raw_definition` checks above -- and
    declines on `OSError` (missing file, a permissions fault, ...),
    `yaml.YAMLError` (not valid YAML), `TypeError` (verified directly:
    `Path(None)` raises `TypeError: expected str, bytes or os.PathLike object, not NoneType`,
    so a caller that ignores this function's own `str | Path` signature and
    passes `None` anyway declines rather than crashing), or `ValueError`
    (`pydantic.ValidationError` is a `ValueError` subclass, confirmed
    elsewhere in this codebase -- `responder/handler.py`'s own docstring --
    so one `except` catches both the field validators above and every
    ordinary pydantic shape failure: a missing field, an extra one,
    `coefficients.x` holding a string that will not parse as a float). The
    second constructs the `CalibrationMap` itself and declines on the
    `ValueError` `CalibrationMap.__post_init__` raises for a coefficient
    count that disagrees with `model` -- see
    `_Coefficients`'s own docstring for why that check is not duplicated
    here. Kept separate so the second block's `except` cannot accidentally
    swallow a bug in the first: each guards exactly the step named above it,
    nothing more.
    """
    try:
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        cal = _ExpcontrollerCalibration.model_validate(payload)
    except (OSError, yaml.YAMLError, TypeError, ValueError):
        return None

    try:
        return CalibrationMap(
            model=CalibrationModel(cal.model),
            x=tuple(cal.coefficients.x),
            y=tuple(cal.coefficients.y),
        )
    except ValueError:
        return None
