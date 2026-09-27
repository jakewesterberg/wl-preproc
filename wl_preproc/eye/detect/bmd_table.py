"""BMD's table of log A(alpha, d), and the lookup the sampler uses.

A(alpha, d) = integral from 0 to infinity of s^d exp(-alpha s^2) I0(s) ds
(design spec `2026-09-27-bmd-design.md` section 1.6). The reference reads a
1000 x 1000 table of log A that its authors computed with MATLAB's
`integral` (paper, Appendix). This module computes its own on the same grid,
from the formula, so nothing of theirs is shipped (spec section 4.1). The
tests inject the authors' table in its place to hold the rest of the
algorithm to theirs exactly.

**The grid is the reference's** (`bmd.cpp` 26-40): alpha_i = exp((i + 1) *
12/1000 - 6) and d_j = (j + 1) * 0.0049 + 0.1, for i and j from 0 to 999.

**Outside two per-d cutoffs, the lookup uses the paper's asymptotic forms**
(Figure A1; `bmd.cpp` 46-49): the large-s expansion of I0 below
`alpha_lower[j]`, and its Taylor series above `alpha_upper[j]`. The cutoffs
here follow the paper's stated criterion, an approximation error below 0.003
in log A:
- `alpha_lower[j]` is the last grid alpha before the small-alpha form's error
  first reaches 0.003, or the grid's first alpha if it starts there;
- `alpha_upper[j]` is the first grid alpha from which the large-alpha form's
  error stays below 0.003.

The authors' `alpha_upper` follows that rule on every column. Their
`alpha_lower` follows it from d = 1 up, and departs from it for d < 1, where
BMD never evaluates: drift fixes d = 1, and the microsaccade grid starts at
1.1 (spec section 1.8).

**The quadrature.** It uses the trapezoid rule in u = ln s, step 0.02 over
[-30, 8]:
- the integrand in u is smooth and decays at both ends, so the rule converges
  fast;
- 0.02 resolves the narrowest peak on the grid, about 0.07 wide in u at the
  smallest alpha.

It is computed once per process, in about a second, and is deterministic.
"""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass

import numba
import numpy as np
import scipy.special

LN2 = 0.69314718056  # `bmd.cpp` 14, the reference's own rounded constant
N_ALPHA = 1000
N_D = 1000
D_STEP = 0.0049
APPROXIMATION_TOLERANCE = 0.003

_U_LOW = -30.0
_U_HIGH = 8.0
_U_STEP = 0.02


@dataclass(frozen=True, slots=True)
class LogATable:
    """`values[i * n_d + j]` is log A(alpha_i, d_j), row-major exactly as the
    reference's `table[a_ind * Nd + d_ind]`."""

    values: np.ndarray
    alpha_lower: np.ndarray
    alpha_upper: np.ndarray
    n_alpha: int
    n_d: int


def alpha_grid() -> np.ndarray:
    return np.exp((np.arange(N_ALPHA) + 1) * 12.0 / 1000.0 - 6.0)


def d_grid() -> np.ndarray:
    return (np.arange(N_D) + 1) * D_STEP + 0.1


@numba.njit(cache=True, error_model="numpy")
def _log_a(alphas, ds, u, log_i0e_of_s, step):
    """log A on the grid, trapezoid in u. In u, the integrand is
    exp(d*u - alpha*s^2 + s + log(I0e(s)) + u) with s = e^u."""
    n_u = u.size
    s = np.exp(u)
    out = np.empty((alphas.size, ds.size))
    for i in range(alphas.size):
        base = np.empty(n_u)
        for k in range(n_u):
            base[k] = -alphas[i] * s[k] * s[k] + s[k] + log_i0e_of_s[k] + u[k]
        for j in range(ds.size):
            total = 0.0
            for k in range(n_u):
                total += math.exp(ds[j] * u[k] + base[k])
            out[i, j] = math.log(total * step)
    return out


def _small_alpha(alpha, d):
    return 0.25 / alpha - d * np.log(alpha) - d * LN2


def _large_alpha(alpha, d):
    return (-LN2 - 0.5 * (d + 1.0) * np.log(alpha)
            + np.log(math.gamma(0.5 * (d + 1.0)) + 0.25 * math.gamma(0.5 * (d + 3.0)) / alpha))


@functools.lru_cache(maxsize=1)
def compute_table() -> LogATable:
    """This implementation's table, computed once per process."""
    u = np.arange(_U_LOW, _U_HIGH + _U_STEP / 2, _U_STEP)
    log_i0e = np.log(scipy.special.i0e(np.exp(u)))
    alphas, ds = alpha_grid(), d_grid()
    log_a = _log_a(alphas, ds, u, log_i0e, _U_STEP)
    lower = np.empty(N_D)
    upper = np.empty(N_D)
    for j, d in enumerate(ds):
        small_ok = np.abs(_small_alpha(alphas, d) - log_a[:, j]) < APPROXIMATION_TOLERANCE
        first_bad = int(np.argmin(small_ok)) if not small_ok.all() else N_ALPHA
        lower[j] = alphas[max(first_bad - 1, 0)]
        large_bad = np.abs(_large_alpha(alphas, d) - log_a[:, j]) >= APPROXIMATION_TOLERANCE
        last_bad = int(N_ALPHA - 1 - np.argmax(large_bad[::-1])) if large_bad.any() else -1
        upper[j] = alphas[min(last_bad + 1, N_ALPHA - 1)]
    return LogATable(values=np.ascontiguousarray(log_a.ravel()), alpha_lower=lower,
                     alpha_upper=upper, n_alpha=N_ALPHA, n_d=N_D)


@numba.njit(cache=True, error_model="numpy")
def lookup(values, alpha_lower, alpha_upper, n_d, a, d, gamma_1, gamma_3):
    """The reference's `integraltable::operator()` (`bmd.cpp` 42-63), with
    Gamma((d + 1)/2) and Gamma((d + 3)/2) passed in as `gamma_1` and `gamma_3`.
    They are computed outside compiled code through the C library, as the
    reference's `tgamma` is (spec section 4.1)."""
    d_ind = int((d - 0.1) / D_STEP - 1)
    if a < alpha_lower[d_ind]:
        return 0.25 / a - d * math.log(a) - d * LN2
    if a > alpha_upper[d_ind]:
        return -LN2 - 0.5 * (d + 1.0) * math.log(a) + math.log(gamma_1 + 0.25 * gamma_3 / a)
    a_ind = int(1000.0 / 12.0 * (math.log(a) + 6.0) - 1)
    i1 = values[a_ind * n_d + d_ind]
    i2 = values[a_ind * n_d + d_ind + 1]
    i3 = values[(a_ind + 1) * n_d + d_ind]
    i4 = values[(a_ind + 1) * n_d + d_ind + 1]
    a_up = math.exp(((a_ind + 2) * 12.0 / 1000.0) - 6.0) - a
    d_up = (d_ind + 2) * D_STEP + 0.1 - d
    a_down = a - math.exp(((a_ind + 1) * 12.0 / 1000.0) - 6.0)
    d_down = d - ((d_ind + 1) * D_STEP + 0.1)
    return ((d_down * (a_down * i4 + a_up * i3) + d_up * (a_down * i2 + a_up * i1))
            / (a_up + a_down) / (d_up + d_down))
