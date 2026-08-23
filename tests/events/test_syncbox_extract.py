"""Per-system code extraction. Pure logic against synthetic fixtures."""

from __future__ import annotations

import pytest

from wl_preproc.events import extract


def test_syncbox_words_come_back_in_order_with_their_ticks(tmp_path):
    """The Pi decodes the 16 lines itself and logs a CodeWord record, so its
    extraction is a read rather than a decode. Native ticks, not session time:
    converting is the fit's job, exactly as timebase/extract.py keeps them
    apart so the transform stays reversible (spec section 4.5)."""
    from wl_sync.log import CodeWord, SyncBoxLogHeader, write_log

    header = SyncBoxLogHeader(
        schema_version=1,
        session_id="2026-01-01_01",
        rig="rigA",
        boot_id="synth00000001",
        written_at="2026-01-01T00:00:00Z",
        gpio_map={"barcode_out": 17, "code_strobe": 27, "code_data_base": 2},
    )
    records = [CodeWord(tick_us=1_000, word=32), CodeWord(tick_us=2_500, word=0x8001)]
    path = tmp_path / "sync.jsonl"
    write_log(path, header, records)

    stream = extract.extract_syncbox_words(path)
    assert stream.words == ((1_000, 32), (2_500, 0x8001))


def test_syncbox_ignores_edge_records(tmp_path):
    """The log interleaves barcode Edges with CodeWords. Only the latter are
    words; an Edge reaching decode_stream would be a code that was never sent."""
    from wl_sync.log import CodeWord, Edge, SyncBoxLogHeader, write_log

    header = SyncBoxLogHeader(
        schema_version=1,
        session_id="2026-01-01_02",
        rig="rigA",
        boot_id="synth00000002",
        written_at="2026-01-01T00:00:00Z",
        gpio_map={"barcode_out": 17, "code_strobe": 27, "code_data_base": 2},
    )
    records = [
        Edge(tick_us=500, gpio=17, level=1),
        CodeWord(tick_us=1_000, word=32),
        Edge(tick_us=1_200, gpio=17, level=0),
    ]
    path = tmp_path / "sync.jsonl"
    write_log(path, header, records)

    assert extract.extract_syncbox_words(path).words == ((1_000, 32),)
