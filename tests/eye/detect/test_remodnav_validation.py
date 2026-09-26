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
