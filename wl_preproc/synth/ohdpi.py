"""Emit an OpenIrisDPI (`ohdpi`) recording: one row per camera frame.

`ohdpi` is a dual-Purkinje eye tracker (design spec section 2.1). It appeared in
`contracts.paths.SYSTEMS` and nowhere else -- no emitter, no profile, no
fixture -- until this module.

**Everything about the file's shape here is PROPOSED, not known.** Design spec
section 12.1: neither the OpenIris repository nor the OpenIrisDPI wiki documents
the recorded file's columns, and no sample recording was available. So every
format assumption is a named constant in this one module, and the reader that
consumes it (`wl_preproc/timebase/_ohdpi_file.py`) restates them for its own
half. A real recording settles them by moving these names and nothing else --
no fit, no table, no extraction logic.

**Why a digital line rather than the analog eye trace.** OpenIrisDPI also
exports eye position as analog voltage, which invites aligning by
cross-correlating that against a DAQ-recorded copy. Design spec section 2.1
rejects it on the instrument's own published numbers: frame processing is
1.1 ms median but up to 50 ms, with 2% of frames over 10 ms, plus 3-4 ms of DAC
delay -- so there is no single offset to recover, and the fit would be wrong for
exactly the frames that matter. The OpenIrisDPI wiki's own recommendation is a
shared digital synchronisation line; the sync box's barcode is that line with a
better encoding.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from wl_sync.barcode import encode

from wl_preproc.synth.recipe import SessionRecipe
from wl_preproc.synth.timeline import apply_drift
from wl_preproc.synth.truth import GroundTruth

# 500 Hz: the rate the OpenIrisDPI paper reports, and 2.5 samples per 5 ms bit
# slot -- the thinnest decoding margin in the design (design spec section 3).
# `min_sample_rate_hz()` is the floor this must clear, and a test asserts it
# against that derivation rather than against a literal.
OHDPI_FPS = 500.0

# A fifth distinct tick origin -- see syncbox.py, and design spec section 10.
# Above `wl_sync.barcode.IDLE_MIN_US` (0.4 s), or the first barcode silently
# fails to decode for want of a preceding idle: the trap that has cost this
# project twice.
OHDPI_PRE_ROLL_S = 0.6

# --- PROPOSED FORMAT. Every name below is a guess awaiting a real file. ---

FILENAME = "ohdpi_frames.csv"

COLUMN_FRAME_INDEX = "frame_index"
COLUMN_TIMESTAMP_US = "timestamp_us"
COLUMN_DIGITAL = "digital"

COLUMNS: tuple[str, ...] = (COLUMN_FRAME_INDEX, COLUMN_TIMESTAMP_US, COLUMN_DIGITAL)

# The timestamp column's unit. Named because it is the assumption most likely to
# be wrong and least likely to fail loudly: a file in milliseconds read as
# microseconds yields a rate off by 1000, which is a fit wrong by exactly that
# ratio and a residual that does not say so.
TIMESTAMP_UNITS_PER_SECOND = 1_000_000


@dataclass(frozen=True, slots=True)
class OhdpiRow:
    """One camera frame: its index, its native time, and the sync line's level."""

    frame_index: int
    timestamp_us: int
    digital: int


def frame_count(recipe: SessionRecipe) -> int:
    """Frames captured for a recipe, pre-roll included."""
    return int((recipe.duration_s + OHDPI_PRE_ROLL_S) * OHDPI_FPS)


def _digital_line(
    recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float
) -> list[int]:
    """The barcode rendered into one 0/1 sample per frame.

    The frame rate is the sampling rate for this line, so a frame index is a
    time. Rendered through `wl_sync.barcode.encode` rather than written out:
    the codec owns the frame's shape, and a second copy of it here would be
    free to drift from the one the hardware implements.
    """
    line = [0] * frame_count(recipe)
    for value, start_s in truth.barcodes:
        cursor_s = apply_drift(start_s, drift_ppm) + OHDPI_PRE_ROLL_S
        for level, duration_us in encode(value):
            start_frame = int(cursor_s * OHDPI_FPS)
            cursor_s += duration_us * 1e-6
            if level:
                stop_frame = min(int(cursor_s * OHDPI_FPS), len(line))
                for frame in range(max(start_frame, 0), stop_frame):
                    line[frame] = 1
    return line


def write_ohdpi(
    dir_path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0
) -> Path:
    """Render one session's eye-tracker recording, and return the file written.

    The native timestamp is derived from the frame index and `OHDPI_FPS` rather
    than tracked separately, so the two columns cannot disagree -- an extractor
    is entitled to derive the rate from the timestamps instead of assuming
    `OHDPI_FPS`, and a fixture whose columns disagreed would make that
    derivation wrong while every value-based assertion still passed.
    """
    line = _digital_line(recipe, truth, drift_ppm)
    path = dir_path / FILENAME
    # newline="" is csv's requirement, not a style choice: without it the writer
    # emits \r\r\n on platforms whose default translates \n, and the file stops
    # being byte-identical across runs.
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for frame_index, digital in enumerate(line):
            timestamp_us = round(
                frame_index * TIMESTAMP_UNITS_PER_SECOND / OHDPI_FPS
            )
            writer.writerow((frame_index, timestamp_us, digital))
    return path


def read_ohdpi_rows(path: Path) -> list[OhdpiRow]:
    """Read back what `write_ohdpi` wrote.

    A convenience for the fixture's own tests, deliberately NOT the production
    reader: `wl_preproc/timebase/_ohdpi_file.py` is that, and it does not import
    this module. A production reader that shared the emitter's parsing would
    agree with the emitter by construction and prove nothing about a real file
    -- the same reason `_syncbox_log.py` restates its gpio key rather than
    importing it from `synth`.
    """
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            OhdpiRow(
                frame_index=int(row[COLUMN_FRAME_INDEX]),
                timestamp_us=int(row[COLUMN_TIMESTAMP_US]),
                digital=int(row[COLUMN_DIGITAL]),
            )
            for row in csv.DictReader(handle)
        ]
