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
    assert [r.value for r in emitted(records)] == [truth.barcodes[0][0] - 1, *(v for v, _ in truth.barcodes)]
    assert not [r for r in records if isinstance(r, Edge) and r.gpio == BARCODE_PIN]


def test_barcode_times_match_ground_truth_after_removing_the_pre_roll(tmp_path):
    """The log's tick origin is deliberately not session time. A fixture where
    they coincide lets a pipeline bug that ignores the offset pass."""
    truth, (_, records) = emit(tmp_path)
    for record, (_, expected_s) in zip(emitted(records)[1:], truth.barcodes, strict=True):
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


# Design spec `2026-11-04-syncbox-header-design.md` section 10: the box's
# headers since 2026-10-30, its late first frame, a roll, a gap, a restart's
# new process, and its losses.


def _session(tmp_path, **update):
    from wl_preproc.synth.session import generate_session

    recipe = CI_RECIPE.model_copy(update=update)
    truth = generate_session(tmp_path, recipe)
    return recipe, truth, [read_log(path) for path in sorted((tmp_path / recipe.session_id / "syncbox").glob("*.log"))]


def test_the_header_names_the_process_its_clocks_and_its_delay(tmp_path):
    from wl_preproc.synth.syncbox import process_uuid

    _, (header, _) = emit(tmp_path)
    assert (header.process_uuid, header.timebase, header.barcode_delay_us) == (
        process_uuid(CI_RECIPE, 0), "fake", None)


def test_a_measured_delay_is_named_and_every_barcode_tick_read_that_much_before_its_frame(tmp_path):
    truth, (_, plain) = emit(tmp_path)
    _, _, [(header, delayed)] = _session(tmp_path / "delayed", syncbox_barcode_delay_us=1_500)
    assert header.barcode_delay_us == 1_500
    assert [r.tick_us for r in emitted(delayed)][1:] == [r.tick_us - 1_500 for r in emitted(plain)][1:]


def test_the_process_starts_with_its_late_first_frame_which_no_device_records(tmp_path):
    """One interval before session time's first barcode, at its clock's
    origin; a reader drops it."""
    truth, (_, records) = emit(tmp_path)
    first = emitted(records)[0]
    assert (first.tick_us, first.value) == (0, truth.barcodes[0][0] - 1)
    assert first.value not in {value for value, _ in truth.barcodes}


def test_a_roll_in_place_writes_a_second_segment_of_the_same_process_on_the_same_clock(tmp_path):
    """The barcode steps by two across it, the skipped value emitted by
    nobody, and the ticks run on."""
    from wl_preproc.synth.recipe import SyncBoxRollSpec

    _, truth, [(first, before), (second, after)] = _session(tmp_path, syncbox_roll=SyncBoxRollSpec(at_s=7.5))
    assert (first.segment_index, second.segment_index) == (0, 1)
    assert first.process_uuid == second.process_uuid
    assert emitted(after)[0].value - emitted(before)[-1].value == 2
    assert emitted(after)[0].value - 1 not in {value for value, _ in truth.barcodes}
    assert min(r.tick_us for r in after) > max(r.tick_us for r in before)


def test_a_gap_leaves_its_records_out_of_the_folder_but_not_out_of_the_devices(tmp_path):
    from wl_preproc.synth.recipe import SyncBoxRollSpec

    _, truth, [(_, before), (_, after)] = _session(tmp_path, syncbox_roll=SyncBoxRollSpec(at_s=5.5, gap_s=3.0))
    logged = {r.value for r in emitted(before) + emitted(after)}
    assert {value for value, start_s in truth.barcodes if 5.5 <= start_s < 8.5} == {1_000_006, 1_000_007, 1_000_008}
    assert not logged & {1_000_006, 1_000_007, 1_000_008}
    assert not [r for r in before + after if isinstance(r, CodeWord) and 6_500_000 <= r.tick_us < 9_500_000]


def test_a_restart_is_a_new_process(tmp_path):
    from wl_preproc.synth.recipe import SyncBoxRestartSpec

    _, _, [(first, _), (second, _)] = _session(tmp_path, syncbox_restart=SyncBoxRestartSpec(at_s=7.5))
    assert first.process_uuid != second.process_uuid


def test_losses_are_written_as_the_box_writes_them(tmp_path):
    from wl_sync.log import Loss

    from wl_preproc.synth.recipe import SyncBoxLossSpec

    _, _, [(_, records)] = _session(tmp_path, syncbox_losses=(
        SyncBoxLossSpec(cause="desync", count=3, from_s=4.0),
        SyncBoxLossSpec(cause="buffer_overflow", count=2, from_s=2.0, to_s=2.5, gpio=GPIO_MAP["pd1_comp"])))
    assert [r for r in records if isinstance(r, Loss)] == [
        Loss(tick_us=3_500_000, path="E", gpio=GPIO_MAP["pd1_comp"], count=2, bound="exact",
             cause="buffer_overflow", before_us=3_000_000, after_us=3_500_000),
        Loss(tick_us=5_000_000, path="W", gpio=None, count=3, bound="at_least", cause="desync",
             before_us=5_000_000, after_us=None)]


def test_each_segment_closes_with_wl_syncs_trailer(tmp_path):
    """Part 1's deferred finding: a hand-written trailer was not wl-sync's."""
    emit(tmp_path)
    trailer = (tmp_path / "seg-000.log").read_text(encoding="utf-8").splitlines()[-1]
    assert trailer.startswith("T,2027-03-14T09:00:00+00:00,999999,")


@pytest.mark.parametrize(("update", "match"), [
    ({"syncbox_restart": {"at_s": 7.5}, "syncbox_roll": {"at_s": 3.0}}, "together"),
    ({"syncbox_restart": {"at_s": 14.0, "gap_s": 1.2}}, "syncbox_restart resumes"),
    ({"syncbox_roll": {"at_s": 14.5, "gap_s": 1.0}}, "syncbox_roll resumes"),
    ({"syncbox_losses": ({"cause": "desync", "from_s": 2.0, "to_s": 3.0},)}, "after_us"),
    ({"syncbox_losses": ({"cause": "coalesced", "from_s": 2.0, "to_s": 2.1},)}, "names its line"),
    ({"syncbox_losses": ({"cause": "desync", "from_s": 20.0},)}, "outside the session"),
])
def test_a_sync_box_change_the_session_cannot_hold_is_refused(update, match):
    """Part 1's deferred finding: nothing checked the restart's fit to the
    session, and the roll and the losses are checked the same way."""
    from pydantic import ValidationError

    from wl_preproc.synth.recipe import SessionRecipe

    with pytest.raises(ValidationError, match=match):
        SessionRecipe.model_validate({**CI_RECIPE.model_dump(), **update})


def test_a_session_generated_again_over_its_folder_writes_its_log_again(tmp_path):
    """wl-sync's writer refuses a file that exists, as the box must; a
    fixture regenerated over its own folder is not the box."""
    from wl_preproc.synth.session import generate_session

    generate_session(tmp_path, CI_RECIPE)
    generate_session(tmp_path, CI_RECIPE)
    assert read_log(tmp_path / CI_RECIPE.session_id / "syncbox" / "seg-000.log")[1]
