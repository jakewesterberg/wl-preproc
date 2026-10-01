# The landed-session listing, and canonical requests that name runs

**Pieces 2 and 3 of four that make a real wl-xcon recording usable here,** designed together.
Piece 1, runs and trials, is merged (`b0f8b52`, 2026-10-01; spec
`2026-10-01-runs-and-trials-design.md`). Piece 4, a metadata-only rebuild, comes after.

- **Why now.** wl.works polls landed sessions and fires each montage's canonical NWB (its
  `docs/superpowers/specs/2026-09-30-january-canonical-nwb-design.md`, read on its `main` at
  `3d0f4358`). Its build 20b-1 waits on this design. wl-xcon sends the run and block codes now
  that they are on our `main`.
- **Asks it answers.** wl.works' asks, as revised on 2026-10-01 under the requester's vocabulary
  (its `2026-10-01-block-run-vocabulary-design.md` §5, the same commit; filed in
  `pending-wl-works-amendments.md` under *"measured runs, and what a block is now"*):
  1. a landed-session listing, with each run's blocks under it;
  2. answered by piece 1: wl.works reads `core.Run`;
  3. per-probe run lists in a canonical request;
  4. a canonical request asserts runs, not blocks, checked against `core.Run`;
  5. the NWB description joins runs to wl.works' `animal_session_run`.
- **Carried from piece 1.** Naming a repeated run or block number is this listing's (its
  amendment 8). Its deferred minor M1, the block-start bound with runs on, is settled here (§5).
- **A refinement from wl.works, 2026-10-01 evening** (session `wl-works-f6`, on the requester's
  word; its `docs/superpowers/specs/2026-10-01-montage-plan-design.md` §4 and §6, read on its
  `main` at `6a57b1cc`): each segment's recorded site set should arrive in a form it can compare
  with a planned IMRO file, *"by electrode index (or the segment's raw `~imroTbl`)"*. It reads
  NP1015, NP1022, NP1030 and NP1032 IMRO files itself. §2.2 carries both forms.

---

## 0. The requester's decisions, 2026-10-01

1. **A canonical file keeps every run of its montage.** Behaviour, trials and eye data cover every
   run. Each probe's run list governs only that probe's sort, and the description says which
   runs each probe covers. A run marked bad on every probe stays in the file for its behaviour.
2. **A repeated run number lists its first run and flags the session.** Before wl-xcon's XC-026
   a crash restart reuses run numbers. The listing shows the first run with each number and
   flags *"run numbers repeat; the restarted runs wait for XC-026"*. The restarted runs cannot go
   into a canonical. *Cost, stated with the choice:* if XC-026 slips past January, a crash day's
   later runs cannot be published as canonical.
3. **A derivative selects whole runs,** the unit a canonical uses and a sort runs over. Selecting
   by block type is left until an analysis needs it.

He also approved the design's four sections as presented: the listing (§2), the request (§3), the
file and what is retired (§4, §5), and the failure cases, testing and plans (§6–§9).

---

## 1. What it is, and what it is not

**It is:**
- `GET /sessions?since=<cursor>`, beside `GET /nwb`, listing each landed session's runs, blocks,
  probes and flags;
- canonical and derivative requests that name runs, checked against the measured runs as they
  arrive;
- per-probe run lists, stored for the sorter;
- the NWB file and its description built from runs (description version 3).

**It is not:**
- **Sorting.** The per-probe lists are stored; the sorter that reads them comes with the compute
  machine.
- **A block-type selection** for derivatives (decision 3).
- **A "removed" entry** for a session deleted after it was listed (§6), nor a page size. `GET /nwb`
  left both open too.
- **A change to element-event's tables.** Its `trial.Block.Attribute` part is used as it is.

---

## 2. The landed-session listing

### 2.1 A change log, written by one stage of the daemon

**`GET /sessions?since=N` returns the current state of every session with a change after `N`,
and a new cursor.** It copies `GET /nwb`:
- **A new append-only table, `ingest.SessionChange`**, beside `ingest.Ingestion`: an
  auto-increment `change_seq`, the session, `changed_at`, and the `digest` of the entry it
  recorded.
- **One writer: a new daemon stage, the listing stage.** It runs after the link stage, whose
  `ephys.ProbeCensus` it reads, and before the NWB stages. For each session the event stage has
  done, it assembles the entry (§2.2), hashes it, and appends a change when the hash differs from
  the session's last one. So any later fact, a timing tier or a probe census, re-lists the session
  without the stage that produced it knowing about the listing.
- **It holds a named database lock**, as the NWB stages do (`nwb/lock.py`), so a second `wlpp`
  process cannot interleave appends. Two writers would leave holes in the cursor: a sequence
  number allocated first but committed last, which a reader can step past and never see.
- **The responder never writes it.** It reads the log and assembles the same entries live.
- **An entry is the current state, not the state at logging time.** A reader that sees a newer
  state than its cursor implies loses nothing: the next change lists the session again.

**The query string** is accepted on `/sessions` as it is on `/nwb`: `since` alone, one
non-negative integer, otherwise a 422.

### 2.2 One entry per session

- **Key:** `subject`, `session_datetime`; and **the rig's session name**, the last part of
  `ingest.Ingestion.session_dir` (§8 item 5).
- **Runs**, from `core.Run`, in run-number order:
  - `run_number`, the task code, and the task's name as wl-xcon recorded it (§2.3);
  - `start_s`, `end_s` on the recording's clock, and `closed`;
  - the rig's stop reason, `stopped_because` and `stop_kind` (§2.3);
  - **the recording segments the run spans**: for each SpikeGLX segment overlapping the run, its
    start and end and, for each probe it recorded:
    - the serial, part number and probe type;
    - **`imro_table`**: the segment's `~imroTbl`, verbatim from its `.meta`. wl.works compares a
      planned IMRO file with it using the one reader it has for both, so no numbering convention
      is shared across repositories;
    - **`electrodes`**: the active sites as this repository's census stores them, sorted indices
      in probeinterface's contact order for the part number, the saved channels only;
    - the site map's identity, `electrode_config_hash` and `n_electrodes`. Two runs with the same
      identity share a bank setting. A bank change needs a SpikeGLX restart, so it appears as a run
      spanning two segments with different identities.
- **Blocks under each run**, from the measured `trial.Block` whose start lies in the run:
  - the block's number in the session (its `BLOCK_START` payload) and in its run (its order there,
    from 1);
  - the block type's name (§2.3);
  - `start_s`, `end_s`, `closed`, and its trial count.
- **Probes:** every serial the recording names, from `ephys.ProbeCensus`.
- **Flags** (§2.4).

### 2.3 What the event stage keeps from wl-xcon's record

**Read once, at the event stage, and stored,** because the raw files are later compressed and
archived, and the listing never reads them.
- **`core.RunRecord`**, new, `-> core.Run`: `task`, `stopped_because`, `stop_kind`, each nullable.
  It is wl-xcon's record of the run, kept apart from the measured `core.Run`, as an assertion is
  kept apart from a measurement throughout this repository. It is read from `xcon/runs.jsonl`
  (wl-xcon's `record.py::run_row`, read on its `main` at `0d00a6c`): the start row's `task`, and
  the end row's `stopped_because` and `stop_kind`. A row is matched to its run by
  `run_in_session` when the start row carries it (wl-xcon's session-levels change), otherwise by
  `run` + 1, its 0-based index.
- **The raw `~imroTbl`**, as a new nullable `imro_table` on `ephys.ProbeCensus.Probe`, read with
  the serial and part number it already reads. The census reads each segment once, so a
  development database redeclares `ProbeCensus` (no real database exists yet).
- **The block type**, as a `trial.Block.Attribute` row `block_type`: the `block` field of
  `xcon/trials.jsonl`'s lines, joined by `trial_number` (piece 1's key) to the block's stored
  trials. It is set only when every joined line names one type.

### 2.4 Flags

Each flag is a code and a sentence, listed per session or per run:
- **`tier`**: the session's timing tier; D means quarantined, not auto-published. *Pending* until
  `TimingProvenance` is computed.
- **`rejected_segments`**: the count and reasons, from `RejectedSegment`.
- **`waiting_for_run_markers`**: the event stage is done and the recording has no measured run.
  The session is listed, with no runs. wl.works shows it as *waiting for the rig's run markers*.
- **`repeated_run_number`** (decision 2): a run number whose `RUN_START` appears more than once
  in `Event`; *"run numbers repeat; the restarted runs wait for XC-026"*.
- **`repeated_block_number`**: the same for blocks.
- **`run_without_block`**: a run with no measured block; it stopped before its first trial.
- **`bank_change_in_run`**: a run spanning segments whose site maps differ for one probe.
- **`block_type_unknown`**: no rig record, or trials of one block naming two types.
- **`task_unknown`**: no rig record of the run.

---

## 3. Requests that name runs

### 3.1 What wl.works sends

- **`metadata.runs`**: every run it holds for the session, typed (the old `metadata.blocks` was an
  untyped list): `run_number`, `start_s`, `end_s`, `works_run_id`. They are wl.works' copies of
  the listing's measured values, with its own id for each.
- **`selection.probe_runs`**, canonical only: for every probe in the montage, keyed by serial, the
  runs its sort covers. **Every probe's list is stated in full**, as wl.works' Plan 20 §1.2 rule
  has it (the record is the run set each probe resolved to, not the exclusions). An empty list
  leaves the probe out of sorting.
- **`selection.run_numbers`**, derivative only: the runs the file holds (decision 3).
- **`metadata.blocks` and `selection.block_ids` are refused** with a 422 naming their
  replacements. Nothing but this repository's tests sends them.

### 3.2 What a file holds, and the check on arrival

- **A canonical holds every measured run whose start lies in its montage's window** (decision 1).
  Each must be asserted in `metadata.runs`, and each probe's list must be a subset of them.
- **A derivative holds its named runs,** each measured and asserted.
- **Each asserted run is checked against `core.Run` when the request arrives,** start and end
  within the agreement tolerance (`events/agreement.py`'s, about 2 ms). Today's block check
  computes once, before any request exists, and so never runs in wl.works' flow (§5); this
  replaces it with one that does.
- **Refusals:**
  - **422, run mismatch**: a run not measured, its times off, or a measured run in the window
    not asserted. The reason names the run and says *rebuild the request from a fresh
    `GET /sessions`*. wl.works stops and shows it.
  - **409, conflict**: a run already recorded with one `works_run_id` arriving with another.
    wl.works stops on a 409. Today a differing `core.Block` is kept-first silently.
  - **422, not yet ingested**: the event stage has not measured the session's runs. wl.works
    retries this one.
- **A session with repeated run numbers** measures only the first of each, so a montage cannot
  include the restarted runs (decision 2).

### 3.3 What is stored

- **`core.RunAssertion`**, `-> core.Run`: `works_run_id`, and the asserted `start_s`, `end_s`.
  Recorded if absent; a different id is the 409 above.
- **`request.ActivationRun`**, `-> request.Activation`, `-> core.Run`: the file's runs.
- **`request.ActivationProbeRun`**, `-> request.Activation`, `probe_serial`, `-> core.Run`: each
  probe's sort runs, which the sorter will read.
- **A derivative's identity hash** uses its task type and sorted run numbers, as it used block ids.

---

## 4. The NWB file and its description

- **The file's runs** (§3.2) set its extent. Trials, task events and eye data are trimmed to the
  runs' measured intervals, in place of wl.works' asserted block windows.
- **`/intervals/runs`**, new: `run_number`, `works_run_id`, task code and name, `start_time`,
  `stop_time`, `closed`, and each recording system's coverage.
- **`/intervals/blocks`**: the measured blocks inside the file's runs: block number, run number,
  block type, `start_time`, `stop_time`, `closed`, trial counts.
- **Each trial keeps its block and gains its run.**
- **The description goes to `schema_version` 3,** since version 2 allows only additions and this
  renames and removes:
  - `runs` replaces `blocks`. Each run carries `works_run_id`, so wl.works joins it to
    `animal_session_run`; its task, interval, `closed` and coverage; its blocks; and its
    conditions.
  - Each probe entry gains `sorted_runs`, its list from the request.
  - The trial notes and probe notes carry over.
- **A probe's two site maps within one file are still refused** (`nwb/gather.py::_probes`).
  Montages split at bank changes, which wl.works already does.

---

## 5. What is retired

- **`core.Block` and `request.ActivationBlock`**, replaced by `core.RunAssertion` and
  `request.ActivationRun`.
- **`TimingProvenance.block_agreement`** and `TierInputs.block_agreement`. **Found while
  designing:** `TimingProvenance` is computed once per session, before wl.works sends any request,
  and nothing recomputes it when `accept()` later writes `core.Block`. So the check wl.works keeps
  for catching a stale request never ran in its flow. §3.2's check on arrival does what it was
  kept for. A development database needs `TimingProvenance` redeclared, as piece 2b needed its NWB
  table; no real database exists yet.
- **`BlockCoverage`** becomes **`RunCoverage`**, keyed on `core.Run`; the file's readiness waits on
  it.
- **Piece 1's deferred minor M1** (`_BLOCK_START_MAX_SLOTS` broken with runs on) goes with
  `block_agreement`: it bounded an asserted block against a measured one, and wl.works now sends
  back our own measured times.

---

## 6. Failure cases

| Input | What happens |
|---|---|
| Landed, event stage not done | Not listed yet; a request gets 422 *not yet ingested* |
| No measured run | Listed with no runs and `waiting_for_run_markers` |
| A crash restart before XC-026 | First of each number listed and measured; `repeated_run_number` (and `repeated_block_number`); the restarted runs cannot be requested |
| `runs.jsonl` or `trials.jsonl` missing or unreadable | `task` or block type empty, with `task_unknown` or `block_type_unknown`; nothing guessed |
| A bank change inside a run | `bank_change_in_run`; the run spans two segments |
| A request from a stale listing | 422 run mismatch, naming the run |
| A run re-asserted with a new `works_run_id` | 409 |
| The old block fields | 422, naming `metadata.runs` and `selection.run_numbers` |
| A session deleted after listing | wl.works' copy goes stale; its next request fails the run check. A "removed" entry is left open |
| Two `wlpp` processes | The second skips the listing stage under its lock |

---

## 7. What goes back to the other repositories

- **wl.works**, for 20b-1 and its vendored schemas (vendor each after its merge, not from the
  branch): the listing's shape and flags, with each segment's `imro_table` and `electrodes` for
  its montage-plan comparison; `metadata.runs`, `works_run_id`,
  `selection.probe_runs`, `selection.run_numbers`; what 422 and 409 mean here; description
  version 3; and that the 2 ms check now runs on arrival, because the old one never ran in its
  flow.
- **wl-xcon**: nothing is asked. It is told that this repository now reads `runs.jsonl`'s `task`,
  `stopped_because` and `stop_kind`, and `trials.jsonl`'s `block`, so that it says so before
  renaming them.

---

## 8. What a plan must verify rather than assume

1. **That `RUN_START`'s run number is `runs.jsonl`'s `run` + 1** where `run_in_session` is
   absent, read in wl-xcon's own code.
2. **`trial.Block.Attribute`'s definition** takes a `block_type` row as `populate_session`
   already writes others.
3. **Every reader of `core.Block`, `ActivationBlock`, `BlockCoverage` and `block_agreement`**:
   coverage, `gather.readiness`, `wlpp delete`'s cascade map, `wlpp report`, the CLI, tests.
4. **How `BlockCoverage.make` decides coverage**, so `RunCoverage` keeps its meaning.
5. **That the rig's session name is the last part of `Ingestion.session_dir`**, in wl-xcon's
   `YYYY-MM-DD_NN` form, for every layout a landed session can have.
6. **That a second named lock does not deadlock** with the NWB stages' lock in one pass.
7. **How the description's `blocks` are read today** (`GET /nwb`, tests), before version 3
   replaces them.
8. **Whether probeinterface's contact order is SpikeGLX's electrode number** for NP1015, NP1022,
   NP1030 and NP1032, so the listing's documentation says truthfully what `electrodes` means. A
   file saving fewer channels than its `~imroTbl` lists has fewer `electrodes` than its table.

---

## 9. Testing

- **The generator with `runs=True`**, plus a crashed run, a repeated run number, a run with no
  block, a bank change, and a rig record with a mixed block type.
- **Through the database:** the listing's entries and flags; the change log appending only on a
  changed digest; every refusal of §3.2; the stored run and probe-run sets; the NWB file and its
  version-3 description.
- **Through the real HTTP server:** `GET /sessions`, its cursor and its 422s.
- **The schemas exported and diffed**, as CI does.
- **The full suite once per branch, on both interpreters.**

---

## 10. Two plans

- **Plan A, the listing:** `core.RunRecord` and the block type at the event stage; the change log
  and the listing stage; `GET /sessions`, its contract and its documentation. wl.works can poll
  once it merges.
- **Plan B, runs in requests and files:** the request contract and its checks; `core.RunAssertion`,
  `ActivationRun`, `ActivationProbeRun`; `RunCoverage`; the NWB file and description version 3;
  the retirements.

Each is its own branch, merged on the requester's word.

---

## Amendments, 2026-10-01, made while proving Plan A

Plan A (`plans/2026-10-01-session-listing.md`) was proven in code before it was written. These
settle what the sections above left open; §0 stands.

1. **Each SpikeGLX segment is listed once per session,** and each run names the barcodes of the
   segments it spans (§2.2 listed them under each run). The information is the same, without
   repeating a segment's sites under every run it touches.
2. **`tier` and `rejected_segments` are fields, not flags** (§2.4): every entry has them, and a
   flag is a finding that may or may not be there. `tier` is null until `TimingProvenance` is
   computed.
3. **A new flag, `block_outside_runs`**: a block in no measured run, when the session has runs.
   Its run's `RUN_START` was lost, or it was strobed outside a run. With no runs, the session is
   already `waiting_for_run_markers`, and the flag is not raised.
4. **`electrodes` are SpikeGLX electrode numbers** for NP1015, NP1022, NP1030 and NP1032
   (§8 item 8, measured with probeinterface 0.3.2: channel 0 in bank 2 is electrode 768, bank x
   384 + channel). Only the saved channels are listed.
5. **A block's closure is stored,** as a `trial.Block.Attribute` row `closed`, beside its
   `block_type`: `trial.Block` held no closure, and the listing reports it.
6. **A rig-record value longer than its column is cut to it,** and an `~imroTbl` longer than
   10,240 characters is not kept and is named as the census probe's problem. Either, kept whole,
   would fail its stage on every pass.
7. **`runs.jsonl`'s rows carry no subject,** so its `config.json` is read: a record naming another
   subject is not read. The generator writes both files only when its blocks are wrapped in runs.
8. **The listing stage's changes count into the pass's `populated`,** as the link stage's do, and
   its lock is `nwb/lock.py::exclusive(prefix, "listing")`, beside the NWB stages' lock.
9. **§8's items for Plan A, answered:**
   - **1:** `RUN_START`'s run number is `run` + 1: wl-xcon's session-levels spec calls `run` the
     0-based run in session, and its start rows gain `run_in_session`, which is used when present;
   - **2:** `trial.Block.Attribute` takes `block_type` and `closed` (`attribute_value` is
     `varchar(2000)`);
   - **5:** the rig's session name is the last part of `Ingestion.session_dir`; a generated
     session's is its `session_id`;
   - **6:** both named locks are taken with `GET_LOCK(name, 0)` and never waited for, so one
     pass holding both cannot deadlock;
   - **8:** as amendment 4.

