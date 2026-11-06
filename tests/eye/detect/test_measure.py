import math

import numpy as np
import pytest

from wl_preproc.eye.detect import geometry
from wl_preproc.eye.detect.geometry import PER_AXIS, TRUE_ANGLE
from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.measure import (
    MICROSACCADE_MAX_DEG, amplitude, classify, measure, measure_event_run, measure_positions,
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

    result = measure(gaze, velocity, start=0, stop=5, fs_hz=500.0, geometry=PER_AXIS)

    assert result.amplitude_deg == pytest.approx(4.0)


def test_amplitude_is_euclidean_across_both_axes():
    gaze = np.array([[0.0, 0.0], [3.0, 4.0]])
    result = measure(gaze, np.zeros((2, 2)), start=0, stop=2, fs_hz=500.0, geometry=PER_AXIS)
    assert result.amplitude_deg == pytest.approx(5.0)


def test_peak_velocity_is_the_maximum_speed_inside_the_interval_only():
    """Bounded to the interval: a faster sample just outside it belongs to a
    different event, and letting it leak in would inflate the main sequence
    that vigor is measured against (design spec section 6.5)."""
    velocity = np.zeros((10, 2))
    velocity[2] = [50.0, 0.0]
    velocity[4] = [80.0, 60.0]      # speed 100, inside
    velocity[8] = [400.0, 0.0]      # outside

    result = measure(np.zeros((10, 2)), velocity, start=1, stop=6, fs_hz=500.0, geometry=PER_AXIS)

    assert result.peak_velocity_deg_s == pytest.approx(100.0)


def test_duration_counts_samples_not_endpoints():
    """`stop` is exclusive, so a 6-sample event at 500 Hz lasts 12 ms."""
    result = measure(np.zeros((20, 2)), np.zeros((20, 2)), start=4, stop=10, fs_hz=500.0, geometry=PER_AXIS)
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
        measure(np.zeros((5, 2)), np.zeros((5, 2)), start=2, stop=2, fs_hz=500.0, geometry=PER_AXIS)
    with pytest.raises(ValueError, match="stop > start.*start=5.*stop=3"):
        measure(np.zeros((5, 2)), np.zeros((5, 2)), start=5, stop=3, fs_hz=500.0, geometry=PER_AXIS)


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
                            runs_end_before_landing=False, min_measured_ms=None, geometry=PER_AXIS)

    assert got == measure(gaze, velocity, start, stop, 500.0, geometry=PER_AXIS)


def test_a_run_ending_before_its_landing_is_measured_to_the_landing_sample():
    """Amplitude to `gaze[stop]`, peak velocity over `[start, stop]`
    inclusive, and not a sample further."""
    gaze = _walk()
    velocity = _speeds()
    velocity[16] = [300.0, 0.0]   # the landing sample: inside
    velocity[17] = [900.0, 0.0]   # the sample after it: outside

    got = measure_event_run(gaze, velocity, _offered(40), 10, 16, 500.0,
                            runs_end_before_landing=True, min_measured_ms=None, geometry=PER_AXIS)

    displacement = gaze[16] - gaze[10]
    assert got.amplitude_deg == float(np.hypot(displacement[0], displacement[1]))
    assert got.amplitude_deg != measure(gaze, velocity, 10, 16, 500.0, geometry=PER_AXIS).amplitude_deg
    assert got.peak_velocity_deg_s == 300.0
    assert got.duration_s == 6 / 500.0


def test_a_one_sample_run_reads_its_whole_step_rather_than_zero():
    """C1's worst case: `measure` reads a one-sample run as exactly 0.0."""
    gaze = _walk()

    got = measure_event_run(gaze, _speeds(), _offered(40), 10, 11, 500.0,
                            runs_end_before_landing=True, min_measured_ms=None, geometry=PER_AXIS)

    assert measure(gaze, _speeds(), 10, 11, 500.0, geometry=PER_AXIS).amplitude_deg == 0.0
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
                            runs_end_before_landing=True, min_measured_ms=None, geometry=PER_AXIS)

    assert got == measure(gaze, _speeds(), start, stop, 500.0, geometry=PER_AXIS)


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
                                runs_end_before_landing=landing, min_measured_ms=10.0, geometry=PER_AXIS)
        if length < measured_from:
            assert got is None, length
        else:
            assert got is not None and got.amplitude_deg > 0.0, length


# The requester's decision of 2026-09-27 (design spec `2026-09-27-bmd-design.md`
# section 3.5): BMD's per-eye microsaccade runs start one sample after the eye
# takes off, and are measured from that take-off sample.


def test_a_run_starting_after_its_takeoff_is_measured_from_the_takeoff_sample():
    """Amplitude from `gaze[start - 1]`, peak velocity over `[start - 1,
    stop)`, and not a sample earlier. The duration is the run's own."""
    gaze = _walk()
    velocity = _speeds()
    velocity[9] = [300.0, 0.0]   # the take-off sample: inside
    velocity[8] = [900.0, 0.0]   # the sample before it: outside

    got = measure_event_run(gaze, velocity, _offered(40), 10, 16, 500.0,
                            runs_end_before_landing=False, min_measured_ms=None,
                            runs_start_after_takeoff=True, geometry=PER_AXIS)

    displacement = gaze[15] - gaze[9]
    assert got.amplitude_deg == float(np.hypot(displacement[0], displacement[1]))
    assert got.amplitude_deg != measure(gaze, velocity, 10, 16, 500.0, geometry=PER_AXIS).amplitude_deg
    assert got.peak_velocity_deg_s == 300.0
    assert got.duration_s == 6 / 500.0


def test_a_one_sample_run_reads_its_take_off_step_rather_than_zero():
    gaze = _walk()

    got = measure_event_run(gaze, _speeds(), _offered(40), 10, 11, 500.0,
                            runs_end_before_landing=False, min_measured_ms=None,
                            runs_start_after_takeoff=True, geometry=PER_AXIS)

    assert measure(gaze, _speeds(), 10, 11, 500.0, geometry=PER_AXIS).amplitude_deg == 0.0
    displacement = gaze[10] - gaze[9]
    assert got.amplitude_deg == float(np.hypot(displacement[0], displacement[1])) > 0.0


@pytest.mark.parametrize("case", ["start is the first sample", "start - 1 is withheld", "start - 1 is not finite"])
def test_the_takeoff_sample_is_used_only_when_it_was_offered(case):
    """Otherwise the run is measured as `measure` measures it."""
    gaze = _walk()
    offered = _offered(40)
    start, stop = 10, 16
    if case == "start is the first sample":
        start, stop = 0, 6
    elif case == "start - 1 is withheld":
        offered[start - 1] = Label.BLINK
    else:
        gaze[start - 1, 0] = np.nan

    got = measure_event_run(gaze, _speeds(), offered, start, stop, 500.0,
                            runs_end_before_landing=False, min_measured_ms=None,
                            runs_start_after_takeoff=True, geometry=PER_AXIS)

    assert got == measure(gaze, _speeds(), start, stop, 500.0, geometry=PER_AXIS)


# -- Saccade geometry (design spec `2026-09-28-saccade-geometry-design.md`) -----
#
# Every measurement carries the gaze at the two samples its amplitude reads,
# and the direction from one to the other: 0 deg rightward, 90 deg upward.


@pytest.mark.parametrize("dx, dy, direction", [
    (1.0, 0.0, 0.0), (0.0, 1.0, 90.0), (-1.0, 0.0, 180.0), (0.0, -1.0, 270.0), (1.0, 1.0, 45.0), (1.0, -1.0, 315.0),
])
def test_the_direction_is_counterclockwise_from_rightward_in_degrees(dx, dy, direction):
    """Spec 1.2: 0 deg is rightward and 90 deg upward, in the calibrated
    frame (positive x rightward, positive y upward); in [0, 360), so straight
    down is 270, never -90 (true-angle spec, amendment 1)."""
    gaze = np.zeros((5, 2))
    gaze[2:] = [dx, dy]
    got = measure(gaze, np.zeros((5, 2)), 1, 4, 500.0, geometry=PER_AXIS)
    assert got.direction_deg == pytest.approx(direction, abs=1e-12)


def test_the_positions_are_the_two_samples_the_amplitude_reads():
    """Spec 1.1: start `gaze[start]`, end `gaze[stop - 1]`, and the amplitude
    is exactly the distance between them."""
    gaze = _walk()
    got = measure(gaze, _speeds(), 10, 16, 500.0, geometry=PER_AXIS)
    assert (got.start_x_deg, got.start_y_deg) == tuple(gaze[10])
    assert (got.end_x_deg, got.end_y_deg) == tuple(gaze[15])
    assert got.amplitude_deg == float(np.hypot(got.end_x_deg - got.start_x_deg, got.end_y_deg - got.start_y_deg))
    assert got.direction_deg == float(geometry.wrap_360_deg(
        math.degrees(math.atan2(gaze[15, 1] - gaze[10, 1], gaze[15, 0] - gaze[10, 0]))))


@pytest.mark.parametrize("rule, first, last", [("landing", 10, 16), ("take-off", 9, 15)])
def test_each_detectors_rule_moves_the_positions_with_the_amplitude(rule, first, last):
    """Spec 1.1: NSLR's landing rule ends at `gaze[stop]`; BMD's take-off rule
    starts at `gaze[start - 1]`. The positions follow the same samples."""
    gaze = _walk()
    got = measure_event_run(gaze, _speeds(), _offered(40), 10, 16, 500.0,
                            runs_end_before_landing=rule == "landing", min_measured_ms=None,
                            runs_start_after_takeoff=rule == "take-off", geometry=PER_AXIS)
    assert (got.start_x_deg, got.start_y_deg) == tuple(gaze[first])
    assert (got.end_x_deg, got.end_y_deg) == tuple(gaze[last])
    assert got.amplitude_deg == float(np.hypot(got.end_x_deg - got.start_x_deg, got.end_y_deg - got.start_y_deg))
    assert got.duration_s == 6 / 500.0


def test_a_zero_displacement_has_positions_and_no_direction():
    """Spec 1.2: where start equals end, the direction is NULL, never 0 deg."""
    gaze = np.full((5, 2), 2.5)
    got = measure(gaze, np.zeros((5, 2)), 0, 5, 500.0, geometry=PER_AXIS)
    assert got.amplitude_deg == 0.0
    assert got.direction_deg is None
    assert (got.start_x_deg, got.start_y_deg, got.end_x_deg, got.end_y_deg) == (2.5, 2.5, 2.5, 2.5)


# -- True visual angle (design spec `2026-10-30-true-angle-saccades-design.md`) --


def _step(start, end, n=10, at=5):
    gaze = np.tile(np.asarray(start, dtype=float), (n, 1))
    gaze[at:] = end
    return gaze


@pytest.mark.parametrize(("start", "end", "true", "per_axis"), [
    ((20.0, 0.0), (20.0, 2.0), 1.879, 2.0),
    ((30.0, 0.0), (30.0, 2.0), 1.732, 2.0),
    ((0.0, 0.0), (8.0, 0.0), 8.0, 8.0),
])
def test_the_size_is_the_setting_s(start, end, true, per_axis):
    """Section 4: under true angle, the angle between the two samples'
    directions; under per-axis, hypot, as v1."""
    gaze = _step(start, end)
    v = np.zeros_like(gaze)
    assert measure(gaze, v, 0, 10, 500.0, geometry=TRUE_ANGLE).amplitude_deg == pytest.approx(true, abs=5e-4)
    assert measure(gaze, v, 0, 10, 500.0, geometry=PER_AXIS).amplitude_deg == pytest.approx(per_axis, rel=1e-15)


def test_size_is_one_function():
    """What a detector reads mid-detection is what is stored (decision 3)."""
    gaze = _step((30.0, 0.0), (31.0, 1.0))
    for setting in (TRUE_ANGLE, PER_AXIS):
        assert amplitude(gaze, 0, 10, geometry=setting) == \
            measure(gaze, np.zeros_like(gaze), 0, 10, 500.0, geometry=setting).amplitude_deg


def test_under_true_angle_the_direction_is_the_eyes_at_its_start():
    """Section 4: atan2 of the end's local coordinates about the start. A
    step straight up off centre is 90 under both; an oblique one bends."""
    up = _step((30.0, 0.0), (30.0, 2.0))
    assert measure(up, np.zeros_like(up), 0, 10, 500.0, geometry=TRUE_ANGLE).direction_deg == \
        pytest.approx(90.0, abs=1e-9)
    oblique = _step((30.0, 20.0), (32.0, 22.0))
    lx, ly = geometry.local_coordinates_deg(geometry.directions(np.array([32.0, 22.0])),
                                            geometry.directions(np.array([30.0, 20.0])))
    got = measure(oblique, np.zeros_like(oblique), 0, 10, 500.0, geometry=TRUE_ANGLE).direction_deg
    assert got == float(geometry.wrap_360_deg(np.degrees(np.arctan2(ly, lx))))
    assert abs(got - 45.0) > 1.0


def test_under_true_angle_a_downward_step_is_270():
    """On the horizontal meridian, where the screen's vertical is the eye's;
    above or below it, a screen-vertical step leans in the eye's frame."""
    gaze = _step((20.0, 0.0), (20.0, -2.0))
    assert measure(gaze, np.zeros_like(gaze), 0, 10, 500.0, geometry=TRUE_ANGLE).direction_deg == \
        pytest.approx(270.0, abs=1e-9)


def test_a_zero_displacement_has_no_direction_under_true_angle_either():
    gaze = np.full((5, 2), 12.5)
    got = measure(gaze, np.zeros((5, 2)), 0, 5, 500.0, geometry=TRUE_ANGLE)
    assert got.amplitude_deg == 0.0
    assert got.direction_deg is None


def test_near_the_centre_the_settings_agree():
    """Section 11: within the error of the small-angle approximation."""
    gaze = _step((0.1, -0.2), (0.4, 0.1))
    v = np.zeros_like(gaze)
    true, per_axis = (measure(gaze, v, 0, 10, 500.0, geometry=g) for g in (TRUE_ANGLE, PER_AXIS))
    assert true.amplitude_deg == pytest.approx(per_axis.amplitude_deg, rel=1e-5)
    assert true.direction_deg == pytest.approx(per_axis.direction_deg, abs=1e-3)


@pytest.mark.parametrize("setting", [TRUE_ANGLE, PER_AXIS])
def test_the_endpoints_carry_their_eccentricity_and_polar_angle_under_both(setting):
    """Facts of the positions, the same under either setting; the polar angle
    is undefined at the centre ("Empty at center")."""
    gaze = _step((0.0, 0.0), (0.0, -10.0))
    got = measure(gaze, np.zeros_like(gaze), 0, 10, 500.0, geometry=setting)
    assert (got.start_eccentricity_deg, got.start_polar_deg) == (0.0, None)
    assert got.end_eccentricity_deg == pytest.approx(10.0, rel=1e-14)
    assert got.end_polar_deg == pytest.approx(270.0, abs=1e-12)


def test_the_peak_speed_is_the_largest_magnitude_of_the_velocity_it_is_handed():
    """Under true angle the caller hands it the local-frame velocity
    (section 5); the magnitude is the same formula under both."""
    v = np.zeros((10, 2))
    v[4] = [30.0, 40.0]
    for setting in (TRUE_ANGLE, PER_AXIS):
        assert measure(np.zeros((10, 2)), v, 0, 10, 500.0, geometry=setting).peak_velocity_deg_s == 50.0


@pytest.mark.parametrize("setting", [TRUE_ANGLE, PER_AXIS])
def test_a_fixation_is_measured_for_its_positions_only(setting):
    """Decision 5: its positions, their eccentricity and polar angle, and its
    direction in the setting, as an event's over the same samples; no size
    and no peak speed."""
    gaze = _step((30.0, 20.0), (30.3, 20.3))
    got = measure_positions(gaze, 2, 9, 500.0, geometry=setting)
    event = measure(gaze, np.zeros_like(gaze), 2, 9, 500.0, geometry=setting)
    assert (got.amplitude_deg, got.peak_velocity_deg_s) == (None, None)
    assert (got.start_x_deg, got.start_y_deg, got.end_x_deg, got.end_y_deg) == (30.0, 20.0, 30.3, 20.3)
    assert got.direction_deg == event.direction_deg
    assert (got.start_eccentricity_deg, got.end_polar_deg) == (event.start_eccentricity_deg, event.end_polar_deg)
    assert got.duration_s == 7 / 500.0


def test_a_fixations_direction_bends_off_centre_under_true_angle():
    gaze = _step((30.0, 20.0), (30.3, 20.3))
    true, per_axis = (measure_positions(gaze, 2, 9, 500.0, geometry=g).direction_deg for g in (TRUE_ANGLE, PER_AXIS))
    assert per_axis == pytest.approx(45.0, abs=1e-9) and abs(true - per_axis) > 1.0


def test_a_one_sample_fixation_has_no_direction():
    got = measure_positions(np.array([[1.0, 2.0]]), 0, 1, 500.0, geometry=TRUE_ANGLE)
    assert got.direction_deg is None


def test_an_unknown_geometry_is_refused():
    with pytest.raises(ValueError, match="geometry"):
        amplitude(np.zeros((5, 2)), 0, 5, geometry="cyclopean")


def test_a_runs_peak_speed_is_its_measured_samples_alone():
    """Design spec `2026-11-06-eye-lost-data-and-region-design.md` section 6:
    a lost sample's speed is not a measurement, so it is never the peak; the
    run is flagged instead."""
    velocity = _speeds()
    velocity[12, 0] = 900.0
    velocity[14, 0] = 300.0
    offered = _offered(40)
    offered[12] = Label.LOST

    got = measure_event_run(np.zeros((40, 2)), velocity, offered, 10, 16, 500.0, geometry=PER_AXIS,
                            runs_end_before_landing=False, min_measured_ms=None)

    assert got.peak_velocity_deg_s == 300.0


def test_a_run_with_lost_ends_is_measured_out_to_the_measured_samples_beyond_them():
    """A detector that ran on the filled trace may end a run in a loss, where
    the eye landed: its size reaches the first measured sample after it, and
    its start the last before (design spec
    `2026-11-06-eye-lost-data-and-region-design.md`, the correction of
    2026-11-06)."""
    gaze = np.column_stack([np.arange(40, dtype=float), np.zeros(40)])
    offered = _offered(40)
    offered[[10, 15]] = Label.LOST

    got = measure_event_run(gaze, _speeds(), offered, 10, 16, 500.0, geometry=PER_AXIS,
                            runs_end_before_landing=False, min_measured_ms=None)

    assert (got.start_x_deg, got.end_x_deg, got.amplitude_deg) == (9.0, 16.0, 7.0)
