"""Deliberate pathology.

Each function corresponds to a real failure the pipeline must survive. They act
on emitted output rather than on the timeline, so ground truth stays the answer
the pipeline should recover — never the corrupted version it was handed.

CLOCK_DRIFT and MISSING_DEVICE are applied at emission rather than here: drift
is a parameter threaded through the writers, and a missing device is a system
absent from the recipe. FAULT_FUNCTIONS records that, so a reader can tell a
deliberate omission from a forgotten one.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from wl_sync.log import Edge, Record

from wl_preproc.synth.ohdpi import OHDPI_FPS
from wl_preproc.synth.recipe import Fault
from wl_preproc.synth.truth import GroundTruth

FAULT_FUNCTIONS: dict[Fault, str] = {
    Fault.DROPPED_BARCODES: "drop_barcodes",
    Fault.SHORT_SEGMENT: "split_into_segments",
    Fault.MID_SESSION_RESTART: "split_into_segments",
    Fault.STOP_MID_TRIAL: "stop_mid_trial",
    Fault.DROPPED_CAMERA_FRAMES: "drop_camera_frames",
    Fault.TRIAL_COUNT_MISMATCH: "corrupt_trial_count",
    Fault.TRUNCATED_FILE: "truncate_file",
}


def drop_barcodes(records: Sequence[Record], every: int) -> list[Record]:
    """Remove every nth barcode edge, leaving code words untouched."""
    kept: list[Record] = []
    seen = 0
    for record in records:
        if isinstance(record, Edge):
            seen += 1
            if seen % every == 0:
                continue
        kept.append(record)
    return kept


def split_into_segments(
    records: Sequence[Record], restart_at_s: float, gap_s: float
) -> list[list[Record]]:
    """Split into two recording segments separated by a real gap."""
    cut = int(restart_at_s * 1e6)
    shift = int(gap_s * 1e6)
    first = [r for r in records if r.tick_us <= cut]
    second = [
        dataclasses.replace(r, tick_us=r.tick_us + shift)
        for r in records
        if r.tick_us > cut
    ]
    return [first, second]


def stop_mid_trial(records: Sequence[Record], at_s: float) -> list[Record]:
    """Cut the stream partway through a trial, leaving that trial incomplete."""
    cut = int(at_s * 1e6)
    return [r for r in records if r.tick_us <= cut]


def drop_camera_frames(frame_count: int, rng: np.random.Generator) -> tuple[int, ...]:
    n_dropped = max(1, frame_count // 200)
    return tuple(sorted(rng.choice(frame_count, n_dropped, replace=False).tolist()))


def drop_ohdpi_frames(
    frame_count: int, at_s: float, n_frames: int, fps: float = OHDPI_FPS
) -> tuple[int, ...]:
    """Row indices for one contiguous run of dropped eye-camera frames.

    A contiguous run rather than a scatter, because that is what the failure
    looks like: OpenIrisDPI's own notebook describes it as the computer being
    too slow to process images in time, which loses a burst.
    `split_into_segments` above models a mid-session RESTART, which is a
    different fault -- that one shifts time, this one removes it. And
    `drop_camera_frames` above is the BEHAVIOUR camera's: those frames are
    sync-box triggered, so a dropped one costs its own samples and shifts
    nothing after it (`timebase/extract.py::extract_bcam` says so in as many
    words), whereas an ohDPI frame number is the camera's own free-running
    counter and its hole is exactly what has to survive into the file.

    **`at_s` is the RECORDING's own time, not session time.** A row's index
    IS its timestamp here (`write_ohdpi` derives `Seconds` from the frame
    index), and the recording starts `OHDPI_PRE_ROLL_S` before session t=0 --
    so a caller naming a session-time instant must add that pre-roll itself.
    Stated because getting it wrong moves a gap by 300 frames silently, and a
    gap 300 frames from where it was meant to be is still a gap: nothing
    downstream would notice.

    Refuses a run that would touch either end of the file. A gap is detected
    from the JUMP between two rows that were both KEPT (`eye/ohdpi.py`
    diffs the frame-number column), so a run starting at row 0, or running to
    the last row, leaves no such pair and plants no detectable gap at all.
    That is the silent no-op this repository keeps paying for -- a fixture
    that appears to inject a fault, injects nothing, and makes every test
    written against it pass against a clean file.
    """
    first = int(at_s * fps)
    stop = first + n_frames
    if not 0 < first < stop < frame_count:
        raise ValueError(
            f"rows {first}..{stop - 1} ({n_frames} frames at {at_s} s, "
            f"{fps} fps) leave no surviving row on both sides of the gap "
            f"within this recording's {frame_count} frames, so the file would "
            "carry no DETECTABLE gap -- a fixture that plants nothing while "
            "looking like it planted something"
        )
    return tuple(range(first, stop))


def corrupt_trial_count(truth: GroundTruth) -> GroundTruth:
    """Remove one trial from the task-file view while leaving the code stream
    intact, so the two disagree by exactly one."""
    return dataclasses.replace(truth, trials=truth.trials[:-1])


def truncate_file(path: Path, keep_fraction: float) -> None:
    size = path.stat().st_size
    with path.open("r+b") as handle:
        handle.truncate(int(size * keep_fraction))
