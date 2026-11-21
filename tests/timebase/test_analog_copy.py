"""The eye tracker's trace lined up with its analog copy on an NI channel
(design spec `2026-10-18-training-records-and-backup-clock-design.md`,
amendments 78, 78a and 84).

Every array is built here: an eye trace of fixations and saccades at 500 Hz on
the eye's own clock, and its copy on an NI channel at `DECIMATED_RATE_HZ`, on
the NI's clock, 100 ppm faster, 5 ms late, scaled by 0.004 V per pixel, offset
and noisy. The truth is that map, so each pair is checked against it, not
against a line fitted to the pairs themselves. A wrong match is silent: it
mistimes the eye by a fixation or a period with residuals as small as a right
one's, so a refusal is always preferred to it.
"""

from __future__ import annotations

import re
import warnings

import numpy as np
import pytest

from wl_preproc.timebase.analog_copy import (
    DECIMATED_RATE_HZ,
    MAX_DROPPED_FRACTION,
    MIN_CORRELATION,
    MIN_PAIRS,
    WINDOW_S,
    CopyMatch,
    CopyRefused,
    NoCopy,
    _Reference,
    _window,
    _window_starts,
    find_channel,
    match_copy,
)

EYE_FS_HZ = 500.0
FS = DECIMATED_RATE_HZ
LAG_S = 0.005
SCALE_V_PER_PX = 0.004
RATE_PPM = 100.0
OFFSET_V = 0.3
NOISE_V = 0.002
#: A pair is "on the line" within this of the truth: a tenth of a 2 ms frame.
ON_THE_LINE_S = 0.2e-3


def _eye_time(duration_s: float) -> np.ndarray:
    return np.arange(int(duration_s * EYE_FS_HZ)) / EYE_FS_HZ


def _gaze_xy(rng: np.random.Generator, n: int, sd_px: tuple[float, float] = (150.0, 112.5)) -> np.ndarray:
    """Both axes of one eye's gaze: fixations of 150-500 ms joined by 20-60 ms
    saccades (a raised-cosine ramp), each saccade moving X and Y together, with
    1 px of measurement noise per frame on each, which the copy carries too,
    since the DAC writes the value the tracker computed. Y's spread is 0.75 of
    X's, as Task 1's generator's 30 px against 40 px."""
    gaze = np.empty((2, n))
    position, i = np.zeros(2), 0
    while i < n:
        n_saccade = int(rng.uniform(0.02, 0.06) * EYE_FS_HZ)
        n_fixation = int(rng.uniform(0.15, 0.5) * EYE_FS_HZ)
        target = rng.normal(0.0, sd_px)
        shape = (1 - np.cos(np.pi * np.arange(n_saccade) / n_saccade)) / 2
        ramp = position[:, None] + (target - position)[:, None] * shape
        piece = np.concatenate((ramp, np.repeat(target[:, None], n_fixation, axis=1)), axis=1)[:, : n - i]
        gaze[:, i : i + piece.shape[1]] = piece
        position, i = target, i + piece.shape[1]
    return gaze + rng.normal(0.0, 1.0, (2, n))


def _gaze(rng: np.random.Generator, n: int) -> np.ndarray:
    """One axis of `_gaze_xy`."""
    return _gaze_xy(rng, n)[0]


def _truth(device_s: np.ndarray, device_at_ni_zero_s: float) -> np.ndarray:
    """`ni_s - device_s` for the copy of device time `device_s`."""
    return (device_s - device_at_ni_zero_s) * (1 + RATE_PPM * 1e-6) + LAG_S - device_s


def _copy(
    eye_time_s: np.ndarray,
    trace: np.ndarray,
    rng: np.random.Generator,
    *,
    ni_duration_s: float,
    device_at_ni_zero_s: float = -3.0,
    late: tuple[float, float, float] | None = None,
) -> np.ndarray:
    """The NI channel: at NI time `tau` it shows the trace at the device time
    whose copy arrives then, `(tau - LAG_S) / (1 + ppm) + device_at_ni_zero_s`,
    interpolated (a smooth DAC, so the truth is exactly `_truth`). `late` is
    `(start_s, stop_s, extra_s)` in device time: there the copy shows the trace
    `extra_s` late as well. Clipped at the NI's +-5 V, as float32 like Task 3's
    decimated channels."""
    tau = np.arange(int(ni_duration_s * FS)) / FS
    shown = (tau - LAG_S) / (1 + RATE_PPM * 1e-6) + device_at_ni_zero_s
    if late is not None:
        start_s, stop_s, extra_s = late
        shown = shown - np.where((shown >= start_s) & (shown < stop_s), extra_s, 0.0)
    volts = OFFSET_V + SCALE_V_PER_PX * np.interp(shown, eye_time_s, trace) + rng.normal(0.0, NOISE_V, tau.size)
    return np.clip(volts, -5.0, 5.0).astype(np.float32)


def _noise_channel(rng: np.random.Generator, ni_duration_s: float) -> np.ndarray:
    return rng.normal(0.0, NOISE_V, int(ni_duration_s * FS)).astype(np.float32)


def _offsets(match: CopyMatch, device_at_ni_zero_s: float) -> np.ndarray:
    """Each pair's distance from the truth, in seconds."""
    ni, device = np.array(match.pairs).T
    return (ni - device) - _truth(device, device_at_ni_zero_s)


def test_a_copy_is_matched_to_within_a_tenth_of_a_frame():
    rng = np.random.default_rng(1)
    eye_time = _eye_time(300.0)
    trace = _gaze(rng, eye_time.size)
    channel = _copy(eye_time, trace, rng, ni_duration_s=310.0)

    match = match_copy(eye_time, trace, channel, FS)

    assert match.n_dropped == 0
    assert match.dropped_stretches == ()
    assert len(match.pairs) >= 0.9 * match.n_windows >= 0.9 * 29
    assert np.max(np.abs(_offsets(match, -3.0))) <= ON_THE_LINE_S


def test_a_mixed_and_limited_copy_still_clears_the_correlation_floor():
    """Task 1's synthetic copy is `tanh` of 0.7 X + 0.3 Y, so it correlates with
    X alone about 0.93-0.95, close to `MIN_CORRELATION`. Window by window, at
    the anchor's view of every shift, the margin is pinned: the median window
    clears the floor by 0.03 and nine in ten clear it (about one in thirty does
    not, where Y happens to move more than X).

    Only the correlation: a mixed copy's peak is pulled by Y's saccades, which
    X does not carry, by about a millisecond a window, so its pairs are not
    held to `ON_THE_LINE_S` (the task's report names it)."""
    rng = np.random.default_rng(2)
    eye_time = _eye_time(300.0)
    x, y = _gaze_xy(rng, eye_time.size)
    mixed = 0.7 * x + 0.3 * y
    limit = 3.0 * float(np.std(mixed))
    channel = _copy(eye_time, limit * np.tanh(mixed / limit), rng, ni_duration_s=310.0)
    reference = _Reference(channel, FS)

    peaks = [
        float(np.max(reference.correlations(_window(eye_time, x, start, FS, 1.0))))
        for start in _window_starts(eye_time)
    ]

    assert len(peaks) == 29
    assert np.median(peaks) >= MIN_CORRELATION + 0.03
    assert np.mean(np.array(peaks) >= MIN_CORRELATION) >= 0.9


def test_the_channel_and_signal_are_found_among_noise():
    """Sixteen channels, the copy on channel 11. The right eye's X follows the
    left's (two eyes look at one target), so it matches too, only less well:
    the count ties, and the closer match must win it."""
    rng = np.random.default_rng(3)
    eye_time = _eye_time(220.0)
    left_x, left_y = _gaze_xy(rng, eye_time.size)
    # The right eye first, so that the tie is not won by order.
    candidates = {
        "Right P1-P4 X": left_x + rng.normal(0.0, 20.0, eye_time.size),
        "Left P1-P4 X": left_x,
        "Left P1-P4 Y": left_y,
        "Left PupilWidth": 40.0 + np.cumsum(rng.normal(0.0, 0.2, eye_time.size)),
    }
    channels = [_noise_channel(rng, 225.0) for _ in range(16)]
    channels[11] = _copy(eye_time, left_x, rng, ni_duration_s=225.0)

    assert find_channel(eye_time, candidates, channels, FS) == (11, "Left P1-P4 X")


def test_noise_alone_is_no_copy():
    rng = np.random.default_rng(4)
    eye_time = _eye_time(220.0)
    trace = _gaze(rng, eye_time.size)
    channels = [_noise_channel(rng, 225.0) for _ in range(3)]

    with pytest.raises(NoCopy):
        find_channel(eye_time, {"Left P1-P4 X": trace}, channels, FS)
    with pytest.raises(NoCopy):
        match_copy(eye_time, trace, channels[0], FS)


def test_a_flat_channel_is_no_copy():
    """A stuck DAC: the channel holds one value. No division by its zero spread,
    so no warning either."""
    rng = np.random.default_rng(5)
    eye_time = _eye_time(220.0)
    trace = _gaze(rng, eye_time.size)
    flat = np.full(int(225.0 * FS), OFFSET_V, dtype=np.float32)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(NoCopy):
            find_channel(eye_time, {"Left P1-P4 X": trace}, [flat], FS)
        with pytest.raises(NoCopy):
            match_copy(eye_time, trace, flat, FS)


def test_a_stall_is_dropped_and_named():
    """5 s of the copy 40 ms late, as a USB DAC stalls: inside the search, so
    the window over it gives a pair off the line, which is dropped, named, and
    leaves the line where the truth is."""
    rng = np.random.default_rng(6)
    eye_time = _eye_time(300.0)
    trace = _gaze(rng, eye_time.size)
    channel = _copy(eye_time, trace, rng, ni_duration_s=310.0, late=(100.0, 105.0, 0.040))

    match = match_copy(eye_time, trace, channel, FS)

    assert match.n_dropped > 0
    assert any(start <= 100.0 and 105.0 <= stop for start, stop in match.dropped_stretches)
    assert np.max(np.abs(_offsets(match, -3.0))) <= ON_THE_LINE_S


def test_too_many_late_windows_refuse():
    """A quarter of the trace 40 ms late: more than `MAX_DROPPED_FRACTION` of
    the pairs would have to go, so the copy is refused, naming the fraction."""
    rng = np.random.default_rng(7)
    eye_time = _eye_time(400.0)
    trace = _gaze(rng, eye_time.size)
    channel = _copy(eye_time, trace, rng, ni_duration_s=410.0, late=(150.0, 250.0, 0.040))

    with pytest.raises(CopyRefused) as refused:
        match_copy(eye_time, trace, channel, FS)

    message = str(refused.value)
    fraction = re.search(r"(\d+)% of the pairs", message)
    assert fraction is not None, message
    assert int(fraction.group(1)) > 100 * MAX_DROPPED_FRACTION
    assert "dropped" in message


def test_an_eye_trace_that_starts_before_the_channel_is_anchored():
    """The rig may run before the NI starts (amendment 44) and after it stops:
    the eye's first minute and last 40 s have no copy. The anchor is searched
    for over the whole channel, window after window, and pairs exist only
    where the NI recorded."""
    rng = np.random.default_rng(8)
    eye_time = _eye_time(360.0)
    trace = _gaze(rng, eye_time.size)
    channel = _copy(eye_time, trace, rng, ni_duration_s=260.0, device_at_ni_zero_s=60.0)

    match = match_copy(eye_time, trace, channel, FS)

    ni, device = np.array(match.pairs).T
    assert len(match.pairs) >= MIN_PAIRS
    # A window's centre is half a sample short of its middle.
    assert device.min() >= 60.0 + WINDOW_S / 2 - 1e-3
    assert np.all((ni >= 0.0) & (ni <= channel.size / FS))
    assert np.max(np.abs(_offsets(match, 60.0))) <= ON_THE_LINE_S


def test_clipped_windows_give_no_pair():
    """The eye looks far away for 40 s, and the copy is held at the NI's +5 V
    rail. No window more than a tenth at the rail gives a pair: those wholly
    inside are flat, and the two that are half inside keep one edge, which the
    clip bends. The rest are on the line."""
    rng = np.random.default_rng(9)
    eye_time = _eye_time(300.0)
    trace = _gaze(rng, eye_time.size)
    away = (eye_time >= 105.0) & (eye_time < 145.0)
    trace[away] += 2000.0
    channel = _copy(eye_time, trace, rng, ni_duration_s=310.0)
    assert np.mean(channel[int(115 * FS) : int(135 * FS)] == np.float32(5.0)) > 0.99

    match = match_copy(eye_time, trace, channel, FS)

    _, device = np.array(match.pairs).T
    assert not np.any((device > 100.0) & (device < 150.0))
    assert np.max(np.abs(_offsets(match, -3.0))) <= ON_THE_LINE_S


def test_a_constant_candidate_is_skipped():
    """One eye untracked: its columns are all zero. It is skipped, with no
    division by zero, no warning and no NaN pair."""
    rng = np.random.default_rng(10)
    eye_time = _eye_time(220.0)
    trace = _gaze(rng, eye_time.size)
    zeros = np.zeros(eye_time.size)
    channels = [_noise_channel(rng, 225.0), _copy(eye_time, trace, rng, ni_duration_s=225.0)]
    candidates = {"Right P1-P4 X": zeros, "Left P1-P4 X": trace, "Right P1-P4 Y": zeros}

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert find_channel(eye_time, candidates, channels, FS) == (1, "Left P1-P4 X")
        with pytest.raises(NoCopy):
            match_copy(eye_time, zeros, channels[1], FS)
        match = match_copy(eye_time, trace, channels[1], FS)

    assert np.all(np.isfinite(np.array(match.pairs)))


def test_a_periodic_trace_refuses_rather_than_matching_a_wrong_period():
    """A sinusoid matches its copy equally well a period either way: no window
    is distinct, so there is no anchor, and no match."""
    rng = np.random.default_rng(11)
    eye_time = _eye_time(300.0)
    trace = 100.0 * np.sin(2 * np.pi * 0.37 * eye_time) + rng.normal(0.0, 1.0, eye_time.size)
    channel = _copy(eye_time, trace, rng, ni_duration_s=310.0)

    with pytest.raises((NoCopy, CopyRefused)):
        match_copy(eye_time, trace, channel, FS)


def test_missing_samples_give_no_pair_where_they_dominate():
    """NaN is missing (a blink, a lost track). A window mostly missing gives no
    pair; a short blink in a window leaves it paired, on the line, and no pair
    is NaN."""
    rng = np.random.default_rng(12)
    eye_time = _eye_time(300.0)
    trace = _gaze(rng, eye_time.size)
    channel = _copy(eye_time, trace, rng, ni_duration_s=310.0)
    trace[(eye_time >= 100.0) & (eye_time < 108.0)] = np.nan
    trace[(eye_time >= 151.0) & (eye_time < 151.1)] = np.nan

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        match = match_copy(eye_time, trace, channel, FS)

    _, device = np.array(match.pairs).T
    assert np.all(np.isfinite(np.array(match.pairs)))
    assert not np.any((device > 100.0) & (device < 110.0))
    assert np.any((device > 150.0) & (device < 160.0))
    assert np.max(np.abs(_offsets(match, -3.0))) <= ON_THE_LINE_S
