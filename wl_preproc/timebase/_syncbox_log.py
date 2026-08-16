"""The sync box log's one reader.

`wl_sync.log` defines the on-disk shape: a JSON header line, then one CSV
line per record, type-tagged 'E' (a GPIO transition) or 'W' (a code word
latched on a strobe edge). The barcode is carried on whichever GPIO the
header's own `gpio_map` names as the barcode line -- not a hardcoded pin
number, because the header exists precisely so a reader never has to assume
one. Recovering "barcode entries" from that line means running wl-sync's own
frame decoder over its edges, never re-deriving the frame shape by hand.

Kept in its own module, separate from `extract.py`, so the log format's shape
has exactly one reader.
"""

from __future__ import annotations

from pathlib import Path

from wl_sync.barcode import decode_edges
from wl_sync.log import Edge, read_log

# `wl_preproc.synth.syncbox.write_syncbox_log` is the log's one writer today,
# and this is the key it stamps into the header's gpio_map for the barcode
# line. wl_sync treats gpio_map as an opaque dict[str, int] -- the name is
# this project's own convention, not wl-sync's, so it is restated here as a
# constant rather than imported from the synth package: a production reader
# has no business depending on the synthetic fixture generator, even one that
# happens to share its vocabulary.
_BARCODE_GPIO_KEY = "barcode_out"


def read_barcode_entries(path: Path) -> list[tuple[int, int]]:
    """Decode the sync box log's barcode line into (value, timestamp_us) pairs.

    `wl_sync.log.read_log` validates the header as it parses it (a malformed
    header raises there, via `SyncBoxLogHeader`'s own pydantic validators) and
    returns every record the log carries -- interleaving whatever GPIO lines
    the sync box captured plus any strobed code words. Filtering to the line
    the header names as the barcode, then handing those edges to
    `wl_sync.barcode.decode_edges`, is the decode this function exists to own,
    so every caller gets it for free instead of re-parsing the log itself.

    `start_us=0` is passed to `decode_edges`: tick 0 is the log's own native
    time origin, and the RP1 tick counter is known LOW there (nothing has
    driven the line yet). Without it, `decode_edges` cannot verify the idle
    preceding the very first frame and correctly refuses to decode it -- see
    that function's own docstring.
    """
    header, records = read_log(path)
    try:
        barcode_gpio = header.gpio_map[_BARCODE_GPIO_KEY]
    except KeyError as exc:
        raise ValueError(
            f"{path}: gpio_map has no {_BARCODE_GPIO_KEY!r} entry; found "
            f"{sorted(header.gpio_map)}"
        ) from exc

    edges = [
        (record.tick_us, record.level)
        for record in records
        if isinstance(record, Edge) and record.gpio == barcode_gpio
    ]
    return [(barcode.value, barcode.start_us) for barcode in decode_edges(edges, start_us=0)]
