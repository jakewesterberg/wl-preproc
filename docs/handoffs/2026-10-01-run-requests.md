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
It was executed on 2026-10-02, inline (the requester's choice), task by task. Each task's
tree is identical to its proof commit on the local branch `proof/run-requests`; these
records differ from the proof's only in dating the build 2026-10-02 and in §5 and §6.

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

## 5. What execution measured

Every count below was read off this branch's own runs on 2026-10-02, and each matched the plan's.
- **Each task's tests failed first, as the plan said they would:** Task 1, 5 failed and 81
  passed; Task 2, 4 and 57; Task 3, 25 and 89; Task 4, 71, 146 and 1 skipped; Task 5, 91 and
  164; Task 6, 2 and 20.
- **Each task's neighbours passed:** 258 and 1 skipped; 72; 313; 635 and 2 skipped; 376; 675
  and 1 skipped.
- **All 23 mutation checks the plan names failed their tests:** T1a–b, T2a–b, T3a–f, T4a–g,
  T5a–d and T6a.
- **The exported schemas** (`job_request.json` twice, `nwb_description.json` once) matched the
  plan's diffs byte for byte.
- **The full suite, once, on both interpreters,** with the BMD, NSLR and Andersson references
  set and `WLPP_OHDPI_REFERENCE` unset, as CI has it:
  - 3.11: **2130 passed, 25 skipped, 1 deselected, 1 xfailed**;
  - 3.13: **2129 passed, 27 skipped, 1 xfailed**.
  - Both ran on `ec799e2` with these records uncommitted.
- **`wl-check`:** `wl.yaml: no findings`.
