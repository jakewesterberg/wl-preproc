"""The binocular conjunction in one pass where both eyes' runs are sorted and disjoint (spec 4.5, amendment 5)."""

from __future__ import annotations

import numpy as np
import pytest

from tests.scaling import _oracles
from tests.scaling._bounds import TIME_RATIO_BOUND, time_ratio
from wl_preproc.eye.detect.labels import Label, Run
from wl_preproc.schema.detect import _overlapping


def _label_for(start, stop):
    return Label.SACCADE if (stop - start) % 2 else Label.MICROSACCADE


def _disjoint(n, seed, max_gap=20, max_len=15):
    rng = np.random.default_rng(seed)
    runs, cursor = [], int(rng.integers(0, 5))
    for _ in range(n):
        cursor += int(rng.integers(0, max_gap))  # 0: touching the previous run
        length = int(rng.integers(1, max_len))
        runs.append(Run(start=cursor, stop=cursor + length, label=Label.SACCADE))
        cursor += length
    return runs


@pytest.mark.parametrize("floor", [0, 1, 3, 6])
@pytest.mark.parametrize("seed", range(10))
def test_sweep_equals_the_nested_loop(seed, floor):
    left, right = _disjoint(200, seed), _disjoint(200, seed + 100)
    assert _overlapping(left, right, floor, _label_for) == _oracles.overlapping(left, right, floor, _label_for)


def test_identical_runs_equal_the_nested_loop():
    runs = _disjoint(100, 7)
    assert _overlapping(runs, list(runs), 2, _label_for) == _oracles.overlapping(runs, list(runs), 2, _label_for)


@pytest.mark.parametrize("seed", range(5))
def test_overlapping_runs_take_the_nested_loop(seed):
    rng = np.random.default_rng(seed)
    left = [Run(start=int(s), stop=int(s + rng.integers(1, 40)), label=Label.SACCADE)
            for s in rng.integers(0, 2_000, 150)]
    right = _disjoint(150, seed + 50)
    for a, b in ((left, right), (right, left)):
        assert _overlapping(a, b, 3, _label_for) == _oracles.overlapping(a, b, 3, _label_for)


def test_unsorted_disjoint_runs_equal_the_nested_loop():
    left, right = _disjoint(100, 1), _disjoint(100, 2)
    left.reverse()
    assert _overlapping(left, right, 1, _label_for) == _oracles.overlapping(left, right, 1, _label_for)


_INPUTS: dict[int, tuple] = {}


def _conjunction_of(n):
    if n not in _INPUTS:
        _INPUTS[n] = (_disjoint(n, 3), _disjoint(n, 4))
    left, right = _INPUTS[n]
    return _overlapping(left, right, 3, _label_for)


def test_conjunction_scales_linearly():
    assert time_ratio(_conjunction_of, 2_000) <= TIME_RATIO_BOUND
