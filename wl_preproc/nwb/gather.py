"""Everything one activation's file is built from, read once (design spec
`2026-09-28-nwb-builder-design.md`).

**The one module in `wl_preproc/nwb/` that reads the database and the raw
ohDPI file.** Everything it returns is plain data -- dicts, lists and arrays
in session seconds, already trimmed to the activation's blocks (section 5) --
so every writer is tested without either."""

from __future__ import annotations

import dataclasses
import datetime
from pathlib import Path

import numpy as np

from wl_preproc.nwb.trim import BlockSet

MASK_LABELS = ("blink", "invalid")
# Wall clocks that disagree by more than this are not trusted to place t = 0
# (section 4.2). Borrowed from wl-sync's CLOCK_TRUST_TOLERANCE_S.
CLOCK_DISAGREEMENT_S = 60.0
# A barcode's value is whole seconds since this instant, off the sync box's
# wall clock: wl-sync's `wl_sync/clock.py::BARCODE_EPOCH`, since its commit
# 3ce66b9 (2026-08-17). RESTATED, not imported: the wl-sync commit this
# repository pins predates that module. `tests/nwb/test_clock.py` pins the
# two equal wherever a newer wl-sync is installed.
BARCODE_EPOCH = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)


class Refused(Exception):
    """The activation cannot be built; the message is the reason (section 10)."""


@dataclasses.dataclass
class Gathered:
    session: dict
    systems: list[str]
    blocks: list[dict]
    trials: list[dict]
    events: list[dict]
    timebase: dict
    eye: dict | None  # None: no ohDPI recording in the session, or no sample of it in the blocks


def _aware_utc(value: datetime.datetime) -> datetime.datetime:
    return value.replace(tzinfo=datetime.timezone.utc) if value.tzinfo is None else value


def _block_set(activation_key: dict, activation: dict, session_key: dict) -> list[dict]:
    from wl_preproc.schema import core, request

    if activation["role"] == "derivative":
        rows = (core.Block & (request.ActivationBlock & activation_key).proj()).to_dicts()
    else:
        montage = (core.Montage & activation_key).fetch1()
        rows = [row for row in (core.Block & session_key).to_dicts()
                if montage["start_s"] <= row["start_s"] < montage["end_s"]]
    return sorted(rows, key=lambda row: row["start_s"])


def identifier_for(activation_key: dict, session_id: str) -> str:
    """`{subject}.{session_id}.montage-{m}.activation-{a}`. A session id is
    the sync box's date and index, not scoped to a subject, so two animals
    can share one (`archive/stage.py::nas_root_for_subject`; the final
    review's C1)."""
    return (f"{activation_key['subject']}.{session_id}.montage-{activation_key['montage_id']}"
            f".activation-{activation_key['activation_id']}")


def readiness(activation_key: dict) -> str | None:
    """`None` when everything the file is built from has been computed for
    the session, else what it is still waiting on (the final review's I4).
    Not a refusal: the stage records nothing and tries again next pass.

    A key of the session that a table's `key_source` holds but the table
    does not is one `populate()` has still to run, or one that errored. A
    file built now would silently leave it out and be recorded as final.
    Every table here writes a row for every key it is given, refusals
    included, so no key waits forever by design."""
    from wl_preproc.schema import consensus, coverage, detect, timebase
    from wl_preproc.schema import eye as eye_schema

    session_key = {k: activation_key[k] for k in ("subject", "session_datetime")}
    if not timebase.TimingProvenance & session_key:
        return "waiting on TimingProvenance"
    for table in (coverage.BlockCoverage, coverage.TrialCoverage, eye_schema.EyeCalibration,
                  detect.EyeValidity, detect.EyeDetection, consensus.DetectorAgreement):
        pending = (table().key_source & session_key) - table.proj()
        if len(pending):
            return f"waiting on {table.__name__}: {len(pending)} key(s) of this session not yet computed"
    return None


def agreement_rows(rows: list[dict], names: dict) -> list[dict]:
    """`DetectorAgreement` rows between registered detectors, by name. A
    NULL score -- the metric undefined, nothing comparable or kappa's 0/0 --
    is written as NaN (the final review's I1)."""
    return [{"detector_a": names[row["paramset_a"]], "detector_b": names[row["paramset_b"]],
             "trace": row["trace"], "metric": row["metric"], "vocabulary": row["vocabulary"],
             "pso_as": row["pso_as"], "value": np.nan if row["value"] is None else float(row["value"]),
             "n_samples_compared": int(row["n_samples_compared"])}
            for row in rows if row["paramset_a"] in names and row["paramset_b"] in names]


def reference_from(first_barcode_value: int, started_at: datetime.datetime) -> dict:
    """Section 4.2: the first barcode's own value places session t = 0 on
    the wall clock, unless it disagrees with the manifest's `started_at` by
    more than `CLOCK_DISAGREEMENT_S`, when `started_at` is used and the
    source says so."""
    barcode_time = BARCODE_EPOCH + datetime.timedelta(seconds=int(first_barcode_value))
    started_at = _aware_utc(started_at)
    difference = (started_at - barcode_time).total_seconds()
    trusted = abs(difference) <= CLOCK_DISAGREEMENT_S
    return {
        "reference_time": barcode_time if trusted else started_at,
        "source": "barcode" if trusted else "manifest",
        "manifest_started_at": started_at,
        "started_at_difference_s": difference,
    }


def _reference_time(session_dir: Path, started_at: datetime.datetime) -> dict:
    from wl_preproc.timebase.segments import session_reference

    reference = session_reference(session_dir)
    return reference_from(min(reference, key=reference.get), started_at)


def _subject(subject_id: str) -> dict:
    from wl_preproc.ingest.landing import SUBJECT_BIRTH_DATE_UNKNOWN
    from wl_preproc.schema import pipeline

    row = (pipeline.subject.Subject & {"subject": subject_id}).fetch1()
    species = [row["species"] for row in (pipeline.subject.Subject.Species & {"subject": subject_id}).to_dicts()]
    birth = row["subject_birth_date"]
    return {
        "subject_id": subject_id,
        "species": str(species[0]) if len(species) else None,
        "sex": row["sex"],
        "date_of_birth": None if birth in (None, SUBJECT_BIRTH_DATE_UNKNOWN) else birth,
    }


def _coverage(table, key_field: str, session_key: dict) -> dict:
    by_item: dict = {}
    for row in (table & session_key).to_dicts():
        by_item.setdefault(row[key_field], {})[row["system"]] = (row["coverage"], float(row["covered_s"]))
    return by_item


def _blocks(block_rows: list[dict], session_key: dict) -> list[dict]:
    from wl_preproc.schema import coverage, pipeline

    measured = {row["block_id"]: row for row in (pipeline.trial.Block & session_key).to_dicts()}
    cover = _coverage(coverage.BlockCoverage, "block_id", session_key)
    return [{
        "block_id": row["block_id"], "start_s": float(row["start_s"]), "end_s": float(row["end_s"]),
        "task_type": row["task_type"], "works_block_id": row["works_block_id"],
        "measured_start_s": None if row["block_id"] not in measured else float(measured[row["block_id"]]["block_start_time"]),
        "measured_stop_s": None if row["block_id"] not in measured else float(measured[row["block_id"]]["block_stop_time"]),
        "coverage": cover.get(row["block_id"], {}),
    } for row in block_rows]


def _trials(blocks: BlockSet, session_key: dict) -> list[dict]:
    from wl_preproc.schema import coverage, pipeline

    block_of = {row["trial_id"]: row["block_id"] for row in (pipeline.trial.BlockTrial & session_key).to_dicts()}
    cover = _coverage(coverage.TrialCoverage, "trial_id", session_key)
    rows = [row for row in (pipeline.trial.Trial & session_key).to_dicts()
            if blocks.contains([row["trial_start_time"]])[0]]
    return [{
        "trial_id": row["trial_id"], "start_s": float(row["trial_start_time"]), "stop_s": float(row["trial_stop_time"]),
        "outcome": row["trial_type"], "block_id": block_of.get(row["trial_id"]),
        "coverage": cover.get(row["trial_id"], {}),
    } for row in rows]


def _events(blocks: BlockSet, session_key: dict) -> list[dict]:
    from wl_preproc.schema import pipeline

    attributes: dict = {}
    for row in (pipeline.event.Event.Attribute & session_key).to_dicts():
        attributes.setdefault((row["event_type"], row["event_start_time"]), {})[row["attribute_name"]] = row["attribute_value"]
    events = []
    for row in (pipeline.event.Event & session_key).to_dicts():
        time_s = float(row["event_start_time"])
        if not blocks.contains_instant([time_s])[0]:
            continue
        extra = attributes.get((row["event_type"], row["event_start_time"]), {})
        events.append({
            "time_s": time_s, "event_type": row["event_type"],
            "trial_id": int(extra["trial_id"]) if extra.get("trial_id") else None,
            "block_id": int(extra["block_id"]) if extra.get("block_id") else None,
            "condition": extra.get("condition") or None,
        })
    return events


def _timebase(session_key: dict, provenance: dict, clock: dict) -> dict:
    from wl_preproc.schema import core, timebase

    clocks = [{field: row[field] for field in ("system", "fit_status", "nominal_rate_hz", "fitted_rate_hz", "drift_ppm", "residual_us_rms")}
              for row in (timebase.SystemTimebase & session_key).to_dicts()]
    for row in clocks:
        for field in ("nominal_rate_hz", "fitted_rate_hz", "drift_ppm", "residual_us_rms"):
            row[field] = np.nan if row[field] is None else float(row[field])
    segments = [{field: row[field] for field in ("system", "file_path", "first_sample", "offset_s", "start_s", "end_s", "n_samples")}
                for row in (core.Segment & session_key).to_dicts()]
    return {
        "provenance": {field: (np.nan if provenance[field] is None else provenance[field])
                       for field in ("tier", "n_systems_aligned", "n_segments", "n_rejected_segments",
                                     "worst_residual_us", "worst_drift_ppm")},
        "clocks": clocks,
        "segments": segments,
        "clock_reference": {
            "source": clock["source"],
            "reference_datetime": clock["reference_time"].isoformat(),
            "manifest_started_at": clock["manifest_started_at"].isoformat(),
            "started_at_difference_s": clock["started_at_difference_s"],
        },
    }


def _edges(times: np.ndarray, segment: dict) -> np.ndarray:
    """Session time of every row, plus the time one sample past the last:
    `edges[stop]` is an exclusive run's stop time."""
    step = (segment["end_s"] - segment["start_s"]) / segment["n_samples"]
    return np.append(times, times[-1] + step)


def _runs_to_intervals(runs, edges: np.ndarray, blocks: BlockSet) -> list[tuple]:
    """`(start_row, stop_row, *rest)` runs, as clipped `(start_s, stop_s, *rest)`."""
    out = []
    for start, stop, *rest in runs:
        out.extend((a, b, *rest) for a, b in blocks.clip(float(edges[start]), float(edges[stop])))
    return out


def _eye(session_key: dict, session_dir: Path, blocks: BlockSet, validity_idx: int, detection_idx: dict) -> dict | None:
    from wl_preproc.eye.detect.labels import true_runs
    from wl_preproc.eye.detect.validity import ValidityParams
    from wl_preproc.eye.ohdpi import read_columns, read_ohdpi
    from wl_preproc.nwb.eye import EYES, PUPIL_COLUMNS
    from wl_preproc.schema import consensus, core, detect, paramset
    from wl_preproc.schema import eye as eye_schema

    segments = (core.Segment & {**session_key, "system": "ohdpi"}).to_dicts()
    if not segments:
        return None
    if len(segments) > 1:
        raise Refused(f"{len(segments)} ohDPI segments: the eye tables assume one (section 4.3)")
    (segment,) = segments
    path = session_dir / "ohdpi" / segment["file_path"]
    recording = read_ohdpi(path)
    offsets = recording.frame_numbers - recording.frame_numbers[0]
    times = eye_schema.row_session_times(segment, offsets)
    edges = _edges(times, segment)
    keep = blocks.contains(times)
    if not keep.any():
        # The tracker started after, or stopped before, these blocks (the
        # final review's I2): nothing to write, and HDF5 cannot chunk it.
        return {"no_samples": True}
    validity_params = ValidityParams(**(paramset.ParamSet & {"paramset_type": "eye_validity",
                                                            "paramset_idx": validity_idx}).fetch1("params"))

    gaze, pupil, calibration, validity, repairs, calibrated = {}, {}, [], {}, {}, []
    for eye in EYES:
        file_eye = eye.capitalize()
        columns = read_columns(path, [f"{file_eye}{name}" for name in PUPIL_COLUMNS])
        pupil[eye] = np.column_stack([columns[f"{file_eye}{name}"] for name in PUPIL_COLUMNS])[keep]
        rows = (eye_schema.EyeCalibration & {**session_key, "eye": eye}).to_dicts()
        map_ = eye_schema._map_from_row(rows[0]) if rows else None
        if map_ is None:
            gaze[eye] = None
            continue
        calibrated.append(eye)
        calibration.append({"eye": eye, **{k: v for k, v in rows[0].items() if k not in ("subject", "session_datetime", "eye")}})
        repaired_gaze, repaired = detect._repaired_gaze(path, file_eye, map_, recording.fs_hz, validity_params)
        gaze[eye] = repaired_gaze[keep]
        repairs[eye] = _runs_to_intervals(true_runs(repaired), edges, blocks)
        mask_runs = (detect.EyeValidity.Run & {**session_key, "eye": eye, "paramset_type": "eye_validity",
                                               "validity_paramset_idx": validity_idx}).to_dicts()
        validity[eye] = _runs_to_intervals([(r["run_start"], r["run_stop"], r["label"]) for r in mask_runs
                                            if r["label"] in MASK_LABELS], edges, blocks)

    names = {idx: name for name, idx in detection_idx.items()}
    detections, sources = [], []
    detection_key = {**session_key, "validity_paramset_type": "eye_validity", "validity_paramset_idx": validity_idx,
                     "paramset_type": "eye_detection"}
    for name, idx in detection_idx.items():
        params = (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": idx}).fetch1("params")
        for trace in ("left", "right", "conjunction"):
            where = {**detection_key, "paramset_idx": idx, "trace": trace}
            master = (detect.EyeDetection & where).to_dicts()
            if not master or master[0]["status"] != "computed":
                continue
            runs = []
            for row in (detect.EyeDetection.Run & where).to_dicts():
                start_s = float(edges[row["run_start"]])
                if row["label"] in MASK_LABELS or not blocks.contains([start_s])[0]:
                    continue
                runs.append({"start_s": start_s, "stop_s": float(edges[row["run_stop"]]), "label": row["label"],
                             **{field: row.get(field) for field in ("amplitude_deg", "peak_velocity_deg_s", "start_x_deg",
                                                                    "start_y_deg", "end_x_deg", "end_y_deg",
                                                                    "direction_deg", "reliability")}})
            detections.append({"name": f"{name}_{trace}",
                               "description": f"{name}, the {trace} trace; eye_detection paramset {idx}: {params}",
                               "runs": runs})
            if trace == "conjunction":
                source_rows = (detect.EyeDetection.Source & where).to_dicts()
                sources.append({"name": f"{name}_source",
                                "description": f"{name}: which eye each stretch of the both-eyes trace came from.",
                                "runs": _runs_to_intervals([(r["source_start"], r["source_stop"], r["source"])
                                                            for r in source_rows], edges, blocks)})

    agreement = agreement_rows(
        (consensus.DetectorAgreement & {**session_key, "validity_paramset_idx": validity_idx}).to_dicts(), names)

    return {"times": times[keep], "gaze": gaze, "pupil": pupil, "calibration": calibration,
            "validity": validity, "repairs": repairs, "detections": detections, "sources": sources,
            "agreement": agreement, "missing_eyes": [eye for eye in EYES if eye not in calibrated]}


def gather(activation_key: dict) -> Gathered:
    """One activation's data, or `Refused` with the reason (section 10)."""
    from wl_preproc.schema import detect, ingest, request, timebase
    from wl_preproc.schema import paramset

    key = {k: activation_key[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")}
    session_key = {k: key[k] for k in ("subject", "session_datetime")}
    activation = (request.Activation & key).fetch1()

    provenance = (timebase.TimingProvenance & session_key).to_dicts()
    if not provenance:
        raise Refused("no TimingProvenance row: the session has no session time yet")
    if provenance[0]["tier"] == "D":
        raise Refused("timing tier D: no trustworthy session time")
    block_rows = _block_set(key, activation, session_key)
    if not block_rows:
        raise Refused(f"no blocks in the {activation['role']} activation's block set")
    blocks = BlockSet.of(block_rows)

    session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
    clock = _reference_time(session_dir, key["session_datetime"])
    detection_idx = detect.register_default_paramsets()
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS

    validity_idx = paramset.register("eye_validity", dataclasses.asdict(DEFAULT_VALIDITY_PARAMS))
    eye = _eye(session_key, session_dir, blocks, validity_idx, detection_idx)
    no_eye_samples = eye is not None and eye.get("no_samples", False)
    if no_eye_samples:
        eye = None

    session_id = session_dir.name
    task_types = sorted({row["task_type"] for row in block_rows})
    description = (f"wl-preproc {activation['role']} NWB for session {session_id}, montage {key['montage_id']}: "
                   f"blocks {', '.join(str(row['block_id']) for row in block_rows)} ({', '.join(task_types)}).")
    if eye is not None and eye["missing_eyes"]:
        description += " No calibration for the " + " and ".join(eye["missing_eyes"]) + " eye, so its gaze is absent."
    if no_eye_samples:
        description += " The eye recording has no sample in these blocks, so the file has no eye data."
    requested_by = (request.Request & {"idempotency_key": activation["request_key"]}).fetch1("requested_by")
    systems = sorted({system for row in _blocks(block_rows, session_key) for system in row["coverage"]})
    return Gathered(
        session={
            "identifier": identifier_for(key, session_id),
            "session_id": session_id,
            "description": description,
            "reference_time": clock["reference_time"],
            "experimenter": requested_by or None,
            "subject": _subject(key["subject"]),
            "clock": clock,
        },
        systems=systems,
        blocks=_blocks(block_rows, session_key),
        trials=_trials(blocks, session_key),
        events=_events(blocks, session_key),
        timebase=_timebase(session_key, provenance[0], clock),
        eye=eye,
    )
