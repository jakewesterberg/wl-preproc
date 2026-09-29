"""Build one activation's NWB file, and record what was written (design spec
`2026-09-28-nwb-builder-design.md` section 2)."""

from __future__ import annotations

import dataclasses
import datetime
from pathlib import Path

from wl_preproc.nwb.checksums import dataset_checksums
from wl_preproc.nwb.describe import describe
from wl_preproc.nwb.eye import add_eye_series, add_eye_tables
from wl_preproc.nwb.eye_events import add_agreement, add_detections, add_sources
from wl_preproc.nwb.gather import Refused, gather, readiness
from wl_preproc.nwb.intervals import add_blocks, add_conditions, add_task_events, add_trials
from wl_preproc.nwb.session import new_file
from wl_preproc.nwb.timebase import add_timebase
from wl_preproc.nwb.validate import inspect_file, n_critical
from wl_preproc.nwb.write import write_atomically


@dataclasses.dataclass
class BuildResult:
    status: str  # written, invalid or refused
    path: Path | None = None
    reason: str = ""
    n_bytes: int | None = None
    identifier: str = ""
    clock: dict | None = None
    findings: list = dataclasses.field(default_factory=list)
    checksums: list = dataclasses.field(default_factory=list)
    # The file's description (design spec
    # `2026-09-29-nwb-publishing-design.md` section 2); None when refused.
    description: dict | None = None
    built_at: datetime.datetime = dataclasses.field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))


def nwb_path(nwb_root: Path, subject: str, session_id: str, identifier: str) -> Path:
    """`{nwb_root}/{subject}/{session_id}/{identifier}.nwb` (section 6). The
    subject scopes it, as it scopes the NAS copy
    (`archive/stage.py::nas_root_for_subject`): two animals can share a
    session id (the final review's C1)."""
    return Path(nwb_root) / subject / session_id / f"{identifier}.nwb"


def _recorded_for_another(activation_key: dict, path: Path) -> dict | None:
    """The key of another activation whose recorded file is `path`, if any."""
    from wl_preproc.schema import nwb as nwb_schema

    own = {k: activation_key[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")}
    for row in (nwb_schema.NwbFile & {"path": str(path)}).keys():
        if row != own:
            return row
    return None


def build(activation_key: dict, nwb_root: Path) -> BuildResult:
    """Gather, write atomically, checksum and inspect one activation's file.
    A refusal writes nothing."""
    try:
        data = gather(activation_key)
    except Refused as refusal:
        return BuildResult(status="refused", reason=str(refusal))
    nwb = new_file(data.session)
    add_blocks(nwb, data.blocks, data.systems)
    add_trials(nwb, data.trials, data.systems)
    add_conditions(nwb, data.conditions)
    add_task_events(nwb, data.events)
    add_timebase(nwb, **data.timebase)
    if data.eye is not None:
        add_eye_series(nwb, data.eye["times"], data.eye["gaze"], data.eye["pupil"])
        add_eye_tables(nwb, data.eye["calibration"], data.eye["validity"], data.eye["repairs"])
        add_detections(nwb, data.eye["detections"])
        add_sources(nwb, data.eye["sources"])
        add_agreement(nwb, data.eye["agreement"])
    path = nwb_path(nwb_root, data.session["subject"]["subject_id"], data.session["session_id"],
                    data.session["identifier"])
    other = _recorded_for_another(activation_key, path)
    if other is not None:
        # Never replace another activation's recorded file (the final
        # review's C1, second line): one subject's two rigs can number the
        # same day's sessions alike.
        return BuildResult(status="refused", reason=f"{path} is already recorded for {other}")
    write_atomically(nwb, path)
    findings = inspect_file(path)
    status = "invalid" if n_critical(findings) else "written"
    checksums = dataset_checksums(path)
    built_at = datetime.datetime.now(datetime.timezone.utc)
    return BuildResult(
        status=status,
        path=path,
        n_bytes=path.stat().st_size,
        identifier=data.session["identifier"],
        clock=data.session["clock"],
        findings=findings,
        checksums=checksums,
        description=describe(data, status=status, n_critical=n_critical(findings), checksums=checksums,
                             built_at=built_at),
        built_at=built_at,
    )


def record(activation_key: dict, result: BuildResult) -> None:
    """One `NwbFile` row and its `Dataset` rows, in one transaction."""
    import datajoint as dj

    from wl_preproc.schema import nwb as nwb_schema

    key = {k: activation_key[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")}
    clock = result.clock or {}
    row = {
        **key,
        "status": result.status,
        "path": str(result.path or ""),
        "n_bytes": result.n_bytes,
        "built_at": result.built_at.astimezone(datetime.timezone.utc).replace(tzinfo=None),
        "nwb_identifier": result.identifier,
        "reference_time": clock["reference_time"].astimezone(datetime.timezone.utc).replace(tzinfo=None) if clock else None,
        "reference_source": clock.get("source"),
        "started_at_difference_s": clock.get("started_at_difference_s"),
        "n_critical": None if result.status == "refused" else n_critical(result.findings),
        "inspector_findings": result.findings or None,
        "description": result.description,
        "reason": result.reason,
    }
    connection = dj.conn()
    with connection.transaction:
        nwb_schema.NwbFile.insert1(row)
        nwb_schema.NwbFile.Dataset.insert({**key, **checksum} for checksum in result.checksums)


def run_stage(nwb_root: Path, freed: list[dict] | None = None) -> tuple[int, list[str]]:
    """The daemon's `_nwb_stage`: every activation without an `NwbFile` row,
    skipping freed sessions and, without recording anything, those whose
    inputs are not all computed yet (`gather.readiness`). Returns
    `(activations recorded, per-activation failures)`, the archive stage's
    shape."""
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema import request

    freed = freed or []
    recorded, errors = 0, []
    for key in (request.Activation - nwb_schema.NwbFile.proj()).keys():
        if {"subject": key["subject"], "session_datetime": key["session_datetime"]} in freed:
            continue
        try:
            if readiness(key) is not None:
                continue
            record(key, build(key, nwb_root))
            recorded += 1
        except Exception as exc:  # one bad activation must not take down the run
            errors.append(f"NwbFile {key}: {exc}")
    return recorded, errors
