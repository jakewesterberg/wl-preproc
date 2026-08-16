"""Per-system extraction of a barcode bit stream.

This module is the ONLY per-system code in Phase 1c-4. Everything downstream —
decode, rate fit, offset fit, residual, rejection, tier — is shared across all
five systems, because all five carry the same barcode (design spec section 2).

A sixth system costs one function here and one synthetic emitter. It touches no
table and no fit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wl_sync.barcode import BIT_SLOT_US, encode


def min_sample_rate_hz() -> float:
    """The lowest sampling rate that can decode a barcode, derived rather than
    written down.

    Decoding needs at least two samples per bit slot. `BIT_SLOT_US` is
    wl-sync's, so deriving from it means a change there fails a test here
    instead of silently invalidating every camera system's assumed rate. Design
    spec section 3: this project has twice shipped timing arithmetic that no
    code had executed.
    """
    return 2.0 / (BIT_SLOT_US / 1_000_000.0)


@dataclass(frozen=True)
class BitStream:
    """A digital line's edges in the device's OWN time, plus the rate that
    sampled them.

    Native time, not session time: converting to session time is the fit's job
    (`timebase/fit.py`), and keeping the two apart is what makes the transform
    reversible as design spec section 4.5 requires.
    """

    edges: tuple[tuple[int, int], ...]
    fs_hz: float
    n_samples: int

    def __post_init__(self) -> None:
        floor = min_sample_rate_hz()
        if self.fs_hz < floor:
            raise ValueError(
                f"{self.fs_hz} Hz is below the {floor} Hz floor for decoding a "
                f"{BIT_SLOT_US} us bit slot: at least two samples per bit are "
                "needed, so this stream cannot yield a barcode at all"
            )


# The sync box logs at microsecond resolution, so its "sampling rate" is
# nominal: it is not a sampled line at all. 1 MHz is stated rather than
# measured, and is only ever used to satisfy BitStream's floor check.
_SYNCBOX_NOMINAL_FS_HZ = 1_000_000.0


def extract_syncbox(path: Path) -> BitStream:
    """The sync box's own log, rendered into the same edge form every other
    system produces.

    Rendering through `wl_sync.barcode.encode` rather than writing edges by
    hand: the codec owns the frame's shape, and a second copy of it here is the
    reimplementation this phase's constraints forbid.

    A hand-built edge list has to supply two things `edges_from_samples` gives
    every *sampled* system for free (its `previous` state starts `None`, so it
    always emits an edge at its own t0): a LOW edge before the first frame, so
    that frame's preceding idle is verifiable by a caller who decodes this
    stream without passing `start_us` itself; and a LOW edge closing each
    frame's TRAIL pulse, so `decode_edges`'s own completeness check — which
    requires the edge list to extend to the frame's end — has something to see
    past the last frame. Native tick 0 is the log's own time origin and is
    known LOW there, before anything has driven the line.
    """
    from wl_preproc.timebase._syncbox_log import read_barcode_entries

    entries = read_barcode_entries(path)
    edges: list[tuple[int, int]] = []
    for value, t_us in entries:
        tick = t_us
        for level, duration_us in encode(value):
            edges.append((tick, level))
            tick += duration_us
        edges.append((tick, 0))
    if edges:
        edges.append((0, 0))
    edges.sort()
    last_us = edges[-1][0] if edges else 0
    return BitStream(
        edges=tuple(edges),
        fs_hz=_SYNCBOX_NOMINAL_FS_HZ,
        n_samples=last_us,
    )
