"""Where the wall-clock time of session t = 0 comes from (design spec
`2026-09-28-nwb-builder-design.md` section 4.2)."""

from __future__ import annotations

import datetime


def test_the_first_barcode_places_t0_unless_the_clocks_disagree():
    """Section 4.2. Within 60 s of the manifest the barcode wins; beyond it
    (a synthetic session's counter barcodes start at 1,000,000) the manifest
    does, and the source says which."""
    from wl_preproc.nwb.gather import BARCODE_EPOCH, reference_from

    started = datetime.datetime(2026, 9, 28, 12, 0, 30)
    value = int((started.replace(tzinfo=datetime.timezone.utc) - BARCODE_EPOCH).total_seconds()) - 12
    near = reference_from(value, started)
    assert near["source"] == "barcode"
    assert near["reference_time"] == datetime.datetime(2026, 9, 28, 12, 0, 18, tzinfo=datetime.timezone.utc)
    assert near["started_at_difference_s"] == 12.0

    far = reference_from(1_000_000, started)
    assert far["source"] == "manifest"
    assert far["reference_time"] == started.replace(tzinfo=datetime.timezone.utc)


def test_the_barcode_epoch_is_wl_syncs_own():
    """One definition, wl-sync's: the pinned wl-sync carries its `clock`
    module, so nothing here restates it (part 1's deferred finding)."""
    from wl_sync import clock

    from wl_preproc.nwb.gather import BARCODE_EPOCH
    from wl_preproc.timebase import clock as timebase_clock

    assert BARCODE_EPOCH is clock.BARCODE_EPOCH is timebase_clock.BARCODE_EPOCH
