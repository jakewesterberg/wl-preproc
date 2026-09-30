# The Canonical Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build wl-preproc's side of the canonical NWB lifecycle.
- Honour a replacement canonical that wl.works asks for, by name.
- Keep the superseded file readable and marked in `GET /nwb`.
- Count only the current canonical for reclamation.
- Rebuild `invalid` files once the subject details they lacked arrive.

**Architecture:**
- **The current canonical.** `schema/request.py` gains `current_canonical`: the canonical no other activation supersedes. `submit()`, reclamation and the builder all ask it.
- **Replacements.** `submit_replacement` writes a new canonical at the montage's next free id, with `supersedes` set. It holds a per-montage MySQL named lock, taken before its transaction.
- **The protocol.** `responder/jobs.py::accept` reads two new optional `selection` keys, `role` and `supersedes_activation_id`. The server turns a stale replacement into a `409`.
- **The build stage.** Under the NWB lock, it records a `superseded` change for the listing, and discards `invalid` rows whose subject details have changed so that they rebuild.

**Tech Stack:** Python ≥3.11; DataJoint 2.3 with MySQL (tables and end-to-end tests); pydantic 2 (the contracts); the standard-library HTTP server (the responder).

**Spec:** `docs/superpowers/specs/2026-09-30-canonical-lifecycle-design.md` (`d025af5`). It is binding. The requester approved it on 2026-09-30 ("Yes, write the plan").

**Every piece of code below was proven before this plan was written.** It was built in a scratch worktree on a branch of its own, one commit per task.
- **Each task's failing run** was measured on the previous task's code plus this task's tests.
- **Its passing run** was measured on its own commit.
- **Every mutation check named here** was run against the final tree, and each failed its test.
- **The full suite** was run on both interpreters with every task applied (Task 6 quotes it).

## Global Constraints

- **The spec is binding,** including its dated amendments. Where it and this plan disagree, the spec wins; record a ruling.
- **wl.works fires every canonical** (spec §0, decision 1). Nothing here adds a clock, a timer or a montage guess.
- **A replacement is explicit** (decision 2). Only a request carrying `supersedes_activation_id` creates a second canonical for a montage.
- **Supersede, never overwrite** (parent spec §8.3). The superseded activation, its `NwbFile` row and its published file are left exactly as they were.
- **Only `submit_replacement` writes `Activation.supersedes`.** `tests/schema/test_guardrails.py` enforces this by scanning the source. Name nothing else's keyword argument or dict key `supersedes`.
- **A `selection` without `role` means what it always meant:** `block_ids` makes a derivative, and no `block_ids` makes a canonical over the whole montage.
- **Frozen interfaces changed here, each with its export regenerated:**
  - `contracts/protocol.py` (`NwbListingEntry.superseded_by`, a new field);
  - `docs/schemas/nwb_listing.json`;
  - `docs/ops/lab-host-protocol.md`.
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code, data or tables.
- **Tests.**
  - Run with `.venv/bin/python -m pytest <files> -q -p no:cacheprovider` from the repository root. In a git worktree, prefix `PYTHONPATH=$PWD`.
  - Database tests need Docker (OrbStack on this machine: `open -a OrbStack` after a reboot). A module-wide setup error on a 120 s container timeout is the known flake: re-run it.
  - Mutation checks:
    - clear `__pycache__` first, and again after restoring;
    - run with `PYTHONDONTWRITEBYTECODE=1`;
    - make one mutation at a time, and restore the file afterwards.
  - **Run each task's own test files.** The full suite runs once, on both interpreters, in Task 6.
  - The shell is zsh:
    - an unquoted `$VAR` holding several arguments is not split;
    - arrays start at 1;
    - a pipe through `tail` or `grep` hides the test command's exit status, so gate on the command's own status.
- **Every commit message ends with:**

  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R
  ```

## Review Focus

Five inputs the spec implies but its own test list (§10) does not name, most likely first. Each is pinned by a test named here, in the task that owns the code.

1. **Two replacements of one canonical at the same moment.** For example, wl.works retries under a new key while a person regenerates. Exactly one may win; the other waits for the montage lock, then sees the winner and is refused. Without the lock, DataJoint's consistent snapshot would let both pass and fork the chain. Task 2: `test_a_replacement_waits_for_the_montage_lock`.
2. **wl.works re-sends its first canonical request after a replacement.** It gets the current canonical, not activation 0. Task 3: `test_a_replacement_request_supersedes_the_named_canonical`.
3. **A replacement arrives before its predecessor was built.** The predecessor is never built, and the replacement is. Task 1: `test_a_superseded_activation_that_was_never_built_is_not_built`.
4. **A rebuild that is still invalid, for a reason other than the subject.** It is left alone until the details change again, never rebuilt every pass. Task 5: `test_an_invalid_file_is_rebuilt_once_its_missing_subject_details_arrive`.
5. **A canonical block set naming a block outside the montage, or an empty one.** The first is a `422`. The second is the plain canonical over the whole montage. Task 3: `test_a_selection_the_lifecycle_refuses_is_a_value_error` and `test_a_canonical_role_with_no_blocks_takes_the_whole_montage`.

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/schema/request.py` | `current_canonical`, `is_superseded` (Task 1); `SupersedeConflict`, `submit_replacement`, a canonical's block set (Task 2) |
| `wl_preproc/archive/reclaim.py` | the current canonical only (Task 1) |
| `wl_preproc/nwb/build.py` | skip a superseded activation (Task 1); `note_superseded` (Task 4); `resolved_invalid`, `_discard` (Task 5) |
| `wl_preproc/nwb/gather.py` | a canonical's named blocks (Task 2) |
| `wl_preproc/responder/jobs.py`, `responder/server.py` | the new `selection` keys, and the `409` (Task 3) |
| `wl_preproc/schema/nwb.py`, `contracts/protocol.py`, `responder/nwb.py` | the `superseded` change, and `superseded_by` (Task 4) |
| `docs/pending-wl-works-amendments.md`, the parent and publishing specs | what wl.works must do, and the amendments (Task 6) |

---

### Task 1: The current canonical

**Files:**
- Modify: `wl_preproc/schema/request.py`, `wl_preproc/archive/reclaim.py`, `wl_preproc/nwb/build.py`
- Test: `tests/schema/test_request.py`, `tests/archive/test_reclaim.py`, `tests/schema/test_nwb_build.py`

**Interfaces — produces:**
- `request.current_canonical(montage_key) -> dict | None`: the `Activation` row of the montage's canonical that no activation supersedes.
- `request.is_superseded(key) -> bool`.
- `submit()` returns the current canonical, not whichever canonical row it finds.

**Why this task comes first.** Once a replacement exists, a montage has two canonical rows. `submit()`'s old `existing.fetch1()` would raise, and reclamation would ask of the superseded one as if it were current.

- [ ] **Step 1: Write the failing tests.** Apply these diffs:

```diff
--- a/tests/schema/test_request.py
+++ b/tests/schema/test_request.py
@@ -1209,3 +1209,34 @@ def test_locking_read_one_rejects_an_incomplete_key(req):
                 # activation_id deliberately omitted
             }
         )
+
+
+def _replace_by_hand(req, selection, *, supersedes: int, activation_id: int, key: str) -> dict:
+    """A replacement canonical written directly, as design spec
+    `2026-09-30-canonical-lifecycle-design.md` section 3 will have
+    `submit_replacement` write it: the next activation id, superseding the
+    one named."""
+    now = datetime.datetime(2027, 6, 1, 12, 0)
+    req.Request.insert1({"idempotency_key": key, "task_type": "neural", "origin": "wl_works",
+                         "payload": {}, "requested_at": now})
+    row = {**selection, "activation_id": activation_id, "role": "canonical", "request_key": key,
+           "created_at": now, "supersedes": supersedes}
+    req.Activation.insert1(row)
+    return {k: row[k] for k in req.Activation.primary_key}
+
+
+def test_submit_returns_the_current_canonical_after_a_replacement(req, selection):
+    """Design spec `2026-09-30-canonical-lifecycle-design.md` section 3,
+    case 2: once a replacement exists, a canonical request for the montage
+    answers with the current canonical, the one nothing supersedes -- not
+    activation 0."""
+    first = req.submit("k-cur-1", "neural", "wl_works", selection, {}, "jake")
+    replacement = _replace_by_hand(req, selection, supersedes=first["activation_id"], activation_id=1,
+                                   key="k-cur-2")
+    assert req.submit("k-cur-3", "neural", "wl_works", selection, {}, "jake") == replacement
+    assert {k: req.current_canonical(selection)[k] for k in req.Activation.primary_key} == replacement
+    assert req.is_superseded(first) and not req.is_superseded(replacement)
+
+
+def test_a_montage_without_a_canonical_has_no_current_one(req, selection):
+    assert req.current_canonical(selection) is None
```

```diff
--- a/tests/archive/test_reclaim.py
+++ b/tests/archive/test_reclaim.py
@@ -348,6 +348,11 @@ _NWB_STATES = {
     "invalid": ("rcnwb3", "montage 0: its canonical NWB is invalid"),
     "unpublished": ("rcnwb4", "montage 0: its canonical NWB is built but not yet published"),
     "published": ("rcnwb5", ""),
+    # The canonical lifecycle (design spec
+    # `2026-09-30-canonical-lifecycle-design.md` section 4): the refused
+    # activation 0 is superseded by a published activation 1, so only the
+    # current one counts.
+    "superseded": ("rcnwb6", ""),
 }
 
 
@@ -378,20 +383,30 @@ def test_canonical_nwb_present_follows_the_published_canonical_file(session, pre
                                  "payload": {}, "requested_at": now})
         request.Activation.insert1({**activation, "role": "canonical", "request_key": f"{subject}-k",
                                     "created_at": now})
+    current = activation
+    if state == "superseded":
+        current = {**activation, "activation_id": 1}
+        request.Request.insert1({"idempotency_key": f"{subject}-k2", "task_type": "neural",
+                                 "origin": "wl_works", "payload": {}, "requested_at": now})
+        request.Activation.insert1({**current, "role": "canonical", "request_key": f"{subject}-k2",
+                                    "created_at": now, "supersedes": 0})
     try:
-        if state in ("invalid", "unpublished", "published"):
-            nwb_schema.NwbFile.insert1({**activation, "status": "invalid" if state == "invalid" else "written",
+        if state == "superseded":
+            nwb_schema.NwbFile.insert1({**activation, "status": "refused", "built_at": now,
+                                        "reason": "no blocks"})
+        if state in ("invalid", "unpublished", "published", "superseded"):
+            nwb_schema.NwbFile.insert1({**current, "status": "invalid" if state == "invalid" else "written",
                                         "built_at": now})
-        if state == "published":
-            record_change(activation, "published",
+        if state in ("published", "superseded"):
+            record_change(current, "published",
                           {"tier": "slow", "host": "wl-nas", "share": "hdd", "path": "nwb/x.nwb", "n_bytes": 1})
 
         nwb = _condition(reclaim_conditions(key, expected_file_count=0, prefix=prefix), "canonical_nwb_present")
-        assert (nwb.passed, nwb.detail, nwb.overridable) == (state == "published", detail, True)
+        assert (nwb.passed, nwb.detail, nwb.overridable) == (state in ("published", "superseded"), detail, True)
     finally:
         # These rows have no file behind them. Left in the suite's shared
         # database, the NWB stages in later tests would try to publish them.
-        (nwb_schema.NwbFile & activation).delete(prompt=False)
+        (nwb_schema.NwbFile & key).delete(prompt=False)
 
 
 def test_a_force_overrides_tier_d_and_the_missing_nwb(session, prefix):
```

```diff
--- a/tests/schema/test_nwb_build.py
+++ b/tests/schema/test_nwb_build.py
@@ -1126,6 +1126,33 @@ def test_a_second_wlpp_process_leaves_the_nwb_stages_alone(activation, daemon_mo
     assert not [e for e in report["errors"] if "another wlpp process" in e]
     assert nwb_schema.NwbFile & key
 
+
+def test_a_superseded_activation_that_was_never_built_is_not_built(activation, prefix, tmp_path_factory):
+    """Design spec `2026-09-30-canonical-lifecycle-design.md` section 4: a
+    replacement that arrives before its predecessor was built leaves the
+    predecessor unbuilt."""
+    from wl_preproc.nwb.build import run_stage
+    from wl_preproc.schema import nwb as nwb_schema
+    from wl_preproc.schema import request
+
+    session_key, _key, _blocks = activation
+    now = datetime.datetime(2027, 6, 1, 12, 0)
+    old = {**session_key, "montage_id": 1, "activation_id": 50}
+    new = {**session_key, "montage_id": 1, "activation_id": 51}
+    request.Request.insert1({"idempotency_key": "nwbstep1-superseded", "task_type": "neural",
+                             "origin": "wl_works", "payload": {}, "requested_at": now})
+    request.Activation.insert1({**old, "role": "canonical", "request_key": "nwbstep1-superseded", "created_at": now})
+    request.Activation.insert1({**new, "role": "canonical", "request_key": "nwbstep1-superseded", "created_at": now,
+                                "supersedes": 50})
+    try:
+        run_stage(tmp_path_factory.mktemp("nwb-superseded"))
+        assert not nwb_schema.NwbFile & old
+        assert nwb_schema.NwbFile & new
+    finally:
+        (nwb_schema.NwbFile & [old, new]).delete(prompt=False)
+        (request.Activation & [new, old]).delete(prompt=False)
+        (request.Request & {"idempotency_key": "nwbstep1-superseded"}).delete(prompt=False)
+
 def test_one_failing_activation_does_not_stop_the_stage(activation, prefix, monkeypatch, tmp_path_factory):
     """The stage catches a failure per activation, as the archive stage
     does: the others are recorded, the failure is reported, and the failed
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_request.py tests/archive/test_reclaim.py tests/schema/test_nwb_build.py tests/schema/test_guardrails.py -q --tb=line -p no:cacheprovider`
Expected: 4 failed, 130 passed. `fetch1 requires exactly one tuple` (two canonical rows), `module 'wl_preproc.schema.request' has no attribute 'current_canonical'`, and two assertions: the `superseded` reclaim state, and the build stage building a superseded activation.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/schema/request.py
+++ b/wl_preproc/schema/request.py
@@ -517,13 +517,12 @@ def submit(
         # canonical result: caught live by
         # test_a_derivative_before_any_canonical_does_not_claim_activation_id_zero
         # in tests/schema/test_request.py.
-        existing = Activation & canonical_selection_key
-        if existing:
-            # One fetch1() for the whole row rather than one per key
-            # attribute — Activation's key has four parts, so the brief's
-            # dict-comprehension form issued four SELECTs for this branch.
-            row = existing.fetch1()
-            return {k: row[k] for k in Activation.primary_key}
+        # The CURRENT canonical, not any canonical row: once a replacement
+        # exists the montage has two, and a fetch1() here raised (design
+        # spec `2026-09-30-canonical-lifecycle-design.md` section 3, case 2).
+        existing = current_canonical(selection_key)
+        if existing is not None:
+            return {k: existing[k] for k in Activation.primary_key}
 
         key = {**selection_key, "activation_id": 0}
         Activation.insert1(
@@ -547,6 +546,30 @@ def submit(
 # _locking_read_one's for why the re-check inside that retry is an
 # exact-primary-key locking read and a local increment, never a range read,
 # unlike in paramset.register.
+def current_canonical(montage_key: dict) -> dict | None:
+    """The montage's current canonical activation: the canonical row no
+    other activation supersedes (parent spec section 8.3, "exactly one
+    current per (session, montage)"; design spec
+    `2026-09-30-canonical-lifecycle-design.md` section 3). None when the
+    montage has no canonical.
+
+    A replacement only ever supersedes the current canonical, so the chain
+    is linear and exactly one row qualifies; should a hand edit leave two,
+    the latest id wins rather than raising under every stage that asks."""
+    montage = {k: montage_key[k] for k in ("subject", "session_datetime", "montage_id")}
+    rows = (Activation & montage).to_dicts()
+    replaced = {row["supersedes"] for row in rows if row["supersedes"] is not None}
+    current = [row for row in rows if row["role"] == "canonical" and row["activation_id"] not in replaced]
+    return max(current, key=lambda row: row["activation_id"]) if current else None
+
+
+def is_superseded(key: dict) -> bool:
+    """Whether another activation of the same montage supersedes this one."""
+    montage = {k: key[k] for k in ("subject", "session_datetime", "montage_id")}
+    replaced = (Activation & montage & "supersedes IS NOT NULL").to_arrays("supersedes")
+    return int(key["activation_id"]) in {int(value) for value in replaced}
+
+
 _MAX_DERIVATIVE_ALLOCATE_ATTEMPTS = 10
 
 
```

```diff
--- a/wl_preproc/archive/reclaim.py
+++ b/wl_preproc/archive/reclaim.py
@@ -76,7 +76,10 @@ def reclaimable(predicate: Predicate) -> bool:
 def _canonical_nwb(session_key: dict, prefix: str) -> tuple[bool, str]:
     """Whether every montage of the session has a canonical activation whose
     NWB file is `written` and published, on either share (design spec
-    `2026-09-29-nwb-publishing-design.md` section 8), and, when not, why."""
+    `2026-09-29-nwb-publishing-design.md` section 8), and, when not, why.
+    Only each montage's CURRENT canonical counts: a superseded one, refused
+    or invalid, must not block a session whose replacement is published
+    (design spec `2026-09-30-canonical-lifecycle-design.md` section 4)."""
     from wl_preproc.nwb.publish import current_placement
     from wl_preproc.schema import core, request
     from wl_preproc.schema import nwb as nwb_schema
@@ -87,10 +90,10 @@ def _canonical_nwb(session_key: dict, prefix: str) -> tuple[bool, str]:
         return False, "no montage, so no canonical activation"
     problems = []
     for montage_id in montages:
-        canonical = (request.Activation & session_key & {"montage_id": montage_id, "role": "canonical"}).keys()
-        if not canonical:
+        current = request.current_canonical({**session_key, "montage_id": montage_id})
+        if current is None:
             problems.append(f"montage {montage_id}: no canonical activation")
-        for key in canonical:
+        for key in ([] if current is None else [{k: current[k] for k in request.Activation.primary_key}]):
             rows = (nwb_schema.NwbFile & key).to_dicts()
             if not rows:
                 problems.append(f"montage {montage_id}: its canonical NWB is not built yet")
```

```diff
--- a/wl_preproc/nwb/build.py
+++ b/wl_preproc/nwb/build.py
@@ -135,8 +135,11 @@ def record(activation_key: dict, result: BuildResult) -> None:
 
 def run_stage(nwb_root: Path, freed: list[dict] | None = None) -> tuple[int, list[str]]:
     """The daemon's `_nwb_stage`: every activation without an `NwbFile` row,
-    skipping freed sessions and, without recording anything, those whose
-    inputs are not all computed yet (`gather.readiness`). Returns
+    skipping freed sessions, superseded activations (a replacement arrived
+    before this one was built: design spec
+    `2026-09-30-canonical-lifecycle-design.md` section 4) and, without
+    recording anything, those whose inputs are not all computed yet
+    (`gather.readiness`). Returns
     `(activations recorded, per-activation failures)`, the archive stage's
     shape."""
     from wl_preproc.schema import nwb as nwb_schema
@@ -148,7 +151,7 @@ def run_stage(nwb_root: Path, freed: list[dict] | None = None) -> tuple[int, lis
         if {"subject": key["subject"], "session_datetime": key["session_datetime"]} in freed:
             continue
         try:
-            if readiness(key) is not None:
+            if request.is_superseded(key) or readiness(key) is not None:
                 continue
             record(key, build(key, nwb_root))
             recorded += 1
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 134 passed.

- [ ] **Step 5: Mutation checks.** Each was measured to fail the test named in brackets.
  - T1a (`request.py`): in `submit()`, `existing = current_canonical(selection_key)` becomes `existing = None` [`test_submit_returns_the_current_canonical_after_a_replacement`].
  - T1b (`request.py`): in `current_canonical`, `if row["role"] == "canonical" and row["activation_id"] not in replaced]` becomes `if row["activation_id"] not in replaced]` [`test_a_replacement_of_anything_but_the_current_canonical_is_a_conflict`, Task 2's].
  - T1c (`request.py`): `is_superseded`'s return becomes `return False` [`test_submit_returns_the_current_canonical_after_a_replacement`].
  - T1d (`build.py`): `if request.is_superseded(key) or readiness(key) is not None:` becomes `if readiness(key) is not None:` [`test_a_superseded_activation_that_was_never_built_is_not_built`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/request.py wl_preproc/archive/reclaim.py wl_preproc/nwb/build.py tests/schema/test_request.py tests/archive/test_reclaim.py tests/schema/test_nwb_build.py
git commit -m "feat(request): the current canonical -- the one nothing supersedes -- answers a canonical request, and only it counts for reclamation; a superseded activation is never built

<trailer lines>"
```

---

### Task 2: A replacement, in the request schema

**Files:**
- Modify: `wl_preproc/schema/request.py`, `wl_preproc/nwb/gather.py`
- Test: `tests/schema/test_request.py`, `tests/schema/test_guardrails.py`

**Interfaces:**
- **Consumes:** Task 1's `current_canonical`.
- **Produces:**
  - `request.submit_replacement(idempotency_key, task_type, origin, selection, payload, requested_by=None, *, supersedes_activation_id: int, block_ids=()) -> dict`: the new activation's key.
  - `request.SupersedeConflict(dj.DataJointError)`.
  - `submit(..., block_ids=())`: a first canonical may name its block set.
  - `gather._block_set` reads `ActivationBlock` rows for a canonical that has them.

**Why the lock is taken before the transaction.** DataJoint starts every transaction `WITH CONSISTENT SNAPSHOT` (`datajoint/adapters/mysql.py`, measured). A check inside one cannot see a rival replacement committed after it began, so two replacements of one canonical would both pass. A MySQL named lock, held before the snapshot is taken, serialises them. It sits outside InnoDB's row locks, so it cannot deadlock with `submit_derivative`'s allocation in the same montage.

**Why the parameter is `supersedes_activation_id`.** It is the protocol's own key. A keyword argument spelled `supersedes=` in `jobs.py`'s call would match the guardrail's write pattern, and was caught doing so while proving.

- [ ] **Step 1: Write the failing tests.** Apply these diffs. The guardrail narrows from "no writer" to exactly `submit_replacement`, and gains a test of its own scanner:

```diff
--- a/tests/schema/test_request.py
+++ b/tests/schema/test_request.py
@@ -1240,3 +1240,80 @@ def test_submit_returns_the_current_canonical_after_a_replacement(req, selection
 
 def test_a_montage_without_a_canonical_has_no_current_one(req, selection):
     assert req.current_canonical(selection) is None
+
+
+def test_a_replacement_supersedes_the_current_canonical_at_the_next_id(req, selection):
+    """Design spec `2026-09-30-canonical-lifecycle-design.md` section 3,
+    case 3: the next free activation id, `role = 'canonical'`, superseding
+    the one named, and from then on the current canonical."""
+    first = req.submit("k-rep-1", "neural", "wl_works", selection, {}, None)
+    replacement = req.submit_replacement("k-rep-2", "neural", "wl_works", selection, {}, None,
+                                         supersedes_activation_id=first["activation_id"])
+    row = (req.Activation & replacement).fetch1()
+    assert (row["activation_id"], row["role"], row["supersedes"]) == (1, "canonical", 0)
+    assert {k: req.current_canonical(selection)[k] for k in req.Activation.primary_key} == replacement
+
+
+def test_a_retried_replacement_returns_the_same_activation(req, selection):
+    """Case 4: the protocol's ordinary retry, same idempotency key."""
+    first = req.submit("k-retry-1", "neural", "wl_works", selection, {}, None)
+    once = req.submit_replacement("k-retry-2", "neural", "wl_works", selection, {}, None, supersedes_activation_id=0)
+    again = req.submit_replacement("k-retry-2", "neural", "wl_works", selection, {}, None, supersedes_activation_id=0)
+    assert once == again and len(req.Activation & selection) == 2 and first["activation_id"] == 0
+
+
+@pytest.mark.parametrize("target", ["superseded", "unknown", "derivative"])
+def test_a_replacement_of_anything_but_the_current_canonical_is_a_conflict(req, selection, target):
+    """Case 5: wl.works and this host disagree about which canonical is
+    current. Refused, naming the current one, and nothing is recorded."""
+    req.submit(f"k-stale-1-{target}", "neural", "wl_works", selection, {}, None)
+    req.submit_replacement(f"k-stale-2-{target}", "neural", "wl_works", selection, {}, None, supersedes_activation_id=0)
+    derivative = req.submit_derivative(f"k-stale-3-{target}", "neural", "wl_works", selection, [1, 2], {}, None)
+    named = {"superseded": 0, "unknown": 77, "derivative": derivative["activation_id"]}[target]
+    with pytest.raises(req.SupersedeConflict, match="current canonical is activation 1"):
+        req.submit_replacement(f"k-stale-4-{target}", "neural", "wl_works", selection, {}, None, supersedes_activation_id=named)
+    assert not req.Request & {"idempotency_key": f"k-stale-4-{target}"}
+    assert len(req.Activation & selection & "role = 'canonical'") == 2
+
+
+def test_a_canonical_can_name_its_block_set(req, selection):
+    """Section 3: how wl.works leaves out a bad block, on a first canonical
+    or a replacement. The builder reads the set from `ActivationBlock`."""
+    from wl_preproc.nwb.gather import _block_set
+
+    first = req.submit("k-blocks-1", "neural", "wl_works", selection, {}, None, block_ids=[1, 3])
+    replacement = req.submit_replacement("k-blocks-2", "neural", "wl_works", selection, {}, None,
+                                         supersedes_activation_id=0, block_ids=[2])
+    session_key = {k: selection[k] for k in ("subject", "session_datetime")}
+    for key, expected in ((first, [1, 3]), (replacement, [2])):
+        row = (req.Activation & key).fetch1()
+        assert sorted(int(b) for b in (req.ActivationBlock & key).to_arrays("block_id")) == expected
+        assert [b["block_id"] for b in _block_set(key, row, session_key)] == expected
+
+
+def test_a_reused_key_for_another_replacement_is_key_reuse(req, selection):
+    req.submit("k-reuse-1", "neural", "wl_works", selection, {}, None)
+    req.submit_replacement("k-reuse-2", "neural", "wl_works", selection, {}, None, supersedes_activation_id=0)
+    with pytest.raises(req.KeyReuseError):
+        req.submit_replacement("k-reuse-2", "neural", "wl_works", selection, {}, None, supersedes_activation_id=1)
+
+
+def test_a_replacement_waits_for_the_montage_lock(req, selection, monkeypatch):
+    """Two replacements of one canonical, racing, must not both succeed:
+    each takes the montage's lock before its transaction, whose consistent
+    snapshot would otherwise hide the other's commit. Held elsewhere, the
+    lock is a retryable failure, never a second replacement."""
+    import datajoint as dj
+
+    req.submit("k-lock-1", "neural", "wl_works", selection, {}, None)
+    monkeypatch.setattr(req, "_REPLACEMENT_LOCK_WAIT_S", 0)
+    other = _raw_connection()
+    try:
+        with other.cursor() as cursor:
+            cursor.execute("SELECT GET_LOCK(%s, 0)", (req._replacement_lock_name(selection),))
+            assert cursor.fetchone()[0] == 1
+        with pytest.raises(dj.DataJointError, match="another replacement"):
+            req.submit_replacement("k-lock-2", "neural", "wl_works", selection, {}, None, supersedes_activation_id=0)
+    finally:
+        other.close()
+    assert len(req.Activation & selection) == 1
```

```diff
--- a/tests/schema/test_guardrails.py
+++ b/tests/schema/test_guardrails.py
@@ -885,34 +885,72 @@ def test_no_code_path_writes_activation_supersedes():
     RST double-backticks, never a Python quote character next to an `=` or
     `[`. Confirmed all patterns fire on their intended shape and stay
     silent against the committed source before being relied on here.
+
+    *Narrowed 2026-09-30* (design spec
+    `2026-09-30-canonical-lifecycle-design.md` section 3): one function,
+    `request.py::submit_replacement`, now writes it, and
+    `_supersedes_writes` below skips exactly that function.
     """
+    offenders = []
+    for path in SOURCE_ROOT.rglob("*.py"):
+        rel = path.relative_to(SOURCE_ROOT.parent)
+        offenders.extend(f"{rel}:{lineno}: {line[:70]}"
+                         for lineno, line in _supersedes_writes(rel.as_posix(), path.read_text()))
+    assert not offenders, (
+        "a source line writes 'supersedes' (as a dict key, a subscript "
+        "assignment, or a keyword argument) outside "
+        f"{_SUPERSEDES_WRITER[0]}::{_SUPERSEDES_WRITER[1]}; a derivative never "
+        "supersedes a canonical, and only a replacement canonical, which that "
+        "one function creates, supersedes anything (design spec "
+        "`2026-09-30-canonical-lifecycle-design.md` section 3):\n  " + "\n  ".join(offenders)
+    )
+
+
+# The one function allowed to write `Activation.supersedes`, since the
+# canonical lifecycle (design spec `2026-09-30-canonical-lifecycle-design.md`
+# section 3). Until then the rule allowed no writer at all.
+_SUPERSEDES_WRITER = ("wl_preproc/schema/request.py", "submit_replacement")
+
+
+def _supersedes_writes(rel: str, text: str) -> list[tuple[int, str]]:
+    """Every line of `text` that writes `supersedes`, with its number, but
+    not inside `_SUPERSEDES_WRITER`. The enclosing function is the last
+    top-level `def` above the line."""
     import re
 
     dict_key_write = re.compile(r"""["']supersedes["']\s*:""")
     subscript_write = re.compile(r"""\[\s*["']supersedes["']\s*\]\s*=(?!=)""")
     keyword_write = re.compile(r"""supersedes\s*=(?!=)""")
     ddl_declaration = re.compile(r"""^supersedes\s*=\s*\w+\s*:""")
-
-    offenders = []
-    for path in SOURCE_ROOT.rglob("*.py"):
-        for lineno, line in enumerate(path.read_text().splitlines(), 1):
-            stripped = line.strip()
-            if stripped.startswith("#"):
-                continue
-            if (
-                dict_key_write.search(stripped)
-                or subscript_write.search(stripped)
-                or (keyword_write.search(stripped) and not ddl_declaration.match(stripped))
-            ):
-                rel = path.relative_to(SOURCE_ROOT.parent)
-                offenders.append(f"{rel}:{lineno}: {stripped[:70]}")
-    assert not offenders, (
-        "a source line writes 'supersedes' (as a dict key, a subscript "
-        "assignment, or a keyword argument); Activation.supersedes must "
-        "remain unwritten by every code path -- a derivative never "
-        "supersedes a canonical, and nothing regenerates a canonical yet "
-        "either:\n  " + "\n  ".join(offenders)
-    )
+    top_level_def = re.compile(r"""^def (\w+)\(""")
+    writes, function = [], None
+    for lineno, line in enumerate(text.splitlines(), 1):
+        if line.startswith(("def ", "class ")):
+            match = top_level_def.match(line)
+            function = match.group(1) if match else None
+        stripped = line.strip()
+        if stripped.startswith("#"):
+            continue
+        if (
+            dict_key_write.search(stripped)
+            or subscript_write.search(stripped)
+            or (keyword_write.search(stripped) and not ddl_declaration.match(stripped))
+        ) and (rel, function) != _SUPERSEDES_WRITER:
+            writes.append((lineno, stripped))
+    return writes
+
+
+def test_the_supersedes_rule_allows_exactly_one_function():
+    """The narrowing admits only `submit_replacement`: the same write in any
+    other function of the same module, or in the same-named function of
+    another module, is still caught."""
+    writer = "def submit_replacement(x):\n    row = {'supersedes': x}\n"
+    other = "def submit(x):\n    row = {'supersedes': x}\n"
+    assert _supersedes_writes("wl_preproc/schema/request.py", writer) == []
+    assert _supersedes_writes("wl_preproc/schema/request.py", other) == [(2, "row = {'supersedes': x}")]
+    assert _supersedes_writes("wl_preproc/nwb/build.py", writer) == [(2, "row = {'supersedes': x}")]
+    after = writer + "\n\ndef later(x):\n    row['supersedes'] = x\n"
+    assert _supersedes_writes("wl_preproc/schema/request.py", after) == [(6, "row['supersedes'] = x")]
 
 
 def test_every_table_documents_its_key_in_schema(all_tables):
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_request.py tests/schema/test_guardrails.py tests/nwb -q --tb=line -p no:cacheprovider`
Expected: 8 failed, 93 passed, 1 skipped. `no attribute 'submit_replacement'` (six), `submit()` refusing `block_ids` (`TypeError`), and the lock test's missing `_REPLACEMENT_LOCK_WAIT_S`.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/schema/request.py
+++ b/wl_preproc/schema/request.py
@@ -64,6 +64,14 @@ schema = dj.Schema()
 _ORIGIN_ENUM = "enum('ingest','wl_works','cli','auto')"
 
 
+class SupersedeConflict(dj.DataJointError):
+    """A replacement naming an activation that is not its montage's current
+    canonical: wl.works and this host disagree about which file is current
+    (design spec `2026-09-30-canonical-lifecycle-design.md` section 3, case
+    5). Like `KeyReuseError`, a disagreement resending cannot fix, so the
+    responder answers it `409`."""
+
+
 class KeyReuseError(dj.DataJointError):
     """An idempotency key reused for materially different content — see
     ``_reject_key_reuse``, this module's only raiser. A caller's mistake that
@@ -404,6 +412,7 @@ def submit(
     selection: dict,
     payload: dict,
     requested_by: str | None = None,
+    block_ids: list[int] | tuple[int, ...] = (),
 ) -> dict:
     """Record a request and the canonical activation it selects, atomically.
 
@@ -534,6 +543,15 @@ def submit(
             },
             skip_duplicates=True,
         )
+        # A canonical that names its block set -- how wl.works leaves out a
+        # bad block (design spec `2026-09-30-canonical-lifecycle-design.md`
+        # section 3). Without one, the builder takes every block in the
+        # montage, as before.
+        if block_ids:
+            ActivationBlock.insert(
+                [{**key, "block_id": block_id} for block_id in sorted(set(block_ids))],
+                skip_duplicates=True,
+            )
         return key
 
 
@@ -570,6 +588,143 @@ def is_superseded(key: dict) -> bool:
     return int(key["activation_id"]) in {int(value) for value in replaced}
 
 
+# How long a replacement waits for its montage's lock before failing
+# retryably. A module constant so a test can make it 0.
+_REPLACEMENT_LOCK_WAIT_S = 10
+
+
+def _replacement_lock_name(montage_key: dict) -> str:
+    """One MySQL named lock per montage, for replacements. Hashed: MySQL
+    allows 64 characters, and a subject and a datetime can exceed that."""
+    montage = "|".join(str(montage_key[k]) for k in ("subject", "session_datetime", "montage_id"))
+    return "wlpp_canonical_" + hashlib.sha256(montage.encode("utf-8")).hexdigest()[:40]
+
+
+def submit_replacement(
+    idempotency_key: str,
+    task_type: str,
+    origin: str,
+    selection: dict,
+    payload: dict,
+    requested_by: str | None = None,
+    *,
+    supersedes_activation_id: int,
+    block_ids: list[int] | tuple[int, ...] = (),
+) -> dict:
+    """Record a request and the canonical activation that replaces the
+    montage's current one, `supersedes_activation_id`: at the montage's next free
+    activation id, with `supersedes` set and, if given, its block set
+    (design spec `2026-09-30-canonical-lifecycle-design.md` section 3, case
+    3). The superseded activation and its file are left exactly as they are.
+
+    A retry under the same idempotency key returns the same activation
+    (case 4). Naming anything but the current canonical raises
+    `SupersedeConflict` (case 5) and records nothing.
+
+    **The one writer of `Activation.supersedes`**
+    (`tests/schema/test_guardrails.py::_SUPERSEDES_WRITER`).
+
+    **Serialised per montage by a named lock, taken BEFORE the transaction.**
+    DataJoint starts every transaction `WITH CONSISTENT SNAPSHOT`
+    (`datajoint/adapters/mysql.py`), so a check made inside one cannot see
+    a rival replacement committed after it began: two replacements of the
+    same canonical would both pass and fork the chain. Holding the lock
+    before the snapshot is taken means each sees the other's commit. A
+    named lock sits outside InnoDB's row locks, so it cannot deadlock with
+    `submit_derivative`'s allocation in the same montage (see
+    `_locking_read_one`). Held elsewhere for longer than
+    `_REPLACEMENT_LOCK_WAIT_S`, it fails as an ordinary, retryable
+    `DataJointError`.
+    """
+    if not schema.is_activated():
+        raise dj.DataJointError(
+            "request.activate(prefix) must run before submit_replacement() — "
+            "the Request/Activation tables are not yet bound to a database."
+        )
+    if dj.conn().in_transaction:
+        raise dj.DataJointError(
+            "submit_replacement() opens its own transaction and DataJoint "
+            "transactions do not nest, so it cannot be called from inside "
+            "one. Call it as its own unit of work; see submit()'s docstring."
+        )
+    import datetime as _dt
+
+    montage_key = {k: selection[k] for k in ("subject", "session_datetime", "montage_id")}
+    selection_key = {**montage_key, "role": "canonical", "supersedes": int(supersedes_activation_id)}
+    connection = dj.conn()
+    lock = _replacement_lock_name(montage_key)
+    if connection.query("SELECT GET_LOCK(%s, %s)", args=(lock, _REPLACEMENT_LOCK_WAIT_S)).fetchone()[0] != 1:
+        raise dj.DataJointError(
+            f"another replacement of montage {montage_key!r} holds its lock; try again"
+        )
+    try:
+        with connection.transaction:
+            prior = Request & {"idempotency_key": idempotency_key}
+            if prior:
+                _reject_key_reuse(
+                    prior.fetch1(),
+                    idempotency_key=idempotency_key,
+                    task_type=task_type,
+                    origin=origin,
+                    payload=payload,
+                    requested_by=requested_by,
+                    selection_key=selection_key,
+                )
+                produced = (Activation & {"request_key": idempotency_key} & selection_key).to_dicts()
+                if produced:
+                    return {k: produced[0][k] for k in Activation.primary_key}
+            current = current_canonical(montage_key)
+            if current is None or current["activation_id"] != int(supersedes_activation_id):
+                named = "none" if current is None else f"activation {current['activation_id']}"
+                raise SupersedeConflict(
+                    f"activation {supersedes_activation_id} is not the current canonical of montage "
+                    f"{montage_key['montage_id']}; the current canonical is {named}. wl.works and "
+                    "this host disagree about which file is current: a person should look."
+                )
+            if not prior:
+                Request.insert1(
+                    {
+                        "idempotency_key": idempotency_key,
+                        "task_type": task_type,
+                        "origin": origin,
+                        "payload": payload,
+                        "requested_by": requested_by,
+                        "requested_at": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None),
+                    }
+                )
+            used = (Activation & montage_key).to_arrays("activation_id")
+            activation_id = int(max(used) + 1) if len(used) else 1
+            for _ in range(_MAX_DERIVATIVE_ALLOCATE_ATTEMPTS):
+                key = {**montage_key, "activation_id": activation_id}
+                try:
+                    _insert_new_derivative(
+                        {
+                            **key,
+                            "role": "canonical",
+                            "request_key": idempotency_key,
+                            "created_at": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None),
+                            "supersedes": int(supersedes_activation_id),
+                        }
+                    )
+                    break
+                except dj.errors.DuplicateError:
+                    # A derivative took this id meanwhile: the lock above
+                    # serialises replacements, not derivatives.
+                    activation_id += 1
+            else:
+                raise dj.DataJointError(
+                    f"submit_replacement: exhausted {_MAX_DERIVATIVE_ALLOCATE_ATTEMPTS} "
+                    f"attempts to allocate an activation_id for {montage_key!r}"
+                )
+            if block_ids:
+                ActivationBlock.insert(
+                    [{**key, "block_id": block_id} for block_id in sorted(set(block_ids))]
+                )
+            return key
+    finally:
+        connection.query("SELECT RELEASE_LOCK(%s)", args=(lock,))
+
+
 _MAX_DERIVATIVE_ALLOCATE_ATTEMPTS = 10
 
 
```

```diff
--- a/wl_preproc/nwb/gather.py
+++ b/wl_preproc/nwb/gather.py
@@ -55,8 +55,12 @@ def _aware_utc(value: datetime.datetime) -> datetime.datetime:
 def _block_set(activation_key: dict, activation: dict, session_key: dict) -> list[dict]:
     from wl_preproc.schema import core, request
 
-    if activation["role"] == "derivative":
-        rows = (core.Block & (request.ActivationBlock & activation_key).proj()).to_dicts()
+    # A derivative always names its blocks; since the canonical lifecycle a
+    # canonical may too (design spec `2026-09-30-canonical-lifecycle-design.md`
+    # section 3). Without named blocks, a canonical takes its montage's.
+    named = request.ActivationBlock & activation_key
+    if activation["role"] == "derivative" or named:
+        rows = (core.Block & named.proj()).to_dicts()
     else:
         montage = (core.Montage & activation_key).fetch1()
         rows = [row for row in (core.Block & session_key).to_dicts()
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 101 passed, 1 skipped.

- [ ] **Step 5: Mutation checks.**
  - T2a (`request.py`): `if current is None or current["activation_id"] != int(supersedes_activation_id):` becomes `if current is None:` [`test_a_replacement_of_anything_but_the_current_canonical_is_a_conflict`].
  - T2b (`request.py`): the lock's `.fetchone()[0] != 1:` becomes `.fetchone()[0] == 7:` [`test_a_replacement_waits_for_the_montage_lock`].
  - T2c (`gather.py`): `if activation["role"] == "derivative" or named:` becomes `if activation["role"] == "derivative":` [`test_a_canonical_can_name_its_block_set`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/request.py wl_preproc/nwb/gather.py tests/schema/test_request.py tests/schema/test_guardrails.py
git commit -m "feat(request): a replacement canonical supersedes the current one at the next id, under a per-montage lock; a canonical may name its block set; the supersedes rule allows exactly that one writer

<trailer lines>"
```

---

### Task 3: The protocol

**Files:**
- Modify: `wl_preproc/responder/jobs.py`, `wl_preproc/responder/server.py`, `docs/ops/lab-host-protocol.md`
- Test: `tests/responder/test_jobs.py`, `tests/responder/test_http.py`

**Interfaces:**
- **Consumes:** Task 2's `submit_replacement`, `SupersedeConflict` and `submit(..., block_ids=)`.
- **Produces:**
  - `accept()` reads `selection["role"]` (`canonical` or `derivative`) and `selection["supersedes_activation_id"]`.
  - `jobs._lifecycle_role(selection, block_ids) -> bool` (canonical or not), which raises `ValueError` for the refused combinations before any database read.
  - `server._translate_accept_errors` answers `SupersedeConflict` with `ConflictError`, as it answers `KeyReuseError`.

- [ ] **Step 1: Write the failing tests.** Apply these diffs:

```diff
--- a/tests/responder/test_jobs.py
+++ b/tests/responder/test_jobs.py
@@ -1018,3 +1018,106 @@ def test_a_request_without_details_leaves_the_subject_alone(landed_session, pref
 
     assert (pipeline.subject.Subject & {"subject": subject}).fetch1() == before
     assert len(pipeline.subject.Subject.Species & {"subject": subject}) == 0
+
+
+# -- The canonical lifecycle (design spec 2026-09-30-canonical-lifecycle-design.md section 3)
+
+_LC_BOUNDARIES = [{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}]
+_LC_BLOCKS = [
+    {"block_id": block_id, "task_type": "neural", "start_s": start_s, "end_s": end_s, "works_block_id": None}
+    for block_id, (start_s, end_s) in enumerate(((0.0, 4.0), (4.0, 8.0), (8.0, 12.0)), start=1)
+]
+
+
+def _lifecycle_job(subject, session_datetime, key, **selection) -> JobRequest:
+    return JobRequest(
+        domain="neural",
+        selection={"session_datetime": session_datetime, "montage_id": 0, **selection},
+        parameters={},
+        idempotency_key=key,
+        metadata=MetadataBundle(blocks=_LC_BLOCKS, montage_boundaries=_LC_BOUNDARIES, probes=[],
+                                experimenter="jw", subject=subject, task_types=[]),
+    )
+
+
+def test_a_canonical_request_can_name_its_block_set(landed_session, prefix):
+    """How wl.works leaves out a bad block: `role: canonical` with
+    `block_ids`. Without the role, `block_ids` still means a derivative."""
+    from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema import request as schema_request
+
+    when = datetime.datetime(2027, 5, 20, 9, 0)
+    landed_session("jblc001", when)
+    key = accept(_lifecycle_job("jblc001", when, "jblc001-k1", role="canonical", block_ids=[1, 3]), prefix=prefix)
+    assert key["activation_id"] == 0
+    assert (schema_request.Activation & key).fetch1("role") == "canonical"
+    assert sorted(int(b) for b in (schema_request.ActivationBlock & key).to_arrays("block_id")) == [1, 3]
+    derivative = accept(_lifecycle_job("jblc001", when, "jblc001-k2", block_ids=[1, 3]), prefix=prefix)
+    assert (schema_request.Activation & derivative).fetch1("role") == "derivative"
+
+
+def test_a_replacement_request_supersedes_the_named_canonical(landed_session, prefix):
+    from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema import request as schema_request
+
+    when = datetime.datetime(2027, 5, 20, 10, 0)
+    landed_session("jblc002", when)
+    first = accept(_lifecycle_job("jblc002", when, "jblc002-k1"), prefix=prefix)
+    job = _lifecycle_job("jblc002", when, "jblc002-k2", role="canonical", supersedes_activation_id=0,
+                         block_ids=[1, 2])
+    replacement = accept(job, prefix=prefix)
+    row = (schema_request.Activation & replacement).fetch1()
+    assert (row["activation_id"], row["role"], row["supersedes"]) == (1, "canonical", first["activation_id"])
+    assert accept(job, prefix=prefix) == replacement
+    assert accept(_lifecycle_job("jblc002", when, "jblc002-k3"), prefix=prefix) == replacement
+
+
+def test_a_replacement_of_a_superseded_canonical_is_a_conflict(landed_session, prefix):
+    from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema.request import SupersedeConflict
+
+    when = datetime.datetime(2027, 5, 20, 11, 0)
+    landed_session("jblc003", when)
+    accept(_lifecycle_job("jblc003", when, "jblc003-k1"), prefix=prefix)
+    accept(_lifecycle_job("jblc003", when, "jblc003-k2", role="canonical", supersedes_activation_id=0), prefix=prefix)
+    with pytest.raises(SupersedeConflict, match="current canonical is activation 1"):
+        accept(_lifecycle_job("jblc003", when, "jblc003-k3", role="canonical", supersedes_activation_id=0),
+               prefix=prefix)
+
+
+@pytest.mark.parametrize("selection", [
+    {"supersedes_activation_id": 0},
+    {"role": "derivative", "supersedes_activation_id": 0, "block_ids": [1]},
+    {"role": "derivative"},
+    {"role": "bogus"},
+    {"role": "canonical", "supersedes_activation_id": -1},
+    {"role": "canonical", "supersedes_activation_id": True},
+    {"role": "canonical", "supersedes_activation_id": "0"},
+    {"role": "canonical", "block_ids": [99]},
+])
+def test_a_selection_the_lifecycle_refuses_is_a_value_error(landed_session, prefix, selection):
+    """Section 3's refusals, each a `422` over HTTP: only a canonical
+    supersedes, a derivative names its blocks, and an activation id is one
+    non-negative integer."""
+    from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema import request as schema_request
+
+    when = datetime.datetime(2027, 5, 20, 12, 0)
+    landed_session("jblc004", when)
+    key = f"jblc004-{sorted(selection.items())!r}"
+    with pytest.raises(ValueError):
+        accept(_lifecycle_job("jblc004", when, key, **selection), prefix=prefix)
+    assert not schema_request.Request & {"idempotency_key": key}
+
+
+def test_a_canonical_role_with_no_blocks_takes_the_whole_montage(landed_session, prefix):
+    """`role: canonical` with an empty `block_ids` is the plain canonical:
+    no named block set, so the builder takes every block in the montage."""
+    from wl_preproc.responder.jobs import accept
+    from wl_preproc.schema import request as schema_request
+
+    when = datetime.datetime(2027, 5, 20, 13, 0)
+    landed_session("jblc005", when)
+    key = accept(_lifecycle_job("jblc005", when, "jblc005-k1", role="canonical", block_ids=[]), prefix=prefix)
+    assert key["activation_id"] == 0 and (schema_request.Activation & key).fetch1("role") == "canonical"
+    assert not schema_request.ActivationBlock & key
```

```diff
--- a/tests/responder/test_http.py
+++ b/tests/responder/test_http.py
@@ -1101,6 +1101,22 @@ def test_translate_accept_errors_turns_key_reuse_into_a_conflict_error(monkeypat
         server_module._translate_accept_errors(object(), prefix=prefix)
 
 
+def test_translate_accept_errors_turns_a_stale_replacement_into_a_conflict_error(monkeypatch, prefix):
+    """A replacement naming a canonical that is not current (design spec
+    `2026-09-30-canonical-lifecycle-design.md` section 3, case 5) is a
+    disagreement for a person, like key reuse: `409`, never retried."""
+    import wl_preproc.responder.server as server_module
+    from wl_preproc.schema.request import SupersedeConflict
+
+    def boom(request, prefix=None):
+        raise SupersedeConflict("activation 0 is not the current canonical of montage 0")
+
+    monkeypatch.setattr("wl_preproc.responder.jobs.accept", boom)
+
+    with pytest.raises(ConflictError, match="not the current canonical"):
+        server_module._translate_accept_errors(object(), prefix=prefix)
+
+
 @pytest.mark.parametrize(
     "error_name",
     ["LostConnectionError", "AccessError", "MissingTableError", "IntegrityError", "ThreadSafetyError"],
@@ -1178,6 +1194,34 @@ def test_a_lost_connection_error_through_the_real_seam_reaches_the_client_as_500
     assert json.loads(body)["error"].startswith("LostConnectionError:")
 
 
+
+def test_a_stale_replacement_through_the_real_seam_reaches_the_client_as_409(start_server, monkeypatch, prefix):
+    """Design spec `2026-09-30-canonical-lifecycle-design.md` section 3, case
+    5, end to end over a real socket through the real
+    `_translate_accept_errors`."""
+    import wl_preproc.responder.server as server_module
+    from wl_preproc.schema.request import SupersedeConflict
+
+    def boom(request, prefix=None):
+        raise SupersedeConflict("activation 0 is not the current canonical of montage 0")
+
+    monkeypatch.setattr("wl_preproc.responder.jobs.accept", boom)
+    base = start_server(TOKEN, _health_ok, lambda request: server_module._translate_accept_errors(request, prefix=prefix))
+    status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=_valid_job_payload())
+    assert status == 409 and "not the current canonical" in json.loads(body)["error"]
+
+
+def test_a_supersedes_without_a_canonical_role_reaches_the_client_as_422(start_server, prefix):
+    """Section 3: only a canonical supersedes. The real `jobs.accept`,
+    refusing before it reads the database."""
+    import wl_preproc.responder.server as server_module
+
+    payload = _valid_job_payload()
+    payload["selection"] = {**payload["selection"], "supersedes_activation_id": 0}
+    base = start_server(TOKEN, _health_ok, lambda request: server_module._translate_accept_errors(request, prefix=prefix))
+    status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=payload)
+    assert status == 422 and "only a canonical supersedes" in body.decode("utf-8")
+
 def test_a_datajoint_error_from_accept_fn_bypassing_translation_is_still_a_safe_500(start_server):
     """Defense in depth, not the documented mapping: `handler.py` imports
     no DataJoint, so it cannot recognise a `dj.DataJointError` by name at
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/responder tests/schema/test_request.py tests/schema/test_guardrails.py -q --tb=line -p no:cacheprovider`
Expected: 13 failed, 196 passed. Two of the new tests pass before the code, by design. They pin behaviour that already holds, and guard it against the new routing: an unknown block in a canonical's block set is refused (`[selection7]`), and a request whose `role` is not yet read stays the whole-montage canonical (`test_a_canonical_role_with_no_blocks_takes_the_whole_montage`).

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/responder/jobs.py
+++ b/wl_preproc/responder/jobs.py
@@ -140,6 +140,9 @@ _REQUIRED_SELECTION_KEYS = ("session_datetime", "montage_id")
 # the same errno as the integer case). None of the three appears in
 # DataJoint's MySQL adapter's translated-error list.
 _MONTAGE_ID_RANGE = (-128, 127)  # core.Montage.montage_id : tinyint
+# request.Activation.activation_id : int, and a superseded one is never
+# negative: the allocator starts at 0.
+_ACTIVATION_ID_RANGE = (0, 2**31 - 1)
 _BLOCK_ID_RANGE = (-32768, 32767)  # core.Block.block_id : smallint
 _TASK_TYPE_MAX_LEN = 32  # core.Block.task_type : varchar(32)
 _WORKS_BLOCK_ID_MAX_LEN = 64  # core.Block.works_block_id : varchar(64)
@@ -206,6 +209,29 @@ def _coerce_session_datetime(value) -> datetime.datetime:
     )
 
 
+def _lifecycle_role(selection: dict, block_ids: list) -> bool:
+    """Whether the request asks for a canonical, from `selection`'s optional
+    `role` and `supersedes_activation_id` (design spec
+    `2026-09-30-canonical-lifecycle-design.md` section 3). Without a `role`
+    a request means what it always meant: `block_ids` makes a derivative,
+    none a canonical over the whole montage. Raises `ValueError` (a `422`)
+    for the combinations section 3 refuses, before anything is written."""
+    role = selection.get("role")
+    if role not in (None, "canonical", "derivative"):
+        raise ValueError(f"selection['role'] must be 'canonical' or 'derivative', got {role!r}")
+    if "supersedes_activation_id" in selection:
+        if role != "canonical":
+            raise ValueError(
+                "selection['supersedes_activation_id'] needs selection['role'] == 'canonical': only "
+                "a canonical supersedes another (parent spec section 8.3)"
+            )
+        _reject_out_of_range_int(selection["supersedes_activation_id"],
+                                 name="selection['supersedes_activation_id']", bounds=_ACTIVATION_ID_RANGE)
+    if role == "derivative" and not block_ids:
+        raise ValueError("selection['role'] 'derivative' needs block_ids: a derivative is its block set")
+    return role == "canonical" or (role is None and not block_ids)
+
+
 def _reject_out_of_range_int(value, *, name: str, bounds: tuple[int, int]) -> None:
     low, high = bounds
     if isinstance(value, bool) or not isinstance(value, int):
@@ -450,6 +476,9 @@ def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
     """
     selection = request.selection
     _require_selection_keys(selection)
+    # Before anything reads the database: a malformed lifecycle selection
+    # is the caller's to fix, whatever this host holds.
+    canonical = _lifecycle_role(selection, selection.get("block_ids") or [])
 
     metadata = request.metadata
     _reject_oversized_subject(metadata.subject)
@@ -556,7 +585,7 @@ def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
     # test_accept_normalises_an_aware_datetime_anywhere_in_the_stored_payload.
     payload = request.model_dump(mode="json")
 
-    if block_ids:
+    if not canonical:
         return schema_request.submit_derivative(
             idempotency_key=request.idempotency_key,
             task_type=request.domain,
@@ -567,6 +596,17 @@ def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
             requested_by=metadata.experimenter,
         )
 
+    if "supersedes_activation_id" in selection:
+        return schema_request.submit_replacement(
+            idempotency_key=request.idempotency_key,
+            task_type=request.domain,
+            origin="wl_works",
+            selection=montage_key,
+            payload=payload,
+            requested_by=metadata.experimenter,
+            supersedes_activation_id=selection["supersedes_activation_id"],
+            block_ids=list(block_ids),
+        )
     return schema_request.submit(
         idempotency_key=request.idempotency_key,
         task_type=request.domain,
@@ -574,4 +614,5 @@ def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
         selection=montage_key,
         payload=payload,
         requested_by=metadata.experimenter,
+        block_ids=list(block_ids),
     )
```

```diff
--- a/wl_preproc/responder/server.py
+++ b/wl_preproc/responder/server.py
@@ -145,7 +145,7 @@ from wl_preproc.responder import health, jobs
 from wl_preproc.responder import nwb as nwb_endpoints
 from wl_preproc.responder.handler import ConflictError, make_handler
 from wl_preproc.schema import DEFAULT_PREFIX
-from wl_preproc.schema.request import KeyReuseError
+from wl_preproc.schema.request import KeyReuseError, SupersedeConflict
 
 
 def _translate_accept_errors(request, *, prefix: str) -> dict:
@@ -168,7 +168,10 @@ def _translate_accept_errors(request, *, prefix: str) -> dict:
     """
     try:
         return jobs.accept(request, prefix=prefix)
-    except KeyReuseError as exc:
+    except (KeyReuseError, SupersedeConflict) as exc:
+        # Both are disagreements resending cannot fix: a reused key, or a
+        # replacement naming a canonical that is no longer current (design
+        # spec `2026-09-30-canonical-lifecycle-design.md` section 3).
         raise ConflictError(str(exc)) from exc
 
 
```

Then document the keys. Apply this diff to `docs/ops/lab-host-protocol.md`:

````diff
--- a/docs/ops/lab-host-protocol.md
+++ b/docs/ops/lab-host-protocol.md
@@ -350,6 +350,17 @@ than something one side can do quietly.
   `block_ids`, when present and non-empty, makes the request a **derivative** activation
   over that hand-picked block set; absent or empty makes it the **canonical** activation
   for `(session, montage)`.
+  **Since the canonical lifecycle** (design spec `2026-09-30-canonical-lifecycle-design.md`
+  §3), two optional keys say more:
+  - `"role": "canonical"` with `block_ids` asks for a canonical over those blocks. This is
+    how wl.works leaves out a bad block. `"role": "derivative"` means what `block_ids`
+    alone means.
+  - `"supersedes_activation_id": N`, with `"role": "canonical"`, is a **replacement**: a
+    new canonical superseding `N`, which must be the montage's current canonical. The
+    superseded file stays where it is, readable, and `GET /nwb` marks it. A replacement
+    naming anything else is a `409`.
+  - A canonical request with no `supersedes_activation_id`, for a montage that already has
+    one, returns the montage's **current** canonical.
 - **`metadata`** is the bundle this host needs from the ELN, and it is the reason this
   protocol works pull-only: everything wl-preproc needs arrives inbound with the request,
   because this host cannot call wl.works to ask. `montage_boundaries` and `blocks` are
@@ -372,7 +383,8 @@ than something one side can do quietly.
 ```
 
 `activation` is the primary key of the `Activation` row this request resolved to.
-`activation_id` is `0` for a canonical activation and a positive integer for a derivative.
+`activation_id` is `0` for a montage's first canonical activation, and a positive integer
+for a derivative or a replacement canonical.
 `session_datetime` is rendered ISO-8601 and is naive UTC.
 
 **`accepted: true` means recorded, not finished.** The responder does not compute. It
@@ -461,7 +473,7 @@ Every code this host can return, on any endpoint.
 | `404` | all | `{"error": "not found"}` | Path is not one of `/health`, `/jobs`, `/nwb`, `/nwb/active`; or a query string on any path but `/nwb`. | No |
 | `405` | all | `{"error": "method not allowed"}` | Known path, wrong verb — `GET /jobs`, `POST /health`, `GET /nwb/active`, authenticated `PUT /health`. | No |
 | `408` | `POST /jobs`, `PUT /nwb/active` | `{"error": "request timed out"}` | The declared body never fully arrived. | **Yes** |
-| `409` | `POST /jobs` | `{"error": "<what differed>"}` | Idempotency key reused for materially different content. | **No — needs a human** |
+| `409` | `POST /jobs` | `{"error": "<what differed>"}` | Idempotency key reused for materially different content; or a replacement naming a canonical that is not the montage's current one. | **No — needs a human** |
 | `414` | all | `{"error": "request line too long"}` | Over-long request line. | No |
 | `422` | `POST /jobs`, `PUT /nwb/active`, `GET /nwb` (a `since` that is not one non-negative integer) | `{"error": "…"}` or `{"error": "invalid request body", "detail": […]}` | The request is malformed, or asks for something this host cannot do — **including naming a session it has not ingested yet**. | No — fix and resend; for a not-yet-ingested session, resend once the transfer lands |
 | `431` | all | `{"error": "request header fields too large"}` | Oversized header. | No |
@@ -491,9 +503,11 @@ arrive as ordinary responses with a status line, as does every other code above.
   an oversized `subject`; **a session this host has not ingested yet** (see below); a
   `montage_id`, `block_id`, `task_type`, `works_block_id`, `start_s` or `end_s` that will
   not fit its column; a `montage_id` with no boundary on record and none supplied in the
-  request either; a `block_ids` entry naming no block anywhere; or a `block_ids` entry
-  naming a block outside its montage's `[start_s, end_s)` window. The message names what
-  was wrong.
+  request either; a `block_ids` entry naming no block anywhere; a `block_ids` entry
+  naming a block outside its montage's `[start_s, end_s)` window; a `role` other than
+  `canonical` or `derivative`; `supersedes_activation_id` without `"role": "canonical"`,
+  or not a non-negative integer; or `"role": "derivative"` without `block_ids`. The
+  message names what was wrong.
 
 **A session this host has not ingested yet is a `422`, and it is the ordinary case.** You
 know a session exists from the ELN the moment it is created; this host knows it exists only
````

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 209 passed.

- [ ] **Step 5: Mutation checks.**
  - T3a (`jobs.py`): `if role != "canonical":` becomes `if False:` [`test_a_selection_the_lifecycle_refuses_is_a_value_error`].
  - T3b (`server.py`): `except (KeyReuseError, SupersedeConflict) as exc:` becomes `except KeyReuseError as exc:` [`test_a_stale_replacement_through_the_real_seam_reaches_the_client_as_409`].
  - T3c (`jobs.py`): the routing's `if "supersedes_activation_id" in selection:` becomes `if False:` [`test_a_replacement_request_supersedes_the_named_canonical`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/responder/jobs.py wl_preproc/responder/server.py docs/ops/lab-host-protocol.md tests/responder/test_jobs.py tests/responder/test_http.py
git commit -m "feat(responder): a canonical request may name its block set or the canonical it supersedes; a stale replacement is a 409, a malformed one a 422

<trailer lines>"
```

---

### Task 4: `GET /nwb` marks a superseded file

**Files:**
- Modify: `wl_preproc/schema/nwb.py`, `wl_preproc/contracts/protocol.py`, `wl_preproc/responder/nwb.py`, `wl_preproc/nwb/build.py`, `docs/schemas/nwb_listing.json` (exported)
- Test: `tests/schema/test_nwb_build.py`

**Interfaces:**
- **Consumes:** Task 1's `is_superseded`, and 2a's `publish.record_change`.
- **Produces:**
  - `NwbChange.kind` gains `superseded`.
  - `build.note_superseded() -> int`, run at the start of `run_stage`.
  - `NwbListingEntry.superseded_by: int | None`.

**Why the daemon records the change, not the responder.** The spec (§4) says the change is recorded "when a replacement is accepted". But the responder is not under the NWB lock (`nwb/lock.py`). A `NwbChange` written there, beside a daemon pass, could commit a sequence number out of order and leave a hole in `GET /nwb`'s cursor. That is 2a's review M4, which the lock exists to close. The build stage records it on its next pass instead, once per activation. See the ruling at the end; Task 6 amends the spec.

- [ ] **Step 1: Write the failing test.** Apply this diff:

```diff
--- a/tests/schema/test_nwb_build.py
+++ b/tests/schema/test_nwb_build.py
@@ -1153,6 +1153,42 @@ def test_a_superseded_activation_that_was_never_built_is_not_built(activation, p
         (request.Activation & [new, old]).delete(prompt=False)
         (request.Request & {"idempotency_key": "nwbstep1-superseded"}).delete(prompt=False)
 
+
+def test_the_listing_marks_a_superseded_file_through_the_cursor(activation, prefix, tmp_path_factory):
+    """Design spec `2026-09-30-canonical-lifecycle-design.md` section 4: the
+    build stage records a `superseded` change for an activation with a row
+    once a replacement names it, exactly once, and `GET /nwb` with a cursor
+    then lists it with `superseded_by`."""
+    from wl_preproc.nwb.build import run_stage
+    from wl_preproc.responder.nwb import list_files
+    from wl_preproc.schema import nwb as nwb_schema
+    from wl_preproc.schema import request
+
+    session_key, _key, _blocks = activation
+    now = datetime.datetime(2027, 6, 1, 12, 0)
+    old = {**session_key, "montage_id": 1, "activation_id": 60}
+    new = {**session_key, "montage_id": 1, "activation_id": 61}
+    request.Request.insert1({"idempotency_key": "nwbstep1-listing-superseded", "task_type": "neural",
+                             "origin": "wl_works", "payload": {}, "requested_at": now})
+    request.Activation.insert1({**old, "role": "canonical", "request_key": "nwbstep1-listing-superseded",
+                                "created_at": now})
+    nwb_schema.NwbFile.insert1({**old, "status": "refused", "built_at": now, "reason": "no blocks"})
+    cursor = list_files(None, prefix=prefix)["cursor"]
+    request.Activation.insert1({**new, "role": "canonical", "request_key": "nwbstep1-listing-superseded",
+                                "created_at": now, "supersedes": 60})
+    try:
+        run_stage(tmp_path_factory.mktemp("nwb-listing-superseded"))
+        run_stage(tmp_path_factory.mktemp("nwb-listing-superseded-again"))
+        assert len(nwb_schema.NwbChange & old & {"kind": "superseded"}) == 1
+        listed = {item["activation"]["activation_id"]: item for item in list_files(cursor, prefix=prefix)["files"]
+                  if item["activation"]["montage_id"] == 1}
+        assert listed[60]["superseded_by"] == 61
+        assert listed[61]["superseded_by"] is None
+    finally:
+        (nwb_schema.NwbFile & [old, new]).delete(prompt=False)
+        (request.Activation & [new, old]).delete(prompt=False)
+        (request.Request & {"idempotency_key": "nwbstep1-listing-superseded"}).delete(prompt=False)
+
 def test_one_failing_activation_does_not_stop_the_stage(activation, prefix, monkeypatch, tmp_path_factory):
     """The stage catches a failure per activation, as the archive stage
     does: the others are recorded, the failure is reported, and the failed
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_nwb_build.py tests/responder tests/cli/test_schemas_export.py tests/schema/test_guardrails.py -q --tb=line -p no:cacheprovider`
Expected: 1 failed, 224 passed. `assert 0 == 1`: no `superseded` change was recorded.

- [ ] **Step 3: Implement.** Apply these diffs:

```diff
--- a/wl_preproc/schema/nwb.py
+++ b/wl_preproc/schema/nwb.py
@@ -71,14 +71,16 @@ class NwbFile(dj.Manual):
 class NwbChange(dj.Manual):
     definition = """
     # Every change to an activation's file that wl.works polls for: built
-    # (an NwbFile row), published, or moved between shares. The sequence only
-    # increases, and is GET /nwb's cursor (design spec
+    # (an NwbFile row), published, moved between shares, or superseded by a
+    # replacement canonical (design spec
+    # `2026-09-30-canonical-lifecycle-design.md` section 4). The sequence
+    # only increases, and is GET /nwb's cursor (design spec
     # `2026-09-29-nwb-publishing-design.md` sections 6 and 9).
     # Key: (change_seq).
     change_seq : int unsigned auto_increment
     ---
     -> NwbFile
-    kind : enum('built','published','moved')
+    kind : enum('built','published','moved','superseded')
     changed_at : datetime(6)
     """
 
```

```diff
--- a/wl_preproc/contracts/protocol.py
+++ b/wl_preproc/contracts/protocol.py
@@ -302,6 +302,9 @@ class NwbListingEntry(BaseModel):
     # `contracts/nwb_description.py::NwbDescription`, exported on its own as
     # `nwb_description.json`; null for a refused activation.
     description: dict[str, Any] | None
+    # The replacement canonical that supersedes this activation, or null
+    # (design spec `2026-09-30-canonical-lifecycle-design.md` section 4).
+    superseded_by: int | None
 
 
 class NwbListing(BaseModel):
```

```diff
--- a/wl_preproc/responder/nwb.py
+++ b/wl_preproc/responder/nwb.py
@@ -28,6 +28,7 @@ def list_files(since: int | None, prefix: str = DEFAULT_PREFIX) -> dict:
     None): its status, where its file is now, and its description."""
     from wl_preproc.nwb.publish import activation_tuple, key_of
     from wl_preproc.schema import nwb as nwb_schema
+    from wl_preproc.schema import request as request_schema
 
     nwb_schema.activate(prefix=prefix)
     changes = nwb_schema.NwbChange.proj(*_KEY).to_dicts() if since is None else \
@@ -38,6 +39,15 @@ def list_files(since: int | None, prefix: str = DEFAULT_PREFIX) -> dict:
     # columns it returns (the final review's M2).
     rows = {activation_tuple(row): row for row in (nwb_schema.NwbFile & keys).proj(
         "nwb_identifier", "status", "reason", "description").to_dicts()} if keys else {}
+    # Which activation supersedes which: the old file stays, marked.
+    successor = {}
+    montages = list({(k["subject"], k["session_datetime"], k["montage_id"]): {
+        "subject": k["subject"], "session_datetime": k["session_datetime"], "montage_id": k["montage_id"]}
+        for k in keys}.values())
+    if montages:
+        for row in (request_schema.Activation & montages & "supersedes IS NOT NULL").to_dicts():
+            successor[(row["subject"], row["session_datetime"], row["montage_id"], row["supersedes"])] = \
+                row["activation_id"]
     latest = {}
     for placement in ((nwb_schema.NwbPlacement * nwb_schema.NwbChange & keys).to_dicts() if keys else []):
         held = latest.get(activation_tuple(placement))
@@ -54,6 +64,8 @@ def list_files(since: int | None, prefix: str = DEFAULT_PREFIX) -> dict:
             "placement": None if placement is None else {
                 field: placement[field] for field in ("tier", "host", "share", "path", "n_bytes")},
             "description": row["description"],
+            "superseded_by": successor.get((key["subject"], key["session_datetime"], key["montage_id"],
+                                            key["activation_id"])),
         })
     return NwbListing.model_validate({"cursor": cursor, "files": files}).model_dump(mode="json")
 
```

```diff
--- a/wl_preproc/nwb/build.py
+++ b/wl_preproc/nwb/build.py
@@ -133,6 +133,31 @@ def record(activation_key: dict, result: BuildResult) -> None:
         nwb_schema.NwbChange.insert1({**key, "kind": "built", "changed_at": row["built_at"]})
 
 
+def note_superseded() -> int:
+    """A `superseded` change, once, for every activation with a row that a
+    replacement canonical names, so `GET /nwb`'s cursor carries it (design
+    spec `2026-09-30-canonical-lifecycle-design.md` section 4). Recorded by
+    the daemon, under the NWB lock with every other `NwbChange` writer,
+    rather than by the responder when it accepts the replacement: a second
+    writer outside the lock could commit a sequence number out of order and
+    leave a hole in the cursor. Returns how many it recorded."""
+    from wl_preproc.nwb.publish import record_change
+    from wl_preproc.schema import nwb as nwb_schema
+    from wl_preproc.schema import request
+
+    named = [{"subject": row["subject"], "session_datetime": row["session_datetime"],
+              "montage_id": row["montage_id"], "activation_id": row["supersedes"]}
+             for row in (request.Activation & "supersedes IS NOT NULL").to_dicts()]
+    if not named:
+        return 0
+    noted = (nwb_schema.NwbChange & {"kind": "superseded"}).proj(
+        "subject", "session_datetime", "montage_id", "activation_id")
+    keys = ((nwb_schema.NwbFile & named) - noted).keys()
+    for key in keys:
+        record_change(key, "superseded")
+    return len(keys)
+
+
 def run_stage(nwb_root: Path, freed: list[dict] | None = None) -> tuple[int, list[str]]:
     """The daemon's `_nwb_stage`: every activation without an `NwbFile` row,
     skipping freed sessions, superseded activations (a replacement arrived
@@ -147,6 +172,10 @@ def run_stage(nwb_root: Path, freed: list[dict] | None = None) -> tuple[int, lis
 
     freed = freed or []
     recorded, errors = 0, []
+    try:
+        note_superseded()
+    except Exception as exc:  # the builds below must still run
+        errors.append(f"NwbChange superseded: {exc}")
     for key in (request.Activation - nwb_schema.NwbFile.proj()).keys():
         if {"subject": key["subject"], "session_datetime": key["session_datetime"]} in freed:
             continue
```

Then export the schemas: `.venv/bin/python -m wl_preproc.cli.main schemas export --out docs/schemas`. Expected: `git status --short docs/schemas` lists only `docs/schemas/nwb_listing.json`, which gains `superseded_by`.

- [ ] **Step 4: Run it to verify it passes**

Run: the Step 2 command. Expected: 225 passed.

- [ ] **Step 5: Mutation checks.**
  - T4a (`build.py`): `record_change(key, "superseded")` becomes `pass` [`test_the_listing_marks_a_superseded_file_through_the_cursor`].
  - T4b (`build.py`): `keys = ((nwb_schema.NwbFile & named) - noted).keys()` becomes `keys = (nwb_schema.NwbFile & named).keys()`, which records the change every pass [the same test].
  - T4c (`nwb.py`): `"superseded_by": successor.get(` becomes `"superseded_by": None and successor.get(` [the same test].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/nwb.py wl_preproc/contracts/protocol.py wl_preproc/responder/nwb.py wl_preproc/nwb/build.py docs/schemas/nwb_listing.json tests/schema/test_nwb_build.py
git commit -m "feat(nwb): GET /nwb marks a superseded file with superseded_by, carried by a superseded change the build stage records once, under the NWB lock

<trailer lines>"
```

---

### Task 5: Rebuilding invalid files

**Files:**
- Modify: `wl_preproc/nwb/build.py`
- Test: `tests/schema/test_nwb_build.py`

**Interfaces — produces:** `build.resolved_invalid() -> list[dict]` and `build._discard(key)`, run at the start of `run_stage`.

**Compared by value.** `responder/jobs.py::_record_subject_details` writes the subject's sex, species and date of birth with no timestamp. So each pass compares an `invalid` row's stored description (`subject`) with `gather._subject`, the same reader the builder uses. The date of birth is compared as ISO text, the form the description stores. A rebuild that is still invalid then matches, so it is not rebuilt again.

- [ ] **Step 1: Write the failing test.** Apply this diff:

```diff
--- a/tests/schema/test_nwb_build.py
+++ b/tests/schema/test_nwb_build.py
@@ -1189,6 +1189,38 @@ def test_the_listing_marks_a_superseded_file_through_the_cursor(activation, pref
         (request.Activation & [new, old]).delete(prompt=False)
         (request.Request & {"idempotency_key": "nwbstep1-listing-superseded"}).delete(prompt=False)
 
+
+def test_an_invalid_file_is_rebuilt_once_its_missing_subject_details_arrive(activation, prefix, slow_share,
+                                                                            fast_share, tmp_path_factory):
+    """Design spec `2026-09-30-canonical-lifecycle-design.md` section 5.
+    Built without a date of birth, the file is `invalid`; a pass with nothing
+    changed leaves it alone; once the date arrives, the next pass rebuilds
+    it, and it is `written`. It was never published, so nothing is lost."""
+    from wl_preproc.ingest.landing import SUBJECT_BIRTH_DATE_UNKNOWN
+    from wl_preproc.nwb.build import run_stage
+    from wl_preproc.schema import nwb as nwb_schema
+    from wl_preproc.schema import pipeline
+
+    session_key, _key, blocks = activation
+    key = _derivative(session_key, blocks, prefix, blocks[0])
+    _unrecord(key, slow_share, fast_share)
+    birth = (pipeline.subject.Subject & {"subject": _SUBJECT}).fetch1("subject_birth_date")
+    pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": SUBJECT_BIRTH_DATE_UNKNOWN})
+    try:
+        run_stage(tmp_path_factory.mktemp("nwb-rebuild-first"))
+        first = (nwb_schema.NwbFile & key).fetch1()
+        assert first["status"] == "invalid"
+        run_stage(tmp_path_factory.mktemp("nwb-rebuild-unchanged"))
+        assert (nwb_schema.NwbFile & key).fetch1("built_at") == first["built_at"]
+        pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": birth})
+        run_stage(tmp_path_factory.mktemp("nwb-rebuild-after"))
+        rebuilt = (nwb_schema.NwbFile & key).fetch1()
+        assert rebuilt["status"] == "written"
+        assert rebuilt["description"]["subject"]["date_of_birth"] == birth.isoformat()
+        assert not Path(first["path"]).exists()
+    finally:
+        pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": birth})
+
 def test_one_failing_activation_does_not_stop_the_stage(activation, prefix, monkeypatch, tmp_path_factory):
     """The stage catches a failure per activation, as the archive stage
     does: the others are recorded, the failure is reported, and the failed
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_nwb_build.py tests/schema/test_guardrails.py -q --tb=line -p no:cacheprovider`
Expected: 1 failed, 66 passed. `assert 'invalid' == 'written'`: the file was not rebuilt.

- [ ] **Step 3: Implement.** Apply this diff:

```diff
--- a/wl_preproc/nwb/build.py
+++ b/wl_preproc/nwb/build.py
@@ -158,6 +158,39 @@ def note_superseded() -> int:
     return len(keys)
 
 
+def resolved_invalid() -> list[dict]:
+    """Every `invalid` activation whose subject's details differ from those
+    its file was built with -- the date of birth arrived, say (design spec
+    `2026-09-30-canonical-lifecycle-design.md` section 5). Compared by value:
+    `responder/jobs.py::_record_subject_details` keeps no timestamp. A
+    rebuild that is still invalid now matches, so it is not rebuilt again
+    until the details change once more."""
+    from wl_preproc.nwb.gather import _subject
+    from wl_preproc.schema import nwb as nwb_schema
+
+    resolved = []
+    for row in (nwb_schema.NwbFile & {"status": "invalid"}).proj("description").to_dicts():
+        current = _subject(row["subject"])
+        now = {"species": current["species"], "sex": current["sex"],
+               "date_of_birth": None if current["date_of_birth"] is None else current["date_of_birth"].isoformat()}
+        built_with = (row["description"] or {}).get("subject") or {}
+        if {field: built_with.get(field) for field in now} != now:
+            resolved.append({k: row[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")})
+    return resolved
+
+
+def _discard(key: dict) -> None:
+    """An `invalid` activation's row and scratch file, so the stage rebuilds
+    it. Never published (publishing takes only `written`), so no one has
+    annotated it."""
+    from wl_preproc.schema import nwb as nwb_schema
+
+    path = (nwb_schema.NwbFile & key).fetch1("path")
+    (nwb_schema.NwbFile & key).delete(prompt=False)
+    if path:
+        Path(path).unlink(missing_ok=True)
+
+
 def run_stage(nwb_root: Path, freed: list[dict] | None = None) -> tuple[int, list[str]]:
     """The daemon's `_nwb_stage`: every activation without an `NwbFile` row,
     skipping freed sessions, superseded activations (a replacement arrived
@@ -176,6 +209,11 @@ def run_stage(nwb_root: Path, freed: list[dict] | None = None) -> tuple[int, lis
         note_superseded()
     except Exception as exc:  # the builds below must still run
         errors.append(f"NwbChange superseded: {exc}")
+    try:
+        for key in resolved_invalid():
+            _discard(key)
+    except Exception as exc:  # the builds below must still run
+        errors.append(f"NwbFile invalid: {exc}")
     for key in (request.Activation - nwb_schema.NwbFile.proj()).keys():
         if {"subject": key["subject"], "session_datetime": key["session_datetime"]} in freed:
             continue
```

- [ ] **Step 4: Run it to verify it passes**

Run: the Step 2 command. Expected: 67 passed.

- [ ] **Step 5: Mutation checks.**
  - T5a (`build.py`): `if {field: built_with.get(field) for field in now} != now:` becomes `if False:` [`test_an_invalid_file_is_rebuilt_once_its_missing_subject_details_arrive`].
  - T5b (`build.py`): `_discard(key)` becomes `pass` [the same test].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/nwb/build.py tests/schema/test_nwb_build.py
git commit -m "feat(nwb): an invalid file is rebuilt once the subject details it was built without have arrived, compared by value

<trailer lines>"
```

---

### Task 6: What wl.works must do, the amendments, records, and the full suite

**Files:**
- Modify: `docs/pending-wl-works-amendments.md`, `docs/superpowers/specs/2026-08-12-wl-preproc-design.md`, `docs/superpowers/specs/2026-09-29-nwb-publishing-design.md`, `docs/superpowers/specs/2026-09-30-canonical-lifecycle-design.md`, `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-09-30-canonical-lifecycle.md`

- [ ] **Step 1: wl.works' half, and the parent and publishing specs' amendments.** Apply these diffs:

```diff
--- a/docs/pending-wl-works-amendments.md
+++ b/docs/pending-wl-works-amendments.md
@@ -1,12 +1,56 @@
 # Amendments to wl-works
 
-**Four are outstanding: two opened 2026-08-22, one 2026-09-28, one 2026-09-29.** The earlier two
+**Five are outstanding: two opened 2026-08-22, one 2026-09-28, one 2026-09-29, one 2026-09-30.** The earlier two
 batches are closed; their records are kept below, because
 [`specs/2026-08-12-wl-preproc-design.md`](superpowers/specs/2026-08-12-wl-preproc-design.md)
 §14 items 10–11 point at it and a reference that dead-ends teaches nothing.
 
 ---
 
+# OPEN — wl.works fires the canonical NWB, and regenerates it by naming what it replaces
+
+**Opened 2026-09-30** with the canonical lifecycle
+([`specs/2026-09-30-canonical-lifecycle-design.md`](superpowers/specs/2026-09-30-canonical-lifecycle-design.md)),
+on the requester's decision that day: **wl.works fires every canonical**. Everything the decision
+needs lives in wl.works: which probe insertions define the montages, which blocks are bad, and
+the subject's details. This host cannot ask for them, since wl.works opens every connection, and
+it must not guess a montage (parent spec §8.3, "no insertion record → no canonical").
+
+**This repository's half is built.**
+- **A canonical request may name its block set:** `selection` gains `"role": "canonical"` with
+  `block_ids`. Without `role`, a request means what it always meant.
+- **A replacement names what it supersedes:** `"role": "canonical"` with
+  `"supersedes_activation_id": N`.
+  - `N` must be the montage's current canonical.
+  - This host creates a new canonical at the next free activation id, with `supersedes = N`,
+    and answers with its key.
+  - The old file stays exactly where it is, readable, its annotations untouched.
+  - A replacement naming anything else is a `409`: wl.works and this host disagree about which
+    file is current.
+- **A canonical request without `supersedes_activation_id`,** for a montage that already has
+  one, returns its current canonical.
+- **`GET /nwb` lists a superseded file with `superseded_by`,** carried to a cursor by a
+  `superseded` change.
+- **An `invalid` file is rebuilt here** once the subject details it lacked arrive in a job
+  request. wl.works need do nothing for it.
+
+**Still open on their side:**
+1. **Run the 12-hour clock,** and wait until the ELN holds the session's insertions, block
+   verdicts and subject details. Then send a canonical job request, and re-fire while the ELN
+   is not current: the parent spec's "retryable state with no manual step" (§8.3.1) is now
+   wl.works'. A `422` naming a session this host has not ingested yet is the ordinary
+   "not yet": retry it.
+2. **Leave out bad blocks** by sending `"role": "canonical"` with `block_ids`.
+3. **Regenerate with a replacement** naming the current canonical, and treat a `409` as a
+   disagreement for a person.
+4. **Read `superseded_by`** in `GET /nwb`, alongside wl.works' own `supersedesId` and
+   `supersededAt` (Plan 24 §10.4).
+5. **Change `docs/ops/waiting-on.md`,** which says "wl-preproc generates the canonical session
+   NWB automatically some hours after the data lands". wl.works now fires it; wl-preproc still
+   generates the file.
+
+---
+
 # OPEN — NWB files: find them, select them by what they hold, and say which are active
 
 **Opened 2026-09-29** with NWB publishing
```

```diff
--- a/docs/superpowers/specs/2026-08-12-wl-preproc-design.md
+++ b/docs/superpowers/specs/2026-08-12-wl-preproc-design.md
@@ -1089,6 +1089,18 @@ activation must re-evaluate once the missing rows land** — otherwise every lat
 strands a session until somebody notices. Whatever implements the canonical trigger must treat
 "quarantined, waiting on ELN" as a *retryable* state with no manual step, not a terminal one.
 
+> **Amended 2026-09-30 by the canonical lifecycle design**
+> ([`2026-09-30-canonical-lifecycle-design.md`](2026-09-30-canonical-lifecycle-design.md)
+> §0 decision 1, the requester's): **what implements the canonical trigger is wl.works.**
+> - wl.works runs the 12-hour clock and waits on its own ELN, since the probe insertions,
+>   block verdicts and subject details all live there.
+> - It sends a canonical job request, re-fires while the ELN is not current, and regenerates
+>   with a replacement naming the canonical it supersedes.
+> - wl-preproc holds no clock and never guesses a montage.
+>
+> The 12-hour value stands, and now lives in wl.works. The requirement above, retryable with
+> no manual step, moves with it (`pending-wl-works-amendments.md`'s 2026-09-30 entry).
+
 **Item 12 shrinks but does not close.** No machine creates a block under this ruling, so no
 machine actor is needed *for blocks*. The question remains open for the canonical activation
 row itself.
@@ -1379,7 +1391,7 @@ So three of five fold into existing phases at near-zero marginal cost, and two a
 | 9 | Who creates `animal_session_block` rows, and when. **wl-preproc's half is closed** (2026-08-13, §8.3.1): it never writes, it cross-validates decoded boundaries, and it quarantines on absence — a ruling that needs nothing from wl.works. **The cross-repo half is open, and was never carried back** (found 2026-08-15 while designing 1c-2): nothing in wl.works commits it to authoring montage or block rows, or to when, and Plan 11 §3.2 still reads *"neither taken… Owner: whoever plans 11a"*. **A resolution is proposed and not ratified** (2026-08-15, §8.3.1's amendment): wl-preproc owns the *measurement* in `core.Block`/`core.Montage`, wl.works keeps the *authored record*, and `works_block_id` links them — the third option Plan 11 §3.2 named and declined to refuse. Absence then reports an unlinked block instead of quarantining. Text deferred in `docs/pending-wl-works-amendments.md`; **item stays open until it lands there** | Phase 0 |
 | 10 | ~~The X-hour canonical delay value~~ **Closed 2026-08-13 — 12 hours.** The tight end of the range: it buys morning availability and pays in regeneration, and it makes automatic re-firing of a quarantined activation load-bearing rather than optional. §8.3.1 | ~~Phase 0~~ |
 | 11 | Whether `seed` and `device` are pinned to the activation or may differ across its probe runs. wl.works flags this as unsettled; **wl-preproc is the machine that would pin them**, so this is answerable from here | Phase 2 |
-| 12 | Identity of the actor for automatic canonical activations — a system user, or a nullable `requestedBy` under an `origin` discriminator (§11). **Narrowed 2026-08-13 by item 9's ruling**: no machine creates a block, so this is now only about the activation row itself. The narrowing survives item 9's 2026-08-15 reopening, because it rests on wl-preproc's own half, which did close | Phase 1 |
+| 12 | ~~Identity of the actor for automatic canonical activations — a system user, or a nullable `requestedBy` under an `origin` discriminator (§11). **Narrowed 2026-08-13 by item 9's ruling**: no machine creates a block, so this is now only about the activation row itself. The narrowing survives item 9's 2026-08-15 reopening, because it rests on wl-preproc's own half, which did close~~ **Closed 2026-09-30** by the canonical lifecycle design (§2): wl.works' scheduler fires the canonical, and wl-preproc writes the activation row on that request, with `Request.origin` `wl_works` and `requested_by` null — the nullable `requestedBy` under an `origin` discriminator | ~~Phase 1~~ |
 | 13 | Who renders the "checked good" verdict (§8.5), and whether it is entered here or in wl.works. **Nothing in wl.works models it and it was declined rather than folded in**, so if it lives there it needs a row somebody designs | Phase 3 |
 | 14 | Chunk shape and per-dataset compression settings that keep the NWB efficiently range-readable (§8.1.2). Measurable on synthetic files before January | Phase 3 |
 | 15 | Whether the derived-vs-recorded channel map comparison (§11.6) should ever *block* a session or only warn. Blocking makes wl.works' pinout a hard dependency of preprocessing | Phase 2 |
```

```diff
--- a/docs/superpowers/specs/2026-09-29-nwb-publishing-design.md
+++ b/docs/superpowers/specs/2026-09-29-nwb-publishing-design.md
@@ -373,6 +373,12 @@ file is published,** on either share. It is false, with the reason stated, when:
 
 It stays `overridable`: a recorded force still clears it, as today.
 
+*Amended 2026-09-30 by the canonical lifecycle design
+([`2026-09-30-canonical-lifecycle-design.md`](2026-09-30-canonical-lifecycle-design.md) §4; this
+spec's final review, M12):* *only each montage's **current** canonical counts, the one no
+replacement supersedes. A superseded canonical, refused or invalid, no longer blocks a session
+whose replacement is published.*
+
 ## 9. Tables
 
 **`nwb.NwbFile`** gains:
```

```diff
--- a/docs/superpowers/specs/2026-09-30-canonical-lifecycle-design.md
+++ b/docs/superpowers/specs/2026-09-30-canonical-lifecycle-design.md
@@ -164,6 +164,16 @@ replacement, so a stray write anywhere else still fails it.
   canonical that superseded it, or null.
 - The replacement itself appears when it is built, as any file does.
 
+*Amended while planning, 2026-09-30:* *the `superseded` change is recorded by the daemon's
+build stage on its next pass, once per activation, not by the responder when it accepts the
+replacement.*
+- *The responder is not under the NWB lock (`nwb/lock.py`, the publishing review's M4).*
+- *An `NwbChange` written there beside a daemon pass could commit a sequence number out of
+  order, and leave a hole in the cursor.*
+- *The listing's `superseded_by` is read from `Activation.supersedes` whenever the old file is
+  listed, so it is right from the moment the replacement is accepted. Only the cursor's
+  notice waits for the pass.*
+
 **Reclamation checks only the current canonical** (the final review's M12).
 - `archive/reclaim.py::canonical_nwb_present` is true when **each montage's
   current canonical** is `written` and published.
```

- [ ] **Step 2: Full suite on both interpreters, once.**
  - Set `WLPP_BMD_REFERENCE=~/.cache/wl-preproc-references/bmd/BMD`, `WLPP_BMD_BOOST_INCLUDE=~/.cache/wl-preproc-references/bmd/boost_1_86_0` and `WLPP_NSLR_REFERENCE=~/.cache/wl-preproc-references`.
  - Leave `WLPP_OHDPI_REFERENCE` unset, as CI has it.
  - Run `.venv/bin/python -m pytest -q -p no:cacheprovider` and `~/.cache/wl-preproc-venv313/bin/python -m pytest -q -p no:cacheprovider`.

  Expected: both green. Measured with every task applied: **1941 passed, 31 skipped, 1 deselected, 1 xfailed** on 3.11, and **1940 passed, 33 skipped, 1 xfailed** on 3.13, 0 failed. `main` at `668d7eb` gives 1911 and 1910 locally; this plan adds 30 tests.

- [ ] **Step 3: The handoff.** Create `docs/handoffs/2026-09-30-canonical-lifecycle.md` with these sections:
  - **What was built.** Piece 2b, on branch `spec/canonical-lifecycle`. Name the spec and this plan, and cover:
    - the current canonical;
    - replacements and the lock;
    - the protocol's new keys, with the `409` and `422`;
    - `superseded_by`;
    - rebuilding invalid files.
  - **What wl.works must do.** The new OPEN entry.
  - **Still open.**
    - upstream recomputes;
    - a hand-deleted row in `GET /nwb`;
    - wl-xcon's XC-155;
    - wl.works sending subject details.
  - **The rulings.** Every ruling this plan made ("Rulings made while planning"), plus any made while executing.
  - **The measured counts.** Each task's before and after runs, the mutation checks, and Step 2's two full-suite runs.

- [ ] **Step 4: CHECKPOINT.** In "Start here", after the NWB publishing paragraph and the archive-share paragraph, add one bold-led paragraph saying:
  - the canonical lifecycle (piece 2b) is built on `spec/canonical-lifecycle`, NOT merged as written;
  - **wl.works fires every canonical** (the requester's decision), and wl-preproc honours an explicit replacement;
  - `GET /nwb` marks a superseded file;
  - invalid files rebuild themselves once the date of birth arrives;
  - the handoff's path.

  Do not renumber. Do not re-point the header; that happens at merge, with CI read.

- [ ] **Step 5: `wl.yaml`.** Add one sentence to `status.phase`, after the archive-share sentence: the canonical lifecycle (piece 2b) is built on `spec/canonical-lifecycle`, not yet merged, with the spec and handoff paths. Then run `.venv/bin/wl-check` **on its own** and read its exit status.

- [ ] **Step 6: Commit**

```bash
git add docs/pending-wl-works-amendments.md docs/superpowers/specs docs/CHECKPOINT.md docs/handoffs/2026-09-30-canonical-lifecycle.md wl.yaml
git commit -m "docs: the canonical lifecycle built -- what wl.works must do to fire and regenerate the canonical, and the amendments

<trailer lines>"
```

## Rulings made while planning

Each is recorded where the code it governs is, and in the spec where it amends the spec.

1. **The `superseded` change is recorded by the daemon's build stage, not by the responder when it accepts the replacement** (spec §4 says the latter). Only the daemon writes `NwbChange` under the NWB lock, and a second writer outside it could leave a hole in `GET /nwb`'s cursor. Cost if wrong: the change reaches the listing one daemon pass later.
2. **Replacements are serialised per montage by a MySQL named lock taken before the transaction.** DataJoint's transactions start `WITH CONSISTENT SNAPSHOT` (measured), so a check inside one cannot see a rival's commit. Cost if wrong: a replacement waits up to 10 s for another, then fails retryably.
3. **`submit_replacement`'s parameter is `supersedes_activation_id`,** the protocol's own key. A keyword spelled `supersedes=` in the caller matches the guardrail's write pattern; this was caught while proving.
4. **`accept()` checks the lifecycle keys before any database read,** so a malformed request is a `422` whatever the host holds.
5. **Should a hand edit ever leave two unsuperseded canonicals, `current_canonical` takes the latest id** rather than raising under every stage that asks. A replacement only ever supersedes the current canonical, so this cannot arise through the protocol.
6. **A reused key with a different block set is key reuse** (`409`). The stored payload is the whole request, `selection.block_ids` included, so `_reject_key_reuse` already compares it. No new comparison is needed.
7. **Spec §10's "the old file stays published, with its annotations" has no test of its own.** A replacement writes nothing to any file, placement or `NwbFile` row of the activation it supersedes. The only new writes are the replacement's own `Activation` and `ActivationBlock` rows, and the old activation's `superseded` change. Task 4's test shows the old row surviving with `superseded_by` set. Cost if wrong: a future change that touches the old file on acceptance would go unnoticed by this plan's tests.
