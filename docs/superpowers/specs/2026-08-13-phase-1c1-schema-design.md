# Phase 1c-1 — Schema, guardrails and the populate daemon: design

**Written 2026-08-13.** First of four sub-projects decomposed from Phase 1c. It builds the
DataJoint schema every later stage inserts into, the single job runner behind it, and the
guardrails §10 requires — including one that only exists because of what a spike found today.

**Parent spec:** [`2026-08-12-wl-preproc-design.md`](2026-08-12-wl-preproc-design.md) §5, §10, §11.3
**Sub-projects that follow:** 1c-2 ingest watcher · 1c-3 responder and action list · 1c-4 timebase and coverage

---

## 1. Why this is its own sub-project, and where its edges are

Phase 1c names five things: DataJoint schemas and guardrails, ingest watcher, timebase
fitting, coverage, and the responder. The checkpoint warns it is larger than one plan.

**§11.3 is what decides where the seams fall:**

> *"The DataJoint populate daemon **is** that job runner. The responder does not compute; it
> inserts a Manual-tier request row and the daemon picks it up, exactly as the ingest watcher
> does."*

So the watcher and the responder are not two systems that must coordinate over a queue — they
are **the same shape**: two entry points that insert Manual-tier rows into a schema, with one
daemon behind them. That makes the schema plus the daemon a coherent foundation, and the two
entry points siblings on top of it. It is also why §12.1 calls the responder *"cheap if built
with the ingest watcher; expensive to retrofit"*.

**In scope:** the schema modules and their activation, the `Request` surface, the populate
daemon, `wlpp doctor`, `wlpp delete`, the paramset tables, and the test harness.

**Out of scope, with reasons:** the ingest watcher (1c-2); the responder and action list
(1c-3); timebase fitting and coverage *computation* (1c-4) — though their **tables** are
declared here; anything that populates the tables with real content.

**The daily status report is 1c-2, not here.** It reports ingested / populated / failed /
stuck / quarantined sessions, and until the watcher exists there is nothing to report on
except stuck jobs. `wlpp doctor` **is** here, because everything it checks — DB connectivity,
external store paths, stale job reservations — exists the moment the schema does.

---

## 2. The stack, settled

§5.1.1 of the parent spec records this in full; the operative facts:

- **`datajoint>=2.3,<3`.** DataJoint 2.0 (Feb 2026) is a breaking major version whose migration
  guide states pre-2.0 receives no further support and that new projects should adopt 2.0
  directly. Staying on 0.14 would be choosing the abandoned branch.
- **No forks.** `element-lab`, `element-session`, `element-event` take upstream commit pins;
  `element-animal` pins to PR #51's branch; **`element-array-ephys` is not activated** until
  issue #230 is fixed.
- **Long computations use the three-part make** — `make_fetch` / `make_compute` /
  `make_insert`, verified on 2.3.2 with the call order `fetch, compute, fetch, insert`.

---

## 3. Module structure

One Python module per DataJoint schema, which is the Elements convention and keeps each file
small enough to hold in context.

| Module | Schema | Contents |
|---|---|---|
| `pipeline.py` | — | The linking module. Imports every schema, supplies the names Elements expect, activates in dependency order. |
| `lab.py` | `wlpp_lab` | `element-lab` |
| `subject.py` | `wlpp_subject` | `element-animal` |
| `session.py` | `wlpp_session` | `element-session` |
| `event.py` | `wlpp_event` | `element-event` |
| `block.py` | `wlpp_block` | `Block` — mirrors wl.works `animal_session_block` |
| `montage.py` | `wlpp_montage` | `Montage` — from `item_insertion` and nothing else |
| `sync.py` | `wlpp_sync` | `AcquisitionSystem`, `Segment`, `RejectedSegment`, timebase fit tables |
| `coverage.py` | `wlpp_coverage` | `TrialCoverage`, `BlockCoverage` |
| `request.py` | `wlpp_request` | `Request`, `Activation`, `ActivationBlock` |
| `paramset.py` | `wlpp_paramset` | The §5.3 paramset tables |

**`pipeline.py` is the only place activation happens.** Elements resolve their foreign keys
through a linking module, so scattering `activate()` calls makes the dependency order implicit
and the failure mode a foreign-key error at import. One module makes the order explicit and
reviewable. It is also where `Experimenter = User` lives — `element-session` expects a name
`element-lab` does not provide, and supplying it is exactly what a linking module is for.

**Where `element-array-ephys` would go, there is a comment instead** naming the Phase 2
precondition, so the next person to reach for it reads why before they type `activate`.

---

## 4. The schema

§5.2 of the parent spec gives the key hierarchy and it is not restated here. What this design
adds is the part §5.2 leaves undefined.

### 4.1 What is declared now, and what is not

**Every custom table in the hierarchy is declared in 1c-1, including ones nothing populates
until Phase 2 or 3.** This inverts the usual preference for declaring only what is consumed,
and the reason is the blob deadline in §6: the audit is only free while a table is empty, and a
schema built in halves is audited in halves. Declaring the whole custom surface now means one
audit, entirely pre-data.

**The `element-array-ephys` branch is the exception** — `ProbeInsertion`, `Clustering`,
`CuratedClustering`, `WaveformSet` and the unit tables arrive in Phase 2. That costs nothing
against the deadline, because the deadline is **per-table**: a table that does not exist has no
rows, so declaring it later with the fix in place is exactly as safe.

### 4.2 `Request` — the protocol boundary

Both entry points insert here, and nothing computes here.

```
Request (Manual)
  idempotency_key : varchar(64)      # from wl.works, or minted locally
  ---
  task_type    : varchar(32)         # a domain from the published action list (§11.4)
  origin       : enum('ingest','wl_works','cli','auto')
  payload      : <blob>              # the request exactly as received
  requested_by : varchar(64) null    # null for machine origins — see §13 item 12
  requested_at : datetime
```

**It is append-only and records what was asked, not what will be done.** That separation is
§11's own: *"wl-preproc executes and reports, and renders no verdicts of its own."* The raw
payload is kept because a request that turns out to be malformed is evidence, and reconstructing
it from the rows it produced is not the same thing.

### 4.3 Every activation enters through `Request`, including the automatic one

§8.3 describes canonical activations as automatic and derivatives as requested, which reads
like two doors. **There is one door.** The 12-hour canonical trigger inserts a `Request` with
`origin='auto'`, exactly as the responder and the CLI do.

**`Activation` is `Manual`, not `Computed`, and the entry point writes both rows in one
transaction.**

> **Corrected during this spec's own self-review.** An earlier draft made `Activation`
> `Computed` from `Request`. That cannot work: a DataJoint computed table inherits its primary
> key from its parents, so `idempotency_key` would land in `Activation`'s key and contradict
> §5.2's `(…, montage_id, activation_id)`. The error is recorded rather than quietly fixed
> because the plausible-but-wrong version is what a reader would otherwise reconstruct.

So the shape is:

```
Request     (Manual)    idempotency_key            ← what was asked
Activation  (Manual)    (subject, session_datetime,
                         montage_id, activation_id) ← what will be computed over
                        + request_key : varchar(64) → Request, provenance only
Clustering  (Computed)  -> Activation, paramset_idx ← the daemon's work
```

`Activation` references `Request` as an attribute rather than in its key, so provenance is
recoverable — *which request produced this activation* — without the protocol boundary leaking
into the pipeline's identity. **The daemon "picking it up" means populating `Clustering` and
everything downstream of `Activation`**, not materialising `Activation` itself; §11.3's phrase
is about who computes, and nothing about the fan-out computes.

Three things make this the right reading rather than a liberty:

- §5.4 already says it: *"Manual reprocessing is an insert, not a second execution path…
  There is no 'manual mode' that behaves differently from automatic mode."* Two doors would be
  exactly the second execution path that section refuses.
- **It gives §8.3.1's retry requirement somewhere to live.** At a 12-hour delay the ELN is
  frequently not current, so quarantine is ordinary and the activation must re-fire by itself
  once the missing rows land. With one door, re-firing is re-inserting a `Request` — not a
  separate resurrection path with its own bugs.
- `origin` keeps the distinction that actually matters for §13 item 12, without duplicating
  the machinery around it.

### 4.4 Dedupe is structural, never a lock

§11.3 is explicit that wl.works never asks *"is a run going, then start one"*, and wl.works'
own row 29 §7 states *"assertions are inserts. Nothing reads before writing one."*

So **nothing in this design asks whether a run is in flight.** `Activation` carries the real
key — `(subject, session_datetime, montage_id, activation_id)` — and two requests naming the
same selection resolve to the same key. The second `Activation` insert is a duplicate and is
skipped; `populate()` then computes that key exactly once by construction. The idempotency key
distinguishes a network retry from a new request, which is its documented job, and is **not**
what prevents a second run.

**The two inserts are one transaction, and that is load-bearing.** A `Request` written without
its `Activation` is an accepted request that will never run — the failure mode wl.works would
see as a silent hang. A test asserts that a failure between the two leaves neither.

### 4.5 Paramsets

§5.3's pattern, unchanged: `paramset_idx` with a content-hash uniqueness constraint, immutable
once used, computed tables keyed on `(…, paramset_idx)` so re-running adds rows. The CLI
refuses in-place modification; an edit yields a different hash and is therefore a new paramset.

---

## 5. The populate daemon

`wlpp daemon` — **one process holds the machine** (§11.3), calling `populate(reserve_jobs=True)`
over the computed tables in dependency order, with priority expressed inside the loop rather
than by running several daemons. Two runners each concluding the machine is free is the failure
§11.3 refuses for VRAM, and it is tractable here because both contenders are on one host.

**Long stages use the three-part make.** A 4-hour sort in a plain `make` holds a MySQL
connection to `wait_timeout`; splitting it puts the compute outside the transaction, with a
re-fetch-and-compare inside it so referential integrity survives. Upstream is doing the same —
`element-array-ephys` moved `EphysRecording` to it in December 2025.

**Stale reservations are reaped on a schedule.** A crashed populate leaves `~jobs` marked
reserved and the key is skipped forever, which §10 names as a top-four DataJoint hazard. The
reaper is here; surfacing stuck jobs in the daily report is 1c-2.

---

## 6. The blob audit, which is a test rather than a rule

**Under DataJoint 2.x a bare `longblob` declares a raw binary column rather than a DataJoint
blob.** A numpy array inserted into one is stored as its *string repr*, elided in the middle by
numpy above ~1000 elements, and **nothing raises on insert or on fetch**. Measured: a 384 × 82
float32 waveform set — 31,488 values — stored as 488 bytes, unrecoverable. `<blob>` round-trips
correctly.

Every array-valued attribute in this repository is exposed to this, and §5.1 puts sync,
segments, timebase, coverage, provenance, eye and stim in the custom column.

**Two enforced tests, not a convention:**

1. **No bare `longblob` anywhere in the pipeline.** Walk every declared table's heading and
   fail on any attribute declared as a bare `longblob` rather than `<blob>`.
2. **Round-trip every array-valued attribute.** Insert a real numpy array, fetch it, assert
   `shape`, `dtype` and contents survive.

**The second test is the one that matters, and its absence is what upstream is currently paying
for.** A declaration test cannot see this failure — the tables declare perfectly. Only a
round-trip can.

---

## 7. Guardrails

§10's four hazards and their handling stand. What this design fixes is that most of them were
conventions.

| §10 requirement | How it is enforced here |
|---|---|
| No bare `.delete()` anywhere | **A CI assertion**, in the style of the existing "contracts came from wl-sync" step. A rule nothing enforces decays. |
| Primary keys documented in-schema with a warning | Table docstrings, and a test asserting each declared table has one. |
| Long computations out of transactions | The three-part make (§5), plus `wait_timeout` / `max_allowed_packet` settings in `wlpp doctor`'s checks. |
| Stale job reservations | The reaper (§5). |
| `wlpp delete --session X --from-stage Y` | Prints the full cascade, defaults to `--dry-run`, requires explicit confirmation. |
| `wlpp doctor` | DB connectivity, external store paths, scratch and warm-pool headroom, GPU visibility, stale jobs, orphaned files. |

---

## 8. Testing

**A real MySQL, via `testcontainers[mysql]`** — datajoint's own test extra. Verified working
locally (MySQL 8.0 ready in ~12 s) and available on `ubuntu-latest`, which has Docker. A
session-scoped fixture starts one container for the suite.

This is what makes Phase 1's stated deliverable — *CI green on synthetic* — achievable for a
database-backed schema. CI currently has no database service; adding one is part of this
sub-project.

**What the suite must cover:** every table declares; the activation order in `pipeline.py`
resolves; a `Request` fans into an `Activation` and `populate()` computes it once when two
requests name the same selection; **a failure between the two inserts leaves neither** (§4.4);
the three-part make runs compute outside the transaction; the two blob tests in §6; and the
`.delete()` assertion.

---

## 9. Deliberately excluded

- **`element-array-ephys`** — issue #230 unfixed. §5.1.1 carries the Phase 2 precondition.
- **The daily status report** — 1c-2, where there is something to report on.
- **Timebase fitting and coverage computation** — 1c-4. Their tables are declared here.
- **The canonical 12-hour trigger itself** — it fires a sort, so it ships with Phase 2. This
  design only ensures the door it will knock on exists and is the same door everything else uses.
- **MySQL backup** — §10 calls it a first-class component. It is ops rather than schema, and it
  wants the server, which does not exist yet.

## 9.1 A limit found in execution: `submit()` only makes canonical activations

**Found 2026-08-13 during Task 5's review, and it contradicts parent-spec §8.3.**

§4.4's dedupe returns on any existing `Activation` for `(subject, session_datetime, montage_id)`.
That is exactly right for a **canonical** activation, which §8.3 defines as *"exactly one current
per (session, montage)"*. It is wrong for a **derivative**, which §8.3 defines as *"any
hand-picked subset… unbounded, additive"* — a caller asking for one would silently receive the
canonical activation's key instead, with no error.

Two things follow, and both are recorded rather than patched:

- **`submit()` takes no block set**, so `ActivationBlock` has no writer. Since unit identity is a
  product of the block set (§8.3), a derivative is not even expressible without one.
- **`supersedes` has no reachable writer**, so a regenerated canonical cannot point at the one it
  replaces — which §8.3 requires for enrichment to survive.

**1c-1's `submit()` is therefore narrowed to what it can honour**: canonical activations only,
with `activation_id` pinned at `0` and a test asserting it, so the day the dedupe key changes the
change is visible rather than silent. **Derivative support belongs to 1c-3**, the responder, which
is where a hand-picked block set actually arrives — and it needs a dedupe rule keyed on the
selection rather than the montage, per §11.3's *"dedupe on `(selection, task type)`"*.

Recorded here rather than in the plan because it is a boundary of the design, not a defect in the
code that implements it.

## 10. Open questions this design does not close

- **§13 item 5** — the tolerance for the ingest-time task-PC vs sync-box clock cross-check. It
  belongs to 1c-4, but the schema needs somewhere to record the check's *result*, so `sync.py`
  reserves a place for it.
- **§13 item 12** — the actor identity for automatic activations. `Request.requested_by` is
  nullable with `origin` as the discriminator, which is one of the two shapes item 12 offers;
  the choice is recorded but not argued here.
- **Who runs the daemon** — systemd, a container, or a supervisor. §6.6.1's "one container per
  stage, image digest is the provenance identity" may already imply the answer, but it is an
  ops decision and is not made here.
