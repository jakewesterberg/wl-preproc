"""Amplitude, peak velocity and duration -- computed once, for every detector.

**Detectors return intervals; this measures them** (design spec section 3).
All seven natively produce different things, and if each computed its own
amplitude the agreement metric would compare MEASUREMENTS as well as
detections, making a disagreement uninterpretable. Measuring here also means
the main sequence that vigor is fitted against is the same measurement
whichever detector found the saccade.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from wl_preproc.eye.detect.labels import Label

# The conventional microsaccade cut. A paramset overrides it; this is the
# default and the lab will want to move it -- amplitude distributions are
# continuous and the boundary is a convention, not a fact about the eye.
MICROSACCADE_MAX_DEG = 1.0


@dataclass(frozen=True, slots=True)
class Measurement:
    amplitude_deg: float
    peak_velocity_deg_s: float
    duration_s: float


def measure(
    gaze_deg: np.ndarray,
    velocity_deg_s: np.ndarray,
    start: int,
    stop: int,
    fs_hz: float,
) -> Measurement:
    """Measure one interval. `stop` is exclusive, matching `Run`.

    **Amplitude is endpoint-to-endpoint displacement, not path length.** A
    saccade's amplitude is where the eye ended up relative to where it began;
    path length would count post-saccadic wobble on the way as extra
    amplitude, which is exactly the contamination design spec section 6.5.3
    names as shifting the whole main sequence.

    **Peak velocity is bounded to the interval.** A faster sample just outside
    belongs to a different event.
    """
    displacement = gaze_deg[stop - 1] - gaze_deg[start]
    speed = np.hypot(velocity_deg_s[start:stop, 0], velocity_deg_s[start:stop, 1])
    return Measurement(
        amplitude_deg=float(np.hypot(displacement[0], displacement[1])),
        peak_velocity_deg_s=float(speed.max()) if speed.size else 0.0,
        duration_s=float(stop - start) / fs_hz,
    )


def classify(amplitude_deg: float, microsaccade_max_deg: float) -> Label:
    """`saccade` at or above the threshold, `microsaccade` below it.

    At-or-above is stated rather than left to the reader: a boundary
    convention nobody writes down is one every reimplementation gets to choose
    differently, and six of this subsystem's seven detectors are
    reimplementations.
    """
    return Label.MICROSACCADE if amplitude_deg < microsaccade_max_deg else Label.SACCADE
