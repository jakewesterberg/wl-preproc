"""The saccade detector's published fixtures (`docs/fixtures/saccade/`), which
wl-xcon's contract tests read instead of importing this package (its XC-398):
its online detector mirrors this one, and its CI, which merges nothing red,
would otherwise go red the moment this one changed. v1 is pinned by its digest,
and today's code must reproduce every output in it from its inputs, so a change
to the detector, its velocity, its scale or an interval's size fails here
first; the answer is a new version beside v1, never an edit to it.

The pin is the file's digest, not a regeneration from `build_v1`: v1's inputs
are seeded numpy draws, and numpy does not promise the same draws across
versions, so a regeneration could differ in CI with nothing wrong. Once
published, the inputs are the file's, and a later version reads them from it."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from wl_preproc.eye.detect.engbert_kliegl import (
    DEFAULT_EK_PARAMS, DEFAULT_LAMBDA, DEFAULT_MIN_DURATION_SAMPLES, _median_scale, detect_engbert_kliegl,
)
from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude
from wl_preproc.eye.detect.velocity import velocity

V1 = Path(__file__).resolve().parents[3] / "docs" / "fixtures" / "saccade" / "v1.json"
V1_SHA256 = "f94ea3a32521e7c5bdfb80040f10fe82303f76550296145b21fbeb94d9667666"


def _v1() -> dict:
    return json.loads(V1.read_text(encoding="utf-8"))


def test_the_published_v1_is_never_edited():
    assert hashlib.sha256(V1.read_bytes()).hexdigest() == V1_SHA256


def test_v1_names_its_geometry_and_the_defaults_it_was_made_with():
    fixture = _v1()
    assert fixture["version"] == 1
    assert fixture["geometry"] == "per-axis"
    assert fixture["fs_hz"] == 500.0
    assert fixture["defaults"] == {
        "lambda": DEFAULT_LAMBDA, "min_duration_samples": DEFAULT_MIN_DURATION_SAMPLES,
        "microsaccade_max_deg": MICROSACCADE_MAX_DEG,
    }


def test_a_reader_of_v1_gets_its_outputs_back_from_its_inputs():
    """What wl-xcon's test does, from the file alone: the inputs come back
    from JSON exactly, so this code reproduces every output to the bit."""
    fixture = _v1()
    for case in fixture["median_scale"]:
        assert _median_scale(np.array(case["values"], dtype=float)) == case["scale"], case["name"]
    fs_hz = fixture["fs_hz"]
    for case in fixture["traces"]:
        gaze = np.array(case["gaze_deg"])
        speed = velocity(gaze, fs_hz)
        assert speed.tolist() == case["velocity_deg_s"], case["name"]
        runs = detect_engbert_kliegl(gaze, speed, np.array(case["available"], dtype=object), fs_hz,
                                     DEFAULT_EK_PARAMS)
        assert [(r.start, r.stop, r.label.value) for r in runs] == [
            (run["start"], run["stop"], run["label"]) for run in case["runs"]], case["name"]
        # A size to within a few units in the last place, not to the bit: `np.hypot` is the C library's,
        # which is not correctly rounded, and Linux's differs from macOS's by one unit on these traces
        # (CI on 10ea913, 8.001592662084843 against the file's ...844). Everything else here is
        # arithmetic and `sqrt`, which IEEE 754 rounds the same everywhere, so it is held exactly.
        for r, run in zip(runs, case["runs"], strict=True):
            assert amplitude(gaze, r.start, r.stop) == pytest.approx(run["amplitude_deg"], rel=1e-12, abs=0.0), (
                case["name"])


def test_v1_covers_what_true_angle_will_change():
    """Saccades off centre, where a step in degrees per axis is not its true
    angle; both labels, so the 1-degree cut is pinned; a span the mask claims;
    and the near-tied scale whose rounding the detector guards against."""
    fixture = _v1()
    traces = fixture["traces"]
    assert all(case["runs"] for case in traces)
    assert {run["label"] for case in traces for run in case["runs"]} == {"saccade", "microsaccade"}
    assert any(abs(case["gaze_deg"][0][0]) >= 20.0 for case in traces)
    assert any(entry is not None for case in traces for entry in case["available"])
    assert any(case["scale"] == 0.0 and len(case["values"]) == 4 and len(set(case["values"])) == 4
               for case in fixture["median_scale"])
