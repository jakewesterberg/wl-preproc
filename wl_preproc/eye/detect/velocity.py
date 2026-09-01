"""The one velocity estimator every detector shares.

**This is the most consequential preprocessing decision in the subsystem**
(design spec section 3). Every threshold-based method inherits its
differentiator, so seven private ones would make every between-detector
disagreement partly a disagreement about smoothing -- and the agreement metric
exists precisely to attribute disagreement to method. It is also what the
validity mask's speed criterion needs, so it exists before any detector runs.

The five-point weighted difference is Engbert & Kliegl's own estimator,
adopted here as the shared one because the baseline detector is calibrated
against it and because averaging over five samples suppresses the sample-level
noise a two-point difference amplifies.
"""

from __future__ import annotations

import numpy as np

# Samples on each side of the sample being estimated. The five-point estimator
# spans `[n-2, n+2]`, so the first and last two samples of any trace have no
# window.
_HALF_WINDOW = 2

# The denominator of Engbert & Kliegl's five-point weighted difference:
# `(x[n+2] + x[n+1] - x[n-1] - x[n-2]) / (6 * dt)`.
_WEIGHT_SUM = 6.0


def velocity(gaze_deg: np.ndarray, fs_hz: float) -> np.ndarray:
    """Degrees per second, per axis, one row per sample.

    **The two samples at each edge are zero, not extrapolated.** A fabricated
    edge velocity is indistinguishable from a real one to every detector
    downstream, and an event detected in the first two samples of a recording
    is an artifact of the estimator rather than of the eye. Stating zero makes
    that region quiet instead of wrong.
    """
    out = np.zeros_like(gaze_deg, dtype=float)
    if gaze_deg.shape[0] < 2 * _HALF_WINDOW + 1:
        return out
    interior = slice(_HALF_WINDOW, gaze_deg.shape[0] - _HALF_WINDOW)
    out[interior] = (
        gaze_deg[4:] + gaze_deg[3:-1] - gaze_deg[1:-3] - gaze_deg[:-4]
    ) * (fs_hz / _WEIGHT_SUM)
    return out
