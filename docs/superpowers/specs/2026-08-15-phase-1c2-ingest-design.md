# Phase 1c-2 — The ingest watcher and the daily report: design

**Written 2026-08-15.** Second of four sub-projects decomposed from Phase 1c. It turns a
directory of files that finished landing on the server into rows the populate daemon can
compute from, and it makes the two ways that can go wrong visible instead of silent.

**Parent spec:** [`2026-08-12-wl-preproc-design.md`](2026-08-12-wl-preproc-design.md) §3.1 component 3, §4.6, §4.7, §5.4, §9.1, §10
**Predecessor:** [`2026-08-13-phase-1c1-schema-design.md`](2026-08-13-phase-1c1-schema-design.md) — the schema this writes into
**Sub-projects that follow:** 1c-3 responder and action list · 1c-4 timebase and coverage

---

## 1. What this builds, and where its edges are

§3.1 defines component 3 in one line: *"Detects session-complete, validates manifest,
discovers topology, inserts Manual-tier rows."* Three of §3.5's seven frozen interfaces are
its inputs — the session directory layout, the `session_manifest.yaml` schema, and the sync
box log format — which is why it is worth building before January rather than after: it is
the first consumer those contracts have ever had, and a contract with no consumer is a guess.

**In scope:** session-complete detection, transfer integrity verification, manifest
validation, topology discovery, the `Subject`/`Session`/`AcquisitionSystem`/`Ingestion` rows,
the quarantine record, the stalled-transfer alarm, `session_params.yaml` registration, and
the daily status report.

**Out of scope, with reasons:**

- **`Segment`, `RejectedSegment`, `Montage`, `Block`** — every one is a *measurement* that
  requires decoding the event stream or the barcode. The watcher reads directory structure and
  YAML; it decodes nothing. **1c-4** measures them.
- **`submit()`, activations, the 12-hour canonical trigger** — see §2, which is the reason.
- **Timebase fitting, coverage computation, tier derivation** — 1c-4. §4.7's tiers depend on
  barcode match rates and fit residuals that do not exist at ingest time.
- **The responder** — 1c-3.

---

## 2. The watcher never calls `submit()`, and that is the load-bearing decision

An earlier reading of this sub-project had the watcher end by submitting a request, because
§3.1 says it *"inserts Manual-tier rows"* and `Request.origin` has an `'ingest'` value
reserved. That reading ran straight into a wall: `Activation` has a hard foreign key to
`Montage`, montage boundaries are a measurement this component cannot take, and §11.1 forbids
fetching them from wl.works. The apparent conclusion was that 1c-2 was blocked on a cross-repo
question (§13 item 9, reopened 2026-08-15).

**It is not blocked, because two different things were being conflated.**

`submit()` creates an **`Activation`** — §8.3's selection of *what an NWB is computed over*.
That is an analysis-level judgment and it genuinely needs a montage.

But the first computations after ingest are **timebase and coverage**, and those are DataJoint
**Computed** tables keyed on `Session` and `AcquisitionSystem`. `populate()` computes a
Computed table for every parent key that lacks a result. No `Request` is involved, no
`Activation` is involved, and nothing needs a montage. Landing the parent rows *is* the
trigger — that is what a computed table means.

So the watcher's job ends at facts:

```
watcher lands:    Subject → Session → AcquisitionSystem → Ingestion
populate() then:  timebase, coverage, …                     (1c-4, automatic)
12h later:        canonical Activation over measured montages (needs 1c-4's output)
```

**`Request.origin='ingest'` therefore stays reserved and unused.** It is not deleted, because
a future ingest-triggered *activation* is plausible and the enum value costs nothing; but
nothing in 1c-2 writes it, and a test asserts that, so the day something does, it is a
deliberate change rather than a drift.

**This also isolates the item 9 exposure.** The block/montage authorship question is real and
still open, but it gates the **canonical activation**, not ingest. If wl.works rejects the
resolution proposed in §8.3.1's 2026-08-15 amendment, nothing in this sub-project changes.

---

## 3. Module structure

§3.4 already names the package and its contents — *"`ingest/` watcher, manifest validation,
device discovery, session-complete detection"* — so this follows it rather than inventing a
layout.

| Module | Responsibility |
|---|---|
| `ingest/sentinel.py` | Is this session finished landing? Reads `DONE` markers against `expected_systems`. Detects stalled transfers. |
| `ingest/verify.py` | Does what arrived match what was sent? Sizes and hashes from the `DONE` payload. |
| `ingest/discover.py` | What is actually on disk, versus what the manifest declared. |
| `ingest/landing.py` | The schema writes, and only these. The one module that imports `datajoint`. |
| `ingest/watcher.py` | `scan_once(root, prefix)` — the orchestrator, and the only public entry point. It examines **each immediate child directory of `root`** as one candidate session and does not recurse; a session directory's own structure is `SessionLayout`'s business, not the scanner's. |
| `schema/ingest.py` | New schema `wlpp_ingest`: `Ingestion`, `Quarantine`. |
| `cli/report.py` | The daily report. Reads; never writes. |

**`landing.py` is the only module that touches DataJoint**, so every other module is testable
without a database. That matters more here than it did in 1c-1: sentinel detection, hashing
and topology discovery are filesystem logic, and a MySQL container in the loop would make
their tests slow enough to discourage the number of cases they need.

---

## 4. Session-complete detection

### 4.1 The primitive already exists and is already shared

`SessionLayout.done_marker(system)` puts a `DONE` file in each *system's* directory, and
`contracts/paths.py` states the intended aggregate semantics in its own docstring:

> *"Written by a transfer when that system's files are complete. Session-complete detection
> waits for every expected system's marker, and wl.works' `nas_artifact_observation.complete`
> reads the same signal."*

Nothing has ever read a `DONE` marker back — the synthetic generator writes them and tests
assert they exist, and that is the whole of it. **This sub-project is the first reader**, and
it implements exactly the aggregate that docstring describes.

`session_manifest.yaml` already carries a required `expected_systems: list[str]`, constrained
to `SYSTEMS` and required to contain `syncbox`. So the rule is:

> **A session is complete when the manifest parses and every system named in its
> `expected_systems` has a `DONE` marker.**

Nothing else is consulted. A system present on disk but *not* declared is recorded by topology
discovery (§7) and does not affect completeness; a system declared but never marked keeps the
session incomplete, forever if necessary — which is what §4.3 exists to make visible.

### 4.2 Why a sentinel rather than quiescence

The alternative considered was declaring a session complete once nothing in the tree had
changed for N minutes. It was rejected because **a stalled transfer and a finished one are
indistinguishable under it**: both are quiet. Picking N trades one failure for the other —
short N ingests half-written sessions, long N delays every session to insure against a rare
one — and no value of N makes the two states distinguishable, because quiescence is not
evidence of completion.

The sentinel is evidence, because something that knew it had finished wrote it.

### 4.3 Quiescence survives as an alarm, which is the point

A directory holding a parsed manifest whose `expected_systems` are not all marked, and which
has not changed in `STALL_AFTER_S` (default 2 h), is a **stalled transfer**. It is:

- **not ingested** — it is not complete, and no timer makes it complete;
- **not quarantined** — nothing is known to be wrong with it;
- **reported**, under its own heading in the daily report, with the systems still missing.

This is the failure mode every other option leaves silent. A rig transfer that dies at 80%
produces a directory that looks exactly like one still transferring, and without the alarm a
weekend's recording simply never appears, with nothing anywhere saying so.

**The threshold is a reporting threshold, not a correctness one.** Setting it wrong makes the
report early or late; it can never cause an ingest.

---

## 5. Transfer integrity: `DONE` gains content

### 5.1 The change

`DONE` is currently an empty file whose **existence** is the completion signal. It stays that,
and gains a body:

```yaml
# <session>/<system>/DONE
schema_version: 1
system: spikeglx
transfer_finished_at: 2027-03-14T19:04:11Z
files:
  - path: 2027-03-14_01_imec0.ap.bin
    bytes: 384102400000
    blake3: 9f2c1a…
  - path: 2027-03-14_01_imec0.ap.meta
    bytes: 4211
    blake3: 04bb7e…
  - path: 2027-03-14_01.nidq.bin
    bytes: 216042000
    blake3: c17d90…
  - path: 2027-03-14_01.nidq.meta
    bytes: 312
    blake3: 5ae338…
```

> **Amended 2026-08-22 (Phase 1c-4).** The NI pair was added to this example
> when the generator gained it. One SpikeGLX run stops `imec0` and `nidq`
> together, so both streams are one system's transfer and one `DONE` covers
> them — and §4.5's barcode arrives on the NI digital line, so a marker listing
> only the imec pair declares a complete transfer of the stream nothing aligns
> on. The listing is illustrative either way: the marker is written by
> `rglob`, not from a fixed list.

Paths are relative to the system directory. **Existence keeps meaning exactly what it meant**,
so wl.works' `nas_artifact_observation.complete` is unaffected — it reads presence, and
presence is unchanged.

**This amends frozen interface #1** (session directory layout). Amending it now is the cheap
moment: the rig-side transfer scripts that must write this file do not exist yet, so the
contract is being written before its producer rather than after.

### 5.2 An empty `DONE` stays legal

A zero-byte `DONE` means *complete, no integrity data*. The session ingests, and the
`Ingestion` row records `integrity: 'declared_only'` rather than `'verified'`. This is not
generosity toward sloppy producers — it is what keeps the change backward-compatible with the
synthetic generator's existing output and with any early rig script, and it makes the
difference **visible in the record** rather than assumed.

### 5.3 What verification costs, since it is not free

Verification re-hashes every listed file on the server and compares against the declared size
and hash. **Measured 2026-08-15 on this machine**: `blake3` 1.0.9 at **1.83 GB/s**
single-threaded against stdlib `blake2b`'s 1.15 GB/s, so a 360 GB dual-probe session costs
about **3.3 minutes** — once, at ingest. In practice the NVMe read is the real floor, so this
is roughly disk-bound rather than CPU-bound.

`blake3` becomes a dependency for this. It is not a free choice made for speed: §4.6's
behaviour-camera sidecar — a frozen interface — already specifies `checksum: <blake3>`, so the
project committed to the algorithm before this sub-project existed, and hashing the same data
two different ways in one pipeline would be worse than adding the wheel. Wheels are published
for cp311 and cp313, which is what CI runs.

That is worth paying, and worth stating why: rsync verifies *in flight*, not *at rest*. A file
that transferred correctly and then met a bad block on the scratch array passes every check
the transfer tool makes. Re-hashing at the destination is the only step that catches it, and
ingest is the last moment where catching it is cheap.

**Configurable, default on.** `--no-verify` exists for a rebuild against known-good data, and
sets `integrity: 'skipped'` so the record never claims a check that did not run.

### 5.4 A mismatch quarantines

A file listed in `DONE` that is absent, the wrong size, or the wrong hash quarantines the
session with reason `checksum_mismatch` and the offending paths in the detail. This is the one
place the watcher renders a verdict, and it is a verdict about **transfer**, not about
science — the distinction §7 depends on.

---

## 6. Manifest validation

`SessionManifest` is a pydantic model with `extra="forbid"` and `frozen=True`, and it already
rejects unknown keys, naive datetimes, unknown systems, a missing `syncbox`, and a malformed
`session_id`. The watcher adds two checks the model cannot make:

**`schema_version` is enforced.** `SCHEMA_VERSION = 1` is declared in `contracts/manifest.py`
and the field is required, but **nothing has ever compared them** — a manifest declaring
version 7 parses cleanly today. The watcher rejects a manifest whose `schema_version` it does
not implement, with reason `manifest_schema_version`. This is the check that lets the contract
be versioned at all; without it the version field is decoration.

**`session_id` agrees with the directory name.** `SessionLayout` derives the directory from the
`SessionId`, so a manifest whose `session_id` names a different directory than the one it sits
in is incoherent, and silently trusting either one produces a session filed under the wrong
identity. Reason: `session_id_mismatch`.

An unparseable manifest quarantines with reason `manifest_invalid`, and this is the case §9
exists for: it is precisely the failure that leaves no derivable primary key.

---

## 7. Topology discovery, which never renders a verdict

`discover_topology(layout)` returns, for each of `SYSTEMS`:

| State | Meaning |
|---|---|
| `present` | Declared in `expected_systems`, directory exists, `DONE` present |
| `absent` | Not declared, no directory — the ordinary case for a rig that lacks the device |
| `undeclared` | Directory exists with content, but the manifest never declared it |
| `pending` | Declared, but no `DONE` yet — the state §4.3 alarms on |

**Only `pending` blocks ingest**, and it blocks it by meaning "not complete yet", not by
judgment. In particular **`absent` never blocks and never quarantines.** A session with no
behaviour camera and no eye tracker ingests exactly like one that has them; the absence
surfaces later as `absent` coverage (§4.6) and as a ceiling on the achievable timing tier
(§4.7). This follows §1.1's principle that the pipeline *"renders no verdicts of its own"*,
and it matches wl.works' own governing rule for every dispatch domain — **"silence is
`unknown`, never `failed`"** — which the corpus states in Plan 10 §1.3 and restates in
Plan 20 §4.5.

**`undeclared` is recorded and reported, not treated as an error.** A device that recorded
without being declared is a manifest bug worth seeing, but the data is real and refusing it
would destroy the more valuable thing to protect the less.

---

## 8. What lands in the schema

### 8.1 The rows

| Table | Source | Note |
|---|---|---|
| `Subject` | `manifest.subject` | Insert if absent. Keyed on the animal's name, which is also wl.works' identity for an animal (`animal.name`, unique). |
| `Session` | `manifest.subject`, `manifest.started_at` | `(subject, session_datetime)`. Maps onto wl.works' `(animalId, at)` without translation. |
| `AcquisitionSystem` | topology `present` **and `undeclared`** | One row per system with real data on disk. `undeclared` is included deliberately: §7 rules that its data is real, and omitting the row would hide a recording from every downstream stage to punish a manifest bug. |
| `Ingestion` | the ingest event itself | New. See §8.2. |

wl.works mints **no session-ID string** — its business key is `(animal.name, timestamptz)`.
`wl-sync`'s `2027-03-14_01` is a local convenience with no counterpart there, so it is recorded
in `Ingestion.session_dir` as provenance and never used as a key.

### 8.2 `Ingestion`, and why the timestamp needs its own table

```
Ingestion (Manual)
  -> pipeline.Session
  ---
  ingested_at    : datetime      # when this row was written
  session_dir    : varchar(255)  # where it came from
  integrity      : enum('verified','declared_only','skipped')
  topology       : <blob>        # the full per-system state map from §7
  manifest_hash  : varchar(64)   # blake3 of the manifest FILE'S BYTES, not of the parsed form
```

`manifest_hash` is taken over the raw file bytes rather than a canonicalised parse, because its
purpose is to answer *"is the file on disk still the one that was ingested?"* — a question a
re-serialised parse cannot answer, since two different files can parse to the same model.

`Session` records when the recording *happened*; nothing records when it was *ingested*, and
the daily report's first line is "ingested in the last 24 h". Deriving that from
`session_datetime` would answer a different question and answer it wrongly for any backfill.

`topology` is stored whole rather than normalised into rows because it is a **report input,
read as a unit and never joined against** — and because §6's blob audit means it costs one
`<blob>` attribute correctly declared, which is cheaper than five tables nothing queries.

### 8.3 Re-running is safe, and that is designed rather than hoped

`scan_once` over an already-ingested session must be a no-op, because there is no lock. The
daemon's own docstring admits the gap — *"nothing here enforces the single-runner invariant …
no lock file, no advisory lock"* — and a watcher invoked from cron inherits it exactly.

Rather than add a lock, **every write is idempotent by construction**: `Subject`, `Session`,
`AcquisitionSystem` and `Ingestion` all insert with `skip_duplicates=True` on keys derived
deterministically from the manifest, and `Ingestion`'s presence is what marks a session
already done. Two watchers racing the same directory produce the same rows in either order.

This is wl.works' most-repeated defect lesson applied here — *check-then-write is "the single
largest source of real defects"* — and the answer there is the same as the answer here:
unconditional idempotent write, never a read followed by a conditional write.

---

## 9. Quarantine is keyed by path, not by session

```
Quarantine (Manual)
  session_dir : varchar(255)     # THE KEY
  ---
  failed_at    : datetime
  reason       : enum('manifest_invalid','manifest_schema_version',
                      'session_id_mismatch','subject_unrepresentable',
                      'session_dir_unrepresentable','checksum_mismatch',
                      'params_invalid','unexpected_failure')
  detail       : <blob>
  subject      : varchar(32) null    # best effort, may be null
  session_dt   : datetime null       # best effort, may be null
```

**The key is the directory path because the worst failure is an unparseable manifest, and the
manifest is what yields `(subject, session_datetime)`.** A quarantine record addressed by the
session key cannot represent the failures that most need recording — which is the whole reason
the record exists. The directory path is available in every case, because the watcher is
standing in it.

Two consequences follow, and both are wanted. The `Session` hierarchy contains **only sessions
that validated**, so every downstream query is honest without a filter nobody remembers to
write. And a quarantined session that is later fixed and re-ingested simply produces the real
rows, with the quarantine row left as history rather than something to clean up.

**Three reasons were added during implementation, and each records a lesson.**
`subject_unrepresentable` and `session_dir_unrepresentable` exist because element-animal declares
`subject : varchar(8)` and this table declares `session_dir : varchar(255)`, while the manifest's
`subject` and the operator's `--root` are both unbounded strings. An unbounded value meeting a
narrow column raises a `DataError` from inside the insert, which is a crash rather than a
quarantine — so both are length-checked before they can reach one.

`unexpected_failure` is the outer boundary's reason: `scan_once` evaluates each session inside a
comprehension, so an exception escaping one session would lose every other session in the same
root. Anything not matching a named reason is caught there and recorded rather than raised. It is
structurally last, and every named reason is tested at the watcher level, so it collects only what
nothing else claims — but **it is a catch-all, and a reason appearing there frequently is a signal
that it deserves a name of its own**, not that the boundary is working.

**Where a check sits relative to `already_ingested` is load-bearing, and got this wrong twice.**
A check above it must test a property of *this directory and this manifest* that is fixed for as
long as the directory exists — `session_id` versus the directory's basename qualifies. A check
that depends on anything mutable must sit below, or an already-landed session starts quarantining
on every poll while keeping its `Ingestion` row, and appears under both Ingested and Quarantined,
contradicting this section. Two things caught this way: `schema_version`, which fails for
untouched sessions the day `SCHEMA_VERSION` is bumped, and the `session_dir` length check, which
depends on the operator-supplied `--root` and so changes when a storage root is remounted or moved.

**The rule has two forced exceptions, and stating them is the point.** Reading and parsing
the manifest also sits above `already_ingested`, and a manifest's bytes *can* change under a
landed session — corrupt one and it re-quarantines as `manifest_invalid` on every poll while its
`Ingestion` row persists, which is the contradiction above. It cannot be moved: `already_ingested`
is keyed on `(subject, session_datetime)`, and both come *from the parse*. So the parse is
accepted as a standing exception, and the residual is accepted with it — a session whose manifest
is corrupted after landing is genuinely worth reporting, and the noise is one row rewritten per
poll rather than a wrong verdict.

**The second exception is `session_id_mismatch`, and it was found the same way the first was —
by someone checking whether "exactly one" was true.** That check was placed above
`already_ingested` on the reasoning that it compares a directory's own basename against its own
manifest, both fixed properties of the directory. The manifest is not fixed: edit a landed
session's `session_id`, or rename the directory, and it quarantines on every poll thereafter with
its `Ingestion` row intact — the same contradiction. It is accepted for the same reason the parse
is: the alarm is arguably right, and the cost is one row rewritten per poll rather than a wrong
verdict.

This rule was stated as having no exceptions, then as having one, and both times the number was
wrong. That is worth more than either correction. **A rule presented as complete is what the next
person pattern-matches a new check against** — and two checks were placed wrongly before this rule
existed at all, which is what it was written to prevent. If a third exception appears, the honest
conclusion is not a third amendment but that the two groups are the wrong shape, and what actually
governs is whether a check's inputs can change after landing — which is a question to ask of each
input, not a group to sort a check into.

`subject` and `session_dt` are recorded when they could be parsed, because a quarantine report
naming an animal and a date is far more useful than one naming a path — but they are nullable,
and nothing may key on them.

---

## 10. `session_params.yaml`

§5.4 specifies the behaviour and this sub-project implements it unchanged: validate against a
schema, **reject unknown keys** — *"so `nblocks` vs `n_blocks` fails loudly rather than
silently defaulting"* — content-hash, and register if new.

Registration goes through 1c-1's existing paramset path, which already content-hashes and
already refuses to mutate a registered set. **Provenance records that the set arrived from the
session file rather than a lab default**, per §5.4.

What §5.4 also says — *"and inserts the request row"* — is **not** done here, for §2's reason:
a request means an activation, an activation means a montage. The paramset is registered and
waits. Nothing is lost, because a paramset's identity is its content hash: whenever the request
is eventually made, it resolves to the same set.

An invalid params file quarantines the session with reason `params_invalid`. It is a
declaration about how to process the data, and processing it under lab defaults because its
own file was malformed is the silent-default failure §5.4 exists to prevent.

---

## 11. The daily report

`wlpp report [--out DIR]` writes `DIR/YYYY-MM-DD.md` and prints it to stdout. Writing and
printing both, so a future `cron … | mail` needs no change here and the accumulating file
history answers "when did scratch start filling up?" without anyone having planned for the
question.

Contents, per §10's list, restricted to what exists by the end of 1c-2:

| Section | Source | Present in 1c-2? |
|---|---|---|
| Ingested (24 h) | `Ingestion` | yes |
| Quarantined | `Quarantine` | yes |
| Stalled transfers | filesystem scan, §4.3 | yes |
| Stuck jobs | `daemon.count_stale_jobs` | yes — already built |
| Disk headroom | `cli/doctor.py`'s existing checks | yes — reused, not reimplemented |
| Populated / failed | `populate()` results | **placeholder** — nothing computes until 1c-4 |
| Tier-D sessions | §4.7 tier derivation | **omitted** — 1c-4 derives tiers |
| Eye-detector outliers | §7.2 | **omitted** — Phase 3 |

**The three that cannot exist yet are named in the report itself**, as a line saying what is
not yet reported and which sub-project brings it. A report that silently omits a category is
indistinguishable from one where the category is empty, and "no failures" and "failures are
not counted" must never render identically.

The report **reads and never writes**, so it can be run at any time by anyone without
considering what else is running. A test asserts it opens no write transaction, in the same
shape as the guarantee `wlpp doctor` already carries.

---

## 12. Testing

The synthetic generator is the fixture source, and it already produces both faults this
sub-project needs:

- **`Fault.TRUNCATED_FILE`** — wired, truncates the SpikeGLX `.bin` → exercises §5.4's
  checksum mismatch.
- **`Fault.MISSING_DEVICE`** — wired trivially, by omitting a system from `recipe.systems` →
  exercises §7's `absent` and, when the system is left in `expected_systems`, `pending`.

**Three of §9.1's faults are not generatable today** — dropped barcodes, mid-session restart,
and trial-count mismatch each have a tested function in `synth/faults.py` that
`generate_session` never calls. They are 1c-4's fixtures, not this sub-project's, so nothing
here is blocked. It is recorded because the test that should have caught the gap cannot:
`test_every_fault_has_an_implementation` asserts only `hasattr(faults, name)`, so it passes
while the wiring is absent. **1c-4 must not trust that test.**

Fixtures the watcher needs that the generator cannot yet produce — an empty `DONE`, a `DONE`
listing a file that is not there, a manifest with a future `schema_version`, a `session_id`
disagreeing with its directory — are built directly in the test module by mutating a generated
session. They are one-line corruptions of a valid tree, and routing them through the generator
would add nine recipe knobs that only tests use.

Everything except `landing.py` and the report is filesystem logic and tests without a database.

---

## 13. Deliberately excluded

- **A lock.** §8.3 makes concurrency safe by idempotence instead. A lock file would need
  cleanup after a crash, which is the same stale-reservation problem `reap_stale_jobs` exists
  for, and solving it twice differently is worse than solving it once.
- **inotify / filesystem events.** A polling `scan_once` is simpler, has no platform surface,
  and cannot miss an event that fired while nothing was listening. Sessions arrive a few times
  a week; latency is irrelevant.
- **Retry of a quarantined session.** Fixing the cause and re-running is the whole procedure.
  Automatic retry of something that failed validation just fails repeatedly and fills the
  report.
- **Any wl.works call.** §11.1, and it is not negotiable.

---

## 14. Open questions this design does not close

- **`STALL_AFTER_S`'s value.** 2 h is a first guess against a transfer whose real duration
  nobody has measured, because no rig exists. It is a reporting threshold (§4.3), so being
  wrong is visible and harmless, and it should be revisited against the first real transfers.
- **Whether the rig writes `DONE` per system or the transfer tool does.** Both satisfy §5.1;
  the choice belongs with whoever writes the transfer scripts, and it is recorded here so that
  person knows the contract exists before they start.
- **§13 item 5** — the ingest-time task-PC vs sync-box clock cross-check tolerance. It belongs
  to 1c-4, which computes the fit; the watcher records nothing about it.
- **§13 item 9** — reopened 2026-08-15, resolution proposed and not ratified. It gates the
  canonical activation, **not** this sub-project (§2).

- **A landed `Ingestion` row has no correction path, and that was not deliberate.** §8.3 requires
  `scan_once` over an already-ingested session to be a no-op, and the watcher checks
  `already_ingested` *before* landing — so once the row exists, nothing re-runs: not verification,
  not topology discovery, not landing. §9 gives a repair story for a *quarantined* session ("fixed
  and re-ingested simply produces the real rows") and there is no analogous sentence for a landed
  one. So a session that ingested successfully but recorded a wrong `integrity`, `topology` or
  `manifest_hash` — because a `DONE` marker was empty when it should not have been, say — cannot
  be corrected by any command this phase ships, and `wlpp delete` is preview-only by design.

  **Found 2026-08-15 by Task 6's review, and left open on purpose.** The fix is either a
  re-ingest path or a supported way to remove an `Ingestion` row, and nothing consumes either
  yet. This project's standing rule is that nothing is invented before it has a consumer, so the
  gap is recorded rather than filled. **Whichever sub-project first needs to correct a landed
  session owns closing it** — most likely 1c-4, which is the first to compute anything from these
  rows and therefore the first to care that they are right.
