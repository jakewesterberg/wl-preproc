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

Written only for a session wl-xcon ran (`gather.py::_rig_record`)."""

from __future__ import annotations

import json

from pynwb import NWBFile
from pynwb.core import DynamicTable

from wl_preproc.nwb.columns import column


def add_rig_record(nwb: NWBFile, record: dict) -> None:
    """One `session` row, and an `accepted_warnings` table when the session
    accepted any warning."""
    calibration = record["calibration"] or {}
    standard = calibration.get("standard")
    module = nwb.create_processing_module(
        name="rig_record",
        description=("What wl-xcon's record says the session was: the kind it was run as, the display calibration "
                     "it opened with, and the warnings it accepted. Carried as the rig wrote them."),
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
    if not warnings:
        return
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
