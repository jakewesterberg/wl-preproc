# Both-Eyes Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Where only one eye has usable data, the both-eyes (conjunction) trace takes that eye's labels. A one-eye event the other eye could not have seen is kept whole. A new table says which eye each stretch of the trace came from.

**Architecture:** Three pure functions in `schema/detect.py` decide it from the two eyes' runs and validity masks:
- `_conjunction_fallback`: the one-eye events kept whole, and a per-sample "which eye" array;
- `_one_eye_pieces`: each eye's runs, cut to the samples where that eye alone is usable;
- `_value_runs`: a per-sample array as maximal runs.

`EyeDetection.make()` hands their runs to `_insert_trace` beside the two-eye runs, which are unchanged. It fills gaps from the left eye's mask only where neither eye is usable, and writes the which-eye runs to a new part table, `EyeDetection.Source`. `_insert_trace` measures a kept run as its own eye's row, and every other conjunction run on the eye usable throughout it.

**Tech Stack:** Python ≥3.11, numpy, DataJoint 2.3 (schema tests only).

**Spec:** `docs/superpowers/specs/2026-09-28-both-eyes-fallback-design.md` (`8b1d684`). It is binding. The requester approved it on 2026-09-28 ("Yes, write the plan").

**Every piece of code below was proven before this plan was written,** in a scratch worktree, `$SCRATCH/fb_wt`, which holds these exact files.
- **Tests:** every test named here passed there. So did every test area that touches the stored runs: `tests/eye`, `tests/schema/test_detect_populate.py`, `tests/schema/test_eye_populate.py`, `tests/schema/test_consensus_populate.py`, `tests/schema/test_detect_schema.py` and `tests/cli`.
- **Failing runs:** each task's was measured on the code that task starts from. The messages quoted below are the real ones.
- **Mutation checks:** every one named below was run, and each failed the test named.
- **The code blocks** were cut from those files, and applying them in task order to `8b1d684` reproduces them byte for byte.

`$SCRATCH` is `/private/tmp/claude-501/-Users-jakewesterberg-GitHub-wl-preproc/68c9c246-19c3-4839-8d7e-6b5ebcddb8e4/scratchpad`.

## Global Constraints

- **The spec is binding.** Where it and this plan disagree, the spec wins; record a ruling.
- **Which eyes are usable** at a sample is read from each eye's validity mask, `per_eye[eye][2]` (`offered`): `None` where usable (spec §1).
- **The trace, moment by moment (spec §1):**
  - both usable: unchanged, the two-eye rule (`_conjunction_runs`, not touched), `fixation` otherwise;
  - one usable: that eye's own labels (`_one_eye_pieces`), `fixation` between them;
  - neither: the left eye's mask label, as today.
- **A one-eye run is kept whole (spec §2)** when all three hold:
  - its kind is one the conjunction carries (`labels.py::kind_of` is not `None`);
  - the other eye has no run of the same kind overlapping it by at least the floor;
  - the other eye's mask withholds at least one of its samples.

  Any two candidates from the two eyes that overlap or touch are both dropped, whatever their kinds.
- **A kept run is stored as its own eye's row:** its own label, its own eye's gaze, velocity and mask, that eye's rules (NSLR's landing sample, BMD's take-off sample), its own `reliability`.
- **Every other conjunction run** is measured on the eye usable throughout it, the left first. With neither, it is stored unmeasured. Two-eye runs keep `reliability` NULL and never take the landing or take-off rule (spec §3).
- **`EyeDetection.Source` (spec §4):**
  - a part table of `EyeDetection`, written for the `conjunction` trace only, never for a refused row;
  - its runs tile `[0, n_samples)`, and `source_stop` is exclusive;
  - `source` is `enum('both','left','right','neither')`;
  - a kept run is marked with its own eye along its whole length.
- **An eye with no calibration** keeps today's refused both-eyes row (spec §2's ruling). That code path is not touched.
- **Unchanged:** the per-eye traces, `docs/schemas` and `wlpp report`. **No migration** (spec §5).
- **Tests.**
  - Run with `.venv/bin/python -m pytest <files> -p no:cacheprovider`. In a git worktree, prefix `PYTHONPATH=$PWD`.
  - Database tests need Docker for the MySQL testcontainer. A module-wide setup error on a 120 s container timeout is the known flake: re-run it.
  - Mutation checks run with `PYTHONDONTWRITEBYTECODE=1`, after `find . -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +`, and again after restoring.
  - **Run each task's own tests.** The full suite runs once, on both interpreters, in Task 3 (the requester's preference of 2026-09-27).
  - The Bash tool's shell is zsh: an unquoted `$VAR` holding several arguments is not split. Use `bash -c` for such loops.
- **Every commit message ends with:**

  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R
  ```

## Review Focus

Five inputs the spec implies, most likely first. Each is pinned by a test named here, in the task that owns the code.

1. **A one-eye event that starts where both eyes are usable and runs into the other eye's blink.** It must be kept whole, not cut at the mask edge, and marked with its own eye along its whole length. Task 1: `test_a_one_eye_event_the_other_eye_could_not_see_is_kept_whole`. Task 2: `test_the_both_eyes_trace_keeps_a_step_only_the_left_eye_could_see`.
2. **The other eye's own label over the event, of another kind.** This is often that eye's background: BMD's `drift`, NSLR's and REMoDNaV's pursuit, which the conjunction intersects. It is what the other eye saw instead, not a counterpart, so the event is still kept. Measured on the reference recording after the usable-data guard: 451 of BMD's 1,500 kept runs overlap such a run, 257 of NSLR's 3,353, 71 of REMoDNaV's 1,049, 2 of Nyström–Holmqvist's 768, and none for Engbert–Kliegl or Otero-Millan, whose background (`fixation`) is not intersected. Task 1: `test_the_other_eyes_run_of_another_kind_is_no_match`.
3. **A conjunction run that joins two sources.** Two cases:
   - a two-eye event continuing into a one-eye stretch;
   - two kept runs from one eye that touch. Measured on the reference recording: 36 kept runs for Nyström–Holmqvist, 1 each for Engbert–Kliegl and NSLR, none for the other three. The per-eye trace stores such a pair merged too.

   Either way it is one stored run. It is measured on the eye usable throughout it, the left first, and stored unmeasured when neither was usable throughout: never measured on a withheld sample. Task 2: `test_a_conjunction_run_is_measured_on_the_eye_usable_throughout_it`.
4. **Both eyes withheld.** The trace keeps the left eye's own mask label, `blink` or `invalid`, never `fixation`, and `Source` says `neither`. Task 2: `test_where_neither_eye_is_usable_the_both_eyes_trace_keeps_the_left_masks_label`.
5. **A session with no withheld sample.** The trace must be exactly today's, and `Source` one `both` run. Task 2: `test_the_which_eye_trace_is_both_throughout_when_both_eyes_are_usable`, with the existing conjunction tests on `stepped_session` (`test_engbert_kliegl_conjunction_rows_are_unchanged` among them) passing unchanged.

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/schema/detect.py` | the three functions (Task 1); `EyeDetection.Source`, `make()`'s conjunction branch and `_insert_trace`'s measurement (Task 2) |
| `tests/schema/test_detect_populate.py` | the functions' tests (Task 1); `_insert_trace`'s and the stored rows' tests, and one existing test updated (Task 2) |
| `tests/eye/detect/test_nystrom_holmqvist_validation.py` | the gated check on the reference recording (Task 3) |
| `docs/…`, `wl.yaml` | the parent specs' amendments, the handoff and the status (Task 3) |

---

### Task 1: The fallback's three functions

**Files:**
- Modify: `wl_preproc/schema/detect.py` (the labels import; three functions just above `_conjunction_runs`)
- Test: `tests/schema/test_detect_populate.py` (appended)

**Interfaces — produces:**
- `_conjunction_fallback(left: list[Run], right: list[Run], left_offered: np.ndarray, right_offered: np.ndarray, min_duration_samples: int) -> tuple[list[tuple[str, Run]], np.ndarray]`: the kept runs as `(eye, run)` in time order, and a per-sample object array of `"both"`, `"left"`, `"right"` or `"neither"`;
- `_one_eye_pieces(left, right, left_offered, right_offered) -> list[Run]`: the left eye's pieces first, then the right's, each carrying its run's label and no reliability;
- `_value_runs(values: np.ndarray) -> list[tuple[int, int, object]]`: maximal `(start, stop, value)` runs tiling the array.

- [ ] **Step 1: Write the failing tests.** Append to `tests/schema/test_detect_populate.py`:

```python
# -- The both-eyes fallback (design spec `2026-09-28-both-eyes-fallback-design.md`) --


def _offered(n, withheld=()):
    from wl_preproc.eye.detect.labels import Label

    offered = np.full(n, None, dtype=object)
    for index in withheld:
        offered[index] = Label.INVALID
    return offered


def test_a_one_eye_event_the_other_eye_could_not_see_is_kept_whole():
    """Spec section 2: the right eye is withheld over half of the left
    eye's saccade, so the right eye could not have confirmed it. It is kept
    whole, and marked `left` along its whole length, including where both
    eyes were usable (section 4)."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_fallback

    saccade = Run(10, 20, Label.SACCADE)
    kept, source = _conjunction_fallback([saccade], [], _offered(40), _offered(40, range(15, 40)), 1)
    assert kept == [("left", saccade)]
    assert list(source) == ["both"] * 10 + ["left"] * 30


def test_an_unmatched_event_the_other_eye_could_see_is_still_dropped():
    """The two-eye rule stands wherever both eyes were usable throughout."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_fallback

    kept, source = _conjunction_fallback([Run(10, 20, Label.SACCADE)], [], _offered(40), _offered(40), 1)
    assert kept == []
    assert set(source) == {"both"}


def test_a_label_the_conjunction_does_not_carry_is_never_kept():
    """Spec section 2's first condition: `fixation` is painted, not
    intersected (`labels.py::kind_of` is `None`), so NSLR's own fixation run
    is not kept at a mask edge, and the which-eye trace stays `both` over
    its usable samples."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_fallback

    kept, source = _conjunction_fallback([Run(10, 20, Label.FIXATION)], [], _offered(40), _offered(40, [15]), 1)
    assert kept == []
    assert list(source[10:20]) == ["both"] * 5 + ["left"] + ["both"] * 4


def test_a_matched_event_is_left_to_the_two_eye_rule():
    """A same-kind counterpart overlapping by exactly the floor makes it the
    two-eye rule's, although the right eye was withheld during it."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_fallback

    kept, _ = _conjunction_fallback([Run(10, 20, Label.SACCADE)], [Run(12, 22, Label.MICROSACCADE)],
                                    _offered(40), _offered(40, [19]), 8)
    assert kept == []


def test_a_same_kind_overlap_shorter_than_the_floor_is_no_match():
    """Spec section 2: unmatched in `_kind_agreement`'s sense, so an overlap
    under the floor does not count. The right eye was withheld during the
    left eye's saccade and not the other way round, so only the left run is
    a candidate, and it is kept."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_fallback

    saccade = Run(10, 20, Label.SACCADE)
    kept, _ = _conjunction_fallback([saccade], [Run(17, 26, Label.SACCADE)],
                                    _offered(40), _offered(40, [11]), 6)
    assert kept == [("left", saccade)]


def test_the_other_eyes_run_of_another_kind_is_no_match():
    """Spec section 2 matches by kind. The other eye's own label over the
    event is what it saw instead, not a counterpart: often its background,
    which BMD (`drift`), NSLR and REMoDNaV (pursuit) emit as a kind the
    conjunction intersects. Measured 2026-09-28 on the reference recording,
    after the usable-data guard: such a run overlaps 451 of BMD's 1,500 kept
    runs, 257 of NSLR's 3,353, 71 of REMoDNaV's 1,049 and 2 of
    Nystrom-Holmqvist's 768."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_fallback

    saccade = Run(10, 20, Label.SACCADE)
    kept, _ = _conjunction_fallback([saccade], [Run(12, 18, Label.DRIFT)],
                                    _offered(40), _offered(40, [18, 19]), 1)
    assert kept == [("left", saccade)]


@pytest.mark.parametrize("right_run", ["different kind, overlapping", "one label, touching",
                                       "same kind, overlapping less than the floor"])
def test_two_candidates_that_overlap_or_touch_are_both_dropped(right_run):
    """Spec section 2's ruling, as widened while building: each eye was
    withheld somewhere in the other's run, so both are candidates, and they
    overlap or touch, so both are dropped."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_fallback

    right = {"different kind, overlapping": Run(18, 26, Label.PSO),
             "one label, touching": Run(20, 26, Label.SACCADE),
             "same kind, overlapping less than the floor": Run(17, 26, Label.SACCADE)}[right_run]
    kept, _ = _conjunction_fallback([Run(10, 20, Label.SACCADE)], [right], _offered(40, [25]),
                                    _offered(40, [11]), 6)
    assert kept == []


def test_the_which_eye_trace_names_the_usable_eyes():
    """Spec section 4, with nothing kept: `both`, `left` where only the left
    eye was usable, `right` where only the right, `neither` where no eye."""
    from wl_preproc.schema.detect import _conjunction_fallback, _value_runs

    _kept, source = _conjunction_fallback([], [], _offered(12, [6, 7, 8, 9]), _offered(12, [2, 3, 8, 9]), 1)
    assert _value_runs(source) == [(0, 2, "both"), (2, 4, "left"), (4, 6, "both"), (6, 8, "right"),
                                   (8, 10, "neither"), (10, 12, "both")]


def test_where_one_eye_alone_is_usable_the_conjunction_takes_its_labels():
    """Spec section 1: each eye's runs, cut to the samples where it alone
    was usable. A run the other eye matched elsewhere contributes only its
    one-eye stretch; the two-eye rule has the rest."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _one_eye_pieces

    left = [Run(10, 20, Label.SACCADE), Run(30, 40, Label.DRIFT)]
    right = [Run(10, 14, Label.SACCADE), Run(22, 28, Label.PSO)]
    left_offered = _offered(40, range(24, 26))       # left withheld at 24-25
    right_offered = _offered(40, [*range(15, 22), 35])  # right withheld at 15-21 and 35
    assert _one_eye_pieces(left, right, left_offered, right_offered) == [
        Run(15, 20, Label.SACCADE), Run(35, 36, Label.DRIFT), Run(24, 26, Label.PSO),
    ]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py -q -p no:cacheprovider -k "kept_whole or still_dropped or does_not_carry or left_to_the_two_eye or shorter_than_the_floor or another_kind_is_no_match or overlap_or_touch or names_the_usable or alone_is_usable"`
Expected: 11 failed. Ten with `ImportError: cannot import name '_conjunction_fallback'`, one with `ImportError: cannot import name '_one_eye_pieces'`.

- [ ] **Step 3: Implement** — apply exactly this diff:

```diff
--- a/wl_preproc/schema/detect.py
+++ b/wl_preproc/schema/detect.py
@@ -106,6 +106,7 @@
     kind_of,
     labels_from_runs,
     runs_from_labels,
+    true_runs,
 )
 from wl_preproc.schema import DEFAULT_PREFIX, core, paramset, pipeline
 
@@ -1023,8 +1024,103 @@
     FLOOR is what is under test and which label a surviving span carries is
     not."""
     return lambda _start, _stop: label
+
+
+def _conjunction_fallback(left, right, left_offered, right_offered, min_duration_samples):
+    """The one-eye events the conjunction keeps, and which eye each of its
+    samples draws on (design spec `2026-09-28-both-eyes-fallback-design.md`
+    sections 2 and 4).
+
+    **An own-eye run is kept, whole,** when its kind is one the conjunction
+    carries, the other eye has no same-kind run overlapping it by
+    `min_duration_samples`, and the other eye's mask withholds at least one
+    of its samples: the other eye could not have confirmed it. Two such runs
+    from the two eyes that overlap or touch are both dropped. Over an
+    overlap both eyes were usable and the two-eye rule did not accept them;
+    touching runs of one label would merge into one stored run.
+
+    Returns `(kept, source)`: `kept` as `(eye, run)` in time order, and
+    `source` a per-sample object array of `both`, `left`, `right` or
+    `neither` -- which eyes were usable, with each kept run marked by its
+    own eye along its whole length."""
+    floor = max(int(min_duration_samples), 1)
+    usable = {"left": np.array([label is None for label in left_offered], dtype=bool),
+              "right": np.array([label is None for label in right_offered], dtype=bool)}
+    runs = {"left": sorted(left, key=lambda run: run.start), "right": sorted(right, key=lambda run: run.start)}
+    withheld_before = {eye: np.concatenate(([0], np.cumsum(~usable[eye]))) for eye in usable}
+    stops = {eye: np.array([run.stop for run in runs[eye]], dtype=np.int64) for eye in runs}
+
+    def overlapping(run, eye):
+        """`eye`'s runs overlapping or touching `run` -- per-eye runs are
+        disjoint and sorted, so they are one contiguous slice."""
+        index = int(np.searchsorted(stops[eye], run.start, side="left"))
+        while index < len(runs[eye]) and runs[eye][index].start <= run.stop:
+            yield runs[eye][index]
+            index += 1
+
+    def matched(run, other_eye) -> bool:
+        kind = kind_of(run.label)
+        return any(kind_of(other.label) == kind
+                   and min(run.stop, other.stop) - max(run.start, other.start) >= floor
+                   for other in overlapping(run, other_eye))
+
+    candidates = {"left": [], "right": []}
+    for eye, other_eye in (("left", "right"), ("right", "left")):
+        for run in runs[eye]:
+            if (kind_of(run.label) is not None
+                    and withheld_before[other_eye][run.stop] > withheld_before[other_eye][run.start]
+                    and not matched(run, other_eye)):
+                candidates[eye].append(run)
+
+    candidate_stops = {eye: np.array([run.stop for run in candidates[eye]], dtype=np.int64)
+                       for eye in candidates}
 
+    def clashes(run, other_eye) -> bool:
+        index = int(np.searchsorted(candidate_stops[other_eye], run.start, side="left"))
+        return index < len(candidates[other_eye]) and candidates[other_eye][index].start <= run.stop
 
+    kept = sorted(
+        [(eye, run) for eye, other_eye in (("left", "right"), ("right", "left"))
+         for run in candidates[eye] if not clashes(run, other_eye)],
+        key=lambda pair: pair[1].start,
+    )
+
+    source = np.full(len(usable["left"]), "neither", dtype=object)
+    source[usable["left"] & usable["right"]] = "both"
+    source[usable["left"] & ~usable["right"]] = "left"
+    source[~usable["left"] & usable["right"]] = "right"
+    for eye, run in kept:
+        source[run.start:run.stop] = eye
+    return kept, source
+
+
+def _one_eye_pieces(left, right, left_offered, right_offered) -> list[Run]:
+    """Each eye's runs, cut to the samples where that eye alone was usable
+    (design spec `2026-09-28-both-eyes-fallback-design.md` section 1): where
+    only one eye has data, the conjunction takes that eye's labels. A piece
+    of a run matched elsewhere joins the two-eye run beside it when they
+    share a label."""
+    usable = {"left": np.array([label is None for label in left_offered], dtype=bool),
+              "right": np.array([label is None for label in right_offered], dtype=bool)}
+    pieces: list[Run] = []
+    for eye, other_eye, runs in (("left", "right", left), ("right", "left", right)):
+        alone = usable[eye] & ~usable[other_eye]
+        for run in runs:
+            for start, stop in true_runs(alone[run.start:run.stop]):
+                pieces.append(Run(start=run.start + start, stop=run.start + stop, label=run.label))
+    return pieces
+
+
+def _value_runs(values: np.ndarray) -> list[tuple[int, int, object]]:
+    """Maximal `(start, stop, value)` runs of a per-sample array, tiling it."""
+    runs, start = [], 0
+    for index in range(1, len(values) + 1):
+        if index == len(values) or values[index] != values[start]:
+            runs.append((start, index, values[start]))
+            start = index
+    return runs
+
+
 def _conjunction_runs(
     left: list[Run],
     right: list[Run],
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: the Step 2 command.
Expected: 11 passed.

- [ ] **Step 5: Mutation checks.** Each must fail the test named; each was measured to.
  - `for run in candidates[eye] if not clashes(run, other_eye)]` → `for run in candidates[eye]]`: the three `test_two_candidates_that_overlap_or_touch_are_both_dropped` cases.
  - `withheld_before[other_eye][run.stop] > withheld_before[other_eye][run.start]` → `>=`: `test_an_unmatched_event_the_other_eye_could_see_is_still_dropped`.
  - `if (kind_of(run.label) is not None` → `if (True`: `test_a_label_the_conjunction_does_not_carry_is_never_kept`.
  - `return any(kind_of(other.label) == kind` → `return any(True`: `test_the_other_eyes_run_of_another_kind_is_no_match`.
  - In `matched`, `max(run.start, other.start) >= floor` → `>= 1`: `test_a_same_kind_overlap_shorter_than_the_floor_is_no_match`. → `> floor`: `test_a_matched_event_is_left_to_the_two_eye_rule`.
  - `source[run.start:run.stop] = eye` → `pass`: `test_a_one_eye_event_the_other_eye_could_not_see_is_kept_whole`.
  - `alone = usable[eye] & ~usable[other_eye]` → `& ~usable[eye]`: `test_where_one_eye_alone_is_usable_the_conjunction_takes_its_labels`.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/detect.py tests/schema/test_detect_populate.py
git commit -m "feat(eye): which one-eye events the both-eyes trace keeps, where each eye alone is usable, and which eye each sample draws on

<trailer lines>"
```

---

### Task 2: The both-eyes trace falls back, and stores which eye it used

**Files:**
- Modify: `wl_preproc/schema/detect.py` (`EyeDetection.Source`; `make()`'s conjunction branch; `_insert_trace`)
- Test: `tests/schema/test_detect_populate.py` (the `_run_insert_trace` harness; `test_no_detector_labels_a_missing_gaze_value`; appended tests and a fixture)

**Interfaces:**
- Consumes (Task 1): `_conjunction_fallback`, `_one_eye_pieces` and `_value_runs`, with the signatures above.
- Produces:
  - `EyeDetection.Source`, a part table: `-> master`, `source_index : int unsigned`, then `source_start`, `source_stop : int unsigned` and `source : enum('both','left','right','neither')`;
  - `EyeDetection._insert_trace(self, key, trace, gaze, v, offered, intervals, fs_hz, detector, detector_params, kept=None, eyes=None)`:
    - `kept` maps a run's `(start, stop)` to `(eye, gaze, v, offered)`, which it is measured on as that eye's row;
    - `eyes` maps `"left"` and `"right"` to `(gaze, v, offered)`, the inputs every other conjunction run is measured from.

    Both default to `None`, so the per-eye call is unchanged.

- [ ] **Step 1: Write the failing tests.**
  - Update the `_run_insert_trace` harness and `test_no_detector_labels_a_missing_gaze_value`, exactly as this diff does. That test's missing value is in the left eye alone, so the both-eyes trace now falls back to the right eye there. The test's own dated note records the change.

```diff
--- a/tests/schema/test_detect_populate.py
+++ b/tests/schema/test_detect_populate.py
@@ -1342,19 +1342,30 @@
 
 def test_no_detector_labels_a_missing_gaze_value(missing_gaze_session):
     """The point of withholding it: every registered detector's stored run
-    over the missing sample, on the left trace and in the conjunction, is the
-    mask's own `invalid` -- no detector was handed the sample to label."""
+    over the missing sample, on the left trace, is the mask's own `invalid`
+    -- no detector was handed the sample to label. The conjunction falls
+    back to the right eye there: it carries the right eye's own label, and
+    `EyeDetection.Source` says `right` (design spec
+    `2026-09-28-both-eyes-fallback-design.md` section 1).
+
+    *Until 2026-09-28 the conjunction was labelled from the left eye's mask
+    alone, and read `invalid` here too; true when written.*"""
     from wl_preproc.schema import detect
 
+    def covering(table, trace, name, start, stop):
+        (row,) = (table & {**session_key, "trace": trace, **_detector(name)}
+                  & f"{start} <= {missing_row}" & f"{stop} > {missing_row}").to_dicts()
+        return row
+
     session_key, _report, missing_row = missing_gaze_session
     for name in _detector_names():
-        for trace in ("left", "conjunction"):
-            (covering,) = (
-                detect.EyeDetection.Run
-                & {**session_key, "trace": trace, **_detector(name)}
-                & f"run_start <= {missing_row}" & f"run_stop > {missing_row}"
-            ).to_dicts()
-            assert covering["label"] == "invalid", (name, trace, covering["label"])
+        left = covering(detect.EyeDetection.Run, "left", name, "run_start", "run_stop")
+        assert left["label"] == "invalid", (name, left["label"])
+        right = covering(detect.EyeDetection.Run, "right", name, "run_start", "run_stop")
+        both = covering(detect.EyeDetection.Run, "conjunction", name, "run_start", "run_stop")
+        assert both["label"] == right["label"], (name, both["label"], right["label"])
+        source = covering(detect.EyeDetection.Source, "conjunction", name, "source_start", "source_stop")
+        assert source["source"] == "right", (name, source)
 
 
 def test_saccade_runs_carry_measurements_and_others_do_not(stepped_session):
@@ -2555,7 +2566,7 @@
 
 
 def _run_insert_trace(intervals, n_samples=200, fs_hz=500.0, *, detector_name="engbert_kliegl",
-                      trace="left", gaze=None, v=None, offered=None):
+                      trace="left", gaze=None, v=None, offered=None, kept=None, eyes=None):
     """`EyeDetection._insert_trace` over a ramp and a fully-available mask
     unless given others, as `detector_name` at its default params, returning
     `(master_row, run_rows)`."""
@@ -2575,7 +2586,7 @@
     sink = _CapturedInserts()
     detect_schema.EyeDetection._insert_trace(
         sink, {"subject": "s"}, trace, gaze, v, offered, intervals, fs_hz,
-        detector, detector.defaults,
+        detector, detector.defaults, kept=kept, eyes=eyes,
     )
     (master,) = sink.master
     return master, sink.rows
```

  - Then append to the end of the file, after Task 1's tests:

```python
@pytest.mark.parametrize("detector_name, rule, first, last", [
    ("nslr", {"runs_end_before_landing": True, "min_measured_ms": 10.0}, 60, 80),
    ("bmd", {"runs_end_before_landing": False, "min_measured_ms": None, "runs_start_after_takeoff": True}, 59, 79),
])
def test_a_kept_one_eye_event_is_stored_as_its_own_eyes_row(detector_name, rule, first, last):
    """Spec sections 2 and 3: a kept run is measured on its own eye's gaze,
    under that eye's rules -- NSLR's landing sample, BMD's take-off sample
    for its own events, neither of which the conjunction's two-eye runs use
    -- and carries its own reliability."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.eye.detect.measure import measure_event_run

    gaze, v, _brief, _long, _intervals = _insert_trace_inputs()
    left_gaze = gaze * 2.0
    offered = np.full(len(gaze), None, dtype=object)
    run = Run(60, 80, Label.SACCADE, reliability=0.9)

    _master, rows = _run_insert_trace([run], detector_name=detector_name, trace="conjunction", gaze=gaze, v=v,
                                      kept={(60, 80): ("left", left_gaze, v, offered)})

    row = _event_rows(rows)[(60, 80)]
    expected = measure_event_run(left_gaze, v, offered, 60, 80, 500.0, **rule)
    assert row["amplitude_deg"] == expected.amplitude_deg
    assert (row["start_x_deg"], row["end_x_deg"]) == (float(left_gaze[first, 0]), float(left_gaze[last, 0]))
    assert row["reliability"] == 0.9


@pytest.mark.parametrize("withheld, measured_on", [("right", "left"), ("left", "right"), ("both", None)])
def test_a_conjunction_run_is_measured_on_the_eye_usable_throughout_it(withheld, measured_on):
    """Spec section 3, as ruled while building: a two-eye saccade joined by
    its one-eye continuation is one stored run. It is measured on the eye
    usable throughout it, the left first, and unmeasured when neither was."""
    from wl_preproc.eye.detect.labels import Label, Run

    gaze, v, *_ = _insert_trace_inputs()
    eye_gaze = {"left": gaze * 2.0, "right": gaze * 3.0}
    offered = {eye: np.full(len(gaze), None, dtype=object) for eye in ("left", "right")}
    for eye in ("left", "right"):
        if withheld in (eye, "both"):
            offered[eye][75] = Label.INVALID
    eyes = {eye: (eye_gaze[eye], v, offered[eye]) for eye in ("left", "right")}
    two_eye, continuation = Run(60, 70, Label.SACCADE), Run(70, 80, Label.SACCADE)

    _master, rows = _run_insert_trace([two_eye, continuation], trace="conjunction", gaze=gaze, v=v, eyes=eyes)

    row = _event_rows(rows)[(60, 80)]
    if measured_on is None:
        assert row["amplitude_deg"] is None
    else:
        assert (row["start_x_deg"], row["end_x_deg"]) == (float(eye_gaze[measured_on][60, 0]),
                                                          float(eye_gaze[measured_on][79, 0]))


# Both eyes withheld over 100 ms of the detection trial's quiet opening,
# well before its first step at 1.0 s.
_BOTH_WITHHELD_S = (4 * TRIAL_DURATION_S + 0.3, 4 * TRIAL_DURATION_S + 0.4)


def _withhold_one_eye_then_both(session_dir) -> None:
    """Drive the RIGHT eye's `DataQuality` to zero from 50 ms before the
    first planted step to 150 ms after its onset, so only the left eye can
    see it, and BOTH eyes' over `_BOTH_WITHHELD_S`. `_build_stepped_session`
    puts the detection trial after four calibration trials."""
    from wl_preproc.synth.ohdpi import HEADER

    (ohdpi_txt,) = (session_dir / "ohdpi").glob("*.txt")
    lines = ohdpi_txt.read_text(encoding="utf-8").splitlines()
    header_line, data_lines = lines[0], lines[1:]
    onset_s = 4 * TRIAL_DURATION_S + _ONSET_OFFSETS_S[0]
    withheld = [
        ("RightDataQuality", onset_s - 0.05, onset_s + 0.15),
        ("LeftDataQuality", *_BOTH_WITHHELD_S),
        ("RightDataQuality", *_BOTH_WITHHELD_S),
    ]
    for name, start_s, stop_s in withheld:
        column = HEADER.index(name)
        for row in range(_first_row_at(start_s), _first_row_at(stop_s)):
            fields = data_lines[row].split(" ")
            fields[column] = "0.0000"
            data_lines[row] = " ".join(fields)
    ohdpi_txt.write_text("\n".join([header_line, *data_lines]) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def one_eye_gap_session(daemon_module, prefix, tmp_path_factory):
    """`stepped_session`'s construction with the right eye withheld over the
    first planted step and both eyes over `_BOTH_WITHHELD_S`. Date, subject
    and seed checked unclaimed across `tests/` on 2026-09-28. Returns
    `(session_key, report, planted_onsets, both_withheld_row)`, the last the
    stored-trace row at the middle of `_BOTH_WITHHELD_S`."""
    from tests.schema.test_eye_populate import _rows_for_times

    session_key, segment, onset_times = _build_stepped_session(
        tmp_path_factory,
        dirname="detectgap", session_id="2027-07-11_01", subject="detgap01",
        session_datetime=datetime.datetime(2027, 7, 11, 9, 0), seed=711,
        after_generate=_withhold_one_eye_then_both,
    )
    report = daemon_module.run_once(prefix=prefix)
    *planted, both_withheld_row = _rows_for_times(session_key, segment,
                                                  [*onset_times, sum(_BOTH_WITHHELD_S) / 2])
    return session_key, report, planted, both_withheld_row


def test_the_both_eyes_trace_keeps_a_step_only_the_left_eye_could_see(one_eye_gap_session):
    """Spec section 6, stored: with the right eye withheld over the first
    planted step, every registered detector's both-eyes trace still carries
    it. It is marked `left` in `EyeDetection.Source`, which tiles the trace,
    and it is measured as the left eye's own row."""
    from wl_preproc.schema import detect

    session_key, _report, planted, _both_withheld_row = one_eye_gap_session
    first = planted[0]
    for name in _detector_names():
        where = {**session_key, "trace": "conjunction", **_detector(name)}
        conjunction = {(r["run_start"], r["run_stop"]): r
                       for r in (detect.EyeDetection.Run & where).to_dicts()}
        # The left eye's own event at the step, wherever this detector puts
        # its onset (Otero-Millan's is 7 samples early on this session).
        at_step = [r for r in (detect.EyeDetection.Run
                               & {**session_key, "trace": "left", **_detector(name)}).to_dicts()
                   if r["label"] in ("saccade", "microsaccade")
                   and r["run_start"] <= first + 5 and r["run_stop"] > first]
        assert at_step, name

        sources = (detect.EyeDetection.Source & where).to_dicts(order_by="source_index")
        n_samples = (detect.EyeDetection & where).fetch1("n_samples")
        assert sources[0]["source_start"] == 0 and sources[-1]["source_stop"] == n_samples, name
        assert all(a["source_stop"] == b["source_start"] for a, b in zip(sources, sources[1:])), name

        for left in at_step:
            span = (left["run_start"], left["run_stop"])
            assert span in conjunction, (name, span)
            assert conjunction[span]["label"] == left["label"], name
            assert conjunction[span]["amplitude_deg"] == left["amplitude_deg"], name
            covering = [s["source"] for s in sources if s["source_start"] <= span[0] and span[1] <= s["source_stop"]]
            assert covering == ["left"], (name, span, covering)


def test_where_neither_eye_is_usable_the_both_eyes_trace_keeps_the_left_masks_label(one_eye_gap_session):
    """Spec section 1's last row: with both eyes withheld, the both-eyes
    trace carries the left eye's own mask label, as it did before the
    fallback -- not `fixation` -- and `EyeDetection.Source` says `neither`."""
    from wl_preproc.schema import detect

    def covering(table, trace, name, start, stop):
        (row,) = (table & {**session_key, "trace": trace, **_detector(name)}
                  & f"{start} <= {row_index}" & f"{stop} > {row_index}").to_dicts()
        return row

    session_key, _report, _planted, row_index = one_eye_gap_session
    for name in _detector_names():
        left = covering(detect.EyeDetection.Run, "left", name, "run_start", "run_stop")
        both = covering(detect.EyeDetection.Run, "conjunction", name, "run_start", "run_stop")
        assert left["label"] in ("blink", "invalid"), (name, left["label"])
        assert both["label"] == left["label"], (name, both["label"], left["label"])
        source = covering(detect.EyeDetection.Source, "conjunction", name, "source_start", "source_stop")
        assert source["source"] == "neither", (name, source)


def test_the_which_eye_trace_is_both_throughout_when_both_eyes_are_usable(stepped_session):
    """Spec section 4: the stepped session withholds nothing, so every
    registered detector's both-eyes trace draws on both eyes throughout."""
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session
    for name in _detector_names():
        where = {**session_key, "trace": "conjunction", **_detector(name)}
        n_samples = (detect.EyeDetection & where).fetch1("n_samples")
        sources = (detect.EyeDetection.Source & where).to_dicts(order_by="source_index")
        assert [(s["source_start"], s["source_stop"], s["source"]) for s in sources] == [(0, n_samples, "both")], name
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py -q -p no:cacheprovider -k "its_own_eyes_row or usable_throughout or missing_gaze_value or left_eye_could_see or neither_eye_is_usable or which_eye_trace_is_both"`
Expected: 9 failed, 1 passed (`test_a_missing_gaze_value_is_withheld_and_counted_end_to_end`, unchanged). The failures:
- five with `TypeError: EyeDetection._insert_trace() got an unexpected keyword argument 'kept'`;
- three with `AttributeError: type object 'EyeDetection' has no attribute 'Source'`;
- one with `AssertionError: ('bmd', 'invalid', 'drift')`: BMD's right eye labels the missing sample `drift`, and the both-eyes trace still reads the left mask's `invalid`.

- [ ] **Step 3: Implement** — apply exactly this diff:

```diff
--- a/wl_preproc/schema/detect.py
+++ b/wl_preproc/schema/detect.py
@@ -432,6 +432,23 @@
         end_x_deg=null     : double
         end_y_deg=null     : double
         direction_deg=null : double
+        """
+
+    class Source(dj.Part):
+        definition = """
+        # Which eye each stretch of the CONJUNCTION trace's labels came from
+        # (design spec `2026-09-28-both-eyes-fallback-design.md` section 4):
+        # `both` where the two-eye rule labelled it, `left` or `right` where
+        # that eye alone did -- a one-eye stretch, or a one-eye event kept
+        # whole at a mask edge -- and `neither` where no eye was usable.
+        # Written for the `conjunction` trace only; the runs tile
+        # [0, n_samples), and `source_stop` is EXCLUSIVE.
+        -> master
+        source_index : int unsigned
+        ---
+        source_start : int unsigned
+        source_stop  : int unsigned
+        source       : enum('both','left','right','neither')
         """
 
     @property
@@ -638,17 +655,44 @@
             # applies WITHIN a kind, so the conjunction trace carries the same
             # vocabulary as the two eyes it is built from. `_overlapping` is
             # the single-kind primitive underneath it.
+            floor = _min_duration_samples(detector_params)
             conjunction_spans = _conjunction_runs(
                 spans["left"],
                 spans["right"],
-                _min_duration_samples(detector_params),
+                floor,
                 _conjunction_label(detector, params, gaze),
             )
-            self._insert_trace(key, "conjunction", gaze, v, offered, conjunction_spans, fs_hz,
-                               detector, detector_params)
+            # Where only one eye is usable, the trace falls back to it, and a
+            # one-eye event the other eye could not have seen is kept whole
+            # (design spec `2026-09-28-both-eyes-fallback-design.md`, the
+            # requester's decisions of 2026-09-28). A gap is filled from the
+            # left eye's mask only where NEITHER eye is usable; one usable
+            # eye makes the gap that eye's `fixation`.
+            kept, source = _conjunction_fallback(
+                spans["left"], spans["right"], per_eye["left"][2], per_eye["right"][2], floor,
+            )
+            fill = np.array(
+                [label if source_label == "neither" else None
+                 for label, source_label in zip(offered, source)],
+                dtype=object,
+            )
+            self._insert_trace(
+                key, "conjunction", gaze, v, fill,
+                [*conjunction_spans,
+                 *_one_eye_pieces(spans["left"], spans["right"], per_eye["left"][2], per_eye["right"][2]),
+                 *(run for _, run in kept)],
+                fs_hz, detector, detector_params,
+                kept={(run.start, run.stop): (eye, *per_eye[eye]) for eye, run in kept},
+                eyes=per_eye,
+            )
+            self.Source.insert(
+                {**key, "trace": "conjunction", "source_index": index,
+                 "source_start": start, "source_stop": stop, "source": value}
+                for index, (start, stop, value) in enumerate(_value_runs(source))
+            )
 
     def _insert_trace(self, key, trace, gaze, v, offered, intervals, fs_hz,
-                      detector, detector_params) -> None:
+                      detector, detector_params, kept=None, eyes=None) -> None:
         """One trace's master row and its runs.
 
         **This method assigns no labels of its own.** Each interval arrives
@@ -800,17 +844,15 @@
         matches neither. Attributing either half's reliability to that run
         would put a fabricated number in the one column a reader consults to
         decide how much to trust a detection -- so the map simply misses and
-        `None` is stored, which is the honest answer. The conjunction trace
-        gets `None` throughout for the same reason it gets its label derived
-        rather than checked: no detector produced it.
+        `None` is stored, which is the honest answer. The conjunction trace's
+        two-eye runs get `None` for the same reason they get their label
+        derived rather than checked: no detector produced them. A one-eye
+        event the conjunction keeps (`kept`) is that eye's own run, and
+        carries its own reliability (design spec
+        `2026-09-28-both-eyes-fallback-design.md` section 2).
         """
         from wl_preproc.eye.detect.measure import measure_event_run
 
-        # The landing rule is per-eye only: a conjunction span is an
-        # intersection and does not end on a detector's knot. The floor
-        # applies to every trace (see this docstring's conjunction
-        # paragraph).
-        runs_end_before_landing = detector.runs_end_before_landing and trace != "conjunction"
         # Read the way `_min_duration_samples` reads a detector's params: a
         # field only NSLR's params declare, so every other detector has no
         # floor on any trace.
@@ -833,16 +875,45 @@
             "n_microsaccades": sum(1 for run in runs if run.label is Label.MICROSACCADE),
             "reason": "",
         })
+
+        # A one-eye event kept in the conjunction (`kept`, keyed by span) is
+        # measured exactly as its own eye's row is: that eye's gaze, velocity
+        # and mask, and that eye's rules. Every other conjunction run, given
+        # both eyes' inputs (`eyes`), is measured on the eye usable
+        # throughout it, the left first; with neither, it is stored
+        # unmeasured (design spec `2026-09-28-both-eyes-fallback-design.md`
+        # section 3).
+        kept = kept or {}
+
+        def _measured_on(run: Run):
+            span = (run.start, run.stop)
+            if span in kept:
+                return kept[span]
+            if eyes is None:
+                return (trace, gaze, v, offered)
+            for eye in ("left", "right"):
+                eye_gaze, eye_v, eye_offered = eyes[eye]
+                if all(label is None for label in eye_offered[run.start:run.stop]):
+                    return (trace, eye_gaze, eye_v, offered)
+            return None
 
         def _run_row(index: int, run: Run) -> dict:
             measurement = None
-            if run.label in (Label.SACCADE, Label.MICROSACCADE):
+            source = _measured_on(run) if run.label in (Label.SACCADE, Label.MICROSACCADE) else None
+            if source is not None:
+                span = (run.start, run.stop)
+                measured_as, run_gaze, run_v, run_offered = source
+                # The landing rule is per-eye only: a two-eye span is an
+                # intersection and does not end on a detector's knot. The
+                # floor applies to every trace (see this docstring's
+                # conjunction paragraph).
                 measurement = measure_event_run(
-                    gaze, v, offered, run.start, run.stop, fs_hz,
-                    runs_end_before_landing=runs_end_before_landing,
+                    run_gaze, run_v, run_offered, run.start, run.stop, fs_hz,
+                    runs_end_before_landing=(detector.runs_end_before_landing
+                                             and measured_as != "conjunction"),
                     min_measured_ms=min_measured_ms,
                     runs_start_after_takeoff=_measured_from_takeoff(
-                        detector, trace, run.label, reliability_by_span.get((run.start, run.stop))
+                        detector, measured_as, run.label, reliability_by_span.get(span)
                     ),
                 )
             return {
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_schema.py tests/schema/test_detect_populate.py tests/schema/test_eye_populate.py tests/schema/test_consensus_populate.py tests/cli -q -p no:cacheprovider`
Expected: all pass, with every existing test unchanged. Spec §6 names three of them:
- the right-eye-only phantom step, with both eyes usable, is still not in the both-eyes trace: `test_the_conjunction_requires_temporal_overlap_in_both_eyes`;
- two-eye events are unchanged: `test_engbert_kliegl_conjunction_rows_are_unchanged`, with `test_the_conjunction_trace_never_carries_a_borrowed_reliability`;
- every per-eye row is unchanged: `_insert_trace`'s per-eye call passes neither `kept` nor `eyes`, so it measures exactly as before, which every per-eye test holds.

A refused eye still refuses the conjunction: `test_a_refused_left_eye_leaves_the_right_eye_computed` and `test_a_refused_right_eye_leaves_the_left_eye_computed`.

- [ ] **Step 5: Mutation checks.** Each must fail the test named; each was measured to.
  - In `make()`, `[label if source_label == "neither" else None` → `[None`: `test_where_neither_eye_is_usable_the_both_eyes_trace_keeps_the_left_masks_label`.
  - Delete the `*_one_eye_pieces(...)` line from `make()`'s `_insert_trace` call: `test_no_detector_labels_a_missing_gaze_value`.
  - In `_measured_on`, `            if span in kept:` → `            if False:`: `test_a_kept_one_eye_event_is_stored_as_its_own_eyes_row`, both cases.
  - `if all(label is None for label in eye_offered[run.start:run.stop]):` → `if eye == "left":`: `test_a_conjunction_run_is_measured_on_the_eye_usable_throughout_it[left-right]` and `[both-None]`.
  - `and measured_as != "conjunction"),` → `and trace != "conjunction"),`: `test_a_kept_one_eye_event_is_stored_as_its_own_eyes_row[nslr-...]` and `test_the_both_eyes_trace_keeps_a_step_only_the_left_eye_could_see`.
  - `detector, measured_as, run.label, reliability_by_span.get(span)` → `detector, trace, ...`: `test_a_kept_one_eye_event_is_stored_as_its_own_eyes_row[bmd-...]`. The database test cannot catch this one: at the planted steps, BMD stores the saccade it copies from Engbert–Kliegl, which carries no reliability, so the take-off rule does not apply to it.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/detect.py tests/schema/test_detect_populate.py
git commit -m "feat(eye): the both-eyes trace falls back to the usable eye, keeps a one-eye event the other eye could not see, and stores which eye each stretch came from -- the requester's decisions

<trailer lines>"
```

---

### Task 3: The recording, the records, and the full suite

**Files:**
- Modify: `tests/eye/detect/test_nystrom_holmqvist_validation.py` (appended)
- Modify: `docs/superpowers/specs/2026-09-05-conjunction-shape-design.md`, `docs/superpowers/specs/2026-08-31-saccade-detection-design.md`, `docs/handoffs/2026-09-28-why-one-eye-alone.md`, `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-09-28-both-eyes-fallback.md`

**Ruling (this plan): the gated check covers Nyström–Holmqvist alone.** It is the detector this file's `reference` fixture already runs, in 13 s. The six-detector numbers in spec §2 and §5 were measured by a probe, not a kept test; BMD alone takes about 9 minutes on the recording. Cost if wrong: a later change to another detector's kept count goes unnoticed until someone re-measures.

- [ ] **Step 1: The gated check.** Append to `tests/eye/detect/test_nystrom_holmqvist_validation.py`:

```python
@pytest.mark.skipif(
    not os.environ.get("WLPP_OHDPI_REFERENCE"),
    reason="needs the real reference recording",
)
def test_the_both_eyes_fallback_on_the_reference_recording(reference, capsys):
    """The both-eyes trace's fallback to the usable eye (design spec
    `2026-09-28-both-eyes-fallback-design.md` section 6), on this detector's
    two eyes. Recorded, and one thing asserted: after the usable-data guard
    no run covers a withheld sample, so the which-eye trace is `neither`
    exactly where both eyes' masks withhold. Measured 2026-09-28: 542 left
    and 186 right one-eye saccades kept, and the trace is `both` on 93.84%,
    `left` 2.36%, `right` 1.59% and `neither` 2.20%."""
    from wl_preproc.schema.detect import _conjunction_fallback

    left, right = reference["traces"]
    kept, source = _conjunction_fallback(left.runs, right.runs, left.mask, right.mask,
                                         NH_CONJUNCTION_FLOOR_SAMPLES)
    kinds = Counter((eye, _kind_of(run.label)) for eye, run in kept)
    shares = {value: float(np.mean(source == value)) for value in ("both", "left", "right", "neither")}
    with capsys.disabled():
        print(f"\n  both-eyes fallback (Nystrom-Holmqvist): one-eye saccades kept, left "
              f"{kinds['left', 'saccadic']}, right {kinds['right', 'saccadic']}; all kept runs {len(kept)}")
        print("  which eye: " + ", ".join(f"{value} {share:.2%}" for value, share in shares.items()))
    neither = np.array([a is not None and b is not None for a, b in zip(left.mask, right.mask)])
    assert np.array_equal(source == "neither", neither)
```

Run it without the recording, as CI does: `.venv/bin/python -m pytest tests/eye/detect/test_nystrom_holmqvist_validation.py -q -p no:cacheprovider -k both_eyes_fallback`
Expected: 1 skipped.

Then with it: prefix `WLPP_OHDPI_REFERENCE=$HOME/Downloads/Tutorial/OpenIris-2024Jul31-114628/OpenIris-2024Jul31-114628.txt` and add `-s`.
Expected: 1 passed, printing `one-eye saccades kept, left 542, right 186; all kept runs 768` and `which eye: both 93.84%, left 2.36%, right 1.59%, neither 2.20%`.

Never commit the recording.

- [ ] **Step 2: Amend the conjunction-shape spec.** In `docs/superpowers/specs/2026-09-05-conjunction-shape-design.md`, directly under `## 1. The rule`'s first paragraph (the one ending "and it carries that kind's label."), add:

  > *Amended 2026-09-28 (spec `2026-09-28-both-eyes-fallback-design.md`, the requester's decisions): this rule holds where both eyes are usable. Where only one eye is usable, the conjunction carries that eye's own labels. A one-eye event is kept whole when the other eye's data was withheld during it and the other eye has no counterpart of its kind. `EyeDetection.Source` records which eye each stretch came from.*

- [ ] **Step 3: Amend the parent spec.** In `docs/superpowers/specs/2026-08-31-saccade-detection-design.md` §4, after the `> **Amended 2026-09-05 by the conjunction-shape design.**` block, add:

  > *Amended 2026-09-28 (spec `2026-09-28-both-eyes-fallback-design.md`, the requester's decisions): "never a silent monocular fallback wearing a binocular name" still holds for a session with a refused eye, which keeps its refused conjunction row. Within a session where both eyes calibrated, the conjunction now falls back to the usable eye where the other's data is withheld, and a one-eye event the other eye could not have seen is kept whole. The fallback is not silent: `EyeDetection.Source` names the eye each stretch came from, `both`, `left`, `right` or `neither`.*

- [ ] **Step 4: Mark the question answered.** In `docs/handoffs/2026-09-28-why-one-eye-alone.md` §4, after the paragraph beginning "**The requester chose on 2026-09-28 to fall back to the good eye there**", add:

  > *Built 2026-09-28 on `spec/both-eyes-fallback`; see handoff `2026-09-28-both-eyes-fallback.md`. The "missingness trace for each eye" was ruled into one "which eye" trace, `EyeDetection.Source` (the requester's choice the same day).*

- [ ] **Step 5: The handoff.** Create `docs/handoffs/2026-09-28-both-eyes-fallback.md`:

```markdown
# The both-eyes trace falls back to the usable eye, and says which eye it used

Branch `spec/both-eyes-fallback`, forked from `main` at `978a3a3`. Spec
`docs/superpowers/specs/2026-09-28-both-eyes-fallback-design.md`; plan
`docs/superpowers/plans/2026-09-28-both-eyes-fallback.md`.

The requester's decisions of 2026-09-28, after handoff
`2026-09-28-why-one-eye-alone.md` found that half of the saccades the
binocular rule drops sat where the other eye had no usable data:
- **fall back to the usable eye** where only one eye has data;
- **keep a one-eye event whole** when the other eye's data was missing at
  any point during it;
- **store one "which eye" trace** with the both-eyes detection.

---

## 1. What was built

`schema/detect.py`:
- `_conjunction_fallback` decides which one-eye events are kept whole, and
  which eye each sample draws on. `_one_eye_pieces` cuts each eye's runs to
  where it alone is usable. `_value_runs` turns the per-sample array into
  stored runs.
- `EyeDetection.make()` hands those runs to `_insert_trace` beside the
  two-eye runs, which are unchanged. It fills a gap from the left eye's
  mask only where neither eye is usable.
- `EyeDetection.Source`, a new part table, tiles the conjunction trace
  with `both`, `left`, `right` or `neither`.
- `_insert_trace` measures a kept event as its own eye's row, with that
  eye's rules and its own reliability. It measures every other conjunction
  run on the eye usable throughout it, the left first, and leaves one with
  neither unmeasured.

## 2. What it changes, on the reference recording

Measured after the usable-data guard. One-eye saccades now kept whole in
the both-eyes trace, left/right:

| Detector | Left | Right |
|---|---|---|
| Engbert–Kliegl | 793 | 513 |
| Otero-Millan | 599 | 201 |
| Nyström–Holmqvist | 542 | 186 |
| REMoDNaV | 587 | 191 |
| NSLR | 1,091 | 785 |
| BMD | 762 | 382 |

For Nyström–Holmqvist the which-eye trace is `both` on 93.84% of samples,
`left` 2.36%, `right` 1.59% and `neither` 2.20%. For every detector it
is `neither` on 2.20%, where both masks withhold.

## 3. Rulings

- **Any two candidates from the two eyes that overlap or touch are both
  dropped** (spec §2, widened while building): 0–36 pairs per detector.
- **A refused eye keeps the refused both-eyes row** (spec §2).
- **A two-eye run joined by its one-eye continuation is one run,**
  measured on the eye usable throughout it (spec §3).
- **The other eye's run of another kind is no counterpart** (this plan's
  Review Focus 2). For BMD, NSLR and REMoDNaV it is usually that eye's
  background label, `drift` or pursuit, which the conjunction intersects:
  451, 257 and 71 kept runs overlap one. Dropping them would make the
  fallback depend on which detector ran.
- **The gated check covers Nyström–Holmqvist alone** (plan, Task 3).

## 4. Tests

- 11 tests of the three functions, and 8 of `_insert_trace` and the stored
  rows, including a session with the right eye withheld over a planted step
  and both eyes over a quiet stretch.
- One existing test changed its expectation:
  `test_no_detector_labels_a_missing_gaze_value`. The missing value is in
  the left eye alone, so the both-eyes trace now carries the right eye's
  label there.
- A gated check on the reference recording,
  `test_the_both_eyes_fallback_on_the_reference_recording`.

## 5. What is next

The requester's choice. The known open items in `docs/CHECKPOINT.md`:
- the one-sample conjunction floor that three detectors inherit, a
  cross-detector decision still open;
- the suite's missing session-date allocator;
- the items blocked on the rig and the compute machine.
```

- [ ] **Step 6: CHECKPOINT.** In `docs/CHECKPOINT.md`'s "Start here" item 4:
  - After "built on `fix/runs-stay-on-usable-data` and NOT merged as written.", add:

    *Merged 2026-09-28 as `978a3a3`: CI green on both interpreters, 1733 passed on each with 31 skipped (`gh run view 36415852458`), and the manifest check green. The measurement itself merged earlier the same day as `a80e061`, CI green.*
  - Replace the paragraph beginning "**Next, the requester's decision of 2026-09-28: the both-eyes trace**" with:

    **The both-eyes trace now falls back to the usable eye (the requester's decisions of 2026-09-28), BUILT on `spec/both-eyes-fallback` and NOT merged as written.** Where only one eye is usable, it takes that eye's labels. A one-eye event the other eye could not have seen is kept whole: 542 left and 186 right saccades for Nyström–Holmqvist, and 191–1,091 per eye for the other detectors. `EyeDetection.Source` records which eye each stretch came from. See `docs/handoffs/2026-09-28-both-eyes-fallback.md`.

    *Until then this paragraph said the both-eyes trace holds no event where one eye is missing, and labels that gap from the left eye's mask alone; true when written.*

  Do not re-point the header; that happens at merge, with CI read.

- [ ] **Step 7: `wl.yaml`.** In `status`, item (4):
  - change "(branch fix/runs-stay-on-usable-data, NOT merged as written)" to "(MERGED 2026-09-28 as 978a3a3)";
  - replace the sentence beginning "Next, the requester's decision the same day: the both-eyes trace" with: "The requester's decision the same day, that the both-eyes trace falls back to the usable eye and says which eye it used, is BUILT on spec/both-eyes-fallback, NOT merged as written."

  Then run `.venv/bin/wl-check` **on its own** and read its exit status before committing. `describes` is not touched here. If an all-digit SHA is ever written to it, quote it.

- [ ] **Step 8: Full suite on both interpreters, once.** Run with `WLPP_BMD_REFERENCE=$SCRATCH/bmd/BMD`, `WLPP_BMD_BOOST_INCLUDE=$SCRATCH/bmd/boost_1_86_0` and `WLPP_NSLR_REFERENCE=$SCRATCH` set, and `WLPP_OHDPI_REFERENCE` unset, as CI has it:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
$SCRATCH/venv_ci/bin/python -m pytest -q -p no:cacheprovider
```

Expected: both green. CI on `978a3a3` gave 1733 passed and 31 skipped on each interpreter. This plan adds 19 passing tests (11 in Task 1, 8 in Task 2) and one gated one that skips here. So expect 19 more passed and 1 more skipped than `main` gives on the same interpreter. Then commit:

```bash
git add tests/eye/detect/test_nystrom_holmqvist_validation.py docs wl.yaml
git commit -m "docs: the both-eyes fallback built -- what it keeps per detector, the which-eye trace, and its rulings

<trailer lines>"
```
