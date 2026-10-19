"""The saccade detector's published fixtures (`docs/fixtures/saccade/`), which
wl-xcon's contract tests read instead of importing this package (its XC-398):
its online detector mirrors this one, and its CI, which merges nothing red,
would otherwise go red the moment this one changed. The committed v1 is what
today's code produces, so a change to the detector, its velocity, its scale or
an interval's size fails here first; the answer is a new version beside v1,
never an edit to it."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from wl_preproc.eye.detect import contract_fixtures
from wl_preproc.eye.detect.engbert_kliegl import (
    DEFAULT_EK_PARAMS, DEFAULT_LAMBDA, DEFAULT_MIN_DURATION_SAMPLES, _median_scale, detect_engbert_kliegl,
)
from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude
from wl_preproc.eye.detect.velocity import velocity

V1 = Path(__file__).resolve().parents[3] / "docs" / "fixtures" / "saccade" / "v1.json"


def _v1() -> dict:
    return json.loads(V1.read_text(encoding="utf-8"))


def test_the_published_v1_is_what_the_detector_does_today():
    assert _v1() == json.loads(contract_fixtures.dumps(contract_fixtures.build_v1()))
    assert V1.read_text(encoding="utf-8") == contract_fixtures.dumps(contract_fixtures.build_v1())


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
        assert [{"start": r.start, "stop": r.stop, "label": r.label.value,
                 "amplitude_deg": amplitude(gaze, r.start, r.stop)} for r in runs] == case["runs"], case["name"]


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
