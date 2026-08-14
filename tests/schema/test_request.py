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
    key = {"subject": "pico", "session_datetime": datetime.datetime(2027, 3, 14, 9, 0)}
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
    req.submit("k-5", "neural", "wl_works", selection, {}, "jake")
    req.submit("k-5", "neural", "wl_works", selection, {}, "jake")
    assert len(req.Request & {"idempotency_key": "k-5"}) == 1


def test_a_failure_between_the_two_inserts_leaves_neither(req, selection, monkeypatch):
    """A Request written without its Activation is an accepted request that will
    never run — which wl.works experiences as a silent hang, the worst available
    failure across that boundary."""

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure after the Request insert")

    monkeypatch.setattr(req.Activation, "insert1", boom)
    with pytest.raises(RuntimeError):
        req.submit("k-6", "neural", "wl_works", selection, {}, "jake")
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
