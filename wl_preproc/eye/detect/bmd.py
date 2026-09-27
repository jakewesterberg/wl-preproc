"""Bayesian microsaccade detection (Mihali, van Opheusden & Ma 2017), held
sample for sample to its authors' C++ (design spec
`2026-09-27-bmd-design.md`).

**What it is.** A hidden semi-Markov model of fixation. The eye either drifts
or makes a microsaccade; within a state its velocity is constant. A
Metropolis-Hastings chain samples the state sequence, and the settings (the
motor and measurement noise, and the speed distributions) are re-estimated
between rounds of sampling (spec section 1).

**Reference.** `github.com/basvanopheusden/BMD` at `1fab635`, `bmd.cpp`.
Every line number below is that file's. The code has no licence file. It is
run by the tests under its authors' permission and never by the pipeline, and
nothing of it is copied here (spec preamble, section 2).

**In the pipeline** (spec section 3):
- Engbert-Kliegl's saccades are removed first and stored as BMD's `saccade`.
- BMD analyses the fixation stretches between them.
- Its settings are pooled over blocks of about a minute of stretches.
- Samples whose probability of a microsaccade is at least 0.5 are
  `microsaccade`, and the rest of each stretch is `drift`.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import math
from dataclasses import dataclass

import numba
import numpy as np

from wl_preproc.eye.detect import bmd_rng
from wl_preproc.eye.detect.bmd_table import LogATable, lookup

LN2PI = 1.83787706641  # `bmd.cpp` 13, the reference's own rounded constant
LN2 = 0.69314718056  # `bmd.cpp` 14
#: The paper's boundary offset (Preprocessing): x0 = x1 - epsilon.
ORIGIN_EPSILON_DEG = 1e-4

_libm = ctypes.CDLL(ctypes.util.find_library("m"))
_libm.lgamma.restype = ctypes.c_double
_libm.lgamma.argtypes = [ctypes.c_double]
_libm.tgamma.restype = ctypes.c_double
_libm.tgamma.argtypes = [ctypes.c_double]


def _lgamma(x: float) -> float:
    """The C library's `lgamma`, as the reference calls it (spec 4.1)."""
    return _libm.lgamma(x)


def _tgamma(x: float) -> float:
    return _libm.tgamma(x)


# ------------------------------------------------------------------ theta
# The settings, as one float array the compiled code reads by index.
D0, D1, S0, S1, SZ, SX, CUP, CDOWN, PREF, L0, L1, G1U, G3U, G1D, G3D = range(15)


def _theta(d1: float, s0: float, s1: float, sz: float, sx: float, lam0: float, lam1: float) -> np.ndarray:
    th = np.zeros(15)
    th[D0], th[L0], th[L1], th[SX] = 1.0, lam0, lam1, sx
    th[D1], th[S1], th[S0] = d1, s1, s0
    _set_sigmaz(th, sz)
    return th


def _set_up(th: np.ndarray, d1: float, s1: float) -> None:
    """`params::set_d_sigma_up` (`bmd.cpp` 153-157)."""
    th[D1], th[S1] = d1, s1
    th[CUP] = ((1.0 - 0.5 * (d1 + 1)) * LN2 - (d1 + 1) * math.log(s1) - _lgamma(0.5 * (d1 + 1))
               + 2.0 * (d1 + 1.0) * math.log(th[SZ]))
    th[G1U], th[G3U] = _tgamma(0.5 * (d1 + 1.0)), _tgamma(0.5 * (d1 + 3.0))


def _set_down(th: np.ndarray, s0: float) -> None:
    """`params::set_sigma_down` (`bmd.cpp` 159-162)."""
    d0 = th[D0]
    th[S0] = s0
    th[CDOWN] = ((1.0 - 0.5 * (d0 + 1)) * LN2 - (d0 + 1) * math.log(s0) - _lgamma(0.5 * (d0 + 1))
                 + 2.0 * (d0 + 1.0) * math.log(th[SZ]))
    th[G1D], th[G3D] = _tgamma(0.5 * (d0 + 1.0)), _tgamma(0.5 * (d0 + 3.0))


def _set_sigmaz(th: np.ndarray, sz: float) -> None:
    """`params::set_sigmaz` (`bmd.cpp` 146-151)."""
    th[SZ] = sz
    _set_up(th, th[D1], th[S1])
    _set_down(th, th[S0])
    th[PREF] = LN2PI + 2.0 * math.log(sz)


# ------------------------------------------------------------- likelihood
@numba.njit(cache=True, error_model="numpy")
def _dz2(z, off, t1, t2):
    """`data::get_dz_squared` (`bmd.cpp` 320-324): the run [t1, t2) carries the
    eye from sample t1 - 1 to t2 - 1, and from the origin when t1 is 0."""
    if t1 == 0:
        return z[0, off + t2 - 1] * z[0, off + t2 - 1] + z[1, off + t2 - 1] * z[1, off + t2 - 1]
    a = z[0, off + t2 - 1] - z[0, off + t1 - 1]
    b = z[1, off + t2 - 1] - z[1, off + t1 - 1]
    return a * a + b * b


@numba.njit(cache=True, error_model="numpy")
def _ll_up(t1, t2, th, z, off, tab, lo, hi, nd):
    """`calc_loglik_up` (`bmd.cpp` 326-331)."""
    dzs = _dz2(z, off, t1, t2)
    dt = t2 - t1
    a = 0.5 * math.pow(th[SZ], 4.0) / dzs * (1.0 / math.pow(th[S1], 2.0) + dt / math.pow(th[SZ], 2.0))
    return th[CUP] - th[PREF] * dt - 0.5 * (th[D1] + 1.0) * math.log(dzs) + lookup(tab, lo, hi, nd, a, th[D1], th[G1U], th[G3U])


@numba.njit(cache=True, error_model="numpy")
def _ll_down(t1, t2, th, z, off, tab, lo, hi, nd):
    """`calc_loglik_down` (`bmd.cpp` 333-338)."""
    dzs = _dz2(z, off, t1, t2)
    dt = t2 - t1
    a = 0.5 * math.pow(th[SZ], 4.0) / dzs * (1.0 / math.pow(th[S0], 2.0) + dt / math.pow(th[SZ], 2.0))
    return th[CDOWN] - th[PREF] * dt - 0.5 * (th[D0] + 1.0) * math.log(dzs) + lookup(tab, lo, hi, nd, a, th[D0], th[G1D], th[G3D])


@numba.njit(cache=True, error_model="numpy")
def _loglik(t01, t10, n, th, z, off, sdz2, tab, lo, hi, nd):
    """`calc_loglik` (`bmd.cpp` 340-349)."""
    total = -0.5 * sdz2 / (th[SZ] * th[SZ])
    for k in range(n):
        total += _ll_up(t01[k], t10[k], th, z, off, tab, lo, hi, nd)
    for k in range(n - 1):
        total += _ll_down(t10[k], t01[k + 1], th, z, off, tab, lo, hi, nd)
    return total


@numba.njit(cache=True, error_model="numpy")
def _logprior(t01, t10, n, th):
    """`calc_logprior` (`bmd.cpp` 351-360): Gamma(2) durations."""
    l0, l1 = th[L0], th[L1]
    total = n * l0 + (n - 1) * l1 + 2.0 * n * math.log(1.0 - math.exp(-l0)) + 2.0 * (n - 1) * math.log(1.0 - math.exp(-l1))
    for k in range(n):
        total += math.log(float(t10[k] - t01[k])) - l1 * (t10[k] - t01[k])
    for k in range(n - 1):
        total += math.log(float(t01[k + 1] - t10[k])) - l0 * (t01[k + 1] - t10[k])
    return total


@numba.njit(cache=True, error_model="numpy")
def _logpost(t01, t10, n, th, z, off, sdz2, tab, lo, hi, nd):
    return _loglik(t01, t10, n, th, z, off, sdz2, tab, lo, hi, nd) + _logprior(t01, t10, n, th)


# ---------------------------------------------------------------- sampler
@numba.njit(cache=True, error_model="numpy")
def _erase(arr, n, k):
    for i in range(k, n - 1):
        arr[i] = arr[i + 1]


@numba.njit(cache=True, error_model="numpy")
def _insert(arr, n, k, value):
    for i in range(n, k, -1):
        arr[i] = arr[i - 1]
    arr[k] = value


@numba.njit(cache=True, error_model="numpy")
def _step(gen, t01, t10, count, th, z, off, tab, lo, hi, nd):
    """One Metropolis-Hastings step: `MCMC_chain::make_step` and
    `changepoints::execute_step` (`bmd.cpp` 393-562). `count` is [n, N]: the
    number of state-1 runs and of state-1 samples.

    The draws are the reference's, in its order: the move type, the index, the
    position for an insertion, then -- only if the move is possible -- the
    acceptance draw."""
    n = count[0]
    l0, l1 = th[L0], th[L1]
    log_norm = l0 + l1 + 2.0 * math.log(1.0 - math.exp(-l0)) + 2.0 * math.log(1.0 - math.exp(-l1))
    move = bmd_rng.discrete(gen, bmd_rng.MOVE_WEIGHTS_CUMULATIVE)
    possible = True
    removes = False
    k = 0
    pos = 0
    comp = 0.0
    dll = 0.0
    dlp = 0.0
    if move == 0:  # a 0->1 change point one sample earlier
        if n >= 2:
            k = bmd_rng.uniform_int(gen, 1, n - 1)
            pos = t01[k] - 1
            if t01[k] - t10[k - 1] == 1:
                removes = True
                dll = (_ll_up(t01[k - 1], t10[k], th, z, off, tab, lo, hi, nd) - _ll_up(t01[k - 1], t10[k - 1], th, z, off, tab, lo, hi, nd)
                       - _ll_down(t10[k - 1], t01[k], th, z, off, tab, lo, hi, nd) - _ll_up(t01[k], t10[k], th, z, off, tab, lo, hi, nd))
                comp = math.log(float(t10[k] - t01[k - 1] - 2))
                dlp = (-log_norm + l0 - l1 + math.log(float(t10[k] - t01[k - 1])) - math.log(float(t10[k - 1] - t01[k - 1]))
                       - math.log(float(t10[k] - t01[k])))
            else:
                dll = (_ll_up(pos, t10[k], th, z, off, tab, lo, hi, nd) + _ll_down(t10[k - 1], pos, th, z, off, tab, lo, hi, nd)
                       - _ll_up(t01[k], t10[k], th, z, off, tab, lo, hi, nd) - _ll_down(t10[k - 1], t01[k], th, z, off, tab, lo, hi, nd))
                dlp = (l0 - l1 + math.log(float(t10[k] - pos)) + math.log(float(pos - t10[k - 1]))
                       - math.log(float(t10[k] - t01[k])) - math.log(float(t01[k] - t10[k - 1])))
        else:
            possible = False
    elif move == 1:  # a 0->1 change point one sample later
        if n >= 2:
            k = bmd_rng.uniform_int(gen, 1, n - 1)
            pos = t01[k]
            if t10[k] - t01[k] == 1:
                if k != n - 1:
                    removes = True
                    dll = (_ll_down(t10[k - 1], t01[k + 1], th, z, off, tab, lo, hi, nd) - _ll_down(t10[k - 1], t01[k], th, z, off, tab, lo, hi, nd)
                           - _ll_up(t01[k], t10[k], th, z, off, tab, lo, hi, nd) - _ll_down(t10[k], t01[k + 1], th, z, off, tab, lo, hi, nd))
                    comp = -math.log(float(n - 1)) + math.log(float(n - 2)) + math.log(float(t01[k + 1] - t10[k - 1] - 2))
                    dlp = (-log_norm + l1 - l0 + math.log(float(t01[k + 1] - t10[k - 1])) - math.log(float(t01[k] - t10[k - 1]))
                           - math.log(float(t01[k + 1] - t10[k])))
                else:
                    possible = False
            else:
                dll = (_ll_up(pos + 1, t10[k], th, z, off, tab, lo, hi, nd) + _ll_down(t10[k - 1], pos + 1, th, z, off, tab, lo, hi, nd)
                       - _ll_up(t01[k], t10[k], th, z, off, tab, lo, hi, nd) - _ll_down(t10[k - 1], t01[k], th, z, off, tab, lo, hi, nd))
                dlp = (l1 - l0 + math.log(float(t10[k] - pos - 1)) + math.log(float(pos + 1 - t10[k - 1]))
                       - math.log(float(t10[k] - t01[k])) - math.log(float(t01[k] - t10[k - 1])))
        else:
            possible = False
    elif move == 2:  # a 1->0 change point one sample later
        if n >= 2:
            k = bmd_rng.uniform_int(gen, 0, n - 2)
            pos = t10[k]
            if t01[k + 1] - t10[k] == 1:
                removes = True
                dll = (_ll_up(t01[k], t10[k + 1], th, z, off, tab, lo, hi, nd) - _ll_up(t01[k], t10[k], th, z, off, tab, lo, hi, nd)
                       - _ll_down(t10[k], t01[k + 1], th, z, off, tab, lo, hi, nd) - _ll_up(t01[k + 1], t10[k + 1], th, z, off, tab, lo, hi, nd))
                comp = math.log(float(t10[k + 1] - t01[k] - 2))
                dlp = (-log_norm - l1 + l0 + math.log(float(t10[k + 1] - t01[k])) - math.log(float(t10[k] - t01[k]))
                       - math.log(float(t10[k + 1] - t01[k + 1])))
            else:
                dll = (_ll_up(t01[k], pos + 1, th, z, off, tab, lo, hi, nd) + _ll_down(pos + 1, t01[k + 1], th, z, off, tab, lo, hi, nd)
                       - _ll_up(t01[k], t10[k], th, z, off, tab, lo, hi, nd) - _ll_down(t10[k], t01[k + 1], th, z, off, tab, lo, hi, nd))
                dlp = (l0 - l1 + math.log(float(pos + 1 - t01[k])) + math.log(float(t01[k + 1] - pos - 1))
                       - math.log(float(t10[k] - t01[k])) - math.log(float(t01[k + 1] - t10[k])))
        else:
            possible = False
    elif move == 3:  # a 1->0 change point one sample earlier
        if n >= 2:
            k = bmd_rng.uniform_int(gen, 0, n - 2)
            pos = t10[k] - 1
            if t10[k] - t01[k] == 1:
                if k != 0:
                    removes = True
                    dll = (_ll_down(t10[k - 1], t01[k + 1], th, z, off, tab, lo, hi, nd) - _ll_down(t10[k - 1], t01[k], th, z, off, tab, lo, hi, nd)
                           - _ll_up(t01[k], t10[k], th, z, off, tab, lo, hi, nd) - _ll_down(t10[k], t01[k + 1], th, z, off, tab, lo, hi, nd))
                    comp = -math.log(float(n - 1)) + math.log(float(n - 2)) + math.log(float(t01[k + 1] - t10[k - 1] - 2))
                    dlp = (l1 - l0 - log_norm + math.log(float(t01[k + 1] - t10[k - 1])) - math.log(float(t01[k] - t10[k - 1]))
                           - math.log(float(t01[k + 1] - t10[k])))
                else:
                    possible = False
            else:
                dll = (_ll_up(t01[k], pos, th, z, off, tab, lo, hi, nd) + _ll_down(pos, t01[k + 1], th, z, off, tab, lo, hi, nd)
                       - _ll_up(t01[k], t10[k], th, z, off, tab, lo, hi, nd) - _ll_down(t10[k], t01[k + 1], th, z, off, tab, lo, hi, nd))
                dlp = (l1 - l0 + math.log(float(pos - t01[k])) + math.log(float(t01[k + 1] - pos))
                       - math.log(float(t10[k] - t01[k])) - math.log(float(t01[k + 1] - t10[k])))
        else:
            possible = False
    elif move == 4:  # a drift sample inside a microsaccade run
        k = bmd_rng.uniform_int(gen, 0, n - 1)
        if t10[k] - t01[k] > 2:
            pos = bmd_rng.uniform_int(gen, t01[k] + 1, t10[k] - 2)
            dll = (_ll_up(t01[k], pos, th, z, off, tab, lo, hi, nd) + _ll_down(pos, pos + 1, th, z, off, tab, lo, hi, nd)
                   + _ll_up(pos + 1, t10[k], th, z, off, tab, lo, hi, nd) - _ll_up(t01[k], t10[k], th, z, off, tab, lo, hi, nd))
            comp = -math.log(float(t10[k] - t01[k] - 2))
            dlp = (log_norm + l1 - l0 + math.log(float(pos - t01[k])) + math.log(float(t10[k] - pos - 1))
                   - math.log(float(t10[k] - t01[k])))
        else:
            possible = False
    else:  # a microsaccade sample inside a drift run
        if n >= 2:
            k = bmd_rng.uniform_int(gen, 0, n - 2)
            if t01[k + 1] - t10[k] > 2:
                pos = bmd_rng.uniform_int(gen, t10[k] + 1, t01[k + 1] - 2)
                dll = (_ll_down(t10[k], pos, th, z, off, tab, lo, hi, nd) + _ll_up(pos, pos + 1, th, z, off, tab, lo, hi, nd)
                       + _ll_down(pos + 1, t01[k + 1], th, z, off, tab, lo, hi, nd) - _ll_down(t10[k], t01[k + 1], th, z, off, tab, lo, hi, nd))
                comp = -math.log(float(n - 1)) - math.log(float(t01[k + 1] - t10[k] - 2)) + math.log(float(n))
                dlp = (log_norm + l0 - l1 + math.log(float(pos - t10[k])) + math.log(float(t01[k + 1] - pos - 1))
                       - math.log(float(t01[k + 1] - t10[k])))
            else:
                possible = False
        else:
            possible = False
    if not possible:
        return
    if not (-bmd_rng.exponential(gen) < dlp + 1.0 * dll - comp):  # `MCMC_chain::accept`, beta = 1
        return
    if move == 0:
        if removes:
            _erase(t01, n, k)
            _erase(t10, n, k - 1)
            count[0] = n - 1
        else:
            t01[k] -= 1
        count[1] += 1
    elif move == 1:
        if removes:
            _erase(t01, n, k)
            _erase(t10, n, k)
            count[0] = n - 1
        else:
            t01[k] += 1
        count[1] -= 1
    elif move == 2:
        if removes:
            _erase(t01, n, k + 1)
            _erase(t10, n, k)
            count[0] = n - 1
        else:
            t10[k] += 1
        count[1] += 1
    elif move == 3:
        if removes:
            _erase(t01, n, k)
            _erase(t10, n, k)
            count[0] = n - 1
        else:
            t10[k] -= 1
        count[1] -= 1
    elif move == 4:
        _insert(t01, n, k + 1, pos + 1)
        _insert(t10, n, k, pos)
        count[0] = n + 1
        count[1] -= 1
    else:
        _insert(t01, n, k + 1, pos)
        _insert(t10, n, k + 1, pos + 1)
        count[0] = n + 1
        count[1] += 1


@numba.njit(cache=True, error_model="numpy")
def _sweep(gen, t01, t10, count, th, z, off, length, tab, lo, hi, nd):
    """`parallel_tempered_chain::sweep` (`bmd.cpp` 589-603), one chain: as many
    steps as the stretch has samples."""
    for _ in range(length):
        _step(gen, t01, t10, count, th, z, off, tab, lo, hi, nd)


# ------------------------------------------------ smoothing, noise, start
@numba.njit(cache=True, error_model="numpy")
def _smooth(x, z, off, length, sz, sx):
    """`data::kalman_filter` (`bmd.cpp` 174-195), steady state, one stretch.
    The filter's state before the first sample is the origin (spec 1.2):
    the reference reads `zf[-1]`, which holds 0. Returns the sum of squared
    smoothed steps."""
    sigzsq = sz * sz
    sigxsq = sx * sx
    p = 0.5 * (math.sqrt(sigzsq * sigzsq + 4.0 * sigzsq * sigxsq) - sigzsq)
    gain = (p + sigzsq) / (p + sigzsq + sigxsq)
    back = p / (p + sigzsq)
    zf = np.empty((2, length))
    for dim in range(2):
        prev = 0.0
        for i in range(length):
            zf[dim, i] = prev + gain * (x[dim, off + i] - prev)
            prev = zf[dim, i]
    for dim in range(2):
        z[dim, off + length - 1] = zf[dim, length - 1]
        for i in range(length - 2, -1, -1):
            z[dim, off + i] = zf[dim, i] + back * (z[dim, off + i + 1] - zf[dim, i])
    total = 0.0
    for i in range(1, length):
        a = z[0, off + i] - z[0, off + i - 1]
        b = z[1, off + i] - z[1, off + i - 1]
        total += a * a + b * b
    return total


@numba.njit(cache=True, error_model="numpy")
def _start(x, off, length, t01, t10):
    """`changepoints::changepoints(data&)` (`bmd.cpp` 266-284): state 1 where
    the squared step reaches the 99th percentile, the first step measured
    from the origin. Fills t01/t10 and returns [n, N]."""
    dx2 = np.empty(length)
    dx2[0] = x[0, off] * x[0, off] + x[1, off] * x[1, off]
    for t in range(1, length):
        a = x[0, off + t] - x[0, off + t - 1]
        b = x[1, off + t] - x[1, off + t - 1]
        dx2[t] = a * a + b * b
    threshold = np.sort(dx2)[int(0.99 * length)]
    n = 1
    ones = 1
    t01[0] = 0
    m = 0
    for t in range(1, length):
        if t != 1 and dx2[t - 1] < threshold and (t == length - 1 or dx2[t] >= threshold):
            t01[n] = t
            n += 1
        if (t == 1 or dx2[t - 1] >= threshold) and t != length - 1 and dx2[t] < threshold:
            t10[m] = t
            m += 1
        if dx2[t] >= threshold:
            ones += 1
    t10[m] = length
    return np.array([n, ones])


@numba.njit(cache=True, error_model="numpy")
def _lag_sums(x, off, length, t01, t10, n, sums, pairs):
    """`data::get_residual` and the lag sums of `estimate_sigmazx` (`bmd.cpp`
    705-747), accumulated into `sums`/`pairs` so stretches pool (spec 3.2).
    The residual's slope for the last run reaches past the stretch to the
    origin: the reference reads `x[T]`, which holds 0 (spec 1.2)."""
    res = np.empty((2, length))
    for dim in range(2):
        i = 0
        j = 0
        pred = x[dim, off]
        slope = 0.0
        for t in range(length):
            if i < n and t01[i] == t:
                i += 1
                end = x[dim, off + t10[j]] if t10[j] < length else 0.0
                slope = (end - x[dim, off + t]) / (t10[j] - t)
            if t10[j] == t:
                j += 1
                end = x[dim, off + t01[i]] if t01[i] < length else 0.0
                slope = (end - x[dim, off + t]) / (t01[i] - t)
            res[dim, t] = x[dim, off + t] - pred
            pred += slope
    for lag in range(1, 20):
        total = 0.0
        for t in range(lag, length):
            a = res[0, t] - res[0, t - lag]
            b = res[1, t] - res[1, t - lag]
            total += a * a + b * b
        sums[lag] += total
        pairs[lag] += length - lag


def _noise(x, offsets, lengths, samples) -> tuple[float, float]:
    """`estimate_sigmazx` (`bmd.cpp` 723-763), pooled: for each sample, the
    lag sums over every stretch; the medians over samples. With one stretch,
    the reference's."""
    nmax = 20
    mean_n = nmax / 2.0
    mean_nn = nmax * (2 * nmax - 1) / 6.0
    sz_est, sx_est = [], []
    for sample in samples:
        sums = np.zeros(nmax)
        pairs = np.zeros(nmax, np.int64)
        for s, (t01, t10, count) in enumerate(sample):
            _lag_sums(x, offsets[s], lengths[s], t01, t10, count[0], sums, pairs)
        mean_c = 0.0
        mean_cn = 0.0
        for lag in range(1, nmax):
            c = sums[lag] / pairs[lag]
            mean_c += c / (nmax - 1)
            mean_cn += lag * c / (nmax - 1)
        slope = (mean_cn - mean_c * mean_n) / (mean_nn - mean_n * mean_n)
        intercept = mean_c - slope * mean_n
        if abs(intercept - 0.0) < 0.00001:
            intercept = 0.00001
        sz_est.append(math.sqrt(slope / 2.0) if slope >= 0 else math.nan)
        sx_est.append(math.sqrt(max(0.00001, intercept) / 4.0))
    half = len(samples) // 2
    return float(np.sort(sz_est)[half]), float(np.sort(sx_est)[half])


# ----------------------------------------------------------- grid search
@numba.njit(cache=True, parallel=True, error_model="numpy")
def _grid_up(t01s, t10s, ns, th, z, offsets, sdz2, tab, lo, hi, nd, d_grid, lgamma_d, g1_d, g3_d, s_grid, cap):
    """`estimate_dsigma_up` (`bmd.cpp` 655-681), pooled: for each sample, the
    first maximum over the grid of the log posterior summed over stretches.
    Samples are independent, so they run in parallel with identical results.
    Grid points above `cap` are skipped, the first point never (spec 3.3)."""
    n_samples = ns.shape[0]
    n_stretch = ns.shape[1]
    best_s = np.empty(n_samples)
    best_d = np.empty(n_samples)
    for j in numba.prange(n_samples):
        t = th.copy()
        best = 0.0
        sb = 0.0
        db = 0.0
        for di in range(100):
            d = d_grid[di]
            for sgi in range(100):
                sigma = s_grid[sgi]
                if sgi > 0 and sigma > cap:
                    continue
                t[D1] = d
                t[S1] = sigma
                t[CUP] = (1.0 - 0.5 * (d + 1)) * LN2 - (d + 1) * math.log(sigma) - lgamma_d[di] + 2.0 * (d + 1.0) * math.log(t[SZ])
                t[G1U] = g1_d[di]
                t[G3U] = g3_d[di]
                total = 0.0
                for s in range(n_stretch):
                    total += _logpost(t01s[j, s], t10s[j, s], ns[j, s], t, z, offsets[s], sdz2[s], tab, lo, hi, nd)
                if (di == 0 and sgi == 0) or total > best:
                    sb = sigma
                    db = d
                    best = total
        best_s[j] = sb
        best_d[j] = db
    return best_s, best_d


@numba.njit(cache=True, parallel=True, error_model="numpy")
def _grid_down(t01s, t10s, ns, th, z, offsets, sdz2, tab, lo, hi, nd, lgamma_d0, s_grid, cap):
    """`estimate_sigma_down` (`bmd.cpp` 683-703), pooled, as `_grid_up`."""
    n_samples = ns.shape[0]
    n_stretch = ns.shape[1]
    best_s = np.empty(n_samples)
    for j in numba.prange(n_samples):
        t = th.copy()
        d0 = t[D0]
        best = 0.0
        sb = 0.0
        for sgi in range(100):
            sigma = s_grid[sgi]
            if sgi > 0 and sigma > cap:
                continue
            t[S0] = sigma
            t[CDOWN] = (1.0 - 0.5 * (d0 + 1)) * LN2 - (d0 + 1) * math.log(sigma) - lgamma_d0 + 2.0 * (d0 + 1.0) * math.log(t[SZ])
            total = 0.0
            for s in range(n_stretch):
                total += _logpost(t01s[j, s], t10s[j, s], ns[j, s], t, z, offsets[s], sdz2[s], tab, lo, hi, nd)
            if sgi == 0 or total > best:
                sb = sigma
                best = total
        best_s[j] = sb
    return best_s


# ------------------------------------------------------------ one block
_D_GRID = np.array([1.1 + di * (5.0 - 1.1) / 100.0 for di in range(100)])  # `bmd.cpp` 664
_S_GRID = np.array([10.0 ** (-4.0 + 4.0 / 100.0 * sgi) for sgi in range(100)])  # `bmd.cpp` 666


def _first_speeds() -> tuple[float, float, float]:
    """d1, sigma0 and sigma1's starting values (`bmd.cpp` 111-113): drawn from
    the `params` object's own generator, which is never seeded and so starts
    at the standard's default every run (spec 1.3). A module-level function so
    the fidelity check's null can replace it."""
    gen = bmd_rng.new_generator()
    return (bmd_rng.uniform_real(gen, 1.1, 5.0), bmd_rng.uniform_real(gen, 0.0001, 0.005),
            bmd_rng.uniform_real(gen, 0.005, 0.1))


def _chain_generators(seed: int, count: int) -> list[np.ndarray]:
    """One generator per stretch's chain, each seeded with one draw of the
    block's generator, in stretch order (`bmd.cpp` 807, 820; spec 3.2). With
    one stretch, the reference's. A module-level function so the fidelity
    check's null can replace it."""
    block = bmd_rng.new_generator(seed)
    gens = []
    for _ in range(count):
        g = bmd_rng.new_generator()
        bmd_rng.seed(g, np.uint64(bmd_rng.next_u64(block)))
        gens.append(g)
    return gens


@dataclass(frozen=True, slots=True)
class BlockRecord:
    """What the reference writes, for the fidelity tests: the settings at the
    start of each iteration (`params` file) and every sample (`changepoints`
    file), per stretch."""

    settings: list[tuple[float, float, float, float, float, float]]
    samples: list[list[list[tuple[int, int, np.ndarray, np.ndarray, float]]]]


@dataclass(frozen=True, slots=True)
class BlockResult:
    probability: list[np.ndarray]
    record: BlockRecord | None


def run_block(
    stretches: list[np.ndarray],
    *,
    drift_rate: float,
    microsaccade_rate: float,
    drift_cap: float,
    microsaccade_cap: float,
    seed: int,
    table: LogATable,
    burn_in: int = 40,
    samples: int = 40,
    iterations: int = 6,
    record: bool = False,
) -> BlockResult:
    """BMD over one block of stretches with shared settings (spec 3.2), each
    stretch `(T, 2)` in degrees, already rescaled and shifted (spec 1.2).
    Rates and caps are per sample. With one stretch this is `bmd.cpp`'s
    `main`, step for step, and `record` returns what it writes.

    Returns, per stretch, P(microsaccade) per sample from the last
    iteration's samples, the two end runs of every sample excluded (spec 3.4).
    The reference re-estimates the settings once more after its last
    sampling round; nothing reads them, so that round is skipped."""
    x = np.ascontiguousarray(np.concatenate([np.asarray(s, float).T for s in stretches], axis=1))
    lengths = np.array([len(s) for s in stretches], np.int64)
    offsets = np.concatenate(([0], np.cumsum(lengths)[:-1])).astype(np.int64)
    tab, lo, hi, nd = table.values, table.alpha_lower, table.alpha_upper, table.n_d

    d1, s0, s1 = _first_speeds()
    th = _theta(d1, s0, s1, 0.015, 0.02, drift_rate, microsaccade_rate)

    chains = []
    for s in range(len(stretches)):
        t01 = np.zeros(lengths[s] + 2, np.int64)
        t10 = np.zeros(lengths[s] + 2, np.int64)
        count = _start(x, offsets[s], lengths[s], t01, t10)
        chains.append((t01, t10, count))
    sz, sx = _noise(x, offsets, lengths, [[(c[0], c[1], c[2]) for c in chains]])
    _set_sigmaz(th, sz)
    th[SX] = sx
    z = np.empty_like(x)
    sdz2 = np.array([_smooth(x, z, offsets[s], lengths[s], th[SZ], th[SX]) for s in range(len(stretches))])

    gens = _chain_generators(seed, len(stretches))

    lgamma_d = np.array([_lgamma(0.5 * (d + 1)) for d in _D_GRID])
    g1_d = np.array([_tgamma(0.5 * (d + 1.0)) for d in _D_GRID])
    g3_d = np.array([_tgamma(0.5 * (d + 3.0)) for d in _D_GRID])
    settings, all_samples = [], []
    kept = []
    for it in range(iterations):
        settings.append((th[SZ], th[SX], th[D0], th[S0], th[D1], th[S1]))
        kept = [[] for _ in range(samples)]
        for s, (t01, t10, count) in enumerate(chains):
            for _ in range(burn_in):
                _sweep(gens[s], t01, t10, count, th, z, offsets[s], lengths[s], tab, lo, hi, nd)
            for j in range(samples):
                _sweep(gens[s], t01, t10, count, th, z, offsets[s], lengths[s], tab, lo, hi, nd)
                n = count[0]
                kept[j].append((t01[:n].copy(), t10[:n].copy(), count.copy()))
        if record:
            all_samples.append([
                [(int(kept[j][s][2][1]), int(kept[j][s][2][0]), kept[j][s][0], kept[j][s][1],
                  _logpost(kept[j][s][0], kept[j][s][1], kept[j][s][2][0], th, z, offsets[s], sdz2[s], tab, lo, hi, nd))
                 for j in range(samples)]
                for s in range(len(stretches))
            ])
        if it == iterations - 1:
            break
        width = max(int(kept[j][s][2][0]) for j in range(samples) for s in range(len(stretches)))
        t01s = np.zeros((samples, len(stretches), width), np.int64)
        t10s = np.zeros((samples, len(stretches), width), np.int64)
        ns = np.zeros((samples, len(stretches)), np.int64)
        for j in range(samples):
            for s in range(len(stretches)):
                n = int(kept[j][s][2][0])
                t01s[j, s, :n] = kept[j][s][0]
                t10s[j, s, :n] = kept[j][s][1]
                ns[j, s] = n
        best_s, best_d = _grid_up(t01s, t10s, ns, th, z, offsets, sdz2, tab, lo, hi, nd,
                                  _D_GRID, lgamma_d, g1_d, g3_d, _S_GRID, microsaccade_cap)
        half = samples // 2
        _set_up(th, float(np.sort(best_d)[half]), float(np.sort(best_s)[half]))
        best_s0 = _grid_down(t01s, t10s, ns, th, z, offsets, sdz2, tab, lo, hi, nd,
                             _lgamma(0.5 * (th[D0] + 1)), _S_GRID, drift_cap)
        _set_down(th, float(np.sort(best_s0)[half]))
        sz, sx = _noise(x, offsets, lengths, kept)
        _set_sigmaz(th, sz)
        th[SX] = sx
        sdz2 = np.array([_smooth(x, z, offsets[s], lengths[s], th[SZ], th[SX]) for s in range(len(stretches))])

    probability = []
    for s in range(len(stretches)):
        hits = np.zeros(lengths[s])
        for j in range(samples):
            t01, t10, count = kept[j][s]
            for k in range(1, int(count[0]) - 1):  # the end runs are not detections
                hits[t01[k]:t10[k]] += 1.0
        probability.append(hits / samples)
    return BlockResult(probability=probability,
                       record=BlockRecord(settings=settings, samples=all_samples) if record else None)
