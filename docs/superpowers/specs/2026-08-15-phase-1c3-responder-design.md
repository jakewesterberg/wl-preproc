# Phase 1c-3 — The responder, the action list, and the protocol document: design

**Written 2026-08-15.** Third of four sub-projects decomposed from Phase 1c. It builds the HTTP
surface wl.works polls, the health and action list it publishes, the path from a job request to
Manual-tier rows, and the protocol document both repositories have been citing and neither has
written.

**Parent spec:** [`2026-08-12-wl-preproc-design.md`](2026-08-12-wl-preproc-design.md) §3.1 component 4, §11 entire, §12.1
**Predecessors:** [`2026-08-13-phase-1c1-schema-design.md`](2026-08-13-phase-1c1-schema-design.md) §9.1 · [`2026-08-15-phase-1c2-ingest-design.md`](2026-08-15-phase-1c2-ingest-design.md) §2
**Follows:** 1c-4 timebase and coverage

---

## 1. The montage precondition is answered inbound, and that was the finding

Two sub-projects treated montage boundaries as a blocker. 1c-1 narrowed `submit()` to canonical
activations because it had no block set. 1c-2 discovered it could avoid `submit()` entirely
because timebase and coverage are Computed tables that populate from `Session` keys alone. §13
item 9 was reopened over it.

**The answer was already in the frozen contract.** `contracts/protocol.py`'s `MetadataBundle`
carries `blocks`, `montage_boundaries`, `probes`, `experimenter`, `subject` and `task_types`
**inbound with every request**, and says why in its own docstring: *"everything wl-preproc needs
from the ELN arrives in the request payload."* The schema was designed to match — `core.Montage`
says *"Sourced from wl.works `item_insertion` and nothing else"*, and `core.Block` carries
`works_block_id` as the link to the authored row.

The contract and the schema were built for each other. Nothing had joined them because the
component that receives the payload did not exist. **This sub-project is that component**, and
building it dissolves the precondition for every piece of *requested* work.

**It does not dissolve for the automatic canonical activation**, which has no inbound request to
carry a montage. That is the true, narrowed residual of item 9, and §12 records it rather than
solving it.

---

## 2. What this builds, and where its edges are

§3.1 defines component 4: *"HTTP endpoint wl.works polls. Publishes the action list, accepts
requests carrying an idempotency key, inserts Manual-tier rows. **Never opens a connection
outward.**"*

**In scope:** the HTTP server and its two endpoints; bearer-token authentication; the health
verdict and readings; the action list, derived rather than declared; the job path from
`JobRequest` to `Request`/`Activation`/`Montage`/`Block` rows; derivative activations and the
`selection_hash` 1c-1 deferred here; and `docs/ops/lab-host-protocol.md`.

**Out of scope, with reasons:**

- **Executing anything.** §11.3 is explicit: *"The responder does not compute; it inserts a
  Manual-tier request row and the daemon picks it up, exactly as the ingest watcher does."*
- **The five dispatch domains themselves** — 20b is Phase 2, 24b Phase 3, 18b/27b/29b
  post-January. §3 explains why the action list is therefore empty today and why that is correct.
- **The 12-hour canonical trigger.** It needs a montage source this sub-project does not give it
  (§1), and it is scheduling rather than protocol.
- **TLS.** wl.works' Plan 10 §5.4 makes plain HTTP the stated default for this leg and argues it
  explicitly; adding TLS introduces a certificate-trust coordination point their design does not
  have. New scope, not a gap.

---

## 3. The action list is derived, and today it is empty

`HealthResponse` carries `readings` and `actions`. The readings are useful from day one; the
actions are not, and pretending otherwise would be worse than publishing none.

wl.works renders each action as a button any lab member can press — its Plan 10 §4.1 states the
rule as the protocol's most prominent line: *"Publishing an action makes it available to every
member of the lab. There is no permission model on the app side. If an action should not be
triggerable by anyone who can log in, do not publish it."* A button that queues work no computed
stage will pick up for six months is a button that teaches people to distrust the surface.

**So the list is computed from which stages actually exist**, the same way `daemon._computed_tables()`
already discovers what `populate()` can run. Today that yields nothing. When the ephys branch
lands, spike sorting appears in the list with no change here and no change in wl.works — which is
exactly what *"the host publishes its own action list"* was written to buy.

This mirrors 1c-2's report, which names the categories it cannot yet count rather than omitting
them. Here the same instinct applies to capability: **the health response says what it cannot yet
offer**, as a reading, so an empty `actions` array is legible rather than mysterious.

---

## 4. The HTTP surface

### 4.1 stdlib, and why

`http.server.ThreadingHTTPServer` with a hand-written dispatch. No web framework.

The surface is two endpoints with no sessions, no uploads, no streaming and one known client.
Every payload is already validated by pydantic models that exist and are tested, so a framework's
main contribution would be routing — which this codebase already does by hand in `cli/main.py`'s
subcommand dispatch. Against that, FastAPI brings eight to ten packages onto the box holding every
session's raw recording, which must run to 2030 in a lab with no dedicated sysadmin.

The one strong argument for FastAPI was its OpenAPI schema, since wl.works' contract tests run
against a fake wl-preproc. That is already satisfied: `wlpp schemas export` writes JSON Schema for
`HealthResponse` and `JobRequest` to `docs/schemas/`, and CI diffs the directory to keep it current.

**The honest cost, recorded rather than glossed:** Python's documentation says `http.server` is not
recommended for production, and it is not hardened against malformed or deliberately slow clients.
The mitigations are that it binds to the lab LAN with one known caller, that authentication is
required (§4.3), and that putting nginx in front of it later is a deployment change rather than a
rewrite. If this endpoint ever becomes reachable from outside the lab, that hardening is the
precondition.

### 4.2 Two endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/health` | — | `HealthResponse` |
| `POST` | `/jobs` | `JobRequest` | `{"activation": {…}, "accepted": true}` |

Anything else is `404`. A malformed body is `422` with the pydantic error, never a traceback.

### 4.3 A bearer token on both endpoints

**wl.works plans to send no credential at all** — Plan 10 §6's request is an action name plus an
idempotency key. And its reason for building no permission model of its own is an assumption about
this host: *"the host is the real boundary anyway — an action the app refuses to show is still an
HTTP endpoint on the LAN."*

That was written 2026-08-03, before any wl-preproc design existed to check it against. It is not a
requirement placed on us; it is a prediction about us, and we are free to be better than it.

The intended trigger population is *any lab member*. The actual population of an unauthenticated
LAN endpoint is *anything on the lab network* — rig PCs, the task PC, laptops, whatever else is
plugged in. Those are not the same set, and the `POST` starts compute on the box holding every
session's raw data.

**So both endpoints require `Authorization: Bearer <token>`**, compared with
`hmac.compare_digest` against a value read from configuration, never from the repository. A
missing or wrong token is `401` with no detail. The health endpoint is included not because its
readings are sensitive but because one rule is easier to hold than two.

**This requires a change on wl.works' side**, and that is the point of §7: the requirement belongs
in the protocol document, which is where their own design says such things go.

### 4.4 Threading and the database connection

`ThreadingHTTPServer` gives a thread per request, and **DataJoint connections are not shareable
across threads** — 1c-2's final review lost an afternoon to a four-thread probe that produced
`InternalError: Packet sequence number wrong` from exactly this, and was only diagnosed by redoing
it with subprocesses.

The responder therefore serialises database work behind a single lock rather than opening a
connection per thread. One known client polling every few minutes does not need concurrency, and a
lock is a great deal easier to reason about than a connection pool. The health endpoint's reads
and the job endpoint's writes take the same lock.

---

## 5. Health

### 5.1 The verdict is three values, not four

wl.works validates `verdict` against exactly `ok`, `degraded`, `down`, `unknown`, and rejects
anything else. **`contracts/protocol.py` types it as a bare `str`**, which is a live
wire-compatibility gap in a frozen interface: a typo ships and their validator rejects the whole
response. Tighten it to a `Literal`.

**But this host emits only three of the four.** `unknown` is what *wl.works* records when a host
goes silent past its `stale_after_seconds` — it is their word for our absence, and we are never in
a position to assert it about ourselves. Emitting it would claim knowledge of our own silence.

| Verdict | When |
|---|---|
| `down` | The database is unreachable. Nothing else can be computed, and saying `degraded` would overstate what we know. |
| `degraded` | Reachable, but something needs a human: stuck jobs, quarantined sessions, stalled transfers, or scratch below the floor. |
| `ok` | Reachable and none of the above. |

### 5.2 The readings are 1c-2's report, over the wire

1c-2 already computes ingested-in-24h, quarantined, stalled transfers, stuck jobs and disk
headroom — but `cli/report.py` exposes only `build_report()` and `write_report()`, which return
**Markdown**. There is no function that returns the values.

**So this sub-project extracts one**, exactly as 1c-2 extracted `scratch_headroom` out of
`doctor.run_checks()` when the report needed the same number: a `gather_readings(root, prefix, now)`
returning structured values, with `build_report` rendering them to Markdown and the responder
rendering them to `Reading` rows. Neither caller computes anything itself.

That extraction is the point rather than a convenience. Two definitions of "how many sessions are
stuck" that can disagree is a defect this project has now found in four separate shapes, and the
one time it was caught early — the disk-headroom check — it was caught precisely because someone
noticed the second caller was about to reimplement the first.

`featured` marks the one reading worth wl.works' home page. Plan 10 §4 settles the ambiguity — more
than one featured reading and the first wins — so exactly one is emitted, and it is whichever
condition drove a non-`ok` verdict, or the ingest count when everything is fine.

---

## 6. The job path

### 6.1 What a request produces

A validated `JobRequest` yields, in one transaction:

1. `Montage` rows from `metadata.montage_boundaries`, if absent. **This is §1's finding in code** —
   the boundaries are wl.works' authored record arriving inbound, so recording them is not guessing.
2. `Block` rows from `metadata.blocks`, with `works_block_id` set, if absent.
3. A `Request` row, through `submit()`'s existing idempotency machinery.
4. An `Activation` — canonical or derivative per §6.2.

Steps 1 and 2 are insert-if-absent. wl.works owns those records; we record what it asserts and
never overwrite it, because a later request carrying corrected boundaries is wl.works correcting
its own record, and §9 of the parent spec makes that their call rather than ours.

### 6.2 Derivative activations and `selection_hash`

1c-1 narrowed `submit()` to canonical activations and deferred derivatives here, because a
derivative is *"any hand-picked subset… unbounded, additive"* and needs a dedupe key that is not
the montage.

`Activation` gains `selection_hash : varchar(64) null` — the content hash of the sorted block set
plus the task type, computed the way `paramset.content_hash` already does it, and **null for a
canonical activation**, whose identity is `(session, montage)` per §8.3.

§11.3's rule then implements directly: *"a request whose `(selection, task type)` is already in
flight returns the running one instead of starting a second."* Dedupe is a lookup on
`selection_hash`, and it is structural rather than a lock — the same shape 1c-1 used for canonical
activations and for paramset registration.

**The residual 1c-2 recorded closes here.** A reused idempotency key whose original submission
deduped onto a pre-existing activation could not be compared on selection, because nothing recorded
which selection it asked for. `selection_hash` is that record.

---

## 7. `docs/ops/lab-host-protocol.md`

wl.works cites this file from five places and it does not exist. Their `waiting-on.md` lists it as
explicitly not blocked, and Plan 10 §8.2 argues for writing it now: *"If `lab-host-protocol.md` and
its reference responder exist before wl-nas is racked and configured, installing the responder is
part of standard build-out rather than a retrofit."*

**We write it, because we are the half that knows what the responder does**, and because §3.5 of
the parent spec already lists the wl.works↔wl-preproc protocol as a frozen pre-January deliverable.

It carries: the two endpoints and their shapes; the bearer token, since wl.works must send it and
does not today; the four verdict values and the three this host emits; the `featured` rule; and
**the numbers wl.works left as unfilled constants** — poll cadence, request timeout, and the
per-host refresh cooldown. Their Plan 10 defers all three to environment configuration and never
gives a value, and a protocol document with no timing in it is what leaves two sides guessing.

It lives here and is proposed to them, in the same append-only shape as the item-9 amendment. It
is not written into their repository unilaterally.

---

## 8. Module structure

| Module | Responsibility |
|---|---|
| `responder/server.py` | `ThreadingHTTPServer` wiring, the lock, `serve(port, token, prefix)`. |
| `responder/handler.py` | Routing, auth, status codes. Knows HTTP; knows nothing about DataJoint. |
| `responder/health.py` | Verdict and readings. Reads only. |
| `responder/actions.py` | Derives the action list from the computed stages that exist. |
| `responder/jobs.py` | `JobRequest` → rows. The only module here that writes. |
| `cli/main.py` | `wlpp responder --port --prefix` |

`handler.py` knowing no DataJoint is the same separation `ingest/` uses, and for the same reason:
routing, auth and status codes are testable without a database, and there are more of those cases
than there are of the ones that need one.

---

## 9. Testing

The health and action paths test against a live schema, as 1c-2's do. The HTTP layer tests against
a real `ThreadingHTTPServer` on an ephemeral port — not a mocked handler, because the defects worth
catching here are in routing, status codes and header parsing, which a mock reproduces by
construction.

**Specifically tested, because each is a rule someone assumed rather than checked:** a missing
token is `401` and not `403`; a wrong token is `401` with no hint which part was wrong; a valid
token with a malformed body is `422` and never a traceback; an unknown path is `404`; the same
idempotency key twice yields one `Activation`; two different selections yield two; `verdict` is
never `unknown`; and exactly one reading is `featured`.

**And one negative test the project has earned:** nothing in `responder/` may open an outbound
connection. §11.1 is not negotiable and a test should say so, in the same shape as the guardrail
that forbids a bare `.delete()`.

---

## 10. Deliberately excluded

- **A permission model.** wl.works' is flat by design, its Plan 17 was flagged to reconsider and
  never did, and inventing per-action roles here would put this host in the business of deciding
  who in the lab may do what — a much larger decision than this sub-project.
- **Rate limiting.** One known client with a cooldown on its own side.
- **A connection pool.** §4.4.
- **Result upload.** wl.works pulls; we never push. §11.1.

---

## 11. What this inherits from 1c-2

- Guard every filesystem call that can raise; the failure answer is the honest one, not an exception.
- `in_transaction` is **not** a read-only check — DataJoint's `insert()` bypasses the transaction
  machinery entirely. To prove the health path does not write, snapshot rows.
- Test subjects are ≤8 characters and must not collide with `tests/schema/test_core.py`'s
  `(pico, 2027-03-14 09:00:00)`.
- Adding a `QUARANTINE_REASONS` member — or any enum value — needs a migration on a deployed
  schema; `activate(create_tables=True)` will not `ALTER` a column.

---

## 12. Open questions this design does not close

- **Where the automatic canonical activation gets its montage.** §1 dissolves the precondition for
  requested work and not for the 12-hour trigger, which has no inbound payload. Three candidates:
  wl.works pushes session metadata ahead of any request; the canonical trigger waits until some
  request has supplied montages for that session; or 1c-4 measures them, which contradicts
  `core.Montage`'s own comment. **This is the true residual of §13 item 9** and it belongs to
  whoever builds the trigger.
- **The token's lifecycle** — where it is stored, how it is rotated, and whether wl.works can hold
  more than one during a rotation. The protocol document names the requirement; operating it is a
  deployment decision this design does not make.
- **§13 item 12**, the actor identity for automatic activations, is untouched here: every request
  this component handles carries a `requested_by`.
