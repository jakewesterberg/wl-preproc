"""The least squares, from matched `(session_s, device_s)` pairs.

Barcodes are one way to get pairs; a strobe's edges are another (part 3b-2).
The arithmetic is the same, so the barcode fits and the pair fits are asserted
to agree to the bit, not to a tolerance.
"""

import pytest
from wl_sync.barcode import Barcode

from wl_preproc.timebase.fit import (
    _matched_pairs,
    fit_offset,
    fit_offset_to_pairs,
    fit_pairs_across,
    fit_rate_across,
)


def _barcodes_on_a_drifting_clock(
    drift_ppm: float, n: int
) -> tuple[list[Barcode], dict[int, float]]:
    """`n` barcodes a second apart on a device clock `drift_ppm` fast, and the
    sync box's reference for them, which is the session's own seconds."""
    reference = {v: float(v) for v in range(n)}
    barcodes = [
        Barcode(value=v, start_us=int(v * 1_000_000 * (1 + drift_ppm / 1e6)))
        for v in reference
    ]
    return barcodes, reference


def test_pairs_fit_as_the_barcodes_they_came_from():
    # The same pairs, given as barcodes against a reference and as pairs, fit identically.
    barcodes, reference = _barcodes_on_a_drifting_clock(drift_ppm=37.0, n=40)
    by_barcode = fit_rate_across([barcodes], reference, 30_000.0)
    by_pair = fit_pairs_across([_matched_pairs(barcodes, reference)], 30_000.0)
    assert by_pair == by_barcode
    assert fit_offset_to_pairs(_matched_pairs(barcodes, reference), by_pair) == fit_offset(
        barcodes, reference, by_barcode
    )


def test_two_groups_keep_their_own_intercepts():
    pairs_a = [(t, t * (1 + 50e-6) + 3.0) for t in (1.0, 2.0, 5.0)]
    pairs_b = [(t, (t - 100.0) * (1 + 50e-6)) for t in (101.0, 104.0, 110.0)]
    fit = fit_pairs_across([pairs_a, pairs_b], 20_000.0)
    assert fit.drift_ppm == pytest.approx(50.0, abs=1e-6)


def test_one_pair_is_a_position_not_a_slope():
    with pytest.raises(ValueError, match="1 pair matched"):
        fit_pairs_across([[(1.0, 1.0)]], 30_000.0)


def test_no_pairs_cannot_be_offset():
    barcodes, reference = _barcodes_on_a_drifting_clock(drift_ppm=0.0, n=5)
    fit = fit_rate_across([barcodes], reference, 30_000.0)
    with pytest.raises(ValueError, match="no pairs"):
        fit_offset_to_pairs([], fit)
