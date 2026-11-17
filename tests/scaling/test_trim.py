"""An activation's blocks hold an event by binary search (spec 4.9)."""

from __future__ import annotations

import numpy as np
import pytest

from tests.scaling import _oracles
from tests.scaling._bounds import TIME_RATIO_BOUND, time_ratio
from wl_preproc.nwb.trim import BlockSet


def _blocks(n, seed):
    rng = np.random.default_rng(seed)
    starts = np.cumsum(rng.uniform(0.0, 20.0, n))
    return BlockSet.of([{"start_s": s, "end_s": s + rng.uniform(0.0, 15.0)} for s in starts])


def _times(blocks, n, seed):
    rng = np.random.default_rng(seed)
    edges = np.array([t for pair in blocks.intervals for t in pair])
    span = edges.max() + 10.0
    return np.concatenate([rng.uniform(-5.0, span, n), edges, np.nextafter(edges, np.inf),
                           np.nextafter(edges, -np.inf), [np.nan, -np.inf, np.inf]])


@pytest.mark.parametrize("seed", range(6))
def test_contains_equals_today(seed):
    blocks = _blocks(50, seed)
    times = _times(blocks, 2_000, seed)
    np.testing.assert_array_equal(blocks.contains(times), _oracles.contains(blocks.intervals, times))


@pytest.mark.parametrize("seed", range(6))
def test_contains_instant_equals_today(seed):
    blocks = _blocks(50, seed)
    times = _times(blocks, 2_000, seed)
    np.testing.assert_array_equal(blocks.contains_instant(times), _oracles.contains_instant(blocks.intervals, times))


def test_no_blocks_hold_nothing():
    empty = BlockSet.of([])
    assert not empty.contains([0.0, 1.0]).any()
    assert not empty.contains_instant([0.0]).any()


def test_a_scalar_list_and_shape_are_kept():
    blocks = _blocks(5, 1)
    assert blocks.contains([blocks.intervals[0][0]]).shape == (1,)
    assert blocks.contains(np.zeros((2, 3))).shape == (2, 3)


_SETS: dict[int, tuple] = {}


def _trim_of(n):
    if n not in _SETS:
        blocks = _blocks(n // 10, 2)
        _SETS[n] = (blocks, _times(blocks, n, 3))
    blocks, times = _SETS[n]
    return blocks.contains(times)


def test_trim_scales_linearly():
    assert time_ratio(_trim_of, 50_000) <= TIME_RATIO_BOUND
