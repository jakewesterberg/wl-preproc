# The lab host protocol

**How wl.works talks to a lab host, and what a lab host answers.**

This document is written by **wl-preproc**, which implements the host half, and is
**proposed to wl.works** rather than written into that repository. Seven documents there
name `docs/ops/lab-host-protocol.md` — Plan 10 §2 makes it the host-side contract, §4.1
puts a specific sentence at the top of it, and `waiting-on.md` lists writing it as
explicitly not blocked — and neither repository had written it. This is that file.

Its two readers are the person installing the responder on the lab host, and whoever
maintains the client in wl.works. Neither should have to re-derive anything, so every
status code, header and number below is stated rather than implied, and each was taken
from the shipped implementation in `wl_preproc/responder/` rather than from a design
document. Where the two disagreed, the code won and the design document was corrected.

---

## The most prominent line

wl.works' Plan 10 §4.1 asks that this sentence sit at the top of this file rather than
buried in it, and it is right to:

> **Publishing an action makes it available to every member of the lab.** There is no
> permission model on the app side. If an action should not be triggerable by anyone who
> can log in, do not publish it.

And its counterpart, which is this host's own:

> **Every request to this host must carry a bearer token.** Both endpoints. An action the
> app declines to show is still an HTTP endpoint on the LAN, and the population of an
> unauthenticated LAN endpoint is not "any lab member" — it is anything plugged into the
> lab network.

---

## What changes on the wl.works side

Everything else here describes behaviour that already exists. These four items are the
ones a client written against Plan 10 as it stands today would get wrong.

1. **Send `Authorization: Bearer <token>` on both endpoints.** Plan 10 §6's request is an
   action name plus an idempotency key and carries no credential. Without the header,
   every request — including a health check — is answered `401`. See
   [Authentication](#authentication).
2. **Handle `409 Conflict` outside the retry loop.** It means an idempotency key was
   reused for materially different content, and no number of retries will clear it. See
   [409 is not retryable, and that is the point](#409-is-not-retryable-and-that-is-the-point).
3. **Handle `408 Request Timeout`, which is new on the wire.** It is retryable, unlike
   `409`. See [408 is retryable](#408-is-retryable).
4. **Fill in the three timing constants.** Plan 10 leaves poll cadence, request timeout
   and the per-host refresh cooldown as unfilled environment configuration. Values are
   proposed in [The three numbers](#the-three-numbers).

---

## Transport

| | |
|---|---|
| Scheme | `http://` — plain, on the lab LAN, per Plan 10 §5.4 |
| Address | the responder binds **every interface** on one TCP port |
| Direction | **wl.works opens every connection. This host never initiates one.** |
| Framing | HTTP/1.0 responses; **one request per TCP connection**, no keep-alive |
| Content type | `application/json` on every response that carries headers at all — three framing-error paths carry none, see the framing note under [Status codes](#status-codes) |
| Encoding | UTF-8; a request body that is not valid UTF-8 is `422` |
| `Server` | present, with a single-space value (`Server:  ` on the wire) — never a version |

**This host never initiates a connection**, and that is not merely an intention. Plan 10
§11 rules out push and heartbeats from the app's side; the parent wl-preproc design §3.1
says the same from this side (*"Never opens a connection outward"*). It is enforced by
`tests/test_cli_guardrails.py::test_nothing_in_wl_preproc_opens_an_outbound_connection`,
an AST walk over **every** `.py` file in the `wl_preproc` package — not only
`wl_preproc/responder/` — which fails the build if any module imports `requests`,
`httpx`, `aiohttp`, `urllib.request`, `http.client` or `socket`. The scope is the whole
package because the property is not a property of a directory: `GET /health` calls
`wl_preproc.cli.report.gather_readings` on the request path, so an outbound call added in
`cli/report.py` would run inside a request the responder is serving while sitting outside
a `responder/`-only scan. The inbound listening socket comes from `http.server`, which
binds without this repository's source naming `socket` at all.

**Results are pulled, never pushed.** Nothing in this protocol uploads an output, and
nothing calls back when a job finishes. wl.works discovers finished work the same way it
discovers everything else: by polling. See
[What this protocol does not carry](#what-this-protocol-does-not-carry).

**No keep-alive.** Responses are HTTP/1.0 and the server closes the connection after each
one, whatever the request's own version was — measured directly against the shipped
handler. A client must not hold a connection open between polls.

---

## Authentication

Both endpoints require:

```
Authorization: Bearer <token>
```

- The scheme token is matched **case-insensitively** (RFC 7235 §2.1), so `bearer`
  authenticates exactly as `Bearer` does.
- The token is compared with `hmac.compare_digest`, not `==`.
- The check runs **before routing and before any request body is read**. An
  unauthenticated caller learns nothing about which paths exist or which verbs they
  answer, and this host spends no effort parsing a body it was never entitled to send.

**Every authentication failure is the same `401` with the same body.** A missing header, a
header with the wrong scheme, and a header carrying the wrong token are indistinguishable
from the response:

```
HTTP/1.0 401 Unauthorized
Content-Type: application/json

{"error": "unauthorized"}
```

Never `403`: the absence of any credential and the presence of a wrong one are the same
failure from the caller's side, not two. There is deliberately no hint about which part
was wrong.

**Every verb other than `GET` and `POST` also answers `401`** — `PUT`, `DELETE`, `HEAD`,
`OPTIONS`, `PATCH`, `TRACE`, and any arbitrary method token (`CONNECT`, `PROPFIND`,
`BREW`, lowercase `get`). An *authenticated* `PUT`/`DELETE`/`HEAD`/`OPTIONS`/`PATCH`/`TRACE`
gets the honest routing answer instead (`404` or `405`); an authenticated arbitrary token
still gets `401`, because the code path that answers it cannot tell whether authentication
passed. `401` is the safe direction of that trade.

**The `Server` header discloses no version.** Do not expect it to be *absent*: measured on
the wire it is `Server:  ` — the header is sent on every response that carries headers at
all, and its value is a single space. `server_version` and `sys_version` are both emptied,
so the string the standard library joins them into has nothing in it but the separator, and
stdlib sends the header name regardless. What it never contains is this host's interpreter
version. No response body ever contains a Python traceback, a stack frame, or that version
either.

### Operating the token

| | |
|---|---|
| Source | the environment variable `WLPP_RESPONDER_TOKEN`, read at process start |
| Default | **none.** There is no built-in, no fallback, and no value in this repository |
| Unset or empty | `wlpp responder` prints an error and exits **2** without binding a socket |
| Non-ASCII | `wlpp responder` prints an error and exits **2** without binding a socket |

The non-ASCII refusal is not pedantry. `hmac.compare_digest` raises `TypeError` when
either `str` operand contains a non-ASCII character, and that exception escapes the
handler's own error handling entirely — the socket closes with no HTTP response at all. A
single en-dash pasted from a document into the token would therefore have made **every**
request fail that way, the correct token included, silently and permanently for as long as
the process ran. The responder refuses to start instead, loudly, at the source.

**Rotation is not solved here.** Where the token is stored, how it is rotated, and whether
wl.works can hold two during a rotation are open (responder design §12). Today: one token,
one host, changed by editing the environment file and restarting the unit, which drops
in-flight requests and makes the host briefly unreachable — which wl.works renders as
`unknown`, not `down`, and which is why the poll cadence proposed below tolerates several
consecutive misses.

---

## `GET /health`

The endpoint wl.works polls. It takes no body and no query parameters. It only ever reads
— proven by row snapshots rather than by DataJoint's `in_transaction`, which `insert()`
bypasses (`tests/responder/test_health.py::test_build_health_does_not_write`).

**Response `200`**, body validated against
[`docs/schemas/health_response.json`](../schemas/health_response.json):

```json
{
  "verdict": "ok",
  "readings": [
    {"key": "ingested_24h",      "label": "Ingested (24 h)",   "value": "3",                     "featured": true},
    {"key": "quarantined_7d",    "label": "Quarantined (7 d)", "value": "0",                     "featured": false},
    {"key": "stalled_transfers", "label": "Stalled transfers", "value": "0",                     "featured": false},
    {"key": "stuck_jobs",        "label": "Stuck jobs",        "value": "0 stale reservation(s)","featured": false},
    {"key": "disk_headroom",     "label": "Disk headroom",     "value": "4210 GiB free (ok)",    "featured": false}
  ],
  "actions": []
}
```

`verdict`, `readings` and `actions` are all **always present**. `actions` may be empty and
is empty today; a 10a client must accept and ignore the field, so that shipping 10b is not
a breaking protocol change to responders already deployed (Plan 10 §9).

**Readings carry no timestamp of their own.** The observation time is the time wl.works
polled — `lab_host_observation.observed_at`, which is already how Plan 10 §3.2 models it.

### The verdict is three values, not four

wl.works validates `verdict` against exactly `ok`, `degraded`, `down`, `unknown` and
rejects anything else. This host emits **three** of the four:

| Verdict | When this host emits it |
|---|---|
| `ok` | The database is reachable and nothing needs a human. |
| `degraded` | Reachable, but something needs a human: a storage-root scan fault, an unmeasurable disk, stuck jobs, quarantined sessions, stalled transfers, or scratch below the floor. |
| `down` | The database is unreachable. Nothing else could be computed. |
| `unknown` | **Never emitted by this host.** |

`unknown` is **wl.works' word for this host's silence**, recorded on their side when a
host goes quiet past its `lab_host.stale_after_seconds`. A host that is still running and
answering `/health` is by construction never in a position to assert it — doing so would
be claiming knowledge of its own absence. The value stays in the shared enum because their
verdict function produces it; it simply never arrives on the wire from here.

**A `down` verdict arrives as HTTP `200`, not as a `5xx`** — measured directly. That is
deliberate and it matters for Plan 10 §1.3's rule that `down` is reserved for a *positive
observation of failure*: a `200` carrying `"verdict": "down"` **is** the responder
answering and declaring itself faulty. A `500`, a connection refused, or a timed-out poll
is the opposite — no observation at all — and should be recorded as a transport failure
(`lab_host_observation.verdict` NULL, `error` set) and left to age into `unknown`. Reading
a transport failure as `down` would claim a certainty the wire does not support.

### The readings

Emitted in this order, always, except where noted:

| `key` | `label` | `value` |
|---|---|---|
| `ingested_24h` | `Ingested (24 h)` | count of sessions ingested in the last 24 h |
| `quarantined_7d` | `Quarantined (7 d)` | count of sessions quarantined in the last 7 days |
| `stalled_transfers` | `Stalled transfers` | count of sessions incomplete and quiet for ≥ 2 h |
| `walk_fault` | `Storage root scan` | **present only when the storage root could not be fully listed**: `root not fully scanned — <error>` |
| `stuck_jobs` | `Stuck jobs` | `<n> stale reservation(s)`, or `not checked (no schema activated in this process)` |
| `disk_headroom` | `Disk headroom` | `<n> GiB free (ok)` / `<n> GiB free (LOW)`, or `not measured — <error>` |

On the `down` path the array is replaced by a single reading —
`{"key": "database", "label": "Database", "value": "<ExceptionType>: <message>", "featured": true}`
— because nothing else could be computed, and `actions` is hardcoded empty there even if
stages exist: this host has just declared its database unreachable, so publishing a button
that ultimately inserts a request row would be an overclaim.

**Strings are plain text, never markup.** `<`, `>` and `&` are rejected outright in a
`label`, and in producer-supplied `value` text (an exception message, a filesystem path)
they are replaced with the literal substitutes `(lt)`, `(gt)` and `(amp)` before the
response is built. So a storage root genuinely named `A&B` renders as `A(amp)B` rather
than failing the whole response. wl.works escapes what it renders anyway; this host
refuses to emit markup in the first place rather than relying on that.

### `featured`: exactly one, always

Plan 10 §4 settles the ambiguity — *more than one `featured` reading: the first wins* — so
this host emits **exactly one**, on every path including `down`. Publishing two would let
their renderer choose for us, silently.

The featured reading is whichever condition drove the verdict away from `ok`, or the
ingest count when nothing did. When several conditions are bad at once, this priority
order decides, highest first:

1. `walk_fault` — the storage root could not be listed at all
2. `disk_headroom`, because the disk **could not be measured** (not because it read low)
3. `stuck_jobs`
4. `quarantined_7d`
5. `stalled_transfers`
6. `disk_headroom`, because the disk measured **below the floor**
7. `ingested_24h` — the `ok` fallback

The ordering is this host's own call; Plan 10 does not rank them and neither does the
responder design's §5.1. It separates **events** from the one **level**. A low disk can
stay true for weeks, long after the lab already knows about it, so ranking it with the
events would let a known chronic condition permanently occupy the one home-page slot and
keep a fresh acute fault off it. A disk that could not be measured at all ranks as an
event, and above the other events, because an unmeasured disk might already be below the
floor and this host cannot rule that out; a vanished storage root outranks even that,
because when the root is gone the listing and the disk measurement fail from the same
cause, and "Storage root scan" is the more honest description of what broke than "Disk
headroom: not measured".

### `actions`: derived, and empty today

`actions` is empty, and that is the deliverable rather than a placeholder for one. wl.works
renders every published action as a button any lab member can press, so a button that
queues work no stage will pick up for six months teaches people to distrust the surface.

An entry appears when **two hardcoded lists are extended in the same commit**: a computed
table is added to `wl_preproc/daemon.py::_computed_tables()`, and that table's name is
mapped to one of the five domains in `wl_preproc/responder/actions.py::_TABLE_DOMAINS`.
**This is a mapping, not a discovery** — nothing introspects a schema, a dependency graph,
or `populate()`'s key source, and a table present in `_computed_tables()` with no mapping
entry is silently excluded rather than raising, so a bookkeeping gap fails toward
"nothing published" instead of breaking `/health` for every caller.

The five domains and the exact `label` each will carry are already fixed:

| `name` | `label` |
|---|---|
| `behaviour` | `Per-task behavioural analysis` |
| `neural` | `Spike sorting` |
| `export` | `Trim-and-export an NWB to a block subset for DANDI deposit` |
| `atlas` | `@animal_warper atlas registration` |
| `calibration` | `Stimulus calibration from a .spectrashop measurement` |

Sorted alphabetically by `name`, so two polls against the same state render byte-identical
JSON. When a stage lands, its action appears here with **no change in wl.works** — which
is what "the host publishes its own action list" was written to buy.

---

## `POST /jobs`

Request body validated against
[`docs/schemas/job_request.json`](../schemas/job_request.json). Unknown top-level fields
are refused (`extra=forbid`), so adding a field is a protocol change on both sides rather
than something one side can do quietly.

```json
{
  "domain": "neural",
  "selection": {
    "session_datetime": "2027-06-01T09:00:00+00:00",
    "montage_id": 0,
    "block_ids": [1, 2, 3]
  },
  "parameters": {},
  "idempotency_key": "6f1c2f8e-…",
  "metadata": {
    "blocks": [],
    "montage_boundaries": [],
    "probes": [],
    "experimenter": "jwesterberg",
    "subject": "pico",
    "task_types": ["memguided"]
  }
}
```

- **`domain`** is one of the five `name` values from the published `actions` list. The app
  never sends a command string — that distinction from SSH is the whole security argument.
- **`selection`** requires `session_datetime` and `montage_id`. `session_datetime` arrives
  as an ISO-8601 string (JSON has no datetime type); a `Z` suffix, a numeric offset, or no
  offset at all are all accepted, and the value is normalised to naive UTC on this side.
  `block_ids`, when present and non-empty, makes the request a **derivative** activation
  over that hand-picked block set; absent or empty makes it the **canonical** activation
  for `(session, montage)`.
- **`metadata`** is the bundle this host needs from the ELN, and it is the reason this
  protocol works pull-only: everything wl-preproc needs arrives inbound with the request,
  because this host cannot call wl.works to ask. `montage_boundaries` and `blocks` are
  wl.works' own authored records; this host records them **if absent and never overwrites
  them**, since a later request carrying corrected boundaries is wl.works correcting its
  own record, which is its call to make explicitly rather than something to infer from
  whichever payload arrived last.
- **`parameters`** is recorded verbatim as part of the stored request and is read by
  nothing today; the stage that eventually runs the job is what will interpret it. It is
  not inert, though: the whole request payload participates in the key-reuse comparison, so
  **resending the same `idempotency_key` with different `parameters` is a `409`**, not a
  retry.
- **`metadata.experimenter`** becomes the request's `requested_by`. **`metadata.subject`**,
  not any `subject` inside `selection`, is what identifies the session.

**Response `200`:**

```json
{"activation": {"subject": "pico", "session_datetime": "2027-06-01T09:00:00", "montage_id": 0, "activation_id": 0}, "accepted": true}
```

`activation` is the primary key of the `Activation` row this request resolved to.
`activation_id` is `0` for a canonical activation and a positive integer for a derivative.
`session_datetime` is rendered ISO-8601 and is naive UTC.

**`accepted: true` means recorded, not finished.** The responder does not compute. It
writes a Manual-tier request row and the daemon picks it up, exactly as the ingest watcher
does.

### Idempotency, and what the key is for

The key distinguishes **a retry of a request this host already accepted** from **a
genuinely new request**. It is not what prevents a second run; single-flight is structural
on this side (a request whose `(selection, task type)` is already in flight returns the
running one).

Plan 10 §6.1 specifies that the key is *"generated once when the confirmation dialog is
accepted and reused across retries of that same intent, never regenerated per click."*
This host is built around that exactly:

- The **same key with the same content** returns `200` with the same `activation` — every
  time, however many retries. A genuine retry is a `200`, not a `409`.
- The **same key with materially different content** — a different domain, origin,
  requester, payload, or selection — is `409`. See below.
- **A different key naming the same selection** deduplicates: it returns the existing
  activation rather than starting a second run.

An aborted request is safe to repeat. If wl.works' client times out and retries with the
same key, and this host had in fact already accepted the first attempt, the retry returns
that same activation.

---

## Status codes

Every code this host can return, on either endpoint.

| Code | Endpoint | Body | Meaning | Retryable? |
|---|---|---|---|---|
| `200` | both | the response above | Accepted, or health served. A `down` verdict is a `200`. | — |
| `400` | both | `{"error": "bad request"}` | Malformed request line, or an unparseable version such as `HTTP/9.9.9`. See the framing note below. | No — fix the client |
| `401` | both | `{"error": "unauthorized"}` | Missing, wrong-scheme, or wrong token; or a verb neither endpoint answers. | No — fix the credential |
| `404` | both | `{"error": "not found"}` | Path is neither `/health` nor `/jobs`. | No |
| `405` | both | `{"error": "method not allowed"}` | Known path, wrong verb — `GET /jobs`, `POST /health`, authenticated `PUT /health`. | No |
| `408` | `POST /jobs` | `{"error": "request timed out"}` | The declared body never fully arrived. | **Yes** |
| `409` | `POST /jobs` | `{"error": "<what differed>"}` | Idempotency key reused for materially different content. | **No — needs a human** |
| `414` | both | `{"error": "request line too long"}` | Over-long request line. | No |
| `422` | `POST /jobs` | `{"error": "…"}` or `{"error": "invalid request body", "detail": […]}` | The request is malformed, or asks for something this host cannot do — **including naming a session it has not ingested yet**. | No — fix and resend; for a not-yet-ingested session, resend once the transfer lands |
| `431` | both | `{"error": "request header fields too large"}` | Oversized header. | No |
| `500` | both | `{"error": "<ExceptionType>: <message>"}` | This host's own fault, infrastructure included. | **Yes** |
| `505` | both | `{"error": "http version not supported"}` | `HTTP/2.0` or later. See the framing note below. | No |

**The framing note**, stated rather than left to be discovered. On the paths where the
request line itself could not be parsed — a malformed request line, and any unparseable or
unsupported version string — the response carries **no status line and no headers**, only
the JSON body. The request version is still the `HTTP/0.9` placeholder at that point, and
the standard library's status-line and header machinery are both no-ops for HTTP/0.9. So
`400` and `505` are the codes this host *means* on those paths, and neither reaches the
wire as a number: a client sees a bare JSON body and should read that as a client-side
framing error rather than as a transport failure. `414` and `431` are not affected — those
arrive as ordinary responses with a status line, as does every other code above.

### `422` is the caller's mistake

`422` covers everything wrong with what was sent, and it never carries a traceback:

- **Bad JSON**, or a body that is not valid UTF-8 → `{"error": "<parser message>"}`.
- **JSON that fails the `JobRequest` schema** → `{"error": "invalid request body",
  "detail": [ … ]}`, where `detail` is pydantic's own error list verbatim (with its
  documentation links stripped), including `extra_forbidden` for an unknown field.
- **A well-formed request this host refuses**: a `selection` missing `session_datetime` or
  `montage_id`; a `session_datetime` that is not parseable ISO-8601 or is before year 1000;
  an oversized `subject`; **a session this host has not ingested yet** (see below); a
  `montage_id`, `block_id`, `task_type`, `works_block_id`, `start_s` or `end_s` that will
  not fit its column; a `montage_id` with no boundary on record and none supplied in the
  request either; a `block_ids` entry naming no block anywhere; or a `block_ids` entry
  naming a block outside its montage's `[start_s, end_s)` window. The message names what
  was wrong.

**A session this host has not ingested yet is a `422`, and it is the ordinary case.** You
know a session exists from the ELN the moment it is created; this host knows it exists only
once its data transfer has landed and been ingested. Between those two moments — routinely
hours, sometimes overnight — a job posted for that session is refused:

```json
{"error": "session Sam/2027-06-15T09:00:00 is not yet on record on this host: …"}
```

The full message, which is one line on the wire:

> session `<subject>`/`<session_datetime>` is not yet on record on this host: no Session row
> exists for it, so there is nothing to attach a montage, a block or a request to. wl.works
> knows a session exists from the ELN before its data transfer lands here; until ingest has
> landed it, this host cannot accept a job for it. Resend once the transfer has completed.

The subject and the session datetime are always named, so this is renderable as **"that
session has not arrived on the lab host yet"** rather than as "invalid request". It is a
`422` and not a `409` on purpose: `409` here means *stop and tell a person*, and nobody
needs telling — the situation resolves itself when the transfer completes. Resending later
is the correct client behaviour; resending immediately, in a tight retry loop, is not, and
that is exactly why this is not a `500`.

**A rejected request leaves nothing behind.** Every check runs against candidate rows
before anything is written, so a request refused over a bad boundary does not plant that
boundary, and the corrected request that follows succeeds. That holds for the not-yet-
ingested case too: the check runs before the first write, so nothing is left half-created
for the real ingest to collide with when the transfer does land.

### `Content-Length` rules

`POST /jobs` validates the header itself before reading a byte:

| Header | Result |
|---|---|
| Absent | Treated as `0`; the empty body then fails JSON parsing → `422` |
| Plain digits, `0` … `10000000` | Accepted; body read |
| Anything not all digits — `-1`, `1_2`, `+5`, `1.0`, `0x10` | `422` — `{"error": "Content-Length '-1' is not a valid non-negative integer"}` |
| Digits above `10000000` | `422` — `{"error": "Content-Length 10000001 exceeds the 10000000-byte limit"}` |
| Plain digits within the cap that **overstate** the body | `408` after 30 s (see below) |

The digits-only rule is not fussiness about grammar. `int("-1")` succeeds and
`read(-1)` reads until the peer closes, which a live client never does; `int("1_2")` is
`12`, silently reinterpreting a header that is not valid HTTP at all. Both used to hang a
handler thread forever. The 10 MB cap is far larger than any legitimate body — a real
request's metadata is a few hundred small JSON entries — and it bounds **memory only**.
What bounds thread occupancy is the timeout.

### `408` is retryable

The responder sets a **30-second socket timeout** on every request. If a `POST /jobs`
declares a `Content-Length` whose bytes never fully arrive — a dropped connection
mid-upload, a lying header, a client that stalled — the read gives up at 30 s and this host
answers:

```
HTTP/1.0 408 Request Timeout
{"error": "request timed out"}
```

**`408` means the request never fully arrived, so nothing was done with it.** No row was
written, no job was queued, no key was consumed. It is safe and correct for wl.works'
retry loop to resend the identical request with the identical idempotency key, and the
retry is the right response — this is a transport hiccup, not a rejection. It is
`408` specifically, and not `500`, so it can be told apart from a host-side fault: RFC 9110
§15.5.9 defines it for exactly this case.

The timeout scopes strictly to the body read. A timeout raised anywhere downstream —
inside the database write, say — is a `500`, not a `408`, so `408` never claims a body
arrived late when in fact it arrived completely and something else was slow.

### `409` is not retryable, and that is the point

`409 Conflict` has exactly one cause on this host: **an idempotency key was reused for
materially different content.** The request cannot succeed as sent, and the remedy is a
*new key*, which Plan 10 §6.1 puts outside the retry loop's power to produce — the key is
minted once when the confirmation dialog is accepted and reused across every retry of that
intent, never regenerated per click.

So a retry loop that treats `409` as transient will resend the identical key, hit the
identical refusal, and do so forever. **The right client behaviour is to stop retrying and
tell a human** — reopen the confirmation dialog, which mints a fresh key — rather than to
back off and try again. RFC 9110 §15.5.10 describes it exactly: *"the request could not be
completed due to a conflict with the current state of the target resource… the user might
be able to resolve the conflict."*

**The body names what differed and states the remedy**, so it can be shown to that human
rather than paraphrased. It enumerates whichever of `task_type`, `origin`, `requested_by`,
`payload` and `selection` disagree with what is already recorded under that key, and ends
with the sentence `Mint a new idempotency key.`

Because a genuine retry carries the same key **and the same content**, it is a `200`, not
a `409`. A client following Plan 10 §6.1 will never see `409` from an honest retry; it sees
it when two different intents were given one key.

### `500` **is** retryable, infrastructure faults included

Everything that is this host's own fault is a `500` with a clean JSON body naming the
exception type and message — never a traceback, under any input:

```json
{"error": "LostConnectionError: <the database driver's own message>"}
```

**A lost database connection is a `500`, on purpose.** `POST /jobs` writes inside a
transaction, so a MySQL restart or a LAN blip mid-request raises a connection error on the
production path. Those are faults a retry clears, and the retry loop is exactly where they
belong. Every DataJoint error other than key reuse — a lost connection, an access error, a
missing table, an integrity error — is a `500` here, and only key reuse is translated to
`409`.

**One integrity error used to reach here and no longer does.** A job naming a session this
host has not ingested yet violated the foreign key on the first write, and that arrived as
a `500` — which this section tells you to retry, for a request that could not succeed until
a transfer completed. It is now caught before any write and answered `422`; see *`422` is
the caller's mistake* above. Nothing else about this section changed, and no other integrity
error is known to be reachable from a well-formed request: if you see one, it is genuinely
this host's fault and a retry is genuinely the right response.

An earlier draft of the implementation mapped the whole family to `409`, which told
wl.works to stop retrying and escalate to a human for the one class of fault a retry would
have cleared on its own; that is fixed, and the pairing above is the reason the two codes
are worth keeping distinct in the client.

The short version of the distinction, which is the single most useful thing in this
document for whoever writes that client:

> **`408` and `500`: retry.** **`409`: stop, and tell a person.** **`4xx` otherwise: fix
> the request or the credential; retrying changes nothing.**

---

## The three numbers

Plan 10 defers poll cadence, request timeout and the per-host refresh cooldown to
environment configuration and gives no value for any of them. A protocol document with no
timing in it is what leaves two sides guessing, so each is proposed here with its
reasoning. They are proposals, not requirements: wl.works owns all three, because all
three describe how hard the app is willing to push rather than a property of this host.

### Poll cadence: **60 s**

The scheduled sweep interval (Plan 10 §5.1, global environment configuration).

`lab_host.stale_after_seconds` defaults to **300**, and that is the only thing that turns
silence into `unknown`. At 60 s this host must miss **five consecutive polls** before its
card blanks, so a responder restart, a token rotation, or one dropped health check never
blanks the board — while the home page's featured reading is never more than a minute old.
Going faster buys nothing: no reading here changes on a shorter timescale than that (the
ingest window is 24 h, the quarantine window 7 days, the stall threshold 2 h). Going
slower widens the gap between "the transfer finished" and the dashboard saying so, which
is the question the feature exists to answer.

### Request timeout: **10 s** for `GET /health`, **30 s** for `POST /jobs`

The hard timeout on each outbound call (Plan 10 §5.3 layer 3, §5.5). If only one constant
is available, use **30 s**.

`GET /health` is not free. It runs three database queries, lists the storage root, reads one
manifest per session directory, runs one `statvfs`, and — for each session still
**incomplete** — walks that session's tree for its newest mtime. On a host mid-transfer,
over NAS storage, that walk is the term that can take seconds. **All of it runs under the
same process-wide lock `POST /jobs` takes**, so a health check can also wait behind a job
write. 10 s gives the realistic case an order of magnitude of headroom while staying a
sixth of the poll cadence, so even a fully hung host never leaves two health checks
outstanding at once.

`POST /jobs` gets the longer bound because truncating it is worse than waiting: it does
real database work, behind that same lock, and a client that gives up early has learned
nothing about whether the write happened. 30 s also lines up with this host's own
30-second socket timeout, so the two sides give up on the same scale rather than one
silently outliving the other. Aborting early is nonetheless safe — the idempotency key
makes the retry converge on the same activation.

### Per-host refresh cooldown: **30 s**

The floor between on-demand refreshes of one host (Plan 10 §5.5). A health check newer
than this returns the existing observation instead of making a call.

The abuse profile is a member holding down the refresh button, and this number is the only
thing on the app's side that bounds it. At 30 s against a 60 s sweep, the worst case a
leaned-on button can produce is **twice** the scheduled load on this host rather than a
multiple of it — which matters more here than it would for a passive monitor, because
health checks contend with `POST /jobs` for the one lock. It stays short enough to answer
the question the feature was requested for: a member who clicks refresh, waits half a
minute and clicks again gets a genuinely fresh look rather than the same cached
observation.

---

## Running the responder

```
wlpp responder --port PORT --root ROOT [--prefix PREFIX]
```

| Flag | Required | Default | Meaning |
|---|---|---|---|
| `--port` | **yes** | **none** | TCP port to bind, 0–65535. An out-of-range value is refused by argument parsing, not forty frames deep in the socket layer. `0` is legal and asks the OS for an ephemeral port — useful in tests, never in a unit file. |
| `--root` | **yes** | **none** | The storage root holding session directories. `GET /health` reads it — the disk, stall and scan readings are all about this directory. |
| `--prefix` | no | `wlpp_` | The schema prefix. |

`WLPP_RESPONDER_TOKEN` must be set in the environment. There is no `--token` flag, so the
token never appears in a process listing or a shell history.

### The port must be written identically in three places

The systemd unit, this document, and whatever wl.works is configured with. That is why
`--port` has **no default**: a default invites two of the three to disagree silently, and
the failure is a host that looks permanently `unknown` on the dashboard while running
perfectly well on a port nobody is asking.

**Proposed: `8420`.** It is above 1024, so the unit needs no `CAP_NET_BIND_SERVICE` and can
run as an unprivileged user; it is below the Linux ephemeral range floor
(`net.ipv4.ip_local_port_range`, default 32768–60999), so the kernel can never hand it out
as an outbound source port and steal it from a restarting responder; and it is unassigned
in the IANA service-name registry (checked against `/etc/services`, where its neighbours
8417 and 8443 are assigned and 8420 is not). Confirm once, at install time, that nothing
else on the host already listens there — then write it in all three places.

### A systemd unit

Illustrative rather than prescriptive — which supervisor runs this host's services is an
open ops decision (Phase 1c-1 design, "Who runs the daemon"). What is not optional is
where the port and the token come from.

```ini
[Unit]
Description=wl-preproc responder for wl.works
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=wlpp
EnvironmentFile=/etc/wlpp/responder.env
ExecStart=/opt/wlpp/venv/bin/wlpp responder --port 8420 --root /srv/sessions
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

`/etc/wlpp/responder.env` holds `WLPP_RESPONDER_TOKEN=…` and should be mode `0600`, owned
by root. Use `EnvironmentFile=`, **not** `Environment=`: values written inline in a unit
file are visible to any local user through `systemctl show`.

The responder also needs whatever DataJoint configuration the rest of the pipeline uses;
`wlpp doctor` is what checks that the database is reachable from this host at all.

### Exit codes

Re-derived by running the shipped CLI, one subprocess per row, rather than read off the
source. `2` means **refusing to start**: the invocation or the configuration is wrong and
nothing was bound. `1` means it tried and the OS said no.

| Code | Cause | What the journal shows |
|---|---|---|
| `2` | `WLPP_RESPONDER_TOKEN` unset, or empty (`WLPP_RESPONDER_TOKEN=` in the unit file) | `error: WLPP_RESPONDER_TOKEN is not set (or is empty); refusing to start a responder with no token -- there is no default.` |
| `2` | `WLPP_RESPONDER_TOKEN` containing a non-ASCII character | `error: refusing to start: the configured bearer token contains a non-ASCII character; …` — it would fail every request, the correct one included |
| `2` | `--root` naming a path that does not exist, names a file, or is only whitespace | `error: --root '/srv/sesions' does not exist or is not a directory; refusing to start a responder with a broken storage root.` |
| `2` | `--root=` with nothing after it — `--root=${WLPP_ROOT}` with `WLPP_ROOT` unset expands to exactly this | the same message, with `''` — **not** the working directory, which is what an earlier version silently served |
| `2` | `--port` outside 0–65535 | `wlpp responder: error: argument --port: 99999 is not a TCP port (must be 0-65535)` |
| `2` | `--port` not an integer | `wlpp responder: error: argument --port: invalid tcp_port value: 'abc'` |
| `2` | `--port` or `--root` absent; an unrecognised flag; no subcommand | argparse's own message — `wlpp responder: error: the following arguments are required: --port` |
| `1` | The bind failed | `error: responder failed on port 8420: <the OS's own errno text>` — measured as `[Errno 48] Address already in use` on the development machine; Linux reports that same condition as errno 98. A restart racing the previous process is the usual cause; a privileged port with no capability to bind it arrives on this same path |
| `0` | Only if `serve_forever()` returns, which nothing in this CLI asks it to | — in practice the process ends on a signal instead |

Checked in that order: argument parsing first, then the token, then `--root`. Two things
wrong at once reports the first of those three, not both.

No row prints a traceback. Every one is a single line an operator reads in a journal.

One measured oddity worth knowing if you script this CLI rather than running it from a
unit: **`wlpp responder --help` prints its help and exits `2`, not `0`.** Nothing about
starting the responder depends on it, and it is recorded here rather than fixed in this
wave.

---

## What this protocol does not carry

Declined rather than skipped, so a later revision overturns a decision instead of
discovering an option.

- **No job status endpoint.** `POST /jobs` returns an activation key; there is no
  `GET /jobs/<id>`, and nothing calls back when work finishes. Today a submitted job's
  progress is not observable through this protocol at all. When it becomes observable it
  will be as a **reading**, because readings are the surface this host already publishes
  and wl.works already polls — not as a new endpoint.
- **No result upload.** wl.works pulls; this host never pushes. Rows 27 and 29 already
  discover their outputs by polling the NAS.
- **No TLS.** Plan 10 §5.4 makes plain HTTP the stated default for this leg and argues it:
  the call never leaves the building, and an internal CA or self-signed certificates on
  every lab appliance is real friction for confidentiality against an attacker already
  inside the LAN. If this endpoint ever becomes reachable from outside the lab, TLS and a
  reverse proxy in front of it are the precondition, not an improvement.
- **No permission model on this host.** wl.works' is flat by design; inventing per-action
  roles here would put this host in the business of deciding who in the lab may do what.
  The bearer token is an authentication boundary, not an authorisation one: it separates
  wl.works from everything else on the LAN, and does not separate lab members from each
  other. That is what makes the most prominent line at the top of this document load-bearing.
- **No rate limiting.** One known client, with a cooldown on its own side.
- **No `Retry-After` header.** The retry policy lives in wl.works' client; this host does
  not attempt to schedule it.

---

## Machine-readable schemas

`wlpp schemas export` writes JSON Schema for both wire contracts, and CI diffs the
directory so a drifted export fails the build:

- [`docs/schemas/health_response.json`](../schemas/health_response.json)
- [`docs/schemas/job_request.json`](../schemas/job_request.json)

These are what wl.works' contract tests should validate against, and they are why this
protocol needed no OpenAPI-generating web framework on the box that holds every session's
raw recordings.

---

## Provenance

Written 2026-08-16 against the shipped responder in `wl_preproc/responder/`
(`handler.py`, `server.py`, `health.py`, `actions.py`, `jobs.py`),
`wl_preproc/contracts/protocol.py`, and `wl_preproc/cli/main.py`. Every status code, header
and error body above was measured against a real `ThreadingHTTPServer` running the shipped
handler, not read off a design document. Design: [Phase 1c-3
§7](../superpowers/specs/2026-08-15-phase-1c3-responder-design.md).

**Proposed to wl.works, not written into it.** If they accept it, the natural home is
`docs/ops/lab-host-protocol.md` in that repository, with this copy kept in step. Amendments
to their documents are staged in [`docs/pending-wl-works-amendments.md`](../pending-wl-works-amendments.md)
in the append-only form that file establishes, and are applied by hand after
re-verification — never unilaterally.
