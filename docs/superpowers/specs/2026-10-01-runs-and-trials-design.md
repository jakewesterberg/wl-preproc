# Runs and trials from a real wl-xcon session

**The first of four pieces that make a wl-xcon recording usable here.** The other three come
later, each with its own spec: the landed-session list (`GET /sessions?since=`), per-probe block
lists in a canonical request, and a metadata-only rebuild of a published file.

**Status.**
- Approved in conversation on 2026-10-01, section by section. The requester's decisions are in
  §0.
- **Why now.** Probes go into the brain in January, and wl.works fires each recording's
  canonical NWB the next morning (its `docs/superpowers/specs/2026-09-30-january-canonical-nwb-design.md`,
  §0 rulings 1 and 2, read on its main at `c8dc0613`). Today a real wl-xcon recording gives
  wl-preproc **no measured blocks**, because wl-xcon sends no `BLOCK_START`, and **no joined
  trials**, because nothing here reads XC-155's `trial_number`.
- **Asks it answers.** wl.works' ask 2 (measure runs, numbered in session order; its §12) and
  wl-xcon's message of 2026-10-01: XC-155's join field, with its questions 2–4 and two warnings.

**The rig and compute machine are still unavailable** (the requester, 2026-09-30). Nothing here
needs them.

---

## 0. The requester's decisions, 2026-10-01

1. **Split the work, runs and trials first.** Then the session list with per-probe block lists,
   then the metadata-only rebuild. Each later piece reads the runs this one measures.
2. **wl-xcon sends our `BLOCK_START` at each run's start, and `BLOCK_END` when a run ends by
   design.** The recording then says which run it is, even when the rig's files are missing,
   and one lost code costs one run rather than renumbering the rest.

   Declined: reading wl-xcon's bare `RUN_START`/`RUN_END` (4135/4136) and identifying each run
   through the trials inside it; and doing that first, then this.
3. **One sync-box recording holds one animal.** Each animal gets its own sync-box session, as
   wl-xcon's XC-088 asks wl-sync to make happen when the subject changes. So trial numbers
   start at 1 per recording.
4. **A wl-xcon crash is corrected at its source**: wl-xcon's XC-026, *"Resume a session after a
   `taskd` crash, carrying block and trial indices forward from the record"* (its
   `docs/backlog.md`, which says it waits on nothing). A recording then never repeats a number,
   and every trial keeps the number the rig strobed. **wl-preproc reports any repeat that still
   appears, and never drops one silently.**

   Declined: wl-preproc renumbering the trials after a detected restart, alone or alongside
   XC-026.

**A run is a block.** wl.works' own words: *"What wl-xcon calls a "run" is what you and the ELN
call a "block"."* This spec uses *run* for wl-xcon's thing and *block* for the table row it
becomes.

## 1. What it is, and what it is not

**It is:**
- runs measured as blocks from `BLOCK_START`/`BLOCK_END`, including a run that never closed (§2);
- trials joined to the rig's record by `trial_number` (§3.1);
- what wl-preproc does with a repeated number, a trial with no outcome, a number too large to
  store, and an escape cut by a crash (§3.2–§3.5);
- the asks back to wl-xcon, and the answer to wl.works' ask 2 (§4).

**It is not:**
- **The landed-session list, or per-probe block lists.** Pieces 2 and 3.
- **The task's name.** It stays in wl-xcon's `runs.jsonl`, joined by run number. Piece 2's listing
  reads it, and wl.works sends it back in every request's `metadata.blocks`.
- **Allocating task codes** for wl-xcon's tasks. `TaskTypeCode` 100+ is wl-xtasks'
  (`contracts/events.py`, ADR-0007).
- **The rig's finer outcomes** (`no_fixation` where the stream says `abort`). The file keeps the
  stream's outcome; adding the rig's is a later choice.
- **A change to the decoder.** Framing is a frozen interface (§3.5).

## 2. Runs

### 2.1 What wl-xcon sends

At each run's start, **our existing `BLOCK_START` escape** (`0x8002`, two payload words:
`contracts/events.py`'s `(block_number, task_type_code)`):
- **word 1: the run number, counting from 1 across the session.** That is element-event's own
  numbering, `block_id : smallint # block number (1-based indexing)`, and wl.works' *"numbered in
  session order"*. wl-xcon counts its runs from 0 (`wl_xcon/taskd.py`: `self.run_index = 0 if
  self.run_index is None else self.run_index + 1`), so the run number is its `run` plus one, and
  that is the join to `runs.jsonl`.
- **word 2: the task's code, or 0** until wl-xtasks allocates codes for wl-xcon's tasks.

When a run **ends by design**, `BLOCK_END` (marker 3). That is the rule wl-xcon already applies to
its `RUN_END`: *"`RUN_END` is strobed for a run that ended by design, not after a fault"* (its
`tasks/allocation.py`). `RUN_START` and `RUN_END` themselves may stay. They are stored as ordinary
events (`CODE_4135`, `CODE_4136`) and read by nothing here.

**Nothing on this side needs to change to read them.** `events/assemble.py` already turns
`BLOCK_START`/`BLOCK_END` into measured blocks, and `schema/events.py` stores them in
element-event's `trial.Block`. wl.works' block id is then the run number, so
`events/agreement.py`'s 2 ms block agreement compares wl-preproc's measurement with wl.works'
copy of it, as wl.works' §2.2 intends.

### 2.2 A run that never closed

A run that faults sends no `BLOCK_END`. **Today it swallows every later run's trials**:
`assemble()` keeps it open with `end_s = None`, and `schema/events.py::_containing_block` then
treats it as running to the end of the stream. It returns the first block that holds a trial's
start, so every later trial lands in it. wl-xcon reproduced this with `BLOCK_START` standing in
for `RUN_START`.

**From now on:**
- **For deciding which run a trial belongs to,** an unclosed run ends where the next run starts,
  or at the end of the stream if it is the last.
- **Its recorded stop** (`block_stop_time`, which is not nullable) is its **last event**: the
  last code strobed before the next run starts. The recording does not say when the run
  stopped, and its last event is the tightest bound it does give.
- A run that closed keeps its `BLOCK_END` time, as now.

## 3. Trials

### 3.1 Joined to the rig's record by `trial_number`

XC-155 is built (wl-xcon's main `eeec053`). **Every line of `xcon/trials.jsonl` carries
`trial_number`**: an integer counted from 1 across the whole wl-xcon session, equal to the
`TRIAL_NUMBER` (`0x8001`) payload strobed after that trial's `TRIAL_START`. Each line still
carries its `run` and a per-run `index`, which restarts at 0.

`events/rigtrials.py` **keys each line by `trial_number`**:
- A line with `trial_number` is joined by it, whether or not it names a run.
- **A record from before XC-155**, whose lines name no run, is still joined by `index`, as today.
- A line that names a run but carries no `trial_number` stays unjoined, and the record says why,
  as today.
- A number that two lines carry joins neither, as `nwb/conditions.py` already does for an index.

**What the join meets** (wl-xcon's list):
- **A trial that faults or is interrupted** has a `TRIAL_START`, its number and its codes, but no
  `TRIAL_END` and no line. It is stored from the stream, and its condition is unknown. The file
  says so in its existing condition notes.
- **A closed trial with no line** (a fault between `TRIAL_END` and the write) is the same.
- **A hang** strobes no outcome marker, then `TRIAL_END`: it is stored with no outcome and its
  recorded end.

### 3.2 A repeated number

With one animal per recording (decision 3) and XC-026 (decision 4), a recording should never
repeat a trial number. **If one still does, the first trial with that number is stored, and every
repeat is named** in the file's notes and its description, for example: *"trial number 7
appears 2 times in the recording; only the first is stored"*.
- The repeats are recoverable without a new table. Every `TRIAL_NUMBER` payload is already
  stored as a `pipeline.event.Event` row with a `trial_id` attribute; a number on more than one
  such row was repeated.
- **Nothing is renumbered.** A trial's id is the number the rig strobed, or the trial is not
  stored.

### 3.3 A trial with no outcome

**It stores.** Measured on 2026-10-01: a `trial.Trial` row with `trial_type` null inserts, since
element-event declares `-> [nullable] TrialType`. `populate_session` inserts `TrialType` rows only
for outcomes that exist, so nothing else is needed. No test of ours covers it yet, and one is
added (§7).

**Its inferred stop never reaches past its own run** (wl-xcon's warning about
`_trial_stop_time`). A trial with no `TRIAL_END` has its stop inferred:
`schema/events.py::_trial_stop_time` prefers its block's end when the block closed, and otherwise
the next trial's start. In a run that faulted, the next trial is in the next run, after the gap
between runs. **From now on, the inferred stop is capped at the containing run's stop, whether
that run closed or not** (an unclosed run's stop is its last event, §2.2).

### 3.4 A number too large to store

element-event's `trial_id` is a **smallint** (`trial_id : smallint # trial number (1-based
indexing)`), and the stream's number is a uint32. **Measured on 2026-10-01:** inserting
`trial_id` 40000 fails with MySQL's *"Out of range value for column 'trial_id'"* (1264). That
fails `populate_session`'s whole batch, so the session's event stage would fail on every pass.

**From now on, the trials that fit are stored, and those that don't are left out and counted** in
the file's notes. A session would need more than 32,767 trials to reach it.

### 3.5 An escape cut by a crash

A crash in the roughly 2 ms between a `TRIAL_NUMBER` escape's four words cuts it short.
`decode_stream` then reads the next words as its payload, logs a checksum or truncation
`DecodeError`, and loses that trial, and sometimes the next (wl-xcon's probe). **The decoder is not
changed.** Framing is a frozen interface. Resynchronising after a checksum failure would read a
genuine payload's words as codes: a trial number's low word is often a marker's value.

**What it costs:** the `DecodeError` drops the session to timing tier D
(`events/agreement.py::resolve_tier`), so that session has no canonical. **It is rare**: a crash
lands inside an escape about 2 ms in every few seconds of trial, so around 0.07% of crashes.
Recorded as open beside wl-xcon's XC-199, whose job is to close it at the source or say it is
accepted.

## 4. The asks back, and the answer to wl.works

**A new OPEN entry in `docs/pending-wl-xcon-amendments.md`:**
1. **`BLOCK_START` at each run's start, and `BLOCK_END` when it ends by design** (§2.1): run
   number from 1, task code or 0.
2. **XC-026 before January** (§0, decision 4), so a crash never repeats a trial or run number.

It also answers wl-xcon's questions:
- **2 (two wl-xcon sessions in one sync-box recording):** no. One recording holds one animal, and
  wl-sync should start a new session when the subject changes (XC-088).
- **3 (does a trial with no outcome store):** yes, measured; and its inferred stop no longer
  crosses into the next run.
- **4 (above 32,767):** MySQL refuses it. From now on those trials are left out and counted, and
  the rest are stored.
- **The cut escape:** accepted as rare and recorded here; closing it is XC-199's.

**To wl.works,** a note in `docs/pending-wl-works-amendments.md`: **runs come from `BLOCK_START`,
not 4135/4136**. The intent of its ask 2, runs measured on the recording's clock and numbered in
session order, is met. Until wl-xcon sends `BLOCK_START`, a real recording has no measured
blocks, and wl.works' grid should treat that as *waiting*, not as an empty session.

## 5. Failure cases

| Case | What happens |
|---|---|
| A run that faults (no `BLOCK_END`) | Its trials stay in it. It ends where the next run starts, and its recorded stop is its last event (§2.2) |
| A `BLOCK_START` that does not decode | A `DecodeError`, so tier D, as for any. That run's trials belong to no block, or to the run before it if that run never closed. Later runs keep their numbers |
| A trial that faults | Stored with no outcome and an inferred stop capped at its run's stop; no line, so no condition (§3.1, §3.3) |
| A repeated trial number | The first is stored; every repeat is named in the file and its description (§3.2) |
| A trial number above 32,767 | Left out and counted; the rest are stored (§3.4) |
| A `TRIAL_NUMBER` cut by a crash | One or two trials lost, and tier D. Not changed here (§3.5) |
| A recording made before wl-xcon sends `BLOCK_START` | No measured blocks, as today; its trials are still stored and joined |
| A rig record from before XC-155 | Joined by `index`, as today |

## 6. What a plan must verify rather than assume

1. **Which of `assemble()`'s and `schema/events.py`'s existing tests pin today's behaviour of an
   unclosed block**, and so must change with §2.2, rather than break by accident.
2. **That `TRIAL_NUMBER` events are stored with a `trial_id` attribute for every occurrence**,
   repeats included, since `Event`'s key is `(event_type, event_start_time)`.
3. **Where the file's and the description's notes are assembled** (`nwb/gather.py`'s condition
   notes), so the repeat and ceiling notes reach both.
4. **`nwb/conditions.py`'s join**, which today keys on `index`: it must key on what
   `events/rigtrials.py` now returns.
5. **The synthetic generator's rig record** (`synth/peripherals.py::write_rig_trials`), which
   writes the pre-XC-155 shape today.

## 7. Testing

- **Fix the fixture:** the synthetic rig record gains XC-155's shape, a `run`, a per-run `index`
  and `trial_number`. One recipe has two runs.
- **Through the real codec** (`contracts/events.py`'s encoder and `decode_stream`, as
  `tests/schema/test_events.py` builds its streams), streams with:
  - a run that never closed, followed by another run: each run keeps its own trials, and the
    unclosed one's stop is its last event;
  - a faulted trial in an unclosed run: its stop does not reach the next run;
  - a hang: no outcome, its recorded end;
  - a repeated trial number: the first stored, the repeat named;
  - a trial number above 32,767: left out and counted, the rest stored.
- **`populate_session` against the database** for each of those, including a trial with no
  outcome, which no test covers today.
- **The join:** a post-XC-155 record joined by `trial_number`, a pre-XC-155 one by `index`, and a
  run-named line without `trial_number` left unjoined.
- **The NWB file and its description** carry the repeat and ceiling notes.
- **The full suite runs once, on both interpreters.**
