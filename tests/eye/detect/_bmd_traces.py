"""Traces from BMD's own generative model (design spec
`2026-09-27-bmd-design.md` section 1.1), for tests.

This repository's own simulator of the paper's model, with numpy's
generator. It is not the authors' `sim_data_create.m`, which is MATLAB and
draws with MATLAB's generator.
- State durations are Gamma(2, 1/lambda) samples, at least one.
- Speeds follow the generalized gamma: r = sigma * sqrt(Gamma((d + 1)/2, 2)).
  The direction is uniform.
- Position is a random walk (sigma_z) plus the state's constant velocity.
- The measurement adds sigma_x.

Units are per sample, as the reference's are."""

from __future__ import annotations

import numpy as np


def simulate(length: int, *, lambda0: float, lambda1: float, sigma0: float, sigma1: float,
             d1: float, sigmaz: float, sigmax: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """`(x, state)`: the measured trace `(length, 2)` and the true state per
    sample (1 is a microsaccade). It starts in drift."""
    rng = np.random.default_rng(seed)
    state = np.zeros(length, dtype=np.int64)
    t, current = 0, 0
    while t < length:
        rate = lambda0 if current == 0 else lambda1
        duration = max(1, int(round(rng.gamma(2.0, 1.0 / rate))))
        state[t:t + duration] = current
        t += duration
        current = 1 - current
    z = np.zeros((length, 2))
    velocity = np.zeros(2)
    for i in range(length):
        if i == 0 or state[i] != state[i - 1]:
            d, sigma = (1.0, sigma0) if state[i] == 0 else (d1, sigma1)
            r = sigma * np.sqrt(rng.gamma((d + 1.0) / 2.0, 2.0))
            theta = rng.uniform(0.0, 2.0 * np.pi)
            velocity = r * np.array([np.cos(theta), np.sin(theta)])
        previous = z[i - 1] if i else np.zeros(2)
        z[i] = previous + velocity + rng.normal(0.0, sigmaz, 2)
    return z + rng.normal(0.0, sigmax, (length, 2)), state


#: The paper's simulated-data parameters (Comparison of algorithms on
#: simulated data), per millisecond: sigma0 = 0.3 deg/s, d1 = 4.4, sigma1 =
#: 30 deg/s. The noise levels here are this repository's choice.
PAPER_1KHZ = dict(lambda0=0.004, lambda1=0.1, sigma0=0.0003, sigma1=0.03, d1=4.4)
