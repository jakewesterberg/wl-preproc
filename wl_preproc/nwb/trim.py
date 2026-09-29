"""An activation's block set, and what falls inside it (design spec
`2026-09-28-nwb-builder-design.md` section 5; parent spec section 8.1: each
NWB is self-contained over its own activation's block set)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BlockSet:
    """Half-open `[start_s, end_s)` intervals, one per block."""

    intervals: tuple[tuple[float, float], ...]

    @classmethod
    def of(cls, blocks: list[dict]) -> BlockSet:
        """Blocks that touch are merged, so a stretch that crosses from one
        into the next is clipped once, to the edges of their union, and
        stays one stretch."""
        merged: list[list[float]] = []
        for start, end in sorted((float(b["start_s"]), float(b["end_s"])) for b in blocks):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return cls(tuple((start, end) for start, end in merged))

    def contains(self, times) -> np.ndarray:
        """Which of `times` fall inside a block."""
        times = np.asarray(times, dtype=float)
        inside = np.zeros(times.shape, dtype=bool)
        for start, end in self.intervals:
            inside |= (times >= start) & (times < end)
        return inside

    def contains_instant(self, times) -> np.ndarray:
        """Which of `times` fall inside a block, its END INCLUDED. An event is
        an instant, and the one exactly at a block's end -- the block's own
        `BLOCK_END` marker -- belongs to that block; half-open intervals are
        for samples, so adjacent blocks never share one."""
        times = np.asarray(times, dtype=float)
        inside = np.zeros(times.shape, dtype=bool)
        for start, end in self.intervals:
            inside |= (times >= start) & (times <= end)
        return inside

    def clip(self, start: float, stop: float) -> list[tuple[float, float]]:
        """`[start, stop)` cut to the blocks: one piece per block it
        overlaps, none if it overlaps none."""
        return [(max(start, b_start), min(stop, b_end)) for b_start, b_end in self.intervals
                if start < b_end and stop > b_start]
