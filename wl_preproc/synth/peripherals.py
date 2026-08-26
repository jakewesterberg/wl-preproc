"""Manifest, camera sidecar and task file.

Each is written through its real contract model, so the generator cannot emit
something the pipeline would reject — if it could, the fixture would be testing
a format nothing else speaks.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import yaml
from wl_sync.barcode import encode

from wl_preproc.contracts.manifest import SCHEMA_VERSION, SessionManifest, StartedAtSource
from wl_preproc.contracts.sidecar import BehaviorCameraSidecar, VideoFile
from wl_preproc.synth.recipe import SYNTH_EPOCH, SessionRecipe
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
