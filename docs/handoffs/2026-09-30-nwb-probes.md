# Probes and areas in every NWB file: the recording names the probe, wl.works says where it went

**The hardware-free slice of Phase 3's NWB export, piece 3.**
- **Branch:** `spec/nwb-probes`, forked from `main` at `da95dd9`.
- **Spec:** `docs/superpowers/specs/2026-09-30-nwb-probes-design.md` (`3079136`, amended in
  this branch's last commit).
- **Plan:** `docs/superpowers/plans/2026-09-30-nwb-probes.md`.
- **The requester's choices:** they chose this slice as next, made the four decisions in the
  spec's §0, and approved the spec ("Yes, write the plan").

Every line of the plan was proven in a scratch worktree before the plan was written.

---

## 1. What was built

**The recording names the probe** (the requester's decision 2).
- `ephys/spikeglx_probes.py` reads each run's `.ap.meta` files: the serial (`imDatPrb_sn`),
  the part number (`imDatPrb_pn`) and the sites its imroTbl recorded, numbered as
  `ProbeType.Electrode` numbers them. It reads both the generator's flat layout and SpikeGLX's
  per-probe folders. What a `.meta` cannot say becomes a `problem`, never an exception.
- **A new daemon stage, `ephys.ProbeCensus`**, runs after `core.Segment`. A master row per
  SpikeGLX segment marks its run read, and a part row per imec stream records its probe. A
  placeable probe fills `ProbeType`, `Probe` and `ElectrodeConfig`.

**wl.works says where it went** (decisions 1 and 2).
- `ProbeEntry` gains `target` (the aim: area, atlas, atlas level, all three or none) and
  `area_assignment` (the latest: area, source, `asserted_at`). Both are optional.
- `accept()` records them: `ephys.InsertionReport` per insertion (the latest request wins) and
  `ephys.AreaAssignment` per assignment (append-only).
- **Each pass links the two** (`ephys.link_insertions`): `ProbeInsertion`, `InsertionLocation`
  and `SegmentConfig`, in either arrival order. A corrected report relinks: a changed
  trajectory or aim is updated in place, and a changed serial moves the link.

**Every file names its probes** (decision 3; `nwb/probes.py`).
- A device per serial, `probe-<serial>`, with its part number as a `DeviceModel`.
- An electrode group per insertion, or per probe when none was reported, located at the area
  label: the assignment, else the aim, else `unknown`.
- One electrode row per active site, with `rel_x`/`rel_y`, `shank`, `electrode`, and both areas
  in `target_area`, `assigned_area` and `assigned_area_source`.
- **A bank change inside a montage refuses the file,** naming the segments.
- **A file waits for the census** of every SpikeGLX segment in its session.

**The description is version 2.** `probes` gains its shape, and `notes` says what the file
could not place or join. `docs/schemas/nwb_description.json` is regenerated, and
`docs/ops/lab-host-protocol.md` documents both contracts.

**The generator gains a second probe and a restart,** which may change a probe's bank. The
second run takes SpikeGLX's own `_g1_t0` names, so spikeinterface reads two segments.

**A timebase defect, found and fixed.** `timebase/fit.py` fitted a system's rate from every
segment's barcodes under one intercept. Each recording's barcodes are timed from its own first
sample, so a restarted SpikeGLX run fitted −951,278 ppm and its session fell to tier D. On the
rig, every bank change would have cost its session its timing and its NWB file.
`fit_rate_across` fits one rate with an intercept per segment, as parent spec §4.5 describes.
A single segment fits exactly as before.

## 2. What wl.works must do

A new OPEN entry in `docs/pending-wl-works-amendments.md`:
- **Send each insertion's aim and latest assignment** in every request's `metadata.probes`.
- **Two flags.** Plan 19's assignment row lists no atlas, so none is carried. A probe inserted
  twice in one session cannot be joined, because the request does not say which montage each
  insertion covers.

## 3. Still open

- **A restart across task codes costs the timing tier** (the spec's amendment 13). The NI
  record lacks the codes sent while SpikeGLX was stopped, and the 0.999 agreement threshold
  reads them as disagreement. A bank change on the rig pauses the task, so this should not
  arise. If it does, the agreement should compare only the stretches the NI recorded.
- **The rig's real SpikeGLX layout** has not been seen. Both layouts are read.
- **An existing database needs the new tables:** `ephys.probe_census`, its part,
  `ephys.insertion_report` and `ephys.area_assignment`. They are created on activation;
  nothing existing changes shape.
- **Piece 3 proper** (sorting, units, LFP, MUA) needs the compute machine. It will read
  `ProbeInsertion` and `SegmentConfig`, which this fills.

## 4. The rulings

**Made while planning,** each also a dated amendment in the spec:
1. **`SegmentProbe` is `ProbeCensus.Probe`,** with a master row that marks a run read even
   with no probe. *Cost if wrong:* a table rename.
2. **What a `.meta` cannot say is recorded once, on the part row,** not reported every pass. A
   landed file does not change. *Cost if wrong:* a corrected file needs its census row
   deleted by hand.
3. **An unknown part number gets no `ProbeType`,** keeping Phase 2a's invariant. The probe is
   listed with its part number and no electrodes.
4. **A serial registered as another type is a problem, not a second identity.**
5. **A serial reported for two insertions is not joined.** *Cost if wrong:* a moved probe's
   file says `unknown` until wl.works says which montage each insertion covers.
6. **An insertion a later request omits is left alone,** and the linker takes back only
   insertions that have a report. The full suite found the second: a synthetic insertion
   with a sort on it, and no report, was being taken back every pass, and the foreign key
   refused it.
7. **The file waits on the whole session's census,** not only its montage's. *Cost if wrong:*
   a file waits one pass longer than it needs to.
8. **The builder reads the census and the reports,** not the linked tables.
9. **pynwb fixes the electrode table's description,** so the area columns and groups say the
   areas are per insertion.
10. **`probeinterface` is declared in `wl.yaml`,** and the spec's "test-only oracle" is
    corrected: it has been a runtime dependency since Phase 2a.
11. **The timebase fix is in this plan,** as its own task, before the census. Without it the
    spec's restart fixture could not produce a session with trustworthy timing, and neither
    could a bank change on the rig.
12. **The fixtures restart mid-trial,** where the task sends no code. A restart across codes is
    recorded as open (§3), not changed.

## 5. The measured counts

Measured while proving the plan. Execution measured every one of them again and got the same
counts, and every mutation was caught again.

| Task | Before the code | After | Mutations |
|---|---|---|---|
| 1 the reader | 10 failed, 167 passed | 177 passed | T1a–T1c caught |
| 2 the generator | 11 failed, 194 passed, 1 deselected | 205 passed, 1 deselected | T2a–T2c caught |
| 3 the timebase | collection stops on the missing `fit_rate_across`; the daemon test alone: 1 failed, 19 passed (−875,200 ppm) | 103 passed, 1 skipped | T3a–T3b caught |
| 4 the census | 6 failed, 55 passed | 61 passed | T4a–T4d caught |
| 5 the contract | 8 failed, 270 passed | 278 passed | T5a–T5e caught |
| 6 linking | 4 failed, 20 passed | 24 passed | T6a–T6d caught |
| 7 the file | 13 failed, 101 passed, 1 skipped | 114 passed, 1 skipped | T7a–T7f caught |
| 8 the description | 5 failed, 125 passed, 1 skipped | 130 passed, 1 skipped | T8a–T8b caught |

Some tests pass before their task's code, by design. In Task 5, seven refusal cases are
refused as unknown keys. In Task 6, three tests expect nothing linked. A mutation proves
each of them after the code (T5a, T5e, T6a, T6d).

**Full suite, once, with every task applied** (BMD and NSLR references set,
`WLPP_OHDPI_REFERENCE` unset, as CI has it): **2029 passed, 25 skipped, 1 deselected, 1
xfailed** on 3.11 and **2028 passed, 27 skipped, 1 xfailed** on 3.13, 0 failed. `main` at
`f106bb2` gives 1956 and 1955. Task 7's last two tests, for an Intan probe and a `.meta` naming no
serial, were added after that run, when a check against spec §5 found neither path tested; both
pass on 3.11 and 3.13. **A first full run failed one test on both interpreters:** the
linker was taking back a synthetic insertion that had no report (ruling 6). It is fixed, with
a test, in Task 6.

## 6. The final review, and its fix pass

A fresh reviewer on Opus read `da95dd9..78296a5`. It found no Critical issue, one Important
and ten Minor, and its verdict was "with fixes". Two Minors were re-graded to Important by
their effect. All three were fixed in one pass, each with a test watched failing first:

- **I1: a file's probes came from its whole montage window** (`077122a`). A derivative on one
  side of a bank change inside wl.works' montage was refused, permanently, though parent spec
  §8.3 names it as the remedy. Segments are now those the file's own blocks overlap (spec
  amendment 14). `test_a_file_on_one_side_of_a_bank_change_builds`.
- **M1, re-graded: a damaged site table** (`92c28b1`). A table holding only its header raised
  `TypeError` past the reader, which would park the segment's census for good and hold its
  files on readiness. A table cut mid-entry passed as a map of fewer sites. Both are now
  problems (amendment 15). `test_a_short_imro_table_is_a_problem_not_a_partial_map`.
- **M4, re-graded: a serial holding `/` or `:`** (`df7d14e`). HDMF refuses those in a name, so
  every build of the file failed. That can reach the Intan path, where wl.works supplies the
  serial. The device and group names are made safe, and the serial is kept whole.
  `test_a_serial_an_hdf5_name_cannot_hold_is_kept_whole_under_a_safe_name`.

**Full suite after the fix pass:** **2035 passed, 25 skipped, 1 deselected, 1 xfailed** on 3.11 and **2034 passed, 27 skipped, 1 xfailed** on 3.13, 0 failed (four tests more than before the fix pass: one for I1, two for M1, one for M4).

**Deferred minors:**
- **M2.** A request that `submit()` refuses (a `409`, or a database error) has already
  replaced the reports and appended its assignments, as the subject's details already are.
  `test_a_refused_request_records_no_report`'s docstring claims more than `accept()`'s own
  checks do.
- **M3.** A relink updates `ProbeInsertion.probe_serial` and `SegmentConfig`'s configuration
  underneath an existing sort; only taking a link back refuses. **Sorting's plan must guard
  this.**
- **M5.** A probe whose `.meta` names a serial but no part number is described as named by
  wl.works' report.
- **M6.** In a session with both SpikeGLX and Intan, a mistyped SpikeGLX serial in a report is
  listed as an Intan probe.
- **M7.** `rel_x`/`rel_y` are µm, and nothing in the file says so.
- **M8.** `test_one_segment_fits_exactly_as_the_pooled_fit_does` compares `fit_rate_across`
  with `fit_rate`, which now calls it; pin a literal instead.
- **M9.** Overlapping daemon passes can report a spurious duplicate-key link error. It rolls
  back, so nothing is corrupted.
- **M10.** In `ProbeCensus.make`, a bare `Probe` is the Manual table and `self.Probe` the
  part: correct, and easy to misread.

**What the reviewer set aside,** each ruled in the ledger as it stands: an out-of-order retry
regressing a report; a serial reported twice; the refusal of a canonical across a bank
change; a restart across task codes; a corrected `.meta`; an insertion wl.works deletes;
per-insertion areas; an Intan probe listed in every montage's file (the contract carries no
montage per insertion); duplicate insertion numbers in one request (a wl.works fault); the
link stage visiting every reported session each pass; and the `test_ephys`/`test_daemon`
order dependency, which `main` has too.
