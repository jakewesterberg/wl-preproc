"""`processing/behavior`: the eye as recorded and as the mask judged it
(design spec `2026-09-28-nwb-builder-design.md` section 3)."""

from __future__ import annotations

import numpy as np
from pynwb import NWBFile
from pynwb.base import TimeSeries
from pynwb.behavior import EyeTracking, PupilTracking, SpatialSeries
from pynwb.core import DynamicTable
from pynwb.epoch import TimeIntervals

from wl_preproc.eye.detect.geometry import eccentricity_polar_deg
from wl_preproc.nwb.columns import column, continuous

EYES = ("left", "right")
PUPIL_COLUMNS = ("PupilX", "PupilY", "PupilWidth", "PupilHeight", "PupilAngle")
CALIBRATION_FIELDS = (
    "calibration_source", "calibration_model",
    "gx_const", "gx_dx", "gx_dy", "gx_dx2", "gx_dy2", "gx_dxdy",
    "gy_const", "gy_dx", "gy_dy", "gy_dx2", "gy_dy2", "gy_dxdy",
    "validation_error_deg", "n_points", "residual_deg_rms", "residual_deg_max", "reason",
)


def behavior_module(nwb: NWBFile):
    if "behavior" not in nwb.processing:
        nwb.create_processing_module(name="behavior", description="The eye, as recorded and as the validity mask judged it.")
    return nwb.processing["behavior"]


def add_eye_series(nwb: NWBFile, times: np.ndarray, gaze: dict, pupil: dict) -> None:
    """Gaze and pupil for each eye, on one shared `timestamps` dataset.

    `times` is session seconds per kept sample (section 4.3: explicit, never
    a nominal rate, since the ohDPI rate is measured and nothing is
    resampled). `gaze[eye]` is `(n, 2)` degrees in the calibrated frame, the
    glitch-repaired gaze every stored detection was made from, or None for
    an eye with no calibration (section 10). `pupil[eye]` is `(n, 5)`,
    OpenIris's `PUPIL_COLUMNS`, uncalibrated. Each gazed eye also gets
    `gaze_polar_{eye}`, the same gaze's true eccentricity and polar angle
    (design spec `2026-10-30-true-angle-saccades-design.md` section 8)."""
    module = behavior_module(nwb)
    shared = None
    gaze_series, pupil_series = [], []
    for eye in EYES:
        if gaze.get(eye) is None:
            continue
        series = SpatialSeries(
            name=f"gaze_{eye}",
            description=("Glitch-repaired gaze (eye/detect/glitch.py), the gaze every stored detection was made "
                         "from; NaN where the recording has no finite value."),
            data=continuous(np.asarray(gaze[eye], dtype=np.float32)),
            timestamps=shared if shared is not None else continuous(np.asarray(times, dtype=np.float64)),
            reference_frame=("Degrees of visual angle per axis in the calibrated frame, x rightward and y upward: a "
                             "point (X, Y) from straight ahead at distance D is (atan(X/D), atan(Y/D)). Its true "
                             f"eccentricity and polar angle are gaze_polar_{eye}."),
            unit="degrees",
        )
        if shared is None:
            shared = series
        gaze_series.append(series)
        eccentricity, polar = eccentricity_polar_deg(np.asarray(gaze[eye], dtype=float))
        angles = np.column_stack([eccentricity, polar]).astype(np.float32)
        # A polar angle just below 360 rounds up to 360 in float32; the
        # series holds [0, 360), as `geometry.wrap_360_deg` does.
        angles[angles[:, 1] >= 360.0, 1] = 0.0
        gaze_series.append(SpatialSeries(
            name=f"gaze_polar_{eye}",
            description=("The same gaze's true eccentricity, degrees of visual angle from straight ahead, and its polar "
                         "angle, degrees counterclockwise from rightward in [0, 360); NaN where the gaze is, and the "
                         "polar angle NaN at zero eccentricity."),
            data=continuous(angles),
            timestamps=shared,
            reference_frame="Eccentricity from straight ahead, then polar angle counterclockwise from rightward.",
            unit="degrees",
        ))
    for eye in EYES:
        if pupil.get(eye) is None:
            continue
        series = TimeSeries(
            name=f"pupil_{eye}",
            description="OpenIris's " + ", ".join(PUPIL_COLUMNS) + " columns: pixels, and degrees for the angle; uncalibrated.",
            data=continuous(np.asarray(pupil[eye], dtype=np.float32)),
            timestamps=shared if shared is not None else continuous(np.asarray(times, dtype=np.float64)),
            unit="pixels",
        )
        if shared is None:
            shared = series
        pupil_series.append(series)
    if gaze_series:
        module.add(EyeTracking(spatial_series=gaze_series))
    if pupil_series:
        module.add(PupilTracking(time_series=pupil_series))


def add_eye_tables(nwb: NWBFile, calibration: list[dict], validity: dict, repairs: dict) -> None:
    """`eye_calibration` (one row per calibrated eye), and per eye the
    stretches the mask withheld (`eye_validity_{eye}`, with their label) and
    the stretches `glitch.py` repaired (`eye_glitch_repairs_{eye}`), each
    `(start_s, stop_s[, label])`."""
    module = behavior_module(nwb)
    if calibration:
        rows = sorted(calibration, key=lambda row: row["eye"])
        module.add(DynamicTable(
            name="eye_calibration",
            description="EyeCalibration: each eye's calibration map and its quality (NaN where not applicable).",
            columns=[column("eye", "left or right.", [r["eye"] for r in rows]),
                     *[column(field, field.replace("_", " ") + ".",
                              [(np.nan if r.get(field) is None else r[field]) if field not in ("calibration_source", "calibration_model", "reason")
                               else (r.get(field) or "") for r in rows])
                       for field in CALIBRATION_FIELDS]],
        ))
    for eye in EYES:
        if eye in validity:
            runs = sorted(validity[eye])
            module.add(TimeIntervals(
                name=f"eye_validity_{eye}",
                description=f"Stretches of the {eye} eye the validity mask withheld, with the mask's label.",
                columns=[column("start_time", "Start, session seconds.", [r[0] for r in runs]),
                         column("stop_time", "Stop, session seconds.", [r[1] for r in runs]),
                         column("label", "blink or invalid.", [r[2] for r in runs])],
            ))
        if eye in repairs:
            runs = sorted(repairs[eye])
            module.add(TimeIntervals(
                name=f"eye_glitch_repairs_{eye}",
                description=(f"Stretches of the {eye} eye's gaze that were a tracker glitch -- out and back faster "
                             "than an eye moves -- and were replaced by the straight line across them."),
                columns=[column("start_time", "Start, session seconds.", [r[0] for r in runs]),
                         column("stop_time", "Stop, session seconds.", [r[1] for r in runs])],
            ))
