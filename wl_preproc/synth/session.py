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

# Matches the epoch peripherals.py stamps into the manifest's started_at and
# syncbox.py stamps into the log header's written_at: this generator's fake
# session always starts at the same synthetic wall-clock instant. The DONE
# marker's transfer_finished_at is derived from it plus the recipe's own
# duration_s, never datetime.now() -- two runs of the same recipe must produce
# identical trees (test_generation_is_byte_identical_for_one_seed).
_EPOCH = datetime.datetime(2027, 3, 14, 9, 0, tzinfo=datetime.timezone.utc)


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
    finished_at = _EPOCH + datetime.timedelta(seconds=recipe.duration_s)

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

        _write_done_marker(layout, system, finished_at)

    return truth
