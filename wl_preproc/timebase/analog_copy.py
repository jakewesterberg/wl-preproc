"""Lining the eye tracker's trace up with its analog copy on an NI channel
(design spec `2026-10-18-training-records-and-backup-clock-design.md`,
amendments 77-84).

Under the NI's clock, an eye file with no usable barcodes still has a witness
on the NI: the eye tracker writes its own values through a DAC onto the NI's
analog inputs. A window of the eye file's trace, laid against the channel,
lines up at one shift; that shift, at the window's centre, is a pair
`(ni_s, device_s)`, and pairs fit the eye's clock to the NI's as barcodes do
(`fit.py::fit_pairs_across`).

**What is compared.** Correlation, normalised over each window (Pearson), so
the DAC's scale and offset drop out. The eye's trace is interpolated onto the
channel's grid, `fs_hz`, before it is compared. The copy is not the trace: it
is the tracker's output, which amendment 78a does not know, so the caller tries
several candidate columns and `find_channel` takes the best.

**The pairs, in two stages,** as `strobes.py` finds its own:

* **The anchor.** Windows are tried in turn against the whole channel, by FFT
  cross-correlation, until one is distinct. The eye may run before the NI
  starts (amendment 44), and a window the NI never saw cannot anchor.
* **The walk.** Each later window is searched only within
  `RUNNER_UP_EXCLUSION_S` of the line through the pairs found so far.

**Distinct, or no pair.** A window gives a pair only when its best shift
correlates at least `MIN_CORRELATION` and beats the best rival shift (another
local maximum) more than `RUNNER_UP_EXCLUSION_S` from it by `MIN_PEAK_MARGIN`
(`_distinct` says why a rival, not any shift). A flat channel correlates 0; a
periodic stretch, or a fixation whose only movement is noise the copy does not
share, has rivals as good as its peak. Each gives no pair rather than a guess.
A periodic trace never anchors at all, so it is `NoCopy`, never a match a
period off, whose residuals would be as small as a right one's. A window whose
channel sits at its rail for more than a tenth of it (a clipped copy) gives no
pair either: its clipped edges would bend the peak.

**The drop and refit** (78). The copy comes through a USB DAC driven by
software, whose delay jumps in a stall (amendment 84: 2% of frames 10 ms or
more late, up to 50 ms). A stalled window lines up off the line, inside the
search. The pair furthest from the fitted line is dropped and the line
refitted until every pair is within `RESIDUAL_BOUND_US`, up to
`MAX_DROPPED_FRACTION` of the pairs; beyond that the copy is refused. The
dropped stretches are named, since the eye's time there is the line's.

**Time.** The eye's is its own device seconds, from frame numbers, so a dropped
frame leaves a gap rather than shifting every later sample. The channel's
sample `k` is at `k / fs_hz` NI seconds; what that instant means against the
NI's native samples is the caller's (Task 3's decimation).

**Memory.** The anchor's FFT and its normalisation span the channel given
(one decimated channel, float64, about 1.4 GB at the peak for 12 h at 1 kHz),
and `match_copy` releases them when the anchor is found. The walk holds a
window's worth of arrays at a time. `find_channel` keeps every channel's
spectrum for its prefix (about 10 MB a channel for 600 s), since every
candidate searches them all.

Pure numeric: no DataJoint, no file I/O, no `wl_preproc.synth`, as `fit.py`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import scipy.fft

from wl_preproc.timebase.fit import fit_pairs_across

# Every constant below was set on synthetic data (Task 1's generator, and this
# module's tests) and is revisited on real traces with their copy
# (`docs/hardware-checks.md` item 17).

#: The rate both sides are compared at, after the channel's decimation: a
#: millisecond, so each 2 ms eye frame spans two samples, and the parabola
#: refines below one. Set on synthetic data; revisited on real traces
#: (hardware check 17).
DECIMATED_RATE_HZ = 1000.0
#: Each window's length: about 30 fixations and their saccades, enough to be
#: distinct; short enough that the line so far predicts it within a sample.
#: Set on synthetic data; revisited on real traces (hardware check 17).
WINDOW_S = 10.0
#: From one window's start to the next's: windows abut, so each sample is
#: compared once. Set on synthetic data; revisited on real traces (hardware check 17).
STEP_S = 10.0
#: `find_channel` looks at the eye's first this many seconds: enough windows to
#: tell a channel by, without reading a 12 h file whole for each. Set on
#: synthetic data; revisited on real traces (hardware check 17).
SEARCH_PREFIX_S = 600.0
#: A window's best shift must correlate at least this well. The synthetic copy,
#: a mix of two columns through `tanh`, correlates about 0.93-0.95 with the
#: best single one. Set on synthetic data; revisited on real traces (hardware check 17).
MIN_CORRELATION = 0.9
#: ... and beat the best rival (a local maximum) outside `RUNNER_UP_EXCLUSION_S`
#: of it by this. Set on synthetic data; revisited on real traces (hardware check 17).
MIN_PEAK_MARGIN = 0.1
#: The half-width of a peak (a saccade's duration, about), beyond which a local
#: maximum is a rival; and the half-width of each later window's search about
#: the line so far. Set on synthetic data; revisited on real traces (hardware check 17).
RUNNER_UP_EXCLUSION_S = 0.05
#: Fewer distinct windows than this is no copy (absent); fewer pairs than this
#: left after the drop is a refusal. Set on synthetic data; revisited on real
#: traces (hardware check 17).
MIN_PAIRS = 20
#: A pair further than this from the fitted line is dropped: a quarter of a
#: 2 ms frame. Set on synthetic data; revisited on real traces (hardware check 17).
RESIDUAL_BOUND_US = 500.0
#: At most this fraction of the pairs may be dropped; more, and the copy is
#: refused. Set on synthetic data; revisited on real traces (hardware check 17).
MAX_DROPPED_FRACTION = 0.1
#: A measured lag must be at most this (Task 4's check, amendment 79): far
#: above the copy's 4-5 ms of output latency, far below the spacing of
#: saccades. Set on synthetic data; revisited on real traces (hardware check 17).
LAG_CEILING_S = 0.03

# A window more than this fraction missing (NaN, or in a gap of dropped frames)
# gives no pair: its correlation would rest on interpolation.
_MAX_MISSING_FRACTION = 0.1
# Eye samples further apart than this many frame periods (the window's median
# spacing) leave the grid points between them missing.
_MAX_GAP_FRAMES = 1.5
# A channel stretch whose spread is below this fraction of the channel's whole
# spread is flat: a stuck DAC, or the rail. Its correlation is 0, not a ratio
# of two rounding errors. A thousand times above float64 rounding of the
# chunked sums below, and below any real signal (the NI's own 16-bit step).
_FLAT_SPREAD_FRACTION = 1e-4
# A trace window whose spread is below this fraction of its largest value is
# constant (an untracked eye's zeros): skipped, never divided by.
_CONSTANT_SPREAD_FRACTION = 1e-12
# The channel's moving spread is computed over chunks of this many shifts, each
# from its own centred cumulative sums, so rounding stays local to a chunk
# rather than growing over a 12 h channel.
_SPREAD_CHUNK = 1 << 16
# Each later window is correlated over this many exclusion widths either side
# of the line, so that a peak anywhere inside the search still has rivals a
# full exclusion width beyond it on both sides.
_LOCAL_SPAN_WIDTHS = 3


class NoCopy(Exception):
    """No copy: no channel, or no candidate, gives `MIN_PAIRS` distinct windows.
    The eye file falls back to its barcodes (amendment 77's 'absent')."""


class CopyRefused(Exception):
    """A copy was found but does not fit: too few pairs, or too many dropped
    for the residual to be met. The message says which (amendment 77's 'failed')."""


@dataclass(frozen=True)
class CopyMatch:
    """The pairs that place the eye file on the NI's clock.

    `pairs` are `(ni_s, device_s)`, one per kept window at its centre, in
    device order, every one within `RESIDUAL_BOUND_US` of their line.
    `channel` and `signal` are the caller's to fill (`dataclasses.replace`):
    `match_copy` is given one channel and one trace and knows neither's name,
    so it returns -1 and ''. `n_windows` counts the windows tried, `n_dropped`
    the pairs the refit dropped, and `dropped_stretches` the device-time extents
    of those pairs' windows, adjacent ones merged.
    """

    pairs: tuple[tuple[float, float], ...]
    channel: int
    signal: str
    n_windows: int
    n_dropped: int
    dropped_stretches: tuple[tuple[float, float], ...]


def find_channel(
    eye_time_s: np.ndarray, candidates: dict[str, np.ndarray], channels: list[np.ndarray], fs_hz: float
) -> tuple[int, str]:
    """The `(channel, signal)` that carries the copy: the most distinct windows
    over the eye's first `SEARCH_PREFIX_S`, against the channels' prefixes as
    given. A tie (the two eyes' X both follow the gaze) goes to the higher
    summed peak correlation. A constant candidate (an untracked eye) gives no
    window, and so no score.

    Raises `NoCopy` when the best gives fewer than `MIN_PAIRS`.
    """
    eye_time_s = np.asarray(eye_time_s, dtype=np.float64)
    if not channels or not candidates:
        raise NoCopy(f"{len(channels)} analog channels and {len(candidates)} candidate traces: nothing to match")
    starts = _window_starts(eye_time_s, limit_s=SEARCH_PREFIX_S)
    references = [_Reference(channel, fs_hz) for channel in channels]
    best: tuple[int, float] = (-1, -math.inf)
    choice: tuple[int, str] | None = None
    for signal, trace in candidates.items():
        _check_lengths(eye_time_s, trace)
        walks = [_Walk(reference, release_on_anchor=False) for reference in references]
        for start in starts:
            # At a rate of 1: the windows are shared by every channel's walk, and
            # only counted here, not timed.
            window = _window(eye_time_s, trace, start, fs_hz, 1.0)
            if window is None:
                continue
            for walk in walks:
                walk.offer(window)
        for index, walk in enumerate(walks):
            score = (len(walk.found), sum(hit.correlation for hit in walk.found))
            if score > best:
                best, choice = score, (index, signal)
    if choice is None or best[0] < MIN_PAIRS:
        named = "none" if choice is None else f"channel {choice[0]}'s {choice[1]!r}"
        raise NoCopy(
            f"the best match ({named}) gave {max(best[0], 0)} distinct windows of {len(starts)} "
            f"in the first {SEARCH_PREFIX_S:g} s, and a copy needs {MIN_PAIRS}"
        )
    return choice


def match_copy(eye_time_s: np.ndarray, trace: np.ndarray, channel: np.ndarray, fs_hz: float) -> CopyMatch:
    """Every window of `trace` (on `eye_time_s`, the eye's own seconds; NaN
    is missing) placed on `channel` (decimated to `fs_hz`), dropped to its line.

    Raises `NoCopy` below `MIN_PAIRS` distinct windows, and `CopyRefused` when
    the drop and refit cannot meet `RESIDUAL_BOUND_US` within
    `MAX_DROPPED_FRACTION`, or leaves fewer than `MIN_PAIRS`.
    """
    eye_time_s = np.asarray(eye_time_s, dtype=np.float64)
    _check_lengths(eye_time_s, trace)
    starts = _window_starts(eye_time_s)
    walk = _Walk(_Reference(channel, fs_hz), release_on_anchor=True)
    for start in starts:
        window = _window(eye_time_s, trace, start, fs_hz, walk.scale)
        if window is not None:
            walk.offer(window)
    if len(walk.found) < MIN_PAIRS:
        raise NoCopy(
            f"{len(walk.found)} of {len(starts)} windows matched the channel distinctly, "
            f"and a copy needs {MIN_PAIRS}"
        )
    kept, dropped = _drop_and_refit(walk.found)
    return CopyMatch(
        pairs=tuple((hit.ni_s, hit.device_s) for hit in kept),
        channel=-1,
        signal="",
        n_windows=len(starts),
        n_dropped=len(dropped),
        dropped_stretches=_stretches(dropped),
    )


@dataclass(frozen=True)
class _Hit:
    ni_s: float
    device_s: float  # the window's centre
    start_s: float  # the window's start, device seconds
    correlation: float


class _Window:
    """One window of the eye's trace on the channel's grid, centred and scaled so
    that its dot product with a channel stretch, over that stretch's spread, is
    their Pearson correlation."""

    def __init__(self, start_s: float, unit: np.ndarray, device_step_s: float) -> None:
        self.start_s = start_s
        self.unit = unit
        self.centre_s = start_s + (unit.size - 1) / 2 * device_step_s
        self._spectrum: tuple[int, np.ndarray] | None = None

    def spectrum(self, length: int) -> np.ndarray:
        """`conj(rfft(unit, length))`, kept for the next channel of the same length."""
        if self._spectrum is None or self._spectrum[0] != length:
            self._spectrum = (length, np.conj(scipy.fft.rfft(self.unit, length, workers=-1)))
        return self._spectrum[1]


class _Reference:
    """One channel, with what its anchor search needs, made once and released
    when nothing needs it any more."""

    def __init__(self, channel: np.ndarray, fs_hz: float) -> None:
        self.fs_hz = fs_hz
        self.n = _window_samples(fs_hz)
        self.exclusion = round(RUNNER_UP_EXCLUSION_S * fs_hz)
        values = np.asarray(channel, dtype=np.float64)
        self.values = values - (float(np.mean(values)) if values.size else 0.0)
        spread = math.sqrt(float(np.dot(self.values, self.values)) / values.size) if values.size else 0.0
        # Variances at or below this are flat (`_FLAT_SPREAD_FRACTION`).
        self.flat_variance = (_FLAT_SPREAD_FRACTION * spread) ** 2
        # The channel's extremes: where a copy is clipped, the rails it holds.
        self.rails = (float(np.min(self.values)), float(np.max(self.values))) if values.size else (0.0, 0.0)
        self.n_shifts = max(values.size - self.n + 1, 0)
        self._spectrum: np.ndarray | None = None
        self._spread: np.ndarray | None = None

    @property
    def fft_length(self) -> int:
        return scipy.fft.next_fast_len(self.values.size, real=True)

    def correlations(self, window: _Window) -> np.ndarray:
        """The window's correlation at every shift that lies wholly inside the
        channel, by FFT; 0 where the channel is flat."""
        length = self.fft_length
        if self._spectrum is None:
            self._spectrum = scipy.fft.rfft(self.values, length, workers=-1)
            self._spread = _moving_spread(self.values, self.n, self.flat_variance)
        product = self._spectrum * window.spectrum(length)
        dot = scipy.fft.irfft(product, length, workers=-1)[: self.n_shifts]
        return _divide(dot, self._spread)

    def local_correlations(self, window: _Window, lo: int, hi: int) -> np.ndarray:
        """The window's correlation at shifts `lo..hi` inclusive, directly."""
        stretch = self.values[lo : hi + self.n]
        dot = np.correlate(stretch, window.unit, mode="valid")
        return _divide(dot, _moving_spread(stretch, self.n, self.flat_variance))

    def release(self) -> None:
        """Drop the anchor's whole-channel arrays; made again if asked for."""
        self._spectrum = None
        self._spread = None

    def clipped(self, shift: int) -> bool:
        """Whether more than `_MAX_MISSING_FRACTION` of the channel's stretch at
        `shift` sits at one of its extremes: the copy clipped at the NI's
        range, which is missing, as NaN is in the trace. Its remainder still
        correlates, but the clip bends the edges it keeps, and a peak from
        them is off by a fraction of a millisecond."""
        stretch = self.values[shift : shift + self.n]
        at_rail = np.count_nonzero((stretch == self.rails[0]) | (stretch == self.rails[1]))
        return at_rail > _MAX_MISSING_FRACTION * self.n


class _Walk:
    """One trace's search along one channel: the anchor, then the walk."""

    def __init__(self, reference: _Reference, *, release_on_anchor: bool) -> None:
        self.reference = reference
        # `match_copy`'s channel is whole and walked once, so its anchor's arrays
        # go as soon as it is found; `find_channel`'s are prefixes, shared by
        # every candidate's walk.
        self._release_on_anchor = release_on_anchor
        self.found: list[_Hit] = []
        # The line so far: ni_s = ni_mean + (device_s - device_mean) / scale.
        self._line = (0.0, 0.0, 1.0)

    def offer(self, window: _Window) -> None:
        if self.reference.n_shifts < 3:
            return
        hit = self._anchor(window) if not self.found else self._next(window)
        if hit is None:
            return
        if not self.found and self._release_on_anchor:
            self.reference.release()
        self.found.append(hit)
        self._refit()

    def _anchor(self, window: _Window) -> _Hit | None:
        ref = self.reference
        correlation = ref.correlations(window)
        peak = int(np.argmax(correlation))
        if not _distinct(correlation, peak, ref.exclusion):
            return None
        if peak < 1 or peak > ref.n_shifts - 2:
            return None
        # Refined from the direct correlation, not the FFT's: the same numbers
        # the walk refines from.
        return self._hit(window, ref.local_correlations(window, peak - 1, peak + 1), peak - 1, 1)

    def _next(self, window: _Window) -> _Hit | None:
        ref = self.reference
        ni_mean, device_mean, scale = self._line
        predicted_ni_s = ni_mean + (window.centre_s - device_mean) / scale
        centre = round(predicted_ni_s * ref.fs_hz - (ref.n - 1) / 2)
        span = _LOCAL_SPAN_WIDTHS * ref.exclusion
        lo, hi = max(centre - span, 0), min(centre + span, ref.n_shifts - 1)
        # The peak is looked for within the exclusion of the line, and away from
        # the ends, so that the parabola has both neighbours.
        first, last = max(centre - ref.exclusion, lo + 1), min(centre + ref.exclusion, hi - 1)
        if first > last:
            return None
        correlation = ref.local_correlations(window, lo, hi)
        peak = first - lo + int(np.argmax(correlation[first - lo : last - lo + 1]))
        if not _distinct(correlation, peak, ref.exclusion):
            return None
        return self._hit(window, correlation, lo, peak)

    def _hit(self, window: _Window, correlation: np.ndarray, lo: int, peak: int) -> _Hit | None:
        """The pair at the window's centre, the peak refined below a sample by
        the parabola through it and its two neighbours; None when the channel
        there is clipped."""
        if self.reference.clipped(lo + peak):
            return None
        before, at, after = (float(v) for v in correlation[peak - 1 : peak + 2])
        curvature = before - 2.0 * at + after
        step = 0.0 if curvature >= 0.0 else min(max(0.5 * (before - after) / curvature, -0.5), 0.5)
        shift = lo + peak + step
        ref = self.reference
        ni_s = (shift + (ref.n - 1) / 2) / ref.fs_hz
        return _Hit(ni_s=ni_s, device_s=window.centre_s, start_s=window.start_s, correlation=at)

    def _refit(self) -> None:
        pairs = [(hit.ni_s, hit.device_s) for hit in self.found]
        ni_mean = sum(ni for ni, _ in pairs) / len(pairs)
        device_mean = sum(device for _, device in pairs) / len(pairs)
        # Until two pairs exist, a rate of 1: both sides are seconds.
        scale = 1.0 if len(pairs) < 2 else fit_pairs_across([pairs], 1.0).scale
        self._line = (ni_mean, device_mean, scale)

    @property
    def scale(self) -> float:
        """Device seconds per NI second, on the line so far."""
        return self._line[2]


def _distinct(correlation: np.ndarray, peak: int, exclusion: int) -> bool:
    """Whether the peak correlates at least `MIN_CORRELATION` and beats the best
    rival shift, more than `exclusion` samples from it, by `MIN_PEAK_MARGIN`.

    A rival is a local maximum: a shift at least as good as its neighbours (an
    end, as good as its one). Not merely any shift: the peak's own shoulder
    just past the exclusion is the same alignment, a few saccades fewer, and a
    gaze trace fixating for about 300 ms at a time still correlates about 0.87
    with itself 50 ms on, so the shoulder would refuse a third of good windows
    while telling nothing about another alignment. A period, a repeated
    pattern, a fixation's noise: each is another local maximum, and refuses.
    With no rival within reach (a walk's search is 150 ms either way), the peak
    is the only alignment there, and distinct."""
    best = float(correlation[peak])
    if not best >= MIN_CORRELATION:
        return False
    rival = np.ones(correlation.size, dtype=bool)
    rival[1:] &= correlation[1:] >= correlation[:-1]
    rival[:-1] &= correlation[:-1] >= correlation[1:]
    rival[max(peak - exclusion, 0) : peak + exclusion + 1] = False
    return not rival.any() or best - float(np.max(correlation[rival])) >= MIN_PEAK_MARGIN


def _drop_and_refit(found: Sequence[_Hit]) -> tuple[list[_Hit], list[_Hit]]:
    """The pair furthest from the line dropped and the line refitted, until every
    pair is within `RESIDUAL_BOUND_US`, at most `MAX_DROPPED_FRACTION` of them."""
    kept, dropped = list(found), []
    limit = int(MAX_DROPPED_FRACTION * len(found))
    while True:
        pairs = [(hit.ni_s, hit.device_s) for hit in kept]
        # A nominal rate of 1.0: both sides are seconds, so the scale is the rate.
        fit = fit_pairs_across([pairs], 1.0)
        if fit.residual_us_max <= RESIDUAL_BOUND_US:
            break
        residuals = _residuals_us(pairs, fit.scale)
        if len(dropped) >= limit:
            off = len(dropped) + int(np.sum(residuals > RESIDUAL_BOUND_US))
            raise CopyRefused(
                f"{off} of {len(found)} pairs, {round(100 * off / len(found))}% of the pairs, lie further than "
                f"{RESIDUAL_BOUND_US:g} us from the line, and at most {MAX_DROPPED_FRACTION:.0%} may be dropped; "
                f"after dropping {len(dropped)}, the residual is still {fit.residual_us_max:.0f} us"
            )
        dropped.append(kept.pop(int(np.argmax(residuals))))
    if len(kept) < MIN_PAIRS:
        raise CopyRefused(
            f"too few pairs: {len(kept)} remain after dropping {len(dropped)} off the line, "
            f"and a copy needs {MIN_PAIRS}"
        )
    dropped.sort(key=lambda hit: hit.start_s)
    return kept, dropped


def _residuals_us(pairs: Sequence[tuple[float, float]], scale: float) -> np.ndarray:
    """Each pair's distance from the line `fit_pairs_across` fitted, in the NI's
    frame, as it computes its own maximum."""
    ni, device = np.array(pairs, dtype=np.float64).T
    intercept = float(np.mean(device)) - scale * float(np.mean(ni))
    return np.abs((device - intercept) / scale - ni) * 1e6


def _stretches(dropped: Sequence[_Hit]) -> tuple[tuple[float, float], ...]:
    """The dropped pairs' windows, in device seconds, adjacent ones merged."""
    merged: list[list[float]] = []
    for hit in dropped:
        start, stop = hit.start_s, hit.start_s + WINDOW_S
        if merged and start <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], stop)
        else:
            merged.append([start, stop])
    return tuple((start, stop) for start, stop in merged)


def _window_samples(fs_hz: float) -> int:
    return round(WINDOW_S * fs_hz)


def _window_starts(eye_time_s: np.ndarray, limit_s: float | None = None) -> list[float]:
    """Each window's start, `STEP_S` apart from the eye's first sample, every
    window wholly inside the trace (and inside its first `limit_s`, if given)."""
    if eye_time_s.size < 2:
        return []
    first, end = float(eye_time_s[0]), float(eye_time_s[-1])
    if limit_s is not None:
        end = min(end, first + limit_s)
    count = max(math.floor((end - first - WINDOW_S) / STEP_S + 1e-9) + 1, 0)
    return [first + k * STEP_S for k in range(count)]


def _window(
    eye_time_s: np.ndarray, trace: np.ndarray, start_s: float, fs_hz: float, scale: float
) -> _Window | None:
    """The trace from `start_s` for `WINDOW_S`, on the channel's grid; None when
    too much of it is missing, or it is constant.

    The grid is the channel's, carried into device time by `scale` (device
    seconds per NI second, from the line so far): at 100 ppm a window on the
    device's own grid is a sample longer than its copy, and its peak then
    favours whichever end has the saccades, by up to half a sample."""
    n = _window_samples(fs_hz)
    device_step_s = scale / fs_hz
    grid = start_s + np.arange(n) * device_step_s
    lo = max(int(np.searchsorted(eye_time_s, grid[0], side="right")) - 1, 0)
    hi = min(int(np.searchsorted(eye_time_s, grid[-1], side="left")) + 1, eye_time_s.size)
    times = eye_time_s[lo:hi]
    values = np.asarray(trace[lo:hi], dtype=np.float64)
    finite = np.isfinite(values)
    if times.size < 2 or np.count_nonzero(finite) < 2:
        return None
    # A grid point is missing unless both samples bracketing it are present and
    # no further apart than a frame and a half.
    period = float(np.median(np.diff(times)))
    left = np.clip(np.searchsorted(times, grid, side="right") - 1, 0, times.size - 2)
    bracketed = (times[left] <= grid) & (grid <= times[left + 1])
    present = bracketed & finite[left] & finite[left + 1] & (times[left + 1] - times[left] <= _MAX_GAP_FRAMES * period)
    if np.count_nonzero(present) < (1.0 - _MAX_MISSING_FRACTION) * n:
        return None
    on_grid = np.interp(grid, times[finite], values[finite])
    centred = on_grid - np.mean(on_grid)
    spread = float(np.sqrt(np.mean(centred * centred)))
    if not spread > _CONSTANT_SPREAD_FRACTION * float(np.max(np.abs(on_grid))):
        return None
    return _Window(start_s, centred / (n * spread), device_step_s)


def _moving_spread(values: np.ndarray, n: int, flat_variance: float) -> np.ndarray:
    """The population standard deviation of every `n` consecutive values, 0
    where it is flat. Chunked, each chunk from its own centred sums."""
    n_shifts = values.size - n + 1
    spread = np.zeros(max(n_shifts, 0))
    for a in range(0, n_shifts, _SPREAD_CHUNK):
        b = min(a + _SPREAD_CHUNK, n_shifts)
        stretch = values[a : b + n - 1]
        stretch = stretch - np.mean(stretch)
        sums = np.concatenate(([0.0], np.cumsum(stretch)))
        squares = np.concatenate(([0.0], np.cumsum(stretch * stretch)))
        mean = (sums[n:] - sums[:-n]) / n
        variance = (squares[n:] - squares[:-n]) / n - mean * mean
        spread[a:b] = np.sqrt(np.where(variance > flat_variance, variance, 0.0))
    return spread


def _divide(dot: np.ndarray, spread: np.ndarray) -> np.ndarray:
    """`dot / spread`, 0 where the spread is (flat channel)."""
    return np.divide(dot, spread, out=np.zeros_like(dot), where=spread > 0.0)


def _check_lengths(eye_time_s: np.ndarray, trace: np.ndarray) -> None:
    if len(trace) != eye_time_s.size:
        raise ValueError(f"a trace of {len(trace)} samples on {eye_time_s.size} eye times")
