"""Each sync box segment is its own clock (design spec
`2026-10-22-syncbox-records-and-segments-design.md` section 4). A segment is one
run of the box's process, and "ticks after a restart are not comparable BY
NUMBER to ticks before it" (wl-sync's `segment.py`); a restart resumes into the
open session's folder. Session time comes from the first segment holding a
barcode, and a later segment's records are never mixed into it."""

from __future__ import annotations

import datetime

import pytest
from wl_sync.log import SCHEMA_VERSION, BarcodeEmitted, CodeWord, SyncBoxLogHeader, write_log
from wl_sync.pins import GPIO_MAP

from wl_preproc.synth.recipe import CI_RECIPE, SyncBoxRestartSpec
from wl_preproc.synth.session import generate_session
from wl_preproc.timebase import segments
from wl_preproc.timebase.clock import syncbox_codes

RESTART = CI_RECIPE.model_copy(update={"syncbox_restart": SyncBoxRestartSpec(at_s=7.5, gap_s=1.2)})


def _restarted(tmp_path):
    truth = generate_session(tmp_path, RESTART)
    return tmp_path / RESTART.session_id, truth


def _segment(syncbox_dir, name, index, records):
    syncbox_dir.mkdir(parents=True, exist_ok=True)
    header = SyncBoxLogHeader(schema_version=SCHEMA_VERSION, session_id="2027-03-14_01", rig="rig0",
                              boot_id="b0", written_at=datetime.datetime(2027, 3, 14, 9, tzinfo=datetime.UTC),
                              gpio_map=GPIO_MAP, segment_index=index)
    write_log(syncbox_dir / name, header, records)


def test_a_restarted_box_writes_two_segments_on_unrelated_clocks(tmp_path):
    """The fixture is the case: the box emits nothing while down, so no device
    records a barcode in the gap, and the second segment's ticks start again
    below the first's."""
    session_dir, truth = _restarted(tmp_path)
    assert 1_000_008 not in {value for value, _ in truth.barcodes}
    first, second = segments.syncbox_logs(session_dir)
    assert (first.path.name, first.segment_index, second.path.name, second.segment_index) == (
        "seg-000.log", 0, "seg-001.log", 1)
    assert [value for value, _ in first.barcodes] == list(range(1_000_000, 1_000_008))
    assert [value for value, _ in second.barcodes] == list(range(1_000_009, 1_000_015))
    assert second.barcodes[0][1] < first.barcodes[-1][1]


def test_segments_are_ordered_by_their_index_not_their_names(tmp_path):
    """`seg-1000.log` sorts before `seg-999.log` by name; the header's
    `segment_index` is what orders them."""
    syncbox_dir = tmp_path / "s" / "syncbox"
    _segment(syncbox_dir, "seg-999.log", 999, [BarcodeEmitted(tick_us=1_000_000, value=50)])
    _segment(syncbox_dir, "seg-1000.log", 1000, [BarcodeEmitted(tick_us=1_000_000, value=90)])
    assert [log.segment_index for log in segments.syncbox_logs(tmp_path / "s")] == [999, 1000]
    assert segments.session_reference(tmp_path / "s") == {50: 0.0}


def test_session_time_comes_from_the_first_segment_alone(tmp_path):
    session_dir, truth = _restarted(tmp_path)
    reference = segments.session_reference(session_dir)
    assert sorted(reference) == list(range(1_000_000, 1_000_008))
    for value, start_s in truth.barcodes:
        if value in reference:
            assert reference[value] == pytest.approx(start_s, abs=1e-6)


def test_the_boxs_codes_after_a_restart_are_left_out_not_mixed_in(tmp_path):
    session_dir, truth = _restarted(tmp_path)
    codes = syncbox_codes(session_dir)
    before = [(time_s, word) for time_s, word in truth.code_words if time_s < RESTART.syncbox_restart.at_s]
    assert [word for _, word in codes] == [word for _, word in before]
    for (got_s, _), (want_s, _) in zip(codes, before, strict=True):
        assert got_s == pytest.approx(want_s, abs=1e-5)


def test_a_first_segment_with_no_barcode_leaves_session_time_to_the_next(tmp_path):
    """A box that crashed within its first second logged no barcode there."""
    syncbox_dir = tmp_path / "s" / "syncbox"
    _segment(syncbox_dir, "seg-000.log", 0, [CodeWord(tick_us=500_000, word=0x8001)])
    _segment(syncbox_dir, "seg-001.log", 1, [BarcodeEmitted(tick_us=2_000_000, value=70),
                                             BarcodeEmitted(tick_us=3_000_000, value=71)])
    assert segments.session_log(tmp_path / "s").segment_index == 1
    assert segments.session_reference(tmp_path / "s") == {70: 0.0, 71: 1.0}


def test_a_box_that_logged_no_barcode_in_any_segment_gives_no_session_time(tmp_path):
    syncbox_dir = tmp_path / "s" / "syncbox"
    _segment(syncbox_dir, "seg-000.log", 0, [CodeWord(tick_us=500_000, word=0x8001)])
    with pytest.raises(ValueError, match="no barcodes"):
        segments.session_reference(tmp_path / "s")
