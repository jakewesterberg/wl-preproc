# NWB Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every built NWB file leaves scratch described and findable. It is published to the NAS's slow share and moved to and from the fast share when wl.works asks. wl.works can list it with its location and description, and reclamation can see that a session's canonical file is safely published.

**Architecture:** The builder gains a description of every file. The description comes from the same gathered data as the file, including each trial's condition and stimulus settings read from the rig's own record, `xcon/trials.jsonl`. `nwb/publish.py` copies `written` files to the NAS with verification and records every change in an append-only table. Its placement stage keeps one live copy per file, on the share the latest active set wants. The responder gains `GET /nwb`, a listing wl.works polls with a cursor, and `PUT /nwb/active`, the whole active set. `archive/reclaim.py::canonical_nwb_present` becomes a real query.

**Tech Stack:** Python ≥3.11; pynwb 4.x and h5py; pydantic 2 (the contracts); DataJoint 2.3 with MySQL (tables and end-to-end tests); the standard-library HTTP server (the responder).

**Spec:** `docs/superpowers/specs/2026-09-29-nwb-publishing-design.md` (`8fe883a`, amended in this plan's commit). It is binding. The requester approved it on 2026-09-29 ("Yes, write the plan").

**Every piece of code below was proven before this plan was written,** in a scratch worktree on a branch of its own, one commit per task.
- **Each task's failing run** was measured on the previous task's code plus this task's tests. **Its passing run** was measured on its own commit. Both are quoted in its steps.
- **Every mutation check named here** was run against the final tree, and each failed its test.
- **The full suite** was run on both interpreters with every task applied (Task 9 quotes it).

## Global Constraints

- **The spec is binding,** including its dated amendments. Where it and this plan disagree, the spec wins; record a ruling.
- **Pull-only, unchanged.** wl.works opens every connection (parent spec §11.1). Nothing here initiates a connection. `tests/test_cli_guardrails.py` bans `importlib`, `socket`, `urllib.request` and `http.client` in `wl_preproc/`.
- **One live copy per file,** on the slow or the fast share, never both (spec §5). **Nothing carries its final name until verified** (`.partial`, re-hashed, renamed; the description written last). **Nothing written over a file no placement records** (spec §4's amendment).
- **Layout on either share:** `<mount>/nwb/<subject>/<session_id>/<identifier>.nwb`, with `<identifier>.json` beside it. The recorded path is relative to the share.
- **Checksums are `sha256`** of each dataset's decoded contents (spec §7). Everything else about them is piece 1's rule.
- **The description is `contracts/nwb_description.py::NwbDescription`, `schema_version` 1.** It is strict on this side (`extra="forbid"`), exported to `docs/schemas/nwb_description.json`, and checked in CI.
- **Conditions are joined by trial number only** (the stream's `TRIAL_NUMBER` against the rig's `index`). No guessing: a trial the record does not name exactly once gets no condition and no settings, and `notes` says why.
- **Frozen interfaces changed here, each with its export regenerated:** `contracts/protocol.py` (new models only), `docs/schemas/*.json`, and `docs/ops/lab-host-protocol.md`.
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code, data or tables.
- **Tests.**
  - Run with `.venv/bin/python -m pytest <files> -q -p no:cacheprovider` from the repository root. In a git worktree, prefix `PYTHONPATH=$PWD`.
  - Database tests need Docker (OrbStack on this machine: `open -a OrbStack` after a reboot). A module-wide setup error on a 120 s container timeout is the known flake: re-run it.
  - Mutation checks:
    - clear `__pycache__` first, and again after restoring;
    - run with `PYTHONDONTWRITEBYTECODE=1`;
    - make one mutation at a time, and restore the file afterwards.
  - **Run each task's own test files.** The full suite runs once, on both interpreters, in Task 9.
  - The shell is zsh: an unquoted `$VAR` holding several arguments is not split, and zsh arrays start at 1. A pipe through `tail` or `grep` hides the test command's exit status: gate on the command's own status.
- **Every commit message ends with:**

  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R
  ```

## Review Focus

Five inputs the spec implies but its own test list (§13) does not exercise, most likely first. Each is pinned by a test named here, in the task that owns the code.

1. **The old copy cannot be deleted after a move.** A viewer holds it open, or the share refuses the delete. The move stands; the next pass removes the leftover, so two copies never outlive a move, and a later move back is not stuck on the never-overwrite rule. Task 6: `test_an_old_copy_that_could_not_be_deleted_is_removed_on_the_next_pass`.
2. **A share fills, or fails, mid-copy.** Nothing carries the final name, no half-written file is left, and the error is reported and retried. Task 5: `test_a_copy_the_share_cannot_hold_leaves_nothing`.
3. **A setting whose type differs between trials.** The rig's parameters are an untyped dict, so a setting can be a number on one trial and text on the next. It is written as JSON text and summarised by its distinct values, never a crash. Task 2: `test_a_setting_whose_type_differs_between_trials_is_text`.
4. **wl.works marks a refused file active.** It is accepted, never published or moved, and costs no error a pass. Task 6: `test_an_active_set_naming_a_refused_file_changes_nothing`.
5. **A published file deleted from the NAS by hand.** The placement stays as recorded, and every pass reports the missing path instead of failing obscurely. Task 6: `test_a_published_file_deleted_by_hand_is_reported_not_moved`.

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/nwb/checksums.py` | sha256 per dataset (Task 1) |
| `wl_preproc/events/rigtrials.py` | reading the rig's `xcon/trials.jsonl` (Task 2) |
| `wl_preproc/nwb/conditions.py` | the join, each block's conditions by settings, the trials' columns (Task 2) |
| `wl_preproc/synth/peripherals.py`, `synth/session.py` | the synthetic rig record (Task 2) |
| `wl_preproc/nwb/intervals.py`, `nwb/gather.py`, `nwb/build.py` | conditions and settings in the file (Task 3) |
| `wl_preproc/contracts/nwb_description.py`, `nwb/describe.py` | the description (Task 4) |
| `wl_preproc/schema/nwb.py` | `NwbFile.description`, `NwbChange`, `NwbPlacement`, `ActiveSet` (Tasks 1, 4, 5) |
| `wl_preproc/nwb/publish.py` | shares, the verified copy, publishing (Task 5), placement (Task 6) |
| `wl_preproc/daemon.py`, `cli/main.py` | the two stages and their options (Tasks 5, 6) |
| `wl_preproc/contracts/protocol.py`, `responder/{nwb,handler,server}.py` | `GET /nwb`, `PUT /nwb/active` (Task 7) |
| `wl_preproc/archive/reclaim.py` | the real `canonical_nwb_present` (Task 8) |
| `docs/pending-wl-works-amendments.md`, `docs/pending-wl-xcon-amendments.md` | what wl.works and wl-xcon must do (Task 9) |

---

### Task 1: Checksums become sha256

**Files:**
- Modify: `wl_preproc/nwb/checksums.py`, `wl_preproc/schema/nwb.py`, `tests/nwb/test_helpers.py`

**Interfaces — produces:** `dataset_checksums(path) -> list[dict]`, each row with key `sha256` in place of `blake3`. `nwb.NwbFile.Dataset.sha256 : char(64)`.

- [ ] **Step 1: Write the failing test.** Apply this diff:

```diff
--- a/tests/nwb/test_helpers.py
+++ b/tests/nwb/test_helpers.py
@@ -60,10 +60,22 @@ def test_checksums_are_of_contents_and_stable_across_rebuilds(tmp_path):
     assert "/intervals/task_events/start_time" in by_path
     assert not any(row["dataset_path"].startswith(("/specifications", "/file_create_date")) for row in first)
     changed_paths = {row["dataset_path"] for row in changed} & set(by_path)
-    assert [p for p in sorted(changed_paths) if {r["dataset_path"]: r for r in changed}[p]["blake3"] != by_path[p]["blake3"]] == [
+    assert [p for p in sorted(changed_paths) if {r["dataset_path"]: r for r in changed}[p]["sha256"] != by_path[p]["sha256"]] == [
         "/intervals/task_events/start_time", "/intervals/task_events/stop_time"]
 
 
+def test_each_checksum_is_the_sha256_of_the_decoded_contents(tmp_path):
+    """wl.works' Plan 24 settles the algorithm, sha256 (design spec
+    `2026-09-29-nwb-publishing-design.md` section 7): a checksum anyone can
+    recompute from the dataset's values with the standard library."""
+    import hashlib
+
+    from wl_preproc.nwb.checksums import dataset_checksums
+
+    rows = {row["dataset_path"]: row for row in dataset_checksums(_file(tmp_path, "a.nwb", [1.0, 2.0]))}
+    expected = hashlib.sha256(np.array([1.0, 2.0]).tobytes()).hexdigest()
+    assert rows["/intervals/task_events/start_time"]["sha256"] == expected
+
 def test_a_ragged_column_is_recorded_as_a_pair(tmp_path):
     from pynwb.file import Subject  # noqa: F401 -- pynwb's experimenter is a ragged-free list; build one below
     from hdmf.common import VectorData, VectorIndex
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/nwb -q --tb=line -p no:cacheprovider`
Expected: 2 failed, 18 passed, 1 skipped; both failures are `KeyError: 'sha256'`.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/nwb/checksums.py
+++ b/wl_preproc/nwb/checksums.py
@@ -1,6 +1,8 @@
 """Checksums of the file's written-once datasets (parent spec section 8.2,
 for wl.works Plan 24 section 3.3; design spec
-`2026-09-28-nwb-builder-design.md` section 7).
+`2026-09-28-nwb-builder-design.md` section 7), as `sha256`: wl.works' Plan 24
+settles the algorithm (design spec `2026-09-29-nwb-publishing-design.md`
+section 7).
 
 **Each dataset's DECODED contents, never a group's**: `colnames` is an
 attribute that changes when a column is appended, so a group checksum
@@ -11,9 +13,9 @@ carries, not data) and `/file_create_date` (when it was written)."""
 
 from __future__ import annotations
 
+import hashlib
 from pathlib import Path
 
-import blake3
 import h5py
 import numpy as np
 
@@ -35,7 +37,7 @@ def _content_bytes(dataset: h5py.Dataset) -> bytes:
 
 
 def dataset_checksums(path: Path) -> list[dict]:
-    """One row per dataset: `dataset_path`, `dtype`, `shape`, `blake3` and
+    """One row per dataset: `dataset_path`, `dtype`, `shape`, `sha256` and
     `paired_with` (a ragged column's other half, '' otherwise)."""
     rows = []
     with h5py.File(path, "r") as handle:
@@ -56,7 +58,7 @@ def dataset_checksums(path: Path) -> list[dict]:
                 "dataset_path": name,
                 "dtype": str(dataset.dtype),
                 "shape": str(tuple(dataset.shape)),
-                "blake3": blake3.blake3(_content_bytes(dataset)).hexdigest(),
+                "sha256": hashlib.sha256(_content_bytes(dataset)).hexdigest(),
                 "paired_with": paired if paired in present else "",
             })
     return rows
```

```diff
--- a/wl_preproc/schema/nwb.py
+++ b/wl_preproc/schema/nwb.py
@@ -52,7 +52,7 @@ class NwbFile(dj.Manual):
         ---
         dtype : varchar(64)
         shape : varchar(64)
-        blake3 : char(64)
+        sha256 : char(64)
         paired_with = '' : varchar(512)
         """
 
```

- [ ] **Step 4: Run it to verify it passes**

Run: the Step 2 command. Expected: 20 passed, 1 skipped.

- [ ] **Step 5: Mutation check (T1a).** In `checksums.py`, replace `hashlib.sha256(_content_bytes(dataset))` with `hashlib.sha512(_content_bytes(dataset))`. `test_each_checksum_is_the_sha256_of_the_decoded_contents` must fail; measured, it does.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/nwb/checksums.py wl_preproc/schema/nwb.py tests/nwb/test_helpers.py
git commit -m "feat(nwb): per-dataset checksums are sha256, as wl.works' Plan 24 settles

<trailer lines>"
```

---

### Task 2: The rig's trial record

**Files:**
- Create: `wl_preproc/events/rigtrials.py`, `wl_preproc/nwb/conditions.py`, `tests/events/test_rigtrials.py`, `tests/nwb/test_conditions.py`
- Modify: `wl_preproc/synth/peripherals.py`, `wl_preproc/synth/session.py`, `tests/synth/test_peripherals.py`, `tests/synth/test_session.py`

**Interfaces — produces:**
- `events.rigtrials.read_rig_trials(session_dir, subject) -> RigRecord | None`, where:
  - `RigRecord(trials: tuple[RigTrial, ...], problems: tuple[str, ...])`;
  - `RigTrial(index, outcome, block, condition, params)`.
- `nwb.conditions.join(trials, record) -> (dict[trial_id, RigTrial], notes)`.
- `nwb.conditions.stream_codes(trials, events) -> dict[trial_id, int]`.
- `nwb.conditions.block_conditions(trials, matched, codes) -> list[dict]`. Each entry is `{name, code, settings, varying, trials: {total, by_outcome}}`.
- `nwb.conditions.trial_columns(trials, matched, codes) -> (conditions, settings)`.
- `synth.peripherals.rig_condition(trial_id) -> (name, params)` and `write_rig_trials(path, recipe, truth)`. `generate_session` writes `xcon/trials.jsonl` for every synthetic session.

Trials here are dicts with `trial_id`, `start_s`, `stop_s` and `outcome`. Events are dicts with `time_s`, `event_type` and `condition`.

- [ ] **Step 1: Write the failing tests.** Create `tests/events/test_rigtrials.py`:

```python
"""The rig's own trial record, `xcon/trials.jsonl` (design spec
`2026-09-29-nwb-publishing-design.md` section 2.1)."""

from __future__ import annotations

import json


def _record(tmp_path, lines):
    (tmp_path / "xcon").mkdir()
    (tmp_path / "xcon" / "trials.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


def _line(index, subject="pico", condition="contrast-50", **params):
    return json.dumps({"index": index, "subject": subject, "outcome": "correct", "block": "main",
                       "condition": condition, "params": params or {"contrast": 0.5}})


def test_each_line_is_one_of_this_subjects_trials(tmp_path):
    """wl-xcon's `Recorder.trial` writes one JSON object per trial, with the
    subject on every line because two animals share one day's directory."""
    from wl_preproc.events.rigtrials import RigTrial, read_rig_trials

    session = _record(tmp_path, [_line(0), _line(1, subject="other"), _line(2, contrast=0.25)])
    record = read_rig_trials(session, "pico")
    assert record.trials == (
        RigTrial(index=0, outcome="correct", block="main", condition="contrast-50", params={"contrast": 0.5}),
        RigTrial(index=2, outcome="correct", block="main", condition="contrast-50", params={"contrast": 0.25}),
    )
    assert record.problems == ()


def test_a_bad_line_is_reported_by_number_and_left_out(tmp_path):
    from wl_preproc.events.rigtrials import read_rig_trials

    bad_index = json.dumps({"index": "7", "subject": "pico", "outcome": "correct", "block": "main",
                            "condition": "c", "params": {}})
    session = _record(tmp_path, [_line(0), "not json", json.dumps({"index": 1}), bad_index, _line(3)])
    record = read_rig_trials(session, "pico")
    assert [trial.index for trial in record.trials] == [0, 3]
    assert [problem.split(":")[0] for problem in record.problems] == ["line 2", "line 3", "line 4"]


def test_a_session_without_a_record_has_none(tmp_path):
    from wl_preproc.events.rigtrials import read_rig_trials

    assert read_rig_trials(tmp_path, "pico") is None
```

Create `tests/nwb/test_conditions.py`:

```python
"""What actually ran, by condition and stimulus settings (design spec
`2026-09-29-nwb-publishing-design.md` sections 2.1 and 2.2)."""

from __future__ import annotations

import math


def _trial(trial_id, start_s, outcome="correct"):
    return {"trial_id": trial_id, "start_s": start_s, "stop_s": start_s + 1.0, "outcome": outcome}


def _rig(index, condition, **params):
    from wl_preproc.events.rigtrials import RigTrial

    return RigTrial(index=index, outcome="correct", block="main", condition=condition, params=params)


def test_trials_join_the_rig_record_by_trial_number_only():
    """A trial number the record repeats, or never names, joins nothing, and
    the notes say so: no guessing."""
    from wl_preproc.events.rigtrials import RigRecord
    from wl_preproc.nwb.conditions import join

    trials = [_trial(1, 0.0), _trial(2, 1.0), _trial(3, 2.0)]
    record = RigRecord(trials=(_rig(1, "a"), _rig(2, "b"), _rig(2, "b"), _rig(9, "z")),
                       problems=("line 5: not JSON",))
    matched, notes = join(trials, record)
    assert list(matched) == [1]
    assert notes == ["line 5: not JSON", "1 trial number(s) appear more than once in the rig record",
                     "2 trial(s) have no single line in the rig record"]
    assert join(trials, None) == ({}, ["no rig trial record (xcon/trials.jsonl)"])


def test_a_trials_code_is_the_one_condition_event_inside_it():
    from wl_preproc.nwb.conditions import stream_codes

    trials = [_trial(1, 0.0), _trial(2, 1.0), _trial(3, 2.0)]
    events = [{"time_s": 0.5, "event_type": "CONDITION", "condition": "7"},
              {"time_s": 1.2, "event_type": "CONDITION", "condition": "3"},
              {"time_s": 1.4, "event_type": "CONDITION", "condition": "4"},
              {"time_s": 2.5, "event_type": "TRIAL_START", "condition": None}]
    assert stream_codes(trials, events) == {1: 7}


def test_a_blocks_conditions_are_what_ran_by_settings():
    """Constant settings are the condition's; a number that varied within it
    is a range, anything else its distinct values; counts are by outcome."""
    from wl_preproc.nwb.conditions import block_conditions

    trials = [_trial(1, 0.0), _trial(2, 1.0, outcome="error"), _trial(3, 2.0), _trial(4, 3.0)]
    matched = {1: _rig(1, "c50", contrast=0.5, hold=0.3, xy=[0, 5]),
               2: _rig(2, "c50", contrast=0.5, hold=0.35, xy=[0, 5]),
               3: _rig(3, "c25", contrast=0.25, side="left"),
               4: _rig(4, "c25", contrast=0.25, side="right")}
    assert block_conditions(trials, matched, {1: 7, 2: 7}) == [
        {"name": "c25", "code": None, "settings": {"contrast": 0.25},
         "varying": {"side": {"values": ["left", "right"], "n_distinct": 2}},
         "trials": {"total": 2, "by_outcome": {"correct": 2}}},
        {"name": "c50", "code": 7, "settings": {"contrast": 0.5, "xy": [0, 5]},
         "varying": {"hold": {"min": 0.3, "max": 0.35}},
         "trials": {"total": 2, "by_outcome": {"correct": 1, "error": 1}}},
    ]


def test_without_the_rig_record_a_condition_is_its_stream_number():
    from wl_preproc.nwb.conditions import block_conditions

    trials = [_trial(1, 0.0), _trial(2, 1.0), _trial(3, 2.0)]
    assert block_conditions(trials, {}, {1: 7, 2: 7}) == [
        {"name": None, "code": 7, "settings": None, "varying": None,
         "trials": {"total": 2, "by_outcome": {"correct": 2}}},
    ]


def test_the_trials_table_gets_the_condition_and_each_varying_setting():
    from wl_preproc.nwb.conditions import trial_columns

    trials = [_trial(1, 0.0), _trial(2, 1.0), _trial(3, 2.0), _trial(4, 3.0)]
    matched = {1: _rig(1, "c50", contrast=0.5, hold=0.3, xy=[0, 5], fixed=1),
               2: _rig(2, "c25", contrast=0.25, hold=0.3, xy=[0, 6], fixed=1),
               3: _rig(3, "c50", contrast=0.5, xy=[0, 5], fixed=1)}
    conditions, settings = trial_columns(trials, matched, {4: 9})
    assert conditions == ["c50", "c25", "c50", "9"]
    assert sorted(settings) == ["contrast", "xy"]
    assert settings["contrast"][:3] == [0.5, 0.25, 0.5] and math.isnan(settings["contrast"][3])
    assert settings["xy"] == ["[0, 5]", "[0, 6]", "[0, 5]", ""]


def test_a_setting_whose_type_differs_between_trials_is_text():
    """Review Focus 3 (the plan): the rig's parameters are an untyped dict,
    so one setting can be a number on one trial and text on the next. It is
    written as JSON text and summarised by its distinct values, never a
    crash and never a numeric column with holes."""
    from wl_preproc.nwb.conditions import block_conditions, trial_columns

    trials = [_trial(1, 0.0), _trial(2, 1.0)]
    matched = {1: _rig(1, "c", target=5.0), 2: _rig(2, "c", target="left")}
    _conditions, settings = trial_columns(trials, matched, {})
    assert settings["target"] == ["5.0", '"left"']
    (entry,) = block_conditions(trials, matched, {})
    assert entry["varying"] == {"target": {"values": [5.0, "left"], "n_distinct": 2}}
```

Apply these diffs:

```diff
--- a/tests/synth/test_peripherals.py
+++ b/tests/synth/test_peripherals.py
@@ -126,3 +126,21 @@ def test_camera_fps_has_margin_over_the_floor():
     from wl_preproc.timebase.extract import min_sample_rate_hz
 
     assert CAMERA_FPS >= 1.25 * min_sample_rate_hz()
+
+
+def test_the_rig_record_has_wl_xcons_line_shape(tmp_path):
+    """One line per planted trial, each with the fields wl-xcon's
+    `Recorder.trial` writes and the subject on every line (design spec
+    `2026-09-29-nwb-publishing-design.md` section 13)."""
+    from wl_preproc.events.rigtrials import read_rig_trials
+    from wl_preproc.synth.peripherals import write_rig_trials
+
+    truth = build_timeline(CI_RECIPE)
+    (tmp_path / "xcon").mkdir()
+    write_rig_trials(tmp_path / "xcon" / "trials.jsonl", CI_RECIPE, truth)
+    lines = [json.loads(line) for line in (tmp_path / "xcon" / "trials.jsonl").read_text().splitlines()]
+    assert all(set(line) == {"index", "subject", "outcome", "block", "condition", "params"} for line in lines)
+    record = read_rig_trials(tmp_path, CI_RECIPE.subject)
+    assert record.problems == ()
+    assert [trial.index for trial in record.trials] == [trial.trial_id for trial in truth.trials]
+    assert {trial.condition for trial in record.trials} <= {"contrast-10", "contrast-25", "contrast-50", "contrast-100"}
```

```diff
--- a/tests/synth/test_session.py
+++ b/tests/synth/test_session.py
@@ -104,3 +104,10 @@ def test_every_system_declares_the_files_it_wrote(tmp_path, recipe):
         assert {entry.path for entry in marker.files} == on_disk
         for entry in marker.files:
             assert (system_dir / entry.path).stat().st_size == entry.bytes
+
+
+def test_the_rigs_trial_record_is_written_beside_the_systems(tmp_path):
+    """`xcon/` is not one of SYSTEMS; the rig writes it beside them."""
+    truth = generate_session(tmp_path, CI_RECIPE)
+    layout = SessionLayout(tmp_path, SessionId.parse(CI_RECIPE.session_id))
+    assert len((layout.xcon_dir / "trials.jsonl").read_text().splitlines()) == len(truth.trials)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/events/test_rigtrials.py tests/nwb/test_conditions.py tests/synth/test_peripherals.py tests/synth/test_session.py -q --tb=line -p no:cacheprovider`
Expected: 11 failed, 17 passed: `ModuleNotFoundError` for `wl_preproc.events.rigtrials` and for `wl_preproc.nwb.conditions`, and a `FileNotFoundError` for the generated session's missing `xcon/trials.jsonl`.

- [ ] **Step 3: Implement.** Create `wl_preproc/events/rigtrials.py`:

```python
"""The rig's own record of every trial: wl-xcon's `xcon/trials.jsonl`
(design spec `2026-09-29-nwb-publishing-design.md` section 2.1).

**Codes own identity and timing; the rig's record owns content.** wl-xcon's own
allocation rule, verbatim: "Event codes carry identity and timing. The session
record carries content." (wl-xcon's S2 event-vocabulary design.) Each line of
the record is one trial -- `wl_xcon/record.py::Recorder.trial` -- with its
`index`, `subject`, `outcome`, the scheduler's `block` and `condition` names,
and `params`, "the whole resolved parameter set, per trial": the stimulus
settings it actually ran with.

**Read, never repaired.** A line that is not a JSON object with those fields is
reported as a problem, by line number, and left out; nothing is guessed. Two
animals routinely share one day's session directory, so the record carries the
subject on every line and only this subject's lines are kept.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from wl_preproc.contracts.paths import XCON_DIRNAME

RECORD_NAME = "trials.jsonl"
_FIELDS = ("index", "subject", "outcome", "block", "condition", "params")


@dataclasses.dataclass(frozen=True, slots=True)
class RigTrial:
    index: int
    outcome: str
    block: str
    condition: str
    params: dict


@dataclasses.dataclass(frozen=True, slots=True)
class RigRecord:
    trials: tuple[RigTrial, ...]
    problems: tuple[str, ...]


def read_rig_trials(session_dir: Path, subject: str) -> RigRecord | None:
    """This subject's trials from `<session>/xcon/trials.jsonl`, or `None`
    when the session has no such record."""
    path = Path(session_dir) / XCON_DIRNAME / RECORD_NAME
    if not path.is_file():
        return None
    trials, problems = [], []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {number}: not JSON")
            continue
        if not isinstance(row, dict) or any(field not in row for field in _FIELDS):
            problems.append(f"line {number}: missing one of {', '.join(_FIELDS)}")
            continue
        if not isinstance(row["index"], int) or isinstance(row["index"], bool) or not isinstance(row["params"], dict):
            problems.append(f"line {number}: index is not an integer or params is not an object")
            continue
        if row["subject"] != subject:
            continue
        trials.append(RigTrial(index=row["index"], outcome=str(row["outcome"]), block=str(row["block"]),
                               condition=str(row["condition"]), params=row["params"]))
    return RigRecord(trials=tuple(trials), problems=tuple(problems))
```

Create `wl_preproc/nwb/conditions.py`:

```python
"""What actually ran, by condition and stimulus settings (design spec
`2026-09-29-nwb-publishing-design.md` sections 2.1 and 2.2).

Pure functions over plain data. The trials are this file's own, with ids from
the stream's `TRIAL_NUMBER`; the rig's record (`events/rigtrials.py`) is joined
to them by that number and nothing else. **No guessing**: a trial the record
does not name exactly once gets no condition and no settings, and the notes say
why."""

from __future__ import annotations

import collections
import json
import math

from wl_preproc.events.rigtrials import RigRecord, RigTrial

# A varying setting that is not a number is summarised by its distinct values,
# at most this many.
MAX_DISTINCT = 20


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _text(value) -> str:
    return json.dumps(value, sort_keys=True)


def join(trials: list[dict], record: RigRecord | None) -> tuple[dict[int, RigTrial], list[str]]:
    """`{trial_id: RigTrial}` for every trial the rig's record names exactly
    once, and the notes on everything that did not join."""
    if record is None:
        return {}, ["no rig trial record (xcon/trials.jsonl)"]
    notes = list(record.problems)
    counts = collections.Counter(trial.index for trial in record.trials)
    by_index = {trial.index: trial for trial in record.trials if counts[trial.index] == 1}
    repeated = sorted(index for index, count in counts.items() if count > 1)
    if repeated:
        notes.append(f"{len(repeated)} trial number(s) appear more than once in the rig record")
    matched = {trial["trial_id"]: by_index[trial["trial_id"]] for trial in trials if trial["trial_id"] in by_index}
    unmatched = len(trials) - len(matched)
    if unmatched:
        notes.append(f"{unmatched} trial(s) have no single line in the rig record")
    return matched, notes


def stream_codes(trials: list[dict], events: list[dict]) -> dict[int, int]:
    """`{trial_id: code}` where exactly one `CONDITION` event was sent inside
    the trial, `[start, stop)`."""
    codes = [(event["time_s"], int(event["condition"])) for event in events
             if event["event_type"] == "CONDITION" and event.get("condition") not in (None, "")]
    out = {}
    for trial in trials:
        inside = [code for time_s, code in codes if trial["start_s"] <= time_s < trial["stop_s"]]
        if len(inside) == 1:
            out[trial["trial_id"]] = inside[0]
    return out


def _summarise(values: list) -> dict:
    if all(_is_number(value) for value in values):
        return {"min": min(values), "max": max(values)}
    distinct, seen = [], set()
    for value in values:
        if _text(value) not in seen:
            seen.add(_text(value))
            distinct.append(value)
    return {"values": distinct[:MAX_DISTINCT], "n_distinct": len(distinct)}


def block_conditions(trials: list[dict], matched: dict[int, RigTrial], codes: dict[int, int]) -> list[dict]:
    """One entry per condition that ran among `trials` (one block's).

    With the rig's record, by condition name: the settings constant across
    that condition's trials, a summary of each that varied, and trial counts.
    Without it, by the stream's `CONDITION` number, settings unknown."""
    groups: dict = {}
    for trial in trials:
        rig = matched.get(trial["trial_id"])
        if rig is not None:
            groups.setdefault(("name", rig.condition), []).append((trial, rig))
        elif trial["trial_id"] in codes:
            groups.setdefault(("code", codes[trial["trial_id"]]), []).append((trial, None))
    out = []
    for (kind, label), members in sorted(groups.items(), key=lambda item: (item[0][0], str(item[0][1]))):
        outcomes = collections.Counter(trial["outcome"] or "unknown" for trial, _ in members)
        member_codes = {codes.get(trial["trial_id"]) for trial, _ in members}
        entry = {
            "name": label if kind == "name" else None,
            "code": label if kind == "code" else (member_codes.pop() if len(member_codes) == 1 else None),
            "settings": None,
            "varying": None,
            "trials": {"total": len(members), "by_outcome": dict(sorted(outcomes.items()))},
        }
        if kind == "name":
            keys = sorted(set().union(*(rig.params.keys() for _, rig in members)))
            settings, varying = {}, {}
            for key in keys:
                values = [rig.params.get(key) for _, rig in members]
                if len({_text(value) for value in values}) == 1:
                    settings[key] = values[0]
                else:
                    varying[key] = _summarise([value for value in values if value is not None])
            entry["settings"], entry["varying"] = settings, varying
        out.append(entry)
    return out


def trial_columns(trials: list[dict], matched: dict[int, RigTrial], codes: dict[int, int]) -> tuple[list[str], dict[str, list]]:
    """The trials table's `condition` (the name, else the stream's number as
    text, else '') and one list per setting that varies across the matched
    trials: numbers as float (NaN where absent), anything else as JSON text
    ('' where absent)."""
    conditions = []
    for trial in trials:
        rig = matched.get(trial["trial_id"])
        if rig is not None:
            conditions.append(rig.condition)
        elif trial["trial_id"] in codes:
            conditions.append(str(codes[trial["trial_id"]]))
        else:
            conditions.append("")
    rigs = [matched.get(trial["trial_id"]) for trial in trials]
    keys = sorted(set().union(*(rig.params.keys() for rig in rigs if rig is not None)))
    settings = {}
    for key in keys:
        values = [rig.params.get(key) if rig is not None else None for rig in rigs]
        present = [value for value in values if value is not None]
        if len({_text(value) for value in present}) <= 1:
            continue
        if all(_is_number(value) for value in present):
            settings[key] = [math.nan if value is None else float(value) for value in values]
        else:
            settings[key] = ["" if value is None else _text(value) for value in values]
    return conditions, settings
```

Apply these diffs:

```diff
--- a/wl_preproc/synth/peripherals.py
+++ b/wl_preproc/synth/peripherals.py
@@ -143,3 +143,40 @@ def write_task_file(path: Path, truth: GroundTruth) -> None:
         ],
     }
     path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
+
+
+# The rig's per-trial record, as wl-xcon writes it (`wl_xcon/record.py::
+# Recorder.trial`): four contrasts, one fixed orientation and target, and a
+# fixation hold that varies WITHIN one condition -- so every branch of the
+# builder's condition summary has something to read (design spec
+# `2026-09-29-nwb-publishing-design.md` section 13).
+RIG_CONTRASTS = (0.10, 0.25, 0.50, 1.00)
+
+
+def rig_condition(trial_id: int) -> tuple[str, dict]:
+    """The synthetic rig's condition name and resolved parameters for a trial."""
+    contrast = RIG_CONTRASTS[trial_id % len(RIG_CONTRASTS)]
+    hold_s = 0.35 if contrast == 0.50 and (trial_id // len(RIG_CONTRASTS)) % 2 else 0.30
+    return f"contrast-{round(contrast * 100)}", {
+        "contrast": contrast,
+        "orientation_deg": 45.0,
+        "fix_hold_s": hold_s,
+        "target_xy_deg": [0.0, 5.0],
+    }
+
+
+def write_rig_trials(path: Path, recipe: SessionRecipe, truth: GroundTruth) -> None:
+    """Stands in for wl-xcon's `xcon/trials.jsonl`: one JSON object per line,
+    per trial, with the subject on every line."""
+    lines = []
+    for trial in truth.trials:
+        condition, params = rig_condition(trial.trial_id)
+        lines.append(json.dumps({
+            "index": trial.trial_id,
+            "subject": recipe.subject,
+            "outcome": "correct",
+            "block": f"block-{trial.block_id}",
+            "condition": condition,
+            "params": params,
+        }, sort_keys=True))
+    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
```

```diff
--- a/wl_preproc/synth/session.py
+++ b/wl_preproc/synth/session.py
@@ -15,6 +15,7 @@ from wl_preproc.synth.peripherals import (
     camera_frame_count,
     write_camera_sidecar,
     write_manifest,
+    write_rig_trials,
     write_task_file,
 )
 from wl_preproc.synth.ohdpi import write_ohdpi
@@ -58,6 +59,10 @@ def generate_session(root: Path, recipe: SessionRecipe) -> GroundTruth:
     layout = SessionLayout(root, SessionId.parse(recipe.session_id))
     layout.dir.mkdir(parents=True, exist_ok=True)
     write_manifest(layout.manifest_path, recipe)
+    # The rig's own trial record, which is not one of SYSTEMS (wl-xcon writes
+    # its folder beside them).
+    layout.xcon_dir.mkdir(exist_ok=True)
+    write_rig_trials(layout.xcon_dir / "trials.jsonl", recipe, truth)
 
     rng = np.random.default_rng(recipe.seed + 2)
     finished_at = SYNTH_EPOCH + datetime.timedelta(seconds=recipe.duration_s)
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 28 passed.

Then, since every synthetic session now carries `xcon/trials.jsonl`, run `.venv/bin/python -m pytest tests/synth tests/archive tests/ingest tests/events -q -p no:cacheprovider`. Expected: all pass (measured: 387 passed before Review Focus 3's test was added).

- [ ] **Step 5: Mutation checks.** Each was measured to fail the test named in brackets.
  - T2a (`rigtrials.py`): in `read_rig_trials`, the subject check `if row["subject"] != subject:` becomes `if False:`. Fails `test_each_line_is_one_of_this_subjects_trials`.
  - T2b (`conditions.py`): `if counts[trial.index] == 1}` becomes `if counts[trial.index] >= 1}`. Fails `test_trials_join_the_rig_record_by_trial_number_only`.
  - T2c (`conditions.py`): `if len(inside) == 1:` becomes `if inside:`. Fails `test_a_trials_code_is_the_one_condition_event_inside_it`.
  - T2d (`conditions.py`): `if len({_text(value) for value in values}) == 1:` becomes `if True:`. Fails `test_a_blocks_conditions_are_what_ran_by_settings` and `test_a_setting_whose_type_differs_between_trials_is_text`.
  - T2e (`conditions.py`): `if len({_text(value) for value in present}) <= 1:` becomes `if False:`. Fails `test_the_trials_table_gets_the_condition_and_each_varying_setting`.
  - T2f (`conditions.py`): in `_summarise`, `if all(_is_number(value) for value in values):` becomes `if True:`. Fails `test_a_blocks_conditions_are_what_ran_by_settings` and `test_a_setting_whose_type_differs_between_trials_is_text`.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/events/rigtrials.py wl_preproc/nwb/conditions.py wl_preproc/synth tests/events/test_rigtrials.py tests/nwb/test_conditions.py tests/synth
git commit -m "feat(nwb): the rig's own trial record, joined by trial number, gives each block's conditions by their stimulus settings

<trailer lines>"
```

---

### Task 3: Conditions and settings in the file

**Files:**
- Modify: `wl_preproc/nwb/intervals.py`, `wl_preproc/nwb/gather.py`, `wl_preproc/nwb/build.py`, `tests/nwb/test_writers.py`, `tests/schema/test_nwb_build.py`

**Interfaces:**
- **Consumes:** Task 2's `read_rig_trials`, `join`, `stream_codes`, `block_conditions`, `trial_columns`.
- **Produces:**
  - `intervals.add_trials` reads each trial's `condition` and `settings` (`{name: value}`, the same keys on every trial) when present.
  - `intervals.add_conditions(nwb, conditions)` writes `processing/behavior/conditions`, or nothing when the list is empty.
  - `gather.Gathered` gains `conditions` and `condition_notes`, and each block dict gains `trials` and `conditions`.

- [ ] **Step 1: Write the failing tests.** Apply these diffs:

```diff
--- a/tests/nwb/test_writers.py
+++ b/tests/nwb/test_writers.py
@@ -78,6 +78,42 @@ def test_blocks_trials_and_events(tmp_path):
         assert events["trial_id"].tolist() == [1, -1]
 
 
+def test_the_trials_carry_their_condition_and_the_settings_that_varied(tmp_path):
+    """Design spec `2026-09-29-nwb-publishing-design.md` section 2.2: the
+    condition's name, and one column per setting that varied."""
+    from wl_preproc.nwb.intervals import add_trials
+
+    trials = [{**TRIALS[0], "condition": "contrast-50", "settings": {"contrast": 0.5, "xy": "[0, 5]"}},
+              {**TRIALS[0], "trial_id": 2, "start_s": 4.0, "stop_s": 5.0, "condition": "",
+               "settings": {"contrast": np.nan, "xy": ""}}]
+    _path, io, nwb = _write(tmp_path, lambda nwb: add_trials(nwb, trials, ["ohdpi"]))
+    with io:
+        frame = nwb.trials.to_dataframe()
+        assert frame["condition"].tolist() == ["contrast-50", ""]
+        assert frame["setting_contrast"].iloc[0] == 0.5 and np.isnan(frame["setting_contrast"].iloc[1])
+        assert frame["setting_xy"].tolist() == ["[0, 5]", ""]
+
+
+def test_the_conditions_table_and_none_when_no_condition_is_known(tmp_path):
+    from wl_preproc.nwb.intervals import add_conditions
+
+    conditions = [{"name": "contrast-50", "code": None, "settings": {"contrast": 0.5},
+                   "varying": {"hold": {"min": 0.3, "max": 0.35}}, "trials": {"total": 2, "by_outcome": {"correct": 2}}},
+                  {"name": None, "code": 7, "settings": None, "varying": None,
+                   "trials": {"total": 1, "by_outcome": {"correct": 1}}}]
+    _path, io, nwb = _write(tmp_path, lambda nwb: add_conditions(nwb, conditions))
+    with io:
+        frame = nwb.processing["behavior"]["conditions"].to_dataframe()
+        assert frame["condition"].tolist() == ["", "contrast-50"]
+        assert frame["code"].tolist() == [7, -1]
+        assert frame["settings"].tolist() == ["", '{"contrast": 0.5}']
+        assert frame["varying"].tolist() == ["", '{"hold": {"max": 0.35, "min": 0.3}}']
+        assert frame["n_trials"].tolist() == [1, 2]
+    (tmp_path / "empty").mkdir()
+    _path, io, nwb = _write(tmp_path / "empty", lambda nwb: add_conditions(nwb, []))
+    with io:
+        assert "behavior" not in nwb.processing
+
 def test_the_timebase_tables(tmp_path):
     from wl_preproc.nwb.timebase import add_timebase
 
```

```diff
--- a/tests/schema/test_nwb_build.py
+++ b/tests/schema/test_nwb_build.py
@@ -5,6 +5,7 @@ from __future__ import annotations
 
 import datetime
 import io
+import json
 from pathlib import Path
 
 import h5py
@@ -160,6 +161,27 @@ def test_blocks_trials_and_events_carry_the_tables_times(activation, built):
         assert not {"SESSION_START", "SESSION_END"} & set(events["event_type"])
 
 
+def test_the_trials_carry_the_rigs_conditions_and_settings(activation, built):
+    """Design spec `2026-09-29-nwb-publishing-design.md` sections 2.1 and
+    2.2: the synthetic rig record (`synth/peripherals.py::rig_condition`),
+    joined by trial number. A setting constant across the session is the
+    conditions table's, never a trials column."""
+    from pynwb import NWBHDF5IO
+
+    from wl_preproc.synth.peripherals import rig_condition
+
+    with NWBHDF5IO(str(built.path), "r") as handle:
+        nwb = handle.read()
+        trials = nwb.trials.to_dataframe()
+        expected = [rig_condition(int(trial_id)) for trial_id in trials["trial_id"]]
+        assert trials["condition"].tolist() == [name for name, _ in expected]
+        assert trials["setting_contrast"].tolist() == [params["contrast"] for _, params in expected]
+        assert "setting_orientation_deg" not in trials.columns
+        conditions = nwb.processing["behavior"]["conditions"].to_dataframe()
+        assert sorted(conditions["condition"]) == sorted({name for name, _ in expected})
+        assert all(json.loads(settings)["orientation_deg"] == 45.0 for settings in conditions["settings"])
+
+
 def test_the_eye_is_on_session_time_and_every_detector_is_there(activation, built):
     from pynwb import NWBHDF5IO
 
@@ -354,7 +376,8 @@ def test_a_session_without_an_eye_recording_is_built_without_one(activation, mon
     assert result.status == "written", result.findings
     with NWBHDF5IO(str(result.path), "r") as handle:
         nwb = handle.read()
-        assert set(nwb.processing) == {"timebase"}
+        assert "eye_events" not in nwb.processing
+        assert not {"EyeTracking", "PupilTracking"} & set(nwb.processing["behavior"].data_interfaces)
         assert len(nwb.trials) and len(nwb.intervals["task_events"])
 
 
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/nwb/test_writers.py tests/schema/test_nwb_build.py -q --tb=line -p no:cacheprovider`
Expected: 4 failed, 33 passed, on `KeyError: 'condition'`, `KeyError: 'behavior'` and `ImportError: cannot import name 'add_conditions'`.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/nwb/intervals.py
+++ b/wl_preproc/nwb/intervals.py
@@ -3,6 +3,8 @@
 
 from __future__ import annotations
 
+import json
+
 import numpy as np
 from pynwb import NWBFile
 from pynwb.epoch import TimeIntervals
@@ -48,12 +50,25 @@ def add_blocks(nwb: NWBFile, blocks: list[dict], systems: list[str]) -> None:
     ))
 
 
+def _setting_columns(rows: list[dict]) -> list:
+    """One column per setting that varied across the session's trials
+    (design spec `2026-09-29-nwb-publishing-design.md` section 2.2)."""
+    keys = sorted(set().union(*(row.get("settings", {}).keys() for row in rows))) if rows else []
+    return [column(f"setting_{key}",
+                   f"The rig's resolved {key} for the trial (xcon/trials.jsonl): numbers as numbers, anything "
+                   "else as JSON text; NaN or '' where the trial has no line in that record.",
+                   [row["settings"][key] for row in rows])
+            for key in keys]
+
+
 def add_trials(nwb: NWBFile, trials: list[dict], systems: list[str]) -> None:
-    """`/intervals/trials`: trial id, outcome, block and per-system coverage."""
+    """`/intervals/trials`: trial id, outcome, block, condition, the
+    settings that varied, and per-system coverage."""
     rows = sorted(trials, key=lambda row: row["start_s"])
     nwb.trials = TimeIntervals(
         name="trials",
-        description="Trials decoded from the event codes, with their outcome and per-system coverage.",
+        description=("Trials decoded from the event codes, with their outcome, the condition they ran under, "
+                     "the stimulus settings that varied across the session, and per-system coverage."),
         columns=[
             column("start_time", "Trial start, session seconds.", [r["start_s"] for r in rows]),
             column("stop_time", "Trial stop, session seconds.", [r["stop_s"] for r in rows]),
@@ -61,11 +76,48 @@ def add_trials(nwb: NWBFile, trials: list[dict], systems: list[str]) -> None:
             column("outcome", "correct, error, abort, fixation_break or no_response.", [r["outcome"] or "" for r in rows]),
             column("block_id", "The measured block the trial belongs to (-1 if none).",
                    [-1 if r["block_id"] is None else r["block_id"] for r in rows]),
+            column("condition", ("The condition the trial ran under: its name in the rig's record "
+                                 "(xcon/trials.jsonl), else the CONDITION number sent inside it, else ''."),
+                   [r.get("condition", "") for r in rows]),
+            *_setting_columns(rows),
             *_coverage_columns(rows, systems),
         ],
     )
 
 
+def add_conditions(nwb: NWBFile, conditions: list[dict]) -> None:
+    """`processing/behavior/conditions`: one row per condition that ran, with
+    the settings constant across its trials and those that varied (design
+    spec `2026-09-29-nwb-publishing-design.md` section 2.2). Nothing is
+    written when no condition is known."""
+    if not conditions:
+        return
+    from pynwb.core import DynamicTable
+
+    from wl_preproc.nwb.eye import behavior_module
+
+    rows = sorted(conditions, key=lambda row: (row["name"] or "", -1 if row["code"] is None else row["code"]))
+    behavior_module(nwb).add(DynamicTable(
+        name="conditions",
+        description=("Every condition that ran in the file's trials: its name in the rig's record and the "
+                     "CONDITION number sent for it (-1 where none), the settings constant across its trials "
+                     "and a summary of those that varied, both as JSON ('' where the rig's record is absent)."),
+        columns=[
+            # `condition`, not `name`: a DynamicTable's own `name` attribute
+            # would shadow a column called that.
+            column("condition", "The condition's name in the rig's record ('' where only its number is known).",
+                   [row["name"] or "" for row in rows]),
+            column("code", "The CONDITION number sent for it (-1 where none).",
+                   [-1 if row["code"] is None else row["code"] for row in rows]),
+            column("settings", "The settings constant across its trials, as JSON.",
+                   ["" if row["settings"] is None else json.dumps(row["settings"], sort_keys=True) for row in rows]),
+            column("varying", "Each setting that varied within it: a range for numbers, else its distinct values, as JSON.",
+                   ["" if row["varying"] is None else json.dumps(row["varying"], sort_keys=True) for row in rows]),
+            column("n_trials", "How many of the file's trials ran under it.", [row["trials"]["total"] for row in rows]),
+        ],
+    ))
+
+
 def add_task_events(nwb: NWBFile, events: list[dict]) -> None:
     """`/intervals/task_events`: every decoded event code.
 
```

```diff
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -41,6 +41,11 @@ class Gathered:
     events: list[dict]
     timebase: dict
     eye: dict | None  # None: no ohDPI recording in the session, or no sample of it in the blocks
+    # Every condition that ran in the file's trials, and why any trial's
+    # condition or settings are unknown (design spec
+    # `2026-09-29-nwb-publishing-design.md` section 2.1).
+    conditions: list[dict] = dataclasses.field(default_factory=list)
+    condition_notes: list[str] = dataclasses.field(default_factory=list)
 
 
 def _aware_utc(value: datetime.datetime) -> datetime.datetime:
@@ -219,6 +224,33 @@ def _events(blocks: BlockSet, session_key: dict) -> list[dict]:
     return events
 
 
+def _conditions(trials: list[dict], events: list[dict], blocks: list[dict], session_dir: Path,
+                subject: str) -> tuple[list[dict], list[str]]:
+    """Each trial's condition and the settings that varied, and each block's
+    conditions and trial counts, from the rig's own record joined by trial
+    number (design spec `2026-09-29-nwb-publishing-design.md` sections 2.1
+    and 2.2). Returns the file's conditions and the notes on what did not
+    join."""
+    import collections
+
+    from wl_preproc.events.rigtrials import read_rig_trials
+    from wl_preproc.nwb.conditions import block_conditions, join, stream_codes, trial_columns
+
+    trials.sort(key=lambda trial: trial["start_s"])
+    matched, notes = join(trials, read_rig_trials(session_dir, subject))
+    codes = stream_codes(trials, events)
+    names, settings = trial_columns(trials, matched, codes)
+    for position, trial in enumerate(trials):
+        trial["condition"] = names[position]
+        trial["settings"] = {key: values[position] for key, values in settings.items()}
+    for block in blocks:
+        inside = [trial for trial in trials if block["start_s"] <= trial["start_s"] < block["end_s"]]
+        outcomes = collections.Counter(trial["outcome"] or "unknown" for trial in inside)
+        block["trials"] = {"total": len(inside), "by_outcome": dict(sorted(outcomes.items()))}
+        block["conditions"] = block_conditions(inside, matched, codes)
+    return block_conditions(trials, matched, codes), notes
+
+
 def _timebase(session_key: dict, provenance: dict, clock: dict) -> dict:
     from wl_preproc.schema import core, timebase
 
@@ -379,7 +411,11 @@ def gather(activation_key: dict) -> Gathered:
     if no_eye_samples:
         description += " The eye recording has no sample in these blocks, so the file has no eye data."
     requested_by = (request.Request & {"idempotency_key": activation["request_key"]}).fetch1("requested_by")
-    systems = sorted({system for row in _blocks(block_rows, session_key) for system in row["coverage"]})
+    block_out = _blocks(block_rows, session_key)
+    systems = sorted({system for row in block_out for system in row["coverage"]})
+    trials = _trials(blocks, session_key)
+    events = _events(blocks, session_key)
+    conditions, condition_notes = _conditions(trials, events, block_out, session_dir, key["subject"])
     return Gathered(
         session={
             "identifier": identifier_for(key, session_id),
@@ -391,9 +427,11 @@ def gather(activation_key: dict) -> Gathered:
             "clock": clock,
         },
         systems=systems,
-        blocks=_blocks(block_rows, session_key),
-        trials=_trials(blocks, session_key),
-        events=_events(blocks, session_key),
+        blocks=block_out,
+        trials=trials,
+        events=events,
         timebase=_timebase(session_key, provenance[0], clock),
         eye=eye,
+        conditions=conditions,
+        condition_notes=condition_notes,
     )
```

```diff
--- a/wl_preproc/nwb/build.py
+++ b/wl_preproc/nwb/build.py
@@ -11,7 +11,7 @@ from wl_preproc.nwb.checksums import dataset_checksums
 from wl_preproc.nwb.eye import add_eye_series, add_eye_tables
 from wl_preproc.nwb.eye_events import add_agreement, add_detections, add_sources
 from wl_preproc.nwb.gather import Refused, gather, readiness
-from wl_preproc.nwb.intervals import add_blocks, add_task_events, add_trials
+from wl_preproc.nwb.intervals import add_blocks, add_conditions, add_task_events, add_trials
 from wl_preproc.nwb.session import new_file
 from wl_preproc.nwb.timebase import add_timebase
 from wl_preproc.nwb.validate import inspect_file, n_critical
@@ -59,6 +59,7 @@ def build(activation_key: dict, nwb_root: Path) -> BuildResult:
     nwb = new_file(data.session)
     add_blocks(nwb, data.blocks, data.systems)
     add_trials(nwb, data.trials, data.systems)
+    add_conditions(nwb, data.conditions)
     add_task_events(nwb, data.events)
     add_timebase(nwb, **data.timebase)
     if data.eye is not None:
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 37 passed.

- [ ] **Step 5: Mutation checks.**
  - T3a (`gather.py`): `trial["condition"] = names[position]` becomes `trial["condition"] = ""`. Fails `test_the_trials_carry_the_rigs_conditions_and_settings`.
  - T3b (`intervals.py`): in `add_conditions`, `if not conditions:` becomes `if False:`. Fails `test_the_conditions_table_and_none_when_no_condition_is_known`.
  - T3c (`intervals.py`): `*_setting_columns(rows),` becomes nothing (deleted). Fails `test_the_trials_carry_their_condition_and_the_settings_that_varied`.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/nwb/intervals.py wl_preproc/nwb/gather.py wl_preproc/nwb/build.py tests/nwb/test_writers.py tests/schema/test_nwb_build.py
git commit -m "feat(nwb): each trial's condition and the settings that varied, and a conditions table, in the file

<trailer lines>"
```

---

### Task 4: The description

**Files:**
- Create: `wl_preproc/contracts/nwb_description.py`, `wl_preproc/nwb/describe.py`, `tests/nwb/test_describe.py`, `docs/schemas/nwb_description.json` (exported)
- Modify: `wl_preproc/nwb/gather.py`, `wl_preproc/nwb/build.py`, `wl_preproc/schema/nwb.py`, `wl_preproc/cli/main.py`, `tests/cli/test_schemas_export.py`, `tests/schema/test_guardrails.py`, `tests/schema/test_nwb_build.py`

**Interfaces:**
- **Consumes:** Task 3's `Gathered.conditions`, `condition_notes` and each block's `trials` and `conditions`.
- **Produces:**
  - `describe(data, *, status, n_critical, checksums, built_at) -> dict`: validated JSON.
  - `BuildResult.description` and `BuildResult.built_at`.
  - `nwb.NwbFile.description` (`<blob>`, null when refused).
  - `gather.Gathered.session` gains:
    - `session_datetime` (aware UTC);
    - `montage_id`, `activation_id` and `role`;
    - `supersedes_activation_id`;
    - `rig` and `timing_tier`.
  - The eye dict gains `usable_fraction`.

- [ ] **Step 1: Write the failing tests.** Create `tests/nwb/test_describe.py`:

```python
"""The file's description, on plain data (design spec
`2026-09-29-nwb-publishing-design.md` section 2)."""

from __future__ import annotations

import datetime

import pytest

BUILT_AT = datetime.datetime(2026, 9, 29, 12, 0, tzinfo=datetime.timezone.utc)
SESSION = {
    "identifier": "monk01.2026-09-28_01.montage-0.activation-0",
    "session_id": "2026-09-28_01",
    "session_datetime": datetime.datetime(2026, 9, 28, 9, 0, tzinfo=datetime.timezone.utc),
    "montage_id": 0,
    "activation_id": 0,
    "role": "canonical",
    "supersedes_activation_id": None,
    "rig": "rig-a",
    "timing_tier": "B",
    "clock": {"source": "manifest"},
    "subject": {"subject_id": "monk01", "species": "Macaca mulatta", "sex": "F",
                "date_of_birth": datetime.date(2016, 3, 2)},
}
CONDITION = {"name": "contrast-50", "code": None, "settings": {"contrast": 0.5}, "varying": {},
             "trials": {"total": 1, "by_outcome": {"correct": 1}}}
BLOCK = {"block_id": 1, "start_s": 0.0, "end_s": 30.0, "task_type": "2", "works_block_id": "wb-1",
         "measured_start_s": 0.5, "measured_stop_s": 29.5, "coverage": {"ohdpi": ("full", 30.0)},
         "trials": {"total": 1, "by_outcome": {"correct": 1}}, "conditions": [CONDITION]}
CHECKSUM = {"dataset_path": "/intervals/trials/start_time", "dtype": "float64", "shape": "(1,)",
            "sha256": "0" * 64, "paired_with": ""}


def _gathered(eye=None, notes=()):
    from wl_preproc.nwb.gather import Gathered

    return Gathered(session=SESSION, systems=["ohdpi"], blocks=[BLOCK], trials=[{"trial_id": 1}],
                    events=[{}, {}], timebase={}, eye=eye, conditions=[CONDITION], condition_notes=list(notes))


def test_the_description_of_a_file_without_eye_data():
    from wl_preproc.nwb.describe import describe

    out = describe(_gathered(notes=["no rig trial record (xcon/trials.jsonl)"]), status="written", n_critical=0,
                   checksums=[CHECKSUM], built_at=BUILT_AT)
    assert out["schema_version"] == 1
    assert out["identity"]["identifier"] == SESSION["identifier"]
    assert (out["identity"]["rig"], out["identity"]["role"], out["identity"]["status"]) == ("rig-a", "canonical", "written")
    assert out["identity"]["pipeline"]["name"] == "wl-preproc"
    assert out["subject"] == {"species": "Macaca mulatta", "sex": "F", "date_of_birth": "2016-03-02",
                              "age_days": (datetime.date(2026, 9, 28) - datetime.date(2016, 3, 2)).days}
    assert out["data_types"]["eye"] is None and out["data_types"]["eye_events"] is None
    assert out["data_types"]["behaviour"] == {"trials": 1, "events": 2}
    assert out["data_types"]["ephys"] is None and out["probes"] == [] and out["processing"] == {}
    (block,) = out["blocks"]
    assert block["task"] == {"code": "2", "name": "rf_map"}
    assert block["asserted"] == {"start_s": 0.0, "stop_s": 30.0}
    assert block["coverage"] == {"ohdpi": {"coverage": "full", "covered_s": 30.0}}
    assert block["conditions"] == [CONDITION]
    assert out["quality"] == {"timing_tier": "B", "reference_source": "manifest",
                              "eye_usable_fraction": {"left": None, "right": None}}
    assert out["notes"] == ["no rig trial record (xcon/trials.jsonl)"]
    assert out["checksums"] == {"algorithm": "sha256", "datasets": [CHECKSUM]}


def test_the_eye_is_described_by_the_series_and_detectors_it_has():
    from wl_preproc.nwb.describe import describe

    eye = {"gaze": {"left": [1], "right": None}, "pupil": {"left": [1], "right": [1]},
           "detections": [{"name": "engbert_kliegl_left"}, {"name": "engbert_kliegl_conjunction"}, {"name": "bmd_left"}],
           "usable_fraction": {"left": 0.9, "right": None}}
    out = describe(_gathered(eye=eye), status="invalid", n_critical=1, checksums=[], built_at=BUILT_AT)
    assert out["data_types"]["eye"] == {"gaze": ["left"], "pupil": ["left", "right"]}
    assert out["data_types"]["eye_events"] == {"detectors": ["bmd", "engbert_kliegl"]}
    assert out["quality"]["eye_usable_fraction"] == {"left": 0.9, "right": None}
    assert (out["identity"]["status"], out["identity"]["n_critical"]) == ("invalid", 1)


def test_the_description_is_held_to_its_contract():
    """Strict on this side, so what is published is exactly the exported
    schema; a reader ignores what it does not know."""
    from pydantic import ValidationError

    from wl_preproc.nwb.describe import describe

    with pytest.raises(ValidationError):
        describe(_gathered(), status="refused", n_critical=0, checksums=[], built_at=BUILT_AT)
```

Apply these diffs:

```diff
--- a/tests/cli/test_schemas_export.py
+++ b/tests/cli/test_schemas_export.py
@@ -91,3 +91,12 @@ def test_job_request_schema_carries_the_montage_bounds(tmp_path):
     boundary = schema["$defs"]["MontageBoundary"]
     assert boundary["properties"]["montage_id"]["maximum"] == 127  # tinyint
     assert boundary["additionalProperties"] is False
+
+
+def test_the_nwb_description_schema_is_exported_for_wl_works(tmp_path):
+    """wl.works' dataset builder selects on it (design spec
+    `2026-09-29-nwb-publishing-design.md` section 2)."""
+    export_schemas(tmp_path)
+    schema = json.loads((tmp_path / "nwb_description.json").read_text())
+    assert schema["properties"]["schema_version"]["const"] == 1
+    assert {"Block", "Condition", "Checksums"} <= set(schema["$defs"])
```

```diff
--- a/tests/schema/test_guardrails.py
+++ b/tests/schema/test_guardrails.py
@@ -680,6 +680,9 @@ _EXPECTED_EXERCISED_BLOB_ATTRIBUTES = frozenset(
         # The NWB builder's inspector findings (design spec
         # `2026-09-28-nwb-builder-design.md` section 8), added 2026-09-28.
         "wl_preproc.schema.nwb.NwbFile.inspector_findings",
+        # The file's description (design spec
+        # `2026-09-29-nwb-publishing-design.md` section 2), added 2026-09-29.
+        "wl_preproc.schema.nwb.NwbFile.description",
         "wl_preproc.schema.ephys.Unit.spike_times",
         "wl_preproc.schema.ephys.Unit.spike_sites",
         "wl_preproc.schema.ephys.Unit.spike_depths",
```

```diff
--- a/tests/schema/test_nwb_build.py
+++ b/tests/schema/test_nwb_build.py
@@ -182,6 +182,26 @@ def test_the_trials_carry_the_rigs_conditions_and_settings(activation, built):
         assert all(json.loads(settings)["orientation_deg"] == 45.0 for settings in conditions["settings"])
 
 
+def test_the_description_describes_the_file(activation, built):
+    """Design spec `2026-09-29-nwb-publishing-design.md` section 2, on the
+    synthetic session: what is in the file, by what actually ran."""
+    from wl_preproc.contracts.nwb_description import NwbDescription
+
+    description = built.description
+    NwbDescription.model_validate(description)
+    assert description["identity"]["identifier"] == built.identifier
+    assert (description["identity"]["rig"], description["identity"]["role"]) == ("rig-a", "canonical")
+    assert description["subject"]["age_days"] == (_SESSION_DATETIME.date() - datetime.date(2016, 3, 2)).days
+    assert description["data_types"]["eye"] == {"gaze": ["left", "right"], "pupil": ["left", "right"]}
+    assert len(description["data_types"]["eye_events"]["detectors"]) == 6
+    assert [block["block_id"] for block in description["blocks"]] == [1, 2]
+    assert all(block["task"]["name"] == "rf_map" for block in description["blocks"])
+    names = {condition["name"] for block in description["blocks"] for condition in block["conditions"]}
+    assert names and names <= {"contrast-10", "contrast-25", "contrast-50", "contrast-100"}
+    assert description["checksums"]["datasets"] == built.checksums
+    assert description["notes"] == []
+
+
 def test_the_eye_is_on_session_time_and_every_detector_is_there(activation, built):
     from pynwb import NWBHDF5IO
 
@@ -439,6 +459,7 @@ def test_the_daemon_stage_records_every_activation(activation, daemon_module, pr
     assert set(rows) == {(r["montage_id"], r["activation_id"]) for r in (request.Activation & session_key).to_dicts()}
     canonical = rows[(key["montage_id"], key["activation_id"])]
     assert canonical["status"] == "written" and canonical["reference_source"] == "manifest"
+    assert canonical["description"]["identity"]["identifier"] == canonical["nwb_identifier"]
     assert len(nwb_schema.NwbFile.Dataset & canonical) > 50
     assert rows[(1, 0)]["status"] == "refused"
 
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/nwb tests/cli/test_schemas_export.py tests/schema/test_nwb_build.py tests/schema/test_guardrails.py -q --tb=line -p no:cacheprovider`
Expected: 7 failed, 73 passed, 1 skipped, on `ModuleNotFoundError` for `wl_preproc.nwb.describe` and `wl_preproc.contracts.nwb_description`, on `KeyError: 'description'`, and on the blob guardrail finding `NwbFile.description` missing.

- [ ] **Step 3: Implement.** Create `wl_preproc/contracts/nwb_description.py`:

```python
"""What an NWB file holds, for wl.works' dataset builder to select on without
opening it. Frozen interface: exported to `docs/schemas/nwb_description.json`
and checked in CI (design spec `2026-09-29-nwb-publishing-design.md`
section 2).

**Versioned and open.** `schema_version` is 1. Later pieces add groups and
fields -- the probes and areas, a processing summary with unit counts, the
photodiode, video and stimulation -- and never rename or remove one within a
version. This side validates strictly (`extra="forbid"`), so what it publishes
is exactly this; a READER must ignore fields it does not know, which is what
lets a later version add them.

**Not here:** the file's location, which changes when it moves between the
fast and slow shares (`GET /nwb` reports it), and the experimenter's notes and
experiment links, which wl.works already holds."""

from __future__ import annotations

import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = 1


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Pipeline(_Frozen):
    """The code that built the file. The commit is the provenance: the
    package's own version is a constant that has never moved, and reading it
    needs `importlib`, which `tests/test_cli_guardrails.py` bans outright."""

    name: str
    commit: str | None


class Identity(_Frozen):
    identifier: str
    subject: str
    session_id: str
    session_datetime: datetime.datetime
    rig: str | None
    montage_id: int
    activation_id: int
    role: Literal["canonical", "derivative"]
    # The activation this one supersedes, or null (never `supersedes`: the
    # guardrail that nothing writes `Activation.supersedes` scans for it).
    supersedes_activation_id: int | None
    built_at: datetime.datetime
    pipeline: Pipeline
    status: Literal["written", "invalid"]
    n_critical: int


class SubjectInfo(_Frozen):
    species: str | None
    sex: Literal["M", "F", "U"]
    date_of_birth: datetime.date | None
    age_days: int | None


class EyeData(_Frozen):
    gaze: list[Literal["left", "right"]]
    pupil: list[Literal["left", "right"]]


class EyeEvents(_Frozen):
    detectors: list[str]


class Behaviour(_Frozen):
    trials: int
    events: int


class DataTypes(_Frozen):
    eye: EyeData | None
    eye_events: EyeEvents | None
    behaviour: Behaviour
    # Present and null until their pieces fill them.
    ephys: dict[str, Any] | None = None
    photodiode: dict[str, Any] | None = None
    video: dict[str, Any] | None = None
    stimulation: dict[str, Any] | None = None


class TrialCounts(_Frozen):
    total: int
    by_outcome: dict[str, int]


class Condition(_Frozen):
    """One condition that ran in a block: by its name in the rig's record,
    with the settings constant across its trials and a summary of those that
    varied; or, without that record, by the stream's CONDITION number with
    settings unknown (null)."""

    name: str | None
    code: int | None
    settings: dict[str, Any] | None
    varying: dict[str, dict[str, Any]] | None
    trials: TrialCounts


class Interval(_Frozen):
    start_s: float
    stop_s: float


class Coverage(_Frozen):
    coverage: str
    covered_s: float


class Task(_Frozen):
    code: str
    name: str


class Block(_Frozen):
    block_id: int
    works_block_id: str | None
    task: Task
    asserted: Interval
    measured: Interval | None
    trials: TrialCounts
    coverage: dict[str, Coverage]
    conditions: list[Condition]


class Quality(_Frozen):
    timing_tier: str
    reference_source: Literal["barcode", "manifest"]
    eye_usable_fraction: dict[Literal["left", "right"], float | None]


class DatasetChecksum(_Frozen):
    dataset_path: str
    dtype: str
    shape: str
    sha256: str
    paired_with: str


class Checksums(_Frozen):
    algorithm: Literal["sha256"]
    datasets: list[DatasetChecksum]


class NwbDescription(_Frozen):
    schema_version: Literal[1] = SCHEMA_VERSION
    identity: Identity
    subject: SubjectInfo
    data_types: DataTypes
    # Empty in version 1; piece 3 describes each probe (type, serial,
    # insertion, trajectory, target areas, per-channel areas).
    probes: list[dict[str, Any]] = []
    blocks: list[Block]
    quality: Quality
    # Empty in version 1; piece 3 adds the processing summary (for example
    # the number of single units, and whether any narrow-waveform units).
    processing: dict[str, Any] = {}
    # Why any trial's condition or settings are unknown.
    notes: list[str]
    checksums: Checksums
```

Create `wl_preproc/nwb/describe.py`:

```python
"""The file's description, made from the same gathered data as the file
(design spec `2026-09-29-nwb-publishing-design.md` section 2): validated
against `contracts/nwb_description.py` and returned as plain JSON, which is
what `nwb.NwbFile.description` stores and what is published beside the file."""

from __future__ import annotations

import datetime
import subprocess
from pathlib import Path

from wl_preproc.contracts.nwb_description import NwbDescription


def _commit() -> str | None:
    """The commit the running code was checked out at, or None when it is
    not a git checkout."""
    try:
        out = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    commit = out.stdout.strip()
    return commit if out.returncode == 0 and len(commit) == 40 else None


def _task(value) -> dict:
    """A block's task type, by code and name. Lab-defined codes (100 and
    up) have no name here; their code stands for it."""
    from wl_preproc.contracts.events import TaskTypeCode

    try:
        name = TaskTypeCode(int(value)).name.lower()
    except (TypeError, ValueError):
        name = str(value)
    return {"code": str(value), "name": name}


def _age_days(date_of_birth: datetime.date | None, session_datetime: datetime.datetime) -> int | None:
    return None if date_of_birth is None else (session_datetime.date() - date_of_birth).days


def describe(data, *, status: str, n_critical: int, checksums: list[dict], built_at: datetime.datetime) -> dict:
    """The description of one built file. `data` is `gather.Gathered`."""
    session, subject, eye = data.session, data.session["subject"], data.eye
    description = {
        "identity": {
            "identifier": session["identifier"],
            "subject": subject["subject_id"],
            "session_id": session["session_id"],
            "session_datetime": session["session_datetime"],
            "rig": session["rig"],
            "montage_id": session["montage_id"],
            "activation_id": session["activation_id"],
            "role": session["role"],
            "supersedes_activation_id": session["supersedes_activation_id"],
            "built_at": built_at,
            "pipeline": {"name": "wl-preproc", "commit": _commit()},
            "status": status,
            "n_critical": n_critical,
        },
        "subject": {
            "species": subject["species"],
            "sex": subject["sex"],
            "date_of_birth": subject["date_of_birth"],
            "age_days": _age_days(subject["date_of_birth"], session["session_datetime"]),
        },
        "data_types": {
            "eye": None if eye is None else {
                "gaze": sorted(side for side, gaze in eye["gaze"].items() if gaze is not None),
                "pupil": sorted(eye["pupil"]),
            },
            "eye_events": None if eye is None else {
                "detectors": sorted({table["name"].rsplit("_", 1)[0] for table in eye["detections"]}),
            },
            "behaviour": {"trials": len(data.trials), "events": len(data.events)},
        },
        "blocks": [{
            "block_id": block["block_id"],
            "works_block_id": block["works_block_id"],
            "task": _task(block["task_type"]),
            "asserted": {"start_s": block["start_s"], "stop_s": block["end_s"]},
            "measured": None if block["measured_start_s"] is None else {
                "start_s": block["measured_start_s"], "stop_s": block["measured_stop_s"]},
            "trials": block["trials"],
            "coverage": {system: {"coverage": verdict, "covered_s": covered}
                         for system, (verdict, covered) in block["coverage"].items()},
            "conditions": block["conditions"],
        } for block in data.blocks],
        "quality": {
            "timing_tier": session["timing_tier"],
            "reference_source": session["clock"]["source"],
            "eye_usable_fraction": (eye or {}).get("usable_fraction") or {"left": None, "right": None},
        },
        "notes": list(data.condition_notes),
        "checksums": {"algorithm": "sha256", "datasets": checksums},
    }
    return NwbDescription.model_validate(description).model_dump(mode="json")
```

Apply these diffs:

```diff
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -276,6 +276,29 @@ def _timebase(session_key: dict, provenance: dict, clock: dict) -> dict:
     }
 
 
+def _usable_fraction(times: np.ndarray, stretches: list[tuple]) -> float | None:
+    """The share of the file's samples of one eye that no withheld stretch
+    covers (design spec `2026-09-29-nwb-publishing-design.md` section 2)."""
+    if not len(times):
+        return None
+    withheld = np.zeros(len(times), dtype=bool)
+    for start, stop, *_ in stretches:
+        low, high = np.searchsorted(times, [start, stop], side="left")
+        withheld[low:high] = True
+    return float(1.0 - withheld.mean())
+
+
+def _rig(session_dir: Path) -> str | None:
+    """The rig the session's manifest names, or None if it cannot be read."""
+    from wl_preproc.contracts.manifest import SessionManifest
+    from wl_preproc.contracts.paths import MANIFEST_FILENAME
+
+    try:
+        return SessionManifest.from_yaml((session_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")).rig
+    except (OSError, ValueError):
+        return None
+
+
 def _edges(times: np.ndarray, segment: dict) -> np.ndarray:
     """Session time of every row, plus the time one sample past the last:
     `edges[stop]` is an exclusive run's stop time."""
@@ -373,7 +396,9 @@ def _eye(session_key: dict, session_dir: Path, blocks: BlockSet, validity_idx: i
 
     return {"times": times[keep], "gaze": gaze, "pupil": pupil, "calibration": calibration,
             "validity": validity, "repairs": repairs, "detections": detections, "sources": sources,
-            "agreement": agreement, "missing_eyes": [eye for eye in EYES if eye not in calibrated]}
+            "agreement": agreement, "missing_eyes": [eye for eye in EYES if eye not in calibrated],
+            "usable_fraction": {eye: _usable_fraction(times[keep], validity[eye]) if eye in validity else None
+                                for eye in EYES}}
 
 
 def gather(activation_key: dict) -> Gathered:
@@ -420,6 +445,13 @@ def gather(activation_key: dict) -> Gathered:
         session={
             "identifier": identifier_for(key, session_id),
             "session_id": session_id,
+            "session_datetime": _aware_utc(key["session_datetime"]),
+            "montage_id": key["montage_id"],
+            "activation_id": key["activation_id"],
+            "role": activation["role"],
+            "supersedes_activation_id": activation["supersedes"],
+            "rig": _rig(session_dir),
+            "timing_tier": provenance[0]["tier"],
             "description": description,
             "reference_time": clock["reference_time"],
             "experimenter": requested_by or None,
```

```diff
--- a/wl_preproc/nwb/build.py
+++ b/wl_preproc/nwb/build.py
@@ -8,6 +8,7 @@ import datetime
 from pathlib import Path
 
 from wl_preproc.nwb.checksums import dataset_checksums
+from wl_preproc.nwb.describe import describe
 from wl_preproc.nwb.eye import add_eye_series, add_eye_tables
 from wl_preproc.nwb.eye_events import add_agreement, add_detections, add_sources
 from wl_preproc.nwb.gather import Refused, gather, readiness
@@ -28,6 +29,11 @@ class BuildResult:
     clock: dict | None = None
     findings: list = dataclasses.field(default_factory=list)
     checksums: list = dataclasses.field(default_factory=list)
+    # The file's description (design spec
+    # `2026-09-29-nwb-publishing-design.md` section 2); None when refused.
+    description: dict | None = None
+    built_at: datetime.datetime = dataclasses.field(
+        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))
 
 
 def nwb_path(nwb_root: Path, subject: str, session_id: str, identifier: str) -> Path:
@@ -78,14 +84,20 @@ def build(activation_key: dict, nwb_root: Path) -> BuildResult:
         return BuildResult(status="refused", reason=f"{path} is already recorded for {other}")
     write_atomically(nwb, path)
     findings = inspect_file(path)
+    status = "invalid" if n_critical(findings) else "written"
+    checksums = dataset_checksums(path)
+    built_at = datetime.datetime.now(datetime.timezone.utc)
     return BuildResult(
-        status="invalid" if n_critical(findings) else "written",
+        status=status,
         path=path,
         n_bytes=path.stat().st_size,
         identifier=data.session["identifier"],
         clock=data.session["clock"],
         findings=findings,
-        checksums=dataset_checksums(path),
+        checksums=checksums,
+        description=describe(data, status=status, n_critical=n_critical(findings), checksums=checksums,
+                             built_at=built_at),
+        built_at=built_at,
     )
 
 
@@ -102,13 +114,14 @@ def record(activation_key: dict, result: BuildResult) -> None:
         "status": result.status,
         "path": str(result.path or ""),
         "n_bytes": result.n_bytes,
-        "built_at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
+        "built_at": result.built_at.astimezone(datetime.timezone.utc).replace(tzinfo=None),
         "nwb_identifier": result.identifier,
         "reference_time": clock["reference_time"].astimezone(datetime.timezone.utc).replace(tzinfo=None) if clock else None,
         "reference_source": clock.get("source"),
         "started_at_difference_s": clock.get("started_at_difference_s"),
         "n_critical": None if result.status == "refused" else n_critical(result.findings),
         "inspector_findings": result.findings or None,
+        "description": result.description,
         "reason": result.reason,
     }
     connection = dj.conn()
```

```diff
--- a/wl_preproc/schema/nwb.py
+++ b/wl_preproc/schema/nwb.py
@@ -39,6 +39,9 @@ class NwbFile(dj.Manual):
     started_at_difference_s = null : double
     n_critical = null : int unsigned
     inspector_findings = null : <blob>
+    # What the file holds, for wl.works' dataset builder (design spec
+    # `2026-09-29-nwb-publishing-design.md` section 2); null when refused.
+    description = null : <blob>
     reason = '' : varchar(1024)
     """
 
```

```diff
--- a/wl_preproc/cli/main.py
+++ b/wl_preproc/cli/main.py
@@ -23,6 +23,7 @@ from wl_sync.log import SyncBoxLogHeader
 
 from wl_preproc.contracts.done import DoneMarker
 from wl_preproc.contracts.manifest import SessionManifest
+from wl_preproc.contracts.nwb_description import NwbDescription
 from wl_preproc.contracts.protocol import HealthResponse, JobRequest
 from wl_preproc.contracts.sidecar import BehaviorCameraSidecar
 
@@ -37,6 +38,7 @@ EXPORTED_MODELS: dict[str, type[BaseModel]] = {
     "syncbox_log_header": SyncBoxLogHeader,
     "health_response": HealthResponse,
     "job_request": JobRequest,
+    "nwb_description": NwbDescription,
 }
 
 
```

Then export the schema: `.venv/bin/python -m wl_preproc.cli.main schemas export --out docs/schemas`. Expected: `git status --short docs/schemas` lists only the new `docs/schemas/nwb_description.json`.

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 80 passed, 1 skipped.

- [ ] **Step 5: Mutation checks.**
  - T4a (`describe.py`): `"age_days": _age_days(subject["date_of_birth"], session["session_datetime"]),` becomes `"age_days": None,`. Fails `test_the_description_of_a_file_without_eye_data`.
  - T4b (`describe.py`): `"detectors": sorted({table["name"].rsplit("_", 1)[0] for table in eye["detections"]}),` becomes `"detectors": sorted({table["name"] for table in eye["detections"]}),`. Fails `test_the_eye_is_described_by_the_series_and_detectors_it_has`.
  - T4c (`build.py`): `description=describe(data,` becomes `description=None and describe(data,`. Fails `test_the_description_describes_the_file`.
  - T4d (`build.py`): `"description": result.description,` becomes nothing (deleted). Fails `test_the_daemon_stage_records_every_activation`.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/contracts/nwb_description.py wl_preproc/nwb/describe.py wl_preproc/nwb/gather.py wl_preproc/nwb/build.py wl_preproc/schema/nwb.py wl_preproc/cli/main.py docs/schemas/nwb_description.json tests/nwb/test_describe.py tests/cli/test_schemas_export.py tests/schema/test_guardrails.py tests/schema/test_nwb_build.py
git commit -m "feat(nwb): a description of every file, for wl.works' dataset builder to select on without opening it

<trailer lines>"
```

---

### Task 5: Publishing

**Files:**
- Create: `wl_preproc/nwb/publish.py`, `tests/nwb/test_publish.py`, `tests/cli/test_daemon_nwb_options.py`
- Modify: `wl_preproc/schema/nwb.py`, `wl_preproc/nwb/build.py`, `wl_preproc/daemon.py`, `wl_preproc/cli/main.py`, `tests/schema/test_nwb_build.py`, `tests/schema/test_daemon.py`, `tests/schema/test_guardrails.py`

**Interfaces:**
- **Consumes:** Task 4's `NwbFile.description`.
- **Produces:**
  - Tables `NwbChange`, `NwbPlacement` and `ActiveSet` (spec §9).
  - `publish.Share(tier, mount, host, name, headroom_bytes=0)`, with `.relative`, `.local` and `.has_room`.
  - `copy_verified`, `mismatches`, `write_description`, `description_path`.
  - `current_placement(key)`, `record_change(key, kind, placement=None) -> int`.
  - `active_keys()`, `activation_tuple(key)`, `place(...)`, `publish(...)`.
  - `run_publish(slow, fast=None, freed=None) -> (published, errors)`.
  - `VerificationError` and `PublishConflict`.
  - `daemon.run_once(..., nwb_slow=None, nwb_fast=None)`, whose report gains `nwb_published`.
  - `wlpp daemon --nwb-slow-root --nwb-slow-share --nwb-fast-root --nwb-fast-share --nwb-fast-headroom-gb`, with the NAS named by `--host`.
  - `record()` also logs each build as a `built` change.

**The guardrail change in this task.** `test_every_blob_attribute_round_trips_an_array` writes a synthetic row into every table with a blob, and leaves it. For `ActiveSet` that row becomes the latest active set, and every later test's stages read it as live state. Measured: the first full suite after this task failed eight tests that way. It now removes the rows it wrote into `ActiveSet` and `NwbFile`. The synthetic activation stays, and piece 1's readiness gate keeps it out of the build stage.

- [ ] **Step 1: Write the failing tests.** Create `tests/nwb/test_publish.py`:

```python
"""Publishing's file handling, without a database (design spec
`2026-09-29-nwb-publishing-design.md` sections 3 and 4)."""

from __future__ import annotations

import datetime
import json

import pytest

from tests.nwb.test_helpers import _file


def test_a_verified_copy_carries_its_final_name_only_after_verification(tmp_path):
    from wl_preproc.nwb.checksums import dataset_checksums
    from wl_preproc.nwb.publish import copy_verified

    source = _file(tmp_path, "built.nwb", [1.0, 2.0])
    target = tmp_path / "share" / "nwb" / "s" / "d" / "f.nwb"
    copy_verified(source, target, dataset_checksums(source))
    assert target.read_bytes() == source.read_bytes()
    assert sorted(p.name for p in target.parent.iterdir()) == ["f.nwb"]


def test_a_copy_that_fails_verification_leaves_nothing(tmp_path):
    from wl_preproc.nwb.checksums import dataset_checksums
    from wl_preproc.nwb.publish import VerificationError, copy_verified

    source = _file(tmp_path, "built.nwb", [1.0, 2.0])
    wrong = [{**row, "sha256": "0" * 64} if row["dataset_path"].endswith("start_time") else row
             for row in dataset_checksums(source)]
    target = tmp_path / "share" / "f.nwb"
    with pytest.raises(VerificationError, match="start_time"):
        copy_verified(source, target, wrong)
    assert list(target.parent.iterdir()) == []


def test_a_copy_the_share_cannot_hold_leaves_nothing(tmp_path, monkeypatch):
    """Review Focus 2 (the plan): a share that fills, or fails, mid-copy.
    Nothing carries the final name, nothing half-written is left, and the
    error goes up to be reported and retried."""
    import errno
    import shutil
    from pathlib import Path

    from wl_preproc.nwb.checksums import dataset_checksums
    from wl_preproc.nwb.publish import copy_verified

    source = _file(tmp_path, "built.nwb", [1.0, 2.0])
    target = tmp_path / "share" / "f.nwb"

    def full(src, dst):
        Path(dst).write_bytes(b"half")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(shutil, "copyfile", full)
    with pytest.raises(OSError, match="No space"):
        copy_verified(source, target, dataset_checksums(source))
    assert list(target.parent.iterdir()) == []

def test_the_description_is_written_beside_the_file(tmp_path):
    from wl_preproc.nwb.publish import description_path, write_description

    nwb = tmp_path / "x.nwb"
    write_description(nwb, {"schema_version": 1})
    assert json.loads(description_path(nwb).read_text()) == {"schema_version": 1}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.json"]


def test_a_share_names_the_path_relative_to_itself_and_keeps_its_headroom(tmp_path, monkeypatch):
    import collections
    import shutil

    from wl_preproc.nwb.publish import Share

    usage = collections.namedtuple("usage", "total used free")

    share = Share(tier="fast", mount=tmp_path, host="wl-nas", name="nvme", headroom_bytes=100)
    relative = share.relative("monk01", "2027-01-12_01", "monk01.2027-01-12_01.montage-0.activation-0")
    assert relative == "nwb/monk01/2027-01-12_01/monk01.2027-01-12_01.montage-0.activation-0.nwb"
    assert share.local(relative) == tmp_path / relative
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage(1000, 850, 150))
    assert share.has_room(50) and not share.has_room(51)


def test_an_activation_key_compares_whatever_carried_it():
    from wl_preproc.nwb.publish import activation_tuple

    naive = {"subject": "s", "session_datetime": datetime.datetime(2027, 1, 12, 9), "montage_id": 0, "activation_id": 1}
    aware = {**naive, "session_datetime": datetime.datetime(2027, 1, 12, 10, tzinfo=datetime.timezone(datetime.timedelta(hours=1)))}
    text = {**naive, "session_datetime": "2027-01-12T09:00:00"}
    assert activation_tuple(naive) == activation_tuple(aware) == activation_tuple(text) == ("s", "2027-01-12T09:00:00", 0, 1)
```

Create `tests/cli/test_daemon_nwb_options.py`:

```python
"""The daemon's NWB share options (design spec
`2026-09-29-nwb-publishing-design.md` section 3), refused at parse time when
given by halves, before anything touches the database."""

from __future__ import annotations

import pytest


def test_a_share_given_by_halves_is_refused(tmp_path, capsys):
    from wl_preproc.cli.main import main

    with pytest.raises(SystemExit) as raised:
        main(["daemon", "--nwb-slow-root", str(tmp_path)])
    assert raised.value.code == 2
    assert "--nwb-slow-share" in capsys.readouterr().err


def test_the_fast_share_needs_the_slow_one(tmp_path, capsys):
    from wl_preproc.cli.main import main

    with pytest.raises(SystemExit) as raised:
        main(["daemon", "--host", "wl-nas", "--nwb-fast-root", str(tmp_path), "--nwb-fast-share", "nvme"])
    assert raised.value.code == 2
    assert "long-term home" in capsys.readouterr().err
```

Apply these diffs:

```diff
--- a/tests/schema/test_nwb_build.py
+++ b/tests/schema/test_nwb_build.py
@@ -464,6 +464,124 @@ def test_the_daemon_stage_records_every_activation(activation, daemon_module, pr
     assert rows[(1, 0)]["status"] == "refused"
 
 
+@pytest.fixture(scope="module")
+def slow_share(tmp_path_factory):
+    """One slow share for the whole module, as a real deployment has: a file
+    published in one test is where the next one looks."""
+    from wl_preproc.nwb.publish import Share
+
+    return Share(tier="slow", mount=tmp_path_factory.mktemp("nwb-slow"), host="wl-nas", name="hdd")
+
+
+def _placed_path(share, key):
+    from wl_preproc.schema import nwb as nwb_schema
+
+    identifier = (nwb_schema.NwbFile & key).fetch1("nwb_identifier")
+    subject, session_id = identifier.split(".")[:2]
+    return share.local(share.relative(subject, session_id, identifier))
+
+
+def _unrecord(key, *shares):
+    """Delete an activation's row and any file it published: what an
+    operator does before rebuilding it."""
+    from wl_preproc.schema import nwb as nwb_schema
+
+    if nwb_schema.NwbFile & key:
+        for share in shares:
+            path = _placed_path(share, key)
+            for leftover in path.parent.glob(path.stem + ".*"):
+                leftover.unlink()
+        (nwb_schema.NwbFile & key).delete(prompt=False)
+
+
+def test_the_daemon_publishes_every_written_file_to_the_slow_share(activation, daemon_module, prefix, slow_share,
+                                                                    tmp_path_factory):
+    """Design spec `2026-09-29-nwb-publishing-design.md` section 4: verified,
+    described, recorded as a change and a placement, and the scratch copy
+    deleted. Only `written` files are published."""
+    from wl_preproc.nwb.publish import current_placement, description_path, mismatches
+    from wl_preproc.schema import nwb as nwb_schema
+
+    session_key, key, _blocks = activation
+    slow = slow_share
+    report = daemon_module.run_once(prefix=prefix, nwb_root=tmp_path_factory.mktemp("nwb-publish-build"),
+                                    nwb_slow=slow)
+    assert not [e for e in report["errors"] if "NwbPlacement" in e], report["errors"]
+    assert report["nwb_published"] >= 1
+    row = (nwb_schema.NwbFile & key).fetch1()
+    placement = current_placement(key)
+    assert (placement["tier"], placement["host"], placement["share"]) == ("slow", "wl-nas", "hdd")
+    assert placement["path"] == f"nwb/nwbstep1/2025-07-20_01/{row['nwb_identifier']}.nwb"
+    published = slow.local(placement["path"])
+    assert mismatches(published, (nwb_schema.NwbFile.Dataset & key).to_dicts()) == []
+    assert json.loads(description_path(published).read_text())["identity"]["identifier"] == row["nwb_identifier"]
+    assert not Path(row["path"]).exists()
+    assert sorted(change["kind"] for change in (nwb_schema.NwbChange & key).to_dicts()) == ["built", "published"]
+    for unpublished in (nwb_schema.NwbFile & session_key & "status != 'written'").keys():
+        assert current_placement(unpublished) is None
+
+
+def test_publishing_skips_a_freed_session(activation, prefix, slow_share, tmp_path_factory):
+    from wl_preproc.nwb import build as build_module
+    from wl_preproc.nwb import publish as publish_module
+    from wl_preproc.schema import nwb as nwb_schema
+
+    session_key, _key, blocks = activation
+    key = _derivative(session_key, blocks, prefix, blocks[-1])
+    _unrecord(key, slow_share)
+    build_module.record(key, build_module.build(key, tmp_path_factory.mktemp("nwb-freed-publish-build")))
+    publish_module.run_publish(slow_share, freed=[session_key])
+    assert publish_module.current_placement(key) is None
+
+
+def test_a_publish_that_fails_verification_records_nothing_and_retries(activation, prefix, monkeypatch, slow_share,
+                                                                       tmp_path_factory):
+    """Section 4: a copy that does not verify is deleted and reported, no
+    placement is recorded, and the next pass publishes it."""
+    from wl_preproc.nwb import build as build_module
+    from wl_preproc.nwb import publish as publish_module
+    from wl_preproc.schema import nwb as nwb_schema
+
+    session_key, _key, blocks = activation
+    key = _derivative(session_key, blocks, prefix, blocks[-1])
+    _unrecord(key, slow_share)
+    build_module.record(key, build_module.build(key, tmp_path_factory.mktemp("nwb-verify-build")))
+    slow = slow_share
+    real = publish_module.mismatches
+    monkeypatch.setattr(publish_module, "mismatches", lambda path, checksums: ["/forced"])
+    _published, errors = publish_module.run_publish(slow)
+    assert [error for error in errors if "differ after copying" in error]
+    assert publish_module.current_placement(key) is None
+    target = _placed_path(slow, key)
+    assert not list(target.parent.glob(target.name + "*"))
+    monkeypatch.setattr(publish_module, "mismatches", real)
+    publish_module.run_publish(slow)
+    assert publish_module.current_placement(key)["tier"] == "slow"
+
+
+def test_publishing_never_overwrites_a_file_no_placement_records(activation, prefix, slow_share, tmp_path_factory):
+    """Parent spec section 8.3: regeneration never overwrites. A row deleted
+    without its published file, then rebuilt, must not replace that file:
+    the lab's annotations exist only inside it."""
+    from wl_preproc.nwb import build as build_module
+    from wl_preproc.nwb import publish as publish_module
+    from wl_preproc.schema import nwb as nwb_schema
+
+    session_key, _key, blocks = activation
+    key = _derivative(session_key, blocks, prefix, blocks[0])
+    _unrecord(key, slow_share)
+    build_module.record(key, build_module.build(key, tmp_path_factory.mktemp("nwb-overwrite-first")))
+    publish_module.run_publish(slow_share)
+    published = _placed_path(slow_share, key)
+    before = published.read_bytes()
+    (nwb_schema.NwbFile & key).delete(prompt=False)
+    build_module.record(key, build_module.build(key, tmp_path_factory.mktemp("nwb-overwrite-second")))
+    _published, errors = publish_module.run_publish(slow_share)
+    assert [error for error in errors if "not overwritten" in error]
+    assert published.read_bytes() == before
+    assert publish_module.current_placement(key) is None
+
+
 def test_one_failing_activation_does_not_stop_the_stage(activation, prefix, monkeypatch, tmp_path_factory):
     """The stage catches a failure per activation, as the archive stage
     does: the others are recorded, the failure is reported, and the failed
@@ -527,6 +645,13 @@ def test_a_path_recorded_for_another_activation_is_refused(activation, prefix, m
     if not nwb_schema.NwbFile & canonical:
         build_module.record(canonical, build_module.build(canonical, tmp_path_factory.mktemp("nwb-canonical")))
     recorded = Path((nwb_schema.NwbFile & canonical).fetch1("path"))
+    # Publishing deletes the scratch copy once it is on a share (design spec
+    # `2026-09-29-nwb-publishing-design.md` section 4); the row still names
+    # the path, which is what the refusal reads, so a stand-in file keeps the
+    # "left untouched" half of this test meaningful.
+    if not recorded.exists():
+        recorded.parent.mkdir(parents=True, exist_ok=True)
+        recorded.write_bytes(b"another activation's file")
     before = recorded.read_bytes()
     key = _derivative(session_key, blocks, prefix, blocks[-1])
     monkeypatch.setattr(build_module, "nwb_path", lambda *args: recorded)
```

```diff
--- a/tests/schema/test_daemon.py
+++ b/tests/schema/test_daemon.py
@@ -272,7 +272,7 @@ def test_run_once_reports_what_it_did(daemon_env, prefix, tmp_path):
 
     # `nwb` joined 2026-09-28 (design spec `2026-09-28-nwb-builder-design.md`).
     assert set(baseline) == {
-        "populated", "errors", "stale_jobs_reaped", "archived", "nwb", "freed_skipped"
+        "populated", "errors", "stale_jobs_reaped", "archived", "nwb", "nwb_published", "freed_skipped"
     }
     assert isinstance(baseline["freed_skipped"], int)
     assert baseline["populated"] == first["populated"], (
```

```diff
--- a/tests/schema/test_guardrails.py
+++ b/tests/schema/test_guardrails.py
@@ -683,6 +683,7 @@ _EXPECTED_EXERCISED_BLOB_ATTRIBUTES = frozenset(
         # The file's description (design spec
         # `2026-09-29-nwb-publishing-design.md` section 2), added 2026-09-29.
         "wl_preproc.schema.nwb.NwbFile.description",
+        "wl_preproc.schema.nwb.ActiveSet.activations",
         "wl_preproc.schema.ephys.Unit.spike_times",
         "wl_preproc.schema.ephys.Unit.spike_sites",
         "wl_preproc.schema.ephys.Unit.spike_depths",
@@ -758,18 +759,32 @@ def test_every_blob_attribute_round_trips_an_array(all_tables, dj_conn):
     # docstring makes.
     arr = _PROBE_ARRAY
     exercised = []
+    inserted = []
     for module_name, table_name, table, attr in blob_attrs:
         qualified = f"{module_name}.{table_name}.{attr}"
         _build_parents(table)
         row = {**_synthetic_row(table, exclude=attr), attr: arr}
         key = {k: row[k] for k in table.primary_key}
         table.insert1(row, skip_duplicates=True)
+        inserted.append((table, key))
         got = (table & key).fetch1(attr)
         assert isinstance(got, np.ndarray), f"{table_name}.{attr} returned {type(got).__name__}"
         assert got.shape == arr.shape and got.dtype == arr.dtype
         assert np.array_equal(got, arr)
         exercised.append(qualified)
 
+    # The NWB stages read `nwb.ActiveSet`'s latest row as the desired active
+    # set and every `nwb.NwbFile` row as a built file, so a synthetic row left
+    # here acts as live state for every later test in the session (found
+    # 2026-09-29, design spec `2026-09-29-nwb-publishing-design.md`). Removed
+    # once round-tripped; the synthetic activation it hangs from stays, and
+    # the NWB readiness gate keeps it out of the build stage.
+    from wl_preproc.schema import nwb as nwb_schema
+
+    for table, key in inserted:
+        if table is nwb_schema.ActiveSet or table is nwb_schema.NwbFile:
+            (table & key).delete(prompt=False)
+
     exercised_set = set(exercised)
     assert exercised_set == _EXPECTED_EXERCISED_BLOB_ATTRIBUTES, (
         "the set of blob attributes actually round-tripped has changed -- "
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/nwb tests/cli/test_daemon_nwb_options.py tests/schema/test_nwb_build.py tests/schema/test_daemon.py tests/schema/test_guardrails.py -q --tb=line -p no:cacheprovider`
Expected: 10 failed, 83 passed, 1 skipped, 4 errors, on `ModuleNotFoundError: No module named 'wl_preproc.nwb.publish'`, on the daemon's unknown share options (`DID NOT RAISE SystemExit`), on `nwb.ActiveSet` missing, and on the report's key set.

- [ ] **Step 3: Implement.** Create `wl_preproc/nwb/publish.py`:

```python
"""Publishing built files to the NAS, and where each one is (design spec
`2026-09-29-nwb-publishing-design.md` sections 3, 4 and 9).

**Two shares, one live copy.** A file is on the slow long-term share or the
fast active one, never both: the lab appends annotations into its NWBs, and two
copies would diverge at the first one. On either share it lives at
`nwb/<subject>/<session_id>/<identifier>.nwb`, its description beside it as
`<identifier>.json`, and the recorded path is relative to the share, the
triple wl.works' Plan 23 section 10.1 names.

**Nothing carries its final name until it is verified.** A copy is written to
`.partial`, every dataset the build checksummed is hashed again and compared,
and only then renamed; the description is written last, the "complete"
signal wl.works' Plan 20 asks for."""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import shutil
from pathlib import Path

from wl_preproc.nwb.checksums import dataset_checksums

# The top-level folder NWB files get on either share, apart from the raw
# archive's `<subject>/<session>` folders.
NWB_DIR = "nwb"


class VerificationError(Exception):
    """A copy whose written-once datasets do not match the build's checksums."""


class PublishConflict(Exception):
    """A file already at the target that no placement records. Never
    overwritten: the lab's annotations live only inside published files
    (parent spec section 8.3, "regeneration supersedes; it never
    overwrites")."""


@dataclasses.dataclass(frozen=True)
class Share:
    """One NAS share, as the daemon is given it: its tier, where it is
    mounted here, and the host and share names wl.works' triple uses."""

    tier: str  # "slow" or "fast"
    mount: Path
    host: str
    name: str
    headroom_bytes: int = 0

    def relative(self, subject: str, session_id: str, identifier: str) -> str:
        return f"{NWB_DIR}/{subject}/{session_id}/{identifier}.nwb"

    def local(self, relative: str) -> Path:
        return Path(self.mount) / relative

    def has_room(self, n_bytes: int) -> bool:
        """Whether `n_bytes` more still leaves the configured headroom free."""
        return shutil.disk_usage(self.mount).free - n_bytes >= self.headroom_bytes


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def key_of(row: dict) -> dict:
    return {k: row[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")}


def mismatches(path: Path, checksums: list[dict]) -> list[str]:
    """The recorded datasets whose contents in `path` no longer hash the same.
    Datasets appended since (annotations) are not written-once and not
    compared."""
    actual = {row["dataset_path"]: row["sha256"] for row in dataset_checksums(path)}
    return [row["dataset_path"] for row in checksums if actual.get(row["dataset_path"]) != row["sha256"]]


def copy_verified(source: Path, target: Path, checksums: list[dict]) -> None:
    """`source` to `target` by way of `target.partial`, verified before the
    rename. Raises `VerificationError`, leaving nothing behind, on a
    mismatch."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    try:
        shutil.copyfile(source, partial)
        with open(partial, "rb+") as handle:
            os.fsync(handle.fileno())
        bad = mismatches(partial, checksums)
        if bad:
            raise VerificationError(f"{target}: {len(bad)} dataset(s) differ after copying, first {bad[0]}")
        os.replace(partial, target)
    finally:
        if partial.exists():
            partial.unlink()


def description_path(nwb_path: Path) -> Path:
    return nwb_path.with_suffix(".json")


def write_description(nwb_path: Path, description: dict) -> None:
    """The description beside the file, written last and atomically."""
    target = description_path(nwb_path)
    partial = target.with_name(target.name + ".partial")
    partial.write_text(json.dumps(description, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(partial, target)


def current_placement(key: dict) -> dict | None:
    """Where an activation's file is now: its latest `NwbPlacement`, or None
    if it was never published."""
    from wl_preproc.schema import nwb as nwb_schema

    rows = (nwb_schema.NwbPlacement * nwb_schema.NwbChange & key_of(key)).to_dicts()
    return max(rows, key=lambda row: row["change_seq"]) if rows else None


def record_change(key: dict, kind: str, placement: dict | None = None) -> int:
    """One `NwbChange`, and its `NwbPlacement` when it moved a file, in one
    transaction. Returns the change's sequence number."""
    import datajoint as dj

    from wl_preproc.schema import nwb as nwb_schema

    connection = dj.conn()
    with connection.transaction:
        nwb_schema.NwbChange.insert1({**key_of(key), "kind": kind, "changed_at": _now()})
        sequence = int(connection.query("SELECT LAST_INSERT_ID()").fetchone()[0])
        if placement is not None:
            nwb_schema.NwbPlacement.insert1({"change_seq": sequence, **placement})
    return sequence


def active_keys() -> set[tuple]:
    """The activations the latest `PUT /nwb/active` wants on the fast share."""
    from wl_preproc.schema import nwb as nwb_schema

    rows = nwb_schema.ActiveSet.to_dicts()
    if not rows:
        return set()
    latest = max(rows, key=lambda row: row["set_seq"])
    return {activation_tuple(item) for item in latest["activations"]}


def activation_tuple(key: dict) -> tuple:
    """An activation key in one comparable form, whatever carried it:
    `session_datetime` as naive-UTC ISO text."""
    moment = key["session_datetime"]
    if isinstance(moment, str):
        moment = datetime.datetime.fromisoformat(moment)
    if moment.tzinfo is not None:
        moment = moment.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return (str(key["subject"]), moment.isoformat(), int(key["montage_id"]), int(key["activation_id"]))


def place(key: dict, source: Path, share: Share, checksums: list[dict], description: dict) -> dict:
    """Copy one file, verified, and its description onto `share`. Returns
    the placement (not yet recorded)."""
    identity = description["identity"]
    relative = share.relative(identity["subject"], identity["session_id"], identity["identifier"])
    target = share.local(relative)
    if target.exists() or description_path(target).exists():
        raise PublishConflict(f"{target} already exists and no placement of this activation records it; "
                              "not overwritten")
    copy_verified(source, target, checksums)
    write_description(target, description)
    return {"tier": share.tier, "host": share.host, "share": share.name, "path": relative,
            "n_bytes": target.stat().st_size}


def publish(key: dict, slow: Share, fast: Share | None, wanted_fast: set[tuple]) -> dict:
    """Publish one `written` file from scratch: to the fast share if it is in
    the active set and the fast share has room, otherwise to the slow one.
    The scratch copy is deleted once the placement is recorded."""
    from wl_preproc.schema import nwb as nwb_schema

    row = (nwb_schema.NwbFile & key_of(key)).fetch1()
    checksums = (nwb_schema.NwbFile.Dataset & key_of(key)).to_dicts()
    source = Path(row["path"])
    share = slow
    if fast is not None and activation_tuple(key) in wanted_fast and fast.has_room(source.stat().st_size):
        share = fast
    placement = place(key, source, share, checksums, row["description"])
    record_change(key, "published", placement)
    source.unlink(missing_ok=True)
    return placement


def run_publish(slow: Share, fast: Share | None = None, freed: list[dict] | None = None) -> tuple[int, list[str]]:
    """The daemon's publishing stage: every `written` file not yet
    published, skipping freed sessions. Returns `(published, failures)`."""
    from wl_preproc.schema import nwb as nwb_schema

    freed = freed or []
    wanted_fast = active_keys()
    published, errors = 0, []
    published_keys = (nwb_schema.NwbChange & {"kind": "published"}).proj(
        "subject", "session_datetime", "montage_id", "activation_id")
    pending = (nwb_schema.NwbFile & {"status": "written"}) - published_keys
    for key in pending.keys():
        if {"subject": key["subject"], "session_datetime": key["session_datetime"]} in freed:
            continue
        try:
            publish(key, slow, fast, wanted_fast)
            published += 1
        except Exception as exc:  # one file must not stop the others; retried next pass
            errors.append(f"NwbPlacement {key}: {exc}")
    return published, errors
```

Apply these diffs:

```diff
--- a/wl_preproc/schema/nwb.py
+++ b/wl_preproc/schema/nwb.py
@@ -60,6 +60,55 @@ class NwbFile(dj.Manual):
         """
 
 
+
+@schema
+class NwbChange(dj.Manual):
+    definition = """
+    # Every change to an activation's file that wl.works polls for: built
+    # (an NwbFile row), published, or moved between shares. The sequence only
+    # increases, and is GET /nwb's cursor (design spec
+    # `2026-09-29-nwb-publishing-design.md` sections 6 and 9).
+    # Key: (change_seq).
+    change_seq : int unsigned auto_increment
+    ---
+    -> NwbFile
+    kind : enum('built','published','moved')
+    changed_at : datetime(6)
+    """
+
+
+@schema
+class NwbPlacement(dj.Manual):
+    definition = """
+    # Where an activation's file is, one row per publish or move: append-only,
+    # and the latest row for an activation is where the file is now (design
+    # spec `2026-09-29-nwb-publishing-design.md` section 9). `path` is
+    # relative to the share, the triple wl.works' Plan 23 section 10.1 names.
+    # Key: (change_seq).
+    -> NwbChange
+    ---
+    tier : enum('slow','fast')
+    host : varchar(64)
+    share : varchar(64)
+    path : varchar(512)
+    n_bytes : bigint unsigned
+    """
+
+
+@schema
+class ActiveSet(dj.Manual):
+    definition = """
+    # Each PUT /nwb/active, as received: the whole set of activations
+    # wl.works wants on the fast share. Append-only; the latest row is the
+    # desired state (design spec `2026-09-29-nwb-publishing-design.md`
+    # section 5). Key: (set_seq).
+    set_seq : int unsigned auto_increment
+    ---
+    received_at : datetime(6)
+    requested_by = null : varchar(64)
+    activations : <blob>   # [{subject, session_datetime, montage_id, activation_id}, ...]
+    """
+
 def activate(prefix: str = DEFAULT_PREFIX) -> None:
     """Bind these tables to `{prefix}nwb`. Idempotent."""
     request.activate(prefix=prefix)
```

```diff
--- a/wl_preproc/nwb/build.py
+++ b/wl_preproc/nwb/build.py
@@ -128,6 +128,9 @@ def record(activation_key: dict, result: BuildResult) -> None:
     with connection.transaction:
         nwb_schema.NwbFile.insert1(row)
         nwb_schema.NwbFile.Dataset.insert({**key, **checksum} for checksum in result.checksums)
+        # What GET /nwb's cursor sees first (design spec
+        # `2026-09-29-nwb-publishing-design.md` section 6).
+        nwb_schema.NwbChange.insert1({**key, "kind": "built", "changed_at": row["built_at"]})
 
 
 def run_stage(nwb_root: Path, freed: list[dict] | None = None) -> tuple[int, list[str]]:
```

```diff
--- a/wl_preproc/daemon.py
+++ b/wl_preproc/daemon.py
@@ -880,6 +880,8 @@ def run_once(
     host: str | None = None,
     share: str | None = None,
     nwb_root: Path | None = None,
+    nwb_slow=None,
+    nwb_fast=None,
 ) -> dict:
     """One pass of the runner. Returns what it did, for the daily report.
 
@@ -996,6 +998,17 @@ def run_once(
         nwb_built, nwb_errors = run_stage(nwb_root, freed=currently_freed(prefix=prefix))
         errors.extend(nwb_errors)
 
+    # Publishing (design spec `2026-09-29-nwb-publishing-design.md` section
+    # 4): opt-in on the slow share, a `publish.Share`. `None` when absent.
+    nwb_published: int | None
+    if nwb_slow is None:
+        nwb_published = None
+    else:
+        from wl_preproc.nwb.publish import run_publish
+
+        nwb_published, publish_errors = run_publish(nwb_slow, nwb_fast, freed=currently_freed(prefix=prefix))
+        errors.extend(publish_errors)
+
     archived: int | None
     if nas_root is None or host is None or share is None:
         archived = None
@@ -1011,6 +1024,7 @@ def run_once(
         "stale_jobs_reaped": reaped,
         "archived": archived,
         "nwb": nwb_built,
+        "nwb_published": nwb_published,
         # How many sessions were freed, and so skipped, when the pass began --
         # a count, so a skip never reads as an all-clear.
         "freed_skipped": freed_skipped,
```

```diff
--- a/wl_preproc/cli/main.py
+++ b/wl_preproc/cli/main.py
@@ -196,6 +196,14 @@ def main(argv: list[str] | None = None) -> int:
     # Optional like `--nas-root`: absent, the NWB builder stage is skipped
     # and says so (design spec `2026-09-28-nwb-builder-design.md` section 2).
     daemon_p.add_argument("--nwb-root", type=Path, default=None)
+    # Publishing (design spec `2026-09-29-nwb-publishing-design.md` section
+    # 3): each share as where it is mounted here and its name, the NAS named
+    # by `--host`. Without the slow share, publishing is skipped.
+    daemon_p.add_argument("--nwb-slow-root", type=Path, default=None)
+    daemon_p.add_argument("--nwb-slow-share", default=None)
+    daemon_p.add_argument("--nwb-fast-root", type=Path, default=None)
+    daemon_p.add_argument("--nwb-fast-share", default=None)
+    daemon_p.add_argument("--nwb-fast-headroom-gb", type=float, default=0.0)
 
     nwb_p = subparsers.add_parser("nwb", help="NWB export")
     nwb_sub = nwb_p.add_subparsers(dest="action", required=True)
@@ -688,10 +696,21 @@ def main(argv: list[str] | None = None) -> int:
 
     if args.group == "daemon":
         from wl_preproc.daemon import run_once
-
+        from wl_preproc.nwb.publish import Share
+
+        shares = {}
+        for tier in ("slow", "fast"):
+            root, name = getattr(args, f"nwb_{tier}_root"), getattr(args, f"nwb_{tier}_share")
+            if (root is None) != (name is None) or (root is not None and args.host is None):
+                parser.error(f"--nwb-{tier}-root needs --nwb-{tier}-share and --host, and the reverse")
+            if root is not None:
+                headroom = int(args.nwb_fast_headroom_gb * 1e9) if tier == "fast" else 0
+                shares[tier] = Share(tier=tier, mount=root, host=args.host, name=name, headroom_bytes=headroom)
+        if "fast" in shares and "slow" not in shares:
+            parser.error("--nwb-fast-root needs --nwb-slow-root: the slow share is every file's long-term home")
         report = run_once(
             prefix=args.prefix, nas_root=args.nas_root, host=args.host, share=args.share,
-            nwb_root=args.nwb_root,
+            nwb_root=args.nwb_root, nwb_slow=shares.get("slow"), nwb_fast=shares.get("fast"),
         )
         print(f"populated: {report['populated']}")
         print(f"stale jobs reaped: {report['stale_jobs_reaped']}")
@@ -709,6 +728,10 @@ def main(argv: list[str] | None = None) -> int:
             print("nwb: skipped (no --nwb-root)")
         else:
             print(f"nwb: {report['nwb']}")
+        if report["nwb_published"] is None:
+            print("nwb published: skipped (no --nwb-slow-root)")
+        else:
+            print(f"nwb published: {report['nwb_published']}")
         if report["errors"]:
             print("errors:")
             for err in report["errors"]:
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 97 passed, 1 skipped.

- [ ] **Step 5: Mutation checks.**
  - T5a (`publish.py`): `bad = mismatches(partial, checksums)` becomes `bad = []`. Fails `test_a_copy_that_fails_verification_leaves_nothing`.
  - T5b (`publish.py`): `if target.exists() or description_path(target).exists():` becomes `if False:`. Fails `test_publishing_never_overwrites_a_file_no_placement_records`.
  - T5c (`publish.py`): in `publish`, the line `source.unlink(missing_ok=True)` is deleted. Fails `test_the_daemon_publishes_every_written_file_to_the_slow_share`.
  - T5d (`publish.py`): in `run_publish`, the freed-session skip's `continue` becomes `pass`. Fails `test_publishing_skips_a_freed_session`.
  - T5e (`publish.py`): in `copy_verified`, the `finally:` block's `if partial.exists(): partial.unlink()` becomes `pass`. Fails `test_a_copy_that_fails_verification_leaves_nothing` and `test_a_copy_the_share_cannot_hold_leaves_nothing`.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/nwb/publish.py wl_preproc/schema/nwb.py wl_preproc/nwb/build.py wl_preproc/daemon.py wl_preproc/cli/main.py tests/nwb/test_publish.py tests/cli/test_daemon_nwb_options.py tests/schema/test_nwb_build.py tests/schema/test_daemon.py tests/schema/test_guardrails.py
git commit -m "feat(nwb): publish written files to the NAS, verified, described and recorded, and never over a file no placement records

<trailer lines>"
```

---

### Task 6: Placement

**Files:**
- Modify: `wl_preproc/nwb/publish.py`, `wl_preproc/daemon.py`, `wl_preproc/cli/main.py`, `tests/schema/test_nwb_build.py`, `tests/schema/test_daemon.py`

**Interfaces:**
- **Consumes:** Task 5's `Share`, `place`, `mismatches`, `record_change`, `current_placement`, `active_keys`, `activation_tuple`.
- **Produces:**
  - `publish.move(key, placement, source_share, target_share) -> dict`.
  - `publish.run_placement(slow, fast, freed=None) -> (moved, errors)`.
  - `publish.ChangedData`.
  - The daemon's report gains `nwb_moved`.

- [ ] **Step 1: Write the failing tests.** Apply these diffs:

```diff
--- a/tests/schema/test_nwb_build.py
+++ b/tests/schema/test_nwb_build.py
@@ -473,6 +473,23 @@ def slow_share(tmp_path_factory):
     return Share(tier="slow", mount=tmp_path_factory.mktemp("nwb-slow"), host="wl-nas", name="hdd")
 
 
+@pytest.fixture(scope="module")
+def fast_share(tmp_path_factory):
+    from wl_preproc.nwb.publish import Share
+
+    return Share(tier="fast", mount=tmp_path_factory.mktemp("nwb-fast"), host="wl-nas", name="nvme")
+
+
+def _set_active(*keys):
+    """What PUT /nwb/active records: the whole set, as JSON carries it."""
+    from wl_preproc.schema import nwb as nwb_schema
+
+    nwb_schema.ActiveSet.insert1({
+        "received_at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
+        "activations": [{**key, "session_datetime": key["session_datetime"].isoformat()} for key in keys],
+    })
+
+
 def _placed_path(share, key):
     from wl_preproc.schema import nwb as nwb_schema
 
@@ -582,6 +599,157 @@ def test_publishing_never_overwrites_a_file_no_placement_records(activation, pre
     assert publish_module.current_placement(key) is None
 
 
+def test_the_active_set_moves_a_file_to_the_fast_share_and_back_with_its_annotations(
+        activation, daemon_module, prefix, slow_share, fast_share):
+    """Design spec `2026-09-29-nwb-publishing-design.md` section 5: one live
+    copy, moved by the daemon to whichever share the latest active set
+    wants; an annotation appended on the fast share travels back with it."""
+    from wl_preproc.nwb.publish import current_placement, description_path, run_placement
+    from wl_preproc.schema import nwb as nwb_schema
+
+    _session_key, key, _blocks = activation
+    _set_active(key)
+    report = daemon_module.run_once(prefix=prefix, nwb_slow=slow_share, nwb_fast=fast_share)
+    assert report["nwb_moved"] >= 1
+    placement = current_placement(key)
+    assert placement["tier"] == "fast"
+    on_fast = fast_share.local(placement["path"])
+    assert on_fast.exists() and description_path(on_fast).exists()
+    assert not slow_share.local(placement["path"]).exists()
+    with h5py.File(on_fast, "a") as handle:
+        handle.create_dataset("/lab_annotation", data=[1, 2, 3])
+    _set_active()
+    run_placement(slow_share, fast_share)
+    back = current_placement(key)
+    assert back["tier"] == "slow" and not on_fast.exists()
+    with h5py.File(slow_share.local(back["path"]), "r") as handle:
+        assert handle["/lab_annotation"][:].tolist() == [1, 2, 3]
+    kinds = [change["kind"] for change in sorted((nwb_schema.NwbChange & key).to_dicts(), key=lambda c: c["change_seq"])]
+    assert kinds[-2:] == ["moved", "moved"]
+
+
+def test_changed_written_once_data_stops_a_move(activation, slow_share, fast_share):
+    """Section 5: something changed data that must never change; the file
+    stays where it is, and the report names the dataset."""
+    from wl_preproc.nwb.publish import current_placement, run_placement
+
+    _session_key, key, _blocks = activation
+    path = slow_share.local(current_placement(key)["path"])
+    with h5py.File(path, "r+") as handle:
+        original = handle["/intervals/trials/start_time"][0]
+        handle["/intervals/trials/start_time"][0] = original + 1.0
+    try:
+        _set_active(key)
+        _moved, errors = run_placement(slow_share, fast_share)
+        assert [error for error in errors if "changed on the NAS" in error and "start_time" in error]
+        assert current_placement(key)["tier"] == "slow" and path.exists()
+    finally:
+        with h5py.File(path, "r+") as handle:
+            handle["/intervals/trials/start_time"][0] = original
+        _set_active()
+
+
+def test_the_fast_share_headroom_stops_a_move(activation, slow_share, fast_share):
+    import dataclasses
+
+    from wl_preproc.nwb.publish import current_placement, run_placement
+
+    _session_key, key, _blocks = activation
+    _set_active(key)
+    try:
+        _moved, errors = run_placement(slow_share, dataclasses.replace(fast_share, headroom_bytes=10**18))
+        assert [error for error in errors if "headroom" in error]
+        assert current_placement(key)["tier"] == "slow"
+    finally:
+        _set_active()
+
+
+def test_an_old_copy_that_could_not_be_deleted_is_removed_on_the_next_pass(activation, slow_share, fast_share,
+                                                                           monkeypatch):
+    """Review Focus 1 (the plan): a reader holds the old copy, or the share
+    refuses the delete. The move stands; the next pass finishes it, so one
+    live copy remains and a later move back is not stuck on a conflict."""
+    from wl_preproc.nwb import publish as publish_module
+
+    _session_key, key, _blocks = activation
+    old = slow_share.local(publish_module.current_placement(key)["path"])
+    real = publish_module._remove_old_copy
+
+    def in_use(path):
+        raise PermissionError(f"{path} is in use")
+
+    monkeypatch.setattr(publish_module, "_remove_old_copy", in_use)
+    _set_active(key)
+    try:
+        _moved, errors = publish_module.run_placement(slow_share, fast_share)
+        assert [error for error in errors if "is in use" in error]
+        assert publish_module.current_placement(key)["tier"] == "fast" and old.exists()
+        monkeypatch.setattr(publish_module, "_remove_old_copy", real)
+        publish_module.run_placement(slow_share, fast_share)
+        assert not old.exists()
+    finally:
+        monkeypatch.setattr(publish_module, "_remove_old_copy", real)
+        _set_active()
+        publish_module.run_placement(slow_share, fast_share)
+    assert publish_module.current_placement(key)["tier"] == "slow"
+
+
+def test_an_active_set_naming_a_refused_file_changes_nothing(activation, slow_share, fast_share):
+    """Review Focus 4 (the plan): wl.works may name any activation. A refused
+    one is accepted, never published or moved, and costs no error a pass."""
+    from wl_preproc.nwb.publish import current_placement, run_placement, run_publish
+    from wl_preproc.schema import nwb as nwb_schema
+
+    session_key, _key, _blocks = activation
+    refused = (nwb_schema.NwbFile & session_key & {"status": "refused"}).keys()
+    assert refused
+    _set_active(*refused)
+    try:
+        _published, publish_errors = run_publish(slow_share, fast_share)
+        _moved, placement_errors = run_placement(slow_share, fast_share)
+        assert not [error for error in publish_errors + placement_errors if "'montage_id': 1," in error]
+        assert all(current_placement(key) is None for key in refused)
+    finally:
+        _set_active()
+
+
+def test_a_published_file_deleted_by_hand_is_reported_not_moved(activation, slow_share, fast_share):
+    """Review Focus 5 (the plan): the file is gone from its share. The
+    placement stays as recorded and every pass says so, by path."""
+    from wl_preproc.nwb.publish import current_placement, run_placement
+
+    _session_key, key, _blocks = activation
+    path = slow_share.local(current_placement(key)["path"])
+    kept = path.read_bytes()
+    path.unlink()
+    _set_active(key)
+    try:
+        _moved, errors = run_placement(slow_share, fast_share)
+        assert [error for error in errors if "missing from its share" in error and str(path) in error]
+        assert current_placement(key)["tier"] == "slow"
+    finally:
+        path.write_bytes(kept)
+        _set_active()
+
+
+def test_an_active_activation_publishes_straight_to_the_fast_share(activation, prefix, slow_share, fast_share,
+                                                                   tmp_path_factory):
+    """Section 5: a dataset can be marked active before its files exist."""
+    from wl_preproc.nwb import build as build_module
+    from wl_preproc.nwb import publish as publish_module
+
+    session_key, _key, blocks = activation
+    key = _derivative(session_key, blocks, prefix, blocks[-1])
+    _unrecord(key, slow_share, fast_share)
+    build_module.record(key, build_module.build(key, tmp_path_factory.mktemp("nwb-straight-to-fast")))
+    _set_active(key)
+    try:
+        publish_module.run_publish(slow_share, fast_share)
+        assert publish_module.current_placement(key)["tier"] == "fast"
+    finally:
+        _set_active()
+
+
 def test_one_failing_activation_does_not_stop_the_stage(activation, prefix, monkeypatch, tmp_path_factory):
     """The stage catches a failure per activation, as the archive stage
     does: the others are recorded, the failure is reported, and the failed
```

```diff
--- a/tests/schema/test_daemon.py
+++ b/tests/schema/test_daemon.py
@@ -272,7 +272,7 @@ def test_run_once_reports_what_it_did(daemon_env, prefix, tmp_path):
 
     # `nwb` joined 2026-09-28 (design spec `2026-09-28-nwb-builder-design.md`).
     assert set(baseline) == {
-        "populated", "errors", "stale_jobs_reaped", "archived", "nwb", "nwb_published", "freed_skipped"
+        "populated", "errors", "stale_jobs_reaped", "archived", "nwb", "nwb_published", "nwb_moved", "freed_skipped"
     }
     assert isinstance(baseline["freed_skipped"], int)
     assert baseline["populated"] == first["populated"], (
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/nwb tests/schema/test_nwb_build.py tests/schema/test_daemon.py -q --tb=line -p no:cacheprovider`
Expected: 7 failed, 85 passed, 1 skipped, on `cannot import name 'run_placement'`, on `_remove_old_copy` missing, and on the report's key set.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/nwb/publish.py
+++ b/wl_preproc/nwb/publish.py
@@ -33,6 +33,13 @@ class VerificationError(Exception):
     """A copy whose written-once datasets do not match the build's checksums."""
 
 
+class ChangedData(Exception):
+    """A written-once dataset that no longer matches its checksum on the NAS:
+    something changed data that must never change. The file is not moved,
+    for a person to look at (design spec
+    `2026-09-29-nwb-publishing-design.md` section 5)."""
+
+
 class PublishConflict(Exception):
     """A file already at the target that no placement records. Never
     overwritten: the lab's annotations live only inside published files
@@ -209,3 +216,74 @@ def run_publish(slow: Share, fast: Share | None = None, freed: list[dict] | None
         except Exception as exc:  # one file must not stop the others; retried next pass
             errors.append(f"NwbPlacement {key}: {exc}")
     return published, errors
+
+
+def move(key: dict, placement: dict, source_share: Share, target_share: Share) -> dict:
+    """Move one published file, and its description, to the other share:
+    checked, copied and verified, recorded, then the old copy deleted.
+    Annotations the lab appended travel with it."""
+    from wl_preproc.schema import nwb as nwb_schema
+
+    source = source_share.local(placement["path"])
+    if not source.exists():
+        raise FileNotFoundError(f"{source}: the published file is missing from its share; not moved")
+    checksums = (nwb_schema.NwbFile.Dataset & key_of(key)).to_dicts()
+    changed = mismatches(source, checksums)
+    if changed:
+        raise ChangedData(f"{source}: {len(changed)} written-once dataset(s) changed on the NAS, first "
+                          f"{changed[0]}; not moved")
+    description = (nwb_schema.NwbFile & key_of(key)).fetch1("description")
+    moved = place(key, source, target_share, checksums, description)
+    record_change(key, "moved", moved)
+    _remove_old_copy(source)
+    return moved
+
+
+def _remove_old_copy(path: Path) -> None:
+    """A moved file's old copy, and its description. A deletion that fails
+    (the share refuses, a reader holds it) leaves the move recorded; the
+    placement stage finishes it on a later pass."""
+    path.unlink(missing_ok=True)
+    description_path(path).unlink(missing_ok=True)
+
+
+def run_placement(slow: Share, fast: Share, freed: list[dict] | None = None) -> tuple[int, list[str]]:
+    """The daemon's placement stage: every published file whose share is not
+    the one the latest active set wants is moved, one live copy at a time;
+    moves to the fast share stop at its headroom. Returns `(moved,
+    failures)`."""
+    from wl_preproc.schema import nwb as nwb_schema
+
+    freed = freed or []
+    wanted_fast = active_keys()
+    shares = {"slow": slow, "fast": fast}
+    moved, errors = 0, []
+    published = nwb_schema.NwbFile & (nwb_schema.NwbChange & {"kind": "published"}).proj(
+        "subject", "session_datetime", "montage_id", "activation_id")
+    for key in published.keys():
+        if {"subject": key["subject"], "session_datetime": key["session_datetime"]} in freed:
+            continue
+        placement = current_placement(key)
+        if placement is None:
+            continue
+        # One live copy: a copy on the other share is an earlier move whose
+        # old copy could not be deleted then. Finish that move first.
+        other = shares["fast" if placement["tier"] == "slow" else "slow"].local(placement["path"])
+        if other.exists() or description_path(other).exists():
+            try:
+                _remove_old_copy(other)
+            except OSError as exc:
+                errors.append(f"NwbPlacement {key}: the old copy at {other} could not be removed: {exc}")
+                continue
+        wanted = "fast" if activation_tuple(key) in wanted_fast else "slow"
+        if placement["tier"] == wanted:
+            continue
+        if wanted == "fast" and not fast.has_room(placement["n_bytes"]):
+            errors.append(f"NwbPlacement {key}: the fast share is at its headroom; not moved")
+            continue
+        try:
+            move(key, placement, shares[placement["tier"]], shares[wanted])
+            moved += 1
+        except Exception as exc:  # one file must not stop the others; retried next pass
+            errors.append(f"NwbPlacement {key}: {exc}")
+    return moved, errors
```

```diff
--- a/wl_preproc/daemon.py
+++ b/wl_preproc/daemon.py
@@ -1009,6 +1009,17 @@ def run_once(
         nwb_published, publish_errors = run_publish(nwb_slow, nwb_fast, freed=currently_freed(prefix=prefix))
         errors.extend(publish_errors)
 
+    # Placement (section 5): with both shares, each published file is moved
+    # to the one the latest active set wants. `None` without the fast share.
+    nwb_moved: int | None
+    if nwb_slow is None or nwb_fast is None:
+        nwb_moved = None
+    else:
+        from wl_preproc.nwb.publish import run_placement
+
+        nwb_moved, placement_errors = run_placement(nwb_slow, nwb_fast, freed=currently_freed(prefix=prefix))
+        errors.extend(placement_errors)
+
     archived: int | None
     if nas_root is None or host is None or share is None:
         archived = None
@@ -1025,6 +1036,7 @@ def run_once(
         "archived": archived,
         "nwb": nwb_built,
         "nwb_published": nwb_published,
+        "nwb_moved": nwb_moved,
         # How many sessions were freed, and so skipped, when the pass began --
         # a count, so a skip never reads as an all-clear.
         "freed_skipped": freed_skipped,
```

```diff
--- a/wl_preproc/cli/main.py
+++ b/wl_preproc/cli/main.py
@@ -732,6 +732,10 @@ def main(argv: list[str] | None = None) -> int:
             print("nwb published: skipped (no --nwb-slow-root)")
         else:
             print(f"nwb published: {report['nwb_published']}")
+        if report["nwb_moved"] is None:
+            print("nwb moved: skipped (no --nwb-fast-root)")
+        else:
+            print(f"nwb moved: {report['nwb_moved']}")
         if report["errors"]:
             print("errors:")
             for err in report["errors"]:
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 92 passed, 1 skipped.

- [ ] **Step 5: Mutation checks.**
  - T6a (`publish.py`): `changed = mismatches(source, checksums)` becomes `changed = []`. Fails `test_changed_written_once_data_stops_a_move`.
  - T6b (`publish.py`): `if wanted == "fast" and not fast.has_room(placement["n_bytes"]):` becomes `if False:`. Fails `test_the_fast_share_headroom_stops_a_move`.
  - T6c (`publish.py`): `if other.exists() or description_path(other).exists():` becomes `if False:`. Fails `test_an_old_copy_that_could_not_be_deleted_is_removed_on_the_next_pass`.
  - T6d (`publish.py`): in `move`, `if not source.exists():` becomes `if False:`. Fails `test_a_published_file_deleted_by_hand_is_reported_not_moved`.
  - T6e (`publish.py`): `and activation_tuple(key) in wanted_fast and fast.has_room(` becomes `and False and fast.has_room(`. Fails `test_an_active_activation_publishes_straight_to_the_fast_share`.
  - T6f (`daemon.py`): in `run_once`, `if nwb_slow is None or nwb_fast is None:` becomes `if True:`. Fails `test_the_active_set_moves_a_file_to_the_fast_share_and_back_with_its_annotations`.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/nwb/publish.py wl_preproc/daemon.py wl_preproc/cli/main.py tests/schema/test_nwb_build.py tests/schema/test_daemon.py
git commit -m "feat(nwb): keep each file on the share the latest active set wants, one live copy, annotations with it

<trailer lines>"
```

---

### Task 7: `GET /nwb` and `PUT /nwb/active`

**Files:**
- Create: `wl_preproc/responder/nwb.py`, `tests/responder/test_nwb_http.py`, `docs/schemas/active_set_request.json`, `docs/schemas/nwb_listing.json` (exported)
- Modify: `wl_preproc/contracts/protocol.py`, `wl_preproc/responder/handler.py`, `wl_preproc/responder/server.py`, `wl_preproc/cli/main.py`, `docs/ops/lab-host-protocol.md`, `tests/schema/test_nwb_build.py`

**Interfaces:**
- **Consumes:** Tasks 5–6's `current_placement`, `activation_tuple`, `key_of`, `active_keys`.
- **Produces:**
  - Contract models in `contracts/protocol.py`: `ActivationKey`, `ActiveSetRequest`, `ActiveSetResponse`, `Placement`, `NwbListingEntry`, `NwbListing`.
  - `responder.nwb.list_files(since, prefix) -> dict` and `responder.nwb.set_active(request, prefix) -> dict`.
  - `make_handler(token, health_fn, accept_fn, nwb_list_fn=None, nwb_active_fn=None)`: the NWB routes exist only when their callables are given.

**What stays exactly as it was.**
- A query string on any path but `/nwb` is still a 404.
- `PUT` on any path but `/nwb/active` is still a 405.
- The five other foreign verbs still answer as before.
- `POST /jobs` reads its body through the same code, now shared as `_parse_body(model)`.

`test_the_older_endpoints_answer_as_before` pins all of this.

- [ ] **Step 1: Write the failing tests.** Create `tests/responder/test_nwb_http.py`:

```python
"""`GET /nwb` and `PUT /nwb/active` over real HTTP, with stand-in callables
(design spec `2026-09-29-nwb-publishing-design.md` section 6). The database
half is exercised end to end in `tests/schema/test_nwb_build.py`."""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from tests.responder.test_http import TOKEN, _health_ok, _request, _unused
from wl_preproc.responder.handler import make_handler

KEY = {"subject": "monk01", "session_datetime": "2027-01-12T09:00:00", "montage_id": 0, "activation_id": 0}


@pytest.fixture
def serve_nwb():
    started = []

    def _start(nwb_list_fn=_unused, nwb_active_fn=_unused, *, with_nwb=True):
        handler_cls = (make_handler(TOKEN, _health_ok, _unused, nwb_list_fn, nwb_active_fn) if with_nwb
                       else make_handler(TOKEN, _health_ok, _unused))
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield _start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def test_the_listing_is_behind_the_token_and_takes_an_optional_cursor(serve_nwb):
    calls = []
    base = serve_nwb(nwb_list_fn=lambda since: calls.append(since) or {"cursor": 7, "files": []})
    assert _request(f"{base}/nwb", method="GET")[0] == 401
    status, body = _request(f"{base}/nwb", method="GET", token=TOKEN)
    assert (status, json.loads(body)) == (200, {"cursor": 7, "files": []})
    assert _request(f"{base}/nwb?since=5", method="GET", token=TOKEN)[0] == 200
    assert calls == [None, 5]


@pytest.mark.parametrize("query", ["since=x", "since=-1", "since=1&since=2", "other=1", "since="])
def test_a_cursor_that_is_not_one_non_negative_integer_is_422(serve_nwb, query):
    base = serve_nwb(nwb_list_fn=_unused)
    status, body = _request(f"{base}/nwb?{query}", method="GET", token=TOKEN)
    assert status == 422 and "since" in json.loads(body)["error"]


def test_the_active_set_is_accepted_whole(serve_nwb):
    received = []

    def active(request):
        received.append(request)
        return {"accepted": len(request.activations), "unknown": []}

    base = serve_nwb(nwb_active_fn=active)
    status, body = _request(f"{base}/nwb/active", method="PUT", token=TOKEN,
                            body={"activations": [KEY], "requested_by": "jw"})
    assert (status, json.loads(body)) == (202, {"accepted": 1, "unknown": []})
    (request,) = received
    assert request.requested_by == "jw" and request.activations[0].subject == "monk01"


@pytest.mark.parametrize("body", [{"activations": [KEY], "extra": 1}, {"activations": [{**KEY, "activation_id": -1}]},
                                  b"not json"])
def test_a_malformed_active_set_is_422(serve_nwb, body):
    base = serve_nwb(nwb_active_fn=_unused)
    assert _request(f"{base}/nwb/active", method="PUT", token=TOKEN, body=body)[0] == 422


def test_a_failure_behind_either_endpoint_is_a_clean_500(serve_nwb):
    def broken(*_args):
        raise RuntimeError("the database went away")

    base = serve_nwb(nwb_list_fn=broken, nwb_active_fn=broken)
    status, body = _request(f"{base}/nwb", method="GET", token=TOKEN)
    assert (status, json.loads(body)) == (500, {"error": "RuntimeError: the database went away"})
    assert _request(f"{base}/nwb/active", method="PUT", token=TOKEN, body={"activations": []})[0] == 500


def test_the_older_endpoints_answer_as_before(serve_nwb):
    """A query string anywhere but `/nwb` is still a path this host does not
    answer, PUT anywhere but `/nwb/active` is still 405, and a handler built
    without the NWB callables has no NWB paths at all."""
    base = serve_nwb()
    assert _request(f"{base}/health?x=1", method="GET", token=TOKEN)[0] == 404
    assert _request(f"{base}/health", method="PUT", token=TOKEN)[0] == 405
    assert _request(f"{base}/nwb/active", method="GET", token=TOKEN)[0] == 405
    bare = serve_nwb(with_nwb=False)
    assert _request(f"{bare}/nwb", method="GET", token=TOKEN)[0] == 404
    assert _request(f"{bare}/nwb/active", method="PUT", token=TOKEN, body={"activations": []})[0] == 404
```

Apply this diff:

```diff
--- a/tests/schema/test_nwb_build.py
+++ b/tests/schema/test_nwb_build.py
@@ -750,6 +750,38 @@ def test_an_active_activation_publishes_straight_to_the_fast_share(activation, p
         _set_active()
 
 
+def test_the_listing_and_the_active_set_through_the_responders_functions(activation, prefix, slow_share,
+                                                                         fast_share):
+    """Design spec `2026-09-29-nwb-publishing-design.md` section 6, against
+    the database: what GET /nwb lists, what its cursor holds back, and what
+    PUT /nwb/active records, unknown activations named."""
+    from wl_preproc.contracts.protocol import ActiveSetRequest
+    from wl_preproc.nwb.publish import activation_tuple, active_keys, run_placement
+    from wl_preproc.responder.nwb import list_files, set_active
+    from wl_preproc.schema import nwb as nwb_schema
+
+    _session_key, key, _blocks = activation
+    identifier = (nwb_schema.NwbFile & key).fetch1("nwb_identifier")
+    everything = list_files(None, prefix=prefix)
+    (entry,) = [item for item in everything["files"] if item["identifier"] == identifier]
+    assert entry["status"] == "written" and entry["placement"]["tier"] == "slow"
+    assert entry["description"]["identity"]["identifier"] == identifier
+    cursor = everything["cursor"]
+    assert list_files(cursor, prefix=prefix)["files"] == []
+    answer = set_active(ActiveSetRequest.model_validate(
+        {"activations": [key, {**key, "activation_id": 99}], "requested_by": "jw"}), prefix=prefix)
+    assert answer["accepted"] == 2 and [item["activation_id"] for item in answer["unknown"]] == [99]
+    assert activation_tuple(key) in active_keys()
+    try:
+        run_placement(slow_share, fast_share)
+        changed = list_files(cursor, prefix=prefix)
+        assert [item["placement"]["tier"] for item in changed["files"] if item["identifier"] == identifier] == ["fast"]
+        assert changed["cursor"] > cursor
+    finally:
+        _set_active()
+        run_placement(slow_share, fast_share)
+
+
 def test_one_failing_activation_does_not_stop_the_stage(activation, prefix, monkeypatch, tmp_path_factory):
     """The stage catches a failure per activation, as the archive stage
     does: the others are recorded, the failure is reported, and the failed
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/responder tests/cli/test_schemas_export.py tests/schema/test_nwb_build.py -q --tb=line -p no:cacheprovider`
Expected: 13 failed, 168 passed: `TypeError` from `make_handler`, which does not yet take the NWB callables, and `cannot import name 'ActiveSetRequest'`.

- [ ] **Step 3: Implement.** Create `wl_preproc/responder/nwb.py`:

```python
"""What `GET /nwb` and `PUT /nwb/active` do (design spec
`2026-09-29-nwb-publishing-design.md` sections 5 and 6).

`handler.py` owns HTTP and imports no DataJoint; this module owns the
database, as `jobs.py` does for `POST /jobs`, and `server.py` wires the two
together under the responder's one lock."""

from __future__ import annotations

import datetime

from wl_preproc.contracts.protocol import ActiveSetRequest, ActiveSetResponse, NwbListing
from wl_preproc.schema import DEFAULT_PREFIX


def _key_json(key: dict) -> dict:
    moment = key["session_datetime"]
    if isinstance(moment, datetime.datetime) and moment.tzinfo is not None:
        moment = moment.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return {"subject": key["subject"], "session_datetime": moment.isoformat(),
            "montage_id": int(key["montage_id"]), "activation_id": int(key["activation_id"])}


def list_files(since: int | None, prefix: str = DEFAULT_PREFIX) -> dict:
    """Every activation whose file changed after `since` (all of them when
    None): its status, where its file is now, and its description."""
    from wl_preproc.nwb.publish import activation_tuple, current_placement, key_of
    from wl_preproc.schema import nwb as nwb_schema

    nwb_schema.activate(prefix=prefix)
    changes = nwb_schema.NwbChange.to_dicts() if since is None else \
        (nwb_schema.NwbChange & f"change_seq > {int(since)}").to_dicts()
    cursor = max((change["change_seq"] for change in changes), default=since or 0)
    keys = {activation_tuple(change): key_of(change) for change in changes}
    files = []
    for _tuple, key in sorted(keys.items()):
        rows = (nwb_schema.NwbFile & key).to_dicts()
        if not rows:
            continue
        row, placement = rows[0], current_placement(key)
        files.append({
            "activation": _key_json(key),
            "identifier": row["nwb_identifier"],
            "status": row["status"],
            "reason": row["reason"],
            "placement": None if placement is None else {
                field: placement[field] for field in ("tier", "host", "share", "path", "n_bytes")},
            "description": row["description"],
        })
    return NwbListing.model_validate({"cursor": cursor, "files": files}).model_dump(mode="json")


def set_active(request: ActiveSetRequest, prefix: str = DEFAULT_PREFIX) -> dict:
    """Record the whole active set as received; name the activations this
    host has no row for yet. The placement stage acts on it next pass."""
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema import request as request_schema

    nwb_schema.activate(prefix=prefix)
    activations = [_key_json(key.model_dump()) for key in request.activations]
    nwb_schema.ActiveSet.insert1({
        "received_at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
        "requested_by": request.requested_by,
        "activations": activations,
    })
    unknown = [key for key in activations
               if not request_schema.Activation & {**key, "session_datetime": datetime.datetime.fromisoformat(
                   key["session_datetime"])}]
    return ActiveSetResponse.model_validate({"accepted": len(activations), "unknown": unknown}).model_dump(mode="json")
```

Apply these diffs:

```diff
--- a/wl_preproc/contracts/protocol.py
+++ b/wl_preproc/contracts/protocol.py
@@ -240,3 +240,75 @@ class JobRequest(BaseModel):
     parameters: dict[str, Any]
     idempotency_key: str
     metadata: MetadataBundle
+
+
+# -- NWB files: GET /nwb and PUT /nwb/active (design spec
+# `2026-09-29-nwb-publishing-design.md` section 6). -------------------------
+
+
+class ActivationKey(BaseModel):
+    """One activation, as wl.works names it back to this host. A naive
+    `session_datetime` is UTC, as every one this host issues is."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    subject: Annotated[str, Field(min_length=1, max_length=64)]
+    session_datetime: datetime.datetime
+    montage_id: _MontageId
+    activation_id: Annotated[int, Field(ge=0)]
+
+
+class ActiveSetRequest(BaseModel):
+    """`PUT /nwb/active`: the WHOLE set of activations wl.works wants on the
+    fast share, every time. Sending the same set twice changes nothing."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    activations: list[ActivationKey]
+    requested_by: Annotated[str, Field(min_length=1, max_length=64)] | None = None
+
+
+class ActiveSetResponse(BaseModel):
+    """`202`: how many activations the set holds, and which of them this
+    host has no activation for yet (kept; they may be built later)."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    accepted: int
+    unknown: list[ActivationKey]
+
+
+class Placement(BaseModel):
+    """Where a published file is: wl.works' location triple (its Plan 23
+    section 10.1), the path relative to the share, and which share tier."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    tier: Literal["slow", "fast"]
+    host: str
+    share: str
+    path: str
+    n_bytes: int
+
+
+class NwbListingEntry(BaseModel):
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    activation: ActivationKey
+    identifier: str
+    status: Literal["written", "invalid", "refused"]
+    reason: str
+    placement: Placement | None
+    # `contracts/nwb_description.py::NwbDescription`, exported on its own as
+    # `nwb_description.json`; null for a refused activation.
+    description: dict[str, Any] | None
+
+
+class NwbListing(BaseModel):
+    """`GET /nwb?since=<cursor>`: every file whose record changed after the
+    cursor, and the cursor to send next time. The cursor only increases."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    cursor: int
+    files: list[NwbListingEntry]
```

```diff
--- a/wl_preproc/responder/handler.py
+++ b/wl_preproc/responder/handler.py
@@ -182,13 +182,19 @@ import hmac
 import json
 from http.server import BaseHTTPRequestHandler
 from typing import Any, Callable
+from urllib.parse import parse_qs
 
 from pydantic import ValidationError
 
-from wl_preproc.contracts.protocol import JobRequest
+from wl_preproc.contracts.protocol import ActiveSetRequest, JobRequest
 
 _HEALTH_PATH = "/health"
 _JOBS_PATH = "/jobs"
+# NWB files (design spec `2026-09-29-nwb-publishing-design.md` section 6):
+# answered only by a handler built with their callables. `/nwb` is the one
+# path that takes a query string (`?since=<cursor>`).
+_NWB_PATH = "/nwb"
+_NWB_ACTIVE_PATH = "/nwb/active"
 
 # The two known paths, mapped to which HTTP method each one answers -- used
 # by both do_GET and do_POST (and do_PUT's five aliases) to decide 404 (path
@@ -350,6 +356,8 @@ def make_handler(
     token: str,
     health_fn: Callable[[], Any],
     accept_fn: Callable[[JobRequest], dict],
+    nwb_list_fn: Callable[[int | None], dict] | None = None,
+    nwb_active_fn: Callable[[ActiveSetRequest], dict] | None = None,
 ) -> type[BaseHTTPRequestHandler]:
     """Build a `BaseHTTPRequestHandler` subclass closing over `token` and the
     two callables. A fresh class per call -- not a module-level singleton --
@@ -411,6 +419,15 @@ def make_handler(
         _token = token
         _health_fn = staticmethod(health_fn)
         _accept_fn = staticmethod(accept_fn)
+        _nwb_list_fn = staticmethod(nwb_list_fn) if nwb_list_fn is not None else None
+        _nwb_active_fn = staticmethod(nwb_active_fn) if nwb_active_fn is not None else None
+        # This handler's route table: the two fixed endpoints, plus the NWB
+        # ones when their callables were given.
+        _paths = {
+            **_PATH_METHODS,
+            **({_NWB_PATH: "GET"} if nwb_list_fn is not None else {}),
+            **({_NWB_ACTIVE_PATH: "PUT"} if nwb_active_fn is not None else {}),
+        }
 
         # Review Important 5: BaseHTTPRequestHandler's version_string() joins
         # these two into the Server header sent on EVERY response (this
@@ -613,8 +630,11 @@ def make_handler(
             to 404 or 405 here, never `None`, since `_PATH_METHODS` never
             maps any path to any of those six verbs.
             """
-            expected = _PATH_METHODS.get(self.path)
-            if expected is None:
+            path, _, query = self.path.partition("?")
+            expected = self._paths.get(path)
+            # A query string is `/nwb`'s alone; anywhere else it is a path
+            # this host does not answer, exactly as before `/nwb` existed.
+            if expected is None or (query and path != _NWB_PATH):
                 self._send_json(404, {"error": "not found"})
                 return 404
             if expected != method:
@@ -628,8 +648,10 @@ def make_handler(
                 return
             if self._route_or_none("GET") is not None:
                 return
-            # Only _HEALTH_PATH answers GET (see _PATH_METHODS) -- reached
-            # only when self.path == _HEALTH_PATH.
+            if self.path.partition("?")[0] == _NWB_PATH:
+                self._get_nwb()
+                return
+            # Otherwise only _HEALTH_PATH answers GET (see _PATH_METHODS).
             try:
                 response = self._health_fn()
                 # response.model_dump_json() moved INSIDE this try -- review
@@ -761,6 +783,21 @@ def make_handler(
             else:
                 self._send_json(200, {"activation": result, "accepted": True})
 
+        def _get_nwb(self) -> None:
+            """`GET /nwb[?since=<cursor>]`: 200 with the listing; 422 when
+            `since` is anything but one non-negative integer."""
+            query = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
+            values = query.get("since", [])
+            if set(query) - {"since"} or len(values) > 1 or (values and not values[0].isdigit()):
+                self._send_json(422, {"error": "the only parameter is since, one non-negative integer"})
+                return
+            try:
+                payload = self._nwb_list_fn(int(values[0]) if values else None)
+            except Exception as exc:  # noqa: BLE001 -- see module docstring's table
+                self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
+                return
+            self._send_json(200, payload)
+
         def _parse_job_request(self) -> JobRequest:
             """The request body as a validated `JobRequest`. Raises
             `ValueError` (via this method's own `Content-Length` checks,
@@ -844,19 +881,20 @@ def make_handler(
             default, a digit string) -- an empty body then fails at
             `json.loads(b"")`, still a `ValueError`, just one step later.
             """
+            return self._parse_body(JobRequest)
+
+        def _parse_body(self, model):
+            """The body as a validated `model`, under exactly the
+            `Content-Length`, size and timeout rules `_parse_job_request`
+            documents; `PUT /nwb/active` reads its body the same way."""
             raw_length = self.headers.get("Content-Length", "0")
             if not raw_length.isdigit():
-                raise ValueError(
-                    f"Content-Length {raw_length!r} is not a valid non-negative integer"
-                )
+                raise ValueError(f"Content-Length {raw_length!r} is not a valid non-negative integer")
             length = int(raw_length)
             if length > _MAX_CONTENT_LENGTH:
-                raise ValueError(
-                    f"Content-Length {length} exceeds the {_MAX_CONTENT_LENGTH}-byte limit"
-                )
+                raise ValueError(f"Content-Length {length} exceeds the {_MAX_CONTENT_LENGTH}-byte limit")
             raw = self.rfile.read(length) if length else b""
-            payload = json.loads(raw.decode("utf-8"))
-            return JobRequest.model_validate(payload)
+            return model.model_validate(json.loads(raw.decode("utf-8")))
 
         # Review Important 5: every verb neither endpoint answers still
         # authenticates FIRST, exactly like do_GET/do_POST -- the
@@ -876,17 +914,43 @@ def make_handler(
         # actually sends: an authenticated caller gets 404 or 405, the
         # honest routing answer, where send_error's blanket 501-becomes-401
         # would tell an authenticated caller nothing at all.
-        def do_PUT(self) -> None:
+        def _foreign(self) -> None:
             if not self._authorized():
                 self._send_json(401, _UNAUTHORIZED_BODY)
                 return
             self._route_or_none(self.command)
 
-        do_DELETE = do_PUT
-        do_HEAD = do_PUT
-        do_OPTIONS = do_PUT
-        do_PATCH = do_PUT
-        do_TRACE = do_PUT
+        def do_PUT(self) -> None:
+            """`PUT /nwb/active` (design spec
+            `2026-09-29-nwb-publishing-design.md` section 6): 202 with the
+            count accepted and the activations unknown here; 422 for a
+            malformed body; 408 when it never arrives. PUT anywhere else is
+            the foreign-verb answer above, unchanged."""
+            if not self._authorized():
+                self._send_json(401, _UNAUTHORIZED_BODY)
+                return
+            if self._route_or_none("PUT") is not None:
+                return
+            try:
+                request = self._parse_body(ActiveSetRequest)
+            except ValueError as exc:
+                self._send_json(422, _error_body(exc))
+                return
+            except TimeoutError:
+                self._send_json(408, {"error": "request timed out"})
+                return
+            try:
+                result = self._nwb_active_fn(request)
+            except Exception as exc:  # noqa: BLE001 -- see module docstring's table
+                self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
+                return
+            self._send_json(202, result)
+
+        do_DELETE = _foreign
+        do_HEAD = _foreign
+        do_OPTIONS = _foreign
+        do_PATCH = _foreign
+        do_TRACE = _foreign
 
         # log_message is deliberately NOT overridden: BaseHTTPRequestHandler's
         # default writes one access-log line per request to stderr, which is
```

```diff
--- a/wl_preproc/responder/server.py
+++ b/wl_preproc/responder/server.py
@@ -142,6 +142,7 @@ from http.server import ThreadingHTTPServer
 from pathlib import Path
 
 from wl_preproc.responder import health, jobs
+from wl_preproc.responder import nwb as nwb_endpoints
 from wl_preproc.responder.handler import ConflictError, make_handler
 from wl_preproc.schema import DEFAULT_PREFIX
 from wl_preproc.schema.request import KeyReuseError
@@ -213,7 +214,18 @@ def serve(
         with lock:
             return _translate_accept_errors(request, prefix=prefix)
 
-    handler_cls = make_handler(token, locked_health_fn, locked_accept_fn)
+    # GET /nwb and PUT /nwb/active (design spec
+    # `2026-09-29-nwb-publishing-design.md` section 6), under the same lock.
+    def locked_nwb_list_fn(since):
+        with lock:
+            return nwb_endpoints.list_files(since, prefix=prefix)
+
+    def locked_nwb_active_fn(request):
+        with lock:
+            return nwb_endpoints.set_active(request, prefix=prefix)
+
+    handler_cls = make_handler(token, locked_health_fn, locked_accept_fn, locked_nwb_list_fn,
+                               locked_nwb_active_fn)
     httpd = ThreadingHTTPServer(("", port), handler_cls)
     try:
         if ready is not None:
```

```diff
--- a/wl_preproc/cli/main.py
+++ b/wl_preproc/cli/main.py
@@ -24,7 +24,7 @@ from wl_sync.log import SyncBoxLogHeader
 from wl_preproc.contracts.done import DoneMarker
 from wl_preproc.contracts.manifest import SessionManifest
 from wl_preproc.contracts.nwb_description import NwbDescription
-from wl_preproc.contracts.protocol import HealthResponse, JobRequest
+from wl_preproc.contracts.protocol import ActiveSetRequest, HealthResponse, JobRequest, NwbListing
 from wl_preproc.contracts.sidecar import BehaviorCameraSidecar
 
 # `wl_preproc.schema.__init__` is deliberately import-cheap (a constant and
@@ -39,6 +39,8 @@ EXPORTED_MODELS: dict[str, type[BaseModel]] = {
     "health_response": HealthResponse,
     "job_request": JobRequest,
     "nwb_description": NwbDescription,
+    "active_set_request": ActiveSetRequest,
+    "nwb_listing": NwbListing,
 }
 
 
```

Export the schemas: `.venv/bin/python -m wl_preproc.cli.main schemas export --out docs/schemas`. Expected: `git status --short docs/schemas` lists only the new `active_set_request.json` and `nwb_listing.json`.

Then document the endpoints. Apply this diff to `docs/ops/lab-host-protocol.md`:

````diff
--- a/docs/ops/lab-host-protocol.md
+++ b/docs/ops/lab-host-protocol.md
@@ -27,7 +27,7 @@ buried in it, and it is right to:
 
 And its counterpart, which is this host's own:
 
-> **Every request to this host must carry a bearer token.** Both endpoints. An action the
+> **Every request to this host must carry a bearer token.** Every endpoint. An action the
 > app declines to show is still an HTTP endpoint on the LAN, and the population of an
 > unauthenticated LAN endpoint is not "any lab member" — it is anything plugged into the
 > lab network.
@@ -403,21 +403,67 @@ that same activation.
 
 ---
 
+## `GET /nwb`
+
+*Added 2026-09-29 with NWB publishing (`docs/superpowers/specs/2026-09-29-nwb-publishing-design.md`
+section 6).* Lists every NWB file whose record changed after a cursor, so wl.works can learn
+where each file is and what it holds without opening it.
+
+```
+GET /nwb?since=<cursor>
+Authorization: Bearer <token>
+```
+
+- **`since`** is optional, one non-negative integer. Without it, every file is listed.
+- **The response** is [`docs/schemas/nwb_listing.json`](../schemas/nwb_listing.json):
+  `{"cursor": <int>, "files": [...]}`. Send `cursor` as the next `since`; it only increases.
+- **Each file:**
+  - `activation`: the key;
+  - `identifier` and `status` (`written`, `invalid` or `refused`), and `reason`;
+  - `placement`: `tier` (`fast` or `slow`), `host`, `share`, `path` relative to the share,
+    and `n_bytes`, or `null` until published;
+  - `description`: [`docs/schemas/nwb_description.json`](../schemas/nwb_description.json),
+    or `null` for a refused activation.
+
+A file changes when it is built, published, or moved between shares.
+
+## `PUT /nwb/active`
+
+*Added 2026-09-29, the same spec, section 5.* The **whole** set of activations wl.works wants
+on the fast share, every time. This host records it and moves files in the daemon's next
+pass; `GET /nwb` then shows each file's new `placement`. Sending the same set twice changes
+nothing.
+
+```json
+{"activations": [{"subject": "…", "session_datetime": "2027-01-12T09:00:00",
+                  "montage_id": 0, "activation_id": 0}],
+ "requested_by": "<wl.works user, or null>"}
+```
+
+The body is [`docs/schemas/active_set_request.json`](../schemas/active_set_request.json),
+read under the same `Content-Length` rules as `POST /jobs`. The answer is `202` with
+`{"accepted": <n>, "unknown": [<keys this host has no activation for yet>]}`. Unknown keys
+are kept: a dataset may be marked active before its files exist, and a file in the set
+publishes straight to the fast share.
+
+---
+
 ## Status codes
 
-Every code this host can return, on either endpoint.
+Every code this host can return, on any endpoint.
 
 | Code | Endpoint | Body | Meaning | Retryable? |
 |---|---|---|---|---|
-| `200` | both | the response above | Accepted, or health served. A `down` verdict is a `200`. | — |
+| `200` | all but `PUT /nwb/active` | the response above | Accepted, health served, or files listed. A `down` verdict is a `200`. | — |
+| `202` | `PUT /nwb/active` | `{"accepted": …, "unknown": […]}` | The active set is recorded; the daemon's next pass moves files. | — |
 | `400` | both | `{"error": "bad request"}` | Malformed request line, or an unparseable version such as `HTTP/9.9.9`. See the framing note below. | No — fix the client |
 | `401` | both | `{"error": "unauthorized"}` | Missing, wrong-scheme, or wrong token; or a verb neither endpoint answers. | No — fix the credential |
-| `404` | both | `{"error": "not found"}` | Path is neither `/health` nor `/jobs`. | No |
+| `404` | all | `{"error": "not found"}` | Path is not one of `/health`, `/jobs`, `/nwb`, `/nwb/active`; or a query string on any path but `/nwb`. | No |
 | `405` | both | `{"error": "method not allowed"}` | Known path, wrong verb — `GET /jobs`, `POST /health`, authenticated `PUT /health`. | No |
-| `408` | `POST /jobs` | `{"error": "request timed out"}` | The declared body never fully arrived. | **Yes** |
+| `408` | `POST /jobs`, `PUT /nwb/active` | `{"error": "request timed out"}` | The declared body never fully arrived. | **Yes** |
 | `409` | `POST /jobs` | `{"error": "<what differed>"}` | Idempotency key reused for materially different content. | **No — needs a human** |
 | `414` | both | `{"error": "request line too long"}` | Over-long request line. | No |
-| `422` | `POST /jobs` | `{"error": "…"}` or `{"error": "invalid request body", "detail": […]}` | The request is malformed, or asks for something this host cannot do — **including naming a session it has not ingested yet**. | No — fix and resend; for a not-yet-ingested session, resend once the transfer lands |
+| `422` | `POST /jobs`, `PUT /nwb/active`, `GET /nwb` (a `since` that is not one non-negative integer) | `{"error": "…"}` or `{"error": "invalid request body", "detail": […]}` | The request is malformed, or asks for something this host cannot do — **including naming a session it has not ingested yet**. | No — fix and resend; for a not-yet-ingested session, resend once the transfer lands |
 | `431` | both | `{"error": "request header fields too large"}` | Oversized header. | No |
 | `500` | both | `{"error": "<ExceptionType>: <message>"}` | This host's own fault, infrastructure included. | **Yes** |
 | `505` | both | `{"error": "http version not supported"}` | `HTTP/2.0` or later. See the framing note below. | No |
@@ -765,6 +811,11 @@ discovering an option.
   progress is not observable through this protocol at all. When it becomes observable it
   will be as a **reading**, because readings are the surface this host already publishes
   and wl.works already polls — not as a new endpoint.
+  - *Amended 2026-09-29 for NWB files, not job progress: `GET /nwb` is a new endpoint,
+    because what wl.works needs there is a list of files, each with its location and its
+    description, which a reading's single `label`/`value` string cannot carry
+    (`docs/superpowers/specs/2026-09-29-nwb-publishing-design.md` section 6). Job progress
+    itself is unchanged by this: still a reading when it arrives.*
 - **No result upload.** wl.works pulls; this host never pushes. Rows 27 and 29 already
   discover their outputs by polling the NAS.
 - **No TLS.** Plan 10 §5.4 makes plain HTTP the stated default for this leg and argues it:
@@ -785,11 +836,14 @@ discovering an option.
 
 ## Machine-readable schemas
 
-`wlpp schemas export` writes JSON Schema for both wire contracts, and CI diffs the
+`wlpp schemas export` writes JSON Schema for every wire contract, and CI diffs the
 directory so a drifted export fails the build:
 
 - [`docs/schemas/health_response.json`](../schemas/health_response.json)
 - [`docs/schemas/job_request.json`](../schemas/job_request.json)
+- [`docs/schemas/nwb_listing.json`](../schemas/nwb_listing.json) and
+  [`docs/schemas/nwb_description.json`](../schemas/nwb_description.json), `GET /nwb`
+- [`docs/schemas/active_set_request.json`](../schemas/active_set_request.json), `PUT /nwb/active`
 
 These are what wl.works' contract tests should validate against, and they are why this
 protocol needed no OpenAPI-generating web framework on the box that holds every session's
````

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 181 passed.

- [ ] **Step 5: Mutation checks.**
  - T7a (`handler.py`): `if set(query) - {"since"} or len(values) > 1 or (values and not values[0].isdigit()):` becomes `if False:`. Fails `test_a_cursor_that_is_not_one_non_negative_integer_is_422`.
  - T7b (`handler.py`): `if expected is None or (query and path != _NWB_PATH):` becomes `if expected is None:`. Fails `test_the_older_endpoints_answer_as_before`.
  - T7c (`handler.py`): `self._send_json(202, result)` becomes `self._send_json(200, result)`. Fails `test_the_active_set_is_accepted_whole`.
  - T7d (`nwb.py`): `(nwb_schema.NwbChange & f"change_seq > {int(since)}").to_dicts()` becomes `nwb_schema.NwbChange.to_dicts()`. Fails `test_the_listing_and_the_active_set_through_the_responders_functions`.
  - T7e (`nwb.py`): `if not request_schema.Activation &` becomes `if False and request_schema.Activation &`. Fails `test_the_listing_and_the_active_set_through_the_responders_functions`.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/responder wl_preproc/contracts/protocol.py wl_preproc/cli/main.py docs/schemas docs/ops/lab-host-protocol.md tests/responder/test_nwb_http.py tests/schema/test_nwb_build.py
git commit -m "feat(responder): GET /nwb lists files with their place and description; PUT /nwb/active sets the active set

<trailer lines>"
```

---

### Task 8: `canonical_nwb_present` becomes real

**Files:**
- Modify: `wl_preproc/archive/reclaim.py`, `tests/archive/test_reclaim.py`

**Interfaces — consumes:** Task 5's `current_placement`.

- [ ] **Step 1: Write the failing test.** Apply this diff. It replaces the test that pinned the hard-coded detail "NWB export is not built (Phase 3)", which existed to break when the real query arrived. The new test deletes the `NwbFile` rows it makes, which have no file behind them, so the NWB stages in later test modules never try to publish them:

```diff
--- a/tests/archive/test_reclaim.py
+++ b/tests/archive/test_reclaim.py
@@ -262,8 +262,9 @@ def _hold(key, *, verdict: str, hour: int = 11):
 def test_pins_condition_names_and_order_to_production(session, prefix):
     """A session set up to pass every condition that CAN pass today returns
     conditions whose `.name`s equal `CONDITION_NAMES`, in that order -- and
-    is blocked by `canonical_nwb_present` alone, because NWB export does not
-    exist yet (2026-09-26 rehydration design, section 0 ruling 3)."""
+    is blocked by `canonical_nwb_present` alone, because the session has no
+    published canonical NWB (2026-09-26 rehydration design, section 0 ruling
+    3; design spec `2026-09-29-nwb-publishing-design.md` section 8)."""
     from wl_preproc.archive.reclaim import reclaim_conditions
 
     key = session("rclmall")
@@ -340,21 +341,57 @@ def test_pins_condition_kinds_to_production(session, prefix):
     assert _condition(predicate, "timing_resolved").overridable is False
 
 
-def test_canonical_nwb_present_fails_until_phase_3(session, prefix):
-    """Ruling 3: reclamation follows the canonical NWB, and NWB export is not
-    built. Pinned on the detail string, so that wiring the real query in
-    Phase 3 breaks this test -- the reminder to update what a reader of the
-    report sees."""
-    from wl_preproc.archive.reclaim import reclaim_conditions
+_NWB_STATES = {
+    "no montage": ("rcnwb0", "no montage, so no canonical activation"),
+    "no activation": ("rcnwb1", "montage 0: no canonical activation"),
+    "not built": ("rcnwb2", "montage 0: its canonical NWB is not built yet"),
+    "invalid": ("rcnwb3", "montage 0: its canonical NWB is invalid"),
+    "unpublished": ("rcnwb4", "montage 0: its canonical NWB is built but not yet published"),
+    "published": ("rcnwb5", ""),
+}
 
-    key = session("rclmnwb")
 
-    predicate = reclaim_conditions(key, expected_file_count=0, prefix=prefix)
+@pytest.mark.parametrize("state", list(_NWB_STATES))
+def test_canonical_nwb_present_follows_the_published_canonical_file(session, prefix, state):
+    """Design spec `2026-09-29-nwb-publishing-design.md` section 8: true only
+    when every montage has a canonical activation whose file is `written`
+    and published; otherwise false, saying why, and still overridable.
 
-    nwb = _condition(predicate, "canonical_nwb_present")
-    assert nwb.passed is False
-    assert nwb.overridable is True
-    assert nwb.detail == "NWB export is not built (Phase 3)"
+    *Until 2026-09-29 this test pinned the hard-coded detail "NWB export is
+    not built (Phase 3)", as the reminder to update it when the real query
+    arrived; true when written.*"""
+    from wl_preproc.archive.reclaim import reclaim_conditions
+    from wl_preproc.nwb.publish import record_change
+    from wl_preproc.schema import core, request
+    from wl_preproc.schema import nwb as nwb_schema
+
+    subject, detail = _NWB_STATES[state]
+    key = session(subject)
+    request.activate(prefix=prefix)
+    nwb_schema.activate(prefix=prefix)
+    now = datetime.datetime(2027, 5, 1, 12, 0)
+    activation = {**key, "montage_id": 0, "activation_id": 0}
+    if state != "no montage":
+        core.Montage.insert1({**key, "montage_id": 0, "start_s": 0.0, "end_s": 10.0})
+    if state not in ("no montage", "no activation"):
+        request.Request.insert1({"idempotency_key": f"{subject}-k", "task_type": "neural", "origin": "wl_works",
+                                 "payload": {}, "requested_at": now})
+        request.Activation.insert1({**activation, "role": "canonical", "request_key": f"{subject}-k",
+                                    "created_at": now})
+    try:
+        if state in ("invalid", "unpublished", "published"):
+            nwb_schema.NwbFile.insert1({**activation, "status": "invalid" if state == "invalid" else "written",
+                                        "built_at": now})
+        if state == "published":
+            record_change(activation, "published",
+                          {"tier": "slow", "host": "wl-nas", "share": "hdd", "path": "nwb/x.nwb", "n_bytes": 1})
+
+        nwb = _condition(reclaim_conditions(key, expected_file_count=0, prefix=prefix), "canonical_nwb_present")
+        assert (nwb.passed, nwb.detail, nwb.overridable) == (state == "published", detail, True)
+    finally:
+        # These rows have no file behind them. Left in the suite's shared
+        # database, the NWB stages in later tests would try to publish them.
+        (nwb_schema.NwbFile & activation).delete(prompt=False)
 
 
 def test_a_force_overrides_tier_d_and_the_missing_nwb(session, prefix):
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/archive tests/cli/test_archive_cli.py -q --tb=line -p no:cacheprovider`
Expected: 6 failed, 100 passed: every state of the new test, because the old condition is a hard-coded `False` with the Phase 3 detail.

- [ ] **Step 3: Implement.** Apply this diff:

```diff
--- a/wl_preproc/archive/reclaim.py
+++ b/wl_preproc/archive/reclaim.py
@@ -73,6 +73,34 @@ def reclaimable(predicate: Predicate) -> bool:
     return not blocking(predicate)
 
 
+def _canonical_nwb(session_key: dict, prefix: str) -> tuple[bool, str]:
+    """Whether every montage of the session has a canonical activation whose
+    NWB file is `written` and published, on either share (design spec
+    `2026-09-29-nwb-publishing-design.md` section 8), and, when not, why."""
+    from wl_preproc.nwb.publish import current_placement
+    from wl_preproc.schema import core, request
+    from wl_preproc.schema import nwb as nwb_schema
+
+    nwb_schema.activate(prefix=prefix)
+    montages = sorted(int(m) for m in (core.Montage & session_key).to_arrays("montage_id"))
+    if not montages:
+        return False, "no montage, so no canonical activation"
+    problems = []
+    for montage_id in montages:
+        canonical = (request.Activation & session_key & {"montage_id": montage_id, "role": "canonical"}).keys()
+        if not canonical:
+            problems.append(f"montage {montage_id}: no canonical activation")
+        for key in canonical:
+            rows = (nwb_schema.NwbFile & key).to_dicts()
+            if not rows:
+                problems.append(f"montage {montage_id}: its canonical NWB is not built yet")
+            elif rows[0]["status"] != "written":
+                problems.append(f"montage {montage_id}: its canonical NWB is {rows[0]['status']}")
+            elif current_placement(key) is None:
+                problems.append(f"montage {montage_id}: its canonical NWB is built but not yet published")
+    return not problems, "; ".join(problems)
+
+
 def reclaim_conditions(
     session_key: dict,
     expected_file_count: int,
@@ -211,15 +239,14 @@ def reclaim_conditions(
                 "no paramset queue exists yet (2b-5); passes vacuously",
                 overridable=True,
             ),
-            # Fails, unlike the vacuous condition above: the requester's
-            # position is that reclamation follows the canonical NWB
-            # (2026-09-26 rehydration design, section 0, ruling 3), so the
-            # absence of NWB export must block rather than wave through. It
-            # gains a real query when Phase 3 writes one.
+            # The requester's position is that reclamation follows the
+            # canonical NWB (2026-09-26 rehydration design, section 0, ruling
+            # 3). *A hard-coded False, "NWB export is not built (Phase 3)",
+            # until the NWB publishing design of 2026-09-29 gave it this
+            # query; true when written.*
             Condition(
                 "canonical_nwb_present",
-                False,
-                "NWB export is not built (Phase 3)",
+                *_canonical_nwb(session_key, prefix),
                 overridable=True,
             ),
             # Safety-kind: a hold must block. A force clears it by being the
```

- [ ] **Step 4: Run it to verify it passes**

Run: the Step 2 command. Expected: 106 passed.

- [ ] **Step 5: Mutation checks.**
  - T8a (`reclaim.py`): `elif current_placement(key) is None:` becomes `elif False:`. Fails `test_canonical_nwb_present_follows_the_published_canonical_file`.
  - T8b (`reclaim.py`): `elif rows[0]["status"] != "written":` becomes `elif False:`. Fails `test_canonical_nwb_present_follows_the_published_canonical_file`.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/archive/reclaim.py tests/archive/test_reclaim.py
git commit -m "feat(archive): reclamation's canonical_nwb_present is true once every montage's canonical NWB is published

<trailer lines>"
```

---

### Task 9: What wl.works and wl-xcon must do, records, and the full suite

**Files:**
- Modify: `docs/pending-wl-works-amendments.md`, `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/pending-wl-xcon-amendments.md`, `docs/handoffs/2026-09-29-nwb-publishing.md`

- [ ] **Step 1: wl.works' half.** Apply this diff:

```diff
--- a/docs/pending-wl-works-amendments.md
+++ b/docs/pending-wl-works-amendments.md
@@ -1,12 +1,57 @@
 # Amendments to wl-works
 
-**Three are outstanding: two opened 2026-08-22, one 2026-09-28.** The earlier two batches are
-closed; their records are kept below, because
+**Four are outstanding: two opened 2026-08-22, one 2026-09-28, one 2026-09-29.** The earlier two
+batches are closed; their records are kept below, because
 [`specs/2026-08-12-wl-preproc-design.md`](superpowers/specs/2026-08-12-wl-preproc-design.md)
 §14 items 10–11 point at it and a reference that dead-ends teaches nothing.
 
 ---
 
+# OPEN — NWB files: find them, select them by what they hold, and say which are active
+
+**Opened 2026-09-29** with NWB publishing
+([`specs/2026-09-29-nwb-publishing-design.md`](superpowers/specs/2026-09-29-nwb-publishing-design.md)),
+the requester's decisions: every finished NWB file is published to the NAS with a description
+of what it holds; a file is on the fast share when it belongs to a dataset someone has marked
+active in wl.works; and **wl.works' dataset builder (Plan 24) is what assigns files to datasets**,
+by task, by the stimulus settings of the conditions that actually ran, by the experimenter's
+notes, and later by areas, probes and processing results.
+
+**This repository's half is built.** None of it needs wl.works to reach this host; wl.works opens
+every connection, as always ([`docs/ops/lab-host-protocol.md`](ops/lab-host-protocol.md)):
+
+- **`GET /nwb?since=<cursor>`** lists every file whose record changed: its activation, status,
+  where it is now (host, share, path relative to the share, and `fast` or `slow`), and its
+  description. The cursor only increases.
+- **Each file's description** ([`docs/schemas/nwb_description.json`](schemas/nwb_description.json))
+  is also written beside the file as `<identifier>.json`, so wl.works need never open an NWB to
+  decide anything (its own Plan 20 §4.5 asks for exactly this). Per block it names the task, the
+  conditions that ran with their stimulus settings and trial counts, and per-system coverage;
+  per file, the subject, the data types present, the timing tier and the checksums.
+- **Checksums are `sha256`** of each written-once dataset's decoded contents, as Plan 24 §3.3 and
+  its item 1 settle.
+- **`PUT /nwb/active`** takes the whole set of activations that belong on the fast share, every
+  time ([`docs/schemas/active_set_request.json`](schemas/active_set_request.json)). This host
+  moves the files; the next `GET /nwb` shows where they went.
+
+**Still open on their side:**
+
+1. **The dataset builder's predicates reach into the description**: a condition's settings, the
+   data types, and later probes, areas and the processing summary. Plan 24's predicates today
+   "live on **blocks**" (its §1.1); the description's per-block entries are shaped to join onto
+   `animal_session_block` through `works_block_id`.
+2. **"Active" is a record with a person's name on it**, not a status column, per Plan 24 line 60
+   ("Status is derived, never stored").
+3. **wl.works computes which activations its active datasets match, and sends them** with
+   `PUT /nwb/active` whenever that set changes.
+4. **wl.works polls `GET /nwb`** to fill `analysis_activation`'s location triple (Plan 23
+   §10.1), instead of the owner of that row reading paths out of anywhere else.
+5. **The planner's planned experiments stay intent.** The requester's own caution, 2026-09-29:
+   planned sessions do not always follow through as planned, so datasets select on what was
+   recorded.
+
+---
+
 # OPEN — the activation-request payload gains the subject's details
 
 **Opened 2026-09-28** with the NWB builder
```

- [ ] **Step 2: wl-xcon's half.** Create `docs/pending-wl-xcon-amendments.md`:

```markdown
# Amendments to wl-xcon

**One is outstanding, opened 2026-09-29.** wl-xcon (formerly wl-expcontroller) is the rig's
experiment controller. What it asked of this repository is recorded in
[`HANDOVER-wl-expcontroller.md`](../HANDOVER-wl-expcontroller.md) and in its own
`docs/pending-wl-preproc-amendments.md`; this file is the other direction.

---

# OPEN — the stream must carry each trial's number and condition

**Opened 2026-09-29** while planning NWB publishing
([`specs/2026-09-29-nwb-publishing-design.md`](superpowers/specs/2026-09-29-nwb-publishing-design.md)
§11 item 1), and **January-critical beyond it.**

**What this repository needs.** It identifies a trial by the stream's `TRIAL_NUMBER` escape
(`0x8001`, a uint32) and nothing else: `events/assemble.py` says "The ID arrives here, never from
a running count. A TRIAL_START whose payload was lost therefore yields NO trial rather than a
misnumbered one." wl-xcon's own allocation rule says the same thing from the other side: "Event
codes carry identity and timing. The session record carries content." (its S2
event-vocabulary design), and its list of what must survive without the session files includes
"trial number, condition".

**What wl-xcon does today, read 2026-09-29 at `88e69ac`.** Its codec knows both escapes
(`wl_xcon/encode.py`: `_UINT32_ESCAPES = (0x8001, 0x8003)  # TRIAL_NUMBER, CONDITION`), but
nothing calls `words_for`: no module in `wl_xcon/` emits either escape, and no backlog item tracks
it (XC-008 covers `PARAM_CHANGE` alone). **So a real rig session today would give this
repository no trials at all**, and nothing could join the rig's own per-trial record to them.

**The ask:**

1. **Emit `TRIAL_NUMBER` at the start of every trial**, its value equal to the `index` the same
   trial is recorded under in `xcon/trials.jsonl` (`Recorder.trial`). That equality is the join:
   this repository reads each trial's condition and stimulus settings from that record by it.
2. **Emit `CONDITION` inside every trial**, a number for the condition it ran under, and
   **record that number in `trials.jsonl`** beside the condition's name, so the name and the
   stream's number can be tied without a table kept anywhere else.

**What this repository does meanwhile.** It joins by trial number only; a trial the record does
not name exactly once gets no condition and no settings, and the file's description says why
(`nwb/conditions.py`). Nothing is guessed from trial order.
```

- [ ] **Step 3: Full suite on both interpreters, once.**
  - Set `WLPP_BMD_REFERENCE=~/.cache/wl-preproc-references/bmd/BMD`, `WLPP_BMD_BOOST_INCLUDE=~/.cache/wl-preproc-references/bmd/boost_1_86_0` and `WLPP_NSLR_REFERENCE=~/.cache/wl-preproc-references`.
  - Leave `WLPP_OHDPI_REFERENCE` unset, as CI has it.
  - Run `.venv/bin/python -m pytest -q -p no:cacheprovider` and `~/.cache/wl-preproc-venv313/bin/python -m pytest -q -p no:cacheprovider`.

  Expected: both green. Measured with every task applied: **1886 passed, 31 skipped, 1 deselected, 1 xfailed** on 3.11, and **1885 passed, 33 skipped, 1 xfailed** on 3.13, 0 failed. `main` at `85ea88d` gives 1829 and 1828 locally; this plan adds 57 tests.

- [ ] **Step 4: The handoff.** Create `docs/handoffs/2026-09-29-nwb-publishing.md` with these sections:
  - **What was built.** Piece 2a, on branch `spec/nwb-publishing`. Name the spec and this plan, and cover:
    - the description and where it lives;
    - the conditions from the rig's record;
    - publishing, placement and the two endpoints;
    - the real `canonical_nwb_present`.
  - **How to turn it on.**
    - The daemon options, and the fixed `nwb/` folder on each share.
    - Every file is `invalid` until wl.works sends the subject's date of birth (piece 1).
    - `invalid` files are never published.
  - **What wl.works and wl-xcon must do.** The two OPEN entries.
  - **The January-critical finding.** A real rig session gives no trials today, because wl-xcon emits no `TRIAL_NUMBER`.
  - **Still not done.** Piece 2b, the canonical lifecycle:
    - the automatic 12-hour canonical;
    - re-firing;
    - superseding;
    - rebuilding `invalid` rows once the date of birth arrives.
  - **The rulings.** Every ruling this plan made ("Rulings made while planning"), plus any made while executing.
  - **The measured counts.** Each task's before and after runs, the mutation checks, and Step 3's two full-suite runs.

- [ ] **Step 5: CHECKPOINT.** In "Start here", item 4 ends with the NWB builder paragraph ("**The NWB builder is BUILT on `spec/nwb-builder` …**"). After it, add one bold-led paragraph, as the paragraphs before it do. It says:
  - NWB publishing (piece 2a) is built on `spec/nwb-publishing`, NOT merged as written;
  - files are published to the slow share, moved to the fast share by `PUT /nwb/active`, and listed by `GET /nwb`;
  - `canonical_nwb_present` is now real;
  - **the rig emits no `TRIAL_NUMBER`, so a real session gives no trials: the OPEN entry in `pending-wl-xcon-amendments.md`**;
  - the handoff's path.

  Do not renumber. Do not re-point the header; that happens at merge, with CI read.

- [ ] **Step 6: `wl.yaml`.** Add one sentence to `status.phase`, after the NWB builder's: NWB publishing (piece 2a) is built on `spec/nwb-publishing`, not yet merged, with the spec and handoff paths. Then run `.venv/bin/wl-check` **on its own** and read its exit status.

- [ ] **Step 7: Commit**

```bash
git add docs/pending-wl-works-amendments.md docs/pending-wl-xcon-amendments.md docs/CHECKPOINT.md docs/handoffs/2026-09-29-nwb-publishing.md wl.yaml
git commit -m "docs: NWB publishing built -- what wl.works and wl-xcon must do, and the rig's missing trial numbers

<trailer lines>"
```

---

## Rulings made while planning

Each is recorded where the code it governs is, and in the spec where it amends the spec.

1. **The description names the superseded activation `supersedes_activation_id`,** not `supersedes`. A guardrail forbids any `"supersedes":` key in the source; a clearer name beats weakening it.
2. **`identity.pipeline` is the name and the commit only.** Reading the package version needs `importlib`, which a guardrail bans, and the version has never moved. Cost if wrong: an installed deployment that is not a checkout records no commit.
3. **Why a trial's condition or settings are unknown is one file-level `notes` list,** because the rig's record is joined for the whole file.
4. **The conditions table's name column is `condition`.** A `DynamicTable`'s own `name` attribute shadows a column called `name`; this was measured.
5. **Files live under a fixed `nwb/` folder on each share's mount.** The share-relative path is then computable from the mount alone.
6. **The change cursor is its own table, `NwbChange`** (`auto_increment`), and `NwbPlacement` is keyed on it. Both were measured working on DataJoint 2.3.
7. **Publishing never writes over a file no placement records.** Found while proving the code: a deleted-and-rebuilt row would otherwise have replaced the lab's annotations. Cost if wrong: a person removes the stale file before rebuilding.
8. **A move whose old copy could not be deleted is finished on the next pass,** and a missing published file is reported by path. These are Review Focus 1 and 5.
9. **Test rows with no file behind them are removed by the tests that make them:** the blob guardrail's rows in `ActiveSet` and `NwbFile` (Task 5), and the reclaim test's `NwbFile` rows (Task 8). The NWB stages read the suite's whole shared database, so leftovers act as live state. Measured: eight later tests failed on the guardrail's rows in the first full suite, and one on the reclaim test's rows in the second.
10. **A file in the active set publishes straight to the fast share only if the fast share has room;** otherwise it publishes to the slow share, and placement moves it later.
11. **wl-xcon emits no `TRIAL_NUMBER` or `CONDITION` today** (read at its `88e69ac`). So this piece is built and tested against the synthetic session, which does emit them, and the request to wl-xcon is Task 9's OPEN entry. Cost if wrong: none to this code. A real session gives no trials until wl-xcon emits them.
