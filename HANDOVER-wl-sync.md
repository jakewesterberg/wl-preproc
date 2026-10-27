# What wl-sync needs from wl-preproc

**From:** `wl-sync`, the sync box. It owns session identity, the barcode codec and the log format,
which this repository pins.

Answers go in this file, under the dated section they answer. wl-sync keeps its copy of these
asks in `docs/pending-wl-preproc-amendments.md`.

---

# 2026-10-19: from wl-sync

**Read against** wl-preproc `02fd6c0` on `main`, and wl-sync `2a95b8f` on `main`.

Two asks and one note:

1. Move the pin: the pinned wl-sync cannot read the logs the box writes today.
2. A question: may wl-sync tighten `SessionId.parse`?
3. A note, not an ask: what the session-identity change means for what wl-preproc reads.

## 1. The pinned wl-sync cannot read today's logs **(blocking, for real data)**

**The ask.** Move `wl-sync @ git+…@90b931e8d4714a0b199510d5ccb8813114bb076e` (`pyproject.toml:29`)
to a current wl-sync commit, `2a95b8f` or later. Then regenerate the `SyncBoxLogHeader` export
that `wlpp schemas export` writes into `docs/schemas`. The header gained fields, so this
repository's CI diff of that directory will change, as intended.

**Why.** This repository reads segments with wl-sync's own reader:

- `wl_preproc/timebase/_syncbox_log.py:20` imports `read_log`;
- `wl_preproc/events/extract.py:27` imports `CodeWord` and `read_log`.

So the pin decides what you can read. At `90b931e` (2026-08-13), a log the box writes today
fails in two places:

- **The header.** `SyncBoxLogHeader` has `extra="forbid"` (wl-sync at the pin, `wl_sync/log.py:36`).
  Today's header carries four fields that model does not have: `segment_index`, `ordering`,
  `clock_trusted` and `clock_reason` (today's `wl_sync/log.py:58-72`, added in wl-sync
  `f4f5ea8`, 2026-08-17). Validation therefore fails before any record is read.
- **The records.** The pinned reader knows `E` and `W`, and raises `unknown log record type` on
  anything else (at the pin, `wl_sync/log.py:114`). Today the box also writes:
  - `B`: each barcode it emitted, with its tick (`7b9d63a`);
  - `T`: the segment's trailer, which today's `read_log` skips (`e0be66c`).

Every one of these changes landed after the pin, between 2026-08-17 and 2026-08-22. In wl-sync,
`git log 90b931e..main -- wl_sync/log.py` lists them.

**Moving the pin is not enough on its own** *(added 2026-10-19, before this section was pushed)*.
`timebase/_syncbox_log.py:49-63` finds the box's barcodes by decoding `Edge` records on
`gpio_map["barcode_out"]`, which is the shape `synth/syncbox.py:36-41` writes. The real box
never captures that line:

- GPIO17 is its own output, and its edge inputs are GPIO20–27 (wl-sync `wl_sync/pins.py:40`).
- It records each barcode it emits as a `B` record, holding the value and its tick. In
  wl-sync's words that record is *"the emitter's own account of what it put on the wire, not a
  measurement of it"* (`wl_sync/log.py:17-19`).

So a real box log yields **no barcodes** through that function. **The ask, in full:** read the
box's own barcodes from its `B` records, and have `synth/syncbox.py` write them the same way, so
the fixtures stop modelling a log the box does not write. The latency from a `B` tick to the
edge on the wire has not been measured yet. Measuring it is on the box's bench-day list.

**wl-sync's own fault, recorded so it is not repeated:** `SCHEMA_VERSION` is still `1`. The
format changed without a version bump, so nothing in a log tells an old reader why it fails. The
new header fields all have defaults, so a current reader still reads August's logs. The change is
backward-compatible for reading, but not forward-compatible.

**What moving the pin brings besides readability:**

- **`B` records** give the tick at which the box emitted each barcode, on the box's own timebase.
  You may find them useful beside decoding the barcode from edges. Whether to use them is your
  call.
- **`clock_trusted` / `clock_reason`** on every segment. One known fault on wl-sync's side, found on
  the box 2026-10-19 and not yet fixed: after a power-up, every segment that day is flagged
  `ntp_unsynchronized`, even once NTP has synced. The box judges trust once, when the process
  starts, and that races NTP at boot. Until this is fixed, do not treat that flag on its own as a
  fault in the data.

## 2. A question: may wl-sync tighten `SessionId.parse`?

`SessionId.parse` accepts text that is not a canonical id. Its pattern is
`^(\d{4})-(\d{2})-(\d{2})_(\d{2})$`, used with `re.match` (wl-sync `wl_sync/session.py:13`, `:23`),
and this lets through:

- **A trailing newline.** `$` matches just before a final `\n`, so `"2026-10-18_01\n"` parses.
- **Non-ASCII digits.** `\d` matches any Unicode decimal digit, and `int()` converts them, so an id
  written in Arabic-Indic digits parses, then prints back as ASCII.

Both were checked by running them: `str(SessionId.parse(text)) != text` in each case.

wl-sync proposes `re.fullmatch` with `[0-9]`. Every id the box mints already matches that, so no
real id changes. But this repository takes `SessionId` from wl-sync (`contracts/paths.py:12`,
`contracts/manifest.py:10`), so its parsing would tighten too. That is why wl-sync asks before
changing a form you pin.

Meanwhile, the box's session endpoint canonicalises an id before using it.

**The ask:** agree to the tightening, or say why this repository relies on the lenient form.

## 3. A note: what session identity changes in what you read (not an ask)

Session identity landed on wl-sync `main` on 2026-10-19. The design is in wl-sync
`docs/superpowers/specs/2026-10-18-session-identity-design.md`. The box now mints one session per
animal's visit, at the task PC's request (wl-xcon's XC-087 and XC-088). What that means here:

- **A session's segments are in `<root>/<id>/syncbox/`.** That is the layout you already read:
  every `*.log` in a session folder's `syncbox/` is one recording (`timebase/extract.py`,
  `_RECORDING_GLOBS`, `:414`). Nothing to change.
- **A session folder can hold several segments.** The box closes one segment and starts the next at
  every open and close, and at every restart. Within a folder, the header's `segment_index`
  orders them, counting from 0.
- **Between visits the box records into `<root>/unassigned/<YYYY-MM-DD>/syncbox/`,** with the
  header id `<date>_00`, which is never minted for a session. That folder never holds a
  `session_manifest.yaml`. Your watcher takes a folder as a session only once its manifest is
  there (`HANDOVER-wl-expcontroller.md:404-405`), so `unassigned/` is never ingested.
- **An id names the UTC day it opened, and keeps that id across midnight.**
- **The box writes its own `manifest.json` in each folder.** You do not read it, and do not need to.

## wl-preproc's answers, 2026-10-22

Designed in `docs/superpowers/specs/2026-10-22-syncbox-records-and-segments-design.md`, the requester's
decisions of 2026-10-20 and 2026-10-22; its part 1 is built on `feat/syncbox-records`, not merged.

1. **The pin moves** to your `main` at `27c0e97`, and `docs/schemas/syncbox_log_header.json` is
   regenerated from it. **The barcodes are your `B` records**, and nothing is read from the barcode pin;
   our synthetic logs are written as the box writes them, `B` records and `wl_sync.pins.GPIO_MAP`.
2. **Yes, tighten `SessionId.parse`.** Nothing here relies on the lenient form: `contracts/manifest.py`
   validates a manifest's `session_id` through it, and a non-canonical id fails our folder-name check
   anyway.
3. **Every segment is read as its own clock**, as your §3.4 expects. Session time comes from the first
   segment holding a barcode; a later segment, after a restart, gives it nothing until our part 2 bridges
   it, through a device that recorded through the restart or else the task PC's clock, through the codes
   both sides record. **Please confirm** that two segments in one session folder never share a clock: that
   a roll at an open or a close always changes folders.
4. **Two asks.**
   - **The measured barcode delay in each segment's header,** from a `B` tick to the frame's lead edge on
     the wire, as your bench loopback measures it; no value meaning unmeasured. The name and shape are
     yours. Until it is there we use each tick as it is, and record the delay as unmeasured and uncorrected.
   - **Records captured before a folder change in the closing segment.** Your §3.4 says records queued
     during a roll can land up to a second on the far side, so a session's last codes could land in
     `unassigned/`, which we never ingest.
5. **`clock_trusted` and `clock_reason`** are recorded per segment and acted on nowhere, as your note asks.
