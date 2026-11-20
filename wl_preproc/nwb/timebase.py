"""`processing/timebase`: how every time in the file was put on one clock
(design spec `2026-09-28-nwb-builder-design.md` sections 3 and 4).

**Every time in the file is session seconds on the session's clock: the sync
box's barcode, the authoritative clock, or, where the sync box did not record
the session throughout, the NI's first sample (design spec
`2026-10-18-training-records-and-backup-clock-design.md`, amendments 46 and
50). A PTP stamp, when a device carries one, may appear here only as a column
labelled a cross-check, never as the timestamps of any data** (section 4.4;
wl-works' time-service design section 4). Every text is the sync box's unless
the NI kept time."""

from __future__ import annotations

from pynwb import NWBFile
from pynwb.core import DynamicTable

from wl_preproc.nwb.columns import column


def _table(name: str, description: str, rows: list[dict], fields: dict[str, str]) -> DynamicTable:
    return DynamicTable(name=name, description=description,
                        columns=[column(field, text, [row[field] for row in rows]) for field, text in fields.items()])


# Each text that names the clock, by `timebase.SessionClock`'s source: the
# sync box's as they always were, and the NI's (part 3b-1).
_TEXTS = {
    "syncbox": {
        "module": ("How every time in this file was placed on session time: t = 0 is the sync box's first "
                   "barcode, the authoritative clock. PTP, where present, is a cross-check only."),
        "tier": "A to D (parent spec section 4.7).",
        "drift": "Clock drift against the sync box, parts per million.",
        "source": "barcode: the first barcode's own value (seconds since 2020-01-01 UTC); manifest: started_at.",
        "difference": "started_at minus the barcode's time of t = 0, seconds.",
        "worst_residual": "Largest barcode residual, microseconds.",
        "fitted_rate": "The rate fitted against the barcodes.",
        "residual_rms": "RMS barcode residual, microseconds.",
        "first_sample": "Native index of the segment's first barcode.",
    },
    "nidq": {
        "module": ("How every time in this file was placed on session time: t = 0 is the NI's first sample, the "
                   "NI keeping time because the sync box did not record the session throughout. PTP, where "
                   "present, is a cross-check only."),
        "tier": ("A to D, or N: the NI kept time and its codes hold every run (parent spec section 4.7; design "
                 "spec 2026-10-18 section 7)."),
        "drift": "Clock drift against the session's clock, parts per million.",
        "source": ("barcode: the first barcode's own value (seconds since 2020-01-01 UTC); manifest: started_at; "
                   "rig_clock: the rig's clock fit to session time (core.RigClock.rig_at_zero); nidq_meta: the "
                   "first NI file's fileCreateTime, in the NI machine's time zone. Under the NI, rig_clock, else "
                   "nidq_meta, else manifest, each within 60 s of started_at."),
        "difference": ("started_at minus the source's time of t = 0, seconds; for manifest, minus the first of "
                       "rig_clock and nidq_meta that disagreed, NaN where neither gave a time."),
        # No barcode places anything under the NI: SpikeGLX is on the identity
        # map, every other system not placed yet (amendments 50 and 51).
        "worst_residual": ("Largest residual, microseconds: under the NI, 0, SpikeGLX being on the identity map and "
                           "no barcode fitted."),
        "fitted_rate": ("The rate on the session's clock: under the NI, SpikeGLX's nominal rate, on the identity map "
                        "with no barcode fitted; NaN for a system not placed on the NI's clock."),
        "residual_rms": ("RMS residual, microseconds: under the NI, 0 for SpikeGLX, on the identity map with no "
                         "barcode fitted; NaN for a system not placed on the NI's clock."),
        "first_sample": ("Native index the segment's offset is anchored to: under the NI, 0, its first sample, the "
                         "NI's first file being session time on the identity map; no barcode anchors it."),
    },
}


def add_timebase(nwb: NWBFile, provenance: dict, clocks: list[dict], segments: list[dict],
                 clock_reference: dict, clock: str) -> None:
    """The session's timing provenance, every system's clock fit, every
    segment's placement on session time, and where the wall-clock time of
    t = 0 came from, described by `clock`, the session's clock
    (`timebase.SessionClock.source`). With `system_clocks` and `segments`
    every native timestamp is recoverable (parent spec section 4.5)."""
    texts = _TEXTS[clock]
    module = nwb.create_processing_module(name="timebase", description=texts["module"])
    module.add(_table("timing_provenance", "TimingProvenance: the session's timing tier and its evidence.", [provenance], {
        "tier": texts["tier"],
        "n_systems_aligned": "Systems placed on session time.",
        "n_segments": "Segments aligned.",
        "n_rejected_segments": "Segments that could not be aligned.",
        "worst_residual_us": texts["worst_residual"],
        "worst_drift_ppm": "Largest clock drift, parts per million.",
    }))
    module.add(_table("system_clocks", "SystemTimebase: each system's fitted clock.", clocks, {
        "system": "Acquisition system.",
        "fit_status": "fitted, no_recording or unfittable.",
        "nominal_rate_hz": "The system's nominal sampling rate.",
        "fitted_rate_hz": texts["fitted_rate"],
        "drift_ppm": texts["drift"],
        "residual_us_rms": texts["residual_rms"],
    }))
    module.add(_table("segments", "core.Segment: each recording file's placement on session time.", segments, {
        "system": "Acquisition system.",
        "file_path": "The recording file, relative to its system's folder.",
        "first_sample": texts["first_sample"],
        "offset_s": "session_s = native_s / scale + offset_s.",
        "start_s": "Session time of the file's first sample.",
        "end_s": "Session time one sample past the file's last.",
        "n_samples": "True samples spanned, dropped frames included.",
    }))
    module.add(_table("clock_reference", "Where the wall-clock time of session t = 0 came from (section 4.2).",
                      [clock_reference], {
        "source": texts["source"],
        "reference_datetime": "The wall-clock time of t = 0, ISO 8601 UTC.",
        "manifest_started_at": "The session manifest's started_at, ISO 8601 UTC.",
        "started_at_difference_s": texts["difference"],
    }))
