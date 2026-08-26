"""Kilosort spacing parameters, derived from the probe rather than defaulted.

**Why this lives at the reader seam and not in the sorting phase.** KS4's
`dminx` and `max_channel_distance` both default to 32 um, and its own parameter
documentation says that "should work well for Neuropixels 1 and Neuropixels 2
probes". This lab's probe is NP1032, whose two columns sit 103 um apart. At the
default, a channel in one column is never compared with any channel in the
other: a spike straddling both becomes two units, silently. The geometry is
known here, where the probe is read; leaving the constant to 2b-5 would put a
probe-dependent number in the phase furthest from the probe.
"""

from __future__ import annotations

import numpy as np

from wl_preproc.ephys.geometry import electrode_rows


def kilosort_spacing(part_number: str) -> dict[str, float]:
    """`dminx` and `max_channel_distance`, in microns, for `part_number`."""
    xs = np.unique([row["x_coord"] for row in electrode_rows(part_number)])
    steps = np.diff(xs)
    return {
        # The smallest real horizontal step: template centres closer together
        # than the sites themselves buy nothing.
        "dminx": float(steps.min()) if steps.size else 1.0,
        # Wide enough that the outermost columns are still compared, which is
        # the failure the default produces.
        "max_channel_distance": float(xs.max() - xs.min()) if xs.size > 1 else 32.0,
    }
