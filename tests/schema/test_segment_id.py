"""A segment is keyed by its file's path within its system's folder, not by
the first barcode it holds: a session without the sync box has no barcode on
any device (spec amendment 39). Subject and date checked unclaimed across
`tests/` on 2026-11-19."""

from __future__ import annotations

import pytest

from tests.schema.test_spikeglx_restart import RESTART, _session


@pytest.fixture(scope="module")
def landed(dj_conn, prefix, tmp_path_factory):
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    recipe, key = _session(tmp_path_factory, subject="segid1", session_id="2025-08-03_01", runs=True,
                           spikeglx_restart={**RESTART, "probe_bank": 1})
    daemon.run_once(prefix=prefix)
    return recipe, key


def test_a_segment_is_keyed_by_its_files_path(landed):
    from wl_preproc.schema import core

    from pathlib import Path

    from wl_preproc.schema import ingest

    _recipe, key = landed
    assert "file_path" not in core.Segment.heading.names
    assert core.Segment.primary_key == ["subject", "session_datetime", "system", "segment_id"]
    rows = (core.Segment & key & {"system": "spikeglx"}).to_dicts(order_by="start_s")
    assert len(rows) == 2
    root = Path((ingest.Ingestion & key).fetch1("session_dir")) / "spikeglx"
    on_disk = sorted(str(path.relative_to(root)) for path in root.rglob("*.nidq.bin"))
    assert sorted(row["segment_id"] for row in rows) == on_disk


def test_the_listing_names_segments_by_path(landed):
    from wl_preproc.listing.entry import session_entry
    from wl_preproc.schema import core

    _recipe, key = landed
    paths = [row["segment_id"] for row in (core.Segment & key & {"system": "spikeglx"}).to_dicts(order_by="start_s")]
    entry = session_entry(key)
    assert [segment["segment_id"] for segment in entry["segments"]] == paths
    assert all(isinstance(path, str) for run in entry["runs"] for path in run["segments"])
    assert entry["runs"][-1]["segments"] == paths
