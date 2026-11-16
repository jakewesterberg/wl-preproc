"""The Pi-vs-NI code agreement aligned between trial-number anchors (spec 4.6, amendment 1)."""

from __future__ import annotations

import random

import pytest

from tests.scaling import _oracles
from tests.scaling._bounds import TIME_RATIO_BOUND, time_ratio
from wl_preproc.contracts.events import Escape
from wl_preproc.events.agreement import code_agreement


@pytest.fixture(scope="module")
def stream():
    from wl_preproc.synth.recipe import BENCHMARK_RECIPE
    from wl_preproc.synth.timeline import build_timeline

    return [code for _, code in build_timeline(BENCHMARK_RECIPE.model_copy(update={"runs": True})).code_words]


def _damaged(words, rng, drops=(0, 1, 10, 50), inserts=(0, 5)):
    words = list(words)
    for k in sorted(rng.sample(range(len(words)), rng.choice(drops)), reverse=True):
        del words[k]
    for _ in range(rng.choice(inserts)):
        words.insert(rng.randrange(len(words)), rng.randrange(65536))
    return words


@pytest.mark.parametrize("seed", range(40))
def test_anchored_equals_whole_near_the_threshold(stream, seed):
    """Where a session is damaged little enough for its tier to turn on `AGREEMENT_THRESHOLD`, the count is today's
    exactly (amendment 7): up to three words dropped and one inserted in either stream."""
    rng = random.Random(seed)
    reference = _damaged(stream[:3_000], rng, drops=(0, 1, 2, 3), inserts=(0, 1))
    other = _damaged(stream[:3_000], rng, drops=(0, 1, 2, 3), inserts=(0, 1))
    assert code_agreement(reference, other) == _oracles.code_agreement(reference, other)


@pytest.mark.parametrize("seed", range(12))
def test_a_heavily_damaged_session_keeps_its_tier(stream, seed):
    """Far below the threshold the greedy matcher can find a few codes more or fewer split at trials than whole
    (amendment 7: 5 of 300 such streams, at most 0.0017): the value moves in its third decimal, the tier does not."""
    from wl_preproc.events.agreement import AGREEMENT_THRESHOLD

    rng = random.Random(seed)
    reference, other = _damaged(stream[:3_000], rng), _damaged(stream[:3_000], rng)
    anchored, whole = code_agreement(reference, other), _oracles.code_agreement(reference, other)
    assert abs(anchored - whole) <= 0.002
    assert (anchored < AGREEMENT_THRESHOLD) == (whole < AGREEMENT_THRESHOLD)


def test_a_damaged_anchor_merges_segments(stream):
    reference = stream[:3_000]
    other = list(reference)
    at = [i for i, word in enumerate(other) if word == Escape.TRIAL_NUMBER][40]
    del other[at + 1]  # one word of the trial number's payload
    assert code_agreement(reference, other) == _oracles.code_agreement(reference, other)


def test_a_payload_word_equal_to_an_escape_is_not_an_anchor():
    """A trial number whose low word is 0x8006 (RUN_START) or 0x8001 (TRIAL_NUMBER): a bare value search would cut
    inside the payload."""
    trial = int(Escape.TRIAL_NUMBER)
    reference = [5, trial, 0, int(Escape.RUN_START), 7, 7, trial, 0, trial, 9, 9, trial, 0, 3, 8]
    other = [5, trial, 0, int(Escape.RUN_START), 7, trial, 0, trial, 9, trial, 0, 3, 8]
    assert code_agreement(reference, other) == _oracles.code_agreement(reference, other)


def test_out_of_order_anchors_align_the_whole_stream(stream):
    reference = stream[:2_000]
    starts = [i for i, word in enumerate(reference) if word == Escape.TRIAL_NUMBER]
    a, b, c = starts[10], starts[11], starts[12]
    other = reference[:a] + reference[b:c] + reference[a:b] + reference[c:]  # two trials swapped
    assert code_agreement(reference, other) == _oracles.code_agreement(reference, other)


def test_empty_streams_still_agree():
    assert code_agreement([], []) == 1.0
    assert code_agreement([1, 2], []) == _oracles.code_agreement([1, 2], [])


_STREAMS: dict[int, tuple] = {}


def test_code_agreement_scales_linearly(stream):
    def agreement_of(n):
        if n not in _STREAMS:
            rng = random.Random(n)
            _STREAMS[n] = (_damaged(stream[:n], rng), _damaged(stream[:n], rng))
        return code_agreement(*_STREAMS[n])

    assert time_ratio(agreement_of, 2_000) <= TIME_RATIO_BOUND
