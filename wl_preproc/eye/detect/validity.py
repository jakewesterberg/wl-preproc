"""The validity mask -- OpenIrisDPI's own five criteria.

**None of them involves a detector** (design spec section 2), which is why
this is its own module and, downstream, its own table with its own paramset:
three detectors running against three different masks would make the agreement
metric compare masks as well as detections, measuring the thing it exists to
hold constant.

Returns `None` for a sample a detector may label, and a `Label` for one it may
not. Precedence is enforced HERE, by not offering the sample at all, rather
than by asking every detector to respect an ordering -- and the one ordering
that is genuinely a ranking, `blink` over `invalid`, is `MASK_PRECEDENCE`
below.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.velocity import _HALF_WINDOW
from wl_preproc.eye.ohdpi import FrameGap

# `EyeQuality`'s own `_FULL_TRACKING_QUALITY`, restated rather than imported --
# it is private there, and the value belongs to the frozen recording format
# rather than being a choice free to drift between the two places it is read.
#
# NECESSARY, NOT SUFFICIENT: OpenIrisDPI "does not determine when the image
# processing algorithm has failed", so a frame at 100 is one the tracker did
# not declare a failure on, which is not the same as one it tracked correctly.
_FULL_TRACKING_QUALITY = 100.0

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
MASK_PRECEDENCE: tuple[Label, ...] = (Label.BLINK, Label.INVALID)


@dataclass(frozen=True, slots=True)
class ValidityParams:
    region_half_width_deg: float
    region_half_height_deg: float
    max_speed_deg_s: float
    dilate_samples: int
    min_epoch_samples: int


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
)


@dataclass(frozen=True, slots=True)
class ValidityMask:
    """One eye's mask, plus the per-criterion bookkeeping that says WHICH
    criterion rejected what -- design spec section 7's "the mask, as runs;
    per-criterion rejected fractions".

    **A record rather than a bare array, because the return shape was the
    only obstacle there ever was.** All five criteria are already named
    locals inside `validity_labels`; four of them were simply never
    returned, so `schema/detect.py::EyeValidity.make()` wrote `NULL` into
    four columns section 7 asks it to fill, under a comment calling them
    "not separately recoverable". They were always recoverable. Nothing
    about the data was missing, and a mask that rejects most of a session
    said only that something did.

    `fractions` is keyed by CRITERION name (`blink`, `out_of_region`,
    `too_fast`, `frame_gap`, `short_epoch`), never by `EyeValidity` column
    name: this module states criteria, the schema states columns. Those keys
    are exactly the column suffixes, which is what lets `EyeValidity.make()`
    spread all five in ONE line instead of writing out five. Five columns
    fed by hand from one place is precisely the shape a copy-paste error
    survives in -- every column still looks populated, so nothing downstream
    reads wrong, and no test that only checks for a number notices.

    **Every fraction is a RAW per-criterion count over all `n` samples,
    never an apportioned share of the rejection**, because "which criterion
    did it" is a question only raw counts answer. Two consequences a reader
    is owed, and neither is a defect:

    - They OVERLAP. One sample can be both `out_of_region` and `too_fast`,
      and the mask keeps no record of which criterion "got there first"
      because no such record would mean anything. Summing all five can
      therefore exceed 1.0, and can exceed the fraction of samples the mask
      actually rejects.
    - They are counted at two DIFFERENT stages, deliberately. `blink`,
      `out_of_region`, `too_fast` and `frame_gap` are counted BEFORE
      `_dilate` grows every rejected region: the halo dilation adds belongs
      to no one criterion, and attributing it to whichever criterion it
      happens to surround would be an invention. `short_epoch` can only be
      counted after, because dilation is what creates most of the short
      epochs it drops. So the five can sum BELOW the rejected fraction too.
    """

    labels: np.ndarray
    fractions: Mapping[str, float]


def validity_labels(
    gaze_deg: np.ndarray,
    velocity_deg_s: np.ndarray,
    data_quality: np.ndarray,
    frame_gaps: Sequence[FrameGap],
    params: ValidityParams,
) -> ValidityMask:
    """One entry per sample: a `Label` where the sample is unusable, `None`
    where a detector may label it -- and beside it each criterion's own
    rejected fraction. See `ValidityMask` for what the five fractions are,
    and for what they deliberately do not sum to."""
    n = gaze_deg.shape[0]
    blink = data_quality < _FULL_TRACKING_QUALITY

    outside = (np.abs(gaze_deg[:, 0]) > params.region_half_width_deg) | (
        np.abs(gaze_deg[:, 1]) > params.region_half_height_deg
    )
    too_fast = np.hypot(velocity_deg_s[:, 0], velocity_deg_s[:, 1]) > params.max_speed_deg_s

    # A gap sits between rows `row` and `row + 1`. The velocity estimator spans
    # `[n-2, n+2]`, so it corrupts four velocity estimates: those at indices
    # `[row-1, row, row+1, row+2]`. Their gaze samples are unavailable for
    # detection. The coupling to `_HALF_WINDOW` is deliberate: if the
    # estimator's window changes, the mask's excluded range changes with it.
    #
    # **This loop is unreachable in production today, and the reason is one
    # layer out** (whole-branch review, finding H2 -- recorded, deliberately
    # not fixed here). `timebase/extract.py::extract_ohdpi` raises on any
    # non-empty `frame_gaps`; `timebase/segments.py::scan_system` does not
    # catch it and `SystemTimebase.make()` wraps only `fit_rate`, so such a
    # session gets no `SystemTimebase` row for ohDPI, no `core.Segment`
    # follows, and `EyeValidity.key_source` -- which requires one -- never
    # names it. `EyeValidity.make()` can therefore only ever pass
    # `frame_gaps=()`, and this loop runs for real in
    # `tests/eye/detect/test_validity.py` alone. Kept rather than deleted:
    # the criterion is one of OpenIrisDPI's own five (design spec section 2),
    # its input genuinely exists at the `read_ohdpi` layer, and whether a
    # dropped frame should reject a whole recording is a timebase decision
    # being taken separately -- see design spec section 2 for that dependency
    # stated in full. The reference recording has zero gaps across 1,177,799
    # rows, so real data has not exercised it either.
    across_gap = np.zeros(n, dtype=bool)
    for gap in frame_gaps:
        across_gap[max(gap.row + 1 - _HALF_WINDOW, 0) : min(gap.row + _HALF_WINDOW + 1, n)] = True

    unusable = blink | outside | too_fast | across_gap
    unusable = _dilate(unusable, params.dilate_samples)
    # Bound to a name rather than folded straight into `unusable`, because
    # `_short_valid_epochs`' own docstring says it is returned as its own
    # mask "so the caller can see that these samples were dropped for being
    # SHORT rather than for a tracking failure". `fractions["short_epoch"]`
    # below is the caller finally doing that; until it existed, that
    # distinction was computed on every session and immediately discarded.
    short_epoch = _short_valid_epochs(unusable, params.min_epoch_samples)
    unusable = unusable | short_epoch

    # Walked in REVERSE precedence order, so the most specific label is
    # assigned last and wins. `MASK_PRECEDENCE` is what decides that, not the
    # order these two lines happen to be written in: reverse the tuple and
    # every doubly-claimed sample comes back `invalid` instead of `blink`
    # (`tests/eye/detect/test_validity.py::
    # test_mask_precedence_is_what_decides_which_criterion_wins` reverses it
    # and checks exactly that). A label added to the tuple with no criterion
    # beside it raises `KeyError` here rather than being silently skipped.
    claimed = {Label.BLINK: blink, Label.INVALID: unusable}
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
        # makes this line say the same kind of thing the four beside it say
        # -- a fraction OF A CRITERION, not of a rendered verdict.
        fractions={
            "blink": _fraction(blink),
            "out_of_region": _fraction(outside),
            "too_fast": _fraction(too_fast),
            "frame_gap": _fraction(across_gap),
            "short_epoch": _fraction(short_epoch),
        },
    )


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
