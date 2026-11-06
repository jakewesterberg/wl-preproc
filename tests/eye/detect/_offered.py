"""What `schema/detect.py::EyeDetection.make` hands a detector, restated
without a database for the tests run on the reference recording: the
validity mask on the gaze, its repaired glitches lost; then the gaze over a
carried loss filled along frame time, and the velocity of the filled gaze
(design spec `2026-11-06-eye-lost-data-and-region-design.md` section 3). The
final review of 2026-11-07 found the restated chains had not followed it, so
the detectors met lost samples' raw positions, which production never hands
them."""

from __future__ import annotations


def offered(gaze_deg, loss, frame_gaps, fs_hz, *, repaired=None):
    """`(gaze, velocity, mask)` as a detector is handed them."""
    from wl_preproc.eye.detect.validity import (
        DEFAULT_VALIDITY_PARAMS, fill_lost, frame_numbers, rows_across_gaps, validity_labels,
    )
    from wl_preproc.eye.detect.velocity import velocity

    mask = validity_labels(gaze_deg, velocity(gaze_deg, fs_hz), loss, frame_gaps, DEFAULT_VALIDITY_PARAMS,
                           fs_hz=fs_hz, repaired=repaired).labels
    n = len(gaze_deg)
    filled = fill_lost(gaze_deg, mask, frame_numbers(n, frame_gaps), rows_across_gaps(n, frame_gaps))
    return filled, velocity(filled, fs_hz), mask
