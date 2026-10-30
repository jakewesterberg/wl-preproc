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

## 6. The final review

A fresh Opus reviewer read the branch (`bbdfa0e..b0a3b06`) and found **no Critical, two Important and ten
Minor**, judging it ready to merge with fixes, and one fault outside it, in wl-sync. The re-grade by effect kept
both Important and raised one Minor; the three were fixed (`dfe50b0`, spec amendments 7-9), each by a test that
failed first and mutations it catches (four):
- **A device recording begun after a sync box restart failed the timing stage** (its I1): `TimingProvenance`
  placed every SpikeGLX run with `fit_offset`, which raised, so the session never got a tier. The plan had
  left out §8's test of that case:
  `test_a_device_recording_begun_after_the_restart_is_rejected_and_the_session_tiered`.
- **A device whose only recording began after the restart was named `unfitted_system`** (its I2), the reason
  for a mostly-dead cable: `test_a_device_whose_only_recording_began_after_the_restart_names_why`.
- **The report missed a restart whose later segment logged no barcode** (its 7, re-graded Important: a tier-D
  session is not otherwise in the report): `test_the_report_names_a_restart_whose_later_segment_logged_no_barcode`.
- **Outside the branch, wl-sync's** (its 3; amendment 10): the box unwraps code-word and edge ticks per path and
  misses a 32-bit wrap after 71.6 minutes without a record, so codes after a long pause would be timed early
  by multiples of 71.6 minutes, undetected here. Asked of wl-sync (`HANDOVER-wl-sync.md`, ask 6), to fix
  before the first real session.
- **Deferred, Minor:**
  - a barcode within 400 ms of a segment's tick 0 is dropped in the round trip through edges (an after-restart
    segment's first; the crash-within-a-second case's t = 0); building the reference from the `B` records
    fixes it and saves a read;
  - no test pins that the reader carries `clock_trusted` and `clock_reason`;
  - each sync box log is read more often than before (about twice), a cost that grows with real segments;
  - the sync box's barcode counts include a post-restart segment's;
  - one segment with an unreadable header fails every timing stage for its session;
  - stale text now that the pin carries `wl_sync.clock` (`timebase/clock.py`, `tests/nwb/test_clock.py`,
    `report.py`'s second copy of `BARCODE_EPOCH`, `_rhs_header.py`);
  - the test log's trailer is not wl-sync's format (harmless);
  - `SyncBoxRestartSpec` has no coherence check;
  - wl.works should hear that the listing's rejected-segment reasons gain `no_shared_barcode`.
- **Set aside by the reviewer, and the rulings on them** (all stand): a session whose box crashed within its
  first second is tier D; codes in a no-barcode segment before session time's are dropped; codes before the
  first barcode have negative session time; the box's own Segment row spans back to its process start; the
  `session_time` role ignores `SessionClock` until part 3b; back-fill on deploy; the trial cut by a restart;
  the `*.log` glob; records near a folder change (asked of wl-sync).

**The full suite after the fixes,** at `dfe50b0`: 3.11, **2529 passed, 26 skipped, 1 deselected, 1 xfailed**; 3.13, **2529 passed, 27 skipped, 1 xfailed**, both exiting 0.
