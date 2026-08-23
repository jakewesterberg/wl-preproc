import pytest

from wl_preproc.contracts.events import DecodeError, Escape, Marker, TaskTypeCode, decode_stream
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.timeline import CODE_WORD_SPACING_S, apply_drift, build_timeline


def test_barcodes_are_emitted_once_per_second():
    truth = build_timeline(CI_RECIPE)
    times = [t for _, t in truth.barcodes]
    assert times[0] == pytest.approx(0.0)
    for earlier, later in zip(times, times[1:]):
        assert later - earlier == pytest.approx(1.0)


def test_barcode_values_are_monotonic():
    values = [v for v, _ in build_timeline(CI_RECIPE).barcodes]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_blocks_tile_the_session_without_gaps():
    truth = build_timeline(CI_RECIPE)
    assert truth.blocks[0].start_s == pytest.approx(0.0)
    for earlier, later in zip(truth.blocks, truth.blocks[1:]):
        assert later.start_s == pytest.approx(earlier.end_s)
    assert truth.blocks[-1].end_s == pytest.approx(CI_RECIPE.duration_s)


def test_trial_ids_are_unique_and_ascending():
    ids = [t.trial_id for t in build_timeline(CI_RECIPE).trials]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_every_trial_belongs_to_a_real_block():
    truth = build_timeline(CI_RECIPE)
    block_ids = {b.block_id for b in truth.blocks}
    assert all(t.block_id in block_ids for t in truth.trials)


def test_code_words_decode_back_to_the_planted_structure():
    """The generator emits through the real encoder, so the real decoder must
    recover it. This is the loop that catches a protocol mismatch."""
    truth = build_timeline(CI_RECIPE)
    events = decode_stream(list(truth.code_words))
    starts = [e for e in events if getattr(e, "escape", None) is Escape.BLOCK_START]
    assert len(starts) == len(truth.blocks)
    assert TaskTypeCode(starts[0].words[1]) is TaskTypeCode.RF_MAP

    numbers = [e for e in events if getattr(e, "escape", None) is Escape.TRIAL_NUMBER]
    assert len(numbers) == len(truth.trials)
    first = (numbers[0].words[0] << 16) | numbers[0].words[1]
    assert first == truth.trials[0].trial_id


def test_no_decode_errors_in_a_clean_session():
    events = decode_stream(list(build_timeline(CI_RECIPE).code_words))
    assert not [e for e in events if isinstance(e, DecodeError)]


def test_trial_outcome_markers_are_present():
    truth = build_timeline(CI_RECIPE)
    codes = [w for _, w in truth.code_words]
    assert Marker.TRIAL_START.value in codes
    assert Marker.SESSION_START.value in codes
    assert Marker.SESSION_END.value in codes


def test_drift_is_proportional_and_signed():
    assert apply_drift(100.0, 0.0) == pytest.approx(100.0)
    assert apply_drift(100.0, 50.0) == pytest.approx(100.0 * (1 + 50e-6))
    assert apply_drift(100.0, -50.0) == pytest.approx(100.0 * (1 - 50e-6))


def test_timeline_is_deterministic():
    assert build_timeline(CI_RECIPE) == build_timeline(CI_RECIPE)


def test_every_trial_emits_an_explicit_trial_end():
    """Fix round 1, Task 8: the generator emitted TRIAL_START, a TRIAL_NUMBER
    payload and an outcome marker per trial, but never Marker.TRIAL_END --
    even though it is in the frozen contract and assemble() already handles
    it correctly (tests/events/test_assemble.py's own _trial() fixture proves
    that). That left every trial's own recorded end inferred rather than
    decoded. This is a fixture gap, not a code gap, and it blocks Task 9: trial
    coverage is a fraction of a trial's own duration, and a fraction of a
    guess is not a measurement.
    """
    truth = build_timeline(CI_RECIPE)
    codes = [w for _, w in truth.code_words]
    assert codes.count(Marker.TRIAL_END.value) == len(truth.trials)


def test_decoded_trial_ends_match_the_planted_truth_not_none():
    """The property the fix exists for. AssembledTrial.end_s is no longer
    None for a single trial in this fixture, and it recovers TrialTruth.end_s
    -- not bit-exactly, because a strobed bus carries one word at a time and
    TRIAL_END necessarily takes its own slot CODE_WORD_SPACING_S before the
    boundary it marks (the same slot the outcome marker held before this fix;
    see build_timeline's own comment), but to within that one word's worth of
    timing, which is the granularity the protocol itself imposes, not slack
    added to this assertion.
    """
    from wl_preproc.events.assemble import assemble

    truth = build_timeline(CI_RECIPE)
    assembly = assemble(decode_stream(list(truth.code_words)))

    assert len(assembly.trials) == len(truth.trials)
    planted_by_id = {t.trial_id: t for t in truth.trials}
    for decoded_trial in assembly.trials:
        assert decoded_trial.end_s is not None, (
            f"trial {decoded_trial.trial_id} was not closed by an explicit TRIAL_END"
        )
        planted = planted_by_id[decoded_trial.trial_id]
        assert decoded_trial.end_s == pytest.approx(planted.end_s - CODE_WORD_SPACING_S)


def test_code_words_never_overlap_on_the_bus():
    """A strobed bus carries one word at a time. Words placed at computed
    offsets from block and trial starts interleave, and the decoder then reads
    one payload's words as the other's."""
    times = [t for t, _ in build_timeline(CI_RECIPE).code_words]
    assert times == sorted(times)
    assert len(set(times)) == len(times)
