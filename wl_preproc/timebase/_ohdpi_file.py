"""The OpenIrisDPI per-frame recording's one reader.

**Every column name here is PROPOSED.** Design spec section 12.1: neither the
OpenIris repository nor the OpenIrisDPI wiki documents the recorded file's
columns, and no sample recording existed when this was written. A real file
settles them by moving the constants below and nothing else.

The names are restated rather than imported from `wl_preproc.synth.ohdpi`,
which writes the matching fixture, for the reason `_syncbox_log.py` restates
its gpio key: a production reader that shared the emitter's constants would
agree with the emitter by construction, and January's files come from OpenIris
rather than from that emitter. When the real columns are learned, these two
lists disagreeing is the signal that the fixture needs updating too.

Kept in its own module, separate from `extract.py`, so the format has exactly
one reader.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

_COLUMN_FRAME_INDEX = "frame_index"
_COLUMN_TIMESTAMP_US = "timestamp_us"
_COLUMN_DIGITAL = "digital"

# The timestamp column's unit. The assumption most likely to be wrong and least
# likely to fail loudly: a file in milliseconds read as microseconds yields a
# rate off by 1000x, which is a fit wrong by exactly that ratio and a residual
# that does not say so.
_TIMESTAMP_UNITS_PER_SECOND = 1_000_000


@dataclass(frozen=True, slots=True)
class OhdpiRecording:
    """A whole recording: the sync line per frame, and the rate that sampled it.

    `fs_hz` is **measured from the file's own timestamps**, not assumed. The
    emitter's `OHDPI_FPS` is what the fixture runs at; the instrument's rate is
    a property of the recording, and the file carries it.
    """

    digital: tuple[int, ...]
    fs_hz: float
    n_frames: int


def read_ohdpi(path: Path) -> OhdpiRecording:
    """Read one recording, refusing anything whose frame index is not a time.

    Contiguity and monotonicity are checked rather than assumed, because both
    fail silently. A gap in the frame indices is a dropped frame the file does
    not declare, and treating the remaining rows as consecutive shifts every
    sample after the gap — which moves the decoded barcode times and therefore
    this system's whole offset fit, with nothing to say so.
    """
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = {_COLUMN_FRAME_INDEX, _COLUMN_TIMESTAMP_US, _COLUMN_DIGITAL} - set(
            reader.fieldnames or ()
        )
        if missing:
            raise ValueError(
                f"{path}: no {', '.join(sorted(missing))} column; found "
                f"{list(reader.fieldnames or ())}. These column names are a "
                "proposal (design spec 12.1) and a real recording is expected "
                "to move them"
            )
        rows = [
            (
                int(row[_COLUMN_FRAME_INDEX]),
                int(row[_COLUMN_TIMESTAMP_US]),
                int(row[_COLUMN_DIGITAL]),
            )
            for row in reader
        ]

    if len(rows) < 2:
        raise ValueError(
            f"{path}: {len(rows)} frames cannot establish a sampling rate, so "
            "nothing here can be timed"
        )

    for position, (frame_index, _timestamp_us, _digital) in enumerate(rows):
        if frame_index != position:
            raise ValueError(
                f"{path}: frame index {frame_index} at row {position}; the "
                "indices must be contiguous from zero, because a gap is a "
                "dropped frame the file does not declare and reading past it "
                "shifts every later sample against its true time"
            )

    timestamps = [timestamp_us for _index, timestamp_us, _digital in rows]
    span_us = timestamps[-1] - timestamps[0]
    if span_us <= 0:
        raise ValueError(
            f"{path}: the last frame's timestamp does not follow the first "
            f"({timestamps[0]} to {timestamps[-1]}), so the file carries no "
            "usable clock"
        )

    # The rate over the whole span rather than an adjacent difference: one
    # interval is quantised to the timestamp's own resolution, and at 500 Hz
    # that quantisation is a percent-level error in the rate the entire fit
    # inherits.
    fs_hz = (len(rows) - 1) * _TIMESTAMP_UNITS_PER_SECOND / span_us

    return OhdpiRecording(
        digital=tuple(digital for _index, _timestamp_us, digital in rows),
        fs_hz=fs_hz,
        n_frames=len(rows),
    )
