"""Everything one activation's file is built from, read once (design spec
`2026-09-28-nwb-builder-design.md`).

**The one module in `wl_preproc/nwb/` that reads the database and the raw
ohDPI file.** Everything it returns is plain data -- dicts, lists and arrays
in session seconds, already trimmed to the activation's runs (section 5) --
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
    # The file's runs, and the measured blocks inside them (design spec
    # `2026-10-01-session-listing-and-run-requests-design.md` section 4).
    runs: list[dict]
    blocks: list[dict]
    trials: list[dict]
    events: list[dict]
    timebase: dict
    eye: dict | None  # None: no ohDPI recording in the session, or no sample of it in the runs
    # Every condition that ran in the file's trials, and why any trial's
    # condition or settings are unknown (design spec
    # `2026-09-29-nwb-publishing-design.md` section 2.1).
    conditions: list[dict] = dataclasses.field(default_factory=list)
    condition_notes: list[str] = dataclasses.field(default_factory=list)
    # Every probe the montage recorded or wl.works reported, and what the
    # file says about any probe it cannot place or join (design spec
    # `2026-09-30-nwb-probes-design.md` sections 3 and 5). See `_probes`.
    probes: list[dict] = dataclasses.field(default_factory=list)
    probe_notes: list[str] = dataclasses.field(default_factory=list)
    # The trials the canonical trial list leaves out: a number strobed again,
    # and one too large to store (design spec
    # `2026-10-01-runs-and-trials-design.md` sections 3.2 and 3.4).
    trial_notes: list[str] = dataclasses.field(default_factory=list)


def _aware_utc(value: datetime.datetime) -> datetime.datetime:
    return value.replace(tzinfo=datetime.timezone.utc) if value.tzinfo is None else value


def _run_set(activation_key: dict, session_key: dict) -> list[dict]:
    """The file's runs (`request.ActivationRun`), measured (`core.Run`), with
    wl.works' id for each and the rig's name for its task (design spec
    `2026-10-01-session-listing-and-run-requests-design.md` section 4)."""
    from wl_preproc.schema import core, request

    numbers = {int(number) for number in (request.ActivationRun & activation_key).to_arrays("run_number")}
    works = {row["run_number"]: row["works_run_id"] for row in (core.RunAssertion & session_key).to_dicts()}
    tasks = {row["run_number"]: row["task"] for row in (core.RunRecord & session_key).to_dicts()}
    rows = [{"run_number": row["run_number"], "start_s": float(row["run_start_time"]),
             "end_s": float(row["run_stop_time"]), "task_type": row["task_type"], "task": tasks.get(row["run_number"]),
             "works_run_id": works.get(row["run_number"]), "closed": bool(row["closed"])}
            for row in (core.Run & session_key).to_dicts() if row["run_number"] in numbers]
    return sorted(rows, key=lambda row: row["start_s"])


def _task_name(code, rig_name: str | None) -> str:
    """The rig's name for a run's task, else its code's name, else the code."""
    from wl_preproc.contracts.events import TaskTypeCode

    if rig_name:
        return rig_name
    try:
        return TaskTypeCode(int(code)).name.lower()
    except (TypeError, ValueError):
        return str(code)


def identifier_for(activation_key: dict, session_id: str) -> str:
    """`{subject}.{session_id}.montage-{m}.activation-{a}`. A session id is
    the sync box's date and index, not scoped to a subject, so two animals
    can share one (`archive/stage.py::nas_root_for_subject`; the final
    review's C1)."""
    return (f"{activation_key['subject']}.{session_id}.montage-{activation_key['montage_id']}"
            f".activation-{activation_key['activation_id']}")


def _paramsets() -> tuple[int, dict[str, int]]:
    """The eye_validity paramset and the eye_detection paramsets a file is
    built from: the registered defaults. Registering is idempotent."""
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS
    from wl_preproc.schema import detect, paramset

    detection_idx = detect.register_default_paramsets()
    return paramset.register("eye_validity", dataclasses.asdict(DEFAULT_VALIDITY_PARAMS)), detection_idx


def readiness(activation_key: dict) -> str | None:
    """`None` when everything the file is built from has been computed for
    the session, else what it is still waiting on (the final review's I4).
    Not a refusal: the stage records nothing and tries again next pass.

    A key of the session that a table's `key_source` holds but the table
    does not is one `populate()` has still to run, or one that errored. A
    file built now would silently leave it out and be recorded as final.
    Every table here writes a row for every key it is given, refusals
    included, so no key waits forever by design. Only the paramsets the
    file reads are waited on: a key of another paramset, one that can only
    ever error say, must not hold every file back."""
    from wl_preproc.schema import consensus, coverage, detect, ephys, timebase
    from wl_preproc.schema import eye as eye_schema

    session_key = {k: activation_key[k] for k in ("subject", "session_datetime")}
    if not timebase.TimingProvenance & session_key:
        return "waiting on TimingProvenance"
    validity_idx, detection_idx = _paramsets()
    detectors = ", ".join(str(idx) for idx in sorted(detection_idx.values()))
    read = {
        detect.EyeValidity: {"validity_paramset_idx": validity_idx},
        detect.EyeDetection: f"validity_paramset_idx = {validity_idx} AND paramset_idx IN ({detectors})",
        consensus.DetectorAgreement: (f"validity_paramset_idx = {validity_idx} AND paramset_a IN ({detectors}) "
                                      f"AND paramset_b IN ({detectors})"),
    }
    # `ProbeCensus` for every SpikeGLX segment of the session, not only the
    # montage's: a segment not yet read has no extent to place it by. A
    # session with no SpikeGLX segment has nothing to wait for.
    for table in (coverage.RunCoverage, coverage.TrialCoverage, ephys.ProbeCensus, eye_schema.EyeCalibration,
                  detect.EyeValidity, detect.EyeDetection, consensus.DetectorAgreement):
        pending = (table().key_source & session_key & read.get(table, {})) - table.proj()
        if len(pending):
            return (f"waiting on {table.__name__}: {len(pending)} key(s) of this session not yet computed, "
                    f"first {pending.keys()[0]}")
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


def _runs(run_rows: list[dict], session_key: dict) -> list[dict]:
    """The file's runs, each with its per-system coverage (`RunCoverage`)."""
    from wl_preproc.schema import coverage

    cover = _coverage(coverage.RunCoverage, "run_number", session_key)
    return [{**row, "coverage": cover.get(row["run_number"], {})} for row in run_rows]


def _blocks(run_rows: list[dict], session_key: dict) -> list[dict]:
    """The measured blocks inside the file's runs (`trial.Block`), each with
    its run, its order there, its block type and whether it closed."""
    from wl_preproc.events.runs import run_of, stored_doubles
    from wl_preproc.schema import pipeline

    attributes: dict = {}
    for row in (pipeline.trial.Block.Attribute & session_key).to_dicts():
        attributes.setdefault(row["block_id"], {})[row["attribute_name"]] = row["attribute_value"]
    out, order = [], {}
    stored = stored_doubles(pipeline.trial.Block & session_key, "block_start_time", "block_stop_time")
    for row in sorted(stored, key=lambda row: row["block_start_time"]):
        run_number = run_of(row["block_start_time"], run_rows)
        if run_number is None:
            continue
        order[run_number] = order.get(run_number, 0) + 1
        found = attributes.get(row["block_id"], {})
        out.append({"block_number": row["block_id"], "run_number": run_number, "block_in_run": order[run_number],
                    "block_type": found.get("block_type"), "start_s": float(row["block_start_time"]),
                    "stop_s": float(row["block_stop_time"]),
                    "closed": None if "closed" not in found else found["closed"] == "1"})
    return out


def _trials(run_rows: list[dict], session_key: dict) -> list[dict]:
    """The trials whose start lies in one of the file's runs, each with its
    run and its measured block."""
    from wl_preproc.events.runs import run_of, stored_doubles
    from wl_preproc.schema import coverage, pipeline

    block_of = {row["trial_id"]: row["block_id"] for row in (pipeline.trial.BlockTrial & session_key).to_dicts()}
    cover = _coverage(coverage.TrialCoverage, "trial_id", session_key)
    trials = []
    for row in stored_doubles(pipeline.trial.Trial & session_key, "trial_start_time", "trial_stop_time"):
        run_number = run_of(row["trial_start_time"], run_rows)
        if run_number is None:
            continue
        trials.append({
            "trial_id": row["trial_id"], "start_s": float(row["trial_start_time"]),
            "stop_s": float(row["trial_stop_time"]), "outcome": row["trial_type"], "run_number": run_number,
            "block_id": block_of.get(row["trial_id"]), "coverage": cover.get(row["trial_id"], {}),
        })
    return trials


def _events(run_rows: list[dict], session_key: dict) -> list[dict]:
    """The task events inside one of the file's runs, its ends included."""
    from wl_preproc.events.runs import event_inside
    from wl_preproc.schema import pipeline

    attributes: dict = {}
    for row in (pipeline.event.Event.Attribute & session_key).to_dicts():
        attributes.setdefault((row["event_type"], row["event_start_time"]), {})[row["attribute_name"]] = row["attribute_value"]
    events = []
    for row in (pipeline.event.Event & session_key).to_dicts():
        time_s = float(row["event_start_time"])
        if not any(event_inside(time_s, run["start_s"], run["end_s"]) for run in run_rows):
            continue
        extra = attributes.get((row["event_type"], row["event_start_time"]), {})
        events.append({
            "time_s": time_s, "event_type": row["event_type"],
            "trial_id": int(extra["trial_id"]) if extra.get("trial_id") else None,
            "block_id": int(extra["block_id"]) if extra.get("block_id") else None,
            "condition": extra.get("condition") or None,
        })
    return events


def _trial_notes(session_key: dict, run_rows: list[dict]) -> list[str]:
    """What the stored trials leave out, for this file's runs (design spec
    `2026-10-01-runs-and-trials-design.md` sections 3.2 and 3.4). Read from
    every strobed `TRIAL_NUMBER`, which `Event` keeps whether or not its trial
    was stored: a number strobed again after its first, and one above
    element-event's smallint `trial_id`."""
    import collections

    from wl_preproc.events.runs import event_inside
    from wl_preproc.schema import pipeline
    from wl_preproc.schema.events import TRIAL_ID_MAX

    strobed = sorted(
        (float(row["event_start_time"]), int(row["attribute_value"]))
        for row in (pipeline.event.Event.Attribute & session_key
                    & {"event_type": "TRIAL_NUMBER", "attribute_name": "trial_id"}).to_dicts())
    counts = collections.Counter(number for _time_s, number in strobed)
    inside = [any(event_inside(time_s, run["start_s"], run["end_s"]) for run in run_rows)
              for time_s, _number in strobed]
    seen, repeated, too_large = set(), set(), 0
    for (_time_s, number), here in zip(strobed, inside, strict=True):
        first = number not in seen
        seen.add(number)
        if here and number > TRIAL_ID_MAX:
            too_large += 1
        elif here and not first:
            repeated.add(number)
    notes = [f"trial number {number} appears {counts[number]} times in the recording; only the first is stored"
             for number in sorted(repeated)]
    if too_large:
        notes.append(f"{too_large} trial(s) numbered above {TRIAL_ID_MAX:,} are not stored: element-event's "
                     "trial_id holds no larger number")
    return notes


def _conditions(trials: list[dict], events: list[dict], runs: list[dict], session_dir: Path,
                subject: str) -> tuple[list[dict], list[str]]:
    """Each trial's condition and the settings that varied, and each run's
    conditions and trial counts, from the rig's own record joined by trial
    number (design spec `2026-09-29-nwb-publishing-design.md` sections 2.1
    and 2.2). Returns the file's conditions and the notes on what did not
    join."""
    import collections

    from wl_preproc.events.rigtrials import read_rig_trials
    from wl_preproc.nwb.conditions import block_conditions, join, stream_codes, trial_columns

    trials.sort(key=lambda trial: trial["start_s"])
    matched, notes = join(trials, read_rig_trials(session_dir, subject))
    codes = stream_codes(trials, events)
    names, settings = trial_columns(trials, matched, codes)
    for position, trial in enumerate(trials):
        trial["condition"] = names[position]
        trial["settings"] = {key: values[position] for key, values in settings.items()}
    for run in runs:
        inside = [trial for trial in trials if trial["run_number"] == run["run_number"]]
        outcomes = collections.Counter(trial["outcome"] or "unknown" for trial in inside)
        run["trials"] = {"total": len(inside), "by_outcome": dict(sorted(outcomes.items()))}
        run["conditions"] = block_conditions(inside, matched, codes)
    return block_conditions(trials, matched, codes), notes


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


def _usable_fraction(times: np.ndarray, stretches: list[tuple]) -> float | None:
    """The share of the file's samples of one eye that no withheld stretch
    covers (design spec `2026-09-29-nwb-publishing-design.md` section 2)."""
    if not len(times):
        return None
    withheld = np.zeros(len(times), dtype=bool)
    for start, stop, *_ in stretches:
        low, high = np.searchsorted(times, [start, stop], side="left")
        withheld[low:high] = True
    return float(1.0 - withheld.mean())


def _rig(session_dir: Path) -> str | None:
    """The rig the session's manifest names, or None if it cannot be read."""
    from wl_preproc.contracts.manifest import SessionManifest
    from wl_preproc.contracts.paths import MANIFEST_FILENAME

    try:
        return SessionManifest.from_yaml((session_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")).rig
    except (OSError, ValueError):
        return None


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
            "agreement": agreement, "missing_eyes": [eye for eye in EYES if eye not in calibrated],
            "usable_fraction": {eye: _usable_fraction(times[keep], validity[eye]) if eye in validity else None
                                for eye in EYES}}


def _electrodes(config_hash: str, probe_type: str) -> list[dict]:
    """One configuration's sites, in the probe model's own frame (um)."""
    from wl_preproc.schema import ephys

    config = {"electrode_config_hash": config_hash, "probe_type": probe_type}
    rows = ((ephys.ElectrodeConfig.Electrode & config) * ephys.ProbeType.Electrode).to_dicts(order_by="electrode")
    return [{"electrode": int(row["electrode"]), "shank": int(row["shank"]), "x": float(row["x_coord"]),
             "y": float(row["y_coord"])} for row in rows]


def _probe(serial: str, probe_type: str | None, report: dict | None, electrodes: list[dict],
           session_key: dict) -> dict:
    """One probe's entry: what the recording says, and what wl.works' report
    of its insertion adds. The label is the latest assignment, else the aim,
    else `unknown` (the requester's decision 1 of 2026-09-30)."""
    from wl_preproc.schema import ephys

    target = assignment = None
    if report is not None:
        if report["target_area"] is not None:
            target = {"area": report["target_area"], "atlas": report["target_atlas"],
                      "atlas_level": int(report["target_atlas_level"])}
        latest = (ephys.AreaAssignment & session_key & {"insertion_number": report["insertion_number"]}).to_dicts(
            order_by="asserted_at DESC", limit=1)
        if latest:
            assignment = {"area": latest[0]["area"], "source": latest[0]["source"],
                          "asserted_at": _aware_utc(latest[0]["asserted_at"])}
    area_from = "assignment" if assignment else "target" if target else "unknown"
    return {
        "serial": serial,
        "probe_type": probe_type,
        "insertion_number": None if report is None else int(report["insertion_number"]),
        "trajectory_id": None if report is None else report["trajectory_id"],
        "target": target,
        "assignment": assignment,
        "area_from": area_from,
        "area": {"assignment": (assignment or {}).get("area"), "target": (target or {}).get("area"),
                 "unknown": "unknown"}[area_from],
        "electrodes": electrodes,
    }


def _probes(key: dict, session_key: dict, run_rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Every probe the SpikeGLX segments under the file's runs recorded, joined to
    wl.works' report of its insertion by serial, and the notes the file
    carries about what could not be placed or joined (design spec
    `2026-09-30-nwb-probes-design.md` sections 3 and 5).

    **Read from the tables the probes stage and `accept()` fill** --
    `ProbeCensus`, `InsertionReport`, `AreaAssignment` -- rather than from
    the linked `ProbeInsertion`/`SegmentConfig`: one path then covers a
    joined probe and one that cannot be, and the linked tables hold the same
    facts, joined for sorting.

    `Refused` when one probe recorded two active-site maps under the file's
    blocks: a bank change should have started a new montage (parent spec
    section 8.3), so the montage is wrong, and a file across it would later
    be sorted across it. **The segments are those the file's own blocks
    overlap, not its whole montage's** (the final review's I1): a derivative
    on one side of the change is recorded through one map and builds, which
    is the remedy section 8.3 names for exactly this case."""
    from wl_preproc.schema import core, ephys

    segments = {
        row["segment_barcode"]: row
        for row in (core.Segment & session_key & {"system": "spikeglx"}).to_dicts()
        if any(row["start_s"] < run["end_s"] and row["end_s"] > run["start_s"] for run in run_rows)
    }
    parts = [part for part in (ephys.ProbeCensus.Probe & session_key).to_dicts(order_by=("segment_barcode", "stream"))
             if part["segment_barcode"] in segments]
    reports = (ephys.InsertionReport & session_key).to_dicts(order_by="insertion_number")
    recorded_anywhere = {serial for serial in (ephys.ProbeCensus.Probe & session_key).to_arrays("probe_serial") if serial}
    probes, notes, by_serial = [], [], {}
    for part in parts:
        if part["probe_serial"]:
            by_serial.setdefault(part["probe_serial"], []).append(part)
        else:
            notes.append(f"{segments[part['segment_barcode']]['file_path']} {part['stream']}: {part['problem']}; the "
                         "probe it recorded is unknown")
    for serial, recorded in by_serial.items():
        maps: dict[tuple, list[str]] = {}
        for part in recorded:
            if part["electrode_config_hash"] is not None:
                maps.setdefault((part["electrode_config_hash"], part["probe_type"]), []).append(
                    segments[part["segment_barcode"]]["file_path"])
        if len(maps) > 1:
            raise Refused(
                f"probe {serial} recorded two active-site maps under this file's runs of montage "
                f"{key['montage_id']}, in "
                + " and in ".join(", ".join(paths) for paths in maps.values())
                + ": a bank change needs a new montage (parent spec section 8.3), and a file across it would be "
                "sorted across it")
        problems = sorted({part["problem"] for part in recorded if part["problem"]})
        electrodes = _electrodes(*next(iter(maps))) if maps else []
        if problems:
            notes.append(f"probe {serial}: {'; '.join(problems)}"
                         + ("" if electrodes else "; it has no electrodes in this file"))
        mine = [report for report in reports if report["probe_serial"] == serial]
        if len(mine) > 1:
            notes.append(f"probe {serial} is reported for insertions "
                         f"{' and '.join(str(report['insertion_number']) for report in mine)}, and which segments "
                         "each covers is not known, so neither is joined and its area is unknown")
        elif not mine:
            notes.append(f"probe {serial} has no report from wl.works, so its insertion and area are unknown")
        probes.append(_probe(serial, recorded[0]["part_number"], mine[0] if len(mine) == 1 else None, electrodes,
                             session_key))
    intan = bool(core.AcquisitionSystem & session_key & {"system": "rhs"})
    unrecorded: dict[str, list[dict]] = {}
    for report in reports:
        if report["probe_serial"] not in recorded_anywhere:
            unrecorded.setdefault(report["probe_serial"], []).append(report)
    for serial, mine in unrecorded.items():
        numbers = " and ".join(str(report["insertion_number"]) for report in mine)
        if intan:
            notes.append(f"probe {serial} (insertion {numbers}) is listed from wl.works' report alone: no SpikeGLX "
                         "recording names it, and an Intan (RHS) header does not name its probe")
            probes.append(_probe(serial, None, mine[0] if len(mine) == 1 else None, [], session_key))
        else:
            notes.append(f"insertion {numbers} names probe {serial}, which no recording in this session names, so "
                         "the report is not joined")
    probes.sort(key=lambda probe: (probe["insertion_number"] is None, probe["insertion_number"] or 0, probe["serial"]))
    return probes, notes


def gather(activation_key: dict) -> Gathered:
    """One activation's data, or `Refused` with the reason (section 10)."""
    from wl_preproc.schema import ingest, request, timebase

    key = {k: activation_key[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")}
    session_key = {k: key[k] for k in ("subject", "session_datetime")}
    activation = (request.Activation & key).fetch1()

    provenance = (timebase.TimingProvenance & session_key).to_dicts()
    if not provenance:
        raise Refused("no TimingProvenance row: the session has no session time yet")
    if provenance[0]["tier"] == "D":
        raise Refused("timing tier D: no trustworthy session time")
    run_rows = _run_set(key, session_key)
    if not run_rows:
        raise Refused(f"no runs in the {activation['role']} activation's run set")
    extent = BlockSet.of(run_rows)
    probes, probe_notes = _probes(key, session_key, run_rows)
    sorted_runs: dict[str, list[int]] = {}
    for row in (request.ActivationProbeRun & key).to_dicts(order_by=("probe_serial", "run_number")):
        sorted_runs.setdefault(row["probe_serial"], []).append(row["run_number"])
    for probe in probes:
        probe["sorted_runs"] = sorted_runs.get(probe["serial"], [])

    session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
    clock = _reference_time(session_dir, key["session_datetime"])
    validity_idx, detection_idx = _paramsets()
    eye = _eye(session_key, session_dir, extent, validity_idx, detection_idx)
    no_eye_samples = eye is not None and eye.get("no_samples", False)
    if no_eye_samples:
        eye = None

    session_id = session_dir.name
    tasks = sorted({_task_name(row["task_type"], row["task"]) for row in run_rows})
    description = (f"wl-preproc {activation['role']} NWB for session {session_id}, montage {key['montage_id']}: "
                   f"runs {', '.join(str(row['run_number']) for row in run_rows)} ({', '.join(tasks)}).")
    if eye is not None and eye["missing_eyes"]:
        description += " No calibration for the " + " and ".join(eye["missing_eyes"]) + " eye, so its gaze is absent."
    if no_eye_samples:
        description += " The eye recording has no sample in these runs, so the file has no eye data."
    requested_by = (request.Request & {"idempotency_key": activation["request_key"]}).fetch1("requested_by")
    run_out = _runs(run_rows, session_key)
    systems = sorted({system for row in run_out for system in row["coverage"]})
    trials = _trials(run_rows, session_key)
    events = _events(run_rows, session_key)
    blocks = _blocks(run_rows, session_key)
    for block in blocks:
        block["n_trials"] = sum(1 for trial in trials if trial["block_id"] == block["block_number"])
    conditions, condition_notes = _conditions(trials, events, run_out, session_dir, key["subject"])
    return Gathered(
        session={
            "identifier": identifier_for(key, session_id),
            "session_id": session_id,
            "session_datetime": _aware_utc(key["session_datetime"]),
            "montage_id": key["montage_id"],
            "activation_id": key["activation_id"],
            "role": activation["role"],
            "supersedes_activation_id": activation["supersedes"],
            "rig": _rig(session_dir),
            "timing_tier": provenance[0]["tier"],
            "description": description,
            "reference_time": clock["reference_time"],
            "experimenter": requested_by or None,
            "subject": _subject(key["subject"]),
            "clock": clock,
        },
        systems=systems,
        runs=run_out,
        blocks=blocks,
        trials=trials,
        events=events,
        timebase=_timebase(session_key, provenance[0], clock),
        eye=eye,
        conditions=conditions,
        condition_notes=condition_notes,
        probes=probes,
        probe_notes=probe_notes,
        trial_notes=_trial_notes(session_key, run_rows),
    )
