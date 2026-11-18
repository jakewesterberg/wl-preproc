# Full-Precision Trial and Block Times Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** every trial and block time the pipeline publishes or computes from is the double it measured, not
element-event's float32 copy.

**Architecture:** two tables of our own in `schema/core.py`, `TrialTime` and `BlockTime`, written beside
element-event's rows by one pair of writer functions and read by one pair of reader functions that refuse a trial or
block with no time row. Every reader switches in one commit, with `starts_inside` comparing exactly. A guard test keeps
element-event's four time column names out of every module but `schema/core.py`.

**Tech Stack:** Python 3.11 and 3.13, DataJoint 2.3.3, MySQL 8 (testcontainer), pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-full-precision-trial-times-design.md` (approved 2026-11-17).

## Global Constraints

- element-event's tables are not changed; its `trial_start_time`, `trial_stop_time`, `block_start_time`,
  `block_stop_time` are still written (from the same values), and read by nothing.
- Event times (`decimal(10,4)`, `event_bounds`) and `core.Run` are unchanged; `stored_doubles` stays for them.
- A missing time row raises `core.MissingTimeRows`, naming the session(s) and counts; nothing joins and drops.
- The listing and the NWB file decide run membership through the same `events/runs.py::run_of`/`starts_inside`.
- Descriptions claim storage as measured and accuracy as the session's clock fit and the 1 ms code-word slot,
  never more.
- Times round-tripped through MySQL `double` are compared exactly; tests pinned to float32-rounded values are
  corrected to the measured value, not loosened.
- Every new table's definition comment carries a `Key: (...)` line (`test_every_table_documents_its_key_in_schema`).
- Never commit while a full suite runs on the same checkout; leave the root `wl-preproc` symlink out of commits.

## Review Focus

1. A session re-ingested after element-event's rows exist but its time rows do not (ingest is not one transaction):
   the re-run fills the time rows. Test in Task 1.
2. A trial with no outcome (`trial_type` None): written and read back with its times. Test in Task 1.
3. The listing over several sessions where one lacks time rows: the error names that session, not the restriction.
   Test in Task 1.
4. A trial starting exactly on its run's start, and a block 0.1 ms after it, 12 hours in: both in the run, in the
   listing and the NWB file alike. Test in Task 2.
5. `TrialCoverage` for a trial with no time row: raises `MissingTimeRows`, not a bare `fetch1` error or a skipped
   trial. Test in Task 2.

---

### Task 1: The tables, their writer and their readers

**Files:**
- Modify: `wl_preproc/schema/core.py` (two tables after `Run`; four functions and the exception at module level)
- Modify: `wl_preproc/schema/events.py:546-588` (`populate_session` builds rows in our shape and calls the writers)
- Test: `tests/schema/test_full_precision_times.py` (new; subject `fptimes`, checked unclaimed across `tests/`)

**Interfaces:**
- Produces:
  - `core.TrialTime` — `-> pipeline.trial.Trial`; `start_s : double  # (s) session time, as measured`;
    `stop_s : double`. `core.BlockTime` — `-> pipeline.trial.Block`; same two attributes.
  - `class MissingTimeRows(RuntimeError)` in `core`.
  - `insert_trials(rows: list[dict]) -> None` — each row: the session key, `trial_id`, `trial_type` (str or None),
    `start_s`, `stop_s`; writes `trial.Trial` (`allow_direct_insert=True`) and `TrialTime`, both
    `skip_duplicates=True`.
  - `insert_blocks(rows: list[dict]) -> None` — the session key, `block_id`, `start_s`, `stop_s`; writes
    `trial.Block` and `BlockTime` the same way.
  - `measured_trials(restriction) -> list[dict]` — per trial: the key, `trial_type`, `start_s`, `stop_s`, ordered by
    session then `trial_id`; raises `MissingTimeRows` when any restricted `trial.Trial` row has no `TrialTime`.
  - `measured_blocks(restriction) -> list[dict]` — per block: the key, `start_s`, `stop_s`, same order and rule.
  - `restriction` is anything DataJoint restricts by: a key dict or a list of them.

- [ ] **Step 1: Write the failing tests** in `tests/schema/test_full_precision_times.py`, with a module fixture like
  `tests/schema/test_float_times_round_trip.py::hours_in` (lab, subject, `Session`, `Ingestion`,
  `BehaviorRecording`) for two sessions, `_A` and `_B`:

```python
_LATE = 43_199.9876  # float32 stores 43199.98828125, 0.68 ms off

def test_a_trial_and_block_12_hours_in_read_back_as_measured(two_sessions):
    core.insert_blocks([{**_A, "block_id": 1, "start_s": _LATE, "stop_s": _LATE + 0.0003}])
    core.insert_trials([{**_A, "trial_id": 1, "trial_type": None, "start_s": _LATE, "stop_s": _LATE + 0.0002}])
    (block,) = core.measured_blocks(_A)
    (trial,) = core.measured_trials(_A)
    assert (block["start_s"], block["stop_s"]) == (_LATE, _LATE + 0.0003)
    assert (trial["start_s"], trial["stop_s"], trial["trial_type"]) == (_LATE, _LATE + 0.0002, None)

def test_a_trial_with_no_time_row_raises_naming_its_session(two_sessions):
    # element-event's row alone, as a run killed between the two inserts leaves it
    pipeline.trial.Trial.insert1({**_B, "trial_id": 7, "trial_start_time": 1.0, "trial_stop_time": 2.0},
                                 allow_direct_insert=True)
    with pytest.raises(core.MissingTimeRows, match=r"fptimes.*1 of 1 trial"):
        core.measured_trials([_A, _B])
    assert core.measured_trials(_A)  # the other session still reads

def test_a_rerun_fills_the_missing_time_row(two_sessions):
    core.insert_trials([{**_B, "trial_id": 7, "trial_type": None, "start_s": 1.0, "stop_s": 2.0}])
    assert [row["trial_id"] for row in core.measured_trials(_B)] == [7]

def test_populate_session_writes_the_measured_times(ci_session):  # in tests/schema/test_events.py
    session_key, session_dir, _ = ci_session
    events.populate_session(session_key, session_dir)
    assembly = assemble(decode_stream(clock_for(session_key, session_dir).codes()))
    measured = core.measured_trials(session_key)
    assert {row["trial_id"]: row["start_s"] for row in measured} == {t.trial_id: t.start_s for t in assembly.trials}
    copy = {row["trial_id"]: row for row in stored_doubles(pipeline.trial.Trial & session_key,
                                                         "trial_start_time", "trial_stop_time")}
    for row in measured:  # element-event's copy is the same value, rounded to float32
        assert float(np.float32(row["start_s"])) == copy[row["trial_id"]]["trial_start_time"]
        assert float(np.float32(row["stop_s"])) == copy[row["trial_id"]]["trial_stop_time"]
    # and the same three assertions for blocks: measured_blocks, assembly.blocks' start_s, the block copy
```

  The last one goes beside `test_populate_session_builds_the_canonical_trial_and_block_lists` and reuses its
  `ci_session`, importing `assemble`, `decode_stream` and `clock_for` from where `schema/events.py` imports them.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_full_precision_times.py tests/schema/test_events.py -q`
Expected: FAIL with `AttributeError: module 'wl_preproc.schema.core' has no attribute 'insert_blocks'`.

- [ ] **Step 3: Implement the tables, `MissingTimeRows`, the two writers and the two readers in `core.py`.** Each
  definition comment says it holds the time as measured, that element-event's column of the same row is a float32
  copy read by nothing, and its `Key: (subject, session_datetime, trial_id)` / `(..., block_id)`. A reader joins
  `(pipeline.trial.Trial & r).proj("trial_type")` with `TrialTime & r`, and first counts
  `(pipeline.trial.Trial & r) - TrialTime` per session; the message names each such session and
  `"<missing> of <total> trial(s)"` (blocks: `block(s)`) with no time row, and says a re-run of the event stage
  fills them. Then in `events.py` build `trial_rows` and `block_rows` with `start_s`/`stop_s` and call
  `core.insert_trials`/`core.insert_blocks` in place of the two `insert` calls; nothing else in `populate_session`
  moves.

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/schema/test_full_precision_times.py tests/schema/test_events.py tests/schema/test_guardrails.py -q`
Expected: PASS (the guardrails sweep the new tables' keys and the delete-preview graph, which omits them as it omits
`core.Run`).

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/schema/core.py wl_preproc/schema/events.py tests/schema/test_full_precision_times.py tests/schema/test_events.py
git commit -m "feat(schema): trial and block times as measured, in core.TrialTime and core.BlockTime ..."
```

### Task 2: Every reader reads the measured times, and run membership is exact

**Files:**
- Modify: `wl_preproc/events/runs.py` (module docstring; `starts_inside` compares `run_start_s <= start_s <= run_stop_s`)
- Modify: `wl_preproc/nwb/gather.py:252-295, 360-380` (`_blocks`, `_trials`, the outside-runs count)
- Modify: `wl_preproc/listing/entry.py` (module docstring, `blocks` field comment, lines ~112-160 and 264)
- Modify: `wl_preproc/schema/coverage.py:103-126` (`TrialCoverage.make`)
- Modify: `wl_preproc/schema/main_sequence.py:190-222` (`_groups`)
- Modify (fixtures): `tests/schema/test_float_times_round_trip.py`, `test_main_sequence_populate.py`,
  `test_nwb_outside_runs.py`, `test_session_listing.py`, `test_nwb_build.py`, `tests/listing/test_entry.py`
- Test: `tests/schema/test_full_precision_times.py`

**Interfaces:**
- Consumes: Task 1's `insert_trials`, `insert_blocks`, `measured_trials`, `measured_blocks`, `MissingTimeRows`.
- Produces: listing `Facts.blocks` rows keyed `block_id`, `start_s`, `stop_s` (were `block_start_time`,
  `block_stop_time`); `starts_inside(start_s, run_start_s, run_stop_s)` unchanged in signature, now exact.

- [ ] **Step 1: Write the failing tests** in `tests/schema/test_full_precision_times.py`, on a session `_C` with
  `core.Run` 1 from `_RUN_START = 43_000.1234` to `43_190.0`:

```python
def test_a_trial_on_its_run_start_and_a_block_just_after_are_in_the_run_in_the_file_and_the_listing(run_c):
    core.insert_blocks([{**_C, "block_id": 1, "start_s": _RUN_START + 0.0001, "stop_s": 43_150.0}])
    core.insert_trials([{**_C, "trial_id": 1, "trial_type": None, "start_s": _RUN_START, "stop_s": _RUN_START + 0.02}])
    pipeline.trial.BlockTrial.insert1({**_C, "block_id": 1, "trial_id": 1}, allow_direct_insert=True)
    (block,) = gather._blocks(run_c, _C)
    (trial,) = gather._trials(run_c, _C)
    assert (block["run_number"], block["start_s"]) == (1, _RUN_START + 0.0001)
    assert (trial["run_number"], trial["start_s"], trial["stop_s"]) == (1, _RUN_START, _RUN_START + 0.02)
    (run,) = session_entry(_C)["runs"]
    assert [(b["block_number"], b["start_s"]) for b in run["blocks"]] == [(1, _RUN_START + 0.0001)]

def test_a_trial_with_no_time_row_fails_its_coverage(run_c):
    pipeline.trial.Trial.insert1({**_C, "trial_id": 9, "trial_start_time": 43_100.0, "trial_stop_time": 43_101.0},
                                 allow_direct_insert=True)
    with pytest.raises(core.MissingTimeRows):
        coverage.TrialCoverage().make({**_C, "trial_id": 9, "system": "syncbox"})
```

  (float32 stores `_RUN_START` and `_RUN_START + 0.0001` alike as 43000.125. Today the assertions fail on that
  value; with the measured value but `starts_inside` still rounding the run's start to 43000.125, the block, 1.5 ms
  before it, falls out of its run: the test fails both halves of a partial switch.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_full_precision_times.py -q`
Expected: FAIL: `block["start_s"]` is the float32 copy, 43000.125; coverage raises `DataJointError`, not
`MissingTimeRows`.

- [ ] **Step 3: Switch the readers, in one commit with `starts_inside`.** `_blocks`/`_trials`/the outside-runs count
  read `core.measured_blocks`/`core.measured_trials` (keys `start_s`, `stop_s`); the listing reads
  `core.measured_blocks(keys)` and its entry code uses `start_s`/`stop_s`; `main_sequence._groups` reads both
  readers (its event read keeps `stored_doubles`); `TrialCoverage.make` reads `(core.TrialTime & key).to_dicts()`
  only, raising `MissingTimeRows` naming the session and trial when it is empty, and drops its six-digits comment.
  `starts_inside`'s docstring and `runs.py`'s module docstring say trial and block starts are the measured doubles,
  compared exactly; `stored_doubles` remains for event times.

- [ ] **Step 4: Correct the fixtures and the float32 tests.** The five schema test files insert through
  `core.insert_trials`/`core.insert_blocks`; `test_float_times_round_trip.py` asserts the measured values
  (`_BLOCK_START`, `_SHORT_TRIAL`), its docstring saying what it now holds; `tests/listing/test_entry.py`'s facts use
  `start_s`/`stop_s`, and `test_a_block_starting_just_after_its_run_is_in_it_despite_float32_storage` becomes
  `test_a_block_starting_just_after_its_run_is_in_it`, a block 0.1 ms after a run start 12 hours in, as a double.

- [ ] **Step 5: Run the affected tests**

Run: `.venv/bin/python -m pytest tests/schema tests/listing tests/nwb tests/events -q -x`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc tests
git commit -m "feat: every reader reads the measured trial and block times, and run membership is exact ..."
```

### Task 3: The guard and the descriptions

**Files:**
- Modify: `tests/schema/test_guardrails.py` (one test beside `test_no_bare_delete_call_anywhere_in_the_source`)
- Modify: `wl_preproc/nwb/intervals.py:66-75, 130-140` (the blocks' and trials' start and stop column descriptions)
- Modify: any module docstring or comment outside `schema/core.py` the guard names

**Interfaces:**
- Consumes: Tasks 1 and 2 (nothing outside `core.py` should still name the columns).

- [ ] **Step 1: Write the guard**

```python
_ELEMENT_EVENT_TIMES = ("trial_start_time", "trial_stop_time", "block_start_time", "block_stop_time")

def test_only_core_names_element_events_float32_time_columns():
    """element-event stores them as float32, about 4 ms coarse 12 hours in (design spec
    2026-11-17-full-precision-trial-times-design.md); core.TrialTime and core.BlockTime hold the measured time,
    and only core.py's writers touch the copy."""
    offenders = [f"{path.relative_to(SOURCE_ROOT.parent)}:{n}" for path in SOURCE_ROOT.rglob("*.py")
                 if path.name != "core.py" or path.parent.name != "schema"
                 for n, line in enumerate(path.read_text().splitlines(), 1)
                 if any(name in line for name in _ELEMENT_EVENT_TIMES)]
    assert not offenders, offenders
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/schema/test_guardrails.py::test_only_core_names_element_events_float32_time_columns -q`
Expected: FAIL on whatever docstrings and comments still name them (e.g. `main_sequence.py`'s `_groups` docstring,
`events.py`'s comments). Prove it can fail on code, too: a reader line restored from `git show HEAD~1` fails it.

- [ ] **Step 3: Reword those passages** to name `core.TrialTime`/`core.BlockTime`, and set the NWB descriptions to
  (exact copy; trials say "Trial" for "Measured block"):
  - start: `"Measured block start, session seconds, stored as measured. Its accuracy is the session's clock fit, as the timing report states, and a code word marks it to its 1 ms slot."`
  - stop: `"Measured block stop, session seconds, stored as measured; accurate as its start is."`

- [ ] **Step 4: Run the guard, the NWB tests and the guardrails**

Run: `.venv/bin/python -m pytest tests/schema/test_guardrails.py tests/nwb tests/schema/test_nwb_build.py -q`
Expected: PASS (a test pinning the old description text is corrected to the new copy).

- [ ] **Step 5: Commit**

```bash
git add tests/schema/test_guardrails.py wl_preproc
git commit -m "test(guardrails): nothing but core names element-event's float32 time columns; the descriptions claim what the timing holds ..."
```
