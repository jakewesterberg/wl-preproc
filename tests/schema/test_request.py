# tests/schema/test_request.py
import datetime
import itertools

import pytest

PREFIX = "t_"

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
def req(dj_conn):
    from wl_preproc.schema import core, pipeline, request

    pipeline.activate(prefix=PREFIX)
    core.activate(prefix=PREFIX)
    request.activate(prefix=PREFIX)
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
    first = req.submit("k-5", "neural", "wl_works", selection, {}, "jake")
    second = req.submit("k-5", "neural", "wl_works", selection, {}, "jake")
    # the property a network retry actually depends on: the same call, made
    # twice, must resolve to the same activation, not merely leave Request
    # alone
    assert first == second
    assert len(req.Request & {"idempotency_key": "k-5"}) == 1


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
