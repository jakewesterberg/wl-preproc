"""Kilosort spacing parameters, derived from the probe rather than defaulted.

**Why this lives at the reader seam and not in the sorting phase.** KS4's
`dminx` and `max_channel_distance` both default to 32 um, and its own parameter
documentation says that "should work well for Neuropixels 1 and Neuropixels 2
probes". This lab's probe is NP1032, whose two columns sit 103 um apart. At the
default, a channel in one column is never compared with any channel in the
other -- the mechanism by which a spike straddling both COULD become two units,
confirmed directly in Kilosort's own source. Whether it does in practice is
narrower than that (design spec section 7's 2026-08-26 amendment). The geometry
is known here, where the probe is read; leaving the constant to 2b-5 would put
a probe-dependent number in the phase furthest from the probe.
"""

from __future__ import annotations

import numpy as np

from wl_preproc.ephys.geometry import electrode_rows


class MultiShankSpacingUnsupported(NotImplementedError):
    """A probe with more than one shank: per-shank derivation is unimplemented.

    Raised rather than pooling every shank's x-coordinates into one answer.
    KS4's own `template_centers()` (`kilosort/spikedetect.py`) builds its
    candidate grid per shank, and `ephys.geometry.electrode_rows` already
    numbers columns within a shank for the same reason (its own docstring).
    Pooling across shanks would silently derive a spacing wide enough to
    compare channels on separate silicon -- on NP2010 (4 shanks, 250 um shank
    pitch) that is `max_channel_distance=782.0`, measured directly against
    `electrode_rows`. No caller reaches this today: every probe this lab runs
    (NP1032, NP1030, NP1022, NP1015) is single-shank.
    """


def kilosort_spacing(part_number: str) -> dict[str, float]:
    """`dminx` and `max_channel_distance`, in microns, for `part_number`."""
    rows = electrode_rows(part_number)
    if len({row["shank"] for row in rows}) > 1:
        raise MultiShankSpacingUnsupported(
            f"{part_number!r} has more than one shank; kilosort_spacing only "
            "derives dminx/max_channel_distance for a single shank today -- "
            "per-shank derivation is unimplemented."
        )
    xs = np.unique([row["x_coord"] for row in rows])
    steps = np.diff(xs)
    return {
        # The smallest real horizontal step: template centres closer together
        # than the sites themselves buy nothing.
        "dminx": float(steps.min()) if steps.size else 1.0,
        # Wide enough that the outermost columns are still compared, which is
        # the failure the default produces.
        "max_channel_distance": float(xs.max() - xs.min()) if xs.size > 1 else 32.0,
    }
