"""Manifest, camera sidecar and task file.

Each is written through its real contract model, so the generator cannot emit
something the pipeline would reject — if it could, the fixture would be testing
a format nothing else speaks.
"""

from __future__ import annotations

import datetime
import json
import math
import random
from collections.abc import Callable, Sequence
from pathlib import Path

import yaml
from wl_sync.barcode import encode

from wl_preproc.contracts.manifest import SCHEMA_VERSION, SessionManifest, StartedAtSource
from wl_preproc.contracts.sidecar import BehaviorCameraSidecar, VideoFile
from wl_preproc.synth.faults import mismatch_rig_line
from wl_preproc.synth.recipe import SYNTH_EPOCH, Fault, SessionRecipe
from wl_preproc.synth.timeline import apply_drift
from wl_preproc.synth.truth import GroundTruth

# 500 Hz, raised from 200 Hz in Phase 1c-4. The behaviour camera carries the
# barcode like every other system, so its frame rate is its sampling rate for
# that line — and 200 Hz gave exactly 1.0 samples per 5 ms bit slot, which
# cannot decode. 500 Hz gives 2.5 and is the rate with a published system
# behind it (the ohDPI cameras). See `timebase.extract.min_sample_rate_hz`.
CAMERA_FPS = 500.0

# A fourth distinct tick origin — see syncbox.py. Design spec section 10 asks
# for one per camera system, so that a pipeline which never computes an offset
# cannot pass by accident. It must clear `wl_sync.barcode.IDLE_MIN_US` (0.4 s)
# or the first barcode silently fails to decode for want of a preceding idle:
# that trap has now cost this project twice, in section 4.1 and again in Phase
# 1b, so any new emitter's pre-roll is checked against it rather than chosen.
BCAM_PRE_ROLL_S = 0.85


def write_manifest(path: Path, recipe: SessionRecipe) -> None:
    manifest = SessionManifest(
        schema_version=SCHEMA_VERSION,
        session_id=recipe.session_id,
        subject=recipe.subject,
        rig=recipe.rig,
        started_at=SYNTH_EPOCH,
        started_at_source=StartedAtSource.BEHAVIORAL_CONTROL,
        expected_systems=list(recipe.systems),
        acquisition_build_id=f"blake3:synth{recipe.seed:08x}",
        stimulus_calibration_id="SYNTH-MONITOR@2027-01-01",
        notes="synthetic session",
    )
    path.write_text(manifest.to_yaml(), encoding="utf-8")


def camera_frame_count(recipe: SessionRecipe) -> int:
    """How many frames the camera captures for a recipe, pre-roll included.

    One definition, because `session.py` needs the same number to decide which
    frames a fault drops and a second copy would be free to disagree — the
    reason `SYNTH_EPOCH` and `waveforms.render_traces` are each imported
    rather than restated.
    """
    return int((recipe.duration_s + BCAM_PRE_ROLL_S) * CAMERA_FPS)


def _barcode_digital_line(
    recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float
) -> list[int]:
    """The barcode rendered into one 0/1 sample per frame.

    The frame rate is the sampling rate for this line, so a frame index is a
    time: `wl_sync.barcode.encode` gives the frame's shape and each level is
    held across the frames it spans. Rendering through the codec rather than
    writing the pattern out is the same rule every other emitter follows — the
    codec owns the shape, and a second copy of it here would be free to drift
    from the one the hardware implements.
    """
    line = [0] * camera_frame_count(recipe)
    for value, start_s in truth.barcodes:
        cursor_s = apply_drift(start_s, drift_ppm) + BCAM_PRE_ROLL_S
        for level, duration_us in encode(value):
            start_frame = int(cursor_s * CAMERA_FPS)
            cursor_s += duration_us * 1e-6
            if level:
                stop_frame = min(int(cursor_s * CAMERA_FPS), len(line))
                for frame in range(max(start_frame, 0), stop_frame):
                    line[frame] = 1
    return line


def write_camera_sidecar(
    path: Path,
    recipe: SessionRecipe,
    truth: GroundTruth,
    dropped: Sequence[int] = (),
    drift_ppm: float = 0.0,
) -> None:
    frame_count = camera_frame_count(recipe)
    sidecar = BehaviorCameraSidecar(
        schema_version=1,
        system="bcam0",
        trigger_source="syncbox",
        frame_count=frame_count,
        dropped_frame_ids=list(dropped),
        digital_line=_barcode_digital_line(recipe, truth, drift_ppm),
        frame_rate_hz=CAMERA_FPS,
        video_files=[
            VideoFile(
                path="bcam0_seg000.mp4",
                first_frame_index=0,
                last_frame_index=frame_count - 1,
                codec="h264",
                checksum=f"blake3:synth{recipe.seed:08x}",
            )
        ],
    )
    path.write_text(
        yaml.safe_dump(sidecar.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )


def write_task_file(path: Path, truth: GroundTruth) -> None:
    """Stands in for MonkeyLogic's .bhv2 until the task stack is chosen.

    Carries what the code stream deliberately does not: condition numbers and
    reward volumes. The pipeline joins the two and hard-fails on a trial-count
    mismatch, so the fixture must exercise both halves.
    """
    payload = {
        "format": "synthetic-task-file",
        "version": 1,
        "trials": [
            {
                "trial_id": trial.trial_id,
                "block_id": trial.block_id,
                "start_s": trial.start_s,
                "end_s": trial.end_s,
                "condition": (trial.trial_id % 4) + 1,
                "reward_ms": 120,
                "outcome": "correct",
            }
            for trial in truth.trials
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# The rig's per-trial record, as wl-xcon writes it (`wl_xcon/record.py::
# Recorder.trial`): four contrasts, one fixed orientation and target, and a
# fixation hold that varies WITHIN one condition -- so every branch of the
# builder's condition summary has something to read (design spec
# `2026-09-29-nwb-publishing-design.md` section 13).
RIG_CONTRASTS = (0.10, 0.25, 0.50, 1.00)


def rig_condition(trial_id: int) -> tuple[str, dict]:
    """The synthetic rig's condition name and resolved parameters for a trial."""
    contrast = RIG_CONTRASTS[trial_id % len(RIG_CONTRASTS)]
    hold_s = 0.35 if contrast == 0.50 and (trial_id // len(RIG_CONTRASTS)) % 2 else 0.30
    return f"contrast-{round(contrast * 100)}", {
        "contrast": contrast,
        "orientation_deg": 45.0,
        "fix_hold_s": hold_s,
        "target_xy_deg": [0.0, 5.0],
    }


def _place(x: float, y: float) -> dict:
    """A position as wl-xcon's XC-399 writes one: its true eccentricity and
    polar angle beside its per-axis (x, y), the angle null at the centre."""
    import numpy as np

    from wl_preproc.eye.detect.geometry import eccentricity_polar_deg

    eccentricity, polar = (float(value) for value in eccentricity_polar_deg(np.array([x, y])))
    return {"eccentricity": eccentricity, "direction": None if np.isnan(polar) else polar, "x": x, "y": y}


def _stimuli(index_in_run: int, design: str) -> list[dict]:
    """The fixation point and the target (`rig_condition`'s `target_xy_deg`);
    a run's third trial shows a two-object array beside them, and a plain
    run's trials a `Blank` mask after the fixation point, which wl-xcon's
    `record.py::_stimuli` writes as drawn false."""
    shown = {"kind": "show", "changed": None, "drawn": True, "unresolved": None}
    entries = [{"name": "fix", "frame": 1, "off": 61, **shown, **_place(0.0, 0.0)},
               {"name": "target", "frame": 85, "off": None, **shown, **_place(0.0, 5.0)}]
    if index_in_run == 2:
        entries.append({"name": "search", "frame": 85, "off": None, **shown, **_place(0.0, 0.0), "target": 0,
                        "objects": [_place(8.0, 0.0), _place(-8.0, 0.0)]})
    if design == "plain":
        entries.append({"name": "mask", "frame": 61, "off": 85, **shown, "drawn": False, **_place(0.0, 0.0)})
    return entries


#: Each run's design in a record with `fields` (design spec
#: `2026-11-07-rig-record-fields-design.md` section 10's mix): the stream
#: wraps each block in a run of its own, so the mix is spread over the
#: session's runs -- the first a run of a plan whose block is a factorial
#: played sequence-balanced, the second a plain run, the third a run of a
#: plan whose block primes, which a person moves on from to a block that runs
#: no trial; any later run is plain.
RUN_DESIGNS = ("balanced", "plain", "priming")

#: The appearances the priming block plans, one run of one value each, in
#: wl-xcon's appearance form (accepted 2026-10-30): an appearance-valued
#: setting, `target_appearance`, on each of its trials' lines.
PRIMED = ({"class": "Disc", "fields": {"size": 1.0}}, {"class": "Square", "fields": {"size": 1.0}})


def _design(run_index: int) -> str:
    return RUN_DESIGNS[run_index] if run_index < len(RUN_DESIGNS) else "plain"


def _planned(index: int, design: str) -> dict | None:
    """A designed block's planned place for a trial, as wl-xcon's engine build
    C3 writes it: a sequence-balanced block's cycle, position and lead-in; a
    priming block's run of one value, whether it is that run's first, and the
    value; none in a plain run."""
    if design == "balanced":
        return {"cycle": 0, "position": index, "lead_in": index == 0}
    if design == "priming":
        return {"run": index, "switch": True, "value": PRIMED[index % len(PRIMED)]}
    return None


def _trial_fields(index: int, previous: str | None, design: str = "balanced") -> dict:
    """What wl-xcon's engine builds C2a, C2b and C3 and its XC-399 add to a
    trial line (design spec `2026-11-07-rig-record-fields-design.md` section
    10): each run's first trial its block's first, in a sequence-balanced
    block's lead-in, its second a drum's repeat of its first, straight after
    it and owed, its third held by a person at a contrast that kept it out of
    moving up, with an array beside its target; each trial's planned place by
    its run's design (`_planned`), its hold drawn by the task, and its
    fluid."""
    fields = {"counting": "counted", "shown": True, "repeat_of": None, "at_once": False, "previous": previous,
              "drummed": False, "drumming": None, "aids": [], "structure": "default", "overridden": [],
              "drawn": {"fix_hold_s": "task"}, "live": [], "eased": [], "planned": _planned(index, design),
              "fluid_ml": 0.1, "stimuli": _stimuli(index, design)}
    if index == 1:
        fields.update(counting="owed", repeat_of=0, at_once=True, drummed=True, drumming="block", aids=["drumming"])
    if index == 2:
        fields.update(overridden=["contrast"], live=["contrast"], eased=["contrast"])
    return fields


def write_rig_trials(path: Path, recipe: SessionRecipe, truth: GroundTruth) -> None:
    """Stands in for wl-xcon's `xcon/trials.jsonl`: one JSON object per line,
    per trial, with the subject on every line. In wl-xcon's shape since its
    XC-155: the run (counted from 0, as wl-xcon counts them; one run per
    block), the trial's index within its run, and `trial_number`, counted
    across the session and equal to the stream's TRIAL_NUMBER; and, for a
    record with `fields`, what its later builds add (`_trial_fields`)."""
    fields = recipe.rig_record is not None and recipe.rig_record.fields
    lines, per_run, previous = [], {}, {}
    for position, trial in enumerate(truth.trials, start=1):
        index = per_run.get(trial.block_id, 0)
        per_run[trial.block_id] = index + 1  # a faulted trial still used its index
        if position in recipe.faulted_trials:
            continue  # a trial that faults leaves no line (wl-xcon, 2026-10-01)
        condition, params = rig_condition(trial.trial_id)
        design = _design(trial.block_id - 1)
        if fields and design == "priming":
            params = {**params, "target_appearance": _planned(index, design)["value"]}
        lines.append({
            "index": index,
            "run": trial.block_id - 1,
            "trial_number": trial.trial_id,
            "subject": recipe.subject,
            "outcome": "correct",
            "block": f"block-{trial.block_id}",
            "condition": condition,
            "params": params,
            **(_trial_fields(index, previous.get(trial.block_id), design) if fields else {}),
        })
        previous[trial.block_id] = condition
    if Fault.MISMATCHED_RIG_LINE in recipe.faults:
        lines = mismatch_rig_line(lines)
    path.write_text("\n".join(json.dumps(line, sort_keys=True) for line in lines) + "\n", encoding="utf-8")


# The synthetic rig's anchored wall clock (wl-xcon's `taskd.Session.wall_now`,
# POSIX seconds): it read `SYNTH_EPOCH` plus a lead at session t = 0, runs
# `RIG_DRIFT_PPM` fast against the sync box, and stamps each instant up to
# `RIG_STAMP_JITTER_S` either side of its code. The jitter is the one the
# training records' clock fit is measured against (design spec
# `2026-10-18-training-records-and-backup-clock-design.md` section 10, item 10;
# `events/rigtiming.py::RIG_CLOCK_LIMIT_S`).
RIG_CLOCK_LEAD_S = 12.5
RIG_DRIFT_PPM = 20.0
RIG_STAMP_JITTER_S = 0.005


def rig_clock(recipe: SessionRecipe) -> Callable[[float], float]:
    """The synthetic rig's instant for a session time, its jitter seeded by
    the recipe, so each call stamps the next instant."""
    generator = random.Random(recipe.seed)
    zero = SYNTH_EPOCH.timestamp() + RIG_CLOCK_LEAD_S
    return lambda session_s: (zero + session_s * (1 + RIG_DRIFT_PPM / 1e6)
                              + generator.uniform(-RIG_STAMP_JITTER_S, RIG_STAMP_JITTER_S))


def write_rig_runs(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth) -> None:
    """Stands in for wl-xcon's `xcon/runs.jsonl` and `xcon/config.json`, for a
    recipe whose blocks are wrapped in runs (design spec
    `2026-10-01-session-listing-and-run-requests-design.md` section 2.3).

    In the shape `wl_xcon/record.py::run_row` writes on wl-xcon's `main`
    (read at `0d00a6c`): a start row with the task, and an end row with
    `stopped_because` and `stop_kind`, each naming its 0-based `run`. wl-xcon
    writes the end row on every way out (`taskd.py`, read at `c4ee20d`): a run
    whose block is unclosed faulted, and its end row says so in `taskd.py`'s
    words for a fault; the others are its words for a run that finished its
    blocks. Each row's `at` is the rig's clock (`rig_clock`).

    *Until the training records' design, each row's `at` was its session
    time, which wl-xcon never writes; nothing read it.*"""
    rig = recipe.rig_record
    stamp = rig_clock(recipe)
    rows = []
    for run_index, block in enumerate(truth.blocks):
        spec = recipe.blocks[run_index]
        rows.append({"event": "start", "run": run_index, "at": stamp(block.start_s),
                     "task": spec.task_type.name.lower(),
                     **({} if rig is None else {"calibration": rig.calibration_id}),
                     **(_start_fields(recipe, run_index) if rig is not None and rig.fields else {})})
        faulted = run_index + 1 in recipe.unclosed_blocks
        if faulted:
            rows.append({"event": "end", "run": run_index, "at": stamp(block.end_s),
                         "stopped_because": "fault, session aborted: RuntimeError: a synthetic fault",
                         "stop_kind": "fault"})
        else:
            rows.append({"event": "end", "run": run_index, "at": stamp(block.end_s),
                         "stopped_because": "every block is finished", "stop_kind": "completed"})
        if rig is not None and rig.fields:
            rows[-1].update(_end_fields(recipe, truth, run_index, faulted))
    (xcon_dir / "runs.jsonl").write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
                                         encoding="utf-8")
    config = {"session_id": recipe.session_id, "subject": recipe.subject}
    if recipe.setup_recorded:
        config["setup"] = RIG_SETUP
    if rig is not None:
        config["session_kind"] = rig.session_kind
        config["calibration"] = None if rig.calibration_id is None else {
            "id": rig.calibration_id, "standard": False, "measured_on": "2027-03-01", "cones": RIG_CONES}
        write_rig_warnings(xcon_dir, recipe, truth)
        write_rig_records(xcon_dir, recipe, truth, stamp)
    (xcon_dir / "config.json").write_text(json.dumps(config, sort_keys=True), encoding="utf-8")


def _start_fields(recipe: SessionRecipe, run_index: int) -> dict:
    """What wl-xcon's engine builds C2b and C3 add to a run's start row
    (`taskd.py`'s `run_row("start", ...)`, read at engine-c3 `7ea7913`): its
    plan and the plan as declared, or null for a plain run, which names its
    planned trials instead; its task's content hash; the seed; the task's
    draw of the hold; and what a factorial block made (`_generated`), `[]`
    where no block makes any."""
    design = _design(run_index)
    block = f"block-{run_index + 1}"
    plan = {"balanced": "synthetic", "priming": "priming"}.get(design)
    blocks = {"balanced": [block], "priming": [block, "probe"]}.get(design)
    generated = [] if design != "balanced" else [{
        "block": block, "excluded": [],
        "conditions": [{"name": f"contrast-{round(contrast * 100)}", "values": {"contrast": contrast}, "target": None}
                       for contrast in RIG_CONTRASTS]}]
    return {"plan": plan, "plan_form": None if plan is None else {"class": "Plan", "fields": {"blocks": blocks}},
            "versions": {"task": "sha256:synthetic", "allocation": "synthetic"}, "seed": recipe.seed,
            "draws": {"fix_hold_s": {"class": "Uniform", "fields": {"high": 0.35, "low": 0.3, "repeat": "fresh"}}},
            "generated": generated, "directions": [],
            "trials": recipe.blocks[run_index].n_trials if plan is None else None}


def _end_fields(recipe: SessionRecipe, truth: GroundTruth, run_index: int, faulted: bool) -> dict:
    """What wl-xcon's engine builds C2a, C2b and C3 add to a run's end row,
    counted from the run's own trial lines (`_trial_fields`): its trials, a
    drum's repeat one with the trial it repeats, its presentations, its
    block's end -- a priming run's moved on by a person (`next_block`,
    `taskd.py::_move`) to a block that ran no trial before the plan's time ran
    out -- a shortfall for each clear in it, and each condition's counts."""
    lines = [trial for position, trial in enumerate(truth.trials, start=1)
             if trial.block_id == run_index + 1 and position not in recipe.faulted_trials]
    conditions: dict[str, int] = {}
    for trial in lines:
        name = rig_condition(trial.trial_id)[0]
        conditions[name] = conditions.get(name, 0) + 1
    repeats = 1 if len(lines) > 1 else 0
    block = f"block-{run_index + 1}"
    moved = _design(run_index) == "priming" and not faulted
    ends = [{"block": block, "ended": "run" if faulted else "next" if moved else "rule",
             "by": {"kind": "member", "name": "synth"} if moved else None,
             "after_trial": len(lines) - 1 if lines else None, "block_in_session": run_index + 1,
             "position": 0, "passed_at": None, "varied": {"fix_hold_s": [{"by": "draw"}]},
             "planned": None, "counts": {}}]
    if moved:
        ends.append({"block": "probe", "ended": "run", "by": None, "after_trial": None, "block_in_session": None,
                     "position": 1, "passed_at": None, "varied": {}, "planned": None, "counts": {}})
    first = 1 + sum(spec.n_trials for spec in recipe.blocks[:run_index])
    clears = [change for change in recipe.rig_record.changes
              if change.kind == "clear" and first <= change.trial < first + recipe.blocks[run_index].n_trials]
    return {"trials": len(lines) - repeats, "presentations": len(lines),
            "end_reason": None if faulted else "plan_time" if moved else "plan_done",
            "block_ends": ends,
            "shortfalls": [{"block": change.detail.get("block", block), "condition": change.detail.get("condition"),
                            "trials": change.detail.get("cleared", 0), "because": "cleared",
                            "possible_avoidance": False, "after_trial": change.trial - first, "by": change.by}
                           for change in clears],
            "conditions": [{"block": f"block-{run_index + 1}", "condition": name, "trials": n, "presentations": n,
                            "right": n, "wrong": 0, "right_first": n, "owed": 0, "repeats": 0, "moved_past": 0,
                            "possible_avoidance": False, "drummed": 0, "failing": 0}
                           for name, n in sorted(conditions.items())]}


def write_rig_records(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth,
                      stamp: Callable[[float], float]) -> None:
    """Stands in for wl-xcon's training records as it writes them today,
    `xcon/parameter_changes.jsonl` and `xcon/controls.jsonl`, in the keys
    `wl_xcon/record.py`'s `parameter_change` and `control` write (read at
    wl-xcon's `7398d3e`), `event_code` included (design spec
    `2026-10-18-training-records-and-backup-clock-design.md`, amendment 2). A
    run wraps each block, so an act's run is its trial's block's, from 0, and
    a control's `trial_index` its trial within that run, from 0, as wl-xcon's
    `taskd.py` counts it (the final review's M3). A file is written only when
    it has a row, as wl-xcon appends its first."""
    rig = recipe.rig_record
    run_of = {position: trial.block_id - 1 for position, trial in enumerate(truth.trials, start=1)}
    first_of: dict[int, int] = {}
    for position, trial in enumerate(truth.trials, start=1):
        first_of.setdefault(trial.block_id, position)
    index_in_run = {position: position - first_of[trial.block_id]
                    for position, trial in enumerate(truth.trials, start=1)}
    changes = [{"sequence": sequence, "name": change.name, "was": change.was, "now": change.now, "by": change.by,
                "run": run_of[change.trial], "event_code": change.event_code}
               for sequence, change in enumerate(rig.changes, start=1)]
    if rig.fields:
        changes = _c3_changes(rig, truth, run_of, stamp)
    controls = []
    for control, time in zip(rig.controls, truth.control_times, strict=True):
        at = stamp(time)
        controls.append({"kind": control.kind, "by": control.by, "at": at,
                         "at_local": datetime.datetime.fromtimestamp(at, datetime.timezone.utc).isoformat(),
                         "trial_index": index_in_run[control.trial], "event_code": control.event_code,
                         "run": run_of[control.trial], **control.detail})
    for name, rows in (("parameter_changes.jsonl", changes), ("controls.jsonl", controls)):
        if rows:
            (xcon_dir / name).write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
                                         encoding="utf-8")


def _c3_changes(rig, truth: GroundTruth, run_of: dict[int, int], stamp: Callable[[float], float]) -> list[dict]:
    """Change rows in the shape wl-xcon's engine build C3 writes them
    (`wl_xcon/record.py`'s change row): each its kind, its act's id (its
    place, unless it shares an act's, as a revert's rows do), its instant on
    the rig's clock, the first trial it applies to, the setting's meaning and
    its kind's own fields; a `sequence` only on a row whose act strobed
    `PARAM_CHANGED`, so a clear or a switch carries none."""
    rows, sequence = [], 0
    for place, (change, time) in enumerate(zip(rig.changes, truth.change_times, strict=True), start=1):
        at = stamp(time)
        row = {"change": place if change.change is None else change.change, "kind": change.kind,
               "name": change.name, "was": change.was, "now": change.now, "by": change.by, "at": at,
               "at_local": datetime.datetime.fromtimestamp(at, datetime.timezone.utc).isoformat(),
               "trial_number": change.trial, "run": run_of[change.trial], "meaning": None, "offered": [],
               "odds": None, "event_code": change.event_code, **change.detail}
        if change.event_code is not None:
            sequence += 1
            row["sequence"] = sequence
        rows.append(row)
    return rows


# How a calibration's cone colours became light, in the shape wl-xcon's
# `photometry.py::cone_record` writes it (read at `b2c2850`): its observer as
# `cones.py::Observer.record` gives the CIE 2006 10-degree one, with floats,
# lists and a degree sign, so the real shape is what crosses the database's
# blob and the NWB file's JSON (the session kind's final review's I4). Its
# `luminosity_weights` are placeholders: carried verbatim and read by nothing.
# `via` and `luminance` are null, as wl-xcon writes them where no cone colour
# converts, so the nulls a nested record holds are carried too.
#: The display setup the synthetic rig records in `config.json`, as wl-xcon's
#: `taskd.Session._fixed_config` writes it (its `main` at `957ec98`): this
#: rig in direct view, its panel 58.997 x 33.293 cm at 50 cm (wl-xcon's
#: `tasks/rig.py`), so each eye's field is `atan(half-size / distance)` per
#: axis, as its `geometry.py` computes it (design spec
#: `2026-11-06-eye-lost-data-and-region-design.md` section 7).
RIG_SETUP = {"view": "direct", "viewing_distance_cm": 50.0, "mask_deg": None,
             "half_field_deg": [math.degrees(math.atan(58.997 / 2 / 50.0)),
                                math.degrees(math.atan(33.293 / 2 / 50.0))]}

RIG_CONES = {
    "observer": {"name": "CIE 2006 10°", "data": "10.25039/CIE.DS.nxsqeri8", "field_deg": 10.0,
                 "peak_optical_density": [0.38, 0.38, 0.30], "macular_density_460nm": 0.095,
                 "lens_density_400nm": 1.7649, "luminosity": "V_F,10", "luminosity_weights": [0.69, 0.35],
                 "interpolation": "linear between the table's 5 nm points; zero outside 390-830 nm"},
    "via": None, "luminance": None, "dkl": "unit",
}


def write_rig_warnings(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth) -> None:
    """Stands in for wl-xcon's `xcon/warnings.jsonl`: one row per accepted
    warning, in the keys `wl_xcon/record.py::SessionRecord.warning` writes
    (read at `b2c2850`). One accepted at the open is at the session's start; one
    accepted at a run's start, at that run's."""
    rig = recipe.rig_record
    rows = []
    for warning in rig.warnings:
        at = 0.0 if warning.run is None or warning.run >= len(truth.blocks) else truth.blocks[warning.run].start_s
        rows.append({"code": warning.code, "detail": f"the synthetic {warning.code} warning",
                     "accepted_in": [rig.session_kind], "session_kind": rig.session_kind, "by": warning.by,
                     "at": at, "at_local": (SYNTH_EPOCH + datetime.timedelta(seconds=at)).isoformat(),
                     "how": warning.how, "run": warning.run})
    (xcon_dir / "warnings.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
                                             encoding="utf-8")
