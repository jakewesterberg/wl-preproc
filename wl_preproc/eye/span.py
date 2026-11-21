"""The covered span, as a mask over session times (design spec
`2026-10-18-training-records-and-backup-clock-design.md`, amendment 58).

Under the NI's clock with a sync box that stopped partway, the eye tracker and
the cameras are placed through the NI's barcodes only over the stretch those
barcodes cover: `timebase/clock.py::Clock.covered_span`, `[first, last]` in
session seconds. A sample outside it is placed by extrapolating the fit, which
nothing verified, so no stage uses it. This module is the one statement of
"inside": every eye stage, the coverage tables and the NWB file ask it here.

`None` is the whole session: the sync box keeps time, or the NI decoded no
barcode and nothing was placed by barcode at all.
"""

from __future__ import annotations

import numpy as np

Span = tuple[float, float]


def inside_span(times: np.ndarray, span: Span | None) -> np.ndarray:
    """One truth value per session time: inside `span`, its ends included.
    All True for `None`."""
    times = np.asarray(times, dtype=float)
    if span is None:
        return np.ones(times.shape, dtype=bool)
    start_s, end_s = span
    return (times >= start_s) & (times <= end_s)


def rows_inside(times: np.ndarray, span: Span | None) -> slice | None:
    """The rows of one recording inside `span`, as one slice, `times` being
    each row's session time in recording order; `None` for `None`, the whole
    recording. An empty slice where no row is inside.

    One slice, because a recording's times increase and a span is one
    interval, so its rows inside are contiguous. Raises where they are not:
    times that do not increase are no recording's."""
    if span is None:
        return None
    inside = inside_span(times, span)
    rows = np.flatnonzero(inside)
    if rows.size == 0:
        return slice(0, 0)
    found = slice(int(rows[0]), int(rows[-1]) + 1)
    if not inside[found].all():
        raise ValueError("the rows inside the covered span are not contiguous: their session times do not increase")
    return found


def clip_to_span(start_s: float, end_s: float, span: Span | None) -> Span | None:
    """The extent `[start_s, end_s)` cut to `span`, or `None` where nothing
    of it is inside. Unchanged for a `None` span."""
    if span is None:
        return (start_s, end_s)
    start_s, end_s = max(start_s, span[0]), min(end_s, span[1])
    return (start_s, end_s) if end_s > start_s else None
