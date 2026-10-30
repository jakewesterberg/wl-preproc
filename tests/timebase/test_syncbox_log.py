"""The sync box's barcodes are read from its `B` records, as the box writes
them today (design spec `2026-10-22-syncbox-records-and-segments-design.md`
section 3): the box never captures its own barcode line, so nothing is read
from it, whatever a log's edges hold."""

from __future__ import annotations

import datetime

from wl_sync.barcode import encode
from wl_sync.log import SCHEMA_VERSION, BarcodeEmitted, CodeWord, Edge, SyncBoxLogHeader, write_log
from wl_sync.pins import BARCODE_PIN, GPIO_MAP

from wl_preproc.timebase._syncbox_log import read_barcode_entries
from wl_preproc.timebase.extract import extract_syncbox


def _real_shaped_log(path):
    """Written by wl-sync's own writer: the board's pin map, a photodiode's
    edges, code words, three barcodes the box emitted, and a trailer. Edges on
    the barcode pin carry a different value, which a reader of that pin would
    report and a reader of the box's records never does."""
    header = SyncBoxLogHeader(schema_version=SCHEMA_VERSION, session_id="2027-03-14_01", rig="rig0",
                              boot_id="b0", written_at=datetime.datetime(2027, 3, 14, 9, tzinfo=datetime.UTC),
                              gpio_map=GPIO_MAP, segment_index=0, clock_trusted=False,
                              clock_reason="ntp_unsynchronized")
    records = [Edge(tick_us=5_000_000, gpio=GPIO_MAP["pd1_comp"], level=1),
               CodeWord(tick_us=5_100_000, word=0x8001)]
    tick = 9_000_000
    for level, duration_us in encode(4242):
        records.append(Edge(tick_us=tick, gpio=BARCODE_PIN, level=level))
        tick += duration_us
    records += [BarcodeEmitted(tick_us=t, value=v)
                for t, v in ((2_000_000, 912_345_678), (3_000_000, 912_345_679), (4_000_000, 912_345_680))]
    write_log(path, header, records)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("T,4500000,clean\n")
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
    header = SyncBoxLogHeader(schema_version=SCHEMA_VERSION, session_id="2027-03-14_01", rig="rig0",
                              boot_id="b0", written_at=datetime.datetime(2027, 3, 14, 9, tzinfo=datetime.UTC),
                              gpio_map=GPIO_MAP)
    write_log(path, header, [CodeWord(tick_us=1_000_000, word=0x8001)])
    assert read_barcode_entries(path) == []
