"""Kilosort spacing parameters, derived from the probe rather than defaulted.

**UNVALIDATED. Do not adopt these values without measuring first.**

This module was written to prevent a specific failure, and that failure turned
out not to exist. The original reasoning ran: KS4's `dminx` and
`max_channel_distance` both default to 32 um, its own parameter documentation
says that "should work well for Neuropixels 1 and Neuropixels 2 probes", this
lab's probe is NP1032 with columns 103 um apart, and so at the default a
channel in one column would never be compared with one in the other -- splitting
any unit straddling the gap.

**Measured 2026-08-27, and the last step is false.** Instrumenting
`template_centers()` and `nearest_chans()` on this probe's own geometry: at the
default, 158 of the 252 surviving templates draw channels from BOTH columns,
against 95 of 189 at the derived spacing. The surviving grid columns are
{0, 17.2, 85.8, 103}, and every template at 17.2 and 85.8 spans the gap --
`nearest_chans` gives its tenth slot to the far column at 85.8 um rather than to
a same-column site two rows further out at 101.5 um. The default bridges the
columns MORE than the derived spacing does. See design spec section 7's
2026-08-27 amendment.

**What is still true**: the parameter defaults are what they are, Kilosort's
docs really do scope them to NP1 and NP2, this probe is neither, and the
arithmetic below is sound. What is gone is any evidence that the derived values
sort BETTER. They differ -- the derived spacing puts a template at the 51.5 um
midpoint where the default has none, and widens `max_channel_distance` to
103 um, which against a ~60 um footprint decay may admit channels carrying
mostly noise -- and which is preferable is unmeasured in both directions.

So this stays as arithmetic worth having, and 2b-5 owns the comparison against
ground truth that would justify using it.
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
