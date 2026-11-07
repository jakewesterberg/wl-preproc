"""The rig's own record of every trial: wl-xcon's `xcon/trials.jsonl`
(design spec `2026-09-29-nwb-publishing-design.md` section 2.1).

**Codes own identity and timing; the rig's record owns content.** wl-xcon's own
allocation rule, verbatim: "Event codes carry identity and timing. The session
record carries content." (wl-xcon's S2 event-vocabulary design.) Each line of
the record is one trial -- `wl_xcon/record.py::Recorder.trial` -- with its
`index`, `subject`, `outcome`, the scheduler's `block` and `condition` names,
and `params`, "the whole resolved parameter set, per trial": the stimulus
settings it actually ran with.

**Read, never repaired.** A line that is not a JSON object with those fields is
reported as a problem, by line number, and left out; nothing is guessed. Two
animals routinely share one day's session directory, so the record carries the
subject on every line and only this subject's lines are kept.

**Each line is keyed by its `trial_number`** (design spec
`2026-10-01-runs-and-trials-design.md` section 3.1). Since wl-xcon's slice
b3a-1 a session holds several runs, each line names its run, and each run
counts its `index` from 0; since its XC-155 each line also carries
`trial_number`, counted from 1 across the session and equal to the stream's
`TRIAL_NUMBER`. That number is the key. A record from before XC-155, whose
lines name no run, is keyed by `index`, which then counted across the
session. A line that names a run but carries no `trial_number` has no key
across the session, so it is reported and left out.

**And what the line says beyond its six** (design spec
`2026-11-07-rig-record-fields-design.md` sections 2 and 3): since wl-xcon's
engine build C2a, what the trial did to its condition's count (`counting`,
never its answer, which `outcome` is), whether its stimulus appeared, which
trial it shows again and whether straight after it, the condition before it,
whether it is a drum's repeat and drumming was on, and the training aids on;
since C2b, the trial structure it played and the values its condition set that
a live edit overrode; since C3, where each drawn setting's range came from, the
settings a person held, those whose live change kept it out of moving up, and
a designed block's planned place; since XC-399, where each stimulus it showed
was and when (`stimuli`); and the fluid it commanded. Each is optional
(`events/rigfields.py`): a line without it reads as before, and one it cannot
read is read as none, said once per field, never left out for it. `carries`
names the ones the line has, so a column is published only where some line
carries its field.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from wl_preproc.contracts.paths import XCON_DIRNAME
from wl_preproc.events.rigfields import (
    BOOLEAN,
    DRAWN,
    MAPPING,
    NUMBER,
    OBJECTS,
    TEXT,
    TEXTS,
    WHOLE,
    Fields,
    Kind,
)

RECORD_NAME = "trials.jsonl"
_FIELDS = ("index", "subject", "outcome", "block", "condition", "params")


@dataclasses.dataclass(frozen=True, slots=True)
class Planned:
    """A designed block's planned place for its trial (wl-xcon's engine build
    C3): in a sequence-balanced block its cycle and position, from 0, and
    whether it is in the cycle's lead-in; in a priming block its run of one
    value, from 0, whether it is that run's first, and the value planned."""

    cycle: int | None = None
    position: int | None = None
    lead_in: bool | None = None
    run: int | None = None
    switch: bool | None = None
    value: object = None


def _planned(value):
    if value is None:
        return None, True
    if not isinstance(value, dict):
        return None, False
    wholes, flags = ("cycle", "position", "run"), ("lead_in", "switch")
    if (any(not WHOLE.read(value.get(name))[1] for name in wholes)
            or any(not BOOLEAN.read(value.get(name))[1] for name in flags)):
        return None, False
    return Planned(**{name: value.get(name) for name in (*wholes, *flags)}, value=value.get("value")), True


PLANNED = Kind(_planned, "a planned place")


@dataclasses.dataclass(frozen=True, slots=True)
class Place:
    """Where something was drawn, as the rig's record says (wl-xcon's XC-399):
    its cyclopean per-axis position and its true eccentricity and polar angle,
    in degrees, each None where the record says none."""

    x: float | None = None
    y: float | None = None
    eccentricity: float | None = None
    polar: float | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class Stimulus:
    """One entry of a trial line's `stimuli` (XC-399): a `Show` that put a
    stimulus up, or an `Update` that changed it while it was up, on the screen
    for at least one frame of the trial. `frame` and `off` are the trial's
    frames, from 1; `off` None while it stayed on to the trial's end. An
    array's `objects` and `target`; a per-eye stimulus's `left` and `right` as
    written, and their objects, by eye, in `eye_objects`."""

    name: str | None
    kind: str | None
    frame: int | None
    off: int | None
    changed: tuple[str, ...]
    drawn: bool | None
    unresolved: str | None
    at: Place
    target: int | None = None
    objects: tuple[Place, ...] | None = None
    left: dict | None = None
    right: dict | None = None
    eye_objects: tuple[tuple[str, tuple[Place, ...]], ...] = ()


def _place(entry: dict, fields: Fields, field: str) -> Place:
    """A position's four numbers. Its polar angle is wl-xcon's `direction`
    until its rename to `polar` (the requester's decision of 2026-10-30, and
    wl-xcon's PI's of 2026-11-04), so either is read."""
    angle = "polar" if "polar" in entry else "direction"
    return Place(**{name: fields.read(entry, key, NUMBER, field=f"{field}.{key}")
                    for name, key in (("x", "x"), ("y", "y"), ("eccentricity", "eccentricity"), ("polar", angle))})


def _places(entry: dict, fields: Fields, field: str) -> tuple[Place, ...] | None:
    objects = fields.read(entry, "objects", OBJECTS, field=f"{field}.objects")
    return None if objects is None else tuple(_place(item, fields, f"{field}.objects[]") for item in objects)


def _stimuli(row: dict, fields: Fields) -> tuple[Stimulus, ...] | None:
    entries = fields.read(row, "stimuli", OBJECTS)
    if entries is None:
        return None
    out = []
    for entry in entries:
        eyes = {eye: fields.read(entry, eye, MAPPING, field=f"stimuli[].{eye}") for eye in ("left", "right")}
        eye_objects = []
        for eye, value in eyes.items():
            objects = None if value is None else _places(value, fields, f"stimuli[].{eye}")
            if objects is not None:
                eye_objects.append((eye, objects))
        out.append(Stimulus(
            name=fields.read(entry, "name", TEXT, field="stimuli[].name"),
            kind=fields.read(entry, "kind", TEXT, field="stimuli[].kind"),
            frame=fields.read(entry, "frame", WHOLE, field="stimuli[].frame"),
            off=fields.read(entry, "off", WHOLE, field="stimuli[].off"),
            changed=fields.read(entry, "changed", TEXTS, field="stimuli[].changed"),
            drawn=fields.read(entry, "drawn", BOOLEAN, field="stimuli[].drawn"),
            unresolved=fields.read(entry, "unresolved", TEXT, field="stimuli[].unresolved"),
            at=_place(entry, fields, "stimuli[]"), target=fields.read(entry, "target", WHOLE, field="stimuli[].target"),
            objects=_places(entry, fields, "stimuli[]"), left=eyes["left"], right=eyes["right"],
            eye_objects=tuple(eye_objects)))
    return tuple(out)

# What a line may carry beyond its six, and how each is read.
OPTIONAL: dict[str, Kind] = {
    "counting": TEXT, "shown": BOOLEAN, "repeat_of": WHOLE, "at_once": BOOLEAN, "previous": TEXT,
    "drummed": BOOLEAN, "drumming": TEXT, "aids": TEXTS,  # engine build C2a
    "structure": TEXT, "overridden": TEXTS,  # C2b
    "drawn": DRAWN, "live": TEXTS, "eased": TEXTS, "planned": PLANNED,  # C3
    "fluid_ml": NUMBER,  # XC-026
}


@dataclasses.dataclass(frozen=True, slots=True)
class RigTrial:
    number: int  # the stream's TRIAL_NUMBER: `trial_number`, or `index` before XC-155
    index: int
    outcome: str
    block: str
    condition: str
    params: dict
    run: int | None = None  # wl-xcon's, from 0; None before its slice b3a-1
    counting: str | None = None
    shown: bool | None = None
    repeat_of: int | None = None  # the `index`, in this run, of the trial it shows again
    at_once: bool | None = None
    previous: str | None = None
    drummed: bool | None = None
    drumming: str | None = None
    aids: tuple[str, ...] = ()
    structure: str | None = None
    overridden: tuple[str, ...] = ()
    drawn: dict | None = None
    live: tuple[str, ...] = ()
    eased: tuple[str, ...] = ()
    planned: Planned | None = None
    fluid_ml: float | None = None
    stimuli: tuple[Stimulus, ...] | None = None
    carries: frozenset[str] = frozenset()


@dataclasses.dataclass(frozen=True, slots=True)
class RigRecord:
    trials: tuple[RigTrial, ...]
    problems: tuple[str, ...]


def read_rig_trials(session_dir: Path, subject: str) -> RigRecord | None:
    """This subject's trials from `<session>/xcon/trials.jsonl`, or `None`
    when the session has no such record."""
    path = Path(session_dir) / XCON_DIRNAME / RECORD_NAME
    if not path.is_file():
        return None
    trials, problems = [], []
    fields = Fields(("trial line", "trial lines"))
    for number_of_line, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {number_of_line}: not JSON")
            continue
        if not isinstance(row, dict) or any(field not in row for field in _FIELDS):
            problems.append(f"line {number_of_line}: missing one of {', '.join(_FIELDS)}")
            continue
        if not isinstance(row["index"], int) or isinstance(row["index"], bool) or not isinstance(row["params"], dict):
            problems.append(f"line {number_of_line}: index is not an integer or params is not an object")
            continue
        if row["subject"] != subject:
            continue
        if "trial_number" in row:
            number = row["trial_number"]
            if not isinstance(number, int) or isinstance(number, bool):
                problems.append(f"line {number_of_line}: trial_number is not an integer")
                continue
        elif "run" in row:
            problems.append(f"line {number_of_line}: it names a run but carries no trial_number, so it has no "
                            "number across the session and joins nothing")
            continue
        else:
            number = row["index"]
        trials.append(RigTrial(number=number, index=row["index"], outcome=str(row["outcome"]),
                               block=str(row["block"]), condition=str(row["condition"]), params=row["params"],
                               run=fields.read(row, "run", WHOLE),
                               **{name: fields.read(row, name, kind) for name, kind in OPTIONAL.items()},
                               stimuli=_stimuli(row, fields),
                               carries=frozenset(name for name in (*OPTIONAL, "stimuli") if name in row)))
    return RigRecord(trials=tuple(trials), problems=(*problems, *fields.problems()))
