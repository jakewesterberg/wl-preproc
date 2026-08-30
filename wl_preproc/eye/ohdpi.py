"""The OpenIrisDPI recording's one reader, for every consumer.

**Every column name here was verified against a real recording** on
2026-08-30 (`OpenIris-2024Jul31-114628`, 1,177,799 rows) and against
OpenIris's own `EyeTrackerData.cs::GetStringHeader()`. That replaces the
proposal this project shipped in Phase 1c-4, whose three guesses were all
wrong -- see the design spec's section 0.

Lives in `eye/` rather than `timebase/` because parent design spec section 3.4's
module layout assigns the ohDPI reader here, and because two subsystems now
consume it. This module imports nothing from `timebase`: it reads a file and
knows nothing of session time, so `timebase/extract.py` can import it without a
cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

EYES: tuple[str, ...] = ("Left", "Right")

# The generic extra-data slot OpenIris writes the digital input into. Measured:
# `Int0` takes exactly {12, 13} across the whole reference recording, while
# every other Int slot is 0.
SYNC_WORD_COLUMN = "Int0"

# **Rig wiring, not a property of the format.** On the reference recording bit 0
# toggles and bits 2 and 3 sit constant-high, so 12/13 is bit 0 carrying the
# signal. Another rig may wire it elsewhere; that is a one-constant change, and
# this is the constant.
SYNC_BIT_INDEX = 0

_FRAME_NUMBER = "LeftFrameNumber"
_SECONDS = "LeftSeconds"

# The columns every caller can rely on. Not the whole header -- the file has
# about a hundred columns and no consumer wants them all -- but enough to
# recognise the format and refuse anything else.
_REQUIRED = (_FRAME_NUMBER, _SECONDS, SYNC_WORD_COLUMN, "LeftCR1X", "LeftCR4X")


@dataclass(frozen=True, slots=True)
class OhdpiRecording:
    """The sync line per frame, and the rate that sampled it.

    **No timestamp column is exposed, deliberately.** `LeftSeconds` and
    `RightSeconds` differ by a constant 49.48 ms over this module's 200-row
    test fixture (min 49.40, max 49.50 -- one timestamp tick of spread there;
    design spec section 1.3 measured the same 49.40-49.50 ms range over
    10,000 rows), while `LeftFrameNumber` and `RightFrameNumber` are identical
    in every row. That offset is not fixed over a full session either: across
    the whole 1,177,799-row reference recording it drifts smoothly from
    49.5 ms to 45.8 ms (checked at 10 points spread through the file) --
    confirmation that this is per-camera clock drift, not shared jitter around
    a fixed origin. The cameras are frame-locked by the trigger chain; their
    clocks free-run independently, and at 500 Hz this offset is on the order
    of 25 frames.

    So frame number is the index and `Seconds` is used only to derive `fs_hz`
    inside this module. Parent design spec section 7.1 puts eye frame times on
    the sync-box clock by construction via the Pi trigger, which is where
    session time comes from -- never from here.
    """

    frame_numbers: np.ndarray
    digital: np.ndarray
    fs_hz: float
    n_frames: int


def read_columns(path: Path, columns: list[str]) -> dict[str, np.ndarray]:
    """Named columns from one recording, as arrays.

    Column-selective because the file has ~100 columns and no caller wants
    them all: timebase needs 2 and gaze needs about 10. Measured on the
    reference recording, reading 10 columns takes 2.5 s and 94 MB against
    roughly a gigabyte for the whole file.
    """
    try:
        frame = pd.read_csv(path, sep=r"\s+", usecols=columns, engine="c")
    except ValueError as exc:
        # pandas validates `usecols` against the header BEFORE ever returning a
        # frame, so it beats the check this function used to run on
        # `frame.columns` to every real bad-header file -- that check was
        # unreachable dead code, not merely untested (task-1 fix round: found
        # by mutation, since dropping it changed nothing). Read the header
        # alone -- `nrows=0`, effectively free, one line -- to name exactly
        # which requested columns are missing in our own words, rather than
        # leave a caller with pandas' "Usecols do not match columns," which
        # says the same thing for a genuinely malformed file and never says
        # which case this is.
        header = pd.read_csv(path, sep=r"\s+", nrows=0, engine="c")
        missing = set(columns) - set(header.columns)
        if missing:
            raise ValueError(
                f"{path}: header is missing {sorted(missing)}. This is not an "
                "OpenIris recording, or its format has changed since 2026-08-30"
            ) from exc
        raise
    return {name: frame[name].to_numpy() for name in columns}


def read_ohdpi(path: Path) -> OhdpiRecording:
    """Read the sync line and establish the recording's own rate."""
    try:
        data = read_columns(path, list(_REQUIRED))
    except ValueError as exc:
        raise ValueError(f"{path}: unrecognised header -- {exc}") from exc

    frames = data[_FRAME_NUMBER]
    if frames.size < 2:
        raise ValueError(
            f"{path}: {frames.size} frames cannot establish a sampling rate, "
            "so nothing here can be timed"
        )

    # Contiguity, NOT a zero start. The shipped reader required
    # `frame_index == position`; the reference recording runs 308788 to
    # 1486586, so that check rejects every real file. A gap is still a dropped
    # frame the file does not declare, and reading past it shifts every later
    # sample against its true time.
    gaps = np.flatnonzero(np.diff(frames) != 1)
    if gaps.size:
        first = int(gaps[0])
        raise ValueError(
            f"{path}: frame numbers jump from {frames[first]} to "
            f"{frames[first + 1]} at row {first}; a gap is a dropped frame the "
            "file does not declare"
        )

    seconds = data[_SECONDS]
    span_s = float(seconds[-1] - seconds[0])
    if span_s <= 0:
        raise ValueError(
            f"{path}: the last frame's timestamp does not follow the first "
            f"({seconds[0]} to {seconds[-1]}), so the file carries no usable clock"
        )

    # The rate over the whole span rather than an adjacent difference: one
    # interval is quantised to the timestamp's own resolution (0.1 ms here),
    # and at 500 Hz that quantisation is a percent-level error the entire fit
    # would inherit.
    fs_hz = (frames.size - 1) / span_s

    return OhdpiRecording(
        frame_numbers=frames,
        digital=data[SYNC_WORD_COLUMN],
        fs_hz=fs_hz,
        n_frames=int(frames.size),
    )
