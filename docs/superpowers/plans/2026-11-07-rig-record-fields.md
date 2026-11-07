# The Rig's Record, Read Whole: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Read into the published dataset what wl-xcon's engine builds C2a, C2b and C3 and its XC-399 add to the
rig's record: what each trial paid and where its stimuli were, how each run and block ended and what each run owed,
a condition's own values apart from drawn and person-held ones, and the change log by kind and act.

**Architecture:** Every new field is read from the session folder when the NWB file is built, as trial lines
already are, and from the change log's stored line; nothing is added to the database. One small reader holds every
optional field to one rule (`events/rigfields.py`); pure functions beside `nwb/conditions.py` turn them into
trials-, runs- and blocks-table columns and the `rig_record` module's tables; the description gains defaulted
fields within version 3.

**Tech Stack:** Python 3.11/3.13, numpy, pydantic, pynwb, DataJoint 2.3.3 on MySQL (testcontainers in tests).

**Spec:** `docs/superpowers/specs/2026-11-07-rig-record-fields-design.md`: §0-§13, its six sections approved by the
requester on 2026-11-07; amendments 1-10 are Task 7's, made while proving this plan.

**Proven before written.** Every step below was run on a scratch branch and replayed from this plan's own text to
an identical tree: 32 mutation checks caught, and the full suite on both interpreters as Task 7 states.

## Global Constraints

- **No database change** (§2, decision 3.1): no table gains a column, so no `ALTER TABLE` and no migration guard;
  the new fields are read from the session folder when the NWB file is built, and the change log's from the line
  `core.ParameterChange.row` already stores.
- **Every new field optional**: a record from before wl-xcon's engine builds C2a, C2b, C3 and its XC-399 reads, and
  publishes, exactly as today; a column, table or summary key appears only where some row carries its field.
- **Read, never repaired**: a new field of the wrong type is read as none and said once per field per session --
  a trial line's or a change row's in the description's `notes`, a run row's among the run record's problems, as
  its other problems are (amendment 10); it never drops its line.
- **A trial index is published as a trial id, a run index as a run number** (§2's rulings).
- **The NWB description stays at version 3**, each new field defaulted; `docs/schemas/` is exported again in the
  task that changes a contract (`python -m wl_preproc.cli.main schemas export --out docs/schemas`).
- **A float computed through sin, cos or atan2 is compared to a tolerance in a test**, never exactly: CI's Linux
  runners round it otherwise than macOS (main's CI of 2026-11-07, fixed by `01f2a57`).
- **Execute on a branch `feat/rig-record-fields`** cut from `plan/rig-record-fields` once this plan is committed
  there.
- **Python 3.11 and 3.13**: the full suite runs once on each, at the end (Task 7).
- **wl-xcon's simulated session folder** (§10) is vendored by a task added when it arrives; the merge waits for
  neither.
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code,
  data or tables; leave the stray `wl-preproc` symlink at the repository root out of every commit.
- **Commands run from the repository root.** In a git worktree, `.venv/bin/python` is the main checkout's;
  every test command sets `PYTHONPATH=$PWD`, `PYTHONDONTWRITEBYTECODE=1` and the NSLR and BMD references, as CI
  does; export the three `WLPP_*` variables before running `mutate.py`.
- **Patches apply with `git apply` and must apply cleanly.**
- **Every commit ends with:**
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
  ```

## Review Focus

The five inputs the spec implies that a person is most likely to meet, most likely first; each is pinned by a
test in the task that owns the code:
1. **Every session recorded before these builds:** it publishes exactly as today.
   `test_a_line_before_c2a_carries_none_of_them`, `test_a_column_is_written_only_where_some_line_carries_its_field`
   (Task 1),
   `test_rows_before_c2a_carry_none_of_them`, `test_a_run_column_is_written_only_where_some_run_carries_its_field`
   (Task 3), the conditions summary's exact-dictionary tests written before (Task 4), and
   `test_the_rig_record_has_wl_xcons_line_shape`, unchanged (Task 6).
2. **A field of the wrong type**, as a hand-edited or half-written record has: none, said once, the line kept.
   `test_a_field_it_cannot_read_is_none_said_once_and_its_line_kept` (Task 1), its stimulus, run and change-row
   peers (Tasks 2, 3 and 5).
3. **A rig record that disagrees with the stream:** a block end naming a block the recording never opened, a block
   no end names, a presentation count off, a trial index no line names; notes, never a refusal.
   `test_a_disagreement_with_the_stream_is_a_note_never_a_refusal` (Task 3).
4. **A revert of several settings, a clear and a switch:** one act, and neither counted.
   `test_the_live_changes_counted_are_a_persons_acts_on_a_setting` (Task 5) and the listing through the database
   in `test_the_description_and_the_listing_say_it` (Task 6).
5. **A direction drawn across 0 degrees, or given as -90:** an arc, never 0 to 360.
   `test_a_direction_is_summarised_as_the_smallest_arc_holding_it` and `test_the_arc_leaves_out_the_widest_gap`
   (Task 4).

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
| `wl_preproc/events/rigfields.py` (new) | every optional field's one rule | 1, 2 |
| `wl_preproc/events/rigtrials.py`, `nwb/trial_record.py` (new) | a trial line's fields; the trials table's columns | 1, 2 |
| `wl_preproc/nwb/stimuli.py` (new), `nwb/rig_record.py`, `nwb/build.py` | where each stimulus was; the module's tables | 2, 3, 5 |
| `wl_preproc/events/rigruns.py`, `nwb/run_record.py` (new), `nwb/intervals.py` | the run rows; runs, blocks and what each run owed | 3 |
| `wl_preproc/nwb/conditions.py` | a condition's own values, drawn and held ones, arcs, long names | 4, 6 |
| `wl_preproc/events/rigrecords.py`, `events/rigtiming.py`, `listing/entry.py` | the change log by kind and act; its timing; its count | 5 |
| `wl_preproc/nwb/gather.py`, `nwb/describe.py`, `contracts/nwb_description.py`, `contracts/protocol.py`, `docs/schemas/` | gathering and describing | 1-6 |
| `wl_preproc/synth/recipe.py`, `synth/peripherals.py` | the synthetic rig's new fields | 6 |
| the spec, `HANDOVER-wl-expcontroller.md`, `docs/pending-wl-works-amendments.md`, the handoff (new), `docs/CHECKPOINT.md`, `wl.yaml` | the records | 7 |

---

### Task 1: Each trial says what the rig's record says of it

§2 and §3: the trial line's optional fields from wl-xcon's engine builds C2a, C2b and C3 and the fluid it commanded, read by `events/rigfields.py`'s rule (absent, the line reads as before; unreadable, none, said once per field, never dropping the line), and published as trials-table columns where some line carries them; `repeat_of` as the repeated trial's `trial_id`, through the run's lines.

**Files:**
- Create: `wl_preproc/events/rigfields.py` (`Kind`, `Fields`, the readers)
- Modify: `wl_preproc/events/rigtrials.py` (`Planned`, `OPTIONAL`, `RigTrial`'s fields, `carries`)
- Create: `wl_preproc/nwb/trial_record.py` (`COLUMNS`, `PLANNED_COLUMNS`, `places`, `record_columns`)
- Modify: `wl_preproc/nwb/gather.py` (`_conditions`), `wl_preproc/nwb/intervals.py` (`_record_columns`, `add_trials`)
- Test: `tests/events/test_rigtrials.py`, `tests/nwb/test_trial_record.py` (new), `tests/nwb/test_writers.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `rigfields.Kind(read, says)`, `rigfields.Fields(rows).read(row, name, kind, *, field=None)`,
  `.wrong(field, says)`, `.problems() -> list[str]`; the kinds `BOOLEAN`, `WHOLE`, `NUMBER`, `TEXT`, `TEXTS`,
  `OBJECTS`, `DRAWN`, `ANY`; `rigtrials.Planned`, `rigtrials.OPTIONAL`, `RigTrial.run` and the fields of §3,
  `RigTrial.carries: frozenset[str]`; `trial_record.places(record) -> {(run, index): trial_number}`,
  `trial_record.record_columns(trials, matched, place) -> (columns, notes)`; each gathered trial's `record`
  dict; `intervals._record_columns(rows, descriptions)`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/events/test_rigtrials.py b/tests/events/test_rigtrials.py
index 1e85a14..7b6e4d2 100644
--- a/tests/events/test_rigtrials.py
+++ b/tests/events/test_rigtrials.py
@@ -81,3 +81,70 @@ def test_a_line_naming_a_run_without_its_number_is_left_out(tmp_path):
     assert [trial.number for trial in record.trials] == [2]
     assert [problem.split(":")[0] for problem in record.problems] == ["line 1", "line 3"]
     assert "trial_number" in record.problems[0]
+
+
+def _line_c3(trial_number, index, **fields):
+    """A line as wl-xcon writes it since its engine builds C2a, C2b and C3
+    (`wl_xcon/record.py::Recorder.trial`), with the fluid it commanded."""
+    return json.dumps({**json.loads(_line155(trial_number, 0, index)), "counting": "counted", "shown": True,
+                       "repeat_of": None, "at_once": False, "previous": "contrast-50", "drummed": False,
+                       "drumming": None, "aids": [], "structure": "default", "overridden": [], "drawn": {},
+                       "live": [], "eased": [], "planned": None, "fluid_ml": 0.12, **fields})
+
+
+def test_a_line_since_c2a_says_what_the_trial_paid_and_where_it_sits(tmp_path):
+    """Design spec `2026-11-07-rig-record-fields-design.md` section 3: C2a's
+    count and sequence, C2b's structure and overrides, C3's draws, a person's
+    holds, eased settings and planned place, and the fluid commanded. What it
+    paid is not its answer: a wrong answer under drumming is `owed`."""
+    from wl_preproc.events.rigtrials import Planned, read_rig_trials
+
+    rows = [_line_c3(1, 0),
+            _line_c3(2, 1, counting="owed", repeat_of=0, at_once=True, drummed=True, drumming="block",
+                     aids=["drumming", "clear"], overridden=["contrast"], live=["contrast", "hold"],
+                     eased=["contrast"], drawn={"hold": "task", "jitter": 7},
+                     planned={"cycle": 0, "position": 1, "lead_in": False}),
+            _line_c3(3, 2, planned={"run": 2, "switch": True, "value": {"class": "Disc", "fields": {}}})]
+    first, second, third = read_rig_trials(_record(tmp_path, rows), "pico").trials
+    assert (first.run, first.counting, first.shown, first.repeat_of, first.at_once, first.previous) == (
+        0, "counted", True, None, False, "contrast-50")
+    assert (first.structure, first.fluid_ml, first.drawn, first.planned) == ("default", 0.12, {}, None)
+    assert (second.counting, second.repeat_of, second.at_once, second.drummed, second.drumming) == (
+        "owed", 0, True, True, "block")
+    assert (second.aids, second.overridden, second.live, second.eased) == (
+        ("drumming", "clear"), ("contrast",), ("contrast", "hold"), ("contrast",))
+    assert second.drawn == {"hold": "task", "jitter": 7}
+    assert second.planned == Planned(cycle=0, position=1, lead_in=False)
+    assert third.planned == Planned(run=2, switch=True, value={"class": "Disc", "fields": {}})
+    assert "planned" in third.carries and "counting" in third.carries
+
+
+def test_a_line_before_c2a_carries_none_of_them(tmp_path):
+    """A record written before the builds reads exactly as before."""
+    from wl_preproc.events.rigtrials import read_rig_trials
+
+    (trial,) = read_rig_trials(_record(tmp_path, [_line155(1, 0, 0)]), "pico").trials
+    assert trial.carries == frozenset()
+    assert (trial.counting, trial.shown, trial.aids, trial.drawn, trial.planned, trial.fluid_ml) == (
+        None, None, (), None, None, None)
+
+
+def test_a_field_it_cannot_read_is_none_said_once_and_its_line_kept(tmp_path):
+    """Read, never repaired (section 2): a value of the wrong type is read as
+    none and reported once per field, with how many lines; the line stays."""
+    from wl_preproc.events.rigtrials import read_rig_trials
+
+    rows = [_line_c3(1, 0, shown="yes"), _line_c3(2, 1, shown=1, aids="clear"),
+            _line_c3(3, 2, planned={"cycle": "0"}, drawn={"hold": None}, fluid_ml=float("nan"))]
+    record = read_rig_trials(_record(tmp_path, rows), "pico")
+    assert [trial.number for trial in record.trials] == [1, 2, 3]
+    assert [trial.shown for trial in record.trials] == [None, None, True]
+    assert (record.trials[1].aids, record.trials[2].planned, record.trials[2].drawn,
+            record.trials[2].fluid_ml) == ((), None, None, None)
+    assert record.problems == (
+        "1 trial line: `aids` is not a list of text, so it is read as none",
+        "1 trial line: `drawn` is not an object naming each drawn setting's source, so it is read as none",
+        "1 trial line: `fluid_ml` is not a finite number or null, so it is read as none",
+        "1 trial line: `planned` is not a planned place, so it is read as none",
+        "2 trial lines: `shown` is not true, false or null, so it is read as none",
+    )
diff --git a/tests/nwb/test_trial_record.py b/tests/nwb/test_trial_record.py
new file mode 100644
index 0000000..c715264
--- /dev/null
+++ b/tests/nwb/test_trial_record.py
@@ -0,0 +1,74 @@
+"""What the rig's record says of each trial beyond its condition and settings,
+as trials-table columns (design spec `2026-11-07-rig-record-fields-design.md`
+section 3)."""
+
+from __future__ import annotations
+
+import math
+
+from wl_preproc.events.rigtrials import Planned, RigRecord, RigTrial
+
+
+def _rig(number, index, run=0, **fields):
+    return RigTrial(number=number, index=index, outcome="correct", block="main", condition="c", params={}, run=run,
+                    **fields, carries=frozenset(fields))
+
+
+def _trials(*numbers):
+    return [{"trial_id": number} for number in numbers]
+
+
+def test_a_column_is_written_only_where_some_line_carries_its_field():
+    """A record from before wl-xcon's engine build C2a gains no column; a
+    trial with no line has -1, '' or NaN."""
+    from wl_preproc.nwb.trial_record import record_columns
+
+    assert record_columns(_trials(1, 2), {1: _rig(1, 0)}, {}) == ({}, [])
+    matched = {1: _rig(1, 0, counting="spent", shown=False, aids=("drumming", "clear"), fluid_ml=0.1)}
+    columns, notes = record_columns(_trials(1, 2), matched, {})
+    assert sorted(columns) == ["aids", "counting", "fluid_ml", "shown"]
+    assert (columns["counting"], columns["shown"], columns["aids"]) == (["spent", ""], [0, -1], ["clear,drumming", ""])
+    assert columns["fluid_ml"][0] == 0.1 and math.isnan(columns["fluid_ml"][1])
+    assert notes == []
+
+
+def test_a_repeat_names_the_trial_it_shows_again_by_its_trial_id():
+    """Section 2's ruling: the rig names it by its index in the run; the
+    column gives that trial's trial_id. One its run's lines do not name is
+    -1, and the notes say so."""
+    from wl_preproc.nwb.trial_record import places, record_columns
+
+    lines = [_rig(11, 0, run=1), _rig(12, 1, run=1, repeat_of=0), _rig(13, 2, run=1, repeat_of=9),
+             _rig(14, 0, run=2, repeat_of=None)]
+    record = RigRecord(trials=tuple(lines), problems=())
+    columns, notes = record_columns(_trials(11, 12, 13, 14), {line.number: line for line in lines}, places(record))
+    assert columns["repeat_of"] == [-1, 11, -1, -1]
+    assert notes == ["1 trial(s) show again a trial no line of their run names, so their repeat_of is -1 (trial 13)"]
+
+
+def test_the_planned_place_is_six_columns_where_a_designed_block_ran():
+    """Written only when some trial's planned place is not null: a
+    sequence-balanced block's cycle, position and lead-in, a priming block's
+    run, switch and value."""
+    from wl_preproc.nwb.trial_record import record_columns
+
+    plain = {1: _rig(1, 0, planned=None)}
+    assert "planned_cycle" not in record_columns(_trials(1), plain, {})[0]
+    matched = {1: _rig(1, 0, planned=Planned(cycle=0, position=0, lead_in=True)),
+               2: _rig(2, 1, planned=Planned(run=3, switch=False, value=270.0)), 3: _rig(3, 2, planned=None)}
+    columns, _notes = record_columns(_trials(1, 2, 3), matched, {})
+    assert columns["planned_cycle"] == [0, -1, -1] and columns["planned_position"] == [0, -1, -1]
+    assert columns["planned_lead_in"] == [1, -1, -1]
+    assert (columns["planned_run"], columns["planned_switch"], columns["planned_value"]) == (
+        [-1, 3, -1], [-1, 0, -1], ["", "270.0", ""])
+
+
+def test_lists_are_sorted_and_comma_joined_and_draws_are_json_text():
+    from wl_preproc.nwb.trial_record import record_columns
+
+    matched = {1: _rig(1, 0, overridden=("b", "a"), live=("hold", "contrast"), eased=(),
+                       drawn={"jitter": 7, "hold": "task"}, structure="default", previous=None, drumming=None)}
+    columns, _notes = record_columns(_trials(1), matched, {})
+    assert (columns["overridden"], columns["live"], columns["eased"]) == (["a,b"], ["contrast,hold"], [""])
+    assert columns["drawn"] == ['{"hold": "task", "jitter": 7}']
+    assert (columns["structure"], columns["previous"], columns["drumming"]) == (["default"], [""], [""])
diff --git a/tests/nwb/test_writers.py b/tests/nwb/test_writers.py
index 7d08147..fb0f649 100644
--- a/tests/nwb/test_writers.py
+++ b/tests/nwb/test_writers.py
@@ -104,6 +104,23 @@ def test_the_trials_carry_their_condition_and_the_settings_that_varied(tmp_path)
         assert frame["setting_xy"].tolist() == ["[0, 5]", ""]
 
 
+def test_the_trials_carry_what_the_rigs_record_says_of_each_trial(tmp_path):
+    """Design spec `2026-11-07-rig-record-fields-design.md` section 3: one
+    column per field some trial's line carries, described."""
+    from wl_preproc.nwb.intervals import add_trials
+
+    trials = [{**TRIALS[0], "record": {"counting": "owed", "repeat_of": -1, "planned_lead_in": 1}},
+              {**TRIALS[0], "trial_id": 2, "start_s": 4.0, "stop_s": 5.0,
+               "record": {"counting": "", "repeat_of": 1, "planned_lead_in": -1}}]
+    _path, io, nwb = _write(tmp_path, lambda nwb: add_trials(nwb, trials, ["ohdpi"]))
+    with io:
+        frame = nwb.trials.to_dataframe()
+        assert frame["counting"].tolist() == ["owed", ""]
+        assert frame["repeat_of"].tolist() == [-1, 1]
+        assert frame["planned_lead_in"].tolist() == [1, -1]
+        assert "never its answer" in nwb.trials["counting"].description
+
+
 def test_the_conditions_table_and_none_when_no_condition_is_known(tmp_path):
     from wl_preproc.nwb.intervals import add_conditions
 
PATCH
```

- [ ] **Step 2: Run them and see them fail.** The run stops at collection: `tests/nwb/test_trial_record.py` imports `Planned`, which Step 3 adds.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/events/test_rigtrials.py tests/nwb/test_trial_record.py tests/nwb/test_writers.py::test_the_trials_carry_what_the_rigs_record_says_of_each_trial -q -p no:cacheprovider
```

Expected: `1 error`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/events/rigfields.py b/wl_preproc/events/rigfields.py
new file mode 100644
index 0000000..74140c1
--- /dev/null
+++ b/wl_preproc/events/rigfields.py
@@ -0,0 +1,117 @@
+"""The fields of wl-xcon's record that no row must carry (design spec
+`2026-11-07-rig-record-fields-design.md` section 2): what its engine builds
+C2a, C2b and C3 and its XC-399 added to a trial line and a run's rows.
+
+**Every one is optional.** A row written before the build that added it has
+none, and reads as it did. **Read, never repaired:** a value of the wrong type
+is read as none and counted by field, so a reader says once per file which
+field it could not read and on how many rows. It never drops the row: that is
+for the row's own required fields to decide."""
+
+from __future__ import annotations
+
+import collections
+import dataclasses
+import math
+from collections.abc import Callable
+
+
+@dataclasses.dataclass(frozen=True, slots=True)
+class Kind:
+    """What a field holds: `read` gives `(value, readable)`, the value none
+    (or empty) when unreadable; `says` names the kind in a problem."""
+
+    read: Callable[[object], tuple[object, bool]]
+    says: str
+
+
+def _integer(value: object) -> bool:
+    return isinstance(value, int) and not isinstance(value, bool)
+
+
+def _boolean(value):
+    return (value, True) if value is None or isinstance(value, bool) else (None, False)
+
+
+def _whole(value):
+    return (value, True) if value is None or _integer(value) else (None, False)
+
+
+def _number(value):
+    if value is None:
+        return None, True
+    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
+        return None, False
+    return float(value), True
+
+
+def _text(value):
+    return (value, True) if value is None or isinstance(value, str) else (None, False)
+
+
+def _texts(value):
+    if value is None:
+        return (), True
+    if isinstance(value, list) and all(isinstance(item, str) for item in value):
+        return tuple(value), True
+    return (), False
+
+
+def _objects(value):
+    if value is None:
+        return None, True
+    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
+        return value, True
+    return None, False
+
+
+def _source(value: object) -> bool:
+    return isinstance(value, str) or _integer(value)
+
+
+def _drawn(value):
+    if value is None:
+        return None, True
+    if isinstance(value, dict) and all(isinstance(name, str) and _source(source) for name, source in value.items()):
+        return dict(value), True
+    return None, False
+
+
+BOOLEAN = Kind(_boolean, "true, false or null")
+WHOLE = Kind(_whole, "a whole number or null")
+NUMBER = Kind(_number, "a finite number or null")
+TEXT = Kind(_text, "text or null")
+TEXTS = Kind(_texts, "a list of text")
+OBJECTS = Kind(_objects, "a list of objects or null")
+DRAWN = Kind(_drawn, "an object naming each drawn setting's source")
+ANY = Kind(lambda value: (value, True), "a value")
+
+
+class Fields:
+    """One file's optional fields, read row by row, and what could not be
+    read. `rows` names what a row is, singular and plural."""
+
+    def __init__(self, rows: tuple[str, str]) -> None:
+        self._rows = rows
+        self._wrong: collections.Counter[tuple[str, str]] = collections.Counter()
+
+    def read(self, row: dict, name: str, kind: Kind, *, field: str | None = None):
+        """`row[name]` as `kind`; its none (or empty) value when the row does
+        not carry it, or carries it unreadable. `field` names it in a problem
+        when it sits inside another (`block_ends[].ended`)."""
+        value, readable = kind.read(row.get(name))
+        if not readable:
+            self.wrong(field or name, kind.says)
+        return value
+
+    def wrong(self, field: str, says: str) -> None:
+        """One row whose `field` is not `says`."""
+        self._wrong[(field, says)] += 1
+
+    def problems(self) -> list[str]:
+        """One sentence per field that some row carried unreadable."""
+        out = []
+        for (field, says), n in sorted(self._wrong.items()):
+            what = self._rows[0] if n == 1 else self._rows[1]
+            out.append(f"{n} {what}: `{field}` is not {says}, so it is read as none")
+        return out
diff --git a/wl_preproc/events/rigtrials.py b/wl_preproc/events/rigtrials.py
index 38d463b..769eab9 100644
--- a/wl_preproc/events/rigtrials.py
+++ b/wl_preproc/events/rigtrials.py
@@ -23,6 +23,21 @@ counts its `index` from 0; since its XC-155 each line also carries
 lines name no run, is keyed by `index`, which then counted across the
 session. A line that names a run but carries no `trial_number` has no key
 across the session, so it is reported and left out.
+
+**And what the line says beyond its six** (design spec
+`2026-11-07-rig-record-fields-design.md` sections 2 and 3): since wl-xcon's
+engine build C2a, what the trial did to its condition's count (`counting`,
+never its answer, which `outcome` is), whether its stimulus appeared, which
+trial it shows again and whether straight after it, the condition before it,
+whether it is a drum's repeat and drumming was on, and the training aids on;
+since C2b, the trial structure it played and the values its condition set that
+a live edit overrode; since C3, where each drawn setting's range came from, the
+settings a person held, those whose live change kept it out of moving up, and
+a designed block's planned place; and the fluid it commanded. Each is optional
+(`events/rigfields.py`): a line without it reads as before, and one it cannot
+read is read as none, said once per field, never left out for it. `carries`
+names the ones the line has, so a column is published only where some line
+carries its field.
 """
 
 from __future__ import annotations
@@ -32,11 +47,51 @@ import json
 from pathlib import Path
 
 from wl_preproc.contracts.paths import XCON_DIRNAME
+from wl_preproc.events.rigfields import BOOLEAN, DRAWN, NUMBER, TEXT, TEXTS, WHOLE, Fields, Kind
 
 RECORD_NAME = "trials.jsonl"
 _FIELDS = ("index", "subject", "outcome", "block", "condition", "params")
 
 
+@dataclasses.dataclass(frozen=True, slots=True)
+class Planned:
+    """A designed block's planned place for its trial (wl-xcon's engine build
+    C3): in a sequence-balanced block its cycle and position, from 0, and
+    whether it is in the cycle's lead-in; in a priming block its run of one
+    value, from 0, whether it is that run's first, and the value planned."""
+
+    cycle: int | None = None
+    position: int | None = None
+    lead_in: bool | None = None
+    run: int | None = None
+    switch: bool | None = None
+    value: object = None
+
+
+def _planned(value):
+    if value is None:
+        return None, True
+    if not isinstance(value, dict):
+        return None, False
+    wholes, flags = ("cycle", "position", "run"), ("lead_in", "switch")
+    if (any(not WHOLE.read(value.get(name))[1] for name in wholes)
+            or any(not BOOLEAN.read(value.get(name))[1] for name in flags)):
+        return None, False
+    return Planned(**{name: value.get(name) for name in (*wholes, *flags)}, value=value.get("value")), True
+
+
+PLANNED = Kind(_planned, "a planned place")
+
+# What a line may carry beyond its six, and how each is read.
+OPTIONAL: dict[str, Kind] = {
+    "counting": TEXT, "shown": BOOLEAN, "repeat_of": WHOLE, "at_once": BOOLEAN, "previous": TEXT,
+    "drummed": BOOLEAN, "drumming": TEXT, "aids": TEXTS,  # engine build C2a
+    "structure": TEXT, "overridden": TEXTS,  # C2b
+    "drawn": DRAWN, "live": TEXTS, "eased": TEXTS, "planned": PLANNED,  # C3
+    "fluid_ml": NUMBER,  # XC-026
+}
+
+
 @dataclasses.dataclass(frozen=True, slots=True)
 class RigTrial:
     number: int  # the stream's TRIAL_NUMBER: `trial_number`, or `index` before XC-155
@@ -45,6 +100,23 @@ class RigTrial:
     block: str
     condition: str
     params: dict
+    run: int | None = None  # wl-xcon's, from 0; None before its slice b3a-1
+    counting: str | None = None
+    shown: bool | None = None
+    repeat_of: int | None = None  # the `index`, in this run, of the trial it shows again
+    at_once: bool | None = None
+    previous: str | None = None
+    drummed: bool | None = None
+    drumming: str | None = None
+    aids: tuple[str, ...] = ()
+    structure: str | None = None
+    overridden: tuple[str, ...] = ()
+    drawn: dict | None = None
+    live: tuple[str, ...] = ()
+    eased: tuple[str, ...] = ()
+    planned: Planned | None = None
+    fluid_ml: float | None = None
+    carries: frozenset[str] = frozenset()
 
 
 @dataclasses.dataclass(frozen=True, slots=True)
@@ -60,6 +132,7 @@ def read_rig_trials(session_dir: Path, subject: str) -> RigRecord | None:
     if not path.is_file():
         return None
     trials, problems = [], []
+    fields = Fields(("trial line", "trial lines"))
     for number_of_line, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
         if not line.strip():
             continue
@@ -88,5 +161,8 @@ def read_rig_trials(session_dir: Path, subject: str) -> RigRecord | None:
         else:
             number = row["index"]
         trials.append(RigTrial(number=number, index=row["index"], outcome=str(row["outcome"]),
-                               block=str(row["block"]), condition=str(row["condition"]), params=row["params"]))
-    return RigRecord(trials=tuple(trials), problems=tuple(problems))
+                               block=str(row["block"]), condition=str(row["condition"]), params=row["params"],
+                               run=fields.read(row, "run", WHOLE),
+                               **{name: fields.read(row, name, kind) for name, kind in OPTIONAL.items()},
+                               carries=frozenset(name for name in OPTIONAL if name in row)))
+    return RigRecord(trials=tuple(trials), problems=(*problems, *fields.problems()))
diff --git a/wl_preproc/nwb/gather.py b/wl_preproc/nwb/gather.py
index 052a235..db76f67 100644
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -377,14 +377,19 @@ def _conditions(trials: list[dict], events: list[dict], runs: list[dict], sessio
 
     from wl_preproc.events.rigtrials import read_rig_trials
     from wl_preproc.nwb.conditions import block_conditions, join, stream_codes, trial_columns
+    from wl_preproc.nwb.trial_record import places, record_columns
 
     trials.sort(key=lambda trial: trial["start_s"])
-    matched, notes = join(trials, read_rig_trials(session_dir, subject))
+    record = read_rig_trials(session_dir, subject)
+    matched, notes = join(trials, record)
     codes = stream_codes(trials, events)
     names, settings = trial_columns(trials, matched, codes)
+    columns, record_notes = record_columns(trials, matched, places(record))
+    notes += record_notes
     for position, trial in enumerate(trials):
         trial["condition"] = names[position]
         trial["settings"] = {key: values[position] for key, values in settings.items()}
+        trial["record"] = {key: values[position] for key, values in columns.items()}
     for run in runs:
         inside = [trial for trial in trials if trial["run_number"] == run["run_number"]]
         outcomes = collections.Counter(trial["outcome"] or "unknown" for trial in inside)
diff --git a/wl_preproc/nwb/intervals.py b/wl_preproc/nwb/intervals.py
index 9722e25..d4aabcd 100644
--- a/wl_preproc/nwb/intervals.py
+++ b/wl_preproc/nwb/intervals.py
@@ -92,9 +92,21 @@ def _setting_columns(rows: list[dict]) -> list:
             for key in keys]
 
 
+def _record_columns(rows: list[dict]) -> list:
+    """One column per field of the rig's line that some trial's line carries
+    (design spec `2026-11-07-rig-record-fields-design.md` section 3), in
+    `nwb/trial_record.py`'s order."""
+    from wl_preproc.nwb.trial_record import COLUMNS, PLANNED_COLUMNS
+
+    present = set().union(*(row.get("record", {}).keys() for row in rows)) if rows else set()
+    return [column(name, description, [row["record"][name] for row in rows])
+            for name, description in {**COLUMNS, **PLANNED_COLUMNS}.items() if name in present]
+
+
 def add_trials(nwb: NWBFile, trials: list[dict], systems: list[str]) -> None:
     """`/intervals/trials`: trial id, outcome, block, condition, the
-    settings that varied, and per-system coverage."""
+    settings that varied, what the rig's record says of each trial beyond
+    them, and per-system coverage."""
     rows = sorted(trials, key=lambda row: row["start_s"])
     nwb.trials = TimeIntervals(
         name="trials",
@@ -112,6 +124,7 @@ def add_trials(nwb: NWBFile, trials: list[dict], systems: list[str]) -> None:
                                  "(xcon/trials.jsonl), else the CONDITION number sent inside it, else ''."),
                    [r.get("condition", "") for r in rows]),
             *_setting_columns(rows),
+            *_record_columns(rows),
             *_coverage_columns(rows, systems),
         ],
     )
diff --git a/wl_preproc/nwb/trial_record.py b/wl_preproc/nwb/trial_record.py
new file mode 100644
index 0000000..5e8b86d
--- /dev/null
+++ b/wl_preproc/nwb/trial_record.py
@@ -0,0 +1,126 @@
+"""What the rig's record says of each trial beyond its condition and settings,
+as trials-table columns (design spec `2026-11-07-rig-record-fields-design.md`
+section 3).
+
+Pure functions over plain data, beside `nwb/conditions.py`: the trials are this
+file's own, joined to the rig's lines by trial number. **A column is written
+only where some line carries its field**, as a setting column is written only
+where it varies, so a record from before wl-xcon's engine build C2a gains none;
+the six planned-place columns only where a designed block ran. A trial with no
+line has -1, NaN or '' in each, as each description says.
+
+**A trial index is published as a trial id** (section 2): the rig names the
+trial a repeat shows again by its `index` in its run; the column gives that
+trial's `trial_id`, the stream's `TRIAL_NUMBER`, so a reader joins without
+the rig's counting."""
+
+from __future__ import annotations
+
+import json
+import math
+
+from wl_preproc.events.rigtrials import RigRecord, RigTrial
+
+_NONE = "-1 where the trial has no line in the rig's record or its line says none"
+
+# Each column a line's field gives, by that field's name, with its description.
+COLUMNS: dict[str, str] = {
+    "counting": ("What the trial did to its condition's count, as the rig's record (xcon/trials.jsonl) says: "
+                 "counted, spent, owed or unpaid. What it paid, never its answer, which outcome is ('' if none)."),
+    "shown": f"1 when the trial's stimulus appeared, 0 when not; {_NONE}.",
+    "repeat_of": f"The trial_id of the trial this one shows again; {_NONE}, or no line of its run is that trial.",
+    "at_once": f"1 when it came straight after the trial it shows again, 0 when not; {_NONE}.",
+    "previous": "The condition of the trial before it in its block, an aborted one included ('' if none).",
+    "drummed": f"1 when it is a drum's repeat, 0 when not; {_NONE}.",
+    "drumming": "Whether drumming was on as it was drawn: block or run ('' if not).",
+    "aids": "The training aids on as it was drawn, comma-joined: clear, drumming ('' if none).",
+    "structure": "The trial structure it played: its condition's or the task's default ('' if not recorded).",
+    "overridden": "The values its condition set that a person's live edit replaced, comma-joined ('' if none).",
+    "drawn": ("Each setting drawn at random for it, and where the range came from: task, block, condition or "
+              "the id of the live change that put it in force, as JSON text ('' if none)."),
+    "live": "The settings a person held at a value for it, comma-joined ('' if none).",
+    "eased": "The settings whose live change kept it out of moving up, comma-joined ('' if none).",
+    "fluid_ml": "The fluid it commanded, ml (NaN if not recorded).",
+}
+PLANNED_COLUMNS: dict[str, str] = {
+    "planned_cycle": f"A sequence-balanced block's planned cycle for it, from 0; {_NONE}.",
+    "planned_position": f"Its planned position in that cycle, from 0; {_NONE}.",
+    "planned_lead_in": f"1 when it is in its cycle's lead-in, which only precedes what follows, 0 when not; {_NONE}.",
+    "planned_run": f"A priming block's planned run of one value for it, from 0; {_NONE}.",
+    "planned_switch": f"1 when it is its run's first, 0 when not; {_NONE}.",
+    "planned_value": "The value that run plans, as JSON text ('' if none).",
+}
+
+
+def _flag(value: bool | None) -> int:
+    return -1 if value is None else int(value)
+
+
+def _whole(value: int | None) -> int:
+    return -1 if value is None else value
+
+
+def _joined(values: tuple[str, ...]) -> str:
+    return ",".join(sorted(values))
+
+
+def _text(value) -> str:
+    return "" if value is None else json.dumps(value, sort_keys=True)
+
+
+def places(record: RigRecord | None) -> dict[tuple[int | None, int], int]:
+    """`{(run, index): trial_number}` for every line of the record."""
+    return {} if record is None else {(trial.run, trial.index): trial.number for trial in record.trials}
+
+
+def _value(name: str, rig: RigTrial | None, place: dict, unplaced: list[int]):
+    if name == "fluid_ml":
+        return math.nan if rig is None or rig.fluid_ml is None else rig.fluid_ml
+    if rig is None:
+        return -1 if name in ("shown", "repeat_of", "at_once", "drummed") else ""
+    if name == "repeat_of":
+        if rig.repeat_of is None:
+            return -1
+        found = place.get((rig.run, rig.repeat_of))
+        if found is None:
+            unplaced.append(rig.number)
+            return -1
+        return found
+    if name in ("shown", "at_once", "drummed"):
+        return _flag(getattr(rig, name))
+    if name in ("aids", "overridden", "live", "eased"):
+        return _joined(getattr(rig, name))
+    if name == "drawn":
+        return _text(rig.drawn)
+    value = getattr(rig, name)
+    return "" if value is None else value
+
+
+def _planned(name: str, rig: RigTrial | None):
+    planned = None if rig is None else rig.planned
+    if name == "planned_value":
+        return "" if planned is None or planned.value is None else _text(planned.value)
+    field = name.removeprefix("planned_")
+    value = None if planned is None else getattr(planned, field)
+    return _flag(value) if field in ("lead_in", "switch") else _whole(value)
+
+
+def record_columns(trials: list[dict], matched: dict[int, RigTrial],
+                   place: dict[tuple[int | None, int], int]) -> tuple[dict[str, list], list[str]]:
+    """One list per column some matched line carries, in `trials`' order,
+    and the notes on what could not be placed."""
+    rigs = [matched.get(trial["trial_id"]) for trial in trials]
+    carried = set().union(*(rig.carries for rig in rigs if rig is not None))
+    columns, unplaced = {}, []
+    for name in COLUMNS:
+        if name in carried:
+            columns[name] = [_value(name, rig, place, unplaced) for rig in rigs]
+    if any(rig is not None and rig.planned is not None for rig in rigs):
+        for name in PLANNED_COLUMNS:
+            columns[name] = [_planned(name, rig) for rig in rigs]
+    notes = []
+    if unplaced:
+        notes.append(f"{len(unplaced)} trial(s) show again a trial no line of their run names, so their repeat_of "
+                     f"is -1 (trial {', '.join(str(number) for number in unplaced[:5])}"
+                     f"{', ...' if len(unplaced) > 5 else ''})")
+    return columns, notes
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/events/test_rigtrials.py tests/nwb/test_trial_record.py tests/nwb/test_writers.py::test_the_trials_carry_what_the_rigs_record_says_of_each_trial -q -p no:cacheprovider
```

Expected: `13 passed`

- [ ] **Step 5: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/events tests/nwb tests/schema/test_nwb_build.py tests/schema/test_nwb_rig_record.py tests/schema/test_main_sequence_populate.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `246 passed`

- [ ] **Step 6: Commit.**

```bash
git add -A tests wl_preproc docs
git commit -F - <<'MSG'
feat(nwb): each trial says what the rig's record says of it -- what it paid toward its condition's count (never its answer), whether its stimulus appeared, the trial it shows again by its trial_id, its drumming and aids, its structure, the values a person overrode or held, its draws, the settings that kept it out of moving up, a designed block's planned place and its fluid, as trials-table columns written where some line carries them; each field optional, one it cannot read none and said once (design spec 2026-11-07-rig-record-fields-design.md, sections 2 and 3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

- [ ] **Step 7: The mutation checks,** on the commit. Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "a wrong value is kept as read",
  "wl_preproc/events/rigfields.py",
  "    return (value, True) if value is None or isinstance(value, bool) else (None, False)\n",
  "    return (value, True)\n",
  "tests/events/test_rigtrials.py tests/nwb/test_trial_record.py"
 ],
 [
  "a line carries every field",
  "wl_preproc/events/rigtrials.py",
  "carries=frozenset(name for name in OPTIONAL if name in row)",
  "carries=frozenset(OPTIONAL)",
  "tests/events/test_rigtrials.py tests/nwb/test_trial_record.py"
 ],
 [
  "a repeat keeps the rig's index",
  "wl_preproc/nwb/trial_record.py",
  "        found = place.get((rig.run, rig.repeat_of))\n",
  "        found = rig.repeat_of\n",
  "tests/events/test_rigtrials.py tests/nwb/test_trial_record.py"
 ],
 [
  "the planned place is always six columns",
  "wl_preproc/nwb/trial_record.py",
  "    if any(rig is not None and rig.planned is not None for rig in rigs):\n",
  "    if True:\n",
  "tests/events/test_rigtrials.py tests/nwb/test_trial_record.py"
 ],
 [
  "lists are joined unsorted",
  "wl_preproc/nwb/trial_record.py",
  "    return \",\".join(sorted(values))\n",
  "    return \",\".join(values)\n",
  "tests/events/test_rigtrials.py tests/nwb/test_trial_record.py"
 ],
 [
  "an unreadable field is not said",
  "wl_preproc/events/rigtrials.py",
  "problems=(*problems, *fields.problems())",
  "problems=tuple(problems)",
  "tests/events/test_rigtrials.py tests/nwb/test_trial_record.py"
 ]
]
```

Expected: 6 lines, each `CAUGHT`; `git status --short` shows no change.

---

### Task 2: Where each stimulus a trial showed was

§4: a trial line's `stimuli` (XC-399) read into `Stimulus` and `Place`, the polar angle from `direction` today or `polar` after wl-xcon's rename; the `rig_record` module's `stimuli` and `stimulus_objects` tables, made by `add_rig_tables` (the module made if the session has no record of its own); each position's angles checked against `eye/detect/geometry.py`, a disagreement a note. The stimulus's name is the column `stimulus` (amendment 2).

**Files:**
- Modify: `wl_preproc/events/rigfields.py` (`MAPPING`), `wl_preproc/events/rigtrials.py` (`Place`, `Stimulus`, `_stimuli`)
- Create: `wl_preproc/nwb/stimuli.py` (`stimulus_rows`, `AGREE_DEG`)
- Modify: `wl_preproc/nwb/rig_record.py` (`DESCRIPTION`, `TABLES`, `_module`, `add_rig_tables`), `wl_preproc/nwb/build.py`, `wl_preproc/nwb/gather.py` (`Gathered.rig_tables`, `_conditions`)
- Test: `tests/events/test_rigtrials.py`, `tests/nwb/test_stimuli.py` (new), `tests/nwb/test_writers.py`

**Interfaces:**
- Consumes: Task 1's `Fields`, the kinds, `RigTrial`.
- Produces: `rigtrials.Place(x, y, eccentricity, polar)`, `rigtrials.Stimulus`, `RigTrial.stimuli`;
  `stimuli.stimulus_rows(trials, matched) -> (entries, objects, notes)`; `rig_record.TABLES`,
  `rig_record.add_rig_tables(nwb, tables)`; `Gathered.rig_tables: dict[str, list[dict]]`; `_conditions` returns
  `(conditions, notes, tables)`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/events/test_rigtrials.py b/tests/events/test_rigtrials.py
index 7b6e4d2..129f895 100644
--- a/tests/events/test_rigtrials.py
+++ b/tests/events/test_rigtrials.py
@@ -148,3 +148,55 @@ def test_a_field_it_cannot_read_is_none_said_once_and_its_line_kept(tmp_path):
         "1 trial line: `planned` is not a planned place, so it is read as none",
         "2 trial lines: `shown` is not true, false or null, so it is read as none",
     )
+
+
+_FIX = {"name": "fix", "kind": "show", "frame": 1, "off": 61, "changed": None, "drawn": True, "unresolved": None,
+        "eccentricity": 0.0, "direction": None, "x": 0.0, "y": 0.0}
+_TARGET = {"name": "target", "kind": "update", "frame": 97, "off": None, "changed": ["opacity"], "drawn": True,
+           "unresolved": None, "eccentricity": 27.23631347517073, "direction": 45.0, "x": 20.0, "y": 20.0}
+
+
+def test_each_stimulus_a_trial_showed_is_read_with_where_and_when(tmp_path):
+    """wl-xcon's XC-399 (design spec `2026-11-07-rig-record-fields-design.md`
+    section 4): a `Show` or `Update` per entry, its frames, its cyclopean
+    position and true angles; an array's objects and target; a per-eye
+    stimulus's two positions. The polar angle is read from `direction` today
+    and from `polar` once wl-xcon renames it."""
+    from wl_preproc.events.rigtrials import Place, read_rig_trials
+
+    search = {**_FIX, "name": "search", "frame": 85, "off": None, "target": 1,
+              "objects": [{"eccentricity": 8.0, "direction": 0.0, "x": 8.0, "y": 0.0},
+                          {"eccentricity": 8.0, "polar": 180.0, "x": -8.0, "y": 0.0}]}
+    eyes = {**_FIX, "name": "dot", "eccentricity": None, "direction": None, "x": None, "y": None,
+            "left": {"eccentricity": 1.0, "direction": 0.0, "x": 1.0, "y": 0.0,
+                     "objects": [{"eccentricity": 2.0, "direction": 0.0, "x": 2.0, "y": 0.0}]},
+            "right": {"eccentricity": 1.0, "direction": 180.0, "x": -1.0, "y": 0.0}}
+    rows = [_line155(1, 0, 0, stimuli=None)]
+    rows[0] = json.dumps({**json.loads(rows[0]), "stimuli": [_FIX, _TARGET, search, {**_TARGET, "polar": 46.0}, eyes]})
+    (trial,) = read_rig_trials(_record(tmp_path, rows), "pico").trials
+    fix, target, array, renamed, dot = trial.stimuli
+    assert (fix.name, fix.kind, fix.frame, fix.off, fix.changed, fix.drawn) == ("fix", "show", 1, 61, (), True)
+    assert fix.at == Place(x=0.0, y=0.0, eccentricity=0.0, polar=None)
+    assert (target.kind, target.off, target.changed, target.at.polar) == ("update", None, ("opacity",), 45.0)
+    assert array.target == 1 and array.objects == (Place(8.0, 0.0, 8.0, 0.0), Place(-8.0, 0.0, 8.0, 180.0))
+    assert renamed.at.polar == 46.0
+    assert dot.at == Place() and dot.left["x"] == 1.0 and dot.right["direction"] == 180.0
+    assert dot.eye_objects == (("left", (Place(2.0, 0.0, 2.0, 0.0),)),)
+    assert "stimuli" in trial.carries
+
+
+def test_a_stimulus_field_it_cannot_read_is_none_and_said_by_its_path(tmp_path):
+    from wl_preproc.events.rigtrials import read_rig_trials
+
+    row = json.loads(_line155(1, 0, 0))
+    rows = [json.dumps({**row, "stimuli": [{**_FIX, "frame": "1", "x": "0"}]}),
+            json.dumps({**row, "trial_number": 2, "index": 1, "stimuli": "none"})]
+    record = read_rig_trials(_record(tmp_path, rows), "pico")
+    first, second = record.trials
+    assert (first.stimuli[0].frame, first.stimuli[0].at.x, first.stimuli[0].at.y) == (None, None, 0.0)
+    assert second.stimuli is None
+    assert record.problems == (
+        "1 trial line: `stimuli` is not a list of objects or null, so it is read as none",
+        "1 trial line: `stimuli[].frame` is not a whole number or null, so it is read as none",
+        "1 trial line: `stimuli[].x` is not a finite number or null, so it is read as none",
+    )
diff --git a/tests/nwb/test_stimuli.py b/tests/nwb/test_stimuli.py
new file mode 100644
index 0000000..4b71936
--- /dev/null
+++ b/tests/nwb/test_stimuli.py
@@ -0,0 +1,60 @@
+"""Where each stimulus a trial showed was, and when (design spec
+`2026-11-07-rig-record-fields-design.md` section 4)."""
+
+from __future__ import annotations
+
+import math
+
+from wl_preproc.events.rigtrials import Place, RigTrial, Stimulus
+
+
+def _stimulus(name="target", at=Place(20.0, 20.0, 27.23631347517073, 45.0), **fields):
+    return Stimulus(**{"name": name, "kind": "show", "frame": 85, "off": None, "changed": (), "drawn": True,
+                       "unresolved": None, "at": at, **fields})
+
+
+def _rig(number, stimuli):
+    return RigTrial(number=number, index=0, outcome="correct", block="b", condition="c", params={},
+                    stimuli=stimuli, carries=frozenset({"stimuli"}))
+
+
+def test_one_row_per_entry_and_one_per_array_object():
+    """Frames as frames (-1 for none), positions as numbers (NaN for none),
+    an array's objects in their own rows, a per-eye array's by eye."""
+    from wl_preproc.nwb.stimuli import stimulus_rows
+
+    fix = _stimulus("fix", at=Place(0.0, 0.0, 0.0, None), frame=1, off=61)
+    array = _stimulus("search", at=Place(0.0, 0.0, 0.0, None), target=1,
+                      objects=(Place(8.0, 0.0, 8.0, 0.0), Place(-8.0, 0.0, 8.0, 180.0)))
+    per_eye = _stimulus("dot", at=Place(), left={"x": 1.0}, eye_objects=(("left", (Place(2.0, 0.0, 2.0, 0.0),)),))
+    blank = _stimulus("catch", drawn=False, kind="update", changed=("at", "opacity"))
+    matched = {3: _rig(3, (fix, array)), 4: _rig(4, (per_eye, blank)), 5: _rig(5, None)}
+    entries, objects, notes = stimulus_rows([{"trial_id": number} for number in (3, 4, 5, 6)], matched)
+    assert [(row["trial_id"], row["entry"], row["stimulus"]) for row in entries] == [
+        (3, 0, "fix"), (3, 1, "search"), (4, 0, "dot"), (4, 1, "catch")]
+    assert (entries[0]["frame"], entries[0]["off"], entries[1]["off"]) == (1, 61, -1)
+    assert math.isnan(entries[0]["polar_deg"]) and entries[0]["eccentricity_deg"] == 0.0
+    assert (entries[1]["target"], entries[1]["n_objects"], entries[0]["target"]) == (1, 2, -1)
+    assert math.isnan(entries[2]["x_deg"]) and entries[2]["left"] == '{"x": 1.0}' and entries[2]["right"] == ""
+    assert (entries[3]["drawn"], entries[3]["kind"], entries[3]["changed"]) == (0, "update", "at,opacity")
+    assert [(row["trial_id"], row["entry"], row["eye"], row["object"], row["x_deg"]) for row in objects] == [
+        (3, 1, "", 0, 8.0), (3, 1, "", 1, -8.0), (4, 0, "left", 0, 2.0)]
+    assert notes == []
+
+
+def test_a_position_whose_angles_are_not_its_x_and_ys_is_noted_never_corrected():
+    """One formula, two implementations: the record's eccentricity and polar
+    angle are checked against `eye/detect/geometry.py`, past 1e-9 degrees. A
+    polar angle a hair under 360 agrees with 0."""
+    from wl_preproc.nwb.stimuli import stimulus_rows
+
+    right = _stimulus("right", at=Place(8.0, -1e-13, 8.0, 0.0))
+    wrong_size = _stimulus("off", at=Place(20.0, 20.0, 28.28, 45.0))
+    wrong_angle = _stimulus("turned", at=Place(20.0, 20.0, 27.23631347517073, 44.0))
+    centre = _stimulus("centre", at=Place(0.0, 0.0, 0.0, 90.0))
+    unplaced = _stimulus("nowhere", at=Place(None, None, None, None), unresolved="setting 'x' has no value")
+    matched = {1: _rig(1, (right, wrong_size)), 2: _rig(2, (wrong_angle, centre, unplaced))}
+    entries, _objects, notes = stimulus_rows([{"trial_id": 1}, {"trial_id": 2}], matched)
+    assert entries[1]["eccentricity_deg"] == 28.28
+    assert notes == ["3 stimulus entries give an eccentricity or polar angle that is not its x and y's, past 1e-09 "
+                     "degrees; published as written (trial 1 entry 1, trial 2 entry 0, trial 2 entry 1)"]
diff --git a/tests/nwb/test_writers.py b/tests/nwb/test_writers.py
index fb0f649..44df73a 100644
--- a/tests/nwb/test_writers.py
+++ b/tests/nwb/test_writers.py
@@ -121,6 +121,28 @@ def test_the_trials_carry_what_the_rigs_record_says_of_each_trial(tmp_path):
         assert "never its answer" in nwb.trials["counting"].description
 
 
+def test_the_stimulus_tables_join_the_rig_record_module_or_make_it(tmp_path):
+    """Design spec `2026-11-07-rig-record-fields-design.md` section 4: a
+    table with no row is not written; the module is made when the session
+    has no record of its own."""
+    from wl_preproc.nwb.rig_record import add_rig_tables
+
+    entry = {"trial_id": 1, "entry": 0, "stimulus": "fix", "kind": "show", "frame": 1, "off": -1, "changed": "",
+             "drawn": 1, "unresolved": "", "x_deg": 0.0, "y_deg": 0.0, "eccentricity_deg": 0.0,
+             "polar_deg": float("nan"), "target": -1, "n_objects": 0, "left": "", "right": ""}
+    _path, io, nwb = _write(tmp_path, lambda nwb: add_rig_tables(nwb, {"stimuli": [entry], "stimulus_objects": []}))
+    with io:
+        module = nwb.processing["rig_record"]
+        assert set(module.data_interfaces) == {"stimuli"}
+        frame = module["stimuli"].to_dataframe()
+        assert (frame["stimulus"].tolist(), frame["frame"].tolist(), frame["off"].tolist()) == (["fix"], [1], [-1])
+        assert "Frames, not times" in module["stimuli"].description
+    (tmp_path / "none").mkdir()
+    _path, io, nwb = _write(tmp_path / "none", lambda nwb: add_rig_tables(nwb, {}))
+    with io:
+        assert "rig_record" not in nwb.processing
+
+
 def test_the_conditions_table_and_none_when_no_condition_is_known(tmp_path):
     from wl_preproc.nwb.intervals import add_conditions
 
PATCH
```

- [ ] **Step 2: Run them and see them fail.** The run stops at collection: `tests/nwb/test_stimuli.py` imports `Place` and `Stimulus`, which Step 3 adds.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/events/test_rigtrials.py tests/nwb/test_stimuli.py tests/nwb/test_writers.py::test_the_stimulus_tables_join_the_rig_record_module_or_make_it -q -p no:cacheprovider
```

Expected: `1 error`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/events/rigfields.py b/wl_preproc/events/rigfields.py
index 74140c1..b06278b 100644
--- a/wl_preproc/events/rigfields.py
+++ b/wl_preproc/events/rigfields.py
@@ -57,6 +57,10 @@ def _texts(value):
     return (), False
 
 
+def _mapping(value):
+    return (value, True) if value is None or isinstance(value, dict) else (None, False)
+
+
 def _objects(value):
     if value is None:
         return None, True
@@ -83,6 +87,7 @@ NUMBER = Kind(_number, "a finite number or null")
 TEXT = Kind(_text, "text or null")
 TEXTS = Kind(_texts, "a list of text")
 OBJECTS = Kind(_objects, "a list of objects or null")
+MAPPING = Kind(_mapping, "an object or null")
 DRAWN = Kind(_drawn, "an object naming each drawn setting's source")
 ANY = Kind(lambda value: (value, True), "a value")
 
diff --git a/wl_preproc/events/rigtrials.py b/wl_preproc/events/rigtrials.py
index 769eab9..8edd364 100644
--- a/wl_preproc/events/rigtrials.py
+++ b/wl_preproc/events/rigtrials.py
@@ -33,7 +33,8 @@ whether it is a drum's repeat and drumming was on, and the training aids on;
 since C2b, the trial structure it played and the values its condition set that
 a live edit overrode; since C3, where each drawn setting's range came from, the
 settings a person held, those whose live change kept it out of moving up, and
-a designed block's planned place; and the fluid it commanded. Each is optional
+a designed block's planned place; since XC-399, where each stimulus it showed
+was and when (`stimuli`); and the fluid it commanded. Each is optional
 (`events/rigfields.py`): a line without it reads as before, and one it cannot
 read is read as none, said once per field, never left out for it. `carries`
 names the ones the line has, so a column is published only where some line
@@ -47,7 +48,18 @@ import json
 from pathlib import Path
 
 from wl_preproc.contracts.paths import XCON_DIRNAME
-from wl_preproc.events.rigfields import BOOLEAN, DRAWN, NUMBER, TEXT, TEXTS, WHOLE, Fields, Kind
+from wl_preproc.events.rigfields import (
+    BOOLEAN,
+    DRAWN,
+    MAPPING,
+    NUMBER,
+    OBJECTS,
+    TEXT,
+    TEXTS,
+    WHOLE,
+    Fields,
+    Kind,
+)
 
 RECORD_NAME = "trials.jsonl"
 _FIELDS = ("index", "subject", "outcome", "block", "condition", "params")
@@ -82,6 +94,82 @@ def _planned(value):
 
 PLANNED = Kind(_planned, "a planned place")
 
+
+@dataclasses.dataclass(frozen=True, slots=True)
+class Place:
+    """Where something was drawn, as the rig's record says (wl-xcon's XC-399):
+    its cyclopean per-axis position and its true eccentricity and polar angle,
+    in degrees, each None where the record says none."""
+
+    x: float | None = None
+    y: float | None = None
+    eccentricity: float | None = None
+    polar: float | None = None
+
+
+@dataclasses.dataclass(frozen=True, slots=True)
+class Stimulus:
+    """One entry of a trial line's `stimuli` (XC-399): a `Show` that put a
+    stimulus up, or an `Update` that changed it while it was up, on the screen
+    for at least one frame of the trial. `frame` and `off` are the trial's
+    frames, from 1; `off` None while it stayed on to the trial's end. An
+    array's `objects` and `target`; a per-eye stimulus's `left` and `right` as
+    written, and their objects, by eye, in `eye_objects`."""
+
+    name: str | None
+    kind: str | None
+    frame: int | None
+    off: int | None
+    changed: tuple[str, ...]
+    drawn: bool | None
+    unresolved: str | None
+    at: Place
+    target: int | None = None
+    objects: tuple[Place, ...] | None = None
+    left: dict | None = None
+    right: dict | None = None
+    eye_objects: tuple[tuple[str, tuple[Place, ...]], ...] = ()
+
+
+def _place(entry: dict, fields: Fields, field: str) -> Place:
+    """A position's four numbers. Its polar angle is wl-xcon's `direction`
+    until its rename to `polar` (the requester's decision of 2026-10-30, and
+    wl-xcon's PI's of 2026-11-04), so either is read."""
+    angle = "polar" if "polar" in entry else "direction"
+    return Place(**{name: fields.read(entry, key, NUMBER, field=f"{field}.{key}")
+                    for name, key in (("x", "x"), ("y", "y"), ("eccentricity", "eccentricity"), ("polar", angle))})
+
+
+def _places(entry: dict, fields: Fields, field: str) -> tuple[Place, ...] | None:
+    objects = fields.read(entry, "objects", OBJECTS, field=f"{field}.objects")
+    return None if objects is None else tuple(_place(item, fields, f"{field}.objects[]") for item in objects)
+
+
+def _stimuli(row: dict, fields: Fields) -> tuple[Stimulus, ...] | None:
+    entries = fields.read(row, "stimuli", OBJECTS)
+    if entries is None:
+        return None
+    out = []
+    for entry in entries:
+        eyes = {eye: fields.read(entry, eye, MAPPING, field=f"stimuli[].{eye}") for eye in ("left", "right")}
+        eye_objects = []
+        for eye, value in eyes.items():
+            objects = None if value is None else _places(value, fields, f"stimuli[].{eye}")
+            if objects is not None:
+                eye_objects.append((eye, objects))
+        out.append(Stimulus(
+            name=fields.read(entry, "name", TEXT, field="stimuli[].name"),
+            kind=fields.read(entry, "kind", TEXT, field="stimuli[].kind"),
+            frame=fields.read(entry, "frame", WHOLE, field="stimuli[].frame"),
+            off=fields.read(entry, "off", WHOLE, field="stimuli[].off"),
+            changed=fields.read(entry, "changed", TEXTS, field="stimuli[].changed"),
+            drawn=fields.read(entry, "drawn", BOOLEAN, field="stimuli[].drawn"),
+            unresolved=fields.read(entry, "unresolved", TEXT, field="stimuli[].unresolved"),
+            at=_place(entry, fields, "stimuli[]"), target=fields.read(entry, "target", WHOLE, field="stimuli[].target"),
+            objects=_places(entry, fields, "stimuli[]"), left=eyes["left"], right=eyes["right"],
+            eye_objects=tuple(eye_objects)))
+    return tuple(out)
+
 # What a line may carry beyond its six, and how each is read.
 OPTIONAL: dict[str, Kind] = {
     "counting": TEXT, "shown": BOOLEAN, "repeat_of": WHOLE, "at_once": BOOLEAN, "previous": TEXT,
@@ -116,6 +204,7 @@ class RigTrial:
     eased: tuple[str, ...] = ()
     planned: Planned | None = None
     fluid_ml: float | None = None
+    stimuli: tuple[Stimulus, ...] | None = None
     carries: frozenset[str] = frozenset()
 
 
@@ -164,5 +253,6 @@ def read_rig_trials(session_dir: Path, subject: str) -> RigRecord | None:
                                block=str(row["block"]), condition=str(row["condition"]), params=row["params"],
                                run=fields.read(row, "run", WHOLE),
                                **{name: fields.read(row, name, kind) for name, kind in OPTIONAL.items()},
-                               carries=frozenset(name for name in OPTIONAL if name in row)))
+                               stimuli=_stimuli(row, fields),
+                               carries=frozenset(name for name in (*OPTIONAL, "stimuli") if name in row)))
     return RigRecord(trials=tuple(trials), problems=(*problems, *fields.problems()))
diff --git a/wl_preproc/nwb/build.py b/wl_preproc/nwb/build.py
index dc8db99..857c989 100644
--- a/wl_preproc/nwb/build.py
+++ b/wl_preproc/nwb/build.py
@@ -14,7 +14,7 @@ from wl_preproc.nwb.eye_events import add_agreement, add_detections, add_sources
 from wl_preproc.nwb.gather import Refused, gather, readiness
 from wl_preproc.nwb.intervals import add_blocks, add_conditions, add_runs, add_task_events, add_trials
 from wl_preproc.nwb.probes import add_probes
-from wl_preproc.nwb.rig_record import add_rig_record
+from wl_preproc.nwb.rig_record import add_rig_record, add_rig_tables
 from wl_preproc.nwb.session import new_file
 from wl_preproc.nwb.timebase import add_timebase
 from wl_preproc.nwb.validate import inspect_file, n_critical
@@ -74,6 +74,7 @@ def build(activation_key: dict, nwb_root: Path) -> BuildResult:
     add_probes(nwb, data.probes)
     if data.rig_record is not None:
         add_rig_record(nwb, data.rig_record)
+    add_rig_tables(nwb, data.rig_tables)
     if data.eye is not None:
         add_eye_series(nwb, data.eye["times"], data.eye["gaze"], data.eye["pupil"])
         add_eye_tables(nwb, data.eye["calibration"], data.eye["validity"], data.eye["repairs"])
diff --git a/wl_preproc/nwb/gather.py b/wl_preproc/nwb/gather.py
index db76f67..997e2cf 100644
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -62,6 +62,10 @@ class Gathered:
     # `2026-10-14-session-kind-and-warnings-design.md` section 5); None for a
     # session wl-xcon did not run. See `_rig_record`.
     rig_record: dict | None = None
+    # The rows of the `rig_record` module's tables read from the rig's own
+    # files when the file is built, by table (`nwb/rig_record.py::TABLES`;
+    # design spec `2026-11-07-rig-record-fields-design.md` section 2).
+    rig_tables: dict[str, list[dict]] = dataclasses.field(default_factory=dict)
 
 
 def _run_set(activation_key: dict, session_key: dict) -> list[dict]:
@@ -367,16 +371,18 @@ def _outside_runs_notes(key: dict, session_key: dict, role: str) -> list[str]:
 
 
 def _conditions(trials: list[dict], events: list[dict], runs: list[dict], session_dir: Path,
-                subject: str) -> tuple[list[dict], list[str]]:
+                subject: str) -> tuple[list[dict], list[str], dict[str, list[dict]]]:
     """Each trial's condition and the settings that varied, and each run's
     conditions and trial counts, from the rig's own record joined by trial
     number (design spec `2026-09-29-nwb-publishing-design.md` sections 2.1
-    and 2.2). Returns the file's conditions and the notes on what did not
-    join."""
+    and 2.2). Returns the file's conditions, the notes on what did not
+    join, and the stimulus tables' rows (design spec
+    `2026-11-07-rig-record-fields-design.md` section 4)."""
     import collections
 
     from wl_preproc.events.rigtrials import read_rig_trials
     from wl_preproc.nwb.conditions import block_conditions, join, stream_codes, trial_columns
+    from wl_preproc.nwb.stimuli import stimulus_rows
     from wl_preproc.nwb.trial_record import places, record_columns
 
     trials.sort(key=lambda trial: trial["start_s"])
@@ -385,7 +391,8 @@ def _conditions(trials: list[dict], events: list[dict], runs: list[dict], sessio
     codes = stream_codes(trials, events)
     names, settings = trial_columns(trials, matched, codes)
     columns, record_notes = record_columns(trials, matched, places(record))
-    notes += record_notes
+    stimuli, stimulus_objects, stimulus_notes = stimulus_rows(trials, matched)
+    notes += record_notes + stimulus_notes
     for position, trial in enumerate(trials):
         trial["condition"] = names[position]
         trial["settings"] = {key: values[position] for key, values in settings.items()}
@@ -395,7 +402,8 @@ def _conditions(trials: list[dict], events: list[dict], runs: list[dict], sessio
         outcomes = collections.Counter(trial["outcome"] or "unknown" for trial in inside)
         run["trials"] = {"total": len(inside), "by_outcome": dict(sorted(outcomes.items()))}
         run["conditions"] = block_conditions(inside, matched, codes)
-    return block_conditions(trials, matched, codes), notes
+    return (block_conditions(trials, matched, codes), notes,
+            {"stimuli": stimuli, "stimulus_objects": stimulus_objects})
 
 
 def _timebase(session_key: dict, provenance: dict, clock: dict) -> dict:
@@ -719,7 +727,7 @@ def gather(activation_key: dict) -> Gathered:
     blocks = _blocks(run_rows, session_key)
     for block in blocks:
         block["n_trials"] = sum(1 for trial in trials if trial["block_id"] == block["block_number"])
-    conditions, condition_notes = _conditions(trials, events, run_out, session_dir, key["subject"])
+    conditions, condition_notes, rig_tables = _conditions(trials, events, run_out, session_dir, key["subject"])
     return Gathered(
         session={
             "identifier": identifier_for(key, session_id),
@@ -750,4 +758,5 @@ def gather(activation_key: dict) -> Gathered:
         probe_notes=probe_notes,
         trial_notes=_trial_notes(session_key, run_rows) + _outside_runs_notes(key, session_key, activation["role"]),
         rig_record=_rig_record(session_key, {row["run_number"] for row in run_rows}),
+        rig_tables=rig_tables,
     )
diff --git a/wl_preproc/nwb/rig_record.py b/wl_preproc/nwb/rig_record.py
index 6cb52a0..0958fda 100644
--- a/wl_preproc/nwb/rig_record.py
+++ b/wl_preproc/nwb/rig_record.py
@@ -17,6 +17,12 @@ change the rig applied, in `parameter_changes`, and each console control, in
 detail as JSON text. The file gives only the kind of actor, never a name,
 account, issuer or token (its decision 6).
 
+**Where each trial's stimuli were** (design spec
+`2026-11-07-rig-record-fields-design.md` section 4): `stimuli` and
+`stimulus_objects`, from the trial lines (`nwb/stimuli.py`), by
+`add_rig_tables`, which makes the module if the session has no record of its
+own.
+
 Written only for a session wl-xcon ran (`gather.py::_rig_record`)."""
 
 from __future__ import annotations
@@ -30,21 +36,75 @@ from pynwb.core import DynamicTable
 from wl_preproc.nwb.columns import column
 
 
+DESCRIPTION = ("What wl-xcon's record says the session was: the kind it was run as, the display calibration it "
+               "opened with, the warnings it accepted, its training records, the changes it applied and the controls "
+               "it was sent, and where each trial's stimuli were. Carried as the rig wrote them.")
+
+_POSITION = (
+    ("x_deg", "Its cyclopean per-axis position across, degrees, atan(X/D) (NaN if none). Through the stereoscope "
+              "no image is drawn there: each eye's is offset by half its disparity and the vergence offset."),
+    ("y_deg", "Its cyclopean per-axis position up, degrees, atan(Y/D) (NaN if none)."),
+    ("eccentricity_deg", "Its true angle from straight ahead, degrees of visual angle (NaN if none)."),
+    ("polar_deg", "Its polar angle, degrees counter-clockwise from rightward in [0, 360) (NaN at eccentricity 0, "
+                  "or if none)."),
+)
+# Each table `add_rig_tables` may add, with its description and its columns'.
+TABLES: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
+    "stimuli": (
+        "Each stimulus a trial put on the screen, or changed while it was up, for at least one frame, from the "
+        "rig's record (xcon/trials.jsonl, wl-xcon's XC-399), in the order they took effect. Frames, not times.",
+        (("trial_id", "The trial."),
+         ("entry", "Its order among the trial's entries, from 0."),
+         ("stimulus", "The stimulus's name in its task (a table's own name is `name`, so not that)."),
+         ("kind", "show: a Show put it up; update: an Update changed it while it was up."),
+         ("frame", "The trial's frame from which it was so, from 1 at the trial's first (-1 if not recorded)."),
+         ("off", "The first frame its presentation was no longer on the screen; -1 when it stayed on to the "
+                 "trial's end, or not recorded."),
+         ("changed", "The fields an update set, comma-joined ('' for a show)."),
+         ("drawn", "1 when its appearance is something to draw, 0 for a Blank (a catch trial's stimulus), -1 when "
+                   "its appearance is a setting with no value."),
+         ("unresolved", "Why a field could not be given ('' when everything resolved)."),
+         *_POSITION,
+         ("target", "An array's target, its index among its objects (-1 if none)."),
+         ("n_objects", "An array's objects, in stimulus_objects (0 for one stimulus)."),
+         ("left", "A per-eye stimulus's left-eye position, after the vergence offset, as JSON text ('' if none)."),
+         ("right", "A per-eye stimulus's right-eye position, likewise ('' if none)."))),
+    "stimulus_objects": (
+        "Each object of an array the stimuli table names, by its trial and entry.",
+        (("trial_id", "The trial."),
+         ("entry", "The array's entry in the stimuli table."),
+         ("eye", "'' for an array drawn once, left or right for a per-eye array's ring about that eye's position."),
+         ("object", "Its index among the array's objects, from 0."),
+         *_POSITION)),
+}
+
+
 def _time(value: float | None) -> float:
     return math.nan if value is None else value
 
 
+def _module(nwb: NWBFile):
+    """The file's `rig_record` module, made if it is not there yet."""
+    if "rig_record" in nwb.processing:
+        return nwb.processing["rig_record"]
+    return nwb.create_processing_module(name="rig_record", description=DESCRIPTION)
+
+
+def add_rig_tables(nwb: NWBFile, tables: dict[str, list[dict]]) -> None:
+    """Each table of `TABLES` that has a row."""
+    for name, (description, columns) in TABLES.items():
+        rows = tables.get(name) or []
+        if rows:
+            _module(nwb).add(DynamicTable(name=name, description=description, columns=[
+                column(field, text, [row[field] for row in rows]) for field, text in columns]))
+
+
 def add_rig_record(nwb: NWBFile, record: dict) -> None:
     """One `session` row, and an `accepted_warnings`, `parameter_changes` or
     `controls` table when the file has any such row."""
     calibration = record["calibration"] or {}
     standard = calibration.get("standard")
-    module = nwb.create_processing_module(
-        name="rig_record",
-        description=("What wl-xcon's record says the session was: the kind it was run as, the display calibration "
-                     "it opened with, the warnings it accepted, and its training records, the changes it applied "
-                     "and the controls it was sent. Carried as the rig wrote them."),
-    )
+    module = _module(nwb)
     module.add(DynamicTable(name="session", description="The session's kind and display calibration.", columns=[
         column("session_kind", "training, piloting or recording ('' if not recorded).",
                [record["session_kind"] or ""]),
diff --git a/wl_preproc/nwb/stimuli.py b/wl_preproc/nwb/stimuli.py
new file mode 100644
index 0000000..c3b5af3
--- /dev/null
+++ b/wl_preproc/nwb/stimuli.py
@@ -0,0 +1,84 @@
+"""Where each stimulus a trial showed was, and when, as rows of two tables
+(design spec `2026-11-07-rig-record-fields-design.md` section 4; wl-xcon's
+XC-399).
+
+Pure functions over plain data. One row per entry of a trial line's
+`stimuli`, and one per object of an array. **Frames, not times**: a frame is
+matched to the recording only through wl-xcon's screen log (its build F).
+**Checked, never corrected**: each position's eccentricity and polar angle are
+recomputed from its `x` and `y` by `eye/detect/geometry.py`, and a disagreement
+past `AGREE_DEG` is a note. One formula, two implementations, as the saccade
+fixtures pin for the detector."""
+
+from __future__ import annotations
+
+import json
+import math
+
+import numpy as np
+
+from wl_preproc.events.rigtrials import Place, RigTrial
+
+AGREE_DEG = 1e-9
+
+
+def _number(value: float | None) -> float:
+    return math.nan if value is None else value
+
+
+def _whole(value: int | None) -> int:
+    return -1 if value is None else value
+
+
+def _numbers(place: Place) -> dict:
+    return {"x_deg": _number(place.x), "y_deg": _number(place.y),
+            "eccentricity_deg": _number(place.eccentricity), "polar_deg": _number(place.polar)}
+
+
+def _disagrees(place: Place) -> bool:
+    """Whether the record's eccentricity or polar angle is not its `x` and
+    `y`'s, past `AGREE_DEG`; a position without both is not checked."""
+    from wl_preproc.eye.detect.geometry import eccentricity_polar_deg
+
+    if place.x is None or place.y is None:
+        return False
+    eccentricity, polar = (float(value) for value in eccentricity_polar_deg(np.array([place.x, place.y])))
+    if place.eccentricity is None or abs(place.eccentricity - eccentricity) > AGREE_DEG:
+        return True
+    if math.isnan(polar) or place.polar is None:
+        return math.isnan(polar) != (place.polar is None)
+    turn = abs(place.polar - polar) % 360.0
+    return min(turn, 360.0 - turn) > AGREE_DEG
+
+
+def stimulus_rows(trials: list[dict], matched: dict[int, RigTrial]) -> tuple[list[dict], list[dict], list[str]]:
+    """The `stimuli` and `stimulus_objects` rows of `trials`' matched lines,
+    in trial and entry order, and the notes on positions that disagree."""
+    entries, objects, disagreeing = [], [], []
+    for trial in trials:
+        rig = matched.get(trial["trial_id"])
+        if rig is None or rig.stimuli is None:
+            continue
+        for number, stimulus in enumerate(rig.stimuli):
+            key = {"trial_id": trial["trial_id"], "entry": number}
+            entries.append({
+                **key, "stimulus": stimulus.name or "", "kind": stimulus.kind or "", "frame": _whole(stimulus.frame),
+                "off": _whole(stimulus.off), "changed": ",".join(stimulus.changed),
+                "drawn": -1 if stimulus.drawn is None else int(stimulus.drawn), "unresolved": stimulus.unresolved or "",
+                **_numbers(stimulus.at), "target": _whole(stimulus.target),
+                "n_objects": len(stimulus.objects or ()),
+                "left": "" if stimulus.left is None else json.dumps(stimulus.left, sort_keys=True),
+                "right": "" if stimulus.right is None else json.dumps(stimulus.right, sort_keys=True)})
+            places = [stimulus.at]
+            for eye, these in (("", stimulus.objects or ()), *stimulus.eye_objects):
+                for index, place in enumerate(these):
+                    objects.append({**key, "eye": eye, "object": index, **_numbers(place)})
+                    places.append(place)
+            if any(_disagrees(place) for place in places):
+                disagreeing.append(f"trial {trial['trial_id']} entry {number}")
+    notes = []
+    if disagreeing:
+        notes.append(f"{len(disagreeing)} stimulus entr{'y' if len(disagreeing) == 1 else 'ies'} give an eccentricity "
+                     f"or polar angle that is not its x and y's, past {AGREE_DEG:g} degrees; published as written "
+                     f"({', '.join(disagreeing[:5])}{', ...' if len(disagreeing) > 5 else ''})")
+    return entries, objects, notes
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/events/test_rigtrials.py tests/nwb/test_stimuli.py tests/nwb/test_writers.py::test_the_stimulus_tables_join_the_rig_record_module_or_make_it -q -p no:cacheprovider
```

Expected: `13 passed`

- [ ] **Step 5: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/events tests/nwb tests/schema/test_nwb_build.py tests/schema/test_nwb_rig_record.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `237 passed`

- [ ] **Step 6: Commit.**

```bash
git add -A tests wl_preproc docs
git commit -F - <<'MSG'
feat(nwb): where each stimulus a trial showed was, and when -- the rig_record module's stimuli and stimulus_objects tables, frames not times, each position's eccentricity and polar angle checked against eye/detect/geometry.py and a disagreement noted, never corrected; the polar angle read from wl-xcon's direction today and polar after its rename (section 4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

- [ ] **Step 7: The mutation checks,** on the commit. Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "the renamed angle is not read",
  "wl_preproc/events/rigtrials.py",
  "    angle = \"polar\" if \"polar\" in entry else \"direction\"\n",
  "    angle = \"direction\"\n",
  "tests/events/test_rigtrials.py tests/nwb/test_stimuli.py tests/nwb/test_writers.py"
 ],
 [
  "a polar angle a hair under 360 disagrees with 0",
  "wl_preproc/nwb/stimuli.py",
  "    return min(turn, 360.0 - turn) > AGREE_DEG\n",
  "    return turn > AGREE_DEG\n",
  "tests/events/test_rigtrials.py tests/nwb/test_stimuli.py tests/nwb/test_writers.py"
 ],
 [
  "a per-eye array's objects are dropped",
  "wl_preproc/nwb/stimuli.py",
  "            for eye, these in ((\"\", stimulus.objects or ()), *stimulus.eye_objects):\n",
  "            for eye, these in ((\"\", stimulus.objects or ()),):\n",
  "tests/events/test_rigtrials.py tests/nwb/test_stimuli.py tests/nwb/test_writers.py"
 ],
 [
  "a table with no row is written",
  "wl_preproc/nwb/rig_record.py",
  "        rows = tables.get(name) or []\n        if rows:\n",
  "        rows = tables.get(name) or []\n        if name in tables:\n",
  "tests/events/test_rigtrials.py tests/nwb/test_stimuli.py tests/nwb/test_writers.py"
 ],
 [
  "a stimulus still on at the end reads 0",
  "wl_preproc/nwb/stimuli.py",
  "\"off\": _whole(stimulus.off)",
  "\"off\": stimulus.off or 0",
  "tests/events/test_rigtrials.py tests/nwb/test_stimuli.py tests/nwb/test_writers.py"
 ]
]
```

Expected: 5 lines, each `CAUGHT`; `git status --short` shows no change.

---

### Task 3: Each run says its plan and why it ended, each block how it ended

§5 and §8, amendments 1 and 4: the start row's plan, plan as declared, task hash, seed, draws, factorial and direction settings, and the end row's counts, end reason, block ends, shortfalls and condition counts, read by the same rule; published as runs- and blocks-table columns where some run carries them, and as the `unopened_blocks`, `shortfalls` and `condition_counts` tables; a disagreement with the stream a note; `Run.plan`, `Run.end_reason` and `RunBlock.ended` in the description, defaulted. `_conditions` takes the trial record, read once in `gather`.

**Files:**
- Modify: `wl_preproc/events/rigruns.py` (`BlockEnd`, `Shortfall`, `ConditionCount`, `RigRun`'s fields, the readers)
- Create: `wl_preproc/nwb/run_record.py` (`RUN_COLUMNS`, `BLOCK_COLUMNS`, `run_record`)
- Modify: `wl_preproc/nwb/gather.py`, `wl_preproc/nwb/intervals.py` (`add_runs`, `add_blocks`), `wl_preproc/nwb/rig_record.py` (`TABLES`), `wl_preproc/nwb/describe.py`, `wl_preproc/contracts/nwb_description.py`, `docs/schemas/nwb_description.json`
- Test: `tests/events/test_rigruns.py`, `tests/nwb/test_run_record.py` (new), `tests/nwb/test_writers.py`, `tests/nwb/test_describe.py`

**Interfaces:**
- Consumes: Task 1's `Fields` and kinds, `trial_record.places`; Task 2's `TABLES` and `rig_tables`.
- Produces: `rigruns.BlockEnd`, `Shortfall`, `ConditionCount`; `RigRun.index`, `.plan`, `.plan_form`,
  `.task_hash`, `.seed`, `.draws`, `.generated`, `.directions`, `.rig_trials`, `.presentations`, `.end_reason`,
  `.block_ends`, `.shortfalls`, `.conditions`, `.carries` (`index` and `carries` not compared);
  `run_record.run_record(runs, blocks, rig_runs, place) -> (tables, notes)`, setting each run's `plan`,
  `end_reason` and `record` and each block's `ended` and `record`; `_conditions(trials, events, runs, record)`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/events/test_rigruns.py b/tests/events/test_rigruns.py
index b14ad43..fcab52b 100644
--- a/tests/events/test_rigruns.py
+++ b/tests/events/test_rigruns.py
@@ -143,3 +143,67 @@ def test_a_runs_instants_are_its_rows_on_the_rigs_clock(tmp_path):
             _start(1, "rf_map", at="soon"), json.dumps({"event": "end", "run": 1, "stopped_because": "stopped by jw"})]
     runs = read_rig_runs(_record(tmp_path, rows), "pico").runs
     assert [(run.start_at, run.end_at) for run in runs] == [(1773478800.5, 1.0), (None, None)]
+
+
+def test_since_c2b_a_run_names_its_plan_and_how_each_block_ended(tmp_path):
+    """Design spec `2026-11-07-rig-record-fields-design.md` section 5, in
+    the shape `wl_xcon/taskd.py`'s two `run_row` calls write: the start row's
+    plan, plan as declared, task hash, seed, draws, factorial and direction
+    settings; the end row's counts, end reason, block ends, shortfalls and
+    per-condition counts."""
+    from wl_preproc.events.rigruns import BlockEnd, ConditionCount, Shortfall, read_rig_runs
+
+    start = _start(0, "visual_search", plan="training", plan_form={"class": "Plan", "fields": {}},
+                   versions={"task": "sha256:ab12", "allocation": "a.yaml"}, seed=1234,
+                   draws={"hold": {"class": "Uniform", "fields": {"high": 0.8, "low": 0.2, "repeat": "fresh"}}},
+                   generated=[], directions=["target_dir"])
+    end = json.dumps({**json.loads(_end(0, "its plan's last block ended", "completed")), "trials": 40,
+                      "presentations": 43, "end_reason": "plan_done",
+                      "block_ends": [{"block": "easy", "ended": "rule", "by": None, "after_trial": 19,
+                                      "block_in_session": 1, "position": 0, "passed_at": None,
+                                      "varied": {"hold": [{"by": "draw"}]}, "planned": None, "counts": {}},
+                                     {"block": "hard", "ended": "next", "by": {"kind": "member", "name": "jw"},
+                                      "after_trial": 42, "block_in_session": None, "position": 1,
+                                      "passed_at": None}],
+                      "shortfalls": [{"block": "easy", "condition": "c1", "trials": 2, "because": "cap",
+                                      "possible_avoidance": True, "after_trial": 17, "by": None}],
+                      "conditions": [{"block": "easy", "condition": "c1", "trials": 20, "presentations": 23,
+                                      "right": 15, "wrong": 5, "right_first": 12, "owed": 0, "repeats": 3,
+                                      "moved_past": 2, "possible_avoidance": True, "drummed": 3, "failing": 0}]})
+    (run,) = read_rig_runs(_record(tmp_path, [start, end]), "pico").runs
+    assert (run.plan, run.plan_form, run.task_hash, run.seed, run.directions) == (
+        "training", {"class": "Plan", "fields": {}}, "sha256:ab12", 1234, ("target_dir",))
+    assert run.draws["hold"]["class"] == "Uniform" and run.generated == []
+    assert (run.rig_trials, run.presentations, run.end_reason, run.index) == (40, 43, "plan_done", 0)
+    assert run.block_ends == (
+        BlockEnd("easy", "rule", None, 19, 1, 0, None, varied={"hold": [{"by": "draw"}]}, planned=None, counts={}),
+        BlockEnd("hard", "next", "member", 42, None, 1, None))
+    assert run.shortfalls == (Shortfall("easy", "c1", 2, "cap", True, 17, None),)
+    assert run.conditions == (ConditionCount("easy", "c1", 20, 23, 15, 5, 12, 0, 3, 2, True, 3, 0),)
+    assert run.carries >= {"plan", "task_hash", "seed", "rig_trials", "block_ends", "stop_kind"}
+
+
+def test_rows_before_c2a_carry_none_of_them(tmp_path):
+    from wl_preproc.events.rigruns import read_rig_runs
+
+    rows = [_start(0, "rf_map"), _end(0, "every block is finished", "completed")]
+    (run,) = read_rig_runs(_record(tmp_path, rows), "pico").runs
+    assert run.carries == {"stopped_because", "stop_kind"}
+    assert (run.plan, run.block_ends, run.shortfalls, run.rig_trials, run.task_hash) == (None, (), (), None, None)
+
+
+def test_a_run_field_it_cannot_read_is_none_and_said_by_its_path(tmp_path):
+    from wl_preproc.events.rigruns import read_rig_runs
+
+    end = json.dumps({**json.loads(_end(0, "done", "completed")), "presentations": "43",
+                      "block_ends": [{"block": "easy", "ended": 3, "after_trial": 1.5}]})
+    record = read_rig_runs(_record(tmp_path, [_start(0, "rf_map", seed="1234"), end]), "pico")
+    (run,) = record.runs
+    assert (run.seed, run.presentations, run.block_ends[0].ended, run.block_ends[0].after_trial) == (
+        None, None, None, None)
+    assert record.problems == (
+        "1 run row: `block_ends[].after_trial` is not a whole number or null, so it is read as none",
+        "1 run row: `block_ends[].ended` is not text or null, so it is read as none",
+        "1 run row: `presentations` is not a whole number or null, so it is read as none",
+        "1 run row: `seed` is not a whole number or null, so it is read as none",
+    )
diff --git a/tests/nwb/test_describe.py b/tests/nwb/test_describe.py
index ab1ca1d..8e0bf44 100644
--- a/tests/nwb/test_describe.py
+++ b/tests/nwb/test_describe.py
@@ -65,7 +65,8 @@ def test_the_description_of_a_file_without_eye_data():
     # The measured blocks inside the run (design spec
     # `2026-10-01-session-listing-and-run-requests-design.md` section 4).
     assert run["blocks"] == [{"block_number": 1, "block_in_run": 1, "block_type": "Bt1",
-                              "measured": {"start_s": 0.6, "stop_s": 29.4}, "closed": None, "trials": 1}]
+                              "measured": {"start_s": 0.6, "stop_s": 29.4}, "closed": None, "trials": 1,
+                              "ended": None}]
     assert out["quality"] == {"timing_tier": "B", "reference_source": "manifest",
                               "eye_usable_fraction": {"left": None, "right": None}}
     assert out["notes"] == ["no rig trial record (xcon/trials.jsonl)"]
@@ -199,3 +200,21 @@ def test_a_description_recorded_before_the_polar_series_reads_back_without_it():
     out = describe(_gathered(eye=eye), status="written", n_critical=0, checksums=[], built_at=BUILT_AT)
     del out["data_types"]["eye"]["gaze_polar"]
     assert as_described_now(out)["data_types"]["eye"]["gaze_polar"] == []
+
+
+def test_a_run_says_its_plan_and_end_reason_and_a_block_how_it_ended():
+    """Design spec `2026-11-07-rig-record-fields-design.md` section 8: within
+    version 3, each defaulted to null where the rig's record says none."""
+    from wl_preproc.nwb.describe import describe
+
+    def described(run, block):
+        from wl_preproc.nwb.gather import Gathered
+
+        data = Gathered(session=SESSION, systems=["ohdpi"], runs=[run], blocks=[block], trials=[{"trial_id": 1}],
+                        events=[{}, {}], timebase={}, eye=None, conditions=[CONDITION])
+        return describe(data, status="written", n_critical=0, checksums=[CHECKSUM], built_at=BUILT_AT)["runs"][0]
+
+    run = described({**RUN, "plan": "training", "end_reason": "plan_done"}, {**BLOCK, "ended": "rule"})
+    assert (run["plan"], run["end_reason"], run["blocks"][0]["ended"]) == ("training", "plan_done", "rule")
+    run = described(RUN, BLOCK)
+    assert (run["plan"], run["end_reason"], run["blocks"][0]["ended"]) == (None, None, None)
diff --git a/tests/nwb/test_run_record.py b/tests/nwb/test_run_record.py
new file mode 100644
index 0000000..2f2f44f
--- /dev/null
+++ b/tests/nwb/test_run_record.py
@@ -0,0 +1,92 @@
+"""What the rig's record says of each run and block beyond its task and
+calibration (design spec `2026-11-07-rig-record-fields-design.md` section 5)."""
+
+from __future__ import annotations
+
+from wl_preproc.events.rigruns import BlockEnd, ConditionCount, RigRun, RigRuns, Shortfall
+
+
+def _run(number=1, index=0, **fields):
+    carries = {"stopped_because", "stop_kind", *fields}
+    if "rig_trials" in fields:
+        carries.add("rig_trials")
+    return RigRun(number, "visual_search", "done", "completed", index=index, **fields, carries=frozenset(carries))
+
+
+def _file(*numbers):
+    runs = [{"run_number": number} for number in numbers]
+    blocks = [{"block_number": 10 + number, "run_number": number} for number in numbers]
+    return runs, blocks
+
+
+def test_a_run_column_is_written_only_where_some_run_carries_its_field():
+    """A record from before C2b gains its runs' stop kind and sentence (long
+    in the listing, missing from the file) and nothing else."""
+    from wl_preproc.nwb.run_record import run_record
+
+    runs, blocks = _file(1, 2)
+    old = RigRuns(runs=(RigRun(1, "rf_map", "done", "completed", index=0,
+                               carries=frozenset({"stopped_because", "stop_kind"})),), problems=())
+    tables, notes = run_record(runs, blocks, old, {})
+    assert runs[0]["record"] == {"stop_kind": "completed", "stopped_because": "done"}
+    assert runs[1]["record"] == {"stop_kind": "", "stopped_because": ""}
+    assert (runs[0]["plan"], runs[0]["end_reason"], "record" in blocks[0]) == (None, None, False)
+    assert tables == {"unopened_blocks": [], "shortfalls": [], "condition_counts": []} and notes == []
+
+
+def test_a_planned_runs_columns_and_its_blocks_ends():
+    """Each block end joins the measured block whose number it names; a trial
+    index becomes the trial's trial_id through the run's own lines."""
+    from wl_preproc.nwb.run_record import run_record
+
+    runs, blocks = _file(1)
+    rig = _run(plan="training", end_reason="plan_done", rig_trials=2, presentations=3, task_hash="sha256:ab",
+               seed=7, plan_form={"class": "Plan"},
+               block_ends=(BlockEnd("easy", "rule", None, 2, 11, 0, 1, varied={"hold": []}),
+                           BlockEnd("hard", "next", "member", 2, None, 1, None)))
+    place = {(0, 0): 101, (0, 1): 102, (0, 2): 103}
+    tables, notes = run_record(runs, blocks, RigRuns(runs=(rig,), problems=()), place)
+    record = runs[0]["record"]
+    assert (record["plan"], record["end_reason"], record["rig_trials"], record["rig_presentations"]) == (
+        "training", "plan_done", 2, 3)
+    assert (record["task_hash"], record["seed"], record["plan_form"]) == ("sha256:ab", "7", '{"class": "Plan"}')
+    assert (runs[0]["plan"], runs[0]["end_reason"], blocks[0]["ended"]) == ("training", "plan_done", "rule")
+    assert blocks[0]["record"] == {"ended": "rule", "ended_by": "", "after_trial": 103, "position": 0,
+                                   "passed_at": 102, "varied": '{"hold": []}', "planned": "", "counts": ""}
+    assert tables["unopened_blocks"] == [{"run_number": 1, "block": "hard", "ended": "next", "ended_by": "member",
+                                          "after_trial": 103, "position": 1}]
+    assert notes == []
+
+
+def test_what_each_run_owed_is_two_tables():
+    from wl_preproc.nwb.run_record import run_record
+
+    runs, blocks = _file(1)
+    rig = _run(shortfalls=(Shortfall("easy", "c1", 2, "cap", True, 0, None),),
+               conditions=(ConditionCount("easy", "c1", 20, 23, 15, 5, 12, 0, 3, 2, None, 3, 0),))
+    tables, _notes = run_record(runs, blocks, RigRuns(runs=(rig,), problems=()), {(0, 0): 101})
+    assert tables["shortfalls"] == [{"run_number": 1, "block": "easy", "condition": "c1", "trials": 2,
+                                     "because": "cap", "possible_avoidance": 1, "after_trial": 101, "by_kind": ""}]
+    (count,) = tables["condition_counts"]
+    assert (count["trials"], count["presentations"], count["right_first"], count["possible_avoidance"]) == (
+        20, 23, 12, -1)
+
+
+def test_a_disagreement_with_the_stream_is_a_note_never_a_refusal():
+    """A block end naming a block the stream never opened in its run; a
+    measured block no end names; a presentation count that is not the run's
+    lines; a trial index no line of the run names."""
+    from wl_preproc.nwb.run_record import run_record
+
+    runs, blocks = _file(1)
+    blocks.append({"block_number": 12, "run_number": 1})
+    rig = _run(presentations=5, block_ends=(BlockEnd("easy", "rule", None, 9, 11, 0, None),
+                                            BlockEnd("hard", "time", None, None, 99, 1, None)))
+    tables, notes = run_record(runs, blocks, RigRuns(runs=(rig,), problems=()), {(0, 0): 101, (0, 1): 102})
+    assert blocks[0]["record"]["after_trial"] == -1 and blocks[1]["record"]["ended"] == ""
+    assert notes == [
+        "run 1: the rig's record ends block 99, which the recording never opened in it, so that end is on no block",
+        "run 1: block 12 has no end in the rig's record",
+        "run 1: the rig's end row counts 5 presentation(s) and its record holds 2 trial line(s)",
+        "1 trial index(es) in the rig's run rows name no trial line of their run, so they are -1",
+    ]
diff --git a/tests/nwb/test_writers.py b/tests/nwb/test_writers.py
index 44df73a..5355a6c 100644
--- a/tests/nwb/test_writers.py
+++ b/tests/nwb/test_writers.py
@@ -143,6 +143,28 @@ def test_the_stimulus_tables_join_the_rig_record_module_or_make_it(tmp_path):
         assert "rig_record" not in nwb.processing
 
 
+def test_runs_and_blocks_carry_what_the_rigs_record_says_of_them(tmp_path):
+    """Design spec `2026-11-07-rig-record-fields-design.md` section 5."""
+    from wl_preproc.nwb.intervals import add_blocks, add_runs
+
+    runs = [{**RUNS[0], "record": {"plan": "training", "end_reason": "plan_done", "seed": "7", "rig_trials": 4}},
+            {**RUNS[1], "record": {"plan": "", "end_reason": "", "seed": "", "rig_trials": -1}}]
+    blocks = [{**BLOCKS[0], "record": {"ended": "rule", "after_trial": 1}},
+              {**BLOCKS[1], "record": {"ended": "", "after_trial": -1}}]
+
+    def build(nwb):
+        add_runs(nwb, runs, ["ohdpi"])
+        add_blocks(nwb, blocks)
+
+    _path, io, nwb = _write(tmp_path, build)
+    with io:
+        frame = nwb.intervals["runs"].to_dataframe()
+        assert (frame["plan"].tolist(), frame["seed"].tolist(), frame["rig_trials"].tolist()) == (
+            ["training", ""], ["7", ""], [4, -1])
+        blocks_frame = nwb.intervals["blocks"].to_dataframe()
+        assert (blocks_frame["ended"].tolist(), blocks_frame["after_trial"].tolist()) == (["rule", ""], [1, -1])
+
+
 def test_the_conditions_table_and_none_when_no_condition_is_known(tmp_path):
     from wl_preproc.nwb.intervals import add_conditions
 
PATCH
```

- [ ] **Step 2: Run them and see them fail.** The run stops at collection: `tests/nwb/test_run_record.py` imports `BlockEnd` and its peers, which Step 3 adds. Step 1 also gives `tests/nwb/test_describe.py`'s expected blocks the `ended` the description writes, defaulted.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/events/test_rigruns.py tests/nwb/test_run_record.py tests/nwb/test_writers.py::test_runs_and_blocks_carry_what_the_rigs_record_says_of_them tests/nwb/test_describe.py -q -p no:cacheprovider
```

Expected: `1 error`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/docs/schemas/nwb_description.json b/docs/schemas/nwb_description.json
index 09d37c7..bedb97b 100644
--- a/docs/schemas/nwb_description.json
+++ b/docs/schemas/nwb_description.json
@@ -767,9 +767,33 @@
           "title": "Coverage",
           "type": "object"
         },
+        "end_reason": {
+          "anyOf": [
+            {
+              "type": "string"
+            },
+            {
+              "type": "null"
+            }
+          ],
+          "default": null,
+          "title": "End Reason"
+        },
         "measured": {
           "$ref": "#/$defs/Interval"
         },
+        "plan": {
+          "anyOf": [
+            {
+              "type": "string"
+            },
+            {
+              "type": "null"
+            }
+          ],
+          "default": null,
+          "title": "Plan"
+        },
         "run_number": {
           "title": "Run Number",
           "type": "integer"
@@ -840,6 +864,18 @@
           ],
           "title": "Closed"
         },
+        "ended": {
+          "anyOf": [
+            {
+              "type": "string"
+            },
+            {
+              "type": "null"
+            }
+          ],
+          "default": null,
+          "title": "Ended"
+        },
         "measured": {
           "$ref": "#/$defs/Interval"
         },
diff --git a/wl_preproc/contracts/nwb_description.py b/wl_preproc/contracts/nwb_description.py
index e111f04..b332755 100644
--- a/wl_preproc/contracts/nwb_description.py
+++ b/wl_preproc/contracts/nwb_description.py
@@ -13,7 +13,9 @@ display calibration and each run's, and the warnings it accepted (design spec
 many of wl-xcon's training records the file holds (design spec
 `2026-10-18-training-records-and-backup-clock-design.md` section 3.2), and
 2026-10-30 the eyes whose true eccentricity and polar angle it holds (design
-spec `2026-10-30-true-angle-saccades-design.md` section 8).
+spec `2026-10-30-true-angle-saccades-design.md` section 8), and 2026-11-07
+each run's plan and end reason and each block's end (design spec
+`2026-11-07-rig-record-fields-design.md` section 8).
 Version 2 gave `probes` their shape, each with both of its areas (design spec
 `2026-09-30-nwb-probes-design.md` section 3.2). Later pieces add groups and
 fields -- a processing summary with unit counts, the photodiode, video and
@@ -147,6 +149,10 @@ class RunBlock(_Frozen):
     measured: Interval
     closed: bool | None
     trials: int
+    # How it ended, from its run's end row in wl-xcon's record: rule, time,
+    # fall_back, next, back, run_time or run; null where not recorded (design
+    # spec `2026-11-07-rig-record-fields-design.md` section 8).
+    ended: str | None = None
 
 
 class Run(_Frozen):
@@ -166,6 +172,12 @@ class Run(_Frozen):
     # wl-xcon's record; null where it names none (design spec
     # `2026-10-14-session-kind-and-warnings-design.md` section 5).
     calibration: str | None = None
+    # The plan it played, by its name in its task, and why a completed run
+    # ended (plan_done, plan_time, stalled, cannot_draw), from wl-xcon's record;
+    # null where not recorded (design spec
+    # `2026-11-07-rig-record-fields-design.md` section 8).
+    plan: str | None = None
+    end_reason: str | None = None
 
 
 class RigCalibration(_Frozen):
diff --git a/wl_preproc/events/rigruns.py b/wl_preproc/events/rigruns.py
index e98429d..17b8c02 100644
--- a/wl_preproc/events/rigruns.py
+++ b/wl_preproc/events/rigruns.py
@@ -19,6 +19,17 @@ record is more than its runs (design spec
 way out, a fault included (`stop_kind` "fault"); only a killed process leaves
 none, and that run's stop reason is absent, not guessed.
 
+**And what the rows say beyond these** (design spec
+`2026-11-07-rig-record-fields-design.md` section 5): since wl-xcon's engine
+build C2b, the start row's plan and the plan as declared, and the end row's
+reason a completed run ended and how each block ended (`block_ends`); since
+C2a, the end row's trials and presentations, what each condition left unrun
+(`shortfalls`) and each condition's counts (`conditions`); since C3, the
+start row's declared draws, the conditions a factorial generated, and the
+settings that place a direction; and the task's content hash and the run's
+seed. Each is optional and read by `events/rigfields.py`'s rule: absent, it
+reads as before; unreadable, it is none, said once per field.
+
 **One record is one wl-xcon session, which is one animal** (wl-xcon's own
 vocabulary). Its rows carry no subject, so the session's `config.json` is
 checked instead: a record whose `config.json` names another subject is not
@@ -33,11 +44,116 @@ import math
 from pathlib import Path
 
 from wl_preproc.contracts.paths import XCON_DIRNAME
+from wl_preproc.events.rigfields import ANY, BOOLEAN, MAPPING, OBJECTS, TEXT, TEXTS, WHOLE, Fields
 
 RECORD_NAME = "runs.jsonl"
 CONFIG_NAME = "config.json"
 
 
+@dataclasses.dataclass(frozen=True, slots=True)
+class BlockEnd:
+    """How one block the run opened ended (wl-xcon's engine build C2b): its
+    type, `ended` (`rule`, `time`, `fall_back`, `next`, `back`, `run_time`,
+    `run`), who moved it by kind, the run's last trial before it ended (an
+    index in the run, from 0), its number in the stream (None for a block that
+    ran no trial), its place in the run's sequence, and, for a training
+    progression's top stage, the trial after which its rule was first met; and
+    since C3 what varied in it, its planned sequence and its counts, as
+    written."""
+
+    block: str | None
+    ended: str | None
+    by_kind: str | None
+    after_trial: int | None
+    block_in_session: int | None
+    position: int | None
+    passed_at: int | None
+    varied: object = None
+    planned: object = None
+    counts: object = None
+
+
+@dataclasses.dataclass(frozen=True, slots=True)
+class Shortfall:
+    """What a condition left unrun, and why (`cap`, `rig`, `cleared`, `time`,
+    `run_time`, `designed`); `after_trial` an index in the run."""
+
+    block: str | None
+    condition: str | None
+    trials: int | None
+    because: str | None
+    possible_avoidance: bool | None
+    after_trial: int | None
+    by_kind: str | None
+
+
+@dataclasses.dataclass(frozen=True, slots=True)
+class ConditionCount:
+    """One condition's counts in one block of the run, as the end row writes
+    them (wl-xcon's engine build C2a)."""
+
+    block: str | None
+    condition: str | None
+    trials: int | None
+    presentations: int | None
+    right: int | None
+    wrong: int | None
+    right_first: int | None
+    owed: int | None
+    repeats: int | None
+    moved_past: int | None
+    possible_avoidance: bool | None
+    drummed: int | None
+    failing: int | None
+
+
+_COUNTS = ("trials", "presentations", "right", "wrong", "right_first", "owed", "repeats", "moved_past", "drummed",
+           "failing")
+
+
+def _by_kind(entry: dict, fields: Fields, field: str) -> str | None:
+    by = fields.read(entry, "by", MAPPING, field=f"{field}.by")
+    return None if by is None else fields.read(by, "kind", TEXT, field=f"{field}.by.kind")
+
+
+def _entries(row: dict, name: str, fields: Fields) -> list[dict]:
+    return fields.read(row, name, OBJECTS) or []
+
+
+def _block_ends(row: dict, fields: Fields) -> tuple[BlockEnd, ...]:
+    field = "block_ends[]"
+    return tuple(BlockEnd(
+        block=fields.read(entry, "block", TEXT, field=f"{field}.block"),
+        ended=fields.read(entry, "ended", TEXT, field=f"{field}.ended"), by_kind=_by_kind(entry, fields, field),
+        **{name: fields.read(entry, name, WHOLE, field=f"{field}.{name}")
+           for name in ("after_trial", "block_in_session", "position", "passed_at")},
+        varied=entry.get("varied"), planned=entry.get("planned"), counts=entry.get("counts"))
+        for entry in _entries(row, "block_ends", fields))
+
+
+def _shortfalls(row: dict, fields: Fields) -> tuple[Shortfall, ...]:
+    field = "shortfalls[]"
+    return tuple(Shortfall(
+        block=fields.read(entry, "block", TEXT, field=f"{field}.block"),
+        condition=fields.read(entry, "condition", TEXT, field=f"{field}.condition"),
+        trials=fields.read(entry, "trials", WHOLE, field=f"{field}.trials"),
+        because=fields.read(entry, "because", TEXT, field=f"{field}.because"),
+        possible_avoidance=fields.read(entry, "possible_avoidance", BOOLEAN, field=f"{field}.possible_avoidance"),
+        after_trial=fields.read(entry, "after_trial", WHOLE, field=f"{field}.after_trial"),
+        by_kind=_by_kind(entry, fields, field))
+        for entry in _entries(row, "shortfalls", fields))
+
+
+def _conditions(row: dict, fields: Fields) -> tuple[ConditionCount, ...]:
+    field = "conditions[]"
+    return tuple(ConditionCount(
+        block=fields.read(entry, "block", TEXT, field=f"{field}.block"),
+        condition=fields.read(entry, "condition", TEXT, field=f"{field}.condition"),
+        possible_avoidance=fields.read(entry, "possible_avoidance", BOOLEAN, field=f"{field}.possible_avoidance"),
+        **{name: fields.read(entry, name, WHOLE, field=f"{field}.{name}") for name in _COUNTS})
+        for entry in _entries(row, "conditions", fields))
+
+
 @dataclasses.dataclass(frozen=True, slots=True)
 class RigRun:
     number: int  # the run's number in the session, as RUN_START strobes it
@@ -51,6 +167,28 @@ class RigRun:
     # section 3.3). None where a row has none.
     start_at: float | None = None
     end_at: float | None = None
+    # wl-xcon's `run`, from 0: how the rows name it, not what it is, so it is
+    # not compared, nor is `carries` below.
+    index: int | None = dataclasses.field(default=None, compare=False)
+    # The start row's, since C2b and C3 (`plan_form`, `draws` and `generated`
+    # as written).
+    plan: str | None = None
+    plan_form: object = None
+    task_hash: str | None = None  # `versions.task`, the task file's content hash
+    seed: int | None = None
+    draws: object = None
+    generated: object = None
+    directions: tuple[str, ...] = ()
+    # The end row's, since C2a and C2b.
+    rig_trials: int | None = None  # a trial and its drummed repeats one
+    presentations: int | None = None  # its trial lines
+    end_reason: str | None = None
+    block_ends: tuple[BlockEnd, ...] = ()
+    shortfalls: tuple[Shortfall, ...] = ()
+    conditions: tuple[ConditionCount, ...] = ()
+    # The optional fields its rows carry, by name; `stopped_because` and
+    # `stop_kind` when it has an end row.
+    carries: frozenset[str] = dataclasses.field(default=frozenset(), compare=False)
 
 
 @dataclasses.dataclass(frozen=True, slots=True)
@@ -95,6 +233,7 @@ def read_rig_runs(session_dir: Path, subject: str) -> RigRuns | None:
                                           "its runs are not this session's",))
     fields: dict[int, dict] = {}
     problems = []
+    optional = Fields(("run row", "run rows"))
     for number_of_line, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
         if not line.strip():
             continue
@@ -110,7 +249,7 @@ def read_rig_runs(session_dir: Path, subject: str) -> RigRuns | None:
         # sure to carry `run_in_session`, so the number is taken from it. A
         # second row of either kind for one run is reported, and the first
         # kept, as everywhere a number repeats here.
-        run = fields.setdefault(row["run"], {"number": row["run"] + 1})
+        run = fields.setdefault(row["run"], {"number": row["run"] + 1, "index": row["run"], "carries": set()})
         if row["event"] in run.setdefault("seen", set()):
             problems.append(f"{RECORD_NAME} line {number_of_line}: run {row['run']} has a second {row['event']} row; "
                             "the first is kept")
@@ -122,15 +261,30 @@ def read_rig_runs(session_dir: Path, subject: str) -> RigRuns | None:
             run["start_at"] = _instant(row.get("at"))
             if _integer(row.get("run_in_session")):
                 run["number"] = row["run_in_session"]
+            versions = optional.read(row, "versions", MAPPING) or {}
+            run.update(plan=optional.read(row, "plan", TEXT), plan_form=optional.read(row, "plan_form", ANY),
+                       task_hash=optional.read(versions, "task", TEXT, field="versions.task"),
+                       seed=optional.read(row, "seed", WHOLE), draws=optional.read(row, "draws", ANY),
+                       generated=optional.read(row, "generated", ANY),
+                       directions=optional.read(row, "directions", TEXTS))
+            run["carries"] |= {name for name in ("plan", "plan_form", "seed", "draws", "generated", "directions")
+                               if name in row} | ({"task_hash"} if "task" in versions else set())
         else:
             run["stopped_because"] = _text(row.get("stopped_because"))
             run["stop_kind"] = _text(row.get("stop_kind"))
             run["end_at"] = _instant(row.get("at"))
-    runs = tuple(sorted((RigRun(number=run["number"], task=run.get("task"),
-                                stopped_because=run.get("stopped_because"), stop_kind=run.get("stop_kind"),
-                                calibration=run.get("calibration"), start_at=run.get("start_at"),
-                                end_at=run.get("end_at"))
+            run.update(rig_trials=optional.read(row, "trials", WHOLE),
+                       presentations=optional.read(row, "presentations", WHOLE),
+                       end_reason=optional.read(row, "end_reason", TEXT), block_ends=_block_ends(row, optional),
+                       shortfalls=_shortfalls(row, optional), conditions=_conditions(row, optional))
+            run["carries"] |= {"stopped_because", "stop_kind"} | {
+                {"trials": "rig_trials"}.get(name, name)
+                for name in ("trials", "presentations", "end_reason", "block_ends", "shortfalls", "conditions")
+                if name in row}
+    runs = tuple(sorted((RigRun(**{"task": None, "stopped_because": None, "stop_kind": None,
+                                   **{name: value for name, value in run.items() if name not in ("seen", "carries")}},
+                                carries=frozenset(run["carries"]))
                          for run in fields.values()), key=lambda run: run.number))
-    return RigRuns(runs=runs, problems=tuple(problems),
+    return RigRuns(runs=runs, problems=(*problems, *optional.problems()),
                    run_numbers={index: run["number"] for index, run in sorted(fields.items())
                                 if "start" in run["seen"]})
diff --git a/wl_preproc/nwb/describe.py b/wl_preproc/nwb/describe.py
index d22eb3c..66f7564 100644
--- a/wl_preproc/nwb/describe.py
+++ b/wl_preproc/nwb/describe.py
@@ -132,8 +132,11 @@ def describe(data, *, status: str, n_critical: int, checksums: list[dict], built
                 "measured": {"start_s": block["start_s"], "stop_s": block["stop_s"]},
                 "closed": block["closed"],
                 "trials": block["n_trials"],
+                "ended": block.get("ended"),
             } for block in data.blocks if block["run_number"] == run["run_number"]],
             "calibration": run.get("calibration"),
+            "plan": run.get("plan"),
+            "end_reason": run.get("end_reason"),
         } for run in data.runs],
         "quality": {
             "timing_tier": session["timing_tier"],
diff --git a/wl_preproc/nwb/gather.py b/wl_preproc/nwb/gather.py
index 997e2cf..e84c11f 100644
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -370,23 +370,22 @@ def _outside_runs_notes(key: dict, session_key: dict, role: str) -> list[str]:
             "outside a run"]
 
 
-def _conditions(trials: list[dict], events: list[dict], runs: list[dict], session_dir: Path,
-                subject: str) -> tuple[list[dict], list[str], dict[str, list[dict]]]:
+def _conditions(trials: list[dict], events: list[dict], runs: list[dict],
+                record) -> tuple[list[dict], list[str], dict[str, list[dict]]]:
     """Each trial's condition and the settings that varied, and each run's
     conditions and trial counts, from the rig's own record joined by trial
     number (design spec `2026-09-29-nwb-publishing-design.md` sections 2.1
-    and 2.2). Returns the file's conditions, the notes on what did not
-    join, and the stimulus tables' rows (design spec
+    and 2.2). `record` is the session's `events/rigtrials.py::RigRecord`, or
+    None. Returns the file's conditions, the notes on what did not join, and
+    the stimulus tables' rows (design spec
     `2026-11-07-rig-record-fields-design.md` section 4)."""
     import collections
 
-    from wl_preproc.events.rigtrials import read_rig_trials
     from wl_preproc.nwb.conditions import block_conditions, join, stream_codes, trial_columns
     from wl_preproc.nwb.stimuli import stimulus_rows
     from wl_preproc.nwb.trial_record import places, record_columns
 
     trials.sort(key=lambda trial: trial["start_s"])
-    record = read_rig_trials(session_dir, subject)
     matched, notes = join(trials, record)
     codes = stream_codes(trials, events)
     names, settings = trial_columns(trials, matched, codes)
@@ -727,7 +726,16 @@ def gather(activation_key: dict) -> Gathered:
     blocks = _blocks(run_rows, session_key)
     for block in blocks:
         block["n_trials"] = sum(1 for trial in trials if trial["block_id"] == block["block_number"])
-    conditions, condition_notes, rig_tables = _conditions(trials, events, run_out, session_dir, key["subject"])
+    from wl_preproc.events.rigruns import read_rig_runs
+    from wl_preproc.events.rigtrials import read_rig_trials
+    from wl_preproc.nwb.run_record import run_record
+    from wl_preproc.nwb.trial_record import places
+
+    record = read_rig_trials(session_dir, key["subject"])
+    conditions, condition_notes, rig_tables = _conditions(trials, events, run_out, record)
+    run_tables, run_notes = run_record(run_out, blocks, read_rig_runs(session_dir, key["subject"]), places(record))
+    rig_tables.update(run_tables)
+    condition_notes += run_notes
     return Gathered(
         session={
             "identifier": identifier_for(key, session_id),
diff --git a/wl_preproc/nwb/intervals.py b/wl_preproc/nwb/intervals.py
index d4aabcd..3d6d838 100644
--- a/wl_preproc/nwb/intervals.py
+++ b/wl_preproc/nwb/intervals.py
@@ -49,6 +49,7 @@ def add_runs(nwb: NWBFile, runs: list[dict], systems: list[str]) -> None:
             column("closed", "Whether a RUN_END arrived.", [bool(r["closed"]) for r in rows]),
             column("calibration", "The display calibration the run ran against, from its start row in the rig's "
                                   "record ('' if it names none).", [r.get("calibration") or "" for r in rows]),
+            *_record_columns(rows, _run_columns()),
             *_coverage_columns(rows, systems),
         ],
     ))
@@ -77,6 +78,7 @@ def add_blocks(nwb: NWBFile, blocks: list[dict]) -> None:
             column("closed", "1 when a BLOCK_END arrived, 0 when not, -1 when never recorded.",
                    [-1 if r["closed"] is None else int(r["closed"]) for r in rows]),
             column("n_trials", "The block's trials in this file.", [r["n_trials"] for r in rows]),
+            *_record_columns(rows, _block_columns()),
         ],
     ))
 
@@ -92,15 +94,31 @@ def _setting_columns(rows: list[dict]) -> list:
             for key in keys]
 
 
-def _record_columns(rows: list[dict]) -> list:
-    """One column per field of the rig's line that some trial's line carries
-    (design spec `2026-11-07-rig-record-fields-design.md` section 3), in
-    `nwb/trial_record.py`'s order."""
-    from wl_preproc.nwb.trial_record import COLUMNS, PLANNED_COLUMNS
-
+def _record_columns(rows: list[dict], descriptions: dict[str, str]) -> list:
+    """One column per field of the rig's record some row carries (design spec
+    `2026-11-07-rig-record-fields-design.md` sections 3 and 5), in
+    `descriptions`' order."""
     present = set().union(*(row.get("record", {}).keys() for row in rows)) if rows else set()
     return [column(name, description, [row["record"][name] for row in rows])
-            for name, description in {**COLUMNS, **PLANNED_COLUMNS}.items() if name in present]
+            for name, description in descriptions.items() if name in present]
+
+
+def _trial_columns() -> dict[str, str]:
+    from wl_preproc.nwb.trial_record import COLUMNS, PLANNED_COLUMNS
+
+    return {**COLUMNS, **PLANNED_COLUMNS}
+
+
+def _run_columns() -> dict[str, str]:
+    from wl_preproc.nwb.run_record import RUN_COLUMNS
+
+    return dict(RUN_COLUMNS.values())
+
+
+def _block_columns() -> dict[str, str]:
+    from wl_preproc.nwb.run_record import BLOCK_COLUMNS
+
+    return BLOCK_COLUMNS
 
 
 def add_trials(nwb: NWBFile, trials: list[dict], systems: list[str]) -> None:
@@ -124,7 +142,7 @@ def add_trials(nwb: NWBFile, trials: list[dict], systems: list[str]) -> None:
                                  "(xcon/trials.jsonl), else the CONDITION number sent inside it, else ''."),
                    [r.get("condition", "") for r in rows]),
             *_setting_columns(rows),
-            *_record_columns(rows),
+            *_record_columns(rows, _trial_columns()),
             *_coverage_columns(rows, systems),
         ],
     )
diff --git a/wl_preproc/nwb/rig_record.py b/wl_preproc/nwb/rig_record.py
index 0958fda..c824c9e 100644
--- a/wl_preproc/nwb/rig_record.py
+++ b/wl_preproc/nwb/rig_record.py
@@ -17,11 +17,12 @@ change the rig applied, in `parameter_changes`, and each console control, in
 detail as JSON text. The file gives only the kind of actor, never a name,
 account, issuer or token (its decision 6).
 
-**Where each trial's stimuli were** (design spec
-`2026-11-07-rig-record-fields-design.md` section 4): `stimuli` and
-`stimulus_objects`, from the trial lines (`nwb/stimuli.py`), by
-`add_rig_tables`, which makes the module if the session has no record of its
-own.
+**Where each trial's stimuli were, and what each run owed** (design spec
+`2026-11-07-rig-record-fields-design.md` sections 4 and 5): `stimuli` and
+`stimulus_objects`, from the trial lines (`nwb/stimuli.py`), and
+`unopened_blocks`, `shortfalls` and `condition_counts`, from the run rows
+(`nwb/run_record.py`), by `add_rig_tables`, which makes the module if the
+session has no record of its own.
 
 Written only for a session wl-xcon ran (`gather.py::_rig_record`)."""
 
@@ -38,7 +39,8 @@ from wl_preproc.nwb.columns import column
 
 DESCRIPTION = ("What wl-xcon's record says the session was: the kind it was run as, the display calibration it "
                "opened with, the warnings it accepted, its training records, the changes it applied and the controls "
-               "it was sent, and where each trial's stimuli were. Carried as the rig wrote them.")
+               "it was sent, where each trial's stimuli were, and what each run owed. Carried as the rig wrote "
+               "them.")
 
 _POSITION = (
     ("x_deg", "Its cyclopean per-axis position across, degrees, atan(X/D) (NaN if none). Through the stereoscope "
@@ -76,6 +78,44 @@ TABLES: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
          ("eye", "'' for an array drawn once, left or right for a per-eye array's ring about that eye's position."),
          ("object", "Its index among the array's objects, from 0."),
          *_POSITION)),
+    "unopened_blocks": (
+        "Each block a run's end row says ended without running a trial, so the stream never opened it: a plan's "
+        "block can be over before it draws one.",
+        (("run_number", "The run."),
+         ("block", "Its block type."),
+         ("ended", "How it ended: rule, time, fall_back, next, back, run_time or run."),
+         ("ended_by", "Who moved the run on or back, by kind only ('' for nobody)."),
+         ("after_trial", "The trial_id of the run's last trial before it ended (-1 if none)."),
+         ("position", "Its place in the run's sequence of blocks, from 0 (-1 if not recorded)."))),
+    "shortfalls": (
+        "What each condition of a run left unrun, and why, from the run's end row.",
+        (("run_number", "The run."),
+         ("block", "The block type."),
+         ("condition", "The condition."),
+         ("trials", "The trials it left unrun (-1 if not recorded)."),
+         ("because", "cap (its repeat limit), rig, cleared (by a person), time (its block's), run_time (the run's) "
+                     "or designed (a designed block's broken trial not played again)."),
+         ("possible_avoidance", "1 when the animal's own failures reached the cap, 0 when not, -1 if not "
+                                "recorded."),
+         ("after_trial", "The trial_id after which it was left (-1 if none)."),
+         ("by_kind", "Who cleared it, by kind only ('' for nobody)."))),
+    "condition_counts": (
+        "Each condition's counts in each block of a run, from the run's end row. A run that ended in a fault can "
+        "leave these one try ahead of the trial lines.",
+        (("run_number", "The run."),
+         ("block", "The block type."),
+         ("condition", "The condition."),
+         ("trials", "Its trials, a trial and its drummed repeats one."),
+         ("presentations", "Its tries recorded."),
+         ("right", "Its right answers."),
+         ("wrong", "Its wrong answers."),
+         ("right_first", "Its trials right at the first try."),
+         ("owed", "What it still owed."),
+         ("repeats", "Its repeats."),
+         ("moved_past", "Its trials moved past."),
+         ("possible_avoidance", "1 when flagged as possible avoidance, 0 when not, -1 if not recorded."),
+         ("drummed", "Its drummed tries."),
+         ("failing", "Its failed tries in a row while being drummed as the run ended."))),
 }
 
 
diff --git a/wl_preproc/nwb/run_record.py b/wl_preproc/nwb/run_record.py
new file mode 100644
index 0000000..43d4cec
--- /dev/null
+++ b/wl_preproc/nwb/run_record.py
@@ -0,0 +1,157 @@
+"""What the rig's record says of each run and block beyond its task and
+calibration: the plan it played and why it ended, how each block ended, and
+what each run owed (design spec `2026-11-07-rig-record-fields-design.md`
+section 5).
+
+Pure functions over plain data. The runs and blocks are this file's own,
+measured from the recording; the rig's runs are joined to them by number, and
+each block end to the measured block whose number is its `block_in_session`.
+**A column is written only where some run carries its field**, as a trial's
+are. **A disagreement is a note, never a refusal**: the stream stays the
+measurement. **A trial index is published as a trial id** (section 2), through
+the run's own trial lines."""
+
+from __future__ import annotations
+
+import json
+
+from wl_preproc.events.rigruns import RigRun, RigRuns
+
+# Each runs-table column, by the field of the rig's run it reads (its name in
+# `RigRun.carries`), with its description.
+RUN_COLUMNS: dict[str, tuple[str, str]] = {
+    "plan": ("plan", "The plan the run played, by its name in its task ('' for a plain run of N trials, or if not "
+                     "recorded)."),
+    "plan_form": ("plan_form", "That plan as declared, as JSON text ('' if none)."),
+    "end_reason": ("end_reason", "Why a completed run ended: plan_done, plan_time, stalled or cannot_draw ('' for "
+                                 "a run that ended any other way, or if not recorded)."),
+    "stop_kind": ("stop_kind", "How the run stopped, as the rig's record says: completed, operator, limit, fault "
+                               "and the others ('' if not recorded: its process was killed)."),
+    "stopped_because": ("stopped_because", "The rig's sentence for why it stopped ('' if not recorded)."),
+    "rig_trials": ("rig_trials", "Its trials as the rig counts them, a trial and its drummed repeats one (-1 if not "
+                                 "recorded)."),
+    "presentations": ("rig_presentations", "Its presentations as the rig counts them, one per trial line (-1 if "
+                                           "not recorded)."),
+    "task_hash": ("task_hash", "The content hash of the task file it ran ('' if not recorded)."),
+    "seed": ("seed", "The seed its random draws came from, as text ('' if not recorded)."),
+    "draws": ("draws", "The task's random draws as declared, each with every field, as JSON text; a direction "
+                       "draw with equal ends is the whole turn ('' if none)."),
+    "generated": ("generated", "The conditions each block type's factorial generated, as JSON text ('' if none)."),
+}
+BLOCK_COLUMNS: dict[str, str] = {
+    "ended": "How the block ended: rule, time, fall_back, next, back, run_time or run ('' if not recorded).",
+    "ended_by": "Who moved the run on or back, by kind only: box or member ('' for nobody, or if not recorded).",
+    "after_trial": "The trial_id of the run's last trial before it ended (-1 if none, or not recorded).",
+    "position": "Its place in the run's sequence of blocks, from 0: a training progression's stage (-1 if not "
+                "recorded).",
+    "passed_at": "For a training progression's top stage, the trial_id after which its rule was first met (-1 if "
+                 "not, or not recorded).",
+    "varied": "Each setting that varied in it and how, as JSON text ('' if not recorded).",
+    "planned": "A designed block's planned sequence, as JSON text ('' if none).",
+    "counts": "Each condition's trials in it, overridden and eased, as JSON text ('' if not recorded).",
+}
+
+
+def _text(value) -> str:
+    return "" if value is None else json.dumps(value, sort_keys=True)
+
+
+def _whole(value: int | None) -> int:
+    return -1 if value is None else value
+
+
+def _flag(value: bool | None) -> int:
+    return -1 if value is None else int(value)
+
+
+def _run_value(field: str, rig: RigRun | None):
+    value = None if rig is None else getattr(rig, field)
+    if field in ("plan_form", "draws", "generated"):
+        return _text(value)
+    if field in ("rig_trials", "presentations"):
+        return _whole(value)
+    if field == "seed":
+        return "" if value is None else str(value)
+    return "" if value is None else value
+
+
+class _Trials:
+    """A run's trial index as a trial id, through its trial lines, counting
+    those it cannot place."""
+
+    def __init__(self, place: dict[tuple[int | None, int], int]) -> None:
+        self.place, self.unplaced = place, 0
+
+    def __call__(self, rig: RigRun, index: int | None) -> int:
+        if index is None:
+            return -1
+        found = self.place.get((rig.index, index))
+        if found is None:
+            self.unplaced += 1
+            return -1
+        return found
+
+
+def run_record(runs: list[dict], blocks: list[dict], rig_runs: RigRuns | None,
+               place: dict[tuple[int | None, int], int]) -> tuple[dict[str, list[dict]], list[str]]:
+    """Sets each of the file's runs' `plan` and `end_reason` and its `record`
+    (its runs-table columns), and each measured block's `ended` and `record`;
+    returns the rows of the `unopened_blocks`, `shortfalls` and
+    `condition_counts` tables, and the notes."""
+    by_number = {} if rig_runs is None else {rig.number: rig for rig in rig_runs.runs}
+    files = [(run, by_number.get(run["run_number"])) for run in runs]
+    carried = set().union(*(rig.carries for _, rig in files if rig is not None))
+    trial_id, notes = _Trials(place), []
+    tables: dict[str, list[dict]] = {"unopened_blocks": [], "shortfalls": [], "condition_counts": []}
+    measured = {block["block_number"]: block for block in blocks}
+    for run, rig in files:
+        run["plan"] = None if rig is None else rig.plan
+        run["end_reason"] = None if rig is None else rig.end_reason
+        run["record"] = {column: _run_value(field, rig) for field, (column, _) in RUN_COLUMNS.items()
+                         if field in carried}
+        if rig is None:
+            continue
+        number = run["run_number"]
+        named = set()
+        for end in rig.block_ends:
+            fields = {"ended": end.ended or "", "ended_by": end.by_kind or "",
+                      "after_trial": trial_id(rig, end.after_trial), "position": _whole(end.position)}
+            if end.block_in_session is None:
+                tables["unopened_blocks"].append({"run_number": number, "block": end.block or "", **fields})
+                continue
+            block = measured.get(end.block_in_session)
+            if block is None or block["run_number"] != number:
+                notes.append(f"run {number}: the rig's record ends block {end.block_in_session}, which the "
+                             "recording never opened in it, so that end is on no block")
+                continue
+            named.add(end.block_in_session)
+            block["ended"] = end.ended
+            block["record"] = {**fields, "passed_at": trial_id(rig, end.passed_at), "varied": _text(end.varied),
+                               "planned": _text(end.planned), "counts": _text(end.counts)}
+        if "block_ends" in rig.carries:
+            for block in blocks:
+                if block["run_number"] == number and block["block_number"] not in named:
+                    notes.append(f"run {number}: block {block['block_number']} has no end in the rig's record")
+        tables["shortfalls"] += [
+            {"run_number": number, "block": short.block or "", "condition": short.condition or "",
+             "trials": _whole(short.trials), "because": short.because or "",
+             "possible_avoidance": _flag(short.possible_avoidance), "after_trial": trial_id(rig, short.after_trial),
+             "by_kind": short.by_kind or ""} for short in rig.shortfalls]
+        tables["condition_counts"] += [
+            {"run_number": number, "block": count.block or "", "condition": count.condition or "",
+             **{name: _whole(getattr(count, name)) for name in ("trials", "presentations", "right", "wrong",
+                                                               "right_first", "owed", "repeats", "moved_past")},
+             "possible_avoidance": _flag(count.possible_avoidance), "drummed": _whole(count.drummed),
+             "failing": _whole(count.failing)} for count in rig.conditions]
+        lines = sum(1 for (run_index, _index) in place if run_index == rig.index)
+        if rig.presentations is not None and rig.presentations != lines:
+            notes.append(f"run {number}: the rig's end row counts {rig.presentations} presentation(s) and its "
+                         f"record holds {lines} trial line(s)")
+    if any("block_ends" in rig.carries for _, rig in files if rig is not None):
+        for block in blocks:
+            block.setdefault("record", {name: "" if name in ("ended", "ended_by", "varied", "planned", "counts")
+                                        else -1 for name in BLOCK_COLUMNS})
+    if trial_id.unplaced:
+        notes.append(f"{trial_id.unplaced} trial index(es) in the rig's run rows name no trial line of their run, "
+                     "so they are -1")
+    return tables, notes
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/events/test_rigruns.py tests/nwb/test_run_record.py tests/nwb/test_writers.py::test_runs_and_blocks_carry_what_the_rigs_record_says_of_them tests/nwb/test_describe.py -q -p no:cacheprovider
```

Expected: `30 passed`

- [ ] **Step 5: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/events tests/nwb tests/schema/test_nwb_build.py tests/schema/test_nwb_rig_record.py tests/schema/test_session_listing.py tests/cli/test_schemas_export.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `277 passed`

- [ ] **Step 6: Commit.**

```bash
git add -A tests wl_preproc docs
git commit -F - <<'MSG'
feat(nwb): each run says the plan it played and why it ended, and each block how it ended -- the runs table's plan, end reason, stop, the rig's counts, the task's content hash and the seed; the blocks table's end, joined by the stream's block number; unopened_blocks, shortfalls and condition_counts tables; a disagreement with the stream a note, never a refusal; the description's Run.plan, Run.end_reason and RunBlock.ended, defaulted (sections 5 and 8)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

- [ ] **Step 7: The mutation checks,** on the commit. Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "every run column is written",
  "wl_preproc/nwb/run_record.py",
  "                         if field in carried}\n",
  "                         if True}\n",
  "tests/events/test_rigruns.py tests/nwb/test_run_record.py tests/nwb/test_writers.py tests/nwb/test_describe.py"
 ],
 [
  "a block end joins the block by its place",
  "wl_preproc/nwb/run_record.py",
  "            block = measured.get(end.block_in_session)\n",
  "            block = measured.get(end.position)\n",
  "tests/events/test_rigruns.py tests/nwb/test_run_record.py tests/nwb/test_writers.py tests/nwb/test_describe.py"
 ],
 [
  "a block that ran no trial is not unopened",
  "wl_preproc/nwb/run_record.py",
  "            if end.block_in_session is None:\n",
  "            if False:\n",
  "tests/events/test_rigruns.py tests/nwb/test_run_record.py tests/nwb/test_writers.py tests/nwb/test_describe.py"
 ],
 [
  "the presentations are not checked",
  "wl_preproc/nwb/run_record.py",
  "        if rig.presentations is not None and rig.presentations != lines:\n",
  "        if False:\n",
  "tests/events/test_rigruns.py tests/nwb/test_run_record.py tests/nwb/test_writers.py tests/nwb/test_describe.py"
 ],
 [
  "the seed is a number",
  "wl_preproc/nwb/run_record.py",
  "        return \"\" if value is None else str(value)\n",
  "        return \"\" if value is None else value\n",
  "tests/events/test_rigruns.py tests/nwb/test_run_record.py tests/nwb/test_writers.py tests/nwb/test_describe.py"
 ],
 [
  "the end row's trials keep their own name",
  "wl_preproc/events/rigruns.py",
  "                {\"trials\": \"rig_trials\"}.get(name, name)\n",
  "                name\n",
  "tests/events/test_rigruns.py tests/nwb/test_run_record.py tests/nwb/test_writers.py tests/nwb/test_describe.py"
 ]
]
```

Expected: 6 lines, each `CAUGHT`; `git status --short` shows no change.

---

### Task 4: A condition's own values, apart from drawn and person-held ones

§6, the requester's decision of 2026-11-07, amendment 3: a condition's `settings` and `varying` from the trials where a setting was neither drawn nor held by a person; `drawn` and `live` summarised beside them and its eased trials counted, each where some line carries its field; a setting the run names as a direction summarised as the smallest counter-clockwise arc; the description's `Condition.drawn`, `Condition.live` and `TrialCounts.eased`, defaulted; the NWB conditions table's `drawn`, `live` and `n_eased`.

**Files:**
- Modify: `wl_preproc/nwb/conditions.py` (`arc`, `_summarise`, `_apart`, `block_conditions`)
- Modify: `wl_preproc/nwb/gather.py` (the runs' directions), `wl_preproc/nwb/intervals.py` (`_apart_columns`), `wl_preproc/contracts/nwb_description.py`, `docs/schemas/nwb_description.json`
- Test: `tests/nwb/test_conditions.py`, `tests/nwb/test_writers.py`, `tests/nwb/test_describe.py`

**Interfaces:**
- Consumes: Task 1's `RigTrial.drawn`, `.live`, `.eased`, `.carries`; Task 3's `RigRun.directions`.
- Produces: `conditions.arc(values) -> {"from", "to"}`; `block_conditions(trials, matched, codes,
  directions=frozenset())`; each condition entry's `drawn`, `live` and `trials["eased"]` where carried;
  `_conditions(..., directions)`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/nwb/test_conditions.py b/tests/nwb/test_conditions.py
index 3c3c479..213a74e 100644
--- a/tests/nwb/test_conditions.py
+++ b/tests/nwb/test_conditions.py
@@ -113,3 +113,62 @@ def test_a_setting_whose_type_differs_between_trials_is_text():
     assert settings["target"] == ["5.0", '"left"']
     (entry,) = block_conditions(trials, matched, {})
     assert entry["varying"] == {"target": {"values": [5.0, "left"], "n_distinct": 2}}
+
+
+def _c3(index, condition, drawn=None, live=(), eased=(), **params):
+    """A line as wl-xcon's engine build C3 writes it: each drawn setting's
+    source, the settings a person held, and those that kept it out of moving
+    up."""
+    from wl_preproc.events.rigtrials import RigTrial
+
+    return RigTrial(number=index, index=index, outcome="correct", block="main", condition=condition, params=params,
+                    drawn=drawn or {}, live=tuple(live), eased=tuple(eased),
+                    carries=frozenset({"drawn", "live", "eased"}))
+
+
+def test_a_conditions_own_values_are_kept_apart_from_drawn_and_person_held_ones():
+    """Design spec `2026-11-07-rig-record-fields-design.md` section 6, the
+    requester's decision of 2026-11-07: a setting drawn on a trial, or held
+    there by a person, is not the condition's own on that trial. A person's
+    value on every trial no longer reads as the condition's constant."""
+    from wl_preproc.nwb.conditions import block_conditions
+
+    trials = [_trial(number, float(number)) for number in (1, 2, 3, 4)]
+    matched = {1: _c3(1, "c", contrast=0.5, hold=0.3, size=2.0, drawn={"hold": "task"}, live=["size"]),
+               2: _c3(2, "c", contrast=0.5, hold=0.6, size=2.0, drawn={"hold": 4}, live=["size"]),
+               3: _c3(3, "c", contrast=0.25, hold=0.4, size=2.0, drawn={"hold": "task"}, live=["size", "contrast"],
+                      eased=["contrast"]),
+               4: _c3(4, "c", contrast=0.5, hold=0.5, size=2.0, drawn={"hold": "task"}, live=["size"])}
+    (entry,) = block_conditions(trials, matched, {})
+    assert entry["settings"] == {"contrast": 0.5} and entry["varying"] == {}
+    assert entry["drawn"] == {"hold": {"sources": [4, "task"], "min": 0.3, "max": 0.6}}
+    assert entry["live"] == {"contrast": {"trials": 1, "min": 0.25, "max": 0.25},
+                             "size": {"trials": 4, "min": 2.0, "max": 2.0}}
+    assert entry["trials"] == {"total": 4, "by_outcome": {"correct": 4}, "eased": 1}
+
+
+def test_a_direction_is_summarised_as_the_smallest_arc_holding_it():
+    """A -45 to 45 degree draw, written 315 to 45, is that arc, never 0 to
+    360; a value given as -90 is 270."""
+    from wl_preproc.nwb.conditions import block_conditions
+
+    trials = [_trial(number, float(number)) for number in (1, 2, 3)]
+    matched = {1: _c3(1, "c", target_dir=330.0, drawn={"target_dir": "block"}),
+               2: _c3(2, "c", target_dir=40.0, drawn={"target_dir": "block"}),
+               3: _c3(3, "c", target_dir=10.0, drawn={"target_dir": "block"})}
+    (entry,) = block_conditions(trials, matched, {}, frozenset({"target_dir"}))
+    assert entry["drawn"]["target_dir"] == {"sources": ["block"], "from": 330.0, "to": 40.0}
+    held = {1: _c3(1, "c", dir=-90.0), 2: _c3(2, "c", dir=80.0)}
+    (entry,) = block_conditions(trials[:2], held, {}, frozenset({"dir"}))
+    assert entry["varying"]["dir"] == {"from": 270.0, "to": 80.0}
+
+
+def test_the_arc_leaves_out_the_widest_gap():
+    """Section 6's ruling: the circle less its widest gap between neighbours,
+    the first from 0 degrees where gaps tie; one value is its own arc."""
+    from wl_preproc.nwb.conditions import arc
+
+    assert arc([10.0]) == {"from": 10.0, "to": 10.0}
+    assert arc([350.0, 20.0, 5.0]) == {"from": 350.0, "to": 20.0}
+    assert arc([0.0, 90.0, 180.0, 270.0]) == {"from": 90.0, "to": 0.0}
+    assert arc([100.0, 200.0]) == {"from": 100.0, "to": 200.0}
diff --git a/tests/nwb/test_describe.py b/tests/nwb/test_describe.py
index 8e0bf44..a170959 100644
--- a/tests/nwb/test_describe.py
+++ b/tests/nwb/test_describe.py
@@ -61,7 +61,8 @@ def test_the_description_of_a_file_without_eye_data():
     assert run["task"] == {"code": "2", "name": "rf_map"}
     assert run["measured"] == {"start_s": 0.5, "stop_s": 29.5}
     assert run["coverage"] == {"ohdpi": {"coverage": "full", "covered_s": 29.0}}
-    assert run["conditions"] == [CONDITION]
+    assert run["conditions"] == [{**CONDITION, "trials": {**CONDITION["trials"], "eased": 0}, "drawn": None,
+                                  "live": None}]
     # The measured blocks inside the run (design spec
     # `2026-10-01-session-listing-and-run-requests-design.md` section 4).
     assert run["blocks"] == [{"block_number": 1, "block_in_run": 1, "block_type": "Bt1",
@@ -218,3 +219,19 @@ def test_a_run_says_its_plan_and_end_reason_and_a_block_how_it_ended():
     assert (run["plan"], run["end_reason"], run["blocks"][0]["ended"]) == ("training", "plan_done", "rule")
     run = described(RUN, BLOCK)
     assert (run["plan"], run["end_reason"], run["blocks"][0]["ended"]) == (None, None, None)
+
+
+def test_a_condition_says_its_drawn_and_person_held_values_and_its_eased_trials():
+    """Design spec `2026-11-07-rig-record-fields-design.md` sections 6 and 8."""
+    from wl_preproc.nwb.describe import describe
+    from wl_preproc.nwb.gather import Gathered
+
+    condition = {**CONDITION, "drawn": {"hold": {"sources": ["task"], "min": 0.2, "max": 0.8}},
+                 "live": {"contrast": {"trials": 1, "min": 0.25, "max": 0.25}},
+                 "trials": {**CONDITION["trials"], "eased": 1}}
+    data = Gathered(session=SESSION, systems=["ohdpi"], runs=[{**RUN, "conditions": [condition]}], blocks=[BLOCK],
+                    trials=[{"trial_id": 1}], events=[{}, {}], timebase={}, eye=None, conditions=[condition])
+    (described,) = describe(data, status="written", n_critical=0, checksums=[CHECKSUM],
+                            built_at=BUILT_AT)["runs"][0]["conditions"]
+    assert (described["drawn"], described["live"], described["trials"]["eased"]) == (
+        condition["drawn"], condition["live"], 1)
diff --git a/tests/nwb/test_writers.py b/tests/nwb/test_writers.py
index 5355a6c..3235bc1 100644
--- a/tests/nwb/test_writers.py
+++ b/tests/nwb/test_writers.py
@@ -180,11 +180,29 @@ def test_the_conditions_table_and_none_when_no_condition_is_known(tmp_path):
         assert frame["settings"].tolist() == ["", '{"contrast": 0.5}']
         assert frame["varying"].tolist() == ["", '{"hold": {"max": 0.35, "min": 0.3}}']
         assert frame["n_trials"].tolist() == [1, 2]
+        assert "drawn" not in frame and "n_eased" not in frame
     (tmp_path / "empty").mkdir()
     _path, io, nwb = _write(tmp_path / "empty", lambda nwb: add_conditions(nwb, []))
     with io:
         assert "behavior" not in nwb.processing
 
+
+def test_the_conditions_table_carries_drawn_and_person_held_values_where_recorded(tmp_path):
+    """Design spec `2026-11-07-rig-record-fields-design.md` section 6."""
+    from wl_preproc.nwb.intervals import add_conditions
+
+    conditions = [{"name": "c", "code": None, "settings": {}, "varying": {},
+                   "drawn": {"hold": {"sources": ["task"], "min": 0.2, "max": 0.8}}, "live": {},
+                   "trials": {"total": 4, "by_outcome": {"correct": 4}, "eased": 1}},
+                  {"name": "d", "code": None, "settings": {}, "varying": {}, "drawn": None, "live": None,
+                   "trials": {"total": 1, "by_outcome": {"correct": 1}}}]
+    _path, io, nwb = _write(tmp_path, lambda nwb: add_conditions(nwb, conditions))
+    with io:
+        frame = nwb.processing["behavior"]["conditions"].to_dataframe()
+        assert frame["drawn"].tolist() == ['{"hold": {"max": 0.8, "min": 0.2, "sources": ["task"]}}', ""]
+        assert frame["live"].tolist() == ["{}", ""]
+        assert frame["n_eased"].tolist() == [1, 0]
+
 def test_the_timebase_tables(tmp_path):
     from wl_preproc.nwb.timebase import add_timebase
 
PATCH
```

- [ ] **Step 2: Run them and see them fail.** The two tests of the conditions table run with this task's: the first now also checks that a record from before C3 gains no `drawn` or `n_eased` column.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/nwb/test_conditions.py tests/nwb/test_writers.py::test_the_conditions_table_and_none_when_no_condition_is_known tests/nwb/test_writers.py::test_the_conditions_table_carries_drawn_and_person_held_values_where_recorded tests/nwb/test_describe.py -q -p no:cacheprovider
```

Expected: `6 failed, 18 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/docs/schemas/nwb_description.json b/docs/schemas/nwb_description.json
index bedb97b..52f7fa3 100644
--- a/docs/schemas/nwb_description.json
+++ b/docs/schemas/nwb_description.json
@@ -84,6 +84,38 @@
           ],
           "title": "Code"
         },
+        "drawn": {
+          "anyOf": [
+            {
+              "additionalProperties": {
+                "additionalProperties": true,
+                "type": "object"
+              },
+              "type": "object"
+            },
+            {
+              "type": "null"
+            }
+          ],
+          "default": null,
+          "title": "Drawn"
+        },
+        "live": {
+          "anyOf": [
+            {
+              "additionalProperties": {
+                "additionalProperties": true,
+                "type": "object"
+              },
+              "type": "object"
+            },
+            {
+              "type": "null"
+            }
+          ],
+          "default": null,
+          "title": "Live"
+        },
         "name": {
           "anyOf": [
             {
@@ -1000,6 +1032,11 @@
           "title": "By Outcome",
           "type": "object"
         },
+        "eased": {
+          "default": 0,
+          "title": "Eased",
+          "type": "integer"
+        },
         "total": {
           "title": "Total",
           "type": "integer"
diff --git a/wl_preproc/contracts/nwb_description.py b/wl_preproc/contracts/nwb_description.py
index b332755..3e87c55 100644
--- a/wl_preproc/contracts/nwb_description.py
+++ b/wl_preproc/contracts/nwb_description.py
@@ -14,7 +14,8 @@ many of wl-xcon's training records the file holds (design spec
 `2026-10-18-training-records-and-backup-clock-design.md` section 3.2), and
 2026-10-30 the eyes whose true eccentricity and polar angle it holds (design
 spec `2026-10-30-true-angle-saccades-design.md` section 8), and 2026-11-07
-each run's plan and end reason and each block's end (design spec
+each run's plan and end reason, each block's end, and each condition's drawn
+and person-held values and eased trials (design spec
 `2026-11-07-rig-record-fields-design.md` section 8).
 Version 2 gave `probes` their shape, each with both of its areas (design spec
 `2026-09-30-nwb-probes-design.md` section 3.2). Later pieces add groups and
@@ -108,6 +109,10 @@ class DataTypes(_Frozen):
 class TrialCounts(_Frozen):
     total: int
     by_outcome: dict[str, int]
+    # A condition's trials a live change kept out of moving up, from
+    # wl-xcon's record; 0 where none is recorded (design spec
+    # `2026-11-07-rig-record-fields-design.md` section 6).
+    eased: int = 0
 
 
 class Condition(_Frozen):
@@ -121,6 +126,13 @@ class Condition(_Frozen):
     settings: dict[str, Any] | None
     varying: dict[str, dict[str, Any]] | None
     trials: TrialCounts
+    # Since 2026-11-07 its settings and varying are its own values only, from
+    # the trials where a setting was neither drawn nor held by a person; its
+    # drawn and person-held values are summarised here, null where the rig's
+    # record names none (design spec `2026-11-07-rig-record-fields-design.md`
+    # section 6).
+    drawn: dict[str, dict[str, Any]] | None = None
+    live: dict[str, dict[str, Any]] | None = None
 
 
 class Interval(_Frozen):
diff --git a/wl_preproc/nwb/conditions.py b/wl_preproc/nwb/conditions.py
index 4ec542e..5b52224 100644
--- a/wl_preproc/nwb/conditions.py
+++ b/wl_preproc/nwb/conditions.py
@@ -5,7 +5,19 @@ Pure functions over plain data. The trials are this file's own, with ids from
 the stream's `TRIAL_NUMBER`; the rig's record (`events/rigtrials.py`) is joined
 to them by that number and nothing else. **No guessing**: a trial the record
 does not name exactly once gets no condition and no settings, and the notes say
-why."""
+why.
+
+**A condition's own values, apart from drawn and person-held ones** (design
+spec `2026-11-07-rig-record-fields-design.md` section 6, the requester's
+decision of 2026-11-07): its `settings` and `varying` come only from trials
+where the setting was neither drawn at random nor held by a person; beside
+them, `drawn` (each drawn setting's sources and a summary of its values) and
+`live` (how many trials a person held it on, and a summary). Each condition's
+`trials` counts its `eased` ones. Each appears only where some line carries
+its field, so a record from before wl-xcon's engine build C3 summarises as
+before. **A direction is an arc**: a setting the run's start row names in
+`directions` is summarised by the smallest counter-clockwise arc holding its
+values, never by its least and greatest."""
 
 from __future__ import annotations
 
@@ -59,8 +71,27 @@ def stream_codes(trials: list[dict], events: list[dict]) -> dict[int, int]:
     return out
 
 
-def _summarise(values: list) -> dict:
+def arc(values: list[float]) -> dict:
+    """The smallest arc, counter-clockwise, holding every angle in `values`
+    (each taken mod 360 first): the circle less its widest gap between
+    neighbouring values, the first from 0 degrees where gaps tie (section
+    6's ruling). One value is the arc from it to itself."""
+    from wl_preproc.eye.detect.geometry import wrap_360_deg
+
+    angles = sorted({float(wrap_360_deg(value)) for value in values})
+    gaps = [(angles[(i + 1) % len(angles)] - angle) % 360.0 for i, angle in enumerate(angles)]
+    if len(angles) == 1:
+        return {"from": angles[0], "to": angles[0]}
+    widest = max(range(len(gaps)), key=lambda i: (gaps[i], -i))
+    return {"from": angles[(widest + 1) % len(angles)], "to": angles[widest]}
+
+
+def _summarise(values: list, direction: bool = False) -> dict:
+    if not values:
+        return {}
     if all(_is_number(value) for value in values):
+        if direction:
+            return arc(values)
         return {"min": min(values), "max": max(values)}
     distinct, seen = [], set()
     for value in values:
@@ -70,12 +101,26 @@ def _summarise(values: list) -> dict:
     return {"values": distinct[:MAX_DISTINCT], "n_distinct": len(distinct)}
 
 
-def block_conditions(trials: list[dict], matched: dict[int, RigTrial], codes: dict[int, int]) -> list[dict]:
+def _apart(key: str, rig: RigTrial) -> str | None:
+    """Why `key`'s value on this trial is not its condition's own: `drawn` or
+    `live`; None when it is."""
+    if rig.drawn and key in rig.drawn:
+        return "drawn"
+    if key in rig.live:
+        return "live"
+    return None
+
+
+def block_conditions(trials: list[dict], matched: dict[int, RigTrial], codes: dict[int, int],
+                     directions: frozenset[str] = frozenset()) -> list[dict]:
     """One entry per condition that ran among `trials` (one block's).
 
     With the rig's record, by condition name: the settings constant across
-    that condition's trials, a summary of each that varied, and trial counts.
-    Without it, by the stream's `CONDITION` number, settings unknown."""
+    that condition's trials, a summary of each that varied, and trial counts,
+    each setting from the trials where it was the condition's own; and, where
+    the lines carry them, its drawn and person-held values and its eased
+    trials. Without it, by the stream's `CONDITION` number, settings unknown.
+    `directions` names the settings summarised as arcs."""
     groups: dict = {}
     for trial in trials:
         rig = matched.get(trial["trial_id"])
@@ -95,15 +140,31 @@ def block_conditions(trials: list[dict], matched: dict[int, RigTrial], codes: di
             "trials": {"total": len(members), "by_outcome": dict(sorted(outcomes.items()))},
         }
         if kind == "name":
-            keys = sorted(set().union(*(rig.params.keys() for _, rig in members)))
-            settings, varying = {}, {}
+            rigs = [rig for _, rig in members]
+            keys = sorted(set().union(*(rig.params.keys() for rig in rigs)))
+            settings, varying, drawn, live = {}, {}, {}, {}
             for key in keys:
-                values = [rig.params.get(key) for _, rig in members]
-                if len({_text(value) for value in values}) == 1:
-                    settings[key] = values[0]
-                else:
-                    varying[key] = _summarise([value for value in values if value is not None])
+                own = [rig.params.get(key) for rig in rigs if _apart(key, rig) is None]
+                if own and len({_text(value) for value in own}) == 1:
+                    settings[key] = own[0]
+                elif own:
+                    varying[key] = _summarise([value for value in own if value is not None], key in directions)
+                taken = [rig for rig in rigs if _apart(key, rig) == "drawn"]
+                if taken:
+                    drawn[key] = {"sources": sorted({rig.drawn[key] for rig in taken}, key=str),
+                                  **_summarise([rig.params[key] for rig in taken if key in rig.params],
+                                               key in directions)}
+                held = [rig for rig in rigs if _apart(key, rig) == "live"]
+                if held:
+                    live[key] = {"trials": len(held), **_summarise(
+                        [rig.params[key] for rig in held if key in rig.params], key in directions)}
             entry["settings"], entry["varying"] = settings, varying
+            if any("drawn" in rig.carries for rig in rigs):
+                entry["drawn"] = drawn
+            if any("live" in rig.carries for rig in rigs):
+                entry["live"] = live
+            if any("eased" in rig.carries for rig in rigs):
+                entry["trials"]["eased"] = sum(1 for rig in rigs if rig.eased)
         out.append(entry)
     return out
 
diff --git a/wl_preproc/nwb/gather.py b/wl_preproc/nwb/gather.py
index e84c11f..f0a845d 100644
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -370,13 +370,14 @@ def _outside_runs_notes(key: dict, session_key: dict, role: str) -> list[str]:
             "outside a run"]
 
 
-def _conditions(trials: list[dict], events: list[dict], runs: list[dict],
-                record) -> tuple[list[dict], list[str], dict[str, list[dict]]]:
+def _conditions(trials: list[dict], events: list[dict], runs: list[dict], record,
+                directions: frozenset[str] = frozenset()) -> tuple[list[dict], list[str], dict[str, list[dict]]]:
     """Each trial's condition and the settings that varied, and each run's
     conditions and trial counts, from the rig's own record joined by trial
     number (design spec `2026-09-29-nwb-publishing-design.md` sections 2.1
     and 2.2). `record` is the session's `events/rigtrials.py::RigRecord`, or
-    None. Returns the file's conditions, the notes on what did not join, and
+    None; `directions` the settings its runs name as directions. Returns the
+    file's conditions, the notes on what did not join, and
     the stimulus tables' rows (design spec
     `2026-11-07-rig-record-fields-design.md` section 4)."""
     import collections
@@ -400,8 +401,8 @@ def _conditions(trials: list[dict], events: list[dict], runs: list[dict],
         inside = [trial for trial in trials if trial["run_number"] == run["run_number"]]
         outcomes = collections.Counter(trial["outcome"] or "unknown" for trial in inside)
         run["trials"] = {"total": len(inside), "by_outcome": dict(sorted(outcomes.items()))}
-        run["conditions"] = block_conditions(inside, matched, codes)
-    return (block_conditions(trials, matched, codes), notes,
+        run["conditions"] = block_conditions(inside, matched, codes, directions)
+    return (block_conditions(trials, matched, codes, directions), notes,
             {"stimuli": stimuli, "stimulus_objects": stimulus_objects})
 
 
@@ -732,8 +733,10 @@ def gather(activation_key: dict) -> Gathered:
     from wl_preproc.nwb.trial_record import places
 
     record = read_rig_trials(session_dir, key["subject"])
-    conditions, condition_notes, rig_tables = _conditions(trials, events, run_out, record)
-    run_tables, run_notes = run_record(run_out, blocks, read_rig_runs(session_dir, key["subject"]), places(record))
+    rig_runs = read_rig_runs(session_dir, key["subject"])
+    directions = frozenset() if rig_runs is None else frozenset().union(*(rig.directions for rig in rig_runs.runs))
+    conditions, condition_notes, rig_tables = _conditions(trials, events, run_out, record, directions)
+    run_tables, run_notes = run_record(run_out, blocks, rig_runs, places(record))
     rig_tables.update(run_tables)
     condition_notes += run_notes
     return Gathered(
diff --git a/wl_preproc/nwb/intervals.py b/wl_preproc/nwb/intervals.py
index 3d6d838..a4fd35d 100644
--- a/wl_preproc/nwb/intervals.py
+++ b/wl_preproc/nwb/intervals.py
@@ -148,6 +148,25 @@ def add_trials(nwb: NWBFile, trials: list[dict], systems: list[str]) -> None:
     )
 
 
+def _apart_columns(rows: list[dict]) -> list:
+    """A condition's drawn and person-held values and its eased trials, each
+    where some condition has them (design spec
+    `2026-11-07-rig-record-fields-design.md` section 6)."""
+    out = []
+    for name, description in (
+            ("drawn", "Each setting drawn at random on its trials: where its ranges came from and a summary of the "
+                      "values drawn, as JSON ('' where none, or not recorded)."),
+            ("live", "Each setting a person held on its trials: on how many, and a summary of the values, as JSON "
+                     "('' where none, or not recorded).")):
+        if any(name in row for row in rows):
+            out.append(column(name, description, ["" if row.get(name) is None else json.dumps(row[name], sort_keys=True)
+                                                  for row in rows]))
+    if any("eased" in row["trials"] for row in rows):
+        out.append(column("n_eased", "How many of them a live change kept out of moving up (0 where not recorded).",
+                          [row["trials"].get("eased", 0) for row in rows]))
+    return out
+
+
 def add_conditions(nwb: NWBFile, conditions: list[dict]) -> None:
     """`processing/behavior/conditions`: one row per condition that ran, with
     the settings constant across its trials and those that varied (design
@@ -177,6 +196,7 @@ def add_conditions(nwb: NWBFile, conditions: list[dict]) -> None:
             column("varying", "Each setting that varied within it: a range for numbers, else its distinct values, as JSON.",
                    ["" if row["varying"] is None else json.dumps(row["varying"], sort_keys=True) for row in rows]),
             column("n_trials", "How many of the file's trials ran under it.", [row["trials"]["total"] for row in rows]),
+            *_apart_columns(rows),
         ],
     ))
 
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/nwb/test_conditions.py tests/nwb/test_writers.py::test_the_conditions_table_and_none_when_no_condition_is_known tests/nwb/test_writers.py::test_the_conditions_table_carries_drawn_and_person_held_values_where_recorded tests/nwb/test_describe.py -q -p no:cacheprovider
```

Expected: `24 passed`

- [ ] **Step 5: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/nwb tests/schema/test_nwb_build.py tests/cli/test_schemas_export.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `170 passed`

- [ ] **Step 6: Commit.**

```bash
git add -A tests wl_preproc docs
git commit -F - <<'MSG'
feat(nwb): a condition's own values, apart from drawn and person-held ones -- its settings and varying from the trials where a setting was neither drawn nor held, its drawn and held values summarised beside them, its eased trials counted, directions summarised as the smallest arc; each where some line carries its field (section 6, the requester's decision of 2026-11-07)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

- [ ] **Step 7: The mutation checks,** on the commit. Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "a drawn or held value is the condition's own",
  "wl_preproc/nwb/conditions.py",
  "                own = [rig.params.get(key) for rig in rigs if _apart(key, rig) is None]\n",
  "                own = [rig.params.get(key) for rig in rigs]\n",
  "tests/nwb/test_conditions.py tests/nwb/test_writers.py tests/nwb/test_describe.py"
 ],
 [
  "a direction is its least and greatest",
  "wl_preproc/nwb/conditions.py",
  "        if direction:\n            return arc(values)\n",
  "        if False:\n            return arc(values)\n",
  "tests/nwb/test_conditions.py tests/nwb/test_writers.py tests/nwb/test_describe.py"
 ],
 [
  "a tie leaves out the last gap",
  "wl_preproc/nwb/conditions.py",
  "    widest = max(range(len(gaps)), key=lambda i: (gaps[i], -i))\n",
  "    widest = max(range(len(gaps)), key=lambda i: (gaps[i], i))\n",
  "tests/nwb/test_conditions.py tests/nwb/test_writers.py tests/nwb/test_describe.py"
 ],
 [
  "every trial is eased",
  "wl_preproc/nwb/conditions.py",
  "                entry[\"trials\"][\"eased\"] = sum(1 for rig in rigs if rig.eased)\n",
  "                entry[\"trials\"][\"eased\"] = len(rigs)\n",
  "tests/nwb/test_conditions.py tests/nwb/test_writers.py tests/nwb/test_describe.py"
 ],
 [
  "an old record gains drawn",
  "wl_preproc/nwb/conditions.py",
  "            if any(\"drawn\" in rig.carries for rig in rigs):\n",
  "            if True:\n",
  "tests/nwb/test_conditions.py tests/nwb/test_writers.py tests/nwb/test_describe.py"
 ]
]
```

Expected: 5 lines, each `CAUGHT`; `git status --short` shows no change.

---

### Task 5: The change log by kind and act, and the count of a person's acts

§7, the requester's decision of 2026-11-07, amendments 1, 5 and 6: a change row's instant read; its kind, act, first trial, way, step and a kind's own fields read from its stored line (`change_fields`); a change row that strobed nothing timed through the rig's clock fit, which a coded change's instant joins unless with it the fit would fail; the NWB `parameter_changes` table by kind; the description's and the listing's count of live parameter changes a person's acts on a setting.

**Files:**
- Modify: `wl_preproc/events/rigrecords.py` (`ParameterChange.at`, `SETTING_KINDS`, `ChangeFields`, `change_fields`, `acts`), `wl_preproc/events/rigtiming.py` (`time_records`)
- Modify: `wl_preproc/nwb/gather.py` (`_rig_record`), `wl_preproc/nwb/rig_record.py` (`_add_changes`), `wl_preproc/nwb/describe.py`, `wl_preproc/listing/entry.py`, `wl_preproc/contracts/nwb_description.py`, `wl_preproc/contracts/protocol.py`, `docs/schemas/`
- Test: `tests/events/test_rigrecords.py`, `tests/events/test_rigtiming.py`, `tests/nwb/test_writers.py`, `tests/nwb/test_describe.py`

**Interfaces:**
- Consumes: Task 1's `Fields` and kinds.
- Produces: `rigrecords.ChangeFields(kind, change, trial_number, went, step, to, from_draw, block, moment,
  strobed_nothing)`, `change_fields(rows) -> (fields, problems)`, `acts(changes) -> int`, `SETTING_KINDS`;
  `ParameterChange.at`; each gathered change's `fields` and `time_source`, and the rig record's
  `change_notes`; `tests/nwb/test_writers.py::a_set(**fields)`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/events/test_rigrecords.py b/tests/events/test_rigrecords.py
index 4ce39d4..588ea67 100644
--- a/tests/events/test_rigrecords.py
+++ b/tests/events/test_rigrecords.py
@@ -130,3 +130,41 @@ def test_a_session_without_either_file_has_no_records_and_no_problem(tmp_path):
     from wl_preproc.events.rigrecords import RigRecords, read_rig_records
 
     assert read_rig_records(_record(tmp_path)) == RigRecords(changes=(), controls=(), problems=())
+
+
+def test_since_c3_a_change_line_names_its_kind_its_act_and_its_instant(tmp_path):
+    """Design spec `2026-11-07-rig-record-fields-design.md` section 7: read
+    from the stored line, so no column is added; a line written before C3
+    is a person's value with no act id."""
+    import json
+
+    from wl_preproc.events.rigrecords import ChangeFields, acts, change_fields, read_rig_records
+
+    rows = [{"name": "contrast", "was": 0.5, "now": 0.4, "event_code": 4130},
+            {"name": "contrast", "was": 0.4, "now": 0.5, "kind": "set", "change": 3, "at": 1773478800.5,
+             "trial_number": 12, "meaning": {"went": "harder", "step": 0.1}, "event_code": 4130},
+            {"name": "hold", "was": None, "now": None, "kind": "hand_back", "change": 4, "to": "draw",
+             "event_code": 4130},
+            {"name": "owed", "was": 2, "now": 0, "kind": "clear", "change": 5, "event_code": None,
+             "at": 1773478801.0}]
+    (tmp_path / "xcon").mkdir()
+    (tmp_path / "xcon" / "parameter_changes.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
+    records = read_rig_records(tmp_path)
+    assert [change.at for change in records.changes] == [None, 1773478800.5, None, 1773478801.0]
+    fields, problems = change_fields([change.row for change in records.changes])
+    assert fields[0] == ChangeFields("set", None, None, None, None, None, None, None, None, False)
+    assert (fields[1].change, fields[1].trial_number, fields[1].went, fields[1].step) == (3, 12, "harder", 0.1)
+    assert (fields[2].kind, fields[2].to, fields[3].kind, fields[3].strobed_nothing) == ("hand_back", "draw",
+                                                                                       "clear", True)
+    assert problems == [] and acts(fields) == 3
+
+
+def test_a_change_field_it_cannot_read_is_none_and_said_once():
+    import json
+
+    from wl_preproc.events.rigrecords import change_fields
+
+    fields, problems = change_fields([json.dumps({"name": "a", "change": "3", "meaning": {"step": "big"}})])
+    assert (fields[0].change, fields[0].step, fields[0].kind) == (None, None, "set")
+    assert problems == ["1 change row: `change` is not a whole number or null, so it is read as none",
+                        "1 change row: `meaning.step` is not a finite number or null, so it is read as none"]
diff --git a/tests/events/test_rigtiming.py b/tests/events/test_rigtiming.py
index ada0c4b..22a10ef 100644
--- a/tests/events/test_rigtiming.py
+++ b/tests/events/test_rigtiming.py
@@ -209,3 +209,43 @@ def test_the_measured_runs_give_the_rigs_start_and_end_instants():
     measured = {1: (0.5, 9.5), 2: (10.0, None), 3: (16.0, 18.0)}
     assert run_instants(runs, measured) == [(_rig(0.5), 0.5), (_rig(9.5), 9.5), (_rig(10.0), 10.0),
                                             (_rig(16.0), 16.0)]
+
+
+def _c3_change(line, code=4130, at=None, coded=True):
+    """A change row as wl-xcon's engine build C3 writes it, with its instant."""
+    import dataclasses
+
+    return dataclasses.replace(_change(line, code, coded), at=at)
+
+
+def test_a_change_that_strobed_nothing_is_timed_through_the_rigs_clock():
+    """Design spec `2026-11-07-rig-record-fields-design.md` section 7: a clear
+    or a switch, written to the change log since C3, strobes nothing, and is
+    timed as such a control is."""
+    from wl_preproc.events.rigtiming import time_records
+
+    instants = [(_rig(0.0), 0.0), (_rig(100.0), 100.0)]
+    timing = time_records(_records(changes=[_c3_change(1, code=None, at=_rig(40.0))]), {}, instants)
+    assert timing.changes[0] == pytest.approx(40.0, abs=1e-6)
+    assert timing.problems == ()
+    unfitted = time_records(_records(changes=[_c3_change(1, code=None, at=_rig(40.0))]), {}, [])
+    assert unfitted.changes == (None,)
+    assert unfitted.problems == ("parameter_changes.jsonl: 1 row strobed nothing, and the rig's clock could not be "
+                                 "fitted to the recording (none of its instants matched a code), so it is not timed",)
+
+
+def test_a_coded_change_joins_the_clock_fit_unless_with_it_the_fit_would_fail():
+    """Its instant and its code are one more pair, as a coded control's are;
+    a change whose instant misses its code past the fit's limit is left out
+    of the fit, never leaving untimed what was timed before."""
+    from wl_preproc.events.rigtiming import time_records
+
+    instants = [(_rig(0.0), 0.0), (_rig(100.0), 100.0)]
+    joined = time_records(_records(changes=[_c3_change(1, at=_rig(50.0))]), {4130: [50.0]}, instants)
+    assert joined.clock.n_matched == 3 and joined.problems == ()
+    late = time_records(_records(changes=[_c3_change(1, at=_rig(50.0) + 1.0),
+                                          _c3_change(2, code=None, at=_rig(60.0))]), {4130: [50.0]}, instants)
+    assert late.clock.n_matched == 2
+    assert late.changes[0] == 50.0 and late.changes[1] == pytest.approx(60.0, abs=1e-6)
+    assert late.problems == ("parameter_changes.jsonl: with its rows the rig's clock would not fit (its instants miss "
+                             "their codes by up to 0.667 s, past the 0.05 s limit), so it is fitted without them",)
diff --git a/tests/nwb/test_describe.py b/tests/nwb/test_describe.py
index a170959..316148b 100644
--- a/tests/nwb/test_describe.py
+++ b/tests/nwb/test_describe.py
@@ -173,12 +173,32 @@ def test_the_description_counts_the_training_records_the_file_holds():
     from tests.nwb.test_writers import RIG_RECORD
     from wl_preproc.nwb.describe import describe
 
-    record = {**RIG_RECORD, "parameter_changes": [{"by_kind": "member"}] * 2, "controls": [{"by_kind": "box"}] * 3}
+    from tests.nwb.test_writers import a_set
+
+    record = {**RIG_RECORD, "parameter_changes": [{"by_kind": "member", "fields": a_set()}] * 2,
+              "controls": [{"by_kind": "box"}] * 3}
     out = describe(_gathered(rig_record=record), status="written", n_critical=0, checksums=[CHECKSUM],
                    built_at=BUILT_AT)
     assert out["training_records"] == {"parameter_changes": 2, "controls": 3}
 
 
+def test_the_live_changes_counted_are_a_persons_acts_on_a_setting():
+    """Design spec `2026-11-07-rig-record-fields-design.md` section 7, the
+    requester's decision of 2026-11-07: a revert of two settings is one act;
+    a clear and a switch are not changes of a setting; a row written before
+    wl-xcon's engine build C3, with no act id, is an act of its own."""
+    from tests.nwb.test_writers import RIG_RECORD, a_set
+    from wl_preproc.nwb.describe import describe
+
+    fields = [a_set(), a_set(change=3), a_set(kind="hand_back", change=4), a_set(kind="range", change=5),
+              a_set(kind="revert", change=6), a_set(kind="revert", change=6), a_set(kind="clear", change=7),
+              a_set(kind="drum", change=8), a_set(kind="training", change=9)]
+    record = {**RIG_RECORD, "parameter_changes": [{"fields": these} for these in fields], "controls": []}
+    out = describe(_gathered(rig_record=record), status="written", n_critical=0, checksums=[CHECKSUM],
+                   built_at=BUILT_AT)
+    assert out["training_records"] == {"parameter_changes": 5, "controls": 0}
+
+
 def test_a_file_of_a_session_wl_xcon_did_not_run_describes_no_kind():
     from wl_preproc.nwb.describe import describe
 
diff --git a/tests/nwb/test_writers.py b/tests/nwb/test_writers.py
index 3235bc1..a80556f 100644
--- a/tests/nwb/test_writers.py
+++ b/tests/nwb/test_writers.py
@@ -507,9 +507,9 @@ def test_the_rig_record_holds_its_training_records_with_who_acted_by_kind_only(t
 
     record = {**RIG_RECORD, "parameter_changes": [
         {"session_time": 2.5, "run_number": 1, "name": "reward_ml", "was": "0.2", "now": "0.25", "sequence": 1,
-         "by_kind": "member"},
+         "by_kind": "member", "fields": a_set(), "time_source": "code"},
         {"session_time": None, "run_number": None, "name": "seed", "was": None, "now": "[8, 9]", "sequence": None,
-         "by_kind": None}],
+         "by_kind": None, "fields": a_set(), "time_source": ""}],
         "controls": [
         {"session_time": 4.0, "time_source": "code", "at": 1773478804.0, "kind": "pause", "run_number": 1,
          "trial_index": 1, "detail": "{}", "by_kind": "box"},
@@ -528,6 +528,44 @@ def test_the_rig_record_holds_its_training_records_with_who_acted_by_kind_only(t
         assert controls["rig_at"].iloc[0] == 1773478804.0 and math.isnan(controls["rig_at"].iloc[1])
 
 
+def a_set(**fields):
+    """A change line's fields as `events/rigrecords.py::change_fields` reads
+    them: a person's value, as every row before wl-xcon's engine build C3."""
+    from wl_preproc.events.rigrecords import ChangeFields
+
+    return ChangeFields(**{"kind": "set", "change": None, "trial_number": None, "went": None, "step": None,
+                           "to": None, "from_draw": None, "block": None, "moment": None, "strobed_nothing": False,
+                           **fields})
+
+
+def test_the_change_log_is_published_by_kind_and_act(tmp_path):
+    """Design spec `2026-11-07-rig-record-fields-design.md` section 7: a
+    revert's rows share their act; a clear, which strobed nothing, is timed
+    by the rig's clock; a range's values are draws."""
+    from wl_preproc.nwb.rig_record import add_rig_record
+
+    def change(name, fields, time_source="code"):
+        return {"session_time": 1.0, "run_number": 1, "name": name, "was": "1", "now": "2", "sequence": None,
+                "by_kind": "member", "fields": fields, "time_source": time_source}
+
+    record = {**RIG_RECORD, "parameter_changes": [
+        change("contrast", a_set(change=3, trial_number=12, went="easier", step=-0.1)),
+        change("hold", a_set(kind="range", change=4, from_draw="block", block="easy")),
+        change("contrast", a_set(kind="revert", change=5, moment=0)),
+        change("hold", a_set(kind="revert", change=5, moment=0)),
+        change("owed", a_set(kind="clear", change=6, strobed_nothing=True), "rig_clock")], "controls": []}
+    _path, io, nwb = _write(tmp_path, lambda nwb: add_rig_record(nwb, record))
+    with io:
+        changes = nwb.processing["rig_record"]["parameter_changes"].to_dataframe()
+        assert changes["kind"].tolist() == ["set", "range", "revert", "revert", "clear"]
+        assert changes["change"].tolist() == [3, 4, 5, 5, 6]
+        assert (changes["trial_id"].tolist()[:2], changes["went"].tolist()[0], changes["step"].iloc[0]) == (
+            [12, -1], "easier", -0.1)
+        assert (changes["from_draw"].tolist()[1], changes["block"].tolist()[1], changes["moment"].tolist()) == (
+            "block", "easy", [-1, -1, 0, 0, -1])
+        assert changes["time_source"].tolist() == ["code", "code", "code", "code", "rig_clock"]
+
+
 def test_a_kind_and_calibration_not_recorded_are_empty_and_no_warnings_mean_no_table(tmp_path):
     """A `config.json` written before wl-xcon's engine build B: the session
     row says nothing was recorded, and a session that accepted no warning
PATCH
```

- [ ] **Step 2: Run them and see them fail.** Two writer tests written before this task are in its list: their change rows gain the `fields` and `time_source` this task's gather adds.

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/events/test_rigrecords.py tests/events/test_rigtiming.py tests/nwb/test_writers.py::test_the_rig_record_holds_its_training_records_with_who_acted_by_kind_only tests/nwb/test_writers.py::test_the_change_log_is_published_by_kind_and_act tests/nwb/test_describe.py -q -p no:cacheprovider
```

Expected: `8 failed, 31 passed`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/docs/schemas/nwb_description.json b/docs/schemas/nwb_description.json
index 52f7fa3..43ea895 100644
--- a/docs/schemas/nwb_description.json
+++ b/docs/schemas/nwb_description.json
@@ -1004,7 +1004,7 @@
     },
     "TrainingRecords": {
       "additionalProperties": false,
-      "description": "How many of wl-xcon's training records the file holds: the live\nchanges the rig applied and the console controls it was sent. Who acted\nis not published (design spec\n`2026-10-18-training-records-and-backup-clock-design.md` section 0,\ndecision 6).",
+      "description": "How many of wl-xcon's training records the file holds: the live\nchanges the rig applied and the console controls it was sent. Who acted\nis not published (design spec\n`2026-10-18-training-records-and-backup-clock-design.md` section 0,\ndecision 6). Since 2026-11-07 `parameter_changes` counts a person's acts\non a setting, one per act among a set, a hand-back, a range edit and a\nrevert, not the change log's rows (design spec\n`2026-11-07-rig-record-fields-design.md` section 7).",
       "properties": {
         "controls": {
           "title": "Controls",
diff --git a/docs/schemas/session_listing.json b/docs/schemas/session_listing.json
index b3db94f..49e6e10 100644
--- a/docs/schemas/session_listing.json
+++ b/docs/schemas/session_listing.json
@@ -454,7 +454,7 @@
     },
     "ListedTrainingRecords": {
       "additionalProperties": false,
-      "description": "How many of wl-xcon's training records the session has: its live\nparameter changes, and of its console controls the pauses and the manual\nrewards. The rows are in the database and the NWB file, not here (design\nspec `2026-10-18-training-records-and-backup-clock-design.md` section 3.4).",
+      "description": "How many of wl-xcon's training records the session has: its live\nparameter changes, and of its console controls the pauses and the manual\nrewards. The rows are in the database and the NWB file, not here (design\nspec `2026-10-18-training-records-and-backup-clock-design.md` section 3.4).\nSince 2026-11-07 `parameter_changes` counts a person's acts on a setting,\none per act among a set, a hand-back, a range edit and a revert; a clear\nand a switch are not counted (design spec\n`2026-11-07-rig-record-fields-design.md` section 7).",
       "properties": {
         "manual_rewards": {
           "title": "Manual Rewards",
diff --git a/wl_preproc/contracts/nwb_description.py b/wl_preproc/contracts/nwb_description.py
index 3e87c55..b8ea4dd 100644
--- a/wl_preproc/contracts/nwb_description.py
+++ b/wl_preproc/contracts/nwb_description.py
@@ -217,7 +217,10 @@ class TrainingRecords(_Frozen):
     changes the rig applied and the console controls it was sent. Who acted
     is not published (design spec
     `2026-10-18-training-records-and-backup-clock-design.md` section 0,
-    decision 6)."""
+    decision 6). Since 2026-11-07 `parameter_changes` counts a person's acts
+    on a setting, one per act among a set, a hand-back, a range edit and a
+    revert, not the change log's rows (design spec
+    `2026-11-07-rig-record-fields-design.md` section 7)."""
 
     parameter_changes: int
     controls: int
diff --git a/wl_preproc/contracts/protocol.py b/wl_preproc/contracts/protocol.py
index 77d7ec6..ab591f5 100644
--- a/wl_preproc/contracts/protocol.py
+++ b/wl_preproc/contracts/protocol.py
@@ -531,7 +531,11 @@ class ListedTrainingRecords(BaseModel):
     """How many of wl-xcon's training records the session has: its live
     parameter changes, and of its console controls the pauses and the manual
     rewards. The rows are in the database and the NWB file, not here (design
-    spec `2026-10-18-training-records-and-backup-clock-design.md` section 3.4)."""
+    spec `2026-10-18-training-records-and-backup-clock-design.md` section 3.4).
+    Since 2026-11-07 `parameter_changes` counts a person's acts on a setting,
+    one per act among a set, a hand-back, a range edit and a revert; a clear
+    and a switch are not counted (design spec
+    `2026-11-07-rig-record-fields-design.md` section 7)."""
 
     model_config = ConfigDict(extra="forbid", frozen=True)
 
diff --git a/wl_preproc/events/rigrecords.py b/wl_preproc/events/rigrecords.py
index 060c2d7..84bce0b 100644
--- a/wl_preproc/events/rigrecords.py
+++ b/wl_preproc/events/rigrecords.py
@@ -20,7 +20,17 @@ it has no key, which is read as unknown, never as null; so is a value that is
 not a 16-bit code. `events/rigtiming.py` joins the rows to the recording by it.
 
 **This session's only:** the caller reads these for a session whose
-`config.json` is its own (`events/rigsession.py`'s `of_this_session`)."""
+`config.json` is its own (`events/rigsession.py`'s `of_this_session`).
+
+**The change log by kind** (design spec `2026-11-07-rig-record-fields-design.md`
+section 7): since wl-xcon's engine build C3 a change row names its `kind`
+(`set`, `hand_back`, `range`, `revert`, `clear`, `drum`, `training`; a row
+written before has none, and is a `set`), the act it belongs to (`change`, a
+revert's rows sharing one), the first trial it applies to, which way it went,
+the kind's own fields, and its instant (`at`). Its instant times a row that
+strobed nothing through the rig's clock fit, as a control's does; the rest is
+read from the stored line when the file is built (`change_fields`), so no
+column is added. `acts` counts a person's acts on a setting."""
 
 from __future__ import annotations
 
@@ -30,6 +40,7 @@ import math
 from pathlib import Path
 
 from wl_preproc.contracts.paths import XCON_DIRNAME
+from wl_preproc.events.rigfields import NUMBER, TEXT, WHOLE, Fields
 
 CHANGES_NAME = "parameter_changes.jsonl"
 CONTROLS_NAME = "controls.jsonl"
@@ -60,6 +71,7 @@ class ParameterChange:
     by_issuer: str | None
     by_token_id: str | None
     row: str
+    at: float | None = None  # its instant on the rig's anchored clock, since C3
 
 
 @dataclasses.dataclass(frozen=True, slots=True)
@@ -168,7 +180,8 @@ def read_rig_records(session_dir: Path) -> RigRecords:
         changes.append(ParameterChange(
             line=line, sequence=_count(row.get("sequence")), name=row["name"],
             was=_json(row["was"]) if "was" in row else None, now=_json(row["now"]) if "now" in row else None,
-            run_index=_integer(row.get("run")), event_code=code, coded=coded, **_actor(row), row=text))
+            run_index=_integer(row.get("run")), event_code=code, coded=coded, **_actor(row), row=text,
+            at=_number(row.get("at"))))
     controls = []
     for line, text, row in _rows(xcon / CONTROLS_NAME, problems, "kind", "not a control naming its kind"):
         code, coded = _code(row)
@@ -178,3 +191,61 @@ def read_rig_records(session_dir: Path) -> RigRecords:
             coded=coded, detail=_json({key: value for key, value in row.items() if key not in _CONTROL_FIELDS}),
             **_actor(row), row=text))
     return RigRecords(changes=tuple(changes), controls=tuple(controls), problems=tuple(problems))
+
+
+# The kinds that change a setting, as a person's act; a clear and the two
+# switches are not (the requester's decision of 2026-11-07).
+SETTING_KINDS = ("set", "hand_back", "range", "revert")
+
+
+@dataclasses.dataclass(frozen=True, slots=True)
+class ChangeFields:
+    """What a stored change line says beyond its setting and values (C3):
+    `kind` (`set` for a row written before C3), the act's id, the first trial
+    it applies to, which way it went and by how much, a hand-back's `to`, a
+    range's draw (`from`) and `block`, a revert's `moment` (0 the run's
+    start), and whether its act strobed nothing."""
+
+    kind: str
+    change: int | None
+    trial_number: int | None
+    went: str | None
+    step: float | None
+    to: str | None
+    from_draw: str | None
+    block: str | None
+    moment: int | None
+    strobed_nothing: bool
+
+
+def change_fields(rows: list[str]) -> tuple[list[ChangeFields], list[str]]:
+    """Each stored line's `ChangeFields`, and what could not be read, once
+    per field (`events/rigfields.py`'s rule)."""
+    fields = Fields(("change row", "change rows"))
+    out = []
+    for text in rows:
+        row = json.loads(text)
+        meaning = row.get("meaning") if isinstance(row.get("meaning"), dict) else {}
+        kind = fields.read(row, "kind", TEXT) or "set"
+        out.append(ChangeFields(
+            kind=kind, change=fields.read(row, "change", WHOLE), trial_number=fields.read(row, "trial_number", WHOLE),
+            went=fields.read(meaning, "went", TEXT, field="meaning.went"),
+            step=fields.read(meaning, "step", NUMBER, field="meaning.step"), to=fields.read(row, "to", TEXT),
+            from_draw=fields.read(row, "from", TEXT), block=fields.read(row, "block", TEXT),
+            moment=fields.read(row, "moment", WHOLE),
+            strobed_nothing="event_code" in row and row["event_code"] is None))
+    return out, fields.problems()
+
+
+def acts(changes: list[ChangeFields]) -> int:
+    """A person's acts on a setting: one per act id among rows of
+    `SETTING_KINDS`, a row with no id (written before C3) an act of its own."""
+    ids, alone = set(), 0
+    for change in changes:
+        if change.kind not in SETTING_KINDS:
+            continue
+        if change.change is None:
+            alone += 1
+        else:
+            ids.add(change.change)
+    return len(ids) + alone
diff --git a/wl_preproc/events/rigtiming.py b/wl_preproc/events/rigtiming.py
index 5d6b57d..43418d1 100644
--- a/wl_preproc/events/rigtiming.py
+++ b/wl_preproc/events/rigtiming.py
@@ -16,7 +16,13 @@ measured `RUN_START`, its end row to its `RUN_END` when it closed, and each
 control timed by its code to that code -- they fit the rig's clock as a rate and
 an offset, residuals kept. A control that strobed nothing (a note, a schedule,
 a pause its allocation has no code for) is timed through the fit, and is
-otherwise untimed. Fewer than two matches, a residual past `RIG_CLOCK_LIMIT_S`,
+otherwise untimed. **So is a change row** (design spec
+`2026-11-07-rig-record-fields-design.md` section 7): since wl-xcon's engine
+build C3 each carries its instant, so a clear or a switch, which strobes
+nothing, is timed through the fit, and one its code times joins the fit's
+pairs, as a coded control does, **unless with them the fit would fail**: then
+it is made without them, and a problem says so, so no session timed before is
+left untimed by them. Fewer than two matches, a residual past `RIG_CLOCK_LIMIT_S`,
 or a rate no clock runs at (`RIG_CLOCK_DRIFT_LIMIT_PPM`), and there is no fit:
 a resumed session's process anchors the rig's clock again (wl-xcon's
 `welfare.SessionClock`), which one line cannot follow.
@@ -168,11 +174,20 @@ def time_records(records: RigRecords, codes: Mapping[int, list[float]],
                  instants: Sequence[tuple[float, float]]) -> Timing:
     """Each record's session time. `codes` is `code_times` of the session's
     decoded codes; `instants` is `run_instants` of its runs."""
-    change_times, change_problems = _join(records.changes, codes, CHANGES_NAME)
+    coded_changes, change_problems = _join(records.changes, codes, CHANGES_NAME)
     coded_times, control_problems = _join(records.controls, codes, CONTROLS_NAME)
     pairs = [*instants, *((control.at, time) for control, time in zip(records.controls, coded_times, strict=True)
                           if time is not None and control.at is not None)]
-    fitted = fit_rig_clock(pairs)
+    changed = [(change.at, time) for change, time in zip(records.changes, coded_changes, strict=True)
+               if time is not None and change.at is not None]
+    fitted = fit_rig_clock([*pairs, *changed])
+    fit_problems = []
+    if changed and not isinstance(fitted, RigClock):
+        without = fit_rig_clock(pairs)
+        if isinstance(without, RigClock):
+            fit_problems.append(f"{CHANGES_NAME}: with its rows the rig's clock would not fit ({fitted}), so it is "
+                                "fitted without them")
+            fitted = without
     clock = fitted if isinstance(fitted, RigClock) else None
     controls, unfitted = [], 0
     for control, time in zip(records.controls, coded_times, strict=True):
@@ -184,9 +199,17 @@ def time_records(records: RigRecords, codes: Mapping[int, list[float]],
         else:
             unfitted += strobed_nothing
             controls.append((None, None))
-    problems = [*change_problems, *control_problems]
-    if unfitted:
-        one = unfitted == 1
-        problems.append(f"{CONTROLS_NAME}: {_rows(unfitted)} strobed nothing, and the rig's clock could not be fitted "
-                        f"to the recording ({fitted}), so {'it is' if one else 'they are'} not timed")
+    change_times, changes_unfitted = [], 0
+    for change, time in zip(records.changes, coded_changes, strict=True):
+        strobed_nothing = change.coded and change.event_code is None and change.at is not None
+        if time is None and strobed_nothing and clock is not None:
+            time = clock.session_time(change.at)
+        changes_unfitted += time is None and strobed_nothing
+        change_times.append(time)
+    problems = [*change_problems, *control_problems, *fit_problems]
+    for name, n in ((CONTROLS_NAME, unfitted), (CHANGES_NAME, changes_unfitted)):
+        if n:
+            one = n == 1
+            problems.append(f"{name}: {_rows(n)} strobed nothing, and the rig's clock could not be fitted "
+                            f"to the recording ({fitted}), so {'it is' if one else 'they are'} not timed")
     return Timing(changes=tuple(change_times), controls=tuple(controls), clock=clock, problems=tuple(problems))
diff --git a/wl_preproc/listing/entry.py b/wl_preproc/listing/entry.py
index 7110b93..fb11468 100644
--- a/wl_preproc/listing/entry.py
+++ b/wl_preproc/listing/entry.py
@@ -22,6 +22,7 @@ import dataclasses
 import datetime
 from pathlib import Path
 
+from wl_preproc.events.rigrecords import acts, change_fields
 from wl_preproc.events.runs import starts_inside, stored_doubles
 
 from wl_preproc.contracts.protocol import SessionEntry
@@ -56,7 +57,7 @@ class SessionFacts:
     # How many of wl-xcon's training records it has, and the clock that kept
     # its time (design spec `2026-10-18-training-records-and-backup-clock-design.md`
     # section 3.4).
-    parameter_changes: int  # its core.ParameterChange rows
+    parameter_changes: int  # a person's acts on a setting among its core.ParameterChange rows
     control_kinds: dict[str, int]  # control kind -> its core.RigControl rows
     clock: str | None  # timebase.SessionClock's source; None until chosen
 
@@ -270,9 +271,12 @@ def gather_all(keys: list[dict]) -> dict[tuple[str, datetime.datetime], SessionF
         "session_kind", *_CALIBRATION_COLUMNS).to_dicts()}
     run_calibrations = _by_session((core.RunCalibration & keys).to_dicts())
     warnings = _by_session((core.AcceptedWarning & keys).to_dicts(order_by="warning_number"))
-    # Counted by the database, since the rows are not listed.
-    changes = {_session(row): int(row["n"]) for row in (pipeline.Session & keys).aggr(
-        core.ParameterChange, n="count(*)", exclude_nonmatching=True).to_dicts()}
+    # A person's acts on a setting, read from each row's stored line (design
+    # spec `2026-11-07-rig-record-fields-design.md` section 7).
+    lines = collections.defaultdict(list)
+    for row in (core.ParameterChange & keys).proj("row").to_dicts(order_by="change_line"):
+        lines[_session(row)].append(row["row"])
+    changes = {session: acts(change_fields(rows)[0]) for session, rows in lines.items()}
     control_kinds = collections.defaultdict(dict)
     for row in dj.U("subject", "session_datetime", "kind").aggr(core.RigControl & keys, n="count(*)").to_dicts():
         control_kinds[_session(row)][row["kind"]] = int(row["n"])
diff --git a/wl_preproc/nwb/describe.py b/wl_preproc/nwb/describe.py
index 66f7564..b95f963 100644
--- a/wl_preproc/nwb/describe.py
+++ b/wl_preproc/nwb/describe.py
@@ -53,7 +53,11 @@ def _rig_record(record: dict | None) -> dict:
     kind: the calibration without its colour model, and each warning by its
     code and run (design spec `2026-10-14-session-kind-and-warnings-design.md`
     section 5); and how many training records the file holds (design spec
-    `2026-10-18-training-records-and-backup-clock-design.md` section 3.2)."""
+    `2026-10-18-training-records-and-backup-clock-design.md` section 3.2), its
+    live parameter changes counted as a person's acts on a setting (design
+    spec `2026-11-07-rig-record-fields-design.md` section 7)."""
+    from wl_preproc.events.rigrecords import acts
+
     if record is None:
         return {"session_kind": None, "calibration": None, "accepted_warnings": [], "training_records": None}
     calibration = record["calibration"]
@@ -63,7 +67,7 @@ def _rig_record(record: dict | None) -> dict:
             name: calibration[name] for name in ("id", "standard", "measured_on")},
         "accepted_warnings": [{"code": warning["code"], "run_number": warning["run_number"]}
                               for warning in record["warnings"]],
-        "training_records": {"parameter_changes": len(record["parameter_changes"]),
+        "training_records": {"parameter_changes": acts([change["fields"] for change in record["parameter_changes"]]),
                              "controls": len(record["controls"])},
     }
 
diff --git a/wl_preproc/nwb/gather.py b/wl_preproc/nwb/gather.py
index f0a845d..8604bb4 100644
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -96,7 +96,11 @@ def _rig_record(session_key: dict, run_numbers: set[int]) -> dict | None:
     training records are chosen by the same rule (design spec
     `2026-10-18-training-records-and-backup-clock-design.md`, amendment 10).
     Who acted is read by kind only: the file names no one (decision 3 of the
-    first; decision 6 of the second)."""
+    first; decision 6 of the second). Each change's kind, act and the rest of
+    what wl-xcon's engine build C3 adds are read from its stored line (design
+    spec `2026-11-07-rig-record-fields-design.md` section 7), and what could
+    not be read is in `change_notes`."""
+    from wl_preproc.events.rigrecords import change_fields
     from wl_preproc.schema import core
 
     def the_files(row: dict) -> bool:
@@ -106,6 +110,9 @@ def _rig_record(session_key: dict, run_numbers: set[int]) -> dict | None:
     if not rows:
         return None
     (row,) = rows
+    changes = [change for change in (core.ParameterChange & session_key).to_dicts(order_by="change_line")
+               if the_files(change)]
+    fields, change_notes = change_fields([change["row"] for change in changes])
     recorded = any(row[column] is not None for column in ("calibration_id", "calibration_standard",
                                                           "calibration_measured_on"))
     return {
@@ -120,9 +127,13 @@ def _rig_record(session_key: dict, run_numbers: set[int]) -> dict | None:
                      for warning in (core.AcceptedWarning & session_key).to_dicts(order_by="warning_number")
                      if the_files(warning)],
         "parameter_changes": [
-            {name: change[name] for name in ("session_time", "run_number", "name", "was", "now", "sequence",
-                                             "by_kind")}
-            for change in (core.ParameterChange & session_key).to_dicts(order_by="change_line") if the_files(change)],
+            {**{name: change[name] for name in ("session_time", "run_number", "name", "was", "now", "sequence",
+                                                "by_kind")},
+             "fields": these,
+             "time_source": "" if change["session_time"] is None else (
+                 "rig_clock" if these.strobed_nothing else "code")}
+            for change, these in zip(changes, fields, strict=True)],
+        "change_notes": change_notes,
         "controls": [
             {name: control[name] for name in ("session_time", "time_source", "at", "kind", "run_number",
                                               "trial_index", "detail", "by_kind")}
@@ -739,6 +750,9 @@ def gather(activation_key: dict) -> Gathered:
     run_tables, run_notes = run_record(run_out, blocks, rig_runs, places(record))
     rig_tables.update(run_tables)
     condition_notes += run_notes
+    rig_record = _rig_record(session_key, {row["run_number"] for row in run_rows})
+    if rig_record is not None:
+        condition_notes += rig_record["change_notes"]
     return Gathered(
         session={
             "identifier": identifier_for(key, session_id),
@@ -768,6 +782,6 @@ def gather(activation_key: dict) -> Gathered:
         probes=probes,
         probe_notes=probe_notes,
         trial_notes=_trial_notes(session_key, run_rows) + _outside_runs_notes(key, session_key, activation["role"]),
-        rig_record=_rig_record(session_key, {row["run_number"] for row in run_rows}),
+        rig_record=rig_record,
         rig_tables=rig_tables,
     )
diff --git a/wl_preproc/nwb/rig_record.py b/wl_preproc/nwb/rig_record.py
index c824c9e..a30a579 100644
--- a/wl_preproc/nwb/rig_record.py
+++ b/wl_preproc/nwb/rig_record.py
@@ -182,8 +182,11 @@ def _add_warnings(module, warnings: list[dict]) -> None:
 
 
 def _add_changes(module, changes: list[dict]) -> None:
+    fields = [change["fields"] for change in changes]
     module.add(DynamicTable(name="parameter_changes", description=(
-        "Each live change the rig applied, from wl-xcon's parameter_changes.jsonl, timed by the code it strobed."),
+        "Each change the rig applied to what the animal experienced, from wl-xcon's parameter_changes.jsonl, by its "
+        "kind and the act it belongs to, timed by the code it strobed or, for one that strobed nothing, by the rig's "
+        "clock fitted to the recording."),
         columns=[
             column("session_time", "When, in session seconds: its code's time (NaN if untimed).",
                    [_time(change["session_time"]) for change in changes]),
@@ -198,6 +201,27 @@ def _add_changes(module, changes: list[dict]) -> None:
                    [-1 if change["sequence"] is None else change["sequence"] for change in changes]),
             column("by_kind", "Who made it, by kind only: box or member ('' if not recorded).",
                    [change["by_kind"] or "" for change in changes]),
+            column("kind", "set (a person's value), hand_back, range (a draw's numbers: was and now are draws), "
+                           "revert, clear, drum or training; a row written before wl-xcon's engine build C3 is a "
+                           "set.", [field.kind for field in fields]),
+            column("change", "The act it belongs to, an id a revert's rows share (-1 if not recorded).",
+                   [-1 if field.change is None else field.change for field in fields]),
+            column("trial_id", "The first trial it applies to (-1 if not recorded).",
+                   [-1 if field.trial_number is None else field.trial_number for field in fields]),
+            column("went", "Which way it went by the setting's declared meaning: easier or harder ('' where that "
+                           "cannot be read, or not recorded).", [field.went or "" for field in fields]),
+            column("step", "now minus was, in the setting's unit (NaN where either is not a number).",
+                   [math.nan if field.step is None else field.step for field in fields]),
+            column("to", "What takes a handed-back setting in the open block: conditions, draw or run ('' "
+                         "otherwise).", [field.to or "" for field in fields]),
+            column("from_draw", "Whose draw a range changed or a revert restored: task or block ('' otherwise).",
+                   [field.from_draw or "" for field in fields]),
+            column("block", "That block type, where it is a block's own draw ('' otherwise).",
+                   [field.block or "" for field in fields]),
+            column("moment", "The act a revert goes back to, 0 for the run's start (-1 otherwise).",
+                   [-1 if field.moment is None else field.moment for field in fields]),
+            column("time_source", "How its time was found: code, or rig_clock for the rig's clock fit ('' if "
+                                  "untimed).", [change["time_source"] for change in changes]),
         ]))
 
 
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/events/test_rigrecords.py tests/events/test_rigtiming.py tests/nwb/test_writers.py::test_the_rig_record_holds_its_training_records_with_who_acted_by_kind_only tests/nwb/test_writers.py::test_the_change_log_is_published_by_kind_and_act tests/nwb/test_describe.py -q -p no:cacheprovider
```

Expected: `39 passed`

- [ ] **Step 5: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/events tests/nwb tests/listing tests/responder tests/schema/test_training_records.py tests/schema/test_events.py tests/schema/test_session_listing.py tests/schema/test_nwb_rig_record.py tests/cli/test_schemas_export.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `477 passed`

- [ ] **Step 6: Commit.**

```bash
git add -A tests wl_preproc docs
git commit -F - <<'MSG'
feat(events): the change log read by kind and act -- each row's kind, act, first trial, way and step, and a kind's own fields, read from its stored line and published; a clear or a switch, which strobes nothing, timed through the rig's clock fit, which a coded change's instant joins unless it would fail the fit; wl.works' count of live parameter changes becomes a person's acts on a setting (section 7, the requester's decision of 2026-11-07)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

- [ ] **Step 7: The mutation checks,** on the commit. Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "a row before C3 is a clear",
  "wl_preproc/events/rigrecords.py",
  "        kind = fields.read(row, \"kind\", TEXT) or \"set\"\n",
  "        kind = fields.read(row, \"kind\", TEXT) or \"clear\"\n",
  "tests/events/test_rigrecords.py tests/events/test_rigtiming.py tests/nwb/test_writers.py tests/nwb/test_describe.py"
 ],
 [
  "a clear is a person's act",
  "wl_preproc/events/rigrecords.py",
  "        if change.kind not in SETTING_KINDS:\n            continue\n",
  "",
  "tests/events/test_rigrecords.py tests/events/test_rigtiming.py tests/nwb/test_writers.py tests/nwb/test_describe.py"
 ],
 [
  "a change that strobed nothing is untimed",
  "wl_preproc/events/rigtiming.py",
  "        if time is None and strobed_nothing and clock is not None:\n",
  "        if False:\n",
  "tests/events/test_rigrecords.py tests/events/test_rigtiming.py tests/nwb/test_writers.py tests/nwb/test_describe.py"
 ],
 [
  "a change that fails the fit fails it",
  "wl_preproc/events/rigtiming.py",
  "    if changed and not isinstance(fitted, RigClock):\n",
  "    if False:\n",
  "tests/events/test_rigrecords.py tests/events/test_rigtiming.py tests/nwb/test_writers.py tests/nwb/test_describe.py"
 ],
 [
  "the description counts rows",
  "wl_preproc/nwb/describe.py",
  "acts([change[\"fields\"] for change in record[\"parameter_changes\"]])",
  "len(record[\"parameter_changes\"])",
  "tests/events/test_rigrecords.py tests/events/test_rigtiming.py tests/nwb/test_writers.py tests/nwb/test_describe.py"
 ]
]
```

Expected: 5 lines, each `CAUGHT`; `git status --short` shows no change.

---

### Task 6: The synthetic rig writes every new field, and the build carries them end to end

§9 and §10, amendments 7 and 8: `RigRecordSpec.fields` makes the synthetic rig write each trial line, run row and change row in C2a's, C2b's, C3's and XC-399's shape, every existing profile byte-identical without it; a session with them through the daemon and the builder, into the file, its description and the listing; an appearance pinned as its sorted JSON text; a condition name too long for the main sequence noted.

**Files:**
- Modify: `wl_preproc/synth/recipe.py` (`RigChangeSpec.kind`, `RigRecordSpec.fields`), `wl_preproc/synth/peripherals.py` (`_place`, `_stimuli`, `_trial_fields`, `_start_fields`, `_end_fields`, `_c3_changes`)
- Modify: `wl_preproc/nwb/conditions.py` (`long_names`), `wl_preproc/nwb/gather.py`, `wl_preproc/schema/main_sequence.py` (`CONDITION_WIDTH`)
- Create: `tests/schema/test_nwb_rig_fields.py`
- Test: `tests/synth/test_peripherals.py`, `tests/nwb/test_conditions.py`, `tests/events/test_rigrecords.py`

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces: `RigRecordSpec.fields: bool = False`, `RigChangeSpec.kind: str = "set"`;
  `conditions.long_names(matched, width) -> list[str]`; `main_sequence.CONDITION_WIDTH`.

- [ ] **Step 1: Write the failing tests.**

```bash
git apply <<'PATCH'
diff --git a/tests/events/test_rigrecords.py b/tests/events/test_rigrecords.py
index 588ea67..fc4bc47 100644
--- a/tests/events/test_rigrecords.py
+++ b/tests/events/test_rigrecords.py
@@ -159,6 +159,24 @@ def test_since_c3_a_change_line_names_its_kind_its_act_and_its_instant(tmp_path)
     assert problems == [] and acts(fields) == 3
 
 
+def test_an_appearance_in_a_change_is_kept_as_its_sorted_json_text(tmp_path):
+    """wl-xcon's appearance ask (design spec
+    `2026-11-07-rig-record-fields-design.md` section 9): a change of
+    appearance's `was` and `now` are the objects' text, keys sorted, a
+    setting it refers to kept as its reference."""
+    import json
+
+    from wl_preproc.events.rigrecords import read_rig_records
+
+    disc = {"class": "Disc", "fields": {"size": 1.0, "color": {"class": "P", "fields": {"name": "lum"}}}}
+    row = {"name": "target_appearance", "was": disc, "now": {**disc, "class": "Square"}, "event_code": 4130}
+    (tmp_path / "xcon").mkdir()
+    (tmp_path / "xcon" / "parameter_changes.jsonl").write_text(json.dumps(row) + "\n")
+    (change,) = read_rig_records(tmp_path).changes
+    assert change.was == json.dumps(disc, sort_keys=True)
+    assert json.loads(change.now)["class"] == "Square"
+
+
 def test_a_change_field_it_cannot_read_is_none_and_said_once():
     import json
 
diff --git a/tests/nwb/test_conditions.py b/tests/nwb/test_conditions.py
index 213a74e..cd85d67 100644
--- a/tests/nwb/test_conditions.py
+++ b/tests/nwb/test_conditions.py
@@ -172,3 +172,39 @@ def test_the_arc_leaves_out_the_widest_gap():
     assert arc([350.0, 20.0, 5.0]) == {"from": 350.0, "to": 20.0}
     assert arc([0.0, 90.0, 180.0, 270.0]) == {"from": 90.0, "to": 0.0}
     assert arc([100.0, 200.0]) == {"from": 100.0, "to": 200.0}
+
+
+_DISC = {"class": "Disc", "fields": {"color": {"class": "Gray", "fields": {"cd_m2": {"class": "P", "fields": {
+    "name": "target_luminance"}}}}, "contrast": None, "size": 1.0}}
+
+
+def test_an_appearance_is_published_as_its_objects_sorted_json_text():
+    """wl-xcon's appearance ask, its form accepted on 2026-10-30 (design spec
+    `2026-11-07-rig-record-fields-design.md` section 9): an appearance in
+    `params` is its object's text with sorted keys in the trials table, and
+    a condition's constant or varying values keep it whole."""
+    import json
+
+    from wl_preproc.nwb.conditions import block_conditions, trial_columns
+
+    square = {**_DISC, "class": "Square"}
+    trials = [_trial(1, 0.0), _trial(2, 1.0), _trial(3, 2.0)]
+    matched = {1: _rig(1, "disc", target_appearance=_DISC), 2: _rig(2, "disc", target_appearance=_DISC),
+               3: _rig(3, "square", target_appearance=square)}
+    _names, settings = trial_columns(trials, matched, {})
+    assert settings["target_appearance"][0] == json.dumps(_DISC, sort_keys=True)
+    disc, square_entry = sorted(block_conditions(trials, matched, {}), key=lambda entry: entry["name"])
+    assert disc["settings"]["target_appearance"] == _DISC and square_entry["settings"]["target_appearance"] == square
+
+
+def test_a_condition_name_too_long_for_the_main_sequence_is_noted():
+    """Section 9: a large factorial's generated name past 255 characters
+    gets no main-sequence group; the file says so rather than the column
+    widening."""
+    from wl_preproc.nwb.conditions import long_names
+
+    matched = {1: _rig(1, "x" * 256), 2: _rig(2, "x" * 255), 3: _rig(3, "y" * 300)}
+    assert long_names(matched, 255) == [
+        "2 trial(s) name a condition longer than 255 characters, so the main sequence groups none of them by "
+        "condition"]
+    assert long_names({2: _rig(2, "x" * 255)}, 255) == []
diff --git a/tests/schema/test_nwb_rig_fields.py b/tests/schema/test_nwb_rig_fields.py
new file mode 100644
index 0000000..e3b2af0
--- /dev/null
+++ b/tests/schema/test_nwb_rig_fields.py
@@ -0,0 +1,118 @@
+"""The rig's record, read whole, through the builder (design spec
+`2026-11-07-rig-record-fields-design.md` sections 3-8 and 10): what each trial
+paid and where its stimuli were, how each run and block ended and what each
+run owed, a condition's own values apart from drawn and person-held ones, and
+the change log by kind and act, in the NWB file, its description and the
+listing. Subject and date checked unclaimed across `tests/` on 2026-11-07, the
+date in the PAST, since `nwbinspector` calls a future `session_start_time`
+critical."""
+
+from __future__ import annotations
+
+import pytest
+
+from tests.schema.test_nwb_probes import _canonical
+from tests.schema.test_spikeglx_restart import _session as _restart_session
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
+    """Two runs whose rig wrote every field of wl-xcon's engine builds C2a,
+    C2b and C3 and its XC-399 (`synth/peripherals.py`, `fields`): a person's
+    value strobed in trial 1, a clear (strobing nothing) in trial 2, and a
+    pause."""
+    recipe, key = _restart_session(
+        tmp_path_factory, None, runs=True, subject="rfnwb1", session_id="2025-07-01_01",
+        rig_record={"fields": True, "controls": [{"trial": 2}],
+                    "changes": [{"trial": 1}, {"trial": 2, "kind": "clear", "name": "owed", "was": 2, "now": 0,
+                                               "event_code": None}]})
+    daemon_module.run_once(prefix=prefix)
+    return recipe, key, _canonical(daemon_module, prefix, key, "rfnwb1-k1", [])
+
+
+@pytest.fixture(scope="module")
+def built(recorded, tmp_path_factory):
+    from wl_preproc.nwb.build import build
+
+    return build(recorded[2], tmp_path_factory.mktemp("nwb-rig-fields"))
+
+
+def _file(built):
+    from pynwb import NWBHDF5IO
+
+    return NWBHDF5IO(str(built.path), "r")
+
+
+def test_each_trial_says_what_it_paid_and_where_its_stimuli_were(built):
+    with _file(built) as io:
+        nwb = io.read()
+        trials = nwb.trials.to_dataframe()
+        first = trials[trials["run_number"] == 1]
+        assert first["counting"].tolist() == ["counted", "owed", "counted"]
+        assert first["repeat_of"].tolist() == [-1, int(first["trial_id"].iloc[0]), -1]
+        assert first["planned_lead_in"].tolist() == [1, 0, 0]
+        assert first["live"].tolist() == ["", "", "contrast"] and first["fluid_ml"].tolist() == [0.1, 0.1, 0.1]
+        stimuli = nwb.processing["rig_record"]["stimuli"].to_dataframe()
+        assert stimuli["stimulus"].value_counts().to_dict() == {"fix": 4, "target": 4, "search": 1}
+        target = stimuli[stimuli["stimulus"] == "target"].iloc[0]
+        # The angles pass through atan2, which Linux and macOS round apart in
+        # the last places (the v3 fixture's CI failure of 2026-11-07).
+        assert (target["x_deg"], target["y_deg"]) == (0.0, 5.0)
+        assert target["polar_deg"] == pytest.approx(90.0, abs=1e-9)
+        objects = nwb.processing["rig_record"]["stimulus_objects"].to_dataframe()
+        assert objects["x_deg"].tolist() == [8.0, -8.0]
+
+
+def test_each_run_and_block_says_how_it_ended_and_what_it_owed(built):
+    with _file(built) as io:
+        nwb = io.read()
+        runs = nwb.intervals["runs"].to_dataframe()
+        assert runs["plan"].tolist() == ["synthetic", "synthetic"]
+        assert runs["end_reason"].tolist() == ["plan_done", "plan_done"]
+        assert runs["stop_kind"].tolist() == ["completed", "completed"]
+        assert runs["task_hash"].tolist() == ["sha256:synthetic", "sha256:synthetic"]
+        blocks = nwb.intervals["blocks"].to_dataframe()
+        assert blocks["ended"].tolist() == ["rule", "rule"]
+        counts = nwb.processing["rig_record"]["condition_counts"].to_dataframe()
+        trials = nwb.trials.to_dataframe()
+        for run in (1, 2):
+            assert (set(counts[counts["run_number"] == run]["condition"])
+                    == set(trials[trials["run_number"] == run]["condition"]))
+
+
+def test_the_change_log_is_read_by_kind_and_a_clear_is_timed_by_the_rigs_clock(recorded, built):
+    from wl_preproc.schema import core
+
+    _recipe, key, _activation = recorded
+    with _file(built) as io:
+        changes = io.read().processing["rig_record"]["parameter_changes"].to_dataframe()
+        assert changes["kind"].tolist() == ["set", "clear"]
+        assert changes["time_source"].tolist() == ["code", "rig_clock"]
+    stored = (core.ParameterChange & key).to_dicts(order_by="change_line")
+    assert all(row["session_time"] is not None for row in stored)
+
+
+def test_the_description_and_the_listing_say_it(recorded, built):
+    """A condition's own values apart from drawn and held ones; each run's
+    plan and end; the count of a person's acts, the clear not among them."""
+    from wl_preproc.listing.entry import session_entry
+
+    description = built.description
+    first = description["runs"][0]
+    assert (first["plan"], first["end_reason"], first["blocks"][0]["ended"]) == ("synthetic", "plan_done", "rule")
+    held = [condition for condition in first["conditions"] if condition["live"]]
+    assert held and all(condition["trials"]["eased"] >= 1 for condition in held)
+    assert all("fix_hold_s" in condition["drawn"] for condition in first["conditions"])
+    assert all("fix_hold_s" not in (condition["settings"] or {}) for condition in first["conditions"])
+    assert description["training_records"]["parameter_changes"] == 1
+    assert not [note for note in description["notes"] if "rig's" in note or "stimulus" in note]
+    _recipe, key, _activation = recorded
+    assert session_entry(key)["training_records"]["parameter_changes"] == 1
diff --git a/tests/synth/test_peripherals.py b/tests/synth/test_peripherals.py
index 10bcb0d..9853a74 100644
--- a/tests/synth/test_peripherals.py
+++ b/tests/synth/test_peripherals.py
@@ -337,3 +337,45 @@ def test_the_config_records_this_rigs_display_setup_unless_a_recipe_says_not(tmp
     (tmp_path / "xcon").mkdir()
     write_rig_runs(tmp_path / "xcon", recipe, build_timeline(recipe))
     assert "setup" not in json.loads((tmp_path / "xcon" / "config.json").read_text())
+
+
+def test_a_rig_record_with_fields_writes_what_wl_xcons_later_builds_add():
+    """Design spec `2026-11-07-rig-record-fields-design.md` section 10: each
+    trial line, run row and change row in the shape wl-xcon's engine builds
+    C2a, C2b and C3 and its XC-399 write, read back with nothing to report;
+    the end row's presentations are the run's lines. Without `fields` none
+    is written (`test_the_rig_record_has_wl_xcons_line_shape`)."""
+    import tempfile
+    from pathlib import Path
+
+    from wl_preproc.events.rigrecords import change_fields, read_rig_records
+    from wl_preproc.events.rigruns import read_rig_runs
+    from wl_preproc.events.rigtrials import read_rig_trials
+    from wl_preproc.synth.peripherals import write_rig_runs, write_rig_trials
+    from wl_preproc.synth.recipe import RigChangeSpec, RigRecordSpec, SessionRecipe
+
+    rig = RigRecordSpec(fields=True, changes=(RigChangeSpec(trial=1),
+                                              RigChangeSpec(trial=2, kind="clear", name="owed", event_code=None)))
+    recipe = SessionRecipe.model_validate({**CI_RECIPE.model_dump(), "runs": True, "rig_record": rig.model_dump()})
+    truth = build_timeline(recipe)
+    with tempfile.TemporaryDirectory() as directory:
+        xcon = Path(directory) / "xcon"
+        xcon.mkdir()
+        write_rig_trials(xcon / "trials.jsonl", recipe, truth)
+        write_rig_runs(xcon, recipe, truth)
+        trials = read_rig_trials(Path(directory), recipe.subject)
+        runs = read_rig_runs(Path(directory), recipe.subject)
+        records = read_rig_records(Path(directory))
+        changes = [json.loads(line) for line in (xcon / "parameter_changes.jsonl").read_text().splitlines()]
+    assert (trials.problems, runs.problems, records.problems) == ((), (), ())
+    first = [trial for trial in trials.trials if trial.run == 0]
+    assert [trial.repeat_of for trial in first] == [None, 0, None] and first[2].live == ("contrast",)
+    assert [len(trial.stimuli) for trial in first] == [2, 2, 3]
+    assert [(run.plan, run.presentations) for run in runs.runs] == [
+        ("synthetic", sum(1 for trial in trials.trials if trial.run == index)) for index in range(len(runs.runs))]
+    assert [run.block_ends[0].block_in_session for run in runs.runs] == [1, 2]
+    assert ["sequence" in row for row in changes] == [True, False]
+    fields, problems = change_fields([change.row for change in records.changes])
+    assert [(field.kind, field.change, field.strobed_nothing) for field in fields] == [("set", 1, False),
+                                                                                    ("clear", 2, True)]
+    assert problems == []
PATCH
```

- [ ] **Step 2: Run them and see them fail.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/synth/test_peripherals.py tests/nwb/test_conditions.py tests/events/test_rigrecords.py tests/schema/test_nwb_rig_fields.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `2 failed, 43 passed, 4 errors`

- [ ] **Step 3: Write the implementation.**

```bash
git apply <<'PATCH'
diff --git a/wl_preproc/nwb/conditions.py b/wl_preproc/nwb/conditions.py
index 5b52224..c107fdf 100644
--- a/wl_preproc/nwb/conditions.py
+++ b/wl_preproc/nwb/conditions.py
@@ -58,6 +58,18 @@ def join(trials: list[dict], record: RigRecord | None) -> tuple[dict[int, RigTri
     return matched, notes
 
 
+def long_names(matched: dict[int, RigTrial], width: int) -> list[str]:
+    """A note on the trials whose condition's name is longer than `width`,
+    the main sequence's column (design spec
+    `2026-11-07-rig-record-fields-design.md` section 9): a large factorial's
+    generated name can be, and wl-xcon warns its author past 200."""
+    long = sum(1 for rig in matched.values() if len(rig.condition) > width)
+    if not long:
+        return []
+    return [f"{long} trial(s) name a condition longer than {width} characters, so the main sequence groups none "
+            "of them by condition"]
+
+
 def stream_codes(trials: list[dict], events: list[dict]) -> dict[int, int]:
     """`{trial_id: code}` where exactly one `CONDITION` event was sent inside
     the trial, `[start, stop)`."""
diff --git a/wl_preproc/nwb/gather.py b/wl_preproc/nwb/gather.py
index 8604bb4..7377bbd 100644
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -393,7 +393,8 @@ def _conditions(trials: list[dict], events: list[dict], runs: list[dict], record
     `2026-11-07-rig-record-fields-design.md` section 4)."""
     import collections
 
-    from wl_preproc.nwb.conditions import block_conditions, join, stream_codes, trial_columns
+    from wl_preproc.nwb.conditions import block_conditions, join, long_names, stream_codes, trial_columns
+    from wl_preproc.schema.main_sequence import CONDITION_WIDTH
     from wl_preproc.nwb.stimuli import stimulus_rows
     from wl_preproc.nwb.trial_record import places, record_columns
 
@@ -403,7 +404,7 @@ def _conditions(trials: list[dict], events: list[dict], runs: list[dict], record
     names, settings = trial_columns(trials, matched, codes)
     columns, record_notes = record_columns(trials, matched, places(record))
     stimuli, stimulus_objects, stimulus_notes = stimulus_rows(trials, matched)
-    notes += record_notes + stimulus_notes
+    notes += record_notes + stimulus_notes + long_names(matched, CONDITION_WIDTH)
     for position, trial in enumerate(trials):
         trial["condition"] = names[position]
         trial["settings"] = {key: values[position] for key, values in settings.items()}
diff --git a/wl_preproc/schema/main_sequence.py b/wl_preproc/schema/main_sequence.py
index 527b616..59b2df4 100644
--- a/wl_preproc/schema/main_sequence.py
+++ b/wl_preproc/schema/main_sequence.py
@@ -32,6 +32,9 @@ schema = dj.Schema()
 #: The width of `reason`, and of `.Condition`'s `condition`: a longer reason
 #: is cut to it, and a longer condition name is no condition.
 _VARCHAR_LEN = 255
+#: That width, for the NWB file's note on such a name (design spec
+#: `2026-11-07-rig-record-fields-design.md` section 9).
+CONDITION_WIDTH = _VARCHAR_LEN
 
 
 @schema
diff --git a/wl_preproc/synth/peripherals.py b/wl_preproc/synth/peripherals.py
index 29188b1..1d39db8 100644
--- a/wl_preproc/synth/peripherals.py
+++ b/wl_preproc/synth/peripherals.py
@@ -169,13 +169,58 @@ def rig_condition(trial_id: int) -> tuple[str, dict]:
     }
 
 
+def _place(x: float, y: float) -> dict:
+    """A position as wl-xcon's XC-399 writes one: its true eccentricity and
+    polar angle beside its per-axis (x, y), the angle null at the centre."""
+    import numpy as np
+
+    from wl_preproc.eye.detect.geometry import eccentricity_polar_deg
+
+    eccentricity, polar = (float(value) for value in eccentricity_polar_deg(np.array([x, y])))
+    return {"eccentricity": eccentricity, "direction": None if np.isnan(polar) else polar, "x": x, "y": y}
+
+
+def _stimuli(index_in_run: int) -> list[dict]:
+    """The fixation point and the target (`rig_condition`'s `target_xy_deg`);
+    a run's third trial shows a two-object array beside them."""
+    shown = {"kind": "show", "changed": None, "drawn": True, "unresolved": None}
+    entries = [{"name": "fix", "frame": 1, "off": 61, **shown, **_place(0.0, 0.0)},
+               {"name": "target", "frame": 85, "off": None, **shown, **_place(0.0, 5.0)}]
+    if index_in_run == 2:
+        entries.append({"name": "search", "frame": 85, "off": None, **shown, **_place(0.0, 0.0), "target": 0,
+                        "objects": [_place(8.0, 0.0), _place(-8.0, 0.0)]})
+    return entries
+
+
+def _trial_fields(index: int, previous: str | None) -> dict:
+    """What wl-xcon's engine builds C2a, C2b and C3 and its XC-399 add to a
+    trial line (design spec `2026-11-07-rig-record-fields-design.md` section
+    10): each run's first trial in a sequence-balanced block's lead-in, its
+    second a drum's repeat of its first, straight after it and owed, its third
+    held by a person at a contrast that kept it out of moving up, with an
+    array beside its target; each trial's hold drawn by the task, and its
+    fluid."""
+    fields = {"counting": "counted", "shown": True, "repeat_of": None, "at_once": False, "previous": previous,
+              "drummed": False, "drumming": None, "aids": [], "structure": "default", "overridden": [],
+              "drawn": {"fix_hold_s": "task"}, "live": [], "eased": [],
+              "planned": {"cycle": 0, "position": index, "lead_in": index == 0}, "fluid_ml": 0.1,
+              "stimuli": _stimuli(index)}
+    if index == 1:
+        fields.update(counting="owed", repeat_of=0, at_once=True, drummed=True, drumming="block", aids=["drumming"])
+    if index == 2:
+        fields.update(overridden=["contrast"], live=["contrast"], eased=["contrast"])
+    return fields
+
+
 def write_rig_trials(path: Path, recipe: SessionRecipe, truth: GroundTruth) -> None:
     """Stands in for wl-xcon's `xcon/trials.jsonl`: one JSON object per line,
     per trial, with the subject on every line. In wl-xcon's shape since its
     XC-155: the run (counted from 0, as wl-xcon counts them; one run per
     block), the trial's index within its run, and `trial_number`, counted
-    across the session and equal to the stream's TRIAL_NUMBER."""
-    lines, per_run = [], {}
+    across the session and equal to the stream's TRIAL_NUMBER; and, for a
+    record with `fields`, what its later builds add (`_trial_fields`)."""
+    fields = recipe.rig_record is not None and recipe.rig_record.fields
+    lines, per_run, previous = [], {}, {}
     for position, trial in enumerate(truth.trials, start=1):
         index = per_run.get(trial.block_id, 0)
         per_run[trial.block_id] = index + 1  # a faulted trial still used its index
@@ -191,7 +236,9 @@ def write_rig_trials(path: Path, recipe: SessionRecipe, truth: GroundTruth) -> N
             "block": f"block-{trial.block_id}",
             "condition": condition,
             "params": params,
+            **(_trial_fields(index, previous.get(trial.block_id)) if fields else {}),
         })
+        previous[trial.block_id] = condition
     if Fault.MISMATCHED_RIG_LINE in recipe.faults:
         lines = mismatch_rig_line(lines)
     path.write_text("\n".join(json.dumps(line, sort_keys=True) for line in lines) + "\n", encoding="utf-8")
@@ -240,14 +287,18 @@ def write_rig_runs(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth) ->
         spec = recipe.blocks[run_index]
         rows.append({"event": "start", "run": run_index, "at": stamp(block.start_s),
                      "task": spec.task_type.name.lower(),
-                     **({} if rig is None else {"calibration": rig.calibration_id})})
-        if run_index + 1 in recipe.unclosed_blocks:
+                     **({} if rig is None else {"calibration": rig.calibration_id}),
+                     **(_start_fields(recipe) if rig is not None and rig.fields else {})})
+        faulted = run_index + 1 in recipe.unclosed_blocks
+        if faulted:
             rows.append({"event": "end", "run": run_index, "at": stamp(block.end_s),
                          "stopped_because": "fault, session aborted: RuntimeError: a synthetic fault",
                          "stop_kind": "fault"})
         else:
             rows.append({"event": "end", "run": run_index, "at": stamp(block.end_s),
                          "stopped_because": "every block is finished", "stop_kind": "completed"})
+        if rig is not None and rig.fields:
+            rows[-1].update(_end_fields(recipe, truth, run_index, faulted))
     (xcon_dir / "runs.jsonl").write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
                                          encoding="utf-8")
     config = {"session_id": recipe.session_id, "subject": recipe.subject}
@@ -262,6 +313,40 @@ def write_rig_runs(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth) ->
     (xcon_dir / "config.json").write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
 
 
+def _start_fields(recipe: SessionRecipe) -> dict:
+    """What wl-xcon's engine builds C2b and C3 add to a run's start row: a
+    plan, its task's content hash, the seed, and the task's draw of the hold."""
+    return {"plan": "synthetic", "plan_form": {"class": "Plan", "fields": {"blocks": ["block"]}},
+            "versions": {"task": "sha256:synthetic", "allocation": "synthetic"}, "seed": recipe.seed,
+            "draws": {"fix_hold_s": {"class": "Uniform", "fields": {"high": 0.35, "low": 0.3, "repeat": "fresh"}}},
+            "generated": [], "directions": []}
+
+
+def _end_fields(recipe: SessionRecipe, truth: GroundTruth, run_index: int, faulted: bool) -> dict:
+    """What wl-xcon's engine builds C2a, C2b and C3 add to a run's end row,
+    counted from the run's own trial lines (`_trial_fields`): its trials, a
+    drum's repeat one with the trial it repeats, its presentations, its
+    block's end, and each condition's counts."""
+    lines = [trial for position, trial in enumerate(truth.trials, start=1)
+             if trial.block_id == run_index + 1 and position not in recipe.faulted_trials]
+    conditions: dict[str, int] = {}
+    for trial in lines:
+        name = rig_condition(trial.trial_id)[0]
+        conditions[name] = conditions.get(name, 0) + 1
+    repeats = 1 if len(lines) > 1 else 0
+    return {"trials": len(lines) - repeats, "presentations": len(lines),
+            "end_reason": None if faulted else "plan_done",
+            "block_ends": [{"block": f"block-{run_index + 1}", "ended": "run" if faulted else "rule", "by": None,
+                            "after_trial": len(lines) - 1 if lines else None, "block_in_session": run_index + 1,
+                            "position": 0, "passed_at": None, "varied": {"fix_hold_s": [{"by": "draw"}]},
+                            "planned": None, "counts": {}}],
+            "shortfalls": [],
+            "conditions": [{"block": f"block-{run_index + 1}", "condition": name, "trials": n, "presentations": n,
+                            "right": n, "wrong": 0, "right_first": n, "owed": 0, "repeats": 0, "moved_past": 0,
+                            "possible_avoidance": False, "drummed": 0, "failing": 0}
+                           for name, n in sorted(conditions.items())]}
+
+
 def write_rig_records(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth,
                       stamp: Callable[[float], float]) -> None:
     """Stands in for wl-xcon's training records as it writes them today,
@@ -283,6 +368,8 @@ def write_rig_records(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth,
     changes = [{"sequence": sequence, "name": change.name, "was": change.was, "now": change.now, "by": change.by,
                 "run": run_of[change.trial], "event_code": change.event_code}
                for sequence, change in enumerate(rig.changes, start=1)]
+    if rig.fields:
+        changes = _c3_changes(rig, truth, run_of, stamp)
     controls = []
     for control, time in zip(rig.controls, truth.control_times, strict=True):
         at = stamp(time)
@@ -296,6 +383,27 @@ def write_rig_records(xcon_dir: Path, recipe: SessionRecipe, truth: GroundTruth,
                                          encoding="utf-8")
 
 
+def _c3_changes(rig, truth: GroundTruth, run_of: dict[int, int], stamp: Callable[[float], float]) -> list[dict]:
+    """Change rows in the shape wl-xcon's engine build C3 writes them
+    (`wl_xcon/record.py`'s change row): each its kind, its act's id, its
+    instant on the rig's clock, the first trial it applies to and the
+    setting's meaning; a `sequence` only on a row whose act strobed
+    `PARAM_CHANGED`, so a clear or a switch carries none."""
+    rows, sequence = [], 0
+    for change_id, (change, time) in enumerate(zip(rig.changes, truth.change_times, strict=True), start=1):
+        at = stamp(time)
+        row = {"change": change_id, "kind": change.kind, "name": change.name, "was": change.was, "now": change.now,
+               "by": change.by, "at": at,
+               "at_local": datetime.datetime.fromtimestamp(at, datetime.timezone.utc).isoformat(),
+               "trial_number": change.trial, "run": run_of[change.trial], "meaning": None, "offered": [],
+               "odds": None, "event_code": change.event_code}
+        if change.event_code is not None:
+            sequence += 1
+            row["sequence"] = sequence
+        rows.append(row)
+    return rows
+
+
 # How a calibration's cone colours became light, in the shape wl-xcon's
 # `photometry.py::cone_record` writes it (read at `b2c2850`): its observer as
 # `cones.py::Observer.record` gives the CIE 2006 10-degree one, with floats,
diff --git a/wl_preproc/synth/recipe.py b/wl_preproc/synth/recipe.py
index 06f2e14..a8906ce 100644
--- a/wl_preproc/synth/recipe.py
+++ b/wl_preproc/synth/recipe.py
@@ -261,6 +261,10 @@ class RigChangeSpec(BaseModel):
     now: Any = 0.25
     event_code: int | None = 4130
     by: dict = Field(default_factory=lambda: {"kind": "box", "name": "synth"})
+    # Its kind, as wl-xcon's engine build C3 writes it (design spec
+    # `2026-11-07-rig-record-fields-design.md` section 7), written only by a
+    # record with `fields`; a clear or a switch strobes nothing.
+    kind: str = "set"
 
 
 class RigControlSpec(BaseModel):
@@ -296,6 +300,11 @@ class RigRecordSpec(BaseModel):
     warnings: tuple[RigWarningSpec, ...] = ()
     changes: tuple[RigChangeSpec, ...] = ()
     controls: tuple[RigControlSpec, ...] = ()
+    # Write each trial line and run row with what wl-xcon's engine builds
+    # C2a, C2b and C3 and its XC-399 add, and each change row in C3's shape
+    # (design spec `2026-11-07-rig-record-fields-design.md` section 10). Off
+    # by default, so every existing profile is byte-identical.
+    fields: bool = False
 
 
 class SessionRecipe(BaseModel):
PATCH
```

- [ ] **Step 4: Run them again and see them pass.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/synth/test_peripherals.py tests/nwb/test_conditions.py tests/events/test_rigrecords.py tests/schema/test_nwb_rig_fields.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `49 passed`

- [ ] **Step 5: Run their neighbours.**

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD WLPP_NSLR_REFERENCE=$HOME/.cache/wl-preproc-references WLPP_BMD_REFERENCE=$HOME/.cache/wl-preproc-references/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$HOME/.cache/wl-preproc-references/bmd/boost_1_86_0 .venv/bin/python -m pytest tests/synth tests/nwb tests/events tests/schema/test_nwb_rig_fields.py tests/schema/test_nwb_rig_record.py tests/schema/test_training_records.py tests/schema/test_events.py tests/schema/test_main_sequence_populate.py -q -p no:cacheprovider -o faulthandler_timeout=1800
```

Expected: `470 passed`

- [ ] **Step 6: Commit.**

```bash
git add -A tests wl_preproc docs
git commit -F - <<'MSG'
test(synth): the synthetic rig writes every field wl-xcon's later builds add, on request, and the build carries them end to end -- each trial, run and change row in C2a's, C2b's, C3's and XC-399's shape behind RigRecordSpec.fields, every existing profile byte-identical; an appearance pinned as its sorted JSON text; a condition name too long for the main sequence noted (sections 9 and 10)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```

- [ ] **Step 7: The mutation checks,** on the commit. Save this list as JSON and run `mutate.py` on it ("The mutation checks" above).

```json
[
 [
  "every synthetic line carries the new fields",
  "wl_preproc/synth/peripherals.py",
  "            **(_trial_fields(index, previous.get(trial.block_id)) if fields else {}),\n",
  "            **_trial_fields(index, previous.get(trial.block_id)),\n",
  "tests/synth/test_peripherals.py tests/nwb/test_conditions.py tests/schema/test_nwb_rig_fields.py"
 ],
 [
  "a clear carries a sequence",
  "wl_preproc/synth/peripherals.py",
  "        if change.event_code is not None:\n            sequence += 1\n",
  "        if True:\n            sequence += 1\n",
  "tests/synth/test_peripherals.py tests/nwb/test_conditions.py tests/schema/test_nwb_rig_fields.py"
 ],
 [
  "a name of exactly the width is long",
  "wl_preproc/nwb/conditions.py",
  "    long = sum(1 for rig in matched.values() if len(rig.condition) > width)\n",
  "    long = sum(1 for rig in matched.values() if len(rig.condition) >= width)\n",
  "tests/synth/test_peripherals.py tests/nwb/test_conditions.py tests/schema/test_nwb_rig_fields.py"
 ],
 [
  "the listing counts rows",
  "wl_preproc/listing/entry.py",
  "    changes = {session: acts(change_fields(rows)[0]) for session, rows in lines.items()}\n",
  "    changes = {session: len(rows) for session, rows in lines.items()}\n",
  "tests/synth/test_peripherals.py tests/nwb/test_conditions.py tests/schema/test_nwb_rig_fields.py"
 ],
 [
  "a clear is timed by a code",
  "wl_preproc/nwb/gather.py",
  "\"rig_clock\" if these.strobed_nothing else \"code\")}",
  "\"code\")}",
  "tests/synth/test_peripherals.py tests/nwb/test_conditions.py tests/schema/test_nwb_rig_fields.py"
 ]
]
```

Expected: 5 lines, each `CAUGHT`; `git status --short` shows no change.

---

### Task 7: The records

The spec's amendments 1-10, made while proving this plan; wl-preproc's answers in `HANDOVER-wl-expcontroller.md`;
wl.works' amendment in `docs/pending-wl-works-amendments.md`; this piece's handoff; and the checkpoint's and
`wl.yaml`'s lines (spec §12). Then the full suite, once, on both interpreters.

**Files:**
- Modify: `docs/superpowers/specs/2026-11-07-rig-record-fields-design.md`, `HANDOVER-wl-expcontroller.md`,
  `docs/pending-wl-works-amendments.md`, `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-11-07-rig-record-fields.md`

**Interfaces:** none; documents only.

- [ ] **Step 1: Apply the records.** Save this script outside the repository as `records.py` and run it from
  the repository root. It writes the handoff with `{{...}}` slots in its §5, filled in Step 4.

```python
"""Task 7 of the proof: the records. Run from the worktree's root, on the branch rebased onto main's pointer."""
import pathlib


def edit(path, old, new):
    p = pathlib.Path(path)
    t = p.read_text()
    assert t.count(old) == 1, (path, old[:70], t.count(old))
    p.write_text(t.replace(old, new))


def append(path, text):
    p = pathlib.Path(path)
    p.write_text(p.read_text().rstrip("\n") + "\n" + text)


SPEC = "docs/superpowers/specs/2026-11-07-rig-record-fields-design.md"
append(SPEC, """
## Amendments, 2026-11-07, made while proving the plan

1. **Every new column of the runs and blocks tables is written only where some run carries its field** (§5), as a
   trial's are (§3): a record from before wl-xcon's engine build C2b gains `stop_kind` and `stopped_because`, which
   every end row carries, and nothing else; the blocks table's columns appear where some run's end row carries
   `block_ends`, a block no entry names reading '' and -1. **The change log's new columns are written for every
   row** (§7): a row from before C3 reads `set`, -1 and ''.
2. **The stimulus's name is published as `stimulus`** (§4): a table's own `name` attribute would shadow a column
   called that, as the conditions table's `condition` already avoids.
3. **A condition's new keys appear only where some line carries their field** (§6): `drawn` where a line carries
   `drawn`, `live` where one carries `live`, `eased` in its counts where one carries `eased`. The description
   defaults them to null, null and 0, so an old record's description reads as before. The NWB conditions table
   gains `drawn`, `live` and `n_eased` columns by the same rule.
4. **A run's own trial index is joined through its trial lines** (§2's ruling): `RigTrial.run` is read for it,
   and a run's `index` and the fields it carries are bookkeeping, left out of a run's equality.
5. **The rig's clock fit without the change rows** (§7's ruling) says so in a problem: "with its rows the rig's
   clock would not fit (...), so it is fitted without them". A change row that strobed nothing and could not be
   timed is named as such a control is.
6. **The listing counts acts from each change's stored line** (§7), not by counting rows in the database.
7. **The note on long names reads the main sequence's own width** (§9), `schema/main_sequence.CONDITION_WIDTH`.
8. **The synthetic rig writes the new fields only with `RigRecordSpec.fields`** (§10), so every existing profile
   stays byte-identical; a change's `kind` makes a clear or a switch, which carries no `sequence`.
9. **wl-xcon's simulated session folder** (§10) had not arrived when the plan was written; its own task follows
   the records, and the merge waits for neither.
10. **Where an unreadable field is said** (§2): a trial line's or a change row's, which only the build reads, in
    the description's `notes`; a run row's where the run rows' other problems already are, `core.RunRecordProblem`,
    which the listing sends wl.works as a `rig_record_problem` flag, since the event stage reads the run rows too.
""")

pathlib.Path("docs/handoffs/2026-11-07-rig-record-fields.md").write_text("""# The rig's record, read whole: wl-xcon's five asks

**`specs/2026-11-07-rig-record-fields-design.md`**: wl-xcon's engine builds C2a, C2b and C3, its XC-399, and a
setting's value that is an appearance, each "read them into the published dataset". The rig's new fields are read
when the NWB file is built, with no database change.
- **Branch:** `feat/rig-record-fields`, from `plan/rig-record-fields`.
- **Spec:** the design, its six sections approved one by one on 2026-11-07, its rulings, and its amendments 1-10,
  made while proving the plan.
- **Plan:** `docs/superpowers/plans/2026-11-07-rig-record-fields.md`.

Every line of the plan was proven in a scratch branch before the plan was written.

---

## 1. What was built

- **Each trial** (`events/rigtrials.py`, `nwb/trial_record.py`): what it paid (`counting`, never its answer),
  whether its stimulus appeared, the trial it shows again (by its `trial_id`), its drumming and aids, its
  structure, the values a person overrode or held, its draws, the settings that kept it out of moving up, a
  designed block's planned place, and its fluid, as trials-table columns written where some line carries them.
- **Each stimulus** (`nwb/stimuli.py`): the `rig_record` module's `stimuli` and `stimulus_objects`, frames not
  times, each position's eccentricity and polar angle checked against our geometry.
- **Each run and block** (`events/rigruns.py`, `nwb/run_record.py`): the plan, its end reason, its stop, the
  rig's counts, the task's hash and the seed; how each block ended; `unopened_blocks`, `shortfalls` and
  `condition_counts`; a disagreement with the stream a note.
- **A condition's summary** (`nwb/conditions.py`): its own values apart from drawn and person-held ones, those
  beside it, its eased trials, directions as arcs.
- **The change log** (`events/rigrecords.py`, `events/rigtiming.py`): published by kind and act; a clear or a
  switch timed through the rig's clock; wl.works' count of live changes is a person's acts on a setting.
- **The description** gains `Run.plan`, `Run.end_reason`, `RunBlock.ended`, `Condition.drawn`, `Condition.live`
  and `TrialCounts.eased`, all defaulted, within version 3.

## 2. What the other repositories must do

- **wl-xcon:** told at the merge (`HANDOVER-wl-expcontroller.md`, 2026-11-07); it may stop writing clears and
  switches to both files.
- **wl.works:** the description's new fields, the count's meaning and the file's new tables
  (`docs/pending-wl-works-amendments.md`, 2026-11-07); and whether it wants `plan` and `end_reason` on the listing.

## 3. Still open

- **wl-xcon's simulated session folder**, vendored as a contract fixture by its own task when it arrives.
- `plan` and `end_reason` on the listing, until wl.works asks.

## 4. The rulings

The spec's rulings and its amendments 1-10.

## 5. What execution measured

Executed on {{DATE}}: Task 1 `{{SHA1}}`, Task 2 `{{SHA2}}`, Task 3 `{{SHA3}}`, Task 4 `{{SHA4}}`, Task 5
`{{SHA5}}`, Task 6 `{{SHA6}}`.
- **The full suite, once, on both interpreters,** with the BMD and NSLR references set, as CI has them, the
  Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, as in CI:
  - 3.11: **{{S311}}**;
  - 3.13: **{{S313}}**.
""")

append("HANDOVER-wl-expcontroller.md", """
# 2026-11-07: the rig's record, read whole (wl-xcon's asks C2a, C2b, C3, XC-399 and appearances)

Designed in `docs/superpowers/specs/2026-11-07-rig-record-fields-design.md`, its six sections approved by the
requester on 2026-11-07; built on `feat/rig-record-fields`, not merged. **Every new field is read when the NWB
file is built, from the session folder, with no database change**, and each is optional: a record from before a
build reads as it did.
- *C2a and C2b:* each trial's `counting` (published with the warning that it is what the trial paid, never its
  answer), `shown`, `repeat_of` (as the repeated trial's `trial_id`), `at_once`, `previous`, `drummed`,
  `drumming`, `aids`, `structure` and `overridden` are trials-table columns; each run's `plan`, `end_reason`,
  `stop_kind`, `stopped_because`, `trials` and `presentations` are runs-table columns, its `plan_form` JSON text.
  **Your two questions:** `plan` is on the runs table and in the NWB description's run, not yet on the listing,
  which waits for wl.works; **the blocks table carries each block's end** (`ended`, `ended_by` by kind,
  `after_trial`, `position`, `passed_at`). A block that ran no trial, with no `block_in_session`, is a row of
  `unopened_blocks`; `shortfalls` and `condition_counts` are tables of their own.
- *XC-399:* **two tables of their own**, `stimuli` (one row per entry) and `stimulus_objects` (one row per array
  object, by eye for a per-eye array), in the file's `rig_record` module. The angle is published as `polar_deg`
  beside `eccentricity_deg`, read from `direction` today and from `polar` after your rename; each position's
  angles are checked against ours, a disagreement past 1e-9 degrees a note, never a correction. Frames stay
  frames until your screen log.
- *Appearances:* the form is accepted as written (2026-10-30); a test pins it as its object's sorted JSON text.
- *C3, your four asks:* (1) `drawn`, `live`, `eased` and `planned` are trials-table columns, and **a condition's
  summary keeps its own values apart from drawn and person-held ones**, summarising those beside them, with its
  eased trials counted (the requester's decision of 2026-11-07); (2) the start row's `draws` and `generated` are
  runs-table columns as JSON text, `directions` makes a setting's summary an arc, and each block end's `varied`,
  `planned` and `counts` and the `designed` shortfall are published; (3) the change log is published by `kind`
  and `change`, with `trial_id`, `went`, `step`, `to`, `from_draw`, `block` and `moment`, and **wl.works' count
  of live parameter changes becomes a person's acts on a setting**, one per act among `set`, `hand_back`,
  `range` and `revert` (the requester's decision of 2026-11-07); (4) a change row that strobed nothing is timed
  through the rig's clock fit, as a control is, and a coded change row's instant joins the fit unless with it the
  fit would fail. **Once this is merged you may stop writing clears and switches to `controls.jsonl`** (your
  XC-442); we will say so with the merge.
- *Asked of wl-xcon, 2026-11-07, and agreed:* one simulated session's `xcon/` folder from your C3 build, vendored
  here as a contract fixture.
""")

edit("docs/pending-wl-works-amendments.md", """**Ten are outstanding: two opened 2026-08-22, one 2026-09-28, one 2026-09-29, two 2026-09-30, one 2026-10-01, one 2026-10-05, one 2026-10-14, one 2026-10-18.**""",
     """**Eleven are outstanding: two opened 2026-08-22, one 2026-09-28, one 2026-09-29, two 2026-09-30, one 2026-10-01, one 2026-10-05, one 2026-10-14, one 2026-10-18, one 2026-11-07.**""")
edit("docs/pending-wl-works-amendments.md", """
---

# OPEN — wl-xcon's training records, counted, and the clock that kept each session's time""", """
---

# OPEN — the rig's record, read whole: new fields in the NWB description, and the live-change count's meaning

**Opened 2026-11-07** with
[`specs/2026-11-07-rig-record-fields-design.md`](superpowers/specs/2026-11-07-rig-record-fields-design.md)
(§7, §8; the requester's decisions of 2026-11-07 in §0), answering wl-xcon's asks C2a, C2b, C3 and XC-399.

- **`GET /nwb`:** each description gains, within version 3 and each defaulted so a description written before
  reads unchanged: each run's `plan` and `end_reason`; each block's `ended`; each condition's `drawn` and
  `live` (its drawn and person-held values, which no longer read as its own `settings` or `varying`) and its
  trial counts' `eased`. `docs/schemas/nwb_description.json` refuses fields it does not know, so wl.works' copy
  must take these first.
- **The count of live parameter changes changes meaning**, in the listing's `training_records` and the
  description's: since wl-xcon's engine build C3 the change log also holds hand-backs, range edits, reverts,
  clears and switches; the count is a person's acts on a setting, one per act among a set, a hand-back, a range
  edit and a revert. A session from before C3 counts as before.
- **The file gains** trials-table columns (what each trial paid, its sequence, aids, draws, holds and planned
  place), runs- and blocks-table columns (plan, end reason, stop, counts, each block's end), and `rig_record`
  tables (`stimuli`, `stimulus_objects`, `unopened_blocks`, `shortfalls`, `condition_counts`); none is in the
  description beyond the fields above.
- **A question:** should the listing's runs carry `plan` and `end_reason`? `ListedRun` refuses unknown fields,
  so nothing is added until wl.works says yes.
- **Every listed session whose change log holds C3 rows is listed once more** when this ships: its count, and so
  its digest, changes.

---

# OPEN — wl-xcon's training records, counted, and the clock that kept each session's time""")

edit("docs/CHECKPOINT.md", """> v3's digest on 2026-11-07. Next, the rest of wl-xcon's asks (engine builds C2a, C2b and C3;
> XC-399; and a setting's value that is an appearance), designed as one piece
> (`specs/2026-11-07-rig-record-fields-design.md` on `spec/rig-record-fields`, approved
> 2026-11-07; its plan being proved); then, in the requester's order of 2026-10-31, the
> pipeline's scaling to long sessions, part 3b, the NI as the clock, part 2, the bridges, and the
> older minors in one branch.""", """> v3's digest on 2026-11-07. **The rest of wl-xcon's asks (engine builds C2a, C2b and C3;
> XC-399; and a setting's value that is an appearance) are built** as one piece on
> `feat/rig-record-fields`, not merged (`specs/2026-11-07-rig-record-fields-design.md`,
> approved 2026-11-07; `handoffs/2026-11-07-rig-record-fields.md`): the rig's new fields are read
> when the NWB file is built, with no database change. Next: its review; then, in the
> requester's order of 2026-10-31, the pipeline's scaling to long sessions, part 3b, the NI as
> the clock, part 2, the bridges, and the older minors in one branch.""")
edit("docs/CHECKPOINT.md", """> until its review, that the review was next; until its merge, that the merge was next; each
> true when written.*""", """> until its review, that the review was next; until its merge, that the merge was next; until
> wl-xcon's other asks were built, that their plan was being proved; each true when written.*""")
edit("wl.yaml", """    docs/handoffs/2026-11-06-eye-lost-data-and-region.md). Next, the rest
    of wl-xcon's asks (C2a, C2b, C3, XC-399, a setting's value that is an
    appearance), designed as one piece (the spec
    2026-11-07-rig-record-fields-design.md on spec/rig-record-fields,
    approved, its plan being proved); then, in the requester's order of""", """    docs/handoffs/2026-11-06-eye-lost-data-and-region.md). The rest of
    wl-xcon's asks (C2a, C2b, C3, XC-399, a setting's value that is an
    appearance) are built as one piece on feat/rig-record-fields
    (docs/handoffs/2026-11-07-rig-record-fields.md), not merged, its
    review next; then, in the requester's order of""")
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

Expected, each exiting 0: 3.11, **2791 passed, 26 skipped, 1 deselected, 1 xfailed**; 3.13, **2791 passed, 27 skipped, 1 xfailed**. Each is the 2750 on main before this plan plus its 41 new tests. A MySQL container
that does not start within its 120 s under heavy load errors every database test at once; that is the known flake,
and the suite is run again.

- [ ] **Step 4: Fill the handoff's §5** with this execution's own figures: `{{DATE}}` the day, `{{SHA1}}` to
  `{{SHA6}}` Tasks 1-6's commits (`git log --oneline -7`), and `{{S311}}` and `{{S313}}` Step 3's two summary
  lines. Then `grep -c '{{' docs/handoffs/2026-11-07-rig-record-fields.md`.

Expected: `0`.

- [ ] **Step 5: Commit.**

```bash
git add -A docs wl.yaml HANDOVER-wl-expcontroller.md
git commit -F - <<'MSG'
docs: the rig's record's records -- the spec's amendments 1-10, made while proving the plan, wl-preproc's answers in HANDOVER-wl-expcontroller.md, wl.works' amendment, the handoff, and the checkpoint's and wl.yaml's lines: built, not merged; next, the review

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016qk6ZmpHJTmi88idVn1B44
MSG
```
