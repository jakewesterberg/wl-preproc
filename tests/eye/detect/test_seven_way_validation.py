"""The seven-way agreement on the lab's reference recording (design spec
`2026-10-08-seven-way-agreement-design.md` sections 1.3 and 7): white noise
the validity mask does not catch lowers the score.

Gated on `WLPP_OHDPI_REFERENCE`, so skipped in CI; the recording is never
committed. Skipped too where a detector cannot run, U'n'Eye without its
install say (`registry.unavailable_detectors`). Imports nothing from
`wl_preproc.schema`: the gaze is repaired and each detector run as
`schema/detect.py::EyeDetection.make()` does, restated here. Each detector
gets `Detector.defaults`, which is what its registered paramset holds: the
shared `microsaccade_max_deg` equals every detector's own default today
(`schema/detect.py::_eye_detection_params`)."""

from __future__ import annotations

import os

import numpy as np
import pytest

from wl_preproc.eye.detect.consensus import PSO_AS_SACCADE, blended_agreement, krippendorff_alpha
from wl_preproc.eye.detect.labels import Label

MINUTES = 10
NOISE_SD_DEG = 0.05
_SCALE_P99_AT_DEG = 15.0


def _scaled_affine_map(scale: float):
    """Restated from `test_remodnav_validation.py`, not imported."""
    from wl_preproc.eye.calibration import CalibrationMap, CalibrationModel

    return CalibrationMap(model=CalibrationModel.AFFINE, x=(0.0, scale, 0.0), y=(0.0, 0.0, scale))


@pytest.fixture(scope="module")
def left_eye():
    """The first ten minutes of the left eye's glitch-repaired gaze, as
    `schema/detect.py::_repaired_gaze` builds it, its quality flags and the
    rate. The recording is uncalibrated, so its 99th percentile is put at 15
    degrees, as the other validation tests do."""
    sample = os.environ.get("WLPP_OHDPI_REFERENCE")
    if not sample:
        pytest.skip("WLPP_OHDPI_REFERENCE is not set -- see test_nystrom_holmqvist_validation.py")
    from wl_preproc.eye.detect.registry import unavailable_detectors

    # The score is every detector's, so one that cannot run here, U'n'Eye
    # without its install say, skips the test rather than fail it minutes in.
    if unavailable := unavailable_detectors():
        pytest.skip(f"a detector cannot run here: {unavailable}")
    from wl_preproc.eye.detect.glitch import repair_glitches
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS
    from wl_preproc.eye.gaze import gaze_trace, purkinje_vector
    from wl_preproc.eye.ohdpi import read_columns, read_ohdpi

    rec = read_ohdpi(sample)
    # The mask below is given no frame gaps: they are rows of the whole
    # recording, and this one has none.
    assert not rec.frame_gaps
    raw = {eye: purkinje_vector(sample, eye) for eye in ("Left", "Right")}
    pooled_x = np.concatenate([np.abs(raw["Left"][:, 0]), np.abs(raw["Right"][:, 0])])
    pooled_y = np.concatenate([np.abs(raw["Left"][:, 1]), np.abs(raw["Right"][:, 1])])
    scale = _SCALE_P99_AT_DEG / max(float(np.percentile(pooled_x, 99)), float(np.percentile(pooled_y, 99)))
    gaze, _repaired = repair_glitches(gaze_trace(sample, "Left", _scaled_affine_map(scale)), rec.fs_hz,
                                      DEFAULT_VALIDITY_PARAMS.max_speed_deg_s, DEFAULT_VALIDITY_PARAMS.max_glitch_ms)
    n = int(MINUTES * 60 * rec.fs_hz)
    quality = read_columns(sample, ["LeftDataQuality"])["LeftDataQuality"]
    return gaze[:n], quality[:n], rec.fs_hz


def _seven_way(gaze, quality, fs_hz) -> tuple[float, float]:
    """Krippendorff's alpha over every registered detector, glissades as
    saccade, BMD abstaining on the saccades it copies; and the share of
    samples the mask offered."""
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS, validity_labels
    from wl_preproc.eye.detect.velocity import velocity

    v = velocity(gaze, fs_hz)
    offered = np.asarray(validity_labels(gaze, v, quality, (), DEFAULT_VALIDITY_PARAMS).labels, dtype=object)
    labels = {}
    for name, detector in DETECTORS.items():
        trace = offered.copy()
        for run in detector.detect(gaze, v, offered, fs_hz, detector.defaults):
            trace[run.start:run.stop] = run.label
        labels[name] = np.where(trace == None, Label.FIXATION, trace)  # noqa: E711
    result = blended_agreement(labels, {name: detector.vocabulary for name, detector in DETECTORS.items()},
                               {name: detector.copies_saccades_from for name, detector in DETECTORS.items()},
                               PSO_AS_SACCADE, krippendorff_alpha)
    return result.value, float(np.mean(offered == None))  # noqa: E711


def test_noise_the_mask_keeps_lowers_the_seven_way_score(left_eye):
    """Spec section 1.3 measured 0.684 with no noise and 0.594 under 0.05
    degrees, the mask keeping 0.991 of samples at both. A drop of more than
    0.05 is asked for, with the mask's share unmoved."""
    gaze, quality, fs_hz = left_eye
    clean, clean_share = _seven_way(gaze, quality, fs_hz)
    noisy_gaze = gaze + np.random.default_rng(0).normal(0.0, NOISE_SD_DEG, gaze.shape)
    noisy, noisy_share = _seven_way(noisy_gaze, quality, fs_hz)
    assert noisy_share == pytest.approx(clean_share, abs=0.001)
    assert noisy < clean - 0.05, (clean, noisy)
