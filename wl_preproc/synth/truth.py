"""What was planted. Returned alongside the files so tests assert *recovery*.

A test that recomputes expectations from generator internals tests nothing —
it agrees with itself. Ground truth exists so the assertion crosses the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from wl_preproc.contracts.events import TaskTypeCode
from wl_preproc.synth.stim import StimEvent


@dataclass(frozen=True, slots=True)
class TrialTruth:
    trial_id: int
    block_id: int
    start_s: float
    end_s: float


@dataclass(frozen=True, slots=True)
class BlockTruth:
    block_id: int
    task_type: TaskTypeCode
    start_s: float
    end_s: float


@dataclass(frozen=True, slots=True)
class GroundTruth:
    barcodes: tuple[tuple[int, float], ...]      # (value, session-time seconds)
    code_words: tuple[tuple[float, int], ...]    # (session-time seconds, 16-bit word)
    trials: tuple[TrialTruth, ...]
    blocks: tuple[BlockTruth, ...]
    spikes: tuple[tuple[float, int], ...]        # (session-time seconds, channel)
    stim_events: tuple[StimEvent, ...] = ()
