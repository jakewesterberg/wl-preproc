"""`processing/rig_record`: what wl-xcon's record says the session was (design
spec `2026-10-14-session-kind-and-warnings-design.md` section 5).

**Carried, never acted on.** The kind the session was run as (training,
piloting or recording) and the display calibration it opened with, one row in
`session`; each warning it accepted, a row in `accepted_warnings`, by its
code, its sentence, the kinds it is acceptable in, how and for which run it
was accepted, and the rig's local time, its bookkeeping rather than a time on
the session's clock. **The file names no one who accepted a warning** (section
0, decision 3): this repository's database keeps that, and the listing gives
wl.works its own member's name and account.

**Its training records** (design spec
`2026-10-18-training-records-and-backup-clock-design.md` section 3.2): each live
change the rig applied, in `parameter_changes`, and each console control, in
`controls`, at its session time where it was timed, with its values and its
detail as JSON text. The file gives only the kind of actor, never a name,
account, issuer or token (its decision 6).

Written only for a session wl-xcon ran (`gather.py::_rig_record`)."""

from __future__ import annotations

import json
import math

from pynwb import NWBFile
from pynwb.core import DynamicTable

from wl_preproc.nwb.columns import column


def _time(value: float | None) -> float:
    return math.nan if value is None else value


def add_rig_record(nwb: NWBFile, record: dict) -> None:
    """One `session` row, and an `accepted_warnings`, `parameter_changes` or
    `controls` table when the file has any such row."""
    calibration = record["calibration"] or {}
    standard = calibration.get("standard")
    module = nwb.create_processing_module(
        name="rig_record",
        description=("What wl-xcon's record says the session was: the kind it was run as, the display calibration "
                     "it opened with, the warnings it accepted, and its training records, the changes it applied "
                     "and the controls it was sent. Carried as the rig wrote them."),
    )
    module.add(DynamicTable(name="session", description="The session's kind and display calibration.", columns=[
        column("session_kind", "training, piloting or recording ('' if not recorded).",
               [record["session_kind"] or ""]),
        column("calibration_id", "The display calibration's id ('' if not recorded).", [calibration.get("id") or ""]),
        column("calibration_standard", "1 for the sRGB standard nobody measured, 0 for a measured calibration, "
                                       "-1 if not recorded.", [-1 if standard is None else int(standard)]),
        column("calibration_measured_on", "The day it was measured, YYYY-MM-DD ('' for the standard, or if not "
                                          "recorded).", [calibration.get("measured_on") or ""]),
        column("calibration_cones", "How its cone colours became light, as the rig gave it, as JSON ('' if not "
                                    "recorded).",
               ["" if calibration.get("cones") is None else json.dumps(calibration["cones"], sort_keys=True)]),
    ]))
    warnings = record["warnings"]
    if warnings:
        _add_warnings(module, warnings)
    if record["parameter_changes"]:
        _add_changes(module, record["parameter_changes"])
    if record["controls"]:
        _add_controls(module, record["controls"])


def _add_warnings(module, warnings: list[dict]) -> None:
    module.add(DynamicTable(name="accepted_warnings", description="Each warning the session accepted.", columns=[
        column("code", "The warning's code.", [warning["code"] for warning in warnings]),
        column("detail", "Its sentence ('' if none).", [warning["detail"] or "" for warning in warnings]),
        column("accepted_in", "The session kinds it is acceptable in, comma-joined.",
               [",".join(warning["accepted_in"]) for warning in warnings]),
        column("how", "How it was accepted: open, start or --accept-warnings ('' if not recorded).",
               [warning["how"] or "" for warning in warnings]),
        column("run_number", "The run it was accepted for (-1: at the session's open).",
               [-1 if warning["run_number"] is None else warning["run_number"] for warning in warnings]),
        column("accepted_at_local", "When, in the rig's local time: its bookkeeping, not a time on the session's "
                                    "clock ('' if not recorded).", [warning["at_local"] or "" for warning in warnings]),
    ]))


def _add_changes(module, changes: list[dict]) -> None:
    module.add(DynamicTable(name="parameter_changes", description=(
        "Each live change the rig applied, from wl-xcon's parameter_changes.jsonl, timed by the code it strobed."),
        columns=[
            column("session_time", "When, in session seconds: its code's time (NaN if untimed).",
                   [_time(change["session_time"]) for change in changes]),
            column("run_number", "The run it was applied in (-1: none, or a run the recording did not measure).",
                   [-1 if change["run_number"] is None else change["run_number"] for change in changes]),
            column("setting", "The setting it changed.", [change["name"] for change in changes]),
            column("was", "Its old value, as JSON text ('' if the row has none).",
                   [change["was"] or "" for change in changes]),
            column("now", "Its new value, as JSON text ('' if the row has none).",
                   [change["now"] or "" for change in changes]),
            column("sequence", "wl-xcon's number for the change (-1 if the row has none).",
                   [-1 if change["sequence"] is None else change["sequence"] for change in changes]),
            column("by_kind", "Who made it, by kind only: box or member ('' if not recorded).",
                   [change["by_kind"] or "" for change in changes]),
        ]))


def _add_controls(module, controls: list[dict]) -> None:
    module.add(DynamicTable(name="controls", description=(
        "Each console control the rig was sent, from wl-xcon's controls.jsonl, timed by the code it strobed or, "
        "for one that strobed nothing, by the rig's clock fitted to the recording."), columns=[
            column("session_time", "When, in session seconds (NaN if untimed).",
                   [_time(control["session_time"]) for control in controls]),
            column("time_source", "How its time was found: code, or rig_clock for the rig's clock fit ('' if "
                                  "untimed).", [control["time_source"] or "" for control in controls]),
            column("rig_at", "The rig's own instant, POSIX seconds on its wall clock: its bookkeeping, not a time "
                             "on the session's clock (NaN if none).", [_time(control["at"]) for control in controls]),
            column("kind", "The control: pause, resume, mark, note, schedule, cancel, scheduled_stop, stop, end or "
                           "reward, as written.", [control["kind"] for control in controls]),
            column("run_number", "The run it happened in (-1: none, or a run the recording did not measure).",
                   [-1 if control["run_number"] is None else control["run_number"] for control in controls]),
            column("trial_index", "Its trial within that run, from 0, as wl-xcon counts them: the one it happened "
                                  "in or, between trials, the one about to run; not a trial number (-1 if not "
                                  "recorded).",
                   [-1 if control["trial_index"] is None else control["trial_index"] for control in controls]),
            column("detail", "The control's own fields, as JSON text.", [control["detail"] for control in controls]),
            column("by_kind", "Who sent it, by kind only: box or member ('' for nobody).",
                   [control["by_kind"] or "" for control in controls]),
        ]))
