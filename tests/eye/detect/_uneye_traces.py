"""Synthetic gaze for U'n'Eye's tests: saccades of stated amplitudes, with
known onsets, between fixations (design spec `2026-10-06-uneye-design.md`
section 5)."""

from __future__ import annotations

import numpy as np

#: The reference recording's rate (saccade-detection design spec section 5).
FS_HZ = 498.55


def planted(amplitudes_deg, fs_hz: float = FS_HZ, seed: int = 0) -> tuple[np.ndarray, list[int]]:
    """Gaze in degrees, `(n, 2)`, and each saccade's first sample.

    One saccade per amplitude, in order, each in a random direction, turned
    back toward the centre where it would pass 8 deg. Each lasts its
    main-sequence duration, `2.2 * amplitude + 21` ms, with a raised-cosine
    profile, between 0.3-0.6 s fixations. 0.01 deg of noise on everything."""
    rng = np.random.default_rng(seed)
    pieces, onsets, position, t = [], [], np.zeros(2), 0
    for amplitude in amplitudes_deg:
        hold = int(rng.integers(int(0.3 * fs_hz), int(0.6 * fs_hz)))
        pieces.append(np.tile(position, (hold, 1)))
        t += hold
        angle = rng.uniform(0, 2 * np.pi)
        step = amplitude * np.array([np.cos(angle), np.sin(angle)])
        if np.hypot(*(position + step)) > 8:
            step = -step
        samples = max(2, int(round((2.2 * amplitude + 21) * fs_hz / 1000)))
        profile = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, samples))
        pieces.append(position + np.outer(profile, step))
        onsets.append(t)
        t += samples
        position = position + step
    tail = int(0.5 * fs_hz)
    pieces.append(np.tile(position, (tail, 1)))
    gaze = np.concatenate(pieces) + rng.normal(0, 0.01, (t + tail, 2))
    return gaze, onsets


def all_usable(n: int) -> np.ndarray:
    """A validity mask offering every sample."""
    return np.full(n, None, dtype=object)
