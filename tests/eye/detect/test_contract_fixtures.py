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

import dataclasses
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from wl_preproc.eye.detect.engbert_kliegl import (
    DEFAULT_EK_PARAMS, DEFAULT_LAMBDA, DEFAULT_MIN_DURATION_SAMPLES, _median_scale, detect_engbert_kliegl,
    thresholds,
)
from wl_preproc.eye.detect.geometry import PER_AXIS, TRUE_ANGLE
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
            assert amplitude(gaze, r.start, r.stop, geometry=PER_AXIS) == pytest.approx(run["amplitude_deg"], rel=1e-12, abs=0.0), (
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


# The frozen companion beside v1 (`v1-thresholds.json`): each v1 trace's two
# thresholds, and traces whose movements sit near them. wl-xcon found v1 pins
# the threshold only to about +-40% (any lambda from 4.5 to 8.5 gives v1's
# intervals) and an ellipse limit of 1.5 not at all; these close that.

V1_THRESHOLDS = V1.with_name("v1-thresholds.json")
V1_THRESHOLDS_SHA256 = "f6da9801eb3f196b50ba546710b27816879acc46011ac117ff7ddbdeaad56e8e"


def _thresholds_file() -> dict:
    return json.loads(V1_THRESHOLDS.read_text(encoding="utf-8"))


def test_the_published_thresholds_are_never_edited():
    assert hashlib.sha256(V1_THRESHOLDS.read_bytes()).hexdigest() == V1_THRESHOLDS_SHA256


def test_the_thresholds_name_v1_and_the_rule_they_follow():
    companion = _thresholds_file()
    assert companion["companion_to"] == {"file": "v1.json", "sha256": V1_SHA256}
    assert companion["geometry"] == "per-axis"
    assert {case["name"] for case in companion["v1_traces"]} == {case["name"] for case in _v1()["traces"]}


def test_a_reader_gets_every_threshold_back():
    """Thresholds are medians, products and `sqrt`, so they are held exactly;
    a near-threshold trace's intervals too, and its sizes to 1e-12, as v1's."""
    v1, companion = _v1(), _thresholds_file()
    fs_hz, by_name = v1["fs_hz"], {case["name"]: case for case in v1["traces"]}
    for case in companion["v1_traces"]:
        trace = by_name[case["name"]]
        speed = velocity(np.array(trace["gaze_deg"]), fs_hz)
        usable = np.array([entry is None for entry in trace["available"]])
        assert list(thresholds(speed, usable, DEFAULT_EK_PARAMS)) == case["eta_deg_s"], case["name"]
    for case in companion["near_threshold_traces"]:
        gaze = np.array(case["gaze_deg"])
        speed = velocity(gaze, fs_hz)
        assert speed.tolist() == case["velocity_deg_s"], case["name"]
        usable = np.array([entry is None for entry in case["available"]])
        assert list(thresholds(speed, usable, DEFAULT_EK_PARAMS)) == case["eta_deg_s"], case["name"]
        runs = detect_engbert_kliegl(gaze, speed, np.array(case["available"], dtype=object), fs_hz,
                                     DEFAULT_EK_PARAMS)
        assert [(r.start, r.stop, r.label.value) for r in runs] == [
            (run["start"], run["stop"], run["label"]) for run in case["runs"]], case["name"]
        for r, run in zip(runs, case["runs"], strict=True):
            assert amplitude(gaze, r.start, r.stop, geometry=PER_AXIS) == pytest.approx(run["amplitude_deg"], rel=1e-12, abs=0.0)


def test_the_near_threshold_traces_catch_a_threshold_off_by_five_percent_or_a_wider_ellipse():
    """What v1 could not: lambda 5% either way, and the ellipse's limit moved
    from 1 to 1.1 or 1.5, each change some interval. A limit L is lambda
    scaled by sqrt(L), since (v/eta)^2 > L is (v/(eta sqrt(L)))^2 > 1."""
    fs_hz = _v1()["fs_hz"]
    for scale in (0.95, 1.05, np.sqrt(1.1), np.sqrt(1.5)):
        params = dataclasses.replace(DEFAULT_EK_PARAMS, lambda_=DEFAULT_EK_PARAMS.lambda_ * scale)
        changed = False
        for case in _thresholds_file()["near_threshold_traces"]:
            gaze = np.array(case["gaze_deg"])
            runs = detect_engbert_kliegl(gaze, velocity(gaze, fs_hz), np.array(case["available"], dtype=object),
                                         fs_hz, params)
            changed |= [(r.start, r.stop) for r in runs] != [(run["start"], run["stop"]) for run in case["runs"]]
        assert changed, scale


# v2, true visual angle (design spec `2026-10-30-true-angle-saccades-design.md`
# section 9): v1's and the companion's inputs, and a short trace, with what
# this code makes of them under the `true-angle` setting.

V2 = V1.with_name("v2.json")
V2_SHA256 = "481eab5b1a8465da5994af16c5d332778649231261bd58ca5f38639853b46339"
_V2_PARAMS = dataclasses.replace(DEFAULT_EK_PARAMS, geometry=TRUE_ANGLE)


def _v2() -> dict:
    return json.loads(V2.read_text(encoding="utf-8"))


#: v2's one tolerance, as its `about` states it (amendment 4): every number
#: but the intervals and labels passes through sin, cos or atan2, which C
#: libraries and other forms of the direction round differently in the last
#: places, and a velocity component near zero loses relative digits.
_V2_TOLERANCE = "|got - want| <= 1e-9 + 1e-12 * |want|"


def _close(got, want) -> bool:
    """Both null, or within v2's tolerance."""
    if want is None or got is None:
        return got is want
    return bool(np.all(np.abs(np.asarray(got) - np.asarray(want)) <= 1e-9 + 1e-12 * np.abs(np.asarray(want))))


def test_the_published_v2_is_never_edited():
    assert hashlib.sha256(V2.read_bytes()).hexdigest() == V2_SHA256


def test_v2_states_the_tolerance_its_readers_hold():
    """wl-xcon's reader holds v2 to what the file says, so the file says what
    this test holds it to."""
    assert _V2_TOLERANCE in _v2()["about"]


def test_v2_names_its_geometry_its_sources_and_its_defaults():
    fixture = _v2()
    assert (fixture["version"], fixture["geometry"]) == (2, TRUE_ANGLE)
    assert fixture["derived_from"] == {"v1.json": V1_SHA256, "v1-thresholds.json": V1_THRESHOLDS_SHA256}
    assert fixture["defaults"] == _v1()["defaults"] and fixture["fs_hz"] == _v1()["fs_hz"]


def test_v2s_inputs_are_v1s_and_the_companions():
    by_source = {"v1.json": {case["name"]: case for case in _v1()["traces"]},
                 "v1-thresholds.json": {case["name"]: case for case in _thresholds_file()["near_threshold_traces"]}}
    traces = _v2()["traces"]
    assert [case["name"] for case in traces if case["inputs_from"] != "v2"] == [
        *by_source["v1.json"], *by_source["v1-thresholds.json"]]
    for case in traces:
        if case["inputs_from"] != "v2":
            source = by_source[case["inputs_from"]][case["name"]]
            assert (case["gaze_deg"], case["available"]) == (source["gaze_deg"], source["available"]), case["name"]


def test_a_reader_of_v2_gets_its_outputs_back_from_its_inputs():
    """What wl-xcon's test will do, from the file alone: the intervals and
    labels exactly, every other number to a relative 1e-12."""
    from wl_preproc.eye.detect.measure import measure
    from wl_preproc.eye.detect.velocity import local_velocity

    fixture = _v2()
    fs_hz = fixture["fs_hz"]
    for case in fixture["traces"]:
        gaze = np.array(case["gaze_deg"])
        speed = local_velocity(gaze, fs_hz)
        assert _close(speed, np.array(case["velocity_deg_s"])), case["name"]
        usable = np.array([entry is None for entry in case["available"]])
        assert all(_close(got, want) for got, want in zip(thresholds(speed, usable, _V2_PARAMS), case["eta_deg_s"],
                                                          strict=True)), case["name"]
        runs = detect_engbert_kliegl(gaze, speed, np.array(case["available"], dtype=object), fs_hz, _V2_PARAMS)
        assert [(r.start, r.stop, r.label.value) for r in runs] == [
            (run["start"], run["stop"], run["label"]) for run in case["runs"]], case["name"]
        for r, run in zip(runs, case["runs"], strict=True):
            got = measure(gaze, speed, r.start, r.stop, fs_hz, geometry=TRUE_ANGLE)
            for field in ("amplitude_deg", "peak_velocity_deg_s", "direction_deg", "start_eccentricity_deg",
                          "start_polar_deg", "end_eccentricity_deg", "end_polar_deg"):
                assert _close(getattr(got, field), run[field]), (case["name"], r.start, field)
            assert (got.start_x_deg, got.start_y_deg, got.end_x_deg, got.end_y_deg) == (
                run["start_x_deg"], run["start_y_deg"], run["end_x_deg"], run["end_y_deg"])


def test_v2s_short_trace_pins_the_complete_window_scale():
    """Section 6.1: its thresholds are more than 1% from v1's scale, taken
    over every usable sample, on both axes, so a reader that keeps v1's scale
    fails on them."""
    from wl_preproc.eye.detect.velocity import local_velocity

    (case,) = [case for case in _v2()["traces"] if case["inputs_from"] == "v2"]
    speed = local_velocity(np.array(case["gaze_deg"]), _v2()["fs_hz"])
    usable = np.array([entry is None for entry in case["available"]])
    old = thresholds(speed, usable, DEFAULT_EK_PARAMS)
    assert all(abs(new / was - 1.0) > 0.01 for new, was in zip(case["eta_deg_s"], old, strict=True))


def test_in_v2_the_step_across_the_cut_is_a_microsaccade():
    """1.12 degrees up from (30, 0), planted: a saccade in v1, and in v2
    0.970 degrees of true angle as planted, under the cut with the noise too
    (wl-xcon's proposals 1 and 3)."""
    (v1_case,) = [case for case in _v1()["traces"] if case["name"] == "off-centre-30-across-the-cut"]
    (v2_case,) = [case for case in _v2()["traces"] if case["name"] == "off-centre-30-across-the-cut"]
    assert [run["label"] for run in v1_case["runs"]] == ["saccade"]
    assert [run["label"] for run in v2_case["runs"]] == ["microsaccade"]
