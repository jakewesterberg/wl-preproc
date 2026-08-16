# tests/schema/test_request.py
import datetime
import itertools

import pytest

# submit()'s dedupe is keyed on exactly (subject, session_datetime, montage_id) —
# that is the load-bearing, intentional behaviour under test in
# test_two_requests_for_one_selection_yield_one_activation. But it means any two
# tests that request a selection with the SAME montage_id share an Activation:
# whichever test's submit() call runs first creates it, and every later test's
# call silently short-circuits at the "existing" branch in submit() before ever
# reaching Activation.insert1. A module-scoped fixture returning a hardcoded
# montage_id=0 hits exactly that — verified live: it produced
# `assert 'k-1' == 'k-2'` in test_activation_records_which_request_produced_it
# (a later test read back an earlier test's request_key) and "DID NOT RAISE
# RuntimeError" in test_a_failure_between_the_two_inserts_leaves_neither (the
# monkeypatched Activation.insert1 was never reached, so the atomicity test
# wasn't exercising atomicity at all). A fresh montage_id per test gives each
# test its own Activation lineage; the one test that deliberately wants two
# submissions to collide asks for the fixture once and reuses that single value
# for both of its own calls, so it is unaffected by this change.
_montage_ids = itertools.count()


def _raw_connection():
    """A second, genuinely independent connection to the same database --
    literal pymysql, not another dj.Connection -- for tests that need to
    commit a rival row mid-race, for real, on a connection wholly separate
    from the one submit_derivative's own transaction runs on.

    Used by the concurrent-allocation tests below (review round 2, Important
    2). A same-connection trick -- the shape
    tests/schema/test_paramset.py's own test_register_recovers_from_a_
    concurrent_index_collision uses for paramset.register's retry loop --
    cannot exercise request.py's locking-read fix: a single connection
    always sees its own writes, committed or not, regardless of isolation
    level, which is exactly what would hide a plain (non-locking) re-read
    being wrong inside an already-open transaction. Literal pymysql rather
    than threads, per this project's own standing caution: threaded database
    probes have had to be retracted twice here. A second, already-connected,
    sequential connection making its write BEFORE the function under test
    attempts its own reproduces the race deterministically instead.
    """
    import datajoint as dj
    import pymysql

    return pymysql.connect(
        host=dj.config["database.host"],
        port=int(dj.config["database.port"]),
        user=dj.config["database.user"],
        password=dj.config["database.password"],
        autocommit=True,
    )


def _commit_rival_activation(*, request, row: dict, request_key: str, selection_hash: str) -> None:
    """Insert an Activation row directly, via a SEPARATE raw connection,
    committing immediately (autocommit=True) and independently of whatever
    transaction the caller under test currently has open. `row` supplies
    subject/session_datetime/montage_id/activation_id; `request_key` must
    already exist in Request (Activation's foreign key) or this raises."""
    conn = _raw_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {request.Activation.full_table_name}
                (subject, session_datetime, montage_id, activation_id,
                 role, request_key, created_at, selection_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    row["subject"],
                    row["session_datetime"],
                    row["montage_id"],
                    row["activation_id"],
                    "derivative",
                    request_key,
                    datetime.datetime(2027, 6, 1, 11, 0),
                    selection_hash,
                ),
            )
    finally:
        conn.close()


# Review round 3, Important 1's precondition: >=3 concurrent submitters on
# one montage, >=2 losing with distinct selections. This file's single-rival
# tests above cannot reach it -- that rival's raw-connection insert
# (autocommit=True) acquires its lock, writes, and releases before this
# process's own attempt even starts, so there is no window where two
# transactions simultaneously HOLD conflicting lock requests, which a real
# InnoDB deadlock needs. Reaching it needs genuinely overlapping execution:
# separate OS processes, never threads (this project has retracted threaded
# database findings twice). A plain subprocess -- not multiprocessing.Process
# -- so nothing here depends on this test module being importable/picklable
# by a spawned child; each worker is a fully independent interpreter running
# this script, talking back to the parent only over stdout. Rendezvoused at
# a file-based barrier (each worker touches a marker, then waits for every
# worker's marker to exist) so all three enter submit_derivative's retry
# loop as close to simultaneously as the OS allows.
_CONCURRENT_DERIVATIVE_WORKER = r'''
import datetime
import json
import os
import time

import datajoint as dj

dj.config["database.host"] = os.environ["WLPP_HOST"]
dj.config["database.port"] = int(os.environ["WLPP_PORT"])
dj.config["database.user"] = os.environ["WLPP_USER"]
dj.config["database.password"] = os.environ["WLPP_PASSWORD"]
dj.config["safemode"] = False

from wl_preproc.schema import core, pipeline, request

prefix = os.environ["WLPP_PREFIX"]
pipeline.activate(prefix=prefix)
core.activate(prefix=prefix)
request.activate(prefix=prefix)

selection = json.loads(os.environ["WLPP_SELECTION"])
selection["session_datetime"] = datetime.datetime.fromisoformat(selection["session_datetime"])

barrier_dir = os.environ["WLPP_BARRIER_DIR"]
worker_id = os.environ["WLPP_WORKER_ID"]
n_workers = int(os.environ["WLPP_N_WORKERS"])
with open(os.path.join(barrier_dir, "ready-" + worker_id), "w") as f:
    f.write("1")
deadline = time.monotonic() + 20
while time.monotonic() < deadline:
    if len([n for n in os.listdir(barrier_dir) if n.startswith("ready-")]) >= n_workers:
        break
    time.sleep(0.01)

try:
    result = request.submit_derivative(
        idempotency_key=os.environ["WLPP_KEY"], task_type="neural", origin="wl_works",
        selection=selection, block_ids=json.loads(os.environ["WLPP_BLOCK_IDS"]), payload={},
    )
    print("OK " + str(result["activation_id"]))
except Exception as e:  # noqa: BLE001 -- must report ANY exception type back
    # to the parent, including an untyped one; that is exactly the failure
    # mode under test.
    print("ERROR " + type(e).__module__ + "." + type(e).__name__ + ": " + str(e))
'''


@pytest.fixture(scope="module")
def req(dj_conn, prefix):
    from wl_preproc.schema import core, pipeline, request

    pipeline.activate(prefix=prefix)
    core.activate(prefix=prefix)
    request.activate(prefix=prefix)
    return request


@pytest.fixture
def selection(req):
    from wl_preproc.schema import core, pipeline

    pipeline.lab.Lab.insert1(
        # element-lab's Lab is (lab, lab_name, address, time_zone). There is no
        # `institution` attribute — corrected 2026-08-13 after Task 3 hit it.
        {"lab": "wl", "lab_name": "W", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": "pico",
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    # Deliberately NOT test_core.py's (subject="pico", 2027-03-14 09:00): that
    # exact tuple backs test_core.py's `a_session` fixture, and
    # test_rejected_segment_records_why asserts `len(RejectedSegment &
    # a_session) == 1` with no `system` filter — a second file's rows landing
    # under the same (subject, session_datetime) would silently inflate that
    # count. This module writes nothing to RejectedSegment today, so the
    # collision is dormant rather than active, but there is no reason to keep
    # it dormant when a distinct key removes it outright.
    key = {"subject": "pico", "session_datetime": datetime.datetime(2027, 6, 1, 10, 0)}
    pipeline.Session.insert1(key, skip_duplicates=True)
    montage_id = next(_montage_ids)
    core.Montage.insert1({**key, "montage_id": montage_id, "start_s": 0.0, "end_s": 12.0},
                         skip_duplicates=True)
    # ActivationBlock's `-> core.Block` (request.py) is a real foreign key, so
    # submit_derivative's block_ids must name rows that already exist. Three
    # covers every block_ids value Task 4's tests use ([1, 2], [2, 1], [1, 3]).
    # skip_duplicates=True because this fixture runs once per test but
    # (subject, session_datetime) -- unlike montage_id -- is the same tuple
    # every time, so block_id 1-3 only actually get inserted on the first call.
    for block_id, (start_s, end_s) in enumerate(((0.0, 4.0), (4.0, 8.0), (8.0, 12.0)), start=1):
        core.Block.insert1(
            {**key, "block_id": block_id, "task_type": "neural", "start_s": start_s, "end_s": end_s},
            skip_duplicates=True,
        )
    return {**key, "montage_id": montage_id}


def test_activation_is_manual_not_computed(req):
    """A computed table inherits its primary key from its parents, so computing
    Activation from Request would drag idempotency_key into its key and
    contradict section 5.2's (…, montage_id, activation_id)."""
    import datajoint as dj

    assert issubclass(req.Activation, dj.Manual)


def test_activation_key_matches_the_spec_hierarchy(req):
    assert set(req.Activation.primary_key) == {
        "subject",
        "session_datetime",
        "montage_id",
        "activation_id",
    }


def test_request_is_keyed_on_the_idempotency_key(req):
    assert set(req.Request.primary_key) == {"idempotency_key"}


def test_submit_writes_both_rows(req, selection):
    key = req.submit(
        idempotency_key="k-1",
        task_type="neural",
        origin="wl_works",
        selection=selection,
        payload={"raw": "as received"},
        requested_by="jake",
    )
    assert len(req.Request & {"idempotency_key": "k-1"}) == 1
    assert len(req.Activation & key) == 1


def test_activation_records_which_request_produced_it(req, selection):
    key = req.submit("k-2", "neural", "wl_works", selection, {"raw": 2}, "jake")
    assert (req.Activation & key).fetch1("request_key") == "k-2"


def test_two_requests_for_one_selection_yield_one_activation(req, selection):
    """Dedupe is structural. Nothing asks whether a run is in flight; the second
    Activation insert is a duplicate and is skipped."""
    first = req.submit("k-3", "neural", "wl_works", selection, {}, "jake")
    second = req.submit("k-4", "neural", "cli", selection, {}, "jake")
    assert first == second
    assert len(req.Activation & first) == 1
    # both requests are still recorded — the audit trail is not deduped
    assert len(req.Request & 'idempotency_key in ("k-3","k-4")') == 2


def test_a_retry_of_the_same_idempotency_key_is_not_a_second_request(req, selection):
    """The accept branch of submit()'s key-reuse check: same key, same
    (task_type, origin, payload), same selection, so it is a network retry and
    proceeds."""
    payload = {"raw": "as received", "n": 3}
    first = req.submit("k-5", "neural", "wl_works", selection, payload, "jake")
    second = req.submit("k-5", "neural", "wl_works", selection, payload, "jake")
    # the property a network retry actually depends on: the same call, made
    # twice, must resolve to the same activation, not merely leave Request
    # alone
    assert first == second
    assert len(req.Request & {"idempotency_key": "k-5"}) == 1
    # and the recorded ask is untouched by the retry
    assert (req.Request & {"idempotency_key": "k-5"}).fetch1("payload") == payload


def test_reusing_an_idempotency_key_for_a_different_request_is_refused(req, selection):
    """The refuse branch. `Request.insert1(..., skip_duplicates=True)` made the
    second call a silent no-op *on Request only* — execution continued, so the
    second ask went unrecorded (contradicting section 4.2's "records what was
    asked") while its Activation pointed at a `request_key` whose stored
    payload belongs to the first ask. Both future consumers take keys from
    outside this process, which is exactly where reuse happens."""
    import datajoint as dj

    req.submit("k-11", "neural", "wl_works", selection, {"raw": "first"}, "jake")

    with pytest.raises(dj.DataJointError, match="key reuse"):
        req.submit("k-11", "neural", "wl_works", selection, {"raw": "SECOND"}, "jake")

    # the recorded ask is the first one, unmodified, and there is still only one
    assert len(req.Request & {"idempotency_key": "k-11"}) == 1
    assert (req.Request & {"idempotency_key": "k-11"}).fetch1("payload") == {"raw": "first"}

    # a differing task_type or origin is the same conflict, on the same key
    with pytest.raises(dj.DataJointError, match="task_type"):
        req.submit("k-11", "behavior", "wl_works", selection, {"raw": "first"}, "jake")
    with pytest.raises(dj.DataJointError, match="origin"):
        req.submit("k-11", "neural", "cli", selection, {"raw": "first"}, "jake")


def test_a_numpy_carrying_retry_survives_a_different_key_order(req, selection):
    """`_payload_differs`'s numpy fallback, which no test reached until
    2026-08-14.

    A payload carrying an array cannot be compared with `!=` — that returns an
    array and `bool()` of it raises — so the comparison falls through to
    `datajoint.blob.pack`, which is byte-exact and therefore *key-order
    sensitive*. A genuine retry whose dict was built in a different order (a
    JSON round trip, a different client library, a dict rebuilt from kwargs)
    packed to different bytes and was refused as key reuse. That is the exact
    inverse of the defect `_reject_key_reuse` exists to fix, and the opposite
    of what `_payload_differs`'s own docstring promises two lines above the
    fallback — which is why nothing caught it: the documented behaviour is
    real, just not on this path.

    The nested dict is load-bearing. `pack` is order-sensitive at every level,
    so a top-level-only sort would leave this test failing with the bug intact
    one dict down; the fix canonicalises recursively, as
    `paramset.content_hash`'s `sort_keys=True` does.
    """
    import datajoint as dj
    import numpy as np

    first = {"zeta": 1, "arr": np.arange(8, dtype=np.float32), "nested": {"b": 2, "a": 1}}
    # the same request, with every dict built in a different order
    again = {
        "nested": {"a": 1, "b": 2},
        "arr": np.arange(8, dtype=np.float32),
        "zeta": 1,
    }

    key = req.submit("k-14", "neural", "wl_works", selection, first, "jake")
    assert req.submit("k-14", "neural", "wl_works", selection, again, "jake") == key
    assert len(req.Request & {"idempotency_key": "k-14"}) == 1

    # The fallback must still discriminate, or this test would pass just as
    # well against a `_payload_differs` that never reports a difference at all.
    different = {**again, "arr": np.arange(8, dtype=np.float32) + 1}
    with pytest.raises(dj.DataJointError, match="payload"):
        req.submit("k-14", "neural", "wl_works", selection, different, "jake")


def test_reusing_an_idempotency_key_from_a_different_requester_is_refused(req, selection):
    """An idempotency key is caller-scoped: the same key from a different
    person is a collision between two key spaces, not one person's retry.

    `requested_by` went uncompared until 2026-08-14, so the second person's ask
    — identical in every other field, which is exactly the case the retry path
    is meant to absorb — was absorbed too, the recorded requester stayed the
    first person, and the second ask went unrecorded. That is section 4.2's
    "Request records what was asked" failing through the one field that says
    who asked.
    """
    import datajoint as dj

    payload = {"raw": "same ask, different person"}
    key = req.submit("k-15", "neural", "wl_works", selection, payload, "jake")

    # accept: the same requester resubmitting is still a retry
    assert req.submit("k-15", "neural", "wl_works", selection, payload, "jake") == key

    # refuse: a different requester is not
    with pytest.raises(dj.DataJointError, match="requested_by"):
        req.submit("k-15", "neural", "wl_works", selection, payload, "alice")

    # the first ask is recorded, unmodified, and is still the only one
    assert len(req.Request & {"idempotency_key": "k-15"}) == 1
    assert (req.Request & {"idempotency_key": "k-15"}).fetch1("requested_by") == "jake"


def test_reusing_an_idempotency_key_for_a_different_selection_is_refused(req, selection):
    """The selection is part of "the same ask", and it is not stored on Request,
    so it is checked against the activations the key actually produced."""
    import datajoint as dj

    from wl_preproc.schema import core

    payload = {"raw": "same payload, different session"}
    req.submit("k-12", "neural", "wl_works", selection, payload, "jake")

    other = {
        "subject": selection["subject"],
        "session_datetime": selection["session_datetime"],
        "montage_id": next(_montage_ids),
    }
    core.Montage.insert1({**other, "start_s": 0.0, "end_s": 12.0}, skip_duplicates=True)

    with pytest.raises(dj.DataJointError, match="selection"):
        req.submit("k-12", "neural", "wl_works", other, payload, "jake")

    # nothing was written for the rejected ask: the whole submission rolls back
    assert len(req.Activation & {"request_key": "k-12"}) == 1
    assert len(req.Activation & other) == 0


def test_activation_cannot_name_a_request_that_does_not_exist(req, selection):
    """Section 4.3's provenance claim, as structure rather than convention.
    `request_key` was a bare varchar(64) until 2026-08-14, so this insert
    succeeded — verified — and an Activation could name a Request that was
    never made.

    `match=` pins the InnoDB constraint specifically. A bare
    `pytest.raises(dj.DataJointError)` is satisfied by any insert failure at
    all — a missing column, a bad enum value, a typo in the key — so it would
    still pass on a build where the foreign key had been dropped and something
    unrelated broke instead."""
    import datetime

    import datajoint as dj

    with pytest.raises(dj.DataJointError, match="foreign key constraint fails"):
        req.Activation.insert1(
            {
                **selection,
                "activation_id": 7,
                "role": "canonical",
                "request_key": "no-such-request",
                "created_at": datetime.datetime(2027, 6, 1, 10, 0),
            }
        )


def test_request_key_is_a_foreign_key_and_stays_out_of_the_primary_key(req):
    """The projected form, `-> Request.proj(request_key='idempotency_key')`,
    below the divider: integrity without dragging `idempotency_key` into
    Activation's key, which is what section 4.3 asks for and what making
    Activation `Computed` would have broken."""
    assert "request_key" in req.Activation.heading.names
    assert "request_key" not in req.Activation.primary_key
    assert set(req.Activation.primary_key) == {
        "subject",
        "session_datetime",
        "montage_id",
        "activation_id",
    }
    assert req.Request.full_table_name in req.Activation.parents()


def test_a_failure_between_the_two_inserts_leaves_neither(req, selection, monkeypatch):
    """A Request written without its Activation is an accepted request that will
    never run — which wl.works experiences as a silent hang, the worst available
    failure across that boundary."""
    invoked = {"boom": False}

    def boom(*args, **kwargs):
        invoked["boom"] = True
        raise RuntimeError("simulated failure after the Request insert")

    monkeypatch.setattr(req.Activation, "insert1", boom)
    # match= pins the exact failure: without it, a RuntimeError raised by
    # anything else on this path (e.g. the Request insert itself) would also
    # satisfy pytest.raises and the assertion below would never actually
    # prove Activation.insert1 was reached.
    with pytest.raises(RuntimeError, match="simulated failure after the Request insert"):
        req.submit("k-6", "neural", "wl_works", selection, {}, "jake")
    assert invoked["boom"] is True, (
        "Activation.insert1 was never reached — the dedupe branch was taken "
        "instead, so this test would not be exercising the rollback at all"
    )
    assert len(req.Request & {"idempotency_key": "k-6"}) == 0


def test_automatic_origin_needs_no_requester(req, selection):
    """Section 8.3.1: the canonical trigger enters through the same door, with
    origin='auto' and no human requester. Item 12 is narrowed, not closed."""
    key = req.submit("k-7", "neural", "auto", selection, {}, requested_by=None)
    assert (req.Request & {"idempotency_key": "k-7"}).fetch1("origin") == "auto"
    assert len(req.Activation & key) == 1


def test_payload_survives_as_a_structure_not_a_string(req, selection):
    """The raw payload is evidence. A request that turns out to be malformed
    cannot be reconstructed from the rows it produced."""
    req.submit("k-8", "neural", "wl_works", selection, {"nested": {"a": [1, 2, 3]}}, "jake")
    got = (req.Request & {"idempotency_key": "k-8"}).fetch1("payload")
    assert got["nested"]["a"] == [1, 2, 3]


def test_submit_always_produces_a_canonical_activation_at_id_zero(req, selection):
    """submit() has no way to form a derivative: the dedupe above returns on
    ANY existing Activation for the selection, so the branch that would
    allocate a second activation_id is unreachable by construction, and a
    derivative needs a block set this function's selection does not carry.
    Pinned here so that the day someone widens the dedupe key (or the
    selection) to admit derivatives, this test fails loudly instead of the
    canonical-only assumption silently rotting."""
    key = req.submit("k-9", "neural", "wl_works", selection, {}, "jake")
    assert key["activation_id"] == 0
    assert (req.Activation & key).fetch1("role") == "canonical"


def test_submit_before_activate_raises_a_clear_error(req, selection, monkeypatch):
    """Both future entry points — the ingest watcher and the responder — call
    submit() as their first contact with this module. Without this guard,
    calling it before activate() fails deep inside DataJoint's lazy table
    declaration with "Cannot declare new tables inside a transaction": true,
    but useless to whoever is debugging a misordered startup."""
    import datajoint as dj

    from wl_preproc.schema import request

    monkeypatch.setattr(request.schema, "is_activated", lambda: False)
    with pytest.raises(dj.DataJointError, match="activate"):
        req.submit("k-10", "neural", "wl_works", selection, {}, "jake")


def test_selection_hash_is_order_independent():
    """The block set is a set. Two requests naming the same blocks in a
    different order are the same selection, and if they hash differently the
    dedupe in section 11.3 silently starts a second run."""
    from wl_preproc.schema.request import selection_hash

    assert selection_hash("neural", [3, 1, 2]) == selection_hash("neural", [1, 2, 3])


def test_selection_hash_separates_task_types():
    from wl_preproc.schema.request import selection_hash

    assert selection_hash("neural", [1, 2]) != selection_hash("export", [1, 2])


def test_selection_hash_deduplicates_repeated_block_ids():
    """The block set is a *set*: naming the same block twice is one block, not
    two. A caller that accumulates block ids across, say, paginated results
    and does not itself de-duplicate must still land on the same selection as
    one that does -- otherwise the same logical selection would silently carry
    two different identities depending on how the caller happened to build its
    list, and section 11.3's in-flight lookup would miss the match.
    `sorted()` alone would not catch this: `[1, 1, 2]` is already sorted and
    stays three elements long, so de-duplication has to be a deliberate
    `set(...)`, not a side effect of sorting."""
    from wl_preproc.schema.request import selection_hash

    assert selection_hash("neural", [1, 1, 2]) == selection_hash("neural", [1, 2])


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


def test_submit_refuses_to_run_inside_a_transaction(req, selection):
    """submit()'s hardest constraint, and the one that most directly binds both
    future consumers: it opens its own transaction, DataJoint transactions do
    not nest, so it can never be called from inside one. Guarded for the same
    reason the is_activated() guard above is — otherwise the failure surfaces
    from inside DataJoint as a connection-level complaint that names nothing
    the caller did wrong. It was stated only in a plan, which is not a place
    1c-2 or 1c-3 will read it from."""
    import datajoint as dj

    conn = dj.conn()
    with conn.transaction:
        with pytest.raises(dj.DataJointError, match="do not nest"):
            req.submit("k-13", "neural", "wl_works", selection, {}, "jake")

    # the outer transaction stayed usable and nothing was written
    assert len(req.Request & {"idempotency_key": "k-13"}) == 0


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
    not a lock — the same shape canonical dedupe already uses.

    The brief's own final assertion reads `len(request.Activation &
    {"role": "derivative"}) == 1` -- a bare, table-wide role filter. That
    only holds if this is the only test in the module that has ever created a
    derivative row, which is not true here: test_a_derivative_gets_its_own_
    activation_id, immediately above, creates one of its own, and by
    definition-order execution (pytest's default, confirmed live) it runs
    first. Scoped to `first` instead -- the same shape
    test_two_requests_for_one_selection_yield_one_activation already uses for
    submit()'s own dedupe two-request test -- so this test asserts what it
    actually means (no second Activation for THIS selection) rather than a
    global count this file's own header comment already explains cannot be
    test-order-independent (see `_montage_ids`)."""
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
    assert len(request.Activation & first) == 1


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


def test_a_derivative_before_any_canonical_does_not_claim_activation_id_zero(selection, prefix):
    """0 is reserved for canonical even when none has been submitted yet for
    this montage. If a derivative claimed 0 first, a later submit() for the
    same montage would silently no-op onto the derivative's row --
    Activation.insert1(..., skip_duplicates=True) at activation_id=0 treats
    an existing row at that key as "already done" -- and hand its caller a
    key that names somebody else's derivative under the role it asked for,
    instead of ever inserting a canonical row at all."""
    from wl_preproc.schema import request

    derivative = request.submit_derivative(
        idempotency_key="dv-7", task_type="neural", origin="wl_works",
        selection=selection, block_ids=[1], payload={},
    )
    assert derivative["activation_id"] != 0

    canonical = request.submit(
        idempotency_key="dv-8", task_type="neural", origin="cli",
        selection=selection, payload={},
    )
    assert canonical["activation_id"] == 0
    assert (request.Activation & canonical).fetch1("role") == "canonical"


def test_a_retry_of_a_derivative_idempotency_key_returns_the_running_activation(selection, prefix):
    """The accept branch of the reuse check, now widened to also compare the
    block set (see test_reusing_a_derivative_idempotency_key_for_a_different_
    block_set_is_refused below): the identical key, resubmitted with the
    identical block set, is still a retry and returns the same activation
    rather than being refused."""
    from wl_preproc.schema import request

    first = request.submit_derivative(
        idempotency_key="dv-10", task_type="neural", origin="wl_works",
        selection=selection, block_ids=[1, 2], payload={},
    )
    second = request.submit_derivative(
        idempotency_key="dv-10", task_type="neural", origin="wl_works",
        selection=selection, block_ids=[1, 2], payload={},
    )
    assert first == second
    assert len(request.Request & {"idempotency_key": "dv-10"}) == 1


def test_reusing_a_derivative_idempotency_key_for_a_different_block_set_is_refused(selection, prefix):
    """The block set is part of "the same ask" for a derivative exactly as
    the selection is for submit() (see
    test_reusing_an_idempotency_key_for_a_different_selection_is_refused
    above): reusing a key with different block_ids is a collision, not a
    retry, and the running activation must not be handed back to a caller who
    asked for different blocks.

    This covers the case where the key's OWN first submission is what
    produced the derivative Activation below (`first`, naming request_key=
    "dv-9" directly) -- see _reject_key_reuse's docstring, "What this also
    checks -- and does not -- for a derivative". It does NOT cover the case
    where the first submission under a key instead deduped onto a
    pre-existing derivative created by some OTHER key: that shape stays
    open, the same pre-existing residual _reject_key_reuse's "What cannot be
    checked here" paragraph already describes for canonical, now also true
    for derivative -- not closed by this test or by Activation.selection_hash
    alone."""
    import datajoint as dj

    from wl_preproc.schema import request

    first = request.submit_derivative(
        idempotency_key="dv-9", task_type="neural", origin="wl_works",
        selection=selection, block_ids=[1, 2], payload={},
    )

    with pytest.raises(dj.DataJointError, match="selection"):
        request.submit_derivative(
            idempotency_key="dv-9", task_type="neural", origin="wl_works",
            selection=selection, block_ids=[1, 3], payload={},
        )

    # the first ask's activation is unaffected, unmodified, and still the
    # only derivative on this montage
    assert len(request.Activation & {"role": "derivative"} & selection) == 1
    assert (request.Activation & first).fetch1("selection_hash") == (
        request.selection_hash("neural", [1, 2])
    )


def test_reusing_a_submit_key_for_a_derivative_is_refused(selection, prefix):
    """One direction of the cross-entry-point asymmetry (review round 2,
    Important 3): submit() then submit_derivative() under the same key. This
    direction already worked before this round -- submit_derivative's
    selection_key names selection_hash, always null on a canonical row, so it
    could never match -- pinned here so the resolved behaviour is symmetric
    and explicit on both sides rather than true by accident on one."""
    import datajoint as dj

    from wl_preproc.schema import request

    canonical = request.submit(
        idempotency_key="asym-1", task_type="neural", origin="cli",
        selection=selection, payload={},
    )

    with pytest.raises(dj.DataJointError, match="selection"):
        request.submit_derivative(
            idempotency_key="asym-1", task_type="neural", origin="cli",
            selection=selection, block_ids=[1, 2], payload={},
        )

    # the canonical is unaffected, and no derivative was created under this key
    assert len(request.Activation & canonical) == 1
    assert len(request.Activation & {"request_key": "asym-1"}) == 1


def test_reusing_a_derivative_key_for_submit_is_refused(selection, prefix):
    """The other direction (review round 2, Important 3): submit_derivative()
    then submit() under the same key. Before the role="canonical" fix to
    submit()'s canonical_selection_key, this direction was silently
    ACCEPTED: submit()'s reuse check named only the three montage attributes,
    which the derivative row (same montage) satisfied trivially, so
    _reject_key_reuse found no conflict and submit() went on to insert a
    second, canonical Activation under a key that had already produced a
    derivative -- two activations under one idempotency key, contradicting
    the same "this ask would go unrecorded" reasoning the refusal message
    itself gives. Resolved in the direction that already held for
    submit()-then-submit_derivative (test_reusing_a_submit_key_for_a_
    derivative_is_refused above): a canonical ask and a derivative ask are
    different asks even when they share a montage, so reusing a key across
    them is refused, symmetrically, in both directions."""
    import datajoint as dj

    from wl_preproc.schema import request

    derivative = request.submit_derivative(
        idempotency_key="asym-2", task_type="neural", origin="wl_works",
        selection=selection, block_ids=[1, 2], payload={},
    )

    with pytest.raises(dj.DataJointError, match="selection"):
        request.submit(
            idempotency_key="asym-2", task_type="neural", origin="wl_works",
            selection=selection, payload={},
        )

    # the derivative is unaffected, and no canonical was created under this
    # key -- in particular, no row at activation_id=0 for this montage
    assert len(request.Activation & derivative) == 1
    assert len(request.Activation & {"request_key": "asym-2"}) == 1
    assert len(request.Activation & {**selection, "role": "canonical"}) == 0


def test_a_concurrent_identical_selection_returns_the_rival_not_a_collision(selection, prefix, monkeypatch):
    """Review round 2, Important 2, case (a): two callers submit the SAME
    selection concurrently, under different idempotency keys. Both pass
    their own pre-check (neither sees the other's not-yet-committed row) and
    both attempt to claim the id this test's own allocation read computes.
    Reproduced with a genuinely separate connection (see _raw_connection's
    docstring for why this, not threads and not the same-connection trick
    test_paramset.py uses for paramset.register's own retry test).

    Section 11.3: a request whose selection is already in flight returns the
    running one instead of starting a second -- true here even though "in
    flight" means "committed by a rival between this transaction's read and
    its own insert attempt," not merely "already fully committed before this
    transaction even started" (the ordinary, sequential case every other
    dedupe test in this file already covers)."""
    from wl_preproc.schema import request

    request.Request.insert1(
        {
            "idempotency_key": "race-a-rival",
            "task_type": "neural",
            "origin": "wl_works",
            "payload": {},
            "requested_by": None,
            "requested_at": datetime.datetime(2027, 6, 1, 10, 0),
        },
        skip_duplicates=True,
    )
    digest = request.selection_hash("neural", [1, 2])  # the SAME selection

    real_insert = request._insert_new_derivative
    calls = {"n": 0}

    def racing_insert(row):
        calls["n"] += 1
        if calls["n"] == 1:
            # A SEPARATE, real connection commits the rival -- the identical
            # selection -- at the exact id this call is about to attempt,
            # for real, before this call's own insert is attempted.
            _commit_rival_activation(
                request=request, row=row, request_key="race-a-rival", selection_hash=digest
            )
        real_insert(row)

    monkeypatch.setattr(request, "_insert_new_derivative", racing_insert)

    result = request.submit_derivative(
        idempotency_key="race-a-mine", task_type="neural", origin="wl_works",
        selection=selection, block_ids=[1, 2], payload={},
    )

    assert calls["n"] == 1, "must return the rival's key on the FIRST collision, not retry past it"
    assert result["activation_id"] == 1  # the rival's row -- this montage's first activation
    assert (request.Activation & result).fetch1("request_key") == "race-a-rival"
    # only the rival's row exists -- my own submission did not create a second one
    assert len(request.Activation & {"role": "derivative"} & selection) == 1
    # my own Request row is still recorded even though my Activation attempt
    # lost the race -- the audit trail is not deduped, same as the
    # sequential canonical case (test_two_requests_for_one_selection_yield_
    # one_activation)
    assert len(request.Request & {"idempotency_key": "race-a-mine"}) == 1


def test_a_concurrent_different_selection_retries_to_a_fresh_id(selection, prefix, monkeypatch):
    """Review round 2, Important 2, case (b) -- the likelier and more
    consequential case: two researchers picking DIFFERENT block sets on one
    montage, racing for the same allocated id. Before this fix, this
    collision raised outright: the loser's legitimate, distinct submission
    was destroyed and its Activation never created at all. That is precisely
    what paramset.register's own retry loop exists to prevent for
    paramset_idx, and section 11.3 protects a losing-but-distinct submission
    exactly as much as an identical one -- "concurrent runs are legitimate
    and expected."""
    from wl_preproc.schema import request

    request.Request.insert1(
        {
            "idempotency_key": "race-b-rival",
            "task_type": "neural",
            "origin": "wl_works",
            "payload": {},
            "requested_by": None,
            "requested_at": datetime.datetime(2027, 6, 1, 10, 0),
        },
        skip_duplicates=True,
    )
    rival_digest = request.selection_hash("neural", [3])  # a DIFFERENT selection

    real_insert = request._insert_new_derivative
    calls = {"n": 0}

    def racing_insert(row):
        calls["n"] += 1
        if calls["n"] == 1:
            _commit_rival_activation(
                request=request, row=row, request_key="race-b-rival", selection_hash=rival_digest
            )
        real_insert(row)

    monkeypatch.setattr(request, "_insert_new_derivative", racing_insert)

    result = request.submit_derivative(
        idempotency_key="race-b-mine", task_type="neural", origin="wl_works",
        selection=selection, block_ids=[1, 2], payload={},
    )

    assert calls["n"] == 2, "must retry exactly once past the simulated collision"
    assert result["activation_id"] == 2, "must land on a fresh id, not the rival's"
    rival_row = (request.Activation & {**selection, "activation_id": 1}).fetch1()
    assert rival_row["request_key"] == "race-b-rival", "the rival's row must survive untouched"
    mine_row = (request.Activation & result).fetch1()
    assert mine_row["request_key"] == "race-b-mine"
    assert mine_row["selection_hash"] == request.selection_hash("neural", [1, 2])
    # BOTH activations exist -- the loser's legitimate, distinct submission
    # was not destroyed by the rival's collision
    assert len(request.Activation & {"role": "derivative"} & selection) == 2
    # and its own ActivationBlock rows were written under the fresh id
    assert len(request.ActivationBlock & result) == 2


def test_derivative_allocation_gives_up_after_sustained_contention(selection, prefix, monkeypatch):
    """The bound on the retry loop: if every attempt collides against a
    DIFFERENT selection (sustained, not resolved), submit_derivative must
    fail loudly rather than spin forever -- the same shape
    paramset.ContentionExhausted exists for, though raised here as a bare
    DataJointError (see submit_derivative's docstring, "2. Index allocation
    retries...", for why a dedicated exception type was not borrowed from
    paramset for this)."""
    import datajoint as dj

    from wl_preproc.schema import request

    def always_collide(row):
        raise dj.errors.DuplicateError("simulated sustained contention")

    monkeypatch.setattr(request, "_insert_new_derivative", always_collide)

    with pytest.raises(dj.DataJointError, match="exhausted"):
        request.submit_derivative(
            idempotency_key="race-c-mine", task_type="neural", origin="wl_works",
            selection=selection, block_ids=[1, 2], payload={},
        )

    # nothing was written for the exhausted submission -- rolled back whole
    assert len(request.Request & {"idempotency_key": "race-c-mine"}) == 0
    assert len(request.Activation & {"role": "derivative"} & selection) == 0


def test_locking_read_one_takes_a_record_only_lock_no_gap(selection, prefix):
    """Review round 4, Important 1: the deterministic guard for the deadlock
    round 3 fixed, replacing reliance on the probabilistic 3-process test
    below for detection. That test's own measured detection rate against
    the true pre-fix (range-shaped) code -- see its docstring -- is far
    below "reliable"; this test needs no concurrency and no timing at all,
    and asserts the exact InnoDB lock shape that fixes the deadlock
    directly, via performance_schema.data_locks, inside one open
    transaction.

    Against the range-shaped first draft of this fix (`Activation &
    montage_key`, a primary-key PREFIX), performance_schema.data_locks
    showed lock modes including a plain "S" (not "S,REC_NOT_GAP") and a row
    locked at the 'supremum pseudo-record' -- the gap lock that lets two
    losers deadlock on each other's insert-intention lock; see
    _locking_read_one's own docstring for the full account. Against the
    shipped exact-key read, every RECORD lock on `activation` is
    'S,REC_NOT_GAP' and no supremum row ever appears -- asserted below, and
    reproduced failing against the range shape in this round's own
    verification (see the report)."""
    import datajoint as dj

    from wl_preproc.schema import request

    seed = request.submit_derivative(
        idempotency_key="lock-shape-seed", task_type="neural", origin="wl_works",
        selection=selection, block_ids=[1], payload={},
    )

    conn = dj.conn()
    conn.start_transaction()
    try:
        rival = request._locking_read_one(seed)
        assert rival is not None, "the seed row must exist to be locked"

        cursor = dj.conn().query(
            """
            SELECT dl.LOCK_TYPE, dl.LOCK_MODE, dl.LOCK_DATA
            FROM performance_schema.data_locks dl
            JOIN performance_schema.threads t ON dl.THREAD_ID = t.THREAD_ID
            WHERE t.PROCESSLIST_ID = CONNECTION_ID()
              AND dl.OBJECT_SCHEMA = %s
              AND dl.OBJECT_NAME = 'activation'
            """,
            (f"{prefix}request",),
            as_dict=True,
        )
        rows = list(cursor.fetchall())
    finally:
        conn.commit_transaction()

    record_locks = [r for r in rows if r["LOCK_TYPE"] == "RECORD"]
    assert record_locks, f"expected at least one RECORD lock from _locking_read_one; got {rows}"
    for r in record_locks:
        assert r["LOCK_MODE"] == "S,REC_NOT_GAP", (
            f"expected a record-only shared lock (no gap); got LOCK_MODE={r['LOCK_MODE']!r} "
            f"on LOCK_DATA={r['LOCK_DATA']!r} -- a gap lock here is exactly what deadlocks "
            "two losers on each other's insert-intention lock"
        )
        assert r["LOCK_DATA"] != "supremum pseudo-record", (
            "a lock on the supremum pseudo-record means a gap (next-key) lock was taken, "
            "not a record-only one"
        )


def test_three_concurrent_derivative_submissions_do_not_deadlock(selection, prefix, tmp_path):
    """An end-to-end smoke test of the real concurrent path under the
    >=3-submitter, >=2-distinct-selection shape a range-shaped locking read
    deadlocks on -- NOT the reliable regression guard for that bug. That is
    `test_locking_read_one_takes_a_record_only_lock_no_gap`, above: fully
    deterministic, no concurrency, asserting the exact InnoDB lock shape
    directly via `performance_schema.data_locks`.

    This test's own `ids == [1, 2, 3]` assertion is close to deterministic
    -- each of the three submissions has run in this process's own
    verification with no observed failure of THAT specific assertion -- but
    what it is not is a reliable catcher of the range-shaped regression
    itself: measured directly (review round 4) by looping this exact
    scenario against the TRUE pre-fix code, 8 deadlocks in 40 runs, plus 10
    standalone runs of this test with 10/10 passing anyway (the >=2-loser
    precondition this needs was reached only ~42% of the time even with the
    barrier, and even when reached the race did not always resolve into a
    deadlock) -- roughly 16% combined detection, meaning a CI run would go
    green on a reintroduced range lock roughly 5 times in 6. An earlier
    version of this docstring claimed the opposite ("reliably deadlocked"),
    which the same measurement disproved; kept here, corrected, as a
    record of a claim this project's own review caught contradicting a
    measurement in the same task's report.

    See the module-level comment above _CONCURRENT_DERIVATIVE_WORKER for
    why this needs separate processes (never threads) and a file-based
    barrier rather than multiprocessing or the single-rival tests above.
    Against the exact-key fix, all three submissions succeed with distinct
    activation_ids and nothing deadlocks -- which is what this test still
    verifies, honestly described as a smoke test rather than a gate."""
    import datajoint as dj

    import json
    import os
    import pathlib
    import subprocess
    import sys

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    barrier_dir = tmp_path / "barrier"
    barrier_dir.mkdir()

    n_workers = 3
    env_base = {
        **os.environ,
        "WLPP_HOST": str(dj.config["database.host"]),
        "WLPP_PORT": str(dj.config["database.port"]),
        "WLPP_USER": str(dj.config["database.user"]),
        "WLPP_PASSWORD": str(dj.config["database.password"]),
        "WLPP_PREFIX": prefix,
        "WLPP_BARRIER_DIR": str(barrier_dir),
        "WLPP_N_WORKERS": str(n_workers),
        "WLPP_SELECTION": json.dumps(
            {**selection, "session_datetime": selection["session_datetime"].isoformat()}
        ),
    }

    processes = []
    for i in range(n_workers):
        env = {
            **env_base,
            "WLPP_WORKER_ID": str(i),
            "WLPP_KEY": f"deadlock-{i}",
            "WLPP_BLOCK_IDS": json.dumps([i + 1]),  # three genuinely distinct selections
        }
        processes.append(
            subprocess.Popen(
                [sys.executable, "-c", _CONCURRENT_DERIVATIVE_WORKER],
                env=env,
                cwd=str(repo_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )

    outputs = []
    for p in processes:
        try:
            stdout, stderr = p.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()
            stdout, stderr = p.communicate()
            stdout = (stdout or "") + "\nERROR [KILLED AFTER 60s TIMEOUT]"
        # The worker's own OK/ERROR line is the last such line in stdout --
        # a bare subprocess (nothing here runs under pytest) also carries
        # DataJoint's own connection log and element_lab's deprecation
        # notice on stdout, ahead of it.
        result_line = next(
            (
                line
                for line in reversed((stdout or "").splitlines())
                if line.startswith("OK ") or line.startswith("ERROR ")
            ),
            None,
        )
        outputs.append((p.returncode, result_line, stdout, stderr))

    failures = [
        (rc, line, out, err)
        for rc, line, out, err in outputs
        if rc != 0 or line is None or not line.startswith("OK ")
    ]
    assert not failures, f"a concurrent derivative submission failed: {failures}"

    ids = sorted(int(line.split()[1]) for _, line, _, _ in outputs)
    assert ids == [1, 2, 3], (
        f"all three distinct selections must each get their own activation_id: {ids}"
    )


def test_activation_block_gets_one_row_per_distinct_block_id(selection, prefix):
    """Review round 2, Important 4: the third of submit_derivative's three
    documented differences -- writing ActivationBlock -- had zero test
    coverage. Deleting the entire ActivationBlock.insert(...) call left the
    suite at 29 passed; the only hit for ActivationBlock anywhere under
    tests/ was a comment. Duplicated and unsorted on purpose, so this also
    covers "duplicates collapse" and "the count matches", not just presence:
    the written rows must mirror what selection_hash itself canonicalises
    block_ids to (sorted(set(...))), or the two could silently disagree
    about what "this selection" even contains."""
    from wl_preproc.schema import request

    key = request.submit_derivative(
        idempotency_key="ab-1", task_type="neural", origin="wl_works",
        selection=selection, block_ids=[2, 1, 2, 1], payload={},
    )

    assert len(request.ActivationBlock & key) == 2, (
        "duplicates must collapse to one row per distinct block id"
    )
    assert set((request.ActivationBlock & key).to_arrays("block_id")) == {1, 2}


def test_an_empty_block_set_is_refused(selection, prefix):
    """Review round 2, Minor: block_ids=[] would silently create a
    role='derivative' Activation covering nothing -- selection_hash("neural",
    []) is a perfectly stable digest (it hashes an empty list like any
    other), so nothing else in this module would have caught it. Rejected
    before the transaction opens, so nothing is written at all."""
    import datajoint as dj

    from wl_preproc.schema import request

    with pytest.raises(dj.DataJointError, match="block"):
        request.submit_derivative(
            idempotency_key="empty-1", task_type="neural", origin="wl_works",
            selection=selection, block_ids=[], payload={},
        )

    assert len(request.Request & {"idempotency_key": "empty-1"}) == 0
    assert len(request.Activation & {"role": "derivative"} & selection) == 0


def test_submit_derivative_before_activate_raises_a_clear_error(req, selection, monkeypatch):
    """Review round 2, Minor: submit_derivative's own guards were untested --
    deleting either left the suite passing, unlike submit()'s equivalent
    guards, which each already have a test. Mirrors test_submit_before_
    activate_raises_a_clear_error."""
    import datajoint as dj

    from wl_preproc.schema import request

    monkeypatch.setattr(request.schema, "is_activated", lambda: False)
    with pytest.raises(dj.DataJointError, match="activate"):
        req.submit_derivative(
            idempotency_key="dv-guard-1", task_type="neural", origin="wl_works",
            selection=selection, block_ids=[1, 2], payload={},
        )


def test_submit_derivative_refuses_to_run_inside_a_transaction(req, selection):
    """The half of the guard pair the review flagged especially:
    submit_derivative opens its own transaction, and DataJoint transactions
    do not nest, so it must refuse to run inside one -- mirrors submit()'s
    own test_submit_refuses_to_run_inside_a_transaction."""
    import datajoint as dj

    conn = dj.conn()
    with conn.transaction:
        with pytest.raises(dj.DataJointError, match="do not nest"):
            req.submit_derivative(
                idempotency_key="dv-guard-2", task_type="neural", origin="wl_works",
                selection=selection, block_ids=[1, 2], payload={},
            )

    # the outer transaction stayed usable and nothing was written
    assert len(req.Request & {"idempotency_key": "dv-guard-2"}) == 0


def test_locking_read_one_rejects_an_incomplete_key(req):
    """Review round 4, Minor: the ValueError guard inside _locking_read_one
    had zero coverage -- deleting it left the suite passing (403/403), so it
    was the docstring's claim of narrowing ("Activation-only, exact-primary-
    key-only, one row only"), not anything enforcing it, that a reader had
    to take on trust rather than see verified. Pins the guard directly: a
    key missing activation_id must be refused before ever reaching a query,
    with no live montage or collision needed to exercise it."""
    from wl_preproc.schema import request

    with pytest.raises(ValueError, match="primary_key"):
        request._locking_read_one(
            {
                "subject": "pico",
                "session_datetime": datetime.datetime(2027, 6, 1, 10, 0),
                "montage_id": 0,
                # activation_id deliberately omitted
            }
        )
