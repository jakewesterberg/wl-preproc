import pytest
from wl_sync.barcode import decode_edges
from wl_sync.log import CodeWord, Edge, read_log

from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.syncbox import BARCODE_GPIO, SYNCBOX_PRE_ROLL_S, write_syncbox_log
from wl_preproc.synth.timeline import build_timeline


def emit(tmp_path, drift_ppm=0.0, name="syncbox.log"):
    truth = build_timeline(CI_RECIPE)
    path = tmp_path / name
    write_syncbox_log(path, CI_RECIPE, truth, drift_ppm=drift_ppm)
    return truth, read_log(path)


def barcode_edges(records):
    return [
        (r.tick_us, r.level)
        for r in records
        if isinstance(r, Edge) and r.gpio == BARCODE_GPIO
    ]


def test_header_validates_and_names_the_session(tmp_path):
    _, (header, _) = emit(tmp_path)
    assert header.session_id == CI_RECIPE.session_id
    assert header.rig == CI_RECIPE.rig
    assert header.written_at.tzinfo is not None


def test_barcodes_round_trip_through_the_real_decoder(tmp_path):
    """Written with wl-sync's encoder, read with wl-sync's decoder. If the
    generator and the pipeline ever disagree about the format, this fails."""
    truth, (_, records) = emit(tmp_path)
    decoded = decode_edges(barcode_edges(records), start_us=0)
    assert [b.value for b in decoded] == [v for v, _ in truth.barcodes]


def test_first_barcode_is_decodable(tmp_path):
    """Without a pre-roll the first frame sits at tick 0 with no preceding idle,
    and wl-sync's decoder correctly refuses it."""
    truth, (_, records) = emit(tmp_path)
    assert decode_edges(barcode_edges(records), start_us=0)[0].value == truth.barcodes[0][0]


def test_barcode_times_match_ground_truth_after_removing_the_pre_roll(tmp_path):
    """The log's tick origin is deliberately not session time. A fixture where
    they coincide lets a pipeline bug that ignores the offset pass."""
    truth, (_, records) = emit(tmp_path)
    decoded = decode_edges(barcode_edges(records), start_us=0)
    for barcode, (_, expected_s) in zip(decoded, truth.barcodes):
        assert barcode.start_us / 1e6 - SYNCBOX_PRE_ROLL_S == pytest.approx(
            expected_s, abs=1e-3
        )


def test_every_code_word_is_logged(tmp_path):
    truth, (_, records) = emit(tmp_path)
    logged = [r.word for r in records if isinstance(r, CodeWord)]
    assert logged == [w for _, w in truth.code_words]


def test_records_are_in_tick_order(tmp_path):
    _, (_, records) = emit(tmp_path)
    ticks = [r.tick_us for r in records]
    assert ticks == sorted(ticks)


def test_drift_stretches_the_timeline(tmp_path):
    """A device running 100 ppm fast puts its last barcode measurably late."""
    _, (_, clean) = emit(tmp_path, drift_ppm=0.0, name="clean.log")
    _, (_, drifted) = emit(tmp_path, drift_ppm=100.0, name="drift.log")
    assert max(r.tick_us for r in drifted) > max(r.tick_us for r in clean)


def test_emission_is_deterministic(tmp_path):
    truth = build_timeline(CI_RECIPE)
    first, second = tmp_path / "a.log", tmp_path / "b.log"
    write_syncbox_log(first, CI_RECIPE, truth)
    write_syncbox_log(second, CI_RECIPE, truth)
    assert first.read_text() == second.read_text()
