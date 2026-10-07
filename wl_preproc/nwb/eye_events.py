"""`processing/eye_events`: every detector's events (design spec
`2026-09-28-nwb-builder-design.md` section 3; the requester's decision: all
six detectors, each labelled -- every registered detector, seven since
U'n'Eye, 2026-10-06)."""

from __future__ import annotations

import numpy as np
from pynwb import NWBFile
from pynwb.core import DynamicTable
from pynwb.epoch import TimeIntervals

from wl_preproc.nwb.columns import column

MEASUREMENTS = {
    "amplitude_deg": "Amplitude, degrees (saccadic rows; NaN otherwise or when unmeasured).",
    "peak_velocity_deg_s": "Peak velocity, degrees per second (saccadic rows; NaN otherwise).",
    "start_x_deg": "Gaze x at the start, degrees (saccadic rows; NaN otherwise).",
    "start_y_deg": "Gaze y at the start, degrees (saccadic rows; NaN otherwise).",
    "end_x_deg": "Gaze x at the end, degrees (saccadic rows; NaN otherwise).",
    "end_y_deg": "Gaze y at the end, degrees (saccadic rows; NaN otherwise).",
    "direction_deg": "Direction, degrees counterclockwise from rightward (NaN otherwise or for no displacement).",
    "reliability": "The detector's own reliability, where it reports one (NaN otherwise).",
}


def _module(nwb: NWBFile):
    if "eye_events" not in nwb.processing:
        nwb.create_processing_module(
            name="eye_events",
            description=("Every registered detector's events, per eye and for both eyes together, and how much "
                         "the detectors agree. Times are session seconds; each table names its detector."),
        )
    return nwb.processing["eye_events"]


def add_detections(nwb: NWBFile, tables: list[dict]) -> None:
    """One `TimeIntervals` per detector per trace. Each of `tables` has
    `name` (`{detector}_{trace}`), `description` (the detector and its
    parameters) and `runs`, each with `start_s`, `stop_s`, `label` and the
    `MEASUREMENTS` (None where absent)."""
    module = _module(nwb)
    for table in tables:
        runs = sorted(table["runs"], key=lambda run: run["start_s"])
        module.add(TimeIntervals(
            name=table["name"],
            description=table["description"],
            columns=[column("start_time", "Start, session seconds.", [r["start_s"] for r in runs]),
                     column("stop_time", "Stop, session seconds (the time just after the run's last sample).",
                            [r["stop_s"] for r in runs]),
                     column("label", "saccade, microsaccade, pso, fixation, pursuit or drift.", [r["label"] for r in runs]),
                     *[column(field, text, [np.nan if r.get(field) is None else r[field] for r in runs])
                       for field, text in MEASUREMENTS.items()]],
        ))


def add_sources(nwb: NWBFile, tables: list[dict]) -> None:
    """One `TimeIntervals` per detector, `{detector}_source`: which eye each
    stretch of the both-eyes trace's labels came from (`EyeDetection.Source`)."""
    module = _module(nwb)
    for table in tables:
        runs = sorted(table["runs"])
        module.add(TimeIntervals(
            name=table["name"],
            description=table["description"],
            columns=[column("start_time", "Start, session seconds.", [r[0] for r in runs]),
                     column("stop_time", "Stop, session seconds.", [r[1] for r in runs]),
                     column("source", "both, left, right or neither.", [r[2] for r in runs])],
        ))


def add_agreement(nwb: NWBFile, rows: list[dict]) -> None:
    """`detector_agreement`: `DetectorAgreement`'s scores between detectors."""
    if not rows:
        return
    fields = {
        "detector_a": "The first detector.", "detector_b": "The second detector.",
        "trace": "left, right or conjunction.", "metric": "The agreement metric.",
        "vocabulary": "Which labels were compared.", "pso_as": "How glissades were counted.",
        "value": "The score.", "n_samples_compared": "Samples the score is over.",
    }
    _module(nwb).add(DynamicTable(
        name="detector_agreement",
        description="DetectorAgreement: how much each pair of detectors agrees, over the whole session.",
        columns=[column(field, text, [row[field] for row in rows]) for field, text in fields.items()],
    ))
