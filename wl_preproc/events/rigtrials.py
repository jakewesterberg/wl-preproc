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
a designed block's planned place; and the fluid it commanded. Each is optional
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
from wl_preproc.events.rigfields import BOOLEAN, DRAWN, NUMBER, TEXT, TEXTS, WHOLE, Fields, Kind

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
                               carries=frozenset(name for name in OPTIONAL if name in row)))
    return RigRecord(trials=tuple(trials), problems=(*problems, *fields.problems()))
