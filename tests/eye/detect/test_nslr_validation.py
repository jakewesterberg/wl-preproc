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


# -- Spec 5.3: the paper's human coders, the paper's way -----------------------------

from tests.eye.detect._andersson import ANDERSSON_FILES, load_andersson  # noqa: E402

#: Table 1: (Human, NSLR-HMM) per class, and the Andersson label codes.
PAPER_TABLE_1 = {"saccade": (0.90, 0.82), "fixation": (0.81, 0.51), "pursuit": (0.79, 0.42), "pso": (0.73, 0.53)}
CODES = {"fixation": 1, "saccade": 2, "pso": 3, "pursuit": 4}
OMITTED = (5, 6)  # blink, undefined -- omitted if EITHER coder used them (paper, Benchmarking methodology)
CODER_TOLERANCE = 0.01
OURS_TOLERANCE = 0.05


def test_the_null_random_labels_score_near_zero():
    rng = np.random.default_rng(15)
    a, b = rng.random(50_000) < 0.3, rng.random(50_000) < 0.3
    assert abs(cohen_kappa(a, b, np.ones(a.size, dtype=bool))) < 0.05


@pytest.fixture(scope="module")
def andersson():
    root = os.environ.get("WLPP_ANDERSSON_DATA")
    if not root:
        pytest.skip("WLPP_ANDERSSON_DATA is not set")
    from pathlib import Path

    from wl_preproc.eye.detect.remodnav import _dilate
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS

    columns = {"MN": [], "RA": [], "US": []}
    for stim, names in ANDERSSON_FILES.items():
        for name in names:
            _xy, mn, _p, _f = load_andersson(Path(root), stim, name.format("MN"))
            xy_px, ra, px2deg, fs = load_andersson(Path(root), stim, name.format("RA"))
            gaze = xy_px * px2deg
            unusable = _dilate(np.isnan(gaze[:, 0]), DEFAULT_VALIDITY_PARAMS.dilate_samples)
            available = np.array([Label.INVALID if bad else None for bad in unusable], dtype=object)
            codes = np.zeros(len(gaze), dtype=int)
            for run in detect_nslr(np.nan_to_num(gaze), np.zeros_like(gaze), available, fs, DEFAULT_NSLR_PARAMS):
                codes[run.start:run.stop] = CODES[run.label.value]
            shorter = min(len(mn), len(ra))
            columns["MN"].append(np.asarray(mn)[:shorter])
            columns["RA"].append(np.asarray(ra)[:shorter])
            columns["US"].append(codes[:shorter])
    mn, ra, us = (np.concatenate(columns[k]) for k in ("MN", "RA", "US"))
    keep = ~(np.isin(mn, OMITTED) | np.isin(ra, OMITTED))
    mn, ra, us = mn[keep], ra[keep], us[keep]
    everywhere = np.ones(mn.size, dtype=bool)
    out = {}
    for name, code in CODES.items():
        coders = cohen_kappa(mn == code, ra == code, everywhere)
        ours = (cohen_kappa(us == code, mn == code, everywhere) + cohen_kappa(us == code, ra == code, everywhere)) / 2
        out[name] = (coders, ours)
    return out


def test_the_harness_reproduces_the_coders_own_agreement(andersson):
    for name, (human, _nslr) in PAPER_TABLE_1.items():
        assert andersson[name][0] == pytest.approx(human, abs=CODER_TOLERANCE), name


def test_ours_reproduces_the_papers_nslr_column(andersson, capsys):
    """In-sample, and says so (spec 5.3): the HMM's emissions were fitted on
    these data."""
    with capsys.disabled():
        for name, (coders, ours) in andersson.items():
            print(f"\n  {name}: coders {coders:.3f} (paper {PAPER_TABLE_1[name][0]}), "
                  f"nslr {ours:.3f} (paper {PAPER_TABLE_1[name][1]})")
    for name, (_human, nslr) in PAPER_TABLE_1.items():
        assert andersson[name][1] == pytest.approx(nslr, abs=OURS_TOLERANCE), name
