"""The file's description, made from the same gathered data as the file
(design spec `2026-09-29-nwb-publishing-design.md` section 2): validated
against `contracts/nwb_description.py` and returned as plain JSON, which is
what `nwb.NwbFile.description` stores and what is published beside the file."""

from __future__ import annotations

import datetime
import subprocess
from pathlib import Path

from wl_preproc.contracts.nwb_description import NwbDescription


def _commit() -> str | None:
    """The commit the running code was checked out at, or None when it is
    not a git checkout."""
    try:
        out = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    commit = out.stdout.strip()
    return commit if out.returncode == 0 and len(commit) == 40 else None


def _task(value) -> dict:
    """A block's task type, by code and name. Lab-defined codes (100 and
    up) have no name here; their code stands for it."""
    from wl_preproc.contracts.events import TaskTypeCode

    try:
        name = TaskTypeCode(int(value)).name.lower()
    except (TypeError, ValueError):
        name = str(value)
    return {"code": str(value), "name": name}


def _age_days(date_of_birth: datetime.date | None, session_datetime: datetime.datetime) -> int | None:
    return None if date_of_birth is None else (session_datetime.date() - date_of_birth).days


def describe(data, *, status: str, n_critical: int, checksums: list[dict], built_at: datetime.datetime) -> dict:
    """The description of one built file. `data` is `gather.Gathered`."""
    session, subject, eye = data.session, data.session["subject"], data.eye
    description = {
        "identity": {
            "identifier": session["identifier"],
            "subject": subject["subject_id"],
            "session_id": session["session_id"],
            "session_datetime": session["session_datetime"],
            "rig": session["rig"],
            "montage_id": session["montage_id"],
            "activation_id": session["activation_id"],
            "role": session["role"],
            "supersedes_activation_id": session["supersedes_activation_id"],
            "built_at": built_at,
            "pipeline": {"name": "wl-preproc", "commit": _commit()},
            "status": status,
            "n_critical": n_critical,
        },
        "subject": {
            "species": subject["species"],
            "sex": subject["sex"],
            "date_of_birth": subject["date_of_birth"],
            "age_days": _age_days(subject["date_of_birth"], session["session_datetime"]),
        },
        "data_types": {
            "eye": None if eye is None else {
                "gaze": sorted(side for side, gaze in eye["gaze"].items() if gaze is not None),
                "pupil": sorted(eye["pupil"]),
            },
            "eye_events": None if eye is None else {
                "detectors": sorted({table["name"].rsplit("_", 1)[0] for table in eye["detections"]}),
            },
            "behaviour": {"trials": len(data.trials), "events": len(data.events)},
        },
        "probes": [{
            "serial": probe["serial"],
            "probe_type": probe["probe_type"],
            "insertion_number": probe["insertion_number"],
            "trajectory_id": probe["trajectory_id"],
            "n_electrodes": len(probe["electrodes"]),
            "target": probe["target"],
            "assignment": probe["assignment"],
            "area_from": probe["area_from"],
        } for probe in data.probes],
        "blocks": [{
            "block_id": block["block_id"],
            "works_block_id": block["works_block_id"],
            "task": _task(block["task_type"]),
            "asserted": {"start_s": block["start_s"], "stop_s": block["end_s"]},
            "measured": None if block["measured_start_s"] is None else {
                "start_s": block["measured_start_s"], "stop_s": block["measured_stop_s"]},
            "trials": block["trials"],
            "coverage": {system: {"coverage": verdict, "covered_s": covered}
                         for system, (verdict, covered) in block["coverage"].items()},
            "conditions": block["conditions"],
        } for block in data.blocks],
        "quality": {
            "timing_tier": session["timing_tier"],
            "reference_source": session["clock"]["source"],
            "eye_usable_fraction": (eye or {}).get("usable_fraction") or {"left": None, "right": None},
        },
        "notes": [*data.condition_notes, *data.trial_notes, *data.probe_notes],
        "checksums": {"algorithm": "sha256", "datasets": checksums},
    }
    return NwbDescription.model_validate(description).model_dump(mode="json")
