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

from wl_preproc.eye.detect.labels import Run
from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude, classify

# lambda = 6 and a 6-sample minimum (12 ms at 500 Hz) are the paper's own
# conventional values, and what nearly every reimplementation uses.
DEFAULT_LAMBDA = 6.0
DEFAULT_MIN_DURATION_SAMPLES = 6


@dataclass(frozen=True, slots=True)
class EngbertKlieglParams:
    lambda_: float
    min_duration_samples: int
    # **Declared here, but not this detector's own parameter.** It lives in
    # the SHARED `eye_detection` paramset (`schema/detect.py::register_
    # default_paramsets`, which registers it beside `detector` rather than
    # inside any one detector's defaults), because every detector whose
    # vocabulary splits by amplitude must split at the SAME place -- design
    # spec section 3's whole argument for measuring centrally applies to the
    # threshold that measurement is compared against.
    #
    # Declaring the field is how this detector STATES that it consumes that
    # shared key: `schema/detect.py::_params_for` hands over exactly the
    # paramset keys a detector's own dataclass names, so a detector with no
    # amplitude-derived labels (U'n'Eye emits `saccade` alone, design spec
    # section 3.1) simply does not declare it and never receives it -- rather
    # than being handed a threshold it has no use for.
    #
    # Otero-Millan was this example until 2026-09-01, on the strength of a
    # `microsaccade`-only vocabulary that reading its reference disproved. It
    # declares this field, for exactly the reason given above.
    #
    # The default is `measure.MICROSACCADE_MAX_DEG` itself, by reference and
    # never a copied literal, so this is not a second place the conventional
    # cut could drift to.
    microsaccade_max_deg: float = MICROSACCADE_MAX_DEG


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
) -> list[Run]:
    """Labelled half-open `[start, stop)` intervals, in sample indices.

    `available` is the validity mask: `None` where a detector may label, a
    `Label` where the mask has already claimed the sample. **Unavailable
    samples are excluded from the threshold estimate as well as from the
    output** -- a blink's velocity spike would otherwise inflate the scale and
    desensitise the detector for the whole recording.

    **The saccade/microsaccade split is THIS detector's own work, not shared
    post-processing.** Design spec section 3.1 gives Engbert-Kliegl the
    vocabulary "saccade / microsaccade", and four of the seven planned
    detectors declare vocabularies including `pso`, `pursuit` or `fixation`
    that no amplitude threshold could ever produce -- so a shared step that
    labelled every detector's spans by amplitude would silently overwrite
    what those four detected. Labelling belongs to each detector;
    MEASUREMENT stays shared, which is why the split below goes through
    `measure.py`'s own `amplitude` and `classify` rather than a private copy
    of either.

    `gaze_deg` is what that split reads -- velocity alone finds the events
    (the elliptic test below), and position is what says how far the eye
    actually went.
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
        Run(
            start=start,
            stop=stop,
            label=classify(amplitude(gaze_deg, start, stop), params.microsaccade_max_deg),
        )
        for start, stop in _true_runs(outside)
        if stop - start >= params.min_duration_samples
    ]


def _true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Maximal `True` stretches as half-open intervals."""
    padded = np.concatenate(([False], mask, [False]))
    edges = np.diff(padded.astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True))
