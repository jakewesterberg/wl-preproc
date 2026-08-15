# Phase 1c-3 — Responder, action list, and protocol document: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the HTTP surface wl.works polls — health, a derived action list, and a job endpoint that turns a `JobRequest` into Manual-tier rows — plus the derivative activations 1c-1 deferred here and the protocol document neither repository has written.

**Architecture:** `http.server.ThreadingHTTPServer` with hand-written dispatch, no web framework. Two endpoints, both requiring a bearer token. Database work serialises behind one lock because DataJoint connections are not shareable across threads. The health readings are extracted out of `cli/report.py` so the report and the responder share one computation rather than growing two that can disagree.

**Tech Stack:** Python ≥3.11 stdlib `http.server`, pydantic v2, DataJoint 2.3.x, pytest, testcontainers[mysql].

**Spec:** [`docs/superpowers/specs/2026-08-15-phase-1c3-responder-design.md`](../specs/2026-08-15-phase-1c3-responder-design.md) — read it before Task 1.

## Global Constraints

- Python `>=3.11`; CI runs **3.11 and 3.13**. All five git dependencies are commit-pinned in `pyproject.toml` — do not change a pin. **No new runtime dependency** (§4.1).
- **Never a bare `longblob` — always `<blob>`.** `tests/schema/test_guardrails.py` auto-discovers schema modules and sweeps them.
- **No bare `.delete()` in `wl_preproc/`** (`.delete_quick()` permitted).
- `.fetch()` is deprecated — `.to_arrays()`, `.to_dicts()`, `.keys()`, `.fetch1()`.
- **One schema prefix per process.** **0 warnings** — suite is at 374.
- **Nothing in `responder/` may open an outbound connection.** §11.1 of the parent spec is not negotiable; Task 9 makes it a test.
- Test subjects ≤ 8 characters (element-animal declares `subject : varchar(8)`) and must not collide with `tests/schema/test_core.py`'s `(pico, 2027-03-14 09:00:00)`.
- Verify filesystem behaviour with real `os.chmod`, never `monkeypatch.setattr(Path, ...)`; restore in a `finally` **before** any assertion.
- **`in_transaction` is not a read-only check** — DataJoint's `insert()` bypasses the transaction machinery. To prove something does not write, snapshot rows.
- Run tests as `.venv/bin/python -m pytest`; **never** `.venv/bin/pytest`. `-W error` dies at the datajoint import — grep the summary.

## File structure

| File | Responsibility |
|---|---|
| `wl_preproc/responder/health.py` | Verdict and readings. Reads only. |
| `wl_preproc/responder/actions.py` | Derives the action list from the computed stages that exist. |
| `wl_preproc/responder/jobs.py` | `JobRequest` → rows. The only module here that writes. |
| `wl_preproc/responder/handler.py` | Routing, auth, status codes. Imports no DataJoint. |
| `wl_preproc/responder/server.py` | `ThreadingHTTPServer` wiring and the lock. |
| `docs/ops/lab-host-protocol.md` | The shared contract document. |

---

### Task 1: Extract `gather_readings` out of `build_report`

Spec §5.2. `cli/report.py` computes ingested/quarantined/stalled/stuck/headroom and returns **Markdown**. The responder needs the values. Two computations of "how many sessions are stuck" that can disagree is a defect this project has found in four shapes.

**Files:**
- Modify: `wl_preproc/cli/report.py`
- Test: `tests/cli/test_report.py`

**Interfaces:**
- Produces: `gather_readings(root: Path, prefix: str = DEFAULT_PREFIX, now: datetime.datetime | None = None) -> Readings`, where `Readings` is a frozen dataclass with `ingested: list[dict]`, `quarantined: list[dict]`, `stalled: list[tuple[Path, list[str]]]`, `stale_jobs: int | None`, `free_gib: int`, `headroom_ok: bool`, `root_error: str | None`, `at: datetime.datetime`.

- [ ] **Step 1: Write the failing test**

Add to `tests/cli/test_report.py`:

```python
def test_gather_readings_returns_the_values_build_report_renders(scanned):
    """The extraction's whole point: one computation, two renderings. If these
    two disagree the responder and the daily report will report different
    numbers for the same question, which is the defect the doctor/report
    headroom extraction already caught once in this project."""
    root, prefix = scanned
    from wl_preproc.cli.report import build_report, gather_readings

    readings = gather_readings(root, prefix=prefix)
    body = build_report(root, prefix=prefix)

    assert f"Ingested (24 h) — {len(readings.ingested)}" in body
    assert f"Quarantined (7 d) — {len(readings.quarantined)}" in body
    assert f"Stalled transfers — {len(readings.stalled)}" in body
    assert f"{readings.free_gib} GiB free" in body


def test_gather_readings_does_not_write(scanned):
    """Same guarantee build_report carries. `in_transaction` cannot detect a
    write here — DataJoint's insert() never touches it — so this snapshots
    rows, exactly as test_the_report_opens_no_write_transaction does."""
    root, prefix = scanned
    from tests.conftest import deep_equal, table_snapshot   # moved in step 3
    from wl_preproc.cli.report import gather_readings
    from wl_preproc.schema import core, ingest, pipeline

    watched = [ingest.Ingestion, ingest.Quarantine, pipeline.Session, core.AcquisitionSystem]
    before = [table_snapshot(t) for t in watched]
    gather_readings(root, prefix=prefix)
    after = [table_snapshot(t) for t in watched]

    assert deep_equal(after, before), "gather_readings wrote or changed at least one row"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/cli/test_report.py -q -k gather_readings`
Expected: FAIL — `ImportError: cannot import name 'gather_readings'`

- [ ] **Step 3a: Move the snapshot helpers somewhere both suites can reach**

`_table_snapshot(table)` and `_deep_equal(a, b)` currently live in
`tests/cli/test_report.py` and take a **table**, not a prefix. Task 5 needs them too.
Move both to `tests/conftest.py` as `table_snapshot` / `deep_equal` (dropping the
underscore, since they are now shared), and update `test_report.py`'s existing
`test_the_report_opens_no_write_transaction` to import them from there. This is the same
move the `prefix` fixture made in 1c-2, for the same reason: one definition, resolvable
from every test directory.

**Do not reimplement them.** `deep_equal` is NumPy-aware by necessity — the shared test
database holds a real ndarray in `Quarantine.detail`, planted by the guardrail sweep, and
bare `==` raises `ValueError` on it rather than comparing.

- [ ] **Step 3b: Extract**

In `wl_preproc/cli/report.py`, add above `build_report`:

```python
@dataclasses.dataclass(frozen=True, slots=True)
class Readings:
    """Everything both renderings need, computed once.

    `build_report` turns this into Markdown; the responder turns it into
    `protocol.Reading` rows. Neither computes anything itself — that is the
    entire reason this type exists rather than each caller doing its own
    queries, and it is the same move that pulled `scratch_headroom` out of
    `doctor.run_checks()` when the report needed the same number.
    """

    at: datetime.datetime
    ingested: list[dict]
    quarantined: list[dict]
    stalled: list[tuple[Path, list[str]]]
    stale_jobs: int | None
    free_gib: int
    headroom_ok: bool
    root_error: str | None
```

Then move the body of `build_report` down to (and including) the `scratch_headroom` call into `gather_readings(root, prefix, now)`, returning a `Readings`. `build_report` keeps every line that produces Markdown, and begins by calling `gather_readings`.

**Do not change any behaviour.** The window constants, the naive-`now` coercion, the guarded walk import, the `child.name` layout fix and the quarantine ordering all move as they are.

- [ ] **Step 4: Run the whole report suite**

Run: `.venv/bin/python -m pytest tests/cli/test_report.py -q`
Expected: PASS — the pre-existing tests must be untouched by an extraction.

- [ ] **Step 5: Prove the extraction is real**

Change `gather_readings`'s `free_gib` to a hardcoded `0`, run `tests/cli/test_report.py`, and confirm `test_gather_readings_returns_the_values_build_report_renders` fails — which shows `build_report` genuinely consumes the extracted value rather than recomputing it. Restore.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor(cli): one computation of the report's numbers, two renderings

The responder needs the same values the daily report renders, and report.py
returned only Markdown. Two definitions of "how many sessions are stuck"
that can disagree is a defect this project has already found in four
shapes, and the one time it was caught early was when the report was about
to reimplement doctor's disk check.

gather_readings returns the values; build_report renders them. Neither
computes anything itself.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Tighten `HealthResponse.verdict` to the four values wl.works accepts

Spec §5.1. `verdict` is typed `str` in a **frozen interface** while wl.works validates against exactly `ok`/`degraded`/`down`/`unknown` and rejects anything else. A typo ships and the whole response is refused.

**Files:**
- Modify: `wl_preproc/contracts/protocol.py`
- Test: `tests/contracts/test_protocol.py`
- Generated: `docs/schemas/health_response.json`

**Interfaces:**
- Produces: `Verdict = Literal["ok", "degraded", "down", "unknown"]`; `HealthResponse.verdict: Verdict`.

- [ ] **Step 1: Write the failing test**

Add to `tests/contracts/test_protocol.py`:

```python
def test_an_unknown_verdict_is_rejected():
    """wl.works validates verdict against exactly four values and refuses the
    whole response otherwise, so a bare `str` here means a typo ships and
    fails at their end rather than ours. Plan 10 section 1.1."""
    with pytest.raises(ValidationError):
        HealthResponse.model_validate({**PLAN_10_EXAMPLE, "verdict": "okay"})


@pytest.mark.parametrize("verdict", ["ok", "degraded", "down", "unknown"])
def test_every_verdict_wl_works_accepts_validates_here(verdict):
    assert HealthResponse.model_validate({**PLAN_10_EXAMPLE, "verdict": verdict}).verdict == verdict
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/contracts/test_protocol.py -q -k verdict`
Expected: FAIL — `"okay"` currently validates.

- [ ] **Step 3: Tighten the type**

In `wl_preproc/contracts/protocol.py`:

```python
from typing import Any, Literal

Verdict = Literal["ok", "degraded", "down", "unknown"]
"""The four values wl.works validates against, per its Plan 10 section 1.1.

This host emits only three of them. `unknown` is what *wl.works* records when
a host goes silent past its `stale_after_seconds` -- it is their word for our
absence, and we are never in a position to assert it about ourselves. See
`responder/health.py`.
"""
```

and change `HealthResponse.verdict` to `verdict: Verdict`.

- [ ] **Step 4: Run the test and regenerate the export**

Run: `.venv/bin/python -m pytest tests/contracts/test_protocol.py -q`
Run: `.venv/bin/python -m wl_preproc.cli.main schemas export`
Run: `git status --porcelain docs/schemas/`

`health_response.json` will change — a `Literal` becomes an `enum` in JSON Schema. **Commit it.** CI re-runs the export and `git diff --exit-code`s the directory, so a stale export is a red build.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
fix(contracts): verdict is four values, not any string

HealthResponse.verdict was typed str in a frozen interface while wl.works
validates against exactly ok/degraded/down/unknown and refuses the whole
response otherwise. A typo would have shipped and failed at their end.

The JSON Schema export changes with it: a Literal renders as an enum, which
is what their contract tests read.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `Activation.selection_hash`

Spec §6.2. A derivative activation's identity is its block set, not its montage. 1c-1 deferred this column here and 1c-2 recorded a residual that only this column closes.

**Files:**
- Modify: `wl_preproc/schema/request.py`
- Test: `tests/schema/test_request.py`

**Interfaces:**
- Produces: `Activation.selection_hash` (`varchar(64)`, nullable); `request.selection_hash(task_type: str, block_ids: list[int]) -> str`.

- [ ] **Step 1: Write the failing test**

Add to `tests/schema/test_request.py`:

```python
def test_selection_hash_is_order_independent():
    """The block set is a set. Two requests naming the same blocks in a
    different order are the same selection, and if they hash differently the
    dedupe in section 11.3 silently starts a second run."""
    from wl_preproc.schema.request import selection_hash

    assert selection_hash("neural", [3, 1, 2]) == selection_hash("neural", [1, 2, 3])


def test_selection_hash_separates_task_types():
    from wl_preproc.schema.request import selection_hash

    assert selection_hash("neural", [1, 2]) != selection_hash("export", [1, 2])


def test_canonical_activations_leave_selection_hash_null(selection, prefix):
    """A canonical activation's identity is (session, montage) per section 8.3.
    Giving it a selection hash would create a second identity for the same
    thing."""
    from wl_preproc.schema import request

    key = request.submit(
        idempotency_key="sel-null-1",
        task_type="neural",
        origin="cli",
        selection=selection,
        payload={},
    )
    assert (request.Activation & key).fetch1("selection_hash") is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/schema/test_request.py -q -k selection_hash`
Expected: FAIL — no `selection_hash` function and no such column.

- [ ] **Step 3: Add the column and the hash**

In `wl_preproc/schema/request.py`, add to `Activation`'s definition below the divider:

```
    # Null for a canonical activation, whose identity is (session, montage)
    # per section 8.3. Set for a derivative, whose identity is its block set --
    # which is why section 11.3's "a request whose (selection, task type) is
    # already in flight returns the running one" can be a lookup here rather
    # than a lock. 1c-1 deferred this column to 1c-3 on the grounds that both
    # are pre-data and adding it later is equally cheap; 1c-2 then recorded a
    # residual that only this column closes -- a reused idempotency key whose
    # first submission deduped onto an existing activation recorded nowhere
    # which selection it had asked for.
    selection_hash = null : varchar(64)
```

and add:

```python
def selection_hash(task_type: str, block_ids: list[int]) -> str:
    """Content hash of a derivative's identity: its task type and block set.

    Sorted and de-duplicated first, because the block set is a *set* -- two
    requests naming the same blocks in a different order are the same
    selection, and hashing them differently would start a second run for work
    already in flight. This is the same canonicalisation `paramset.content_hash`
    performs and for the same reason.
    """
    payload = json.dumps(
        {"task_type": task_type, "block_ids": sorted(set(block_ids))}, sort_keys=True
    )
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()
```

Add `import hashlib` and `import json` if absent.

- [ ] **Step 4: Run the schema tests and the guardrails**

Run: `.venv/bin/python -m pytest tests/schema/ -q`
Expected: PASS. The guardrail sweep auto-discovers this module, so a malformed declaration fails there.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(schema): Activation.selection_hash, the derivative's identity

A canonical activation is identified by (session, montage); a derivative by
its block set, which is why 1c-1 could not support derivatives and narrowed
submit() to canonical only. This is the column it deferred here.

The hash sorts and de-duplicates the block ids first, because the set is a
set: two requests naming the same blocks in a different order are the same
selection, and hashing them differently would start a second run for work
already in flight.

It also closes the residual 1c-2 recorded — a reused idempotency key whose
first submission deduped onto an existing activation recorded nowhere which
selection it had asked for.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Derivative activations, and dedupe on the selection

Spec §6.2. `submit()` structurally cannot make a derivative — it returns the canonical key for any `role`. This adds the path §11.3 describes.

**Files:**
- Modify: `wl_preproc/schema/request.py`
- Test: `tests/schema/test_request.py`

**Interfaces:**
- Consumes: `selection_hash` (Task 3), `submit`'s existing `_reject_key_reuse`.
- Produces: `submit_derivative(idempotency_key: str, task_type: str, origin: str, selection: dict, block_ids: list[int], payload: dict, requested_by: str | None = None) -> dict` — returns the `Activation` primary key.

- [ ] **Step 1: Write the failing test**

```python
def test_a_derivative_gets_its_own_activation_id(selection, prefix):
    from wl_preproc.schema import request

    canonical = request.submit(
        idempotency_key="dv-1", task_type="neural", origin="cli",
        selection=selection, payload={},
    )
    derivative = request.submit_derivative(
        idempotency_key="dv-2", task_type="neural", origin="wl_works",
        selection=selection, block_ids=[1, 2], payload={},
    )

    assert derivative["activation_id"] != canonical["activation_id"]
    assert (request.Activation & derivative).fetch1("role") == "derivative"


def test_the_same_selection_returns_the_running_one(selection, prefix):
    """Section 11.3: "a request whose (selection, task type) is already in
    flight returns the running one instead of starting a second." Structural,
    not a lock — the same shape canonical dedupe already uses."""
    from wl_preproc.schema import request

    first = request.submit_derivative(
        idempotency_key="dv-3", task_type="neural", origin="wl_works",
        selection=selection, block_ids=[1, 2], payload={},
    )
    second = request.submit_derivative(
        idempotency_key="dv-4", task_type="neural", origin="wl_works",
        selection=selection, block_ids=[2, 1], payload={},
    )

    assert second == first
    assert len(request.Activation & {"role": "derivative"}) == 1


def test_a_different_block_set_is_a_different_activation(selection, prefix):
    from wl_preproc.schema import request

    a = request.submit_derivative(
        idempotency_key="dv-5", task_type="neural", origin="wl_works",
        selection=selection, block_ids=[1, 2], payload={},
    )
    b = request.submit_derivative(
        idempotency_key="dv-6", task_type="neural", origin="wl_works",
        selection=selection, block_ids=[1, 3], payload={},
    )

    assert a != b
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/schema/test_request.py -q -k derivative`
Expected: FAIL — no `submit_derivative`.

- [ ] **Step 3: Implement**

Write `submit_derivative` in `wl_preproc/schema/request.py`, mirroring `submit`'s structure: the same activation guard, the same no-nesting guard, the same `_reject_key_reuse`, one transaction. It differs in three places:

- it computes `digest = selection_hash(task_type, block_ids)` and, **before inserting**, returns the existing `Activation` key if one already carries that `selection_hash` for this session and montage;
- it allocates `activation_id` as `max(existing) + 1` for the montage rather than pinning `0`, inside the same transaction, and lets a primary-key collision raise rather than using `skip_duplicates` — the identical reasoning `paramset.register` documents for its index allocation;
- it writes `role='derivative'`, `selection_hash=digest`, and one `ActivationBlock` row per block id.

Document in the docstring that a derivative never supersedes a canonical, and that `supersedes` remains unwritten by any code path.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/schema/test_request.py -q`
Expected: PASS.

- [ ] **Step 5: Prove the dedupe is real**

Change `selection_hash`'s `sorted(set(block_ids))` to `list(block_ids)`, run the suite, and confirm `test_the_same_selection_returns_the_running_one` fails — the reordered set now hashes differently and starts a second activation. Restore and re-run.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(schema): derivative activations, deduped on the selection

submit() structurally could not make a derivative — it returned the
canonical key for any role, which 1c-1 recorded as section 9.1 rather than
patching. This is the path section 11.3 describes: a request whose
(selection, task type) is already in flight returns the running one instead
of starting a second.

The dedupe is a lookup on selection_hash rather than a lock, which is the
same shape canonical dedupe and paramset registration already use here.
Index allocation lets a primary-key collision raise rather than skipping it,
for the reason paramset.register documents at length.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `responder/health.py`

Spec §5. Verdict and readings, reading only.

**Files:**
- Create: `wl_preproc/responder/__init__.py` (empty, `__all__: list[str] = []`), `wl_preproc/responder/health.py`
- Test: `tests/responder/test_health.py`

**Interfaces:**
- Consumes: `cli/report.py`'s `gather_readings` / `Readings` (Task 1); `contracts.protocol.HealthResponse`, `Reading`, `Action`, `Verdict`.
- Produces: `build_health(root: Path, prefix: str = DEFAULT_PREFIX, now: datetime.datetime | None = None) -> HealthResponse`.

- [ ] **Step 1: Write the failing test**

Create `tests/responder/test_health.py`:

```python
"""The health response. Reads only, and never claims `unknown`."""

from __future__ import annotations

import pytest

from wl_preproc.responder.health import build_health


def test_a_healthy_root_is_ok(scanned):
    root, prefix = scanned
    health = build_health(root, prefix=prefix)

    assert health.verdict == "ok"


def test_exactly_one_reading_is_featured(scanned):
    """Plan 10 section 4 settles the ambiguity — more than one featured
    reading and the first wins — so emitting more than one would let their
    renderer pick for us."""
    root, prefix = scanned
    health = build_health(root, prefix=prefix)

    assert sum(1 for r in health.readings if r.featured) == 1


def test_a_stuck_job_degrades_the_verdict(scanned, monkeypatch):
    root, prefix = scanned
    monkeypatch.setattr("wl_preproc.daemon.count_stale_jobs", lambda *a, **k: 3)
    health = build_health(root, prefix=prefix)

    assert health.verdict == "degraded"
    assert any("stuck" in r.key for r in health.readings)


def test_an_unreachable_database_is_down(scanned, monkeypatch):
    root, prefix = scanned

    def boom(*args, **kwargs):
        raise RuntimeError("no database")

    monkeypatch.setattr("wl_preproc.cli.report.gather_readings", boom)
    health = build_health(root, prefix=prefix)

    assert health.verdict == "down"


def test_this_host_never_claims_unknown(scanned, monkeypatch):
    """`unknown` is what wl.works records when a host goes silent past its
    stale_after_seconds. It is their word for our absence and we are never in
    a position to assert it about ourselves — claiming it would be asserting
    knowledge of our own silence."""
    root, prefix = scanned

    def boom(*args, **kwargs):
        raise RuntimeError("no database")

    monkeypatch.setattr("wl_preproc.cli.report.gather_readings", boom)
    assert build_health(root, prefix=prefix).verdict != "unknown"


def test_the_action_list_is_empty_until_a_stage_exists(scanned):
    root, prefix = scanned
    assert build_health(root, prefix=prefix).actions == []
```

You will need a `scanned` fixture in `tests/responder/conftest.py` — copy the shape of `tests/cli/test_report.py`'s, giving each test a dedicated ≤8-character subject.

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/responder/ -q`
Expected: FAIL — no module `wl_preproc.responder.health`.

- [ ] **Step 3: Implement**

`build_health` calls `gather_readings` inside a `try`, returns a `down` verdict with a single explanatory reading if it raises, and otherwise maps the values onto `Reading` rows. Verdict rules exactly as spec §5.1's table. Exactly one reading carries `featured=True`: whichever condition drove a non-`ok` verdict, or the ingest count when everything is fine.

- [ ] **Step 4: Run and confirm**

Run: `.venv/bin/python -m pytest tests/responder/ -q`
Expected: PASS.

- [ ] **Step 5: Prove it does not write**

Add a test that snapshots rows around `build_health` using `tests/conftest.py`'s `table_snapshot`/`deep_equal` — moved there in Task 1, step 3a — then mutate `build_health` to insert a `Quarantine` row and confirm the test fails. Restore. **Do not use `in_transaction`** — DataJoint's `insert()` never touches it, so it is equally `False` for a writing function and a reading one.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(responder): the health response, and the verdict it will not claim

Three verdicts, not four. `unknown` is what wl.works records when a host
goes silent past its stale_after_seconds — it is their word for our absence,
and claiming it would be asserting knowledge of our own silence.

Exactly one reading is featured, because Plan 10 section 4 settles the
ambiguity by taking the first and emitting several would let their renderer
choose for us.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `responder/actions.py` — the derived action list

Spec §3. Today it is empty, and that is the deliverable.

**Files:**
- Create: `wl_preproc/responder/actions.py`
- Test: `tests/responder/test_actions.py`
- Modify: `wl_preproc/responder/health.py` to use it

**Interfaces:**
- Consumes: `daemon._computed_tables()`.
- Produces: `available_actions(prefix: str = DEFAULT_PREFIX) -> list[Action]`; `DOMAIN_LABELS: dict[str, str]`.

- [ ] **Step 1: Write the failing test**

```python
def test_no_computed_stage_means_no_actions(dj_conn, prefix):
    """Section 3: wl.works renders each action as a button any lab member can
    press. A button that queues work nothing will pick up for six months is a
    button that teaches people to distrust the surface."""
    from wl_preproc.responder.actions import available_actions

    assert available_actions(prefix=prefix) == []


def test_an_action_appears_when_its_stage_does(dj_conn, prefix, monkeypatch):
    """The property the whole design rests on: Phase 2 lands, spike sorting
    appears, and neither this file nor wl.works changes."""
    from wl_preproc.responder import actions

    class FakeStage:
        __name__ = "Clustering"

    monkeypatch.setattr(actions, "_stage_domains", lambda prefix: {"neural"})
    published = actions.available_actions(prefix=prefix)

    assert [a.name for a in published] == ["neural"]
    assert published[0].label == actions.DOMAIN_LABELS["neural"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/responder/test_actions.py -q`
Expected: FAIL — no module.

- [ ] **Step 3: Implement**

`_stage_domains(prefix)` maps whatever `daemon._computed_tables()` returns onto the five §11.4 domain names, returning the set that exist.

**Read that function before writing this.** It is not a discovery mechanism — it
`return []` with a docstring saying *"Empty in 1c-1: nothing computes yet. The ordering
lives here so that 1c-4's timebase and coverage stages, and Phase 2's sorting, extend one
list rather than inventing their own traversal."* So it is a hardcoded list later phases
append to, and `_stage_domains` maps its entries rather than discovering them. That still
gives the property this design wants — a stage added there appears in the action list with
no change here — but the plan must not imply an introspection that does not exist. `available_actions` turns that set into `Action` rows using `DOMAIN_LABELS`, sorted for determinism. `DOMAIN_LABELS` carries all five domains with their §11.4 labels — the *labels* are known now even though the stages are not, and hardcoding them here is what makes a stage's arrival a zero-change event.

- [ ] **Step 4: Run, then wire into health**

Run: `.venv/bin/python -m pytest tests/responder/ -q`
Then have `build_health` call `available_actions` instead of returning `[]`, and confirm `test_the_action_list_is_empty_until_a_stage_exists` still passes for the right reason.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(responder): an action list derived from the stages that exist

Today it is empty, and that is correct. None of the five dispatch domains
can run until Phase 2 or later, and wl.works renders each action as a button
any lab member can press — publishing one that queues work nothing will pick
up for six months teaches people to distrust the surface.

The labels are hardcoded for all five because they are known now; the list
is derived from which computed stages exist. So the ephys branch landing
makes spike sorting appear with no change here and none in wl.works, which
is what "the host publishes its own action list" was written to buy.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `responder/jobs.py` — a request becomes rows

Spec §6.1. This is where §1's finding lands in code.

**Files:**
- Create: `wl_preproc/responder/jobs.py`
- Test: `tests/responder/test_jobs.py`

**Interfaces:**
- Consumes: `contracts.protocol.JobRequest`, `schema.request.submit` / `submit_derivative`, `schema.core.Montage`/`Block`, `ingest.landing.manifest_session_key`-style key handling.
- Produces: `accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict` — returns the `Activation` primary key. Raises `ValueError` for a request that cannot be honoured.

- [ ] **Step 1: Write the failing test**

Cover: a request creates `Montage` rows from `metadata.montage_boundaries`; creates `Block` rows with `works_block_id` set; is idempotent on the same key; a request naming no `block_ids` produces a canonical activation and one naming some produces a derivative; and **existing `Montage`/`Block` rows are never overwritten** — a second request carrying different boundaries leaves the first rows intact, because wl.works correcting its own record is their call and not ours to infer from a payload.

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/responder/test_jobs.py -q`
Expected: FAIL — no module.

- [ ] **Step 3: Implement**

**`accept` must NOT wrap this in a transaction of its own.** Both `submit` and
`submit_derivative` guard on `dj.conn().in_transaction` and raise, because DataJoint
transactions do not nest — and `submit()`'s own docstring states directly that neither the
ingest watcher nor the responder may wrap it to bundle it with other writes. The plan said
"one transaction" here and was wrong; Task 4's review caught it before this task was
dispatched. Structure it as: insert-if-absent `Montage` from `metadata.montage_boundaries`,
insert-if-absent `Block` from `metadata.blocks` with `works_block_id`, **each idempotent on its
own**, and then call `submit` or `submit_derivative` outside any transaction of yours.

That is not a weakening. Those inserts are idempotent by construction — the same property 1c-2
relies on for having no lock — so a partial `accept` followed by a retry converges on the same
rows, which is what a transaction would have bought and is the same reasoning `landing.py`
already records for the ingest path.

**`accept` also owns the montage window.** `ActivationBlock`'s own comment says *"the responder
(1c-3) is its first writer and owns enforcing the window"*, and `submit_derivative` currently
accepts a block at `[20.0, 24.0)` against a montage of `[0.0, 12.0)`. No other task in this
plan mentions it, so it would have shipped unowned with the comment reading as satisfied.
Reject a selection naming a block outside its montage's `[start_s, end_s)` with a `ValueError`
naming the offending block, and test it. Session identity comes from `metadata.subject` plus the selection's `session_datetime`, normalised through `landing.to_naive_utc`, **the same conversion every other datetime in this codebase goes through** — two call sites converting differently is how two equal keys stop being equal.

Raise `ValueError` with a plain message for a request whose selection is missing a required key, whose montage boundaries are absent when no montage exists, or whose subject exceeds `landing.SUBJECT_MAX_LEN`.

- [ ] **Step 4: Run and confirm**

Run: `.venv/bin/python -m pytest tests/responder/ tests/schema/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(responder): a job request becomes rows

MetadataBundle carries blocks and montage_boundaries inbound with every
request, and core.Montage says it is sourced from wl.works item_insertion
and nothing else. Recording them here is not guessing — it is wl.works'
authored record arriving by the route its own contract describes, which is
what dissolves the precondition 1c-1 and 1c-2 both worked around.

Existing rows are never overwritten. A later request carrying different
boundaries is wl.works correcting its own record, and that is their call to
make explicitly rather than ours to infer from a payload.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `responder/handler.py` and `server.py` — HTTP, auth, and the lock

Spec §4. `handler.py` imports no DataJoint.

**Files:**
- Create: `wl_preproc/responder/handler.py`, `wl_preproc/responder/server.py`
- Test: `tests/responder/test_http.py`

**Interfaces:**
- Produces: `make_handler(token: str, health_fn, accept_fn) -> type[BaseHTTPRequestHandler]`; `serve(port: int, token: str, root: Path, prefix: str, ready: threading.Event | None = None) -> None`.

- [ ] **Step 1: Write the failing test**

Test against a **real `ThreadingHTTPServer` on an ephemeral port**, not a mocked handler — the defects worth catching here are in routing, status codes and header parsing, which a mock reproduces by construction. Cover each rule spec §9 names: missing token → `401` (not `403`); wrong token → `401` with no hint which part was wrong; valid token + malformed body → `422` and never a traceback; unknown path → `404`; `GET /jobs` → `405`; and a valid request → `200` with the activation key.

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/responder/test_http.py -q`
Expected: FAIL — no module.

- [ ] **Step 3: Implement**

`make_handler` closes over the token and two callables, so the handler never imports DataJoint and the HTTP tests need no database. Compare tokens with `hmac.compare_digest`. `server.py` owns a single `threading.Lock` that both endpoints take around their callable — **DataJoint connections are not shareable across threads**, and 1c-2's final review lost an afternoon to a four-thread probe producing `InternalError: Packet sequence number wrong` from exactly this, diagnosed only by redoing it with subprocesses.

- [ ] **Step 4: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, 0 warnings.

- [ ] **Step 5: Prove the auth is real**

Remove the `compare_digest` check, run `tests/responder/test_http.py`, confirm the missing-token and wrong-token tests fail. Restore.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(responder): the HTTP surface, a bearer token, and one lock

Two endpoints, stdlib http.server, no web framework: the payloads are
already validated by pydantic models that exist, and the OpenAPI argument
for a framework is satisfied by the JSON Schema export CI already diffs.

Both endpoints require a bearer token. wl.works plans to send no credential,
and its reason for building no permission model of its own is a prediction
about this host written before any wl-preproc design existed. The intended
trigger population is any lab member; the actual population of an
unauthenticated LAN endpoint is anything plugged into the lab network.

Database work serialises behind one lock. DataJoint connections are not
shareable across threads, and this project has already spent an afternoon
diagnosing that as a race before subprocesses showed what it really was.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: `wlpp responder`, and the guardrail that forbids calling out

Spec §8, §9. §11.1 is not negotiable and a test should say so.

**Files:**
- Modify: `wl_preproc/cli/main.py`
- Test: `tests/test_cli_guardrails.py`

- [ ] **Step 1: Write the failing guardrail test**

```python
def test_nothing_in_the_responder_opens_an_outbound_connection():
    """Section 11.1: wl.works opens every connection and this host never
    initiates. That is a property of the code, not an intention, so it is a
    test — the same shape as the guardrail forbidding a bare .delete().

    A source scan rather than a runtime check, because the failure this
    prevents is someone adding a convenient callback months from now, and
    that lands in the source long before it lands in a running process.
    """
    import pathlib

    forbidden = ("requests.", "urllib.request", "httpx.", "socket.create_connection", "aiohttp")
    offenders = []
    for path in pathlib.Path("wl_preproc/responder").rglob("*.py"):
        text = path.read_text()
        offenders += [f"{path}: {token}" for token in forbidden if token in text]

    assert not offenders, f"the responder must never initiate a connection: {offenders}"
```

- [ ] **Step 2: Run it — it should pass immediately**

Run: `.venv/bin/python -m pytest tests/test_cli_guardrails.py -q -k outbound`
Expected: PASS. Then **prove it is not vacuous**: add `import requests` to `responder/health.py`, confirm the test fails, remove it.

- [ ] **Step 3: Register the subcommand**

In `wl_preproc/cli/main.py`, add a parser and dispatch branch following the existing pattern. **Do not add `from pathlib import Path` inside the branch** — `main.py` imports `Path` at module level, and a function-local import makes `Path` function-local throughout `main()`, raising `UnboundLocalError` on `schemas export`'s unconditional `type=Path` and breaking every subcommand. That bug has already been introduced twice in this project.

Read the token from `WLPP_RESPONDER_TOKEN`, and refuse to start with a clear message if it is unset — never a default.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, 0 warnings.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(cli): wlpp responder, and a test that it never calls out

Section 11.1 says wl.works opens every connection and this host never
initiates. That has been a sentence in a spec; it is now a property the
suite checks, in the same shape as the guardrail forbidding a bare
.delete().

A source scan rather than a runtime check, because the failure it prevents
is a convenient callback added months from now, and that lands in the source
long before it lands in a process.

The token comes from the environment and has no default. A responder that
starts without one is a responder with no boundary at all.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: `docs/ops/lab-host-protocol.md`

Spec §7. wl.works cites this file from five places; neither repository has written it.

**Files:**
- Create: `docs/ops/lab-host-protocol.md`

- [ ] **Step 1: Write it**

It carries, with no placeholders:

- The two endpoints, their methods, request and response shapes, and every status code the handler can return.
- **The bearer token** — that wl.works must send it, the header form, and that a missing or wrong token is `401` with no detail. Flag prominently that this is a **change on their side**: their Plan 10 §6 request carries an action name and an idempotency key and no credential.
- The four verdict values, and that this host emits three — with `unknown` explained as theirs for our silence.
- The `featured` rule: more than one and the first wins, so exactly one is emitted.
- **The three numbers their Plan 10 leaves as unfilled environment constants** — poll cadence, request timeout, per-host refresh cooldown — with a proposed value and one sentence of reasoning each. A protocol document with no timing in it is what leaves two sides guessing.
- That the action list is derived and currently empty, and what makes an entry appear.
- That this host never initiates a connection, and that results are pulled rather than pushed.

- [ ] **Step 2: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
docs(ops): the lab host protocol, which neither repo had written

wl.works cites docs/ops/lab-host-protocol.md from five places and it does
not exist; its own waiting-on.md lists it as unblocked and its Plan 10
argues for writing it before the hardware arrives, so that installing the
responder is part of build-out rather than a retrofit.

We write it because we are the half that knows what the responder does. It
carries the endpoints, the bearer token wl.works does not currently send,
the verdict values and which three this host emits, and the poll cadence,
timeout and cooldown their Plan 10 leaves as unfilled constants — a protocol
document with no timing in it is what leaves two sides guessing.

Proposed to them rather than written into their repository.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review

**Spec coverage.** §1 → Task 7. §3 → Task 6. §4.1/4.2 → Task 8. §4.3 → Tasks 8, 9, 10. §4.4 → Task 8. §5.1 → Task 2. §5.2 → Tasks 1, 5. §6.1 → Task 7. §6.2 → Tasks 3, 4. §7 → Task 10. §8 → the file table. §9 → Tasks 5, 8, 9. §10 excludes; §11 is carried into every task's constraints; §12 records open questions and needs no task.

**Type consistency.** `Readings` is produced by Task 1 and consumed by Task 5. `selection_hash` is produced by Task 3 and consumed by Task 4. `available_actions` is produced by Task 6 and wired into Task 5's `build_health` in Task 6's own step 4. `accept` is produced by Task 7 and consumed by Task 8's `make_handler`. `Verdict` is produced by Task 2 and used by Task 5.

**Two items were resolved during self-review rather than left as caveats.**
`daemon._computed_tables()` was read: it returns a literal `[]` and is a hardcoded list later
phases extend, not an introspection — Task 6 says so rather than implying otherwise.
`_table_snapshot`/`_deep_equal` were located in `tests/cli/test_report.py`, taking a **table**
rather than a prefix, so Task 1 now moves them to `tests/conftest.py` before Task 5 needs them.

**One thing the implementer must still verify.** `submit()`'s `_reject_key_reuse` compares
`(task_type, origin, payload, requested_by)`. Task 4 must decide whether a derivative's reuse
check also needs the block set — the same idempotency key naming a different selection is
exactly the shape `selection_hash` exists to make detectable — and say in its report what it
concluded and why.
