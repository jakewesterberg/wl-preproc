"""The sync box's log is read as the box writes it today (design specs
`2026-10-22-syncbox-records-and-segments-design.md` section 3 and
`2026-11-04-syncbox-header-design.md` sections 3 and 5): the barcodes are its
`B` records, with the measured delay added, and nothing is read from its
barcode line; the header's fields, the code words and the losses are
carried."""

from __future__ import annotations

import datetime

import pytest
from wl_sync.barcode import encode
from wl_sync.log import SCHEMA_VERSION, BarcodeEmitted, CodeWord, Edge, Loss, SyncBoxLogHeader, write_log
from wl_sync.pins import BARCODE_PIN, GPIO_MAP
from wl_sync.segment import SegmentWriter

from wl_preproc.timebase._syncbox_log import read_barcode_entries, read_syncbox_log
from wl_preproc.timebase.extract import extract_syncbox

PROCESS = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
WRITTEN_AT = datetime.datetime(2027, 3, 14, 9, tzinfo=datetime.UTC)


def _header(**fields) -> SyncBoxLogHeader:
    return SyncBoxLogHeader(schema_version=SCHEMA_VERSION, session_id="2027-03-14_01", rig="rig0", boot_id="b0",
                            written_at=WRITTEN_AT, gpio_map=GPIO_MAP, **fields)


def _real_shaped_log(path, **fields):
    """Written by wl-sync's own segment writer, trailer and all: the board's
    pin map, a photodiode's edges, code words, a loss on each path, and three
    barcodes the box emitted. Edges on the barcode pin carry a different value,
    which a reader of that pin would report and a reader of the box's records
    never does."""
    header = _header(segment_index=0, clock_trusted=False, clock_reason="ntp_unsynchronized", **fields)
    records = [Edge(tick_us=5_000_000, gpio=GPIO_MAP["pd1_comp"], level=1),
               CodeWord(tick_us=5_100_000, word=0x8001),
               Loss(tick_us=5_200_000, path="E", gpio=GPIO_MAP["pd1_comp"], count=2, bound="exact",
                    cause="buffer_overflow", before_us=5_000_000, after_us=5_200_000),
               CodeWord(tick_us=5_300_000, word=0x0042),
               Loss(tick_us=5_300_000, path="W", gpio=None, count=3, bound="at_least", cause="desync",
                    before_us=5_300_000, after_us=None)]
    tick = 9_000_000
    for level, duration_us in encode(4242):
        records.append(Edge(tick_us=tick, gpio=BARCODE_PIN, level=level))
        tick += duration_us
    records += [BarcodeEmitted(tick_us=t, value=v)
                for t, v in ((2_000_000, 912_345_678), (3_000_000, 912_345_679), (4_000_000, 912_345_680))]
    writer = SegmentWriter(path, header)
    for record in records:
        writer.write(record)
    writer.close(WRITTEN_AT + datetime.timedelta(seconds=10))
    return path


def test_the_barcodes_are_the_boxs_own_records_and_nothing_on_its_barcode_pin(tmp_path):
    path = _real_shaped_log(tmp_path / "seg-000.log")
    assert read_barcode_entries(path) == [(912_345_678, 2_000_000), (912_345_679, 3_000_000),
                                          (912_345_680, 4_000_000)]


def test_the_sync_box_stream_decodes_to_the_boxs_records(tmp_path):
    """`extract_syncbox` renders them into the edge form every device has,
    so every stage downstream reads them as it reads any device."""
    from wl_sync.barcode import decode_edges

    stream = extract_syncbox(_real_shaped_log(tmp_path / "seg-000.log"))
    assert [(b.value, b.start_us) for b in decode_edges(list(stream.edges))] == [
        (912_345_678, 2_000_000), (912_345_679, 3_000_000), (912_345_680, 4_000_000)]


def test_a_log_with_no_barcode_record_has_no_barcodes(tmp_path):
    path = tmp_path / "seg-000.log"
    write_log(path, _header(), [CodeWord(tick_us=1_000_000, word=0x8001)])
    assert read_barcode_entries(path) == []


def test_the_reader_carries_what_the_header_says(tmp_path):
    """Its process, its measured delay and the clocks its ticks count, beside
    the clock's trust as the box judged it (part 1's deferred finding: nothing
    pinned that these two were carried)."""
    log = read_syncbox_log(_real_shaped_log(tmp_path / "seg-000.log", process_uuid=PROCESS,
                                            barcode_delay_us=1_234, timebase="split"))
    assert (log.segment_index, log.boot_id, log.process_uuid, log.barcode_delay_us, log.timebase) == (
        0, "b0", PROCESS, 1_234, "split")
    assert (log.clock_trusted, log.clock_reason) == (False, "ntp_unsynchronized")


def test_the_barcode_delay_is_added_to_every_barcode_tick(tmp_path):
    """wl-sync's `barcode_delay_us`: "ADD IT TO A `B` TICK to get the wire
    time". Code words and losses are ticks of their own paths, unchanged."""
    log = read_syncbox_log(_real_shaped_log(tmp_path / "seg-000.log", process_uuid=PROCESS,
                                            barcode_delay_us=1_234))
    assert log.barcodes == ((912_345_678, 2_001_234), (912_345_679, 3_001_234), (912_345_680, 4_001_234))
    assert log.codes == ((5_100_000, 0x8001), (5_300_000, 0x0042))


def test_an_older_log_is_read_with_its_ticks_as_they_are(tmp_path):
    """A header from before 2026-10-30 names none of the three, and an
    unmeasured delay is not zero: each tick is used as it is."""
    log = read_syncbox_log(_real_shaped_log(tmp_path / "seg-000.log"))
    assert (log.process_uuid, log.barcode_delay_us, log.timebase) == (None, None, None)
    assert log.barcodes[0] == (912_345_678, 2_000_000)


def test_the_reader_carries_each_loss_as_the_box_wrote_it(tmp_path):
    log = read_syncbox_log(_real_shaped_log(tmp_path / "seg-000.log", process_uuid=PROCESS))
    assert [(loss.path, loss.cause, loss.count, loss.bound, loss.gpio, loss.before_us, loss.after_us)
            for loss in log.losses] == [
        ("E", "buffer_overflow", 2, "exact", GPIO_MAP["pd1_comp"], 5_000_000, 5_200_000),
        ("W", "desync", 3, "at_least", None, 5_300_000, None)]


def test_an_unreadable_segment_is_named(tmp_path):
    """Part 1's deferred finding: the error named nothing, so a session whose
    timing stages all refused gave no file to look at."""
    path = tmp_path / "seg-003.log"
    path.write_text('{"schema_version": 1, "session_id": "not a session"}\nB,1000000,7\n', encoding="utf-8")
    with pytest.raises(ValueError, match="seg-003.log"):
        read_syncbox_log(path)
