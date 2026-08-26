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
