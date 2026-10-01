# The Landed-Session Listing (Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `GET /sessions?since=<cursor>` lists each landed session's measured runs, the blocks inside them, the SpikeGLX segments and probes it recorded, and its flags, so wl.works can make a session's runs from them and fire its canonical NWB.

**Architecture:**
- **The event stage keeps what the listing needs from wl-xcon's record,** once, before the raw files are archived: each run's task and stop reason (`core.RunRecord`), and each block's closure and block type (`trial.Block.Attribute`).
- **The probe census keeps each segment's `~imroTbl` verbatim,** for wl.works to compare with a planned IMRO file.
- **One session's entry is gathered from the database, then built without it** (`listing/entry.py`), so every flag is tested on rows made by hand.
- **A daemon stage logs a change whenever an entry's digest changes** (`ingest.SessionChange`), as `GET /nwb`'s log does. It is the log's one writer, under its own named lock.
- **`GET /sessions` reads the log and assembles each changed session as it stands now.**

**Tech Stack:** Python ≥3.11; DataJoint 2.3 with MySQL; element-event (adopted, pinned); pydantic 2 (the contract); probeinterface 0.3.2 (the census); the stdlib `http.server` responder.

**Spec:** `docs/superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md` (`89ad4e9`, amended `03c6784` for wl.works' site-set note). It is binding. The requester approved it on 2026-10-01 (*"Approved, write Plan A"*). This plan is its §10's Plan A; Plan B, requests that name runs, follows. Task 7 adds the spec's dated amendments, which record the rulings below.

**Every piece of code below was proven before this plan was written.** It was built in a scratch worktree on a branch of its own, one commit per task.
- **Each task's failing run** was measured on the previous task's code plus this task's tests.
- **Its passing runs** were measured on its own commit.
- **Every mutation check named here** was run against the final tree, and each failed its test.
- **The full suite** was run on both interpreters with every task applied (Task 7 quotes it).

## Global Constraints

- **The spec is binding,** including the dated amendments Task 7 adds. Where it and this plan disagree, the spec wins; record a ruling.
- **The requester's decisions (spec §0):**
  - a canonical file keeps every run of its montage;
  - a repeated run number lists its first run and flags the session (*"run numbers repeat; the restarted runs wait for XC-026"*);
  - a derivative selects whole runs.
- **The listing is read-only.** Its responder never writes; the daemon's listing stage is `ingest.SessionChange`'s one writer.
- **Nothing is guessed.** A rig record that is missing, unreadable, or names two block types for one block leaves the field empty and raises a flag.
- **A value that cannot be stored is cut or named, never a failed stage:** a stage that fails on a landed file fails on every pass.
- **element-event's tables are adopted, not changed.** `trial.Block.Attribute` takes new rows; no definition changes.
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code, data or tables.
- **Tests.**
  - Run with `.venv/bin/python -m pytest <files> -q -p no:cacheprovider` from the repository root. In a git worktree, prefix `PYTHONPATH=$PWD`.
  - Database tests need Docker (OrbStack on this machine: `open -a OrbStack` after a reboot). A module-wide setup error on a 120 s container timeout is the known flake: re-run it.
  - Mutation checks:
    - clear `__pycache__` first, and again after restoring;
    - run with `PYTHONDONTWRITEBYTECODE=1`;
    - make one mutation at a time, and restore the file afterwards.
  - **Run each task's own test files.** The full suite runs once, on both interpreters, in Task 7.
  - The shell is zsh:
    - an unquoted `$VAR` holding several arguments is not split;
    - arrays start at 1;
    - a pipe through `tail` or `grep` hides the test command's exit status, so gate on the command's own status.
- **`wl-check` after `wl.yaml` changes,** on its own (Task 7).
- **`wlpp schemas export` after a wire contract changes** (Task 6); CI diffs `docs/schemas`.
- **Every commit message ends with:**

  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R
  ```

## Review Focus

Five inputs the spec implies but its own testing section (§9) does not name, most likely first. Each is pinned by a test named here, in the task that owns the code.

1. **A block in no measured run, in a session that has runs**: its run's `RUN_START` was lost. A reasonable person expects to be told, not to find the block missing. Task 4: `test_a_block_in_no_run_is_flagged_when_the_session_has_runs`, with the new flag `block_outside_runs`.
2. **A session listed before its timing and probe census are computed**: listed now, with `tier` null and no segments, and again when they arrive. Task 4: `test_a_session_listed_before_its_timing_and_census_has_neither`; Task 5: `test_a_fact_that_arrives_later_lists_the_session_again`.
3. **A probe the census could not name** (no serial): listed in its segment with its problem, among no serials, never a bank change. Task 4: `test_a_probe_the_census_could_not_name_is_listed_with_its_problem`.
4. **A rig-record value longer than its column**: cut, so the session's event stage does not fail on every pass. Task 2: `test_a_rig_value_longer_than_its_column_is_cut_not_a_failed_session`.
5. **A reader whose cursor predates several changes of one session** sees that session once, as it stands now. Task 5: `test_a_fact_that_arrives_later_lists_the_session_again`, which lists from the earlier cursor.

## The spec's §8, verified at `03c6784` for Plan A

1. **`RUN_START`'s run number is `runs.jsonl`'s `run` + 1.** wl-xcon's session-levels spec (its branch `session-levels-design`) calls `run` the 0-based run in session, and gives start rows `run_in_session`, which the reader uses when present.
2. **`trial.Block.Attribute` takes the new rows**: `attribute_name : varchar(32)`, `attribute_value : varchar(2000)` (element-event's `trial.py`), as `populate_session` already writes `task_type`.
5. **The rig's session name is the last part of `ingest.Ingestion.session_dir`** (`landing.py` stores the session folder), which for a generated session is its `session_id`.
6. **Two named locks cannot deadlock:** `nwb/lock.py` takes `GET_LOCK(name, 0)` and never waits.
8. **probeinterface's contact order is SpikeGLX's electrode number** for NP1015, NP1022, NP1030 and NP1032. Measured with probeinterface 0.3.2: each model's contacts are `e0`, `e1`, … in order, and channel 0 in bank 2 of an `~imroTbl` maps to `e768`, bank × 384 + channel.

**Found while verifying:** `trial.Block` records no closure, so Task 2 stores it, as `trial.Block.Attribute` row `closed`.

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/events/rigruns.py` (new) | read wl-xcon's `xcon/runs.jsonl` (Task 1) |
| `wl_preproc/synth/peripherals.py`, `synth/session.py` | write the run record and `config.json` when runs are on (Task 1) |
| `wl_preproc/schema/core.py`, `schema/events.py` | `core.RunRecord`; the event stage keeps it, and each block's closure and type (Task 2) |
| `wl_preproc/ephys/spikeglx_probes.py`, `schema/ephys.py` | the census keeps each segment's `~imroTbl` (Task 3) |
| `wl_preproc/contracts/protocol.py` | `SessionEntry`, `SessionListing` and their parts (Task 4) |
| `wl_preproc/listing/entry.py` (new) | one session's entry: gathered, then built (Task 4) |
| `wl_preproc/schema/ingest.py`, `listing/stage.py` (new), `nwb/lock.py`, `daemon.py` | the change log and the stage that writes it (Task 5) |
| `wl_preproc/responder/sessions.py` (new), `responder/handler.py`, `responder/server.py`, `cli/main.py` | `GET /sessions` and its exported schema (Task 6) |
| docs, `wl.yaml` | the protocol, the records, the amendments (Tasks 6 and 7) |

---

### Task 1: wl-xcon's run record is read

**Files:**
- Create: `wl_preproc/events/rigruns.py`, `tests/events/test_rigruns.py`
- Modify: `wl_preproc/synth/peripherals.py`, `wl_preproc/synth/session.py`
- Test: `tests/events/test_rigruns.py`, `tests/synth/test_peripherals.py`

**Interfaces — produces:**
- `events.rigruns.RigRun(number: int, task: str | None, stopped_because: str | None, stop_kind: str | None)`; `RigRuns(runs: tuple[RigRun, ...], problems: tuple[str, ...])`.
- `events.rigruns.read_rig_runs(session_dir: Path, subject: str) -> RigRuns | None`: `None` when there is no `xcon/runs.jsonl`.
- `synth.peripherals.write_rig_runs(xcon_dir: Path, recipe, truth)`, called by `generate_session` when `recipe.runs`.

**Why these fields.** wl-xcon's `record.py::run_row` (its `main`, read at `0d00a6c`) writes `{"event": "start" | "end", "run": <0-based>, ...}`; `taskd.py` passes the start row `task` and the end row `stopped_because` and `stop_kind`. A run that faults writes no end row. The rows carry no subject, so the session's `config.json` (written as wl-xcon's session opens) stands in.

- [ ] **Step 1: Write the failing tests.** Create the first file and apply the diff:

```python
"""The rig's own run record, `xcon/runs.jsonl` (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 2.3)."""

from __future__ import annotations

import json


def _record(tmp_path, rows, config=None):
    (tmp_path / "xcon").mkdir()
    (tmp_path / "xcon" / "runs.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    if config is not None:
        (tmp_path / "xcon" / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return tmp_path


def _start(run, task, **fields):
    return json.dumps({"event": "start", "run": run, "at": 0.0, "task": task, **fields})


def _end(run, stopped_because, stop_kind):
    return json.dumps({"event": "end", "run": run, "at": 1.0, "stopped_because": stopped_because,
                       "stop_kind": stop_kind})


def test_a_run_is_its_start_row_and_its_end_row(tmp_path):
    """wl-xcon's `record.py::run_row`: the start row names the task, the end
    row why the run stopped. Before `run_in_session`, a run's number is its
    0-based `run` plus one."""
    from wl_preproc.events.rigruns import RigRun, read_rig_runs

    rows = [_start(0, "fixation_detection"), _end(0, "every block is finished", "completed"),
            _start(1, "rf_map"), _end(1, "stopped by jw", "operator")]
    record = read_rig_runs(_record(tmp_path, rows), "pico")
    assert record.runs == (RigRun(1, "fixation_detection", "every block is finished", "completed"),
                           RigRun(2, "rf_map", "stopped by jw", "operator"))
    assert record.problems == ()


def test_run_in_session_is_the_number_when_the_start_row_carries_it(tmp_path):
    """wl-xcon's session-levels change adds `run_in_session` to the start row
    only; its end row is matched to it through the shared `run`."""
    from wl_preproc.events.rigruns import read_rig_runs

    rows = [_start(0, "rf_map", run_in_session=7), _end(0, "every block is finished", "completed")]
    (run,) = read_rig_runs(_record(tmp_path, rows), "pico").runs
    assert (run.number, run.task, run.stop_kind) == (7, "rf_map", "completed")


def test_a_run_that_faulted_has_no_stop_reason(tmp_path):
    from wl_preproc.events.rigruns import read_rig_runs

    (run,) = read_rig_runs(_record(tmp_path, [_start(0, "rf_map")]), "pico").runs
    assert (run.stopped_because, run.stop_kind) == (None, None)


def test_a_row_it_cannot_read_is_reported_by_line_and_left_out(tmp_path):
    from wl_preproc.events.rigruns import read_rig_runs

    rows = [_start(0, "rf_map"), "{not json", json.dumps({"event": "start"}), json.dumps({"event": "pause", "run": 0})]
    record = read_rig_runs(_record(tmp_path, rows), "pico")
    assert [run.number for run in record.runs] == [1]
    assert [problem.split(":")[0] for problem in record.problems] == ["line 2", "line 3", "line 4"]


def test_a_record_of_another_animal_is_not_read(tmp_path):
    """Its rows carry no subject; its `config.json` does."""
    from wl_preproc.events.rigruns import read_rig_runs

    record = read_rig_runs(_record(tmp_path, [_start(0, "rf_map")], config={"subject": "other"}), "pico")
    assert record.runs == ()
    assert len(record.problems) == 1 and "'other'" in record.problems[0]


def test_a_session_without_a_record_has_none(tmp_path):
    from wl_preproc.events.rigruns import read_rig_runs

    assert read_rig_runs(tmp_path, "pico") is None
```

```diff
--- a/tests/synth/test_peripherals.py
+++ b/tests/synth/test_peripherals.py
@@ -151,3 +151,28 @@ def test_the_rig_record_has_wl_xcons_line_shape(tmp_path):
     assert record.problems == ()
     assert [trial.number for trial in record.trials] == [trial.trial_id for trial in truth.trials]
     assert {trial.condition for trial in record.trials} <= {"contrast-10", "contrast-25", "contrast-50", "contrast-100"}
+
+
+def test_the_run_record_has_wl_xcons_row_shape(tmp_path):
+    """With runs on, one start row per run and an end row unless its block is
+    unclosed, as wl-xcon's `run_row` writes them (design spec
+    `2026-10-01-session-listing-and-run-requests-design.md` section 2.3)."""
+    from wl_preproc.events.rigruns import read_rig_runs
+    from wl_preproc.synth.peripherals import write_rig_runs
+    from wl_preproc.synth.recipe import SessionRecipe
+
+    recipe = SessionRecipe.model_validate({**CI_RECIPE.model_dump(), "runs": True, "unclosed_blocks": [1]})
+    (tmp_path / "xcon").mkdir()
+    write_rig_runs(tmp_path / "xcon", recipe, build_timeline(recipe))
+    record = read_rig_runs(tmp_path, recipe.subject)
+    assert record.problems == ()
+    assert [(run.number, run.task, run.stop_kind) for run in record.runs] == [
+        (1, recipe.blocks[0].task_type.name.lower(), None),
+        (2, recipe.blocks[1].task_type.name.lower(), "completed")]
+
+
+def test_a_session_without_runs_has_no_run_record(tmp_path):
+    from wl_preproc.synth.session import generate_session
+
+    generate_session(tmp_path, CI_RECIPE)
+    assert not list(tmp_path.rglob("runs.jsonl")) and not list(tmp_path.rglob("config.json"))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/events/test_rigruns.py tests/synth/test_peripherals.py -q --tb=line -p no:cacheprovider`
Expected: 7 failed, 11 passed. `wl_preproc.events.rigruns` does not exist, so the six reader tests and the generator's run-record test fail on importing it. `test_a_session_without_runs_has_no_run_record` passes, since nothing writes a run record yet.

- [ ] **Step 3: Implement.** Create the reader and apply the diffs:

```python
"""The rig's own record of every run: wl-xcon's `xcon/runs.jsonl` (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 2.3).

**The recording measures a run; the rig's record names it.** `core.Run` is
measured from the run's `RUN_START` and `RUN_END`; the task that ran and why it
stopped are in wl-xcon's record. `wl_xcon/record.py::run_row` writes one row
per event, `{"event": "start" | "end", "run": <0-based run in session>, ...}`:
the start row carries the `task`, the end row `stopped_because` and
`stop_kind`. Since wl-xcon's session-levels change a start row also carries
`run_in_session`, the number `RUN_START` strobes; before it, that number is
`run` + 1. A start row and its end row share `run`.

**Read, never repaired.** A row that is not a JSON object naming its event and
run is reported by line and left out. A run that faulted wrote no end row, so
its stop reason is absent, not guessed.

**One record is one wl-xcon session, which is one animal** (wl-xcon's own
vocabulary). Its rows carry no subject, so the session's `config.json` is
checked instead: a record whose `config.json` names another subject is not
read at all.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from wl_preproc.contracts.paths import XCON_DIRNAME

RECORD_NAME = "runs.jsonl"
CONFIG_NAME = "config.json"


@dataclasses.dataclass(frozen=True, slots=True)
class RigRun:
    number: int  # the run's number in the session, as RUN_START strobes it
    task: str | None
    stopped_because: str | None
    stop_kind: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class RigRuns:
    runs: tuple[RigRun, ...]
    problems: tuple[str, ...]


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def read_rig_runs(session_dir: Path, subject: str) -> RigRuns | None:
    """This session's runs from `<session>/xcon/runs.jsonl`, or `None` when
    the session has no such record."""
    xcon = Path(session_dir) / XCON_DIRNAME
    path = xcon / RECORD_NAME
    if not path.is_file():
        return None
    try:
        recorded = json.loads((xcon / CONFIG_NAME).read_text(encoding="utf-8")).get("subject")
    except (OSError, ValueError, AttributeError):
        recorded = None
    if recorded is not None and recorded != subject:
        return RigRuns(runs=(), problems=(f"{CONFIG_NAME} names subject {recorded!r}, not {subject!r}, so "
                                          "its runs are not this session's",))
    fields: dict[int, dict] = {}
    problems = []
    for number_of_line, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {number_of_line}: not JSON")
            continue
        if not isinstance(row, dict) or row.get("event") not in ("start", "end") or not _integer(row.get("run")):
            problems.append(f"line {number_of_line}: not a start or end row naming its run")
            continue
        # A start row and its end row share `run`; only the start row is
        # sure to carry `run_in_session`, so the number is taken from it.
        run = fields.setdefault(row["run"], {"number": row["run"] + 1})
        if row["event"] == "start":
            run["task"] = _text(row.get("task"))
            if _integer(row.get("run_in_session")):
                run["number"] = row["run_in_session"]
        else:
            run["stopped_because"] = _text(row.get("stopped_because"))
            run["stop_kind"] = _text(row.get("stop_kind"))
    runs = tuple(sorted((RigRun(number=run["number"], task=run.get("task"),
                                stopped_because=run.get("stopped_because"), stop_kind=run.get("stop_kind"))
                         for run in fields.values()), key=lambda run: run.number))
    return RigRuns(runs=runs, problems=tuple(problems))
```

```diff
--- a/wl_preproc/synth/peripherals.py
+++ b/wl_preproc/synth/peripherals.py
@@ -192,3 +192,26 @@ def write_rig_trials(path: Path, recipe: SessionRecipe, truth: GroundTruth) -> N
     if Fault.MISMATCHED_RIG_LINE in recipe.faults:
         lines = mismatch_rig_line(lines)
     path.write_text("\n".join(json.dumps(line, sort_keys=True) for line in lines) + "\n", encoding="utf-8")
+
+
+def write_rig_runs(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth) -> None:
+    """Stands in for wl-xcon's `xcon/runs.jsonl` and `xcon/config.json`, for a
+    recipe whose blocks are wrapped in runs (design spec
+    `2026-10-01-session-listing-and-run-requests-design.md` section 2.3).
+
+    In the shape `wl_xcon/record.py::run_row` writes on wl-xcon's `main`
+    (read at `0d00a6c`): a start row with the task, and an end row with
+    `stopped_because` and `stop_kind`, each naming its 0-based `run`. A run
+    whose block is unclosed faulted, and a run that faults writes no end row.
+    The values are `taskd.py`'s for a run that finished its blocks."""
+    rows = []
+    for run_index, block in enumerate(truth.blocks):
+        spec = recipe.blocks[run_index]
+        rows.append({"event": "start", "run": run_index, "at": block.start_s, "task": spec.task_type.name.lower()})
+        if run_index + 1 not in recipe.unclosed_blocks:
+            rows.append({"event": "end", "run": run_index, "at": block.end_s,
+                         "stopped_because": "every block is finished", "stop_kind": "completed"})
+    (xcon_dir / "runs.jsonl").write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
+                                         encoding="utf-8")
+    (xcon_dir / "config.json").write_text(json.dumps({"session": recipe.session_id, "subject": recipe.subject},
+                                                     sort_keys=True), encoding="utf-8")
```

```diff
--- a/wl_preproc/synth/session.py
+++ b/wl_preproc/synth/session.py
@@ -15,6 +15,7 @@ from wl_preproc.synth.peripherals import (
     camera_frame_count,
     write_camera_sidecar,
     write_manifest,
+    write_rig_runs,
     write_rig_trials,
     write_task_file,
 )
@@ -63,6 +64,8 @@ def generate_session(root: Path, recipe: SessionRecipe) -> GroundTruth:
     # its folder beside them).
     layout.xcon_dir.mkdir(exist_ok=True)
     write_rig_trials(layout.xcon_dir / "trials.jsonl", recipe, truth)
+    if recipe.runs:
+        write_rig_runs(layout.xcon_dir, recipe, truth)
 
     rng = np.random.default_rng(recipe.seed + 2)
     finished_at = SYNTH_EPOCH + datetime.timedelta(seconds=recipe.duration_s)
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 18 passed.

Then everything the generator feeds: `.venv/bin/python -m pytest tests/events tests/synth -q -p no:cacheprovider`. Expected: 224 passed.

- [ ] **Step 5: Mutation checks.** Each was measured to fail the tests named in brackets.
  - T1a (`rigruns.py`): `if _integer(row.get("run_in_session")):` becomes `if False:` [`test_run_in_session_is_the_number_when_the_start_row_carries_it`].
  - T1b: `if recorded is not None and recorded != subject:` becomes `if False:` [`test_a_record_of_another_animal_is_not_read`].
  - T1c (`peripherals.py`): `if run_index + 1 not in recipe.unclosed_blocks:` becomes `if True:` [`test_the_run_record_has_wl_xcons_row_shape`].
  - T1d (`session.py`): `if recipe.runs:` becomes `if True:` [`test_a_session_without_runs_has_no_run_record`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/events/rigruns.py wl_preproc/synth/peripherals.py wl_preproc/synth/session.py tests/events/test_rigruns.py tests/synth/test_peripherals.py
git commit -m "feat(events): wl-xcon's run record is read -- each run's task from its start row and its stop reason from its end row, numbered by run_in_session or run + 1; the generator writes it when its blocks are wrapped in runs

<trailer lines>"
```

---

### Task 2: The event stage keeps the rig's record of each run, and each block's facts

**Files:**
- Modify: `wl_preproc/schema/core.py`, `wl_preproc/schema/events.py`
- Test: `tests/schema/test_events.py`

**Interfaces — consumes:** Task 1's `read_rig_runs`; `events.rigtrials.read_rig_trials` (piece 1).

**Interfaces — produces:**
- `core.RunRecord`: `-> core.Run`; `task = null : varchar(255)`, `stopped_because = null : varchar(1024)`, `stop_kind = null : varchar(64)`. One row per measured run the rig's record names.
- `trial.Block.Attribute` rows `closed` (`"1"` or `"0"`) and `block_type` (the `block` every joined trial line names), per block.

**Why the block type is joined by trial number.** `BLOCK_START` carries no type name; the rig record's `block` field does, on every trial line, joined by `trial_number` as conditions are. A block whose lines name two types, or none, gets no type, and Task 4 flags it.

- [ ] **Step 1: Write the failing tests.** Apply this diff:

```diff
--- a/tests/schema/test_events.py
+++ b/tests/schema/test_events.py
@@ -597,3 +597,82 @@ def test_a_faulted_trial_stops_inside_its_own_run_when_runs_are_marked():
     stop = _trial_stop_time(faulted, 1, assembly.trials,
                             _containing_block(faulted, assembly.blocks, stream_end_s), stream_end_s)
     assert stop <= _block_stop_time(block) <= run.last_s < assembly.runs[1].start_s
+
+
+# -- What the event stage keeps from wl-xcon's record (design spec
+# `2026-10-01-session-listing-and-run-requests-design.md` section 2.3).
+
+
+def _populate_generated(tmp_path, subject, when, mutate=None, **update):
+    from wl_preproc.schema import pipeline
+    from wl_preproc.synth.recipe import CI_RECIPE, SessionRecipe
+    from wl_preproc.synth.session import generate_session
+
+    recipe = SessionRecipe.model_validate({**CI_RECIPE.model_dump(), "subject": subject, **update})
+    generate_session(tmp_path, recipe)
+    if mutate is not None:
+        mutate(tmp_path / recipe.session_id)
+    pipeline.lab.Lab.insert1({"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
+                             skip_duplicates=True)
+    pipeline.subject.Subject.insert1({"subject": subject, "sex": "M", "subject_birth_date": datetime.date(2020, 1, 1),
+                                      "subject_description": ""}, skip_duplicates=True)
+    key = {"subject": subject, "session_datetime": when}
+    pipeline.Session.insert1(key, skip_duplicates=True)
+    events.populate_session(key, tmp_path / recipe.session_id)
+    return recipe, key
+
+
+def _block_attributes(key, name):
+    from wl_preproc.schema import pipeline
+
+    rows = (pipeline.trial.Block.Attribute & key & {"attribute_name": name}).to_dicts()
+    return {row["block_id"]: row["attribute_value"] for row in rows}
+
+
+def test_populate_session_keeps_the_rig_record_of_each_run_and_each_blocks_facts(events_activated, dj_conn,
+                                                                                  tmp_path):
+    """Run 1 faulted (its block unclosed), so wl-xcon wrote it no end row; run
+    2 finished. Each block keeps whether it closed and the block type its
+    trials' lines name."""
+    from wl_preproc.schema import core
+
+    recipe, key = _populate_generated(tmp_path, "slrig1", datetime.datetime(2027, 7, 20, 9, 0),
+                                      runs=True, unclosed_blocks=[1])
+    records = (core.RunRecord & key).to_dicts(order_by="run_number")
+    assert [(row["run_number"], row["task"], row["stopped_because"], row["stop_kind"]) for row in records] == [
+        (1, recipe.blocks[0].task_type.name.lower(), None, None),
+        (2, recipe.blocks[1].task_type.name.lower(), "every block is finished", "completed")]
+    assert _block_attributes(key, "closed") == {1: "0", 2: "1"}
+    assert _block_attributes(key, "block_type") == {1: "block-1", 2: "block-2"}
+
+
+def test_a_block_whose_trials_name_two_types_has_none(events_activated, dj_conn, tmp_path):
+    """Nothing is guessed: block 1's second trial's line names another type."""
+    import json
+
+    def rename_one(session_dir):
+        path = session_dir / "xcon" / "trials.jsonl"
+        lines = [json.loads(line) for line in path.read_text().splitlines()]
+        lines[1]["block"] = "block-x"
+        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
+
+    _recipe, key = _populate_generated(tmp_path, "slrig2", datetime.datetime(2027, 7, 21, 9, 0), mutate=rename_one)
+    assert _block_attributes(key, "block_type") == {2: "block-2"}
+
+
+def test_a_rig_value_longer_than_its_column_is_cut_not_a_failed_session(events_activated, dj_conn, tmp_path):
+    """A value too long for its column would fail the session's whole event
+    stage on every pass, so it is cut to the column."""
+    import json
+
+    from wl_preproc.schema import core
+
+    def long_task(session_dir):
+        path = session_dir / "xcon" / "runs.jsonl"
+        rows = [json.loads(line) for line in path.read_text().splitlines()]
+        rows[0]["task"] = "t" * 300
+        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
+
+    _recipe, key = _populate_generated(tmp_path, "slrig3", datetime.datetime(2027, 7, 23, 9, 0), mutate=long_task,
+                                       runs=True)
+    assert (core.RunRecord & key & {"run_number": 1}).fetch1("task") == "t" * 255
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_events.py -q --tb=line -p no:cacheprovider`
Expected: 3 failed, 16 passed. `core` has no `RunRecord`, and no block carries a `closed` or `block_type` attribute.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/schema/core.py
+++ b/wl_preproc/schema/core.py
@@ -53,6 +53,24 @@ class Run(dj.Manual):
     """
 
 
+@schema
+class RunRecord(dj.Manual):
+    definition = """
+    # wl-xcon's own record of one run, from `xcon/runs.jsonl`, read by
+    # `schema/events.py::populate_session` (design spec
+    # 2026-10-01-session-listing-and-run-requests-design.md section 2.3). The
+    # rig's word, kept apart from the measured Run, as an assertion is kept
+    # apart from a measurement throughout. Only a measured run has one. A
+    # value longer than its column is cut to it. Key: (subject,
+    # session_datetime, run_number).
+    -> Run
+    ---
+    task = null            : varchar(255)   # the start row's task
+    stopped_because = null : varchar(1024)  # the end row's; null when the run faulted
+    stop_kind = null       : varchar(64)    # the end row's, e.g. completed, operator, limit
+    """
+
+
 @schema
 class Block(dj.Manual):
     definition = """
```

```diff
--- a/wl_preproc/schema/events.py
+++ b/wl_preproc/schema/events.py
@@ -53,6 +53,8 @@ from wl_preproc.contracts.events import (
 )
 from wl_preproc.events.assemble import AssembledBlock, AssembledTrial, assemble
 from wl_preproc.events.extract import extract_syncbox_words
+from wl_preproc.events.rigruns import read_rig_runs
+from wl_preproc.events.rigtrials import read_rig_trials
 from wl_preproc.schema import DEFAULT_PREFIX, core, pipeline
 from wl_preproc.timebase import segments
 from wl_preproc.timebase.fit import RateFit, fit_offset
@@ -344,6 +346,36 @@ def _block_stop_time(block: AssembledBlock) -> float:
 TRIAL_ID_MAX = 32767
 
 
+def _clip(value: str | None, width: int) -> str | None:
+    """A rig-record value cut to its column: a value too long would fail the
+    session's whole event stage on every pass."""
+    return None if value is None else value[:width]
+
+
+def _block_type_rows(session_key: dict, block_trial_rows: list[dict], record) -> list[dict]:
+    """Each block's type: the `block` its trials' rig-record lines name, when
+    they name one (design spec
+    `2026-10-01-session-listing-and-run-requests-design.md` section 2.3).
+    Joined by trial number, as conditions are. A block none of whose trials
+    has a line, or whose lines name two types, has none."""
+    if record is None:
+        return []
+    by_number: dict[int, object] = {}
+    for trial in record.trials:
+        by_number.setdefault(trial.number, trial)
+    names: dict[int, set[str]] = {}
+    for row in block_trial_rows:
+        trial = by_number.get(row["trial_id"])
+        if trial is not None:
+            names.setdefault(row["block_id"], set()).add(trial.block)
+    return [
+        {**session_key, "block_id": block_id, "attribute_name": "block_type",
+         "attribute_value": _clip(next(iter(found)), 2000)}
+        for block_id, found in sorted(names.items())
+        if len(found) == 1
+    ]
+
+
 def populate_session(key: dict, session_dir: Path) -> None:
     """Populate one session's `BehaviorRecording`, `EventType`, `Event`,
     `Trial`, `TrialType`, `Block` and `BlockTrial` from the sync box's decoded
@@ -556,6 +588,17 @@ def populate_session(key: dict, session_dir: Path) -> None:
         }
         for block in assembly.blocks
     ]
+    # Whether each block closed, and its block type from the rig's record
+    # (design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    # section 2.3): trial.Block holds neither, and the session listing
+    # reports both.
+    block_attribute_rows += [
+        {**session_key, "block_id": block.block_id, "attribute_name": "closed",
+         "attribute_value": str(int(block.end_s is not None))}
+        for block in assembly.blocks
+    ]
+    block_attribute_rows += _block_type_rows(session_key, block_trial_rows,
+                                             read_rig_trials(session_dir, session_key["subject"]))
     if block_attribute_rows:
         pipeline.trial.Block.Attribute.insert(block_attribute_rows, skip_duplicates=True)
 
@@ -574,3 +617,17 @@ def populate_session(key: dict, session_dir: Path) -> None:
     ]
     if run_rows:
         core.Run.insert(run_rows, skip_duplicates=True)
+
+    # -- core.RunRecord: the rig's record of each measured run (design spec
+    # `2026-10-01-session-listing-and-run-requests-design.md` section 2.3).
+    # Read here, once, because the raw files are later archived.
+    rig_runs = read_rig_runs(session_dir, session_key["subject"])
+    measured = {row["run_number"] for row in run_rows}
+    record_rows = [
+        {**session_key, "run_number": run.number, "task": _clip(run.task, 255),
+         "stopped_because": _clip(run.stopped_because, 1024), "stop_kind": _clip(run.stop_kind, 64)}
+        for run in (rig_runs.runs if rig_runs is not None else ())
+        if run.number in measured
+    ]
+    if record_rows:
+        core.RunRecord.insert(record_rows, skip_duplicates=True)
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 19 passed.

Then the tables beside it: `.venv/bin/python -m pytest tests/schema/test_events.py tests/schema/test_core.py tests/schema/test_nwb_trials.py -q -p no:cacheprovider`. Expected: 30 passed.

- [ ] **Step 5: Mutation checks.**
  - T2a (`schema/events.py`): `"task": _clip(run.task, 255)` becomes `"task": run.task` [`test_a_rig_value_longer_than_its_column_is_cut_not_a_failed_session`].
  - T2b: the `closed` attribute is always `"1"` [`test_populate_session_keeps_the_rig_record_of_each_run_and_each_blocks_facts`].
  - T2c: `if len(found) == 1` becomes `if found` [`test_a_block_whose_trials_name_two_types_has_none`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/core.py wl_preproc/schema/events.py tests/schema/test_events.py
git commit -m "feat(events): the event stage keeps wl-xcon's record of each run in core.RunRecord, and each block's closure and block type -- read once, before the raw files are archived

<trailer lines>"
```

---

### Task 3: The probe census keeps each segment's `~imroTbl`

**Files:**
- Modify: `wl_preproc/ephys/spikeglx_probes.py`, `wl_preproc/schema/ephys.py`
- Test: `tests/ephys/test_spikeglx_probes.py`, `tests/schema/test_probe_census.py`

**Interfaces — produces:**
- `RecordedProbe.imro_table: str | None = None`, the `.meta`'s `~imroTbl` verbatim.
- `ephys.ProbeCensus.Probe.imro_table = null : varchar(10240)`; `ephys.IMRO_TABLE_MAX = 10240`; `ephys.kept_imro_table(table) -> (table | None, problem | None)`.

**Why verbatim.** wl.works compares a planned IMRO file with the recorded table using the one reader it has for both (its `2026-10-01-montage-plan-design.md` §4 and §6, read on its `main` at `6a57b1cc`), so no electrode-numbering convention is shared across repositories. A Neuropixels table names one entry per channel, about 8,000 characters for 384.

**A development database redeclares `ephys.ProbeCensus`,** which gains a column. No real database exists yet.

- [ ] **Step 1: Write the failing tests.** Apply these diffs:

```diff
--- a/tests/ephys/test_spikeglx_probes.py
+++ b/tests/ephys/test_spikeglx_probes.py
@@ -129,3 +129,16 @@ def test_a_run_without_probes_has_none(tmp_path):
     for meta in nidq.parent.glob("*_imec0.*"):
         meta.unlink()
     assert read_run(nidq) == []
+
+
+def test_a_probe_keeps_its_imro_table_verbatim(tmp_path):
+    """wl.works compares a planned IMRO file with the recorded table using its
+    own reader (design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    section 2.2), so the table is kept exactly as the `.meta` has it."""
+    from wl_preproc.ephys.spikeglx_probes import read_run
+
+    _recipe, nidq = _run(tmp_path, probe_bank=1)
+    (meta,) = nidq.parent.glob("*_imec0.ap.meta")
+    (line,) = [line for line in meta.read_text().splitlines() if line.startswith("~imroTbl=")]
+    (probe,) = read_run(nidq)
+    assert probe.imro_table == line.removeprefix("~imroTbl=")
```

```diff
--- a/tests/schema/test_probe_census.py
+++ b/tests/schema/test_probe_census.py
@@ -154,3 +154,24 @@ def test_a_run_mixing_a_placed_and_an_unplaced_probe_records_both(landed):
     assert _electrodes(placed) == [0, 1, 2, 3]
     assert (unplaced["stream"], unplaced["part_number"], unplaced["electrode_config_hash"]) == (
         "imec1", "NP9999", None)
+
+
+def test_each_probe_keeps_its_segments_imro_table(landed):
+    """The bank change at the restart is in the two segments' own tables."""
+    _recipe, key = landed["two"]
+    tables = [part["imro_table"] for part in _parts(key)]
+    assert all(table and table.startswith("(") for table in tables)
+    assert tables[0] != tables[2] and tables[1] == tables[3]
+
+
+
+def test_an_imro_table_longer_than_kept_is_a_problem():
+    """A table longer than the column would fail the segment on every pass;
+    it is left out and said instead."""
+    from wl_preproc.schema.ephys import IMRO_TABLE_MAX, kept_imro_table
+
+    assert kept_imro_table("(0,384)(0 0 0 500 250 1)") == ("(0,384)(0 0 0 500 250 1)", None)
+    assert kept_imro_table(None) == (None, None)
+    table, problem = kept_imro_table("(" * (IMRO_TABLE_MAX + 1))
+    assert table is None and problem == (f"the imroTbl is {IMRO_TABLE_MAX + 1} characters, longer than the "
+                                         f"{IMRO_TABLE_MAX} kept, so it is not kept")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/ephys/test_spikeglx_probes.py tests/schema/test_probe_census.py -q --tb=line -p no:cacheprovider`
Expected: 3 failed, 18 passed. `RecordedProbe` has no `imro_table`, so neither the reader nor the census keeps one, and `ephys` has no `kept_imro_table`.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/ephys/spikeglx_probes.py
+++ b/wl_preproc/ephys/spikeglx_probes.py
@@ -35,6 +35,10 @@ class RecordedProbe:
     part_number: str | None
     electrodes: tuple[int, ...] | None  # None when the sites cannot be mapped
     problem: str | None  # why something is missing, or None
+    # The `.meta`'s `~imroTbl`, verbatim, for wl.works to compare with a
+    # planned IMRO file with its own reader (design spec
+    # `2026-10-01-session-listing-and-run-requests-design.md` section 2.2).
+    imro_table: str | None = None
 
 
 def _metas(nidq_bin: Path) -> list[Path]:
@@ -113,7 +117,7 @@ def read_probe(meta: Path) -> RecordedProbe:
                 else:
                     electrodes = placed
     return RecordedProbe(stream=stream, serial=serial, part_number=part_number, electrodes=electrodes,
-                         problem="; ".join(problems) or None)
+                         problem="; ".join(problems) or None, imro_table=fields.get("~imroTbl"))
 
 
 def read_run(nidq_bin: Path) -> list[RecordedProbe]:
```

```diff
--- a/wl_preproc/schema/ephys.py
+++ b/wl_preproc/schema/ephys.py
@@ -240,6 +240,19 @@ class SegmentConfig(dj.Manual):
     """
 
 
+# `ProbeCensus.Probe.imro_table`'s width. A Neuropixels imroTbl names one
+# entry per channel, about 8,000 characters for 384 channels.
+IMRO_TABLE_MAX = 10240
+
+
+def kept_imro_table(table: str | None) -> tuple[str | None, str | None]:
+    """The table to keep, and a problem when it is too long to keep: a value
+    longer than its column would fail the segment on every pass."""
+    if table is not None and len(table) > IMRO_TABLE_MAX:
+        return None, f"the imroTbl is {len(table)} characters, longer than the {IMRO_TABLE_MAX} kept, so it is not kept"
+    return table, None
+
+
 @schema
 class ProbeCensus(dj.Computed):
     definition = """
@@ -273,6 +286,11 @@ class ProbeCensus(dj.Computed):
         part_number = null : varchar(32)
         -> [nullable] ElectrodeConfig
         problem = '' : varchar(1024)
+        # The segment's `~imroTbl`, verbatim from its `.meta` (design spec
+        # 2026-10-01-session-listing-and-run-requests-design.md section 2.2):
+        # wl.works compares a planned IMRO file with it. Null when absent, or
+        # longer than the column, which is then a problem.
+        imro_table = null : varchar(10240)
         """
 
     @property
@@ -311,8 +329,11 @@ class ProbeCensus(dj.Computed):
                     "electrode_config_hash": register_electrode_config(probe.part_number, list(probe.electrodes)),
                     "probe_type": probe.part_number,
                 }
+            imro_table, too_long = kept_imro_table(probe.imro_table)
+            problems += [too_long] if too_long else []
             parts.append({**key, "stream": probe.stream, "probe_serial": probe.serial,
-                          "part_number": probe.part_number, **config, "problem": "; ".join(problems)})
+                          "part_number": probe.part_number, **config, "problem": "; ".join(problems),
+                          "imro_table": imro_table})
         self.insert1({**key, "n_probes": len(parts)})
         self.Probe.insert(parts)
 
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 21 passed.

Then the census's readers: `.venv/bin/python -m pytest tests/ephys tests/schema/test_probe_census.py tests/schema/test_probe_linking.py tests/schema/test_nwb_probes.py -q -p no:cacheprovider`. Expected: 54 passed, 1 deselected.

- [ ] **Step 5: Mutation checks.**
  - T3a (`spikeglx_probes.py`): `imro_table=fields.get("~imroTbl")` becomes `imro_table=None` [`test_a_probe_keeps_its_imro_table_verbatim`].
  - T3b (`schema/ephys.py`): the length check never refuses [`test_an_imro_table_longer_than_kept_is_a_problem`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/ephys/spikeglx_probes.py wl_preproc/schema/ephys.py tests/ephys/test_spikeglx_probes.py tests/schema/test_probe_census.py
git commit -m "feat(ephys): the probe census keeps each segment's ~imroTbl verbatim, for wl.works to compare with a planned IMRO file -- a table too long to keep is a problem, not a failed segment

<trailer lines>"
```

---

### Task 4: One landed session's entry

**Files:**
- Create: `wl_preproc/listing/__init__.py`, `wl_preproc/listing/entry.py`, `tests/listing/test_entry.py`, `tests/schema/test_session_listing.py`
- Modify: `wl_preproc/contracts/protocol.py`
- Test: `tests/listing/test_entry.py`, `tests/schema/test_session_listing.py`

**Interfaces — consumes:** Task 2's `core.RunRecord` and block attributes; Task 3's `imro_table`; piece 1's `Event.Attribute` rows `run_number` (`RUN_START`) and `block_id` (`BLOCK_START`), one per occurrence.

**Interfaces — produces:**
- `contracts.protocol`: `ListedFlagCode`, `ListedFlag`, `ListedProbe`, `ListedSegment`, `ListedBlock`, `ListedRun`, `RejectedFile`, `SessionEntry`, `SessionListing(cursor: int, sessions: list[SessionEntry])`.
- `listing.entry.SessionFacts` (a frozen dataclass of rows); `build_entry(facts) -> dict` (no database); `gather_facts(session_key) -> SessionFacts`; `session_entry(session_key) -> dict`, the two together, which Tasks 5 and 6 call.

**Why gathered, then built.** Eight flags and the assignment of blocks and segments to runs are pure logic over rows. Built without a database, each is tested on rows made by hand; the database test checks the gathering on one generated session.

**Why segments are listed once.** A segment's sites can run to hundreds of electrodes and an 8,000-character table; listed under every run it spans, they would repeat. Each run names its segments by barcode instead (spec amendment 1).

- [ ] **Step 1: Write the failing tests.** Create both files:

```python
"""One landed session's entry in `GET /sessions`, built from rows made by hand
(design spec `2026-10-01-session-listing-and-run-requests-design.md` sections
2.2 and 2.4)."""

from __future__ import annotations

import dataclasses
import datetime

WHEN = datetime.datetime(2027, 7, 22, 9, 0)


def _probe(barcode, config, serial="19011110001"):
    return {"segment_barcode": barcode, "stream": "imec0", "probe_serial": serial, "part_number": "NP1000",
            "electrode_config_hash": config, "probe_type": "NP1000", "problem": "",
            "imro_table": f"(0,384)({config})"}


def _facts(**changes):
    """Two runs of one block each; one SpikeGLX segment spanning both."""
    from wl_preproc.listing.entry import SessionFacts

    facts = SessionFacts(
        subject="pico", session_datetime=WHEN, session_name="2027-07-22_01", tier="A", rejected=[],
        runs=[{"run_number": 1, "task_type": 0, "run_start_time": 1.0, "run_stop_time": 10.0, "closed": 1},
              {"run_number": 2, "task_type": 0, "run_start_time": 12.0, "run_stop_time": 20.0, "closed": 1}],
        records={1: {"task": "rf_map", "stopped_because": "every block is finished", "stop_kind": "completed"},
                 2: {"task": "fixation", "stopped_because": "stopped by jw", "stop_kind": "operator"}},
        blocks=[{"block_id": 1, "block_start_time": 1.004, "block_stop_time": 9.9},
                {"block_id": 2, "block_start_time": 12.004, "block_stop_time": 19.9}],
        block_attributes={1: {"closed": "1", "block_type": "Bt1"}, 2: {"closed": "1", "block_type": "Bt2"}},
        trial_counts={1: 3, 2: 1},
        segments=[{"segment_barcode": 100, "start_s": 0.5, "end_s": 21.0}],
        census=[_probe(100, "a" * 32)],
        electrodes={("a" * 32, "NP1000"): [0, 1, 2]},
        strobed_runs=[1, 2], strobed_blocks=[1, 2],
    )
    return dataclasses.replace(facts, **changes)


def test_a_run_lists_its_task_its_stop_its_segments_and_its_blocks():
    from wl_preproc.listing.entry import build_entry

    entry = build_entry(_facts())
    assert (entry["session_name"], entry["tier"], entry["probes"], entry["flags"]) == (
        "2027-07-22_01", "A", ["19011110001"], [])
    first = entry["runs"][0]
    assert {name: first[name] for name in ("run_number", "task", "start_s", "end_s", "closed", "stopped_because",
                                           "stop_kind", "segments")} == {
        "run_number": 1, "task": "rf_map", "start_s": 1.0, "end_s": 10.0, "closed": True,
        "stopped_because": "every block is finished", "stop_kind": "completed", "segments": [100]}
    assert first["blocks"] == [{"block_number": 1, "block_in_run": 1, "block_type": "Bt1", "start_s": 1.004,
                                "end_s": 9.9, "closed": True, "n_trials": 3}]
    (segment,) = entry["segments"]
    assert segment["probes"] == [{"stream": "imec0", "serial": "19011110001", "part_number": "NP1000",
                                  "probe_type": "NP1000", "electrode_config_hash": "a" * 32, "n_electrodes": 3,
                                  "electrodes": [0, 1, 2], "imro_table": f"(0,384)({'a' * 32})", "problem": ""}]


def test_a_recording_with_no_measured_run_is_listed_as_waiting():
    from wl_preproc.listing.entry import build_entry

    entry = build_entry(_facts(runs=[], records={}, strobed_runs=[]))
    assert entry["runs"] == []
    assert [flag["code"] for flag in entry["flags"]] == ["waiting_for_run_markers"]


def test_a_repeated_run_or_block_number_is_named_once():
    """A crash restart before wl-xcon's XC-026 (the requester's decision 2):
    the tables keep the first; every occurrence is in `Event`."""
    from wl_preproc.listing.entry import build_entry

    entry = build_entry(_facts(strobed_runs=[1, 2, 1, 2, 1], strobed_blocks=[1, 2, 1]))
    assert [(flag["code"], flag["run_number"], flag["block_number"]) for flag in entry["flags"]] == [
        ("repeated_run_number", 1, None), ("repeated_run_number", 2, None), ("repeated_block_number", None, 1)]
    assert entry["flags"][0]["message"] == ("run number 1 appears 3 times in the recording; only the first is "
                                            "listed, and the restarted runs wait for wl-xcon's XC-026")


def test_a_run_that_stopped_before_its_first_trial_has_no_block():
    from wl_preproc.listing.entry import build_entry

    entry = build_entry(_facts(blocks=[{"block_id": 1, "block_start_time": 1.004, "block_stop_time": 9.9}],
                               strobed_blocks=[1]))
    assert entry["runs"][1]["blocks"] == []
    assert [(flag["code"], flag["run_number"]) for flag in entry["flags"]] == [("run_without_block", 2)]


def test_a_bank_change_inside_a_run_is_flagged_and_one_between_runs_is_not():
    """Runs and segments do not align: a segment boundary inside run 2 with a
    different site map is a bank change in that run; the same boundary in the
    gap between runs is not."""
    from wl_preproc.listing.entry import build_entry

    inside = _facts(segments=[{"segment_barcode": 100, "start_s": 0.5, "end_s": 15.0},
                              {"segment_barcode": 200, "start_s": 15.5, "end_s": 21.0}],
                    census=[_probe(100, "a" * 32), _probe(200, "b" * 32)],
                    electrodes={("a" * 32, "NP1000"): [0, 1, 2], ("b" * 32, "NP1000"): [384, 385, 386]})
    entry = build_entry(inside)
    assert [run["segments"] for run in entry["runs"]] == [[100], [100, 200]]
    assert [(flag["code"], flag["run_number"]) for flag in entry["flags"]] == [("bank_change_in_run", 2)]

    between = dataclasses.replace(inside, segments=[{"segment_barcode": 100, "start_s": 0.5, "end_s": 11.0},
                                                    {"segment_barcode": 200, "start_s": 11.5, "end_s": 21.0}])
    assert build_entry(between)["flags"] == []


def test_a_run_or_block_the_rig_did_not_name_is_flagged():
    from wl_preproc.listing.entry import build_entry

    entry = build_entry(_facts(records={1: {"task": "rf_map", "stopped_because": None, "stop_kind": None}},
                               block_attributes={1: {"closed": "0"}, 2: {"closed": "1", "block_type": "Bt2"}}))
    assert [(flag["code"], flag["run_number"], flag["block_number"]) for flag in entry["flags"]] == [
        ("block_type_unknown", 1, 1), ("task_unknown", 2, None)]
    assert entry["runs"][0]["blocks"][0]["closed"] is False


def test_a_block_in_no_run_is_flagged_when_the_session_has_runs():
    """Its run's RUN_START was lost. With no runs at all, the session is
    waiting for its run markers instead."""
    from wl_preproc.listing.entry import build_entry

    stray = {"block_id": 3, "block_start_time": 25.0, "block_stop_time": 26.0}
    entry = build_entry(_facts(blocks=[*_facts().blocks, stray], strobed_blocks=[1, 2, 3]))
    assert [(flag["code"], flag["block_number"]) for flag in entry["flags"]] == [("block_outside_runs", 3)]
    waiting = build_entry(_facts(runs=[], records={}, strobed_runs=[], blocks=[stray], strobed_blocks=[3]))
    assert [flag["code"] for flag in waiting["flags"]] == ["waiting_for_run_markers"]


def test_a_probe_the_census_could_not_name_is_listed_with_its_problem():
    """It is in its segment, with no site map, and among no serials; it
    cannot make a bank change."""
    from wl_preproc.listing.entry import build_entry

    unnamed = {"segment_barcode": 100, "stream": "imec1", "probe_serial": None, "part_number": None,
               "electrode_config_hash": None, "probe_type": None, "problem": "the .meta names no serial (imDatPrb_sn)",
               "imro_table": None}
    entry = build_entry(_facts(census=[_probe(100, "a" * 32), unnamed]))
    assert entry["segments"][0]["probes"][1] == {
        "stream": "imec1", "serial": None, "part_number": None, "probe_type": None, "electrode_config_hash": None,
        "n_electrodes": None, "electrodes": None, "imro_table": None,
        "problem": "the .meta names no serial (imDatPrb_sn)"}
    assert (entry["probes"], entry["flags"]) == (["19011110001"], [])


def test_a_session_listed_before_its_timing_and_census_has_neither():
    """The event stage is done, and the computed tables are not yet: the
    session is listed now, and again when they are."""
    from wl_preproc.listing.entry import build_entry

    entry = build_entry(_facts(tier=None, segments=[], census=[], electrodes={}))
    assert (entry["tier"], entry["segments"], entry["probes"], entry["flags"]) == (None, [], [], [])
    assert [run["segments"] for run in entry["runs"]] == [[], []]
```

```python
"""The landed-session listing's entries, read from what one daemon pass
measured (design spec `2026-10-01-session-listing-and-run-requests-design.md`
section 2). Subjects and dates checked unclaimed across `tests/` on
2026-10-01."""

from __future__ import annotations

import pytest

from tests.schema.test_spikeglx_restart import RESTART, _session


@pytest.fixture(scope="module")
def listed(dj_conn, prefix, tmp_path_factory):
    """Two sessions, then one daemon pass. `runs`: its blocks wrapped in
    runs, run 1 faulted (its block unclosed), and SpikeGLX restarted at 11 s
    with a new bank, inside run 2. `plain`: no run markers."""
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    sessions = {
        "runs": _session(tmp_path_factory, subject="sllist1", session_id="2025-07-21_01", runs=True,
                         unclosed_blocks=[1], spikeglx_restart={**RESTART, "probe_bank": 1}),
        "plain": _session(tmp_path_factory, subject="sllist2", session_id="2025-07-22_01"),
    }
    daemon.run_once(prefix=prefix)
    return sessions


def test_an_entry_reads_what_the_daemon_measured(listed):
    from wl_preproc.listing.entry import session_entry
    from wl_preproc.schema import ephys

    recipe, key = listed["runs"]
    entry = session_entry(key)
    assert (entry["subject"], entry["session_name"], entry["probes"]) == (
        "sllist1", recipe.session_id, [recipe.probe_serial])
    assert entry["tier"] in ("A", "B", "C", "D")
    assert [(run["run_number"], run["task"], run["closed"], run["stop_kind"]) for run in entry["runs"]] == [
        (1, recipe.blocks[0].task_type.name.lower(), False, None),
        (2, recipe.blocks[1].task_type.name.lower(), True, "completed")]
    assert [[(block["block_number"], block["block_type"], block["closed"], block["n_trials"])
             for block in run["blocks"]] for run in entry["runs"]] == [[(1, "block-1", False, 3)],
                                                                      [(2, "block-2", True, 1)]]
    first, second = entry["segments"]
    assert [run["segments"] for run in entry["runs"]] == [[first["segment_barcode"]],
                                                          [first["segment_barcode"], second["segment_barcode"]]]
    census = (ephys.ProbeCensus.Probe & key).to_dicts(order_by="segment_barcode")
    assert [segment["probes"][0]["imro_table"] for segment in entry["segments"]] == [
        part["imro_table"] for part in census]
    assert [segment["probes"][0]["electrodes"][0] for segment in entry["segments"]] == [0, 384]
    assert [(flag["code"], flag["run_number"]) for flag in entry["flags"]] == [("bank_change_in_run", 2)]


def test_a_session_without_run_markers_is_listed_waiting(listed):
    from wl_preproc.listing.entry import session_entry

    _recipe, key = listed["plain"]
    entry = session_entry(key)
    assert entry["runs"] == []
    assert [flag["code"] for flag in entry["flags"]] == ["waiting_for_run_markers"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/listing/test_entry.py tests/schema/test_session_listing.py -q --tb=line -p no:cacheprovider`
Expected: 11 failed. `wl_preproc.listing` does not exist.

- [ ] **Step 3: Implement.** Apply the diff and create the package:

```diff
--- a/wl_preproc/contracts/protocol.py
+++ b/wl_preproc/contracts/protocol.py
@@ -350,3 +350,133 @@ class NwbListing(BaseModel):
 
     cursor: int
     files: list[NwbListingEntry]
+
+
+# -- Landed sessions: GET /sessions (design spec
+# `2026-10-01-session-listing-and-run-requests-design.md` section 2). --------
+
+ListedFlagCode = Literal[
+    "waiting_for_run_markers",
+    "repeated_run_number",
+    "repeated_block_number",
+    "run_without_block",
+    "bank_change_in_run",
+    "block_type_unknown",
+    "task_unknown",
+    "block_outside_runs",
+]
+
+
+class ListedFlag(BaseModel):
+    """A fact wl.works shows beside the session as a hint. None blocks
+    anything here. `run_number` and `block_number` say where, when it is one
+    run or one block."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    code: ListedFlagCode
+    message: str
+    run_number: int | None = None
+    block_number: int | None = None
+
+
+class ListedProbe(BaseModel):
+    """One probe in one SpikeGLX segment, from `ephys.ProbeCensus`. Two
+    segments with the same `electrode_config_hash` for a serial share a bank
+    setting. `electrodes` are SpikeGLX electrode numbers (bank x 384 +
+    channel for the NP1.0 family), the saved channels only; `imro_table` is
+    the segment's `~imroTbl`, verbatim."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    stream: str
+    serial: str | None
+    part_number: str | None
+    probe_type: str | None
+    electrode_config_hash: str | None
+    n_electrodes: int | None
+    electrodes: list[int] | None
+    imro_table: str | None
+    problem: str
+
+
+class ListedSegment(BaseModel):
+    """One SpikeGLX file's extent on the recording's clock, and its probes."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    segment_barcode: int
+    start_s: float
+    end_s: float
+    probes: list[ListedProbe]
+
+
+class ListedBlock(BaseModel):
+    """A measured block: consecutive trials under one block type, inside a
+    run. `block_number` is its number in the session, as `BLOCK_START`
+    strobes it; `block_in_run` its order in its run, from 1."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    block_number: int
+    block_in_run: int
+    block_type: str | None
+    start_s: float
+    end_s: float
+    closed: bool
+    n_trials: int
+
+
+class ListedRun(BaseModel):
+    """A measured run (`core.Run`) with the rig's record of it
+    (`core.RunRecord`). `segments` names, by barcode, the SpikeGLX segments
+    it spans; each is listed once, in the session's `segments`."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    run_number: int
+    task_code: int
+    task: str | None
+    start_s: float
+    end_s: float
+    closed: bool
+    stopped_because: str | None
+    stop_kind: str | None
+    segments: list[int]
+    blocks: list[ListedBlock]
+
+
+class RejectedFile(BaseModel):
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    system: str
+    file_path: str
+    reason: str
+
+
+class SessionEntry(BaseModel):
+    """One landed session, as it stands now. `tier` is the timing tier, null
+    until it is computed; D is quarantined, not published automatically."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    subject: str
+    session_datetime: datetime.datetime
+    session_name: str
+    tier: Literal["A", "B", "C", "D"] | None
+    rejected_segments: list[RejectedFile]
+    runs: list[ListedRun]
+    segments: list[ListedSegment]
+    probes: list[str]
+    flags: list[ListedFlag]
+
+
+class SessionListing(BaseModel):
+    """`GET /sessions?since=<cursor>`: every session whose entry changed after
+    the cursor, as it stands now, and the cursor to send next time. The
+    cursor only increases."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    cursor: int
+    sessions: list[SessionEntry]
```

```python
"""The landed-session listing, `GET /sessions` (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 2)."""
```

```python
"""One landed session's entry in `GET /sessions` (design spec
`2026-10-01-session-listing-and-run-requests-design.md` sections 2.2 and 2.4).

**Gathered, then built.** `gather_facts` reads the database into plain rows;
`build_entry` turns them into the entry, with no database, so every flag is
tested on rows made by hand. `session_entry` is the two together, and the
listing stage and `GET /sessions` both call it, so they cannot disagree.

**A block belongs to the run its start lies in**, and a segment to every run
it overlaps: runs and segments do not align, since a bank change needs a
SpikeGLX restart and a run need not stop for one.
"""

from __future__ import annotations

import collections
import dataclasses
import datetime
from pathlib import Path

from wl_preproc.contracts.protocol import SessionEntry


@dataclasses.dataclass(frozen=True)
class SessionFacts:
    """Everything an entry is built from, as the tables hold it."""

    subject: str
    session_datetime: datetime.datetime
    session_name: str
    tier: str | None
    rejected: list[dict]  # {system, file_path, reason}
    runs: list[dict]  # core.Run rows
    records: dict[int, dict]  # run_number -> core.RunRecord row
    blocks: list[dict]  # trial.Block rows: block_id, block_start_time, block_stop_time
    block_attributes: dict[int, dict[str, str]]  # block_id -> {attribute_name: attribute_value}
    trial_counts: dict[int, int]  # block_id -> trials stored in it
    segments: list[dict]  # SpikeGLX core.Segment rows
    census: list[dict]  # ephys.ProbeCensus.Probe rows
    electrodes: dict[tuple[str, str], list[int]]  # (electrode_config_hash, probe_type) -> sorted electrodes
    strobed_runs: list[int]  # every RUN_START's run number, one per occurrence
    strobed_blocks: list[int]  # every BLOCK_START's block number, one per occurrence


def _repeats(numbers: list[int]) -> list[tuple[int, int]]:
    return sorted((number, count) for number, count in collections.Counter(numbers).items() if count > 1)


def _flag(code: str, message: str, run_number: int | None = None, block_number: int | None = None) -> dict:
    return {"code": code, "message": message, "run_number": run_number, "block_number": block_number}


def build_entry(facts: SessionFacts) -> dict:
    """The entry, validated against `SessionEntry`, as JSON-ready data."""
    flags = []
    if not facts.runs:
        flags.append(_flag("waiting_for_run_markers",
                           "the recording has no measured run: waiting for the rig's run markers"))
    for number, count in _repeats(facts.strobed_runs):
        flags.append(_flag("repeated_run_number",
                           f"run number {number} appears {count} times in the recording; only the first is listed, "
                           "and the restarted runs wait for wl-xcon's XC-026", run_number=number))
    for number, count in _repeats(facts.strobed_blocks):
        flags.append(_flag("repeated_block_number",
                           f"block number {number} appears {count} times in the recording; only the first is "
                           "listed", block_number=number))

    probes_of = collections.defaultdict(list)
    for part in sorted(facts.census, key=lambda part: (part["segment_barcode"], part["stream"])):
        config = (part["electrode_config_hash"], part["probe_type"])
        electrodes = facts.electrodes.get(config) if part["electrode_config_hash"] else None
        probes_of[part["segment_barcode"]].append({
            "stream": part["stream"], "serial": part["probe_serial"], "part_number": part["part_number"],
            "probe_type": part["probe_type"], "electrode_config_hash": part["electrode_config_hash"],
            "n_electrodes": None if electrodes is None else len(electrodes), "electrodes": electrodes,
            "imro_table": part["imro_table"], "problem": part["problem"],
        })
    segments = [
        {"segment_barcode": segment["segment_barcode"], "start_s": segment["start_s"], "end_s": segment["end_s"],
         "probes": probes_of[segment["segment_barcode"]]}
        for segment in sorted(facts.segments, key=lambda segment: segment["start_s"])
    ]

    runs, in_a_run = [], set()
    ordered_runs = sorted(facts.runs, key=lambda run: run["run_number"])
    for run in ordered_runs:
        start, stop = run["run_start_time"], run["run_stop_time"]
        record = facts.records.get(run["run_number"], {})
        spanned = [segment for segment in segments if segment["start_s"] < stop and segment["end_s"] > start]
        blocks = sorted((block for block in facts.blocks if start <= block["block_start_time"] <= stop),
                        key=lambda block: block["block_start_time"])
        listed_blocks = []
        in_a_run.update(block["block_id"] for block in blocks)
        for order, block in enumerate(blocks, start=1):
            attributes = facts.block_attributes.get(block["block_id"], {})
            listed_blocks.append({
                "block_number": block["block_id"], "block_in_run": order,
                "block_type": attributes.get("block_type"),
                "start_s": float(block["block_start_time"]), "end_s": float(block["block_stop_time"]),
                "closed": attributes.get("closed") == "1", "n_trials": facts.trial_counts.get(block["block_id"], 0),
            })
        runs.append({
            "run_number": run["run_number"], "task_code": run["task_type"], "task": record.get("task"),
            "start_s": start, "end_s": stop, "closed": bool(run["closed"]),
            "stopped_because": record.get("stopped_because"), "stop_kind": record.get("stop_kind"),
            "segments": [segment["segment_barcode"] for segment in spanned], "blocks": listed_blocks,
        })
        if not listed_blocks:
            flags.append(_flag("run_without_block",
                               f"run {run['run_number']} recorded no block: it stopped before its first trial",
                               run_number=run["run_number"]))
        maps = collections.defaultdict(set)
        for segment in spanned:
            for probe in segment["probes"]:
                if probe["serial"] and probe["electrode_config_hash"]:
                    maps[probe["serial"]].add(probe["electrode_config_hash"])
        for serial in sorted(serial for serial, found in maps.items() if len(found) > 1):
            flags.append(_flag("bank_change_in_run",
                               f"run {run['run_number']} spans segments where probe {serial} records two site "
                               "maps: a bank changed inside the run", run_number=run["run_number"]))
        if record.get("task") is None:
            flags.append(_flag("task_unknown",
                               f"run {run['run_number']} has no task: the rig's record has no start row for it",
                               run_number=run["run_number"]))
        for block in listed_blocks:
            if block["block_type"] is None:
                flags.append(_flag("block_type_unknown",
                                   f"block {block['block_number']} has no block type: the rig's record names none "
                                   "or two for its trials", run_number=run["run_number"],
                                   block_number=block["block_number"]))

    # A block in no run, when the session has runs: its run's RUN_START was
    # lost, or it was strobed outside a run. With no runs at all the session
    # is already waiting for its run markers, and every block is outside one.
    if facts.runs:
        for block in sorted(facts.blocks, key=lambda block: block["block_start_time"]):
            if block["block_id"] not in in_a_run:
                flags.append(_flag("block_outside_runs",
                                   f"block {block['block_id']} lies in no measured run: its run's RUN_START was "
                                   "lost, or it was strobed outside a run", block_number=block["block_id"]))

    entry = {
        "subject": facts.subject, "session_datetime": facts.session_datetime, "session_name": facts.session_name,
        "tier": facts.tier, "rejected_segments": sorted(facts.rejected, key=lambda row: (row["system"],
                                                                                          row["file_path"])),
        "runs": runs, "segments": segments,
        "probes": sorted({part["probe_serial"] for part in facts.census if part["probe_serial"]}),
        "flags": flags,
    }
    return SessionEntry.model_validate(entry).model_dump(mode="json")


def gather_facts(session_key: dict) -> SessionFacts:
    """One session's facts, read from the database."""
    from wl_preproc.schema import core, ephys, ingest, pipeline, timebase

    session_key = {name: session_key[name] for name in ("subject", "session_datetime")}
    tiers = (timebase.TimingProvenance & session_key).to_arrays("tier")
    attributes = collections.defaultdict(dict)
    for row in (pipeline.trial.Block.Attribute & session_key).to_dicts():
        attributes[row["block_id"]][row["attribute_name"]] = row["attribute_value"]
    census = (ephys.ProbeCensus.Probe & session_key).to_dicts()
    electrodes = {}
    for part in census:
        config = (part["electrode_config_hash"], part["probe_type"])
        if part["electrode_config_hash"] and config not in electrodes:
            restriction = {"electrode_config_hash": config[0], "probe_type": config[1]}
            electrodes[config] = sorted(int(e) for e in (ephys.ElectrodeConfig.Electrode & restriction)
                                        .to_arrays("electrode"))

    def strobed(event_type: str, name: str) -> list[int]:
        rows = pipeline.event.Event.Attribute & session_key & {"event_type": event_type, "attribute_name": name}
        return [int(value) for value in rows.to_arrays("attribute_value")]

    return SessionFacts(
        subject=session_key["subject"],
        session_datetime=session_key["session_datetime"],
        session_name=Path((ingest.Ingestion & session_key).fetch1("session_dir")).name,
        tier=str(tiers[0]) if len(tiers) else None,
        rejected=[{"system": row["system"], "file_path": row["file_path"], "reason": row["reason"]}
                  for row in (core.RejectedSegment & session_key).to_dicts()],
        runs=(core.Run & session_key).to_dicts(),
        records={row["run_number"]: row for row in (core.RunRecord & session_key).to_dicts()},
        blocks=(pipeline.trial.Block & session_key).to_dicts(),
        block_attributes=dict(attributes),
        trial_counts=dict(collections.Counter(int(block_id) for block_id in
                                              (pipeline.trial.BlockTrial & session_key).to_arrays("block_id"))),
        segments=(core.Segment & session_key & {"system": "spikeglx"}).to_dicts(),
        census=census,
        electrodes=electrodes,
        strobed_runs=strobed("RUN_START", "run_number"),
        strobed_blocks=strobed("BLOCK_START", "block_id"),
    )


def session_entry(session_key: dict) -> dict:
    """The session's entry as it stands now."""
    return build_entry(gather_facts(session_key))
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 11 passed.

Then the contracts: `.venv/bin/python -m pytest tests/listing tests/schema/test_session_listing.py tests/contracts -q -p no:cacheprovider`. Expected: 121 passed.

- [ ] **Step 5: Mutation checks.**
  - T4a (`entry.py`): a block belongs to every run that started before it (the `<= stop` bound dropped) [`test_a_run_lists_its_task_its_stop_its_segments_and_its_blocks`, `test_a_block_in_no_run_is_flagged_when_the_session_has_runs`].
  - T4b: a bank change needs three site maps [`test_a_bank_change_inside_a_run_is_flagged_and_one_between_runs_is_not`].
  - T4c: a number repeats only when it appears three times [`test_a_repeated_run_or_block_number_is_named_once`].
  - T4d: `block_outside_runs` is raised with no runs too [`test_a_recording_with_no_measured_run_is_listed_as_waiting`, `test_a_block_in_no_run_is_flagged_when_the_session_has_runs`].
  - T4e: a probe's `electrodes` are always null [`test_a_run_lists_its_task_its_stop_its_segments_and_its_blocks`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/contracts/protocol.py wl_preproc/listing/__init__.py wl_preproc/listing/entry.py tests/listing/test_entry.py tests/schema/test_session_listing.py
git commit -m "feat(listing): one landed session's entry -- its runs with the rig's task and stop reason, the SpikeGLX segments each spans with every probe's site map and raw imroTbl, the blocks under each run, and its flags; built from rows, so every flag is tested without a database

<trailer lines>"
```

---

### Task 5: The change log, and the daemon stage that writes it

**Files:**
- Create: `wl_preproc/listing/stage.py`
- Modify: `wl_preproc/schema/ingest.py`, `wl_preproc/nwb/lock.py`, `wl_preproc/daemon.py`
- Test: `tests/schema/test_session_listing.py`

**Interfaces — consumes:** Task 4's `session_entry`.

**Interfaces — produces:**
- `ingest.SessionChange`: `change_seq : int unsigned auto_increment`; `-> pipeline.Session`, `digest : char(64)`, `changed_at : datetime(6)`.
- `listing.stage.digest(entry) -> str`, `listable() -> list[dict]`, `run_stage() -> (appended, errors)`.
- `nwb.lock.exclusive(prefix, what="nwb")` and `lock_name(prefix, what="nwb")`; `what="listing"` is the stage's lock.
- `daemon.run_once` runs the stage after the link stage, counting its changes into `populated`.

**Why a digest, not a hook in every stage.** A timing tier, a probe census or a rejected segment arrives in a different stage, at a different pass. Comparing each entry's hash with its last lets any of them re-list the session without knowing the listing exists.

**Why its own lock.** `GET /nwb`'s finding M4 (`docs/handoffs/2026-09-29-nwb-publishing.md`): two writers leave holes in a cursor, a sequence number allocated first and committed last, which a reader can step past. The stage is the log's one writer; a second `wlpp` process skips it and says so.

- [ ] **Step 1: Write the failing tests.** Apply this diff:

```diff
--- a/tests/schema/test_session_listing.py
+++ b/tests/schema/test_session_listing.py
@@ -59,3 +59,75 @@ def test_a_session_without_run_markers_is_listed_waiting(listed):
     entry = session_entry(key)
     assert entry["runs"] == []
     assert [flag["code"] for flag in entry["flags"]] == ["waiting_for_run_markers"]
+
+
+# -- The change log and the listing stage (section 2.1). Run after the
+# entries' tests: these append changes and restore what they change.
+
+
+def _changes(key):
+    from wl_preproc.schema import ingest
+
+    return (ingest.SessionChange & key).to_dicts(order_by="change_seq")
+
+
+def test_one_pass_logs_each_listed_session_once_with_its_entrys_digest(listed):
+    from wl_preproc.listing.entry import session_entry
+    from wl_preproc.listing.stage import digest
+
+    for _recipe, key in listed.values():
+        (change,) = _changes(key)
+        assert change["digest"] == digest(session_entry(key))
+
+
+def test_an_unchanged_entry_is_not_logged_again(listed):
+    from wl_preproc.listing.stage import run_stage
+
+    before = {name: len(_changes(key)) for name, (_recipe, key) in listed.items()}
+    _appended, errors = run_stage()
+    assert errors == []
+    assert {name: len(_changes(key)) for name, (_recipe, key) in listed.items()} == before
+
+
+def test_a_fact_that_arrives_later_lists_the_session_again(listed):
+    """A rejected segment recorded after the first listing changes the entry,
+    so that session alone is logged again; removing it logs it once more."""
+    from wl_preproc.listing.stage import run_stage
+    from wl_preproc.schema import core
+
+    _recipe, key = listed["runs"]
+    _plain, other = listed["plain"]
+    row = {**key, "system": "spikeglx", "file_path": "late/run_g9_t0.nidq.bin", "reason": "late"}
+    before, other_before = len(_changes(key)), len(_changes(other))
+    core.RejectedSegment.insert1(row)
+    try:
+        run_stage()
+        assert (len(_changes(key)), len(_changes(other))) == (before + 1, other_before)
+    finally:
+        (core.RejectedSegment & row).delete_quick()
+    run_stage()
+    assert len(_changes(key)) == before + 2
+
+
+def test_a_pass_leaves_the_listing_to_a_process_holding_its_lock(listed, prefix):
+    from tests.schema.test_request import _raw_connection
+    from wl_preproc import daemon
+    from wl_preproc.nwb.lock import lock_name
+    from wl_preproc.schema import core
+
+    _recipe, key = listed["runs"]
+    row = {**key, "system": "spikeglx", "file_path": "late/run_g8_t0.nidq.bin", "reason": "late"}
+    before = len(_changes(key))
+    core.RejectedSegment.insert1(row)
+    other = _raw_connection()
+    try:
+        with other.cursor() as cursor:
+            cursor.execute("SELECT GET_LOCK(%s, 0)", (lock_name(prefix, "listing"),))
+            assert cursor.fetchone()[0] == 1
+        report = daemon.run_once(prefix=prefix)
+    finally:
+        other.close()
+        (core.RejectedSegment & row).delete_quick()
+    assert len(_changes(key)) == before
+    assert any(error.startswith("SessionChange: another wlpp process holds the listing lock")
+               for error in report["errors"])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_session_listing.py -q --tb=line -p no:cacheprovider`
Expected: 4 failed, 2 passed. `wl_preproc.listing.stage` does not exist and `ingest` has no `SessionChange`, so the four change-log tests fail; Task 4's two entry tests pass.

- [ ] **Step 3: Implement.** Apply the diffs and create the stage:

```diff
--- a/wl_preproc/schema/ingest.py
+++ b/wl_preproc/schema/ingest.py
@@ -87,6 +87,22 @@ class Ingestion(dj.Manual):
     """
 
 
+@schema
+class SessionChange(dj.Manual):
+    definition = """
+    # Every change to a landed session's entry in GET /sessions (design spec
+    # 2026-10-01-session-listing-and-run-requests-design.md section 2.1),
+    # appended by the daemon's listing stage alone, under its lock, when the
+    # entry's digest differs from the session's last. The sequence only
+    # increases, and is GET /sessions' cursor. Key: (change_seq).
+    change_seq : int unsigned auto_increment
+    ---
+    -> pipeline.Session
+    digest     : char(64)     # sha256 of the entry's JSON, keys sorted
+    changed_at : datetime(6)
+    """
+
+
 @schema
 class Quarantine(dj.Manual):
     definition = f"""
```

```diff
--- a/wl_preproc/nwb/lock.py
+++ b/wl_preproc/nwb/lock.py
@@ -8,7 +8,13 @@ to the same `.partial`, record it twice, and leave holes in `GET /nwb`'s
 cursor (a sequence number allocated first but committed last). A MySQL
 named lock, held by one database session for the stages' duration, lets
 the second process skip them and say why; the lock goes with the session if
-the process dies."""
+the process dies.
+
+**The listing stage takes its own lock the same way** (design spec
+`2026-10-01-session-listing-and-run-requests-design.md` section 2.1): it is
+`GET /sessions`' one writer, and two would leave holes in that cursor too.
+Neither lock is ever waited for, so holding both in one pass cannot
+deadlock."""
 
 from __future__ import annotations
 
@@ -17,24 +23,29 @@ from collections.abc import Iterator
 
 
 class Busy(Exception):
-    """Another wlpp process holds the NWB lock."""
+    """Another wlpp process holds a stage's lock."""
+
+
+# Each lock's name in a message, and what is left to the process holding it.
+_LOCKS = {"nwb": ("NWB", "the NWB stages are"), "listing": ("listing", "the listing stage is")}
 
 
-def lock_name(prefix: str) -> str:
+def lock_name(prefix: str, what: str = "nwb") -> str:
     """One lock per database prefix: a test suite's and a deployment's do
     not collide. MySQL allows 64 characters."""
-    return f"wlpp_nwb_{prefix}"[:64]
+    return f"wlpp_{what}_{prefix}"[:64]
 
 
 @contextlib.contextmanager
-def exclusive(prefix: str) -> Iterator[None]:
-    """Hold the NWB lock, or raise `Busy` at once: never wait for it."""
+def exclusive(prefix: str, what: str = "nwb") -> Iterator[None]:
+    """Hold the `what` lock, or raise `Busy` at once: never wait for it."""
     import datajoint as dj
 
+    label, left = _LOCKS[what]
     connection = dj.conn()
-    name = lock_name(prefix)
+    name = lock_name(prefix, what)
     if connection.query("SELECT GET_LOCK(%s, 0)", args=(name,)).fetchone()[0] != 1:
-        raise Busy(f"another wlpp process holds the NWB lock {name}; the NWB stages are left to it")
+        raise Busy(f"another wlpp process holds the {label} lock {name}; {left} left to it")
     try:
         yield
     finally:
```

```python
"""The daemon's listing stage: `GET /sessions`' one writer (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 2.1).

**A change is logged when a session's entry changes.** Every pass assembles
the entry of every session the event stage has done, hashes it, and appends
an `ingest.SessionChange` when the hash differs from the session's last. So a
fact that arrives later -- a timing tier, a probe census -- lists the session
again, without the stage that produced it knowing about the listing.

**One writer, under its lock** (`nwb/lock.py`, `what="listing"`). A sequence
number allocated first but committed last is a hole a reader's cursor can
step past and never see; one writer at a time has none.
"""

from __future__ import annotations

import datetime
import hashlib
import json

from wl_preproc.listing.entry import session_entry


def digest(entry: dict) -> str:
    """The entry's sha256, over its JSON with keys sorted."""
    return hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def listable() -> list[dict]:
    """The sessions the event stage has done: their runs, if any, are measured."""
    from wl_preproc.schema import ingest, pipeline

    return sorted((pipeline.Session & ingest.Ingestion & pipeline.event.BehaviorRecording).keys(),
                  key=lambda key: (key["subject"], key["session_datetime"]))


def run_stage() -> tuple[int, list[str]]:
    """Append a change for each session whose entry changed. Returns
    `(changes appended, per-session failures)`, as every stage of
    `daemon.run_once` does."""
    from wl_preproc.schema import ingest

    last = {}
    for row in ingest.SessionChange.to_dicts(order_by="change_seq"):
        last[(row["subject"], row["session_datetime"])] = row["digest"]
    appended, errors = 0, []
    for key in listable():
        try:
            entry_digest = digest(session_entry(key))
            if last.get((key["subject"], key["session_datetime"])) != entry_digest:
                ingest.SessionChange.insert1({**key, "digest": entry_digest, "changed_at": _now()})
                appended += 1
        except Exception as exc:  # one session must not stop the others
            errors.append(f"SessionChange {key}: {exc}")
    return appended, errors


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
```

```diff
--- a/wl_preproc/daemon.py
+++ b/wl_preproc/daemon.py
@@ -1033,6 +1033,21 @@ def run_once(
     populated += linked
     errors.extend(link_errors)
 
+    # After the link stage and the computed tables, whose census, timing and
+    # segments it reads. Under its own lock: it is `GET /sessions`' one
+    # writer (design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    # section 2.1).
+    from wl_preproc.listing.stage import run_stage as run_listing_stage
+    from wl_preproc.nwb import lock
+
+    try:
+        with lock.exclusive(prefix, "listing"):
+            listed, listing_errors = run_listing_stage()
+            populated += listed
+            errors.extend(listing_errors)
+    except lock.Busy as busy:
+        errors.append(f"SessionChange: {busy}")
+
     # The three NWB stages run under one database lock (`nwb/lock.py`): a
     # second wlpp process -- a pass outliving its cron interval, or `wlpp nwb
     # build` -- leaves them to the first and says so. `0`, not `None`, for a
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 6 passed.

Then the daemon's own suites: `.venv/bin/python -m pytest tests/schema/test_session_listing.py tests/schema/test_daemon.py tests/schema/test_daemon_skips_freed_sessions.py -q -p no:cacheprovider`. Expected: 31 passed.

- [ ] **Step 5: Mutation checks.**
  - T5a (`stage.py`): every pass appends, changed or not [`test_an_unchanged_entry_is_not_logged_again`].
  - T5b (`daemon.py`): the stage runs without its lock [`test_a_pass_leaves_the_listing_to_a_process_holding_its_lock`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/ingest.py wl_preproc/nwb/lock.py wl_preproc/listing/stage.py wl_preproc/daemon.py tests/schema/test_session_listing.py
git commit -m "feat(listing): the daemon's listing stage logs a change when a session's entry changes -- GET /sessions' one writer, under its own named lock, so its cursor has no holes

<trailer lines>"
```

---

### Task 6: `GET /sessions`

**Files:**
- Create: `wl_preproc/responder/sessions.py`, `tests/responder/test_sessions_http.py`, `docs/schemas/session_listing.json` (exported)
- Modify: `wl_preproc/responder/handler.py`, `wl_preproc/responder/server.py`, `wl_preproc/cli/main.py`, `docs/ops/lab-host-protocol.md`, `tests/schema/test_session_listing.py`
- Test: `tests/responder/test_sessions_http.py`, `tests/schema/test_session_listing.py`, `tests/cli/test_schemas_export.py`

**Interfaces — consumes:** Task 4's `session_entry` and `SessionListing`; Task 5's `ingest.SessionChange`.

**Interfaces — produces:**
- `responder.sessions.list_sessions(since: int | None, prefix=DEFAULT_PREFIX) -> dict`.
- `handler.make_handler(..., sessions_list_fn=None)`: `/sessions` answers `GET`, with `?since=` validated as for `/nwb`.
- `docs/schemas/session_listing.json`.

**Why it reads, never writes.** The responder takes its one process-wide lock around a single database connection; the change log has one writer, the daemon's stage (Task 5).

- [ ] **Step 1: Write the failing tests.** Create the HTTP test and apply the diff:

```python
"""`GET /sessions` over real HTTP, with a stand-in callable (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 2.1). The
database half is in `tests/schema/test_session_listing.py`."""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from tests.responder.test_http import TOKEN, _health_ok, _request, _unused
from wl_preproc.responder.handler import make_handler


@pytest.fixture
def serve_sessions():
    started = []

    def _start(sessions_list_fn=_unused, *, with_sessions=True):
        handler_cls = (make_handler(TOKEN, _health_ok, _unused, sessions_list_fn=sessions_list_fn)
                       if with_sessions else make_handler(TOKEN, _health_ok, _unused))
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield _start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def test_the_listing_is_behind_the_token_and_takes_an_optional_cursor(serve_sessions):
    calls = []
    base = serve_sessions(lambda since: calls.append(since) or {"cursor": 3, "sessions": []})
    assert _request(f"{base}/sessions", method="GET")[0] == 401
    status, body = _request(f"{base}/sessions", method="GET", token=TOKEN)
    assert (status, json.loads(body)) == (200, {"cursor": 3, "sessions": []})
    assert _request(f"{base}/sessions?since=2", method="GET", token=TOKEN)[0] == 200
    assert calls == [None, 2]


@pytest.mark.parametrize("query", ["since=x", "since=-1", "since=1&since=2", "other=1", "since="])
def test_a_cursor_that_is_not_one_non_negative_integer_is_422(serve_sessions, query):
    base = serve_sessions()
    status, body = _request(f"{base}/sessions?{query}", method="GET", token=TOKEN)
    assert status == 422 and "since" in json.loads(body)["error"]


def test_a_host_without_the_listing_does_not_answer_it(serve_sessions):
    base = serve_sessions(with_sessions=False)
    assert _request(f"{base}/sessions", method="GET", token=TOKEN)[0] == 404
    assert _request(f"{base}/sessions?since=1", method="GET", token=TOKEN)[0] == 404


def test_only_get_answers_it_and_a_query_elsewhere_is_still_404(serve_sessions):
    base = serve_sessions(lambda since: {"cursor": 0, "sessions": []})
    assert _request(f"{base}/sessions", method="POST", token=TOKEN, body={})[0] == 405
    assert _request(f"{base}/health?since=1", method="GET", token=TOKEN)[0] == 404


def test_a_failure_behind_it_is_a_clean_500(serve_sessions):
    def broken(_since):
        raise RuntimeError("the database went away")

    status, body = _request(f"{serve_sessions(broken)}/sessions", method="GET", token=TOKEN)
    assert (status, json.loads(body)) == (500, {"error": "RuntimeError: the database went away"})
```

```diff
--- a/tests/schema/test_session_listing.py
+++ b/tests/schema/test_session_listing.py
@@ -61,6 +61,20 @@ def test_a_session_without_run_markers_is_listed_waiting(listed):
     assert [flag["code"] for flag in entry["flags"]] == ["waiting_for_run_markers"]
 
 
+
+def test_get_sessions_lists_each_logged_session_as_it_stands_and_its_cursor(listed, prefix):
+    from wl_preproc.listing.entry import session_entry
+    from wl_preproc.responder.sessions import list_sessions
+    from wl_preproc.schema import ingest
+
+    listing = list_sessions(None, prefix=prefix)
+    assert listing["cursor"] == max(ingest.SessionChange.to_arrays("change_seq"))
+    by_subject = {entry["subject"]: entry for entry in listing["sessions"]}
+    for _recipe, key in listed.values():
+        assert by_subject[key["subject"]] == session_entry(key)
+    assert list_sessions(listing["cursor"], prefix=prefix) == {"cursor": listing["cursor"], "sessions": []}
+
+
 # -- The change log and the listing stage (section 2.1). Run after the
 # entries' tests: these append changes and restore what they change.
 
@@ -89,7 +103,7 @@ def test_an_unchanged_entry_is_not_logged_again(listed):
     assert {name: len(_changes(key)) for name, (_recipe, key) in listed.items()} == before
 
 
-def test_a_fact_that_arrives_later_lists_the_session_again(listed):
+def test_a_fact_that_arrives_later_lists_the_session_again(listed, prefix):
     """A rejected segment recorded after the first listing changes the entry,
     so that session alone is logged again; removing it logs it once more."""
     from wl_preproc.listing.stage import run_stage
@@ -98,7 +112,11 @@ def test_a_fact_that_arrives_later_lists_the_session_again(listed):
     _recipe, key = listed["runs"]
     _plain, other = listed["plain"]
     row = {**key, "system": "spikeglx", "file_path": "late/run_g9_t0.nidq.bin", "reason": "late"}
+    from wl_preproc.responder.sessions import list_sessions
+    from wl_preproc.schema import ingest
+
     before, other_before = len(_changes(key)), len(_changes(other))
+    cursor = max(ingest.SessionChange.to_arrays("change_seq"))
     core.RejectedSegment.insert1(row)
     try:
         run_stage()
@@ -107,6 +125,8 @@ def test_a_fact_that_arrives_later_lists_the_session_again(listed):
         (core.RejectedSegment & row).delete_quick()
     run_stage()
     assert len(_changes(key)) == before + 2
+    # A reader holding the cursor from before sees that session alone.
+    assert [entry["subject"] for entry in list_sessions(cursor, prefix=prefix)["sessions"]] == ["sllist1"]
 
 
 def test_a_pass_leaves_the_listing_to_a_process_holding_its_lock(listed, prefix):
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/responder/test_sessions_http.py tests/schema/test_session_listing.py tests/cli/test_schemas_export.py -q --tb=line -p no:cacheprovider`
Expected: 10 failed, 19 passed. `make_handler` takes no `sessions_list_fn`, so the eight HTTP tests that build one fail, and `wl_preproc.responder.sessions` does not exist, so the two database tests do. `test_a_host_without_the_listing_does_not_answer_it` passes, by design, and so does the schema export check, since nothing exports the new schema yet.

- [ ] **Step 3: Implement.** Apply the diffs and create the responder:

```diff
--- a/wl_preproc/responder/handler.py
+++ b/wl_preproc/responder/handler.py
@@ -197,6 +197,11 @@ _JOBS_PATH = "/jobs"
 # path that takes a query string (`?since=<cursor>`).
 _NWB_PATH = "/nwb"
 _NWB_ACTIVE_PATH = "/nwb/active"
+# Landed sessions (design spec
+# `2026-10-01-session-listing-and-run-requests-design.md` section 2): the
+# other path that takes `?since=<cursor>`.
+_SESSIONS_PATH = "/sessions"
+_CURSOR_PATHS = frozenset({_NWB_PATH, _SESSIONS_PATH})
 
 # The two known paths, mapped to which HTTP method each one answers -- used
 # by both do_GET and do_POST (and do_PUT's five aliases) to decide 404 (path
@@ -361,6 +366,7 @@ def make_handler(
     accept_fn: Callable[[JobRequest], dict],
     nwb_list_fn: Callable[[int | None], dict] | None = None,
     nwb_active_fn: Callable[[ActiveSetRequest], dict] | None = None,
+    sessions_list_fn: Callable[[int | None], dict] | None = None,
 ) -> type[BaseHTTPRequestHandler]:
     """Build a `BaseHTTPRequestHandler` subclass closing over `token` and the
     two callables. A fresh class per call -- not a module-level singleton --
@@ -424,12 +430,14 @@ def make_handler(
         _accept_fn = staticmethod(accept_fn)
         _nwb_list_fn = staticmethod(nwb_list_fn) if nwb_list_fn is not None else None
         _nwb_active_fn = staticmethod(nwb_active_fn) if nwb_active_fn is not None else None
+        _sessions_list_fn = staticmethod(sessions_list_fn) if sessions_list_fn is not None else None
         # This handler's route table: the two fixed endpoints, plus the NWB
         # ones when their callables were given.
         _paths = {
             **_PATH_METHODS,
             **({_NWB_PATH: "GET"} if nwb_list_fn is not None else {}),
             **({_NWB_ACTIVE_PATH: "PUT"} if nwb_active_fn is not None else {}),
+            **({_SESSIONS_PATH: "GET"} if sessions_list_fn is not None else {}),
         }
 
         # Review Important 5: BaseHTTPRequestHandler's version_string() joins
@@ -635,9 +643,10 @@ def make_handler(
             """
             path, _, query = self.path.partition("?")
             expected = self._paths.get(path)
-            # A query string is `/nwb`'s alone; anywhere else it is a path
-            # this host does not answer, exactly as before `/nwb` existed.
-            if expected is None or (query and path != _NWB_PATH):
+            # A query string is `/nwb`'s and `/sessions`' alone; anywhere
+            # else it is a path this host does not answer, exactly as before
+            # `/nwb` existed.
+            if expected is None or (query and path not in _CURSOR_PATHS):
                 self._send_json(404, {"error": "not found"})
                 return 404
             if expected != method:
@@ -652,7 +661,10 @@ def make_handler(
             if self._route_or_none("GET") is not None:
                 return
             if self.path.partition("?")[0] == _NWB_PATH:
-                self._get_nwb()
+                self._get_since(self._nwb_list_fn)
+                return
+            if self.path.partition("?")[0] == _SESSIONS_PATH:
+                self._get_since(self._sessions_list_fn)
                 return
             # Otherwise only _HEALTH_PATH answers GET (see _PATH_METHODS).
             try:
@@ -786,16 +798,17 @@ def make_handler(
             else:
                 self._send_json(200, {"activation": result, "accepted": True})
 
-        def _get_nwb(self) -> None:
-            """`GET /nwb[?since=<cursor>]`: 200 with the listing; 422 when
-            `since` is anything but one non-negative integer."""
+        def _get_since(self, list_fn: Callable[[int | None], dict]) -> None:
+            """`GET /nwb` or `GET /sessions`, `[?since=<cursor>]`: 200 with
+            the listing; 422 when `since` is anything but one non-negative
+            integer."""
             query = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
             values = query.get("since", [])
             if set(query) - {"since"} or len(values) > 1 or (values and not (values[0].isascii() and values[0].isdigit())):
                 self._send_json(422, {"error": "the only parameter is since, one non-negative integer"})
                 return
             try:
-                payload = self._nwb_list_fn(int(values[0]) if values else None)
+                payload = list_fn(int(values[0]) if values else None)
             except Exception as exc:  # noqa: BLE001 -- see module docstring's table
                 self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
                 return
```

```python
"""What `GET /sessions` does (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 2.1).

`handler.py` owns HTTP and imports no DataJoint; this module owns the
database, as `nwb.py` does for `GET /nwb`. **It never writes**: the daemon's
listing stage is `ingest.SessionChange`'s one writer. An entry is the session
as it stands now, assembled by the same `session_entry` the stage hashes.
"""

from __future__ import annotations

from wl_preproc.contracts.protocol import SessionListing
from wl_preproc.schema import DEFAULT_PREFIX


def list_sessions(since: int | None, prefix: str = DEFAULT_PREFIX) -> dict:
    """Every session whose entry changed after `since` (all of them when
    None), in the order of their latest change, and the cursor to send next."""
    from wl_preproc.listing.entry import session_entry
    from wl_preproc.schema import core, ephys, ingest, timebase

    for module in (ingest, core, ephys, timebase):
        module.activate(prefix=prefix)
    changes = ingest.SessionChange if since is None else ingest.SessionChange & f"change_seq > {int(since)}"
    latest: dict[tuple, int] = {}
    for change in changes.proj("subject", "session_datetime").to_dicts():
        session = (change["subject"], change["session_datetime"])
        latest[session] = max(latest.get(session, 0), change["change_seq"])
    cursor = max(latest.values(), default=since or 0)
    sessions = [session_entry({"subject": subject, "session_datetime": moment})
                for (subject, moment), _seq in sorted(latest.items(), key=lambda item: item[1])]
    return SessionListing.model_validate({"cursor": cursor, "sessions": sessions}).model_dump(mode="json")
```

```diff
--- a/wl_preproc/responder/server.py
+++ b/wl_preproc/responder/server.py
@@ -143,6 +143,7 @@ from pathlib import Path
 
 from wl_preproc.responder import health, jobs
 from wl_preproc.responder import nwb as nwb_endpoints
+from wl_preproc.responder import sessions as sessions_endpoint
 from wl_preproc.responder.handler import ConflictError, make_handler
 from wl_preproc.schema import DEFAULT_PREFIX
 from wl_preproc.schema.request import KeyReuseError, SupersedeConflict
@@ -228,8 +229,15 @@ def serve(
         with lock:
             return nwb_endpoints.set_active(request, prefix=prefix)
 
+    # GET /sessions (design spec
+    # `2026-10-01-session-listing-and-run-requests-design.md` section 2),
+    # under the same lock.
+    def locked_sessions_list_fn(since):
+        with lock:
+            return sessions_endpoint.list_sessions(since, prefix=prefix)
+
     handler_cls = make_handler(token, locked_health_fn, locked_accept_fn, locked_nwb_list_fn,
-                               locked_nwb_active_fn)
+                               locked_nwb_active_fn, locked_sessions_list_fn)
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
-from wl_preproc.contracts.protocol import ActiveSetRequest, HealthResponse, JobRequest, NwbListing
+from wl_preproc.contracts.protocol import ActiveSetRequest, HealthResponse, JobRequest, NwbListing, SessionListing
 from wl_preproc.contracts.sidecar import BehaviorCameraSidecar
 
 # `wl_preproc.schema.__init__` is deliberately import-cheap (a constant and
@@ -41,6 +41,7 @@ EXPORTED_MODELS: dict[str, type[BaseModel]] = {
     "nwb_description": NwbDescription,
     "active_set_request": ActiveSetRequest,
     "nwb_listing": NwbListing,
+    "session_listing": SessionListing,
 }
 
 
```

Run `.venv/bin/python -m wl_preproc.cli.main schemas export --out docs/schemas`. Expected: one new file, `docs/schemas/session_listing.json`, and no other change.

````diff
--- a/docs/ops/lab-host-protocol.md
+++ b/docs/ops/lab-host-protocol.md
@@ -493,6 +493,49 @@ read under the same `Content-Length` rules as `POST /jobs`. The answer is `202`
 are kept: a dataset may be marked active before its files exist, and a file in the set
 publishes straight to the fast share.
 
+## `GET /sessions`
+
+*Added 2026-10-01 with the landed-session listing
+(`docs/superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md` section 2).*
+Lists every landed session whose entry changed after a cursor: its runs, the blocks inside them,
+the SpikeGLX segments and probes it recorded, and its flags. It is how wl.works learns a
+session's measured runs, to make the session's runs from them and to fire its canonical NWB.
+
+```
+GET /sessions?since=<cursor>
+Authorization: Bearer <token>
+```
+
+- **`since`** is optional, one non-negative integer, as for `GET /nwb`. Without it, every
+  listed session is listed.
+- **The response** is [`docs/schemas/session_listing.json`](../schemas/session_listing.json):
+  `{"cursor": <int>, "sessions": [...]}`. Send `cursor` as the next `since`; it only increases.
+- **A session is listed** once the daemon has read its event codes, and again whenever its entry
+  changes: a timing tier computed later, a probe census, a rejected segment. **Each entry is the
+  session as it stands now.**
+- **Each session:**
+  - `subject`, `session_datetime`, and `session_name`, the rig's `YYYY-MM-DD_NN`;
+  - `tier`, the timing tier (`A` to `D`, `D` quarantined), or `null` until computed; and
+    `rejected_segments`, each file this host could not use, with its reason;
+  - `runs`, measured from the recording's `RUN_START` (`0x8006`) and `RUN_END` (4): `run_number`
+    (wl-xcon's `run_in_session`), `task_code`, `task` (wl-xcon's name for it), `start_s` and
+    `end_s` on the recording's clock, `closed`, wl-xcon's `stopped_because` and `stop_kind`,
+    `segments` (the barcodes of the SpikeGLX segments it spans), and `blocks`;
+  - each block: `block_number` (in the session), `block_in_run`, `block_type`, `start_s`,
+    `end_s`, `closed`, `n_trials`;
+  - `segments`, each SpikeGLX file once: `segment_barcode`, `start_s`, `end_s`, and per probe its
+    `serial`, `part_number`, `probe_type`, site map (`electrode_config_hash`, `n_electrodes`,
+    `electrodes` as SpikeGLX electrode numbers), its `~imroTbl` verbatim as `imro_table`, and
+    any `problem`. **Runs and segments do not align**: a bank change needs a SpikeGLX restart,
+    which is a new segment, and a run need not stop for it;
+  - `probes`, every serial the recording names;
+  - `flags`, each a `code`, a `message`, and the `run_number` or `block_number` it is about:
+    `waiting_for_run_markers` (no measured run yet), `repeated_run_number` and
+    `repeated_block_number` (a crash restart before wl-xcon's XC-026; only the first is
+    listed), `run_without_block`, `bank_change_in_run`, `block_type_unknown`, `task_unknown`,
+    and `block_outside_runs` (a block in no measured run: its run's `RUN_START` was lost).
+    **None of them blocks anything here.**
+
 ---
 
 ## Status codes
@@ -893,6 +936,7 @@ directory so a drifted export fails the build:
 - [`docs/schemas/nwb_listing.json`](../schemas/nwb_listing.json) and
   [`docs/schemas/nwb_description.json`](../schemas/nwb_description.json), `GET /nwb`
 - [`docs/schemas/active_set_request.json`](../schemas/active_set_request.json), `PUT /nwb/active`
+- [`docs/schemas/session_listing.json`](../schemas/session_listing.json), `GET /sessions`
 
 These are what wl.works' contract tests should validate against, and they are why this
 protocol needed no OpenAPI-generating web framework on the box that holds every session's
````

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 29 passed.

Then every HTTP and CLI suite: `.venv/bin/python -m pytest tests/responder tests/schema/test_session_listing.py tests/cli tests/test_cli_guardrails.py -q -p no:cacheprovider`. Expected: 396 passed.

- [ ] **Step 5: Mutation checks.**
  - T6a (`handler.py`): only `/nwb` takes a query string [`test_the_listing_is_behind_the_token_and_takes_an_optional_cursor`, `test_a_cursor_that_is_not_one_non_negative_integer_is_422[since=x]`, `test_a_cursor_that_is_not_one_non_negative_integer_is_422[since=-1]`, `test_a_cursor_that_is_not_one_non_negative_integer_is_422[since=1&since=2]`, `test_a_cursor_that_is_not_one_non_negative_integer_is_422[other=1]`, `test_a_cursor_that_is_not_one_non_negative_integer_is_422[since=]`].
  - T6b (`sessions.py`): the cursor never advances [`test_get_sessions_lists_each_logged_session_as_it_stands_and_its_cursor`].
  - T6c: `since` is ignored [`test_get_sessions_lists_each_logged_session_as_it_stands_and_its_cursor`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/responder/sessions.py wl_preproc/responder/handler.py wl_preproc/responder/server.py wl_preproc/cli/main.py docs/schemas/session_listing.json docs/ops/lab-host-protocol.md tests/responder/test_sessions_http.py tests/schema/test_session_listing.py
git commit -m "feat(responder): GET /sessions lists each landed session whose entry changed after a cursor, as it stands now -- its runs, blocks, segments, probes and flags; documented, and its schema exported

<trailer lines>"
```

---

### Task 7: The records, and the full suite

**Files:**
- Modify: `docs/pending-wl-works-amendments.md`, `docs/pending-wl-xcon-amendments.md`, `docs/superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md`, `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-10-01-session-listing.md`

- [ ] **Step 1: What wl.works and wl-xcon are told.** Apply:

```diff
--- a/docs/pending-wl-works-amendments.md
+++ b/docs/pending-wl-works-amendments.md
@@ -58,6 +58,19 @@ only its landed-session poll writes. Its grid reads a landed session with no mea
    `animal_session_block` through `works_block_id`. They should join each run to
    `animal_session_run`. A dated note is added there; the code changes with the joint design.
 
+**Designed 2026-10-01** ([`specs/2026-10-01-session-listing-and-run-requests-design.md`](superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md),
+approved by the requester), and wl.works told the same day. **Ask 1 is BUILT** (its Plan A):
+`GET /sessions?since=<cursor>`, documented in `ops/lab-host-protocol.md` and exported as
+`docs/schemas/session_listing.json`. Vendor that schema once this is on `main`.
+- **Each session:** its key and rig session name; `tier` and `rejected_segments`; its runs, each
+  with wl-xcon's task and stop reason, the barcodes of the SpikeGLX segments it spans, and its
+  blocks (number in the session and in the run, block type, times, `closed`, trial count); the
+  segments once each, with every probe's serial, site map, `electrodes` (SpikeGLX electrode
+  numbers) and `imro_table` (the segment's `~imroTbl`, verbatim, for its montage-plan
+  comparison); the serials; and its flags.
+- **The block type** comes from the rig record's `block`, joined by `trial_number`, as asked.
+- **Asks 3 and 4, and item 5,** are the same spec's Plan B, which follows.
+
 ---
 
 # OPEN — wl.works fires the canonical NWB, and regenerates it by naming what it replaces
```

```diff
--- a/docs/pending-wl-xcon-amendments.md
+++ b/docs/pending-wl-xcon-amendments.md
@@ -48,6 +48,14 @@ block is a stretch of trials under one block type (spec §0).
 - **A `TRIAL_NUMBER` cut by a crash:** the decoder's framing is frozen. One or two trials are lost
   and the session falls to tier D; this is recorded as open beside its XC-199.
 
+**Noted, 2026-10-01: what this repository now reads from wl-xcon's record** (the session listing,
+[`specs/2026-10-01-session-listing-and-run-requests-design.md`](superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md)
+§2.3). Nothing is asked; **say so before renaming any of these:**
+- `xcon/runs.jsonl`: each row's `event` and `run`; the start row's `task` and, once it carries it,
+  `run_in_session`; the end row's `stopped_because` and `stop_kind`;
+- `xcon/config.json`: `subject`, since the run rows carry none;
+- `xcon/trials.jsonl`: `block`, each block's type, joined by `trial_number`.
+
 ---
 
 # OPEN — the stream must carry each trial's number and condition
```

- [ ] **Step 2: The spec's amendments.** Apply:

```diff
--- a/docs/superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md
+++ b/docs/superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md
@@ -310,3 +310,43 @@ Each flag is a code and a sentence, listed per session or per run:
   the retirements.
 
 Each is its own branch, merged on the requester's word.
+
+---
+
+## Amendments, 2026-10-01, made while proving Plan A
+
+Plan A (`plans/2026-10-01-session-listing.md`) was proven in code before it was written. These
+settle what the sections above left open; §0 stands.
+
+1. **Each SpikeGLX segment is listed once per session,** and each run names the barcodes of the
+   segments it spans (§2.2 listed them under each run). The information is the same, without
+   repeating a segment's sites under every run it touches.
+2. **`tier` and `rejected_segments` are fields, not flags** (§2.4): every entry has them, and a
+   flag is a finding that may or may not be there. `tier` is null until `TimingProvenance` is
+   computed.
+3. **A new flag, `block_outside_runs`**: a block in no measured run, when the session has runs.
+   Its run's `RUN_START` was lost, or it was strobed outside a run. With no runs, the session is
+   already `waiting_for_run_markers`, and the flag is not raised.
+4. **`electrodes` are SpikeGLX electrode numbers** for NP1015, NP1022, NP1030 and NP1032
+   (§8 item 8, measured with probeinterface 0.3.2: channel 0 in bank 2 is electrode 768, bank x
+   384 + channel). Only the saved channels are listed.
+5. **A block's closure is stored,** as a `trial.Block.Attribute` row `closed`, beside its
+   `block_type`: `trial.Block` held no closure, and the listing reports it.
+6. **A rig-record value longer than its column is cut to it,** and an `~imroTbl` longer than
+   10,240 characters is not kept and is named as the census probe's problem. Either, kept whole,
+   would fail its stage on every pass.
+7. **`runs.jsonl`'s rows carry no subject,** so its `config.json` is read: a record naming another
+   subject is not read. The generator writes both files only when its blocks are wrapped in runs.
+8. **The listing stage's changes count into the pass's `populated`,** as the link stage's do, and
+   its lock is `nwb/lock.py::exclusive(prefix, "listing")`, beside the NWB stages' lock.
+9. **§8's items for Plan A, answered:**
+   - **1:** `RUN_START`'s run number is `run` + 1: wl-xcon's session-levels spec calls `run` the
+     0-based run in session, and its start rows gain `run_in_session`, which is used when present;
+   - **2:** `trial.Block.Attribute` takes `block_type` and `closed` (`attribute_value` is
+     `varchar(2000)`);
+   - **5:** the rig's session name is the last part of `Ingestion.session_dir`; a generated
+     session's is its `session_id`;
+   - **6:** both named locks are taken with `GET_LOCK(name, 0)` and never waited for, so one
+     pass holding both cannot deadlock;
+   - **8:** as amendment 4.
+
```

- [ ] **Step 3: The checkpoint, `wl.yaml` and the handoff.** Apply:

```diff
--- a/docs/CHECKPOINT.md
+++ b/docs/CHECKPOINT.md
@@ -577,6 +577,19 @@ requester chose to merge the same day; true when written.*
 >    on `main`, so tell it when this merges**, and XC-026 comes before
 >    January. See `docs/handoffs/2026-10-01-runs-and-trials.md`.
 >
+>    **The landed-session listing, `GET /sessions` (Plan A of pieces 2
+>    and 3), is BUILT on `spec/session-listing-and-run-requests`
+>    (2026-10-01), NOT merged as written** (spec
+>    `superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md`).
+>    **The requester's decisions that day:** a canonical file keeps every run
+>    of its montage; a repeated run number lists the first and flags the
+>    session; a derivative selects whole runs. Each session lists its runs
+>    with wl-xcon's task and stop reason, the blocks under each, the SpikeGLX
+>    segments with every probe's site map and raw `~imroTbl`, and its flags;
+>    the daemon's listing stage logs a change whenever an entry changes.
+>    **Plan B, requests that name runs and the NWB description at version 3,
+>    follows.** See `docs/handoffs/2026-10-01-session-listing.md`.
+>
 > **Deferred minors: DONE 2026-09-26, both lists.** The gap-aware branch's
 > items 1–3 (`8af4278`; `eye/detect/validity.py` now cites commit `7d4a00f`
 > in place of a "finding H2" no document named) and all six parked
```

```diff
--- a/wl.yaml
+++ b/wl.yaml
@@ -170,6 +170,13 @@ status:
     only its own trials; and repeated or too-large trial numbers are named
     rather than dropped (design spec `2026-10-01-runs-and-trials-design.md`;
     handoff `docs/handoffs/2026-10-01-runs-and-trials.md`).
+    The landed-session listing, `GET /sessions`, is BUILT on
+    `spec/session-listing-and-run-requests` (2026-10-01, NOT merged as
+    written): each landed session's runs with wl-xcon's task and stop reason,
+    the blocks under each, the SpikeGLX segments with every probe's site map
+    and raw ~imroTbl, and its flags, re-listed whenever its entry changes
+    (design spec `2026-10-01-session-listing-and-run-requests-design.md`, Plan
+    A; handoff `docs/handoffs/2026-10-01-session-listing.md`).
   next: >-
     **Hardware status as of 2026-09-19, stated by the requester at session
     close: the rig is NOT ready and the compute machine is NOT assembled.**
```

Run `wl-check` on its own. Expected: `wl.yaml: no findings`.

Create `docs/handoffs/2026-10-01-session-listing.md`:

```markdown
# The landed-session listing, `GET /sessions`

**Plan A of pieces 2 and 3, designed together.** Piece 1, runs and trials, is merged
(`b0f8b52`). Plan B, requests that name runs and the NWB description at version 3, follows; piece
4, a metadata-only rebuild, after it.
- **Branch:** `spec/session-listing-and-run-requests`, forked from `main` at `7e49cc9`.
- **Spec:** `docs/superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md`
  (`89ad4e9`, amended `03c6784` for wl.works' site-set note). It is amended again in this
  branch's last commit.
- **Plan:** `docs/superpowers/plans/2026-10-01-session-listing.md`.
- **The requester's choices:** design the two pieces together; a canonical file keeps every run of
  its montage; a repeated run number lists the first and flags the session; a derivative selects
  whole runs.

Every line of the plan was proven in a scratch worktree before the plan was written.

---

## 1. What was built

- **wl-xcon's run record is read** (`events/rigruns.py`): each run's task from its start row, and
  its stop reason from its end row, numbered by `run_in_session` or `run` + 1.
- **The event stage keeps it**, in a new `core.RunRecord`, and each block's closure and block type
  as `trial.Block.Attribute` rows, once, before the raw files are archived.
- **The probe census keeps each segment's `~imroTbl` verbatim** (`ephys.ProbeCensus.Probe.imro_table`).
- **One session's entry** (`listing/entry.py`): its runs, the blocks under each, the SpikeGLX
  segments each spans with every probe's site map and table, its serials, `tier`,
  `rejected_segments`, and eight flags. It is built from rows, so every flag is tested without a
  database.
- **The listing stage** (`listing/stage.py`) appends an `ingest.SessionChange` whenever an entry's
  digest changes. It is the log's one writer, under its own named lock.
- **`GET /sessions?since=<cursor>`** (`responder/sessions.py`) lists each changed session as it
  stands now. It is documented in `ops/lab-host-protocol.md` and exported as
  `docs/schemas/session_listing.json`.
- **The generator** writes wl-xcon's run record and `config.json` when its blocks are wrapped in
  runs.

## 2. What the other repositories must do

- **wl.works** (`pending-wl-works-amendments.md`): vendor `session_listing.json` once this is on
  `main`. Compare a planned IMRO file with each segment's `imro_table`, using the reader it has
  for both.
- **wl-xcon** (`pending-wl-xcon-amendments.md`): nothing is asked. It is told which fields of
  `runs.jsonl`, `config.json` and `trials.jsonl` are now read, so that it says so before renaming
  one.

## 3. Still open

- **Plan B:** canonical requests that name runs, per-probe run lists, the check on arrival, the
  NWB description at version 3, and the retirements of `core.Block` and `block_agreement`.
- **A session deleted after it was listed** has no "removed" entry. `GET /nwb` leaves the same gap.
- **No page size**, as for `GET /nwb`.
- **A development database** redeclares `ephys.ProbeCensus`, which gains `imro_table`.

## 4. The rulings

Each is a dated amendment in the spec:
1. **Segments are listed once per session,** and runs name theirs by barcode.
2. **`tier` and `rejected_segments` are fields;** flags are findings.
3. **A new flag, `block_outside_runs`.**
4. **`electrodes` are SpikeGLX electrode numbers** for the four models wl.works reads.
5. **A block's closure is stored.**
6. **Over-long values are cut or named,** never a failed stage.
7. **`config.json` stands in for the subject** the run rows lack.
8. **The listing stage counts into `populated`,** under its own lock.
9. **§8's items for Plan A, answered.**
```

Add §5, the measured counts, and §6, the final review, as execution measures them.

- [ ] **Step 4: The full suite, once, on both interpreters.** With the BMD and NSLR references set and `WLPP_OHDPI_REFERENCE` unset, as CI has it:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider > .superpowers/sdd/2026-10-01-session-listing/full311.log 2>&1; echo "311 exit $?"
~/.cache/wl-preproc-venv313/bin/python -m pytest -q -p no:cacheprovider > .superpowers/sdd/2026-10-01-session-listing/full313.log 2>&1; echo "313 exit $?"
```

Expected: `311 exit 0` and `313 exit 0`, **2096 passed, 25 skipped, 1 deselected, 1 xfailed** on 3.11 and **2095 passed, 27 skipped, 1 xfailed** on 3.13, proven with every task applied. The plan adds 39 tests (2057 and 2056 at `main`'s `7e49cc9`).

- [ ] **Step 5: Commit**

```bash
git add docs/pending-wl-works-amendments.md docs/pending-wl-xcon-amendments.md docs/superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md docs/CHECKPOINT.md wl.yaml docs/handoffs/2026-10-01-session-listing.md
git commit -m "docs: the landed-session listing -- built, and what wl.works and wl-xcon are told; the spec's amendments, the checkpoint, wl.yaml and the handoff

<trailer lines>"
```

---

## Rulings made while planning

Each is also a dated amendment in the spec (Task 7), of the same number, and each names its cost if wrong.

1. **Each SpikeGLX segment is listed once per session,** and runs name theirs by barcode. *Cost:* a reader joins two lists rather than reading one.
2. **`tier` and `rejected_segments` are fields, not flags.** *Cost:* none; a flag is a finding that may be absent, and these are always present.
3. **A new flag, `block_outside_runs`,** raised only when the session has runs. *Cost:* a contract value the spec did not name; wl.works shows an unknown code as a hint.
4. **`electrodes` are SpikeGLX electrode numbers,** saved channels only. *Cost:* a file saving fewer channels than its table lists has fewer `electrodes`; `imro_table` is the comparison wl.works was told to use.
5. **A block's closure is stored as a block attribute.** *Cost:* none known.
6. **Over-long values are cut (`core.RunRecord`) or not kept and named (`imro_table`).** *Cost:* a 300-character task name is listed cut to 255.
7. **`config.json` stands in for the subject `runs.jsonl` lacks;** the generator writes both only when runs are on. *Cost:* a record without `config.json` is read unchecked.
8. **The listing stage counts into `populated`,** under its own lock beside the NWB stages'. *Cost:* the daily report does not separate listed sessions from computed keys.
9. **§8's items for Plan A are answered as above.** *Cost:* none.
