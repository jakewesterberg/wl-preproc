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


def _segment(syncbox_dir, name, index, records, **fields):
    syncbox_dir.mkdir(parents=True, exist_ok=True)
    header = SyncBoxLogHeader(schema_version=SCHEMA_VERSION, session_id="2027-03-14_01", rig="rig0",
                              boot_id="b0", written_at=datetime.datetime(2027, 3, 14, 9, tzinfo=datetime.UTC),
                              gpio_map=GPIO_MAP, segment_index=index, **fields)
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
    assert segments.read_syncbox_session(tmp_path / "s").roles() == ["no_barcode", "session_time"]
    assert segments.session_reference(tmp_path / "s") == {70: 0.0, 71: 1.0}


def test_a_box_that_logged_no_barcode_in_any_segment_gives_no_session_time(tmp_path):
    syncbox_dir = tmp_path / "s" / "syncbox"
    _segment(syncbox_dir, "seg-000.log", 0, [CodeWord(tick_us=500_000, word=0x8001)])
    with pytest.raises(ValueError, match="no barcodes"):
        segments.session_reference(tmp_path / "s")


def test_each_segment_has_a_role(tmp_path):
    """Session time's segment; one that logged no barcode, before it (a box
    that crashed within its first second) or after; and one after a restart,
    on another clock, waiting for part 2's bridge (section 6)."""
    syncbox_dir = tmp_path / "s" / "syncbox"
    _segment(syncbox_dir, "seg-000.log", 0, [CodeWord(tick_us=500_000, word=0x8001)])
    _segment(syncbox_dir, "seg-001.log", 1, [BarcodeEmitted(tick_us=2_000_000, value=70)])
    _segment(syncbox_dir, "seg-002.log", 2, [BarcodeEmitted(tick_us=500_000, value=75)])
    _segment(syncbox_dir, "seg-003.log", 3, [CodeWord(tick_us=600_000, word=0x8002)])
    assert segments.segment_roles(segments.syncbox_logs(tmp_path / "s")) == [
        "no_barcode", "session_time", "after_restart_unbridged", "no_barcode"]


# Design spec `2026-11-04-syncbox-header-design.md` sections 4 and 5: a clock
# is a process of the box, and a header from 2026-10-30 on names it.

PROCESS = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
LATER_PROCESS = "16fd2706-8baf-433b-82eb-8c7fada847da"


def _barcodes(*pairs):
    return [BarcodeEmitted(tick_us=round(tick_s * 1e6), value=value) for tick_s, value in pairs]


def _codes(*pairs):
    return [CodeWord(tick_us=round(tick_s * 1e6), word=word) for tick_s, word in pairs]


def _rolled(tmp_path, *later, process=PROCESS):
    """`seg-000.log` of `PROCESS`, its first frame the late one, then
    `seg-001.log` of `process` holding `later`."""
    syncbox_dir = tmp_path / "s" / "syncbox"
    _segment(syncbox_dir, "seg-000.log", 0, _barcodes((1.0, 100), (2.0, 101), (3.0, 102)) + _codes((2.5, 0x8001)),
             process_uuid=PROCESS)
    _segment(syncbox_dir, "seg-001.log", 1, list(later), process_uuid=process)
    return segments.read_syncbox_session(tmp_path / "s")


def test_a_roll_in_place_is_one_clock(tmp_path):
    """Two segments of one process in one folder are not a restart: their
    ticks compare by number, and session time and its codes take both."""
    session = _rolled(tmp_path, *_barcodes((4.0, 103), (5.0, 104)), *_codes((4.5, 0x8002)))
    assert (session.follows, session.roles()) == ((None, "adjacent"), ["session_time", "session_time"])
    assert session.reference() == {101: 0.0, 102: 1.0, 103: 2.0, 104: 3.0}
    assert session.codes() == [(0.5, 0x8001), (2.5, 0x8002)]
    assert session.gaps() == []


def test_a_roll_in_place_may_skip_one_value(tmp_path):
    """NTP's first sync can step the clock, and the frame grid placed again
    skips one value about 1.6 s on: adjacent, not a gap."""
    session = _rolled(tmp_path, *_barcodes((4.6, 104), (5.6, 105)))
    assert session.follows[1] == "adjacent" and session.gaps() == []


def test_a_gap_within_one_process_keeps_one_clock_and_is_named(tmp_path):
    """The box recorded elsewhere, on the same clock, and came back: session
    time is unbroken, and the stretch whose code words are not here is the
    gap's."""
    session = _rolled(tmp_path, *_barcodes((40.0, 140), (41.0, 141)), *_codes((40.2, 0x8003)))
    assert (session.follows[1], session.roles()) == ("gap", ["session_time", "session_time"])
    assert session.reference()[140] == 38.0
    assert session.gaps() == [(1, 3_000_000, 40_000_000)]


@pytest.mark.parametrize(("later", "why"), [
    (_barcodes((0.5, 103)), "its first tick is before the last of the segment it follows"),
    (_barcodes((4.0, 101)), "its barcode values do not go forward"),
    (_barcodes((30.0, 103)), "its ticks step 27.0 s and its barcode values 1 s"),
])
def test_a_contradiction_of_one_clock_is_read_as_a_restart(tmp_path, later, why):
    """Decision 3.1: the box's claim is contradicted, so neither reading is
    safe, and the later segment waits for a bridge as a restart's does."""
    session = _rolled(tmp_path, *later)
    assert session.follows[1] == f"contradiction: {why}"
    assert session.roles() == ["session_time", "after_restart_unbridged"]
    assert sorted(session.reference()) == [101, 102]


def test_each_processs_late_first_barcode_is_dropped_and_an_older_logs_is_kept(tmp_path):
    """wl-sync: "EXCEPT THE FIRST `B` OF EACH `process_uuid`", whose frame
    goes out later than its tick says by an amount nothing measures. A folder
    cannot show whether its process began in it, so its first is dropped
    either way; a log from before the field names no process, and is read as
    part 1 read it."""
    session = _rolled(tmp_path, *_barcodes((0.4, 200), (1.4, 201)), process=LATER_PROCESS)
    assert session.kept == (((101, 2_000_000), (102, 3_000_000)), ((201, 1_400_000),))
    assert session.roles() == ["session_time", "after_restart_unbridged"]
    older = tmp_path / "older" / "syncbox"
    _segment(older, "seg-000.log", 0, _barcodes((1.0, 100), (2.0, 101)))
    assert segments.session_reference(tmp_path / "older") == {100: 0.0, 101: 1.0}


def test_a_segment_of_session_times_clock_that_no_barcode_places_still_places_its_codes(tmp_path):
    """A roll whose segment ends before its first frame: its codes are on
    session time's clock, and it is neither a segment nor a rejection."""
    session = _rolled(tmp_path, *_codes((3.4, 0x8002)))
    assert (session.follows[1], session.roles()) == ("adjacent", ["session_time", "session_time"])
    assert session.codes() == [(0.5, 0x8001), (1.4, 0x8002)]
    assert [scan.path.name for scan in segments.syncbox_scans(tmp_path / "s")] == ["seg-000.log"]


def test_a_barcode_at_tick_0_places_its_segment(tmp_path):
    """Part 1's deferred finding: rendered into edges and decoded back, a
    barcode within 400 ms of a segment's tick 0 had no idle before it and was
    lost."""
    syncbox_dir = tmp_path / "s" / "syncbox"
    _segment(syncbox_dir, "seg-000.log", 0, _barcodes((0.1, 50), (1.1, 51)))
    assert segments.session_reference(tmp_path / "s") == {50: 0.0, 51: 1.0}
    (scan,) = segments.syncbox_scans(tmp_path / "s")
    assert [(barcode.value, barcode.start_us) for barcode in scan.barcodes] == [(50, 100_000), (51, 1_100_000)]


def test_each_log_is_read_once_however_many_answers_a_stage_asks_for(tmp_path, monkeypatch):
    """Part 1's deferred finding: session time, the box's scans and its codes
    each read every log again."""
    from wl_preproc.timebase import _syncbox_log

    session_dir, _truth = _restarted(tmp_path)
    reads = []
    real = _syncbox_log.read_log
    monkeypatch.setattr(_syncbox_log, "read_log", lambda path: reads.append(path.name) or real(path))
    _syncbox_log._read.cache_clear()
    segments.session_reference(session_dir)
    segments.scan_system("syncbox", session_dir / "syncbox")
    syncbox_codes(session_dir)
    assert sorted(reads) == ["seg-000.log", "seg-001.log"]
