import pytest
from wl_sync.log import BarcodeEmitted, CodeWord, Edge, read_log
from wl_sync.pins import BARCODE_PIN, GPIO_MAP

from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.syncbox import SYNCBOX_PRE_ROLL_S, write_syncbox_log
from wl_preproc.synth.timeline import build_timeline


def emit(tmp_path, drift_ppm=0.0, name="seg-000.log"):
    truth = build_timeline(CI_RECIPE)
    path = tmp_path / name
    write_syncbox_log(path, CI_RECIPE, truth, drift_ppm=drift_ppm)
    return truth, read_log(path)


def emitted(records):
    return [r for r in records if isinstance(r, BarcodeEmitted)]


def test_header_validates_and_names_the_session(tmp_path):
    _, (header, _) = emit(tmp_path)
    assert header.session_id == CI_RECIPE.session_id
    assert header.rig == CI_RECIPE.rig
    assert header.written_at.tzinfo is not None


def test_header_is_the_boards_and_the_first_segment(tmp_path):
    """The box's own pin map, not names of this project's: the synthetic log
    is the log the box writes (design spec
    `2026-10-22-syncbox-records-and-segments-design.md` section 3)."""
    _, (header, _) = emit(tmp_path)
    assert header.gpio_map == GPIO_MAP
    assert header.segment_index == 0


def test_every_barcode_is_a_record_of_the_box_and_none_is_drawn_on_its_pin(tmp_path):
    """The box logs each barcode it emits and never captures its own barcode
    line (wl-sync's `log.py`; its GPIO17 is an output)."""
    truth, (_, records) = emit(tmp_path)
    assert [r.value for r in emitted(records)] == [v for v, _ in truth.barcodes]
    assert not [r for r in records if isinstance(r, Edge) and r.gpio == BARCODE_PIN]


def test_barcode_times_match_ground_truth_after_removing_the_pre_roll(tmp_path):
    """The log's tick origin is deliberately not session time. A fixture where
    they coincide lets a pipeline bug that ignores the offset pass."""
    truth, (_, records) = emit(tmp_path)
    for record, (_, expected_s) in zip(emitted(records), truth.barcodes, strict=True):
        assert record.tick_us / 1e6 - SYNCBOX_PRE_ROLL_S == pytest.approx(expected_s, abs=1e-6)


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


def test_a_session_names_its_log_as_the_box_names_its_first_segment(tmp_path):
    from wl_preproc.synth.session import generate_session

    generate_session(tmp_path, CI_RECIPE)
    assert sorted(p.name for p in (tmp_path / CI_RECIPE.session_id / "syncbox").glob("*.log")) == ["seg-000.log"]
