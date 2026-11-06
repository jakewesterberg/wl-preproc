"""The sync box log's one reader.

`wl_sync.log` defines the on-disk shape: a JSON header line, then one CSV
line per record, type-tagged 'E' (a transition on an input line), 'W' (a code
word latched on a strobe edge), 'B' (a barcode the box emitted), 'L' (capture
events a path saw and could not record) or 'T' (a segment's trailer, which
`read_log` skips). **The barcodes are the 'B' records**: the box never
captures its own barcode line, which is an output (wl-sync's `pins.py`,
GPIO17; its edge inputs are GPIO20-27), and logs each frame it emits instead,
"the emitter's own account of what it put on the wire, not a measurement of
it" (`wl_sync/log.py`). Until 2026-10-22 this decoded edges on the line the
header's `gpio_map` named `barcode_out`, the shape only this project's
synthetic logs had: a real log yielded no barcodes (design spec
`2026-10-22-syncbox-records-and-segments-design.md` section 1).

A 'B' tick is read before the frame is sent, so it leads the frame's edge on
the wire by `barcode_delay_us`, which the box's header carries once its bench
loopback has measured it: "ADD IT TO A `B` TICK to get the wire time". It is
added here, the one place, and a header without it (unmeasured, or written
before 2026-10-30) leaves each tick as it is (design spec
`2026-11-04-syncbox-header-design.md` section 5). The code words ('W') and
the losses ('L') are carried as written; the edges ('E') are read by no stage
here and are not kept.

Kept in its own module, separate from `extract.py`, so the log format's shape
has exactly one reader.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wl_sync.log import BarcodeEmitted, CodeWord, Loss, read_log


@dataclass(frozen=True, slots=True)
class SyncBoxLog:
    """One segment of the sync box's log, with what its header says of it:
    the process that wrote it (`process_uuid`, None before 2026-10-30), the
    barcode delay added to its barcodes (None while unmeasured), the clocks
    its ticks count (`timebase`), and its barcodes `(value, tick_us)` as on
    the wire, its code words `(tick_us, word)` and its losses, each in the
    order the log holds them."""

    path: Path
    segment_index: int
    boot_id: str
    clock_trusted: bool
    clock_reason: str
    process_uuid: str | None
    barcode_delay_us: int | None
    timebase: str | None
    barcodes: tuple[tuple[int, int], ...]
    codes: tuple[tuple[int, int], ...]
    losses: tuple[Loss, ...]


def read_syncbox_log(path: Path) -> SyncBoxLog:
    """One segment, or `ValueError` naming it: a header or a record the
    format does not define (`wl_sync.log.read_log` validates both) refuses
    every stage that reads the session's sync box, and the file is what a
    person must open (design spec `2026-11-04-syncbox-header-design.md`
    section 3)."""
    try:
        header, records = read_log(path)
    except ValueError as error:
        raise ValueError(f"{path}: the sync box log cannot be read, so its session's timing stages refuse it: "
                         f"{error}") from error
    delay_us = header.barcode_delay_us or 0
    return SyncBoxLog(
        path=path, segment_index=header.segment_index, boot_id=header.boot_id,
        clock_trusted=header.clock_trusted, clock_reason=header.clock_reason,
        process_uuid=header.process_uuid, barcode_delay_us=header.barcode_delay_us, timebase=header.timebase,
        barcodes=tuple((record.value, record.tick_us + delay_us) for record in records
                       if isinstance(record, BarcodeEmitted)),
        codes=tuple((record.tick_us, record.word) for record in records if isinstance(record, CodeWord)),
        losses=tuple(record for record in records if isinstance(record, Loss)),
    )


def read_barcode_entries(path: Path) -> list[tuple[int, int]]:
    """`(value, tick_us)` for every barcode the box logged emitting, in the
    order the log holds them, each tick as on the wire.

    `wl_sync.log.read_log` validates the header as it parses it (a malformed
    header raises there, via `SyncBoxLogHeader`'s own pydantic validators),
    drops a torn last line, and skips the trailer. A log holding no 'B' record
    yields no barcodes, and a session whose sync box logged none has no
    session time (`timebase/segments.py::session_reference`).
    """
    return list(read_syncbox_log(path).barcodes)
