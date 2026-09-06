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


def amplitude(gaze_deg: np.ndarray, start: int, stop: int) -> float:
    """One interval's amplitude in degrees. `stop` is exclusive, matching `Run`.

    **Split out of `measure` so a DETECTOR can call it, and there is still
    exactly one implementation.** Design spec section 3's detector signature
    now carries `fs_hz` too (`detect(gaze_deg, velocity, valid, fs_hz,
    params)`, amended in place -- `2026-08-31-saccade-detection-design.md`
    section 3), so a detector calling this directly is not fabricating a
    sampling rate it does not have; that was this function's original
    reason for existing, and it no longer holds now that every detector is
    handed one.

    The split still holds, for a narrower reason: `amplitude` needs neither
    `fs_hz` nor velocity at all, so a detector comparing amplitudes MID-
    DETECTION -- classifying saccade versus microsaccade
    (`engbert_kliegl.py::detect_engbert_kliegl`, and every other entry in
    design spec section 3.1's table that names both `saccade` and
    `microsaccade`), or comparing a glissade's amplitude against its
    preceding saccade's (`nystrom_holmqvist.py::_glissade_bounds`) -- can
    call this directly on `gaze_deg` alone, rather than building a velocity
    slice and calling the fuller `measure` merely to read one of its three
    returned fields. And a private amplitude formula inside the detector
    would still break section 3's own guarantee that a disagreement is
    "never a disagreement about measurement": this function is what both
    `measure` below and the detector call, so that guarantee holds
    literally: one formula, one caller-independent answer.

    **Precondition:** `stop > start`, enforced by `measure` and again here --
    `gaze_deg[stop - 1]` on an empty interval reads the wrong end of the
    array (`start=stop=0` accesses `gaze_deg[-1]`) rather than raising.

    **Endpoint-to-endpoint displacement, not path length.** A saccade's
    amplitude is where the eye ended up relative to where it began; path
    length would count post-saccadic wobble on the way as extra amplitude,
    which is related to the contamination design spec section 6.5.3 names as
    shifting the whole main sequence.
    """
    if stop <= start:
        raise ValueError(f"amplitude requires stop > start; got start={start}, stop={stop}")
    displacement = gaze_deg[stop - 1] - gaze_deg[start]
    return float(np.hypot(displacement[0], displacement[1]))


def measure(
    gaze_deg: np.ndarray,
    velocity_deg_s: np.ndarray,
    start: int,
    stop: int,
    fs_hz: float,
) -> Measurement:
    """Measure one interval. `stop` is exclusive, matching `Run`.

    **Precondition:** `stop > start`. Empty intervals are invalid; they
    silently read nonsensical indices (e.g., `start=stop=0` accesses
    `gaze_deg[-1]`). Raises ValueError naming the offending values.

    **Amplitude comes from `amplitude` above**, the same function a detector
    that splits by amplitude calls to label its own intervals -- see that
    function's own docstring for why it is separable at all.

    **Peak velocity is bounded to the interval.** A faster sample just outside
    belongs to a different event. The `speed.size` guard makes this safe; it
    could be removed but is kept for symmetry.
    """
    if stop <= start:
        raise ValueError(f"measure requires stop > start; got start={start}, stop={stop}")
    speed = np.hypot(velocity_deg_s[start:stop, 0], velocity_deg_s[start:stop, 1])
    return Measurement(
        amplitude_deg=amplitude(gaze_deg, start, stop),
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
