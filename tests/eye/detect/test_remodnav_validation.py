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
from dataclasses import dataclass
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

#: The only oracle failure `_oracle`'s window-wide trim is known not to
#: fix, and exactly why: `remodnav` 1.1.2's `preproc` (`clf.py` 846-863)
#: runs its own internal `dilate_nan` step -- widening any interior missing
#: run longer than `min_blink_duration` (10 samples at 500 Hz) by a further
#: `dilate_nan` (5 samples) on each side -- *inside* `preproc`, using a mask
#: `_oracle`'s own external, un-dilated check cannot see, before
#: `savgol_filter` ever runs. `video/UL31_video_triple_jump_labelled_RA.mat`
#: has a 91-sample interior gap ending 10 samples before the recording's own
#: end; `_oracle`'s check finds the trailing 9 samples clean, but `preproc`'s
#: own dilation shrinks that to 4, below `savgol_filter`'s 9-sample edge
#: window, so it still raises.
#:
#: `(stim, RA filename) -> expected message fragment`. This is the harness's
#: OWN bound on its exclusion, checked by
#: `test_the_oracle_fails_only_on_the_diagnosed_file`: a failure not in this
#: mapping, or one of these that stops failing, must be loud, not silently
#: folded into (or out of) the shared AL/US file set -- whether it is a
#: `preproc` crash or `_oracle`'s own `_NoCleanEdgeWindow`.
EXPECTED_ORACLE_FAILURES: dict[tuple[str, str], str] = {
    ("video", "UL31_video_triple_jump_labelled_RA.mat"): "array must not contain infs or NaNs",
}


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


class _NoCleanEdgeWindow(Exception):
    """`_oracle` found no `w`-sample window anywhere in the recording free
    of missing samples -- there is no `[i, j+1)` span to trim to. Raised
    rather than silently returned as an all-`0` (unlabelled) array, so a
    recording this degenerate is recorded exactly like a `preproc` crash
    is, not folded silently into the shared file set. No file in this
    dataset raises it today; `EXPECTED_ORACLE_FAILURES` has no entry for
    it, so one doing so in the future fails loudly."""


def _oracle(remodnav, xy_px, px2deg, fs, n):
    """The oracle, on the coder's own positions, trimmed to the span whose
    own leading and trailing `savgol_filter` edge windows are entirely
    present.

    `remodnav` 1.1.2's `preproc` (`clf.py` 863) runs
    `scipy.signal.savgol_filter` (`mode="interp"`, scipy's own default)
    directly on the coder's raw positions. That mode's edge fit calls
    `scipy.linalg.lstsq`, whose default `check_finite=True` -- present in
    the scipy release this repository pins, `scipy>=1.17` -- raises the
    moment a NaN falls inside the `w`-sample window its polynomial fit
    reads at either edge (`w = int(0.019 * fs)`, `clf.py` 841's own
    truncation of the 0.019 s default `savgol_length`; 9 at 500 Hz). The
    paper's own, far older scipy let a NaN through there instead of
    raising. A real recording's own first or last few samples, or an
    interior run close enough to either true edge to still fall inside
    that `w`-sample window, are sometimes themselves missing (dichotomised
    to NaN, exactly as the paper's `load_anderson` does) -- alone enough to
    crash under the newer scipy.

    The fix below changes only samples whose own edge-fit window was
    unusable anyway: `i` is the first index whose window `x[i:i+w]`,
    `y[i:i+w]` is entirely present, and `j` the last index whose window
    `x[j-w+1:j+1]`, `y[j-w+1:j+1]` is entirely present; only `[i, j+1)` is
    handed to `preproc`/`__call__`, and events are painted back at `i`'s own
    offset. Samples outside `[i, j+1)` stay `0` (unlabelled) in the
    returned codes -- the same place the untrimmed oracle's own
    missing-data handling would have left them, since `preproc` never
    assigns a label to a sample it never received.

    **Measured out of suite** (a scratch venv with `remodnav`'s own
    `scipy==1.13.1`, which never raises here regardless of trimming): this
    window-wide trim, like the single-sample trim it replaces, changes
    every `AL-RA`/`AL-MN` kappa by less than 0.001 relative to the
    untrimmed computation on the same file. The trim's cost is a handful of
    unlabelled samples per affected file -- not a change to the algorithm's
    verdict on any sample it can actually see.

    Still not a guarantee against every possible failure: if no `w`-wide
    window is fully present anywhere in the recording, there is no `[i,
    j+1)` to compute, and this raises `_NoCleanEdgeWindow` rather than
    invent one or return all-unlabelled codes silently. Any other
    exception from `preproc`/`__call__` -- most notably the `ValueError`
    `preproc`'s own internal, further dilation can still cause even after
    this trim (see the `andersson` fixture's docstring) -- is left to
    propagate. Either way, the caller must report it (bounded by
    `EXPECTED_ORACLE_FAILURES`), never fabricate samples to make it
    disappear.
    """
    bad = np.isnan(xy_px[:, 0]) | np.isnan(xy_px[:, 1])
    m = bad.size
    w = int(0.019 * fs)  # clf.py 841: savgol_length (0.019 s default) * sr, truncated
    if w <= 0 or m < w:
        raise _NoCleanEdgeWindow(f"recording has {m} samples, shorter than the {w}-sample edge window")
    counts = np.concatenate(([0], np.cumsum(bad.astype(np.int64))))
    window_bad = counts[w:] - counts[:-w]  # window_bad[k] = bad[k:k + w].sum()
    clean_starts = np.flatnonzero(window_bad == 0)
    if clean_starts.size == 0:
        raise _NoCleanEdgeWindow(
            "no window of savgol_filter's own edge-fit width is free of missing samples anywhere in this recording"
        )
    lo, hi = int(clean_starts[0]), int(clean_starts[-1]) + w
    classifier = remodnav.EyegazeClassifier(px2deg=px2deg, sampling_rate=fs)
    data = np.rec.fromarrays([xy_px[lo:hi, 0].copy(), xy_px[lo:hi, 1].copy()], names=["x", "y"])
    codes = np.zeros(n, dtype=int)
    for event in classifier(classifier.preproc(data)):
        start = lo + int(event["start_time"] * fs)
        stop = lo + int(event["end_time"] * fs)
        codes[start:stop] = ORACLE_CODES[event["label"]]
    return codes


@dataclass(frozen=True, slots=True)
class AnderssonResult:
    """`andersson`'s return: every `(stimulus, event)` kappa row, plus
    every oracle failure actually observed (`(stim, RA filename, message)`,
    whichever of `ValueError` or `_NoCleanEdgeWindow` `_oracle` raised).
    Kept separate from `kappas` rather than folded in as an extra key,
    since `kappas`' keys are `(stim, event)` tuples that
    `test_our_saccade_agreement_is_within_its_prediction_of_the_oracles`
    sorts -- a stray string key there would break that sort, not merely
    go unread.

    `test_the_oracle_fails_only_on_the_diagnosed_file` checks
    `oracle_failures` against `EXPECTED_ORACLE_FAILURES` exactly, so a new
    or missing failure is loud, never silently absorbed into (or out of)
    the shared AL/US file set the other three tests read `kappas` from."""

    kappas: dict[tuple[str, str], dict[str, float]]
    oracle_failures: tuple[tuple[str, str, str], ...]


@pytest.fixture(scope="module")
def andersson():
    """Every `(stimulus, event)`: kappas of MN-RA, AL-RA, AL-MN (AL the
    oracle) and US-RA, US-MN (US this implementation).

    **AL-RA, AL-MN, US-RA and US-MN are computed over one shared file set**
    -- exactly the files `_oracle` can produce labels for -- so ours and the
    oracle are compared on identical data, never on whichever subset
    happened to survive each independently. `_oracle`'s window-wide trim
    (see its docstring) now succeeds on 33 of the 34 files -- up from 32
    with round 1's single-sample trim. The remaining file,
    `video/UL31_video_triple_jump_labelled_RA.mat`, still raises
    `ValueError`; `EXPECTED_ORACLE_FAILURES` (bounding constant) and its own
    comment give the precise mechanism (`preproc`'s internal `dilate_nan`
    step widening this file's interior gap past what `_oracle`'s own
    external, un-dilated check can see). Rather than replicate that internal
    dilation logic to predict it -- transcribing more of `clf.py`'s own
    behaviour than the trim's edge-window contract needs -- or fabricate
    samples this recording never had, this one file is dropped from the
    shared set. **Every failure `_oracle` raises is recorded in
    `AnderssonResult.oracle_failures`, checked exactly against
    `EXPECTED_ORACLE_FAILURES` by `test_the_oracle_fails_only_on_the_diagnosed_file`,
    and printed there under `capsys.disabled()`** -- nothing here decides
    silently that a failure is the expected one; the dedicated test does,
    every run, gated or not, `-s` or not.

    **MN-RA is computed separately, over all 34 files.** It never touches
    `remodnav` -- nothing about it can fail the way `_oracle` can -- and the
    paper's own Table 3 MN-RA values are themselves computed over the full
    file list. Confirmed empirically, for the one file the window-wide trim
    still cannot save: dropping `UL31_video_triple_jump` from MN-RA's own
    computation moves `(video, Fix)` from 0.6527 to 0.6374 -- past its own
    ±0.006 tolerance (diff -0.0126), on a pair that has no oracle in it at
    all. (Round 1 found the same effect, larger, from `UL31_trial1` in
    `dots`; that file no longer needs excluding at all, since the
    window-wide trim now succeeds on it.) Coupling MN-RA to AL's file set
    would make it fail spuriously *because* the coder-only comparison was
    made to depend on a third piece of software that has nothing to do
    with it. This keeps MN-RA on its own full corpus and unifies only the
    pairs finding #2 was actually about: ours against the oracle.
    """
    root = os.environ.get("WLPP_ANDERSSON_DATA")
    if not root:
        pytest.skip("WLPP_ANDERSSON_DATA is not set -- a local clone of "
                    "github.com/richardandersson/EyeMovementDetectorEvaluation (GPL-3.0; never commit it)")
    remodnav = pytest.importorskip("remodnav")
    root = Path(root)
    kappas = {}
    oracle_failures: list[tuple[str, str, str]] = []
    for stim, names in ANDERSSON_FILES.items():
        coder_columns = {"MN": [], "RA": []}
        oracle_columns = {"MN": [], "RA": [], "AL": [], "US": []}
        for name in names:
            _xy_mn, mn, _p, _f = _load_andersson(root, stim, name.format("MN"))
            xy, ra, px2deg, fs = _load_andersson(root, stim, name.format("RA"))
            shorter = min(len(mn), len(ra))
            mn_t, ra_t = np.asarray(mn)[:shorter], np.asarray(ra)[:shorter]
            coder_columns["MN"].append(mn_t)
            coder_columns["RA"].append(ra_t)
            try:
                al = _oracle(remodnav, xy, px2deg, fs, len(ra))
            except (ValueError, _NoCleanEdgeWindow) as exc:
                oracle_failures.append((stim, name.format("RA"), str(exc)))
                continue
            us = _ours(xy, px2deg, fs)
            for key, value in (("MN", mn_t), ("RA", ra_t), ("AL", al), ("US", us)):
                oracle_columns[key].append(np.asarray(value)[:shorter])
        coder_arrays = {key: np.concatenate(parts) for key, parts in coder_columns.items()}
        oracle_arrays = {key: np.concatenate(parts) for key, parts in oracle_columns.items()}
        coder_everywhere = np.ones(coder_arrays["MN"].size, dtype=bool)
        oracle_everywhere = np.ones(oracle_arrays["MN"].size, dtype=bool)
        for event, code in EVENT_CODES.items():
            coder_binary = {key: value == code for key, value in coder_arrays.items()}
            oracle_binary = {key: value == code for key, value in oracle_arrays.items()}
            kappas[(stim, event)] = {
                "MN-RA": cohen_kappa(coder_binary["MN"], coder_binary["RA"], coder_everywhere),
                "AL-RA": cohen_kappa(oracle_binary["AL"], oracle_binary["RA"], oracle_everywhere),
                "AL-MN": cohen_kappa(oracle_binary["AL"], oracle_binary["MN"], oracle_everywhere),
                "US-RA": cohen_kappa(oracle_binary["US"], oracle_binary["RA"], oracle_everywhere),
                "US-MN": cohen_kappa(oracle_binary["US"], oracle_binary["MN"], oracle_everywhere),
            }
    return AnderssonResult(kappas=kappas, oracle_failures=tuple(oracle_failures))


def test_the_harness_reproduces_the_coders_own_agreement(andersson):
    for key, (mn_ra, _al_ra, _al_mn) in PAPER_TABLE_3.items():
        assert andersson.kappas[key]["MN-RA"] == pytest.approx(mn_ra, abs=CODER_TOLERANCE), key


def test_the_harness_reproduces_the_oracles_agreement(andersson):
    """If this fails while the coders' own agreement reproduces, the
    installed oracle's behaviour differs from the paper's version. Stop and
    report; do not widen."""
    for key, (_mn_ra, al_ra, al_mn) in PAPER_TABLE_3.items():
        assert andersson.kappas[key]["AL-RA"] == pytest.approx(al_ra, abs=ORACLE_TOLERANCE), key
        assert andersson.kappas[key]["AL-MN"] == pytest.approx(al_mn, abs=ORACLE_TOLERANCE), key


def test_the_oracle_fails_only_on_the_diagnosed_file(andersson, capsys):
    """Bounds `_oracle`'s exclusion from the shared AL/US file set (spec
    5.3): the files it actually fails on, and why, must be exactly
    `EXPECTED_ORACLE_FAILURES` -- not whatever a future scipy, remodnav or
    dataset change happens to produce. A new failure (a different file, or
    the same file for a different reason) or a diagnosed one that stops
    failing must fail this test loudly, rather than being silently folded
    into (or out of) `kappas`.

    Also carries the exclusion notice itself, printed under
    `capsys.disabled()` so it is visible on every run -- gated or not,
    `-s` or not -- unlike the fixture's own (silent-by-default) computation."""
    actual = {(stim, fname): msg for stim, fname, msg in andersson.oracle_failures}
    total = sum(len(v) for v in ANDERSSON_FILES.values())
    with capsys.disabled():
        if actual:
            print(f"\n  oracle excluded from the shared AL/US file set ({len(actual)} of {total} RA files; "
                  "MN-RA is unaffected -- it is computed over all 34 files):")
            for (stim, fname), msg in actual.items():
                print(f"    {stim}/{fname}: {msg}")
        else:
            print(f"\n  oracle excluded no files from the shared AL/US file set (all {total} RA files ran).")
    assert actual.keys() == EXPECTED_ORACLE_FAILURES.keys(), (
        "oracle failures do not match the diagnosed set", sorted(actual), sorted(EXPECTED_ORACLE_FAILURES)
    )
    for key, expected_fragment in EXPECTED_ORACLE_FAILURES.items():
        assert expected_fragment in actual[key], (key, actual[key], expected_fragment)


def test_our_saccade_agreement_is_within_its_prediction_of_the_oracles(andersson, capsys):
    with capsys.disabled():
        for key, k in sorted(andersson.kappas.items()):
            print(f"\n  {key}: " + ", ".join(f"{pair}={value:.3f}" for pair, value in k.items()))
    for stim in ANDERSSON_FILES:
        k = andersson.kappas[(stim, "Sac")]
        assert k["US-RA"] >= k["AL-RA"] - OURS_BELOW_ORACLE_AT_MOST, stim
        assert k["US-MN"] >= k["AL-MN"] - OURS_BELOW_ORACLE_AT_MOST, stim
