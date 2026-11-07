"""What the rig's record says of each run and block beyond its task and
calibration: the plan it played and why it ended, how each block ended, and
what each run owed (design spec `2026-11-07-rig-record-fields-design.md`
section 5).

Pure functions over plain data. The runs and blocks are this file's own,
measured from the recording; the rig's runs are joined to them by number, and
each block end to the measured block whose number is its `block_in_session`.
**A column is written only where some run carries its field**, as a trial's
are. **A disagreement is a note, never a refusal**: the stream stays the
measurement. **A trial index is published as a trial id** (section 2), through
the run's own trial lines."""

from __future__ import annotations

import json

from wl_preproc.events.rigruns import RigRun, RigRuns

# Each runs-table column, by the field of the rig's run it reads (its name in
# `RigRun.carries`), with its description.
RUN_COLUMNS: dict[str, tuple[str, str]] = {
    "plan": ("plan", "The plan the run played, by its name in its task ('' for a plain run of N trials, or if not "
                     "recorded)."),
    "plan_form": ("plan_form", "That plan as declared, as JSON text ('' if none)."),
    "end_reason": ("end_reason", "Why a completed run ended: plan_done, plan_time, stalled or cannot_draw ('' for "
                                 "a run that ended any other way, or if not recorded)."),
    "stop_kind": ("stop_kind", "How the run stopped, as the rig's record says: completed, operator, limit, fault "
                               "and the others ('' if not recorded: its process was killed)."),
    "stopped_because": ("stopped_because", "The rig's sentence for why it stopped ('' if not recorded)."),
    "rig_trials": ("rig_trials", "Its trials as the rig counts them, a trial and its drummed repeats one (-1 if not "
                                 "recorded)."),
    "presentations": ("rig_presentations", "Its presentations as the rig counts them, one per trial line (-1 if "
                                           "not recorded)."),
    "task_hash": ("task_hash", "The content hash of the task file it ran ('' if not recorded, or if the rig named "
                               "the task by its path, as before wl-xcon's engine build C1 of 2026-10-11)."),
    "seed": ("seed", "The seed its random draws came from, as text ('' if not recorded)."),
    "draws": ("draws", "The task's random draws as declared, each with every field, as JSON text; a direction "
                       "draw with equal ends is the whole turn ('' if none)."),
    "generated": ("generated", "The conditions each block type's factorial generated, as JSON text ('[]' if no "
                               "block type of the run makes any, '' if its row does not say)."),
}
BLOCK_COLUMNS: dict[str, str] = {
    "ended": "How the block ended: rule, time, fall_back, next, back, run_time or run ('' if not recorded).",
    "ended_by": "Who moved the run on or back, by kind only: box or member ('' for nobody, or if not recorded).",
    "after_trial": "The trial_id of the run's last trial before it ended (-1 if none, or not recorded).",
    "position": "Its place in the run's sequence of blocks, from 0: a training progression's stage (-1 if not "
                "recorded).",
    "passed_at": "For a training progression's top stage, the trial_id after which its rule was first met (-1 if "
                 "not, or not recorded).",
    "varied": "Each setting that varied in it and how, as JSON text ('' if not recorded).",
    "planned": "A designed block's planned sequence, as JSON text ('' if none).",
    "counts": "Each condition's trials in it, overridden and eased, as JSON text ('' if not recorded).",
}


def _text(value) -> str:
    return "" if value is None else json.dumps(value, sort_keys=True)


def _whole(value: int | None) -> int:
    return -1 if value is None else value


def _flag(value: bool | None) -> int:
    return -1 if value is None else int(value)


def _run_value(field: str, rig: RigRun | None):
    value = None if rig is None else getattr(rig, field)
    if field in ("plan_form", "draws", "generated"):
        return _text(value)
    if field in ("rig_trials", "presentations"):
        return _whole(value)
    if field == "seed":
        return "" if value is None else str(value)
    return "" if value is None else value


class _Trials:
    """A run's trial index as a trial id, through its trial lines, counting
    those it cannot place."""

    def __init__(self, place: dict[tuple[int | None, int], int]) -> None:
        self.place, self.unplaced = place, 0

    def __call__(self, rig: RigRun, index: int | None) -> int:
        if index is None:
            return -1
        found = self.place.get((rig.index, index))
        if found is None:
            self.unplaced += 1
            return -1
        return found


def run_record(runs: list[dict], blocks: list[dict], rig_runs: RigRuns | None,
               place: dict[tuple[int | None, int], int]) -> tuple[dict[str, list[dict]], list[str]]:
    """Sets each of the file's runs' `plan` and `end_reason` and its `record`
    (its runs-table columns), and each measured block's `ended` and `record`;
    returns the rows of the `unopened_blocks`, `shortfalls` and
    `condition_counts` tables, and the notes."""
    by_number = {} if rig_runs is None else {rig.number: rig for rig in rig_runs.runs}
    files = [(run, by_number.get(run["run_number"])) for run in runs]
    carried = set().union(*(rig.carries for _, rig in files if rig is not None))
    trial_id, notes = _Trials(place), []
    tables: dict[str, list[dict]] = {"unopened_blocks": [], "shortfalls": [], "condition_counts": []}
    measured = {block["block_number"]: block for block in blocks}
    for run, rig in files:
        run["plan"] = None if rig is None else rig.plan
        run["end_reason"] = None if rig is None else rig.end_reason
        run["record"] = {column: _run_value(field, rig) for field, (column, _) in RUN_COLUMNS.items()
                         if field in carried}
        if rig is None:
            continue
        number = run["run_number"]
        named = set()
        for end in rig.block_ends:
            fields = {"ended": end.ended or "", "ended_by": end.by_kind or "",
                      "after_trial": trial_id(rig, end.after_trial), "position": _whole(end.position)}
            if end.block_in_session is None:
                tables["unopened_blocks"].append({"run_number": number, "block": end.block or "", **fields})
                continue
            block = measured.get(end.block_in_session)
            if block is None or block["run_number"] != number:
                notes.append(f"run {number}: the rig's record ends block {end.block_in_session}, which the "
                             "recording never opened in it, so that end is on no block")
                continue
            if end.block_in_session in named:
                # The first is kept, as everywhere a number repeats here (the
                # final review's M6).
                notes.append(f"run {number}: the rig's record ends block {end.block_in_session} twice; the first "
                             "end is kept")
                continue
            named.add(end.block_in_session)
            block["ended"] = end.ended
            block["record"] = {**fields, "passed_at": trial_id(rig, end.passed_at), "varied": _text(end.varied),
                               "planned": _text(end.planned), "counts": _text(end.counts)}
        if "block_ends" in rig.carries:
            for block in blocks:
                if block["run_number"] == number and block["block_number"] not in named:
                    notes.append(f"run {number}: block {block['block_number']} has no end in the rig's record")
        tables["shortfalls"] += [
            {"run_number": number, "block": short.block or "", "condition": short.condition or "",
             "trials": _whole(short.trials), "because": short.because or "",
             "possible_avoidance": _flag(short.possible_avoidance), "after_trial": trial_id(rig, short.after_trial),
             "by_kind": short.by_kind or ""} for short in rig.shortfalls]
        tables["condition_counts"] += [
            {"run_number": number, "block": count.block or "", "condition": count.condition or "",
             **{name: _whole(getattr(count, name)) for name in ("trials", "presentations", "right", "wrong",
                                                               "right_first", "owed", "repeats", "moved_past")},
             "possible_avoidance": _flag(count.possible_avoidance), "drummed": _whole(count.drummed),
             "failing": _whole(count.failing)} for count in rig.conditions]
        lines = sum(1 for (run_index, _index) in place if run_index == rig.index)
        if rig.presentations is not None and rig.presentations != lines:
            notes.append(f"run {number}: the rig's end row counts {rig.presentations} presentation(s) and its "
                         f"record holds {lines} trial line(s)")
    if any("block_ends" in rig.carries for _, rig in files if rig is not None):
        for block in blocks:
            block.setdefault("record", {name: "" if name in ("ended", "ended_by", "varied", "planned", "counts")
                                        else -1 for name in BLOCK_COLUMNS})
    if trial_id.unplaced:
        notes.append(f"{trial_id.unplaced} trial index(es) in the rig's run rows name no trial line of their run, "
                     "so they are -1")
    return tables, notes
