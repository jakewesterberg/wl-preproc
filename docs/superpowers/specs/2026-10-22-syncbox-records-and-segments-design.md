# Reading the sync box's logs as it writes them today

**wl-sync's asks of 2026-10-19** (`HANDOVER-wl-sync.md`): the wl-sync this repository pins cannot
read the logs the box writes today, and a real log carries the box's barcodes as `B` records, which
nothing here reads. Its own words: "a real box log yields **no barcodes** through that function."
Without this, no real session gets a session time. wl-sync also asks whether it may tighten
`SessionId.parse`, and notes what its session identity changes here.

**Status.**
- Chosen on 2026-10-19 as next after the saccade fixtures (the requester's order that day);
  designed in conversation on 2026-10-20 and 2026-10-22, section by section. The requester's
  decisions are in §0.
- Branch `spec/syncbox-records`, from `main` at `bbdfa0e`.
- **Built in two parts** (§2). Part 1 can be built now. Part 2 is designed to the depth its inputs
  allow: its task-PC bridge is complete only once wl-xcon's timed event log (its build C7) exists.

**Read for this spec,** each passage relied on opened at its source:
- wl-sync's `main` at `27c0e97`: `wl_sync/log.py` (the record types, the header's fields, `read_log`),
  `wl_sync/segment.py` (a segment is one process run), `wl_sync/clock.py` (`BARCODE_EPOCH`,
  `value_from_clock`, `next_value`), `wl_sync/pins.py` (`GPIO_MAP`, `EDGE_PINS`),
  `docs/superpowers/specs/2026-10-18-session-identity-design.md` (§3.1, §3.2, §3.4),
  `docs/next-session.md` (Step B, the bench loopback) and `docs/pending-wl-preproc-amendments.md`.
- This repository: `HANDOVER-wl-sync.md`, the 2026-08-12 design spec (§4.5, session time; §4.7, the
  tiers), and the code each section cites.

---

## 0. The requester's decisions

1. **The barcode delay is corrected once measured** (2026-10-20; offered against ignoring it and
   against a hardware loopback). The box reads its tick before it sends a barcode, and the delay to
   the edge on the wire is not yet measured. Read the box's barcode records now; ask wl-sync to write
   the measured delay into each log; subtract it then. Until then each session records the delay as
   unmeasured and uncorrected (§5).
2. **A sync box restart mid-session is bridged by a recording device first, then by the task PC's
   clock** (2026-10-20; offered against the task PC always, and a device only). The requester asked
   whether the box could recover using the clock of the xcon machine: it can, through the codes both
   sides record (§7). With neither bridge, the session is timed up to the restart, and its record says
   why.
3. **Two parts** (2026-10-20; offered against one piece, and part 1 only): part 1 reads today's logs
   and keeps a restart from mixing two clocks; part 2 builds the bridges.
4. **Each section of part 1, as written here, approved** on 2026-10-22.

## 1. What is wrong today

- **The pin.** `pyproject.toml` pins wl-sync at `90b931e` (2026-08-13). Its `SyncBoxLogHeader` has
  `extra="forbid"`, and today's header carries four fields it does not know (`segment_index`,
  `ordering`, `clock_trusted`, `clock_reason`), so validation fails before a record is read. Its
  `read_log` also raises on `B` and `T` records.
- **The barcodes.** `timebase/_syncbox_log.py::read_barcode_entries` decodes edges on the line the
  header's `gpio_map` names `barcode_out`. The real box never captures that line: GPIO17 is its own
  output, and its edge inputs are GPIO20 to 27 (`wl_sync/pins.py`, `EDGE_PINS`). It logs each barcode
  it emits as `B,tick_us,value` instead: "the emitter's own account of what it put on the wire, not a
  measurement of it" (`wl_sync/log.py`), with the tick "sampled BEFORE the frame is emitted"
  (`BarcodeEmitted`). Every edge input is allocated (photodiodes, reward, stimulus and accessory
  triggers, camera frame times), so a permanent loopback of the barcode line would need a board change.
- **Segments.** A segment is "one process run's worth of records" (`wl_sync/segment.py`): "ticks after
  a restart are not comparable BY NUMBER to ticks before it." A restart "resumes into whatever session
  was open" (session identity spec §3.1), so a session folder can hold `seg-000.log` and `seg-001.log`
  on unrelated clocks. `timebase/segments.py::session_reference` decodes every log in `syncbox/` and
  rebases them all to the earliest tick across them, and `timebase/clock.py::syncbox_codes` converts
  every log's code words: a restart would mix two clocks into session time, silently.
- **Barcode values** are whole seconds since 2020-01-01 UTC (`wl_sync/clock.py`, `BARCODE_EPOCH`), and
  never repeat, across restarts too (`next_value`'s clamp).
- **Synthetic logs** draw the barcode as edges on GPIO17 and name three pins of their own in
  `gpio_map` (`synth/syncbox.py`): a log the box does not write.

## 2. The parts

- **Part 1:** read today's logs (§3), keep each segment on its own clock and a restart from mixing
  them (§4), record the barcode delay as uncorrected (§5), and record each segment (§6).
- **Part 2:** the bridges (§7). Its device bridge can be built after part 1; its task-PC bridge rests
  on the task PC's instants for each code, which today exist only for parameter changes and controls
  (part 1 of the 2026-10-18 design, `core.RigClock`), and for every code once wl-xcon's timed event
  log (its XC-391, build C7) is written.

## 3. Part 1: reading today's logs

1. **The pin moves** to wl-sync's `main` at the commit current when planned (`27c0e97` or later), in
   `pyproject.toml` and in `wl.yaml`'s `third_party` entry, whose `why` gains the reason: the box's
   logs since 2026-08-17 are unreadable by the old pin. `docs/schemas/syncbox_log_header.json` is
   regenerated by `wlpp schemas export`; the CI diff of that directory changes, as intended.
2. **Barcodes come from `B` records.** `read_barcode_entries` returns each `BarcodeEmitted` record's
   `(value, tick_us)`, in the order the log holds them, and reads no edges and no `gpio_map`.
   `timebase/extract.py::extract_syncbox` already renders that list into the edge form every device
   produces, so nothing downstream changes. A log with no `B` record yields no barcodes, and a session
   whose box logs none has no session time, as today.
3. **The other header fields.** `segment_index` orders a folder's segments (§4). `clock_trusted` and
   `clock_reason` are recorded (§6) and acted on nowhere: wl-sync reports that after a power-up the
   box flags every segment of the day `ntp_unsynchronized` even once NTP has synced, a fault on its
   side not yet fixed. `ordering: "per-path"` (edges, code words and barcodes each in order, not
   across) needs nothing, since each reader here filters one record type. `T` trailers are skipped by
   wl-sync's `read_log`, which also drops a torn last line.
4. **Synthetic logs are written as the box writes them** (the requester's standing rule: the fixture
   is corrected, not the design). `synth/syncbox.py` writes a `B` record per barcode, at the tick of
   its lead edge on the box's own clock, and no barcode edges; its header's `gpio_map` is
   `wl_sync.pins.GPIO_MAP`, the board's. Every test built on a synthetic session then reads barcodes as
   production does.
5. **`SessionId.parse` may be tightened** to `re.fullmatch` with `[0-9]`. Nothing here relies on the
   lenient form: `contracts/manifest.py` validates a manifest's `session_id` through it, so a
   non-canonical id would be refused one step earlier, and the folder-name check refuses it anyway.

## 4. Part 1: segments and restarts

- **Each segment is its own clock.** In a session folder, a second segment comes only from a restart
  (session identity spec §3.1 and §3.4: a roll at an open or a close changes folders), and wl-sync's
  own reading is that "wl-preproc fits each segment separately anyway." No tick is compared across
  segments. A folder's segments are ordered by the header's `segment_index`.
- **Session time comes from the first segment that holds a barcode**: t = 0 at its first barcode
  (2026-08-12 spec §4.5), the reference built from that segment alone. `session_reference` and
  `syncbox_codes` change accordingly.
- **After a restart, part 1 mixes nothing:**
  - the box's later segments give session time no barcode and the events stage no code;
  - every other device is still timed by its own fit, from the barcodes it shares with the reference:
    its clock ran through the restart;
  - **a recording whose barcodes are all outside the reference**, the box's own later segment or a
    device recording begun after the restart, is rejected with a new reason, `no_shared_barcode`, and
    waits for a bridge. Today `timebase/fit.py::fit_offset` raises on it ("no barcodes in this segment
    matched the reference") and `schema/core.py::Segment.make` does not catch it, so the whole stage
    would fail instead of recording why; `classify_segment` counts the barcodes a file decoded, not the
    ones it shares with the reference, so it never sends such a file to `RejectedSegment` itself;
  - the codes after the restart are missing against the task file and against the NI, so the session
    fails a check and is tier D (`events/agreement.py::resolve_tier`): quarantined, not published
    automatically, and its scratch copy kept;
  - its record (§6) and the daily report say that the sync box restarted, when, and that its later
    records wait for a bridge.
- **Records near a folder change.** wl-sync's §3.4: records queued during a roll "go to whichever
  segment is open when they drain", up to a second on the far side. Codes strobed in a session's last
  second could land in the between-sessions folder, which is never ingested. Asked of wl-sync (§9),
  not handled here.

## 5. Part 1: the barcode delay

- A `B` tick is read before the frame is sent; the delay to its lead edge on the wire is the software
  latency wl-sync's bench loopback measures (`docs/next-session.md`, Step B), not yet done.
- **Part 1 reads each tick as it is**, and records the delay as unmeasured and uncorrected (§6).
- **Asked of wl-sync (§9):** write the measured delay into each segment's header, no value meaning
  unmeasured; the name and shape are wl-sync's. A header field rather than a lab constant, so each box
  carries its own measurement.
- **The correction** adds that delay to every `B` tick before anything uses it. It is built once
  wl-sync has added the field and the pin moves again, since its header refuses a field it does not
  know: a small follow-on, not part 1.

## 6. Part 1: the record

**`timebase.SyncBoxSegment`**, one row per sync box segment of a session, computed by the daemon like
the other timing stages, keyed by the session and the segment's index:
- the segment's file name and the box's `boot_id`;
- its number of `B` records, and its first and last barcode values;
- `clock_trusted` and `clock_reason`, as written;
- its role: `session time`, or `after a restart, not bridged` (part 2 adds its bridged roles);
- the barcode delay applied, null while unmeasured.

A table rather than columns on `TimingProvenance`, which is unchanged: a session can hold several
segments, and part 2 records a bridge per segment. **The daily report** gains a line for every session
whose box restarted.

## 7. Part 2: the bridges

For each segment after the first, in the requester's order:
1. **A device that recorded through the restart** (the NI, Intan, a camera): its own fit places the
   later segment's barcodes, and through them that segment's ticks, on session time, to that device's
   precision (well under a millisecond).
2. **The task PC's clock, through the codes both sides record.** The box logs each code word on its
   clock; the task PC logs, on its own, the instant it sent the code. Codes before the restart tie the
   task PC's clock to session time, codes after tie it to the later segment, the matching part 1 of the
   2026-10-18 design does to fit the rig's clock (`core.RigClock`). Its precision is that of the task
   PC's software timestamps: within about 7 ms on synthetic data with 5 ms of jitter, until measured.
3. **Neither:** the session is timed up to the restart, as in part 1.

The segment's row records which bridge placed it and the bridge's precision. A session whose every
segment is placed is re-timed: its events stage, timing provenance and tier are made again.

## 8. Testing (part 1)

- **A real-shaped log**, written by wl-sync's `write_log` with `B`, `E` and `W` records, a `T` trailer
  and the board's `gpio_map`: barcodes come from the `B` records and from nothing on GPIO17, and the
  header's fields are carried.
- **Every synthetic session** writes `B` records (§3.4), so the whole suite (alignment, events, eye,
  NWB) runs on barcodes read as production reads them.
- **A restart:** a synthetic session whose box writes a second segment on a new clock partway through.
  Session time comes from the first segment alone; the later segment's codes are left out, never mixed
  in; the other devices are timed; the later segment, and a device recording begun after the restart,
  are rejected as `no_shared_barcode` and the stage completes; one `SyncBoxSegment` row per segment,
  with its role; tier D; the report's line.
- The exported header schema matches wl-sync's; mutation checks; the full suite on both interpreters
  once, at the merge.

## 9. Asked of wl-sync

Answered in `HANDOVER-wl-sync.md`, under its section of 2026-10-19:
1. The pin is moved, and barcodes come from `B` records.
2. Yes to tightening `SessionId.parse`.
3. Write the measured barcode delay into each segment's header; no value means unmeasured. Shape
   wl-sync's.
4. Put records captured before a folder change into the closing segment, so a session's last codes do
   not land in the between-sessions folder.
5. For confirmation: every segment is read as its own clock.

## 10. Records

This spec; `wl.yaml`'s pin and its `why`; `docs/CHECKPOINT.md`; a handoff for part 1; the answers in
`HANDOVER-wl-sync.md`.

## Amendments, 2026-10-22, made while proving part 1's plan

1. **A segment's roles, as stored** on `timebase.SyncBoxSegment`: `session_time`, `no_barcode` and
   `after_restart_unbridged` (`timebase/segments.py::SEGMENT_ROLES`). The second is new to §6: a
   segment holding no barcode, before session time's segment (a box that crashed within its first
   second) or after it, places nothing.
2. **The synthetic log is named as the box names a segment**, `seg-000.log` (wl-sync's
   `segment.segment_name`). The restart fixture (`synth/recipe.py::SyncBoxRestartSpec`, its `at_s` and
   `gap_s`) writes `seg-001.log` on a tick origin that starts below the first segment's, so a reader that
   compared ticks by number would mix the two clocks, and drops every barcode emitted in the gap from every
   device, since the box emits none while down.
3. **The events stage failed on a restart too.** `timebase/clock.py::syncbox_codes` ran `fit_offset` on
   every sync box log, so a restart would have failed the events stage as it failed `Segment.make` (§4); it
   reads session time's segment alone.
4. **The pin is wl-sync's `main` at `27c0e97`**, the commit current when planned. Each environment
   installs it (`uv pip install --python <its python> "wl-sync @ git+…@27c0e97…"`); CI installs it from
   `pyproject.toml`.
5. **The report's restart section** (§6) names each later segment's first barcode and that barcode's time
   by the box's clock (a barcode is seconds since 2020-01-01 UTC, `wl_sync.clock.BARCODE_EPOCH`), and is
   live, as the reclamation list is: a segment part 2 bridges leaves it.
6. **One test's fixture is corrected, not its meaning:**
   `tests/timebase/test_clock.py::test_a_sync_box_log_with_no_barcode_is_the_clock_and_cannot_time_the_session`
   made its log barcode-less by removing `E` records; it removes `B` records.

## Amendments, 2026-10-27, made in part 1's final review

7. **`TimingProvenance` places only the SpikeGLX runs that share a barcode with session time** (the review's
   I1). It placed every run with `fit_offset`, which raised on a run begun after a sync box restart, so such a
   session never got a tier; that run is rejected as `no_shared_barcode` by `Segment.make`, and its codes wait
   for a bridge. §8's test of a device recording begun after the restart, which the plan had left out, is
   added.
8. **The shared-barcode check comes before the unfitted system's** (its I2): a device whose every recording
   began after the restart has no fit for that reason, and is rejected as `no_shared_barcode`, not as
   `unfitted_system`, which names a mostly-dead cable.
9. **The report's "Sync box restarts" lists every segment other than session time's** (its 7, re-graded
   Important: a tier-D session is not otherwise in the report), so a box that crash-looped for the rest of a
   session, its later segments holding no barcode, is named too.
10. **Code words and edges can miss a counter wrap on the box** (its 3; wl-sync's, not this repository's).
    wl-sync unwraps each kind of record on its own (`wl_sync/service.py::_TickUnwrapper`, one per path, at
    `27c0e97`), noticing a wrap only when a record's raw tick is below the one before. Barcodes, at 1 Hz, see
    every wrap; a path with no record for 71.6 minutes or more (the first code of a day hours after the box
    started, or a long pause between codes) misses one, and every later record on it is 4294.97 s early for
    each wrap missed. Session time would then place those codes that much early, and no check here catches it.
    Asked of wl-sync (`HANDOVER-wl-sync.md`, its ask 6); not handled in part 1.
