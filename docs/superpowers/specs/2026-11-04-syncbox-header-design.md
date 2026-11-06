# Reading wl-sync's header change: one clock per process, the barcode delay, and lost events

**wl-sync's header change**, pushed on 2026-10-30 as its `main` at `fe1ea63`: every segment's header now
names the process that wrote it, the measured barcode delay and the clocks its ticks count; the box rolls
once in place, in the same folder, when NTP first syncs; and a new `L` record marks capture events the box
saw and could not record. The pin here, `27c0e97`, refuses all of it: its header forbids unknown fields,
and its `read_log` raises on an `L` line. wl-sync holds the box on `4f1739c` until this repository's pin
has moved: "nothing is deployed until the pin has moved, the same hold as the header fields" (its
`docs/pending-wl-preproc-amendments.md`, note 6).

**Status.**
- Chosen on 2026-10-30 (the requester's order of that evening): right after XC-398. Designed from the
  groundwork of 2026-10-30, whose decisions the requester took that evening (§0).
- Branch `spec/syncbox-header`, from `main` at `4bd10f1`, rebased onto `82380d2` (XC-398's merge).
- **Approved by the requester on 2026-11-06**, with its five rulings.
- One part. It must merge before the box runs wl-sync's change (§2). Part 2 of the 2026-10-22 design, the
  bridges, comes after it: a restart now means a new process, which is what part 2 bridges.

**Read for this spec,** each passage relied on opened at its source:
- wl-sync's `main` at `fe1ea63`: `wl_sync/log.py` (`SyncBoxLogHeader`, its `process_uuid`,
  `barcode_delay_us` and `timebase` docstrings; `Loss` and `LOSS_SHAPES`; `read_log`),
  `wl_sync/segment.py` (a run writes one segment, then one more at every roll; `next_segment_index` counts
  per folder), `wl_sync/service.py` (`_BoxClock`: one origin and one wrap count for every path, the PIO
  counter reading 0 when capture starts), and `docs/pending-wl-preproc-amendments.md` (the answers to our
  items 3 to 6, and notes 4 to 6).
- This repository: the 2026-10-22 design (`2026-10-22-syncbox-records-and-segments-design.md`) and its
  amendments, part 1's handoff (`docs/handoffs/2026-10-22-syncbox-records.md`, §6's deferred findings),
  `HANDOVER-wl-sync.md`, and the code each section cites.

---

## 0. The requester's decisions

Taken on 2026-10-30, in the question interface, from the groundwork's recommendations:
1. **The pin moves with the header change**, once, past every commit since `27c0e97` (alone, if the header
   change had slipped past mid-November).
2. **The `L` record is batched with the header change, and a lost code word inside a run is tier D.**
3. **The groundwork's five decisions as recommended:**
   1. a failed roll check, where one process's next segment does not continue it, is read as a restart,
      not bridged, and the report says why;
   2. every segment of session time's process takes the role `session_time`; no new role;
   3. the late first `B` of a process is dropped and counted nowhere but `n_barcodes`, which keeps counting
      what the box logged; no new column for it;
   4. a log without `barcode_delay_us` is read with each tick as it is, and the delay stored as null;
   5. a lost code word inside a run fails the session's timing check (tier D); a lost edge, or a word lost
      outside every run, is reported only.
4. **wl-sync was told** the same evening.
5. **This follow-up comes right after XC-398** (wl-sync holds the box's deploy until it merges).
6. **Code words on a split clock are recorded and reported, not corrected** (the groundwork's decision 6,
   option (a)). wl-sync's one-clock fix is a draft to be decided after bench day, and the box's unit runs
   `--fake` until then, so no real session carries the drift yet. If that fix slips toward January,
   placing codes by the NI's own record of them is the remedy, in a later piece.

Five points below are rulings made while writing this spec, marked **Ruling** where they appear, for the
requester's review: §3's unreadable segment, §4's gap within one process (a refinement of decision 3.1),
§5's first `B`, §6's storage of losses as written, and §6's open window.

## 1. What wl-sync changed, and what our pin does with it

- **Three header fields,** all defaulting to null, `schema_version` still 1:
  - `process_uuid`: one UUID4 per run of the box's recording process, in canonical form, in every segment
    that process writes. "Segments with the same id are on one clock: their ticks compare by number." A
    different id, or null, is a restart and a new clock. "One clock" is one origin and one wrap count, not
    one rate.
  - `barcode_delay_us`: from a `B` tick to the frame's lead edge on the wire, in µs, at least 0; "ADD IT TO
    A `B` TICK". Null is unmeasured: "use each tick as it is". **Except the first `B` of each process**,
    whose frame also waits for the output line to be requested, so its edge is later by an amount nothing
    measures.
  - `timebase`: free text naming the clocks the ticks count. `"split"` on the real backend: `W` ticks count
    RP1's crystal, joined to CLOCK_MONOTONIC once when capture starts, while `B` and `E` count
    CLOCK_MONOTONIC, so code words drift from barcodes "about 0.18 s an hour at 50 ppm", the real rate
    unmeasured. `"fake"` on the test backend. Null before the field existed. A value a reader does not know
    means "relative rates unknown".
- **A roll in place:** when NTP first syncs after a power-up, the box closes its segment and opens the next
  in the same folder, same process. Across it the `B` value steps by +1, or by +2 when the sync stepped the
  clock and the frame grid was placed again; "treat +1 or +2 across a roll in place as adjacent".
- **The id promises a shared clock, not adjacency.** Rarely, a failed read of the box's state rolls the
  recording out to `unassigned/` and back into the session's folder: two segments of one process with
  time between them, the records of that time elsewhere.
- **The `L` record**, `L,tick_us,path,gpio,count,bound,cause,before_us,after_us`, read by `read_log` as
  `Loss`. Causes, from a closed set per path: `buffer_overflow`, `coalesced` and `unaccounted` on `E`;
  `desync` and the reserved `fifo_full` on `W`. The loss lies in `[before_us, after_us]`, an empty end
  leaving that side open. A `desync` stops the word pump: its window opens at the last code word recorded
  and has no end, "every later strobe is lost too, uncounted". Counts do not add across records: one lost
  edge can be named twice. A record can sit in a later segment than its loss.
- **Records near a roll** land in the closing segment (wl-sync's `4043737`, our ask of 2026-10-22), up to
  about 65 ms of later ones aside.
- **Also brought by the pin:** `decode_edges` made linear (`dc68cad`); one box clock unwrapping every path
  (`d309bf1`, our ask 6, which closes the 2026-10-22 design's amendment 10); clock trust judged again at
  every roll, with a new `clock_reason`, `barcode_off_clock` (`2b420c0`); `SessionId.parse` taking only the
  canonical form (`5dc0f1e`, as we agreed on 2026-10-22).

**At our pin** a second segment of one process in a session folder is read as a restart:
`timebase/segments.py::session_log` takes session time from the first segment holding a barcode, alone, and
`segment_roles` calls every later one `after_restart_unbridged`.

## 2. Why this merges before the box runs the change

A session open across the box's roll in place would, at our pin, take session time from the first segment
only; reject as `no_shared_barcode` every device recording begun after the roll; and lose every code word
logged after it, so the codes would fail against the task file and the NI: tier D, quarantined. wl-sync
holds the box until our pin moves (the quotation above); this follow-up's merge releases it, and wl-sync is
told when it merges (§12).

## 3. Reading the new log

1. **The pin moves to wl-sync's `main` at `fe1ea63`** (`fe1ea633972df9590dbe1974aeb66dc3a1eb20ad`), in
   `pyproject.toml` and in `wl.yaml`'s `third_party` entry, whose `why` gains the reason: the box's logs
   since 2026-10-30 are unreadable by the old pin. Each environment reinstalls it; CI installs it from
   `pyproject.toml`. `docs/schemas/syncbox_log_header.json` is regenerated by `wlpp schemas export`.
2. **The reader** (`timebase/_syncbox_log.py::SyncBoxLog`, `read_syncbox_log`) carries `process_uuid`,
   `barcode_delay_us` and `timebase` as written (each None on an older log), the `B` records, the `W` and
   `E` paths' `L` records, and still `clock_trusted` and `clock_reason`, acted on nowhere.
3. **An unreadable segment** — **Ruling** on part 1's deferred finding: a segment whose header does not
   validate still refuses the session's timing stages, now naming the file and why. A segment whose header cannot be read could be session time's or a
   restart's, and nothing can tell which, so no stage guesses. (Part 1's deferred finding.)
4. **`SessionId`'s canonical form:** every session folder this repository makes or reads must still parse;
   the plan checks `contracts/manifest.py`, `contracts/paths.py` and `synth/session.py` against it.

## 4. A clock is a process, not a segment

- **Segments of one process are one clock.** A folder's segments are grouped by `process_uuid`; within a
  group their ticks compare by number, so their `B` records are one series and their `W` records one
  stream. A segment without the field (an older log) is a process of its own, as today.
- **Session time's process** is the process of the first segment, by `segment_index`, that holds a barcode.
  `session_log` becomes the segments of that process; `session_reference`, `syncbox_codes`, and the sync
  box's own scans read them together.
- **The check at each boundary within one process,** in `segment_index` order:
  - **adjacent:** the later segment's first `B` value is the earlier's last plus 1 or 2, and its tick is
    later. The roll in place, and nothing is missing.
  - **a gap** — **Ruling** (refining decision 3.1 with wl-sync's statement that one id need not be
    adjacent): the later segment's first value is more than 2 above the earlier's last and its tick is
    later, by about as many seconds as the values. The box recorded elsewhere in between, on the same clock.
    Session time is unbroken and every barcode stays in the reference; the code words of the gap are not in
    this folder, so the gap is treated as a lost stretch of the `W` path, from the earlier segment's last
    code word to the later's first (§6).
  - **a contradiction:** a value or a tick that does not go forward, or a tick step that disagrees with the
    value step by more than 2 s. The box's claim of one clock is contradicted, so neither reading is safe:
    the later segment and every one after it in that process are read as after a restart (decision 3.1),
    and the report says why.
- **Roles** (decision 3.2): every segment of session time's process is `session_time`, holding a barcode
  or not; another process's segment holding a barcode is `after_restart_unbridged`; one holding none is
  `no_barcode`. A segment refused by a contradiction is `after_restart_unbridged`. The stored enum is
  unchanged, and the per-segment rows still show each roll.
- **The report's sync box restarts** lists every segment outside session time's process (as part 1's
  amendment 9 lists every segment other than session time's), and each contradiction, with why.

## 5. The barcodes

- **The delay.** Every `B` tick has `barcode_delay_us` added before anything uses it, in the reader, one
  place; null leaves the tick as it is (decision 3.4). `SyncBoxSegment.barcode_delay_us` stores what was
  added, null while unmeasured.
- **The late first `B` of a process** — **Ruling** on which record that is: a folder cannot show whether a
  process began in it (`segment_index` counts per folder, and a process normally starts in `unassigned/`
  and rolls in at a session's open), so the first `B` of each process's earliest segment in the folder is
  dropped before anything uses it. Where the process began elsewhere that drops one good barcode of
  thousands, which costs nothing; where it began here it drops the late one. `n_barcodes` keeps counting
  what the box logged (decision 3.3). **Session time's t = 0 is the first barcode kept**, one second later
  than the first logged where one is dropped; codes before it have negative session time, as part 1's
  rulings already allow.
- **Every reader of the box's barcodes takes them from the `B` records themselves:** session time's
  reference, `scan_system`'s scans of the sync box (which `core.Segment` and `timebase.SystemTimebase`
  read, as they read every system's), and `syncbox_codes`. Nothing renders them into edges and decodes
  them back any longer (`timebase/extract.py::extract_syncbox`). That fixes part 1's deferred finding that
  a barcode within 400 ms of a segment's tick 0 was lost in the round trip, saves a read of each log, and
  takes the sync box off the codec's path.

## 6. Lost events

- **Stored as written** — **Ruling** on the storage: one row per `L` record, in a part table of
  `timebase.SyncBoxSegment` (`SyncBoxSegment.Loss`): its tick, path, line, count, bound, cause and window,
  as the box wrote them. Not summed per segment, since "counts do not add across records".
- **The timing check** (decision 3.5), for the `W` records of session time's process. A `W` loss, or a gap
  (§4), whose window, placed in session time as code words are, overlaps a measured run
  (`core.Run`, `run_start_time` to `run_stop_time`) fails the timing check: the session is tier D.
  **Ruling** on an open window: a `desync` has no end, every later strobe of its process is lost, and a
  run whose codes were all lost is never measured, so no overlap can show what it lost. A `desync` in
  session time's process therefore fails the timing check whenever its record is in the session's folder.
  A `fifo_full` record, reserved and not yet written, is read as its window says.
- **Reported only:** an `E` loss (no stage here reads edges), and a `W` loss whose window overlaps no run.
- **The daily report** gains a section naming every session with an `L` record or a gap: path, cause, count
  with its bound ("at least 3"), window in session time, and whether it failed the timing check.
- `TimingProvenance` stores no new column: the tier carries the failure and the report the reason.

## 7. Code words on a split clock (decision 6)

- `SyncBoxSegment.timebase` stores the header's value.
- **The daily report names every session whose code words came from a clock whose relative rates are not
  known to be one:** session time's process with `timebase` `"split"`, null, or any value this repository
  does not know; `"fake"` is not named. Each line gives the worst-case drift of its code words against
  session time at 50 ppm, wl-sync's figure, the rate unmeasured: 50 × 10⁻⁶ times the time since the
  process's capture began at the session's last code word, which is that word's tick, since the box clock
  reads 0 when capture starts (`_BoxClock`). Asked of wl-sync to confirm as a reader's contract (§12).
- Nothing is corrected. The one-clock build will write a new `timebase` value; a later piece learns its name
  and stops naming those sessions.

## 8. The record

**`timebase.SyncBoxSegment`** gains:
- `process_uuid`, null on an older log;
- `timebase`, as written, null on an older log;
- `barcode_delay_us`, now filled (the column exists);
- its part table `Loss` (§6).

A database declared before this change has no new columns, and DataJoint adds none to an existing table:
`SyncBoxSegment.populate` refuses and names the `ALTER TABLE` that adds them, as `EyeValidity` and
`EyeDetection` do for theirs. Nothing real is stored in it yet (the box runs `--fake`).

## 9. Part 1's deferred findings, fixed here

Part 1's final review deferred nine Minor findings (its handoff §6). Eight are in code this follow-up
rewrites, and each is fixed here, test first:
1. a barcode within 400 ms of tick 0 lost in the round trip (§5);
2. no test pins that the reader carries `clock_trusted` and `clock_reason` (§3.2);
3. each log read about twice per stage: read once;
4. the sync box's barcode counts include a post-restart segment's: they count session time's process;
5. one unreadable segment fails every timing stage: it still does, now naming the file (§3.3);
6. stale text now that the pin carries `wl_sync.clock` (`timebase/clock.py`, `tests/nwb/test_clock.py`,
   `cli/report.py`'s second copy of `BARCODE_EPOCH`, `_rhs_header.py`);
7. the synthetic log's trailer is not wl-sync's: the synthesizer writes through wl-sync's own writer (§10);
8. `SyncBoxRestartSpec` has no coherence check: it gains one, and so does its new sibling (§10).
The ninth, telling wl.works of `no_shared_barcode`, was done on 2026-10-30.

## 10. The synthesizer

The requester's standing rule: the fixture is corrected, not the design.
- Every synthetic segment's header carries a `process_uuid`, one per process, and a `timebase`;
  `barcode_delay_us` on request.
- Each process's first `B` is written late by a chosen amount, so a test can see it dropped.
- **A roll in place:** a new fixture writes a second segment in the same folder, same process, the ticks
  running on, the `B` value stepping +2 once.
- **A gap:** the same, with time between, its code words absent.
- **The restart fixture** gives its second process a new `process_uuid`.
- `L` records on request, each built through wl-sync's `Loss`, so the fixture cannot write one the format
  does not define.

## 11. Testing

- **A log written as the box writes it today,** by wl-sync's writer, with all three header fields, `B`,
  `E`, `W` and `L` records: every field carried; the delay added; the first `B` dropped.
- **A roll in place** in a synthetic session: one clock, session time from both segments, no device
  rejected, every code placed, both segments `session_time`, the tier as without the roll.
- **A gap:** the same, the gap reported, and tier D only when the gap lies in a run.
- **A contradiction:** the later segment read as after a restart, the report saying why.
- **A restart** (a new process): as part 1's tests, now told apart by `process_uuid`.
- **Losses:** a `W` loss in a run, tier D; outside every run, reported only; a `desync`, tier D; an `E`
  loss, reported only.
- **A split clock:** the report names the session with its worst-case drift; `"fake"` is not named.
- **An older log**, with none of the new fields: read as part 1 reads it.
- **A database declared before:** the stage refuses and names its statement, which restores the columns
  as DataJoint declares them.
- The exported header schema matches wl-sync's; mutation checks; the full suite on both interpreters once,
  at the merge.

## 12. Told to and asked of wl-sync

In `HANDOVER-wl-sync.md`, under wl-preproc's answers, and by message when this merges:
1. **The pin has moved** to `fe1ea63`: the box may run its change.
2. **How the new fields are read** (§§4-7), so wl-sync can see its contract read as written.
3. **One confirmation asked:** that a reader may take a `W` tick, in seconds, as the time since its
   process's capture began (the box clock reading 0 when capture starts), which §7's worst-case drift
   relies on. `_BoxClock` says so; it is not yet in a field's docstring.

## 13. Records

This spec; `wl.yaml`'s pin, its `why` and `status`; `docs/CHECKPOINT.md`; a handoff for the piece; the
answers in `HANDOVER-wl-sync.md`.
