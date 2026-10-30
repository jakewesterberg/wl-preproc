# Saccades in True Visual Angle (XC-398) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Measure every detector's saccades in true visual angle by default, detect Engbert-Kliegl's in the
eye's local frame as wl-xcon's online copy will, keep per axis as a paramset registered on request, store and
publish each endpoint's and each sample's eccentricity and polar angle, and publish v2 of the contract fixtures.

**Architecture:** One geometry module holds wl-xcon's definitions; the shared measurement takes the paramset's
`geometry`, which every caller passes; Engbert-Kliegl and BMD's gate are handed the local-frame velocity; the
default paramsets write `true-angle`; `EyeDetection.Run` stores the new measures and fixations' positions; the
report and the NWB file name and carry them; v2 pins it for wl-xcon.

**Tech Stack:** Python 3.11/3.13, NumPy, DataJoint 2.3.3 on MySQL (testcontainers in tests), pydantic, pynwb.

**Spec:** `docs/superpowers/specs/2026-10-30-true-angle-saccades-design.md`: §0-§12 and its amendment 1;
amendments 2-7 are Task 8's, made while proving this plan.

**Proven before written.** Every step below was run on a scratch branch and replayed from this plan's own
text to an identical tree: 46 mutation checks caught, and the full suite on both interpreters as
Task 8 states.

## Global Constraints

- **The geometry is wl-xcon's, to the formula** (§2, amendment 1): every angle in [0, 360), the polar angle null at
  zero eccentricity, a direction null for no displacement.
- **Per-axis is v1 exactly:** `docs/fixtures/saccade/v1.json` and `v1-thresholds.json` are never edited, and the
  per-axis setting reproduces them.
- **`amplitude`, `measure`, `measure_event_run` and `measure_positions` take `geometry` with no default.**
- **A paramset without the key means per-axis** (amendment 3); the registered default is true angle.
- **The ports keep their own detection** (decision 1): NSLR, REMoDNaV and U'n'Eye unchanged inside; BMD's sampler
  unchanged, its gate Engbert-Kliegl's (amendment 2); every fidelity test unchanged.
- **Execute on a branch `feat/true-angle-saccades`** cut from `plan/true-angle-saccades` once this plan is
  committed there.
- **Python 3.11 and 3.13**: the full suite runs once on each, at the end (Task 8).
- **A test that registers a paramset removes it afterwards** with what was computed from it
  (`delete(part_integrity="cascade")`), so no later module's populate meets it.
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code,
  data or tables; leave the stray `wl-preproc` symlink at the repository root out of every commit.
- **Commands run from the repository root.** In a git worktree, `.venv/bin/python` is the main checkout's;
  every test command sets `PYTHONPATH=$PWD` and `PYTHONDONTWRITEBYTECODE=1`.
- **Patches apply with `git apply` and must apply cleanly**; `docs/schemas` is regenerated with
  `schemas export`, and `docs/fixtures/saccade/v2.json` written by its CLI, never either by hand.
- **Neighbour runs name their files in the suite's order.**
- **Every commit ends with:**
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
  ```

## Review Focus

The five inputs the spec implies that a person is most likely to meet, most likely first; each is pinned by a
test in the task that owns the code:
1. **Gaze far off centre,** where the two geometries differ most: `test_the_numbers_both_sides_checked` (Task 1),
   `test_a_glissade_is_compared_with_its_saccade_in_the_settings_geometry` (Task 2),
   `test_under_true_angle_the_step_across_the_cut_is_a_microsaccade` (Task 3).
2. **A position at the centre, or an event or fixation with no displacement:** the polar angle and direction null,
   never 0. `test_the_polar_angle_is_undefined_at_the_centre` (Task 1),
   `test_a_zero_displacement_has_no_direction_under_true_angle_either` and
   `test_the_endpoints_carry_their_eccentricity_and_polar_angle_under_both` (Task 2).
3. **A database declared, or an NWB file described, before this change:** the stage refuses and names its
   `ALTER TABLE`, which restores the columns as DataJoint declares them; an older description still reads.
   `test_a_database_without_the_new_columns_detects_nothing_and_names_the_statement`,
   `test_the_statement_restores_the_columns_as_datajoint_declares_them` (Task 5),
   `test_a_description_recorded_before_the_polar_series_reads_back_without_it` (Task 6).
4. **A paramset without the key, a per-axis one beside the default, or one with an unknown value:** per axis
   and named so, or refused. `test_the_conjunction_label_reads_the_paramsets_geometry` and
   `test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused` (Task 2),
   `test_a_per_axis_paramset_detects_and_measures_as_v1` (Task 4),
   `test_a_paramset_without_the_setting_is_named_per_axis` (Task 6).
5. **A short trace, or one with masked samples,** where incomplete windows move the scale:
   `test_under_true_angle_the_scale_leaves_out_incomplete_windows` (Task 3),
   `test_v2s_short_trace_pins_the_complete_window_scale` (Task 7).

## The mutation checks


Each task names mutations as `[name, file, old, new, test]`. Save this helper once, outside the repository,
as `mutate.py`, and run it from the repository root with a task's list saved as JSON:
`.venv/bin/python /path/to/mutate.py /path/to/mutations.json`. Each mutation is applied alone, caches
cleared, its test run, and the file restored; every line must print `CAUGHT`, and `git status` must be
clean afterwards.

```python
"""Run the mutations in a JSON list [[name, file, old, new, test], ...] from the
current directory: each alone, caches cleared, restored after."""
import json, os, pathlib, shutil, subprocess, sys
root = pathlib.Path.cwd()
def clear():
    for cache in root.rglob("__pycache__"):
        if ".venv" not in cache.parts:
            shutil.rmtree(cache, ignore_errors=True)
for name, rel, old, new, test in json.loads(pathlib.Path(sys.argv[1]).read_text()):
    path = root / rel
    original = path.read_text()
    assert original.count(old) == 1, (name, "old text not found once")
    clear()
    path.write_text(original.replace(old, new))
    try:
        result = subprocess.run([sys.executable, "-m", "pytest", *test.split(), "-q", "-p", "no:cacheprovider",
                                 "-o", "faulthandler_timeout=1800"],
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root)},
                                capture_output=True, text=True)
    finally:
        path.write_text(original)
        clear()
    tail = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr[-200:]
    print(f"{name}: {'CAUGHT' if result.returncode else 'SURVIVED'} -- {tail}", flush=True)
```

## Files

| File | Responsibility | Task |
|---|---|---|
| `wl_preproc/eye/detect/geometry.py` (new) | wl-xcon's definitions | 1 |
| `wl_preproc/eye/detect/measure.py`, `velocity.py` | measurement in the setting; the local-frame velocity | 2 |
| `wl_preproc/eye/detect/engbert_kliegl.py`, `otero_millan.py`, `nystrom_holmqvist.py`, `bmd.py`, `registry.py` | each detector's use of the setting; the complete-window scale | 2, 3 |
| `wl_preproc/schema/detect.py` | the default, per-axis on request, the velocity handed, the stored columns, the guard | 2, 4, 5 |
| `wl_preproc/nwb/eye_events.py`, `nwb/gather.py`, `nwb/eye.py`, `nwb/describe.py`, `contracts/nwb_description.py`, `docs/schemas/nwb_description.json` | the NWB file and its description | 5, 6 |
| `wl_preproc/cli/report.py` | the vigor lines' geometry | 6 |
| `wl_preproc/eye/detect/contract_fixtures.py`, `docs/fixtures/saccade/v2.json` (new) | v2 | 2, 7 |
| the spec, `HANDOVER-wl-expcontroller.md`, `docs/handoffs/2026-10-30-true-angle-saccades.md` (new), `docs/CHECKPOINT.md`, `wl.yaml` | the records | 8 |

---

### Task 1: The geometry of true visual angle

§2: wl-xcon's definitions, to the formula, in one module nothing else duplicates: a position's direction, the true angle between two, eccentricity and polar angle, the local frame at a direction; every angle in [0, 360), mod 360 and 0 where that rounds to 360 (amendment 1); the polar angle undefined at the centre. The local frame is wl-xcon's `viewport.local_true_angle`, broadcast so each direction may have its own centre.

**Files:**
- Create: `wl_preproc/eye/detect/geometry.py`
- Create: `tests/eye/detect/test_true_angle_geometry.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `geometry.TRUE_ANGLE = "true-angle"`, `PER_AXIS = "per-axis"`, `GEOMETRIES`, `DEFAULT_GEOMETRY`
  (true angle), `UNSTATED_GEOMETRY` (per-axis); `directions(xy_deg) -> (..., 3)`; `true_angle_deg(u, v)`;
  `wrap_360_deg(angle_deg)`; `eccentricity_polar_deg(xy_deg) -> (e, polar)`, polar NaN at e = 0;
  `local_coordinates_deg(v, c) -> (x, y)`, broadcast.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/eye/detect/test_true_angle_geometry.py b/tests/eye/detect/test_true_angle_geometry.py
new file mode 100644
index 0000000..ca81bc5
--- /dev/null
+++ b/tests/eye/detect/test_true_angle_geometry.py
@@ -0,0 +1,107 @@
+"""True visual angle between gaze positions in per-axis degrees (design spec
+`2026-10-30-true-angle-saccades-design.md` section 2): wl-xcon's definitions,
+to the formula, so its online detector and eye records match these."""
+
+import math
+
+import numpy as np
+import pytest
+
+from wl_preproc.eye.detect import geometry
+
+
+def _angle(a, b):
+    return float(geometry.true_angle_deg(geometry.directions(np.array(a)), geometry.directions(np.array(b))))
+
+
+@pytest.mark.parametrize(("start", "end", "expected"), [
+    ((20.0, 0.0), (20.0, 2.0), 1.879),
+    ((30.0, 0.0), (30.0, 2.0), 1.732),
+    ((30.0, 0.0), (30.0, 1.12), 0.970),
+])
+def test_the_numbers_both_sides_checked(start, end, expected):
+    """Section 1: a 2-degree step up is 1.879 degrees of true angle from
+    (20, 0) and 1.732 from (30, 0); 1.12 up from (30, 0) is under the cut."""
+    assert _angle(start, end) == pytest.approx(expected, abs=5e-4)
+
+
+def test_at_the_centre_a_step_along_an_axis_is_its_own_size():
+    assert _angle((0.0, 0.0), (8.0, 0.0)) == pytest.approx(8.0, rel=1e-12)
+    assert _angle((0.0, 0.0), (0.0, -8.0)) == pytest.approx(8.0, rel=1e-12)
+
+
+def test_a_direction_points_ahead_and_is_a_unit_vector():
+    d = geometry.directions(np.array([[0.0, 0.0], [30.0, -20.0], [-45.0, 10.0]]))
+    np.testing.assert_allclose(np.linalg.norm(d, axis=-1), 1.0, rtol=1e-15)
+    assert (d[:, 2] > 0).all()
+    np.testing.assert_allclose(d[0], [0.0, 0.0, 1.0])
+    # The same direction as normalize(tan x, tan y, 1), the definition.
+    tan = np.array([math.tan(math.radians(30.0)), math.tan(math.radians(-20.0)), 1.0])
+    np.testing.assert_allclose(d[1], tan / np.linalg.norm(tan), rtol=1e-15)
+
+
+def test_eccentricity_and_polar_angle():
+    """Polar angle counter-clockwise from rightward, in [0, 360): straight
+    down is 270, never -90 (amendment 1)."""
+    e, theta = geometry.eccentricity_polar_deg(np.array([[10.0, 0.0], [0.0, 10.0], [-10.0, 0.0], [0.0, -10.0]]))
+    np.testing.assert_allclose(e, 10.0, rtol=1e-14)
+    np.testing.assert_allclose(theta, [0.0, 90.0, 180.0, 270.0], atol=1e-12)
+    e, _ = geometry.eccentricity_polar_deg(np.array([[30.0, 30.0]]))
+    expected = math.degrees(math.atan(math.hypot(math.tan(math.radians(30.0)), math.tan(math.radians(30.0)))))
+    assert float(e[0]) == pytest.approx(expected, rel=1e-14)
+
+
+def test_the_polar_angle_is_undefined_at_the_centre():
+    """'Empty at center': NaN, never 0, which would say rightward."""
+    e, theta = geometry.eccentricity_polar_deg(np.array([[0.0, 0.0], [np.nan, 1.0]]))
+    assert float(e[0]) == 0.0
+    assert math.isnan(float(theta[0]))
+    assert math.isnan(float(e[1])) and math.isnan(float(theta[1]))
+
+
+def test_an_angle_is_wrapped_into_0_to_360():
+    """atan2's result mod 360, and 0 where that rounds to 360: -1e-15 mod
+    360 is exactly 360.0 in float64. Never -0.0, which a JSON file would
+    write as such."""
+    wrapped = geometry.wrap_360_deg(np.array([-90.0, -1e-15, -0.0, 0.0, 180.0, -180.0, 359.5]))
+    np.testing.assert_array_equal(wrapped, [270.0, 0.0, 0.0, 0.0, 180.0, 180.0, 359.5])
+    assert not np.signbit(wrapped).any()
+
+
+def test_the_local_frame_keeps_distances_from_its_centre_and_points_right_and_up_at_the_centre():
+    c = geometry.directions(np.array([25.0, -10.0]))
+    for point in ([27.0, -10.0], [25.0, -6.0], [20.0, -14.0]):
+        v = geometry.directions(np.array(point))
+        lx, ly = geometry.local_coordinates_deg(v, c)
+        assert math.hypot(float(lx), float(ly)) == pytest.approx(_angle((25.0, -10.0), point), rel=1e-12)
+    centre = geometry.directions(np.array([0.0, 0.0]))
+    lx, ly = geometry.local_coordinates_deg(geometry.directions(np.array([3.0, 0.0])), centre)
+    assert (float(lx), float(ly)) == pytest.approx((3.0, 0.0), abs=1e-12)
+    lx, ly = geometry.local_coordinates_deg(geometry.directions(np.array([0.0, 3.0])), centre)
+    assert (float(lx), float(ly)) == pytest.approx((0.0, 3.0), abs=1e-12)
+
+
+def test_a_direction_about_itself_is_the_origin():
+    """To rounding: a unit vector's dot with itself can round below 1."""
+    for xy in ([30.0, 12.0], [-7.3, 41.9], [0.0, 0.0]):
+        c = geometry.directions(np.array(xy))
+        lx, ly = geometry.local_coordinates_deg(c, c)
+        assert math.hypot(float(lx), float(ly)) < 1e-12
+
+
+def test_each_direction_may_have_its_own_centre():
+    """Broadcast: rows of `v` about rows of `c`, as one at a time."""
+    c = geometry.directions(np.array([[10.0, 5.0], [-20.0, 30.0]]))
+    v = geometry.directions(np.array([[11.0, 6.0], [-21.0, 28.0]]))
+    lx, ly = geometry.local_coordinates_deg(v, c)
+    for i in range(2):
+        one = geometry.local_coordinates_deg(v[i], c[i])
+        assert (float(lx[i]), float(ly[i])) == (float(one[0]), float(one[1]))
+
+
+def test_a_step_straight_up_off_centre_points_up_in_the_eyes_frame():
+    """The widening's 'true direction in the eye's frame at its start': a
+    step up from (30, 0) is 90 degrees, as at the centre."""
+    start = geometry.directions(np.array([30.0, 0.0]))
+    lx, ly = geometry.local_coordinates_deg(geometry.directions(np.array([30.0, 2.0])), start)
+    assert float(geometry.wrap_360_deg(np.degrees(np.arctan2(ly, lx)))) == pytest.approx(90.0, abs=1e-9)
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/eye/detect/test_true_angle_geometry.py -q -p no:cacheprovider
```

Expected: `1 error`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/eye/detect/geometry.py b/wl_preproc/eye/detect/geometry.py
new file mode 100644
index 0000000..33ba0b4
--- /dev/null
+++ b/wl_preproc/eye/detect/geometry.py
@@ -0,0 +1,95 @@
+"""True visual angle between gaze positions given in per-axis degrees.
+
+**Gaze here is in per-axis degrees**: a point `(X, Y)` cm from straight ahead
+at distance `D` is `(atan(X/D), atan(Y/D))`, the convention wl-xcon draws in
+(`viewport.center`) and our calibration fits to. Distances taken in those
+coordinates shrink off centre -- a 2-degree step up from (20, 0) is 1.879
+degrees of visual angle -- so a size, speed or direction that means what it
+says is measured between DIRECTIONS (design spec
+`2026-10-30-true-angle-saccades-design.md` section 2).
+
+These are wl-xcon's definitions to the formula (its `geometry.separation_deg`,
+`polar_deg` and `viewport.local_true_angle`, at its `main` `642ce31`), so its
+online detector and eye records match ours to the sample. Nothing else in this
+repository computes them.
+"""
+
+from __future__ import annotations
+
+import numpy as np
+
+#: The two settings of the shared `eye_detection` paramset key `geometry`
+#: (design spec section 3).
+TRUE_ANGLE = "true-angle"
+PER_AXIS = "per-axis"
+GEOMETRIES = (TRUE_ANGLE, PER_AXIS)
+#: What a default paramset registers.
+DEFAULT_GEOMETRY = TRUE_ANGLE
+#: What a paramset WITHOUT the key means: every one written before the key
+#: existed was detected per axis, so a detector's own params default to it.
+UNSTATED_GEOMETRY = PER_AXIS
+
+
+def directions(xy_deg: np.ndarray) -> np.ndarray:
+    """Unit directions, `(..., 2)` per-axis degrees to `(..., 3)`.
+
+    `normalize(tan x, tan y, 1)`, computed as wl-xcon's `separation_deg`
+    computes it, `(sin x cos y, cos x sin y, cos x cos y)`, normalized: the
+    same direction for every position a screen shows, without `tan`'s blow-up
+    near 90 degrees. The third component is positive for every such position,
+    so no direction is ever along the x axis, where the local frame below
+    would have no rightward axis. NaN in, NaN out."""
+    rad = np.radians(np.asarray(xy_deg, dtype=float))
+    x, y = rad[..., 0], rad[..., 1]
+    d = np.stack([np.sin(x) * np.cos(y), np.cos(x) * np.sin(y), np.cos(x) * np.cos(y)], axis=-1)
+    return d / np.linalg.norm(d, axis=-1, keepdims=True)
+
+
+def true_angle_deg(u: np.ndarray, v: np.ndarray) -> np.ndarray:
+    """The angle between directions, `atan2(|u x v|, u . v)` in degrees:
+    exact at small angles, where `acos` of a dot product loses its digits."""
+    return np.degrees(np.arctan2(np.linalg.norm(np.cross(u, v), axis=-1), np.sum(u * v, axis=-1)))
+
+
+def wrap_360_deg(angle_deg) -> np.ndarray:
+    """An angle in `[0, 360)` (amendment 1, "0 to 360 in both repos"): mod
+    360, and 0 where that rounds to 360, as a tiny negative angle does
+    (-1e-15 mod 360 is 360.0 in float64). Adding 0.0 turns a -0.0 into 0.0,
+    which a JSON file would otherwise write with its sign. NaN stays NaN."""
+    wrapped = np.mod(np.asarray(angle_deg, dtype=float), 360.0)
+    return np.where(wrapped >= 360.0, 0.0, wrapped) + 0.0
+
+
+def eccentricity_polar_deg(xy_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
+    """Each position's eccentricity, `atan(sqrt(tan^2 x + tan^2 y))`, and
+    polar angle, `atan2(tan y, tan x)` counter-clockwise from rightward in
+    `[0, 360)`. **The polar angle is NaN at zero eccentricity** ("Empty at
+    center"): `atan2(0, 0)` would say rightward. Computed from the direction,
+    whose first two components have the signs and ratio of `tan x` and
+    `tan y`."""
+    d = directions(xy_deg)
+    across, up, ahead = d[..., 0], d[..., 1], d[..., 2]
+    eccentricity = np.degrees(np.arctan2(np.hypot(across, up), ahead))
+    polar = wrap_360_deg(np.degrees(np.arctan2(up, across)))
+    return eccentricity, np.where(eccentricity == 0.0, np.nan, polar)
+
+
+def local_coordinates_deg(v: np.ndarray, c: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
+    """Directions `v`'s azimuthal-equidistant coordinates about `c`, degrees
+    along `c`'s own rightward and upward axes (wl-xcon's
+    `viewport.local_true_angle`, the same formula, broadcast so that each `v`
+    may have its own `c`): `ex = normalize(x - (x.c)c)`, `ey = c x ex`, and
+    `v` at `angle(c, v)` from the origin in the direction of its tangent
+    `t = v - (v.c)c`. A distance from `c` is exact and a direction about it
+    true; at the centre `ex` is rightward and `ey` upward. A tangent of zero
+    length is the origin. `v` and `c` are `(..., 3)`, broadcast together."""
+    c = np.asarray(c, dtype=float)
+    ex = np.array([1.0, 0.0, 0.0]) - c[..., 0:1] * c
+    ex = ex / np.linalg.norm(ex, axis=-1, keepdims=True)
+    ey = np.cross(c, ex)
+    cos_t = np.clip(np.sum(v * c, axis=-1), -1.0, 1.0)
+    t = v - cos_t[..., None] * c
+    tn = np.linalg.norm(t, axis=-1)
+    theta = np.degrees(np.arctan2(tn, cos_t))
+    safe = np.where(tn > 0, tn, 1.0)
+    return theta * np.sum(t * ex, axis=-1) / safe, theta * np.sum(t * ey, axis=-1) / safe
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/eye/detect/test_true_angle_geometry.py -q -p no:cacheprovider
```

Expected: `12 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/eye/detect -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `471 passed, 72 skipped, 1 xfailed`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "true-angle-arguments-swapped",
  "wl_preproc/eye/detect/geometry.py",
  "np.arctan2(np.linalg.norm(np.cross(u, v), axis=-1), np.sum(u * v, axis=-1))",
  "np.arctan2(np.sum(u * v, axis=-1), np.linalg.norm(np.cross(u, v), axis=-1))",
  "tests/eye/detect/test_true_angle_geometry.py"
 ],
 [
  "no-rounding-to-360-fix",
  "wl_preproc/eye/detect/geometry.py",
  "return np.where(wrapped >= 360.0, 0.0, wrapped) + 0.0",
  "return wrapped + 0.0",
  "tests/eye/detect/test_true_angle_geometry.py"
 ],
 [
  "polar-angle-at-centre-defined",
  "wl_preproc/eye/detect/geometry.py",
  "return eccentricity, np.where(eccentricity == 0.0, np.nan, polar)",
  "return eccentricity, polar",
  "tests/eye/detect/test_true_angle_geometry.py"
 ],
 [
  "upward-axis-flipped",
  "wl_preproc/eye/detect/geometry.py",
  "    ey = np.cross(c, ex)",
  "    ey = np.cross(ex, c)",
  "tests/eye/detect/test_true_angle_geometry.py"
 ],
 [
  "directions-not-normalized",
  "wl_preproc/eye/detect/geometry.py",
  "    return d / np.linalg.norm(d, axis=-1, keepdims=True)",
  "    return d",
  "tests/eye/detect/test_true_angle_geometry.py"
 ],
 [
  "zero-tangent-divides",
  "wl_preproc/eye/detect/geometry.py",
  "    safe = np.where(tn > 0, tn, 1.0)",
  "    safe = tn",
  "tests/eye/detect/test_true_angle_geometry.py"
 ],
 [
  "polar-angle-not-wrapped",
  "wl_preproc/eye/detect/geometry.py",
  "    polar = wrap_360_deg(np.degrees(np.arctan2(up, across)))",
  "    polar = np.degrees(np.arctan2(up, across))",
  "tests/eye/detect/test_true_angle_geometry.py"
 ]
]
```

Expected: 7 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(eye): the geometry of true visual angle, wl-xcon's definitions to the formula -- a position's direction, the true angle between two, eccentricity and polar angle, the local frame at a direction, every angle in [0, 360) (design spec 2026-10-30-true-angle-saccades-design.md, section 2)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 2: Measurement takes the geometry

§4 and §5, decision 3: `amplitude`, `measure` and `measure_event_run` take `geometry`, with no default; under true angle the size is the angle between the two samples' directions, the direction the end's in the eye's frame at the start, and the peak speed the largest magnitude of the velocity handed in; each endpoint's eccentricity and polar angle under both; `measure_positions`, a fixation's measurement without size or speed; `velocity.local_velocity`. A per-axis direction moves to [0, 360). Every caller passes the setting: Engbert-Kliegl, Otero-Millan and Nyström-Holmqvist through a `geometry` field defaulting to per-axis (amendment 3), BMD likewise, its gate taking BMD's own (amendment 2), the two-eye label and `_insert_trace` from the paramset dict, read once and an unknown value refused (`_geometry_of`, amendment 7). Nothing registers the key yet, so every stored row is as before but its direction's range.

**Files:**
- Modify: `wl_preproc/eye/detect/measure.py`, `velocity.py`, `engbert_kliegl.py`, `otero_millan.py`, `nystrom_holmqvist.py`, `bmd.py`, `contract_fixtures.py`
- Modify: `wl_preproc/schema/detect.py` (`make`, `_insert_trace`, `_conjunction_label`)
- Test: `tests/eye/detect/test_measure.py`, `test_velocity.py`, `test_bmd.py`, `test_otero_millan.py`, `test_nystrom_holmqvist.py`, `test_engbert_kliegl.py`, `test_contract_fixtures.py`, `test_otero_millan_validation.py`, `test_bmd_validation.py`, `tests/schema/test_detect_populate.py`

**Interfaces:**
- Consumes: Task 1's `geometry`.
- Produces: `measure.amplitude(gaze, start, stop, *, geometry)`; `measure(gaze, velocity, start, stop, fs_hz, *,
  geometry)`; `measure_event_run(..., fs_hz, *, geometry, runs_end_before_landing, ...)`;
  `measure_positions(gaze, start, stop, fs_hz, *, geometry)`; `Measurement` gains `start_eccentricity_deg`,
  `start_polar_deg`, `end_eccentricity_deg`, `end_polar_deg`, and its size and speed may be None;
  `velocity.local_velocity(gaze_deg, fs_hz)`; a `geometry: str = UNSTATED_GEOMETRY` field on
  `EngbertKlieglParams`, `OteroMillanParams`, `NystromHolmqvistParams`, `BmdParams`;
  `EyeDetection._insert_trace(..., *, geometry)`; `schema.detect._geometry_of(params) -> str`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/eye/detect/test_bmd.py b/tests/eye/detect/test_bmd.py
index 429658d..8be5174 100644
--- a/tests/eye/detect/test_bmd.py
+++ b/tests/eye/detect/test_bmd.py
@@ -372,3 +372,40 @@ def test_a_bmd_event_of_the_cut_or_more_is_a_saccade(monkeypatch):
     assert events_found == [(200, 206, Label.MICROSACCADE, 1.0), (600, 606, Label.SACCADE, 1.0),
                             (800, 806, Label.SACCADE, 1.0)]
     assert np.hypot(*(x[805] - x[800])) < 1.0 <= np.hypot(*(x[805] - x[799]))
+
+
+def test_under_true_angle_the_saccades_are_still_engbert_kliegls():
+    """True-angle spec amendment 2: the gate is Engbert-Kliegl as registered,
+    so in the same geometry, and `DetectorAgreement` may go on leaving its
+    saccades out of the pair. A 1.2-degree horizontal step 40 degrees up is a
+    saccade per axis and about 0.92 degrees of true angle: neither copies it."""
+    from wl_preproc.eye.detect.geometry import PER_AXIS, TRUE_ANGLE
+    from wl_preproc.eye.detect.velocity import local_velocity
+
+    x = _drifting_trace(3.0, 13, steps=[(1.0, 1.2)]) + np.array([0.0, 40.0])
+    v = local_velocity(x, FS)
+
+    def gate(geometry):
+        params = EngbertKlieglParams(lambda_=LIGHT.gate_lambda, min_duration_samples=LIGHT.gate_min_duration_samples,
+                                     geometry=geometry)
+        return [(r.start, r.stop) for r in detect_engbert_kliegl(x, v, _open(len(x)), FS, params)
+                if r.label is Label.SACCADE]
+
+    runs = bmd.detect_bmd(x, v, _open(len(x)), FS, dataclasses.replace(LIGHT, geometry=TRUE_ANGLE))
+    copied = [(r.start, r.stop) for r in runs if r.label is Label.SACCADE and r.reliability is None]
+    assert gate(PER_AXIS) and copied == gate(TRUE_ANGLE) == []
+
+
+def test_bmds_own_event_is_split_by_the_settings_size():
+    """True-angle spec section 6.2: BMD's own event, measured from its
+    take-off sample, is a saccade at the cut or above in the setting's
+    geometry. 1.12 degrees up from (30, 0) is 0.970 degrees of true angle."""
+    from wl_preproc.eye.detect.geometry import PER_AXIS, TRUE_ANGLE
+    from wl_preproc.eye.detect.labels import Run
+
+    gaze = np.tile([30.0, 0.0], (30, 1))
+    gaze[10:] = [30.0, 1.12]
+    event = Run(start=10, stop=14, label=Label.MICROSACCADE, reliability=0.9)
+    label = {setting: bmd._by_size(event, gaze, np.zeros_like(gaze), _open(30), FS, 1.0, setting).label
+             for setting in (PER_AXIS, TRUE_ANGLE)}
+    assert label == {PER_AXIS: Label.SACCADE, TRUE_ANGLE: Label.MICROSACCADE}
diff --git a/tests/eye/detect/test_bmd_validation.py b/tests/eye/detect/test_bmd_validation.py
index e74950f..a02587d 100644
--- a/tests/eye/detect/test_bmd_validation.py
+++ b/tests/eye/detect/test_bmd_validation.py
@@ -19,6 +19,7 @@ from wl_preproc.eye.detect import bmd
 from wl_preproc.eye.detect.bmd_table import compute_table
 from wl_preproc.eye.detect.consensus import cohen_kappa
 from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS, detect_engbert_kliegl
+from wl_preproc.eye.detect.geometry import PER_AXIS
 from wl_preproc.eye.detect.labels import Label
 from wl_preproc.eye.detect.velocity import velocity
 
@@ -192,7 +193,7 @@ def test_bmds_own_events_on_the_recording_are_split_at_the_cut(recording, eye, c
         if r.reliability is not None:
             sizes[r.label].append(measure_event_run(
                 gaze, v, mask, r.start, r.stop, rec.fs_hz, runs_end_before_landing=False,
-                min_measured_ms=None, runs_start_after_takeoff=True).amplitude_deg)
+                min_measured_ms=None, runs_start_after_takeoff=True, geometry=PER_AXIS).amplitude_deg)
     with capsys.disabled():
         print(f"\n  {eye}: BMD's own events over {COMPARISON_SAMPLES} samples: "
               f"{len(sizes[Label.MICROSACCADE])} microsaccades, {len(sizes[Label.SACCADE])} saccades")
diff --git a/tests/eye/detect/test_contract_fixtures.py b/tests/eye/detect/test_contract_fixtures.py
index 39b6b09..4caad8f 100644
--- a/tests/eye/detect/test_contract_fixtures.py
+++ b/tests/eye/detect/test_contract_fixtures.py
@@ -25,6 +25,7 @@ from wl_preproc.eye.detect.engbert_kliegl import (
     DEFAULT_EK_PARAMS, DEFAULT_LAMBDA, DEFAULT_MIN_DURATION_SAMPLES, _median_scale, detect_engbert_kliegl,
     thresholds,
 )
+from wl_preproc.eye.detect.geometry import PER_AXIS
 from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude
 from wl_preproc.eye.detect.velocity import velocity
 
@@ -71,7 +72,7 @@ def test_a_reader_of_v1_gets_its_outputs_back_from_its_inputs():
         # (CI on 10ea913, 8.001592662084843 against the file's ...844). Everything else here is
         # arithmetic and `sqrt`, which IEEE 754 rounds the same everywhere, so it is held exactly.
         for r, run in zip(runs, case["runs"], strict=True):
-            assert amplitude(gaze, r.start, r.stop) == pytest.approx(run["amplitude_deg"], rel=1e-12, abs=0.0), (
+            assert amplitude(gaze, r.start, r.stop, geometry=PER_AXIS) == pytest.approx(run["amplitude_deg"], rel=1e-12, abs=0.0), (
                 case["name"])
 
 
@@ -134,7 +135,7 @@ def test_a_reader_gets_every_threshold_back():
         assert [(r.start, r.stop, r.label.value) for r in runs] == [
             (run["start"], run["stop"], run["label"]) for run in case["runs"]], case["name"]
         for r, run in zip(runs, case["runs"], strict=True):
-            assert amplitude(gaze, r.start, r.stop) == pytest.approx(run["amplitude_deg"], rel=1e-12, abs=0.0)
+            assert amplitude(gaze, r.start, r.stop, geometry=PER_AXIS) == pytest.approx(run["amplitude_deg"], rel=1e-12, abs=0.0)
 
 
 def test_the_near_threshold_traces_catch_a_threshold_off_by_five_percent_or_a_wider_ellipse():
diff --git a/tests/eye/detect/test_engbert_kliegl.py b/tests/eye/detect/test_engbert_kliegl.py
index a20508c..2fd75ba 100644
--- a/tests/eye/detect/test_engbert_kliegl.py
+++ b/tests/eye/detect/test_engbert_kliegl.py
@@ -4,6 +4,7 @@ import pytest
 from wl_preproc.eye.detect.engbert_kliegl import (
     DEFAULT_EK_PARAMS, EngbertKlieglParams, detect_engbert_kliegl,
 )
+from wl_preproc.eye.detect.geometry import PER_AXIS
 from wl_preproc.eye.detect.labels import Label, Run
 
 FS_HZ = 500.0
@@ -313,7 +314,7 @@ def test_each_label_is_what_classify_gives_for_that_intervals_own_amplitude():
 
     assert found
     for run in found:
-        expected = classify(amplitude(gaze, run.start, run.stop), MICROSACCADE_MAX_DEG)
+        expected = classify(amplitude(gaze, run.start, run.stop, geometry=PER_AXIS), MICROSACCADE_MAX_DEG)
         assert run.label is expected
     # Not vacuous: both sides of the split actually occur above.
     assert {run.label for run in found} == {Label.SACCADE, Label.MICROSACCADE}
diff --git a/tests/eye/detect/test_measure.py b/tests/eye/detect/test_measure.py
index d046edc..da201af 100644
--- a/tests/eye/detect/test_measure.py
+++ b/tests/eye/detect/test_measure.py
@@ -3,9 +3,11 @@ import math
 import numpy as np
 import pytest
 
+from wl_preproc.eye.detect import geometry
+from wl_preproc.eye.detect.geometry import PER_AXIS, TRUE_ANGLE
 from wl_preproc.eye.detect.labels import Label
 from wl_preproc.eye.detect.measure import (
-    MICROSACCADE_MAX_DEG, classify, measure, measure_event_run,
+    MICROSACCADE_MAX_DEG, amplitude, classify, measure, measure_event_run, measure_positions,
 )
 
 
@@ -21,14 +23,14 @@ def test_amplitude_is_the_displacement_across_the_interval():
     gaze = np.column_stack([[0.0, 1.0, 3.0, 2.0, 4.0], np.zeros(5)])
     velocity = np.zeros((5, 2))
 
-    result = measure(gaze, velocity, start=0, stop=5, fs_hz=500.0)
+    result = measure(gaze, velocity, start=0, stop=5, fs_hz=500.0, geometry=PER_AXIS)
 
     assert result.amplitude_deg == pytest.approx(4.0)
 
 
 def test_amplitude_is_euclidean_across_both_axes():
     gaze = np.array([[0.0, 0.0], [3.0, 4.0]])
-    result = measure(gaze, np.zeros((2, 2)), start=0, stop=2, fs_hz=500.0)
+    result = measure(gaze, np.zeros((2, 2)), start=0, stop=2, fs_hz=500.0, geometry=PER_AXIS)
     assert result.amplitude_deg == pytest.approx(5.0)
 
 
@@ -41,14 +43,14 @@ def test_peak_velocity_is_the_maximum_speed_inside_the_interval_only():
     velocity[4] = [80.0, 60.0]      # speed 100, inside
     velocity[8] = [400.0, 0.0]      # outside
 
-    result = measure(np.zeros((10, 2)), velocity, start=1, stop=6, fs_hz=500.0)
+    result = measure(np.zeros((10, 2)), velocity, start=1, stop=6, fs_hz=500.0, geometry=PER_AXIS)
 
     assert result.peak_velocity_deg_s == pytest.approx(100.0)
 
 
 def test_duration_counts_samples_not_endpoints():
     """`stop` is exclusive, so a 6-sample event at 500 Hz lasts 12 ms."""
-    result = measure(np.zeros((20, 2)), np.zeros((20, 2)), start=4, stop=10, fs_hz=500.0)
+    result = measure(np.zeros((20, 2)), np.zeros((20, 2)), start=4, stop=10, fs_hz=500.0, geometry=PER_AXIS)
     assert result.duration_s == pytest.approx(6 / 500.0)
 
 
@@ -69,9 +71,9 @@ def test_measure_requires_stop_greater_than_start():
     """Empty intervals silently read nonsensical indices; measure rejects them
     explicitly."""
     with pytest.raises(ValueError, match="stop > start.*start=2.*stop=2"):
-        measure(np.zeros((5, 2)), np.zeros((5, 2)), start=2, stop=2, fs_hz=500.0)
+        measure(np.zeros((5, 2)), np.zeros((5, 2)), start=2, stop=2, fs_hz=500.0, geometry=PER_AXIS)
     with pytest.raises(ValueError, match="stop > start.*start=5.*stop=3"):
-        measure(np.zeros((5, 2)), np.zeros((5, 2)), start=5, stop=3, fs_hz=500.0)
+        measure(np.zeros((5, 2)), np.zeros((5, 2)), start=5, stop=3, fs_hz=500.0, geometry=PER_AXIS)
 
 
 # -- `measure_event_run`: a detector's declared measurement rule ------------------
@@ -108,9 +110,9 @@ def test_with_no_rule_declared_a_run_is_measured_as_measure_measures_it(start, s
     velocity = np.random.default_rng(7).normal(0.0, 50.0, gaze.shape)
 
     got = measure_event_run(gaze, velocity, _offered(40), start, stop, 500.0,
-                            runs_end_before_landing=False, min_measured_ms=None)
+                            runs_end_before_landing=False, min_measured_ms=None, geometry=PER_AXIS)
 
-    assert got == measure(gaze, velocity, start, stop, 500.0)
+    assert got == measure(gaze, velocity, start, stop, 500.0, geometry=PER_AXIS)
 
 
 def test_a_run_ending_before_its_landing_is_measured_to_the_landing_sample():
@@ -122,11 +124,11 @@ def test_a_run_ending_before_its_landing_is_measured_to_the_landing_sample():
     velocity[17] = [900.0, 0.0]   # the sample after it: outside
 
     got = measure_event_run(gaze, velocity, _offered(40), 10, 16, 500.0,
-                            runs_end_before_landing=True, min_measured_ms=None)
+                            runs_end_before_landing=True, min_measured_ms=None, geometry=PER_AXIS)
 
     displacement = gaze[16] - gaze[10]
     assert got.amplitude_deg == float(np.hypot(displacement[0], displacement[1]))
-    assert got.amplitude_deg != measure(gaze, velocity, 10, 16, 500.0).amplitude_deg
+    assert got.amplitude_deg != measure(gaze, velocity, 10, 16, 500.0, geometry=PER_AXIS).amplitude_deg
     assert got.peak_velocity_deg_s == 300.0
     assert got.duration_s == 6 / 500.0
 
@@ -136,9 +138,9 @@ def test_a_one_sample_run_reads_its_whole_step_rather_than_zero():
     gaze = _walk()
 
     got = measure_event_run(gaze, _speeds(), _offered(40), 10, 11, 500.0,
-                            runs_end_before_landing=True, min_measured_ms=None)
+                            runs_end_before_landing=True, min_measured_ms=None, geometry=PER_AXIS)
 
-    assert measure(gaze, _speeds(), 10, 11, 500.0).amplitude_deg == 0.0
+    assert measure(gaze, _speeds(), 10, 11, 500.0, geometry=PER_AXIS).amplitude_deg == 0.0
     displacement = gaze[11] - gaze[10]
     assert got.amplitude_deg == float(np.hypot(displacement[0], displacement[1])) > 0.0
 
@@ -158,9 +160,9 @@ def test_the_landing_sample_is_used_only_at_an_interior_knot(case):
         gaze[stop, 1] = np.nan
 
     got = measure_event_run(gaze, _speeds(), offered, start, stop, 500.0,
-                            runs_end_before_landing=True, min_measured_ms=None)
+                            runs_end_before_landing=True, min_measured_ms=None, geometry=PER_AXIS)
 
-    assert got == measure(gaze, _speeds(), start, stop, 500.0)
+    assert got == measure(gaze, _speeds(), start, stop, 500.0, geometry=PER_AXIS)
 
 
 @pytest.mark.parametrize("landing", [True, False])
@@ -173,7 +175,7 @@ def test_a_run_shorter_than_the_floor_is_not_measured(landing, fs_hz, measured_f
     gaze = _walk()
     for length in range(1, 13):
         got = measure_event_run(gaze, _speeds(), _offered(40), 10, 10 + length, fs_hz,
-                                runs_end_before_landing=landing, min_measured_ms=10.0)
+                                runs_end_before_landing=landing, min_measured_ms=10.0, geometry=PER_AXIS)
         if length < measured_from:
             assert got is None, length
         else:
@@ -195,11 +197,11 @@ def test_a_run_starting_after_its_takeoff_is_measured_from_the_takeoff_sample():
 
     got = measure_event_run(gaze, velocity, _offered(40), 10, 16, 500.0,
                             runs_end_before_landing=False, min_measured_ms=None,
-                            runs_start_after_takeoff=True)
+                            runs_start_after_takeoff=True, geometry=PER_AXIS)
 
     displacement = gaze[15] - gaze[9]
     assert got.amplitude_deg == float(np.hypot(displacement[0], displacement[1]))
-    assert got.amplitude_deg != measure(gaze, velocity, 10, 16, 500.0).amplitude_deg
+    assert got.amplitude_deg != measure(gaze, velocity, 10, 16, 500.0, geometry=PER_AXIS).amplitude_deg
     assert got.peak_velocity_deg_s == 300.0
     assert got.duration_s == 6 / 500.0
 
@@ -209,9 +211,9 @@ def test_a_one_sample_run_reads_its_take_off_step_rather_than_zero():
 
     got = measure_event_run(gaze, _speeds(), _offered(40), 10, 11, 500.0,
                             runs_end_before_landing=False, min_measured_ms=None,
-                            runs_start_after_takeoff=True)
+                            runs_start_after_takeoff=True, geometry=PER_AXIS)
 
-    assert measure(gaze, _speeds(), 10, 11, 500.0).amplitude_deg == 0.0
+    assert measure(gaze, _speeds(), 10, 11, 500.0, geometry=PER_AXIS).amplitude_deg == 0.0
     displacement = gaze[10] - gaze[9]
     assert got.amplitude_deg == float(np.hypot(displacement[0], displacement[1])) > 0.0
 
@@ -231,9 +233,9 @@ def test_the_takeoff_sample_is_used_only_when_it_was_offered(case):
 
     got = measure_event_run(gaze, _speeds(), offered, start, stop, 500.0,
                             runs_end_before_landing=False, min_measured_ms=None,
-                            runs_start_after_takeoff=True)
+                            runs_start_after_takeoff=True, geometry=PER_AXIS)
 
-    assert got == measure(gaze, _speeds(), start, stop, 500.0)
+    assert got == measure(gaze, _speeds(), start, stop, 500.0, geometry=PER_AXIS)
 
 
 # -- Saccade geometry (design spec `2026-09-28-saccade-geometry-design.md`) -----
@@ -243,14 +245,15 @@ def test_the_takeoff_sample_is_used_only_when_it_was_offered(case):
 
 
 @pytest.mark.parametrize("dx, dy, direction", [
-    (1.0, 0.0, 0.0), (0.0, 1.0, 90.0), (-1.0, 0.0, 180.0), (0.0, -1.0, -90.0), (1.0, 1.0, 45.0),
+    (1.0, 0.0, 0.0), (0.0, 1.0, 90.0), (-1.0, 0.0, 180.0), (0.0, -1.0, 270.0), (1.0, 1.0, 45.0), (1.0, -1.0, 315.0),
 ])
 def test_the_direction_is_counterclockwise_from_rightward_in_degrees(dx, dy, direction):
     """Spec 1.2: 0 deg is rightward and 90 deg upward, in the calibrated
-    frame (positive x rightward, positive y upward)."""
+    frame (positive x rightward, positive y upward); in [0, 360), so straight
+    down is 270, never -90 (true-angle spec, amendment 1)."""
     gaze = np.zeros((5, 2))
     gaze[2:] = [dx, dy]
-    got = measure(gaze, np.zeros((5, 2)), 1, 4, 500.0)
+    got = measure(gaze, np.zeros((5, 2)), 1, 4, 500.0, geometry=PER_AXIS)
     assert got.direction_deg == pytest.approx(direction, abs=1e-12)
 
 
@@ -258,11 +261,12 @@ def test_the_positions_are_the_two_samples_the_amplitude_reads():
     """Spec 1.1: start `gaze[start]`, end `gaze[stop - 1]`, and the amplitude
     is exactly the distance between them."""
     gaze = _walk()
-    got = measure(gaze, _speeds(), 10, 16, 500.0)
+    got = measure(gaze, _speeds(), 10, 16, 500.0, geometry=PER_AXIS)
     assert (got.start_x_deg, got.start_y_deg) == tuple(gaze[10])
     assert (got.end_x_deg, got.end_y_deg) == tuple(gaze[15])
     assert got.amplitude_deg == float(np.hypot(got.end_x_deg - got.start_x_deg, got.end_y_deg - got.start_y_deg))
-    assert got.direction_deg == math.degrees(math.atan2(gaze[15, 1] - gaze[10, 1], gaze[15, 0] - gaze[10, 0]))
+    assert got.direction_deg == float(geometry.wrap_360_deg(
+        math.degrees(math.atan2(gaze[15, 1] - gaze[10, 1], gaze[15, 0] - gaze[10, 0]))))
 
 
 @pytest.mark.parametrize("rule, first, last", [("landing", 10, 16), ("take-off", 9, 15)])
@@ -272,7 +276,7 @@ def test_each_detectors_rule_moves_the_positions_with_the_amplitude(rule, first,
     gaze = _walk()
     got = measure_event_run(gaze, _speeds(), _offered(40), 10, 16, 500.0,
                             runs_end_before_landing=rule == "landing", min_measured_ms=None,
-                            runs_start_after_takeoff=rule == "take-off")
+                            runs_start_after_takeoff=rule == "take-off", geometry=PER_AXIS)
     assert (got.start_x_deg, got.start_y_deg) == tuple(gaze[first])
     assert (got.end_x_deg, got.end_y_deg) == tuple(gaze[last])
     assert got.amplitude_deg == float(np.hypot(got.end_x_deg - got.start_x_deg, got.end_y_deg - got.start_y_deg))
@@ -282,7 +286,127 @@ def test_each_detectors_rule_moves_the_positions_with_the_amplitude(rule, first,
 def test_a_zero_displacement_has_positions_and_no_direction():
     """Spec 1.2: where start equals end, the direction is NULL, never 0 deg."""
     gaze = np.full((5, 2), 2.5)
-    got = measure(gaze, np.zeros((5, 2)), 0, 5, 500.0)
+    got = measure(gaze, np.zeros((5, 2)), 0, 5, 500.0, geometry=PER_AXIS)
     assert got.amplitude_deg == 0.0
     assert got.direction_deg is None
     assert (got.start_x_deg, got.start_y_deg, got.end_x_deg, got.end_y_deg) == (2.5, 2.5, 2.5, 2.5)
+
+
+# -- True visual angle (design spec `2026-10-30-true-angle-saccades-design.md`) --
+
+
+def _step(start, end, n=10, at=5):
+    gaze = np.tile(np.asarray(start, dtype=float), (n, 1))
+    gaze[at:] = end
+    return gaze
+
+
+@pytest.mark.parametrize(("start", "end", "true", "per_axis"), [
+    ((20.0, 0.0), (20.0, 2.0), 1.879, 2.0),
+    ((30.0, 0.0), (30.0, 2.0), 1.732, 2.0),
+    ((0.0, 0.0), (8.0, 0.0), 8.0, 8.0),
+])
+def test_the_size_is_the_setting_s(start, end, true, per_axis):
+    """Section 4: under true angle, the angle between the two samples'
+    directions; under per-axis, hypot, as v1."""
+    gaze = _step(start, end)
+    v = np.zeros_like(gaze)
+    assert measure(gaze, v, 0, 10, 500.0, geometry=TRUE_ANGLE).amplitude_deg == pytest.approx(true, abs=5e-4)
+    assert measure(gaze, v, 0, 10, 500.0, geometry=PER_AXIS).amplitude_deg == pytest.approx(per_axis, rel=1e-15)
+
+
+def test_size_is_one_function():
+    """What a detector reads mid-detection is what is stored (decision 3)."""
+    gaze = _step((30.0, 0.0), (31.0, 1.0))
+    for setting in (TRUE_ANGLE, PER_AXIS):
+        assert amplitude(gaze, 0, 10, geometry=setting) == \
+            measure(gaze, np.zeros_like(gaze), 0, 10, 500.0, geometry=setting).amplitude_deg
+
+
+def test_under_true_angle_the_direction_is_the_eyes_at_its_start():
+    """Section 4: atan2 of the end's local coordinates about the start. A
+    step straight up off centre is 90 under both; an oblique one bends."""
+    up = _step((30.0, 0.0), (30.0, 2.0))
+    assert measure(up, np.zeros_like(up), 0, 10, 500.0, geometry=TRUE_ANGLE).direction_deg == \
+        pytest.approx(90.0, abs=1e-9)
+    oblique = _step((30.0, 20.0), (32.0, 22.0))
+    lx, ly = geometry.local_coordinates_deg(geometry.directions(np.array([32.0, 22.0])),
+                                            geometry.directions(np.array([30.0, 20.0])))
+    got = measure(oblique, np.zeros_like(oblique), 0, 10, 500.0, geometry=TRUE_ANGLE).direction_deg
+    assert got == float(geometry.wrap_360_deg(np.degrees(np.arctan2(ly, lx))))
+    assert abs(got - 45.0) > 1.0
+
+
+def test_under_true_angle_a_downward_step_is_270():
+    """On the horizontal meridian, where the screen's vertical is the eye's;
+    above or below it, a screen-vertical step leans in the eye's frame."""
+    gaze = _step((20.0, 0.0), (20.0, -2.0))
+    assert measure(gaze, np.zeros_like(gaze), 0, 10, 500.0, geometry=TRUE_ANGLE).direction_deg == \
+        pytest.approx(270.0, abs=1e-9)
+
+
+def test_a_zero_displacement_has_no_direction_under_true_angle_either():
+    gaze = np.full((5, 2), 12.5)
+    got = measure(gaze, np.zeros((5, 2)), 0, 5, 500.0, geometry=TRUE_ANGLE)
+    assert got.amplitude_deg == 0.0
+    assert got.direction_deg is None
+
+
+def test_near_the_centre_the_settings_agree():
+    """Section 11: within the error of the small-angle approximation."""
+    gaze = _step((0.1, -0.2), (0.4, 0.1))
+    v = np.zeros_like(gaze)
+    true, per_axis = (measure(gaze, v, 0, 10, 500.0, geometry=g) for g in (TRUE_ANGLE, PER_AXIS))
+    assert true.amplitude_deg == pytest.approx(per_axis.amplitude_deg, rel=1e-5)
+    assert true.direction_deg == pytest.approx(per_axis.direction_deg, abs=1e-3)
+
+
+@pytest.mark.parametrize("setting", [TRUE_ANGLE, PER_AXIS])
+def test_the_endpoints_carry_their_eccentricity_and_polar_angle_under_both(setting):
+    """Facts of the positions, the same under either setting; the polar angle
+    is undefined at the centre ("Empty at center")."""
+    gaze = _step((0.0, 0.0), (0.0, -10.0))
+    got = measure(gaze, np.zeros_like(gaze), 0, 10, 500.0, geometry=setting)
+    assert (got.start_eccentricity_deg, got.start_polar_deg) == (0.0, None)
+    assert got.end_eccentricity_deg == pytest.approx(10.0, rel=1e-14)
+    assert got.end_polar_deg == pytest.approx(270.0, abs=1e-12)
+
+
+def test_the_peak_speed_is_the_largest_magnitude_of_the_velocity_it_is_handed():
+    """Under true angle the caller hands it the local-frame velocity
+    (section 5); the magnitude is the same formula under both."""
+    v = np.zeros((10, 2))
+    v[4] = [30.0, 40.0]
+    for setting in (TRUE_ANGLE, PER_AXIS):
+        assert measure(np.zeros((10, 2)), v, 0, 10, 500.0, geometry=setting).peak_velocity_deg_s == 50.0
+
+
+@pytest.mark.parametrize("setting", [TRUE_ANGLE, PER_AXIS])
+def test_a_fixation_is_measured_for_its_positions_only(setting):
+    """Decision 5: its positions, their eccentricity and polar angle, and its
+    direction in the setting, as an event's over the same samples; no size
+    and no peak speed."""
+    gaze = _step((30.0, 20.0), (30.3, 20.3))
+    got = measure_positions(gaze, 2, 9, 500.0, geometry=setting)
+    event = measure(gaze, np.zeros_like(gaze), 2, 9, 500.0, geometry=setting)
+    assert (got.amplitude_deg, got.peak_velocity_deg_s) == (None, None)
+    assert (got.start_x_deg, got.start_y_deg, got.end_x_deg, got.end_y_deg) == (30.0, 20.0, 30.3, 20.3)
+    assert got.direction_deg == event.direction_deg
+    assert (got.start_eccentricity_deg, got.end_polar_deg) == (event.start_eccentricity_deg, event.end_polar_deg)
+    assert got.duration_s == 7 / 500.0
+
+
+def test_a_fixations_direction_bends_off_centre_under_true_angle():
+    gaze = _step((30.0, 20.0), (30.3, 20.3))
+    true, per_axis = (measure_positions(gaze, 2, 9, 500.0, geometry=g).direction_deg for g in (TRUE_ANGLE, PER_AXIS))
+    assert per_axis == pytest.approx(45.0, abs=1e-9) and abs(true - per_axis) > 1.0
+
+
+def test_a_one_sample_fixation_has_no_direction():
+    got = measure_positions(np.array([[1.0, 2.0]]), 0, 1, 500.0, geometry=TRUE_ANGLE)
+    assert got.direction_deg is None
+
+
+def test_an_unknown_geometry_is_refused():
+    with pytest.raises(ValueError, match="geometry"):
+        amplitude(np.zeros((5, 2)), 0, 5, geometry="cyclopean")
diff --git a/tests/eye/detect/test_nystrom_holmqvist.py b/tests/eye/detect/test_nystrom_holmqvist.py
index b06682a..b9fd31e 100644
--- a/tests/eye/detect/test_nystrom_holmqvist.py
+++ b/tests/eye/detect/test_nystrom_holmqvist.py
@@ -1356,3 +1356,33 @@ def test_a_recomputed_glissade_does_not_duplicate_an_already_claimed_one():
     sorted_runs = sorted(runs, key=lambda run: run.start)
     for earlier, later in zip(sorted_runs, sorted_runs[1:]):
         assert earlier.stop <= later.start, (earlier, later)
+
+
+def test_a_glissade_is_compared_with_its_saccade_in_the_settings_geometry():
+    """True-angle spec decision 3: a 2-degree saccade straight up at
+    (40, 0) is about 1.53 degrees of true angle, so the 1.8-degree
+    horizontal movement after it is smaller per axis and kept, larger in
+    true angle and omitted."""
+    import dataclasses
+
+    import numpy as np
+
+    from wl_preproc.eye.detect.geometry import TRUE_ANGLE
+    from wl_preproc.eye.detect.nystrom_holmqvist import (
+        DEFAULT_NH_PARAMS, PeakThreshold, _glissade_bounds,
+    )
+
+    fs = 500.0
+    speed = np.full(600, 2.0)
+    speed[300:320] = 300.0
+    speed[320:330] = 150.0
+    gaze = np.tile([40.0, 0.0], (600, 1))
+    gaze[300:320, 1] = np.linspace(0.0, 2.0, 20)
+    gaze[320:, 1] = 2.0
+    gaze[320:330, 0] = np.linspace(40.0, 41.8, 10)
+    gaze[330:, 0] = 41.8
+    thresholds = PeakThreshold(100.0, 20.0, 2, True)
+
+    assert _glissade_bounds(speed, gaze, 300, 320, 25.0, thresholds, fs, DEFAULT_NH_PARAMS) is not None
+    assert _glissade_bounds(speed, gaze, 300, 320, 25.0, thresholds, fs,
+                            dataclasses.replace(DEFAULT_NH_PARAMS, geometry=TRUE_ANGLE)) is None
diff --git a/tests/eye/detect/test_otero_millan.py b/tests/eye/detect/test_otero_millan.py
index 107dd99..09f1823 100644
--- a/tests/eye/detect/test_otero_millan.py
+++ b/tests/eye/detect/test_otero_millan.py
@@ -12,6 +12,7 @@ from __future__ import annotations
 import numpy as np
 import pytest
 
+from wl_preproc.eye.detect.geometry import PER_AXIS
 from wl_preproc.eye.detect.labels import Label
 from wl_preproc.eye.detect.otero_millan import (
     DEFAULT_OM_PARAMS,
@@ -624,7 +625,7 @@ def test_whitening_is_what_makes_a_small_event_findable_at_all():
     speed = np.hypot(v[:, 0], v[:, 1])
     spans = _candidate_spans(speed, np.ones(len(gaze), dtype=bool), 10)
     peak_velocity, onset, brake, displacement = _features(
-        gaze, speed, _acceleration(v), spans
+        gaze, speed, _acceleration(v), spans, geometry=PER_AXIS
     )
     raw = np.column_stack([
         _zscore(_log_finite(peak_velocity)), _zscore(_log_finite(onset)),
@@ -669,7 +670,7 @@ def test_the_silhouette_margin_is_what_chooses_the_cluster_count():
     speed = np.hypot(v[:, 0], v[:, 1])
     spans = _candidate_spans(speed, np.ones(len(gaze), dtype=bool), 10)
     peak_velocity, onset, brake, _displacement = _features(
-        gaze, speed, _acceleration(v), spans
+        gaze, speed, _acceleration(v), spans, geometry=PER_AXIS
     )
     whitened = _whiten(np.column_stack([
         _zscore(_log_finite(peak_velocity)), _zscore(_log_finite(onset)),
@@ -716,7 +717,7 @@ def test_the_onset_window_reaches_one_sample_past_the_velocity_peak():
     accel = np.array([1.0, 2.0, 3.0, 99.0, 4.0])         # max at index 3 == peak + 1
     gaze = np.column_stack([np.arange(5) * 0.5, np.zeros(5)])
 
-    _pv, onset, brake, _disp = _features(gaze, speed, accel, [(0, 5)])
+    _pv, onset, brake, _disp = _features(gaze, speed, accel, [(0, 5)], geometry=PER_AXIS)
 
     assert onset[0] == 99.0
     assert brake[0] == 99.0
@@ -839,7 +840,7 @@ def test_a_velocity_peak_on_an_events_last_sample_is_handled_at_both_ends():
     accel = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 999.0])     # 999 is OUTSIDE [0, 5)
     gaze = np.column_stack([np.arange(6) * 0.5, np.zeros(6)])
 
-    peak_velocity, onset, brake, _displacement = _features(gaze, speed, accel, [(0, 5)])
+    peak_velocity, onset, brake, _displacement = _features(gaze, speed, accel, [(0, 5)], geometry=PER_AXIS)
 
     assert peak_velocity[0] == 40.0
     assert onset[0] == 5.0    # unclamped this reads accel[5] and returns 999.0
@@ -934,3 +935,20 @@ def test_runs_from_labels_gives_reconstructed_runs_no_reliability():
     labels = np.array([Label.FIXATION] * 3 + [Label.SACCADE] * 2, dtype=object)
 
     assert all(run.reliability is None for run in runs_from_labels(labels))
+
+
+def test_the_displacement_is_the_settings_size():
+    """True-angle spec decision 3: the displacement the 0.2-degree floor and
+    the 1-degree split read is `measure.amplitude` in the paramset's
+    geometry. A 2-degree step up from (30, 0) is 1.732 degrees of true
+    angle."""
+    from wl_preproc.eye.detect.geometry import TRUE_ANGLE
+    from wl_preproc.eye.detect.otero_millan import _features
+
+    speed = np.array([0.0, 10.0, 50.0, 20.0, 5.0])
+    accel = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
+    gaze = np.column_stack([np.full(5, 30.0), np.linspace(0.0, 2.0, 5)])
+    *_, per_axis = _features(gaze, speed, accel, [(0, 5)], geometry=PER_AXIS)
+    *_, true = _features(gaze, speed, accel, [(0, 5)], geometry=TRUE_ANGLE)
+    assert per_axis[0] == 2.0
+    assert true[0] == pytest.approx(1.732, abs=5e-4)
diff --git a/tests/eye/detect/test_otero_millan_validation.py b/tests/eye/detect/test_otero_millan_validation.py
index 03dead6..d317bbf 100644
--- a/tests/eye/detect/test_otero_millan_validation.py
+++ b/tests/eye/detect/test_otero_millan_validation.py
@@ -69,6 +69,7 @@ import time
 import numpy as np
 import pytest
 
+from wl_preproc.eye.detect.geometry import PER_AXIS
 from wl_preproc.eye.detect.labels import Label
 
 # --------------------------------------------------------------------------
@@ -243,7 +244,7 @@ def _measured(runs, gaze, v, fs_hz):
     quantity this pipeline never writes down."""
     from wl_preproc.eye.detect.measure import measure
 
-    rows = [measure(gaze, v, run.start, run.stop, fs_hz) for run in runs]
+    rows = [measure(gaze, v, run.start, run.stop, fs_hz, geometry=PER_AXIS) for run in runs]
     return (
         np.array([row.amplitude_deg for row in rows]),
         np.array([row.peak_velocity_deg_s for row in rows]),
@@ -341,7 +342,7 @@ def _random_span_control(gaze, v, mask, durations, fs_hz, seed=0):
         index = int(rng.choice(len(segments), p=weight))
         lo, hi = segments[index]
         start = int(rng.integers(lo, hi - int(duration)))
-        row = measure(gaze, v, start, start + int(duration), fs_hz)
+        row = measure(gaze, v, start, start + int(duration), fs_hz, geometry=PER_AXIS)
         amplitude.append(row.amplitude_deg)
         peak_velocity.append(row.peak_velocity_deg_s)
     return np.array(amplitude), np.array(peak_velocity)
diff --git a/tests/eye/detect/test_velocity.py b/tests/eye/detect/test_velocity.py
index e8bbe25..edc8576 100644
--- a/tests/eye/detect/test_velocity.py
+++ b/tests/eye/detect/test_velocity.py
@@ -107,3 +107,59 @@ def test_non_linear_input_verifies_the_formula():
     # Compare interior samples only
     assert result[2:18, 0] == pytest.approx(expected[2:18, 0], abs=1e-9)
     assert result[2:18, 1] == pytest.approx(expected[2:18, 1], abs=1e-9)
+
+
+# -- The local-frame velocity (design spec `2026-10-30-true-angle-saccades-design.md` section 5) --
+
+from wl_preproc.eye.detect import geometry  # noqa: E402
+from wl_preproc.eye.detect.velocity import local_velocity  # noqa: E402
+
+
+def _great_circle(start_xy, toward, speed_deg_s, n, fs_hz):
+    """Per-axis gaze of a direction moving along a great circle at a constant
+    true speed, toward `toward` (a 3-vector): Rodrigues' rotation of the
+    start direction about the axis perpendicular to both, read back as
+    per-axis degrees, `(atan(dx/dz), atan(dy/dz))`. The axis must be
+    perpendicular to the start direction, or the path is a small circle and
+    its speed `speed_deg_s` times the sine of the angle between them."""
+    d0 = geometry.directions(np.array(start_xy))
+    k = np.cross(d0, np.asarray(toward, dtype=float))
+    k = k / np.linalg.norm(k)
+    out = []
+    for i in range(n):
+        a = np.radians(speed_deg_s * i / fs_hz)
+        d = d0 * np.cos(a) + np.cross(k, d0) * np.sin(a) + k * (k @ d0) * (1 - np.cos(a))
+        out.append([np.degrees(np.arctan(d[0] / d[2])), np.degrees(np.arctan(d[1] / d[2]))])
+    return np.array(out)
+
+
+def test_a_constant_true_speed_reads_as_that_speed_anywhere():
+    """Section 11: a sweep at constant true speed along a great circle, off
+    centre, reads as that speed; the per-axis velocity does not."""
+    fs_hz = 500.0
+    gaze = _great_circle((25.0, 15.0), (0.3, 1.0, 0.2), 200.0, 40, fs_hz)
+    speed = np.hypot(*local_velocity(gaze, fs_hz)[2:-2].T)
+    np.testing.assert_allclose(speed, 200.0, rtol=1e-9)
+    per_axis = np.hypot(*velocity(gaze, fs_hz)[2:-2].T)
+    assert np.abs(per_axis - 200.0).max() > 2.0
+
+
+def test_at_the_centre_the_local_velocity_is_the_per_axis_one():
+    t = np.arange(30) / 500.0
+    gaze = np.column_stack([0.5 * t, -0.3 * t])
+    np.testing.assert_allclose(local_velocity(gaze, 500.0), velocity(gaze, 500.0), rtol=1e-6, atol=1e-9)
+
+
+def test_the_local_velocity_s_edges_are_zero_and_a_short_trace_is_all_zero():
+    gaze = _great_circle((10.0, 0.0), (0.0, 1.0, 0.0), 100.0, 12, 500.0)
+    v = local_velocity(gaze, 500.0)
+    assert (v[:2] == 0).all() and (v[-2:] == 0).all()
+    assert (local_velocity(gaze[:4], 500.0) == 0).all()
+
+
+def test_a_non_finite_position_propagates_into_the_windows_that_read_it():
+    gaze = _great_circle((10.0, 0.0), (0.0, 1.0, 0.0), 100.0, 12, 500.0)
+    gaze[6] = np.nan
+    v = local_velocity(gaze, 500.0)
+    assert np.isnan(v[4:9]).all()
+    assert np.isfinite(v[2:4]).all() and np.isfinite(v[9:10]).all()
diff --git a/tests/schema/test_detect_populate.py b/tests/schema/test_detect_populate.py
index 5dd0f79..ad391c6 100644
--- a/tests/schema/test_detect_populate.py
+++ b/tests/schema/test_detect_populate.py
@@ -77,6 +77,7 @@ import pytest
 # `__globals__` is still the ENCLOSING MODULE's namespace, never the
 # enclosing function's own locals.
 from wl_preproc.eye.detect.engbert_kliegl import EngbertKlieglParams
+from wl_preproc.eye.detect.geometry import PER_AXIS, wrap_360_deg
 
 TRIAL_DURATION_S = 3.0
 
@@ -1240,10 +1241,10 @@ def test_bmd_microsaccades_are_measured_from_take_off(stepped_session):
                 continue
             if run["reliability"] is None:  # a saccade copied from Engbert-Kliegl
                 assert run["label"] == "saccade", (trace, start, stop)
-                expected = measure(gaze, v, start, stop, fs_hz)
+                expected = measure(gaze, v, start, stop, fs_hz, geometry=PER_AXIS)
             else:  # BMD's own event, either side of the cut (final review I1)
                 takeoff = start >= 1 and offered[start - 1] is None and bool(np.isfinite(gaze[start - 1]).all())
-                expected = measure(gaze, v, start - 1 if takeoff else start, stop, fs_hz)
+                expected = measure(gaze, v, start - 1 if takeoff else start, stop, fs_hz, geometry=PER_AXIS)
                 checked += 1
             assert run["amplitude_deg"] == expected.amplitude_deg, (trace, start, stop)
             assert run["peak_velocity_deg_s"] == expected.peak_velocity_deg_s, (trace, start, stop)
@@ -1646,7 +1647,7 @@ def test_every_other_detectors_measurements_are_unchanged(stepped_session):
                         < _NSLR_MIN_MEASURED_SACCADE_MS / 1000.0):
                     assert run["amplitude_deg"] is None, (name, trace)
                     continue
-                expected = measure(gaze, v, run["run_start"], run["run_stop"], fs_hz)
+                expected = measure(gaze, v, run["run_start"], run["run_stop"], fs_hz, geometry=PER_AXIS)
                 assert run["amplitude_deg"] == expected.amplitude_deg, (name, trace)
                 assert run["peak_velocity_deg_s"] == expected.peak_velocity_deg_s, (name, trace)
                 checked += 1
@@ -2659,10 +2660,10 @@ class _CapturedInserts:
 
 
 def _run_insert_trace(intervals, n_samples=200, fs_hz=500.0, *, detector_name="engbert_kliegl",
-                      trace="left", gaze=None, v=None, offered=None, kept=None, eyes=None):
+                      trace="left", gaze=None, v=None, offered=None, kept=None, eyes=None, geometry=PER_AXIS):
     """`EyeDetection._insert_trace` over a ramp and a fully-available mask
-    unless given others, as `detector_name` at its default params, returning
-    `(master_row, run_rows)`."""
+    unless given others, as `detector_name` at its default params, in
+    `geometry`, returning `(master_row, run_rows)`."""
     from wl_preproc.eye.detect.registry import get_detector
     from wl_preproc.schema import detect as detect_schema
 
@@ -2679,7 +2680,7 @@ def _run_insert_trace(intervals, n_samples=200, fs_hz=500.0, *, detector_name="e
     sink = _CapturedInserts()
     detect_schema.EyeDetection._insert_trace(
         sink, {"subject": "s"}, trace, gaze, v, offered, intervals, fs_hz,
-        detector, detector.defaults, kept=kept, eyes=eyes,
+        detector, detector.defaults, kept=kept, eyes=eyes, geometry=geometry,
     )
     (master,) = sink.master
     return master, sink.rows
@@ -2780,7 +2781,7 @@ def test_insert_trace_applies_nslrs_rule_per_eye_and_measure_for_every_other_det
         _master, rows = _run_insert_trace(intervals, detector_name=detector_name, trace=trace,
                                           gaze=gaze, v=v)
         for (start, stop), row in _event_rows(rows).items():
-            expected = measure(gaze, v, start, stop, 500.0)
+            expected = measure(gaze, v, start, stop, 500.0, geometry=PER_AXIS)
             assert row["amplitude_deg"] == expected.amplitude_deg, (detector_name, trace)
             assert row["peak_velocity_deg_s"] == expected.peak_velocity_deg_s, (detector_name, trace)
 
@@ -2812,7 +2813,7 @@ def test_insert_trace_applies_nslrs_floor_but_not_its_landing_rule_to_the_conjun
         # section 1.3; final review M2, 2026-09-28).
         for column in _STORED_MEASUREMENTS:
             assert stored[span][column] is None, (span, column)
-    expected = measure(gaze, v, *long_, 500.0)
+    expected = measure(gaze, v, *long_, 500.0, geometry=PER_AXIS)
     assert stored[long_]["amplitude_deg"] == expected.amplitude_deg
     assert stored[long_]["peak_velocity_deg_s"] == expected.peak_velocity_deg_s
     assert stored[long_]["amplitude_deg"] != _hypot(gaze[80] - gaze[60])
@@ -2954,7 +2955,7 @@ def test_the_conjunction_label_matches_the_amplitude_that_gets_stored():
     detector = get_detector("engbert_kliegl")
     detect_schema.EyeDetection._insert_trace(
         sink, {"subject": "s"}, "conjunction", gaze, v, offered, spans, 500.0,
-        detector, detector.defaults,
+        detector, detector.defaults, geometry=PER_AXIS,
     )
     events = [row for row in sink.rows if row["label"] in ("saccade", "microsaccade")]
 
@@ -3010,7 +3011,7 @@ def test_two_touching_intersections_become_one_conjunction_event():
     detector = get_detector("engbert_kliegl")
     detect_schema.EyeDetection._insert_trace(
         sink, {"subject": "s"}, "conjunction", gaze, v, offered, spans, 500.0,
-        detector, detector.defaults,
+        detector, detector.defaults, geometry=PER_AXIS,
     )
     (event,) = [row for row in sink.rows if row["label"] != "fixation"]
     assert (event["run_start"], event["run_stop"]) == (100, 140)
@@ -3087,7 +3088,7 @@ def test_the_full_split_still_classifies_by_amplitude():
     from wl_preproc.eye.detect.registry import DETECTORS
     from wl_preproc.schema.detect import _conjunction_label
 
-    # `amplitude()` reads `gaze_deg[stop - 1] - gaze_deg[start]` as an (x, y)
+    # `amplitude(, geometry=PER_AXIS)` reads `gaze_deg[stop - 1] - gaze_deg[start]` as an (x, y)
     # pair -- `measure.py::amplitude`'s own `displacement[0], displacement[1]`
     # -- matching every real gaze array in this codebase (`eye/calibration.py
     # ::apply_map`, which is what `eye/gaze.py::gaze_trace` returns, ends in
@@ -3502,7 +3503,7 @@ def _event_amplitudes(runs, gaze, v, fs_hz):
     from wl_preproc.eye.detect.measure import measure
 
     return [
-        (run, measure(gaze, v, run.start, run.stop, fs_hz).amplitude_deg)
+        (run, measure(gaze, v, run.start, run.stop, fs_hz, geometry=PER_AXIS).amplitude_deg)
         for run in runs
         if run.label in (Label.SACCADE, Label.MICROSACCADE)
     ]
@@ -4485,7 +4486,7 @@ def test_every_stored_event_row_carries_the_geometry_its_amplitude_reads(stepped
                 if dx == 0.0 and dy == 0.0:
                     assert row["direction_deg"] is None, (name, trace, row)
                 else:
-                    assert row["direction_deg"] == math.degrees(math.atan2(dy, dx)), (name, trace, row)
+                    assert row["direction_deg"] == float(wrap_360_deg(math.degrees(math.atan2(dy, dx)))), (name, trace, row)
                 measured += 1
     assert measured, "the fixture must give the detectors events to measure"
 
@@ -4662,7 +4663,7 @@ def test_a_kept_one_eye_event_is_stored_as_its_own_eyes_row(detector_name, rule,
                                       kept={(60, 80): ("left", left_gaze, v, offered)})
 
     row = _event_rows(rows)[(60, 80)]
-    expected = measure_event_run(left_gaze, v, offered, 60, 80, 500.0, **rule)
+    expected = measure_event_run(left_gaze, v, offered, 60, 80, 500.0, **rule, geometry=PER_AXIS)
     assert row["amplitude_deg"] == expected.amplitude_deg
     assert (row["start_x_deg"], row["end_x_deg"]) == (float(left_gaze[first, 0]), float(left_gaze[last, 0]))
     assert row["reliability"] == 0.9
@@ -5017,3 +5018,53 @@ def test_the_share_of_gaze_repaired_is_stored_per_eye(glitch_session):
     left = rows["left"]
     assert left["frac_glitch_repaired"] == pytest.approx(sum(w for _, w in _GLITCHES) / left["n_samples"])
     assert rows["right"]["frac_glitch_repaired"] == 0.0
+
+
+# -- True visual angle (design spec `2026-10-30-true-angle-saccades-design.md`) --
+
+
+def _across_the_cut(n_samples=30):
+    """1.12 degrees up from (30, 0) at sample 10: a saccade per axis, 0.970
+    degrees of true angle."""
+    gaze = np.tile([30.0, 0.0], (n_samples, 1))
+    gaze[10:] = [30.0, 1.12]
+    return gaze
+
+
+def test_the_conjunction_label_reads_the_paramsets_geometry():
+    """As it reads the cut, from the paramset dict; a paramset without the key
+    means per-axis (amendment 3)."""
+    from wl_preproc.eye.detect.labels import Label
+    from wl_preproc.eye.detect.registry import get_detector
+    from wl_preproc.schema.detect import _conjunction_label
+
+    detector, gaze = get_detector("engbert_kliegl"), _across_the_cut()
+    true = _conjunction_label(detector, {"microsaccade_max_deg": 1.0, "geometry": "true-angle"}, gaze)
+    unstated = _conjunction_label(detector, {"microsaccade_max_deg": 1.0}, gaze)
+    assert (true(5, 15), unstated(5, 15)) == (Label.MICROSACCADE, Label.SACCADE)
+
+
+def test_insert_trace_measures_in_the_geometry_it_is_given():
+    from wl_preproc.eye.detect.labels import Label, Run
+    from wl_preproc.eye.detect.measure import amplitude
+
+    gaze = _across_the_cut()
+    sizes = {}
+    for setting in ("true-angle", "per-axis"):
+        _master, rows = _run_insert_trace([Run(5, 15, Label.SACCADE)], n_samples=30, gaze=gaze, geometry=setting)
+        (event,) = [row for row in rows if row["label"] == "saccade"]
+        sizes[setting] = event["amplitude_deg"]
+        assert event["amplitude_deg"] == amplitude(gaze, 5, 15, geometry=setting)
+    assert sizes["per-axis"] == pytest.approx(1.12) and sizes["true-angle"] < 1.0
+
+
+def test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused():
+    """True-angle spec section 3, amendment 3: absent, per-axis; a value that
+    is neither setting is refused where the stage reads it, rather than
+    detecting per axis and failing later in measurement."""
+    from wl_preproc.schema.detect import _geometry_of
+
+    assert _geometry_of({}) == "per-axis"
+    assert _geometry_of({"geometry": "true-angle"}) == "true-angle"
+    with pytest.raises(ValueError, match="geometry"):
+        _geometry_of({"geometry": "cyclopean"})
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/eye/detect/test_bmd.py tests/eye/detect/test_contract_fixtures.py tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_measure.py tests/eye/detect/test_nystrom_holmqvist.py tests/eye/detect/test_otero_millan.py tests/eye/detect/test_velocity.py tests/schema/test_detect_populate.py::test_the_conjunction_label_reads_the_paramsets_geometry tests/schema/test_detect_populate.py::test_insert_trace_measures_in_the_geometry_it_is_given -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `2 errors`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/eye/detect/bmd.py b/wl_preproc/eye/detect/bmd.py
index cae657a..abf7e4f 100644
--- a/wl_preproc/eye/detect/bmd.py
+++ b/wl_preproc/eye/detect/bmd.py
@@ -38,6 +38,7 @@ import numpy as np
 from wl_preproc.eye.detect import bmd_rng
 from wl_preproc.eye.detect.bmd_table import LogATable, compute_table, lookup
 from wl_preproc.eye.detect.engbert_kliegl import EngbertKlieglParams, detect_engbert_kliegl
+from wl_preproc.eye.detect.geometry import UNSTATED_GEOMETRY
 from wl_preproc.eye.detect.labels import Label, Run, true_runs
 from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, classify, measure_event_run
 from wl_preproc.eye.detect.numba_threads import keep_off_openmp
@@ -106,6 +107,15 @@ class BmdParams:
     gate_lambda: float
     gate_min_duration_samples: int
     microsaccade_max_deg: float = MICROSACCADE_MAX_DEG
+    #: The SHARED `geometry` key (true-angle spec section 3). The gate is
+    #: Engbert-Kliegl at its registered defaults (BMD design spec section 4),
+    #: and `schema/consensus.py::DetectorAgreement` relies on its saccades being
+    #: Engbert-Kliegl's, so the gate runs in this geometry as Engbert-Kliegl
+    #: does, on the velocity it is handed (true-angle spec amendment 2); BMD's
+    #: own events are split by this geometry's size. The sampler, the authors'
+    #: algorithm, reads positions and is unchanged. Absent, a paramset means
+    #: per-axis.
+    geometry: str = UNSTATED_GEOMETRY
 
 
 DEFAULT_BMD_PARAMS = BmdParams(
@@ -794,17 +804,17 @@ def to_origin(piece: np.ndarray) -> np.ndarray:
 
 
 def _by_size(run: Run, gaze: np.ndarray, velocity_deg_s: np.ndarray, available: np.ndarray, fs_hz: float,
-             microsaccade_max_deg: float) -> Run:
+             microsaccade_max_deg: float, geometry: str) -> Run:
     """The requester's decision of 2026-09-27 (final review I1): BMD's own
     event is split by size, as every other detector's is. Its amplitude is
     the one stored for it, measured from its take-off sample (spec 3.5), and
     `classify` names it `saccade` at or above `microsaccade_max_deg`. It
     keeps its mean P as reliability, which is what marks it as BMD's own
     rather than a copied Engbert-Kliegl saccade (`_insert_trace`)."""
-    amplitude = measure_event_run(gaze, velocity_deg_s, available, run.start, run.stop, fs_hz,
-                                  runs_end_before_landing=False, min_measured_ms=None,
-                                  runs_start_after_takeoff=True).amplitude_deg
-    return dataclasses.replace(run, label=classify(amplitude, microsaccade_max_deg))
+    size = measure_event_run(gaze, velocity_deg_s, available, run.start, run.stop, fs_hz, geometry=geometry,
+                             runs_end_before_landing=False, min_measured_ms=None,
+                             runs_start_after_takeoff=True).amplitude_deg
+    return dataclasses.replace(run, label=classify(size, microsaccade_max_deg))
 
 
 def _labelled(probability: np.ndarray, offset: int, threshold: float) -> list[Run]:
@@ -844,9 +854,10 @@ def detect_bmd(
     Runs are half-open and sorted."""
     gaze = np.asarray(gaze_deg, dtype=float)
     usable = np.array([entry is None for entry in available], dtype=bool) & np.isfinite(gaze).all(axis=1)
+    # Engbert-Kliegl as registered, in the same geometry (amendment 2).
     gate_params = EngbertKlieglParams(lambda_=params.gate_lambda,
                                       min_duration_samples=params.gate_min_duration_samples,
-                                      microsaccade_max_deg=params.microsaccade_max_deg)
+                                      microsaccade_max_deg=params.microsaccade_max_deg, geometry=params.geometry)
     saccades = [r for r in detect_engbert_kliegl(gaze, velocity_deg_s, available, fs_hz, gate_params)
                 if r.label is Label.SACCADE]
     gate = np.zeros(len(gaze), dtype=bool)
@@ -876,6 +887,7 @@ def detect_bmd(
         for (a, _e), probability in zip(group, result.probability):
             for run in _labelled(probability, a, params.threshold):
                 if run.label is Label.MICROSACCADE:
-                    run = _by_size(run, gaze, velocity_deg_s, available, fs_hz, params.microsaccade_max_deg)
+                    run = _by_size(run, gaze, velocity_deg_s, available, fs_hz, params.microsaccade_max_deg,
+                                   params.geometry)
                 runs.append(run)
     return sorted(runs, key=lambda r: r.start)
diff --git a/wl_preproc/eye/detect/contract_fixtures.py b/wl_preproc/eye/detect/contract_fixtures.py
index c58f174..456b2d2 100644
--- a/wl_preproc/eye/detect/contract_fixtures.py
+++ b/wl_preproc/eye/detect/contract_fixtures.py
@@ -41,6 +41,7 @@ from wl_preproc.eye.detect.engbert_kliegl import (
     DEFAULT_EK_PARAMS, DEFAULT_LAMBDA, DEFAULT_MIN_DURATION_SAMPLES, _median_scale, detect_engbert_kliegl,
     thresholds,
 )
+from wl_preproc.eye.detect.geometry import PER_AXIS
 from wl_preproc.eye.detect.labels import Label
 from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude
 from wl_preproc.eye.detect.velocity import velocity
@@ -122,7 +123,7 @@ def build_v1() -> dict:
             "name": name, "note": note, "gaze_deg": gaze.tolist(), "available": available,
             "velocity_deg_s": speed.tolist(),
             "runs": [{"start": int(run.start), "stop": int(run.stop), "label": run.label.value,
-                      "amplitude_deg": amplitude(gaze, run.start, run.stop)} for run in runs],
+                      "amplitude_deg": amplitude(gaze, run.start, run.stop, geometry=PER_AXIS)} for run in runs],
         })
     return {
         "version": VERSION,
@@ -175,7 +176,7 @@ def build_v1_thresholds(v1: dict, v1_sha256: str) -> dict:
             "gaze_deg": gaze.tolist(), "available": available, "velocity_deg_s": speed.tolist(),
             "eta_deg_s": list(thresholds(speed, usable, DEFAULT_EK_PARAMS)),
             "runs": [{"start": int(run.start), "stop": int(run.stop), "label": run.label.value,
-                      "amplitude_deg": amplitude(gaze, run.start, run.stop)} for run in runs],
+                      "amplitude_deg": amplitude(gaze, run.start, run.stop, geometry=PER_AXIS)} for run in runs],
         })
     return {
         "version": 1,
diff --git a/wl_preproc/eye/detect/engbert_kliegl.py b/wl_preproc/eye/detect/engbert_kliegl.py
index 1e740a8..e437f1d 100644
--- a/wl_preproc/eye/detect/engbert_kliegl.py
+++ b/wl_preproc/eye/detect/engbert_kliegl.py
@@ -17,6 +17,7 @@ from dataclasses import dataclass
 import numpy as np
 
 from wl_preproc.eye.detect.labels import Run, true_runs
+from wl_preproc.eye.detect.geometry import UNSTATED_GEOMETRY
 from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude, classify
 
 # lambda = 6 and a 6-sample minimum (12 ms at 500 Hz) are the paper's own
@@ -52,6 +53,11 @@ class EngbertKlieglParams:
     # never a copied literal, so this is not a second place the conventional
     # cut could drift to.
     microsaccade_max_deg: float = MICROSACCADE_MAX_DEG
+    #: The SHARED `geometry` key (true-angle spec section 3), declared, as
+    #: `microsaccade_max_deg` is, because this detector reads sizes: the
+    #: registered default paramset writes it last. Absent, a paramset means
+    #: per-axis, which every paramset written before the key existed was.
+    geometry: str = UNSTATED_GEOMETRY
 
 
 DEFAULT_EK_PARAMS = EngbertKlieglParams(
@@ -138,7 +144,7 @@ def detect_engbert_kliegl(
         Run(
             start=start,
             stop=stop,
-            label=classify(amplitude(gaze_deg, start, stop), params.microsaccade_max_deg),
+            label=classify(amplitude(gaze_deg, start, stop, geometry=params.geometry), params.microsaccade_max_deg),
         )
         for start, stop in true_runs(outside)
         if stop - start >= params.min_duration_samples
diff --git a/wl_preproc/eye/detect/measure.py b/wl_preproc/eye/detect/measure.py
index bdeb724..8dab1d2 100644
--- a/wl_preproc/eye/detect/measure.py
+++ b/wl_preproc/eye/detect/measure.py
@@ -16,6 +16,10 @@ from dataclasses import dataclass
 
 import numpy as np
 
+from wl_preproc.eye.detect.geometry import (
+    GEOMETRIES, PER_AXIS, directions, eccentricity_polar_deg, local_coordinates_deg, true_angle_deg,
+    wrap_360_deg,
+)
 from wl_preproc.eye.detect.labels import Label
 
 # The conventional microsaccade cut. A paramset overrides it; this is the
@@ -29,32 +33,89 @@ class Measurement:
     """One event's measurement. The positions are the gaze at the two
     samples the amplitude is measured between, so `amplitude_deg` is exactly
     the distance from start to end and `direction_deg` its angle (design spec
-    `2026-09-28-saccade-geometry-design.md` section 1)."""
-
-    amplitude_deg: float
-    peak_velocity_deg_s: float
+    `2026-09-28-saccade-geometry-design.md` section 1), each in the geometry
+    it was measured in (`2026-10-30-true-angle-saccades-design.md` section
+    4). A fixation's (`measure_positions`) has no size and no peak speed.
+    The eccentricities and polar angles are facts of the two positions, the
+    same in either geometry; a polar angle is None at the centre."""
+
+    amplitude_deg: float | None
+    peak_velocity_deg_s: float | None
     duration_s: float
     start_x_deg: float
     start_y_deg: float
     end_x_deg: float
     end_y_deg: float
     direction_deg: float | None
+    start_eccentricity_deg: float
+    start_polar_deg: float | None
+    end_eccentricity_deg: float
+    end_polar_deg: float | None
 
 
 def direction(dx_deg: float, dy_deg: float) -> float | None:
-    """The angle of a displacement, in degrees counterclockwise from
-    rightward: 0 is rightward, 90 upward, +/-180 leftward, -90 downward, in
-    the calibrated frame (positive x rightward, positive y upward; the task
-    code's frame for target positions). `None` for a zero displacement,
-    which has no direction -- `atan2(0, 0)` would say 0, "rightward"
-    (design spec `2026-09-28-saccade-geometry-design.md` section 1.2)."""
+    """The angle of a per-axis displacement, in degrees counterclockwise from
+    rightward in `[0, 360)`: 0 is rightward, 90 upward, 180 leftward, 270
+    downward, in the calibrated frame (positive x rightward, positive y
+    upward; the task code's frame for target positions). `[0, 360)` is the
+    requester's "0 to 360 in both repos" (true-angle spec, amendment 1); it
+    was `[-180, 180]`. `None` for a zero displacement, which has no direction
+    -- `atan2(0, 0)` would say 0, "rightward" (design spec
+    `2026-09-28-saccade-geometry-design.md` section 1.2)."""
     if dx_deg == 0.0 and dy_deg == 0.0:
         return None
-    return math.degrees(math.atan2(dy_deg, dx_deg))
+    return float(wrap_360_deg(math.degrees(math.atan2(dy_deg, dx_deg))))
+
+
+def _checked(geometry: str) -> str:
+    if geometry not in GEOMETRIES:
+        raise ValueError(f"unknown geometry {geometry!r}; one of {GEOMETRIES}")
+    return geometry
 
 
-def amplitude(gaze_deg: np.ndarray, start: int, stop: int) -> float:
-    """One interval's amplitude in degrees. `stop` is exclusive, matching `Run`.
+def _size(first: np.ndarray, last: np.ndarray, geometry: str) -> float:
+    """Per-axis, `hypot` of the displacement, as v1; true angle, the angle
+    between the two positions' directions (true-angle spec section 4)."""
+    if _checked(geometry) == PER_AXIS:
+        displacement = last - first
+        return float(np.hypot(displacement[0], displacement[1]))
+    return float(true_angle_deg(directions(first), directions(last)))
+
+
+def _direction(first: np.ndarray, last: np.ndarray, geometry: str) -> float | None:
+    """Per-axis, `direction` of the displacement; true angle, the end's
+    direction in the eye's frame at the start: `atan2` of its local
+    coordinates about the start, in `[0, 360)`. `None` where the two
+    positions are equal, tested as positions: the local frame's own arithmetic
+    can leave a tangent of rounding size where they are."""
+    if _checked(geometry) == PER_AXIS:
+        return direction(float(last[0] - first[0]), float(last[1] - first[1]))
+    if first[0] == last[0] and first[1] == last[1]:
+        return None
+    lx, ly = local_coordinates_deg(directions(last), directions(first))
+    return float(wrap_360_deg(np.degrees(np.arctan2(ly, lx))))
+
+
+def _positions(gaze_deg: np.ndarray, first: int, last: int, geometry: str) -> dict:
+    """The fields every measurement shares: the two positions, the direction
+    from one to the other, and each one's eccentricity and polar angle."""
+    a, b = gaze_deg[first], gaze_deg[last]
+    eccentricity, polar = eccentricity_polar_deg(np.stack([a, b]))
+    return {
+        "start_x_deg": float(a[0]), "start_y_deg": float(a[1]),
+        "end_x_deg": float(b[0]), "end_y_deg": float(b[1]),
+        "direction_deg": _direction(a, b, geometry),
+        "start_eccentricity_deg": float(eccentricity[0]),
+        "start_polar_deg": None if math.isnan(polar[0]) else float(polar[0]),
+        "end_eccentricity_deg": float(eccentricity[1]),
+        "end_polar_deg": None if math.isnan(polar[1]) else float(polar[1]),
+    }
+
+
+def amplitude(gaze_deg: np.ndarray, start: int, stop: int, *, geometry: str) -> float:
+    """One interval's amplitude in degrees, in `geometry` (true-angle spec
+    section 4; no default, so no caller gets one by omission). `stop` is
+    exclusive, matching `Run`.
 
     **Split out of `measure` so a DETECTOR can call it, and there is still
     exactly one implementation.** Design spec section 3's detector signature
@@ -92,8 +153,7 @@ def amplitude(gaze_deg: np.ndarray, start: int, stop: int) -> float:
     """
     if stop <= start:
         raise ValueError(f"amplitude requires stop > start; got start={start}, stop={stop}")
-    displacement = gaze_deg[stop - 1] - gaze_deg[start]
-    return float(np.hypot(displacement[0], displacement[1]))
+    return _size(gaze_deg[start], gaze_deg[stop - 1], geometry)
 
 
 def measure(
@@ -102,8 +162,10 @@ def measure(
     start: int,
     stop: int,
     fs_hz: float,
+    *,
+    geometry: str,
 ) -> Measurement:
-    """Measure one interval. `stop` is exclusive, matching `Run`.
+    """Measure one interval in `geometry`. `stop` is exclusive, matching `Run`.
 
     **Precondition:** `stop > start`. Empty intervals are invalid; they
     silently read nonsensical indices (e.g., `start=stop=0` accesses
@@ -116,23 +178,34 @@ def measure(
     **Peak velocity is bounded to the interval.** A faster sample just outside
     belongs to a different event. The `speed.size` guard makes this safe; it
     could be removed but is kept for symmetry.
+
+    **Peak velocity is the largest magnitude of the velocity it is handed.**
+    Under true angle the caller hands it `velocity.local_velocity`, whose
+    magnitude is the true speed (true-angle spec section 4); under per-axis,
+    `velocity.velocity`, as v1.
     """
     if stop <= start:
         raise ValueError(f"measure requires stop > start; got start={start}, stop={stop}")
     speed = np.hypot(velocity_deg_s[start:stop, 0], velocity_deg_s[start:stop, 1])
-    first, last = gaze_deg[start], gaze_deg[stop - 1]
     return Measurement(
-        amplitude_deg=amplitude(gaze_deg, start, stop),
+        amplitude_deg=amplitude(gaze_deg, start, stop, geometry=geometry),
         peak_velocity_deg_s=float(speed.max()) if speed.size else 0.0,
         duration_s=float(stop - start) / fs_hz,
-        start_x_deg=float(first[0]),
-        start_y_deg=float(first[1]),
-        end_x_deg=float(last[0]),
-        end_y_deg=float(last[1]),
-        direction_deg=direction(float(last[0] - first[0]), float(last[1] - first[1])),
+        **_positions(gaze_deg, start, stop - 1, geometry),
     )
 
 
+def measure_positions(gaze_deg: np.ndarray, start: int, stop: int, fs_hz: float, *, geometry: str) -> Measurement:
+    """A fixation's measurement (true-angle spec decision 5): its first and
+    last samples' positions, their eccentricity and polar angle, and its
+    direction in `geometry`; no size and no peak speed, so the main sequence
+    and vigor, which read those, never see a fixation. `stop` is exclusive."""
+    if stop <= start:
+        raise ValueError(f"measure_positions requires stop > start; got start={start}, stop={stop}")
+    return Measurement(amplitude_deg=None, peak_velocity_deg_s=None, duration_s=float(stop - start) / fs_hz,
+                       **_positions(gaze_deg, start, stop - 1, _checked(geometry)))
+
+
 def measure_event_run(
     gaze_deg: np.ndarray,
     velocity_deg_s: np.ndarray,
@@ -141,6 +214,7 @@ def measure_event_run(
     stop: int,
     fs_hz: float,
     *,
+    geometry: str,
     runs_end_before_landing: bool,
     min_measured_ms: float | None,
     runs_start_after_takeoff: bool = False,
@@ -159,10 +233,10 @@ def measure_event_run(
     meet, declared on its `registry.Detector` entry. NSLR's segment `k` is
     `[J[k], J[k+1])`, and the eye lands at knot `J[k+1]`, the next run's
     first sample (design spec `2026-09-27-nslr-design.md` section 4). So
-    `measure`'s `gaze[stop - 1] - gaze[start]` would miss the last step: a
+    `measure`'s reading of `gaze[start]` and `gaze[stop - 1]` would miss the last step: a
     fraction `1/L` of an `L`-sample run, and all of it when `L` is 1. Such a
     run is measured up to where the eye lands instead -- amplitude
-    `gaze[stop] - gaze[start]`, peak velocity over `[start, stop]` inclusive
+    from `gaze[start]` to `gaze[stop]`, peak velocity over `[start, stop]` inclusive
     -- when `stop` is an interior knot:
     - `stop < n`;
     - the mask offered it (`offered[stop] is None`);
@@ -183,7 +257,7 @@ def measure_event_run(
     (`schema/detect.py::_measured_from_takeoff`). BMD's state-1 run
     `[t1, t2)` carries the eye from sample `t1 - 1` (design spec
     `2026-09-27-bmd-design.md` section 3.5), so such a run is measured from
-    that take-off sample -- amplitude `gaze[stop - 1] - gaze[start - 1]`, peak
+    that take-off sample -- amplitude from `gaze[start - 1]` to `gaze[stop - 1]`, peak
     velocity over `[start - 1, stop)` -- when `start - 1` exists, the mask
     offered it, and its gaze is finite. Otherwise it is measured as `measure`
     measures it. The requester's decision of 2026-09-27.
@@ -210,8 +284,8 @@ def measure_event_run(
     ):
         lo = start - 1
     if (lo, hi) == (start, stop):
-        return measure(gaze_deg, velocity_deg_s, start, stop, fs_hz)
-    widened = measure(gaze_deg, velocity_deg_s, lo, hi, fs_hz)
+        return measure(gaze_deg, velocity_deg_s, start, stop, fs_hz, geometry=geometry)
+    widened = measure(gaze_deg, velocity_deg_s, lo, hi, fs_hz, geometry=geometry)
     return dataclasses.replace(widened, duration_s=float(stop - start) / fs_hz)
 
 
diff --git a/wl_preproc/eye/detect/nystrom_holmqvist.py b/wl_preproc/eye/detect/nystrom_holmqvist.py
index 3589100..012fe16 100644
--- a/wl_preproc/eye/detect/nystrom_holmqvist.py
+++ b/wl_preproc/eye/detect/nystrom_holmqvist.py
@@ -41,6 +41,7 @@ from dataclasses import dataclass
 import numpy as np
 
 from wl_preproc.eye.detect.labels import Label, Run, true_runs
+from wl_preproc.eye.detect.geometry import UNSTATED_GEOMETRY
 from wl_preproc.eye.detect.measure import amplitude
 
 
@@ -165,6 +166,12 @@ class NystromHolmqvistParams:
     #: and is dropped before detection (p. 194).
     max_acceleration_deg_s2: float
 
+    #: The SHARED `geometry` key (true-angle spec section 3), declared, as
+    #: `microsaccade_max_deg` is, because this detector compares a glissade's size with its saccade's: the
+    #: registered default paramset writes it last. Absent, a paramset means
+    #: per-axis, which every paramset written before the key existed was.
+    geometry: str = UNSTATED_GEOMETRY
+
 
 DEFAULT_NH_PARAMS = NystromHolmqvistParams(
     initial_peak_threshold_deg_s=200.0,
@@ -465,8 +472,8 @@ def _glissade_bounds(
         stop += 1
     stop = min(stop + 1, speed_deg_s.size)
 
-    saccade_amp = amplitude(gaze_deg, saccade_onset, saccade_offset)
-    if amplitude(gaze_deg, saccade_offset, stop) > saccade_amp:
+    saccade_amp = amplitude(gaze_deg, saccade_onset, saccade_offset, geometry=params.geometry)
+    if amplitude(gaze_deg, saccade_offset, stop, geometry=params.geometry) > saccade_amp:
         return None
     return saccade_offset, stop
 
diff --git a/wl_preproc/eye/detect/otero_millan.py b/wl_preproc/eye/detect/otero_millan.py
index c4e0b40..cb0975f 100644
--- a/wl_preproc/eye/detect/otero_millan.py
+++ b/wl_preproc/eye/detect/otero_millan.py
@@ -92,6 +92,7 @@ from dataclasses import dataclass
 import numpy as np
 
 from wl_preproc.eye.detect.labels import Run
+from wl_preproc.eye.detect.geometry import UNSTATED_GEOMETRY
 from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude, classify
 
 # The reference's `SACCADELIMITTH`, in deg/s. **Not a detection threshold**: no
@@ -189,6 +190,11 @@ class OteroMillanParams:
     #: detector's vocabulary splits by amplitude, so it consumes the SHARED
     #: cut rather than owning one. See `schema/detect.py::_params_for`.
     microsaccade_max_deg: float = MICROSACCADE_MAX_DEG
+    #: The SHARED `geometry` key (true-angle spec section 3), declared, as
+    #: `microsaccade_max_deg` is, because this detector reads sizes (its 0.2-degree floor and its 1-degree split): the
+    #: registered default paramset writes it last. Absent, a paramset means
+    #: per-axis, which every paramset written before the key existed was.
+    geometry: str = UNSTATED_GEOMETRY
 
 
 DEFAULT_OM_PARAMS = OteroMillanParams(
@@ -246,7 +252,7 @@ def detect_otero_millan(
         return []
 
     peak_velocity, accel_onset, accel_brake, displacement = _features(
-        gaze_deg, speed, accel, spans
+        gaze_deg, speed, accel, spans, params.geometry
     )
     # **Z-scoring and whitening are GLOBAL; only the clustering is chunked.**
     # The reference calls `GetFeatures` once over every peak in the recording
@@ -499,6 +505,7 @@ def _features(
     speed: np.ndarray,
     accel: np.ndarray,
     spans: list[tuple[int, int]],
+    geometry: str,
 ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
     """Per candidate: peak velocity, peak acceleration at onset and at braking,
     and displacement.
@@ -558,7 +565,7 @@ def _features(
         accel_onset[i] = float(accel[start:onset_stop].max())
         brake_start = min(start + at + 1, stop - 1)
         accel_brake[i] = float(accel[brake_start:stop].max())
-        displacement[i] = amplitude(gaze_deg, start, stop)
+        displacement[i] = amplitude(gaze_deg, start, stop, geometry=geometry)
     return peak_velocity, accel_onset, accel_brake, displacement
 
 
diff --git a/wl_preproc/eye/detect/velocity.py b/wl_preproc/eye/detect/velocity.py
index 735ec75..664aa21 100644
--- a/wl_preproc/eye/detect/velocity.py
+++ b/wl_preproc/eye/detect/velocity.py
@@ -44,3 +44,32 @@ def velocity(gaze_deg: np.ndarray, fs_hz: float) -> np.ndarray:
         gaze_deg[4:] + gaze_deg[3:-1] - gaze_deg[1:-3] - gaze_deg[:-4]
     ) * (fs_hz / _WEIGHT_SUM)
     return out
+
+
+def local_velocity(gaze_deg: np.ndarray, fs_hz: float) -> np.ndarray:
+    """Degrees per second in the local frame at each sample, one row per
+    sample: rightward and upward about that sample's own direction (design
+    spec `2026-10-30-true-angle-saccades-design.md` section 5; wl-xcon's
+    proposal 2).
+
+    For sample `n`, the positions of samples `n-2 ... n+2` are read in the
+    local frame at sample `n`'s direction (`geometry.local_coordinates_deg`,
+    azimuthal-equidistant, so distances from `n` are exact), then the same
+    five-point weighted difference as `velocity`. At the centre it is the
+    per-axis velocity; off centre it is the eye's angular velocity, so its
+    magnitude is the true speed. The two samples at each edge are zero, as
+    in `velocity`, and a non-finite position makes every window that reads
+    it NaN."""
+    from wl_preproc.eye.detect.geometry import directions, local_coordinates_deg
+
+    n = gaze_deg.shape[0]
+    out = np.zeros((n, 2), dtype=float)
+    if n < 2 * _HALF_WINDOW + 1:
+        return out
+    d = directions(gaze_deg)
+    centre = d[_HALF_WINDOW:n - _HALF_WINDOW]
+    # The neighbours in `velocity`'s own order, `[n+2] + [n+1] - [n-1] - [n-2]`.
+    plus2, plus1, minus1, minus2 = (np.stack(local_coordinates_deg(neighbour, centre), axis=-1)
+                                    for neighbour in (d[4:], d[3:-1], d[1:-3], d[:-4]))
+    out[_HALF_WINDOW:n - _HALF_WINDOW] = (plus2 + plus1 - minus1 - minus2) * (fs_hz / _WEIGHT_SUM)
+    return out
diff --git a/wl_preproc/schema/detect.py b/wl_preproc/schema/detect.py
index 341f533..ec4dda2 100644
--- a/wl_preproc/schema/detect.py
+++ b/wl_preproc/schema/detect.py
@@ -100,6 +100,7 @@ from pathlib import Path
 import datajoint as dj
 import numpy as np
 
+from wl_preproc.eye.detect.geometry import GEOMETRIES, UNSTATED_GEOMETRY
 from wl_preproc.eye.detect.labels import (
     Label,
     Run,
@@ -581,6 +582,10 @@ class EyeDetection(dj.Computed):
         # their spans are then demonstrably talking about one set of detector
         # parameters rather than about two separate constructions of it.
         detector_params = _params_for(detector, params)
+        # The paramset's geometry (true-angle spec section 3), read from the
+        # dict, not the detector's params: a port that does not declare it is
+        # still measured in it.
+        geometry = _geometry_of(params)
 
         session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
         segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
@@ -636,7 +641,7 @@ class EyeDetection(dj.Computed):
             else:
                 gaze, v, offered = per_eye[eye_value]
                 self._insert_trace(key, eye_value, gaze, v, offered, spans[eye_value], fs_hz,
-                                   detector, detector_params)
+                                   detector, detector_params, geometry=geometry)
 
         if refused_reason:
             # The conjunction gets a REFUSED ROW here, never an absent one --
@@ -731,7 +736,7 @@ class EyeDetection(dj.Computed):
                 key, "conjunction", gaze, v, fill, intervals,
                 fs_hz, detector, detector_params,
                 kept={(run.start, run.stop): (eye, *per_eye[eye]) for eye, run in kept},
-                eyes=per_eye,
+                eyes=per_eye, geometry=geometry,
             )
             self.Source.insert(
                 {**key, "trace": "conjunction", "source_index": index,
@@ -740,7 +745,7 @@ class EyeDetection(dj.Computed):
             )
 
     def _insert_trace(self, key, trace, gaze, v, offered, intervals, fs_hz,
-                      detector, detector_params, kept=None, eyes=None) -> None:
+                      detector, detector_params, kept=None, eyes=None, *, geometry: str) -> None:
         """One trace's master row and its runs.
 
         **This method assigns no labels of its own.** Each interval arrives
@@ -981,7 +986,7 @@ class EyeDetection(dj.Computed):
                 # floor applies to every trace (see this docstring's
                 # conjunction paragraph).
                 measurement = measure_event_run(
-                    run_gaze, run_v, run_offered, run.start, run.stop, fs_hz,
+                    run_gaze, run_v, run_offered, run.start, run.stop, fs_hz, geometry=geometry,
                     runs_end_before_landing=(detector.runs_end_before_landing
                                              and measured_as != "conjunction"),
                     min_measured_ms=min_measured_ms,
@@ -1592,13 +1597,27 @@ def _conjunction_label(detector, params: dict, gaze: np.ndarray) -> Callable[[in
     # too. (This no longer says `detector.vocabulary` itself is that set --
     # only its saccadic slice is, which is the whole point of slicing.)
     microsaccade_max_deg = params["microsaccade_max_deg"]
+    # Read as the cut is, from the paramset dict.
+    geometry = _geometry_of(params)
 
     def label_for(start: int, stop: int) -> Label:
-        return classify(amplitude(gaze, start, stop), microsaccade_max_deg)
+        return classify(amplitude(gaze, start, stop, geometry=geometry), microsaccade_max_deg)
 
     return label_for
 
 
+def _geometry_of(params: dict) -> str:
+    """An `eye_detection` paramset's `geometry` (true-angle spec section 3):
+    absent, per-axis, which every paramset written before the key existed was
+    (amendment 3); a value that is neither setting is refused here, where the
+    stage reads it, rather than detecting per axis and failing in
+    measurement."""
+    geometry = params.get("geometry", UNSTATED_GEOMETRY)
+    if geometry not in GEOMETRIES:
+        raise ValueError(f"eye_detection paramset geometry {geometry!r} is not one of {GEOMETRIES}")
+    return geometry
+
+
 def _repaired_gaze(path, file_eye: str, map_, fs_hz: float, validity_params):
     """One eye's gaze in degrees, with tracker glitches repaired, and which
     samples were: the one place `EyeValidity` and `EyeDetection` both get
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/eye/detect/test_bmd.py tests/eye/detect/test_contract_fixtures.py tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_measure.py tests/eye/detect/test_nystrom_holmqvist.py tests/eye/detect/test_otero_millan.py tests/eye/detect/test_velocity.py tests/schema/test_detect_populate.py::test_the_conjunction_label_reads_the_paramsets_geometry tests/schema/test_detect_populate.py::test_insert_trace_measures_in_the_geometry_it_is_given -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `184 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/eye/detect tests/nwb tests/schema/test_detect_populate.py tests/schema/test_detect_schema.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `686 passed, 73 skipped, 1 xfailed`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "size-branches-swapped",
  "wl_preproc/eye/detect/measure.py",
  "    if _checked(geometry) == PER_AXIS:\n        displacement = last - first",
  "    if _checked(geometry) == TRUE_ANGLE:\n        displacement = last - first",
  "tests/eye/detect/test_measure.py tests/eye/detect/test_velocity.py tests/eye/detect/test_bmd.py tests/eye/detect/test_otero_millan.py tests/eye/detect/test_nystrom_holmqvist.py tests/schema/test_detect_populate.py::test_the_conjunction_label_reads_the_paramsets_geometry tests/schema/test_detect_populate.py::test_insert_trace_measures_in_the_geometry_it_is_given tests/schema/test_detect_populate.py::test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused"
 ],
 [
  "per-axis-direction-unwrapped",
  "wl_preproc/eye/detect/measure.py",
  "    return float(wrap_360_deg(math.degrees(math.atan2(dy_deg, dx_deg))))",
  "    return math.degrees(math.atan2(dy_deg, dx_deg))",
  "tests/eye/detect/test_measure.py tests/eye/detect/test_velocity.py tests/eye/detect/test_bmd.py tests/eye/detect/test_otero_millan.py tests/eye/detect/test_nystrom_holmqvist.py tests/schema/test_detect_populate.py::test_the_conjunction_label_reads_the_paramsets_geometry tests/schema/test_detect_populate.py::test_insert_trace_measures_in_the_geometry_it_is_given tests/schema/test_detect_populate.py::test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused"
 ],
 [
  "true-direction-of-no-displacement",
  "wl_preproc/eye/detect/measure.py",
  "    if first[0] == last[0] and first[1] == last[1]:\n        return None\n    lx, ly",
  "    lx, ly",
  "tests/eye/detect/test_measure.py tests/eye/detect/test_velocity.py tests/eye/detect/test_bmd.py tests/eye/detect/test_otero_millan.py tests/eye/detect/test_nystrom_holmqvist.py tests/schema/test_detect_populate.py::test_the_conjunction_label_reads_the_paramsets_geometry tests/schema/test_detect_populate.py::test_insert_trace_measures_in_the_geometry_it_is_given tests/schema/test_detect_populate.py::test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused"
 ],
 [
  "polar-null-stored-as-nan",
  "wl_preproc/eye/detect/measure.py",
  "\"start_polar_deg\": None if math.isnan(polar[0]) else float(polar[0]),",
  "\"start_polar_deg\": float(polar[0]),",
  "tests/eye/detect/test_measure.py tests/eye/detect/test_velocity.py tests/eye/detect/test_bmd.py tests/eye/detect/test_otero_millan.py tests/eye/detect/test_nystrom_holmqvist.py tests/schema/test_detect_populate.py::test_the_conjunction_label_reads_the_paramsets_geometry tests/schema/test_detect_populate.py::test_insert_trace_measures_in_the_geometry_it_is_given tests/schema/test_detect_populate.py::test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused"
 ],
 [
  "fixation-direction-per-axis",
  "wl_preproc/eye/detect/measure.py",
  "**_positions(gaze_deg, start, stop - 1, _checked(geometry)))",
  "**_positions(gaze_deg, start, stop - 1, \"per-axis\"))",
  "tests/eye/detect/test_measure.py tests/eye/detect/test_velocity.py tests/eye/detect/test_bmd.py tests/eye/detect/test_otero_millan.py tests/eye/detect/test_nystrom_holmqvist.py tests/schema/test_detect_populate.py::test_the_conjunction_label_reads_the_paramsets_geometry tests/schema/test_detect_populate.py::test_insert_trace_measures_in_the_geometry_it_is_given tests/schema/test_detect_populate.py::test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused"
 ],
 [
  "local-velocity-sign",
  "wl_preproc/eye/detect/velocity.py",
  "(plus2 + plus1 - minus1 - minus2)",
  "(plus2 + plus1 - minus1 + minus2)",
  "tests/eye/detect/test_measure.py tests/eye/detect/test_velocity.py tests/eye/detect/test_bmd.py tests/eye/detect/test_otero_millan.py tests/eye/detect/test_nystrom_holmqvist.py tests/schema/test_detect_populate.py::test_the_conjunction_label_reads_the_paramsets_geometry tests/schema/test_detect_populate.py::test_insert_trace_measures_in_the_geometry_it_is_given tests/schema/test_detect_populate.py::test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused"
 ],
 [
  "engbert-kliegl-split-per-axis",
  "wl_preproc/eye/detect/engbert_kliegl.py",
  "classify(amplitude(gaze_deg, start, stop, geometry=params.geometry), params.microsaccade_max_deg)",
  "classify(amplitude(gaze_deg, start, stop, geometry=\"per-axis\"), params.microsaccade_max_deg)",
  "tests/eye/detect/test_measure.py tests/eye/detect/test_velocity.py tests/eye/detect/test_bmd.py tests/eye/detect/test_otero_millan.py tests/eye/detect/test_nystrom_holmqvist.py tests/schema/test_detect_populate.py::test_the_conjunction_label_reads_the_paramsets_geometry tests/schema/test_detect_populate.py::test_insert_trace_measures_in_the_geometry_it_is_given tests/schema/test_detect_populate.py::test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused"
 ],
 [
  "otero-millan-displacement-per-axis",
  "wl_preproc/eye/detect/otero_millan.py",
  "        displacement[i] = amplitude(gaze_deg, start, stop, geometry=geometry)",
  "        displacement[i] = amplitude(gaze_deg, start, stop, geometry=\"per-axis\")",
  "tests/eye/detect/test_measure.py tests/eye/detect/test_velocity.py tests/eye/detect/test_bmd.py tests/eye/detect/test_otero_millan.py tests/eye/detect/test_nystrom_holmqvist.py tests/schema/test_detect_populate.py::test_the_conjunction_label_reads_the_paramsets_geometry tests/schema/test_detect_populate.py::test_insert_trace_measures_in_the_geometry_it_is_given tests/schema/test_detect_populate.py::test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused"
 ],
 [
  "nystrom-holmqvist-comparison-per-axis",
  "wl_preproc/eye/detect/nystrom_holmqvist.py",
  "    saccade_amp = amplitude(gaze_deg, saccade_onset, saccade_offset, geometry=params.geometry)\n    if amplitude(gaze_deg, saccade_offset, stop, geometry=params.geometry) > saccade_amp:",
  "    saccade_amp = amplitude(gaze_deg, saccade_onset, saccade_offset, geometry=\"per-axis\")\n    if amplitude(gaze_deg, saccade_offset, stop, geometry=\"per-axis\") > saccade_amp:",
  "tests/eye/detect/test_measure.py tests/eye/detect/test_velocity.py tests/eye/detect/test_bmd.py tests/eye/detect/test_otero_millan.py tests/eye/detect/test_nystrom_holmqvist.py tests/schema/test_detect_populate.py::test_the_conjunction_label_reads_the_paramsets_geometry tests/schema/test_detect_populate.py::test_insert_trace_measures_in_the_geometry_it_is_given tests/schema/test_detect_populate.py::test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused"
 ],
 [
  "bmd-own-split-per-axis",
  "wl_preproc/eye/detect/bmd.py",
  "run.start, run.stop, fs_hz, geometry=geometry,",
  "run.start, run.stop, fs_hz, geometry=\"per-axis\",",
  "tests/eye/detect/test_measure.py tests/eye/detect/test_velocity.py tests/eye/detect/test_bmd.py tests/eye/detect/test_otero_millan.py tests/eye/detect/test_nystrom_holmqvist.py tests/schema/test_detect_populate.py::test_the_conjunction_label_reads_the_paramsets_geometry tests/schema/test_detect_populate.py::test_insert_trace_measures_in_the_geometry_it_is_given tests/schema/test_detect_populate.py::test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused"
 ],
 [
  "bmd-gate-per-axis",
  "wl_preproc/eye/detect/bmd.py",
  "microsaccade_max_deg=params.microsaccade_max_deg, geometry=params.geometry)",
  "microsaccade_max_deg=params.microsaccade_max_deg, geometry=\"per-axis\")",
  "tests/eye/detect/test_measure.py tests/eye/detect/test_velocity.py tests/eye/detect/test_bmd.py tests/eye/detect/test_otero_millan.py tests/eye/detect/test_nystrom_holmqvist.py tests/schema/test_detect_populate.py::test_the_conjunction_label_reads_the_paramsets_geometry tests/schema/test_detect_populate.py::test_insert_trace_measures_in_the_geometry_it_is_given tests/schema/test_detect_populate.py::test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused"
 ],
 [
  "conjunction-label-per-axis",
  "wl_preproc/schema/detect.py",
  "        return classify(amplitude(gaze, start, stop, geometry=geometry), microsaccade_max_deg)",
  "        return classify(amplitude(gaze, start, stop, geometry=\"per-axis\"), microsaccade_max_deg)",
  "tests/eye/detect/test_measure.py tests/eye/detect/test_velocity.py tests/eye/detect/test_bmd.py tests/eye/detect/test_otero_millan.py tests/eye/detect/test_nystrom_holmqvist.py tests/schema/test_detect_populate.py::test_the_conjunction_label_reads_the_paramsets_geometry tests/schema/test_detect_populate.py::test_insert_trace_measures_in_the_geometry_it_is_given tests/schema/test_detect_populate.py::test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused"
 ],
 [
  "insert-trace-per-axis",
  "wl_preproc/schema/detect.py",
  "run_gaze, run_v, run_offered, run.start, run.stop, fs_hz, geometry=geometry,",
  "run_gaze, run_v, run_offered, run.start, run.stop, fs_hz, geometry=\"per-axis\",",
  "tests/eye/detect/test_measure.py tests/eye/detect/test_velocity.py tests/eye/detect/test_bmd.py tests/eye/detect/test_otero_millan.py tests/eye/detect/test_nystrom_holmqvist.py tests/schema/test_detect_populate.py::test_the_conjunction_label_reads_the_paramsets_geometry tests/schema/test_detect_populate.py::test_insert_trace_measures_in_the_geometry_it_is_given tests/schema/test_detect_populate.py::test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused"
 ],
 [
  "unknown-geometry-accepted",
  "wl_preproc/schema/detect.py",
  "        raise ValueError(f\"eye_detection paramset geometry {geometry!r} is not one of {GEOMETRIES}\")",
  "        pass",
  "tests/eye/detect/test_measure.py tests/eye/detect/test_velocity.py tests/eye/detect/test_bmd.py tests/eye/detect/test_otero_millan.py tests/eye/detect/test_nystrom_holmqvist.py tests/schema/test_detect_populate.py::test_the_conjunction_label_reads_the_paramsets_geometry tests/schema/test_detect_populate.py::test_insert_trace_measures_in_the_geometry_it_is_given tests/schema/test_detect_populate.py::test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused"
 ]
]
```

Expected: 14 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(eye): measurement takes the geometry -- size, direction and peak speed in the setting, each endpoint's eccentricity and polar angle, a fixation's positions, the local-frame velocity; every detector that reads a size passes the setting (sections 4 and 5, decision 3)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 3: Engbert-Kliegl in true angle

§6.1, amendment 2: under true angle the threshold's scale is taken over complete windows only (`complete_windows`), as wl-xcon proposed; per axis, it is v1's. `registry.Detector.detects_in_geometry` marks the detectors handed the setting's velocity: Engbert-Kliegl, and BMD, whose gate is Engbert-Kliegl.

**Files:**
- Modify: `wl_preproc/eye/detect/engbert_kliegl.py` (`complete_windows`, `thresholds`)
- Modify: `wl_preproc/eye/detect/registry.py` (`Detector.detects_in_geometry`)
- Test: `tests/eye/detect/test_engbert_kliegl.py`, `tests/eye/detect/test_registry.py`

**Interfaces:**
- Consumes: Task 2's `local_velocity` and `EngbertKlieglParams.geometry`.
- Produces: `engbert_kliegl.complete_windows(usable) -> np.ndarray`; `registry.Detector.detects_in_geometry: bool`,
  true for `engbert_kliegl` and `bmd`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/eye/detect/test_engbert_kliegl.py b/tests/eye/detect/test_engbert_kliegl.py
index 2fd75ba..d64bde5 100644
--- a/tests/eye/detect/test_engbert_kliegl.py
+++ b/tests/eye/detect/test_engbert_kliegl.py
@@ -1,10 +1,14 @@
+import dataclasses
+import json
+from pathlib import Path
+
 import numpy as np
 import pytest
 
 from wl_preproc.eye.detect.engbert_kliegl import (
-    DEFAULT_EK_PARAMS, EngbertKlieglParams, detect_engbert_kliegl,
+    DEFAULT_EK_PARAMS, EngbertKlieglParams, complete_windows, detect_engbert_kliegl, thresholds,
 )
-from wl_preproc.eye.detect.geometry import PER_AXIS
+from wl_preproc.eye.detect.geometry import PER_AXIS, TRUE_ANGLE
 from wl_preproc.eye.detect.labels import Label, Run
 
 FS_HZ = 500.0
@@ -354,3 +358,48 @@ def test_the_default_threshold_is_the_shared_constant_not_a_second_copy():
     from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG
 
     assert DEFAULT_EK_PARAMS.microsaccade_max_deg == MICROSACCADE_MAX_DEG
+
+
+# -- True visual angle (design spec `2026-10-30-true-angle-saccades-design.md` section 6.1) --
+
+_TRUE = dataclasses.replace(DEFAULT_EK_PARAMS, geometry=TRUE_ANGLE)
+
+
+def _v1_case(name):
+    v1 = json.loads((Path(__file__).parents[3] / "docs/fixtures/saccade/v1.json").read_text())
+    case = next(case for case in v1["traces"] if case["name"] == name)
+    return np.array(case["gaze_deg"]), np.array(case["available"], dtype=object)
+
+
+def test_a_complete_window_is_five_usable_samples_clear_of_the_edges():
+    usable = np.ones(12, dtype=bool)
+    usable[6] = False
+    assert np.flatnonzero(complete_windows(usable)).tolist() == [2, 3, 9]
+
+
+def test_under_true_angle_the_scale_leaves_out_incomplete_windows():
+    """wl-xcon's proposal: the two zero-filled samples at each edge and every
+    sample whose window touches a masked one are left out of the scale."""
+    v = np.random.default_rng(5).normal(0.0, 3.0, (40, 2))
+    v[:2] = v[-2:] = 0.0
+    usable = np.ones(40, dtype=bool)
+    usable[20] = False
+    assert thresholds(v, usable, _TRUE) == thresholds(v, complete_windows(usable), DEFAULT_EK_PARAMS)
+    assert thresholds(v, usable, _TRUE) != thresholds(v, usable, DEFAULT_EK_PARAMS)
+
+
+def test_per_axis_is_v1s_scale():
+    """The default params are per-axis, so v1 and its companion stand."""
+    assert DEFAULT_EK_PARAMS.geometry == PER_AXIS
+
+
+def test_under_true_angle_the_step_across_the_cut_is_a_microsaccade():
+    """1.12 degrees up from (30, 0) is 0.970 degrees of true angle: a saccade
+    per axis, a microsaccade in true angle (wl-xcon's proposals 1 and 3)."""
+    from wl_preproc.eye.detect.velocity import local_velocity, velocity
+
+    gaze, available = _v1_case("off-centre-30-across-the-cut")
+    true = detect_engbert_kliegl(gaze, local_velocity(gaze, 500.0), available, 500.0, _TRUE)
+    per_axis = detect_engbert_kliegl(gaze, velocity(gaze, 500.0), available, 500.0, DEFAULT_EK_PARAMS)
+    assert [r.label for r in true] == [Label.MICROSACCADE]
+    assert [r.label for r in per_axis] == [Label.SACCADE]
diff --git a/tests/eye/detect/test_registry.py b/tests/eye/detect/test_registry.py
index ef400b7..7d9fa37 100644
--- a/tests/eye/detect/test_registry.py
+++ b/tests/eye/detect/test_registry.py
@@ -434,3 +434,21 @@ def test_a_glissade_goes_with_its_saccade_when_the_guard_drops_or_cuts_its_end()
         (41, 46, Label.SACCADE), (46, 50, Label.PSO),
         (60, 66, Label.SACCADE), (66, 70, Label.PSO),
     ]
+
+
+def test_engbert_kliegl_and_bmd_are_handed_the_settings_velocity():
+    """True-angle spec section 6.1 and amendment 2: Engbert-Kliegl detects in
+    the setting's geometry, and BMD's gate is Engbert-Kliegl as registered;
+    every other detector is handed the per-axis velocity under either."""
+    assert {name for name, detector in DETECTORS.items() if detector.detects_in_geometry} == {"engbert_kliegl", "bmd"}
+
+
+def test_a_detectors_own_params_mean_per_axis():
+    """Amendment 3: absent the key, a paramset means per-axis, so every
+    detector that declares `geometry` defaults to it; the registered default
+    paramset writes true angle last."""
+    from wl_preproc.eye.detect.geometry import PER_AXIS
+
+    declared = {name: detector.defaults.geometry for name, detector in DETECTORS.items()
+                if hasattr(detector.defaults, "geometry")}
+    assert declared == dict.fromkeys(("engbert_kliegl", "otero_millan", "nystrom_holmqvist", "bmd"), PER_AXIS)
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_registry.py -q -p no:cacheprovider
```

Expected: `1 error`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/eye/detect/engbert_kliegl.py b/wl_preproc/eye/detect/engbert_kliegl.py
index e437f1d..f5ab9f8 100644
--- a/wl_preproc/eye/detect/engbert_kliegl.py
+++ b/wl_preproc/eye/detect/engbert_kliegl.py
@@ -17,7 +17,7 @@ from dataclasses import dataclass
 import numpy as np
 
 from wl_preproc.eye.detect.labels import Run, true_runs
-from wl_preproc.eye.detect.geometry import UNSTATED_GEOMETRY
+from wl_preproc.eye.detect.geometry import TRUE_ANGLE, UNSTATED_GEOMETRY
 from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude, classify
 
 # lambda = 6 and a 6-sample minimum (12 ms at 500 Hz) are the paper's own
@@ -85,12 +85,29 @@ def _median_scale(component: np.ndarray) -> float:
     return float(np.sqrt(variance)) if variance > 0 else 0.0
 
 
+def complete_windows(usable: np.ndarray) -> np.ndarray:
+    """Where a sample's five-sample velocity window is complete: it and the
+    two samples on each side all usable, which also leaves out the two
+    zero-filled samples at each edge (`velocity.py`)."""
+    complete = np.zeros(usable.size, dtype=bool)
+    if usable.size >= 5:
+        complete[2:-2] = usable[:-4] & usable[1:-3] & usable[2:-2] & usable[3:-1] & usable[4:]
+    return complete
+
+
 def thresholds(velocity_deg_s: np.ndarray, usable: np.ndarray, params: EngbertKlieglParams) -> tuple[float, float]:
-    """`(eta_x, eta_y)`: lambda times each axis's median scale, over the
-    velocity at every sample the mask leaves usable -- the two zero-filled
-    samples at each edge (`velocity.py`) and any whose window touches a masked
-    sample included. Published per trace in the detector's contract fixtures
+    """`(eta_x, eta_y)`: lambda times each axis's median scale.
+
+    **Per-axis, as v1:** over the velocity at every sample the mask leaves
+    usable -- the two zero-filled samples at each edge (`velocity.py`) and any
+    whose window touches a masked sample included. **Under true angle, over
+    complete windows only** (`complete_windows`; true-angle spec section 6.1,
+    wl-xcon's proposal, which its online detector follows): those samples'
+    velocities are not the eye's, and on a short trace they move the scale by
+    a few percent. Published per trace in the detector's contract fixtures
     (`contract_fixtures.py`), so wl-xcon's online copy is held to them."""
+    if params.geometry == TRUE_ANGLE:
+        usable = complete_windows(usable)
     return (params.lambda_ * _median_scale(velocity_deg_s[usable, 0]),
             params.lambda_ * _median_scale(velocity_deg_s[usable, 1]))
 
diff --git a/wl_preproc/eye/detect/registry.py b/wl_preproc/eye/detect/registry.py
index 533381b..26f1dea 100644
--- a/wl_preproc/eye/detect/registry.py
+++ b/wl_preproc/eye/detect/registry.py
@@ -131,6 +131,16 @@ class Detector:
     # `2026-09-27-bmd-design.md` section 3.5). The requester's decision of
     # 2026-09-27. Off by default, so every other detector is unchanged.
     runs_start_after_takeoff: bool = False
+    # **True when this detector is handed the velocity of the paramset's
+    # geometry**: under true angle, `velocity.local_velocity`, the eye's
+    # angular velocity in the local frame at each sample; under per-axis, and
+    # for every other detector under either, `velocity.velocity`
+    # (`schema/detect.py::EyeDetection.make`). Engbert-Kliegl's is: it
+    # detects in true angle, as wl-xcon's online copy does (true-angle spec
+    # section 6.1). BMD's is too: its gate is Engbert-Kliegl at its
+    # registered defaults (amendment 2). Off by default: the ports detect per
+    # axis, as their references do.
+    detects_in_geometry: bool = False
     # **The registered detector whose saccades this one copies**, if any.
     # BMD's gate runs Engbert-Kliegl at its registered defaults and stores
     # those saccades as its own, so the two agree on them by construction.
@@ -238,6 +248,7 @@ DETECTORS: dict[str, Detector] = {
         vocabulary=frozenset({Label.SACCADE, Label.MICROSACCADE}),
         run=detect_engbert_kliegl,
         defaults=DEFAULT_EK_PARAMS,
+        detects_in_geometry=True,
     ),
     # **`saccade` AND `microsaccade`, per design spec section 3.1 as corrected
     # 2026-09-01.** That table gave this detector `microsaccade` alone, from
@@ -327,6 +338,7 @@ DETECTORS: dict[str, Detector] = {
         run=detect_bmd,
         defaults=DEFAULT_BMD_PARAMS,
         runs_start_after_takeoff=True,
+        detects_in_geometry=True,
         copies_saccades_from="engbert_kliegl",
     ),
     # **U'n'Eye** (design spec `2026-10-06-uneye-design.md`), the only
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_registry.py -q -p no:cacheprovider
```

Expected: `43 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/eye/detect -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `502 passed, 72 skipped, 1 xfailed`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "scale-over-every-usable-sample",
  "wl_preproc/eye/detect/engbert_kliegl.py",
  "    if params.geometry == TRUE_ANGLE:\n        usable = complete_windows(usable)",
  "    if False:\n        usable = complete_windows(usable)",
  "tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_registry.py"
 ],
 [
  "window-misses-its-last-sample",
  "wl_preproc/eye/detect/engbert_kliegl.py",
  "usable[:-4] & usable[1:-3] & usable[2:-2] & usable[3:-1] & usable[4:]",
  "usable[:-4] & usable[1:-3] & usable[2:-2] & usable[3:-1]",
  "tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_registry.py"
 ],
 [
  "bmd-not-handed-the-settings-velocity",
  "wl_preproc/eye/detect/registry.py",
  "        runs_start_after_takeoff=True,\n        detects_in_geometry=True,",
  "        runs_start_after_takeoff=True,",
  "tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_registry.py"
 ]
]
```

Expected: 3 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(eye): Engbert-Kliegl in true angle -- its threshold's scale over complete windows only, and Engbert-Kliegl and BMD's gate handed the setting's velocity (section 6.1, amendment 2)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 4: The default paramsets detect in true angle

§3 and §5: `_eye_detection_params` writes `geometry: true-angle` after a detector's own defaults; `register_per_axis_paramsets(names)` registers per-axis on request; `EyeDetection.make` computes the local-frame velocity under true angle, hands it to a detector that detects in the setting's geometry, and measures every run on it. The tests that recompute stored measurements now do so in true angle.

**Files:**
- Modify: `wl_preproc/schema/detect.py` (`_eye_detection_params`, `register_per_axis_paramsets`, `EyeDetection.make`)
- Create: `tests/schema/test_true_angle_detection.py`
- Modify: `tests/schema/test_detect_populate.py` (`_stored_inputs`, the measurement tests)

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces: `schema.detect.register_per_axis_paramsets(names) -> dict[str, int]`; every default `eye_detection`
  paramset carries `geometry`; `tests/schema/test_true_angle_detection.py::session`, a module fixture
  `(session_key, per_axis_idx)` that removes its per-axis paramset afterwards.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/schema/test_detect_populate.py b/tests/schema/test_detect_populate.py
index ad391c6..516831d 100644
--- a/tests/schema/test_detect_populate.py
+++ b/tests/schema/test_detect_populate.py
@@ -77,7 +77,10 @@ import pytest
 # `__globals__` is still the ENCLOSING MODULE's namespace, never the
 # enclosing function's own locals.
 from wl_preproc.eye.detect.engbert_kliegl import EngbertKlieglParams
-from wl_preproc.eye.detect.geometry import PER_AXIS, wrap_360_deg
+from wl_preproc.eye.detect.geometry import (
+    PER_AXIS, TRUE_ANGLE, directions, local_coordinates_deg, true_angle_deg, wrap_360_deg,
+)
+from wl_preproc.eye.detect.measure import amplitude
 
 TRIAL_DURATION_S = 3.0
 
@@ -1241,10 +1244,10 @@ def test_bmd_microsaccades_are_measured_from_take_off(stepped_session):
                 continue
             if run["reliability"] is None:  # a saccade copied from Engbert-Kliegl
                 assert run["label"] == "saccade", (trace, start, stop)
-                expected = measure(gaze, v, start, stop, fs_hz, geometry=PER_AXIS)
+                expected = measure(gaze, v, start, stop, fs_hz, geometry=TRUE_ANGLE)
             else:  # BMD's own event, either side of the cut (final review I1)
                 takeoff = start >= 1 and offered[start - 1] is None and bool(np.isfinite(gaze[start - 1]).all())
-                expected = measure(gaze, v, start - 1 if takeoff else start, stop, fs_hz, geometry=PER_AXIS)
+                expected = measure(gaze, v, start - 1 if takeoff else start, stop, fs_hz, geometry=TRUE_ANGLE)
                 checked += 1
             assert run["amplitude_deg"] == expected.amplitude_deg, (trace, start, stop)
             assert run["peak_velocity_deg_s"] == expected.peak_velocity_deg_s, (trace, start, stop)
@@ -1450,11 +1453,13 @@ def _stored_inputs(session_key, eye_value):
     """What `EyeDetection.make()` measured one eye's runs on, rebuilt the way
     it builds them: `(gaze, velocity, offered, fs_hz)`. The gaze goes through
     that eye's STORED calibration, so a test can recompute a stored
-    measurement from the very numbers it was computed from."""
+    measurement from the very numbers it was computed from. The velocity is
+    the default paramsets' -- true angle's, the local frame's (true-angle
+    spec section 5)."""
     from pathlib import Path
 
     from wl_preproc.eye.detect.labels import Label, Run, labels_from_runs
-    from wl_preproc.eye.detect.velocity import velocity
+    from wl_preproc.eye.detect.velocity import local_velocity
     from wl_preproc.eye.gaze import gaze_trace
     from wl_preproc.eye.ohdpi import read_ohdpi
     from wl_preproc.schema import core, detect, ingest
@@ -1475,7 +1480,7 @@ def _stored_inputs(session_key, eye_value):
         len(gaze),
     )
     offered = np.where(available == Label.FIXATION, None, available)
-    return gaze, velocity(gaze, fs_hz), offered, fs_hz
+    return gaze, local_velocity(gaze, fs_hz), offered, fs_hz
 
 
 def test_no_saccade_row_is_stored_at_zero_amplitude_with_a_nonzero_peak_velocity(stepped_session):
@@ -1614,9 +1619,9 @@ def test_nslr_measures_each_planted_step_to_where_the_eye_lands(stepped_session)
             start, stop = run["run_start"], run["run_stop"]
             assert stop < len(gaze) and offered[stop] is None, "the landing must be interior"
             speed = np.hypot(v[start:stop + 1, 0], v[start:stop + 1, 1])
-            assert run["amplitude_deg"] == _hypot(gaze[stop] - gaze[start]), (trace, start)
+            assert run["amplitude_deg"] == amplitude(gaze, start, stop + 1, geometry=TRUE_ANGLE), (trace, start)
             assert run["peak_velocity_deg_s"] == float(speed.max()), (trace, start)
-            assert run["amplitude_deg"] != _hypot(gaze[stop - 1] - gaze[start]), (trace, start)
+            assert run["amplitude_deg"] != amplitude(gaze, start, stop, geometry=TRUE_ANGLE), (trace, start)
 
 
 def test_every_other_detectors_measurements_are_unchanged(stepped_session):
@@ -1647,7 +1652,7 @@ def test_every_other_detectors_measurements_are_unchanged(stepped_session):
                         < _NSLR_MIN_MEASURED_SACCADE_MS / 1000.0):
                     assert run["amplitude_deg"] is None, (name, trace)
                     continue
-                expected = measure(gaze, v, run["run_start"], run["run_stop"], fs_hz, geometry=PER_AXIS)
+                expected = measure(gaze, v, run["run_start"], run["run_stop"], fs_hz, geometry=TRUE_ANGLE)
                 assert run["amplitude_deg"] == expected.amplitude_deg, (name, trace)
                 assert run["peak_velocity_deg_s"] == expected.peak_velocity_deg_s, (name, trace)
                 checked += 1
@@ -4463,8 +4468,9 @@ _DIRECTION_TOLERANCE_DEG = {True: 3.0, False: 15.0}  # keyed on "1 deg or more"
 def test_every_stored_event_row_carries_the_geometry_its_amplitude_reads(stepped_session):
     """Spec section 4. On every trace, for every registered detector:
     - a row with an amplitude has its start and end positions, the amplitude
-      is exactly their distance, and the direction is their angle (NULL only
-      where they coincide);
+      is exactly their true angle, and the direction is the end's in the
+      eye's frame at the start (NULL only where they coincide; true-angle
+      spec section 4);
     - a row without an amplitude has none of the five."""
     import math
 
@@ -4480,13 +4486,14 @@ def test_every_stored_event_row_carries_the_geometry_its_amplitude_reads(stepped
                 if row["amplitude_deg"] is None:
                     assert all(row[column] is None for column in geometry), (name, trace, row)
                     continue
-                dx = row["end_x_deg"] - row["start_x_deg"]
-                dy = row["end_y_deg"] - row["start_y_deg"]
-                assert row["amplitude_deg"] == float(np.hypot(dx, dy)), (name, trace, row)
-                if dx == 0.0 and dy == 0.0:
+                start = directions(np.array([row["start_x_deg"], row["start_y_deg"]]))
+                end = directions(np.array([row["end_x_deg"], row["end_y_deg"]]))
+                assert row["amplitude_deg"] == float(true_angle_deg(start, end)), (name, trace, row)
+                if (row["start_x_deg"], row["start_y_deg"]) == (row["end_x_deg"], row["end_y_deg"]):
                     assert row["direction_deg"] is None, (name, trace, row)
                 else:
-                    assert row["direction_deg"] == float(wrap_360_deg(math.degrees(math.atan2(dy, dx)))), (name, trace, row)
+                    lx, ly = local_coordinates_deg(end, start)
+                    assert row["direction_deg"] == float(wrap_360_deg(math.degrees(math.atan2(ly, lx)))), (name, trace, row)
                 measured += 1
     assert measured, "the fixture must give the detectors events to measure"
 
diff --git a/tests/schema/test_true_angle_detection.py b/tests/schema/test_true_angle_detection.py
new file mode 100644
index 0000000..3c22a88
--- /dev/null
+++ b/tests/schema/test_true_angle_detection.py
@@ -0,0 +1,186 @@
+"""True visual angle in the detection stage (design spec
+`2026-10-30-true-angle-saccades-design.md` sections 3 to 6): every default
+paramset detects in true angle; Engbert-Kliegl is handed the local-frame
+velocity and every run is measured on it; a per-axis paramset, registered
+only on request, detects and measures as v1 did."""
+
+from __future__ import annotations
+
+import pytest
+
+from tests.identities import new_animal
+from tests.schema.test_detect_populate import _build_stepped_session, _stored_inputs
+from wl_preproc.eye.detect.geometry import PER_AXIS, TRUE_ANGLE
+
+_EVENTS = ("saccade", "microsaccade")
+
+
+@pytest.fixture(scope="module")
+def daemon_module(dj_conn, prefix):
+    """Activation only: `daemon.run_once()` registers the default paramsets."""
+    from wl_preproc import daemon
+
+    daemon.activate_all(prefix=prefix)
+    return daemon
+
+
+@pytest.fixture(scope="module")
+def session(daemon_module, prefix, tmp_path_factory):
+    """A stepped session detected under the defaults, then under a per-axis
+    Engbert-Kliegl paramset registered for it alone. The paramset is removed
+    afterwards, with everything computed from it, so no other module's pass
+    meets it."""
+    from wl_preproc.schema import detect, paramset
+
+    animal = new_animal().session()
+    session_key, _segment, _onsets = _build_stepped_session(
+        tmp_path_factory, dirname="trueangle", session_id=animal.session_id, subject=animal.subject,
+        session_datetime=animal.session_datetime, seed=1301)
+    daemon_module.run_once(prefix=prefix)
+    per_axis = detect.register_per_axis_paramsets(["engbert_kliegl"])["engbert_kliegl"]
+    try:
+        detect.EyeDetection.populate({**session_key, "paramset_type": "eye_detection", "paramset_idx": per_axis})
+        yield session_key, per_axis
+    finally:
+        (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": per_axis}).delete(
+            part_integrity="cascade")
+
+
+def _events(session_key, trace, idx):
+    from wl_preproc.schema import detect
+
+    return [row for row in (detect.EyeDetection.Run & {**session_key, "trace": trace, "paramset_type": "eye_detection",
+                                                      "paramset_idx": idx}).to_dicts(order_by="run_index")
+            if row["label"] in _EVENTS]
+
+
+def test_the_registered_default_is_true_angle_over_a_detectors_own_per_axis():
+    """Section 3: written after a detector's own defaults, so a detector
+    declaring the field -- its own default per-axis, what a paramset without
+    the key means (amendment 3) -- never shadows it."""
+    from wl_preproc.eye.detect.registry import DETECTORS
+    from wl_preproc.schema import detect
+
+    declaring = 0
+    for name, detector in DETECTORS.items():
+        assert detect._eye_detection_params(detector)["geometry"] == TRUE_ANGLE, name
+        if hasattr(detector.defaults, "geometry"):
+            assert detector.defaults.geometry == PER_AXIS, name
+            declaring += 1
+    assert declaring
+
+
+def test_every_default_paramset_detects_in_true_angle(session):
+    from wl_preproc.schema import detect, paramset
+
+    for name, idx in detect.register_default_paramsets().items():
+        params = (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": idx}).fetch1("params")
+        assert params["geometry"] == TRUE_ANGLE, name
+
+
+def test_a_per_axis_paramset_is_the_default_with_the_other_setting(session):
+    """Registered on request, idempotently, for the detectors named alone."""
+    from wl_preproc.eye.detect.registry import get_detector
+    from wl_preproc.schema import detect, paramset
+
+    _key, per_axis = session
+    assert detect.register_per_axis_paramsets(["engbert_kliegl"]) == {"engbert_kliegl": per_axis}
+    params = (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": per_axis}).fetch1("params")
+    assert params == {**detect._eye_detection_params(get_detector("engbert_kliegl")), "geometry": PER_AXIS}
+
+
+def test_engbert_kliegl_detects_on_the_local_velocity_and_every_run_is_measured_on_it(session):
+    """Sections 4 to 6.1: under the default, Engbert-Kliegl's runs are its
+    detector's over `local_velocity`, and each is measured in true angle,
+    its peak speed the local velocity's. Not vacuous: some size differs from
+    its per-axis measurement."""
+    import dataclasses
+
+    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS, detect_engbert_kliegl
+    from wl_preproc.eye.detect.measure import measure
+    from wl_preproc.eye.detect.velocity import local_velocity, velocity
+    from wl_preproc.schema import detect
+
+    session_key, _per_axis = session
+    idx = detect.register_default_paramsets()["engbert_kliegl"]
+    true = dataclasses.replace(DEFAULT_EK_PARAMS, geometry=TRUE_ANGLE)
+    differs = 0
+    for trace in ("left", "right"):
+        gaze, _v, offered, fs_hz = _stored_inputs(session_key, trace)
+        local = local_velocity(gaze, fs_hz)
+        rows = _events(session_key, trace, idx)
+        assert rows
+        assert [(r["run_start"], r["run_stop"], r["label"]) for r in rows] == [
+            (run.start, run.stop, run.label.value) for run in detect_engbert_kliegl(gaze, local, offered, fs_hz, true)]
+        for row in rows:
+            expected = measure(gaze, local, row["run_start"], row["run_stop"], fs_hz, geometry=TRUE_ANGLE)
+            assert (row["amplitude_deg"], row["peak_velocity_deg_s"], row["direction_deg"]) == (
+                expected.amplitude_deg, expected.peak_velocity_deg_s, expected.direction_deg), (trace, row["run_start"])
+            per_axis = measure(gaze, velocity(gaze, fs_hz), row["run_start"], row["run_stop"], fs_hz, geometry=PER_AXIS)
+            differs += per_axis.amplitude_deg != expected.amplitude_deg
+    assert differs
+
+
+def test_each_detector_is_handed_the_velocity_its_geometry_asks_for(session, monkeypatch):
+    """Section 6.1, amendment 2: under true angle Engbert-Kliegl (and BMD,
+    whose gate it is) is handed the local-frame velocity, every other
+    detector the per-axis one. Recorded where the stage hands it over,
+    because near the centre, where this session's gaze is, the two detect
+    alike. Each spied detector runs under a fresh paramset, removed after."""
+    import dataclasses
+    import functools
+
+    import numpy as np
+
+    from wl_preproc.eye.detect import registry
+    from wl_preproc.eye.detect.velocity import local_velocity, velocity
+    from wl_preproc.schema import detect, paramset
+
+    session_key, _per_axis = session
+    spied = ("engbert_kliegl", "otero_millan")
+    handed = {}
+    for name in spied:
+        original = registry.DETECTORS[name]
+
+        @functools.wraps(original.run)
+        def spy(gaze, v, available, fs_hz, params, _run=original.run, _name=name):
+            handed.setdefault(_name, []).append((gaze, v, fs_hz))
+            return _run(gaze, v, available, fs_hz, params)
+
+        monkeypatch.setitem(registry.DETECTORS, name, dataclasses.replace(original, run=spy))
+    fresh = [paramset.register("eye_detection", {**detect._eye_detection_params(registry.DETECTORS[name]),
+                                                 "probe": "the velocity handed over"}) for name in spied]
+    try:
+        for idx in fresh:
+            detect.EyeDetection.populate({**session_key, "paramset_type": "eye_detection", "paramset_idx": idx})
+        assert sorted(handed) == list(spied)
+        for name, calls in handed.items():
+            assert len(calls) == 2, name
+            for gaze, v, fs_hz in calls:
+                want = local_velocity(gaze, fs_hz) if name == "engbert_kliegl" else velocity(gaze, fs_hz)
+                assert np.array_equal(v, want), name
+    finally:
+        for idx in fresh:
+            (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": idx}).delete(
+                part_integrity="cascade")
+
+
+def test_a_per_axis_paramset_detects_and_measures_as_v1(session):
+    """Section 3: the per-axis setting is today's behaviour exactly."""
+    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS, detect_engbert_kliegl
+    from wl_preproc.eye.detect.measure import measure
+    from wl_preproc.eye.detect.velocity import velocity
+
+    session_key, per_axis = session
+    for trace in ("left", "right"):
+        gaze, _v, offered, fs_hz = _stored_inputs(session_key, trace)
+        v = velocity(gaze, fs_hz)
+        rows = _events(session_key, trace, per_axis)
+        assert rows
+        assert [(r["run_start"], r["run_stop"], r["label"]) for r in rows] == [
+            (run.start, run.stop, run.label.value) for run in detect_engbert_kliegl(gaze, v, offered, fs_hz,
+                                                                                  DEFAULT_EK_PARAMS)]
+        for row in rows:
+            expected = measure(gaze, v, row["run_start"], row["run_stop"], fs_hz, geometry=PER_AXIS)
+            assert (row["amplitude_deg"], row["peak_velocity_deg_s"]) == (
+                expected.amplitude_deg, expected.peak_velocity_deg_s), (trace, row["run_start"])
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_detect_populate.py tests/schema/test_true_angle_detection.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `5 failed, 109 passed, 1 skipped, 5 errors`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/schema/detect.py b/wl_preproc/schema/detect.py
index ec4dda2..d5a1d47 100644
--- a/wl_preproc/schema/detect.py
+++ b/wl_preproc/schema/detect.py
@@ -100,7 +100,7 @@ from pathlib import Path
 import datajoint as dj
 import numpy as np
 
-from wl_preproc.eye.detect.geometry import GEOMETRIES, UNSTATED_GEOMETRY
+from wl_preproc.eye.detect.geometry import DEFAULT_GEOMETRY, GEOMETRIES, PER_AXIS, TRUE_ANGLE, UNSTATED_GEOMETRY
 from wl_preproc.eye.detect.labels import (
     Label,
     Run,
@@ -554,7 +554,7 @@ class EyeDetection(dj.Computed):
         """
         from wl_preproc.eye.detect.registry import get_detector
         from wl_preproc.eye.detect.validity import ValidityParams
-        from wl_preproc.eye.detect.velocity import velocity
+        from wl_preproc.eye.detect.velocity import local_velocity, velocity
         from wl_preproc.eye.ohdpi import read_ohdpi
         from wl_preproc.schema import eye as eye_schema, ingest
 
@@ -620,6 +620,12 @@ class EyeDetection(dj.Computed):
             )
             gaze, _repaired = _repaired_gaze(path, file_eye, map_, fs_hz, validity_params)
             v = velocity(gaze, fs_hz)
+            # Under true angle, the eye's angular velocity in the local frame at
+            # each sample (true-angle spec section 5): what every run is
+            # measured on, and what a detector that detects in the setting's
+            # geometry is handed (`registry.Detector.detects_in_geometry`).
+            # Under per-axis, the per-axis velocity, as v1.
+            measured_v = local_velocity(gaze, fs_hz) if geometry == TRUE_ANGLE else v
             available = labels_from_runs(
                 [Run(r["run_start"], r["run_stop"], Label(r["label"]))
                  for r in (EyeValidity.Run & {**validity_key, "eye": eye_value}).to_dicts(
@@ -631,8 +637,9 @@ class EyeDetection(dj.Computed):
             # `detector.detect`, never `detector.run`: the wrapper is what
             # holds the detector to its own declared `vocabulary`, and this
             # is the one place in production that runs a detector at all.
-            spans[eye_value] = detector.detect(gaze, v, offered, fs_hz, detector_params)
-            per_eye[eye_value] = (gaze, v, offered)
+            spans[eye_value] = detector.detect(gaze, measured_v if detector.detects_in_geometry else v, offered,
+                                               fs_hz, detector_params)
+            per_eye[eye_value] = (gaze, measured_v, offered)
 
         for eye_value in ("left", "right"):
             if eye_value in refused_reason:
@@ -1749,6 +1756,21 @@ def register_default_paramsets() -> dict[str, int]:
     }
 
 
+def register_per_axis_paramsets(names) -> dict[str, int]:
+    """A per-axis `eye_detection` paramset for each detector named, returned
+    by name: its default paramset with `geometry` `"per-axis"`, v1's
+    behaviour, so its results compare with the per-axis literature
+    (true-angle spec section 3, the requester's decision 2). Registered only
+    on request -- nothing calls this by default, so detection costs what it
+    did; once registered, `EyeDetection` runs it beside the default, as it
+    runs any registered paramset. Idempotent, by content hash."""
+    from wl_preproc.eye.detect.registry import get_detector
+
+    return {name: paramset.register("eye_detection", {**_eye_detection_params(get_detector(name)),
+                                                      "geometry": PER_AXIS})
+            for name in names}
+
+
 def _eye_detection_params(detector) -> dict:
     """One detector's `eye_detection` paramset dict: its own declared
     defaults, plus the keys the whole subsystem shares.
@@ -1781,7 +1803,12 @@ def _eye_detection_params(detector) -> dict:
     exactly the per-detector threshold this paramset shape exists to prevent.
 
     `detector` is the whole `registry.Detector`, not its name, because the
-    name alone is what required a lookup table in the first place."""
+    name alone is what required a lookup table in the first place.
+
+    **`geometry` is written last too, for the same reason** (true-angle spec
+    section 3): a detector that reads sizes declares the field, defaulting to
+    per-axis -- what a paramset without the key means (amendment 3) -- and
+    the registered default is true angle."""
     from dataclasses import asdict
 
     from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG
@@ -1790,6 +1817,7 @@ def _eye_detection_params(detector) -> dict:
         "detector": detector.name,
         **asdict(detector.defaults),
         "microsaccade_max_deg": MICROSACCADE_MAX_DEG,
+        "geometry": DEFAULT_GEOMETRY,
     }
 
 
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_detect_populate.py tests/schema/test_true_angle_detection.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `119 passed, 1 skipped`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/cli/test_consensus_report.py tests/cli/test_detect_report.py tests/cli/test_quality_report.py tests/cli/test_vigor_report.py tests/nwb/test_gather.py tests/schema/test_consensus_populate.py tests/schema/test_detection_quality_populate.py tests/schema/test_eye_loss_populate.py tests/schema/test_main_sequence_populate.py tests/schema/test_nwb_build.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `152 passed`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "default-per-axis",
  "wl_preproc/schema/detect.py",
  "        \"geometry\": DEFAULT_GEOMETRY,",
  "        \"geometry\": PER_AXIS,",
  "tests/schema/test_true_angle_detection.py"
 ],
 [
  "engbert-kliegl-handed-per-axis-velocity",
  "wl_preproc/schema/detect.py",
  "measured_v if detector.detects_in_geometry else v",
  "v",
  "tests/schema/test_true_angle_detection.py"
 ],
 [
  "measured-on-per-axis-velocity",
  "wl_preproc/schema/detect.py",
  "measured_v = local_velocity(gaze, fs_hz) if geometry == TRUE_ANGLE else v",
  "measured_v = v",
  "tests/schema/test_true_angle_detection.py"
 ],
 [
  "per-axis-paramset-registered-as-true-angle",
  "wl_preproc/schema/detect.py",
  "\"geometry\": PER_AXIS})",
  "\"geometry\": TRUE_ANGLE})",
  "tests/schema/test_true_angle_detection.py"
 ]
]
```

Expected: 4 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(detect): the default paramsets detect in true angle -- the geometry key written last, per-axis registered on request, Engbert-Kliegl handed and every run measured on the local-frame velocity (sections 3 and 5)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 5: What is stored

§7, decision 5, amendment 5: `EyeDetection.Run` gains each endpoint's eccentricity and polar angle; a fixation row is measured for its positions; `EyeDetection.populate` refuses a database declared before and names its `ALTER TABLE`; the NWB event tables' columns follow, gathered from the one list the writer describes.

**Files:**
- Modify: `wl_preproc/schema/detect.py` (`EyeDetection.Run`, `populate`, `_insert_trace`, `_STORED_MEASUREMENTS`, `_POLAR_COLUMNS`, `_lacks_polar_columns`, `_polar_statement`)
- Modify: `wl_preproc/nwb/eye_events.py` (`MEASUREMENTS`), `wl_preproc/nwb/gather.py`
- Test: `tests/schema/test_true_angle_detection.py`, `tests/schema/test_detect_populate.py`, `tests/nwb/test_writers.py`

**Interfaces:**
- Consumes: Task 4's `session` fixture.
- Produces: `EyeDetection.Run.start_eccentricity_deg`, `start_polar_deg`, `end_eccentricity_deg`, `end_polar_deg`;
  `schema.detect._POLAR_COLUMNS`, `_lacks_polar_columns() -> bool`, `_polar_statement() -> str`;
  `nwb.eye_events.MEASUREMENTS` with the four.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/nwb/test_writers.py b/tests/nwb/test_writers.py
index ef5a992..2af6b98 100644
--- a/tests/nwb/test_writers.py
+++ b/tests/nwb/test_writers.py
@@ -236,6 +236,28 @@ def test_every_detectors_events_and_sources_and_agreement(tmp_path):
         assert module["detector_agreement"].to_dataframe()["value"].tolist() == [0.8]
 
 
+def test_the_event_tables_carry_each_positions_eccentricity_and_polar_angle(tmp_path):
+    """True-angle spec section 7: four columns beside the positions, and a
+    fixation row's positions with no size."""
+    from wl_preproc.nwb.eye_events import MEASUREMENTS, add_detections
+
+    runs = [{"start_s": 1.0, "stop_s": 1.04, "label": "saccade", "amplitude_deg": 1.8, "start_x_deg": 20.0,
+             "start_y_deg": 0.0, "end_x_deg": 20.0, "end_y_deg": 2.0, "direction_deg": 90.0,
+             "start_eccentricity_deg": 20.0, "start_polar_deg": 0.0, "end_eccentricity_deg": 20.1,
+             "end_polar_deg": 5.4},
+            {"start_s": 1.04, "stop_s": 2.0, "label": "fixation", "start_x_deg": 0.0, "start_y_deg": 0.0,
+             "end_x_deg": 0.0, "end_y_deg": 0.1, "direction_deg": 90.0, "start_eccentricity_deg": 0.0,
+             "start_polar_deg": None, "end_eccentricity_deg": 0.1, "end_polar_deg": 90.0}]
+    assert {"start_eccentricity_deg", "start_polar_deg", "end_eccentricity_deg", "end_polar_deg"} <= set(MEASUREMENTS)
+
+    _path, io, nwb = _write(tmp_path, lambda nwb: add_detections(nwb, [{"name": "t", "description": "d", "runs": runs}]))
+    with io:
+        table = nwb.processing["eye_events"]["t"].to_dataframe()
+        assert table["end_eccentricity_deg"].tolist() == [20.1, 0.1]
+        assert table["end_polar_deg"].tolist() == [5.4, 90.0]
+        assert np.isnan(table["start_polar_deg"].iloc[1]) and np.isnan(table["amplitude_deg"].iloc[1])
+
+
 def test_a_table_with_no_rows_writes(tmp_path):
     from wl_preproc.nwb.intervals import add_task_events
 
diff --git a/tests/schema/test_detect_populate.py b/tests/schema/test_detect_populate.py
index 516831d..03eab56 100644
--- a/tests/schema/test_detect_populate.py
+++ b/tests/schema/test_detect_populate.py
@@ -4471,7 +4471,9 @@ def test_every_stored_event_row_carries_the_geometry_its_amplitude_reads(stepped
       is exactly their true angle, and the direction is the end's in the
       eye's frame at the start (NULL only where they coincide; true-angle
       spec section 4);
-    - a row without an amplitude has none of the five."""
+    - a fixation row measured on an eye has its positions and direction and
+      no amplitude (true-angle spec decision 5);
+    - any other row has none of the five."""
     import math
 
     from wl_preproc.schema import detect
@@ -4483,12 +4485,17 @@ def test_every_stored_event_row_carries_the_geometry_its_amplitude_reads(stepped
         for trace in ("left", "right", "conjunction"):
             where = {**session_key, "trace": trace, **_detector(name)}
             for row in (detect.EyeDetection.Run & where).to_dicts():
-                if row["amplitude_deg"] is None:
+                if row["amplitude_deg"] is None and row["label"] != "fixation":
                     assert all(row[column] is None for column in geometry), (name, trace, row)
                     continue
+                if row["start_x_deg"] is None:
+                    continue
                 start = directions(np.array([row["start_x_deg"], row["start_y_deg"]]))
                 end = directions(np.array([row["end_x_deg"], row["end_y_deg"]]))
-                assert row["amplitude_deg"] == float(true_angle_deg(start, end)), (name, trace, row)
+                if row["label"] == "fixation":
+                    assert (row["amplitude_deg"], row["peak_velocity_deg_s"]) == (None, None), (name, trace, row)
+                else:
+                    assert row["amplitude_deg"] == float(true_angle_deg(start, end)), (name, trace, row)
                 if (row["start_x_deg"], row["start_y_deg"]) == (row["end_x_deg"], row["end_y_deg"]):
                     assert row["direction_deg"] is None, (name, trace, row)
                 else:
diff --git a/tests/schema/test_true_angle_detection.py b/tests/schema/test_true_angle_detection.py
index 3c22a88..f6d9270 100644
--- a/tests/schema/test_true_angle_detection.py
+++ b/tests/schema/test_true_angle_detection.py
@@ -184,3 +184,113 @@ def test_a_per_axis_paramset_detects_and_measures_as_v1(session):
             expected = measure(gaze, v, row["run_start"], row["run_stop"], fs_hz, geometry=PER_AXIS)
             assert (row["amplitude_deg"], row["peak_velocity_deg_s"]) == (
                 expected.amplitude_deg, expected.peak_velocity_deg_s), (trace, row["run_start"])
+
+
+_POSITIONS = ("start_x_deg", "start_y_deg", "end_x_deg", "end_y_deg", "start_eccentricity_deg", "end_eccentricity_deg")
+
+
+def test_a_fixation_row_carries_its_positions_and_no_size(session):
+    """Decision 5: from its first sample to its last, the positions, their
+    eccentricity and polar angle, and its direction under the setting; no
+    size and no peak speed."""
+    from wl_preproc.eye.detect.measure import measure_positions
+    from wl_preproc.schema import detect
+
+    session_key, _per_axis = session
+    idx = detect.register_default_paramsets()["engbert_kliegl"]
+    measured = 0
+    for trace in ("left", "right"):
+        gaze, _v, _offered, fs_hz = _stored_inputs(session_key, trace)
+        rows = (detect.EyeDetection.Run & {**session_key, "trace": trace, "paramset_type": "eye_detection",
+                                          "paramset_idx": idx, "label": "fixation"}).to_dicts()
+        for row in rows:
+            expected = measure_positions(gaze, row["run_start"], row["run_stop"], fs_hz, geometry=TRUE_ANGLE)
+            assert (row["amplitude_deg"], row["peak_velocity_deg_s"]) == (None, None)
+            for column in (*_POSITIONS, "start_polar_deg", "end_polar_deg", "direction_deg"):
+                assert row[column] == getattr(expected, column), (trace, row["run_start"], column)
+            measured += 1
+    assert measured
+
+
+def test_an_event_row_carries_its_endpoints_eccentricity_and_polar_angle(session):
+    from wl_preproc.eye.detect.geometry import eccentricity_polar_deg
+    from wl_preproc.schema import detect
+
+    session_key, _per_axis = session
+    rows = _events(session_key, "left", detect.register_default_paramsets()["engbert_kliegl"])
+    for row in rows:
+        import numpy as np
+
+        e, polar = eccentricity_polar_deg(np.array([[row["start_x_deg"], row["start_y_deg"]],
+                                                    [row["end_x_deg"], row["end_y_deg"]]]))
+        assert (row["start_eccentricity_deg"], row["end_eccentricity_deg"]) == (float(e[0]), float(e[1]))
+        assert (row["start_polar_deg"], row["end_polar_deg"]) == (float(polar[0]), float(polar[1]))
+
+
+def test_the_main_sequence_takes_the_rows_it_took_before(session):
+    """A fixation row now has positions but still no size, so the fit picks
+    the same rows from every row as from the event rows alone."""
+    from wl_preproc.eye.detect.main_sequence import DEFAULT_MAIN_SEQUENCE_PARAMS
+    from wl_preproc.schema import detect, main_sequence
+
+    session_key, _per_axis = session
+    idx = detect.register_default_paramsets()["engbert_kliegl"]
+    every = (detect.EyeDetection.Run & {**session_key, "trace": "left", "paramset_type": "eye_detection",
+                                       "paramset_idx": idx}).to_dicts(order_by="run_index")
+    params = DEFAULT_MAIN_SEQUENCE_PARAMS
+    assert main_sequence.selected_runs(every, 500.0, params) == main_sequence.selected_runs(
+        [row for row in every if row["label"] in _EVENTS], 500.0, params)
+
+
+def test_a_database_without_the_new_columns_detects_nothing_and_names_the_statement(session, monkeypatch):
+    """As `EyeValidity` refuses without `frac_feature_lost`: DataJoint adds
+    no column to an existing table and reads a table's columns once per
+    process, so the stage refuses before any job is reserved and says how to
+    fix it."""
+    from wl_preproc.schema import detect
+
+    assert not detect._lacks_polar_columns()
+    monkeypatch.setattr(detect, "_lacks_polar_columns", lambda: True)
+    with pytest.raises(RuntimeError) as raised:
+        detect.EyeDetection.populate()
+    assert detect._polar_statement() in str(raised.value)
+    assert "restart" in str(raised.value)
+
+
+def test_a_part_table_read_without_the_columns_is_refused(session, monkeypatch):
+    from types import SimpleNamespace
+
+    from wl_preproc.schema import detect
+
+    names = [name for name in detect.EyeDetection.Run.heading.names if name != "end_polar_deg"]
+    monkeypatch.setattr(detect.EyeDetection.Run, "_heading", SimpleNamespace(names=names))
+    assert detect._lacks_polar_columns()
+    with pytest.raises(RuntimeError, match="has no columns"):
+        detect.EyeDetection.populate()
+
+
+def test_the_statement_restores_the_columns_as_datajoint_declares_them(session):
+    """Dropped and restored by the named statement on a copy of the part
+    table: type, nullability, default, comment and place match what
+    declaring it wrote, and no row of the table itself is touched."""
+    import datajoint as dj
+
+    from wl_preproc.schema import detect
+
+    table = detect.EyeDetection.Run.full_table_name
+    copy = table[:-1] + '_restore_check`'
+    statement = detect._polar_statement()
+    assert statement.count(table) == 1
+
+    def columns(name):
+        return [row[:2] + row[3:6] + row[8:9] for row in dj.conn().query(f"SHOW FULL COLUMNS FROM {name}").fetchall()]
+
+    dj.conn().query(f"CREATE TABLE {copy} LIKE {table}")
+    try:
+        dj.conn().query(f"ALTER TABLE {copy} " + ", ".join(
+            f"DROP COLUMN {column}" for column in detect._POLAR_COLUMNS))
+        dj.conn().query(statement.replace(table, copy))
+        restored = columns(copy)
+    finally:
+        dj.conn().query(f"DROP TABLE IF EXISTS {copy}")
+    assert restored == columns(table)
PATCH
```

- [ ] **Step 2: Run them and see them fail.** `test_the_main_sequence_takes_the_rows_it_took_before` passes already: a fixation row has no size before this task and after it; it pins what Step 3 keeps.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/nwb/test_writers.py tests/schema/test_true_angle_detection.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `6 failed, 31 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/nwb/eye_events.py b/wl_preproc/nwb/eye_events.py
index 247501e..ac03e3d 100644
--- a/wl_preproc/nwb/eye_events.py
+++ b/wl_preproc/nwb/eye_events.py
@@ -13,13 +13,24 @@ from pynwb.epoch import TimeIntervals
 from wl_preproc.nwb.columns import column
 
 MEASUREMENTS = {
-    "amplitude_deg": "Amplitude, degrees (saccadic rows; NaN otherwise or when unmeasured).",
-    "peak_velocity_deg_s": "Peak velocity, degrees per second (saccadic rows; NaN otherwise).",
-    "start_x_deg": "Gaze x at the start, degrees (saccadic rows; NaN otherwise).",
-    "start_y_deg": "Gaze y at the start, degrees (saccadic rows; NaN otherwise).",
-    "end_x_deg": "Gaze x at the end, degrees (saccadic rows; NaN otherwise).",
-    "end_y_deg": "Gaze y at the end, degrees (saccadic rows; NaN otherwise).",
-    "direction_deg": "Direction, degrees counterclockwise from rightward (NaN otherwise or for no displacement).",
+    "amplitude_deg": "Size, degrees, in the geometry the table's paramset names: true angle, the angle between the "
+                     "start's and end's directions, or per axis, their distance (saccadic rows; NaN otherwise or when "
+                     "unmeasured).",
+    "peak_velocity_deg_s": "Peak speed, degrees per second, in the paramset's geometry (saccadic rows; NaN otherwise).",
+    "start_x_deg": "Gaze x at the start, per-axis degrees (measured saccadic and fixation rows; NaN otherwise).",
+    "start_y_deg": "Gaze y at the start, per-axis degrees (measured saccadic and fixation rows; NaN otherwise).",
+    "end_x_deg": "Gaze x at the end, per-axis degrees (measured saccadic and fixation rows; NaN otherwise).",
+    "end_y_deg": "Gaze y at the end, per-axis degrees (measured saccadic and fixation rows; NaN otherwise).",
+    "direction_deg": "Direction, degrees counterclockwise from rightward in [0, 360), in the paramset's geometry: true "
+                     "angle, in the eye's frame at the start (NaN otherwise or for no displacement).",
+    "start_eccentricity_deg": "The start's eccentricity, degrees of visual angle from straight ahead (rows with "
+                              "positions; NaN otherwise).",
+    "start_polar_deg": "The start's polar angle, degrees counterclockwise from rightward in [0, 360) (NaN at zero "
+                       "eccentricity and otherwise).",
+    "end_eccentricity_deg": "The end's eccentricity, degrees of visual angle from straight ahead (rows with "
+                            "positions; NaN otherwise).",
+    "end_polar_deg": "The end's polar angle, degrees counterclockwise from rightward in [0, 360) (NaN at zero "
+                     "eccentricity and otherwise).",
     "reliability": "The detector's own reliability, where it reports one (NaN otherwise).",
 }
 
diff --git a/wl_preproc/nwb/gather.py b/wl_preproc/nwb/gather.py
index 37a9b11..d5fac70 100644
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -14,6 +14,7 @@ from pathlib import Path
 import numpy as np
 
 from wl_preproc.nwb.describe import task_name
+from wl_preproc.nwb.eye_events import MEASUREMENTS
 from wl_preproc.nwb.trim import BlockSet
 # Where the wall-clock time of t = 0 comes from is the session clock's to
 # answer (`timebase/clock.py`); these stay importable here, where
@@ -519,9 +520,7 @@ def _eye(session_key: dict, session_dir: Path, blocks: BlockSet, validity_idx: i
                 if row["label"] in MASK_LABELS or not blocks.contains([start_s])[0]:
                     continue
                 runs.append({"start_s": start_s, "stop_s": float(edges[row["run_stop"]]), "label": row["label"],
-                             **{field: row.get(field) for field in ("amplitude_deg", "peak_velocity_deg_s", "start_x_deg",
-                                                                    "start_y_deg", "end_x_deg", "end_y_deg",
-                                                                    "direction_deg", "reliability")}})
+                             **{field: row.get(field) for field in MEASUREMENTS}})
             detections.append({"name": f"{name}_{trace}",
                                "description": f"{name}, the {trace} trace; eye_detection paramset {idx}: {params}",
                                "runs": runs})
diff --git a/wl_preproc/schema/detect.py b/wl_preproc/schema/detect.py
index d5a1d47..220dde8 100644
--- a/wl_preproc/schema/detect.py
+++ b/wl_preproc/schema/detect.py
@@ -450,7 +450,8 @@ class EyeDetection(dj.Computed):
         run_stop  : int unsigned
         label     : enum({_LABEL_ENUM})
         # A saccade or microsaccade run IS an event, so it carries its own
-        # measurements; every other label leaves them NULL. So does a saccade
+        # measurements; a fixation run carries its positions alone (below);
+        # every other label leaves them NULL. So does a saccade
         # run too brief for its detector's paramset to measure (NSLR's
         # `min_measured_saccade_ms`, on every trace; `_insert_trace`).
         # `reliability` is Otero-Millan's per-detection index; for an event
@@ -465,22 +466,37 @@ class EyeDetection(dj.Computed):
         peak_velocity_deg_s=null : double
         reliability=null         : double
         # Where the event starts and ends, and its direction: the gaze at
-        # the two samples `amplitude_deg` is measured between, so the
-        # amplitude is exactly their distance. Those samples are the run's
-        # first and last, except that NSLR's per-eye saccades end at
-        # `gaze[run_stop]` (its landing sample) and BMD's own per-eye events
-        # start at `gaze[run_start - 1]` (its take-off sample), each when
-        # that sample was offered and is finite (`measure.py::
-        # measure_event_run`). Degrees of visual angle,
-        # positive x rightward and y upward; the direction counterclockwise
-        # from rightward, in [-180, 180], NULL for a zero displacement. On
-        # exactly the rows that carry `amplitude_deg` (design spec
-        # `2026-09-28-saccade-geometry-design.md`).
+        # the two samples `amplitude_deg` is measured between. Those samples
+        # are the run's first and last, except that NSLR's per-eye saccades
+        # end at `gaze[run_stop]` (its landing sample) and BMD's own per-eye
+        # events start at `gaze[run_start - 1]` (its take-off sample), each
+        # when that sample was offered and is finite (`measure.py::
+        # measure_event_run`). Positions in per-axis degrees, positive x
+        # rightward and y upward (design spec
+        # `2026-09-28-saccade-geometry-design.md`). The amplitude, peak
+        # velocity and direction are in the paramset's `geometry` (design
+        # spec `2026-10-30-true-angle-saccades-design.md` section 4): under
+        # true angle, the angle between the two positions' directions, the
+        # local-frame velocity's largest magnitude and the end's direction in
+        # the eye's frame at the start; under per-axis, their distance, the
+        # per-axis speed and `atan2(dy, dx)`. The direction is
+        # counterclockwise from rightward, in [0, 360), NULL for a zero
+        # displacement. On every row that carries `amplitude_deg`, and on a
+        # `fixation` row, measured from its first sample to its last, with
+        # no amplitude or peak velocity (decision 5).
         start_x_deg=null   : double
         start_y_deg=null   : double
         end_x_deg=null     : double
         end_y_deg=null     : double
         direction_deg=null : double
+        # Each position's eccentricity and polar angle (true-angle spec
+        # section 2), the same under either geometry: the polar angle
+        # counterclockwise from rightward in [0, 360), NULL at zero
+        # eccentricity. On the rows that carry positions.
+        start_eccentricity_deg=null : double
+        start_polar_deg=null        : double
+        end_eccentricity_deg=null   : double
+        end_polar_deg=null          : double
         """
 
     class Source(dj.Part):
@@ -533,6 +549,21 @@ class EyeDetection(dj.Computed):
             & EyeValidity.proj(validity_paramset_type="paramset_type")
         ) * (paramset.ParamSet & {"paramset_type": "eye_detection"})
 
+    def populate(self, *args, **kwargs):
+        """`AutoPopulate.populate`, refused before any job is reserved while
+        this process reads `EyeDetection.Run` without `_POLAR_COLUMNS`: a
+        database declared before true angle (design spec
+        `2026-10-30-true-angle-saccades-design.md` section 7), refused here
+        for `EyeValidity.populate`'s reason. The daemon reports this stage's
+        error and runs the others."""
+        if _lacks_polar_columns():
+            raise RuntimeError(
+                f"{self.Run.full_table_name} has no columns {', '.join(_POLAR_COLUMNS)}, as read by this "
+                "process: a database declared before 2026-10-30. Nothing is detected until it is altered and "
+                f"the daemon restarted: {_polar_statement()}"
+            )
+        return super().populate(*args, **kwargs)
+
     def make(self, key: dict) -> None:
         """All three traces for one session, one mask and one detector.
 
@@ -771,7 +802,11 @@ class EyeDetection(dj.Computed):
         Writes each interval's label onto the mask BEFORE encoding -- not
         encode-then-label -- then measures every event run over its own
         final `[start, stop)`, rather than reusing whichever measurement the
-        detector made while labelling it.
+        detector made while labelling it. A `fixation` run is measured for
+        its positions alone, from its first sample to its last, on the eye an
+        event there would be measured on (`measure.py::measure_positions`;
+        true-angle spec decision 5). Every measurement is in the paramset's
+        `geometry`.
 
         **How an event run is measured is its detector's declared rule**
         (`measure.py::measure_event_run`; the requester's decisions of
@@ -936,7 +971,7 @@ class EyeDetection(dj.Computed):
         carries its own reliability (design spec
         `2026-09-28-both-eyes-fallback-design.md` section 2).
         """
-        from wl_preproc.eye.detect.measure import measure_event_run
+        from wl_preproc.eye.detect.measure import measure_event_run, measure_positions
 
         # Read the way `_min_duration_samples` reads a detector's params: a
         # field only NSLR's params declare, so every other detector has no
@@ -984,8 +1019,15 @@ class EyeDetection(dj.Computed):
 
         def _run_row(index: int, run: Run) -> dict:
             measurement = None
-            source = _measured_on(run) if run.label in (Label.SACCADE, Label.MICROSACCADE) else None
-            if source is not None:
+            source = _measured_on(run) if run.label in (Label.SACCADE, Label.MICROSACCADE, Label.FIXATION) else None
+            if source is not None and run.label is Label.FIXATION:
+                # Its positions alone, on the same eye an event there would be
+                # measured on (true-angle spec decision 5). Its samples were
+                # offered, so its gaze is finite: the mask claims every sample
+                # whose gaze or velocity is not (`validity.py`).
+                _measured_as, run_gaze, _run_v, _run_offered = source
+                measurement = measure_positions(run_gaze, run.start, run.stop, fs_hz, geometry=geometry)
+            elif source is not None:
                 span = (run.start, run.stop)
                 measured_as, run_gaze, run_v, run_offered = source
                 # The landing rule is per-eye only: a two-eye span is an
@@ -1018,8 +1060,30 @@ class EyeDetection(dj.Computed):
 _STORED_MEASUREMENTS = (
     "amplitude_deg", "peak_velocity_deg_s",
     "start_x_deg", "start_y_deg", "end_x_deg", "end_y_deg", "direction_deg",
+    "start_eccentricity_deg", "start_polar_deg", "end_eccentricity_deg", "end_polar_deg",
 )
 
+#: `EyeDetection.Run`'s columns added with true angle (2026-10-30), in the
+#: order they follow `direction_deg`.
+_POLAR_COLUMNS = ("start_eccentricity_deg", "start_polar_deg", "end_eccentricity_deg", "end_polar_deg")
+
+
+def _lacks_polar_columns() -> bool:
+    """`EyeDetection.Run`, as this process's DataJoint reads it, lacks one of
+    `_POLAR_COLUMNS`: a database declared before true angle. DataJoint reads a
+    table's columns once per process (`_lacks_feature_lost`)."""
+    return not set(_POLAR_COLUMNS) <= set(EyeDetection.Run.heading.names)
+
+
+def _polar_statement() -> str:
+    """The statement that adds `_POLAR_COLUMNS` to an `EyeDetection.Run`
+    declared before them, as DataJoint declares them: `double`, nullable, no
+    comment, after `direction_deg` in order."""
+    after = ("direction_deg", *_POLAR_COLUMNS[:-1])
+    return f"ALTER TABLE {EyeDetection.Run.full_table_name} " + ", ".join(
+        f"ADD COLUMN {column} double DEFAULT NULL COMMENT '' AFTER {previous}"
+        for column, previous in zip(_POLAR_COLUMNS, after, strict=True))
+
 
 def _measured_from_takeoff(detector, trace: str, label: Label, reliability: float | None) -> bool:
     """Whether a stored run is measured from its take-off sample: BMD's
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/nwb/test_writers.py tests/schema/test_true_angle_detection.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `37 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/nwb tests/schema/test_detect_populate.py tests/schema/test_main_sequence_populate.py tests/schema/test_nwb_build.py tests/schema/test_true_angle_detection.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `270 passed, 1 skipped`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "fixations-unmeasured",
  "wl_preproc/schema/detect.py",
  "(Label.SACCADE, Label.MICROSACCADE, Label.FIXATION)",
  "(Label.SACCADE, Label.MICROSACCADE)",
  "tests/schema/test_true_angle_detection.py tests/nwb/test_writers.py"
 ],
 [
  "end-polar-angle-not-stored",
  "wl_preproc/schema/detect.py",
  "\"start_eccentricity_deg\", \"start_polar_deg\", \"end_eccentricity_deg\", \"end_polar_deg\",\n)",
  "\"start_eccentricity_deg\", \"start_polar_deg\", \"end_eccentricity_deg\",\n)",
  "tests/schema/test_true_angle_detection.py tests/nwb/test_writers.py"
 ],
 [
  "guard-never-refuses",
  "wl_preproc/schema/detect.py",
  "    return not set(_POLAR_COLUMNS) <= set(EyeDetection.Run.heading.names)",
  "    return False",
  "tests/schema/test_true_angle_detection.py tests/nwb/test_writers.py"
 ],
 [
  "statement-adds-each-column-straight-after-direction",
  "wl_preproc/schema/detect.py",
  "after = (\"direction_deg\", *_POLAR_COLUMNS[:-1])",
  "after = (\"direction_deg\",) * len(_POLAR_COLUMNS)",
  "tests/schema/test_true_angle_detection.py tests/nwb/test_writers.py"
 ],
 [
  "event-tables-drop-the-end-polar-angle",
  "wl_preproc/nwb/eye_events.py",
  "    \"end_polar_deg\": \"The end's polar angle, degrees counterclockwise from rightward in [0, 360) (NaN at zero \"\n                     \"eccentricity and otherwise).\",\n",
  "",
  "tests/schema/test_true_angle_detection.py tests/nwb/test_writers.py"
 ]
]
```

Expected: 5 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(detect): each event's endpoints' eccentricity and polar angle and each fixation's positions are stored; a database declared before refuses the stage and names its ALTER TABLE (section 7, amendment 5)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 6: What is published

§7 and §8: the daily report's vigor names each figure's geometry, per axis for a paramset without the key; the NWB file gains `gaze_polar_{eye}` on the gaze's timestamps, the gaze's reference frame says it is per axis, its float32 polar angle never 360, and the description gains `gaze_polar` within version 3, empty for a description recorded before (amendment 6), its exported schema regenerated. A paramset's geometry neither setting names is shown as given (amendment 7).

**Files:**
- Modify: `wl_preproc/cli/report.py` (`_vigor_lines`, `_GEOMETRY_TEXT`)
- Modify: `wl_preproc/nwb/eye.py` (`add_eye_series`), `wl_preproc/nwb/describe.py`, `wl_preproc/contracts/nwb_description.py` (`EyeData.gaze_polar`)
- Modify: `docs/schemas/nwb_description.json` (exported)
- Test: `tests/cli/test_vigor_report.py`, `tests/nwb/test_writers.py`, `tests/nwb/test_describe.py`, `tests/cli/test_schemas_export.py`, `tests/schema/test_nwb_build.py`

**Interfaces:**
- Consumes: Task 4's `register_per_axis_paramsets`; Task 1's `eccentricity_polar_deg`.
- Produces: `contracts.nwb_description.EyeData.gaze_polar: list[Literal["left", "right"]]`; an NWB file's
  `processing/behavior/EyeTracking/gaze_polar_{eye}`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/cli/test_schemas_export.py b/tests/cli/test_schemas_export.py
index f42306b..e8922da 100644
--- a/tests/cli/test_schemas_export.py
+++ b/tests/cli/test_schemas_export.py
@@ -106,6 +106,11 @@ def test_the_nwb_description_schema_is_exported_for_wl_works(tmp_path):
     assert set(probe["required"]) == {"serial", "probe_type", "insertion_number", "trajectory_id", "n_electrodes",
                                       "target", "assignment", "area_from"}
     assert set(probe["properties"]["area_from"]["enum"]) == {"assignment", "target", "unknown"}
+    # Within version 3, true angle's polar series (design spec
+    # `2026-10-30-true-angle-saccades-design.md` section 8), defaulted so a
+    # description recorded before it still reads.
+    eye = schema["$defs"]["EyeData"]
+    assert "gaze_polar" in eye["properties"] and "gaze_polar" not in eye["required"]
 
 
 def test_job_request_schema_carries_the_aim_and_the_assignment(tmp_path):
diff --git a/tests/cli/test_vigor_report.py b/tests/cli/test_vigor_report.py
index 4bde803..fb5f5c4 100644
--- a/tests/cli/test_vigor_report.py
+++ b/tests/cli/test_vigor_report.py
@@ -119,25 +119,25 @@ def test_a_session_gets_each_detectors_vigor_against_its_earlier_sessions(vigor_
     _detect(vigor_schema, session, "left", saccades=_saccades(40, 0.9))
     line = _line(tmp_path, prefix, session, "left")
     assert line.endswith(f"(validity paramset {vigor_schema.validity}, fit paramset {vigor_schema.fit}): "
-                         "`engbert_kliegl` 90% (3 earlier)")
+                         "`engbert_kliegl` (true angle) 90% (3 earlier)")
 
 
 def test_fewer_than_three_earlier_sessions_is_no_history_yet(vigor_schema, tmp_path, prefix):
     session = _animal_with_history(vigor_schema, 2)
     _detect(vigor_schema, session, "left", saccades=_saccades(40, 0.9))
-    assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` no history yet (2 earlier)")
+    assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` (true angle) no history yet (2 earlier)")
 
 
 def test_too_few_saccades_say_how_many(vigor_schema, tmp_path, prefix):
     session = _animal_with_history(vigor_schema, 3)
     _detect(vigor_schema, session, "left", saccades=_saccades(10, 0.9))
-    assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` too few saccades (10)")
+    assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` (true angle) too few saccades (10)")
 
 
 def test_a_refused_detection_says_so(vigor_schema, tmp_path, prefix):
     session = _animal_with_history(vigor_schema, 3)
     _detect(vigor_schema, session, "right", status="refused")
-    assert _line(tmp_path, prefix, session, "right").endswith("`engbert_kliegl` detection refused")
+    assert _line(tmp_path, prefix, session, "right").endswith("`engbert_kliegl` (true angle) detection refused")
 
 
 def test_a_detection_without_its_row_is_not_computed_yet(vigor_schema, tmp_path, prefix):
@@ -146,7 +146,7 @@ def test_a_detection_without_its_row_is_not_computed_yet(vigor_schema, tmp_path,
     session = _animal_with_history(vigor_schema, 3)
     key = _detect(vigor_schema, session, "left", saccades=_saccades(40, 0.9), master=False)
     try:
-        assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` not computed yet")
+        assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` (true angle) not computed yet")
     finally:
         # A later `daemon.run_once()` in this suite would try to fit it.
         (detect.EyeDetection & key).delete()
@@ -171,7 +171,7 @@ def test_a_line_carries_every_detectors_figure_in_paramset_order(vigor_schema, t
     in_order = sorted(figures, key=vigor_schema.detections.get)
     assert in_order != sorted(figures)
     assert _line(tmp_path, prefix, session, "left").endswith(
-        ": " + ", ".join(f"`{name}` {figures[name]}" for name in in_order))
+        ": " + ", ".join(f"`{name}` (true angle) {figures[name]}" for name in in_order))
 
 
 def test_vigor_is_shown_where_the_sessions_own_fit_was_refused(vigor_schema, tmp_path, prefix):
@@ -180,4 +180,63 @@ def test_vigor_is_shown_where_the_sessions_own_fit_was_refused(vigor_schema, tmp
     vigor. The main-sequence review's minor."""
     session = _animal_with_history(vigor_schema, 3)
     _detect(vigor_schema, session, "left", saccades=_saccades(40, 0.9), own_fit_refused=True)
-    assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` 90% (3 earlier)")
+    assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` (true angle) 90% (3 earlier)")
+
+
+def test_a_figure_names_the_geometry_its_sizes_were_measured_in(vigor_schema, tmp_path, prefix):
+    """True-angle spec section 7: the default paramsets measure in true
+    angle and a per-axis one, registered on request, per axis; a figure says
+    which, since the two are not comparable. The per-axis paramset is
+    removed afterwards, with what was planted under it."""
+    from wl_preproc.schema import detect, paramset
+
+    per_axis = detect.register_per_axis_paramsets(["engbert_kliegl"])["engbert_kliegl"]
+    vigor_schema.detections["engbert_kliegl:per-axis"] = per_axis
+    try:
+        session = _animal_with_history(vigor_schema, 3, detectors=("engbert_kliegl:per-axis",))
+        _detect(vigor_schema, session, "left", detector="engbert_kliegl:per-axis", saccades=_saccades(40, 0.9))
+        assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` (per axis) 90% (3 earlier)")
+    finally:
+        del vigor_schema.detections["engbert_kliegl:per-axis"]
+        (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": per_axis}).delete(
+            part_integrity="cascade")
+
+
+def test_a_paramset_without_the_setting_is_named_per_axis(vigor_schema, tmp_path, prefix):
+    """True-angle spec amendment 3: a paramset written before the key
+    existed was detected per axis, and its figure says so."""
+    from wl_preproc.eye.detect.registry import get_detector
+    from wl_preproc.schema import detect, paramset
+
+    params = detect._eye_detection_params(get_detector("engbert_kliegl"))
+    del params["geometry"]
+    older = paramset.register("eye_detection", params)
+    vigor_schema.detections["engbert_kliegl:unstated"] = older
+    try:
+        session = _animal_with_history(vigor_schema, 3, detectors=("engbert_kliegl:unstated",))
+        _detect(vigor_schema, session, "left", detector="engbert_kliegl:unstated", saccades=_saccades(40, 0.9))
+        assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` (per axis) 90% (3 earlier)")
+    finally:
+        del vigor_schema.detections["engbert_kliegl:unstated"]
+        (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": older}).delete(
+            part_integrity="cascade")
+
+
+def test_a_paramset_with_an_unknown_geometry_is_named_as_given(vigor_schema, tmp_path, prefix):
+    """One paramset with a geometry neither setting names does not take the
+    report down: its figure names the value as given. (Detection refuses it:
+    `schema/detect.py::_geometry_of`.)"""
+    from wl_preproc.eye.detect.registry import get_detector
+    from wl_preproc.schema import detect, paramset
+
+    odd = paramset.register("eye_detection", {**detect._eye_detection_params(get_detector("engbert_kliegl")),
+                                              "geometry": "cyclopean"})
+    vigor_schema.detections["engbert_kliegl:cyclopean"] = odd
+    try:
+        session = _animal_with_history(vigor_schema, 3, detectors=("engbert_kliegl:cyclopean",))
+        _detect(vigor_schema, session, "left", detector="engbert_kliegl:cyclopean", saccades=_saccades(40, 0.9))
+        assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` (cyclopean) 90% (3 earlier)")
+    finally:
+        del vigor_schema.detections["engbert_kliegl:cyclopean"]
+        (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": odd}).delete(
+            part_integrity="cascade")
diff --git a/tests/nwb/test_describe.py b/tests/nwb/test_describe.py
index 6f81f82..ab1ca1d 100644
--- a/tests/nwb/test_describe.py
+++ b/tests/nwb/test_describe.py
@@ -79,7 +79,7 @@ def test_the_eye_is_described_by_the_series_and_detectors_it_has():
            "detections": [{"name": "engbert_kliegl_left"}, {"name": "engbert_kliegl_conjunction"}, {"name": "bmd_left"}],
            "usable_fraction": {"left": 0.9, "right": None}}
     out = describe(_gathered(eye=eye), status="invalid", n_critical=1, checksums=[], built_at=BUILT_AT)
-    assert out["data_types"]["eye"] == {"gaze": ["left"], "pupil": ["left", "right"]}
+    assert out["data_types"]["eye"] == {"gaze": ["left"], "gaze_polar": ["left"], "pupil": ["left", "right"]}
     assert out["data_types"]["eye_events"] == {"detectors": ["bmd", "engbert_kliegl"]}
     assert out["quality"]["eye_usable_fraction"] == {"left": 0.9, "right": None}
     assert (out["identity"]["status"], out["identity"]["n_critical"]) == ("invalid", 1)
@@ -184,3 +184,18 @@ def test_a_file_of_a_session_wl_xcon_did_not_run_describes_no_kind():
     assert (out["session_kind"], out["calibration"], out["accepted_warnings"], out["training_records"]) == (
         None, None, [], None)
     assert [run["calibration"] for run in out["runs"]] == [None]
+
+
+def test_a_description_recorded_before_the_polar_series_reads_back_without_it():
+    """True-angle spec section 8: `gaze_polar` was added within version 3, so a
+    description recorded before it reads back with it empty -- the truth for
+    a file that holds no such series -- as every field added within a version
+    does (`nwb/correct.py::as_described_now`)."""
+    from wl_preproc.nwb.correct import as_described_now
+    from wl_preproc.nwb.describe import describe
+
+    eye = {"gaze": {"left": [1], "right": None}, "pupil": {"left": [1], "right": [1]},
+           "detections": [{"name": "engbert_kliegl_left"}], "usable_fraction": {"left": 0.9, "right": None}}
+    out = describe(_gathered(eye=eye), status="written", n_critical=0, checksums=[], built_at=BUILT_AT)
+    del out["data_types"]["eye"]["gaze_polar"]
+    assert as_described_now(out)["data_types"]["eye"]["gaze_polar"] == []
diff --git a/tests/nwb/test_writers.py b/tests/nwb/test_writers.py
index 2af6b98..2978b74 100644
--- a/tests/nwb/test_writers.py
+++ b/tests/nwb/test_writers.py
@@ -170,6 +170,44 @@ def test_the_eye_series_share_one_timestamps_dataset(tmp_path):
         assert data.compression == "gzip" and data.chunks == (2000, 2)
 
 
+def test_each_eye_has_its_true_eccentricity_and_polar_angle_beside_its_gaze(tmp_path):
+    """True-angle spec section 8: a second series per eye on the gaze's own
+    timestamps, NaN where the gaze is and the polar angle NaN at the centre;
+    the gaze's reference frame names its convention and its partner."""
+    from wl_preproc.eye.detect.geometry import eccentricity_polar_deg
+    from wl_preproc.nwb.eye import add_eye_series
+
+    times, gaze, pupil = _eye_data(n=20)
+    gaze["left"][3] = [0.0, 0.0]
+    gaze["left"][4] = [np.nan, np.nan]
+    path, io, nwb = _write(tmp_path, lambda nwb: add_eye_series(nwb, times, gaze, pupil))
+    with io:
+        eye = nwb.processing["behavior"]["EyeTracking"]
+        polar = eye["gaze_polar_left"]
+        expected = np.column_stack(eccentricity_polar_deg(gaze["left"])).astype(np.float32)
+        np.testing.assert_array_equal(polar.data[:], expected)
+        assert np.isnan(polar.data[3, 1]) and polar.data[3, 0] == 0.0 and np.isnan(polar.data[4]).all()
+        assert polar.unit == "degrees" and "gaze_polar_left" in eye["gaze_left"].reference_frame
+        assert "per axis" in eye["gaze_left"].reference_frame
+    with h5py.File(path) as handle:
+        base = "processing/behavior/EyeTracking"
+        assert handle[f"{base}/gaze_polar_right/timestamps"].id == handle[f"{base}/gaze_left/timestamps"].id
+
+
+def test_a_polar_angle_just_below_360_is_stored_as_0_not_360(tmp_path):
+    """Gaze a millionth of a degree below the right horizontal meridian has a
+    polar angle of 359.99999433, which float32 rounds to 360.0: the series
+    holds [0, 360)."""
+    from wl_preproc.nwb.eye import add_eye_series
+
+    times, gaze, pupil = _eye_data(n=4, eyes=("left",))
+    gaze["left"][:] = [10.0, -1e-6]
+    _path, io, nwb = _write(tmp_path, lambda nwb: add_eye_series(nwb, times, gaze, pupil))
+    with io:
+        polar = nwb.processing["behavior"]["EyeTracking"]["gaze_polar_left"].data[:, 1]
+        assert polar.tolist() == [0.0, 0.0, 0.0, 0.0]
+
+
 def test_continuous_chunks_are_about_256_kb():
     from wl_preproc.nwb.columns import CHUNK_BYTES, continuous
 
@@ -185,7 +223,7 @@ def test_an_eye_with_no_calibration_has_no_gaze(tmp_path):
     times, gaze, pupil = _eye_data(eyes=("left",))
     _path, io, nwb = _write(tmp_path, lambda nwb: add_eye_series(nwb, times, gaze, pupil))
     with io:
-        assert list(nwb.processing["behavior"]["EyeTracking"].spatial_series) == ["gaze_left"]
+        assert sorted(nwb.processing["behavior"]["EyeTracking"].spatial_series) == ["gaze_left", "gaze_polar_left"]
 
 
 def test_the_eye_tables(tmp_path):
diff --git a/tests/schema/test_nwb_build.py b/tests/schema/test_nwb_build.py
index a220110..65b5cda 100644
--- a/tests/schema/test_nwb_build.py
+++ b/tests/schema/test_nwb_build.py
@@ -196,7 +196,8 @@ def test_the_description_describes_the_file(activation, built):
     assert description["identity"]["identifier"] == built.identifier
     assert (description["identity"]["rig"], description["identity"]["role"]) == ("rig-a", "canonical")
     assert description["subject"]["age_days"] == (_SESSION_DATETIME.date() - datetime.date(2016, 3, 2)).days
-    assert description["data_types"]["eye"] == {"gaze": ["left", "right"], "pupil": ["left", "right"]}
+    assert description["data_types"]["eye"] == {"gaze": ["left", "right"], "gaze_polar": ["left", "right"],
+                                                "pupil": ["left", "right"]}
     assert description["data_types"]["eye_events"]["detectors"] == sorted(DETECTORS)
     assert description["schema_version"] == 3
     assert [(run["run_number"], run["works_run_id"]) for run in description["runs"]] == [(1, "wr-1"), (2, "wr-2")]
@@ -385,7 +386,7 @@ def test_an_eye_without_calibration_is_left_out_and_the_rest_is_built(activation
     with NWBHDF5IO(str(result.path), "r") as handle:
         nwb = handle.read()
         behavior = nwb.processing["behavior"]
-        assert list(behavior["EyeTracking"].spatial_series) == ["gaze_left"]
+        assert sorted(behavior["EyeTracking"].spatial_series) == ["gaze_left", "gaze_polar_left"]
         assert "eye_validity_right" not in behavior.data_interfaces
         assert behavior["eye_calibration"].to_dataframe()["eye"].tolist() == ["left"]
         assert "pupil_right" in behavior["PupilTracking"].time_series
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/cli/test_schemas_export.py tests/cli/test_vigor_report.py tests/nwb/test_describe.py tests/nwb/test_writers.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `16 failed, 46 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/cli/report.py b/wl_preproc/cli/report.py
index f1be9df..6d405c1 100644
--- a/wl_preproc/cli/report.py
+++ b/wl_preproc/cli/report.py
@@ -18,6 +18,7 @@ from pathlib import Path
 from wl_sync.clock import BARCODE_EPOCH
 
 from wl_preproc.cli.doctor import scratch_headroom
+from wl_preproc.eye.detect.geometry import PER_AXIS, TRUE_ANGLE, UNSTATED_GEOMETRY
 from wl_preproc.schema import DEFAULT_PREFIX
 
 # Ingested is windowed to 24 h because "what landed since yesterday's report"
@@ -1028,6 +1029,18 @@ def _agreement_line(row: dict, detector_names: dict[int, str]) -> str:
     )
 
 
+# How a figure names the geometry its sizes were measured in.
+_GEOMETRY_TEXT = {TRUE_ANGLE: "true angle", PER_AXIS: "per axis"}
+
+
+def _geometry_text(params: dict) -> str:
+    """A paramset's geometry in words; a value neither setting names, as
+    given, so one odd paramset cannot take the report down (detection
+    refuses it: `schema/detect.py::_geometry_of`)."""
+    geometry = params.get("geometry", UNSTATED_GEOMETRY)
+    return _GEOMETRY_TEXT.get(geometry, geometry)
+
+
 def _vigor_lines(ingested_keys: set, prefix: str = DEFAULT_PREFIX) -> list[str]:
     """`### Saccade vigor per session per eye (24 h)`'s lines (main-sequence
     design spec `2026-10-07-main-sequence-design.md` section 5): for each
@@ -1050,7 +1063,10 @@ def _vigor_lines(ingested_keys: set, prefix: str = DEFAULT_PREFIX) -> list[str]:
 
     main_sequence.activate(prefix=prefix)
     table = main_sequence.SaccadeMainSequence
-    names = {row["paramset_idx"]: row["params"]["detector"]
+    # Each figure names the geometry its paramset measured sizes in (true-angle
+    # spec section 7): a per-axis paramset's vigor is not comparable with a
+    # true-angle one's. A paramset without the key was detected per axis.
+    names = {row["paramset_idx"]: f"`{row['params']['detector']}` ({_geometry_text(row['params'])})"
              for row in (paramset_schema.ParamSet & {"paramset_type": "eye_detection"}).to_dicts()}
     fit_params = {row["paramset_idx"]: MainSequenceParams(**row["params"])
                   for row in (paramset_schema.ParamSet & {"paramset_type": "main_sequence"}).to_dicts()}
@@ -1087,7 +1103,7 @@ def _vigor_lines(ingested_keys: set, prefix: str = DEFAULT_PREFIX) -> list[str]:
                 groups.setdefault((detection["trace"], detection["validity_paramset_idx"], fit_idx), []).append(
                     detection)
         for (trace, validity_idx, fit_idx), members in sorted(groups.items()):
-            figures = ", ".join(f"`{names[detection['paramset_idx']]}` {figure(detection, fit_idx)}"
+            figures = ", ".join(f"{names[detection['paramset_idx']]} {figure(detection, fit_idx)}"
                                 for detection in sorted(members, key=lambda row: row["paramset_idx"]))
             lines.append(f"- `{subject}` @ {session_datetime:%Y-%m-%d %H:%M} — {trace} (validity paramset "
                          f"{validity_idx}, fit paramset {fit_idx}): {figures}")
diff --git a/wl_preproc/contracts/nwb_description.py b/wl_preproc/contracts/nwb_description.py
index bc23c62..e111f04 100644
--- a/wl_preproc/contracts/nwb_description.py
+++ b/wl_preproc/contracts/nwb_description.py
@@ -11,7 +11,9 @@ it, 2026-10-14 added what wl-xcon's record says the session was, its kind, its
 display calibration and each run's, and the warnings it accepted (design spec
 `2026-10-14-session-kind-and-warnings-design.md` section 5), and 2026-10-18 how
 many of wl-xcon's training records the file holds (design spec
-`2026-10-18-training-records-and-backup-clock-design.md` section 3.2).
+`2026-10-18-training-records-and-backup-clock-design.md` section 3.2), and
+2026-10-30 the eyes whose true eccentricity and polar angle it holds (design
+spec `2026-10-30-true-angle-saccades-design.md` section 8).
 Version 2 gave `probes` their shape, each with both of its areas (design spec
 `2026-09-30-nwb-probes-design.md` section 3.2). Later pieces add groups and
 fields -- a processing summary with unit counts, the photodiode, video and
@@ -73,6 +75,11 @@ class SubjectInfo(_Frozen):
 
 class EyeData(_Frozen):
     gaze: list[Literal["left", "right"]]
+    # The eyes whose true eccentricity and polar angle are in the file,
+    # `gaze_polar_{eye}`: every gazed eye (2026-10-30). Empty for a file
+    # described before, which holds no such series, as every field added
+    # within a version defaults (`nwb/correct.py::as_described_now`).
+    gaze_polar: list[Literal["left", "right"]] = []
     pupil: list[Literal["left", "right"]]
 
 
diff --git a/wl_preproc/nwb/describe.py b/wl_preproc/nwb/describe.py
index afbf776..d22eb3c 100644
--- a/wl_preproc/nwb/describe.py
+++ b/wl_preproc/nwb/describe.py
@@ -96,6 +96,7 @@ def describe(data, *, status: str, n_critical: int, checksums: list[dict], built
         "data_types": {
             "eye": None if eye is None else {
                 "gaze": sorted(side for side, gaze in eye["gaze"].items() if gaze is not None),
+                "gaze_polar": sorted(side for side, gaze in eye["gaze"].items() if gaze is not None),
                 "pupil": sorted(eye["pupil"]),
             },
             "eye_events": None if eye is None else {
diff --git a/wl_preproc/nwb/eye.py b/wl_preproc/nwb/eye.py
index aaaf743..f8b9e82 100644
--- a/wl_preproc/nwb/eye.py
+++ b/wl_preproc/nwb/eye.py
@@ -10,6 +10,7 @@ from pynwb.behavior import EyeTracking, PupilTracking, SpatialSeries
 from pynwb.core import DynamicTable
 from pynwb.epoch import TimeIntervals
 
+from wl_preproc.eye.detect.geometry import eccentricity_polar_deg
 from wl_preproc.nwb.columns import column, continuous
 
 EYES = ("left", "right")
@@ -36,7 +37,9 @@ def add_eye_series(nwb: NWBFile, times: np.ndarray, gaze: dict, pupil: dict) ->
     resampled). `gaze[eye]` is `(n, 2)` degrees in the calibrated frame, the
     glitch-repaired gaze every stored detection was made from, or None for
     an eye with no calibration (section 10). `pupil[eye]` is `(n, 5)`,
-    OpenIris's `PUPIL_COLUMNS`, uncalibrated."""
+    OpenIris's `PUPIL_COLUMNS`, uncalibrated. Each gazed eye also gets
+    `gaze_polar_{eye}`, the same gaze's true eccentricity and polar angle
+    (design spec `2026-10-30-true-angle-saccades-design.md` section 8)."""
     module = behavior_module(nwb)
     shared = None
     gaze_series, pupil_series = [], []
@@ -49,12 +52,29 @@ def add_eye_series(nwb: NWBFile, times: np.ndarray, gaze: dict, pupil: dict) ->
                          "from; NaN where the recording has no finite value."),
             data=continuous(np.asarray(gaze[eye], dtype=np.float32)),
             timestamps=shared if shared is not None else continuous(np.asarray(times, dtype=np.float64)),
-            reference_frame="Degrees of visual angle in the calibrated frame: x rightward, y upward.",
+            reference_frame=("Degrees of visual angle per axis in the calibrated frame, x rightward and y upward: a "
+                             "point (X, Y) from straight ahead at distance D is (atan(X/D), atan(Y/D)). Its true "
+                             f"eccentricity and polar angle are gaze_polar_{eye}."),
             unit="degrees",
         )
         if shared is None:
             shared = series
         gaze_series.append(series)
+        eccentricity, polar = eccentricity_polar_deg(np.asarray(gaze[eye], dtype=float))
+        angles = np.column_stack([eccentricity, polar]).astype(np.float32)
+        # A polar angle just below 360 rounds up to 360 in float32; the
+        # series holds [0, 360), as `geometry.wrap_360_deg` does.
+        angles[angles[:, 1] >= 360.0, 1] = 0.0
+        gaze_series.append(SpatialSeries(
+            name=f"gaze_polar_{eye}",
+            description=("The same gaze's true eccentricity, degrees of visual angle from straight ahead, and its polar "
+                         "angle, degrees counterclockwise from rightward in [0, 360); NaN where the gaze is, and the "
+                         "polar angle NaN at zero eccentricity."),
+            data=continuous(angles),
+            timestamps=shared,
+            reference_frame="Eccentricity from straight ahead, then polar angle counterclockwise from rightward.",
+            unit="degrees",
+        ))
     for eye in EYES:
         if pupil.get(eye) is None:
             continue
PATCH
```

- [ ] **Step 4: Export the schemas.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m wl_preproc.cli.main schemas export --out docs/schemas
git status --short docs/schemas
```

Expected: ` M docs/schemas/nwb_description.json`, and no other file.

- [ ] **Step 5: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/cli/test_schemas_export.py tests/cli/test_vigor_report.py tests/nwb/test_describe.py tests/nwb/test_writers.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `62 passed`

- [ ] **Step 6: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/cli/test_report.py tests/cli/test_schemas_export.py tests/cli/test_vigor_report.py tests/nwb tests/schema/test_nwb_build.py tests/schema/test_nwb_correct.py tests/schema/test_session_listing.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `245 passed`

- [ ] **Step 7: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "report-names-every-figure-true-angle",
  "wl_preproc/cli/report.py",
  "    geometry = params.get(\"geometry\", UNSTATED_GEOMETRY)\n    return _GEOMETRY_TEXT.get(geometry, geometry)",
  "    return \"true angle\"",
  "tests/cli/test_vigor_report.py tests/nwb/test_writers.py tests/nwb/test_describe.py tests/cli/test_schemas_export.py"
 ],
 [
  "report-reads-a-keyless-paramset-as-true-angle",
  "wl_preproc/cli/report.py",
  "    geometry = params.get(\"geometry\", UNSTATED_GEOMETRY)",
  "    geometry = params.get(\"geometry\", TRUE_ANGLE)",
  "tests/cli/test_vigor_report.py tests/nwb/test_writers.py tests/nwb/test_describe.py tests/cli/test_schemas_export.py"
 ],
 [
  "report-fails-on-an-unknown-geometry",
  "wl_preproc/cli/report.py",
  "    return _GEOMETRY_TEXT.get(geometry, geometry)",
  "    return _GEOMETRY_TEXT[geometry]",
  "tests/cli/test_vigor_report.py tests/nwb/test_writers.py tests/nwb/test_describe.py tests/cli/test_schemas_export.py"
 ],
 [
  "polar-series-misnamed",
  "wl_preproc/nwb/eye.py",
  "name=f\"gaze_polar_{eye}\",",
  "name=f\"gaze_angles_{eye}\",",
  "tests/cli/test_vigor_report.py tests/nwb/test_writers.py tests/nwb/test_describe.py tests/cli/test_schemas_export.py"
 ],
 [
  "polar-series-columns-swapped",
  "wl_preproc/nwb/eye.py",
  "np.column_stack([eccentricity, polar])",
  "np.column_stack([polar, eccentricity])",
  "tests/cli/test_vigor_report.py tests/nwb/test_writers.py tests/nwb/test_describe.py tests/cli/test_schemas_export.py"
 ],
 [
  "description-names-no-polar-series",
  "wl_preproc/nwb/describe.py",
  "\"gaze_polar\": sorted(side for side, gaze in eye[\"gaze\"].items() if gaze is not None),",
  "\"gaze_polar\": [],",
  "tests/cli/test_vigor_report.py tests/nwb/test_writers.py tests/nwb/test_describe.py tests/cli/test_schemas_export.py"
 ],
 [
  "description-requires-gaze-polar",
  "wl_preproc/contracts/nwb_description.py",
  "    gaze_polar: list[Literal[\"left\", \"right\"]] = []",
  "    gaze_polar: list[Literal[\"left\", \"right\"]]",
  "tests/cli/test_vigor_report.py tests/nwb/test_writers.py tests/nwb/test_describe.py tests/cli/test_schemas_export.py"
 ],
 [
  "polar-angle-rounded-to-360-kept",
  "wl_preproc/nwb/eye.py",
  "        angles[angles[:, 1] >= 360.0, 1] = 0.0\n",
  "",
  "tests/cli/test_vigor_report.py tests/nwb/test_writers.py tests/nwb/test_describe.py tests/cli/test_schemas_export.py"
 ]
]
```

Expected: 8 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 8: Commit.**

```bash
git add -A tests wl_preproc docs/schemas
git commit -F - <<'MSG'
feat(nwb): the report's vigor names its geometry, and each NWB file holds each eye's true eccentricity and polar angle, gaze_polar_{eye}, named in its description (sections 7 and 8)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 7: The v2 contract fixtures

§9, amendment 4: `contract_fixtures.build_v2` and the CLI's `v2`, from v1's and the companion's inputs and a 60-sample trace that pins the complete-window scale; the file written by the CLI, never by hand, and pinned by its digest.

**Files:**
- Modify: `wl_preproc/eye/detect/contract_fixtures.py` (`build_v2`, `_short_trace`, `main`)
- Create: `docs/fixtures/saccade/v2.json` (written by the CLI)
- Test: `tests/eye/detect/test_contract_fixtures.py`

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: `docs/fixtures/saccade/v2.json`, SHA-256 `481eab5b1a8465da5994af16c5d332778649231261bd58ca5f38639853b46339`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/eye/detect/test_contract_fixtures.py b/tests/eye/detect/test_contract_fixtures.py
index 4caad8f..d14ed66 100644
--- a/tests/eye/detect/test_contract_fixtures.py
+++ b/tests/eye/detect/test_contract_fixtures.py
@@ -25,7 +25,7 @@ from wl_preproc.eye.detect.engbert_kliegl import (
     DEFAULT_EK_PARAMS, DEFAULT_LAMBDA, DEFAULT_MIN_DURATION_SAMPLES, _median_scale, detect_engbert_kliegl,
     thresholds,
 )
-from wl_preproc.eye.detect.geometry import PER_AXIS
+from wl_preproc.eye.detect.geometry import PER_AXIS, TRUE_ANGLE
 from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude
 from wl_preproc.eye.detect.velocity import velocity
 
@@ -152,3 +152,109 @@ def test_the_near_threshold_traces_catch_a_threshold_off_by_five_percent_or_a_wi
                                          fs_hz, params)
             changed |= [(r.start, r.stop) for r in runs] != [(run["start"], run["stop"]) for run in case["runs"]]
         assert changed, scale
+
+
+# v2, true visual angle (design spec `2026-10-30-true-angle-saccades-design.md`
+# section 9): v1's and the companion's inputs, and a short trace, with what
+# this code makes of them under the `true-angle` setting.
+
+V2 = V1.with_name("v2.json")
+V2_SHA256 = "481eab5b1a8465da5994af16c5d332778649231261bd58ca5f38639853b46339"
+_V2_PARAMS = dataclasses.replace(DEFAULT_EK_PARAMS, geometry=TRUE_ANGLE)
+
+
+def _v2() -> dict:
+    return json.loads(V2.read_text(encoding="utf-8"))
+
+
+#: v2's one tolerance, as its `about` states it (amendment 4): every number
+#: but the intervals and labels passes through sin, cos or atan2, which C
+#: libraries and other forms of the direction round differently in the last
+#: places, and a velocity component near zero loses relative digits.
+_V2_TOLERANCE = "|got - want| <= 1e-9 + 1e-12 * |want|"
+
+
+def _close(got, want) -> bool:
+    """Both null, or within v2's tolerance."""
+    if want is None or got is None:
+        return got is want
+    return bool(np.all(np.abs(np.asarray(got) - np.asarray(want)) <= 1e-9 + 1e-12 * np.abs(np.asarray(want))))
+
+
+def test_the_published_v2_is_never_edited():
+    assert hashlib.sha256(V2.read_bytes()).hexdigest() == V2_SHA256
+
+
+def test_v2_states_the_tolerance_its_readers_hold():
+    """wl-xcon's reader holds v2 to what the file says, so the file says what
+    this test holds it to."""
+    assert _V2_TOLERANCE in _v2()["about"]
+
+
+def test_v2_names_its_geometry_its_sources_and_its_defaults():
+    fixture = _v2()
+    assert (fixture["version"], fixture["geometry"]) == (2, TRUE_ANGLE)
+    assert fixture["derived_from"] == {"v1.json": V1_SHA256, "v1-thresholds.json": V1_THRESHOLDS_SHA256}
+    assert fixture["defaults"] == _v1()["defaults"] and fixture["fs_hz"] == _v1()["fs_hz"]
+
+
+def test_v2s_inputs_are_v1s_and_the_companions():
+    by_source = {"v1.json": {case["name"]: case for case in _v1()["traces"]},
+                 "v1-thresholds.json": {case["name"]: case for case in _thresholds_file()["near_threshold_traces"]}}
+    traces = _v2()["traces"]
+    assert [case["name"] for case in traces if case["inputs_from"] != "v2"] == [
+        *by_source["v1.json"], *by_source["v1-thresholds.json"]]
+    for case in traces:
+        if case["inputs_from"] != "v2":
+            source = by_source[case["inputs_from"]][case["name"]]
+            assert (case["gaze_deg"], case["available"]) == (source["gaze_deg"], source["available"]), case["name"]
+
+
+def test_a_reader_of_v2_gets_its_outputs_back_from_its_inputs():
+    """What wl-xcon's test will do, from the file alone: the intervals and
+    labels exactly, every other number to a relative 1e-12."""
+    from wl_preproc.eye.detect.measure import measure
+    from wl_preproc.eye.detect.velocity import local_velocity
+
+    fixture = _v2()
+    fs_hz = fixture["fs_hz"]
+    for case in fixture["traces"]:
+        gaze = np.array(case["gaze_deg"])
+        speed = local_velocity(gaze, fs_hz)
+        assert _close(speed, np.array(case["velocity_deg_s"])), case["name"]
+        usable = np.array([entry is None for entry in case["available"]])
+        assert all(_close(got, want) for got, want in zip(thresholds(speed, usable, _V2_PARAMS), case["eta_deg_s"],
+                                                          strict=True)), case["name"]
+        runs = detect_engbert_kliegl(gaze, speed, np.array(case["available"], dtype=object), fs_hz, _V2_PARAMS)
+        assert [(r.start, r.stop, r.label.value) for r in runs] == [
+            (run["start"], run["stop"], run["label"]) for run in case["runs"]], case["name"]
+        for r, run in zip(runs, case["runs"], strict=True):
+            got = measure(gaze, speed, r.start, r.stop, fs_hz, geometry=TRUE_ANGLE)
+            for field in ("amplitude_deg", "peak_velocity_deg_s", "direction_deg", "start_eccentricity_deg",
+                          "start_polar_deg", "end_eccentricity_deg", "end_polar_deg"):
+                assert _close(getattr(got, field), run[field]), (case["name"], r.start, field)
+            assert (got.start_x_deg, got.start_y_deg, got.end_x_deg, got.end_y_deg) == (
+                run["start_x_deg"], run["start_y_deg"], run["end_x_deg"], run["end_y_deg"])
+
+
+def test_v2s_short_trace_pins_the_complete_window_scale():
+    """Section 6.1: its thresholds are more than 1% from v1's scale, taken
+    over every usable sample, on both axes, so a reader that keeps v1's scale
+    fails on them."""
+    from wl_preproc.eye.detect.velocity import local_velocity
+
+    (case,) = [case for case in _v2()["traces"] if case["inputs_from"] == "v2"]
+    speed = local_velocity(np.array(case["gaze_deg"]), _v2()["fs_hz"])
+    usable = np.array([entry is None for entry in case["available"]])
+    old = thresholds(speed, usable, DEFAULT_EK_PARAMS)
+    assert all(abs(new / was - 1.0) > 0.01 for new, was in zip(case["eta_deg_s"], old, strict=True))
+
+
+def test_in_v2_the_step_across_the_cut_is_a_microsaccade():
+    """1.12 degrees up from (30, 0), planted: a saccade in v1, and in v2
+    0.970 degrees of true angle as planted, under the cut with the noise too
+    (wl-xcon's proposals 1 and 3)."""
+    (v1_case,) = [case for case in _v1()["traces"] if case["name"] == "off-centre-30-across-the-cut"]
+    (v2_case,) = [case for case in _v2()["traces"] if case["name"] == "off-centre-30-across-the-cut"]
+    assert [run["label"] for run in v1_case["runs"]] == ["saccade"]
+    assert [run["label"] for run in v2_case["runs"]] == ["microsaccade"]
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/eye/detect/test_contract_fixtures.py -q -p no:cacheprovider
```

Expected: `7 failed, 8 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/eye/detect/contract_fixtures.py b/wl_preproc/eye/detect/contract_fixtures.py
index 456b2d2..cf74967 100644
--- a/wl_preproc/eye/detect/contract_fixtures.py
+++ b/wl_preproc/eye/detect/contract_fixtures.py
@@ -1,9 +1,9 @@
 """The saccade detector's behaviour as versioned fixtures, for wl-xcon's
 contract tests (its XC-398; `docs/pending-wl-xcon-amendments.md`).
 
-wl-xcon's online detector mirrors `engbert_kliegl.py` -- velocity per axis,
-the median scale, the elliptic test, and `measure.amplitude` -- and its CI
-checks that the two agree. It imported this package to do so until it asked
+wl-xcon's online detector mirrors `engbert_kliegl.py` -- the velocity, the
+median scale, the elliptic test, and `measure.amplitude`; per axis as of v1,
+in true angle from v2 -- and its CI checks that the two agree. It imported this package to do so until it asked
 for files instead: its CI merges nothing red, and any change here would have
 turned it red before it could match. Each version is one JSON file of inputs
 and this code's outputs under `docs/fixtures/saccade/`. **A published version
@@ -20,7 +20,13 @@ near them. wl-xcon found that v1 pins the threshold only to about +-40% and an
 ellipse limit above 1 not at all; these close that (the requester's choice of
 2026-10-20).
 
-`python -m wl_preproc.eye.detect.contract_fixtures {v1,v1-thresholds}
+v2 is true visual angle (design spec `2026-10-30-true-angle-saccades-design.md`
+section 9): v1's traces and the companion's, read from those files, and one
+short trace that pins the threshold's scale over complete windows, each with
+what this code makes of it under the `true-angle` setting -- the local-frame
+velocity, the thresholds, the runs and their measurements.
+
+`python -m wl_preproc.eye.detect.contract_fixtures {v1,v1-thresholds,v2}
 [--out DIR]` wrote each, and refuses to write over a published file;
 `tests/eye/detect/test_contract_fixtures.py` pins each by its digest and holds
 this code to every output in it. v1's inputs are seeded numpy draws, which
@@ -31,6 +37,7 @@ from v1 rather than drawing them again, as the companion does.
 from __future__ import annotations
 
 import argparse
+import dataclasses
 import hashlib
 import json
 from pathlib import Path
@@ -41,10 +48,10 @@ from wl_preproc.eye.detect.engbert_kliegl import (
     DEFAULT_EK_PARAMS, DEFAULT_LAMBDA, DEFAULT_MIN_DURATION_SAMPLES, _median_scale, detect_engbert_kliegl,
     thresholds,
 )
-from wl_preproc.eye.detect.geometry import PER_AXIS
+from wl_preproc.eye.detect.geometry import PER_AXIS, TRUE_ANGLE
 from wl_preproc.eye.detect.labels import Label
-from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude
-from wl_preproc.eye.detect.velocity import velocity
+from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude, measure
+from wl_preproc.eye.detect.velocity import local_velocity, velocity
 
 VERSION = 1
 #: Both sides' rate: the synthetic tracker's (`synth/ohdpi.py::OHDPI_FPS`) and wl-xcon's contract test's.
@@ -195,6 +202,82 @@ def build_v1_thresholds(v1: dict, v1_sha256: str) -> dict:
     }
 
 
+# v2's short trace: 60 samples of the same noise about (10, 5), three masked
+# as a blink in the middle. Its four edge samples and the eight whose windows
+# touch the blink move the scale by 9% across and 21% up (measured
+# 2026-10-30), so its thresholds tell the complete-window scale from v1's.
+_SHORT = ("short-complete-windows", 22, (10.0, 5.0), 60, (30, 33, Label.BLINK))
+
+#: The measurements each v2 run carries, `measure`'s under true angle.
+_V2_MEASUREMENTS = ("amplitude_deg", "peak_velocity_deg_s", "direction_deg", "start_x_deg", "start_y_deg",
+                    "end_x_deg", "end_y_deg", "start_eccentricity_deg", "start_polar_deg", "end_eccentricity_deg",
+                    "end_polar_deg")
+
+
+def _short_trace() -> tuple[np.ndarray, list]:
+    """Noise alone, with the blink masked but its gaze left as it was: what
+    the trace pins is which samples the scale is taken over."""
+    _name, seed, start, samples, (first, stop, label) = _SHORT
+    gaze, available = _trace(seed, start, (), None, samples=samples)
+    available[first:stop] = [label.value] * (stop - first)
+    return gaze, available
+
+
+def build_v2(v1: dict, v1_sha256: str, companion: dict, companion_sha256: str) -> dict:
+    """v2, true angle: each input with what this code makes of it under the
+    `true-angle` setting (true-angle spec section 9)."""
+    fs_hz = v1["fs_hz"]
+    params = dataclasses.replace(DEFAULT_EK_PARAMS, geometry=TRUE_ANGLE)
+    cases = [(case["name"], case["note"], "v1.json", case["gaze_deg"], case["available"]) for case in v1["traces"]]
+    cases += [(case["name"], case["note"], "v1-thresholds.json", case["gaze_deg"], case["available"])
+              for case in companion["near_threshold_traces"]]
+    gaze, available = _short_trace()
+    cases.append((_SHORT[0], "60 samples of noise about (10, 5), samples 30-32 masked as a blink: its thresholds "
+                             "are the complete-window scale's, which differs from v1's by 9% across and 21% up",
+                  "v2", gaze.tolist(), available))
+    traces = []
+    for name, note, source, gaze_deg, mask in cases:
+        gaze = np.array(gaze_deg, dtype=float)
+        speed = local_velocity(gaze, fs_hz)
+        usable = np.array([entry is None for entry in mask])
+        runs = detect_engbert_kliegl(gaze, speed, np.array(mask, dtype=object), fs_hz, params)
+        traces.append({
+            "name": name, "note": note, "inputs_from": source, "gaze_deg": gaze_deg, "available": mask,
+            "velocity_deg_s": speed.tolist(), "eta_deg_s": list(thresholds(speed, usable, params)),
+            "runs": [{"start": int(run.start), "stop": int(run.stop), "label": run.label.value,
+                      **{field: getattr(measure(gaze, speed, run.start, run.stop, fs_hz, geometry=TRUE_ANGLE), field)
+                         for field in _V2_MEASUREMENTS}} for run in runs],
+        })
+    return {
+        "version": 2,
+        "geometry": TRUE_ANGLE,
+        "derived_from": {"v1.json": v1_sha256, "v1-thresholds.json": companion_sha256},
+        "about": "wl-preproc's Engbert-Kliegl saccade detector in true visual angle (design spec "
+                 "2026-10-30-true-angle-saccades-design.md). Gaze is in degrees per axis; a position (x, y) is the "
+                 "direction normalize(tan x, tan y, 1). velocity_deg_s: for each sample n, samples n-2..n+2 in the "
+                 "local frame at n's direction (azimuthal-equidistant; ex = normalize(x - (x.c)c), ey = c x ex), "
+                 "then (p[n+2] + p[n+1] - p[n-1] - p[n-2]) * fs / 6; the two samples at each edge are 0. "
+                 "eta_deg_s = lambda times each axis's median scale, sqrt(median(v^2) - median(v)^2) or 0, over "
+                 "complete windows only: a sample and the two on each side all available, so neither the edges "
+                 "nor any sample whose window touches a masked one. A sample is in an event when "
+                 "(vx/eta_x)^2 + (vy/eta_y)^2 > 1, runs shorter than min_duration_samples dropped. Size: the angle "
+                 "between the first and last samples' directions, atan2(|u x v|, u.v); a run is a saccade at or "
+                 "above microsaccade_max_deg. Direction: atan2 of the end's local coordinates about the start. "
+                 "Every angle in degrees, polar angles and directions in [0, 360) (mod 360, 0 where that rounds to "
+                 "360); null for a polar angle at zero eccentricity and a direction with no displacement. Peak "
+                 "speed: the largest |velocity| over the run. A direction is computed here as normalize(sin x cos y, "
+                 "cos x sin y, cos x cos y), the same direction as normalize(tan x, tan y, 1); computed otherwise, "
+                 "or on another C library, the last places differ. Intervals and labels are exact; every other "
+                 "number is held to |got - want| <= 1e-9 + 1e-12 * |want|, in its own unit, since a velocity "
+                 "component near zero is a cancelling difference that loses relative digits. inputs_from names the "
+                 "file a trace's inputs were read from. A run is [start, stop) in samples. Never edited.",
+        "fs_hz": fs_hz,
+        "defaults": {"lambda": DEFAULT_LAMBDA, "min_duration_samples": DEFAULT_MIN_DURATION_SAMPLES,
+                     "microsaccade_max_deg": MICROSACCADE_MAX_DEG},
+        "traces": traces,
+    }
+
+
 def dumps(fixture: dict) -> str:
     """The file's text: one top-level field, or one case, per line, so a
     reader can see its shape and a diff its cases."""
@@ -210,7 +293,7 @@ def dumps(fixture: dict) -> str:
 
 def main(argv: list[str] | None = None) -> int:
     parser = argparse.ArgumentParser(description="write one of the saccade detector's contract fixtures")
-    parser.add_argument("which", choices=("v1", "v1-thresholds"))
+    parser.add_argument("which", choices=("v1", "v1-thresholds", "v2"))
     parser.add_argument("--out", type=Path, default=Path("docs/fixtures/saccade"))
     args = parser.parse_args(argv)
     path = args.out / f"{args.which}.json"
@@ -218,9 +301,13 @@ def main(argv: list[str] | None = None) -> int:
         parser.error(f"{path} is published, and a published fixture is never edited")
     if args.which == "v1":
         text = dumps(build_v1())
-    else:
+    elif args.which == "v1-thresholds":
         raw = (args.out / "v1.json").read_bytes()
         text = dumps(build_v1_thresholds(json.loads(raw), hashlib.sha256(raw).hexdigest()))
+    else:
+        raw, companion = (args.out / "v1.json").read_bytes(), (args.out / "v1-thresholds.json").read_bytes()
+        text = dumps(build_v2(json.loads(raw), hashlib.sha256(raw).hexdigest(), json.loads(companion),
+                              hashlib.sha256(companion).hexdigest()))
     args.out.mkdir(parents=True, exist_ok=True)
     path.write_text(text, encoding="utf-8")
     print(path)
PATCH
```

- [ ] **Step 4: Write v2 with its CLI,** which refuses to write over a published file.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m wl_preproc.eye.detect.contract_fixtures v2
shasum -a 256 docs/fixtures/saccade/v2.json
```

Expected: `docs/fixtures/saccade/v2.json`, then `481eab5b1a8465da5994af16c5d332778649231261bd58ca5f38639853b46339  docs/fixtures/saccade/v2.json`: the digest Step 1's test pins. Another digest means another NumPy or C library than the proof's; stop and report it rather than editing the test.

- [ ] **Step 5: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/eye/detect/test_contract_fixtures.py -q -p no:cacheprovider
```

Expected: `15 passed`

- [ ] **Step 6: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/eye/detect -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `509 passed, 72 skipped, 1 xfailed`

- [ ] **Step 7: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "scale-over-every-usable-sample",
  "wl_preproc/eye/detect/engbert_kliegl.py",
  "    if params.geometry == TRUE_ANGLE:\n        usable = complete_windows(usable)",
  "    if False:\n        usable = complete_windows(usable)",
  "tests/eye/detect/test_contract_fixtures.py"
 ],
 [
  "ellipse-limit-1.1",
  "wl_preproc/eye/detect/engbert_kliegl.py",
  "    ) ** 2 > 1.0\n",
  "    ) ** 2 > 1.1\n",
  "tests/eye/detect/test_contract_fixtures.py"
 ],
 [
  "local-velocity-sign",
  "wl_preproc/eye/detect/velocity.py",
  "(plus2 + plus1 - minus1 - minus2)",
  "(plus2 + plus1 - minus1 + minus2)",
  "tests/eye/detect/test_contract_fixtures.py"
 ],
 [
  "true-direction-unwrapped",
  "wl_preproc/eye/detect/measure.py",
  "    return float(wrap_360_deg(np.degrees(np.arctan2(ly, lx))))",
  "    return float(np.degrees(np.arctan2(ly, lx)))",
  "tests/eye/detect/test_contract_fixtures.py"
 ],
 [
  "lambda-five-percent-high",
  "wl_preproc/eye/detect/engbert_kliegl.py",
  "DEFAULT_LAMBDA = 6.0",
  "DEFAULT_LAMBDA = 6.3",
  "tests/eye/detect/test_contract_fixtures.py"
 ]
]
```

Expected: 5 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 8: Commit.**

```bash
git add -A tests wl_preproc docs/fixtures
git commit -F - <<'MSG'
feat(eye): the saccade detector's v2 contract fixtures, true angle, for wl-xcon -- v1's and the companion's inputs and a short trace that pins the complete-window scale (section 9, amendment 4)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 8: The records

The spec's amendments 2-7, made while proving this plan; wl-preproc's answer in
`HANDOVER-wl-expcontroller.md`; this piece's handoff; and the checkpoint's and `wl.yaml`'s lines (spec §12).
Then the full suite, once, on both interpreters.

**Files:**
- Modify: `docs/superpowers/specs/2026-10-30-true-angle-saccades-design.md`, `HANDOVER-wl-expcontroller.md`,
  `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-10-30-true-angle-saccades.md`

**Interfaces:** none; documents only.

- [ ] **Step 1: Apply the records.** Save this script outside the repository as `records.py` and run it from
  the repository root.

```python
"""XC-398's records: the spec's amendments 2-7, wl-preproc's answer in
HANDOVER-wl-expcontroller.md, and the checkpoint's and wl.yaml's lines (built,
not merged; next, the review). Run from the repository root."""
import pathlib

root = pathlib.Path.cwd()


def swap(rel, old, new):
    path = root / rel
    text = path.read_text()
    assert text.count(old) == 1, (rel, old[:70])
    path.write_text(text.replace(old, new))


spec = root / "docs/superpowers/specs/2026-10-30-true-angle-saccades-design.md"
spec.write_text(spec.read_text().rstrip("\n") + """

## Amendments, 2026-10-30, made while proving the plan

2. **BMD's gate follows the setting, as Engbert-Kliegl does** (replacing §6.2's "BMD: its own detection is
   unchanged" for the gate). The gate is Engbert-Kliegl at its registered defaults (BMD design spec §4), and
   `schema/consensus.py::DetectorAgreement` leaves Engbert-Kliegl's saccade samples out of the BMD and
   Engbert-Kliegl pair because BMD's copies are identical to them by construction (`registry.Detector.
   copies_saccades_from`). A per-axis gate beside a true-angle Engbert-Kliegl would break that silently. So
   `registry.Detector.detects_in_geometry` is true for Engbert-Kliegl and BMD: under true angle both are
   handed the local-frame velocity, and BMD runs its gate with its own `geometry`. The BMD authors' sampler
   reads positions and is unchanged, so its fidelity tests stand. BMD's own events are split by the
   setting's size (`measure_event_run`). **One pair stays approximate:** `DetectorAgreement` excludes a
   copied saccade by detector name, so a per-axis Engbert-Kliegl registered on request beside a true-angle BMD
   (or the reverse) is compared with an exclusion that is no longer exact. The blend uses the default
   paramsets alone, which share one geometry, so it is unaffected; recorded, not fixed.
3. **A paramset without the key means per-axis.** Every one written before the key existed was detected per
   axis, so `geometry.UNSTATED_GEOMETRY` is per-axis: a detector's own params dataclass defaults to it, and the
   schema reads `params.get("geometry", "per-axis")`. The registered default writes `true-angle` after a
   detector's own defaults (`_eye_detection_params`), so no detector shadows it. This keeps `DEFAULT_*_PARAMS`,
   the v1 builders and every fidelity test per axis without special code.
4. **v2 holds only its intervals and labels exactly** (correcting §9's "the thresholds are held exactly"):
   under true angle every other number passes through `sin`, `cos` and `atan2`, which C libraries, and other
   forms of the direction (`normalize(tan x, tan y, 1)` against this code's `normalize(sin x cos y, cos x sin y,
   cos x cos y)`), round differently in the last places, and a velocity component near zero is a cancelling
   difference that loses relative digits. So every one is held to `|got - want| <= 1e-9 + 1e-12 * |want|`, in
   its own unit, and nulls as nulls; the file states the rule and the form it computes directions in. A
   relative 1e-12 alone failed 277 of v2's 11,320 velocity components recomputed with wl-xcon's own
   `viewport.center` and `local_true_angle` (worst 1.9e-12 deg/s); the rule fails none. The file names its
   sources' digests (`derived_from`) and each trace's (`inputs_from`); its short trace is 60 samples, three
   masked, on which the complete-window scale is 9% and 21% from v1's. wl-xcon was told on 2026-10-30.
5. **A database declared before true angle is refused, not re-declared** (correcting §7's "the table is
   re-declared"): DataJoint adds no column to an existing table, so `EyeDetection.populate` refuses while this
   process reads `EyeDetection.Run` without the four new columns and names the `ALTER TABLE` that adds them
   (`schema/detect.py::_polar_statement`), as `EyeValidity` does for `frac_feature_lost` (the eye-loss design).
   **After the statement,** the default paramsets registered before the key stay registered and, meaning
   per-axis, keep running beside the new defaults, their earlier rows holding directions in [-180, 180] and no
   fixation positions: delete those `eye_detection` paramsets with what was computed from them (nothing real is
   stored).
6. **Two points of §8's NWB additions.** `NwbDescription`'s `data_types.eye.gaze_polar` defaults to empty, as
   every field added within a version does, so a description recorded before it still reads
   (`nwb/correct.py::as_described_now`, which a correction and publishing's adoption re-read every one with);
   and `gaze_polar_{eye}`'s polar angle, stored as float32, is 0 where float32 would round it up to 360.
7. **An `eye_detection` paramset whose `geometry` is neither setting is refused** where the stage reads it
   (`schema/detect.py::_geometry_of`), rather than detecting per axis and failing in measurement; the daily
   report names such a paramset's geometry as given rather than failing.
""")

swap("HANDOVER-wl-expcontroller.md",
     """  they could. The XC-398 spec says which is meant, and v2 pins it with a short trace.""",
     """  they could. The XC-398 spec says which is meant, and v2 pins it with a short trace.
- *wl-preproc's answer, 2026-10-30: the spec* (`docs/superpowers/specs/2026-10-30-true-angle-saccades-design.md`,
  the requester's decisions of 2026-10-30), *sent to wl-xcon the same day.* Yes to all four proposals. Every
  detector's sizes, peak speeds and directions, and its endpoints' eccentricity and polar angle, are in true
  angle, and the 1-degree cut is read in true angle; Engbert-Kliegl (and BMD's gate, which is Engbert-Kliegl)
  detects on the local-frame velocity, its scale over complete windows only, as wl-xcon's online copy does;
  the four detectors ported from their authors' code keep their own detection per axis. Per axis stays here
  as a paramset setting, registered on request; true angle is the default. Every angle is in [0, 360) (mod
  360, 0 where that rounds to 360), the polar angle null at zero eccentricity and a direction null for no
  displacement (amendment 1, the PI's "0 to 360 in both repos" and "Empty at center"). Fixations carry their
  positions, eccentricity, polar angle and direction, and no size. Each NWB file gains each eye's true
  eccentricity and polar angle, `gaze_polar_{eye}`.
- *v2:* `docs/fixtures/saccade/v2.json`, made by `python -m wl_preproc.eye.detect.contract_fixtures v2` from
  v1's and the companion's inputs and one short trace. Intervals and labels exact; every other number to
  `|got - want| <= 1e-9 + 1e-12 * |want|`, since it passes through `sin`, `cos` or `atan2` and a velocity
  component near zero loses relative digits (amendment 4). Built on `feat/true-angle-saccades`, not merged; its
  SHA-256 goes to wl-xcon with the merge.""")

swap("docs/CHECKPOINT.md",
     """> fixed how its box unwraps code-word ticks (its ask 6; its `d309bf1`). Next, in the requester's
> order of 2026-10-30: XC-398, saccades in true visual angle (spec approved,
> `specs/2026-10-30-true-angle-saccades-design.md` on `spec/true-angle-saccades`, its plan being
> proved); then part 2, the bridges, and part 3b, the NI as the clock. Fine-tuning
> U'n'Eye to the DPI tracker waits for hand-labelled lab data.
>""",
     """> fixed how its box unwraps code-word ticks (its ask 6; its `d309bf1`). **XC-398, saccades in
> true visual angle, is built** on `feat/true-angle-saccades`, not merged
> (`specs/2026-10-30-true-angle-saccades-design.md`, the requester's decisions of 2026-10-30;
> `handoffs/2026-10-30-true-angle-saccades.md`): every detector measures in true angle by
> default, Engbert-Kliegl detects in it, per axis is a paramset registered on request, fixations
> carry positions, the NWB file each eye's eccentricity and polar angle, and v2 of the fixtures
> is made. Next: its review; then part 2, the bridges, and part 3b, the NI as the clock, in the
> requester's order of 2026-10-30. Fine-tuning U'n'Eye to the DPI tracker waits for
> hand-labelled lab data.
>
> *Until XC-398 was built, this said its plan was being proved; true when written.*
>""")

swap("wl.yaml",
     """      code's outputs: velocity, the median scale, the thresholds, the
      intervals detected with their labels, and each interval's size.
      v1.json and its companion v1-thresholds.json, versioned, and never
      edited once published.""",
     """      code's outputs: velocity, the median scale, the thresholds, the
      intervals detected with their labels, and each interval's size.
      v1.json and its companion v1-thresholds.json are per axis; v2.json is
      true visual angle (the local-frame velocity, the complete-window
      scale, and each interval's size, peak speed, direction and endpoints'
      eccentricity and polar angle). Versioned, and never
      edited once published.""")

swap("wl.yaml",
     """    next, XC-398's true-angle saccades (spec approved 2026-10-30), then
    part 2, the bridges, and part 3b, the NI as the
    clock.""",
     """    XC-398's true-angle saccades are built on feat/true-angle-saccades
    (docs/handoffs/2026-10-30-true-angle-saccades.md), not merged; next,
    their review; then part 2, the bridges, and part 3b, the NI as the
    clock.""")
```

Expected: no output, and exit 0.

- [ ] **Step 2: Write the handoff.** Its §5 keeps `{{...}}` slots until Step 5.

```bash
cat > docs/handoffs/2026-10-30-true-angle-saccades.md <<'HANDOFF'
# Saccades in true visual angle (XC-398)

**`specs/2026-10-30-true-angle-saccades-design.md`**: wl-xcon's XC-398, the PI's "Change both repos". Gaze is
in degrees per axis, and distances taken in it shrink off centre: a 2-degree step up from (20, 0) is 1.879
degrees of visual angle. Sizes, speeds and directions are now measured between gaze directions, Engbert-Kliegl
detects in the eye's local frame as wl-xcon's online copy will, and v2 of the contract fixtures pins it.
- **Branch:** `feat/true-angle-saccades`, from `plan/true-angle-saccades`.
- **Spec:** the design, its amendment 1 (every angle in [0, 360)) and amendments 2-7, made while proving the
  plan.
- **Plan:** `docs/superpowers/plans/2026-10-30-true-angle-saccades.md`.

Every line of the plan was proven in a scratch branch before the plan was written.

---

## 1. What was built

- **`eye/detect/geometry.py`**: wl-xcon's definitions to the formula: a position's direction, the true angle
  between two, a position's eccentricity and polar angle, the local frame at a direction, every angle in
  [0, 360).
- **The setting:** the shared paramset key `geometry`, `true-angle` by default and written last; a detector's
  own default, and a paramset without the key, per-axis; `schema/detect.py::register_per_axis_paramsets`
  registers per-axis on request.
- **Measurement** (`measure.py`) takes the setting, with no default: size, direction and peak speed in it, and
  each endpoint's eccentricity and polar angle; `velocity.local_velocity`, the five-point estimator in the
  local frame at each sample. Size is one function, so Otero-Millan's displacement floor and split, and
  Nyström-Holmqvist's glissade comparison, follow the setting.
- **Engbert-Kliegl** detects on the local-frame velocity under true angle, its scale over complete windows only
  (`engbert_kliegl.complete_windows`); BMD's gate, which is Engbert-Kliegl, follows it
  (`registry.Detector.detects_in_geometry`).
- **Stored:** `EyeDetection.Run` gains each endpoint's eccentricity and polar angle; fixation rows carry their
  positions and direction; a database declared before refuses the stage and names its `ALTER TABLE`.
- **Published:** the daily report's vigor names each figure's geometry; the NWB file gains `gaze_polar_{eye}`
  and its description `gaze_polar`; `docs/fixtures/saccade/v2.json`.

## 2. What the other repositories must do

- **wl-xcon:** switch its `saccade.py` to v2 in one commit, against the file's SHA-256, sent with the merge; its
  reader holds intervals and labels exactly and every other number to `|got - want| <= 1e-9 + 1e-12 * |want|`
  (amendment 4; told on 2026-10-30, replacing the relative 1e-12 it was told first; the SHA-256 goes with the merge).
- **wl.works:** the NWB description gains `data_types.eye.gaze_polar` within version 3; a reader ignores what
  it does not know, so nothing is required, and a note goes with the merge.

## 3. Still open

- **wl-xcon's XC-399** (each shown stimulus's eccentricity and polar angle on its trial's row) and its record
  fields for builds C2a and C2b: asks to come.

## 4. The rulings

The spec's §0 and its amendments 1-7.

## 5. What execution measured

Executed on {{DATE}}: Task 1 `{{SHA1}}`, Task 2 `{{SHA2}}`, Task 3 `{{SHA3}}`, Task 4 `{{SHA4}}`, Task 5
`{{SHA5}}`, Task 6 `{{SHA6}}`, Task 7 `{{SHA7}}`.
- **The full suite, once, on both interpreters,** with the BMD and NSLR references set, as CI has them, the
  Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, as in CI:
  - 3.11: **{{S311}}**;
  - 3.13: **{{S313}}**.
HANDOFF
```

- [ ] **Step 3: Check the manifest.**

```bash
.venv/bin/wl-check
```

Expected: `wl.yaml: no findings`.

- [ ] **Step 4: The full suite, once, on both interpreters,** in parallel, with the BMD and NSLR references
  set, as CI has them, the Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, each logged
  outside the repository:

```bash
R=$HOME/.cache/wl-preproc-references
LOG=$(mktemp -d)
unset WLPP_OHDPI_REFERENCE
suite() { env PYTHONPATH=$PWD PYTHONDONTWRITEBYTECODE=1 WLPP_NSLR_REFERENCE=$R WLPP_BMD_REFERENCE=$R/bmd/BMD \
  WLPP_BMD_BOOST_INCLUDE=$R/bmd/boost_1_86_0 WLPP_ANDERSSON_DATA=$R/andersson \
  "$1" -m pytest -q -p no:cacheprovider -o faulthandler_timeout=1800 > "$LOG/suite-$2.txt" 2>&1; echo "$2 exit $?"; }
suite .venv/bin/python 311 & suite $HOME/.cache/wl-preproc-venv313/bin/python 313 & wait
tail -1 $LOG/suite-311.txt $LOG/suite-313.txt
```

Expected, each exiting 0: 3.11, **2601 passed, 26 skipped, 1 deselected, 1 xfailed**; 3.13, **2601 passed, 27 skipped, 1 xfailed**. About 30 minutes, or 40 on a busy host. That is 72 tests more than `main`'s, this piece's; `tests/responder/test_health.py`'s floor test skips on a host with more than 800 GiB free, so on a fuller disk it passes instead. CI, without the
Andersson data, should show about 2592 passed, 36 skipped. A MySQL container that does not start within its 120 s under
heavy load errors every database test at once; that is the known flake, and the suite is run again.

- [ ] **Step 5: Fill the handoff's §5** with this execution's own figures: `{{DATE}}` the day,
  `{{SHA1}}` to `{{SHA7}}` Tasks 1-7's commits (`git log --oneline -8`), and `{{S311}}` and
  `{{S313}}` Step 4's two summary lines. Then `grep -c '{{' docs/handoffs/2026-10-30-true-angle-saccades.md`.

Expected: `0`.

- [ ] **Step 6: Commit.**

```bash
git add -A docs wl.yaml HANDOVER-wl-expcontroller.md
git commit -F - <<'MSG'
docs: XC-398's records -- spec amendments 2-7, wl-preproc's answer in HANDOVER-wl-expcontroller.md, its handoff, and the checkpoint's and wl.yaml's lines: built, not merged; next, the review

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```
