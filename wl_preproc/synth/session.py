"""Assemble a complete session directory and return what was planted."""

from __future__ import annotations

import datetime
from pathlib import Path

import numpy as np
from wl_sync.session import SessionId

from wl_preproc.contracts.done import DONE_SCHEMA_VERSION, DoneMarker, FileEntry, blake3_file
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.synth.faults import drop_camera_frames, truncate_file
from wl_preproc.synth.peripherals import (
    camera_frame_count,
    write_camera_sidecar,
    write_manifest,
    write_task_file,
)
from wl_preproc.synth.ohdpi import write_ohdpi
from wl_preproc.synth.recipe import Fault, SYNTH_EPOCH, SessionRecipe
from wl_preproc.synth.rhs import write_rhs
from wl_preproc.synth.spikeglx import write_spikeglx
from wl_preproc.synth.syncbox import write_syncbox_log
from wl_preproc.synth.timeline import build_timeline
from wl_preproc.synth.truth import GroundTruth


def _write_done_marker(layout: SessionLayout, system: str, finished_at: datetime.datetime) -> None:
    """Hash every file this system wrote, and declare them.

    `rglob` rather than a caller-supplied list: the rhs writer emits a nested
    directory, and a marker that silently omitted those files would make the
    verification step pass while proving nothing.
    """
    system_dir = layout.system_dir(system)
    marker_path = layout.done_marker(system)
    entries = [
        FileEntry(
            path=str(candidate.relative_to(system_dir)),
            bytes=candidate.stat().st_size,
            blake3=blake3_file(candidate),
        )
        for candidate in sorted(system_dir.rglob("*"))
        if candidate.is_file() and candidate != marker_path
    ]
    marker = DoneMarker(
        schema_version=DONE_SCHEMA_VERSION,
        system=system,
        transfer_finished_at=finished_at,
        files=entries,
    )
    marker_path.write_text(marker.to_yaml(), encoding="utf-8")


def generate_session(root: Path, recipe: SessionRecipe) -> GroundTruth:
    truth = build_timeline(recipe)
    layout = SessionLayout(root, SessionId.parse(recipe.session_id))
    layout.dir.mkdir(parents=True, exist_ok=True)
    write_manifest(layout.manifest_path, recipe)

    rng = np.random.default_rng(recipe.seed + 2)
    finished_at = SYNTH_EPOCH + datetime.timedelta(seconds=recipe.duration_s)
    overrides = dict(recipe.system_drift_ppm)

    for system in recipe.systems:
        directory = layout.system_dir(system)
        directory.mkdir(exist_ok=True)
        drift_ppm = overrides.get(system, recipe.drift_ppm)

        if system == "syncbox":
            # Zero, unconditionally. Session time IS the sync box's timeline
            # (spec section 4.5), so it cannot drift against itself — and
            # drifting it alongside every device, which this did until Phase
            # 1c-4, cancels the drift exactly and leaves every fixture with no
            # relative drift for the rate fit to find.
            write_syncbox_log(directory / "syncbox.log", recipe, truth, drift_ppm=0.0)
            write_task_file(directory / "task.json", truth)
        elif system == "spikeglx":
            bin_path = write_spikeglx(
                directory, recipe, truth, drift_ppm=drift_ppm
            )
            if Fault.TRUNCATED_FILE in recipe.faults:
                truncate_file(bin_path, keep_fraction=0.6)
        elif system == "rhs":
            write_rhs(directory, recipe, truth, drift_ppm=drift_ppm)
        elif system == "ohdpi":
            write_ohdpi(directory, recipe, truth, drift_ppm=drift_ppm)
        elif system == "bcam":
            dropped = (
                drop_camera_frames(camera_frame_count(recipe), rng)
                if Fault.DROPPED_CAMERA_FRAMES in recipe.faults
                else ()
            )
            write_camera_sidecar(
                directory / "frames.yaml",
                recipe,
                truth,
                dropped=dropped,
                drift_ppm=drift_ppm,
            )

        _write_done_marker(layout, system, finished_at)

    return truth
