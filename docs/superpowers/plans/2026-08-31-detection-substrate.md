# Detection substrate and the baseline detector — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build everything saccade detection needs — labels, a shared velocity
estimator, the validity mask, runs-as-rows storage, the detector registry and
schema — plus Engbert–Kliegl as the first detector, round-tripped against
planted truth.

**Architecture:** Detectors are pure functions returning labelled intervals; a
registry holds them and declares each one's vocabulary. Velocity and event
measurement are computed once, upstream and downstream respectively, so that a
later disagreement between detectors is attributable to method rather than to
preprocessing. The per-sample label trace is stored as runs (rows, never a
blob) which tile the sample range exactly.

**Tech Stack:** Python 3.11 (3.13 in CI), NumPy 2.4, pandas 3.0.5, DataJoint
2.3.2, pytest.

**Spec:** `docs/superpowers/specs/2026-08-31-saccade-detection-design.md`

**This is stage 1 of a staged plan.** The spec covers seven detectors, the
consensus suite and vigor; this plan covers the substrate and one detector.
Stage 1 therefore produces **no agreement metric** — that arrives in stage 2
with the second detector, which is when the vocabulary-coarsening lattice
(spec §6.1) is first exercised and therefore first worth building.

## Global Constraints

- **The label enum ships COMPLETE — all eight values — even though stage 1
  produces only five.** `blink`, `invalid`, `saccade`, `microsaccade`,
  `fixation` are produced here; `pso`, `pursuit`, `drift` are declared and
  unused until stage 2. Adding an enum value later is a schema change and the
  migration window closes January 2027 (spec §4.1 of the second-order design).
  Declare the full vocabulary now, produce a subset.
- **Runs tile `[0, n_samples)` exactly** — no gap, no overlap. Checked in code
  and asserted in tests, not merely documented.
- **No bare `longblob` anywhere.** `tests/schema/test_guardrails.py` sweeps for
  it; under DataJoint 2.x it stores a numpy array as its truncated repr —
  measured, 31,488 float32 values became 488 bytes, unrecoverable.
- **Velocity is computed ONCE and passed into detectors.** No detector
  computes its own. This is spec §3's most consequential preprocessing rule.
- **Amplitude, peak velocity and duration are measured ONCE, downstream**, by
  shared code, identically for every detector.
- A refused fit or a refused detection is a **first-class outcome with a
  stated reason**, never an error and never a fabricated result — the
  discipline `EyeCalibration` already uses.
- Comments explain **why**, and cite **by symbol name, never line number** —
  line citations went stale three times in a previous plan.
- Conventional-commit subjects, lowercase after the colon. Venv is
  `.venv/bin/python`. Baseline: **1027 passed, 1 skipped, 1 deselected**;
  `wl-check` clean; CI green on 3.11 **and 3.13**.
- **A green local run is evidence about 3.11 on macOS arm64 and nothing else.**
  CI runs 3.13 too, and has already been red for a day on 3.13 alone.
- Verify `git branch --show-current` before staging and before committing.

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/eye/detect/__init__.py` | package docstring only |
| `wl_preproc/eye/detect/labels.py` | the eight labels, their precedence, and run encode/decode with the tiling invariant |
| `wl_preproc/eye/detect/velocity.py` | the one velocity estimator every detector shares |
| `wl_preproc/eye/detect/measure.py` | amplitude, peak velocity, duration from an interval |
| `wl_preproc/eye/detect/validity.py` | the five-criterion mask |
| `wl_preproc/eye/detect/engbert_kliegl.py` | the baseline detector |
| `wl_preproc/eye/detect/registry.py` | `DETECTORS`, and each entry's declared vocabulary |
| `wl_preproc/schema/detect.py` | `EyeValidity`, `EyeDetection` and its `Run` part |

Split by responsibility rather than by layer: `velocity.py` and `measure.py`
are separate from any detector precisely because they must not belong to one.

---

### Task 1: The labels, and runs that tile

**Files:** Create `wl_preproc/eye/detect/__init__.py`,
`wl_preproc/eye/detect/labels.py`; Test `tests/eye/detect/test_labels.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Label` (StrEnum with 8 members), `PRECEDENCE: tuple[Label, ...]`,
  `Run` (frozen dataclass: `start`, `stop`, `label`), `runs_from_labels(labels)
  -> list[Run]`, `labels_from_runs(runs, n_samples) -> np.ndarray`,
  `TilingError`.

`Run.stop` is **exclusive**, matching Python slice convention, so
`labels[run.start:run.stop]` is the run.

- [ ] **Step 1: Write the failing tests**

```python
import numpy as np
import pytest

from wl_preproc.eye.detect.labels import (
    PRECEDENCE, Label, Run, TilingError, labels_from_runs, runs_from_labels,
)


def test_all_eight_labels_are_declared_even_though_stage_one_uses_five():
    """The enum ships complete. Adding a value later is a schema change, and
    the migration window closes January 2027."""
    assert {label.value for label in Label} == {
        "blink", "invalid", "saccade", "microsaccade",
        "pso", "pursuit", "drift", "fixation",
    }


def test_precedence_puts_blink_above_invalid():
    """A blink IS a validity failure, so generic-first would mean no sample is
    ever labelled `blink` and the label would be dead code that looks alive."""
    assert PRECEDENCE.index(Label.BLINK) < PRECEDENCE.index(Label.INVALID)
    assert PRECEDENCE[-1] is Label.FIXATION


def test_runs_round_trip_through_labels():
    labels = np.array([Label.FIXATION] * 3 + [Label.SACCADE] * 2 + [Label.FIXATION])
    runs = runs_from_labels(labels)

    assert runs == [
        Run(start=0, stop=3, label=Label.FIXATION),
        Run(start=3, stop=5, label=Label.SACCADE),
        Run(start=5, stop=6, label=Label.FIXATION),
    ]
    assert list(labels_from_runs(runs, 6)) == list(labels)


def test_runs_tile_the_whole_range_with_no_gap_or_overlap():
    """THE structural invariant. A blob has no such property; rows do, and it
    is checkable on insert."""
    with pytest.raises(TilingError, match="gap"):
        labels_from_runs([Run(0, 2, Label.FIXATION), Run(3, 6, Label.SACCADE)], 6)
    with pytest.raises(TilingError, match="overlap"):
        labels_from_runs([Run(0, 4, Label.FIXATION), Run(3, 6, Label.SACCADE)], 6)
    with pytest.raises(TilingError, match="does not reach"):
        labels_from_runs([Run(0, 4, Label.FIXATION)], 6)
    with pytest.raises(TilingError, match="does not start"):
        labels_from_runs([Run(1, 6, Label.FIXATION)], 6)


def test_an_empty_trace_has_no_runs():
    assert runs_from_labels(np.array([], dtype=object)) == []
    assert list(labels_from_runs([], 0)) == []


def test_adjacent_runs_never_share_a_label():
    """Two touching runs of the same label are one run. Otherwise the encoding
    is not canonical and two equal traces could store differently."""
    labels = np.array([Label.SACCADE] * 4)
    assert runs_from_labels(labels) == [Run(0, 4, Label.SACCADE)]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_labels.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.eye.detect'`

- [ ] **Step 3: Implement**

```python
"""The label vocabulary, and the run encoding that stores it.

**All eight labels are declared, and stage 1 produces five of them.** Adding
an enum value later is a schema change, and the migration window closes
January 2027 -- so the vocabulary is declared complete now and filled in as
detectors that can emit `pso`, `pursuit` and `drift` arrive.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np


class Label(StrEnum):
    BLINK = "blink"
    INVALID = "invalid"
    SACCADE = "saccade"
    MICROSACCADE = "microsaccade"
    PSO = "pso"
    PURSUIT = "pursuit"
    DRIFT = "drift"
    FIXATION = "fixation"


# Most specific first, `fixation` last as the default. **`BLINK` outranks
# `INVALID` and the order is load-bearing**: a blink IS a validity failure, so
# generic-first would mean no sample is ever labelled `blink`.
#
# `SACCADE` and `MICROSACCADE` are adjacent rather than ranked -- they are a
# split by amplitude, never in contention for the same sample (design spec
# section 1).
PRECEDENCE: tuple[Label, ...] = (
    Label.BLINK,
    Label.INVALID,
    Label.SACCADE,
    Label.MICROSACCADE,
    Label.PSO,
    Label.PURSUIT,
    Label.DRIFT,
    Label.FIXATION,
)


class TilingError(ValueError):
    """Runs do not tile the sample range exactly."""


@dataclass(frozen=True, slots=True)
class Run:
    """One maximal stretch of a single label. `stop` is EXCLUSIVE, so
    `labels[run.start:run.stop]` is the run and `stop - start` is its length
    in samples."""

    start: int
    stop: int
    label: Label


def runs_from_labels(labels: np.ndarray) -> list[Run]:
    """Encode a per-sample label array as maximal runs.

    Maximal, so two adjacent runs never share a label: otherwise the encoding
    is not canonical and two equal traces could store differently, which would
    make every stored comparison depend on how a trace happened to be built.
    """
    if len(labels) == 0:
        return []
    boundaries = [0]
    for index in range(1, len(labels)):
        if labels[index] != labels[index - 1]:
            boundaries.append(index)
    boundaries.append(len(labels))
    return [
        Run(start=boundaries[i], stop=boundaries[i + 1], label=Label(labels[boundaries[i]]))
        for i in range(len(boundaries) - 1)
    ]


def labels_from_runs(runs: list[Run], n_samples: int) -> np.ndarray:
    """Decode runs back to a per-sample array, refusing anything that does not
    tile `[0, n_samples)` exactly.

    **This is the invariant that makes rows better than a blob.** A blob can
    be short, long, or internally inconsistent and nothing notices; runs
    either cover the range exactly or they do not, and that is checkable here
    and again on insert.
    """
    if n_samples == 0:
        if runs:
            raise TilingError(f"{len(runs)} run(s) for a zero-sample trace")
        return np.array([], dtype=object)

    if not runs:
        raise TilingError(f"no runs for a {n_samples}-sample trace")
    if runs[0].start != 0:
        raise TilingError(f"runs do not start at 0: first run starts at {runs[0].start}")

    out = np.empty(n_samples, dtype=object)
    cursor = 0
    for run in runs:
        if run.start > cursor:
            raise TilingError(f"gap between sample {cursor} and run starting at {run.start}")
        if run.start < cursor:
            raise TilingError(f"overlap: run starts at {run.start}, previous ended at {cursor}")
        if run.stop <= run.start:
            raise TilingError(f"run [{run.start}, {run.stop}) is empty or reversed")
        out[run.start : run.stop] = run.label
        cursor = run.stop
    if cursor != n_samples:
        raise TilingError(f"runs end at {cursor}, which does not reach {n_samples}")
    return out
```

- [ ] **Step 4: Run, then mutation-check each test**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_labels.py -v`
Expected: PASS.

Then, with `PYTHONDONTWRITEBYTECODE=1` set (a same-length mutation restored
within one filesystem-mtime second otherwise leaves `__pycache__` serving the
mutated bytecode — this has already cost this project a debugging session):
swap `BLINK` and `INVALID` in `PRECEDENCE` and confirm the precedence test
fails; drop the `cursor != n_samples` check and confirm the tiling test fails;
remove the maximal-run merging and confirm the adjacency test fails. Report
what you observed, not what you predict.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/eye/detect/ tests/eye/detect/
git commit -m "detect: eight labels, and runs that tile the sample range"
```

---

### Task 2: The one velocity estimator

**Files:** Create `wl_preproc/eye/detect/velocity.py`; Test
`tests/eye/detect/test_velocity.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `velocity(gaze_deg: np.ndarray, fs_hz: float) -> np.ndarray` of
  shape `(n, 2)` in degrees per second.

- [ ] **Step 1: Write the failing tests**

```python
import numpy as np
import pytest

from wl_preproc.eye.detect.velocity import velocity


def test_a_constant_gaze_has_zero_velocity():
    gaze = np.tile([3.0, -2.0], (20, 1))
    assert velocity(gaze, 500.0) == pytest.approx(np.zeros((20, 2)), abs=1e-12)


def test_a_linear_ramp_recovers_its_own_slope():
    """5 deg/s in x and -3 deg/s in y, sampled at 500 Hz. Interior samples must
    recover exactly; the estimator's own window is what makes the first two and
    last two samples different, which the next test pins."""
    fs_hz = 500.0
    t = np.arange(50) / fs_hz
    gaze = np.column_stack([5.0 * t, -3.0 * t])

    result = velocity(gaze, fs_hz)

    assert result[2:-2, 0] == pytest.approx(5.0, abs=1e-9)
    assert result[2:-2, 1] == pytest.approx(-3.0, abs=1e-9)


def test_the_two_samples_at_each_edge_are_zero_not_wrong():
    """The five-point estimator has no window at the edges. Zero is stated
    rather than extrapolated: a fabricated edge velocity would be indis-
    tinguishable from a real one to every detector downstream, and a saccade
    detected in the first two samples of a recording is an artifact."""
    fs_hz = 500.0
    t = np.arange(30) / fs_hz
    gaze = np.column_stack([100.0 * t, 100.0 * t])

    result = velocity(gaze, fs_hz)

    assert result[:2] == pytest.approx(np.zeros((2, 2)))
    assert result[-2:] == pytest.approx(np.zeros((2, 2)))


def test_the_rate_scales_the_result():
    """A velocity in degrees per SECOND must double when the same samples are
    declared to have arrived twice as fast."""
    gaze = np.column_stack([np.arange(20) * 0.1, np.zeros(20)])

    slow = velocity(gaze, 500.0)
    fast = velocity(gaze, 1000.0)

    assert fast[2:-2] == pytest.approx(2.0 * slow[2:-2])


def test_a_trace_shorter_than_the_window_is_all_zero_not_an_error():
    """A four-sample recording cannot support a five-point estimator. Returning
    zeros lets a caller proceed and find nothing, rather than raising from deep
    inside a daemon pass."""
    assert velocity(np.zeros((4, 2)), 500.0) == pytest.approx(np.zeros((4, 2)))
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_velocity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.eye.detect.velocity'`

- [ ] **Step 3: Implement**

```python
"""The one velocity estimator every detector shares.

**This is the most consequential preprocessing decision in the subsystem**
(design spec section 3). Every threshold-based method inherits its
differentiator, so seven private ones would make every between-detector
disagreement partly a disagreement about smoothing -- and the agreement metric
exists precisely to attribute disagreement to method. It is also what the
validity mask's speed criterion needs, so it exists before any detector runs.

The five-point weighted difference is Engbert & Kliegl's own estimator,
adopted here as the shared one because the baseline detector is calibrated
against it and because averaging over five samples suppresses the sample-level
noise a two-point difference amplifies.
"""

from __future__ import annotations

import numpy as np

# Samples on each side of the sample being estimated. The five-point estimator
# spans `[n-2, n+2]`, so the first and last two samples of any trace have no
# window.
_HALF_WINDOW = 2

# The denominator of Engbert & Kliegl's five-point weighted difference:
# `(x[n+2] + x[n+1] - x[n-1] - x[n-2]) / (6 * dt)`.
_WEIGHT_SUM = 6.0


def velocity(gaze_deg: np.ndarray, fs_hz: float) -> np.ndarray:
    """Degrees per second, per axis, one row per sample.

    **The two samples at each edge are zero, not extrapolated.** A fabricated
    edge velocity is indistinguishable from a real one to every detector
    downstream, and an event detected in the first two samples of a recording
    is an artifact of the estimator rather than of the eye. Stating zero makes
    that region quiet instead of wrong.
    """
    out = np.zeros_like(gaze_deg, dtype=float)
    if gaze_deg.shape[0] < 2 * _HALF_WINDOW + 1:
        return out
    interior = slice(_HALF_WINDOW, gaze_deg.shape[0] - _HALF_WINDOW)
    out[interior] = (
        gaze_deg[4:] + gaze_deg[3:-1] - gaze_deg[1:-3] - gaze_deg[:-4]
    ) * (fs_hz / _WEIGHT_SUM)
    return out
```

- [ ] **Step 4: Run, then mutation-check**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_velocity.py -v`
Expected: PASS.

With `PYTHONDONTWRITEBYTECODE=1`: change `_WEIGHT_SUM` to `2.0` and confirm the
ramp test fails; extrapolate the edges instead of zeroing them (assign
`out[:2] = out[2]`) and confirm the edge test fails; drop the `fs_hz` factor
and confirm the rate-scaling test fails.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/eye/detect/velocity.py tests/eye/detect/test_velocity.py
git commit -m "detect: one velocity estimator, shared by every detector"
```

---

### Task 3: Measuring an event, once, for every detector

**Files:** Create `wl_preproc/eye/detect/measure.py`; Test
`tests/eye/detect/test_measure.py`

**Interfaces:**
- Consumes: Task 2's `velocity`.
- Produces: `Measurement` (frozen dataclass: `amplitude_deg`,
  `peak_velocity_deg_s`, `duration_s`), `measure(gaze_deg, velocity_deg_s,
  start, stop, fs_hz) -> Measurement`, `MICROSACCADE_MAX_DEG = 1.0`,
  `classify(amplitude_deg, microsaccade_max_deg) -> Label`.

- [ ] **Step 1: Write the failing tests**

```python
import numpy as np
import pytest

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.measure import (
    MICROSACCADE_MAX_DEG, classify, measure,
)


def _ramp(n=20, fs_hz=500.0, vx=100.0, vy=0.0):
    t = np.arange(n) / fs_hz
    return np.column_stack([vx * t, vy * t])


def test_amplitude_is_the_displacement_across_the_interval():
    """Endpoint-to-endpoint, NOT path length: a saccade's amplitude is where
    the eye ended up relative to where it started, and path length would count
    any wobble on the way as extra amplitude."""
    gaze = np.column_stack([[0.0, 1.0, 3.0, 2.0, 4.0], np.zeros(5)])
    velocity = np.zeros((5, 2))

    result = measure(gaze, velocity, start=0, stop=5, fs_hz=500.0)

    assert result.amplitude_deg == pytest.approx(4.0)


def test_amplitude_is_euclidean_across_both_axes():
    gaze = np.array([[0.0, 0.0], [3.0, 4.0]])
    result = measure(gaze, np.zeros((2, 2)), start=0, stop=2, fs_hz=500.0)
    assert result.amplitude_deg == pytest.approx(5.0)


def test_peak_velocity_is_the_maximum_speed_inside_the_interval_only():
    """Bounded to the interval: a faster sample just outside it belongs to a
    different event, and letting it leak in would inflate the main sequence
    that vigor is measured against (design spec section 6.5)."""
    velocity = np.zeros((10, 2))
    velocity[2] = [50.0, 0.0]
    velocity[4] = [80.0, 60.0]      # speed 100, inside
    velocity[8] = [400.0, 0.0]      # outside

    result = measure(np.zeros((10, 2)), velocity, start=1, stop=6, fs_hz=500.0)

    assert result.peak_velocity_deg_s == pytest.approx(100.0)


def test_duration_counts_samples_not_endpoints():
    """`stop` is exclusive, so a 6-sample event at 500 Hz lasts 12 ms."""
    result = measure(np.zeros((20, 2)), np.zeros((20, 2)), start=4, stop=10, fs_hz=500.0)
    assert result.duration_s == pytest.approx(6 / 500.0)


def test_classify_splits_at_the_threshold_and_the_boundary_is_a_saccade():
    """At-or-above is a saccade. Stated because a boundary convention nobody
    writes down is one every reimplementation gets to choose differently."""
    assert classify(0.4, MICROSACCADE_MAX_DEG) is Label.MICROSACCADE
    assert classify(0.999, MICROSACCADE_MAX_DEG) is Label.MICROSACCADE
    assert classify(1.0, MICROSACCADE_MAX_DEG) is Label.SACCADE
    assert classify(12.0, MICROSACCADE_MAX_DEG) is Label.SACCADE


def test_the_threshold_default_is_the_conventional_one_degree():
    assert MICROSACCADE_MAX_DEG == 1.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_measure.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
"""Amplitude, peak velocity and duration -- computed once, for every detector.

**Detectors return intervals; this measures them** (design spec section 3).
All seven natively produce different things, and if each computed its own
amplitude the agreement metric would compare MEASUREMENTS as well as
detections, making a disagreement uninterpretable. Measuring here also means
the main sequence that vigor is fitted against is the same measurement
whichever detector found the saccade.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from wl_preproc.eye.detect.labels import Label

# The conventional microsaccade cut. A paramset overrides it; this is the
# default and the lab will want to move it -- amplitude distributions are
# continuous and the boundary is a convention, not a fact about the eye.
MICROSACCADE_MAX_DEG = 1.0


@dataclass(frozen=True, slots=True)
class Measurement:
    amplitude_deg: float
    peak_velocity_deg_s: float
    duration_s: float


def measure(
    gaze_deg: np.ndarray,
    velocity_deg_s: np.ndarray,
    start: int,
    stop: int,
    fs_hz: float,
) -> Measurement:
    """Measure one interval. `stop` is exclusive, matching `Run`.

    **Amplitude is endpoint-to-endpoint displacement, not path length.** A
    saccade's amplitude is where the eye ended up relative to where it began;
    path length would count post-saccadic wobble on the way as extra
    amplitude, which is exactly the contamination design spec section 6.5.3
    names as shifting the whole main sequence.

    **Peak velocity is bounded to the interval.** A faster sample just outside
    belongs to a different event.
    """
    displacement = gaze_deg[stop - 1] - gaze_deg[start]
    speed = np.hypot(velocity_deg_s[start:stop, 0], velocity_deg_s[start:stop, 1])
    return Measurement(
        amplitude_deg=float(np.hypot(displacement[0], displacement[1])),
        peak_velocity_deg_s=float(speed.max()) if speed.size else 0.0,
        duration_s=float(stop - start) / fs_hz,
    )


def classify(amplitude_deg: float, microsaccade_max_deg: float) -> Label:
    """`saccade` at or above the threshold, `microsaccade` below it.

    At-or-above is stated rather than left to the reader: a boundary
    convention nobody writes down is one every reimplementation gets to choose
    differently, and six of this subsystem's seven detectors are
    reimplementations.
    """
    return Label.MICROSACCADE if amplitude_deg < microsaccade_max_deg else Label.SACCADE
```

- [ ] **Step 4: Run, then mutation-check**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_measure.py -v`
Expected: PASS.

With `PYTHONDONTWRITEBYTECODE=1`: make amplitude the path length
(`np.sum(np.hypot(*np.diff(...)))`) and confirm the displacement test fails;
take `speed.max()` over the whole array rather than the interval and confirm
the peak-velocity test fails; flip `<` to `<=` in `classify` and confirm the
boundary test fails.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/eye/detect/measure.py tests/eye/detect/test_measure.py
git commit -m "detect: measure an event once, identically for every detector"
```

---

### Task 4: The validity mask, and its five criteria

**Files:** Create `wl_preproc/eye/detect/validity.py`; Test
`tests/eye/detect/test_validity.py`

**Interfaces:**
- Consumes: Task 1's `Label`, Task 2's `velocity`.
- Produces: `ValidityParams` (frozen dataclass: `region_half_width_deg`,
  `region_half_height_deg`, `max_speed_deg_s`, `dilate_samples`,
  `min_epoch_samples`), `DEFAULT_VALIDITY_PARAMS`, `validity_labels(gaze_deg,
  velocity_deg_s, data_quality, frame_gaps, params) -> np.ndarray`.

Returns an array of `Label.BLINK`, `Label.INVALID` or `None` — `None` meaning
"this sample is available for a detector to label". Detectors never overwrite
a non-`None` entry, which is how precedence is enforced structurally rather
than by convention.

- [ ] **Step 1: Write the failing tests**

```python
import numpy as np
import pytest

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.validity import (
    DEFAULT_VALIDITY_PARAMS, ValidityParams, validity_labels,
)

FS_HZ = 500.0
_QUIET = ValidityParams(
    region_half_width_deg=20.0, region_half_height_deg=15.0,
    max_speed_deg_s=1000.0, dilate_samples=0, min_epoch_samples=1,
)


def _clean(n):
    return np.zeros((n, 2)), np.zeros((n, 2)), np.full(n, 100.0), ()


def test_a_clean_recording_has_every_sample_available():
    gaze, vel, quality, gaps = _clean(50)
    assert all(label is None for label in validity_labels(gaze, vel, quality, gaps, _QUIET))


def test_data_quality_below_one_hundred_is_a_blink():
    """Criterion 1, and it reuses `EyeQuality`'s existing definition exactly --
    a second blink definition free to drift from the one the daily report
    already publishes is the defect this repository names most often."""
    gaze, vel, quality, gaps = _clean(10)
    quality[3:6] = 50.0
    quality[7] = 0.0

    labels = validity_labels(gaze, vel, quality, gaps, _QUIET)

    assert [labels[i] for i in (3, 4, 5, 7)] == [Label.BLINK] * 4
    assert labels[2] is None and labels[6] is None


def test_gaze_outside_the_plausible_region_is_invalid():
    gaze, vel, quality, gaps = _clean(10)
    gaze[4] = [25.0, 0.0]      # beyond 20 deg half-width
    gaze[6] = [0.0, -18.0]     # beyond 15 deg half-height

    labels = validity_labels(gaze, vel, quality, gaps, _QUIET)

    assert labels[4] is Label.INVALID and labels[6] is Label.INVALID
    assert labels[5] is None


def test_implausible_speed_is_invalid():
    gaze, vel, quality, gaps = _clean(10)
    vel[5] = [1200.0, 0.0]

    assert validity_labels(gaze, vel, quality, gaps, _QUIET)[5] is Label.INVALID


def test_a_frame_gap_invalidates_the_samples_either_side_of_it():
    """Criterion 4, and the reason `read_ohdpi` reports `frame_gaps` instead of
    refusing a recording: a velocity computed ACROSS a gap is a spurious
    saccade, so the samples whose estimate spans the discontinuity are the
    ones that must go."""
    from wl_preproc.eye.ohdpi import FrameGap

    gaze, vel, quality, _ = _clean(20)
    labels = validity_labels(gaze, vel, quality, (FrameGap(row=9, n_missing=3),), _QUIET)

    assert labels[9] is Label.INVALID and labels[10] is Label.INVALID
    assert labels[6] is None and labels[13] is None


def test_blink_wins_over_invalid_when_a_sample_qualifies_for_both():
    """Precedence, enforced where the labels are assigned rather than trusted
    to a downstream reader."""
    gaze, vel, quality, gaps = _clean(6)
    quality[2] = 0.0
    gaze[2] = [99.0, 99.0]

    assert validity_labels(gaze, vel, quality, gaps, _QUIET)[2] is Label.BLINK


def test_invalid_regions_are_dilated_by_the_stated_number_of_samples():
    """The notebook's fifth criterion. A tracking failure does not begin and
    end cleanly on the sample the tracker admits it."""
    params = ValidityParams(20.0, 15.0, 1000.0, dilate_samples=2, min_epoch_samples=1)
    gaze, vel, quality, gaps = _clean(20)
    quality[10] = 0.0

    labels = validity_labels(gaze, vel, quality, gaps, params)

    assert all(labels[i] is not None for i in range(8, 13))
    assert labels[7] is None and labels[13] is None


def test_a_valid_epoch_shorter_than_the_minimum_is_dropped():
    """Also the fifth criterion. Three valid samples between two blinks cannot
    support a detector and would produce edge artifacts if handed to one."""
    params = ValidityParams(20.0, 15.0, 1000.0, dilate_samples=0, min_epoch_samples=5)
    gaze, vel, quality, gaps = _clean(20)
    quality[0:8] = 0.0
    quality[11:20] = 0.0        # leaves a 3-sample valid epoch at 8..10

    labels = validity_labels(gaze, vel, quality, gaps, params)

    assert [labels[i] for i in (8, 9, 10)] == [Label.INVALID] * 3


def test_a_dropped_short_epoch_is_invalid_not_blink():
    """It was dropped for being short, not for a tracking failure, and the two
    reasons must not render identically."""
    params = ValidityParams(20.0, 15.0, 1000.0, dilate_samples=0, min_epoch_samples=5)
    gaze, vel, quality, gaps = _clean(12)
    quality[0:4] = 0.0
    quality[6:12] = 0.0

    labels = validity_labels(gaze, vel, quality, gaps, params)

    assert labels[4] is Label.INVALID and labels[0] is Label.BLINK


def test_the_defaults_are_stated_and_flagged_as_unmeasured():
    """Design spec section 11 open question 1: the region and speed ceiling
    have no measured value for this rig yet. Pinned so a later measurement is
    a visible change rather than a silent drift."""
    assert DEFAULT_VALIDITY_PARAMS.region_half_width_deg == 20.0
    assert DEFAULT_VALIDITY_PARAMS.max_speed_deg_s == 1000.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_validity.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
"""The validity mask -- OpenIrisDPI's own five criteria.

**None of them involves a detector** (design spec section 2), which is why
this is its own module and, downstream, its own table with its own paramset:
three detectors running against three different masks would make the agreement
metric compare masks as well as detections, measuring the thing it exists to
hold constant.

Returns `None` for a sample a detector may label, and a `Label` for one it may
not. Precedence is enforced HERE, by not offering the sample at all, rather
than by asking every detector to respect an ordering.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.ohdpi import FrameGap

# `EyeQuality`'s own `_FULL_TRACKING_QUALITY`, restated rather than imported --
# it is private there, and the value belongs to the frozen recording format
# rather than being a choice free to drift between the two places it is read.
#
# NECESSARY, NOT SUFFICIENT: OpenIrisDPI "does not determine when the image
# processing algorithm has failed", so a frame at 100 is one the tracker did
# not declare a failure on, which is not the same as one it tracked correctly.
_FULL_TRACKING_QUALITY = 100.0


@dataclass(frozen=True, slots=True)
class ValidityParams:
    region_half_width_deg: float
    region_half_height_deg: float
    max_speed_deg_s: float
    dilate_samples: int
    min_epoch_samples: int


# **These are placeholders with no measured basis on this rig** (design spec
# section 11, open question 1). 20 x 15 degrees is a generous screen; 1000
# deg/s is above any physiological saccade. Pinned by a test so that measuring
# them properly is a visible change rather than a silent drift.
DEFAULT_VALIDITY_PARAMS = ValidityParams(
    region_half_width_deg=20.0,
    region_half_height_deg=15.0,
    max_speed_deg_s=1000.0,
    dilate_samples=5,
    min_epoch_samples=10,
)


def validity_labels(
    gaze_deg: np.ndarray,
    velocity_deg_s: np.ndarray,
    data_quality: np.ndarray,
    frame_gaps: Sequence[FrameGap],
    params: ValidityParams,
) -> np.ndarray:
    """One entry per sample: a `Label` where the sample is unusable, `None`
    where a detector may label it."""
    n = gaze_deg.shape[0]
    blink = data_quality < _FULL_TRACKING_QUALITY

    outside = (np.abs(gaze_deg[:, 0]) > params.region_half_width_deg) | (
        np.abs(gaze_deg[:, 1]) > params.region_half_height_deg
    )
    too_fast = np.hypot(velocity_deg_s[:, 0], velocity_deg_s[:, 1]) > params.max_speed_deg_s

    # A gap sits between rows `row` and `row + 1`; both estimates span the
    # discontinuity, so both go. `read_ohdpi` reports gaps rather than refusing
    # the recording precisely so this can happen here (design spec section 2).
    across_gap = np.zeros(n, dtype=bool)
    for gap in frame_gaps:
        across_gap[max(gap.row, 0) : min(gap.row + 2, n)] = True

    unusable = blink | outside | too_fast | across_gap
    unusable = _dilate(unusable, params.dilate_samples)
    unusable |= _short_valid_epochs(unusable, params.min_epoch_samples)

    out = np.full(n, None, dtype=object)
    out[unusable] = Label.INVALID
    # Assigned LAST so it wins: a blink is a specific reason and `invalid` the
    # generic one, and generic-first would mean no sample is ever a blink.
    out[blink] = Label.BLINK
    return out


def _dilate(mask: np.ndarray, samples: int) -> np.ndarray:
    """Grow every `True` region by `samples` in each direction. A tracking
    failure does not begin and end cleanly on the sample the tracker admits
    it, which is the notebook's own reason for expanding invalid regions."""
    if samples <= 0 or not mask.any():
        return mask
    grown = mask.copy()
    for shift in range(1, samples + 1):
        grown[shift:] |= mask[:-shift]
        grown[:-shift] |= mask[shift:]
    return grown


def _short_valid_epochs(unusable: np.ndarray, minimum: int) -> np.ndarray:
    """Valid stretches too short to hand a detector. Returned as their own
    mask rather than folded into `unusable` in place, so the caller can see
    that these samples were dropped for being SHORT rather than for a tracking
    failure -- two different facts that must not render identically."""
    out = np.zeros_like(unusable)
    if minimum <= 1:
        return out
    start = None
    for index in range(len(unusable) + 1):
        inside = index < len(unusable) and not unusable[index]
        if inside and start is None:
            start = index
        elif not inside and start is not None:
            if index - start < minimum:
                out[start:index] = True
            start = None
    return out
```

- [ ] **Step 4: Run, then mutation-check**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_validity.py -v`
Expected: PASS.

With `PYTHONDONTWRITEBYTECODE=1`: assign `out[blink]` BEFORE `out[unusable]`
and confirm the precedence test fails; narrow the gap span to
`across_gap[gap.row] = True` alone and confirm the frame-gap test fails; make
`_dilate` a no-op and confirm the dilation test fails; make
`_short_valid_epochs` return zeros and confirm the short-epoch test fails.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/eye/detect/validity.py tests/eye/detect/test_validity.py
git commit -m "detect: the validity mask, and its five criteria"
```

---

### Task 5: Engbert–Kliegl, and the registry it registers into

**Files:** Create `wl_preproc/eye/detect/engbert_kliegl.py`,
`wl_preproc/eye/detect/registry.py`; Test
`tests/eye/detect/test_engbert_kliegl.py`, `tests/eye/detect/test_registry.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `EngbertKlieglParams` (frozen: `lambda_`, `min_duration_samples`),
  `DEFAULT_EK_PARAMS`, `detect_engbert_kliegl(gaze_deg, velocity_deg_s,
  available, params) -> list[tuple[int, int]]`; and in `registry.py`:
  `Detector` (frozen: `name`, `vocabulary`, `run`), `DETECTORS: dict[str,
  Detector]`, `DetectorNotRegistered`.

`available` is Task 4's mask with `None` meaning usable; detectors never look
at a sample that is not `None` there.

- [ ] **Step 1: Write the failing tests**

```python
import numpy as np
import pytest

from wl_preproc.eye.detect.engbert_kliegl import (
    DEFAULT_EK_PARAMS, EngbertKlieglParams, detect_engbert_kliegl,
)

FS_HZ = 500.0


def _trace_with_saccades(onsets, amplitude_deg=8.0, n=2000, dur=10, seed=3):
    """A still eye with tiny noise, stepped by `amplitude_deg` over `dur`
    samples at each onset. Returns `(gaze, planted_intervals)`.

    Each step is applied to everything from its onset onward, then the ramp
    region is pulled back linearly -- so the gaze is flat, ramps once per
    onset, and stays at its new level. Written this way rather than by
    accumulating an offset because an off-by-one in the accumulation would
    plant saccades at times the test then "confirms".
    """
    rng = np.random.default_rng(seed)
    gaze = rng.normal(0.0, 0.01, (n, 2))
    planted = []
    for onset in onsets:
        gaze[onset:, 0] += amplitude_deg
        gaze[onset : onset + dur, 0] -= amplitude_deg * (1.0 - np.linspace(0.0, 1.0, dur))
        planted.append((onset, onset + dur))
    return gaze, planted


def test_it_finds_planted_saccades_at_their_planted_times():
    """NOT 'some events were found'. The eye plan shipped a suite where gutting
    the whole session-time-to-row alignment left every test green because
    nothing asserted a fitted result was numerically right; a detector suite
    that only counts events has the same hole."""
    from wl_preproc.eye.detect.velocity import velocity

    gaze, planted = _trace_with_saccades([300, 800, 1400])
    available = np.full(len(gaze), None, dtype=object)

    found = detect_engbert_kliegl(gaze, velocity(gaze, FS_HZ), available, DEFAULT_EK_PARAMS)

    assert len(found) == len(planted)
    for (got_start, got_stop), (want_start, want_stop) in zip(found, planted, strict=True):
        assert abs(got_start - want_start) <= 3
        assert abs(got_stop - want_stop) <= 3


def test_a_still_eye_yields_nothing():
    """The false-positive floor. A detector that fires on noise makes every
    downstream agreement number meaningless."""
    from wl_preproc.eye.detect.velocity import velocity

    rng = np.random.default_rng(11)
    gaze = rng.normal(0.0, 0.01, (2000, 2))
    available = np.full(2000, None, dtype=object)

    assert detect_engbert_kliegl(gaze, velocity(gaze, FS_HZ), available, DEFAULT_EK_PARAMS) == []


def test_events_shorter_than_the_minimum_duration_are_rejected():
    from wl_preproc.eye.detect.velocity import velocity

    gaze, _ = _trace_with_saccades([500], dur=2)
    available = np.full(len(gaze), None, dtype=object)
    strict = EngbertKlieglParams(lambda_=6.0, min_duration_samples=6)

    assert detect_engbert_kliegl(gaze, velocity(gaze, FS_HZ), available, strict) == []


def test_a_higher_lambda_finds_fewer_events():
    """Pins that lambda is actually consulted. A hardcoded threshold passes
    every test above."""
    from wl_preproc.eye.detect.velocity import velocity

    gaze, _ = _trace_with_saccades([300, 800, 1400], amplitude_deg=0.4)
    available = np.full(len(gaze), None, dtype=object)
    v = velocity(gaze, FS_HZ)

    lenient = detect_engbert_kliegl(gaze, v, available, EngbertKlieglParams(3.0, 6))
    strict = detect_engbert_kliegl(gaze, v, available, EngbertKlieglParams(30.0, 6))

    assert len(lenient) > len(strict)


def test_unavailable_samples_are_never_part_of_an_event():
    """A detector must not label a sample the mask has already claimed --
    precedence is structural, not a convention each detector honours."""
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.velocity import velocity

    gaze, planted = _trace_with_saccades([300, 800])
    available = np.full(len(gaze), None, dtype=object)
    available[295:320] = Label.BLINK

    found = detect_engbert_kliegl(gaze, velocity(gaze, FS_HZ), available, DEFAULT_EK_PARAMS)

    assert all(not (start < 320 and stop > 295) for start, stop in found)


def test_the_defaults_are_the_papers_conventional_values():
    assert DEFAULT_EK_PARAMS.lambda_ == 6.0
    assert DEFAULT_EK_PARAMS.min_duration_samples == 6
```

And `tests/eye/detect/test_registry.py`:

```python
import pytest

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.registry import DETECTORS, DetectorNotRegistered, get_detector


def test_engbert_kliegl_is_registered_and_declares_its_vocabulary():
    """A detector that cannot emit `pso` is not DISAGREEING with one that can;
    it has nothing to say. Stage 2's comparisons need this declared."""
    detector = get_detector("engbert_kliegl")

    assert detector.name == "engbert_kliegl"
    assert detector.vocabulary == frozenset({Label.SACCADE, Label.MICROSACCADE})


def test_an_unregistered_name_is_refused_by_name():
    with pytest.raises(DetectorNotRegistered, match="uneye"):
        get_detector("uneye")


def test_every_registered_vocabulary_is_a_subset_of_the_label_enum():
    """A detector declaring a label the schema cannot store is a silent insert
    failure on whichever session first reaches it."""
    for detector in DETECTORS.values():
        assert detector.vocabulary <= frozenset(Label)


def test_no_detector_claims_a_mask_owned_label():
    """`blink` and `invalid` come from the validity mask, never from a
    detector. A detector claiming them would let two sources write one fact."""
    for detector in DETECTORS.values():
        assert not (detector.vocabulary & {Label.BLINK, Label.INVALID})
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`wl_preproc/eye/detect/engbert_kliegl.py`:

```python
"""Engbert & Kliegl's velocity-threshold detector -- the always-on baseline.

Reimplemented from the published algorithm rather than vendored (design spec
section 3.2): it is small, fully specified, and reimplementing means it shares
this subsystem's one velocity estimator instead of bringing a private one.

The threshold is a MEDIAN-based estimate of the velocity distribution's scale,
not a standard deviation: a real standard deviation is inflated by the very
saccades the threshold is meant to find, so the detector would grow less
sensitive exactly as a session contained more of what it looks for.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# lambda = 6 and a 6-sample minimum (12 ms at 500 Hz) are the paper's own
# conventional values, and what nearly every reimplementation uses.
DEFAULT_LAMBDA = 6.0
DEFAULT_MIN_DURATION_SAMPLES = 6


@dataclass(frozen=True, slots=True)
class EngbertKlieglParams:
    lambda_: float
    min_duration_samples: int


DEFAULT_EK_PARAMS = EngbertKlieglParams(
    lambda_=DEFAULT_LAMBDA, min_duration_samples=DEFAULT_MIN_DURATION_SAMPLES
)


def _median_scale(component: np.ndarray) -> float:
    """`sqrt(median(v^2) - median(v)^2)`, the paper's own scale estimate."""
    if component.size == 0:
        return 0.0
    variance = float(np.median(component**2) - np.median(component) ** 2)
    return float(np.sqrt(variance)) if variance > 0 else 0.0


def detect_engbert_kliegl(
    gaze_deg: np.ndarray,
    velocity_deg_s: np.ndarray,
    available: np.ndarray,
    params: EngbertKlieglParams,
) -> list[tuple[int, int]]:
    """Half-open `(start, stop)` intervals, in sample indices.

    `available` is the validity mask: `None` where a detector may label, a
    `Label` where the mask has already claimed the sample. **Unavailable
    samples are excluded from the threshold estimate as well as from the
    output** -- a blink's velocity spike would otherwise inflate the scale and
    desensitise the detector for the whole recording.
    """
    usable = np.array([entry is None for entry in available], dtype=bool)
    if not usable.any():
        return []

    eta_x = params.lambda_ * _median_scale(velocity_deg_s[usable, 0])
    eta_y = params.lambda_ * _median_scale(velocity_deg_s[usable, 1])
    if eta_x <= 0 or eta_y <= 0:
        return []

    # The paper's elliptic test: a sample is in an event when the velocity
    # vector lies outside the ellipse whose semi-axes are the two thresholds.
    outside = (velocity_deg_s[:, 0] / eta_x) ** 2 + (
        velocity_deg_s[:, 1] / eta_y
    ) ** 2 > 1.0
    outside &= usable

    return [
        (start, stop)
        for start, stop in _true_runs(outside)
        if stop - start >= params.min_duration_samples
    ]


def _true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Maximal `True` stretches as half-open intervals."""
    padded = np.concatenate(([False], mask, [False]))
    edges = np.diff(padded.astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True))
```

`wl_preproc/eye/detect/registry.py`:

```python
"""The detector registry, and each detector's declared vocabulary.

Follows `timebase/extract.py::EXTRACTORS`' precedent: a dict whose set
equality against the registered paramsets is this subsystem's completeness
claim.

**Vocabulary is declared, not inferred.** Detectors emit between one and four
label classes (design spec section 3.1), and a detector that cannot emit `pso`
is not disagreeing with one that can -- it has nothing to say. Stage 2's
comparisons are computed in the coarsest vocabulary both sides declare, and
this is where that declaration lives.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from wl_preproc.eye.detect.engbert_kliegl import detect_engbert_kliegl
from wl_preproc.eye.detect.labels import Label


class DetectorNotRegistered(KeyError):
    """No detector of that name."""


@dataclass(frozen=True, slots=True)
class Detector:
    name: str
    # `blink` and `invalid` are NEVER in a vocabulary: they come from the
    # validity mask, and a detector claiming them would let two sources write
    # one fact.
    vocabulary: frozenset[Label]
    run: Callable


DETECTORS: dict[str, Detector] = {
    "engbert_kliegl": Detector(
        name="engbert_kliegl",
        vocabulary=frozenset({Label.SACCADE, Label.MICROSACCADE}),
        run=detect_engbert_kliegl,
    ),
}


def get_detector(name: str) -> Detector:
    try:
        return DETECTORS[name]
    except KeyError as exc:
        raise DetectorNotRegistered(
            f"{name!r} is not a registered detector; have "
            f"{sorted(DETECTORS)}"
        ) from exc
```

- [ ] **Step 4: Run, then mutation-check**

Run: `.venv/bin/python -m pytest tests/eye/detect/ -v`
Expected: PASS.

With `PYTHONDONTWRITEBYTECODE=1`: replace `_median_scale` with `np.std` and
confirm the planted-saccade test still passes but the still-eye test does not
degrade — **report what you actually observe here rather than assuming**, and
if both survive, that is a gap in these tests worth naming in the commit
rather than hiding; drop `outside &= usable` and confirm the
unavailable-samples test fails; ignore `params.lambda_` and confirm the
higher-lambda test fails; drop the `min_duration_samples` filter and confirm
the short-event test fails.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/eye/detect/ tests/eye/detect/
git commit -m "detect: engbert-kliegl, and the registry that declares vocabularies"
```

---

### Task 6: The schema — `EyeValidity`, `EyeDetection`, and runs as rows

**Files:** Create `wl_preproc/schema/detect.py`; Test
`tests/schema/test_detect_schema.py`

**Interfaces:**
- Consumes: Task 1's `Label`, Task 4's `ValidityParams`.
- Produces: `EyeValidity` (`dj.Computed`) with `.Run` part; `EyeDetection`
  (`dj.Computed`) with `.Run` part; `activate(prefix)`.

This task declares the tables and their `activate`; `make()` is Task 7, so
both ship with an empty `key_source` in the interim — the pattern the eye
subsystem's own Task 9 used, and whose cost that module records: a
`dj.Computed` with no `make()` and a non-empty `key_source` raises on every
landed session the moment it joins `_computed_tables()`.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def schemas(dj_conn, prefix):
    from wl_preproc.schema import detect

    detect.activate(prefix=prefix)
    return detect


def test_no_bare_longblob(schemas):
    """The repo-wide sweep covers this, but state it here: under DataJoint 2.x
    a bare longblob stores a numpy array as its truncated string repr --
    measured, 31,488 float32 values became 488 bytes, unrecoverable. This
    subsystem is the first to store a derived array, so it is the first that
    could reintroduce it."""
    assert "longblob" not in schemas.EyeValidity.definition
    assert "longblob" not in schemas.EyeValidity.Run.definition
    assert "longblob" not in schemas.EyeDetection.definition
    assert "longblob" not in schemas.EyeDetection.Run.definition


def test_the_label_enum_declares_all_eight_values(schemas, enum_values):
    """Complete from the start even though stage 1 produces five. Adding a
    value later is a schema change and the migration window closes January
    2027."""
    from wl_preproc.eye.detect.labels import Label

    attr = schemas.EyeDetection.Run.heading.attributes["label"]
    assert enum_values(attr.type) == {label.value for label in Label}


def test_the_detection_key_carries_both_paramsets(schemas):
    """Two traces are comparable only if masked identically, so which mask was
    used belongs in the key (design spec section 2)."""
    key = schemas.EyeDetection.primary_key
    assert "validity_paramset_idx" in key
    assert "paramset_idx" in key


def test_trace_is_not_called_eye_and_admits_a_conjunction(schemas, enum_values):
    """A conjunction is honestly not an eye."""
    attr = schemas.EyeDetection.heading.attributes["trace"]
    assert enum_values(attr.type) == {"left", "right", "conjunction"}
    assert "eye" not in schemas.EyeDetection.primary_key


def test_validity_is_keyed_per_real_eye_not_per_trace(schemas, enum_values):
    """The mask is a property of one eye's recording; there is no conjunction
    of masks."""
    attr = schemas.EyeValidity.heading.attributes["eye"]
    assert enum_values(attr.type) == {"left", "right"}


def test_a_run_row_carries_its_measurements_nullably(schemas):
    """A saccade run IS an event, so it carries amplitude and peak velocity;
    fixation, blink and invalid runs leave them null."""
    for name in ("amplitude_deg", "peak_velocity_deg_s", "reliability"):
        assert schemas.EyeDetection.Run.heading.attributes[name].nullable


def test_both_tables_are_daemon_stages():
    """`TrialCoverage` was missing from `_computed_tables()` for a whole phase
    and silently returned tier D for every session."""
    from wl_preproc import daemon

    names = {table.__name__ for table in daemon._computed_tables()}
    assert {"EyeValidity", "EyeDetection"} <= names


def test_a_refusal_is_expressible(schemas):
    """A session whose calibration was refused has no gaze, so detection is
    refused too -- with a reason, never an error and never a fabricated run."""
    for table in (schemas.EyeValidity, schemas.EyeDetection):
        assert table.heading.attributes["status"] is not None
        assert table.heading.attributes["reason"] is not None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.schema.detect'`

- [ ] **Step 3: Implement**

```python
# wl_preproc/schema/detect.py
"""The validity mask and detected events, stored as runs.

Design spec `docs/superpowers/specs/2026-08-31-saccade-detection-design.md`.

**The first stored derived array in this pipeline, and it is not a blob.** A
per-sample label trace is piecewise constant, so it is stored as maximal runs
in rows: the same information losslessly, the guardrail satisfied by
construction rather than by a round-trip test someone must remember, "total
microsaccade time this month" as a WHERE clause, and a tiling invariant a blob
cannot have.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.eye.detect.labels import Label
from wl_preproc.schema import DEFAULT_PREFIX, core, paramset, pipeline

schema = dj.Schema()

_LABEL_ENUM = ",".join(f"'{label.value}'" for label in Label)


@schema
class EyeValidity(dj.Computed):
    definition = f"""
    # Which samples are usable, per eye. Design spec section 2.
    # Key: (subject, session_datetime, eye, validity paramset).
    -> pipeline.Session
    eye : enum('left','right')
    -> paramset.ParamSet.proj(validity_paramset_idx='paramset_idx')
    ---
    # Read this before any column below: a refused mask has no runs and a
    # stated reason, exactly as a refused calibration has no map.
    status : enum('computed','refused')
    n_samples=null : int unsigned
    # Per-criterion bookkeeping, so a mask that rejects most of a session says
    # WHICH criterion did it rather than only that something did.
    frac_blink=null       : double
    frac_out_of_region=null : double
    frac_too_fast=null    : double
    frac_frame_gap=null   : double
    frac_short_epoch=null : double
    reason='' : varchar(255)
    """

    class Run(dj.Part):
        definition = f"""
        # One maximal stretch of a single mask label. `run_stop` is EXCLUSIVE.
        -> master
        run_index : int unsigned
        ---
        run_start : int unsigned
        run_stop  : int unsigned
        label     : enum({_LABEL_ENUM})
        """

    @property
    def key_source(self):
        """Empty until Task 7 gives this table a `make()`.

        Deliberate, and the eye subsystem records the cost of the alternative:
        a `dj.Computed` with a real `key_source` and no `make()` raises on
        every already-landed session the moment it joins
        `daemon._computed_tables()`.
        """
        return pipeline.Session & "FALSE"


@schema
class EyeDetection(dj.Computed):
    definition = f"""
    # Detected events as a label trace, per trace and per detector.
    # Key: (subject, session_datetime, trace, validity paramset, paramset).
    -> pipeline.Session
    # `trace`, not `eye`: a conjunction is honestly not an eye.
    trace : enum('left','right','conjunction')
    -> paramset.ParamSet.proj(validity_paramset_idx='paramset_idx')
    -> paramset.ParamSet
    ---
    status : enum('computed','refused')
    n_samples=null       : int unsigned
    n_saccades=null      : int unsigned
    n_microsaccades=null : int unsigned
    reason='' : varchar(255)
    """

    class Run(dj.Part):
        definition = f"""
        # One maximal stretch of a single label. `run_stop` is EXCLUSIVE, and
        # the runs of one master row tile [0, n_samples) exactly.
        -> master
        run_index : int unsigned
        ---
        run_start : int unsigned
        run_stop  : int unsigned
        label     : enum({_LABEL_ENUM})
        # A saccade or microsaccade run IS an event, so it carries its own
        # measurements; every other label leaves them NULL. `reliability` is
        # Otero-Millan's per-detection index, null for every detector that has
        # none -- declared now because the migration window closes January.
        amplitude_deg=null       : double
        peak_velocity_deg_s=null : double
        reliability=null         : double
        """

    @property
    def key_source(self):
        """Empty until Task 7. See `EyeValidity.key_source`."""
        return pipeline.Session & "FALSE"


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}detect`. Idempotent."""
    core.activate(prefix=prefix)
    paramset.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}detect", create_tables=True)
```

Then register both in `wl_preproc/daemon.py::_computed_tables()`, after the
eye tables — `EyeValidity` before `EyeDetection`, since the ordering in that
list IS the dependency ordering and nothing else enforces it.

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_schema.py tests/schema/test_daemon.py tests/schema/test_guardrails.py -v`
Expected: PASS. The guardrail suite must pass unchanged — it is the sweep that
would catch a bare `longblob` here.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/schema/detect.py wl_preproc/daemon.py tests/schema/test_detect_schema.py
git commit -m "schema: the validity mask and detected events, stored as runs"
```

---

### Task 7: `make()`, the conjunction, and the paramsets

**Files:** Modify `wl_preproc/schema/detect.py`; Test
`tests/schema/test_detect_populate.py`

**Interfaces:**
- Consumes: everything above.
- Produces: real `key_source` and `make()` on both tables;
  `register_default_paramsets() -> dict[str, int]`.

- [ ] **Step 1: Write the failing tests**

Reuse `tests/schema/test_eye_populate.py`'s own fixture machinery — `_land`,
`_held_gaze_recipe`, `_held_gaze_session` — which already generate a session
whose gaze is HELD at stated raw positions. A session that holds, steps, holds
is a planted saccade with a known onset.

```python
def test_a_planted_step_is_detected_at_its_planted_time(stepped_session):
    """The round-trip that matters. `SessionRecipe.eye_fixations` holds gaze at
    stated positions, so a hold-step-hold session has ground-truth onsets, and
    a detector that found events at the wrong times passes every count-based
    test and fails this one."""
    from wl_preproc.schema import detect

    session_key, _report, planted_onsets = stepped_session
    runs = (detect.EyeDetection.Run & {**session_key, "trace": "left"}).to_dicts(
        order_by="run_index"
    )
    onsets = [r["run_start"] for r in runs if r["label"] in ("saccade", "microsaccade")]

    assert len(onsets) == len(planted_onsets)
    for got, want in zip(onsets, planted_onsets, strict=True):
        assert abs(got - want) <= 5


def test_the_runs_tile_the_whole_trace(stepped_session):
    """The structural invariant, asserted on real populated rows and not only
    in the encoder's unit tests."""
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session
    row = (detect.EyeDetection & {**session_key, "trace": "left"}).fetch1()
    runs = (detect.EyeDetection.Run & {**session_key, "trace": "left"}).to_dicts(
        order_by="run_index"
    )

    assert runs[0]["run_start"] == 0
    assert runs[-1]["run_stop"] == row["n_samples"]
    for earlier, later in zip(runs, runs[1:], strict=False):
        assert earlier["run_stop"] == later["run_start"]
        assert earlier["label"] != later["label"]


def test_saccade_runs_carry_measurements_and_others_do_not(stepped_session):
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session
    for run in (detect.EyeDetection.Run & {**session_key, "trace": "left"}).to_dicts():
        if run["label"] in ("saccade", "microsaccade"):
            assert run["amplitude_deg"] is not None
            assert run["peak_velocity_deg_s"] is not None
        else:
            assert run["amplitude_deg"] is None


def test_the_conjunction_requires_temporal_overlap_in_both_eyes(stepped_session):
    """Engbert-Kliegl's own noise suppression, applied uniformly. An event in
    one eye alone is not in the conjunction."""
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session
    def saccade_spans(trace):
        return [
            (r["run_start"], r["run_stop"])
            for r in (detect.EyeDetection.Run & {**session_key, "trace": trace}).to_dicts()
            if r["label"] in ("saccade", "microsaccade")
        ]

    both = saccade_spans("conjunction")
    left = saccade_spans("left")
    assert both
    for start, stop in both:
        assert any(ls < stop and start < lstop for ls, lstop in left)


def test_a_session_with_no_calibration_is_refused_with_a_reason(uncalibrated_session):
    """Detection reads gaze as a computation, so no calibration means no gaze.
    A refused row with a stated reason, never an error and never an empty
    success."""
    from wl_preproc.schema import detect

    session_key, report = uncalibrated_session
    rows = (detect.EyeDetection & session_key).to_dicts()

    assert rows
    for row in rows:
        assert row["status"] == "refused"
        assert "calibration" in row["reason"]
        assert row["n_samples"] is None


def test_the_registered_paramsets_match_the_detector_registry():
    """The completeness claim, in the shape `EXTRACTORS` already uses. A
    detector with no paramset never runs; a paramset with no detector fails on
    the session that reaches it."""
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import detect

    registered = detect.register_default_paramsets()
    assert set(registered) == set(DETECTORS)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py -v`
Expected: FAIL — no `make()`, so no rows.

- [ ] **Step 3: Implement**

Replace both `key_source` stubs and add `make()` to each.

```python
    # --- EyeValidity ------------------------------------------------------
    @property
    def key_source(self):
        """Landed sessions whose ohDPI recording `core.Segment` aligned, times
        the registered `eye_validity` paramsets.

        The FINE-grained `core.Segment` check, like `EyeQuality`'s: with no
        aligned recording there is no sample to mask and no row this table
        could honestly write. A session whose CALIBRATION is unusable still
        reaches `make()` and gets a refused row -- that is a different fact,
        and it is the one `EyeDetection` needs to be able to report.
        """
        from wl_preproc.schema import ingest

        return (
            pipeline.Session
            & ingest.Ingestion
            & (core.Segment & '`system` = "ohdpi"')
        ) * (paramset.ParamSet & {"paramset_type": "eye_validity"}).proj(
            validity_paramset_idx="paramset_idx"
        )

    def make(self, key: dict) -> None:
        """Both eyes' mask for one session and one paramset."""
        from wl_preproc.eye.detect.validity import ValidityParams, validity_labels
        from wl_preproc.eye.detect.velocity import velocity
        from wl_preproc.eye.gaze import gaze_trace
        from wl_preproc.eye.ohdpi import read_columns, read_ohdpi
        from wl_preproc.schema import eye as eye_schema, ingest

        session_key = {k: key[k] for k in pipeline.Session.primary_key}
        params = ValidityParams(**(paramset.ParamSet & {
            "paramset_idx": key["validity_paramset_idx"]
        }).fetch1("params"))
        session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
        segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
        path = session_dir / "ohdpi" / segment["file_path"]

        for eye_value, file_eye in (("left", "Left"), ("right", "Right")):
            calibration = (eye_schema.EyeCalibration & {**session_key, "eye": eye_value})
            row = {**key, "eye": eye_value}
            map_ = eye_schema._map_from_row(calibration.fetch1()) if calibration else None
            if map_ is None:
                # Three of the five criteria need degrees, so without a
                # calibration there is no mask -- refused with a reason,
                # never a mask built from raw pixels pretending to be one.
                self.insert1({**row, "status": "refused", "reason":
                              "no usable calibration, so gaze is undefined"})
                continue

            gaze = gaze_trace(path, file_eye, map_)
            quality = read_columns(path, [f"{file_eye}DataQuality"])[f"{file_eye}DataQuality"]
            labels = validity_labels(
                gaze, velocity(gaze, read_ohdpi(path).fs_hz), quality,
                read_ohdpi(path).frame_gaps, params,
            )
            runs = runs_from_labels(np.where(labels == None, Label.FIXATION, labels))  # noqa: E711
            self.insert1({
                **row, "status": "computed", "n_samples": len(labels),
                "frac_blink": float(np.mean(labels == Label.BLINK)),
                "frac_out_of_region": None, "frac_too_fast": None,
                "frac_frame_gap": None, "frac_short_epoch": None,
                "reason": "",
            })
            self.Run.insert(
                {**row, "run_index": index, "run_start": run.start,
                 "run_stop": run.stop, "label": run.label.value}
                for index, run in enumerate(runs)
            )
```

`validity_labels` returns `None` for a usable sample; the `np.where` above
turns those into `FIXATION` **only for storage**, because the mask's own runs
must still tile. A detector reads the mask back and treats `fixation` there as
"available".

```python
    # --- EyeDetection -----------------------------------------------------
    @property
    def key_source(self):
        """Every validity row -- INCLUDING refused ones -- times the
        `eye_detection` paramsets.

        Refused rows are included deliberately: a session whose calibration
        failed must still produce a detection row saying so. Excluding them
        would make "no calibration" and "detector never ran" render
        identically, which is the distinction this table exists to keep.
        """
        return EyeValidity * (paramset.ParamSet & {"paramset_type": "eye_detection"})

    def make(self, key: dict) -> None:
        """All three traces for one session, one mask and one detector."""
        from wl_preproc.eye.detect.measure import classify, measure
        from wl_preproc.eye.detect.registry import get_detector
        from wl_preproc.eye.detect.velocity import velocity
        from wl_preproc.eye.gaze import gaze_trace
        from wl_preproc.eye.ohdpi import read_ohdpi
        from wl_preproc.schema import eye as eye_schema, ingest

        session_key = {k: key[k] for k in pipeline.Session.primary_key}
        validity_key = {**session_key, "validity_paramset_idx": key["validity_paramset_idx"]}
        params = (paramset.ParamSet & {"paramset_idx": key["paramset_idx"]}).fetch1("params")
        detector = get_detector(params["detector"])

        refused = (EyeValidity & validity_key & 'status = "refused"')
        if refused:
            self.insert(
                {**key, "trace": trace, "status": "refused",
                 "reason": (refused.fetch("reason")[0] or "validity refused")}
                for trace in ("left", "right", "conjunction")
            )
            return

        session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
        segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
        path = session_dir / "ohdpi" / segment["file_path"]
        fs_hz = read_ohdpi(path).fs_hz

        spans: dict[str, list[tuple[int, int]]] = {}
        per_eye: dict[str, tuple] = {}
        for eye_value, file_eye in (("left", "Left"), ("right", "Right")):
            map_ = eye_schema._map_from_row(
                (eye_schema.EyeCalibration & {**session_key, "eye": eye_value}).fetch1()
            )
            gaze = gaze_trace(path, file_eye, map_)
            v = velocity(gaze, fs_hz)
            available = labels_from_runs(
                [Run(r["run_start"], r["run_stop"], Label(r["label"]))
                 for r in (EyeValidity.Run & {**validity_key, "eye": eye_value}).to_dicts(
                     order_by="run_index")],
                len(gaze),
            )
            # The mask stored `fixation` where a sample is available.
            offered = np.where(available == Label.FIXATION, None, available)
            spans[eye_value] = detector.run(gaze, v, offered, _params_for(detector, params))
            per_eye[eye_value] = (gaze, v, offered)

        spans["conjunction"] = _overlapping(spans["left"], spans["right"])

        for trace in ("left", "right", "conjunction"):
            gaze, v, offered = per_eye["left" if trace == "conjunction" else trace]
            self._insert_trace(key, trace, gaze, v, offered, spans[trace], fs_hz, params)
```

```python
def _overlapping(left, right):
    """Spans present in BOTH eyes with temporal overlap -- Engbert & Kliegl's
    own binocular criterion, applied uniformly to every detector. The
    intersection, never the union: an event in one eye alone is noise, which is
    the whole point of the criterion."""
    return [
        (max(ls, rs), min(lstop, rstop))
        for ls, lstop in left
        for rs, rstop in right
        if ls < rstop and rs < lstop
    ]
```

`_insert_trace` builds the label array by starting from the mask, writing
`classify(measure(...).amplitude_deg, ...)` over each span, encoding with
`runs_from_labels`, and inserting the master row plus its runs with
`amplitude_deg`/`peak_velocity_deg_s` set on event runs only.

```python
def register_default_paramsets() -> dict[str, int]:
    """One `eye_validity` paramset and one `eye_detection` paramset per
    registered detector, returned by detector name.

    Set equality against `DETECTORS` is this subsystem's completeness claim,
    in the shape `EXTRACTORS` already uses: a detector with no paramset never
    runs, and a paramset naming no detector fails on the first session that
    reaches it.
    """
    from dataclasses import asdict

    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS

    paramset.register("eye_validity", asdict(DEFAULT_VALIDITY_PARAMS))
    defaults = {"engbert_kliegl": asdict(DEFAULT_EK_PARAMS)}
    return {
        name: paramset.register("eye_detection", {"detector": name, **defaults[name]})
        for name in DETECTORS
    }
```

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 1027 baseline plus this plan's additions, 1 skipped, 1 deselected.

Then mutation-check with `PYTHONDONTWRITEBYTECODE=1`: make the conjunction the
UNION rather than the intersection and confirm the overlap test fails; drop
the `classify` call so every event is a `saccade` and confirm a microsaccade
assertion fails; return runs that stop one sample short and confirm the tiling
test fails.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/schema/detect.py tests/schema/test_detect_populate.py
git commit -m "detect: populate the mask and the trace, and derive the conjunction"
```

---

### Task 8: Measure what the spec only estimated

**Files:** Test `tests/schema/test_detect_populate.py` (add); Modify
`docs/superpowers/specs/2026-08-31-saccade-detection-design.md`

The spec's §5 says plainly that ~294,000 rows per session is **extrapolated
from typical saccade rates, not measured**, and that the plan must measure it
before the storage argument is trusted. This is that task. A design whose
storage argument rests on an unmeasured number is exactly the shape of defect
this project's checkpoint records repeatedly.

- [ ] **Step 1: Add a test that reports the real figure**

```python
def test_the_run_count_per_session_is_measured_not_assumed(stepped_session, capsys):
    """Design spec section 5 flags its own ~294,000-rows-per-session figure as
    an ESTIMATE. This measures the real one on a generated session and prints
    it, in the same spirit as the decode-reliability table the timebase suite
    prints on every run.

    Asserted only against a generous ceiling: the point is to SURFACE the
    number, and a tight assertion on a figure nobody has measured before would
    be inventing precision.
    """
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session
    n_runs = len(detect.EyeDetection.Run & session_key)
    n_samples = (detect.EyeDetection & {**session_key, "trace": "left"}).fetch1("n_samples")

    with capsys.disabled():
        print(f"\n  runs per session: {n_runs} over {n_samples} samples per trace")

    assert n_runs < 50 * n_samples / 100
```

- [ ] **Step 2: Run it and read the number**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py -k run_count -s -v`

- [ ] **Step 3: Correct the spec with the measured figure**

Replace §5's estimate with the measured number, scaled to a 1,177,799-sample
recording, and strike the "must measure it" sentence — recording what it was
measured on. If the real figure is far from ~294,000, say so and say what
follows for the storage argument. **Do not quietly adjust the estimate to
match; state that it was an estimate and what the measurement showed.**

- [ ] **Step 4: Commit**

```bash
git add tests/schema/test_detect_populate.py docs/superpowers/specs/2026-08-31-saccade-detection-design.md
git commit -m "detect: measure the run count the spec only estimated"
```

---

### Task 9: The report line, and closing the stage

**Files:** Modify `wl_preproc/cli/report.py`; Test
`tests/cli/test_detect_report.py`

**Interfaces:** Consumes `EyeValidity` and `EyeDetection`.

- [ ] **Step 1: Write the failing tests**

```python
import datetime

from wl_preproc.cli.report import build_report


def test_the_detection_section_reports_counts_per_session(detect_rows, tmp_path, prefix):
    section = _section(build_report(tmp_path, prefix=prefix), "Detection")

    line = _line_for(section, "detn0001")
    assert "saccades" in line and "microsaccades" in line


def test_two_distinct_refusal_reasons_render_as_two_distinct_lines(
    refused_rows, tmp_path, prefix
):
    """Controller ruling B's shape, carried over from the Eye section: a
    session with no detection is a first-class outcome with a STATED reason,
    and two different reasons must never collapse into one count."""
    section = _section(build_report(tmp_path, prefix=prefix), "Detection")

    assert "no usable calibration" in section
    assert "no ohDPI recording" in section
    assert "refused: 2" not in section


def test_the_invalid_and_blink_fractions_are_shown(detect_rows, tmp_path, prefix):
    """These two numbers are a LOWER bound on unusable samples -- DataQuality
    reports that detection succeeded, never that it was correct -- and the
    report says so rather than presenting them as the whole truth."""
    section = _section(build_report(tmp_path, prefix=prefix), "Detection")

    assert "invalid" in section and "blink" in section
    assert "lower bound" in section


def test_no_agreement_line_exists_in_this_stage(detect_rows, tmp_path, prefix):
    """One detector cannot disagree with anything. A line that always read
    1.00 would look like a measurement, which is worse than an absent one."""
    section = _section(build_report(tmp_path, prefix=prefix), "Detection")

    assert "agreement" not in section.lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/cli/test_detect_report.py -v`
Expected: FAIL — `AssertionError: no section headed 'Detection'`

- [ ] **Step 3: Implement in `build_report`, never `gather_readings`**

```python
    # Detection. Computed HERE and not in `gather_readings`, which runs on
    # every wl.works poll under the single lock that also serialises job
    # accepts -- and the responder reads none of these values.
    detection_rows, validity_rows = _detection_rows(prefix=prefix)

    lines += ["", "## Detection", ""]
    lines += [f"### Events per session per trace (24 h) — {len(recent)}", ""]
    lines += [
        f"- `{row['subject']}` @ {row['session_datetime']:%Y-%m-%d %H:%M} — "
        f"{row['trace']}: {row['n_saccades']} saccades, "
        f"{row['n_microsaccades']} microsaccades"
        for row in recent
    ] or ["- none"]

    # A LOWER BOUND, and it says so: `DataQuality` reports that detection
    # SUCCEEDED, never that it was correct, so a mis-detected P4 reads 100
    # here and is counted as tracked (design spec section 1).
    lines += ["", "### Unusable samples (lower bound, running total)", ""]
    lines += [
        f"- {label}: {fraction:.1%}"
        for label, fraction in _unusable_fractions(validity_rows).items()
    ]

    # Distinct causes, distinct lines -- never a collapsed "refused: N".
    lines += ["", f"### Detection refused (7 days) — {len(refused)}", ""]
    lines += [
        f"- `{row['subject']}` @ {row['session_datetime']:%Y-%m-%d %H:%M} — "
        f"{row['trace']}: {row['reason']}"
        for row in refused
    ] or ["- none"]
```

**No agreement line in this stage.** One detector cannot disagree with
anything, and a line that would always read `1.00` is worse than an absent
one: it looks like a measurement.

- [ ] **Step 4: Run the whole suite and `wl-check`**

Run: `.venv/bin/python -m pytest -q && wl-check`

Then run `tests/eye` and `tests/contracts` under a real 3.13 interpreter, per
the Global Constraints: a green 3.11 run is evidence about 3.11 alone, and CI
has already been red on 3.13 for a day while every local run passed.

- [ ] **Step 5: Commit, and write the stage handoff**

```bash
git add wl_preproc/cli/report.py tests/cli/test_detect_report.py
git commit -m "report: detected events, and the sessions that refused"
```

Then write `docs/handoffs/YYYY-MM-DD-detection-substrate-built.md` recording:
the measured run count from Task 8, which of the eight labels are produced and
which are still declared-but-unused, what stage 2 inherits, and any mutation
that survived — a surviving mutation is a gap in the tests, and naming it is
what stops stage 2 building on ground nobody checked.

---

## Not in this plan

- **The other six detectors** — Otero-Millan, Nyström–Holmqvist, NSLR,
  REMoDNaV, Bayesian microsaccade detection, U'n'Eye. Stage 2.
- **The consensus suite and the vocabulary-coarsening lattice** (spec §6, §6.1).
  Both need a second detector to be exercised by, and machinery whose only
  consumer is a future task is the unexercised-fallback defect this project's
  checkpoint records three times over.
- **Saccade vigor and the main-sequence fits** (spec §6.5). They need
  amplitudes from more than one detector to be worth comparing, and the
  condition grain needs a generator that emits `CONDITION` payloads.
- **`pso`, `pursuit` and `drift` as produced labels.** Declared in the enum
  here, produced in stage 2 by the detectors that can see them.
- **The `torch` declaration and U'n'Eye vendoring** (spec §8).
