"""`processing/timebase`: how every time in the file was put on one clock
(design spec `2026-09-28-nwb-builder-design.md` sections 3 and 4).

**Every time in the file is session seconds from the sync box's barcode,
the authoritative clock. A PTP stamp, when a device carries one, may appear
here only as a column labelled a cross-check, never as the timestamps of any
data** (section 4.4; wl-works' time-service design section 4)."""

from __future__ import annotations

from pynwb import NWBFile
from pynwb.core import DynamicTable

from wl_preproc.nwb.columns import column


def _table(name: str, description: str, rows: list[dict], fields: dict[str, str]) -> DynamicTable:
    return DynamicTable(name=name, description=description,
                        columns=[column(field, text, [row[field] for row in rows]) for field, text in fields.items()])


def add_timebase(nwb: NWBFile, provenance: dict, clocks: list[dict], segments: list[dict],
                 clock_reference: dict) -> None:
    """The session's timing provenance, every system's clock fit, every
    segment's placement on session time, and where the wall-clock time of
    t = 0 came from. With `system_clocks` and `segments` every native
    timestamp is recoverable (parent spec section 4.5)."""
    module = nwb.create_processing_module(
        name="timebase",
        description=("How every time in this file was placed on session time: t = 0 is the sync box's first "
                     "barcode, the authoritative clock. PTP, where present, is a cross-check only."),
    )
    module.add(_table("timing_provenance", "TimingProvenance: the session's timing tier and its evidence.", [provenance], {
        "tier": "A to D (parent spec section 4.7).",
        "n_systems_aligned": "Systems placed on session time.",
        "n_segments": "Segments aligned.",
        "n_rejected_segments": "Segments that could not be aligned.",
        "worst_residual_us": "Largest barcode residual, microseconds.",
        "worst_drift_ppm": "Largest clock drift, parts per million.",
    }))
    module.add(_table("system_clocks", "SystemTimebase: each system's fitted clock.", clocks, {
        "system": "Acquisition system.",
        "fit_status": "fitted, no_recording or unfittable.",
        "nominal_rate_hz": "The system's nominal sampling rate.",
        "fitted_rate_hz": "The rate fitted against the barcodes.",
        "drift_ppm": "Clock drift against the sync box, parts per million.",
        "residual_us_rms": "RMS barcode residual, microseconds.",
    }))
    module.add(_table("segments", "core.Segment: each recording file's placement on session time.", segments, {
        "system": "Acquisition system.",
        "file_path": "The recording file, relative to the session directory.",
        "first_sample": "Native index of the segment's first barcode.",
        "offset_s": "session_s = native_s / scale + offset_s.",
        "start_s": "Session time of the file's first sample.",
        "end_s": "Session time one sample past the file's last.",
        "n_samples": "True samples spanned, dropped frames included.",
    }))
    module.add(_table("clock_reference", "Where the wall-clock time of session t = 0 came from (section 4.2).",
                      [clock_reference], {
        "source": "barcode: the first barcode's own value (seconds since 2020-01-01 UTC); manifest: started_at.",
        "reference_time": "The wall-clock time of t = 0, ISO 8601 UTC.",
        "manifest_started_at": "The session manifest's started_at, ISO 8601 UTC.",
        "started_at_difference_s": "started_at minus the barcode's time of t = 0, seconds.",
    }))
