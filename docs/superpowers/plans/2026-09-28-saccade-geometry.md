# Saccade Geometry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every stored saccade and microsaccade row carries where the event starts and ends and its direction, for every registered detector.

**Architecture:** `measure.py`'s `Measurement` gains five fields, filled from the two gaze samples its amplitude already reads, under each detector's measurement rule. `EyeDetection.Run` gains five nullable columns, stored from the measurement. No detector changes.

**Tech Stack:** Python ≥3.11, numpy, DataJoint 2.3 (schema tests only).

**Spec:** `docs/superpowers/specs/2026-09-28-saccade-geometry-design.md` (`9ad5d21`). It is binding. The requester approved it on 2026-09-28 ("Yes, write the plan").

**Every piece of code below was proven before this plan was written,** in a scratch worktree, `$SCRATCH/geo_wt`, which holds these exact files.
- **Tests:** every test named here passed there. So did every test area that touches the stored runs: `tests/eye`, `tests/schema/test_detect_populate.py`, `tests/schema/test_consensus_populate.py`, `tests/schema/test_detect_schema.py` and `tests/cli`.
- **Mutation checks:** every one named below was run, and each failed its test.
- **Direction tolerance:** it was set from what was measured there (Task 2).

## Global Constraints

- **The spec is binding.** Where it and this plan disagree, the spec wins; record a ruling.
- **One computation, in `measure.py`, for every detector** (spec §1.1). The positions are the two gaze samples the amplitude reads, so `amplitude_deg` is exactly the distance between them. No detector computes its own.
- **The convention, verbatim from spec §1.2:**
  - degrees, counterclockwise from rightward: 0° rightward, 90° upward, ±180° leftward, −90° downward;
  - `degrees(atan2(Δy, Δx))`, in [−180°, 180°];
  - the calibrated frame, degrees of visual angle, positive x rightward and positive y upward;
  - NULL for a zero displacement.
- **Which rows (spec §1.3):** exactly those that carry `amplitude_deg`. The conjunction is measured on the left eye's gaze, as its amplitude already is.
- **Columns:** `start_x_deg`, `start_y_deg`, `end_x_deg`, `end_y_deg`, `direction_deg`, all `double`, nullable. **No migration** (spec §3).
- **Unchanged:** `docs/schemas`, `wlpp report`, every detector, and every existing column's value.
- **Tests.**
  - Run with `.venv/bin/python -m pytest <files> -p no:cacheprovider`. In a git worktree, prefix `PYTHONPATH=$PWD`.
  - Database tests need Docker for the MySQL testcontainer. A module-wide setup error on a 120 s container timeout is the known flake: re-run it.
  - Mutation checks run with `PYTHONDONTWRITEBYTECODE=1`, after `find . -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +`, and again after restoring.
  - **Run each task's own test files.** The full suite runs once, on both interpreters, in Task 3 (the requester's preference of 2026-09-27).
  - The Bash tool's shell is zsh: an unquoted `$VAR` holding several arguments is not split. Use `bash -c` for such loops.
- **Every commit message ends with:**

  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R
  ```

## Review Focus

Five input classes, most likely first. Each is pinned by a test named here, in the task that owns the code.

1. **A zero displacement.** The shared `measure` reads a one-sample run as 0°. The positions are then stored, and the direction is NULL, never 0°. Task 1: `test_a_zero_displacement_has_positions_and_no_direction`.
2. **A detector's widened window.** NSLR measures to its landing sample and BMD from its take-off sample; the positions must move with the amplitude. Task 1: `test_each_detectors_rule_moves_the_positions_with_the_amplitude`.
3. **Rows that are not measured.** Non-event labels, and NSLR saccades under 10 ms, must carry none of the five. Task 2: `test_every_stored_event_row_carries_the_geometry_its_amplitude_reads`.
4. **The conjunction trace,** which is measured on the left eye's gaze. The same invariants hold on it. Task 2: the same test, over all three traces.
5. **Directions near ±180°.** A leftward step is stored as 179.99° by some detectors and −179.2° by others. The comparison must wrap. Task 2: `test_a_planted_steps_stored_direction_is_the_planted_direction`.

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/eye/detect/measure.py` | `Measurement`'s five new fields, `direction`, and their filling in `measure` and `measure_event_run` |
| `wl_preproc/schema/detect.py` | the five columns on `EyeDetection.Run`, stored by `_insert_trace` |
| `tests/eye/detect/test_measure.py` | the geometry of one measurement |
| `tests/schema/test_detect_schema.py`, `tests/schema/test_detect_populate.py` | the columns, and the stored rows of every detector |
| `docs/…`, `wl.yaml` | the parent spec's amendment (Task 2); records (Task 3) |

---

### Task 1: The measurement

**Files:**
- Modify: `wl_preproc/eye/detect/measure.py`
- Test: `tests/eye/detect/test_measure.py`

**Interfaces — produces:**
- `Measurement(amplitude_deg, peak_velocity_deg_s, duration_s, start_x_deg, start_y_deg, end_x_deg, end_y_deg, direction_deg: float | None)`, frozen, with slots;
- `direction(dx_deg: float, dy_deg: float) -> float | None`;
- `measure` and `measure_event_run` return the five fields filled.

- [ ] **Step 1: Write the failing tests.** Add `import math` as the file's first import line, then append:

```python
# -- Saccade geometry (design spec `2026-09-28-saccade-geometry-design.md`) -----
#
# Every measurement carries the gaze at the two samples its amplitude reads,
# and the direction from one to the other: 0 deg rightward, 90 deg upward.


@pytest.mark.parametrize("dx, dy, direction", [
    (1.0, 0.0, 0.0), (0.0, 1.0, 90.0), (-1.0, 0.0, 180.0), (0.0, -1.0, -90.0), (1.0, 1.0, 45.0),
])
def test_the_direction_is_counterclockwise_from_rightward_in_degrees(dx, dy, direction):
    """Spec 1.2: 0 deg is rightward and 90 deg upward, in the calibrated
    frame (positive x rightward, positive y upward)."""
    gaze = np.zeros((5, 2))
    gaze[2:] = [dx, dy]
    got = measure(gaze, np.zeros((5, 2)), 1, 4, 500.0)
    assert got.direction_deg == pytest.approx(direction, abs=1e-12)


def test_the_positions_are_the_two_samples_the_amplitude_reads():
    """Spec 1.1: start `gaze[start]`, end `gaze[stop - 1]`, and the amplitude
    is exactly the distance between them."""
    gaze = _walk()
    got = measure(gaze, _speeds(), 10, 16, 500.0)
    assert (got.start_x_deg, got.start_y_deg) == tuple(gaze[10])
    assert (got.end_x_deg, got.end_y_deg) == tuple(gaze[15])
    assert got.amplitude_deg == float(np.hypot(got.end_x_deg - got.start_x_deg, got.end_y_deg - got.start_y_deg))
    assert got.direction_deg == math.degrees(math.atan2(gaze[15, 1] - gaze[10, 1], gaze[15, 0] - gaze[10, 0]))


@pytest.mark.parametrize("rule, first, last", [("landing", 10, 16), ("take-off", 9, 15)])
def test_each_detectors_rule_moves_the_positions_with_the_amplitude(rule, first, last):
    """Spec 1.1: NSLR's landing rule ends at `gaze[stop]`; BMD's take-off rule
    starts at `gaze[start - 1]`. The positions follow the same samples."""
    gaze = _walk()
    got = measure_event_run(gaze, _speeds(), _offered(40), 10, 16, 500.0,
                            runs_end_before_landing=rule == "landing", min_measured_ms=None,
                            runs_start_after_takeoff=rule == "take-off")
    assert (got.start_x_deg, got.start_y_deg) == tuple(gaze[first])
    assert (got.end_x_deg, got.end_y_deg) == tuple(gaze[last])
    assert got.amplitude_deg == float(np.hypot(got.end_x_deg - got.start_x_deg, got.end_y_deg - got.start_y_deg))
    assert got.duration_s == 6 / 500.0


def test_a_zero_displacement_has_positions_and_no_direction():
    """Spec 1.2: where start equals end, the direction is NULL, never 0 deg."""
    gaze = np.full((5, 2), 2.5)
    got = measure(gaze, np.zeros((5, 2)), 0, 5, 500.0)
    assert got.amplitude_deg == 0.0
    assert got.direction_deg is None
    assert (got.start_x_deg, got.start_y_deg, got.end_x_deg, got.end_y_deg) == (2.5, 2.5, 2.5, 2.5)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_measure.py -q -p no:cacheprovider`
Expected: 9 failed, 28 passed. Each new test fails with `AttributeError: 'Measurement' object has no attribute ...`.

- [ ] **Step 3: Implement** — apply exactly this diff:

```diff
diff --git a/wl_preproc/eye/detect/measure.py b/wl_preproc/eye/detect/measure.py
index ec826cc..bdeb724 100644
--- a/wl_preproc/eye/detect/measure.py
+++ b/wl_preproc/eye/detect/measure.py
@@ -10,6 +10,8 @@ whichever detector found the saccade.
 
 from __future__ import annotations
 
+import dataclasses
+import math
 from dataclasses import dataclass
 
 import numpy as np
@@ -24,9 +26,31 @@ MICROSACCADE_MAX_DEG = 1.0
 
 @dataclass(frozen=True, slots=True)
 class Measurement:
+    """One event's measurement. The positions are the gaze at the two
+    samples the amplitude is measured between, so `amplitude_deg` is exactly
+    the distance from start to end and `direction_deg` its angle (design spec
+    `2026-09-28-saccade-geometry-design.md` section 1)."""
+
     amplitude_deg: float
     peak_velocity_deg_s: float
     duration_s: float
+    start_x_deg: float
+    start_y_deg: float
+    end_x_deg: float
+    end_y_deg: float
+    direction_deg: float | None
+
+
+def direction(dx_deg: float, dy_deg: float) -> float | None:
+    """The angle of a displacement, in degrees counterclockwise from
+    rightward: 0 is rightward, 90 upward, +/-180 leftward, -90 downward, in
+    the calibrated frame (positive x rightward, positive y upward; the task
+    code's frame for target positions). `None` for a zero displacement,
+    which has no direction -- `atan2(0, 0)` would say 0, "rightward"
+    (design spec `2026-09-28-saccade-geometry-design.md` section 1.2)."""
+    if dx_deg == 0.0 and dy_deg == 0.0:
+        return None
+    return math.degrees(math.atan2(dy_deg, dx_deg))
 
 
 def amplitude(gaze_deg: np.ndarray, start: int, stop: int) -> float:
@@ -96,10 +120,16 @@ def measure(
     if stop <= start:
         raise ValueError(f"measure requires stop > start; got start={start}, stop={stop}")
     speed = np.hypot(velocity_deg_s[start:stop, 0], velocity_deg_s[start:stop, 1])
+    first, last = gaze_deg[start], gaze_deg[stop - 1]
     return Measurement(
         amplitude_deg=amplitude(gaze_deg, start, stop),
         peak_velocity_deg_s=float(speed.max()) if speed.size else 0.0,
         duration_s=float(stop - start) / fs_hz,
+        start_x_deg=float(first[0]),
+        start_y_deg=float(first[1]),
+        end_x_deg=float(last[0]),
+        end_y_deg=float(last[1]),
+        direction_deg=direction(float(last[0] - first[0]), float(last[1] - first[1])),
     )
 
 
@@ -159,7 +189,9 @@ def measure_event_run(
     measures it. The requester's decision of 2026-09-27.
 
     `duration_s` is the run's own, `(stop - start) / fs_hz`, under every
-    rule."""
+    rule. The positions and direction are the widened window's, the same two
+    samples its amplitude reads (design spec
+    `2026-09-28-saccade-geometry-design.md` section 1.1)."""
     if min_measured_ms is not None and (stop - start) / fs_hz < min_measured_ms / 1000.0:
         return None
     lo, hi = start, stop
@@ -180,11 +212,7 @@ def measure_event_run(
     if (lo, hi) == (start, stop):
         return measure(gaze_deg, velocity_deg_s, start, stop, fs_hz)
     widened = measure(gaze_deg, velocity_deg_s, lo, hi, fs_hz)
-    return Measurement(
-        amplitude_deg=widened.amplitude_deg,
-        peak_velocity_deg_s=widened.peak_velocity_deg_s,
-        duration_s=float(stop - start) / fs_hz,
-    )
+    return dataclasses.replace(widened, duration_s=float(stop - start) / fs_hz)
 
 
 def classify(amplitude_deg: float, microsaccade_max_deg: float) -> Label:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_measure.py -q -p no:cacheprovider`
Expected: 37 passed.

- [ ] **Step 5: Mutation checks.** Each must fail `test_measure.py`; each was measured to.
  - `math.atan2(dy_deg, dx_deg)` → `math.atan2(dx_deg, dy_deg)`: 5 failed.
  - `gaze_deg[stop - 1]` in `measure`'s `first, last = ...` → `gaze_deg[min(stop, len(gaze_deg) - 1)]`: 4 failed.
  - Delete the zero-displacement `return None`: `test_a_zero_displacement_has_positions_and_no_direction` fails.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/eye/detect/measure.py tests/eye/detect/test_measure.py
git commit -m "feat(eye): every measurement carries where the event starts and ends and its direction, from the two samples its amplitude reads

<trailer lines>"
```

---

### Task 2: Storing it, for every detector

**Files:**
- Modify: `wl_preproc/schema/detect.py`
- Modify: `docs/superpowers/specs/2026-08-31-saccade-detection-design.md`
- Test: `tests/schema/test_detect_schema.py`, `tests/schema/test_detect_populate.py`

**Interfaces:**
- Consumes: Task 1's `Measurement` fields.
- Produces: `EyeDetection.Run`'s five columns, and `schema/detect.py::_STORED_MEASUREMENTS`.

**The tolerance** (spec §4, set from measurement). On the stepped session, with BMD on its drifting copy, every registered detector's stored direction for a planted step was:
- within 1.8° of the planted direction on the two steps above 1°;
- within 10.7° on the 0.7° step, where gaze noise is a larger share of the displacement.

The test allows 3° and 15°.

- [ ] **Step 1: Write the failing tests.**

In `tests/schema/test_detect_schema.py`, apply:

```diff
diff --git a/tests/schema/test_detect_schema.py b/tests/schema/test_detect_schema.py
index ecf046a..6c8db15 100644
--- a/tests/schema/test_detect_schema.py
+++ b/tests/schema/test_detect_schema.py
@@ -76,9 +76,12 @@ def test_validity_is_keyed_per_real_eye_not_per_trace(schemas, enum_values):
 
 
 def test_a_run_row_carries_its_measurements_nullably(schemas):
-    """A saccade run IS an event, so it carries amplitude and peak velocity;
-    fixation, blink and invalid runs leave them null."""
-    for name in ("amplitude_deg", "peak_velocity_deg_s", "reliability"):
+    """A saccade run IS an event, so it carries amplitude and peak velocity,
+    and where it starts and ends and its direction (design spec
+    `2026-09-28-saccade-geometry-design.md`); fixation, blink and invalid runs
+    leave them null."""
+    for name in ("amplitude_deg", "peak_velocity_deg_s", "reliability",
+                 "start_x_deg", "start_y_deg", "end_x_deg", "end_y_deg", "direction_deg"):
         assert schemas.EyeDetection.Run.heading.attributes[name].nullable
 
 
```

Append to `tests/schema/test_detect_populate.py`:

```python
# -- Saccade geometry (design spec `2026-09-28-saccade-geometry-design.md`) -----

#: How far a planted step's stored direction may sit from the planted one.
#: Measured 2026-09-28 on `stepped_session` (BMD on the drifting copy), over
#: every registered detector: at most 1.8 deg on the two steps above 1 deg,
#: and at most 10.7 deg on the 0.7 deg step, where the gaze noise is a larger
#: share of the displacement (Engbert-Kliegl's; REMoDNaV's 8.3 deg next).
_DIRECTION_TOLERANCE_DEG = {True: 3.0, False: 15.0}  # keyed on "1 deg or more"


def test_every_stored_event_row_carries_the_geometry_its_amplitude_reads(stepped_session):
    """Spec section 4. On every trace, for every registered detector:
    - a row with an amplitude has its start and end positions, the amplitude
      is exactly their distance, and the direction is their angle (NULL only
      where they coincide);
    - a row without an amplitude has none of the five."""
    import math

    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session
    geometry = ("start_x_deg", "start_y_deg", "end_x_deg", "end_y_deg", "direction_deg")
    measured = 0
    for name in _detector_names():
        for trace in ("left", "right", "conjunction"):
            where = {**session_key, "trace": trace, **_detector(name)}
            for row in (detect.EyeDetection.Run & where).to_dicts():
                if row["amplitude_deg"] is None:
                    assert all(row[column] is None for column in geometry), (name, trace, row)
                    continue
                dx = row["end_x_deg"] - row["start_x_deg"]
                dy = row["end_y_deg"] - row["start_y_deg"]
                assert row["amplitude_deg"] == float(np.hypot(dx, dy)), (name, trace, row)
                if dx == 0.0 and dy == 0.0:
                    assert row["direction_deg"] is None, (name, trace, row)
                else:
                    assert row["direction_deg"] == math.degrees(math.atan2(dy, dx)), (name, trace, row)
                measured += 1
    assert measured, "the fixture must give the detectors events to measure"


def test_a_planted_steps_stored_direction_is_the_planted_direction(stepped_session, drifting_stepped_session):
    """Spec section 4, end to end. The stepped session plants A -> B -> C -> A
    on the x axis alone: rightward, rightward, then leftward back to A. Every
    event run a detector finds at a planted step points the planted way,
    within `_DIRECTION_TOLERANCE_DEG`. BMD is read on its drifting copy
    (`_HELD_ON_A_DRIFTING_EYE`), and NSLR is not held below 1 deg
    (`_NOT_USED_BELOW_1_DEG`)."""
    from wl_preproc.schema import detect

    positions = [0.0]
    for step in _STEPS_PX:
        positions.append(positions[-1] + step)
    positions[-1] = positions[0]
    planted = [0.0 if b > a else 180.0 for a, b in zip(positions, positions[1:])]
    amplitudes_deg = [abs(b - a) * CAL_SCALE for a, b in zip(positions, positions[1:])]
    assert planted == [0.0, 0.0, 180.0]

    checked = 0
    for name in _detector_names():
        session_key, _report, planted_onsets = (
            drifting_stepped_session if name in _HELD_ON_A_DRIFTING_EYE else stepped_session
        )
        runs = (detect.EyeDetection.Run & {**session_key, "trace": "left", **_detector(name)}).to_dicts()
        for onset, want, amplitude_deg in zip(planted_onsets, planted, amplitudes_deg, strict=True):
            if name in _NOT_USED_BELOW_1_DEG and amplitude_deg < 1.0:
                continue
            found = [r for r in runs if r["label"] in ("saccade", "microsaccade")
                     and abs(r["run_start"] - onset) <= 5]
            assert found, (name, onset)
            for run in found:
                off = (run["direction_deg"] - want + 180.0) % 360.0 - 180.0
                assert abs(off) <= _DIRECTION_TOLERANCE_DEG[amplitude_deg >= 1.0], (name, onset, run["direction_deg"])
                checked += 1
    assert checked >= 3 * len(_detector_names()) - 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_schema.py tests/schema/test_detect_populate.py -k "nullably or geometry_its_amplitude or planted_direction" -q -p no:cacheprovider`
Expected: 3 failed, each with a `KeyError` on a new column (`'start_x_deg'` or `'direction_deg'`).

- [ ] **Step 3: Implement** — apply exactly this diff:

```diff
diff --git a/wl_preproc/schema/detect.py b/wl_preproc/schema/detect.py
index 2cace82..3957c98 100644
--- a/wl_preproc/schema/detect.py
+++ b/wl_preproc/schema/detect.py
@@ -414,6 +414,18 @@ class EyeDetection(dj.Computed):
         amplitude_deg=null       : double
         peak_velocity_deg_s=null : double
         reliability=null         : double
+        # Where the event starts and ends, and its direction: the gaze at
+        # the two samples `amplitude_deg` is measured between, so the
+        # amplitude is exactly their distance. Degrees of visual angle,
+        # positive x rightward and y upward; the direction counterclockwise
+        # from rightward, in [-180, 180], NULL for a zero displacement. On
+        # exactly the rows that carry `amplitude_deg` (design spec
+        # `2026-09-28-saccade-geometry-design.md`).
+        start_x_deg=null   : double
+        start_y_deg=null   : double
+        end_x_deg=null     : double
+        end_y_deg=null     : double
+        direction_deg=null : double
         """
 
     @property
@@ -817,7 +829,7 @@ class EyeDetection(dj.Computed):
         })
 
         def _run_row(index: int, run: Run) -> dict:
-            amplitude_deg = peak_velocity_deg_s = None
+            measurement = None
             if run.label in (Label.SACCADE, Label.MICROSACCADE):
                 measurement = measure_event_run(
                     gaze, v, offered, run.start, run.stop, fs_hz,
@@ -827,19 +839,26 @@ class EyeDetection(dj.Computed):
                         detector, trace, run.label, reliability_by_span.get((run.start, run.stop))
                     ),
                 )
-                if measurement is not None:
-                    amplitude_deg = measurement.amplitude_deg
-                    peak_velocity_deg_s = measurement.peak_velocity_deg_s
             return {
                 **row, "run_index": index, "run_start": run.start, "run_stop": run.stop,
-                "label": run.label.value, "amplitude_deg": amplitude_deg,
-                "peak_velocity_deg_s": peak_velocity_deg_s,
+                "label": run.label.value,
+                **{column: None if measurement is None else getattr(measurement, column)
+                   for column in _STORED_MEASUREMENTS},
                 "reliability": reliability_by_span.get((run.start, run.stop)),
             }
 
         self.Run.insert(_run_row(index, run) for index, run in enumerate(runs))
 
 
+#: The `Measurement` fields `EyeDetection.Run` stores, each under its own
+#: name, all NULL for a run that is not measured. `duration_s` is not stored:
+#: it is `(run_stop - run_start) / fs_hz` on every row.
+_STORED_MEASUREMENTS = (
+    "amplitude_deg", "peak_velocity_deg_s",
+    "start_x_deg", "start_y_deg", "end_x_deg", "end_y_deg", "direction_deg",
+)
+
+
 def _measured_from_takeoff(detector, trace: str, label: Label, reliability: float | None) -> bool:
     """Whether a stored run is measured from its take-off sample: BMD's
     take-off rule (design spec `2026-09-27-bmd-design.md` section 3.5), per
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_schema.py tests/schema/test_detect_populate.py tests/schema/test_consensus_populate.py tests/cli -q -p no:cacheprovider`
Expected: all pass. The rows every other test reads are unchanged, including `test_every_other_detectors_measurements_are_unchanged`.

- [ ] **Step 5: Mutation check.** Remove `"direction_deg",` from `_STORED_MEASUREMENTS`. Both new stored-row tests must fail; measured, they do.

- [ ] **Step 6: Amend the parent spec.** In `docs/superpowers/specs/2026-08-31-saccade-detection-design.md`:
  - after the paragraph that says each event row "carries `amplitude_deg`, `peak_velocity_deg_s` and a nullable `reliability`" (§5), add:

    > *Amended 2026-09-28 (spec `2026-09-28-saccade-geometry-design.md`): each event row also carries `start_x_deg`, `start_y_deg`, `end_x_deg`, `end_y_deg` and `direction_deg`. These are the gaze at the two samples its amplitude reads, and the direction from one to the other, counterclockwise from rightward in degrees. They are on exactly the rows that carry an amplitude.*

  - in the table row for `EyeDetection.Run` (the storage summary near the end of the spec), append the five columns after `reliability`.

- [ ] **Step 7: Commit**

```bash
git add wl_preproc/schema/detect.py tests/schema/test_detect_schema.py tests/schema/test_detect_populate.py docs/superpowers/specs/2026-08-31-saccade-detection-design.md
git commit -m "feat(eye): EyeDetection.Run stores each event's start and end position and direction, for every detector

<trailer lines>"
```

---

### Task 3: Records and the full suite

**Files:**
- Modify: `docs/CHECKPOINT.md`, `wl.yaml`

- [ ] **Step 1: CHECKPOINT.** "Start here" item 2 names this branch as next. Replace that with a one-paragraph entry:
  - the geometry is built on `spec/saccade-geometry`, NOT merged as written;
  - the five columns and the convention;
  - the measured direction tolerance.

  Do not re-point the header; that happens at merge, with CI read.

- [ ] **Step 2: `wl.yaml`.** Add one sentence to `status.phase` naming the geometry, its branch, and that it is not yet merged. Then run `.venv/bin/wl-check` **on its own** and read its exit status before committing. If a short SHA is ever written to `describes` and has no letter in it, quote it.

- [ ] **Step 3: Full suite on both interpreters, once.** Run with `WLPP_BMD_REFERENCE=$SCRATCH/bmd/BMD`, `WLPP_BMD_BOOST_INCLUDE=$SCRATCH/bmd/boost_1_86_0` and `WLPP_NSLR_REFERENCE=$SCRATCH` set, and `WLPP_OHDPI_REFERENCE` unset, as CI has it:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
$SCRATCH/venv_ci/bin/python -m pytest -q -p no:cacheprovider
```

Expected: both green. `main` gave 1714 passed on 3.11 and 1713 on 3.13; this plan adds 11 tests (9 in Task 1, 2 in Task 2), so expect 1725 and 1724. Then commit:

```bash
git add docs wl.yaml
git commit -m "docs: saccade geometry built -- its columns, the direction convention, and the measured tolerance

<trailer lines>"
```
