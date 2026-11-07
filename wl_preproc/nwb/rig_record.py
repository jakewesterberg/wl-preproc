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

**Where each trial's stimuli were, and what each run owed** (design spec
`2026-11-07-rig-record-fields-design.md` sections 4 and 5): `stimuli` and
`stimulus_objects`, from the trial lines (`nwb/stimuli.py`), and
`unopened_blocks`, `shortfalls` and `condition_counts`, from the run rows
(`nwb/run_record.py`), by `add_rig_tables`, which makes the module if the
session has no record of its own.

Written only for a session wl-xcon ran (`gather.py::_rig_record`)."""

from __future__ import annotations

import json
import math

from pynwb import NWBFile
from pynwb.core import DynamicTable

from wl_preproc.nwb.columns import column


DESCRIPTION = ("What wl-xcon's record says the session was: the kind it was run as, the display calibration it "
               "opened with, the warnings it accepted, its training records, the changes it applied and the controls "
               "it was sent, where each trial's stimuli were, and what each run owed. Carried as the rig wrote "
               "them.")

_POSITION = (
    ("x_deg", "Its cyclopean per-axis position across, degrees, atan(X/D) (NaN if none). Through the stereoscope "
              "no image is drawn there: each eye's is offset by half its disparity and the vergence offset."),
    ("y_deg", "Its cyclopean per-axis position up, degrees, atan(Y/D) (NaN if none)."),
    ("eccentricity_deg", "Its true angle from straight ahead, degrees of visual angle (NaN if none)."),
    ("polar_deg", "Its polar angle, degrees counter-clockwise from rightward in [0, 360) (NaN at eccentricity 0, "
                  "or if none)."),
)
# Each table `add_rig_tables` may add, with its description and its columns'.
TABLES: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "stimuli": (
        "Each stimulus a trial put on the screen, or changed while it was up, for at least one frame, from the "
        "rig's record (xcon/trials.jsonl, wl-xcon's XC-399), in the order they took effect. Frames, not times.",
        (("trial_id", "The trial."),
         ("entry", "Its order among the trial's entries, from 0."),
         ("stimulus", "The stimulus's name in its task (a table's own name is `name`, so not that)."),
         ("kind", "show: a Show put it up; update: an Update changed it while it was up."),
         ("frame", "The trial's frame from which it was so, from 1 at the trial's first (-1 if not recorded)."),
         ("off", "The first frame its presentation was no longer on the screen; -1 when it stayed on to the "
                 "trial's end, or not recorded."),
         ("changed", "The fields an update set, comma-joined ('' for a show)."),
         ("drawn", "1 when its appearance is something to draw, 0 for a Blank (a catch trial's stimulus), -1 when "
                   "its appearance is a setting with no value."),
         ("unresolved", "Why a field could not be given ('' when everything resolved)."),
         *_POSITION,
         ("target", "An array's target, its index among its objects (-1 if none)."),
         ("n_objects", "An array's objects, in stimulus_objects (0 for one stimulus)."),
         ("left", "A per-eye stimulus's left-eye position, after the vergence offset, as JSON text ('' if none)."),
         ("right", "A per-eye stimulus's right-eye position, likewise ('' if none)."))),
    "stimulus_objects": (
        "Each object of an array the stimuli table names, by its trial and entry.",
        (("trial_id", "The trial."),
         ("entry", "The array's entry in the stimuli table."),
         ("eye", "'' for an array drawn once, left or right for a per-eye array's ring about that eye's position."),
         ("object", "Its index among the array's objects, from 0."),
         *_POSITION)),
    "unopened_blocks": (
        "Each block a run's end row says ended without running a trial, so the stream never opened it: a plan's "
        "block can be over before it draws one.",
        (("run_number", "The run."),
         ("block", "Its block type."),
         ("ended", "How it ended: rule, time, fall_back, next, back, run_time or run."),
         ("ended_by", "Who moved the run on or back, by kind only ('' for nobody)."),
         ("after_trial", "The trial_id of the run's last trial before it ended (-1 if none)."),
         ("position", "Its place in the run's sequence of blocks, from 0 (-1 if not recorded)."))),
    "shortfalls": (
        "What each condition of a run left unrun, and why, from the run's end row.",
        (("run_number", "The run."),
         ("block", "The block type."),
         ("condition", "The condition."),
         ("trials", "The trials it left unrun (-1 if not recorded)."),
         ("because", "cap (its repeat limit), rig, cleared (by a person), time (its block's), run_time (the run's) "
                     "or designed (a designed block's broken trial not played again)."),
         ("possible_avoidance", "1 when the animal's own failures reached the cap, 0 when not, -1 if not "
                                "recorded."),
         ("after_trial", "The trial_id after which it was left (-1 if none)."),
         ("by_kind", "Who cleared it, by kind only ('' for nobody)."))),
    "condition_counts": (
        "Each condition's counts in each block of a run, from the run's end row. A run that ended in a fault can "
        "leave these one try ahead of the trial lines.",
        (("run_number", "The run."),
         ("block", "The block type."),
         ("condition", "The condition."),
         ("trials", "Its trials, a trial and its drummed repeats one."),
         ("presentations", "Its tries recorded."),
         ("right", "Its right answers."),
         ("wrong", "Its wrong answers."),
         ("right_first", "Its trials right at the first try."),
         ("owed", "What it still owed."),
         ("repeats", "Its repeats."),
         ("moved_past", "Its trials moved past."),
         ("possible_avoidance", "1 when flagged as possible avoidance, 0 when not, -1 if not recorded."),
         ("drummed", "Its drummed tries."),
         ("failing", "Its failed tries in a row while being drummed as the run ended."))),
}


def _time(value: float | None) -> float:
    return math.nan if value is None else value


def _module(nwb: NWBFile):
    """The file's `rig_record` module, made if it is not there yet."""
    if "rig_record" in nwb.processing:
        return nwb.processing["rig_record"]
    return nwb.create_processing_module(name="rig_record", description=DESCRIPTION)


def add_rig_tables(nwb: NWBFile, tables: dict[str, list[dict]]) -> None:
    """Each table of `TABLES` that has a row."""
    for name, (description, columns) in TABLES.items():
        rows = tables.get(name) or []
        if rows:
            _module(nwb).add(DynamicTable(name=name, description=description, columns=[
                column(field, text, [row[field] for row in rows]) for field, text in columns]))


def add_rig_record(nwb: NWBFile, record: dict) -> None:
    """One `session` row, and an `accepted_warnings`, `parameter_changes` or
    `controls` table when the file has any such row."""
    calibration = record["calibration"] or {}
    standard = calibration.get("standard")
    module = _module(nwb)
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
    fields = [change["fields"] for change in changes]
    module.add(DynamicTable(name="parameter_changes", description=(
        "Each change the rig applied to what the animal experienced, from wl-xcon's parameter_changes.jsonl, by its "
        "kind and the act it belongs to, timed by the code it strobed or, for one that strobed nothing, by the rig's "
        "clock fitted to the recording."),
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
            column("kind", "set (a person's value), hand_back, range (a draw's numbers: was and now are draws), "
                           "revert, clear, drum or training; a row written before wl-xcon's engine build C3 is a "
                           "set.", [field.kind for field in fields]),
            column("change", "The act it belongs to, an id a revert's rows share (-1 if not recorded).",
                   [-1 if field.change is None else field.change for field in fields]),
            column("trial_id", "The first trial it applies to (-1 if not recorded).",
                   [-1 if field.trial_number is None else field.trial_number for field in fields]),
            column("went", "Which way it went by the setting's declared meaning: easier or harder ('' where that "
                           "cannot be read, or not recorded).", [field.went or "" for field in fields]),
            column("step", "now minus was, in the setting's unit (NaN where either is not a number).",
                   [math.nan if field.step is None else field.step for field in fields]),
            column("to", "What takes a handed-back setting in the open block: conditions, draw or run ('' "
                         "otherwise).", [field.to or "" for field in fields]),
            column("from_draw", "Whose draw a range changed or a revert restored: task or block ('' otherwise).",
                   [field.from_draw or "" for field in fields]),
            column("block", "That block type, where it is a block's own draw ('' otherwise).",
                   [field.block or "" for field in fields]),
            column("moment", "The act a revert goes back to, 0 for the run's start (-1 otherwise).",
                   [-1 if field.moment is None else field.moment for field in fields]),
            column("time_source", "How its time was found: code, or rig_clock for the rig's clock fit ('' if "
                                  "untimed).", [change["time_source"] for change in changes]),
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
