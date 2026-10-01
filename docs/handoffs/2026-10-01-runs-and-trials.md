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
- **A repeated run or block number** (a crash restart before XC-026) is kept first in
  `core.Run` and `trial.Block` and is recoverable from `Event`, but nothing names it yet. The
  session list (piece 2) must (spec amendment 8).
- **A faulted run followed by manual rewards** stops at the last reward before the next run
  (spec amendment 9).
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
7. **A block lies inside its run** (from the final review).
8. **A repeated run or block number is kept first and named by piece 2.** *Cost if wrong:* until
   then a repeat is visible only in `Event`.
9. **A run's last event includes codes strobed between runs.** *Cost if wrong:* a faulted run
   followed by manual rewards stops late, inside the gap.

## 5. Measured

- **Every task's failing run and passing run matched the plan,** on this branch, in order:
  9/23, 8/25, 3/30, 6/0 and 4/49 failed/passed before each task's code; 32, 304, 227,
  135 (1 skipped) and 53 passed after it.
- **All 23 mutations were caught** by the tests the plan names. T2e's second test arrives in
  Task 5, so it was re-run there and caught by both.
- **The full suite, once, with every task applied:** 2055 passed, 25 skipped, 1 deselected,
  1 xfailed on 3.11, and 2054 passed, 27 skipped, 1 xfailed on 3.13. The plan adds 20 tests.
- **`wl-check`:** `wl.yaml: no findings`. **`schemas export`:** no file under `docs/schemas`
  changed.
- **Two rulings in execution,** both text: a test docstring's unread claim about wl-xcon was cut,
  and wl-xcon's session-levels plan is cited on its branch `session-levels-design`, where it is.

## 6. The final review

One fresh Opus reviewer read the whole branch, and checked its claims about wl-xcon against
wl-xcon's own files.
- **Critical, fixed:** with runs marked, an unclosed block's stop was the next run's `RUN_START`, so
  a faulted trial's stop crossed the gap between runs. A block now ends with its run (spec
  amendment 7). Tests: `test_a_faulted_trial_stops_inside_its_own_run_when_runs_are_marked`,
  `test_a_block_left_open_ends_with_its_run`, and two new assertions in the runs tests. Each failed
  first.
- **Important, partly fixed:** a repeated run or block number was silently dropped. Every
  `RUN_START`'s numbers are now kept in `Event`; naming the repeat is piece 2's (amendment 8).
- **Important, fixed:** the wl.works note now says its asserted blocks are checked against
  measured ones, and why blocks asserted from runs would fail.
- **Important, ruled to stand:** a run's last event includes codes strobed between runs
  (amendment 9).
- **Minor, fixed:** `core.Block`'s comment placed `Run` below it and said wl.works was revising its
  blocks, which no wl.works document says.
- **Four minors deferred,** in the ledger and the final message; M3 (`RUN_START`'s payload not stored) was fixed with the repeat finding.

- **The full suite after the fix pass:** 2057 passed, 25 skipped, 1 deselected, 1 xfailed on 3.11,
  and 2056 passed, 27 skipped, 1 xfailed on 3.13.
