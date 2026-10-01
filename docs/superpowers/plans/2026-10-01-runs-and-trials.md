# Runs and Trials from a Real wl-xcon Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A real wl-xcon recording gives wl-preproc its measured runs, its blocks and its trials, joined to the rig's record, with nothing dropped silently.

**Architecture:**
- **Trials join the rig's record by `trial_number`**, wl-xcon's XC-155 field. `events/rigtrials.py` keys each line by it, and `nwb/conditions.py` joins on that key.
- **The requester's vocabulary (2026-10-01): a run holds blocks, and a block is a stretch of trials under one block type.** Blocks come from our existing `BLOCK_START`/`BLOCK_END`, which wl-xcon sends per block. **Runs come from two new protocol values**: a `RUN_START` escape (`0x8006`, carrying the run number and task code) and a `RUN_END` marker (4). They are measured into a new table, `core.Run`.
- **A run or block that never closed** (a run that faults sends neither end) ends where the next one starts; its stop is its last event; and a faulted trial's inferred stop stays inside it.
- **`populate_session`** stores the first trial with each number and leaves out a number above element-event's smallint. The description names both.
- **The generator** gains wl-xcon's shapes: the XC-155 record, faulted blocks and trials, chosen trial numbers, and runs.

**Tech Stack:** Python ≥3.11; DataJoint 2.3 with MySQL; element-event (adopted, pinned); pydantic 2 (the recipe); pynwb 4.1 (the file).

**Spec:** `docs/superpowers/specs/2026-10-01-runs-and-trials-design.md` (`43543ad`, revised as `3d3cc85` when the requester ruled the vocabulary). It is binding. The requester approved it on 2026-10-01 and chose to send wl-xcon its asks before this plan; wl-xcon accepted all three. Task 6 adds the spec's dated amendments, which record the rulings below.

**Every piece of code below was proven before this plan was written.** It was built in a scratch worktree on a branch of its own, one commit per task.
- **Each task's failing run** was measured on the previous task's code plus this task's tests.
- **Its passing run** was measured on its own commit.
- **Every mutation check named here** was run against the final tree, and each failed its test.
- **The full suite** was run on both interpreters with every task applied (Task 6 quotes it).

## Global Constraints

- **The spec is binding,** including the dated amendments Task 6 adds. Where it and this plan disagree, the spec wins; record a ruling.
- **The requester's decisions (spec §0):**
  - runs and trials first;
  - the recording identifies each run and block itself: `BLOCK_START` per block, and the new run escape and marker per run;
  - one sync-box recording holds one animal;
  - a crash is corrected at its source by wl-xcon's XC-026, and wl-preproc names any repeat and never drops one silently.
- **A trial's id is the number the rig strobed, or the trial is not stored.** Nothing is renumbered, and nothing is matched by position.
- **One frozen interface changes, by addition only:** `contracts/events.py` gains `Escape.RUN_START = 0x8006` (`(run_number, task_type_code)`) and `Marker.RUN_END = 4`. No value is renumbered, and the decoder's framing is not changed: it decodes the new escape as it decodes every escape.
- **element-event's tables are adopted, not changed.** `trial_id` and `block_id` stay smallints.
- **No JSON schema changes.** `docs/schemas/*` are regenerated once, in Task 5 after the last contract change, to confirm it.
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code, data or tables.
- **Tests.**
  - Run with `.venv/bin/python -m pytest <files> -q -p no:cacheprovider` from the repository root. In a git worktree, prefix `PYTHONPATH=$PWD`.
  - Database tests need Docker (OrbStack on this machine: `open -a OrbStack` after a reboot). A module-wide setup error on a 120 s container timeout is the known flake: re-run it.
  - Mutation checks:
    - clear `__pycache__` first, and again after restoring;
    - run with `PYTHONDONTWRITEBYTECODE=1`;
    - make one mutation at a time, and restore the file afterwards.
  - **Run each task's own test files.** The full suite runs once, on both interpreters, in Task 6.
  - The shell is zsh:
    - an unquoted `$VAR` holding several arguments is not split;
    - arrays start at 1;
    - a pipe through `tail` or `grep` hides the test command's exit status, so gate on the command's own status.
- **`wl-check` after `wl.yaml` changes,** on its own (Task 6).
- **Every commit message ends with:**

  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R
  ```

## Review Focus

Five inputs the spec implies but its own test list (§7) does not name, most likely first. Each is pinned by a test named here, in the task that owns the code.

1. **A run that faults and the next run starts.** Each block keeps its own trials, the faulted trial's stop stays inside its block, and the run's stop is its last event. This is wl-xcon's own warning, reproduced. Task 2: `test_an_unclosed_block_keeps_its_own_trials_and_no_later_ones` and `test_populate_session_stores_a_faulted_run_and_its_trial_with_no_outcome`; Task 5: `test_populate_session_measures_each_run_into_core_run`.
2. **A rig record mixing lines with and without `trial_number`.** The numbered lines join, and the others are reported by line. Task 1: `test_a_line_naming_a_run_without_its_number_is_left_out`.
3. **A repeat whose first occurrence is the one stored, not the later one.** Task 3: `test_populate_session_stores_the_first_of_a_repeated_number_and_none_too_large`, which checks the stored trial's start time.
4. **A repeated or too-large number in the description, and no other note.** Task 4: `test_the_description_names_a_repeated_number_and_one_too_large` asserts the exact list.
5. **The mismatched-line fault under the new key.** One `trial_number` named twice and one not at all joins neither. Task 1: `tests/synth/test_faults.py`'s existing test, which mutation T1d proves fails when the fault copies `index` instead.

## The spec's §6, verified at `3d3cc85`

1. **No existing test pins the old reach of an unclosed block.** Three touch an unclosed trial or block, and all stay green unchanged:
   - `test_a_genuinely_truncated_trial_falls_back_to_the_last_stream_event` has no block, so the new cap never applies;
   - `test_an_abandoned_trial_whose_block_closed_prefers_the_block_boundary` has a closed block;
   - `test_an_abandoned_trial_whose_block_never_closed_falls_to_the_next_trial` has its next trial in the same block.

   `tests/events/test_assemble.py` leaves no block open. Task 2 adds tests; it changes none.
2. **Every `TRIAL_NUMBER` is stored, with a `trial_id` attribute.** `populate_session` writes one `Event` row per escape at its own time, and one `Event.Attribute` row `trial_id` beside it (`schema/events.py`, the `Escape.TRIAL_NUMBER` branch). Each occurrence has its own time, so a repeat is a row of its own. Task 4 reads them.
3. **The notes are assembled in `nwb/describe.py`:** `"notes": [*data.condition_notes, *data.probe_notes]`, from `Gathered`, which `nwb/gather.py` fills. The NWB file has no notes field of its own (amendment 1). Task 4 adds the trial notes between the two.
4. **`nwb/conditions.py::join` keyed on `RigTrial.index`** (`by_index`). Task 1 keys it on `RigTrial.number`.
5. **`synth/peripherals.py::write_rig_trials` wrote the pre-XC-155 shape:** `index` was the trial's id, with no `run` and no `trial_number`. Task 1 writes XC-155's shape.

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/events/rigtrials.py`, `wl_preproc/nwb/conditions.py` | the rig record keyed by `trial_number`, and the join on it (Task 1) |
| `wl_preproc/synth/peripherals.py`, `synth/faults.py` | the XC-155 record shape (Task 1); no line for a faulted trial (Task 2) |
| `wl_preproc/synth/recipe.py`, `synth/timeline.py` | `unclosed_blocks`, `faulted_trials` (Task 2); `trial_numbers` (Task 3); `runs` (Task 5) |
| `wl_preproc/events/assemble.py`, `wl_preproc/schema/events.py` | an unclosed block's last event, containment and stops (Task 2); repeats and the smallint ceiling (Task 3); runs (Task 5) |
| `wl_preproc/contracts/events.py`, `wl_preproc/schema/core.py` | the run escape and marker, and `core.Run` (Task 5) |
| `wl_preproc/nwb/gather.py`, `nwb/describe.py`, `contracts/nwb_description.py` | the trial notes (Task 4) |
| docs, `wl.yaml` | the asks to wl-xcon, the answer to wl.works, the amendments and records (Task 6) |

---

### Task 1: Trials join the rig's record by `trial_number`

**Files:**
- Modify: `wl_preproc/events/rigtrials.py`, `wl_preproc/nwb/conditions.py`, `wl_preproc/synth/peripherals.py`, `wl_preproc/synth/faults.py`
- Test: `tests/events/test_rigtrials.py`, `tests/nwb/test_conditions.py`, `tests/synth/test_peripherals.py`

**Interfaces — produces:**
- `RigTrial.number: int`, its first field: the line's `trial_number`, or its `index` for a record from before XC-155. `RigTrial.index` stays the line's own index.
- `nwb.conditions.join` keys on `RigTrial.number`.
- The synthetic `xcon/trials.jsonl` lines carry `run` (`block_id - 1`), a per-run `index` and `trial_number` (the trial's id).

**Why the key is `trial_number`.** wl-xcon's XC-155, on its `main` at `eeec053`: every line carries `trial_number`, an integer counted from 1 across the session and equal to the stream's `TRIAL_NUMBER`, while `index` restarts in each run. A line that names a run but carries no number has no key across the session, so it is reported and left out; a record from before XC-155 still joins by `index`.

- [ ] **Step 1: Write the failing tests.** Apply these diffs:

```diff
--- a/tests/events/test_rigtrials.py
+++ b/tests/events/test_rigtrials.py
@@ -25,8 +25,10 @@ def test_each_line_is_one_of_this_subjects_trials(tmp_path):
     session = _record(tmp_path, [_line(0), _line(1, subject="other"), _line(2, contrast=0.25)])
     record = read_rig_trials(session, "pico")
     assert record.trials == (
-        RigTrial(index=0, outcome="correct", block="main", condition="contrast-50", params={"contrast": 0.5}),
-        RigTrial(index=2, outcome="correct", block="main", condition="contrast-50", params={"contrast": 0.25}),
+        RigTrial(number=0, index=0, outcome="correct", block="main", condition="contrast-50",
+                 params={"contrast": 0.5}),
+        RigTrial(number=2, index=2, outcome="correct", block="main", condition="contrast-50",
+                 params={"contrast": 0.25}),
     )
     assert record.problems == ()
 
@@ -48,15 +50,34 @@ def test_a_session_without_a_record_has_none(tmp_path):
     assert read_rig_trials(tmp_path, "pico") is None
 
 
-def test_a_record_that_numbers_trials_within_each_run_is_not_read(tmp_path):
-    """Since wl-xcon's slice b3a-1 a session holds several runs: each line names
-    its run, and each run counts its trials from 0. The stream's TRIAL_NUMBER
-    is numbered across the session (wl-xcon XC-155), so a per-run index is not
-    its join key -- index 1 here is unique, yet it is the second run's second
-    trial, not the session's second. No line is read, and the problem says why."""
+def _line155(trial_number, run, index, **fields):
+    """A line as wl-xcon writes it since XC-155: its run, its index within the
+    run, and its number across the session."""
+    return json.dumps({**json.loads(_line(index, **fields)), "run": run, "trial_number": trial_number})
+
+
+def test_a_line_since_xc155_is_keyed_by_its_trial_number(tmp_path):
+    """wl-xcon XC-155: each line's `trial_number` counts from 1 across the
+    session and equals the stream's TRIAL_NUMBER, while `index` restarts in
+    each run. The number is the key, so the second run's first trial is 3,
+    not 0 (design spec `2026-10-01-runs-and-trials-design.md` section 3.1)."""
+    from wl_preproc.events.rigtrials import read_rig_trials
+
+    rows = [_line155(1, 0, 0), _line155(2, 0, 1), _line155(3, 1, 0)]
+    record = read_rig_trials(_record(tmp_path, rows), "pico")
+    assert [(trial.number, trial.index) for trial in record.trials] == [(1, 0), (2, 1), (3, 0)]
+    assert record.problems == ()
+
+
+def test_a_line_naming_a_run_without_its_number_is_left_out(tmp_path):
+    """A run-numbered line with no `trial_number` has no session-wide key,
+    so it joins nothing and the record says why; the lines that carry the
+    number are still read. A number that is not an integer is a problem too."""
     from wl_preproc.events.rigtrials import read_rig_trials
 
-    rows = [json.dumps({**json.loads(_line(index)), "run": run}) for run, index in ((0, 0), (1, 0), (1, 1))]
+    rows = [json.dumps({**json.loads(_line(0)), "run": 0}), _line155(2, 0, 1),
+            json.dumps({**json.loads(_line155(3, 0, 2)), "trial_number": "3"})]
     record = read_rig_trials(_record(tmp_path, rows), "pico")
-    assert record.trials == ()
-    assert len(record.problems) == 1 and "within each run" in record.problems[0]
+    assert [trial.number for trial in record.trials] == [2]
+    assert [problem.split(":")[0] for problem in record.problems] == ["line 1", "line 3"]
+    assert "trial_number" in record.problems[0]
```

```diff
--- a/tests/nwb/test_conditions.py
+++ b/tests/nwb/test_conditions.py
@@ -13,7 +13,7 @@ def _trial(trial_id, start_s, outcome="correct"):
 def _rig(index, condition, **params):
     from wl_preproc.events.rigtrials import RigTrial
 
-    return RigTrial(index=index, outcome="correct", block="main", condition=condition, params=params)
+    return RigTrial(number=index, index=index, outcome="correct", block="main", condition=condition, params=params)
 
 
 def test_trials_join_the_rig_record_by_trial_number_only():
@@ -63,6 +63,19 @@ def test_a_blocks_conditions_are_what_ran_by_settings():
     ]
 
 
+def test_the_join_is_on_the_lines_number_not_its_index():
+    """Since XC-155 a line's index restarts in each run; only its number is
+    the stream's TRIAL_NUMBER (design spec `2026-10-01-runs-and-trials-design.md`
+    section 3.1)."""
+    from wl_preproc.events.rigtrials import RigRecord, RigTrial
+    from wl_preproc.nwb.conditions import join
+
+    line = RigTrial(number=7, index=0, outcome="correct", block="main", condition="a", params={})
+    matched, notes = join([_trial(7, 0.0), _trial(0, 1.0)], RigRecord(trials=(line,), problems=()))
+    assert matched == {7: line}
+    assert notes == ["1 trial(s) have no single line in the rig record"]
+
+
 def test_without_the_rig_record_a_condition_is_its_stream_number():
     from wl_preproc.nwb.conditions import block_conditions
 
```

```diff
--- a/tests/synth/test_peripherals.py
+++ b/tests/synth/test_peripherals.py
@@ -139,8 +139,15 @@ def test_the_rig_record_has_wl_xcons_line_shape(tmp_path):
     (tmp_path / "xcon").mkdir()
     write_rig_trials(tmp_path / "xcon" / "trials.jsonl", CI_RECIPE, truth)
     lines = [json.loads(line) for line in (tmp_path / "xcon" / "trials.jsonl").read_text().splitlines()]
-    assert all(set(line) == {"index", "subject", "outcome", "block", "condition", "params"} for line in lines)
+    assert all(set(line) == {"index", "run", "trial_number", "subject", "outcome", "block", "condition", "params"}
+               for line in lines)
+    # Since XC-155: runs count from 0 in wl-xcon, indexes restart in each
+    # run, and the number counts across the session (design spec
+    # `2026-10-01-runs-and-trials-design.md` section 3.1).
+    assert [(line["run"], line["index"]) for line in lines] == [
+        (trial.block_id - 1, sum(1 for t in truth.trials[:position] if t.block_id == trial.block_id))
+        for position, trial in enumerate(truth.trials)]
     record = read_rig_trials(tmp_path, CI_RECIPE.subject)
     assert record.problems == ()
-    assert [trial.index for trial in record.trials] == [trial.trial_id for trial in truth.trials]
+    assert [trial.number for trial in record.trials] == [trial.trial_id for trial in truth.trials]
     assert {trial.condition for trial in record.trials} <= {"contrast-10", "contrast-25", "contrast-50", "contrast-100"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/events/test_rigtrials.py tests/nwb/test_conditions.py tests/synth/test_peripherals.py tests/synth/test_faults.py -q --tb=line -p no:cacheprovider`
Expected: 9 failed, 23 passed. Five join tests and one rig-record test build a `RigTrial` with a `number` it does not have yet (`TypeError: RigTrial.__init__`); the two keying tests find no trial (`assert [] == [2]`); and the generator's line has no `trial_number`. `tests/synth/test_faults.py` passes, since its fault still copies the field the old record is keyed by.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/events/rigtrials.py
+++ b/wl_preproc/events/rigtrials.py
@@ -14,12 +14,15 @@ reported as a problem, by line number, and left out; nothing is guessed. Two
 animals routinely share one day's session directory, so the record carries the
 subject on every line and only this subject's lines are kept.
 
-**A record that numbers trials within each run is not read at all.** Since
-wl-xcon's slice b3a-1 a session holds several runs, each line names its run,
-and each run counts its trials from 0; the stream's `TRIAL_NUMBER` is numbered
-across the session instead (wl-xcon XC-155). A per-run `index` is then not the
-join key even where it is unique, so no line is read until this module reads
-the key XC-155 records.
+**Each line is keyed by its `trial_number`** (design spec
+`2026-10-01-runs-and-trials-design.md` section 3.1). Since wl-xcon's slice
+b3a-1 a session holds several runs, each line names its run, and each run
+counts its `index` from 0; since its XC-155 each line also carries
+`trial_number`, counted from 1 across the session and equal to the stream's
+`TRIAL_NUMBER`. That number is the key. A record from before XC-155, whose
+lines name no run, is keyed by `index`, which then counted across the
+session. A line that names a run but carries no `trial_number` has no key
+across the session, so it is reported and left out.
 """
 
 from __future__ import annotations
@@ -36,6 +39,7 @@ _FIELDS = ("index", "subject", "outcome", "block", "condition", "params")
 
 @dataclasses.dataclass(frozen=True, slots=True)
 class RigTrial:
+    number: int  # the stream's TRIAL_NUMBER: `trial_number`, or `index` before XC-155
     index: int
     outcome: str
     block: str
@@ -55,28 +59,34 @@ def read_rig_trials(session_dir: Path, subject: str) -> RigRecord | None:
     path = Path(session_dir) / XCON_DIRNAME / RECORD_NAME
     if not path.is_file():
         return None
-    trials, problems, per_run = [], [], False
-    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
+    trials, problems = [], []
+    for number_of_line, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
         if not line.strip():
             continue
         try:
             row = json.loads(line)
         except json.JSONDecodeError:
-            problems.append(f"line {number}: not JSON")
+            problems.append(f"line {number_of_line}: not JSON")
             continue
         if not isinstance(row, dict) or any(field not in row for field in _FIELDS):
-            problems.append(f"line {number}: missing one of {', '.join(_FIELDS)}")
+            problems.append(f"line {number_of_line}: missing one of {', '.join(_FIELDS)}")
             continue
         if not isinstance(row["index"], int) or isinstance(row["index"], bool) or not isinstance(row["params"], dict):
-            problems.append(f"line {number}: index is not an integer or params is not an object")
+            problems.append(f"line {number_of_line}: index is not an integer or params is not an object")
             continue
         if row["subject"] != subject:
             continue
-        per_run = per_run or "run" in row
-        trials.append(RigTrial(index=row["index"], outcome=str(row["outcome"]), block=str(row["block"]),
-                               condition=str(row["condition"]), params=row["params"]))
-    if per_run:
-        return RigRecord(trials=(), problems=(*problems, (
-            "the rig record numbers trials within each run (its lines name a run) and the stream "
-            "numbers them across the session (wl-xcon XC-155), so no line is joined")))
+        if "trial_number" in row:
+            number = row["trial_number"]
+            if not isinstance(number, int) or isinstance(number, bool):
+                problems.append(f"line {number_of_line}: trial_number is not an integer")
+                continue
+        elif "run" in row:
+            problems.append(f"line {number_of_line}: it names a run but carries no trial_number, so it has no "
+                            "number across the session and joins nothing")
+            continue
+        else:
+            number = row["index"]
+        trials.append(RigTrial(number=number, index=row["index"], outcome=str(row["outcome"]),
+                               block=str(row["block"]), condition=str(row["condition"]), params=row["params"]))
     return RigRecord(trials=tuple(trials), problems=tuple(problems))
```

```diff
--- a/wl_preproc/nwb/conditions.py
+++ b/wl_preproc/nwb/conditions.py
@@ -34,12 +34,12 @@ def join(trials: list[dict], record: RigRecord | None) -> tuple[dict[int, RigTri
     if record is None:
         return {}, ["no rig trial record (xcon/trials.jsonl)"]
     notes = list(record.problems)
-    counts = collections.Counter(trial.index for trial in record.trials)
-    by_index = {trial.index: trial for trial in record.trials if counts[trial.index] == 1}
-    repeated = sorted(index for index, count in counts.items() if count > 1)
+    counts = collections.Counter(trial.number for trial in record.trials)
+    by_number = {trial.number: trial for trial in record.trials if counts[trial.number] == 1}
+    repeated = sorted(number for number, count in counts.items() if count > 1)
     if repeated:
         notes.append(f"{len(repeated)} trial number(s) appear more than once in the rig record")
-    matched = {trial["trial_id"]: by_index[trial["trial_id"]] for trial in trials if trial["trial_id"] in by_index}
+    matched = {trial["trial_id"]: by_number[trial["trial_id"]] for trial in trials if trial["trial_id"] in by_number}
     unmatched = len(trials) - len(matched)
     if unmatched:
         notes.append(f"{unmatched} trial(s) have no single line in the rig record")
```

```diff
--- a/wl_preproc/synth/peripherals.py
+++ b/wl_preproc/synth/peripherals.py
@@ -168,12 +168,19 @@ def rig_condition(trial_id: int) -> tuple[str, dict]:
 
 def write_rig_trials(path: Path, recipe: SessionRecipe, truth: GroundTruth) -> None:
     """Stands in for wl-xcon's `xcon/trials.jsonl`: one JSON object per line,
-    per trial, with the subject on every line."""
-    lines = []
+    per trial, with the subject on every line. In wl-xcon's shape since its
+    XC-155: the run (counted from 0, as wl-xcon counts them; one run per
+    block), the trial's index within its run, and `trial_number`, counted
+    across the session and equal to the stream's TRIAL_NUMBER."""
+    lines, per_run = [], {}
     for trial in truth.trials:
         condition, params = rig_condition(trial.trial_id)
+        index = per_run.get(trial.block_id, 0)
+        per_run[trial.block_id] = index + 1
         lines.append({
-            "index": trial.trial_id,
+            "index": index,
+            "run": trial.block_id - 1,
+            "trial_number": trial.trial_id,
             "subject": recipe.subject,
             "outcome": "correct",
             "block": f"block-{trial.block_id}",
```

```diff
--- a/wl_preproc/synth/faults.py
+++ b/wl_preproc/synth/faults.py
@@ -138,7 +138,8 @@ def mismatch_rig_line(lines: list[dict]) -> list[dict]:
     """The rig's trial record with its second line carrying the first line's
     trial number: one number named twice, one not at all. Joining by trial
     number must attach neither trial's condition (design spec
-    `2026-09-29-nwb-publishing-design.md` section 13)."""
+    `2026-09-29-nwb-publishing-design.md` section 13). The number is
+    `trial_number`, the join key since wl-xcon's XC-155."""
     if len(lines) < 2:
         return list(lines)
-    return [lines[0], {**lines[1], "index": lines[0]["index"]}, *lines[2:]]
+    return [lines[0], {**lines[1], "trial_number": lines[0]["trial_number"]}, *lines[2:]]
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 32 passed.

Then the builder's database tests, which join conditions end to end through the new shape: `.venv/bin/python -m pytest tests/schema/test_nwb_build.py -q -p no:cacheprovider`. Expected: 62 passed.

- [ ] **Step 5: Mutation checks.** Each was measured to fail the tests named in brackets.
  - T1a (`rigtrials.py`): `if "trial_number" in row:` becomes `if False:` [`test_a_line_since_xc155_is_keyed_by_its_trial_number`, `test_a_line_naming_a_run_without_its_number_is_left_out`].
  - T1b: `elif "run" in row:` becomes `elif False:` [`test_a_line_naming_a_run_without_its_number_is_left_out`].
  - T1c (`conditions.py`): the join keys on `trial.index` [`test_the_join_is_on_the_lines_number_not_its_index`].
  - T1d (`faults.py`): the mismatched line copies `index` rather than `trial_number` [`test_a_mismatched_rig_line_joins_neither_trial_it_confuses`].
  - T1e (`peripherals.py`): `"trial_number": trial.trial_id` becomes `"trial_number": index` [`test_the_rig_record_has_wl_xcons_line_shape`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/events/rigtrials.py wl_preproc/nwb/conditions.py wl_preproc/synth/peripherals.py wl_preproc/synth/faults.py tests/events/test_rigtrials.py tests/nwb/test_conditions.py tests/synth/test_peripherals.py
git commit -m "feat(events): the rig's trial record is keyed by trial_number, wl-xcon's XC-155 field -- a record from before it by index; the synthetic record gains XC-155's shape

<trailer lines>"
```

---

### Task 2: A block that never closed keeps only its own trials

**Files:**
- Modify: `wl_preproc/events/assemble.py`, `wl_preproc/schema/events.py`, `wl_preproc/synth/recipe.py`, `wl_preproc/synth/timeline.py`, `wl_preproc/synth/peripherals.py`
- Test: `tests/events/test_assemble.py`, `tests/schema/test_events.py`, `tests/synth/test_timeline.py`

**Interfaces — consumes:** Task 1's synthetic record shape.

**Interfaces — produces:**
- `AssembledBlock.last_s: float`: its `BLOCK_END` when it closed, else its last event before the next `BLOCK_START` or the end of the stream.
- `schema.events._containing_block` bounds an unclosed block by the next block's start; `_block_stop_time(block)` (one argument now) returns `end_s`, else `last_s`; `_trial_stop_time` caps an inferred stop at the containing block's stop.
- `SessionRecipe.unclosed_blocks: tuple[int, ...]` (block numbers from 1) and `SessionRecipe.faulted_trials: tuple[int, ...]` (trial positions from 1). A faulted trial sends no outcome and no `TRIAL_END`, and has no rig line, though it still uses its index in the run.

**Why.** wl-xcon's warning, reproduced here as the containment test's failure `[1, 1, 1]`: a run that faults sends no `BLOCK_END`, so `assemble()` leaves its block open, `_containing_block` reached it to the end of the stream, and every later trial landed in it. A faulted trial's inferred stop then ran across the gap to the next run's first trial. Each run in these tests holds one block, so the run's fault leaves its one block open. `test_an_abandoned_trial_whose_block_never_closed_falls_to_the_next_trial` stays green: its next trial is in the same block, so the cap leaves it unchanged.

- [ ] **Step 1: Write the failing tests.** Apply these diffs:

```diff
--- a/tests/synth/test_timeline.py
+++ b/tests/synth/test_timeline.py
@@ -124,3 +124,48 @@ def test_code_words_never_overlap_on_the_bus():
     times = [t for t, _ in build_timeline(CI_RECIPE).code_words]
     assert times == sorted(times)
     assert len(set(times)) == len(times)
+
+
+# -- Runs that fault, and trials that fault (design spec
+# `2026-10-01-runs-and-trials-design.md` section 7): wl-xcon's own shapes.
+
+
+def _recipe(**update):
+    from wl_preproc.synth.recipe import CI_RECIPE, SessionRecipe
+
+    return SessionRecipe.model_validate({**CI_RECIPE.model_dump(), **update})
+
+
+def test_an_unclosed_block_and_a_faulted_trial_send_what_wl_xcon_would():
+    """A run that faults sends no BLOCK_END; a trial that faults sends its
+    TRIAL_START and number, then no outcome and no TRIAL_END (wl-xcon's
+    message of 2026-10-01). The third trial is block 1's last."""
+    from wl_preproc.contracts.events import Marker
+
+    clean, faulted = build_timeline(_recipe()), build_timeline(_recipe(unclosed_blocks=[1], faulted_trials=[3]))
+    count = lambda truth, marker: sum(1 for _time, word in truth.code_words if word == marker.value)
+    assert count(clean, Marker.BLOCK_END) - count(faulted, Marker.BLOCK_END) == 1
+    assert count(clean, Marker.TRIAL_END) - count(faulted, Marker.TRIAL_END) == 1
+    assert count(clean, Marker.TRIAL_CORRECT) - count(faulted, Marker.TRIAL_CORRECT) == 1
+    assert count(clean, Marker.TRIAL_START) == count(faulted, Marker.TRIAL_START)
+
+
+def test_a_faulted_trial_has_no_line_in_the_rig_record(tmp_path):
+    import json
+
+    from wl_preproc.synth.peripherals import write_rig_trials
+
+    recipe = _recipe(faulted_trials=[3])
+    (tmp_path / "trials.jsonl").parent.mkdir(exist_ok=True)
+    write_rig_trials(tmp_path / "trials.jsonl", recipe, build_timeline(recipe))
+    numbers = [json.loads(line)["trial_number"] for line in (tmp_path / "trials.jsonl").read_text().splitlines()]
+    assert numbers == [1, 2, 4]
+
+
+@pytest.mark.parametrize("update, expect", [
+    ({"unclosed_blocks": [3]}, "unclosed_blocks names block 3"),
+    ({"faulted_trials": [5]}, "faulted_trials names trial 5"),
+])
+def test_a_fault_naming_nothing_the_session_has_is_refused(update, expect):
+    with pytest.raises(ValueError, match=expect):
+        _recipe(**update)
```

```diff
--- a/tests/events/test_assemble.py
+++ b/tests/events/test_assemble.py
@@ -90,3 +90,18 @@ def test_decode_errors_are_kept_rather_than_dropped():
     words = [(0.0, Escape.TRIAL_NUMBER.value), (0.001, 1), (0.002, 0xDEAD)]  # truncated: only 2 of the 3 required words follow the escape
     result = assemble.assemble(_stream(words))
     assert result.errors, "a decode error must reach the assembly's error list"
+
+
+def test_an_unclosed_block_records_its_last_event():
+    """A run that faults sends no BLOCK_END, so the next BLOCK_START finds it
+    open. Its last event is what the recording proves of its end (design
+    spec `2026-10-01-runs-and-trials-design.md` section 2.3)."""
+    words = [*encode_payload(Escape.BLOCK_START, [1, 0]), Marker.TRIAL_START.value,
+             *encode_payload(Escape.TRIAL_NUMBER, [0, 1]),
+             *encode_payload(Escape.BLOCK_START, [2, 0]), Marker.TRIAL_START.value,
+             *encode_payload(Escape.TRIAL_NUMBER, [0, 2]), Marker.TRIAL_END.value, Marker.BLOCK_END.value]
+    pairs = [(0.001 * i, word) for i, word in enumerate(words)]
+    first, second = assemble.assemble(_stream(pairs)).blocks
+    # A payload event is timed at its escape word: trial 1's TRIAL_NUMBER.
+    assert first.end_s is None and first.last_s == pairs[5][0]
+    assert second.end_s == second.last_s == pairs[-1][0]
```

```diff
--- a/tests/schema/test_events.py
+++ b/tests/schema/test_events.py
@@ -412,3 +412,82 @@ def test_a_trial_whose_real_end_arrives_after_its_block_closed():
         trial, 0, assembly.trials, containing_block=containing, stream_end_s=stream_end_s
     )
     assert stop == trial.end_s
+
+
+# -- A block that never closed, because its run faulted (design spec `2026-10-01-runs-and-trials-design.md`
+# section 2.3, wl-xcon's warning of 2026-10-01).
+
+
+def _faulted_run_stream():
+    """Run 1: trial 1 complete, trial 2 faults (number, then nothing) and
+    the run sends no BLOCK_END. Run 2: trial 3 complete, closed."""
+    from wl_preproc.contracts.events import Escape, Marker, decode_stream, encode_payload
+
+    words = [*encode_payload(Escape.BLOCK_START, [1, 0]),
+             Marker.TRIAL_START.value, *encode_payload(Escape.TRIAL_NUMBER, [0, 1]),
+             Marker.TRIAL_CORRECT.value, Marker.TRIAL_END.value,
+             Marker.TRIAL_START.value, *encode_payload(Escape.TRIAL_NUMBER, [0, 2]),
+             *encode_payload(Escape.BLOCK_START, [2, 0]),
+             Marker.TRIAL_START.value, *encode_payload(Escape.TRIAL_NUMBER, [0, 3]),
+             Marker.TRIAL_CORRECT.value, Marker.TRIAL_END.value, Marker.BLOCK_END.value]
+    return decode_stream(_stream_at_1ms(words))
+
+
+def test_an_unclosed_block_keeps_its_own_trials_and_no_later_ones():
+    """Today the open block reaches the end of the stream and takes trial 3,
+    which is block 2's. It ends, for containment, where block 2 starts. Each
+    run here has one block, as every wl-xcon run has until its day plan."""
+    from wl_preproc.events.assemble import assemble
+    from wl_preproc.schema.events import _containing_block
+
+    decoded = _faulted_run_stream()
+    assembly = assemble(decoded)
+    stream_end_s = max(item.time_s for item in decoded)
+    assert [_containing_block(trial, assembly.blocks, stream_end_s).block_id for trial in assembly.trials] == [1, 1, 2]
+
+
+def test_a_faulted_trial_stops_inside_its_own_block():
+    """Its inferred stop is capped at its block's stop, the block's last
+    event, rather than reaching across the gap to the next run's first
+    trial."""
+    from wl_preproc.events.assemble import assemble
+    from wl_preproc.schema.events import _block_stop_time, _containing_block, _trial_stop_time
+
+    decoded = _faulted_run_stream()
+    assembly = assemble(decoded)
+    stream_end_s = max(item.time_s for item in decoded)
+    run_one = assembly.blocks[0]
+    faulted = assembly.trials[1]
+    assert faulted.end_s is None and faulted.outcome is None
+    stop = _trial_stop_time(faulted, 1, assembly.trials,
+                            _containing_block(faulted, assembly.blocks, stream_end_s), stream_end_s)
+    assert stop == run_one.last_s == _block_stop_time(run_one)
+    assert stop < assembly.blocks[1].start_s
+
+
+def test_populate_session_stores_a_faulted_run_and_its_trial_with_no_outcome(events_activated, dj_conn, tmp_path):
+    """Through the generator and the database: block 1 sends no BLOCK_END
+    and its last trial (trial 3) faults. Each block keeps its own trials,
+    and the faulted trial stores with no outcome, stopping inside block 1."""
+    from wl_preproc.schema import pipeline
+    from wl_preproc.synth.recipe import CI_RECIPE, SessionRecipe
+    from wl_preproc.synth.session import generate_session
+
+    recipe = SessionRecipe.model_validate({**CI_RECIPE.model_dump(), "subject": "rtfault1",
+                                           "unclosed_blocks": [1], "faulted_trials": [3]})
+    generate_session(tmp_path, recipe)
+    pipeline.lab.Lab.insert1({"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
+                             skip_duplicates=True)
+    pipeline.subject.Subject.insert1({"subject": "rtfault1", "sex": "M", "subject_birth_date": datetime.date(2020, 1, 1),
+                                      "subject_description": ""}, skip_duplicates=True)
+    key = {"subject": "rtfault1", "session_datetime": datetime.datetime(2027, 3, 23, 9, 0)}
+    pipeline.Session.insert1(key, skip_duplicates=True)
+
+    events.populate_session(key, tmp_path / recipe.session_id)
+
+    pairs = {(row["block_id"], row["trial_id"]) for row in (pipeline.trial.BlockTrial & key).to_dicts()}
+    assert pairs == {(1, 1), (1, 2), (1, 3), (2, 4)}
+    faulted = (pipeline.trial.Trial & key & {"trial_id": 3}).fetch1()
+    block_one, block_two = (pipeline.trial.Block & key).to_dicts(order_by="block_id")
+    assert faulted["trial_type"] is None
+    assert faulted["trial_stop_time"] <= block_one["block_stop_time"] < block_two["block_start_time"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/synth/test_timeline.py tests/events/test_assemble.py tests/schema/test_events.py -q --tb=line -p no:cacheprovider`
Expected: 8 failed, 25 passed. `SessionRecipe` refuses `unclosed_blocks` and `faulted_trials` as extra inputs, so two timeline tests and the database test fail on it, and the two refusals do not match their own words. `AssembledBlock` has no `last_s` (the assembler test and the faulted-trial stop test). The containment test gives `[1, 1, 1]` for `[1, 1, 2]`: every trial after the unclosed block lands in it.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/synth/recipe.py
+++ b/wl_preproc/synth/recipe.py
@@ -196,6 +196,15 @@ class SessionRecipe(BaseModel):
     # `2026-09-30-nwb-probes-design.md` section 7).
     extra_probes: tuple[ProbeSpec, ...] = ()
     spikeglx_restart: RestartSpec | None = None
+    # wl-xcon's fault shapes (design spec `2026-10-01-runs-and-trials-design.md`
+    # section 7), both empty by default so every existing profile is
+    # byte-identical. `unclosed_blocks`: block numbers (from 1) whose BLOCK_END
+    # is never sent, as a run that faults sends none. `faulted_trials`: trial
+    # positions (from 1, across the session) that send their TRIAL_START and
+    # number and then nothing -- no outcome, no TRIAL_END -- and leave no line
+    # in the rig's record.
+    unclosed_blocks: tuple[int, ...] = ()
+    faulted_trials: tuple[int, ...] = ()
 
     # How many neurons this session contains. Zero is legal and is what every
     # timing-only fixture wants: Phase 1c's recipes care about barcodes and
@@ -360,6 +369,13 @@ class SessionRecipe(BaseModel):
         duplicated = sorted({serial for serial in serials if serials.count(serial) > 1})
         if duplicated:
             raise ValueError(f"the run names serial {duplicated[0]} twice; one probe cannot be two streams")
+        for block in self.unclosed_blocks:
+            if not 1 <= block <= len(self.blocks):
+                raise ValueError(f"unclosed_blocks names block {block}, and the session has {len(self.blocks)}")
+        n_trials = sum(block.n_trials for block in self.blocks)
+        for trial in self.faulted_trials:
+            if not 1 <= trial <= n_trials:
+                raise ValueError(f"faulted_trials names trial {trial}, and the session has {n_trials}")
         restart = self.spikeglx_restart
         banks = [(probe.part_number, probe.bank) for probe in self.extra_probes]
         if restart is not None and restart.probe_bank is not None:
```

```diff
--- a/wl_preproc/synth/timeline.py
+++ b/wl_preproc/synth/timeline.py
@@ -157,8 +157,11 @@ def build_timeline(recipe: SessionRecipe) -> GroundTruth:
             # TRIAL_END moving later: this way neither the next trial's own
             # TRIAL_START nor this block's BLOCK_END (both ratcheted off the
             # last word placed before them, in `_emit`) shift by this change.
-            _emit(words, trial_end - 2 * CODE_WORD_SPACING_S, Marker.TRIAL_CORRECT.value)
-            _emit(words, trial_end - CODE_WORD_SPACING_S, Marker.TRIAL_END.value)
+            # A faulted trial (wl-xcon's shape) sends no outcome and no
+            # TRIAL_END; its time still passes.
+            if len(trials) not in recipe.faulted_trials:
+                _emit(words, trial_end - 2 * CODE_WORD_SPACING_S, Marker.TRIAL_CORRECT.value)
+                _emit(words, trial_end - CODE_WORD_SPACING_S, Marker.TRIAL_END.value)
             cursor = trial_end
             trial_id += 1
 
@@ -170,7 +173,9 @@ def build_timeline(recipe: SessionRecipe) -> GroundTruth:
                 end_s=cursor,
             )
         )
-        _emit(words, cursor - CODE_WORD_SPACING_S / 2, Marker.BLOCK_END.value)
+        # A run that faults sends no BLOCK_END (wl-xcon's rule for its RUN_END).
+        if block_index not in recipe.unclosed_blocks:
+            _emit(words, cursor - CODE_WORD_SPACING_S / 2, Marker.BLOCK_END.value)
 
     _emit(words, recipe.duration_s, Marker.SESSION_END.value)
 
```

```diff
--- a/wl_preproc/synth/peripherals.py
+++ b/wl_preproc/synth/peripherals.py
@@ -173,10 +173,12 @@ def write_rig_trials(path: Path, recipe: SessionRecipe, truth: GroundTruth) -> N
     block), the trial's index within its run, and `trial_number`, counted
     across the session and equal to the stream's TRIAL_NUMBER."""
     lines, per_run = [], {}
-    for trial in truth.trials:
-        condition, params = rig_condition(trial.trial_id)
+    for position, trial in enumerate(truth.trials, start=1):
         index = per_run.get(trial.block_id, 0)
-        per_run[trial.block_id] = index + 1
+        per_run[trial.block_id] = index + 1  # a faulted trial still used its index
+        if position in recipe.faulted_trials:
+            continue  # a trial that faults leaves no line (wl-xcon, 2026-10-01)
+        condition, params = rig_condition(trial.trial_id)
         lines.append({
             "index": index,
             "run": trial.block_id - 1,
```

```diff
--- a/wl_preproc/events/assemble.py
+++ b/wl_preproc/events/assemble.py
@@ -16,7 +16,7 @@ session with decode errors is a tier-D candidate that silence would hide.
 
 from __future__ import annotations
 
-from dataclasses import dataclass, field
+from dataclasses import dataclass, field, replace
 
 from wl_preproc.contracts.events import (
     DecodeError,
@@ -49,7 +49,13 @@ class AssembledBlock:
     block_id: int
     task_type: int
     start_s: float
-    end_s: float | None
+    end_s: float | None  # its BLOCK_END, or None when it never closed
+    # The last event received while it was open: its BLOCK_END when it closed,
+    # otherwise the last code before the next BLOCK_START or the end of the
+    # stream. For a run that faulted -- wl-xcon sends no BLOCK_END then -- it
+    # is the tightest bound the recording gives on its end (design spec
+    # `2026-10-01-runs-and-trials-design.md` section 2.3).
+    last_s: float
 
 
 @dataclass
@@ -72,6 +78,7 @@ def assemble(events: list[DecodedEvent]) -> Assembly:
     open_trial_id: int | None = None
     open_outcome: str | None = None
     open_block: AssembledBlock | None = None
+    last_s = 0.0  # the time of the last event before the one in hand
 
     def close_trial(end_s: float | None) -> None:
         nonlocal open_trial_start, open_trial_id, open_outcome
@@ -101,19 +108,22 @@ def assemble(events: list[DecodedEvent]) -> Assembly:
                     open_trial_start = event.time_s
             elif event.escape is Escape.BLOCK_START:
                 if open_block is not None:
-                    result.blocks.append(open_block)
+                    result.blocks.append(replace(open_block, last_s=last_s))
                 open_block = AssembledBlock(
                     block_id=event.words[0],
                     task_type=event.words[1],
                     start_s=event.time_s,
                     end_s=None,
+                    last_s=event.time_s,
                 )
+            last_s = event.time_s
             continue
 
         if isinstance(event, SimpleEvent):
             try:
                 marker = Marker(event.code)
             except ValueError:
+                last_s = event.time_s
                 continue  # a task event, not a marker; Event rows keep it
             if marker is Marker.TRIAL_START:
                 close_trial(end_s=None)
@@ -123,17 +133,11 @@ def assemble(events: list[DecodedEvent]) -> Assembly:
             elif marker is Marker.TRIAL_END:
                 close_trial(end_s=event.time_s)
             elif marker is Marker.BLOCK_END and open_block is not None:
-                result.blocks.append(
-                    AssembledBlock(
-                        block_id=open_block.block_id,
-                        task_type=open_block.task_type,
-                        start_s=open_block.start_s,
-                        end_s=event.time_s,
-                    )
-                )
+                result.blocks.append(replace(open_block, end_s=event.time_s, last_s=event.time_s))
                 open_block = None
+            last_s = event.time_s
 
     close_trial(end_s=None)
     if open_block is not None:
-        result.blocks.append(open_block)
+        result.blocks.append(replace(open_block, last_s=last_s))
     return result
```

```diff
--- a/wl_preproc/schema/events.py
+++ b/wl_preproc/schema/events.py
@@ -186,9 +186,19 @@ def _containing_block(
     `events/agreement.py`'s job, fed into tier resolution by Task 9, not this
     module's. So the containing block is recovered from the two assembled
     interval lists directly.
+
+    **A block that never closed ends where the next one starts** (design spec
+    `2026-10-01-runs-and-trials-design.md` section 2.3). A run that faults
+    sends no BLOCK_END (wl-xcon's rule), and reaching to the end of the
+    stream instead made it the first block holding every later trial's start.
     """
-    for block in blocks:
-        block_end = block.end_s if block.end_s is not None else stream_end_s
+    for position, block in enumerate(blocks):
+        if block.end_s is not None:
+            block_end = block.end_s
+        elif position + 1 < len(blocks):
+            block_end = blocks[position + 1].start_s
+        else:
+            block_end = stream_end_s
         if block.start_s <= trial.start_s < block_end:
             return block
     return None
@@ -252,6 +262,13 @@ def _trial_stop_time(
     4. The last event time in the whole decoded stream -- the only branch a
        trial truncated with nothing recorded after it can ever reach.
 
+    **Branches 3 and 4 are capped at the containing block's stop, closed or
+    not** (design spec `2026-10-01-runs-and-trials-design.md` section 3.3).
+    A trial that faults is in a run that faulted, which sent no BLOCK_END, so
+    branch 2 does not apply and the next trial is in the next run, after the
+    gap between runs. An unclosed block's stop is its last event, so the
+    inferred stop stays inside the trial's own run.
+
     **Fix round 3: branch 1 is exempt from the invariant below, and returns
     before reaching it.** `_containing_block` decides containment by START
     time alone (`block.start_s <= trial.start_s < block_end`), and -- as fix
@@ -291,6 +308,8 @@ def _trial_stop_time(
         stop = ordered_trials[trial_index + 1].start_s
     else:
         stop = stream_end_s
+    if containing_block is not None:
+        stop = min(stop, _block_stop_time(containing_block))
 
     if containing_block is not None and containing_block.end_s is not None:
         assert stop <= containing_block.end_s, (
@@ -302,17 +321,18 @@ def _trial_stop_time(
     return stop
 
 
-def _block_stop_time(block: AssembledBlock, stream_end_s: float) -> float:
-    """`block.end_s`, or the last known event time when `BLOCK_END` never
-    arrived.
+def _block_stop_time(block: AssembledBlock) -> float:
+    """`block.end_s`, or the block's own last event when `BLOCK_END` never
+    arrived: the tightest bound the recording gives (design spec
+    `2026-10-01-runs-and-trials-design.md` section 2.3). `block_stop_time` is
+    not nullable, so something concrete must be written however the run
+    ended.
 
-    Every block in this project's current fixtures DOES carry an explicit
-    `BLOCK_END` (checked in `synth/timeline.py`), so this path is defensive
-    rather than exercised today -- kept for the same reason `_trial_stop_time`
-    has one: `block_stop_time` is not nullable, so something concrete must be
-    written regardless of how the stream ended.
+    *This used to fall back to the last event of the whole stream, reasoning
+    that every fixture's blocks closed. A run that faults sends no BLOCK_END,
+    and the stream's last event is then in a later run.*
     """
-    return block.end_s if block.end_s is not None else stream_end_s
+    return block.end_s if block.end_s is not None else block.last_s
 
 
 def populate_session(key: dict, session_dir: Path) -> None:
@@ -483,7 +503,7 @@ def populate_session(key: dict, session_dir: Path) -> None:
             **session_key,
             "block_id": block.block_id,
             "block_start_time": block.start_s,
-            "block_stop_time": _block_stop_time(block, stream_end_s),
+            "block_stop_time": _block_stop_time(block),
         }
         for block in assembly.blocks
     ]
```

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/synth tests/events tests/schema/test_events.py tests/schema/test_coverage.py tests/schema/test_timebase.py tests/schema/test_nwb_build.py -q --tb=line -p no:cacheprovider`
Expected: 304 passed.

- [ ] **Step 5: Mutation checks.**
  - T2a (`assemble.py`): the open block is appended without its `last_s` [`test_an_unclosed_block_records_its_last_event`].
  - T2b (`schema/events.py`): `elif position + 1 < len(blocks):` becomes `elif False:` [`test_an_unclosed_block_keeps_its_own_trials_and_no_later_ones`, `test_populate_session_stores_a_faulted_run_and_its_trial_with_no_outcome`].
  - T2c: the cap `stop = min(stop, _block_stop_time(containing_block))` becomes `stop = stop` [`test_a_faulted_trial_stops_inside_its_own_block`, `test_populate_session_stores_a_faulted_run_and_its_trial_with_no_outcome`].
  - T2d: an unclosed block's stop becomes its `start_s` [`test_a_faulted_trial_stops_inside_its_own_block`].
  - T2e (`timeline.py`): `if block_index not in recipe.unclosed_blocks:` becomes `if True:` [`test_an_unclosed_block_and_a_faulted_trial_send_what_wl_xcon_would`, `test_runs_wrap_each_block_and_a_faulted_run_sends_no_end`].
  - T2f: `if len(trials) not in recipe.faulted_trials:` becomes `if True:` [`test_an_unclosed_block_and_a_faulted_trial_send_what_wl_xcon_would`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/events/assemble.py wl_preproc/schema/events.py wl_preproc/synth/recipe.py wl_preproc/synth/timeline.py wl_preproc/synth/peripherals.py tests/events/test_assemble.py tests/schema/test_events.py tests/synth/test_timeline.py
git commit -m "feat(events): a block that never closed keeps only its own trials -- it ends where the next block starts, its stop is its last event, and a faulted trial's inferred stop stays inside it; the generator gains wl-xcon's faulted runs and trials

<trailer lines>"
```

---

### Task 3: A repeated number, and one too large to store

**Files:**
- Modify: `wl_preproc/schema/events.py`, `wl_preproc/synth/recipe.py`, `wl_preproc/synth/timeline.py`
- Test: `tests/schema/test_events.py`, `tests/synth/test_timeline.py`

**Interfaces — produces:**
- `schema.events.TRIAL_ID_MAX = 32767`.
- `populate_session` stores the first trial with each number and none above `TRIAL_ID_MAX`. Every strobed number stays in `Event` with its `trial_id` attribute.
- `SessionRecipe.trial_numbers: tuple[int, ...]`: each trial's strobed number when given, one per trial.

**Why the first is kept explicitly.** A batch insert with `skip_duplicates` already keeps the first row, but `populate_session` now says so in code. The mutation that proves the test (T3b) reverses the order, since deleting the check alone changes nothing.

**Why the ceiling.** element-event's `trial_id` is a smallint. MySQL refuses a larger id ("Out of range value for column 'trial_id'", 1264, measured), which failed the whole batch on every pass.

`test_a_hang_stores_with_no_outcome_and_its_recorded_end` passes before this task's code, by design. It pins today's behaviour for wl-xcon's question 3.

- [ ] **Step 1: Write the failing tests.** Apply these diffs:

```diff
--- a/tests/synth/test_timeline.py
+++ b/tests/synth/test_timeline.py
@@ -169,3 +169,19 @@ def test_a_faulted_trial_has_no_line_in_the_rig_record(tmp_path):
 def test_a_fault_naming_nothing_the_session_has_is_refused(update, expect):
     with pytest.raises(ValueError, match=expect):
         _recipe(**update)
+
+
+def test_a_fixture_can_strobe_the_numbers_it_names():
+    """`trial_numbers` sets each trial's strobed TRIAL_NUMBER, so a fixture
+    can repeat one or exceed what element-event can store (design spec
+    `2026-10-01-runs-and-trials-design.md` section 7)."""
+    from wl_preproc.events.assemble import assemble
+
+    truth = build_timeline(_recipe(trial_numbers=[1, 2, 2, 40000]))
+    assert [trial.trial_id for trial in truth.trials] == [1, 2, 2, 40000]
+    assert [trial.trial_id for trial in assemble(decode_stream(list(truth.code_words))).trials] == [1, 2, 2, 40000]
+
+
+def test_trial_numbers_must_name_every_trial():
+    with pytest.raises(ValueError, match="trial_numbers names 3 trials, and the session has 4"):
+        _recipe(trial_numbers=[1, 2, 3])
```

```diff
--- a/tests/schema/test_events.py
+++ b/tests/schema/test_events.py
@@ -491,3 +491,47 @@ def test_populate_session_stores_a_faulted_run_and_its_trial_with_no_outcome(eve
     block_one, block_two = (pipeline.trial.Block & key).to_dicts(order_by="block_id")
     assert faulted["trial_type"] is None
     assert faulted["trial_stop_time"] <= block_one["block_stop_time"] < block_two["block_start_time"]
+
+
+def test_a_hang_stores_with_no_outcome_and_its_recorded_end():
+    """wl-xcon's hang: no outcome marker, then TRIAL_END (design spec
+    `2026-10-01-runs-and-trials-design.md` section 3.1)."""
+    from wl_preproc.contracts.events import Escape, Marker, decode_stream, encode_payload
+    from wl_preproc.events.assemble import assemble
+    from wl_preproc.schema.events import _trial_stop_time
+
+    words = [Marker.TRIAL_START.value, *encode_payload(Escape.TRIAL_NUMBER, [0, 1]), Marker.TRIAL_END.value]
+    decoded = decode_stream(_stream_at_1ms(words))
+    (trial,) = assemble(decoded).trials
+    assert (trial.outcome, trial.end_s) == (None, 0.005)
+    assert _trial_stop_time(trial, 0, [trial], None, 0.005) == 0.005
+
+
+def test_populate_session_stores_the_first_of_a_repeated_number_and_none_too_large(events_activated, dj_conn,
+                                                                                  tmp_path):
+    """Design spec sections 3.2 and 3.4: the first trial with a number is
+    stored and its repeat is not; a number above element-event's smallint is
+    left out rather than failing the session; every strobed number stays in
+    `Event`, so both are recoverable for the file's notes."""
+    from wl_preproc.schema import pipeline
+    from wl_preproc.synth.recipe import CI_RECIPE, SessionRecipe
+    from wl_preproc.synth.session import generate_session
+
+    recipe = SessionRecipe.model_validate({**CI_RECIPE.model_dump(), "subject": "rtnums1",
+                                           "trial_numbers": [1, 2, 2, 40000]})
+    truth = generate_session(tmp_path, recipe)
+    pipeline.lab.Lab.insert1({"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
+                             skip_duplicates=True)
+    pipeline.subject.Subject.insert1({"subject": "rtnums1", "sex": "M", "subject_birth_date": datetime.date(2020, 1, 1),
+                                      "subject_description": ""}, skip_duplicates=True)
+    key = {"subject": "rtnums1", "session_datetime": datetime.datetime(2027, 3, 24, 9, 0)}
+    pipeline.Session.insert1(key, skip_duplicates=True)
+
+    events.populate_session(key, tmp_path / recipe.session_id)
+
+    stored = (pipeline.trial.Trial & key).to_dicts(order_by="trial_id")
+    assert [row["trial_id"] for row in stored] == [1, 2]
+    assert stored[1]["trial_start_time"] == pytest.approx(truth.trials[1].start_s, abs=0.01)  # the first 2
+    strobed = sorted(int(value) for value in (pipeline.event.Event.Attribute & key
+                                              & {"attribute_name": "trial_id"}).to_arrays("attribute_value"))
+    assert strobed == [1, 2, 2, 40000]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/synth/test_timeline.py tests/schema/test_events.py -q --tb=line -p no:cacheprovider`
Expected: 3 failed, 30 passed. `SessionRecipe` refuses `trial_numbers` as an extra input, so the timeline test and the database test fail on it, and the refusal does not match its own words. `test_a_hang_stores_with_no_outcome_and_its_recorded_end` passes, by design.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/synth/recipe.py
+++ b/wl_preproc/synth/recipe.py
@@ -205,6 +205,10 @@ class SessionRecipe(BaseModel):
     # in the rig's record.
     unclosed_blocks: tuple[int, ...] = ()
     faulted_trials: tuple[int, ...] = ()
+    # Each trial's strobed TRIAL_NUMBER, in order, when given; otherwise 1, 2,
+    # 3, ... A fixture uses it to repeat a number, as a crash and restart
+    # without wl-xcon's XC-026 would, or to exceed element-event's smallint.
+    trial_numbers: tuple[int, ...] = ()
 
     # How many neurons this session contains. Zero is legal and is what every
     # timing-only fixture wants: Phase 1c's recipes care about barcodes and
@@ -376,6 +380,10 @@ class SessionRecipe(BaseModel):
         for trial in self.faulted_trials:
             if not 1 <= trial <= n_trials:
                 raise ValueError(f"faulted_trials names trial {trial}, and the session has {n_trials}")
+        if self.trial_numbers and len(self.trial_numbers) != n_trials:
+            raise ValueError(f"trial_numbers names {len(self.trial_numbers)} trials, and the session has {n_trials}")
+        if any(not 0 <= number < 2**32 for number in self.trial_numbers):
+            raise ValueError("trial_numbers must fit TRIAL_NUMBER's uint32")
         restart = self.spikeglx_restart
         banks = [(probe.part_number, probe.bank) for probe in self.extra_probes]
         if restart is not None and restart.probe_bank is not None:
```

```diff
--- a/wl_preproc/synth/timeline.py
+++ b/wl_preproc/synth/timeline.py
@@ -120,6 +120,8 @@ def build_timeline(recipe: SessionRecipe) -> GroundTruth:
             _emit(words, block_start, word)
 
         for _ in range(block.n_trials):
+            if recipe.trial_numbers:
+                trial_id = recipe.trial_numbers[len(trials)]
             trial_start = cursor
             trial_end = cursor + block.trial_duration_s
             trials.append(
```

```diff
--- a/wl_preproc/schema/events.py
+++ b/wl_preproc/schema/events.py
@@ -335,6 +335,15 @@ def _block_stop_time(block: AssembledBlock) -> float:
     return block.end_s if block.end_s is not None else block.last_s
 
 
+# element-event's `trial_id` is a smallint (`trial_id : smallint # trial
+# number (1-based indexing)`), and the stream's TRIAL_NUMBER is a uint32.
+# MySQL refuses a larger id ("Out of range value for column 'trial_id'",
+# 1264, measured 2026-10-01), which failed the whole batch. Such trials are
+# left out instead (design spec `2026-10-01-runs-and-trials-design.md`
+# section 3.4).
+TRIAL_ID_MAX = 32767
+
+
 def populate_session(key: dict, session_dir: Path) -> None:
     """Populate one session's `BehaviorRecording`, `EventType`, `Event`,
     `Trial`, `TrialType`, `Block` and `BlockTrial` from the sync box's decoded
@@ -474,7 +483,16 @@ def populate_session(key: dict, session_dir: Path) -> None:
     # need each trial's containing block.
     trial_rows: list[dict] = []
     block_trial_rows: list[dict] = []
+    stored: set[int] = set()
     for index, trial in enumerate(assembly.trials):
+        # The first trial with a number is stored and a repeat is not; a
+        # number element-event cannot hold is left out. Both stay in Event,
+        # where the file's notes find them (design spec
+        # `2026-10-01-runs-and-trials-design.md` sections 3.2 and 3.4).
+        # Nothing is renumbered.
+        if trial.trial_id in stored or trial.trial_id > TRIAL_ID_MAX:
+            continue
+        stored.add(trial.trial_id)
         containing_block = _containing_block(trial, assembly.blocks, stream_end_s)
         trial_rows.append(
             {
```

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/synth tests/events tests/schema/test_events.py -q --tb=line -p no:cacheprovider`
Expected: 227 passed.

- [ ] **Step 5: Mutation checks.**
  - T3a (`schema/events.py`): the ceiling is dropped from the skip [`test_populate_session_stores_the_first_of_a_repeated_number_and_none_too_large`].
  - T3b: the trials are walked in reverse, so the last of a repeat is kept [`test_populate_session_stores_the_first_of_a_repeated_number_and_none_too_large`].
  - T3c (`timeline.py`): `trial_id = recipe.trial_numbers[len(trials)]` becomes `trial_id = trial_id` [`test_a_fixture_can_strobe_the_numbers_it_names`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/events.py wl_preproc/synth/recipe.py wl_preproc/synth/timeline.py tests/schema/test_events.py tests/synth/test_timeline.py
git commit -m "feat(events): the first trial with a number is stored and a repeat is not; a number above element-event's smallint is left out instead of failing the session -- nothing renumbered, every number kept in Event

<trailer lines>"
```

---

### Task 4: The description names what the trials leave out

**Files:**
- Modify: `wl_preproc/nwb/gather.py`, `wl_preproc/nwb/describe.py`, `wl_preproc/contracts/nwb_description.py` (a comment only)
- Test: `tests/nwb/test_describe.py`; create `tests/schema/test_nwb_trials.py`

**Interfaces — consumes:** Task 3's `TRIAL_ID_MAX` and `trial_numbers`; `tests/schema/test_spikeglx_restart.py::_session`, `tests/schema/test_nwb_probes.py::_canonical`.

**Interfaces — produces:** `Gathered.trial_notes: list[str]`; the description's `notes` are the condition notes, then the trial notes, then the probe notes.

**Why the database test has no task file.** `TimingProvenance.trial_count_agreement` compares stored trials with a task file in the synthetic format. Real wl-xcon sessions have none, so theirs is null. The synthetic one would list four trials against two stored and put the session at tier D (spec amendment 2).

**Why the test asserts the exact list of trial notes.** A membership check would pass a stray extra note. T4c, `elif here:`, adds one for trial 1 and is caught only because the list is exact.

**Why the notes read `Event`.** Every strobed `TRIAL_NUMBER` is stored there with its `trial_id`, whether or not its trial was. So a repeat and a too-large number are recoverable for the file's blocks without a new table.

- [ ] **Step 1: Write the failing tests.** Apply this diff, and create the file below it:

```diff
--- a/tests/nwb/test_describe.py
+++ b/tests/nwb/test_describe.py
@@ -31,12 +31,12 @@ CHECKSUM = {"dataset_path": "/intervals/trials/start_time", "dtype": "float64",
             "sha256": "0" * 64, "paired_with": ""}
 
 
-def _gathered(eye=None, notes=(), probes=(), probe_notes=()):
+def _gathered(eye=None, notes=(), probes=(), probe_notes=(), trial_notes=()):
     from wl_preproc.nwb.gather import Gathered
 
     return Gathered(session=SESSION, systems=["ohdpi"], blocks=[BLOCK], trials=[{"trial_id": 1}],
                     events=[{}, {}], timebase={}, eye=eye, conditions=[CONDITION], condition_notes=list(notes),
-                    probes=list(probes), probe_notes=list(probe_notes))
+                    probes=list(probes), probe_notes=list(probe_notes), trial_notes=list(trial_notes))
 
 
 def test_the_description_of_a_file_without_eye_data():
@@ -112,3 +112,14 @@ def test_each_probe_is_described_with_both_areas_and_where_its_label_came_from()
          "target": None, "assignment": None, "area_from": "unknown"},
     ]
     assert out["notes"] == ["a condition note", "a probe note"]
+
+
+def test_the_trial_notes_follow_the_condition_notes():
+    """Design spec `2026-10-01-runs-and-trials-design.md` sections 3.2 and
+    3.4: what the stored trials leave out, between the condition notes and
+    the probe notes."""
+    from wl_preproc.nwb.describe import describe
+
+    out = describe(_gathered(notes=["a condition note"], trial_notes=["a trial note"], probe_notes=["a probe note"]),
+                   status="written", n_critical=0, checksums=[], built_at=BUILT_AT)
+    assert out["notes"] == ["a condition note", "a trial note", "a probe note"]
```

```python
"""What the file says about trials the canonical trial list leaves out
(design spec `2026-10-01-runs-and-trials-design.md` sections 3.2 and 3.4).
Subject and date checked unclaimed across `tests/` on 2026-10-01; the date is
in the PAST, since `nwbinspector` calls a future `session_start_time`
critical."""

from __future__ import annotations

import pytest

from tests.schema.test_nwb_probes import _canonical
from tests.schema.test_spikeglx_restart import _session


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


def _no_task_file(spikeglx_directory):
    """A real wl-xcon session has no task file in the synthetic format, so
    `TimingProvenance.trial_count_agreement` is null for it. Kept, the
    synthetic one lists four trials against two stored, and the session would
    fall to tier D, which no real session meets for this reason."""
    (spikeglx_directory.parent / "syncbox" / "task.json").unlink()


def test_the_description_names_a_repeated_number_and_one_too_large(daemon_module, prefix, tmp_path_factory):
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb.build import build

    _recipe, key = _session(tmp_path_factory, _no_task_file, subject="rtnwb1", session_id="2025-06-28_01",
                            trial_numbers=[1, 2, 2, 40000])
    daemon_module.run_once(prefix=prefix)
    activation = _canonical(daemon_module, prefix, key, "rtnwb1-k1", [])

    result = build(activation, tmp_path_factory.mktemp("nwb-trials"))

    assert result.status == "written", (result.reason, result.findings)
    notes = result.description["notes"]
    assert [note for note in notes if note.startswith(("trial number ", "1 trial(s) numbered"))] == [
        "trial number 2 appears 2 times in the recording; only the first is stored",
        "1 trial(s) numbered above 32,767 are not stored: element-event's trial_id holds no larger number"]
    with NWBHDF5IO(str(result.path), "r") as handle:
        assert sorted(handle.read().trials["trial_id"][:]) == [1, 2]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/nwb/test_describe.py tests/schema/test_nwb_trials.py -q --tb=line -p no:cacheprovider`
Expected: 6 failed. `Gathered` takes no `trial_notes`, so the five description tests fail building one, and the database test's trial notes are `[]`.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -51,6 +51,10 @@ class Gathered:
     # `2026-09-30-nwb-probes-design.md` sections 3 and 5). See `_probes`.
     probes: list[dict] = dataclasses.field(default_factory=list)
     probe_notes: list[str] = dataclasses.field(default_factory=list)
+    # The trials the canonical trial list leaves out: a number strobed again,
+    # and one too large to store (design spec
+    # `2026-10-01-runs-and-trials-design.md` sections 3.2 and 3.4).
+    trial_notes: list[str] = dataclasses.field(default_factory=list)
 
 
 def _aware_utc(value: datetime.datetime) -> datetime.datetime:
@@ -236,6 +240,39 @@ def _events(blocks: BlockSet, session_key: dict) -> list[dict]:
     return events
 
 
+def _trial_notes(session_key: dict, blocks: BlockSet) -> list[str]:
+    """What the stored trials leave out, for this file's blocks (design spec
+    `2026-10-01-runs-and-trials-design.md` sections 3.2 and 3.4). Read from
+    every strobed `TRIAL_NUMBER`, which `Event` keeps whether or not its trial
+    was stored: a number strobed again after its first, and one above
+    element-event's smallint `trial_id`."""
+    import collections
+
+    from wl_preproc.schema import pipeline
+    from wl_preproc.schema.events import TRIAL_ID_MAX
+
+    strobed = sorted(
+        (float(row["event_start_time"]), int(row["attribute_value"]))
+        for row in (pipeline.event.Event.Attribute & session_key
+                    & {"event_type": "TRIAL_NUMBER", "attribute_name": "trial_id"}).to_dicts())
+    counts = collections.Counter(number for _time_s, number in strobed)
+    inside = blocks.contains_instant([time_s for time_s, _number in strobed]) if strobed else []
+    seen, repeated, too_large = set(), set(), 0
+    for (_time_s, number), here in zip(strobed, inside, strict=True):
+        first = number not in seen
+        seen.add(number)
+        if here and number > TRIAL_ID_MAX:
+            too_large += 1
+        elif here and not first:
+            repeated.add(number)
+    notes = [f"trial number {number} appears {counts[number]} times in the recording; only the first is stored"
+             for number in sorted(repeated)]
+    if too_large:
+        notes.append(f"{too_large} trial(s) numbered above {TRIAL_ID_MAX:,} are not stored: element-event's "
+                     "trial_id holds no larger number")
+    return notes
+
+
 def _conditions(trials: list[dict], events: list[dict], blocks: list[dict], session_dir: Path,
                 subject: str) -> tuple[list[dict], list[str]]:
     """Each trial's condition and the settings that varied, and each block's
@@ -605,4 +642,5 @@ def gather(activation_key: dict) -> Gathered:
         condition_notes=condition_notes,
         probes=probes,
         probe_notes=probe_notes,
+        trial_notes=_trial_notes(session_key, blocks),
     )
```

```diff
--- a/wl_preproc/nwb/describe.py
+++ b/wl_preproc/nwb/describe.py
@@ -102,7 +102,7 @@ def describe(data, *, status: str, n_critical: int, checksums: list[dict], built
             "reference_source": session["clock"]["source"],
             "eye_usable_fraction": (eye or {}).get("usable_fraction") or {"left": None, "right": None},
         },
-        "notes": [*data.condition_notes, *data.probe_notes],
+        "notes": [*data.condition_notes, *data.trial_notes, *data.probe_notes],
         "checksums": {"algorithm": "sha256", "datasets": checksums},
     }
     return NwbDescription.model_validate(description).model_dump(mode="json")
```

```diff
--- a/wl_preproc/contracts/nwb_description.py
+++ b/wl_preproc/contracts/nwb_description.py
@@ -197,7 +197,9 @@ class NwbDescription(_Frozen):
     # Empty in version 1; piece 3 adds the processing summary (for example
     # the number of single units, and whether any narrow-waveform units).
     processing: dict[str, Any] = {}
-    # Why any trial's condition or settings are unknown, then what the file
-    # could not place or join about a probe.
+    # Why any trial's condition or settings are unknown, then which trials the
+    # recording strobed and the file does not hold (a repeated number, one too
+    # large to store), then what the file could not place or join about a
+    # probe.
     notes: list[str]
     checksums: Checksums
```

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/nwb tests/schema/test_nwb_trials.py tests/schema/test_nwb_build.py tests/schema/test_nwb_probes.py tests/cli/test_schemas_export.py -q --tb=line -p no:cacheprovider`
Expected: 135 passed, 1 skipped.

- [ ] **Step 5: Mutation checks.**
  - T4a (`gather.py`): `trial_notes=_trial_notes(session_key, blocks)` becomes `trial_notes=[]` [`test_the_description_names_a_repeated_number_and_one_too_large`].
  - T4b (`describe.py`): the trial notes are left out of `notes` [`test_the_trial_notes_follow_the_condition_notes`].
  - T4c (`gather.py`): `elif here and not first:` becomes `elif here:` [`test_the_description_names_a_repeated_number_and_one_too_large`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/nwb/gather.py wl_preproc/nwb/describe.py wl_preproc/contracts/nwb_description.py tests/nwb/test_describe.py tests/schema/test_nwb_trials.py
git commit -m "feat(nwb): the description names the trials the recording strobed and the file does not hold -- a repeated number, and one too large for element-event's smallint

<trailer lines>"
```

---

### Task 5: Runs, measured from the recording

**Files:**
- Modify: `wl_preproc/contracts/events.py`, `wl_preproc/events/assemble.py`, `wl_preproc/schema/core.py`, `wl_preproc/schema/events.py`, `wl_preproc/synth/recipe.py`, `wl_preproc/synth/timeline.py`
- Test: `tests/contracts/test_events_codec.py`, `tests/events/test_assemble.py`, `tests/synth/test_timeline.py`, `tests/schema/test_events.py`

**Interfaces — consumes:** Task 2's `last_s` handling and `unclosed_blocks`.

**Interfaces — produces:**
- `Escape.RUN_START = 0x8006`, `PAYLOAD_WORD_COUNTS[Escape.RUN_START] = 2` (`(run_number, task_type_code)`), and `Marker.RUN_END = 4`.
- `events.assemble.AssembledRun(run_number, task_type, start_s, end_s, last_s)` and `Assembly.runs`.
- `core.Run`: `-> pipeline.Session`, `run_number : smallint`; `task_type`, `run_start_time`, `run_stop_time`, `closed`. `populate_session` writes it.
- `SessionRecipe.runs: bool` (default `False`): each block wrapped in its own run.

**Why a new escape and a new marker.** The requester ruled on 2026-10-01 that a run holds blocks, and that the recording should identify each run as it does each block. wl-xcon's own `RUN_START`/`RUN_END` (4135/4136) are bare, provisional codes in wl-xtasks' range, carrying no number. Session structure is this repository's under ADR-0007, so the run's start is an escape in `BLOCK_START`'s layout, and its end is a marker beside `BLOCK_END`. wl-xcon confirmed the layout on 2026-10-01, and will send both once they are on `main`.

**Why `runs` is off by default.** It adds words to the stream, and with it on every existing profile would change. A run whose block is unclosed sends no `RUN_END`, since it faulted.

- [ ] **Step 1: Write the failing tests.** Apply these diffs:

```diff
--- a/tests/contracts/test_events_codec.py
+++ b/tests/contracts/test_events_codec.py
@@ -101,3 +101,15 @@ def test_markers_and_escapes_occupy_disjoint_ranges():
     mistaken for the start of a payload."""
     assert all(1 <= m.value <= 255 for m in Marker)
     assert all(e.value >= 0x8000 for e in Escape)
+
+
+def test_a_run_start_carries_its_number_and_task_and_a_run_ends_with_a_marker():
+    """Design spec `2026-10-01-runs-and-trials-design.md` section 2.1: the
+    run's start is escape 0x8006 in BLOCK_START's layout, and a run that ends
+    by design sends marker 4, beside BLOCK_END (3). Neither value may ever be
+    renumbered once a recording carries it."""
+    assert (Escape.RUN_START.value, Marker.RUN_END.value) == (0x8006, 4)
+    (start, end) = decode_stream(
+        stream([*encode_payload(Escape.RUN_START, [2, TaskTypeCode.RF_MAP.value]), Marker.RUN_END.value], 1.0))
+    assert (start.escape, start.words) == (Escape.RUN_START, (2, TaskTypeCode.RF_MAP.value))
+    assert end.code == Marker.RUN_END.value
```

```diff
--- a/tests/events/test_assemble.py
+++ b/tests/events/test_assemble.py
@@ -105,3 +105,19 @@ def test_an_unclosed_block_records_its_last_event():
     # A payload event is timed at its escape word: trial 1's TRIAL_NUMBER.
     assert first.end_s is None and first.last_s == pairs[5][0]
     assert second.end_s == second.last_s == pairs[-1][0]
+
+
+def test_runs_are_measured_like_blocks_and_an_unclosed_one_ends_at_its_last_event():
+    """Design spec `2026-10-01-runs-and-trials-design.md` sections 2.1 and
+    2.3: RUN_START opens a run with its number and task, RUN_END closes it,
+    and a run that faulted -- no RUN_END -- records its last event."""
+    words = [*encode_payload(Escape.RUN_START, [1, 0]), *encode_payload(Escape.BLOCK_START, [1, 0]),
+             Marker.TRIAL_START.value, *encode_payload(Escape.TRIAL_NUMBER, [0, 1]),
+             *encode_payload(Escape.RUN_START, [2, 5]), *encode_payload(Escape.BLOCK_START, [2, 5]),
+             Marker.TRIAL_START.value, *encode_payload(Escape.TRIAL_NUMBER, [0, 2]), Marker.TRIAL_END.value,
+             Marker.BLOCK_END.value, Marker.RUN_END.value]
+    pairs = [(0.001 * i, word) for i, word in enumerate(words)]
+    first, second = assemble.assemble(_stream(pairs)).runs
+    assert (first.run_number, first.task_type, first.start_s, first.end_s) == (1, 0, 0.0, None)
+    assert first.last_s == pairs[9][0]  # trial 1's TRIAL_NUMBER, timed at its escape word
+    assert (second.run_number, second.task_type, second.end_s, second.last_s) == (2, 5, pairs[-1][0], pairs[-1][0])
```

```diff
--- a/tests/synth/test_timeline.py
+++ b/tests/synth/test_timeline.py
@@ -185,3 +185,16 @@ def test_a_fixture_can_strobe_the_numbers_it_names():
 def test_trial_numbers_must_name_every_trial():
     with pytest.raises(ValueError, match="trial_numbers names 3 trials, and the session has 4"):
         _recipe(trial_numbers=[1, 2, 3])
+
+
+def test_runs_wrap_each_block_and_a_faulted_run_sends_no_end():
+    """`runs=True` sends wl-xcon's order: the run's start (escape 0x8006),
+    its block, and the run's end (marker 4) after the block's end. A run whose
+    block is unclosed faulted, so it sends no RUN_END either (design spec
+    `2026-10-01-runs-and-trials-design.md` sections 2.1 and 7)."""
+    from wl_preproc.events.assemble import assemble
+
+    runs = assemble(decode_stream(list(build_timeline(_recipe(runs=True, unclosed_blocks=[1])).code_words))).runs
+    assert [(run.run_number, run.task_type, run.end_s is not None) for run in runs] == [
+        (1, int(TaskTypeCode.RF_MAP), False), (2, int(TaskTypeCode.RESTING_DARK), True)]
+    assert not assemble(decode_stream(list(build_timeline(_recipe()).code_words))).runs, "off by default"
```

```diff
--- a/tests/schema/test_events.py
+++ b/tests/schema/test_events.py
@@ -535,3 +535,30 @@ def test_populate_session_stores_the_first_of_a_repeated_number_and_none_too_lar
     strobed = sorted(int(value) for value in (pipeline.event.Event.Attribute & key
                                               & {"attribute_name": "trial_id"}).to_arrays("attribute_value"))
     assert strobed == [1, 2, 2, 40000]
+
+
+def test_populate_session_measures_each_run_into_core_run(events_activated, dj_conn, tmp_path):
+    """Design spec `2026-10-01-runs-and-trials-design.md` section 2.2:
+    element-event has no run level, so `core.Run` holds each measured run.
+    Run 1's block is unclosed, so run 1 faulted: no RUN_END, and its stop is
+    its last event, before run 2 starts."""
+    from wl_preproc.schema import core, pipeline
+    from wl_preproc.synth.recipe import CI_RECIPE, SessionRecipe
+    from wl_preproc.synth.session import generate_session
+
+    recipe = SessionRecipe.model_validate({**CI_RECIPE.model_dump(), "subject": "rtruns1", "runs": True,
+                                           "unclosed_blocks": [1]})
+    generate_session(tmp_path, recipe)
+    pipeline.lab.Lab.insert1({"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
+                             skip_duplicates=True)
+    pipeline.subject.Subject.insert1({"subject": "rtruns1", "sex": "M", "subject_birth_date": datetime.date(2020, 1, 1),
+                                      "subject_description": ""}, skip_duplicates=True)
+    key = {"subject": "rtruns1", "session_datetime": datetime.datetime(2027, 3, 25, 9, 0)}
+    pipeline.Session.insert1(key, skip_duplicates=True)
+
+    events.populate_session(key, tmp_path / recipe.session_id)
+
+    first, second = (core.Run & key).to_dicts(order_by="run_number")
+    assert [(run["run_number"], run["task_type"], run["closed"]) for run in (first, second)] == [
+        (1, int(recipe.blocks[0].task_type), 0), (2, int(recipe.blocks[1].task_type), 1)]
+    assert first["run_start_time"] < first["run_stop_time"] < second["run_start_time"] < second["run_stop_time"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/contracts/test_events_codec.py tests/events/test_assemble.py tests/synth/test_timeline.py tests/schema/test_events.py -q --tb=line -p no:cacheprovider`
Expected: 4 failed, 49 passed. `Escape` has no `RUN_START` (the codec and assembler tests), and `SessionRecipe` refuses `runs` as an extra input (the timeline and database tests).

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/contracts/events.py
+++ b/wl_preproc/contracts/events.py
@@ -70,6 +70,10 @@ class Marker(IntEnum):
     SESSION_START = 1
     SESSION_END = 2
     BLOCK_END = 3
+    # A run that ends by design (design spec `2026-10-01-runs-and-trials-design.md`
+    # section 2.1). Session structure, so this repository's under ADR-0007; a
+    # run that faults sends none. Its start is `Escape.RUN_START`.
+    RUN_END = 4
     TRIAL_START = 32
     TRIAL_END = 33
     TRIAL_CORRECT = 34
@@ -150,6 +154,12 @@ class Escape(IntEnum):
     CONDITION = 0x8003
     TARGET_POSITION = 0x8004
     PARAM_CHANGE = 0x8005
+    # A run's start, carrying its number in the session and its task: the
+    # run is identified in the recording itself, as a block is, so a lost code
+    # costs one run rather than renumbering the rest (design spec
+    # `2026-10-01-runs-and-trials-design.md` section 2.1; the requester's
+    # vocabulary: a run holds blocks, a block holds trials).
+    RUN_START = 0x8006
 
 
 PAYLOAD_WORD_COUNTS: dict[Escape, int] = {
@@ -158,6 +168,7 @@ PAYLOAD_WORD_COUNTS: dict[Escape, int] = {
     Escape.CONDITION: 2,  # uint32, high word first
     Escape.TARGET_POSITION: 3,  # (role, x_dva, y_dva)
     Escape.PARAM_CHANGE: 2,  # uint32 sequence number, high word first
+    Escape.RUN_START: 2,  # (run_number, task_type_code), BLOCK_START's layout
 }
 
 
```

```diff
--- a/wl_preproc/events/assemble.py
+++ b/wl_preproc/events/assemble.py
@@ -58,11 +58,25 @@ class AssembledBlock:
     last_s: float
 
 
+@dataclass(frozen=True, slots=True)
+class AssembledRun:
+    """One run, measured as a block is: from `RUN_START` to `RUN_END`, or to
+    its last event when it faulted and sent no `RUN_END` (design spec
+    `2026-10-01-runs-and-trials-design.md` sections 2.1 and 2.3)."""
+
+    run_number: int
+    task_type: int
+    start_s: float
+    end_s: float | None
+    last_s: float
+
+
 @dataclass
 class Assembly:
     trials: list[AssembledTrial] = field(default_factory=list)
     blocks: list[AssembledBlock] = field(default_factory=list)
     errors: list[DecodeError] = field(default_factory=list)
+    runs: list[AssembledRun] = field(default_factory=list)
 
 
 def _u32(words: tuple[int, ...]) -> int:
@@ -78,6 +92,7 @@ def assemble(events: list[DecodedEvent]) -> Assembly:
     open_trial_id: int | None = None
     open_outcome: str | None = None
     open_block: AssembledBlock | None = None
+    open_run: AssembledRun | None = None
     last_s = 0.0  # the time of the last event before the one in hand
 
     def close_trial(end_s: float | None) -> None:
@@ -116,6 +131,11 @@ def assemble(events: list[DecodedEvent]) -> Assembly:
                     end_s=None,
                     last_s=event.time_s,
                 )
+            elif event.escape is Escape.RUN_START:
+                if open_run is not None:
+                    result.runs.append(replace(open_run, last_s=last_s))
+                open_run = AssembledRun(run_number=event.words[0], task_type=event.words[1], start_s=event.time_s,
+                                        end_s=None, last_s=event.time_s)
             last_s = event.time_s
             continue
 
@@ -135,9 +155,14 @@ def assemble(events: list[DecodedEvent]) -> Assembly:
             elif marker is Marker.BLOCK_END and open_block is not None:
                 result.blocks.append(replace(open_block, end_s=event.time_s, last_s=event.time_s))
                 open_block = None
+            elif marker is Marker.RUN_END and open_run is not None:
+                result.runs.append(replace(open_run, end_s=event.time_s, last_s=event.time_s))
+                open_run = None
             last_s = event.time_s
 
     close_trial(end_s=None)
     if open_block is not None:
         result.blocks.append(replace(open_block, last_s=last_s))
+    if open_run is not None:
+        result.runs.append(replace(open_run, last_s=last_s))
     return result
```

```diff
--- a/wl_preproc/schema/core.py
+++ b/wl_preproc/schema/core.py
@@ -35,10 +35,31 @@ class Montage(dj.Manual):
     """
 
 
+@schema
+class Run(dj.Manual):
+    definition = """
+    # One run of one task, MEASURED from the recording's RUN_START escape and
+    # RUN_END marker by `schema/events.py::populate_session` (design spec
+    # 2026-10-01-runs-and-trials-design.md section 2.2). element-event has no
+    # run level, so this table holds what trial.Block holds for blocks. Blocks
+    # sit inside runs by time. Key: (subject, session_datetime, run_number).
+    -> pipeline.Session
+    run_number : smallint  # from 1 in the session: wl-xcon's run_in_session
+    ---
+    task_type      : smallint unsigned  # the escape's task code; 0 until wl-xtasks allocates one
+    run_start_time : double             # (s) session time
+    run_stop_time  : double             # (s) its RUN_END, or its last event when it faulted
+    closed         : tinyint(1)         # 1 when a RUN_END arrived
+    """
+
+
 @schema
 class Block(dj.Manual):
     definition = """
     # One run of one task, mirroring wl.works animal_session_block.
+    # *True under the August glossary. Since 2026-10-01 the requester's
+    # vocabulary makes a block a stretch of trials inside a run, and a run is
+    # `Run` below; wl.works is revising its own blocks to match.*
     # start_s/end_s are WL.WORKS' ASSERTION, recorded here through accept() --
     # recording an assertion is not authoring it. Closed open item 9: block rows
     # are authored by wl.works' session planner and wl-preproc never writes
```

```diff
--- a/wl_preproc/schema/events.py
+++ b/wl_preproc/schema/events.py
@@ -550,3 +550,14 @@ def populate_session(key: dict, session_dir: Path) -> None:
         pipeline.trial.BlockTrial.insert(
             block_trial_rows, allow_direct_insert=True, skip_duplicates=True
         )
+
+    # -- core.Run: each run, measured as blocks are (design spec
+    # `2026-10-01-runs-and-trials-design.md` section 2.2). A run that faulted
+    # sent no RUN_END, and its stop is its last event.
+    run_rows = [
+        {**session_key, "run_number": run.run_number, "task_type": run.task_type, "run_start_time": run.start_s,
+         "run_stop_time": run.end_s if run.end_s is not None else run.last_s, "closed": int(run.end_s is not None)}
+        for run in assembly.runs
+    ]
+    if run_rows:
+        core.Run.insert(run_rows, skip_duplicates=True)
```

```diff
--- a/wl_preproc/synth/recipe.py
+++ b/wl_preproc/synth/recipe.py
@@ -209,6 +209,11 @@ class SessionRecipe(BaseModel):
     # 3, ... A fixture uses it to repeat a number, as a crash and restart
     # without wl-xcon's XC-026 would, or to exceed element-event's smallint.
     trial_numbers: tuple[int, ...] = ()
+    # Wrap each block in its own run, as wl-xcon sends them: the run's start
+    # (escape 0x8006, its number and task) before the block, and its end
+    # (marker 4) after the block's end. Off by default, so every existing
+    # profile is byte-identical.
+    runs: bool = False
 
     # How many neurons this session contains. Zero is legal and is what every
     # timing-only fixture wants: Phase 1c's recipes care about barcodes and
```

```diff
--- a/wl_preproc/synth/timeline.py
+++ b/wl_preproc/synth/timeline.py
@@ -114,6 +114,9 @@ def build_timeline(recipe: SessionRecipe) -> GroundTruth:
 
     for block_index, block in enumerate(recipe.blocks, start=1):
         block_start = cursor
+        if recipe.runs:
+            for word in encode_payload(Escape.RUN_START, [block_index, int(block.task_type)]):
+                _emit(words, block_start, word)
         for word in encode_payload(
             Escape.BLOCK_START, [block_index, int(block.task_type)]
         ):
@@ -178,6 +181,8 @@ def build_timeline(recipe: SessionRecipe) -> GroundTruth:
         # A run that faults sends no BLOCK_END (wl-xcon's rule for its RUN_END).
         if block_index not in recipe.unclosed_blocks:
             _emit(words, cursor - CODE_WORD_SPACING_S / 2, Marker.BLOCK_END.value)
+            if recipe.runs:
+                _emit(words, cursor - CODE_WORD_SPACING_S / 2, Marker.RUN_END.value)
 
     _emit(words, recipe.duration_s, Marker.SESSION_END.value)
 
```

Run `.venv/bin/python -m wl_preproc.cli.main schemas export --out docs/schemas`, then `git status --short docs/schemas`. Expected: nothing; no published schema names an event code.

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 53 passed.

Then the suites a new table and a new marker touch: `.venv/bin/python -m pytest tests/schema/test_core.py tests/schema/test_daemon.py tests/schema/test_daemon_skips_freed_sessions.py tests/schema/test_guardrails.py tests/schema/test_timebase.py tests/schema/test_coverage.py tests/test_cli_guardrails.py tests/cli/test_schemas_export.py -q -p no:cacheprovider`. Expected: 151 passed.

- [ ] **Step 5: Mutation checks.**
  - T5a (`contracts/events.py`): `RUN_START = 0x8006` becomes `0x8007` [`test_a_run_start_carries_its_number_and_task_and_a_run_ends_with_a_marker`].
  - T5b (`assemble.py`): `elif marker is Marker.RUN_END and open_run is not None:` becomes `elif False:` [`test_runs_are_measured_like_blocks_and_an_unclosed_one_ends_at_its_last_event`].
  - T5c: an open run is appended without its `last_s` when the next run starts [`test_runs_are_measured_like_blocks_and_an_unclosed_one_ends_at_its_last_event`].
  - T5d (`schema/events.py`): `"closed"` is always 1 [`test_populate_session_measures_each_run_into_core_run`].
  - T5e: an unclosed run's stop becomes its start [`test_populate_session_measures_each_run_into_core_run`].
  - T5f (`timeline.py`): `if recipe.runs:` becomes `if False:` [`test_runs_wrap_each_block_and_a_faulted_run_sends_no_end`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/contracts/events.py wl_preproc/events/assemble.py wl_preproc/schema/core.py wl_preproc/schema/events.py wl_preproc/synth/recipe.py wl_preproc/synth/timeline.py tests/contracts/test_events_codec.py tests/events/test_assemble.py tests/synth/test_timeline.py tests/schema/test_events.py
git commit -m "feat(events): runs measured from the recording -- a RUN_START escape (0x8006: run number, task code) and a RUN_END marker (4), stored in core.Run, an unclosed run ending at its last event; the generator can wrap its blocks in runs

<trailer lines>"
```

---

### Task 6: The asks, the amendments, the records, and the full suite

**Files:**
- Modify: `docs/pending-wl-xcon-amendments.md`, `docs/pending-wl-works-amendments.md`, `docs/superpowers/specs/2026-10-01-runs-and-trials-design.md`, `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-10-01-runs-and-trials.md`

- [ ] **Step 1: What wl-xcon and wl.works must do.** The asks were sent to wl-xcon on 2026-10-01, before this plan, and accepted that day; these entries record them. Apply:

```diff
--- a/docs/pending-wl-xcon-amendments.md
+++ b/docs/pending-wl-xcon-amendments.md
@@ -1,12 +1,55 @@
 # Amendments to wl-xcon
 
-**One is outstanding, opened 2026-09-29.** wl-xcon (formerly wl-expcontroller) is the rig's
+**Two are outstanding, opened 2026-09-29 and 2026-10-01.** wl-xcon (formerly wl-expcontroller) is the rig's
 experiment controller. What it asked of this repository is recorded in
 [`HANDOVER-wl-expcontroller.md`](../HANDOVER-wl-expcontroller.md) and in its own
 `docs/pending-wl-preproc-amendments.md`; this file is the other direction.
 
 ---
 
+# OPEN — mark each run and each block, and resume after a crash
+
+**Opened 2026-10-01** with runs and trials
+([`specs/2026-10-01-runs-and-trials-design.md`](superpowers/specs/2026-10-01-runs-and-trials-design.md)),
+**January-critical.** A real wl-xcon recording gives this repository no measured runs or blocks:
+wl-xcon sends no `BLOCK_START`, and its `RUN_START`/`RUN_END` (4135/4136, provisional, in
+wl-xtasks' range) carry no run number. **All three asks were sent and accepted on 2026-10-01**,
+under the vocabulary the requester ruled in wl-xcon's session that day: a run holds blocks, and a
+block is a stretch of trials under one block type (spec §0).
+
+**The asks:**
+1. **Each block:** our `BLOCK_START` (`0x8002`) at each block's start, `(block in session from 1,
+   task code or 0)`, and `BLOCK_END` (marker 3) when it ends. wl-xcon accepted it per block and
+   is building it with its session-levels change (its
+   `docs/superpowers/plans/2026-10-01-session-levels.md`).
+2. **Each run:** the new **`RUN_START` escape (`0x8006`)** at each run's start, `(run in session
+   from 1, task code or 0)`, and the new **`RUN_END` marker (4)** when a run ends by design. A run
+   that faults sends neither its block's `BLOCK_END` nor `RUN_END`. Allocated by this
+   repository under ADR-0007. **wl-xcon will send them once `contracts/events.py` carries them
+   on this repository's `main`**, since its CI pins every code against these enums: **tell it
+   when that lands.** The order is `RUN_START` → each block (`BLOCK_START`, its trials,
+   `BLOCK_END`) → `RUN_END`; 4135/4136 may stay, and are read by nothing here.
+3. **XC-026 before January**: a restarted session carries its run, block and trial numbers on, so
+   a recording never repeats one. Accepted.
+
+**What this repository does meanwhile,** built with that spec:
+- **Runs and blocks are measured** from those codes, runs into `core.Run`.
+- **A run or block that never closed** ends where the next starts, and its recorded stop is its
+  last event.
+- **A repeated trial number** keeps its first trial, and every repeat is named in the file's
+  description. Nothing is renumbered.
+
+**Its questions, answered the same day** (it said this closes its XC-198):
+- **Two wl-xcon sessions in one sync-box recording:** no. One recording holds one animal.
+- **A trial with no outcome:** it stores, measured, and its inferred stop stays inside its own
+  block.
+- **A trial number above 32,767:** MySQL refuses it (measured, 1264). Such trials are left out and
+  counted, and the rest are stored.
+- **A `TRIAL_NUMBER` cut by a crash:** the decoder's framing is frozen. One or two trials are lost
+  and the session falls to tier D; this is recorded as open beside its XC-199.
+
+---
+
 # OPEN — the stream must carry each trial's number and condition
 
 **Opened 2026-09-29** while planning NWB publishing
@@ -45,8 +88,16 @@ it cannot simply be that index."
    **record that number in `trials.jsonl`** beside the condition's name, so the name and the
    stream's number can be tied without a table kept anywhere else.
 
+**Ask 1 is DONE (2026-10-01).** XC-155 is built on wl-xcon's `main` at `eeec053`: every line of
+`trials.jsonl` carries `trial_number`, counted from 1 across the session and equal to the
+`TRIAL_NUMBER` payload. This repository keys each line by it since the runs-and-trials spec
+(§3.1, `events/rigtrials.py`). **Ask 2 stays open**: wl-xcon will emit `CONDITION` once conditions
+exist (its XC-150), numbered then (its XC-197).
+
 **What this repository does meanwhile.** It joins by trial number only; a trial the record does
 not name exactly once gets no condition and no settings, and the file's description says why
 (`nwb/conditions.py`). Nothing is guessed from trial order. **A record whose lines name a run is
 not joined at all** (`events/rigtrials.py`): a per-run `index` is not the session's trial number
 even where it is unique. Once this repository reads XC-155's field, such records join again.
+*True when written. Since 2026-10-01 a line carrying `trial_number` is joined by it; only a
+run-named line without one is left out.*
```

```diff
--- a/docs/pending-wl-works-amendments.md
+++ b/docs/pending-wl-works-amendments.md
@@ -1,12 +1,33 @@
 # Amendments to wl-works
 
-**Six are outstanding: two opened 2026-08-22, one 2026-09-28, one 2026-09-29, two 2026-09-30.** The earlier two
+**Seven are outstanding: two opened 2026-08-22, one 2026-09-28, one 2026-09-29, two 2026-09-30, one 2026-10-01.** The earlier two
 batches are closed; their records are kept below, because
 [`specs/2026-08-12-wl-preproc-design.md`](superpowers/specs/2026-08-12-wl-preproc-design.md)
 §14 items 10–11 point at it and a reference that dead-ends teaches nothing.
 
 ---
 
+# OPEN — measured runs, and what a block is now
+
+**Opened 2026-10-01**, answering ask 2 of wl.works' three asks back (its
+`docs/superpowers/specs/2026-09-30-january-canonical-nwb-design.md` §12, read on its `main` at
+`c8dc0613`). The requester accepted all three in principle on 2026-09-30. The landed-session list
+(ask 1) and per-probe block lists (ask 3) are designed next.
+
+- **Measured runs** come from a new `RUN_START` escape (`0x8006`, carrying the run number and task
+  code) and a new `RUN_END` marker (4), not from 4135/4136, and are held in `core.Run`
+  ([`specs/2026-10-01-runs-and-trials-design.md`](superpowers/specs/2026-10-01-runs-and-trials-design.md)).
+  The intent of ask 2 is met: runs measured on the recording's clock and numbered in session
+  order.
+- **A block is now a stretch of trials inside a run.** The requester ruled this in wl-xcon's
+  session on 2026-10-01, superseding the glossary row *"block = one run of one task"*.
+  wl.works' plan to create ELN blocks from measured runs, and to send each block's id as the run
+  number, is wl.works' to revise; wl-xcon is telling it the same.
+- **Until wl-xcon sends the new codes,** a real recording has no measured runs or blocks.
+  wl.works' grid should read that as *waiting*, not as an empty session.
+
+---
+
 # OPEN — wl.works fires the canonical NWB, and regenerates it by naming what it replaces
 
 *Acknowledged by wl.works on 2026-09-30, read on its local `main` at `862f3af9`, which was not yet pushed. It is queued in its `docs/superpowers/brainstorm-queue.md` as "wl-preproc's three NWB-export asks", its next session's brainstorm, and the requester confirmed the asks as theirs.*
```

- [ ] **Step 2: The spec's amendments.** Apply:

```diff
--- a/docs/superpowers/specs/2026-10-01-runs-and-trials-design.md
+++ b/docs/superpowers/specs/2026-10-01-runs-and-trials-design.md
@@ -294,3 +294,34 @@ It also answers wl-xcon's questions, which it acknowledged closes its XC-198:
   run-named line without `trial_number` left unjoined.
 - **The NWB file and its description** carry the repeat and ceiling notes.
 - **The full suite runs once, on both interpreters.**
+
+## Amendments, 2026-10-01, made while proving the plan
+
+The plan (`plans/2026-10-01-runs-and-trials.md`) was proven in code before it was written. These
+settle what the sections above left open; §0 stands.
+
+1. **"The file's notes" are its description's `notes`** (§3.2, §3.4), as they are for conditions
+   and probes. The NWB file holds no notes field of its own. Its task-event table still carries
+   every strobed `TRIAL_NUMBER`, repeats and too-large numbers included.
+2. **A repeat or a too-large number lowers `TimingProvenance.trial_count_agreement` only where a
+   task file in the synthetic format exists.** That check compares the stored trials with the
+   file. No real wl-xcon session has one, so its agreement is null and its tier is unaffected; a
+   synthetic session with repeats falls to tier D. *When a reader of a real task file is built,
+   it must count strobed trials, not stored ones,* or a crashed session will fall to tier D for
+   its repeats.
+3. **The generator gains four fields** (§7):
+   - `unclosed_blocks`: block numbers whose `BLOCK_END` is not sent;
+   - `faulted_trials`: trial positions that send no outcome and no `TRIAL_END` and leave no rig
+     line, though they still use up their index in the run;
+   - `trial_numbers`: each trial's strobed number;
+   - `runs`: wrap each block in its own run.
+
+   `runs` is **off by default**, so every existing profile stays byte-identical. A run whose
+   block is unclosed sends no `RUN_END`.
+4. **The rig record's key is `RigTrial.number`**: `trial_number`, or `index` for a record from
+   before XC-155. `nwb/conditions.py` joins on it.
+5. **`_block_stop_time(block)` no longer takes the stream's end.** An unclosed block's stop is its
+   own last event (`AssembledBlock.last_s`), never the last event of a later run. A run's is the
+   same (`AssembledRun.last_s`).
+6. **An existing database needs `core.Run` created.** It is created when `core` is activated, and
+   no existing table changes shape.
```

- [ ] **Step 3: The checkpoint, `wl.yaml` and the handoff.** Apply:

```diff
--- a/docs/CHECKPOINT.md
+++ b/docs/CHECKPOINT.md
@@ -556,6 +556,20 @@ requester chose to merge the same day; true when written.*
 >    restart). What wl.works must do is a new OPEN entry in
 >    `pending-wl-works-amendments.md`. See `docs/handoffs/2026-09-30-nwb-probes.md`.
 >
+>    **Runs and trials from a real wl-xcon session are BUILT on
+>    `spec/runs-and-trials` (2026-10-01), NOT merged as written** (spec
+>    `superpowers/specs/2026-10-01-runs-and-trials-design.md`), piece 1 of
+>    four. **The requester ruled the vocabulary that day: a run holds blocks,
+>    and a block is a stretch of trials under one block type.** Trials join
+>    the rig's record by wl-xcon's `trial_number` (XC-155). Runs are
+>    measured from a new `RUN_START` escape (0x8006) and `RUN_END` marker
+>    (4), into `core.Run`; blocks from `BLOCK_START` per block. A run or
+>    block that never closed keeps only its own trials. A repeated trial
+>    number keeps its first trial, and a number above 32,767 is left out;
+>    the description names both. **wl-xcon sends the new codes once they are
+>    on `main`, so tell it when this merges**, and XC-026 comes before
+>    January. See `docs/handoffs/2026-10-01-runs-and-trials.md`.
+>
 > **Deferred minors: DONE 2026-09-26, both lists.** The gap-aware branch's
 > items 1–3 (`8af4278`; `eye/detect/validity.py` now cites commit `7d4a00f`
 > in place of a "finding H2" no document named) and all six parked
```

```diff
--- a/wl.yaml
+++ b/wl.yaml
@@ -161,6 +161,14 @@ status:
     fitting a restarted system under one intercept, now fixed (design spec
     `2026-09-30-nwb-probes-design.md`; handoff
     `docs/handoffs/2026-09-30-nwb-probes.md`).
+    Runs and trials from a real wl-xcon session are BUILT on
+    `spec/runs-and-trials` (2026-10-01, NOT merged as written): trials join
+    wl-xcon's record by `trial_number`; runs are measured from a new
+    RUN_START escape (0x8006) and RUN_END marker (4) into `core.Run`, and
+    blocks from BLOCK_START per block; a run or block that never closed keeps
+    only its own trials; and repeated or too-large trial numbers are named
+    rather than dropped (design spec `2026-10-01-runs-and-trials-design.md`;
+    handoff `docs/handoffs/2026-10-01-runs-and-trials.md`).
   next: >-
     **Hardware status as of 2026-09-19, stated by the requester at session
     close: the rig is NOT ready and the compute machine is NOT assembled.**
```

Run `wl-check` on its own. Expected: `wl.yaml: no findings`.

Create `docs/handoffs/2026-10-01-runs-and-trials.md`:

```markdown
# Runs and trials from a real wl-xcon session

**The first of four pieces that make a wl-xcon recording usable here.** The others are the
landed-session list, per-probe block lists in a canonical request, and a metadata-only rebuild.
- **Branch:** `spec/runs-and-trials`, forked from `main` at `0458b10`.
- **Spec:** `docs/superpowers/specs/2026-10-01-runs-and-trials-design.md`. Written as `43543ad`,
  then revised as `3d3cc85` when the requester ruled the vocabulary in wl-xcon's session. It is
  amended in this branch's last commit.
- **Plan:** `docs/superpowers/plans/2026-10-01-runs-and-trials.md`.
- **The requester's choices:** the split, with this piece first; the four decisions in the spec's
  §0; the vocabulary (a run holds blocks, and a block is a stretch of trials); and the new run
  escape. The asks were sent to wl-xcon before the plan, at their choice, and accepted the same
  day.

Every line of the plan was proven in a scratch worktree before the plan was written.

---

## 1. What was built

- **Trials join the rig's record by `trial_number`**, wl-xcon's XC-155 field
  (`events/rigtrials.py`, `nwb/conditions.py`). A record from before XC-155 is joined by `index`.
  A run-named line without the number is reported and left out.
- **Runs are measured from the recording**:
  - two new protocol values (`contracts/events.py`): the `RUN_START` escape (`0x8006`, run number
    and task code) and the `RUN_END` marker (4);
  - `events/assemble.py` measures runs as it does blocks;
  - a new table, `core.Run`, holds them.
- **A run or block that never closed keeps only its own trials.** It ends where the next one
  starts, its recorded stop is its last event, and a faulted trial's inferred stop stays inside
  it.
- **Repeated and too-large trial numbers** (`schema/events.py`): the first trial with a number is
  stored and a repeat is not, and a number above 32,767 is left out instead of failing the
  session. The description names both (`nwb/gather.py`).
- **The generator has wl-xcon's shapes:**
  - the XC-155 rig record (`run`, a per-run `index`, `trial_number`);
  - `unclosed_blocks`, `faulted_trials` and `trial_numbers`;
  - `runs`, which wraps each block in a run.

## 2. What the other repositories must do

- **wl-xcon** (`pending-wl-xcon-amendments.md`, all accepted 2026-10-01):
  - `BLOCK_START`/`BLOCK_END` per block;
  - the new run escape and marker, **sent once `contracts/events.py` carries them on `main`.
    Tell wl-xcon when this branch merges;**
  - XC-026 before January;
  - `CONDITION` is still open (its XC-150, XC-197).
- **wl.works** (`pending-wl-works-amendments.md`):
  - runs come from the new escape, into `core.Run`;
  - a block is now a stretch of trials inside a run, so its block plan is its to revise;
  - a recording without the new codes is *waiting*, not empty.
- **wl-xtasks:** task codes for wl-xcon's tasks, so the second payload word stops being 0.

## 3. Still open

- **Until wl-xcon sends the new codes,** a real recording has no measured runs or blocks.
- **A `TRIAL_NUMBER` cut by a crash** costs one or two trials and tier D (spec §3.5, wl-xcon's
  XC-199).
- **The rig's finer outcomes** are not in the file.
- **A real task-file reader** must count strobed trials (spec amendment 2).
- **An existing database needs `core.Run`,** which activation creates.
- **Pieces 2–4:** the landed-session list, per-probe block lists, and the metadata-only rebuild.

## 4. The rulings

Each is a dated amendment in the spec:
1. **The file's notes are its description's.** *Cost if wrong:* the NWB file itself does not
   say which trials are missing; its task events still show them.
2. **Repeats lower the tier only where a synthetic task file exists.** *Cost if wrong:* a future
   real task-file reader puts crashed sessions at tier D.
3. **The generator's four fields, with `runs` off by default.** *Cost if wrong:* no fixture tests
   runs unless it asks for them.
4. **`RigTrial.number` is the join key.**
5. **`_block_stop_time` takes only the block.**
6. **`core.Run` is created on activation.**
```

Add §5, the measured counts, and §6, the final review, as execution measures them.

- [ ] **Step 4: The full suite, once, on both interpreters.** With the BMD and NSLR references set and `WLPP_OHDPI_REFERENCE` unset, as CI has it:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider > .superpowers/sdd/2026-10-01-runs-and-trials/full311.log 2>&1; echo "311 exit $?"
~/.cache/wl-preproc-venv313/bin/python -m pytest -q -p no:cacheprovider > .superpowers/sdd/2026-10-01-runs-and-trials/full313.log 2>&1; echo "313 exit $?"
```

Expected: `311 exit 0` and `313 exit 0`, **2055 passed, 25 skipped, 1 deselected, 1 xfailed** on 3.11 and **2054 passed, 27 skipped, 1 xfailed** on 3.13, proven with every task applied. The plan adds 20 tests (2061 collected at the spec commit, 2081 after).

- [ ] **Step 5: Commit**

```bash
git add docs/pending-wl-xcon-amendments.md docs/pending-wl-works-amendments.md docs/superpowers/specs/2026-10-01-runs-and-trials-design.md docs/CHECKPOINT.md wl.yaml docs/handoffs/2026-10-01-runs-and-trials.md
git commit -m "docs: runs and trials -- the asks sent to wl-xcon, the answer to wl.works' ask 2, the spec's amendments, the checkpoint and the handoff

<trailer lines>"
```

---

## Rulings made while planning

Items 1–6 are the spec's dated amendments of the same numbers (Task 6). Each names its cost if wrong.

1. **"The file's notes" are its description's `notes`,** as for conditions and probes. *Cost:* the NWB file itself does not say which trials are missing, though its task events show them.
2. **A repeat lowers `trial_count_agreement` only where a synthetic task file exists.** *Cost:* a future reader of a real task file must count strobed trials, or a crashed session falls to tier D.
3. **The generator's four new fields,** with a faulted trial still using its run index, and `runs` off by default. *Cost:* existing profiles stay byte-identical, but no fixture exercises runs unless it asks for them.
4. **`RigTrial.number` is the join key.** *Cost:* a rename if a later record changes the field.
5. **`_block_stop_time` takes only the block.** *Cost:* none; its one caller is updated.
6. **`core.Run` is created on activation,** and no existing table changes shape. *Cost:* none known; an existing database gains the table on its next activation.
7. **The explicit first-wins check is kept beside `skip_duplicates`** for clarity, and its proof is the reversed-order mutation. This one changes no behaviour, so it is not a spec amendment. *Cost:* none.
