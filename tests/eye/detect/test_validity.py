import numpy as np
import pytest

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.validity import (
    DEFAULT_VALIDITY_PARAMS, ValidityParams, validity_labels,
)

FS_HZ = 500.0
_QUIET = ValidityParams(
    region_half_width_deg=20.0, region_half_height_deg=15.0,
    max_speed_deg_s=1000.0, dilate_samples=0, min_epoch_samples=1,
)


def _clean(n):
    return np.zeros((n, 2)), np.zeros((n, 2)), np.full(n, 100.0), ()


def test_a_clean_recording_has_every_sample_available():
    gaze, vel, quality, gaps = _clean(50)
    assert all(label is None for label in validity_labels(gaze, vel, quality, gaps, _QUIET))


def test_data_quality_below_one_hundred_is_a_blink():
    """Criterion 1, and it reuses `EyeQuality`'s existing definition exactly --
    a second blink definition free to drift from the one the daily report
    already publishes is the defect this repository names most often."""
    gaze, vel, quality, gaps = _clean(10)
    quality[3:6] = 50.0
    quality[7] = 0.0

    labels = validity_labels(gaze, vel, quality, gaps, _QUIET)

    assert [labels[i] for i in (3, 4, 5, 7)] == [Label.BLINK] * 4
    assert labels[2] is None and labels[6] is None


def test_gaze_outside_the_plausible_region_is_invalid():
    gaze, vel, quality, gaps = _clean(10)
    gaze[4] = [25.0, 0.0]      # beyond 20 deg half-width
    gaze[6] = [0.0, -18.0]     # beyond 15 deg half-height

    labels = validity_labels(gaze, vel, quality, gaps, _QUIET)

    assert labels[4] is Label.INVALID and labels[6] is Label.INVALID
    assert labels[5] is None


def test_implausible_speed_is_invalid():
    gaze, vel, quality, gaps = _clean(10)
    vel[5] = [1200.0, 0.0]

    assert validity_labels(gaze, vel, quality, gaps, _QUIET)[5] is Label.INVALID


def test_a_frame_gap_invalidates_the_samples_either_side_of_it():
    """Criterion 4, and the reason `read_ohdpi` reports `frame_gaps` instead of
    refusing a recording: a velocity computed ACROSS a gap is a spurious
    saccade, so the samples whose estimate spans the discontinuity are the
    ones that must go."""
    from wl_preproc.eye.ohdpi import FrameGap

    gaze, vel, quality, _ = _clean(20)
    labels = validity_labels(gaze, vel, quality, (FrameGap(row=9, n_missing=3),), _QUIET)

    assert labels[9] is Label.INVALID and labels[10] is Label.INVALID
    assert labels[6] is None and labels[13] is None


def test_blink_wins_over_invalid_when_a_sample_qualifies_for_both():
    """Precedence, enforced where the labels are assigned rather than trusted
    to a downstream reader."""
    gaze, vel, quality, gaps = _clean(6)
    quality[2] = 0.0
    gaze[2] = [99.0, 99.0]

    assert validity_labels(gaze, vel, quality, gaps, _QUIET)[2] is Label.BLINK


def test_invalid_regions_are_dilated_by_the_stated_number_of_samples():
    """The notebook's fifth criterion. A tracking failure does not begin and
    end cleanly on the sample the tracker admits it."""
    params = ValidityParams(20.0, 15.0, 1000.0, dilate_samples=2, min_epoch_samples=1)
    gaze, vel, quality, gaps = _clean(20)
    quality[10] = 0.0

    labels = validity_labels(gaze, vel, quality, gaps, params)

    assert all(labels[i] is not None for i in range(8, 13))
    assert labels[7] is None and labels[13] is None


def test_a_valid_epoch_shorter_than_the_minimum_is_dropped():
    """Also the fifth criterion. Three valid samples between two blinks cannot
    support a detector and would produce edge artifacts if handed to one."""
    params = ValidityParams(20.0, 15.0, 1000.0, dilate_samples=0, min_epoch_samples=5)
    gaze, vel, quality, gaps = _clean(20)
    quality[0:8] = 0.0
    quality[11:20] = 0.0        # leaves a 3-sample valid epoch at 8..10

    labels = validity_labels(gaze, vel, quality, gaps, params)

    assert [labels[i] for i in (8, 9, 10)] == [Label.INVALID] * 3


def test_a_dropped_short_epoch_is_invalid_not_blink():
    """It was dropped for being short, not for a tracking failure, and the two
    reasons must not render identically."""
    params = ValidityParams(20.0, 15.0, 1000.0, dilate_samples=0, min_epoch_samples=5)
    gaze, vel, quality, gaps = _clean(12)
    quality[0:4] = 0.0
    quality[6:12] = 0.0

    labels = validity_labels(gaze, vel, quality, gaps, params)

    assert labels[4] is Label.INVALID and labels[0] is Label.BLINK


def test_the_defaults_are_stated_and_flagged_as_unmeasured():
    """Design spec section 11 open question 1: the region and speed ceiling
    have no measured value for this rig yet. Pinned so a later measurement is
    a visible change rather than a silent drift."""
    assert DEFAULT_VALIDITY_PARAMS.region_half_width_deg == 20.0
    assert DEFAULT_VALIDITY_PARAMS.max_speed_deg_s == 1000.0


def test_a_short_valid_epoch_at_the_end_of_the_recording_is_dropped():
    """The loop's final-index boundary: when a valid epoch ends at the very end
    of the recording (not followed by invalid samples), it is still checked
    against the minimum epoch length and dropped if too short."""
    params = ValidityParams(20.0, 15.0, 1000.0, dilate_samples=0, min_epoch_samples=5)
    gaze, vel, quality, gaps = _clean(10)
    quality[0:7] = 0.0  # leaves a 3-sample valid epoch at 7..9 at the end

    labels = validity_labels(gaze, vel, quality, gaps, params)

    assert [labels[i] for i in (7, 8, 9)] == [Label.INVALID] * 3
    assert labels[6] is Label.BLINK
