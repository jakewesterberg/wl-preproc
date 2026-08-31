"""Engbert & Kliegl's velocity-threshold detector -- the always-on baseline.

Reimplemented from the published algorithm rather than vendored (design spec
section 3.2): it is small, fully specified, and reimplementing means it shares
this subsystem's one velocity estimator instead of bringing a private one.

The threshold is a MEDIAN-based estimate of the velocity distribution's scale,
not a standard deviation: a real standard deviation is inflated by the very
saccades the threshold is meant to find, so the detector would grow less
sensitive exactly as a session contained more of what it looks for.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# lambda = 6 and a 6-sample minimum (12 ms at 500 Hz) are the paper's own
# conventional values, and what nearly every reimplementation uses.
DEFAULT_LAMBDA = 6.0
DEFAULT_MIN_DURATION_SAMPLES = 6


@dataclass(frozen=True, slots=True)
class EngbertKlieglParams:
    lambda_: float
    min_duration_samples: int


DEFAULT_EK_PARAMS = EngbertKlieglParams(
    lambda_=DEFAULT_LAMBDA, min_duration_samples=DEFAULT_MIN_DURATION_SAMPLES
)


def _median_scale(component: np.ndarray) -> float:
    """`sqrt(median(v^2) - median(v)^2)`, the paper's own scale estimate.

    **The `variance > 0` guard is not theoretical caution.** The quantity is
    mathematically bounded below by zero (an order-statistics argument: at
    most half of any sample can have smaller magnitude than its own median,
    so `median(v^2)` can never fall below `median(v)^2`) -- but that bound is
    about exact arithmetic. In float64, two near-tied values landing as the
    two middle order statistics can make the subtraction round to a genuine,
    reproducible small NEGATIVE number (task-5-report.md measures
    -1.7763568394002505e-15 on a literal four-sample array), and `sqrt` of
    that is `nan`, not a domain error -- silent, not loud. The guard turns
    that `nan` back into the zero it should have been.
    """
    if component.size == 0:
        return 0.0
    variance = float(np.median(component**2) - np.median(component) ** 2)
    return float(np.sqrt(variance)) if variance > 0 else 0.0


def detect_engbert_kliegl(
    gaze_deg: np.ndarray,
    velocity_deg_s: np.ndarray,
    available: np.ndarray,
    params: EngbertKlieglParams,
) -> list[tuple[int, int]]:
    """Half-open `(start, stop)` intervals, in sample indices.

    `available` is the validity mask: `None` where a detector may label, a
    `Label` where the mask has already claimed the sample. **Unavailable
    samples are excluded from the threshold estimate as well as from the
    output** -- a blink's velocity spike would otherwise inflate the scale and
    desensitise the detector for the whole recording.

    `gaze_deg` is part of the shared detector signature (design spec section
    3: `detect(gaze_deg, velocity, valid, params)`) but unused here -- this
    method thresholds velocity alone. Other registry entries need positions.
    """
    usable = np.array([entry is None for entry in available], dtype=bool)
    if not usable.any():
        return []

    eta_x = params.lambda_ * _median_scale(velocity_deg_s[usable, 0])
    eta_y = params.lambda_ * _median_scale(velocity_deg_s[usable, 1])
    if eta_x <= 0 or eta_y <= 0:
        return []

    # The paper's elliptic test: a sample is in an event when the velocity
    # vector lies outside the ellipse whose semi-axes are the two thresholds.
    outside = (velocity_deg_s[:, 0] / eta_x) ** 2 + (
        velocity_deg_s[:, 1] / eta_y
    ) ** 2 > 1.0
    outside &= usable

    return [
        (start, stop)
        for start, stop in _true_runs(outside)
        if stop - start >= params.min_duration_samples
    ]


def _true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Maximal `True` stretches as half-open intervals."""
    padded = np.concatenate(([False], mask, [False]))
    edges = np.diff(padded.astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True))
