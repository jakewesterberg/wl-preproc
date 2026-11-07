from dataclasses import asdict

import numpy as np
import pytest

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.validity import (
    DEFAULT_VALIDITY_PARAMS, ValidityParams, validity_labels,
)
from wl_preproc.eye.ohdpi import TrackerLoss

FS_HZ = 500.0
_QUIET = ValidityParams(
    region_half_width_deg=20.0, region_half_height_deg=15.0,
    max_speed_deg_s=1000.0, dilate_samples=0, min_epoch_samples=1,
)


def _no_loss(n):
    """No frame lost: the input every test below plants its loss into."""
    return TrackerLoss(blink=np.zeros(n, dtype=bool), feature_lost=np.zeros(n, dtype=bool),
                       unreadable=np.zeros(n, dtype=bool))


def _clean(n):
    return np.zeros((n, 2)), np.zeros((n, 2)), _no_loss(n), ()


def test_a_clean_recording_has_every_sample_available():
    gaze, vel, loss, gaps = _clean(50)
    assert all(label is None for label in validity_labels(gaze, vel, loss, gaps, _QUIET, fs_hz=FS_HZ).labels)


def test_a_blink_by_the_rule_is_a_blink():
    """Criterion 1 reads `ohdpi.TrackerLoss.blink`, the rule `EyeQuality`
    counts the daily report's blink rate by -- a second blink definition free
    to drift from the one the report publishes is the defect this repository
    names most often. *Until 2026-10-11 both read `DataQuality` below 100,
    which OpenIrisDPI writes as 100 through every blink; true when written.*"""
    gaze, vel, loss, gaps = _clean(10)
    loss.blink[3:6] = True
    loss.blink[7] = True

    labels = validity_labels(gaze, vel, loss, gaps, _QUIET, fs_hz=FS_HZ).labels

    assert [labels[i] for i in (3, 4, 5, 7)] == [Label.BLINK] * 4
    assert labels[2] is None and labels[6] is None


def test_a_lost_feature_is_invalid_not_blink():
    """P1 or P4 at 0 with the pupil found (`TrackerLoss.feature_lost`): the
    gaze vector `CR1 - CR4` is not a position there, but the eye is open, so
    the frame is withheld as `invalid`, never counted as a blink (design spec
    `2026-10-11-eye-loss-design.md` section 3)."""
    gaze, vel, loss, gaps = _clean(10)
    loss.feature_lost[4:6] = True

    labels = validity_labels(gaze, vel, loss, gaps, _QUIET, fs_hz=FS_HZ).labels

    assert labels[4] is Label.INVALID and labels[5] is Label.INVALID
    assert labels[3] is None and labels[6] is None


def test_a_blink_whose_p1_is_lost_too_is_a_blink():
    """Inside a real blink P1 is lost as well, so both criteria claim the
    frame, and `MASK_PRECEDENCE` gives it to `blink`."""
    gaze, vel, loss, gaps = _clean(6)
    loss.blink[2] = True
    loss.feature_lost[2] = True

    assert validity_labels(gaze, vel, loss, gaps, _QUIET, fs_hz=FS_HZ).labels[2] is Label.BLINK


def test_gaze_outside_the_plausible_region_is_invalid():
    gaze, vel, loss, gaps = _clean(10)
    gaze[4] = [25.0, 0.0]      # beyond 20 deg half-width
    gaze[6] = [0.0, -18.0]     # beyond 15 deg half-height

    labels = validity_labels(gaze, vel, loss, gaps, _QUIET, fs_hz=FS_HZ).labels

    assert labels[4] is Label.INVALID and labels[6] is Label.INVALID
    assert labels[5] is None


def test_implausible_speed_is_invalid():
    gaze, vel, loss, gaps = _clean(10)
    vel[5] = [1200.0, 0.0]

    assert validity_labels(gaze, vel, loss, gaps, _QUIET, fs_hz=FS_HZ).labels[5] is Label.INVALID


def test_a_frame_gap_invalidates_the_samples_either_side_of_it():
    """Criterion 4, and the reason `read_ohdpi` reports `frame_gaps` instead of
    refusing a recording: a velocity computed ACROSS a gap is a spurious
    saccade, so the samples whose estimate spans the discontinuity are the
    ones that must go. With a 5-point velocity estimator, a gap at row r
    corrupts estimates at indices r-1, r, r+1, r+2."""
    from wl_preproc.eye.ohdpi import FrameGap

    gaze, vel, loss, _ = _clean(25)
    labels = validity_labels(gaze, vel, loss, (FrameGap(row=9, n_missing=3),), _QUIET, fs_hz=FS_HZ).labels

    # Gap between rows 9 and 10 corrupts velocity at indices 8, 9, 10, 11
    assert all(labels[i] is Label.INVALID for i in range(8, 12))
    # Samples before and after are available
    assert labels[7] is None and labels[12] is None


def test_a_sample_whose_gaze_is_missing_is_invalid():
    """The sixth criterion, a data-integrity guard rather than one of
    OpenIrisDPI's own five. A NaN compares false against every threshold, so
    `abs(NaN) > half-width` never rejected it and a sample with no gaze at
    all was offered to every detector as usable."""
    gaze, vel, loss, gaps = _clean(10)
    gaze[4] = [np.nan, 0.0]
    gaze[6] = [0.0, np.nan]

    labels = validity_labels(gaze, vel, loss, gaps, _QUIET, fs_hz=FS_HZ).labels

    assert labels[4] is Label.INVALID and labels[6] is Label.INVALID
    assert labels[5] is None


def test_a_sample_whose_speed_is_missing_is_invalid():
    gaze, vel, loss, gaps = _clean(10)
    vel[5] = [np.nan, 0.0]

    assert validity_labels(gaze, vel, loss, gaps, _QUIET, fs_hz=FS_HZ).labels[5] is Label.INVALID


def test_an_unreadable_frame_is_invalid_not_blink():
    """A frame whose loss columns the rule could not read (`TrackerLoss.
    unreadable`) was never a blink, and must not become one: `blink` means
    the tracker DECLARED a failure, and a missing value declares nothing. It
    is withheld as `invalid`."""
    gaze, vel, loss, gaps = _clean(10)
    loss.unreadable[5] = True

    labels = validity_labels(gaze, vel, loss, gaps, _QUIET, fs_hz=FS_HZ).labels

    assert labels[5] is Label.INVALID
    assert labels[4] is None and labels[6] is None


def test_one_missing_gaze_sample_also_withholds_the_speeds_it_spoiled():
    """Through the real estimator, with no dilation: one NaN gaze sample
    makes the velocity NaN wherever the 5-point window reaches it -- two
    samples either side, though not the sample itself, whose own estimate
    skips its centre -- and every one of those samples is withheld by this
    criterion itself, not left to `dilate_samples`, which a paramset may set
    below `_HALF_WINDOW`."""
    from wl_preproc.eye.detect.velocity import velocity

    gaze = np.zeros((30, 2))
    gaze[15] = [np.nan, np.nan]
    vel = velocity(gaze, FS_HZ)
    loss = _no_loss(30)

    labels = validity_labels(gaze, vel, loss, (), _QUIET, fs_hz=FS_HZ).labels

    spoiled = [i for i in range(30) if not np.isfinite(vel[i]).all()]
    assert spoiled == [13, 14, 16, 17]
    assert all(labels[i] is Label.INVALID for i in [13, 14, 15, 16, 17])
    assert labels[12] is None and labels[18] is None


def test_blink_wins_over_invalid_when_a_sample_qualifies_for_both():
    """Precedence, enforced where the labels are assigned rather than trusted
    to a downstream reader."""
    gaze, vel, loss, gaps = _clean(6)
    loss.blink[2] = True
    gaze[2] = [99.0, 99.0]

    assert validity_labels(gaze, vel, loss, gaps, _QUIET, fs_hz=FS_HZ).labels[2] is Label.BLINK


def test_mask_precedence_is_what_decides_which_criterion_wins(monkeypatch):
    """`MASK_PRECEDENCE` is operative, not decorative -- reversing it reverses
    the answer above.

    A constant asserted against itself (`MASK_PRECEDENCE.index(BLINK) <
    MASK_PRECEDENCE.index(INVALID)`) is what let its eight-label predecessor
    in `labels.py` sit unread while looking alive. This runs the real
    `validity_labels` with the tuple reversed and checks the OUTPUT flips, so
    an implementation that ignored the constant and hard-coded the order
    would fail here rather than pass everything.

    The trace below is claimed by two criteria at once -- a blink AND outside
    the plausible region -- which is the only situation in which any ordering
    is consulted at all.
    """
    from wl_preproc.eye.detect import validity as validity_module

    gaze, vel, loss, gaps = _clean(6)
    loss.blink[2] = True
    gaze[2] = [99.0, 99.0]

    monkeypatch.setattr(
        validity_module, "MASK_PRECEDENCE", tuple(reversed(validity_module.MASK_PRECEDENCE))
    )

    assert validity_labels(gaze, vel, loss, gaps, _QUIET, fs_hz=FS_HZ).labels[2] is Label.INVALID
    # Samples claimed by only one criterion are unaffected either way: the
    # ordering decides a contest, and there is no contest here.
    assert validity_labels(gaze, vel, loss, gaps, _QUIET, fs_hz=FS_HZ).labels[0] is None


def test_invalid_regions_are_dilated_by_the_stated_number_of_samples():
    """The notebook's fifth criterion. A tracking failure does not begin and
    end cleanly on the sample the tracker admits it. A blink is a specific
    reason (tracked failure), and the dilated halo must be marked INVALID
    (generic reason), not BLINK."""
    params = ValidityParams(20.0, 15.0, 1000.0, dilate_samples=2, min_epoch_samples=1)
    gaze, vel, loss, gaps = _clean(20)
    loss.blink[10] = True

    labels = validity_labels(gaze, vel, loss, gaps, params, fs_hz=FS_HZ).labels

    # Center sample 10 is BLINK; dilated halo samples 8, 9, 11, 12 are INVALID
    assert labels[10] is Label.BLINK
    assert all(labels[i] is Label.INVALID for i in [8, 9, 11, 12])
    assert labels[7] is None and labels[13] is None


def test_a_valid_epoch_shorter_than_the_minimum_is_dropped():
    """Also the fifth criterion. Three valid samples between two blinks cannot
    support a detector and would produce edge artifacts if handed to one."""
    params = ValidityParams(20.0, 15.0, 1000.0, dilate_samples=0, min_epoch_samples=5)
    gaze, vel, loss, gaps = _clean(20)
    loss.blink[0:8] = True
    loss.blink[11:20] = True        # leaves a 3-sample valid epoch at 8..10

    labels = validity_labels(gaze, vel, loss, gaps, params, fs_hz=FS_HZ).labels

    assert [labels[i] for i in (8, 9, 10)] == [Label.INVALID] * 3


def test_a_dropped_short_epoch_is_invalid_not_blink():
    """It was dropped for being short, not for a tracking failure, and the two
    reasons must not render identically."""
    params = ValidityParams(20.0, 15.0, 1000.0, dilate_samples=0, min_epoch_samples=5)
    gaze, vel, loss, gaps = _clean(12)
    loss.blink[0:4] = True
    loss.blink[6:12] = True

    labels = validity_labels(gaze, vel, loss, gaps, params, fs_hz=FS_HZ).labels

    assert labels[4] is Label.INVALID and labels[0] is Label.BLINK


def test_the_defaults_are_stated_and_flagged_as_unmeasured():
    """Design spec section 11 open question 1: the region and speed ceiling
    have no measured value for this rig yet. Pinned so a later measurement is
    a visible change rather than a silent drift."""
    assert DEFAULT_VALIDITY_PARAMS.region_half_width_deg == 20.0
    assert DEFAULT_VALIDITY_PARAMS.max_speed_deg_s == 1000.0
    # Measured, not placeholders (design spec `2026-10-11-blink-padding-design.md`).
    assert DEFAULT_VALIDITY_PARAMS.blink_pad_before_ms == 50.0
    assert DEFAULT_VALIDITY_PARAMS.blink_pad_after_ms == 80.0


def test_a_short_valid_epoch_at_the_end_of_the_recording_is_dropped():
    """The loop's final-index boundary: when a valid epoch ends at the very end
    of the recording (not followed by invalid samples), it is still checked
    against the minimum epoch length and dropped if too short."""
    params = ValidityParams(20.0, 15.0, 1000.0, dilate_samples=0, min_epoch_samples=5)
    gaze, vel, loss, gaps = _clean(10)
    loss.blink[0:7] = True  # leaves a 3-sample valid epoch at 7..9 at the end

    labels = validity_labels(gaze, vel, loss, gaps, params, fs_hz=FS_HZ).labels

    assert [labels[i] for i in (7, 8, 9)] == [Label.INVALID] * 3
    assert labels[6] is Label.BLINK


def _fractions(gaze, vel, loss, gaps, params):
    """Just the per-criterion fractions, keyed by criterion name."""
    return validity_labels(gaze, vel, loss, gaps, params, fs_hz=FS_HZ).fractions


_CRITERIA = ("blink", "feature_lost", "out_of_region", "too_fast", "frame_gap", "short_epoch", "non_finite")


def test_the_mask_reports_a_fraction_for_every_criterion():
    """Design spec section 7 asks `EyeValidity` for "per-criterion rejected
    fractions", plural. Four of the five were hardcoded `None` at the schema
    for want of a return value that existed all along (finding M6). There
    are seven criteria since 2026-10-11: `non_finite` joined OpenIrisDPI's
    five on 2026-09-27 (this test was named for "the five criteria" until
    then), and `feature_lost` on 2026-10-11.

    Pins the KEY SET exactly, not merely that some keys are present: the
    schema builds its column names as `frac_` + these names, in one
    spread, so a criterion silently renamed here would be an insert-time
    failure there and a criterion silently dropped would be a column that
    goes back to `NULL` with nothing else complaining.
    """
    gaze, vel, loss, gaps = _clean(20)

    assert set(_fractions(gaze, vel, loss, gaps, _QUIET)) == set(_CRITERIA)


def _only(criterion: str, fractions: dict) -> None:
    """`criterion` rejected something and every other criterion rejected
    nothing.

    The negative half is the whole point. Seven fractions fed from one place
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
    gaze, vel, loss, gaps = _clean(20)
    loss.blink[4:8] = True

    fractions = _fractions(gaze, vel, loss, gaps, _QUIET)

    _only("blink", fractions)
    assert fractions["blink"] == 4 / 20


def test_only_the_feature_lost_fraction_moves_when_only_a_feature_was_lost():
    gaze, vel, loss, gaps = _clean(20)
    loss.feature_lost[2:5] = True

    fractions = _fractions(gaze, vel, loss, gaps, _QUIET)

    _only("feature_lost", fractions)
    assert fractions["feature_lost"] == 3 / 20


def test_only_the_out_of_region_fraction_moves_when_gaze_leaves_the_screen():
    gaze, vel, loss, gaps = _clean(20)
    gaze[3] = [25.0, 0.0]       # beyond the 20 deg half-width
    gaze[9] = [0.0, -18.0]      # beyond the 15 deg half-height

    fractions = _fractions(gaze, vel, loss, gaps, _QUIET)

    _only("out_of_region", fractions)
    assert fractions["out_of_region"] == 2 / 20


def test_only_the_too_fast_fraction_moves_when_the_speed_is_implausible():
    gaze, vel, loss, gaps = _clean(20)
    vel[11] = [1200.0, 0.0]

    fractions = _fractions(gaze, vel, loss, gaps, _QUIET)

    _only("too_fast", fractions)
    assert fractions["too_fast"] == 1 / 20


def test_only_the_frame_gap_fraction_moves_when_a_frame_is_missing():
    from wl_preproc.eye.ohdpi import FrameGap

    gaze, vel, loss, _ = _clean(25)

    fractions = _fractions(gaze, vel, loss, (FrameGap(row=9, n_missing=3),), _QUIET)

    _only("frame_gap", fractions)
    # The four velocity estimates the gap corrupts, exactly as
    # `test_a_frame_gap_invalidates_the_samples_either_side_of_it` above
    # asserts them on the labels.
    assert fractions["frame_gap"] == 4 / 25


def test_only_the_non_finite_fraction_moves_when_a_value_is_missing():
    """Missing values planted alone -- a gaze, a speed and an unreadable
    loss column, each in its own sample -- and counted once each. The region and speed
    criteria stay at zero: a missing value is not a position outside the
    screen or a speed above the ceiling, and the bookkeeping must not say it
    was."""
    gaze, vel, loss, gaps = _clean(20)
    gaze[3] = [np.nan, 0.0]
    vel[9] = [0.0, np.nan]
    loss.unreadable[14] = True

    fractions = _fractions(gaze, vel, loss, gaps, _QUIET)

    _only("non_finite", fractions)
    assert fractions["non_finite"] == 3 / 20


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
    gaze, vel, loss, gaps = _clean(20)
    loss.blink[0:8] = True
    loss.blink[11:20] = True        # leaves a 3-sample valid epoch at 8..10

    fractions = _fractions(gaze, vel, loss, gaps, params)

    assert fractions["short_epoch"] == 3 / 20
    assert fractions["blink"] == 17 / 20
    assert fractions["out_of_region"] == 0.0
    assert fractions["too_fast"] == 0.0
    assert fractions["frame_gap"] == 0.0
    assert fractions["non_finite"] == 0.0


def test_the_fractions_are_raw_per_criterion_counts_and_may_sum_above_one():
    """`ValidityMask`'s stated contract, asserted rather than only written
    down: a sample rejected by two criteria is counted by both.

    A reader who sums the seven criterion columns of one `EyeValidity` row and gets
    1.3 is seeing the intended thing. An implementation that instead
    apportioned each sample to one "winning" criterion would pass every
    isolation test above and fail here.
    """
    gaze, vel, loss, gaps = _clean(10)
    loss.blink[0:10] = True          # every sample: the tracker flagged trouble
    gaze[:, 0] = 25.0            # every sample: also outside the region
    vel[:, 0] = 1200.0           # every sample: also implausibly fast

    fractions = _fractions(gaze, vel, loss, gaps, _QUIET)

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
    gaze, vel, loss, gaps = _clean(20)
    loss.blink[10] = True

    mask = validity_labels(gaze, vel, loss, gaps, params, fs_hz=FS_HZ)
    rejected = sum(label is not None for label in mask.labels) / 20

    # One blink sample, four halo samples rejected around it.
    assert mask.fractions["blink"] == 1 / 20
    assert rejected == 5 / 20
    assert sum(mask.fractions.values()) < rejected


# -- A blink's flanks (design spec `2026-10-11-blink-padding-design.md`). The
# gaze vector is disturbed for tens of milliseconds before the pupil is lost
# and after it returns, so each blink is padded, `invalid`, from its edges.


def _padded(before_ms, after_ms, dilate=0, min_epoch=1):
    return ValidityParams(20.0, 15.0, 1000.0, dilate_samples=dilate, min_epoch_samples=min_epoch,
                          blink_pad_before_ms=before_ms, blink_pad_after_ms=after_ms)


def test_a_blink_is_padded_by_the_stated_milliseconds_either_side():
    """10 ms before and 20 ms after at 500 Hz: 5 and 10 samples, from the
    blink's edges, `invalid`; the blink's own frames stay `blink`."""
    gaze, vel, loss, gaps = _clean(40)
    loss.blink[15:20] = True

    labels = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 20.0), fs_hz=FS_HZ).labels

    assert [labels[i] for i in range(10, 15)] == [Label.INVALID] * 5
    assert [labels[i] for i in range(15, 20)] == [Label.BLINK] * 5
    assert [labels[i] for i in range(20, 30)] == [Label.INVALID] * 10
    assert labels[9] is None and labels[30] is None


def test_the_margin_sits_inside_the_pad_rather_than_adding_to_it():
    gaze, vel, loss, gaps = _clean(40)
    loss.blink[15:20] = True

    labels = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 20.0, dilate=2), fs_hz=FS_HZ).labels

    assert labels[10] is Label.INVALID and labels[29] is Label.INVALID
    assert labels[9] is None and labels[30] is None


def test_the_pad_is_turned_into_samples_at_the_rate_given():
    """The same milliseconds at 1 kHz: 10 and 20 samples."""
    gaze, vel, loss, gaps = _clean(60)
    loss.blink[20:25] = True

    labels = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 20.0), fs_hz=1000.0).labels

    assert labels[9] is None and labels[10] is Label.INVALID
    assert labels[44] is Label.INVALID and labels[45] is None


def test_a_pad_is_rounded_to_the_nearest_sample():
    """At the reference recording's 498.55 Hz, 50 ms is 24.93 samples and 80
    ms 39.88: 25 and 40, where truncating would give 24 and 39. And 51 ms is
    25.43 and 81 ms 40.38: 25 and 40 again, where rounding up would give 26
    and 41."""
    gaze, vel, loss, gaps = _clean(100)
    loss.blink[40:42] = True

    up = validity_labels(gaze, vel, loss, gaps, _padded(50.0, 80.0), fs_hz=498.55).labels
    down = validity_labels(gaze, vel, loss, gaps, _padded(51.0, 81.0), fs_hz=498.55).labels

    for labels in (up, down):
        assert labels[14] is None and labels[15] is Label.INVALID
        assert labels[81] is Label.INVALID and labels[82] is None


def test_a_pad_on_one_side_alone_is_applied():
    """A pad of 0 on one side leaves the other in place."""
    gaze, vel, loss, gaps = _clean(40)
    loss.blink[15:20] = True

    before = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 0.0), fs_hz=FS_HZ).labels
    after = validity_labels(gaze, vel, loss, gaps, _padded(0.0, 20.0), fs_hz=FS_HZ).labels

    assert [before[i] for i in range(10, 15)] == [Label.INVALID] * 5
    assert before[9] is None and before[20] is None
    assert [after[i] for i in range(20, 30)] == [Label.INVALID] * 10
    assert after[14] is None and after[30] is None


def test_two_blinks_whose_pads_meet_are_one_withheld_stretch():
    gaze, vel, loss, gaps = _clean(60)
    loss.blink[10:12] = True    # padded to 5..21
    loss.blink[25:27] = True    # padded to 20..36

    labels = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 20.0), fs_hz=FS_HZ).labels

    assert all(labels[i] is not None for i in range(5, 37))
    assert labels[4] is None and labels[37] is None


def test_a_short_epoch_stranded_between_two_pads_is_dropped():
    gaze, vel, loss, gaps = _clean(80)
    loss.blink[10:12] = True    # padded to 5..21
    loss.blink[30:32] = True    # padded to 25..41, leaving 22..24: three samples

    labels = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 20.0, min_epoch=5), fs_hz=FS_HZ).labels

    assert [labels[i] for i in (22, 23, 24)] == [Label.INVALID] * 3


def test_only_blinks_are_padded():
    """A lost reflection keeps the margin and nothing more."""
    gaze, vel, loss, gaps = _clean(40)
    loss.feature_lost[15:20] = True

    labels = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 20.0, dilate=2), fs_hz=FS_HZ).labels

    assert labels[13] is Label.INVALID and labels[21] is Label.INVALID
    assert labels[12] is None and labels[22] is None


def test_a_frame_gap_keeps_the_margin_and_is_not_padded():
    """As a lost reflection does (spec section 4): the gap after row 19
    corrupts samples 18-21, and the margin grows that to 16-23."""
    from wl_preproc.eye.ohdpi import FrameGap

    gaze, vel, loss, _ = _clean(40)

    labels = validity_labels(gaze, vel, loss, (FrameGap(row=19, n_missing=3),), _padded(10.0, 20.0, dilate=2),
                             fs_hz=FS_HZ).labels

    assert labels[16] is Label.INVALID and labels[23] is Label.INVALID
    assert labels[15] is None and labels[24] is None


def test_a_paramset_stored_before_the_pads_loads_without_them():
    """A stored paramset is a dict without the two fields: it loads with
    pads of 0, so its content hash still names what it does, and it masks a
    blink as before -- the blink and its margin."""
    stored = {name: value for name, value in asdict(DEFAULT_VALIDITY_PARAMS).items()
              if not name.startswith("blink_pad_")}
    params = ValidityParams(**stored)
    gaze, vel, loss, gaps = _clean(60)
    loss.blink[20:25] = True

    labels = validity_labels(gaze, vel, loss, gaps, params, fs_hz=FS_HZ).labels

    assert params.blink_pad_before_ms == 0.0 and params.blink_pad_after_ms == 0.0
    assert labels[14] is None and labels[15] is Label.INVALID
    assert labels[29] is Label.INVALID and labels[30] is None


def test_the_pads_count_toward_no_criterion():
    """As the margin does not: the raw criteria are unchanged."""
    gaze, vel, loss, gaps = _clean(40)
    loss.blink[15:20] = True

    fractions = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 20.0), fs_hz=FS_HZ).fractions

    assert fractions["blink"] == 5 / 40
    assert sum(fractions.values()) == 5 / 40


def test_a_blink_at_the_recording_s_edge_is_padded_only_inward():
    """A pad stops at the recording's first and last frames: a blink two
    frames in is padded over those two, and one two frames from the end
    over the last two."""
    gaze, vel, loss, gaps = _clean(40)
    loss.blink[2:5] = True
    loss.blink[35:38] = True

    labels = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 20.0), fs_hz=FS_HZ).labels

    assert [labels[i] for i in (0, 1, 38, 39)] == [Label.INVALID] * 4
    assert [labels[i] for i in range(5, 15)] == [Label.INVALID] * 10
    assert [labels[i] for i in range(30, 35)] == [Label.INVALID] * 5
    assert all(labels[i] is None for i in range(15, 30))


# Design spec `2026-11-06-eye-lost-data-and-region-design.md` section 2: a
# sample not offered is LOST (no measurement, or none to trust), carried
# across by a run where the loss is short, or INVALID (a measurement, of the
# wrong thing), which ends one. `_QUIET` above names no bound, so it masks as
# every paramset stored before lost data was carried; `_CARRY` carries it.
_CARRY = ValidityParams(**{**asdict(_QUIET), "max_lost_ms": 50.0})


def test_a_lost_reflection_a_value_that_is_not_a_number_and_an_impossible_speed_are_lost():
    gaze, vel, loss, gaps = _clean(40)
    loss.feature_lost[10] = True
    gaze[20] = np.nan
    vel[30] = (2000.0, 0.0)

    labels = validity_labels(gaze, vel, loss, gaps, _CARRY, fs_hz=FS_HZ).labels

    assert [labels[i] for i in (10, 20, 30)] == [Label.LOST] * 3
    assert sum(label is not None for label in labels) == 3


def test_a_blink_and_gaze_off_the_screen_are_invalid():
    gaze, vel, loss, gaps = _clean(20)
    loss.blink[5] = True
    gaze[12] = (25.0, 0.0)

    labels = validity_labels(gaze, vel, loss, gaps, _CARRY, fs_hz=FS_HZ).labels

    assert labels[5] is Label.BLINK and labels[12] is Label.INVALID


def test_a_repaired_glitch_is_lost():
    """Filled in by a straight line before the mask reads it: offered, but no
    measurement (section 2)."""
    gaze, vel, loss, gaps = _clean(20)
    repaired = np.zeros(20, dtype=bool)
    repaired[7] = True

    labels = validity_labels(gaze, vel, loss, gaps, _CARRY, fs_hz=FS_HZ, repaired=repaired).labels

    assert labels[7] is Label.LOST and sum(label is not None for label in labels) == 1


def test_a_repaired_glitch_is_lost_on_its_own_sample_alone():
    """Filled in by a straight line, not a tracking failure: no margin, as
    the spec's table and wl-xcon's reading (its R5) have it. With one, a
    single glitch inside a short saccade erased it (the final review of
    2026-11-07)."""
    gaze, vel, loss, gaps = _clean(20)
    repaired = np.zeros(20, dtype=bool)
    repaired[7] = True
    params = ValidityParams(**{**asdict(_CARRY), "dilate_samples": 5})

    labels = validity_labels(gaze, vel, loss, gaps, params, fs_hz=FS_HZ, repaired=repaired).labels

    assert [i for i, label in enumerate(labels) if label is not None] == [7]


def test_a_frame_gap_is_lost_and_takes_no_margin():
    """Section 2.2: a dropped frame is not a tracking failure, and its
    neighbours' positions are measurements; only the four velocities that
    span it are lost."""
    from wl_preproc.eye.ohdpi import FrameGap

    gaze, vel, loss, _ = _clean(30)
    params = ValidityParams(**{**asdict(_CARRY), "dilate_samples": 5})

    labels = validity_labels(gaze, vel, loss, (FrameGap(row=14, n_missing=1),), params, fs_hz=FS_HZ).labels

    assert [i for i, label in enumerate(labels) if label is not None] == [13, 14, 15, 16]
    assert all(labels[i] is Label.LOST for i in (13, 14, 15, 16))


def test_a_lost_margin_is_lost():
    """Section 2.1: a margin takes the kind of what it grows."""
    gaze, vel, loss, gaps = _clean(30)
    loss.feature_lost[10] = True
    params = ValidityParams(**{**asdict(_CARRY), "dilate_samples": 2})

    labels = validity_labels(gaze, vel, loss, gaps, params, fs_hz=FS_HZ).labels

    assert [i for i, label in enumerate(labels) if label is Label.LOST] == [8, 9, 10, 11, 12]
    assert sum(label is not None for label in labels) == 5


@pytest.mark.parametrize("where", [
    pytest.param({"blink": 11}, id="beside-a-blink"),
    pytest.param({"lost": 0}, id="at-the-recordings-start"),
    pytest.param({"lost": 19}, id="at-its-end"),
    pytest.param({"lost": 2}, id="beside-the-start's-zero-filled-velocities"),
    pytest.param({"lost": 17}, id="beside-the-end's-zero-filled-velocities"),
])
def test_a_lost_stretch_not_between_two_measured_samples_is_withheld(where):
    """A run is carried across a loss from one measured sample to the next;
    a loss with a withheld sample or the recording's edge on a side carries
    nothing, and is withheld (section 3). The two samples at each end have
    zero-filled velocities, not measured ones, so a loss beside them is
    withheld too (the rule both sessions agreed on 2026-11-07)."""
    gaze, vel, loss, gaps = _clean(20)
    lost = where.get("lost", 10)
    loss.feature_lost[lost] = True
    if "blink" in where:
        loss.blink[where["blink"]] = True

    labels = validity_labels(gaze, vel, loss, gaps, _CARRY, fs_hz=FS_HZ).labels

    assert labels[lost] is Label.INVALID


@pytest.mark.parametrize("lost", [3, 16])
def test_a_lost_stretch_between_two_measured_samples_inside_the_ends_is_carried(lost):
    gaze, vel, loss, gaps = _clean(20)
    loss.feature_lost[lost] = True

    assert validity_labels(gaze, vel, loss, gaps, _CARRY, fs_hz=FS_HZ).labels[lost] is Label.LOST


@pytest.mark.parametrize(("n_lost", "kind"), [(24, Label.LOST), (25, Label.INVALID)])
def test_a_lost_stretch_is_carried_while_its_measured_neighbours_are_50_ms_apart(n_lost, kind):
    """Section 3: inclusive, as wl-xcon's `max_gap_s` is, and counted in whole
    frames, 25 at 500 Hz: 24 lost samples leave their neighbours 50 ms apart,
    25 leave them 52 ms apart."""
    gaze, vel, loss, gaps = _clean(60)
    loss.feature_lost[10:10 + n_lost] = True

    labels = validity_labels(gaze, vel, loss, gaps, _CARRY, fs_hz=FS_HZ).labels

    assert {labels[i] for i in range(10, 10 + n_lost)} == {kind}
    assert labels[9] is None and labels[10 + n_lost] is None


@pytest.mark.parametrize(("n_missing", "kind"), [(24, Label.LOST), (25, Label.INVALID)])
def test_the_bound_counts_the_frames_the_camera_dropped(n_missing, kind):
    """The bound is the loss of data, from the last position measured to the
    next (wl-xcon's `max_gap_s`): a gap after row 14 makes rows 13-16 lost,
    their velocities spanning it, but their positions are measured, so the
    loss is the `1 + n_missing` frames from row 14 to row 15."""
    from wl_preproc.eye.ohdpi import FrameGap

    gaze, vel, loss, _ = _clean(40)

    labels = validity_labels(gaze, vel, loss, (FrameGap(row=14, n_missing=n_missing),), _CARRY,
                             fs_hz=FS_HZ).labels

    assert {labels[i] for i in (13, 14, 15, 16)} == {kind}


def test_the_default_carries_lost_data_for_50_ms():
    assert DEFAULT_VALIDITY_PARAMS.max_lost_ms == 50.0


def test_a_paramset_stored_before_lost_data_was_carried_masks_as_before():
    """A stored paramset without `max_lost_ms` loads with 0, so its content
    hash still names what it does: every loss withheld as `invalid`, a frame
    gap with its margin (section 8)."""
    from wl_preproc.eye.ohdpi import FrameGap

    stored = {name: value for name, value in asdict(DEFAULT_VALIDITY_PARAMS).items() if name != "max_lost_ms"}
    params = ValidityParams(**{**stored, "dilate_samples": 2, "min_epoch_samples": 1})
    gaze, vel, loss, _ = _clean(40)
    loss.feature_lost[30] = True

    labels = validity_labels(gaze, vel, loss, (FrameGap(row=10, n_missing=1),), params, fs_hz=FS_HZ).labels

    assert params.max_lost_ms == 0.0
    assert [i for i, label in enumerate(labels) if label is not None] == [*range(7, 15), *range(28, 33)]
    assert all(labels[i] is Label.INVALID for i in [*range(7, 15), *range(28, 33)])


def test_lost_positions_are_filled_along_frame_time_and_a_dropped_frames_neighbours_keep_theirs():
    """Design spec `2026-11-06-eye-lost-data-and-region-design.md` section 3:
    what a detector is offered over a carried loss. A position that is not a
    measurement is filled by the straight line between the measured samples
    either side, placed at its own frame's time; the rows whose velocity
    spans a dropped frame keep their measured positions."""
    from wl_preproc.eye.detect.validity import fill_lost, frame_numbers, rows_across_gaps
    from wl_preproc.eye.ohdpi import FrameGap

    gaze = np.column_stack([np.arange(20, dtype=float), np.zeros(20)])
    gaze[5:7] = np.nan
    gaze[13, 0] = 99.0
    gaps = (FrameGap(row=13, n_missing=2),)
    offered = np.full(20, None, dtype=object)
    offered[[5, 6, 12, 13, 14, 15]] = Label.LOST

    filled = fill_lost(gaze, offered, frame_numbers(20, gaps), rows_across_gaps(20, gaps))

    assert filled[5:7, 0].tolist() == [5.0, 6.0]
    assert filled[13, 0] == 99.0 and filled[12, 0] == 12.0
    assert np.isfinite(filled).all() and filled[[0, 4, 7, 19], 0].tolist() == [0.0, 4.0, 7.0, 19.0]



# Design spec `2026-11-06-eye-lost-data-and-region-design.md` section 7: the
# screen region is each session's visible field plus a margin, on each axis;
# a session that recorded no field keeps the placeholder.
_SETUP = ValidityParams(**{**asdict(_QUIET), "region_from_setup": True, "region_margin_deg": 2.0})


def test_the_screen_region_is_the_sessions_visible_field_plus_its_margin():
    gaze, vel, loss, gaps = _clean(4)
    gaze[:] = [(32.5, 0.0), (32.6, 0.0), (0.0, 20.4), (0.0, 20.5)]

    mask = validity_labels(gaze, vel, loss, gaps, _SETUP, fs_hz=FS_HZ, field_deg=(30.54, 18.41))

    assert list(mask.labels) == [None, Label.INVALID, None, Label.INVALID]
    assert mask.region_deg == pytest.approx((32.54, 20.41)) and mask.region_from_setup is True


def test_a_session_with_no_recorded_field_keeps_the_placeholder():
    gaze, vel, loss, gaps = _clean(2)
    gaze[:] = [(19.9, 0.0), (20.1, 0.0)]

    mask = validity_labels(gaze, vel, loss, gaps, _SETUP, fs_hz=FS_HZ, field_deg=None)

    assert list(mask.labels) == [None, Label.INVALID]
    assert (mask.region_deg, mask.region_from_setup) == ((20.0, 15.0), False)


def test_a_paramset_stored_before_the_region_came_from_the_session_keeps_its_own():
    """Loaded without the two fields, it masks at its stored region whatever
    the session recorded, so its content hash still names what it does."""
    stored = {name: value for name, value in asdict(DEFAULT_VALIDITY_PARAMS).items()
              if name not in ("region_from_setup", "region_margin_deg")}
    params = ValidityParams(**stored)
    gaze, vel, loss, gaps = _clean(1)

    mask = validity_labels(gaze, vel, loss, gaps, params, fs_hz=FS_HZ, field_deg=(30.54, 18.41))

    assert (params.region_from_setup, params.region_margin_deg) == (False, 0.0)
    assert (mask.region_deg, mask.region_from_setup) == ((20.0, 15.0), False)


def test_the_default_takes_the_region_from_the_session_with_a_2_degree_margin():
    assert (DEFAULT_VALIDITY_PARAMS.region_from_setup, DEFAULT_VALIDITY_PARAMS.region_margin_deg) == (True, 2.0)
