"""Runs, blocks, trials and task events (design spec
`2026-09-28-nwb-builder-design.md` section 3, `/intervals`; runs since
`2026-10-01-session-listing-and-run-requests-design.md` section 4)."""

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


def add_runs(nwb: NWBFile, runs: list[dict], systems: list[str]) -> None:
    """`/intervals/runs`: the activation's runs, measured from the recording's
    `RUN_START` and `RUN_END`, with wl.works' id for each and per-system
    coverage."""
    rows = sorted(runs, key=lambda row: row["start_s"])
    nwb.add_time_intervals(TimeIntervals(
        name="runs",
        description=("The activation's runs, measured from the recording: start and stop are the run's "
                     "RUN_START and its RUN_END, or its last event when it faulted."),
        columns=[
            column("start_time", "Measured run start, session seconds.", [r["start_s"] for r in rows]),
            column("stop_time", "Measured run stop, session seconds.", [r["end_s"] for r in rows]),
            column("run_number", "The run's number in the session.", [r["run_number"] for r in rows]),
            column("works_run_id", "wl.works' id for the run (its animal_session_run; '' if none).",
                   [r["works_run_id"] or "" for r in rows]),
            column("task_code", "The run's task code (0 until wl-xtasks allocates one).",
                   [int(r["task_type"]) for r in rows]),
            column("task", "The rig's name for the run's task ('' if its record names none).",
                   [r["task"] or "" for r in rows]),
            column("closed", "Whether a RUN_END arrived.", [bool(r["closed"]) for r in rows]),
            *_coverage_columns(rows, systems),
        ],
    ))


def add_blocks(nwb: NWBFile, blocks: list[dict]) -> None:
    """`/intervals/blocks`: the measured blocks inside the activation's runs,
    each with its run, its block type and whether it closed. None when the
    runs hold no block."""
    rows = sorted(blocks, key=lambda row: row["start_s"])
    if not rows:
        return
    nwb.add_time_intervals(TimeIntervals(
        name="blocks",
        description=("The measured blocks inside the file's runs: consecutive trials under one block type, "
                     "from BLOCK_START to BLOCK_END, or to the block's last event when its run faulted."),
        columns=[
            column("start_time", "Measured block start, session seconds.", [r["start_s"] for r in rows]),
            column("stop_time", "Measured block stop, session seconds.", [r["stop_s"] for r in rows]),
            column("block_number", "The block's number in the session, as BLOCK_START strobed it.",
                   [r["block_number"] for r in rows]),
            column("run_number", "The run the block is inside.", [r["run_number"] for r in rows]),
            column("block_in_run", "The block's order in its run, from 1.", [r["block_in_run"] for r in rows]),
            column("block_type", "The rig's name for the block's type ('' if its record names none).",
                   [r["block_type"] or "" for r in rows]),
            column("closed", "1 when a BLOCK_END arrived, 0 when not, -1 when never recorded.",
                   [-1 if r["closed"] is None else int(r["closed"]) for r in rows]),
            column("n_trials", "The block's trials in this file.", [r["n_trials"] for r in rows]),
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
            column("run_number", "The run the trial belongs to.", [r["run_number"] for r in rows]),
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
