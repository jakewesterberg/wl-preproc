"""Emit an OpenIrisDPI (`ohdpi`) recording, in OpenIris's own real format.

`ohdpi` is a dual-Purkinje eye tracker (design spec section 2.1). Until this
task the file this module wrote was a proposal: design spec section 12.1
recorded that neither the OpenIris repository nor the OpenIrisDPI wiki
documented the recorded file's columns, and no sample recording was
available, so every column name here was a guess synchronised only with this
project's own guessed reader (a private module under `wl_preproc/timebase/`,
since deleted) -- the two agreed with each other by construction and said
nothing about what OpenIris actually writes.

A real recording has since settled the question. `wl_preproc/eye/ohdpi.py` is
the format's one reader, with every column name verified against a genuine
recording, and `tests/fixtures/ohdpi/OpenIris-sample.txt` is a committed
200-row slice of it. This module now writes that same shape: the full header
in OpenIris's own column order, the `{12, 13}` `Int0` sync word, and Purkinje
geometry (P1 in `CR1X`/`CR1Y`, P4 in `CR4X`/`CR4Y`, `CR2`/`CR3`/`CR5` always
zero, matching every row of the sample). The fixture and the reader now agree
because both match OpenIris, not because they share a guess.

**Why a digital line rather than the analog eye trace.** OpenIrisDPI also
exports eye position as analog voltage, which invites aligning by
cross-correlating that against a DAQ-recorded copy. Design spec section 2.1
rejects it on the instrument's own published numbers: frame processing is
1.1 ms median but up to 50 ms, with 2% of frames over 10 ms, plus 3-4 ms of DAC
delay -- so there is no single offset to recover, and the fit would be wrong for
exactly the frames that matter. The OpenIrisDPI wiki's own recommendation is a
shared digital synchronisation line; the sync box's barcode is that line with a
better encoding.

**Why P1 and P4 move independently.** Gaze calibration (later tasks) needs a
signal to recover, not merely two columns that happen to be nonzero: P1 and P4
here share a slow common translation (the eye moving in the socket) and differ
by an added rotation term that only P4 sees, so `P1 - P4` carries the
corneal-curvature signal a dual-Purkinje calibration exists to recover.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
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

# OpenIris's own recorded-file name convention is `<session>.txt`, and the
# corrected glob in `wl_preproc/timebase/extract.py` (`_RECORDING_GLOBS`)
# matches exactly that -- NOT `ohdpi_frames.csv`, which this module wrote
# before this task and which matched nothing real.
FILENAME = "OpenIris-synthetic.txt"

# One eye's columns, in OpenIris's own order. Built once and reused for both
# `Left` and `Right` below rather than written out twice, so the two halves of
# the header cannot silently diverge from each other.
_PER_EYE = (
    "FrameNumber", "FrameNumberRaw", "Seconds",
    "PupilX", "PupilY", "PupilWidth", "PupilHeight", "PupilAngle",
    "IrisRadius", "Torsion", "UpperEyelid", "LowerEyelid", "DataQuality",
    "CR1X", "CR1Y", "CR2X", "CR2Y", "CR3X", "CR3Y",
    "CR4X", "CR4Y", "CR5X", "CR5Y",
)
_IMU = tuple(
    f"{sensor}{axis}"
    for sensor in ("Accelerometer", "Gyro", "Magnetometer")
    for axis in ("X", "Y", "Z")
)
_EXTRA = tuple(f"Int{i}" for i in range(8)) + tuple(f"Double{i}" for i in range(8))
_DEBUG = ("DebugTimeGrabbedLeft", "DebugTimeGrabbedRight", "DebugTimeProcessed")

# The full OpenIris header, in order: both eyes, then the IMU, then the eight
# generic Int/Double slots (one of which -- `Int0` -- carries the sync line),
# then the three debug timestamps. `wl_preproc/eye/ohdpi.py` does not import
# this tuple -- it restates the handful of column names it actually reads --
# so this is the one place the FULL header lives.
HEADER: tuple[str, ...] = (
    tuple(f"Left{name}" for name in _PER_EYE)
    + tuple(f"Right{name}" for name in _PER_EYE)
    + _IMU
    + _EXTRA
    + _DEBUG
)

# The reference recording's constant left/right timestamp offset (design spec
# section 1.3 measured 49.40-49.50 ms over 10,000 rows; this module's own
# 200-row fixture spans the same range). Reproduced here, added to the left
# eye's `Seconds`, so a consumer that wrongly treats `Seconds` as session time
# is wrong on this fixture in exactly the way it would be wrong in production,
# rather than reading a fixture where the two cameras' clocks happen to agree.
RIGHT_SECONDS_OFFSET_S = -0.04948

# `Int0` = 12 with bit 0 carrying the barcode, matching the reference
# recording's {12, 13} (bits 2 and 3 constant-high, bit 1 always low). The
# constant-high bits are reproduced, not just the toggling one, because a
# reader that masks the wrong bit or fails to mask at all must fail on this
# fixture too -- exactly as it would on the real file.
_INT0_BASE = 0b1100

# Decimal places every sample column carries in the reference recording
# (`524.5200`, `100.0000`, ...). Named once and routed through `_fmt` below so
# every formatted field in `write_ohdpi` is tied to this one constant rather
# than to a `.4f` literal repeated three dozen times and free to drift from it.
SAMPLE_DTYPE_DECIMALS = 4


def _fmt(value: float) -> str:
    """One sample field, at the fixture's own decimal precision."""
    return f"{value:.{SAMPLE_DTYPE_DECIMALS}f}"


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
    """Render one session's eye recording in OpenIris's real format.

    `Seconds` is derived from the frame index and `OHDPI_FPS` so the two
    columns cannot disagree -- an extractor is entitled to derive the rate
    from the timestamps instead of assuming `OHDPI_FPS` (design spec section
    3.1), and a fixture whose columns disagreed would make that derivation
    wrong while every value-based assertion still passed. The right eye's
    `Seconds` carries the reference recording's constant offset
    (`RIGHT_SECONDS_OFFSET_S`).

    Pupil and Purkinje positions follow a slow common drift (translation) plus
    per-frame noise; P1 and P4 share the drift and differ by a rotation term
    only P4 sees, so `P1 - P4` carries the signal a calibration can recover
    (see the module docstring). Both eyes are given the same Purkinje
    geometry -- only `Seconds` differs between them -- since nothing downstream
    of this fixture needs independent left/right eye positions yet.
    """
    line = _digital_line(recipe, truth, drift_ppm)
    n = len(line)
    rng = np.random.default_rng(recipe.seed)

    # Not zero: matches the reference recording's own camera counter, and
    # `wl_preproc/eye/ohdpi.py`'s reader depends on frame numbers being
    # CONTIGUOUS rather than starting at any particular position.
    frame0 = 308788
    t = np.arange(n) / OHDPI_FPS
    # Slow common motion (translation), plus a rotation term only P4 sees.
    common_x = 500.0 + 20.0 * np.sin(2 * np.pi * 0.05 * t)
    common_y = 220.0 + 15.0 * np.cos(2 * np.pi * 0.03 * t)
    rot_x = 40.0 * np.sin(2 * np.pi * 0.20 * t) + rng.normal(0, 0.5, n)
    rot_y = 30.0 * np.cos(2 * np.pi * 0.17 * t) + rng.normal(0, 0.5, n)

    path = dir_path / FILENAME
    with path.open("w", encoding="utf-8") as handle:
        handle.write(" ".join(HEADER) + "\n")
        for i in range(n):
            p1x, p1y = common_x[i], common_y[i]
            p4x, p4y = common_x[i] - rot_x[i], common_y[i] - rot_y[i]
            row: list[str] = []
            for eye_index in range(2):
                offset = 0.0 if eye_index == 0 else RIGHT_SECONDS_OFFSET_S
                row += [
                    str(frame0 + i), str(frame0 + i - 1), _fmt(t[i] + offset),
                    _fmt(p1x), _fmt(p1y), _fmt(60.0), _fmt(58.0), _fmt(0.0),
                    _fmt(180.0), _fmt(0.0), _fmt(0.0), _fmt(0.0), _fmt(100.0),
                    _fmt(p1x), _fmt(p1y), _fmt(0.0), _fmt(0.0), _fmt(0.0), _fmt(0.0),
                    _fmt(p4x), _fmt(p4y), _fmt(0.0), _fmt(0.0),
                ]
            row += [_fmt(0.0)] * len(_IMU)
            row += [str(_INT0_BASE | line[i])] + ["0"] * 7
            row += [_fmt(0.0)] * 8
            row += [_fmt(t[i]), _fmt(t[i]), _fmt(t[i])]
            handle.write(" ".join(row) + "\n")
    return path
