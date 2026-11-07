# Lost Data and the Screen Region Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Carry a saccade across a short loss of data, counted and flagged, while an invalid sample still ends it,
as wl-xcon's online detector does; and mask gaze at each session's recorded visible field plus 2 degrees.

**Architecture:** The validity mask tells `lost` from `invalid` and decides which losses are short; every detector
meets exactly what is stored, Engbert-Kliegl carrying a run by wl-xcon's rule and the other six running unchanged
under one shared rule; each run stores its lost samples and its flag, which the main sequence, the NWB file and the
report read; the region comes from `core.SessionSetup`; v3 of the fixtures states the rule for wl-xcon.

**Tech Stack:** Python 3.11/3.13, numpy, DataJoint 2.3.3 on MySQL (testcontainers in tests), pynwb.

**Spec:** `docs/superpowers/specs/2026-11-06-eye-lost-data-and-region-design.md`: §0-§14 and its seven rulings,
approved 2026-11-06; amendments 1-10 are Task 8's, made while proving this plan, with wl-xcon's check of that day.

**Proven before written.** Every step below was run on a scratch branch and replayed from this plan's own text to
an identical tree: 38 mutation checks caught, and the full suite on both interpreters as Task 8 states.

## Global Constraints

- **Lost or invalid** (§2, decisions 2): a lost reflection, a value that is not a number, a sample over 1000°/s,
  a repaired glitch and the four velocities a dropped frame spans are `lost`; a blink with its padding and gaze off
  the screen are `invalid`. A margin takes the kind of what it grows; a dropped frame takes none; `blink` over
  `invalid` over `lost`.
- **Short is 50 ms, the loss of data** (§3, amendment 2): a lost stretch is carried while no two positions measured
  in turn across it are more than `floor(max_lost_ms · fs_hz / 1000 + 1e-9)` frames apart, inclusive; otherwise,
  or without a measured sample on a side, it is withheld as `invalid`.
- **Engbert-Kliegl's rule is wl-xcon's** (§4, amendment 6): an unknown velocity carries an open run and never opens
  one or counts toward the minimum; a short loss at a run's end is carried to the landing, that first measured
  sample after it the run's last. **The other six run unchanged**, and one shared rule holds every detector's runs.
- **A run is flagged** when a lost sample lies inside it or in the two samples before its start (amendment 7);
  **the main sequence and vigor leave flagged saccades out** (decision 5.2).
- **The region is the session's recorded field plus 2 degrees**, per axis, per eye; the placeholder where none
  was recorded (§7).
- **Schema changes, inside the January 2027 window:** `lost` in the label enum of `EyeValidity.Run` and
  `EyeDetection.Run`; `EyeDetection.Run.n_lost`, `.flagged`; `EyeValidity`'s region columns; a new table,
  `core.SessionSetup`. A database declared before is refused with its statements named, and nothing here runs
  them.
- **A paramset stored before masks as it did:** every new `ValidityParams` field defaults so.
- **v2 is never edited**; v3 is written by its command and never edited once published.
- **Execute on a branch `feat/eye-lost-and-region`** cut from `plan/eye-lost-and-region` once this plan is
  committed there.
- **Python 3.11 and 3.13**: the full suite runs once on each, at the end (Task 8).
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code,
  data or tables; leave the stray `wl-preproc` symlink at the repository root out of every commit.
- **Commands run from the repository root.** In a git worktree, `.venv/bin/python` is the main checkout's;
  every test command sets `PYTHONPATH=$PWD`, `PYTHONDONTWRITEBYTECODE=1` and the NSLR and BMD references, as CI
  does; export the three `WLPP_*` variables before running `mutate.py`.
- **Patches apply with `git apply` and must apply cleanly.**
- **Every commit ends with:**
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
  ```

## Review Focus

The five inputs the spec implies that a person is most likely to meet, most likely first; each is pinned by a
test in the task that owns the code:
1. **One dropped frame inside a saccade:** one saccade, as wide, flagged, with its lost samples counted, for every
   detector. `test_a_dropped_frame_inside_a_saccade_leaves_it_whole` (Task 2, all seven) and
   `test_a_saccade_across_a_dropped_frame_is_one_saccade_flagged_with_its_lost_samples` (Task 3, stored).
2. **A saccade that lands inside a short loss:** carried to its landing, its size whole, flagged.
   `test_a_short_loss_at_a_runs_end_is_carried_to_the_landing` (Task 2),
   `test_a_run_with_lost_ends_is_measured_out_to_the_measured_samples_beyond_them` (Task 3), and v3's
   `a-short-loss-at-a-saccades-landing` (Task 7).
3. **A paramset stored before:** it masks as it did, at its own region.
   `test_a_paramset_stored_before_lost_data_was_carried_masks_as_before` (Task 1) and
   `test_a_paramset_stored_before_the_region_came_from_the_session_keeps_its_own` (Task 4).
4. **A session that recorded no display setup** (the reference recording, any before 2026-09-29): the placeholder,
   said. `test_a_session_that_recorded_no_field_is_masked_at_the_placeholder` (Task 4) and the report's
   `_placeholder_region_lines` (Task 5).
5. **A database declared before:** both stages refuse and name statements that restore the tables as declared.
   `test_the_statements_restore_the_tables_as_datajoint_declares_them` (Task 6).

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
| `wl_preproc/eye/detect/labels.py`, `eye/detect/validity.py` | lost or invalid; the bound; the fill; the region | 1, 4 |
| `wl_preproc/eye/detect/engbert_kliegl.py`, `eye/detect/registry.py`, the six detectors' adapters | carrying a run; the shared rule | 2 |
| `wl_preproc/eye/detect/measure.py`, `schema/detect.py` | the filled gaze, each run's count and flag, the measurement; the region's columns; the guards | 3, 4, 6 |
| `wl_preproc/events/rigsession.py`, `schema/core.py`, `schema/events.py`, `synth/peripherals.py`, `synth/recipe.py` | the session's display setup | 4 |
| `wl_preproc/eye/detect/main_sequence.py`, `schema/main_sequence.py`, `nwb/eye_events.py`, `nwb/gather.py`, `cli/report.py` | the fits, the NWB file, the report | 5 |
| `wl_preproc/eye/detect/contract_fixtures.py`, `docs/fixtures/saccade/v3.json`, `wl.yaml` | v3 | 7 |
| the spec, three earlier specs, `HANDOVER-wl-expcontroller.md`, `HANDOVER-wl-sync.md`, the handoff (new), `docs/CHECKPOINT.md`, `wl.yaml` | the records | 8 |

---

### Task 1: The mask tells lost data from invalid

§2 and §3, the requester's decisions 2 and 5.1, and amendments 1-3: a sample not offered as measured is `lost` (no measurement, or none to trust) or `invalid` (a measurement, of the wrong thing); a dropped frame costs the four rows whose velocity spans it, with no margin; a lost stretch is carried while no two positions measured in turn across it are more than `max_lost_ms` apart, in whole frames, and withheld as `invalid` otherwise; `fill_lost` is what a detector will be offered over a carried loss. A paramset stored before names no bound and masks as it did. The stored mask's missing-gaze and unreadable-pupil runs read `lost`; every detector still withholds them until Task 2.

**Files:**
- Modify: `wl_preproc/eye/detect/labels.py` (`Label.LOST`, `NOT_INTERSECTED`, `may_label`)
- Modify: `wl_preproc/eye/detect/validity.py` (`MASK_PRECEDENCE`, `ValidityParams.max_lost_ms`, `validity_labels`' `repaired`, `frame_numbers`, `carried_frames`, `_uncarried`, `rows_across_gaps`, `fill_lost`)
- Test: `tests/eye/detect/test_labels.py`, `tests/eye/detect/test_validity.py`, `tests/schema/test_detect_populate.py`, `tests/schema/test_eye_loss_populate.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `labels.Label.LOST`; `labels.may_label(available) -> np.ndarray[bool]` (`None` or `Label.LOST`);
  `validity.ValidityParams.max_lost_ms: float = 0.0` (50.0 in `DEFAULT_VALIDITY_PARAMS`);
  `validity_labels(..., *, fs_hz, repaired=None)`; `validity.frame_numbers(n, frame_gaps) -> np.ndarray`;
  `validity.carried_frames(max_lost_ms, fs_hz) -> int`; `validity._uncarried(lost, withheld, untrusted, frames,
  max_frames) -> np.ndarray`; `validity.rows_across_gaps(n, frame_gaps) -> np.ndarray`;
  `validity.fill_lost(gaze_deg, offered, frames, keep) -> np.ndarray`; `validity._dilate`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/eye/detect/test_labels.py b/tests/eye/detect/test_labels.py
index b500142..12abf63 100644
--- a/tests/eye/detect/test_labels.py
+++ b/tests/eye/detect/test_labels.py
@@ -15,11 +15,13 @@ from wl_preproc.eye.detect.labels import (
 )
 
 
-def test_all_eight_labels_are_declared_even_though_stage_one_uses_five():
+def test_all_nine_labels_are_declared_even_though_stage_one_uses_six():
     """The enum ships complete. Adding a value later is a schema change, and
-    the migration window closes January 2027."""
+    the migration window closes January 2027: `lost` joined inside it, on
+    2026-11-06 (design spec `2026-11-06-eye-lost-data-and-region-design.md`
+    section 8)."""
     assert {label.value for label in Label} == {
-        "blink", "invalid", "saccade", "microsaccade",
+        "blink", "invalid", "lost", "saccade", "microsaccade",
         "pso", "pursuit", "drift", "fixation",
     }
 
diff --git a/tests/eye/detect/test_validity.py b/tests/eye/detect/test_validity.py
index e27db5f..737d2a5 100644
--- a/tests/eye/detect/test_validity.py
+++ b/tests/eye/detect/test_validity.py
@@ -1,6 +1,7 @@
 from dataclasses import asdict
 
 import numpy as np
+import pytest
 
 from wl_preproc.eye.detect.labels import Label
 from wl_preproc.eye.detect.validity import (
@@ -619,3 +620,179 @@ def test_a_blink_at_the_recording_s_edge_is_padded_only_inward():
     assert [labels[i] for i in range(5, 15)] == [Label.INVALID] * 10
     assert [labels[i] for i in range(30, 35)] == [Label.INVALID] * 5
     assert all(labels[i] is None for i in range(15, 30))
+
+
+# Design spec `2026-11-06-eye-lost-data-and-region-design.md` section 2: a
+# sample not offered is LOST (no measurement, or none to trust), carried
+# across by a run where the loss is short, or INVALID (a measurement, of the
+# wrong thing), which ends one. `_QUIET` above names no bound, so it masks as
+# every paramset stored before lost data was carried; `_CARRY` carries it.
+_CARRY = ValidityParams(**{**asdict(_QUIET), "max_lost_ms": 50.0})
+
+
+def test_a_lost_reflection_a_value_that_is_not_a_number_and_an_impossible_speed_are_lost():
+    gaze, vel, loss, gaps = _clean(40)
+    loss.feature_lost[10] = True
+    gaze[20] = np.nan
+    vel[30] = (2000.0, 0.0)
+
+    labels = validity_labels(gaze, vel, loss, gaps, _CARRY, fs_hz=FS_HZ).labels
+
+    assert [labels[i] for i in (10, 20, 30)] == [Label.LOST] * 3
+    assert sum(label is not None for label in labels) == 3
+
+
+def test_a_blink_and_gaze_off_the_screen_are_invalid():
+    gaze, vel, loss, gaps = _clean(20)
+    loss.blink[5] = True
+    gaze[12] = (25.0, 0.0)
+
+    labels = validity_labels(gaze, vel, loss, gaps, _CARRY, fs_hz=FS_HZ).labels
+
+    assert labels[5] is Label.BLINK and labels[12] is Label.INVALID
+
+
+def test_a_repaired_glitch_is_lost():
+    """Filled in by a straight line before the mask reads it: offered, but no
+    measurement (section 2)."""
+    gaze, vel, loss, gaps = _clean(20)
+    repaired = np.zeros(20, dtype=bool)
+    repaired[7] = True
+
+    labels = validity_labels(gaze, vel, loss, gaps, _CARRY, fs_hz=FS_HZ, repaired=repaired).labels
+
+    assert labels[7] is Label.LOST and sum(label is not None for label in labels) == 1
+
+
+def test_a_frame_gap_is_lost_and_takes_no_margin():
+    """Section 2.2: a dropped frame is not a tracking failure, and its
+    neighbours' positions are measurements; only the four velocities that
+    span it are lost."""
+    from wl_preproc.eye.ohdpi import FrameGap
+
+    gaze, vel, loss, _ = _clean(30)
+    params = ValidityParams(**{**asdict(_CARRY), "dilate_samples": 5})
+
+    labels = validity_labels(gaze, vel, loss, (FrameGap(row=14, n_missing=1),), params, fs_hz=FS_HZ).labels
+
+    assert [i for i, label in enumerate(labels) if label is not None] == [13, 14, 15, 16]
+    assert all(labels[i] is Label.LOST for i in (13, 14, 15, 16))
+
+
+def test_a_lost_margin_is_lost():
+    """Section 2.1: a margin takes the kind of what it grows."""
+    gaze, vel, loss, gaps = _clean(30)
+    loss.feature_lost[10] = True
+    params = ValidityParams(**{**asdict(_CARRY), "dilate_samples": 2})
+
+    labels = validity_labels(gaze, vel, loss, gaps, params, fs_hz=FS_HZ).labels
+
+    assert [i for i, label in enumerate(labels) if label is Label.LOST] == [8, 9, 10, 11, 12]
+    assert sum(label is not None for label in labels) == 5
+
+
+@pytest.mark.parametrize("where", [
+    pytest.param({"blink": 11}, id="beside-a-blink"),
+    pytest.param({"lost": 0}, id="at-the-recordings-start"),
+    pytest.param({"lost": 19}, id="at-its-end"),
+    pytest.param({"lost": 2}, id="beside-the-start's-zero-filled-velocities"),
+    pytest.param({"lost": 17}, id="beside-the-end's-zero-filled-velocities"),
+])
+def test_a_lost_stretch_not_between_two_measured_samples_is_withheld(where):
+    """A run is carried across a loss from one measured sample to the next;
+    a loss with a withheld sample or the recording's edge on a side carries
+    nothing, and is withheld (section 3). The two samples at each end have
+    zero-filled velocities, not measured ones, so a loss beside them is
+    withheld too (the rule both sessions agreed on 2026-11-07)."""
+    gaze, vel, loss, gaps = _clean(20)
+    lost = where.get("lost", 10)
+    loss.feature_lost[lost] = True
+    if "blink" in where:
+        loss.blink[where["blink"]] = True
+
+    labels = validity_labels(gaze, vel, loss, gaps, _CARRY, fs_hz=FS_HZ).labels
+
+    assert labels[lost] is Label.INVALID
+
+
+@pytest.mark.parametrize("lost", [3, 16])
+def test_a_lost_stretch_between_two_measured_samples_inside_the_ends_is_carried(lost):
+    gaze, vel, loss, gaps = _clean(20)
+    loss.feature_lost[lost] = True
+
+    assert validity_labels(gaze, vel, loss, gaps, _CARRY, fs_hz=FS_HZ).labels[lost] is Label.LOST
+
+
+@pytest.mark.parametrize(("n_lost", "kind"), [(24, Label.LOST), (25, Label.INVALID)])
+def test_a_lost_stretch_is_carried_while_its_measured_neighbours_are_50_ms_apart(n_lost, kind):
+    """Section 3: inclusive, as wl-xcon's `max_gap_s` is, and counted in whole
+    frames, 25 at 500 Hz: 24 lost samples leave their neighbours 50 ms apart,
+    25 leave them 52 ms apart."""
+    gaze, vel, loss, gaps = _clean(60)
+    loss.feature_lost[10:10 + n_lost] = True
+
+    labels = validity_labels(gaze, vel, loss, gaps, _CARRY, fs_hz=FS_HZ).labels
+
+    assert {labels[i] for i in range(10, 10 + n_lost)} == {kind}
+    assert labels[9] is None and labels[10 + n_lost] is None
+
+
+@pytest.mark.parametrize(("n_missing", "kind"), [(24, Label.LOST), (25, Label.INVALID)])
+def test_the_bound_counts_the_frames_the_camera_dropped(n_missing, kind):
+    """The bound is the loss of data, from the last position measured to the
+    next (wl-xcon's `max_gap_s`): a gap after row 14 makes rows 13-16 lost,
+    their velocities spanning it, but their positions are measured, so the
+    loss is the `1 + n_missing` frames from row 14 to row 15."""
+    from wl_preproc.eye.ohdpi import FrameGap
+
+    gaze, vel, loss, _ = _clean(40)
+
+    labels = validity_labels(gaze, vel, loss, (FrameGap(row=14, n_missing=n_missing),), _CARRY,
+                             fs_hz=FS_HZ).labels
+
+    assert {labels[i] for i in (13, 14, 15, 16)} == {kind}
+
+
+def test_the_default_carries_lost_data_for_50_ms():
+    assert DEFAULT_VALIDITY_PARAMS.max_lost_ms == 50.0
+
+
+def test_a_paramset_stored_before_lost_data_was_carried_masks_as_before():
+    """A stored paramset without `max_lost_ms` loads with 0, so its content
+    hash still names what it does: every loss withheld as `invalid`, a frame
+    gap with its margin (section 8)."""
+    from wl_preproc.eye.ohdpi import FrameGap
+
+    stored = {name: value for name, value in asdict(DEFAULT_VALIDITY_PARAMS).items() if name != "max_lost_ms"}
+    params = ValidityParams(**{**stored, "dilate_samples": 2, "min_epoch_samples": 1})
+    gaze, vel, loss, _ = _clean(40)
+    loss.feature_lost[30] = True
+
+    labels = validity_labels(gaze, vel, loss, (FrameGap(row=10, n_missing=1),), params, fs_hz=FS_HZ).labels
+
+    assert params.max_lost_ms == 0.0
+    assert [i for i, label in enumerate(labels) if label is not None] == [*range(7, 15), *range(28, 33)]
+    assert all(labels[i] is Label.INVALID for i in [*range(7, 15), *range(28, 33)])
+
+
+def test_lost_positions_are_filled_along_frame_time_and_a_dropped_frames_neighbours_keep_theirs():
+    """Design spec `2026-11-06-eye-lost-data-and-region-design.md` section 3:
+    what a detector is offered over a carried loss. A position that is not a
+    measurement is filled by the straight line between the measured samples
+    either side, placed at its own frame's time; the rows whose velocity
+    spans a dropped frame keep their measured positions."""
+    from wl_preproc.eye.detect.validity import fill_lost, frame_numbers, rows_across_gaps
+    from wl_preproc.eye.ohdpi import FrameGap
+
+    gaze = np.column_stack([np.arange(20, dtype=float), np.zeros(20)])
+    gaze[5:7] = np.nan
+    gaze[13, 0] = 99.0
+    gaps = (FrameGap(row=13, n_missing=2),)
+    offered = np.full(20, None, dtype=object)
+    offered[[5, 6, 12, 13, 14, 15]] = Label.LOST
+
+    filled = fill_lost(gaze, offered, frame_numbers(20, gaps), rows_across_gaps(20, gaps))
+
+    assert filled[5:7, 0].tolist() == [5.0, 6.0]
+    assert filled[13, 0] == 99.0 and filled[12, 0] == 12.0
+    assert np.isfinite(filled).all() and filled[[0, 4, 7, 19], 0].tolist() == [0.0, 4.0, 7.0, 19.0]
diff --git a/tests/schema/test_detect_populate.py b/tests/schema/test_detect_populate.py
index f9b8a98..473454a 100644
--- a/tests/schema/test_detect_populate.py
+++ b/tests/schema/test_detect_populate.py
@@ -1359,14 +1359,19 @@ def test_a_refused_mask_leaves_every_fraction_null(uncalibrated_session):
             assert row[column] is None, f"{eye_value}: refused row has {column} populated"
 
 
-def test_a_missing_gaze_value_is_withheld_and_counted_end_to_end(missing_gaze_session):
+def test_a_missing_gaze_value_is_lost_and_counted_end_to_end(missing_gaze_session):
     """The sixth validity criterion through a real database (added
     2026-09-27). One `NaN` in the left eye's recording: that sample and the
     four velocity estimates it spoils are `non_finite`, the left row's
-    `frac_non_finite` counts exactly those five, the stored mask withholds the
-    sample, and the right eye -- untouched -- counts nothing. Before the
+    `frac_non_finite` counts exactly those five, the stored mask marks the
+    sample `lost`, and the right eye -- untouched -- counts nothing. Before the
     criterion, `abs(NaN) > half-width` was false and the sample was offered to
-    every detector as usable."""
+    every detector as usable.
+
+    *Until 2026-11-06 the mask withheld it as `invalid`; since then nothing
+    measured is lost, carried by a run where the loss is short (design spec
+    `2026-11-06-eye-lost-data-and-region-design.md` section 2); true when
+    written.*"""
     from wl_preproc.schema import detect
 
     session_key, _report, missing_row = missing_gaze_session
@@ -1381,19 +1386,20 @@ def test_a_missing_gaze_value_is_withheld_and_counted_end_to_end(missing_gaze_se
         detect.EyeValidity.Run & {**session_key, "eye": "left"}
         & f"run_start <= {missing_row}" & f"run_stop > {missing_row}"
     ).to_dicts()
-    assert covering["label"] == "invalid"
+    assert covering["label"] == "lost"
 
 
 def test_no_detector_labels_a_missing_gaze_value(missing_gaze_session):
     """The point of withholding it: every registered detector's stored run
-    over the missing sample, on the left trace, is the mask's own `invalid`
+    over the missing sample, on the left trace, is the mask's own `lost`
     -- no detector was handed the sample to label. The conjunction falls
     back to the right eye there: it carries the right eye's own label, and
     `EyeDetection.Source` says `right` (design spec
     `2026-09-28-both-eyes-fallback-design.md` section 1).
 
     *Until 2026-09-28 the conjunction was labelled from the left eye's mask
-    alone, and read `invalid` here too; true when written.*"""
+    alone, and read `invalid` here too; until 2026-11-06 the mask's own run
+    was `invalid`; true when written.*"""
     from wl_preproc.schema import detect
 
     def covering(table, trace, name, start, stop):
@@ -1404,7 +1410,7 @@ def test_no_detector_labels_a_missing_gaze_value(missing_gaze_session):
     session_key, _report, missing_row = missing_gaze_session
     for name in _detector_names():
         left = covering(detect.EyeDetection.Run, "left", name, "run_start", "run_stop")
-        assert left["label"] == "invalid", (name, left["label"])
+        assert left["label"] == "lost", (name, left["label"])
         right = covering(detect.EyeDetection.Run, "right", name, "run_start", "run_stop")
         both = covering(detect.EyeDetection.Run, "conjunction", name, "run_start", "run_stop")
         assert both["label"] == right["label"], (name, both["label"], right["label"])
diff --git a/tests/schema/test_eye_loss_populate.py b/tests/schema/test_eye_loss_populate.py
index 21dca2f..304fc4e 100644
--- a/tests/schema/test_eye_loss_populate.py
+++ b/tests/schema/test_eye_loss_populate.py
@@ -116,11 +116,14 @@ def test_p1_off_camera_is_stored_as_invalid_in_its_eye_only(lost_session):
     assert set(_stored_mask(session_key, "right")[start:stop]) == {"fixation"}
 
 
-def test_an_unreadable_pupil_is_stored_as_invalid_not_a_blink(lost_session):
+def test_an_unreadable_pupil_is_stored_as_lost_not_a_blink(lost_session):
+    """Nothing was measured, and nothing declared: lost, carried where short
+    (design spec `2026-11-06-eye-lost-data-and-region-design.md` section 2).
+    *Until 2026-11-06 it was stored as `invalid`; true when written.*"""
     session_key, _segment, _report = lost_session
     start = _first_row_at(_UNREADABLE_S)
 
-    assert set(_stored_mask(session_key, "right")[start:start + _UNREADABLE_ROWS]) == {"invalid"}
+    assert set(_stored_mask(session_key, "right")[start:start + _UNREADABLE_ROWS]) == {"lost"}
 
 
 def test_the_mask_counts_the_lost_features_and_the_blinks(lost_session):
PATCH
```

- [ ] **Step 2: Run them and see them fail.** Every test of `test_validity.py` written before this task passes already: they build their paramset without `max_lost_ms`, which is how a paramset stored before reads, and pin that such a one masks as it did.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/eye/detect/test_labels.py tests/eye/detect/test_validity.py tests/schema/test_detect_populate.py::test_a_missing_gaze_value_is_lost_and_counted_end_to_end tests/schema/test_detect_populate.py::test_no_detector_labels_a_missing_gaze_value tests/schema/test_eye_loss_populate.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `1 error`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/eye/detect/labels.py b/wl_preproc/eye/detect/labels.py
index 9a45fda..7ed0205 100644
--- a/wl_preproc/eye/detect/labels.py
+++ b/wl_preproc/eye/detect/labels.py
@@ -4,6 +4,11 @@
 an enum value later is a schema change, and the migration window closes
 January 2027 -- so the vocabulary is declared complete now and filled in as
 detectors that can emit `pso`, `pursuit` and `drift` arrive.
+
+**A ninth, `lost`, joined on 2026-11-06, inside that window:** a sample the
+validity mask did not offer as measured because nothing was measured there,
+or nothing to trust, which a run is carried across where the loss is short
+(design spec `2026-11-06-eye-lost-data-and-region-design.md` section 2).
 """
 
 from __future__ import annotations
@@ -17,6 +22,7 @@ import numpy as np
 class Label(StrEnum):
     BLINK = "blink"
     INVALID = "invalid"
+    LOST = "lost"
     SACCADE = "saccade"
     MICROSACCADE = "microsaccade"
     PSO = "pso"
@@ -95,7 +101,7 @@ KIND_OF: dict[Label, str] = {
 #: nested loop over the largest runs in the trace for no observable difference
 #: (spec section 1.2). `blink` and `invalid` come from the validity mask,
 #: never from a detector, and are in no detector's vocabulary at all.
-NOT_INTERSECTED = frozenset({Label.FIXATION, Label.BLINK, Label.INVALID})
+NOT_INTERSECTED = frozenset({Label.FIXATION, Label.BLINK, Label.INVALID, Label.LOST})
 
 
 def kind_of(label) -> str | None:
@@ -216,6 +222,16 @@ def labels_from_runs(runs: list[Run], n_samples: int) -> np.ndarray:
     return out
 
 
+def may_label(available) -> np.ndarray:
+    """Where a detector may label: each sample the validity mask offered,
+    measured (`None`) or lost and carried (`Label.LOST`; design spec
+    `2026-11-06-eye-lost-data-and-region-design.md` section 3). Every
+    detector but Engbert-Kliegl, which tells the two apart (section 4), runs
+    on these as its own usable samples, unchanged; `registry.Detector.detect`
+    then holds its runs to the shared rule (section 5)."""
+    return np.array([entry is None or entry is Label.LOST for entry in available], dtype=bool)
+
+
 def true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
     """Maximal `True` stretches of a boolean array, as half-open `(start,
     stop)` pairs.
diff --git a/wl_preproc/eye/detect/validity.py b/wl_preproc/eye/detect/validity.py
index 4ee33cf..30c9ed1 100644
--- a/wl_preproc/eye/detect/validity.py
+++ b/wl_preproc/eye/detect/validity.py
@@ -28,8 +28,21 @@ hold constant.
 Returns `None` for a sample a detector may label, and a `Label` for one it may
 not. Precedence is enforced HERE, by not offering the sample at all, rather
 than by asking every detector to respect an ordering -- and the one ordering
-that is genuinely a ranking, `blink` over `invalid`, is `MASK_PRECEDENCE`
-below.
+that is genuinely a ranking, `blink` over `invalid` over `lost`, is
+`MASK_PRECEDENCE` below.
+
+**Lost or invalid** (design spec `2026-11-06-eye-lost-data-and-region-design.md`
+section 2, the requester's decisions of 2026-11-06). Where a paramset carries
+lost data (`max_lost_ms` above 0), a sample not offered as measured is one of
+two kinds. LOST: nothing was measured, or nothing to trust -- a lost
+reflection, a value that is not a number, an impossible speed, a repaired
+glitch, and the four velocities a dropped frame spans. INVALID: a
+measurement, of the wrong thing -- a blink with its padding, and gaze off the
+screen. A run is carried across a lost stretch that lies between two measured
+samples at most `max_lost_ms` apart; any other lost stretch is withheld as
+`invalid`, and a detector meets exactly what is stored. A paramset stored
+before, naming no bound, masks as it always did: every criterion withheld as
+`invalid`, with its margin.
 """
 
 from __future__ import annotations
@@ -39,7 +52,7 @@ from dataclasses import dataclass
 
 import numpy as np
 
-from wl_preproc.eye.detect.labels import Label
+from wl_preproc.eye.detect.labels import Label, true_runs
 from wl_preproc.eye.detect.velocity import _HALF_WINDOW
 from wl_preproc.eye.ohdpi import FrameGap, TrackerLoss
 
@@ -61,7 +74,11 @@ from wl_preproc.eye.ohdpi import FrameGap, TrackerLoss
 # conjunction, where it ranked a pair section 1 says is a split -- see
 # `schema/detect.py::_conjunction_label`. Trimmed to the pair that is
 # genuinely ranked, and moved to where the ranking is genuinely applied.
-MASK_PRECEDENCE: tuple[Label, ...] = (Label.BLINK, Label.INVALID)
+#
+# `lost` joined it on 2026-11-06, last: a lost margin that meets a withheld
+# sample is withheld (design spec `2026-11-06-eye-lost-data-and-region-design.md`
+# section 2.1).
+MASK_PRECEDENCE: tuple[Label, ...] = (Label.BLINK, Label.INVALID, Label.LOST)
 
 
 @dataclass(frozen=True, slots=True)
@@ -83,6 +100,12 @@ class ValidityParams:
     # what it does; `DEFAULT_VALIDITY_PARAMS` sets the measured values.
     blink_pad_before_ms: float = 0.0
     blink_pad_after_ms: float = 0.0
+    # How far apart, at most, the measured samples either side of a lost
+    # stretch may be for a run to be carried across it (design spec
+    # `2026-11-06-eye-lost-data-and-region-design.md` section 3; wl-xcon's
+    # `max_gap_s`). 0 by default, as the pads are: a paramset stored before
+    # it carries nothing and masks as it did.
+    max_lost_ms: float = 0.0
 
 
 # **These are placeholders with no measured basis on this rig** (design spec
@@ -105,6 +128,9 @@ DEFAULT_VALIDITY_PARAMS = ValidityParams(
     # (design spec `2026-10-11-blink-padding-design.md` section 1).
     blink_pad_before_ms=50.0,
     blink_pad_after_ms=80.0,
+    # wl-xcon's online detector's bound, which the requester's decision of
+    # 2026-11-06 makes both detectors' (its `max_gap_s`, 0.05 s).
+    max_lost_ms=50.0,
 )
 
 
@@ -167,13 +193,15 @@ def validity_labels(
     params: ValidityParams,
     *,
     fs_hz: float,
+    repaired: np.ndarray | None = None,
 ) -> ValidityMask:
     """One entry per sample: a `Label` where the sample is unusable, `None`
     where a detector may label it -- and beside it each criterion's own
     rejected fraction. See `ValidityMask` for what the seven fractions are,
     and for what they deliberately do not sum to. `fs_hz` is the
-    recording's rate, which turns the blink pads' milliseconds into
-    samples."""
+    recording's rate, which turns the blink pads' and the bound's
+    milliseconds into samples. `repaired` marks the samples
+    `glitch.repair_glitches` filled in, lost where lost data is carried."""
     n = gaze_deg.shape[0]
     # Eye open, read by the rule: a blink (the pupil not found), and a lost
     # reflection (P1 or P4 at 0, or `DataQuality` below 100), where `CR1 -
@@ -238,9 +266,7 @@ def validity_labels(
     # full. The reference recording still has zero gaps across 1,177,799
     # rows, so real (non-synthetic) data has still not exercised this loop --
     # only a planted gap has, so far.
-    across_gap = np.zeros(n, dtype=bool)
-    for gap in frame_gaps:
-        across_gap[max(gap.row + 1 - _HALF_WINDOW, 0) : min(gap.row + _HALF_WINDOW + 1, n)] = True
+    across_gap = rows_across_gaps(n, frame_gaps)
 
     # **The guard, and the one criterion not from OpenIrisDPI's notebook.**
     # Every comparison above is false for a NaN: `abs(NaN) > half-width`,
@@ -260,17 +286,36 @@ def validity_labels(
         | loss.unreadable
     )
 
-    unusable = blink | feature_lost | outside | too_fast | across_gap | non_finite
-    unusable = _dilate(unusable, params.dilate_samples)
     # Each blink's flanks, measured from its edges, after the margin so that
     # the margin sits inside a pad rather than adding to it, and before
     # `short_epoch`, so a fragment stranded between two pads is dropped as
     # any short epoch is (design spec `2026-10-11-blink-padding-design.md`).
-    unusable = unusable | _blink_pads(
+    pads = _blink_pads(
         blink,
         round(params.blink_pad_before_ms * fs_hz / 1000.0),
         round(params.blink_pad_after_ms * fs_hz / 1000.0),
     )
+    if params.max_lost_ms > 0:
+        # Each kind grown by its own margin (design spec
+        # `2026-11-06-eye-lost-data-and-region-design.md` section 2): a
+        # dropped frame by none, since it is not a tracking failure and its
+        # neighbours' positions are measurements; a lost margin that meets a
+        # withheld sample is withheld; and a lost stretch no run can be
+        # carried across is withheld too.
+        unusable = _dilate(blink | outside, params.dilate_samples) | pads
+        lost_raw = feature_lost | too_fast | non_finite
+        if repaired is not None:
+            lost_raw = lost_raw | repaired
+        untrusted = _dilate(lost_raw, params.dilate_samples)
+        lost = (untrusted | across_gap) & ~unusable
+        uncarried = _uncarried(lost, unusable, untrusted, frame_numbers(n, frame_gaps),
+                               carried_frames(params.max_lost_ms, fs_hz))
+        unusable = unusable | uncarried
+        lost = lost & ~uncarried
+    else:
+        unusable = _dilate(blink | feature_lost | outside | too_fast | across_gap | non_finite,
+                           params.dilate_samples) | pads
+        lost = np.zeros(n, dtype=bool)
     # Bound to a name rather than folded straight into `unusable`, because
     # `_short_valid_epochs`' own docstring says it is returned as its own
     # mask "so the caller can see that these samples were dropped for being
@@ -279,6 +324,7 @@ def validity_labels(
     # distinction was computed on every session and immediately discarded.
     short_epoch = _short_valid_epochs(unusable, params.min_epoch_samples)
     unusable = unusable | short_epoch
+    lost = lost & ~short_epoch
 
     # Walked in REVERSE precedence order, so the most specific label is
     # assigned last and wins. `MASK_PRECEDENCE` is what decides that, not the
@@ -288,7 +334,7 @@ def validity_labels(
     # test_mask_precedence_is_what_decides_which_criterion_wins` reverses it
     # and checks exactly that). A label added to the tuple with no criterion
     # beside it raises `KeyError` here rather than being silently skipped.
-    claimed = {Label.BLINK: blink, Label.INVALID: unusable}
+    claimed = {Label.BLINK: blink, Label.INVALID: unusable, Label.LOST: lost}
     out = np.full(n, None, dtype=object)
     for label in reversed(MASK_PRECEDENCE):
         out[claimed[label]] = label
@@ -316,6 +362,74 @@ def validity_labels(
     )
 
 
+def rows_across_gaps(n: int, frame_gaps: Sequence[FrameGap]) -> np.ndarray:
+    """The rows whose velocity spans a dropped frame: `[row-1, row+2]` for a
+    gap after `row`, coupled to `_HALF_WINDOW` (`validity_labels`)."""
+    out = np.zeros(n, dtype=bool)
+    for gap in frame_gaps:
+        out[max(gap.row + 1 - _HALF_WINDOW, 0) : min(gap.row + _HALF_WINDOW + 1, n)] = True
+    return out
+
+
+
+def fill_lost(gaze_deg: np.ndarray, offered: np.ndarray, frames: np.ndarray, keep: np.ndarray) -> np.ndarray:
+    """The gaze a detector is offered over carried losses (design spec
+    `2026-11-06-eye-lost-data-and-region-design.md` section 3): each lost
+    sample's position (`Label.LOST` in `offered`) filled by the straight line
+    between the samples either side, placed at its own frame's time, except
+    where `keep` says its position is a measurement -- a row whose velocity
+    spans a dropped frame (`rows_across_gaps`). The mask carries only a loss with a
+    measured sample on both sides, so every filled stretch has two."""
+    out = gaze_deg.copy()
+    filled = np.array([entry is Label.LOST for entry in offered], dtype=bool) & ~keep
+    for start, stop in true_runs(filled):
+        before, after = start - 1, stop
+        share = (frames[start:stop] - frames[before]) / (frames[after] - frames[before])
+        out[start:stop] = gaze_deg[before] + share[:, None] * (gaze_deg[after] - gaze_deg[before])
+    return out
+
+
+def frame_numbers(n: int, frame_gaps: Sequence[FrameGap]) -> np.ndarray:
+    """Each row's frame, counted from the first row's: its index, plus every
+    frame the camera dropped before it."""
+    frames = np.arange(n, dtype=np.int64)
+    for gap in frame_gaps:
+        frames[gap.row + 1:] += gap.n_missing
+    return frames
+
+
+def carried_frames(max_lost_ms: float, fs_hz: float) -> int:
+    """How many frames apart, at most, the measured samples either side of a
+    lost stretch may be for a run to be carried across it: `max_lost_ms` in
+    whole frames, inclusive, so no float rounding decides a boundary (design
+    spec `2026-11-06-eye-lost-data-and-region-design.md` section 3)."""
+    return int(np.floor(max_lost_ms * fs_hz / 1000.0 + 1e-9))
+
+
+def _uncarried(lost: np.ndarray, withheld: np.ndarray, untrusted: np.ndarray, frames: np.ndarray,
+               max_frames: int) -> np.ndarray:
+    """The lost stretches no run can be carried across: each without a
+    measured sample on both sides, or across which two positions measured in
+    turn are more than `max_frames` apart. The bound is the loss of data, from
+    the last position measured to the next, as wl-xcon's `max_gap_s` is: a
+    dropped frame's neighbours have positions, though their velocities span
+    it (`untrusted` marks the positions that are not measurements). The two
+    samples at each end of the recording are no measured sample beside a
+    stretch: their velocities are zero-filled (`velocity.py`), so they neither
+    end a carried run nor bound a stretch (the rule both sessions agreed on
+    2026-11-07)."""
+    out = np.zeros_like(lost)
+    for start, stop in true_runs(lost):
+        before, after = start - 1, stop
+        if before < _HALF_WINDOW or after >= lost.size - _HALF_WINDOW or withheld[before] or withheld[after]:
+            out[start:stop] = True
+            continue
+        measured = before + np.flatnonzero(~untrusted[before:after + 1])
+        if np.diff(frames[measured]).max() > max_frames:
+            out[start:stop] = True
+    return out
+
+
 def _fraction(mask: np.ndarray) -> float:
     """The share of all samples one criterion rejected; `0.0` for a
     zero-length recording.
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/eye/detect/test_labels.py tests/eye/detect/test_validity.py tests/schema/test_detect_populate.py::test_a_missing_gaze_value_is_lost_and_counted_end_to_end tests/schema/test_detect_populate.py::test_no_detector_labels_a_missing_gaze_value tests/schema/test_eye_loss_populate.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `86 passed`

- [ ] **Step 5: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/eye/detect/test_glitch.py tests/eye/detect/test_labels.py tests/eye/detect/test_validity.py tests/schema/test_eye_loss_populate.py tests/schema/test_eye_populate.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `135 passed`

- [ ] **Step 6: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(eye): the validity mask tells lost data from invalid -- a lost reflection, a value that is not a number, an impossible speed, a repaired glitch and the four velocities a dropped frame spans are lost, a blink and gaze off the screen invalid; a dropped frame takes no margin; a lost stretch is carried while the positions either side are at most 50 ms apart, in whole frames, and withheld otherwise; a paramset stored before masks as it did (design spec 2026-11-06-eye-lost-data-and-region-design.md, sections 2 and 3)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

- [ ] **Step 7: The mutation checks,** on the commit. Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "frame-gap-takes-a-margin",
  "wl_preproc/eye/detect/validity.py",
  "        lost = (untrusted | across_gap) & ~unusable",
  "        lost = _dilate(untrusted | across_gap, params.dilate_samples) & ~unusable",
  "tests/eye/detect/test_validity.py"
 ],
 [
  "a-lost-reflection-is-invalid",
  "wl_preproc/eye/detect/validity.py",
  "        unusable = _dilate(blink | outside, params.dilate_samples) | pads",
  "        unusable = _dilate(blink | outside | feature_lost, params.dilate_samples) | pads",
  "tests/eye/detect/test_validity.py"
 ],
 [
  "a-repaired-glitch-is-measured",
  "wl_preproc/eye/detect/validity.py",
  "        if repaired is not None:",
  "        if False:",
  "tests/eye/detect/test_validity.py"
 ],
 [
  "the-bound-is-exclusive",
  "wl_preproc/eye/detect/validity.py",
  "        if np.diff(frames[measured]).max() > max_frames:",
  "        if np.diff(frames[measured]).max() >= max_frames:",
  "tests/eye/detect/test_validity.py"
 ],
 [
  "the-bound-counts-rows",
  "wl_preproc/eye/detect/validity.py",
  "        uncarried = _uncarried(lost, unusable, untrusted, frame_numbers(n, frame_gaps),",
  "        uncarried = _uncarried(lost, unusable, untrusted, np.arange(n),",
  "tests/eye/detect/test_validity.py"
 ],
 [
  "the-bound-is-the-velocities",
  "wl_preproc/eye/detect/validity.py",
  "        measured = before + np.flatnonzero(~untrusted[before:after + 1])",
  "        measured = np.array([before, after])",
  "tests/eye/detect/test_validity.py"
 ],
 [
  "an-old-paramset-carries",
  "wl_preproc/eye/detect/validity.py",
  "    if params.max_lost_ms > 0:",
  "    if True:",
  "tests/eye/detect/test_validity.py"
 ],
 [
  "a-gap-row-is-filled",
  "wl_preproc/eye/detect/validity.py",
  "    filled = np.array([entry is Label.LOST for entry in offered], dtype=bool) & ~keep",
  "    filled = np.array([entry is Label.LOST for entry in offered], dtype=bool)",
  "tests/eye/detect/test_validity.py"
 ],
 [
  "a-stretch-at-an-edge-is-carried",
  "wl_preproc/eye/detect/validity.py",
  "        if before < _HALF_WINDOW or after >= lost.size - _HALF_WINDOW or withheld[before] or withheld[after]:",
  "        if before >= 0 and after < lost.size and (withheld[before] or withheld[after]):",
  "tests/eye/detect/test_validity.py"
 ],
 [
  "the-zero-filled-ends-are-measured",
  "wl_preproc/eye/detect/validity.py",
  "before < _HALF_WINDOW or after >= lost.size - _HALF_WINDOW",
  "before < 0 or after >= lost.size",
  "tests/eye/detect/test_validity.py"
 ]
]
```

Expected: 10 lines, each `CAUGHT`; `git status --short` shows no change.

---

### Task 2: Every detector carries a run across a short loss

§4 and §5, amendment 6 and the correction both sessions agreed on 2026-11-06: Engbert-Kliegl, the contract's detector, takes a lost sample's velocity as unknown, carrying an open run and never opening one or counting toward its minimum, and carries a run to the landing where a short loss is at its end, as wl-xcon's online copy does; the other six treat a lost sample as usable (`labels.may_label`) and run unchanged; `registry._within_usable` holds every detector's runs to the one shared rule, a run holding lost samples kept only where its measured samples reach its detector's own minimum, which moves to `registry.min_duration_samples`. One dropped frame anywhere inside an 8-degree saccade leaves every detector's saccade as it was.

**Files:**
- Modify: `wl_preproc/eye/detect/engbert_kliegl.py`, `eye/detect/registry.py` (`min_duration_samples`, `_within_usable`)
- Modify: `wl_preproc/eye/detect/bmd.py`, `nslr.py`, `nystrom_holmqvist.py`, `otero_millan.py`, `remodnav.py`, `uneye.py` (each one line: `usable = may_label(available)`)
- Create: `tests/eye/detect/test_lost_data.py`
- Test: `tests/eye/detect/test_engbert_kliegl.py`, `tests/eye/detect/test_registry.py`

**Interfaces:**
- Consumes: Task 1's `Label.LOST`, `may_label`.
- Produces: `registry.min_duration_samples(detector_params, fs_hz) -> int` (the body of
  `schema/detect.py::_min_duration_samples`, which Task 3 makes delegate to it);
  `registry._within_usable(intervals, available, floor)`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/eye/detect/test_engbert_kliegl.py b/tests/eye/detect/test_engbert_kliegl.py
index d64bde5..a6fb50e 100644
--- a/tests/eye/detect/test_engbert_kliegl.py
+++ b/tests/eye/detect/test_engbert_kliegl.py
@@ -403,3 +403,67 @@ def test_under_true_angle_the_step_across_the_cut_is_a_microsaccade():
     per_axis = detect_engbert_kliegl(gaze, velocity(gaze, 500.0), available, 500.0, DEFAULT_EK_PARAMS)
     assert [r.label for r in true] == [Label.MICROSACCADE]
     assert [r.label for r in per_axis] == [Label.SACCADE]
+
+
+# Design spec `2026-11-06-eye-lost-data-and-region-design.md` section 4: a
+# lost sample's velocity is unknown. It continues an open run and never opens
+# one or counts toward the minimum, as wl-xcon's online copy does.
+
+
+def _lost(n, *spans):
+    available = np.full(n, None, dtype=object)
+    for start, stop in spans:
+        available[start:stop] = Label.LOST
+    return available
+
+
+def _detected(gaze, available, params=DEFAULT_EK_PARAMS):
+    from wl_preproc.eye.detect.velocity import velocity
+
+    return [(run.start, run.stop)
+            for run in detect_engbert_kliegl(gaze, velocity(gaze, FS_HZ), available, FS_HZ, params)]
+
+
+def test_a_lost_stretch_inside_a_run_carries_it():
+    gaze, _ = _trace_with_saccades([500], n=1000)
+    ((start, stop),) = _detected(gaze, _lost(1000))
+
+    assert _detected(gaze, _lost(1000, (start + 2, start + 6))) == [(start, stop)]
+
+
+def test_lost_samples_never_open_a_run():
+    gaze, _ = _trace_with_saccades([500], n=1000)
+    ((start, stop),) = _detected(gaze, _lost(1000))
+
+    assert _detected(gaze, _lost(1000, (start - 3, start + 1))) == [(start + 1, stop)]
+
+
+def test_a_short_loss_at_a_runs_end_is_carried_to_the_landing():
+    """The eye landed in the loss: the run goes on through it and ends at the
+    first measured sample after it, that sample its last (the correction both
+    sessions agreed on 2026-11-06). Withheld, as a long loss is, the loss ends
+    the run before it."""
+    gaze, _ = _trace_with_saccades([500], n=1000)
+    ((start, stop),) = _detected(gaze, _lost(1000))
+    withheld = _lost(1000)
+    withheld[stop - 1:stop + 3] = Label.INVALID
+
+    assert _detected(gaze, _lost(1000, (stop - 1, stop + 3))) == [(start, stop + 4)]
+    assert _detected(gaze, withheld) == [(start, stop - 1)]
+
+
+def test_only_measured_velocities_count_toward_the_minimum():
+    """wl-xcon's rule since its version 3: unknowns carry a run but never make
+    one."""
+    gaze, _ = _trace_with_saccades([500], n=1000)
+    ((start, stop),) = _detected(gaze, _lost(1000))
+    params = dataclasses.replace(DEFAULT_EK_PARAMS, min_duration_samples=stop - start)
+
+    assert _detected(gaze, _lost(1000), params) == [(start, stop)]
+    assert _detected(gaze, _lost(1000, (start + 3, start + 4)), params) == []
+
+
+def test_lost_samples_alone_make_no_run():
+    gaze, _ = _trace_with_saccades([500], n=1000)
+
+    assert _detected(gaze, _lost(1000, (100, 120))) == _detected(gaze, _lost(1000)) != []
diff --git a/tests/eye/detect/test_lost_data.py b/tests/eye/detect/test_lost_data.py
new file mode 100644
index 0000000..5eab3cf
--- /dev/null
+++ b/tests/eye/detect/test_lost_data.py
@@ -0,0 +1,43 @@
+"""A short loss of data inside a saccade leaves it whole, for every
+registered detector (design spec `2026-11-06-eye-lost-data-and-region-design.md`
+section 11): the requester's question of 2026-11-06, asked jointly with
+wl-xcon, began with a dropped frame splitting an 8-degree saccade in two,
+or erasing it."""
+
+import json
+from pathlib import Path
+
+import numpy as np
+import pytest
+
+from wl_preproc.eye.detect.labels import Label
+from wl_preproc.eye.detect.registry import DETECTORS
+from wl_preproc.eye.detect.velocity import velocity
+
+_V1 = json.loads((Path(__file__).parents[3] / "docs/fixtures/saccade/v1.json").read_text())
+
+
+@pytest.mark.parametrize("name", sorted(DETECTORS))
+def test_a_dropped_frame_inside_a_saccade_leaves_it_whole(name):
+    """v1's `centre-horizontal` 8-degree step. A frame dropped after row `row`
+    makes rows `row - 1` to `row + 2` lost, their velocities spanning the gap
+    and their positions measured (section 2); wherever it falls inside the
+    saccade, each detector's saccades are as they were."""
+    detector = DETECTORS[name]
+    if detector.unavailable is not None and detector.unavailable() is not None:
+        pytest.skip(detector.unavailable())
+    (case,) = [case for case in _V1["traces"] if case["name"] == "centre-horizontal"]
+    gaze = np.array(case["gaze_deg"], dtype=float)
+    fs_hz = _V1["fs_hz"]
+    v = velocity(gaze, fs_hz)
+
+    def saccades(available):
+        return [(run.start, run.stop) for run in detector.detect(gaze, v, available, fs_hz, detector.defaults)
+                if run.label in (Label.SACCADE, Label.MICROSACCADE)]
+
+    whole = saccades(np.full(len(gaze), None, dtype=object))
+    (start, stop), = [(start, stop) for start, stop in whole if start <= 206 < stop]
+    for row in range(start + 2, stop - 3):
+        available = np.full(len(gaze), None, dtype=object)
+        available[row - 1:row + 3] = Label.LOST
+        assert saccades(available) == whole, row
diff --git a/tests/eye/detect/test_registry.py b/tests/eye/detect/test_registry.py
index 7d9fa37..00ac701 100644
--- a/tests/eye/detect/test_registry.py
+++ b/tests/eye/detect/test_registry.py
@@ -452,3 +452,44 @@ def test_a_detectors_own_params_mean_per_axis():
     declared = {name: detector.defaults.geometry for name, detector in DETECTORS.items()
                 if hasattr(detector.defaults, "geometry")}
     assert declared == dict.fromkeys(("engbert_kliegl", "otero_millan", "nystrom_holmqvist", "bmd"), PER_AXIS)
+
+
+@dataclass(frozen=True)
+class _Floor:
+    """A detector's declared minimum, as `registry.min_duration_samples` reads
+    it."""
+
+    min_duration_samples: int = 6
+
+
+def test_a_run_may_span_lost_samples_and_its_measured_samples_make_it():
+    """Design spec `2026-11-06-eye-lost-data-and-region-design.md` section 5,
+    the one rule every detector's runs are held to:
+    - a run is carried across lost samples, at its ends as inside it, and
+      keeps its label and reliability (the correction of 2026-11-06:
+      `measure.measure_event_run` measures it out to the measured samples
+      beyond its lost ends);
+    - a run holding lost samples is kept only if its measured samples alone
+      reach its detector's minimum; one holding none is untouched."""
+    import numpy as np
+
+    from wl_preproc.eye.detect.labels import Run
+    from wl_preproc.eye.detect.registry import Detector
+
+    n = 60
+    available = np.full(n, None, dtype=object)
+    available[[4, 5, 18, 19, 20, 31, 32, 33, 34]] = Label.LOST
+    runs = [
+        Run(0, 10, Label.SACCADE),                     # 4-5 lost inside: kept
+        Run(10, 20, Label.SACCADE),                    # 18-19 lost at its end: kept
+        Run(20, 30, Label.SACCADE, reliability=0.6),   # 20 lost at its start: kept
+        Run(30, 36, Label.SACCADE),                    # 2 measured of 6: dropped
+        Run(40, 43, Label.SACCADE),                    # none lost: untouched
+    ]
+    detector = Detector(name="fake", vocabulary=frozenset({Label.SACCADE}), run=lambda *_: list(runs),
+                        defaults=None)
+
+    got = detector.detect(np.zeros((n, 2)), np.zeros((n, 2)), available, 500.0, _Floor())
+
+    assert [(r.start, r.stop, r.reliability) for r in got] == [(0, 10, None), (10, 20, None), (20, 30, 0.6),
+                                                               (40, 43, None)]
PATCH
```

- [ ] **Step 2: Run them and see them fail.** `test_lost_samples_never_open_a_run` and `test_only_measured_velocities_count_toward_the_minimum` pass already: a lost sample is withheld before this task, and they pin what Step 3 keeps.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_lost_data.py tests/eye/detect/test_registry.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `10 failed, 46 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/eye/detect/bmd.py b/wl_preproc/eye/detect/bmd.py
index abf7e4f..d19a806 100644
--- a/wl_preproc/eye/detect/bmd.py
+++ b/wl_preproc/eye/detect/bmd.py
@@ -39,7 +39,7 @@ from wl_preproc.eye.detect import bmd_rng
 from wl_preproc.eye.detect.bmd_table import LogATable, compute_table, lookup
 from wl_preproc.eye.detect.engbert_kliegl import EngbertKlieglParams, detect_engbert_kliegl
 from wl_preproc.eye.detect.geometry import UNSTATED_GEOMETRY
-from wl_preproc.eye.detect.labels import Label, Run, true_runs
+from wl_preproc.eye.detect.labels import Label, Run, may_label, true_runs
 from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, classify, measure_event_run
 from wl_preproc.eye.detect.numba_threads import keep_off_openmp
 
@@ -853,7 +853,7 @@ def detect_bmd(
 
     Runs are half-open and sorted."""
     gaze = np.asarray(gaze_deg, dtype=float)
-    usable = np.array([entry is None for entry in available], dtype=bool) & np.isfinite(gaze).all(axis=1)
+    usable = may_label(available) & np.isfinite(gaze).all(axis=1)
     # Engbert-Kliegl as registered, in the same geometry (amendment 2).
     gate_params = EngbertKlieglParams(lambda_=params.gate_lambda,
                                       min_duration_samples=params.gate_min_duration_samples,
diff --git a/wl_preproc/eye/detect/engbert_kliegl.py b/wl_preproc/eye/detect/engbert_kliegl.py
index f5ab9f8..2bcb439 100644
--- a/wl_preproc/eye/detect/engbert_kliegl.py
+++ b/wl_preproc/eye/detect/engbert_kliegl.py
@@ -16,7 +16,7 @@ from dataclasses import dataclass
 
 import numpy as np
 
-from wl_preproc.eye.detect.labels import Run, true_runs
+from wl_preproc.eye.detect.labels import Label, Run, true_runs
 from wl_preproc.eye.detect.geometry import TRUE_ANGLE, UNSTATED_GEOMETRY
 from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude, classify
 
@@ -141,8 +141,19 @@ def detect_engbert_kliegl(
     `gaze_deg` is what that split reads -- velocity alone finds the events
     (the elliptic test below), and position is what says how far the eye
     actually went.
+
+    **A lost sample's velocity is unknown** (`Label.LOST`; design spec
+    `2026-11-06-eye-lost-data-and-region-design.md` section 4, wl-xcon's
+    online copy's rule): it continues an open run and never opens one, and
+    only measured velocities count toward `min_duration_samples`. A run that
+    reaches a carried loss goes on through it, and where the first measured
+    velocity after it is not over the threshold the run ends there, that
+    sample its last: the eye landed in the loss, and the run's size reaches
+    the landing (the correction both sessions agreed on 2026-11-06, wl-xcon's
+    version 8). A run's ends are measured samples.
     """
     usable = np.array([entry is None for entry in available], dtype=bool)
+    lost = np.array([entry is Label.LOST for entry in available], dtype=bool)
     if not usable.any():
         return []
 
@@ -157,12 +168,17 @@ def detect_engbert_kliegl(
     ) ** 2 > 1.0
     outside &= usable
 
-    return [
-        Run(
-            start=start,
-            stop=stop,
-            label=classify(amplitude(gaze_deg, start, stop, geometry=params.geometry), params.microsaccade_max_deg),
-        )
-        for start, stop in true_runs(outside)
-        if stop - start >= params.min_duration_samples
-    ]
+    runs = []
+    for start, stop in true_runs(outside | lost):
+        events = start + np.flatnonzero(outside[start:stop])
+        if events.size == 0 or events.size < params.min_duration_samples:
+            continue
+        first, last = int(events[0]), int(events[-1]) + 1
+        if last < stop < usable.size and usable[stop]:
+            last = stop + 1
+        runs.append(Run(
+            start=first,
+            stop=last,
+            label=classify(amplitude(gaze_deg, first, last, geometry=params.geometry), params.microsaccade_max_deg),
+        ))
+    return runs
diff --git a/wl_preproc/eye/detect/nslr.py b/wl_preproc/eye/detect/nslr.py
index 6cbef77..b780f7a 100644
--- a/wl_preproc/eye/detect/nslr.py
+++ b/wl_preproc/eye/detect/nslr.py
@@ -28,7 +28,7 @@ import numpy as np
 import scipy.interpolate
 import scipy.stats
 
-from wl_preproc.eye.detect.labels import Label, Run, true_runs
+from wl_preproc.eye.detect.labels import Label, Run, may_label, true_runs
 
 
 @dataclass(frozen=True, slots=True)
@@ -638,7 +638,7 @@ def detect_nslr(
     half-open, sorted (spec 3, 4).
 
     - **The mask splits pieces.** Every maximal stretch of samples the
-      validity mask offers (`entry is None`) is a piece. No segment spans a
+      validity mask offers (`labels.may_label`) is a piece. No segment spans a
       gap. A one-sample piece is left unlabelled.
     - **So does non-finite gaze.** A sample whose gaze is NaN or infinite is
       withheld here even where the mask offers it. The noise is pooled over
@@ -653,7 +653,7 @@ def detect_nslr(
     - **The conjunction.** The saccadic slice is `{saccade}`, so conjunction
       runs take `_conjunction_label`'s degenerate branch, and there is no
       minimum duration (spec 4)."""
-    usable = np.array([entry is None for entry in available], dtype=bool)
+    usable = may_label(available)
     usable &= np.isfinite(np.asarray(gaze_deg, dtype=float)).all(axis=1)
     spans = [(int(a), int(b)) for a, b in true_runs(usable) if b - a >= 2]
     per_piece = classify([np.asarray(gaze_deg[a:b], dtype=float) for a, b in spans], fs_hz, params)
diff --git a/wl_preproc/eye/detect/nystrom_holmqvist.py b/wl_preproc/eye/detect/nystrom_holmqvist.py
index 012fe16..a1de693 100644
--- a/wl_preproc/eye/detect/nystrom_holmqvist.py
+++ b/wl_preproc/eye/detect/nystrom_holmqvist.py
@@ -40,7 +40,7 @@ from dataclasses import dataclass
 
 import numpy as np
 
-from wl_preproc.eye.detect.labels import Label, Run, true_runs
+from wl_preproc.eye.detect.labels import Label, Run, may_label, true_runs
 from wl_preproc.eye.detect.geometry import UNSTATED_GEOMETRY
 from wl_preproc.eye.detect.measure import amplitude
 
@@ -496,7 +496,7 @@ def detect_nystrom_holmqvist(
     **Assembles the three pure steps above, in the paper's own order**: the
     adaptive peak threshold, then -- for each velocity peak -- saccade
     bounds and the glissade that may follow it, then fixation over whatever
-    is left. `usable` is `available`'s own definition (`entry is None`),
+    is left. `usable` is `available`'s own definition (`labels.may_label`),
     additionally narrowed by Table 2's two rejections (max velocity, max
     acceleration) before either threshold estimation or peak-finding ever
     sees a sample -- the same reasoning `_peak_threshold`'s own docstring
@@ -512,7 +512,7 @@ def detect_nystrom_holmqvist(
     # derives from that one rather than replacing it.
     acceleration = np.abs(np.gradient(speed)) * fs_hz
 
-    usable = np.array([entry is None for entry in available], dtype=bool)
+    usable = may_label(available)
     usable &= speed <= params.max_velocity_deg_s
     usable &= acceleration <= params.max_acceleration_deg_s2
     if not usable.any():
diff --git a/wl_preproc/eye/detect/otero_millan.py b/wl_preproc/eye/detect/otero_millan.py
index cb0975f..0923335 100644
--- a/wl_preproc/eye/detect/otero_millan.py
+++ b/wl_preproc/eye/detect/otero_millan.py
@@ -91,7 +91,7 @@ from dataclasses import dataclass
 
 import numpy as np
 
-from wl_preproc.eye.detect.labels import Run
+from wl_preproc.eye.detect.labels import Run, may_label
 from wl_preproc.eye.detect.geometry import UNSTATED_GEOMETRY
 from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude, classify
 
@@ -236,7 +236,7 @@ def detect_otero_millan(
     formula, so design spec section 3's guarantee that a disagreement is "never
     a disagreement about measurement" holds literally.
     """
-    usable = np.array([entry is None for entry in available], dtype=bool)
+    usable = may_label(available)
     if not usable.any():
         return []
 
diff --git a/wl_preproc/eye/detect/registry.py b/wl_preproc/eye/detect/registry.py
index 26f1dea..b6b877f 100644
--- a/wl_preproc/eye/detect/registry.py
+++ b/wl_preproc/eye/detect/registry.py
@@ -34,7 +34,7 @@ import numpy as np
 
 from wl_preproc.eye.detect.bmd import DEFAULT_BMD_PARAMS, detect_bmd
 from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS, detect_engbert_kliegl
-from wl_preproc.eye.detect.labels import Label, LabelledInterval, kind_of, true_runs
+from wl_preproc.eye.detect.labels import Label, LabelledInterval, kind_of, may_label, true_runs
 from wl_preproc.eye.detect.nslr import DEFAULT_NSLR_PARAMS, detect_nslr
 from wl_preproc.eye.detect.nystrom_holmqvist import (
     DEFAULT_NH_PARAMS, detect_nystrom_holmqvist,
@@ -193,19 +193,66 @@ class Detector:
                 f"vocabulary {sorted(label.value for label in self.vocabulary)} "
                 "does not contain"
             )
-        return _within_usable(intervals, available)
+        return _within_usable(intervals, available, min_duration_samples(params, fs_hz))
 
 
-def _within_usable(intervals: list[LabelledInterval], available: np.ndarray) -> list[LabelledInterval]:
+def min_duration_samples(detector_params, fs_hz: float) -> int:
+    """The floor a detector's runs are held to: by `_within_usable`, a run
+    carried across lost samples (design spec
+    `2026-11-06-eye-lost-data-and-region-design.md` section 5), and by
+    `schema/detect.py::_overlapping`, the two-eye event, which inherits it
+    from the detector that produced both eyes' spans -- the SAME params
+    object the detector actually ran with, not a second reading of the
+    paramset dict. Moved here from `schema/detect.py` on 2026-11-06.
+
+    **The detector's own declared minimum, and never less than 2 samples**
+    (the requester's decision of 2026-09-28):
+    - `min_duration_samples` where the params declare it (Engbert-Kliegl's
+      6);
+    - otherwise `min_saccade_duration_ms`, counted in samples as the
+      detectors that declare it count it, `round(ms * fs_hz / 1000)`
+      (Nystrom-Holmqvist's and REMoDNaV's 10 ms: 5 samples at the rig's
+      498.55 Hz);
+    - otherwise nothing, so the lab-wide 2.
+
+    A single-sample event's amplitude is 0.0 deg by construction
+    (`measure`'s `gaze_deg[stop - 1] - gaze_deg[start]`), so no two-eye
+    event may be one sample, whatever its detector would accept.
+
+    *Until 2026-09-28 only `min_duration_samples` was read, with a default
+    of 1: the other five detectors' two-eye events could be one sample
+    long, and Nystrom-Holmqvist's and REMoDNaV's 10 ms minimums, declared
+    in milliseconds, were never seen. On the reference recording that
+    stored 3-26 two-eye saccades per detector at exactly 0.0 deg; true when
+    written.*
+    """
+    declared = getattr(detector_params, "min_duration_samples", None)
+    if declared is None:
+        minimum_ms = getattr(detector_params, "min_saccade_duration_ms", None)
+        declared = 1 if minimum_ms is None else int(round(minimum_ms * fs_hz / 1000.0))
+    return max(int(declared), 2)
+
+
+def _within_usable(intervals: list[LabelledInterval], available: np.ndarray, floor: int) -> list[LabelledInterval]:
     """Every interval held to the samples the validity mask offered: the
-    requester's decision of 2026-09-28. The mask, not the detector, owns
-    `blink` and `invalid` (the `vocabulary` comment above), so a run must
-    not overwrite them.
+    requester's decisions of 2026-09-28 and 2026-11-06 (design spec
+    `2026-11-06-eye-lost-data-and-region-design.md` section 5). The mask, not
+    the detector, owns `blink`, `invalid` and `lost` (the `vocabulary`
+    comment above), so a run must not overwrite a withheld sample.
     - With withheld samples only at its ends, a run is trimmed back to its
-      one usable stretch, keeping its label and reliability.
-    - With a withheld stretch inside it and usable data on both sides, a run
-      is dropped: a "saccade" across a blink is not a measurable saccade.
-    - With no usable sample, a run is dropped.
+      one offered stretch, keeping its label and reliability.
+    - With a withheld stretch inside it and offered samples on both sides, a
+      run is dropped: a "saccade" across a blink is not a measurable
+      saccade.
+    - A run is carried across lost samples (`Label.LOST`), at its ends as
+      inside it: a detector that is not Engbert-Kliegl ran on the trace with
+      their positions filled, and where it ended a run in a loss the eye may
+      have landed there. `measure.measure_event_run` measures such a run out
+      to the measured samples beyond its lost ends.
+    - A run holding lost samples is kept only if its measured samples alone
+      reach `floor`, its detector's own minimum: lost samples carry a run but
+      never make one. A run holding none is untouched.
+    - With no measured sample, a run is dropped.
 
     **A glissade goes with its saccade.** A `pso` run starts where its
     saccade ends, so when this guard drops a saccadic run, or trims its end,
@@ -216,16 +263,18 @@ def _within_usable(intervals: list[LabelledInterval], available: np.ndarray) ->
     edges walked into blinks: 521 of its 5,062 left-eye and 985 of its 5,213
     right-eye saccades, most of them straddling a blink. No other detector
     emitted such a run."""
-    usable = np.array([entry is None for entry in available], dtype=bool)
+    offered = may_label(available)
+    measured = np.array([entry is None for entry in available], dtype=bool)
     fates = []
     broken_saccade_ends = set()
     for interval in intervals:
-        pieces = true_runs(usable[interval.start:interval.stop])
-        if len(pieces) != 1:
-            fate = None
-        else:
-            start, stop = pieces[0]
-            fate = (interval.start + start, interval.start + stop)
+        pieces = true_runs(offered[interval.start:interval.stop])
+        fate = None
+        if len(pieces) == 1:
+            start, stop = interval.start + pieces[0][0], interval.start + pieces[0][1]
+            kept = np.flatnonzero(measured[start:stop])
+            if kept.size and (kept.size == stop - start or kept.size >= floor):
+                fate = (start, stop)
         fates.append(fate)
         if kind_of(interval.label) == "saccadic" and (fate is None or fate[1] != interval.stop):
             broken_saccade_ends.add(interval.stop)
diff --git a/wl_preproc/eye/detect/remodnav.py b/wl_preproc/eye/detect/remodnav.py
index 661c6f2..b21d0cd 100644
--- a/wl_preproc/eye/detect/remodnav.py
+++ b/wl_preproc/eye/detect/remodnav.py
@@ -27,7 +27,7 @@ import numpy as np
 from scipy.ndimage import median_filter
 from scipy.signal import butter, filtfilt
 
-from wl_preproc.eye.detect.labels import Label, Run, true_runs
+from wl_preproc.eye.detect.labels import Label, Run, may_label, true_runs
 from wl_preproc.eye.detect.velocity import _HALF_WINDOW, velocity
 
 #: statsmodels' `mad` normalisation, `scipy.stats.norm.ppf(0.75)` -- the
@@ -614,7 +614,7 @@ def detect_remodnav(
     slice is `{saccade}`, so conjunction runs take `_conjunction_label`'s
     degenerate branch, as Nystrom-Holmqvist's do (spec 4).
     """
-    usable = np.array([entry is None for entry in available], dtype=bool)
+    usable = may_label(available)
     if not usable.any():
         return []
     signals = Signals(
diff --git a/wl_preproc/eye/detect/uneye.py b/wl_preproc/eye/detect/uneye.py
index 6c1b37f..54abce1 100644
--- a/wl_preproc/eye/detect/uneye.py
+++ b/wl_preproc/eye/detect/uneye.py
@@ -31,7 +31,7 @@ from pathlib import Path
 
 import numpy as np
 
-from wl_preproc.eye.detect.labels import Label, Run, true_runs
+from wl_preproc.eye.detect.labels import Label, Run, may_label, true_runs
 from wl_preproc.eye.detect.numba_threads import keep_off_openmp
 
 #: The copied networks, each with two classes: fixation (0) and saccade (1).
@@ -146,7 +146,7 @@ def detect_uneye(
       it: 0.5 to 1 for a run the network found whole, and possibly less for
       one merged across a gap, whose samples there it called fixation.
     """
-    usable = np.array([entry is None for entry in available], dtype=bool)
+    usable = may_label(available)
     usable &= np.isfinite(np.asarray(gaze_deg, dtype=float)).all(axis=1)
     pieces = [(int(a), int(b)) for a, b in true_runs(usable) if b - a >= MIN_PIECE_SAMPLES]
     if not pieces:
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_lost_data.py tests/eye/detect/test_registry.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `56 passed`

- [ ] **Step 5: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/eye/detect -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `582 passed, 32 skipped, 1 xfailed`

- [ ] **Step 6: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(detect): every detector carries a run across a short loss of data -- Engbert-Kliegl, the contract's detector, as wl-xcon's online copy does: an unknown velocity continues an open run, never opens one or counts toward its minimum, and a short loss at its end is carried to the landing; the other six run unchanged on the lost samples offered, and one shared rule holds every detector's runs to measured samples enough to make them (sections 4 and 5, and the correction both sessions agreed on 2026-11-06)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

- [ ] **Step 7: The mutation checks,** on the commit. Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "ek-withholds-lost",
  "wl_preproc/eye/detect/engbert_kliegl.py",
  "    for start, stop in true_runs(outside | lost):",
  "    for start, stop in true_runs(outside):",
  "tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_registry.py tests/eye/detect/test_lost_data.py"
 ],
 [
  "ek-counts-lost",
  "wl_preproc/eye/detect/engbert_kliegl.py",
  "        if events.size == 0 or events.size < params.min_duration_samples:",
  "        if events.size == 0 or stop - start < params.min_duration_samples:",
  "tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_registry.py tests/eye/detect/test_lost_data.py"
 ],
 [
  "ek-keeps-trailing-lost",
  "wl_preproc/eye/detect/engbert_kliegl.py",
  "        first, last = int(events[0]), int(events[-1]) + 1",
  "        first, last = int(events[0]), int(stop)",
  "tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_registry.py tests/eye/detect/test_lost_data.py"
 ],
 [
  "the-ports-withhold-lost",
  "wl_preproc/eye/detect/labels.py",
  "    return np.array([entry is None or entry is Label.LOST for entry in available], dtype=bool)",
  "    return np.array([entry is None for entry in available], dtype=bool)",
  "tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_registry.py tests/eye/detect/test_lost_data.py"
 ],
 [
  "the-floor-is-ignored",
  "wl_preproc/eye/detect/registry.py",
  "            if kept.size and (kept.size == stop - start or kept.size >= floor):",
  "            if kept.size:",
  "tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_registry.py tests/eye/detect/test_lost_data.py"
 ],
 [
  "lost-ends-are-trimmed",
  "wl_preproc/eye/detect/registry.py",
  "                fate = (start, stop)",
  "                fate = (start + int(kept[0]), start + int(kept[-1]) + 1)",
  "tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_registry.py tests/eye/detect/test_lost_data.py"
 ],
 [
  "the-rule-withholds-lost",
  "wl_preproc/eye/detect/registry.py",
  "    offered = may_label(available)\n",
  "    offered = np.array([entry is None for entry in available], dtype=bool)\n",
  "tests/eye/detect/test_engbert_kliegl.py tests/eye/detect/test_registry.py tests/eye/detect/test_lost_data.py"
 ]
]
```

Expected: 7 lines, each `CAUGHT`; `git status --short` shows no change.

---

### Task 3: Each detected run stores its lost samples and its flag

§3, §5, §6 and §8, amendments 4, 6 and 7: `EyeValidity.make` hands the mask the repaired glitches; `EyeDetection.make` fills a carried loss's positions along frame time, a dropped frame's neighbours keeping theirs, before the velocity; each detector's run stores `n_lost` and `flagged` (a lost sample inside it or in the two samples before its start; on the conjunction, either eye's), the mask's own runs neither; a run is measured on an eye with no withheld sample in it, and the two-eye fallback counts a lost sample as offered; a run with lost ends is measured out to the measured samples beyond them, and a peak speed is its measured samples'.

**Files:**
- Modify: `wl_preproc/eye/detect/measure.py` (`measure_event_run`)
- Modify: `wl_preproc/schema/detect.py` (`EyeDetection.Run`'s `n_lost`, `flagged`; `_MASK_LABELS`; `EyeValidity.make`, `EyeDetection.make`, `_insert_trace`, `_conjunction_fallback`; `_min_duration_samples` delegates)
- Test: `tests/eye/detect/test_measure.py`, `tests/schema/test_detect_populate.py`

**Interfaces:**
- Consumes: Task 1's `fill_lost`, `frame_numbers`, `rows_across_gaps`, `may_label`; Task 2's
  `registry.min_duration_samples`.
- Produces: `detect.EyeDetection.Run.n_lost`, `.flagged`; `detect._MASK_LABELS`; the module fixture
  `dropped_frame_session` and `_DROPPED_FRAME_ROW` in `tests/schema/test_detect_populate.py`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/eye/detect/test_measure.py b/tests/eye/detect/test_measure.py
index da201af..3323559 100644
--- a/tests/eye/detect/test_measure.py
+++ b/tests/eye/detect/test_measure.py
@@ -410,3 +410,35 @@ def test_a_one_sample_fixation_has_no_direction():
 def test_an_unknown_geometry_is_refused():
     with pytest.raises(ValueError, match="geometry"):
         amplitude(np.zeros((5, 2)), 0, 5, geometry="cyclopean")
+
+
+def test_a_runs_peak_speed_is_its_measured_samples_alone():
+    """Design spec `2026-11-06-eye-lost-data-and-region-design.md` section 6:
+    a lost sample's speed is not a measurement, so it is never the peak; the
+    run is flagged instead."""
+    velocity = _speeds()
+    velocity[12, 0] = 900.0
+    velocity[14, 0] = 300.0
+    offered = _offered(40)
+    offered[12] = Label.LOST
+
+    got = measure_event_run(np.zeros((40, 2)), velocity, offered, 10, 16, 500.0, geometry=PER_AXIS,
+                            runs_end_before_landing=False, min_measured_ms=None)
+
+    assert got.peak_velocity_deg_s == 300.0
+
+
+def test_a_run_with_lost_ends_is_measured_out_to_the_measured_samples_beyond_them():
+    """A detector that ran on the filled trace may end a run in a loss, where
+    the eye landed: its size reaches the first measured sample after it, and
+    its start the last before (design spec
+    `2026-11-06-eye-lost-data-and-region-design.md`, the correction of
+    2026-11-06)."""
+    gaze = np.column_stack([np.arange(40, dtype=float), np.zeros(40)])
+    offered = _offered(40)
+    offered[[10, 15]] = Label.LOST
+
+    got = measure_event_run(gaze, _speeds(), offered, 10, 16, 500.0, geometry=PER_AXIS,
+                            runs_end_before_landing=False, min_measured_ms=None)
+
+    assert (got.start_x_deg, got.end_x_deg, got.amplitude_deg) == (9.0, 16.0, 7.0)
diff --git a/tests/schema/test_detect_populate.py b/tests/schema/test_detect_populate.py
index 473454a..a0ddcda 100644
--- a/tests/schema/test_detect_populate.py
+++ b/tests/schema/test_detect_populate.py
@@ -738,6 +738,58 @@ def _inject_missing_left_gaze(session_dir) -> None:
     ohdpi_txt.write_text("\n".join([header_line, *data_lines]) + "\n", encoding="utf-8")
 
 
+def _drop_a_frame_inside_the_first_step(session_dir) -> None:
+    """One frame of the eye camera dropped half-way through the first planted
+    ramp, A to B, 2.4 degrees: its row removed from the file, so its frame
+    number is missing for both eyes, as the camera drops one."""
+    (ohdpi_txt,) = (session_dir / "ohdpi").glob("*.txt")
+    lines = ohdpi_txt.read_text(encoding="utf-8").splitlines()
+    del lines[1 + _DROPPED_FRAME_ROW]
+    ohdpi_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
+
+
+#: The file row `dropped_frame_session` removes: eight frames into the first
+#: ramp, which lasts sixteen (`_DURATIONS_S[0]`).
+_DROPPED_FRAME_ROW = _first_row_at(4 * TRIAL_DURATION_S + _ONSET_OFFSETS_S[0]) + 8
+
+
+@pytest.fixture(scope="module")
+def dropped_frame_session(daemon_module, prefix, tmp_path_factory):
+    """`_build_stepped_session`'s own construction with one frame dropped
+    inside its first planted saccade (`_drop_a_frame_inside_the_first_step`),
+    and the daemon run immediately after (design spec
+    `2026-11-06-eye-lost-data-and-region-design.md` section 11). Date,
+    subject and seed checked unclaimed across `tests/` on 2026-11-06.
+    Returns the session's key."""
+    session_key, _segment, _onset_times = _build_stepped_session(
+        tmp_path_factory,
+        dirname="detectdrop", session_id="2027-06-24_01", subject="detdrop1",
+        session_datetime=datetime.datetime(2027, 6, 24, 9, 0), seed=624,
+        after_generate=_drop_a_frame_inside_the_first_step,
+    )
+    daemon_module.run_once(prefix=prefix)
+    return session_key
+
+
+def test_a_saccade_across_a_dropped_frame_is_one_saccade_flagged_with_its_lost_samples(dropped_frame_session):
+    """The requester's question of 2026-11-06 began here: one frame dropped
+    inside a saccade split it in two, or erased it. The four rows whose
+    velocity spans the gap are lost, and each trace's saccade is carried
+    across them: one saccade, its four lost samples counted, flagged."""
+    from wl_preproc.schema import detect, paramset
+
+    key = dropped_frame_session
+    gap = _DROPPED_FRAME_ROW - 1
+    ek = [row["paramset_idx"] for row in (paramset.ParamSet & {"paramset_type": "eye_detection"}).to_dicts()
+          if row["params"]["detector"] == "engbert_kliegl"]
+    for trace in ("left", "right", "conjunction"):
+        runs = (detect.EyeDetection.Run & key & {"paramset_idx": ek[0], "trace": trace}
+                & f"run_start <= {gap + 2} AND run_stop > {gap - 1}").to_dicts()
+        assert [(row["label"], row["n_lost"], row["flagged"]) for row in runs] == [("saccade", 4, 1)], (trace, runs)
+    lost = (detect.EyeValidity.Run & key & {"eye": "left", "label": "lost"}).to_dicts()
+    assert [(row["run_start"], row["run_stop"]) for row in lost] == [(gap - 1, gap + 3)]
+
+
 @pytest.fixture(scope="module")
 def missing_gaze_session(daemon_module, prefix, tmp_path_factory):
     """`_build_stepped_session`'s own construction with one missing left-eye
@@ -1389,17 +1441,18 @@ def test_a_missing_gaze_value_is_lost_and_counted_end_to_end(missing_gaze_sessio
     assert covering["label"] == "lost"
 
 
-def test_no_detector_labels_a_missing_gaze_value(missing_gaze_session):
-    """The point of withholding it: every registered detector's stored run
-    over the missing sample, on the left trace, is the mask's own `lost`
-    -- no detector was handed the sample to label. The conjunction falls
-    back to the right eye there: it carries the right eye's own label, and
-    `EyeDetection.Source` says `right` (design spec
+def test_no_detector_measures_a_missing_gaze_value(missing_gaze_session):
+    """Lost, the sample is offered with its position filled (design spec
+    `2026-11-06-eye-lost-data-and-region-design.md` section 3): every
+    registered detector's stored run over it, on the left trace, is the
+    mask's own `lost`, or a run of the detector's carried across it and
+    flagged. The conjunction draws on both eyes there, the left eye's loss
+    offered as the right eye's measurement is (design spec
     `2026-09-28-both-eyes-fallback-design.md` section 1).
 
-    *Until 2026-09-28 the conjunction was labelled from the left eye's mask
-    alone, and read `invalid` here too; until 2026-11-06 the mask's own run
-    was `invalid`; true when written.*"""
+    *Until 2026-11-06 it was withheld, every left run over it the mask's
+    `invalid`, and the conjunction fell back to the right eye there; true
+    when written.*"""
     from wl_preproc.schema import detect
 
     def covering(table, trace, name, start, stop):
@@ -1410,12 +1463,9 @@ def test_no_detector_labels_a_missing_gaze_value(missing_gaze_session):
     session_key, _report, missing_row = missing_gaze_session
     for name in _detector_names():
         left = covering(detect.EyeDetection.Run, "left", name, "run_start", "run_stop")
-        assert left["label"] == "lost", (name, left["label"])
-        right = covering(detect.EyeDetection.Run, "right", name, "run_start", "run_stop")
-        both = covering(detect.EyeDetection.Run, "conjunction", name, "run_start", "run_stop")
-        assert both["label"] == right["label"], (name, both["label"], right["label"])
+        assert left["label"] == "lost" or left["flagged"] == 1, (name, left["label"], left["flagged"])
         source = covering(detect.EyeDetection.Source, "conjunction", name, "source_start", "source_stop")
-        assert source["source"] == "right", (name, source)
+        assert source["source"] == "both", (name, source)
 
 
 def test_saccade_runs_carry_measurements_and_others_do_not(stepped_session):
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/eye/detect/test_measure.py tests/schema/test_detect_populate.py::test_a_saccade_across_a_dropped_frame_is_one_saccade_flagged_with_its_lost_samples tests/schema/test_detect_populate.py::test_no_detector_measures_a_missing_gaze_value -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `4 failed, 54 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/eye/detect/measure.py b/wl_preproc/eye/detect/measure.py
index 8dab1d2..39fd643 100644
--- a/wl_preproc/eye/detect/measure.py
+++ b/wl_preproc/eye/detect/measure.py
@@ -283,10 +283,27 @@ def measure_event_run(
         and bool(np.isfinite(gaze_deg[start - 1]).all())
     ):
         lo = start - 1
+    # A run carried across a loss at its ends is measured out to the
+    # measured samples beyond it, where the eye took off and landed (design
+    # spec `2026-11-06-eye-lost-data-and-region-design.md`, the correction
+    # of 2026-11-06); the mask carries only a loss with one on each side.
+    while lo > 0 and offered[lo] is Label.LOST:
+        lo -= 1
+    while hi < gaze_deg.shape[0] and offered[hi - 1] is Label.LOST:
+        hi += 1
     if (lo, hi) == (start, stop):
-        return measure(gaze_deg, velocity_deg_s, start, stop, fs_hz, geometry=geometry)
-    widened = measure(gaze_deg, velocity_deg_s, lo, hi, fs_hz, geometry=geometry)
-    return dataclasses.replace(widened, duration_s=float(stop - start) / fs_hz)
+        measurement = measure(gaze_deg, velocity_deg_s, start, stop, fs_hz, geometry=geometry)
+    else:
+        widened = measure(gaze_deg, velocity_deg_s, lo, hi, fs_hz, geometry=geometry)
+        measurement = dataclasses.replace(widened, duration_s=float(stop - start) / fs_hz)
+    # A lost sample's speed is not a measurement, so the peak is its measured
+    # samples' (design spec `2026-11-06-eye-lost-data-and-region-design.md`
+    # section 6); where the loss fell on the peak, the run's flag says so.
+    measured = np.array([entry is None for entry in offered[lo:hi]], dtype=bool)
+    if not measured.all():
+        speed = np.hypot(velocity_deg_s[lo:hi, 0], velocity_deg_s[lo:hi, 1])[measured]
+        measurement = dataclasses.replace(measurement, peak_velocity_deg_s=float(speed.max()) if speed.size else 0.0)
+    return measurement
 
 
 def classify(amplitude_deg: float, microsaccade_max_deg: float) -> Label:
diff --git a/wl_preproc/schema/detect.py b/wl_preproc/schema/detect.py
index 491afce..9e058ea 100644
--- a/wl_preproc/schema/detect.py
+++ b/wl_preproc/schema/detect.py
@@ -106,6 +106,7 @@ from wl_preproc.eye.detect.labels import (
     Run,
     kind_of,
     labels_from_runs,
+    may_label,
     runs_from_labels,
     true_runs,
 )
@@ -315,7 +316,7 @@ class EyeValidity(dj.Computed):
             gaze, repaired = _repaired_gaze(path, file_eye, map_, recording.fs_hz, params)
             mask = validity_labels(
                 gaze, velocity(gaze, recording.fs_hz), read_tracker_loss(path, file_eye),
-                recording.frame_gaps, params, fs_hz=recording.fs_hz,
+                recording.frame_gaps, params, fs_hz=recording.fs_hz, repaired=repaired,
             )
             labels = mask.labels
             # `validity_labels` returns `None` for a usable sample; encoded
@@ -497,6 +498,14 @@ class EyeDetection(dj.Computed):
         start_polar_deg=null        : double
         end_eccentricity_deg=null   : double
         end_polar_deg=null          : double
+        # The lost samples inside a detector's run, 0 for one measured
+        # throughout, and whether it is flagged: a lost sample inside it or in
+        # the two samples before its start, where its onset was decided
+        # (design spec `2026-11-06-eye-lost-data-and-region-design.md`
+        # sections 5 and 8). On the conjunction, either eye's. NULL on the
+        # mask's own runs.
+        n_lost=null  : int unsigned
+        flagged=null : tinyint(1)
         """
 
     class Source(dj.Part):
@@ -587,7 +596,7 @@ class EyeDetection(dj.Computed):
         refused session wearing one eye's excuse for the other's silence.
         """
         from wl_preproc.eye.detect.registry import get_detector
-        from wl_preproc.eye.detect.validity import ValidityParams
+        from wl_preproc.eye.detect.validity import ValidityParams, fill_lost, frame_numbers, rows_across_gaps
         from wl_preproc.eye.detect.velocity import local_velocity, velocity
         from wl_preproc.eye.ohdpi import read_ohdpi
         from wl_preproc.schema import eye as eye_schema, ingest
@@ -624,7 +633,8 @@ class EyeDetection(dj.Computed):
         session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
         segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
         path = session_dir / "ohdpi" / segment["file_path"]
-        fs_hz = read_ohdpi(path).fs_hz
+        recording = read_ohdpi(path)
+        fs_hz = recording.fs_hz
 
         spans: dict[str, list[Run]] = {}
         per_eye: dict[str, tuple] = {}
@@ -653,13 +663,6 @@ class EyeDetection(dj.Computed):
                 (eye_schema.EyeCalibration & {**session_key, "eye": eye_value}).fetch1()
             )
             gaze, _repaired = _repaired_gaze(path, file_eye, map_, fs_hz, validity_params)
-            v = velocity(gaze, fs_hz)
-            # Under true angle, the eye's angular velocity in the local frame at
-            # each sample (true-angle spec section 5): what every run is
-            # measured on, and what a detector that detects in the setting's
-            # geometry is handed (`registry.Detector.detects_in_geometry`).
-            # Under per-axis, the per-axis velocity, as v1.
-            measured_v = local_velocity(gaze, fs_hz) if geometry == TRUE_ANGLE else v
             available = labels_from_runs(
                 [Run(r["run_start"], r["run_stop"], Label(r["label"]))
                  for r in (EyeValidity.Run & {**validity_key, "eye": eye_value}).to_dicts(
@@ -668,6 +671,19 @@ class EyeDetection(dj.Computed):
             )
             # The mask stored `fixation` where a sample is available.
             offered = np.where(available == Label.FIXATION, None, available)
+            # Over a carried loss, a position that is not a measurement is
+            # filled along frame time, so every detector reads finite gaze
+            # (design spec `2026-11-06-eye-lost-data-and-region-design.md`
+            # section 3); the velocities are the filled gaze's.
+            gaze = fill_lost(gaze, offered, frame_numbers(len(gaze), recording.frame_gaps),
+                             rows_across_gaps(len(gaze), recording.frame_gaps))
+            v = velocity(gaze, fs_hz)
+            # Under true angle, the eye's angular velocity in the local frame at
+            # each sample (true-angle spec section 5): what every run is
+            # measured on, and what a detector that detects in the setting's
+            # geometry is handed (`registry.Detector.detects_in_geometry`).
+            # Under per-axis, the per-axis velocity, as v1.
+            measured_v = local_velocity(gaze, fs_hz) if geometry == TRUE_ANGLE else v
             # `detector.detect`, never `detector.run`: the wrapper is what
             # holds the detector to its own declared `vocabulary`, and this
             # is the one place in production that runs a detector at all.
@@ -1007,6 +1023,12 @@ class EyeDetection(dj.Computed):
         # unmeasured (design spec `2026-09-28-both-eyes-fallback-design.md`
         # section 3).
         kept = kept or {}
+        # The lost samples a run's flag and count read: its trace's, or, on the
+        # conjunction, either eye's (design spec
+        # `2026-11-06-eye-lost-data-and-region-design.md` section 6).
+        lost = np.logical_or.reduce([np.array([label is Label.LOST for label in mask], dtype=bool)
+                                     for mask in ([offered] if eyes is None
+                                                  else [eyes["left"][2], eyes["right"][2]])])
 
         def _measured_on(run: Run):
             span = (run.start, run.stop)
@@ -1016,7 +1038,7 @@ class EyeDetection(dj.Computed):
                 return (trace, gaze, v, offered)
             for eye in ("left", "right"):
                 eye_gaze, eye_v, eye_offered = eyes[eye]
-                if all(label is None for label in eye_offered[run.start:run.stop]):
+                if may_label(eye_offered[run.start:run.stop]).all():
                     return (trace, eye_gaze, eye_v, offered)
             return None
 
@@ -1052,11 +1074,19 @@ class EyeDetection(dj.Computed):
                 **{column: None if measurement is None else getattr(measurement, column)
                    for column in _STORED_MEASUREMENTS},
                 "reliability": reliability_by_span.get((run.start, run.stop)),
+                **({"n_lost": None, "flagged": None} if run.label in _MASK_LABELS else
+                   {"n_lost": int(lost[run.start:run.stop].sum()),
+                    "flagged": int(lost[max(run.start - 2, 0):run.stop].any())}),
             }
 
         self.Run.insert(_run_row(index, run) for index, run in enumerate(runs))
 
 
+#: The labels the validity mask, not a detector, writes into a trace: no
+#: lost-sample count or flag (`EyeDetection.Run`).
+_MASK_LABELS = frozenset({Label.BLINK, Label.INVALID, Label.LOST})
+
+
 #: The `Measurement` fields `EyeDetection.Run` stores, each under its own
 #: name, all NULL for a run that is not measured. `duration_s` is not stored:
 #: it is `(run_stop - run_start) / fs_hz` on every row.
@@ -1322,8 +1352,7 @@ def _conjunction_fallback(left, right, left_offered, right_offered):
     `source` a per-sample object array of `both`, `left`, `right` or
     `neither` -- which eyes were usable, with each kept run marked by its
     own eye along its whole length."""
-    usable = {"left": np.array([label is None for label in left_offered], dtype=bool),
-              "right": np.array([label is None for label in right_offered], dtype=bool)}
+    usable = {"left": may_label(left_offered), "right": may_label(right_offered)}
     runs = {"left": sorted(left, key=lambda run: run.start), "right": sorted(right, key=lambda run: run.start)}
     withheld_before = {eye: np.concatenate(([0], np.cumsum(~usable[eye]))) for eye in usable}
 
@@ -1705,36 +1734,11 @@ def _repaired_gaze(path, file_eye: str, map_, fs_hz: float, validity_params):
 
 
 def _min_duration_samples(detector_params, fs_hz: float) -> int:
-    """The floor `_overlapping` inherits from the detector that produced both
-    eyes' spans -- the SAME params object the detector actually ran with, not
-    a second reading of the paramset dict.
-
-    **The detector's own declared minimum, and never less than 2 samples**
-    (the requester's decision of 2026-09-28):
-    - `min_duration_samples` where the params declare it (Engbert-Kliegl's
-      6);
-    - otherwise `min_saccade_duration_ms`, counted in samples as the
-      detectors that declare it count it, `round(ms * fs_hz / 1000)`
-      (Nystrom-Holmqvist's and REMoDNaV's 10 ms: 5 samples at the rig's
-      498.55 Hz);
-    - otherwise nothing, so the lab-wide 2.
-
-    A single-sample event's amplitude is 0.0 deg by construction
-    (`measure`'s `gaze_deg[stop - 1] - gaze_deg[start]`), so no two-eye
-    event may be one sample, whatever its detector would accept.
-
-    *Until 2026-09-28 only `min_duration_samples` was read, with a default
-    of 1: the other five detectors' two-eye events could be one sample
-    long, and Nystrom-Holmqvist's and REMoDNaV's 10 ms minimums, declared
-    in milliseconds, were never seen. On the reference recording that
-    stored 3-26 two-eye saccades per detector at exactly 0.0 deg; true when
-    written.*
-    """
-    declared = getattr(detector_params, "min_duration_samples", None)
-    if declared is None:
-        minimum_ms = getattr(detector_params, "min_saccade_duration_ms", None)
-        declared = 1 if minimum_ms is None else int(round(minimum_ms * fs_hz / 1000.0))
-    return max(int(declared), 2)
+    """`registry.min_duration_samples`, where it moved on 2026-11-06: the
+    shared rule holds every detector's runs to it as well."""
+    from wl_preproc.eye.detect.registry import min_duration_samples
+
+    return min_duration_samples(detector_params, fs_hz)
 
 
 def _params_for(detector, params: dict):
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/eye/detect/test_measure.py tests/schema/test_detect_populate.py::test_a_saccade_across_a_dropped_frame_is_one_saccade_flagged_with_its_lost_samples tests/schema/test_detect_populate.py::test_no_detector_measures_a_missing_gaze_value -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `58 passed`

- [ ] **Step 5: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/nwb tests/schema/test_detect_populate.py tests/schema/test_true_angle_detection.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `198 passed, 1 skipped`

- [ ] **Step 6: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(detect): each detected run stores its lost samples and its flag -- the gaze a detector reads is filled over a carried loss, a dropped frame's neighbours keeping their measured positions; a run with lost ends is measured out to the samples beyond them, and its peak speed is its measured samples' (sections 3, 5, 6 and 8)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

- [ ] **Step 7: The mutation checks,** on the commit. Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "the-peak-is-a-lost-sample",
  "wl_preproc/eye/detect/measure.py",
  "    if not measured.all():",
  "    if False:",
  "tests/eye/detect/test_measure.py"
 ],
 [
  "lost-samples-uncounted",
  "wl_preproc/schema/detect.py",
  "                   {\"n_lost\": int(lost[run.start:run.stop].sum()),",
  "                   {\"n_lost\": 0,",
  "tests/schema/test_detect_populate.py::test_a_saccade_across_a_dropped_frame_is_one_saccade_flagged_with_its_lost_samples"
 ],
 [
  "never-flagged",
  "wl_preproc/schema/detect.py",
  "                    \"flagged\": int(lost[max(run.start - 2, 0):run.stop].any())}),",
  "                    \"flagged\": 0}),",
  "tests/schema/test_detect_populate.py::test_a_saccade_across_a_dropped_frame_is_one_saccade_flagged_with_its_lost_samples"
 ]
]
```

Expected: 3 lines, each `CAUGHT`; `git status --short` shows no change.

---

### Task 4: The screen region is each session's recorded field plus 2 degrees

§7 and §8, amendment 8: `events/rigsession.py` reads `config.json`'s `setup` into `RigSetup`; the event stage keeps it in a new table, `core.SessionSetup`; the mask's region is the field plus `region_margin_deg` where the paramset takes it from the setup and the session recorded one, the placeholder otherwise, and `EyeValidity` records which; the synthetic rig records this rig's direct view, as wl-xcon's does.

**Files:**
- Modify: `wl_preproc/events/rigsession.py` (`RigSetup`, `_setup`), `schema/core.py` (`SessionSetup`), `schema/events.py`
- Modify: `wl_preproc/eye/detect/validity.py` (`ValidityParams.region_margin_deg`, `.region_from_setup`; `screen_region`; `ValidityMask.region_deg`, `.region_from_setup`), `schema/detect.py` (`EyeValidity`'s region columns and `make`)
- Modify: `wl_preproc/synth/peripherals.py` (`RIG_SETUP`), `synth/recipe.py` (`setup_recorded`)
- Test: `tests/events/test_rigsession.py`, `tests/eye/detect/test_validity.py`, `tests/synth/test_peripherals.py`, `tests/schema/test_events.py`, `tests/schema/test_detect_populate.py`, `tests/schema/test_nwb_build.py`

**Interfaces:**
- Consumes: Task 3's `dropped_frame_session`.
- Produces: `rigsession.RigSetup(view, half_field_deg)`, `RigSession.setup`; `core.SessionSetup`;
  `validity.screen_region(params, field_deg) -> (width, height, from_setup)`; `validity_labels(..., field_deg=None)`;
  `detect.EyeValidity.region_half_width_deg`, `.region_half_height_deg`, `.region_from_setup`;
  `peripherals.RIG_SETUP`; `SessionRecipe.setup_recorded = True`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/events/test_rigsession.py b/tests/events/test_rigsession.py
index 32e9430..41a4c8d 100644
--- a/tests/events/test_rigsession.py
+++ b/tests/events/test_rigsession.py
@@ -7,6 +7,8 @@ from __future__ import annotations
 
 import json
 
+import pytest
+
 CALIBRATION = {"id": "rig-a-2026-10-01", "standard": False, "measured_on": "2026-10-01",
                "cones": {"observer": {"name": "CIE2006_10"}, "via": "spectra", "luminance": None, "dkl": "unit"}}
 
@@ -196,3 +198,41 @@ def test_each_warning_keeps_its_line_in_the_file(tmp_path):
     warnings = read_rig_session(_record(tmp_path, {"subject": "pico"}, [
         _warning("default-calibration"), "{not json", _warning("head-free")]), "pico").warnings
     assert [(warning.code, warning.line) for warning in warnings] == [("default-calibration", 1), ("head-free", 3)]
+
+
+#: `config.json`'s `setup` as wl-xcon's `taskd.Session._fixed_config` writes it
+#: (its `main` at `957ec98`), this rig in direct view.
+SETUP = {"view": "direct", "viewing_distance_cm": 50.0, "half_field_deg": [30.54, 18.41], "mask_deg": None}
+
+
+def test_the_display_setup_is_read_from_config(tmp_path):
+    """Each eye's visible field, from which the validity mask's screen region
+    comes (design spec `2026-11-06-eye-lost-data-and-region-design.md`
+    section 7)."""
+    from wl_preproc.events.rigsession import RigSetup, read_rig_session
+
+    session = read_rig_session(_record(tmp_path, {"subject": "pico", "setup": SETUP}), "pico")
+
+    assert session.setup == RigSetup(view="direct", half_field_deg=(30.54, 18.41))
+    assert session.problems == ()
+
+
+def test_a_config_written_before_the_setup_has_none_and_no_problem(tmp_path):
+    """wl-xcon writes it since its `c634749`, 2026-09-29."""
+    from wl_preproc.events.rigsession import read_rig_session
+
+    session = read_rig_session(_record(tmp_path, {"subject": "pico"}), "pico")
+
+    assert (session.setup, session.problems) == (None, ())
+
+
+@pytest.mark.parametrize("field", [None, [30.0], [30.0, "wide"], [30.0, -1.0], [30.0, float("inf")]])
+def test_a_setup_naming_no_visible_field_is_a_problem_and_none(tmp_path, field):
+    from wl_preproc.events.rigsession import read_rig_session
+
+    session = read_rig_session(_record(tmp_path, {"subject": "pico", "setup": {**SETUP, "half_field_deg": field}}),
+                               "pico")
+
+    assert session.setup is None
+    assert session.problems == (
+        "config.json: setup names no visible field, [horizontal, vertical] half-angles in degrees",)
diff --git a/tests/eye/detect/test_validity.py b/tests/eye/detect/test_validity.py
index 737d2a5..3ad07a9 100644
--- a/tests/eye/detect/test_validity.py
+++ b/tests/eye/detect/test_validity.py
@@ -796,3 +796,48 @@ def test_lost_positions_are_filled_along_frame_time_and_a_dropped_frames_neighbo
     assert filled[5:7, 0].tolist() == [5.0, 6.0]
     assert filled[13, 0] == 99.0 and filled[12, 0] == 12.0
     assert np.isfinite(filled).all() and filled[[0, 4, 7, 19], 0].tolist() == [0.0, 4.0, 7.0, 19.0]
+
+
+
+# Design spec `2026-11-06-eye-lost-data-and-region-design.md` section 7: the
+# screen region is each session's visible field plus a margin, on each axis;
+# a session that recorded no field keeps the placeholder.
+_SETUP = ValidityParams(**{**asdict(_QUIET), "region_from_setup": True, "region_margin_deg": 2.0})
+
+
+def test_the_screen_region_is_the_sessions_visible_field_plus_its_margin():
+    gaze, vel, loss, gaps = _clean(4)
+    gaze[:] = [(32.5, 0.0), (32.6, 0.0), (0.0, 20.4), (0.0, 20.5)]
+
+    mask = validity_labels(gaze, vel, loss, gaps, _SETUP, fs_hz=FS_HZ, field_deg=(30.54, 18.41))
+
+    assert list(mask.labels) == [None, Label.INVALID, None, Label.INVALID]
+    assert mask.region_deg == pytest.approx((32.54, 20.41)) and mask.region_from_setup is True
+
+
+def test_a_session_with_no_recorded_field_keeps_the_placeholder():
+    gaze, vel, loss, gaps = _clean(2)
+    gaze[:] = [(19.9, 0.0), (20.1, 0.0)]
+
+    mask = validity_labels(gaze, vel, loss, gaps, _SETUP, fs_hz=FS_HZ, field_deg=None)
+
+    assert list(mask.labels) == [None, Label.INVALID]
+    assert (mask.region_deg, mask.region_from_setup) == ((20.0, 15.0), False)
+
+
+def test_a_paramset_stored_before_the_region_came_from_the_session_keeps_its_own():
+    """Loaded without the two fields, it masks at its stored region whatever
+    the session recorded, so its content hash still names what it does."""
+    stored = {name: value for name, value in asdict(DEFAULT_VALIDITY_PARAMS).items()
+              if name not in ("region_from_setup", "region_margin_deg")}
+    params = ValidityParams(**stored)
+    gaze, vel, loss, gaps = _clean(1)
+
+    mask = validity_labels(gaze, vel, loss, gaps, params, fs_hz=FS_HZ, field_deg=(30.54, 18.41))
+
+    assert (params.region_from_setup, params.region_margin_deg) == (False, 0.0)
+    assert (mask.region_deg, mask.region_from_setup) == ((20.0, 15.0), False)
+
+
+def test_the_default_takes_the_region_from_the_session_with_a_2_degree_margin():
+    assert (DEFAULT_VALIDITY_PARAMS.region_from_setup, DEFAULT_VALIDITY_PARAMS.region_margin_deg) == (True, 2.0)
diff --git a/tests/schema/test_detect_populate.py b/tests/schema/test_detect_populate.py
index a0ddcda..7bb0beb 100644
--- a/tests/schema/test_detect_populate.py
+++ b/tests/schema/test_detect_populate.py
@@ -5141,3 +5141,14 @@ def test_a_paramsets_geometry_is_read_once_and_an_unknown_one_refused():
     assert _geometry_of({"geometry": "true-angle"}) == "true-angle"
     with pytest.raises(ValueError, match="geometry"):
         _geometry_of({"geometry": "cyclopean"})
+
+
+def test_a_session_that_recorded_no_field_is_masked_at_the_placeholder(dropped_frame_session):
+    """No `config.json`, so no setup: the region is the paramset's own, and
+    the mask says so (design spec `2026-11-06-eye-lost-data-and-region-design.md`
+    section 7)."""
+    from wl_preproc.schema import detect
+
+    rows = (detect.EyeValidity & dropped_frame_session & {"status": "computed"}).to_dicts()
+    assert rows and {(row["region_half_width_deg"], row["region_half_height_deg"], row["region_from_setup"])
+                     for row in rows} == {(20.0, 15.0, 0)}
diff --git a/tests/schema/test_events.py b/tests/schema/test_events.py
index 822e1d7..3e8b3fb 100644
--- a/tests/schema/test_events.py
+++ b/tests/schema/test_events.py
@@ -913,3 +913,19 @@ def test_a_lone_surrogate_in_a_warnings_text_is_kept_and_the_stage_completes(eve
                                        rig_record=_rig_record(warnings=(
                                            RigWarningSpec(code="head-free", by={"kind": "box", "name": "J\ud83d"}),)))
     assert (core.AcceptedWarning & key).fetch1("by_name") == "J\\ud83d"
+
+
+def test_populate_session_keeps_the_rigs_display_setup(events_activated, dj_conn, tmp_path):
+    """Each eye's visible field, where the session's `config.json` records
+    one (design spec `2026-11-06-eye-lost-data-and-region-design.md`
+    section 7); a session whose config records none has no row."""
+    from wl_preproc.schema import core
+    from wl_preproc.synth.peripherals import RIG_SETUP
+
+    _recipe, key = _populate_generated(tmp_path / "with", "slsetup1", datetime.datetime(2027, 7, 27, 9, 0), runs=True)
+    _recipe, bare = _populate_generated(tmp_path / "without", "slsetup2", datetime.datetime(2027, 7, 27, 9, 0),
+                                        runs=True, setup_recorded=False)
+
+    assert (core.SessionSetup & key).fetch1("view", "half_field_h_deg", "half_field_v_deg") == (
+        "direct", *RIG_SETUP["half_field_deg"])
+    assert len(core.SessionSetup & bare) == 0
diff --git a/tests/schema/test_nwb_build.py b/tests/schema/test_nwb_build.py
index 65b5cda..e1b5041 100644
--- a/tests/schema/test_nwb_build.py
+++ b/tests/schema/test_nwb_build.py
@@ -1658,3 +1658,16 @@ def test_a_trial_in_no_run_is_named_in_the_canonicals_description(activation, tm
     assert [note for note in result.description["notes"] if "in no measured run" in note] == [
         "1 trial(s) and 0 block(s) start in montage 0's window but in no measured run, so the file leaves them "
         "out: their run's RUN_START was lost, or they were strobed outside a run"]
+
+
+def test_the_masks_screen_region_is_the_sessions_field_plus_its_margin(activation):
+    """A session whose `config.json` records its setup (design spec
+    `2026-11-06-eye-lost-data-and-region-design.md` section 7)."""
+    from wl_preproc.schema import detect
+    from wl_preproc.synth.peripherals import RIG_SETUP
+
+    session_key, _key, _runs = activation
+    rows = (detect.EyeValidity & session_key & {"status": "computed"}).to_dicts()
+    width, height = RIG_SETUP["half_field_deg"]
+    assert rows and {(row["region_half_width_deg"], row["region_half_height_deg"], row["region_from_setup"])
+                     for row in rows} == {(width + 2.0, height + 2.0, 1)}
diff --git a/tests/synth/test_peripherals.py b/tests/synth/test_peripherals.py
index 354eab2..10bcb0d 100644
--- a/tests/synth/test_peripherals.py
+++ b/tests/synth/test_peripherals.py
@@ -215,8 +215,10 @@ def test_a_recipe_without_a_rig_record_writes_config_as_before(tmp_path):
     recipe = SessionRecipe.model_validate({**CI_RECIPE.model_dump(), "runs": True})
     (tmp_path / "xcon").mkdir()
     write_rig_runs(tmp_path / "xcon", recipe, build_timeline(recipe))
+    from wl_preproc.synth.peripherals import RIG_SETUP
+
     assert json.loads((tmp_path / "xcon" / "config.json").read_text()) == {
-        "session_id": recipe.session_id, "subject": recipe.subject}
+        "session_id": recipe.session_id, "subject": recipe.subject, "setup": RIG_SETUP}
     assert "calibration" not in (tmp_path / "xcon" / "runs.jsonl").read_text()
     assert not (tmp_path / "xcon" / "warnings.jsonl").exists()
 
@@ -316,3 +318,22 @@ def test_an_act_past_the_sessions_trials_is_refused():
 
     with pytest.raises(ValueError, match=r"rig_record acts in trials \[5\], past the session's 4"):
         _training(controls=(RigControlSpec(trial=5),))
+
+
+def test_the_config_records_this_rigs_display_setup_unless_a_recipe_says_not(tmp_path):
+    """As wl-xcon's does since 2026-09-29, this rig in direct view: each eye's
+    field `atan(half-size / distance)` per axis, as wl-xcon's `geometry.py`
+    computes it (design spec `2026-11-06-eye-lost-data-and-region-design.md`
+    section 7)."""
+    import math
+
+    from wl_preproc.synth.peripherals import RIG_SETUP, write_rig_runs
+    from wl_preproc.synth.recipe import SessionRecipe
+
+    assert RIG_SETUP["view"] == "direct"
+    assert RIG_SETUP["half_field_deg"] == [math.degrees(math.atan(58.997 / 2 / 50.0)),
+                                           math.degrees(math.atan(33.293 / 2 / 50.0))]
+    recipe = SessionRecipe.model_validate({**CI_RECIPE.model_dump(), "runs": True, "setup_recorded": False})
+    (tmp_path / "xcon").mkdir()
+    write_rig_runs(tmp_path / "xcon", recipe, build_timeline(recipe))
+    assert "setup" not in json.loads((tmp_path / "xcon" / "config.json").read_text())
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/events/test_rigsession.py tests/eye/detect/test_validity.py tests/synth/test_peripherals.py tests/schema/test_events.py::test_populate_session_keeps_the_rigs_display_setup tests/schema/test_detect_populate.py::test_a_session_that_recorded_no_field_is_masked_at_the_placeholder tests/schema/test_nwb_build.py::test_the_masks_screen_region_is_the_sessions_field_plus_its_margin -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `1 error`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/events/rigsession.py b/wl_preproc/events/rigsession.py
index a14904f..c729dc4 100644
--- a/wl_preproc/events/rigsession.py
+++ b/wl_preproc/events/rigsession.py
@@ -1,5 +1,6 @@
-"""What wl-xcon's record says the session was: its kind and the display
-calibration it opened with, from `xcon/config.json`, and the warnings it
+"""What wl-xcon's record says the session was: its kind, the display
+calibration it opened with and the display it ran on, from
+`xcon/config.json`, and the warnings it
 accepted, from `xcon/warnings.jsonl` (design spec
 `2026-10-14-session-kind-and-warnings-design.md` sections 1 and 2).
 
@@ -47,6 +48,19 @@ class RigCalibration:
     cones: dict | None
 
 
+@dataclasses.dataclass(frozen=True, slots=True)
+class RigSetup:
+    """The display a session ran on, from `config.json`'s `setup` (wl-xcon's
+    `taskd.Session._fixed_config`, since its `c634749`, 2026-09-29): its view,
+    and each eye's visible field, `[horizontal, vertical]` half-angles in
+    per-axis degrees, capped by the stereoscope's mask. The validity mask's
+    screen region is that field plus a margin (design spec
+    `2026-11-06-eye-lost-data-and-region-design.md` section 7)."""
+
+    view: str | None
+    half_field_deg: tuple[float, float]
+
+
 @dataclasses.dataclass(frozen=True, slots=True)
 class RigWarning:
     """One accepted warning, as wl-xcon wrote it. `run_index` is wl-xcon's
@@ -78,6 +92,7 @@ class RigSession:
     # False for a record whose `config.json` names another animal: nothing of
     # it is kept but its problem.
     of_this_session: bool = True
+    setup: RigSetup | None = None
 
 
 def _text(value: object) -> str | None:
@@ -108,6 +123,19 @@ def _calibration(value: object, problems: list[str]) -> RigCalibration | None:
                           measured_on=_text(value.get("measured_on")), cones=cones if isinstance(cones, dict) else None)
 
 
+def _setup(value: object, problems: list[str]) -> RigSetup | None:
+    """A config written before wl-xcon recorded its setup has none, which is
+    not a problem; one whose field is not two positive finite numbers is."""
+    if value is None:
+        return None
+    field = value.get("half_field_deg") if isinstance(value, dict) else None
+    halves = [_number(half) for half in field] if isinstance(field, list) and len(field) == 2 else [None]
+    if any(half is None or half <= 0 for half in halves):
+        problems.append(f"{CONFIG_NAME}: setup names no visible field, [horizontal, vertical] half-angles in degrees")
+        return None
+    return RigSetup(view=_text(value.get("view")), half_field_deg=(halves[0], halves[1]))
+
+
 def _warning(row: dict, line: int) -> RigWarning:
     accepted_in = row.get("accepted_in")
     by = row.get("by") if isinstance(row.get("by"), dict) else {}
@@ -146,6 +174,7 @@ def read_rig_session(session_dir: Path, subject: str) -> RigSession | None:
         problems.append(f"{CONFIG_NAME}: session kind {kind!r} is not one of {', '.join(SESSION_KINDS)}")
         kind = None
     calibration = _calibration(config.get("calibration"), problems)
+    setup = _setup(config.get("setup"), problems)
     warnings = []
     record = xcon / WARNINGS_NAME
     lines = record.read_text(encoding="utf-8").splitlines() if record.is_file() else []
@@ -161,4 +190,5 @@ def read_rig_session(session_dir: Path, subject: str) -> RigSession | None:
             problems.append(f"{WARNINGS_NAME} line {number_of_line}: not a warning naming its code")
             continue
         warnings.append(_warning(row, number_of_line))
-    return RigSession(kind=kind, calibration=calibration, warnings=tuple(warnings), problems=tuple(problems))
+    return RigSession(kind=kind, calibration=calibration, warnings=tuple(warnings), problems=tuple(problems),
+                      setup=setup)
diff --git a/wl_preproc/eye/detect/validity.py b/wl_preproc/eye/detect/validity.py
index 30c9ed1..3bb2801 100644
--- a/wl_preproc/eye/detect/validity.py
+++ b/wl_preproc/eye/detect/validity.py
@@ -106,6 +106,13 @@ class ValidityParams:
     # `max_gap_s`). 0 by default, as the pads are: a paramset stored before
     # it carries nothing and masks as it did.
     max_lost_ms: float = 0.0
+    # The screen region from the session's own recorded display setup: each
+    # eye's visible field plus `region_margin_deg` on each axis, where the
+    # session recorded one (design spec
+    # `2026-11-06-eye-lost-data-and-region-design.md` section 7). Off and 0
+    # by default, so a paramset stored before them masks at its own region.
+    region_margin_deg: float = 0.0
+    region_from_setup: bool = False
 
 
 # **These are placeholders with no measured basis on this rig** (design spec
@@ -131,6 +138,12 @@ DEFAULT_VALIDITY_PARAMS = ValidityParams(
     # wl-xcon's online detector's bound, which the requester's decision of
     # 2026-11-06 makes both detectors' (its `max_gap_s`, 0.05 s).
     max_lost_ms=50.0,
+    # The requester's decision of 2026-11-06: the visible field the session
+    # recorded, plus 2 degrees for calibration error and fixation scatter
+    # about a target at its edge. The placeholder region above stays for a
+    # session that recorded none.
+    region_margin_deg=2.0,
+    region_from_setup=True,
 )
 
 
@@ -183,6 +196,11 @@ class ValidityMask:
 
     labels: np.ndarray
     fractions: Mapping[str, float]
+    # The screen region the mask applied, `(half-width, half-height)` in
+    # per-axis degrees, and whether it was the session's own field plus the
+    # margin or the paramset's placeholder (`screen_region`).
+    region_deg: tuple[float, float]
+    region_from_setup: bool
 
 
 def validity_labels(
@@ -194,6 +212,7 @@ def validity_labels(
     *,
     fs_hz: float,
     repaired: np.ndarray | None = None,
+    field_deg: tuple[float, float] | None = None,
 ) -> ValidityMask:
     """One entry per sample: a `Label` where the sample is unusable, `None`
     where a detector may label it -- and beside it each criterion's own
@@ -201,7 +220,9 @@ def validity_labels(
     and for what they deliberately do not sum to. `fs_hz` is the
     recording's rate, which turns the blink pads' and the bound's
     milliseconds into samples. `repaired` marks the samples
-    `glitch.repair_glitches` filled in, lost where lost data is carried."""
+    `glitch.repair_glitches` filled in, lost where lost data is carried.
+    `field_deg` is each eye's visible field as the session recorded it, or
+    None (`screen_region`)."""
     n = gaze_deg.shape[0]
     # Eye open, read by the rule: a blink (the pupil not found), and a lost
     # reflection (P1 or P4 at 0, or `DataQuality` below 100), where `CR1 -
@@ -210,9 +231,8 @@ def validity_labels(
     blink = loss.blink
     feature_lost = loss.feature_lost
 
-    outside = (np.abs(gaze_deg[:, 0]) > params.region_half_width_deg) | (
-        np.abs(gaze_deg[:, 1]) > params.region_half_height_deg
-    )
+    width, height, from_setup = screen_region(params, field_deg)
+    outside = (np.abs(gaze_deg[:, 0]) > width) | (np.abs(gaze_deg[:, 1]) > height)
     too_fast = np.hypot(velocity_deg_s[:, 0], velocity_deg_s[:, 1]) > params.max_speed_deg_s
 
     # A gap sits between rows `row` and `row + 1`. The velocity estimator spans
@@ -359,9 +379,22 @@ def validity_labels(
             "short_epoch": _fraction(short_epoch),
             "non_finite": _fraction(non_finite),
         },
+        region_deg=(width, height),
+        region_from_setup=from_setup,
     )
 
 
+def screen_region(params: ValidityParams, field_deg: tuple[float, float] | None) -> tuple[float, float, bool]:
+    """`(half-width, half-height, from_setup)`: the session's visible field
+    plus `region_margin_deg` on each axis, where the paramset takes it from
+    the setup and the session recorded one; otherwise the paramset's own
+    region (design spec `2026-11-06-eye-lost-data-and-region-design.md`
+    section 7)."""
+    if params.region_from_setup and field_deg is not None:
+        return (field_deg[0] + params.region_margin_deg, field_deg[1] + params.region_margin_deg, True)
+    return (params.region_half_width_deg, params.region_half_height_deg, False)
+
+
 def rows_across_gaps(n: int, frame_gaps: Sequence[FrameGap]) -> np.ndarray:
     """The rows whose velocity spans a dropped frame: `[row-1, row+2]` for a
     gap after `row`, coupled to `_HALF_WINDOW` (`validity_labels`)."""
diff --git a/wl_preproc/schema/core.py b/wl_preproc/schema/core.py
index 755297d..a537aac 100644
--- a/wl_preproc/schema/core.py
+++ b/wl_preproc/schema/core.py
@@ -93,6 +93,28 @@ class RunRecordProblem(dj.Manual):
     """
 
 
+@schema
+class SessionSetup(dj.Manual):
+    definition = """
+    # The display a session ran on, from `xcon/config.json`'s `setup`, read by
+    # `schema/events.py::populate_session`: its view, and each eye's visible
+    # field as wl-xcon records it, half-angles in per-axis degrees capped by
+    # the stereoscope's mask (wl-xcon's `taskd.Session._fixed_config`, since
+    # its `c634749`, 2026-09-29). The validity mask's screen region is that
+    # field plus a margin (`schema/detect.py::EyeValidity`; design spec
+    # 2026-11-06-eye-lost-data-and-region-design.md section 7). Its own table
+    # rather than columns of `SessionRecord`, so a database declared before it
+    # needs no ALTER. Only a session whose config.json of its own animal
+    # records one has a row.
+    # Key: (subject, session_datetime).
+    -> pipeline.Session
+    ---
+    view = null      : varchar(16)  # stereoscope or direct, as written
+    half_field_h_deg : double       # each eye's horizontal half-field
+    half_field_v_deg : double       # each eye's vertical half-field
+    """
+
+
 @schema
 class SessionRecord(dj.Manual):
     definition = """
diff --git a/wl_preproc/schema/detect.py b/wl_preproc/schema/detect.py
index 9e058ea..f949cf9 100644
--- a/wl_preproc/schema/detect.py
+++ b/wl_preproc/schema/detect.py
@@ -179,6 +179,14 @@ class EyeValidity(dj.Computed):
     # recording (6,684 of 10,969) and 45% of the right's, and with the
     # margin and the blink pads 84% and 75%.
     frac_glitch_repaired=null : double
+    # The screen region the mask applied, half-width and half-height in
+    # per-axis degrees, and whether it was the session's own visible field
+    # plus the paramset's margin (`core.SessionSetup`) or the paramset's
+    # placeholder (design spec `2026-11-06-eye-lost-data-and-region-design.md`
+    # section 7). `NULL` on a refused row.
+    region_half_width_deg=null  : double
+    region_half_height_deg=null : double
+    region_from_setup=null      : tinyint(1)
     reason='' : varchar(255)
     """
 
@@ -300,6 +308,10 @@ class EyeValidity(dj.Computed):
         # `fs_hz`/`frame_gaps` describe the FILE's own sync line, not either
         # eye's gaze -- read once per session rather than once per eye.
         recording = read_ohdpi(path)
+        # The session's own visible field, where its config recorded one
+        # (design spec `2026-11-06-eye-lost-data-and-region-design.md` section 7).
+        setup = (core.SessionSetup & session_key).to_dicts()
+        field_deg = (setup[0]["half_field_h_deg"], setup[0]["half_field_v_deg"]) if setup else None
 
         for eye_value, file_eye in (("left", "Left"), ("right", "Right")):
             calibration = (eye_schema.EyeCalibration & {**session_key, "eye": eye_value})
@@ -316,7 +328,7 @@ class EyeValidity(dj.Computed):
             gaze, repaired = _repaired_gaze(path, file_eye, map_, recording.fs_hz, params)
             mask = validity_labels(
                 gaze, velocity(gaze, recording.fs_hz), read_tracker_loss(path, file_eye),
-                recording.frame_gaps, params, fs_hz=recording.fs_hz, repaired=repaired,
+                recording.frame_gaps, params, fs_hz=recording.fs_hz, repaired=repaired, field_deg=field_deg,
             )
             labels = mask.labels
             # `validity_labels` returns `None` for a usable sample; encoded
@@ -362,6 +374,8 @@ class EyeValidity(dj.Computed):
                 # short version for a reader looking only at the schema.
                 **{f"frac_{criterion}": value
                    for criterion, value in mask.fractions.items()},
+                "region_half_width_deg": mask.region_deg[0], "region_half_height_deg": mask.region_deg[1],
+                "region_from_setup": int(mask.region_from_setup),
                 "reason": "",
             })
             self.Run.insert(
diff --git a/wl_preproc/schema/events.py b/wl_preproc/schema/events.py
index aed7fc0..e3f2f73 100644
--- a/wl_preproc/schema/events.py
+++ b/wl_preproc/schema/events.py
@@ -671,6 +671,12 @@ def populate_session(key: dict, session_dir: Path) -> None:
             "calibration_measured_on": None if calibration is None else _clip(calibration.measured_on, 32),
             "calibration_cones": None if calibration is None else calibration.cones,
         }, skip_duplicates=True)
+        if rig_session.setup is not None:
+            core.SessionSetup.insert1({
+                **session_key, "view": _clip(rig_session.setup.view, 16),
+                "half_field_h_deg": rig_session.setup.half_field_deg[0],
+                "half_field_v_deg": rig_session.setup.half_field_deg[1],
+            }, skip_duplicates=True)
         numbers = rig_runs.run_numbers if rig_runs is not None else {}
         warning_rows = []
         for number, warning in enumerate(rig_session.warnings, start=1):
diff --git a/wl_preproc/synth/peripherals.py b/wl_preproc/synth/peripherals.py
index a08f660..29188b1 100644
--- a/wl_preproc/synth/peripherals.py
+++ b/wl_preproc/synth/peripherals.py
@@ -9,6 +9,7 @@ from __future__ import annotations
 
 import datetime
 import json
+import math
 import random
 from collections.abc import Callable, Sequence
 from pathlib import Path
@@ -250,6 +251,8 @@ def write_rig_runs(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth) ->
     (xcon_dir / "runs.jsonl").write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
                                          encoding="utf-8")
     config = {"session_id": recipe.session_id, "subject": recipe.subject}
+    if recipe.setup_recorded:
+        config["setup"] = RIG_SETUP
     if rig is not None:
         config["session_kind"] = rig.session_kind
         config["calibration"] = None if rig.calibration_id is None else {
@@ -301,6 +304,16 @@ def write_rig_records(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth,
 # `luminosity_weights` are placeholders: carried verbatim and read by nothing.
 # `via` and `luminance` are null, as wl-xcon writes them where no cone colour
 # converts, so the nulls a nested record holds are carried too.
+#: The display setup the synthetic rig records in `config.json`, as wl-xcon's
+#: `taskd.Session._fixed_config` writes it (its `main` at `957ec98`): this
+#: rig in direct view, its panel 58.997 x 33.293 cm at 50 cm (wl-xcon's
+#: `tasks/rig.py`), so each eye's field is `atan(half-size / distance)` per
+#: axis, as its `geometry.py` computes it (design spec
+#: `2026-11-06-eye-lost-data-and-region-design.md` section 7).
+RIG_SETUP = {"view": "direct", "viewing_distance_cm": 50.0, "mask_deg": None,
+             "half_field_deg": [math.degrees(math.atan(58.997 / 2 / 50.0)),
+                                math.degrees(math.atan(33.293 / 2 / 50.0))]}
+
 RIG_CONES = {
     "observer": {"name": "CIE 2006 10°", "data": "10.25039/CIE.DS.nxsqeri8", "field_deg": 10.0,
                  "peak_optical_density": [0.38, 0.38, 0.30], "macular_density_460nm": 0.095,
diff --git a/wl_preproc/synth/recipe.py b/wl_preproc/synth/recipe.py
index 1e51f3c..06f2e14 100644
--- a/wl_preproc/synth/recipe.py
+++ b/wl_preproc/synth/recipe.py
@@ -368,6 +368,11 @@ class SessionRecipe(BaseModel):
     # runs', so it needs them. Off by default, so every existing profile is
     # byte-identical.
     rig_record: RigRecordSpec | None = None
+    # Whether `config.json` records the display setup, as wl-xcon's has since
+    # 2026-09-29 (`peripherals.RIG_SETUP`; design spec
+    # `2026-11-06-eye-lost-data-and-region-design.md` section 7). On by
+    # default, so every synthetic session with runs is masked at its field.
+    setup_recorded: bool = True
 
     # How many neurons this session contains. Zero is legal and is what every
     # timing-only fixture wants: Phase 1c's recipes care about barcodes and
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/events/test_rigsession.py tests/eye/detect/test_validity.py tests/synth/test_peripherals.py tests/schema/test_events.py::test_populate_session_keeps_the_rigs_display_setup tests/schema/test_detect_populate.py::test_a_session_that_recorded_no_field_is_masked_at_the_placeholder tests/schema/test_nwb_build.py::test_the_masks_screen_region_is_the_sessions_field_plus_its_margin -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `110 passed`

- [ ] **Step 5: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/events tests/schema/test_events.py tests/schema/test_nwb_build.py tests/synth -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `393 passed`

- [ ] **Step 6: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(eye): the screen region is each session's recorded visible field plus 2 degrees -- wl-xcon's config.json setup kept in core.SessionSetup, the mask's region and its source recorded in EyeValidity, the placeholder only where a session recorded none, and the synthetic rig records its setup as wl-xcon's does (section 7)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

- [ ] **Step 7: The mutation checks,** on the commit. Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "the-setup-is-not-read",
  "wl_preproc/events/rigsession.py",
  "    setup = _setup(config.get(\"setup\"), problems)",
  "    setup = None",
  "tests/events/test_rigsession.py"
 ],
 [
  "a-negative-field-is-read",
  "wl_preproc/events/rigsession.py",
  "    if any(half is None or half <= 0 for half in halves):",
  "    if any(half is None for half in halves):",
  "tests/events/test_rigsession.py"
 ],
 [
  "the-margin-is-dropped",
  "wl_preproc/eye/detect/validity.py",
  "        return (field_deg[0] + params.region_margin_deg, field_deg[1] + params.region_margin_deg, True)",
  "        return (field_deg[0], field_deg[1], True)",
  "tests/eye/detect/test_validity.py"
 ],
 [
  "an-old-paramset-takes-the-setup",
  "wl_preproc/eye/detect/validity.py",
  "    if params.region_from_setup and field_deg is not None:",
  "    if field_deg is not None:",
  "tests/eye/detect/test_validity.py"
 ],
 [
  "the-setup-is-not-stored",
  "wl_preproc/schema/events.py",
  "        if rig_session.setup is not None:",
  "        if False:",
  "tests/schema/test_events.py::test_populate_session_keeps_the_rigs_display_setup"
 ],
 [
  "the-synthetic-rig-records-none",
  "wl_preproc/synth/peripherals.py",
  "    if recipe.setup_recorded:",
  "    if False:",
  "tests/synth/test_peripherals.py"
 ]
]
```

Expected: 6 lines, each `CAUGHT`; `git status --short` shows no change.

---

### Task 5: Flagged saccades out of the fits; the NWB file and the report name the lost data

§6 and §10, the requester's decision 5.2, amendment 9: the main sequence and vigor take no flagged run (`main_sequence.selected`'s `flagged`), and `schema/main_sequence.flagged_left_out` counts what each fit left out; each NWB eye-events row carries `n_lost` and `flagged`, and the validity table `lost`; the daily report gives each eye's lost share, each fit's flagged saccades left out on its vigor line, and each eye masked at the placeholder.

**Files:**
- Modify: `wl_preproc/eye/detect/main_sequence.py`, `schema/main_sequence.py`
- Modify: `wl_preproc/nwb/eye_events.py`, `nwb/gather.py` (`MASK_LABELS`)
- Modify: `wl_preproc/cli/report.py` (`_unusable_fractions`, `_vigor_lines`, `_placeholder_region_lines`, `build_report`)
- Test: `tests/eye/detect/test_main_sequence.py`, `tests/nwb/test_writers.py`, `tests/cli/test_report.py`, `tests/cli/test_detect_report.py`, `tests/schema/test_detect_populate.py`

**Interfaces:**
- Consumes: Task 3's `EyeDetection.Run.flagged` and `dropped_frame_session`; Task 4's
  `EyeValidity.region_from_setup`.
- Produces: `main_sequence.selected(..., flagged=None)`; `schema.main_sequence.flagged_left_out(runs, fs_hz,
  params) -> int`; `report._placeholder_region_lines(ingested_keys, prefix)`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/cli/test_detect_report.py b/tests/cli/test_detect_report.py
index 4c20f88..2de4ef8 100644
--- a/tests/cli/test_detect_report.py
+++ b/tests/cli/test_detect_report.py
@@ -310,7 +310,7 @@ def _insert_detection(schema, row: dict) -> None:
 
 
 def _raw_totals(schema=None, run_rows: list[dict] | None = None):
-    """`({"blink": n, "invalid": n}, total)` counted row by row off
+    """`({"blink": n, "invalid": n, "lost": n}, total)` counted row by row off
     `EyeValidity.Run`, INDEPENDENTLY of `_unusable_fractions` and of the
     database aggregation that feeds it.
 
@@ -325,7 +325,7 @@ def _raw_totals(schema=None, run_rows: list[dict] | None = None):
     if run_rows is None:
         run_rows = schema.EyeValidity.Run.to_dicts()
     total = sum(row["run_stop"] - row["run_start"] for row in run_rows)
-    counts = {"blink": 0, "invalid": 0}
+    counts = {"blink": 0, "invalid": 0, "lost": 0}
     for row in run_rows:
         if row["label"] in counts:
             counts[row["label"]] += row["run_stop"] - row["run_start"]
@@ -727,7 +727,7 @@ def test_an_empty_pipeline_reports_zero_unusable_rather_than_a_wrong_number():
     """
     from wl_preproc.cli.report import _unusable_fractions
 
-    assert _unusable_fractions([]) == {"blink": 0.0, "invalid": 0.0}
+    assert _unusable_fractions([]) == {"blink": 0.0, "invalid": 0.0, "lost": 0.0}
 
 
 def test_a_mostly_blink_session_is_visible_per_session_rather_than_only_in_the_total(
@@ -829,9 +829,13 @@ def _pre_m8_fractions(run_rows: list[dict]) -> dict[str, float]:
                 counts[row["label"]] += row["run_stop"] - row["run_start"]
         return {label: (counts[label] / total if total else 0.0)
                 for label in ("blink", "invalid")}
+
+    Extended to `lost`, the mask's third label since 2026-11-06 (design spec
+    `2026-11-06-eye-lost-data-and-region-design.md` section 10), by the same
+    arithmetic.
     """
     counts, total = _raw_totals(run_rows=run_rows)
-    return {label: (counts[label] / total if total else 0.0) for label in ("blink", "invalid")}
+    return {label: (counts[label] / total if total else 0.0) for label in ("blink", "invalid", "lost")}
 
 
 def test_the_database_aggregation_matches_the_full_table_scan_it_replaced(
diff --git a/tests/cli/test_report.py b/tests/cli/test_report.py
index c14543c..3d0b9c1 100644
--- a/tests/cli/test_report.py
+++ b/tests/cli/test_report.py
@@ -961,3 +961,14 @@ def test_a_session_landed_without_its_sync_box_leaves_the_stalled_list_and_says_
     finally:
         (pipeline.Session & {"subject": "rptwo2"}).delete(prompt=False)
         (ingest.LandedWithout & {"subject": "rptwo2"}).delete(prompt=False)
+
+
+def test_the_unusable_shares_name_the_lost_samples_beside_blink_and_invalid():
+    """Design spec `2026-11-06-eye-lost-data-and-region-design.md` section 10:
+    a lost sample is not measured, carried by a run or not."""
+    from wl_preproc.cli.report import _unusable_fractions
+
+    shares = _unusable_fractions([{"label": "lost", "samples": 1}, {"label": "blink", "samples": 1},
+                                  {"label": "fixation", "samples": 2}])
+
+    assert shares == {"blink": 0.25, "invalid": 0.0, "lost": 0.25}
diff --git a/tests/eye/detect/test_main_sequence.py b/tests/eye/detect/test_main_sequence.py
index a1f5d7e..d2f1745 100644
--- a/tests/eye/detect/test_main_sequence.py
+++ b/tests/eye/detect/test_main_sequence.py
@@ -205,3 +205,15 @@ def test_vigor_needs_enough_saccades_the_history_covers():
     result = vigor(amplitude, PLANTED(amplitude), history, PARAMS)
     assert result.value is None
     assert result.reason == "too few saccades (10)"
+
+
+def test_a_flagged_saccade_is_left_out_of_the_fits():
+    """Design spec `2026-11-06-eye-lost-data-and-region-design.md` section 6,
+    the requester's decision: its peak speed may be understated where the
+    loss fell on its peak."""
+    from wl_preproc.eye.detect.main_sequence import DEFAULT_MAIN_SEQUENCE_PARAMS, selected
+
+    taken = selected(["saccade", "saccade"], [5.0, 5.0], [20, 20], 500.0, DEFAULT_MAIN_SEQUENCE_PARAMS,
+                     flagged=[False, True])
+
+    assert taken.tolist() == [True, False]
diff --git a/tests/nwb/test_writers.py b/tests/nwb/test_writers.py
index 2978b74..7d08147 100644
--- a/tests/nwb/test_writers.py
+++ b/tests/nwb/test_writers.py
@@ -464,3 +464,18 @@ def test_a_kind_and_calibration_not_recorded_are_empty_and_no_warnings_mean_no_t
                 session["calibration_cones"]) == ("", "", -1, "")
         assert "accepted_warnings" not in module.data_interfaces
         assert "parameter_changes" not in module.data_interfaces and "controls" not in module.data_interfaces
+
+
+def test_each_event_carries_its_lost_samples_and_its_flag(tmp_path):
+    """Design spec `2026-11-06-eye-lost-data-and-region-design.md` section 10."""
+    from wl_preproc.nwb.eye_events import add_detections
+    from wl_preproc.nwb.gather import MASK_LABELS
+
+    runs = [{"start_s": 1.0, "stop_s": 1.04, "label": "saccade", "n_lost": 4, "flagged": 1},
+            {"start_s": 1.04, "stop_s": 2.0, "label": "fixation", "n_lost": 0, "flagged": 0}]
+
+    _path, io, nwb = _write(tmp_path, lambda nwb: add_detections(nwb, [{"name": "t", "description": "d", "runs": runs}]))
+    with io:
+        table = nwb.processing["eye_events"]["t"].to_dataframe()
+        assert table["n_lost"].tolist() == [4, 0] and table["flagged"].tolist() == [1, 0]
+    assert MASK_LABELS == ("blink", "invalid", "lost")
diff --git a/tests/schema/test_detect_populate.py b/tests/schema/test_detect_populate.py
index 7bb0beb..1c8b5e2 100644
--- a/tests/schema/test_detect_populate.py
+++ b/tests/schema/test_detect_populate.py
@@ -5152,3 +5152,29 @@ def test_a_session_that_recorded_no_field_is_masked_at_the_placeholder(dropped_f
     rows = (detect.EyeValidity & dropped_frame_session & {"status": "computed"}).to_dicts()
     assert rows and {(row["region_half_width_deg"], row["region_half_height_deg"], row["region_from_setup"])
                      for row in rows} == {(20.0, 15.0, 0)}
+
+
+def test_a_flagged_saccade_is_left_out_of_each_fit_and_the_report_says_so(dropped_frame_session, prefix):
+    """The requester's decision of 2026-11-06 (design spec
+    `2026-11-06-eye-lost-data-and-region-design.md` sections 6 and 10): the
+    main sequence and vigor fit only saccades measured throughout, and the
+    daily report counts what each fit left out, and names each eye masked at
+    the placeholder region."""
+    from wl_preproc.cli.report import _placeholder_region_lines, _vigor_lines
+    from wl_preproc.eye.detect.main_sequence import DEFAULT_MAIN_SEQUENCE_PARAMS
+    from wl_preproc.schema import detect, main_sequence, paramset
+
+    key = dropped_frame_session
+    (ek,) = [row["paramset_idx"] for row in (paramset.ParamSet & {"paramset_type": "eye_detection"}).to_dicts()
+             if row["params"]["detector"] == "engbert_kliegl"]
+    runs = (detect.EyeDetection.Run & key & {"paramset_idx": ek, "trace": "left"}
+            & 'label in ("saccade", "microsaccade")').to_dicts(order_by="run_index")
+
+    taken = main_sequence.selected_runs(runs, 500.0, DEFAULT_MAIN_SEQUENCE_PARAMS)
+    assert taken and not any(run["flagged"] for run in taken)
+    assert main_sequence.flagged_left_out(runs, 500.0, DEFAULT_MAIN_SEQUENCE_PARAMS) == 1
+    session = {(key["subject"], key["session_datetime"])}
+    (line,) = [line for line in _vigor_lines(session, prefix=prefix) if "— left (" in line]
+    assert "`engbert_kliegl` (true angle)" in line and "1 flagged saccade left out" in line
+    assert [line.split(" — ")[1].split(" (")[0]
+            for line in _placeholder_region_lines(session, prefix=prefix)] == ["left", "right"]
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/cli/test_detect_report.py tests/cli/test_report.py::test_the_unusable_shares_name_the_lost_samples_beside_blink_and_invalid tests/eye/detect/test_main_sequence.py tests/nwb/test_writers.py tests/schema/test_detect_populate.py::test_a_flagged_saccade_is_left_out_of_each_fit_and_the_report_says_so -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `6 failed, 55 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/cli/report.py b/wl_preproc/cli/report.py
index 9f62b45..2895032 100644
--- a/wl_preproc/cli/report.py
+++ b/wl_preproc/cli/report.py
@@ -751,6 +751,22 @@ def _detection_rows(prefix: str = DEFAULT_PREFIX) -> tuple[list[dict], list[dict
     ]
 
 
+def _placeholder_region_lines(ingested_keys: set, prefix: str = DEFAULT_PREFIX) -> list[str]:
+    """Each eye of a session ingested in the last 24 h whose validity mask
+    took the paramset's placeholder screen region, its session having
+    recorded no display setup (design spec
+    `2026-11-06-eye-lost-data-and-region-design.md` sections 7 and 10)."""
+    from wl_preproc.schema import detect as detect_schema
+
+    detect_schema.activate(prefix=prefix)
+    rows = (detect_schema.EyeValidity & {"status": "computed", "region_from_setup": 0}).to_dicts(
+        order_by=["subject", "session_datetime", "eye", "validity_paramset_idx"])
+    return [f"- `{row['subject']}` @ {row['session_datetime']:%Y-%m-%d %H:%M} — {row['eye']} (validity paramset "
+            f"{row['validity_paramset_idx']}): no display setup recorded, so the screen region is the placeholder, "
+            f"±{row['region_half_width_deg']:g} × ±{row['region_half_height_deg']:g} degrees"
+            for row in rows if (row["subject"], row["session_datetime"]) in ingested_keys]
+
+
 def _unusable_fractions(label_totals: list[dict]) -> dict[str, float]:
     """`{"blink": fraction, "invalid": fraction}` of the samples covered by
     whatever `_detection_rows` rows it is handed -- every one of them for
@@ -787,11 +803,11 @@ def _unusable_fractions(label_totals: list[dict]) -> dict[str, float]:
     section 9's.
     """
     total = sum(row["samples"] for row in label_totals)
-    counts = {"blink": 0, "invalid": 0}
+    counts = {"blink": 0, "invalid": 0, "lost": 0}
     for row in label_totals:
         if row["label"] in counts:
             counts[row["label"]] += row["samples"]
-    return {label: (counts[label] / total if total else 0.0) for label in ("blink", "invalid")}
+    return {label: (counts[label] / total if total else 0.0) for label in counts}
 
 
 def _unusable_per_eye(label_totals: list[dict]) -> list[dict]:
@@ -1080,9 +1096,10 @@ def _vigor_lines(ingested_keys: set, prefix: str = DEFAULT_PREFIX) -> list[str]:
         if not rows:
             return "not computed yet"
         params = fit_params[fit_idx]
-        runs = main_sequence.selected_runs(
-            (detect_schema.EyeDetection.Run & {name: detection[name] for name in detect_schema.EyeDetection.primary_key}
-             & 'label in ("saccade", "microsaccade")').to_dicts(order_by="run_index"), rows[0]["fs_hz"], params)
+        every = (detect_schema.EyeDetection.Run
+                 & {name: detection[name] for name in detect_schema.EyeDetection.primary_key}
+                 & 'label in ("saccade", "microsaccade")').to_dicts(order_by="run_index")
+        runs = main_sequence.selected_runs(every, rows[0]["fs_hz"], params)
         earlier = (table & {name: value for name, value in key.items() if name != "session_datetime"}
                    & f"session_datetime < '{detection['session_datetime']:%Y-%m-%d %H:%M:%S}'"
                    & 'fit_status = "computed"').to_dicts()
@@ -1091,7 +1108,11 @@ def _vigor_lines(ingested_keys: set, prefix: str = DEFAULT_PREFIX) -> list[str]:
                               row["saturation_se_deg"], row["r_squared"], "") for row in earlier]
         result = vigor([run["amplitude_deg"] for run in runs], [run["peak_velocity_deg_s"] for run in runs],
                        history, params)
-        return result.reason if result.value is None else f"{result.value:.0%} ({result.n_history} earlier)"
+        text = result.reason if result.value is None else f"{result.value:.0%} ({result.n_history} earlier)"
+        # Flagged saccades are left out of the fits (design spec
+        # `2026-11-06-eye-lost-data-and-region-design.md` section 6).
+        left_out = main_sequence.flagged_left_out(every, rows[0]["fs_hz"], params)
+        return text + (f"; {left_out} flagged saccade{'' if left_out == 1 else 's'} left out" if left_out else "")
 
     lines = []
     for subject, session_datetime in sorted(ingested_keys):
@@ -2004,7 +2025,7 @@ def build_report(
     lines += [
         f"- `{entry['subject']}` @ {entry['session_datetime']:%Y-%m-%d %H:%M} — "
         f"{entry['eye']} (validity paramset {entry['validity_paramset_idx']}): "
-        f"blink {entry['blink']:.1%}, invalid {entry['invalid']:.1%} "
+        f"blink {entry['blink']:.1%}, invalid {entry['invalid']:.1%}, lost {entry['lost']:.1%} "
         f"of {entry['n_samples']} samples"
         for entry in per_eye
     ] or ["- none"]
@@ -2016,9 +2037,13 @@ def build_report(
     # "Calibration model" breakdowns are: two numbers that cannot grow
     # without bound. Both headings state their own scope, so neither can be
     # read as the other.
+    placeholder = _placeholder_region_lines(ingested_keys, prefix=prefix)
+    lines += ["", f"### Eyes masked at the placeholder screen region (24 h) — {len(placeholder)}", ""]
+    lines += placeholder or ["- none"]
+
     unusable = _unusable_fractions(validity_label_totals)
     lines += ["", "### Unusable samples, running total across every session (lower bound)", ""]
-    lines += [f"- {label}: {unusable[label]:.1%}" for label in ("blink", "invalid")]
+    lines += [f"- {label}: {unusable[label]:.1%}" for label in ("blink", "invalid", "lost")]
     # One note for the pair, placed after both: it opens on "these", and
     # both headings above it already carry the "lower bound" qualifier it
     # explains.
diff --git a/wl_preproc/eye/detect/main_sequence.py b/wl_preproc/eye/detect/main_sequence.py
index 1032ada..1a077ad 100644
--- a/wl_preproc/eye/detect/main_sequence.py
+++ b/wl_preproc/eye/detect/main_sequence.py
@@ -118,18 +118,29 @@ class Vigor:
     reason: str
 
 
-def selected(labels, amplitude_deg, n_samples, fs_hz: float, params: MainSequenceParams) -> np.ndarray:
+def selected(labels, amplitude_deg, n_samples, fs_hz: float, params: MainSequenceParams,
+             flagged=None) -> np.ndarray:
     """Which stored runs a fit takes (spec section 4.1): a `saccade` or
     `microsaccade` of at least `min_amplitude_deg`, lasting at most
     `max_duration_ms`. By size, not label: five of the seven detectors call
     every saccadic event `saccade`, whatever its size. A NULL amplitude, an
-    unmeasured run, arrives as NaN and is never taken."""
+    unmeasured run, arrives as NaN and is never taken.
+
+    **Never a flagged run** (`flagged`, one truth value per run): its peak
+    speed is its measured samples', and where a loss fell on the peak it is
+    understated (design spec `2026-11-06-eye-lost-data-and-region-design.md`
+    section 6, the requester's decision of 2026-11-06). A saccade with a
+    dropped frame inside holds lost samples, so every run a fit takes is
+    evenly sampled, and its rows are its duration."""
     amplitude = np.asarray(amplitude_deg, dtype=float)
     duration_ms = 1000.0 * np.asarray(n_samples, dtype=float) / fs_hz
+    measured = (np.ones(amplitude.shape, dtype=bool) if flagged is None
+                else ~np.asarray(flagged, dtype=bool))
     with np.errstate(invalid="ignore"):
         return (np.isin(np.asarray(labels, dtype=object), _SACCADIC)
                 & (amplitude >= params.min_amplitude_deg)
-                & (duration_ms <= params.max_duration_ms))
+                & (duration_ms <= params.max_duration_ms)
+                & measured)
 
 
 def _range(amplitude: np.ndarray) -> tuple[float | None, float | None]:
diff --git a/wl_preproc/nwb/eye_events.py b/wl_preproc/nwb/eye_events.py
index ac03e3d..308a4d8 100644
--- a/wl_preproc/nwb/eye_events.py
+++ b/wl_preproc/nwb/eye_events.py
@@ -32,6 +32,10 @@ MEASUREMENTS = {
     "end_polar_deg": "The end's polar angle, degrees counterclockwise from rightward in [0, 360) (NaN at zero "
                      "eccentricity and otherwise).",
     "reliability": "The detector's own reliability, where it reports one (NaN otherwise).",
+    "n_lost": "The lost samples inside the run, carried across: no position or none to trust, or a velocity "
+              "spanning a dropped frame (design spec 2026-11-06-eye-lost-data-and-region-design.md).",
+    "flagged": "1 when a lost sample lies inside the run or in the two samples before its start, else 0; such a "
+               "saccade's peak speed is its measured samples', and the main sequence leaves it out.",
 }
 
 
diff --git a/wl_preproc/nwb/gather.py b/wl_preproc/nwb/gather.py
index d5fac70..052a235 100644
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -22,7 +22,7 @@ from wl_preproc.nwb.trim import BlockSet
 from wl_preproc.timebase.clock import BARCODE_EPOCH, CLOCK_DISAGREEMENT_S, clock_for, reference_from  # noqa: F401
 from wl_preproc.timebase.clock import aware_utc as _aware_utc
 
-MASK_LABELS = ("blink", "invalid")
+MASK_LABELS = ("blink", "invalid", "lost")
 
 
 class Refused(Exception):
diff --git a/wl_preproc/schema/main_sequence.py b/wl_preproc/schema/main_sequence.py
index 279cc41..527b616 100644
--- a/wl_preproc/schema/main_sequence.py
+++ b/wl_preproc/schema/main_sequence.py
@@ -250,10 +250,19 @@ def selected_runs(runs: list[dict], fs_hz: float, params: MainSequenceParams) ->
         [np.nan if run["amplitude_deg"] is None else run["amplitude_deg"] for run in runs],
         [run["run_stop"] - run["run_start"] for run in runs],
         fs_hz, params,
+        flagged=[bool(run.get("flagged")) for run in runs],
     )
     return [run for run, taken in zip(runs, take, strict=True) if taken]
 
 
+def flagged_left_out(runs: list[dict], fs_hz: float, params: MainSequenceParams) -> int:
+    """How many of `runs` a fit would take but for their flag (design spec
+    `2026-11-06-eye-lost-data-and-region-design.md` section 6), for the daily
+    report to say."""
+    unflagged = [{**run, "flagged": 0} for run in runs]
+    return len(selected_runs(unflagged, fs_hz, params)) - len(selected_runs(runs, fs_hz, params))
+
+
 def register_default_paramsets() -> dict[str, int]:
     """The default `main_sequence` paramset (spec section 4.4), by name."""
     return {"default": paramset.register("main_sequence", asdict(DEFAULT_MAIN_SEQUENCE_PARAMS))}
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/cli/test_detect_report.py tests/cli/test_report.py::test_the_unusable_shares_name_the_lost_samples_beside_blink_and_invalid tests/eye/detect/test_main_sequence.py tests/nwb/test_writers.py tests/schema/test_detect_populate.py::test_a_flagged_saccade_is_left_out_of_each_fit_and_the_report_says_so -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `61 passed`

- [ ] **Step 5: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/cli tests/nwb tests/schema/test_main_sequence_populate.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `266 passed`

- [ ] **Step 6: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(report): flagged saccades are left out of the main sequence and vigor, and counted -- each NWB eye-events row carries its lost samples and its flag and the validity table its lost stretches; the daily report gives each eye's lost share, each fit's flagged saccades left out, and each eye masked at the placeholder region (sections 6 and 10)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

- [ ] **Step 7: The mutation checks,** on the commit. Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "a-flagged-saccade-is-fitted",
  "wl_preproc/eye/detect/main_sequence.py",
  "                & measured)",
  "                )",
  "tests/eye/detect/test_main_sequence.py"
 ],
 [
  "the-nwb-mask-has-no-lost",
  "wl_preproc/nwb/gather.py",
  "MASK_LABELS = (\"blink\", \"invalid\", \"lost\")",
  "MASK_LABELS = (\"blink\", \"invalid\")",
  "tests/nwb/test_writers.py"
 ],
 [
  "no-lost-share",
  "wl_preproc/cli/report.py",
  "    counts = {\"blink\": 0, \"invalid\": 0, \"lost\": 0}",
  "    counts = {\"blink\": 0, \"invalid\": 0}",
  "tests/cli/test_report.py"
 ],
 [
  "nothing-left-out-is-counted",
  "wl_preproc/schema/main_sequence.py",
  "    return len(selected_runs(unflagged, fs_hz, params)) - len(selected_runs(runs, fs_hz, params))",
  "    return 0",
  "tests/schema/test_detect_populate.py::test_a_flagged_saccade_is_left_out_of_each_fit_and_the_report_says_so"
 ]
]
```

Expected: 4 lines, each `CAUGHT`; `git status --short` shows no change.

---

### Task 6: A database declared before refuses the two stages and names their statements

§8: DataJoint adds no column to an existing table, alters no enum, and reads a table's columns once per process. `EyeValidity` refuses while it lacks its region columns or its runs the label `lost`, and names the statements and the deletion of the old default's results; `EyeDetection` refuses while its runs lack `n_lost`, `flagged` or `lost`, after true angle's own refusal. Each statement restores the columns exactly as DataJoint declares them: an enum's comment begins with its declared type. Nothing here runs them.

**Files:**
- Modify: `wl_preproc/schema/detect.py` (the two guards; `_REGION_COLUMNS`, `_LOST_COLUMNS`, `_knows_lost`, `_lacks_region`, `_lacks_lost_columns`, `_label_statement`, `_region_statements`, `_lost_statements`)
- Create: `tests/schema/test_lost_data_migration.py`

**Interfaces:**
- Consumes: Task 3's and Task 4's columns.
- Produces: `detect._region_statements() -> list[str]`, `detect._lost_statements() -> list[str]`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/schema/test_lost_data_migration.py b/tests/schema/test_lost_data_migration.py
new file mode 100644
index 0000000..7d2204b
--- /dev/null
+++ b/tests/schema/test_lost_data_migration.py
@@ -0,0 +1,89 @@
+"""A database declared before lost data and the screen region (design spec
+`2026-11-06-eye-lost-data-and-region-design.md` section 8): DataJoint adds no
+column to an existing table, alters no enum and reads a table's columns once
+per process, so `EyeValidity` and `EyeDetection` refuse before any job is
+reserved and name the statements that bring the tables to what DataJoint
+declares now."""
+
+from __future__ import annotations
+
+import pytest
+
+
+@pytest.fixture(scope="module")
+def detect_schema(dj_conn, prefix):
+    from wl_preproc.schema import detect
+
+    detect.activate(prefix=prefix)
+    return detect
+
+
+def test_a_database_declared_before_masks_nothing_and_names_the_statements_and_the_deletion(detect_schema,
+                                                                                            monkeypatch):
+    assert not detect_schema._lacks_region()
+    monkeypatch.setattr(detect_schema, "_lacks_region", lambda: True)
+
+    with pytest.raises(RuntimeError) as raised:
+        detect_schema.EyeValidity.populate()
+
+    assert all(statement in str(raised.value) for statement in detect_schema._region_statements())
+    assert "restarted" in str(raised.value)
+    assert "delete the eye_validity paramsets whose params name no max_lost_ms" in str(raised.value)
+
+
+def test_a_database_declared_before_detects_nothing_and_names_the_statements(detect_schema, monkeypatch):
+    assert not detect_schema._lacks_lost_columns()
+    monkeypatch.setattr(detect_schema, "_lacks_lost_columns", lambda: True)
+
+    with pytest.raises(RuntimeError) as raised:
+        detect_schema.EyeDetection.populate()
+
+    assert all(statement in str(raised.value) for statement in detect_schema._lost_statements())
+
+
+def test_a_label_read_without_lost_is_a_database_declared_before(detect_schema, monkeypatch):
+    from types import SimpleNamespace
+
+    label = detect_schema.EyeDetection.Run.heading.attributes["label"]
+    old = SimpleNamespace(names=detect_schema.EyeDetection.Run.heading.names,
+                          attributes={"label": SimpleNamespace(type=label.type.replace(",'lost'", ""))})
+    monkeypatch.setattr(detect_schema.EyeDetection.Run, "_heading", old)
+
+    assert detect_schema._lacks_lost_columns()
+
+
+@pytest.mark.parametrize(("table", "columns", "statements"), [
+    ("EyeValidity", "_REGION_COLUMNS", "_region_statements"),
+    ("EyeDetection", "_LOST_COLUMNS", "_lost_statements"),
+])
+def test_the_statements_restore_the_tables_as_datajoint_declares_them(detect_schema, table, columns, statements):
+    """Each table copied, taken back to what a database declared before held
+    (the new columns dropped, the label without `lost`), and brought forward
+    by the named statements: type, nullability, default, comment and place
+    match what declaring them wrote, and no row of the tables is touched."""
+    import datajoint as dj
+
+    master = getattr(detect_schema, table)
+    added = master if table == "EyeValidity" else master.Run
+    names = {added.full_table_name: added.full_table_name[:-1] + '_restore_check`',
+             master.Run.full_table_name: master.Run.full_table_name[:-1] + '_restore_label`'}
+
+    def described(name):
+        return [row[:2] + row[3:6] + row[8:9] for row in dj.conn().query(f"SHOW FULL COLUMNS FROM {name}").fetchall()]
+
+    old_enum = master.Run.heading.attributes["label"].type.replace(",'lost'", "")
+    for original, copy in names.items():
+        dj.conn().query(f"CREATE TABLE {copy} LIKE {original}")
+    try:
+        dj.conn().query(f"ALTER TABLE {names[added.full_table_name]} "
+                        + ", ".join(f"DROP COLUMN {column}" for column in getattr(detect_schema, columns)))
+        dj.conn().query(f"ALTER TABLE {names[master.Run.full_table_name]} MODIFY COLUMN label {old_enum} NOT NULL")
+        for statement in getattr(detect_schema, statements)():
+            for original, copy in names.items():
+                statement = statement.replace(original, copy)
+            dj.conn().query(statement)
+        restored = {original: described(copy) for original, copy in names.items()}
+    finally:
+        for copy in names.values():
+            dj.conn().query(f"DROP TABLE IF EXISTS {copy}")
+    assert restored == {original: described(original) for original in names}
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/schema/test_lost_data_migration.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `5 failed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/schema/detect.py b/wl_preproc/schema/detect.py
index f949cf9..2991af2 100644
--- a/wl_preproc/schema/detect.py
+++ b/wl_preproc/schema/detect.py
@@ -279,6 +279,15 @@ class EyeValidity(dj.Computed):
                 "database declared before 2026-10-11. Nothing is masked until it is altered and the "
                 f"daemon restarted: {_feature_lost_statement()}"
             )
+        if _lacks_region():
+            raise RuntimeError(
+                f"{self.full_table_name} has no columns {', '.join(_REGION_COLUMNS)}, or its runs no label "
+                "`lost`, as read by this process: a database declared before 2026-11-06. Nothing is masked until "
+                f"it is altered and the daemon restarted: {'; '.join(_region_statements())}; then delete the "
+                "eye_validity paramsets whose params name no max_lost_ms, with what was computed from them: "
+                "registered before lost data was carried, they mask as they did and otherwise keep running beside "
+                "the new default (design spec 2026-11-06-eye-lost-data-and-region-design.md section 8)."
+            )
         return super().populate(*args, **kwargs)
 
     def make(self, key: dict) -> None:
@@ -588,6 +597,13 @@ class EyeDetection(dj.Computed):
                 "mean per axis and otherwise keep running beside the true-angle defaults (design spec "
                 "2026-10-30-true-angle-saccades-design.md, amendment 5)."
             )
+        if _lacks_lost_columns():
+            raise RuntimeError(
+                f"{self.Run.full_table_name} has no columns {', '.join(_LOST_COLUMNS)}, or no label `lost`, as "
+                "read by this process: a database declared before 2026-11-06. Nothing is detected until it is "
+                f"altered and the daemon restarted: {'; '.join(_lost_statements())} (design spec "
+                "2026-11-06-eye-lost-data-and-region-design.md section 8)."
+            )
         return super().populate(*args, **kwargs)
 
     def make(self, key: dict) -> None:
@@ -1132,6 +1148,67 @@ def _polar_statement() -> str:
         for column, previous in zip(_POLAR_COLUMNS, after, strict=True))
 
 
+#: `EyeValidity`'s columns of 2026-11-06, after `frac_glitch_repaired` in
+#: order, and `EyeDetection.Run`'s, after `end_polar_deg`, each with its type
+#: as DataJoint declares it (design spec
+#: `2026-11-06-eye-lost-data-and-region-design.md` section 8).
+_REGION_COLUMNS = ("region_half_width_deg", "region_half_height_deg", "region_from_setup")
+_REGION_TYPES = ("double", "double", "tinyint(1)")
+_LOST_COLUMNS = ("n_lost", "flagged")
+_LOST_TYPES = ("int unsigned", "tinyint(1)")
+
+
+def _knows_lost(table) -> bool:
+    """`table.Run`'s `label`, as this process reads it, has the value `lost`:
+    an enum DataJoint never alters on an existing table."""
+    return "'lost'" in table.Run.heading.attributes["label"].type
+
+
+def _lacks_region() -> bool:
+    """`EyeValidity`, as this process reads it, lacks one of
+    `_REGION_COLUMNS`, or its runs the label `lost`: a database declared
+    before 2026-11-06. DataJoint reads a table's columns once per process
+    (`_lacks_feature_lost`)."""
+    return not set(_REGION_COLUMNS) <= set(EyeValidity.heading.names) or not _knows_lost(EyeValidity)
+
+
+def _lacks_lost_columns() -> bool:
+    """`EyeDetection.Run`, as this process reads it, lacks one of
+    `_LOST_COLUMNS` or the label `lost` (`_lacks_region`)."""
+    return not set(_LOST_COLUMNS) <= set(EyeDetection.Run.heading.names) or not _knows_lost(EyeDetection)
+
+
+def _label_statement(table) -> str:
+    """The statement that gives `table.Run`'s `label` the value `lost`, as
+    DataJoint declares the column: its comment begins with its declared type,
+    `:enum(...):`, each quote doubled inside the SQL literal."""
+    comment = f":enum({_LABEL_ENUM}):".replace("'", "''")
+    return (f"ALTER TABLE {table.Run.full_table_name} MODIFY COLUMN label enum({_LABEL_ENUM}) NOT NULL "
+            f"COMMENT '{comment}'")
+
+
+def _added(table_name: str, columns, types, after: str) -> str:
+    previous = (after, *columns[:-1])
+    return f"ALTER TABLE {table_name} " + ", ".join(
+        f"ADD COLUMN {column} {kind} DEFAULT NULL COMMENT '' AFTER {before}"
+        for column, kind, before in zip(columns, types, previous, strict=True))
+
+
+def _region_statements() -> list[str]:
+    """The statements that bring an `EyeValidity` declared before 2026-11-06
+    to what DataJoint declares now: its region columns, nullable, no
+    comment, after `frac_glitch_repaired`; and its runs' label `lost`."""
+    return [_added(EyeValidity.full_table_name, _REGION_COLUMNS, _REGION_TYPES, "frac_glitch_repaired"),
+            _label_statement(EyeValidity)]
+
+
+def _lost_statements() -> list[str]:
+    """`_region_statements`' for `EyeDetection.Run`: its lost-sample count and
+    flag after `end_polar_deg`, and its label `lost`."""
+    return [_added(EyeDetection.Run.full_table_name, _LOST_COLUMNS, _LOST_TYPES, "end_polar_deg"),
+            _label_statement(EyeDetection)]
+
+
 def _measured_from_takeoff(detector, trace: str, label: Label, reliability: float | None) -> bool:
     """Whether a stored run is measured from its take-off sample: BMD's
     take-off rule (design spec `2026-09-27-bmd-design.md` section 3.5), per
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/schema/test_lost_data_migration.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `5 passed`

- [ ] **Step 5: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/schema/test_eye_loss_populate.py tests/schema/test_guardrails.py tests/schema/test_lost_data_migration.py tests/schema/test_true_angle_detection.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `40 passed`

- [ ] **Step 6: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(detect): a database declared before lost data and the region refuses the mask and the detection and names their statements -- the region columns and the lost count and flag added, the label enum given lost, each as DataJoint declares it, and the old default's results to delete (section 8)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

- [ ] **Step 7: The mutation checks,** on the commit. Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "the-mask-is-not-guarded",
  "wl_preproc/schema/detect.py",
  "        if _lacks_region():",
  "        if False:",
  "tests/schema/test_lost_data_migration.py"
 ],
 [
  "detection-is-not-guarded",
  "wl_preproc/schema/detect.py",
  "        if _lacks_lost_columns():",
  "        if False:",
  "tests/schema/test_lost_data_migration.py"
 ],
 [
  "the-enums-comment-is-plain",
  "wl_preproc/schema/detect.py",
  "    comment = f\":enum({_LABEL_ENUM}):\".replace(\"'\", \"''\")",
  "    comment = \"\"",
  "tests/schema/test_lost_data_migration.py"
 ],
 [
  "the-label-is-not-checked",
  "wl_preproc/schema/detect.py",
  "    return not set(_LOST_COLUMNS) <= set(EyeDetection.Run.heading.names) or not _knows_lost(EyeDetection)",
  "    return not set(_LOST_COLUMNS) <= set(EyeDetection.Run.heading.names)",
  "tests/schema/test_lost_data_migration.py"
 ],
 [
  "the-deletion-is-not-named",
  "wl_preproc/schema/detect.py",
  "\"eye_validity paramsets whose params name no max_lost_ms, with what was computed from them: \"",
  "\"eye_validity paramsets, with what was computed from them: \"",
  "tests/schema/test_lost_data_migration.py"
 ]
]
```

Expected: 5 lines, each `CAUGHT`; `git status --short` shows no change.

---

### Task 7: v3 of the saccade detector's contract fixtures

§9, amendment 10: v2's traces, read from v2, and five of the fixture's own, each v1's 8-degree step with a loss or a blink inside it or at its landing; a sample with no position is read by `contract_fixtures.detector_input`, the fixture's rule in the mask's terms. Written by its command, never edited once published; a test rebuilds it and compares it with the file.

**Files:**
- Modify: `wl_preproc/eye/detect/contract_fixtures.py` (`_V3_TRACES`, `MAX_LOST_MS`, `detector_input`, `build_v3`, the command's `v3`), `wl.yaml` (`saccade-detector-fixtures`)
- Create: `docs/fixtures/saccade/v3.json` (by its command)
- Test: `tests/eye/detect/test_contract_fixtures.py`

**Interfaces:**
- Consumes: Task 1's `_dilate`, `_uncarried`, `carried_frames`; Task 2's Engbert-Kliegl; Task 3's
  `measure_event_run`.
- Produces: `docs/fixtures/saccade/v3.json`; `contract_fixtures.detector_input(gaze_deg, available, fs_hz,
  max_lost_ms) -> (gaze, offered)`, `build_v3(v2, v2_sha256)`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/eye/detect/test_contract_fixtures.py b/tests/eye/detect/test_contract_fixtures.py
index 6d2dfec..5548bd5 100644
--- a/tests/eye/detect/test_contract_fixtures.py
+++ b/tests/eye/detect/test_contract_fixtures.py
@@ -269,3 +269,99 @@ def test_in_v2_the_step_across_the_cut_is_a_microsaccade():
     (v2_case,) = [case for case in _v2()["traces"] if case["name"] == "off-centre-30-across-the-cut"]
     assert [run["label"] for run in v1_case["runs"]] == ["saccade"]
     assert [run["label"] for run in v2_case["runs"]] == ["microsaccade"]
+
+
+# v3 (design spec `2026-11-06-eye-lost-data-and-region-design.md` section 9):
+# a run carried across a short loss of data, the requester's decision of
+# 2026-11-06, which wl-xcon's online detector follows.
+V3 = V1.with_name("v3.json")
+V3_SHA256 = "4f6301413431bb4483b1a89ce41c17c344319390780aa4991de29b73db640f35"
+
+
+def _v3() -> dict:
+    return json.loads(V3.read_text(encoding="utf-8"))
+
+
+def test_the_published_v3_is_never_edited():
+    assert hashlib.sha256(V3.read_bytes()).hexdigest() == V3_SHA256
+
+
+def test_v3_says_how_a_loss_of_data_is_carried():
+    about = _v3()["about"]
+    assert '"lost" where the sample has no position' in about
+    assert "continues a run open before it and never opens one" in about
+    assert "floor(max_lost_ms * fs / 1000 + 1e-9)" in about
+    assert "Each maximal stretch of unknown velocities is judged whole" in about
+    assert "nor among the two at each end of the trace" in about
+    assert "flagged: a sample of a carried stretch" in about
+    assert _V2_TOLERANCE in about
+
+
+def test_v3_names_its_source_and_its_bound():
+    fixture = _v3()
+    assert (fixture["version"], fixture["geometry"]) == (3, TRUE_ANGLE)
+    assert fixture["derived_from"] == {"v2.json": V2_SHA256}
+    assert fixture["defaults"] == {**_v2()["defaults"], "max_lost_ms": 50.0}
+
+
+def test_v3s_traces_from_v2_keep_v2s_runs_measured_throughout():
+    v2 = {case["name"]: case for case in _v2()["traces"]}
+    for case in _v3()["traces"]:
+        if case["inputs_from"] == "v2.json":
+            source = v2[case["name"]]
+            assert (case["gaze_deg"], case["available"]) == (source["gaze_deg"], source["available"]), case["name"]
+            assert [(run["start"], run["stop"], run["label"], run["flagged"], run["n_lost"]) for run in case["runs"]] == [
+                (run["start"], run["stop"], run["label"], False, 0) for run in source["runs"]], case["name"]
+    assert [case["name"] for case in _v3()["traces"] if case["inputs_from"] == "v2.json"] == list(v2)
+
+
+def test_a_reader_of_v3_gets_its_outputs_back_from_its_inputs():
+    """What wl-xcon's test will do, from the file alone."""
+    from wl_preproc.eye.detect.contract_fixtures import detector_input
+    from wl_preproc.eye.detect.measure import measure_event_run
+    from wl_preproc.eye.detect.velocity import local_velocity
+
+    fixture = _v3()
+    fs_hz = fixture["fs_hz"]
+    for case in fixture["traces"]:
+        gaze, offered = detector_input(case["gaze_deg"], case["available"], fs_hz, fixture["defaults"]["max_lost_ms"])
+        speed = local_velocity(gaze, fs_hz)
+        known = np.array([entry is None for entry in offered])
+        want = case["velocity_deg_s"]
+        assert all(_close(speed[n] if known[n] else None, want[n]) for n in range(len(want))), case["name"]
+        assert all(_close(got, want) for got, want in zip(thresholds(speed, known, _V2_PARAMS), case["eta_deg_s"],
+                                                          strict=True)), case["name"]
+        runs = detect_engbert_kliegl(gaze, speed, offered, fs_hz, _V2_PARAMS)
+        assert [(r.start, r.stop, r.label.value) for r in runs] == [
+            (run["start"], run["stop"], run["label"]) for run in case["runs"]], case["name"]
+        for r, run in zip(runs, case["runs"], strict=True):
+            got = measure_event_run(gaze, speed, offered, r.start, r.stop, fs_hz, geometry=TRUE_ANGLE,
+                                    runs_end_before_landing=False, min_measured_ms=None)
+            for field in ("amplitude_deg", "peak_velocity_deg_s", "direction_deg", "start_eccentricity_deg",
+                          "start_polar_deg", "end_eccentricity_deg", "end_polar_deg"):
+                assert _close(getattr(got, field), run[field]), (case["name"], r.start, field)
+
+
+def test_v3s_own_traces_carry_a_short_loss_end_at_a_blink_and_at_a_long_loss():
+    traces = {case["name"]: case for case in _v3()["traces"]}
+    (whole,) = traces["centre-horizontal"]["runs"]
+
+    def saccades(name):
+        return [(run["start"], run["stop"], run["flagged"], run["n_lost"]) for run in traces[name]["runs"]
+                if run["label"] == "saccade"]
+
+    assert saccades("a-lost-sample-inside-a-saccade") == [(whole["start"], whole["stop"], True, 5)]
+    assert all(run["stop"] <= 205 or run["start"] >= 207 for run in traces["a-blink-inside-a-saccade"]["runs"])
+    ((start, stop, flagged, _),) = saccades("a-long-loss-inside-a-saccade")
+    assert (start, flagged) == (whole["start"], False) and stop <= 207
+    assert saccades("lost-samples-alone") == [(whole["start"], whole["stop"], False, 0)]
+    (landing,) = [run for run in traces["a-short-loss-at-a-saccades-landing"]["runs"] if run["label"] == "saccade"]
+    assert (landing["start"], landing["flagged"]) == (whole["start"], True) and landing["stop"] > whole["stop"]
+    assert abs(landing["amplitude_deg"] - whole["amplitude_deg"]) < 0.1
+
+
+def test_v3_is_what_this_code_makes_of_v2():
+    """The file is this code's output, the builder's every rule included."""
+    from wl_preproc.eye.detect.contract_fixtures import build_v3, dumps
+
+    assert dumps(build_v3(_v2(), V2_SHA256)) == V3.read_text(encoding="utf-8")
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/eye/detect/test_contract_fixtures.py -q -p no:cacheprovider
```

Expected: `7 failed, 16 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl.yaml b/wl.yaml
index 5f6f231..c94c567 100644
--- a/wl.yaml
+++ b/wl.yaml
@@ -653,8 +653,10 @@ publishes:
       v1.json and its companion v1-thresholds.json are per axis; v2.json is
       true visual angle (the local-frame velocity, the complete-window
       scale, and each interval's size, peak speed, direction and endpoints'
-      eccentricity and polar angle). Versioned, and never
-      edited once published. wl-xcon's online detector mirrors this one, and
+      eccentricity and polar angle); v3.json carries a run across a short
+      loss of data, a sample with no position, flagged, as wl-xcon's online
+      detector does (design spec 2026-11-06-eye-lost-data-and-region-design.md).
+      Versioned, and never edited once published. wl-xcon's online detector mirrors this one, and
       its contract tests read these files rather than import this package,
       so a change of behaviour reaches it as a new version beside the old
       (its XC-398).
diff --git a/wl_preproc/eye/detect/contract_fixtures.py b/wl_preproc/eye/detect/contract_fixtures.py
index a39cfb9..ed6f29a 100644
--- a/wl_preproc/eye/detect/contract_fixtures.py
+++ b/wl_preproc/eye/detect/contract_fixtures.py
@@ -26,7 +26,14 @@ short trace that pins the threshold's scale over complete windows, each with
 what this code makes of it under the `true-angle` setting -- the local-frame
 velocity, the thresholds, the runs and their measurements.
 
-`python -m wl_preproc.eye.detect.contract_fixtures {v1,v1-thresholds,v2}
+v3 carries a run across a short loss of data (design spec
+`2026-11-06-eye-lost-data-and-region-design.md` section 9, the requester's
+decision of 2026-11-06, which wl-xcon's online detector follows): v2's traces,
+read from v2, and five of its own, each v1's `centre-horizontal` step with a
+loss or a blink inside it or at its landing. A sample may have no position; `detector_input` is
+the rule that reads such a trace.
+
+`python -m wl_preproc.eye.detect.contract_fixtures {v1,v1-thresholds,v2,v3}
 [--out DIR]` wrote each, and refuses to write over a published file;
 `tests/eye/detect/test_contract_fixtures.py` pins each by its digest and holds
 this code to every output in it. v1's inputs are seeded numpy draws, which
@@ -50,8 +57,9 @@ from wl_preproc.eye.detect.engbert_kliegl import (
 )
 from wl_preproc.eye.detect.geometry import PER_AXIS, TRUE_ANGLE
 from wl_preproc.eye.detect.labels import Label
-from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude, measure
-from wl_preproc.eye.detect.velocity import local_velocity, velocity
+from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG, amplitude, measure, measure_event_run
+from wl_preproc.eye.detect.validity import _dilate, _uncarried, carried_frames
+from wl_preproc.eye.detect.velocity import _HALF_WINDOW, local_velocity, velocity
 
 VERSION = 1
 #: Both sides' rate: the synthetic tracker's (`synth/ohdpi.py::OHDPI_FPS`) and wl-xcon's contract test's.
@@ -281,6 +289,138 @@ def build_v2(v1: dict, v1_sha256: str, companion: dict, companion_sha256: str) -
     }
 
 
+#: v3's own traces: v1's `centre-horizontal` 8-degree step, from sample 200
+#: over 12, with `(first, stop)` samples having no position, or masked.
+_V3_TRACES = (
+    ("a-lost-sample-inside-a-saccade",
+     "v1's centre-horizontal step with sample 206 having no position: one saccade, carried across it and flagged",
+     (206, 207), None),
+    ("a-short-loss-at-a-saccades-landing",
+     "v1's centre-horizontal step with samples 209-213, where it lands, having no position: the run is carried "
+     "across the loss and ends at the first known velocity after it, its size reaching the landing, and flagged",
+     (209, 214), None),
+    ("a-blink-inside-a-saccade",
+     "v1's centre-horizontal step with samples 205-206 masked as a blink: no run crosses it",
+     None, (205, 207, Label.BLINK)),
+    ("a-long-loss-inside-a-saccade",
+     "v1's centre-horizontal step with samples 209-245 having no position, 74 ms, too long to carry: the run ends "
+     "at the last known velocity before it, kept as long enough, and unflagged, as a run ending at a blink is",
+     (209, 246), None),
+    ("lost-samples-alone",
+     "v1's centre-horizontal step with samples 100-104, in fixation, having no position: they make no event",
+     (100, 105), None),
+)
+MAX_LOST_MS = 50.0
+
+
+def detector_input(gaze_deg: list, available: list, fs_hz: float, max_lost_ms: float) -> tuple[np.ndarray, np.ndarray]:
+    """A v3 trace as Engbert-Kliegl is handed it: `(gaze, offered)`.
+
+    A sample with no position (`"lost"`, its gaze null) makes the velocity of
+    every sample whose window, `n-2..n+2`, holds it unknown: `Label.LOST`,
+    carried by a run. Each maximal stretch of unknown velocities is judged
+    whole, as the validity mask judges its own (`validity._uncarried`): it is
+    withheld, `Label.INVALID`, where a sample beside it is withheld, beyond
+    the trace or one of the two at each end, whose velocities are zero-filled,
+    or where two samples with positions, taken in turn across it,
+    are more than `max_lost_ms` apart in whole samples. So two losses within
+    two samples of each other are one stretch, and a loss within two samples
+    of the trace's edge is withheld. Positions it lacks are filled by the straight
+    line between those either side, for the velocity's arithmetic alone:
+    no filled position is a run's end, and no unknown velocity a peak."""
+    n = len(gaze_deg)
+    missing = np.array([position is None for position in gaze_deg], dtype=bool)
+    withheld = np.array([entry not in (None, "lost") for entry in available], dtype=bool)
+    lost = _dilate(missing, _HALF_WINDOW) & ~withheld
+    uncarried = _uncarried(lost, withheld, missing, np.arange(n), carried_frames(max_lost_ms, fs_hz))
+    known = np.flatnonzero(~missing)
+    gaze = np.array([[np.nan, np.nan] if position is None else position for position in gaze_deg], dtype=float)
+    for axis in (0, 1):
+        gaze[missing, axis] = np.interp(np.flatnonzero(missing), known, gaze[known, axis])
+    offered = np.array([Label(entry) if entry not in (None, "lost") else None for entry in available], dtype=object)
+    offered[lost & ~uncarried] = Label.LOST
+    offered[uncarried] = Label.INVALID
+    return gaze, offered
+
+
+def build_v3(v2: dict, v2_sha256: str) -> dict:
+    """v3, a run carried across a short loss of data: v2's traces, read from
+    v2, and `_V3_TRACES`, each with what this code makes of it."""
+    fs_hz = v2["fs_hz"]
+    params = dataclasses.replace(DEFAULT_EK_PARAMS, geometry=TRUE_ANGLE)
+    cases = [(case["name"], case["note"], "v2.json", case["gaze_deg"], case["available"]) for case in v2["traces"]]
+    (centre,) = [case for case in v2["traces"] if case["name"] == "centre-horizontal"]
+    for name, note, missing, masked in _V3_TRACES:
+        gaze_deg, mask = [list(position) for position in centre["gaze_deg"]], list(centre["available"])
+        if missing is not None:
+            for index in range(*missing):
+                gaze_deg[index], mask[index] = None, "lost"
+        if masked is not None:
+            first, stop, label = masked
+            mask[first:stop] = [label.value] * (stop - first)
+        cases.append((name, note, "v3", gaze_deg, mask))
+    traces = []
+    for name, note, source, gaze_deg, mask in cases:
+        gaze, offered = detector_input(gaze_deg, mask, fs_hz, MAX_LOST_MS)
+        speed = local_velocity(gaze, fs_hz)
+        known = np.array([entry is None for entry in offered])
+        lost = np.array([entry is Label.LOST for entry in offered])
+        runs = detect_engbert_kliegl(gaze, speed, offered, fs_hz, params)
+        traces.append({
+            "name": name, "note": note, "inputs_from": source, "gaze_deg": gaze_deg, "available": mask,
+            "velocity_deg_s": [speed[n].tolist() if known[n] else None for n in range(len(gaze_deg))],
+            "eta_deg_s": list(thresholds(speed, known, params)),
+            "runs": [{"start": int(run.start), "stop": int(run.stop), "label": run.label.value,
+                      "flagged": bool(lost[max(run.start - 2, 0):run.stop].any()),
+                      "n_lost": int(lost[run.start:run.stop].sum()),
+                      **{field: getattr(measure_event_run(gaze, speed, offered, run.start, run.stop, fs_hz,
+                                                          geometry=TRUE_ANGLE, runs_end_before_landing=False,
+                                                          min_measured_ms=None), field)
+                         for field in _V2_MEASUREMENTS}} for run in runs],
+        })
+    return {
+        "version": 3,
+        "geometry": TRUE_ANGLE,
+        "derived_from": {"v2.json": v2_sha256},
+        "about": "wl-preproc's Engbert-Kliegl saccade detector in true visual angle, carrying a run across a short "
+                 "loss of data (design spec 2026-11-06-eye-lost-data-and-region-design.md): v2's traces, read from "
+                 "v2.json, and five of its own, each v1's centre-horizontal step with a loss or a blink inside it "
+                 "or at its landing. "
+                 "Gaze is in degrees per axis, null where the sample has no position; a position (x, y) is the "
+                 "direction normalize(tan x, tan y, 1). available: null where a detector may label, \"lost\" where "
+                 "the sample has no position, a loss of data a run is carried across, else the label the mask gave, "
+                 "which withholds the sample. A velocity is unknown where its window, samples n-2..n+2, holds a "
+                 "sample with no position; velocity_deg_s is null there, and otherwise as in v2. Each maximal "
+                 "stretch of unknown velocities is judged whole: it is carried when the samples either side of it "
+                 "have known velocities, neither withheld nor among the two at each end of the trace, whose "
+                 "velocities (0 in velocity_deg_s) are not measured, and no two samples with positions, taken in "
+                 "turn from the one before it to "
+                 "the one after it, are more than max_lost_ms apart, inclusive, in whole samples, "
+                 "floor(max_lost_ms * fs / 1000 + 1e-9); otherwise, beside a withheld sample or the trace's edge, "
+                 "or holding a longer loss, it is withheld as a mask label is. So two losses within two samples of "
+                 "each other are one stretch, and a loss within two samples of the trace's edge is withheld. "
+                 "eta_deg_s is as in v2, over complete windows of known velocities. A sample with a "
+                 "known velocity is in an event when (vx/eta_x)^2 + (vy/eta_y)^2 > 1, and with either eta 0 none "
+                 "is; a sample whose velocity is unknown continues a run open before it and never opens one; a "
+                 "withheld sample never is in an event. A run is a maximal stretch of such samples from a known "
+                 "velocity over the threshold; where it reaches a carried stretch it goes on through it, and where "
+                 "the first known velocity after that stretch is not over the threshold the run ends there, that "
+                 "sample its last, the eye having landed in the loss; at a withheld stretch or the trace's end it "
+                 "ends at its last known velocity over the threshold. A run is dropped when its known velocities "
+                 "over the threshold number fewer than min_duration_samples. flagged: a sample of a carried stretch, its "
+                 "velocity unknown, lies inside the run or in the two samples before its start; n_lost: the samples "
+                 "of a carried stretch inside it. Size, direction, "
+                 "positions and polar angles are as in v2, from the run's first and last samples; peak speed is "
+                 "the largest |velocity| over its known velocities. Intervals, labels, flags and counts are exact; "
+                 "every other number is held to |got - want| <= 1e-9 + 1e-12 * |want|, in its own unit. "
+                 "inputs_from names the file a trace's inputs were read from. A run is [start, stop) in samples. "
+                 "Never edited.",
+        "fs_hz": fs_hz,
+        "defaults": {**v2["defaults"], "max_lost_ms": MAX_LOST_MS},
+        "traces": traces,
+    }
+
+
 def dumps(fixture: dict) -> str:
     """The file's text: one top-level field, or one case, per line, so a
     reader can see its shape and a diff its cases."""
@@ -296,7 +436,7 @@ def dumps(fixture: dict) -> str:
 
 def main(argv: list[str] | None = None) -> int:
     parser = argparse.ArgumentParser(description="write one of the saccade detector's contract fixtures")
-    parser.add_argument("which", choices=("v1", "v1-thresholds", "v2"))
+    parser.add_argument("which", choices=("v1", "v1-thresholds", "v2", "v3"))
     parser.add_argument("--out", type=Path, default=Path("docs/fixtures/saccade"))
     args = parser.parse_args(argv)
     path = args.out / f"{args.which}.json"
@@ -307,6 +447,9 @@ def main(argv: list[str] | None = None) -> int:
     elif args.which == "v1-thresholds":
         raw = (args.out / "v1.json").read_bytes()
         text = dumps(build_v1_thresholds(json.loads(raw), hashlib.sha256(raw).hexdigest()))
+    elif args.which == "v3":
+        raw = (args.out / "v2.json").read_bytes()
+        text = dumps(build_v3(json.loads(raw), hashlib.sha256(raw).hexdigest()))
     else:
         raw, companion = (args.out / "v1.json").read_bytes(), (args.out / "v1-thresholds.json").read_bytes()
         text = dumps(build_v2(json.loads(raw), hashlib.sha256(raw).hexdigest(), json.loads(companion),
PATCH
```

- [ ] **Step 4: Write v3.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m wl_preproc.eye.detect.contract_fixtures v3
shasum -a 256 docs/fixtures/saccade/v3.json
```

Expected: `docs/fixtures/saccade/v3.json`, then `4f6301413431bb4483b1a89ce41c17c344319390780aa4991de29b73db640f35`, the digest Step 1's test pins.

- [ ] **Step 5: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/eye/detect/test_contract_fixtures.py -q -p no:cacheprovider
```

Expected: `23 passed`

- [ ] **Step 6: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/eye/detect/test_contract_fixtures.py tests/eye/detect/test_engbert_kliegl.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `48 passed`

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc docs wl.yaml
git commit -F - <<'MSG'
feat(eye): v3 of the saccade detector's contract fixtures, for wl-xcon -- v2's traces and five of its own, a loss or a blink inside v1's 8-degree step or at its landing, a sample with no position read by contract_fixtures.detector_input (section 9)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

- [ ] **Step 8: The mutation checks,** on the commit. Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "only-the-missing-sample-is-unknown",
  "wl_preproc/eye/detect/contract_fixtures.py",
  "    lost = _dilate(missing, _HALF_WINDOW) & ~withheld",
  "    lost = missing & ~withheld",
  "tests/eye/detect/test_contract_fixtures.py"
 ],
 [
  "no-run-is-flagged",
  "wl_preproc/eye/detect/contract_fixtures.py",
  "                      \"flagged\": bool(lost[max(run.start - 2, 0):run.stop].any()),",
  "                      \"flagged\": False,",
  "tests/eye/detect/test_contract_fixtures.py"
 ],
 [
  "a-long-loss-is-carried",
  "wl_preproc/eye/detect/contract_fixtures.py",
  "    uncarried = _uncarried(lost, withheld, missing, np.arange(n), carried_frames(max_lost_ms, fs_hz))",
  "    uncarried = _uncarried(lost, withheld, missing, np.arange(n), 10**9)",
  "tests/eye/detect/test_contract_fixtures.py"
 ]
]
```

Expected: 3 lines, each `CAUGHT`; `git status --short` shows no change.

---

### Task 8: The records

The spec's amendments 1-10, made while proving this plan; the three earlier specs' dated amendments; wl-preproc's
answer in `HANDOVER-wl-expcontroller.md` and wl-sync's answer to item 6 in `HANDOVER-wl-sync.md`; this piece's
handoff; and the checkpoint's and `wl.yaml`'s lines (spec §13). Then the full suite, once, on both interpreters.

**Files:**
- Modify: `docs/superpowers/specs/2026-11-06-eye-lost-data-and-region-design.md`,
  `docs/superpowers/specs/2026-08-31-saccade-detection-design.md`, `2026-10-11-blink-padding-design.md`,
  `2026-10-11-eye-loss-design.md`, `HANDOVER-wl-expcontroller.md`, `HANDOVER-wl-sync.md`, `docs/CHECKPOINT.md`,
  `wl.yaml`
- Create: `docs/handoffs/2026-11-06-eye-lost-data-and-region.md`

**Interfaces:** none; documents only.

- [ ] **Step 1: Apply the records.** Save this script outside the repository as `records.py` and run it from
  the repository root. It writes the handoff with `{{...}}` slots in its §5, filled in Step 4.

```python
"""Task 8 of the proof: the records. Run from the worktree's root, after the branch carries main's d48943c."""
import pathlib


def edit(path, old, new):
    p = pathlib.Path(path)
    t = p.read_text()
    assert t.count(old) == 1, (path, old[:70], t.count(old))
    p.write_text(t.replace(old, new))


def append(path, text):
    p = pathlib.Path(path)
    p.write_text(p.read_text().rstrip("\n") + "\n" + text)


SPEC = "docs/superpowers/specs/2026-11-06-eye-lost-data-and-region-design.md"
append(SPEC, """
## Amendments, 2026-11-06, made while proving the plan

1. **The mask applies the bound** (§3): a lost stretch no run can be carried across, longer than `max_lost_ms` or
   without a measured sample on a side, is stored `invalid`, so every detector meets exactly what is stored:
   measured, `lost` (carried), or withheld. Its criteria's fractions still say what it was.
2. **The bound is the loss of data, from the last position measured to the next** (§3; wl-xcon's check of
   2026-11-06, its R10, which its detector already did): a dropped frame's neighbours have measured positions,
   though their velocities span it, so one dropped frame is `1 + n_missing` frames, not `5 + n_missing`. A lost
   reflection's, a value's that is not a number, an impossible speed's and a filled glitch's positions, and their
   margins', are not measurements. Each stretch of lost samples is judged whole, so two losses within two samples
   of each other are one stretch (wl-xcon's question of 2026-11-06). The two samples at each end of the
   recording, whose velocities are zero-filled, are no measured sample beside a stretch, so a loss beside them is
   withheld (both sessions, 2026-11-07). v3's `about` says both.
3. **A paramset stored before** names no bound, no margin and no setup, and loads with 0, 0 and false: it masks as
   it always did, every criterion withheld as `invalid` with its margin, a frame gap's included, at its own
   region (§8).
4. **What a detector is offered over a carried loss** (§3): every lost sample's position is filled along frame
   time by the straight line between the samples either side, except a row whose velocity spans a dropped frame,
   whose position is a measurement; the velocities are the filled gaze's (`validity.fill_lost`).
5. **No duration by frame time** (§6): a saccade with a dropped frame inside holds lost samples and is flagged, so
   every saccade a fit takes is evenly sampled and its rows are its duration.
6. **A short loss at a run's end is carried to the landing** (§4, §5.2; found by wl-xcon's review of its own
   detector on 2026-11-06, and agreed by both): trimming a run back to its last measured velocity cut a saccade
   that landed inside a short loss down to a fraction of its size, unflagged (an 8-degree saccade reported as
   0.45 degrees). Engbert-Kliegl carries a run through a carried loss, and where the first measured velocity
   after it is not over the threshold the run ends there, that sample its last, so its size reaches the
   landing. The other six detectors' runs keep their lost ends, and `measure.measure_event_run` measures such a
   run out to the measured samples beyond them. A run ends at its last measured velocity over the threshold only
   at a withheld stretch, a long loss among them, or the trace's end. An unknown velocity still never opens a run.
7. **The flag is judged on the run as stored** (§5.4; wl-xcon's question of 2026-11-06): a lost sample inside it
   or in the two samples before its start, clipped to the trace. A run ended by a withheld stretch is unflagged,
   as a run ending at a blink is, and the two zero-filled velocities at each edge are not lost. On the
   conjunction, either eye's lost samples count.
8. **The field is kept in a table of its own, `core.SessionSetup`** (§7, §8), not in columns of `SessionRecord`,
   so the event stage needs no `ALTER TABLE`. **The frame is cyclopean** (wl-xcon, 2026-11-06: each eye's map is
   fitted to the one cyclopean target, its stereoscope calibration task not yet built), so §7's ruling stands
   with no offset term.
9. **The NWB columns are `n_lost` and `flagged`**, the stored names (§10). **The report** gives each eye's lost
   share beside its blink and invalid shares, each detector's flagged saccades left out of its fit on its vigor
   line, and each eye masked at the placeholder.
10. **v3's `lost` is a sample with no position** (§9), as wl-xcon's detector represents a lost datagram: a
    velocity is unknown where its window holds one, and `contract_fixtures.detector_input` is the rule reading
    such a trace into the mask's terms. Its five traces: one lost sample inside v1's 8-degree step (one saccade,
    flagged, five unknown velocities); a short loss at its landing (carried to the landing, its size whole,
    flagged); a blink inside it (no run crosses it); 74 ms with no position inside it (the run ends before the
    loss, kept, unflagged); lost samples alone in fixation (no event). A test rebuilds v3 and compares it with
    the file, so the builder's every rule is held.
""")

for path, sections in (
    ("docs/superpowers/specs/2026-08-31-saccade-detection-design.md", "§2 and §11's open question 1"),
    ("docs/superpowers/specs/2026-10-11-blink-padding-design.md", "§3's line keeping a frame gap's margin"),
    ("docs/superpowers/specs/2026-10-11-eye-loss-design.md", "§3's withholding of `feature_lost` as `invalid`"),
):
    append(path, f"""
## Amendment, 2026-11-06: lost data and the screen region

{sections[0].upper() + sections[1:]} is superseded where a paramset carries lost data (`max_lost_ms` above 0), as
the default does since 2026-11-06 (design spec `2026-11-06-eye-lost-data-and-region-design.md`, the requester's
decisions of that day): a sample not offered as measured is lost (a lost reflection, a value that is not a
number, an impossible speed, a repaired glitch, the four velocities a dropped frame spans, which take no margin)
or invalid (a blink with its padding, gaze off the screen), a saccade is carried across a short loss, and the
screen region is each session's recorded visible field plus 2 degrees, the placeholder only where none was
recorded. A paramset stored before masks as it did.
""")

append("HANDOVER-wl-expcontroller.md", """
# 2026-11-06: lost data and the screen region (the requester's joint answer)

Asked jointly by both sessions on 2026-11-06, and answered in wl-preproc's question interface: **the two detectors
agree, and wl-preproc changes.** A saccade is carried across a short loss of data, counted and flagged; a sample
present but invalid ends it; the mask's screen region is each session's recorded visible field (`setup.half_field_deg`)
plus 2 degrees. Designed in `docs/superpowers/specs/2026-11-06-eye-lost-data-and-region-design.md` (its §12 answers
wl-xcon's six questions; its amendments 2, 6, 7 and 10 record wl-xcon's check of 2026-11-06), built on
`feat/eye-lost-and-region`, not merged.
- *Agreed with wl-xcon, rule by rule:* the bound is the loss of data, from the last position to the next, at most
  50 ms inclusive (its `max_gap_s`); unknown velocities carry a run and never make one; a run ends at its last
  measured velocity, except that a short loss at its end is carried to the landing (both sessions' correction of
  2026-11-06, its version 8); the flag is judged on the run as stored, `[start - 2, stop)`; a non-finite position
  is lost. Its invalid input, and the region, it builds to these definitions.
- *v3:* `docs/fixtures/saccade/v3.json`, made by `python -m wl_preproc.eye.detect.contract_fixtures v3` from v2's
  traces and five of its own; its `about` is the reading rule. Built, not merged; its SHA-256 goes to wl-xcon with
  the merge.
""")

append("HANDOVER-wl-sync.md", """
### wl-sync's answer to item 6, 2026-11-06

**Confirmed, with three limits** (wl-sync's `main` at `84c55d6`, its `wl_sync/rp1/backend.py`): on `"split"` a `W`
tick is microseconds of RP1's crystal since its process's capture began, the counter reading 0 at enable, once per
process, unwrapped by `_BoxClock`. Within the crystal's error (the 50 ppm used); to one syscall's offset at the
start, which bench day measures; and `B` and `E` count `CLOCK_MONOTONIC` from the same origin, so `W - B` drift
grows from capture start, which is item 5's premise. On `"fake"` a tick is not elapsed time, and the daily report
already leaves `"fake"` out of its split-clock section. wl-sync may state it in `CodeWord.tick_us`'s docstring
once the requester approves. Its `84c55d6` adds a streaming reader and changes nothing the box writes: the pin stays.
""")

pathlib.Path("docs/handoffs/2026-11-06-eye-lost-data-and-region.md").write_text("""# Lost data and the screen region: a saccade carried across a short loss, and the mask at the recorded field

**`specs/2026-11-06-eye-lost-data-and-region-design.md`**: the requester's answers of 2026-11-06 to a question
asked jointly with wl-xcon. A saccade is carried across a short loss of data, counted and flagged; a sample present
but invalid still ends it; the mask's screen region is each session's recorded visible field plus 2 degrees.
- **Branch:** `feat/eye-lost-and-region`, from `plan/eye-lost-and-region`.
- **Spec:** the design, its three parts approved one by one on 2026-11-06, its seven rulings, and its amendments
  1-10, made while proving the plan, with wl-xcon's check of that day.
- **Plan:** `docs/superpowers/plans/2026-11-06-eye-lost-data-and-region.md`.

Every line of the plan was proven in a scratch branch before the plan was written.

---

## 1. What was built

- **The mask** (`eye/detect/validity.py`): a sample not offered as measured is `lost` or `invalid`; a dropped frame
  costs the four rows whose velocity spans it, with no margin; a lost stretch is carried while the positions either
  side are at most 50 ms apart, in whole frames, and withheld otherwise. A paramset stored before masks as it did.
- **The detectors:** Engbert-Kliegl, the contract's detector, carries an open run across lost samples and counts
  only measured velocities toward its minimum; the other six run unchanged on the trace with lost positions filled,
  and one shared rule (`registry._within_usable`) holds every detector's runs to the same terms.
- **The detection stage:** each run stores its lost samples, `n_lost`, and its flag; a peak speed is its measured
  samples'; the main sequence and vigor leave flagged saccades out.
- **The region:** `core.SessionSetup` keeps each session's display setup from `xcon/config.json`; the mask's region
  is its field plus 2 degrees, the placeholder where none was recorded, and `EyeValidity` says which.
- **Published:** the NWB eye-events tables carry `n_lost` and `flagged`, and the validity table `lost`; the daily
  report gives each eye's lost share, each fit's flagged saccades left out, and each eye on the placeholder.
- **v3** of the saccade fixtures, for wl-xcon.
- **A database declared before** refuses the two stages and names their statements, and the deletion of the old
  default's results.

## 2. What the other repositories must do

- **wl-xcon:** told with the merge, with v3's SHA-256; it vendors v3, builds its invalid input and the region to
  these definitions, and its rules match ours at its `16a5961` (`HANDOVER-wl-expcontroller.md`, 2026-11-06).

## 3. Still open

- **The speed ceiling** (`max_speed_deg_s`, 1000), the other half of the saccade-detection spec's open question 1.
- **wl-xcon's stereoscope calibration task** is not built; if it puts gaze in each viewport's frame, the region
  gains the vergence offset.

## 4. The rulings

The spec's seven rulings and its amendments 1-10.

## 5. What execution measured

Executed from {{DATE}}: Task 1 `{{SHA1}}`, Task 2 `{{SHA2}}`, Task 3 `{{SHA3}}`, Task 4 `{{SHA4}}`, Task 5
`{{SHA5}}`, Task 6 `{{SHA6}}`, Task 7 `{{SHA7}}`.
- **The full suite, once, on both interpreters,** with the BMD and NSLR references set, as CI has them, the
  Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, as in CI:
  - 3.11: **{{S311}}**;
  - 3.13: **{{S313}}**.
""")

edit("docs/CHECKPOINT.md", """> on 2026-11-06. Next, first of wl-xcon's asks by the requester's decision of 2026-11-06: lost data
> and the screen region (`specs/2026-11-06-eye-lost-data-and-region-design.md` on
> `spec/eye-lost-and-region`, approved that day; its plan being proved), a saccade carried across a
> short loss of data and the mask at each session's recorded field; then, in the requester's order""",
     """> on 2026-11-06. **Lost data and the screen region, first of wl-xcon's asks, is built** on
> `feat/eye-lost-and-region`, not merged (`specs/2026-11-06-eye-lost-data-and-region-design.md`,
> approved 2026-11-06; `handoffs/2026-11-06-eye-lost-data-and-region.md`): a saccade is carried
> across a short loss of data, counted and flagged, an invalid sample still ending it; the mask's
> region is each session's recorded field plus 2 degrees; v3 of the saccade fixtures is made.
> Next: its review; then, in the requester's order""")
edit("docs/CHECKPOINT.md", """> merge was next; each true when written.*""",
     """> merge was next; until lost data and the screen region was built, that its plan was being proved;
> each true when written.*""")
edit("wl.yaml", """    Next, first of wl-xcon's asks by the requester's decision of
    2026-11-06: lost data and the screen region
    (docs/superpowers/specs/2026-11-06-eye-lost-data-and-region-design.md,
    approved, its plan being proved); then, in the requester's order of""",
     """    Lost data and the screen region, first of wl-xcon's asks by the
    requester's decision of 2026-11-06, is built on
    feat/eye-lost-and-region (docs/handoffs/2026-11-06-eye-lost-data-and-region.md),
    not merged, its review next; then, in the requester's order of""")
```

Expected: no output, and exit 0.

- [ ] **Step 2: Check the manifest.**

```bash
.venv/bin/wl-check
```

Expected: `wl.yaml: no findings`.

- [ ] **Step 3: The full suite, once, on both interpreters,** in parallel, with the BMD and NSLR references
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

Expected, each exiting 0: 3.11, **2744 passed, 26 skipped, 1 deselected, 1 xfailed**; 3.13, **2744 passed, 27 skipped, 1 xfailed**. Each is the 2678 on main before this plan plus its 66 new tests. A MySQL container
that does not start within its 120 s under heavy load errors every database test at once; that is the known flake,
and the suite is run again.

- [ ] **Step 4: Fill the handoff's §5** with this execution's own figures: `{{DATE}}` the days, `{{SHA1}}` to
  `{{SHA7}}` Tasks 1-7's commits (`git log --oneline -8`), and `{{S311}}` and `{{S313}}` Step 3's two summary
  lines. Then `grep -c '{{' docs/handoffs/2026-11-06-eye-lost-data-and-region.md`.

Expected: `0`.

- [ ] **Step 5: Commit.**

```bash
git add -A docs wl.yaml HANDOVER-wl-expcontroller.md HANDOVER-wl-sync.md
git commit -F - <<'MSG'
docs: lost data and the screen region's records -- spec amendments 1-10, the three earlier specs' dated amendments, wl-preproc's answer in HANDOVER-wl-expcontroller.md, wl-sync's answer to item 6 in HANDOVER-wl-sync.md, the handoff, and the checkpoint's and wl.yaml's lines: built, not merged; next, the review

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```
