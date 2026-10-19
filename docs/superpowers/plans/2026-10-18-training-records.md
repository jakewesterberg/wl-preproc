# The Training Records (Part 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Keep wl-xcon's training records, `parameter_changes.jsonl` and `controls.jsonl`, in the database, the
NWB file and the listing, each row timed by the code it strobed or the rig's clock fitted to the recording, on
sessions with the sync box.

**Architecture:** Two pure modules read the files (`events/rigrecords.py`) and time their rows
(`events/rigtiming.py`, which also fits the rig's clock); the events stage writes `core.ParameterChange`,
`core.RigControl` and `core.RigClock` beside ask 5's rig tables; the NWB gather, writer and description and
the listing read those tables. The synthetic generator plants the acts, their codes and a drifting, jittered
rig clock, so every test asserts recovery of what was planted.

**Tech Stack:** Python 3.11/3.13, DataJoint 2.3.3 on MySQL (testcontainers in tests), pynwb, pydantic.

**Spec:** `docs/superpowers/specs/2026-10-18-training-records-and-backup-clock-design.md` (approved
2026-10-18): §2's part 1 and §3, with §10's items 1, 6, 7 and 10 verified. Amendment 2 records wl-xcon's
`event_code` and its three corrections (`plan/training-records`, before this plan); amendments 9-16 are Task
7's, made while proving it.

**Proven before written.** Every step below was run on a scratch branch and replayed from this plan's own
text to an identical tree: 26 mutation checks caught, and the full suite on both interpreters as
Task 7 states.

## Global Constraints

- **On sessions with the sync box** (spec §2, part 1). A session the sync box did not record is part 3's, and
  its events stage still raises, as part 2 left it.
- **Never guessed** (§3.3): a row is timed by the code its act strobed, or, for a control that strobed nothing,
  by the rig's clock fit; otherwise it is untimed, and a count that disagrees names both counts.
- **Who acted** is kept in the database as the rig wrote it; the NWB file gives only the kind of actor
  (decision 6), and the listing carries counts only (decision 7).
- **No code's number is read as a meaning** (ADR-0007): each row says which number it strobed (amendment 2).
  The synthetic generator uses wl-xcon's reference numbers (4130 to 4134) as fixture data only.
- **The contracts widen within themselves** (amendment 14): the NWB description within version 3, the listing
  by two fields; `docs/schemas` is regenerated with `wlpp schemas export`, never edited.
- **Execute on a branch `feat/training-records`** cut from `plan/training-records` once this plan is committed
  there.
- **Python 3.11 and 3.13**: the full suite runs once on each, at the end (Task 7).
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code,
  data or tables; leave the stray `wl-preproc` symlink at the repository root out of every commit.
- **Commands run from the repository root.** In a git worktree, `.venv/bin/python` is the main checkout's;
  every test command sets `PYTHONPATH=$PWD` and `PYTHONDONTWRITEBYTECODE=1` (a stale `.pyc` can defeat a
  mutation check).
- **Patches apply with `git apply` and must apply cleanly**; each step's patch is the exact change.
- **Neighbour runs name their files in the suite's order** (amendment 7): `tests/schema/test_guardrails.py`
  run before `tests/schema/test_daemon.py` fails three of its clean-pass tests.
- **Every commit ends with:**
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
  ```

## Review Focus

The five inputs the spec implies that a person is most likely to meet, most likely first; each is pinned by a
test in the task that owns the code:
1. **A session wl-xcon resumed,** whose second process anchored the rig's clock again: no fit, so a control that
   strobed nothing is untimed and the problem says why, never timed by a line that misses its instants.
   `test_a_rig_clock_re_anchored_mid_session_has_no_fit` (Task 2).
2. **A value MySQL's `json` type would round or refuse** (a seventeen-digit double; NaN, which `json.dumps`
   writes): kept exactly as written, and the stage does not fail. `test_a_value_mysqls_json_type_would_round_
   or_refuse_is_kept_as_written` (Task 4).
3. **A line torn by a killed process,** half a last line with no newline: named by file and line, the rows before
   it kept. `test_a_line_torn_by_a_killed_process_is_named_and_the_rows_before_it_kept` (Task 1).
4. **A task's own code with a framework code's number** (wl-xcon's XC-382): that number's rows in that file are
   untimed and named; other numbers' rows are timed. `test_a_tasks_own_code_with_a_framework_codes_number_
   untimes_that_numbers_rows` (Task 2).
5. **A session event-staged before this part,** its record kept and no training rows: listed with zeros, never
   null. `test_a_session_read_before_its_training_records_were_kept_lists_zeros` (Task 6).

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
| `wl_preproc/events/rigrecords.py` (new) | read both files as written | 1 |
| `wl_preproc/events/rigtiming.py` (new), `events/rigruns.py` | the joins by code; the rig's clock fit; the run rows' instants | 2 |
| `wl_preproc/synth/recipe.py`, `timeline.py`, `truth.py`, `peripherals.py` | the planted acts, their codes and the rig's clock | 3 |
| `wl_preproc/schema/core.py`, `schema/events.py` | the three tables; the events stage writes them | 4 |
| `wl_preproc/nwb/gather.py`, `rig_record.py`, `describe.py`, `contracts/nwb_description.py` | the file's two tables; the description's counts | 5 |
| `wl_preproc/listing/entry.py`, `contracts/protocol.py` | the listing's counts and clock | 6 |
| `docs/ops/lab-host-protocol.md`, `docs/schemas/*.json` | the wire contract, named and exported | 5, 6 |
| the spec, `docs/handoffs/2026-10-18-training-records.md` (new), `docs/pending-wl-works-amendments.md`, `docs/CHECKPOINT.md`, `wl.yaml` | the records | 7 |

---

### Task 1: Read the training records as written

`parameter_changes.jsonl` and `controls.jsonl` read as wl-xcon writes them today (spec §3.1, amendment 2): each row's line and values kept as JSON text, its `event_code` read as a code, as none, or as unknown, and an unreadable line named by file and line. Nothing is stored yet.

**Files:**
- Create: `wl_preproc/events/rigrecords.py`
- Test: `tests/events/test_rigrecords.py` (new)

**Interfaces:**
- Consumes: `contracts/paths.py::XCON_DIRNAME`.
- Produces: `events/rigrecords.py`:
  - `CHANGES_NAME = "parameter_changes.jsonl"`, `CONTROLS_NAME = "controls.jsonl"`;
  - `ParameterChange` (frozen): `line: int`, `sequence: int | None`, `name: str`, `was: str | None`,
    `now: str | None` (JSON text), `run_index: int | None`, `event_code: int | None`, `coded: bool`,
    `by_kind`, `by_name`, `by_account`, `by_issuer`, `by_token_id` (`str | None`), `row: str`;
  - `Control` (frozen): `line: int`, `kind: str`, `at: float | None`, `at_local: str | None`,
    `trial_index: int | None`, `run_index: int | None`, `event_code: int | None`, `coded: bool`,
    `detail: str` (JSON text), the five `by_*`, `row: str`;
  - `RigRecords(changes: tuple[ParameterChange, ...], controls: tuple[Control, ...], problems: tuple[str, ...])`;
  - `read_rig_records(session_dir: Path) -> RigRecords`.
  `coded` is False when the row does not say which code it strobed: no key, or a value that is not a code
  from 1 to 65535.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/events/test_rigrecords.py b/tests/events/test_rigrecords.py
new file mode 100644
index 0000000..4ce39d4
--- /dev/null
+++ b/tests/events/test_rigrecords.py
@@ -0,0 +1,132 @@
+"""wl-xcon's training records as it writes them today: `xcon/parameter_changes.jsonl`
+and `xcon/controls.jsonl` (design spec
+`2026-10-18-training-records-and-backup-clock-design.md` section 3.1, amendment 2)."""
+
+from __future__ import annotations
+
+import json
+
+MEMBER = {"kind": "member", "name": "J. W.", "account": "acct-1", "issuer": "https://wl.works", "token_id": "tok-1"}
+BOX = {"kind": "box", "name": "rig-a"}
+
+
+def _record(tmp_path, changes=None, controls=None):
+    (tmp_path / "xcon").mkdir()
+    for name, lines in (("parameter_changes.jsonl", changes), ("controls.jsonl", controls)):
+        if lines is not None:
+            (tmp_path / "xcon" / name).write_text("".join(line + "\n" for line in lines), encoding="utf-8")
+    return tmp_path
+
+
+def _change(name="reward_ml", was=0.2, now=0.25, **fields):
+    return json.dumps({"sequence": 1, "name": name, "was": was, "now": now, "by": MEMBER, "run": 0,
+                       "event_code": 4130, **fields}, sort_keys=True)
+
+
+def _control(kind="pause", **fields):
+    return json.dumps({"kind": kind, "by": BOX, "at": 1773478800.25, "at_local": "2027-03-14T10:00:00.250000+01:00",
+                       "trial_index": 3, "event_code": 4131, "run": 0, **fields}, sort_keys=True)
+
+
+def test_every_change_is_kept_as_written(tmp_path):
+    """Its line as written, not as JSON would write it again; its values as
+    JSON text; its actor as `core.AcceptedWarning` holds one."""
+    from wl_preproc.events.rigrecords import read_rig_records
+
+    line = _change(was=[1, 2.5], now={"x": None, "y": "ten"}).replace('": ', '":')
+    (change,) = read_rig_records(_record(tmp_path, changes=[line])).changes
+    assert (change.line, change.sequence, change.name, change.run_index, change.event_code, change.coded) == (
+        1, 1, "reward_ml", 0, 4130, True)
+    assert (json.loads(change.was), json.loads(change.now)) == ([1, 2.5], {"x": None, "y": "ten"})
+    assert (change.by_kind, change.by_name, change.by_account, change.by_issuer, change.by_token_id) == (
+        "member", "J. W.", "acct-1", "https://wl.works", "tok-1")
+    assert change.row == line
+
+
+def test_every_control_is_kept_with_its_own_fields_as_its_detail(tmp_path):
+    """`detail` is what the row carries beyond a control's common fields: a
+    manual reward's mL, a mark's frame and number."""
+    from wl_preproc.events.rigrecords import read_rig_records
+
+    line = _control("reward", event_code=4134, ml=0.1, entry="reward_ml", where="paused before trial 3")
+    (control,) = read_rig_records(_record(tmp_path, controls=[line])).controls
+    assert (control.line, control.kind, control.at, control.at_local, control.trial_index, control.run_index,
+            control.event_code, control.coded) == (1, "reward", 1773478800.25, "2027-03-14T10:00:00.250000+01:00",
+                                                    3, 0, 4134, True)
+    assert json.loads(control.detail) == {"ml": 0.1, "entry": "reward_ml", "where": "paused before trial 3"}
+    assert (control.by_kind, control.by_name, control.by_account) == ("box", "rig-a", None)
+    assert control.row == line
+
+
+def test_a_row_that_strobed_nothing_says_so_and_one_written_before_the_field_does_not_say(tmp_path):
+    """Amendment 2: `null` is a row whose act strobed nothing; a row without
+    the key was written before wl-xcon carried it, and is unknown, never
+    null. A value that is not a code says nothing either."""
+    from wl_preproc.events.rigrecords import read_rig_records
+
+    lines = [_control("note", event_code=None),
+             json.dumps({k: v for k, v in json.loads(_control()).items() if k != "event_code"}),
+             _control(event_code="4131"), _control(event_code=0), _control(event_code=70000)]
+    controls = read_rig_records(_record(tmp_path, controls=lines)).controls
+    assert [(control.event_code, control.coded) for control in controls] == [
+        (None, True), (None, False), (None, False), (None, False), (None, False)]
+
+
+def test_a_control_by_nobody_has_no_actor(tmp_path):
+    """wl-xcon writes `by: null` for a control nobody sent: a mark, a
+    scheduled stop firing."""
+    from wl_preproc.events.rigrecords import read_rig_records
+
+    (control,) = read_rig_records(_record(tmp_path, controls=[_control("mark", by=None)])).controls
+    assert (control.by_kind, control.by_name, control.by_account, control.by_issuer, control.by_token_id) == (
+        None, None, None, None, None)
+
+
+def test_an_unreadable_line_is_named_by_file_and_line_and_left_out(tmp_path):
+    from wl_preproc.events.rigrecords import read_rig_records
+
+    records = read_rig_records(_record(
+        tmp_path, changes=[_change(), "{not json", json.dumps({"was": 1}), "", _change(name="seed")],
+        controls=["[1, 2]", _control(), json.dumps({"kind": ""})]))
+    assert [change.line for change in records.changes] == [1, 5]
+    assert [control.line for control in records.controls] == [2]
+    assert records.problems == (
+        "parameter_changes.jsonl line 2: not JSON",
+        "parameter_changes.jsonl line 3: not a change naming its setting",
+        "controls.jsonl line 1: not a control naming its kind",
+        "controls.jsonl line 3: not a control naming its kind",
+    )
+
+
+def test_a_line_torn_by_a_killed_process_is_named_and_the_rows_before_it_kept(tmp_path):
+    """The plan's Review Focus 3: a process killed while appending leaves
+    half a last line and no newline."""
+    from wl_preproc.events.rigrecords import read_rig_records
+
+    (tmp_path / "xcon").mkdir()
+    (tmp_path / "xcon" / "parameter_changes.jsonl").write_text(
+        _change() + "\n" + _change(name="seed") + "\n" + '{"sequence": 3, "na', encoding="utf-8")
+    records = read_rig_records(tmp_path)
+    assert [change.line for change in records.changes] == [1, 2]
+    assert records.problems == ("parameter_changes.jsonl line 3: not JSON",)
+
+
+def test_a_value_that_is_not_what_its_field_holds_is_read_as_none(tmp_path):
+    """As `events/rigsession.py` reads one: a number that is not finite, a
+    count that is not a whole number, read as none rather than failing the
+    session's event stage on every pass."""
+    from wl_preproc.events.rigrecords import read_rig_records
+
+    records = read_rig_records(_record(
+        tmp_path, changes=[_change(sequence="7", run="0")],
+        controls=[_control(at=float("nan"), trial_index=2.5, run=True, at_local=5)]))
+    (change,), (control,) = records.changes, records.controls
+    assert (change.sequence, change.run_index) == (None, None)
+    assert (control.at, control.trial_index, control.run_index, control.at_local) == (None, None, None, "5")
+    assert records.problems == ()
+
+
+def test_a_session_without_either_file_has_no_records_and_no_problem(tmp_path):
+    from wl_preproc.events.rigrecords import RigRecords, read_rig_records
+
+    assert read_rig_records(_record(tmp_path)) == RigRecords(changes=(), controls=(), problems=())
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/events/test_rigrecords.py -q -p no:cacheprovider
```

Expected: `8 failed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/events/rigrecords.py b/wl_preproc/events/rigrecords.py
new file mode 100644
index 0000000..7558646
--- /dev/null
+++ b/wl_preproc/events/rigrecords.py
@@ -0,0 +1,175 @@
+"""wl-xcon's training records as it writes them today: `xcon/parameter_changes.jsonl`,
+one row per live change the rig applied, and `xcon/controls.jsonl`, one row per
+console control (design spec `2026-10-18-training-records-and-backup-clock-design.md`
+section 3.1; wl-xcon's `wl_xcon/record.py`, `parameter_change` and `control`).
+
+**Kept as written.** Each row's line is kept whole, and its values as JSON text,
+so a field wl-xcon adds (XC-288's change id, instant, trial, meaning and
+direction) is kept before any reader here knows it. Who acted is kept as
+`core.AcceptedWarning` keeps it.
+
+**Read, never repaired, by `events/rigruns.py`'s rules.** A line that is not a
+JSON object naming its setting (a change) or its kind (a control) is reported by
+file and line and left out. A value that is not what its field holds (a count
+that is not a whole number, an instant that is not a finite number) is read as
+none.
+
+**`event_code`, the number the row's act strobed** (spec amendment 2): an
+integer, or null when it strobed nothing. A row written before wl-xcon carried
+it has no key, which is read as unknown, never as null; so is a value that is
+not a 16-bit code. `events/rigtiming.py` joins the rows to the recording by it.
+
+**This session's only:** the caller reads these for a session whose
+`config.json` is its own (`events/rigsession.py`'s `of_this_session`)."""
+
+from __future__ import annotations
+
+import dataclasses
+import json
+import math
+from pathlib import Path
+
+from wl_preproc.contracts.paths import XCON_DIRNAME
+
+CHANGES_NAME = "parameter_changes.jsonl"
+CONTROLS_NAME = "controls.jsonl"
+# What every control row carries (`record.py::control`, its `run` passed by
+# `taskd.py::_control`); the rest of a row is the control's own detail.
+_CONTROL_FIELDS = frozenset({"kind", "by", "at", "at_local", "trial_index", "event_code", "run"})
+_COUNT_MAX = 2**32 - 1  # an `int unsigned` column
+_CODE_MAX = 0xFFFF
+
+
+@dataclasses.dataclass(frozen=True, slots=True)
+class ParameterChange:
+    """One row of `parameter_changes.jsonl`. `was` and `now` are JSON text,
+    None when the row has no such key. `coded` is False when the row does not
+    say which code it strobed."""
+
+    line: int
+    sequence: int | None
+    name: str
+    was: str | None
+    now: str | None
+    run_index: int | None
+    event_code: int | None
+    coded: bool
+    by_kind: str | None
+    by_name: str | None
+    by_account: str | None
+    by_issuer: str | None
+    by_token_id: str | None
+    row: str
+
+
+@dataclasses.dataclass(frozen=True, slots=True)
+class Control:
+    """One row of `controls.jsonl`. `at` is the rig's anchored wall clock,
+    POSIX seconds; `detail` is the row's own fields as JSON text."""
+
+    line: int
+    kind: str
+    at: float | None
+    at_local: str | None
+    trial_index: int | None
+    run_index: int | None
+    event_code: int | None
+    coded: bool
+    detail: str
+    by_kind: str | None
+    by_name: str | None
+    by_account: str | None
+    by_issuer: str | None
+    by_token_id: str | None
+    row: str
+
+
+@dataclasses.dataclass(frozen=True, slots=True)
+class RigRecords:
+    changes: tuple[ParameterChange, ...]
+    controls: tuple[Control, ...]
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
+def _count(value: object) -> int | None:
+    number = _integer(value)
+    return number if number is not None and 0 <= number <= _COUNT_MAX else None
+
+
+def _number(value: object) -> float | None:
+    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
+        return None
+    return float(value)
+
+
+def _json(value: object) -> str:
+    return json.dumps(value, sort_keys=True, ensure_ascii=False)
+
+
+def _code(row: dict) -> tuple[int | None, bool]:
+    """The row's code and whether it says one: `(None, True)` for a row whose
+    act strobed nothing, `(None, False)` for one that does not say."""
+    if "event_code" not in row:
+        return None, False
+    value = row["event_code"]
+    if value is None:
+        return None, True
+    code = _integer(value)
+    return (code, True) if code is not None and 1 <= code <= _CODE_MAX else (None, False)
+
+
+def _actor(row: dict) -> dict:
+    by = row.get("by") if isinstance(row.get("by"), dict) else {}
+    return {f"by_{field}": _text(by.get(field)) for field in ("kind", "name", "account", "issuer", "token_id")}
+
+
+def _rows(path: Path, problems: list[str], field: str, what: str) -> list[tuple[int, str, dict]]:
+    """Each line of `path` that is a JSON object naming its `field`, with its
+    number and its text; each other line is a problem, `what` saying what it
+    is not."""
+    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
+    rows = []
+    for number_of_line, line in enumerate(lines, start=1):
+        if not line.strip():
+            continue
+        try:
+            row = json.loads(line)
+        except json.JSONDecodeError:
+            problems.append(f"{path.name} line {number_of_line}: not JSON")
+            continue
+        if not isinstance(row, dict) or not isinstance(row.get(field), str) or not row[field]:
+            problems.append(f"{path.name} line {number_of_line}: {what}")
+            continue
+        rows.append((number_of_line, line, row))
+    return rows
+
+
+def read_rig_records(session_dir: Path) -> RigRecords:
+    """The session's training records from `<session>/xcon/`; none where a
+    file is absent."""
+    xcon = Path(session_dir) / XCON_DIRNAME
+    problems: list[str] = []
+    changes = []
+    for line, text, row in _rows(xcon / CHANGES_NAME, problems, "name", "not a change naming its setting"):
+        code, coded = _code(row)
+        changes.append(ParameterChange(
+            line=line, sequence=_count(row.get("sequence")), name=row["name"],
+            was=_json(row["was"]) if "was" in row else None, now=_json(row["now"]) if "now" in row else None,
+            run_index=_integer(row.get("run")), event_code=code, coded=coded, **_actor(row), row=text))
+    controls = []
+    for line, text, row in _rows(xcon / CONTROLS_NAME, problems, "kind", "not a control naming its kind"):
+        code, coded = _code(row)
+        controls.append(Control(
+            line=line, kind=row["kind"], at=_number(row.get("at")), at_local=_text(row.get("at_local")),
+            trial_index=_count(row.get("trial_index")), run_index=_integer(row.get("run")), event_code=code,
+            coded=coded, detail=_json({key: value for key, value in row.items() if key not in _CONTROL_FIELDS}),
+            **_actor(row), row=text))
+    return RigRecords(changes=tuple(changes), controls=tuple(controls), problems=tuple(problems))
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/events/test_rigrecords.py -q -p no:cacheprovider
```

Expected: `8 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/events -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `63 passed`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "a missing event_code read as none",
  "wl_preproc/events/rigrecords.py",
  "    if \"event_code\" not in row:\n        return None, False",
  "    if \"event_code\" not in row:\n        return None, True",
  "tests/events/test_rigrecords.py"
 ],
 [
  "detail keeps the common fields",
  "wl_preproc/events/rigrecords.py",
  "if key not in _CONTROL_FIELDS})",
  "if key != \"kind\"})",
  "tests/events/test_rigrecords.py"
 ],
 [
  "a change's line reserialised",
  "wl_preproc/events/rigrecords.py",
  "run_index=_integer(row.get(\"run\")), event_code=code, coded=coded, **_actor(row), row=text))",
  "run_index=_integer(row.get(\"run\")), event_code=code, coded=coded, **_actor(row), row=json.dumps(row)))",
  "tests/events/test_rigrecords.py"
 ],
 [
  "code 0 read as a code",
  "wl_preproc/events/rigrecords.py",
  "1 <= code <= _CODE_MAX",
  "0 <= code <= _CODE_MAX",
  "tests/events/test_rigrecords.py"
 ]
]
```

Expected: 4 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(events): read wl-xcon's training records as written -- parameter_changes.jsonl and controls.jsonl, each line and value kept as JSON text, each row's event_code read as a code, as none or as unknown, and an unreadable line named by file and line (design spec 2026-10-18-training-records-and-backup-clock-design.md section 3.1, amendment 2)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 2: Time the records by their codes and the rig's clock

The joins (spec §3.3, amendment 2): the n-th row of a file carrying a number takes the n-th occurrence of that number among the session's simple events; a count that disagrees leaves that number's rows untimed and names both counts. The rig's clock is fitted from the run rows' and coded controls' instants against their codes, and times a control that strobed nothing; no fit past `RIG_CLOCK_LIMIT_S`, 50 ms, measured against the synthetic record's stated 5 ms jitter (§10 item 10; amendment 11). Each run row's instant is read from `runs.jsonl`. Pure: no database.

**Files:**
- Create: `wl_preproc/events/rigtiming.py`
- Modify: `wl_preproc/events/rigruns.py` (`RigRun.start_at`, `end_at`)
- Test: `tests/events/test_rigtiming.py` (new), `tests/events/test_rigruns.py` (two existing equalities now name the instants; one test added)

**Interfaces:**
- Consumes: Task 1's `RigRecords`, `ParameterChange`, `Control`, `CHANGES_NAME`, `CONTROLS_NAME`;
  `contracts/events.py::SimpleEvent`.
- Produces:
  - `events/rigruns.py::RigRun` gains `start_at: float | None = None` and `end_at: float | None = None`,
    each row's `at` when it is a finite number;
  - `events/rigtiming.py`: `RIG_CLOCK_LIMIT_S = 0.05`;
    `RigClock(rig_at_zero, drift_ppm, n_matched, residual_ms_rms, residual_ms_max)` with
    `session_time(at: float) -> float`;
    `Timing(changes: tuple[float | None, ...], controls: tuple[tuple[float | None, str | None], ...],
    clock: RigClock | None, problems: tuple[str, ...])`;
    `code_times(decoded) -> dict[int, list[float]]`;
    `run_instants(runs, measured: Mapping[int, tuple[float, float | None]]) -> list[tuple[float, float]]`;
    `fit_rig_clock(pairs) -> RigClock | str` (the string says why there is no fit);
    `time_records(records, codes, instants) -> Timing`. A control's source is `"code"` or `"rig_clock"`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/events/test_rigruns.py b/tests/events/test_rigruns.py
index db61ef5..b14ad43 100644
--- a/tests/events/test_rigruns.py
+++ b/tests/events/test_rigruns.py
@@ -32,8 +32,9 @@ def test_a_run_is_its_start_row_and_its_end_row(tmp_path):
     rows = [_start(0, "fixation_detection"), _end(0, "every block is finished", "completed"),
             _start(1, "rf_map"), _end(1, "stopped by jw", "operator")]
     record = read_rig_runs(_record(tmp_path, rows), "pico")
-    assert record.runs == (RigRun(1, "fixation_detection", "every block is finished", "completed"),
-                           RigRun(2, "rf_map", "stopped by jw", "operator"))
+    assert record.runs == (RigRun(1, "fixation_detection", "every block is finished", "completed", start_at=0.0,
+                                  end_at=1.0),
+                           RigRun(2, "rf_map", "stopped by jw", "operator", start_at=0.0, end_at=1.0))
     assert record.problems == ()
 
 
@@ -89,7 +90,7 @@ def test_a_run_named_twice_keeps_its_first_rows_and_says_so(tmp_path):
     rows = [_start(0, "rf_map"), _end(0, "every block is finished", "completed"),
             _start(0, "fixation"), _end(0, "stopped by jw", "operator")]
     record = read_rig_runs(_record(tmp_path, rows), "pico")
-    assert record.runs == (RigRun(1, "rf_map", "every block is finished", "completed"),)
+    assert record.runs == (RigRun(1, "rf_map", "every block is finished", "completed", start_at=0.0, end_at=1.0),)
     assert record.problems == ("runs.jsonl line 3: run 0 has a second start row; the first is kept",
                                "runs.jsonl line 4: run 0 has a second end row; the first is kept")
 
@@ -129,3 +130,16 @@ def test_a_run_without_its_start_row_maps_to_no_number(tmp_path):
     record = read_rig_runs(_record(tmp_path, rows), "pico")
     assert [run.number for run in record.runs] == [1, 2]
     assert record.run_numbers == {0: 1}
+
+
+def test_a_runs_instants_are_its_rows_on_the_rigs_clock(tmp_path):
+    """Each row's `at`, the rig's anchored wall clock, which the training
+    records' clock fit matches to the run's codes (design spec
+    `2026-10-18-training-records-and-backup-clock-design.md` section 3.3); an
+    instant that is not a finite number is none."""
+    from wl_preproc.events.rigruns import read_rig_runs
+
+    rows = [_start(0, "rf_map", at=1773478800.5), _end(0, "every block is finished", "completed"),
+            _start(1, "rf_map", at="soon"), json.dumps({"event": "end", "run": 1, "stopped_because": "stopped by jw"})]
+    runs = read_rig_runs(_record(tmp_path, rows), "pico").runs
+    assert [(run.start_at, run.end_at) for run in runs] == [(1773478800.5, 1.0), (None, None)]
diff --git a/tests/events/test_rigtiming.py b/tests/events/test_rigtiming.py
new file mode 100644
index 0000000..b985962
--- /dev/null
+++ b/tests/events/test_rigtiming.py
@@ -0,0 +1,190 @@
+"""Placing wl-xcon's training records on the session's clock: each row by the
+code its act strobed, and a control that strobed nothing through the rig's
+clock fit (design spec `2026-10-18-training-records-and-backup-clock-design.md`
+section 3.3, amendment 2)."""
+
+from __future__ import annotations
+
+import random
+
+import pytest
+
+# The rig's anchored wall clock at session t = 0, POSIX seconds, and its rate.
+ZERO = 1_773_478_790.0
+DRIFT_PPM = 20.0
+
+
+def _rig(session_s: float, jitter: float = 0.0) -> float:
+    return ZERO + session_s * (1 + DRIFT_PPM / 1e6) + jitter
+
+
+def _change(line, code=4130, coded=True):
+    from wl_preproc.events.rigrecords import ParameterChange
+
+    return ParameterChange(line=line, sequence=line, name="reward_ml", was="0.2", now="0.25", run_index=0,
+                           event_code=code, coded=coded, by_kind="box", by_name="rig-a", by_account=None,
+                           by_issuer=None, by_token_id=None, row="{}")
+
+
+def _control(line, kind="pause", code=4131, at=None, coded=True):
+    from wl_preproc.events.rigrecords import Control
+
+    return Control(line=line, kind=kind, at=at, at_local=None, trial_index=line, run_index=0, event_code=code,
+                   coded=coded, detail="{}", by_kind="box", by_name="rig-a", by_account=None, by_issuer=None,
+                   by_token_id=None, row="{}")
+
+
+def _records(changes=(), controls=()):
+    from wl_preproc.events.rigrecords import RigRecords
+
+    return RigRecords(changes=tuple(changes), controls=tuple(controls), problems=())
+
+
+def test_the_sessions_codes_are_its_simple_events_by_number_not_a_payloads_words():
+    """A payload word equal to a framework code's number is not that code:
+    `decode_stream` reads payloads by position, and only its simple events
+    are codes a row can have strobed."""
+    from wl_preproc.contracts.events import Escape, decode_stream, encode_payload
+    from wl_preproc.events.rigtiming import code_times
+
+    words = [(1.0, 4130), *((2.0 + 0.001 * i, word) for i, word in enumerate(encode_payload(
+        Escape.TRIAL_NUMBER, [0, 4130]))), (3.0, 4131), (4.0, 4130)]
+    assert code_times(decode_stream(words)) == {4130: [1.0, 4.0], 4131: [3.0]}
+
+
+def test_each_row_takes_the_time_of_its_numbers_occurrence_in_order():
+    """The n-th row of a file carrying a number takes the n-th occurrence of
+    that number; each file's rows are counted alone."""
+    from wl_preproc.events.rigtiming import time_records
+
+    codes = {4130: [10.0, 20.0], 4131: [12.0, 30.0], 4132: [14.0], 4134: [13.0]}
+    timing = time_records(_records(
+        changes=[_change(1), _change(2)],
+        controls=[_control(1), _control(2, "reward", 4134), _control(3, "resume", 4132), _control(4)]), codes, [])
+    assert timing.changes == (10.0, 20.0)
+    assert timing.controls == ((12.0, "code"), (13.0, "code"), (14.0, "code"), (30.0, "code"))
+    assert timing.problems == ()
+
+
+def test_a_count_that_disagrees_leaves_that_numbers_rows_untimed_and_names_both_counts():
+    """A code the recording lost, or one strobed with no row (wl-xcon's
+    corrections 2 and 3), leaves that number's rows untimed; the other
+    numbers' rows are timed."""
+    from wl_preproc.events.rigtiming import time_records
+
+    codes = {4130: [10.0, 20.0, 25.0], 4131: [12.0], 4134: [13.0, 15.0]}
+    timing = time_records(_records(
+        changes=[_change(1), _change(2)],
+        controls=[_control(1), _control(2, "reward", 4134)]), codes, [])
+    assert timing.changes == (None, None)
+    assert timing.controls == ((12.0, "code"), (None, None))
+    assert timing.problems == (
+        "parameter_changes.jsonl: 2 rows carry code 4130 and the recording holds 3, so none of them is timed",
+        "controls.jsonl: 1 row carries code 4134 and the recording holds 2, so it is not timed",
+    )
+
+
+def test_a_tasks_own_code_with_a_framework_codes_number_untimes_that_numbers_rows():
+    """The plan's Review Focus 4: a task's mark strobing `PARAM_CHANGED`'s
+    number (wl-xcon's XC-382, a load-time warning there) is one more such
+    code than the change log has rows, so no change is timed by it; the
+    pause, another number, still is."""
+    from wl_preproc.events.rigtiming import time_records
+
+    timing = time_records(_records(changes=[_change(1), _change(2)], controls=[_control(1)]),
+                          {4130: [5.0, 10.0, 20.0], 4131: [12.0]}, [])
+    assert (timing.changes, timing.controls) == ((None, None), ((12.0, "code"),))
+    assert timing.problems == (
+        "parameter_changes.jsonl: 2 rows carry code 4130 and the recording holds 3, so none of them is timed",)
+
+
+def test_a_row_that_does_not_say_its_code_is_untimed():
+    """Amendment 2: written before wl-xcon carried `event_code`, it is
+    unknown, so neither joined nor fitted; one problem per file."""
+    from wl_preproc.events.rigtiming import time_records
+
+    timing = time_records(_records(changes=[_change(1, None, coded=False), _change(2, None, coded=False)],
+                                   controls=[_control(1, "note", None, at=_rig(5.0), coded=False)]),
+                          {4130: [10.0]}, [(_rig(0.0), 0.0), (_rig(100.0), 100.0)])
+    assert (timing.changes, timing.controls) == ((None, None), ((None, None),))
+    assert timing.problems == (
+        "parameter_changes.jsonl: 2 rows do not say which code they strobed, so they are not timed",
+        "controls.jsonl: 1 row does not say which code it strobed, so it is not timed",
+    )
+
+
+def test_a_control_that_strobed_nothing_is_timed_through_the_rigs_clock():
+    """The run rows' instants and the coded controls' instants, against
+    their codes, fit the rig's clock; a note, which strobes nothing, is
+    timed through it."""
+    from wl_preproc.events.rigtiming import time_records
+
+    timing = time_records(_records(controls=[_control(1, at=_rig(12.0)), _control(2, "note", None, at=_rig(40.0))]),
+                          {4131: [12.0]}, [(_rig(0.0), 0.0), (_rig(60.0), 60.0)])
+    assert timing.controls[0] == (12.0, "code")
+    time, source = timing.controls[1]
+    assert source == "rig_clock" and time == pytest.approx(40.0, abs=1e-6)
+    clock = timing.clock
+    assert (clock.n_matched, clock.drift_ppm, clock.rig_at_zero) == (3, pytest.approx(DRIFT_PPM, abs=0.01),
+                                                                     pytest.approx(ZERO, abs=1e-5))
+    assert timing.problems == ()
+
+
+def test_the_rigs_clock_is_fitted_within_its_stated_jitter():
+    """Spec section 10, item 10: instants stamped up to 5 ms either side of
+    their codes (`synth/peripherals.py`'s `RIG_STAMP_JITTER_S`), over a short
+    session and an hour, fit within the limit, the drift found to the ppm
+    the jitter allows."""
+    from wl_preproc.events.rigtiming import RIG_CLOCK_LIMIT_S, fit_rig_clock
+
+    generator = random.Random(20271018)
+    for duration_s, n in ((15.0, 4), (3600.0, 60)):
+        times = sorted(generator.uniform(0.0, duration_s) for _ in range(n - 2)) + [0.0, duration_s]
+        clock = fit_rig_clock([(_rig(time, generator.uniform(-0.005, 0.005)), time) for time in times])
+        assert clock.residual_ms_max <= 10.0 < RIG_CLOCK_LIMIT_S * 1e3
+        assert clock.drift_ppm == pytest.approx(DRIFT_PPM, abs=0.01 / duration_s * 1e6)
+
+
+def test_a_rig_clock_re_anchored_mid_session_has_no_fit():
+    """A resumed session's process anchors the rig's clock again; one line
+    cannot follow the step, so there is no fit, and the controls that needed
+    it say why."""
+    from wl_preproc.events.rigtiming import time_records
+
+    step = 2.0
+    instants = [(_rig(0.0), 0.0), (_rig(30.0), 30.0), (_rig(40.0) + step, 40.0), (_rig(90.0) + step, 90.0)]
+    timing = time_records(_records(controls=[_control(1, "note", None, at=_rig(50.0) + step),
+                                             _control(2, "schedule", None, at=_rig(60.0) + step)]), {}, instants)
+    assert timing.clock is None
+    assert timing.controls == ((None, None), (None, None))
+    (problem,) = timing.problems
+    assert problem.startswith("controls.jsonl: 2 rows strobed nothing, and the rig's clock could not be fitted "
+                              "to the recording (its instants miss their codes by up to ")
+    assert problem.endswith(" s, past the 0.05 s limit), so they are not timed")
+
+
+def test_fewer_than_two_matched_instants_is_no_fit():
+    from wl_preproc.events.rigtiming import fit_rig_clock
+
+    assert fit_rig_clock([]) == "none of its instants matched a code"
+    assert fit_rig_clock([(_rig(3.0), 3.0)]) == "only 1 of its instants matched a code, and a fit needs 2"
+    assert fit_rig_clock([(_rig(3.0), 3.0), (_rig(3.0) + 0.001, 3.0)]) == (
+        "its instants matched codes at a single time")
+
+
+def test_the_measured_runs_give_the_rigs_start_and_end_instants():
+    """Each run's start row against its measured `RUN_START`; its end row
+    against its `RUN_END`, only when the run closed: a run that faulted sent
+    none, and its stop is its last event."""
+    from wl_preproc.events.rigruns import RigRun
+    from wl_preproc.events.rigtiming import run_instants
+
+    runs = [RigRun(number=1, task=None, stopped_because=None, stop_kind=None, start_at=_rig(0.5), end_at=_rig(9.5)),
+            RigRun(number=2, task=None, stopped_because=None, stop_kind=None, start_at=_rig(10.0),
+                   end_at=_rig(15.0)),
+            RigRun(number=3, task=None, stopped_because=None, stop_kind=None, start_at=_rig(16.0), end_at=None),
+            RigRun(number=4, task=None, stopped_because=None, stop_kind=None, start_at=_rig(20.0),
+                   end_at=_rig(25.0))]
+    measured = {1: (0.5, 9.5), 2: (10.0, None), 3: (16.0, 18.0)}
+    assert run_instants(runs, measured) == [(_rig(0.5), 0.5), (_rig(9.5), 9.5), (_rig(10.0), 10.0),
+                                            (_rig(16.0), 16.0)]
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/events/test_rigruns.py tests/events/test_rigtiming.py -q -p no:cacheprovider
```

Expected: `13 failed, 8 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/events/rigruns.py b/wl_preproc/events/rigruns.py
index ba46dc2..e98429d 100644
--- a/wl_preproc/events/rigruns.py
+++ b/wl_preproc/events/rigruns.py
@@ -29,6 +29,7 @@ from __future__ import annotations
 
 import dataclasses
 import json
+import math
 from pathlib import Path
 
 from wl_preproc.contracts.paths import XCON_DIRNAME
@@ -44,6 +45,12 @@ class RigRun:
     stopped_because: str | None
     stop_kind: str | None
     calibration: str | None = None  # the start row's display calibration id
+    # Its start and end rows' instants on the rig's anchored wall clock, POSIX
+    # seconds: what the training records' clock fit matches to the run's codes
+    # (design spec `2026-10-18-training-records-and-backup-clock-design.md`
+    # section 3.3). None where a row has none.
+    start_at: float | None = None
+    end_at: float | None = None
 
 
 @dataclasses.dataclass(frozen=True, slots=True)
@@ -66,6 +73,12 @@ def _text(value: object) -> str | None:
     return None if value is None else str(value)
 
 
+def _instant(value: object) -> float | None:
+    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
+        return None
+    return float(value)
+
+
 def read_rig_runs(session_dir: Path, subject: str) -> RigRuns | None:
     """This session's runs from `<session>/xcon/runs.jsonl`, or `None` when
     the session has no such record."""
@@ -106,14 +119,17 @@ def read_rig_runs(session_dir: Path, subject: str) -> RigRuns | None:
         if row["event"] == "start":
             run["task"] = _text(row.get("task"))
             run["calibration"] = _text(row.get("calibration"))
+            run["start_at"] = _instant(row.get("at"))
             if _integer(row.get("run_in_session")):
                 run["number"] = row["run_in_session"]
         else:
             run["stopped_because"] = _text(row.get("stopped_because"))
             run["stop_kind"] = _text(row.get("stop_kind"))
+            run["end_at"] = _instant(row.get("at"))
     runs = tuple(sorted((RigRun(number=run["number"], task=run.get("task"),
                                 stopped_because=run.get("stopped_because"), stop_kind=run.get("stop_kind"),
-                                calibration=run.get("calibration"))
+                                calibration=run.get("calibration"), start_at=run.get("start_at"),
+                                end_at=run.get("end_at"))
                          for run in fields.values()), key=lambda run: run.number))
     return RigRuns(runs=runs, problems=tuple(problems),
                    run_numbers={index: run["number"] for index, run in sorted(fields.items())
diff --git a/wl_preproc/events/rigtiming.py b/wl_preproc/events/rigtiming.py
new file mode 100644
index 0000000..45abd7a
--- /dev/null
+++ b/wl_preproc/events/rigtiming.py
@@ -0,0 +1,180 @@
+"""Placing wl-xcon's training records on the session's clock (design spec
+`2026-10-18-training-records-and-backup-clock-design.md` section 3.3, amendment 2).
+
+**Never guessed.** A row is timed by the code its act strobed, its
+`event_code`: the n-th row of a file carrying a number takes the time of the
+n-th occurrence of that number in the session's codes. A count that disagrees
+leaves that number's rows in that file untimed, and one problem names both
+counts. A code the recording lost reads so, as does one strobed with no row (a
+`wlx run` session's `HEAD_RELEASED`, a manual reward's pump fault: wl-xcon's
+corrections 2 and 3) and a task's own code with a framework code's number (its
+XC-382). A row that does not say which code it strobed is untimed.
+
+**The rig's clock fit.** wl-xcon stamps its run rows and its controls with its
+anchored wall clock. Matched to their codes -- each run's start row to its
+measured `RUN_START`, its end row to its `RUN_END` when it closed, and each
+control timed by its code to that code -- they fit the rig's clock as a rate and
+an offset, residuals kept. A control that strobed nothing (a note, a schedule,
+a pause its allocation has no code for) is timed through the fit, and is
+otherwise untimed. Fewer than two matches, or a residual past
+`RIG_CLOCK_LIMIT_S`, and there is no fit: a resumed session's process anchors
+the rig's clock again (wl-xcon's `welfare.SessionClock`), which one line cannot
+follow.
+
+Pure: no database and no file I/O, as `timebase/fit.py`."""
+
+from __future__ import annotations
+
+import collections
+import dataclasses
+from collections.abc import Iterable, Mapping, Sequence
+
+from wl_preproc.contracts.events import SimpleEvent
+from wl_preproc.events.rigrecords import CHANGES_NAME, CONTROLS_NAME, RigRecords
+from wl_preproc.events.rigruns import RigRun
+
+# The largest miss between a rig instant and its code that a fit may have
+# (spec section 10, item 10): ten times the jitter the synthetic rig record
+# states (`synth/peripherals.py::RIG_STAMP_JITTER_S`, 5 ms either side), under
+# which the largest miss measured was 6.8 ms over 2000 synthetic sessions of
+# 15 s to two hours. A step in the rig's clock of 2 s was refused in each of
+# 1000 sessions, one of 0.5 s in 998 and one of 0.1 s in 819: a step at a
+# session's last instants tilts the line instead, within the step.
+RIG_CLOCK_LIMIT_S = 0.05
+
+
+@dataclasses.dataclass(frozen=True, slots=True)
+class RigClock:
+    """The rig's anchored wall clock against session time:
+    `rig_s = rig_at_zero + session_s * (1 + drift_ppm / 1e6)`."""
+
+    rig_at_zero: float  # POSIX seconds on the rig's clock at session t = 0
+    drift_ppm: float
+    n_matched: int
+    residual_ms_rms: float
+    residual_ms_max: float
+
+    def session_time(self, at: float) -> float:
+        return (at - self.rig_at_zero) / (1.0 + self.drift_ppm / 1e6)
+
+
+@dataclasses.dataclass(frozen=True, slots=True)
+class Timing:
+    """Each change's session time, in the file's order; each control's,
+    with `code` or `rig_clock` for how it was found; the fit, if one was
+    made; and what could not be timed, and why."""
+
+    changes: tuple[float | None, ...]
+    controls: tuple[tuple[float | None, str | None], ...]
+    clock: RigClock | None
+    problems: tuple[str, ...]
+
+
+def code_times(decoded: Iterable) -> dict[int, list[float]]:
+    """Every simple event's time, by its code, in the stream's order. A
+    payload's words are not codes: `decode_stream` reads them by position."""
+    times: dict[int, list[float]] = collections.defaultdict(list)
+    for item in decoded:
+        if isinstance(item, SimpleEvent):
+            times[item.code].append(item.time_s)
+    return dict(times)
+
+
+def run_instants(runs: Iterable[RigRun],
+                 measured: Mapping[int, tuple[float, float | None]]) -> list[tuple[float, float]]:
+    """`(rig instant, session time)` for each measured run's start row, and
+    its end row when the run closed. `measured` maps a run's number to its
+    measured start and its `RUN_END`'s time, None for a run that faulted."""
+    pairs = []
+    for run in runs:
+        if run.number not in measured:
+            continue
+        start_s, end_s = measured[run.number]
+        if run.start_at is not None:
+            pairs.append((run.start_at, start_s))
+        if run.end_at is not None and end_s is not None:
+            pairs.append((run.end_at, end_s))
+    return pairs
+
+
+def fit_rig_clock(pairs: Sequence[tuple[float, float]]) -> RigClock | str:
+    """The rig's clock from `(rig instant, session time)` pairs, by centred
+    least squares as `timebase/fit.py` fits a device, residuals in the
+    session's frame; or why there is none, as a phrase."""
+    n = len(pairs)
+    if n == 0:
+        return "none of its instants matched a code"
+    if n < 2:
+        return f"only {n} of its instants matched a code, and a fit needs 2"
+    mean_rig = sum(rig for rig, _ in pairs) / n
+    mean_session = sum(session for _, session in pairs) / n
+    variance = sum((session - mean_session) ** 2 for _, session in pairs)
+    if variance <= 0.0:
+        return "its instants matched codes at a single time"
+    scale = sum((session - mean_session) * (rig - mean_rig) for rig, session in pairs) / variance
+    rig_at_zero = mean_rig - scale * mean_session
+    misses = [abs((rig - rig_at_zero) / scale - session) for rig, session in pairs]
+    if max(misses) > RIG_CLOCK_LIMIT_S:
+        return f"its instants miss their codes by up to {max(misses):.3f} s, past the {RIG_CLOCK_LIMIT_S} s limit"
+    return RigClock(rig_at_zero=rig_at_zero, drift_ppm=(scale - 1.0) * 1e6, n_matched=n,
+                    residual_ms_rms=(sum(miss * miss for miss in misses) / n) ** 0.5 * 1e3,
+                    residual_ms_max=max(misses) * 1e3)
+
+
+def _rows(n: int) -> str:
+    return "1 row" if n == 1 else f"{n} rows"
+
+
+def _join(rows: Sequence, codes: Mapping[int, list[float]], name: str) -> tuple[list[float | None], list[str]]:
+    """Each row's time by its code, and the counts that disagree."""
+    times: list[float | None] = [None] * len(rows)
+    carrying: dict[int, list[int]] = collections.defaultdict(list)
+    unsaid = 0
+    for position, row in enumerate(rows):
+        if not row.coded:
+            unsaid += 1
+        elif row.event_code is not None:
+            carrying[row.event_code].append(position)
+    problems = []
+    for code, positions in sorted(carrying.items()):
+        found = codes.get(code, [])
+        if len(found) != len(positions):
+            one = len(positions) == 1
+            problems.append(f"{name}: {_rows(len(positions))} carr{'ies' if one else 'y'} code {code} and the "
+                            f"recording holds {len(found)}, so {'it is not' if one else 'none of them is'} timed")
+            continue
+        for position, time in zip(positions, found, strict=True):
+            times[position] = time
+    if unsaid:
+        one = unsaid == 1
+        problems.append(f"{name}: {_rows(unsaid)} do{'es' if one else ''} not say which code {'it' if one else 'they'} "
+                        f"strobed, so {'it is' if one else 'they are'} not timed")
+    return times, problems
+
+
+def time_records(records: RigRecords, codes: Mapping[int, list[float]],
+                 instants: Sequence[tuple[float, float]]) -> Timing:
+    """Each record's session time. `codes` is `code_times` of the session's
+    decoded codes; `instants` is `run_instants` of its runs."""
+    change_times, change_problems = _join(records.changes, codes, CHANGES_NAME)
+    coded_times, control_problems = _join(records.controls, codes, CONTROLS_NAME)
+    pairs = [*instants, *((control.at, time) for control, time in zip(records.controls, coded_times, strict=True)
+                          if time is not None and control.at is not None)]
+    fitted = fit_rig_clock(pairs)
+    clock = fitted if isinstance(fitted, RigClock) else None
+    controls, unfitted = [], 0
+    for control, time in zip(records.controls, coded_times, strict=True):
+        strobed_nothing = control.coded and control.event_code is None and control.at is not None
+        if time is not None:
+            controls.append((time, "code"))
+        elif strobed_nothing and clock is not None:
+            controls.append((clock.session_time(control.at), "rig_clock"))
+        else:
+            unfitted += strobed_nothing
+            controls.append((None, None))
+    problems = [*change_problems, *control_problems]
+    if unfitted:
+        one = unfitted == 1
+        problems.append(f"{CONTROLS_NAME}: {_rows(unfitted)} strobed nothing, and the rig's clock could not be fitted "
+                        f"to the recording ({fitted}), so {'it is' if one else 'they are'} not timed")
+    return Timing(changes=tuple(change_times), controls=tuple(controls), clock=clock, problems=tuple(problems))
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/events/test_rigruns.py tests/events/test_rigtiming.py -q -p no:cacheprovider
```

Expected: `21 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/events -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `74 passed`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "fewer codes than rows joins the first",
  "wl_preproc/events/rigtiming.py",
  "        if len(found) != len(positions):",
  "        if len(found) < len(positions):",
  "tests/events/test_rigtiming.py"
 ],
 [
  "a limit below the stated jitter",
  "wl_preproc/events/rigtiming.py",
  "    if max(misses) > RIG_CLOCK_LIMIT_S:",
  "    if max(misses) > 0.001:",
  "tests/events/test_rigtiming.py"
 ],
 [
  "a row that does not say is fitted",
  "wl_preproc/events/rigtiming.py",
  "        elif strobed_nothing and clock is not None:",
  "        elif clock is not None and control.at is not None:",
  "tests/events/test_rigtiming.py"
 ],
 [
  "a faulted run's end row matched",
  "wl_preproc/events/rigtiming.py",
  "        if run.end_at is not None and end_s is not None:",
  "        if run.end_at is not None:",
  "tests/events/test_rigtiming.py"
 ],
 [
  "session time by the rate, not its inverse",
  "wl_preproc/events/rigtiming.py",
  "        return (at - self.rig_at_zero) / (1.0 + self.drift_ppm / 1e6)",
  "        return (at - self.rig_at_zero) * (1.0 + self.drift_ppm / 1e6)",
  "tests/events/test_rigtiming.py"
 ],
 [
  "an end row's instant dropped",
  "wl_preproc/events/rigruns.py",
  "            run[\"end_at\"] = _instant(row.get(\"at\"))",
  "            run[\"end_at\"] = None",
  "tests/events/test_rigruns.py"
 ]
]
```

Expected: 6 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(events): time the training records by their codes and the rig's clock -- the n-th row carrying a number takes that number's n-th code, and a count that disagrees leaves its rows untimed, naming both counts; a control that strobed nothing is timed through the rig's clock, fitted from its run rows' and coded controls' instants, with no fit past a 50 ms miss (section 3.3, amendments 2, 11 and 12)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 3: The synthetic rig's training records and its clock

A recipe's rig record can carry changes and controls (spec §3, §8): each code strobed mid-trial, where no other word is on the bus, and written in wl-xcon's keys (`record.py` at `7398d3e`). The rig's instants are its own clock, `RIG_DRIFT_PPM` fast and stamped within `RIG_STAMP_JITTER_S` of each code, the jitter §10 item 10 measures against; the run rows' instants are on it too (amendment 16). Every profile without a rig record's acts is unchanged but for the run rows' instants.

**Files:**
- Modify: `wl_preproc/synth/recipe.py` (`RigChangeSpec`, `RigControlSpec`; `RigRecordSpec`; a check in `_coherent`)
- Modify: `wl_preproc/synth/timeline.py`, `wl_preproc/synth/truth.py`
- Modify: `wl_preproc/synth/peripherals.py` (`rig_clock`, `write_rig_records`; the run rows' instants)
- Test: `tests/synth/test_peripherals.py`

**Interfaces:**
- Consumes: Task 1's `read_rig_records` (a test reads the files back).
- Produces:
  - `synth/recipe.py`: `RigChangeSpec(trial: int, name="reward_ml", was=0.2, now=0.25, event_code: int | None
    = 4130, by: dict)`; `RigControlSpec(trial: int, kind="pause", event_code: int | None = 4131, detail: dict,
    by: dict | None)`; `RigRecordSpec.changes` and `.controls`, both `()` by default;
  - `synth/truth.py::GroundTruth.change_times` and `.control_times` (session seconds, the recipe's order);
  - `synth/peripherals.py`: `RIG_CLOCK_LEAD_S = 12.5`, `RIG_DRIFT_PPM = 20.0`, `RIG_STAMP_JITTER_S = 0.005`;
    `rig_clock(recipe) -> Callable[[float], float]`; `write_rig_records(xcon_dir, recipe, truth, stamp)`,
    called by `write_rig_runs` for a recipe with a rig record.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/synth/test_peripherals.py b/tests/synth/test_peripherals.py
index c6fdf0f..d8b4fdd 100644
--- a/tests/synth/test_peripherals.py
+++ b/tests/synth/test_peripherals.py
@@ -228,3 +228,89 @@ def test_a_rig_record_needs_runs():
 
     with pytest.raises(ValueError, match="rig_record needs runs"):
         SessionRecipe.model_validate({**CI_RECIPE.model_dump(), "rig_record": RigRecordSpec().model_dump()})
+
+
+# -- The rig's training records (design spec
+# `2026-10-18-training-records-and-backup-clock-design.md` section 3).
+
+def _training(changes=(), controls=()):
+    from wl_preproc.synth.recipe import RigRecordSpec, SessionRecipe
+
+    rig = RigRecordSpec(changes=changes, controls=controls)
+    return SessionRecipe.model_validate({**CI_RECIPE.model_dump(), "runs": True, "rig_record": rig.model_dump()})
+
+
+def test_each_act_strobes_its_code_mid_trial_and_one_that_strobed_nothing_strobes_none():
+    from wl_preproc.synth.recipe import RigChangeSpec, RigControlSpec
+
+    recipe = _training(changes=(RigChangeSpec(trial=2),),
+                       controls=(RigControlSpec(trial=2), RigControlSpec(trial=3, kind="note", event_code=None)))
+    truth = build_timeline(recipe)
+    (change,), (pause, note) = truth.change_times, truth.control_times
+    assert (change, 4130) in truth.code_words and (pause, 4131) in truth.code_words
+    second, third = truth.trials[1], truth.trials[2]
+    assert second.start_s < change < pause < second.end_s
+    assert note == (third.start_s + third.end_s) / 2
+    assert len(truth.code_words) == len(build_timeline(_training()).code_words) + 2
+
+
+def test_the_training_records_are_written_in_wl_xcons_keys():
+    """`record.py`'s `parameter_change` and `control` (wl-xcon's `7398d3e`),
+    read back with nothing to report."""
+    import tempfile
+    from pathlib import Path
+
+    from wl_preproc.events.rigrecords import read_rig_records
+    from wl_preproc.synth.peripherals import write_rig_runs
+    from wl_preproc.synth.recipe import RigChangeSpec, RigControlSpec
+
+    recipe = _training(changes=(RigChangeSpec(trial=1), RigChangeSpec(trial=4, name="seed", was=1, now=[2, 3])),
+                       controls=(RigControlSpec(trial=4, kind="reward", event_code=4134, detail={"ml": 0.1}),))
+    with tempfile.TemporaryDirectory() as directory:
+        xcon = Path(directory) / "xcon"
+        xcon.mkdir()
+        write_rig_runs(xcon, recipe, build_timeline(recipe))
+        changes = [json.loads(line) for line in (xcon / "parameter_changes.jsonl").read_text().splitlines()]
+        (control,) = [json.loads(line) for line in (xcon / "controls.jsonl").read_text().splitlines()]
+        records = read_rig_records(Path(directory))
+    assert [set(row) for row in changes] == [{"sequence", "name", "was", "now", "by", "run", "event_code"}] * 2
+    assert [(row["sequence"], row["run"], row["now"]) for row in changes] == [(1, 0, 0.25), (2, 1, [2, 3])]
+    assert set(control) == {"kind", "by", "at", "at_local", "trial_index", "event_code", "run", "ml"}
+    assert (control["trial_index"], control["run"], control["event_code"]) == (3, 1, 4134)
+    assert (len(records.changes), len(records.controls), records.problems) == (2, 1, ())
+
+
+def test_the_rigs_instants_are_its_clock_within_the_stated_jitter(tmp_path):
+    """Its run rows and its controls, stamped on the rig's anchored wall
+    clock, each within `RIG_STAMP_JITTER_S` of its code."""
+    from wl_preproc.synth.peripherals import (RIG_CLOCK_LEAD_S, RIG_DRIFT_PPM, RIG_STAMP_JITTER_S,
+                                              write_rig_runs)
+    from wl_preproc.synth.recipe import SYNTH_EPOCH, RigControlSpec
+
+    recipe = _training(controls=(RigControlSpec(trial=2), RigControlSpec(trial=3, kind="resume", event_code=4132)))
+    truth = build_timeline(recipe)
+    (tmp_path / "xcon").mkdir()
+    write_rig_runs(tmp_path / "xcon", recipe, truth)
+    runs = [json.loads(line) for line in (tmp_path / "xcon" / "runs.jsonl").read_text().splitlines()]
+    controls = [json.loads(line) for line in (tmp_path / "xcon" / "controls.jsonl").read_text().splitlines()]
+    planted = [time for block in truth.blocks for time in (block.start_s, block.end_s)] + list(truth.control_times)
+    for row, time in zip(runs + controls, planted, strict=True):
+        ideal = SYNTH_EPOCH.timestamp() + RIG_CLOCK_LEAD_S + time * (1 + RIG_DRIFT_PPM / 1e6)
+        assert abs(row["at"] - ideal) <= RIG_STAMP_JITTER_S
+
+
+def test_a_rig_record_without_acts_writes_no_training_records(tmp_path):
+    from wl_preproc.synth.peripherals import write_rig_runs
+
+    recipe = _training()
+    (tmp_path / "xcon").mkdir()
+    write_rig_runs(tmp_path / "xcon", recipe, build_timeline(recipe))
+    assert not (tmp_path / "xcon" / "parameter_changes.jsonl").exists()
+    assert not (tmp_path / "xcon" / "controls.jsonl").exists()
+
+
+def test_an_act_past_the_sessions_trials_is_refused():
+    from wl_preproc.synth.recipe import RigControlSpec
+
+    with pytest.raises(ValueError, match=r"rig_record acts in trials \[5\], past the session's 4"):
+        _training(controls=(RigControlSpec(trial=5),))
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/synth/test_peripherals.py -q -p no:cacheprovider
```

Expected: `5 failed, 15 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/synth/peripherals.py b/wl_preproc/synth/peripherals.py
index 1fe3bef..40645dc 100644
--- a/wl_preproc/synth/peripherals.py
+++ b/wl_preproc/synth/peripherals.py
@@ -9,7 +9,8 @@ from __future__ import annotations
 
 import datetime
 import json
-from collections.abc import Sequence
+import random
+from collections.abc import Callable, Sequence
 from pathlib import Path
 
 import yaml
@@ -195,6 +196,27 @@ def write_rig_trials(path: Path, recipe: SessionRecipe, truth: GroundTruth) -> N
     path.write_text("\n".join(json.dumps(line, sort_keys=True) for line in lines) + "\n", encoding="utf-8")
 
 
+# The synthetic rig's anchored wall clock (wl-xcon's `taskd.Session.wall_now`,
+# POSIX seconds): it read `SYNTH_EPOCH` plus a lead at session t = 0, runs
+# `RIG_DRIFT_PPM` fast against the sync box, and stamps each instant up to
+# `RIG_STAMP_JITTER_S` either side of its code. The jitter is the one the
+# training records' clock fit is measured against (design spec
+# `2026-10-18-training-records-and-backup-clock-design.md` section 10, item 10;
+# `events/rigtiming.py::RIG_CLOCK_LIMIT_S`).
+RIG_CLOCK_LEAD_S = 12.5
+RIG_DRIFT_PPM = 20.0
+RIG_STAMP_JITTER_S = 0.005
+
+
+def rig_clock(recipe: SessionRecipe) -> Callable[[float], float]:
+    """The synthetic rig's instant for a session time, its jitter seeded by
+    the recipe, so each call stamps the next instant."""
+    generator = random.Random(recipe.seed)
+    zero = SYNTH_EPOCH.timestamp() + RIG_CLOCK_LEAD_S
+    return lambda session_s: (zero + session_s * (1 + RIG_DRIFT_PPM / 1e6)
+                              + generator.uniform(-RIG_STAMP_JITTER_S, RIG_STAMP_JITTER_S))
+
+
 def write_rig_runs(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth) -> None:
     """Stands in for wl-xcon's `xcon/runs.jsonl` and `xcon/config.json`, for a
     recipe whose blocks are wrapped in runs (design spec
@@ -206,19 +228,24 @@ def write_rig_runs(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth) ->
     writes the end row on every way out (`taskd.py`, read at `c4ee20d`): a run
     whose block is unclosed faulted, and its end row says so in `taskd.py`'s
     words for a fault; the others are its words for a run that finished its
-    blocks."""
+    blocks. Each row's `at` is the rig's clock (`rig_clock`).
+
+    *Until the training records' design, each row's `at` was its session
+    time, which wl-xcon never writes; nothing read it.*"""
     rig = recipe.rig_record
+    stamp = rig_clock(recipe)
     rows = []
     for run_index, block in enumerate(truth.blocks):
         spec = recipe.blocks[run_index]
-        rows.append({"event": "start", "run": run_index, "at": block.start_s, "task": spec.task_type.name.lower(),
+        rows.append({"event": "start", "run": run_index, "at": stamp(block.start_s),
+                     "task": spec.task_type.name.lower(),
                      **({} if rig is None else {"calibration": rig.calibration_id})})
         if run_index + 1 in recipe.unclosed_blocks:
-            rows.append({"event": "end", "run": run_index, "at": block.end_s,
+            rows.append({"event": "end", "run": run_index, "at": stamp(block.end_s),
                          "stopped_because": "fault, session aborted: RuntimeError: a synthetic fault",
                          "stop_kind": "fault"})
         else:
-            rows.append({"event": "end", "run": run_index, "at": block.end_s,
+            rows.append({"event": "end", "run": run_index, "at": stamp(block.end_s),
                          "stopped_because": "every block is finished", "stop_kind": "completed"})
     (xcon_dir / "runs.jsonl").write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
                                          encoding="utf-8")
@@ -228,9 +255,37 @@ def write_rig_runs(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth) ->
         config["calibration"] = None if rig.calibration_id is None else {
             "id": rig.calibration_id, "standard": False, "measured_on": "2027-03-01", "cones": RIG_CONES}
         write_rig_warnings(xcon_dir, recipe, truth)
+        write_rig_records(xcon_dir, recipe, truth, stamp)
     (xcon_dir / "config.json").write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
 
 
+def write_rig_records(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth,
+                      stamp: Callable[[float], float]) -> None:
+    """Stands in for wl-xcon's training records as it writes them today,
+    `xcon/parameter_changes.jsonl` and `xcon/controls.jsonl`, in the keys
+    `wl_xcon/record.py`'s `parameter_change` and `control` write (read at
+    wl-xcon's `7398d3e`), `event_code` included (design spec
+    `2026-10-18-training-records-and-backup-clock-design.md`, amendment 2). A
+    run wraps each block, so an act's run is its trial's block's, from 0. A
+    file is written only when it has a row, as wl-xcon appends its first."""
+    rig = recipe.rig_record
+    run_of = {position: trial.block_id - 1 for position, trial in enumerate(truth.trials, start=1)}
+    changes = [{"sequence": sequence, "name": change.name, "was": change.was, "now": change.now, "by": change.by,
+                "run": run_of[change.trial], "event_code": change.event_code}
+               for sequence, change in enumerate(rig.changes, start=1)]
+    controls = []
+    for control, time in zip(rig.controls, truth.control_times, strict=True):
+        at = stamp(time)
+        controls.append({"kind": control.kind, "by": control.by, "at": at,
+                         "at_local": datetime.datetime.fromtimestamp(at, datetime.timezone.utc).isoformat(),
+                         "trial_index": control.trial - 1, "event_code": control.event_code,
+                         "run": run_of[control.trial], **control.detail})
+    for name, rows in (("parameter_changes.jsonl", changes), ("controls.jsonl", controls)):
+        if rows:
+            (xcon_dir / name).write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
+                                         encoding="utf-8")
+
+
 # How a calibration's cone colours became light, in the shape wl-xcon's
 # `photometry.py::cone_record` writes it (read at `b2c2850`): its observer as
 # `cones.py::Observer.record` gives the CIE 2006 10-degree one, with floats,
diff --git a/wl_preproc/synth/recipe.py b/wl_preproc/synth/recipe.py
index d45e248..6f06edf 100644
--- a/wl_preproc/synth/recipe.py
+++ b/wl_preproc/synth/recipe.py
@@ -5,6 +5,7 @@ from __future__ import annotations
 
 import datetime
 from enum import Enum
+from typing import Any
 
 from pydantic import BaseModel, ConfigDict, Field, model_validator
 
@@ -177,17 +178,58 @@ class RigWarningSpec(BaseModel):
     by: dict | None = Field(default_factory=lambda: {"kind": "box", "name": "synth"})
 
 
+class RigChangeSpec(BaseModel):
+    """One live change the synthetic rig applied, as wl-xcon's
+    `record.py::SessionRecord.parameter_change` writes it, in trial `trial`
+    (from 1, across the session). Its code is strobed mid-trial unless
+    `event_code` is None. 4130 is `PARAM_CHANGED`'s number in wl-xcon's
+    reference allocation (its `tasks/allocation.py`): a synthetic choice,
+    since nothing in the pipeline reads a number's meaning (design spec
+    `2026-10-18-training-records-and-backup-clock-design.md`, amendment 2)."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    trial: int = Field(ge=1)
+    name: str = "reward_ml"
+    was: Any = 0.2
+    now: Any = 0.25
+    event_code: int | None = 4130
+    by: dict = Field(default_factory=lambda: {"kind": "box", "name": "synth"})
+
+
+class RigControlSpec(BaseModel):
+    """One console control on the synthetic rig, as wl-xcon's
+    `record.py::SessionRecord.control` writes it, in trial `trial` (from 1,
+    across the session): its kind, the code its act strobed (None for one
+    that strobed nothing, a note or a schedule; 4131 is `PAUSE`'s number in
+    the reference allocation), its own fields, and who sent it."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    trial: int = Field(ge=1)
+    kind: str = "pause"
+    event_code: int | None = 4131
+    detail: dict = Field(default_factory=dict)
+    by: dict | None = Field(default_factory=lambda: {"kind": "box", "name": "synth"})
+
+
 class RigRecordSpec(BaseModel):
     """What wl-xcon's engine build B adds to a session's record (design spec
     `2026-10-14-session-kind-and-warnings-design.md` section 1): the kind,
     the display calibration the session and each run ran against (`None`:
-    the rig's record would not load), and the warnings accepted."""
+    the rig's record would not load), and the warnings accepted; and its
+    training records, the changes it applied and the controls it was sent
+    (design spec `2026-10-18-training-records-and-backup-clock-design.md`
+    section 3). None of the latter by default, so every existing profile is
+    byte-identical in them."""
 
     model_config = ConfigDict(extra="forbid", frozen=True)
 
     session_kind: str = "training"
     calibration_id: str | None = "rig-synth-2027-03-01"
     warnings: tuple[RigWarningSpec, ...] = ()
+    changes: tuple[RigChangeSpec, ...] = ()
+    controls: tuple[RigControlSpec, ...] = ()
 
 
 class SessionRecipe(BaseModel):
@@ -357,6 +399,12 @@ class SessionRecipe(BaseModel):
             raise ValueError("syncbox is present at every session")
         if self.rig_record is not None and not self.runs:
             raise ValueError("rig_record needs runs: it is written with the run record")
+        if self.rig_record is not None:
+            n_trials = sum(block.n_trials for block in self.blocks)
+            late = sorted(act.trial for act in (*self.rig_record.changes, *self.rig_record.controls)
+                          if act.trial > n_trials)
+            if late:
+                raise ValueError(f"rig_record acts in trials {late}, past the session's {n_trials}")
         covered = sum(m.end_s - m.start_s for m in self.montages)
         if abs(covered - self.duration_s) > 1e-6:
             raise ValueError(
diff --git a/wl_preproc/synth/timeline.py b/wl_preproc/synth/timeline.py
index 85620a3..9ece5ce 100644
--- a/wl_preproc/synth/timeline.py
+++ b/wl_preproc/synth/timeline.py
@@ -111,6 +111,9 @@ def build_timeline(recipe: SessionRecipe) -> GroundTruth:
     _emit(words, 0.0, Marker.SESSION_START.value)
     cursor = 0.0
     trial_id = 1
+    rig = recipe.rig_record
+    acts = [] if rig is None else [*rig.changes, *rig.controls]
+    act_times = [0.0] * len(acts)
 
     for block_index, block in enumerate(recipe.blocks, start=1):
         block_start = cursor
@@ -152,6 +155,18 @@ def build_timeline(recipe: SessionRecipe) -> GroundTruth:
             _emit(words, trial_start, Marker.TRIAL_START.value)
             for word in encode_payload(Escape.TRIAL_NUMBER, _uint32_words(trial_id)):
                 _emit(words, trial_start, word)
+            # The rig's acts in this trial, each code strobed mid-trial, where no
+            # other word is on the bus (design spec
+            # `2026-10-18-training-records-and-backup-clock-design.md` section 3.3).
+            for position, act in enumerate(acts):
+                if act.trial != len(trials):
+                    continue
+                middle = trial_start + block.trial_duration_s / 2
+                if act.event_code is None:
+                    act_times[position] = middle
+                else:
+                    _emit(words, middle, act.event_code)
+                    act_times[position] = words[-1][0]
             # Outcome, then TRIAL_END -- both still inside the trial, and kept
             # CODE_WORD_SPACING_S apart from each other and from trial_end
             # itself (fix round 1, Task 8): TRIAL_END was missing entirely, so
@@ -209,4 +224,6 @@ def build_timeline(recipe: SessionRecipe) -> GroundTruth:
         units=units,
         spikes=spikes,
         stim_events=tuple(stim_events),
+        change_times=tuple(act_times[:len(rig.changes)]) if rig is not None else (),
+        control_times=tuple(act_times[len(rig.changes):]) if rig is not None else (),
     )
diff --git a/wl_preproc/synth/truth.py b/wl_preproc/synth/truth.py
index 8033b7d..ccc49fb 100644
--- a/wl_preproc/synth/truth.py
+++ b/wl_preproc/synth/truth.py
@@ -38,3 +38,7 @@ class GroundTruth:
     units: tuple[UnitTruth, ...]
     spikes: tuple[tuple[float, int], ...]        # (session-time seconds, UNIT id)
     stim_events: tuple[StimEvent, ...] = ()
+    # Each of the rig's training records' acts, in session time: its code's
+    # strobe, or mid-trial for one that strobed nothing; in the recipe's order.
+    change_times: tuple[float, ...] = ()
+    control_times: tuple[float, ...] = ()
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/synth/test_peripherals.py -q -p no:cacheprovider
```

Expected: `20 passed`

- [ ] **Step 5: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/events tests/synth -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `271 passed`

- [ ] **Step 6: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "an act's code not strobed",
  "wl_preproc/synth/timeline.py",
  "                    _emit(words, middle, act.event_code)\n                    act_times[position] = words[-1][0]",
  "                    act_times[position] = middle",
  "tests/synth/test_peripherals.py"
 ],
 [
  "a start row in session seconds",
  "wl_preproc/synth/peripherals.py",
  "rows.append({\"event\": \"start\", \"run\": run_index, \"at\": stamp(block.start_s),",
  "rows.append({\"event\": \"start\", \"run\": run_index, \"at\": block.start_s,",
  "tests/synth/test_peripherals.py"
 ],
 [
  "a jitter wider than stated",
  "wl_preproc/synth/peripherals.py",
  "generator.uniform(-RIG_STAMP_JITTER_S, RIG_STAMP_JITTER_S))",
  "generator.uniform(-RIG_STAMP_JITTER_S, 3 * RIG_STAMP_JITTER_S))",
  "tests/synth/test_peripherals.py"
 ],
 [
  "an act past the session accepted",
  "wl_preproc/synth/recipe.py",
  "if act.trial > n_trials)",
  "if act.trial > n_trials + 1)",
  "tests/synth/test_peripherals.py"
 ]
]
```

Expected: 4 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 7: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
test(synth): the synthetic rig's training records and its clock -- changes and controls with their codes strobed mid-trial, and the rig's instants on its own clock, 20 ppm fast and within 5 ms of each code, its run rows' among them (sections 3 and 10, amendment 16)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 4: Keep the records in the database

`core.ParameterChange`, `core.RigControl` and `core.RigClock` (spec §3.1, §3.3; amendments 9 and 12), written by the events stage in the transaction that writes ask 5's rig tables, for a session whose `config.json` is its own; their problems go to `core.RunRecordProblem`. The stage's activation binds `timebase`, whose `SessionClock` the stage records into since part 2 (amendment 15: `tests/schema/test_events.py` alone fails 20 of 30 without it). And two tests pin what already holds for the agent's SQLite file (§3.5).

**Files:**
- Modify: `wl_preproc/schema/core.py` (three tables, after `AcceptedWarning`; `RunRecordProblem`'s comment)
- Modify: `wl_preproc/schema/events.py` (`activate`; `_keep_training_records`, called by `populate_session`)
- Test: `tests/schema/test_training_records.py` (new); one test each added to `tests/ingest/test_watcher.py` and `tests/schema/test_eye_populate.py`

**Interfaces:**
- Consumes: Task 1's `read_rig_records`; Task 2's `code_times`, `run_instants`, `time_records`,
  `RigRun.start_at`/`end_at`; Task 3's `RigChangeSpec`, `RigControlSpec`, `GroundTruth.change_times`/
  `.control_times`; `tests/schema/test_events.py::_populate_generated`.
- Produces:
  - `core.ParameterChange` (key: the session, `change_line`): `sequence`, `name`, `was`, `now`, `event_code`,
    `run_index`, `run_number`, the five `by_*`, `session_time`, `row`;
  - `core.RigControl` (key: the session, `control_line`): `kind`, `at`, `at_local`, `trial_index`, `event_code`,
    `run_index`, `run_number`, `detail`, the five `by_*`, `session_time`, `time_source` enum(`code`,
    `rig_clock`), `row`;
  - `core.RigClock` (key: the session): `rig_at_zero`, `drift_ppm`, `n_matched`, `residual_ms_rms`,
    `residual_ms_max`.
  The JSON values and the line are `mediumtext`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/ingest/test_watcher.py b/tests/ingest/test_watcher.py
index b4a21b9..5609d29 100644
--- a/tests/ingest/test_watcher.py
+++ b/tests/ingest/test_watcher.py
@@ -1287,3 +1287,18 @@ def test_cli_ingest_still_prints_outcomes_found_before_a_root_fault(monkeypatch,
     assert exit_code == 1
     assert "2027-03-14_01" in captured.out
     assert "ingested" in captured.out
+
+
+def test_the_rig_agents_sqlite_file_in_xcon_changes_nothing(root):
+    """wl-xcon's agent rebuilds a SQLite file in `xcon/`; ingest lands the
+    session unread and unchanged by it (design spec
+    `2026-10-18-training-records-and-backup-clock-design.md` section 3.5):
+    `xcon/` is not one of `SYSTEMS`, so nothing verifies or lists it."""
+    tmp_path, prefix, session_dir = root
+    _use_dedicated_subject(tmp_path, "sqlkey")
+    (Path(session_dir) / "xcon" / "training.sqlite").write_bytes(b"SQLite format 3\x00" + bytes(84))
+
+    result = scan_once(tmp_path, prefix=prefix)
+
+    assert result.outcomes[session_dir] is Outcome.INGESTED
+    assert (ingest.Ingestion & {"session_dir": session_dir}).fetch1("integrity") == "verified"
diff --git a/tests/schema/test_eye_populate.py b/tests/schema/test_eye_populate.py
index e9d0605..30a9ed5 100644
--- a/tests/schema/test_eye_populate.py
+++ b/tests/schema/test_eye_populate.py
@@ -1849,6 +1849,22 @@ def test_the_xcon_log_is_found_in_its_own_directory(tmp_path):
     assert eye._find_xcon_log(tmp_path) == log
 
 
+def test_the_rig_agents_sqlite_file_is_not_the_calibration_map(tmp_path):
+    """wl-xcon's agent rebuilds a SQLite file in `xcon/`; the eye stage's
+    search takes only `*.bhv2` and `*.yaml` there, so it is never the map, and
+    the map beside it is still found (design spec
+    `2026-10-18-training-records-and-backup-clock-design.md` section 3.5)."""
+    from wl_preproc.contracts.paths import XCON_DIRNAME
+    from wl_preproc.schema import eye
+
+    (tmp_path / XCON_DIRNAME).mkdir()
+    (tmp_path / XCON_DIRNAME / "a-training.sqlite").write_bytes(b"SQLite format 3\x00")
+    assert eye._find_xcon_log(tmp_path) is None
+    log = tmp_path / XCON_DIRNAME / "session.bhv2"
+    _write_walkable_bhv2(log)
+    assert eye._find_xcon_log(tmp_path) == log
+
+
 def test_a_log_outside_the_xcon_directory_is_not_found(tmp_path):
     """THE point of settling the convention.
 
diff --git a/tests/schema/test_training_records.py b/tests/schema/test_training_records.py
new file mode 100644
index 0000000..265c3eb
--- /dev/null
+++ b/tests/schema/test_training_records.py
@@ -0,0 +1,178 @@
+"""wl-xcon's training records in the database: every row of
+`parameter_changes.jsonl` and `controls.jsonl` kept as written, each timed by
+the code it strobed or the rig's clock fit (design spec
+`2026-10-18-training-records-and-backup-clock-design.md` sections 3.1, 3.3 and
+8)."""
+
+from __future__ import annotations
+
+import datetime
+import json
+
+import pytest
+
+from wl_preproc.schema import events
+
+
+@pytest.fixture(scope="module")
+def events_activated(dj_conn, prefix):
+    events.activate(prefix=prefix)
+    return events
+
+
+def _land(tmp_path, subject, day, mutate=None, changes=(), controls=(), **update):
+    """A CI session with runs, its rig record carrying `changes` and
+    `controls`, through the events stage."""
+    from wl_preproc.synth.recipe import RigRecordSpec
+
+    from tests.schema.test_events import _populate_generated
+
+    rig = RigRecordSpec(changes=changes, controls=controls).model_dump()
+    return _populate_generated(tmp_path, subject, datetime.datetime(2027, 8, day, 9, 0), mutate=mutate, runs=True,
+                               rig_record=rig, **update)
+
+
+def _acts():
+    from wl_preproc.synth.recipe import RigChangeSpec, RigControlSpec
+
+    member = {"kind": "member", "name": "J. W.", "account": "acct-1", "issuer": "https://wl.works",
+              "token_id": "tok-1"}
+    changes = (RigChangeSpec(trial=1, by=member), RigChangeSpec(trial=3, name="seed", was=7, now=[8, 9]))
+    controls = (RigControlSpec(trial=2), RigControlSpec(trial=2, kind="resume", event_code=4132),
+                RigControlSpec(trial=3, kind="note", event_code=None, detail={"note": "lid droop"}),
+                RigControlSpec(trial=4, kind="reward", event_code=4134, detail={"ml": 0.1}))
+    return changes, controls
+
+
+def test_every_row_is_kept_as_written_and_timed_by_its_code(events_activated, dj_conn, tmp_path):
+    """Section 8, part 1: every row of both files stored as written, each
+    change and coded control at its code's session time, and a note, which
+    strobes nothing, through the rig's clock."""
+    from wl_preproc.schema import core
+    from wl_preproc.synth.timeline import build_timeline
+
+    changes, controls = _acts()
+    recipe, key = _land(tmp_path, "trrec1", 1, changes=changes, controls=controls)
+    truth = build_timeline(recipe)
+    xcon = tmp_path / recipe.session_id / "xcon"
+    change_rows = (core.ParameterChange & key).to_dicts(order_by="change_line")
+    assert [row["row"] for row in change_rows] == (xcon / "parameter_changes.jsonl").read_text().splitlines()
+    assert [(row["name"], json.loads(row["was"]), json.loads(row["now"]), row["sequence"], row["event_code"],
+             row["run_index"], row["run_number"]) for row in change_rows] == [
+        ("reward_ml", 0.2, 0.25, 1, 4130, 0, 1), ("seed", 7, [8, 9], 2, 4130, 0, 1)]
+    assert (change_rows[0]["by_kind"], change_rows[0]["by_account"], change_rows[0]["by_token_id"]) == (
+        "member", "acct-1", "tok-1")
+    assert [row["session_time"] for row in change_rows] == pytest.approx(list(truth.change_times), abs=1e-4)
+
+    control_rows = (core.RigControl & key).to_dicts(order_by="control_line")
+    assert [row["row"] for row in control_rows] == (xcon / "controls.jsonl").read_text().splitlines()
+    assert [(row["kind"], row["event_code"], row["time_source"], row["run_number"]) for row in control_rows] == [
+        ("pause", 4131, "code", 1), ("resume", 4132, "code", 1), ("note", None, "rig_clock", 1),
+        ("reward", 4134, "code", 2)]
+    assert [json.loads(row["detail"]) for row in control_rows] == [{}, {}, {"note": "lid droop"}, {"ml": 0.1}]
+    coded = [row["session_time"] for row in control_rows if row["time_source"] == "code"]
+    assert coded == pytest.approx([truth.control_times[0], truth.control_times[1], truth.control_times[3]],
+                                  abs=1e-4)
+    assert control_rows[2]["session_time"] == pytest.approx(truth.control_times[2], abs=0.01)
+    clock = (core.RigClock & key).fetch1()
+    assert clock["n_matched"] == 7 and clock["residual_ms_max"] <= 10.0  # 4 run instants, 3 coded controls
+    assert len(core.RunRecordProblem & key) == 0
+
+
+def test_a_value_mysqls_json_type_would_round_or_refuse_is_kept_as_written(events_activated, dj_conn, tmp_path):
+    """The plan's Review Focus 2 (amendment 9): MySQL's json type rounds a
+    double's seventeenth digit and refuses NaN, which wl-xcon's `json.dumps`
+    writes; kept as JSON text, both read back as written."""
+    from wl_preproc.schema import core
+    from wl_preproc.synth.recipe import RigChangeSpec
+
+    recipe, key = _land(tmp_path, "trrec8", 8, changes=(RigChangeSpec(trial=1, was=float("nan"),
+                                                                      now=0.30000000000000004),))
+    row = (core.ParameterChange & key).fetch1()
+    assert (row["was"], row["now"]) == ("NaN", "0.30000000000000004")
+    assert row["row"] == (tmp_path / recipe.session_id / "xcon" / "parameter_changes.jsonl").read_text().strip()
+
+
+def test_a_count_that_disagrees_leaves_its_rows_untimed_and_names_both_counts(events_activated, dj_conn,
+                                                                              tmp_path):
+    """A change row lost from the file: two codes, one row, so that number's
+    rows stay untimed; the controls are timed as before."""
+    from wl_preproc.schema import core
+
+    def lose_a_change(session_dir):
+        path = session_dir / "xcon" / "parameter_changes.jsonl"
+        path.write_text(path.read_text().splitlines()[0] + "\n")
+
+    changes, controls = _acts()
+    _recipe, key = _land(tmp_path, "trrec2", 2, mutate=lose_a_change, changes=changes, controls=controls)
+    assert (core.ParameterChange & key).to_arrays("session_time").tolist() == [None]
+    assert set((core.RigControl & key).to_arrays("time_source")) == {"code", "rig_clock"}
+    assert [row["problem"] for row in (core.RunRecordProblem & key).to_dicts(order_by="problem_number")] == [
+        "parameter_changes.jsonl: 1 row carries code 4130 and the recording holds 2, so it is not timed"]
+
+
+def test_an_unreadable_line_is_named_by_file_and_line(events_activated, dj_conn, tmp_path):
+    from wl_preproc.schema import core
+
+    def damage(session_dir):
+        path = session_dir / "xcon" / "controls.jsonl"
+        lines = path.read_text().splitlines()
+        path.write_text("\n".join([lines[0], "{not json", *lines[1:]]) + "\n")
+
+    changes, controls = _acts()
+    _recipe, key = _land(tmp_path, "trrec3", 3, mutate=damage, changes=changes, controls=controls)
+    assert (core.RigControl & key).to_arrays("control_line").tolist() == [1, 3, 4, 5]
+    assert [row["problem"] for row in (core.RunRecordProblem & key).to_dicts(order_by="problem_number")] == [
+        "controls.jsonl line 2: not JSON"]
+
+
+def test_another_animals_record_writes_no_training_records(events_activated, dj_conn, tmp_path):
+    """Ask 5's rule: a `config.json` naming another subject is not this
+    session's, and nothing of it is kept but its problem."""
+    from wl_preproc.schema import core
+
+    def another_animal(session_dir):
+        path = session_dir / "xcon" / "config.json"
+        path.write_text(json.dumps({**json.loads(path.read_text()), "subject": "other"}))
+
+    changes, controls = _acts()
+    _recipe, key = _land(tmp_path, "trrec4", 4, mutate=another_animal, changes=changes, controls=controls)
+    assert (len(core.ParameterChange & key), len(core.RigControl & key), len(core.RigClock & key)) == (0, 0, 0)
+
+
+def test_a_run_that_will_not_fit_is_a_problem_and_names_no_run(events_activated, dj_conn, tmp_path):
+    from wl_preproc.schema import core
+
+    def far_run(session_dir):
+        path = session_dir / "xcon" / "controls.jsonl"
+        rows = [json.loads(line) for line in path.read_text().splitlines()]
+        rows[0]["run"] = 70000
+        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
+
+    _changes, controls = _acts()
+    _recipe, key = _land(tmp_path, "trrec5", 5, mutate=far_run, controls=controls[:1])
+    row = (core.RigControl & key).fetch1()
+    assert (row["run_index"], row["run_number"]) == (None, None)
+    assert [row["problem"] for row in (core.RunRecordProblem & key).to_dicts(order_by="problem_number")] == [
+        "controls.jsonl line 1: run 70000 cannot be stored (0 to 65535), so the control names no run"]
+
+
+def test_a_session_read_again_keeps_one_of_each_record(events_activated, dj_conn, tmp_path):
+    from wl_preproc.schema import core
+
+    changes, controls = _acts()
+    recipe, key = _land(tmp_path, "trrec6", 6, changes=changes, controls=controls)
+    events.populate_session(key, tmp_path / recipe.session_id)
+    assert (len(core.ParameterChange & key), len(core.RigControl & key), len(core.RigClock & key)) == (2, 4, 1)
+
+
+def test_a_session_without_training_records_fits_the_rigs_clock_from_its_runs(events_activated, dj_conn,
+                                                                             tmp_path):
+    """Section 3.3: the fit is made on every session whose codes and rig
+    record allow it; two closed runs give four instants."""
+    from wl_preproc.schema import core
+
+    _recipe, key = _land(tmp_path, "trrec7", 7)
+    assert (len(core.ParameterChange & key), len(core.RigControl & key)) == (0, 0)
+    assert (core.RigClock & key).fetch1("n_matched") == 4
+    assert len(core.RunRecordProblem & key) == 0
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_events.py tests/schema/test_training_records.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `28 failed, 10 passed`

- [ ] **Step 3: Run the two tests that pin what already holds** for the agent's SQLite file (§3.5): they pass before any change, and Step 7's last two mutations show they can fail.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/ingest/test_watcher.py::test_the_rig_agents_sqlite_file_in_xcon_changes_nothing tests/schema/test_eye_populate.py::test_the_rig_agents_sqlite_file_is_not_the_calibration_map -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `2 passed`

- [ ] **Step 4: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/schema/core.py b/wl_preproc/schema/core.py
index 707ff96..8baef0b 100644
--- a/wl_preproc/schema/core.py
+++ b/wl_preproc/schema/core.py
@@ -82,8 +82,9 @@ class RunRecordProblem(dj.Manual):
     # record or of its accepted warnings it could not read, a config.json that
     # is not a JSON object or whose calibration is neither an object nor
     # null, a session kind it does not know, a warning's run that will not fit
-    # its column, a record of another animal, or no run record at all for a
-    # session with measured runs. At most 20, the last saying how many more.
+    # its column, a training record it could not read or time, a record of
+    # another animal, or no run record at all for a session with measured
+    # runs. At most 20, the last saying how many more.
     # Key: (subject, session_datetime, problem_number).
     -> pipeline.Session
     problem_number : smallint unsigned  # from 1, in the reader's order
@@ -157,6 +158,90 @@ class AcceptedWarning(dj.Manual):
     """
 
 
+@schema
+class ParameterChange(dj.Manual):
+    definition = """
+    # One live change the rig applied, from `xcon/parameter_changes.jsonl`, as
+    # wl-xcon wrote it, read by `schema/events.py::populate_session` (design
+    # spec 2026-10-18-training-records-and-backup-clock-design.md section 3.1).
+    # Timed by the code its act strobed (section 3.3), and otherwise untimed.
+    # Its values and its line are JSON text as written: MySQL's json type
+    # rounds a double's seventeenth digit and refuses NaN (amendment 9). Who
+    # made it is kept here, never in the NWB file. Key: (subject,
+    # session_datetime, change_line).
+    -> pipeline.Session
+    change_line : int unsigned  # its line in the file, from 1
+    ---
+    sequence = null     : int unsigned       # wl-xcon's change number; null when the row has none
+    name                : varchar(255)       # the setting
+    was = null          : mediumtext         # its old value, as JSON text; null when the row has none
+    now = null          : mediumtext         # its new value, likewise
+    event_code = null   : smallint unsigned  # the code its act strobed; null for none, or when the row does not say
+    run_index = null    : smallint unsigned  # wl-xcon's run, from 0; null for none, or when it will not fit
+    run_number = null   : smallint           # the measured run's; null when the run was not measured
+    by_kind = null      : varchar(16)        # box or member
+    by_name = null      : varchar(255)
+    by_account = null   : varchar(255)
+    by_issuer = null    : varchar(255)
+    by_token_id = null  : varchar(255)
+    session_time = null : double             # (s) session time; null when untimed
+    row                 : mediumtext         # the line as written
+    """
+
+
+@schema
+class RigControl(dj.Manual):
+    definition = """
+    # One console control, from `xcon/controls.jsonl`, as wl-xcon wrote it,
+    # read by `schema/events.py::populate_session` (design spec
+    # 2026-10-18-training-records-and-backup-clock-design.md section 3.1).
+    # Timed by the code its act strobed, or, for one that strobed nothing,
+    # through the rig's clock fit (`RigClock`); otherwise untimed. Its detail
+    # and its line are JSON text as written. Key: (subject, session_datetime,
+    # control_line).
+    -> pipeline.Session
+    control_line : int unsigned  # its line in the file, from 1
+    ---
+    kind                : varchar(64)        # as written: pause, resume, reward and the others amendment 1 lists
+    at = null           : double             # the rig's anchored wall clock, POSIX seconds
+    at_local = null     : varchar(64)
+    trial_index = null  : int unsigned       # the trial it happened in or, between trials, the one about to run
+    event_code = null   : smallint unsigned  # the code its act strobed; null for none, or when the row does not say
+    run_index = null    : smallint unsigned  # wl-xcon's run, from 0; null for none, or when it will not fit
+    run_number = null   : smallint           # the measured run's; null when the run was not measured
+    detail              : mediumtext         # the row's own fields, as JSON text
+    by_kind = null      : varchar(16)        # box or member; null for nobody
+    by_name = null      : varchar(255)
+    by_account = null   : varchar(255)
+    by_issuer = null    : varchar(255)
+    by_token_id = null  : varchar(255)
+    session_time = null : double             # (s) session time; null when untimed
+    time_source = null  : enum('code', 'rig_clock')  # its own code, or the rig's clock fit
+    row                 : mediumtext         # the line as written
+    """
+
+
+@schema
+class RigClock(dj.Manual):
+    definition = """
+    # The rig's anchored wall clock fitted to session time: its run rows' and
+    # its coded controls' instants against their codes (design spec
+    # 2026-10-18-training-records-and-backup-clock-design.md section 3.3),
+    # rig_s = rig_at_zero + session_s * (1 + drift_ppm / 1e6). Written by the
+    # event stage with the records it times. None when fewer than two instants
+    # matched, or one missed its code by more than 50 ms
+    # (`events/rigtiming.py::RIG_CLOCK_LIMIT_S`). Key: (subject,
+    # session_datetime).
+    -> pipeline.Session
+    ---
+    rig_at_zero     : double        # (s) POSIX seconds on the rig's clock at session t = 0
+    drift_ppm       : double
+    n_matched       : int unsigned  # instants matched to a code
+    residual_ms_rms : double
+    residual_ms_max : double
+    """
+
+
 @schema
 class RunAssertion(dj.Manual):
     definition = """
diff --git a/wl_preproc/schema/events.py b/wl_preproc/schema/events.py
index 79c935d..42edf17 100644
--- a/wl_preproc/schema/events.py
+++ b/wl_preproc/schema/events.py
@@ -52,8 +52,10 @@ from wl_preproc.contracts.events import (
     decode_stream,
 )
 from wl_preproc.events.assemble import AssembledBlock, AssembledTrial, assemble
+from wl_preproc.events.rigrecords import read_rig_records
 from wl_preproc.events.rigruns import read_rig_runs
 from wl_preproc.events.rigsession import read_rig_session
+from wl_preproc.events.rigtiming import code_times, run_instants, time_records
 from wl_preproc.events.rigtrials import read_rig_trials
 from wl_preproc.schema import DEFAULT_PREFIX, core, pipeline
 from wl_preproc.timebase.clock import clock_for
@@ -63,10 +65,18 @@ def activate(prefix: str = DEFAULT_PREFIX) -> None:
     """Ensure the tables this module fills are bound. Idempotent.
 
     No `schema.activate` of its own -- there is no schema to activate, because
-    this module declares no table.
+    this module declares no table. `timebase` is among them: the stage reads
+    the session's codes through its clock, which records `timebase.SessionClock`
+    on first ask (design spec
+    `2026-10-18-training-records-and-backup-clock-design.md` amendment 3).
+    Until part 1 of that design this bound `pipeline` and `core` only, and a
+    process that activated this module alone failed at the clock.
     """
+    from wl_preproc.schema import timebase
+
     pipeline.activate(prefix=prefix)
     core.activate(prefix=prefix)
+    timebase.activate(prefix=prefix)
 
 
 def sync_event_types() -> None:
@@ -323,6 +333,61 @@ def _block_type_rows(session_key: dict, block_trial_rows: list[dict], record) ->
     ]
 
 
+def _actor_columns(record) -> dict:
+    return {"by_kind": _clip(record.by_kind, 16), "by_name": _clip(record.by_name, 255),
+            "by_account": _clip(record.by_account, 255), "by_issuer": _clip(record.by_issuer, 255),
+            "by_token_id": _clip(record.by_token_id, 255)}
+
+
+def _keep_training_records(session_key: dict, session_dir: Path, decoded, rig_runs, run_rows: list[dict],
+                           measured: set[int]) -> list[str]:
+    """`core.ParameterChange`, `core.RigControl` and `core.RigClock`: wl-xcon's
+    training records, each timed by its code or the rig's clock fit (design
+    spec `2026-10-18-training-records-and-backup-clock-design.md` sections
+    3.1 and 3.3). Returns what could not be read or timed, for the problem
+    rows."""
+    records = read_rig_records(session_dir)
+    first_runs: dict[int, tuple[float, float | None]] = {}
+    for row in run_rows:
+        first_runs.setdefault(row["run_number"],
+                              (row["run_start_time"], row["run_stop_time"] if row["closed"] else None))
+    timing = time_records(records, code_times(decoded),
+                          run_instants(rig_runs.runs if rig_runs is not None else (), first_runs))
+    numbers = rig_runs.run_numbers if rig_runs is not None else {}
+    problems = [*records.problems]
+
+    def run_columns(record, name: str, what: str) -> dict:
+        if record.run_index is not None and _run_index(record.run_index) is None:
+            problems.append(f"{name} line {record.line}: run {record.run_index} cannot be stored (0 to 65535), so "
+                            f"the {what} names no run")
+        number = numbers.get(record.run_index) if record.run_index is not None else None
+        return {"run_index": _run_index(record.run_index), "run_number": number if number in measured else None}
+
+    change_rows = [{
+        **session_key, "change_line": change.line, "sequence": change.sequence, "name": _clip(change.name, 255),
+        "was": change.was, "now": change.now, "event_code": change.event_code,
+        **run_columns(change, "parameter_changes.jsonl", "change"), **_actor_columns(change),
+        "session_time": time, "row": change.row,
+    } for change, time in zip(records.changes, timing.changes, strict=True)]
+    control_rows = [{
+        **session_key, "control_line": control.line, "kind": _clip(control.kind, 64), "at": control.at,
+        "at_local": _clip(control.at_local, 64), "trial_index": control.trial_index,
+        "event_code": control.event_code, **run_columns(control, "controls.jsonl", "control"),
+        "detail": control.detail, **_actor_columns(control), "session_time": time, "time_source": source,
+        "row": control.row,
+    } for control, (time, source) in zip(records.controls, timing.controls, strict=True)]
+    if change_rows:
+        core.ParameterChange.insert(change_rows, skip_duplicates=True)
+    if control_rows:
+        core.RigControl.insert(control_rows, skip_duplicates=True)
+    if timing.clock is not None:
+        clock = timing.clock
+        core.RigClock.insert1({**session_key, "rig_at_zero": clock.rig_at_zero, "drift_ppm": clock.drift_ppm,
+                               "n_matched": clock.n_matched, "residual_ms_rms": clock.residual_ms_rms,
+                               "residual_ms_max": clock.residual_ms_max}, skip_duplicates=True)
+    return [*problems, *timing.problems]
+
+
 def populate_session(key: dict, session_dir: Path) -> None:
     """Populate one session's `BehaviorRecording`, `EventType`, `Event`,
     `Trial`, `TrialType`, `Block` and `BlockTrial` from the sync box's decoded
@@ -582,7 +647,8 @@ def populate_session(key: dict, session_dir: Path) -> None:
     # what wl-xcon's record says the session was, its kind, its calibrations
     # and the warnings it accepted (design spec
     # `2026-10-14-session-kind-and-warnings-design.md` sections 2 and 3).
-    # Carried, never acted on.
+    # Carried, never acted on. Then its training records, each timed by the
+    # code it strobed or the rig's clock (`_keep_training_records`).
     calibration_rows = [
         {**session_key, "run_number": run.number, "calibration_id": _clip(run.calibration, 255)}
         for run in (rig_runs.runs if rig_runs is not None else ())
@@ -622,6 +688,7 @@ def populate_session(key: dict, session_dir: Path) -> None:
             })
         if warning_rows:
             core.AcceptedWarning.insert(warning_rows, skip_duplicates=True)
+        unfitting += _keep_training_records(session_key, session_dir, decoded, rig_runs, run_rows, measured)
 
     if rig_runs is not None:
         problems = list(rig_runs.problems)
PATCH
```

- [ ] **Step 5: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/schema/test_events.py tests/schema/test_training_records.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `38 passed`

- [ ] **Step 6: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/events tests/ingest/test_watcher.py tests/schema/test_daemon.py tests/schema/test_events.py tests/schema/test_guardrails.py tests/schema/test_nwb_rig_record.py tests/schema/test_session_listing.py tests/schema/test_training_records.py tests/synth -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `395 passed`

- [ ] **Step 7: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "the stage's activation without timebase",
  "wl_preproc/schema/events.py",
  "    timebase.activate(prefix=prefix)\n",
  "",
  "tests/schema/test_training_records.py"
 ],
 [
  "the time's source not kept",
  "wl_preproc/schema/events.py",
  "\"time_source\": source,",
  "\"time_source\": None,",
  "tests/schema/test_training_records.py"
 ],
 [
  "the records' problems dropped",
  "wl_preproc/schema/events.py",
  "        unfitting += _keep_training_records(",
  "        _keep_training_records(",
  "tests/schema/test_training_records.py"
 ],
 [
  "the eye stage's search takes any file",
  "wl_preproc/schema/eye.py",
  "    matches = sorted((*directory.glob(\"*.bhv2\"), *directory.glob(\"*.yaml\")))",
  "    matches = sorted(directory.glob(\"*\"))",
  "tests/schema/test_eye_populate.py::test_the_rig_agents_sqlite_file_is_not_the_calibration_map"
 ],
 [
  "xcon read as a system",
  "wl_preproc/ingest/discover.py",
  "    for system in SYSTEMS:",
  "    for system in (*SYSTEMS, \"xcon\"):",
  "tests/ingest/test_watcher.py::test_the_rig_agents_sqlite_file_in_xcon_changes_nothing"
 ]
]
```

Expected: 5 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 8: Commit.**

```bash
git add -A tests wl_preproc
git commit -F - <<'MSG'
feat(schema): keep the training records -- core.ParameterChange, core.RigControl and core.RigClock, written by the events stage for a session whose config.json is its own, their problems in core.RunRecordProblem; the stage's activation binds timebase, which the session's clock records into, and a SQLite file in xcon/ changes nothing (sections 3.1 and 3.5, amendments 9 and 15)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 5: The records in the NWB file and its description

`processing/rig_record` gains `parameter_changes` and `controls` (spec §3.2): the file's own runs' rows and those outside any run (amendment 10), at their session times, who acted by kind only (decision 6). The description gains `training_records` within version 3 (amendment 14), named in the protocol, and its exported schema is regenerated.

**Files:**
- Modify: `wl_preproc/nwb/gather.py` (`_rig_record`), `wl_preproc/nwb/rig_record.py`, `wl_preproc/nwb/describe.py`
- Modify: `wl_preproc/contracts/nwb_description.py` (`TrainingRecords`), `docs/ops/lab-host-protocol.md` (`GET /nwb`), `docs/schemas/nwb_description.json` (exported)
- Test: `tests/nwb/test_writers.py` (its two hand-built records gain the two keys), `tests/nwb/test_describe.py`, `tests/schema/test_nwb_rig_record.py` (its session gains acts), `tests/responder/test_protocol_document.py`

**Interfaces:**
- Consumes: Task 4's `core.ParameterChange` and `core.RigControl`.
- Produces: `gather._rig_record`'s dict gains `parameter_changes` (each `session_time`, `run_number`, `name`,
  `was`, `now`, `sequence`, `by_kind`) and `controls` (each `session_time`, `time_source`, `at`, `kind`,
  `trial_index`, `detail`, `by_kind`); `contracts/nwb_description.py::TrainingRecords(parameter_changes: int,
  controls: int)` and `NwbDescription.training_records: TrainingRecords | None = None`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/nwb/test_describe.py b/tests/nwb/test_describe.py
index 235ed61..6f81f82 100644
--- a/tests/nwb/test_describe.py
+++ b/tests/nwb/test_describe.py
@@ -165,9 +165,22 @@ def test_the_description_says_what_wl_xcons_record_says_the_session_was():
     assert out["schema_version"] == 3
 
 
+def test_the_description_counts_the_training_records_the_file_holds():
+    """Design spec `2026-10-18-training-records-and-backup-clock-design.md`
+    section 3.2: the counts, never who acted."""
+    from tests.nwb.test_writers import RIG_RECORD
+    from wl_preproc.nwb.describe import describe
+
+    record = {**RIG_RECORD, "parameter_changes": [{"by_kind": "member"}] * 2, "controls": [{"by_kind": "box"}] * 3}
+    out = describe(_gathered(rig_record=record), status="written", n_critical=0, checksums=[CHECKSUM],
+                   built_at=BUILT_AT)
+    assert out["training_records"] == {"parameter_changes": 2, "controls": 3}
+
+
 def test_a_file_of_a_session_wl_xcon_did_not_run_describes_no_kind():
     from wl_preproc.nwb.describe import describe
 
     out = describe(_gathered(), status="written", n_critical=0, checksums=[CHECKSUM], built_at=BUILT_AT)
-    assert (out["session_kind"], out["calibration"], out["accepted_warnings"]) == (None, None, [])
+    assert (out["session_kind"], out["calibration"], out["accepted_warnings"], out["training_records"]) == (
+        None, None, [], None)
     assert [run["calibration"] for run in out["runs"]] == [None]
diff --git a/tests/nwb/test_writers.py b/tests/nwb/test_writers.py
index a8b3a92..e321687 100644
--- a/tests/nwb/test_writers.py
+++ b/tests/nwb/test_writers.py
@@ -323,6 +323,8 @@ RIG_RECORD = {
         {"code": "luminance-step", "detail": "the luminance-step sentence", "accepted_in": ["training"],
          "how": "start", "run_number": 2, "at_local": "2026-10-09T09:05:00+02:00"},
     ],
+    "parameter_changes": [],
+    "controls": [],
 }
 
 
@@ -356,13 +358,44 @@ def test_the_rig_record_holds_the_session_and_the_warnings_it_accepted(tmp_path)
         assert warnings["detail"].tolist() == ["the default-calibration sentence", "the luminance-step sentence"]
 
 
+def test_the_rig_record_holds_its_training_records_with_who_acted_by_kind_only(tmp_path):
+    """Design spec `2026-10-18-training-records-and-backup-clock-design.md`
+    section 3.2: each change and control at its session time (NaN where
+    untimed), its values and detail as JSON text, and the kind of actor."""
+    import math
+
+    from wl_preproc.nwb.rig_record import add_rig_record
+
+    record = {**RIG_RECORD, "parameter_changes": [
+        {"session_time": 2.5, "run_number": 1, "name": "reward_ml", "was": "0.2", "now": "0.25", "sequence": 1,
+         "by_kind": "member"},
+        {"session_time": None, "run_number": None, "name": "seed", "was": None, "now": "[8, 9]", "sequence": None,
+         "by_kind": None}],
+        "controls": [
+        {"session_time": 4.0, "time_source": "code", "at": 1773478804.0, "kind": "pause", "trial_index": 1,
+         "detail": "{}", "by_kind": "box"},
+        {"session_time": None, "time_source": None, "at": None, "kind": "note", "trial_index": None,
+         "detail": '{"note": "lid droop"}', "by_kind": None}]}
+    _path, io, nwb = _write(tmp_path, lambda nwb: add_rig_record(nwb, record))
+    with io:
+        module = nwb.processing["rig_record"]
+        changes = module["parameter_changes"].to_dataframe()
+        assert changes[["run_number", "setting", "was", "now", "sequence", "by_kind"]].values.tolist() == [
+            [1, "reward_ml", "0.2", "0.25", 1, "member"], [-1, "seed", "", "[8, 9]", -1, ""]]
+        assert changes["session_time"].iloc[0] == 2.5 and math.isnan(changes["session_time"].iloc[1])
+        controls = module["controls"].to_dataframe()
+        assert controls[["time_source", "kind", "trial_index", "detail", "by_kind"]].values.tolist() == [
+            ["code", "pause", 1, "{}", "box"], ["", "note", -1, '{"note": "lid droop"}', ""]]
+        assert controls["rig_at"].iloc[0] == 1773478804.0 and math.isnan(controls["rig_at"].iloc[1])
+
+
 def test_a_kind_and_calibration_not_recorded_are_empty_and_no_warnings_mean_no_table(tmp_path):
     """A `config.json` written before wl-xcon's engine build B: the session
     row says nothing was recorded, and a session that accepted no warning
     has no warnings table."""
     from wl_preproc.nwb.rig_record import add_rig_record
 
-    record = {"session_kind": None, "calibration": None, "warnings": []}
+    record = {"session_kind": None, "calibration": None, "warnings": [], "parameter_changes": [], "controls": []}
     _path, io, nwb = _write(tmp_path, lambda nwb: add_rig_record(nwb, record))
     with io:
         module = nwb.processing["rig_record"]
@@ -370,3 +403,4 @@ def test_a_kind_and_calibration_not_recorded_are_empty_and_no_warnings_mean_no_t
         assert (session["session_kind"], session["calibration_id"], session["calibration_standard"],
                 session["calibration_cones"]) == ("", "", -1, "")
         assert "accepted_warnings" not in module.data_interfaces
+        assert "parameter_changes" not in module.data_interfaces and "controls" not in module.data_interfaces
diff --git a/tests/responder/test_protocol_document.py b/tests/responder/test_protocol_document.py
index 411cd72..6d62af5 100644
--- a/tests/responder/test_protocol_document.py
+++ b/tests/responder/test_protocol_document.py
@@ -29,11 +29,11 @@ def test_every_field_a_listed_session_carries_is_named_in_the_protocol():
 
 
 def test_the_descriptions_record_of_the_session_is_named_in_the_protocol():
-    from wl_preproc.contracts.nwb_description import AcceptedWarning, RigCalibration
+    from wl_preproc.contracts.nwb_description import AcceptedWarning, RigCalibration, TrainingRecords
 
     section = _section("`GET /nwb`")
-    names = ["session_kind", "calibration", "accepted_warnings",
-             *AcceptedWarning.model_fields, *RigCalibration.model_fields]
+    names = ["session_kind", "calibration", "accepted_warnings", "training_records",
+             *AcceptedWarning.model_fields, *RigCalibration.model_fields, *TrainingRecords.model_fields]
     assert [name for name in names if f"`{name}`" not in section] == []
 
 
diff --git a/tests/schema/test_nwb_rig_record.py b/tests/schema/test_nwb_rig_record.py
index 7b430e9..2e4c771 100644
--- a/tests/schema/test_nwb_rig_record.py
+++ b/tests/schema/test_nwb_rig_record.py
@@ -27,12 +27,17 @@ def daemon_module(dj_conn, prefix):
 def recorded(daemon_module, prefix, tmp_path_factory):
     """A training session wl-xcon ran, two runs, one warning accepted at the
     open by a name typed at the rig and one at run 2's start by a wl.works
-    member; a canonical over both runs and a derivative over run 1."""
+    member; its training records, a change and two controls in run 1 and a
+    change and a manual reward by the member in run 2; a canonical over both
+    runs and a derivative over run 1."""
     recipe, key = _restart_session(
         tmp_path_factory, None, runs=True, subject="rknwb1", session_id="2025-06-29_01",
         rig_record={"session_kind": "training", "warnings": [
             {"code": "default-calibration", "by": {"kind": "box", "name": "Typed Name"}},
-            {"code": "luminance-step", "run": 1, "how": "start", "by": _MEMBER}]})
+            {"code": "luminance-step", "run": 1, "how": "start", "by": _MEMBER}],
+            "changes": [{"trial": 1}, {"trial": 4, "name": "seed", "was": 1, "now": [2, 3], "by": _MEMBER}],
+            "controls": [{"trial": 2}, {"trial": 2, "kind": "note", "event_code": None, "detail": {"note": "ok"}},
+                         {"trial": 4, "kind": "reward", "event_code": 4134, "detail": {"ml": 0.1}, "by": _MEMBER}]})
     daemon_module.run_once(prefix=prefix)
     canonical = _canonical(daemon_module, prefix, key, "rknwb1-k1", [])
     derivative = _canonical(daemon_module, prefix, key, "rknwb1-k2", [], run_numbers=[1])
@@ -76,7 +81,10 @@ def test_the_file_holds_the_kind_the_calibrations_and_the_warnings(recorded, bui
 
 def test_the_file_names_no_one_who_accepted_a_warning(built):
     """Decision 3 of the spec's section 0: the database keeps who, and the
-    listing gives wl.works the name and account; the file names no one."""
+    listing gives wl.works the name and account; the file names no one. Nor
+    anyone who made a change or sent a control (design spec
+    `2026-10-18-training-records-and-backup-clock-design.md` decision 6): the
+    member made run 2's change and gave its manual reward."""
     canonical, _derivative = built
     raw = canonical.path.read_bytes()
     for text in ("Typed Name", "Jay Dubya", "acct-rk1", "tok-rk1", "https://wl.works"):
@@ -105,3 +113,33 @@ def test_a_file_of_some_runs_holds_the_warnings_of_the_open_and_of_its_own_runs(
         warnings = handle.read().processing["rig_record"]["accepted_warnings"].to_dataframe()
         assert warnings["code"].tolist() == ["default-calibration"]
     assert derivative.description["accepted_warnings"] == [{"code": "default-calibration", "run_number": None}]
+
+
+def test_the_file_holds_the_training_records_of_its_runs_at_their_session_times(recorded, built):
+    """Design spec `2026-10-18-training-records-and-backup-clock-design.md`
+    section 3.2: the canonical holds every change and control, timed as the
+    database holds them; the derivative over run 1 holds run 1's only
+    (amendment 10)."""
+    from pynwb import NWBHDF5IO
+
+    from wl_preproc.schema import core
+
+    _recipe, canonical_key, _derivative_key = recorded
+    canonical, derivative = built
+    session_key = {name: canonical_key[name] for name in ("subject", "session_datetime")}
+    stored = (core.RigControl & session_key).to_arrays("session_time", order_by="control_line")
+    with NWBHDF5IO(str(canonical.path), "r") as handle:
+        module = handle.read().processing["rig_record"]
+        changes = module["parameter_changes"].to_dataframe()
+        controls = module["controls"].to_dataframe()
+        assert changes[["run_number", "setting", "now", "by_kind"]].values.tolist() == [
+            [1, "reward_ml", "0.25", "box"], [2, "seed", "[2, 3]", "member"]]
+        assert controls[["kind", "time_source", "by_kind"]].values.tolist() == [
+            ["pause", "code", "box"], ["note", "rig_clock", "box"], ["reward", "code", "member"]]
+        assert controls["session_time"].tolist() == pytest.approx(stored.tolist())
+    with NWBHDF5IO(str(derivative.path), "r") as handle:
+        module = handle.read().processing["rig_record"]
+        assert module["parameter_changes"].to_dataframe()["setting"].tolist() == ["reward_ml"]
+        assert module["controls"].to_dataframe()["kind"].tolist() == ["pause", "note"]
+    assert canonical.description["training_records"] == {"parameter_changes": 2, "controls": 3}
+    assert derivative.description["training_records"] == {"parameter_changes": 1, "controls": 2}
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/nwb/test_describe.py tests/nwb/test_writers.py tests/responder/test_protocol_document.py tests/schema/test_nwb_rig_record.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `5 failed, 36 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/docs/ops/lab-host-protocol.md b/docs/ops/lab-host-protocol.md
index 092c5b6..5f26bea 100644
--- a/docs/ops/lab-host-protocol.md
+++ b/docs/ops/lab-host-protocol.md
@@ -520,9 +520,13 @@ Authorization: Bearer <token>
     `2026-10-14-session-kind-and-warnings-design.md` §5): what wl-xcon's record says the session
     was, `session_kind`, `calibration` (`id`, `standard`, `measured_on`) and
     `accepted_warnings` (each `code` and `run_number`, null at the open), and each run's
-    `calibration`; null and empty for a session wl-xcon did not run. The exported schema refuses
-    fields it does not know, so a copy validated against must take these. Read
-    `schema_version`, and ignore fields you do not know.
+    `calibration`; null and empty for a session wl-xcon did not run. **Within version 3, since
+    2026-10-18** (design spec `2026-10-18-training-records-and-backup-clock-design.md` §3.2):
+    `training_records`, how many of wl-xcon's training records the file holds, its
+    `parameter_changes` and its `controls` (the rows themselves are in the file's `rig_record`
+    module, which names no one who acted); null for a session wl-xcon did not run, and in a file
+    built before. The exported schema refuses fields it does not know, so a copy validated against
+    must take these. Read `schema_version`, and ignore fields you do not know.
 
 A file changes when it is built, published, moved between shares, or **its subject's details are
 corrected** (design spec `2026-10-05-subject-corrections-design.md`): a request that carries an
diff --git a/wl_preproc/contracts/nwb_description.py b/wl_preproc/contracts/nwb_description.py
index 351b18b..bc23c62 100644
--- a/wl_preproc/contracts/nwb_description.py
+++ b/wl_preproc/contracts/nwb_description.py
@@ -9,7 +9,9 @@ the measured blocks inside it, and gave each probe its `sorted_runs` (design
 spec `2026-10-01-session-listing-and-run-requests-design.md` section 4); within
 it, 2026-10-14 added what wl-xcon's record says the session was, its kind, its
 display calibration and each run's, and the warnings it accepted (design spec
-`2026-10-14-session-kind-and-warnings-design.md` section 5).
+`2026-10-14-session-kind-and-warnings-design.md` section 5), and 2026-10-18 how
+many of wl-xcon's training records the file holds (design spec
+`2026-10-18-training-records-and-backup-clock-design.md` section 3.2).
 Version 2 gave `probes` their shape, each with both of its areas (design spec
 `2026-09-30-nwb-probes-design.md` section 3.2). Later pieces add groups and
 fields -- a processing summary with unit counts, the photodiode, video and
@@ -179,6 +181,17 @@ class AcceptedWarning(_Frozen):
     run_number: int | None
 
 
+class TrainingRecords(_Frozen):
+    """How many of wl-xcon's training records the file holds: the live
+    changes the rig applied and the console controls it was sent. Who acted
+    is not published (design spec
+    `2026-10-18-training-records-and-backup-clock-design.md` section 0,
+    decision 6)."""
+
+    parameter_changes: int
+    controls: int
+
+
 class Quality(_Frozen):
     timing_tier: str
     reference_source: Literal["barcode", "manifest"]
@@ -260,3 +273,7 @@ class NwbDescription(_Frozen):
     session_kind: Literal["training", "piloting", "recording"] | None = None
     calibration: RigCalibration | None = None
     accepted_warnings: list[AcceptedWarning] = []
+    # How many training records the file holds (design spec
+    # `2026-10-18-training-records-and-backup-clock-design.md` section 3.2),
+    # added within version 3: null for a session wl-xcon did not run.
+    training_records: TrainingRecords | None = None
diff --git a/wl_preproc/nwb/describe.py b/wl_preproc/nwb/describe.py
index 32e4fe8..afbf776 100644
--- a/wl_preproc/nwb/describe.py
+++ b/wl_preproc/nwb/describe.py
@@ -52,9 +52,10 @@ def _rig_record(record: dict | None) -> dict:
     """What wl-xcon's record says the session was, for selecting a dataset by
     kind: the calibration without its colour model, and each warning by its
     code and run (design spec `2026-10-14-session-kind-and-warnings-design.md`
-    section 5)."""
+    section 5); and how many training records the file holds (design spec
+    `2026-10-18-training-records-and-backup-clock-design.md` section 3.2)."""
     if record is None:
-        return {"session_kind": None, "calibration": None, "accepted_warnings": []}
+        return {"session_kind": None, "calibration": None, "accepted_warnings": [], "training_records": None}
     calibration = record["calibration"]
     return {
         "session_kind": record["session_kind"],
@@ -62,6 +63,8 @@ def _rig_record(record: dict | None) -> dict:
             name: calibration[name] for name in ("id", "standard", "measured_on")},
         "accepted_warnings": [{"code": warning["code"], "run_number": warning["run_number"]}
                               for warning in record["warnings"]],
+        "training_records": {"parameter_changes": len(record["parameter_changes"]),
+                             "controls": len(record["controls"])},
     }
 
 
diff --git a/wl_preproc/nwb/gather.py b/wl_preproc/nwb/gather.py
index 37d4ee6..5d5b606 100644
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -83,14 +83,20 @@ def _run_set(activation_key: dict, session_key: dict) -> list[dict]:
 
 def _rig_record(session_key: dict, run_numbers: set[int]) -> dict | None:
     """What wl-xcon's record says the session was (`core.SessionRecord`,
-    `core.AcceptedWarning`), or None when wl-xcon did not run it. The
-    warnings are those accepted at the session's open and those for the
-    file's own runs: one accepted for a run the file does not hold is about
-    that run (design spec `2026-10-14-session-kind-and-warnings-design.md`,
-    amendment 1). Who accepted a warning is not read: the file names no one
-    (section 0, decision 3)."""
+    `core.AcceptedWarning`), and its training records (`core.ParameterChange`,
+    `core.RigControl`), or None when wl-xcon did not run it. The warnings are
+    those accepted at the session's open and those for the file's own runs:
+    one accepted for a run the file does not hold is about that run (design
+    spec `2026-10-14-session-kind-and-warnings-design.md`, amendment 1). The
+    training records are chosen by the same rule (design spec
+    `2026-10-18-training-records-and-backup-clock-design.md`, amendment 10).
+    Who acted is read by kind only: the file names no one (decision 3 of the
+    first; decision 6 of the second)."""
     from wl_preproc.schema import core
 
+    def the_files(row: dict) -> bool:
+        return row["run_index"] is None or row["run_number"] in run_numbers
+
     rows = (core.SessionRecord & session_key).to_dicts()
     if not rows:
         return None
@@ -107,7 +113,15 @@ def _rig_record(session_key: dict, run_numbers: set[int]) -> dict | None:
                       "accepted_in": [kind for kind in (warning["accepted_in"] or "").split(",") if kind],
                       "how": warning["how"], "run_number": warning["run_number"], "at_local": warning["at_local"]}
                      for warning in (core.AcceptedWarning & session_key).to_dicts(order_by="warning_number")
-                     if warning["run_index"] is None or warning["run_number"] in run_numbers],
+                     if the_files(warning)],
+        "parameter_changes": [
+            {name: change[name] for name in ("session_time", "run_number", "name", "was", "now", "sequence",
+                                             "by_kind")}
+            for change in (core.ParameterChange & session_key).to_dicts(order_by="change_line") if the_files(change)],
+        "controls": [
+            {name: control[name] for name in ("session_time", "time_source", "at", "kind", "trial_index", "detail",
+                                              "by_kind")}
+            for control in (core.RigControl & session_key).to_dicts(order_by="control_line") if the_files(control)],
     }
 
 
diff --git a/wl_preproc/nwb/rig_record.py b/wl_preproc/nwb/rig_record.py
index 10283e9..a81837a 100644
--- a/wl_preproc/nwb/rig_record.py
+++ b/wl_preproc/nwb/rig_record.py
@@ -10,11 +10,19 @@ the session's clock. **The file names no one who accepted a warning** (section
 0, decision 3): this repository's database keeps that, and the listing gives
 wl.works its own member's name and account.
 
+**Its training records** (design spec
+`2026-10-18-training-records-and-backup-clock-design.md` section 3.2): each live
+change the rig applied, in `parameter_changes`, and each console control, in
+`controls`, at its session time where it was timed, with its values and its
+detail as JSON text. The file gives only the kind of actor, never a name,
+account, issuer or token (its decision 6).
+
 Written only for a session wl-xcon ran (`gather.py::_rig_record`)."""
 
 from __future__ import annotations
 
 import json
+import math
 
 from pynwb import NWBFile
 from pynwb.core import DynamicTable
@@ -22,15 +30,20 @@ from pynwb.core import DynamicTable
 from wl_preproc.nwb.columns import column
 
 
+def _time(value: float | None) -> float:
+    return math.nan if value is None else value
+
+
 def add_rig_record(nwb: NWBFile, record: dict) -> None:
-    """One `session` row, and an `accepted_warnings` table when the session
-    accepted any warning."""
+    """One `session` row, and an `accepted_warnings`, `parameter_changes` or
+    `controls` table when the file has any such row."""
     calibration = record["calibration"] or {}
     standard = calibration.get("standard")
     module = nwb.create_processing_module(
         name="rig_record",
         description=("What wl-xcon's record says the session was: the kind it was run as, the display calibration "
-                     "it opened with, and the warnings it accepted. Carried as the rig wrote them."),
+                     "it opened with, the warnings it accepted, and its training records, the changes it applied "
+                     "and the controls it was sent. Carried as the rig wrote them."),
     )
     module.add(DynamicTable(name="session", description="The session's kind and display calibration.", columns=[
         column("session_kind", "training, piloting or recording ('' if not recorded).",
@@ -45,8 +58,15 @@ def add_rig_record(nwb: NWBFile, record: dict) -> None:
                ["" if calibration.get("cones") is None else json.dumps(calibration["cones"], sort_keys=True)]),
     ]))
     warnings = record["warnings"]
-    if not warnings:
-        return
+    if warnings:
+        _add_warnings(module, warnings)
+    if record["parameter_changes"]:
+        _add_changes(module, record["parameter_changes"])
+    if record["controls"]:
+        _add_controls(module, record["controls"])
+
+
+def _add_warnings(module, warnings: list[dict]) -> None:
     module.add(DynamicTable(name="accepted_warnings", description="Each warning the session accepted.", columns=[
         column("code", "The warning's code.", [warning["code"] for warning in warnings]),
         column("detail", "Its sentence ('' if none).", [warning["detail"] or "" for warning in warnings]),
@@ -59,3 +79,44 @@ def add_rig_record(nwb: NWBFile, record: dict) -> None:
         column("accepted_at_local", "When, in the rig's local time: its bookkeeping, not a time on the session's "
                                     "clock ('' if not recorded).", [warning["at_local"] or "" for warning in warnings]),
     ]))
+
+
+def _add_changes(module, changes: list[dict]) -> None:
+    module.add(DynamicTable(name="parameter_changes", description=(
+        "Each live change the rig applied, from wl-xcon's parameter_changes.jsonl, timed by the code it strobed."),
+        columns=[
+            column("session_time", "When, in session seconds: its code's time (NaN if untimed).",
+                   [_time(change["session_time"]) for change in changes]),
+            column("run_number", "The run it was applied in (-1: none, or a run the recording did not measure).",
+                   [-1 if change["run_number"] is None else change["run_number"] for change in changes]),
+            column("setting", "The setting it changed.", [change["name"] for change in changes]),
+            column("was", "Its old value, as JSON text ('' if the row has none).",
+                   [change["was"] or "" for change in changes]),
+            column("now", "Its new value, as JSON text ('' if the row has none).",
+                   [change["now"] or "" for change in changes]),
+            column("sequence", "wl-xcon's number for the change (-1 if the row has none).",
+                   [-1 if change["sequence"] is None else change["sequence"] for change in changes]),
+            column("by_kind", "Who made it, by kind only: box or member ('' if not recorded).",
+                   [change["by_kind"] or "" for change in changes]),
+        ]))
+
+
+def _add_controls(module, controls: list[dict]) -> None:
+    module.add(DynamicTable(name="controls", description=(
+        "Each console control the rig was sent, from wl-xcon's controls.jsonl, timed by the code it strobed or, "
+        "for one that strobed nothing, by the rig's clock fitted to the recording."), columns=[
+            column("session_time", "When, in session seconds (NaN if untimed).",
+                   [_time(control["session_time"]) for control in controls]),
+            column("time_source", "How its time was found: code, or rig_clock for the rig's clock fit ('' if "
+                                  "untimed).", [control["time_source"] or "" for control in controls]),
+            column("rig_at", "The rig's own instant, POSIX seconds on its wall clock: its bookkeeping, not a time "
+                             "on the session's clock (NaN if none).", [_time(control["at"]) for control in controls]),
+            column("kind", "The control: pause, resume, mark, note, schedule, cancel, scheduled_stop, stop, end or "
+                           "reward, as written.", [control["kind"] for control in controls]),
+            column("trial_index", "The trial it happened in or, between trials, the one about to run (-1 if not "
+                                  "recorded).",
+                   [-1 if control["trial_index"] is None else control["trial_index"] for control in controls]),
+            column("detail", "The control's own fields, as JSON text.", [control["detail"] for control in controls]),
+            column("by_kind", "Who sent it, by kind only: box or member ('' for nobody).",
+                   [control["by_kind"] or "" for control in controls]),
+        ]))
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
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/nwb/test_describe.py tests/nwb/test_writers.py tests/responder/test_protocol_document.py tests/schema/test_nwb_rig_record.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `41 passed`

- [ ] **Step 6: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/cli/test_schemas_export.py tests/contracts tests/nwb tests/responder/test_protocol_document.py tests/schema/test_daemon.py tests/schema/test_nwb_build.py tests/schema/test_nwb_correct.py tests/schema/test_nwb_outside_runs.py tests/schema/test_nwb_probes.py tests/schema/test_nwb_rig_record.py tests/schema/test_nwb_trials.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `343 passed, 1 skipped`

- [ ] **Step 7: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "every row in every file",
  "wl_preproc/nwb/gather.py",
  "        return row[\"run_index\"] is None or row[\"run_number\"] in run_numbers",
  "        return True",
  "tests/schema/test_nwb_rig_record.py"
 ],
 [
  "an untimed row at zero",
  "wl_preproc/nwb/rig_record.py",
  "    return math.nan if value is None else value",
  "    return 0.0 if value is None else value",
  "tests/nwb/test_writers.py"
 ],
 [
  "the controls counted as changes",
  "wl_preproc/nwb/describe.py",
  "\"controls\": len(record[\"controls\"])},",
  "\"controls\": len(record[\"parameter_changes\"])},",
  "tests/nwb/test_describe.py"
 ]
]
```

Expected: 3 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 8: Commit.**

```bash
git add -A tests wl_preproc docs
git commit -F - <<'MSG'
feat(nwb): the training records in the file and its description -- processing/rig_record's parameter_changes and controls, the file's own runs' and those outside any run, who acted by kind only; the description's training_records, within version 3 (section 3.2, amendments 10 and 14)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 6: The listing's counts and clock

`GET /sessions` gains, per session, `training_records` (its parameter changes, pauses and manual rewards, counted by the database) and `clock`, the clock that kept its time (spec §3.4; amendments 13 and 14), named in the protocol, its exported schema regenerated.

**Files:**
- Modify: `wl_preproc/listing/entry.py` (`SessionFacts`, `build_entry`, `gather_all`)
- Modify: `wl_preproc/contracts/protocol.py` (`ListedTrainingRecords`; `SessionEntry`), `docs/ops/lab-host-protocol.md` (`GET /sessions`), `docs/schemas/session_listing.json` (exported)
- Test: `tests/listing/test_entry.py` (`_facts` gains the three fields), `tests/schema/test_session_listing.py` (its session gains acts), `tests/responder/test_protocol_document.py`

**Interfaces:**
- Consumes: Task 4's `core.ParameterChange` and `core.RigControl`; part 2's `timebase.SessionClock`.
- Produces: `SessionFacts.parameter_changes: int`, `.control_kinds: dict[str, int]`, `.clock: str | None`;
  `contracts/protocol.py::ListedTrainingRecords(parameter_changes: int, pauses: int, manual_rewards: int)`;
  `SessionEntry.training_records: ListedTrainingRecords | None` and `.clock: Literal["syncbox", "nidq",
  "lab"] | None`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/listing/test_entry.py b/tests/listing/test_entry.py
index 7f6035c..6eabf29 100644
--- a/tests/listing/test_entry.py
+++ b/tests/listing/test_entry.py
@@ -34,7 +34,7 @@ def _facts(**changes):
         census=[_probe(100, "a" * 32)],
         electrodes={("a" * 32, "NP1000"): [0, 1, 2]},
         strobed_runs=[1, 2], strobed_blocks=[1, 2], censused=frozenset({100}), rig_problems=[],
-        rig_session=None, run_calibrations={}, warnings=[],
+        rig_session=None, run_calibrations={}, warnings=[], parameter_changes=0, control_kinds={}, clock="syncbox",
     )
     return dataclasses.replace(facts, **changes)
 
@@ -278,3 +278,27 @@ def test_a_kind_not_recorded_lists_none_beside_a_calibration_not_recorded():
 
     entry = build_entry(_facts(rig_session={**{name: None for name in _SESSION_RECORD}}))
     assert (entry["session_kind"], entry["calibration"]) == (None, None)
+
+
+def test_a_session_lists_how_many_training_records_it_has_and_its_clock():
+    """Design spec `2026-10-18-training-records-and-backup-clock-design.md`
+    section 3.4: the parameter changes, pauses and manual rewards, counted;
+    the rows are not listed. A session wl-xcon did not run lists none."""
+    from wl_preproc.listing.entry import build_entry
+
+    entry = build_entry(_facts(rig_session={"session_kind": "training"}, parameter_changes=3,
+                               control_kinds={"pause": 2, "resume": 3, "reward": 1, "note": 4}))
+    assert entry["training_records"] == {"parameter_changes": 3, "pauses": 2, "manual_rewards": 1}
+    assert entry["clock"] == "syncbox"
+    plain = build_entry(_facts(clock=None))
+    assert (plain["training_records"], plain["clock"]) == (None, None)
+
+
+def test_a_session_read_before_its_training_records_were_kept_lists_zeros():
+    """The plan's Review Focus 5: a session wl-xcon ran whose event stage ran
+    before part 1 has its record and no training rows, so it lists zeros,
+    never null, as the protocol says."""
+    from wl_preproc.listing.entry import build_entry
+
+    entry = build_entry(_facts(rig_session={"session_kind": "training"}))
+    assert entry["training_records"] == {"parameter_changes": 0, "pauses": 0, "manual_rewards": 0}
diff --git a/tests/responder/test_protocol_document.py b/tests/responder/test_protocol_document.py
index 6d62af5..9b9a118 100644
--- a/tests/responder/test_protocol_document.py
+++ b/tests/responder/test_protocol_document.py
@@ -19,11 +19,13 @@ def _section(heading: str) -> str:
 
 
 def test_every_field_a_listed_session_carries_is_named_in_the_protocol():
-    from wl_preproc.contracts.protocol import ListedActor, ListedCalibration, ListedRun, ListedWarning, SessionEntry
+    from wl_preproc.contracts.protocol import (ListedActor, ListedCalibration, ListedRun, ListedTrainingRecords,
+                                               ListedWarning, SessionEntry)
 
     section = _section("`GET /sessions`")
     missing = sorted(f"{model.__name__}.{name}"
-                     for model in (SessionEntry, ListedRun, ListedWarning, ListedCalibration, ListedActor)
+                     for model in (SessionEntry, ListedRun, ListedWarning, ListedCalibration, ListedActor,
+                                   ListedTrainingRecords)
                      for name in model.model_fields if f"`{name}`" not in section)
     assert missing == []
 
diff --git a/tests/schema/test_session_listing.py b/tests/schema/test_session_listing.py
index 6a9937c..17de62c 100644
--- a/tests/schema/test_session_listing.py
+++ b/tests/schema/test_session_listing.py
@@ -25,7 +25,11 @@ def listed(dj_conn, prefix, tmp_path_factory):
                              {"code": "default-calibration"},
                              {"code": "luminance-step", "run": 1, "how": "start",
                               "by": {"kind": "member", "name": "J. W.", "account": "acct-1",
-                                     "issuer": "https://wl.works", "token_id": "tok-1"}}]}),
+                                     "issuer": "https://wl.works", "token_id": "tok-1"}}],
+                             "changes": [{"trial": 1}],
+                             "controls": [{"trial": 2}, {"trial": 2, "kind": "resume", "event_code": 4132},
+                                          {"trial": 3, "kind": "note", "event_code": None},
+                                          {"trial": 4, "kind": "reward", "event_code": 4134}]}),
         "plain": _session(tmp_path_factory, subject="sllist2", session_id="2025-07-22_01"),
     }
     daemon.run_once(prefix=prefix)
@@ -78,6 +82,22 @@ def test_an_entry_carries_what_wl_xcons_record_says_the_session_was(listed):
     assert (plain["session_kind"], plain["calibration"], plain["accepted_warnings"]) == (None, None, [])
 
 
+def test_an_entry_counts_the_training_records_and_names_its_clock(listed):
+    """Through one daemon pass (design spec
+    `2026-10-18-training-records-and-backup-clock-design.md` section 3.4): a
+    change, a pause and a manual reward, counted from the database; the
+    resume and the note are in neither count. A session wl-xcon did not run
+    has none to count, and the sync box kept time in both."""
+    from wl_preproc.listing.entry import session_entry
+
+    _recipe, key = listed["runs"]
+    entry = session_entry(key)
+    assert entry["training_records"] == {"parameter_changes": 1, "pauses": 1, "manual_rewards": 1}
+    assert entry["clock"] == "syncbox"
+    plain = session_entry(listed["plain"][1])
+    assert (plain["training_records"], plain["clock"]) == (None, "syncbox")
+
+
 def test_the_listing_does_not_read_the_colour_model_it_does_not_list(listed, monkeypatch):
     """`calibration_cones` is a blob the listing never shows; reading it for
     every listed session on every pass is work for nothing (the session
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/listing/test_entry.py tests/responder/test_protocol_document.py tests/schema/test_session_listing.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `22 failed, 17 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/docs/ops/lab-host-protocol.md b/docs/ops/lab-host-protocol.md
index 5f26bea..c174c15 100644
--- a/docs/ops/lab-host-protocol.md
+++ b/docs/ops/lab-host-protocol.md
@@ -610,14 +610,22 @@ Authorization: Bearer <token>
     box), never the token or its issuer. A session wl-xcon did not run, one whose record predates
     its engine build B, and one read before this host carried them (2026-10-14) list null, null and
     `[]` alike;
+  - `training_records`, how many of wl-xcon's training records the session has (design spec
+    `2026-10-18-training-records-and-backup-clock-design.md` §3.4): its `parameter_changes`, and of
+    its console controls its `pauses` and `manual_rewards`; the rows are in the database and the
+    NWB file, never listed. Null for a session wl-xcon did not run; a session read before this host
+    carried them (2026-10-18) lists zeros;
+  - `clock`, which clock kept the session's time: `syncbox` (the only one built yet), `nidq` or
+    `lab`, or null until the daemon has chosen it;
   - `flags`, each a `code`, a `message`, and the `run_number` or `block_number` it is about:
     `waiting_for_run_markers` (no measured run yet), `repeated_run_number` and
     `repeated_block_number` (a crash restart before wl-xcon's XC-026; only the first is
     listed), `run_without_block`, `bank_change_in_run`, `block_type_unknown`, `task_unknown`,
     `block_outside_runs` (a block in no measured run: its run's `RUN_START` was lost), and
     `rig_record_problem` (what wl-xcon's record could not supply, each naming its file, line by
-    line: `runs.jsonl`, `config.json` or `warnings.jsonl`; or the run record's absence; a problem
-    stored before 2026-10-14 names no file).
+    line: `runs.jsonl`, `config.json`, `warnings.jsonl`, `parameter_changes.jsonl` or
+    `controls.jsonl`, the last two also when a count of codes disagrees or rows cannot be timed; or
+    the run record's absence; a problem stored before 2026-10-14 names no file).
     **None of them blocks anything here.**
 
 ---
diff --git a/wl_preproc/contracts/protocol.py b/wl_preproc/contracts/protocol.py
index fa3e864..77d7ec6 100644
--- a/wl_preproc/contracts/protocol.py
+++ b/wl_preproc/contracts/protocol.py
@@ -527,6 +527,19 @@ class ListedWarning(BaseModel):
     by: ListedActor | None
 
 
+class ListedTrainingRecords(BaseModel):
+    """How many of wl-xcon's training records the session has: its live
+    parameter changes, and of its console controls the pauses and the manual
+    rewards. The rows are in the database and the NWB file, not here (design
+    spec `2026-10-18-training-records-and-backup-clock-design.md` section 3.4)."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    parameter_changes: int
+    pauses: int
+    manual_rewards: int
+
+
 class RejectedFile(BaseModel):
     model_config = ConfigDict(extra="forbid", frozen=True)
 
@@ -558,6 +571,12 @@ class SessionEntry(BaseModel):
     session_kind: Literal["training", "piloting", "recording"] | None
     calibration: ListedCalibration | None
     accepted_warnings: list[ListedWarning]
+    # Its training records' counts, null for a session wl-xcon did not run;
+    # and the clock that kept its time, null until chosen (design spec
+    # `2026-10-18-training-records-and-backup-clock-design.md` sections 3.4
+    # and 4.1).
+    training_records: ListedTrainingRecords | None
+    clock: Literal["syncbox", "nidq", "lab"] | None
 
 
 class SessionListing(BaseModel):
diff --git a/wl_preproc/listing/entry.py b/wl_preproc/listing/entry.py
index f9062bc..7110b93 100644
--- a/wl_preproc/listing/entry.py
+++ b/wl_preproc/listing/entry.py
@@ -53,6 +53,12 @@ class SessionFacts:
     rig_session: dict | None  # its core.SessionRecord row; None when wl-xcon did not run it
     run_calibrations: dict[int, str]  # run_number -> core.RunCalibration's calibration_id
     warnings: list[dict]  # its core.AcceptedWarning rows, by warning_number
+    # How many of wl-xcon's training records it has, and the clock that kept
+    # its time (design spec `2026-10-18-training-records-and-backup-clock-design.md`
+    # section 3.4).
+    parameter_changes: int  # its core.ParameterChange rows
+    control_kinds: dict[str, int]  # control kind -> its core.RigControl rows
+    clock: str | None  # timebase.SessionClock's source; None until chosen
 
 
 def _repeats(numbers: list[int]) -> list[tuple[int, int]]:
@@ -160,6 +166,7 @@ def build_entry(facts: SessionFacts) -> dict:
         "runs": runs, "segments": segments,
         "probes": sorted({part["probe_serial"] for part in facts.census if part["probe_serial"]}),
         "flags": flags,
+        "clock": facts.clock,
         **_rig_session(facts),
     }
     return SessionEntry.model_validate(entry).model_dump(mode="json")
@@ -172,7 +179,10 @@ def _rig_session(facts: SessionFacts) -> dict:
     """The session's kind, calibration and accepted warnings, as wl-xcon's
     record gives them. Who accepted a warning is listed by name and wl.works
     account, never by the token's id or its issuer (design spec
-    `2026-10-14-session-kind-and-warnings-design.md` section 0, decision 3)."""
+    `2026-10-14-session-kind-and-warnings-design.md` section 0, decision 3).
+    And how many training records it has, never the rows (design spec
+    `2026-10-18-training-records-and-backup-clock-design.md` section 0,
+    decision 7)."""
     row = facts.rig_session or {}
     recorded = any(row.get(column) is not None for column in _CALIBRATION_COLUMNS)
     return {
@@ -189,6 +199,9 @@ def _rig_session(facts: SessionFacts) -> dict:
             "by": None if warning["by_kind"] is None and warning["by_name"] is None else {
                 "kind": warning["by_kind"], "name": warning["by_name"], "account": warning["by_account"]},
         } for warning in sorted(facts.warnings, key=lambda warning: warning["warning_number"])],
+        "training_records": None if facts.rig_session is None else {
+            "parameter_changes": facts.parameter_changes, "pauses": facts.control_kinds.get("pause", 0),
+            "manual_rewards": facts.control_kinds.get("reward", 0)},
     }
 
 
@@ -209,6 +222,8 @@ def gather_all(keys: list[dict]) -> dict[tuple[str, datetime.datetime], SessionF
     all of them: every daemon pass builds every listed session's entry, and
     reading each table once per session grew with the lab (the listing's
     M9)."""
+    import datajoint as dj
+
     from wl_preproc.schema import core, ephys, ingest, pipeline, timebase
 
     keys = [{name: key[name] for name in ("subject", "session_datetime")} for key in keys]
@@ -255,6 +270,13 @@ def gather_all(keys: list[dict]) -> dict[tuple[str, datetime.datetime], SessionF
         "session_kind", *_CALIBRATION_COLUMNS).to_dicts()}
     run_calibrations = _by_session((core.RunCalibration & keys).to_dicts())
     warnings = _by_session((core.AcceptedWarning & keys).to_dicts(order_by="warning_number"))
+    # Counted by the database, since the rows are not listed.
+    changes = {_session(row): int(row["n"]) for row in (pipeline.Session & keys).aggr(
+        core.ParameterChange, n="count(*)", exclude_nonmatching=True).to_dicts()}
+    control_kinds = collections.defaultdict(dict)
+    for row in dj.U("subject", "session_datetime", "kind").aggr(core.RigControl & keys, n="count(*)").to_dicts():
+        control_kinds[_session(row)][row["kind"]] = int(row["n"])
+    clocks = {_session(row): row["source"] for row in (timebase.SessionClock & keys).proj("source").to_dicts()}
     facts = {}
     for ingestion in (ingest.Ingestion & keys).proj("session_dir").to_dicts():
         session = _session(ingestion)
@@ -283,6 +305,9 @@ def gather_all(keys: list[dict]) -> dict[tuple[str, datetime.datetime], SessionF
             rig_session=rig_sessions.get(session),
             run_calibrations={row["run_number"]: row["calibration_id"] for row in run_calibrations.get(session, [])},
             warnings=warnings.get(session, []),
+            parameter_changes=changes.get(session, 0),
+            control_kinds=dict(control_kinds.get(session, {})),
+            clock=clocks.get(session),
         )
     return facts
 
PATCH
```

- [ ] **Step 4: Export the schemas.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m wl_preproc.cli.main schemas export --out docs/schemas
git status --short docs/schemas
```

Expected: ` M docs/schemas/session_listing.json`, and no other file.

- [ ] **Step 5: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/listing/test_entry.py tests/responder/test_protocol_document.py tests/schema/test_session_listing.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `39 passed`

- [ ] **Step 6: Run their neighbours,** in the suite's order.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/cli tests/listing tests/responder tests/schema/test_daemon.py tests/schema/test_session_listing.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `417 passed`

- [ ] **Step 7: The mutation checks.** Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "pauses counted as resumes",
  "wl_preproc/listing/entry.py",
  "\"pauses\": facts.control_kinds.get(\"pause\", 0),",
  "\"pauses\": facts.control_kinds.get(\"resume\", 0),",
  "tests/listing/test_entry.py"
 ],
 [
  "a session wl-xcon did not run counted",
  "wl_preproc/listing/entry.py",
  "\"training_records\": None if facts.rig_session is None else {",
  "\"training_records\": {",
  "tests/listing/test_entry.py"
 ],
 [
  "the clock not read",
  "wl_preproc/listing/entry.py",
  "            clock=clocks.get(session),",
  "            clock=None,",
  "tests/schema/test_session_listing.py"
 ],
 [
  "the controls counted as changes",
  "wl_preproc/listing/entry.py",
  "core.ParameterChange, n=\"count(*)\", exclude_nonmatching=True).to_dicts()}",
  "core.RigControl, n=\"count(*)\", exclude_nonmatching=True).to_dicts()}",
  "tests/schema/test_session_listing.py"
 ]
]
```

Expected: 4 lines, each `CAUGHT`; `git status --short` shows no change.

- [ ] **Step 8: Commit.**

```bash
git add -A tests wl_preproc docs
git commit -F - <<'MSG'
feat(listing): each session's training-record counts and its clock -- GET /sessions' training_records (parameter_changes, pauses, manual_rewards) and clock (section 3.4, amendments 13 and 14)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

---

### Task 7: The records

The spec's amendments 9-16, this part's handoff, wl.works' amendment, and the checkpoint's and `wl.yaml`'s
lines (spec §11). Then the full suite, once, on both interpreters.

**Files:**
- Modify: `docs/superpowers/specs/2026-10-18-training-records-and-backup-clock-design.md`,
  `docs/pending-wl-works-amendments.md`, `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-10-18-training-records.md`

**Interfaces:** none; documents only.

- [ ] **Step 1: Apply the records.** Save this script outside the repository as `records.py` and run it from
  the repository root.

```python
"""Part 1's records: the spec's amendments 9-16, wl.works' amendment, and the
checkpoint's and wl.yaml's lines (built, not merged; next, the review). Run
from the repository root."""
import pathlib

root = pathlib.Path.cwd()


def swap(rel, old, new):
    path = root / rel
    text = path.read_text()
    assert text.count(old) == 1, (rel, old[:70])
    path.write_text(text.replace(old, new))


spec = root / "docs/superpowers/specs/2026-10-18-training-records-and-backup-clock-design.md"
spec.write_text(spec.read_text().rstrip("\n") + """

## Amendments, 2026-10-18, made while proving part 1's plan

9. **§10's item 7, measured: the values and the line are stored as JSON text** (`mediumtext`), not in
   MySQL's `json` type. On the test MySQL through DataJoint 2.3.3, a `json` column gave
   `123456789.12345679` back as `123456789.1234568`, a double's seventeenth digit rounded, and refused
   NaN ("Invalid JSON text"), which wl-xcon's `json.dumps` writes for a value that is not a number, and
   which would fail the session's event stage on every pass. A `mediumtext` column gave back every
   probed value and line exactly. `row` is the line as written; `was`, `now` and `detail` are each
   value's JSON text, keys sorted.
10. **The NWB file holds the training records of its own runs, and those outside any run** (§3.2 did
    not say which): a row of a run the file does not hold is about that run, as ask 5's warnings are
    chosen (its amendment 1). wl-xcon numbers a row by the run in progress or, between runs, the last
    one; a row before the first run has none.
11. **§10's item 10, measured: no fit may miss a code by more than 50 ms**
    (`events/rigtiming.py::RIG_CLOCK_LIMIT_S`). The synthetic rig record states its jitter, each instant
    up to 5 ms either side of its code, its clock 20 ppm fast (`synth/peripherals.py`). Over 2000
    synthetic sessions of 15 s to two hours, 4 to 200 instants each, the largest miss was 6.8 ms (the
    99th percentile 6.1 ms). A step in the rig's clock of 2 s, as a resumed process anchors it again,
    was refused in each of 1000 sessions; one of 0.5 s in 998, of 0.1 s in 819, and of 50 ms in 3. A
    step at a session's last instants tilts the line instead, within the step. wl-xcon has not
    measured its rig's own jitter (amendment 2), so the limit is set against the stated one.
12. **The rig's clock fit is kept in `core.RigClock`,** beside the records it times and written with
    them by the events stage: `rig_at_zero` (the rig's clock at session t = 0), `drift_ppm`, the
    instants matched and the residuals. §3.3 said "with the session's other device maps"; those are
    each an acquisition system's (`timebase.SystemTimebase`), and the rig is not one of `SYSTEMS`. A
    control is timed through the fit only when its row says it strobed nothing (`event_code: null`) and
    carries an instant; a row whose code's count disagrees, or that does not say, stays untimed, as
    amendment 2 says.
13. **§10's item 1, verified:** each row's `event_code` (amendment 2) says which code it strobed, and
    this pipeline decodes every number from 4096 up as a simple event (`contracts/events.py`'s ranges),
    so the framework codes are among the session's codes; a payload's words, read by position, are
    not counted (`events/rigtiming.py::code_times`). The listing's pauses are `controls.jsonl`'s
    `pause` rows and its manual rewards its `reward` rows (wl-xcon's `taskd.py`, read at `7398d3e`).
14. **§10's item 6, verified: both contracts widen within themselves.** The NWB description gains
    `training_records` (`parameter_changes`, `controls`) within version 3, as ask 5's fields were
    added; the listing gains `training_records` (`parameter_changes`, `pauses`, `manual_rewards`) and
    `clock`, listed from part 1 since `timebase.SessionClock` exists. Both exported schemas refuse
    fields they do not know, so wl.works' copies must take these first
    (`docs/pending-wl-works-amendments.md`, the section opened 2026-10-18). Every listed session is
    listed once more when this ships.
15. **The events stage's activation binds `timebase`** (found while proving). Since part 2 the stage
    reads the session's codes through its clock, which records `timebase.SessionClock` on first ask,
    and `events.activate` bound `pipeline` and `core` only. The daemon binds every schema, so nothing
    in production failed; but `tests/schema/test_events.py` run alone failed 20 of its 30 tests at
    `5b7744b` ("assert self._connection is not None"), as did each of part 1's own tests run alone.
16. **The synthetic run record's instants are the rig's clock** (`synth/peripherals.py::rig_clock`):
    each run row's `at` was its session time, which wl-xcon never writes and nothing read. A profile
    without runs is unchanged; one with runs differs in those instants only.
""")

swap("docs/pending-wl-works-amendments.md",
     "**Nine are outstanding: two opened 2026-08-22, one 2026-09-28, one 2026-09-29, two 2026-09-30, one "
     "2026-10-01, one 2026-10-05, one 2026-10-14.**",
     "**Ten are outstanding: two opened 2026-08-22, one 2026-09-28, one 2026-09-29, two 2026-09-30, one "
     "2026-10-01, one 2026-10-05, one 2026-10-14, one 2026-10-18.**")
swap("docs/pending-wl-works-amendments.md",
     "---\n\n# OPEN — what wl-xcon's record says the session was, in the listing and the NWB description\n",
     """---

# OPEN — wl-xcon's training records, counted, and the clock that kept each session's time

**Opened 2026-10-18** with part 1 of
[`specs/2026-10-18-training-records-and-backup-clock-design.md`](superpowers/specs/2026-10-18-training-records-and-backup-clock-design.md)
(§3.2, §3.4; amendment 14; the requester's decisions in §0), answering wl-xcon's ask 1 of 2026-10-11
(its XC-321). Every live change the rig applied and every console control it was sent is kept in this
host's database and in the NWB file's `rig_record` module, each timed by the code it strobed or by the
rig's clock fitted to the recording. **wl.works is sent their counts, never the rows.**

- **`GET /sessions`:** each session gains `training_records` (`parameter_changes`, `pauses`,
  `manual_rewards`; null for a session wl-xcon did not run, zeros for one read before this host carried
  them) and `clock` (`syncbox`, `nidq` or `lab`, null until chosen; `syncbox` is the only clock built
  yet). `docs/schemas/session_listing.json` refuses fields it does not know, so wl.works' copy must take
  these before it validates an entry from a host running this change.
- **`GET /nwb`:** each description gains `training_records` (`parameter_changes`, `controls`) within
  version 3, null for a session wl-xcon did not run; `docs/schemas/nwb_description.json` likewise.
- **Who acted is not sent:** neither the listing nor the file names a person for a change or a
  control (decision 6).
- **A `rig_record_problem` flag may now name `parameter_changes.jsonl` or `controls.jsonl`:** a line it
  could not read, or a count of codes that disagrees, so those rows stay untimed.
- **Every listed session is listed once more** when this ships: each entry changes, so its digest
  does.
- **Later parts add** the timing tiers N and L and further `reference_source` values (the spec's §7);
  each is sent here when built.

---

# OPEN — what wl-xcon's record says the session was, in the listing and the NWB description
""")

swap("docs/CHECKPOINT.md",
     "> and five Minor deferred (§6). Part 1, the training records, can be planned:\n"
     "> wl-xcon's `event_code` is on its `main` (`67d7028`, read at `e5b9835`). Fine-tuning\n",
     "> and five Minor deferred (§6); **part 1, the training records, is built on\n"
     "> `feat/training-records`, not merged** (`handoffs/2026-10-18-training-records.md`).\n"
     "> Next: the review. Fine-tuning\n")
swap("docs/CHECKPOINT.md",
     "> *Until part 2's merge, this said it was built, not merged, and that part 1\n",
     "> *Until part 1 was built, this said it could be planned, wl-xcon's `event_code`\n"
     "> being on its `main` (`67d7028`); true when written.*\n"
     ">\n"
     "> *Until part 2's merge, this said it was built, not merged, and that part 1\n")
swap("wl.yaml",
     "    (789e825; docs/handoffs/2026-10-18-clock-choice.md); part 1 can be\n"
     "    planned, wl-xcon's event_code being on its main (67d7028).\n",
     "    (789e825; docs/handoffs/2026-10-18-clock-choice.md); part 1, the\n"
     "    training records, is built on feat/training-records and not merged\n"
     "    (docs/handoffs/2026-10-18-training-records.md); next, the review.\n")
print("ok")
```

Expected: it prints `ok`.

- [ ] **Step 2: Write the handoff.** Its §5 keeps `{{...}}` slots until Step 5.

```bash
cat > docs/handoffs/2026-10-18-training-records.md <<'HANDOFF'
# The training records (part 1)

**Part 1 of five** of `specs/2026-10-18-training-records-and-backup-clock-design.md`: wl-xcon's ask 1, its
XC-321. wl-xcon's training records as it writes them today, `parameter_changes.jsonl` and `controls.jsonl`,
are kept in the database, the NWB file and the listing, on sessions with the sync box. Each row is timed by
the code its act strobed or, for a control that strobed nothing, by the rig's clock fitted to the recording,
and is otherwise untimed: never guessed.
- **Branch:** `feat/training-records`, from `plan/training-records`.
- **Spec:** amendment 2 (wl-xcon's `event_code`, and its three corrections) and amendments 9-16 are this
  part's, the latter made while proving its plan.
- **Plan:** `docs/superpowers/plans/2026-10-18-training-records.md`.
- **The requester's choice:** to plan part 1 once part 2 was merged (2026-10-18).

Every line of the plan was proven in a scratch branch before the plan was written.

---

## 1. What was built

- **`events/rigrecords.py`:** both files read as written, each row's line and values kept as JSON text,
  `event_code` read as the code, as none (`null`), or as unknown (no key); an unreadable line named by
  file and line.
- **`events/rigtiming.py`:** the n-th row of a file carrying a number takes the n-th occurrence of that
  number among the session's codes; a count that disagrees leaves that number's rows untimed and names
  both counts. The rig's clock is fitted from its run rows' and its coded controls' instants against their
  codes, and times a control that strobed nothing; no fit past a 50 ms miss.
- **`core.ParameterChange`, `core.RigControl` and `core.RigClock`,** written by the events stage in the
  transaction that writes ask 5's rig tables, for a session whose `config.json` is its own. Problems go to
  `core.RunRecordProblem`, as ask 5's do.
- **The NWB file:** `processing/rig_record` gains `parameter_changes` and `controls`, the file's own runs'
  and those outside any run, who acted by kind only; the description gains `training_records`.
- **The listing:** `training_records` (`parameter_changes`, `pauses`, `manual_rewards`) and `clock`.
- **The synthetic generator:** a recipe's rig record can carry changes and controls, their codes strobed
  mid-trial; the rig's instants are its own clock, 20 ppm fast and stamped within 5 ms of each code.
- **The agent's file:** a test that a SQLite file in `xcon/` changes nothing for ingest or the eye stage.

## 2. What the other repositories must do

- **wl.works:** take the listing's and the description's new fields before it validates either from a host
  running this change (`docs/pending-wl-works-amendments.md`, the section opened 2026-10-18).
- **wl-xcon:** nothing; it is told when this is merged.

## 3. Still open

- **Parts 3, 4 and 5** (the spec's §2), and part 2's deferred minor M2, which is decided before part 3.
- **A session event-staged before part 1** keeps no training records until its events stage runs again,
  and lists zeros. No rig has recorded a session yet.

## 4. The rulings

The spec's amendments 9-16.

## 5. What execution measured

Executed on {{DATE}}: Task 1 `{{SHA1}}`, Task 2 `{{SHA2}}`, Task 3 `{{SHA3}}`, Task 4 `{{SHA4}}`, Task 5
`{{SHA5}}`, Task 6 `{{SHA6}}`.
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

Expected, each exiting 0: 3.11, **2459 passed, 27 skipped, 1 deselected, 1 xfailed**; 3.13, **2459 passed, 28 skipped, 1 xfailed**. About 40 minutes. That is
40 more than `main`'s 2419: this part's new tests. CI, without the Andersson data, should show
about 2450 passed with 37 skipped.

- [ ] **Step 5: Fill the handoff's §5** with this execution's own figures: `{{DATE}}` the day,
  `{{SHA1}}` to `{{SHA6}}` Tasks 1-6's commits (`git log --oneline -7`), and `{{S311}}` and
  `{{S313}}` Step 4's two summary lines. Then `grep -c '{{' docs/handoffs/2026-10-18-training-records.md`.

Expected: `0`.

- [ ] **Step 6: Commit.**

```bash
git add -A docs wl.yaml
git commit -F - <<'MSG'
docs: part 1's records -- spec amendments 9-16, its handoff, wl.works' amendment, and the checkpoint's and wl.yaml's lines: built, not merged; next, the review

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```
