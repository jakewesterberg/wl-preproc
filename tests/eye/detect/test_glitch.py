"""Tracker glitches: gaze that leaves and returns faster than an eye can move
(the requester's decision of 2026-09-28; handoff
`docs/handoffs/2026-09-28-gaze-glitches.md`).

On the reference recording about 2,000 such one- and two-sample excursions
per eye passed the validity mask as usable. They sat inside 1-20% of each
detector's stored saccades, depending on the detector, and put those
saccades' peak velocities far above the main sequence."""

from __future__ import annotations

import numpy as np
import pytest

FS = 500.0
LIMIT = 1000.0  # deg/s: the validity mask's own `max_speed_deg_s`


def _still(n=40):
    return np.zeros((n, 2))


def _repair(gaze, fs=FS, max_glitch_ms=10.0):
    from wl_preproc.eye.detect.glitch import repair_glitches

    return repair_glitches(gaze, fs, LIMIT, max_glitch_ms)


@pytest.mark.parametrize("width", [1, 2, 4])
def test_a_glitch_is_replaced_by_the_straight_line_across_it(width):
    """Out and back within `width` samples, each jump far above the limit
    (5 deg in one 2 ms sample is 2,500 deg/s). The glitch samples become
    the straight line between the samples either side."""
    gaze = _still()
    gaze[:, 0] = np.arange(40) * 0.01            # a slow drift the line must follow
    glitched = gaze.copy()
    glitched[20:20 + width, 0] += 5.0

    repaired, where = _repair(glitched)

    np.testing.assert_allclose(repaired, gaze, atol=1e-12)
    assert np.flatnonzero(where).tolist() == list(range(20, 20 + width))


def test_an_excursion_lasting_ten_ms_or_more_is_not_a_glitch():
    """Five samples at 500 Hz is 10 ms, as long as the shortest saccade two
    registered detectors accept: no longer ruled out as an eye movement."""
    gaze = _still()
    gaze[20:25, 0] = 5.0

    repaired, where = _repair(gaze)

    np.testing.assert_array_equal(repaired, gaze)
    assert not where.any()


def test_the_ten_ms_limit_is_counted_at_the_recordings_own_rate():
    """At 1000 Hz, 9 samples is 9 ms and a glitch; 10 samples is not."""
    for width, expected in ((9, True), (10, False)):
        gaze = _still(60)
        gaze[20:20 + width, 0] = 5.0
        _repaired, where = _repair(gaze, fs=1000.0)
        assert where.any() == expected, width


def test_a_single_fast_jump_that_stays_is_not_a_glitch():
    """A step with no return is not repaired: most lone jumps over the limit
    on the reference recording sat inside real saccades."""
    gaze = _still()
    gaze[20:, 0] = 5.0

    repaired, where = _repair(gaze)

    np.testing.assert_array_equal(repaired, gaze)
    assert not where.any()


def test_an_out_and_back_slower_than_the_limit_is_left_alone():
    """1.5 deg in 2 ms is 750 deg/s: fast, but not faster than an eye."""
    gaze = _still()
    gaze[20, 0] = 1.5

    repaired, where = _repair(gaze)

    np.testing.assert_array_equal(repaired, gaze)
    assert not where.any()


def test_two_fast_jumps_the_same_way_are_not_a_glitch():
    """Out and further out is not out and back."""
    gaze = _still()
    gaze[20, 0] = 5.0
    gaze[21:, 0] = 10.0

    repaired, where = _repair(gaze)

    np.testing.assert_array_equal(repaired, gaze)
    assert not where.any()


def test_a_glitch_beside_a_missing_sample_is_left_to_the_mask():
    """With no finite sample on one side there is no line to draw; the
    validity mask's `non_finite` criterion owns that stretch."""
    gaze = _still()
    gaze[20, 0] = 5.0
    gaze[21] = np.nan

    repaired, where = _repair(gaze)

    np.testing.assert_array_equal(repaired, gaze)
    assert not where.any()


def test_the_input_gaze_is_not_modified():
    gaze = _still()
    gaze[20, 0] = 5.0
    before = gaze.copy()

    _repair(gaze)

    np.testing.assert_array_equal(gaze, before)


def test_the_default_validity_params_set_the_glitch_limit():
    """Pinned like the mask's other placeholders, so changing it is a
    visible decision."""
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS

    assert DEFAULT_VALIDITY_PARAMS.max_glitch_ms == 10.0
