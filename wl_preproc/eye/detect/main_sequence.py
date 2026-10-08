"""The main sequence and vigor (design spec
`2026-10-07-main-sequence-design.md`): which saccades a fit takes, the
session's fit, a group's gain against it, and a session's vigor against the
same animal's earlier sessions.

Pure functions over arrays. `schema/main_sequence.py` stores what the first
three return; `cli/report.py` computes vigor at report time and never stores
it, because the history it is measured against grows with every session
(saccade-detection design spec section 6.5.1).

**Only a whole session is fitted.** Measured on the reference recording, a
two-parameter fit over a block's or a condition's 20-100 saccades is 12-55%
wrong at the median, while one gain against the session's own curve is within
4-7% (spec section 1.4). So blocks and conditions get a gain: the requester's
decision of 2026-10-07.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from wl_preproc.eye.detect.labels import Label

#: The fit forms built. A paramset names one, so another is new rows later.
FIT_FORMS = ("saturating_exponential",)

#: The report's minimum history: below it a session's vigor is not shown
#: (spec section 5). A report constant, not a paramset value: it decides what
#: is shown, not what is stored.
MIN_HISTORY_SESSIONS = 3

_SACCADIC = (Label.SACCADE.value, Label.MICROSACCADE.value)


@dataclass(frozen=True, slots=True)
class MainSequenceParams:
    """The `main_sequence` paramset (spec section 4.4).
    - `min_amplitude_deg`, `max_duration_ms`: which saccades are taken
      (section 4.1). The floor replaces the saccade-detection spec's
      "include microsaccades" switch.
    - `min_session_saccades`, `min_amplitude_ratio`, `max_relative_se`: the
      session fit's guard, checked in that order (section 4.2; the third,
      amendment 1). The ratio is the middle 80% of the sizes', 90th
      percentile over 10th; the relative error is a parameter's standard
      error over its value.
    - `min_group_saccades`: the fewest saccades a block's or condition's gain,
      or a session's vigor, is taken over."""

    fit_form: str
    min_amplitude_deg: float
    max_duration_ms: float
    min_session_saccades: int
    min_amplitude_ratio: float
    max_relative_se: float
    min_group_saccades: int

    def __post_init__(self) -> None:
        if self.fit_form not in FIT_FORMS:
            raise ValueError(f"no main-sequence fit form {self.fit_form!r}; have {list(FIT_FORMS)}")


DEFAULT_MAIN_SEQUENCE_PARAMS = MainSequenceParams(
    fit_form="saturating_exponential", min_amplitude_deg=1.0, max_duration_ms=150.0,
    min_session_saccades=100, min_amplitude_ratio=3.0, max_relative_se=0.5, min_group_saccades=30,
)


@dataclass(frozen=True, slots=True)
class Curve:
    """`peak_velocity = v_max * (1 - exp(-amplitude / saturation))`, degrees
    and degrees per second."""

    v_max_deg_s: float
    saturation_deg: float

    def __call__(self, amplitude_deg):
        return self.v_max_deg_s * (1.0 - np.exp(-np.asarray(amplitude_deg, dtype=float) / self.saturation_deg))


@dataclass(frozen=True, slots=True)
class SessionFit:
    """One session's fit, or its refusal: `curve` is None and `reason` says
    why. The size range is stored either way, so a reader can judge a fit
    that passed and see what a refused one had."""

    curve: Curve | None
    n_saccades: int
    amplitude_min_deg: float | None
    amplitude_max_deg: float | None
    v_max_se_deg_s: float | None
    saturation_se_deg: float | None
    r_squared: float | None
    reason: str


@dataclass(frozen=True, slots=True)
class Gain:
    """A group's gain against its session's curve, or its refusal."""

    gain: float | None
    n_saccades: int
    amplitude_min_deg: float | None
    amplitude_max_deg: float | None
    reason: str


@dataclass(frozen=True, slots=True)
class Vigor:
    """A session's vigor against its earlier sessions, or why there is none."""

    value: float | None
    n_saccades: int
    n_history: int
    reason: str


def selected(labels, amplitude_deg, n_samples, fs_hz: float, params: MainSequenceParams) -> np.ndarray:
    """Which stored runs a fit takes (spec section 4.1): a `saccade` or
    `microsaccade` of at least `min_amplitude_deg`, lasting at most
    `max_duration_ms`. By size, not label: five of the seven detectors call
    every saccadic event `saccade`, whatever its size. A NULL amplitude, an
    unmeasured run, arrives as NaN and is never taken."""
    amplitude = np.asarray(amplitude_deg, dtype=float)
    duration_ms = 1000.0 * np.asarray(n_samples, dtype=float) / fs_hz
    with np.errstate(invalid="ignore"):
        return (np.isin(np.asarray(labels, dtype=object), _SACCADIC)
                & (amplitude >= params.min_amplitude_deg)
                & (duration_ms <= params.max_duration_ms))


def _range(amplitude: np.ndarray) -> tuple[float | None, float | None]:
    return (float(amplitude.min()), float(amplitude.max())) if amplitude.size else (None, None)


def fit_session(amplitude_deg, peak_velocity_deg_s, params: MainSequenceParams) -> SessionFit:
    """The saturating curve through one session's selected saccades, or a
    refusal, checked in this order (spec section 4.2):
    1. fewer than `min_session_saccades`;
    2. the middle 80% of their sizes spanning less than a factor of
       `min_amplitude_ratio`, so one stray large saccade cannot pass a session
       of small ones;
    3. peak speeds that do not vary, a fit that does not converge, or a V_max
       or C whose standard error is `max_relative_se` of its value or more
       (amendment 1). Measured, whole sessions on the reference recording
       come to 3-5%, and nine in ten 100-saccade samples of them under 28%;
       a relation with no saturation in range comes to 90%.

    Least squares on peak velocity, both parameters held positive, started
    from the fastest saccade and the median size."""
    from scipy.optimize import curve_fit

    amplitude = np.asarray(amplitude_deg, dtype=float)
    peak = np.asarray(peak_velocity_deg_s, dtype=float)
    n = int(amplitude.size)
    low, high = _range(amplitude)

    def refused(reason: str) -> SessionFit:
        return SessionFit(None, n, low, high, None, None, None, reason)

    if n < params.min_session_saccades:
        return refused(f"{n} saccades; a session fit needs at least {params.min_session_saccades}")
    p10, p90 = np.percentile(amplitude, [10, 90])
    if p90 < params.min_amplitude_ratio * p10:
        return refused(f"the middle 80% of their sizes spans {p10:.2f}-{p90:.2f} deg, a factor of "
                       f"{p90 / p10:.2f}; a session fit needs {params.min_amplitude_ratio:g}")

    def form(a, v_max, saturation):
        return v_max * (1.0 - np.exp(-a / saturation))

    total = float(np.sum((peak - peak.mean()) ** 2))
    if total == 0.0:
        return refused("their peak speeds do not vary")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            (v_max, saturation), covariance = curve_fit(
                form, amplitude, peak, p0=(float(peak.max()), float(np.median(amplitude))),
                bounds=([0.0, 0.0], [np.inf, np.inf]), maxfev=10_000)
    except (RuntimeError, ValueError) as exc:
        return refused(f"the fit did not converge: {exc}"[:255])
    v_max_se, saturation_se = np.sqrt(np.diag(covariance))
    if not (v_max_se < params.max_relative_se * v_max and saturation_se < params.max_relative_se * saturation):
        return refused(f"the fit did not determine V_max and C: V_max {v_max:.4g} +/- {v_max_se:.2g} deg/s, "
                       f"C {saturation:.4g} +/- {saturation_se:.2g} deg; a session fit needs each known to "
                       f"within {params.max_relative_se:.0%}")
    residual = peak - form(amplitude, v_max, saturation)
    r_squared = 1.0 - float(np.sum(residual**2)) / total
    return SessionFit(Curve(float(v_max), float(saturation)), n, low, high,
                      float(v_max_se), float(saturation_se), r_squared, "")


def gain(amplitude_deg, peak_velocity_deg_s, curve: Curve, params: MainSequenceParams) -> Gain:
    """The median of a group's peak speeds over what `curve`, its session's
    own fit, predicts for their sizes: 1.06 is 6% faster than the session's
    norm (spec section 4.3). Refused below `min_group_saccades`."""
    amplitude = np.asarray(amplitude_deg, dtype=float)
    peak = np.asarray(peak_velocity_deg_s, dtype=float)
    n = int(amplitude.size)
    low, high = _range(amplitude)
    if n < params.min_group_saccades:
        return Gain(None, n, low, high, f"{n} saccades; a gain needs at least {params.min_group_saccades}")
    return Gain(float(np.median(peak / curve(amplitude))), n, low, high, "")


def vigor(amplitude_deg, peak_velocity_deg_s, history: Sequence[SessionFit], params: MainSequenceParams,
          min_history: int = MIN_HISTORY_SESSIONS) -> Vigor:
    """One session's vigor against `history`, its earlier sessions' computed
    fits (spec section 5): each saccade's peak speed over the median of the
    curves whose own fitted size range covers its size, and the median of
    those ratios. A saccade no earlier curve covers is left out, so no curve is
    extrapolated."""
    amplitude = np.asarray(amplitude_deg, dtype=float)
    peak = np.asarray(peak_velocity_deg_s, dtype=float)
    fits = [fit for fit in history if fit.curve is not None]
    if len(fits) < min_history:
        return Vigor(None, 0, len(fits), f"no history yet ({len(fits)} earlier)")
    predicted = np.array([fit.curve(amplitude) for fit in fits])
    covers = np.array([(amplitude >= fit.amplitude_min_deg) & (amplitude <= fit.amplitude_max_deg)
                       for fit in fits])
    covered = covers.any(axis=0)
    if int(covered.sum()) < params.min_group_saccades:
        return Vigor(None, int(covered.sum()), len(fits), f"too few saccades ({int(covered.sum())})")
    reference = np.nanmedian(np.where(covers[:, covered], predicted[:, covered], np.nan), axis=0)
    return Vigor(float(np.median(peak[covered] / reference)), int(covered.sum()), len(fits), "")
