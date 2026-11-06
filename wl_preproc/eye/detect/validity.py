"""The validity mask -- OpenIrisDPI's own five criteria, plus one guard.

Its first criterion, eye open, is two: `blink` and `feature_lost`, both read
by `ohdpi.TrackerLoss`'s rule from what the tracker wrote (design spec
`2026-10-11-eye-loss-design.md` section 3). *Until 2026-10-11 it was `blink`
alone, read as `DataQuality` below 100, which OpenIrisDPI writes as 100 on
every frame, blinks included; true when written.*

Each blink is padded either side, `invalid`, from its edges: by
`blink_pad_before_ms` before the pupil is lost and `blink_pad_after_ms` after
it returns, 50 and 80 ms in the default paramset, where the gaze vector is
already or still disturbed (design spec `2026-10-11-blink-padding-design.md`).
The pads, like the margin around every rejected frame, belong to no one
criterion; only blinks are padded.

The guard is `non_finite`: a sample whose gaze, velocity or loss columns are
not a finite number. It is not one of OpenIrisDPI's criteria but a
data-integrity check, added 2026-09-27 -- a NaN compares false against every
threshold the five apply, so until then a sample with no gaze at all was
offered to every detector as usable (found by NSLR's final review).

**None of them involves a detector** (design spec section 2), which is why
this is its own module and, downstream, its own table with its own paramset:
three detectors running against three different masks would make the agreement
metric compare masks as well as detections, measuring the thing it exists to
hold constant.

Returns `None` for a sample a detector may label, and a `Label` for one it may
not. Precedence is enforced HERE, by not offering the sample at all, rather
than by asking every detector to respect an ordering -- and the one ordering
that is genuinely a ranking, `blink` over `invalid` over `lost`, is
`MASK_PRECEDENCE` below.

**Lost or invalid** (design spec `2026-11-06-eye-lost-data-and-region-design.md`
section 2, the requester's decisions of 2026-11-06). Where a paramset carries
lost data (`max_lost_ms` above 0), a sample not offered as measured is one of
two kinds. LOST: nothing was measured, or nothing to trust -- a lost
reflection, a value that is not a number, an impossible speed, a repaired
glitch, and the four velocities a dropped frame spans. INVALID: a
measurement, of the wrong thing -- a blink with its padding, and gaze off the
screen. A run is carried across a lost stretch that lies between two measured
samples at most `max_lost_ms` apart; any other lost stretch is withheld as
`invalid`, and a detector meets exactly what is stored. A paramset stored
before, naming no bound, masks as it always did: every criterion withheld as
`invalid`, with its margin.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from wl_preproc.eye.detect.labels import Label, true_runs
from wl_preproc.eye.detect.velocity import _HALF_WINDOW
from wl_preproc.eye.ohdpi import FrameGap, TrackerLoss

# **The one ranking this subsystem actually applies**, most specific first.
# Design spec section 1 gives all eight labels a precedence order, but only
# this pair is ever a contest between two candidates for one sample: a blink
# IS a validity failure, so generic-first would mean no sample is ever
# labelled `blink` and the label would be dead code that looks alive (section
# 1's own words: "the order is load-bearing"). Everything below comes from a
# detector, and a detector returns disjoint intervals, so no sample is ever
# offered two detected labels to choose between.
#
# It lives here, beside the criteria it ranks, and it is READ by
# `validity_labels` below rather than restated by it. The order used to live
# implicitly in two assignments whose sequence was the whole rule ("assigned
# LAST so it wins"), which is a fact stated by statement order and by a
# comment, checkable only by reading both. A general eight-label version of
# this tuple lived in `labels.py` instead and was consumed by the
# conjunction, where it ranked a pair section 1 says is a split -- see
# `schema/detect.py::_conjunction_label`. Trimmed to the pair that is
# genuinely ranked, and moved to where the ranking is genuinely applied.
#
# `lost` joined it on 2026-11-06, last: a lost margin that meets a withheld
# sample is withheld (design spec `2026-11-06-eye-lost-data-and-region-design.md`
# section 2.1).
MASK_PRECEDENCE: tuple[Label, ...] = (Label.BLINK, Label.INVALID, Label.LOST)


@dataclass(frozen=True, slots=True)
class ValidityParams:
    region_half_width_deg: float
    region_half_height_deg: float
    max_speed_deg_s: float
    dilate_samples: int
    min_epoch_samples: int
    # Not a mask criterion: `glitch.py::repair_glitches` repairs gaze that
    # leaves and returns faster than `max_speed_deg_s` within less than
    # this, before the mask or any detector reads it. A default, unlike the
    # fields above, so a paramset written before it existed still loads.
    max_glitch_ms: float = 10.0
    # A blink's flanks, withheld as `invalid` from its edges: the gaze vector
    # is disturbed before the pupil is lost and after it returns (design spec
    # `2026-10-11-blink-padding-design.md`). 0 by default, so a paramset
    # stored before them loads unchanged and its content hash still names
    # what it does; `DEFAULT_VALIDITY_PARAMS` sets the measured values.
    blink_pad_before_ms: float = 0.0
    blink_pad_after_ms: float = 0.0
    # How far apart, at most, the measured samples either side of a lost
    # stretch may be for a run to be carried across it (design spec
    # `2026-11-06-eye-lost-data-and-region-design.md` section 3; wl-xcon's
    # `max_gap_s`). 0 by default, as the pads are: a paramset stored before
    # it carries nothing and masks as it did.
    max_lost_ms: float = 0.0
    # The screen region from the session's own recorded display setup: each
    # eye's visible field plus `region_margin_deg` on each axis, where the
    # session recorded one (design spec
    # `2026-11-06-eye-lost-data-and-region-design.md` section 7). Off and 0
    # by default, so a paramset stored before them masks at its own region.
    region_margin_deg: float = 0.0
    region_from_setup: bool = False


# **These are placeholders with no measured basis on this rig** (design spec
# section 11, open question 1). 20 x 15 degrees is a generous screen; 1000
# deg/s is above any physiological saccade. Pinned by a test so that measuring
# them properly is a visible change rather than a silent drift.
DEFAULT_VALIDITY_PARAMS = ValidityParams(
    region_half_width_deg=20.0,
    region_half_height_deg=15.0,
    max_speed_deg_s=1000.0,
    dilate_samples=5,
    min_epoch_samples=10,
    # Not a placeholder: 10 ms is the shortest saccade Nystrom-Holmqvist and
    # REMoDNaV accept, below which an out-and-back cannot be two eye
    # movements (the requester's decision of 2026-09-28).
    max_glitch_ms=10.0,
    # Measured on the reference recording: the gaze vector's frame-to-frame
    # steps fall to 1.7x baseline 50 ms before the pupil is lost, and to 2.9x
    # 80 ms after it returns, where the real movement after a blink begins
    # (design spec `2026-10-11-blink-padding-design.md` section 1).
    blink_pad_before_ms=50.0,
    blink_pad_after_ms=80.0,
    # wl-xcon's online detector's bound, which the requester's decision of
    # 2026-11-06 makes both detectors' (its `max_gap_s`, 0.05 s).
    max_lost_ms=50.0,
    # The requester's decision of 2026-11-06: the visible field the session
    # recorded, plus 2 degrees for calibration error and fixation scatter
    # about a target at its edge. The placeholder region above stays for a
    # session that recorded none.
    region_margin_deg=2.0,
    region_from_setup=True,
)


@dataclass(frozen=True, slots=True)
class ValidityMask:
    """One eye's mask, plus the per-criterion bookkeeping that says WHICH
    criterion rejected what -- design spec section 7's "the mask, as runs;
    per-criterion rejected fractions".

    **A record rather than a bare array, because the return shape was the
    only obstacle there ever was.** All five criteria it had then were
    already named locals inside `validity_labels`; four of them were never
    returned, so `schema/detect.py::EyeValidity.make()` wrote `NULL` into
    four columns section 7 asks it to fill, under a comment calling them
    "not separately recoverable". They were always recoverable. Nothing
    about the data was missing, and a mask that rejects most of a session
    said only that something did.

    `fractions` is keyed by CRITERION name (`blink`, `feature_lost`,
    `out_of_region`, `too_fast`, `frame_gap`, `short_epoch`, `non_finite`),
    never by `EyeValidity` column name: this module states criteria, the
    schema states columns. Those keys are exactly the column suffixes, which
    is what lets `EyeValidity.make()` spread them all in ONE line instead of
    writing each out. (Five until 2026-09-27, when `non_finite` joined, and
    six until 2026-10-11, when `feature_lost` did.) Columns fed by hand from
    one place is precisely the shape a copy-paste error survives in -- every
    column still looks populated, so nothing downstream reads wrong, and no
    test that only checks for a number notices.

    **Every fraction is a RAW per-criterion count over all `n` samples,
    never an apportioned share of the rejection**, because "which criterion
    did it" is a question only raw counts answer. Two consequences a reader
    is owed, and neither is a defect:

    - They OVERLAP. One sample can be both `out_of_region` and `too_fast`,
      and the mask keeps no record of which criterion "got there first"
      because no such record would mean anything. Summing them all can
      therefore exceed 1.0, and can exceed the fraction of samples the mask
      actually rejects.
    - They are counted at two DIFFERENT stages, deliberately. `blink`,
      `feature_lost`, `out_of_region`, `too_fast`, `frame_gap` and
      `non_finite` are counted BEFORE `_dilate` grows every rejected
      region: the halo dilation adds belongs to no one criterion, nor do
      the blink pads (`_blink_pads`), and attributing either to whichever
      criterion it happens to surround would be an invention. `short_epoch`
      can only be counted after, because dilation is what creates most of
      the short epochs it drops. So they
      can sum BELOW the rejected fraction too.
    """

    labels: np.ndarray
    fractions: Mapping[str, float]
    # The screen region the mask applied, `(half-width, half-height)` in
    # per-axis degrees, and whether it was the session's own field plus the
    # margin or the paramset's placeholder (`screen_region`).
    region_deg: tuple[float, float]
    region_from_setup: bool


def validity_labels(
    gaze_deg: np.ndarray,
    velocity_deg_s: np.ndarray,
    loss: TrackerLoss,
    frame_gaps: Sequence[FrameGap],
    params: ValidityParams,
    *,
    fs_hz: float,
    repaired: np.ndarray | None = None,
    field_deg: tuple[float, float] | None = None,
) -> ValidityMask:
    """One entry per sample: a `Label` where the sample is unusable, `None`
    where a detector may label it -- and beside it each criterion's own
    rejected fraction. See `ValidityMask` for what the seven fractions are,
    and for what they deliberately do not sum to. `fs_hz` is the
    recording's rate, which turns the blink pads' and the bound's
    milliseconds into samples. `repaired` marks the samples
    `glitch.repair_glitches` filled in, lost where lost data is carried.
    `field_deg` is each eye's visible field as the session recorded it, or
    None (`screen_region`)."""
    n = gaze_deg.shape[0]
    # Eye open, read by the rule: a blink (the pupil not found), and a lost
    # reflection (P1 or P4 at 0, or `DataQuality` below 100), where `CR1 -
    # CR4` is not a position (design spec `2026-10-11-eye-loss-design.md`
    # section 3 and amendment 8).
    blink = loss.blink
    feature_lost = loss.feature_lost

    width, height, from_setup = screen_region(params, field_deg)
    outside = (np.abs(gaze_deg[:, 0]) > width) | (np.abs(gaze_deg[:, 1]) > height)
    too_fast = np.hypot(velocity_deg_s[:, 0], velocity_deg_s[:, 1]) > params.max_speed_deg_s

    # A gap sits between rows `row` and `row + 1`. The velocity estimator spans
    # `[n-2, n+2]`, so it corrupts four velocity estimates: those at indices
    # `[row-1, row, row+1, row+2]`. Their gaze samples are unavailable for
    # detection. The coupling to `_HALF_WINDOW` is deliberate: if the
    # estimator's window changes, the mask's excluded range changes with it.
    #
    # **This loop WAS unreachable in production, and the reason was one layer
    # out** (found by the saccade-detection branch's whole-branch review and
    # recorded in commit `7d4a00f`'s amendment to the saccade-detection design
    # spec, 2026-09-01 -- true when written, and deliberately not fixed in that
    # pass; that review's own "finding H2" label lived only in a working
    # ledger that no longer exists, so it is not cited here). `timebase/extract.py::
    # extract_ohdpi` raised on any non-empty `frame_gaps`; `timebase/
    # segments.py::scan_system` did not catch it and `SystemTimebase.make()`
    # wrapped only `fit_rate`, so such a session got no `SystemTimebase` row
    # for ohDPI, no `core.Segment` followed, and `EyeValidity.key_source` --
    # which requires one -- never named it. `EyeValidity.make()` could
    # therefore only ever pass `frame_gaps=()`, and this loop ran for real in
    # `tests/eye/detect/test_validity.py` alone.
    #
    # **Every premise in that paragraph is now false, on this branch.**
    # Commit `053c2cd` ("timebase: correct the sample index across a gap
    # instead of refusing the file") removed `extract_ohdpi`'s raise: a
    # dropped frame is now reconstructed with hold-previous fill and reported
    # on `BitStream.gaps`, never refused. Commit `53d8615` ("timebase:
    # discard the barcode words a gap falls inside") made `scan_system`
    # gap-aware: it decodes through the gap, drops only the barcode words
    # `barcode_clear_of_gaps` says the gap actually corrupts, and records
    # `n_frame_gaps`/`n_frames_missing`/`n_barcodes_dropped` on its own
    # `RecordingScan`. An alignable-but-gapped ohDPI file now gets a real
    # `SystemTimebase` row and a real `core.Segment`, so `EyeValidity.
    # key_source` names it exactly as it would any other aligned recording.
    # This loop is REACHABLE now, and it is exercised end to end from a
    # stored trace -- not merely from the hand-built arrays `tests/eye/
    # detect/test_validity.py` already covered it with -- by `tests/schema/
    # test_eye_populate.py::
    # test_the_frame_gap_criterion_fires_on_a_stored_trace`, run over
    # `gapped_session` (Task 6, `tests/schema/conftest.py`): one real,
    # three-frame ohDPI gap, planted, extracted, aligned, and masked through
    # a real `EyeValidity.populate()`.
    #
    # Kept rather than deleted back when it looked permanently dead, and that
    # reasoning is worth keeping rather than erasing now that it is not: the
    # criterion is one of OpenIrisDPI's own five (design spec section 2), its
    # input genuinely existed at the `read_ohdpi` layer even while nothing
    # downstream could reach it, and whether a dropped frame should reject a
    # whole recording was a timebase decision this branch went on to take
    # separately -- see design spec section 2 for that dependency stated in
    # full. The reference recording still has zero gaps across 1,177,799
    # rows, so real (non-synthetic) data has still not exercised this loop --
    # only a planted gap has, so far.
    across_gap = rows_across_gaps(n, frame_gaps)

    # **The guard, and the one criterion not from OpenIrisDPI's notebook.**
    # Every comparison above is false for a NaN: `abs(NaN) > half-width`,
    # `hypot(NaN, 0) > ceiling`, and the rule's `== 0` and `< 100` all say
    # "fine", so a sample with no gaze, no speed or no loss columns was
    # offered to every detector as usable. Velocity is tested here too, not
    # left to `_dilate` below: one NaN gaze sample spoils every velocity
    # estimate within `_HALF_WINDOW` of it, and a paramset may set
    # `dilate_samples` below that. `inf` gaze was already out of region and
    # still is; the raw counts overlap by design (see `ValidityMask`).
    #
    # An unreadable loss column is `invalid`, not `blink`: `blink` means the
    # tracker DECLARED a failure, and a missing value declares nothing.
    non_finite = (
        ~np.isfinite(gaze_deg).all(axis=1)
        | ~np.isfinite(velocity_deg_s).all(axis=1)
        | loss.unreadable
    )

    # Each blink's flanks, measured from its edges, after the margin so that
    # the margin sits inside a pad rather than adding to it, and before
    # `short_epoch`, so a fragment stranded between two pads is dropped as
    # any short epoch is (design spec `2026-10-11-blink-padding-design.md`).
    pads = _blink_pads(
        blink,
        round(params.blink_pad_before_ms * fs_hz / 1000.0),
        round(params.blink_pad_after_ms * fs_hz / 1000.0),
    )
    if params.max_lost_ms > 0:
        # Each kind grown by its own margin (design spec
        # `2026-11-06-eye-lost-data-and-region-design.md` section 2): a
        # dropped frame by none, since it is not a tracking failure and its
        # neighbours' positions are measurements; a lost margin that meets a
        # withheld sample is withheld; and a lost stretch no run can be
        # carried across is withheld too.
        unusable = _dilate(blink | outside, params.dilate_samples) | pads
        lost_raw = feature_lost | too_fast | non_finite
        if repaired is not None:
            lost_raw = lost_raw | repaired
        untrusted = _dilate(lost_raw, params.dilate_samples)
        lost = (untrusted | across_gap) & ~unusable
        uncarried = _uncarried(lost, unusable, untrusted, frame_numbers(n, frame_gaps),
                               carried_frames(params.max_lost_ms, fs_hz))
        unusable = unusable | uncarried
        lost = lost & ~uncarried
    else:
        unusable = _dilate(blink | feature_lost | outside | too_fast | across_gap | non_finite,
                           params.dilate_samples) | pads
        lost = np.zeros(n, dtype=bool)
    # Bound to a name rather than folded straight into `unusable`, because
    # `_short_valid_epochs`' own docstring says it is returned as its own
    # mask "so the caller can see that these samples were dropped for being
    # SHORT rather than for a tracking failure". `fractions["short_epoch"]`
    # below is the caller finally doing that; until it existed, that
    # distinction was computed on every session and immediately discarded.
    short_epoch = _short_valid_epochs(unusable, params.min_epoch_samples)
    unusable = unusable | short_epoch
    lost = lost & ~short_epoch

    # Walked in REVERSE precedence order, so the most specific label is
    # assigned last and wins. `MASK_PRECEDENCE` is what decides that, not the
    # order these two lines happen to be written in: reverse the tuple and
    # every doubly-claimed sample comes back `invalid` instead of `blink`
    # (`tests/eye/detect/test_validity.py::
    # test_mask_precedence_is_what_decides_which_criterion_wins` reverses it
    # and checks exactly that). A label added to the tuple with no criterion
    # beside it raises `KeyError` here rather than being silently skipped.
    claimed = {Label.BLINK: blink, Label.INVALID: unusable, Label.LOST: lost}
    out = np.full(n, None, dtype=object)
    for label in reversed(MASK_PRECEDENCE):
        out[claimed[label]] = label
    return ValidityMask(
        labels=out,
        # ONE mapping, built here beside the criteria it names rather than
        # reassembled at the call site -- see `ValidityMask` for why five
        # hand-written lines somewhere else is the shape that hides a
        # copy-paste error.
        #
        # `blink` is the raw criterion, not `out == Label.BLINK`. The two
        # are the same set by construction (BLINK is assigned last, so every
        # raw-blink sample carries it), and naming the criterion is what
        # makes this line say the same kind of thing the six beside it say
        # -- a fraction OF A CRITERION, not of a rendered verdict.
        fractions={
            "blink": _fraction(blink),
            "feature_lost": _fraction(feature_lost),
            "out_of_region": _fraction(outside),
            "too_fast": _fraction(too_fast),
            "frame_gap": _fraction(across_gap),
            "short_epoch": _fraction(short_epoch),
            "non_finite": _fraction(non_finite),
        },
        region_deg=(width, height),
        region_from_setup=from_setup,
    )


def screen_region(params: ValidityParams, field_deg: tuple[float, float] | None) -> tuple[float, float, bool]:
    """`(half-width, half-height, from_setup)`: the session's visible field
    plus `region_margin_deg` on each axis, where the paramset takes it from
    the setup and the session recorded one; otherwise the paramset's own
    region (design spec `2026-11-06-eye-lost-data-and-region-design.md`
    section 7)."""
    if params.region_from_setup and field_deg is not None:
        return (field_deg[0] + params.region_margin_deg, field_deg[1] + params.region_margin_deg, True)
    return (params.region_half_width_deg, params.region_half_height_deg, False)


def rows_across_gaps(n: int, frame_gaps: Sequence[FrameGap]) -> np.ndarray:
    """The rows whose velocity spans a dropped frame: `[row-1, row+2]` for a
    gap after `row`, coupled to `_HALF_WINDOW` (`validity_labels`)."""
    out = np.zeros(n, dtype=bool)
    for gap in frame_gaps:
        out[max(gap.row + 1 - _HALF_WINDOW, 0) : min(gap.row + _HALF_WINDOW + 1, n)] = True
    return out



def fill_lost(gaze_deg: np.ndarray, offered: np.ndarray, frames: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """The gaze a detector is offered over carried losses (design spec
    `2026-11-06-eye-lost-data-and-region-design.md` section 3): each lost
    sample's position (`Label.LOST` in `offered`) filled by the straight line
    between the samples either side, placed at its own frame's time, except
    where `keep` says its position is a measurement -- a row whose velocity
    spans a dropped frame (`rows_across_gaps`). The mask carries only a loss with a
    measured sample on both sides, so every filled stretch has two."""
    out = gaze_deg.copy()
    filled = np.array([entry is Label.LOST for entry in offered], dtype=bool) & ~keep
    for start, stop in true_runs(filled):
        before, after = start - 1, stop
        share = (frames[start:stop] - frames[before]) / (frames[after] - frames[before])
        out[start:stop] = gaze_deg[before] + share[:, None] * (gaze_deg[after] - gaze_deg[before])
    return out


def frame_numbers(n: int, frame_gaps: Sequence[FrameGap]) -> np.ndarray:
    """Each row's frame, counted from the first row's: its index, plus every
    frame the camera dropped before it."""
    frames = np.arange(n, dtype=np.int64)
    for gap in frame_gaps:
        frames[gap.row + 1:] += gap.n_missing
    return frames


def carried_frames(max_lost_ms: float, fs_hz: float) -> int:
    """How many frames apart, at most, the measured samples either side of a
    lost stretch may be for a run to be carried across it: `max_lost_ms` in
    whole frames, inclusive, so no float rounding decides a boundary (design
    spec `2026-11-06-eye-lost-data-and-region-design.md` section 3)."""
    return int(np.floor(max_lost_ms * fs_hz / 1000.0 + 1e-9))


def _uncarried(lost: np.ndarray, withheld: np.ndarray, untrusted: np.ndarray, frames: np.ndarray,
               max_frames: int) -> np.ndarray:
    """The lost stretches no run can be carried across: each without a
    measured sample on both sides, or across which two positions measured in
    turn are more than `max_frames` apart. The bound is the loss of data, from
    the last position measured to the next, as wl-xcon's `max_gap_s` is: a
    dropped frame's neighbours have positions, though their velocities span
    it (`untrusted` marks the positions that are not measurements). The two
    samples at each end of the recording are no measured sample beside a
    stretch: their velocities are zero-filled (`velocity.py`), so they neither
    end a carried run nor bound a stretch (the rule both sessions agreed on
    2026-11-07)."""
    out = np.zeros_like(lost)
    for start, stop in true_runs(lost):
        before, after = start - 1, stop
        if before < _HALF_WINDOW or after >= lost.size - _HALF_WINDOW or withheld[before] or withheld[after]:
            out[start:stop] = True
            continue
        measured = before + np.flatnonzero(~untrusted[before:after + 1])
        if np.diff(frames[measured]).max() > max_frames:
            out[start:stop] = True
    return out


def _fraction(mask: np.ndarray) -> float:
    """The share of all samples one criterion rejected; `0.0` for a
    zero-length recording.

    `mask.mean()` of an empty array is NaN and a NumPy "mean of empty slice"
    warning. A recording with no samples has nothing to attribute to any
    criterion, and a stated zero in a `double` column is honest where a NaN
    nobody declared -- which compares false against itself, and which the
    daily report would render as `nan%` -- is not.
    """
    return float(mask.mean()) if mask.size else 0.0


def _blink_pads(blink: np.ndarray, before: int, after: int) -> np.ndarray:
    """`before` samples ahead of each run of `blink` and `after` samples
    behind it, the run itself excluded: it keeps its own label. Pads that
    meet make one stretch."""
    out = np.zeros(blink.shape[0], dtype=bool)
    if (before <= 0 and after <= 0) or not blink.any():
        return out
    edges = np.diff(np.concatenate(([0], blink.astype(np.int8), [0])))
    for start, stop in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
        out[max(start - before, 0):start] = True
        out[stop:min(stop + after, blink.shape[0])] = True
    return out


def _dilate(mask: np.ndarray, samples: int) -> np.ndarray:
    """Grow every `True` region by `samples` in each direction. A tracking
    failure does not begin and end cleanly on the sample the tracker admits
    it, which is the notebook's own reason for expanding invalid regions."""
    if samples <= 0 or not mask.any():
        return mask
    grown = mask.copy()
    for shift in range(1, samples + 1):
        grown[shift:] |= mask[:-shift]
        grown[:-shift] |= mask[shift:]
    return grown


def _short_valid_epochs(unusable: np.ndarray, minimum: int) -> np.ndarray:
    """Valid stretches too short to hand a detector. Returned as their own
    mask rather than folded into `unusable` in place, so the caller can see
    that these samples were dropped for being SHORT rather than for a tracking
    failure -- two different facts that must not render identically.

    **That sentence is now true of a real caller, and for a while it was
    not.** `validity_labels` above keeps this mask as `ValidityMask.
    fractions["short_epoch"]`, which `EyeValidity.make()` stores as
    `frac_short_epoch`; before that column was populated, every caller
    discarded the distinction on the next line and this docstring described
    an intent nothing downstream realised.
    """
    out = np.zeros_like(unusable)
    if minimum <= 1:
        return out
    start = None
    for index in range(len(unusable) + 1):
        inside = index < len(unusable) and not unusable[index]
        if inside and start is None:
            start = index
        elif not inside and start is not None:
            if index - start < minimum:
                out[start:index] = True
            start = None
    return out
