"""NSLR on the lab's reference recording (spec 5.2): exactness where there
are no gaps, runtime, and agreement with the other detectors. Gated on
`WLPP_OHDPI_REFERENCE` (never commit the recording). Imports nothing from
`wl_preproc.schema`."""

from __future__ import annotations

import os
import time

import numpy as np
import pytest

from tests.eye.detect._nslr_reference import reference as nslr_reference
from wl_preproc.eye.detect.consensus import cohen_kappa
from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.nslr import DEFAULT_NSLR_PARAMS, classify, detect_nslr

#: REMoDNaV's and Nystrom-Holmqvist's slice, for the agreement report.
COMPARISON_SAMPLES = 120_000
#: The gap-free stretch the exactness check needs (spec 5.2).
EXACT_STRETCH = 5_000
_SCALE_P99_AT_DEG = 15.0


def _scaled_affine_map(scale: float):
    """Restated from `test_remodnav_validation.py`, not imported."""
    from wl_preproc.eye.calibration import CalibrationMap, CalibrationModel

    return CalibrationMap(model=CalibrationModel.AFFINE, x=(0.0, scale, 0.0), y=(0.0, 0.0, scale))


@pytest.fixture(scope="module")
def recording():
    sample = os.environ.get("WLPP_OHDPI_REFERENCE")
    if not sample:
        pytest.skip("WLPP_OHDPI_REFERENCE is not set -- see test_nystrom_holmqvist_validation.py")
    from wl_preproc.eye.calibration import apply_map
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS, validity_labels
    from wl_preproc.eye.detect.velocity import velocity
    from wl_preproc.eye.gaze import purkinje_vector
    from wl_preproc.eye.ohdpi import read_columns, read_ohdpi

    rec = read_ohdpi(sample)
    raw = {"left": purkinje_vector(sample, "Left"), "right": purkinje_vector(sample, "Right")}
    quality = read_columns(sample, ["LeftDataQuality", "RightDataQuality"])
    pooled_x = np.concatenate([np.abs(raw["left"][:, 0]), np.abs(raw["right"][:, 0])])
    pooled_y = np.concatenate([np.abs(raw["left"][:, 1]), np.abs(raw["right"][:, 1])])
    scale = _SCALE_P99_AT_DEG / max(float(np.percentile(pooled_x, 99)), float(np.percentile(pooled_y, 99)))
    eyes = {}
    for eye, column in (("left", "LeftDataQuality"), ("right", "RightDataQuality")):
        gaze = apply_map(_scaled_affine_map(scale), raw[eye])
        v = velocity(gaze, rec.fs_hz)
        mask = validity_labels(gaze, v, quality[column], rec.frame_gaps, DEFAULT_VALIDITY_PARAMS).labels
        eyes[eye] = (gaze, v, mask)
    return rec, eyes


def _first_clean_stretch(mask, length):
    usable = np.array([entry is None for entry in mask], dtype=bool)
    run = 0
    for i, ok in enumerate(usable):
        run = run + 1 if ok else 0
        if run == length:
            return i - length + 1
    return None


def test_exactness_on_a_gap_free_stretch_of_real_data(recording):
    _, nslr_hmm = nslr_reference()
    rec, eyes = recording
    for eye, (gaze, _v, mask) in eyes.items():
        at = _first_clean_stretch(mask, EXACT_STRETCH)
        assert at is not None, f"{eye}: no {EXACT_STRETCH}-sample stretch without a withheld sample"
        xy = np.asarray(gaze[at:at + EXACT_STRETCH], dtype=float)
        ts = np.arange(len(xy)) / rec.fs_hz
        theirs = np.asarray(nslr_hmm.classify_gaze(ts, xy)[0], dtype=np.int64)
        ours = classify([xy], rec.fs_hz, DEFAULT_NSLR_PARAMS)[0] + 1
        assert np.array_equal(ours, theirs), eye


def test_runtime_and_agreement_are_measured(recording, capsys):
    """Recorded, not gated (spec 5.2)."""
    from wl_preproc.eye.detect.nystrom_holmqvist import DEFAULT_NH_PARAMS, detect_nystrom_holmqvist
    from wl_preproc.eye.detect.remodnav import DEFAULT_REMODNAV_PARAMS, detect_remodnav

    rec, eyes = recording
    limit = min(COMPARISON_SAMPLES, rec.n_frames)
    for eye, (gaze, v, mask) in eyes.items():
        started = time.monotonic()
        full = detect_nslr(gaze, v, mask, rec.fs_hz, DEFAULT_NSLR_PARAMS)
        seconds = time.monotonic() - started
        ours = detect_nslr(gaze[:limit], v[:limit], mask[:limit], rec.fs_hz, DEFAULT_NSLR_PARAMS)
        others = {
            "remodnav": detect_remodnav(gaze[:limit], v[:limit], mask[:limit], rec.fs_hz, DEFAULT_REMODNAV_PARAMS),
            "nystrom_holmqvist": detect_nystrom_holmqvist(gaze[:limit], v[:limit], mask[:limit], rec.fs_hz,
                                                          DEFAULT_NH_PARAMS),
        }

        def saccades(runs):
            out = np.zeros(limit, dtype=bool)
            for r in runs:
                if r.label is Label.SACCADE:
                    out[r.start:r.stop] = True
            return out

        with capsys.disabled():
            print(f"\n  {eye}: full recording ({rec.n_frames} samples) in {seconds:.1f} s, {len(full)} runs")
            print(f"  {eye}: nslr {sum(r.label is Label.SACCADE for r in ours)} saccades over {limit} samples")
            for name, runs in others.items():
                kappa = cohen_kappa(saccades(ours), saccades(runs), np.ones(limit, dtype=bool))
                print(f"  {eye}: vs {name}: {sum(r.label is Label.SACCADE for r in runs)} saccades, "
                      f"saccade kappa {kappa:.3f}")
