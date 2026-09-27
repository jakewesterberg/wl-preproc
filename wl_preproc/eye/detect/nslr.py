"""NSLR-HMM -- Pekkanen & Lappi's segmented-linear-regression detector: the
fifth registered, and the only one that never differentiates.

Reimplemented from the paper and from its authors' code, per design spec
`2026-09-27-nslr-design.md`:
- `gitlab.com/nslr/nslr` at 67d03f8, `nslr/slow_nslr.py`;
- `gitlab.com/nslr/nslr-hmm` at 3598fee, `nslr_hmm.py`.

Both are AGPL-3.0, so their behaviour is followed and their lines cited, and
their text is not copied (spec 4.1). The arithmetic mirrors the reference
operation for operation, because the noise estimate stops on an exact float
repeat (spec 1.4). The fidelity checks (spec 5.1) hold this module to
identical split indices, endpoints, features, Viterbi paths and labels.

Pekkanen, J., & Lappi, O. (2017). A new and general approach to signal
denoising and eye movement classification based on segmented linear
regression. Scientific Reports, 7, 17726. 10.1038/s41598-017-17983-x
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numba
import numpy as np


@dataclass(frozen=True, slots=True)
class NslrParams:
    """Spec section 6, scalar fields only.
    - The first four are `slow_nslr.py`'s defaults (158-161, 174).
    - `max_noise_passes` is this implementation's guard; the reference has no
      cap.
    - The sixteen emission fields are `nslr_hmm.py` 39-42's published means
      and diagonal variances, fitted on the Andersson et al. (2017) human-coded
      data. `turn` is the Fisher-transformed cosine between successive
      segments (spec 1.5).

    Durations are in milliseconds, the repository's convention.
    """

    structural_error_deg: float
    saccade_amplitude_deg: float
    slow_phase_duration_ms: float
    slow_phase_speed_deg_s: float
    max_noise_passes: int
    fixation_log_speed_mean: float
    fixation_turn_mean: float
    fixation_log_speed_var: float
    fixation_turn_var: float
    saccade_log_speed_mean: float
    saccade_turn_mean: float
    saccade_log_speed_var: float
    saccade_turn_var: float
    pso_log_speed_mean: float
    pso_turn_mean: float
    pso_log_speed_var: float
    pso_turn_var: float
    pursuit_log_speed_mean: float
    pursuit_turn_mean: float
    pursuit_log_speed_var: float
    pursuit_turn_var: float


DEFAULT_NSLR_PARAMS = NslrParams(
    structural_error_deg=0.1,
    saccade_amplitude_deg=3.0,
    slow_phase_duration_ms=300.0,
    slow_phase_speed_deg_s=5.0,
    max_noise_passes=50,  # this implementation's guard -- the reference has none
    fixation_log_speed_mean=0.6039844795867605,
    fixation_turn_mean=-0.7788440631929878,
    fixation_log_speed_var=0.1651734722683456,
    fixation_turn_var=1.5875256060544993,
    saccade_log_speed_mean=2.3259276064858194,
    saccade_turn_mean=1.1333265634427712,
    saccade_log_speed_var=0.080879690559802,
    saccade_turn_var=2.0718979621084372,
    pso_log_speed_mean=1.7511546389160744,
    pso_turn_mean=-1.817487032170937,
    pso_log_speed_var=0.0752678429860497,
    pso_turn_var=1.356411391040218,
    pursuit_log_speed_mean=0.8175021916433242,
    pursuit_turn_mean=0.3047120126632254,
    pursuit_log_speed_var=0.13334607025750783,
    pursuit_turn_var=2.5328705587328173,
)


def split_prior(noise_mean: float, params: NslrParams) -> Callable[[float], float]:
    """`slow_nslr.py` 158-172 (spec 1.2): the log-probability of starting a
    new segment after a step of `dt` seconds.

    Computed exactly as the reference computes it, on one-element numpy
    arrays, and memoised by `dt` -- a recording has few distinct steps, and
    this keeps every transcendental function in numpy, outside the compiled
    loop (spec 4.1)."""
    log, exp = np.log, np.exp
    slow_phase_s = params.slow_phase_duration_ms / 1000.0
    logit = (0.5 * (1.0 / noise_mean) + 0.5 * log(params.saccade_amplitude_deg * 2)
             + -1.0 * log(slow_phase_s * 2) + 0.1 * log(params.slow_phase_speed_deg_s * 2) + -3.0)
    memo: dict[float, float] = {}

    def likelihood(dt: float) -> float:
        value = memo.get(dt)
        if value is None:
            step = np.array([dt])
            lp = logit + -1.0 * log(1 / step)
            value = memo[dt] = float(log(1 / (1 + exp(-lp)))[0])
        return value

    return likelihood


@numba.njit
def _grown(values, capacity):
    out = np.empty(capacity, values.dtype)
    out[: values.shape[0]] = values
    return out


@numba.njit
def _segment_loop(ts, xs, ys, split_values, const, den_x, den_y, continuity):
    """`slow_nslr.py` 23-71 (spec 1.1), compiled (no fastmath).

    Each live hypothesis is a candidate last segment, carrying running sums
    per axis. They are held in parallel arrays and compacted in order on
    pruning, so "the first maximum" means what it means in the reference's
    list. `parent[i]` is the start index of the hypothesis that spawned the
    one starting at sample `i`.

    Only `+ - * /` and comparisons run here, and every `v**2` is written
    `v*v`, the numpy square the reference's arrays compute.
    `continuity=False` gives every child its own least-squares intercept --
    not NSLR; it exists for the null in `test_nslr.py`.

    Returns `(start index of the most likely final hypothesis, parent)`."""
    n = ts.shape[0]
    capacity = 64
    start = np.empty(capacity, np.int64)
    lik = np.empty(capacity)
    bx = np.empty(capacity)
    by = np.empty(capacity)
    ax = np.empty(capacity)
    ay = np.empty(capacity)
    count = np.empty(capacity, np.int64)
    elapsed = np.empty(capacity)
    st = np.empty(capacity)
    stt = np.empty(capacity)
    sx = np.empty(capacity)
    sy = np.empty(capacity)
    sxx = np.empty(capacity)
    syy = np.empty(capacity)
    stx = np.empty(capacity)
    sty = np.empty(capacity)
    rx = np.empty(capacity)
    ry = np.empty(capacity)
    free = np.empty(capacity, np.bool_)
    parent = np.full(n, -1, np.int64)
    # The root hypothesis: it fits its own intercept (slow_nslr.py 37-41).
    # Slots are initialised inline, here and for each child below, and not
    # through a helper closure: the arrays are rebound when they grow, and a
    # closure would keep writing to the old ones.
    start[0] = 0
    lik[0] = 0.0
    bx[0] = 0.0
    by[0] = 0.0
    ax[0] = 0.0
    ay[0] = 0.0
    count[0] = 0
    elapsed[0] = 0.0
    st[0] = 0.0
    stt[0] = 0.0
    sx[0] = 0.0
    sy[0] = 0.0
    sxx[0] = 0.0
    syy[0] = 0.0
    stx[0] = 0.0
    sty[0] = 0.0
    rx[0] = 0.0
    ry[0] = 0.0
    free[0] = True
    live = 1
    previous_t = ts[0]
    for i in range(n):
        t = ts[i]
        x = xs[i]
        y = ys[i]
        dt = t - previous_t
        previous_t = t
        for k in range(live):
            count[k] += 1
            elapsed[k] += dt
            tk = elapsed[k]
            st[k] += tk
            stt[k] += tk * tk
            sx[k] += x
            sy[k] += y
            sxx[k] += x * x
            syy[k] += y * y
            stx[k] += tk * x
            sty[k] += tk * y
            m = count[k]
            s1 = st[k]
            s2 = stt[k]
            if free[k]:  # slow_nslr.py 37-41
                d = s1 * s1 - m * s2
                if d != 0:
                    bx[k] = (s1 * stx[k] - s2 * sx[k]) / d
                    by[k] = (s1 * sty[k] - s2 * sy[k]) / d
                else:
                    bx[k] = sx[k] / m
                    by[k] = sy[k] / m
            b1 = bx[k]
            b2 = by[k]
            if s2 > 0.0:  # slow_nslr.py 44-48
                a1 = (stx[k] - b1 * s1) / s2
                a2 = (sty[k] - b2 * s1) / s2
                ax[k] = a1
                ay[k] = a2
                r1 = a1 * a1 * s2 + 2 * a1 * b1 * s1 - 2 * a1 * stx[k] + m * (b1 * b1) - 2 * b1 * sx[k] + sxx[k]
                r2 = a2 * a2 * s2 + 2 * a2 * b2 * s1 - 2 * a2 * sty[k] + m * (b2 * b2) - 2 * b2 * sy[k] + syy[k]
            else:
                r1 = 0.0
                r2 = 0.0
            # slow_nslr.py 51: the builtin sums start from 0.
            lik[k] = lik[k] + (const + (0.0 + (rx[k] - r1) / den_x + (ry[k] - r2) / den_y))
            rx[k] = r1
            ry[k] = r2
        if i == 0:  # slow_nslr.py 54
            continue
        winner = 0
        for k in range(1, live):
            if lik[k] > lik[winner]:
                winner = k
        child_lik = lik[winner] + split_values[i]
        child_x = elapsed[winner] * ax[winner] + bx[winner]  # slow_nslr.py 58
        child_y = elapsed[winner] * ay[winner] + by[winner]
        parent[i] = start[winner]
        kept = 0
        for k in range(live):  # slow_nslr.py 63
            if lik[k] > child_lik or k == winner:
                if kept != k:
                    start[kept] = start[k]
                    lik[kept] = lik[k]
                    bx[kept] = bx[k]
                    by[kept] = by[k]
                    ax[kept] = ax[k]
                    ay[kept] = ay[k]
                    count[kept] = count[k]
                    elapsed[kept] = elapsed[k]
                    st[kept] = st[k]
                    stt[kept] = stt[k]
                    sx[kept] = sx[k]
                    sy[kept] = sy[k]
                    sxx[kept] = sxx[k]
                    syy[kept] = syy[k]
                    stx[kept] = stx[k]
                    sty[kept] = sty[k]
                    rx[kept] = rx[k]
                    ry[kept] = ry[k]
                    free[kept] = free[k]
                kept += 1
        live = kept
        if live == capacity:
            capacity *= 2
            start = _grown(start, capacity)
            lik = _grown(lik, capacity)
            bx = _grown(bx, capacity)
            by = _grown(by, capacity)
            ax = _grown(ax, capacity)
            ay = _grown(ay, capacity)
            count = _grown(count, capacity)
            elapsed = _grown(elapsed, capacity)
            st = _grown(st, capacity)
            stt = _grown(stt, capacity)
            sx = _grown(sx, capacity)
            sy = _grown(sy, capacity)
            sxx = _grown(sxx, capacity)
            syy = _grown(syy, capacity)
            stx = _grown(stx, capacity)
            sty = _grown(sty, capacity)
            rx = _grown(rx, capacity)
            ry = _grown(ry, capacity)
            free = _grown(free, capacity)
        start[live] = i
        lik[live] = child_lik
        bx[live] = child_x
        by[live] = child_y
        ax[live] = 0.0
        ay[live] = 0.0
        count[live] = 0
        elapsed[live] = 0.0
        st[live] = 0.0
        stt[live] = 0.0
        sx[live] = 0.0
        sy[live] = 0.0
        sxx[live] = 0.0
        syy[live] = 0.0
        stx[live] = 0.0
        sty[live] = 0.0
        rx[live] = 0.0
        ry[live] = 0.0
        free[live] = not continuity
        live += 1
    best = 0
    for k in range(1, live):
        if lik[k] > lik[best]:
            best = k
    return start[best], parent


def segment(ts: np.ndarray, xy: np.ndarray, noise: np.ndarray,
            split: Callable[[float], float], *, _continuity: bool = True) -> list[int]:
    """Split indices for one piece (spec 1.1): `J[0] = 0`, `J[-1] = len(ts)`,
    and segment `k` is `[J[k], J[k+1])`.

    The likelihood constant (`slow_nslr.py` 51's first sum), the
    denominators `2*noise**2`, and each sample's split value are computed
    here in numpy, as the reference computes them, and handed to the compiled
    loop."""
    noise = np.asarray(noise, dtype=float)
    logs = np.log(1 / (np.sqrt(2 * np.pi) * noise))
    const = 0 + logs[0] + logs[1]
    den = 2 * noise**2
    steps = np.empty(len(ts))
    steps[0] = 0.0
    steps[1:] = ts[1:] - ts[:-1]
    unique, inverse = np.unique(steps[1:], return_inverse=True)
    split_values = np.zeros(len(ts))
    split_values[1:] = np.array([split(float(dt)) for dt in unique])[inverse]
    last, parent = _segment_loop(
        np.ascontiguousarray(ts, dtype=float),
        np.ascontiguousarray(xy[:, 0], dtype=float),
        np.ascontiguousarray(xy[:, 1], dtype=float),
        split_values, float(const), float(den[0]), float(den[1]), _continuity,
    )
    splits = [len(ts)]
    node = int(last)
    while True:
        splits.append(node)
        if node == 0:
            break
        node = int(parent[node])
    return splits[::-1]


def continuous_fit(ts: np.ndarray, xy: np.ndarray, splits: list[int]) -> list[np.ndarray]:
    """`slow_nslr.py` 75-124 (spec 1.3): the least-squares continuous
    piecewise-linear fit for `splits`, as the positions at the segment
    endpoints. It is a tridiagonal system, solved forward then backward, on
    the reference's array shapes (`ts` as a column)."""
    column = ts.reshape(-1, 1)
    rows = []
    mw0 = 0.0
    ww0 = 0.0
    xw0 = 0.0
    for k in range(len(splits) - 1):
        t = column[splits[k]:splits[k + 1]]
        x = xy[splits[k]:splits[k + 1]]
        span = t[-1] - t[0]
        if span == 0:
            span = 1.0
        w = (t - t[0]) / span
        m = 1 - w
        mw1 = (m * w).sum()
        mm1 = (m * m).sum()
        xm1 = (x * m).sum(axis=0)
        rows.append((mw0, mm1 + ww0, mw1, xm1 + xw0))
        mw0 = mw1
        ww0 = (w * w).sum()
        xw0 = (x * w).sum(axis=0)
    rows.append((mw0, 0.0 + ww0, 0.0, 0.0 + xw0))
    sweep = [(0.0, 0.0)]
    for p0, p1, p2, y in rows:
        b, g = sweep[-1]
        denom = p0 * g + p1
        sweep.append(((y - p0 * b) / denom, -p2 / denom))
    endpoint = 0.0
    ends = []
    for b, g in reversed(sweep[1:]):
        endpoint = g * endpoint + b
        ends.append(endpoint)
    return ends[::-1]
