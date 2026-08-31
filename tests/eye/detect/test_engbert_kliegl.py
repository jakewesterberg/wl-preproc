import numpy as np
import pytest

from wl_preproc.eye.detect.engbert_kliegl import (
    DEFAULT_EK_PARAMS, EngbertKlieglParams, detect_engbert_kliegl,
)

FS_HZ = 500.0


def _trace_with_saccades(onsets, amplitude_deg=8.0, n=2000, dur=10, seed=3):
    """A still eye with tiny noise, stepped by `amplitude_deg` over `dur`
    samples at each onset. Returns `(gaze, planted_intervals)`.

    Each step is applied to everything from its onset onward, then the ramp
    region is pulled back linearly -- so the gaze is flat, ramps once per
    onset, and stays at its new level. Written this way rather than by
    accumulating an offset because an off-by-one in the accumulation would
    plant saccades at times the test then "confirms".
    """
    rng = np.random.default_rng(seed)
    gaze = rng.normal(0.0, 0.01, (n, 2))
    planted = []
    for onset in onsets:
        gaze[onset:, 0] += amplitude_deg
        gaze[onset : onset + dur, 0] -= amplitude_deg * (1.0 - np.linspace(0.0, 1.0, dur))
        planted.append((onset, onset + dur))
    return gaze, planted


def test_it_finds_planted_saccades_at_their_planted_times():
    """NOT 'some events were found'. The eye plan shipped a suite where gutting
    the whole session-time-to-row alignment left every test green because
    nothing asserted a fitted result was numerically right; a detector suite
    that only counts events has the same hole."""
    from wl_preproc.eye.detect.velocity import velocity

    gaze, planted = _trace_with_saccades([300, 800, 1400])
    available = np.full(len(gaze), None, dtype=object)

    found = detect_engbert_kliegl(gaze, velocity(gaze, FS_HZ), available, DEFAULT_EK_PARAMS)

    assert len(found) == len(planted)
    for (got_start, got_stop), (want_start, want_stop) in zip(found, planted, strict=True):
        assert abs(got_start - want_start) <= 3
        assert abs(got_stop - want_stop) <= 3


def test_a_still_eye_yields_nothing():
    """The false-positive floor. A detector that fires on noise makes every
    downstream agreement number meaningless."""
    from wl_preproc.eye.detect.velocity import velocity

    rng = np.random.default_rng(11)
    gaze = rng.normal(0.0, 0.01, (2000, 2))
    available = np.full(2000, None, dtype=object)

    assert detect_engbert_kliegl(gaze, velocity(gaze, FS_HZ), available, DEFAULT_EK_PARAMS) == []


def test_events_shorter_than_the_minimum_duration_are_rejected():
    from wl_preproc.eye.detect.velocity import velocity

    gaze, _ = _trace_with_saccades([500], dur=2)
    available = np.full(len(gaze), None, dtype=object)
    strict = EngbertKlieglParams(lambda_=6.0, min_duration_samples=6)

    assert detect_engbert_kliegl(gaze, velocity(gaze, FS_HZ), available, strict) == []


def test_a_higher_lambda_finds_fewer_events():
    """Pins that lambda is actually consulted. A hardcoded threshold passes
    every test above."""
    from wl_preproc.eye.detect.velocity import velocity

    gaze, _ = _trace_with_saccades([300, 800, 1400], amplitude_deg=0.4)
    available = np.full(len(gaze), None, dtype=object)
    v = velocity(gaze, FS_HZ)

    lenient = detect_engbert_kliegl(gaze, v, available, EngbertKlieglParams(3.0, 6))
    strict = detect_engbert_kliegl(gaze, v, available, EngbertKlieglParams(30.0, 6))

    assert len(lenient) > len(strict)


def test_unavailable_samples_are_never_part_of_an_event():
    """A detector must not label a sample the mask has already claimed --
    precedence is structural, not a convention each detector honours."""
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.velocity import velocity

    gaze, planted = _trace_with_saccades([300, 800])
    available = np.full(len(gaze), None, dtype=object)
    available[295:320] = Label.BLINK

    found = detect_engbert_kliegl(gaze, velocity(gaze, FS_HZ), available, DEFAULT_EK_PARAMS)

    assert all(not (start < 320 and stop > 295) for start, stop in found)


def test_the_defaults_are_the_papers_conventional_values():
    assert DEFAULT_EK_PARAMS.lambda_ == 6.0
    assert DEFAULT_EK_PARAMS.min_duration_samples == 6


# --- Below: tests for branches the brief's own listed suite does not reach.
# Named explicitly (task-5-brief.md) as places to look hard at: the
# `_median_scale` `variance > 0` guard, the `if not usable.any()` early
# return, and the `if eta_x <= 0 or eta_y <= 0` guard.


def test_a_fully_unavailable_trace_yields_nothing():
    """Every sample already claimed by the validity mask -- `usable.any()` is
    False. Exercises the early return directly, independent of the
    `eta <= 0` guard that (see task-5-report.md) would coincidentally also
    catch this same case."""
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.velocity import velocity

    gaze, _ = _trace_with_saccades([300, 800])
    available = np.full(len(gaze), Label.INVALID, dtype=object)

    assert detect_engbert_kliegl(gaze, velocity(gaze, FS_HZ), available, DEFAULT_EK_PARAMS) == []


def test_a_degenerate_constant_velocity_axis_yields_nothing():
    """`_median_scale` is zero whenever a velocity component has zero
    dispersion -- not just when nothing is usable. `velocity_deg_s` is
    constructed directly (bypassing `velocity()`) so the x-axis is a *bit-
    identical* constant across every usable sample -- a floating-point-exact
    tie, giving `eta_x == 0.0` exactly rather than the tiny-but-positive value
    a real differentiated ramp leaves behind. `eta_y` comes from real noise
    and is a normal positive threshold. Verified by direct computation
    (task-5-report.md) that without the `eta <= 0` guard this divides
    real, nonzero x-velocity by exactly zero -- `inf`, then `inf > 1.0` --
    and 1996 of 2000 samples are wrongly flagged as one giant spurious event;
    with the guard, the detector correctly declines to threshold on a
    degenerate scale and returns nothing."""
    n = 2000
    rng = np.random.default_rng(7)
    velocity_deg_s = np.zeros((n, 2))
    velocity_deg_s[2:-2, 0] = 5.0  # exact constant -> zero dispersion
    velocity_deg_s[2:-2, 1] = rng.normal(0.0, 1.5, n - 4)  # real noise
    gaze = np.zeros((n, 2))  # unused by detect_engbert_kliegl; velocity_deg_s is the input
    available = np.full(n, None, dtype=object)

    found = detect_engbert_kliegl(gaze, velocity_deg_s, available, DEFAULT_EK_PARAMS)

    assert found == []


def test_median_scale_is_zero_not_nan_for_a_constant_component():
    """Direct unit test of `_median_scale`'s `variance > 0` guard: a constant
    array has provably zero dispersion (`median(v**2) == median(v)**2`
    exactly, since squaring is monotonic on same-signed input), landing
    exactly on the guard's boundary. Pins that the boundary itself is handled
    (returns 0.0, not nan from `sqrt` of a tiny negative float)."""
    from wl_preproc.eye.detect.engbert_kliegl import _median_scale

    assert _median_scale(np.full(100, 5.0)) == pytest.approx(0.0)
    assert _median_scale(np.zeros(100)) == pytest.approx(0.0)


def test_median_scale_survives_genuine_floating_point_negative_variance():
    """`median(v**2) - median(v)**2` is mathematically bounded below by zero
    for the median-based scale estimator (an order-statistics argument: at
    most half the sample can have smaller magnitude than the median, so the
    squared array's median can never fall below the median's own square) --
    but that guarantee is about EXACT arithmetic. This literal array was
    found by a targeted random search over small-n float64 arrays for one
    where two near-but-not-bit-identical values land as the two middle order
    statistics: `np.median(x**2) - np.median(x)**2` then evaluates to
    -1.7763568394002505e-15 in float64 (task-5-report.md), a real, negative,
    reproducible result of floating-point cancellation -- not a hypothetical.
    Verified directly: `np.sqrt` of that raw value is `nan`, with numpy's own
    "invalid value encountered in sqrt" warning; `_median_scale` must return
    0.0, not propagate that nan into `eta`."""
    from wl_preproc.eye.detect.engbert_kliegl import _median_scale

    x = np.array(
        [-2.073577100040317, -2.0735771000394543, -0.6049092429737151, -2.2681925187157725]
    )
    assert np.median(x**2) - np.median(x) ** 2 < 0.0  # the raw formula genuinely goes negative
    assert _median_scale(x) == 0.0


def test_a_heavily_contaminated_trace_still_finds_a_small_saccade():
    """The module's central claim, made to fail rather than assumed: a real
    standard deviation is inflated by the very saccades it is trying to find.
    Neither `test_it_finds_planted_saccades_at_their_planted_times` (3 events
    in 2000 samples) nor `test_a_still_eye_yields_nothing` (0 events) carries
    enough contamination to separate the two estimators -- confirmed by
    mutation (task-5-report.md): swapping `_median_scale` for `np.std` leaves
    both of those tests passing. This fixture packs 15 large (10 deg)
    saccades into 4000 samples, then plants one small (1 deg) saccade after
    them, and asserts the small one is still found.

    With the median estimator this holds (`eta_x` stays ~7, barely moved from
    a clean trace's ~7.1-7.4). Measured directly (task-5-report.md): with
    `np.std` in place of `_median_scale`, `eta_x` inflates to ~564 -- about
    78x -- and the detector finds NONE of the 16 planted events, not just the
    small one. That is the failure mode this whole module exists to avoid:
    the detector going blind in proportion to how much there is to detect.
    """
    from wl_preproc.eye.detect.velocity import velocity

    n = 4000
    dur = 10
    big_onsets = list(range(100, 3800, 250))  # 15 large saccades
    gaze, planted = _trace_with_saccades(big_onsets, amplitude_deg=10.0, n=n, dur=dur, seed=5)

    small_onset = 3900
    gaze[small_onset:, 0] += 1.0
    gaze[small_onset : small_onset + dur, 0] -= 1.0 * (1.0 - np.linspace(0.0, 1.0, dur))
    planted.append((small_onset, small_onset + dur))

    available = np.full(n, None, dtype=object)

    found = detect_engbert_kliegl(gaze, velocity(gaze, FS_HZ), available, DEFAULT_EK_PARAMS)

    assert len(found) == len(planted)
    small_start, small_stop = found[-1]
    assert abs(small_start - small_onset) <= 3
    assert abs(small_stop - (small_onset + dur)) <= 3
