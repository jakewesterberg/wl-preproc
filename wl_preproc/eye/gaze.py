"""Canonical gaze, exposed as a computation and never as a stored array.

Gaze is a pure function of two durable things: the raw ohDPI `.txt` and a
calibration map (design spec section 5). Caching ~38 MB per session of
derived arrays would bloat the nightly MySQL dump that the parent spec makes
a first-class component, add a second storage path beside the archival one,
and -- worst -- create the possibility of a stored trace disagreeing with the
calibration it came from. So this module has no writer and no cache: every
function here re-reads the file it is given. Cost is dominated by that read
(~2.5 s on a real 1.18M-row recording); the basis product over those
points is milliseconds.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from wl_preproc.eye.calibration import CalibrationMap, apply_map
from wl_preproc.eye.ohdpi import read_columns

# DataQuality is exactly 50*P1_valid + 50*P4_valid (design spec section 1.1),
# so a frame below 100 is one the tracker itself declared a failure on --
# stated by the recording rather than inferred from missing values or a
# threshold on the signal.
#
# **Necessary, not sufficient.** An earlier version of this comment stopped at
# "stated, not inferred", which overclaims: OpenIrisDPI's own tutorial
# notebook is explicit that "OpenIrisDPI does not determine when the image
# processing algorithm has failed, so the user must find ways to be sure they
# only analyse epochs when the corneal reflection and P4 are tracked
# correctly." `DataQuality` reports that detection SUCCEEDED, not that it was
# CORRECT: P4 can be mis-detected from an aberrant glint or an occluding iris
# and still report a confident position at 100.
#
# The validity mask that closes the gap -- eye open, gaze in region, plausible
# speed, no frame discontinuity, invalid regions expanded and short epochs
# dropped -- belongs with saccade detection, which is what consumes it and
# which must run per valid epoch, since a velocity computed across a gap is a
# spurious saccade. It is deliberately not built here.
_FULL_TRACKING_QUALITY = 100


def _purkinje_columns(eye: str) -> list[str]:
    return [f"{eye}CR1X", f"{eye}CR1Y", f"{eye}CR4X", f"{eye}CR4Y"]


def purkinje_vector(path: Path, eye: str) -> np.ndarray:
    """P1 - P4 for one eye, shape (n_frames, 2).

    P1 lives in `CR1`, P4 in `CR4`; `CR2`, `CR3` and `CR5` are unused
    (identically zero in real recordings -- design spec section 1.1). Both
    Purkinje images move together under translation of the eye or camera, so
    their difference cancels that shared component and isolates rotation
    (design spec section 3.2).
    """
    cols = read_columns(path, _purkinje_columns(eye))
    return np.column_stack([
        cols[f"{eye}CR1X"] - cols[f"{eye}CR4X"],
        cols[f"{eye}CR1Y"] - cols[f"{eye}CR4Y"],
    ])


def gaze_trace(path: Path, eye: str, map_: CalibrationMap) -> np.ndarray:
    """Degrees of visual angle for one eye: `map_` applied to its Purkinje vector."""
    return apply_map(map_, purkinje_vector(path, eye))


def tracking_loss_fraction(path: Path, eye: str) -> float:
    """Fraction of frames whose `{eye}DataQuality` is below 100."""
    cols = read_columns(path, [f"{eye}DataQuality"])
    quality = cols[f"{eye}DataQuality"]
    return float(np.mean(quality < _FULL_TRACKING_QUALITY))
