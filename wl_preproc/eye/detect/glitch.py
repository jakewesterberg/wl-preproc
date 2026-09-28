"""Tracker glitches, repaired before anything reads the gaze.

**A glitch is gaze that leaves and comes back faster than an eye can move**
(the requester's decision of 2026-09-28; handoff
`docs/handoffs/2026-09-28-gaze-glitches.md`): a stretch shorter than
`max_glitch_ms`, entered and left by sample-to-sample jumps that each exceed
the validity mask's own `max_speed_deg_s`, in opposite directions. It is
replaced by the straight line between the samples either side.

**Why the mask's speed criterion does not catch it.** That criterion reads
the shared five-point velocity (`velocity.py`), which spreads a one-sample
excursion of `A` degrees over its window and reports about `A * fs / 6`: a
10 deg glitch at 500 Hz reads about 830 deg/s, under a 1000 deg/s limit.
Measured raw, sample to sample, the same glitch is 5,000 deg/s.

**Why out AND back, and why repaired rather than withheld.** On the lab's
reference recording (OpenIrisDPI's tutorial recording):
- a lone jump over the limit mostly sat inside a real saccade (about 750
  of 840), so a jump alone is not evidence of a glitch;
- withholding each glitch, widened like the mask's other criteria, dropped
  every saccade it fell inside, real ones included;
- repaired, the saccades that contain one sit close to the main sequence of
  glitch-free saccades (median +0.3 to +0.6 sd, against +1.8 to +2.5 sd
  unrepaired for Engbert-Kliegl and Nystrom-Holmqvist, +1.0 sd for BMD),
  while saccades that were only a glitch disappear.

**Why shorter than `max_glitch_ms`**: at 10 ms, the shortest saccade
Nystrom-Holmqvist and REMoDNaV accept, an out-and-back can no longer be ruled
out as two eye movements."""

from __future__ import annotations

import math

import numpy as np


def repair_glitches(
    gaze_deg: np.ndarray,
    fs_hz: float,
    max_speed_deg_s: float,
    max_glitch_ms: float,
) -> tuple[np.ndarray, np.ndarray]:
    """`gaze_deg` with every glitch replaced by the straight line across it,
    and a per-sample boolean array marking the samples replaced.

    A stretch `[i, j)` is a glitch when `j - i` samples last less than
    `max_glitch_ms`, the jump into `i` (from `i - 1`) and the jump into `j`
    (from `j - 1`) both exceed `max_speed_deg_s`, and the two point in
    opposite directions. A jump next to a non-finite sample is never fast
    (the comparison is false for NaN), so a glitch beside missing data is
    left to the validity mask's `non_finite` criterion. The input is not
    modified."""
    original = np.asarray(gaze_deg, dtype=float)
    gaze = original.copy()
    repaired = np.zeros(original.shape[0], dtype=bool)
    if original.shape[0] < 3:
        return gaze, repaired

    steps = np.diff(original, axis=0)  # steps[k] = gaze[k + 1] - gaze[k]
    with np.errstate(invalid="ignore"):
        fast = np.hypot(steps[:, 0], steps[:, 1]) * fs_hz > max_speed_deg_s
    # Strictly shorter than `max_glitch_ms`: 4 samples at 500 Hz (8 ms), 9 at
    # 1000 Hz.
    max_width = math.ceil(max_glitch_ms * fs_hz / 1000.0) - 1

    next_free = 1
    for entry in np.flatnonzero(fast):
        start = int(entry) + 1  # the first sample the fast jump lands on
        if start < next_free:
            continue
        for width in range(1, max_width + 1):
            stop = start + width
            if stop >= original.shape[0]:
                break
            if fast[stop - 1] and float(np.dot(steps[start - 1], steps[stop - 1])) < 0.0:
                before, after = original[start - 1], original[stop]
                for k in range(start, stop):
                    gaze[k] = before + (after - before) * (k - start + 1) / (width + 1)
                repaired[start:stop] = True
                next_free = stop + 1
                break
    return gaze, repaired
