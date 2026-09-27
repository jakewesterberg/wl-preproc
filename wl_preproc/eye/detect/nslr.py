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

import warnings
from collections.abc import Callable
from dataclasses import dataclass

import numba
import numpy as np
import scipy.interpolate
import scipy.stats

from wl_preproc.eye.detect.labels import Label, Run, true_runs


@dataclass(frozen=True, slots=True)
class NslrParams:
    """Spec section 6, scalar fields only.
    - The first four are `slow_nslr.py`'s defaults (158-161, 174).
    - `max_noise_passes` is this implementation's guard; the reference has no
      cap.
    - `min_measured_saccade_ms` is this pipeline's measurement rule, not the
      reference's, and the detector never reads it.
      `schema/detect.py::_insert_trace` stores a per-eye saccade run shorter
      than this with no amplitude or peak velocity (the requester's decision
      of 2026-09-27, spec section 4). It is here, in the paramset, because
      it changes stored values, and the paramset hash is what addresses
      them.
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
    min_measured_saccade_ms: float
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
    # This pipeline's measurement rule, not the reference's: a saccade run
    # under 10 ms is stored unmeasured (the requester's decision, spec 4).
    min_measured_saccade_ms=10.0,
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


@dataclass(frozen=True, slots=True)
class PieceFit:
    """One piece's segmentation: `splits`, the endpoint `times` (`ts` at
    each split, the last at the final sample -- `slow_nslr.py` 151-156), and
    the fitted `endpoints`."""

    splits: list[int]
    times: np.ndarray
    endpoints: list[np.ndarray]


@dataclass(frozen=True, slots=True)
class NoiseFit:
    """Every piece's fit from the last pass, the pooled noise, the pass
    count, and whether `max_noise_passes` stopped the loop (spec 1.4, 6)."""

    pieces: list[PieceFit]
    noise: np.ndarray
    passes: int
    capped: bool


def fit_pieces(pieces: list[tuple[np.ndarray, np.ndarray]], params: NslrParams) -> NoiseFit:
    """`fit_gaze`, `slow_nslr.py` 174-191, over pieces (spec 1.4, 3).
    1. The noise starts at the pooled positions' standard deviation per axis.
    2. Each pass adds the structural error, segments and fits every piece,
       and sets the noise to the pooled residuals' standard deviation.
    3. It stops when a noise pair recurs exactly, or at `max_noise_passes`,
       keeping that pass.

    With one piece this is the reference, operation for operation."""
    noise = np.std(np.concatenate([xy for _, xy in pieces]), axis=0)
    seen = {tuple(noise)}
    structural = np.ones(noise.shape) * params.structural_error_deg
    passes = 0
    while True:
        noise += structural
        split = split_prior(np.mean(noise), params)
        fits: list[PieceFit] = []
        residuals = []
        for ts, xy in pieces:
            splits = segment(ts, xy, noise, split)
            ends = continuous_fit(ts, xy, splits)
            at = list(splits)
            at[-1] -= 1
            times = ts[at]
            fits.append(PieceFit(splits=splits, times=times, endpoints=ends))
            residuals.append(scipy.interpolate.interp1d(times, ends, fill_value="extrapolate", axis=0)(ts) - xy)
        passes += 1
        noise = np.std(np.concatenate(residuals), axis=0)
        if tuple(noise) in seen:
            return NoiseFit(pieces=fits, noise=noise, passes=passes, capped=False)
        if passes >= params.max_noise_passes:
            return NoiseFit(pieces=fits, noise=noise, passes=passes, capped=True)
        seen.add(tuple(noise))


def _fisher(cos: float) -> float:
    """`nslr_hmm.py` 305-306: scale off exactly +-1, then Fisher-transform."""
    cos *= 1 - 1e-6
    return np.arctanh(cos)


def features(piece: PieceFit) -> list[tuple[float, float]]:
    """`nslr_hmm.py` 292-311 (spec 1.5): each segment's `log10` speed (clipped
    at 1e-6) and its Fisher-transformed turn from the previous segment. The
    arrays have the reference's shapes, so every value is its value. The
    previous direction starts at (0, 0), and a NaN turn becomes 0."""
    previous = np.array([0.0, 0.0])
    out: list[tuple[float, float]] = []
    for k in range(len(piece.times) - 1):
        duration = np.diff((piece.times[k], piece.times[k + 1])).item()
        speed = np.diff((piece.endpoints[k], piece.endpoints[k + 1]), axis=0) / duration
        velocity = float(np.linalg.norm(speed))
        # A still segment's direction is 0/0. The NaN is the reference's
        # value; only numpy's warning about it is silenced.
        with np.errstate(invalid="ignore", divide="ignore"):
            direction = speed / velocity
            turn = _fisher(np.dot(direction, previous.T).item())
        if turn != turn:
            turn = 0.0
        out.append((np.log10(np.clip(velocity, 1e-6, None)), turn))
        previous = direction
    return out


def transition_matrix() -> np.ndarray:
    """`nslr_hmm.py` 53-62 (spec 1.6), rows and columns in state order
    fixation, saccade, PSO, pursuit. The paper's stated model, not an
    estimate:
    - equal weights, except fixation->PSO, PSO->saccade and pursuit->PSO are
      forbidden;
    - fixation<->pursuit carries half weight;
    - rows are normalised."""
    weights = np.ones((4, 4))
    weights[0, 2] = 0
    weights[2, 1] = 0
    weights[3, 2] = 0
    weights[3, 0] = 0.5
    weights[0, 3] = 0.5
    for row in range(4):
        weights[row] /= np.sum(weights[row])
    return weights


def _log10_clipped(values):
    return np.log10(np.clip(values, 1e-6, None))


def _later_emission(emission: np.ndarray) -> np.ndarray:
    """`nslr_hmm.py` 79: every emission after the first is normalised."""
    return emission / np.sum(emission)


def decode(feats: list[tuple[float, float]], params: NslrParams) -> list[int]:
    """`nslr_hmm.py` 36-93, 313-323 (spec 1.6): the Viterbi path over one
    piece's segments, in `log10`.
    - The emissions are bivariate Gaussians with the params' means and
      diagonal variances, in state order fixation, saccade, PSO, pursuit.
    - The start is uniform.
    - The first emission is used as it is; later ones are normalised."""
    blocks = [
        (params.fixation_log_speed_mean, params.fixation_turn_mean,
         params.fixation_log_speed_var, params.fixation_turn_var),
        (params.saccade_log_speed_mean, params.saccade_turn_mean,
         params.saccade_log_speed_var, params.saccade_turn_var),
        (params.pso_log_speed_mean, params.pso_turn_mean,
         params.pso_log_speed_var, params.pso_turn_var),
        (params.pursuit_log_speed_mean, params.pursuit_turn_mean,
         params.pursuit_log_speed_var, params.pursuit_turn_var),
    ]
    dists = [scipy.stats.multivariate_normal([m1, m2], [[v1, 0.0], [0.0, v2]]) for m1, m2, v1, v2 in blocks]
    emissions = [np.array([d.pdf(f) for d in dists]).T for f in feats]
    start = np.ones(4)
    start /= np.sum(start)
    log_transitions = _log10_clipped(transition_matrix())
    probs = _log10_clipped(emissions[0]) + _log10_clipped(start)
    back = []
    for emission in emissions[1:]:
        emission = _later_emission(emission)
        candidates = log_transitions + probs[:, None]
        best = np.argmax(candidates, axis=0)
        probs = _log10_clipped(emission) + candidates[best, np.arange(4)]
        back.append(best)
    path = [int(np.argmax(probs))]
    while back:
        path.append(int(back.pop()[path[-1]]))
    path.reverse()
    return path


def classify(pieces_xy: list[np.ndarray], fs_hz: float, params: NslrParams) -> list[np.ndarray]:
    """Every piece's per-sample states, 0 fixation, 1 saccade, 2 PSO and 3
    pursuit (spec 1, 3).
    - The noise is estimated once over all the pieces.
    - Each piece is decoded on its own, from its own first segment.
    - Every sample takes its segment's state.

    Within a piece, `t = index / fs_hz`. Each piece needs at least two
    samples; `detect_nslr` passes no shorter one.

    **A capped noise estimate warns.** If the noise pair never recurs within
    `max_noise_passes`, the last pass is kept (spec 6) and a `RuntimeWarning`
    says so, naming the pass count. The reference has no cap, so a capped
    fit is one the reference would not have finished."""
    if not pieces_xy:
        return []
    fit = fit_pieces([(np.arange(len(xy)) / fs_hz, xy) for xy in pieces_xy], params)
    if fit.capped:
        warnings.warn(
            f"NSLR's noise estimate did not repeat within max_noise_passes="
            f"{params.max_noise_passes}; the last of its {fit.passes} passes is kept "
            f"(noise {fit.noise.tolist()} deg)",
            RuntimeWarning,
            stacklevel=2,
        )
    out = []
    for piece in fit.pieces:
        path = decode(features(piece), params)
        states = np.empty(piece.splits[-1], np.int64)
        for k, state in enumerate(path):
            states[piece.splits[k]:piece.splits[k + 1]] = state
        out.append(states)
    return out


#: The states' labels, in `decode`'s order (spec 4).
_STATE_LABELS = (Label.FIXATION, Label.SACCADE, Label.PSO, Label.PURSUIT)


def detect_nslr(
    gaze_deg: np.ndarray,
    velocity_deg_s: np.ndarray,
    available: np.ndarray,
    fs_hz: float,
    params: NslrParams,
) -> list[Run]:
    """NSLR-HMM, as the registered `DetectFn`: maximal runs of one label,
    half-open, sorted (spec 3, 4).

    - **The mask splits pieces.** Every maximal stretch of samples the
      validity mask offers (`entry is None`) is a piece. No segment spans a
      gap. A one-sample piece is left unlabelled.
    - **So does non-finite gaze.** A sample whose gaze is NaN or infinite is
      withheld here even where the mask offers it. The noise is pooled over
      every piece of the eye (spec 3), so one NaN would make it NaN for the
      whole eye: the loop would then run to `max_noise_passes` and relabel
      pieces that hold no NaN at all. The shared mask passes NaN as usable
      (`validity.py`: `NaN > half-width` is false), for every detector; that
      is recorded for the requester, not changed here.
    - **The velocity is ignored.** NSLR never differentiates; a segment's
      slope is its velocity. The argument exists because every `DetectFn` has
      it.
    - **The conjunction.** The saccadic slice is `{saccade}`, so conjunction
      runs take `_conjunction_label`'s degenerate branch, and there is no
      minimum duration (spec 4)."""
    usable = np.array([entry is None for entry in available], dtype=bool)
    usable &= np.isfinite(np.asarray(gaze_deg, dtype=float)).all(axis=1)
    spans = [(int(a), int(b)) for a, b in true_runs(usable) if b - a >= 2]
    per_piece = classify([np.asarray(gaze_deg[a:b], dtype=float) for a, b in spans], fs_hz, params)
    runs: list[Run] = []
    for (offset, _stop), states in zip(spans, per_piece):
        change = np.flatnonzero(np.diff(states)) + 1
        starts = np.concatenate(([0], change))
        stops = np.concatenate((change, [len(states)]))
        runs.extend(
            Run(start=offset + int(s), stop=offset + int(e), label=_STATE_LABELS[int(states[s])])
            for s, e in zip(starts, stops)
        )
    return runs
