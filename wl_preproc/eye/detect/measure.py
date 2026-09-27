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


def measure_event_run(
    gaze_deg: np.ndarray,
    velocity_deg_s: np.ndarray,
    offered: np.ndarray,
    start: int,
    stop: int,
    fs_hz: float,
    *,
    runs_end_before_landing: bool,
    min_measured_ms: float | None,
    runs_start_after_takeoff: bool = False,
) -> Measurement | None:
    """One stored event run's measurement under its detector's declared rule,
    or `None` -- stored as NULL -- for a run too brief to measure.

    `offered` is the validity mask: `None` where a detector may label a
    sample. `stop` is exclusive, matching `Run`.

    **With neither rule declared this is exactly `measure`.** Every
    registered detector but NSLR declares neither, so their stored rows are
    unchanged.

    **`runs_end_before_landing`** is a property of how a detector's runs
    meet, declared on its `registry.Detector` entry. NSLR's segment `k` is
    `[J[k], J[k+1])`, and the eye lands at knot `J[k+1]`, the next run's
    first sample (design spec `2026-09-27-nslr-design.md` section 4). So
    `measure`'s `gaze[stop - 1] - gaze[start]` would miss the last step: a
    fraction `1/L` of an `L`-sample run, and all of it when `L` is 1. Such a
    run is measured up to where the eye lands instead -- amplitude
    `gaze[stop] - gaze[start]`, peak velocity over `[start, stop]` inclusive
    -- when `stop` is an interior knot:
    - `stop < n`;
    - the mask offered it (`offered[stop] is None`);
    - `gaze[stop]` is finite.

    Otherwise the run already ends at its piece's last sample, which is its
    own landing knot (`nslr.py::fit_pieces` places the last knot there), and
    it is measured as `measure` measures it.

    **`min_measured_ms`** is a paramset value (NSLR's
    `min_measured_saccade_ms`). A run lasting less, `(stop - start) /
    fs_hz`, is not measured: the requester's decision of 2026-09-27 (design
    spec section 4). `None` means no floor.

    **`runs_start_after_takeoff`** is BMD's, declared on its `registry.Detector`
    entry and passed for its microsaccade runs only. BMD's state-1 run
    `[t1, t2)` carries the eye from sample `t1 - 1` (design spec
    `2026-09-27-bmd-design.md` section 3.5), so such a run is measured from
    that take-off sample -- amplitude `gaze[stop - 1] - gaze[start - 1]`, peak
    velocity over `[start - 1, stop)` -- when `start - 1` exists, the mask
    offered it, and its gaze is finite. Otherwise it is measured as `measure`
    measures it. The requester's decision of 2026-09-27.

    `duration_s` is the run's own, `(stop - start) / fs_hz`, under every
    rule."""
    if min_measured_ms is not None and (stop - start) / fs_hz < min_measured_ms / 1000.0:
        return None
    lo, hi = start, stop
    if (
        runs_end_before_landing
        and stop < gaze_deg.shape[0]
        and offered[stop] is None
        and bool(np.isfinite(gaze_deg[stop]).all())
    ):
        hi = stop + 1
    if (
        runs_start_after_takeoff
        and start >= 1
        and offered[start - 1] is None
        and bool(np.isfinite(gaze_deg[start - 1]).all())
    ):
        lo = start - 1
    if (lo, hi) == (start, stop):
        return measure(gaze_deg, velocity_deg_s, start, stop, fs_hz)
    widened = measure(gaze_deg, velocity_deg_s, lo, hi, fs_hz)
    return Measurement(
        amplitude_deg=widened.amplitude_deg,
        peak_velocity_deg_s=widened.peak_velocity_deg_s,
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
