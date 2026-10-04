# tests/responder/test_jobs.py
"""`JobRequest` -> rows. Design spec section 6.1."""

from __future__ import annotations

import datetime
import re

import pytest

from wl_preproc.contracts.protocol import JobRequest, MetadataBundle

# The runs the event stage measured for every landed session here (design
# spec `2026-10-01-session-listing-and-run-requests-design.md` section 3):
# run 3 lies outside montage 0's window [0, 12), inside montage 1's [12, 24).
_RUNS = [(1, 0.0, 4.0), (2, 5.0, 11.0), (3, 13.0, 16.0)]
_SERIAL = "19011110001"


def _asserted(runs=_RUNS, ids: dict | None = None) -> list[dict]:
    """wl.works' copy of each run, as `GET /sessions` listed it, and its id."""
    return [{"run_number": number, "start_s": start, "end_s": stop,
             "works_run_id": (ids or {}).get(number, f"wr-{number}")} for number, start, stop in runs]


@pytest.fixture
def landed_session(dj_conn, prefix):
    """A `(subject, session_datetime)` with Lab/Subject/Session already on
    file, and its runs measured -- the state `ingest/landing.py`'s
    `land_session` and the event stage would already have produced before
    any job request naming this session could arrive. `runs=()` leaves the
    session landed and its runs not yet read.

    `accept()` (design spec section 6.1, steps 1-4) is scoped to
    `Montage`/`RunAssertion`/`Request`/`Activation`; it is not what creates
    `Session`, its `Subject` parent or its `Run` rows. Mirrors
    `tests/schema/test_request.py`'s own `selection` fixture for the identical
    reason, stated there.
    """
    from wl_preproc.schema import core, pipeline
    from wl_preproc.schema import request as schema_request

    schema_request.activate(prefix=prefix)

    def _land(subject: str, session_datetime: datetime.datetime, runs=_RUNS) -> dict:
        pipeline.lab.Lab.insert1(
            {"lab": "wl", "lab_name": "W", "address": "y", "time_zone": "UTC"},
            skip_duplicates=True,
        )
        pipeline.subject.Subject.insert1(
            {
                "subject": subject,
                "sex": "M",
                "subject_birth_date": datetime.date(2020, 1, 1),
                "subject_description": "",
            },
            skip_duplicates=True,
        )
        key = {"subject": subject, "session_datetime": session_datetime}
        pipeline.Session.insert1(key, skip_duplicates=True)
        if runs:
            core.Run.insert([{**key, "run_number": number, "task_type": 0, "run_start_time": start,
                              "run_stop_time": stop, "closed": 1} for number, start, stop in runs],
                            skip_duplicates=True)
        return key

    return _land


def _request(
    *,
    subject: str,
    session_datetime,
    idempotency_key: str,
    montage_id: int = 0,
    montage_boundaries: list[dict] | None = None,
    runs=_RUNS,
    run_numbers: list[int] | None = None,
    domain: str = "neural",
    experimenter: str = "jw",
    subject_details: dict | None = None,
    probes: list[dict] | None = None,
) -> JobRequest:
    """A `JobRequest` naming `(montage_id, session_datetime)` in its
    selection and asserting `runs`, with `run_numbers` present only when the
    caller supplies one -- an absent key and an empty list are both legal
    ways to ask for a canonical activation, and callers exercising that
    distinction build the dict directly rather than through this helper. A
    canonical's probes each sort no run unless a test says otherwise.
    """
    selection: dict = {"session_datetime": session_datetime, "montage_id": montage_id}
    if run_numbers is not None:
        selection["run_numbers"] = run_numbers
    elif probes:
        selection["probe_runs"] = {probe["serial"]: [] for probe in probes}
    return JobRequest(
        domain=domain,
        selection=selection,
        parameters={},
        idempotency_key=idempotency_key,
        metadata=MetadataBundle(
            runs=_asserted(runs),
            montage_boundaries=montage_boundaries or [],
            probes=probes or [],
            experimenter=experimenter,
            subject=subject,
            task_types=[],
            subject_details=subject_details,
        ),
    )


def test_accept_creates_montage_rows_from_metadata(landed_session, prefix):
    """Every boundary in `metadata.montage_boundaries` is written, not only
    the one the selection names -- a session can have more than one montage
    on file, and a request about one of them still carries the whole set
    (design spec section 1: "everything wl-preproc needs from the ELN
    arrives in the request payload")."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core

    subject = "jbmtg01"
    naive_dt = datetime.datetime(2027, 5, 4, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=1,
        idempotency_key="jbmtg01-k1",
        montage_boundaries=[
            {"montage_id": 0, "start_s": 0.0, "end_s": 12.0},
            {"montage_id": 1, "start_s": 12.0, "end_s": 24.0},
        ],
    )

    key = accept(job, prefix=prefix)

    session_key = {"subject": subject, "session_datetime": naive_dt}
    m0 = (core.Montage & {**session_key, "montage_id": 0}).fetch1()
    m1 = (core.Montage & {**session_key, "montage_id": 1}).fetch1()
    assert m0["start_s"] == pytest.approx(0.0)
    assert m0["end_s"] == pytest.approx(12.0)
    assert m1["start_s"] == pytest.approx(12.0)
    assert m1["end_s"] == pytest.approx(24.0)
    assert key["montage_id"] == 1
    assert key["activation_id"] == 0  # no run_numbers -> canonical


def test_accept_is_idempotent_on_the_same_key(landed_session, prefix):
    """A resubmission of the identical `JobRequest` (the same idempotency
    key, the same everything) must return the same Activation and must not
    duplicate the Montage/RunAssertion rows or the Request row.

    The selection's `session_datetime` is timezone-aware here on purpose,
    not merely for realism: DataJoint's blob codec drops a datetime's tzinfo
    on its first round trip through the database (confirmed directly against
    this project's installed datajoint -- pack/unpack an aware value and it
    comes back naive), so a payload holding the raw, still-aware value on a
    SECOND call would compare unequal to the first call's now-naive stored
    copy and `_reject_key_reuse` would refuse this exact retry as key reuse.
    `accept()` avoids this by never storing a live datetime object in the
    payload at all -- `model_dump(mode="json")` renders it to a
    deterministic ISO-8601 string first (review round 1, I1), and a string
    carries no tzinfo for the blob codec to drop in the first place. This
    test fails loudly (a `DataJointError` mentioning "payload") if that
    normalisation is ever removed.
    """
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core
    from wl_preproc.schema import request as schema_request

    subject = "jbidm01"
    naive_dt = datetime.datetime(2027, 5, 2, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbidm01-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
    )

    first = accept(job, prefix=prefix)
    second = accept(job, prefix=prefix)  # the identical JobRequest, resubmitted

    assert first == second
    assert len(schema_request.Request & {"idempotency_key": "jbidm01-k1"}) == 1
    session_key = {"subject": subject, "session_datetime": naive_dt}
    assert len(core.Montage & {**session_key, "montage_id": 0}) == 1
    assert len(core.RunAssertion & {**session_key, "run_number": 1}) == 1


def test_no_run_numbers_is_canonical_and_some_run_numbers_is_derivative(landed_session, prefix):
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    subject = "jbcd001"
    naive_dt = datetime.datetime(2027, 5, 5, 9, 0)
    landed_session(subject, naive_dt)
    boundaries = [{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}]

    canonical_job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbcd001-k1",
        montage_boundaries=boundaries,
    )
    derivative_job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbcd001-k2",
        montage_boundaries=boundaries,
        run_numbers=[1, 2],
    )

    canonical_key = accept(canonical_job, prefix=prefix)
    derivative_key = accept(derivative_job, prefix=prefix)

    assert canonical_key["activation_id"] == 0
    assert (schema_request.Activation & canonical_key).fetch1("role") == "canonical"
    assert derivative_key["activation_id"] != 0
    assert (schema_request.Activation & derivative_key).fetch1("role") == "derivative"
    assert len(schema_request.ActivationRun & derivative_key) == 2


def test_an_existing_montage_survives_a_request_naming_different_boundaries(landed_session, prefix):
    """Beyond the brief: wl.works owns `Montage`. A second request carrying
    different boundaries for a montage already on file is wl.works
    correcting its own record, and that correction is their call to make
    explicitly -- it is not this pipeline's to infer from whichever payload
    happened to arrive most recently. So the second request's boundaries are
    silently ignored, not applied.
    """
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core

    subject = "jbkeep1"
    naive_dt = datetime.datetime(2027, 5, 8, 9, 0)
    landed_session(subject, naive_dt)

    first_job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbkeep1-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
    )
    accept(first_job, prefix=prefix)

    # A second request, under a DIFFERENT idempotency key (a distinct ask,
    # not a retry of the first), naming DIFFERENT boundaries for the SAME
    # montage_id.
    correction_job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbkeep1-k2",
        montage_boundaries=[{"montage_id": 0, "start_s": 100.0, "end_s": 200.0}],
    )
    accept(correction_job, prefix=prefix)

    session_key = {"subject": subject, "session_datetime": naive_dt}
    montage_row = (core.Montage & {**session_key, "montage_id": 0}).fetch1()

    assert montage_row["start_s"] == pytest.approx(0.0)
    assert montage_row["end_s"] == pytest.approx(12.0)


def test_accept_treats_the_montage_window_as_half_open(landed_session, prefix):
    """[start_s, end_s) -- a run starting inside the montage is the file's
    even when it ends exactly at the montage's end; a run starting exactly
    where the montage ends is not the file's at all. Boundary conditions are
    exactly where an off-by-one in the comparison would hide."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    subject = "jbedg01"
    naive_dt = datetime.datetime(2027, 5, 10, 9, 0)
    runs = [(1, 0.0, 4.0), (2, 8.0, 12.0), (3, 12.0, 16.0)]
    landed_session(subject, naive_dt, runs=runs)
    boundaries = [{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}]

    canonical = accept(_request(subject=subject, session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
                                idempotency_key="jbedg01-k1", montage_boundaries=boundaries, runs=runs),
                       prefix=prefix)
    assert sorted(int(n) for n in (schema_request.ActivationRun & canonical).to_arrays("run_number")) == [1, 2]

    touching_job = _request(subject=subject, session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
                            idempotency_key="jbedg01-k2", montage_boundaries=boundaries, runs=runs,
                            run_numbers=[3])
    with pytest.raises(ValueError, match=r"run\(s\) \[3\] outside montage 0's window"):
        accept(touching_job, prefix=prefix)


def test_accept_rejects_a_selection_missing_a_required_key(landed_session, prefix):
    subject = "jbkey01"
    naive_dt = datetime.datetime(2027, 5, 11, 9, 0)
    landed_session(subject, naive_dt)

    job = JobRequest(
        domain="neural",
        selection={"montage_id": 0},  # session_datetime missing
        parameters={},
        idempotency_key="jbkey01-k1",
        metadata=MetadataBundle(
            runs=_asserted(),
            montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
            probes=[],
            experimenter="jw",
            subject=subject,
            task_types=[],
        ),
    )

    from wl_preproc.responder.jobs import accept

    with pytest.raises(ValueError, match="session_datetime"):
        accept(job, prefix=prefix)


def test_accept_rejects_when_no_montage_is_on_record(landed_session, prefix):
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    subject = "jbnomt1"
    naive_dt = datetime.datetime(2027, 5, 12, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=3,
        idempotency_key="jbnomt1-k1",
        montage_boundaries=[],  # nothing supplied, and montage_id=3 was never recorded
    )

    with pytest.raises(ValueError, match="montage"):
        accept(job, prefix=prefix)

    assert len(schema_request.Request & {"idempotency_key": "jbnomt1-k1"}) == 0


def test_accept_rejects_an_oversized_subject(dj_conn, prefix):
    from wl_preproc.ingest import landing
    from wl_preproc.responder.jobs import accept

    long_subject = "s" * (landing.SUBJECT_MAX_LEN + 1)
    job = _request(
        subject=long_subject,
        session_datetime=datetime.datetime(2027, 5, 13, 9, 0, tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbovr01-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
    )

    with pytest.raises(ValueError, match="subject"):
        accept(job, prefix=prefix)


def test_accept_refuses_to_run_inside_a_transaction(landed_session, prefix):
    """Correction 1: `accept()` opens no transaction of its own, so a caller
    that wraps it in one is what makes `submit()`'s own no-nesting guard
    fire -- mirroring `tests/schema/test_request.py::
    test_submit_refuses_to_run_inside_a_transaction`.

    The Montage insert-if-absent step, run before `submit()` is ever reached,
    still lands as part of the CALLER's still-open transaction -- proving why
    `accept()` itself must never be the one to open it: only the Request/
    Activation pair, which `submit()`'s own guard refuses, is what's absent
    here. That is the intended, safe shape (module docstring, correction 1):
    the Montage write is idempotent on its own, so a caller who makes this
    mistake and then retries `accept()` correctly, outside any transaction,
    still converges on the same rows.
    """
    import datajoint as dj

    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core
    from wl_preproc.schema import request as schema_request

    subject = "jbtxn01"
    naive_dt = datetime.datetime(2027, 5, 6, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbtxn01-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
    )

    conn = dj.conn()
    with conn.transaction:
        with pytest.raises(dj.DataJointError, match="do not nest"):
            accept(job, prefix=prefix)

    assert len(schema_request.Request & {"idempotency_key": "jbtxn01-k1"}) == 0
    session_key = {"subject": subject, "session_datetime": naive_dt}
    assert len(core.Montage & {**session_key, "montage_id": 0}) == 1


def test_session_datetime_is_normalised_through_to_naive_utc(landed_session, prefix):
    """`landing.to_naive_utc` is the one conversion every other datetime key
    in this codebase goes through. A non-UTC offset proves the actual
    wall-clock conversion rather than merely a value that was already UTC
    and would pass even with tzinfo dropped naively.
    """
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core

    subject = "jbtz001"
    naive_dt = datetime.datetime(2027, 5, 7, 14, 0)  # what must actually get stored
    landed_session(subject, naive_dt)

    # 09:00 at a fixed UTC-05:00 offset is the identical instant as 14:00 UTC.
    offset_aware = datetime.datetime(
        2027, 5, 7, 9, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=-5))
    )
    job = _request(
        subject=subject,
        session_datetime=offset_aware,
        montage_id=0,
        idempotency_key="jbtz001-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
    )

    key = accept(job, prefix=prefix)

    assert key["session_datetime"] == naive_dt
    assert (
        len(core.Montage & {"subject": subject, "session_datetime": naive_dt, "montage_id": 0})
        == 1
    )


# --- Review round 1 (2026-08-16): two Criticals and two Importants, all in
# territory the original 12 tests above could not reach. ---


def test_a_rejected_request_leaves_no_montage_or_run_assertion_and_a_correction_then_succeeds(
    landed_session, prefix
):
    """C1: validate before writing, not after. The first draft inserted the
    request's rows and only then checked the window, so a rejected request
    permanently planted the very rows that caused its own rejection --
    skip_duplicates=True then discarded every later correction, so the
    request that FIXED it was rejected too, citing the stale values it
    refused to replace. Here a derivative naming run 3, outside montage
    [0, 12), is rejected with its montage and its asserted runs unwritten,
    and the corrected request naming run 1 then succeeds.
    """
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core
    from wl_preproc.schema import request as schema_request

    subject = "jbres01"
    naive_dt = datetime.datetime(2027, 5, 14, 9, 0)
    landed_session(subject, naive_dt)
    boundaries = [{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}]

    bad_job = _request(subject=subject, session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
                       idempotency_key="jbres01-k1", montage_boundaries=boundaries, run_numbers=[3])
    with pytest.raises(ValueError, match="outside montage 0's window"):
        accept(bad_job, prefix=prefix)

    session_key = {"subject": subject, "session_datetime": naive_dt}
    assert len(core.Montage & {**session_key, "montage_id": 0}) == 0, (
        "a rejected request must not plant the Montage row it was rejected over"
    )
    assert len(core.RunAssertion & session_key) == 0, (
        "a rejected request must not plant the runs it asserted"
    )
    assert len(schema_request.Request & {"idempotency_key": "jbres01-k1"}) == 0

    corrected_job = _request(subject=subject, session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
                             idempotency_key="jbres01-k2", montage_boundaries=boundaries, run_numbers=[1])
    key = accept(corrected_job, prefix=prefix)  # must not raise

    assert (core.RunAssertion & {**session_key, "run_number": 1}).fetch1("works_run_id") == "wr-1"
    assert (schema_request.Activation & key).fetch1("role") == "derivative"


def test_accept_coerces_an_iso8601_string_session_datetime(landed_session, prefix):
    """C2: real wire traffic carries `session_datetime` as a plain `str` --
    JSON has no datetime type, and `docs/schemas/job_request.json` declares
    `selection` as a bare object with no `session_datetime` property, so
    pydantic coerces nothing inside it. Every other test in this file hands
    `accept()` a live `datetime.datetime` directly; this is the one that
    proves the actual wire shape works too.
    """
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core

    subject = "jbstr01"
    naive_dt = datetime.datetime(2027, 5, 15, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime="2027-05-15T09:00:00Z",  # a plain string, not a datetime
        montage_id=0,
        idempotency_key="jbstr01-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
    )

    key = accept(job, prefix=prefix)

    assert key["session_datetime"] == naive_dt
    assert (
        len(core.Montage & {"subject": subject, "session_datetime": naive_dt, "montage_id": 0})
        == 1
    )


def test_accept_rejects_a_session_datetime_that_is_neither_a_datetime_nor_a_string(
    landed_session, prefix
):
    """Before C2's fix this reached `to_naive_utc` and raised a bare
    `AttributeError` -- outside `accept()`'s documented `ValueError`
    contract, and something Task 8's handler never expected from a request
    that validated cleanly against `JobRequest`'s own schema."""
    from wl_preproc.responder.jobs import accept

    subject = "jbbad01"
    naive_dt = datetime.datetime(2027, 5, 16, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime=12345,  # neither a datetime nor a string
        montage_id=0,
        idempotency_key="jbbad01-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
    )

    with pytest.raises(ValueError, match="session_datetime"):
        accept(job, prefix=prefix)


def test_accept_normalises_an_aware_datetime_anywhere_in_the_stored_payload(
    landed_session, prefix
):
    """I1: the tzinfo-drop finding is not scoped to `selection.
    session_datetime` -- any aware datetime anywhere in the stored payload
    reproduces the identical defect on a retry. Here it's nested inside
    `parameters` instead, a field `accept()` never reads for its own logic
    but still stores verbatim as evidence.
    """
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    subject = "jbpar01"
    naive_dt = datetime.datetime(2027, 5, 23, 9, 0)
    landed_session(subject, naive_dt)
    aware_param = datetime.datetime(2027, 5, 23, 8, 0, tzinfo=datetime.UTC)
    job = JobRequest(
        domain="neural",
        selection={"session_datetime": naive_dt.replace(tzinfo=datetime.UTC), "montage_id": 0},
        parameters={"calibrated_on": aware_param},
        idempotency_key="jbpar01-k1",
        metadata=MetadataBundle(
            runs=_asserted(),
            montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
            probes=[],
            experimenter="jw",
            subject=subject,
            task_types=[],
        ),
    )

    first = accept(job, prefix=prefix)
    second = accept(job, prefix=prefix)  # identical resubmission -- must not raise

    assert first == second
    assert len(schema_request.Request & {"idempotency_key": "jbpar01-k1"}) == 1


def test_an_out_of_range_montage_id_is_refused_with_the_field_named(landed_session, prefix):
    """I2: `core.Montage.montage_id` is a signed `tinyint` (-128..127).

    **The refusal moved on 2026-08-26 and this test moved with it.** It used to
    happen inside `accept()`; `contracts.protocol.MontageBoundary` now carries
    the bound, so the request cannot be CONSTRUCTED with a montage id this
    column cannot hold. Both are the same answer on the wire -- `handler.py`
    maps `pydantic.ValidationError` (a `ValueError` subclass) and this module's
    own `ValueError` to the same `422` -- so the `raises` block now spans the
    build as well as the call, and asserts what a caller can actually observe:
    a `ValueError` naming the offending field, from one of the two.
    """
    from wl_preproc.responder.jobs import accept

    subject = "jbrng01"
    naive_dt = datetime.datetime(2027, 5, 17, 9, 0)
    landed_session(subject, naive_dt)

    with pytest.raises(ValueError, match="montage_id"):
        job = _request(
            subject=subject,
            session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
            montage_id=99999,
            idempotency_key="jbrng01-k1",
            montage_boundaries=[{"montage_id": 99999, "start_s": 0.0, "end_s": 12.0}],
        )
        accept(job, prefix=prefix)


def test_a_non_finite_start_s_or_end_s_is_refused_with_the_field_named(landed_session, prefix):
    """I2: `start_s`/`end_s` are `double` -- unbounded in magnitude for any
    realistic session-time-seconds value, but a non-finite float (here,
    infinity) or a wrong type is exactly the "syntactically fine Python value,
    wrong for the column" case this whole guard class exists for.

    Refusal moved to `MontageBoundary` on 2026-08-26; see the montage-id test
    above for why the `raises` block now spans the build as well as the call.
    """
    from wl_preproc.responder.jobs import accept

    subject = "jbrng05"
    naive_dt = datetime.datetime(2027, 5, 21, 9, 0)
    landed_session(subject, naive_dt)

    with pytest.raises(ValueError, match="end_s"):
        job = _request(
            subject=subject,
            session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
            montage_id=0,
            idempotency_key="jbrng05-k1",
            montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": float("inf")}],
        )
        accept(job, prefix=prefix)


def test_accept_rejects_a_session_this_host_has_never_ingested(
    dj_conn, prefix, table_snapshot, deep_equal
):
    """Whole-branch review C1. A job for a `(subject, session_datetime)` with
    no `Session` row is a `ValueError` -> `422`, raised BEFORE any write --
    not an `IntegrityError` out of `core.Montage.insert`'s foreign key, which
    `handler.py` maps to a `500` and `docs/ops/lab-host-protocol.md`
    documents as retryable. A conforming client retried that forever.

    **This is the ordinary case.** wl.works knows a session exists from the
    ELN hours before its transfer lands here, so any button pressed in that
    window arrives at exactly this state. Deliberately NOT using
    `landed_session`: the whole point is that nothing has landed. `dj_conn`
    and `prefix` are taken directly instead, and `accept()` activates the
    schema itself.

    **Proven by row snapshot, not by `in_transaction`.** DataJoint's
    `insert()` calls `connection.query()` directly and never touches the
    transaction machinery, so `in_transaction` reads `False` for a writing
    function and a reading one alike -- this codebase has been misled by
    that assumption four separate times. Every table `accept()` can write,
    plus `Session` itself (which it must never create), is snapshotted
    whole -- every row, every column -- before and after, and compared with
    `deep_equal` rather than bare `==` (see `tests/conftest.py`).

    Both halves of the message are asserted, because both are what makes the
    422 body actionable for wl.works' UI: the subject and the session
    datetime name WHICH session, and "not yet on record" is what lets them
    render "that session has not arrived yet" instead of "invalid request".
    """
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core, pipeline
    from wl_preproc.schema import request as schema_request

    schema_request.activate(prefix=prefix)

    subject = "jbnoses"
    naive_dt = datetime.datetime(2027, 5, 25, 9, 0)
    session_key = {"subject": subject, "session_datetime": naive_dt}
    assert len(pipeline.Session & session_key) == 0, "the premise: this session never landed"

    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbnoses-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
    )

    written_tables = [
        core.Montage,
        core.RunAssertion,
        schema_request.Request,
        schema_request.Activation,
        schema_request.ActivationRun,
        schema_request.ActivationProbeRun,
        pipeline.Session,
    ]
    before = [table_snapshot(table) for table in written_tables]

    with pytest.raises(ValueError) as excinfo:
        accept(job, prefix=prefix)

    message = str(excinfo.value)
    assert subject in message, "the message must name which subject"
    assert "2027-05-25T09:00:00" in message, "the message must name which session"
    assert "not yet on record" in message
    assert "IntegrityError" not in message and "foreign key" not in message, (
        "the database's own constraint text must never be what the caller reads"
    )

    after = [table_snapshot(table) for table in written_tables]
    for table, rows_before, rows_after in zip(written_tables, before, after, strict=True):
        assert deep_equal(rows_before, rows_after), (
            f"{table.__name__} changed under a rejected request: "
            f"{len(rows_before)} row(s) before, {len(rows_after)} after"
        )


def test_the_subjects_details_fill_its_own_record(landed_session, prefix):
    """Design spec `2026-09-28-nwb-builder-design.md` section 9, the
    requester's decision: wl.works sends the animal's species, sex and date
    of birth with the job request, and they replace what wl-preproc knew --
    `ingest/landing.py` lands only a stub."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import pipeline

    subject = "jbsubj01"
    naive_dt = datetime.datetime(2027, 5, 9, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        idempotency_key="jbsubj01-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
        subject_details={"species": "Macaca mulatta", "sex": "F",
                         "date_of_birth": datetime.date(2016, 3, 2)},
    )

    accept(job, prefix=prefix)

    row = (pipeline.subject.Subject & {"subject": subject}).fetch1()
    assert (row["sex"], row["subject_birth_date"]) == ("F", datetime.date(2016, 3, 2))
    assert (pipeline.subject.Subject.Species & {"subject": subject}).fetch1("species") == "Macaca mulatta"


def test_a_request_without_details_leaves_the_subject_alone(landed_session, prefix):
    """They are optional: a request that does not carry them changes nothing
    about the subject."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import pipeline

    subject = "jbsubj02"
    naive_dt = datetime.datetime(2027, 5, 10, 9, 0)
    landed_session(subject, naive_dt)
    before = (pipeline.subject.Subject & {"subject": subject}).fetch1()
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        idempotency_key="jbsubj02-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
    )

    accept(job, prefix=prefix)

    assert (pipeline.subject.Subject & {"subject": subject}).fetch1() == before
    assert len(pipeline.subject.Subject.Species & {"subject": subject}) == 0


# -- What wl.works reports about each insertion (design spec
# 2026-09-30-nwb-probes-design.md section 2.2)

def _probe(insertion_number, serial, **fields):
    return {"serial": serial, "insertion_number": insertion_number, **fields}


def test_every_insertion_wl_works_reports_is_recorded(landed_session, prefix):
    """The aim, the trajectory and the latest assignment, per insertion, as
    wl.works sent them. The serial is plain text: the probe may not have
    been recorded yet."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import ephys

    naive_dt = datetime.datetime(2027, 5, 11, 9, 0)
    key = landed_session("jbprob01", naive_dt)
    accept(_request(
        subject="jbprob01", session_datetime=naive_dt.replace(tzinfo=datetime.UTC), idempotency_key="jbprob01-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
        probes=[
            _probe(1, "19011110001", trajectory_id="T-7", target={"area": "V4d", "atlas": "CHARM", "atlas_level": 6},
                   area_assignment={"area": "V4v", "source": "at_rig",
                                    "asserted_at": datetime.datetime(2027, 5, 11, 10, 0, 0, 250000,
                                                                     tzinfo=datetime.UTC)}),
            _probe(2, "19011110002"),
        ],
    ), prefix=prefix)

    reports = (ephys.InsertionReport & key).to_dicts(order_by="insertion_number")
    assert [(r["insertion_number"], r["probe_serial"], r["trajectory_id"], r["target_area"], r["target_atlas"],
             r["target_atlas_level"]) for r in reports] == [
        (1, "19011110001", "T-7", "V4d", "CHARM", 6), (2, "19011110002", None, None, None, None)]
    (assigned,) = (ephys.AreaAssignment & key).to_dicts()
    assert (assigned["insertion_number"], assigned["area"], assigned["source"]) == (1, "V4v", "at_rig")
    # Naive UTC, to the microsecond: two assignments a second apart stay two.
    assert assigned["asserted_at"] == datetime.datetime(2027, 5, 11, 10, 0, 0, 250000)


def test_a_later_request_corrects_the_report_and_adds_the_assignment(landed_session, prefix):
    """The latest request wins for the report, as for the subject's details;
    assignments are append-only, as wl.works' own table is. An insertion a
    later request does not mention is left alone: a request may name only
    its own montage's insertions."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import ephys

    naive_dt = datetime.datetime(2027, 5, 12, 9, 0)
    runs = [*_RUNS, (4, 25.0, 30.0)]  # one run in each of the three montages
    key = landed_session("jbprob02", naive_dt, runs=runs)
    first = {"area": "V4d", "source": "at_rig", "asserted_at": "2027-05-12T10:00:00Z"}
    boundaries = [{"montage_id": m, "start_s": 12.0 * m, "end_s": 12.0 * (m + 1)} for m in range(3)]
    for idempotency_key, montage_id, probes in (
        ("jbprob02-k1", 0, [_probe(1, "19011110001", area_assignment=first), _probe(2, "19011110002")]),
        ("jbprob02-k2", 1, [_probe(1, "19011110003", trajectory_id="T-8", area_assignment={
            "area": "V4v", "source": "histology", "asserted_at": "2027-06-01T12:00:00Z"})]),
        # A retry of the first: the report regresses, as the subject's details
        # would, and its assignment is not recorded twice.
        ("jbprob02-k1", 0, [_probe(1, "19011110001", area_assignment=first), _probe(2, "19011110002")]),
        ("jbprob02-k3", 2, [_probe(1, "19011110003", trajectory_id="T-8")]),
    ):
        accept(_request(subject="jbprob02", session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
                        idempotency_key=idempotency_key, montage_id=montage_id,
                        montage_boundaries=boundaries, runs=runs, probes=probes),
               prefix=prefix)

    reports = (ephys.InsertionReport & key).to_dicts(order_by="insertion_number")
    assert [(r["insertion_number"], r["probe_serial"], r["trajectory_id"]) for r in reports] == [
        (1, "19011110003", "T-8"), (2, "19011110002", None)]
    assigned = (ephys.AreaAssignment & key).to_dicts(order_by="asserted_at")
    assert [(a["area"], a["source"]) for a in assigned] == [("V4d", "at_rig"), ("V4v", "histology")]


def test_a_refused_request_records_no_report(landed_session, prefix):
    """Every check runs before anything is written (review C1): a request
    refused for its montage leaves no report behind."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import ephys

    naive_dt = datetime.datetime(2027, 5, 13, 9, 0)
    key = landed_session("jbprob03", naive_dt)
    with pytest.raises(ValueError):
        accept(_request(subject="jbprob03", session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
                        idempotency_key="jbprob03-k1", montage_id=4, probes=[_probe(1, "19011110001")]),
               prefix=prefix)
    ephys.activate(prefix=prefix)
    assert not ephys.InsertionReport & key


# -- The canonical lifecycle (design spec 2026-09-30-canonical-lifecycle-design.md section 3)

_LC_BOUNDARIES = [{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}]


def _lifecycle_job(subject, session_datetime, key, **selection) -> JobRequest:
    return JobRequest(
        domain="neural",
        selection={"session_datetime": session_datetime, "montage_id": 0, **selection},
        parameters={},
        idempotency_key=key,
        metadata=MetadataBundle(runs=_asserted(), montage_boundaries=_LC_BOUNDARIES, probes=[],
                                experimenter="jw", subject=subject, task_types=[]),
    )


def test_a_replacement_request_supersedes_the_named_canonical(landed_session, prefix):
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    when = datetime.datetime(2027, 5, 20, 10, 0)
    landed_session("jblc002", when)
    first = accept(_lifecycle_job("jblc002", when, "jblc002-k1"), prefix=prefix)
    job = _lifecycle_job("jblc002", when, "jblc002-k2", role="canonical", supersedes_activation_id=0)
    replacement = accept(job, prefix=prefix)
    row = (schema_request.Activation & replacement).fetch1()
    assert (row["activation_id"], row["role"], row["supersedes"]) == (1, "canonical", first["activation_id"])
    assert accept(job, prefix=prefix) == replacement
    assert accept(_lifecycle_job("jblc002", when, "jblc002-k3"), prefix=prefix) == replacement
    # The first request, re-sent under its own key (the 2b final review's M2).
    assert accept(_lifecycle_job("jblc002", when, "jblc002-k1"), prefix=prefix) == replacement


def test_a_replacement_of_a_superseded_canonical_is_a_conflict(landed_session, prefix):
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema.request import SupersedeConflict

    when = datetime.datetime(2027, 5, 20, 11, 0)
    landed_session("jblc003", when)
    accept(_lifecycle_job("jblc003", when, "jblc003-k1"), prefix=prefix)
    accept(_lifecycle_job("jblc003", when, "jblc003-k2", role="canonical", supersedes_activation_id=0), prefix=prefix)
    with pytest.raises(SupersedeConflict, match="current canonical is activation 1"):
        accept(_lifecycle_job("jblc003", when, "jblc003-k3", role="canonical", supersedes_activation_id=0),
               prefix=prefix)


@pytest.mark.parametrize("selection", [
    {"supersedes_activation_id": 0},
    {"role": "derivative", "supersedes_activation_id": 0, "run_numbers": [1]},
    {"role": "derivative"},
    {"role": "bogus"},
    {"role": "canonical", "supersedes_activation_id": -1},
    {"role": "canonical", "supersedes_activation_id": True},
    {"role": "canonical", "supersedes_activation_id": "0"},
    {"role": "canonical", "run_numbers": [1]},
    {"run_numbers": [3]},
])
def test_a_selection_the_lifecycle_refuses_is_a_value_error(landed_session, prefix, selection):
    """Section 3's refusals, each a `422` over HTTP: only a canonical
    supersedes, a derivative names its runs, and an activation id is one
    non-negative integer."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    when = datetime.datetime(2027, 5, 20, 12, 0)
    landed_session("jblc004", when)
    key = f"jblc004-{sorted(selection.items())!r}"
    with pytest.raises(ValueError):
        accept(_lifecycle_job("jblc004", when, key, **selection), prefix=prefix)
    assert not schema_request.Request & {"idempotency_key": key}


def test_a_canonical_role_with_no_runs_named_takes_every_run_of_the_montage(landed_session, prefix):
    """`role: canonical` with an empty `run_numbers` is the plain canonical:
    the file holds every measured run of its montage (the requester's
    decision 1 of 2026-10-01)."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    when = datetime.datetime(2027, 5, 20, 13, 0)
    landed_session("jblc005", when)
    key = accept(_lifecycle_job("jblc005", when, "jblc005-k1", role="canonical", run_numbers=[]), prefix=prefix)
    assert key["activation_id"] == 0 and (schema_request.Activation & key).fetch1("role") == "canonical"
    assert sorted(int(n) for n in (schema_request.ActivationRun & key).to_arrays("run_number")) == [1, 2]


@pytest.mark.parametrize("selection", [{"supersedes_activation_id": None},
                                       {"role": "canonical", "supersedes_activation_id": None}])
def test_a_null_supersedes_is_absent(landed_session, prefix, selection):
    """The 2b final review's M10: a client that serialises an optional
    field as null asks for a plain canonical, as a null `role` already
    does."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    when = datetime.datetime(2027, 5, 20, 14, 0)
    landed_session("jblc006", when)
    key = accept(_lifecycle_job("jblc006", when, f"jblc006-{len(selection)}", **selection), prefix=prefix)
    row = (schema_request.Activation & key).fetch1()
    assert (row["activation_id"], row["role"], row["supersedes"]) == (0, "canonical", None)


# -- Requests that name runs (design spec
# `2026-10-01-session-listing-and-run-requests-design.md` section 3), with one
# probe whose runs a canonical states.


def _landed_with_runs(landed_session, subject: str, day: int, runs=_RUNS) -> dict:
    return landed_session(subject, datetime.datetime(2027, 9, day, 9, 0), runs=runs)


def _runs_job(key: dict, idempotency_key: str, *, runs=_RUNS, ids: dict | None = None,
              serials=(_SERIAL,), **selection) -> JobRequest:
    return JobRequest(
        domain="neural",
        selection={"session_datetime": key["session_datetime"], "montage_id": 0, **selection},
        parameters={},
        idempotency_key=idempotency_key,
        metadata=MetadataBundle(
            montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}], runs=_asserted(runs, ids),
            probes=[{"serial": serial, "insertion_number": index} for index, serial in enumerate(serials, start=1)],
            experimenter="jw", subject=key["subject"], task_types=[]),
    )


def _run_rows(table, key) -> list:
    return sorted(tuple(row[name] for name in ("probe_serial", "run_number") if name in row)
                  for row in (table & key).to_dicts())


def test_a_canonical_naming_runs_records_them_and_holds_its_montages_runs(landed_session, prefix):
    """The requester's decision 1: the file holds every measured run whose
    start lies in the montage's window; each probe's list is its sort's."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, "runjob1", 1)
    activation = accept(_runs_job(key, "runjob1-k1", probe_runs={_SERIAL: [1]}), prefix=prefix)
    assert sorted((row["run_number"], row["works_run_id"]) for row in (core.RunAssertion & key).to_dicts()) == [
        (1, "wr-1"), (2, "wr-2"), (3, "wr-3")]
    assert _run_rows(schema_request.ActivationRun, activation) == [(1,), (2,)]
    assert _run_rows(schema_request.ActivationProbeRun, activation) == [(_SERIAL, 1)]


@pytest.mark.parametrize("runs, expect", [
    ([(1, 0.01, 4.0), (2, 5.0, 11.0)], "run 1's start, 0.01 s, is not the measured 0.0 s"),
    ([(1, 0.0, 4.0), (2, 5.0, 11.5)], "run 2's end, 11.5 s, is not the measured 11.0 s"),
    ([(1, 0.0, 4.0), (2, 5.0, 11.0), (9, 20.0, 21.0)], "run 9 is not a measured run of this session"),
    ([(1, 0.0, 4.0)], "measured run(s) [2] lie in montage 0's window"),
])
def test_a_request_from_a_stale_listing_is_a_run_mismatch_and_writes_nothing(landed_session, prefix, runs, expect):
    """Checked as the request arrives, against `core.Run`, within about 2 ms
    (design spec section 3.2): the reason names the run and says to rebuild."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, "runjob2", 2)
    with pytest.raises(ValueError, match=re.escape(expect)) as refused:
        accept(_runs_job(key, f"runjob2-{len(runs)}-{runs[-1][2]}", runs=runs, probe_runs={_SERIAL: [1]}),
               prefix=prefix)
    assert "rebuild the request from a fresh GET /sessions" in str(refused.value)
    assert (len(core.RunAssertion & key), len(schema_request.Activation & key)) == (0, 0)


@pytest.mark.parametrize("subject, measured, runs, selection, expect", [
    # Which copy, and which id, is wl.works'? Neither is guessed.
    ("runjob10", _RUNS, _RUNS + [(1, 0.0, 4.0)], {"probe_runs": {_SERIAL: [1]}},
     "metadata.runs names run 1 twice"),
    # A canonical holds every measured run of its window, and this one has none.
    ("runjob11", [(3, 13.0, 16.0)], [(3, 13.0, 16.0)], {"probe_runs": {_SERIAL: []}},
     "montage 0's window [0.0, 12.0) holds no measured run"),
    # Amendment 19: a derivative's run is asserted now or before; without that
    # its file's run would carry no works_run_id to join.
    ("runjob12", _RUNS, [(1, 0.0, 4.0), (3, 13.0, 16.0)], {"run_numbers": [2]},
     "selection names run(s) [2] asserted neither in metadata.runs nor before"),
    ("runjob13", _RUNS, _RUNS, {"run_numbers": [9]},
     "selection names run(s) [9] are not measured runs of this session"),
])
def test_runs_that_cannot_be_held_as_asserted_are_refused_and_write_nothing(landed_session, prefix, subject,
                                                                              measured, runs, selection, expect):
    """Design spec section 3.2's refusals the stale-listing cases above do not
    reach (Plan B's final review, I2): each is a 422 before anything is
    written."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, subject, 10, runs=measured)
    with pytest.raises(ValueError, match=re.escape(expect)):
        accept(_runs_job(key, f"{subject}-k1", runs=runs, **selection), prefix=prefix)
    assert (len(core.RunAssertion & key), len(schema_request.Activation & key)) == (0, 0)


def test_a_run_asserted_again_under_another_id_is_a_conflict(landed_session, prefix):
    """A 409, which wl.works stops on: the two disagree about which run this
    is. Through the server's translation, as wl.works meets it."""
    from wl_preproc.responder.handler import ConflictError
    from wl_preproc.responder.jobs import RunIdConflict, accept
    from wl_preproc.responder.server import _translate_accept_errors

    key = _landed_with_runs(landed_session, "runjob3", 3)
    accept(_runs_job(key, "runjob3-k1", probe_runs={_SERIAL: [1, 2]}), prefix=prefix)
    renamed = _runs_job(key, "runjob3-k2", ids={2: "wr-other"}, probe_runs={_SERIAL: [1, 2]})
    with pytest.raises(RunIdConflict, match="run 2 is recorded with works_run_id 'wr-2'; this request names 'wr-other'"):
        accept(renamed, prefix=prefix)
    with pytest.raises(ConflictError):
        _translate_accept_errors(renamed, prefix=prefix)


def test_one_works_run_id_for_two_runs_is_refused(landed_session, prefix):
    """An `animal_session_run` records the one measured run it came from, so
    one id naming two runs would join two of the file's runs to one row in
    wl.works (Plan B's final review, M2). In one request it is a 422; against
    an id already recorded for another run of the session, a 409, as for a
    run named under a second id. Neither writes anything."""
    from wl_preproc.responder.jobs import RunIdConflict, accept
    from wl_preproc.schema import core
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, "runjob14", 14)
    with pytest.raises(ValueError, match=re.escape("metadata.runs names works_run_id 'wr-x' for runs 1 and 2")):
        accept(_runs_job(key, "runjob14-k1", ids={1: "wr-x", 2: "wr-x"}, probe_runs={_SERIAL: [1]}), prefix=prefix)
    assert (len(core.RunAssertion & key), len(schema_request.Activation & key)) == (0, 0)

    accept(_runs_job(key, "runjob14-k2", runs=_RUNS[:2], probe_runs={_SERIAL: [1, 2]}), prefix=prefix)
    before = len(schema_request.Activation & key)
    reused = _runs_job(key, "runjob14-k3", runs=_RUNS[2:], ids={3: "wr-1"}, run_numbers=[2])
    with pytest.raises(RunIdConflict, match=re.escape("works_run_id 'wr-1' is recorded for run 1; this request "
                                                      "names it for run 3")):
        accept(reused, prefix=prefix)
    assert (len(core.RunAssertion & key), len(schema_request.Activation & key)) == (2, before)


def test_runs_not_yet_measured_are_not_yet_ingested(landed_session, prefix):
    """wl.works retries this one: the event stage has not read the session."""
    from wl_preproc.responder.jobs import accept

    key = landed_session("runjob4", datetime.datetime(2027, 9, 4, 9, 0), runs=())
    with pytest.raises(ValueError, match="has no measured run on this host yet"):
        accept(_runs_job(key, "runjob4-k1", probe_runs={_SERIAL: [1]}), prefix=prefix)


@pytest.mark.parametrize("probe_runs, expect", [
    (None, "selection.probe_runs must give every probe's runs"),
    ({}, "selection.probe_runs names [], and metadata.probes names ['19011110001']"),
    ({_SERIAL: [1], "19011110002": [1]}, "selection.probe_runs names ['19011110001', '19011110002']"),
    ({_SERIAL: [3]}, "probe 19011110001's runs [3] are not among the file's runs [1, 2]"),
    ({_SERIAL: ["1"]}, "selection.probe_runs['19011110001'] must be a list of run numbers"),
])
def test_each_probes_runs_are_stated_in_full_and_within_the_file(landed_session, prefix, probe_runs, expect):
    """wl.works' Plan 20 rule: the record is the run set each probe resolved
    to, so every probe's list is stated, and only the file's runs."""
    from wl_preproc.responder.jobs import accept

    key = _landed_with_runs(landed_session, "runjob5", 5)
    selection = {} if probe_runs is None else {"probe_runs": probe_runs}
    with pytest.raises(ValueError, match=re.escape(expect)):
        accept(_runs_job(key, f"runjob5-{expect[:20]}", **selection), prefix=prefix)


def test_a_derivative_names_its_runs(landed_session, prefix):
    """The requester's decision 3: a derivative selects whole runs, the unit
    a sort runs over, so each probe the request names covers them all. With
    no row, the sorter and the description would read that probe as sorting
    nothing (Plan B's final review, I1)."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, "runjob6", 6)
    activation = accept(_runs_job(key, "runjob6-k1", run_numbers=[2], serials=(_SERIAL, "19011110002")),
                        prefix=prefix)
    assert (schema_request.Activation & activation).fetch1("role") == "derivative"
    assert _run_rows(schema_request.ActivationRun, activation) == [(2,)]
    assert _run_rows(schema_request.ActivationProbeRun, activation) == [(_SERIAL, 2), ("19011110002", 2)]


@pytest.mark.parametrize("selection, expect", [
    ({"role": "canonical", "run_numbers": [1], "probe_runs": {_SERIAL: [1]}}, "selection.run_numbers is a derivative's"),
    ({"run_numbers": [2], "probe_runs": {_SERIAL: [2]}}, "selection.probe_runs is a canonical's"),
    ({"run_numbers": ["2"]}, "selection.run_numbers must be a list of run numbers"),
    ({"run_numbers": [3]}, "selection names run(s) [3] outside montage 0's window"),
])
def test_a_selection_that_misplaces_runs_is_refused(landed_session, prefix, selection, expect):
    from wl_preproc.responder.jobs import accept

    key = _landed_with_runs(landed_session, "runjob7", 7)
    with pytest.raises(ValueError, match=re.escape(expect)):
        accept(_runs_job(key, f"runjob7-{expect[:24]}", **selection), prefix=prefix)


@pytest.mark.parametrize("selection, expect", [
    ({"block_ids": [1], "probe_runs": {_SERIAL: [1]}},
     "selection.block_ids is retired: a derivative names its runs in selection.run_numbers"),
    ({"role": "canonical", "block_ids": [1, 2], "probe_runs": {_SERIAL: [1]}}, "selection.block_ids is retired"),
    ({"role": "derivative"}, "selection['role'] 'derivative' needs run_numbers: a derivative is its run set"),
])
def test_the_retired_block_selection_is_refused_naming_its_replacement(landed_session, prefix, selection, expect):
    """Design spec `2026-10-01-session-listing-and-run-requests-design.md`
    section 3.1: nothing but this repository's tests sent blocks, and a
    request that still does is a 422 that says what replaced them."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, "runjob8", 8)
    idempotency_key = f"runjob8-{sorted(selection)!r}"
    with pytest.raises(ValueError, match=re.escape(expect)):
        accept(_runs_job(key, idempotency_key, **selection), prefix=prefix)
    assert not schema_request.Request & {"idempotency_key": idempotency_key}
