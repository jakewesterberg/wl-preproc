"""What a planted unit looks like once it reaches a channel.

**This is the borrowed half, and the boundary is deliberate.** `units.py` holds
ground truth -- who fires and when -- and consults nothing. This module calls
`spikeinterface.generation` for waveform and noise physics we have no business
inventing. The two are separate files so that the containment spec section 3.3
requires is auditable: if this module ever plants ground truth, or `units.py`
ever imports SpikeInterface, the separation has been lost.

The format is never borrowed. `spikeglx.py` writes the bytes and
`read_spikeglx` reads them back as an independent oracle, so a bug in
SpikeInterface's model of a Neuropixels probe still fails loudly.
"""

from __future__ import annotations

import numpy as np
from probeinterface import Probe

from wl_preproc.synth.units import UnitTruth

# How fast noise decorrelates with distance, in microns. Passed to
# `generate_noise`, which builds the noise covariance as exp(-distance/decay).
# Without a value here the noise is independent per channel and no reference
# mode can be measured at all -- which is the state this module exists to end.
#
# 30 um -- NP1032's row pitch -- was the first value tried, and this module's
# own median-reference test measured it failing: `referenced.var() /
# noise.var()` came out at 0.96 against a threshold of 0.9. Sixty-four
# channels span ~620 um; correlation that decays within one row pitch gives a
# whole-array median almost nothing shared to remove. Swept decay against the
# real `generate_noise` covariance (not a toy model) and re-measured both of
# this module's test statistics at each value: the median-reference ratio
# only clears 0.9 once decay is at least ~60 um (0.92 at 50 um, 0.90 at 60
# um), and the near/far separation collapses back under the 0.1 margin once
# decay passes ~3500 um (0.12 at 3000 um, 0.09 at 4000 um) -- so both
# assertions bound this constant from opposite sides, not just one.
#
# 100 um sits centrally in that window, with margin on both sides (near/far
# separation ~0.80, median-reference ratio ~0.80, stable across ten seeds).
# It is not a bare curve-fit: it falls inside the 50-200 um annulus Power
# Pixels' `local_cmr` already treats as "local" on this exact hardware
# (design spec section 2.2's table, section 5.2's enum), so noise correlated
# over that same range is a physically defensible choice, not one tuned only
# to clear a threshold.
NOISE_SPATIAL_DECAY_UM = 100.0


def _probe_from_sites(sites: list[dict]) -> Probe:
    """A probeinterface `Probe` over exactly the recorded sites.

    Built from our own geometry rows rather than fetched, so the probe the
    generator plants against is the same object the .meta will describe.
    """
    positions = np.array([[s["x_coord"], s["y_coord"]] for s in sites], dtype=float)
    probe = Probe(ndim=2, si_units="um")
    probe.set_contacts(positions=positions, shapes="square", shape_params={"width": 12.0})
    probe.set_device_channel_indices(np.arange(len(sites)))
    return probe


def correlated_noise(
    sites: list[dict],
    n_samples: int,
    sampling_rate_hz: float,
    noise_uv: float,
    seed: int,
) -> np.ndarray:
    """Background noise in µV, shape `(n_samples, len(sites))`, correlated
    across space."""
    from spikeinterface.generation import generate_noise

    recording = generate_noise(
        probe=_probe_from_sites(sites),
        sampling_frequency=sampling_rate_hz,
        durations=[n_samples / sampling_rate_hz],
        noise_levels=noise_uv,
        spatial_decay=NOISE_SPATIAL_DECAY_UM,
        seed=seed,
    )
    # `return_scaled` (the brief's original spelling) is deprecated as of
    # 0.104 and slated for removal in 0.105 -- `pyproject.toml` pins
    # spikeinterface with no upper bound, so leaving it in place would break
    # on the next routine upgrade. `return_in_uV` is the same parameter under
    # its current name; both mean "do not apply gain_to_uV/offset_to_uV",
    # which this synthetic recording never sets in the first place.
    traces = recording.get_traces(start_frame=0, end_frame=n_samples, return_in_uV=False)
    return np.asarray(traces, dtype=np.float64)
