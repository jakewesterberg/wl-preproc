"""The validity mask -- OpenIrisDPI's own five criteria.

**None of them involves a detector** (design spec section 2), which is why
this is its own module and, downstream, its own table with its own paramset:
three detectors running against three different masks would make the agreement
metric compare masks as well as detections, measuring the thing it exists to
hold constant.

Returns `None` for a sample a detector may label, and a `Label` for one it may
not. Precedence is enforced HERE, by not offering the sample at all, rather
than by asking every detector to respect an ordering.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.ohdpi import FrameGap

# `EyeQuality`'s own `_FULL_TRACKING_QUALITY`, restated rather than imported --
# it is private there, and the value belongs to the frozen recording format
# rather than being a choice free to drift between the two places it is read.
#
# NECESSARY, NOT SUFFICIENT: OpenIrisDPI "does not determine when the image
# processing algorithm has failed", so a frame at 100 is one the tracker did
# not declare a failure on, which is not the same as one it tracked correctly.
_FULL_TRACKING_QUALITY = 100.0


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


def validity_labels(
    gaze_deg: np.ndarray,
    velocity_deg_s: np.ndarray,
    data_quality: np.ndarray,
    frame_gaps: Sequence[FrameGap],
    params: ValidityParams,
) -> np.ndarray:
    """One entry per sample: a `Label` where the sample is unusable, `None`
    where a detector may label it."""
    n = gaze_deg.shape[0]
    blink = data_quality < _FULL_TRACKING_QUALITY

    outside = (np.abs(gaze_deg[:, 0]) > params.region_half_width_deg) | (
        np.abs(gaze_deg[:, 1]) > params.region_half_height_deg
    )
    too_fast = np.hypot(velocity_deg_s[:, 0], velocity_deg_s[:, 1]) > params.max_speed_deg_s

    # A gap sits between rows `row` and `row + 1`; both estimates span the
    # discontinuity, so both go. `read_ohdpi` reports gaps rather than refusing
    # the recording precisely so this can happen here (design spec section 2).
    across_gap = np.zeros(n, dtype=bool)
    for gap in frame_gaps:
        across_gap[max(gap.row, 0) : min(gap.row + 2, n)] = True

    unusable = blink | outside | too_fast | across_gap
    unusable = _dilate(unusable, params.dilate_samples)
    unusable |= _short_valid_epochs(unusable, params.min_epoch_samples)

    out = np.full(n, None, dtype=object)
    out[unusable] = Label.INVALID
    # Assigned LAST so it wins: a blink is a specific reason and `invalid` the
    # generic one, and generic-first would mean no sample is ever a blink.
    out[blink] = Label.BLINK
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
    failure -- two different facts that must not render identically."""
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
