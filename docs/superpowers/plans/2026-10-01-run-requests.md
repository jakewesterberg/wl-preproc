# Requests That Name Runs (Plan B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** wl.works' job requests name the session's measured runs — a canonical asserting every run of its montage with each probe's runs stated, a derivative naming whole runs — each checked against `core.Run` as it arrives, and the NWB file and its description (version 3) are built from those runs; the block paths they replace are retired.

**Architecture:**
- **New tables hold what a request asserts and selects:** `core.RunAssertion` (wl.works' id and copy of a measured run), `request.ActivationRun` (a file's runs), `request.ActivationProbeRun` (each probe's), and `coverage.RunCoverage`, a daemon stage.
- **`accept()` checks every run on arrival** against `core.Run`, within 2 ms, before anything is written, and answers a stale listing with a `422` naming the run and a second `works_run_id` with a `409`.
- **The NWB builder reads the activation's runs**: what a run holds is decided in each table's own stored precision (`events/runs.py`), the file gains `/intervals/runs`, and the description goes to version 3.
- **The block paths are retired last,** once nothing reads them: the request fields, `core.Block`, `ActivationBlock`, `BlockCoverage` and `TimingProvenance.block_agreement`.

**Tech Stack:** Python ≥3.11; DataJoint 2.3 with MySQL; element-event (adopted, pinned); pydantic 2 (the contracts); pynwb (the file); the stdlib `http.server` responder.

**Spec:** `docs/superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md` (`89ad4e9`, amended `03c6784` and in Plan A's branch). It is binding. The requester approved it on 2026-10-01, and chose to write this plan after Plan A's minors merged (*"Merge to main and push ... start writing Plan B"*). This plan is its §10's Plan B: §3, §4 and §5. Task 7 adds the spec's dated amendments 13–23, which record the rulings below.

**Every piece of code below was proven before this plan was written.** It was built in a scratch worktree on a branch of its own, one commit per task, on `main` at `6a67ae2`.
- **Each task's failing run** was measured on the previous task's code plus this task's tests.
- **Its passing runs** were measured on its own commit.
- **Every mutation check named here** was run against the final tree, and each failed its test.
- **The full suite** was run on both interpreters with every task applied (Task 7 quotes it), and on 3.11 after Tasks 4, 5 and 6 as they were proven.

## Global Constraints

- **The spec is binding,** including the dated amendments Task 7 adds. Where it and this plan disagree, the spec wins; record a ruling.
- **The requester's decisions (spec §0):**
  - a canonical file keeps every run of its montage;
  - a repeated run number lists its first run and flags the session (*"run numbers repeat; the restarted runs wait for XC-026"*);
  - a derivative selects whole runs.
- **A request is checked before anything is written** (the responder's review C1): a refused request leaves no `Montage`, `RunAssertion`, report or activation behind.
- **A refusal is a `422` the caller can act on, never a `500`:** its message names what was wrong, and a `500` tells wl.works to retry forever.
- **What wl.works asserts is kept apart from what this host measures:** `core.Run` is the event stage's; `core.RunAssertion` is wl.works' copy and its id.
- **element-event's tables are adopted, not changed.**
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code, data or tables.
- **Leave the stray symlink `wl-preproc` at the repository root alone,** and keep it out of commits.
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
- **`wlpp schemas export` after a wire contract changes** (Tasks 3, 4 and 5); CI diffs `docs/schemas`.
- **`wl-check` after `wl.yaml` changes,** on its own (Task 7).
- **Every commit message ends with:**

  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R
  ```

## Review Focus

Five inputs the spec implies but its own testing section (§9) does not name, most likely first. Each is pinned by a test named here, in the task that pins it.

1. **A run starting exactly where its montage ends** belongs to the next montage, and one ending exactly there is still this montage's: the window is half-open, `[start_s, end_s)`, on the run's start. Task 5: `test_accept_treats_the_montage_window_as_half_open`.
2. **A block, trial or event stored hours into a session just after its run's `RUN_START`** (float32 for blocks and trials, `decimal(10,4)` for events) is in that run, never dropped from the file. Task 4: `test_what_starts_just_after_its_run_is_in_it_however_it_was_stored`.
3. **A client still sending the old block fields over the real wire** gets a `422` naming their replacement, never a `500` that its retry loop would resend forever. Task 5: `test_the_retired_blocks_are_refused_over_http_naming_metadata_runs`.
4. **An eye event that crosses its run's end** keeps its true end in the file, rather than being cut or dropped. Task 4: `test_an_eye_event_that_crosses_its_runs_end_keeps_its_true_end`.
5. **A run that faulted at once, with no length,** is `absent` in `RunCoverage`, not a coverage stage failing on every pass. Task 1: `test_run_coverage_populates_a_row_for_every_run_and_system`.

## The spec's §8, verified at `6a67ae2` for Plan B

3. **Every reader of `core.Block`, `ActivationBlock`, `BlockCoverage` and `block_agreement`:** `core.Block` by `coverage.BlockCoverage`, `timebase.TimingProvenance.make()` and `responder/jobs.py`'s window check; `ActivationBlock` by `nwb/gather.py::_block_set`; all three tables by `cli/deleting.py`'s map; `block_agreement` by `events/agreement.py::resolve_tier` alone. `wlpp report` reads none of them: its tests' fixtures only write the column. Tasks 4 to 6 move each reader to runs or retire it with its table.
4. **`BlockCoverage.make()`** intersects the block's `[start_s, end_s)` with the system's `core.Segment` extents through `timebase/coverage.py::classify_coverage`. `RunCoverage` (Task 1) does the same over the measured run.
7. **The description's `blocks`** are written by `nwb/describe.py` and served as stored by `GET /nwb`; nothing else in this repository reads them, only `tests/nwb/test_describe.py` and `tests/schema/test_nwb_build.py`.

**Plan A's carried rule** (spec amendment 11): a file's runs are found by comparing in each table's stored precision (Task 4's `events/runs.py`), not by recording each block's run at the event stage.

## The rulings this plan carries

Each is a dated amendment Task 7 adds to the spec.
- **13 and 14 (Task 4):** what a run holds is decided in each table's own precision, in one place; a trial belongs to the run its start lies in.
- **15 (Task 4):** `/intervals/blocks` holds the runs' measured blocks and is left out when they hold none.
- **16 (Task 6):** the run check's tolerance is two code-word slots, 2 ms, with no float32 term.
- **17 (Task 5):** `metadata.blocks` stays in the contract, deprecated and `maxItems: 0`, refused by pydantic's custom error.
- **18 and 19 (Tasks 3 and 5):** a canonical cannot name a subset of its runs; `probe_runs` names exactly the request's probes, and a derivative's run may have been asserted earlier.
- **20 (Task 5):** a session with no measured run cannot be requested, so the probe-linking tests report after the event stage.
- **21 (Task 1):** a run with no length is `absent`.
- **22 (Task 4):** fixture fixed — the NI file's last strobe now falls before the file ends.

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/schema/core.py`, `schema/request.py`, `schema/coverage.py`, `daemon.py`, `cli/deleting.py` | the new tables and the coverage stage (Task 1); the block tables retired (Task 6) |
| `wl_preproc/schema/request.py` | an activation records its run sets; a derivative's identity (Tasks 2 and 5) |
| `wl_preproc/contracts/protocol.py`, `responder/jobs.py`, `responder/server.py` | the request contract and the check on arrival (Tasks 3 and 5) |
| `wl_preproc/events/runs.py` (new), `nwb/gather.py`, `nwb/intervals.py`, `nwb/describe.py`, `nwb/build.py`, `contracts/nwb_description.py`, `listing/entry.py` | the file built from its runs, and description version 3 (Task 4) |
| `wl_preproc/synth/spikeglx.py` | the NI file's last strobe falls (Task 4) |
| `wl_preproc/events/agreement.py`, `schema/timebase.py` | `block_agreement` retired; the run tolerance (Task 6) |
| `docs/schemas/*.json` | re-exported (Tasks 3, 4 and 5) |
| docs, `wl.yaml` | the protocol, the records, the amendments (Task 7) |

---

### Task 1: The tables runs are asserted and selected in

**Files:**
- Modify: `wl_preproc/schema/core.py`, `wl_preproc/schema/request.py`, `wl_preproc/schema/coverage.py`, `wl_preproc/daemon.py`, `wl_preproc/cli/deleting.py`
- Test: `tests/schema/test_core.py`, `tests/schema/test_coverage.py`, `tests/timebase/test_coverage_rules.py`, `tests/schema/test_request.py`

**Interfaces — produces:**
- `core.RunAssertion`: `-> core.Run`; `works_run_id : varchar(64)`, `start_s : double`, `end_s : double`. wl.works' id and copy of one measured run, recorded by Task 3.
- `request.ActivationRun`: `-> request.Activation`, `-> core.Run` (a file's runs). `request.ActivationProbeRun`: `-> request.Activation`, `probe_serial : varchar(32)`, `-> core.Run` (each probe's sort runs).
- `coverage.RunCoverage`: `-> core.Run`, `-> core.AcquisitionSystem`; `coverage`, `covered_s`, as `BlockCoverage`. `key_source` is `core.Run * core.AcquisitionSystem`; a run with no length is `absent` with 0 s. It runs in `daemon._computed_tables()` after `Segment`.
- `cli/deleting.py`'s `_PARENTS` gains all four, with `core.Run` left out of the map like `pipeline.Session`.

- [ ] **Step 1: Write the failing tests.** Apply these diffs:

```diff
--- a/tests/schema/test_core.py
+++ b/tests/schema/test_core.py
@@ -44,6 +44,7 @@ def test_every_table_declares_and_documents_its_key(core):
         core.AcquisitionSystem,
         core.Segment,
         core.RejectedSegment,
+        core.RunAssertion,
     ):
         assert table.primary_key, table.__name__
         assert table.definition.strip().startswith("#"), (
@@ -269,3 +270,18 @@ def test_rejected_segment_records_why(core, a_session):
         & {**a_session, "system": "rhs", "file_path": "rhs/2027-03-14_03_rhs/amplifier.dat"}
     ).fetch1()
     assert got["reason"] == "no decodable barcode"
+
+
+def test_a_run_assertion_is_keyed_on_its_measured_run_and_round_trips(core, a_session):
+    session = a_session
+    """wl.works' id for a measured run, and the times it holds for it (design
+    spec `2026-10-01-session-listing-and-run-requests-design.md` section 3.3)."""
+    assert set(core.RunAssertion.primary_key) == {"subject", "session_datetime", "run_number"}
+    core.Run.insert1({**session, "run_number": 1, "task_type": 0, "run_start_time": 1.0, "run_stop_time": 9.0,
+                      "closed": 1}, skip_duplicates=True)
+    row = {**session, "run_number": 1, "works_run_id": "asr-77", "start_s": 1.0, "end_s": 9.0}
+    core.RunAssertion.insert1(row)
+    try:
+        assert (core.RunAssertion & session).fetch1() == row
+    finally:
+        (core.RunAssertion & session).delete_quick()
```

```diff
--- a/tests/schema/test_coverage.py
+++ b/tests/schema/test_coverage.py
@@ -149,3 +149,7 @@ def test_coverage_states_are_exactly_full_partial_absent(cov, enum_values):
             f"{table.__name__}.coverage declares {enum_values(declared)}, "
             f"not exactly {expected}"
         )
+
+
+def test_run_coverage_is_per_run_per_system(cov):
+    assert set(cov.RunCoverage.primary_key) == {"subject", "session_datetime", "run_number", "system"}
```

```diff
--- a/tests/timebase/test_coverage_rules.py
+++ b/tests/timebase/test_coverage_rules.py
@@ -177,3 +177,53 @@ def test_block_coverage_populates_a_row_for_every_block_and_system(
     for system, row in outside.items():
         assert row["coverage"] == "absent", f"{system}: {row['coverage']}"
         assert row["covered_s"] == pytest.approx(0.0)
+
+
+
+def test_run_coverage_populates_a_row_for_every_run_and_system(dj_conn, prefix, tmp_path):
+    """As for blocks: the cross product, so a system that recorded none of a
+    run says `absent`. A run with no length, one that faulted at once, is
+    `absent` too, never a failure on every pass (design spec
+    `2026-10-01-session-listing-and-run-requests-design.md` section 5)."""
+    import datetime
+
+    from wl_preproc.schema import core, coverage, ingest, pipeline, timebase
+    from wl_preproc.synth.recipe import RECIPES
+    from wl_preproc.synth.session import generate_session
+
+    coverage.activate(prefix=prefix)
+    timebase.activate(prefix=prefix)
+    ingest.activate(prefix=prefix)
+
+    recipe = RECIPES["drift"]
+    generate_session(tmp_path, recipe)
+    pipeline.lab.Lab.insert1({"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
+                             skip_duplicates=True)
+    pipeline.subject.Subject.insert1({"subject": recipe.subject, "sex": "M",
+                                      "subject_birth_date": datetime.date(2020, 1, 1), "subject_description": ""},
+                                     skip_duplicates=True)
+    session_key = {"subject": recipe.subject, "session_datetime": datetime.datetime(2027, 7, 26, 9, 0)}
+    pipeline.Session.insert1(session_key, skip_duplicates=True)
+    ingest.Ingestion.insert1({**session_key, "ingested_at": datetime.datetime(2027, 7, 26, 19, 0),
+                              "session_dir": str(tmp_path / recipe.session_id), "integrity": "verified",
+                              "topology": {system: "present" for system in recipe.systems},
+                              "manifest_hash": "blake3:test"}, skip_duplicates=True)
+    core.AcquisitionSystem.insert([{**session_key, "system": system} for system in recipe.systems],
+                                  skip_duplicates=True)
+    core.Run.insert([
+        {**session_key, "run_number": 1, "task_type": 0, "run_start_time": 0.0, "run_stop_time": 10.0, "closed": 1},
+        {**session_key, "run_number": 2, "task_type": 0, "run_start_time": 1_000.0, "run_stop_time": 1_010.0,
+         "closed": 1},
+        {**session_key, "run_number": 3, "task_type": 0, "run_start_time": 5.0, "run_stop_time": 5.0, "closed": 0},
+    ], skip_duplicates=True)
+
+    timebase.SystemTimebase.populate()
+    core.Segment.populate()
+    coverage.RunCoverage.populate()
+
+    rows = (coverage.RunCoverage & session_key).to_dicts()
+    assert len(rows) == 3 * len(recipe.systems)
+    by_run = {number: {row["system"]: row["coverage"] for row in rows if row["run_number"] == number}
+              for number in (1, 2, 3)}
+    assert by_run[1] == {system: "full" for system in recipe.systems}
+    assert by_run[2] == by_run[3] == {system: "absent" for system in recipe.systems}
```

```diff
--- a/tests/schema/test_request.py
+++ b/tests/schema/test_request.py
@@ -1409,3 +1409,11 @@ def test_a_failing_release_never_hides_the_refusal(req, selection, monkeypatch):
     monkeypatch.setattr(req, "_release_lock", broken)
     with pytest.raises(req.SupersedeConflict):
         req.submit_replacement("k-rel-3", "neural", "wl_works", selection, {}, None, supersedes_activation_id=0)
+
+
+def test_an_activation_keeps_its_runs_and_each_probes_runs(req):
+    """The file's runs, and the runs each probe's sort covers (design spec
+    `2026-10-01-session-listing-and-run-requests-design.md` section 3.3)."""
+    montage = {"subject", "session_datetime", "montage_id", "activation_id"}
+    assert set(req.ActivationRun.primary_key) == montage | {"run_number"}
+    assert set(req.ActivationProbeRun.primary_key) == montage | {"probe_serial", "run_number"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_core.py tests/schema/test_coverage.py tests/timebase/test_coverage_rules.py tests/schema/test_request.py -q --tb=line -p no:cacheprovider`
Expected: 5 failed, 81 passed. `core` has no `RunAssertion` (both `test_core.py` tests), `coverage` no `RunCoverage` (one test in each coverage file), and `request` no `ActivationRun` (`test_an_activation_keeps_its_runs_and_each_probes_runs`).

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/schema/core.py
+++ b/wl_preproc/schema/core.py
@@ -86,6 +86,24 @@ class RunRecordProblem(dj.Manual):
     """
 
 
+@schema
+class RunAssertion(dj.Manual):
+    definition = """
+    # wl.works' assertion of one measured run: its own id for the run, and the
+    # times it holds for it, recorded by accept() when a request names the
+    # run (design spec 2026-10-01-session-listing-and-run-requests-design.md
+    # section 3.3). Kept apart from the measured Run, against which accept()
+    # checks it as the request arrives. Recorded if absent; a request naming
+    # another id for the same run is refused. Key: (subject,
+    # session_datetime, run_number).
+    -> Run
+    ---
+    works_run_id : varchar(64)  # wl.works' animal_session_run id
+    start_s      : double       # (s) session time, as wl.works holds it
+    end_s        : double
+    """
+
+
 @schema
 class Block(dj.Manual):
     definition = """
```

```diff
--- a/wl_preproc/schema/request.py
+++ b/wl_preproc/schema/request.py
@@ -189,6 +189,35 @@ class ActivationBlock(dj.Manual):
     """
 
 
+
+@schema
+class ActivationRun(dj.Manual):
+    definition = """
+    # The runs this activation's file holds (design spec
+    # 2026-10-01-session-listing-and-run-requests-design.md section 3.3): a
+    # canonical's are its montage's measured runs, a derivative's the runs it
+    # names. Key: (subject, session_datetime, montage_id, activation_id,
+    # run_number).
+    -> Activation
+    -> core.Run
+    """
+
+
+@schema
+class ActivationProbeRun(dj.Manual):
+    definition = """
+    # The runs one probe's sort covers in this activation: a canonical's run
+    # set minus the runs wl.works marked bad on that probe, stated in full by
+    # the request (design spec 2026-10-01-session-listing-and-run-requests-design.md
+    # section 3.1). The sorter reads it; a probe with no row sorts nothing.
+    # Key: (subject, session_datetime, montage_id, activation_id,
+    # probe_serial, run_number).
+    -> Activation
+    probe_serial : varchar(32)
+    -> core.Run
+    """
+
+
 def selection_hash(task_type: str, block_ids: list[int]) -> str:
     """Content hash of a derivative's identity: its task type and block set.
 
```

```diff
--- a/wl_preproc/schema/coverage.py
+++ b/wl_preproc/schema/coverage.py
@@ -70,6 +70,45 @@ class BlockCoverage(dj.Computed):
         self.insert1({**key, "coverage": state, "covered_s": covered_s})
 
 
+@schema
+class RunCoverage(dj.Computed):
+    definition = f"""
+    # How much of one measured run each system recorded (design spec
+    # 2026-10-01-session-listing-and-run-requests-design.md section 5): what
+    # BlockCoverage was for wl.works' asserted blocks, for the runs a file now
+    # holds. Key: (subject, session_datetime, run_number, system).
+    -> core.Run
+    -> core.AcquisitionSystem
+    ---
+    coverage  : {_COVERAGE_ENUM}
+    covered_s : double  # seconds of the run this system actually recorded
+    """
+
+    @property
+    def key_source(self):
+        """Every (run, system) pair of a session, whatever each system did:
+        the cross product, for `BlockCoverage.key_source`'s reason."""
+        return core.Run * core.AcquisitionSystem
+
+    def make(self, key: dict) -> None:
+        """Intersect this run's measured interval with this system's segment
+        extents, by the rule `BlockCoverage.make()` calls. A run that faulted
+        at once can have no length: it covers nothing, and is `absent` rather
+        than a failure on every pass."""
+        from wl_preproc.timebase.coverage import classify_coverage
+
+        run = (core.Run & key).fetch1("run_start_time", "run_stop_time")
+        if run[1] <= run[0]:
+            self.insert1({**key, "coverage": "absent", "covered_s": 0.0})
+            return
+        extents = [
+            (row["start_s"], row["end_s"])
+            for row in (core.Segment & key).to_dicts()
+        ]
+        state, covered_s = classify_coverage(run, extents)
+        self.insert1({**key, "coverage": state, "covered_s": covered_s})
+
+
 @schema
 class TrialCoverage(dj.Computed):
     definition = f"""
```

```diff
--- a/wl_preproc/daemon.py
+++ b/wl_preproc/daemon.py
@@ -171,6 +171,8 @@ def _computed_tables() -> list:
         # section 2.1).
         ephys.ProbeCensus,
         coverage.BlockCoverage,
+        # After `Segment`, whose extents it intersects, as `BlockCoverage`.
+        coverage.RunCoverage,
         # After `Segment` for exactly `BlockCoverage`'s reason -- it intersects
         # a trial's interval with this system's segment extents -- and after
         # `_populate_event_stage()`, which is what puts rows in the
```

```diff
--- a/wl_preproc/cli/deleting.py
+++ b/wl_preproc/cli/deleting.py
@@ -51,6 +51,14 @@ _PARENTS: dict[str, tuple[str, ...]] = {
     "BlockCoverage": ("Block", "AcquisitionSystem"),
     "TrialCoverage": ("AcquisitionSystem",),
     "ActivationBlock": ("Activation", "Block"),
+    # wl.works' asserted runs and the run sets of an activation (design spec
+    # `2026-10-01-session-listing-and-run-requests-design.md` section 3.3).
+    # Their other parent, the measured `core.Run`, is the event stage's and is
+    # not a stage here, so it is left out of the map like `pipeline.Session`.
+    "RunAssertion": (),
+    "RunCoverage": ("AcquisitionSystem",),
+    "ActivationRun": ("Activation",),
+    "ActivationProbeRun": ("Activation",),
 }
 
 
@@ -92,6 +100,10 @@ def _assert_known_tables_are_real() -> None:
         "BlockCoverage": coverage.BlockCoverage,
         "TrialCoverage": coverage.TrialCoverage,
         "ActivationBlock": request.ActivationBlock,
+        "RunAssertion": core.RunAssertion,
+        "RunCoverage": coverage.RunCoverage,
+        "ActivationRun": request.ActivationRun,
+        "ActivationProbeRun": request.ActivationProbeRun,
     }
     assert tables.keys() == _PARENTS.keys(), (
         "the stage-name graph and the real schema tables have drifted apart: "
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 86 passed.

Then the tables' neighbours: `.venv/bin/python -m pytest tests/schema/test_core.py tests/schema/test_coverage.py tests/timebase tests/schema/test_request.py tests/schema/test_daemon.py tests/schema/test_guardrails.py tests/test_cli_guardrails.py -q -p no:cacheprovider`. Expected: 258 passed, 1 skipped.

- [ ] **Step 5: Mutation checks.** Each was measured, against the final tree, to fail the tests named in brackets.
  - T1a (`schema/coverage.py`): `if run[1] <= run[0]:` becomes `if False:` [`test_run_coverage_populates_a_row_for_every_run_and_system`].
  - T1b (`cli/deleting.py`): `"RunCoverage": ("AcquisitionSystem",),` becomes `"RunCoverage": (),` [`tests/schema/test_guardrails.py::test_the_delete_previews_parent_graph_matches_the_real_foreign_keys`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/core.py wl_preproc/schema/request.py wl_preproc/schema/coverage.py wl_preproc/daemon.py wl_preproc/cli/deleting.py tests/schema/test_core.py tests/schema/test_coverage.py tests/timebase/test_coverage_rules.py tests/schema/test_request.py
git commit -m "feat(schema): the tables runs are asserted and selected in -- core.RunAssertion (wl.works' id and times for a measured run), request.ActivationRun and ActivationProbeRun (a file's runs and each probe's), and coverage.RunCoverage as a daemon stage

<trailer lines>"
```

---

### Task 2: An activation records its runs, and a derivative is its run set

**Files:**
- Modify: `wl_preproc/schema/request.py`
- Test: `tests/schema/test_request.py`

**Interfaces — consumes:** Task 1's `ActivationRun` and `ActivationProbeRun`.

**Interfaces — produces** (the block parameters stay until Task 5 retires them):
- `request.selection_hash(task_type: str, block_ids: list[int], run_numbers: list[int] | tuple[int, ...] = ()) -> str`: `run_numbers` joins the hashed selection only when given, so a block hash is unchanged.
- `request._record_run_sets(key: dict, run_numbers, probe_runs: dict[str, list[int]] | None) -> None`, inside the activation's own transaction.
- `submit(..., block_ids=(), run_numbers=(), probe_runs=None)`, `submit_replacement(..., *, supersedes_activation_id, block_ids=(), run_numbers=(), probe_runs=None)`, and `submit_derivative(idempotency_key, task_type, origin, selection, block_ids, payload, requested_by=None, run_numbers=())`, which needs at least one run or block id.

- [ ] **Step 1: Write the failing tests.** Apply this diff:

```diff
--- a/tests/schema/test_request.py
+++ b/tests/schema/test_request.py
@@ -209,6 +209,10 @@ def selection(req):
             {**key, "block_id": block_id, "task_type": "neural", "start_s": start_s, "end_s": end_s},
             skip_duplicates=True,
         )
+        # The measured runs the same windows hold: ActivationRun's
+        # `-> core.Run` is a real foreign key too.
+        core.Run.insert1({**key, "run_number": block_id, "task_type": 0, "run_start_time": start_s,
+                          "run_stop_time": end_s, "closed": 1}, skip_duplicates=True)
     return {**key, "montage_id": montage_id}
 
 
@@ -1417,3 +1421,56 @@ def test_an_activation_keeps_its_runs_and_each_probes_runs(req):
     montage = {"subject", "session_datetime", "montage_id", "activation_id"}
     assert set(req.ActivationRun.primary_key) == montage | {"run_number"}
     assert set(req.ActivationProbeRun.primary_key) == montage | {"probe_serial", "run_number"}
+
+
+# -- Runs, alongside blocks until the block paths are retired (design spec
+# `2026-10-01-session-listing-and-run-requests-design.md` section 3.3).
+
+
+def _runs_of(req, key):
+    return sorted(int(n) for n in (req.ActivationRun & key).to_arrays("run_number"))
+
+
+def _probe_runs_of(req, key):
+    return sorted((row["probe_serial"], row["run_number"]) for row in (req.ActivationProbeRun & key).to_dicts())
+
+
+def test_a_canonical_keeps_its_runs_and_each_probes_runs(req, selection):
+    key = req.submit("runs-canonical-1", "neural", "wl_works", selection, {}, None, run_numbers=[2, 1, 2],
+                     probe_runs={"19011110001": [1, 2], "19011110002": [2]})
+    assert _runs_of(req, key) == [1, 2]
+    assert _probe_runs_of(req, key) == [("19011110001", 1), ("19011110001", 2), ("19011110002", 2)]
+
+
+def test_a_replacement_keeps_its_own_runs(req, selection):
+    first = req.submit("runs-replaced-1", "neural", "wl_works", selection, {}, None, run_numbers=[1, 2, 3],
+                       probe_runs={"19011110001": [1, 2, 3]})
+    second = req.submit_replacement("runs-replaced-2", "neural", "wl_works", selection, {}, None,
+                                    supersedes_activation_id=first["activation_id"], run_numbers=[1, 2, 3],
+                                    probe_runs={"19011110001": [1, 3]})
+    assert second["activation_id"] != first["activation_id"]
+    assert _probe_runs_of(req, first) == [("19011110001", 1), ("19011110001", 2), ("19011110001", 3)]
+    assert _probe_runs_of(req, second) == [("19011110001", 1), ("19011110001", 3)]
+
+
+def test_a_derivative_is_its_run_set(req, selection):
+    """The requester's decision 3: a derivative selects whole runs, and its
+    identity is that set."""
+    one = req.submit_derivative("runs-deriv-1", "neural", "wl_works", selection, [], {}, None, run_numbers=[3, 2])
+    again = req.submit_derivative("runs-deriv-2", "neural", "wl_works", selection, [], {}, None, run_numbers=[2, 3])
+    other = req.submit_derivative("runs-deriv-3", "neural", "wl_works", selection, [], {}, None, run_numbers=[2])
+    assert one == again and other != one
+    assert (_runs_of(req, one), _runs_of(req, other)) == ([2, 3], [2])
+    assert len(req.ActivationBlock & one) == 0
+
+
+def test_the_selection_hash_separates_run_sets_and_leaves_block_hashes_as_they_were():
+    import hashlib
+    import json
+
+    from wl_preproc.schema.request import selection_hash
+
+    assert selection_hash("neural", [], [1, 2]) == selection_hash("neural", [], [2, 1, 2])
+    assert selection_hash("neural", [], [1, 2]) != selection_hash("neural", [], [1])
+    before = json.dumps({"task_type": "neural", "block_ids": [1, 2]}, sort_keys=True, separators=(",", ":"))
+    assert selection_hash("neural", [2, 1]) == hashlib.blake2b(before.encode(), digest_size=16).hexdigest()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_request.py -q --tb=line -p no:cacheprovider`
Expected: 4 failed, 57 passed. `submit`, `submit_replacement` and `submit_derivative` take no `run_numbers` (three tests), and `selection_hash` no run set (`test_the_selection_hash_separates_run_sets_and_leaves_block_hashes_as_they_were`).

- [ ] **Step 3: Implement.** Apply this diff:

```diff
--- a/wl_preproc/schema/request.py
+++ b/wl_preproc/schema/request.py
@@ -218,7 +218,7 @@ class ActivationProbeRun(dj.Manual):
     """
 
 
-def selection_hash(task_type: str, block_ids: list[int]) -> str:
+def selection_hash(task_type: str, block_ids: list[int], run_numbers: list[int] | tuple[int, ...] = ()) -> str:
     """Content hash of a derivative's identity: its task type and block set.
 
     Sorted and de-duplicated first, because the block set is a *set*: two
@@ -268,14 +268,29 @@ def selection_hash(task_type: str, block_ids: list[int]) -> str:
     happens to existing `selection_hash` rows; it is not a one-line edit in
     either function.
     """
-    payload = json.dumps(
-        {"task_type": task_type, "block_ids": sorted(set(block_ids))},
-        sort_keys=True,
-        separators=(",", ":"),
-    )
+    selected = {"task_type": task_type, "block_ids": sorted(set(block_ids))}
+    # A run set joins the hash only when there is one, so every block-only
+    # hash already on file is unchanged (design spec
+    # `2026-10-01-session-listing-and-run-requests-design.md` section 3.3).
+    if run_numbers:
+        selected["run_numbers"] = sorted(set(run_numbers))
+    payload = json.dumps(selected, sort_keys=True, separators=(",", ":"))
     return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()
 
 
+def _record_run_sets(key: dict, run_numbers, probe_runs: dict[str, list[int]] | None) -> None:
+    """The activation's runs, and the runs each probe's sort covers (design
+    spec `2026-10-01-session-listing-and-run-requests-design.md` section 3.3),
+    written in the activation's own transaction."""
+    if run_numbers:
+        ActivationRun.insert([{**key, "run_number": number} for number in sorted(set(run_numbers))],
+                             skip_duplicates=True)
+    rows = [{**key, "probe_serial": serial, "run_number": number}
+            for serial, numbers in sorted((probe_runs or {}).items()) for number in sorted(set(numbers))]
+    if rows:
+        ActivationProbeRun.insert(rows, skip_duplicates=True)
+
+
 def _canonicalise(value):
     """`value` with every dict's keys in sorted order, at every depth.
 
@@ -444,6 +459,8 @@ def submit(
     payload: dict,
     requested_by: str | None = None,
     block_ids: list[int] | tuple[int, ...] = (),
+    run_numbers: list[int] | tuple[int, ...] = (),
+    probe_runs: dict[str, list[int]] | None = None,
 ) -> dict:
     """Record a request and the canonical activation it selects, atomically.
 
@@ -583,6 +600,7 @@ def submit(
                 [{**key, "block_id": block_id} for block_id in sorted(set(block_ids))],
                 skip_duplicates=True,
             )
+        _record_run_sets(key, run_numbers, probe_runs)
         return key
 
 
@@ -665,6 +683,8 @@ def submit_replacement(
     *,
     supersedes_activation_id: int,
     block_ids: list[int] | tuple[int, ...] = (),
+    run_numbers: list[int] | tuple[int, ...] = (),
+    probe_runs: dict[str, list[int]] | None = None,
 ) -> dict:
     """Record a request and the canonical activation that replaces the
     montage's current one, `supersedes_activation_id`: at the montage's next free
@@ -780,6 +800,7 @@ def submit_replacement(
                 ActivationBlock.insert(
                     [{**key, "block_id": block_id} for block_id in sorted(set(block_ids))]
                 )
+            _record_run_sets(key, run_numbers, probe_runs)
             return key
 
     try:
@@ -920,6 +941,7 @@ def submit_derivative(
     block_ids: list[int],
     payload: dict,
     requested_by: str | None = None,
+    run_numbers: list[int] | tuple[int, ...] = (),
 ) -> dict:
     """Record a request and the derivative activation its block set selects.
 
@@ -1073,7 +1095,7 @@ def submit_derivative(
             "one. Call it as its own unit of work; see submit()'s docstring."
         )
 
-    if not block_ids:
+    if not block_ids and not run_numbers:
         # Checked before opening the transaction, alongside the two guards
         # above: block_ids=[] is not a smaller selection, it is not a
         # selection at all. Without this, selection_hash("neural", [])
@@ -1082,9 +1104,9 @@ def submit_derivative(
         # rows, a derivative covering nothing, which section 8.3's "any
         # hand-picked subset" does not describe (review round 2, Minor).
         raise dj.DataJointError(
-            "submit_derivative() needs at least one block id: block_ids=[] "
-            "would create a derivative covering nothing, which is not a "
-            "valid selection."
+            "submit_derivative() needs at least one run or block id: an empty "
+            "selection would create a derivative covering nothing, which is "
+            "not a valid selection."
         )
 
     import datetime as _dt
@@ -1092,7 +1114,7 @@ def submit_derivative(
     montage_key = {
         k: selection[k] for k in ("subject", "session_datetime", "montage_id")
     }
-    digest = selection_hash(task_type, block_ids)
+    digest = selection_hash(task_type, block_ids, run_numbers)
     selection_key = {**montage_key, "selection_hash": digest}
 
     with dj.conn().transaction:
@@ -1190,9 +1212,11 @@ def submit_derivative(
                 "either sustained genuine contention or a stuck retry loop."
             )
 
-        ActivationBlock.insert(
-            [{**key, "block_id": block_id} for block_id in sorted(set(block_ids))]
-        )
+        if block_ids:
+            ActivationBlock.insert(
+                [{**key, "block_id": block_id} for block_id in sorted(set(block_ids))]
+            )
+        _record_run_sets(key, run_numbers, None)
         return key
 
 
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 61 passed.

Then: `.venv/bin/python -m pytest tests/schema/test_request.py tests/schema/test_guardrails.py -q -p no:cacheprovider`. Expected: 72 passed.

- [ ] **Step 5: Mutation check.**
  - T2b (`schema/request.py::_record_run_sets`): `for number in sorted(set(numbers))]` becomes `for number in sorted(set(numbers))[:1]]` [`test_a_canonical_keeps_its_runs_and_each_probes_runs`, `test_a_replacement_keeps_its_own_runs`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/request.py tests/schema/test_request.py
git commit -m "feat(request): an activation records its runs and each probe's runs, and a derivative is identified by its run set -- alongside blocks, until their paths are retired

<trailer lines>"
```

---

### Task 3: A request may name runs, each checked as it arrives

**Files:**
- Modify: `wl_preproc/contracts/protocol.py`, `wl_preproc/responder/jobs.py`, `wl_preproc/responder/server.py`, `docs/schemas/job_request.json` (re-exported)
- Test: `tests/contracts/test_protocol.py`, `tests/responder/test_jobs.py`

**Interfaces — consumes:** Task 2's `submit*` with `run_numbers` and `probe_runs`; Task 1's `core.RunAssertion`.

**Interfaces — produces:**
- `contracts.protocol.RunEntry(run_number: 1..32767, start_s, end_s: finite, works_run_id: 1..64 chars)`; `MetadataBundle.runs: list[RunEntry] = []`.
- `responder.jobs.RunIdConflict(Exception)`, which `server.py::_translate_accept_errors` answers as a `409`.
- `jobs._check_runs(session_key, montage_row, asserted: list[RunEntry], run_numbers: list[int], canonical: bool) -> tuple[list[int], list[dict]]`: the file's runs and the `RunAssertion` rows to record. `jobs._check_probe_runs(probes, probe_runs, file_runs) -> None`.
- `accept()` checks a request that names runs; one that names blocks keeps the block path until Task 5, and one that mixes them is refused.

**Why the check is here, as the request arrives.** `TimingProvenance.block_agreement` is computed once per session, before any request exists, and nothing recomputes it, so the check wl.works relied on never ran in its flow (spec §5). This one runs on every request, against `core.Run`, before anything is written.

- [ ] **Step 1: Write the failing tests.** Apply these diffs:

```diff
--- a/tests/contracts/test_protocol.py
+++ b/tests/contracts/test_protocol.py
@@ -316,3 +316,24 @@ def test_an_assignment_outside_wl_works_own_shape_is_refused(assignment):
     with pytest.raises(ValidationError):
         MetadataBundle.model_validate(
             _bundle(probes=[{"serial": "NP-1", "insertion_number": 1, "area_assignment": assignment}]))
+
+
+@pytest.mark.parametrize("entry", [
+    {"run_number": 0, "start_s": 0.0, "end_s": 1.0, "works_run_id": "w"},
+    {"run_number": 32768, "start_s": 0.0, "end_s": 1.0, "works_run_id": "w"},
+    {"run_number": 1, "start_s": float("nan"), "end_s": 1.0, "works_run_id": "w"},
+    {"run_number": 1, "start_s": 0.0, "end_s": 1.0, "works_run_id": ""},
+    {"run_number": 1, "start_s": 0.0, "end_s": 1.0, "works_run_id": "w" * 65},
+    {"run_number": 1, "start_s": 0.0, "end_s": 1.0, "works_run_id": "w", "block_id": 1},
+])
+def test_a_run_entry_holds_a_measured_runs_number_times_and_wl_works_id(entry):
+    """`metadata.runs`, typed as `metadata.blocks` never was (design spec
+    `2026-10-01-session-listing-and-run-requests-design.md` section 3.1): a
+    run number core.Run can hold, finite times, and an id its column holds."""
+    from pydantic import ValidationError
+
+    from wl_preproc.contracts.protocol import RunEntry
+
+    RunEntry.model_validate({"run_number": 1, "start_s": 0.0, "end_s": 1.0, "works_run_id": "w"})
+    with pytest.raises(ValidationError):
+        RunEntry.model_validate(entry)
```

```diff
--- a/tests/responder/test_jobs.py
+++ b/tests/responder/test_jobs.py
@@ -4,6 +4,7 @@
 from __future__ import annotations
 
 import datetime
+import re
 
 import pytest
 
@@ -1230,3 +1231,152 @@ def test_a_null_supersedes_is_absent(landed_session, prefix, selection):
     key = accept(_lifecycle_job("jblc006", when, f"jblc006-{len(selection)}", **selection), prefix=prefix)
     row = (schema_request.Activation & key).fetch1()
     assert (row["activation_id"], row["role"], row["supersedes"]) == (0, "canonical", None)
+
+
+# -- Requests that name runs (design spec
+# `2026-10-01-session-listing-and-run-requests-design.md` section 3). Run 3 lies
+# outside montage 0's window [0, 12).
+
+_RUNS = [(1, 0.0, 4.0), (2, 5.0, 11.0), (3, 13.0, 16.0)]
+_SERIAL = "19011110001"
+
+
+def _landed_with_runs(landed_session, subject: str, day: int, runs=_RUNS) -> dict:
+    from wl_preproc.schema import core
+
+    key = landed_session(subject, datetime.datetime(2027, 9, day, 9, 0))
+    core.Run.insert([{**key, "run_number": number, "task_type": 0, "run_start_time": start, "run_stop_time": stop,
+                      "closed": 1} for number, start, stop in runs], skip_duplicates=True)
+    return key
+
+
+def _runs_job(key: dict, idempotency_key: str, *, runs=_RUNS, ids: dict | None = None,
+              serials=(_SERIAL,), **selection) -> JobRequest:
+    return JobRequest(
+        domain="neural",
+        selection={"session_datetime": key["session_datetime"], "montage_id": 0, **selection},
+        parameters={},
+        idempotency_key=idempotency_key,
+        metadata=MetadataBundle(
+            blocks=[], montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
+            runs=[{"run_number": number, "start_s": start, "end_s": stop,
+                   "works_run_id": (ids or {}).get(number, f"wr-{number}")} for number, start, stop in runs],
+            probes=[{"serial": serial, "insertion_number": index} for index, serial in enumerate(serials, start=1)],
+            experimenter="jw", subject=key["subject"], task_types=[]),
+    )
+
+
+def _run_rows(table, key) -> list:
+    return sorted(tuple(row[name] for name in ("probe_serial", "run_number") if name in row)
+                  for row in (table & key).to_dicts())
+
+
+def test_a_canonical_naming_runs_records_them_and_holds_its_montages_runs(landed_session, prefix):
+    """The requester's decision 1: the file holds every measured run whose
+    start lies in the montage's window; each probe's list is its sort's."""
+    from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema import core
+    from wl_preproc.schema import request as schema_request
+
+    key = _landed_with_runs(landed_session, "runjob1", 1)
+    activation = accept(_runs_job(key, "runjob1-k1", probe_runs={_SERIAL: [1]}), prefix=prefix)
+    assert sorted((row["run_number"], row["works_run_id"]) for row in (core.RunAssertion & key).to_dicts()) == [
+        (1, "wr-1"), (2, "wr-2"), (3, "wr-3")]
+    assert _run_rows(schema_request.ActivationRun, activation) == [(1,), (2,)]
+    assert _run_rows(schema_request.ActivationProbeRun, activation) == [(_SERIAL, 1)]
+
+
+@pytest.mark.parametrize("runs, expect", [
+    ([(1, 0.01, 4.0), (2, 5.0, 11.0)], "run 1's start, 0.01 s, is not the measured 0.0 s"),
+    ([(1, 0.0, 4.0), (2, 5.0, 11.5)], "run 2's end, 11.5 s, is not the measured 11.0 s"),
+    ([(1, 0.0, 4.0), (2, 5.0, 11.0), (9, 20.0, 21.0)], "run 9 is not a measured run of this session"),
+    ([(1, 0.0, 4.0)], "measured run(s) [2] lie in montage 0's window"),
+])
+def test_a_request_from_a_stale_listing_is_a_run_mismatch_and_writes_nothing(landed_session, prefix, runs, expect):
+    """Checked as the request arrives, against `core.Run`, within about 2 ms
+    (design spec section 3.2): the reason names the run and says to rebuild."""
+    from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema import core
+    from wl_preproc.schema import request as schema_request
+
+    key = _landed_with_runs(landed_session, "runjob2", 2)
+    with pytest.raises(ValueError, match=re.escape(expect)) as refused:
+        accept(_runs_job(key, f"runjob2-{len(runs)}-{runs[-1][2]}", runs=runs, probe_runs={_SERIAL: [1]}),
+               prefix=prefix)
+    assert "rebuild the request from a fresh GET /sessions" in str(refused.value)
+    assert (len(core.RunAssertion & key), len(schema_request.Activation & key)) == (0, 0)
+
+
+def test_a_run_asserted_again_under_another_id_is_a_conflict(landed_session, prefix):
+    """A 409, which wl.works stops on: the two disagree about which run this
+    is. Through the server's translation, as wl.works meets it."""
+    from wl_preproc.responder.handler import ConflictError
+    from wl_preproc.responder.jobs import RunIdConflict, accept
+    from wl_preproc.responder.server import _translate_accept_errors
+
+    key = _landed_with_runs(landed_session, "runjob3", 3)
+    accept(_runs_job(key, "runjob3-k1", probe_runs={_SERIAL: [1, 2]}), prefix=prefix)
+    renamed = _runs_job(key, "runjob3-k2", ids={2: "wr-other"}, probe_runs={_SERIAL: [1, 2]})
+    with pytest.raises(RunIdConflict, match="run 2 is recorded with works_run_id 'wr-2'; this request names 'wr-other'"):
+        accept(renamed, prefix=prefix)
+    with pytest.raises(ConflictError):
+        _translate_accept_errors(renamed, prefix=prefix)
+
+
+def test_runs_not_yet_measured_are_not_yet_ingested(landed_session, prefix):
+    """wl.works retries this one: the event stage has not read the session."""
+    from wl_preproc.responder.jobs import accept
+
+    key = landed_session("runjob4", datetime.datetime(2027, 9, 4, 9, 0))
+    with pytest.raises(ValueError, match="has no measured run on this host yet"):
+        accept(_runs_job(key, "runjob4-k1", probe_runs={_SERIAL: [1]}), prefix=prefix)
+
+
+@pytest.mark.parametrize("probe_runs, expect", [
+    (None, "selection.probe_runs must give every probe's runs"),
+    ({}, "selection.probe_runs names [], and metadata.probes names ['19011110001']"),
+    ({_SERIAL: [1], "19011110002": [1]}, "selection.probe_runs names ['19011110001', '19011110002']"),
+    ({_SERIAL: [3]}, "probe 19011110001's runs [3] are not among the file's runs [1, 2]"),
+    ({_SERIAL: ["1"]}, "selection.probe_runs['19011110001'] must be a list of run numbers"),
+])
+def test_each_probes_runs_are_stated_in_full_and_within_the_file(landed_session, prefix, probe_runs, expect):
+    """wl.works' Plan 20 rule: the record is the run set each probe resolved
+    to, so every probe's list is stated, and only the file's runs."""
+    from wl_preproc.responder.jobs import accept
+
+    key = _landed_with_runs(landed_session, "runjob5", 5)
+    selection = {} if probe_runs is None else {"probe_runs": probe_runs}
+    with pytest.raises(ValueError, match=re.escape(expect)):
+        accept(_runs_job(key, f"runjob5-{expect[:20]}", **selection), prefix=prefix)
+
+
+def test_a_derivative_names_its_runs(landed_session, prefix):
+    """The requester's decision 3."""
+    from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema import request as schema_request
+
+    key = _landed_with_runs(landed_session, "runjob6", 6)
+    activation = accept(_runs_job(key, "runjob6-k1", run_numbers=[2]), prefix=prefix)
+    assert (schema_request.Activation & activation).fetch1("role") == "derivative"
+    assert _run_rows(schema_request.ActivationRun, activation) == [(2,)]
+
+
+@pytest.mark.parametrize("selection, blocks, expect", [
+    ({"block_ids": [1], "probe_runs": {_SERIAL: [1]}}, [], "a request names runs or blocks, not both"),
+    ({"probe_runs": {_SERIAL: [1]}}, [{"block_id": 1, "task_type": "x", "start_s": 0.0, "end_s": 4.0}],
+     "a request names runs or blocks, not both"),
+    ({"role": "canonical", "run_numbers": [1], "probe_runs": {_SERIAL: [1]}}, [],
+     "selection.run_numbers is a derivative's"),
+    ({"run_numbers": [2], "probe_runs": {_SERIAL: [2]}}, [], "selection.probe_runs is a canonical's"),
+    ({"run_numbers": ["2"]}, [], "selection.run_numbers must be a list of run numbers"),
+    ({"run_numbers": [3]}, [], "selection names run(s) [3] outside montage 0's window"),
+])
+def test_a_selection_that_mixes_or_misplaces_runs_is_refused(landed_session, prefix, selection, blocks, expect):
+    from wl_preproc.responder.jobs import accept
+
+    key = _landed_with_runs(landed_session, "runjob7", 7)
+    job = _runs_job(key, f"runjob7-{expect[:24]}", **selection)
+    if blocks:
+        job = job.model_copy(update={"metadata": job.metadata.model_copy(update={"blocks": blocks})})
+    with pytest.raises(ValueError, match=re.escape(expect)):
+        accept(job, prefix=prefix)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/contracts/test_protocol.py tests/responder/test_jobs.py -q --tb=line -p no:cacheprovider`
Expected: 25 failed, 89 passed. `RunEntry` does not exist (the six cases of `test_a_run_entry_holds_a_measured_runs_number_times_and_wl_works_id`), and `MetadataBundle` refuses `runs` as an unknown field, so each of the 19 run cases in `test_jobs.py` fails building its request.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/contracts/protocol.py
+++ b/wl_preproc/contracts/protocol.py
@@ -246,6 +246,20 @@ class SubjectDetails(BaseModel):
     date_of_birth: datetime.date | None = None
 
 
+class RunEntry(BaseModel):
+    """One run wl.works holds for the session: its copy of a run `GET
+    /sessions` listed, measured from the recording, and its own id for it
+    (design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    section 3.1). Checked against `core.Run` as the request arrives."""
+
+    model_config = ConfigDict(extra="forbid", frozen=True)
+
+    run_number: Annotated[int, Field(ge=1, le=32767)]  # core.Run.run_number : smallint, from 1
+    start_s: _SessionSeconds
+    end_s: _SessionSeconds
+    works_run_id: Annotated[str, Field(min_length=1, max_length=64)]  # core.RunAssertion : varchar(64)
+
+
 class MetadataBundle(BaseModel):
     """Everything wl-preproc needs from the ELN, carried inbound with the request."""
 
@@ -265,6 +279,9 @@ class MetadataBundle(BaseModel):
     subject: str
     task_types: list[str]
     subject_details: SubjectDetails | None = None
+    # Every run wl.works holds for the session (design spec
+    # `2026-10-01-session-listing-and-run-requests-design.md` section 3.1).
+    runs: list[RunEntry] = []
 
 
 class JobRequest(BaseModel):
```

```diff
--- a/wl_preproc/responder/jobs.py
+++ b/wl_preproc/responder/jobs.py
@@ -113,7 +113,8 @@ from __future__ import annotations
 import datetime
 import math
 
-from wl_preproc.contracts.protocol import JobRequest, MontageBoundary
+from wl_preproc.contracts.protocol import JobRequest, MontageBoundary, ProbeEntry, RunEntry
+from wl_preproc.events import agreement
 from wl_preproc.ingest import landing
 from wl_preproc.schema import DEFAULT_PREFIX, core, pipeline
 from wl_preproc.schema import request as schema_request
@@ -233,6 +234,104 @@ def _lifecycle_role(selection: dict, block_ids: list) -> bool:
     return role == "canonical" or (role is None and not block_ids)
 
 
+class RunIdConflict(Exception):
+    """A run already recorded under one wl.works id, named again under
+    another (design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    section 3.2): the two disagree about which run this is, which resending
+    cannot fix. `server.py` answers it with a 409, as for a reused key."""
+
+
+_REBUILD = "rebuild the request from a fresh GET /sessions"
+
+
+def _run_numbers(value, *, name: str) -> list[int]:
+    if not isinstance(value, list) or any(isinstance(n, bool) or not isinstance(n, int) for n in value):
+        raise ValueError(f"{name} must be a list of run numbers, got {value!r}")
+    return value
+
+
+def _check_runs(session_key: dict, montage_row: dict, asserted: list[RunEntry], run_numbers: list[int],
+                canonical: bool) -> tuple[list[int], list[dict]]:
+    """The file's runs, and the `core.RunAssertion` rows to record, after
+    checking every asserted run against the measured `core.Run` as the
+    request arrives (design spec
+    `2026-10-01-session-listing-and-run-requests-design.md` section 3.2).
+
+    A canonical holds every measured run whose start lies in its montage's
+    window, and each must be asserted here; a derivative holds the runs it
+    names, each measured, in the window, and asserted here or before."""
+    measured = {row["run_number"]: row for row in (core.Run & session_key).to_dicts()}
+    if not measured:
+        raise ValueError(
+            f"session {session_key['subject']}/{session_key['session_datetime'].isoformat()} has no measured "
+            "run on this host yet: its event codes are not yet read, or the rig sent no run markers. Resend "
+            "once GET /sessions lists its runs."
+        )
+    by_number: dict[int, RunEntry] = {}
+    for entry in asserted:
+        if entry.run_number in by_number:
+            raise ValueError(f"metadata.runs names run {entry.run_number} twice")
+        by_number[entry.run_number] = entry
+    rows = []
+    for number, entry in sorted(by_number.items()):
+        run = measured.get(number)
+        if run is None:
+            raise ValueError(f"run {number} is not a measured run of this session; {_REBUILD}")
+        for end, asserted_s, measured_s in (("start", entry.start_s, run["run_start_time"]),
+                                            ("end", entry.end_s, run["run_stop_time"])):
+            if abs(asserted_s - measured_s) > agreement.block_agreement_tolerance_s(measured_s, asserted_s):
+                raise ValueError(f"run {number}'s {end}, {asserted_s} s, is not the measured {measured_s} s; "
+                                 f"{_REBUILD}")
+        rows.append({**session_key, "run_number": number, "works_run_id": entry.works_run_id,
+                     "start_s": entry.start_s, "end_s": entry.end_s})
+    on_record = {row["run_number"]: row["works_run_id"] for row in (core.RunAssertion & session_key).to_dicts()}
+    for row in rows:
+        known = on_record.get(row["run_number"])
+        if known is not None and known != row["works_run_id"]:
+            raise RunIdConflict(f"run {row['run_number']} is recorded with works_run_id {known!r}; this request "
+                                f"names {row['works_run_id']!r}")
+    window = [number for number, run in sorted(measured.items())
+              if montage_row["start_s"] <= run["run_start_time"] < montage_row["end_s"]]
+    bounds = f"montage {montage_row['montage_id']}'s window [{montage_row['start_s']}, {montage_row['end_s']})"
+    if canonical:
+        if not window:
+            raise ValueError(f"{bounds} holds no measured run; {_REBUILD}")
+        missing = [number for number in window if number not in by_number]
+        if missing:
+            raise ValueError(f"measured run(s) {missing} lie in {bounds} and the request does not assert them; "
+                             f"{_REBUILD}")
+        return window, rows
+    named = sorted(set(run_numbers))
+    for problem, numbers in (("are not measured runs of this session", [n for n in named if n not in measured]),
+                             (f"outside {bounds}", [n for n in named if n in measured and n not in window]),
+                             ("asserted neither in metadata.runs nor before",
+                              [n for n in named if n not in by_number and n not in on_record])):
+        if numbers:
+            raise ValueError(f"selection names run(s) {numbers} {problem}; {_REBUILD}")
+    return named, rows
+
+
+def _check_probe_runs(probes: list[ProbeEntry], probe_runs, file_runs: list[int]) -> None:
+    """Every probe's runs are stated in full, and only the file's (design
+    spec `2026-10-01-session-listing-and-run-requests-design.md` section 3.1;
+    wl.works' Plan 20 rule: the record is the run set each probe resolved
+    to, not the exclusions)."""
+    serials = sorted({probe.serial for probe in probes})
+    if probe_runs is None:
+        if serials:
+            raise ValueError(f"selection.probe_runs must give every probe's runs; metadata.probes names {serials}")
+        return
+    if not isinstance(probe_runs, dict) or any(not isinstance(serial, str) for serial in probe_runs):
+        raise ValueError(f"selection.probe_runs must map each probe's serial to its runs, got {probe_runs!r}")
+    if sorted(probe_runs) != serials:
+        raise ValueError(f"selection.probe_runs names {sorted(probe_runs)}, and metadata.probes names {serials}: "
+                         "every probe's runs are stated, and only theirs")
+    for serial, numbers in sorted(probe_runs.items()):
+        outside = sorted(set(_run_numbers(numbers, name=f"selection.probe_runs[{serial!r}]")) - set(file_runs))
+        if outside:
+            raise ValueError(f"probe {serial}'s runs {outside} are not among the file's runs {file_runs}")
+
+
 def _reject_out_of_range_int(value, *, name: str, bounds: tuple[int, int]) -> None:
     low, high = bounds
     if isinstance(value, bool) or not isinstance(value, int):
@@ -513,11 +612,23 @@ def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
     """
     selection = request.selection
     _require_selection_keys(selection)
+    metadata = request.metadata
     # Before anything reads the database: a malformed lifecycle selection
-    # is the caller's to fix, whatever this host holds.
-    canonical = _lifecycle_role(selection, selection.get("block_ids") or [])
+    # is the caller's to fix, whatever this host holds. A request names runs
+    # or blocks (design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    # section 3.1).
+    run_numbers = _run_numbers(selection.get("run_numbers") or [], name="selection.run_numbers")
+    probe_runs = selection.get("probe_runs")
+    names_runs = bool(metadata.runs or run_numbers or probe_runs is not None)
+    if names_runs and (selection.get("block_ids") or metadata.blocks):
+        raise ValueError("a request names runs or blocks, not both: metadata.runs, selection.run_numbers and "
+                         "selection.probe_runs, or metadata.blocks and selection.block_ids")
+    canonical = _lifecycle_role(selection, selection.get("block_ids") or run_numbers)
+    if canonical and run_numbers:
+        raise ValueError("selection.run_numbers is a derivative's: a canonical holds every run of its montage")
+    if not canonical and probe_runs is not None:
+        raise ValueError("selection.probe_runs is a canonical's: a derivative's runs are its run_numbers")
 
-    metadata = request.metadata
     _reject_oversized_subject(metadata.subject)
 
     session_datetime = landing.to_naive_utc(_coerce_session_datetime(selection["session_datetime"]))
@@ -579,6 +690,12 @@ def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
     # written. ----
 
     # Step 1 (design spec section 6.1): Montage rows, insert-if-absent.
+    file_runs, run_assertion_rows = [], []
+    if names_runs:
+        file_runs, run_assertion_rows = _check_runs(session_key, montage_row, metadata.runs, run_numbers, canonical)
+        if canonical:
+            _check_probe_runs(metadata.probes, probe_runs, file_runs)
+
     _record_subject_details(metadata.subject, metadata.subject_details)
     _record_probe_reports(session_key, metadata.probes, prefix)
     if montage_rows:
@@ -597,6 +714,8 @@ def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
     # occupied by an asserted value rather than a decoded one.
     if block_rows:
         core.Block.insert(block_rows, skip_duplicates=True)
+    if run_assertion_rows:
+        core.RunAssertion.insert(run_assertion_rows, skip_duplicates=True)
 
     # The payload stored as evidence ("the request as received", Request's
     # own comment). mode="json" -- this project's own existing convention in
@@ -632,6 +751,7 @@ def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
             block_ids=list(block_ids),
             payload=payload,
             requested_by=metadata.experimenter,
+            run_numbers=file_runs,
         )
 
     if selection.get("supersedes_activation_id") is not None:
@@ -644,6 +764,8 @@ def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
             requested_by=metadata.experimenter,
             supersedes_activation_id=selection["supersedes_activation_id"],
             block_ids=list(block_ids),
+            run_numbers=file_runs,
+            probe_runs=probe_runs,
         )
     return schema_request.submit(
         idempotency_key=request.idempotency_key,
@@ -653,4 +775,6 @@ def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
         payload=payload,
         requested_by=metadata.experimenter,
         block_ids=list(block_ids),
+        run_numbers=file_runs,
+        probe_runs=probe_runs,
     )
```

```diff
--- a/wl_preproc/responder/server.py
+++ b/wl_preproc/responder/server.py
@@ -170,7 +170,7 @@ def _translate_accept_errors(request, *, prefix: str) -> dict:
     """
     try:
         return jobs.accept(request, prefix=prefix)
-    except (KeyReuseError, SupersedeConflict) as exc:
+    except (KeyReuseError, SupersedeConflict, jobs.RunIdConflict) as exc:
         # Both are disagreements resending cannot fix: a reused key, or a
         # replacement naming a canonical that is no longer current (design
         # spec `2026-09-30-canonical-lifecycle-design.md` section 3).
```

Then re-export the contracts, `.venv/bin/python -m wl_preproc.cli.main schemas export --out docs/schemas`, which changes `job_request.json` by:

```diff
--- a/docs/schemas/job_request.json
+++ b/docs/schemas/job_request.json
@@ -97,6 +97,14 @@
           "title": "Probes",
           "type": "array"
         },
+        "runs": {
+          "default": [],
+          "items": {
+            "$ref": "#/$defs/RunEntry"
+          },
+          "title": "Runs",
+          "type": "array"
+        },
         "subject": {
           "title": "Subject",
           "type": "string"
@@ -216,6 +224,40 @@
       "title": "ProbeEntry",
       "type": "object"
     },
+    "RunEntry": {
+      "additionalProperties": false,
+      "description": "One run wl.works holds for the session: its copy of a run `GET\n/sessions` listed, measured from the recording, and its own id for it\n(design spec `2026-10-01-session-listing-and-run-requests-design.md`\nsection 3.1). Checked against `core.Run` as the request arrives.",
+      "properties": {
+        "end_s": {
+          "title": "End S",
+          "type": "number"
+        },
+        "run_number": {
+          "maximum": 32767,
+          "minimum": 1,
+          "title": "Run Number",
+          "type": "integer"
+        },
+        "start_s": {
+          "title": "Start S",
+          "type": "number"
+        },
+        "works_run_id": {
+          "maxLength": 64,
+          "minLength": 1,
+          "title": "Works Run Id",
+          "type": "string"
+        }
+      },
+      "required": [
+        "run_number",
+        "start_s",
+        "end_s",
+        "works_run_id"
+      ],
+      "title": "RunEntry",
+      "type": "object"
+    },
     "SubjectDetails": {
       "additionalProperties": false,
       "description": "The animal, as wl.works' own record states it: what an NWB file's\n`subject` needs beyond an id (design spec\n`2026-09-28-nwb-builder-design.md` section 9, the requester's decision\nof 2026-09-28). Optional in the request; `responder/jobs.py::accept`\nwrites it into element-animal's own tables, replacing the stub\n`ingest/landing.py` lands.",
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 114 passed.

Then the contracts and the responder: `.venv/bin/python -m pytest tests/contracts tests/responder tests/cli/test_schemas_export.py -q -p no:cacheprovider`. Expected: 313 passed.

- [ ] **Step 5: Mutation checks** (`responder/jobs.py` unless named).
  - T3b: `missing = [number for number in window if number not in by_number]` becomes `missing = []` [`test_a_request_from_a_stale_listing_is_a_run_mismatch_and_writes_nothing`, its unasserted-run case].
  - T3d: `if known is not None and known != row["works_run_id"]:` becomes `if False:` [`test_a_run_asserted_again_under_another_id_is_a_conflict`].
  - T3e: `if sorted(probe_runs) != serials:` becomes `if False:` [`test_each_probes_runs_are_stated_in_full_and_within_the_file`, its two wrong-probes cases].
  - T3f (`responder/server.py`): `jobs.RunIdConflict` is dropped from the `409` translation [`test_a_run_asserted_again_under_another_id_is_a_conflict`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/contracts/protocol.py wl_preproc/responder/jobs.py wl_preproc/responder/server.py docs/schemas/job_request.json tests/contracts/test_protocol.py tests/responder/test_jobs.py
git commit -m "feat(responder): a request may name runs -- metadata.runs (wl.works' id and copy of each measured run), selection.probe_runs for a canonical and run_numbers for a derivative, each checked against core.Run as the request arrives; a stale listing is a 422 naming the run, a changed run id a 409

<trailer lines>"
```

---

### Task 4: The NWB file is built from its activation's runs — description version 3

**Files:**
- Create: `wl_preproc/events/runs.py`
- Modify: `wl_preproc/listing/entry.py`, `wl_preproc/contracts/nwb_description.py`, `wl_preproc/nwb/gather.py`, `wl_preproc/nwb/describe.py`, `wl_preproc/nwb/intervals.py`, `wl_preproc/nwb/build.py`, `wl_preproc/synth/spikeglx.py`, `docs/schemas/nwb_description.json` (re-exported)
- Test: `tests/synth/test_spikeglx.py`, `tests/nwb/test_helpers.py`, `tests/nwb/test_describe.py`, `tests/nwb/test_writers.py`, `tests/cli/test_schemas_export.py`, `tests/schema/test_detect_populate.py`, `tests/schema/test_nwb_build.py`, `tests/schema/test_nwb_probes.py`, `tests/schema/test_nwb_trials.py`, `tests/schema/test_request.py`

**Interfaces — consumes:** Task 1's `ActivationRun`, `ActivationProbeRun` and `RunCoverage`; Task 3's `accept()` with runs.

**Interfaces — produces:**
- `events.runs.starts_inside(start_s, run_start_s, run_stop_s) -> bool` (a float32-stored start), `run_of(start_s, runs: list[dict]) -> int | None` (each run with `run_number`, `start_s`, `end_s`), and `event_inside(time_s, run_start_s, run_stop_s) -> bool` (a `decimal(10,4)` time, both ends included). `listing/entry.py` uses `starts_inside` in place of its own.
- `contracts.nwb_description`: `SCHEMA_VERSION = 3`; `RunBlock(block_number, block_in_run, block_type, measured: Interval, closed: bool | None, trials: int)`; `Run(run_number, works_run_id, task: Task, measured, closed, trials: TrialCounts, coverage: dict[str, Coverage], conditions, blocks: list[RunBlock])`; `ProbeInfo.sorted_runs: list[int] = []`; `NwbDescription.runs: list[Run]` in place of `blocks`.
- `nwb.gather.Gathered.runs` and `.blocks` (the measured blocks inside the runs); readiness waits on `RunCoverage`; a refusal `"no runs in the {role} activation's run set"`.
- `nwb.intervals.add_runs(nwb, runs, systems)` writes `/intervals/runs`; `add_blocks(nwb, blocks)` writes the measured blocks, and nothing when there are none; `add_trials` gains `run_number`.

**Why the generator changes here.** The SpikeGLX generator ended the NI buffer exactly where the last strobe ended. The reader latches on the falling edge, so the last word was never read: `CI_RECIPE` kept its `SESSION_END` only by rounding, and with its blocks in runs `SESSION_END` moved to 15.002 s, was dropped, `event_code_agreement` fell to 49/50 and the probe tests' sessions read tier D. The fixture was wrong, not the reader (spec amendment 22).

**Why `test_request.py` changes here.** Its block-set test read `nwb.gather._block_set`, which this task removes; the test keeps its `ActivationBlock` assertion until Task 5.

- [ ] **Step 1: Write the failing tests.** Apply these diffs:

```diff
--- a/tests/synth/test_spikeglx.py
+++ b/tests/synth/test_spikeglx.py
@@ -248,6 +248,24 @@ def test_nidq_carries_the_code_words_not_only_the_barcode(tmp_path):
     assert [int(data[i]) for i in falling] == [word for _, word in truth.code_words]
 
 
+@pytest.mark.parametrize("runs", [False, True])
+def test_the_last_code_words_strobe_falls_before_the_file_ends(tmp_path, runs):
+    """The reader latches each word on its strobe's falling edge, so a strobe
+    still high at the file's last sample is a word the NI never latched. The
+    buffer once ended exactly where the last strobe did: CI_RECIPE's own
+    SESSION_END kept its edge only by rounding, and wrapping its blocks in
+    runs moved SESSION_END to 15.002 s, dropped it, and took a tier-A session
+    to D on an `event_code_agreement` of 49/50."""
+    from wl_preproc.events.extract import extract_nidq_words
+
+    recipe = CI_RECIPE.model_copy(update={"systems": ("syncbox", "spikeglx"), "runs": runs})
+    truth = build_timeline(recipe)
+    write_spikeglx(tmp_path, recipe, truth)
+
+    words = extract_nidq_words(tmp_path / f"{recipe.session_id}.nidq.bin").words
+    assert [code for _, code in words] == [word for _, word in truth.code_words]
+
+
 def test_geom_map_comes_from_the_probe_table_not_a_format_string(tmp_path):
     """The fabricated map alternated x between 16 and 48. Real NP1000
     electrodes 0-3 sit at (16,0), (48,0), (0,20), (32,20) -- four x values on a
```

```diff
--- a/tests/nwb/test_helpers.py
+++ b/tests/nwb/test_helpers.py
@@ -150,3 +150,24 @@ def test_findings_ranked_above_critical_also_block(tmp_path):
     with h5py.File(path, "w") as handle:
         handle["data"] = [1, 2, 3]
     assert n_critical(inspect_file(path)) >= 1
+
+
+def test_what_starts_just_after_its_run_is_in_it_however_it_was_stored():
+    """Design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    amendment 11: a block's or trial's start is stored as float32, an
+    event's time as decimal(10, 4), a run's bounds as doubles. Either
+    rounding can put a start, or the RUN_START event lying exactly on the
+    run's start, before it; the run's bounds are rounded the same way."""
+    import numpy as np
+
+    from wl_preproc.events.runs import event_inside, run_of, starts_inside
+
+    block = float(np.float32(18000.124))  # stored 0.6 ms after a RUN_START at 18000.1234
+    assert block < 18000.1234 and starts_inside(block, 18000.1234, 18100.0)
+    assert run_of(block, [{"run_number": 7, "start_s": 18000.1234, "end_s": 18100.0}]) == 7
+    assert run_of(18200.0, [{"run_number": 7, "start_s": 18000.1234, "end_s": 18100.0}]) is None
+    # A RUN_START at 12.34564 s is stored as 12.3456, before it, and a RUN_END
+    # at 20.00006 s as 20.0001, after it: rounding to 0.1 ms moves each out.
+    run_start, run_stop = 12.34564, 20.00006
+    assert event_inside(12.3456, run_start, run_stop) and event_inside(20.0001, run_start, run_stop)
+    assert not event_inside(12.3455, run_start, run_stop) and not event_inside(20.0002, run_start, run_stop)
```

```diff
--- a/tests/nwb/test_describe.py
+++ b/tests/nwb/test_describe.py
@@ -24,9 +24,11 @@ SESSION = {
 }
 CONDITION = {"name": "contrast-50", "code": None, "settings": {"contrast": 0.5}, "varying": {},
              "trials": {"total": 1, "by_outcome": {"correct": 1}}}
-BLOCK = {"block_id": 1, "start_s": 0.0, "end_s": 30.0, "task_type": "2", "works_block_id": "wb-1",
-         "measured_start_s": 0.5, "measured_stop_s": 29.5, "coverage": {"ohdpi": ("full", 30.0)},
-         "trials": {"total": 1, "by_outcome": {"correct": 1}}, "conditions": [CONDITION]}
+RUN = {"run_number": 1, "start_s": 0.5, "end_s": 29.5, "task_type": 2, "task": None, "works_run_id": "wr-1",
+       "closed": True, "coverage": {"ohdpi": ("full", 29.0)},
+       "trials": {"total": 1, "by_outcome": {"correct": 1}}, "conditions": [CONDITION]}
+BLOCK = {"block_number": 1, "run_number": 1, "block_in_run": 1, "block_type": "Bt1", "start_s": 0.6,
+         "stop_s": 29.4, "closed": None, "n_trials": 1}
 CHECKSUM = {"dataset_path": "/intervals/trials/start_time", "dtype": "float64", "shape": "(1,)",
             "sha256": "0" * 64, "paired_with": ""}
 
@@ -34,7 +36,7 @@ CHECKSUM = {"dataset_path": "/intervals/trials/start_time", "dtype": "float64",
 def _gathered(eye=None, notes=(), probes=(), probe_notes=(), trial_notes=()):
     from wl_preproc.nwb.gather import Gathered
 
-    return Gathered(session=SESSION, systems=["ohdpi"], blocks=[BLOCK], trials=[{"trial_id": 1}],
+    return Gathered(session=SESSION, systems=["ohdpi"], runs=[RUN], blocks=[BLOCK], trials=[{"trial_id": 1}],
                     events=[{}, {}], timebase={}, eye=eye, conditions=[CONDITION], condition_notes=list(notes),
                     probes=list(probes), probe_notes=list(probe_notes), trial_notes=list(trial_notes))
 
@@ -44,7 +46,7 @@ def test_the_description_of_a_file_without_eye_data():
 
     out = describe(_gathered(notes=["no rig trial record (xcon/trials.jsonl)"]), status="written", n_critical=0,
                    checksums=[CHECKSUM], built_at=BUILT_AT)
-    assert out["schema_version"] == 2
+    assert out["schema_version"] == 3
     assert out["identity"]["identifier"] == SESSION["identifier"]
     assert (out["identity"]["rig"], out["identity"]["role"], out["identity"]["status"]) == ("rig-a", "canonical", "written")
     assert out["identity"]["pipeline"]["name"] == "wl-preproc"
@@ -53,11 +55,16 @@ def test_the_description_of_a_file_without_eye_data():
     assert out["data_types"]["eye"] is None and out["data_types"]["eye_events"] is None
     assert out["data_types"]["behaviour"] == {"trials": 1, "events": 2}
     assert out["data_types"]["ephys"] is None and out["probes"] == [] and out["processing"] == {}
-    (block,) = out["blocks"]
-    assert block["task"] == {"code": "2", "name": "rf_map"}
-    assert block["asserted"] == {"start_s": 0.0, "stop_s": 30.0}
-    assert block["coverage"] == {"ohdpi": {"coverage": "full", "covered_s": 30.0}}
-    assert block["conditions"] == [CONDITION]
+    (run,) = out["runs"]
+    assert (run["run_number"], run["works_run_id"], run["closed"]) == (1, "wr-1", True)
+    assert run["task"] == {"code": "2", "name": "rf_map"}
+    assert run["measured"] == {"start_s": 0.5, "stop_s": 29.5}
+    assert run["coverage"] == {"ohdpi": {"coverage": "full", "covered_s": 29.0}}
+    assert run["conditions"] == [CONDITION]
+    # The measured blocks inside the run (design spec
+    # `2026-10-01-session-listing-and-run-requests-design.md` section 4).
+    assert run["blocks"] == [{"block_number": 1, "block_in_run": 1, "block_type": "Bt1",
+                              "measured": {"start_s": 0.6, "stop_s": 29.4}, "closed": None, "trials": 1}]
     assert out["quality"] == {"timing_tier": "B", "reference_source": "manifest",
                               "eye_usable_fraction": {"left": None, "right": None}}
     assert out["notes"] == ["no rig trial record (xcon/trials.jsonl)"]
@@ -107,9 +114,9 @@ def test_each_probe_is_described_with_both_areas_and_where_its_label_came_from()
         {"serial": "19011110001", "probe_type": "NP1032", "insertion_number": 1, "trajectory_id": "T-1",
          "n_electrodes": 1, "target": {"area": "V4d", "atlas": "CHARM", "atlas_level": 6},
          "assignment": {"area": "V4v", "source": "histology", "asserted_at": "2026-09-29T12:00:00Z"},
-         "area_from": "assignment"},
+         "area_from": "assignment", "sorted_runs": []},
         {"serial": "R-7", "probe_type": None, "insertion_number": None, "trajectory_id": None, "n_electrodes": 0,
-         "target": None, "assignment": None, "area_from": "unknown"},
+         "target": None, "assignment": None, "area_from": "unknown", "sorted_runs": []},
     ]
     assert out["notes"] == ["a condition note", "a probe note"]
 
@@ -123,3 +130,17 @@ def test_the_trial_notes_follow_the_condition_notes():
     out = describe(_gathered(notes=["a condition note"], trial_notes=["a trial note"], probe_notes=["a probe note"]),
                    status="written", n_critical=0, checksums=[], built_at=BUILT_AT)
     assert out["notes"] == ["a condition note", "a trial note", "a probe note"]
+
+
+def test_a_run_takes_the_rigs_name_for_its_task_and_a_probe_its_sorted_runs():
+    """The rig's record names a run's task (`core.RunRecord`); each probe
+    carries the runs its sort covers, as the request stated them."""
+    from wl_preproc.nwb.describe import describe
+
+    probe = {"serial": "19011110001", "probe_type": "NP1000", "insertion_number": 1, "trajectory_id": None,
+             "electrodes": [0, 1], "target": None, "assignment": None, "area_from": "unknown", "sorted_runs": [1]}
+    data = _gathered(probes=[probe])
+    data.runs[0] = {**RUN, "task": "fixation_detection"}
+    out = describe(data, status="written", n_critical=0, checksums=[], built_at=BUILT_AT)
+    assert out["runs"][0]["task"] == {"code": "2", "name": "fixation_detection"}
+    assert out["probes"][0]["sorted_runs"] == [1]
```

```diff
--- a/tests/nwb/test_writers.py
+++ b/tests/nwb/test_writers.py
@@ -19,12 +19,15 @@ SESSION = {
     "subject": {"subject_id": "monk01", "species": "Macaca mulatta", "sex": "F",
                 "date_of_birth": datetime.date(2016, 3, 2)},
 }
-BLOCKS = [{"block_id": 1, "start_s": 0.0, "end_s": 30.0, "task_type": "rf_map", "works_block_id": "wb-1",
-           "measured_start_s": 0.5, "measured_stop_s": 29.5,
-           "coverage": {"ohdpi": ("full", 30.0), "spikeglx": ("partial", 12.0)}},
-          {"block_id": 2, "start_s": 30.0, "end_s": 60.0, "task_type": "search", "works_block_id": None,
-           "measured_start_s": None, "measured_stop_s": None, "coverage": {"ohdpi": ("full", 30.0)}}]
-TRIALS = [{"trial_id": 1, "start_s": 1.0, "stop_s": 3.0, "outcome": "correct", "block_id": 1,
+RUNS = [{"run_number": 1, "start_s": 0.0, "end_s": 30.0, "task_type": 2, "task": "rf_map", "works_run_id": "wr-1",
+         "closed": True, "coverage": {"ohdpi": ("full", 30.0), "spikeglx": ("partial", 12.0)}},
+        {"run_number": 2, "start_s": 30.0, "end_s": 60.0, "task_type": 0, "task": None, "works_run_id": None,
+         "closed": False, "coverage": {"ohdpi": ("full", 30.0)}}]
+BLOCKS = [{"block_number": 1, "run_number": 1, "block_in_run": 1, "block_type": "Bt1", "start_s": 0.5,
+           "stop_s": 29.5, "closed": True, "n_trials": 1},
+          {"block_number": 2, "run_number": 2, "block_in_run": 1, "block_type": None, "start_s": 30.5,
+           "stop_s": 59.5, "closed": None, "n_trials": 0}]
+TRIALS = [{"trial_id": 1, "start_s": 1.0, "stop_s": 3.0, "outcome": "correct", "run_number": 1, "block_id": 1,
            "coverage": {"ohdpi": ("full", 2.0)}}]
 EVENTS = [{"time_s": 1.0, "event_type": "TRIAL_START", "trial_id": 1, "block_id": None, "condition": None},
           {"time_s": 2.5, "event_type": "CODE_256", "trial_id": None, "block_id": None, "condition": None}]
@@ -54,24 +57,31 @@ def test_the_file_is_one_activation_on_session_time(tmp_path):
         assert nwb.subject.date_of_birth.date() == datetime.date(2016, 3, 2)
 
 
-def test_blocks_trials_and_events(tmp_path):
-    from wl_preproc.nwb.intervals import add_blocks, add_task_events, add_trials
+def test_runs_blocks_trials_and_events(tmp_path):
+    from wl_preproc.nwb.intervals import add_blocks, add_runs, add_task_events, add_trials
 
     def build(nwb):
-        add_blocks(nwb, BLOCKS, ["ohdpi", "spikeglx"])
+        add_runs(nwb, RUNS, ["ohdpi", "spikeglx"])
+        add_blocks(nwb, BLOCKS)
         add_trials(nwb, TRIALS, ["ohdpi"])
         add_task_events(nwb, EVENTS)
 
     _path, io, nwb = _write(tmp_path, build)
     with io:
+        runs = nwb.intervals["runs"].to_dataframe()
+        assert runs["run_number"].tolist() == [1, 2]
+        assert runs["works_run_id"].tolist() == ["wr-1", ""]
+        assert (runs["task"].tolist(), runs["task_code"].tolist(), runs["closed"].tolist()) == (
+            ["rf_map", ""], [2, 0], [True, False])
+        assert runs["coverage_spikeglx"].tolist() == ["partial", ""]
+        assert runs["covered_s_spikeglx"].iloc[0] == 12.0 and np.isnan(runs["covered_s_spikeglx"].iloc[1])
         blocks = nwb.intervals["blocks"].to_dataframe()
-        assert blocks["block_id"].tolist() == [1, 2]
-        assert blocks["works_block_id"].tolist() == ["wb-1", ""]
-        assert np.isnan(blocks["measured_start_time"].iloc[1])
-        assert blocks["coverage_spikeglx"].tolist() == ["partial", ""]
-        assert blocks["covered_s_spikeglx"].iloc[0] == 12.0 and np.isnan(blocks["covered_s_spikeglx"].iloc[1])
+        assert blocks[["block_number", "run_number", "block_in_run", "block_type", "closed", "n_trials"]].values.tolist() == [
+            [1, 1, 1, "Bt1", 1, 1], [2, 2, 1, "", -1, 0]]
+        assert blocks["start_time"].tolist() == [0.5, 30.5]
         trials = nwb.trials.to_dataframe()
-        assert trials[["trial_id", "outcome", "block_id", "coverage_ohdpi"]].iloc[0].tolist() == [1, "correct", 1, "full"]
+        assert trials[["trial_id", "outcome", "block_id", "run_number", "coverage_ohdpi"]].iloc[0].tolist() == [
+            1, "correct", 1, 1, "full"]
         events = nwb.intervals["task_events"].to_dataframe()
         assert events["start_time"].tolist() == events["stop_time"].tolist() == [1.0, 2.5]
         assert events["event_type"].tolist() == ["TRIAL_START", "CODE_256"]
@@ -283,3 +293,17 @@ def test_a_serial_an_hdf5_name_cannot_hold_is_kept_whole_under_a_safe_name(tmp_p
         assert list(nwb.devices) == ["probe-A1x32_5_mm"]
         assert nwb.devices["probe-A1x32_5_mm"].serial_number == "A1x32/5:mm"
         assert list(nwb.electrode_groups) == ["probe-A1x32_5_mm"]
+
+
+def test_runs_that_hold_no_block_add_no_block_table(tmp_path):
+    """A run that stopped before its first trial has no block; an empty
+    table is not written."""
+    from wl_preproc.nwb.intervals import add_blocks, add_runs
+
+    def build(nwb):
+        add_runs(nwb, RUNS[:1], ["ohdpi"])
+        add_blocks(nwb, [])
+
+    _path, io, nwb = _write(tmp_path, build)
+    with io:
+        assert "blocks" not in nwb.intervals and "runs" in nwb.intervals
```

```diff
--- a/tests/cli/test_schemas_export.py
+++ b/tests/cli/test_schemas_export.py
@@ -98,8 +98,8 @@ def test_the_nwb_description_schema_is_exported_for_wl_works(tmp_path):
     `2026-09-29-nwb-publishing-design.md` section 2)."""
     export_schemas(tmp_path)
     schema = json.loads((tmp_path / "nwb_description.json").read_text())
-    assert schema["properties"]["schema_version"]["const"] == 2
-    assert {"Block", "Condition", "Checksums"} <= set(schema["$defs"])
+    assert schema["properties"]["schema_version"]["const"] == 3
+    assert {"Run", "RunBlock", "Condition", "Checksums"} <= set(schema["$defs"]) and "Block" not in schema["$defs"]
     # Version 2: each probe has a defined shape (design spec
     # `2026-09-30-nwb-probes-design.md` section 3.2).
     probe = schema["$defs"]["ProbeInfo"]
```

```diff
--- a/tests/schema/test_detect_populate.py
+++ b/tests/schema/test_detect_populate.py
@@ -549,7 +549,7 @@ def daemon_module(dj_conn, prefix):
 
 def _build_stepped_session(
     tmp_path_factory, *, dirname, session_id, subject, session_datetime, seed,
-    after_generate=None, fixational_drift_px_per_sqrt_frame=0.0,
+    after_generate=None, fixational_drift_px_per_sqrt_frame=0.0, recipe_update=None,
 ):
     """The construction behind `stepped_session`, and -- without
     `after_generate` -- behind the mixed-eye fixtures below (`left_refused_
@@ -642,6 +642,10 @@ def _build_stepped_session(
         eye_fixations=tuple(detect_fixations),
         fixational_drift_px_per_sqrt_frame=fixational_drift_px_per_sqrt_frame,
     )
+    if recipe_update:
+        # The NWB builder's tests wrap these trials in runs (design spec
+        # `2026-10-01-session-listing-and-run-requests-design.md` section 4).
+        recipe = SessionRecipe.model_validate({**recipe.model_dump(), **recipe_update})
 
     root = tmp_path_factory.mktemp(dirname)
     truth = generate_session(root, recipe)
```

```diff
--- a/tests/schema/test_nwb_build.py
+++ b/tests/schema/test_nwb_build.py
@@ -14,7 +14,6 @@ import pytest
 
 _SESSION_DATETIME = datetime.datetime(2025, 7, 20, 9, 0)
 _SUBJECT = "nwbstep1"
-_SPLIT_S = 7.5
 
 
 @pytest.fixture(scope="module")
@@ -27,32 +26,34 @@ def daemon_module(dj_conn, prefix):
     return daemon
 
 
-def _request(key, idempotency_key, montage, blocks, block_ids=None):
+def _request(key, idempotency_key, montage, runs, run_numbers=None):
     from wl_preproc.contracts.protocol import JobRequest, MetadataBundle
 
     selection = {"session_datetime": key["session_datetime"].replace(tzinfo=datetime.UTC),
                  "montage_id": montage["montage_id"]}
-    if block_ids is not None:
-        selection["block_ids"] = block_ids
+    if run_numbers is not None:
+        selection["run_numbers"] = run_numbers
     return JobRequest(
         domain="neural", selection=selection, parameters={}, idempotency_key=idempotency_key,
         metadata=MetadataBundle(
-            blocks=blocks, montage_boundaries=[montage], probes=[], experimenter="jw", subject=key["subject"],
-            task_types=[], subject_details={"species": "Macaca mulatta", "sex": "F",
-                                            "date_of_birth": datetime.date(2016, 3, 2)},
+            blocks=[], runs=runs, montage_boundaries=[montage], probes=[], experimenter="jw",
+            subject=key["subject"], task_types=[],
+            subject_details={"species": "Macaca mulatta", "sex": "F", "date_of_birth": datetime.date(2016, 3, 2)},
         ),
     )
 
 
-def _derivative(session_key, blocks, prefix, block):
-    """The one-block derivative over `block`. `accept` returns the
-    activation it already holds for a block set, so every caller gets the
-    same one."""
+def _montage(runs):
+    return {"montage_id": 0, "start_s": 0.0, "end_s": max(r["end_s"] for r in runs) + 1.0}
+
+
+def _derivative(session_key, runs, prefix, run):
+    """The one-run derivative over `run`. `accept` returns the activation it
+    already holds for a run set, so every caller gets the same one."""
     from wl_preproc.responder.jobs import accept
 
-    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
-    return accept(_request(session_key, f"nwbstep1-block-{block['block_id']}", montage, blocks,
-                           block_ids=[block["block_id"]]), prefix=prefix)
+    return accept(_request(session_key, f"nwbstep1-run-{run['run_number']}", _montage(runs), runs,
+                           run_numbers=[run["run_number"]]), prefix=prefix)
 
 
 def _command(key, nwb_root, prefix):
@@ -64,49 +65,44 @@ def _command(key, nwb_root, prefix):
 @pytest.fixture(scope="module")
 def activation(daemon_module, prefix, tmp_path_factory):
     """`stepped_session`'s construction (tests/schema/test_detect_populate.py),
-    run through the daemon; then wl.works' job request for a canonical
-    activation over its measured blocks, as `responder/jobs.py::accept`
-    records it. The session's timing is computed before that request
-    arrives, as a daemon pass before wl.works asks would leave it. Date,
-    subject and seed checked unclaimed across `tests/` on 2026-09-28. In the
-    PAST, unlike most fixtures here: `nwbinspector` calls
-    a future `session_start_time` critical. Returns `(session_key,
-    activation_key, blocks)`."""
-    from tests.schema.test_detect_populate import _build_stepped_session
+    its five trials split into two blocks of three and two, each in its own
+    run, run through the daemon; then wl.works' job request for a canonical
+    activation over the montage's measured runs, as `responder/jobs.py::accept`
+    records it (design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    section 3). The session's timing is computed before that request arrives,
+    as a daemon pass before wl.works asks would leave it. Date, subject and
+    seed checked unclaimed across `tests/` on 2026-09-28. In the PAST, unlike
+    most fixtures here: `nwbinspector` calls a future `session_start_time`
+    critical. Returns `(session_key, activation_key, runs)`, the runs as
+    wl.works sends them."""
+    from tests.schema.test_detect_populate import TRIAL_DURATION_S, _build_stepped_session
+    from wl_preproc.contracts.events import TaskTypeCode
     from wl_preproc.responder.jobs import accept
-    from wl_preproc.schema import pipeline
+    from wl_preproc.schema import core
 
+    split = [{"task_type": TaskTypeCode.RF_MAP, "n_trials": n, "trial_duration_s": TRIAL_DURATION_S} for n in (3, 2)]
     session_key, _segment, _onsets = _build_stepped_session(
         tmp_path_factory, dirname="nwbstep", session_id="2025-07-20_01", subject=_SUBJECT,
-        session_datetime=_SESSION_DATETIME, seed=720,
+        session_datetime=_SESSION_DATETIME, seed=720, recipe_update={"runs": True, "blocks": split},
     )
     daemon_module.run_once(prefix=prefix)
-    # The session has one measured block; wl.works asserts it as two, split at
-    # `_SPLIT_S`, which it is entitled to do. So a derivative over the second
-    # holds only half the session, and every trimming assertion can fail.
-    (measured,) = (pipeline.trial.Block & session_key).to_dicts()
-    task_type = (pipeline.trial.Block.Attribute & measured & {"attribute_name": "task_type"}).fetch1("attribute_value")
-    blocks = [
-        {"block_id": 1, "task_type": task_type, "start_s": float(measured["block_start_time"]), "end_s": _SPLIT_S,
-         "works_block_id": "wb-1"},
-        {"block_id": 2, "task_type": task_type, "start_s": _SPLIT_S, "end_s": float(measured["block_stop_time"]),
-         "works_block_id": "wb-2"},
-    ]
-    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
-    key = accept(_request(session_key, "nwbstep1-canonical", montage, blocks), prefix=prefix)
-    # The next pass computes what wl.works' blocks add: their coverage.
-    # *Added with the final review's I4: until then every file here was built
-    # before it, without block coverage, and nothing noticed; true when
-    # written.*
+    # Two measured runs, and every planted step is in the second, so a
+    # derivative over it holds only part of the session and every trimming
+    # assertion can fail.
+    runs = [{"run_number": row["run_number"], "start_s": row["run_start_time"], "end_s": row["run_stop_time"],
+             "works_run_id": f"wr-{row['run_number']}"}
+            for row in (core.Run & session_key).to_dicts(order_by="run_number")]
+    key = accept(_request(session_key, "nwbstep1-canonical", _montage(runs), runs), prefix=prefix)
+    # The next pass computes what the runs add: their coverage.
     daemon_module.run_once(prefix=prefix)
-    return session_key, key, blocks
+    return session_key, key, runs
 
 
 @pytest.fixture(scope="module")
 def built(activation, tmp_path_factory):
     from wl_preproc.nwb.build import build
 
-    _session_key, key, _blocks = activation
+    _session_key, key, _runs = activation
     return build(key, tmp_path_factory.mktemp("nwb"))
 
 
@@ -135,29 +131,36 @@ def test_a_synthetic_sessions_clock_falls_back_to_the_manifest(activation, built
     assert abs(built.clock["started_at_difference_s"]) > 60.0
 
 
-def test_blocks_trials_and_events_carry_the_tables_times(activation, built):
+def test_runs_blocks_trials_and_events_carry_the_tables_times(activation, built):
+    """Design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    section 4: the file's runs are measured, with wl.works' ids; its blocks
+    are the measured ones inside them; each trial carries its run."""
     from pynwb import NWBHDF5IO
 
     from wl_preproc.schema import pipeline
 
-    session_key, _key, blocks = activation
+    session_key, _key, runs = activation
     with NWBHDF5IO(str(built.path), "r") as handle:
         nwb = handle.read()
-        stored = nwb.intervals["blocks"].to_dataframe()
-        assert stored["block_id"].tolist() == [1, 2]
-        assert stored["works_block_id"].tolist() == ["wb-1", "wb-2"]
-        assert stored["stop_time"].tolist() == [_SPLIT_S, blocks[1]["end_s"]]
+        stored = nwb.intervals["runs"].to_dataframe()
+        assert stored["run_number"].tolist() == [1, 2]
+        assert stored["works_run_id"].tolist() == ["wr-1", "wr-2"]
+        assert stored["stop_time"].tolist() == [run["end_s"] for run in runs]
+        blocks = nwb.intervals["blocks"].to_dataframe()
+        assert (blocks["block_number"].tolist(), blocks["run_number"].tolist(), blocks["n_trials"].tolist()) == (
+            [1, 2], [1, 2], [3, 2])
         trials = nwb.trials.to_dataframe()
         expected = sorted(float(r["trial_start_time"]) for r in (pipeline.trial.Trial & session_key).to_dicts())
         np.testing.assert_allclose(sorted(trials["start_time"]), expected)
-        # Every event inside a block, the block's end included (an event is
-        # an instant, and BLOCK_END sits exactly on it); SESSION_START and
-        # SESSION_END lie outside every block and are not this file's.
+        assert trials.sort_values("start_time")["run_number"].tolist() == [1, 1, 1, 2, 2]
+        # Every event inside a run, its ends included (an event is an
+        # instant, and RUN_START and RUN_END sit exactly on them);
+        # SESSION_START and SESSION_END lie outside every run.
         events = nwb.intervals["task_events"].to_dataframe()
         times = [float(r["event_start_time"]) for r in (pipeline.event.Event & session_key).to_dicts()]
-        inside = sorted(t for t in times if any(b["start_s"] <= t <= b["end_s"] for b in blocks))
+        inside = sorted(t for t in times if any(run["start_s"] <= t <= run["end_s"] for run in runs))
         np.testing.assert_allclose(sorted(events["start_time"]), inside)
-        assert "BLOCK_END" in set(events["event_type"])
+        assert {"RUN_START", "BLOCK_END", "RUN_END"} <= set(events["event_type"])
         assert not {"SESSION_START", "SESSION_END"} & set(events["event_type"])
 
 
@@ -194,9 +197,11 @@ def test_the_description_describes_the_file(activation, built):
     assert description["subject"]["age_days"] == (_SESSION_DATETIME.date() - datetime.date(2016, 3, 2)).days
     assert description["data_types"]["eye"] == {"gaze": ["left", "right"], "pupil": ["left", "right"]}
     assert len(description["data_types"]["eye_events"]["detectors"]) == 6
-    assert [block["block_id"] for block in description["blocks"]] == [1, 2]
-    assert all(block["task"]["name"] == "rf_map" for block in description["blocks"])
-    names = {condition["name"] for block in description["blocks"] for condition in block["conditions"]}
+    assert description["schema_version"] == 3
+    assert [(run["run_number"], run["works_run_id"]) for run in description["runs"]] == [(1, "wr-1"), (2, "wr-2")]
+    assert all(run["task"]["name"] == "rf_map" for run in description["runs"])
+    assert [[block["block_number"] for block in run["blocks"]] for run in description["runs"]] == [[1], [2]]
+    names = {condition["name"] for run in description["runs"] for condition in run["conditions"]}
     assert names and names <= {"contrast-10", "contrast-25", "contrast-50", "contrast-100"}
     assert description["checksums"]["datasets"] == built.checksums
     assert description["notes"] == []
@@ -225,7 +230,7 @@ def test_the_eye_is_on_session_time_and_every_detector_is_there(activation, buil
 def test_every_dataset_is_checksummed_and_a_rebuild_matches(activation, built, tmp_path_factory):
     from wl_preproc.nwb.build import build
 
-    _session_key, key, _blocks = activation
+    _session_key, key, _runs = activation
     with h5py.File(built.path) as handle:
         names = []
         handle.visititems(lambda name, obj: names.append("/" + name) if isinstance(obj, h5py.Dataset) else None)
@@ -271,23 +276,23 @@ def test_one_second_of_gaze_reads_a_small_fraction_of_the_file(built):
     assert source.served - opened < size / 10, (source.served - opened, size)
 
 
-def test_a_derivative_holds_only_its_own_block(activation, prefix, tmp_path_factory):
-    """Section 5: continuous samples and trial starts inside the block,
-    events inside it or on its end, and nothing from the other block."""
+def test_a_derivative_holds_only_its_own_run(activation, prefix, tmp_path_factory):
+    """Section 5: continuous samples and trial starts inside the run, events
+    inside it or on its ends, and nothing from the other run."""
     from pynwb import NWBHDF5IO
 
     from wl_preproc.nwb.build import build
     from wl_preproc.responder.jobs import accept
 
-    session_key, _key, blocks = activation
-    second = max(blocks, key=lambda b: b["start_s"])
-    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
-    key = accept(_request(session_key, "nwbstep1-derivative", montage, blocks, block_ids=[second["block_id"]]),
+    session_key, _key, runs = activation
+    second = max(runs, key=lambda r: r["start_s"])
+    montage = _montage(runs)
+    key = accept(_request(session_key, "nwbstep1-derivative", montage, runs, run_numbers=[second["run_number"]]),
                  prefix=prefix)
     result = build(key, tmp_path_factory.mktemp("nwb-derivative"))
     with NWBHDF5IO(str(result.path), "r") as handle:
         nwb = handle.read()
-        assert nwb.intervals["blocks"].to_dataframe()["block_id"].tolist() == [second["block_id"]]
+        assert nwb.intervals["runs"].to_dataframe()["run_number"].tolist() == [second["run_number"]]
         times = nwb.processing["behavior"]["EyeTracking"]["gaze_left"].timestamps[:]
         assert times.min() >= second["start_s"] and times.max() < second["end_s"]
         assert times.min() - second["start_s"] < 0.01
@@ -299,20 +304,20 @@ def test_a_derivative_holds_only_its_own_block(activation, prefix, tmp_path_fact
         assert ((runs >= second["start_s"]) & (runs < second["end_s"])).all()
 
 
-def test_a_run_that_crosses_its_blocks_end_keeps_its_true_end(activation, built, prefix, tmp_path_factory):
-    """Section 5: a detected run is kept when it starts in a block and is
-    never cut, so one that ends after its block keeps its true end. Every
-    planted step is in the second block, so for this one build wl.works'
-    boundary is moved into the middle of the first planted saccade, and
-    restored after."""
+def test_an_eye_event_that_crosses_its_runs_end_keeps_its_true_end(activation, built, prefix, tmp_path_factory):
+    """Section 5: a detected event is kept when it starts in a run and is
+    never cut, so one that ends after its run keeps its true end. Every
+    planted step is in the second run, so for this one build the measured
+    boundary between the runs is moved into the middle of the first planted
+    saccade, and restored after."""
     from pynwb import NWBHDF5IO
 
     from wl_preproc.nwb.build import build
     from wl_preproc.responder.jobs import accept
     from wl_preproc.schema import core
 
-    session_key, _key, blocks = activation
-    first, second = sorted(blocks, key=lambda b: b["start_s"])
+    session_key, _key, runs = activation
+    first, second = sorted(runs, key=lambda r: r["start_s"])
     with NWBHDF5IO(str(built.path), "r") as handle:
         events = handle.read().processing["eye_events"]
         saccade = events["engbert_kliegl_left"].to_dataframe().sort_values("start_time").iloc[0]
@@ -326,16 +331,16 @@ def test_a_run_that_crosses_its_blocks_end_keeps_its_true_end(activation, built,
             if len(frame):
                 crossing[name] = list(zip(frame["start_time"], frame["stop_time"], strict=True))
     assert "engbert_kliegl_left" in crossing
-    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
-    key = accept(_request(session_key, "nwbstep1-first-block", montage, blocks, block_ids=[first["block_id"]]),
+    montage = _montage(runs)
+    key = accept(_request(session_key, "nwbstep1-first-run", montage, runs, run_numbers=[first["run_number"]]),
                  prefix=prefix)
-    core.Block.update1({**session_key, "block_id": first["block_id"], "end_s": split})
-    core.Block.update1({**session_key, "block_id": second["block_id"], "start_s": split})
+    core.Run.update1({**session_key, "run_number": first["run_number"], "run_stop_time": split})
+    core.Run.update1({**session_key, "run_number": second["run_number"], "run_start_time": split})
     try:
-        result = build(key, tmp_path_factory.mktemp("nwb-first-block"))
+        result = build(key, tmp_path_factory.mktemp("nwb-first-run"))
     finally:
-        core.Block.update1({**session_key, "block_id": first["block_id"], "end_s": first["end_s"]})
-        core.Block.update1({**session_key, "block_id": second["block_id"], "start_s": second["start_s"]})
+        core.Run.update1({**session_key, "run_number": first["run_number"], "run_stop_time": first["end_s"]})
+        core.Run.update1({**session_key, "run_number": second["run_number"], "run_start_time": second["start_s"]})
     with NWBHDF5IO(str(result.path), "r") as handle:
         events = handle.read().processing["eye_events"]
         for name, runs in crossing.items():
@@ -344,17 +349,22 @@ def test_a_run_that_crosses_its_blocks_end_keeps_its_true_end(activation, built,
                 assert kept.loc[kept["start_time"] == start, "stop_time"].tolist() == [stop], name
 
 
-def test_an_activation_with_no_blocks_is_refused(activation, prefix, tmp_path_factory):
+def test_an_activation_with_no_runs_is_refused(activation, tmp_path_factory):
+    """`accept()` refuses a montage whose window holds no measured run, so
+    such an activation is written here directly. It is left in place: the
+    daemon stage's test below finds it refused."""
     from wl_preproc.nwb.build import build
-    from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema import core
 
-    session_key, _key, blocks = activation
-    end = max(b["end_s"] for b in blocks) + 1.0
-    empty = {"montage_id": 1, "start_s": end, "end_s": end + 10.0}
-    key = accept(_request(session_key, "nwbstep1-empty", empty, []), prefix=prefix)
+    session_key, _key, runs = activation
+    end = max(r["end_s"] for r in runs) + 1.0
+    core.Montage.insert1({**session_key, "montage_id": 1, "start_s": end, "end_s": end + 10.0}, skip_duplicates=True)
+    key = {**session_key, "montage_id": 1, "activation_id": 0}
+    _lifecycle_rows(session_key, "nwbstep1-empty", [{"montage_id": 1, "activation_id": 0, "role": "canonical"}],
+                    runs=())
     result = build(key, tmp_path_factory.mktemp("nwb-empty"))
     assert (result.status, result.path) == ("refused", None)
-    assert "no blocks" in result.reason
+    assert "no runs" in result.reason
 
 
 def test_an_eye_without_calibration_is_left_out_and_the_rest_is_built(activation, monkeypatch, tmp_path_factory):
@@ -368,7 +378,7 @@ def test_an_eye_without_calibration_is_left_out_and_the_rest_is_built(activation
 
     real = eye_schema._map_from_row
     monkeypatch.setattr(eye_schema, "_map_from_row", lambda row: None if row["eye"] == "right" else real(row))
-    _session_key, key, _blocks = activation
+    _session_key, key, _runs = activation
     result = build(key, tmp_path_factory.mktemp("nwb-one-eye"))
     assert result.status == "written", result.findings
     with NWBHDF5IO(str(result.path), "r") as handle:
@@ -383,7 +393,7 @@ def test_an_eye_without_calibration_is_left_out_and_the_rest_is_built(activation
 
 def test_a_session_without_an_eye_recording_is_built_without_one(activation, monkeypatch, tmp_path_factory):
     """No ohDPI recording at all (`gather._eye` returns None): the file is
-    still the activation's blocks, trials, events and timing, with no eye
+    still the activation's runs, trials, events and timing, with no eye
     modules."""
     from pynwb import NWBHDF5IO
 
@@ -391,7 +401,7 @@ def test_a_session_without_an_eye_recording_is_built_without_one(activation, mon
     from wl_preproc.nwb.build import build
 
     monkeypatch.setattr(gather, "_eye", lambda *args: None)
-    _session_key, key, _blocks = activation
+    _session_key, key, _runs = activation
     result = build(key, tmp_path_factory.mktemp("nwb-no-eye"))
     assert result.status == "written", result.findings
     with NWBHDF5IO(str(result.path), "r") as handle:
@@ -407,7 +417,7 @@ def test_the_stage_skips_a_freed_session(activation, tmp_path_factory):
     from wl_preproc.nwb.build import run_stage
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, _key, _blocks = activation
+    session_key, _key, _runs = activation
     before = len(nwb_schema.NwbFile & session_key)
     recorded, errors = run_stage(tmp_path_factory.mktemp("nwb-freed"), freed=[session_key])
     # Nothing is recorded for this session (other sessions in the suite's
@@ -427,9 +437,9 @@ def test_the_command_builds_once_and_rebuilds_only_when_its_row_is_deleted(activ
     from wl_preproc.responder.jobs import accept
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, _key, blocks = activation
-    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
-    key = accept(_request(session_key, "nwbstep1-command", montage, blocks, block_ids=[blocks[0]["block_id"]]),
+    session_key, _key, runs = activation
+    montage = _montage(runs)
+    key = accept(_request(session_key, "nwbstep1-command", montage, runs, run_numbers=[runs[0]["run_number"]]),
                  prefix=prefix)
     argv = ["nwb", "build", "--subject", key["subject"], "--session-datetime", key["session_datetime"].isoformat(),
             "--montage-id", str(key["montage_id"]), "--activation-id", str(key["activation_id"]),
@@ -458,7 +468,7 @@ def test_the_daemon_stage_records_every_activation(activation, daemon_module, pr
     assert daemon_module.run_once(prefix=prefix)["nwb"] is None
     report = daemon_module.run_once(prefix=prefix, nwb_root=tmp_path_factory.mktemp("nwb-daemon"))
     assert not [e for e in report["errors"] if "NwbFile" in e], report["errors"]
-    session_key, key, _blocks = activation
+    session_key, key, _runs = activation
     rows = {(r["montage_id"], r["activation_id"]): r for r in (nwb_schema.NwbFile & session_key).to_dicts()}
     assert set(rows) == {(r["montage_id"], r["activation_id"]) for r in (request.Activation & session_key).to_dicts()}
     canonical = rows[(key["montage_id"], key["activation_id"])]
@@ -528,7 +538,7 @@ def test_the_daemon_publishes_every_written_file_to_the_slow_share(activation, d
     from wl_preproc.nwb.publish import current_placement, description_path, mismatches
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, key, _blocks = activation
+    session_key, key, _runs = activation
     slow = slow_share
     report = daemon_module.run_once(prefix=prefix, nwb_root=tmp_path_factory.mktemp("nwb-publish-build"),
                                     nwb_slow=slow)
@@ -552,8 +562,8 @@ def test_publishing_skips_a_freed_session(activation, prefix, slow_share, tmp_pa
     from wl_preproc.nwb import publish as publish_module
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, _key, blocks = activation
-    key = _derivative(session_key, blocks, prefix, blocks[-1])
+    session_key, _key, runs = activation
+    key = _derivative(session_key, runs, prefix, runs[-1])
     _unrecord(key, slow_share)
     build_module.record(key, build_module.build(key, tmp_path_factory.mktemp("nwb-freed-publish-build")))
     publish_module.run_publish(slow_share, freed=[session_key])
@@ -568,8 +578,8 @@ def test_a_publish_that_fails_verification_records_nothing_and_retries(activatio
     from wl_preproc.nwb import publish as publish_module
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, _key, blocks = activation
-    key = _derivative(session_key, blocks, prefix, blocks[-1])
+    session_key, _key, runs = activation
+    key = _derivative(session_key, runs, prefix, runs[-1])
     _unrecord(key, slow_share)
     build_module.record(key, build_module.build(key, tmp_path_factory.mktemp("nwb-verify-build")))
     slow = slow_share
@@ -595,8 +605,8 @@ def test_publishing_never_overwrites_a_file_no_placement_records(activation, pre
     from wl_preproc.nwb import publish as publish_module
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, _key, blocks = activation
-    key = _derivative(session_key, blocks, prefix, blocks[0])
+    session_key, _key, runs = activation
+    key = _derivative(session_key, runs, prefix, runs[0])
     _unrecord(key, slow_share)
     build_module.record(key, build_module.build(key, tmp_path_factory.mktemp("nwb-overwrite-first")))
     publish_module.run_publish(slow_share)
@@ -620,7 +630,7 @@ def test_the_active_set_moves_a_file_to_the_fast_share_and_back_with_its_annotat
     from wl_preproc.nwb.publish import current_placement, description_path, run_placement
     from wl_preproc.schema import nwb as nwb_schema
 
-    _session_key, key, _blocks = activation
+    _session_key, key, _runs = activation
     _set_active(key)
     report = daemon_module.run_once(prefix=prefix, nwb_slow=slow_share, nwb_fast=fast_share)
     assert report["nwb_moved"] >= 1
@@ -646,7 +656,7 @@ def test_changed_written_once_data_stops_a_move(activation, slow_share, fast_sha
     stays where it is, and the report names the dataset."""
     from wl_preproc.nwb.publish import current_placement, run_placement
 
-    _session_key, key, _blocks = activation
+    _session_key, key, _runs = activation
     path = slow_share.local(current_placement(key)["path"])
     with h5py.File(path, "r+") as handle:
         original = handle["/intervals/trials/start_time"][0]
@@ -667,7 +677,7 @@ def test_the_fast_share_headroom_stops_a_move(activation, slow_share, fast_share
 
     from wl_preproc.nwb.publish import current_placement, run_placement
 
-    _session_key, key, _blocks = activation
+    _session_key, key, _runs = activation
     _set_active(key)
     try:
         _moved, errors = run_placement(slow_share, dataclasses.replace(fast_share, headroom_bytes=10**18))
@@ -684,7 +694,7 @@ def test_an_old_copy_that_could_not_be_deleted_is_removed_on_the_next_pass(activ
     live copy remains and a later move back is not stuck on a conflict."""
     from wl_preproc.nwb import publish as publish_module
 
-    _session_key, key, _blocks = activation
+    _session_key, key, _runs = activation
     old = slow_share.local(publish_module.current_placement(key)["path"])
     real = publish_module._remove_old_copy
 
@@ -713,7 +723,7 @@ def test_an_active_set_naming_a_refused_file_changes_nothing(activation, slow_sh
     from wl_preproc.nwb.publish import current_placement, run_placement, run_publish
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, _key, _blocks = activation
+    session_key, _key, _runs = activation
     refused = (nwb_schema.NwbFile & session_key & {"status": "refused"}).keys()
     assert refused
     _set_active(*refused)
@@ -731,7 +741,7 @@ def test_a_published_file_deleted_by_hand_is_reported_not_moved(activation, slow
     placement stays as recorded and every pass says so, by path."""
     from wl_preproc.nwb.publish import current_placement, run_placement
 
-    _session_key, key, _blocks = activation
+    _session_key, key, _runs = activation
     path = slow_share.local(current_placement(key)["path"])
     kept = path.read_bytes()
     path.unlink()
@@ -751,8 +761,8 @@ def test_an_active_activation_publishes_straight_to_the_fast_share(activation, p
     from wl_preproc.nwb import build as build_module
     from wl_preproc.nwb import publish as publish_module
 
-    session_key, _key, blocks = activation
-    key = _derivative(session_key, blocks, prefix, blocks[-1])
+    session_key, _key, runs = activation
+    key = _derivative(session_key, runs, prefix, runs[-1])
     _unrecord(key, slow_share, fast_share)
     build_module.record(key, build_module.build(key, tmp_path_factory.mktemp("nwb-straight-to-fast")))
     _set_active(key)
@@ -773,7 +783,7 @@ def test_the_listing_and_the_active_set_through_the_responders_functions(activat
     from wl_preproc.responder.nwb import list_files, set_active
     from wl_preproc.schema import nwb as nwb_schema
 
-    _session_key, key, _blocks = activation
+    _session_key, key, _runs = activation
     identifier = (nwb_schema.NwbFile & key).fetch1("nwb_identifier")
     everything = list_files(None, prefix=prefix)
     (entry,) = [item for item in everything["files"] if item["identifier"] == identifier]
@@ -825,8 +835,8 @@ def test_a_rebuilt_row_takes_over_its_annotated_published_file_on_either_share(
     from wl_preproc.nwb import publish as publish_module
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, _key, blocks = activation
-    key = _derivative(session_key, blocks, prefix, blocks[0])
+    session_key, _key, runs = activation
+    key = _derivative(session_key, runs, prefix, runs[0])
     _fresh(key, prefix, slow_share, fast_share, tmp_path_factory, f"nwb-takeover-{old_tier}")
     old_share, new_tier = (fast_share, "slow") if old_tier == "fast" else (slow_share, "fast")
     try:
@@ -860,8 +870,8 @@ def test_an_unrecorded_copy_with_other_data_on_the_other_share_is_refused(activa
     from wl_preproc.nwb import publish as publish_module
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, _key, blocks = activation
-    key = _derivative(session_key, blocks, prefix, blocks[0])
+    session_key, _key, runs = activation
+    key = _derivative(session_key, runs, prefix, runs[0])
     _fresh(key, prefix, slow_share, fast_share, tmp_path_factory, "nwb-other-data")
     _set_active(key)
     try:
@@ -892,8 +902,8 @@ def test_the_sweep_deletes_only_a_leftover_its_history_records_beside_a_present_
 
     from wl_preproc.nwb import publish as publish_module
 
-    session_key, key, blocks = activation
-    stranger_key = _derivative(session_key, blocks, prefix, blocks[-1])
+    session_key, key, runs = activation
+    stranger_key = _derivative(session_key, runs, prefix, runs[-1])
     _fresh(stranger_key, prefix, slow_share, fast_share, tmp_path_factory, "nwb-stranger")
     publish_module.run_publish(slow_share, fast_share)
     stranger = _placed_path(fast_share, stranger_key)
@@ -924,7 +934,7 @@ def test_placement_moves_a_freed_sessions_file(activation, daemon_module, prefix
     from wl_preproc.archive import scratch
     from wl_preproc.nwb.publish import current_placement, run_placement
 
-    session_key, key, _blocks = activation
+    session_key, key, _runs = activation
     monkeypatch.setattr(scratch, "currently_freed", lambda *, prefix=None: [dict(session_key)])
     _set_active(key)
     try:
@@ -941,7 +951,7 @@ def test_a_published_file_missing_where_it_belongs_is_reported_each_pass(activat
     file is gone from it. Publishing's pass says so, by path."""
     from wl_preproc.nwb.publish import current_placement, run_publish
 
-    _session_key, key, _blocks = activation
+    _session_key, key, _runs = activation
     path = slow_share.local(current_placement(key)["path"])
     aside = path.with_name(path.name + ".aside")
     path.rename(aside)
@@ -960,8 +970,8 @@ def test_a_file_left_unrecorded_after_its_rename_is_adopted_next_pass(activation
     from wl_preproc.nwb import publish as publish_module
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, _key, blocks = activation
-    key = _derivative(session_key, blocks, prefix, blocks[-1])
+    session_key, _key, runs = activation
+    key = _derivative(session_key, runs, prefix, runs[-1])
     _fresh(key, prefix, slow_share, fast_share, tmp_path_factory, "nwb-adopt")
     real = publish_module.write_description
 
@@ -991,8 +1001,8 @@ def test_a_share_that_is_not_mounted_is_not_published_to(activation, prefix, slo
     from wl_preproc.nwb.publish import NWB_DIR, Share
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, _key, blocks = activation
-    key = _derivative(session_key, blocks, prefix, blocks[-1])
+    session_key, _key, runs = activation
+    key = _derivative(session_key, runs, prefix, runs[-1])
     _fresh(key, prefix, slow_share, fast_share, tmp_path_factory, f"nwb-unmounted-{state.replace(' ', '-')}")
     mount = tmp_path_factory.mktemp("nwb-unmounted")
     if state == "no mount point":
@@ -1011,7 +1021,7 @@ def test_an_unreachable_fast_share_fails_its_moves_not_the_pass(activation, daem
     still reaches the slow share, and the daemon pass goes on."""
     from wl_preproc.nwb.publish import Share, current_placement
 
-    _session_key, key, _blocks = activation
+    _session_key, key, _runs = activation
     gone = Share(tier="fast", mount=tmp_path_factory.mktemp("nwb-fast-down") / "gone", host="wl-nas", name="nvme")
     _set_active(key)
     try:
@@ -1043,7 +1053,7 @@ def test_a_file_changed_while_it_is_moved_is_not_moved(activation, monkeypatch,
     move is abandoned instead, and retried when the file is still."""
     from wl_preproc.nwb import publish as publish_module
 
-    _session_key, key, _blocks = activation
+    _session_key, key, _runs = activation
     source = slow_share.local(publish_module.current_placement(key)["path"])
     real = publish_module.copy_verified
 
@@ -1069,7 +1079,7 @@ def test_a_leftover_annotated_after_its_move_is_left_for_a_person(activation, mo
     had it open, and they wrote to it. The next pass must not delete it."""
     from wl_preproc.nwb import publish as publish_module
 
-    _session_key, key, _blocks = activation
+    _session_key, key, _runs = activation
     old = slow_share.local(publish_module.current_placement(key)["path"])
     real = publish_module._remove_old_copy
 
@@ -1107,8 +1117,8 @@ def test_a_second_wlpp_process_leaves_the_nwb_stages_alone(activation, daemon_mo
     from wl_preproc.cli.main import main
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, _key, blocks = activation
-    key = _derivative(session_key, blocks, prefix, blocks[-1])
+    session_key, _key, runs = activation
+    key = _derivative(session_key, runs, prefix, runs[-1])
     _unrecord(key, slow_share, fast_share)
     other = pymysql.connect(host=dj.config["database.host"], port=int(dj.config["database.port"]),
                             user=dj.config["database.user"], password=dj.config["database.password"])
@@ -1139,7 +1149,7 @@ def test_a_superseded_activation_that_was_never_built_is_not_built(activation, p
     from wl_preproc.schema import nwb as nwb_schema
     from wl_preproc.schema import request
 
-    session_key, _key, _blocks = activation
+    session_key, _key, _runs = activation
     now = datetime.datetime(2027, 6, 1, 12, 0)
     old = {**session_key, "montage_id": 1, "activation_id": 50}
     new = {**session_key, "montage_id": 1, "activation_id": 51}
@@ -1168,7 +1178,7 @@ def test_the_listing_marks_a_superseded_file_through_the_cursor(activation, pref
     from wl_preproc.schema import nwb as nwb_schema
     from wl_preproc.schema import request
 
-    session_key, _key, _blocks = activation
+    session_key, _key, _runs = activation
     now = datetime.datetime(2027, 6, 1, 12, 0)
     old = {**session_key, "montage_id": 1, "activation_id": 60}
     new = {**session_key, "montage_id": 1, "activation_id": 61}
@@ -1205,8 +1215,8 @@ def test_an_invalid_file_is_rebuilt_once_its_missing_subject_details_arrive(acti
     from wl_preproc.schema import nwb as nwb_schema
     from wl_preproc.schema import pipeline
 
-    session_key, _key, blocks = activation
-    key = _derivative(session_key, blocks, prefix, blocks[0])
+    session_key, _key, runs = activation
+    key = _derivative(session_key, runs, prefix, runs[0])
     _unrecord(key, slow_share, fast_share)
     birth = (pipeline.subject.Subject & {"subject": _SUBJECT}).fetch1("subject_birth_date")
     pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": SUBJECT_BIRTH_DATE_UNKNOWN})
@@ -1226,9 +1236,9 @@ def test_an_invalid_file_is_rebuilt_once_its_missing_subject_details_arrive(acti
         pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": birth})
 
 
-def _lifecycle_rows(session_key, request_key, rows):
-    """`Activation` rows written directly, each over block 2; tests/ is
-    outside the supersedes guardrail's scan."""
+def _lifecycle_rows(session_key, request_key, rows, runs=(2,)):
+    """`Activation` rows written directly, each over `runs` (run 2 by
+    default); tests/ is outside the supersedes guardrail's scan."""
     from wl_preproc.schema import request
 
     now = datetime.datetime(2027, 6, 1, 12, 0)
@@ -1236,8 +1246,9 @@ def _lifecycle_rows(session_key, request_key, rows):
                              "payload": {}, "requested_at": now})
     for row in rows:
         request.Activation.insert1({"request_key": request_key, "created_at": now, **session_key, **row})
-        request.ActivationBlock.insert1({**session_key, "montage_id": row["montage_id"],
-                                         "activation_id": row["activation_id"], "block_id": 2})
+        for number in runs:
+            request.ActivationRun.insert1({**session_key, "montage_id": row["montage_id"],
+                                           "activation_id": row["activation_id"], "run_number": number})
 
 
 def _drop_lifecycle_rows(session_key, request_key, keys):
@@ -1262,7 +1273,7 @@ def test_an_invalid_row_the_stage_will_not_rebuild_survives_its_details_arriving
     from wl_preproc.schema import nwb as nwb_schema
     from wl_preproc.schema import pipeline
 
-    session_key, _key, _blocks = activation
+    session_key, _key, _runs = activation
     old = {**session_key, "montage_id": 0, "activation_id": 90}
     new = {**session_key, "montage_id": 0, "activation_id": 91}
     rows = [{"montage_id": 0, "activation_id": 90, "role": "canonical"}]
@@ -1294,7 +1305,7 @@ def test_a_file_invalid_for_another_reason_is_not_rebuilt_every_pass(activation,
     import wl_preproc.nwb.build as build_module
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, _key, _blocks = activation
+    session_key, _key, _runs = activation
     key = {**session_key, "montage_id": 0, "activation_id": 97}
     _lifecycle_rows(session_key, "loop-invalid", [{"montage_id": 0, "activation_id": 97, "role": "derivative",
                                                    "selection_hash": "loop-invalid"}])
@@ -1319,7 +1330,7 @@ def test_the_invalid_check_reads_each_subject_once(activation, prefix, monkeypat
     from wl_preproc.nwb.build import build, record, resolved_invalid
     from wl_preproc.schema import pipeline
 
-    session_key, _key, _blocks = activation
+    session_key, _key, _runs = activation
     keys = [{**session_key, "montage_id": 0, "activation_id": i} for i in (80, 81)]
     _lifecycle_rows(session_key, "subject-once", [{"montage_id": 0, "activation_id": i, "role": "derivative",
                                                    "selection_hash": f"subject-once-{i}"} for i in (80, 81)])
@@ -1344,7 +1355,7 @@ def test_the_command_refuses_a_superseded_activation(activation, prefix, tmp_pat
     from wl_preproc.cli.main import main
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, _key, _blocks = activation
+    session_key, _key, _runs = activation
     old = {**session_key, "montage_id": 0, "activation_id": 70}
     new = {**session_key, "montage_id": 0, "activation_id": 71}
     _lifecycle_rows(session_key, "command-superseded", [
@@ -1364,7 +1375,7 @@ def test_an_activation_waiting_on_a_freed_session_is_reported(activation, prefix
     and each pass says so."""
     from wl_preproc.nwb.build import run_stage
 
-    session_key, _key, _blocks = activation
+    session_key, _key, _runs = activation
     key = {**session_key, "montage_id": 0, "activation_id": 75}
     _lifecycle_rows(session_key, "freed-waiting", [{"montage_id": 0, "activation_id": 75, "role": "derivative",
                                                     "selection_hash": "freed-waiting"}])
@@ -1384,11 +1395,11 @@ def test_one_failing_activation_does_not_stop_the_stage(activation, prefix, monk
     from wl_preproc.responder.jobs import accept
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, _key, blocks = activation
-    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
-    failing, fine = (accept(_request(session_key, f"nwbstep1-stage-{block['block_id']}", montage, blocks,
-                                     block_ids=[block["block_id"]]), prefix=prefix)
-                     for block in sorted(blocks, key=lambda b: b["start_s"]))
+    session_key, _key, runs = activation
+    montage = _montage(runs)
+    failing, fine = (accept(_request(session_key, f"nwbstep1-stage-{run['run_number']}", montage, runs,
+                                     run_numbers=[run["run_number"]]), prefix=prefix)
+                     for run in sorted(runs, key=lambda r: r["start_s"]))
     assert failing != fine
     for key in (failing, fine):
         (nwb_schema.NwbFile & key).delete(prompt=False)
@@ -1415,8 +1426,8 @@ def test_the_command_refuses_a_freed_session(activation, prefix, monkeypatch, tm
     from wl_preproc.cli.main import main
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, _key, blocks = activation
-    key = _derivative(session_key, blocks, prefix, blocks[-1])
+    session_key, _key, runs = activation
+    key = _derivative(session_key, runs, prefix, runs[-1])
     (nwb_schema.NwbFile & key).delete(prompt=False)
     monkeypatch.setattr(scratch, "currently_freed", lambda *, prefix=None: [dict(session_key)])
     assert main(_command(key, tmp_path_factory.mktemp("nwb-freed-command"), prefix)) == 1
@@ -1433,7 +1444,7 @@ def test_a_path_recorded_for_another_activation_is_refused(activation, prefix, m
     from wl_preproc.nwb import build as build_module
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, canonical, blocks = activation
+    session_key, canonical, runs = activation
     if not nwb_schema.NwbFile & canonical:
         build_module.record(canonical, build_module.build(canonical, tmp_path_factory.mktemp("nwb-canonical")))
     recorded = Path((nwb_schema.NwbFile & canonical).fetch1("path"))
@@ -1445,7 +1456,7 @@ def test_a_path_recorded_for_another_activation_is_refused(activation, prefix, m
         recorded.parent.mkdir(parents=True, exist_ok=True)
         recorded.write_bytes(b"another activation's file")
     before = recorded.read_bytes()
-    key = _derivative(session_key, blocks, prefix, blocks[-1])
+    key = _derivative(session_key, runs, prefix, runs[-1])
     monkeypatch.setattr(build_module, "nwb_path", lambda *args: recorded)
     result = build_module.build(key, tmp_path_factory.mktemp("nwb-collision"))
     assert (result.status, result.path) == ("refused", None)
@@ -1453,52 +1464,53 @@ def test_a_path_recorded_for_another_activation_is_refused(activation, prefix, m
     assert recorded.read_bytes() == before
 
 
-def test_blocks_the_eye_recording_does_not_reach_are_built_without_eye_data(activation, prefix, tmp_path_factory):
-    """The final review's I2: an activation whose blocks hold no eye sample
+def test_runs_the_eye_recording_does_not_reach_are_built_without_eye_data(activation, prefix, tmp_path_factory):
+    """The final review's I2: an activation whose runs hold no eye sample
     (the tracker started late, or stopped early) is built without eye data,
-    and its description says so. For one build the first block is moved past
+    and its description says so. For one build the first run is moved past
     the end of the recording, and restored after."""
     from pynwb import NWBHDF5IO
 
     from wl_preproc.nwb.build import build
     from wl_preproc.schema import core
 
-    session_key, _key, blocks = activation
-    first = min(blocks, key=lambda b: b["start_s"])
-    past = max(b["end_s"] for b in blocks) + 2.0
-    key = _derivative(session_key, blocks, prefix, first)
-    core.Block.update1({**session_key, "block_id": first["block_id"], "start_s": past, "end_s": past + 1.0})
+    session_key, _key, runs = activation
+    first = min(runs, key=lambda r: r["start_s"])
+    past = max(r["end_s"] for r in runs) + 2.0
+    key = _derivative(session_key, runs, prefix, first)
+    core.Run.update1({**session_key, "run_number": first["run_number"], "run_start_time": past,
+                      "run_stop_time": past + 1.0})
     try:
         result = build(key, tmp_path_factory.mktemp("nwb-no-samples"))
     finally:
-        core.Block.update1({**session_key, "block_id": first["block_id"], "start_s": first["start_s"],
-                            "end_s": first["end_s"]})
+        core.Run.update1({**session_key, "run_number": first["run_number"], "run_start_time": first["start_s"],
+                          "run_stop_time": first["end_s"]})
     assert result.status == "written", (result.reason, result.findings)
     with NWBHDF5IO(str(result.path), "r") as handle:
         nwb = handle.read()
         assert "behavior" not in nwb.processing and "eye_events" not in nwb.processing
-        assert "no sample in these blocks" in nwb.session_description
+        assert "no sample in these runs" in nwb.session_description
 
 
 def test_the_stage_waits_for_upstream_keys_not_yet_computed(activation, prefix, tmp_path_factory):
     """The final review's I4(a): a key of this session that an upstream table
     has not computed yet (still to run, or errored) would leave the file
-    without it, recorded as final. One `BlockCoverage` row is held back, and
+    without it, recorded as final. One `RunCoverage` row is held back, and
     the stage waits until it is back."""
     from wl_preproc.nwb.build import run_stage
     from wl_preproc.schema import coverage
     from wl_preproc.schema import nwb as nwb_schema
 
-    session_key, _key, blocks = activation
-    key = _derivative(session_key, blocks, prefix, blocks[-1])
+    session_key, _key, runs = activation
+    key = _derivative(session_key, runs, prefix, runs[-1])
     (nwb_schema.NwbFile & key).delete(prompt=False)
-    saved = (coverage.BlockCoverage & session_key).to_dicts()[0]
-    (coverage.BlockCoverage & {k: saved[k] for k in coverage.BlockCoverage.primary_key}).delete(prompt=False)
+    saved = (coverage.RunCoverage & session_key).to_dicts()[0]
+    (coverage.RunCoverage & {k: saved[k] for k in coverage.RunCoverage.primary_key}).delete(prompt=False)
     try:
         run_stage(tmp_path_factory.mktemp("nwb-wait-upstream"))
         assert len(nwb_schema.NwbFile & key) == 0
     finally:
-        coverage.BlockCoverage.insert1(saved, allow_direct_insert=True)
+        coverage.RunCoverage.insert1(saved, allow_direct_insert=True)
     run_stage(tmp_path_factory.mktemp("nwb-wait-upstream-after"))
     assert (nwb_schema.NwbFile & key).fetch1("status") == "written"
 
@@ -1511,7 +1523,7 @@ def test_the_stage_waits_only_on_what_the_file_reads(activation, prefix):
     from wl_preproc.nwb.gather import readiness
     from wl_preproc.schema import detect, paramset
 
-    session_key, key, _blocks = activation
+    session_key, key, _runs = activation
     extra = paramset.register("eye_detection", {"detector": "not_a_detector_nwb_build"})
     try:
         assert len((detect.EyeDetection().key_source & session_key) - detect.EyeDetection.proj()) >= 1
@@ -1530,8 +1542,8 @@ def test_the_stage_waits_for_session_time_rather_than_refusing(activation, prefi
     from wl_preproc.schema import nwb as nwb_schema
     from wl_preproc.schema import timebase
 
-    session_key, _key, blocks = activation
-    key = _derivative(session_key, blocks, prefix, blocks[-1])
+    session_key, _key, runs = activation
+    key = _derivative(session_key, runs, prefix, runs[-1])
     (nwb_schema.NwbFile & key).delete(prompt=False)
     saved = (timebase.TimingProvenance & session_key).fetch1()
     (timebase.TimingProvenance & session_key).delete(prompt=False)
@@ -1555,7 +1567,7 @@ def test_a_file_without_the_subjects_date_of_birth_is_invalid(activation, tmp_pa
     from wl_preproc.nwb.build import build
     from wl_preproc.schema import pipeline
 
-    _session_key, key, _blocks = activation
+    _session_key, key, _runs = activation
     birth = (pipeline.subject.Subject & {"subject": _SUBJECT}).fetch1("subject_birth_date")
     pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": SUBJECT_BIRTH_DATE_UNKNOWN})
     try:
@@ -1568,13 +1580,13 @@ def test_a_file_without_the_subjects_date_of_birth_is_invalid(activation, tmp_pa
 
 def test_a_session_without_session_time_is_refused(activation, tmp_path_factory):
     """Section 10: no `TimingProvenance` row means no trustworthy session
-    time. The row is restored as it was, not recomputed: recomputing now
-    would compare wl.works' two asserted blocks with the one measured and
-    rightly fail the session to tier D (parent spec section 8.3.1)."""
+    time. The row is restored as it was, not recomputed.
+    *It said, until requests named runs, that recomputing would compare
+    wl.works' two asserted blocks with the one measured; true when written.*"""
     from wl_preproc.nwb.build import build
     from wl_preproc.schema import timebase
 
-    session_key, key, _blocks = activation
+    session_key, key, _runs = activation
     saved = (timebase.TimingProvenance & session_key).fetch1()
     (timebase.TimingProvenance & session_key).delete(prompt=False)
     try:
@@ -1591,7 +1603,7 @@ def test_a_session_at_timing_tier_d_is_refused(activation, tmp_path_factory):
     from wl_preproc.nwb.build import build
     from wl_preproc.schema import timebase
 
-    session_key, key, _blocks = activation
+    session_key, key, _runs = activation
     saved = (timebase.TimingProvenance & session_key).fetch1()
     (timebase.TimingProvenance & session_key).delete(prompt=False)
     timebase.TimingProvenance.insert1({**saved, "tier": "D"}, allow_direct_insert=True)
@@ -1610,7 +1622,7 @@ def test_a_session_with_two_ohdpi_segments_is_refused(activation, tmp_path_facto
     from wl_preproc.nwb.build import build
     from wl_preproc.schema import core
 
-    session_key, key, _blocks = activation
+    session_key, key, _runs = activation
     segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
     extra = {**segment, "segment_barcode": segment["segment_barcode"] + 1_000}
     core.Segment.insert1(extra, allow_direct_insert=True)
```

```diff
--- a/tests/schema/test_nwb_probes.py
+++ b/tests/schema/test_nwb_probes.py
@@ -9,7 +9,15 @@ import datetime
 
 import pytest
 
-from tests.schema.test_spikeglx_restart import RESTART, _session
+from tests.schema.test_spikeglx_restart import RESTART
+from tests.schema.test_spikeglx_restart import _session as _restart_session
+
+
+def _session(tmp_path_factory, mutate=None, **update):
+    """`test_spikeglx_restart.py::_session`, its blocks wrapped in runs: a
+    canonical request names the session's measured runs (design spec
+    `2026-10-01-session-listing-and-run-requests-design.md` section 3)."""
+    return _restart_session(tmp_path_factory, mutate, runs=True, **update)
 
 _S1, _S2, _S3 = "19011110011", "19011110012", "19011110013"
 _AIM = {"area": "V4d", "atlas": "CHARM", "atlas_level": 6}
@@ -30,25 +38,25 @@ def _bad_part(directory):
         meta.write_text(meta.read_text().replace("imDatPrb_pn=NP1000", "imDatPrb_pn=NP9999"))
 
 
-def _canonical(daemon_module, prefix, key, idempotency_key, probes, block_ids=None):
-    """wl.works' canonical over the session's measured blocks, as
-    `tests/schema/test_nwb_build.py`'s fixture asks for it, then a pass.
-    With `block_ids`, a derivative over those blocks instead."""
+def _canonical(daemon_module, prefix, key, idempotency_key, probes, run_numbers=None):
+    """wl.works' canonical over the session's measured runs, as
+    `tests/schema/test_nwb_build.py`'s fixture asks for it, every probe's
+    sort covering every run, then a pass. With `run_numbers`, a derivative
+    over those runs instead."""
     from wl_preproc.contracts.protocol import JobRequest, MetadataBundle
     from wl_preproc.responder.jobs import accept
-    from wl_preproc.schema import pipeline
+    from wl_preproc.schema import core
 
-    blocks = [{"block_id": index, "task_type": (pipeline.trial.Block.Attribute & row & {
-                   "attribute_name": "task_type"}).fetch1("attribute_value"),
-               "start_s": float(row["block_start_time"]), "end_s": float(row["block_stop_time"]),
-               "works_block_id": f"wb-{index}"}
-              for index, row in enumerate((pipeline.trial.Block & key).to_dicts(order_by="block_start_time"), 1)]
+    runs = [{"run_number": row["run_number"], "start_s": row["run_start_time"], "end_s": row["run_stop_time"],
+             "works_run_id": f"wr-{row['run_number']}"}
+            for row in (core.Run & key).to_dicts(order_by="run_number")]
+    every = {probe["serial"]: [run["run_number"] for run in runs] for probe in probes}
     activation = accept(JobRequest(
         domain="neural", parameters={}, idempotency_key=idempotency_key,
         selection={"session_datetime": key["session_datetime"].replace(tzinfo=datetime.UTC), "montage_id": 0,
-                   **({} if block_ids is None else {"block_ids": block_ids})},
+                   **({"probe_runs": every} if run_numbers is None else {"run_numbers": run_numbers})},
         metadata=MetadataBundle(
-            blocks=blocks, montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 16.0}], probes=probes,
+            blocks=[], runs=runs, montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 16.0}], probes=probes,
             experimenter="jw", subject=key["subject"], task_types=[],
             subject_details={"species": "Macaca mulatta", "sex": "F", "date_of_birth": datetime.date(2016, 3, 2)}),
     ), prefix=prefix)
@@ -141,7 +149,7 @@ def test_an_intan_probe_is_listed_from_its_report_alone(daemon_module, prefix, t
     from wl_preproc.synth.session import generate_session
 
     recipe = CI_RECIPE.model_copy(update={"subject": "pnwb4", "session_id": "2025-06-25_01",
-                                          "systems": ("syncbox", "rhs")})
+                                          "systems": ("syncbox", "rhs"), "runs": True})
     root = tmp_path_factory.mktemp("pnwb4")
     generate_session(root, recipe)
     key = _land(root, recipe, datetime.datetime(2025, 6, 25, 9), acquisition_systems=("syncbox", "rhs"))
@@ -202,10 +210,11 @@ def test_a_bank_change_inside_a_montage_refuses_the_file(daemon_module, prefix,
 
 def test_a_file_on_one_side_of_a_bank_change_builds(daemon_module, prefix, tmp_path_factory):
     """The final review's I1: a file's probes come from the segments its own
-    blocks overlap, not from its whole montage. A derivative before the bank
-    change is recorded through one site map and builds -- parent spec
-    section 8.3's remedy for a montage wl.works drew across a bank change --
-    and one whose blocks cross the change is refused, as the canonical is."""
+    runs overlap, not from its whole montage. A derivative of the run before
+    the bank change is recorded through one site map and builds -- parent
+    spec section 8.3's remedy for a montage wl.works drew across a bank
+    change -- and one whose run crosses the change is refused, as the
+    canonical is."""
     from pynwb import NWBHDF5IO
 
     from wl_preproc.nwb.build import build
@@ -213,8 +222,8 @@ def test_a_file_on_one_side_of_a_bank_change_builds(daemon_module, prefix, tmp_p
     _recipe, key = _session(tmp_path_factory, subject="pnwb6", session_id="2025-06-27_01", probe_serial="19011110019",
                             spikeglx_restart={**RESTART, "probe_bank": 1})
     daemon_module.run_once(prefix=prefix)
-    before = _canonical(daemon_module, prefix, key, "pnwb6-k1", [], block_ids=[1])
-    across = _canonical(daemon_module, prefix, key, "pnwb6-k2", [], block_ids=[2])
+    before = _canonical(daemon_module, prefix, key, "pnwb6-k1", [], run_numbers=[1])
+    across = _canonical(daemon_module, prefix, key, "pnwb6-k2", [], run_numbers=[2])
 
     built = build(before, tmp_path_factory.mktemp("nwb-before"))
     assert built.status == "written", (built.reason, built.findings)
@@ -242,12 +251,16 @@ def test_a_file_waits_for_the_census_of_its_session(three_probes):
 
 def test_the_description_lists_each_probe_and_says_what_it_could_not_place_or_join(built):
     """Section 3.2's entries and notes: an unknown part number, a probe with
-    no report, and a report that matches no recorded probe."""
+    no report, and a report that matches no recorded probe. Each probe's
+    sorted runs are its list from the request; the probe with no report has
+    none (design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    section 4)."""
     probes = built.description["probes"]
     assert [(p["serial"], p["probe_type"], p["insertion_number"], p["trajectory_id"], p["n_electrodes"],
              p["area_from"]) for p in probes] == [
         (_S1, "NP1000", 1, "T-1", 4, "assignment"), (_S2, "NP1032", 2, None, 4, "target"),
         (_S3, "NP9999", None, None, 0, "unknown")]
+    assert [p["sorted_runs"] for p in probes] == [[1, 2], [1, 2], []]
     assert probes[0]["target"] == _AIM
     assert probes[0]["assignment"] == {"area": "V4v", "source": "at_rig", "asserted_at": "2025-06-22T11:00:00Z"}
     notes = built.description["notes"]
```

```diff
--- a/tests/schema/test_nwb_trials.py
+++ b/tests/schema/test_nwb_trials.py
@@ -8,8 +8,7 @@ from __future__ import annotations
 
 import pytest
 
-from tests.schema.test_nwb_probes import _canonical
-from tests.schema.test_spikeglx_restart import _session
+from tests.schema.test_nwb_probes import _canonical, _session
 
 
 @pytest.fixture(scope="module")
```

```diff
--- a/tests/schema/test_request.py
+++ b/tests/schema/test_request.py
@@ -1282,17 +1282,13 @@ def test_a_replacement_of_anything_but_the_current_canonical_is_a_conflict(req,
 
 def test_a_canonical_can_name_its_block_set(req, selection):
     """Section 3: how wl.works leaves out a bad block, on a first canonical
-    or a replacement. The builder reads the set from `ActivationBlock`."""
-    from wl_preproc.nwb.gather import _block_set
-
+    or a replacement, recorded in `ActivationBlock`. The builder reads a
+    file's runs now (`ActivationRun`), and the block paths go next."""
     first = req.submit("k-blocks-1", "neural", "wl_works", selection, {}, None, block_ids=[1, 3])
     replacement = req.submit_replacement("k-blocks-2", "neural", "wl_works", selection, {}, None,
                                          supersedes_activation_id=0, block_ids=[2])
-    session_key = {k: selection[k] for k in ("subject", "session_datetime")}
     for key, expected in ((first, [1, 3]), (replacement, [2])):
-        row = (req.Activation & key).fetch1()
         assert sorted(int(b) for b in (req.ActivationBlock & key).to_arrays("block_id")) == expected
-        assert [b["block_id"] for b in _block_set(key, row, session_key)] == expected
 
 
 def test_a_reused_key_for_another_replacement_is_key_reuse(req, selection):
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/synth/test_spikeglx.py tests/nwb tests/cli/test_schemas_export.py tests/schema/test_nwb_build.py tests/schema/test_nwb_probes.py tests/schema/test_nwb_trials.py tests/schema/test_request.py -q --tb=line -p no:cacheprovider`
Expected: 71 failed, 146 passed, 1 skipped. The contracts have no runs: `Gathered` takes no `runs` and `nwb.intervals` has no `add_runs` (the describe and writer tests), nor `events.runs` (`test_helpers.py`), nor a version-3 schema to export. The builder still reads a block set the activation does not hold, so every NWB build fixture's file is refused (`no blocks in the canonical activation's block set`). The probe and trial tests' sessions, whose blocks now sit in runs, read tier D until the generator's fix (`timing tier D: no trustworthy session time`), as does `test_the_last_code_words_strobe_falls_before_the_file_ends[True]`.

- [ ] **Step 3: Implement.** Create the containment helpers and apply the diffs:

```python
"""Which run a block or a trial belongs to (design spec
`2026-10-01-session-listing-and-run-requests-design.md` amendment 11).

element-event stores a block's and a trial's start as a MySQL FLOAT
(`trial.Block`, `trial.Trial`), and an event's time as `decimal(10, 4)`
(`event.Event`); `core.Run` stores a run's bounds as doubles. Either rounding
can put a start that lies just after its run's `RUN_START` -- or the
`RUN_START` event itself, which lies exactly on it -- before the run. The
run's bounds are rounded the same way first: rounding is monotonic, so
anything that starts inside its run is found inside it, at any magnitude.
The listing and the NWB builder both ask here, so they cannot disagree.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import numpy as np


def starts_inside(start_s: float, run_start_s: float, run_stop_s: float) -> bool:
    """Whether a float32-stored start lies in a run's double bounds."""
    return float(np.float32(run_start_s)) <= start_s <= float(np.float32(run_stop_s))


def run_of(start_s: float, runs: list[dict]) -> int | None:
    """The `run_number` of the first of `runs` (each with `start_s`, `end_s`)
    that `start_s` lies in, or None."""
    for run in runs:
        if starts_inside(start_s, run["start_s"], run["end_s"]):
            return run["run_number"]
    return None


def _as_event_time(value_s: float) -> float:
    """`value_s` rounded as MySQL stores it in `decimal(10, 4)`: to 0.1 ms,
    half away from zero."""
    return float(Decimal(repr(float(value_s))).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def event_inside(time_s: float, run_start_s: float, run_stop_s: float) -> bool:
    """Whether an `event.Event` time lies in a run's double bounds, both ends
    included: an event is an instant, and `RUN_START`/`RUN_END` sit exactly
    on them."""
    return _as_event_time(run_start_s) <= time_s <= _as_event_time(run_stop_s)
```

```diff
--- a/wl_preproc/listing/entry.py
+++ b/wl_preproc/listing/entry.py
@@ -9,9 +9,8 @@ listing stage and `GET /sessions` both call it, so they cannot disagree.
 **A block belongs to the run its start lies in**, and a segment to every run
 it overlaps: runs and segments do not align, since a bank change needs a
 SpikeGLX restart and a run need not stop for one. A block's start is stored
-as a MySQL FLOAT (`trial.Block`), a run's as a double (`core.Run`), so the run
-is rounded to float32 before they are compared: rounding is monotonic, so a
-block that starts inside its run is found inside it at any magnitude.
+as a MySQL FLOAT and a run's as a double, so they are compared by
+`events/runs.py::starts_inside`, as the NWB builder compares them.
 """
 
 from __future__ import annotations
@@ -21,7 +20,7 @@ import dataclasses
 import datetime
 from pathlib import Path
 
-import numpy as np
+from wl_preproc.events.runs import starts_inside
 
 from wl_preproc.contracts.protocol import SessionEntry
 
@@ -96,8 +95,7 @@ def build_entry(facts: SessionFacts) -> dict:
         start, stop = run["run_start_time"], run["run_stop_time"]
         record = facts.records.get(run["run_number"], {})
         spanned = [segment for segment in segments if segment["start_s"] < stop and segment["end_s"] > start]
-        low, high = float(np.float32(start)), float(np.float32(stop))
-        blocks = sorted((block for block in facts.blocks if low <= block["block_start_time"] <= high),
+        blocks = sorted((block for block in facts.blocks if starts_inside(block["block_start_time"], start, stop)),
                         key=lambda block: block["block_start_time"])
         listed_blocks = []
         in_a_run.update(block["block_id"] for block in blocks)
```

```diff
--- a/wl_preproc/contracts/nwb_description.py
+++ b/wl_preproc/contracts/nwb_description.py
@@ -3,8 +3,11 @@ opening it. Frozen interface: exported to `docs/schemas/nwb_description.json`
 and checked in CI (design spec `2026-09-29-nwb-publishing-design.md`
 section 2).
 
-**Versioned and open.** `schema_version` is 2: version 2 gave `probes` its
-shape, each probe with both of its areas (design spec
+**Versioned and open.** `schema_version` is 3: version 3 replaced `blocks`
+with `runs`, each with `works_run_id` for wl.works' `animal_session_run` and
+the measured blocks inside it, and gave each probe its `sorted_runs` (design
+spec `2026-10-01-session-listing-and-run-requests-design.md` section 4).
+Version 2 gave `probes` their shape, each with both of its areas (design spec
 `2026-09-30-nwb-probes-design.md` section 3.2). Later pieces add groups and
 fields -- a processing summary with unit counts, the photodiode, video and
 stimulation -- and never rename or remove one within a version. This side validates strictly (`extra="forbid"`), so what it publishes
@@ -22,7 +25,7 @@ from typing import Any, Literal
 
 from pydantic import BaseModel, ConfigDict
 
-SCHEMA_VERSION = 2
+SCHEMA_VERSION = 3
 
 
 class _Frozen(BaseModel):
@@ -94,7 +97,7 @@ class TrialCounts(_Frozen):
 
 
 class Condition(_Frozen):
-    """One condition that ran in a block: by its name in the rig's record,
+    """One condition that ran in a run: by its name in the rig's record,
     with the settings constant across its trials and a summary of those that
     varied; or, without that record, by the stream's CONDITION number with
     settings unknown (null)."""
@@ -121,15 +124,32 @@ class Task(_Frozen):
     name: str
 
 
-class Block(_Frozen):
-    block_id: int
-    works_block_id: str | None
+class RunBlock(_Frozen):
+    """A measured block inside the run: consecutive trials under one block
+    type. `block_number` is its number in the session, as `BLOCK_START`
+    strobes it; `closed` is null when never recorded."""
+
+    block_number: int
+    block_in_run: int
+    block_type: str | None
+    measured: Interval
+    closed: bool | None
+    trials: int
+
+
+class Run(_Frozen):
+    """One run the file holds, measured from the recording. `works_run_id`
+    joins it to wl.works' `animal_session_run`."""
+
+    run_number: int
+    works_run_id: str | None
     task: Task
-    asserted: Interval
-    measured: Interval | None
+    measured: Interval
+    closed: bool
     trials: TrialCounts
     coverage: dict[str, Coverage]
     conditions: list[Condition]
+    blocks: list[RunBlock]
 
 
 class Quality(_Frozen):
@@ -182,17 +202,20 @@ class ProbeInfo(_Frozen):
     target: ProbeTarget | None
     assignment: ProbeAssignment | None
     area_from: Literal["assignment", "target", "unknown"]
+    # The runs this probe's sort covers, as the request stated them (design
+    # spec `2026-10-01-session-listing-and-run-requests-design.md` section 3.1).
+    sorted_runs: list[int] = []
 
 
 class NwbDescription(_Frozen):
-    schema_version: Literal[2] = SCHEMA_VERSION
+    schema_version: Literal[3] = SCHEMA_VERSION
     identity: Identity
     subject: SubjectInfo
     data_types: DataTypes
     # Every probe the file holds; empty in version 1. Areas are per
     # insertion: nothing yet produces a per-channel one.
     probes: list[ProbeInfo] = []
-    blocks: list[Block]
+    runs: list[Run]
     quality: Quality
     # Empty in version 1; piece 3 adds the processing summary (for example
     # the number of single units, and whether any narrow-waveform units).
```

```diff
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -3,7 +3,7 @@
 
 **The one module in `wl_preproc/nwb/` that reads the database and the raw
 ohDPI file.** Everything it returns is plain data -- dicts, lists and arrays
-in session seconds, already trimmed to the activation's blocks (section 5) --
+in session seconds, already trimmed to the activation's runs (section 5) --
 so every writer is tested without either."""
 
 from __future__ import annotations
@@ -36,11 +36,14 @@ class Refused(Exception):
 class Gathered:
     session: dict
     systems: list[str]
+    # The file's runs, and the measured blocks inside them (design spec
+    # `2026-10-01-session-listing-and-run-requests-design.md` section 4).
+    runs: list[dict]
     blocks: list[dict]
     trials: list[dict]
     events: list[dict]
     timebase: dict
-    eye: dict | None  # None: no ohDPI recording in the session, or no sample of it in the blocks
+    eye: dict | None  # None: no ohDPI recording in the session, or no sample of it in the runs
     # Every condition that ran in the file's trials, and why any trial's
     # condition or settings are unknown (design spec
     # `2026-09-29-nwb-publishing-design.md` section 2.1).
@@ -61,22 +64,34 @@ def _aware_utc(value: datetime.datetime) -> datetime.datetime:
     return value.replace(tzinfo=datetime.timezone.utc) if value.tzinfo is None else value
 
 
-def _block_set(activation_key: dict, activation: dict, session_key: dict) -> list[dict]:
+def _run_set(activation_key: dict, session_key: dict) -> list[dict]:
+    """The file's runs (`request.ActivationRun`), measured (`core.Run`), with
+    wl.works' id for each and the rig's name for its task (design spec
+    `2026-10-01-session-listing-and-run-requests-design.md` section 4)."""
     from wl_preproc.schema import core, request
 
-    # A derivative always names its blocks; since the canonical lifecycle a
-    # canonical may too (design spec `2026-09-30-canonical-lifecycle-design.md`
-    # section 3). Without named blocks, a canonical takes its montage's.
-    named = request.ActivationBlock & activation_key
-    if activation["role"] == "derivative" or named:
-        rows = (core.Block & named.proj()).to_dicts()
-    else:
-        montage = (core.Montage & activation_key).fetch1()
-        rows = [row for row in (core.Block & session_key).to_dicts()
-                if montage["start_s"] <= row["start_s"] < montage["end_s"]]
+    numbers = {int(number) for number in (request.ActivationRun & activation_key).to_arrays("run_number")}
+    works = {row["run_number"]: row["works_run_id"] for row in (core.RunAssertion & session_key).to_dicts()}
+    tasks = {row["run_number"]: row["task"] for row in (core.RunRecord & session_key).to_dicts()}
+    rows = [{"run_number": row["run_number"], "start_s": float(row["run_start_time"]),
+             "end_s": float(row["run_stop_time"]), "task_type": row["task_type"], "task": tasks.get(row["run_number"]),
+             "works_run_id": works.get(row["run_number"]), "closed": bool(row["closed"])}
+            for row in (core.Run & session_key).to_dicts() if row["run_number"] in numbers]
     return sorted(rows, key=lambda row: row["start_s"])
 
 
+def _task_name(code, rig_name: str | None) -> str:
+    """The rig's name for a run's task, else its code's name, else the code."""
+    from wl_preproc.contracts.events import TaskTypeCode
+
+    if rig_name:
+        return rig_name
+    try:
+        return TaskTypeCode(int(code)).name.lower()
+    except (TypeError, ValueError):
+        return str(code)
+
+
 def identifier_for(activation_key: dict, session_id: str) -> str:
     """`{subject}.{session_id}.montage-{m}.activation-{a}`. A session id is
     the sync box's date and index, not scoped to a subject, so two animals
@@ -125,7 +140,7 @@ def readiness(activation_key: dict) -> str | None:
     # `ProbeCensus` for every SpikeGLX segment of the session, not only the
     # montage's: a segment not yet read has no extent to place it by. A
     # session with no SpikeGLX segment has nothing to wait for.
-    for table in (coverage.BlockCoverage, coverage.TrialCoverage, ephys.ProbeCensus, eye_schema.EyeCalibration,
+    for table in (coverage.RunCoverage, coverage.TrialCoverage, ephys.ProbeCensus, eye_schema.EyeCalibration,
                   detect.EyeValidity, detect.EyeDetection, consensus.DetectorAgreement):
         pending = (table().key_source & session_key & read.get(table, {})) - table.proj()
         if len(pending):
@@ -191,35 +206,61 @@ def _coverage(table, key_field: str, session_key: dict) -> dict:
     return by_item
 
 
-def _blocks(block_rows: list[dict], session_key: dict) -> list[dict]:
-    from wl_preproc.schema import coverage, pipeline
+def _runs(run_rows: list[dict], session_key: dict) -> list[dict]:
+    """The file's runs, each with its per-system coverage (`RunCoverage`)."""
+    from wl_preproc.schema import coverage
+
+    cover = _coverage(coverage.RunCoverage, "run_number", session_key)
+    return [{**row, "coverage": cover.get(row["run_number"], {})} for row in run_rows]
+
+
+def _blocks(run_rows: list[dict], session_key: dict) -> list[dict]:
+    """The measured blocks inside the file's runs (`trial.Block`), each with
+    its run, its order there, its block type and whether it closed."""
+    from wl_preproc.events.runs import run_of
+    from wl_preproc.schema import pipeline
 
-    measured = {row["block_id"]: row for row in (pipeline.trial.Block & session_key).to_dicts()}
-    cover = _coverage(coverage.BlockCoverage, "block_id", session_key)
-    return [{
-        "block_id": row["block_id"], "start_s": float(row["start_s"]), "end_s": float(row["end_s"]),
-        "task_type": row["task_type"], "works_block_id": row["works_block_id"],
-        "measured_start_s": None if row["block_id"] not in measured else float(measured[row["block_id"]]["block_start_time"]),
-        "measured_stop_s": None if row["block_id"] not in measured else float(measured[row["block_id"]]["block_stop_time"]),
-        "coverage": cover.get(row["block_id"], {}),
-    } for row in block_rows]
+    attributes: dict = {}
+    for row in (pipeline.trial.Block.Attribute & session_key).to_dicts():
+        attributes.setdefault(row["block_id"], {})[row["attribute_name"]] = row["attribute_value"]
+    out, order = [], {}
+    for row in sorted((pipeline.trial.Block & session_key).to_dicts(), key=lambda row: row["block_start_time"]):
+        run_number = run_of(row["block_start_time"], run_rows)
+        if run_number is None:
+            continue
+        order[run_number] = order.get(run_number, 0) + 1
+        found = attributes.get(row["block_id"], {})
+        out.append({"block_number": row["block_id"], "run_number": run_number, "block_in_run": order[run_number],
+                    "block_type": found.get("block_type"), "start_s": float(row["block_start_time"]),
+                    "stop_s": float(row["block_stop_time"]),
+                    "closed": None if "closed" not in found else found["closed"] == "1"})
+    return out
 
 
-def _trials(blocks: BlockSet, session_key: dict) -> list[dict]:
+def _trials(run_rows: list[dict], session_key: dict) -> list[dict]:
+    """The trials whose start lies in one of the file's runs, each with its
+    run and its measured block."""
+    from wl_preproc.events.runs import run_of
     from wl_preproc.schema import coverage, pipeline
 
     block_of = {row["trial_id"]: row["block_id"] for row in (pipeline.trial.BlockTrial & session_key).to_dicts()}
     cover = _coverage(coverage.TrialCoverage, "trial_id", session_key)
-    rows = [row for row in (pipeline.trial.Trial & session_key).to_dicts()
-            if blocks.contains([row["trial_start_time"]])[0]]
-    return [{
-        "trial_id": row["trial_id"], "start_s": float(row["trial_start_time"]), "stop_s": float(row["trial_stop_time"]),
-        "outcome": row["trial_type"], "block_id": block_of.get(row["trial_id"]),
-        "coverage": cover.get(row["trial_id"], {}),
-    } for row in rows]
+    trials = []
+    for row in (pipeline.trial.Trial & session_key).to_dicts():
+        run_number = run_of(row["trial_start_time"], run_rows)
+        if run_number is None:
+            continue
+        trials.append({
+            "trial_id": row["trial_id"], "start_s": float(row["trial_start_time"]),
+            "stop_s": float(row["trial_stop_time"]), "outcome": row["trial_type"], "run_number": run_number,
+            "block_id": block_of.get(row["trial_id"]), "coverage": cover.get(row["trial_id"], {}),
+        })
+    return trials
 
 
-def _events(blocks: BlockSet, session_key: dict) -> list[dict]:
+def _events(run_rows: list[dict], session_key: dict) -> list[dict]:
+    """The task events inside one of the file's runs, its ends included."""
+    from wl_preproc.events.runs import event_inside
     from wl_preproc.schema import pipeline
 
     attributes: dict = {}
@@ -228,7 +269,7 @@ def _events(blocks: BlockSet, session_key: dict) -> list[dict]:
     events = []
     for row in (pipeline.event.Event & session_key).to_dicts():
         time_s = float(row["event_start_time"])
-        if not blocks.contains_instant([time_s])[0]:
+        if not any(event_inside(time_s, run["start_s"], run["end_s"]) for run in run_rows):
             continue
         extra = attributes.get((row["event_type"], row["event_start_time"]), {})
         events.append({
@@ -240,14 +281,15 @@ def _events(blocks: BlockSet, session_key: dict) -> list[dict]:
     return events
 
 
-def _trial_notes(session_key: dict, blocks: BlockSet) -> list[str]:
-    """What the stored trials leave out, for this file's blocks (design spec
+def _trial_notes(session_key: dict, run_rows: list[dict]) -> list[str]:
+    """What the stored trials leave out, for this file's runs (design spec
     `2026-10-01-runs-and-trials-design.md` sections 3.2 and 3.4). Read from
     every strobed `TRIAL_NUMBER`, which `Event` keeps whether or not its trial
     was stored: a number strobed again after its first, and one above
     element-event's smallint `trial_id`."""
     import collections
 
+    from wl_preproc.events.runs import event_inside
     from wl_preproc.schema import pipeline
     from wl_preproc.schema.events import TRIAL_ID_MAX
 
@@ -256,7 +298,8 @@ def _trial_notes(session_key: dict, blocks: BlockSet) -> list[str]:
         for row in (pipeline.event.Event.Attribute & session_key
                     & {"event_type": "TRIAL_NUMBER", "attribute_name": "trial_id"}).to_dicts())
     counts = collections.Counter(number for _time_s, number in strobed)
-    inside = blocks.contains_instant([time_s for time_s, _number in strobed]) if strobed else []
+    inside = [any(event_inside(time_s, run["start_s"], run["end_s"]) for run in run_rows)
+              for time_s, _number in strobed]
     seen, repeated, too_large = set(), set(), 0
     for (_time_s, number), here in zip(strobed, inside, strict=True):
         first = number not in seen
@@ -273,9 +316,9 @@ def _trial_notes(session_key: dict, blocks: BlockSet) -> list[str]:
     return notes
 
 
-def _conditions(trials: list[dict], events: list[dict], blocks: list[dict], session_dir: Path,
+def _conditions(trials: list[dict], events: list[dict], runs: list[dict], session_dir: Path,
                 subject: str) -> tuple[list[dict], list[str]]:
-    """Each trial's condition and the settings that varied, and each block's
+    """Each trial's condition and the settings that varied, and each run's
     conditions and trial counts, from the rig's own record joined by trial
     number (design spec `2026-09-29-nwb-publishing-design.md` sections 2.1
     and 2.2). Returns the file's conditions and the notes on what did not
@@ -292,11 +335,11 @@ def _conditions(trials: list[dict], events: list[dict], blocks: list[dict], sess
     for position, trial in enumerate(trials):
         trial["condition"] = names[position]
         trial["settings"] = {key: values[position] for key, values in settings.items()}
-    for block in blocks:
-        inside = [trial for trial in trials if block["start_s"] <= trial["start_s"] < block["end_s"]]
+    for run in runs:
+        inside = [trial for trial in trials if trial["run_number"] == run["run_number"]]
         outcomes = collections.Counter(trial["outcome"] or "unknown" for trial in inside)
-        block["trials"] = {"total": len(inside), "by_outcome": dict(sorted(outcomes.items()))}
-        block["conditions"] = block_conditions(inside, matched, codes)
+        run["trials"] = {"total": len(inside), "by_outcome": dict(sorted(outcomes.items()))}
+        run["conditions"] = block_conditions(inside, matched, codes)
     return block_conditions(trials, matched, codes), notes
 
 
@@ -492,8 +535,8 @@ def _probe(serial: str, probe_type: str | None, report: dict | None, electrodes:
     }
 
 
-def _probes(key: dict, session_key: dict, block_rows: list[dict]) -> tuple[list[dict], list[str]]:
-    """Every probe the SpikeGLX segments under the file's blocks recorded, joined to
+def _probes(key: dict, session_key: dict, run_rows: list[dict]) -> tuple[list[dict], list[str]]:
+    """Every probe the SpikeGLX segments under the file's runs recorded, joined to
     wl.works' report of its insertion by serial, and the notes the file
     carries about what could not be placed or joined (design spec
     `2026-09-30-nwb-probes-design.md` sections 3 and 5).
@@ -516,7 +559,7 @@ def _probes(key: dict, session_key: dict, block_rows: list[dict]) -> tuple[list[
     segments = {
         row["segment_barcode"]: row
         for row in (core.Segment & session_key & {"system": "spikeglx"}).to_dicts()
-        if any(row["start_s"] < block["end_s"] and row["end_s"] > block["start_s"] for block in block_rows)
+        if any(row["start_s"] < run["end_s"] and row["end_s"] > run["start_s"] for run in run_rows)
     }
     parts = [part for part in (ephys.ProbeCensus.Probe & session_key).to_dicts(order_by=("segment_barcode", "stream"))
              if part["segment_barcode"] in segments]
@@ -537,7 +580,7 @@ def _probes(key: dict, session_key: dict, block_rows: list[dict]) -> tuple[list[
                     segments[part["segment_barcode"]]["file_path"])
         if len(maps) > 1:
             raise Refused(
-                f"probe {serial} recorded two active-site maps under this file's blocks of montage "
+                f"probe {serial} recorded two active-site maps under this file's runs of montage "
                 f"{key['montage_id']}, in "
                 + " and in ".join(", ".join(paths) for paths in maps.values())
                 + ": a bank change needs a new montage (parent spec section 8.3), and a file across it would be "
@@ -587,34 +630,42 @@ def gather(activation_key: dict) -> Gathered:
         raise Refused("no TimingProvenance row: the session has no session time yet")
     if provenance[0]["tier"] == "D":
         raise Refused("timing tier D: no trustworthy session time")
-    block_rows = _block_set(key, activation, session_key)
-    if not block_rows:
-        raise Refused(f"no blocks in the {activation['role']} activation's block set")
-    blocks = BlockSet.of(block_rows)
-    probes, probe_notes = _probes(key, session_key, block_rows)
+    run_rows = _run_set(key, session_key)
+    if not run_rows:
+        raise Refused(f"no runs in the {activation['role']} activation's run set")
+    extent = BlockSet.of(run_rows)
+    probes, probe_notes = _probes(key, session_key, run_rows)
+    sorted_runs: dict[str, list[int]] = {}
+    for row in (request.ActivationProbeRun & key).to_dicts(order_by=("probe_serial", "run_number")):
+        sorted_runs.setdefault(row["probe_serial"], []).append(row["run_number"])
+    for probe in probes:
+        probe["sorted_runs"] = sorted_runs.get(probe["serial"], [])
 
     session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
     clock = _reference_time(session_dir, key["session_datetime"])
     validity_idx, detection_idx = _paramsets()
-    eye = _eye(session_key, session_dir, blocks, validity_idx, detection_idx)
+    eye = _eye(session_key, session_dir, extent, validity_idx, detection_idx)
     no_eye_samples = eye is not None and eye.get("no_samples", False)
     if no_eye_samples:
         eye = None
 
     session_id = session_dir.name
-    task_types = sorted({row["task_type"] for row in block_rows})
+    tasks = sorted({_task_name(row["task_type"], row["task"]) for row in run_rows})
     description = (f"wl-preproc {activation['role']} NWB for session {session_id}, montage {key['montage_id']}: "
-                   f"blocks {', '.join(str(row['block_id']) for row in block_rows)} ({', '.join(task_types)}).")
+                   f"runs {', '.join(str(row['run_number']) for row in run_rows)} ({', '.join(tasks)}).")
     if eye is not None and eye["missing_eyes"]:
         description += " No calibration for the " + " and ".join(eye["missing_eyes"]) + " eye, so its gaze is absent."
     if no_eye_samples:
-        description += " The eye recording has no sample in these blocks, so the file has no eye data."
+        description += " The eye recording has no sample in these runs, so the file has no eye data."
     requested_by = (request.Request & {"idempotency_key": activation["request_key"]}).fetch1("requested_by")
-    block_out = _blocks(block_rows, session_key)
-    systems = sorted({system for row in block_out for system in row["coverage"]})
-    trials = _trials(blocks, session_key)
-    events = _events(blocks, session_key)
-    conditions, condition_notes = _conditions(trials, events, block_out, session_dir, key["subject"])
+    run_out = _runs(run_rows, session_key)
+    systems = sorted({system for row in run_out for system in row["coverage"]})
+    trials = _trials(run_rows, session_key)
+    events = _events(run_rows, session_key)
+    blocks = _blocks(run_rows, session_key)
+    for block in blocks:
+        block["n_trials"] = sum(1 for trial in trials if trial["block_id"] == block["block_number"])
+    conditions, condition_notes = _conditions(trials, events, run_out, session_dir, key["subject"])
     return Gathered(
         session={
             "identifier": identifier_for(key, session_id),
@@ -633,7 +684,8 @@ def gather(activation_key: dict) -> Gathered:
             "clock": clock,
         },
         systems=systems,
-        blocks=block_out,
+        runs=run_out,
+        blocks=blocks,
         trials=trials,
         events=events,
         timebase=_timebase(session_key, provenance[0], clock),
@@ -642,5 +694,5 @@ def gather(activation_key: dict) -> Gathered:
         condition_notes=condition_notes,
         probes=probes,
         probe_notes=probe_notes,
-        trial_notes=_trial_notes(session_key, blocks),
+        trial_notes=_trial_notes(session_key, run_rows),
     )
```

```diff
--- a/wl_preproc/nwb/describe.py
+++ b/wl_preproc/nwb/describe.py
@@ -24,11 +24,14 @@ def _commit() -> str | None:
     return commit if out.returncode == 0 and len(commit) == 40 else None
 
 
-def _task(value) -> dict:
-    """A block's task type, by code and name. Lab-defined codes (100 and
-    up) have no name here; their code stands for it."""
+def _task(value, rig_name: str | None = None) -> dict:
+    """A run's task, by code and name: the rig's name for it when its record
+    gives one, else the code's name. Lab-defined codes (100 and up) have no
+    name here; their code stands for it."""
     from wl_preproc.contracts.events import TaskTypeCode
 
+    if rig_name:
+        return {"code": str(value), "name": rig_name}
     try:
         name = TaskTypeCode(int(value)).name.lower()
     except (TypeError, ValueError):
@@ -84,19 +87,27 @@ def describe(data, *, status: str, n_critical: int, checksums: list[dict], built
             "target": probe["target"],
             "assignment": probe["assignment"],
             "area_from": probe["area_from"],
+            "sorted_runs": probe.get("sorted_runs", []),
         } for probe in data.probes],
-        "blocks": [{
-            "block_id": block["block_id"],
-            "works_block_id": block["works_block_id"],
-            "task": _task(block["task_type"]),
-            "asserted": {"start_s": block["start_s"], "stop_s": block["end_s"]},
-            "measured": None if block["measured_start_s"] is None else {
-                "start_s": block["measured_start_s"], "stop_s": block["measured_stop_s"]},
-            "trials": block["trials"],
+        "runs": [{
+            "run_number": run["run_number"],
+            "works_run_id": run["works_run_id"],
+            "task": _task(run["task_type"], run["task"]),
+            "measured": {"start_s": run["start_s"], "stop_s": run["end_s"]},
+            "closed": run["closed"],
+            "trials": run["trials"],
             "coverage": {system: {"coverage": verdict, "covered_s": covered}
-                         for system, (verdict, covered) in block["coverage"].items()},
-            "conditions": block["conditions"],
-        } for block in data.blocks],
+                         for system, (verdict, covered) in run["coverage"].items()},
+            "conditions": run["conditions"],
+            "blocks": [{
+                "block_number": block["block_number"],
+                "block_in_run": block["block_in_run"],
+                "block_type": block["block_type"],
+                "measured": {"start_s": block["start_s"], "stop_s": block["stop_s"]},
+                "closed": block["closed"],
+                "trials": block["n_trials"],
+            } for block in data.blocks if block["run_number"] == run["run_number"]],
+        } for run in data.runs],
         "quality": {
             "timing_tier": session["timing_tier"],
             "reference_source": session["clock"]["source"],
```

```diff
--- a/wl_preproc/nwb/intervals.py
+++ b/wl_preproc/nwb/intervals.py
@@ -1,5 +1,6 @@
-"""Blocks, trials and task events (design spec
-`2026-09-28-nwb-builder-design.md` section 3, `/intervals`)."""
+"""Runs, blocks, trials and task events (design spec
+`2026-09-28-nwb-builder-design.md` section 3, `/intervals`; runs since
+`2026-10-01-session-listing-and-run-requests-design.md` section 4)."""
 
 from __future__ import annotations
 
@@ -26,26 +27,54 @@ def _coverage_columns(rows: list[dict], systems: list[str]) -> list:
     return columns
 
 
-def add_blocks(nwb: NWBFile, blocks: list[dict], systems: list[str]) -> None:
-    """`/intervals/blocks`: the activation's blocks, their asserted
-    boundaries (`core.Block`) as start and stop, the measured ones
-    (`trial.Block`) beside them, and per-system coverage."""
+def add_runs(nwb: NWBFile, runs: list[dict], systems: list[str]) -> None:
+    """`/intervals/runs`: the activation's runs, measured from the recording's
+    `RUN_START` and `RUN_END`, with wl.works' id for each and per-system
+    coverage."""
+    rows = sorted(runs, key=lambda row: row["start_s"])
+    nwb.add_time_intervals(TimeIntervals(
+        name="runs",
+        description=("The activation's runs, measured from the recording: start and stop are the run's "
+                     "RUN_START and its RUN_END, or its last event when it faulted."),
+        columns=[
+            column("start_time", "Measured run start, session seconds.", [r["start_s"] for r in rows]),
+            column("stop_time", "Measured run stop, session seconds.", [r["end_s"] for r in rows]),
+            column("run_number", "The run's number in the session.", [r["run_number"] for r in rows]),
+            column("works_run_id", "wl.works' id for the run (its animal_session_run; '' if none).",
+                   [r["works_run_id"] or "" for r in rows]),
+            column("task_code", "The run's task code (0 until wl-xtasks allocates one).",
+                   [int(r["task_type"]) for r in rows]),
+            column("task", "The rig's name for the run's task ('' if its record names none).",
+                   [r["task"] or "" for r in rows]),
+            column("closed", "Whether a RUN_END arrived.", [bool(r["closed"]) for r in rows]),
+            *_coverage_columns(rows, systems),
+        ],
+    ))
+
+
+def add_blocks(nwb: NWBFile, blocks: list[dict]) -> None:
+    """`/intervals/blocks`: the measured blocks inside the activation's runs,
+    each with its run, its block type and whether it closed. None when the
+    runs hold no block."""
     rows = sorted(blocks, key=lambda row: row["start_s"])
+    if not rows:
+        return
     nwb.add_time_intervals(TimeIntervals(
         name="blocks",
-        description=("The activation's blocks: start and stop are the boundaries wl.works asserted "
-                     "(core.Block); measured_* are the boundaries decoded from the event codes."),
+        description=("The measured blocks inside the file's runs: consecutive trials under one block type, "
+                     "from BLOCK_START to BLOCK_END, or to the block's last event when its run faulted."),
         columns=[
-            column("start_time", "Asserted block start, session seconds.", [r["start_s"] for r in rows]),
-            column("stop_time", "Asserted block stop, session seconds.", [r["end_s"] for r in rows]),
-            column("block_id", "Block number.", [r["block_id"] for r in rows]),
-            column("task_type", "The block's task type.", [r["task_type"] for r in rows]),
-            column("works_block_id", "wl.works' own block id ('' until linked).", [r["works_block_id"] or "" for r in rows]),
-            column("measured_start_time", "Measured block start, session seconds (NaN if not decoded).",
-                   [np.nan if r["measured_start_s"] is None else r["measured_start_s"] for r in rows]),
-            column("measured_stop_time", "Measured block stop, session seconds (NaN if not decoded).",
-                   [np.nan if r["measured_stop_s"] is None else r["measured_stop_s"] for r in rows]),
-            *_coverage_columns(rows, systems),
+            column("start_time", "Measured block start, session seconds.", [r["start_s"] for r in rows]),
+            column("stop_time", "Measured block stop, session seconds.", [r["stop_s"] for r in rows]),
+            column("block_number", "The block's number in the session, as BLOCK_START strobed it.",
+                   [r["block_number"] for r in rows]),
+            column("run_number", "The run the block is inside.", [r["run_number"] for r in rows]),
+            column("block_in_run", "The block's order in its run, from 1.", [r["block_in_run"] for r in rows]),
+            column("block_type", "The rig's name for the block's type ('' if its record names none).",
+                   [r["block_type"] or "" for r in rows]),
+            column("closed", "1 when a BLOCK_END arrived, 0 when not, -1 when never recorded.",
+                   [-1 if r["closed"] is None else int(r["closed"]) for r in rows]),
+            column("n_trials", "The block's trials in this file.", [r["n_trials"] for r in rows]),
         ],
     ))
 
@@ -76,6 +105,7 @@ def add_trials(nwb: NWBFile, trials: list[dict], systems: list[str]) -> None:
             column("outcome", "correct, error, abort, fixation_break or no_response.", [r["outcome"] or "" for r in rows]),
             column("block_id", "The measured block the trial belongs to (-1 if none).",
                    [-1 if r["block_id"] is None else r["block_id"] for r in rows]),
+            column("run_number", "The run the trial belongs to.", [r["run_number"] for r in rows]),
             column("condition", ("The condition the trial ran under: its name in the rig's record "
                                  "(xcon/trials.jsonl), else the CONDITION number sent inside it, else ''."),
                    [r.get("condition", "") for r in rows]),
```

```diff
--- a/wl_preproc/nwb/build.py
+++ b/wl_preproc/nwb/build.py
@@ -12,7 +12,7 @@ from wl_preproc.nwb.describe import describe
 from wl_preproc.nwb.eye import add_eye_series, add_eye_tables
 from wl_preproc.nwb.eye_events import add_agreement, add_detections, add_sources
 from wl_preproc.nwb.gather import Refused, gather, readiness
-from wl_preproc.nwb.intervals import add_blocks, add_conditions, add_task_events, add_trials
+from wl_preproc.nwb.intervals import add_blocks, add_conditions, add_runs, add_task_events, add_trials
 from wl_preproc.nwb.probes import add_probes
 from wl_preproc.nwb.session import new_file
 from wl_preproc.nwb.timebase import add_timebase
@@ -64,7 +64,8 @@ def build(activation_key: dict, nwb_root: Path) -> BuildResult:
     except Refused as refusal:
         return BuildResult(status="refused", reason=str(refusal))
     nwb = new_file(data.session)
-    add_blocks(nwb, data.blocks, data.systems)
+    add_runs(nwb, data.runs, data.systems)
+    add_blocks(nwb, data.blocks)
     add_trials(nwb, data.trials, data.systems)
     add_conditions(nwb, data.conditions)
     add_task_events(nwb, data.events)
```

```diff
--- a/wl_preproc/synth/spikeglx.py
+++ b/wl_preproc/synth/spikeglx.py
@@ -406,9 +406,18 @@ def write_nidq(
     # timeline.py's own spacing rule always does. Barcodes never need this;
     # code words do.
     session_span_s = code_word_span_s(recipe, truth, drift_ppm, STROBE_WIDTH_S)
-    n_samples = (
-        int((session_span_s + SPIKEGLX_PRE_ROLL_S) * NIDQ_SAMPLE_RATE_HZ)
-        + SAMPLE_COUNT_ROUNDING_SLACK
+    strobes = [
+        (int(round((apply_drift(time_s, drift_ppm) + SPIKEGLX_PRE_ROLL_S) * NIDQ_SAMPLE_RATE_HZ)), word)
+        for time_s, word in truth.code_words
+    ]
+    # One sample past the last strobe, low: the reader latches a word on its
+    # strobe's falling edge, and a span that ends where the last pulse ends
+    # leaves that pulse high to the file's end -- a word never latched. The
+    # span above kept CI_RECIPE's SESSION_END only by rounding, and a session
+    # whose blocks sit in runs lost it.
+    n_samples = max(
+        int((session_span_s + SPIKEGLX_PRE_ROLL_S) * NIDQ_SAMPLE_RATE_HZ) + SAMPLE_COUNT_ROUNDING_SLACK,
+        max((sample + strobe_width for sample, _ in strobes), default=0) + 1,
     )
     control = np.zeros(n_samples, dtype=np.uint16)  # word 0: barcode + strobe
     data = np.zeros(n_samples, dtype=np.uint16)  # word 1: the 16 data lines
@@ -429,10 +438,7 @@ def write_nidq(
     # strobes merge into one long high with no falling edge between them and
     # the words become uncountable. Phase 1b shipped exactly that defect: a
     # 1 ms pulse at 1 ms spacing rendered 31 words as 5 countable edges.
-    for time_s, word in truth.code_words:
-        sample = int(
-            round((apply_drift(time_s, drift_ppm) + SPIKEGLX_PRE_ROLL_S) * NIDQ_SAMPLE_RATE_HZ)
-        )
+    for sample, word in strobes:
         if sample + strobe_width > n_samples:
             continue
         control[sample : sample + strobe_width] |= 1 << NIDQ_CODE_STROBE_XD_LINE
```

Then re-export the contracts, which changes `nwb_description.json` by:

```diff
--- a/docs/schemas/nwb_description.json
+++ b/docs/schemas/nwb_description.json
@@ -19,71 +19,6 @@
       "title": "Behaviour",
       "type": "object"
     },
-    "Block": {
-      "additionalProperties": false,
-      "properties": {
-        "asserted": {
-          "$ref": "#/$defs/Interval"
-        },
-        "block_id": {
-          "title": "Block Id",
-          "type": "integer"
-        },
-        "conditions": {
-          "items": {
-            "$ref": "#/$defs/Condition"
-          },
-          "title": "Conditions",
-          "type": "array"
-        },
-        "coverage": {
-          "additionalProperties": {
-            "$ref": "#/$defs/Coverage"
-          },
-          "title": "Coverage",
-          "type": "object"
-        },
-        "measured": {
-          "anyOf": [
-            {
-              "$ref": "#/$defs/Interval"
-            },
-            {
-              "type": "null"
-            }
-          ]
-        },
-        "task": {
-          "$ref": "#/$defs/Task"
-        },
-        "trials": {
-          "$ref": "#/$defs/TrialCounts"
-        },
-        "works_block_id": {
-          "anyOf": [
-            {
-              "type": "string"
-            },
-            {
-              "type": "null"
-            }
-          ],
-          "title": "Works Block Id"
-        }
-      },
-      "required": [
-        "block_id",
-        "works_block_id",
-        "task",
-        "asserted",
-        "measured",
-        "trials",
-        "coverage",
-        "conditions"
-      ],
-      "title": "Block",
-      "type": "object"
-    },
     "Checksums": {
       "additionalProperties": false,
       "properties": {
@@ -109,7 +44,7 @@
     },
     "Condition": {
       "additionalProperties": false,
-      "description": "One condition that ran in a block: by its name in the rig's record,\nwith the settings constant across its trials and a summary of those that\nvaried; or, without that record, by the stream's CONDITION number with\nsettings unknown (null).",
+      "description": "One condition that ran in a run: by its name in the rig's record,\nwith the settings constant across its trials and a summary of those that\nvaried; or, without that record, by the stream's CONDITION number with\nsettings unknown (null).",
       "properties": {
         "code": {
           "anyOf": [
@@ -594,6 +529,14 @@
           "title": "Serial",
           "type": "string"
         },
+        "sorted_runs": {
+          "default": [],
+          "items": {
+            "type": "integer"
+          },
+          "title": "Sorted Runs",
+          "type": "array"
+        },
         "target": {
           "anyOf": [
             {
@@ -698,6 +641,127 @@
       "title": "Quality",
       "type": "object"
     },
+    "Run": {
+      "additionalProperties": false,
+      "description": "One run the file holds, measured from the recording. `works_run_id`\njoins it to wl.works' `animal_session_run`.",
+      "properties": {
+        "blocks": {
+          "items": {
+            "$ref": "#/$defs/RunBlock"
+          },
+          "title": "Blocks",
+          "type": "array"
+        },
+        "closed": {
+          "title": "Closed",
+          "type": "boolean"
+        },
+        "conditions": {
+          "items": {
+            "$ref": "#/$defs/Condition"
+          },
+          "title": "Conditions",
+          "type": "array"
+        },
+        "coverage": {
+          "additionalProperties": {
+            "$ref": "#/$defs/Coverage"
+          },
+          "title": "Coverage",
+          "type": "object"
+        },
+        "measured": {
+          "$ref": "#/$defs/Interval"
+        },
+        "run_number": {
+          "title": "Run Number",
+          "type": "integer"
+        },
+        "task": {
+          "$ref": "#/$defs/Task"
+        },
+        "trials": {
+          "$ref": "#/$defs/TrialCounts"
+        },
+        "works_run_id": {
+          "anyOf": [
+            {
+              "type": "string"
+            },
+            {
+              "type": "null"
+            }
+          ],
+          "title": "Works Run Id"
+        }
+      },
+      "required": [
+        "run_number",
+        "works_run_id",
+        "task",
+        "measured",
+        "closed",
+        "trials",
+        "coverage",
+        "conditions",
+        "blocks"
+      ],
+      "title": "Run",
+      "type": "object"
+    },
+    "RunBlock": {
+      "additionalProperties": false,
+      "description": "A measured block inside the run: consecutive trials under one block\ntype. `block_number` is its number in the session, as `BLOCK_START`\nstrobes it; `closed` is null when never recorded.",
+      "properties": {
+        "block_in_run": {
+          "title": "Block In Run",
+          "type": "integer"
+        },
+        "block_number": {
+          "title": "Block Number",
+          "type": "integer"
+        },
+        "block_type": {
+          "anyOf": [
+            {
+              "type": "string"
+            },
+            {
+              "type": "null"
+            }
+          ],
+          "title": "Block Type"
+        },
+        "closed": {
+          "anyOf": [
+            {
+              "type": "boolean"
+            },
+            {
+              "type": "null"
+            }
+          ],
+          "title": "Closed"
+        },
+        "measured": {
+          "$ref": "#/$defs/Interval"
+        },
+        "trials": {
+          "title": "Trials",
+          "type": "integer"
+        }
+      },
+      "required": [
+        "block_number",
+        "block_in_run",
+        "block_type",
+        "measured",
+        "closed",
+        "trials"
+      ],
+      "title": "RunBlock",
+      "type": "object"
+    },
     "SubjectInfo": {
       "additionalProperties": false,
       "properties": {
@@ -798,13 +862,6 @@
   },
   "additionalProperties": false,
   "properties": {
-    "blocks": {
-      "items": {
-        "$ref": "#/$defs/Block"
-      },
-      "title": "Blocks",
-      "type": "array"
-    },
     "checksums": {
       "$ref": "#/$defs/Checksums"
     },
@@ -838,9 +895,16 @@
     "quality": {
       "$ref": "#/$defs/Quality"
     },
+    "runs": {
+      "items": {
+        "$ref": "#/$defs/Run"
+      },
+      "title": "Runs",
+      "type": "array"
+    },
     "schema_version": {
-      "const": 2,
-      "default": 2,
+      "const": 3,
+      "default": 3,
       "title": "Schema Version",
       "type": "integer"
     },
@@ -852,7 +916,7 @@
     "identity",
     "subject",
     "data_types",
-    "blocks",
+    "runs",
     "quality",
     "notes",
     "checksums"
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 217 passed, 1 skipped.

Then everything the file and the generator feed: `.venv/bin/python -m pytest tests/synth tests/nwb tests/listing tests/contracts tests/cli/test_schemas_export.py tests/schema/test_nwb_build.py tests/schema/test_nwb_probes.py tests/schema/test_nwb_trials.py tests/schema/test_detect_populate.py tests/schema/test_spikeglx_restart.py tests/schema/test_session_listing.py tests/schema/test_request.py -q -p no:cacheprovider`. Expected: 635 passed, 2 skipped.

- [ ] **Step 5: Mutation checks.**
  - T4a (`events/runs.py`): `starts_inside` compares with the run's bounds unrounded [`test_what_starts_just_after_its_run_is_in_it_however_it_was_stored`].
  - T4b: `event_inside` compares with the run's bounds unrounded [`test_what_starts_just_after_its_run_is_in_it_however_it_was_stored`].
  - T4c (`nwb/gather.py::_events`): an event outside every run is kept [`test_a_derivative_holds_only_its_own_run`].
  - T4d (`nwb/gather.py`): readiness stops waiting on `RunCoverage` [`test_the_stage_waits_for_upstream_keys_not_yet_computed`].
  - T4e (`nwb/gather.py`): every probe's `sorted_runs` is `[]` [`test_the_description_lists_each_probe_and_says_what_it_could_not_place_or_join`].
  - T4f (`nwb/intervals.py::add_blocks`): an empty block list still writes the table [`test_runs_that_hold_no_block_add_no_block_table`].
  - T4g (`synth/spikeglx.py`): the buffer ends where the last strobe ends (`+ 1` dropped) [`test_the_last_code_words_strobe_falls_before_the_file_ends[True]`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/events/runs.py wl_preproc/listing/entry.py wl_preproc/contracts/nwb_description.py wl_preproc/nwb/gather.py wl_preproc/nwb/describe.py wl_preproc/nwb/intervals.py wl_preproc/nwb/build.py wl_preproc/synth/spikeglx.py docs/schemas/nwb_description.json tests/synth/test_spikeglx.py tests/nwb/test_helpers.py tests/nwb/test_describe.py tests/nwb/test_writers.py tests/cli/test_schemas_export.py tests/schema/test_detect_populate.py tests/schema/test_nwb_build.py tests/schema/test_nwb_probes.py tests/schema/test_nwb_trials.py tests/schema/test_request.py
git commit -m "feat(nwb): a file is built from its activation's runs -- description v3

The NWB file's intervals hold a runs table (run number, wl.works' id, task,
closed, coverage), the measured blocks with their run and place in it, and
trials carrying their run. The description's schema is 3: a list of runs,
each with its blocks, and each probe's sorted runs. Readiness waits on
RunCoverage. Run containment for blocks, trials and events is shared in
events/runs.py and compares in each table's stored precision.

The SpikeGLX generator now ends the NI buffer one low sample past the last
strobe: a session whose blocks sit in runs ended on a strobe still high,
lost SESSION_END, and read tier D.

<trailer lines>"
```

---

### Task 5: A request names runs only — the block fields are retired

**Files:**
- Modify: `wl_preproc/contracts/protocol.py`, `wl_preproc/responder/jobs.py`, `wl_preproc/schema/request.py`, `wl_preproc/schema/paramset.py`, `docs/schemas/job_request.json` (re-exported)
- Test: `tests/contracts/test_protocol.py`, `tests/responder/test_jobs.py`, `tests/responder/test_http.py`, `tests/schema/test_probe_linking.py`, `tests/schema/test_request.py`

**Interfaces — consumes:** Task 3's `_check_runs` and `_check_probe_runs`; Task 4's builder, which reads no block set.

**Interfaces — produces:**
- `MetadataBundle.blocks`: optional, `default=[]`, exported `maxItems: 0` and `deprecated: true`; a non-empty one raises `PydanticCustomError("retired_field", "metadata.blocks is retired: ... metadata.runs ...")`.
- `accept()` refuses a non-empty `selection.block_ids` naming `selection.run_numbers`, checks every request's runs, and writes no `core.Block`.
- `request.selection_hash(task_type: str, run_numbers) -> str`, over `{"task_type", "run_numbers": sorted(set(...))}`; `submit(..., requested_by=None, run_numbers=(), probe_runs=None)`; `submit_replacement(..., *, supersedes_activation_id, run_numbers=(), probe_runs=None)`; `submit_derivative(idempotency_key, task_type, origin, selection, run_numbers, payload, requested_by=None)`, which needs at least one run number. None writes `ActivationBlock`.

**Why the contract keeps the field.** Removed, a request still carrying it would be refused as an unknown field, with nothing saying what replaced it (spec §3.1). **Why a custom error, not `ValueError`:** pydantic keeps a raised `ValueError` itself in the error's `ctx`, the `422` body (`responder/handler.py::_error_body`) cannot serialise it, and the refusal went out as a `500` (the HTTP test below).

**Why every test landing a session now measures its runs.** A canonical holds every measured run of its montage and a session with none cannot be requested (spec §3.2). The probe-linking tests run the event stage before reporting: the earliest a report can now arrive, still before the census.

- [ ] **Step 1: Write the failing tests.** Apply these diffs:

```diff
--- a/tests/contracts/test_protocol.py
+++ b/tests/contracts/test_protocol.py
@@ -113,7 +113,7 @@ def test_job_request_carries_the_metadata_bundle():
         parameters={"clustering_paramset": "ks4_default"},
         idempotency_key="a1b2c3",
         metadata=MetadataBundle(
-            blocks=[{"block_id": 1, "task_type": "rf_map"}],
+            runs=[{"run_number": 1, "start_s": 0.0, "end_s": 1800.0, "works_run_id": "wr-1"}],
             montage_boundaries=[{"montage_id": 1, "start_s": 0.0, "end_s": 3600.0}],
             probes=[{"serial": "NP-1234", "insertion_number": 1}],
             experimenter="jw",
@@ -337,3 +337,20 @@ def test_a_run_entry_holds_a_measured_runs_number_times_and_wl_works_id(entry):
     RunEntry.model_validate({"run_number": 1, "start_s": 0.0, "end_s": 1.0, "works_run_id": "w"})
     with pytest.raises(ValidationError):
         RunEntry.model_validate(entry)
+
+
+def test_metadata_blocks_are_refused_naming_metadata_runs():
+    """Retired (design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    section 3.1): a request asserts runs, and one still sending blocks is told
+    what replaced them, not that the field is unknown. An empty list, which
+    every request sent while the field was required, says nothing."""
+    import re
+
+    with pytest.raises(ValidationError, match=re.escape("metadata.blocks is retired: a request asserts its runs "
+                                                        "in metadata.runs")):
+        MetadataBundle.model_validate(_bundle(blocks=[{"block_id": 1, "task_type": "rf_map"}]))
+    assert MetadataBundle.model_validate(_bundle()).blocks == []
+    assert MetadataBundle.model_validate({k: v for k, v in _bundle().items() if k != "blocks"}).runs == []
+    schema = MetadataBundle.model_json_schema()
+    blocks = schema["properties"]["blocks"]
+    assert "blocks" not in schema["required"] and (blocks["maxItems"], blocks["deprecated"]) == (0, True)
```

```diff
--- a/tests/responder/test_jobs.py
+++ b/tests/responder/test_jobs.py
@@ -10,24 +10,39 @@ import pytest
 
 from wl_preproc.contracts.protocol import JobRequest, MetadataBundle
 
+# The runs the event stage measured for every landed session here (design
+# spec `2026-10-01-session-listing-and-run-requests-design.md` section 3):
+# run 3 lies outside montage 0's window [0, 12), inside montage 1's [12, 24).
+_RUNS = [(1, 0.0, 4.0), (2, 5.0, 11.0), (3, 13.0, 16.0)]
+_SERIAL = "19011110001"
+
+
+def _asserted(runs=_RUNS, ids: dict | None = None) -> list[dict]:
+    """wl.works' copy of each run, as `GET /sessions` listed it, and its id."""
+    return [{"run_number": number, "start_s": start, "end_s": stop,
+             "works_run_id": (ids or {}).get(number, f"wr-{number}")} for number, start, stop in runs]
+
 
 @pytest.fixture
 def landed_session(dj_conn, prefix):
     """A `(subject, session_datetime)` with Lab/Subject/Session already on
-    file -- the state `ingest/landing.py`'s `land_session` would already have
-    produced before any job request naming this session could arrive.
+    file, and its runs measured -- the state `ingest/landing.py`'s
+    `land_session` and the event stage would already have produced before
+    any job request naming this session could arrive. `runs=()` leaves the
+    session landed and its runs not yet read.
 
     `accept()` (design spec section 6.1, steps 1-4) is scoped to
-    `Montage`/`Block`/`Request`/`Activation`; it is not what creates `Session`
-    or its `Subject` parent. Mirrors `tests/schema/test_request.py`'s own
-    `selection` fixture for the identical reason, stated there.
+    `Montage`/`RunAssertion`/`Request`/`Activation`; it is not what creates
+    `Session`, its `Subject` parent or its `Run` rows. Mirrors
+    `tests/schema/test_request.py`'s own `selection` fixture for the identical
+    reason, stated there.
     """
-    from wl_preproc.schema import pipeline
+    from wl_preproc.schema import core, pipeline
     from wl_preproc.schema import request as schema_request
 
     schema_request.activate(prefix=prefix)
 
-    def _land(subject: str, session_datetime: datetime.datetime) -> dict:
+    def _land(subject: str, session_datetime: datetime.datetime, runs=_RUNS) -> dict:
         pipeline.lab.Lab.insert1(
             {"lab": "wl", "lab_name": "W", "address": "y", "time_zone": "UTC"},
             skip_duplicates=True,
@@ -43,6 +58,10 @@ def landed_session(dj_conn, prefix):
         )
         key = {"subject": subject, "session_datetime": session_datetime}
         pipeline.Session.insert1(key, skip_duplicates=True)
+        if runs:
+            core.Run.insert([{**key, "run_number": number, "task_type": 0, "run_start_time": start,
+                              "run_stop_time": stop, "closed": 1} for number, start, stop in runs],
+                            skip_duplicates=True)
         return key
 
     return _land
@@ -55,29 +74,32 @@ def _request(
     idempotency_key: str,
     montage_id: int = 0,
     montage_boundaries: list[dict] | None = None,
-    blocks: list[dict] | None = None,
-    block_ids: list[int] | None = None,
+    runs=_RUNS,
+    run_numbers: list[int] | None = None,
     domain: str = "neural",
     experimenter: str = "jw",
     subject_details: dict | None = None,
     probes: list[dict] | None = None,
 ) -> JobRequest:
     """A `JobRequest` naming `(montage_id, session_datetime)` in its
-    selection, with `block_ids` present only when the caller supplies one --
-    an absent key and an empty list are both legal ways to ask for a
-    canonical activation, and callers exercising that distinction build the
-    dict directly rather than through this helper.
+    selection and asserting `runs`, with `run_numbers` present only when the
+    caller supplies one -- an absent key and an empty list are both legal
+    ways to ask for a canonical activation, and callers exercising that
+    distinction build the dict directly rather than through this helper. A
+    canonical's probes each sort no run unless a test says otherwise.
     """
     selection: dict = {"session_datetime": session_datetime, "montage_id": montage_id}
-    if block_ids is not None:
-        selection["block_ids"] = block_ids
+    if run_numbers is not None:
+        selection["run_numbers"] = run_numbers
+    elif probes:
+        selection["probe_runs"] = {probe["serial"]: [] for probe in probes}
     return JobRequest(
         domain=domain,
         selection=selection,
         parameters={},
         idempotency_key=idempotency_key,
         metadata=MetadataBundle(
-            blocks=blocks or [],
+            runs=_asserted(runs),
             montage_boundaries=montage_boundaries or [],
             probes=probes or [],
             experimenter=experimenter,
@@ -121,52 +143,13 @@ def test_accept_creates_montage_rows_from_metadata(landed_session, prefix):
     assert m1["start_s"] == pytest.approx(12.0)
     assert m1["end_s"] == pytest.approx(24.0)
     assert key["montage_id"] == 1
-    assert key["activation_id"] == 0  # no block_ids -> canonical
-
-
-def test_accept_creates_block_rows_with_works_block_id(landed_session, prefix):
-    from wl_preproc.responder.jobs import accept
-    from wl_preproc.schema import core
-
-    subject = "jbblk01"
-    naive_dt = datetime.datetime(2027, 5, 3, 9, 0)
-    landed_session(subject, naive_dt)
-    job = _request(
-        subject=subject,
-        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
-        montage_id=0,
-        idempotency_key="jbblk01-k1",
-        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
-        blocks=[
-            {
-                "block_id": 1,
-                "task_type": "rf_map",
-                "start_s": 0.0,
-                "end_s": 4.0,
-                "works_block_id": "wb-1",
-            },
-            {"block_id": 2, "task_type": "attention", "start_s": 4.0, "end_s": 12.0},
-        ],
-    )
-
-    accept(job, prefix=prefix)
-
-    session_key = {"subject": subject, "session_datetime": naive_dt}
-    with_id = (core.Block & {**session_key, "block_id": 1}).fetch1()
-    without_id = (core.Block & {**session_key, "block_id": 2}).fetch1()
-    assert with_id["works_block_id"] == "wb-1"
-    assert with_id["task_type"] == "rf_map"
-    # a block dict that omits works_block_id leaves the column at its null
-    # default, exactly like a direct core.Block insert would (test_core.py's
-    # own test_a_block_round_trips_with_and_without_works_block_id)
-    assert without_id["works_block_id"] is None
-    assert without_id["task_type"] == "attention"
+    assert key["activation_id"] == 0  # no run_numbers -> canonical
 
 
 def test_accept_is_idempotent_on_the_same_key(landed_session, prefix):
     """A resubmission of the identical `JobRequest` (the same idempotency
     key, the same everything) must return the same Activation and must not
-    duplicate the Montage/Block rows or the Request row.
+    duplicate the Montage/RunAssertion rows or the Request row.
 
     The selection's `session_datetime` is timezone-aware here on purpose,
     not merely for realism: DataJoint's blob codec drops a datetime's tzinfo
@@ -195,15 +178,6 @@ def test_accept_is_idempotent_on_the_same_key(landed_session, prefix):
         montage_id=0,
         idempotency_key="jbidm01-k1",
         montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
-        blocks=[
-            {
-                "block_id": 1,
-                "task_type": "rf_map",
-                "start_s": 0.0,
-                "end_s": 4.0,
-                "works_block_id": "wb-1",
-            }
-        ],
     )
 
     first = accept(job, prefix=prefix)
@@ -213,10 +187,10 @@ def test_accept_is_idempotent_on_the_same_key(landed_session, prefix):
     assert len(schema_request.Request & {"idempotency_key": "jbidm01-k1"}) == 1
     session_key = {"subject": subject, "session_datetime": naive_dt}
     assert len(core.Montage & {**session_key, "montage_id": 0}) == 1
-    assert len(core.Block & {**session_key, "block_id": 1}) == 1
+    assert len(core.RunAssertion & {**session_key, "run_number": 1}) == 1
 
 
-def test_no_block_ids_is_canonical_and_some_block_ids_is_derivative(landed_session, prefix):
+def test_no_run_numbers_is_canonical_and_some_run_numbers_is_derivative(landed_session, prefix):
     from wl_preproc.responder.jobs import accept
     from wl_preproc.schema import request as schema_request
 
@@ -224,22 +198,6 @@ def test_no_block_ids_is_canonical_and_some_block_ids_is_derivative(landed_sessi
     naive_dt = datetime.datetime(2027, 5, 5, 9, 0)
     landed_session(subject, naive_dt)
     boundaries = [{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}]
-    blocks = [
-        {
-            "block_id": 1,
-            "task_type": "neural",
-            "start_s": 0.0,
-            "end_s": 4.0,
-            "works_block_id": "wb-1",
-        },
-        {
-            "block_id": 2,
-            "task_type": "neural",
-            "start_s": 4.0,
-            "end_s": 8.0,
-            "works_block_id": "wb-2",
-        },
-    ]
 
     canonical_job = _request(
         subject=subject,
@@ -247,7 +205,6 @@ def test_no_block_ids_is_canonical_and_some_block_ids_is_derivative(landed_sessi
         montage_id=0,
         idempotency_key="jbcd001-k1",
         montage_boundaries=boundaries,
-        blocks=blocks,
     )
     derivative_job = _request(
         subject=subject,
@@ -255,8 +212,7 @@ def test_no_block_ids_is_canonical_and_some_block_ids_is_derivative(landed_sessi
         montage_id=0,
         idempotency_key="jbcd001-k2",
         montage_boundaries=boundaries,
-        blocks=blocks,
-        block_ids=[1, 2],
+        run_numbers=[1, 2],
     )
 
     canonical_key = accept(canonical_job, prefix=prefix)
@@ -266,18 +222,16 @@ def test_no_block_ids_is_canonical_and_some_block_ids_is_derivative(landed_sessi
     assert (schema_request.Activation & canonical_key).fetch1("role") == "canonical"
     assert derivative_key["activation_id"] != 0
     assert (schema_request.Activation & derivative_key).fetch1("role") == "derivative"
-    assert len(schema_request.ActivationBlock & derivative_key) == 2
+    assert len(schema_request.ActivationRun & derivative_key) == 2
 
 
-def test_an_existing_montage_and_block_survive_a_request_naming_different_boundaries(
-    landed_session, prefix
-):
-    """Beyond the brief: wl.works owns `Montage`/`Block`. A second request
-    carrying different boundaries for a montage or block already on file is
-    wl.works correcting its own record, and that correction is their call to
-    make explicitly -- it is not this pipeline's to infer from whichever
-    payload happened to arrive most recently. So the second request's
-    boundaries are silently ignored, not applied.
+def test_an_existing_montage_survives_a_request_naming_different_boundaries(landed_session, prefix):
+    """Beyond the brief: wl.works owns `Montage`. A second request carrying
+    different boundaries for a montage already on file is wl.works
+    correcting its own record, and that correction is their call to make
+    explicitly -- it is not this pipeline's to infer from whichever payload
+    happened to arrive most recently. So the second request's boundaries are
+    silently ignored, not applied.
     """
     from wl_preproc.responder.jobs import accept
     from wl_preproc.schema import core
@@ -292,134 +246,51 @@ def test_an_existing_montage_and_block_survive_a_request_naming_different_bounda
         montage_id=0,
         idempotency_key="jbkeep1-k1",
         montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
-        blocks=[
-            {
-                "block_id": 1,
-                "task_type": "rf_map",
-                "start_s": 0.0,
-                "end_s": 4.0,
-                "works_block_id": "wb-1",
-            }
-        ],
     )
     accept(first_job, prefix=prefix)
 
     # A second request, under a DIFFERENT idempotency key (a distinct ask,
     # not a retry of the first), naming DIFFERENT boundaries for the SAME
-    # montage_id and block_id.
+    # montage_id.
     correction_job = _request(
         subject=subject,
         session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
         montage_id=0,
         idempotency_key="jbkeep1-k2",
         montage_boundaries=[{"montage_id": 0, "start_s": 100.0, "end_s": 200.0}],
-        blocks=[
-            {
-                "block_id": 1,
-                "task_type": "attention",
-                "start_s": 100.0,
-                "end_s": 150.0,
-                "works_block_id": "wb-CORRECTED",
-            }
-        ],
     )
     accept(correction_job, prefix=prefix)
 
     session_key = {"subject": subject, "session_datetime": naive_dt}
     montage_row = (core.Montage & {**session_key, "montage_id": 0}).fetch1()
-    block_row = (core.Block & {**session_key, "block_id": 1}).fetch1()
 
     assert montage_row["start_s"] == pytest.approx(0.0)
     assert montage_row["end_s"] == pytest.approx(12.0)
-    assert block_row["task_type"] == "rf_map"
-    assert block_row["start_s"] == pytest.approx(0.0)
-    assert block_row["end_s"] == pytest.approx(4.0)
-    assert block_row["works_block_id"] == "wb-1"
-
-
-def test_accept_rejects_a_block_outside_its_montages_window(landed_session, prefix):
-    """`ActivationBlock`'s own comment: the responder is this window's first
-    writer and owns enforcing it. `submit_derivative` itself accepts a block
-    at [20.0, 24.0) against a montage of [0.0, 12.0) -- verified -- so this is
-    `accept()`'s own check, not inherited from the schema layer."""
-    from wl_preproc.responder.jobs import accept
-    from wl_preproc.schema import request as schema_request
-
-    subject = "jbwin01"
-    naive_dt = datetime.datetime(2027, 5, 9, 9, 0)
-    landed_session(subject, naive_dt)
-    job = _request(
-        subject=subject,
-        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
-        montage_id=0,
-        idempotency_key="jbwin01-k1",
-        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
-        blocks=[
-            {
-                "block_id": 9,
-                "task_type": "neural",
-                "start_s": 20.0,
-                "end_s": 24.0,
-                "works_block_id": "wb-9",
-            }
-        ],
-        block_ids=[9],
-    )
-
-    with pytest.raises(ValueError, match="block 9"):
-        accept(job, prefix=prefix)
-
-    assert len(schema_request.Request & {"idempotency_key": "jbwin01-k1"}) == 0
 
 
 def test_accept_treats_the_montage_window_as_half_open(landed_session, prefix):
-    """[start_s, end_s) -- a block ending exactly at the montage's end is
-    still fully covered; a block starting exactly where the montage ends is
-    not covered at all. Boundary conditions are exactly where an off-by-one
-    in the comparison would hide."""
+    """[start_s, end_s) -- a run starting inside the montage is the file's
+    even when it ends exactly at the montage's end; a run starting exactly
+    where the montage ends is not the file's at all. Boundary conditions are
+    exactly where an off-by-one in the comparison would hide."""
     from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema import request as schema_request
 
     subject = "jbedg01"
     naive_dt = datetime.datetime(2027, 5, 10, 9, 0)
-    landed_session(subject, naive_dt)
+    runs = [(1, 0.0, 4.0), (2, 8.0, 12.0), (3, 12.0, 16.0)]
+    landed_session(subject, naive_dt, runs=runs)
+    boundaries = [{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}]
 
-    ok_job = _request(
-        subject=subject,
-        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
-        montage_id=0,
-        idempotency_key="jbedg01-k1",
-        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
-        blocks=[
-            {
-                "block_id": 1,
-                "task_type": "neural",
-                "start_s": 8.0,
-                "end_s": 12.0,
-                "works_block_id": "wb-1",
-            }
-        ],
-        block_ids=[1],
-    )
-    accept(ok_job, prefix=prefix)  # must not raise -- [8, 12) is fully within [0, 12)
+    canonical = accept(_request(subject=subject, session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
+                                idempotency_key="jbedg01-k1", montage_boundaries=boundaries, runs=runs),
+                       prefix=prefix)
+    assert sorted(int(n) for n in (schema_request.ActivationRun & canonical).to_arrays("run_number")) == [1, 2]
 
-    touching_job = _request(
-        subject=subject,
-        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
-        montage_id=0,
-        idempotency_key="jbedg01-k2",
-        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
-        blocks=[
-            {
-                "block_id": 2,
-                "task_type": "neural",
-                "start_s": 12.0,
-                "end_s": 16.0,
-                "works_block_id": "wb-2",
-            }
-        ],
-        block_ids=[2],
-    )
-    with pytest.raises(ValueError, match="block 2"):
+    touching_job = _request(subject=subject, session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
+                            idempotency_key="jbedg01-k2", montage_boundaries=boundaries, runs=runs,
+                            run_numbers=[3])
+    with pytest.raises(ValueError, match=r"run\(s\) \[3\] outside montage 0's window"):
         accept(touching_job, prefix=prefix)
 
 
@@ -434,7 +305,7 @@ def test_accept_rejects_a_selection_missing_a_required_key(landed_session, prefi
         parameters={},
         idempotency_key="jbkey01-k1",
         metadata=MetadataBundle(
-            blocks=[],
+            runs=_asserted(),
             montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
             probes=[],
             experimenter="jw",
@@ -567,18 +438,17 @@ def test_session_datetime_is_normalised_through_to_naive_utc(landed_session, pre
 # territory the original 12 tests above could not reach. ---
 
 
-def test_a_rejected_request_leaves_no_montage_or_block_row_and_a_correction_then_succeeds(
+def test_a_rejected_request_leaves_no_montage_or_run_assertion_and_a_correction_then_succeeds(
     landed_session, prefix
 ):
-    """C1: validate before writing, not after. The first draft inserted
-    Montage/Block and only then checked the window, so a rejected request
-    permanently planted the very Block row that caused its own rejection --
+    """C1: validate before writing, not after. The first draft inserted the
+    request's rows and only then checked the window, so a rejected request
+    permanently planted the very rows that caused its own rejection --
     skip_duplicates=True then discarded every later correction, so the
-    request that FIXED the boundary was rejected too, citing the stale
-    values it refused to replace. Reproduces the reviewer's own two-request
-    scenario exactly: block 9 at [20, 24) against montage [0, 12) is
-    rejected, and a second request naming block 9 at the CORRECTED [2, 6)
-    must not still see the first, rejected [20, 24) permanently on file.
+    request that FIXED it was rejected too, citing the stale values it
+    refused to replace. Here a derivative naming run 3, outside montage
+    [0, 12), is rejected with its montage and its asserted runs unwritten,
+    and the corrected request naming run 1 then succeeds.
     """
     from wl_preproc.responder.jobs import accept
     from wl_preproc.schema import core
@@ -587,59 +457,27 @@ def test_a_rejected_request_leaves_no_montage_or_block_row_and_a_correction_then
     subject = "jbres01"
     naive_dt = datetime.datetime(2027, 5, 14, 9, 0)
     landed_session(subject, naive_dt)
+    boundaries = [{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}]
 
-    bad_job = _request(
-        subject=subject,
-        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
-        montage_id=0,
-        idempotency_key="jbres01-k1",
-        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
-        blocks=[
-            {
-                "block_id": 9,
-                "task_type": "GARBAGE",
-                "start_s": 20.0,
-                "end_s": 24.0,
-                "works_block_id": "wb-bad",
-            }
-        ],
-        block_ids=[9],
-    )
-    with pytest.raises(ValueError, match="block 9"):
+    bad_job = _request(subject=subject, session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
+                       idempotency_key="jbres01-k1", montage_boundaries=boundaries, run_numbers=[3])
+    with pytest.raises(ValueError, match="outside montage 0's window"):
         accept(bad_job, prefix=prefix)
 
     session_key = {"subject": subject, "session_datetime": naive_dt}
     assert len(core.Montage & {**session_key, "montage_id": 0}) == 0, (
         "a rejected request must not plant the Montage row it was rejected over"
     )
-    assert len(core.Block & {**session_key, "block_id": 9}) == 0, (
-        "a rejected request must not plant the Block row it was rejected over"
+    assert len(core.RunAssertion & session_key) == 0, (
+        "a rejected request must not plant the runs it asserted"
     )
     assert len(schema_request.Request & {"idempotency_key": "jbres01-k1"}) == 0
 
-    corrected_job = _request(
-        subject=subject,
-        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
-        montage_id=0,
-        idempotency_key="jbres01-k2",
-        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
-        blocks=[
-            {
-                "block_id": 9,
-                "task_type": "neural",
-                "start_s": 2.0,
-                "end_s": 6.0,
-                "works_block_id": "wb-good",
-            }
-        ],
-        block_ids=[9],
-    )
+    corrected_job = _request(subject=subject, session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
+                             idempotency_key="jbres01-k2", montage_boundaries=boundaries, run_numbers=[1])
     key = accept(corrected_job, prefix=prefix)  # must not raise
 
-    block_row = (core.Block & {**session_key, "block_id": 9}).fetch1()
-    assert block_row["task_type"] == "neural"
-    assert block_row["start_s"] == pytest.approx(2.0)
-    assert block_row["end_s"] == pytest.approx(6.0)
+    assert (core.RunAssertion & {**session_key, "run_number": 1}).fetch1("works_run_id") == "wr-1"
     assert (schema_request.Activation & key).fetch1("role") == "derivative"
 
 
@@ -720,7 +558,7 @@ def test_accept_normalises_an_aware_datetime_anywhere_in_the_stored_payload(
         parameters={"calibrated_on": aware_param},
         idempotency_key="jbpar01-k1",
         metadata=MetadataBundle(
-            blocks=[],
+            runs=_asserted(),
             montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
             probes=[],
             experimenter="jw",
@@ -765,74 +603,6 @@ def test_an_out_of_range_montage_id_is_refused_with_the_field_named(landed_sessi
         accept(job, prefix=prefix)
 
 
-def test_accept_rejects_an_out_of_range_block_id(landed_session, prefix):
-    """I2: `core.Block.block_id` is a signed `smallint` (-32768..32767)."""
-    from wl_preproc.responder.jobs import accept
-
-    subject = "jbrng02"
-    naive_dt = datetime.datetime(2027, 5, 18, 9, 0)
-    landed_session(subject, naive_dt)
-    job = _request(
-        subject=subject,
-        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
-        montage_id=0,
-        idempotency_key="jbrng02-k1",
-        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
-        blocks=[{"block_id": 999999, "task_type": "neural", "start_s": 0.0, "end_s": 4.0}],
-    )
-
-    with pytest.raises(ValueError, match="block_id"):
-        accept(job, prefix=prefix)
-
-
-def test_accept_rejects_an_oversized_task_type(landed_session, prefix):
-    """I2: `core.Block.task_type` is `varchar(32)`."""
-    from wl_preproc.responder.jobs import accept
-
-    subject = "jbrng03"
-    naive_dt = datetime.datetime(2027, 5, 19, 9, 0)
-    landed_session(subject, naive_dt)
-    job = _request(
-        subject=subject,
-        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
-        montage_id=0,
-        idempotency_key="jbrng03-k1",
-        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
-        blocks=[{"block_id": 1, "task_type": "x" * 33, "start_s": 0.0, "end_s": 4.0}],
-    )
-
-    with pytest.raises(ValueError, match="task_type"):
-        accept(job, prefix=prefix)
-
-
-def test_accept_rejects_an_oversized_works_block_id(landed_session, prefix):
-    """I2: `core.Block.works_block_id` is `varchar(64)`."""
-    from wl_preproc.responder.jobs import accept
-
-    subject = "jbrng04"
-    naive_dt = datetime.datetime(2027, 5, 20, 9, 0)
-    landed_session(subject, naive_dt)
-    job = _request(
-        subject=subject,
-        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
-        montage_id=0,
-        idempotency_key="jbrng04-k1",
-        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
-        blocks=[
-            {
-                "block_id": 1,
-                "task_type": "neural",
-                "start_s": 0.0,
-                "end_s": 4.0,
-                "works_block_id": "x" * 65,
-            }
-        ],
-    )
-
-    with pytest.raises(ValueError, match="works_block_id"):
-        accept(job, prefix=prefix)
-
-
 def test_a_non_finite_start_s_or_end_s_is_refused_with_the_field_named(landed_session, prefix):
     """I2: `start_s`/`end_s` are `double` -- unbounded in magnitude for any
     realistic session-time-seconds value, but a non-finite float (here,
@@ -859,32 +629,6 @@ def test_a_non_finite_start_s_or_end_s_is_refused_with_the_field_named(landed_se
         accept(job, prefix=prefix)
 
 
-def test_accept_rejects_an_unknown_block_id(landed_session, prefix):
-    """I4: a `block_ids` entry naming no `Block` anywhere -- neither already
-    on record nor supplied in this same request's `metadata.blocks` -- is a
-    `ValueError`, not silently excluded from the window check nor left to
-    surface later as `ActivationBlock`'s own foreign-key error."""
-    from wl_preproc.responder.jobs import accept
-    from wl_preproc.schema import request as schema_request
-
-    subject = "jbunk01"
-    naive_dt = datetime.datetime(2027, 5, 22, 9, 0)
-    landed_session(subject, naive_dt)
-    job = _request(
-        subject=subject,
-        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
-        montage_id=0,
-        idempotency_key="jbunk01-k1",
-        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
-        block_ids=[7],  # no Block anywhere named 7
-    )
-
-    with pytest.raises(ValueError, match="no Block on record"):
-        accept(job, prefix=prefix)
-
-    assert len(schema_request.Request & {"idempotency_key": "jbunk01-k1"}) == 0
-
-
 def test_accept_rejects_a_session_this_host_has_never_ingested(
     dj_conn, prefix, table_snapshot, deep_equal
 ):
@@ -932,23 +676,15 @@ def test_accept_rejects_a_session_this_host_has_never_ingested(
         montage_id=0,
         idempotency_key="jbnoses-k1",
         montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
-        blocks=[
-            {
-                "block_id": 1,
-                "task_type": "rf_map",
-                "start_s": 0.0,
-                "end_s": 6.0,
-                "works_block_id": "wb-1",
-            }
-        ],
     )
 
     written_tables = [
         core.Montage,
-        core.Block,
+        core.RunAssertion,
         schema_request.Request,
         schema_request.Activation,
-        schema_request.ActivationBlock,
+        schema_request.ActivationRun,
+        schema_request.ActivationProbeRun,
         pipeline.Session,
     ]
     before = [table_snapshot(table) for table in written_tables]
@@ -1069,7 +805,8 @@ def test_a_later_request_corrects_the_report_and_adds_the_assignment(landed_sess
     from wl_preproc.schema import ephys
 
     naive_dt = datetime.datetime(2027, 5, 12, 9, 0)
-    key = landed_session("jbprob02", naive_dt)
+    runs = [*_RUNS, (4, 25.0, 30.0)]  # one run in each of the three montages
+    key = landed_session("jbprob02", naive_dt, runs=runs)
     first = {"area": "V4d", "source": "at_rig", "asserted_at": "2027-05-12T10:00:00Z"}
     boundaries = [{"montage_id": m, "start_s": 12.0 * m, "end_s": 12.0 * (m + 1)} for m in range(3)]
     for idempotency_key, montage_id, probes in (
@@ -1083,7 +820,7 @@ def test_a_later_request_corrects_the_report_and_adds_the_assignment(landed_sess
     ):
         accept(_request(subject="jbprob02", session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
                         idempotency_key=idempotency_key, montage_id=montage_id,
-                        montage_boundaries=boundaries, probes=probes),
+                        montage_boundaries=boundaries, runs=runs, probes=probes),
                prefix=prefix)
 
     reports = (ephys.InsertionReport & key).to_dicts(order_by="insertion_number")
@@ -1095,7 +832,7 @@ def test_a_later_request_corrects_the_report_and_adds_the_assignment(landed_sess
 
 def test_a_refused_request_records_no_report(landed_session, prefix):
     """Every check runs before anything is written (review C1): a request
-    refused for its blocks leaves no report behind."""
+    refused for its montage leaves no report behind."""
     from wl_preproc.responder.jobs import accept
     from wl_preproc.schema import ephys
 
@@ -1112,12 +849,6 @@ def test_a_refused_request_records_no_report(landed_session, prefix):
 # -- The canonical lifecycle (design spec 2026-09-30-canonical-lifecycle-design.md section 3)
 
 _LC_BOUNDARIES = [{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}]
-_LC_BLOCKS = [
-    {"block_id": block_id, "task_type": "neural", "start_s": start_s, "end_s": end_s, "works_block_id": None}
-    for block_id, (start_s, end_s) in enumerate(((0.0, 4.0), (4.0, 8.0), (8.0, 12.0), (12.0, 16.0)), start=1)
-]
-# Block 4 lies outside montage 0's window, [0, 12): named in a canonical's
-# block set, it is refused (the 2b final review's M2).
 
 
 def _lifecycle_job(subject, session_datetime, key, **selection) -> JobRequest:
@@ -1126,27 +857,11 @@ def _lifecycle_job(subject, session_datetime, key, **selection) -> JobRequest:
         selection={"session_datetime": session_datetime, "montage_id": 0, **selection},
         parameters={},
         idempotency_key=key,
-        metadata=MetadataBundle(blocks=_LC_BLOCKS, montage_boundaries=_LC_BOUNDARIES, probes=[],
+        metadata=MetadataBundle(runs=_asserted(), montage_boundaries=_LC_BOUNDARIES, probes=[],
                                 experimenter="jw", subject=subject, task_types=[]),
     )
 
 
-def test_a_canonical_request_can_name_its_block_set(landed_session, prefix):
-    """How wl.works leaves out a bad block: `role: canonical` with
-    `block_ids`. Without the role, `block_ids` still means a derivative."""
-    from wl_preproc.responder.jobs import accept
-    from wl_preproc.schema import request as schema_request
-
-    when = datetime.datetime(2027, 5, 20, 9, 0)
-    landed_session("jblc001", when)
-    key = accept(_lifecycle_job("jblc001", when, "jblc001-k1", role="canonical", block_ids=[1, 3]), prefix=prefix)
-    assert key["activation_id"] == 0
-    assert (schema_request.Activation & key).fetch1("role") == "canonical"
-    assert sorted(int(b) for b in (schema_request.ActivationBlock & key).to_arrays("block_id")) == [1, 3]
-    derivative = accept(_lifecycle_job("jblc001", when, "jblc001-k2", block_ids=[1, 3]), prefix=prefix)
-    assert (schema_request.Activation & derivative).fetch1("role") == "derivative"
-
-
 def test_a_replacement_request_supersedes_the_named_canonical(landed_session, prefix):
     from wl_preproc.responder.jobs import accept
     from wl_preproc.schema import request as schema_request
@@ -1154,8 +869,7 @@ def test_a_replacement_request_supersedes_the_named_canonical(landed_session, pr
     when = datetime.datetime(2027, 5, 20, 10, 0)
     landed_session("jblc002", when)
     first = accept(_lifecycle_job("jblc002", when, "jblc002-k1"), prefix=prefix)
-    job = _lifecycle_job("jblc002", when, "jblc002-k2", role="canonical", supersedes_activation_id=0,
-                         block_ids=[1, 2])
+    job = _lifecycle_job("jblc002", when, "jblc002-k2", role="canonical", supersedes_activation_id=0)
     replacement = accept(job, prefix=prefix)
     row = (schema_request.Activation & replacement).fetch1()
     assert (row["activation_id"], row["role"], row["supersedes"]) == (1, "canonical", first["activation_id"])
@@ -1180,18 +894,18 @@ def test_a_replacement_of_a_superseded_canonical_is_a_conflict(landed_session, p
 
 @pytest.mark.parametrize("selection", [
     {"supersedes_activation_id": 0},
-    {"role": "derivative", "supersedes_activation_id": 0, "block_ids": [1]},
+    {"role": "derivative", "supersedes_activation_id": 0, "run_numbers": [1]},
     {"role": "derivative"},
     {"role": "bogus"},
     {"role": "canonical", "supersedes_activation_id": -1},
     {"role": "canonical", "supersedes_activation_id": True},
     {"role": "canonical", "supersedes_activation_id": "0"},
-    {"role": "canonical", "block_ids": [99]},
-    {"role": "canonical", "block_ids": [4]},
+    {"role": "canonical", "run_numbers": [1]},
+    {"run_numbers": [3]},
 ])
 def test_a_selection_the_lifecycle_refuses_is_a_value_error(landed_session, prefix, selection):
     """Section 3's refusals, each a `422` over HTTP: only a canonical
-    supersedes, a derivative names its blocks, and an activation id is one
+    supersedes, a derivative names its runs, and an activation id is one
     non-negative integer."""
     from wl_preproc.responder.jobs import accept
     from wl_preproc.schema import request as schema_request
@@ -1204,17 +918,18 @@ def test_a_selection_the_lifecycle_refuses_is_a_value_error(landed_session, pref
     assert not schema_request.Request & {"idempotency_key": key}
 
 
-def test_a_canonical_role_with_no_blocks_takes_the_whole_montage(landed_session, prefix):
-    """`role: canonical` with an empty `block_ids` is the plain canonical:
-    no named block set, so the builder takes every block in the montage."""
+def test_a_canonical_role_with_no_runs_named_takes_every_run_of_the_montage(landed_session, prefix):
+    """`role: canonical` with an empty `run_numbers` is the plain canonical:
+    the file holds every measured run of its montage (the requester's
+    decision 1 of 2026-10-01)."""
     from wl_preproc.responder.jobs import accept
     from wl_preproc.schema import request as schema_request
 
     when = datetime.datetime(2027, 5, 20, 13, 0)
     landed_session("jblc005", when)
-    key = accept(_lifecycle_job("jblc005", when, "jblc005-k1", role="canonical", block_ids=[]), prefix=prefix)
+    key = accept(_lifecycle_job("jblc005", when, "jblc005-k1", role="canonical", run_numbers=[]), prefix=prefix)
     assert key["activation_id"] == 0 and (schema_request.Activation & key).fetch1("role") == "canonical"
-    assert not schema_request.ActivationBlock & key
+    assert sorted(int(n) for n in (schema_request.ActivationRun & key).to_arrays("run_number")) == [1, 2]
 
 
 @pytest.mark.parametrize("selection", [{"supersedes_activation_id": None},
@@ -1234,20 +949,12 @@ def test_a_null_supersedes_is_absent(landed_session, prefix, selection):
 
 
 # -- Requests that name runs (design spec
-# `2026-10-01-session-listing-and-run-requests-design.md` section 3). Run 3 lies
-# outside montage 0's window [0, 12).
-
-_RUNS = [(1, 0.0, 4.0), (2, 5.0, 11.0), (3, 13.0, 16.0)]
-_SERIAL = "19011110001"
+# `2026-10-01-session-listing-and-run-requests-design.md` section 3), with one
+# probe whose runs a canonical states.
 
 
 def _landed_with_runs(landed_session, subject: str, day: int, runs=_RUNS) -> dict:
-    from wl_preproc.schema import core
-
-    key = landed_session(subject, datetime.datetime(2027, 9, day, 9, 0))
-    core.Run.insert([{**key, "run_number": number, "task_type": 0, "run_start_time": start, "run_stop_time": stop,
-                      "closed": 1} for number, start, stop in runs], skip_duplicates=True)
-    return key
+    return landed_session(subject, datetime.datetime(2027, 9, day, 9, 0), runs=runs)
 
 
 def _runs_job(key: dict, idempotency_key: str, *, runs=_RUNS, ids: dict | None = None,
@@ -1258,9 +965,7 @@ def _runs_job(key: dict, idempotency_key: str, *, runs=_RUNS, ids: dict | None =
         parameters={},
         idempotency_key=idempotency_key,
         metadata=MetadataBundle(
-            blocks=[], montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
-            runs=[{"run_number": number, "start_s": start, "end_s": stop,
-                   "works_run_id": (ids or {}).get(number, f"wr-{number}")} for number, start, stop in runs],
+            montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}], runs=_asserted(runs, ids),
             probes=[{"serial": serial, "insertion_number": index} for index, serial in enumerate(serials, start=1)],
             experimenter="jw", subject=key["subject"], task_types=[]),
     )
@@ -1327,7 +1032,7 @@ def test_runs_not_yet_measured_are_not_yet_ingested(landed_session, prefix):
     """wl.works retries this one: the event stage has not read the session."""
     from wl_preproc.responder.jobs import accept
 
-    key = landed_session("runjob4", datetime.datetime(2027, 9, 4, 9, 0))
+    key = landed_session("runjob4", datetime.datetime(2027, 9, 4, 9, 0), runs=())
     with pytest.raises(ValueError, match="has no measured run on this host yet"):
         accept(_runs_job(key, "runjob4-k1", probe_runs={_SERIAL: [1]}), prefix=prefix)
 
@@ -1361,22 +1066,35 @@ def test_a_derivative_names_its_runs(landed_session, prefix):
     assert _run_rows(schema_request.ActivationRun, activation) == [(2,)]
 
 
-@pytest.mark.parametrize("selection, blocks, expect", [
-    ({"block_ids": [1], "probe_runs": {_SERIAL: [1]}}, [], "a request names runs or blocks, not both"),
-    ({"probe_runs": {_SERIAL: [1]}}, [{"block_id": 1, "task_type": "x", "start_s": 0.0, "end_s": 4.0}],
-     "a request names runs or blocks, not both"),
-    ({"role": "canonical", "run_numbers": [1], "probe_runs": {_SERIAL: [1]}}, [],
-     "selection.run_numbers is a derivative's"),
-    ({"run_numbers": [2], "probe_runs": {_SERIAL: [2]}}, [], "selection.probe_runs is a canonical's"),
-    ({"run_numbers": ["2"]}, [], "selection.run_numbers must be a list of run numbers"),
-    ({"run_numbers": [3]}, [], "selection names run(s) [3] outside montage 0's window"),
+@pytest.mark.parametrize("selection, expect", [
+    ({"role": "canonical", "run_numbers": [1], "probe_runs": {_SERIAL: [1]}}, "selection.run_numbers is a derivative's"),
+    ({"run_numbers": [2], "probe_runs": {_SERIAL: [2]}}, "selection.probe_runs is a canonical's"),
+    ({"run_numbers": ["2"]}, "selection.run_numbers must be a list of run numbers"),
+    ({"run_numbers": [3]}, "selection names run(s) [3] outside montage 0's window"),
 ])
-def test_a_selection_that_mixes_or_misplaces_runs_is_refused(landed_session, prefix, selection, blocks, expect):
+def test_a_selection_that_misplaces_runs_is_refused(landed_session, prefix, selection, expect):
     from wl_preproc.responder.jobs import accept
 
     key = _landed_with_runs(landed_session, "runjob7", 7)
-    job = _runs_job(key, f"runjob7-{expect[:24]}", **selection)
-    if blocks:
-        job = job.model_copy(update={"metadata": job.metadata.model_copy(update={"blocks": blocks})})
     with pytest.raises(ValueError, match=re.escape(expect)):
-        accept(job, prefix=prefix)
+        accept(_runs_job(key, f"runjob7-{expect[:24]}", **selection), prefix=prefix)
+
+
+@pytest.mark.parametrize("selection, expect", [
+    ({"block_ids": [1], "probe_runs": {_SERIAL: [1]}},
+     "selection.block_ids is retired: a derivative names its runs in selection.run_numbers"),
+    ({"role": "canonical", "block_ids": [1, 2], "probe_runs": {_SERIAL: [1]}}, "selection.block_ids is retired"),
+    ({"role": "derivative"}, "selection['role'] 'derivative' needs run_numbers: a derivative is its run set"),
+])
+def test_the_retired_block_selection_is_refused_naming_its_replacement(landed_session, prefix, selection, expect):
+    """Design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    section 3.1: nothing but this repository's tests sent blocks, and a
+    request that still does is a 422 that says what replaced them."""
+    from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema import request as schema_request
+
+    key = _landed_with_runs(landed_session, "runjob8", 8)
+    idempotency_key = f"runjob8-{sorted(selection)!r}"
+    with pytest.raises(ValueError, match=re.escape(expect)):
+        accept(_runs_job(key, idempotency_key, **selection), prefix=prefix)
+    assert not schema_request.Request & {"idempotency_key": idempotency_key}
```

```diff
--- a/tests/responder/test_http.py
+++ b/tests/responder/test_http.py
@@ -225,7 +225,6 @@ def _valid_job_payload() -> dict:
         "parameters": {},
         "idempotency_key": "http-probe-key-1",
         "metadata": {
-            "blocks": [],
             "montage_boundaries": [],
             "probes": [],
             "experimenter": "jw",
@@ -1409,16 +1408,22 @@ def test_nothing_ever_returns_a_traceback_regardless_of_input(start_server):
 # --------------------------------------------------------------------------
 
 
+# The runs the event stage measured for every session landed here, both in
+# montage 0's window [0, 12) (design spec
+# `2026-10-01-session-listing-and-run-requests-design.md` section 3).
+_RUNS = [(1, 0.0, 4.0), (2, 5.0, 11.0)]
+
+
 @pytest.fixture
 def landed_session(dj_conn, prefix):
     """A `(subject, session_datetime)` with Lab/Subject/Session already on
-    file -- the precondition `accept()` itself assumes (see
+    file, and its runs measured -- the precondition `accept()` itself assumes (see
     `tests/responder/test_jobs.py`'s fixture of the same name and shape;
     this is a local copy since pytest fixtures do not cross test modules
     without living in a shared `conftest.py`, and duplicating four lines
     here was judged cheaper than relocating a fixture Task 7 did not need
     to share)."""
-    from wl_preproc.schema import pipeline
+    from wl_preproc.schema import core, pipeline
     from wl_preproc.schema import request as schema_request
 
     schema_request.activate(prefix=prefix)
@@ -1437,9 +1442,11 @@ def landed_session(dj_conn, prefix):
             },
             skip_duplicates=True,
         )
-        pipeline.Session.insert1(
-            {"subject": subject, "session_datetime": session_datetime}, skip_duplicates=True
-        )
+        key = {"subject": subject, "session_datetime": session_datetime}
+        pipeline.Session.insert1(key, skip_duplicates=True)
+        core.Run.insert([{**key, "run_number": number, "task_type": 0, "run_start_time": start,
+                          "run_stop_time": stop, "closed": 1} for number, start, stop in _RUNS],
+                        skip_duplicates=True)
 
     return _land
 
@@ -1451,7 +1458,8 @@ def _real_job_payload(*, subject: str, session_datetime_iso: str, idempotency_ke
         "parameters": {},
         "idempotency_key": idempotency_key,
         "metadata": {
-            "blocks": [],
+            "runs": [{"run_number": number, "start_s": start, "end_s": stop, "works_run_id": f"wr-{number}"}
+                     for number, start, stop in _RUNS],
             "montage_boundaries": [{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
             "probes": [],
             "experimenter": "jw",
@@ -2062,6 +2070,7 @@ def test_an_insertions_aim_and_assignment_arrive_over_http(start_server, landed_
         "target": {"area": "V4d", "atlas": "CHARM", "atlas_level": 6},
         "area_assignment": {"area": "V4d", "source": "at_rig", "asserted_at": "2027-06-21T10:15:00+02:00"},
     }]
+    payload["selection"]["probe_runs"] = {"19011110001": [1, 2]}
 
     status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=payload)
 
@@ -2071,6 +2080,21 @@ def test_an_insertions_aim_and_assignment_arrive_over_http(start_server, landed_
     assert (ephys.AreaAssignment & key).fetch1("asserted_at") == datetime.datetime(2027, 6, 21, 8, 15)
 
 
+def test_the_retired_blocks_are_refused_over_http_naming_metadata_runs(start_server):
+    """Design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    section 3.1: a request still sending blocks is a 422 that says what
+    replaced them, before `accept()` is reached."""
+    base = start_server(TOKEN, _health_ok, _unused)
+    payload = _valid_job_payload()
+    payload["metadata"]["blocks"] = [{"block_id": 1, "task_type": "rf_map", "start_s": 0.0, "end_s": 4.0}]
+
+    status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=payload)
+
+    assert status == 422
+    [entry] = json.loads(body)["detail"]
+    assert entry["loc"] == ["metadata", "blocks"] and "metadata.runs" in entry["msg"]
+
+
 def test_a_partial_aim_is_refused_over_http(start_server):
     """All three or none: an area without its atlas names no place."""
     base = start_server(TOKEN, _health_ok, _unused)
```

```diff
--- a/tests/schema/test_probe_linking.py
+++ b/tests/schema/test_probe_linking.py
@@ -27,22 +27,37 @@ def daemon_module(dj_conn, prefix):
     return daemon
 
 
-def _landed(tmp_path_factory, subject, session_id, **update):
-    _recipe, key = _session(tmp_path_factory, subject=subject, session_id=session_id, **update)
+def _measured(key):
+    """The session's runs read by the event stage, which runs before the
+    census in a daemon pass: a request names measured runs (design spec
+    `2026-10-01-session-listing-and-run-requests-design.md` section 3.2), so
+    the earliest a report can arrive is after this and before the census."""
+    from wl_preproc import daemon
+
+    daemon._populate_event_stage()
     return key
 
 
+def _landed(tmp_path_factory, subject, session_id, **update):
+    _recipe, key = _session(tmp_path_factory, subject=subject, session_id=session_id, runs=True, **update)
+    return _measured(key)
+
+
 def _report(key, idempotency_key, probes, prefix):
-    """wl.works' job request for the session's canonical, carrying `probes`.
-    A second one for the same montage returns the same activation, and its
-    report is recorded all the same."""
+    """wl.works' job request for the session's canonical, carrying `probes`,
+    each sorting every run. A second one for the same montage returns the
+    same activation, and its report is recorded all the same."""
     from wl_preproc.contracts.protocol import JobRequest, MetadataBundle
     from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema import core
 
+    runs = [{"run_number": row["run_number"], "start_s": row["run_start_time"], "end_s": row["run_stop_time"],
+             "works_run_id": f"wr-{row['run_number']}"} for row in (core.Run & key).to_dicts(order_by="run_number")]
     return accept(JobRequest(
         domain="neural", parameters={}, idempotency_key=idempotency_key,
-        selection={"session_datetime": key["session_datetime"].replace(tzinfo=datetime.UTC), "montage_id": 0},
-        metadata=MetadataBundle(blocks=[], montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 16.0}],
+        selection={"session_datetime": key["session_datetime"].replace(tzinfo=datetime.UTC), "montage_id": 0,
+                   "probe_runs": {probe["serial"]: [run["run_number"] for run in runs] for probe in probes}},
+        metadata=MetadataBundle(runs=runs, montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 16.0}],
                                 probes=probes, experimenter="jw", subject=key["subject"], task_types=[]),
     ), prefix=prefix)
 
@@ -143,8 +158,8 @@ def test_a_probe_without_geometry_is_not_linked(daemon_module, prefix, tmp_path_
     from tests.schema.test_probe_census import _unknown_type
 
     _recipe, key = _session(tmp_path_factory, _unknown_type, subject="plink7", session_id="2025-06-21_01",
-                            probe_serial="19011110004")
-    _report(key, "plink7-k1", [{"serial": "19011110004", "insertion_number": 1}], prefix)
+                            probe_serial="19011110004", runs=True)
+    _report(_measured(key), "plink7-k1", [{"serial": "19011110004", "insertion_number": 1}], prefix)
     daemon_module.run_once(prefix=prefix)
     assert _links(key) == {}
 
```

```diff
--- a/tests/schema/test_request.py
+++ b/tests/schema/test_request.py
@@ -146,7 +146,7 @@ while time.monotonic() < deadline:
 try:
     result = request.submit_derivative(
         idempotency_key=os.environ["WLPP_KEY"], task_type="neural", origin="wl_works",
-        selection=selection, block_ids=json.loads(os.environ["WLPP_BLOCK_IDS"]), payload={},
+        selection=selection, run_numbers=json.loads(os.environ["WLPP_RUN_NUMBERS"]), payload={},
     )
     print("OK " + str(result["activation_id"]))
 except Exception as e:  # noqa: BLE001 -- must report ANY exception type back
@@ -198,20 +198,14 @@ def selection(req):
     montage_id = next(_montage_ids)
     core.Montage.insert1({**key, "montage_id": montage_id, "start_s": 0.0, "end_s": 12.0},
                          skip_duplicates=True)
-    # ActivationBlock's `-> core.Block` (request.py) is a real foreign key, so
-    # submit_derivative's block_ids must name rows that already exist. Three
-    # covers every block_ids value Task 4's tests use ([1, 2], [2, 1], [1, 3]).
+    # ActivationRun's `-> core.Run` (request.py) is a real foreign key, so
+    # submit_derivative's run_numbers must name rows that already exist. Three
+    # covers every run_numbers value these tests use ([1, 2], [2, 1], [1, 3]).
     # skip_duplicates=True because this fixture runs once per test but
     # (subject, session_datetime) -- unlike montage_id -- is the same tuple
-    # every time, so block_id 1-3 only actually get inserted on the first call.
-    for block_id, (start_s, end_s) in enumerate(((0.0, 4.0), (4.0, 8.0), (8.0, 12.0)), start=1):
-        core.Block.insert1(
-            {**key, "block_id": block_id, "task_type": "neural", "start_s": start_s, "end_s": end_s},
-            skip_duplicates=True,
-        )
-        # The measured runs the same windows hold: ActivationRun's
-        # `-> core.Run` is a real foreign key too.
-        core.Run.insert1({**key, "run_number": block_id, "task_type": 0, "run_start_time": start_s,
+    # every time, so runs 1-3 only actually get inserted on the first call.
+    for run_number, (start_s, end_s) in enumerate(((0.0, 4.0), (4.0, 8.0), (8.0, 12.0)), start=1):
+        core.Run.insert1({**key, "run_number": run_number, "task_type": 0, "run_start_time": start_s,
                           "run_stop_time": end_s, "closed": 1}, skip_duplicates=True)
     return {**key, "montage_id": montage_id}
 
@@ -490,7 +484,7 @@ def test_submit_always_produces_a_canonical_activation_at_id_zero(req, selection
     """submit() has no way to form a derivative: the dedupe above returns on
     ANY existing Activation for the selection, so the branch that would
     allocate a second activation_id is unreachable by construction, and a
-    derivative needs a block set this function's selection does not carry.
+    derivative needs a run set this function's selection does not carry.
     Pinned here so that the day someone widens the dedupe key (or the
     selection) to admit derivatives, this test fails loudly instead of the
     canonical-only assumption silently rotting."""
@@ -515,7 +509,7 @@ def test_submit_before_activate_raises_a_clear_error(req, selection, monkeypatch
 
 
 def test_selection_hash_is_order_independent():
-    """The block set is a set. Two requests naming the same blocks in a
+    """The run set is a set. Two requests naming the same runs in a
     different order are the same selection, and if they hash differently the
     dedupe in section 11.3 silently starts a second run."""
     from wl_preproc.schema.request import selection_hash
@@ -529,9 +523,9 @@ def test_selection_hash_separates_task_types():
     assert selection_hash("neural", [1, 2]) != selection_hash("export", [1, 2])
 
 
-def test_selection_hash_deduplicates_repeated_block_ids():
-    """The block set is a *set*: naming the same block twice is one block, not
-    two. A caller that accumulates block ids across, say, paginated results
+def test_selection_hash_deduplicates_repeated_run_numbers():
+    """The run set is a *set*: naming the same run twice is one run, not
+    two. A caller that accumulates run numbers across, say, paginated results
     and does not itself de-duplicate must still land on the same selection as
     one that does -- otherwise the same logical selection would silently carry
     two different identities depending on how the caller happened to build its
@@ -588,7 +582,7 @@ def test_a_derivative_gets_its_own_activation_id(selection, prefix):
     )
     derivative = request.submit_derivative(
         idempotency_key="dv-2", task_type="neural", origin="wl_works",
-        selection=selection, block_ids=[1, 2], payload={},
+        selection=selection, run_numbers=[1, 2], payload={},
     )
 
     assert derivative["activation_id"] != canonical["activation_id"]
@@ -616,27 +610,27 @@ def test_the_same_selection_returns_the_running_one(selection, prefix):
 
     first = request.submit_derivative(
         idempotency_key="dv-3", task_type="neural", origin="wl_works",
-        selection=selection, block_ids=[1, 2], payload={},
+        selection=selection, run_numbers=[1, 2], payload={},
     )
     second = request.submit_derivative(
         idempotency_key="dv-4", task_type="neural", origin="wl_works",
-        selection=selection, block_ids=[2, 1], payload={},
+        selection=selection, run_numbers=[2, 1], payload={},
     )
 
     assert second == first
     assert len(request.Activation & first) == 1
 
 
-def test_a_different_block_set_is_a_different_activation(selection, prefix):
+def test_a_different_run_set_is_a_different_activation(selection, prefix):
     from wl_preproc.schema import request
 
     a = request.submit_derivative(
         idempotency_key="dv-5", task_type="neural", origin="wl_works",
-        selection=selection, block_ids=[1, 2], payload={},
+        selection=selection, run_numbers=[1, 2], payload={},
     )
     b = request.submit_derivative(
         idempotency_key="dv-6", task_type="neural", origin="wl_works",
-        selection=selection, block_ids=[1, 3], payload={},
+        selection=selection, run_numbers=[1, 3], payload={},
     )
 
     assert a != b
@@ -654,7 +648,7 @@ def test_a_derivative_before_any_canonical_does_not_claim_activation_id_zero(sel
 
     derivative = request.submit_derivative(
         idempotency_key="dv-7", task_type="neural", origin="wl_works",
-        selection=selection, block_ids=[1], payload={},
+        selection=selection, run_numbers=[1], payload={},
     )
     assert derivative["activation_id"] != 0
 
@@ -668,31 +662,31 @@ def test_a_derivative_before_any_canonical_does_not_claim_activation_id_zero(sel
 
 def test_a_retry_of_a_derivative_idempotency_key_returns_the_running_activation(selection, prefix):
     """The accept branch of the reuse check, now widened to also compare the
-    block set (see test_reusing_a_derivative_idempotency_key_for_a_different_
-    block_set_is_refused below): the identical key, resubmitted with the
-    identical block set, is still a retry and returns the same activation
+    run set (see test_reusing_a_derivative_idempotency_key_for_a_different_
+    run_set_is_refused below): the identical key, resubmitted with the
+    identical run set, is still a retry and returns the same activation
     rather than being refused."""
     from wl_preproc.schema import request
 
     first = request.submit_derivative(
         idempotency_key="dv-10", task_type="neural", origin="wl_works",
-        selection=selection, block_ids=[1, 2], payload={},
+        selection=selection, run_numbers=[1, 2], payload={},
     )
     second = request.submit_derivative(
         idempotency_key="dv-10", task_type="neural", origin="wl_works",
-        selection=selection, block_ids=[1, 2], payload={},
+        selection=selection, run_numbers=[1, 2], payload={},
     )
     assert first == second
     assert len(request.Request & {"idempotency_key": "dv-10"}) == 1
 
 
-def test_reusing_a_derivative_idempotency_key_for_a_different_block_set_is_refused(selection, prefix):
-    """The block set is part of "the same ask" for a derivative exactly as
+def test_reusing_a_derivative_idempotency_key_for_a_different_run_set_is_refused(selection, prefix):
+    """The run set is part of "the same ask" for a derivative exactly as
     the selection is for submit() (see
     test_reusing_an_idempotency_key_for_a_different_selection_is_refused
-    above): reusing a key with different block_ids is a collision, not a
+    above): reusing a key with different run_numbers is a collision, not a
     retry, and the running activation must not be handed back to a caller who
-    asked for different blocks.
+    asked for different runs.
 
     This covers the case where the key's OWN first submission is what
     produced the derivative Activation below (`first`, naming request_key=
@@ -710,13 +704,13 @@ def test_reusing_a_derivative_idempotency_key_for_a_different_block_set_is_refus
 
     first = request.submit_derivative(
         idempotency_key="dv-9", task_type="neural", origin="wl_works",
-        selection=selection, block_ids=[1, 2], payload={},
+        selection=selection, run_numbers=[1, 2], payload={},
     )
 
     with pytest.raises(dj.DataJointError, match="selection"):
         request.submit_derivative(
             idempotency_key="dv-9", task_type="neural", origin="wl_works",
-            selection=selection, block_ids=[1, 3], payload={},
+            selection=selection, run_numbers=[1, 3], payload={},
         )
 
     # the first ask's activation is unaffected, unmodified, and still the
@@ -746,7 +740,7 @@ def test_reusing_a_submit_key_for_a_derivative_is_refused(selection, prefix):
     with pytest.raises(dj.DataJointError, match="selection"):
         request.submit_derivative(
             idempotency_key="asym-1", task_type="neural", origin="cli",
-            selection=selection, block_ids=[1, 2], payload={},
+            selection=selection, run_numbers=[1, 2], payload={},
         )
 
     # the canonical is unaffected, and no derivative was created under this key
@@ -775,7 +769,7 @@ def test_reusing_a_derivative_key_for_submit_is_refused(selection, prefix):
 
     derivative = request.submit_derivative(
         idempotency_key="asym-2", task_type="neural", origin="wl_works",
-        selection=selection, block_ids=[1, 2], payload={},
+        selection=selection, run_numbers=[1, 2], payload={},
     )
 
     with pytest.raises(dj.DataJointError, match="selection"):
@@ -839,7 +833,7 @@ def test_a_concurrent_identical_selection_returns_the_rival_not_a_collision(sele
 
     result = request.submit_derivative(
         idempotency_key="race-a-mine", task_type="neural", origin="wl_works",
-        selection=selection, block_ids=[1, 2], payload={},
+        selection=selection, run_numbers=[1, 2], payload={},
     )
 
     assert calls["n"] == 1, "must return the rival's key on the FIRST collision, not retry past it"
@@ -856,7 +850,7 @@ def test_a_concurrent_identical_selection_returns_the_rival_not_a_collision(sele
 
 def test_a_concurrent_different_selection_retries_to_a_fresh_id(selection, prefix, monkeypatch):
     """Review round 2, Important 2, case (b) -- the likelier and more
-    consequential case: two researchers picking DIFFERENT block sets on one
+    consequential case: two researchers picking DIFFERENT run sets on one
     montage, racing for the same allocated id. Before this fix, this
     collision raised outright: the loser's legitimate, distinct submission
     was destroyed and its Activation never created at all. That is precisely
@@ -894,7 +888,7 @@ def test_a_concurrent_different_selection_retries_to_a_fresh_id(selection, prefi
 
     result = request.submit_derivative(
         idempotency_key="race-b-mine", task_type="neural", origin="wl_works",
-        selection=selection, block_ids=[1, 2], payload={},
+        selection=selection, run_numbers=[1, 2], payload={},
     )
 
     assert calls["n"] == 2, "must retry exactly once past the simulated collision"
@@ -907,8 +901,8 @@ def test_a_concurrent_different_selection_retries_to_a_fresh_id(selection, prefi
     # BOTH activations exist -- the loser's legitimate, distinct submission
     # was not destroyed by the rival's collision
     assert len(request.Activation & {"role": "derivative"} & selection) == 2
-    # and its own ActivationBlock rows were written under the fresh id
-    assert len(request.ActivationBlock & result) == 2
+    # and its own ActivationRun rows were written under the fresh id
+    assert len(request.ActivationRun & result) == 2
 
 
 def test_derivative_allocation_gives_up_after_sustained_contention(selection, prefix, monkeypatch):
@@ -931,7 +925,7 @@ def test_derivative_allocation_gives_up_after_sustained_contention(selection, pr
     with pytest.raises(dj.DataJointError, match="exhausted"):
         request.submit_derivative(
             idempotency_key="race-c-mine", task_type="neural", origin="wl_works",
-            selection=selection, block_ids=[1, 2], payload={},
+            selection=selection, run_numbers=[1, 2], payload={},
         )
 
     # nothing was written for the exhausted submission -- rolled back whole
@@ -965,7 +959,7 @@ def test_locking_read_one_takes_a_record_only_lock_no_gap(selection, prefix):
 
     seed = request.submit_derivative(
         idempotency_key="lock-shape-seed", task_type="neural", origin="wl_works",
-        selection=selection, block_ids=[1], payload={},
+        selection=selection, run_numbers=[1], payload={},
     )
 
     conn = dj.conn()
@@ -1067,7 +1061,7 @@ def test_three_concurrent_derivative_submissions_do_not_deadlock(selection, pref
             **env_base,
             "WLPP_WORKER_ID": str(i),
             "WLPP_KEY": f"deadlock-{i}",
-            "WLPP_BLOCK_IDS": json.dumps([i + 1]),  # three genuinely distinct selections
+            "WLPP_RUN_NUMBERS": json.dumps([i + 1]),  # three genuinely distinct selections
         }
         processes.append(
             subprocess.Popen(
@@ -1115,31 +1109,29 @@ def test_three_concurrent_derivative_submissions_do_not_deadlock(selection, pref
     )
 
 
-def test_activation_block_gets_one_row_per_distinct_block_id(selection, prefix):
-    """Review round 2, Important 4: the third of submit_derivative's three
-    documented differences -- writing ActivationBlock -- had zero test
-    coverage. Deleting the entire ActivationBlock.insert(...) call left the
-    suite at 29 passed; the only hit for ActivationBlock anywhere under
-    tests/ was a comment. Duplicated and unsorted on purpose, so this also
-    covers "duplicates collapse" and "the count matches", not just presence:
-    the written rows must mirror what selection_hash itself canonicalises
-    block_ids to (sorted(set(...))), or the two could silently disagree
-    about what "this selection" even contains."""
+def test_activation_run_gets_one_row_per_distinct_run_number(selection, prefix):
+    """Review round 2, Important 4, carried from ActivationBlock to
+    ActivationRun: the third of submit_derivative's three documented
+    differences is writing the run set. Duplicated and unsorted on purpose,
+    so this also covers "duplicates collapse" and "the count matches", not
+    just presence: the written rows must mirror what selection_hash itself
+    canonicalises run_numbers to (sorted(set(...))), or the two could
+    silently disagree about what "this selection" even contains."""
     from wl_preproc.schema import request
 
     key = request.submit_derivative(
         idempotency_key="ab-1", task_type="neural", origin="wl_works",
-        selection=selection, block_ids=[2, 1, 2, 1], payload={},
+        selection=selection, run_numbers=[2, 1, 2, 1], payload={},
     )
 
-    assert len(request.ActivationBlock & key) == 2, (
-        "duplicates must collapse to one row per distinct block id"
+    assert len(request.ActivationRun & key) == 2, (
+        "duplicates must collapse to one row per distinct run number"
     )
-    assert set((request.ActivationBlock & key).to_arrays("block_id")) == {1, 2}
+    assert set((request.ActivationRun & key).to_arrays("run_number")) == {1, 2}
 
 
-def test_an_empty_block_set_is_refused(selection, prefix):
-    """Review round 2, Minor: block_ids=[] would silently create a
+def test_an_empty_run_set_is_refused(selection, prefix):
+    """Review round 2, Minor: run_numbers=[] would silently create a
     role='derivative' Activation covering nothing -- selection_hash("neural",
     []) is a perfectly stable digest (it hashes an empty list like any
     other), so nothing else in this module would have caught it. Rejected
@@ -1148,10 +1140,10 @@ def test_an_empty_block_set_is_refused(selection, prefix):
 
     from wl_preproc.schema import request
 
-    with pytest.raises(dj.DataJointError, match="block"):
+    with pytest.raises(dj.DataJointError, match="at least one run number"):
         request.submit_derivative(
             idempotency_key="empty-1", task_type="neural", origin="wl_works",
-            selection=selection, block_ids=[], payload={},
+            selection=selection, run_numbers=[], payload={},
         )
 
     assert len(request.Request & {"idempotency_key": "empty-1"}) == 0
@@ -1171,7 +1163,7 @@ def test_submit_derivative_before_activate_raises_a_clear_error(req, selection,
     with pytest.raises(dj.DataJointError, match="activate"):
         req.submit_derivative(
             idempotency_key="dv-guard-1", task_type="neural", origin="wl_works",
-            selection=selection, block_ids=[1, 2], payload={},
+            selection=selection, run_numbers=[1, 2], payload={},
         )
 
 
@@ -1187,7 +1179,7 @@ def test_submit_derivative_refuses_to_run_inside_a_transaction(req, selection):
         with pytest.raises(dj.DataJointError, match="do not nest"):
             req.submit_derivative(
                 idempotency_key="dv-guard-2", task_type="neural", origin="wl_works",
-                selection=selection, block_ids=[1, 2], payload={},
+                selection=selection, run_numbers=[1, 2], payload={},
             )
 
     # the outer transaction stayed usable and nothing was written
@@ -1280,17 +1272,6 @@ def test_a_replacement_of_anything_but_the_current_canonical_is_a_conflict(req,
     assert len(req.Activation & selection & "role = 'canonical'") == 2
 
 
-def test_a_canonical_can_name_its_block_set(req, selection):
-    """Section 3: how wl.works leaves out a bad block, on a first canonical
-    or a replacement, recorded in `ActivationBlock`. The builder reads a
-    file's runs now (`ActivationRun`), and the block paths go next."""
-    first = req.submit("k-blocks-1", "neural", "wl_works", selection, {}, None, block_ids=[1, 3])
-    replacement = req.submit_replacement("k-blocks-2", "neural", "wl_works", selection, {}, None,
-                                         supersedes_activation_id=0, block_ids=[2])
-    for key, expected in ((first, [1, 3]), (replacement, [2])):
-        assert sorted(int(b) for b in (req.ActivationBlock & key).to_arrays("block_id")) == expected
-
-
 def test_a_reused_key_for_another_replacement_is_key_reuse(req, selection):
     req.submit("k-reuse-1", "neural", "wl_works", selection, {}, None)
     req.submit_replacement("k-reuse-2", "neural", "wl_works", selection, {}, None, supersedes_activation_id=0)
@@ -1419,8 +1400,8 @@ def test_an_activation_keeps_its_runs_and_each_probes_runs(req):
     assert set(req.ActivationProbeRun.primary_key) == montage | {"probe_serial", "run_number"}
 
 
-# -- Runs, alongside blocks until the block paths are retired (design spec
-# `2026-10-01-session-listing-and-run-requests-design.md` section 3.3).
+# -- Runs (design spec `2026-10-01-session-listing-and-run-requests-design.md`
+# section 3.3).
 
 
 def _runs_of(req, key):
@@ -1452,21 +1433,24 @@ def test_a_replacement_keeps_its_own_runs(req, selection):
 def test_a_derivative_is_its_run_set(req, selection):
     """The requester's decision 3: a derivative selects whole runs, and its
     identity is that set."""
-    one = req.submit_derivative("runs-deriv-1", "neural", "wl_works", selection, [], {}, None, run_numbers=[3, 2])
-    again = req.submit_derivative("runs-deriv-2", "neural", "wl_works", selection, [], {}, None, run_numbers=[2, 3])
-    other = req.submit_derivative("runs-deriv-3", "neural", "wl_works", selection, [], {}, None, run_numbers=[2])
+    one = req.submit_derivative("runs-deriv-1", "neural", "wl_works", selection, [3, 2], {}, None)
+    again = req.submit_derivative("runs-deriv-2", "neural", "wl_works", selection, [2, 3], {}, None)
+    other = req.submit_derivative("runs-deriv-3", "neural", "wl_works", selection, [2], {}, None)
     assert one == again and other != one
     assert (_runs_of(req, one), _runs_of(req, other)) == ([2, 3], [2])
-    assert len(req.ActivationBlock & one) == 0
 
 
-def test_the_selection_hash_separates_run_sets_and_leaves_block_hashes_as_they_were():
+def test_a_derivatives_identity_is_its_task_type_and_run_set():
+    """Design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    section 3.3: the hash takes the task type and the sorted run numbers, as
+    it took block ids."""
     import hashlib
     import json
 
     from wl_preproc.schema.request import selection_hash
 
-    assert selection_hash("neural", [], [1, 2]) == selection_hash("neural", [], [2, 1, 2])
-    assert selection_hash("neural", [], [1, 2]) != selection_hash("neural", [], [1])
-    before = json.dumps({"task_type": "neural", "block_ids": [1, 2]}, sort_keys=True, separators=(",", ":"))
-    assert selection_hash("neural", [2, 1]) == hashlib.blake2b(before.encode(), digest_size=16).hexdigest()
+    assert selection_hash("neural", [1, 2]) == selection_hash("neural", [2, 1, 2])
+    assert selection_hash("neural", [1, 2]) != selection_hash("neural", [1])
+    assert selection_hash("neural", [1, 2]) != selection_hash("ephys", [1, 2])
+    expected = json.dumps({"task_type": "neural", "run_numbers": [1, 2]}, sort_keys=True, separators=(",", ":"))
+    assert selection_hash("neural", [2, 1]) == hashlib.blake2b(expected.encode(), digest_size=16).hexdigest()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/contracts/test_protocol.py tests/responder/test_jobs.py tests/responder/test_http.py tests/schema/test_request.py tests/schema/test_probe_linking.py -q --tb=line -p no:cacheprovider`
Expected: 91 failed, 164 passed. `metadata.blocks` is still required, so each request these tests build without it fails validation: the contract, job and probe-linking tests that build one, and the HTTP tests' bodies, which come back `422` where they expected `200`, `409` or `500`; a test expecting a named refusal gets pydantic's instead (`Regex pattern did not match`). `submit_derivative` still takes `block_ids` before `payload` (16 request tests), and the retirement messages do not yet exist.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/contracts/protocol.py
+++ b/wl_preproc/contracts/protocol.py
@@ -16,6 +16,7 @@ import re
 from typing import Annotated, Any, Literal
 
 from pydantic import BaseModel, ConfigDict, Field, field_validator
+from pydantic_core import PydanticCustomError
 
 SCHEMA_VERSION = 1
 
@@ -265,14 +266,12 @@ class MetadataBundle(BaseModel):
 
     model_config = ConfigDict(extra="forbid", frozen=True)
 
-    # `blocks` is the third field of this shape and is deliberately still
-    # untyped -- not overlooked. The 2026-08-23 handoff verified exactly two
-    # holes in this payload, and this was not one of them; typing it is the same
-    # small piece of work as the two below (`responder/jobs.py::_build_block_rows`
-    # already carries the bounds), left as its own change rather than folded in
-    # unasked. Until then the exported contract is strict about two of its three
-    # list fields, which a reader of `docs/schemas/job_request.json` will notice.
-    blocks: list[dict[str, Any]]
+    # Retired (design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    # section 3.1): a request asserts runs, in `runs` below. Kept in the shape
+    # only so a request still sending blocks is told what replaced them rather
+    # than that the field is unknown; an empty list, which every request sent
+    # while the field was required, says nothing and is accepted.
+    blocks: list[dict[str, Any]] = Field(default=[], json_schema_extra={"maxItems": 0, "deprecated": True})
     montage_boundaries: list[MontageBoundary]
     probes: list[ProbeEntry]
     experimenter: str
@@ -283,6 +282,19 @@ class MetadataBundle(BaseModel):
     # `2026-10-01-session-listing-and-run-requests-design.md` section 3.1).
     runs: list[RunEntry] = []
 
+    @field_validator("blocks")
+    @classmethod
+    def _blocks_are_retired(cls, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
+        # A `PydanticCustomError`, not a `ValueError`: pydantic keeps a
+        # raised `ValueError` itself in the error's `ctx`, which the `422`
+        # body (`responder/handler.py::_error_body`) cannot serialise, and
+        # the refusal went out as a `500`.
+        if blocks:
+            raise PydanticCustomError(
+                "retired_field", "metadata.blocks is retired: a request asserts its runs in metadata.runs, each "
+                "with its works_run_id, as GET /sessions lists them")
+        return blocks
+
 
 class JobRequest(BaseModel):
     model_config = ConfigDict(extra="forbid", frozen=True)
```

```diff
--- a/wl_preproc/responder/jobs.py
+++ b/wl_preproc/responder/jobs.py
@@ -6,13 +6,16 @@ existed: 1c-1 narrowed `submit()` to canonical activations because it had no
 block set, and 1c-2 avoided `submit()` entirely because timebase and coverage
 populate from `Session` keys alone. **The answer was already in the frozen
 contract** (design spec section 1): `contracts.protocol.MetadataBundle`
-carries `blocks` and `montage_boundaries` inbound with EVERY request, and its
-own docstring says why -- "everything wl-preproc needs from the ELN arrives in
-the request payload." `core.Montage`'s own comment says it is "Sourced from
-wl.works `item_insertion` and nothing else", and `core.Block` carries
-`works_block_id` as the link. So recording them here is not measuring or
-guessing at a boundary; it is wl.works' own authored record, arriving by the
-exact route its frozen contract already describes.
+carries `montage_boundaries` -- and, since design spec
+`2026-10-01-session-listing-and-run-requests-design.md`, `runs` -- inbound
+with EVERY request, and its own docstring says why -- "everything wl-preproc
+needs from the ELN arrives in the request payload." `core.Montage`'s own
+comment says it is "Sourced from wl.works `item_insertion` and nothing else".
+So recording a montage here is not measuring or guessing at a boundary; it is
+wl.works' own authored record, arriving by the exact route its frozen
+contract already describes. A run is the other way round: `core.Run` is this
+host's measurement, and what arrives is wl.works' copy of it and its id for
+it, checked against the measurement and kept in `core.RunAssertion`.
 
 **Two corrections carried from Task 4's review, applied here rather than
 re-derived:**
@@ -22,39 +25,38 @@ re-derived:**
    raise -- DataJoint transactions do not nest -- and `submit()`'s own
    docstring says directly that neither the ingest watcher nor the responder
    may wrap it to bundle it with other writes. So `accept()` writes `Montage`
-   and `Block` as two independently idempotent, un-transacted inserts
+   and `RunAssertion` as two independently idempotent, un-transacted inserts
    (`skip_duplicates=True`, exactly `ingest/landing.py`'s own shape and
    reasoning -- "a partial run followed by a re-run converges on the same
    rows" without one), and only then calls `submit`/`submit_derivative`,
    which open and own their own transaction for the `Request`+`Activation`
    pair. See `test_accept_refuses_to_run_inside_a_transaction`.
-2. **`accept()` owns the montage window.** `ActivationBlock`'s own comment
-   names this module's function as "its first writer" and says it "owns
-   enforcing the window" between a montage's `[start_s, end_s)` and the
-   blocks a derivative selects -- a check `submit_derivative` itself does not
-   make (it has no `Montage`/`Block` timing to compare against; verified it
-   currently accepts a block at `[20.0, 24.0)` against a montage of
-   `[0.0, 12.0)`). See `_blocks_outside_window`.
-
-**Existing `Montage`/`Block` rows are never overwritten.** Both inserts below
-use `skip_duplicates=True`: wl.works owns these records, and a later request
-naming different boundaries for a montage or block already on file is
-wl.works correcting its own record -- their call to make explicitly, not
-something to infer from whichever payload happened to arrive most recently.
+2. **`accept()` owns the montage window.** The window between a montage's
+   `[start_s, end_s)` and the runs a file holds is checked here -- a check
+   `submit_derivative` itself does not make, having no `Montage`/`Run`
+   timing to compare against. See `_check_runs`.
+
+**Existing `Montage` rows are never overwritten.** The insert below uses
+`skip_duplicates=True`: wl.works owns this record, and a later request naming
+different boundaries for a montage already on file is wl.works correcting its
+own record -- their call to make explicitly, not something to infer from
+whichever payload happened to arrive most recently. A run already asserted
+under one `works_run_id` and named under another is a `RunIdConflict`, a
+`409`: the two disagree about which run it is.
 
 **Review round 1 (2026-08-16) found four more things, addressed here:**
 
 - **C1 -- validate before writing, not after.** The first draft inserted
-  `Montage`/`Block` and only THEN checked the window, so a rejected request
-  permanently planted the very row that caused its own rejection --
+  `Montage` and `Block` rows and only THEN checked the window, so a rejected
+  request permanently planted the very row that caused its own rejection --
   `skip_duplicates=True` then discarded every later correction, so the
   request that fixed the boundary was rejected too, citing the stale values
   it refused to replace. `accept()` now builds every candidate row, checks
-  montage-existence/window/unknown-block validity against those candidates
-  PLUS whatever is already on record, and only writes anything once every
-  check has passed -- so a rejected request leaves no residue at all. See
-  `test_a_rejected_request_leaves_no_montage_or_block_row_and_a_correction_
-  then_succeeds`.
+  montage existence and every run against those candidates PLUS whatever is
+  already on record, and only writes anything once every check has passed --
+  so a rejected request leaves no residue at all. See
+  `test_a_rejected_request_leaves_no_montage_or_run_assertion_and_a_
+  correction_then_succeeds`.
 - **C2 -- `selection["session_datetime"]` is coerced, not assumed to already
   be a `datetime`.** JSON has no datetime type and `docs/schemas/
   job_request.json` declares `selection` as a bare
@@ -81,17 +83,16 @@ something to infer from whichever payload happened to arrive most recently.
   matching `ingest/params.py`'s own stated convention** ("checked here,
   inside the validation step, rather than left for ... insert to discover as
   a raw `pymysql.err.DataError` -- which Task 8's watcher does not
-  special-case"). `montage_id`/`block_id` range, `task_type`/`works_block_id`
-  length, and `start_s`/`end_s` finiteness are all checked against the real
-  column bounds before any insert is attempted; `selection["block_ids"]`
-  naming an id with no `Block` anywhere (I4) raises `ValueError` rather than
-  being silently excluded from the window check.
+  special-case"). The `montage_id` range is checked against the real column
+  bound before any insert is attempted; the montage's and each run's own
+  bounds now live on `contracts.protocol.MontageBoundary` and `RunEntry`, so
+  a request breaking them cannot be built.
 
 **The whole-branch review (2026-08-16) found one more, fixed here:**
 
 - **C1 -- a job for a session this host has never ingested was a `500`,
   which the protocol document tells wl.works to retry forever.** `accept()`
-  validated montage existence, block existence, the window, subject length,
+  validated montage existence, the window, subject length,
   every column bound and the DATETIME floor -- but not that `Session`
   itself exists, so `core.Montage.insert` hit the foreign key and the
   resulting `IntegrityError` became a retryable `500`. Creating the session
@@ -99,13 +100,13 @@ something to infer from whichever payload happened to arrive most recently.
   `_require_landed_session` for the full reasoning, the measured
   before/after, and why the answer is `422` rather than `409`.
 
-**A design gap this task does not fix, recorded on the `Block` write below
-where it applies:** `core.Block`'s own comment says its boundaries are
-"decoded from event codes and cross-validated against those rows", and the
-frozen parent spec section 4.2 says the same -- `start_s`/`end_s` are
-specified as wl-preproc's own MEASUREMENT. This function instead writes
-wl.works' ASSERTED numbers into that same column, and `skip_duplicates`
-makes that permanent. Flagged, not solved, here.
+**A design gap this module once flagged is closed.** `core.Block` was
+specified as wl-preproc's own measurement, and this function wrote wl.works'
+asserted numbers into it, permanently. A request now asserts runs, which
+`core.Run` measures from the recording; what wl.works asserts is checked
+against that measurement as the request arrives and kept apart from it, in
+`core.RunAssertion` (design spec
+`2026-10-01-session-listing-and-run-requests-design.md` section 3).
 """
 
 from __future__ import annotations
@@ -126,27 +127,24 @@ from wl_preproc.schema import request as schema_request
 _REQUIRED_SELECTION_KEYS = ("session_datetime", "montage_id")
 
 # Column bounds, read directly from wl_preproc/schema/core.py's declared
-# types -- neither `montage_id` nor `block_id` declares "unsigned" (unlike
-# core.Segment.segment_barcode's "int unsigned"), so both are SIGNED ranges.
+# types -- `montage_id` does not declare "unsigned" (unlike
+# core.Segment.segment_barcode's "int unsigned"), so its range is SIGNED.
 # Checked before any insert, matching ingest/params.py's own stated
 # convention for paramset_type/PARAMSET_TYPE_MAX_LEN: a value that is a
 # syntactically fine Python int/str can still be too big/long for the column
 # it is about to be inserted into, and finding that out from a raw
 # pymysql.err.DataError leaves Task 8's handler with an exception type its
 # documented ValueError/DataJointError contract does not cover -- confirmed
-# with each guard removed in turn against a live column: 1406 "Data too
-# long" (task_type, works_block_id), 1264 "Out of range value" (block_id),
-# and 1265 "Data truncated" (a non-finite float into start_s/end_s -- not
-# 1264, corrected here after actually reproducing it rather than assuming
-# the same errno as the integer case). None of the three appears in
-# DataJoint's MySQL adapter's translated-error list.
+# with each guard removed in turn against a live column, when this module
+# also wrote core.Block: 1406 "Data too long" (a string column), 1264 "Out of
+# range value" (an integer one), and 1265 "Data truncated" (a non-finite
+# float into a double -- not 1264, corrected after actually reproducing it
+# rather than assuming the same errno as the integer case). None of the three
+# appears in DataJoint's MySQL adapter's translated-error list.
 _MONTAGE_ID_RANGE = (-128, 127)  # core.Montage.montage_id : tinyint
 # request.Activation.activation_id : int, and a superseded one is never
 # negative: the allocator starts at 0.
 _ACTIVATION_ID_RANGE = (0, 2**31 - 1)
-_BLOCK_ID_RANGE = (-32768, 32767)  # core.Block.block_id : smallint
-_TASK_TYPE_MAX_LEN = 32  # core.Block.task_type : varchar(32)
-_WORKS_BLOCK_ID_MAX_LEN = 64  # core.Block.works_block_id : varchar(64)
 # MySQL's DATETIME floor. Python's own datetime.MINYEAR (1) is far below it,
 # and DataJoint's bare `datetime` column type validates nothing on its own.
 _DATETIME_MIN_YEAR = 1000
@@ -210,13 +208,15 @@ def _coerce_session_datetime(value) -> datetime.datetime:
     )
 
 
-def _lifecycle_role(selection: dict, block_ids: list) -> bool:
+def _lifecycle_role(selection: dict, run_numbers: list[int]) -> bool:
     """Whether the request asks for a canonical, from `selection`'s optional
     `role` and `supersedes_activation_id` (design spec
-    `2026-09-30-canonical-lifecycle-design.md` section 3). Without a `role`
-    a request means what it always meant: `block_ids` makes a derivative,
-    none a canonical over the whole montage. Raises `ValueError` (a `422`)
-    for the combinations section 3 refuses, before anything is written."""
+    `2026-09-30-canonical-lifecycle-design.md` section 3). Without a `role`,
+    `run_numbers` makes a derivative and none a canonical over the whole
+    montage, as `block_ids` did (design spec
+    `2026-10-01-session-listing-and-run-requests-design.md` section 3.1).
+    Raises `ValueError` (a `422`) for the combinations section 3 refuses,
+    before anything is written."""
     role = selection.get("role")
     if role not in (None, "canonical", "derivative"):
         raise ValueError(f"selection['role'] must be 'canonical' or 'derivative', got {role!r}")
@@ -229,9 +229,9 @@ def _lifecycle_role(selection: dict, block_ids: list) -> bool:
             )
         _reject_out_of_range_int(selection["supersedes_activation_id"],
                                  name="selection['supersedes_activation_id']", bounds=_ACTIVATION_ID_RANGE)
-    if role == "derivative" and not block_ids:
-        raise ValueError("selection['role'] 'derivative' needs block_ids: a derivative is its block set")
-    return role == "canonical" or (role is None and not block_ids)
+    if role == "derivative" and not run_numbers:
+        raise ValueError("selection['role'] 'derivative' needs run_numbers: a derivative is its run set")
+    return role == "canonical" or (role is None and not run_numbers)
 
 
 class RunIdConflict(Exception):
@@ -340,22 +340,6 @@ def _reject_out_of_range_int(value, *, name: str, bounds: tuple[int, int]) -> No
         raise ValueError(f"{name}={value} is outside the column's range [{low}, {high}]")
 
 
-def _reject_oversized_str(value, *, name: str, max_len: int) -> None:
-    if not isinstance(value, str):
-        raise ValueError(f"{name} must be a string, got {value!r}")
-    if len(value) > max_len:
-        raise ValueError(
-            f"{name} is {len(value)} characters, over the {max_len}-character column limit"
-        )
-
-
-def _reject_non_finite(value, *, name: str) -> None:
-    if isinstance(value, bool) or not isinstance(value, (int, float)):
-        raise ValueError(f"{name} must be a number, got {value!r}")
-    if not math.isfinite(value):
-        raise ValueError(f"{name}={value} is not a finite number")
-
-
 def _require_landed_session(session_key: dict) -> None:
     """`ValueError` -- and so `422`, not `500` -- when no `Session` row
     exists for this request's `(subject, session_datetime)`.
@@ -413,7 +397,7 @@ def _require_landed_session(session_key: dict) -> None:
             f"session {session_key['subject']}/"
             f"{session_key['session_datetime'].isoformat()} is not yet on "
             "record on this host: no Session row exists for it, so there is "
-            "nothing to attach a montage, a block or a request to. wl.works "
+            "nothing to attach a montage, a run or a request to. wl.works "
             "knows a session exists from the ELN before its data transfer "
             "lands here; until ingest has landed it, this host cannot accept "
             "a job for it. Resend once the transfer has completed."
@@ -448,77 +432,6 @@ def _build_montage_rows(
     ]
 
 
-def _build_block_rows(session_key: dict, blocks: list[dict]) -> list[dict]:
-    """`Block` rows this request WOULD write -- validated, not yet inserted.
-    See `accept()`'s two-phase structure (review C1)."""
-    rows = []
-    for block in blocks:
-        b_block_id = block["block_id"]
-        _reject_out_of_range_int(
-            b_block_id, name="metadata.blocks[].block_id", bounds=_BLOCK_ID_RANGE
-        )
-        _reject_oversized_str(
-            block["task_type"], name="metadata.blocks[].task_type", max_len=_TASK_TYPE_MAX_LEN
-        )
-        _reject_non_finite(block["start_s"], name="metadata.blocks[].start_s")
-        _reject_non_finite(block["end_s"], name="metadata.blocks[].end_s")
-        works_block_id = block.get("works_block_id")
-        if works_block_id is not None:
-            _reject_oversized_str(
-                works_block_id,
-                name="metadata.blocks[].works_block_id",
-                max_len=_WORKS_BLOCK_ID_MAX_LEN,
-            )
-        rows.append(
-            {
-                **session_key,
-                "block_id": b_block_id,
-                "task_type": block["task_type"],
-                "start_s": block["start_s"],
-                "end_s": block["end_s"],
-                "works_block_id": works_block_id,
-            }
-        )
-    return rows
-
-
-def _effective_block_rows(
-    block_ids: list[int], *, session_key: dict, candidate_blocks: dict[int, dict]
-) -> tuple[dict[int, dict], list[int]]:
-    """For every id in `block_ids`: the `Block` row already on record, or --
-    when none exists yet -- the one this SAME request would insert (still
-    unwritten at this point; see `accept()`'s two-phase structure). Returns
-    `(found, unknown)` rather than raising, so `accept()` can tell "no Block
-    anywhere names this id" (review I4, its own `ValueError`) apart from "this
-    Block exists and is outside the window" instead of one case silently
-    hiding inside the other.
-    """
-    existing = {row["block_id"]: row for row in (core.Block & session_key).to_dicts()}
-    found: dict[int, dict] = {}
-    unknown: list[int] = []
-    for block_id in sorted(set(block_ids)):
-        row = existing.get(block_id) or candidate_blocks.get(block_id)
-        if row is None:
-            unknown.append(block_id)
-        else:
-            found[block_id] = row
-    return found, unknown
-
-
-def _blocks_outside_window(effective: dict[int, dict], montage_row: dict) -> list[str]:
-    """Every entry in `effective` whose own `[start_s, end_s)` is not fully
-    contained in the montage's. `ActivationBlock`'s own comment: a block the
-    montage does not cover in time is a block the sort must not cover
-    either, and nothing below the responder checks this (`submit_derivative`
-    has no `Montage` to compare against).
-    """
-    return [
-        f"block {block_id} [{row['start_s']}, {row['end_s']})"
-        for block_id, row in effective.items()
-        if row["start_s"] < montage_row["start_s"] or row["end_s"] > montage_row["end_s"]
-    ]
-
-
 def _record_subject_details(subject: str, details) -> None:
     """wl.works' own record of the animal, when the request carries it,
     written into element-animal's tables: `Subject.sex`,
@@ -577,25 +490,29 @@ def _record_probe_reports(session_key: dict, probes, prefix: str) -> None:
 
 
 def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
-    """A validated `JobRequest` becomes `Montage`/`Block`/`Request`/`Activation`
-    rows. Design spec section 6.1. Returns the `Activation` primary key.
+    """A validated `JobRequest` becomes `Montage`/`RunAssertion`/`Request`/
+    `Activation` rows, with the activation's runs and each probe's. Design
+    spec section 6.1, and design spec
+    `2026-10-01-session-listing-and-run-requests-design.md` section 3.
+    Returns the `Activation` primary key.
 
     Raises `ValueError` for a request that cannot be honoured: a `selection`
-    missing `session_datetime` or `montage_id`; a `metadata.subject` longer
-    than `landing.SUBJECT_MAX_LEN`; a `session_datetime` that is neither a
+    missing `session_datetime` or `montage_id`; the retired
+    `selection.block_ids`; a `metadata.subject` longer than
+    `landing.SUBJECT_MAX_LEN`; a `session_datetime` that is neither a
     `datetime.datetime` nor a parseable ISO-8601 string; a
     `(subject, session_datetime)` with no `Session` row on this host yet
     (`_require_landed_session` -- the ordinary ELN-before-transfer case,
     which used to reach the database and come back as a `500` telling
-    wl.works to retry forever); a `montage_id`,
-    `block_id`, `task_type`, `works_block_id`, `start_s` or `end_s` that
-    cannot fit the column it would be written to; a `montage_id` with no
-    boundary on record and none supplied in this request either; a
-    `block_ids` entry naming no `Block` anywhere; or a `block_ids` entry
-    naming a block outside its montage's window. All of these are checked
+    wl.works to retry forever); a `montage_id` that cannot fit its column; a
+    `montage_id` with no boundary on record and none supplied in this request
+    either; or any run that does not match what this host measured
+    (`_check_runs`, `_check_probe_runs`). Raises `RunIdConflict` for a run
+    already asserted under another `works_run_id`. All of these are checked
     against BUILT-BUT-NOT-YET-WRITTEN candidate rows before anything is
     actually inserted (review C1): a rejected request leaves no `Montage` or
-    `Block` residue behind for a later, corrected request to trip over.
+    `RunAssertion` residue behind for a later, corrected request to trip
+    over.
 
     Session identity is `metadata.subject` plus `selection["session_datetime"]`,
     normalised through `landing.to_naive_utc` -- the one conversion every
@@ -606,24 +523,22 @@ def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
     `selection`'s own `subject`, if wl.works ever sends one, is not read: the
     ELN's record of who this is is `metadata.subject`.
 
-    `selection["block_ids"]`, when present and non-empty, makes this a
-    derivative (`submit_derivative`); its absence, or an empty list, makes it
-    canonical (`submit`).
+    `selection["run_numbers"]`, when present and non-empty, makes this a
+    derivative holding those runs (`submit_derivative`); its absence, or an
+    empty list, makes it a canonical holding every measured run of its
+    montage, with each probe's runs in `selection["probe_runs"]` (`submit`).
     """
     selection = request.selection
     _require_selection_keys(selection)
     metadata = request.metadata
-    # Before anything reads the database: a malformed lifecycle selection
-    # is the caller's to fix, whatever this host holds. A request names runs
-    # or blocks (design spec `2026-10-01-session-listing-and-run-requests-design.md`
-    # section 3.1).
+    # Before anything reads the database: a malformed selection is the
+    # caller's to fix, whatever this host holds.
+    if selection.get("block_ids"):
+        raise ValueError("selection.block_ids is retired: a derivative names its runs in selection.run_numbers, "
+                         "and a canonical holds every run of its montage, each probe's in selection.probe_runs")
     run_numbers = _run_numbers(selection.get("run_numbers") or [], name="selection.run_numbers")
     probe_runs = selection.get("probe_runs")
-    names_runs = bool(metadata.runs or run_numbers or probe_runs is not None)
-    if names_runs and (selection.get("block_ids") or metadata.blocks):
-        raise ValueError("a request names runs or blocks, not both: metadata.runs, selection.run_numbers and "
-                         "selection.probe_runs, or metadata.blocks and selection.block_ids")
-    canonical = _lifecycle_role(selection, selection.get("block_ids") or run_numbers)
+    canonical = _lifecycle_role(selection, run_numbers)
     if canonical and run_numbers:
         raise ValueError("selection.run_numbers is a derivative's: a canonical holds every run of its montage")
     if not canonical and probe_runs is not None:
@@ -649,9 +564,7 @@ def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
     # (review C1). ----
 
     montage_rows = _build_montage_rows(session_key, metadata.montage_boundaries)
-    block_rows = _build_block_rows(session_key, metadata.blocks)
     candidate_montages = {row["montage_id"]: row for row in montage_rows}
-    candidate_blocks = {row["block_id"]: row for row in block_rows}
 
     schema_request.activate(prefix=prefix)
 
@@ -667,53 +580,21 @@ def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
             "metadata.montage_boundaries did not supply one either"
         )
 
-    block_ids = selection.get("block_ids") or []
-
     # Correction 2: accept() owns the montage window (module docstring).
-    if block_ids:
-        effective, unknown = _effective_block_rows(
-            block_ids, session_key=session_key, candidate_blocks=candidate_blocks
-        )
-        if unknown:
-            raise ValueError(
-                "selection names block id(s) with no Block on record and "
-                f"none supplied in this request either: {unknown}"
-            )
-        offending = _blocks_outside_window(effective, montage_row)
-        if offending:
-            raise ValueError(
-                f"selection names block(s) outside montage {montage_id}'s window "
-                f"[{montage_row['start_s']}, {montage_row['end_s']}): " + "; ".join(offending)
-            )
+    file_runs, run_assertion_rows = _check_runs(session_key, montage_row, metadata.runs, run_numbers, canonical)
+    if canonical:
+        _check_probe_runs(metadata.probes, probe_runs, file_runs)
 
     # ---- Phase 2: every check above passed. Only now does anything get
     # written. ----
 
-    # Step 1 (design spec section 6.1): Montage rows, insert-if-absent.
-    file_runs, run_assertion_rows = [], []
-    if names_runs:
-        file_runs, run_assertion_rows = _check_runs(session_key, montage_row, metadata.runs, run_numbers, canonical)
-        if canonical:
-            _check_probe_runs(metadata.probes, probe_runs, file_runs)
-
     _record_subject_details(metadata.subject, metadata.subject_details)
     _record_probe_reports(session_key, metadata.probes, prefix)
+    # Step 1 (design spec section 6.1): Montage rows, insert-if-absent.
     if montage_rows:
         core.Montage.insert(montage_rows, skip_duplicates=True)
-
-    # Step 2: Block rows, insert-if-absent, works_block_id set -- the link
-    # back to wl.works' own authored row (core.Block's own comment).
-    #
-    # NOTE -- a design gap this task does not fix, flagged rather than
-    # solved: core.Block's own comment says its boundaries are "decoded from
-    # event codes and cross-validated against those rows", and the frozen
-    # parent spec section 4.2 says the same -- start_s/end_s are specified as
-    # wl-preproc's own MEASUREMENT. This writes wl.works' ASSERTED numbers
-    # into that same column instead, and skip_duplicates makes that
-    # permanent: 1c-4's decoder, when built, will find the slot already
-    # occupied by an asserted value rather than a decoded one.
-    if block_rows:
-        core.Block.insert(block_rows, skip_duplicates=True)
+    # Step 2: wl.works' id and copy of each run it asserts, insert-if-absent;
+    # `_check_runs` has already refused a run named under a second id.
     if run_assertion_rows:
         core.RunAssertion.insert(run_assertion_rows, skip_duplicates=True)
 
@@ -748,10 +629,9 @@ def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
             task_type=request.domain,
             origin="wl_works",
             selection=montage_key,
-            block_ids=list(block_ids),
+            run_numbers=file_runs,
             payload=payload,
             requested_by=metadata.experimenter,
-            run_numbers=file_runs,
         )
 
     if selection.get("supersedes_activation_id") is not None:
@@ -763,7 +643,6 @@ def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
             payload=payload,
             requested_by=metadata.experimenter,
             supersedes_activation_id=selection["supersedes_activation_id"],
-            block_ids=list(block_ids),
             run_numbers=file_runs,
             probe_runs=probe_runs,
         )
@@ -774,7 +653,6 @@ def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
         selection=montage_key,
         payload=payload,
         requested_by=metadata.experimenter,
-        block_ids=list(block_ids),
         run_numbers=file_runs,
         probe_runs=probe_runs,
     )
```

```diff
--- a/wl_preproc/schema/request.py
+++ b/wl_preproc/schema/request.py
@@ -37,12 +37,12 @@ second ``activation_id`` is never allocated — every activation this function
 writes is ``activation_id=0``. That is correct for a canonical activation
 (parent spec section 8.3: exactly one current per (session, montage)) and
 wrong for a derivative (section 8.3: "any hand-picked subset… unbounded,
-additive"), which needs a real allocator and a block set to key on.
-``submit()``'s selection carries no block set, so it has no way to form a
+additive"), which needs a real allocator and a run set to key on.
+``submit()``'s selection carries no run set, so it has no way to form a
 derivative — accepting a ``role`` parameter here would silently hand back the
 canonical activation's key for any caller that asked for a derivative. So the
 parameter is gone rather than half-supported; making derivatives real belongs
-to the responder (design spec section 9.1), once it can supply a block set —
+to the responder (design spec section 9.1), once it can supply a run set —
 which is what ``submit_derivative``, below, does. Because a derivative can
 now exist on a selection with no canonical yet, the dedupe's own query is
 scoped to ``role="canonical"`` (added alongside ``submit_derivative`` —
@@ -147,7 +147,7 @@ class Activation(dj.Manual):
     created_at  : datetime
     supersedes = null : int     # a regenerated canonical points at the old one
     # Null for a canonical activation, whose identity is (session, montage)
-    # per section 8.3. Set for a derivative, whose identity is its block set --
+    # per section 8.3. Set for a derivative, whose identity is its run set --
     # which is why section 11.3's "a request whose (selection, task type) is
     # already in flight returns the running one" is a lookup here in the
     # common, uncontested case. The one exception: submit_derivative's
@@ -218,12 +218,14 @@ class ActivationProbeRun(dj.Manual):
     """
 
 
-def selection_hash(task_type: str, block_ids: list[int], run_numbers: list[int] | tuple[int, ...] = ()) -> str:
-    """Content hash of a derivative's identity: its task type and block set.
+def selection_hash(task_type: str, run_numbers: list[int] | tuple[int, ...]) -> str:
+    """Content hash of a derivative's identity: its task type and run set
+    (design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    section 3.3; its block set until then).
 
-    Sorted and de-duplicated first, because the block set is a *set*: two
-    requests naming the same blocks in a different order, or naming the same
-    block twice, are the same selection, and hashing them differently would
+    Sorted and de-duplicated first, because the run set is a *set*: two
+    requests naming the same runs in a different order, or naming the same
+    run twice, are the same selection, and hashing them differently would
     start a second run for work already in flight (section 11.3). `set(...)`
     is what collapses a repeat; `sorted()` alone would not, since `[1, 1, 2]`
     is already sorted and stays three elements long.
@@ -240,7 +242,7 @@ def selection_hash(task_type: str, block_ids: list[int], run_numbers: list[int]
     calls `paramset.content_hash(declared.params)` rather than
     reimplementing `json.dumps`'s argument list, precisely so the two cannot
     drift. Here they are two functions hashing two different things --
-    a parameter mapping there, a `(task_type, block_ids)` pair here -- so
+    a parameter mapping there, a `(task_type, run_numbers)` pair here -- so
     one function taking both shapes would be a worse abstraction than two
     that happen to agree. What they must keep agreeing on is `digest_size`,
     and the columns they land in do not make that symmetrical:
@@ -268,12 +270,7 @@ def selection_hash(task_type: str, block_ids: list[int], run_numbers: list[int]
     happens to existing `selection_hash` rows; it is not a one-line edit in
     either function.
     """
-    selected = {"task_type": task_type, "block_ids": sorted(set(block_ids))}
-    # A run set joins the hash only when there is one, so every block-only
-    # hash already on file is unchanged (design spec
-    # `2026-10-01-session-listing-and-run-requests-design.md` section 3.3).
-    if run_numbers:
-        selected["run_numbers"] = sorted(set(run_numbers))
+    selected = {"task_type": task_type, "run_numbers": sorted(set(run_numbers))}
     payload = json.dumps(selected, sort_keys=True, separators=(",", ":"))
     return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()
 
@@ -384,14 +381,14 @@ def _reject_key_reuse(
     compared against — i.e., when `produced` above is non-empty because
     THIS key's earlier call inserted a fresh derivative `Activation`, rather
     than deduping onto one created under a different key. In that case, a
-    later call under the same key naming a different block set is now
+    later call under the same key naming a different run set is now
     caught, not just a different session or montage.
 
     It does **not** close the residual the previous paragraph describes. If
     the key's first submission instead deduped onto a pre-existing
     derivative created by some OTHER key, no row names this key at all —
     `produced` is empty, exactly as above — and a second call under this key
-    with a *different* block set is silently accepted, allocating its own
+    with a *different* run set is silently accepted, allocating its own
     activation, rather than refused. That is the same hole the previous
     paragraph describes, for a derivative rather than a canonical, and this
     column alone cannot close it: closing it needs a selection recorded on
@@ -439,7 +436,7 @@ def _reject_key_reuse(
         # KeyReuseError's own docstring for why the distinction has to be a
         # type: responder/server.py's seam maps exactly this, and
         # SupersedeConflict, to 409; every other raise site in this file
-        # (not-activated, in_transaction, empty block_ids, allocation
+        # (not-activated, in_transaction, empty run_numbers, allocation
         # exhaustion, a replacement's lock) must stay 500.
         raise KeyReuseError(
             f"idempotency key {idempotency_key!r} is already recorded against a "
@@ -458,11 +455,11 @@ def submit(
     selection: dict,
     payload: dict,
     requested_by: str | None = None,
-    block_ids: list[int] | tuple[int, ...] = (),
     run_numbers: list[int] | tuple[int, ...] = (),
     probe_runs: dict[str, list[int]] | None = None,
 ) -> dict:
-    """Record a request and the canonical activation it selects, atomically.
+    """Record a request and the canonical activation it selects, atomically,
+    with its runs and each probe's (`_record_run_sets`).
 
     Returns the ``Activation`` key. Both rows land or neither does: a ``Request``
     without its ``Activation`` is an accepted request that will never run, which
@@ -591,15 +588,6 @@ def submit(
             },
             skip_duplicates=True,
         )
-        # A canonical that names its block set -- how wl.works leaves out a
-        # bad block (design spec `2026-09-30-canonical-lifecycle-design.md`
-        # section 3). Without one, the builder takes every block in the
-        # montage, as before.
-        if block_ids:
-            ActivationBlock.insert(
-                [{**key, "block_id": block_id} for block_id in sorted(set(block_ids))],
-                skip_duplicates=True,
-            )
         _record_run_sets(key, run_numbers, probe_runs)
         return key
 
@@ -682,13 +670,12 @@ def submit_replacement(
     requested_by: str | None = None,
     *,
     supersedes_activation_id: int,
-    block_ids: list[int] | tuple[int, ...] = (),
     run_numbers: list[int] | tuple[int, ...] = (),
     probe_runs: dict[str, list[int]] | None = None,
 ) -> dict:
     """Record a request and the canonical activation that replaces the
     montage's current one, `supersedes_activation_id`: at the montage's next free
-    activation id, with `supersedes` set and, if given, its block set
+    activation id, with `supersedes` set, and its runs and each probe's
     (design spec `2026-09-30-canonical-lifecycle-design.md` section 3, case
     3). The superseded activation and its file are left exactly as they are.
 
@@ -796,10 +783,6 @@ def submit_replacement(
                     f"submit_replacement: exhausted {_MAX_DERIVATIVE_ALLOCATE_ATTEMPTS} "
                     f"attempts to allocate an activation_id for {montage_key!r}"
                 )
-            if block_ids:
-                ActivationBlock.insert(
-                    [{**key, "block_id": block_id} for block_id in sorted(set(block_ids))]
-                )
             _record_run_sets(key, run_numbers, probe_runs)
             return key
 
@@ -938,24 +921,25 @@ def submit_derivative(
     task_type: str,
     origin: str,
     selection: dict,
-    block_ids: list[int],
+    run_numbers: list[int] | tuple[int, ...],
     payload: dict,
     requested_by: str | None = None,
-    run_numbers: list[int] | tuple[int, ...] = (),
 ) -> dict:
-    """Record a request and the derivative activation its block set selects.
+    """Record a request and the derivative activation its run set selects.
 
     Mirrors ``submit()``'s structure — the activation guard, the no-nesting
     guard, ``_reject_key_reuse``, one transaction — and differs in exactly
     three places, below. See the module docstring's "``submit()`` only ever
     produces canonical activations" paragraph for why ``submit()`` itself
-    cannot do this: its selection carries no block set, so it has nothing to
+    cannot do this: its selection carries no run set, so it has nothing to
     key a derivative's identity on.
 
     **1. The dedupe is on the selection, not the key.**
-    ``digest = selection_hash(task_type, block_ids)`` is this derivative's
-    identity (parent spec section 8.3: a derivative's identity is its block
-    set, unlike a canonical's, which is its (session, montage)). Before
+    ``digest = selection_hash(task_type, run_numbers)`` is this derivative's
+    identity (parent spec section 8.3: a derivative's identity is its run
+    set -- its block set until design spec
+    `2026-10-01-session-listing-and-run-requests-design.md` -- unlike a
+    canonical's, which is its (session, montage)). Before
     inserting anything, an existing ``Activation`` already carrying that hash
     for this session and montage is returned as-is — section 11.3's "a
     request whose (selection, task type) is already in flight returns the
@@ -1060,10 +1044,10 @@ def submit_derivative(
     a derivative may legitimately be requested before any canonical exists.
 
     **3. What gets written.** ``role='derivative'``, ``selection_hash=digest``,
-    and one ``ActivationBlock`` row per distinct block id — sorted and
+    and one ``ActivationRun`` row per distinct run number — sorted and
     de-duplicated the same way ``selection_hash`` itself canonicalises
-    ``block_ids``, since ``ActivationBlock`` carries no columns beyond its
-    primary key: a block is either in the selection or it is not, and there
+    ``run_numbers``, since ``ActivationRun`` carries no columns beyond its
+    primary key: a run is either in the selection or it is not, and there
     is nothing multiplicity could mean here.
 
     **A derivative never supersedes a canonical.** ``supersedes`` is written
@@ -1075,7 +1059,7 @@ def submit_derivative(
     and no code path should ever write its ``supersedes``.
 
     Returns the ``Activation`` key. Reusing an ``idempotency_key`` for a
-    different request — now including one that names a different block set —
+    different request — now including one that names a different run set —
     raises ``DataJointError``; see ``_reject_key_reuse``.
     """
     if not schema.is_activated():
@@ -1095,16 +1079,16 @@ def submit_derivative(
             "one. Call it as its own unit of work; see submit()'s docstring."
         )
 
-    if not block_ids and not run_numbers:
+    if not run_numbers:
         # Checked before opening the transaction, alongside the two guards
-        # above: block_ids=[] is not a smaller selection, it is not a
+        # above: run_numbers=[] is not a smaller selection, it is not a
         # selection at all. Without this, selection_hash("neural", [])
         # still produces a stable digest, so it would silently succeed —
-        # writing a role='derivative' Activation with zero ActivationBlock
+        # writing a role='derivative' Activation with zero ActivationRun
         # rows, a derivative covering nothing, which section 8.3's "any
         # hand-picked subset" does not describe (review round 2, Minor).
         raise dj.DataJointError(
-            "submit_derivative() needs at least one run or block id: an empty "
+            "submit_derivative() needs at least one run number: an empty "
             "selection would create a derivative covering nothing, which is "
             "not a valid selection."
         )
@@ -1114,7 +1098,7 @@ def submit_derivative(
     montage_key = {
         k: selection[k] for k in ("subject", "session_datetime", "montage_id")
     }
-    digest = selection_hash(task_type, block_ids, run_numbers)
+    digest = selection_hash(task_type, run_numbers)
     selection_key = {**montage_key, "selection_hash": digest}
 
     with dj.conn().transaction:
@@ -1212,10 +1196,6 @@ def submit_derivative(
                 "either sustained genuine contention or a stuck retry loop."
             )
 
-        if block_ids:
-            ActivationBlock.insert(
-                [{**key, "block_id": block_id} for block_id in sorted(set(block_ids))]
-            )
         _record_run_sets(key, run_numbers, None)
         return key
 
```

```diff
--- a/wl_preproc/schema/paramset.py
+++ b/wl_preproc/schema/paramset.py
@@ -80,7 +80,7 @@ def content_hash(params: dict) -> str:
     **`schema/request.py::selection_hash` is a second function with this
     same body, deliberately not merged with it. Read this before changing
     `digest_size`.** The two hash different things — a parameter mapping
-    here, a `(task_type, block_ids)` pair there — so one function taking
+    here, a `(task_type, run_numbers)` pair there — so one function taking
     both shapes would be the worse abstraction; what they must keep
     agreeing on is the primitive and the digest size. The columns they land
     in are sized differently, and that makes a bump break them in opposite
```

Then re-export the contracts, which changes `job_request.json` by:

```diff
--- a/docs/schemas/job_request.json
+++ b/docs/schemas/job_request.json
@@ -72,10 +72,13 @@
       "description": "Everything wl-preproc needs from the ELN, carried inbound with the request.",
       "properties": {
         "blocks": {
+          "default": [],
+          "deprecated": true,
           "items": {
             "additionalProperties": true,
             "type": "object"
           },
+          "maxItems": 0,
           "title": "Blocks",
           "type": "array"
         },
@@ -129,7 +132,6 @@
         }
       },
       "required": [
-        "blocks",
         "montage_boundaries",
         "probes",
         "experimenter",
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 255 passed.

Then: `.venv/bin/python -m pytest tests/contracts tests/responder tests/schema/test_request.py tests/schema/test_probe_linking.py tests/cli/test_schemas_export.py -q -p no:cacheprovider`. Expected: 376 passed.

- [ ] **Step 5: Mutation checks.**
  - T2a (`schema/request.py::selection_hash`): `"run_numbers": sorted(set(run_numbers))` becomes `list(run_numbers)` [`test_a_derivatives_identity_is_its_task_type_and_run_set`].
  - T3c (`responder/jobs.py::_check_runs`, Task 3's line): the window's `< montage_row["end_s"]` becomes `<=` [`test_accept_treats_the_montage_window_as_half_open`].
  - T5a (`responder/jobs.py`): `if selection.get("block_ids"):` becomes `if False:` [`test_the_retired_block_selection_is_refused_naming_its_replacement`, its two `block_ids` cases].
  - T5b (`contracts/protocol.py`): `if blocks:` becomes `if False:` [`test_metadata_blocks_are_refused_naming_metadata_runs`].
  - T5c: the custom error becomes a `ValueError` [`test_the_retired_blocks_are_refused_over_http_naming_metadata_runs`].
  - T5d (`responder/jobs.py::_lifecycle_role`): `if role == "derivative" and not run_numbers:` becomes `if False:` [`test_the_retired_block_selection_is_refused_naming_its_replacement`, its derivative-role case].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/contracts/protocol.py wl_preproc/responder/jobs.py wl_preproc/schema/request.py wl_preproc/schema/paramset.py docs/schemas/job_request.json tests/contracts/test_protocol.py tests/responder/test_jobs.py tests/responder/test_http.py tests/schema/test_probe_linking.py tests/schema/test_request.py
git commit -m "feat(responder): a request names runs only -- the block fields are retired with a 422 naming their replacements

metadata.blocks is refused by the contract (maxItems 0, deprecated) and
selection.block_ids by accept(), each naming metadata.runs and
selection.run_numbers. Every request is checked against the measured runs;
accept() writes no core.Block and submit*() no ActivationBlock. A derivative's
identity is its task type and sorted run numbers.

<trailer lines>"
```

---

### Task 6: `core.Block`, `ActivationBlock`, `BlockCoverage` and `block_agreement` are retired

**Files:**
- Modify: `wl_preproc/schema/core.py`, `wl_preproc/schema/request.py`, `wl_preproc/schema/coverage.py`, `wl_preproc/schema/timebase.py`, `wl_preproc/events/agreement.py`, `wl_preproc/responder/jobs.py`, `wl_preproc/daemon.py`, `wl_preproc/cli/deleting.py`, `wl_preproc/timebase/coverage.py`, `wl_preproc/synth/recipe.py`, and comments in `wl_preproc/contracts/protocol.py`, `wl_preproc/schema/ephys.py`, `wl_preproc/schema/events.py`
- Test: `tests/schema/test_core.py`, `tests/events/test_agreement.py`, `tests/schema/test_timebase.py`, `tests/schema/test_coverage.py`, `tests/timebase/test_coverage_rules.py`, `tests/timebase/test_segments.py`, `tests/schema/test_daemon.py`, `tests/synth/test_recipe.py`, `tests/archive/test_reclaim.py`, `tests/cli/test_consensus_report.py`, `tests/cli/test_detect_report.py`, `tests/cli/test_eye_report.py`

**Interfaces — consumes:** Task 5: nothing reads or writes the block tables any more.

**Interfaces — produces:**
- `events.agreement.RUN_AGREEMENT_TOLERANCE_S = 2 * MIN_CODE_WORD_SLOT_S` (2 ms), which `_check_runs` uses; `block_agreement_tolerance_s`, `BLOCK_AGREEMENT_TOLERANCE_K`, `BLOCK_AGREEMENT_TOLERANCE_FLOOR_S` and `_BLOCK_START_MAX_SLOTS` are gone.
- `TierInputs` and `TimingProvenance` lose `block_agreement`. **A development database redeclares `timebase.TimingProvenance`**; no real database exists yet.

**Why the tolerance has no float32 term.** A run's times are doubles from `core.Run` through `GET /sessions`' JSON to wl.works' copy, so an honest request agrees exactly; two code-word slots is spec §3.2's "about 2 ms". The block check's derivation bounded an asserted block against a float32 measured one, and goes with it (spec §5, piece 1's deferred M1).

- [ ] **Step 1: Write the failing tests.** Apply these diffs:

```diff
--- a/tests/schema/test_core.py
+++ b/tests/schema/test_core.py
@@ -40,7 +40,6 @@ def a_session(core):
 def test_every_table_declares_and_documents_its_key(core):
     for table in (
         core.Montage,
-        core.Block,
         core.AcquisitionSystem,
         core.Segment,
         core.RejectedSegment,
@@ -66,42 +65,6 @@ def test_segment_is_keyed_on_system_and_barcode(core):
     }
 
 
-def test_a_block_round_trips_with_and_without_works_block_id(core, a_session):
-    """`works_block_id = null : varchar(64)` is exercised in both directions: one
-    row that leaves it at its null default, one that sets it."""
-    core.Block.insert1(
-        {
-            **a_session,
-            "block_id": 1,
-            "task_type": "rf_map",
-            "start_s": 0.0,
-            "end_s": 300.0,
-        },
-        skip_duplicates=True,
-    )
-    core.Block.insert1(
-        {
-            **a_session,
-            "block_id": 2,
-            "task_type": "attention",
-            "start_s": 300.0,
-            "end_s": 900.0,
-            "works_block_id": "abc-123",
-        },
-        skip_duplicates=True,
-    )
-    without_id = (core.Block & {**a_session, "block_id": 1}).fetch1()
-    with_id = (core.Block & {**a_session, "block_id": 2}).fetch1()
-
-    assert without_id["task_type"] == "rf_map"
-    assert without_id["start_s"] == pytest.approx(0.0)
-    assert without_id["end_s"] == pytest.approx(300.0)
-    assert without_id["works_block_id"] is None
-
-    assert with_id["task_type"] == "attention"
-    assert with_id["works_block_id"] == "abc-123"
-
-
 def _segment_row(a_session, **overrides):
     """A complete Segment row.
 
@@ -285,3 +248,21 @@ def test_a_run_assertion_is_keyed_on_its_measured_run_and_round_trips(core, a_se
         assert (core.RunAssertion & session).fetch1() == row
     finally:
         (core.RunAssertion & session).delete_quick()
+
+
+def test_what_runs_replaced_is_retired(dj_conn, prefix):
+    """Design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    section 5: `core.Block` and `request.ActivationBlock` gave way to
+    `core.RunAssertion` and `request.ActivationRun`, `BlockCoverage` to
+    `RunCoverage`, and `TimingProvenance.block_agreement` -- computed before
+    any request existed, so it never ran in wl.works' flow -- to the run check
+    on arrival."""
+    from wl_preproc.events import agreement
+    from wl_preproc.schema import core as core_tables
+    from wl_preproc.schema import coverage, request, timebase
+
+    timebase.activate(prefix=prefix)
+    assert not hasattr(core_tables, "Block") and not hasattr(request, "ActivationBlock")
+    assert not hasattr(coverage, "BlockCoverage")
+    assert "block_agreement" not in timebase.TimingProvenance.heading.names
+    assert "block_agreement" not in agreement.TierInputs.__dataclass_fields__
```

```diff
--- a/tests/events/test_agreement.py
+++ b/tests/events/test_agreement.py
@@ -34,28 +34,7 @@ def test_one_full_code_record_plus_a_witness_is_b():
 
 def test_one_full_code_record_alone_is_c():
     """"1 full-code record, cross-checked only against task file" -- behaviour-
-    only training, where the Pi is the sole recorder.
-
-    **`block_agreement=None` does not block C, unlike `trial_count_agreement
-    =None` -- fix round 2, folded in after deleting a duplicate test.** A
-    prior `test_c_requires_the_block_check_to_have_actually_happened`
-    asserted this exact property with `block_agreement=None` passed
-    explicitly, which is a no-op: `block_agreement` already defaults to
-    `None`, so that test's inputs were this one's verbatim and it killed no
-    mutant this one does not. Its own NAME also asserted the opposite of
-    what it checked -- it claimed C "requires" the block check, while its
-    body proved C does NOT require it -- copied from the genuinely-D
-    `..._task_file_check_...` test below without updating for the fact that
-    `block_agreement` and `trial_count_agreement` are NOT gated the same
-    way: `trial_count_agreement` is a precondition for C's OWN branch
-    (`n_full_code_records == 1 and trial_count_agreement is True`), so its
-    `None` fails that branch and falls through toward D; `block_agreement`
-    has no branch anywhere that requires it to be `True`, so its `None`
-    simply never fires the one D-check it participates in and this fixture
-    (which never sets it, leaving it `None`) reaches C exactly as it would
-    without `block_agreement` existing at all. See `resolve_tier`'s own
-    docstring for the fuller version of this distinction.
-    """
+    only training, where the Pi is the sole recorder."""
     assert agreement.resolve_tier(
         _inputs(n_full_code_records=1, n_strobe_witnesses=0, event_code_agreement=None)
     ) == "C"
@@ -98,39 +77,6 @@ def test_c_requires_the_task_file_check_to_have_actually_happened():
     ) == "D"
 
 
-def test_block_disagreement_is_D_even_with_two_agreeing_full_code_records():
-    """Design spec section 5: "A disagreement between `trial.Block` (measured)
-    and `core.Block` (asserted) is a tier-D condition, not a silent
-    reconciliation." Overrides `block_agreement=False` only, everything else
-    at base (`n_full_code_records=2`, `event_code_agreement=1.0`,
-    `trial_count_agreement=True`) -- so this fixture would otherwise satisfy
-    tier A outright. Produces: two full-code records that genuinely agree, a
-    genuine task-file cross-check, and a genuine block-boundary disagreement.
-    `resolve_tier` must reach the `block_agreement is False` line specifically,
-    not merely land on D through some other guard -- confirmed by sabotage:
-    deleting that one `if` from `resolve_tier` turns this fixture's verdict
-    into "A", and nothing else in THIS file moves.
-
-    (Fix round 2 correction: this docstring previously also claimed "the
-    mutant survives every other test in this suite". Checked and false --
-    `tests/schema/test_timebase.py::
-    test_block_disagreement_forces_d_even_with_two_agreeing_full_code_records`
-    exercises the identical `block_agreement is False` path end-to-end
-    through `TimingProvenance.make()` and would fail under the same
-    sabotage too. The claim was never verified against the whole suite, only
-    against this one file; narrowed to what was actually checked.)
-    """
-    assert agreement.resolve_tier(_inputs(block_agreement=False)) == "D"
-
-
-def test_block_agreement_true_does_not_block_tier_a():
-    """The positive case: `block_agreement=True` alongside every other tier-A
-    condition must still resolve to A -- a passing check must never be
-    mistaken for a gating one. Produces: two agreeing full-code records, a
-    genuine task-file cross-check, and a genuine, matching block boundary."""
-    assert agreement.resolve_tier(_inputs(block_agreement=True)) == "A"
-
-
 def test_no_full_code_record_at_all_is_D():
     """Zero full-code recorders present at all -- nothing decoded any event
     codes, so A, B and C's shared precondition (>=1 full-code record) is
@@ -210,72 +156,6 @@ def test_code_agreement_tolerates_a_dropped_word_at_the_head():
     )
 
 
-def test_block_agreement_tolerance_is_derived_from_float32_precision_not_chosen():
-    """Fix round 2: a fixed `1e-3` tolerance in `TimingProvenance.make()`
-    cited `timebase/segments.py`'s alignment durations as precedent for
-    "chosen rather than derived" -- wrong, since that module derives its own
-    numbers explicitly ("consequences of the decoder"), and a real budget
-    exists to derive this one too. `pipeline.trial.Block` declares
-    `block_start_time`/`block_stop_time` as `float` (single precision,
-    confirmed directly against `element_event/trial.py`), so the MEASURED
-    side of a block-boundary comparison always carries up to one float32
-    half-ULP of pure storage rounding -- and that half-ULP grows with
-    magnitude, consuming a fixed 1 ms tolerance's entire budget by 4.5h into
-    a session and exceeding it past 9.1h.
-
-    A schema-level test at that duration would need to generate hours of
-    synthetic session data (`synth/recipe.py::BENCHMARK_RECIPE`'s own
-    comment: "tens of megabytes per generation" for a session far shorter
-    than this), so this tests the derivation function directly instead --
-    honest about testing the unit rather than quietly avoiding the regime
-    the schema-level fixtures never reach.
-
-    Produces: `true_end_s = 42345.678`, a magnitude in the same binade
-    `worst_drift_ppm`'s own review measured (past 32768s / 9.1h) and NOT
-    exactly float32-representable, so storing it in a `float` column
-    genuinely rounds it -- confirmed inline (`numpy.float32(true_end_s) !=
-    true_end_s`) rather than assumed. The resulting rounding error, 1.6875
-    ms, exceeds a fixed 1 ms tolerance outright (proving the fixed tolerance
-    would have wrongly quarantined this honestly-agreeing pair at tier D),
-    while the derived tolerance at this magnitude (3.90625 ms) comfortably
-    covers it. A genuine several-second disagreement at the same magnitude
-    is still correctly rejected, proving the derivation does not just grow
-    permissive without bound.
-    """
-    import numpy as np
-
-    true_end_s = 42345.678
-    stored_measured_end_s = float(np.float32(true_end_s))
-    rounding_error_s = abs(stored_measured_end_s - true_end_s)
-
-    assert rounding_error_s > 0.0, (
-        "this fixture must exercise genuine float32 rounding, not a value "
-        "that happens to already be exactly representable"
-    )
-
-    fixed_tolerance_s = 1e-3
-    assert rounding_error_s > fixed_tolerance_s, (
-        "this test's whole point is a magnitude where a fixed 1 ms tolerance "
-        f"is already insufficient for storage rounding alone: got "
-        f"{rounding_error_s}s"
-    )
-
-    derived = agreement.block_agreement_tolerance_s(stored_measured_end_s, true_end_s)
-    assert derived > fixed_tolerance_s
-    assert rounding_error_s <= derived, (
-        "the derived tolerance must cover pure storage rounding at this "
-        f"magnitude: error {rounding_error_s}s, tolerance {derived}s"
-    )
-
-    # And a genuine disagreement -- not storage rounding -- at the same
-    # magnitude must still be rejected: the derivation must not grow so
-    # permissive that it stops meaning anything.
-    genuinely_disagreeing = stored_measured_end_s + 1.0
-    assert abs(stored_measured_end_s - genuinely_disagreeing) > agreement.block_agreement_tolerance_s(
-        stored_measured_end_s, genuinely_disagreeing
-    )
-
-
 def test_min_code_word_slot_s_tracks_synth_timelines_spacing_or_flags_the_drift():
     """Fix round 4 (coordinator review): a drift detector, not a coupling.
 
@@ -293,18 +173,18 @@ def test_min_code_word_slot_s_tracks_synth_timelines_spacing_or_flags_the_drift(
     both without running that architecture backwards.
 
     **What this guards, concretely.** `events.agreement.
-    BLOCK_AGREEMENT_TOLERANCE_FLOOR_S` is derived from `MIN_CODE_WORD_SLOT_S`
-    as "one code-word slot's worth of transport quantization, doubled for
-    float32-rounding headroom" -- a derivation that is only true because
-    `MIN_CODE_WORD_SLOT_S` matches the ACTUAL slot spacing the synthetic
-    generator (this project's only behavioural-stack implementation) uses to
-    place code words. If `synth.timeline.CODE_WORD_SPACING_S` is ever
-    revised and `MIN_CODE_WORD_SLOT_S` is not updated to match, the floor
-    silently stops covering the ratchet `tests/schema/test_timebase.py::
-    provenance_session` measures (`block_start_time == 0.001`), and an
-    honestly agreeing session starts reading `block_agreement=False` and
-    getting quarantined at tier D -- silently, and in the single most
-    consequential surface this phase produces.
+    RUN_AGREEMENT_TOLERANCE_S` is two of `MIN_CODE_WORD_SLOT_S` -- "two
+    code-word slots", the "about 2 ms" design spec
+    `2026-10-01-session-listing-and-run-requests-design.md` section 3.2 names
+    for the run check on arrival -- which is only a statement about the codes
+    because `MIN_CODE_WORD_SLOT_S` matches the ACTUAL slot spacing the
+    synthetic generator (this project's only behavioural-stack
+    implementation) uses to place code words. If `synth.timeline.
+    CODE_WORD_SPACING_S` is ever revised and `MIN_CODE_WORD_SLOT_S` is not
+    updated to match, the tolerance silently stops meaning what its comment
+    says. (It once also set the floor of `TimingProvenance.block_agreement`,
+    retired with `core.Block`, where a mismatch quarantined honest sessions
+    at tier D.)
 
     **This is a "decide, don't drift" gate, not a permanent lock.**
     Divergence is allowed: the production constant is meant to track a real
@@ -326,14 +206,20 @@ def test_min_code_word_slot_s_tracks_synth_timelines_spacing_or_flags_the_drift(
             "to track a real system's own code-word slot spacing once one "
             "is chosen, not to stay locked to the synthetic generator "
             "forever. But it must be a DECISION, not an accident: "
-            "block_agreement_tolerance_s's floor "
-            "(BLOCK_AGREEMENT_TOLERANCE_FLOOR_S) is derived from "
-            "MIN_CODE_WORD_SLOT_S as one code-word transport slot, and if "
-            "that no longer reflects the slot spacing a real boundary is "
-            "actually quantized to, the floor silently stops covering the "
-            "ratchet and an honestly agreeing session starts getting "
-            "quarantined at tier D. Whoever changed either constant must "
-            "re-derive BLOCK_AGREEMENT_TOLERANCE_FLOOR_S against the new "
-            "value (or explicitly confirm the old derivation still holds) "
-            "before this assertion is updated to match."
+            "RUN_AGREEMENT_TOLERANCE_S is two code-word transport slots, "
+            "and if MIN_CODE_WORD_SLOT_S no longer reflects the slot spacing "
+            "a real boundary is actually quantized to, that tolerance stops "
+            "meaning what its comment says. Whoever changed either constant "
+            "must re-decide RUN_AGREEMENT_TOLERANCE_S against the new value "
+            "(or explicitly confirm the old one still holds) before this "
+            "assertion is updated to match."
         )
+
+
+def test_a_run_agrees_within_two_code_word_slots():
+    """Design spec `2026-10-01-session-listing-and-run-requests-design.md`
+    section 3.2: an asserted run is checked against `core.Run` within about
+    2 ms. wl.works asserts its copy of `GET /sessions`' measured value, a
+    double end to end, so an honest request agrees exactly; the tolerance is
+    two code-word slots, as the retired block check's floor was."""
+    assert agreement.RUN_AGREEMENT_TOLERANCE_S == 2 * agreement.MIN_CODE_WORD_SLOT_S == 0.002
```

```diff
--- a/tests/schema/test_timebase.py
+++ b/tests/schema/test_timebase.py
@@ -25,7 +25,7 @@ def test_derived_tables_are_computed_not_manual(schemas):
     core, coverage, _timebase = schemas
 
     assert issubclass(core.Segment, dj.Computed)
-    assert issubclass(coverage.BlockCoverage, dj.Computed)
+    assert issubclass(coverage.RunCoverage, dj.Computed)
     assert issubclass(coverage.TrialCoverage, dj.Computed)
 
 
@@ -102,20 +102,6 @@ def test_no_bare_longblob_in_the_new_schema_module(schemas):
     assert "longblob" not in timebase.TimingProvenance.definition
 
 
-def test_block_does_not_claim_to_decode_its_own_boundaries(schemas):
-    """Closed open item 9: block rows are authored by wl.works' session planner
-    and wl-preproc NEVER writes them — it cross-validates and quarantines on
-    absence. The column comment claimed the opposite mechanism ("boundaries are
-    decoded from event codes and cross-validated against those rows") for two
-    phases, and 1c-3 predicted the decoder would "find the slot occupied". It
-    resolved the other way: `accept()` was right and the comment was wrong.
-    """
-    core, _coverage, _timebase = schemas
-
-    assert "decoded from event codes" not in core.Block.definition
-    assert "wl.works" in core.Block.definition
-
-
 # --- TimingProvenance.make(). These need a landed, populated session. ---
 
 import datetime  # noqa: E402
@@ -125,8 +111,7 @@ import datetime  # noqa: E402
 def provenance_session(dj_conn, prefix, tmp_path_factory):
     """A landed `drift` session with every populate run, provenance last --
     including `events.populate_session`, which 1c-5 Task 9's tier resolution
-    now depends on for `trial_count_agreement` (codes vs task file) and
-    `block_agreement` (measured `trial.Block` vs asserted `core.Block`).
+    now depends on for `trial_count_agreement` (codes vs task file).
 
     Before Task 9 this fixture never called `events.populate_session` at all,
     because nothing here needed `pipeline.trial.Trial`/`trial.Block` to exist.
@@ -136,27 +121,6 @@ def provenance_session(dj_conn, prefix, tmp_path_factory):
     reason that has nothing to do with what each test actually exercises. Per
     "fix the fixture, not the spec" -- the fixture predates the dependency,
     not the design.
-
-    `core.Block` gets one row asserting the DRIFT recipe's own NOMINAL block
-    boundary independently -- `start_s=0.0`, `end_s=recipe.duration_s` --
-    NOT read back from `pipeline.trial.Block`. Fix round 2 tried the latter
-    after tightening `block_agreement_tolerance_s`'s floor made the nominal
-    assertion fail, but review (fix round 3) caught that this made
-    `block_agreement` compare a value against itself: `trial.Block`'s
-    columns are float32 and `core.Block`'s are double, so a float32 value
-    written into a double and read back is bit-exact, and the "positive
-    path" this fixture exists to exercise could never again detect any
-    disagreement. wl.works asserting the nominal boundary and the decoder
-    measuring it one code-word slot later (`timeline.py`'s own `_emit`
-    ratchets `BLOCK_START`'s escape word one slot past `SESSION_START`) is
-    not a fixture defect to route around -- it is exactly the real
-    relationship this fixture is supposed to model, and
-    `block_agreement_tolerance_s`'s floor is now derived from that same
-    one-slot transport quantization (see its own module-level comment), so
-    it absorbs the ratchet correctly without the fixture needing to already
-    know the measured answer. `test_block_agreement_true_has_teeth_against_a_
-    real_perturbation`, below, is what proves this path still has teeth
-    after the revert.
     """
     from wl_preproc.schema import core, coverage, events, ingest, pipeline, timebase
     from wl_preproc.synth.recipe import RECIPES
@@ -205,17 +169,6 @@ def provenance_session(dj_conn, prefix, tmp_path_factory):
         skip_duplicates=True,
     )
 
-    core.Block.insert1(
-        {
-            **session_key,
-            "block_id": 1,
-            "task_type": "rf_map",
-            "start_s": 0.0,
-            "end_s": recipe.duration_s,
-        },
-        skip_duplicates=True,
-    )
-
     timebase.SystemTimebase.populate()
     core.Segment.populate()
     events.populate_session(session_key, root / recipe.session_id)
@@ -237,14 +190,7 @@ def test_the_tier_resolves_to_a_once_two_full_code_records_genuinely_agree(
     so the two decode identically regardless of the NI's planted 18 ppm
     drift. `trial_count_agreement` is `True`: the task file and the decoded
     code stream both derive from the identical planted `GroundTruth.trials`.
-    `block_agreement` is `True` too -- but NOT because the two values match
-    exactly, which they provably do not. The fixture asserts one `core.Block`
-    row at the DRIFT recipe's NOMINAL boundary (`start_s=0.0`), while the
-    measured `trial.Block` starts one code-word slot later, at `0.001`: the
-    ratchet `provenance_session`'s own docstring describes. It agrees because
-    `block_agreement_tolerance_s`'s floor is derived from exactly that one-slot
-    transport quantization and absorbs it -- so this positive path exercises
-    the tolerance rather than bypassing it. Produces: tier A, by the
+    Produces: tier A, by the
     `n_full_code_records >= 2` branch, with none of the D guards tripped.
     """
     _core, _coverage, timebase = schemas
@@ -258,7 +204,6 @@ def test_the_tier_resolves_to_a_once_two_full_code_records_genuinely_agree(
     assert row["event_code_agreement"] == pytest.approx(1.0)
     assert row["decode_errors"] == 0
     assert row["trial_count_agreement"]
-    assert row["block_agreement"]
 
 
 def test_tier_d_is_fully_derivable_now(schemas, dj_conn, prefix, tmp_path):
@@ -354,10 +299,10 @@ def test_provenance_stores_the_inputs_so_the_tier_can_be_re_derived(
 
     **Fix round 1** (coordinator review): this test's own opening claim is
     "every input", but until this round it checked only the six columns that
-    predate 1c-5 Task 9 and none of the four `TierInputs` fields Task 9 added
-    -- `event_code_agreement`, `trial_count_agreement`, `camera_trigger_count`,
-    `block_agreement` -- which are exactly the evidence the re-derivability
-    clause is about. Extended to check all seven new columns (those four plus
+    predate 1c-5 Task 9 and none of the `TierInputs` fields Task 9 added
+    -- `event_code_agreement`, `trial_count_agreement`, `camera_trigger_count`
+    -- which are exactly the evidence the re-derivability clause is about.
+    Extended to check all six new columns (those three plus
     `n_full_code_records`, `n_strobe_witnesses`, `decode_errors`), and against
     values this test derives independently rather than merely checking the
     keys exist: `camera_trigger_count` against `synth.peripherals.
@@ -366,11 +311,12 @@ def test_provenance_stores_the_inputs_so_the_tier_can_be_re_derived(
     what the `drift` recipe's own topology implies -- two full-code records
     (`syncbox` + `spikeglx`) that must agree exactly regardless of the NI's
     planted drift, one valid `rhs` strobe witness, zero decode errors, and a
-    genuine (not merely non-`None`) trial-count and block-boundary match, both
-    of which this fixture's own `core.Block` insert and populated `Trial` rows
-    make true rather than assumed. Verified directly against a live run
-    before writing the literals below, per `provenance_session`'s current
-    state (`tests/schema/test_timebase.py`'s own module).
+    genuine (not merely non-`None`) trial-count match, which this fixture's
+    populated `Trial` rows make true rather than assumed. (A fourth Task 9
+    field, `block_agreement`, is retired with `core.Block`.) Verified directly
+    against a live run before writing the literals below, per
+    `provenance_session`'s current state (`tests/schema/test_timebase.py`'s
+    own module).
     """
     from wl_preproc.synth.peripherals import camera_frame_count
 
@@ -389,7 +335,7 @@ def test_provenance_stores_the_inputs_so_the_tier_can_be_re_derived(
         max(abs(ppm) for _s, ppm in recipe.system_drift_ppm), abs=300.0
     )
 
-    # -- The four TierInputs fields Task 9 added, and the tier's own
+    # -- The TierInputs fields Task 9 added, and the tier's own
     # derived-count fields: present AND holding what this session's real
     # topology implies, not merely non-null.
     assert row["n_full_code_records"] == 2, "syncbox + spikeglx, both present"
@@ -407,11 +353,6 @@ def test_provenance_stores_the_inputs_so_the_tier_can_be_re_derived(
         "no DROPPED_CAMERA_FRAMES fault on this recipe, so nothing should be "
         "subtracted from the full frame count"
     )
-    assert row["block_agreement"], (
-        "this fixture's own core.Block row asserts the NOMINAL boundary "
-        "(start_s=0.0); the measured trial.Block starts one code-word slot "
-        "later at 0.001, inside the derived tolerance -- not an exact match"
-    )
 
 
 def test_the_tier_leaves_pending_once_the_three_inputs_exist(dj_conn, prefix):
@@ -450,100 +391,6 @@ def test_the_tier_leaves_pending_once_the_three_inputs_exist(dj_conn, prefix):
         )
 
 
-def test_block_disagreement_forces_d_even_with_two_agreeing_full_code_records(
-    dj_conn, prefix, tmp_path
-):
-    """Design spec section 5: "A disagreement between `trial.Block` (measured)
-    and `core.Block` (asserted) is a tier-D condition, not a silent
-    reconciliation" -- proven end-to-end here, not only at
-    `resolve_tier`'s own unit level (`tests/events/test_agreement.py`).
-
-    One of the `ci` recipe's two blocks (RF_MAP: 3 trials * 3.0s, then
-    RESTING_DARK: 1 trial * 6.0s) genuinely spans `[0.0, 9.0)` -- fix round 2
-    correction: this docstring previously called it the recipe's "own single
-    block", which is wrong; `core.Block` instead asserts `end_s=999.0`, a
-    wl.works row that does not describe this session at all. Every other
-    input is
-    clean: `syncbox` + `spikeglx` give two agreeing full-code records (tier A
-    territory otherwise), and the task file's trial count matches the decoded
-    one exactly. Produces: `block_agreement=False`, and a tier of D despite
-    satisfying every other tier-A condition -- proving `block_agreement`
-    actually gates `TimingProvenance.make()`'s own resolved tier, not merely
-    `resolve_tier` in isolation.
-    """
-    import datetime
-
-    from wl_preproc.schema import core, events, ingest, pipeline, timebase
-    from wl_preproc.synth.recipe import RECIPES
-    from wl_preproc.synth.session import generate_session
-
-    ingest.activate(prefix=prefix)
-    events.activate(prefix=prefix)
-    recipe = RECIPES["ci"]
-    generate_session(tmp_path, recipe)
-    session_dir = tmp_path / recipe.session_id
-
-    pipeline.lab.Lab.insert1(
-        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
-        skip_duplicates=True,
-    )
-    pipeline.subject.Subject.insert1(
-        {
-            "subject": recipe.subject,
-            "sex": "M",
-            "subject_birth_date": datetime.date(2020, 1, 1),
-            "subject_description": "",
-        },
-        skip_duplicates=True,
-    )
-    session_key = {
-        "subject": recipe.subject,
-        "session_datetime": datetime.datetime(2027, 3, 23, 9, 0),
-    }
-    pipeline.Session.insert1(session_key, skip_duplicates=True)
-    ingest.Ingestion.insert1(
-        {
-            **session_key,
-            "ingested_at": datetime.datetime(2027, 3, 23, 19, 0),
-            "session_dir": str(session_dir),
-            "integrity": "verified",
-            "topology": {system: "present" for system in recipe.systems},
-            "manifest_hash": "blake3:test",
-        },
-        skip_duplicates=True,
-    )
-    core.AcquisitionSystem.insert(
-        [{**session_key, "system": system} for system in recipe.systems],
-        skip_duplicates=True,
-    )
-    core.Block.insert1(
-        {
-            **session_key,
-            "block_id": 1,
-            "task_type": "rf_map",
-            "start_s": 0.0,
-            "end_s": 999.0,  # disagrees with the measured trial.Block outright
-        },
-        skip_duplicates=True,
-    )
-
-    timebase.SystemTimebase.populate()
-    core.Segment.populate()
-    events.populate_session(session_key, session_dir)
-    timebase.TimingProvenance.populate()
-
-    row = (timebase.TimingProvenance & session_key).fetch1()
-    # `== 0`, not `is False`: a `tinyint(1)` column round-trips through
-    # DataJoint/pymysql as a plain Python int, not the `False` singleton, so
-    # an `is` comparison here would fail on genuinely correct data.
-    assert row["block_agreement"] == 0, row
-    assert row["tier"] == "D", row
-    assert row["n_full_code_records"] == 2, (
-        "this must be a genuine A-territory session apart from the block "
-        f"disagreement, or D proves nothing about block_agreement: {row}"
-    )
-
-
 def test_a_session_with_no_corroboration_at_all_resolves_to_d(
     schemas, dj_conn, prefix, tmp_path
 ):
@@ -585,8 +432,8 @@ def test_a_session_with_no_corroboration_at_all_resolves_to_d(
     through the pre-existing timing-check fast path `test_tier_d_is_fully_
     derivable_now` covers. `n_full_code_records == 1` (only syncbox: no
     `spikeglx`, so no second record; no `rhs`, so `n_strobe_witnesses == 0`),
-    and `trial_count_agreement`/`event_code_agreement`/`camera_trigger_count`/
-    `block_agreement` are all `None` -- nothing corroborated this session,
+    and `trial_count_agreement`/`event_code_agreement`/`camera_trigger_count`
+    are all `None` -- nothing corroborated this session,
     which is the row's own evidence for why D is correct here, not merely
     that it is D.
     """
@@ -670,7 +517,6 @@ def test_a_session_with_no_corroboration_at_all_resolves_to_d(
     assert row["decode_errors"] == 0, row
     assert row["event_code_agreement"] is None, row
     assert row["trial_count_agreement"] is None, row
-    assert row["block_agreement"] is None, row
 
     assert row["tier"] == "D", (
         "one full-code record, no witness, no successful task-file check: "
@@ -678,115 +524,3 @@ def test_a_session_with_no_corroboration_at_all_resolves_to_d(
     )
 
 
-def test_block_agreement_true_has_teeth_against_a_real_perturbation(
-    schemas, dj_conn, prefix, tmp_path_factory
-):
-    """Fix round 3 (coordinator review): the positive path -- a `core.Block`
-    row that genuinely matches the measured boundary -- has to be able to
-    detect a real disagreement, not merely fail to crash. Reverting
-    `provenance_session`'s fixture to assert the nominal boundary
-    independently (rather than reading `pipeline.trial.Block` back, which
-    made the comparison bit-exact and therefore untestable) means that
-    session alone no longer proves this; this test does, with its own
-    session so it is not entangled with `provenance_session`'s module-scoped
-    cache.
-
-    Same `drift` recipe, same nominal assertion `provenance_session` uses
-    (`start_s=0.0`, `end_s=recipe.duration_s`) -- except `start_s` is
-    perturbed by `+0.1` s (100 ms), two orders of magnitude past
-    `block_agreement_tolerance_s`'s derived floor (2 ms at this small a
-    magnitude: one code-word slot, doubled for float32-rounding headroom).
-    100 ms is not a boundary case -- it is comfortably larger than the
-    tolerance in either direction this fixture could plausibly reach, so a
-    tier that does not move here means the positive path proves nothing.
-
-    Produces: every other input identical to a genuine tier-A session
-    (`syncbox` + `spikeglx` give two agreeing full-code records; `rhs` gives
-    a valid witness; the task file's trial count matches the decoded one
-    exactly) -- so the ONLY thing standing between this session and tier A
-    is the perturbed `start_s`. `block_agreement` must read `False` and the
-    tier must move to D.
-    """
-    from wl_preproc.schema import core, coverage, events, ingest, pipeline, timebase
-    from wl_preproc.synth.recipe import RECIPES
-    from wl_preproc.synth.session import generate_session
-
-    timebase.activate(prefix=prefix)
-    coverage.activate(prefix=prefix)
-    ingest.activate(prefix=prefix)
-    events.activate(prefix=prefix)
-
-    root = tmp_path_factory.mktemp("perturbed")
-    recipe = RECIPES["drift"]
-    generate_session(root, recipe)
-
-    pipeline.lab.Lab.insert1(
-        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
-        skip_duplicates=True,
-    )
-    pipeline.subject.Subject.insert1(
-        {
-            "subject": recipe.subject,
-            "sex": "M",
-            "subject_birth_date": datetime.date(2020, 1, 1),
-            "subject_description": "",
-        },
-        skip_duplicates=True,
-    )
-    session_key = {
-        "subject": recipe.subject,
-        "session_datetime": datetime.datetime(2027, 3, 26, 9, 0),
-    }
-    pipeline.Session.insert1(session_key, skip_duplicates=True)
-    ingest.Ingestion.insert1(
-        {
-            **session_key,
-            "ingested_at": datetime.datetime(2027, 3, 26, 19, 0),
-            "session_dir": str(root / recipe.session_id),
-            "integrity": "verified",
-            "topology": {system: "present" for system in recipe.systems},
-            "manifest_hash": "blake3:test",
-        },
-        skip_duplicates=True,
-    )
-    core.AcquisitionSystem.insert(
-        [{**session_key, "system": system} for system in recipe.systems],
-        skip_duplicates=True,
-    )
-    core.Block.insert1(
-        {
-            **session_key,
-            "block_id": 1,
-            "task_type": "rf_map",
-            "start_s": 0.0 + 0.1,  # perturbed: 100ms past the nominal 0.0
-            "end_s": recipe.duration_s,
-        },
-        skip_duplicates=True,
-    )
-
-    timebase.SystemTimebase.populate()
-    core.Segment.populate()
-    events.populate_session(session_key, root / recipe.session_id)
-    timebase.TimingProvenance.populate()
-
-    row = (timebase.TimingProvenance & session_key).fetch1()
-    assert row["n_full_code_records"] == 2, (
-        f"this must be genuine A-territory apart from the perturbation: {row}"
-    )
-    # block_agreement is computed independently of `failed` (gated on
-    # `syncbox_fitted` alone, per TimingProvenance.make()'s own comment), so
-    # this genuinely proves the 100ms perturbation was detected through the
-    # full production path.
-    assert row["block_agreement"] == 0, row
-    # Every system in `RECIPES["drift"]`, ohdpi included, now aligns
-    # (`n_systems_aligned == len(recipe.systems)`, checked directly while
-    # restoring this assertion), so `failed` is False and `tier` comes from
-    # `resolve_tier(inputs)` -- the `block_agreement` branch above, not the
-    # `tier = "D" if failed else ...` short-circuit. This is a genuine
-    # second, end-to-end check of the same branch
-    # tests/events/test_agreement.py's
-    # test_block_disagreement_is_D_even_with_two_agreeing_full_code_records
-    # and test_block_agreement_true_does_not_block_tier_a exercise directly.
-    assert row["tier"] == "D", (
-        f"a 100ms start_s perturbation must move the tier off A: {row}"
-    )
```

```diff
--- a/tests/schema/test_coverage.py
+++ b/tests/schema/test_coverage.py
@@ -11,15 +11,6 @@ def cov(dj_conn, prefix):
     return coverage
 
 
-def test_block_coverage_is_per_block_per_system(cov):
-    assert set(cov.BlockCoverage.primary_key) == {
-        "subject",
-        "session_datetime",
-        "block_id",
-        "system",
-    }
-
-
 def test_trial_coverage_is_per_trial_per_system(cov):
     assert set(cov.TrialCoverage.primary_key) == {
         "subject",
@@ -32,10 +23,10 @@ def test_trial_coverage_is_per_trial_per_system(cov):
 def test_trial_coverage_populates_a_row_for_every_trial_and_system(
     cov, dj_conn, prefix, tmp_path
 ):
-    """1c-5 Task 9: `TrialCoverage.make()` mirrors `BlockCoverage.make()`
-    exactly, calling the same `timebase.coverage.classify_coverage` rather
-    than a second interval rule (`tests/timebase/test_coverage_rules.py::
-    test_block_coverage_populates_a_row_for_every_block_and_system` is this
+    """1c-5 Task 9: `TrialCoverage.make()` calls the same
+    `timebase.coverage.classify_coverage` as `RunCoverage.make()` rather than
+    a second interval rule (`tests/timebase/test_coverage_rules.py::
+    test_run_coverage_populates_a_row_for_every_run_and_system` is this
     test's own sibling, for the other table).
 
     **What this fixture actually produces, checked rather than assumed:**
@@ -139,11 +130,11 @@ def test_coverage_states_are_exactly_full_partial_absent(cov, enum_values):
     satisfies, and which a fourth state added later — the thing that would
     actually collapse `partial` back into a spectrum — could not fail.
 
-    Both coverage tables are checked, not just BlockCoverage: they share
+    Both coverage tables are checked, not just one: they share
     `_COVERAGE_ENUM` today and that is exactly the assumption worth pinning.
     """
     expected = {"full", "partial", "absent"}
-    for table in (cov.BlockCoverage, cov.TrialCoverage):
+    for table in (cov.RunCoverage, cov.TrialCoverage):
         declared = table.heading["coverage"].type
         assert enum_values(declared) == expected, (
             f"{table.__name__}.coverage declares {enum_values(declared)}, "
```

```diff
--- a/tests/timebase/test_coverage_rules.py
+++ b/tests/timebase/test_coverage_rules.py
@@ -81,108 +81,10 @@ def test_a_zero_length_block_is_refused_rather_than_divided_by():
         classify_coverage((10.0, 10.0), [(0.0, 20.0)])
 
 
-def test_block_coverage_populates_a_row_for_every_block_and_system(
-    dj_conn, prefix, tmp_path
-):
-    """The cross product, not a join through Segment: a system that recorded
-    NONE of a block still needs a row saying `absent`, and a missing row is not
-    the same statement.
-
-    The block rows here are inserted the way `accept()` inserts them — as
-    wl.works' assertion. Nothing in this phase authors a boundary.
-    """
-    import datetime
-
-    from wl_preproc.schema import core, coverage, ingest, pipeline, timebase
-    from wl_preproc.synth.recipe import RECIPES
-    from wl_preproc.synth.session import generate_session
-
-    coverage.activate(prefix=prefix)
-    timebase.activate(prefix=prefix)
-    ingest.activate(prefix=prefix)
-
-    recipe = RECIPES["drift"]
-    generate_session(tmp_path, recipe)
-    session_dir = tmp_path / recipe.session_id
-
-    pipeline.lab.Lab.insert1(
-        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
-        skip_duplicates=True,
-    )
-    pipeline.subject.Subject.insert1(
-        {
-            "subject": recipe.subject,
-            "sex": "M",
-            "subject_birth_date": datetime.date(2020, 1, 1),
-            "subject_description": "",
-        },
-        skip_duplicates=True,
-    )
-    session_key = {
-        "subject": recipe.subject,
-        "session_datetime": datetime.datetime(2027, 3, 19, 9, 0),
-    }
-    pipeline.Session.insert1(session_key, skip_duplicates=True)
-    ingest.Ingestion.insert1(
-        {
-            **session_key,
-            "ingested_at": datetime.datetime(2027, 3, 19, 19, 0),
-            "session_dir": str(session_dir),
-            "integrity": "verified",
-            "topology": {system: "present" for system in recipe.systems},
-            "manifest_hash": "blake3:test",
-        },
-        skip_duplicates=True,
-    )
-    core.AcquisitionSystem.insert(
-        [{**session_key, "system": system} for system in recipe.systems],
-        skip_duplicates=True,
-    )
-    # Two blocks: one inside the recorded span, one past its end. The second is
-    # what makes `absent` a measured verdict rather than an untested branch.
-    core.Block.insert(
-        [
-            {
-                **session_key,
-                "block_id": 1,
-                "task_type": "rf_map",
-                "start_s": 0.0,
-                "end_s": 10.0,
-            },
-            {
-                **session_key,
-                "block_id": 2,
-                "task_type": "rf_map",
-                "start_s": 1_000.0,
-                "end_s": 1_010.0,
-            },
-        ],
-        skip_duplicates=True,
-    )
-
-    timebase.SystemTimebase.populate()
-    core.Segment.populate()
-    coverage.BlockCoverage.populate()
-
-    rows = (coverage.BlockCoverage & session_key).to_dicts()
-    assert len(rows) == 2 * len(recipe.systems)
-
-    inside = {row["system"]: row for row in rows if row["block_id"] == 1}
-    outside = {row["system"]: row for row in rows if row["block_id"] == 2}
-    assert set(inside) == set(recipe.systems)
-
-    for system, row in inside.items():
-        assert row["coverage"] == "full", f"{system}: {row['coverage']}"
-        assert row["covered_s"] == pytest.approx(10.0)
-    for system, row in outside.items():
-        assert row["coverage"] == "absent", f"{system}: {row['coverage']}"
-        assert row["covered_s"] == pytest.approx(0.0)
-
-
-
 def test_run_coverage_populates_a_row_for_every_run_and_system(dj_conn, prefix, tmp_path):
-    """As for blocks: the cross product, so a system that recorded none of a
-    run says `absent`. A run with no length, one that faulted at once, is
+    """The cross product, not a join through Segment, so a system that
+    recorded none of a run says `absent`: a missing row is not the same
+    statement. A run with no length, one that faulted at once, is
     `absent` too, never a failure on every pass (design spec
     `2026-10-01-session-listing-and-run-requests-design.md` section 5)."""
     import datetime
```

```diff
--- a/tests/timebase/test_segments.py
+++ b/tests/timebase/test_segments.py
@@ -492,9 +492,9 @@ def test_populate_writes_only_the_tables_this_phase_owns(
     written = {core.Segment, core.RejectedSegment, timebase.SystemTimebase}
     others = [
         core.Montage,
-        core.Block,
+        core.RunAssertion,
         core.AcquisitionSystem,
-        coverage.BlockCoverage,
+        coverage.RunCoverage,
         coverage.TrialCoverage,
         ingest.Ingestion,
         ingest.Quarantine,
```

```diff
--- a/tests/schema/test_daemon.py
+++ b/tests/schema/test_daemon.py
@@ -570,13 +570,13 @@ def test_computed_tables_is_no_longer_empty():
 
 def test_computed_tables_are_in_dependency_order():
     """Ordering is load-bearing rather than tidy. `Segment.make()` needs its
-    system's rate to already exist, and `BlockCoverage.make()` needs the
+    system's rate to already exist, and `RunCoverage.make()` needs the
     segments — and neither dependency is expressed as a `key_source`, precisely
     so that a system with no fit still records why (see `Segment.key_source`).
     So this list IS the ordering, and nothing else enforces it.
 
     `TrialCoverage` was added to the list in 1c-5's fix round and is asserted
-    after `Segment` for exactly `BlockCoverage`'s reason: its `make()`
+    after `Segment` for exactly `RunCoverage`'s reason: its `make()`
     intersects a trial's interval with `core.Segment`'s extents, so running it
     first would record a session as less covered than it is. That is the ONE
     ordering this test adds; its other dependency, `pipeline.trial.Trial`, is
@@ -591,7 +591,7 @@ def test_computed_tables_are_in_dependency_order():
     names = [table.__name__ for table in daemon._computed_tables()]
 
     assert names.index(timebase.SystemTimebase.__name__) < names.index(core.Segment.__name__)
-    assert names.index(core.Segment.__name__) < names.index(coverage.BlockCoverage.__name__)
+    assert names.index(core.Segment.__name__) < names.index(coverage.RunCoverage.__name__)
     assert names.index(core.Segment.__name__) < names.index(coverage.TrialCoverage.__name__)
     assert names.index(core.Segment.__name__) < names.index(
         timebase.TimingProvenance.__name__
@@ -626,7 +626,8 @@ def test_every_computed_table_is_a_daemon_stage():
     its table either.
 
     **What this test actually produces today:** seven discovered tables —
-    `core.Segment`, `coverage.BlockCoverage`, `coverage.TrialCoverage`,
+    `core.Segment`, `coverage.BlockCoverage` (since replaced by
+    `RunCoverage`), `coverage.TrialCoverage`,
     `eye.EyeCalibration`, `eye.EyeQuality`, `timebase.SystemTimebase`,
     `timebase.TimingProvenance` — an empty exemption set, and set equality
     with `_computed_tables()`. Confirmed to fail with `TrialCoverage` removed
```

```diff
--- a/tests/synth/test_recipe.py
+++ b/tests/synth/test_recipe.py
@@ -54,14 +54,14 @@ def test_a_zero_trial_block_is_refused():
     boundary lands several code-word slots away from `(0.0, 0.0)`, and the
     block after it starts displaced too. Traced: nominal `(0.0, 0.0)` measures
     `(0.001, 0.005)`, with the next block's start pushed to `0.006` -- six
-    slots, three times `BLOCK_AGREEMENT_TOLERANCE_FLOOR_S`, i.e. a tier-D
-    quarantine for a session where nothing is actually wrong.
-
-    `events/agreement.py`'s `_BLOCK_START_MAX_SLOTS` derives its one-slot
-    bound assuming every block has at least one trial. Its comment used to
-    claim the refusal already happened downstream in `classify_coverage`,
-    which was never called on the measured block at all; the refusal lives
-    here instead, at the only point a zero-trial block can be described.
+    slots, which the retired `TimingProvenance.block_agreement` read as a
+    tier-D quarantine for a session where nothing was actually wrong.
+
+    The generator's block timing assumes every block has at least one trial.
+    A comment once claimed the refusal already happened downstream in
+    `classify_coverage`, which was never called on the measured block at
+    all; the refusal lives here instead, at the only point a zero-trial block
+    can be described.
     """
     with pytest.raises(ValidationError) as exc:
         BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=0, trial_duration_s=3.0)
```

```diff
--- a/tests/archive/test_reclaim.py
+++ b/tests/archive/test_reclaim.py
@@ -202,11 +202,9 @@ def _timing(key, *, tier: str):
     by direct insert, not by `populate()`."
 
     The choice made here follows that same precedent, for a narrower reason
-    specific to this test. The recipe that reaches tier D honestly
-    (`tests/schema/test_timebase.py::test_block_disagreement_forces_d_even_
-    with_two_agreeing_full_code_records`) pulls in session generation, real
-    event decoding across the recipe's `syncbox` and `spikeglx` systems, and
-    a deliberately disagreeing `core.Block` row -- none of which has
+    specific to this test. A recipe that reaches tier D honestly pulls in
+    session generation, real event decoding across the recipe's `syncbox` and
+    `spikeglx` systems, and a deliberately broken input -- none of which has
     anything to do with the one comparison this module checks
     (`tier_rows[0] != "D"`). Routing through it would make a failure here
     just as likely to mean "the synthetic recipe changed shape" as "the
```

```diff
--- a/tests/cli/test_consensus_report.py
+++ b/tests/cli/test_consensus_report.py
@@ -237,7 +237,6 @@ def _land_session(subject: str, session_datetime: datetime.datetime) -> None:
             "n_full_code_records": 0,
             "n_strobe_witnesses": 0,
             "decode_errors": 0,
-            "block_agreement": None,
         },
         allow_direct_insert=True,
         skip_duplicates=True,
```

```diff
--- a/tests/cli/test_detect_report.py
+++ b/tests/cli/test_detect_report.py
@@ -211,7 +211,6 @@ def _land_session(
             "n_full_code_records": 0,
             "n_strobe_witnesses": 0,
             "decode_errors": 0,
-            "block_agreement": None,
         },
         allow_direct_insert=True,
         skip_duplicates=True,
```

```diff
--- a/tests/cli/test_eye_report.py
+++ b/tests/cli/test_eye_report.py
@@ -258,7 +258,6 @@ def _land_session(
             "n_full_code_records": 0,
             "n_strobe_witnesses": 0,
             "decode_errors": 0,
-            "block_agreement": None,
         },
         allow_direct_insert=True,
         skip_duplicates=True,
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_core.py tests/events/test_agreement.py -q --tb=line -p no:cacheprovider`
Expected: 2 failed, 20 passed: `test_what_runs_replaced_is_retired` (`core` still has `Block`) and `test_a_run_agrees_within_two_code_word_slots` (`agreement` has no `RUN_AGREEMENT_TOLERANCE_S`). The other changed tests only stop reading what this task removes; Step 4 runs them.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/schema/core.py
+++ b/wl_preproc/schema/core.py
@@ -1,10 +1,13 @@
 # wl_preproc/schema/core.py
-"""The custom core tables: montages, blocks, acquisition systems and segments.
-
-Segments and blocks are orthogonal and both are required (spec section 5.2.1).
-A block is one run of one task; a segment is one recording file's extent, forced
-by an RHS stim-parameter change, a crash or a restart. A block can span segments
-and a segment can span blocks, so neither is derivable from the other.
+"""The custom core tables: montages, runs, acquisition systems and segments.
+
+Segments and runs are orthogonal and both are required (spec section 5.2.1).
+A run is one stretch of one task; a segment is one recording file's extent,
+forced by an RHS stim-parameter change, a crash or a restart. A run can span
+segments and a segment can span runs, so neither is derivable from the other.
+`core.Block`, wl.works' assertion of what was then called a block, is retired
+(design spec `2026-10-01-session-listing-and-run-requests-design.md` section
+5): a request asserts runs, kept in `RunAssertion`.
 """
 
 from __future__ import annotations
@@ -104,33 +107,6 @@ class RunAssertion(dj.Manual):
     """
 
 
-@schema
-class Block(dj.Manual):
-    definition = """
-    # One run of one task, mirroring wl.works animal_session_block.
-    # *True under the August glossary. Since 2026-10-01 the requester's
-    # vocabulary makes a block a stretch of trials inside a run, and a run is
-    # `Run` above; revising wl.works' own blocks to match is wl.works'.*
-    # start_s/end_s are WL.WORKS' ASSERTION, recorded here through accept() --
-    # recording an assertion is not authoring it. Closed open item 9: block rows
-    # are authored by wl.works' session planner and wl-preproc never writes
-    # them; it cross-validates and quarantines on absence. The MEASURED boundary
-    # is a different quantity and lives in element-event's `trial.Block`, written
-    # by `schema/events.py::populate_session` (1c-5). This repo declares no table
-    # of its own for it: design spec section 5's adoption table assigns "Events,
-    # trials, blocks" to `element-event`. A disagreement between the two is its
-    # own tier-D condition on `timebase.TimingProvenance.block_agreement`, not a
-    # silent reconciliation. Key: (subject, session_datetime, block_id).
-    -> pipeline.Session
-    block_id : smallint
-    ---
-    task_type   : varchar(32)
-    start_s     : double
-    end_s       : double
-    works_block_id = null : varchar(64)  # the wl.works row this was matched to
-    """
-
-
 @schema
 class AcquisitionSystem(dj.Manual):
     definition = f"""
```

```diff
--- a/wl_preproc/schema/request.py
+++ b/wl_preproc/schema/request.py
@@ -170,26 +170,6 @@ class Activation(dj.Manual):
     """
 
 
-@schema
-class ActivationBlock(dj.Manual):
-    definition = """
-    # The block set this activation covers. Unit identity is a product of the
-    # sort, so two activations over different block sets produce genuinely
-    # different units and nothing may imply otherwise.
-    # Key: (subject, session_datetime, montage_id, activation_id, block_id).
-    # NOT ENFORCED HERE: both foreign keys reach the same Session, so a block
-    # is guaranteed to belong to the right session -- and to nothing narrower.
-    # Nothing stops pairing an activation with a block lying outside its
-    # montage's [start_s, end_s) window, which is a block the sort must not
-    # cover. The check needs Montage.start_s/end_s against Block.start_s/end_s
-    # and so cannot be a foreign key. Nothing writes this table in 1c-1; the
-    # responder (1c-3) is its first writer and owns enforcing the window.
-    -> Activation
-    -> core.Block
-    """
-
-
-
 @schema
 class ActivationRun(dj.Manual):
     definition = """
```

```diff
--- a/wl_preproc/schema/coverage.py
+++ b/wl_preproc/schema/coverage.py
@@ -1,10 +1,11 @@
 # wl_preproc/schema/coverage.py
-"""Per-trial and per-block coverage, one row per system.
+"""Per-run and per-trial coverage, one row per system.
 
-Section 5.2.1: a block partially covered by a probe is the state that matters —
-it is what wl.works asserts block_neural_assertion against, and what excludes a
-block from a sort. So `partial` is a first-class state, never collapsed into
-`absent`.
+Section 5.2.1: a stretch partially covered by a probe is the state that
+matters -- it is what excludes it from a sort, and since design spec
+`2026-10-01-session-listing-and-run-requests-design.md` the stretch a file is
+built from is a measured run. So `partial` is a first-class state, never
+collapsed into `absent`.
 """
 
 from __future__ import annotations
@@ -19,82 +20,44 @@ _COVERAGE_ENUM = "enum('full','partial','absent')"
 
 
 @schema
-class BlockCoverage(dj.Computed):
+class RunCoverage(dj.Computed):
     definition = f"""
-    # Coverage of one block by one system. Computed, not Manual: declared
-    # Manual in 1c-1 when nothing computed it, and it is the intersection of a
-    # block's interval with this system's segment extents.
-    # Key: (subject, session_datetime, block_id, system).
-    -> core.Block
+    # How much of one measured run each system recorded (design spec
+    # 2026-10-01-session-listing-and-run-requests-design.md section 5), which
+    # a file's readiness waits on. It replaced BlockCoverage, which did the
+    # same for wl.works' asserted blocks. The intersection of a run's
+    # interval with this system's segment extents.
+    # Key: (subject, session_datetime, run_number, system).
+    -> core.Run
     -> core.AcquisitionSystem
     ---
     coverage  : {_COVERAGE_ENUM}
-    covered_s : double  # seconds of the block this system actually recorded
+    covered_s : double  # seconds of the run this system actually recorded
     """
 
     @property
     def key_source(self):
-        """Every (block, system) pair of a session, whatever each system did.
+        """Every (run, system) pair of a session, whatever each system did.
 
         The cross product rather than a join through `Segment`: a system that
-        recorded NONE of a block still needs a row saying `absent`, and joining
+        recorded NONE of a run still needs a row saying `absent`, and joining
         through segments would silently omit exactly the systems whose absence
-        matters most. Section 5.2.1 is about blocks a system did not fully
+        matters most. Section 5.2.1 is about stretches a system did not fully
         cover; a missing row is not the same statement as `absent`.
         """
-        return core.Block * core.AcquisitionSystem
-
-    def make(self, key: dict) -> None:
-        """Intersect this block's interval with this system's segment extents.
-
-        **`Block.start_s`/`end_s` are wl.works' assertion, not our
-        measurement.** Closed open item 9: block rows are authored by wl.works'
-        session planner and wl-preproc never writes them. The MEASURED boundary
-        is a different quantity, decoded from event codes, and belongs to 1c-5
-        in its own table. A reader who assumes these boundaries were decoded
-        here will misread every row in this table, which is why it is said at
-        the point where they are read rather than only in the spec.
-
-        The segment extents ARE ours, and they are already in session time --
-        `Segment.start_s`/`end_s` carry each recording's extent after its own
-        offset fit, so no conversion happens here.
-        """
-        from wl_preproc.timebase.coverage import classify_coverage
-
-        block = (core.Block & key).fetch1("start_s", "end_s")
-        extents = [
-            (row["start_s"], row["end_s"])
-            for row in (core.Segment & key).to_dicts()
-        ]
-        state, covered_s = classify_coverage(block, extents)
-        self.insert1({**key, "coverage": state, "covered_s": covered_s})
-
-
-@schema
-class RunCoverage(dj.Computed):
-    definition = f"""
-    # How much of one measured run each system recorded (design spec
-    # 2026-10-01-session-listing-and-run-requests-design.md section 5): what
-    # BlockCoverage was for wl.works' asserted blocks, for the runs a file now
-    # holds. Key: (subject, session_datetime, run_number, system).
-    -> core.Run
-    -> core.AcquisitionSystem
-    ---
-    coverage  : {_COVERAGE_ENUM}
-    covered_s : double  # seconds of the run this system actually recorded
-    """
-
-    @property
-    def key_source(self):
-        """Every (run, system) pair of a session, whatever each system did:
-        the cross product, for `BlockCoverage.key_source`'s reason."""
         return core.Run * core.AcquisitionSystem
 
     def make(self, key: dict) -> None:
         """Intersect this run's measured interval with this system's segment
-        extents, by the rule `BlockCoverage.make()` calls. A run that faulted
-        at once can have no length: it covers nothing, and is `absent` rather
-        than a failure on every pass."""
+        extents, by `timebase/coverage.py`'s one rule. A run that faulted at
+        once can have no length: it covers nothing, and is `absent` rather
+        than a failure on every pass.
+
+        Both sides are ours and already in session time: `core.Run` is
+        decoded from the event codes, and `Segment.start_s`/`end_s` carry each
+        recording's extent after its own offset fit, so no conversion happens
+        here.
+        """
         from wl_preproc.timebase.coverage import classify_coverage
 
         run = (core.Run & key).fetch1("run_start_time", "run_stop_time")
@@ -129,7 +92,7 @@ class TrialCoverage(dj.Computed):
     def key_source(self):
         """Every (trial, system) pair of a session, whatever each system did.
 
-        The cross product, mirroring `BlockCoverage.key_source` exactly and
+        The cross product, mirroring `RunCoverage.key_source` exactly and
         for the identical reason: a system that recorded NONE of a trial still
         needs a row saying `absent`, and joining through `Segment` would
         silently omit exactly the systems whose absence matters most.
@@ -139,17 +102,16 @@ class TrialCoverage(dj.Computed):
     def make(self, key: dict) -> None:
         """Intersect this trial's interval with this system's segment extents.
 
-        The same rule `BlockCoverage.make()` calls -- `timebase/coverage.py`'s
-        own docstring already names this table by name as the second caller,
-        "so the rule has one definition rather than one per table." Nothing
-        here reimplements it.
+        The same rule `RunCoverage.make()` calls -- `timebase/coverage.py`'s
+        own docstring names this table as a caller, "so the rule has one
+        definition rather than one per table." Nothing here reimplements it.
 
         `trial.Trial.trial_stop_time` is not nullable (1c-5 Task 8: the
         synthetic generator now emits `Marker.TRIAL_END` for every trial, so no
         trial comes back with `end_s=None`), so a zero-or-negative-duration
         trial is not papered over here either -- `classify_coverage` raises,
-        and that is surfaced rather than caught, exactly as `BlockCoverage.
-        make()` leaves it.
+        and that is surfaced rather than caught. (`RunCoverage.make()` answers
+        a zero-length run itself, because a faulted run can have one.)
         """
         from wl_preproc.timebase.coverage import classify_coverage
 
```

```diff
--- a/wl_preproc/schema/timebase.py
+++ b/wl_preproc/schema/timebase.py
@@ -70,15 +70,13 @@ _TIER_ENUM = "enum('A','B','C','D')"
 # distinguish "checked, could not fit" from "not reached yet".
 _FIT_STATUS_ENUM = "enum('fitted','no_recording','unfittable')"
 
-# How close the measured block boundary must land to wl.works' own assertion
-# to count as agreeing used to be a fixed constant here (`1e-3`). Fix round 2:
-# that comment cited `timebase/segments.py`'s alignment durations as a
-# "chosen rather than derived" precedent -- wrong, since that module derives
-# its own numbers explicitly, and a real budget exists to derive this one
-# from too (`pipeline.trial.Block`'s float32 columns). Moved to
-# `events.agreement.block_agreement_tolerance_s`, which derives it from
-# float32 storage precision at the magnitude being compared -- see that
-# function's own docstring.
+# `TimingProvenance` once compared the measured block boundary with wl.works'
+# assertion of it (`block_agreement`), within a tolerance kept here and later
+# in `events.agreement`. Retired with `core.Block` (design spec
+# `2026-10-01-session-listing-and-run-requests-design.md` section 5): it was
+# computed once per session, before any request existed, so it never ran in
+# wl.works' flow. A request's runs are checked as it arrives instead
+# (`responder/jobs.py::_check_runs`).
 
 
 @schema
@@ -255,7 +253,6 @@ class TimingProvenance(dj.Computed):
     n_full_code_records        : int unsigned  # independent Pi/NI records that decoded content
     n_strobe_witnesses         : int unsigned  # RHS-style witnesses whose edge count matched
     decode_errors               : int unsigned  # DecodeErrors across every full-code record
-    block_agreement=null        : tinyint(1)  # measured trial.Block vs wl.works' core.Block; null unasserted
     """
 
     @property
@@ -277,11 +274,11 @@ class TimingProvenance(dj.Computed):
         record, the RHS strobe witness, and both cross-checks all need a
         SECOND, independent look at the raw files, which is exactly what
         `decode_errors`, `event_code_agreement`, the witness count, and
-        `trial_count_agreement`/`block_agreement` are for.
+        `trial_count_agreement` are for.
 
         **The pre-1c-5 timing check still forces D on its own, unconditionally,
         checked BEFORE `resolve_tier` is consulted at all.** None of
-        `TierInputs`' seven fields represents an alignment failure -- folding
+        `TierInputs`' six fields represents an alignment failure -- folding
         one into (say) `decode_errors` would corrupt a column whose whole
         point is to mean one specific, re-derivable thing.
 
@@ -296,7 +293,7 @@ class TimingProvenance(dj.Computed):
         it fires whenever ANY present system went unfitted -- the syncbox
         included. When the syncbox is the system that failed (`fit_status` of
         `no_recording` or `unfittable`, both reachable from
-        `SystemTimebase.make()`), `syncbox_fitted` is False and all seven
+        `SystemTimebase.make()`), `syncbox_fitted` is False and all six
         evidence columns keep their zero/`None` defaults, indistinguishable
         from a genuinely uncorroborated session. So the guarantee holds
         precisely when the failure is somewhere OTHER than the syncbox -- a
@@ -375,7 +372,7 @@ class TimingProvenance(dj.Computed):
         # measured input is still computed and stored even when [`failed`]
         # fires" -- which was false: that gate skipped the decode entirely
         # whenever `failed`, so a timing-failed session's row stored zeros
-        # and NULLs for every one of these seven columns, indistinguishable
+        # and NULLs for every one of these six columns, indistinguishable
         # from a genuinely uncorroborated one. Parent spec section 4.7's
         # re-derivability was lost exactly for the quarantined sessions a
         # human is most likely to actually go look at. `syncbox_fitted`
@@ -509,41 +506,6 @@ class TimingProvenance(dj.Computed):
                 )
                 camera_trigger_count = sidecar.frame_count - len(sidecar.dropped_frame_ids)
 
-        # -- block_agreement: the measured boundary (`trial.Block`) against
-        # wl.works' own assertion (`core.Block`). Design spec section 5: a
-        # disagreement here is its own tier-D condition, "not a silent
-        # reconciliation" -- `None` when wl.works asserted no blocks at all
-        # for this session (nothing to compare against; see `TierInputs.
-        # block_agreement`'s own comment for exactly how this parallels, and
-        # does not parallel, `trial_count_agreement`'s `None`). Matched by
-        # `block_id`, mirroring how `timebase/fit.py`'s own barcode matching
-        # is "by value, never by ordinal position". The tolerance itself is
-        # `agreement.block_agreement_tolerance_s` -- derived from float32
-        # storage precision at the magnitude actually being compared, not a
-        # fixed constant; see that function's own docstring (fix round 2).
-        asserted_blocks = {
-            row["block_id"]: (row["start_s"], row["end_s"])
-            for row in (core.Block & session_key).to_dicts()
-        }
-        block_agreement = None
-        if asserted_blocks:
-            measured_blocks = {
-                row["block_id"]: (row["block_start_time"], row["block_stop_time"])
-                for row in (pipeline.trial.Block & session_key).to_dicts()
-            }
-            block_agreement = all(
-                block_id in measured_blocks
-                and abs(measured_blocks[block_id][0] - asserted_start)
-                <= agreement.block_agreement_tolerance_s(
-                    measured_blocks[block_id][0], asserted_start
-                )
-                and abs(measured_blocks[block_id][1] - asserted_end)
-                <= agreement.block_agreement_tolerance_s(
-                    measured_blocks[block_id][1], asserted_end
-                )
-                for block_id, (asserted_start, asserted_end) in asserted_blocks.items()
-            )
-
         inputs = agreement.TierInputs(
             event_code_agreement=event_code_agreement,
             trial_count_agreement=trial_count_agreement,
@@ -551,7 +513,6 @@ class TimingProvenance(dj.Computed):
             n_full_code_records=n_full_code_records,
             n_strobe_witnesses=n_strobe_witnesses,
             decode_errors=decode_errors,
-            block_agreement=block_agreement,
         )
         tier = "D" if failed else agreement.resolve_tier(inputs)
 
@@ -587,7 +548,6 @@ class TimingProvenance(dj.Computed):
                 "n_full_code_records": n_full_code_records,
                 "n_strobe_witnesses": n_strobe_witnesses,
                 "decode_errors": decode_errors,
-                "block_agreement": block_agreement,
             }
         )
 
```

```diff
--- a/wl_preproc/events/agreement.py
+++ b/wl_preproc/events/agreement.py
@@ -10,25 +10,20 @@ underlying count is retained on the row so the verdict can be re-derived
 under different thresholds later. `resolve_tier` therefore takes only the
 measured inputs and holds no state of its own.
 
-**`block_agreement` is a fourth, later addition -- not one of "the three".**
-Design spec section 5 (the 1c-5 phase design, not the parent) makes a
-disagreement between the measured block boundary (`trial.Block`) and
-wl.works' own assertion (`core.Block`) its own tier-D condition, distinct
-from design spec section 7's three. 1c-5 Task 9 found `TierInputs` had no
-field for it -- a gap between the design spec and the plan that built this
-module -- and closed it here, following the exact precedent
-`trial_count_agreement` already set for "nothing to compare against" rather
-than inventing a second convention.
-
-**`code_agreement` and `block_agreement_tolerance_s`, fix round 2.** Two more
-pure functions, added after review: how two independent full-code records
-are compared (content-matched, tolerant of a dropped or inserted word --
-design spec section 4.2 requirement 1's own property, applied one level up
-from the codec), and how close a measured block boundary must land to
-wl.works' own assertion to count as agreeing (derived from float32 storage
-precision, not chosen). Both are called from `schema/timebase.py::
-TimingProvenance.make()`, which supplies the raw decoded code lists and
-boundary values respectively; neither touches DataJoint or a file, matching
+**A fourth input, `block_agreement`, is retired** (design spec
+`2026-10-01-session-listing-and-run-requests-design.md` section 5). It
+compared the measured block boundary with wl.works' assertion of it
+(`core.Block`), but `TimingProvenance` computes once per session, before any
+request exists, so it never ran in wl.works' flow. A request's runs are
+checked as it arrives (`responder/jobs.py::_check_runs`), within
+`RUN_AGREEMENT_TOLERANCE_S` below.
+
+**`code_agreement`, fix round 2.** A pure function added after review: how
+two independent full-code records are compared (content-matched, tolerant
+of a dropped or inserted word -- design spec section 4.2 requirement 1's own
+property, applied one level up from the codec). Called from
+`schema/timebase.py::TimingProvenance.make()`, which supplies the raw
+decoded code lists; it touches neither DataJoint nor a file, matching
 `resolve_tier`'s own pure, stateless shape.
 """
 
@@ -38,7 +33,6 @@ from collections.abc import Sequence
 from dataclasses import dataclass
 from difflib import SequenceMatcher
 
-import numpy as np
 
 # Two independent records must agree on this fraction of their codes to count
 # as agreeing at all. Stated here rather than inlined so the threshold is one
@@ -55,17 +49,6 @@ class TierInputs:
     n_full_code_records: int
     n_strobe_witnesses: int
     decode_errors: int
-    # Design spec section 5: "A disagreement between `trial.Block` (measured)
-    # and `core.Block` (asserted) is a tier-D condition, not a silent
-    # reconciliation." Added in 1c-5 Task 9 -- the original TierInputs had no
-    # field for this, so the condition the spec names could not be expressed
-    # at all. `None` when there is nothing to compare against (wl.works
-    # asserted no blocks for this session): neither field's `None` is ever
-    # read as agreement, the one principle genuinely shared with
-    # `trial_count_agreement`'s own `None` -- see `resolve_tier`'s docstring
-    # for where the two fields' EFFECTS actually diverge, which a fix round 2
-    # correction found this comment had previously overstated.
-    block_agreement: bool | None = None
 
 
 def resolve_tier(inputs: TierInputs) -> str:
@@ -88,35 +71,11 @@ def resolve_tier(inputs: TierInputs) -> str:
     exactly D's job: the tiers have no fifth state for "never checked", and D
     is the quarantined tier that is not auto-published, which is the correct
     home for both "checked and failed" and "never checked at all".
-
-    **`block_agreement is False` is a D condition too -- 1c-5 Task 9, design
-    spec section 5.** "A disagreement between `trial.Block` (measured) and
-    `core.Block` (asserted) is a tier-D condition, not a silent
-    reconciliation."
-
-    **`block_agreement is None` is NOT treated exactly like
-    `trial_count_agreement is None` -- fix round 2 correction.** A commit
-    message once said it was; it wasn't, and review caught the sentence
-    rather than the code, which was already right.
-    `trial_count_agreement` has TWO jobs here: it forces D when `False`
-    (above), and it is ALSO a precondition the C branch below requires to be
-    `True` specifically -- so its `None` fails both the D-check and the
-    C-requirement, and the session falls through to D by elimination.
-    `block_agreement` has only the first job: nothing below ever requires it
-    to be `True` to reach any tier. So its `None` is simply inert here -- it
-    fails to trigger THIS one D-check and affects nothing else -- not
-    "blocked from C the same way `trial_count_agreement`'s is". What the two
-    fields genuinely share is narrower than "identical treatment": neither
-    field's `None` is ever read as agreement, but only `trial_count_agreement`
-    is also a precondition a tier can be earned by satisfying. Block-boundary
-    corroboration is a check this protocol can FAIL, not one it can PASS.
     """
     if inputs.decode_errors:
         return "D"
     if inputs.trial_count_agreement is False:
         return "D"
-    if inputs.block_agreement is False:
-        return "D"
     if inputs.event_code_agreement is not None and (
         inputs.event_code_agreement < AGREEMENT_THRESHOLD
     ):
@@ -175,41 +134,12 @@ def code_agreement(reference: Sequence[int], other: Sequence[int]) -> float:
     return matched / max(len(reference), len(other))
 
 
-# Half a float32 ULP is the exact bound a double-to-float32 storage
-# round-trip can introduce -- element-event's own `trial.Block` declares
-# `block_start_time`/`block_stop_time` as `float` (single precision), while
-# `core.Block`'s `start_s`/`end_s` are `double` (confirmed directly against
-# `element_event/trial.py`). k=2 covers the comparison itself, not the
-# block's two endpoints (start_s and end_s each get their own call to
-# `block_agreement_tolerance_s`, below).
-#
-# **k=2 is conservative margin here, not a derived bound -- fix round 4
-# correction.** Only ONE side of this comparison is stored as float32, so
-# only one half-ULP is ever in play: `core.Block`'s `double` side carries no
-# float32 rounding at all, by this comment's own premise two sentences up.
-# The previous justification -- that "in the general case" the compared value
-# can be off by a half-ULP too, so the pair's worst case is two of them --
-# named a term it had already excluded. What k=2 actually buys is a full ULP
-# of headroom over a term provably at most half of one: free against a
-# genuine disagreement, which is orders of magnitude larger, and still
-# correct if the asserted side is ever narrowed to float32 as well. Kept for
-# that reason, and stated as margin rather than re-derived into a bound it
-# is not.
-BLOCK_AGREEMENT_TOLERANCE_K = 2
-
-# Fix round 3 correction: this floor used to be `1e-6`, matched to
-# `timebase/coverage.py`'s `_FULL_TOLERANCE_S` -- wrong, because that
-# constant is a floor under float64 ACCUMULATION error, which is not a
-# physical quantity, and this tolerance sits under a MEASUREMENT, which has
-# a resolution. The resolution is the transport's: the shared strobe bus
-# carries one code word at a time (`synth/timeline.py`'s own `_emit`:
+# The resolution of a boundary carried on the event codes: the shared strobe
+# bus carries one code word at a time (`synth/timeline.py`'s own `_emit`:
 # "words can never overlap ... if two logical events want the same instant,
-# the second waits"), and a block boundary is transported as one specific
-# code word in one specific slot -- so it cannot be measured any finer than
-# the spacing between slots. That is the actual explanation for the 1 ms
-# this project's very first fixed tolerance happened to cover: not
-# "generous slack", but exactly one slot's worth of transport quantization,
-# covered by accident rather than by argument.
+# the second waits"), and a run or block boundary is transported as one
+# specific code word in one specific slot -- so it cannot be measured any
+# finer than the spacing between slots.
 #
 # `MIN_CODE_WORD_SLOT_S` matches `synth/timeline.py`'s own
 # `CODE_WORD_SPACING_S` (0.001) -- restated, not imported: `wl_preproc.
@@ -225,133 +155,14 @@ BLOCK_AGREEMENT_TOLERANCE_K = 2
 # sibling implementation rather than a replacement then.
 MIN_CODE_WORD_SLOT_S = 0.001
 
-# How many slots the MEASURED block boundary can sit from the NOMINAL one --
-# traced exhaustively through every transition `build_timeline` produces,
-# not merely observed once. A block's own `BLOCK_START` escape word (whose
-# time IS `AssembledBlock.start_s`) is ratcheted exactly one slot past its
-# nominal instant: by `SESSION_START` for the very first block, or by the
-# PREVIOUS block's own `BLOCK_END` for every block after it -- and both
-# resolve to the identical one-slot displacement, by construction of
-# `_emit`'s ratchet (`earliest = words[-1][0] + CODE_WORD_SPACING_S`), for
-# any block with at least one trial.
-#
-# **A zero-trial block is outside this bound, and the refusal this comment
-# claimed did not exist -- fix round 4.** The claim was that such a block
-# "has zero duration and is refused elsewhere, by `classify_coverage`, before
-# this comparison is ever reached". Both halves were false:
-# `classify_coverage` is called only on `core.Block` and `trial.Trial`
-# (`schema/coverage.py`), never on the measured `pipeline.trial.Block` this
-# constant governs; and `daemon.run_once` populates with
-# `suppress_errors=True`, so a raise there could not have stopped
-# `TimingProvenance` even if it had been reached. Measured rather than
-# argued: a nominal `(0.0, 0.0)` block lands at `(0.001, 0.005)`, with the
-# FOLLOWING block starting at `0.006` -- six slots, three times the floor
-# below, i.e. a spurious tier-D quarantine. The guard now genuinely exists,
-# one level up, at the only place such a block can be described at all:
-# `synth/recipe.py`'s `BlockSpec.n_trials` carries `ge=1`, so no
-# `SessionRecipe` can express a zero-duration block and none reaches here.
-#
-# `BLOCK_END`, by contrast, lands EXACTLY at its nominal instant -- zero
-# displacement: its own target (`cursor - CODE_WORD_SPACING_S/2`) is
-# dominated by the ratchet from the preceding `TRIAL_END`
-# (`cursor - CODE_WORD_SPACING_S`, one slot earlier), which resolves to
-# exactly `cursor`.
-#
-# **That zero has a domain of its own, which the word "always" used to hide.**
-# The `TRIAL_END` ratchet dominates only while each trial is long enough to
-# carry its own seven code words (`TRIAL_START`, the four-word `TRIAL_NUMBER`
-# payload, the outcome marker, `TRIAL_END`) AND repay the five the session
-# head spends before the first trial (`SESSION_START` plus the four-word
-# `BLOCK_START` payload). Swept over `trial_duration_s` and trial count: the
-# displacement is zero at 0.012 s and above for every trial count tried (1,
-# 2, 3, 5, 10, 40), and degrades below it -- a single-trial block is 2 slots
-# off at 0.010 s, and every block is 5 slots off at 0.007 s, where a trial
-# can no longer hold its own words at all. So the bound reads "for any trial
-# longer than about a dozen code-word slots", not "always". No plausible
-# behavioural trial is 12 ms long and all five shipped recipes use 3 s or
-# 6 s, but the bound has an edge and a reader should be told where it is.
-#
-# Verified against a live session, not only traced by hand:
-# `tests/schema/test_timebase.py`'s `provenance_session` fixture measures
-# `block_start_time == 0.001` and `block_stop_time == recipe.duration_s`
-# exactly.
-#
-# **This bias is real, systematic and ONE-SIDED, not symmetric noise.**
-# `_emit`'s own `max(at_s, earliest)` can never return a time earlier than
-# the nominal `at_s` it was asked for -- so the measured boundary is always
-# >= the nominal one, never earlier, for any code word this generator
-# places. A tolerance built from it is absorbing a known, signed
-# quantization in one direction, not guarding against noise that could go
-# either way; a reader relying on this constant should not mistake it for
-# the latter.
-_BLOCK_START_MAX_SLOTS = 1
-
-# The floor doubles the proven 1-slot bound above rather than using it bare,
-# for a separate and much smaller reason than the bound itself being
-# uncertain: the transported VALUE is also subject to the same float32
-# storage rounding `BLOCK_AGREEMENT_TOLERANCE_K` exists for, stacked on top
-# of the slot quantization, and comparing two floats at an exact
-# theoretical boundary (`diff == floor` to the last representable bit) is
-# fragile regardless of how solid the derivation behind the boundary is.
-# Doubling costs nothing against a genuine disagreement, which is orders of
-# magnitude larger than either the slot quantization or the storage
-# rounding on top of it.
-BLOCK_AGREEMENT_TOLERANCE_FLOOR_S = 2 * _BLOCK_START_MAX_SLOTS * MIN_CODE_WORD_SLOT_S
-
-
-def _float32_half_ulp(value: float) -> float:
-    """Half the gap between adjacent float32 values at `value`'s own
-    magnitude.
-
-    `numpy.spacing` on a float32 input, not `numpy.finfo(numpy.float32).eps
-    * value`: `eps` is the ULP AT 1.0 specifically, and multiplying it by an
-    arbitrary value reproduces that value's true ULP only at a power of two
-    -- elsewhere in the binade it underestimates by up to 2x, silently
-    licensing too tight a tolerance. `spacing` steps to the correct binade
-    for any input and is exact there, which is what a bound needs to be.
-    """
-    return float(np.spacing(np.float32(value))) / 2.0
-
-
-def block_agreement_tolerance_s(*magnitudes: float) -> float:
-    """How close a measured block boundary must land to wl.works' own
-    assertion, at these magnitudes, to count as agreeing -- two DERIVED
-    terms, neither chosen. Fix round 2: a prior fixed `1e-3` cited
-    `timebase/segments.py`'s alignment durations as "chosen rather than
-    derived" precedent -- wrong, since that module derives its own numbers
-    explicitly. Fix round 3: the float32 term that replaced it was right,
-    but its floor (`1e-6`) was matched to `timebase/coverage.py`'s
-    `_FULL_TOLERANCE_S` -- also wrong, because that constant floors float64
-    ACCUMULATION error, not a physical quantity, while this tolerance sits
-    under a MEASUREMENT with an actual resolution. See
-    `BLOCK_AGREEMENT_TOLERANCE_FLOOR_S`'s own comment for that resolution
-    (the strobe bus's one-code-word-per-slot transport) and the exhaustive
-    trace behind it.
-
-    Design spec section 5 makes a disagreement here its own tier-D
-    condition. Two things make a FIXED tolerance wrong at some magnitude:
-    `pipeline.trial.Block`'s own columns are float32 (see
-    `BLOCK_AGREEMENT_TOLERANCE_K`'s comment), so the MEASURED side of this
-    comparison always carries up to one float32 half-ULP of pure storage
-    rounding, and that half-ULP DOUBLES every time the magnitude doubles --
-    0.977 ms at 16384 s (4.5h into a session), 1.953 ms at 32768 s (9.1h).
-    A fixed 1 ms tolerance already consumes nearly its entire budget on
-    storage rounding alone at 4.5h and is provably too tight past 9.1h: a
-    false tier-D quarantine on an honestly agreeing long session, for a
-    reason that has nothing to do with whether the blocks actually agree --
-    exactly the false-verdict shape parent spec section 4.7's whole
-    apparatus exists to avoid, reintroduced by an underived term. And a
-    fixed tolerance UNDER one slot's transport quantization is wrong at
-    short magnitudes for the opposite reason: it would reject an honestly
-    agreeing SHORT session too, quarantining it for exactly the same kind
-    of reason -- a measurement resolution mistaken for a disagreement.
-
-    Callers pass every value entering ONE boundary comparison (typically the
-    measured time and the asserted time for a single endpoint), so the
-    returned tolerance is scaled to what is actually being compared, never to
-    the session's total duration or some other quantity nothing here checks.
-    """
-    return max(
-        BLOCK_AGREEMENT_TOLERANCE_FLOOR_S,
-        BLOCK_AGREEMENT_TOLERANCE_K * max(_float32_half_ulp(m) for m in magnitudes),
-    )
+# How close a request's run must land to the measured `core.Run` to count as
+# the same run (design spec `2026-10-01-session-listing-and-run-requests-design.md`
+# section 3.2: "within the agreement tolerance ..., about 2 ms"). wl.works
+# asserts its copy of `GET /sessions`' measured value, and the value is a
+# double the whole way -- `core.Run`, the listing's JSON, wl.works' own record
+# -- so an honest request agrees exactly and no storage rounding needs
+# absorbing. Two code-word slots, the floor the retired block check derived
+# (one slot of transport quantization, doubled so a comparison never sits on
+# its boundary to the last bit). What it refuses is a run measured
+# differently from the one wl.works copied: a stale listing.
+RUN_AGREEMENT_TOLERANCE_S = 2 * MIN_CODE_WORD_SLOT_S
```

```diff
--- a/wl_preproc/responder/jobs.py
+++ b/wl_preproc/responder/jobs.py
@@ -279,7 +279,7 @@ def _check_runs(session_key: dict, montage_row: dict, asserted: list[RunEntry],
             raise ValueError(f"run {number} is not a measured run of this session; {_REBUILD}")
         for end, asserted_s, measured_s in (("start", entry.start_s, run["run_start_time"]),
                                             ("end", entry.end_s, run["run_stop_time"])):
-            if abs(asserted_s - measured_s) > agreement.block_agreement_tolerance_s(measured_s, asserted_s):
+            if abs(asserted_s - measured_s) > agreement.RUN_AGREEMENT_TOLERANCE_S:
                 raise ValueError(f"run {number}'s {end}, {asserted_s} s, is not the measured {measured_s} s; "
                                  f"{_REBUILD}")
         rows.append({**session_key, "run_number": number, "works_run_id": entry.works_run_id,
```

```diff
--- a/wl_preproc/daemon.py
+++ b/wl_preproc/daemon.py
@@ -72,7 +72,7 @@ def _computed_tables() -> list:
     """The computed tables, in dependency order.
 
     The ordering is load-bearing rather than tidy. ``Segment.make()`` needs its
-    system's rate to already exist and ``BlockCoverage.make()`` needs the
+    system's rate to already exist and ``RunCoverage.make()`` needs the
     segments — and neither dependency is expressed as a ``key_source``,
     deliberately: keying ``Segment`` off ``SystemTimebase`` would mean a system
     with no fit produced no rows at all, including the ``RejectedSegment`` rows
@@ -170,10 +170,9 @@ def _computed_tables() -> list:
         # names the run it reads (design spec `2026-09-30-nwb-probes-design.md`
         # section 2.1).
         ephys.ProbeCensus,
-        coverage.BlockCoverage,
-        # After `Segment`, whose extents it intersects, as `BlockCoverage`.
+        # After `Segment`, whose extents it intersects.
         coverage.RunCoverage,
-        # After `Segment` for exactly `BlockCoverage`'s reason -- it intersects
+        # After `Segment` for exactly `RunCoverage`'s reason -- it intersects
         # a trial's interval with this system's segment extents -- and after
         # `_populate_event_stage()`, which is what puts rows in the
         # `pipeline.trial.Trial` half of its `key_source`. `run_once` runs that
```

```diff
--- a/wl_preproc/cli/deleting.py
+++ b/wl_preproc/cli/deleting.py
@@ -19,14 +19,16 @@ asking to be believed. ``pipeline.Session`` (and, for ``TrialCoverage``,
 deleted by this preview, so neither appears in the graph.
 
 **This is not simply the brief's list with the direction flipped.** Its first
-draft ordered these nine tables as one flat list, sliced at ``from_stage``.
-That cannot be made correct by reordering: ``BlockCoverage`` depends on both
-``Block`` and ``AcquisitionSystem``, and ``ActivationBlock`` on both
-``Activation`` and ``Block``, so the tables form a DAG with real branches, not
-a chain. Deleting from ``AcquisitionSystem`` must not claim ``Block`` — a
-sibling, not a descendant — is affected, and deleting from ``Montage`` must
-not claim ``Segment`` is; no single linear order slices correctly for both.
-The fix computes each stage's actual dependency closure instead.
+draft ordered these tables as one flat list, sliced at ``from_stage``. That
+cannot be made correct by reordering: the tables form a DAG with real
+branches, not a chain. When the map held them, ``BlockCoverage`` depended on
+both ``Block`` and ``AcquisitionSystem`` and ``ActivationBlock`` on both
+``Activation`` and ``Block``; today ``Segment`` and ``RunCoverage`` share
+``AcquisitionSystem`` while ``Activation`` hangs from ``Montage``. Deleting
+from ``AcquisitionSystem`` must not claim ``Activation`` — a sibling, not a
+descendant — is affected, and deleting from ``Montage`` must not claim
+``Segment`` is; no single linear order slices correctly for both. The fix
+computes each stage's actual dependency closure instead.
 """
 
 from __future__ import annotations
@@ -37,7 +39,6 @@ from __future__ import annotations
 # relies on that instead of maintaining a second ordering by hand.
 _PARENTS: dict[str, tuple[str, ...]] = {
     "Montage": (),
-    "Block": (),
     "AcquisitionSystem": (),
     # `Request` is here because `Activation` names it, not because deleting a
     # request is a routine thing to want: the foreign key added on 2026-08-14
@@ -48,9 +49,7 @@ _PARENTS: dict[str, tuple[str, ...]] = {
     "Activation": ("Montage", "Request"),
     "Segment": ("AcquisitionSystem",),
     "RejectedSegment": ("AcquisitionSystem",),
-    "BlockCoverage": ("Block", "AcquisitionSystem"),
     "TrialCoverage": ("AcquisitionSystem",),
-    "ActivationBlock": ("Activation", "Block"),
     # wl.works' asserted runs and the run sets of an activation (design spec
     # `2026-10-01-session-listing-and-run-requests-design.md` section 3.3).
     # Their other parent, the measured `core.Run`, is the event stage's and is
@@ -91,15 +90,12 @@ def _assert_known_tables_are_real() -> None:
 
     tables = {
         "Montage": core.Montage,
-        "Block": core.Block,
         "AcquisitionSystem": core.AcquisitionSystem,
         "Request": request.Request,
         "Activation": request.Activation,
         "Segment": core.Segment,
         "RejectedSegment": core.RejectedSegment,
-        "BlockCoverage": coverage.BlockCoverage,
         "TrialCoverage": coverage.TrialCoverage,
-        "ActivationBlock": request.ActivationBlock,
         "RunAssertion": core.RunAssertion,
         "RunCoverage": coverage.RunCoverage,
         "ActivationRun": request.ActivationRun,
```

```diff
--- a/wl_preproc/timebase/coverage.py
+++ b/wl_preproc/timebase/coverage.py
@@ -1,13 +1,13 @@
 """How much of an interval a system's segments actually cover.
 
-Section 5.2.1: a block partially covered by a probe is the state that matters --
-it is what wl.works asserts `block_neural_assertion` against, and what excludes
-a block from a sort. So `partial` is a first-class state and is **never**
+Section 5.2.1: a stretch partially covered by a probe is the state that
+matters -- it is what excludes it from a sort, and the stretch a file is built
+from is a measured run. So `partial` is a first-class state and is **never**
 collapsed into `absent`; section 4.6 states the same rule from the other side,
 that a recording which stopped mid-trial must never be silently treated as
 complete.
 
-Pure interval arithmetic: no DataJoint, no I/O. `coverage.BlockCoverage.make()`
+Pure interval arithmetic: no DataJoint, no I/O. `coverage.RunCoverage.make()`
 and 1c-5's `TrialCoverage.make()` both call this, so the rule has one
 definition rather than one per table.
 """
```

```diff
--- a/wl_preproc/synth/recipe.py
+++ b/wl_preproc/synth/recipe.py
@@ -67,12 +67,12 @@ class BlockSpec(BaseModel):
     # `ge=1`, not a bare `int`: a zero-trial block has zero duration, and
     # `timeline.py` still emits its `BLOCK_START` payload and its `BLOCK_END`
     # for it, so the block's MEASURED boundary lands several code-word slots
-    # away from a nominal `(0.0, 0.0)` -- far enough to trip
-    # `TimingProvenance.block_agreement` and quarantine an otherwise clean
-    # session at tier D. `events/agreement.py`'s `_BLOCK_START_MAX_SLOTS`
-    # derives its one-slot bound assuming at least one trial; this is where
-    # that assumption is enforced, and it is enforced here because a recipe is
-    # the only place a zero-trial block can be described at all.
+    # away from a nominal `(0.0, 0.0)` and displaces the block after it. That
+    # once quarantined a clean session at tier D through
+    # `TimingProvenance.block_agreement`, retired since (design spec
+    # `2026-10-01-session-listing-and-run-requests-design.md` section 5); the
+    # generator's block timing still assumes a trial, and a recipe is the only
+    # place a zero-trial block can be described at all, so it is refused here.
     n_trials: int = Field(ge=1)
     trial_duration_s: float
     stim_per_trial: int = 0
```

```diff
--- a/wl_preproc/contracts/protocol.py
+++ b/wl_preproc/contracts/protocol.py
@@ -213,8 +213,8 @@ class ProbeEntry(BaseModel):
     #
     # So this host records what arrived and infers nothing from its absence.
     # Null here means "no trajectory was supplied with this request" and NOT
-    # which of the reasons applies -- the same discipline as `core.Block`'s
-    # "recording an assertion is not authoring it".
+    # which of the reasons applies -- the discipline `core.RunAssertion` keeps
+    # for runs: recording an assertion is not authoring it.
     #
     # **It is not a quarantine condition, and must never be confused with one.**
     # Design spec section 8.3's "no insertion record -> no canonical" is about a
```

```diff
--- a/wl_preproc/schema/ephys.py
+++ b/wl_preproc/schema/ephys.py
@@ -140,8 +140,9 @@ class ProbeInsertion(dj.Manual):
     # penetration for which no trajectory resource exists.
     #
     # This host does not distinguish them and must not try. Null means "no
-    # trajectory arrived with the request" and nothing further -- the same
-    # discipline as `core.Block`'s "recording an assertion is not authoring it".
+    # trajectory arrived with the request" and nothing further -- the
+    # discipline `core.RunAssertion` keeps for runs: recording an assertion is
+    # not authoring it.
     # wl-works' section 9 item 1 leaves the discrimination open on their side
     # and warns against "a null that means three things"; their item 2 is why
     # the no-planned-parent case is legitimate rather than an error.
@@ -395,7 +396,7 @@ class Clustering(dj.Manual):
         # reference this part instead, so an electrode a unit names must be one
         # of the electrodes its own sort declared.
         #
-        # NOT ENFORCED HERE, in the same sense ActivationBlock records: the
+        # NOT ENFORCED HERE, in the sense a foreign key cannot reach: the
         # master's `-> ElectrodeConfig` is BELOW its divider, so a part row
         # cannot inherit it into this key, and nothing at the database level
         # ties these rows to that configuration. Whatever populates this table
```

```diff
--- a/wl_preproc/schema/events.py
+++ b/wl_preproc/schema/events.py
@@ -398,9 +398,10 @@ def populate_session(key: dict, session_dir: Path) -> None:
     to it; every attribute value below is a stringified scalar written to
     `attribute_value` instead.
 
-    **`core.Block` is never written here.** It holds wl.works' own assertion,
-    authored elsewhere (spec section 8.3.1); this function writes only the
-    MEASURED boundary, into `trial.Block`.
+    **Nothing wl.works asserts is written here.** Its copy of a run lives in
+    `core.RunAssertion`, recorded by `responder/jobs.py::accept`; this
+    function writes only the MEASURED boundaries, into `trial.Block` and
+    `core.Run`.
     """
     session_key = {k: key[k] for k in pipeline.Session.primary_key}
 
@@ -563,9 +564,7 @@ def populate_session(key: dict, session_dir: Path) -> None:
     if trial_rows:
         pipeline.trial.Trial.insert(trial_rows, allow_direct_insert=True, skip_duplicates=True)
 
-    # -- Block: the MEASURED boundary (design spec section 5). core.Block --
-    # wl.works' own ASSERTION -- is never written here or anywhere in this
-    # pipeline.
+    # -- Block: the MEASURED boundary (design spec section 5).
     block_rows = [
         {
             **session_key,
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 22 passed.

Then every reader of what went: `.venv/bin/python -m pytest tests/schema/test_core.py tests/schema/test_coverage.py tests/timebase tests/schema/test_daemon.py tests/cli tests/archive/test_reclaim.py tests/events tests/schema/test_timebase.py tests/schema/test_guardrails.py tests/test_cli_guardrails.py tests/responder tests/schema/test_request.py tests/synth/test_recipe.py -q -p no:cacheprovider`. Expected: 675 passed, 1 skipped.

- [ ] **Step 5: Mutation checks.**
  - T3a (`responder/jobs.py::_check_runs`): `if abs(asserted_s - measured_s) > agreement.RUN_AGREEMENT_TOLERANCE_S:` becomes `if False:` [`test_a_request_from_a_stale_listing_is_a_run_mismatch_and_writes_nothing`, all four cases].
  - T6a (`events/agreement.py`): the tolerance becomes one slot [`test_a_run_agrees_within_two_code_word_slots`].

- [ ] **Step 6: Commit**

```bash
git add -u wl_preproc tests
git commit -m "feat(schema): retire core.Block, ActivationBlock, BlockCoverage and block_agreement

What runs replaced is gone: core.RunAssertion and request.ActivationRun stand
for core.Block and ActivationBlock, RunCoverage for BlockCoverage, and the run
check on arrival for TimingProvenance.block_agreement, which was computed
before any request existed and so never ran in wl.works' flow. The run check
keeps its 2 ms as RUN_AGREEMENT_TOLERANCE_S, two code-word slots; the block
derivation behind the old floor (_BLOCK_START_MAX_SLOTS, piece 1's deferred
M1) goes with it. A development database needs TimingProvenance redeclared.

<trailer lines>"
```

---

### Task 7: The records, and the full suite

**Files:**
- Modify: `docs/ops/lab-host-protocol.md`, `docs/pending-wl-works-amendments.md`, `docs/superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md`, `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-10-01-run-requests.md`

- [ ] **Step 1: The protocol, and what wl.works is told.** Apply:

```diff
--- a/docs/ops/lab-host-protocol.md
+++ b/docs/ops/lab-host-protocol.md
@@ -314,12 +314,15 @@ than something one side can do quietly.
   "selection": {
     "session_datetime": "2027-06-01T09:00:00+00:00",
     "montage_id": 0,
-    "block_ids": [1, 2, 3]
+    "probe_runs": {"19011110001": [1, 2]}
   },
   "parameters": {},
   "idempotency_key": "6f1c2f8e-…",
   "metadata": {
-    "blocks": [],
+    "runs": [
+      {"run_number": 1, "start_s": 0.002, "end_s": 1803.5, "works_run_id": "asr-311"},
+      {"run_number": 2, "start_s": 1810.0, "end_s": 3604.25, "works_run_id": "asr-312"}
+    ],
     "montage_boundaries": [],
     "probes": [
       {
@@ -355,14 +358,28 @@ than something one side can do quietly.
   spellings — four whole-second (`+00:00`, `Z`, no offset, `11:00:00+02:00`) and six
   fractional (`.000`, `.123`, `.000123`, `.600`, `.999999`, and `.123` at `+02:00`) — every
   one `200` with `"session_datetime": "2027-08-04T09:00:00"`.
-  `block_ids`, when present and non-empty, makes the request a **derivative** activation
-  over that hand-picked block set; absent or empty makes it the **canonical** activation
-  for `(session, montage)`.
+  **A request names runs** (design spec
+  `2026-10-01-session-listing-and-run-requests-design.md` §3):
+  - `run_numbers`, when present and non-empty, makes the request a **derivative**
+    activation holding those whole runs, each measured, in the montage's window, and
+    asserted in `metadata.runs` (now or by an earlier request). Its identity is its task
+    type and its run set.
+  - Absent or empty, the request is the **canonical** activation for `(session, montage)`,
+    which holds **every measured run whose start lies in the montage's `[start_s, end_s)`
+    window**. Each must be asserted in `metadata.runs`.
+  - `probe_runs`, a canonical's only: for **every** probe in `metadata.probes`, keyed by
+    serial, the runs its sort covers, **stated in full** (the record is the run set each
+    probe resolved to, not the exclusions), and only the file's runs. An empty list leaves
+    that probe out of sorting. This is how a run bad on one probe is left out of that
+    probe's sort only.
+  - **`block_ids` is retired**, and so is `metadata.blocks`: a non-empty one is a `422`
+    naming its replacement.
+
   **Since the canonical lifecycle** (design spec `2026-09-30-canonical-lifecycle-design.md`
   §3), two optional keys say more:
-  - `"role": "canonical"` with `block_ids` asks for a canonical over those blocks. This is
-    how wl.works leaves out a bad block. `"role": "derivative"` means what `block_ids`
-    alone means.
+  - `"role": "canonical"` is the canonical above; a canonical cannot name a subset of its
+    montage's runs (`run_numbers` with it is a `422`). `"role": "derivative"` means what
+    `run_numbers` alone means, and needs them.
   - `"supersedes_activation_id": N`, with `"role": "canonical"`, is a **replacement**: a
     new canonical superseding `N`, which must be the montage's current canonical. The
     superseded file stays where it is, readable, and `GET /nwb` marks it. A replacement
@@ -372,11 +389,29 @@ than something one side can do quietly.
     one, returns the montage's **current** canonical.
 - **`metadata`** is the bundle this host needs from the ELN, and it is the reason this
   protocol works pull-only: everything wl-preproc needs arrives inbound with the request,
-  because this host cannot call wl.works to ask. `montage_boundaries` and `blocks` are
-  wl.works' own authored records; this host records them **if absent and never overwrites
-  them**, since a later request carrying corrected boundaries is wl.works correcting its
-  own record, which is its call to make explicitly rather than something to infer from
-  whichever payload arrived last.
+  because this host cannot call wl.works to ask. `montage_boundaries` is wl.works' own
+  authored record; this host records it **if absent and never overwrites it**, since a
+  later request carrying corrected boundaries is wl.works correcting its own record, which
+  is its call to make explicitly rather than something to infer from whichever payload
+  arrived last.
+- **`metadata.runs`** lists every run you hold for the session: `run_number`, `start_s` and
+  `end_s` — your copy of what `GET /sessions` listed, measured from the recording — and
+  `works_run_id`, your `animal_session_run` id. **Every run the file holds is checked
+  against this host's measured run as the request arrives**, start and end within 2 ms
+  (`events/agreement.py::RUN_AGREEMENT_TOLERANCE_S`); a copy of the listing agrees exactly.
+  - **A stale listing is a `422`**: a run this host did not measure, its times off, or a
+    measured run in the montage's window that the request does not assert. The message
+    names the run and ends *rebuild the request from a fresh GET /sessions*. Stop and show
+    it.
+  - **A session whose runs are not measured yet is a `422` that clears itself**: the event
+    stage has not read it, or the rig sent no run markers. Resend once `GET /sessions`
+    lists its runs.
+  - **A run already recorded under one `works_run_id` and named under another is a
+    `409`.** The two records disagree about which run it is; the first is kept.
+  - **A session with a repeated run number** (a crash restart before wl-xcon's XC-026) has
+    only the first of each measured, so a montage cannot include the restarted runs.
+  - An empty `metadata.blocks`, which every request sent while it was required, is
+    accepted and says nothing.
 - **`metadata.probes`** lists each insertion: its probe's `serial` and its
   `insertion_number`, with an optional `trajectory_id`, and, since the probes design
   (`2026-09-30-nwb-probes-design.md` §4), two more optional keys:
@@ -389,8 +424,8 @@ than something one side can do quietly.
     `asserted_at` is ISO-8601, like `session_datetime`, but is **kept to the
     microsecond**, since two assignments may fall in one second. There is no atlas key,
     because your assignment row lists no atlas column.
-  - **The latest request wins** for an insertion, unlike `blocks` and
-    `montage_boundaries`, and as for `subject_details`: you are the authority on where a
+  - **The latest request wins** for an insertion, unlike `montage_boundaries` and a run's
+    `works_run_id`, and as for `subject_details`: you are the authority on where a
     probe went. Assignments accumulate, as in your own table, and a request repeating one
     adds nothing. An insertion that a request does not mention is left as it was, so a
     request may name only its own montage's insertions.
@@ -465,12 +500,19 @@ Authorization: Bearer <token>
   - `placement`: `tier` (`fast` or `slow`), `host`, `share`, `path` relative to the share,
     and `n_bytes`, or `null` until published;
   - `description`: [`docs/schemas/nwb_description.json`](../schemas/nwb_description.json),
-    or `null` for a refused activation. **Version 2** (since the probes design,
-    `2026-09-30-nwb-probes-design.md` §3.2) gives `probes` its shape: each probe's
+    or `null` for a refused activation. **Version 3** (design spec
+    `2026-10-01-session-listing-and-run-requests-design.md` §4) replaces `blocks` with
+    `runs`, since version 2 allowed only additions and this renames and removes. Each run
+    of the file: `run_number`, `works_run_id` (join it to `animal_session_run`), `task`
+    (`code` and `name`), its `measured` interval, `closed`, `trials` (total and by
+    outcome), each system's `coverage`, its `conditions`, and its measured `blocks`
+    (`block_number`, `block_in_run`, `block_type`, `measured`, `closed`, `trials`). Each
+    probe also carries `sorted_runs`, its list from the request. **Version 2** (the probes
+    design, `2026-09-30-nwb-probes-design.md` §3.2) gave `probes` its shape: each probe's
     `serial`, `probe_type`, `insertion_number`, `trajectory_id`, `n_electrodes`, `target`
     and `assignment`, and `area_from`, which says whether the file's area label came from
-    the assignment, the aim, or neither. A file built before then says version 1 and has
-    no probes; read `schema_version`, and ignore fields you do not know.
+    the assignment, the aim, or neither. Read `schema_version`, and ignore fields you do
+    not know.
 
 A file changes when it is built, published, or moved between shares.
 
@@ -555,7 +597,7 @@ Every code this host can return, on any endpoint.
 | `404` | all | `{"error": "not found"}` | Path is not one of `/health`, `/jobs`, `/nwb`, `/nwb/active`; or a query string on any path but `/nwb`. | No |
 | `405` | all | `{"error": "method not allowed"}` | Known path, wrong verb — `GET /jobs`, `POST /health`, `GET /nwb/active`, authenticated `PUT /health`. | No |
 | `408` | `POST /jobs`, `PUT /nwb/active` | `{"error": "request timed out"}` | The declared body never fully arrived. | **Yes** |
-| `409` | `POST /jobs` | `{"error": "<what differed>"}` | Idempotency key reused for materially different content; or a replacement naming a canonical that is not the montage's current one. | **No — needs a human** |
+| `409` | `POST /jobs` | `{"error": "<what differed>"}` | Idempotency key reused for materially different content; a replacement naming a canonical that is not the montage's current one; or a run named under a `works_run_id` other than the one recorded. | **No — needs a human** |
 | `414` | all | `{"error": "request line too long"}` | Over-long request line. | No |
 | `422` | `POST /jobs`, `PUT /nwb/active`, `GET /nwb` (a `since` that is not one non-negative integer) | `{"error": "…"}` or `{"error": "invalid request body", "detail": […]}` | The request is malformed, or asks for something this host cannot do — **including naming a session it has not ingested yet**. | No — fix and resend; for a not-yet-ingested session, resend once the transfer lands |
 | `431` | all | `{"error": "request header fields too large"}` | Oversized header. | No |
@@ -582,14 +624,18 @@ arrive as ordinary responses with a status line, as does every other code above.
   documentation links stripped), including `extra_forbidden` for an unknown field.
 - **A well-formed request this host refuses**: a `selection` missing `session_datetime` or
   `montage_id`; a `session_datetime` that is not parseable ISO-8601 or is before year 1000;
-  an oversized `subject`; **a session this host has not ingested yet** (see below); a
-  `montage_id`, `block_id`, `task_type`, `works_block_id`, `start_s` or `end_s` that will
-  not fit its column; a `montage_id` with no boundary on record and none supplied in the
-  request either; a `block_ids` entry naming no block anywhere; a `block_ids` entry
-  naming a block outside its montage's `[start_s, end_s)` window; a `role` other than
-  `canonical` or `derivative`; `supersedes_activation_id` without `"role": "canonical"`,
-  or not a non-negative integer; or `"role": "derivative"` without `block_ids`. The
-  message names what was wrong.
+  an oversized `subject`; **a session this host has not ingested yet** (see below), or
+  whose runs it has not measured yet; a `montage_id` that will not fit its column; a
+  `montage_id` with no boundary on record and none supplied in the request either; **a
+  run that does not match the measured one** — not measured, its times more than 2 ms
+  off, a measured run in the window left unasserted, or a derivative's run outside the
+  window — whose message ends *rebuild the request from a fresh GET /sessions*; a
+  `probe_runs` that does not state every probe's runs, or names a run the file does not
+  hold; `run_numbers` on a canonical, or `probe_runs` on a derivative; the retired
+  `block_ids` or a non-empty `metadata.blocks`; a `role` other than `canonical` or
+  `derivative`; `supersedes_activation_id` without `"role": "canonical"`, or not a
+  non-negative integer; or `"role": "derivative"` without `run_numbers`. The message
+  names what was wrong.
 
 **A session this host has not ingested yet is a `422`, and it is the ordinary case.** You
 know a session exists from the ELN the moment it is created; this host knows it exists only
@@ -603,7 +649,7 @@ hours, sometimes overnight — a job posted for that session is refused:
 The full message, which is one line on the wire:
 
 > session `<subject>`/`<session_datetime>` is not yet on record on this host: no Session row
-> exists for it, so there is nothing to attach a montage, a block or a request to. wl.works
+> exists for it, so there is nothing to attach a montage, a run or a request to. wl.works
 > knows a session exists from the ELN before its data transfer lands here; until ingest has
 > landed it, this host cannot accept a job for it. Resend once the transfer has completed.
 
@@ -664,9 +710,12 @@ arrived late when in fact it arrived completely and something else was slow.
 
 ### `409` is not retryable, and that is the point
 
-`409 Conflict` has exactly one cause on this host: **an idempotency key was reused for
-materially different content.** The request cannot succeed as sent, and the remedy is a
-*new key*, which Plan 10 §6.1 puts outside the retry loop's power to produce — the key is
+`409 Conflict` has three causes on this host, and resending cures none of them: **an
+idempotency key reused for materially different content**; a replacement naming a canonical
+that is not the montage's current one; and **a run already recorded under one
+`works_run_id` named under another**, where the two records disagree about which run it is
+and a person must settle it. For the first, the request cannot succeed as sent, and the
+remedy is a *new key*, which Plan 10 §6.1 puts outside the retry loop's power to produce — the key is
 minted once when the confirmation dialog is accepted and reused across every retry of that
 intent, never regenerated per click.
 
```

```diff
--- a/docs/pending-wl-works-amendments.md
+++ b/docs/pending-wl-works-amendments.md
@@ -72,7 +72,27 @@ approved by the requester), and wl.works told the same day. **Ask 1 is BUILT** (
 - **Two refinements before vendoring** (the listing's deferred minors, 2026-10-01): a segment's
   `probes` is null until the probe census has read it, and a new flag, `rig_record_problem`,
   names what wl-xcon's run record could not supply.
-- **Asks 3 and 4, and item 5,** are the same spec's Plan B, which follows.
+- **Asks 3 and 4, and item 5, are BUILT** (its Plan B, branch `spec/run-requests`). Vendor
+  `docs/schemas/job_request.json` and `docs/schemas/nwb_description.json` once it is on `main`:
+  - **A canonical request asserts runs**, in `metadata.runs` (`run_number`, `start_s`, `end_s`,
+    `works_run_id`), and holds every measured run whose start lies in its montage's window, each
+    asserted. A derivative names its runs in `selection.run_numbers`.
+  - **Each probe's runs** are `selection.probe_runs`, keyed by serial and stated in full for
+    every probe, as your Plan 20 §1.2 rule has it. A run bad on one probe is left out of that
+    probe's list only.
+  - **The check runs as the request arrives,** start and end within 2 ms of `core.Run`. The old
+    block check, `TimingProvenance.block_agreement`, was computed before any request existed, so
+    it never ran in your flow; it is retired with `core.Block`.
+  - **What the answers mean:** a `422` ending *rebuild the request from a fresh GET /sessions* is
+    a stale listing, so stop and show it. A `422` saying the session has no measured run yet
+    clears itself: resend once `GET /sessions` lists its runs. A `409` naming a run's
+    `works_run_id` means the two records disagree about which run it is: stop.
+  - **`metadata.blocks` and `selection.block_ids` are refused** with a `422` naming
+    `metadata.runs` and `selection.run_numbers`; an empty `metadata.blocks` is accepted. A
+    canonical can no longer name a subset of its montage's runs.
+  - **The NWB description is version 3:** `runs` replaces `blocks`. Each run carries
+    `works_run_id`, to join `animal_session_run`, with its task, interval, `closed`, trials,
+    coverage, conditions and measured blocks; each probe carries `sorted_runs`.
 
 ---
 
@@ -91,6 +111,9 @@ it must not guess a montage (parent spec §8.3, "no insertion record → no cano
 **This repository's half is built.**
 - **A canonical request may name its block set:** `selection` gains `"role": "canonical"` with
   `block_ids`. Without `role`, a request means what it always meant.
+  *True when written. Since the run requests (2026-10-01) a canonical holds every run of its
+  montage, and a run bad on one probe is left out of that probe's `selection.probe_runs`;
+  `block_ids` is refused. See "measured runs, and what a block is now" above.*
 - **A replacement names what it supersedes:** `"role": "canonical"` with
   `"supersedes_activation_id": N`.
   - `N` must be the montage's current canonical.
@@ -113,6 +136,7 @@ it must not guess a montage (parent spec §8.3, "no insertion record → no cano
    wl.works'. A `422` naming a session this host has not ingested yet is the ordinary
    "not yet": retry it.
 2. **Leave out bad blocks** by sending `"role": "canonical"` with `block_ids`.
+   *Since 2026-10-01: leave a bad run out of each probe's `selection.probe_runs` instead.*
 3. **Regenerate with a replacement** naming the current canonical, and treat a `409` as a
    disagreement for a person.
 4. **Read `superseded_by`** in `GET /nwb`, alongside wl.works' own `supersedesId` and
@@ -146,6 +170,7 @@ every connection, as always ([`docs/ops/lab-host-protocol.md`](ops/lab-host-prot
   decide anything (its own Plan 20 §4.5 asks for exactly this). Per block it names the task, the
   conditions that ran with their stimulus settings and trial counts, and per-system coverage;
   per file, the subject, the data types present, the timing tier and the checksums.
+  *Since version 3 (2026-10-01) these are per run, each naming its `works_run_id`.*
 - **Checksums are `sha256`** of each written-once dataset's decoded contents, as Plan 24 §3.3 and
   its item 1 settle.
 - **`PUT /nwb/active`** takes the whole set of activations that belong on the fast share, every
```

wl-xcon is asked nothing by this plan (spec §7); Plan A told it what is read.

- [ ] **Step 2: The spec's amendments.** Apply:

```diff
--- a/docs/superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md
+++ b/docs/superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md
@@ -369,3 +369,61 @@ settle what the sections above left open; §0 stands.
     a faulted run writes none are corrected (final review M1, raised to Important as a false claim
     about another repository).
 
+
+## Amendments, 2026-10-01, made while proving Plan B
+
+Plan B (`plans/2026-10-01-run-requests.md`) was proven in code before it was written. These
+settle what the sections above left open; §0 stands.
+
+13. **What a run holds is decided in each table's own precision,** in one place
+    (`events/runs.py`): a block's or a trial's start as float32, as `trial.Block` and
+    `trial.Trial` store it, and an event's as `decimal(10,4)`, as `event.Event` stores it, with
+    the run's bounds rounded the same way first. This is amendment 11's rule, extended to trials
+    and events; no block's run is stored at the event stage.
+14. **A trial belongs to the run its start lies in,** and a `RUN_START` event lying on its run's
+    start is in that run (§4: trials, events and eye data are trimmed to the runs' measured
+    intervals).
+15. **`/intervals/blocks` holds the measured blocks inside the file's runs,** and is left out
+    when they hold none. Its `closed` is 1, 0, or -1 for a closure never recorded (amendment 10);
+    the description's is true, false or null. `/intervals/runs` names each run's `works_run_id`,
+    task code and name, `closed` and each system's coverage.
+16. **The run check's tolerance is two code-word slots, 2 ms, at every magnitude**
+    (`events/agreement.py::RUN_AGREEMENT_TOLERANCE_S`). A run's times are doubles from
+    `core.Run` through the listing's JSON to wl.works' copy, so an honest request agrees exactly
+    and no float32 term is needed. The block check's derivation (`_BLOCK_START_MAX_SLOTS` and its
+    float32 half-ULP) goes with `block_agreement`, as §5 says of M1.
+17. **`metadata.blocks` stays in the contract, optional, deprecated and `maxItems: 0`.** A
+    non-empty one is refused by the contract itself, naming `metadata.runs`; an empty one, which
+    every request sent while the field was required, is accepted. `selection.block_ids`,
+    non-empty, is refused by `accept()`, naming `selection.run_numbers`. The contract's refusal
+    is pydantic's custom error: a raised `ValueError` is kept, as an object, in the error pydantic
+    reports, the `422` body could not serialise it, and the refusal went out as a `500`.
+18. **A canonical cannot name a subset of its montage's runs** (§3.2, decision 1):
+    `"role": "canonical"` with `run_numbers` is refused. The canonical lifecycle's "a canonical
+    names its block set" (its spec §3) is replaced by the per-probe lists.
+19. **`selection.probe_runs` names exactly the probes in `metadata.probes`,** each with only the
+    file's runs; a canonical with no probes needs none. A derivative's runs may have been
+    asserted by an earlier request: each must be in `metadata.runs` or `core.RunAssertion`.
+20. **A session with no measured run cannot be requested** (§3.2's `422`, *not yet ingested*),
+    so the probe-linking tests report an insertion after the event stage and before the census:
+    the earliest a report can now arrive.
+21. **A run with no length is `absent` in `RunCoverage`,** with 0 s: a run that faulted at once
+    would otherwise fail the coverage stage on every pass.
+22. **Fixture fixed:** the SpikeGLX generator ended the NI file exactly where the last strobe
+    ended, so the last word had no falling edge and was never latched. `CI_RECIPE` kept its
+    `SESSION_END` only by rounding; with runs on, `SESSION_END` moved to 15.002 s and was
+    dropped, `event_code_agreement` fell to 49/50, and the session read tier D. The buffer now
+    runs one low sample past the last strobe.
+23. **§8's items for Plan B, answered:**
+    - **3:** `core.Block` was read by `BlockCoverage`, `TimingProvenance.make()` and the
+      responder's window check; `ActivationBlock` by `nwb/gather.py`'s block set; all three
+      tables by `wlpp delete`'s map; `block_agreement` by `resolve_tier` alone. `wlpp report`
+      reads none of them: its tests' fixtures only wrote the column. Each reader moved to runs or
+      went with its table.
+    - **4:** `BlockCoverage.make()` intersected the block's interval with the system's
+      `Segment` extents through `timebase/coverage.py::classify_coverage`. `RunCoverage` does the
+      same over the measured run (and amendment 21).
+    - **7:** the description's `blocks` were written by `nwb/describe.py` and served as stored by
+      `GET /nwb`. Nothing in this repository read them but its tests, and wl.works' use of
+      them is recorded as still open on its side (`pending-wl-works-amendments.md`, "NWB
+      files", item 1).
```

- [ ] **Step 3: The checkpoint, `wl.yaml` and the handoff.** Apply:

```diff
--- a/docs/CHECKPOINT.md
+++ b/docs/CHECKPOINT.md
@@ -598,6 +598,19 @@ requester chose to merge the same day; true when written.*
 >    **Plan B, requests that name runs and the NWB description at version 3,
 >    follows.** See `docs/handoffs/2026-10-01-session-listing.md`.
 >
+>    **Requests that name runs, and the NWB description at version 3 (Plan B
+>    of pieces 2 and 3), are BUILT on `spec/run-requests` (2026-10-01), NOT
+>    merged as written** (the same spec). A canonical request asserts its
+>    montage's measured runs, each checked against `core.Run` within 2 ms as
+>    it arrives, and states each probe's runs in full; a derivative names
+>    whole runs. The NWB file is built from its runs: `/intervals/runs`, the
+>    measured blocks inside them, and a description at version 3 whose runs
+>    carry `works_run_id`. `core.Block`, `ActivationBlock`, `BlockCoverage`
+>    and `TimingProvenance.block_agreement` are retired; a development
+>    database redeclares `TimingProvenance`. **wl.works vendors
+>    `job_request.json` and `nwb_description.json` once this merges.** See
+>    `docs/handoffs/2026-10-01-run-requests.md`.
+>
 > **Deferred minors: DONE 2026-09-26, both lists.** The gap-aware branch's
 > items 1–3 (`8af4278`; `eye/detect/validity.py` now cites commit `7d4a00f`
 > in place of a "finding H2" no document named) and all six parked
```

```diff
--- a/wl.yaml
+++ b/wl.yaml
@@ -178,6 +178,13 @@ status:
     and raw ~imroTbl, and its flags, re-listed whenever its entry changes
     (design spec `2026-10-01-session-listing-and-run-requests-design.md`, Plan
     A; handoff `docs/handoffs/2026-10-01-session-listing.md`).
+    Requests that name runs are BUILT on `spec/run-requests` (2026-10-01, NOT
+    merged as written): a canonical asserts its montage's measured runs, each
+    checked against core.Run within 2 ms as it arrives, with each probe's runs
+    stated in full; a derivative names whole runs; the NWB file is built from
+    its runs, with a description at version 3; and core.Block, ActivationBlock,
+    BlockCoverage and block_agreement are retired (the same spec, Plan B;
+    handoff `docs/handoffs/2026-10-01-run-requests.md`).
   next: >-
     **Hardware status as of 2026-09-19, stated by the requester at session
     close: the rig is NOT ready and the compute machine is NOT assembled.**
```

Run `wl-check` on its own. Expected: `wl.yaml: no findings`.

Create the handoff:

```markdown
# Requests that name runs, and the NWB description at version 3

**Plan B of pieces 2 and 3, designed together.** Plan A, the landed-session listing, is merged
(`eb1ff06`, its minors `63fd606`). Piece 4, a metadata-only rebuild, comes after this.
- **Branch:** `spec/run-requests`, forked from `main` at `6a67ae2`.
- **Spec:** `docs/superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md`
  (`89ad4e9`, amended `03c6784`, and in Plan A's branch). It is amended again in this branch's
  last commit, amendments 13 to 23.
- **Plan:** `docs/superpowers/plans/2026-10-01-run-requests.md`.
- **The requester's choices:** a canonical file keeps every run of its montage; a repeated run
  number lists the first and flags the session; a derivative selects whole runs.

Every line of the plan was proven in a scratch worktree before the plan was written.

---

## 1. What was built

- **The tables runs are asserted and selected in:** `core.RunAssertion` (wl.works' id and copy of
  a measured run), `request.ActivationRun` (a file's runs) and `request.ActivationProbeRun` (each
  probe's), and `coverage.RunCoverage`, a daemon stage the file's readiness waits on.
- **A request names runs** (`responder/jobs.py`). `metadata.runs` asserts every run wl.works
  holds; a canonical holds every measured run whose start lies in its montage's window, with
  `selection.probe_runs` stating each probe's runs in full; a derivative names its runs in
  `selection.run_numbers`, and its identity is its task type and run set.
- **Each run is checked against `core.Run` as the request arrives,** within 2 ms. A stale listing
  is a `422` naming the run; a session whose runs are not measured yet is a `422` that clears
  itself; a run named under a second `works_run_id` is a `409`.
- **The NWB file is built from its runs** (`nwb/gather.py`, `nwb/intervals.py`): its trials,
  events and eye data are trimmed to the runs' measured intervals; `/intervals/runs` is new, the
  blocks table holds the measured blocks inside the runs, and each trial carries its run. The
  description is version 3: `runs` replaces `blocks`, and each probe carries `sorted_runs`.
- **Retired:** `metadata.blocks` and `selection.block_ids` (a `422` naming their replacements),
  `core.Block`, `request.ActivationBlock`, `coverage.BlockCoverage`, and
  `TimingProvenance.block_agreement` with `TierInputs.block_agreement`.
- **The generator** ends the NI file one low sample past its last strobe, so a session whose
  blocks sit in runs keeps its `SESSION_END` (amendment 22).

## 2. What the other repositories must do

- **wl.works** (`pending-wl-works-amendments.md`, "measured runs, and what a block is now"):
  vendor `docs/schemas/job_request.json` and `docs/schemas/nwb_description.json` once this is on
  `main`. Build requests from `GET /sessions`: assert every run, state each probe's runs, and
  read the `422`s and the `409` as the protocol document says. Join the description's runs to
  `animal_session_run` by `works_run_id`.
- **wl-xcon:** nothing is asked by this plan.

## 3. Still open

- **A development database** redeclares `timebase.TimingProvenance`, which loses
  `block_agreement`, and may drop the orphaned `core.block`, `request.activation_block` and
  `coverage.block_coverage` tables. No real database exists yet.
- **The sorter is not built.** `request.ActivationProbeRun` is what it will read for each probe's
  runs.
- **Piece 4,** the metadata-only rebuild, is next.
- **Plan A's deferred minors M6 and M9** remain, as its handoff §7 records.

## 4. The rulings

Each is a dated amendment in the spec:
13. **Run containment is decided in each table's own precision,** in `events/runs.py`.
14. **A trial belongs to the run its start lies in;** a `RUN_START` on its run's start is in it.
15. **The NWB blocks table holds the runs' measured blocks,** left out when there are none.
16. **The run check's tolerance is two code-word slots, 2 ms,** with no float32 term.
17. **`metadata.blocks` stays in the contract, deprecated and `maxItems: 0`;** the refusal is
    pydantic's custom error, because a `ValueError` made the `422` a `500`.
18. **A canonical cannot name a subset of its montage's runs.**
19. **`probe_runs` names exactly the request's probes;** a derivative's run may have been asserted
    earlier.
20. **A session with no measured run cannot be requested,** so reports arrive after the event
    stage.
21. **A run with no length is `absent`.**
22. **Fixture fixed:** the NI file's last strobe now falls.
23. **§8's items 3, 4 and 7, answered.**
```

Add §5, the measured counts, and §6, the final review, as execution measures them.

- [ ] **Step 4: The full suite, once, on both interpreters.** With the BMD and NSLR references set and `WLPP_OHDPI_REFERENCE` unset, as CI has it:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider > .superpowers/sdd/2026-10-01-run-requests/full311.log 2>&1; echo "311 exit $?"
~/.cache/wl-preproc-venv313/bin/python -m pytest -q -p no:cacheprovider > .superpowers/sdd/2026-10-01-run-requests/full313.log 2>&1; echo "313 exit $?"
```

Expected: `311 exit 0` and `313 exit 0`, **2130 passed, 25 skipped, 1 deselected, 1 xfailed** on 3.11 and **2129 passed, 27 skipped, 1 xfailed** on 3.13, proven with every task applied. The plan adds 26 tests net (2130 collected at `main`'s `6a67ae2`, 2156 after), having removed the block tests it retires.

- [ ] **Step 5: Commit**

```bash
git add docs/ops/lab-host-protocol.md docs/pending-wl-works-amendments.md docs/superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md docs/CHECKPOINT.md wl.yaml docs/handoffs/2026-10-01-run-requests.md
git commit -m "docs: requests that name runs -- the protocol, what wl.works is told, the spec's amendments, the checkpoint, wl.yaml and the handoff

<trailer lines>"
```
