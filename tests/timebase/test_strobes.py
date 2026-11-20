"""Intan's strobe edges placed on the NI's clock by their pattern alone
(amendment 49).

Edges carry no values, so a wrong match is silent: it mistimes Intan by a burst
or a period and every residual still looks small. Each scenario therefore
checks the matcher against the simulation's truth, edge by edge:

* no wrong match: every real Intan edge is paired with the NI's record of the
  same true edge;
* at least 98% of Intan's real edges that the NI also recorded are matched;
* the largest residual of a straight line through the pairs is at most 100 us;
* that line is within 10 us of the true map at both ends of Intan's recording;
* no NI edge is paired twice.

A periodic stream is either refused or matched right, never matched wrong: the
property the anchor's ambiguity rule exists for.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.timebase.strobe_streams import StrobeScenario, periodic, simulate_with_reference_id
from wl_preproc.timebase.strobes import RESIDUAL_BLOCK_PAIRS, StrobeMatch, Unanchored, match_strobes


def _wrong(match: StrobeMatch, true_id: np.ndarray, reference_id: np.ndarray) -> np.ndarray:
    """Which pairs join a real Intan edge to the NI's record of a different edge."""
    device_id = true_id[match.device_index]
    return (device_id >= 0) & (device_id != reference_id[match.reference_index])


def _matched_against_truth(sc: StrobeScenario) -> StrobeMatch:
    ni_s, intan_s, true_id, reference_id, truth_map = simulate_with_reference_id(sc)
    match = match_strobes(ni_s, intan_s)

    assert match.reference_index.shape == match.device_index.shape
    assert np.all(np.diff(match.device_index) > 0)
    assert np.unique(match.reference_index).size == match.reference_index.size

    real = true_id[match.device_index] >= 0
    wrong = _wrong(match, true_id, reference_id)
    assert not wrong.any(), f"{int(wrong.sum())} edges matched to the wrong NI edge"

    recorded_by_both = np.isin(true_id[true_id >= 0], reference_id).sum()
    assert real.sum() >= 0.98 * recorded_by_both, f"{int(real.sum())} of {int(recorded_by_both)} matched"

    device, reference = intan_s[match.device_index], ni_s[match.reference_index]
    rate, offset = np.polyfit(device, reference, 1)
    residual = reference - (device * rate + offset)
    assert np.max(np.abs(residual)) <= 100e-6

    ends = intan_s[[0, -1]]
    assert np.all(np.abs((ends * rate + offset) - truth_map(ends)) <= 10e-6)
    return match


def test_clean_60_ppm_matches_every_edge():
    _matched_against_truth(StrobeScenario())


def test_drops_and_glitches_match_no_wrong_edge():
    # Seed 5 holds a glitch beside a dropped edge, 387 us from the line, which only
    # the residual limit removes. Seed 1's glitches all sit beside real edges, and
    # the one-to-one rule removes those first.
    _matched_against_truth(StrobeScenario(drop=0.10, extra=0.005, seed=5))


def test_200_ppm_matches():
    _matched_against_truth(StrobeScenario(rel_ppm=200))


def test_20_and_25_khz_match():
    _matched_against_truth(StrobeScenario(fs_intan=20_000, fs_ni=25_000, drop=0.01))


def test_single_words_match():
    _matched_against_truth(StrobeScenario(burst_max=1, drop=0.01))


def test_trial_paced_fixed_delays_match():
    _matched_against_truth(StrobeScenario(trial_offsets=(0, 0.3, 0.8, 1.3, 1.5), burst_max=3))


def test_an_intan_started_late_still_anchors():
    _matched_against_truth(StrobeScenario(intan_late_s=600))


def test_an_ni_started_late_still_anchors():
    # Intan's first 20 s of edges, ten windows, were strobed before the NI started.
    _matched_against_truth(StrobeScenario(ni_late_s=30))


def test_a_periodic_stream_refuses():
    # Single words every 2 s: no burst sizes to tell one period from the next.
    with pytest.raises(Unanchored, match="ambiguous|no anchor matched"):
        match_strobes(*periodic(2.0, jitter=0.0005, burst_max=1))


def test_a_strictly_periodic_stream_is_refused_by_the_margin():
    # Every period-shifted map matches as well as the right one, so the best
    # reaches 95% and only the margin refuses it.
    with pytest.raises(Unanchored, match="ambiguous: the best anchor matched 100% and the runner-up 100%"):
        match_strobes(*periodic(0.5, jitter=0.0, burst_max=1, hours=0.25))


_PERIODIC = [
    (period_s, jitter_s, burst_max, 1)
    for period_s in (0.25, 0.5, 1.0, 2.0)
    for jitter_s in (0.0, 20e-6, 50e-6, 200e-6)
    for burst_max in (1, 4)
] + [
    # Each anchored with every match wrong before rulings R1-R3 (2026-11-20), with
    # (0.5, 20e-6, 1, 1) and (0.5, 50e-6, 1, 1), which the grid above holds.
    (0.25, 20e-6, 1, 3),
    (0.5, 20e-6, 1, 2),
    (0.5, 20e-6, 1, 3),
    (0.5, 50e-6, 1, 2),
    (0.5, 50e-6, 1, 3),
    (0.5, 200e-6, 1, 2),
    # The brief's periodic stream: bursts of one to four words, with 0.5 ms of
    # jitter, are a code after all, and anchor right.
    (2.0, 500e-6, 4, 1),
]


@pytest.mark.parametrize(("period_s", "jitter_s", "burst_max", "seed"), _PERIODIC)
def test_a_periodic_stream_is_refused_or_matched_right(period_s, jitter_s, burst_max, seed):
    ni_s, intan_s, true_id, reference_id, _ = simulate_with_reference_id(
        StrobeScenario(hours=0.25, periodic_s=period_s, jitter_s=jitter_s, burst_max=burst_max, seed=seed)
    )
    try:
        match = match_strobes(ni_s, intan_s)
    except Unanchored:
        return
    wrong = _wrong(match, true_id, reference_id)
    assert not wrong.any(), f"{int(wrong.sum())} of {wrong.size} pairs wrong"


def test_a_map_that_loses_the_pattern_refuses():
    # The NI lost 90% of its strobes in the second half: Intan's edges there are
    # within the NI's recording, and mostly unmatched.
    ni_s, intan_s, _, _, _ = simulate_with_reference_id(StrobeScenario(ni_drop=0.9, ni_drop_after_s=1800))
    with pytest.raises(
        Unanchored, match=r"the anchored map matched \d+% of Intan's edges over the whole recording, below 95%"
    ):
        match_strobes(ni_s, intan_s)


def test_a_rate_step_keeps_the_real_pairs():
    # Intan's rate steps by 0.5 ppm halfway through 2 h: one line through the
    # whole recording is hundreds of us off at its ends and its middle.
    sc = StrobeScenario(hours=2.0, rate_step_ppm=0.5)
    ni_s, intan_s, true_id, reference_id, _ = simulate_with_reference_id(sc)
    match = match_strobes(ni_s, intan_s)
    assert not _wrong(match, true_id, reference_id).any()
    real = true_id[match.device_index] >= 0
    assert real.sum() >= 0.98 * np.isin(true_id[true_id >= 0], reference_id).sum()
    blocks = np.array_split(np.arange(match.device_index.size), match.device_index.size // RESIDUAL_BLOCK_PAIRS)
    for block in blocks:
        device, reference = intan_s[match.device_index[block]], ni_s[match.reference_index[block]]
        rate, offset = np.polyfit(device, reference, 1)
        assert np.max(np.abs(reference - (device * rate + offset))) <= 100e-6


def test_a_rate_change_across_a_long_break_does_not_slip():
    # No strobe for 900 s, and Intan's rate steps 0.7 ppm as the break begins: the
    # fit from before the break is 0.63 ms off when the strobes resume, nearer
    # the neighbouring word than the right one.
    sc = StrobeScenario(silent_s=(2400.0, 3300.0), rate_step_ppm=0.7, rate_step_at_s=2401.0)
    ni_s, intan_s, true_id, reference_id, _ = simulate_with_reference_id(sc)
    match = match_strobes(ni_s, intan_s)
    wrong = _wrong(match, true_id, reference_id)
    assert not wrong.any(), f"{int(wrong.sum())} of {wrong.size} pairs wrong"

    after = np.arange(intan_s.size) > np.argmax(np.diff(intan_s))
    real_after = after & (true_id >= 0) & np.isin(true_id, reference_id)
    matched = np.zeros(intan_s.size, dtype=bool)
    matched[match.device_index] = True
    assert (matched & real_after).sum() >= 0.98 * real_after.sum()


def test_an_ambiguous_stretch_after_a_break_refuses():
    # After a 100 s break the rig strobes single words every 20 ms, exactly: every
    # period-shifted map within the re-anchor's 50 ms search matches that stretch
    # as well as the right one. (A longer period is not ambiguous after a break:
    # the map cannot have run 0.5 s off, so its shifts are never candidates.)
    sc = StrobeScenario(
        hours=0.5, silent_s=(1200.0, 1300.0), periodic_after_s=1300.0, periodic_after_period_s=0.02, jitter_s=0.0
    )
    ni_s, intan_s, _, _, _ = simulate_with_reference_id(sc)
    with pytest.raises(
        Unanchored,
        match=r"the strobes after a \d+ s gap at \d+ s cannot be anchored again: the strobes' pattern is ambiguous",
    ):
        match_strobes(ni_s, intan_s)


@pytest.mark.parametrize("seed", [1, 2])
def test_sparse_strobes_through_a_break_do_not_slip(seed):
    # The break holds single real strobes every 8 s, so no two Intan edges are
    # 10 s apart, while Intan's rate steps 0.7 ppm as the break begins. A line
    # held by the pairs before the break would be 0.63 ms off when the dense
    # strobes resume, nearer the neighbouring word than the right one.
    sc = StrobeScenario(
        silent_s=(2400.0, 3300.0),
        lone_edges_s=tuple(np.arange(2408.0, 3300.0, 8.0)),
        rate_step_ppm=0.7,
        rate_step_at_s=2401.0,
        seed=seed,
    )
    ni_s, intan_s, true_id, reference_id, _ = simulate_with_reference_id(sc)
    assert np.max(np.diff(intan_s)) < 10.0
    match = match_strobes(ni_s, intan_s)
    wrong = _wrong(match, true_id, reference_id)
    assert not wrong.any(), f"{int(wrong.sum())} of {wrong.size} pairs wrong"


def test_a_stray_edge_just_before_the_strobes_resume_does_not_hide_the_truth():
    # The first Intan edge after a silent break is spurious, 1 s before the
    # strobes resume: the re-anchor is made there, and the right alignment must
    # come from the real edges behind it.
    sc = StrobeScenario(
        silent_s=(2400.0, 3300.0), rate_step_ppm=0.7, rate_step_at_s=2401.0, intan_spurious_s=(3299.0,)
    )
    ni_s, intan_s, true_id, reference_id, _ = simulate_with_reference_id(sc)
    match = match_strobes(ni_s, intan_s)
    wrong = _wrong(match, true_id, reference_id)
    assert not wrong.any(), f"{int(wrong.sum())} of {wrong.size} pairs wrong"


def _real_after_the_break(intan_s: np.ndarray, true_id: np.ndarray, reference_id: np.ndarray) -> np.ndarray:
    """Intan's real edges, recorded by the NI too, after the longest gap between
    real edges: spurious edges in a break do not move it."""
    real = (true_id >= 0) & np.isin(true_id, reference_id)
    real_s = intan_s[true_id >= 0]
    return real & (intan_s > real_s[np.argmax(np.diff(real_s))])


def test_a_stray_edge_in_a_break_does_not_refuse():
    # 0.5% spurious edges fall through a 900 s break too: each is a stretch of
    # its own, with no NI edge to match and no edges after it.
    sc = StrobeScenario(silent_s=(2400.0, 3300.0), extra=0.005)
    ni_s, intan_s, true_id, reference_id, _ = simulate_with_reference_id(sc)
    match = match_strobes(ni_s, intan_s)
    assert not _wrong(match, true_id, reference_id).any()
    after = _real_after_the_break(intan_s, true_id, reference_id)
    assert np.isin(np.flatnonzero(after), match.device_index).sum() >= 0.98 * after.sum()


def test_a_lone_edge_in_a_break_is_left_unmatched():
    # One real strobe, recorded by both devices, 450 s from anything else.
    sc = StrobeScenario(silent_s=(2400.0, 3300.0), lone_edges_s=(2850.0,))
    ni_s, intan_s, true_id, reference_id, _ = simulate_with_reference_id(sc)
    alone = (np.diff(intan_s, prepend=-np.inf) > 100) & (np.diff(intan_s, append=np.inf) > 100)
    assert alone.sum() == 1
    match = match_strobes(ni_s, intan_s)
    assert np.flatnonzero(alone)[0] not in match.device_index
    assert not _wrong(match, true_id, reference_id).any()
    after = _real_after_the_break(intan_s, true_id, reference_id)
    assert np.isin(np.flatnonzero(after), match.device_index).sum() >= 0.98 * after.sum()


def test_pairs_are_one_to_one():
    # 5% spurious edges: some land beside a real edge the NI recorded, and match it too.
    ni_s, intan_s, _, _, _ = simulate_with_reference_id(StrobeScenario(drop=0.10, extra=0.05))
    match = match_strobes(ni_s, intan_s)
    assert np.unique(match.reference_index).size == match.reference_index.size


def test_fewer_than_ten_edges_refuses():
    ni_s, intan_s, _, _, _ = simulate_with_reference_id(StrobeScenario(hours=0.1))
    with pytest.raises(Unanchored, match=r"Intan recorded 9 strobe edges, and anchoring needs at least 10"):
        match_strobes(ni_s, intan_s[:9])


def test_a_refusal_never_rounds_a_fraction_up_to_the_limit():
    # 94.96% is below 95%, so no sentence may print it as 95%.
    from wl_preproc.timebase.strobes import _refusal

    assert _refusal(0.9496, 0.0) == "no anchor matched 95% of Intan's edges: the best matched 94%"
    assert _refusal(0.97, 0.8796).startswith("the strobes' pattern is ambiguous: the best anchor matched 97% and "
                                             "the runner-up 87%, within 10%")
    # An exact ratio is printed as itself: 57 of 100 is 57%, though 0.57 * 100 floors to 56.
    assert _refusal(0.57, 0.0).endswith("the best matched 57%")


def test_the_whole_recording_sentence_never_rounds_up_to_the_limit(monkeypatch):
    from wl_preproc.timebase import strobes

    ni_s, intan_s, _, _, _ = simulate_with_reference_id(StrobeScenario(hours=0.1))
    monkeypatch.setattr(strobes, "_fraction_matched", lambda *_: 0.9496)
    with pytest.raises(
        Unanchored, match=r"^the anchored map matched 94% of Intan's edges over the whole recording, below 95%$"
    ):
        match_strobes(ni_s, intan_s)


def test_no_ni_edge_refuses():
    _, intan_s, _, _, _ = simulate_with_reference_id(StrobeScenario(hours=0.1))
    with pytest.raises(Unanchored, match="the NI recorded no strobe edge"):
        match_strobes(np.array([]), intan_s)


def test_chunked_scoring_gives_the_same_anchor():
    # 64 cells score one or two NI edges at a time, never the whole array.
    ni_s, intan_s, _, _, _ = simulate_with_reference_id(StrobeScenario())
    chunked = match_strobes(ni_s, intan_s, scoring_cells=64)
    whole = match_strobes(ni_s, intan_s)
    assert np.array_equal(chunked.reference_index, whole.reference_index)
    assert np.array_equal(chunked.device_index, whole.device_index)
    assert chunked.anchor_fraction == whole.anchor_fraction
    assert chunked.runner_up_fraction == whole.runner_up_fraction
