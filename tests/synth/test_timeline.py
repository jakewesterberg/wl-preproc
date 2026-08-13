import pytest

from wl_preproc.contracts.events import DecodeError, Escape, Marker, TaskTypeCode, decode_stream
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.timeline import apply_drift, build_timeline


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


def test_code_words_never_overlap_on_the_bus():
    """A strobed bus carries one word at a time. Words placed at computed
    offsets from block and trial starts interleave, and the decoder then reads
    one payload's words as the other's."""
    times = [t for t, _ in build_timeline(CI_RECIPE).code_words]
    assert times == sorted(times)
    assert len(set(times)) == len(times)
