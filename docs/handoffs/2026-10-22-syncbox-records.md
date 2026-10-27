# Reading the sync box's logs as it writes them today (part 1)

**Part 1** of `specs/2026-10-22-syncbox-records-and-segments-design.md`: wl-sync's asks of 2026-10-19
(`HANDOVER-wl-sync.md`). The wl-sync this repository pinned could not read the logs the box writes today,
and a real log's barcodes are its `B` records, which nothing here read: no real session could get a session
time. Part 2, the bridges across a sync box restart, follows.
- **Branch:** `feat/syncbox-records`, from `plan/syncbox-records`.
- **Spec:** the design and its amendments 1-6, made while proving the plan.
- **Plan:** `docs/superpowers/plans/2026-10-22-syncbox-records.md`.

Every line of the plan was proven in a scratch branch before the plan was written.

---

## 1. What was built

- **The pin** moves to wl-sync's `main` at `27c0e97`; the exported header schema is regenerated.
- **The barcodes are the box's `B` records** (`timebase/_syncbox_log.py`), and synthetic logs are written as
  the box writes them: `B` records, the board's pin map, `seg-000.log`.
- **Each segment is its own clock.** Session time comes from the first segment holding a barcode
  (`timebase/segments.py::session_log`), and the events stage reads that segment alone.
- **A recording sharing no barcode with session time is rejected as `no_shared_barcode`**, where the stage
  used to fail: the box's segment after a restart, and any device recording begun after it.
- **`timebase.SyncBoxSegment`** records each segment: its header's fields, its barcodes, its role, and the
  barcode delay applied (null: unmeasured, uncorrected). The daily report names every session whose box
  restarted, which is tier D until part 2 bridges it.

## 2. What the other repositories must do

- **wl-sync:** read `HANDOVER-wl-sync.md`'s answers of 2026-10-22 once this merges: the pin moved; yes to
  tightening `SessionId.parse`; confirm that two segments in one session folder never share a clock; write
  the measured barcode delay into each segment's header; put records captured before a folder change into the
  closing segment.
- **wl.works:** nothing; no published shape changes.

## 3. Still open

- **Part 2,** the bridges: a device that recorded through a restart, else the task PC's clock through the
  codes both sides record; the task PC's is complete once wl-xcon's timed event log (its build C7) exists.
- **The barcode delay's correction,** once wl-sync writes it into the header and the pin moves again.

## 4. The rulings

The spec's §0 and its amendments 1-6.

## 5. What execution measured

Executed on 2026-10-27: Task 1 `e8f1350`, Task 2 `7d12aa0`, Task 3 `4c63477`, Task 4 `4aa2653`, Task 5
`d0f0811`.
- **The full suite, once, on both interpreters,** with the BMD and NSLR references set, as CI has them, the
  Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, as in CI:
  - 3.11: **2526 passed, 26 skipped, 1 deselected, 1 xfailed**;
  - 3.13: **2526 passed, 27 skipped, 1 xfailed**.
