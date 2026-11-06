# Reading wl-sync's header change: one clock per process, the barcode delay, and lost events

**`specs/2026-11-04-syncbox-header-design.md`**: wl-sync's header change of 2026-10-30 (its `fe1ea63`). Every
segment's header now names the process that wrote it, the measured barcode delay and the clocks its ticks count;
the box rolls once in place when NTP first syncs; and an `L` record marks capture events it saw and could not
record. The pin here refused all of it, and wl-sync holds the box's deploy until it has moved.
- **Branch:** `feat/syncbox-header`, from `plan/syncbox-header`.
- **Spec:** the design, its decisions of 2026-10-30, its five rulings (approved 2026-11-06), and its amendments
  1-10, made while proving the plan.
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

The spec's five rulings and its amendments 1-10.

## 5. What execution measured

Executed from 2026-11-06: Task 1 `dbf773b`, Task 2 `6719d8a`, Task 3 `ea3ea8f`, Task 4 `37dd6ad`, Task 5
`c6e7f16`.
- **The full suite, once, on both interpreters,** with the BMD and NSLR references set, as CI has them, the
  Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, as in CI:
  - 3.11: **2664 passed, 26 skipped, 1 deselected, 1 xfailed, 53 warnings in 3484.49s (0:58:04)**;
  - 3.13: **2664 passed, 27 skipped, 1 xfailed, 53 warnings in 3552.46s (0:59:12)**.
