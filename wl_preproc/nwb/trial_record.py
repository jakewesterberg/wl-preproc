"""What the rig's record says of each trial beyond its condition and settings,
as trials-table columns (design spec `2026-11-07-rig-record-fields-design.md`
section 3).

Pure functions over plain data, beside `nwb/conditions.py`: the trials are this
file's own, joined to the rig's lines by trial number. **A column is written
only where some line carries its field**, as a setting column is written only
where it varies, so a record from before wl-xcon's engine build C2a gains its
fluid alone, the one such field its lines carried; the six planned-place
columns only where a designed block ran. A trial with no line has -1, NaN or
'' in each, as each description says.

**A trial index is published as a trial id** (section 2): the rig names the
trial a repeat shows again by its `index` in its run; the column gives that
trial's `trial_id`, the stream's `TRIAL_NUMBER`, so a reader joins without
the rig's counting."""

from __future__ import annotations

import json
import math

from wl_preproc.events.rigtrials import RigRecord, RigTrial

_NONE = "-1 where the trial has no line in the rig's record or its line says none"

# Each column a line's field gives, by that field's name, with its description.
COLUMNS: dict[str, str] = {
    "counting": ("What the trial did to its condition's count, as the rig's record (xcon/trials.jsonl) says: "
                 "counted, spent, owed or unpaid. What it paid, never its answer, which outcome is ('' if none)."),
    "shown": f"1 when the trial's stimulus appeared, 0 when not; {_NONE}.",
    "repeat_of": f"The trial_id of the trial this one shows again; {_NONE}, or no line of its run is that trial.",
    "at_once": f"1 when it came straight after the trial it shows again, 0 when not; {_NONE}.",
    "previous": "The condition of the trial before it in its block, an aborted one included ('' if none).",
    "drummed": f"1 when it is a drum's repeat, 0 when not; {_NONE}.",
    "drumming": "Whether drumming was on as it was drawn: block or run ('' if not).",
    "aids": "The training aids on as it was drawn, comma-joined: clear, drumming ('' if none).",
    "structure": "The trial structure it played: its condition's or the task's default ('' if not recorded).",
    "overridden": "The values its condition set that a person's live edit replaced, comma-joined ('' if none).",
    "drawn": ("Each setting drawn at random for it, and where the range came from: task, block, condition or "
              "the id of the live change that put it in force, as JSON text ('' if none)."),
    "live": "The settings a person held at a value for it, comma-joined ('' if none).",
    "eased": "The settings whose live change kept it out of moving up, comma-joined ('' if none).",
    "fluid_ml": "The fluid it commanded, ml (NaN if not recorded).",
}
PLANNED_COLUMNS: dict[str, str] = {
    "planned_cycle": f"A sequence-balanced block's planned cycle for it, from 0; {_NONE}.",
    "planned_position": f"Its planned position in that cycle, from 0; {_NONE}.",
    "planned_lead_in": f"1 when it is in its cycle's lead-in, which only precedes what follows, 0 when not; {_NONE}.",
    "planned_run": f"A priming block's planned run of one value for it, from 0; {_NONE}.",
    "planned_switch": f"1 when it is its run's first, 0 when not; {_NONE}.",
    "planned_value": "The value that run plans, as JSON text ('' if none).",
}


def _flag(value: bool | None) -> int:
    return -1 if value is None else int(value)


def _whole(value: int | None) -> int:
    return -1 if value is None else value


def _joined(values: tuple[str, ...]) -> str:
    return ",".join(sorted(values))


def _text(value) -> str:
    return "" if value is None else json.dumps(value, sort_keys=True)


def places(record: RigRecord | None) -> dict[tuple[int | None, int], int]:
    """`{(run, index): trial_number}` for every line of the record."""
    return {} if record is None else {(trial.run, trial.index): trial.number for trial in record.trials}


def _value(name: str, rig: RigTrial | None, place: dict, unplaced: list[int]):
    if name == "fluid_ml":
        return math.nan if rig is None or rig.fluid_ml is None else rig.fluid_ml
    if rig is None:
        return -1 if name in ("shown", "repeat_of", "at_once", "drummed") else ""
    if name == "repeat_of":
        if rig.repeat_of is None:
            return -1
        found = place.get((rig.run, rig.repeat_of))
        if found is None:
            unplaced.append(rig.number)
            return -1
        return found
    if name in ("shown", "at_once", "drummed"):
        return _flag(getattr(rig, name))
    if name in ("aids", "overridden", "live", "eased"):
        return _joined(getattr(rig, name))
    if name == "drawn":
        return _text(rig.drawn)
    value = getattr(rig, name)
    return "" if value is None else value


def _planned(name: str, rig: RigTrial | None):
    planned = None if rig is None else rig.planned
    if name == "planned_value":
        return "" if planned is None or planned.value is None else _text(planned.value)
    field = name.removeprefix("planned_")
    value = None if planned is None else getattr(planned, field)
    return _flag(value) if field in ("lead_in", "switch") else _whole(value)


def record_columns(trials: list[dict], matched: dict[int, RigTrial],
                   place: dict[tuple[int | None, int], int]) -> tuple[dict[str, list], list[str]]:
    """One list per column some matched line carries, in `trials`' order,
    and the notes on what could not be placed."""
    rigs = [matched.get(trial["trial_id"]) for trial in trials]
    carried = set().union(*(rig.carries for rig in rigs if rig is not None))
    columns, unplaced = {}, []
    for name in COLUMNS:
        if name in carried:
            columns[name] = [_value(name, rig, place, unplaced) for rig in rigs]
    if any(rig is not None and rig.planned is not None for rig in rigs):
        for name in PLANNED_COLUMNS:
            columns[name] = [_planned(name, rig) for rig in rigs]
    notes = []
    if unplaced:
        notes.append(f"{len(unplaced)} trial(s) show again a trial no line of their run names, so their repeat_of "
                     f"is -1 (trial {', '.join(str(number) for number in unplaced[:5])}"
                     f"{', ...' if len(unplaced) > 5 else ''})")
    return columns, notes
