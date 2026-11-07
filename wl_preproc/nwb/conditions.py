"""What actually ran, by condition and stimulus settings (design spec
`2026-09-29-nwb-publishing-design.md` sections 2.1 and 2.2).

Pure functions over plain data. The trials are this file's own, with ids from
the stream's `TRIAL_NUMBER`; the rig's record (`events/rigtrials.py`) is joined
to them by that number and nothing else. **No guessing**: a trial the record
does not name exactly once gets no condition and no settings, and the notes say
why.

**A condition's own values, apart from drawn and person-held ones** (design
spec `2026-11-07-rig-record-fields-design.md` section 6, the requester's
decision of 2026-11-07): its `settings` and `varying` come only from trials
where the setting was neither drawn at random nor held by a person; beside
them, `drawn` (each drawn setting's sources and a summary of its values) and
`live` (how many trials a person held it on, and a summary). Each condition's
`trials` counts its `eased` ones. Each appears only where some line carries
its field, so a record from before wl-xcon's engine build C3 summarises as
before. **A direction is an arc**: a setting the run's start row names in
`directions` is summarised by the smallest counter-clockwise arc holding its
values, never by its least and greatest."""

from __future__ import annotations

import collections
import json
import math

from wl_preproc.events.rigtrials import RigRecord, RigTrial

# A varying setting that is not a number is summarised by its distinct values,
# at most this many.
MAX_DISTINCT = 20


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _text(value) -> str:
    return json.dumps(value, sort_keys=True)


def join(trials: list[dict], record: RigRecord | None) -> tuple[dict[int, RigTrial], list[str]]:
    """`{trial_id: RigTrial}` for every trial the rig's record names exactly
    once, and the notes on everything that did not join."""
    if record is None:
        return {}, ["no rig trial record (xcon/trials.jsonl)"]
    notes = list(record.problems)
    counts = collections.Counter(trial.number for trial in record.trials)
    by_number = {trial.number: trial for trial in record.trials if counts[trial.number] == 1}
    repeated = sorted(number for number, count in counts.items() if count > 1)
    if repeated:
        notes.append(f"{len(repeated)} trial number(s) appear more than once in the rig record")
    matched = {trial["trial_id"]: by_number[trial["trial_id"]] for trial in trials if trial["trial_id"] in by_number}
    unmatched = len(trials) - len(matched)
    if unmatched:
        notes.append(f"{unmatched} trial(s) have no single line in the rig record")
    return matched, notes


def stream_codes(trials: list[dict], events: list[dict]) -> dict[int, int]:
    """`{trial_id: code}` where exactly one `CONDITION` event was sent inside
    the trial, `[start, stop)`."""
    codes = [(event["time_s"], int(event["condition"])) for event in events
             if event["event_type"] == "CONDITION" and event.get("condition") not in (None, "")]
    out = {}
    for trial in trials:
        inside = [code for time_s, code in codes if trial["start_s"] <= time_s < trial["stop_s"]]
        if len(inside) == 1:
            out[trial["trial_id"]] = inside[0]
    return out


def arc(values: list[float]) -> dict:
    """The smallest arc, counter-clockwise, holding every angle in `values`
    (each taken mod 360 first): the circle less its widest gap between
    neighbouring values, the first from 0 degrees where gaps tie (section
    6's ruling). One value is the arc from it to itself."""
    from wl_preproc.eye.detect.geometry import wrap_360_deg

    angles = sorted({float(wrap_360_deg(value)) for value in values})
    gaps = [(angles[(i + 1) % len(angles)] - angle) % 360.0 for i, angle in enumerate(angles)]
    if len(angles) == 1:
        return {"from": angles[0], "to": angles[0]}
    widest = max(range(len(gaps)), key=lambda i: (gaps[i], -i))
    return {"from": angles[(widest + 1) % len(angles)], "to": angles[widest]}


def _summarise(values: list, direction: bool = False) -> dict:
    if not values:
        return {}
    if all(_is_number(value) for value in values):
        if direction:
            return arc(values)
        return {"min": min(values), "max": max(values)}
    distinct, seen = [], set()
    for value in values:
        if _text(value) not in seen:
            seen.add(_text(value))
            distinct.append(value)
    return {"values": distinct[:MAX_DISTINCT], "n_distinct": len(distinct)}


def _apart(key: str, rig: RigTrial) -> str | None:
    """Why `key`'s value on this trial is not its condition's own: `drawn` or
    `live`; None when it is."""
    if rig.drawn and key in rig.drawn:
        return "drawn"
    if key in rig.live:
        return "live"
    return None


def block_conditions(trials: list[dict], matched: dict[int, RigTrial], codes: dict[int, int],
                     directions: frozenset[str] = frozenset()) -> list[dict]:
    """One entry per condition that ran among `trials` (one block's).

    With the rig's record, by condition name: the settings constant across
    that condition's trials, a summary of each that varied, and trial counts,
    each setting from the trials where it was the condition's own; and, where
    the lines carry them, its drawn and person-held values and its eased
    trials. Without it, by the stream's `CONDITION` number, settings unknown.
    `directions` names the settings summarised as arcs."""
    groups: dict = {}
    for trial in trials:
        rig = matched.get(trial["trial_id"])
        if rig is not None:
            groups.setdefault(("name", rig.condition), []).append((trial, rig))
        elif trial["trial_id"] in codes:
            groups.setdefault(("code", codes[trial["trial_id"]]), []).append((trial, None))
    out = []
    for (kind, label), members in sorted(groups.items(), key=lambda item: (item[0][0], str(item[0][1]))):
        outcomes = collections.Counter(trial["outcome"] or "unknown" for trial, _ in members)
        member_codes = {codes.get(trial["trial_id"]) for trial, _ in members}
        entry = {
            "name": label if kind == "name" else None,
            "code": label if kind == "code" else (member_codes.pop() if len(member_codes) == 1 else None),
            "settings": None,
            "varying": None,
            "trials": {"total": len(members), "by_outcome": dict(sorted(outcomes.items()))},
        }
        if kind == "name":
            rigs = [rig for _, rig in members]
            keys = sorted(set().union(*(rig.params.keys() for rig in rigs)))
            settings, varying, drawn, live = {}, {}, {}, {}
            for key in keys:
                own = [rig.params.get(key) for rig in rigs if _apart(key, rig) is None]
                if own and len({_text(value) for value in own}) == 1:
                    settings[key] = own[0]
                elif own:
                    varying[key] = _summarise([value for value in own if value is not None], key in directions)
                taken = [rig for rig in rigs if _apart(key, rig) == "drawn"]
                if taken:
                    drawn[key] = {"sources": sorted({rig.drawn[key] for rig in taken}, key=str),
                                  **_summarise([rig.params[key] for rig in taken if key in rig.params],
                                               key in directions)}
                held = [rig for rig in rigs if _apart(key, rig) == "live"]
                if held:
                    live[key] = {"trials": len(held), **_summarise(
                        [rig.params[key] for rig in held if key in rig.params], key in directions)}
            entry["settings"], entry["varying"] = settings, varying
            if any("drawn" in rig.carries for rig in rigs):
                entry["drawn"] = drawn
            if any("live" in rig.carries for rig in rigs):
                entry["live"] = live
            if any("eased" in rig.carries for rig in rigs):
                entry["trials"]["eased"] = sum(1 for rig in rigs if rig.eased)
        out.append(entry)
    return out


def trial_columns(trials: list[dict], matched: dict[int, RigTrial], codes: dict[int, int]) -> tuple[list[str], dict[str, list]]:
    """The trials table's `condition` (the name, else the stream's number as
    text, else '') and one list per setting that varies across the matched
    trials: numbers as float (NaN where absent), anything else as JSON text
    ('' where absent)."""
    conditions = []
    for trial in trials:
        rig = matched.get(trial["trial_id"])
        if rig is not None:
            conditions.append(rig.condition)
        elif trial["trial_id"] in codes:
            conditions.append(str(codes[trial["trial_id"]]))
        else:
            conditions.append("")
    rigs = [matched.get(trial["trial_id"]) for trial in trials]
    keys = sorted(set().union(*(rig.params.keys() for rig in rigs if rig is not None)))
    settings = {}
    for key in keys:
        values = [rig.params.get(key) if rig is not None else None for rig in rigs]
        present = [value for value in values if value is not None]
        if len({_text(value) for value in present}) <= 1:
            continue
        if all(_is_number(value) for value in present):
            settings[key] = [math.nan if value is None else float(value) for value in values]
        else:
            settings[key] = ["" if value is None else _text(value) for value in values]
    return conditions, settings
