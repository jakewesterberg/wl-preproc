import numpy as np
import pytest

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.measure import (
    MICROSACCADE_MAX_DEG, classify, measure, measure_event_run,
)


def _ramp(n=20, fs_hz=500.0, vx=100.0, vy=0.0):
    t = np.arange(n) / fs_hz
    return np.column_stack([vx * t, vy * t])


def test_amplitude_is_the_displacement_across_the_interval():
    """Endpoint-to-endpoint, NOT path length: a saccade's amplitude is where
    the eye ended up relative to where it started, and path length would count
    any wobble on the way as extra amplitude."""
    gaze = np.column_stack([[0.0, 1.0, 3.0, 2.0, 4.0], np.zeros(5)])
    velocity = np.zeros((5, 2))

    result = measure(gaze, velocity, start=0, stop=5, fs_hz=500.0)

    assert result.amplitude_deg == pytest.approx(4.0)


def test_amplitude_is_euclidean_across_both_axes():
    gaze = np.array([[0.0, 0.0], [3.0, 4.0]])
    result = measure(gaze, np.zeros((2, 2)), start=0, stop=2, fs_hz=500.0)
    assert result.amplitude_deg == pytest.approx(5.0)


def test_peak_velocity_is_the_maximum_speed_inside_the_interval_only():
    """Bounded to the interval: a faster sample just outside it belongs to a
    different event, and letting it leak in would inflate the main sequence
    that vigor is measured against (design spec section 6.5)."""
    velocity = np.zeros((10, 2))
    velocity[2] = [50.0, 0.0]
    velocity[4] = [80.0, 60.0]      # speed 100, inside
    velocity[8] = [400.0, 0.0]      # outside

    result = measure(np.zeros((10, 2)), velocity, start=1, stop=6, fs_hz=500.0)

    assert result.peak_velocity_deg_s == pytest.approx(100.0)


def test_duration_counts_samples_not_endpoints():
    """`stop` is exclusive, so a 6-sample event at 500 Hz lasts 12 ms."""
    result = measure(np.zeros((20, 2)), np.zeros((20, 2)), start=4, stop=10, fs_hz=500.0)
    assert result.duration_s == pytest.approx(6 / 500.0)


def test_classify_splits_at_the_threshold_and_the_boundary_is_a_saccade():
    """At-or-above is a saccade. Stated because a boundary convention nobody
    writes down is one every reimplementation gets to choose differently."""
    assert classify(0.4, MICROSACCADE_MAX_DEG) is Label.MICROSACCADE
    assert classify(0.999, MICROSACCADE_MAX_DEG) is Label.MICROSACCADE
    assert classify(1.0, MICROSACCADE_MAX_DEG) is Label.SACCADE
    assert classify(12.0, MICROSACCADE_MAX_DEG) is Label.SACCADE


def test_the_threshold_default_is_the_conventional_one_degree():
    assert MICROSACCADE_MAX_DEG == 1.0


def test_measure_requires_stop_greater_than_start():
    """Empty intervals silently read nonsensical indices; measure rejects them
    explicitly."""
    with pytest.raises(ValueError, match="stop > start.*start=2.*stop=2"):
        measure(np.zeros((5, 2)), np.zeros((5, 2)), start=2, stop=2, fs_hz=500.0)
    with pytest.raises(ValueError, match="stop > start.*start=5.*stop=3"):
        measure(np.zeros((5, 2)), np.zeros((5, 2)), start=5, stop=3, fs_hz=500.0)


# -- `measure_event_run`: a detector's declared measurement rule ------------------
#
# The requester's decision of 2026-09-27 (design spec `2026-09-27-nslr-design.md`
# section 4): NSLR's per-eye saccade runs are measured up to where the eye lands,
# and one under 10 ms is stored unmeasured. Every other detector declares
# neither rule, and is measured exactly as `measure` measures it.


def _walk(n=40):
    """Gaze that moves every sample, differently on each axis, so any two
    samples' displacement tells which two they were."""
    return np.column_stack([0.5 * np.arange(n), 0.25 * np.arange(n) ** 1.5])


def _offered(n, withheld=()):
    offered = np.full(n, None, dtype=object)
    for index in withheld:
        offered[index] = Label.INVALID
    return offered


def _speeds(n=40):
    velocity = np.zeros((n, 2))
    velocity[:, 0] = 100.0
    return velocity


@pytest.mark.parametrize("start, stop", [(0, 1), (3, 4), (5, 12), (10, 40), (0, 40)])
def test_with_no_rule_declared_a_run_is_measured_as_measure_measures_it(start, stop):
    """Every detector but NSLR: its stored rows must not move."""
    gaze = _walk()
    velocity = np.random.default_rng(7).normal(0.0, 50.0, gaze.shape)

    got = measure_event_run(gaze, velocity, _offered(40), start, stop, 500.0,
                            runs_end_before_landing=False, min_measured_ms=None)

    assert got == measure(gaze, velocity, start, stop, 500.0)


def test_a_run_ending_before_its_landing_is_measured_to_the_landing_sample():
    """Amplitude to `gaze[stop]`, peak velocity over `[start, stop]`
    inclusive, and not a sample further."""
    gaze = _walk()
    velocity = _speeds()
    velocity[16] = [300.0, 0.0]   # the landing sample: inside
    velocity[17] = [900.0, 0.0]   # the sample after it: outside

    got = measure_event_run(gaze, velocity, _offered(40), 10, 16, 500.0,
                            runs_end_before_landing=True, min_measured_ms=None)

    displacement = gaze[16] - gaze[10]
    assert got.amplitude_deg == float(np.hypot(displacement[0], displacement[1]))
    assert got.amplitude_deg != measure(gaze, velocity, 10, 16, 500.0).amplitude_deg
    assert got.peak_velocity_deg_s == 300.0
    assert got.duration_s == 6 / 500.0


def test_a_one_sample_run_reads_its_whole_step_rather_than_zero():
    """C1's worst case: `measure` reads a one-sample run as exactly 0.0."""
    gaze = _walk()

    got = measure_event_run(gaze, _speeds(), _offered(40), 10, 11, 500.0,
                            runs_end_before_landing=True, min_measured_ms=None)

    assert measure(gaze, _speeds(), 10, 11, 500.0).amplitude_deg == 0.0
    displacement = gaze[11] - gaze[10]
    assert got.amplitude_deg == float(np.hypot(displacement[0], displacement[1])) > 0.0


@pytest.mark.parametrize("case", ["stop is the last sample", "stop is withheld", "stop is not finite"])
def test_the_landing_sample_is_used_only_at_an_interior_knot(case):
    """Otherwise the run ends at its piece's last sample, which is its own
    landing knot, and it is measured as `measure` measures it."""
    gaze = _walk()
    offered = _offered(40)
    start, stop = 30, 36
    if case == "stop is the last sample":
        start, stop = 34, 40
    elif case == "stop is withheld":
        offered[stop] = Label.BLINK
    else:
        gaze[stop, 1] = np.nan

    got = measure_event_run(gaze, _speeds(), offered, start, stop, 500.0,
                            runs_end_before_landing=True, min_measured_ms=None)

    assert got == measure(gaze, _speeds(), start, stop, 500.0)


@pytest.mark.parametrize("landing", [True, False])
@pytest.mark.parametrize("fs_hz, measured_from", [(498.55, 5), (500.0, 5), (1000.0, 10)])
def test_a_run_shorter_than_the_floor_is_not_measured(landing, fs_hz, measured_from):
    """10 ms or more is measured, `(stop - start) / fs_hz >= 0.010`. At the
    rig's 498.55 Hz that blanks runs of 4 samples or fewer (8.0 ms) and
    measures 5 or more (10.03 ms); at 500 Hz, 5 samples is exactly 10 ms, and
    measured."""
    gaze = _walk()
    for length in range(1, 13):
        got = measure_event_run(gaze, _speeds(), _offered(40), 10, 10 + length, fs_hz,
                                runs_end_before_landing=landing, min_measured_ms=10.0)
        if length < measured_from:
            assert got is None, length
        else:
            assert got is not None and got.amplitude_deg > 0.0, length
