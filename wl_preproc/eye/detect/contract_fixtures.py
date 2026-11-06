"""The saccade detector's behaviour as versioned fixtures, for wl-xcon's
contract tests (its XC-398; `docs/pending-wl-xcon-amendments.md`).

wl-xcon's online detector mirrors `engbert_kliegl.py` -- the velocity, the
median scale, the elliptic test, and `measure.amplitude`; per axis as of v1,
in true angle from v2 -- and its CI checks that the two agree. It imported this package to do so until it asked
for files instead: its CI merges nothing red, and any change here would have
turned it red before it could match. Each version is one JSON file of inputs
and this code's outputs under `docs/fixtures/saccade/`. **A published version
is never edited:** a change of behaviour is the next version, beside it, made
from the same inputs, so a consumer moves by changing which file it reads.

v1 is the geometry of 2026-10-19: gaze in degrees per axis, distances by
`hypot`. Its off-centre traces are where true visual angle will differ (the
requester's choice, recorded in wl-xcon's XC-398).

`v1-thresholds.json` is a frozen companion beside it: each v1 trace's two
thresholds as the detector computes them, and two traces whose movements sit
near them. wl-xcon found that v1 pins the threshold only to about +-40% and an
ellipse limit above 1 not at all; these close that (the requester's choice of
2026-10-20).

v2 is true visual angle (design spec `2026-10-30-true-angle-saccades-design.md`
section 9): v1's traces and the companion's, read from those files, and one
short trace that pins the threshold's scale over complete windows, each with
what this code makes of it under the `true-angle` setting -- the local-frame
velocity, the thresholds, the runs and their measurements.

`python -m wl_preproc.eye.detect.contract_fixtures {v1,v1-thresholds,v2}
[--out DIR]` wrote each, and refuses to write over a published file;
`tests/eye/detect/test_contract_fixtures.py` pins each by its digest and holds
this code to every output in it. v1's inputs are seeded numpy draws, which
numpy does not promise across versions, so whatever comes after reads them
from v1 rather than drawing them again, as the companion does.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
from pathlib import Path

import numpy as np

from wl_preproc.eye.detect.engbert_kliegl import (
    DEFAULT_EK_PARAMS, DEFAULT_LAMBDA, DEFAULT_MIN_DURATION_SAMPLES, _median_scale, detect_engbert_kliegl,
    thresholds,
)
from wl_preproc.eye.detect.geometry import PER_AXIS, TRUE_ANGLE
from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude, measure
from wl_preproc.eye.detect.velocity import local_velocity, velocity

VERSION = 1
#: Both sides' rate: the synthetic tracker's (`synth/ohdpi.py::OHDPI_FPS`) and wl-xcon's contract test's.
FS_HZ = 500.0
_SAMPLES = 400
_NOISE_DEG = 0.01
# Inputs are stored to the microdegree, so they are short in the file and
# every output is computed from exactly the doubles a reader parses.
_DECIMALS = 6

# (name, note, start (x, y) in degrees, steps (onset, samples, dx, dy), masked (start, stop, label) or None).
# Each step ramps linearly over its samples and holds.
_TRACES = (
    ("centre-horizontal", "an 8-degree step right from straight ahead", (0.0, 0.0), ((200, 12, 8.0, 0.0),), None),
    ("centre-vertical", "an 8-degree step up from straight ahead", (0.0, 0.0), ((200, 12, 0.0, 8.0),), None),
    ("centre-oblique", "a step of (6, -4) degrees from straight ahead", (0.0, 0.0), ((200, 12, 6.0, -4.0),), None),
    ("off-centre-20-up-2", "2 degrees up from (20, 0): 1.879 degrees of true angle", (20.0, 0.0),
     ((200, 8, 0.0, 2.0),), None),
    ("off-centre-30-up-2", "2 degrees up from (30, 0): 1.732 degrees of true angle", (30.0, 0.0),
     ((200, 8, 0.0, 2.0),), None),
    ("off-centre-30-across-the-cut", "1.12 degrees up from (30, 0): a saccade per axis, 0.970 degrees of true angle",
     (30.0, 0.0), ((200, 8, 0.0, 1.12),), None),
    ("centre-microsaccade", "0.6 degrees right from straight ahead, under the 1-degree cut", (0.0, 0.0),
     ((200, 8, 0.6, 0.0),), None),
    ("a-blink-between-two-saccades", "samples 180-219 masked as a blink, the gaze across them thrown as a blink "
     "throws it: excluded from the scale and from the output", (0.0, 0.0),
     ((100, 12, 5.0, 0.0), (300, 12, -5.0, 2.0)), (180, 220, Label.BLINK)),
)

# The first five are the values wl-xcon's contract test passed to
# `_median_scale` before these files, both normal draws from one generator
# seeded 11, in that order; the last is a near-tie whose variance rounds to
# -1.1e-16, which the scale's guard turns back into the zero it should be.
_DRAWS = np.random.default_rng(11)
_SCALE_INPUTS = (
    ("all-equal", [1.0, 1.0, 1.0, 1.0]),
    ("symmetric", [0.1, -0.1, 0.2, -0.2]),
    ("normal-200-sd-3", _DRAWS.normal(0, 3, 200).tolist()),
    ("normal-51-sd-0.01", _DRAWS.normal(0, 0.01, 51).tolist()),
    ("empty", []),
    ("near-tie-rounds-negative", [-0.7298069899551776, -0.7298069899551779, 0.15212593485435533, -1.138548746646266]),
)


# The companion's two near-threshold traces: (name, seed, direction). Each
# carries six movements of 12 samples, one every 170 samples from sample 100,
# along `direction` at each of `_NEAR_RATIOS` times the speed at which that
# trace's noise alone puts the ellipse in that direction.
_NEAR_SAMPLES = 1200
_NEAR_THRESHOLD = (("near-threshold-horizontal", 101, (1.0, 0.0)), ("near-threshold-oblique", 102, (1.0, 1.0)))
_NEAR_RATIOS = (0.95, 1.05, 1.15, 1.25, 1.4, 1.6)


def _trace(seed: int, start: tuple[float, float], steps, masked, samples: int = _SAMPLES) -> tuple[np.ndarray, list]:
    gaze = np.random.default_rng(seed).normal(0.0, _NOISE_DEG, (samples, 2)) + np.asarray(start)
    for onset, ramp, dx, dy in steps:
        step = np.array([dx, dy])
        gaze[onset:] += step
        gaze[onset:onset + ramp] -= np.outer(1.0 - np.linspace(0.0, 1.0, ramp), step)
    available: list = [None] * samples
    if masked is not None:
        first, stop, label = masked
        gaze[first:stop] = np.asarray(start) + np.array([0.0, -15.0])
        available[first:stop] = [label.value] * (stop - first)
    return np.round(gaze, _DECIMALS), available


def build_v1() -> dict:
    """v1: every input with what this code makes of it today."""
    traces = []
    for seed, (name, note, start, steps, masked) in enumerate(_TRACES, start=3):
        gaze, available = _trace(seed, start, steps, masked)
        speed = velocity(gaze, FS_HZ)
        runs = detect_engbert_kliegl(gaze, speed, np.array(available, dtype=object), FS_HZ, DEFAULT_EK_PARAMS)
        traces.append({
            "name": name, "note": note, "gaze_deg": gaze.tolist(), "available": available,
            "velocity_deg_s": speed.tolist(),
            "runs": [{"start": int(run.start), "stop": int(run.stop), "label": run.label.value,
                      "amplitude_deg": amplitude(gaze, run.start, run.stop, geometry=PER_AXIS)} for run in runs],
        })
    return {
        "version": VERSION,
        "geometry": "per-axis",
        "about": "wl-preproc's Engbert-Kliegl saccade detector as of v1: gaze in degrees per axis, velocity per axis "
                 "(velocity.py), the median scale and elliptic test (engbert_kliegl.py), an interval's size between "
                 "its first and last samples by hypot (measure.amplitude). Samples are evenly spaced at fs_hz from "
                 "t = 0. available: null where a detector may label, else the label the mask gave. A run is "
                 "[start, stop) in samples. Never edited; a change of behaviour is the next version.",
        "fs_hz": FS_HZ,
        "defaults": {"lambda": DEFAULT_LAMBDA, "min_duration_samples": DEFAULT_MIN_DURATION_SAMPLES,
                     "microsaccade_max_deg": MICROSACCADE_MAX_DEG},
        "median_scale": [{"name": name, "values": values, "scale": _median_scale(np.array(values, dtype=float))}
                         for name, values in _SCALE_INPUTS],
        "traces": traces,
    }


def _near_threshold_trace(seed: int, direction: tuple[float, float]) -> tuple[np.ndarray, list]:
    """Movements at graded multiples of the speed that crosses the ellipse
    in `direction`, which is set by the noise alone: `1 / sqrt(dx^2/eta_x^2 +
    dy^2/eta_y^2)` for the unit direction `(dx, dy)`."""
    still, _ = _trace(seed, (0.0, 0.0), (), None, samples=_NEAR_SAMPLES)
    eta_x, eta_y = thresholds(velocity(still, FS_HZ), np.ones(_NEAR_SAMPLES, dtype=bool), DEFAULT_EK_PARAMS)
    unit = np.asarray(direction) / np.hypot(*direction)
    crossing = 1.0 / np.sqrt(unit[0] ** 2 / eta_x ** 2 + unit[1] ** 2 / eta_y ** 2)
    steps = tuple((100 + 170 * k, 12, *(unit * ratio * crossing * 12 / FS_HZ)) for k, ratio in enumerate(_NEAR_RATIOS))
    return _trace(seed, (0.0, 0.0), steps, None, samples=_NEAR_SAMPLES)


def build_v1_thresholds(v1: dict, v1_sha256: str) -> dict:
    """v1's companion: each v1 trace's thresholds, from v1's own inputs, and
    the near-threshold traces with everything this code makes of them."""
    fs_hz = v1["fs_hz"]
    v1_traces = []
    for case in v1["traces"]:
        speed = velocity(np.array(case["gaze_deg"]), fs_hz)
        usable = np.array([entry is None for entry in case["available"]])
        v1_traces.append({"name": case["name"], "eta_deg_s": list(thresholds(speed, usable, DEFAULT_EK_PARAMS))})
    near = []
    for name, seed, direction in _NEAR_THRESHOLD:
        gaze, available = _near_threshold_trace(seed, direction)
        speed = velocity(gaze, fs_hz)
        usable = np.array([entry is None for entry in available])
        runs = detect_engbert_kliegl(gaze, speed, np.array(available, dtype=object), fs_hz, DEFAULT_EK_PARAMS)
        near.append({
            "name": name, "note": f"six movements along {list(direction)} at {list(_NEAR_RATIOS)} times the speed "
                                  "that crosses the ellipse there, one every 170 samples from sample 100, each over "
                                  "12 samples; not every one is detected",
            "gaze_deg": gaze.tolist(), "available": available, "velocity_deg_s": speed.tolist(),
            "eta_deg_s": list(thresholds(speed, usable, DEFAULT_EK_PARAMS)),
            "runs": [{"start": int(run.start), "stop": int(run.stop), "label": run.label.value,
                      "amplitude_deg": amplitude(gaze, run.start, run.stop, geometry=PER_AXIS)} for run in runs],
        })
    return {
        "version": 1,
        "companion_to": {"file": "v1.json", "sha256": v1_sha256},
        "geometry": "per-axis",
        "about": "The thresholds of v1's detector: eta = lambda times each axis's median scale, "
                 "sqrt(median(v^2) - median(v)^2), or 0 when that is not positive, over the velocity at every "
                 "sample the mask leaves usable -- the two zero-filled samples at each edge, and any whose "
                 "five-sample window touches a masked sample, included. A sample is in an event when "
                 "(vx/eta_x)^2 + (vy/eta_y)^2 > 1. v1_traces gives each v1 trace's [eta_x, eta_y] in deg/s; the "
                 "near-threshold traces, at v1's fs_hz, move at speeds close to the ellipse, so lambda off by 5% "
                 "or a limit above 1 changes their runs. A run is [start, stop) in samples; amplitude_deg is "
                 "np.hypot, which C libraries round differently in the last place. Never edited.",
        "v1_traces": v1_traces,
        "near_threshold_traces": near,
    }


# v2's short trace: 60 samples of the same noise about (10, 5), three masked
# as a blink in the middle. Its four edge samples and the eight whose windows
# touch the blink move the scale by 9% across and 21% up (measured
# 2026-10-30), so its thresholds tell the complete-window scale from v1's.
_SHORT = ("short-complete-windows", 22, (10.0, 5.0), 60, (30, 33, Label.BLINK))

#: The measurements each v2 run carries, `measure`'s under true angle.
_V2_MEASUREMENTS = ("amplitude_deg", "peak_velocity_deg_s", "direction_deg", "start_x_deg", "start_y_deg",
                    "end_x_deg", "end_y_deg", "start_eccentricity_deg", "start_polar_deg", "end_eccentricity_deg",
                    "end_polar_deg")


def _short_trace() -> tuple[np.ndarray, list]:
    """Noise alone, with the blink masked but its gaze left as it was: what
    the trace pins is which samples the scale is taken over."""
    _name, seed, start, samples, (first, stop, label) = _SHORT
    gaze, available = _trace(seed, start, (), None, samples=samples)
    available[first:stop] = [label.value] * (stop - first)
    return gaze, available


def build_v2(v1: dict, v1_sha256: str, companion: dict, companion_sha256: str) -> dict:
    """v2, true angle: each input with what this code makes of it under the
    `true-angle` setting (true-angle spec section 9)."""
    fs_hz = v1["fs_hz"]
    params = dataclasses.replace(DEFAULT_EK_PARAMS, geometry=TRUE_ANGLE)
    cases = [(case["name"], case["note"], "v1.json", case["gaze_deg"], case["available"]) for case in v1["traces"]]
    cases += [(case["name"], case["note"], "v1-thresholds.json", case["gaze_deg"], case["available"])
              for case in companion["near_threshold_traces"]]
    gaze, available = _short_trace()
    cases.append((_SHORT[0], "60 samples of noise about (10, 5), samples 30-32 masked as a blink: its thresholds "
                             "are the complete-window scale's, which differs from v1's by 9% across and 21% up",
                  "v2", gaze.tolist(), available))
    traces = []
    for name, note, source, gaze_deg, mask in cases:
        gaze = np.array(gaze_deg, dtype=float)
        speed = local_velocity(gaze, fs_hz)
        usable = np.array([entry is None for entry in mask])
        runs = detect_engbert_kliegl(gaze, speed, np.array(mask, dtype=object), fs_hz, params)
        traces.append({
            "name": name, "note": note, "inputs_from": source, "gaze_deg": gaze_deg, "available": mask,
            "velocity_deg_s": speed.tolist(), "eta_deg_s": list(thresholds(speed, usable, params)),
            "runs": [{"start": int(run.start), "stop": int(run.stop), "label": run.label.value,
                      **{field: getattr(measure(gaze, speed, run.start, run.stop, fs_hz, geometry=TRUE_ANGLE), field)
                         for field in _V2_MEASUREMENTS}} for run in runs],
        })
    return {
        "version": 2,
        "geometry": TRUE_ANGLE,
        "derived_from": {"v1.json": v1_sha256, "v1-thresholds.json": companion_sha256},
        "about": "wl-preproc's Engbert-Kliegl saccade detector in true visual angle (design spec "
                 "2026-10-30-true-angle-saccades-design.md). Gaze is in degrees per axis; a position (x, y) is the "
                 "direction normalize(tan x, tan y, 1). available: null where a detector may label, else the label "
                 "the mask gave. velocity_deg_s: for each sample n, samples n-2..n+2 in the "
                 "local frame at n's direction (azimuthal-equidistant; ex = normalize(x - (x.c)c), ey = c x ex), "
                 "then (p[n+2] + p[n+1] - p[n-1] - p[n-2]) * fs / 6; the two samples at each edge are 0. "
                 "eta_deg_s = lambda times each axis's median scale, sqrt(median(v^2) - median(v)^2) or 0, over "
                 "complete windows only: a sample and the two on each side all available, so neither the edges "
                 "nor any sample whose window touches a masked one. An available sample is in an event when "
                 "(vx/eta_x)^2 + (vy/eta_y)^2 > 1; an unavailable one never is, and with either eta 0 no sample "
                 "is. A run is a maximal stretch of consecutive event samples, runs shorter than "
                 "min_duration_samples dropped. Size: the angle "
                 "between the first and last samples' directions, atan2(|u x v|, u.v); a run is a saccade at or "
                 "above microsaccade_max_deg. Direction: atan2 of the end's local coordinates about the start. "
                 "Every angle in degrees, polar angles and directions in [0, 360) (mod 360, 0 where that rounds to "
                 "360); null for a polar angle at zero eccentricity and a direction with no displacement. Peak "
                 "speed: the largest |velocity| over the run. A direction is computed here as normalize(sin x cos y, "
                 "cos x sin y, cos x cos y), the same direction as normalize(tan x, tan y, 1); computed otherwise, "
                 "or on another C library, the last places differ. Intervals and labels are exact; every other "
                 "number is held to |got - want| <= 1e-9 + 1e-12 * |want|, in its own unit, since a velocity "
                 "component near zero is a cancelling difference that loses relative digits. inputs_from names the "
                 "file a trace's inputs were read from. A run is [start, stop) in samples. Never edited.",
        "fs_hz": fs_hz,
        "defaults": {"lambda": DEFAULT_LAMBDA, "min_duration_samples": DEFAULT_MIN_DURATION_SAMPLES,
                     "microsaccade_max_deg": MICROSACCADE_MAX_DEG},
        "traces": traces,
    }


def dumps(fixture: dict) -> str:
    """The file's text: one top-level field, or one case, per line, so a
    reader can see its shape and a diff its cases."""
    fields = []
    for key, value in fixture.items():
        if isinstance(value, list):
            cases = ",\n".join("  " + json.dumps(case, separators=(",", ":")) for case in value)
            fields.append(f" {json.dumps(key)}: [\n{cases}\n ]")
        else:
            fields.append(f" {json.dumps(key)}: {json.dumps(value)}")
    return "{\n" + ",\n".join(fields) + "\n}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="write one of the saccade detector's contract fixtures")
    parser.add_argument("which", choices=("v1", "v1-thresholds", "v2"))
    parser.add_argument("--out", type=Path, default=Path("docs/fixtures/saccade"))
    args = parser.parse_args(argv)
    path = args.out / f"{args.which}.json"
    if path.exists():
        parser.error(f"{path} is published, and a published fixture is never edited")
    if args.which == "v1":
        text = dumps(build_v1())
    elif args.which == "v1-thresholds":
        raw = (args.out / "v1.json").read_bytes()
        text = dumps(build_v1_thresholds(json.loads(raw), hashlib.sha256(raw).hexdigest()))
    else:
        raw, companion = (args.out / "v1.json").read_bytes(), (args.out / "v1-thresholds.json").read_bytes()
        text = dumps(build_v2(json.loads(raw), hashlib.sha256(raw).hexdigest(), json.loads(companion),
                              hashlib.sha256(companion).hexdigest()))
    args.out.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
