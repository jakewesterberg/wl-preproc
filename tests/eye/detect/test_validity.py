import numpy as np

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
    assert all(label is None for label in validity_labels(gaze, vel, quality, gaps, _QUIET).labels)


def test_data_quality_below_one_hundred_is_a_blink():
    """Criterion 1, and it reuses `EyeQuality`'s existing definition exactly --
    a second blink definition free to drift from the one the daily report
    already publishes is the defect this repository names most often."""
    gaze, vel, quality, gaps = _clean(10)
    quality[3:6] = 50.0
    quality[7] = 0.0

    labels = validity_labels(gaze, vel, quality, gaps, _QUIET).labels

    assert [labels[i] for i in (3, 4, 5, 7)] == [Label.BLINK] * 4
    assert labels[2] is None and labels[6] is None


def test_gaze_outside_the_plausible_region_is_invalid():
    gaze, vel, quality, gaps = _clean(10)
    gaze[4] = [25.0, 0.0]      # beyond 20 deg half-width
    gaze[6] = [0.0, -18.0]     # beyond 15 deg half-height

    labels = validity_labels(gaze, vel, quality, gaps, _QUIET).labels

    assert labels[4] is Label.INVALID and labels[6] is Label.INVALID
    assert labels[5] is None


def test_implausible_speed_is_invalid():
    gaze, vel, quality, gaps = _clean(10)
    vel[5] = [1200.0, 0.0]

    assert validity_labels(gaze, vel, quality, gaps, _QUIET).labels[5] is Label.INVALID


def test_a_frame_gap_invalidates_the_samples_either_side_of_it():
    """Criterion 4, and the reason `read_ohdpi` reports `frame_gaps` instead of
    refusing a recording: a velocity computed ACROSS a gap is a spurious
    saccade, so the samples whose estimate spans the discontinuity are the
    ones that must go. With a 5-point velocity estimator, a gap at row r
    corrupts estimates at indices r-1, r, r+1, r+2."""
    from wl_preproc.eye.ohdpi import FrameGap

    gaze, vel, quality, _ = _clean(25)
    labels = validity_labels(gaze, vel, quality, (FrameGap(row=9, n_missing=3),), _QUIET).labels

    # Gap between rows 9 and 10 corrupts velocity at indices 8, 9, 10, 11
    assert all(labels[i] is Label.INVALID for i in range(8, 12))
    # Samples before and after are available
    assert labels[7] is None and labels[12] is None


def test_blink_wins_over_invalid_when_a_sample_qualifies_for_both():
    """Precedence, enforced where the labels are assigned rather than trusted
    to a downstream reader."""
    gaze, vel, quality, gaps = _clean(6)
    quality[2] = 0.0
    gaze[2] = [99.0, 99.0]

    assert validity_labels(gaze, vel, quality, gaps, _QUIET).labels[2] is Label.BLINK


def test_mask_precedence_is_what_decides_which_criterion_wins(monkeypatch):
    """`MASK_PRECEDENCE` is operative, not decorative -- reversing it reverses
    the answer above.

    A constant asserted against itself (`MASK_PRECEDENCE.index(BLINK) <
    MASK_PRECEDENCE.index(INVALID)`) is what let its eight-label predecessor
    in `labels.py` sit unread while looking alive. This runs the real
    `validity_labels` with the tuple reversed and checks the OUTPUT flips, so
    an implementation that ignored the constant and hard-coded the order
    would fail here rather than pass everything.

    The trace below is claimed by two criteria at once -- below full tracking
    quality AND outside the plausible region -- which is the only situation
    in which any ordering is consulted at all.
    """
    from wl_preproc.eye.detect import validity as validity_module

    gaze, vel, quality, gaps = _clean(6)
    quality[2] = 0.0
    gaze[2] = [99.0, 99.0]

    monkeypatch.setattr(
        validity_module, "MASK_PRECEDENCE", tuple(reversed(validity_module.MASK_PRECEDENCE))
    )

    assert validity_labels(gaze, vel, quality, gaps, _QUIET).labels[2] is Label.INVALID
    # Samples claimed by only one criterion are unaffected either way: the
    # ordering decides a contest, and there is no contest here.
    assert validity_labels(gaze, vel, quality, gaps, _QUIET).labels[0] is None


def test_invalid_regions_are_dilated_by_the_stated_number_of_samples():
    """The notebook's fifth criterion. A tracking failure does not begin and
    end cleanly on the sample the tracker admits it. A blink is a specific
    reason (tracked failure), and the dilated halo must be marked INVALID
    (generic reason), not BLINK."""
    params = ValidityParams(20.0, 15.0, 1000.0, dilate_samples=2, min_epoch_samples=1)
    gaze, vel, quality, gaps = _clean(20)
    quality[10] = 0.0

    labels = validity_labels(gaze, vel, quality, gaps, params).labels

    # Center sample 10 is BLINK; dilated halo samples 8, 9, 11, 12 are INVALID
    assert labels[10] is Label.BLINK
    assert all(labels[i] is Label.INVALID for i in [8, 9, 11, 12])
    assert labels[7] is None and labels[13] is None


def test_a_valid_epoch_shorter_than_the_minimum_is_dropped():
    """Also the fifth criterion. Three valid samples between two blinks cannot
    support a detector and would produce edge artifacts if handed to one."""
    params = ValidityParams(20.0, 15.0, 1000.0, dilate_samples=0, min_epoch_samples=5)
    gaze, vel, quality, gaps = _clean(20)
    quality[0:8] = 0.0
    quality[11:20] = 0.0        # leaves a 3-sample valid epoch at 8..10

    labels = validity_labels(gaze, vel, quality, gaps, params).labels

    assert [labels[i] for i in (8, 9, 10)] == [Label.INVALID] * 3


def test_a_dropped_short_epoch_is_invalid_not_blink():
    """It was dropped for being short, not for a tracking failure, and the two
    reasons must not render identically."""
    params = ValidityParams(20.0, 15.0, 1000.0, dilate_samples=0, min_epoch_samples=5)
    gaze, vel, quality, gaps = _clean(12)
    quality[0:4] = 0.0
    quality[6:12] = 0.0

    labels = validity_labels(gaze, vel, quality, gaps, params).labels

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

    labels = validity_labels(gaze, vel, quality, gaps, params).labels

    assert [labels[i] for i in (7, 8, 9)] == [Label.INVALID] * 3
    assert labels[6] is Label.BLINK


def _fractions(gaze, vel, quality, gaps, params):
    """Just the five per-criterion fractions, keyed by criterion name."""
    return validity_labels(gaze, vel, quality, gaps, params).fractions


_CRITERIA = ("blink", "out_of_region", "too_fast", "frame_gap", "short_epoch")


def test_the_mask_reports_a_fraction_for_every_one_of_the_five_criteria():
    """Design spec section 7 asks `EyeValidity` for "per-criterion rejected
    fractions", plural. Four of the five were hardcoded `None` at the schema
    for want of a return value that existed all along (finding M6).

    Pins the KEY SET exactly, not merely that some keys are present: the
    schema builds its five column names as `frac_` + these names, in one
    spread, so a criterion silently renamed here would be an insert-time
    failure there and a criterion silently dropped would be a column that
    goes back to `NULL` with nothing else complaining.
    """
    gaze, vel, quality, gaps = _clean(20)

    assert set(_fractions(gaze, vel, quality, gaps, _QUIET)) == set(_CRITERIA)


def _only(criterion: str, fractions: dict) -> None:
    """`criterion` rejected something and the other four rejected nothing.

    The negative half is the whole point. Five fractions fed from one place
    is the classic shape for a copy-paste error that no test catches,
    because every column still looks populated -- so each criterion below is
    exercised ALONE, and every other criterion is asserted to be exactly
    zero rather than merely different.
    """
    assert fractions[criterion] > 0.0, f"{criterion} rejected nothing"
    for other in _CRITERIA:
        if other != criterion:
            assert fractions[other] == 0.0, (
                f"{criterion} alone was planted, but {other} reports "
                f"{fractions[other]} -- a fraction is reading the wrong mask"
            )


def test_only_the_blink_fraction_moves_when_only_the_tracker_flagged_trouble():
    gaze, vel, quality, gaps = _clean(20)
    quality[4:8] = 0.0

    fractions = _fractions(gaze, vel, quality, gaps, _QUIET)

    _only("blink", fractions)
    assert fractions["blink"] == 4 / 20


def test_only_the_out_of_region_fraction_moves_when_gaze_leaves_the_screen():
    gaze, vel, quality, gaps = _clean(20)
    gaze[3] = [25.0, 0.0]       # beyond the 20 deg half-width
    gaze[9] = [0.0, -18.0]      # beyond the 15 deg half-height

    fractions = _fractions(gaze, vel, quality, gaps, _QUIET)

    _only("out_of_region", fractions)
    assert fractions["out_of_region"] == 2 / 20


def test_only_the_too_fast_fraction_moves_when_the_speed_is_implausible():
    gaze, vel, quality, gaps = _clean(20)
    vel[11] = [1200.0, 0.0]

    fractions = _fractions(gaze, vel, quality, gaps, _QUIET)

    _only("too_fast", fractions)
    assert fractions["too_fast"] == 1 / 20


def test_only_the_frame_gap_fraction_moves_when_a_frame_is_missing():
    from wl_preproc.eye.ohdpi import FrameGap

    gaze, vel, quality, _ = _clean(25)

    fractions = _fractions(gaze, vel, quality, (FrameGap(row=9, n_missing=3),), _QUIET)

    _only("frame_gap", fractions)
    # The four velocity estimates the gap corrupts, exactly as
    # `test_a_frame_gap_invalidates_the_samples_either_side_of_it` above
    # asserts them on the labels.
    assert fractions["frame_gap"] == 4 / 25


def test_only_the_short_epoch_fraction_moves_when_a_surviving_epoch_is_too_short():
    """The one criterion that cannot be planted alone, and the docstring says
    so rather than the assertion quietly weakening.

    A short VALID epoch only exists between two rejected stretches, so
    something must reject those stretches first -- here `blink`, whose own
    fraction is therefore non-zero too. What is asserted is the part that a
    copy-paste error would break: `short_epoch` counts the three samples
    dropped for being short, and neither of the two criteria that were never
    triggered reports anything.
    """
    params = ValidityParams(20.0, 15.0, 1000.0, dilate_samples=0, min_epoch_samples=5)
    gaze, vel, quality, gaps = _clean(20)
    quality[0:8] = 0.0
    quality[11:20] = 0.0        # leaves a 3-sample valid epoch at 8..10

    fractions = _fractions(gaze, vel, quality, gaps, params)

    assert fractions["short_epoch"] == 3 / 20
    assert fractions["blink"] == 17 / 20
    assert fractions["out_of_region"] == 0.0
    assert fractions["too_fast"] == 0.0
    assert fractions["frame_gap"] == 0.0


def test_the_fractions_are_raw_per_criterion_counts_and_may_sum_above_one():
    """`ValidityMask`'s stated contract, asserted rather than only written
    down: a sample rejected by two criteria is counted by both.

    A reader who sums the five columns of one `EyeValidity` row and gets
    1.3 is seeing the intended thing. An implementation that instead
    apportioned each sample to one "winning" criterion would pass every
    isolation test above and fail here.
    """
    gaze, vel, quality, gaps = _clean(10)
    quality[0:10] = 0.0          # every sample: the tracker flagged trouble
    gaze[:, 0] = 25.0            # every sample: also outside the region
    vel[:, 0] = 1200.0           # every sample: also implausibly fast

    fractions = _fractions(gaze, vel, quality, gaps, _QUIET)

    assert fractions["blink"] == 1.0
    assert fractions["out_of_region"] == 1.0
    assert fractions["too_fast"] == 1.0
    assert sum(fractions.values()) > 1.0


def test_the_dilation_halo_belongs_to_no_criterion_so_the_five_can_sum_below():
    """The other half of `ValidityMask`'s contract. The four tracking
    criteria are counted BEFORE `_dilate` grows each rejected region, so the
    halo is rejected by the mask and attributed to nothing -- which is the
    honest answer, since no one criterion produced it."""
    params = ValidityParams(20.0, 15.0, 1000.0, dilate_samples=2, min_epoch_samples=1)
    gaze, vel, quality, gaps = _clean(20)
    quality[10] = 0.0

    mask = validity_labels(gaze, vel, quality, gaps, params)
    rejected = sum(label is not None for label in mask.labels) / 20

    # One blink sample, four halo samples rejected around it.
    assert mask.fractions["blink"] == 1 / 20
    assert rejected == 5 / 20
    assert sum(mask.fractions.values()) < rejected
