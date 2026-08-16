# Phase 1c-3 — the rulings, and what 1c-4 inherits

**Written 2026-08-16**, at the close of the responder's implementation. Ten tasks, 37 commits,
suite 473 → 593, zero warnings throughout.

This is the durable half of the execution ledger, which was scratch and is now deleted. It records
the decisions taken without asking, each with what it costs if it was wrong, and the defect classes
that produced most of them. It exists because a decision made on someone's behalf that they never
see is a decision made in secret — and because 1c-4 will meet the same classes.

The pattern worth reading first is §3.1: **three fix rounds each closed a demonstrated escape and
left the next one live.** That is not a story about an AST walk. It is what happens whenever a rule
is derived correctly and then applied one case too narrowly.

---

## 1. The rulings that changed what was built

**`MetadataBundle` carries `blocks` and `montage_boundaries` inbound, with every request.** This
dissolved a precondition two sub-projects had worked around: `Activation` has a hard foreign key to
`Montage`, which the responder cannot measure and may not fetch from wl.works. Answering it inbound
means the responder never needs a montage source it cannot have. The residual — an automatic
canonical trigger — is the only part still open. *Cost if wrong: a request shape wl.works must
change again.*

**A key reused for materially different content is `409`; everything else is `500`.** The first
attempt caught `dj.DataJointError` at the translation seam, which is the root of a wide tree — so a
`LostConnectionError` from a MySQL restart mid-`POST` answered `409`, whose documented meaning is
*"the remedy is outside the retry loop, tell a human"*. A conforming client would have stopped
retrying a fault a retry clears. Now a dedicated `KeyReuseError` is raised at the one site and caught
by exactly that type. *Cost if wrong: one new exception class in schema code; every existing
`except dj.DataJointError` still catches it.*

**A job for a session this host has not ingested is `422`, not `500` and not `409`.** `accept()` does
not own session existence, so the foreign key used to raise `IntegrityError` → `500` — which the
protocol document documents as **retryable**. Three separately-correct decisions composed into a
client that retries forever. 422 is literally accurate (understood the syntax, cannot process the
instruction), needs no new status on the wire, and `accept()`'s `ValueError` family already carried
it. 409 was the tempting alternative and loses because it would force wl.works to parse messages to
tell key reuse from a not-yet-arrived session. **The trigger is ordinary: wl.works knows a session
exists from the ELN hours before the transfer lands.**

**`session_datetime` is truncated to whole seconds, in one place, inside `to_naive_utc`.** The 422
fix above compared in Python, before the type coercion MySQL used to do — and `Session.session_datetime`
is a second-resolution `DATETIME`, so any fractional part could never match. `Date.prototype.toISOString()`
*always* emits milliseconds, so the most likely client implementation would have hit it on every
request. Truncate rather than reject (a sub-second component is the client's serialisation artefact,
not a different session); truncate rather than round (MySQL rounds half-up, but once the value is
normalised before both the lookup and the insert, MySQL never sees a fractional value). The same
asymmetry sat one module over in `already_ingested`, where it would have made a landed session look
unlanded forever — two call sites, one defect, one fix.

**The responder's single lock is load-bearing for correctness, not only for thread safety**, and it
is held for the whole call and released when the callable raises. A leaked lock wedges every later
request permanently.

**`unknown` is never emitted.** It is wl.works' word for our silence, not a verdict this host can
render about itself. A `down` verdict is served as HTTP **200** — a positive observation of failure —
so a 500 or a timeout is no observation at all and must age into `unknown` on their side.

**The outbound-connection guardrail is AST-based, not a substring scan.** Settled by measurement:
substring 12/16 verdicts wrong, AST 0/16. The false negatives matter more than the false positives
(`import requests as rq` sails through a substring scan), and the false positives have a perverse
shape — a comment reading *"we deliberately do not use aiohttp here"* trips the rule forbidding
aiohttp. **The guardrail would forbid its own documentation.**

**`--port` is required with no default.** The port must be written identically in the systemd unit,
the protocol document and wl.works' configuration; a default invites two of those three to disagree
silently.

## 2. Rulings about scope and process

- **`docs/ops/lab-host-protocol.md` is proposed, not agreed.** wl.works cites it from 11 places
  across 7 documents (the spec says "five"; that figure was never re-derived). Two of its four
  "what changes on your side" items — the bearer token and `408` — are real work for them.
- **The guardrail's chase was capped deliberately.** After three rounds it ships as a *tripwire
  against accidental addition* with its limits documented, not as a sandbox. A static source check
  cannot be a security boundary, and whoever adds a convenient callback months from now writes
  `import requests`, not `vars(http)["client"]`.
- **The scan covers the whole `wl_preproc` package**, not just `responder/` — measured free at 47
  files. `build_health` runs `cli/report.py` code on the request path, so a directory-scoped rule
  would have been true for a directory rather than for the property.
- **Two guards were moved rather than duplicated** (the non-ASCII token check into `make_handler`;
  `_QUARANTINE_WINDOW_DAYS` read from its owner). A second copy is a second place for it to go
  missing — 1c-2's §3.2 finding.
- **Dead code was deleted rather than pinned.** `visit_Call`'s flagging became unreachable once
  `visit_Attribute` existed; adding a test to pin dead code is worse than removing it.

## 3. The defect classes, which are the actual handoff

### 3.1 A rule derived correctly and applied one case too narrowly

Three fix rounds on the guardrail, three live escapes, each proven end to end against a real
listener — outbound `204`, receipt confirmed, guardrail reporting `1 passed`:

1. `import http` + `http.client.HTTPConnection(...)` — the walk judged `Import` statements only.
2. `client = http.client` on its own line — the walk visited `Call` and never `Attribute`.
3. `getattr(http, "client")`, `sys.modules[...]`, `vars(http)[...]`, a list element — chains through
   an expression's *return value*.

**The reason round 2 missed round 3 was written in round 2's own prose.** Its docstring argued that
rebinds are "all caught one statement earlier, at `import socket`, which is the entire argument for
the flat ban" — true for the one module banned outright, false for `http.client`, which needs only
`import http`. The next paragraph said exactly that, and then narrowed the consequence to one node
type.

What ended it was changing *what is forbidden* instead of *which node types are visited*: ban binding
the parent **name** of any forbidden dotted module. Every escape needed `http` bound; `from http.server
import X` does not bind it, and that is the only form the package uses. **When a fix is the third of
its kind, the defect is the rule's shape, not its coverage.**

### 3.2 Prose asserting what the code does not support — seventeen instances

Five were in documents the controller wrote, including one in the brief that the final fix wave
worked from. The sharpest:

- An assertion naming `"Exception happened during processing of request"` — **Python 2's**
  `SocketServer.py` wording. Zero hits in either CI Python. The assertion could not fail under any
  input, while its own docstring called it the live marker.
- A docstring counted "17 `pytest.raises` call sites"; it is 16, because a `grep` counted a mention
  inside a docstring. Only AST parsing counted calls. This happened *in a paragraph that had already
  been through a "derive, don't guess" correction*.
- A "silently truncates" claim about a `digest_size` bump. Measured: MySQL 8's `STRICT_ALL_TABLES`
  raises `DataError (1406)` — loud. And the genuinely quiet side is the *other* column:
  `selection_hash`'s `varchar(64)` absorbs the bump, so every new hash stops matching stored 32-char
  ones and `submit_derivative` allocates a second activation for a selection already in flight.

**A number or a behaviour claim in a docstring is a claim. Re-derive it against the code that ships.**
And prefer re-deriving a whole table to patching three of its rows — that caught a defect three
separate times this phase.

### 3.3 Tests that pass while proving nothing — twelve instances

Three had *names* asserting properties they did not check. Shapes worth knowing:

- **A test whose exit code the program also produces.** `test_responder_requires_a_port_argument`
  asserted `returncode == 2` and no traceback — which the branch's own token refusal satisfies.
  Giving `--port` a default left it passing.
- **A test that reds by timeout.** A hang is not a failure: it burns a CI job's whole budget instead
  of redding one test. Every subprocess helper needs a `timeout=`.
- **A structural check a concurrency probe cannot replace.** Under the two-separate-locks mutation
  the concurrency proof still *passed*; only the structural identity check caught it.
- **Visited is not pinned.** An `_ALLOWED` row that the walk now reaches is an improvement on
  structurally invisible, and still fails under no mutation.

The habit that finds these: **mutate, don't read.** Revert the fix, confirm the test fails, restore.

### 3.4 Cross-artifact staleness

The protocol document's exit-code table listed one exit-2 cause while three others existed —
because the document shipped two commits before the checks that added them, *and the `--root` check
was added with the stated reasoning that the document tells an operator to write a `--root` into a
systemd unit.* Then a one-line CLI fix made the same table wrong again, one commit later.

**Fixing code without sweeping the document that describes it produced this three times.** Anything
that changes a status code, an exit code or a message needs `docs/ops/lab-host-protocol.md` opened in
the same commit.

### 3.5 `in_transaction` is not a read-only check — the fifth reminder

DataJoint's `insert()` calls `connection.query()` directly and never touches the transaction
machinery, so `in_transaction is False` is equally true of a writing function and a reading one. To
prove a function does not write, snapshot rows. `tests/conftest.py` has session-scoped
`table_snapshot` and `deep_equal`.

## 4. What 1c-4 specifically inherits

- **`GET /health` holds the one process-wide lock while doing a recursive mtime walk per *incomplete*
  session, and `POST /jobs` takes that same lock.** Health polling and job submission genuinely
  contend, and the proposed 10 s health timeout is exposed to exactly that walk. On a slow NAS with
  several sessions landing it could exceed the timeout and make a healthy host look flaky. The
  protocol document names the walk as the dominant term so the number can be **re-derived on real
  hardware rather than re-guessed**. Revisit before anyone raises the poll rate.
- **No job-status endpoint exists.** `POST /jobs` returns an activation key and nothing can ask what
  became of it. The document asserts the answer will be a *reading* rather than a new endpoint — a
  forward-looking design claim a later phase could reasonably overturn.
- **`count_stale_jobs` reads DataJoint's internal `~jobs` tables**, which the report's
  write-detection snapshot does not cover. Harmless while this project declares zero
  Computed/Imported tables — **1c-4's timebase stage is the first to add one.**
- **`selection_hash` and `content_hash` are deliberately separate** (`ingest/params.py:174` refuses
  the merge by name), and their columns are asymmetric: `varchar(32)` versus `varchar(64)`. Whoever
  changes `digest_size` meets §3.2's finding at both sites.
- **Adding a `QUARANTINE_REASONS` member needs a migration** on any deployed schema;
  `activate(create_tables=True)` will not `ALTER` an enum column.
- **element-animal declares `subject : varchar(8)`.** Eight characters, still constraining the lab's
  animal naming convention, still cheaper to decide before animals are registered.
- **The venv's `.pth` files get macOS-hidden by a file-sync tool**, and CPython 3.11+ silently skips
  hidden `.pth` files — so the bare `wlpp` command breaks while `python -m pytest` from the repo root
  stays green. `ls -lO`, then `chflags nohidden`; a reinstall does not help. It recurs within minutes.

## 5. Parked, shipped knowingly

- **A response write to a peer that has gone away prints a full traceback to stderr**
  (`BrokenPipeError`/`ConnectionResetError`, likeliest for a peer silent for 30 s). Pre-existing on
  every write path; the comment now says so honestly rather than claiming stdlib catches it. Fixing
  it means a write guard on every `_send_json` path.
- **The guardrail's documented escapes**: every stdlib module that imports `socket` re-exports it, so
  a legal `import socketserver` reaches `socket.create_connection`; likewise `import http.server as
  hs`. Also `__import__`, `subprocess` + curl, and expression-bound modules. Named in the docstring
  with reasons, deliberately not chased. No `_ALLOWED` row blesses them — an `_ALLOWED` row would
  assert a shape is *intended* rather than merely unreached.
- **`--port '1_0'`, `' 80 '` and `'+80'` are accepted** by `int()` semantics — the same underscore
  trap closed for `Content-Length`, left open here because that was remote input on a security
  surface and this is local operator input in a file the operator is reading.
- **The privileged-port errno is unmeasured.** As a non-root user this development machine bound port
  81 without complaint. The document quotes `[Errno 48]` as the dev machine's and states Linux's 98
  as a claim about Linux. **Someone on the Linux host should confirm it once.**
- **`quarantine()`'s `session_dt` now truncates and no test exercises it** — best-effort provenance
  on a row not keyed by it, into a column that could never hold sub-second anyway.
- **`_QUARANTINE_WINDOW_DAYS`'s leading underscore now overstates its privacy**; it has a
  cross-module consumer. Free to rename whenever that file is next opened.
- **Design spec §6.3 and §6.4 remain open by design**: `accept()` writes `Block.start_s`/`end_s`,
  which `core.Block` specifies as wl-preproc's *measurement* — owned by whoever builds the decoder;
  and validate-before-write and never-overwrite cannot both hold without an undo.
