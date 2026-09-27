"""BMD's own table of log A(alpha, d) (design spec `2026-09-27-bmd-design.md`
sections 1.6, 4.1, 5.3)."""

from __future__ import annotations

import math

import numpy as np
import pytest
import scipy.integrate
import scipy.special

from wl_preproc.eye.detect import bmd_table


def _quad_log_a(alpha, d):
    """log A by adaptive quadrature, split at the integrand's peak, which
    sits near s = 1/(2 alpha) for small alpha."""
    peak = max(1.0, 1.0 / (2.0 * alpha))

    def f(s):
        return s ** d * math.exp(-alpha * s * s + s + math.log(scipy.special.i0e(s)))

    total = sum(scipy.integrate.quad(f, a, b, limit=500, epsabs=0, epsrel=1e-12)[0]
                for a, b in ((0.0, peak), (peak, peak + 60.0 / math.sqrt(alpha) + 60.0)))
    return math.log(total)


def test_the_grid_is_the_references():
    """`bmd.cpp` 30-40: alpha_i = exp((i+1)*12/1000 - 6), d_j = (j+1)*0.0049 + 0.1."""
    alphas, ds = bmd_table.alpha_grid(), bmd_table.d_grid()
    assert alphas.size == ds.size == 1000
    assert alphas[0] == math.exp(12.0 / 1000.0 - 6.0) and alphas[-1] == math.exp(1000 * 12.0 / 1000.0 - 6.0)
    assert ds[0] == 0.0049 + 0.1 and ds[-1] == 1000 * 0.0049 + 0.1


@pytest.mark.parametrize("i, j", [(0, 182), (50, 182), (300, 600), (523, 612), (700, 999), (999, 450)])
def test_the_table_is_the_integral(i, j):
    """Against adaptive quadrature, independently. The first two cells are
    the drift column BMD evaluates (d = 1). The cell at (523, 612) is where
    the authors' table is off by 0.0103 (measured 2026-09-27)."""
    table = bmd_table.compute_table()
    alpha, d = bmd_table.alpha_grid()[i], bmd_table.d_grid()[j]
    assert table.values[i * table.n_d + j] == pytest.approx(_quad_log_a(alpha, d), abs=1e-8)


def test_the_cutoffs_follow_the_papers_criterion():
    """Below `alpha_lower` the small-alpha form, above `alpha_upper` the
    large-alpha form, each within 0.003 of the table; at the cutoffs'
    neighbours, not (spec 1.6; Figure A1)."""
    table = bmd_table.compute_table()
    alphas, ds = bmd_table.alpha_grid(), bmd_table.d_grid()
    log_a = table.values.reshape(1000, 1000)
    for j in (182, 300, 600, 999):
        d = ds[j]
        small = np.abs(bmd_table._small_alpha(alphas, d) - log_a[:, j])
        large = np.abs(bmd_table._large_alpha(alphas, d) - log_a[:, j])
        lo = int(np.searchsorted(alphas, table.alpha_lower[j]))
        hi = int(np.searchsorted(alphas, table.alpha_upper[j]))
        assert (small[:lo] < bmd_table.APPROXIMATION_TOLERANCE).all()
        # The last grid alpha before the error first reaches the tolerance,
        # or the grid's first alpha if it starts there (columns 600 and 999).
        assert lo == 0 or small[lo] < bmd_table.APPROXIMATION_TOLERANCE
        assert lo + 1 == alphas.size or small[lo + 1] >= bmd_table.APPROXIMATION_TOLERANCE
        assert (large[hi:] < bmd_table.APPROXIMATION_TOLERANCE).all()
        assert hi == 0 or large[hi - 1] >= bmd_table.APPROXIMATION_TOLERANCE


def test_the_lookup_interpolates_as_the_reference_does_crosswise():
    """`bmd.cpp` 51-61, reproduced as written, and it is not bilinear.

    The corners (alpha_i, d_j+1) and (alpha_i+1, d_j) carry each other's
    weights. A true bilinear interpolation would weight the first by
    a_up*d_down and the second by a_down*d_up; the reference does the
    reverse. Every result the authors published was computed this way, so it
    is kept (spec 1.10).

    Measured 2026-09-27 against a true bilinear over the cells BMD reads:
    - median 0.017 in log A, max 1.2, the largest at small alpha;
    - near d = 1, the drift column, at most 0.0065."""
    table = bmd_table.compute_table()
    alphas, ds = bmd_table.alpha_grid(), bmd_table.d_grid()
    values = table.values.reshape(1000, 1000)
    a, d = 1.5, 2.0 + 0.002
    ai, dj = int(1000.0 / 12.0 * (math.log(a) + 6.0) - 1), int((d - 0.1) / 0.0049 - 1)
    a_up, a_down = alphas[ai + 1] - a, a - alphas[ai]
    d_up, d_down = ds[dj + 1] - d, d - ds[dj]
    crosswise = ((d_down * (a_down * values[ai + 1, dj + 1] + a_up * values[ai + 1, dj])
                  + d_up * (a_down * values[ai, dj + 1] + a_up * values[ai, dj]))
                 / (a_up + a_down) / (d_up + d_down))
    got = bmd_table.lookup(table.values, table.alpha_lower, table.alpha_upper, table.n_d, a, d,
                           math.gamma(0.5 * (d + 1.0)), math.gamma(0.5 * (d + 3.0)))
    assert table.alpha_lower[dj] <= a <= table.alpha_upper[dj]
    assert got == pytest.approx(crosswise, rel=1e-12)


def test_outside_the_cutoffs_the_lookup_uses_the_asymptotic_forms():
    table = bmd_table.compute_table()
    d = 2.0
    g1, g3 = math.gamma(0.5 * (d + 1.0)), math.gamma(0.5 * (d + 3.0))
    far = 1000.0
    assert bmd_table.lookup(table.values, table.alpha_lower, table.alpha_upper, table.n_d, far, d, g1, g3) == (
        -bmd_table.LN2 - 0.5 * (d + 1.0) * math.log(far) + math.log(g1 + 0.25 * g3 / far))
    near = 1e-4
    assert bmd_table.lookup(table.values, table.alpha_lower, table.alpha_upper, table.n_d, near, d, g1, g3) == (
        0.25 / near - d * math.log(near) - d * bmd_table.LN2)


def test_the_table_agrees_with_the_authors_where_bmd_reads_it():
    """Spec 5.3, gated on `WLPP_BMD_REFERENCE`. Where BMD evaluates (d from
    0.99, between the cutoffs), the two tables agree to 1e-5 in all but a
    handful of cells, once this table is printed the way the authors' file
    prints it (seven significant digits, `%e`). The cell at (523, 612) is
    theirs in error. The cutoffs are the same grid points. Measured 2026-09-27:
    99.9988% of those cells identical when printed; median difference 3.4e-7
    over the whole grid unprinted. With this table in place of the authors',
    BMD's probabilities were identical on the authors' example and on a lab
    stretch (spec 5.3; `test_bmd_validation.py`)."""
    from tests.eye.detect import _bmd_reference as ref

    theirs = ref.authors_table()
    ours = bmd_table.compute_table()
    alphas = bmd_table.alpha_grid()
    a, b = ours.values.reshape(1000, 1000), theirs.values.reshape(1000, 1000)
    used = np.zeros_like(a, dtype=bool)
    for j in range(180, 1000):
        used[:, j] = (alphas >= theirs.alpha_lower[j]) & (alphas <= theirs.alpha_upper[j])
    printed = np.vectorize(lambda v: float(f"{v:e}"))(a[used])
    assert np.mean(printed == b[used]) > 0.9999
    grid_index = lambda v: np.abs(np.log(alphas)[None, :] - np.log(v)[:, None]).argmin(axis=1)  # noqa: E731
    assert np.array_equal(grid_index(ours.alpha_upper), grid_index(theirs.alpha_upper))
    assert np.array_equal(grid_index(ours.alpha_lower[180:]), grid_index(theirs.alpha_lower[180:]))
