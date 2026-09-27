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

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import butter, filtfilt

from wl_preproc.eye.detect.labels import Label, Run, true_runs
from wl_preproc.eye.detect.velocity import _HALF_WINDOW, velocity

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


#: `(positions (n, 2), fs_hz) -> speed`. Index k is the speed attributed to
#: sample k; the result may have n entries (the shared estimator) or n - 1
#: (the oracle's two-point difference, `clf.py` 784-790).
Differentiate = Callable[[np.ndarray, float], np.ndarray]


def shared_speed(positions: np.ndarray, fs_hz: float) -> np.ndarray:
    """The shared estimator (`velocity.py`) as a speed -- the differentiator
    production hands `classify` (spec 3)."""
    v = velocity(positions, fs_hz)
    return np.hypot(v[:, 0], v[:, 1])


def _periods(events: list[Run], start: int, end: int) -> list[tuple[int, int]]:
    """`clf.py` 514-579 (spec 1.5). The periods are:
    - from `start` to the first saccade;
    - from each saccade's end (or its PSO's, if it has one) to the next
      saccade's start;
    - from the last to `end`.

    A zero-length period is skipped. None follows a saccade that ends exactly
    at `end`."""
    windows: list[tuple[int, int]] = []
    previous_saccade: Run | None = None
    previous_pso: Run | None = None
    for event in sorted(events, key=lambda run: run.start):
        if previous_saccade is None:
            if event.label is not Label.SACCADE:
                continue
        elif previous_pso is None and event.label is Label.PSO:
            previous_pso = event
            continue
        elif event.label is not Label.SACCADE:
            continue
        window_start = start if previous_saccade is None else (previous_pso or previous_saccade).stop
        if window_start != event.start:
            windows.append((window_start, event.start))
        previous_saccade, previous_pso = event, None
    if previous_saccade is not None and previous_saccade.stop == end:
        return windows
    tail = start if previous_saccade is None else (previous_pso or previous_saccade).stop
    windows.append((tail, end))
    return windows


def _pieces(x: np.ndarray, start: int, end: int) -> list[tuple[int, int]]:
    """`clf.py` 581-605: the maximal runs of present positions within
    `[start, end)`. No event ever spans a gap."""
    if end <= start:
        return []
    return [(start + int(a), start + int(b)) for a, b in true_runs(~np.isnan(x[start:end]))]


def _piece_saccades(signals: Signals, start: int, end: int, samples: _Samples,
                    params: RemodnavParams) -> list[Run]:
    """`clf.py` 624-654 (spec 1.5).

    A piece no longer than `2 * min_intersaccade + min_saccade + max_pso` is
    not searched. Otherwise every saccade or PSO within `min_intersaccade` of
    either edge is dropped, and a dropped saccade takes the PSO right behind
    it along."""
    if end - start <= 2 * samples.min_intersaccade + samples.min_saccade + samples.max_pso:
        return []
    kept: list[Run] = []
    kill_pso = False
    for event in _saccades(signals, start, end, None, None, samples, params):
        if kill_pso:
            kill_pso = False
            if event.label is Label.PSO:
                continue
        if event.start - start < samples.min_intersaccade or end - event.stop < samples.min_intersaccade:
            kill_pso = True
            continue
        kept.append(event)
    return kept


def _stretches(marked: np.ndarray, length: int, samples: _Samples) -> list[tuple[bool, int, int]]:
    """`clf.py` 728-764 (spec 1.6): pursuit-or-fixation stretches covering
    `[0, length)`, as `(pursuit, first, stop)`.

    1. Stretches are built with an inclusive last index.
    2. Any whose `last - first` is below its type's minimum is dropped.
    3. Neighbours of one type merge.
    4. Where the type changes, the boundary sits at `last + int(gap / 2)`.
    5. The first starts at 0 and the last ends at `length`.
    6. Nothing left means one fixation.
    """
    if marked.size == 0:
        return [(False, 0, length)]
    change = np.flatnonzero(np.diff(marked.astype(np.int8))) + 1
    firsts = np.concatenate(([0], change))
    lasts = np.concatenate((change - 1, [marked.size - 1]))
    stretches = [
        [bool(marked[first]), int(first), int(last)]
        for first, last in zip(firsts, lasts, strict=True)
        if last - first >= (samples.min_pursuit if marked[first] else samples.min_fixation)
    ]
    merged: list[list] = []
    for index, stretch in enumerate(stretches):
        if index == len(stretches) - 1:
            merged.append(stretch)
            break
        following = stretches[index + 1]
        if stretch[0] == following[0]:
            following[1] = stretch[1]
            continue
        boundary = stretch[2] + int((following[1] - stretch[2]) / 2)
        stretch[2] = boundary
        following[1] = boundary
        merged.append(stretch)
    if not merged:
        return [(False, 0, length)]
    merged[0][1] = 0
    merged[-1][2] = length
    return [(pursuit, first, stop) for pursuit, first, stop in merged]


def _fixation_or_pursuit(signals: Signals, start: int, end: int, fs_hz: float, samples: _Samples,
                         params: RemodnavParams, differentiate: Differentiate) -> list[Run]:
    """`clf.py` 672-782 (spec 1.6).

    A piece shorter than `min_fixation` emits nothing; storage paints it
    `fixation` anyway (spec 4). Otherwise:
    1. Positions are low-passed zero-phase with a Butterworth filter and
       Gustafsson initial conditions, then differentiated.
    2. Runs above the pursuit threshold, largest first, get the on/offset
       searches with that threshold.
    3. Those long enough are marked.
    4. `_stretches` tidies the result.
    """
    length = end - start
    if length < samples.min_fixation:
        return []
    b, a = butter(params.lowpass_order, params.lowpass_cutoff_hz / (0.5 * fs_hz), btype="low", analog=False)
    positions = np.column_stack((
        filtfilt(b, a, signals.x[start:end], method="gust"),
        filtfilt(b, a, signals.y[start:end], method="gust"),
    ))
    speed = differentiate(positions, fs_hz)
    threshold = params.pursuit_velocity_deg_s
    marked = np.zeros(speed.size, dtype=bool)
    for candidate in sorted(_runs_above(speed, threshold), key=lambda c: -c.weight):
        pursuit_start = _onset(speed, candidate.start, threshold)
        pursuit_stop = _offset(speed, candidate.stop, threshold)
        if pursuit_stop - pursuit_start < samples.min_pursuit:
            continue
        marked[pursuit_start:pursuit_stop] = True
    return [
        Run(start=start + first, stop=start + stop, label=Label.PURSUIT if pursuit else Label.FIXATION)
        for pursuit, first, stop in _stretches(marked, length, samples)
    ]


def classify(signals: Signals, fs_hz: float, params: RemodnavParams,
             differentiate: Differentiate) -> list[Run]:
    """REMoDNaV over one eye's `signals`: runs sorted by start, pairwise
    disjoint. Samples no run covers are ones the algorithm left unlabelled.

    1. **The major pass** (spec 1.2): candidates are runs of candidate speed
       above one recording-wide threshold, each with its own context
       thresholds.
    2. **Each period between major saccades**, split into pieces at missing
       positions (spec 1.5):
       - a piece with saccades of its own is re-divided at them, and the
         parts are processed the same way;
       - a piece without is fixation or pursuit (spec 1.6).

    The oracle recurses; a work list gives the same result without Python's
    recursion limit, because each piece is classified from its own samples
    alone.
    """
    n = signals.speed.size
    samples = _Samples.at(params, fs_hz)
    events: list[Run] = []
    overall = _thresholds(signals.candidate_speed, params)
    if overall is not None:
        events.extend(_saccades(signals, 0, n, _runs_above(signals.candidate_speed, overall.peak),
                                samples.context, samples, params))
    work = _periods(events, 0, n)
    while work:
        period_start, period_end = work.pop()
        for piece_start, piece_end in _pieces(signals.x, period_start, period_end):
            found = _piece_saccades(signals, piece_start, piece_end, samples, params)
            if found:
                events.extend(found)
                work.extend(_periods(found, piece_start, piece_end))
            else:
                events.extend(_fixation_or_pursuit(signals, piece_start, piece_end, fs_hz,
                                                   samples, params, differentiate))
    return sorted(events, key=lambda run: run.start)


def _odd_samples(ms: float, fs_hz: float) -> int:
    """A duration in samples, plus one if even, so a filter of that width is
    centred (spec 3 item 2): 25 at 500 Hz, 51 at 1000 Hz."""
    count = max(int(round(ms * fs_hz / 1000.0)), 1)
    return count if count % 2 else count + 1


def _dilate(mask: np.ndarray, reach: int) -> np.ndarray:
    """True wherever a True in `mask` lies within `reach` samples. Uses
    cumulative sums, so it holds at any length -- `np.convolve(mode="same")`
    returns the kernel's length when the kernel is the longer of the two."""
    if reach <= 0 or not mask.any():
        return mask.copy()
    counts = np.concatenate(([0], np.cumsum(mask.astype(np.int64))))
    index = np.arange(mask.size)
    lo = np.clip(index - reach, 0, mask.size)
    hi = np.clip(index + reach + 1, 0, mask.size)
    return (counts[hi] - counts[lo]) > 0


def _candidate_speed(gaze_deg: np.ndarray, usable: np.ndarray, fs_hz: float,
                     params: RemodnavParams) -> np.ndarray:
    """Spec 3 item 2: the shared estimator's speed of 50 ms median-filtered
    positions -- the oracle's `med_vel` (`clf.py` 869-883), with the shared
    differentiator in place of its two-point difference.

    Missing wherever the filter's window, or the estimator's +-2 reach,
    touches an unusable sample. That replaces the oracle's median filter over
    NaN positions.

    Unusable positions are zeroed before filtering, since every speed they
    could reach is masked anyway. A NaN would not stay inside that reach:
    scipy's 1-D median filter keeps a running median, and a NaN in it
    changes samples well past its own window. The measurements are in
    `test_what_the_mask_withholds_never_reaches_the_candidate_speed_even_as_nan`."""
    width = _odd_samples(params.median_filter_ms, fs_hz)
    withheld = np.where(usable[:, None], np.asarray(gaze_deg, dtype=float), 0.0)
    filtered = np.column_stack([median_filter(withheld[:, axis], size=width) for axis in (0, 1)])
    speed = shared_speed(filtered, fs_hz)
    return np.where(_dilate(~usable, width // 2 + _HALF_WINDOW), np.nan, speed)


def detect_remodnav(
    gaze_deg: np.ndarray,
    velocity_deg_s: np.ndarray,
    available: np.ndarray,
    fs_hz: float,
    params: RemodnavParams,
) -> list[Run]:
    """REMoDNaV, as the registered `DetectFn`: labelled half-open intervals.

    **The validity mask is the only noise definition** (spec 3 item 4). A
    sample the mask withholds (`entry is not None`) reaches `classify` as
    missing -- position, primary speed and candidate speed all NaN. The
    method's own handling of lost data then does the rest.

    The primary speed is the shared estimator's; the candidate speed is its
    speed of median-filtered positions. Both follow spec 3. Its saccadic
    slice is `{saccade}`, so conjunction runs take `_conjunction_label`'s
    degenerate branch, as Nystrom-Holmqvist's do (spec 4).
    """
    usable = np.array([entry is None for entry in available], dtype=bool)
    if not usable.any():
        return []
    signals = Signals(
        x=np.where(usable, gaze_deg[:, 0], np.nan),
        y=np.where(usable, gaze_deg[:, 1], np.nan),
        speed=np.where(usable, np.hypot(velocity_deg_s[:, 0], velocity_deg_s[:, 1]), np.nan),
        candidate_speed=_candidate_speed(gaze_deg, usable, fs_hz, params),
    )
    return classify(signals, fs_hz, params, shared_speed)
