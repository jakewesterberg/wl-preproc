"""The saccade detector's behaviour as versioned fixtures, for wl-xcon's
contract tests (its XC-398; `docs/pending-wl-xcon-amendments.md`).

wl-xcon's online detector mirrors `engbert_kliegl.py` -- velocity per axis,
the median scale, the elliptic test, and `measure.amplitude` -- and its CI
checks that the two agree. It imported this package to do so until it asked
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

`python -m wl_preproc.eye.detect.contract_fixtures {v1,v1-thresholds}
[--out DIR]` wrote each, and refuses to write over a published file;
`tests/eye/detect/test_contract_fixtures.py` pins each by its digest and holds
this code to every output in it. v1's inputs are seeded numpy draws, which
numpy does not promise across versions, so whatever comes after reads them
from v1 rather than drawing them again, as the companion does.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from wl_preproc.eye.detect.engbert_kliegl import (
    DEFAULT_EK_PARAMS, DEFAULT_LAMBDA, DEFAULT_MIN_DURATION_SAMPLES, _median_scale, detect_engbert_kliegl,
    thresholds,
)
from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude
from wl_preproc.eye.detect.velocity import velocity

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
                      "amplitude_deg": amplitude(gaze, run.start, run.stop)} for run in runs],
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
                      "amplitude_deg": amplitude(gaze, run.start, run.stop)} for run in runs],
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
    parser.add_argument("which", choices=("v1", "v1-thresholds"))
    parser.add_argument("--out", type=Path, default=Path("docs/fixtures/saccade"))
    args = parser.parse_args(argv)
    path = args.out / f"{args.which}.json"
    if path.exists():
        parser.error(f"{path} is published, and a published fixture is never edited")
    if args.which == "v1":
        text = dumps(build_v1())
    else:
        raw = (args.out / "v1.json").read_bytes()
        text = dumps(build_v1_thresholds(json.loads(raw), hashlib.sha256(raw).hexdigest()))
    args.out.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
