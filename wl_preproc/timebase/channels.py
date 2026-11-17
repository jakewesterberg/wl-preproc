"""One digital channel of a recording, read in chunks (design spec
`2026-11-14-scaling-long-sessions-design.md` section 4.1).

SpikeGLX's `.nidq.bin` interleaves every channel, one int16 each per sample,
and the Intan RHS's `digitalin.dat` is one uint16 word per sample. Reading
either whole costs the whole file in memory -- tens of GB for 12 hours of NI --
and then wl-sync's `edges_from_samples` walked every sample in Python. Here
the one channel wanted is read through a memory map, a bounded chunk at a
time, and its level changes are found with array operations.

**The edges are wl-sync's, exactly.** `edges_from_samples` yields an edge at
sample 0 (its `previous` state starts `None`) and at each sample whose level
differs from the one before, timed `round(index / fs_hz * 1_000_000)`.
`bit_edges` computes the same IEEE quotient and product per sample and rounds
half to even as `round` does, so each tick is the same integer.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

import numpy as np

#: Rows per chunk: 8 million NI samples of one int16 word is 16 MB, plus
#: the few arrays of the same length computed from it.
CHUNK_ROWS = 8_000_000


def n_rows(path: Path, n_channels: int, dtype) -> int:
    """Whole samples in `path`, `n_channels` values of `dtype` each; a
    trailing partial value is not counted, as `np.fromfile` does not read it.
    Callers check `n_values(path, dtype) % n_channels` themselves, in their
    own words."""
    return n_values(path, dtype) // n_channels


def n_values(path: Path, dtype) -> int:
    """Values of `dtype` in `path`, as `np.fromfile` would read them."""
    return Path(path).stat().st_size // np.dtype(dtype).itemsize


def column_chunks(
    path: Path, n_channels: int, column: int, dtype, chunk_rows: int = CHUNK_ROWS
) -> Iterator[np.ndarray]:
    """Column `column` of `path`'s `(samples, n_channels)` values, as uint16
    chunks of at most `chunk_rows` samples, in order. Each chunk is a copy, so
    nothing holds the map open past it. An int16 word becomes the uint16 with
    the same bits, as `astype(np.uint16)` makes it."""
    rows = n_rows(path, n_channels, dtype)
    if rows == 0:
        return
    mapped = np.memmap(path, dtype=dtype, mode="r", shape=(rows, n_channels))
    try:
        for start in range(0, rows, chunk_rows):
            yield np.array(mapped[start:start + chunk_rows, column]).astype(np.uint16)
    finally:
        del mapped


def bit_edges(chunks: Iterable[np.ndarray], bit: int, fs_hz: float) -> tuple[tuple[int, int], ...]:
    """`(tick_us, level)` for bit `bit` of the words `chunks` carry, equal to
    `wl_sync.barcode.edges_from_samples` over the whole trace: sample 0, then
    every sample whose level differs from the one before, a change across a
    chunk boundary found once.

    `bit` is ZERO-BASED. Intan's own documentation numbers bits from 1, and
    reading it literally keys a mask to the wrong signal silently (parent spec
    section 6.3). The edge at sample 0 is what makes the idle before the first
    barcode verifiable by a caller that decodes without passing `start_us`."""
    indices: list[np.ndarray] = []
    levels: list[np.ndarray] = []
    offset = 0
    previous: int | None = None
    for chunk in chunks:
        level = ((chunk >> bit) & 1).astype(np.int8)
        if level.size == 0:
            continue
        changed = np.flatnonzero(np.diff(level)) + 1
        if previous is None or level[0] != previous:
            changed = np.concatenate(([0], changed))
        indices.append(changed + offset)
        levels.append(level[changed])
        previous = int(level[-1])
        offset += level.size
    if not indices:
        return ()
    index = np.concatenate(indices)
    ticks = np.rint(index / fs_hz * 1_000_000).astype(np.int64)
    return tuple(zip(ticks.tolist(), np.concatenate(levels).astype(int).tolist(), strict=True))


def _with_previous(chunks: Iterable[np.ndarray]) -> Iterator[tuple[int, np.ndarray]]:
    """Each chunk with the last sample of the one before prepended, and the
    global index of that first element: a change that straddles a boundary
    is then inside one array."""
    carried: np.ndarray | None = None
    offset = 0
    for chunk in chunks:
        if chunk.size == 0:
            continue
        if carried is None:
            yield offset, chunk
        else:
            yield offset - 1, np.concatenate((carried, chunk))
        carried = chunk[-1:]
        offset += chunk.size


def strobe_falls(
    control_chunks: Iterable[np.ndarray], data_chunks: Iterable[np.ndarray], bit: int
) -> tuple[tuple[int, int], ...]:
    """`(index, data word)` at each sample where bit `bit` of the control word
    is 1 and the next sample's is 0: the strobe's far edge, data read at that
    sample (`events/extract.py::extract_nidq_words`)."""
    words: list[tuple[int, int]] = []
    for (start, control), (_, data) in zip(_with_previous(control_chunks), _with_previous(data_chunks), strict=True):
        strobe = (control >> bit) & 1
        falling = np.flatnonzero((strobe[:-1] == 1) & (strobe[1:] == 0))
        words.extend(zip((falling + start).tolist(), data[falling].astype(int).tolist(), strict=True))
    return tuple(words)


def strobe_rises(chunks: Iterable[np.ndarray], bit: int) -> tuple[int, ...]:
    """The index of each sample where bit `bit` is 1 and the sample before's
    is 0 (`events/extract.py::extract_rhs_witness`)."""
    rises: list[int] = []
    for start, words in _with_previous(chunks):
        strobe = (words >> bit) & 1
        rises.extend((np.flatnonzero((strobe[1:] == 1) & (strobe[:-1] == 0)) + 1 + start).tolist())
    return tuple(rises)
