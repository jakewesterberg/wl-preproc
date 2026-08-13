"""Assemble a complete session directory and return what was planted."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from wl_sync.session import SessionId

from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.synth.faults import drop_camera_frames, truncate_file
from wl_preproc.synth.peripherals import (
    CAMERA_FPS,
    write_camera_sidecar,
    write_manifest,
    write_task_file,
)
from wl_preproc.synth.recipe import Fault, SessionRecipe
from wl_preproc.synth.rhs import write_rhs
from wl_preproc.synth.spikeglx import write_spikeglx
from wl_preproc.synth.syncbox import write_syncbox_log
from wl_preproc.synth.timeline import build_timeline
from wl_preproc.synth.truth import GroundTruth


def generate_session(root: Path, recipe: SessionRecipe) -> GroundTruth:
    truth = build_timeline(recipe)
    layout = SessionLayout(root, SessionId.parse(recipe.session_id))
    layout.dir.mkdir(parents=True, exist_ok=True)
    write_manifest(layout.manifest_path, recipe)

    rng = np.random.default_rng(recipe.seed + 2)

    for system in recipe.systems:
        directory = layout.system_dir(system)
        directory.mkdir(exist_ok=True)

        if system == "syncbox":
            write_syncbox_log(
                directory / "syncbox.log", recipe, truth, drift_ppm=recipe.drift_ppm
            )
            write_task_file(directory / "task.json", truth)
        elif system == "spikeglx":
            bin_path = write_spikeglx(
                directory, recipe, truth, drift_ppm=recipe.drift_ppm
            )
            if Fault.TRUNCATED_FILE in recipe.faults:
                truncate_file(bin_path, keep_fraction=0.6)
        elif system == "rhs":
            write_rhs(directory, recipe, truth, drift_ppm=recipe.drift_ppm)
        elif system == "bcam":
            dropped = (
                drop_camera_frames(int(recipe.duration_s * CAMERA_FPS), rng)
                if Fault.DROPPED_CAMERA_FRAMES in recipe.faults
                else ()
            )
            write_camera_sidecar(directory / "frames.yaml", recipe, dropped=dropped)

        layout.done_marker(system).write_text("", encoding="utf-8")

    return truth
