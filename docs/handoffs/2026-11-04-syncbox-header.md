# Reading wl-sync's header change: one clock per process, the barcode delay, and lost events

**`specs/2026-11-04-syncbox-header-design.md`**: wl-sync's header change of 2026-10-30 (its `fe1ea63`). Every
segment's header now names the process that wrote it, the measured barcode delay and the clocks its ticks count;
the box rolls once in place when NTP first syncs; and an `L` record marks capture events it saw and could not
record. The pin here refused all of it, and wl-sync holds the box's deploy until it has moved.
- **Branch:** `feat/syncbox-header`, from `plan/syncbox-header`.
- **Spec:** the design, its decisions of 2026-10-30, its five rulings (approved 2026-11-06), and its amendments
  1-10, made while proving the plan, and 11-14, made in the final review.
- **Plan:** `docs/superpowers/plans/2026-11-04-syncbox-header.md`.

Every line of the plan was proven in a scratch branch before the plan was written.

---

## 1. What was built

- **The pin** moves to wl-sync's `main` at `fe1ea63`; `docs/schemas/syncbox_log_header.json` is regenerated.
- **The reader** (`timebase/_syncbox_log.py`) carries each segment's process, delay and clocks, its barcodes with
  the delay added, its code words and its losses, reads each file once, and names a file it cannot read.
- **A clock is a process** (`timebase/segments.py::SyncBoxSession`): one process's segments are one clock, each
  boundary adjacent, a gap, or a contradiction read as a restart; each process's late first barcode is dropped
  and t = 0 is the first kept; every reader of the box's barcodes takes its `B` records as they are.
- **Recorded:** `timebase.SyncBoxSegment` gains each segment's process, clocks, how it follows the one before,
  and its last code word's tick, and `SyncBoxSegment.Loss` holds each `L` record as written and each gap; a
  database declared before refuses the stage and names its `ALTER TABLE`.
- **The timing check:** a `desync`, or a gap or lost code words inside a measured run, make the session tier D.
- **The report** names each loss in its 24 h with what it did, each session whose code words are on a split clock
  with their worst-case drift at 50 ppm, and why a segment that contradicts its clock is read as a restart.
- **The synthesizer** writes the box's log as the box does today: process, clocks, the late first frame, a roll
  in place, a gap, a restart's new process, and `L` records built through wl-sync's own class.
- **Part 1's eight deferred findings** are fixed (spec §9).

## 2. What the other repositories must do

- **wl-sync:** nothing to build. Told with the merge that the pin has moved and the box may run its change;
  asked to confirm that a `W` tick, in seconds, is the time since its process's capture began
  (`HANDOVER-wl-sync.md`, its answers of 2026-11-06).

## 3. Still open

- **Part 2 of the 2026-10-22 design,** the bridges, now bridges a new process.
- **Code words on a split clock** are reported, not corrected; wl-sync's one-clock design is decided after bench
  day.

## 4. The rulings

The spec's five rulings and its amendments 1-14.

## 5. What execution measured

Executed from 2026-11-06: Task 1 `dbf773b`, Task 2 `6719d8a`, Task 3 `ea3ea8f`, Task 4 `37dd6ad`, Task 5
`c6e7f16`.
- **The full suite, once, on both interpreters,** with the BMD and NSLR references set, as CI has them, the
  Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, as in CI:
  - 3.11: **2664 passed, 26 skipped, 1 deselected, 1 xfailed, 53 warnings in 3484.49s (0:58:04)**;
  - 3.13: **2664 passed, 27 skipped, 1 xfailed, 53 warnings in 3552.46s (0:59:12)**.

## 6. The final review

A fresh Opus reviewer read the branch (`573c611..751b211`) and found **no Critical, two Important and eight
Minor**, judging it ready to merge with fixes. The five review-focus inputs behave as specified, and part 1's
deferred findings are fixed. The re-grade by effect raised two Minors to Important. All four were fixed in
`0c462f7`, each by a test that failed first (the spec's amendments 11-14), and reverting each fix fails its
test (8 of 8):
- **A roll's boundary mixed the capture paths** (amendment 11):
  `test_a_code_word_that_crosses_a_roll_behind_a_barcode_leaves_one_clock`.
- **A loss window closed at its ends, and a faulted run's end** (amendment 12):
  `test_a_loss_fails_the_timing_check_where_it_can_have_cost_a_runs_codes`,
  `test_a_faulted_runs_end_is_unknown_until_the_next_run_starts` and
  `test_a_faulted_runs_stop_reaches_the_timing_check_as_unknown`.
- **A segment's losses past 65,535, and a chattering line in the report** (amendment 13):
  `test_a_segment_keeps_more_losses_than_a_smallint_counts` and
  `test_a_lines_many_edge_losses_are_one_line_of_the_report`.
- **The report on a database declared before the header** (amendment 14):
  `test_the_report_on_a_table_read_without_the_columns_names_the_statement`.
- **Asked of the requester:** a gap that swallows a whole run is reported only. It overlaps no measured run,
  since the box lost every code word that would have measured it; the NI agreement and the task file's checks
  usually catch it. Whether it fails the session, as a desync does, is theirs to decide.
- **Deferred, Minor:** after a roll, a later segment's `core.Segment` extent runs from its process's origin, so
  the extents overlap and a gap's stretch reads as covered, though the session is tier D when the gap is in a
  run (`timebase/extract.py`); the log cache's key misses a replacement that keeps size and mtime, and it holds
  up to 256 logs in the daemon (`timebase/_syncbox_log.py`); `events/extract.py::extract_syncbox_words` has no
  production caller and reads the log a second way; `timebase` is a `varchar(64)` and wl-sync's is free text;
  the synthetic desync keeps writing code words after it; a session's code-word losses are still one entry
  each.
- **Set aside by the reviewer, and the rulings on them** (all stand): a desync after the last run fails the
  session (§6); a large step at NTP's first sync reads as a contradiction (the 2 s threshold; wl-sync promises
  +1 or +2); an unreadable segment's tier D is part 1's; the loss check reads the box directly, part 3b's
  concern once the NI keeps time; the decoded and emitted counts include a later process (amendment 8 narrowed
  §9.4 to the matched count); part 1's rows are not recomputed after the `ALTER TABLE` (nothing real is
  stored); `read_log` parses every `E` record, for the scaling piece; the checkpoint's top block is updated at
  the merge; `HANDOVER-wl-sync.md`'s item 3 does not mention amendment 1.

**The full suite after the fixes,** at `0c462f7`: 3.11, **2678 passed, 26 skipped, 1 deselected, 1 xfailed, 53 warnings in 2858.49s (0:47:38)**; 3.13, **2678 passed, 27 skipped, 1 xfailed, 53 warnings in 2962.37s (0:49:22)**, both exiting 0.
