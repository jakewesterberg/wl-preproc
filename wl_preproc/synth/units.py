"""What fires, when, and where it sits.

**This is ground truth and it is ours.** Nothing here consults SpikeInterface:
a unit's identity, its position and its spike times are planted by this module
and by `timeline.py`, so a test asserting recovery crosses a real boundary.
`waveforms.py` holds the borrowed half -- what a spike planted here looks like
once it reaches a channel -- and the two are separate files so the containment
is auditable rather than asserted. See `truth.py`'s own rule.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from wl_preproc.ephys.geometry import electrode_rows

class TwoProbePopulationsUnsupported(ValueError):
    """A recipe plants units and records through both ephys systems.

    Two probes sit in different brain locations and record two different
    populations; one `GroundTruth.units` cannot express that. Raised rather
    than picking a frame, because picking one silently positions the OTHER
    system's units against geometry they were never placed in -- which is the
    exact fault design spec section 11 item 7 records. Whoever first needs a
    dual-system session with units owns the two-population design.
    """


# Intan headstages carry no probe part number, so there is no offline table to
# read. A linear array is declared here rather than borrowed from Neuropixels:
# an RHS session on NP geometry would be a fixture describing hardware this lab
# does not have. Lives beside `place_units` rather than in `rhs.py` because
# `timeline.py` needs it to choose a frame, and importing `rhs.py` from
# `timeline.py` would close an import cycle -- `rhs.py` already imports
# `timeline.py`.
RHS_SITE_PITCH_UM = 50.0


def linear_sites(n_channels: int) -> list[dict]:
    """Rows in `electrode_rows`' shape, so `waveforms.py` needs no RHS branch."""
    return [
        {
            "electrode": index,
            "shank": 0,
            "shank_col": 0,
            "shank_row": index,
            "x_coord": 0.0,
            "y_coord": index * RHS_SITE_PITCH_UM,
        }
        for index in range(n_channels)
    ]


def recording_sites(recipe) -> list[dict]:
    """The sites this session's ephys system actually records through.

    **Placement must happen in the frame that will render.** Before this,
    `build_timeline` reached for `recipe.probe_part_number` unconditionally, so
    an RHS-only session had its units positioned against Neuropixels geometry
    and then rendered against a linear Intan array. On STIM_RECIPE that put all
    three units inside the top 16 um of an array spanning 150 um -- breaking
    `place_units`' own promise that a unit is bounded by the sites the session
    records. Design spec section 11 item 7.
    """
    systems = set(recipe.systems)
    if recipe.n_units and {"spikeglx", "rhs"} <= systems:
        raise TwoProbePopulationsUnsupported(
            f"{recipe.session_id} records through both spikeglx and rhs and "
            f"plants {recipe.n_units} units. Two probes are two populations; "
            "GroundTruth.units holds one. Give the recipe a single ephys "
            "system, or design the two-population model."
        )
    if "rhs" in systems and "spikeglx" not in systems:
        return linear_sites(recipe.n_ap_channels)
    return electrode_rows(recipe.probe_part_number)[: recipe.n_ap_channels]


# Absolute refractory period. A Poisson train without one produces intervals no
# neuron can make, and any ISI-violation metric computed against such a fixture
# measures the generator rather than the pipeline.
REFRACTORY_S = 0.002

# Firing rates are drawn from this range rather than fixed, so unit yield is not
# an artifact of every unit being equally easy to find.
_RATE_RANGE_HZ = (2.0, 20.0)

# How far a unit may sit from the plane of the shank. Zero would place every
# unit exactly on the probe, which makes amplitude a function of depth alone and
# removes the one axis a sorter uses to separate units at the same depth.
_MAX_DISTANCE_UM = 60.0


@dataclass(frozen=True, slots=True)
class UnitTruth:
    """One planted neuron. Position is in the probe's own coordinate frame --
    the same frame `ephys.geometry.electrode_rows` reports."""

    unit_id: int
    x_um: float
    y_um: float
    z_um: float
    firing_rate_hz: float


def place_units(sites: list[dict], n_units: int, rng: np.random.Generator) -> tuple[UnitTruth, ...]:
    """`n_units` neurons distributed across the span of the RECORDED sites.

    Bounded by the sites rather than by the probe: a unit outside the recorded
    span contributes nothing to any channel, so it would be ground truth that
    nothing could recover -- which scores as a miss and blames the pipeline for
    the fixture's choice.
    """
    xs = [s["x_coord"] for s in sites]
    ys = [s["y_coord"] for s in sites]
    return tuple(
        UnitTruth(
            unit_id=index,
            x_um=float(rng.uniform(min(xs), max(xs))),
            y_um=float(rng.uniform(min(ys), max(ys))),
            z_um=float(rng.uniform(-_MAX_DISTANCE_UM, _MAX_DISTANCE_UM)),
            firing_rate_hz=float(rng.uniform(*_RATE_RANGE_HZ)),
        )
        for index in range(n_units)
    )


def spike_train(unit: UnitTruth, duration_s: float, rng: np.random.Generator) -> list[float]:
    """Spike times for one unit, in session-time seconds.

    Exponential intervals with the refractory period added rather than rejected:
    rejection changes the effective rate by an amount that depends on the rate,
    so a 20 Hz unit and a 2 Hz unit would end up further apart than declared.
    Adding it shifts every interval by the same constant, which is what a real
    absolute refractory period does.
    """
    times: list[float] = []
    cursor = 0.0
    while True:
        cursor += REFRACTORY_S + float(rng.exponential(1.0 / unit.firing_rate_hz))
        if cursor >= duration_s:
            return times
        times.append(cursor)
