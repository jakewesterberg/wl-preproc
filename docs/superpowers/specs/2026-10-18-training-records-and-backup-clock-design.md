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

## Amendments, 2026-11-19: part 3b, the NI as the clock, designed

Reading the code against §4.2, §4.4, §6.2 and §7 (at `4bd10f1`, 2026-10-30) found eleven questions this design did
not answer. The requester answered them on 2026-10-30: the segment key renamed everywhere (39), and the other ten
and the split as recommended (40-50). The key's value was chosen on 2026-11-19 (39). The groundwork, with the
spike behind amendment 49, is in `~/.cache/wl-preproc-scratch/2026-10-30-part3b/`.

39. **The segment key is `segment_id`, the file's path within its system's folder** (the requester, 2026-10-30
    and 2026-11-19). Today's `segment_barcode`, a segment's first barcode, exists only where the sync box ran; a
    session without it has no barcode on any device. The new key is the path `core.Segment.make` already
    computes (`scan.path` relative to the system directory, as `DoneMarker` and `core.RejectedSegment` name
    the same file), text, e.g. `run1_g0/run1_g0_t0.nidq.bin`. It is the same under every clock, never changes
    once the session lands, is unique within a system by construction, and, like the barcode, shifts no key
    when a missed file turns up later. `core.Segment`'s `file_path` attribute becomes the key, so there is one
    copy. It is renamed in every table that carries it (`core.Segment`, `ephys.ProbeCensus` and its part,
    `ephys.SegmentConfig`), in the listing (`segments[].segment_id`, `runs[].segments` a list of those paths)
    and its exported schema, and wl.works' contract is amended. Development databases are rebuilt, not
    migrated: there is no production database before January, and serv is purged before it deploys.
40. **Part 3b is built in three branches.** **3b-1, the NI keeps time:** amendments 39, 41-45, 47, 48 and 50.
    **3b-2, Intan by strobes:** amendment 49. **3b-3, a sync box that recorded part of the session as the
    cross-check** (§6.2, §7's tier N). **An NI that restarted within the session is part 3c;** until it, such a
    session is tier D, its reason saying the NI restarted. It is rare, and joining the files needs storage this
    design does not give.
41. **Rule 1 reads "each run from its first code to its last"** (§4.2). A faulted run sends no end code
    (`events.py:625`); "the sync box recorded throughout" is what the rule means, and a missing end code alone
    does not hand a healthy session to the NI.
42. **A sync box that restarted stays the clock.** The 2026-10-22 design bridges a restart through a device that
    recorded through it (wl-sync part 2). The NI keeps time only when the sync box has no log, or when its log
    ends before the session's last run does with no later segment.
43. **The choice is cheap first.** Rule 1 is decided from the sync box's log and the rig's runs, both small. The
    NI's codes are decoded only when rule 1 fails, and outside `SessionClock`'s transaction: the choice is
    computed, then inserted. The NI's word channel is read in chunks (`timebase/channels.py::column_chunks`,
    since the scaling piece).
44. **Rule 2 needs every run within the NI's recording.** The rig may strobe `SESSION_START` before SpikeGLX
    starts; the session's start and end codes outside the NI's recording are not required.
45. **Tier N's evidence** (§7). `TimingProvenance` keeps one row. Under the NI its sync-box-only fields
    (`n_barcodes_emitted`, the task file's check) are null. N needs every run of the rig's record held by the
    NI's codes, each from its first code to its last, and the codes' content agreeing with the rig's record.
    The partial sync box's agreement is added by 3b-3.
46. **The wall-clock time of t = 0 under the NI** (§4.4). The rig's clock fit (`core.RigClock.rig_at_zero`)
    where it fitted, checked against the manifest's start within 60 s as a barcode's is (`'rig_clock'`).
    Failing that, SpikeGLX's `fileCreateTime` (`'nidq_meta'`): "the local date and time on the recording
    machine", to the second, with no zone (SpikeGLX's `Metadata_Help.md`). So the NI machine's time zone is a
    lab setting. The time is good to about a second plus the file's opening latency, and marks the file's
    `firstSample`, which is the NI's first sample. The synthetic `.meta` gains `fileCreateTime`. Part of 3b-1.
47. **What a person landed a session without is never used** (decision 8). A sync box landed without is not a
    cross-check; 3b-3's is a sync box that landed but whose log ends early.
48. **A session timed before its clock was recorded.** 3b-1's deploy records `syncbox` for every session that
    has a `TimingProvenance` row and no `SessionClock` row: a one-time back-fill, in the populate guard's
    pattern. From then on, a session with timed results but no clock row is refused, naming it; this replaces
    amendment 30's rule, as that amendment foresaw. It guards development databases and a restore from an old
    dump; no production database exists before January.
49. **Intan by strobes (3b-2), as the spike of 2026-10-30 found.** Edges carry no values, and matching by order
    is what `timebase/fit.py` forbids for barcodes. Both sides read the strobe's rising edge, the NI's from its
    strobe bit, not the word's latch.
    - **Anchor:** candidate anchors from a 2 s window of Intan's first edges, each verified by extending the
      map over the first 120 s.
    - **Extension:** the whole recording in time order. Each edge is matched to the NI's nearest within 0.5 ms,
      and rate and offset are refitted as it goes. The extension starts with small steps, about 10 edges, and
      grows.
    - **Fit:** a residual limit of 100 µs, then a final fit, with residuals kept as for barcodes;
      `time_source = 'strobes'`.
    - **Ambiguity refuses:** the best anchor must match at least 95% of edges, and at least 10 points more
      than the runner-up. A strictly periodic code stream cannot anchor. An Intan that cannot be anchored is
      left out of the file and named (§6.4).
    - **The spike's result:** on the rig's irregular timing there was no wrong match with 1% or 10% of edges
      dropped, 0.5% glitches, 60 or 200 ppm between the clocks, or 20/25 kHz sampling.
50. **3b-1 carries the clock choice's four deferred minors** (`docs/handoffs/2026-10-18-clock-choice.md` §6),
    and the rest of what the NI as the clock widens:
    - **The four minors:**
      - `SystemTimebase.make`'s identity row stops assuming the sync box;
      - the guard test catches every way of reaching the sync box directly;
      - the stale docstring naming `decode_syncbox_in_session_time` is fixed;
      - `timebase/clock.py::SOURCES` is given a use or removed.
    - **`Clock('nidq')`:** t = 0 at the NI's first sample, codes from `extract_nidq_words`. SpikeGLX's probes
      are on the identity map.
    - **The enums and contracts that widen:** `TimingProvenance.tier`, `SystemTimebase.time_source`,
      `NwbFile.reference_source`, the listing, the NWB description and `processing/timebase`'s texts.
    - **Synthetic sessions without the sync box,** their NI carrying no barcode. This is a recipe the
      synthesizer refuses today, and 3a's tests faked it by deleting `syncbox/`.
    - **wl.works** is sent the key's name and shape when this spec is approved, before anything changes (its
      `lab-host-protocol-wl-preproc.md` asks for it with 3b's spec). It also hears of tier N, the
      `reference_source` values `rig_clock` and `nidq_meta`, and the listing's `clock` at 3b-1's merge.
51. **Under the NI, a device with no way onto its clock makes the session tier D** (the requester's choice of
    2026-11-19, over leaving it out and publishing). 3b-1 places only the SpikeGLX files on the NI's clock. The
    eye tracker and the cameras wait for lab time's host stamps, and Intan for 3b-2. A session that recorded
    any of them is tier D, refused for publishing, as an unfitted system makes any session today
    (`TimingProvenance.make`'s `failed`); it publishes once a later part can place every device it had. Asked
    at the same time, the requester made lab time a priority, "so that data can be synced in a pinch".
    - **wl-xcon** was asked to plan its three inputs (its XC-391 to XC-393), and holds them for its PI's next
      check-in.
    - **wl.works** answered that ordinary NTP (chrony) on the rig and eye-tracker computers suffices until row
      60's appliance exists. It also said that configuring those computers is wl-orchestrator's machine stacks'
      work, and the lab owner's to approve.
    - **wl-sync or the lab** owe the eye tracker's per-frame host stamps. wl-sync had no live session to tell
      on 2026-11-19.

## Amendments, 2026-11-20, made while building part 3b-2

Part 3b-2 builds amendment 49: Intan placed on the NI's clock by its strobes. The plan
(`docs/superpowers/plans/2026-11-20-part-3b-2-intan-by-strobes.md`) made eight decisions the spec was silent on
(52). Building the matcher showed that amendment 49's algorithm, as written, accepts wrong anchors, so the
rulings of the build changed it (53). Where 53 differs from 49, 53 binds. The ledger,
`.superpowers/sdd/2026-11-20-part-3b-2-intan-by-strobes/progress.md`, holds the full wording of every ruling.

52. **The plan's eight decisions.**
    1. **Windows.** The anchor's 2 s windows are tried one after another, from Intan's first edge through its
       first 600 s (`ANCHOR_SEARCH_S`). The first window whose best candidate passes the ambiguity rule anchors
       the map. Amendment 44 lets the rig strobe before SpikeGLX starts, so Intan's first window may hold no
       edge the NI saw. The extension runs forward from the anchoring window, then backward from it. (see
       amendment 53)
    2. **What the 95% is a fraction of.** Intan's edges in the verification span whose predicted NI time lies
       within the NI's recording, give or take 0.5 ms. An edge strobed before the NI started is no evidence
       against the anchor.
    3. **A floor.** A candidate needs at least 10 matched edges (`MIN_MATCHED_EDGES`) to be scored at all.
       Fewer cannot be told from chance.
    4. **Each Intan recording separately.** Each is anchored on its own against the NI's first file, the only
       one on session time (amendment 50). The system's rate is pooled across the anchored recordings, each
       with its own intercept, as for barcodes. A recording that cannot be anchored is rejected as
       `no_strobe_anchor`, and the others are kept.
    5. **What is recorded.** `RejectedSegment.reason` is a fixed vocabulary, so it holds `no_strobe_anchor`.
       The full sentence goes to the module's logger at INFO. `SystemTimebase` gains `n_strobes_matched`
       (null except under strobes). (see amendment 53)
    6. **The NWB file.** Under the NI, `processing/timebase/system_clocks` gains a `time_source` column
       (`clock`, `strobes`, `none`), and the NI texts no longer say every residual is 0. Sync-box files are
       untouched.
    7. **The synthetic sessions.** Back-to-back trials of fixed length are a periodic strobe stream, which
       cannot anchor; the rig's trials start on the animal. The fixture is corrected, not the design: a recipe
       field `trial_waits_s`, empty by default, gives an animal-paced wait before each trial. The new recipe
       `NI_INTAN_RECIPE` uses it.
    8. **Strobe witnesses.** `TimingProvenance.n_strobe_witnesses` stays 0 under the NI. Intan is fitted
       there, not counted as a witness, and tier N does not read the column.
53. **The matcher as built.** These rulings changed amendment 49's algorithm. Constants are in
    `wl_preproc/timebase/strobes.py`.
    - **R1, refit from 3 pairs** (49: "refitted as it goes", about 10 edges at first). The walk refits rate and
      offset once 3 pairs exist, over the last 20,000. With 20 pairs at rate 1.0, verification drifted, and
      whether a period-shifted rival verified was luck: 8 of 144 swept periodic streams anchored wrongly, every
      pair wrong, and the margin rule never fired.
    - **R2, the whole recording must match at least 95%** (49: the best anchor matches 95%). After the full
      extension, the matched fraction of the recording's eligible edges must reach 95%, or the recording is
      `Unanchored`. A wrong map drifts off. This makes an Intan with more than about 5% glitch edges a refusal
      (R9), which is honest, not a wrong placement.
    - **R3, neighbour rivals.** The runner-up set also holds the best candidate's 8 neighbouring alignments on
      each side, each verified, so the margin rule has deterministic rivals. Kept (R8) although no test pins
      it after R1: it is insurance, and removing it slowed the suite.
    - **R4, residual blocks of 2,000 pairs.** The 100 µs limit is applied per consecutive block of 2,000
      pairs against that block's own line, not one global line, because a real crystal's rate wanders over 12 h.
      The final fit stays global, with residuals kept as for barcodes. **R14(e):** blocks are split at
      re-anchor points, so a line is never extrapolated across a blind stretch.
    - **R5, one-to-one pairs.** Where two Intan edges match one NI edge, the nearer is kept.
    - **R7, the brief's periodic stream was a code.** The spike's periodic stream (2.0 s, jitter 0.5 ms, bursts
      up to 4 words) anchors rightly on 12 of 12 seeds: its random burst sizes are a code, not a strictly
      periodic stream. The test for refusal uses one word per burst. The brief's stream joins a property test
      (R6): no wrong match, across periods 0.25-2 s, jitter 0-200 µs and burst sizes 1 and 4.
    - **R11-R14, re-anchoring.** A strobe-free break can let a sub-ppm rate change lock every later edge one
      word (1 ms) off, and the matcher returned them (6,733 of 89,743 pairs wrong, no exception). So:
      - **R11:** the walk anchors again after a blind stretch, under the same rule (95%, 10 points of margin).
        If that fails, the recording is refused, with the sentence "the strobes after a N s gap at T s cannot
        be anchored again".
      - **R14(b), the trigger:** more than 10 s (`REANCHOR_GAP_S`) of device time since the last matched
        pair, not the spacing of Intan edges. Sparse strobes under 10 s apart through a break had defeated the
        edge-spacing trigger.
      - **R14(c), the search:** the re-anchor scores a 2 s window from the resume edge against only the NI edges
        within 50 ms (`REANCHOR_SEARCH_S`) of the prediction. A spurious resume edge cannot hide the true
        alignment.
      - **R12:** the re-anchor is made at the first edge with at least 10 Intan edges after it within
        `VERIFY_S`. Sparse edges before it are left unmatched, never a refusal on their own: a stray edge in
        a break is not evidence about the map. **R13:** that count, and the re-anchor's check, stop at the next
        gap.
      - **R14(a), the refit:** it uses only the pairs from the last 120 s (`REFIT_SPAN_S`) of device time, and
        at most 20,000, so sparse pairs steer it.
      - **R14(d):** the refusal's gap is the device time since the last matched pair.
    - **R15:** Intan edges predicted outside the NI's recording are skipped, not re-anchored. They are
      ineligible by decision 1 of amendment 52, and re-anchoring on them refused the NI-late case.
    - **R16:** a re-anchor takes candidate offsets from every edge of its window, not only the first, so a
      spurious first edge cannot hide the truth.
    - **R17:** a 0.5 s periodic stretch after a break now anchors rightly: a period shift lies outside the
      50 ms search, and continuity across a break is evidence. The test of an ambiguous stretch uses 20 ms.
    - **R18:** an NI-side strobe loss longer than 10 s inside its recording makes the re-anchor fail and refuses
      the Intan recording. An NI that lost strobes mid-run also lost code words, which rule 2 already refuses.
    - **R10:** the backward pass is seeded with the fit over the forward pass's first 20,000 pairs, the ones
      nearest the junction, not the forward pass's final fit, which can be hours away.
    - **R19:** the synthetic Intan writes `rhs/<session_id>_rhs/`, and a recording is resolved with
      `find_recordings("rhs", session_dir / "rhs")`.
    - **R20:** the cache holds only edge sample indices. Each rate is read from its sidecar (`info.rhs`, the
      NI's `.meta`) on every call, so a changed header with unchanged data never returns a stale rate.
    - **R21:** under strobes, `n_barcodes_decoded` keeps the true count of barcodes Intan decoded, and
      `n_barcodes_matched` stays 0. The count is nonzero only where a sync box also ran (3b-3's case).
    - **R22:** a session with one unanchorable Intan recording is tier D, though its other recording is placed.
      This matches today's rule that any rejected file makes a session D (§6.4: "as an unfitted system is
      today").
    - **Left to later parts:** a partial sync box as the cross-check (3b-3), an NI restart (3c), and the eye
      tracker and cameras (part 4, amendment 51).
    - From the final review (I2): the 600 s anchor search is counted from Intan's first anchorable edge, the
      first with at least 10 edges within 120 s after it, not its first edge. This supersedes amendment 52's
      decision 1 on this point.
    - From the final review (I1): a refusal's sentence is logged at WARNING, with its percentages floored. This
      supersedes decision 5's INFO.
    - From the final review (I4): under the NI, when no Intan recording anchored, every file is rejected as
      `no_strobe_anchor` without searching again.
    - From the final review (I3): no residual gate is placed on the stored map. This is put to the requester.

## Amendments, 2026-11-20: part 3b-3, a sync box that recorded part of the session, designed

**Why.** When the sync box stops partway, the NI keeps time (3b-1). The box's files then have no way onto that
clock, so the session is tier D, and so is every session whose eye tracker or cameras ran. The requester wants
such data "synced in a pinch" (amendment 51).

**What the requester chose, 2026-11-20:**
- the part the sync box covered places the eye tracker and the cameras, not only checks the NI;
- outside that part, their data is left out and named;
- they are placed through the NI's own barcodes, over the sync box's own log.

54. **The NI's own barcodes are the barcode reference under the NI.**
    - While the sync box ran, the NI recorded its barcodes on its barcode line (bit 0 of digital word 0), at the
      NI's own samples.
    - Under the NI's clock, `Clock.barcode_reference()` returns those barcodes as value → NI seconds. They are
      the first NI file's, gap-clear, as `segments.scan_system` decodes them, on the identity map
      (amendment 50).
    - No fit is needed: the NI is session time.
    - With no barcode on that file, the reference is empty and nothing is placed by barcode, as in 3b-1.
55. **The covered span** is `[first, last]` of that reference, in session seconds. The span is a property of
    the session, stored once, recomputed from the files as the clock is. Outside it no device is placed by
    barcode.
56. **Each system carrying the barcodes is fitted by today's barcode path** against that reference:
    - the sync box, from its own `B` records;
    - the eye tracker (`ohdpi`);
    - the cameras (`bcam`).

    `SystemTimebase.time_source` is `'barcode'` and each file's offset comes from its own barcodes
    (`fit_rate_across`, `fit_offset`).
    - SpikeGLX stays on the identity map.
    - Intan stays fitted by its strobes (3b-2), which span its whole recording. Its barcodes are not used under
      the NI.
57. **A file whose barcodes all lie outside the covered span** is rejected as `outside_covered_span`, a new
    reason. Under the NI, a file rejected only for this reason does not fail the session (§6.4, as the requester
    chose). It is named in the NWB file and the listing.
    - This is a file that decoded barcodes, none of them in the NI's reference: for example a device that ran
      while the sync box did, but outside the NI's recording.
    - A system whose every file is outside the span is left out of the file and named, likewise. It is
      `unfittable`, but does not fail the session.
    - **A file that decoded no barcode at all** stays `no_barcode` and fails the session, as today. This
      includes every eye and camera file when the reference is empty, so such a session is D, as in 3b-1.
    - This is the requester's choice of 2026-11-20. A file that ran wholly after the box stopped and a file
      whose barcode cable was dead look the same, and only another way of placing them can tell them apart.
      That way is the next piece (amendment 65).
    - Every other rejection still fails it.
58. **Eye data is used and published only inside the covered span.**
    - The eye tracker's samples outside it are placed by extrapolating the fit, which nothing verified.
    - So no stage uses them: not calibration, detection, quality, the main sequence, or the NWB file's samples
      and events.
    - The NWB file names the stretch left out.
    - Cameras have no data in the NWB file. Their placement is only what keeps them from failing the session.
59. **The cross-check** (decision 8; §7's N, "with the sync box's codes over that part"). The sync box's code
    words, placed by its barcode fit, are compared by content with the NI's, with `agreement.code_agreement`.
    The comparison runs only where the box's log actually holds code words:
    - its session-time segments' extent, within the covered span;
    - less every window `SyncBoxSession.losses()` gives (its `L` records and its gaps).

    Both streams are cut to those stretches. A stretch the box did not record cannot disagree; amendment 60 keeps
    the box's losses from failing the session, and this keeps them out of its agreement too.
    - The box's own decode errors do not count. A payload cut at a stretch's edge decodes as an error, which is
      no disagreement. The tier's `decode_errors` stays the NI's.
    - Agreement below `AGREEMENT_THRESHOLD` (0.999) is tier D.
    - These are the builder check's two calls, settled as the designer's, 2026-11-20.
    - `TimingProvenance` keeps the agreement in `event_code_agreement` and counts two full-code records.
    - A session with no sync box, or none placed, is N on the NI alone, as in 3b-1.
60. **Under the NI, the sync box's own losses do not fail the timing check.** The NI's codes are the session's
    record, and the box's are a cross-check only. Its losses are reported, and `fails_the_timing_check` is not
    asked of them. This was 3b-1's carried item, which compared the box's losses in its own time against the
    NI's runs.
61. **`TimingProvenance` reads the sync box's codes as the session's only where the sync box is the clock.**
    `syncbox_fitted` also requires `clock.source == "syncbox"` (3b-1's carried item). Under the NI, the box's
    codes enter only through amendment 59.
62. **Rule 1 counts an end code only where it ends the log** (3b-1's carried item; its review's stray end code).
    - `SESSION_END`, or the last run's `RUN_END`, reaches the end only when it is the log's last decoded code.
    - `BLOCK_END` or `RUN_END` may follow it, but nothing else.
    - Otherwise the last trial line decides, as before.
    - `CHOICE_RULES` becomes `"3b-3"`, so every refusal is decided again.
63. **The NWB file under the NI** says which systems the barcodes placed, and the covered span:
    - `system_clocks.time_source` reads `barcode` for them;
    - the NI texts say barcodes place what they place;
    - the description gains nothing.

    The left-out stretch is named in the file's notes.
64. **Testing, on synthetic sessions.** The NI and the sync box ran with the eye tracker and a camera, and the
    box's log was cut at a time. Tests assert:
    - the barcode reference is the NI's;
    - the span;
    - the eye tracker's and the camera's fits;
    - tier N;
    - the NWB file's eye samples and events lie inside the span, with the rest named;
    - a camera file wholly after the cut is `outside_covered_span` and the session still N;
    - a sync box whose codes disagree with the NI's is D;
    - a stray end code no longer keeps a sync box that stopped inside the last run.
65. **Next, its own piece: the eye tracker placed by its analog copy on the NI.** This is the requester's
    suggestion, 2026-11-20, ordered right after 3b-3.
    - On recording days the eye tracker's analog output, gaze X/Y and pupil for both eyes, from the ACCESIO DAC,
      goes into the NI's analog inputs. See the parent spec §7.1's second sync path, and wl-sync's
      `docs/superpowers/specs/2026-08-13-syncbox-breakout-pcb-design.md` §3.2.
    - Matching the eye file's signal to that copy places the eye file on the NI's clock with no barcode, over
      its whole recording. That covers any NI session, with a partial sync box or none, and it closes amendment
      51 for the eye tracker.
    - The cameras have no analog copy. That piece also weighs placing a barcode-less camera file by its own
      clock time against when the box stopped.
    - Until it lands, amendment 57's `no_barcode` rule stands.

## Amendments, 2026-11-20, made while planning and building part 3b-3

Part 3b-3 builds amendments 54-64. The plan (`docs/superpowers/plans/2026-11-20-part-3b-3-partial-sync-box.md`)
made five decisions the spec was silent on (66). The build's rulings corrected or sharpened what 54-64 say
(67-72), and the build left notes for this spec (73-76). Where these differ from 54-64, these bind. The ledger,
`.superpowers/sdd/2026-11-20-part-3b-3-partial-sync-box/progress.md`, holds the full wording of every ruling.

66. **The plan's five decisions.**
    1. **The span is computed, and also recorded.** `Clock.covered_span()` computes it from the files, cached;
       `TimingProvenance` stores it as `covered_start_s` / `covered_end_s`, null outside this case.
    2. **An empty reference keeps 3b-1's behaviour.** If the NI decoded no barcode, every barcode system under the
       NI is `unfittable` with `time_source` `none`, its files `unfitted_system`, even where they decoded the box's.
    3. **The cross-check is containment, not agreement.** `agreement.code_containment(part, whole)`: the box's
       words found in order in the NI's, over the box's count, on `code_agreement`'s matcher. (see amendment 69)
    4. **The synthetic box can stop.** A recipe field `syncbox_stop_s`, None by default: no barcode from then on,
       and a log of the records before it with no trailer.
    5. **3b-1's `partial` session changes tier.** Its sync box is fitted through the NI's barcodes and
       cross-checked, so it is N where it was D; its fixture moves to `syncbox_stop_s`.
67. **Amendment 64's camera line is corrected** (ruling R3). It says a camera file wholly after the cut is
    `outside_covered_span` and the session still N. That contradicts amendment 57.
    - Such a file decodes no barcode, so it is `no_barcode`, and it fails the session.
    - The test of `outside_covered_span` is a camera file whose only barcode was sent before the NI's first sample
      (`test_a_file_whose_barcodes_precede_the_ni_is_outside_and_does_not_fail`). That session is N.
68. **Coverage stops at the span** (ruling R5). Under the NI, `RunCoverage` and `TrialCoverage` cut each extent of
    a system the NI's barcodes placed to the covered span (`schema/coverage.py::_extents`).
    - A run past the span reads the sync box, the eye tracker and the cameras as not covered there. That is true of
      everything that reads them.
    - SpikeGLX, and Intan on its strobes, keep their extents.
69. **Amendment 59's cut, as built** (rulings R8 and R9). Both streams are cut, as 59 says. Where this differs
    from decision 3 of amendment 66, it binds.
    - **The box's part** is cut in its own session time: from its own time of the smallest NI-reference value it
      also holds to that of the largest, both included (`schema/timebase.py::_box_cross_check`).
    - **Only path-W loss windows are cut from it:** its `W` `L` records and its gaps. A lost edge (path E) loses no
      code word, and cuts nothing.
    - On an honest box the window cut cannot change the value: a box loses words, but never records wrong ones.
      It is kept as amendment 59 says (ruling R6).
    - **The NI's stream** is cut to those two values' NI times, widened each side by a margin of 0.5 s
      (`CROSS_CHECK_MARGIN_S`).
    - **Where the box's timebase is not one-rate**, a session-time segment naming a timebase outside
      `ONE_RATE_TIMEBASES`, the margin also gains `SPLIT_CLOCK_PPM` (50) × 1e-6 × the box's last code tick, in
      seconds since its capture began. Both constants now live in `timebase/segments.py`.
    - Extra NI words cost containment nothing, so the margin only widens what can be matched.
    - **The residual.** A wrong word among the box's last words can still be matched against an NI word within the
      margin. The task review's probes bound this at about 3-4 tail words. On a 12 h split clock the margin grows
      by about 2 s, which leaves about one trial at the end unchecked.
70. **A box that stopped before every calibration window refuses calibration, and names the span** (ruling R10).
    Under a span, a window not wholly inside it is no calibration window.
    - Where windows existed and none lies inside the span, both eyes' `EyeCalibration` rows are `refused`, with
      the reason "N fixation epoch(s) named a target, but the sync box's coverage ended before any calibration
      window: none lies inside the covered span its barcodes place (a to b s)".
    - Not "no fixation epoch named a target position", which would point a reader at the task code for the sync
      box's fault.
71. **The NWB file's notes under a span** (rulings R11 and R12). Wherever `TimingProvenance.covered_start_s` is
    set, `nwb/gather.py::_span_notes` writes, in this order, each time to the millisecond:
    - **The span, and what the NI's barcodes placed over it:** "the sync box's covered span is a s to b s; the NI's
      own barcodes placed X over it". X is every system `fitted` with `time_source` `barcode`, or "no system". A
      system left out is not named.
    - **Each stretch left out, before the span and after it,** where the file has an eye tracker segment and the
      stretch is not empty: "eye data outside the sync box's covered span, a s to b s, is left out: no barcode
      placed it". The stretches run from the NI's first sample to the span's start, and from its end to the NI's
      last. The NI's first barcode comes after its first sample, so the stretch before is in nearly every such
      file (R11).
    - **Each `outside_covered_span` file:** "{system} file {path} is left out: none of its barcodes is one the NI
      recorded, so it lies outside the sync box's covered span, a s to b s".

    These sentences go in the description's `notes`, after the probe notes. No field is added, as amendment 63
    says. A file under the sync box has none.
72. **Eye validity outside the span: the report leaves it out** (the final review's I1). Under a span, `EyeValidity`
    stores every row outside it as `invalid`, with no criterion naming them. Each `frac_*` stays a fraction of all
    the recording's rows.
    - Left so, `cli/report.py::_unusable_fractions` counted the left-out stretch as unusable, and a partial-box
      session's daily report read it as a tracking problem, against its own span-cut `EyeQuality` lines.
    - **Fixed.** `EyeValidity` stores the span's rows, `span_row_start` and `span_row_stop` (exclusive), null where
      the span holds every row, as always under the sync box. `_detection_rows` clips each run to them in SQL, since
      a run outside can merge with an `invalid` run inside and cannot be split after the fact.
    - The per-eye unusable line counts the rows inside the span alone, and ends ", and K samples outside the sync
      box's covered span, not counted". The running total leaves them out too, and names their sum on a line of its
      own. Under the sync box both are as they were.
73. **wl-xcon's lab-time files** (XC-391 to XC-393; amendments 25 and 27). wl-xcon proposed these shapes, and the
    requester accepted them on 2026-11-20:
    - `xcon/words.jsonl`: rows `{process, n, word, at}`, and overflow rows `{process, lost, after_n}`;
    - `xcon/clock.jsonl`: rows `{kind, process, at, host, spread, sync}`, `kind` one of `open`, `pair` or `close`,
      and `sync` `{source, synchronized, offset, error, read_at}`; open rows also carry `opened`;
    - pairs are at least 60 s apart, and written between trials only;
    - `process` is counted from 0, and `n` is contiguous from 0;
    - decoding is split at each `lost`;
    - `synchronized` null means not synchronized;
    - `session_kind` marks a simulated session.

    They are read here once wl-xcon writes them.
74. **Amendment 27 is corrected.** It says a resumed process "writes no such row yet". wl-xcon at `243afb9`
    already writes one.
    - `Session.resume` writes a `session resumed` row to `welfare_notes.jsonl` (its `taskd.py`, line 778).
    - Its instant is the resume's, `wall_now()`, which also becomes that process's `opened_wall_at`.
    - The row has no `at` field. The instant is in `was` and `now`, both the same (its `record.py::welfare_note`),
      in the `session opened` row too.
75. **Amendment 62's residual** (Task 2's review, its P8 and P9). A lost escape whose phantom payload is cut off
    by the box's stop leaves a truncated decode error, which rule 1 skips.
    - So a stray end code before it stays the log's last decoded code, and a box that stopped inside the last run
      is kept as the clock.
    - The window is about 4 words. Outside it, amendment 64's last line holds.
76. **"Outside the span" is decided by range, not by membership** (Task 4's review; ruling R14, the final
    review). Under the NI, a file that decoded barcodes, none of them in the NI's reference, is
    `outside_covered_span` only where every value lies outside `[min, max]` of the reference's values: before its
    first or after its last. Such a file does not fail the session.
    - A file sharing none whose values lie between the reference's ends ran inside the span: the NI's decode has a
      hole under it, or its values were misdecoded. It is `no_shared_barcode`, and fails the session, as under the
      sync box (`timebase/segments.py::unshared_reason`).
    - This is built. The test is `holey_ni`: the NI's barcode line dead from 2.5 s to 6.5 s, and the camera's live
      over that stretch alone (`test_a_file_inside_a_hole_in_the_nis_barcodes_is_not_outside_and_fails`). That
      session is D. `before_ni`'s camera, whose one barcode precedes the NI's first, is still
      `outside_covered_span`, and its session N.

## Amendments, 2026-11-21: the eye tracker placed by its analog copy on the NI, designed

**Why.** Under the NI's clock, an eye-tracker file the NI's barcodes cannot place makes the session tier D
(amendments 51 and 57), and a partial box's eye data is used only inside its covered span (58). On recording days
the eye tracker's analog output reaches the NI's analog inputs (parent spec §7.1's second sync path; wl-sync's
`docs/superpowers/specs/2026-08-13-syncbox-breakout-pcb-design.md` §3.2: gaze X/Y and pupil of both eyes, from
the ACCESIO DAC, all sixteen analog sources reach the NI). Matching the eye file's gaze to that copy places the
whole file on the NI's clock with no barcode. This is amendment 65's eye half, "synced in a pinch" (51).

**What the requester chose, 2026-11-21:**
- **eye tracker only.** Cameras have no analog copy, and their files carry no wall-clock time today (the sidecar
  has none; a video file's modification time moves if anything touches it on the camera computer, which would
  leave a dead-cable file out without failing). A camera file with no barcode under the NI still fails the
  session (57). Telling "ran after the box stopped" from "dead cable" waits for the camera package (wl-camacq,
  registered, no repository yet) to write each file's start and end wall time. That is amendment 65's camera
  half, now carried (amendment 83).
- **slide the trace along its copy**, over the alternatives below.
- **publish with the lag labelled unmeasured** until a real session measures it.
- the behaviour below, played back and approved the same day.

**Approaches considered.**
1. **Line the trace up with its copy, stretch by stretch (chosen).** Over many stretches of the recording, the
   time shift at which the eye file's gaze best matches the NI channel gives a pair (eye time, NI time); the
   existing least-squares fit turns the pairs into a rate and an offset, as for barcodes and Intan's strobes. The
   DAC's unknown scale and offset do not matter, every saccade and blink contributes, and ambiguity is
   measurable.
2. **Find each frame's step on the NI.** The copy changes once per frame, so the NI sees a staircase. Finest in
   principle (an NI sample), but during fixation the steps are below the noise, and the board's buffering or any
   smoothing blurs them: it fails on most of a session.
3. **Match saccades and blinks as events** with the strobe matcher. Throws away most of the signal and needs its
   own detector on a raw voltage.

77. **When the copy is used.** Under the NI's clock (`SessionClock.source = 'nidq'`) only, for the eye tracker
    (`ohdpi`) only. Under the sync box's clock nothing changes. Intan's copy (through the mux) and the cameras
    are out of scope.
    - **No box, or a box with no barcode in the eye file:** the eye file is placed by its copy over its whole
      recording.
    - **A partial box (3b-3):** the eye file is placed by its copy over its whole recording too, and over the
      covered span its barcodes place it as well; the two measure the lag (79). This lifts amendment 58's cut for
      an eye file placed this way: its samples outside the span are placed by a fit the copy verified there, so
      every stage uses them.
    - **No copy found** (no NI analog channel carries it, as on a day without the board): 3b-3's behaviour
      stands, barcodes over the span where there are any, and amendment 57's `no_barcode` where there are none.
    - *Builder's call, from the builder check:* **"no copy" and "a copy that fails" are told apart by the
      distinct windows.** No copy: no channel gives the plan's minimum number of distinct windows (the NI
      recorded no analog channel, or only noise). A copy that fails: some channel does, but the fit is refused
      (78). A copy that fails, where barcodes place the file, falls back to 3b-3's placement over the span and is
      named in the file and the daily report; with no barcodes it is `no_analog_match` and fails. So this piece
      never turns a session 3b-3 publishes into D, except through the cross-check (79).
    - *Builder's call:* **the copy does not override amendment 76.** A file that is `no_shared_barcode` (inside a
      hole in the NI's decode) still fails: that hole is a fault in the NI's own barcode line, which the eye's
      copy cannot vouch for.
    - *Builder's call:* **per system, as `SystemTimebase` is.** The eye tracker has one file per session in
      practice: the NWB builder already refuses more than one ohDPI segment (`nwb/gather.py`, section 4.3). A
      session with more than one eye file is not placed by its copy; 3b-3's rules apply to it.
78. **The match.**
    - The eye file's own time is its frames' (frame number and the camera's seconds, as `eye/ohdpi.py` reads
      them). The NI's is its analog samples at `niSampRate`.
    - **Finding the channel.** Every non-multiplexed analog channel the NI recorded (the census's XA count) is
      tried against the eye file's gaze X and Y of each eye. No channel map is declared: wl-sync's documents pin
      only AI15 (the audio tap). For timing it does not matter which eye or which axis matched; the file records
      which pair did (80).
    - **The pairs.** Over windows across the recording, the shift that best matches the trace to the channel
      (normalised, so scale and offset drop out) gives one pair per window, refined below a frame. A window whose
      best shift is not clearly better than its next best (a fixation with no movement, a flat or clipped
      channel, a periodic stretch) gives no pair.
    - **The fit.** The pairs go through `fit.py::fit_pairs_across`, rate and offset as for barcodes. Its residual
      must stay within a fraction of a frame. A file with too few pairs, or whose pairs do not fit, is refused.
    - *Builder's call:* **pairs off the line are dropped, up to a limit.** The copy comes through a USB DAC driven
      by software, whose delay can jump during a stall. Pairs further from the fitted line than the residual
      bound are dropped and the line refitted, up to a fraction of all pairs the plan names. Beyond it the file
      is refused. The dropped stretches are named in the file, because the eye's time there is the line's, not
      a measured one.
    - **What is compared with the copy.** The eye file holds no gaze: gaze is computed from it through a
      calibration that itself needs session time. So the match reads the file's own columns, and tries each of
      the candidates for what the DAC puts out (78a), taking the best. The synthetic copy is generated from a
      different, mixed and mildly nonlinear map of those columns, never the column the matcher reads, so that a
      passing test is not a tautology.
    - **Refusal.** The eye file is rejected with the new reason `no_analog_match`, which fails the session, as an
      unplaced device does (51). A refusal names why (no candidate channel, too few distinct windows, residual).
    - The plan fixes the window length and step, the distinctness ratio, the minimum number of pairs and the
      residual bound, from synthetic data, each a named constant. Real data revisits them (`docs/hardware-checks.md`).
78a. **What the DAC puts out is not known** (the requester, 2026-11-21: "not sure; try them all"). wl-sync's
    breakout design says only "Eye X/Y, both eyes" and pupil, from the ACCESIO DAC driven by the eye-tracking
    software. So the matcher tries each likely source against each NI input: the DPI's P1-P4 difference vector
    (X and Y), P1 alone (X and Y), and the pupil, each eye, and takes the best. The file records which matched. A
    hardware check confirms what the output carries, and the search narrows to it then.
79. **The lag.** The copy trails each frame by the eye tracker's processing and the DAC's update. The instrument's
    paper gives its parts (84): about 1.1 ms of processing typically and 3-4 ms from the analog output, plus a
    fixed filter delay before the NI digitises it. Its true size is unknown until measured.
    - **Measured** wherever one eye file is placed both by barcodes (over the covered span) and by its copy: the
      mean difference of the two placements over the span. It is recorded per session, with the rig and the
      session it came from.
    - **Applied:** the copy's placement is corrected by the lag. A session that measured it uses its own. One that
      did not uses the latest lag measured on the same rig (the manifest's `rig`) at or before its session time,
      and names that session. With none, the lag is 0 and **unmeasured**.
    - **Kept as placed.** A later measurement does not move a session already placed, as a session keeps the
      clock it was timed by (amendment 28). Re-placing is deleting its rows, as for any clock.
    - **The cross-check.** After the lag is removed, the two placements must agree over the span within one frame
      period (2 ms at 500 Hz). If not, the session is tier D: barcodes and copy disagree about the eye's time.
      That catches a lag that varies across the span, not a wrong constant, which the mean absorbs. So:
    - *Builder's call:* **the lag is bounded.** A measured lag must be at least 0 (the copy cannot lead its
      frame) and at most a ceiling the plan names, far above any output latency and far below the spacing of
      saccades. Outside it the session is tier D.
    - *Builder's call:* **only a good session's lag is carried.** A lag counts for later sessions only from a
      session whose eye placement passed and whose tier is not D. A lag that differs from the rig's previous one
      by more than a frame is named in the daily report.
80. **What is recorded and published.**
    - `SystemTimebase.time_source` gains `analog` (the enum `_TIME_SOURCE_ENUM`); such a fit is `fitted`, so the
      session can be tier N.
    - Which NI channel and which eye signal matched, the number of pairs, the residual, the lag used, whether it was
      measured, and its source session, are stored with the fit.
    - The NWB file's `processing/timebase` says the eye tracker was placed by its analog copy on NI input `XA<k>`,
      the lag used, and either the session it was measured in or that it is unmeasured, with the error that
      implies stated as unknown, of the order of the eye tracker's output latency. `system_clocks.time_source` reads
      `analog` with that text.
    - The listing gives a rejected eye file's `reason` as `no_analog_match`, a plain string, as wl.works already
      reads it. wl.works is told at the merge.
81. **The tests.** Synthetic, like all of part 3b: the breakout board is not built, so no recording with the copy
    exists. The generator writes the copy onto NI analog channels from the eye trace, with a lag, a scale and
    offset, noise and smoothing, on an NI channel the test does not name. Sessions: no box with the copy; a
    partial box with the copy (lag measured, whole file placed); a second session on that rig with no box (lag
    carried, its source named); no copy (3b-3 unchanged); a copy on a channel that is not the eye's, a flat
    channel, and a copy with a dropped stretch (refused or placed, each by its rule); barcodes and copy that
    disagree by more than a frame (tier D).
82. **What waits on hardware** goes to `docs/hardware-checks.md`: the lag on a real session with both the box and
    the copy; which NI inputs carry the eye; what the DAC actually puts out (78a); the plan's constants against
    real traces; the board's buffering and any smoothing on the eye channels.
83. **Carried: the camera half of amendment 65.** The ask to the camera package (wl-camacq): write each video
    file's start and end on the camera computer's wall clock, with its synchronisation state, in the sidecar.
    The same ask corrects our sidecar contract (`contracts/sidecar.py`), which still requires `trigger_source`
    `syncbox`: wl-sync voided the camera triggers on 2026-08-16, and the cameras free-run and record the barcode
    line (wl-sync `docs/superpowers/plans/2026-08-13-sync-box.md:1158`).
84. **This supersedes the 1c-4 timebase design's §2.1 for this use only** (the requester, 2026-11-21, told of the
    conflict). §2.1 rejected aligning the eye tracker through its analog output, on the instrument's paper
    (10.1016/j.jneumeth.2026.110693): frame processing "1.1±0.1 ms (median ± IQR)" but "up to a maximum of 50 ms",
    "2% of frames ≥ 10 ms", and the analog output "imposed an additional 3-4 ms delay". It concluded "there is no
    single offset to recover". That holds for the copy's frames one by one, and less for what is built here:
    - **what is placed is the eye file's own frame times,** stamped at capture. A frame slow to process is late
      only in the copy, not in the file.
    - **the copy only fits a rate and an offset** through many windows (78). A late frame is noise in one window,
      a burst of them is a window off the line, dropped (78), and what remains is the typical delay, the lag (79),
      which the box measures where it ran.
    - §2.1's other objection, that it needs the NI and so cannot serve an Intan-only rig, does not apply to a
      fallback under the NI's clock alone.
    - **Its cost:** until the lag is measured on a real session, eye data placed this way is off by about the
      paper's typical delays (roughly 4-5 ms) plus the filter's, where barcodes place it within one 2 ms frame.
      The file says so (80).
    - Barcodes stay the eye tracker's sync path wherever they exist (77); §2.1 stands for every other use.
