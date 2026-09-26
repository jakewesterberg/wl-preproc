"""REMoDNaV -- Dar, Wagner & Hanke's robust eye-movement classifier, and the
first registered detector that can emit `pursuit`.

Reimplemented from the paper and from `remodnav` 1.1.2's `clf.py` (MIT),
per design spec `2026-09-26-remodnav-design.md`. Where the two differ the
code is followed, because it produced the paper's validation numbers (spec
section 2), and every rule below cites the `clf.py` lines whose behaviour it
reproduces. None of its text is transcribed. Three exceptions, each stated
where it applies:
- durations round rather than truncate to samples (spec 2.1);
- `_runs_above` keeps a run that starts at sample 0, and closes a run still
  open at the window's end (spec 2.2);
- preprocessing is this repository's shared estimator and validity mask, not
  the oracle's own (spec 3).

Dar, A. H., Wagner, A. S., & Hanke, M. (2021). REMoDNaV: robust eye-movement
classification for dynamic stimulation. Behavior Research Methods, 53(1),
399-414. 10.3758/s13428-020-01428-x
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from wl_preproc.eye.detect.labels import Label, Run

#: statsmodels' `mad` normalisation, `scipy.stats.norm.ppf(0.75)` -- the
#: constant `clf.py` 311 divides by (spec 1.1). The exact float statsmodels
#: carries, so the arithmetic below is its arithmetic.
_MAD_C = 0.6744897501960817


@dataclass(frozen=True, slots=True)
class RemodnavParams:
    """The paper's Table 1 (spec section 6), durations in milliseconds --
    Nystrom-Holmqvist's convention, for its reason: a sample count is wrong
    at any other `fs_hz`. Two fields are the code's and not the paper's:
    `max_iterations` (`clf.py` 317) and `lowpass_order` (`clf.py` 680).

    Not fields, because something else already owns them (spec 6):
    - `px2deg`: gaze arrives in degrees;
    - `sampling_rate`: `fs_hz` is positional;
    - `min_blink_duration`, `dilate_nan` and `max_vel`: the validity mask;
    - `savgol_length` and `savgol_polyord`: the shared estimator.
    """

    noise_factor: float
    start_velocity_deg_s: float
    convergence_deg_s: float
    max_iterations: int
    min_saccade_duration_ms: float
    min_intersaccade_duration_ms: float
    max_pso_duration_ms: float
    min_fixation_duration_ms: float
    min_pursuit_duration_ms: float
    max_initial_saccade_freq_hz: float
    saccade_context_window_ms: float
    median_filter_ms: float
    lowpass_cutoff_hz: float
    lowpass_order: int
    pursuit_velocity_deg_s: float


DEFAULT_REMODNAV_PARAMS = RemodnavParams(
    noise_factor=5.0,
    start_velocity_deg_s=300.0,
    convergence_deg_s=1.0,
    max_iterations=30,  # clf.py 317 -- the paper states no cap
    min_saccade_duration_ms=10.0,
    min_intersaccade_duration_ms=40.0,
    max_pso_duration_ms=40.0,
    min_fixation_duration_ms=40.0,
    min_pursuit_duration_ms=40.0,
    max_initial_saccade_freq_hz=2.0,
    saccade_context_window_ms=1000.0,
    median_filter_ms=50.0,
    lowpass_cutoff_hz=4.0,
    lowpass_order=5,  # clf.py 680 -- the paper states no order
    pursuit_velocity_deg_s=2.0,
)


@dataclass(frozen=True, slots=True)
class Thresholds:
    """One window's peak and on/offset velocity thresholds (spec 1.1)."""

    peak: float
    onset: float


def _mad(values: np.ndarray) -> float:
    """The sigma-scaled median absolute deviation, computed the way
    statsmodels computes it (`clf.py` 311): each deviation divided by the
    constant, then the median."""
    median = np.median(values)
    return float(np.median(np.abs(values - median) / _MAD_C))


def _thresholds(speeds: np.ndarray, params: RemodnavParams) -> Thresholds | None:
    """`clf.py` 285-332 (spec 1.1).

    Iterates `PT = median + 2 * noise_factor * MAD` over the speeds below the
    previous `PT`, from `start_velocity_deg_s`. It stops when a step moves
    `PT` by no more than `convergence_deg_s`, or after `max_iterations`,
    keeping the last value. The on/offset threshold is
    `median + noise_factor * MAD` from the final pass.

    `None` when no speed lies below the current threshold. That happens on
    the first pass when nothing is under the start, or on a later one when
    the iteration has emptied the set. The oracle yields NaN there, which
    then detects nothing; this says so rather than inheriting it. A step
    that lands exactly on zero keeps the previous threshold, as `clf.py`
    320-324 does.
    """
    current = params.start_velocity_deg_s
    median = scale = 0.0
    for _ in range(params.max_iterations):
        below = speeds[speeds < current]
        if below.size == 0:
            return None
        median = float(np.median(below))
        scale = _mad(below)
        updated = median + 2 * params.noise_factor * scale
        if updated == 0:
            break
        converged = abs(current - updated) <= params.convergence_deg_s
        current = updated
        if converged:
            break
    return Thresholds(peak=current, onset=median + params.noise_factor * scale)


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One maximal run above a threshold. `weight` is what candidates are
    ranked by, largest first (`clf.py` 423-424, 706-707)."""

    start: int
    stop: int
    weight: float


def _runs_above(values: np.ndarray, threshold: float) -> list[_Candidate]:
    """`clf.py` 41-83 (`find_peaks`).

    A run opens at a sample strictly above `threshold` and closes at the
    next sample strictly below it. A sample exactly at the threshold, or
    missing (NaN), neither opens nor closes one. `stop` is the closing
    sample's index, and the weight sums the run's non-missing values
    including that closing sample, as `clf.py` 72-75 does.

    Spec 2.2's two fixes: a run still open at the end is closed at
    `values.size` -- the oracle closes it one sample short, and drops it
    entirely when it began at sample 0.
    """
    opens = np.flatnonzero(values > threshold)
    closes = np.flatnonzero(values < threshold)
    found: list[_Candidate] = []
    position = 0
    while position < opens.size:
        start = int(opens[position])
        after = int(np.searchsorted(closes, start))
        if after == closes.size:
            tail = values[start:]
            found.append(_Candidate(start, int(values.size), float(tail[~np.isnan(tail)].sum())))
            break
        stop = int(closes[after])
        closed = values[start:stop + 1]
        found.append(_Candidate(start, stop, float(closed[~np.isnan(closed)].sum())))
        position = int(np.searchsorted(opens, stop))
    return found


def _onset(speed: np.ndarray, start: int, threshold: float) -> int:
    """`clf.py` 86-98 (spec 1.3). Walks back while the speed is above
    `threshold` or not above the sample before it. Once below threshold it
    therefore stops at the first sample whose predecessor is lower -- on a
    monotone approach, the first sub-threshold sample, not the preceding
    minimum. Spec 1.3 records why the code's rule is followed."""
    index = start
    while index > 0 and (speed[index] > threshold or speed[index] <= speed[index - 1]):
        index -= 1
    return index


def _offset(speed: np.ndarray, start: int, threshold: float) -> int:
    """`clf.py` 101-112 (spec 1.3). Walks forward while the speed is above
    `threshold` or the next sample is lower: the first local minimum at or
    below threshold. A `start` at the array's end returns it unchanged."""
    index = start
    last = speed.size - 1
    while index < last and (speed[index] > threshold or speed[index] > speed[index + 1]):
        index += 1
    return index


@dataclass(frozen=True, slots=True)
class Signals:
    """What `classify` reads (spec section 3), one entry per sample; NaN marks
    a sample the algorithm treats as missing -- REMoDNaV's own representation
    of signal loss.

    - `x`, `y`: positions, degrees.
    - `speed`: the primary speed, deg/s. Thresholds, onsets, offsets and PSOs
      are all read from it.
    - `candidate_speed`: the speed of median-filtered positions, deg/s. It is
      used only to find the major saccades that chunk the recording
      (paper Table 1: "for initial data chunking only").
    """

    x: np.ndarray
    y: np.ndarray
    speed: np.ndarray
    candidate_speed: np.ndarray


@dataclass(frozen=True, slots=True)
class _Samples:
    """Every duration in samples at one rate, rounded (spec 2.1), plus the
    saccade cap per sample (`clf.py` 272)."""

    min_saccade: int
    min_intersaccade: int
    max_pso: int
    min_fixation: int
    min_pursuit: int
    context: int
    max_saccades_per_sample: float

    @classmethod
    def at(cls, params: RemodnavParams, fs_hz: float) -> _Samples:
        def count(ms: float) -> int:
            return int(round(ms * fs_hz / 1000.0))

        return cls(
            min_saccade=count(params.min_saccade_duration_ms),
            min_intersaccade=count(params.min_intersaccade_duration_ms),
            max_pso=count(params.max_pso_duration_ms),
            min_fixation=count(params.min_fixation_duration_ms),
            min_pursuit=count(params.min_pursuit_duration_ms),
            context=count(params.saccade_context_window_ms),
            max_saccades_per_sample=params.max_initial_saccade_freq_hz / fs_hz,
        )


def _context_window(run_start: int, run_stop: int, context: int, lo: int, hi: int) -> tuple[int, int]:
    """`clf.py` 433-438 (spec 1.2). Half the window before the run's start,
    the rest after it, clipped to `[lo, hi)`. The paper says "centered on the
    peak velocity"; the code anchors it on the run's start (spec 2)."""
    win_start = max(lo, run_start - int(context / 2))
    return win_start, min(hi, run_stop + context - (run_start - win_start))


def _amplitude(signals: Signals, start: int, stop: int) -> float:
    """`clf.py` 274-283: the distance between the first and last samples in
    `[start, stop)` whose primary speed is present, in its own arithmetic.
    NaN when there is none, which fails every comparison -- as there."""
    present = np.flatnonzero(~np.isnan(signals.speed[start:stop]))
    if present.size == 0:
        return float("nan")
    first, last = start + int(present[0]), start + int(present[-1])
    return float(((signals.x[first] - signals.x[last]) ** 2
                  + (signals.y[first] - signals.y[last]) ** 2) ** 0.5)


def _pso(signals: Signals, sac_start: int, sac_stop: int, local: Thresholds,
         samples: _Samples) -> Run | None:
    """`clf.py` 115-138 and 486-506 (spec 1.4). Only the first
    `max_pso` samples after the saccade are looked at.

    - **Kind:** high-velocity if a run there exceeds the peak threshold,
      otherwise low-velocity if one exceeds the on/offset threshold. Both
      are `pso`.
    - **End:** the on/offset search from the last run's end, within the
      window.
    - **Dropped** if any speed before that end is missing, or if its
      amplitude is not below its saccade's. (`clf.py` 134's
      `pso_end > len(velocities)` can never hold and has no counterpart.)
    """
    window = signals.speed[sac_stop:sac_stop + samples.max_pso]
    peaks = _runs_above(window, local.peak) or _runs_above(window, local.onset)
    if not peaks:
        return None
    end = _offset(window, peaks[-1].stop, local.onset)
    if np.isnan(window[:end]).any():
        return None
    if not _amplitude(signals, sac_stop, sac_stop + end) < _amplitude(signals, sac_start, sac_stop):
        return None
    return Run(start=sac_stop, stop=sac_stop + end, label=Label.PSO)


def _saccades(signals: Signals, start: int, end: int, candidates: list[_Candidate] | None,
              context: int | None, samples: _Samples, params: RemodnavParams) -> list[Run]:
    """`clf.py` 389-512: saccades, each followed by its PSO if it has one, in
    the order the oracle yields them.

    **Two modes.**
    - **Major pass** (spec 1.2): `candidates` and a `context` length are
      given. Each candidate's thresholds come from its context window on the
      primary speed.
    - **Piece search** (spec 1.5): `candidates` and `context` are `None`.
      Thresholds come from the whole piece `[start, end)`, and candidates
      are its runs of primary speed above the peak threshold.

    Onset and offset searches run over the whole array, not the piece, as
    the oracle's do.

    A candidate is rejected if it is shorter than the minimum saccade, has a
    missing position, or lies within `min_intersaccade` of anything already
    accepted in this call. The `claimed` mask is fresh per call (`clf.py`
    419); `np.zeros` is lazily zeroed memory, so it costs only the pages
    touched. The major pass stops once the saccades accepted exceed the
    frequency cap times the recording's length (`clf.py` 508-512).
    """
    n = signals.speed.size
    speed = signals.speed
    local: Thresholds | None = None
    if context is None:
        local = _thresholds(speed[start:end], params)
        if local is None:
            return []
        candidates = [
            _Candidate(c.start + start, c.stop + start, c.weight)
            for c in _runs_above(speed[start:end], local.peak)
        ]
    claimed = np.zeros(n, dtype=bool)
    found: list[Run] = []
    accepted = 0
    for candidate in sorted(candidates, key=lambda c: -c.weight):
        if context is not None:
            win_start, win_end = _context_window(candidate.start, candidate.stop, context, start, end)
            local = _thresholds(speed[win_start:win_end], params)
            if local is None:
                continue
        sac_start = _onset(speed, candidate.start, local.onset)
        sac_stop = _offset(speed, candidate.stop, local.onset)
        if sac_stop - sac_start < samples.min_saccade:
            continue
        if np.isnan(signals.x[sac_start:sac_stop]).any():
            continue
        near = claimed[max(0, sac_start - samples.min_intersaccade):
                       min(n, sac_stop + samples.min_intersaccade)]
        if near.any():
            continue
        found.append(Run(start=sac_start, stop=sac_stop, label=Label.SACCADE))
        accepted += 1
        claimed[sac_start:sac_stop] = True
        pso = _pso(signals, sac_start, sac_stop, local, samples)
        if pso is not None:
            found.append(pso)
            claimed[pso.start:pso.stop] = True
        if samples.max_saccades_per_sample and accepted / n > samples.max_saccades_per_sample:
            break
    return found
