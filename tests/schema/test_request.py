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
