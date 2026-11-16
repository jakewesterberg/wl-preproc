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
def test_anchored_equals_whole_with_scattered_light_damage(stream, seed):
    """Up to three words dropped and one inserted in either stream, scattered: today's count exactly."""
    rng = random.Random(seed)
    reference = _damaged(stream[:3_000], rng, drops=(0, 1, 2, 3), inserts=(0, 1))
    other = _damaged(stream[:3_000], rng, drops=(0, 1, 2, 3), inserts=(0, 1))
    assert code_agreement(reference, other) == _oracles.code_agreement(reference, other)


@pytest.mark.parametrize("seed", range(12))
def test_a_heavily_damaged_session_keeps_its_tier(stream, seed):
    """Below the band the greedy matcher can find a few codes more or fewer split at trials than whole (amendment 7:
    5 of 300 such streams, at most 0.0017): the value moves in its third decimal, the tier does not."""
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


@pytest.fixture(scope="module")
def long_stream():
    from wl_preproc.synth.recipe import BENCHMARK_RECIPE, BlockSpec, TaskTypeCode
    from wl_preproc.synth.timeline import build_timeline

    recipe = BENCHMARK_RECIPE.model_copy(update={"runs": True, "blocks": (
        BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=3000, trial_duration_s=3.0),)})
    return [code for _, code in build_timeline(recipe).code_words]


@pytest.mark.parametrize("seed", [102, 127])
def test_burst_damage_near_the_threshold_equals_today(long_stream, seed):
    """A strobe glitch that drops a burst of consecutive words leaves a session just above the threshold, and split
    at trials the greedy matcher counts it a code or two differently (the final review's Important 1: seed 127,
    0.999096 against 0.999191). In the band where a tier could turn, the count is today's: whole-stream."""
    rng = random.Random(seed)
    reference, other = list(long_stream), list(long_stream)
    at = rng.randrange(100, len(other) - 200)
    del other[at:at + rng.randint(4, 15)]
    for _ in range(rng.randint(0, 6)):
        reference.insert(at + rng.randint(-20, 20), rng.randrange(65536))
    assert code_agreement(reference, other) == _oracles.code_agreement(reference, other)


def test_a_perfect_match_is_not_aligned_again(stream, monkeypatch):
    """1.0 means equal streams, which the whole stream counts the same: no second, slow alignment."""
    from wl_preproc.events import agreement

    calls = []
    real = agreement._matched
    monkeypatch.setattr(agreement, "_matched", lambda a, b: calls.append(len(a)) or real(a, b))
    assert agreement.code_agreement(stream, list(stream)) == 1.0
    assert max(calls) < len(stream)


_STREAMS: dict[int, tuple] = {}


def test_code_agreement_scales_linearly(stream):
    """Outside the tier band -- here 2% of each stream's words dropped -- the anchored form alone runs, linear. In the
    band a slightly damaged session pays today's whole-stream alignment by design (amendment 7)."""
    from wl_preproc.events.agreement import AGREEMENT_THRESHOLD, TIER_BAND

    def agreement_of(n):
        if n not in _STREAMS:
            rng = random.Random(n)
            _STREAMS[n] = (_damaged(stream[:n], rng, drops=(n // 50,), inserts=(0,)),
                           _damaged(stream[:n], rng, drops=(n // 50,), inserts=(0,)))
            assert code_agreement(*_STREAMS[n]) < AGREEMENT_THRESHOLD - TIER_BAND
        return code_agreement(*_STREAMS[n])

    assert time_ratio(agreement_of, 2_000) <= TIME_RATIO_BOUND
