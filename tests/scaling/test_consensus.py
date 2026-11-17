"""Detector agreement compares label codes as arrays, each distinct label judged once (spec 4.8)."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from tests.scaling import _oracles
from wl_preproc.eye.detect.consensus import PSO_AS_FIXATION, PSO_AS_SACCADE, _event_starts, comparison_mask
from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.registry import DETECTORS

PAIRS = list(itertools.combinations(sorted(DETECTORS), 2))


def _trace(n, seed, as_text=False):
    rng = np.random.default_rng(seed)
    values = list(Label)
    runs = rng.integers(1, 30, n)
    labels = np.repeat(rng.integers(0, len(values), len(runs)), runs)[:n]
    out = np.empty(n, dtype=object)
    out[:] = [values[i].value if as_text else values[i] for i in labels]
    return out


@pytest.mark.parametrize("pso_as", [PSO_AS_SACCADE, PSO_AS_FIXATION])
@pytest.mark.parametrize("pair", PAIRS)
def test_comparison_mask_equals_today(pair, pso_as):
    a_vocabulary, b_vocabulary = (DETECTORS[name].vocabulary for name in pair)
    for seed, as_text in ((0, False), (1, True)):
        a, b = _trace(3_000, seed, as_text), _trace(3_000, seed + 10, as_text)
        np.testing.assert_array_equal(comparison_mask(a, b, a_vocabulary, b_vocabulary, pso_as),
                                      _oracles.comparison_mask(a, b, a_vocabulary, b_vocabulary, pso_as))


@pytest.mark.parametrize("seed", range(6))
def test_event_starts_equal_today(seed):
    labels = _trace(3_000, seed, as_text=bool(seed % 2))
    mask = np.random.default_rng(seed).random(3_000) < 0.8
    assert _event_starts(labels, mask) == _oracles.event_starts(labels, mask)
    assert all(type(start) is int for start in _event_starts(labels, mask))


def test_empty_traces():
    empty = np.empty(0, dtype=object)
    vocabulary = DETECTORS["engbert_kliegl"].vocabulary
    assert comparison_mask(empty, empty, vocabulary, vocabulary, PSO_AS_SACCADE).shape == (0,)
    assert _event_starts(empty, np.zeros(0, dtype=bool)) == []


AGREEMENT_SPEEDUP_BOUND = 2.0


def _best_of_three(fn) -> float:
    import time

    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    return best


def test_agreement_is_array_fast():
    """Linear already, but one Python call per sample: today's code takes 0.63 s per million samples on the
    development machine, about 126 such passes per trace pair-set a session, 10-14 minutes at two hours (the spike).
    The array form takes 0.16 s there, 3.9 times faster. A CI runner is about twice as slow at both, so the bound is
    that ratio, measured in the same process (as `_bounds.py` bounds ratios, never seconds): at least 2, where a
    return to a per-sample loop reads about 1. Best of three each."""
    a_vocabulary, b_vocabulary = DETECTORS["engbert_kliegl"].vocabulary, DETECTORS["nslr"].vocabulary
    a, b = _trace(1_000_000, 1), _trace(1_000_000, 2)
    array_form = _best_of_three(
        lambda: _event_starts(a, comparison_mask(a, b, a_vocabulary, b_vocabulary, PSO_AS_SACCADE)))
    today = _best_of_three(
        lambda: _oracles.event_starts(a, _oracles.comparison_mask(a, b, a_vocabulary, b_vocabulary, PSO_AS_SACCADE)))
    assert today / array_form >= AGREEMENT_SPEEDUP_BOUND, (
        f"{today / array_form:.1f}x today's code ({array_form:.2f} s against {today:.2f} s per million samples)")


def test_a_sample_that_is_no_label_still_raises():
    vocabulary = DETECTORS["engbert_kliegl"].vocabulary
    a = np.array([Label.SACCADE, "nonsense"], dtype=object)
    with pytest.raises(ValueError):
        comparison_mask(a, a, vocabulary, vocabulary, PSO_AS_SACCADE)
    with pytest.raises(ValueError):
        _oracles.comparison_mask(a, a, vocabulary, vocabulary, PSO_AS_SACCADE)
