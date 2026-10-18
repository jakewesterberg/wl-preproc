# Carrying a Session's Kind, Calibration and Accepted Warnings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Read what wl-xcon's engine build B writes into every session's record (its kind, its display
calibrations and the warnings it accepted) and carry it into the database, the session listing and the NWB
file with its description, acting on none of it.

**Architecture:** A reader beside the run record's (`events/rigsession.py`) feeds three new tables in the
event stage; the listing's entry and the NWB builder read those tables. Who accepted a warning stays in the
database and, by name and wl.works account, in the listing; the NWB file names no one.

**Tech Stack:** Python 3.11/3.13, DataJoint 2.3 on MySQL (testcontainers in tests), pydantic contracts
exported as JSON Schema, pynwb/hdmf.

**Spec:** `docs/superpowers/specs/2026-10-14-session-kind-and-warnings-design.md` (approved 2026-10-14;
amendments 1-5 are Task 5's, made while proving this plan).

**Proven before written.** Every step below was run on a scratch branch and replayed from this plan's own
text to an identical tree: 28 mutation checks caught, and the full suite on both interpreters
as Task 5 states.

## Global Constraints

- **Carry only** (spec §0, decision 1): nothing in this pipeline behaves differently by `session_kind`.
- **No person in the NWB file, no token in the listing** (spec §0, decision 3): the database keeps who
  accepted a warning as wl-xcon wrote it; the listing gives `kind`, `name` and `account` only; the file
  and its description name no one.
- **No existing table changes** (spec §0, decision 4): three new tables, declared on activation.
- **wl-xcon's formats are as read on its `main` at `b2c2850`** (spec §1); `SESSION_KINDS` is
  `("training", "piloting", "recording")`.
- **Python 3.11 and 3.13**: the full suite runs once on each, at the end (Task 5).
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code,
  data or tables; leave the stray `wl-preproc` symlink at the repository root out of every commit.
- **Commands run from the repository root.** In a git worktree, `.venv/bin/python` is the main checkout's;
  every test command sets `PYTHONPATH=$PWD` and `PYTHONDONTWRITEBYTECODE=1` (a stale `.pyc` can defeat a
  mutation check).
- **Patches apply with `git apply` and must apply cleanly**; each step's patch is the exact change.
- **`docs/schemas` is regenerated** with `python -m wl_preproc.cli.main schemas export --out docs/schemas`
  whenever a contract model changes: CI's "Exported schemas are current" step fails otherwise.
- **Every commit ends with:**
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
  ```

## Review Focus

The five inputs the spec implies and no task's tests otherwise exercise, most likely first; each is pinned by
a test in the task that owns the code:
1. **A warning for a run with no start row in `runs.jsonl`** (wl-xcon's run 7 of two): stored with no run
   number, never a failed stage. `test_a_warning_for_a_run_with_no_start_row_has_no_run_number` (Task 2).
2. **A `config.json` that names no subject**: read as this session's, as the run record is.
   `test_a_config_naming_no_subject_is_read_as_this_sessions` (Task 1).
3. **`config.json` and `warnings.jsonl` without `runs.jsonl`**: the kind and the warnings kept, no warning
   joined to a run, the missing run record the one problem.
   `test_a_record_without_its_runs_keeps_the_kind_and_unjoined_warnings` (Task 2).
4. **A warning's sentence beyond ASCII**: stored as written.
   `test_a_warnings_sentence_in_any_script_is_kept_as_written` (Task 2).
5. **A warning whose `accepted_in` is not a list**: acceptable in no kind, never split into characters.
   `test_a_warning_whose_kinds_are_not_a_list_is_acceptable_in_none` (Task 1).

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
| `wl_preproc/events/rigsession.py` (new) | read `config.json`'s kind and calibration and `warnings.jsonl` | 1 |
| `wl_preproc/events/rigruns.py` | each run's calibration, the 0-based run map, problems naming their file | 1, 3 |
| `wl_preproc/schema/core.py` | `SessionRecord`, `RunCalibration`, `AcceptedWarning` | 2 |
| `wl_preproc/schema/events.py` | write them in `populate_session`, problems beside the run record's | 2 |
| `wl_preproc/synth/recipe.py`, `synth/peripherals.py` | the generator's `rig_record` | 2 |
| `wl_preproc/contracts/protocol.py`, `listing/entry.py` | the listing's new fields | 3 |
| `wl_preproc/nwb/rig_record.py` (new), `nwb/gather.py`, `nwb/describe.py`, `nwb/intervals.py`, `nwb/build.py`, `contracts/nwb_description.py` | the file's module, the runs' calibration, the description | 4 |
| the spec, `docs/handoffs/2026-10-14-session-kind.md` (new), `docs/pending-wl-works-amendments.md`, `docs/pending-wl-xcon-amendments.md`, `HANDOVER-wl-expcontroller.md`, `docs/CHECKPOINT.md`, `wl.yaml` | the records | 5 |

---

### Task 1: Reading what wl-xcon's record says the session was

The reader, and each run's calibration and number map in the run record's reader (spec §2).

**Files:**
- Create: `wl_preproc/events/rigsession.py`
- Modify: `wl_preproc/events/rigruns.py`
- Test: `tests/events/test_rigsession.py` (new), `tests/events/test_rigruns.py`

**Interfaces:**
- Consumes: `events/rigruns.py::CONFIG_NAME`; `contracts/paths.py::XCON_DIRNAME`.
- Produces:
  - `events/rigsession.py::read_rig_session(session_dir: Path, subject: str) -> RigSession | None`
    (`None` when `xcon/config.json` is absent);
  - `RigSession(kind: str | None, calibration: RigCalibration | None, warnings: tuple[RigWarning, ...],
    problems: tuple[str, ...])`;
  - `RigCalibration(id: str | None, standard: bool | None, measured_on: str | None, cones: dict | None)`;
  - `RigWarning(code: str, detail, accepted_in: tuple[str, ...], session_kind, how, run_index: int | None,
    at: float | None, at_local, by_kind, by_name, by_account, by_issuer, by_token_id)`, every unnamed field
    `str | None`;
  - `WARNINGS_NAME = "warnings.jsonl"`, `SESSION_KINDS = ("training", "piloting", "recording")`;
  - `events/rigruns.py`: `RigRun.calibration: str | None = None` (last field) and
    `RigRuns.run_numbers: dict[int, int]` (wl-xcon's 0-based `run` -> the run's number).

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/events/test_rigruns.py b/tests/events/test_rigruns.py
index 89eafe5..d67a9df 100644
--- a/tests/events/test_rigruns.py
+++ b/tests/events/test_rigruns.py
@@ -91,3 +91,25 @@ def test_a_run_named_twice_keeps_its_first_rows_and_says_so(tmp_path):
     assert record.runs == (RigRun(1, "rf_map", "every block is finished", "completed"),)
     assert record.problems == ("line 3: run 0 has a second start row; the first is kept",
                                "line 4: run 0 has a second end row; the first is kept")
+
+
+def test_a_runs_calibration_is_its_start_rows(tmp_path):
+    """wl-xcon's engine build B: each start row names the display calibration
+    the run ran against (design spec `2026-10-14-session-kind-and-warnings-design.md`
+    section 1); a row written before it names none."""
+    from wl_preproc.events.rigruns import read_rig_runs
+
+    rows = [_start(0, "rf_map", calibration="rig-a-2026-10-01"), _end(0, "every block is finished", "completed"),
+            _start(1, "rf_map")]
+    runs = read_rig_runs(_record(tmp_path, rows), "pico").runs
+    assert [run.calibration for run in runs] == ["rig-a-2026-10-01", None]
+
+
+def test_each_of_wl_xcons_runs_maps_to_its_number(tmp_path):
+    """A warning names its run as wl-xcon counts it, from 0; the run's number
+    is the start row's `run_in_session`, or that `run` plus one before it."""
+    from wl_preproc.events.rigruns import read_rig_runs
+
+    rows = [_start(0, "rf_map", run_in_session=7), _end(0, "every block is finished", "completed"),
+            _start(1, "rf_map")]
+    assert read_rig_runs(_record(tmp_path, rows), "pico").run_numbers == {0: 7, 1: 2}
diff --git a/tests/events/test_rigsession.py b/tests/events/test_rigsession.py
new file mode 100644
index 0000000..460d874
--- /dev/null
+++ b/tests/events/test_rigsession.py
@@ -0,0 +1,168 @@
+"""What wl-xcon's record says the session was: its kind and display
+calibration, from `xcon/config.json`, and the warnings it accepted, from
+`xcon/warnings.jsonl` (design spec `2026-10-14-session-kind-and-warnings-design.md`
+sections 1 and 2)."""
+
+from __future__ import annotations
+
+import json
+
+CALIBRATION = {"id": "rig-a-2026-10-01", "standard": False, "measured_on": "2026-10-01",
+               "cones": {"observer": {"name": "CIE2006_10"}, "via": "spectra", "luminance": None, "dkl": "unit"}}
+
+
+def _record(tmp_path, config=None, warnings=None):
+    (tmp_path / "xcon").mkdir()
+    if config is not None:
+        (tmp_path / "xcon" / "config.json").write_text(
+            config if isinstance(config, str) else json.dumps(config), encoding="utf-8")
+    if warnings is not None:
+        (tmp_path / "xcon" / "warnings.jsonl").write_text("\n".join(warnings) + "\n", encoding="utf-8")
+    return tmp_path
+
+
+def _warning(code, run=None, **fields):
+    row = {"code": code, "detail": f"the {code} sentence", "accepted_in": ["training", "piloting"],
+           "session_kind": "training", "by": {"kind": "box", "name": "jw"}, "at": 1760000000.5,
+           "at_local": "2026-10-09T09:00:00+02:00 Europe/Berlin", "how": "open", "run": run}
+    return json.dumps({**row, **fields})
+
+
+def test_the_kind_and_the_calibration_are_read_from_config(tmp_path):
+    from wl_preproc.events.rigsession import RigCalibration, read_rig_session
+
+    session = read_rig_session(_record(tmp_path, {"subject": "pico", "session_kind": "training",
+                                                  "calibration": CALIBRATION}), "pico")
+    assert session.kind == "training"
+    assert session.calibration == RigCalibration(id="rig-a-2026-10-01", standard=False, measured_on="2026-10-01",
+                                                 cones=CALIBRATION["cones"])
+    assert (session.warnings, session.problems) == ((), ())
+
+
+def test_a_config_written_before_build_b_has_no_kind_and_no_problem(tmp_path):
+    """wl-xcon's engine build B added both fields; an older `config.json`
+    has neither, which is not a problem: they were not recorded."""
+    from wl_preproc.events.rigsession import read_rig_session
+
+    session = read_rig_session(_record(tmp_path, {"subject": "pico", "session_id": "2026-09-01_01"}), "pico")
+    assert (session.kind, session.calibration, session.problems) == (None, None, ())
+
+
+def test_a_calibration_that_would_not_load_is_null(tmp_path):
+    """wl-xcon writes `null` when the rig's record would not load."""
+    from wl_preproc.events.rigsession import read_rig_session
+
+    session = read_rig_session(_record(tmp_path, {"subject": "pico", "session_kind": "piloting",
+                                                  "calibration": None}), "pico")
+    assert (session.kind, session.calibration, session.problems) == ("piloting", None, ())
+
+
+def test_a_kind_outside_the_three_is_a_problem_and_no_kind(tmp_path):
+    from wl_preproc.events.rigsession import read_rig_session
+
+    session = read_rig_session(_record(tmp_path, {"subject": "pico", "session_kind": "rehearsal"}), "pico")
+    assert session.kind is None
+    assert session.problems == ("config.json: session kind 'rehearsal' is not one of training, piloting, "
+                                "recording",)
+
+
+def test_a_calibration_that_is_not_an_object_is_a_problem(tmp_path):
+    from wl_preproc.events.rigsession import read_rig_session
+
+    session = read_rig_session(_record(tmp_path, {"subject": "pico", "calibration": "rig-a"}), "pico")
+    assert session.calibration is None
+    assert session.problems == ("config.json: calibration is neither an object nor null",)
+
+
+def test_every_accepted_warning_is_read_as_written(tmp_path):
+    from wl_preproc.events.rigsession import RigWarning, read_rig_session
+
+    member = {"kind": "member", "name": "J. W.", "account": "acct-1", "issuer": "https://wl.works",
+              "token_id": "tok-1"}
+    session = read_rig_session(_record(tmp_path, {"subject": "pico", "session_kind": "training"}, [
+        _warning("default-calibration"),
+        _warning("luminance-step", run=1, how="start", by=member),
+    ]), "pico")
+    assert session.warnings == (
+        RigWarning(code="default-calibration", detail="the default-calibration sentence",
+                   accepted_in=("training", "piloting"), session_kind="training", how="open", run_index=None,
+                   at=1760000000.5, at_local="2026-10-09T09:00:00+02:00 Europe/Berlin",
+                   by_kind="box", by_name="jw", by_account=None, by_issuer=None, by_token_id=None),
+        RigWarning(code="luminance-step", detail="the luminance-step sentence",
+                   accepted_in=("training", "piloting"), session_kind="training", how="start", run_index=1,
+                   at=1760000000.5, at_local="2026-10-09T09:00:00+02:00 Europe/Berlin",
+                   by_kind="member", by_name="J. W.", by_account="acct-1", by_issuer="https://wl.works",
+                   by_token_id="tok-1"),
+    )
+    assert session.problems == ()
+
+
+def test_a_warning_accepted_by_nobody_has_no_one(tmp_path):
+    """`by` is null for a warning `wlx run` accepted with no name given."""
+    from wl_preproc.events.rigsession import read_rig_session
+
+    (warning,) = read_rig_session(_record(tmp_path, {"subject": "pico"}, [
+        _warning("head-free", by=None, how="--accept-warnings")]), "pico").warnings
+    assert (warning.by_kind, warning.by_name, warning.how) == (None, None, "--accept-warnings")
+
+
+def test_a_warning_line_it_cannot_read_is_reported_by_file_and_line_and_left_out(tmp_path):
+    from wl_preproc.events.rigsession import read_rig_session
+
+    session = read_rig_session(_record(tmp_path, {"subject": "pico"}, [
+        _warning("default-calibration"), "{not json", json.dumps({"detail": "no code"}), json.dumps([1]),
+    ]), "pico")
+    assert [warning.code for warning in session.warnings] == ["default-calibration"]
+    assert session.problems == ("warnings.jsonl line 2: not JSON",
+                                "warnings.jsonl line 3: not a warning naming its code",
+                                "warnings.jsonl line 4: not a warning naming its code")
+
+
+def test_a_record_of_another_animal_is_not_read(tmp_path):
+    from wl_preproc.events.rigsession import read_rig_session
+
+    session = read_rig_session(_record(tmp_path, {"subject": "zorro", "session_kind": "recording"},
+                                       [_warning("default-calibration")]), "pico")
+    assert (session.kind, session.calibration, session.warnings) == (None, None, ())
+    assert session.problems == ("config.json names subject 'zorro', not 'pico', so its kind, calibration and "
+                                "warnings are not this session's",)
+
+
+def test_a_config_it_cannot_read_is_a_problem_and_its_warnings_are_still_read(tmp_path):
+    """As the run record is read when `config.json` is not: nothing there
+    says the record is another animal's."""
+    from wl_preproc.events.rigsession import read_rig_session
+
+    session = read_rig_session(_record(tmp_path, "{not json", [_warning("default-calibration")]), "pico")
+    assert session.kind is None
+    assert [warning.code for warning in session.warnings] == ["default-calibration"]
+    assert session.problems == ("config.json: not a JSON object",)
+
+
+def test_a_session_without_a_config_has_no_record(tmp_path):
+    """The record is the session's `config.json`: without one, wl-xcon did
+    not run the session, and its warnings, if any, are not read."""
+    from wl_preproc.events.rigsession import read_rig_session
+
+    assert read_rig_session(_record(tmp_path, warnings=[_warning("default-calibration")]), "pico") is None
+    assert read_rig_session(tmp_path / "nowhere", "pico") is None
+
+
+def test_a_config_naming_no_subject_is_read_as_this_sessions(tmp_path):
+    """As the run record is (`events/rigruns.py`): only a subject that differs
+    makes the record another animal's (Review Focus 2)."""
+    from wl_preproc.events.rigsession import read_rig_session
+
+    session = read_rig_session(_record(tmp_path, {"session_kind": "recording"}, [_warning("head-free")]), "pico")
+    assert (session.kind, [warning.code for warning in session.warnings], session.problems) == (
+        "recording", ["head-free"], ())
+
+
+def test_a_warning_whose_kinds_are_not_a_list_is_acceptable_in_none(tmp_path):
+    """Read, never repaired: `accepted_in` is kept only as wl-xcon writes it,
+    a list (Review Focus 5)."""
+    from wl_preproc.events.rigsession import read_rig_session
+
+    (warning,) = read_rig_session(_record(tmp_path, {"subject": "pico"}, [
+        _warning("head-free", accepted_in="training")]), "pico").warnings
+    assert warning.accepted_in == ()
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/events/test_rigsession.py tests/events/test_rigruns.py -q -p no:cacheprovider
```

Expected: `15 failed, 7 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/events/rigruns.py b/wl_preproc/events/rigruns.py
index ab8034b..d7c50c9 100644
--- a/wl_preproc/events/rigruns.py
+++ b/wl_preproc/events/rigruns.py
@@ -8,7 +8,9 @@ per event, `{"event": "start" | "end", "run": <0-based run in session>, ...}`:
 the start row carries the `task`, the end row `stopped_because` and
 `stop_kind`. Since wl-xcon's session-levels change a start row also carries
 `run_in_session`, the number `RUN_START` strobes; before it, that number is
-`run` + 1. A start row and its end row share `run`.
+`run` + 1. A start row and its end row share `run`. Since wl-xcon's engine
+build B a start row also names the display calibration the run ran against
+(design spec `2026-10-14-session-kind-and-warnings-design.md` section 1).
 
 **Read, never repaired.** A row that is not a JSON object naming its event and
 run is reported by line and left out. wl-xcon writes a run's end row on every
@@ -39,12 +41,16 @@ class RigRun:
     task: str | None
     stopped_because: str | None
     stop_kind: str | None
+    calibration: str | None = None  # the start row's display calibration id
 
 
 @dataclasses.dataclass(frozen=True, slots=True)
 class RigRuns:
     runs: tuple[RigRun, ...]
     problems: tuple[str, ...]
+    # wl-xcon's 0-based `run` -> the run's number, which joins an accepted
+    # warning, named by the former, to the measured run.
+    run_numbers: dict[int, int] = dataclasses.field(default_factory=dict)
 
 
 def _integer(value: object) -> bool:
@@ -94,12 +100,15 @@ def read_rig_runs(session_dir: Path, subject: str) -> RigRuns | None:
         run["seen"].add(row["event"])
         if row["event"] == "start":
             run["task"] = _text(row.get("task"))
+            run["calibration"] = _text(row.get("calibration"))
             if _integer(row.get("run_in_session")):
                 run["number"] = row["run_in_session"]
         else:
             run["stopped_because"] = _text(row.get("stopped_because"))
             run["stop_kind"] = _text(row.get("stop_kind"))
     runs = tuple(sorted((RigRun(number=run["number"], task=run.get("task"),
-                                stopped_because=run.get("stopped_because"), stop_kind=run.get("stop_kind"))
+                                stopped_because=run.get("stopped_because"), stop_kind=run.get("stop_kind"),
+                                calibration=run.get("calibration"))
                          for run in fields.values()), key=lambda run: run.number))
-    return RigRuns(runs=runs, problems=tuple(problems))
+    return RigRuns(runs=runs, problems=tuple(problems),
+                   run_numbers={index: run["number"] for index, run in sorted(fields.items())})
diff --git a/wl_preproc/events/rigsession.py b/wl_preproc/events/rigsession.py
new file mode 100644
index 0000000..adda391
--- /dev/null
+++ b/wl_preproc/events/rigsession.py
@@ -0,0 +1,155 @@
+"""What wl-xcon's record says the session was: its kind and the display
+calibration it opened with, from `xcon/config.json`, and the warnings it
+accepted, from `xcon/warnings.jsonl` (design spec
+`2026-10-14-session-kind-and-warnings-design.md` sections 1 and 2).
+
+**Carried, never acted on.** wl-xcon's engine build B writes `session_kind`
+(training, piloting or recording) and `calibration` into `config.json`, and one
+row per accepted warning into `warnings.jsonl` (`wl_xcon/record.py`,
+`ACCEPTED_WARNINGS`). Nothing in this pipeline behaves differently by kind;
+the published record says what the rig said.
+
+**Read, never repaired, by `events/rigruns.py`'s rules.** A warning line that
+is not a JSON object naming its `code` is reported by file and line and left
+out. A kind outside the three, or a calibration that is neither an object nor
+null, is reported and read as none. A `config.json` written before build B has
+neither field: not recorded, which is not a problem. A record whose
+`config.json` names another animal is not read at all.
+
+**The record is `config.json`.** A session without one was not run by wl-xcon,
+and has none, whatever else its `xcon/` folder holds.
+"""
+
+from __future__ import annotations
+
+import dataclasses
+import json
+from pathlib import Path
+
+from wl_preproc.contracts.paths import XCON_DIRNAME
+from wl_preproc.events.rigruns import CONFIG_NAME
+
+WARNINGS_NAME = "warnings.jsonl"
+# wl-xcon's `wl_xcon/findings.py::SESSION_KINDS`.
+SESSION_KINDS = ("training", "piloting", "recording")
+
+
+@dataclasses.dataclass(frozen=True, slots=True)
+class RigCalibration:
+    """The display calibration a session opened with: its id, whether it is
+    the sRGB standard nobody measured, the day it was measured (empty for the
+    standard), and how its cone colours became light, as given."""
+
+    id: str | None
+    standard: bool | None
+    measured_on: str | None
+    cones: dict | None
+
+
+@dataclasses.dataclass(frozen=True, slots=True)
+class RigWarning:
+    """One accepted warning, as wl-xcon wrote it. `run_index` is wl-xcon's
+    0-based run, null for one accepted at the session's open. `at` is the
+    rig's wall clock, its bookkeeping, not a time on the recording's clock."""
+
+    code: str
+    detail: str | None
+    accepted_in: tuple[str, ...]
+    session_kind: str | None
+    how: str | None
+    run_index: int | None
+    at: float | None
+    at_local: str | None
+    by_kind: str | None
+    by_name: str | None
+    by_account: str | None
+    by_issuer: str | None
+    by_token_id: str | None
+
+
+@dataclasses.dataclass(frozen=True, slots=True)
+class RigSession:
+    kind: str | None
+    calibration: RigCalibration | None
+    warnings: tuple[RigWarning, ...]
+    problems: tuple[str, ...]
+
+
+def _text(value: object) -> str | None:
+    return None if value is None else str(value)
+
+
+def _integer(value: object) -> int | None:
+    return value if isinstance(value, int) and not isinstance(value, bool) else None
+
+
+def _number(value: object) -> float | None:
+    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
+
+
+def _calibration(value: object, problems: list[str]) -> RigCalibration | None:
+    if value is None:
+        return None
+    if not isinstance(value, dict):
+        problems.append(f"{CONFIG_NAME}: calibration is neither an object nor null")
+        return None
+    standard = value.get("standard")
+    cones = value.get("cones")
+    return RigCalibration(id=_text(value.get("id")), standard=standard if isinstance(standard, bool) else None,
+                          measured_on=_text(value.get("measured_on")), cones=cones if isinstance(cones, dict) else None)
+
+
+def _warning(row: dict) -> RigWarning:
+    accepted_in = row.get("accepted_in")
+    by = row.get("by") if isinstance(row.get("by"), dict) else {}
+    return RigWarning(
+        code=str(row["code"]), detail=_text(row.get("detail")),
+        accepted_in=tuple(str(kind) for kind in accepted_in) if isinstance(accepted_in, list) else (),
+        session_kind=_text(row.get("session_kind")), how=_text(row.get("how")),
+        run_index=_integer(row.get("run")), at=_number(row.get("at")), at_local=_text(row.get("at_local")),
+        by_kind=_text(by.get("kind")), by_name=_text(by.get("name")), by_account=_text(by.get("account")),
+        by_issuer=_text(by.get("issuer")), by_token_id=_text(by.get("token_id")),
+    )
+
+
+def read_rig_session(session_dir: Path, subject: str) -> RigSession | None:
+    """This session's kind, calibration and accepted warnings from
+    `<session>/xcon/`, or `None` when it has no `config.json`."""
+    xcon = Path(session_dir) / XCON_DIRNAME
+    path = xcon / CONFIG_NAME
+    if not path.is_file():
+        return None
+    problems: list[str] = []
+    try:
+        config = json.loads(path.read_text(encoding="utf-8"))
+    except (OSError, ValueError):
+        config = None
+    if not isinstance(config, dict):
+        problems.append(f"{CONFIG_NAME}: not a JSON object")
+        config = {}
+    recorded = config.get("subject")
+    if recorded is not None and recorded != subject:
+        return RigSession(kind=None, calibration=None, warnings=(), problems=(
+            f"{CONFIG_NAME} names subject {recorded!r}, not {subject!r}, so its kind, calibration and warnings "
+            "are not this session's",))
+    kind = config.get("session_kind")
+    if kind is not None and kind not in SESSION_KINDS:
+        problems.append(f"{CONFIG_NAME}: session kind {kind!r} is not one of {', '.join(SESSION_KINDS)}")
+        kind = None
+    calibration = _calibration(config.get("calibration"), problems)
+    warnings = []
+    record = xcon / WARNINGS_NAME
+    lines = record.read_text(encoding="utf-8").splitlines() if record.is_file() else []
+    for number_of_line, line in enumerate(lines, start=1):
+        if not line.strip():
+            continue
+        try:
+            row = json.loads(line)
+        except json.JSONDecodeError:
+            problems.append(f"{WARNINGS_NAME} line {number_of_line}: not JSON")
+            continue
+        if not isinstance(row, dict) or not isinstance(row.get("code"), str) or not row["code"]:
+            problems.append(f"{WARNINGS_NAME} line {number_of_line}: not a warning naming its code")
+            continue
+        warnings.append(_warning(row))
+    return RigSession(kind=kind, calibration=calibration, warnings=tuple(warnings), problems=tuple(problems))
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/events/test_rigsession.py tests/events/test_rigruns.py -q -p no:cacheprovider
```

Expected: `22 passed`

- [ ] **Step 5: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/events tests/synth tests/schema/test_events.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `260 passed`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "T1a an unknown kind kept",
  "wl_preproc/events/rigsession.py",
  "if kind is not None and kind not in SESSION_KINDS:",
  "if False:",
  "tests/events/test_rigsession.py::test_a_kind_outside_the_three_is_a_problem_and_no_kind"
 ],
 [
  "T1b another animal's record read",
  "wl_preproc/events/rigsession.py",
  "if recorded is not None and recorded != subject:",
  "if False:",
  "tests/events/test_rigsession.py::test_a_record_of_another_animal_is_not_read"
 ],
 [
  "T1c the token read as the issuer",
  "wl_preproc/events/rigsession.py",
  "by_token_id=_text(by.get(\"token_id\"))",
  "by_token_id=_text(by.get(\"issuer\"))",
  "tests/events/test_rigsession.py::test_every_accepted_warning_is_read_as_written"
 ],
 [
  "T1d warnings read without a config",
  "wl_preproc/events/rigsession.py",
  "    if not path.is_file():\n        return None\n",
  "    if not path.is_file() and not (xcon / WARNINGS_NAME).is_file():\n        return None\n",
  "tests/events/test_rigsession.py::test_a_session_without_a_config_has_no_record"
 ],
 [
  "T1e a warning row without a code kept",
  "wl_preproc/events/rigsession.py",
  "if not isinstance(row, dict) or not isinstance(row.get(\"code\"), str) or not row[\"code\"]:",
  "if not isinstance(row, dict):",
  "tests/events/test_rigsession.py::test_a_warning_line_it_cannot_read_is_reported_by_file_and_line_and_left_out"
 ],
 [
  "T1f the run map ignores run_in_session",
  "wl_preproc/events/rigruns.py",
  "run_numbers={index: run[\"number\"] for index, run in sorted(fields.items())}",
  "run_numbers={index: index + 1 for index, run in sorted(fields.items())}",
  "tests/events/test_rigruns.py::test_each_of_wl_xcons_runs_maps_to_its_number"
 ],
 [
  "T1g no run calibration read",
  "wl_preproc/events/rigruns.py",
  "            run[\"calibration\"] = _text(row.get(\"calibration\"))\n",
  "",
  "tests/events/test_rigruns.py::test_a_runs_calibration_is_its_start_rows"
 ],
 [
  "F2 a config naming no subject is another animal's",
  "wl_preproc/events/rigsession.py",
  "if recorded is not None and recorded != subject:",
  "if recorded != subject:",
  "tests/events/test_rigsession.py::test_a_config_naming_no_subject_is_read_as_this_sessions"
 ],
 [
  "F5 kinds read from any iterable",
  "wl_preproc/events/rigsession.py",
  "if isinstance(accepted_in, list) else ()",
  "if accepted_in else ()",
  "tests/events/test_rigsession.py::test_a_warning_whose_kinds_are_not_a_list_is_acceptable_in_none"
 ]
]
```

Expected: 9 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(events): read what wl-xcon's record says the session was -- events/rigsession.py reads config.json's session_kind and calibration and warnings.jsonl by the run record's rules; events/rigruns.py reads each start row's calibration and maps wl-xcon's 0-based runs to their numbers (spec section 2)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 2: Keeping the kind, the calibrations and the accepted warnings

Three new tables written by the event stage, and the generator writing the record as wl-xcon does (spec §2, §3, §7). `tests/schema/test_guardrails.py` pins every blob attribute, so the new one joins its set in the same task.

**Files:**
- Modify: `wl_preproc/schema/core.py` (three tables after `RunRecordProblem`, whose comment widens)
- Modify: `wl_preproc/schema/events.py` (`populate_session`)
- Modify: `wl_preproc/synth/recipe.py`, `wl_preproc/synth/peripherals.py`
- Test: `tests/schema/test_events.py`, `tests/synth/test_peripherals.py`, `tests/schema/test_guardrails.py`

**Interfaces:**
- Consumes: Task 1's `read_rig_session`, `RigRuns.run_numbers`, `RigRun.calibration`.
- Produces:
  - `core.SessionRecord` (key: the session): `session_kind`, `calibration_id`, `calibration_standard`
    (tinyint), `calibration_measured_on`, `calibration_cones` (`<blob>`), each nullable;
  - `core.RunCalibration` (key: `-> Run`): `calibration_id`;
  - `core.AcceptedWarning` (key: the session and `warning_number`, from 1): `code`, `detail`,
    `accepted_in` (comma-joined), `session_kind`, `how`, `run_index`, `run_number`, `at`, `at_local`,
    `by_kind`, `by_name`, `by_account`, `by_issuer`, `by_token_id`;
  - `synth/recipe.py`: `RigWarningSpec(code, run=None, how="open", by={"kind": "box", "name": "synth"})`,
    `RigRecordSpec(session_kind="training", calibration_id="rig-synth-2027-03-01", warnings=())`,
    `SessionRecipe.rig_record: RigRecordSpec | None = None` (refused without `runs`);
  - `synth/peripherals.py`: `RIG_CONES`, `write_rig_warnings(xcon_dir, recipe, truth)`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/schema/test_events.py b/tests/schema/test_events.py
index 8425fab..54f15df 100644
--- a/tests/schema/test_events.py
+++ b/tests/schema/test_events.py
@@ -702,3 +702,162 @@ def test_problems_reading_the_rigs_run_record_are_kept(events_activated, dj_conn
     problems = lambda key: [row["problem"] for row in (core.RunRecordProblem & key).to_dicts(order_by="problem_number")]
     assert problems(bad) == ["line 5: not JSON"]
     assert problems(absent) == ["the session has no xcon/runs.jsonl, so its runs have no task or stop reason"]
+
+
+# -- What the event stage keeps of the session from wl-xcon's record: its kind,
+# its calibrations and its accepted warnings (design spec
+# `2026-10-14-session-kind-and-warnings-design.md` sections 2 and 3).
+
+_MEMBER = {"kind": "member", "name": "J. W.", "account": "acct-1", "issuer": "https://wl.works", "token_id": "tok-1"}
+
+
+def _rig_record(**update):
+    from wl_preproc.synth.recipe import RigRecordSpec, RigWarningSpec
+
+    return RigRecordSpec(**{"session_kind": "training", "warnings": (
+        RigWarningSpec(code="default-calibration"),
+        RigWarningSpec(code="luminance-step", run=1, how="start", by=_MEMBER)), **update}).model_dump()
+
+
+def test_populate_session_keeps_the_rigs_kind_calibration_and_accepted_warnings(events_activated, dj_conn,
+                                                                                 tmp_path):
+    """The session's kind and calibration, each measured run's calibration,
+    and each accepted warning as wl-xcon wrote it, its run joined to the
+    measured run's number: wl-xcon's run 1, from 0, is run number 2."""
+    from wl_preproc.schema import core
+
+    recipe, key = _populate_generated(tmp_path, "slkind1", datetime.datetime(2027, 7, 26, 9, 0), runs=True,
+                                      rig_record=_rig_record())
+    calibration = recipe.rig_record.calibration_id
+    record = (core.SessionRecord & key).fetch1()
+    assert (record["session_kind"], record["calibration_id"], record["calibration_standard"],
+            record["calibration_measured_on"]) == ("training", calibration, 0, "2027-03-01")
+    from wl_preproc.synth.peripherals import RIG_CONES
+
+    assert record["calibration_cones"] == RIG_CONES  # a nested record, its nulls included, read back unchanged
+    assert [(row["run_number"], row["calibration_id"]) for row in
+            (core.RunCalibration & key).to_dicts(order_by="run_number")] == [(1, calibration), (2, calibration)]
+    warnings = (core.AcceptedWarning & key).to_dicts(order_by="warning_number")
+    assert [(row["warning_number"], row["code"], row["how"], row["run_index"], row["run_number"])
+            for row in warnings] == [(1, "default-calibration", "open", None, None),
+                                     (2, "luminance-step", "start", 1, 2)]
+    assert (warnings[1]["by_kind"], warnings[1]["by_name"], warnings[1]["by_account"], warnings[1]["by_issuer"],
+            warnings[1]["by_token_id"]) == ("member", "J. W.", "acct-1", "https://wl.works", "tok-1")
+    assert warnings[0]["accepted_in"] == "training"
+    assert len(core.RunRecordProblem & key) == 0
+
+
+def test_a_warning_for_a_run_the_recording_did_not_measure_has_no_run_number(events_activated, dj_conn, tmp_path):
+    """wl-xcon's run 2, from 0, has a start row, so a number, 3; the
+    recording measured runs 1 and 2 only, so the warning names no measured
+    run."""
+    import json
+
+    from wl_preproc.schema import core
+    from wl_preproc.synth.recipe import RigWarningSpec
+
+    def a_third_run(session_dir):
+        path = session_dir / "xcon" / "runs.jsonl"
+        path.write_text(path.read_text() + json.dumps({"event": "start", "run": 2, "at": 99.0, "task": "rf_map"})
+                        + "\n")
+
+    _recipe, key = _populate_generated(tmp_path, "slkind2", datetime.datetime(2027, 7, 27, 9, 0), mutate=a_third_run,
+                                       runs=True, rig_record=_rig_record(warnings=(
+                                           RigWarningSpec(code="head-free", run=2, how="start"),)))
+    row = (core.AcceptedWarning & key).fetch1()
+    assert (row["run_index"], row["run_number"]) == (2, None)
+
+
+def test_problems_reading_the_rigs_session_record_are_kept_with_the_runs(events_activated, dj_conn, tmp_path):
+    """Kept beside the run record's, in the reader's order, so they reach
+    the listing as its `rig_record_problem` flags."""
+    import json
+
+    from wl_preproc.schema import core
+
+    def unknown_kind(session_dir):
+        path = session_dir / "xcon" / "config.json"
+        path.write_text(json.dumps({**json.loads(path.read_text()), "session_kind": "rehearsal"}))
+        warnings = session_dir / "xcon" / "warnings.jsonl"
+        warnings.write_text(warnings.read_text() + "{not json\n")
+
+    _recipe, key = _populate_generated(tmp_path, "slkind3", datetime.datetime(2027, 7, 28, 9, 0),
+                                       mutate=unknown_kind, runs=True, rig_record=_rig_record())
+    assert (core.SessionRecord & key).fetch1("session_kind") is None
+    assert [row["problem"] for row in (core.RunRecordProblem & key).to_dicts(order_by="problem_number")] == [
+        "config.json: session kind 'rehearsal' is not one of training, piloting, recording",
+        "warnings.jsonl line 3: not JSON"]
+
+
+def test_a_session_read_again_keeps_one_record_of_its_kind_and_warnings(events_activated, dj_conn, tmp_path):
+    """The event stage reads a session again when asked; it adds nothing
+    twice (design spec section 8, item 2)."""
+    from wl_preproc.schema import core
+
+    recipe, key = _populate_generated(tmp_path, "slkind4", datetime.datetime(2027, 7, 29, 9, 0), runs=True,
+                                      rig_record=_rig_record())
+    events.populate_session(key, tmp_path / recipe.session_id)
+    assert (len(core.SessionRecord & key), len(core.RunCalibration & key), len(core.AcceptedWarning & key)) == (
+        1, 2, 2)
+
+
+def test_a_session_without_a_rig_record_keeps_no_kind(events_activated, dj_conn, tmp_path):
+    """A recording wl-xcon did not run has no `config.json`: no row, and no
+    problem for it."""
+    from wl_preproc.schema import core
+
+    _recipe, key = _populate_generated(tmp_path, "slkind5", datetime.datetime(2027, 7, 30, 9, 0))
+    assert (len(core.SessionRecord & key), len(core.AcceptedWarning & key), len(core.RunRecordProblem & key)) == (
+        0, 0, 0)
+
+
+def test_a_warning_for_a_run_with_no_start_row_has_no_run_number(events_activated, dj_conn, tmp_path):
+    """Review Focus 1: wl-xcon's run 7 has no row in `runs.jsonl`, so no number
+    to join it by."""
+    from wl_preproc.schema import core
+    from wl_preproc.synth.recipe import RigWarningSpec
+
+    _recipe, key = _populate_generated(tmp_path, "slkind6", datetime.datetime(2027, 7, 31, 9, 0), runs=True,
+                                       rig_record=_rig_record(warnings=(RigWarningSpec(code="head-free", run=7,
+                                                                                       how="start"),)))
+    row = (core.AcceptedWarning & key).fetch1()
+    assert (row["run_index"], row["run_number"]) == (7, None)
+
+
+def test_a_record_without_its_runs_keeps_the_kind_and_unjoined_warnings(events_activated, dj_conn, tmp_path):
+    """Review Focus 3: `config.json` and `warnings.jsonl` without `runs.jsonl`:
+    the kind and the warnings are kept, and no warning names a run, since
+    nothing joins wl-xcon's runs to the measured ones; the missing run record
+    is the one problem, as before."""
+    from wl_preproc.schema import core
+
+    def no_runs(session_dir):
+        (session_dir / "xcon" / "runs.jsonl").unlink()
+
+    _recipe, key = _populate_generated(tmp_path, "slkind7", datetime.datetime(2027, 8, 1, 9, 0), mutate=no_runs,
+                                       runs=True, rig_record=_rig_record())
+    assert (core.SessionRecord & key).fetch1("session_kind") == "training"
+    assert [row["run_number"] for row in (core.AcceptedWarning & key).to_dicts(order_by="warning_number")] == [
+        None, None]
+    assert (len(core.RunCalibration & key), len(core.RunRecord & key)) == (0, 0)
+    assert [row["problem"] for row in (core.RunRecordProblem & key).to_dicts()] == [
+        "the session has no xcon/runs.jsonl, so its runs have no task or stop reason"]
+
+
+def test_a_warnings_sentence_in_any_script_is_kept_as_written(events_activated, dj_conn, tmp_path):
+    """Review Focus 4: a sentence beyond ASCII is stored as written."""
+    import json
+
+    from wl_preproc.schema import core
+
+    sentence = "Gamma Δ ≠ 2.2 — ¿calibración? 校准"
+
+    def reword(session_dir):
+        path = session_dir / "xcon" / "warnings.jsonl"
+        rows = [json.loads(line) for line in path.read_text().splitlines()]
+        rows[0]["detail"] = sentence
+        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
+
+    _recipe, key = _populate_generated(tmp_path, "slkind8", datetime.datetime(2027, 8, 2, 9, 0), mutate=reword,
+                                       runs=True, rig_record=_rig_record())
+    assert (core.AcceptedWarning & key & {"warning_number": 1}).fetch1("detail") == sentence
diff --git a/tests/schema/test_guardrails.py b/tests/schema/test_guardrails.py
index 15fd4aa..b202138 100644
--- a/tests/schema/test_guardrails.py
+++ b/tests/schema/test_guardrails.py
@@ -695,6 +695,11 @@ _EXPECTED_EXERCISED_BLOB_ATTRIBUTES = frozenset(
         # it: a new array attribute that does not join this set is exactly the
         # silent hole this set exists to close.
         "wl_preproc.schema.ephys.QualityMetrics.Channel.spectral_profile",
+        # How a session's display calibration made its cone colours light, as
+        # wl-xcon's `config.json` gives it (design spec
+        # `2026-10-14-session-kind-and-warnings-design.md` section 3), added
+        # 2026-10-14 with the table that declares it.
+        "wl_preproc.schema.core.SessionRecord.calibration_cones",
     }
 )
 
diff --git a/tests/synth/test_peripherals.py b/tests/synth/test_peripherals.py
index f19c7b1..c6fdf0f 100644
--- a/tests/synth/test_peripherals.py
+++ b/tests/synth/test_peripherals.py
@@ -181,3 +181,50 @@ def test_a_session_without_runs_has_no_run_record(tmp_path):
 
     generate_session(tmp_path, CI_RECIPE)
     assert not list(tmp_path.rglob("runs.jsonl")) and not list(tmp_path.rglob("config.json"))
+
+
+def test_the_rig_record_carries_the_kind_the_calibration_and_the_accepted_warnings(tmp_path):
+    """wl-xcon's engine build B, as its code writes it (read at `b2c2850`):
+    `config.json`'s `session_kind` and `calibration`, each run's start row's
+    `calibration`, and one `warnings.jsonl` row per accepted warning (design
+    spec `2026-10-14-session-kind-and-warnings-design.md` section 1)."""
+    from wl_preproc.events.rigruns import read_rig_runs
+    from wl_preproc.events.rigsession import read_rig_session
+    from wl_preproc.synth.peripherals import write_rig_runs
+    from wl_preproc.synth.recipe import RigRecordSpec, RigWarningSpec, SessionRecipe
+
+    rig = RigRecordSpec(session_kind="piloting", warnings=(RigWarningSpec(code="default-calibration"),
+                                                           RigWarningSpec(code="luminance-step", run=1, how="start")))
+    recipe = SessionRecipe.model_validate({**CI_RECIPE.model_dump(), "runs": True, "rig_record": rig.model_dump()})
+    (tmp_path / "xcon").mkdir()
+    write_rig_runs(tmp_path / "xcon", recipe, build_timeline(recipe))
+    session = read_rig_session(tmp_path, recipe.subject)
+    assert (session.kind, session.calibration.id, session.calibration.standard) == (
+        "piloting", rig.calibration_id, False)
+    assert [(warning.code, warning.run_index, warning.how, warning.by_name) for warning in session.warnings] == [
+        ("default-calibration", None, "open", "synth"), ("luminance-step", 1, "start", "synth")]
+    assert session.problems == ()
+    assert [run.calibration for run in read_rig_runs(tmp_path, recipe.subject).runs] == [rig.calibration_id] * 2
+
+
+def test_a_recipe_without_a_rig_record_writes_config_as_before(tmp_path):
+    """Off by default, so every existing profile's files are byte-identical."""
+    from wl_preproc.synth.peripherals import write_rig_runs
+    from wl_preproc.synth.recipe import SessionRecipe
+
+    recipe = SessionRecipe.model_validate({**CI_RECIPE.model_dump(), "runs": True})
+    (tmp_path / "xcon").mkdir()
+    write_rig_runs(tmp_path / "xcon", recipe, build_timeline(recipe))
+    assert json.loads((tmp_path / "xcon" / "config.json").read_text()) == {
+        "session_id": recipe.session_id, "subject": recipe.subject}
+    assert "calibration" not in (tmp_path / "xcon" / "runs.jsonl").read_text()
+    assert not (tmp_path / "xcon" / "warnings.jsonl").exists()
+
+
+def test_a_rig_record_needs_runs():
+    """The record is written with the runs' (`write_rig_runs`), so a recipe
+    asking for one without runs would silently write nothing."""
+    from wl_preproc.synth.recipe import RigRecordSpec, SessionRecipe
+
+    with pytest.raises(ValueError, match="rig_record needs runs"):
+        SessionRecipe.model_validate({**CI_RECIPE.model_dump(), "rig_record": RigRecordSpec().model_dump()})
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/synth/test_peripherals.py tests/schema/test_events.py tests/schema/test_guardrails.py::test_every_blob_attribute_round_trips_an_array -q -p no:cacheprovider
```

Expected: `11 failed, 33 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/schema/core.py b/wl_preproc/schema/core.py
index 83b485c..4aa3f59 100644
--- a/wl_preproc/schema/core.py
+++ b/wl_preproc/schema/core.py
@@ -77,10 +77,11 @@ class RunRecord(dj.Manual):
 @schema
 class RunRecordProblem(dj.Manual):
     definition = """
-    # What reading wl-xcon's run record could not use, kept by the event stage
-    # for the session listing (final review M2 of the listing): a line it
-    # could not read, a record of another animal, or no record at all for a
-    # session with measured runs. At most 20, the last saying how many more.
+    # What reading wl-xcon's record could not use, kept by the event stage for
+    # the session listing (final review M2 of the listing): a line of its run
+    # record or of its accepted warnings it could not read, a session kind it
+    # does not know, a record of another animal, or no run record at all for
+    # a session with measured runs. At most 20, the last saying how many more.
     # Key: (subject, session_datetime, problem_number).
     -> pipeline.Session
     problem_number : smallint unsigned  # from 1, in the reader's order
@@ -89,6 +90,71 @@ class RunRecordProblem(dj.Manual):
     """
 
 
+@schema
+class SessionRecord(dj.Manual):
+    definition = """
+    # What wl-xcon's record says the session was, from `xcon/config.json`: the
+    # kind it was run as and the display calibration it opened with, read by
+    # `schema/events.py::populate_session` (design spec
+    # 2026-10-14-session-kind-and-warnings-design.md section 3). Carried, never
+    # acted on. Null where not recorded: a config.json written before wl-xcon's
+    # engine build B has neither, and a calibration that would not load is
+    # null. Only a session with a config.json of its own animal has one.
+    # Key: (subject, session_datetime).
+    -> pipeline.Session
+    ---
+    session_kind = null            : varchar(16)   # training, piloting or recording
+    calibration_id = null          : varchar(255)
+    calibration_standard = null    : tinyint       # 1 for the sRGB standard, which nobody measured
+    calibration_measured_on = null : varchar(32)   # YYYY-MM-DD; empty for the standard
+    calibration_cones = null       : <blob>        # how its cone colours became light, as given
+    """
+
+
+@schema
+class RunCalibration(dj.Manual):
+    definition = """
+    # The display calibration a measured run ran against, from its start row
+    # in `xcon/runs.jsonl` (design spec
+    # 2026-10-14-session-kind-and-warnings-design.md section 3). It can differ
+    # from the session's: a resume after the rig's calibration changed is not
+    # refused. Only a run whose start row names one has a row.
+    # Key: (subject, session_datetime, run_number).
+    -> Run
+    ---
+    calibration_id : varchar(255)
+    """
+
+
+@schema
+class AcceptedWarning(dj.Manual):
+    definition = """
+    # One warning the session accepted, from `xcon/warnings.jsonl`, as wl-xcon
+    # wrote it (design spec 2026-10-14-session-kind-and-warnings-design.md
+    # section 3). `at` is the rig's wall clock, its bookkeeping, not a time on
+    # the recording's clock. Who accepted it is kept here and listed for
+    # wl.works without the issuer or the token id; the NWB file names no one.
+    # Key: (subject, session_datetime, warning_number).
+    -> pipeline.Session
+    warning_number : smallint unsigned  # from 1, in the file's order
+    ---
+    code                : varchar(64)
+    detail = null       : varchar(1024)  # the warning's sentence
+    accepted_in = ''    : varchar(64)    # the kinds it is acceptable in, comma-joined
+    session_kind = null : varchar(16)
+    how = null          : varchar(64)    # open, start or --accept-warnings
+    run_index = null    : smallint unsigned  # wl-xcon's run, from 0; null at the open
+    run_number = null   : smallint       # the measured run's; null at the open or when not measured
+    at = null           : double         # the rig's wall clock, seconds
+    at_local = null     : varchar(64)
+    by_kind = null      : varchar(16)    # box or member; null for nobody
+    by_name = null      : varchar(255)
+    by_account = null   : varchar(255)
+    by_issuer = null    : varchar(255)
+    by_token_id = null  : varchar(255)
+    """
+
+
 @schema
 class RunAssertion(dj.Manual):
     definition = """
diff --git a/wl_preproc/schema/events.py b/wl_preproc/schema/events.py
index 517a351..0dc2dcd 100644
--- a/wl_preproc/schema/events.py
+++ b/wl_preproc/schema/events.py
@@ -54,6 +54,7 @@ from wl_preproc.contracts.events import (
 from wl_preproc.events.assemble import AssembledBlock, AssembledTrial, assemble
 from wl_preproc.events.extract import extract_syncbox_words
 from wl_preproc.events.rigruns import read_rig_runs
+from wl_preproc.events.rigsession import read_rig_session
 from wl_preproc.events.rigtrials import read_rig_trials
 from wl_preproc.schema import DEFAULT_PREFIX, core, pipeline
 from wl_preproc.timebase import segments
@@ -357,6 +358,13 @@ def _clip(value: str | None, width: int) -> str | None:
     return None if value is None else value[:width]
 
 
+def _run_index(value: int | None) -> int | None:
+    """wl-xcon's 0-based run, or `None` where it will not fit its column: a
+    value that does not would fail the session's whole event stage on every
+    pass, as a value too long would."""
+    return value if value is not None and 0 <= value <= 65535 else None
+
+
 def _block_type_rows(session_key: dict, block_trial_rows: list[dict], record) -> list[dict]:
     """Each block's type: the `block` its trials' rig-record lines name, when
     they name one (design spec
@@ -635,12 +643,56 @@ def populate_session(key: dict, session_dir: Path) -> None:
     ]
     if record_rows:
         core.RunRecord.insert(record_rows, skip_duplicates=True)
+
+    # -- core.RunCalibration, core.SessionRecord and core.AcceptedWarning:
+    # what wl-xcon's record says the session was, its kind, its calibrations
+    # and the warnings it accepted (design spec
+    # `2026-10-14-session-kind-and-warnings-design.md` sections 2 and 3).
+    # Carried, never acted on.
+    calibration_rows = [
+        {**session_key, "run_number": run.number, "calibration_id": _clip(run.calibration, 255)}
+        for run in (rig_runs.runs if rig_runs is not None else ())
+        if run.number in measured and run.calibration is not None
+    ]
+    if calibration_rows:
+        core.RunCalibration.insert(calibration_rows, skip_duplicates=True)
+    rig_session = read_rig_session(session_dir, session_key["subject"])
+    if rig_session is not None:
+        calibration = rig_session.calibration
+        core.SessionRecord.insert1({
+            **session_key, "session_kind": rig_session.kind,
+            "calibration_id": None if calibration is None else _clip(calibration.id, 255),
+            "calibration_standard": None if calibration is None or calibration.standard is None
+            else int(calibration.standard),
+            "calibration_measured_on": None if calibration is None else _clip(calibration.measured_on, 32),
+            "calibration_cones": None if calibration is None else calibration.cones,
+        }, skip_duplicates=True)
+        numbers = rig_runs.run_numbers if rig_runs is not None else {}
+        warning_rows = []
+        for number, warning in enumerate(rig_session.warnings, start=1):
+            run_number = numbers.get(warning.run_index) if warning.run_index is not None else None
+            warning_rows.append({
+                **session_key, "warning_number": number, "code": _clip(warning.code, 64),
+                "detail": _clip(warning.detail, 1024), "accepted_in": _clip(",".join(warning.accepted_in), 64),
+                "session_kind": _clip(warning.session_kind, 16), "how": _clip(warning.how, 64),
+                "run_index": _run_index(warning.run_index),
+                "run_number": run_number if run_number in measured else None,
+                "at": warning.at, "at_local": _clip(warning.at_local, 64),
+                "by_kind": _clip(warning.by_kind, 16), "by_name": _clip(warning.by_name, 255),
+                "by_account": _clip(warning.by_account, 255), "by_issuer": _clip(warning.by_issuer, 255),
+                "by_token_id": _clip(warning.by_token_id, 255),
+            })
+        if warning_rows:
+            core.AcceptedWarning.insert(warning_rows, skip_duplicates=True)
+
     if rig_runs is not None:
         problems = list(rig_runs.problems)
     elif run_rows:
         problems = ["the session has no xcon/runs.jsonl, so its runs have no task or stop reason"]
     else:
         problems = []
+    if rig_session is not None:
+        problems += rig_session.problems
     if len(problems) > _RIG_PROBLEMS_KEPT:
         problems = [*problems[:_RIG_PROBLEMS_KEPT - 1], f"and {len(problems) - _RIG_PROBLEMS_KEPT + 1} more"]
     if problems:
diff --git a/wl_preproc/synth/peripherals.py b/wl_preproc/synth/peripherals.py
index f0e8a12..c21c24b 100644
--- a/wl_preproc/synth/peripherals.py
+++ b/wl_preproc/synth/peripherals.py
@@ -7,6 +7,7 @@ a format nothing else speaks.
 
 from __future__ import annotations
 
+import datetime
 import json
 from collections.abc import Sequence
 from pathlib import Path
@@ -206,10 +207,12 @@ def write_rig_runs(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth) ->
     whose block is unclosed faulted, and its end row says so in `taskd.py`'s
     words for a fault; the others are its words for a run that finished its
     blocks."""
+    rig = recipe.rig_record
     rows = []
     for run_index, block in enumerate(truth.blocks):
         spec = recipe.blocks[run_index]
-        rows.append({"event": "start", "run": run_index, "at": block.start_s, "task": spec.task_type.name.lower()})
+        rows.append({"event": "start", "run": run_index, "at": block.start_s, "task": spec.task_type.name.lower(),
+                     **({} if rig is None else {"calibration": rig.calibration_id})})
         if run_index + 1 in recipe.unclosed_blocks:
             rows.append({"event": "end", "run": run_index, "at": block.end_s,
                          "stopped_because": "fault, session aborted: RuntimeError: a synthetic fault",
@@ -219,5 +222,35 @@ def write_rig_runs(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth) ->
                          "stopped_because": "every block is finished", "stop_kind": "completed"})
     (xcon_dir / "runs.jsonl").write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
                                          encoding="utf-8")
-    (xcon_dir / "config.json").write_text(json.dumps({"session_id": recipe.session_id, "subject": recipe.subject},
-                                                     sort_keys=True), encoding="utf-8")
+    config = {"session_id": recipe.session_id, "subject": recipe.subject}
+    if rig is not None:
+        config["session_kind"] = rig.session_kind
+        config["calibration"] = None if rig.calibration_id is None else {
+            "id": rig.calibration_id, "standard": False, "measured_on": "2027-03-01", "cones": RIG_CONES}
+        write_rig_warnings(xcon_dir, recipe, truth)
+    (xcon_dir / "config.json").write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
+
+
+# How a calibration's cone colours became light, in the keys wl-xcon's
+# `photometry.py::cone_record` writes (read at `b2c2850`); its values here are
+# placeholders, carried verbatim and read by nothing. `via` and `luminance` are
+# null, as wl-xcon writes them where no cone colour converts, so the nulls a
+# nested record holds are carried too.
+RIG_CONES = {"observer": {"name": "CIE2006_10"}, "via": None, "luminance": None, "dkl": "unit"}
+
+
+def write_rig_warnings(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth) -> None:
+    """Stands in for wl-xcon's `xcon/warnings.jsonl`: one row per accepted
+    warning, in the keys `wl_xcon/record.py::SessionRecord.warning` writes
+    (read at `b2c2850`). One accepted at the open is at the session's start; one
+    accepted at a run's start, at that run's."""
+    rig = recipe.rig_record
+    rows = []
+    for warning in rig.warnings:
+        at = 0.0 if warning.run is None or warning.run >= len(truth.blocks) else truth.blocks[warning.run].start_s
+        rows.append({"code": warning.code, "detail": f"the synthetic {warning.code} warning",
+                     "accepted_in": [rig.session_kind], "session_kind": rig.session_kind, "by": warning.by,
+                     "at": at, "at_local": (SYNTH_EPOCH + datetime.timedelta(seconds=at)).isoformat(),
+                     "how": warning.how, "run": warning.run})
+    (xcon_dir / "warnings.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
+                                             encoding="utf-8")
diff --git a/wl_preproc/synth/recipe.py b/wl_preproc/synth/recipe.py
index 4b9345c..d45e248 100644
--- a/wl_preproc/synth/recipe.py
+++ b/wl_preproc/synth/recipe.py
@@ -163,6 +163,33 @@ class RestartSpec(BaseModel):
     probe_bank: int | None = Field(default=None, ge=0)
 
 
+class RigWarningSpec(BaseModel):
+    """One warning the synthetic session accepted, as wl-xcon's
+    `record.py::SessionRecord.warning` writes it: `run` is wl-xcon's 0-based
+    run, `None` for one accepted at the session's open; `by` its actor map,
+    or `None` for nobody."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    code: str
+    run: int | None = None
+    how: str = "open"
+    by: dict | None = Field(default_factory=lambda: {"kind": "box", "name": "synth"})
+
+
+class RigRecordSpec(BaseModel):
+    """What wl-xcon's engine build B adds to a session's record (design spec
+    `2026-10-14-session-kind-and-warnings-design.md` section 1): the kind,
+    the display calibration the session and each run ran against (`None`:
+    the rig's record would not load), and the warnings accepted."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    session_kind: str = "training"
+    calibration_id: str | None = "rig-synth-2027-03-01"
+    warnings: tuple[RigWarningSpec, ...] = ()
+
+
 class SessionRecipe(BaseModel):
     model_config = ConfigDict(extra="forbid", frozen=True)
 
@@ -214,6 +241,11 @@ class SessionRecipe(BaseModel):
     # (marker 4) after the block's end. Off by default, so every existing
     # profile is byte-identical.
     runs: bool = False
+    # What wl-xcon's engine build B adds to the run record: the session's kind,
+    # its display calibration and the warnings it accepted. Written with the
+    # runs', so it needs them. Off by default, so every existing profile is
+    # byte-identical.
+    rig_record: RigRecordSpec | None = None
 
     # How many neurons this session contains. Zero is legal and is what every
     # timing-only fixture wants: Phase 1c's recipes care about barcodes and
@@ -323,6 +355,8 @@ class SessionRecipe(BaseModel):
             )
         if "syncbox" not in self.systems:
             raise ValueError("syncbox is present at every session")
+        if self.rig_record is not None and not self.runs:
+            raise ValueError("rig_record needs runs: it is written with the run record")
         covered = sum(m.end_s - m.start_s for m in self.montages)
         if abs(covered - self.duration_s) > 1e-6:
             raise ValueError(
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/synth/test_peripherals.py tests/schema/test_events.py tests/schema/test_guardrails.py::test_every_blob_attribute_round_trips_an_array -q -p no:cacheprovider
```

Expected: `44 passed`

- [ ] **Step 5: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/events tests/synth tests/schema/test_events.py tests/schema/test_guardrails.py tests/schema/test_session_listing.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `295 passed`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "T2a a warning's run not mapped",
  "wl_preproc/schema/events.py",
  "run_number = numbers.get(warning.run_index) if warning.run_index is not None else None",
  "run_number = warning.run_index",
  "tests/schema/test_events.py::test_populate_session_keeps_the_rigs_kind_calibration_and_accepted_warnings"
 ],
 [
  "T2b an unmeasured run's number kept",
  "wl_preproc/schema/events.py",
  "\"run_number\": run_number if run_number in measured else None,",
  "\"run_number\": run_number,",
  "tests/schema/test_events.py::test_a_warning_for_a_run_the_recording_did_not_measure_has_no_run_number"
 ],
 [
  "T2c the record's problems dropped",
  "wl_preproc/schema/events.py",
  "        problems += rig_session.problems\n",
  "        pass\n",
  "tests/schema/test_events.py::test_problems_reading_the_rigs_session_record_are_kept_with_the_runs"
 ],
 [
  "T2d a re-read inserts again",
  "wl_preproc/schema/events.py",
  "            \"calibration_cones\": None if calibration is None else calibration.cones,\n        }, skip_duplicates=True)",
  "            \"calibration_cones\": None if calibration is None else calibration.cones,\n        })",
  "tests/schema/test_events.py::test_a_session_read_again_keeps_one_record_of_its_kind_and_warnings"
 ],
 [
  "T2e the generator writes the kind always",
  "wl_preproc/synth/peripherals.py",
  "    if rig is not None:\n        config[\"session_kind\"]",
  "    if True:\n        config[\"session_kind\"]",
  "tests/synth/test_peripherals.py::test_a_recipe_without_a_rig_record_writes_config_as_before"
 ],
 [
  "F1 a run with no start row looked up strictly",
  "wl_preproc/schema/events.py",
  "run_number = numbers.get(warning.run_index) if warning.run_index is not None else None",
  "run_number = numbers[warning.run_index] if warning.run_index is not None else None",
  "tests/schema/test_events.py::test_a_warning_for_a_run_with_no_start_row_has_no_run_number"
 ],
 [
  "F3 warnings joined only through a run record",
  "wl_preproc/schema/events.py",
  "numbers = rig_runs.run_numbers if rig_runs is not None else {}",
  "numbers = rig_runs.run_numbers",
  "tests/schema/test_events.py::test_a_record_without_its_runs_keeps_the_kind_and_unjoined_warnings"
 ],
 [
  "F4 a sentence kept as ASCII",
  "wl_preproc/schema/events.py",
  "\"detail\": _clip(warning.detail, 1024),",
  "\"detail\": _clip(None if warning.detail is None else warning.detail.encode(\"ascii\", \"replace\").decode(), 1024),",
  "tests/schema/test_events.py::test_a_warnings_sentence_in_any_script_is_kept_as_written"
 ]
]
```

Expected: 8 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(schema): keep each session's kind, display calibrations and accepted warnings -- core.SessionRecord, core.RunCalibration and core.AcceptedWarning, none changed, written by the event stage, a warning's run joined to the measured run's number; their problems beside the run record's; the synthetic generator's rig_record (spec sections 2, 3 and 7)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 3: The session listing

The listing's new fields, an amendment for wl.works (spec §4).

**Files:**
- Modify: `wl_preproc/contracts/protocol.py` (`ListedRun`, `SessionEntry`; new `ListedCalibration`, `ListedActor`, `ListedWarning`)
- Modify: `wl_preproc/listing/entry.py`
- Modify: `wl_preproc/events/rigruns.py` (problem wording)
- Regenerate: `docs/schemas/session_listing.json`
- Test: `tests/listing/test_entry.py`, `tests/schema/test_session_listing.py`, `tests/events/test_rigruns.py`, `tests/schema/test_events.py`

**Interfaces:**
- Consumes: Task 2's three tables.
- Produces:
  - each session entry's `session_kind`, `calibration` (`{id, standard, measured_on}` or null) and
    `accepted_warnings` (each `code`, `detail`, `accepted_in` (list), `how`, `run_number`, `at_local`,
    `by: {kind, name, account}` or null); each run's `calibration`;
  - `listing/entry.py::SessionFacts.rig_session: dict | None`, `.run_calibrations: dict[int, str]`,
    `.warnings: list[dict]`;
  - the run record's problems begin `runs.jsonl line N:`; the `rig_record_problem` message begins
    `the rig's record: ` (spec amendment 2).

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/events/test_rigruns.py b/tests/events/test_rigruns.py
index d67a9df..8d617de 100644
--- a/tests/events/test_rigruns.py
+++ b/tests/events/test_rigruns.py
@@ -62,7 +62,8 @@ def test_a_row_it_cannot_read_is_reported_by_line_and_left_out(tmp_path):
     rows = [_start(0, "rf_map"), "{not json", json.dumps({"event": "start"}), json.dumps({"event": "pause", "run": 0})]
     record = read_rig_runs(_record(tmp_path, rows), "pico")
     assert [run.number for run in record.runs] == [1]
-    assert [problem.split(":")[0] for problem in record.problems] == ["line 2", "line 3", "line 4"]
+    assert [problem.split(":")[0] for problem in record.problems] == ["runs.jsonl line 2", "runs.jsonl line 3",
+                                                                   "runs.jsonl line 4"]
 
 
 def test_a_record_of_another_animal_is_not_read(tmp_path):
@@ -89,8 +90,8 @@ def test_a_run_named_twice_keeps_its_first_rows_and_says_so(tmp_path):
             _start(0, "fixation"), _end(0, "stopped by jw", "operator")]
     record = read_rig_runs(_record(tmp_path, rows), "pico")
     assert record.runs == (RigRun(1, "rf_map", "every block is finished", "completed"),)
-    assert record.problems == ("line 3: run 0 has a second start row; the first is kept",
-                               "line 4: run 0 has a second end row; the first is kept")
+    assert record.problems == ("runs.jsonl line 3: run 0 has a second start row; the first is kept",
+                               "runs.jsonl line 4: run 0 has a second end row; the first is kept")
 
 
 def test_a_runs_calibration_is_its_start_rows(tmp_path):
diff --git a/tests/listing/test_entry.py b/tests/listing/test_entry.py
index 68009bb..7f6035c 100644
--- a/tests/listing/test_entry.py
+++ b/tests/listing/test_entry.py
@@ -34,6 +34,7 @@ def _facts(**changes):
         census=[_probe(100, "a" * 32)],
         electrodes={("a" * 32, "NP1000"): [0, 1, 2]},
         strobed_runs=[1, 2], strobed_blocks=[1, 2], censused=frozenset({100}), rig_problems=[],
+        rig_session=None, run_calibrations={}, warnings=[],
     )
     return dataclasses.replace(facts, **changes)
 
@@ -196,11 +197,84 @@ def test_a_segment_whose_census_was_not_read_has_no_probes_yet():
     assert build_entry(_facts(census=[], electrodes={}))["segments"][0]["probes"] == []
 
 
-def test_a_problem_reading_the_rigs_run_record_is_flagged():
-    """Kept by the event stage, so a run with no task says why (final review M2)."""
+def test_a_problem_reading_the_rigs_record_is_flagged():
+    """Kept by the event stage, so a run with no task says why (final review
+    M2); each names the file it is about, now that the record is more than
+    the runs (design spec `2026-10-14-session-kind-and-warnings-design.md`
+    section 3)."""
     from wl_preproc.listing.entry import build_entry
 
-    entry = build_entry(_facts(rig_problems=["line 2: not JSON", "line 3: not a start or end row naming its run"]))
+    entry = build_entry(_facts(rig_problems=["runs.jsonl line 2: not JSON",
+                                             "config.json: session kind 'rehearsal' is not one of training, "
+                                             "piloting, recording"]))
     assert [(flag["code"], flag["message"]) for flag in entry["flags"]] == [
-        ("rig_record_problem", "the rig's run record: line 2: not JSON"),
-        ("rig_record_problem", "the rig's run record: line 3: not a start or end row naming its run")]
+        ("rig_record_problem", "the rig's record: runs.jsonl line 2: not JSON"),
+        ("rig_record_problem", "the rig's record: config.json: session kind 'rehearsal' is not one of training, "
+                               "piloting, recording")]
+
+
+# -- What wl-xcon's record says the session was (design spec
+# `2026-10-14-session-kind-and-warnings-design.md` section 4).
+
+_SESSION_RECORD = {"session_kind": "training", "calibration_id": "rig-a-2026-10-01", "calibration_standard": 0,
+                   "calibration_measured_on": "2026-10-01", "calibration_cones": {"dkl": "unit"}}
+
+
+def _accepted(number, code, run_number=None, **by):
+    return {"warning_number": number, "code": code, "detail": f"the {code} sentence", "accepted_in": "training,piloting",
+            "session_kind": "training", "how": "open" if run_number is None else "start", "run_index": None,
+            "run_number": run_number, "at": 1760000000.5, "at_local": "2026-10-09T09:00:00+02:00",
+            "by_kind": None, "by_name": None, "by_account": None, "by_issuer": None, "by_token_id": None, **by}
+
+
+def test_a_session_lists_its_kind_its_calibration_and_its_accepted_warnings():
+    from wl_preproc.listing.entry import build_entry
+
+    entry = build_entry(_facts(rig_session=_SESSION_RECORD, warnings=[
+        _accepted(1, "default-calibration", by_kind="box", by_name="jw"),
+        _accepted(2, "luminance-step", run_number=2, by_kind="member", by_name="J. W.", by_account="acct-1",
+                  by_issuer="https://wl.works", by_token_id="tok-1")]))
+    assert entry["session_kind"] == "training"
+    assert entry["calibration"] == {"id": "rig-a-2026-10-01", "standard": False, "measured_on": "2026-10-01"}
+    assert entry["accepted_warnings"] == [
+        {"code": "default-calibration", "detail": "the default-calibration sentence",
+         "accepted_in": ["training", "piloting"], "how": "open", "run_number": None,
+         "at_local": "2026-10-09T09:00:00+02:00", "by": {"kind": "box", "name": "jw", "account": None}},
+        {"code": "luminance-step", "detail": "the luminance-step sentence", "accepted_in": ["training", "piloting"],
+         "how": "start", "run_number": 2, "at_local": "2026-10-09T09:00:00+02:00",
+         "by": {"kind": "member", "name": "J. W.", "account": "acct-1"}}]
+
+
+def test_a_warnings_acceptor_is_listed_without_the_issuer_or_the_token():
+    """wl.works gets its own member's name and account; the token's id, and
+    who vouched for it, stay in this repository's database (section 0,
+    decision 3)."""
+    from wl_preproc.listing.entry import build_entry
+
+    entry = build_entry(_facts(rig_session=_SESSION_RECORD, warnings=[
+        _accepted(1, "head-free", by_kind="member", by_name="J. W.", by_account="acct-1",
+                  by_issuer="https://wl.works", by_token_id="tok-1")]))
+    assert "tok-1" not in str(entry) and "https://wl.works" not in str(entry)
+
+
+def test_a_run_lists_its_calibration():
+    from wl_preproc.listing.entry import build_entry
+
+    entry = build_entry(_facts(run_calibrations={1: "rig-a-2026-10-01"}))
+    assert [run["calibration"] for run in entry["runs"]] == ["rig-a-2026-10-01", None]
+
+
+def test_a_session_wl_xcon_did_not_run_lists_no_kind_no_calibration_and_no_warnings():
+    from wl_preproc.listing.entry import build_entry
+
+    entry = build_entry(_facts())
+    assert (entry["session_kind"], entry["calibration"], entry["accepted_warnings"]) == (None, None, [])
+
+
+def test_a_kind_not_recorded_lists_none_beside_a_calibration_not_recorded():
+    """A `config.json` written before wl-xcon's engine build B: its row holds
+    neither, and the entry lists neither."""
+    from wl_preproc.listing.entry import build_entry
+
+    entry = build_entry(_facts(rig_session={**{name: None for name in _SESSION_RECORD}}))
+    assert (entry["session_kind"], entry["calibration"]) == (None, None)
diff --git a/tests/schema/test_events.py b/tests/schema/test_events.py
index 54f15df..b9d4925 100644
--- a/tests/schema/test_events.py
+++ b/tests/schema/test_events.py
@@ -700,7 +700,7 @@ def test_problems_reading_the_rigs_run_record_are_kept(events_activated, dj_conn
     _recipe, absent = _populate_generated(tmp_path / "absent", "slrig5", datetime.datetime(2027, 7, 25, 9, 0),
                                           mutate=no_record, runs=True)
     problems = lambda key: [row["problem"] for row in (core.RunRecordProblem & key).to_dicts(order_by="problem_number")]
-    assert problems(bad) == ["line 5: not JSON"]
+    assert problems(bad) == ["runs.jsonl line 5: not JSON"]
     assert problems(absent) == ["the session has no xcon/runs.jsonl, so its runs have no task or stop reason"]
 
 
diff --git a/tests/schema/test_session_listing.py b/tests/schema/test_session_listing.py
index 8d756c7..5a49f0d 100644
--- a/tests/schema/test_session_listing.py
+++ b/tests/schema/test_session_listing.py
@@ -20,7 +20,12 @@ def listed(dj_conn, prefix, tmp_path_factory):
     daemon.activate_all(prefix=prefix)
     sessions = {
         "runs": _session(tmp_path_factory, subject="sllist1", session_id="2025-07-21_01", runs=True,
-                         unclosed_blocks=[1], spikeglx_restart={**RESTART, "probe_bank": 1}),
+                         unclosed_blocks=[1], spikeglx_restart={**RESTART, "probe_bank": 1},
+                         rig_record={"session_kind": "recording", "warnings": [
+                             {"code": "default-calibration"},
+                             {"code": "luminance-step", "run": 1, "how": "start",
+                              "by": {"kind": "member", "name": "J. W.", "account": "acct-1",
+                                     "issuer": "https://wl.works", "token_id": "tok-1"}}]}),
         "plain": _session(tmp_path_factory, subject="sllist2", session_id="2025-07-22_01"),
     }
     daemon.run_once(prefix=prefix)
@@ -52,6 +57,27 @@ def test_an_entry_reads_what_the_daemon_measured(listed):
     assert [(flag["code"], flag["run_number"]) for flag in entry["flags"]] == [("bank_change_in_run", 2)]
 
 
+def test_an_entry_carries_what_wl_xcons_record_says_the_session_was(listed):
+    """Through one daemon pass: the kind, the calibration the session and each
+    run ran against, and each accepted warning, its run as the measured run's
+    number and its acceptor without the token (design spec
+    `2026-10-14-session-kind-and-warnings-design.md` section 4)."""
+    from wl_preproc.listing.entry import session_entry
+
+    recipe, key = listed["runs"]
+    entry = session_entry(key)
+    calibration = recipe.rig_record.calibration_id
+    assert (entry["session_kind"], entry["calibration"]) == (
+        "recording", {"id": calibration, "standard": False, "measured_on": "2027-03-01"})
+    assert [run["calibration"] for run in entry["runs"]] == [calibration, calibration]
+    assert [(warning["code"], warning["run_number"], warning["by"]) for warning in entry["accepted_warnings"]] == [
+        ("default-calibration", None, {"kind": "box", "name": "synth", "account": None}),
+        ("luminance-step", 2, {"kind": "member", "name": "J. W.", "account": "acct-1"})]
+    assert "tok-1" not in str(entry)
+    plain = session_entry(listed["plain"][1])
+    assert (plain["session_kind"], plain["calibration"], plain["accepted_warnings"]) == (None, None, [])
+
+
 def test_one_sessions_entry_is_found_by_its_key_in_any_form_mysql_compares(listed):
     """Its time as text too, as the per-session reading took it (the M9
     review's M1); a session that has not landed is named."""
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/listing/test_entry.py tests/events/test_rigruns.py tests/schema/test_events.py tests/schema/test_session_listing.py -q -p no:cacheprovider
```

Expected: `22 failed, 47 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/contracts/protocol.py b/wl_preproc/contracts/protocol.py
index bdfe63f..fa3e864 100644
--- a/wl_preproc/contracts/protocol.py
+++ b/wl_preproc/contracts/protocol.py
@@ -476,10 +476,57 @@ class ListedRun(BaseModel):
     closed: bool
     stopped_because: str | None
     stop_kind: str | None
+    # The display calibration the run ran against, from its start row; null
+    # where the rig's record names none (design spec
+    # `2026-10-14-session-kind-and-warnings-design.md` section 4).
+    calibration: str | None
     segments: list[int]
     blocks: list[ListedBlock]
 
 
+class ListedCalibration(BaseModel):
+    """The display calibration the session opened with, as wl-xcon's
+    `config.json` names it: its id, whether it is the sRGB standard nobody
+    measured, and the day it was measured (empty for the standard)."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    id: str | None
+    standard: bool | None
+    measured_on: str | None
+
+
+class ListedActor(BaseModel):
+    """Who accepted a warning: a name typed at the rig (`box`, no account) or
+    a wl.works member, by name and wl.works account. Never the token's id or
+    its issuer."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    kind: str | None
+    name: str | None
+    account: str | None
+
+
+class ListedWarning(BaseModel):
+    """One warning the session accepted, from wl-xcon's `warnings.jsonl`:
+    its code and sentence, the session kinds it is acceptable in, how it was
+    accepted (`open`, `start` or `--accept-warnings`), the measured run it
+    was accepted for (null at the open, or when the run was not measured),
+    and the rig's local time, its bookkeeping rather than a time on the
+    recording's clock."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    code: str
+    detail: str | None
+    accepted_in: list[str]
+    how: str | None
+    run_number: int | None
+    at_local: str | None
+    by: ListedActor | None
+
+
 class RejectedFile(BaseModel):
     model_config = ConfigDict(extra="forbid", frozen=True)
 
@@ -504,6 +551,13 @@ class SessionEntry(BaseModel):
     segments: list[ListedSegment]
     probes: list[str]
     flags: list[ListedFlag]
+    # What wl-xcon's record says the session was (design spec
+    # `2026-10-14-session-kind-and-warnings-design.md` section 4): carried,
+    # never acted on here. Null, null and empty for a session wl-xcon did not
+    # run, or whose record predates its engine build B.
+    session_kind: Literal["training", "piloting", "recording"] | None
+    calibration: ListedCalibration | None
+    accepted_warnings: list[ListedWarning]
 
 
 class SessionListing(BaseModel):
diff --git a/wl_preproc/events/rigruns.py b/wl_preproc/events/rigruns.py
index d7c50c9..b396b96 100644
--- a/wl_preproc/events/rigruns.py
+++ b/wl_preproc/events/rigruns.py
@@ -13,7 +13,9 @@ build B a start row also names the display calibration the run ran against
 (design spec `2026-10-14-session-kind-and-warnings-design.md` section 1).
 
 **Read, never repaired.** A row that is not a JSON object naming its event and
-run is reported by line and left out. wl-xcon writes a run's end row on every
+run is reported by file and line and left out: the file named, since the rig's
+record is more than its runs (design spec
+`2026-10-14-session-kind-and-warnings-design.md` section 3). wl-xcon writes a run's end row on every
 way out, a fault included (`stop_kind` "fault"); only a killed process leaves
 none, and that run's stop reason is absent, not guessed.
 
@@ -83,10 +85,10 @@ def read_rig_runs(session_dir: Path, subject: str) -> RigRuns | None:
         try:
             row = json.loads(line)
         except json.JSONDecodeError:
-            problems.append(f"line {number_of_line}: not JSON")
+            problems.append(f"{RECORD_NAME} line {number_of_line}: not JSON")
             continue
         if not isinstance(row, dict) or row.get("event") not in ("start", "end") or not _integer(row.get("run")):
-            problems.append(f"line {number_of_line}: not a start or end row naming its run")
+            problems.append(f"{RECORD_NAME} line {number_of_line}: not a start or end row naming its run")
             continue
         # A start row and its end row share `run`; only the start row is
         # sure to carry `run_in_session`, so the number is taken from it. A
@@ -94,7 +96,7 @@ def read_rig_runs(session_dir: Path, subject: str) -> RigRuns | None:
         # kept, as everywhere a number repeats here.
         run = fields.setdefault(row["run"], {"number": row["run"] + 1})
         if row["event"] in run.setdefault("seen", set()):
-            problems.append(f"line {number_of_line}: run {row['run']} has a second {row['event']} row; "
+            problems.append(f"{RECORD_NAME} line {number_of_line}: run {row['run']} has a second {row['event']} row; "
                             "the first is kept")
             continue
         run["seen"].add(row["event"])
diff --git a/wl_preproc/listing/entry.py b/wl_preproc/listing/entry.py
index 2be8112..a2603c3 100644
--- a/wl_preproc/listing/entry.py
+++ b/wl_preproc/listing/entry.py
@@ -47,7 +47,12 @@ class SessionFacts:
     strobed_runs: list[int]  # every RUN_START's run number, one per occurrence
     strobed_blocks: list[int]  # every BLOCK_START's block number, one per occurrence
     censused: frozenset[int]  # the segments the probe census has read
-    rig_problems: list[str]  # what reading the rig's run record could not use (core.RunRecordProblem)
+    rig_problems: list[str]  # what reading the rig's record could not use (core.RunRecordProblem)
+    # What wl-xcon's record says the session was (design spec
+    # `2026-10-14-session-kind-and-warnings-design.md` section 4).
+    rig_session: dict | None  # its core.SessionRecord row; None when wl-xcon did not run it
+    run_calibrations: dict[int, str]  # run_number -> core.RunCalibration's calibration_id
+    warnings: list[dict]  # its core.AcceptedWarning rows, by warning_number
 
 
 def _repeats(numbers: list[int]) -> list[tuple[int, int]]:
@@ -73,7 +78,7 @@ def build_entry(facts: SessionFacts) -> dict:
                            f"block number {number} appears {count} times in the recording; only the first is "
                            "listed", block_number=number))
     for problem in facts.rig_problems:
-        flags.append(_flag("rig_record_problem", f"the rig's run record: {problem}"))
+        flags.append(_flag("rig_record_problem", f"the rig's record: {problem}"))
 
     probes_of = collections.defaultdict(list)
     for part in sorted(facts.census, key=lambda part: (part["segment_barcode"], part["stream"])):
@@ -114,6 +119,7 @@ def build_entry(facts: SessionFacts) -> dict:
             "run_number": run["run_number"], "task_code": run["task_type"], "task": record.get("task"),
             "start_s": start, "end_s": stop, "closed": bool(run["closed"]),
             "stopped_because": record.get("stopped_because"), "stop_kind": record.get("stop_kind"),
+            "calibration": facts.run_calibrations.get(run["run_number"]),
             "segments": [segment["segment_barcode"] for segment in spanned], "blocks": listed_blocks,
         })
         if not listed_blocks:
@@ -154,10 +160,38 @@ def build_entry(facts: SessionFacts) -> dict:
         "runs": runs, "segments": segments,
         "probes": sorted({part["probe_serial"] for part in facts.census if part["probe_serial"]}),
         "flags": flags,
+        **_rig_session(facts),
     }
     return SessionEntry.model_validate(entry).model_dump(mode="json")
 
 
+_CALIBRATION_COLUMNS = ("calibration_id", "calibration_standard", "calibration_measured_on")
+
+
+def _rig_session(facts: SessionFacts) -> dict:
+    """The session's kind, calibration and accepted warnings, as wl-xcon's
+    record gives them. Who accepted a warning is listed by name and wl.works
+    account, never by the token's id or its issuer (design spec
+    `2026-10-14-session-kind-and-warnings-design.md` section 0, decision 3)."""
+    row = facts.rig_session or {}
+    recorded = any(row.get(column) is not None for column in _CALIBRATION_COLUMNS)
+    return {
+        "session_kind": row.get("session_kind"),
+        "calibration": None if not recorded else {
+            "id": row["calibration_id"],
+            "standard": None if row["calibration_standard"] is None else bool(row["calibration_standard"]),
+            "measured_on": row["calibration_measured_on"],
+        },
+        "accepted_warnings": [{
+            "code": warning["code"], "detail": warning["detail"],
+            "accepted_in": [kind for kind in (warning["accepted_in"] or "").split(",") if kind],
+            "how": warning["how"], "run_number": warning["run_number"], "at_local": warning["at_local"],
+            "by": None if warning["by_kind"] is None and warning["by_name"] is None else {
+                "kind": warning["by_kind"], "name": warning["by_name"], "account": warning["by_account"]},
+        } for warning in sorted(facts.warnings, key=lambda warning: warning["warning_number"])],
+    }
+
+
 def _session(row: dict) -> tuple[str, datetime.datetime]:
     return row["subject"], row["session_datetime"]
 
@@ -216,6 +250,9 @@ def gather_all(keys: list[dict]) -> dict[tuple[str, datetime.datetime], SessionF
     strobed_runs, strobed_blocks = strobed("RUN_START", "run_number"), strobed("BLOCK_START", "block_id")
     censused = _by_session((ephys.ProbeCensus & keys).proj().to_dicts())
     rig_problems = _by_session((core.RunRecordProblem & keys).to_dicts(order_by="problem_number"))
+    rig_sessions = {_session(row): row for row in (core.SessionRecord & keys).to_dicts()}
+    run_calibrations = _by_session((core.RunCalibration & keys).to_dicts())
+    warnings = _by_session((core.AcceptedWarning & keys).to_dicts(order_by="warning_number"))
     facts = {}
     for ingestion in (ingest.Ingestion & keys).proj("session_dir").to_dicts():
         session = _session(ingestion)
@@ -241,6 +278,9 @@ def gather_all(keys: list[dict]) -> dict[tuple[str, datetime.datetime], SessionF
             strobed_blocks=strobed_blocks.get(session, []),
             censused=frozenset(int(row["segment_barcode"]) for row in censused.get(session, [])),
             rig_problems=[row["problem"] for row in rig_problems.get(session, [])],
+            rig_session=rig_sessions.get(session),
+            run_calibrations={row["run_number"]: row["calibration_id"] for row in run_calibrations.get(session, [])},
+            warnings=warnings.get(session, []),
         )
     return facts
 
PATCH
```

- [ ] **Step 4: Regenerate the exported schemas.**

```bash
env PYTHONPATH=$PWD .venv/bin/python -m wl_preproc.cli.main schemas export --out docs/schemas
```

Expected: `git status --short docs/schemas` lists `docs/schemas/session_listing.json` only.

- [ ] **Step 5: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/listing/test_entry.py tests/events/test_rigruns.py tests/schema/test_events.py tests/schema/test_session_listing.py -q -p no:cacheprovider
```

Expected: `69 passed`

- [ ] **Step 6: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/listing tests/events tests/schema/test_events.py tests/schema/test_session_listing.py tests/responder tests/cli/test_schemas_export.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `313 passed`

- [ ] **Step 7: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "T3a the token listed as the account",
  "wl_preproc/listing/entry.py",
  "\"kind\": warning[\"by_kind\"], \"name\": warning[\"by_name\"], \"account\": warning[\"by_account\"]}",
  "\"kind\": warning[\"by_kind\"], \"name\": warning[\"by_name\"], \"account\": warning[\"by_token_id\"]}",
  "tests/listing/test_entry.py::test_a_warnings_acceptor_is_listed_without_the_issuer_or_the_token"
 ],
 [
  "T3b no run calibration listed",
  "wl_preproc/listing/entry.py",
  "\"calibration\": facts.run_calibrations.get(run[\"run_number\"]),",
  "\"calibration\": None,",
  "tests/listing/test_entry.py::test_a_run_lists_its_calibration"
 ],
 [
  "T3c a calibration not recorded listed",
  "wl_preproc/listing/entry.py",
  "\"calibration\": None if not recorded else {",
  "\"calibration\": None if facts.rig_session is None else {",
  "tests/listing/test_entry.py::test_a_kind_not_recorded_lists_none_beside_a_calibration_not_recorded"
 ],
 [
  "T3d the old flag prefix",
  "wl_preproc/listing/entry.py",
  "f\"the rig's record: {problem}\"",
  "f\"the rig's run record: {problem}\"",
  "tests/listing/test_entry.py::test_a_problem_reading_the_rigs_record_is_flagged"
 ],
 [
  "T3e the facts read no warnings",
  "wl_preproc/listing/entry.py",
  "            warnings=warnings.get(session, []),\n",
  "            warnings=[],\n",
  "tests/schema/test_session_listing.py::test_an_entry_carries_what_wl_xcons_record_says_the_session_was"
 ]
]
```

Expected: 5 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 8: Commit.**

```bash
git add -A tests wl_preproc docs/schemas
git commit -F - <<'MSG'
feat(listing): GET /sessions carries each session's kind, calibration and accepted warnings and each run's calibration -- who accepted a warning by name and wl.works account, never the token; every rig problem names its file, flagged as the rig's record (spec section 4, amendment 2)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 4: The NWB file and its description

The file's `rig_record` module, each run's calibration, and the description's fields within version 3 (spec §5, amendment 1).

**Files:**
- Create: `wl_preproc/nwb/rig_record.py`
- Modify: `wl_preproc/nwb/gather.py`, `wl_preproc/nwb/describe.py`, `wl_preproc/nwb/intervals.py`, `wl_preproc/nwb/build.py`, `wl_preproc/contracts/nwb_description.py`
- Regenerate: `docs/schemas/nwb_description.json`
- Test: `tests/nwb/test_writers.py`, `tests/nwb/test_describe.py`, `tests/schema/test_nwb_rig_record.py` (new)

**Interfaces:**
- Consumes: Task 2's three tables; `tests/schema/test_nwb_probes.py::_canonical` and
  `tests/schema/test_spikeglx_restart.py::_session` in the new build test.
- Produces:
  - `nwb/gather.py::Gathered.rig_record: dict | None = None` (`{"session_kind", "calibration": {"id",
    "standard", "measured_on", "cones"} | None, "warnings": [{"code", "detail", "accepted_in", "how",
    "run_number", "at_local"}]}`), and each run row's `"calibration"`;
  - `nwb/rig_record.py::add_rig_record(nwb: NWBFile, record: dict) -> None`;
  - `contracts/nwb_description.py`: `RigCalibration`, `AcceptedWarning`; `NwbDescription.session_kind`,
    `.calibration`, `.accepted_warnings` and `Run.calibration`, each defaulted.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/nwb/test_describe.py b/tests/nwb/test_describe.py
index 8929aeb..235ed61 100644
--- a/tests/nwb/test_describe.py
+++ b/tests/nwb/test_describe.py
@@ -33,12 +33,13 @@ CHECKSUM = {"dataset_path": "/intervals/trials/start_time", "dtype": "float64",
             "sha256": "0" * 64, "paired_with": ""}
 
 
-def _gathered(eye=None, notes=(), probes=(), probe_notes=(), trial_notes=()):
+def _gathered(eye=None, notes=(), probes=(), probe_notes=(), trial_notes=(), runs=(RUN,), rig_record=None):
     from wl_preproc.nwb.gather import Gathered
 
-    return Gathered(session=SESSION, systems=["ohdpi"], runs=[RUN], blocks=[BLOCK], trials=[{"trial_id": 1}],
+    return Gathered(session=SESSION, systems=["ohdpi"], runs=list(runs), blocks=[BLOCK], trials=[{"trial_id": 1}],
                     events=[{}, {}], timebase={}, eye=eye, conditions=[CONDITION], condition_notes=list(notes),
-                    probes=list(probes), probe_notes=list(probe_notes), trial_notes=list(trial_notes))
+                    probes=list(probes), probe_notes=list(probe_notes), trial_notes=list(trial_notes),
+                    rig_record=rig_record)
 
 
 def test_the_description_of_a_file_without_eye_data():
@@ -144,3 +145,29 @@ def test_a_run_takes_the_rigs_name_for_its_task_and_a_probe_its_sorted_runs():
     out = describe(data, status="written", n_critical=0, checksums=[], built_at=BUILT_AT)
     assert out["runs"][0]["task"] == {"code": "2", "name": "fixation_detection"}
     assert out["probes"][0]["sorted_runs"] == [1]
+
+
+def test_the_description_says_what_wl_xcons_record_says_the_session_was():
+    """So a dataset can be selected by kind (design spec
+    `2026-10-14-session-kind-and-warnings-design.md` section 5): the kind,
+    the calibration without its colour model, each accepted warning's code
+    and run, and each run's calibration. No person."""
+    from tests.nwb.test_writers import RIG_RECORD
+    from wl_preproc.nwb.describe import describe
+
+    out = describe(_gathered(runs=[{**RUN, "calibration": "rig-a-2026-10-01"}], rig_record=RIG_RECORD),
+                   status="written", n_critical=0, checksums=[CHECKSUM], built_at=BUILT_AT)
+    assert out["session_kind"] == "training"
+    assert out["calibration"] == {"id": "rig-a-2026-10-01", "standard": False, "measured_on": "2026-10-01"}
+    assert out["accepted_warnings"] == [{"code": "default-calibration", "run_number": None},
+                                        {"code": "luminance-step", "run_number": 2}]
+    assert [run["calibration"] for run in out["runs"]] == ["rig-a-2026-10-01"]
+    assert out["schema_version"] == 3
+
+
+def test_a_file_of_a_session_wl_xcon_did_not_run_describes_no_kind():
+    from wl_preproc.nwb.describe import describe
+
+    out = describe(_gathered(), status="written", n_critical=0, checksums=[CHECKSUM], built_at=BUILT_AT)
+    assert (out["session_kind"], out["calibration"], out["accepted_warnings"]) == (None, None, [])
+    assert [run["calibration"] for run in out["runs"]] == [None]
diff --git a/tests/nwb/test_writers.py b/tests/nwb/test_writers.py
index fae762d..a8b3a92 100644
--- a/tests/nwb/test_writers.py
+++ b/tests/nwb/test_writers.py
@@ -307,3 +307,66 @@ def test_runs_that_hold_no_block_add_no_block_table(tmp_path):
     _path, io, nwb = _write(tmp_path, build)
     with io:
         assert "blocks" not in nwb.intervals and "runs" in nwb.intervals
+
+
+# -- What wl-xcon's record says the session was (design spec
+# `2026-10-14-session-kind-and-warnings-design.md` section 5).
+
+RIG_RECORD = {
+    "session_kind": "training",
+    "calibration": {"id": "rig-a-2026-10-01", "standard": False, "measured_on": "2026-10-01",
+                    "cones": {"observer": {"name": "CIE2006_10"}, "via": None, "luminance": None, "dkl": "unit"}},
+    "warnings": [
+        {"code": "default-calibration", "detail": "the default-calibration sentence",
+         "accepted_in": ["training", "piloting"], "how": "open", "run_number": None,
+         "at_local": "2026-10-09T09:00:00+02:00"},
+        {"code": "luminance-step", "detail": "the luminance-step sentence", "accepted_in": ["training"],
+         "how": "start", "run_number": 2, "at_local": "2026-10-09T09:05:00+02:00"},
+    ],
+}
+
+
+def test_each_run_carries_the_calibration_it_ran_against(tmp_path):
+    from wl_preproc.nwb.intervals import add_runs
+
+    runs = [{**RUNS[0], "calibration": "rig-a-2026-10-01"}, RUNS[1]]
+    _path, io, nwb = _write(tmp_path, lambda nwb: add_runs(nwb, runs, ["ohdpi"]))
+    with io:
+        assert nwb.intervals["runs"].to_dataframe()["calibration"].tolist() == ["rig-a-2026-10-01", ""]
+
+
+def test_the_rig_record_holds_the_session_and_the_warnings_it_accepted(tmp_path):
+    import json
+
+    from wl_preproc.nwb.rig_record import add_rig_record
+
+    _path, io, nwb = _write(tmp_path, lambda nwb: add_rig_record(nwb, RIG_RECORD))
+    with io:
+        module = nwb.processing["rig_record"]
+        (session,) = module["session"].to_dataframe().to_dict("records")
+        assert {name: session[name] for name in ("session_kind", "calibration_id", "calibration_standard",
+                                                 "calibration_measured_on")} == {
+            "session_kind": "training", "calibration_id": "rig-a-2026-10-01", "calibration_standard": 0,
+            "calibration_measured_on": "2026-10-01"}
+        assert json.loads(session["calibration_cones"]) == RIG_RECORD["calibration"]["cones"]
+        warnings = module["accepted_warnings"].to_dataframe()
+        assert warnings[["code", "accepted_in", "how", "run_number", "accepted_at_local"]].values.tolist() == [
+            ["default-calibration", "training,piloting", "open", -1, "2026-10-09T09:00:00+02:00"],
+            ["luminance-step", "training", "start", 2, "2026-10-09T09:05:00+02:00"]]
+        assert warnings["detail"].tolist() == ["the default-calibration sentence", "the luminance-step sentence"]
+
+
+def test_a_kind_and_calibration_not_recorded_are_empty_and_no_warnings_mean_no_table(tmp_path):
+    """A `config.json` written before wl-xcon's engine build B: the session
+    row says nothing was recorded, and a session that accepted no warning
+    has no warnings table."""
+    from wl_preproc.nwb.rig_record import add_rig_record
+
+    record = {"session_kind": None, "calibration": None, "warnings": []}
+    _path, io, nwb = _write(tmp_path, lambda nwb: add_rig_record(nwb, record))
+    with io:
+        module = nwb.processing["rig_record"]
+        (session,) = module["session"].to_dataframe().to_dict("records")
+        assert (session["session_kind"], session["calibration_id"], session["calibration_standard"],
+                session["calibration_cones"]) == ("", "", -1, "")
+        assert "accepted_warnings" not in module.data_interfaces
diff --git a/tests/schema/test_nwb_rig_record.py b/tests/schema/test_nwb_rig_record.py
new file mode 100644
index 0000000..3b3c967
--- /dev/null
+++ b/tests/schema/test_nwb_rig_record.py
@@ -0,0 +1,99 @@
+"""What wl-xcon's record says the session was, in the NWB file and its
+description, through the builder (design spec
+`2026-10-14-session-kind-and-warnings-design.md` section 5). Subjects and
+dates checked unclaimed across `tests/` on 2026-10-14; the dates are in the
+PAST, since `nwbinspector` calls a future `session_start_time` critical."""
+
+from __future__ import annotations
+
+import pytest
+
+from tests.schema.test_nwb_probes import _canonical
+from tests.schema.test_spikeglx_restart import _session as _restart_session
+
+_MEMBER = {"kind": "member", "name": "Jay Dubya", "account": "acct-rk1", "issuer": "https://wl.works",
+           "token_id": "tok-rk1"}
+
+
+@pytest.fixture(scope="module")
+def daemon_module(dj_conn, prefix):
+    from wl_preproc import daemon
+
+    daemon.activate_all(prefix=prefix)
+    return daemon
+
+
+@pytest.fixture(scope="module")
+def recorded(daemon_module, prefix, tmp_path_factory):
+    """A training session wl-xcon ran, two runs, one warning accepted at the
+    open by a name typed at the rig and one at run 2's start by a wl.works
+    member; a canonical over both runs and a derivative over run 1."""
+    recipe, key = _restart_session(
+        tmp_path_factory, None, runs=True, subject="rknwb1", session_id="2025-06-29_01",
+        rig_record={"session_kind": "training", "warnings": [
+            {"code": "default-calibration", "by": {"kind": "box", "name": "Typed Name"}},
+            {"code": "luminance-step", "run": 1, "how": "start", "by": _MEMBER}]})
+    daemon_module.run_once(prefix=prefix)
+    canonical = _canonical(daemon_module, prefix, key, "rknwb1-k1", [])
+    derivative = _canonical(daemon_module, prefix, key, "rknwb1-k2", [], run_numbers=[1])
+    return recipe, canonical, derivative
+
+
+@pytest.fixture(scope="module")
+def built(recorded, tmp_path_factory):
+    from wl_preproc.nwb.build import build
+
+    _recipe, canonical, derivative = recorded
+    return (build(canonical, tmp_path_factory.mktemp("nwb-rig-canonical")),
+            build(derivative, tmp_path_factory.mktemp("nwb-rig-derivative")))
+
+
+def test_the_file_holds_the_kind_the_calibrations_and_the_warnings(recorded, built):
+    from pynwb import NWBHDF5IO
+
+    recipe, _canonical_key, _derivative_key = recorded
+    canonical, _derivative = built
+    assert canonical.status == "written", (canonical.reason, canonical.findings)
+    calibration = recipe.rig_record.calibration_id
+    with NWBHDF5IO(str(canonical.path), "r") as handle:
+        nwb = handle.read()
+        assert nwb.intervals["runs"].to_dataframe()["calibration"].tolist() == [calibration, calibration]
+        module = nwb.processing["rig_record"]
+        (session,) = module["session"].to_dataframe().to_dict("records")
+        assert (session["session_kind"], session["calibration_id"]) == ("training", calibration)
+        warnings = module["accepted_warnings"].to_dataframe()
+        assert warnings[["code", "how", "run_number"]].values.tolist() == [
+            ["default-calibration", "open", -1], ["luminance-step", "start", 2]]
+
+
+def test_the_file_names_no_one_who_accepted_a_warning(built):
+    """Decision 3 of the spec's section 0: the database keeps who, and the
+    listing gives wl.works the name and account; the file names no one."""
+    canonical, _derivative = built
+    raw = canonical.path.read_bytes()
+    for text in ("Typed Name", "Jay Dubya", "acct-rk1", "tok-rk1", "https://wl.works"):
+        assert text.encode() not in raw, text
+
+
+def test_the_description_selects_the_file_by_kind(recorded, built):
+    recipe, _canonical_key, _derivative_key = recorded
+    canonical, _derivative = built
+    calibration = recipe.rig_record.calibration_id
+    assert canonical.description["session_kind"] == "training"
+    assert canonical.description["calibration"] == {"id": calibration, "standard": False, "measured_on": "2027-03-01"}
+    assert canonical.description["accepted_warnings"] == [{"code": "default-calibration", "run_number": None},
+                                                          {"code": "luminance-step", "run_number": 2}]
+    assert [run["calibration"] for run in canonical.description["runs"]] == [calibration, calibration]
+
+
+def test_a_file_of_some_runs_holds_the_warnings_of_the_open_and_of_its_own_runs(built):
+    """A warning accepted for a run the file does not hold is about that run,
+    and stays out of the file (design spec amendment 1)."""
+    from pynwb import NWBHDF5IO
+
+    _canonical_built, derivative = built
+    assert derivative.status == "written", (derivative.reason, derivative.findings)
+    with NWBHDF5IO(str(derivative.path), "r") as handle:
+        warnings = handle.read().processing["rig_record"]["accepted_warnings"].to_dataframe()
+        assert warnings["code"].tolist() == ["default-calibration"]
+    assert derivative.description["accepted_warnings"] == [{"code": "default-calibration", "run_number": None}]
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/nwb/test_writers.py tests/nwb/test_describe.py tests/schema/test_nwb_rig_record.py -q -p no:cacheprovider
```

Expected: `14 failed, 21 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/contracts/nwb_description.py b/wl_preproc/contracts/nwb_description.py
index 2b2b41b..351b18b 100644
--- a/wl_preproc/contracts/nwb_description.py
+++ b/wl_preproc/contracts/nwb_description.py
@@ -6,7 +6,10 @@ section 2).
 **Versioned and open.** `schema_version` is 3: version 3 replaced `blocks`
 with `runs`, each with `works_run_id` for wl.works' `animal_session_run` and
 the measured blocks inside it, and gave each probe its `sorted_runs` (design
-spec `2026-10-01-session-listing-and-run-requests-design.md` section 4).
+spec `2026-10-01-session-listing-and-run-requests-design.md` section 4); within
+it, 2026-10-14 added what wl-xcon's record says the session was, its kind, its
+display calibration and each run's, and the warnings it accepted (design spec
+`2026-10-14-session-kind-and-warnings-design.md` section 5).
 Version 2 gave `probes` their shape, each with both of its areas (design spec
 `2026-09-30-nwb-probes-design.md` section 3.2). Later pieces add groups and
 fields -- a processing summary with unit counts, the photodiode, video and
@@ -150,6 +153,30 @@ class Run(_Frozen):
     coverage: dict[str, Coverage]
     conditions: list[Condition]
     blocks: list[RunBlock]
+    # The display calibration the run ran against, from its start row in
+    # wl-xcon's record; null where it names none (design spec
+    # `2026-10-14-session-kind-and-warnings-design.md` section 5).
+    calibration: str | None = None
+
+
+class RigCalibration(_Frozen):
+    """The display calibration the session opened with, as wl-xcon's
+    `config.json` names it; its colour model is in the file's `rig_record`
+    module, not here."""
+
+    id: str | None
+    standard: bool | None
+    measured_on: str | None
+
+
+class AcceptedWarning(_Frozen):
+    """A warning the session accepted, by its code, and the run it was
+    accepted for (null: at the session's open). Who accepted it is not
+    published (design spec `2026-10-14-session-kind-and-warnings-design.md`
+    section 0, decision 3)."""
+
+    code: str
+    run_number: int | None
 
 
 class Quality(_Frozen):
@@ -227,3 +254,9 @@ class NwbDescription(_Frozen):
     # probe.
     notes: list[str]
     checksums: Checksums
+    # What wl-xcon's record says the session was (design spec
+    # `2026-10-14-session-kind-and-warnings-design.md` section 5), added within
+    # version 3: null, null and empty for a session wl-xcon did not run.
+    session_kind: Literal["training", "piloting", "recording"] | None = None
+    calibration: RigCalibration | None = None
+    accepted_warnings: list[AcceptedWarning] = []
diff --git a/wl_preproc/nwb/build.py b/wl_preproc/nwb/build.py
index 41d7442..dc8db99 100644
--- a/wl_preproc/nwb/build.py
+++ b/wl_preproc/nwb/build.py
@@ -14,6 +14,7 @@ from wl_preproc.nwb.eye_events import add_agreement, add_detections, add_sources
 from wl_preproc.nwb.gather import Refused, gather, readiness
 from wl_preproc.nwb.intervals import add_blocks, add_conditions, add_runs, add_task_events, add_trials
 from wl_preproc.nwb.probes import add_probes
+from wl_preproc.nwb.rig_record import add_rig_record
 from wl_preproc.nwb.session import new_file
 from wl_preproc.nwb.timebase import add_timebase
 from wl_preproc.nwb.validate import inspect_file, n_critical
@@ -71,6 +72,8 @@ def build(activation_key: dict, nwb_root: Path) -> BuildResult:
     add_task_events(nwb, data.events)
     add_timebase(nwb, **data.timebase)
     add_probes(nwb, data.probes)
+    if data.rig_record is not None:
+        add_rig_record(nwb, data.rig_record)
     if data.eye is not None:
         add_eye_series(nwb, data.eye["times"], data.eye["gaze"], data.eye["pupil"])
         add_eye_tables(nwb, data.eye["calibration"], data.eye["validity"], data.eye["repairs"])
diff --git a/wl_preproc/nwb/describe.py b/wl_preproc/nwb/describe.py
index 9b4ee9f..32e4fe8 100644
--- a/wl_preproc/nwb/describe.py
+++ b/wl_preproc/nwb/describe.py
@@ -48,6 +48,23 @@ def _age_days(date_of_birth: datetime.date | None, session_datetime: datetime.da
     return None if date_of_birth is None else (session_datetime.date() - date_of_birth).days
 
 
+def _rig_record(record: dict | None) -> dict:
+    """What wl-xcon's record says the session was, for selecting a dataset by
+    kind: the calibration without its colour model, and each warning by its
+    code and run (design spec `2026-10-14-session-kind-and-warnings-design.md`
+    section 5)."""
+    if record is None:
+        return {"session_kind": None, "calibration": None, "accepted_warnings": []}
+    calibration = record["calibration"]
+    return {
+        "session_kind": record["session_kind"],
+        "calibration": None if calibration is None else {
+            name: calibration[name] for name in ("id", "standard", "measured_on")},
+        "accepted_warnings": [{"code": warning["code"], "run_number": warning["run_number"]}
+                              for warning in record["warnings"]],
+    }
+
+
 def describe(data, *, status: str, n_critical: int, checksums: list[dict], built_at: datetime.datetime) -> dict:
     """The description of one built file. `data` is `gather.Gathered`."""
     session, subject, eye = data.session, data.session["subject"], data.eye
@@ -112,6 +129,7 @@ def describe(data, *, status: str, n_critical: int, checksums: list[dict], built
                 "closed": block["closed"],
                 "trials": block["n_trials"],
             } for block in data.blocks if block["run_number"] == run["run_number"]],
+            "calibration": run.get("calibration"),
         } for run in data.runs],
         "quality": {
             "timing_tier": session["timing_tier"],
@@ -120,5 +138,6 @@ def describe(data, *, status: str, n_critical: int, checksums: list[dict], built
         },
         "notes": [*data.condition_notes, *data.trial_notes, *data.probe_notes],
         "checksums": {"algorithm": "sha256", "datasets": checksums},
+        **_rig_record(data.rig_record),
     }
     return NwbDescription.model_validate(description).model_dump(mode="json")
diff --git a/wl_preproc/nwb/gather.py b/wl_preproc/nwb/gather.py
index ad97b64..41ef3ad 100644
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -61,6 +61,11 @@ class Gathered:
     # canonical, the trials and blocks that start in its montage's window but
     # in no measured run (`_outside_runs_notes`).
     trial_notes: list[str] = dataclasses.field(default_factory=list)
+    # What wl-xcon's record says the session was, for the file's `rig_record`
+    # module and its description (design spec
+    # `2026-10-14-session-kind-and-warnings-design.md` section 5); None for a
+    # session wl-xcon did not run. See `_rig_record`.
+    rig_record: dict | None = None
 
 
 def _aware_utc(value: datetime.datetime) -> datetime.datetime:
@@ -76,13 +81,45 @@ def _run_set(activation_key: dict, session_key: dict) -> list[dict]:
     numbers = {int(number) for number in (request.ActivationRun & activation_key).to_arrays("run_number")}
     works = {row["run_number"]: row["works_run_id"] for row in (core.RunAssertion & session_key).to_dicts()}
     tasks = {row["run_number"]: row["task"] for row in (core.RunRecord & session_key).to_dicts()}
+    calibrations = {row["run_number"]: row["calibration_id"] for row in (core.RunCalibration & session_key).to_dicts()}
     rows = [{"run_number": row["run_number"], "start_s": float(row["run_start_time"]),
              "end_s": float(row["run_stop_time"]), "task_type": row["task_type"], "task": tasks.get(row["run_number"]),
-             "works_run_id": works.get(row["run_number"]), "closed": bool(row["closed"])}
+             "works_run_id": works.get(row["run_number"]), "closed": bool(row["closed"]),
+             "calibration": calibrations.get(row["run_number"])}
             for row in (core.Run & session_key).to_dicts() if row["run_number"] in numbers]
     return sorted(rows, key=lambda row: row["start_s"])
 
 
+def _rig_record(session_key: dict, run_numbers: set[int]) -> dict | None:
+    """What wl-xcon's record says the session was (`core.SessionRecord`,
+    `core.AcceptedWarning`), or None when wl-xcon did not run it. The
+    warnings are those accepted at the session's open and those for the
+    file's own runs: one accepted for a run the file does not hold is about
+    that run (design spec `2026-10-14-session-kind-and-warnings-design.md`,
+    amendment 1). Who accepted a warning is not read: the file names no one
+    (section 0, decision 3)."""
+    from wl_preproc.schema import core
+
+    rows = (core.SessionRecord & session_key).to_dicts()
+    if not rows:
+        return None
+    (row,) = rows
+    recorded = any(row[column] is not None for column in ("calibration_id", "calibration_standard",
+                                                          "calibration_measured_on"))
+    return {
+        "session_kind": row["session_kind"],
+        "calibration": None if not recorded else {
+            "id": row["calibration_id"],
+            "standard": None if row["calibration_standard"] is None else bool(row["calibration_standard"]),
+            "measured_on": row["calibration_measured_on"], "cones": row["calibration_cones"]},
+        "warnings": [{"code": warning["code"], "detail": warning["detail"],
+                      "accepted_in": [kind for kind in (warning["accepted_in"] or "").split(",") if kind],
+                      "how": warning["how"], "run_number": warning["run_number"], "at_local": warning["at_local"]}
+                     for warning in (core.AcceptedWarning & session_key).to_dicts(order_by="warning_number")
+                     if warning["run_index"] is None or warning["run_number"] in run_numbers],
+    }
+
+
 def identifier_for(activation_key: dict, session_id: str) -> str:
     """`{subject}.{session_id}.montage-{m}.activation-{a}`. A session id is
     the sync box's date and index, not scoped to a subject, so two animals
@@ -727,4 +764,5 @@ def gather(activation_key: dict) -> Gathered:
         probes=probes,
         probe_notes=probe_notes,
         trial_notes=_trial_notes(session_key, run_rows) + _outside_runs_notes(key, session_key, activation["role"]),
+        rig_record=_rig_record(session_key, {row["run_number"] for row in run_rows}),
     )
diff --git a/wl_preproc/nwb/intervals.py b/wl_preproc/nwb/intervals.py
index c2e50ae..9722e25 100644
--- a/wl_preproc/nwb/intervals.py
+++ b/wl_preproc/nwb/intervals.py
@@ -47,6 +47,8 @@ def add_runs(nwb: NWBFile, runs: list[dict], systems: list[str]) -> None:
             column("task", "The rig's name for the run's task ('' if its record names none).",
                    [r["task"] or "" for r in rows]),
             column("closed", "Whether a RUN_END arrived.", [bool(r["closed"]) for r in rows]),
+            column("calibration", "The display calibration the run ran against, from its start row in the rig's "
+                                  "record ('' if it names none).", [r.get("calibration") or "" for r in rows]),
             *_coverage_columns(rows, systems),
         ],
     ))
diff --git a/wl_preproc/nwb/rig_record.py b/wl_preproc/nwb/rig_record.py
new file mode 100644
index 0000000..10283e9
--- /dev/null
+++ b/wl_preproc/nwb/rig_record.py
@@ -0,0 +1,61 @@
+"""`processing/rig_record`: what wl-xcon's record says the session was (design
+spec `2026-10-14-session-kind-and-warnings-design.md` section 5).
+
+**Carried, never acted on.** The kind the session was run as (training,
+piloting or recording) and the display calibration it opened with, one row in
+`session`; each warning it accepted, a row in `accepted_warnings`, by its
+code, its sentence, the kinds it is acceptable in, how and for which run it
+was accepted, and the rig's local time, its bookkeeping rather than a time on
+the session's clock. **The file names no one who accepted a warning** (section
+0, decision 3): this repository's database keeps that, and the listing gives
+wl.works its own member's name and account.
+
+Written only for a session wl-xcon ran (`gather.py::_rig_record`)."""
+
+from __future__ import annotations
+
+import json
+
+from pynwb import NWBFile
+from pynwb.core import DynamicTable
+
+from wl_preproc.nwb.columns import column
+
+
+def add_rig_record(nwb: NWBFile, record: dict) -> None:
+    """One `session` row, and an `accepted_warnings` table when the session
+    accepted any warning."""
+    calibration = record["calibration"] or {}
+    standard = calibration.get("standard")
+    module = nwb.create_processing_module(
+        name="rig_record",
+        description=("What wl-xcon's record says the session was: the kind it was run as, the display calibration "
+                     "it opened with, and the warnings it accepted. Carried as the rig wrote them."),
+    )
+    module.add(DynamicTable(name="session", description="The session's kind and display calibration.", columns=[
+        column("session_kind", "training, piloting or recording ('' if not recorded).",
+               [record["session_kind"] or ""]),
+        column("calibration_id", "The display calibration's id ('' if not recorded).", [calibration.get("id") or ""]),
+        column("calibration_standard", "1 for the sRGB standard nobody measured, 0 for a measured calibration, "
+                                       "-1 if not recorded.", [-1 if standard is None else int(standard)]),
+        column("calibration_measured_on", "The day it was measured, YYYY-MM-DD ('' for the standard, or if not "
+                                          "recorded).", [calibration.get("measured_on") or ""]),
+        column("calibration_cones", "How its cone colours became light, as the rig gave it, as JSON ('' if not "
+                                    "recorded).",
+               ["" if calibration.get("cones") is None else json.dumps(calibration["cones"], sort_keys=True)]),
+    ]))
+    warnings = record["warnings"]
+    if not warnings:
+        return
+    module.add(DynamicTable(name="accepted_warnings", description="Each warning the session accepted.", columns=[
+        column("code", "The warning's code.", [warning["code"] for warning in warnings]),
+        column("detail", "Its sentence ('' if none).", [warning["detail"] or "" for warning in warnings]),
+        column("accepted_in", "The session kinds it is acceptable in, comma-joined.",
+               [",".join(warning["accepted_in"]) for warning in warnings]),
+        column("how", "How it was accepted: open, start or --accept-warnings ('' if not recorded).",
+               [warning["how"] or "" for warning in warnings]),
+        column("run_number", "The run it was accepted for (-1: at the session's open).",
+               [-1 if warning["run_number"] is None else warning["run_number"] for warning in warnings]),
+        column("accepted_at_local", "When, in the rig's local time: its bookkeeping, not a time on the session's "
+                                    "clock ('' if not recorded).", [warning["at_local"] or "" for warning in warnings]),
+    ]))
PATCH
```

- [ ] **Step 4: Regenerate the exported schemas.**

```bash
env PYTHONPATH=$PWD .venv/bin/python -m wl_preproc.cli.main schemas export --out docs/schemas
```

Expected: `git status --short docs/schemas` lists `docs/schemas/nwb_description.json` only.

- [ ] **Step 5: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/nwb/test_writers.py tests/nwb/test_describe.py tests/schema/test_nwb_rig_record.py -q -p no:cacheprovider
```

Expected: `35 passed`

- [ ] **Step 6: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/nwb tests/schema/test_nwb_*.py tests/cli/test_schemas_export.py tests/responder -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `387 passed, 1 skipped`

- [ ] **Step 7: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "T4a every warning in every file",
  "wl_preproc/nwb/gather.py",
  "                     if warning[\"run_index\"] is None or warning[\"run_number\"] in run_numbers],",
  "],",
  "tests/schema/test_nwb_rig_record.py::test_a_file_of_some_runs_holds_the_warnings_of_the_open_and_of_its_own_runs"
 ],
 [
  "T4b who accepted it written as its sentence",
  "wl_preproc/nwb/gather.py",
  "[{\"code\": warning[\"code\"], \"detail\": warning[\"detail\"],",
  "[{\"code\": warning[\"code\"], \"detail\": warning[\"by_name\"],",
  "tests/schema/test_nwb_rig_record.py::test_the_file_names_no_one_who_accepted_a_warning"
 ],
 [
  "T4c no run calibration column value",
  "wl_preproc/nwb/intervals.py",
  "[r.get(\"calibration\") or \"\" for r in rows]",
  "[\"\" for r in rows]",
  "tests/nwb/test_writers.py::test_each_run_carries_the_calibration_it_ran_against"
 ],
 [
  "T4d the module not written",
  "wl_preproc/nwb/build.py",
  "    if data.rig_record is not None:\n        add_rig_record(nwb, data.rig_record)\n",
  "",
  "tests/schema/test_nwb_rig_record.py::test_the_file_holds_the_kind_the_calibrations_and_the_warnings"
 ],
 [
  "T4e the warnings' runs not described",
  "wl_preproc/nwb/describe.py",
  "\"accepted_warnings\": [{\"code\": warning[\"code\"], \"run_number\": warning[\"run_number\"]}",
  "\"accepted_warnings\": [{\"code\": warning[\"code\"], \"run_number\": None}",
  "tests/nwb/test_describe.py::test_the_description_says_what_wl_xcons_record_says_the_session_was"
 ],
 [
  "T4f the standard flag inverted",
  "wl_preproc/nwb/rig_record.py",
  "[-1 if standard is None else int(standard)]",
  "[-1 if standard is None else int(not standard)]",
  "tests/nwb/test_writers.py::test_the_rig_record_holds_the_session_and_the_warnings_it_accepted"
 ]
]
```

Expected: 6 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 8: Commit.**

```bash
git add -A tests wl_preproc docs/schemas
git commit -F - <<'MSG'
feat(nwb): the file's rig_record module and each run's calibration, and the description's kind, calibration and warnings within version 3 -- a file holds the warnings of the open and of its own runs, and names no one (spec section 5, amendment 1)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 5: The records

The spec's amendments 1-5, this piece's handoff, wl.works' amendment, wl-xcon's ask 5 answered and its
raw-video answer recorded, and the checkpoint's and `wl.yaml`'s lines (spec §9). Then the full suite, once,
on both interpreters.

**Files:**
- Modify: `docs/superpowers/specs/2026-10-14-session-kind-and-warnings-design.md`,
  `docs/pending-wl-works-amendments.md`, `docs/pending-wl-xcon-amendments.md`, `HANDOVER-wl-expcontroller.md`,
  `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-10-14-session-kind.md`

**Interfaces:** none; documents only.

- [ ] **Step 1: Apply the records.** Save this script outside the repository as `records.py` and run it from
  the repository root.

```python
"""Task 5's records: run from the repository root."""
import pathlib


def edit(path, pairs):
    p = pathlib.Path(path)
    s = p.read_text()
    for old, new in pairs:
        assert s.count(old) == 1, (path, old[:70])
        s = s.replace(old, new)
    p.write_text(s)


def append(path, text):
    p = pathlib.Path(path)
    s = p.read_text()
    assert s.endswith("\n")
    p.write_text(s + text)


append("docs/superpowers/specs/2026-10-14-session-kind-and-warnings-design.md", """
---

## Amendments, 2026-10-14, made while proving the plan

The plan (`plans/2026-10-14-session-kind-and-warnings.md`) was proven in code before it was
written. These settle what the sections above left open; §0 stands.

1. **A file holds the warnings of the session's open and of its own runs.** §5 did not say which of
   a session's warnings a file of some of its runs holds. One accepted for a run the file does not
   hold is about that run, and stays out (`nwb/gather.py::_rig_record`).
2. **Every problem names its file.** The run record's problems begin `runs.jsonl line N:`, as the
   new ones begin `config.json:` and `warnings.jsonl line N:`, and the listing's `rig_record_problem`
   message begins "the rig's record:", not "the rig's run record:". wl.works sees the wording change;
   its amendment says so.
3. **§8 answered:**
   - **1:** the generator writes `xcon/` in `synth/peripherals.py::write_rig_runs`, for a recipe with
     runs only. A recipe field, `rig_record` (off by default, refused without runs), adds the kind,
     the calibration and the warnings, so every existing profile's files are byte-identical.
   - **2:** the stage's inserts take `skip_duplicates`, as `core.RunRecord`'s do: a session read
     again adds nothing (tested).
   - **3:** `listing/entry.py::build_entry` builds the entry, and its digest is of the whole entry,
     so the new fields enter it; `gather_all` reads each new table once for every session.
   - **4:** `nwb/describe.py::describe`. CI's "Exported schemas are current" step pins
     `docs/schemas`; both `session_listing.json` and `nwb_description.json` are regenerated.
   - **5:** `<blob>` reads a nested record with nulls back unchanged (tested), and
     `tests/schema/test_guardrails.py` pins the new blob attribute, as it pins every one.
4. **A warning's run that will not fit its column** (below 0, or above 65535) is stored null, as a
   value too long is cut: either would otherwise fail the session's whole event stage on every pass.
5. **The description's new fields default** to null and empty, so a description built before them
   still reads as version 3.
""")

edit("docs/pending-wl-works-amendments.md", [
    ("""**Eight are outstanding: two opened 2026-08-22, one 2026-09-28, one 2026-09-29, two 2026-09-30, one 2026-10-01, one 2026-10-05.** The earlier two""",
     """**Nine are outstanding: two opened 2026-08-22, one 2026-09-28, one 2026-09-29, two 2026-09-30, one 2026-10-01, one 2026-10-05, one 2026-10-14.** The earlier two"""),
    ("""---

# OPEN — subject corrections reach every file already built""",
     """---

# OPEN — what wl-xcon's record says the session was, in the listing and the NWB description

**Opened 2026-10-14** with
[`specs/2026-10-14-session-kind-and-warnings-design.md`](superpowers/specs/2026-10-14-session-kind-and-warnings-design.md)
(§4, §5; the requester's decisions in §0), answering wl-xcon's ask 5 of 2026-10-11 (its XC-325).
wl-xcon's engine build B writes every session's kind (training, piloting or recording), the display
calibration it and each run ran against, and each warning it accepted. **Carried, never acted on
here:** whether a training or piloting session gets a canonical NWB is wl.works' to decide.

- **`GET /sessions`:** each session gains `session_kind`, `calibration` (`id`, `standard`,
  `measured_on`) and `accepted_warnings` (each `code`, `detail`, `accepted_in`, `how`, `run_number`,
  `at_local` and `by`); each run gains `calibration`. `docs/schemas/session_listing.json` refuses
  fields it does not know, so wl.works' copy must take these before it validates an entry from a
  host running this change.
- **Who accepted a warning** is listed by name and wl.works account (`by.kind` is `box` for a name
  typed at the rig, `member` for a wl.works member), never by the token's id or its issuer. The NWB
  file names no one.
- **Every listed session is listed once more** when this ships: each entry changes, so its digest
  does.
- **A `rig_record_problem` flag's message now begins "the rig's record:",** and each problem names
  the file it is about (`runs.jsonl`, `config.json` or `warnings.jsonl`).
- **The NWB description** gains `session_kind`, `calibration`, `accepted_warnings` (each `code` and
  `run_number`) and each run's `calibration`, within `schema_version` 3. A reader that ignores fields
  it does not know needs nothing; selecting a dataset by kind can use them.

---

# OPEN — subject corrections reach every file already built"""),
])

edit("docs/pending-wl-xcon-amendments.md", [
    ("""   eyes is the cost to plan. **Tell us** whether the lab's OpenIrisDPI PC records it and where the
   files land in the session folder: this repository archives every file of a session, and reads
   none of the video yet.
""", """   eyes is the cost to plan. **Tell us** whether the lab's OpenIrisDPI PC records it and where the
   files land in the session folder: this repository archives every file of a session, and reads
   none of the video yet.
   - *wl-xcon's answer, read 2026-10-14 in its `docs/pending-wl-preproc-amendments.md` (section
     "ANSWERED 2026-10-11") on its `main` at `b2c2850`:* the PI, 2026-10-12, "Yes, it will record".
     The folder is not yet named.
"""),
])

edit("HANDOVER-wl-expcontroller.md", [
    ("""- *Not asked:* acting on the warnings; only carrying them.

**6. Onset alignment through the screen log and the flip count.**""",
     """- *Not asked:* acting on the warnings; only carrying them.

- *wl-preproc's answer, 2026-10-14: read and carried, never acted on* (the requester's choice; its
  design `docs/superpowers/specs/2026-10-14-session-kind-and-warnings-design.md`, built on branch
  `feat/session-kind`). The event stage reads `config.json`'s `session_kind` and `calibration`, each
  start row's `calibration` and every `warnings.jsonl` row, by the run record's rules: a line it
  cannot read, a kind outside the three, or another animal's record is reported, not repaired. The
  published record carries them: the session listing for wl.works (who accepted a warning by name and
  wl.works account, never the token) and the NWB file and its description (no person). A
  `config.json` without the fields, written before build B, reads as not recorded. A warning's `run`
  is joined to the measured run's number through its start row.

**6. Onset alignment through the screen log and the flip count.**"""),
])

edit("docs/CHECKPOINT.md", [
    ("""> **Hardware: ask whether it has changed.** It had not on 2026-10-13, when the
> requester chose the eye minors.""",
     """> **Hardware: ask whether it has changed.** It had not on 2026-10-14, when the
> requester chose wl-xcon's ask 5."""),
    ("""> *Until 2026-10-13 this said it had not on 2026-10-11; true when written.*""",
     """> *Until 2026-10-14 this said it had not on 2026-10-13; true when written.*
>
> *Until 2026-10-13 this said it had not on 2026-10-11; true when written.*"""),
    ("""> handoff). Still to build: 1's records, 2's flag, 3, 5, 6, 7 and 8; 4 is
> unread. Open and small: the correction""",
     """> handoff). **wl-xcon's ask 5, the requester's choice of 2026-10-14, is built on
> `feat/session-kind`, not merged** (`handoffs/2026-10-14-session-kind.md`): each
> session's kind, display calibration and accepted warnings, carried into the
> database, the listing and the NWB file. Next: the review. Still to build: 1's
> records, 2's flag, 3, 6, 7 and 8, sorted by what January needs on 2026-10-14:
> 1 next; 3, 6 and 2's flag wait on wl-xcon's builds T and F; 4 is read, its
> record designed by wl-xcon's build K. Open and small: the correction"""),
])

edit("wl.yaml", [
    ("""    Ask whether the hardware has changed; it had not on 2026-10-13. With the""",
     """    Ask whether the hardware has changed; it had not on 2026-10-14. With the"""),
    ("""    build: asks 1, 2's
    flag, 3, 5, 6, 7 and 8.""",
     """    build: asks 1, 2's
    flag, 3, 6, 7 and 8."""),
    ("""    and the eye-loss fix's five (section 7 of each handoff). Open and small:
    the""",
     """    and the eye-loss fix's five (section 7 of each handoff). wl-xcon's ask
    5, the requester's choice of 2026-10-14, is built on feat/session-kind
    and not merged (docs/handoffs/2026-10-14-session-kind.md): each
    session's kind, display calibration and accepted warnings, carried into
    the database, the listing and the NWB file; next, the review. Open and
    small: the"""),
])
print("ok")
```

Expected: it prints `ok`.

- [ ] **Step 2: Write the handoff.** Its §5 keeps `{{...}}` slots until Step 5.

```bash
cat > docs/handoffs/2026-10-14-session-kind.md <<'HANDOFF'
# Carrying a session's kind, its display calibration and its accepted warnings

**wl-xcon's ask 5 of 2026-10-11** (`HANDOVER-wl-expcontroller.md`, item 5; its XC-325): what its engine
build B writes into every session's record, read and carried into the published record.
- **Branch:** `feat/session-kind`, forked from `main` at `ae1d439`.
- **Spec:** `docs/superpowers/specs/2026-10-14-session-kind-and-warnings-design.md`, approved
  2026-10-14, with amendments 1–5 made while proving the plan.
- **Plan:** `docs/superpowers/plans/2026-10-14-session-kind-and-warnings.md`.
- **The requester's choices:** carry only, nothing here acts on the kind; into the database, the
  listing and the NWB file with its description; who accepted a warning not in the NWB file; three
  new tables, none changed.

Every line of the plan was proven in a scratch branch, and replayed from the plan's own text, before
the plan was written.

---

## 1. What was built

- **A reader, `events/rigsession.py`,** for `config.json`'s `session_kind` and `calibration` and for
  `warnings.jsonl`, by the run record's rules; `events/rigruns.py` reads each start row's
  `calibration` and maps wl-xcon's 0-based runs to the runs' numbers.
- **Three tables, none changed:** `core.SessionRecord`, `core.RunCalibration` and
  `core.AcceptedWarning`, written by the event stage; their problems join the run record's in
  `core.RunRecordProblem`, each naming its file.
- **The listing** carries each session's kind, calibration and accepted warnings (by name and
  wl.works account, never the token) and each run's calibration.
- **The NWB file** carries `processing/rig_record` (the session row, and the warnings of its open and
  its own runs, naming no one) and each run's calibration in `/intervals/runs`; its description
  gains the kind, the calibration and the warnings' codes and runs, within version 3.
- **The synthetic generator** writes the record as wl-xcon's code does, for a recipe that asks for
  it (`rig_record`, off by default).

## 2. What the other repositories must do

- **wl.works:** take the listing's new fields and the description's
  (`docs/pending-wl-works-amendments.md`, opened 2026-10-14); every listed session is listed once
  more when this ships.
- **wl-xcon:** nothing; ask 5 is answered in `HANDOVER-wl-expcontroller.md`.

## 3. Still open

- **A session read before this piece** has no record of its kind until its events are read again.
- **The colour model** (`cones`) is in the database and the NWB file's `rig_record`, not the listing
  or the description.
- **Nothing acts on the kind:** the daily report is unchanged, and whether a training or piloting
  session gets a canonical NWB is wl.works' decision.

## 4. The rulings

The spec's amendments 1–5: a file holds the warnings of the open and of its own runs; every problem
names its file, and the listing's flag says "the rig's record"; §8 answered; a run index that will
not fit its column is stored null; the description's new fields default.

## 5. What execution measured

Executed on {{DATE}}, task by task: Task 1 {{SHA1}}, Task 2 {{SHA2}}, Task 3 {{SHA3}}, Task 4
{{SHA4}}. Every count matched the plan's:
- **Each task's tests failed first, then passed with their neighbours,** as the plan states.
- **All 23 mutation checks the plan names failed their tests.**
- **The full suite, once, on both interpreters,** with the BMD, NSLR and Andersson references set
  and `WLPP_OHDPI_REFERENCE` unset:
  - 3.11: **{{S311}}**;
  - 3.13: **{{S313}}**.
- **`wl-check`:** `wl.yaml: no findings`.
HANDOFF
```

- [ ] **Step 3: Check the manifest.**

```bash
.venv/bin/wl-check
```

Expected: `wl.yaml: no findings`.

- [ ] **Step 4: The full suite, once, on both interpreters,** in parallel, with the references CI lacks set
  and `WLPP_OHDPI_REFERENCE` unset, each logged outside the repository:

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

Expected, each exiting 0: 3.11, **2393 passed, 27 skipped, 1 deselected, 1 xfailed**; 3.13, **2393 passed, 28 skipped, 1 xfailed**. About 30 minutes. That is
41 more than `main`'s 2352: this piece's new tests. CI, without the references, should show
about 2384.

- [ ] **Step 5: Fill the handoff's §5** with this execution's own figures: `{{DATE}}` the day, `{{SHA1}}`
  to `{{SHA4}}` Tasks 1-4's commits (`git log --oneline -5`), and `{{S311}}` and `{{S313}}` Step 4's two
  summary lines. Then `grep -c '{{' docs/handoffs/2026-10-14-session-kind.md`.

Expected: `0`.

- [ ] **Step 6: Commit.**

```bash
git add -A docs HANDOVER-wl-expcontroller.md wl.yaml
git commit -F - <<'MSG'
docs: carrying a session's kind -- spec amendments 1-5, the handoff, wl.works' amendment, wl-xcon's ask 5 answered and its raw-video answer recorded, and the checkpoint's and wl.yaml's lines: next, the review

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```
