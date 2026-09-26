"""REMoDNaV against its oracle end to end (spec 5.2) and against the
paper's human coders (spec 5.3) -- nulls first.

**The rule governing this file** (Otero-Millan's round, via
`test_nystrom_holmqvist_validation.py`): "an oracle-free statistic is
worthless until a null has been run against it." Every null below runs
without a recording, and in CI.

**Gated.**
- Section 5.2 is gated on `WLPP_OHDPI_REFERENCE` (the OpenIris reference
  recording; never commit it).
- Section 5.3 is gated on `WLPP_ANDERSSON_DATA`: a local clone of
  github.com/richardandersson/EyeMovementDetectorEvaluation, GPL-3.0. Never
  commit or vendor any of it.

**Human data.** Nothing here speaks to macaques (spec section 8).

Imports nothing from `wl_preproc.schema` (this file lives in `tests/eye/`).
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path

import numpy as np
import pytest

from tests.eye.detect._remodnav_traces import (
    gaze_trace,
    oracle_labels,
    oracle_run,
    our_labels,
)
from wl_preproc.eye.detect.consensus import cohen_kappa
from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.remodnav import DEFAULT_REMODNAV_PARAMS, detect_remodnav

#: The leading slice both sides classify -- Nystrom-Holmqvist's oracle
#: check's own, for its stated reason (the oracle's pure-Python loops).
REMODNAV_COMPARISON_SAMPLES = 120_000

#: Restated from `test_nystrom_holmqvist_validation.py`, not imported, per
#: that file's own convention.
_SCALE_P99_AT_DEG = 15.0

#: A duration-matched random-span control must score below this; a real
#: comparison must score above it.
NULL_KAPPA_CEILING = 0.1

KINDS = (Label.SACCADE, Label.PSO, Label.FIXATION, Label.PURSUIT)


def _random_spans_like(labels: np.ndarray, value: str, rng) -> np.ndarray:
    """`labels == value`'s runs, each placed again at a uniformly random
    start: same count, same durations, no relation to the eye."""
    padded = np.concatenate(([False], labels == value, [False]))
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    out = np.zeros(labels.size, dtype=bool)
    for start, stop in zip(edges[::2], edges[1::2], strict=True):
        length = stop - start
        at = int(rng.integers(0, labels.size - length + 1))
        out[at:at + length] = True
    return out


def test_the_null_random_spans_score_near_zero_kappa():
    """Section 5.2's null, on a synthetic trace the oracle classifies:
    duration-matched random saccade spans against the oracle's own
    saccades."""
    remodnav = pytest.importorskip("remodnav")
    xy = gaze_trace(500.0, 12)
    _c, _p, events = oracle_run(remodnav, xy, 500.0)
    theirs = oracle_labels(events, 500.0, len(xy))
    rng = np.random.default_rng(12)

    kappa = cohen_kappa(_random_spans_like(theirs, "saccade", rng), theirs == "saccade",
                        np.ones(len(xy), dtype=bool))

    assert abs(kappa) < NULL_KAPPA_CEILING


# -- Section 5.2: the reference recording --------------------------------------


def _scaled_affine_map(scale: float):
    """Restated from `test_nystrom_holmqvist_validation.py`, not imported."""
    from wl_preproc.eye.calibration import CalibrationMap, CalibrationModel

    return CalibrationMap(model=CalibrationModel.AFFINE, x=(0.0, scale, 0.0), y=(0.0, 0.0, scale))


@pytest.fixture(scope="module")
def reference():
    """Both eyes, the leading slice, classified by both sides. Also each
    eye's full-recording runtime (spec 8 item 5)."""
    sample = os.environ.get("WLPP_OHDPI_REFERENCE")
    if not sample:
        pytest.skip("WLPP_OHDPI_REFERENCE is not set -- see test_nystrom_holmqvist_validation.py")
    remodnav = pytest.importorskip("remodnav")
    from wl_preproc.eye.calibration import apply_map
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS, validity_labels
    from wl_preproc.eye.detect.velocity import velocity
    from wl_preproc.eye.gaze import purkinje_vector
    from wl_preproc.eye.ohdpi import read_columns, read_ohdpi

    recording = read_ohdpi(sample)
    raw = {"left": purkinje_vector(sample, "Left"), "right": purkinje_vector(sample, "Right")}
    quality = read_columns(sample, ["LeftDataQuality", "RightDataQuality"])
    pooled_x = np.concatenate([np.abs(raw["left"][:, 0]), np.abs(raw["right"][:, 0])])
    pooled_y = np.concatenate([np.abs(raw["left"][:, 1]), np.abs(raw["right"][:, 1])])
    scale = _SCALE_P99_AT_DEG / max(float(np.percentile(pooled_x, 99)), float(np.percentile(pooled_y, 99)))
    fs = recording.fs_hz
    limit = min(REMODNAV_COMPARISON_SAMPLES, recording.n_frames)

    eyes = {}
    for eye, column in (("left", "LeftDataQuality"), ("right", "RightDataQuality")):
        gaze = apply_map(_scaled_affine_map(scale), raw[eye])
        v = velocity(gaze, fs)
        mask = validity_labels(gaze, v, quality[column], recording.frame_gaps, DEFAULT_VALIDITY_PARAMS).labels
        started = time.monotonic()
        full_runs = detect_remodnav(gaze, v, mask, fs, DEFAULT_REMODNAV_PARAMS)
        full_s = time.monotonic() - started
        ours = detect_remodnav(gaze[:limit], v[:limit], mask[:limit], fs, DEFAULT_REMODNAV_PARAMS)
        _c, _p, events = oracle_run(remodnav, gaze[:limit], fs)
        eyes[eye] = {
            "ours": our_labels(ours, limit),
            "theirs": oracle_labels(events, fs, limit),
            "our_runs": ours,
            "events": events,
            "full_s": full_s,
            "full_runs": len(full_runs),
        }
    return {"fs": fs, "limit": limit, "n_frames": recording.n_frames, "eyes": eyes}


def test_saccade_counts_agree_within_a_factor_of_two(reference, capsys):
    """Spec 5.2's one assertion, the same looseness as Nystrom-Holmqvist's
    oracle check: the shared estimator changes smoothing, not method."""
    for eye, data in reference["eyes"].items():
        ours = sum(1 for r in data["our_runs"] if r.label is Label.SACCADE)
        theirs = sum(1 for e in data["events"] if e["label"] in ("SACC", "ISAC"))
        with capsys.disabled():
            print(f"\n  {eye}: remodnav (ours) {ours} saccades, oracle {theirs}, over {reference['limit']} samples")
        assert max(ours, theirs) / max(min(ours, theirs), 1) <= 2.0


def test_the_per_kind_kappas_are_measured(reference, capsys):
    """Recorded, not gated (spec 5.2) -- except that saccades must beat the
    null's ceiling, or the comparison measures nothing."""
    everywhere = np.ones(reference["limit"], dtype=bool)
    for eye, data in reference["eyes"].items():
        kappas = {
            kind.value: cohen_kappa(data["ours"] == kind.value, data["theirs"] == kind.value, everywhere)
            for kind in KINDS
        }
        with capsys.disabled():
            print(f"\n  {eye}: " + ", ".join(f"{k} kappa={v:.3f}" for k, v in kappas.items()))
            print(f"  {eye}: full recording ({reference['n_frames']} samples) classified in "
                  f"{data['full_s']:.1f} s, {data['full_runs']} runs")
        assert kappas["saccade"] > NULL_KAPPA_CEILING


# -- Section 5.3: the paper's human coders -------------------------------------

#: `mk_figuresnstats.py`'s own `labeled_files`, verbatim.
ANDERSSON_FILES = {
    "dots": [
        "TH20_trial1_labelled_{}.mat", "TH38_trial1_labelled_{}.mat", "TL22_trial17_labelled_{}.mat",
        "TL24_trial17_labelled_{}.mat", "UH21_trial17_labelled_{}.mat", "UH21_trial1_labelled_{}.mat",
        "UH25_trial1_labelled_{}.mat", "UH33_trial17_labelled_{}.mat", "UL27_trial17_labelled_{}.mat",
        "UL31_trial1_labelled_{}.mat", "UL39_trial1_labelled_{}.mat",
    ],
    "img": [
        "TH34_img_Europe_labelled_{}.mat", "TH34_img_vy_labelled_{}.mat", "TL20_img_konijntjes_labelled_{}.mat",
        "TL28_img_konijntjes_labelled_{}.mat", "UH21_img_Rome_labelled_{}.mat", "UH27_img_vy_labelled_{}.mat",
        "UH29_img_Europe_labelled_{}.mat", "UH33_img_vy_labelled_{}.mat", "UH47_img_Europe_labelled_{}.mat",
        "UL23_img_Europe_labelled_{}.mat", "UL31_img_konijntjes_labelled_{}.mat",
        "UL39_img_konijntjes_labelled_{}.mat", "UL43_img_Rome_labelled_{}.mat",
        "UL47_img_konijntjes_labelled_{}.mat",
    ],
    "video": [
        "TH34_video_BergoDalbana_labelled_{}.mat", "TH38_video_dolphin_fov_labelled_{}.mat",
        "TL30_video_triple_jump_labelled_{}.mat", "UH21_video_BergoDalbana_labelled_{}.mat",
        "UH29_video_dolphin_fov_labelled_{}.mat", "UH47_video_BergoDalbana_labelled_{}.mat",
        "UL23_video_triple_jump_labelled_{}.mat", "UL27_video_triple_jump_labelled_{}.mat",
        "UL31_video_triple_jump_labelled_{}.mat",
    ],
}

#: The paper's Table 3: `(MN-RA, AL-RA, AL-MN)` per `(stimulus, event)`.
PAPER_TABLE_3 = {
    ("img", "Fix"): (0.84, 0.55, 0.52), ("dots", "Fix"): (0.65, 0.37, 0.45), ("video", "Fix"): (0.65, 0.44, 0.39),
    ("img", "Sac"): (0.91, 0.78, 0.78), ("dots", "Sac"): (0.81, 0.72, 0.78), ("video", "Sac"): (0.87, 0.76, 0.79),
    ("img", "PSO"): (0.76, 0.59, 0.58), ("dots", "PSO"): (0.62, 0.38, 0.41), ("video", "PSO"): (0.65, 0.45, 0.51),
}
EVENT_CODES = {"Fix": 1, "Sac": 2, "PSO": 3}
OUR_CODES = {Label.FIXATION: 1, Label.SACCADE: 2, Label.PSO: 3, Label.PURSUIT: 4}
ORACLE_CODES = {"FIXA": 1, "SACC": 2, "ISAC": 2, "HPSO": 3, "IHPS": 3, "LPSO": 3, "ILPS": 3, "PURS": 4}

#: The coders' own agreement is pure data and pure arithmetic: rounding to
#: two places is the only slack. The oracle's may differ by release.
CODER_TOLERANCE = 0.006
ORACLE_TOLERANCE = 0.05
#: Spec 5.3's prediction for ours against the oracle, on saccades.
OURS_BELOW_ORACLE_AT_MOST = 0.05


def test_the_null_random_labels_score_near_zero_kappa():
    rng = np.random.default_rng(14)
    coder = rng.random(50_000) < 0.3
    guess = rng.random(50_000) < 0.3

    # Spec 5.3: random labels at the coders' own proportions score "kappa near zero".
    assert abs(cohen_kappa(coder, guess, np.ones(coder.size, dtype=bool))) < 0.05


def _load_andersson(root: Path, stim: str, name: str):
    from scipy.io import loadmat

    et = loadmat(root / "annotated_data" / "data used in the article" / stim / name)["ETdata"]
    view_dist = float(et["viewDist"][0][0][0][0])
    screen_width = float(et["screenDim"][0][0][0][0])
    screen_res = float(et["screenRes"][0][0][0][0])
    px2deg = math.degrees(math.atan2(0.5 * screen_width, view_dist)) / (0.5 * screen_res)
    fs = float(et["sampFreq"][0][0][0][0])
    pos = et["pos"][0][0]
    x, y = pos[:, 3].astype(float), pos[:, 4].astype(float)
    missing = (x == 0) & (y == 0)
    x[missing] = np.nan
    y[missing] = np.nan
    return np.column_stack([x, y]), pos[:, 5], px2deg, fs


def _ours(xy_px, px2deg, fs):
    """Ours on the coder's positions, in degrees. Missing samples, dilated as
    the validity mask dilates (`DEFAULT_VALIDITY_PARAMS.dilate_samples`, 10 ms
    at 500 Hz -- the paper's own `dilate_nan`), are withheld."""
    from wl_preproc.eye.detect.remodnav import _dilate
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS
    from wl_preproc.eye.detect.velocity import velocity

    gaze = np.nan_to_num(xy_px * px2deg)
    unusable = _dilate(np.isnan(xy_px[:, 0]), DEFAULT_VALIDITY_PARAMS.dilate_samples)
    available = np.array([Label.INVALID if bad else None for bad in unusable], dtype=object)
    codes = np.zeros(len(gaze), dtype=int)
    for run in detect_remodnav(gaze, velocity(gaze, fs), available, fs, DEFAULT_REMODNAV_PARAMS):
        codes[run.start:run.stop] = OUR_CODES[run.label]
    return codes


def _oracle(remodnav, xy_px, px2deg, fs, n):
    classifier = remodnav.EyegazeClassifier(px2deg=px2deg, sampling_rate=fs)
    data = np.rec.fromarrays([xy_px[:, 0].copy(), xy_px[:, 1].copy()], names=["x", "y"])
    codes = np.zeros(n, dtype=int)
    for event in classifier(classifier.preproc(data)):
        codes[int(event["start_time"] * fs):int(event["end_time"] * fs)] = ORACLE_CODES[event["label"]]
    return codes


@pytest.fixture(scope="module")
def andersson():
    """Every `(stimulus, event)`: kappas of MN-RA, AL-RA, AL-MN (AL the
    oracle) and US-RA, US-MN (US this implementation).

    **MN-RA, US-RA and US-MN are computed over every file, unconditionally.**
    Neither the coders nor this implementation ever fails to produce labels,
    so nothing should make their agreement depend on whether a third piece of
    software, run only for AL, happens to run at all.

    **AL-RA and AL-MN are computed only over the files `remodnav` 1.1.2 can
    actually classify.** Installed against `scipy>=1.17` (this repository's
    pin), its `preproc` runs `scipy.signal.savgol_filter` (`mode="interp"`,
    its default) directly on the coder's raw positions. When a real
    recording's own first or last few samples are themselves missing --
    dichotomised to NaN exactly as the paper's `load_anderson` does -- that
    NaN falls inside the filter's edge-fit window, and `scipy.linalg.lstsq`
    (`check_finite=True` by default) raises `ValueError`. This is the
    installed oracle failing outright on real data, not a harness bug: the
    same file, same positions, crash every time, regardless of who calls
    `preproc`. 9 of the 34 files fail this way (recorded below and in the
    report) -- a finding for spec 5.3, not something to route around by
    faking data at the edges the oracle itself never saw."""
    root = os.environ.get("WLPP_ANDERSSON_DATA")
    if not root:
        pytest.skip("WLPP_ANDERSSON_DATA is not set -- a local clone of "
                    "github.com/richardandersson/EyeMovementDetectorEvaluation (GPL-3.0; never commit it)")
    remodnav = pytest.importorskip("remodnav")
    root = Path(root)
    kappas = {}
    oracle_failures: list[tuple[str, str, str]] = []
    for stim, names in ANDERSSON_FILES.items():
        columns = {"MN": [], "RA": [], "US": []}
        oracle_columns = {"MN": [], "RA": [], "AL": []}
        for name in names:
            _xy_mn, mn, _p, _f = _load_andersson(root, stim, name.format("MN"))
            xy, ra, px2deg, fs = _load_andersson(root, stim, name.format("RA"))
            us = _ours(xy, px2deg, fs)
            shorter = min(len(mn), len(ra))
            mn_t, ra_t, us_t = (np.asarray(v)[:shorter] for v in (mn, ra, us))
            for key, value in (("MN", mn_t), ("RA", ra_t), ("US", us_t)):
                columns[key].append(value)
            try:
                al = _oracle(remodnav, xy, px2deg, fs, len(ra))
            except ValueError as exc:
                oracle_failures.append((stim, name.format("RA"), str(exc)))
                continue
            for key, value in (("MN", mn_t), ("RA", ra_t), ("AL", np.asarray(al)[:shorter])):
                oracle_columns[key].append(value)
        arrays = {key: np.concatenate(parts) for key, parts in columns.items()}
        everywhere = np.ones(arrays["MN"].size, dtype=bool)
        oracle_arrays = ({key: np.concatenate(parts) for key, parts in oracle_columns.items()}
                        if oracle_columns["AL"] else None)
        oracle_everywhere = np.ones(oracle_arrays["MN"].size, dtype=bool) if oracle_arrays else None
        for event, code in EVENT_CODES.items():
            binary = {key: value == code for key, value in arrays.items()}
            row = {
                "MN-RA": cohen_kappa(binary["MN"], binary["RA"], everywhere),
                "US-RA": cohen_kappa(binary["US"], binary["RA"], everywhere),
                "US-MN": cohen_kappa(binary["US"], binary["MN"], everywhere),
            }
            if oracle_arrays is not None:
                oracle_binary = {key: value == code for key, value in oracle_arrays.items()}
                row["AL-RA"] = cohen_kappa(oracle_binary["AL"], oracle_binary["RA"], oracle_everywhere)
                row["AL-MN"] = cohen_kappa(oracle_binary["AL"], oracle_binary["MN"], oracle_everywhere)
            else:
                row["AL-RA"] = row["AL-MN"] = float("nan")
            kappas[(stim, event)] = row
    if oracle_failures:
        total = sum(len(v) for v in ANDERSSON_FILES.values())
        print(f"\n  remodnav 1.1.2's preproc raised on {len(oracle_failures)}/{total} RA files "
              "(scipy>=1.17 vs. a recording's own missing edge samples; AL-RA/AL-MN exclude these):")
        for stim, fname, msg in oracle_failures:
            print(f"    {stim}/{fname}: {msg}")
    return kappas


def test_the_harness_reproduces_the_coders_own_agreement(andersson):
    for key, (mn_ra, _al_ra, _al_mn) in PAPER_TABLE_3.items():
        assert andersson[key]["MN-RA"] == pytest.approx(mn_ra, abs=CODER_TOLERANCE), key


def test_the_harness_reproduces_the_oracles_agreement(andersson):
    """If this fails while the coders' own agreement reproduces, the
    installed oracle's behaviour differs from the paper's version. Stop and
    report; do not widen."""
    for key, (_mn_ra, al_ra, al_mn) in PAPER_TABLE_3.items():
        assert andersson[key]["AL-RA"] == pytest.approx(al_ra, abs=ORACLE_TOLERANCE), key
        assert andersson[key]["AL-MN"] == pytest.approx(al_mn, abs=ORACLE_TOLERANCE), key


def test_our_saccade_agreement_is_within_its_prediction_of_the_oracles(andersson, capsys):
    with capsys.disabled():
        for key, k in sorted(andersson.items()):
            print(f"\n  {key}: " + ", ".join(f"{pair}={value:.3f}" for pair, value in k.items()))
    for stim in ANDERSSON_FILES:
        k = andersson[(stim, "Sac")]
        assert k["US-RA"] >= k["AL-RA"] - OURS_BELOW_ORACLE_AT_MOST, stim
        assert k["US-MN"] >= k["AL-MN"] - OURS_BELOW_ORACLE_AT_MOST, stim
