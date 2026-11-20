"""Synthetic strobe streams for the strobe matcher's tests: one strobe line,
sampled by the NI and by an Intan, each on its own clock (amendment 49; the
2026-10-30 spike's `simulate`).

The rig strobes bursts of words 1 ms apart. Each burst is one to `burst_max`
words. Bursts come at irregular gaps, on a timer, or at fixed delays within
trials that start on the animal. Both devices quantize the same true edges to
their own sample rate:

* **the NI** on an identity clock from its first sample;
* **Intan** on its own offset and rate, over a window of the session, with
  edges dropped and spurious edges added.

Every true edge has an id. A matched pair is right when both sides' ids agree.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

#: The NI's first sample, in true seconds, when it is not started late.
_NI_ORIGIN_S = -3.21
#: Intan's first sample comes this long before the first edge it can record.
_INTAN_LEAD_S = 0.25


@dataclass(frozen=True)
class StrobeScenario:
    """One simulated session. Times are true seconds from the rig's first
    possible strobe; the first burst is at 1 s."""

    hours: float = 1.0
    #: Words per burst, drawn from 1..burst_max, 1 ms apart.
    burst_max: int = 8
    #: The mean gap between bursts, when they are neither periodic nor in trials.
    mean_gap_s: float = 0.3
    #: Bursts every `periodic_s`, each gap off by `jitter_s` (a random walk).
    periodic_s: float | None = None
    jitter_s: float = 0.0005
    #: Bursts at these delays within a trial (each off by `jitter_s`); a trial
    #: starts after the last burst plus 1 s plus an exponential wait of mean 1 s.
    trial_offsets: tuple[float, ...] = ()
    #: Intan's rate relative to the NI's.
    rel_ppm: float = 60.0
    #: Intan's rate steps by this much at `rate_step_at_s` (default: halfway), as
    #: a real crystal's wanders.
    rate_step_ppm: float = 0.0
    rate_step_at_s: float | None = None
    #: The fraction of true edges Intan misses.
    drop: float = 0.0
    #: Spurious Intan edges, per real edge it recorded.
    extra: float = 0.0
    intan_late_s: float = 10.0
    intan_early_end_s: float = 10.0
    #: The NI starts this long after its default start, missing those edges.
    ni_late_s: float = 0.0
    #: The fraction of true edges the NI misses after `ni_drop_after_s`.
    ni_drop: float = 0.0
    ni_drop_after_s: float = 0.0
    fs_ni: float = 30_000.0
    fs_intan: float = 30_000.0
    seed: int = 1


def _true_edges(sc: StrobeScenario, rng: np.random.Generator) -> np.ndarray:
    span = sc.hours * 3600
    starts, t = [], 1.0
    while t < span:
        starts.append(t)
        if sc.trial_offsets:
            for offset in sc.trial_offsets[1:]:
                starts.append(t + offset + rng.normal(0, sc.jitter_s))
            t += sc.trial_offsets[-1] + 1.0 + rng.exponential(1.0)
        elif sc.periodic_s:
            t += sc.periodic_s + rng.normal(0, sc.jitter_s)
        else:
            t += rng.exponential(sc.mean_gap_s) + 0.01
    edges = [s + 0.001 * np.arange(rng.integers(1, sc.burst_max + 1)) for s in starts]
    return np.sort(np.concatenate(edges))


def simulate_with_reference_id(
    sc: StrobeScenario,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, Callable[[np.ndarray], np.ndarray]]:
    """`(ni_s, intan_s, true_id, reference_id, truth_map)`.

    * `ni_s`: the NI's edges, seconds from its first sample.
    * `intan_s`: Intan's edges, seconds from its first sample.
    * `true_id`: each Intan edge's true id, `-1` for a spurious edge.
    * `reference_id`: each NI edge's true id.
    * `truth_map`: Intan seconds to NI seconds, exactly.
    """
    rng = np.random.default_rng(sc.seed)
    true = _true_edges(sc, rng)
    ids = np.arange(true.size)
    span = sc.hours * 3600

    ni_origin = _NI_ORIGIN_S + sc.ni_late_s
    on_ni = true >= ni_origin
    if sc.ni_drop:
        on_ni &= (true < sc.ni_drop_after_s) | (rng.random(true.size) >= sc.ni_drop)
    ni_s = np.floor((true[on_ni] - ni_origin) * sc.fs_ni) / sc.fs_ni
    reference_id = ids[on_ni]

    keep = (true > sc.intan_late_s) & (true < span - sc.intan_early_end_s) & (rng.random(true.size) >= sc.drop)
    t_in, id_in = true[keep], ids[keep]
    n_extra = rng.binomial(t_in.size, sc.extra)
    if n_extra:
        t_in = np.concatenate([t_in, rng.uniform(t_in[0], t_in[-1], n_extra)])
        id_in = np.concatenate([id_in, -np.ones(n_extra, dtype=int)])
    order = np.argsort(t_in, kind="stable")
    t_in, id_in = t_in[order], id_in[order]
    intan_origin = sc.intan_late_s - _INTAN_LEAD_S
    scale = 1 + sc.rel_ppm * 1e-6
    if not sc.rate_step_ppm:
        intan_s = np.floor((t_in - intan_origin) * scale * sc.fs_intan) / sc.fs_intan

        def truth_map(x: np.ndarray) -> np.ndarray:
            return np.asarray(x) / scale + intan_origin - ni_origin

        return ni_s, intan_s, id_in, reference_id, truth_map

    step_s = span / 2 if sc.rate_step_at_s is None else sc.rate_step_at_s
    after = 1 + (sc.rel_ppm + sc.rate_step_ppm) * 1e-6
    step_intan_s = (step_s - intan_origin) * scale
    on_intan = np.where(
        t_in < step_s, (t_in - intan_origin) * scale, step_intan_s + (t_in - step_s) * after
    )
    intan_s = np.floor(on_intan * sc.fs_intan) / sc.fs_intan

    def truth_map(x: np.ndarray) -> np.ndarray:
        x = np.asarray(x)
        true_s = np.where(x < step_intan_s, x / scale + intan_origin, step_s + (x - step_intan_s) / after)
        return true_s - ni_origin

    return ni_s, intan_s, id_in, reference_id, truth_map


def simulate(
    sc: StrobeScenario,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, Callable[[np.ndarray], np.ndarray]]:
    """`(ni_s, intan_s, true_id, truth_map)`: `simulate_with_reference_id`
    without the NI's ids, which are its indices offset by the edges it
    started too late to see."""
    ni_s, intan_s, true_id, _reference_id, truth_map = simulate_with_reference_id(sc)
    return ni_s, intan_s, true_id, truth_map


def periodic(period_s: float, *, jitter: float, burst_max: int, **fields) -> tuple[np.ndarray, np.ndarray]:
    """`(ni_s, intan_s)` for bursts every `period_s`: the stream that cannot
    anchor, because every burst looks like every other."""
    ni_s, intan_s, _, _ = simulate(
        StrobeScenario(periodic_s=period_s, jitter_s=jitter, burst_max=burst_max, **fields)
    )
    return ni_s, intan_s
