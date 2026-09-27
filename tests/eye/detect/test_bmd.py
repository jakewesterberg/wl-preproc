"""BMD in the pipeline (design spec `2026-09-27-bmd-design.md` sections 3 and
4): the saccade gate, the stretches, the blocks, the preprocessing, the
labels. The sampler itself is held to the authors' code in
`test_bmd_fidelity.py`. These tests use a light sampler (few sweeps, samples
and iterations) because what they pin is the plumbing around it."""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from tests.eye.detect._bmd_traces import simulate
from wl_preproc.eye.detect import bmd
from wl_preproc.eye.detect.bmd_table import compute_table
from wl_preproc.eye.detect.engbert_kliegl import EngbertKlieglParams, detect_engbert_kliegl
from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.velocity import velocity

FS = 500.0
LIGHT = dataclasses.replace(bmd.DEFAULT_BMD_PARAMS, burn_in_sweeps=4, samples=4, iterations=2)


def _open(n):
    return np.full(n, None, dtype=object)


def _drifting_trace(seconds, seed, steps=()):
    """Random-walk drift and measurement noise at the reference recording's
    ratio (`synth/ohdpi.py::FIXATIONAL_DRIFT_PX_PER_SQRT_FRAME`), in degrees,
    plus 16 ms ramps of the given amplitude at the given times."""
    rng = np.random.default_rng(seed)
    n = int(seconds * FS)
    x = np.cumsum(rng.normal(0.0, 0.012, (n, 2)), axis=0) + rng.normal(0.0, 0.03, (n, 2))
    for at_s, amplitude in steps:
        on = int(at_s * FS)
        x[on:on + 8, 0] += np.linspace(amplitude / 8, amplitude, 8)
        x[on + 8:, 0] += amplitude
    return x


# -- section 3.2: stretches and blocks -------------------------------------------


def test_stretches_are_the_usable_samples_no_saccade_covers():
    usable = np.ones(100, dtype=bool)
    usable[40:45] = False
    gate = np.zeros(100, dtype=bool)
    gate[70:80] = True
    assert bmd.fixation_stretches(usable, gate, 10) == [(0, 40), (45, 70), (80, 100)]
    assert bmd.fixation_stretches(usable, gate, 21) == [(0, 40), (45, 70)]


def test_blocks_close_at_the_block_length_and_a_short_remainder_joins_the_last():
    stretches = [(0, 40), (50, 90), (100, 140), (150, 160)]
    assert bmd.blocks(stretches, 80) == [[(0, 40), (50, 90)], [(100, 140), (150, 160)]]
    assert bmd.blocks(stretches, 60) == [[(0, 40), (50, 90)], [(100, 140), (150, 160)]]
    assert bmd.blocks(stretches, 1000) == [stretches]
    assert bmd.blocks([], 60) == []


def test_the_remainder_stands_alone_when_it_is_at_least_half_a_block():
    assert bmd.blocks([(0, 60), (70, 100)], 60) == [[(0, 60)], [(70, 100)]]


# -- section 1.2: preprocessing --------------------------------------------------


def test_the_isotropy_ratio_is_preprocess_data_m_pooled():
    """`preprocess_data.m` 13-20: sqrt(median of squared second differences)
    per axis, pooled over the block's stretches."""
    rng = np.random.default_rng(1)
    pieces = [np.column_stack([rng.normal(0, 0.02, 300), rng.normal(0, 0.01, 300)]) for _ in range(3)]
    ax = np.concatenate([np.diff(p[:, 0], 2) for p in pieces])
    ay = np.concatenate([np.diff(p[:, 1], 2) for p in pieces])
    assert bmd.isotropy_ratio(pieces) == math.sqrt(np.median(ax * ax)) / math.sqrt(np.median(ay * ay))


def test_a_still_axis_is_left_unscaled():
    """Review Focus: a perfectly still trace has no noise to equalise."""
    assert bmd.isotropy_ratio([np.zeros((100, 2))]) == 1.0


def test_the_origin_is_the_position_before_the_first_sample():
    """The paper's x0 = x1 - epsilon becomes 0 (spec 1.2)."""
    piece = np.array([[3.0, -2.0], [3.5, -2.5]])
    shifted = bmd.to_origin(piece)
    assert np.allclose(shifted[0], [bmd.ORIGIN_EPSILON_DEG, bmd.ORIGIN_EPSILON_DEG])
    assert np.allclose(shifted[1] - shifted[0], piece[1] - piece[0])


# -- section 3.4: from samples to labels -----------------------------------------


def test_labels_threshold_at_or_above_and_carry_mean_probability():
    p = np.array([0.0, 0.5, 0.75, 0.25, 0.0])
    runs = bmd._labelled(p, 10, 0.5)
    fast = [r for r in runs if r.label is Label.MICROSACCADE]
    assert [(r.start, r.stop) for r in fast] == [(11, 13)]
    assert fast[0].reliability == 0.625
    assert sorted((r.start, r.stop) for r in runs if r.label is Label.DRIFT) == [(10, 11), (13, 15)]


def test_the_end_runs_are_never_detections():
    """Every sample's first and last state-1 runs are the representation's
    fixed ends (spec 1.7), so the first and last samples of every stretch
    have probability 0."""
    x = _drifting_trace(2.0, 3)
    result = bmd.run_block([bmd.to_origin(x)], drift_rate=4.0 / FS, microsaccade_rate=100.0 / FS,
                           drift_cap=1.3 / FS, microsaccade_cap=100.0 / FS, seed=5, table=compute_table(),
                           burn_in=4, samples=4, iterations=2)
    assert result.probability[0][0] == 0.0 and result.probability[0][-1] == 0.0


# -- section 3.2: pooling --------------------------------------------------------


def _pooled_block(seed):
    """Three stretches of BMD's own model, prepared as `run_block` prepares
    them, with two kept samples per stretch: the starting state, and the
    state after three sweeps."""
    from wl_preproc.eye.detect import bmd_rng

    pieces = [bmd.to_origin(simulate(n, lambda0=0.004, lambda1=0.1, sigma0=0.0003, sigma1=0.03, d1=4.4,
                                     sigmaz=0.002, sigmax=0.01, seed=seed + k)[0])
              for k, n in enumerate((400, 700, 550))]
    x = np.ascontiguousarray(np.concatenate([p.T for p in pieces], axis=1))
    lengths = np.array([len(p) for p in pieces], np.int64)
    offsets = np.concatenate(([0], np.cumsum(lengths)[:-1])).astype(np.int64)
    table = compute_table()
    th = bmd._theta(2.5, 0.001, 0.02, 0.003, 0.01, 0.004, 0.1)
    z = np.empty_like(x)
    sdz2 = np.array([bmd._smooth(x, z, offsets[s], lengths[s], th[bmd.SZ], th[bmd.SX]) for s in range(3)])
    gen = bmd_rng.new_generator(seed)
    kept = [[], []]
    for s in range(3):
        t01, t10 = np.zeros(lengths[s] + 2, np.int64), np.zeros(lengths[s] + 2, np.int64)
        count = bmd._start(x, offsets[s], lengths[s], t01, t10)
        kept[0].append((t01[:count[0]].copy(), t10[:count[0]].copy(), count.copy()))
        for _ in range(3):
            bmd._sweep(gen, t01, t10, count, th, z, offsets[s], lengths[s], table.values, table.alpha_lower,
                       table.alpha_upper, table.n_d)
        kept[1].append((t01[:count[0]].copy(), t10[:count[0]].copy(), count.copy()))
    return x, lengths, offsets, table, th, z, sdz2, kept


def _padded(kept):
    width = max(int(c[0]) for sample in kept for _, _, c in sample)
    t01s = np.zeros((len(kept), 3, width), np.int64)
    t10s = np.zeros((len(kept), 3, width), np.int64)
    ns = np.zeros((len(kept), 3), np.int64)
    for j, sample in enumerate(kept):
        for s, (t01, t10, count) in enumerate(sample):
            n = int(count[0])
            t01s[j, s, :n], t10s[j, s, :n], ns[j, s] = t01, t10, n
    return t01s, t10s, ns


def test_pooled_noise_sums_every_stretchs_lag_sums_before_the_fit():
    """Spec 5.2: the pooled estimate is the reference's fit to the lag sums
    and pair counts summed over the stretches. One stretch's lag sums are the
    reference's (held exactly by `test_bmd_fidelity.py`). The fit is restated
    here from `bmd.cpp` 748-760."""
    x, lengths, offsets, *_, kept = _pooled_block(31)
    got = bmd._noise(x, offsets, lengths, kept)
    sz_est, sx_est = [], []
    for sample in kept:
        sums, pairs = np.zeros(20), np.zeros(20, np.int64)
        for s, (t01, t10, count) in enumerate(sample):
            own_sums, own_pairs = np.zeros(20), np.zeros(20, np.int64)
            bmd._lag_sums(x, offsets[s], lengths[s], t01, t10, count[0], own_sums, own_pairs)
            sums += own_sums
            pairs += own_pairs
        lags = np.arange(1, 20)
        c = sums[1:] / pairs[1:]
        slope = ((lags * c).mean() - c.mean() * 10.0) / (20 * 39 / 6.0 - 100.0)
        intercept = c.mean() - slope * 10.0
        sz_est.append(math.sqrt(slope / 2.0))
        sx_est.append(math.sqrt(max(0.00001, intercept) / 4.0))
    half = len(kept) // 2
    assert got == pytest.approx((sorted(sz_est)[half], sorted(sx_est)[half]), rel=1e-9)


def test_the_pooled_speed_grid_search_maximises_the_posterior_summed_over_stretches():
    """Spec 5.2: for each kept sample, `_grid_up`'s choice is the first grid
    point that maximises the log posterior summed over every stretch, each
    point's settings made by `_set_up` (the reference's
    `set_d_sigma_up`)."""
    _, _, offsets, table, th, z, sdz2, kept = _pooled_block(32)
    t01s, t10s, ns = _padded(kept)
    lgamma_d = np.array([bmd._lgamma(0.5 * (d + 1)) for d in bmd._D_GRID])
    g1_d = np.array([bmd._tgamma(0.5 * (d + 1.0)) for d in bmd._D_GRID])
    g3_d = np.array([bmd._tgamma(0.5 * (d + 3.0)) for d in bmd._D_GRID])
    best_s, best_d = bmd._grid_up(t01s, t10s, ns, th, z, offsets, sdz2, table.values, table.alpha_lower,
                                  table.alpha_upper, table.n_d, bmd._D_GRID, lgamma_d, g1_d, g3_d, bmd._S_GRID,
                                  np.inf)
    for j in range(len(kept)):
        totals = np.empty((100, 100))
        for di, d in enumerate(bmd._D_GRID):
            for sgi, sigma in enumerate(bmd._S_GRID):
                t = th.copy()
                bmd._set_up(t, d, sigma)
                totals[di, sgi] = sum(
                    bmd._logpost(t01s[j, s], t10s[j, s], ns[j, s], t, z, offsets[s], sdz2[s], table.values,
                                 table.alpha_lower, table.alpha_upper, table.n_d) for s in range(3))
        di, sgi = np.unravel_index(int(np.argmax(totals)), totals.shape)
        assert (best_d[j], best_s[j]) == (bmd._D_GRID[di], bmd._S_GRID[sgi]), j


def test_the_pooled_drift_grid_search_maximises_the_posterior_summed_over_stretches():
    """Spec 5.2, as above, for `_grid_down` and `_set_down`."""
    _, _, offsets, table, th, z, sdz2, kept = _pooled_block(33)
    t01s, t10s, ns = _padded(kept)
    best_s0 = bmd._grid_down(t01s, t10s, ns, th, z, offsets, sdz2, table.values, table.alpha_lower,
                             table.alpha_upper, table.n_d, bmd._lgamma(0.5 * (th[bmd.D0] + 1)), bmd._S_GRID,
                             np.inf)
    for j in range(len(kept)):
        totals = np.empty(100)
        for sgi, sigma in enumerate(bmd._S_GRID):
            t = th.copy()
            bmd._set_down(t, sigma)
            totals[sgi] = sum(
                bmd._logpost(t01s[j, s], t10s[j, s], ns[j, s], t, z, offsets[s], sdz2[s], table.values,
                             table.alpha_lower, table.alpha_upper, table.n_d) for s in range(3))
        assert best_s0[j] == bmd._S_GRID[int(np.argmax(totals))], j


def test_a_block_run_alone_with_its_seed_gives_the_same_labels():
    """Blocks are independent: block b run on its own with `seed + b` gives
    the labels it gets inside `detect_bmd` (spec 3.2)."""
    params = dataclasses.replace(LIGHT, block_s=2.0)
    x = _drifting_trace(6.0, 11)
    v = velocity(x, FS)
    available = _open(len(x))
    for at in range(500, len(x), 500):  # 1 s stretches, so 2 s blocks hold two each
        available[at:at + 5] = Label.INVALID
    runs = bmd.detect_bmd(x, v, available, FS, params)
    usable = np.array([a is None for a in available])
    gate_params = EngbertKlieglParams(lambda_=params.gate_lambda,
                                      min_duration_samples=params.gate_min_duration_samples)
    gate = np.zeros(len(x), dtype=bool)
    for r in detect_engbert_kliegl(x, v, available, FS, gate_params):
        if r.label is Label.SACCADE:
            gate[r.start:r.stop] = True
    groups = bmd.blocks(bmd.fixation_stretches(usable, gate, math.ceil(params.min_stretch_ms * FS / 1000)),
                        math.ceil(params.block_s * FS))
    assert len(groups) >= 2
    b = 1
    pieces = [x[a:e] for a, e in groups[b]]
    ratio = bmd.isotropy_ratio(pieces)
    alone = bmd.run_block([bmd.to_origin(p * np.array([1.0, ratio])) for p in pieces],
                          drift_rate=4.0 / FS, microsaccade_rate=100.0 / FS, drift_cap=1.3 / FS,
                          microsaccade_cap=100.0 / FS, seed=params.seed + b, table=compute_table(),
                          burn_in=4, samples=4, iterations=2)
    expected = []
    for (a, _e), p in zip(groups[b], alone.probability):
        expected += bmd._labelled(p, a, params.threshold)
    got = [r for r in runs if groups[b][0][0] <= r.start < groups[b][-1][1] and r.label is not Label.SACCADE]
    assert sorted(got, key=lambda r: r.start) == sorted(expected, key=lambda r: r.start)


# -- sections 3.1 and 4: the detector --------------------------------------------


def test_the_saccades_are_engbert_kliegls():
    x = _drifting_trace(3.0, 13, steps=[(1.0, 5.0)])
    v = velocity(x, FS)
    runs = bmd.detect_bmd(x, v, _open(len(x)), FS, LIGHT)
    gate_params = EngbertKlieglParams(lambda_=LIGHT.gate_lambda, min_duration_samples=LIGHT.gate_min_duration_samples)
    ek = [(r.start, r.stop) for r in detect_engbert_kliegl(x, v, _open(len(x)), FS, gate_params)
          if r.label is Label.SACCADE]
    assert ek and [(r.start, r.stop) for r in runs if r.label is Label.SACCADE] == ek


def test_runs_are_sorted_disjoint_and_never_cover_an_unusable_sample():
    x = _drifting_trace(4.0, 17, steps=[(2.0, 0.6)])
    available = _open(len(x))
    available[900:1100] = Label.INVALID
    runs = bmd.detect_bmd(x, velocity(x, FS), available, FS, LIGHT)
    assert runs and all(a.stop <= b.start for a, b in zip(runs, runs[1:]))
    covered = np.zeros(len(x), dtype=bool)
    for r in runs:
        covered[r.start:r.stop] = True
    assert not covered[900:1100].any()
    assert {r.label for r in runs} <= {Label.SACCADE, Label.MICROSACCADE, Label.DRIFT}


def test_short_stretches_are_left_unclaimed():
    x = _drifting_trace(1.0, 19)
    available = _open(len(x))
    available[::60] = Label.INVALID  # stretches of 59 samples, under 200 ms at 500 Hz
    assert bmd.detect_bmd(x, velocity(x, FS), available, FS, LIGHT) == []


def test_nothing_usable_is_no_runs():
    x = _drifting_trace(1.0, 23)
    assert bmd.detect_bmd(x, velocity(x, FS), np.full(len(x), Label.INVALID, dtype=object), FS, LIGHT) == []


@pytest.mark.parametrize("position", [(0.0, 0.0), (3.2, -1.7)])
def test_a_perfectly_still_trace_is_labelled_without_error(position):
    """Review Focus: zero variance -- no rescale, no error, all drift. At the
    production sampler settings: the first re-estimate's motor noise is then
    exactly 0, and its log must be the C library's -inf, not Python's
    `ValueError` (final review C1, 2026-09-27)."""
    x = np.tile(np.array(position), (1000, 1))
    runs = bmd.detect_bmd(x, np.zeros_like(x), _open(1000), FS, bmd.DEFAULT_BMD_PARAMS)
    assert [(r.start, r.stop, r.label) for r in runs] == [(0, 1000, Label.DRIFT)]


def test_the_detector_runs_at_the_rigs_real_rate():
    """Review Focus: 498.55 Hz, the per-sample rates and caps follow it."""
    fs = 498.55
    x = _drifting_trace(3.0, 29, steps=[(1.5, 0.5)])
    runs = bmd.detect_bmd(x, velocity(x, fs), _open(len(x)), fs, LIGHT)
    assert runs and {r.label for r in runs} <= {Label.SACCADE, Label.MICROSACCADE, Label.DRIFT}


def test_a_microsaccade_on_a_drifting_eye_is_found():
    """The model's own regime: a 0.5 deg, 16 ms step on the reference
    recording's drift is found within 5 samples, with the full sampler."""
    x = _drifting_trace(3.0, 31, steps=[(1.5, 0.5)])
    runs = bmd.detect_bmd(x, velocity(x, FS), _open(len(x)), FS, bmd.DEFAULT_BMD_PARAMS)
    onsets = [r.start for r in runs if r.label is Label.MICROSACCADE]
    assert any(abs(o - int(1.5 * FS)) <= 5 for o in onsets), onsets


def test_simulated_microsaccades_are_recovered():
    """On the model's own simulated data, most true microsaccade samples are
    found: a sanity check of the whole detector, not a fidelity claim."""
    x, state = simulate(6000, lambda0=4.0 / FS, lambda1=100.0 / FS, sigma0=0.3 / FS, sigma1=30.0 / FS,
                        d1=4.4, sigmaz=0.004, sigmax=0.02, seed=37)
    runs = bmd.detect_bmd(x, velocity(x, FS), _open(len(x)), FS, bmd.DEFAULT_BMD_PARAMS)
    found = np.zeros(len(x), dtype=bool)
    for r in runs:
        if r.label in (Label.MICROSACCADE, Label.SACCADE):
            found[r.start:r.stop] = True
    assert found[state == 1].mean() > 0.5
