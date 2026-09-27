"""Synthetic gaze for NSLR's tests -- pure numpy, so the 3.13 cross-check
runs everything that uses them.

**Why not REMoDNaV's `gaze_trace`.** Its post-saccadic wobble is too small
for NSLR ever to call a PSO, and its pursuit sits at 8.0-9.5 s, beyond the
few seconds the slow reference can afford. Here every saccade overshoots and
settles: a damped oscillation of `wobble_frac` of its amplitude at
`wobble_hz`.

Measured while planning: at the defaults, the 3 s traces for (500 Hz, seeds
4, 12, 17) and (1000 Hz, seeds 1, 2) each contain all four classes, and each
is classified identically by this repository's NSLR and the reference.
"""

from __future__ import annotations

import numpy as np


def overshoot_trace(fs_hz: float, seed: int, seconds: float = 3.0,
                    wobble_frac: float = 0.10, wobble_hz: float = 20.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(round(seconds * fs_hz))
    target = np.zeros((n, 2))
    position = np.zeros(2)
    cursor = 0.3
    while cursor < seconds - 0.3:
        amplitude = rng.uniform(3.0, 12.0)
        angle = rng.uniform(0.0, 2.0 * np.pi)
        step = amplitude * np.array([np.cos(angle), np.sin(angle)])
        if np.any(np.abs(position + step) > 15.0):
            step = -step
        first = int(round(cursor * fs_hz))
        count = max(int(round((2.2 * amplitude + 21.0) / 1000.0 * fs_hz)), 2)
        tau = np.linspace(0.0, 1.0, count)
        profile = 10 * tau**3 - 15 * tau**4 + 6 * tau**5
        target[first:first + count] = position + np.outer(profile, step)
        position = position + step
        target[first + count:] = position
        wobble_t = np.arange(int(round(0.06 * fs_hz))) / fs_hz
        wobble = wobble_frac * amplitude * np.exp(-wobble_t / 0.015) * np.sin(2 * np.pi * wobble_hz * wobble_t)
        stop = min(first + count + wobble.size, n)
        target[first + count:stop] += np.outer(wobble[: stop - first - count], step / amplitude)
        cursor += rng.uniform(0.3, 0.6)
    drift = np.cumsum(rng.normal(0.0, 0.07 / np.sqrt(fs_hz), (n, 2)), axis=0)
    return target + drift + rng.normal(0.0, 0.01, (n, 2))
