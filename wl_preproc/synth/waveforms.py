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
    # Neither scaling keyword is passed. `pyproject.toml` declares
    # `spikeinterface>=0.101` with no upper bound and no lockfile, so every
    # released version from there through the installed 0.104.8 is
    # legitimately in play, and the two spellings do not overlap:
    # `return_in_uV` does not exist before 0.103.0 (an unconditional
    # TypeError on 0.101.0-0.102.3), and `return_scaled` (the brief's
    # original spelling) is deprecated from 0.103.0 onward, not just 0.104.
    # Both default to "no scaling" on every version in range --
    # `return_scaled=False` through 0.102.x, `return_in_uV=False` from
    # 0.103.0 on -- and this synthetic recording never sets
    # gain_to_uV/offset_to_uV in the first place (`has_scaleable_traces()`
    # is False), so the keyword is a no-op here regardless. Omitting it
    # entirely is therefore correct across the whole declared range, not
    # just the version installed today, and it carries no DeprecationWarning
    # on any of them.
    traces = recording.get_traces(start_frame=0, end_frame=n_samples)
    return np.asarray(traces, dtype=np.float64)


TEMPLATE_MS_BEFORE = 1.0

# 2.0 ms (this task's original value) is too short for
# `generate_templates`'s own default `unit_params`, and this is fixed here
# rather than merely documented -- fix round 1 found it load-bearing, not
# cosmetic (see below).
#
# `generate_single_fake_waveform` asserts `nrefac + nrepol < nafter`: the
# repolarization and recovery phases must fit inside `ms_after`.
# `repolarization_ms` is drawn from `[0.5, 0.8)` ms and `recovery_ms` from
# `[1.0, 1.5)` ms (spikeinterface's `default_unit_params_range` -- unchanged
# for these two keys across every git tag checked from 0.101.0 through
# 0.104.0, so this is not version-fragile). At `sampling_rate_hz=30_000`,
# the supremum of `nrepol + nrefac` is `23 + 44 = 67` samples, which needs
# `nafter >= 68`; 2.0 ms gives only `nafter = 60`.
#
# Not a rare edge case: measured directly with `TEMPLATE_ALPHA_RANGE_UV`,
# `TEMPLATE_SPATIAL_DECAY_UM` and `TEMPLATE_MODE` already fixed below, a
# 100-seed sweep's crash rate rose from 28% at `n_units=1` to 100% at
# `n_units=20` -- `generate_templates` draws every unit's shape params in one
# seeded call, so one unit's ~28% own failure rate compounds across a
# session's worth of units. Phase 2b-2's own `SPATIAL_RECIPE` (Task 5) plants
# 12, where an unpatched 100-seed sweep crashed 61-75% of the time.
#
# 2.5 ms (`nafter = 75`) clears the analytic bound with an 8-sample margin --
# a mathematical guarantee, not a swept-and-hoped-for one: no draw of
# `repolarization_ms`/`recovery_ms` from their declared ranges can reach it,
# so this holds for every seed, not only the ones tested. Verified anyway at
# `n_units=12` across 200 seeds and `n_units=1/2/3/5/10/20` across 100 seeds
# each: 0 failures throughout (task-4-report.md, fix round 1).
TEMPLATE_MS_AFTER = 2.5

# **A recorded bias, not an oversight.** `generate_templates` is documented as
# "very naive: it generates a mono channel waveform ... and duplicates this same
# waveform on all channel given a simple decay law per unit" -- amplitude decays
# with distance, waveform SHAPE does not. Interpolating a dead channel is
# spatial averaging of its neighbours, so against these templates interpolation
# reconstructs a dead site almost perfectly. Every interpolation result measured
# on this fixture is therefore an UPPER BOUND, and spec section 3.4 requires
# that caveat to travel with any such number. Real templates
# (`fetch_template_object_from_database`) would remove the bias and need the
# network, which CI and offline reproducibility both rule out here.

# `generate_templates`'s own defaults for these two -- measured, not assumed,
# the same way NOISE_SPATIAL_DECAY_UM above was. `unit_params=None` draws
# "alpha" (peak amplitude at zero distance, in the a.u. this module treats as
# uV) uniform on 100-500, and "spatial_decay" uniform on 20-40 um pre-0.104,
# 10-45 from 0.104 on -- a range that already moved once inside the declared
# `spikeinterface>=0.101` floor, so trusting it ties this fixture's amplitude
# to whichever release happens to be installed.
#
# Swept 200+ draws of `place_units`' own placement (xy anywhere across the
# recorded span, z up to `units.py`'s `_MAX_DISTANCE_UM` = 60 um) through the
# raw defaults: worst-case peak amplitude at a unit's best channel came out at
# 0.3-6 uV -- *below* `NOISE_UV` = 8.0, the opposite of a footprint a sorter
# could use, and not one unlucky seed: the median across the same sweep was
# only 60-69 uV, barely above noise. That is what
# `test_rendered_traces_carry_the_planted_spikes_above_the_noise` caught.
#
# Re-swept the same way at the values below: worst case 91 uV, 5th percentile
# 203 uV, median 300-400 uV -- comfortably clear of the noise floor and in the
# range real best-channel EAP amplitudes occupy (the single-channel fixture
# this task replaces, `spikeglx.SPIKE_TEMPLATE_UV`, peaked at 200 uV). 60 um
# for the decay constant is deliberately `_MAX_DISTANCE_UM` again rather than
# an independent guess: a unit planted at the edge of where `place_units` is
# allowed to put it still registers a real, attenuated footprint instead of
# falling below noise entirely.
TEMPLATE_ALPHA_RANGE_UV = (500.0, 1200.0)
TEMPLATE_SPATIAL_DECAY_UM = 60.0

# The default `mode="ellipsoid"` draws a random per-unit orientation and
# squash, which can put some OTHER channel's warped distance ahead of the
# geometrically nearest one -- measured directly: with the two constants
# above and `place_units(sites, 1, default_rng(0))`'s own unit, ellipsoid
# mode's loudest channel was not the nearest one. `mode="sphere"` makes the
# per-channel distance isotropic (`get_ellipse` with a unit sphere, no random
# shape), and every recorded site shares the same z=0 while one unit's z is
# fixed across all of them, so `channel_factors = alpha * exp(-distance /
# spatial_decay)` alone ranks channels the same way plain xy distance does.
#
# That guarantee holds WITHIN this fixture's own constants, not in general --
# correcting an overclaim from this task's first pass ("provably the
# loudest", full stop). `generate_templates` also applies a distance-
# dependent `propagation_speed` delay by default, an FFT shift per channel
# that `channel_factors` does not account for; fix round 1 re-swept 300
# draws with alpha/decay moved outside `TEMPLATE_ALPHA_RANGE_UV`/
# `TEMPLATE_SPATIAL_DECAY_UM` and found 30 mismatches where the loudest
# channel was not the nearest one. At the constants actually shipped here it
# held over 500 independent draws, 0 mismatches (task-4-report.md, fix round
# 1). That is what
# `test_a_unit_appears_on_several_channels_with_amplitude_falling_off`'s
# second assertion checks: a wiring-regression guard (transposed axes, a
# channel/site mismatch) true at these constants -- not, on its own, evidence
# that the spatial footprint is physically correct. See that test's own
# comment.
#
# Sphere mode is not neutral to spec section 3.4's interpolation-bias caveat
# either -- it compounds it. Flattening the amplitude field to pure radial
# symmetry is the easiest possible pattern for a linear interpolator to
# reconstruct, more so than ellipsoid's already-naive per-channel decay law.
# Section 3.4's "every interpolation result measured on this fixture is an
# UPPER BOUND" applies doubly here, not just once.
TEMPLATE_MODE = "sphere"


def unit_templates(
    sites: list[dict],
    units: tuple[UnitTruth, ...],
    sampling_rate_hz: float,
    seed: int,
) -> np.ndarray:
    """One multi-channel template per unit, µV, `(n_units, n_samples, n_channels)`.

    **Indexed by `unit_id`, which `place_units` assigns as 0..n-1.** If unit ids
    ever stop being contiguous and zero-based, `render_traces` indexes the wrong
    template rather than failing, so the assumption is asserted here.
    """
    if [u.unit_id for u in units] != list(range(len(units))):
        raise ValueError(
            "unit ids must be contiguous from zero; render_traces indexes "
            f"templates by unit_id, and got {[u.unit_id for u in units]}"
        )
    from spikeinterface.generation import generate_templates

    channel_locations = np.array(
        [[s["x_coord"], s["y_coord"]] for s in sites], dtype=float
    )
    unit_locations = np.array(
        [[u.x_um, u.y_um, u.z_um] for u in units], dtype=float
    )
    return np.asarray(
        generate_templates(
            channel_locations=channel_locations,
            units_locations=unit_locations,
            sampling_frequency=sampling_rate_hz,
            ms_before=TEMPLATE_MS_BEFORE,
            ms_after=TEMPLATE_MS_AFTER,
            seed=seed,
            mode=TEMPLATE_MODE,
            unit_params={
                "alpha": TEMPLATE_ALPHA_RANGE_UV,
                "spatial_decay": TEMPLATE_SPATIAL_DECAY_UM,
            },
        ),
        dtype=np.float64,
    )


def render_traces(
    sites: list[dict],
    units: tuple,
    spikes: tuple[tuple[float, int], ...],
    n_samples: int,
    sampling_rate_hz: float,
    noise_uv: float,
    seed: int,
    time_offset_s: float,
    drift_ppm: float,
) -> np.ndarray:
    """Correlated noise with every planted spike summed onto it, in µV.

    `drift_ppm` and `time_offset_s` are applied here rather than by the caller
    so that a spike lands at the same session time in every emitter that renders
    the same ground truth.
    """
    from wl_preproc.synth.timeline import apply_drift

    traces = correlated_noise(sites, n_samples, sampling_rate_hz, noise_uv, seed)
    templates = unit_templates(sites, units, sampling_rate_hz, seed)
    before = int(TEMPLATE_MS_BEFORE * sampling_rate_hz / 1000.0)

    for time_s, unit_id in spikes:
        peak = int((apply_drift(time_s, drift_ppm) + time_offset_s) * sampling_rate_hz)
        start = peak - before
        stop = start + templates.shape[1]
        if start < 0 or stop >= n_samples:
            continue
        traces[start:stop, :] += templates[unit_id]
    return traces
