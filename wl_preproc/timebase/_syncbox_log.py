"""The sync box log's one reader.

`wl_sync.log` defines the on-disk shape: a JSON header line, then one CSV
line per record, type-tagged 'E' (a transition on an input line), 'W' (a code
word latched on a strobe edge), 'B' (a barcode the box emitted) or 'T' (a
segment's trailer, which `read_log` skips). **The barcodes are the 'B'
records**: the box never captures its own barcode line, which is an output
(wl-sync's `pins.py`, GPIO17; its edge inputs are GPIO20-27), and logs each
frame it emits instead, "the emitter's own account of what it put on the wire,
not a measurement of it" (`wl_sync/log.py`). Until 2026-10-22 this decoded
edges on the line the header's `gpio_map` named `barcode_out`, the shape only
this project's synthetic logs had: a real log yielded no barcodes (design spec
`2026-10-22-syncbox-records-and-segments-design.md` section 1).

A 'B' tick is read before the frame is sent, so it leads the frame's edge on
the wire by a delay not yet measured; it is used as it is, and recorded as
uncorrected (that spec's section 5).

Kept in its own module, separate from `extract.py`, so the log format's shape
has exactly one reader.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wl_sync.log import BarcodeEmitted, read_log


@dataclass(frozen=True, slots=True)
class SyncBoxLog:
    """One segment of the sync box's log: one run of its process, on a clock
    of its own (wl-sync's `segment.py`), with what its header says of it and
    the barcodes it logged emitting, `(value, tick_us)`."""

    path: Path
    segment_index: int
    boot_id: str
    clock_trusted: bool
    clock_reason: str
    barcodes: tuple[tuple[int, int], ...]


def read_syncbox_log(path: Path) -> SyncBoxLog:
    header, records = read_log(path)
    return SyncBoxLog(
        path=path, segment_index=header.segment_index, boot_id=header.boot_id,
        clock_trusted=header.clock_trusted, clock_reason=header.clock_reason,
        barcodes=tuple((record.value, record.tick_us) for record in records if isinstance(record, BarcodeEmitted)),
    )


def read_barcode_entries(path: Path) -> list[tuple[int, int]]:
    """`(value, tick_us)` for every barcode the box logged emitting, in the
    order the log holds them.

    `wl_sync.log.read_log` validates the header as it parses it (a malformed
    header raises there, via `SyncBoxLogHeader`'s own pydantic validators),
    drops a torn last line, and skips the trailer. A log holding no 'B' record
    yields no barcodes, and a session whose sync box logged none has no
    session time (`timebase/segments.py::session_reference`).
    """
    return list(read_syncbox_log(path).barcodes)
