"""Blocks, trials and task events (design spec
`2026-09-28-nwb-builder-design.md` section 3, `/intervals`)."""

from __future__ import annotations

import json

import numpy as np
from pynwb import NWBFile
from pynwb.epoch import TimeIntervals

from wl_preproc.nwb.columns import column


def _coverage_columns(rows: list[dict], systems: list[str]) -> list:
    """Per-system coverage, two columns a system: its verdict (`full`,
    `partial`, `absent`, or '' where none was computed) and the seconds it
    covered (NaN where none)."""
    columns = []
    for system in systems:
        cover = [row["coverage"].get(system) for row in rows]
        columns.append(column(f"coverage_{system}", f"How much of the interval {system} recorded: full, partial or absent.",
                              [c[0] if c else "" for c in cover]))
        columns.append(column(f"covered_s_{system}", f"Seconds of the interval {system} recorded (NaN where not computed).",
                              [c[1] if c else np.nan for c in cover]))
    return columns


def add_blocks(nwb: NWBFile, blocks: list[dict], systems: list[str]) -> None:
    """`/intervals/blocks`: the activation's blocks, their asserted
    boundaries (`core.Block`) as start and stop, the measured ones
    (`trial.Block`) beside them, and per-system coverage."""
    rows = sorted(blocks, key=lambda row: row["start_s"])
    nwb.add_time_intervals(TimeIntervals(
        name="blocks",
        description=("The activation's blocks: start and stop are the boundaries wl.works asserted "
                     "(core.Block); measured_* are the boundaries decoded from the event codes."),
        columns=[
            column("start_time", "Asserted block start, session seconds.", [r["start_s"] for r in rows]),
            column("stop_time", "Asserted block stop, session seconds.", [r["end_s"] for r in rows]),
            column("block_id", "Block number.", [r["block_id"] for r in rows]),
            column("task_type", "The block's task type.", [r["task_type"] for r in rows]),
            column("works_block_id", "wl.works' own block id ('' until linked).", [r["works_block_id"] or "" for r in rows]),
            column("measured_start_time", "Measured block start, session seconds (NaN if not decoded).",
                   [np.nan if r["measured_start_s"] is None else r["measured_start_s"] for r in rows]),
            column("measured_stop_time", "Measured block stop, session seconds (NaN if not decoded).",
                   [np.nan if r["measured_stop_s"] is None else r["measured_stop_s"] for r in rows]),
            *_coverage_columns(rows, systems),
        ],
    ))


def _setting_columns(rows: list[dict]) -> list:
    """One column per setting that varied across the session's trials
    (design spec `2026-09-29-nwb-publishing-design.md` section 2.2)."""
    keys = sorted(set().union(*(row.get("settings", {}).keys() for row in rows))) if rows else []
    return [column(f"setting_{key}",
                   f"The rig's resolved {key} for the trial (xcon/trials.jsonl): numbers as numbers, anything "
                   "else as JSON text; NaN or '' where the trial has no line in that record.",
                   [row["settings"][key] for row in rows])
            for key in keys]


def add_trials(nwb: NWBFile, trials: list[dict], systems: list[str]) -> None:
    """`/intervals/trials`: trial id, outcome, block, condition, the
    settings that varied, and per-system coverage."""
    rows = sorted(trials, key=lambda row: row["start_s"])
    nwb.trials = TimeIntervals(
        name="trials",
        description=("Trials decoded from the event codes, with their outcome, the condition they ran under, "
                     "the stimulus settings that varied across the session, and per-system coverage."),
        columns=[
            column("start_time", "Trial start, session seconds.", [r["start_s"] for r in rows]),
            column("stop_time", "Trial stop, session seconds.", [r["stop_s"] for r in rows]),
            column("trial_id", "Trial number.", [r["trial_id"] for r in rows]),
            column("outcome", "correct, error, abort, fixation_break or no_response.", [r["outcome"] or "" for r in rows]),
            column("block_id", "The measured block the trial belongs to (-1 if none).",
                   [-1 if r["block_id"] is None else r["block_id"] for r in rows]),
            column("condition", ("The condition the trial ran under: its name in the rig's record "
                                 "(xcon/trials.jsonl), else the CONDITION number sent inside it, else ''."),
                   [r.get("condition", "") for r in rows]),
            *_setting_columns(rows),
            *_coverage_columns(rows, systems),
        ],
    )


def add_conditions(nwb: NWBFile, conditions: list[dict]) -> None:
    """`processing/behavior/conditions`: one row per condition that ran, with
    the settings constant across its trials and those that varied (design
    spec `2026-09-29-nwb-publishing-design.md` section 2.2). Nothing is
    written when no condition is known."""
    if not conditions:
        return
    from pynwb.core import DynamicTable

    from wl_preproc.nwb.eye import behavior_module

    rows = sorted(conditions, key=lambda row: (row["name"] or "", -1 if row["code"] is None else row["code"]))
    behavior_module(nwb).add(DynamicTable(
        name="conditions",
        description=("Every condition that ran in the file's trials: its name in the rig's record and the "
                     "CONDITION number sent for it (-1 where none), the settings constant across its trials "
                     "and a summary of those that varied, both as JSON ('' where the rig's record is absent)."),
        columns=[
            # `condition`, not `name`: a DynamicTable's own `name` attribute
            # would shadow a column called that.
            column("condition", "The condition's name in the rig's record ('' where only its number is known).",
                   [row["name"] or "" for row in rows]),
            column("code", "The CONDITION number sent for it (-1 where none).",
                   [-1 if row["code"] is None else row["code"] for row in rows]),
            column("settings", "The settings constant across its trials, as JSON.",
                   ["" if row["settings"] is None else json.dumps(row["settings"], sort_keys=True) for row in rows]),
            column("varying", "Each setting that varied within it: a range for numbers, else its distinct values, as JSON.",
                   ["" if row["varying"] is None else json.dumps(row["varying"], sort_keys=True) for row in rows]),
            column("n_trials", "How many of the file's trials ran under it.", [row["trials"]["total"] for row in rows]),
        ],
    ))


def add_task_events(nwb: NWBFile, events: list[dict]) -> None:
    """`/intervals/task_events`: every decoded event code.

    **Zero-length intervals, start equal to stop, by necessity.** NWB 2.10's
    `EventsTable` is the right type, but Neurosift, which wl.works opens these
    files in, does not display it (design spec section 12.1, verified
    2026-09-28), and `TimeIntervals` it does. `nwbinspector` flags a stop
    time that does not exceed its start as a best-practice violation, which
    this table knowingly carries."""
    rows = sorted(events, key=lambda row: (row["time_s"], row["event_type"]))
    nwb.add_time_intervals(TimeIntervals(
        name="task_events",
        description=("Every decoded event code, one row each, as a zero-length interval: start_time is the "
                     "event's session time and stop_time equals it (NWB 2.10's EventsTable is not displayed "
                     "by Neurosift)."),
        columns=[
            column("start_time", "Event time, session seconds.", [r["time_s"] for r in rows]),
            column("stop_time", "Equal to start_time: an event is an instant.", [r["time_s"] for r in rows]),
            column("event_type", "The event code's name (contracts/events.py), or CODE_<n> for a task event.",
                   [r["event_type"] for r in rows]),
            column("trial_id", "The trial number the code carried (-1 if none).",
                   [-1 if r.get("trial_id") is None else r["trial_id"] for r in rows]),
            column("block_id", "The block number the code carried (-1 if none).",
                   [-1 if r.get("block_id") is None else r["block_id"] for r in rows]),
            column("condition", "The condition the code carried ('' if none).", [r.get("condition") or "" for r in rows]),
        ],
    ))
