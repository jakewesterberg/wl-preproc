# The rig's training records, and a clock for a session without the sync box

**wl-xcon's ask 1 of 2026-10-11** (`HANDOVER-wl-expcontroller.md`, item 1; its backlog XC-321): read
the training records its XC-288 spec designs (the change log, reasons, the timed event log and
`controls.jsonl`) into the published dataset, and accept the agent's SQLite file unread. Asked while
designing it whether the sync box runs in every training session, the requester answered that it
should, "but there should be a backup protocol so that information in a session without a sync box is
okay. redundant systems is what we strive for." Today such a session never gets in (§1), so this
spec designs both: the records, and a clock for a session the sync box did not cover.

**Status.**
- Chosen on 2026-10-18, the hardware unchanged that day; designed in conversation the same day,
  section by section. The requester's decisions are in §0.
- Branch `spec/training-records-and-backup-clock`, from `main` at `9f78a3e`.
- **Built in five parts** (§2), each planned and merged on its own. Parts 1 to 3 can be built now;
  parts 4 and 5 wait on work in other repositories, and this spec designs them to the depth those
  inputs allow.

**Read for this spec,** each passage relied on opened at its source:
- wl-xcon's `main` at `a607414`: its XC-288 spec
  (`docs/superpowers/specs/2026-10-10-xc288-training-records-design.md`, §3, §6, §7, §10),
  `wl_xcon/record.py` (the rows written today), `wl_xcon/service.py` (`FRAMEWORK_CODES`),
  `docs/pending-wl-preproc-amendments.md` (its XC-288 section) and `docs/CHECKPOINT.md` (build C split
  into C1 to C9; C1 merged; C2a next).
- wl-works' `main` at `9e33bdf0`: its lab time-service design
  (`docs/superpowers/specs/2026-09-20-row-60-lab-time-service-design.md`, §0, §3.5, §4, §4.1).
- This repository: the 2026-08-12 design spec (§1.1, §2, §4.1, §4.2, the tiers), the NWB builder spec
  (`2026-09-28-nwb-builder-design.md` §4), and the code each section cites.

---

## 0. The requester's decisions, 2026-10-18

1. **One design for the training records and the backup**, built in parts (offered against doing
   the records first and the backup after, or the backup first).
2. **The sync box should run at every session; a session without it must still be taken in,** in
   the requester's words above.
3. **Without the sync box, an NWB file is still built, with the devices aligned by their codes**
   (offered against an NWB file holding the rig's records alone, and against no NWB file).
4. **The eye tracker and the behavior cameras need a fallback**: they are triggered by the sync box
   and record no codes, so without it they record nothing. The requester chose to ask wl-sync and
   the lab for a free-running fallback rather than leave their data out (§9).
5. **The clock without the sync box: the NI's in a recording session, lab time where there is no
   NI.** Asked which clock, the requester asked "We are going to set up a ptp/ntp clock that will be
   injected in all systems (wl-time), could that be usesd?", and, shown how, chose the NI first and
   lab time after it (offered against lab time for every session).
6. **Who acted is not in the NWB file,** as for ask 5's warnings: the database keeps the actor as
   the rig wrote it, and the file gives only the kind of actor.
7. **The listing carries counts of the training records,** with any problem reading them and which
   clock kept time; the rows themselves are in the database and the NWB file.
8. **One clock per session.** A sync box that failed partway hands the whole session to the
   backup; its own part becomes a cross-check (offered against the sync box for its part and the
   backup for the rest).
9. **The clock is a recorded choice** (§4; offered against a stand-in sync box log, and against a
   separate path for backup sessions).
10. **A sync box listed in the manifest whose transfer never finishes waits for a person,** who
    lands the session without it by one command (§5; offered against landing on its own after a
    wait).
11. **Files timed by the NI (tier N) or lab time (tier L) publish as tiers A to C do,** labelled with
    their clock; tier D stays held (§7).

---

## 1. What happens today

**The training records.** Of the four files ask 1 names, two exist, in the shape wl-xcon writes them
before its build C (`wl_xcon/record.py`):
- `parameter_changes.jsonl`, one row per applied live change: `sequence`, `name`, `was`, `now`,
  `by` (an actor map) and `run`. No instant and no trial.
- `controls.jsonl`, one row per console control (a stop, pause, resume, mark, schedule,
  cancellation, scheduled stop firing, or manual reward while paused): `kind`, `by`, `at` (the
  session's anchored wall clock), `at_local`, `trial_index`, and the control's own fields.

The grown change log (one row per setting moved, a `change` id, its instant, `trial_number`, meaning,
direction), `reasons.jsonl` and the timed event log are designed in XC-288 §3.2 to §3.5 and come
with later parts of wl-xcon's build C (C3, C4 and C7 by its checkpoint's split). Nothing here reads
any of them.

**A session without the sync box never gets in.** Every path runs through the sync box:
- **Ingest:** a manifest without `syncbox` is refused, "syncbox must be present at every session"
  (`contracts/manifest.py:63-64`), and quarantined as `manifest_invalid`; a manifest that lists it
  waits for its `DONE` marker, `INCOMPLETE` and then `STALLED` after two hours
  (`ingest/sentinel.py`, `ingest/watcher.py:444-449`), and never lands.
- **Session time** is "t=0 at the sync box's FIRST barcode, on every session type, one code path"
  (`timebase/segments.py:113`), and every other system is fitted to it by barcodes
  (`timebase/fit.py`).
- **Events** come from "the sync box (the Pi) alone" (`schema/events.py:15-16`); the NI's codes are
  decoded only to check agreement and are not stored.
- **Tiers** are A to D (`schema/timebase.py:53`); tier D refuses the NWB file
  (`nwb/gather.py:700-701`).
- **The NWB file** takes t = 0 and its wall-clock time from the first barcode
  (`nwb/gather.py:191-212`), and its `processing/timebase` says "t = 0 is the sync box's first
  barcode" (`nwb/timebase.py:30-31`).

The 2026-08-12 design spec assumed the sync box at every session, "present at every session" (§2)
and "the sole recorder on training days" (§4.2). wl-xcon's XC-288 §3.2 expects a training session
with "no sync box running", where "this log is the only timing there is".

**SpikeGLX's probes do not depend on the sync box:** "SpikeGLX handles imec-NI sync internally, and
the barcode aligns SpikeGLX-as-a-whole through one NI digital line" (`timebase/extract.py:228-231`).

---

## 2. The five parts

| Part | What | Can be built |
|---|---|---|
| **1. The training records** | Today's `parameter_changes.jsonl` and `controls.jsonl` into the database, the NWB file and the listing, timed by their codes and the rig's clock fit (§3), on sessions with the sync box | now |
| **2. The clock as a recorded choice** | The table and the one interface (§4), every caller moved behind it; the sync box the only clock yet, and every session's results unchanged | now |
| **3. The NI as the clock** | A recording session the sync box did not cover: taken in (§5), its events from the NI, each device on the NI's clock (§6), tier N (§7) | now, on synthetic data |
| **4. Lab time as the clock** | A session with neither: the rig's records on lab time, Intan by its strobes against the rig's code instants, the cameras by host stamps (§6.3) | once wl-time runs, wl-xcon writes trial and code instants (its build C), and the camera fallback exists |
| **5. The newer training records** | The grown change log, `reasons.jsonl` and the timed event log (§3.6) | as wl-xcon writes them, each once its row shape is sent |

Parts 1 and 2 share nothing and may be built in either order; part 3 needs part 2, and part 1's
joins (§3.3) read the session's codes through part 2's interface once it exists. The order chosen:
1, then 2 and 3, with 4 and 5 following wl-xcon and wl-time.

*Revised 2026-10-19 (amendment 20): part 4, lab time, is designed in full first and its asks sent,
since it is the one backup every rig has; part 3 is built while its inputs arrive.*

---

## 3. The training records

### 3.1 The database

Two tables, written by the events stage (`schema/events.py::populate_session`) in the transaction
that writes ask 5's rig tables, and only when the rig's `config.json` is this session's
(`events/rigsession.py`'s `of_this_session`), as ask 5's are.

**`core.ParameterChange`**, one row per line of `parameter_changes.jsonl`:
- key: the session, and `change_line`, the line's number in the file (from 1);
- `sequence` (null when the row has none: from wl-xcon's build C3, only rows that strobe
  `PARAM_CHANGED` carry one, XC-288 §3.4);
- `name`, the setting; `was` and `now`, the values as JSON, since a value may be a number, text or
  a list;
- `run_index` as written (from 0) and `run_number`, through `events/rigruns.py::RigRuns.run_numbers`
  as ask 5's warnings are joined (a run without its start row joins none);
- `by_kind`, `by_name`, `by_account`, `by_issuer`, `by_token_id`, as `core.AcceptedWarning` holds
  them;
- `session_time` (double, null when untimed, §3.3);
- `row`, the whole line as written, so a field wl-xcon adds (the `change` id, the instant, the trial,
  the meaning and direction of XC-288 §3.4) is kept before any reader here knows it.

**`core.RigControl`**, one row per line of `controls.jsonl`:
- key: the session, and `control_line` (from 1);
- `kind` as written; `by_*` as above; `at` (double, the rig's instant) and `at_local`;
  `trial_index`;
- `detail`, the row's own fields as JSON (a mark's frame and instants, a schedule's target, a
  manual reward's mL);
- `session_time` (double, null when untimed) and `time_source`: `code` (its own code), `rig_clock`
  (through the rig's clock fit, §6.2), or null;
- `row`, the whole line as written.

**Problems** are worded as ask 5's are, by file and line ("parameter_changes.jsonl line 7: not
JSON", "controls.jsonl line 3: not a control naming its kind"), and go into the problem rows the
listing's `rig_record_problem` flag already reads.

### 3.2 The NWB file

Ask 5's `rig_record` processing module gains two tables, written when the session has any rows:
- **`parameter_changes`:** session time (blank when untimed), the rig's run number, the setting, its
  old and new values as JSON text, the sequence, and the kind of actor (`box`, `member`, or none).
- **`controls`:** session time and where it came from, the rig's instant, the kind, the trial, the
  detail as JSON text, and the kind of actor.

**No name, account, issuer or token id reaches the file** (decision 6). The NWB description gains
their counts.

### 3.3 Joining the records to the session's clock

Never guessed:
- **Changes:** the n-th row that carries a `sequence` takes the session time of the n-th
  `PARAM_CHANGED` code in the session's codes (XC-288 §3.4: "the n-th `sequence` row to the n-th
  code, until XC-008 puts the number on the wire"). Once wl-xcon strobes the `PARAM_CHANGE` escape
  with the number (`contracts/events.py:156`), the join is by number instead.
- **Controls:** the n-th row of each kind that strobes a code takes the time of the n-th code of
  that kind; a kind that strobes nothing (a schedule, a cancellation) is timed through the rig's
  clock fit, and is otherwise untimed.
- **The rig's clock fit,** made on every session whose codes and rig record allow it: the run rows'
  instants (`runs.jsonl`'s `at`) against their `RUN_START` and `RUN_END` codes, and each timed
  control's `at` against its code, fitted as a rate and an offset with residuals kept, and recorded
  with the session's other device maps (§6). Fewer than two matches, or residuals past a limit the
  plan measures, and there is no fit.
- **When a count disagrees,** that file's rows (or that kind's) stay untimed, and one problem names
  both counts: "parameter_changes.jsonl: 12 rows carry a sequence and the recording holds 11
  PARAM_CHANGED codes, so no change is timed".

The codes are read through part 2's interface (§4.3): the sync box's today, the NI's in part 3.

### 3.4 The listing

`GET /sessions` gains, per session, the number of parameter changes, pauses and manual rewards, and
the clock that kept time (§7). Problems reading the files reach its existing flag. The rows are not
listed.

### 3.5 The agent's file

Accepted unread, as confirmed on 2026-10-11 (the handover's answer to item 1): ingest never reads
`xcon/`, and the eye stage's calibration-map search takes only `*.bhv2` and `*.yaml`. Part 1 adds a
test that a `.sqlite` file in `xcon/` changes nothing in either.

### 3.6 The newer records (part 5)

The grown change log arrives in the same file and is read by the same table: its new fields are in
`row` from the first day, and a later amendment gives the ones a reader needs their own columns.
`reasons.jsonl` (one row per reason, pointing at a `change` id or a pause's `id`) and the timed event
log (every state entered and left, every code strobed, with instants) get tables of their own once
wl-xcon sends their row shapes and file names (§9). The event log is also what part 4 times a
training session by.

---

## 4. The session's clock

### 4.1 The choice, recorded

**`timebase.SessionClock`**, one row per session:
- `source`: `syncbox`, `nidq` or `lab`;
- `reason`, one sentence: "the sync box's codes hold every run the rig recorded", "no sync box log",
  "the sync box's log ends at 1234.5 s, before run 3's end".

Chosen first by the events stage, in its own transaction, and never chosen again on its own: a
person deleting the row (and what depends on it) has it chosen again, as with every computed row.

### 4.2 The rule

1. **The sync box** keeps time if its codes hold every run the rig recorded, each from its run
   start to its end code; without a rig record, the session's start and end codes.
2. **Otherwise the NI**, if SpikeGLX recorded codes that hold them.
3. **Otherwise lab time** (part 4). Until part 4 is built, such a session is still taken in and
   archived, so nothing is lost; its events stage reports "no clock: neither the sync box nor the NI
   recorded, and lab time is part 4" on each pass, and the session is processed once part 4 is
   merged.

A sync box whose codes are complete but disagree with the NI's stays the clock and goes to tier D,
as today: a disagreement alone is not taken to mean the sync box stopped.

### 4.3 One interface

`timebase/clock.py` answers, for the chosen clock:
- **the reference:** where t = 0 is, and the wall-clock time it was, with its source;
- **the codes:** the session's code words in session seconds;
- **a device's map:** how a system's own time maps onto session time, and how that was found.

Every caller that reaches the sync box directly today goes through it. They are, at `9f78a3e`:
`timebase/segments.py::session_reference` and its callers in `schema/timebase.py` (the sync box's
identity row, `TimingProvenance.make`), `schema/core.py::Segment.make`,
`schema/events.py::decode_syncbox_in_session_time`, `schema/eye.py::EyeCalibration.make`,
`nwb/gather.py::reference_from` and `_reference_time`, and the fixed texts in `nwb/timebase.py`.

### 4.4 Where t = 0 comes from

| Clock | t = 0 | Its wall-clock time |
|---|---|---|
| sync box | its first barcode, as now | the barcode's value, as now (`reference_source = 'barcode'`, or `'manifest'` as now) |
| NI | the NI's first sample | the rig's instants matched to the run-start codes (`'rig_clock'`, lab time once wl-time runs); failing that, the SpikeGLX file's creation time, to the second (`'nidq_meta'`) |
| lab time | the session's open | that instant on lab time (`'lab_time'`) |

---

## 5. Taking in a session without the sync box

- **The manifest, which wl-xcon writes** (the handover's item 11, answered 2026-10-11): `syncbox` is
  no longer required in `expected_systems`. wl-xcon lists it only when it is running at the open.
- **A session with no acquisition system:** a training session with no sync box, eye tracker or
  camera lists none, and would otherwise land as soon as its manifest appeared, while the rig still
  writes. The manifest gains `expects_rig_record`, and wl-xcon writes `xcon/DONE` when it closes its
  record; the session lands once every listed system and, when the manifest says so, the rig's
  record are done. A manifest without the field behaves exactly as today.
- **A sync box listed whose transfer never finishes** stays `STALLED` in the daily report, named,
  until a person lands the session without it (decision 10): one `wlpp` command, recorded with who
  ran it and when. The clock is then chosen by §4.2.
- `contracts/manifest.py`'s "the timebase is always the sync box" becomes true of the label only.

---

## 6. Each device on the chosen clock

### 6.1 The sync box as the clock

Unchanged.

### 6.2 The NI as the clock (part 3)

- **Events:** the NI's decoded codes (`events/extract.py::extract_nidq_words`) go through the same
  decoding and assembly as the sync box's, in session seconds counted from the NI's first sample at
  its `.meta`'s rate.
- **The probes:** on the NI through SpikeGLX's own sync, unchanged.
- **Intan** (strobe only): fitted to the NI by matching its strobe edges to the NI's strobes, a rate
  and an offset with residuals kept, `time_source = 'strobes'`.
- **The rig's records:** timed by their codes, and the rig's clock fitted from them, as on every
  session (§3.3), now against the NI's codes; a row with no code (a schedule) takes its session time
  through the fit, labelled `rig_clock`.
- **A sync box that recorded part of the session:** fitted to the NI by its codes and kept as a
  cross-check over the part it covers (§7).
- **An NI that restarted within the session:** its files are joined by the rig's instants for the
  run-start codes on each side, and the tier says so (§7).

### 6.3 Lab time as the clock (part 4)

*Designed in full on 2026-10-19 by amendments 20 to 26, which replace this sketch where they differ:
the rig's own clock keeps the session's time, and lab time brings other computers' devices onto it.*

- **The rig's records:** on lab time as written, once the rig's clock follows wl-time.
- **Intan,** in a session with no NI: matched by its strobes against the rig's code instants (the
  timed event log).
- **The eye tracker and the cameras:** by their frames' host stamps, once the camera fallback exists
  (§9).

wl-works' time-service design allows this: PTP is "Never the alignment source where a shared-signal
alignment exists" (§4), and its §4.1 names the case: "Trigger pulled → frames are still PTP-stamped
and usable at sub-ms. PTP domain broken → the trigger is still exact. Precision is lost; the session
is not." A session without the sync box has no shared signal. **The NWB builder spec's §4.4 ("PTP is
never a timestamp here") is amended accordingly:** with the sync box, unchanged; without it, lab time
is the clock and says so.

### 6.4 With every clock

A device that cannot be fitted is left out of the file and named, as an unfitted system is today.

---

## 7. Tiers, and what wl.works sees

- **Tiers A to C keep their meaning,** sessions timed by the sync box. Two new letters:
  - **N:** the NI kept time, its codes agreeing with the rig's record (the same runs, each with its
    start and end code, as tier C's codes agree with the task file) and, where the sync box recorded
    part of the session, with the sync box's codes over that part. A change or control count that
    disagrees leaves that file's rows untimed (§3.3) and does not change the tier;
  - **L:** lab time kept time.
  - **D:** any check failed, whatever the clock, and still refused for publishing.
  An A on a session without the sync box would mislead a reader who knows A as two hardware code
  records aligned by barcodes; the letters keep the clock visible.
- **Publishing:** N and L publish as A to C do (decision 11).
- **The listing** gains `clock` (`syncbox`, `nidq`, `lab`), the new letters, and §3.4's counts.
- **The NWB description** gains the same; its `reference_source` gains §4.4's values.
- **The NWB file's `processing/timebase`** says which clock kept time, drops the fixed sentence about
  the sync box's first barcode, and records each device's map with its source (`barcode`, `strobes`,
  `codes`, `rig_clock`, `host_stamp`).
- **wl.works:** an amendment in `pending-wl-works-amendments.md` (its contract types accept A to D
  and two reference sources today), and a note sent when each part's change is merged.

---

## 8. Testing

On synthetic sessions throughout; no rig exists yet. Each test is seen to fail first; the plan names
its mutation checks; the full suite runs once per branch on both interpreters.
- **Part 1:** every row of both files is stored as written; changes and controls are timed by their
  codes on a session with the sync box; a count that disagrees leaves its rows untimed and names
  both counts; an unreadable line is named by file and line; another animal's `config.json` writes
  no rows; no name, account or token reaches the NWB file; the listing's counts; a `.sqlite` file in
  `xcon/` changes nothing.
- **Part 2:** every existing synthetic session gives the same rows, tiers and NWB contents through
  the interface as before it.
- **Part 3:** an NI-only recording session gives its events, runs, Intan fitted by strobes, the
  rig's records timed by codes and by the rig's clock fit, tier N, an NWB file and its listing entry;
  a sync box that stops mid-session hands the whole session to the NI, its part a cross-check; a
  listed sync box that never finishes stays stalled until the command lands the session; a manifest
  without `syncbox`, and one with `expects_rig_record`, land as §5 says.

---

## 9. Asks of other repositories

- **wl-xcon:**
  1. the manifest's two changes (§5): list `syncbox` only when it is running at the open; set
     `expects_rig_record` and write `xcon/DONE` at the close;
  2. before part 5, each new file's name and row shape: the grown change log, `reasons.jsonl` and the
     timed event log;
  3. which code each control kind strobes, and that each row strobes exactly one (§3.3);
  4. the trial start and end instants and each code's instant (XC-288 §3.1, §3.2), which part 4
     needs; *made precise by amendment 25: every word strobed with its instant, the open instant, and
     the clock pairs.*
- **wl-sync and the lab:** the cameras' fallback (decision 4): when untriggered, the eye and behavior
  cameras free-run with host timestamps on lab time; or the rig's strobe goes into a camera GPIO
  input, recorded per frame, which would make each camera a strobe witness.
- **wl-time** (no repository yet) **and wl-works' time service:** the rig, SpikeGLX, eye tracker and
  camera computers disciplined to lab time, and each host's state reported; and that lab time as the
  clock where there is no shared signal follows the time service's own §4.
- **wl-works:** §7's listing and description changes, through `pending-wl-works-amendments.md`.

---

## 10. What a plan must verify rather than assume

1. **Which code each control kind strobes,** and its number: the rig's codes come from each task's
   allocation (`allocation.code_for`, `wl_xcon/service.py`'s `FRAMEWORK_CODES` names
   `PARAM_CHANGED`, `RUN_START` and `RUN_END`), and this repository names only `MANUAL_REWARD`
   (4134, the runs-and-trials spec). Read where a session records its allocation's codes, and
   whether this pipeline's decoding keeps them; and the kinds as `controls.jsonl` writes them, for
   the listing's counts of pauses and manual rewards.
10. **What limit on the rig's clock fit's residuals** separates a fit from none (§3.3), measured on
    the synthetic rig record against a stated jitter, never assumed.
2. **That `extract_nidq_words` gives what the decoder takes from the sync box:** the same words, the
   escapes and payloads intact, with times; and how a decode error is counted.
3. **How Intan's strobe edges are read today** (the strobe-witness count in
   `TimingProvenance.make`), and whether their times are kept for a fit.
4. **Every caller of the sync box's reference and codes,** against §4.3's list.
5. **The enums that widen:** `TimingProvenance.tier` and `SystemTimebase.time_source`, in a database
   declared before them; give the `ALTER` and check before writing, as `NwbChange.kind`'s
   `corrected` was (the subject-corrections spec, amendment 9).
6. **The contracts that widen:** the listing's schema (`additionalProperties: false`) and the NWB
   description's `reference_source` and tier, within version 3 or not; what wl.works' vendored copies
   refuse.
7. **The storage type for JSON values** (`was`, `now`, `detail`, `row`) in DataJoint 2.3.3.
8. **What a SpikeGLX `.meta` holds for wall-clock time** (`fileCreateTime` or another field), and to
   what resolution.
9. **How the watcher reads the manifest** (`ingest/watcher.py`, `ingest/sentinel.py`), for
   `expects_rig_record` and the person's landing command.

---

## 11. Records this changes

- **The 2026-08-12 design spec:** an amendment where it says the sync box is present at every
  session and the sole recorder on training days, pointing here.
- **The NWB builder spec:** an amendment to §4.1, §4.2 and §4.4 (§4.4, §6.3).
- **`contracts/manifest.py`'s docstring** (§5).
- **`HANDOVER-wl-expcontroller.md`:** item 1 answered (this design, and §9's asks of wl-xcon);
  `docs/pending-wl-xcon-amendments.md` the same.
- **`docs/pending-wl-works-amendments.md`:** §7.
- **`docs/ops/lab-host-protocol.md`:** the listing's new fields (its test holds the document to the
  models).
- **`docs/CHECKPOINT.md` and `wl.yaml`,** at each part's merge.

---

## Amendment, 2026-10-18, made while planning part 1

1. **§10's item 1, answered: the session folder does not record the codes' numbers,** and part 1 is
   not planned until it does. Read in wl-xcon's `main` at `a607414`: each framework code is the run's
   allocation's (`Allocation.code_for`); `runs.jsonl`'s start row names the allocation by its path and
   `config.json` carries no codes; and a pause, resume or mark strobes nothing when its run's
   allocation has no code for it (`taskd.py`'s `_pause`, `_resume`, `_stamp`). The kinds
   `controls.jsonl` writes are `pause`, `resume`, `mark`, `note`, `schedule`, `cancel`,
   `scheduled_stop`, `stop`, `end` and `reward`; only a `reward` always strobes (`MANUAL_REWARD`, or
   it is refused), and each row also carries its `run`. In this repository, a code outside `Marker`
   is stored as an event named `CODE_<n>` (`schema/events.py`), and the `PARAM_CHANGE` escape's
   sequence number is not kept. Copying the reference allocation's numbers was offered and declined
   (ADR-0007; wrong for any task numbering them otherwise), as was timing controls by the rig's clock
   alone for now: the requester chose to ask wl-xcon first (`docs/pending-wl-xcon-amendments.md`,
   the section opened 2026-10-18), and to plan part 1 with §3.3's joins once the record says which
   code each row strobed.

## Amendment, 2026-10-18, wl-xcon's answer

2. **Each row says the code it strobed** (wl-xcon, 2026-10-18; merged on its `main` as `67d7028`,
   read at `e5b9835`). Every `parameter_changes.jsonl` and `controls.jsonl` row
   gains `event_code`, an integer or null: the number that row's act strobed, from the allocation the
   session's process loaded, or null when it strobed nothing. **A row written before the field has no
   key, which is read as unknown, never as null.** A change carries `PARAM_CHANGED`'s number;
   `pause`, `resume` and `mark` their own when the allocation has one; a `reward` always (one
   without its code is refused and writes no row); an `end` `HEAD_RELEASED`'s when ending released
   the head; `note`, `schedule`, `cancel`, `scheduled_stop` and `stop` are always null. It is on the
   row rather than the run's start row because a resumed session can come back on another
   allocation that no file names until its next run. A run whose allocation lacks `PARAM_CHANGED`
   faults at the first change it applies, before applying any value (the first correction below).

   *Until wl-xcon's corrections, this said the field was being built on wl-xcon's branch
   `xc321-event-code`, and that such a run is refused before it applies any value, as wl-xcon first
   said.*
   - **wl-xcon's three corrections** (2026-10-18, once merged; each read in its `main` at `7398d3e`,
     whose code is `e5b9835`'s):
     1. **A run whose allocation lacks `PARAM_CHANGED` is not refused before it starts.** It starts,
        its start row written and its codes strobed, and faults at the first trial boundary that applies
        a staged change, before applying any value or writing any change row
        (`taskd.py::_apply_staged`, which looks the code up first). A run with nothing staged never
        faults on it.
     2. **`HEAD_RELEASED` is strobed with no row in a head-fixed `wlx run` session**, at its run's end
        and while it waits for the animal's return (`taskd.py`, the run's end and its fault handler,
        both `not self.service`). The `end` row exists only in a `wlx taskd` service session
        (`end_runs`), whose release it records. So a `wlx run` session's `HEAD_RELEASED` codes join no
        row, and none needs to.
     3. **Some fault paths strobe a code and write no row.** A manual reward's code goes out before the
        pump runs and its row after it, so a pump fault leaves the code alone (`taskd.py`'s reward);
        wl-xcon also names a mark stamped in a trial that then faults. Each reads here as a count that
        disagrees: that number's rows stay untimed, and the problem names both counts.
   - **§3.3's joins read it:** the n-th row carrying a number takes the time of the n-th occurrence
     of that number in the session's codes; a count that disagrees leaves that number's rows untimed,
     naming both counts; a row without the key is untimed. A task's own mark can strobe a framework
     code's number (wl-xcon's XC-382, a load-time warning there), which reads here as such a
     disagreement.
   - **§9's "each row strobes exactly one" reads "at most one".**
   - **The later asks:** the manifest is wl-xcon's XC-356 (its `DONE` at a stranded session's close
     goes to the PI's welfare review); the new files' shapes are its XC-383, each of builds C3, C4 and
     C7 sending its file names and row shapes before it is built; the instants are XC-288 §3.1 and
     §3.2, with no accuracy stated for them until wl-xcon has measured the rig's clock, so part 4
     assumes none.

## Amendments, 2026-10-18, made while proving part 2's plan

3. **The clock is chosen when a stage first asks for it,** in that stage's transaction, and recorded:
   `timebase.SessionClock`, an Imported table, since the choice reads the session folder. In the
   daemon's order the first to ask is the events stage. §4.1 said "in its own transaction": a stage
   ahead of the events stage was built first, and every test that runs a stage directly had then to
   choose the clock before it (285 failures and errors across 18 test files, each "no clock is
   chosen"), for nothing a recorded choice does not already give. A choice made by a stage that then
   fails rolls back with it and is made again, identically, on the next pass.
4. **Part 2 checks only that the sync box recorded the session** (its log is there). Whether its
   codes hold every run (§4.2's rule 1) arrives with part 3, the first part with another clock to
   choose. A session the sync box did not record raises "no sync box log, so no clock is built for
   this session yet" in its events stage, on each pass, and records nothing, as the missing log
   raised there before.
5. **The NWB file's sentence about t = 0** (`nwb/timebase.py`) is unchanged in part 2: while the sync
   box is the only clock it is true of every file. Part 3 makes it the clock's.
6. **What moved:** `reference_from`, `BARCODE_EPOCH` and `CLOCK_DISAGREEMENT_S` to
   `timebase/clock.py`, still importable from `nwb/gather.py`; `schema/events.py`'s
   `decode_syncbox_in_session_time` to `timebase/clock.py::syncbox_codes`, unchanged. A test finds
   any module outside `timebase/clock.py` that reaches the sync box's reference or codes directly.
7. **A plan's neighbour runs name their files in the suite's order.** `tests/schema/test_guardrails.py`'s
   blob probe leaves a session with no sync box log in the shared test database, and run before
   `tests/schema/test_daemon.py` it fails three of that module's clean-pass tests, on the base commit
   as on part 2 (checked at `54d585b`). The suite runs the daemon's tests first.

## Amendment, 2026-10-18, from part 2's final review

8. **The daemon records every session's clock first, each in its own short transaction, and a stage
   whose snapshot predates another pass's record reads it with a locking read.** Two daemon passes can
   overlap: nothing stops a second cron pass (`daemon.py`). Chosen only on first ask (amendment 3), a
   stage whose transaction opened, `WITH CONSISTENT SNAPSHOT` as every DataJoint transaction does,
   before the other pass committed the clock found no row, its own insert changed nothing, and its
   re-read still saw the snapshot, so it raised; and a pass whose events stage had recorded the row but
   not yet committed held its lock for the length of a decode, which another pass's stage waited on for
   the lock timeout. `daemon._first_tables` now records each session's clock before the events stage,
   and `clock_for` reads with `FOR SHARE` when its re-read is empty. A stage asked outside a daemon pass
   still records the clock itself, as amendment 3 says.

## Amendments, 2026-10-18, made while proving part 1's plan

9. **§10's item 7, measured: the values and the line are stored as JSON text** (`mediumtext`), not in
   MySQL's `json` type. On the test MySQL through DataJoint 2.3.3, a `json` column gave
   `123456789.12345679` back as `123456789.1234568`, a double's seventeenth digit rounded, and refused
   NaN ("Invalid JSON text"), which wl-xcon's `json.dumps` writes for a value that is not a number, and
   which would fail the session's event stage on every pass. A `mediumtext` column gave back every
   probed value and line exactly. `row` is the line as written; `was`, `now` and `detail` are each
   value's JSON text, keys sorted.
10. **The NWB file holds the training records of its own runs, and those outside any run** (§3.2 did
    not say which): a row of a run the file does not hold is about that run, as ask 5's warnings are
    chosen (its amendment 1). wl-xcon numbers a row by the run in progress or, between runs, the last
    one; a row before the first run has none.
11. **§10's item 10, measured: no fit may miss a code by more than 50 ms**
    (`events/rigtiming.py::RIG_CLOCK_LIMIT_S`). The synthetic rig record states its jitter, each instant
    up to 5 ms either side of its code, its clock 20 ppm fast (`synth/peripherals.py`). Over 2000
    synthetic sessions of 15 s to two hours, 4 to 200 instants each, the largest miss was 6.8 ms (the
    99th percentile 6.1 ms). A step in the rig's clock of 2 s, as a resumed process anchors it again,
    was refused in each of 1000 sessions; one of 0.5 s in 998, of 0.1 s in 819, and of 50 ms in 3. A
    step at a session's last instants tilts the line instead, within the step. wl-xcon has not
    measured its rig's own jitter (amendment 2), so the limit is set against the stated one.
12. **The rig's clock fit is kept in `core.RigClock`,** beside the records it times and written with
    them by the events stage: `rig_at_zero` (the rig's clock at session t = 0), `drift_ppm`, the
    instants matched and the residuals. §3.3 said "with the session's other device maps"; those are
    each an acquisition system's (`timebase.SystemTimebase`), and the rig is not one of `SYSTEMS`. A
    control is timed through the fit only when its row says it strobed nothing (`event_code: null`) and
    carries an instant; a row whose code's count disagrees, or that does not say, stays untimed, as
    amendment 2 says.
13. **§10's item 1, verified:** each row's `event_code` (amendment 2) says which code it strobed, and
    this pipeline decodes every number from 4096 up as a simple event (`contracts/events.py`'s ranges),
    so the framework codes are among the session's codes; a payload's words, read by position, are
    not counted (`events/rigtiming.py::code_times`). The listing's pauses are `controls.jsonl`'s
    `pause` rows and its manual rewards its `reward` rows (wl-xcon's `taskd.py`, read at `7398d3e`).
14. **§10's item 6, verified: both contracts widen within themselves.** The NWB description gains
    `training_records` (`parameter_changes`, `controls`) within version 3, as ask 5's fields were
    added; the listing gains `training_records` (`parameter_changes`, `pauses`, `manual_rewards`) and
    `clock`, listed from part 1 since `timebase.SessionClock` exists. Both exported schemas refuse
    fields they do not know, so wl.works' copies must take these first
    (`docs/pending-wl-works-amendments.md`, the section opened 2026-10-18). Every listed session is
    listed once more when this ships.
15. **The events stage's activation binds `timebase`** (found while proving). Since part 2 the stage
    reads the session's codes through its clock, which records `timebase.SessionClock` on first ask,
    and `events.activate` bound `pipeline` and `core` only. The daemon binds every schema, so nothing
    in production failed; but `tests/schema/test_events.py` run alone failed 20 of its 30 tests at
    `5b7744b` ("assert self._connection is not None"), as did each of part 1's own tests run alone.
16. **The synthetic run record's instants are the rig's clock** (`synth/peripherals.py::rig_clock`):
    each run row's `at` was its session time, which wl-xcon never writes and nothing read. A profile
    without runs is unchanged; one with runs differs in those instants only.

## Amendments, 2026-10-19, from part 1's final review

17. **A lone UTF-16 surrogate in a rig record is kept as its escape.** wl-xcon writes ASCII lines, so
    half an emoji (one split at a field's length limit, or sent by a console peer) arrives as a
    `\ud83d` escape, which `json.loads` makes a character utf8mb4 cannot store; inserting it failed the
    session's whole events stage, on every pass. The training records' JSON text is now ASCII, exact JSON
    still, and every rig text column the events stage writes, ask 5's included, keeps a lone surrogate as
    its escape (`schema/events.py::_clip`).
18. **The rig's clock fit also refuses a rate no clock runs at:** zero or less (instants that do not
    advance divided by zero, failing the stage on every pass; two across a backward re-anchor fitted a
    negative rate that timed a note before the session), or more than 1,000 ppm either way
    (`events/rigtiming.py::RIG_CLOCK_DRIFT_LIMIT_PPM`; a quartz oscillator is specified to within
    100 ppm). Two instants always fit exactly, so a step between them shows only as a rate: a step under
    1,000 ppm of the time between them (3 s over an hour) is still taken as the rig's rate, and a
    control that strobed nothing is then timed up to the step off.
19. **A control's `trial_index` is wl-xcon's trial within its run, from 0** (`taskd.py` sets its index
    to 0 at each run's start), not a trial number. The NWB file's `controls` table gains the run it
    happened in (`run_number`), so the index can be read, and the database's comment and the synthetic
    rig say what it counts.

## Amendments, 2026-10-19: part 4, lab time, designed in full

20. **The requester's decisions.** Asked after part 1's merge whether the backup should be the
    computers' PTP/NTP clock rather than the NI, "because not all systems (or future rigs) will
    necessarily have an NI", and shown that the NI is still a shared-signal clock (it records the same
    codes on the same wire, and SpikeGLX syncs the probes to it) and that wl-works' time-service design
    makes PTP "Never the alignment source where a shared-signal alignment exists" (its §4), the
    requester kept both: the sync box, then the NI where a session has one, then lab time for every
    other session (§4.2 unchanged). What changed is the order of work: lab time is designed in full
    now and its asks sent, since it is the one backup every rig has and its inputs take longest; part 3
    is built while they arrive. **The brief,** in the requester's words: "Everything **should** run with
    at least a sync box, unless it fails. There will likely be 1-2 training rigs that wont have a NI or
    Intan (but also wont be used for recording). There may be a future rig that has a system different
    than an NI or Intan (but will still have a sync!)". So lab time is the last resort, used in practice
    by those training rigs when their sync box fails; it serves behaviour, to milliseconds or tens of
    milliseconds, and never neural data. A recording rig with Intan and no NI whose sync box fails is
    out of scope until such a rig exists; a future rig's other acquisition system is a question of what
    counts as "the NI" (part 3), not of lab time.
21. **The rig's own clock keeps a lab-time session's time; lab time brings other computers' devices onto
    it** (the requester's choice of three, against lab time itself as the clock and against a second,
    disciplined stamp on every row). wl-xcon's instants are its anchored clock (`taskd.Session.wall_now`:
    the computer's clock read once at the session's open, then carried on a steady clock, immune to the
    clock being stepped; its XC-288 §3.11), so the rig's records are consistent among themselves whether
    or not any computer is disciplined. The source keeps its name, `lab`, already in `timebase.SessionClock`
    and the listing's contract, and tier L; the spec's meaning for it is this one.
    - **Chosen** by §4.2's rule 3: the sync box's codes do not hold every run and no NI recorded the
      session, and the session has its own rig record (a `config.json` of its animal) with its open
      instant. With no rig record there is nothing to time: the session is archived, and its events
      stage says so on each pass.
    - **t = 0 is the rig's session open,** on its anchored clock. A rig instant's session time is the
      instant less the open, at rate 1, as the sync box's own timeline is session time when it keeps
      time (§4.5 of the 2026-08-12 design spec).
    - **The wall-clock time of t = 0** is the open instant itself: `reference_source` `lab_time` when the
      clock pairs (amendment 23) show the rig's computer synchronised at the open, else `rig_clock`.
22. **A lab-time session's events come from wl-xcon's timed event log** (its XC-288 §3.2), read through
    the clock's `codes()` (§4.3): each word the rig strobed, at its instant less the open. The events
    stage decodes and assembles them as it does the sync box's, so nothing downstream changes. The log
    must hold every word the card sent, escapes and payload words included, in emission order, each with
    its instant on the anchored clock: `decode_stream` reads a payload by position, so a log of named
    codes alone cannot be decoded. **Until the log exists** (wl-xcon's build C7) a lab-time session is
    taken in and archived, and its events stage says on each pass that lab time needs the rig's timed
    event log; nothing is assembled from `trials.jsonl`. Part 1's training records are timed as on every
    session: each change and coded control by its code, now the log's; the rig's clock fit, against the
    log's own instants, finds about zero drift; a control that strobed nothing is timed by its instant
    through it. A resumed session's processes, each on its own anchor, are joined through their maps
    (amendment 23).
23. **The clock pairs, and other computers' devices.** The rig writes a pair at each process's open, at
    least once a minute, and at its close: its anchored instant, its computer's disciplined clock, and
    that computer's synchronisation state as its time daemon reports it (synchronised or not, and the
    estimated offset). From each process's pairs, a map from the rig's clock to lab time is fitted, a
    rate and an offset with residuals kept, as part 1's rig clock fit is; a step in the computer's clock
    mid-session shows as residuals past a limit, and that process then has no map, the problem naming
    it, rather than a piecewise repair. **The eye tracker and the behaviour cameras,** once the camera
    fallback lets them free-run, stamp each frame on their computer's lab time; each stamp goes from lab
    time to the rig's clock to session time, its source `host_stamp`, its accuracy recorded as the stamp
    jitter, both computers' reported offsets and the fit's residual. A device whose computer, or the
    rig's, was not synchronised, or whose map failed, is left out of the file and named, as an unfitted
    system is today; the rig's own records still land. Intan and probes are out of scope (amendment 20).
24. **Taking the session in, its tier, the listing and the NWB file.** §5's ingest changes are unchanged
    and are built with part 3, which needs them too; lab time's build follows. **A sync box that recorded
    part of the session** (decision 8): lab time keeps the whole session's time, and the sync box's codes
    over the part it covers are matched to the event log's, agreement recorded as a cross-check and a
    disagreement sending the session to tier D. **Tier L:** lab time kept time and every check passed;
    any failed check, such as codes in the log that do not decode, is D, whatever the clock. L publishes
    as A to C do, labelled (decision 11). **The listing** gives `clock: "lab"` and tier L. **The NWB
    file's** `processing/timebase` says lab time kept the time, t = 0 at the rig's open, and lists each
    device's map with its source (`rig_clock` for the rig, `host_stamp` for a camera; §7's
    list); `session_start_time` is
    the open instant, `reference_source` `lab_time` or `rig_clock`. The NWB builder spec's §4.4 is
    amended for lab-time sessions only (§6.3). wl.works is sent tier L and the two reference sources
    when this is built.
25. **The asks** (§9's wl-xcon item 4, made precise), sent once this amendment is approved:
    - **wl-xcon:** (1) the timed event log, its XC-383 and build C7, holding every word the card sent,
      escapes and payload words included, in order, each with its instant on the anchored clock, its
      file name and row shape sent here before it is built, as agreed; (2) the session's open instant on
      the anchored clock in the record, and each resumed process's; (3) the clock pairs of amendment 23.
    - **wl-sync and the lab:** the camera fallback (§9), each frame with its host stamp and its
      computer's synchronisation state.
    - **The time service** (wl-works' row 60, waiting on its hardware, its §10): nothing beyond §9; the
      rig and camera computers disciplined, each reporting its state.
26. **Testing, and what can be built when.** On synthetic sessions: a training session with no sync box,
    its event log and pairs generated on a drifting rig clock offset from lab time; a resumed session
    that anchors again; a step in the pairs; a free-running camera's host stamps; a partial sync box,
    agreeing and disagreeing. Each asserts the planted times are recovered, tier L, and the NWB file.
    **Nothing is built against row shapes wl-xcon has not sent**; once it sends the event log's and the
    pairs', the pipeline's side is built and tested on synthetic sessions that write them, before any
    rig does, and checked on a real session once build C7 and the time service exist. The cameras' part
    waits on the fallback's shape. Part 2's deferred question (M2, the clock of a session processed
    before part 2) is decided before part 3 is built.

## Amendments, 2026-10-19, wl-xcon's answer and the clock of a session already processed

27. **wl-xcon's answer to amendment 25:** XC-391 to XC-393, all in its build C7 (its `main` at
    `243afb9`). **The open instant is already written:** the `session opened` row of
    `welfare_notes.jsonl`, whose `at` is the session's `opened_wall_at` (its `taskd.py`, `Session.open`),
    so t = 0 (amendment 21) is read there. A resumed process writes no such row yet; XC-392 adds one,
    perhaps in a file of its own. The pairs are written between trials, never inside a frame, so "at least
    once a minute" may hold only between trials (a trial lasts up to five minutes); a map's fit takes
    them as they come. Each file's name and row shape comes here before C7 is built.
28. **A session already processed keeps the clock it was timed by** (the requester's choice, 2026-10-19,
    deciding part 2's deferred M2 before part 3). A session whose events were staged before part 2 has
    no `timebase.SessionClock` row; the daemon records `syncbox` for it, the clock it was timed by, with
    that reason. And a session whose clock row is deleted while its events remain is never chosen again:
    its stage refuses, saying that the events must be deleted for the session to be timed again. No
    schema changes: built as part 3's first task, since part 3 is the first part with another clock to
    choose.

## Amendments, 2026-10-19, made while proving part 3a's plan

29. **Part 3 is planned and merged in two.** Part 3a takes a session in without the sync box (§5, and
    amendment 28's first rule); part 3b makes the NI its clock (§4.2's rules 1 and 2, §6.2, §7's tier N).
    Both backup clocks need 3a: until it, no session without the sync box is ever taken in.
30. **Amendment 28's two rules, by part.** Part 3a gives a staged session without a clock row `syncbox`,
    read from the database and never the folder, which may have been freed (`timebase/clock.py::
    choose_for`). Until part 3b the sync box is the only clock, so that is the clock that timed it,
    whether its events were staged before part 2 recorded clocks or its row was deleted since. Part 3b,
    the first part with another clock, replaces the rule with the refusal; by then the daemon has
    recorded every staged session's clock, so the refusal meets only a row deleted after it.
31. **§10's item 9, verified.** The watcher (`ingest/watcher.py::_evaluate_session`) lands a session
    once every system its manifest lists has a DONE marker (`ingest/sentinel.py::session_complete`),
    reports it INCOMPLETE until then and STALLED once quiet for `STALL_AFTER_S` (2 h), and the daily
    report lists each stalled session with what it is missing (`missing_systems`). Nothing read `xcon/`
    for completeness before this part.
32. **The manifest (§5).** `syncbox` is no longer required in `expected_systems`. `expects_rig_record`
    (a boolean, false when absent) holds the session until `xcon/DONE` is written; the marker's presence
    is the signal and nothing in it is read, since wl-xcon's agent rebuilds files in `xcon/` after the
    close, so no list of hashes would stay true. The report names the record `xcon` among what a stalled
    session is missing. The exported manifest schema gains the field, and wl-xcon is told the shape (its
    XC-356).
33. **Landing without (§5, decision 10).** `wlpp land-without --session --system --actor --reason`
    records who landed a stalled session without what, why and when, in `ingest.LandedWithout`, keyed by
    the manifest's session since it is written before the session lands, and then evaluates the session
    once, as a scan would. **It takes any listed system whose transfer has not finished, not only the
    sync box** (a stalled transfer holds a session the same way whichever system it is), and `xcon` for a
    manifest that expects the rig's record. It refuses what would change nothing or cannot be stored: a
    system not listed, one whose marker landed, a session already landed, a manifest that cannot be
    read, a subject or path too long to land. The watcher, the stall check, verification and the report
    do not wait for it; the landed topology marks it `waived`; it gets no AcquisitionSystem row; its
    partial files stay in the folder, and so in the archive, which takes every verified session. **A
    session landed without its sync box is never timed by it** (decision 8): until part 3b its events
    stage says, on each pass, who landed it without the sync box and when, and that the NI or lab time
    keeps its time. It is not listed until it has a clock.
34. **`timebase.activate` binds `ingest`** (found while proving): `SessionClock`'s choice reads
    `ingest.LandedWithout`, and `tests/schema/test_events.py` run alone failed 21 of its 31 tests without
    it, as amendment 15 found for `timebase` itself.

## Amendments, 2026-10-19, made in part 3a's final review

35. **`xcon/DONE` is a DONE marker** (the requester's choice of 2026-10-19, replacing amendment 32's "the
    marker's presence is the signal and nothing in it is read"). wl-xcon writes it as a system writes its
    own: `system: xcon`, and each of the rig's record files with its size and blake3, leaving out the
    XC-288 agent's SQLite file and its rebuild's dated snapshot, which the agent may rewrite after the close
    (wl-xcon's XC-357). The rig's files are checked on arrival as a system's are, so a mismatch
    quarantines the session, and archived against the marker, so §5's session with no acquisition system,
    its record alone, lands `verified` and is archived. An empty `xcon/DONE` declares nothing and lands
    `declared_only`, which the daemon does not archive; one that does not parse does not close the record.
    The final review found that the presence-only marker stopped the archive, the reclaim proof and
    rehydration for every session expecting the record (its C1), and left a session with the record alone
    `verified` with nothing checked and never archivable (its I1).
36. **What a person landed a session without stays out of the archive's checks** (the final review's
    I2). The archive, the reclaim proof and preview, the daily report's reclaim list and rehydration read
    each system's own marker and `xcon/DONE` (`archive/verify.py::_expected_digests`), never every file
    named `DONE`, and none of a system the session was landed without, whose marker never finished and may
    not parse. That system's files are stored byte for byte, never laid out as a stream, since a stalled
    transfer can leave a `.bin` without its `.meta` or an `amplifier.dat` without its `time.dat`. This is
    what makes amendment 33's "its partial files stay in the folder, and so in the archive" true.
37. **A session no clock can time yet is left alone until one can** (the final review's I3). The daemon
    runs its computed stages only for sessions whose clock is recorded (`daemon.py::_clocked`), and
    `SessionClock` records nothing and parks no error for a session no clock can time, so it is asked
    again on every pass. Before, such a session was given a tier-D timing record from checks that never
    ran, and its jobs were parked in `error`, which nothing retries, so part 3b would have met sessions to
    repair by hand. Its events stage still says why on each pass. Until it is timed it has no tier, so it
    cannot be reclaimed and keeps its scratch.
38. **`wlpp land-without` records the session's absolute path** (the final review's I4): every later
    stage resolves the recorded path from the daemon's directory, not from the shell's it was typed in.
