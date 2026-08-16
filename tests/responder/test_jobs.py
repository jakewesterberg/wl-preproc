# tests/responder/test_jobs.py
"""`JobRequest` -> rows. Design spec section 6.1."""

from __future__ import annotations

import datetime

import pytest

from wl_preproc.contracts.protocol import JobRequest, MetadataBundle


@pytest.fixture
def landed_session(dj_conn, prefix):
    """A `(subject, session_datetime)` with Lab/Subject/Session already on
    file -- the state `ingest/landing.py`'s `land_session` would already have
    produced before any job request naming this session could arrive.

    `accept()` (design spec section 6.1, steps 1-4) is scoped to
    `Montage`/`Block`/`Request`/`Activation`; it is not what creates `Session`
    or its `Subject` parent. Mirrors `tests/schema/test_request.py`'s own
    `selection` fixture for the identical reason, stated there.
    """
    from wl_preproc.schema import pipeline
    from wl_preproc.schema import request as schema_request

    schema_request.activate(prefix=prefix)

    def _land(subject: str, session_datetime: datetime.datetime) -> dict:
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
        return key

    return _land


def _request(
    *,
    subject: str,
    session_datetime,
    idempotency_key: str,
    montage_id: int = 0,
    montage_boundaries: list[dict] | None = None,
    blocks: list[dict] | None = None,
    block_ids: list[int] | None = None,
    domain: str = "neural",
    experimenter: str = "jw",
) -> JobRequest:
    """A `JobRequest` naming `(montage_id, session_datetime)` in its
    selection, with `block_ids` present only when the caller supplies one --
    an absent key and an empty list are both legal ways to ask for a
    canonical activation, and callers exercising that distinction build the
    dict directly rather than through this helper.
    """
    selection: dict = {"session_datetime": session_datetime, "montage_id": montage_id}
    if block_ids is not None:
        selection["block_ids"] = block_ids
    return JobRequest(
        domain=domain,
        selection=selection,
        parameters={},
        idempotency_key=idempotency_key,
        metadata=MetadataBundle(
            blocks=blocks or [],
            montage_boundaries=montage_boundaries or [],
            probes=[],
            experimenter=experimenter,
            subject=subject,
            task_types=[],
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
    assert key["activation_id"] == 0  # no block_ids -> canonical


def test_accept_creates_block_rows_with_works_block_id(landed_session, prefix):
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core

    subject = "jbblk01"
    naive_dt = datetime.datetime(2027, 5, 3, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbblk01-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
        blocks=[
            {
                "block_id": 1,
                "task_type": "rf_map",
                "start_s": 0.0,
                "end_s": 4.0,
                "works_block_id": "wb-1",
            },
            {"block_id": 2, "task_type": "attention", "start_s": 4.0, "end_s": 12.0},
        ],
    )

    accept(job, prefix=prefix)

    session_key = {"subject": subject, "session_datetime": naive_dt}
    with_id = (core.Block & {**session_key, "block_id": 1}).fetch1()
    without_id = (core.Block & {**session_key, "block_id": 2}).fetch1()
    assert with_id["works_block_id"] == "wb-1"
    assert with_id["task_type"] == "rf_map"
    # a block dict that omits works_block_id leaves the column at its null
    # default, exactly like a direct core.Block insert would (test_core.py's
    # own test_a_block_round_trips_with_and_without_works_block_id)
    assert without_id["works_block_id"] is None
    assert without_id["task_type"] == "attention"


def test_accept_is_idempotent_on_the_same_key(landed_session, prefix):
    """A resubmission of the identical `JobRequest` (the same idempotency
    key, the same everything) must return the same Activation and must not
    duplicate the Montage/Block rows or the Request row.

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
        blocks=[
            {
                "block_id": 1,
                "task_type": "rf_map",
                "start_s": 0.0,
                "end_s": 4.0,
                "works_block_id": "wb-1",
            }
        ],
    )

    first = accept(job, prefix=prefix)
    second = accept(job, prefix=prefix)  # the identical JobRequest, resubmitted

    assert first == second
    assert len(schema_request.Request & {"idempotency_key": "jbidm01-k1"}) == 1
    session_key = {"subject": subject, "session_datetime": naive_dt}
    assert len(core.Montage & {**session_key, "montage_id": 0}) == 1
    assert len(core.Block & {**session_key, "block_id": 1}) == 1


def test_no_block_ids_is_canonical_and_some_block_ids_is_derivative(landed_session, prefix):
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    subject = "jbcd001"
    naive_dt = datetime.datetime(2027, 5, 5, 9, 0)
    landed_session(subject, naive_dt)
    boundaries = [{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}]
    blocks = [
        {
            "block_id": 1,
            "task_type": "neural",
            "start_s": 0.0,
            "end_s": 4.0,
            "works_block_id": "wb-1",
        },
        {
            "block_id": 2,
            "task_type": "neural",
            "start_s": 4.0,
            "end_s": 8.0,
            "works_block_id": "wb-2",
        },
    ]

    canonical_job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbcd001-k1",
        montage_boundaries=boundaries,
        blocks=blocks,
    )
    derivative_job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbcd001-k2",
        montage_boundaries=boundaries,
        blocks=blocks,
        block_ids=[1, 2],
    )

    canonical_key = accept(canonical_job, prefix=prefix)
    derivative_key = accept(derivative_job, prefix=prefix)

    assert canonical_key["activation_id"] == 0
    assert (schema_request.Activation & canonical_key).fetch1("role") == "canonical"
    assert derivative_key["activation_id"] != 0
    assert (schema_request.Activation & derivative_key).fetch1("role") == "derivative"
    assert len(schema_request.ActivationBlock & derivative_key) == 2


def test_an_existing_montage_and_block_survive_a_request_naming_different_boundaries(
    landed_session, prefix
):
    """Beyond the brief: wl.works owns `Montage`/`Block`. A second request
    carrying different boundaries for a montage or block already on file is
    wl.works correcting its own record, and that correction is their call to
    make explicitly -- it is not this pipeline's to infer from whichever
    payload happened to arrive most recently. So the second request's
    boundaries are silently ignored, not applied.
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
        blocks=[
            {
                "block_id": 1,
                "task_type": "rf_map",
                "start_s": 0.0,
                "end_s": 4.0,
                "works_block_id": "wb-1",
            }
        ],
    )
    accept(first_job, prefix=prefix)

    # A second request, under a DIFFERENT idempotency key (a distinct ask,
    # not a retry of the first), naming DIFFERENT boundaries for the SAME
    # montage_id and block_id.
    correction_job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbkeep1-k2",
        montage_boundaries=[{"montage_id": 0, "start_s": 100.0, "end_s": 200.0}],
        blocks=[
            {
                "block_id": 1,
                "task_type": "attention",
                "start_s": 100.0,
                "end_s": 150.0,
                "works_block_id": "wb-CORRECTED",
            }
        ],
    )
    accept(correction_job, prefix=prefix)

    session_key = {"subject": subject, "session_datetime": naive_dt}
    montage_row = (core.Montage & {**session_key, "montage_id": 0}).fetch1()
    block_row = (core.Block & {**session_key, "block_id": 1}).fetch1()

    assert montage_row["start_s"] == pytest.approx(0.0)
    assert montage_row["end_s"] == pytest.approx(12.0)
    assert block_row["task_type"] == "rf_map"
    assert block_row["start_s"] == pytest.approx(0.0)
    assert block_row["end_s"] == pytest.approx(4.0)
    assert block_row["works_block_id"] == "wb-1"


def test_accept_rejects_a_block_outside_its_montages_window(landed_session, prefix):
    """`ActivationBlock`'s own comment: the responder is this window's first
    writer and owns enforcing it. `submit_derivative` itself accepts a block
    at [20.0, 24.0) against a montage of [0.0, 12.0) -- verified -- so this is
    `accept()`'s own check, not inherited from the schema layer."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    subject = "jbwin01"
    naive_dt = datetime.datetime(2027, 5, 9, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbwin01-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
        blocks=[
            {
                "block_id": 9,
                "task_type": "neural",
                "start_s": 20.0,
                "end_s": 24.0,
                "works_block_id": "wb-9",
            }
        ],
        block_ids=[9],
    )

    with pytest.raises(ValueError, match="block 9"):
        accept(job, prefix=prefix)

    assert len(schema_request.Request & {"idempotency_key": "jbwin01-k1"}) == 0


def test_accept_treats_the_montage_window_as_half_open(landed_session, prefix):
    """[start_s, end_s) -- a block ending exactly at the montage's end is
    still fully covered; a block starting exactly where the montage ends is
    not covered at all. Boundary conditions are exactly where an off-by-one
    in the comparison would hide."""
    from wl_preproc.responder.jobs import accept

    subject = "jbedg01"
    naive_dt = datetime.datetime(2027, 5, 10, 9, 0)
    landed_session(subject, naive_dt)

    ok_job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbedg01-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
        blocks=[
            {
                "block_id": 1,
                "task_type": "neural",
                "start_s": 8.0,
                "end_s": 12.0,
                "works_block_id": "wb-1",
            }
        ],
        block_ids=[1],
    )
    accept(ok_job, prefix=prefix)  # must not raise -- [8, 12) is fully within [0, 12)

    touching_job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbedg01-k2",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
        blocks=[
            {
                "block_id": 2,
                "task_type": "neural",
                "start_s": 12.0,
                "end_s": 16.0,
                "works_block_id": "wb-2",
            }
        ],
        block_ids=[2],
    )
    with pytest.raises(ValueError, match="block 2"):
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
            blocks=[],
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


def test_a_rejected_request_leaves_no_montage_or_block_row_and_a_correction_then_succeeds(
    landed_session, prefix
):
    """C1: validate before writing, not after. The first draft inserted
    Montage/Block and only then checked the window, so a rejected request
    permanently planted the very Block row that caused its own rejection --
    skip_duplicates=True then discarded every later correction, so the
    request that FIXED the boundary was rejected too, citing the stale
    values it refused to replace. Reproduces the reviewer's own two-request
    scenario exactly: block 9 at [20, 24) against montage [0, 12) is
    rejected, and a second request naming block 9 at the CORRECTED [2, 6)
    must not still see the first, rejected [20, 24) permanently on file.
    """
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core
    from wl_preproc.schema import request as schema_request

    subject = "jbres01"
    naive_dt = datetime.datetime(2027, 5, 14, 9, 0)
    landed_session(subject, naive_dt)

    bad_job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbres01-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
        blocks=[
            {
                "block_id": 9,
                "task_type": "GARBAGE",
                "start_s": 20.0,
                "end_s": 24.0,
                "works_block_id": "wb-bad",
            }
        ],
        block_ids=[9],
    )
    with pytest.raises(ValueError, match="block 9"):
        accept(bad_job, prefix=prefix)

    session_key = {"subject": subject, "session_datetime": naive_dt}
    assert len(core.Montage & {**session_key, "montage_id": 0}) == 0, (
        "a rejected request must not plant the Montage row it was rejected over"
    )
    assert len(core.Block & {**session_key, "block_id": 9}) == 0, (
        "a rejected request must not plant the Block row it was rejected over"
    )
    assert len(schema_request.Request & {"idempotency_key": "jbres01-k1"}) == 0

    corrected_job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbres01-k2",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
        blocks=[
            {
                "block_id": 9,
                "task_type": "neural",
                "start_s": 2.0,
                "end_s": 6.0,
                "works_block_id": "wb-good",
            }
        ],
        block_ids=[9],
    )
    key = accept(corrected_job, prefix=prefix)  # must not raise

    block_row = (core.Block & {**session_key, "block_id": 9}).fetch1()
    assert block_row["task_type"] == "neural"
    assert block_row["start_s"] == pytest.approx(2.0)
    assert block_row["end_s"] == pytest.approx(6.0)
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
            blocks=[],
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


def test_accept_rejects_an_out_of_range_montage_id(landed_session, prefix):
    """I2: `core.Montage.montage_id` is a signed `tinyint` (-128..127)."""
    from wl_preproc.responder.jobs import accept

    subject = "jbrng01"
    naive_dt = datetime.datetime(2027, 5, 17, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=99999,
        idempotency_key="jbrng01-k1",
        montage_boundaries=[{"montage_id": 99999, "start_s": 0.0, "end_s": 12.0}],
    )

    with pytest.raises(ValueError, match="montage_id"):
        accept(job, prefix=prefix)


def test_accept_rejects_an_out_of_range_block_id(landed_session, prefix):
    """I2: `core.Block.block_id` is a signed `smallint` (-32768..32767)."""
    from wl_preproc.responder.jobs import accept

    subject = "jbrng02"
    naive_dt = datetime.datetime(2027, 5, 18, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbrng02-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
        blocks=[{"block_id": 999999, "task_type": "neural", "start_s": 0.0, "end_s": 4.0}],
    )

    with pytest.raises(ValueError, match="block_id"):
        accept(job, prefix=prefix)


def test_accept_rejects_an_oversized_task_type(landed_session, prefix):
    """I2: `core.Block.task_type` is `varchar(32)`."""
    from wl_preproc.responder.jobs import accept

    subject = "jbrng03"
    naive_dt = datetime.datetime(2027, 5, 19, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbrng03-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
        blocks=[{"block_id": 1, "task_type": "x" * 33, "start_s": 0.0, "end_s": 4.0}],
    )

    with pytest.raises(ValueError, match="task_type"):
        accept(job, prefix=prefix)


def test_accept_rejects_an_oversized_works_block_id(landed_session, prefix):
    """I2: `core.Block.works_block_id` is `varchar(64)`."""
    from wl_preproc.responder.jobs import accept

    subject = "jbrng04"
    naive_dt = datetime.datetime(2027, 5, 20, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbrng04-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
        blocks=[
            {
                "block_id": 1,
                "task_type": "neural",
                "start_s": 0.0,
                "end_s": 4.0,
                "works_block_id": "x" * 65,
            }
        ],
    )

    with pytest.raises(ValueError, match="works_block_id"):
        accept(job, prefix=prefix)


def test_accept_rejects_a_non_finite_start_s_or_end_s(landed_session, prefix):
    """I2: `start_s`/`end_s` are `double` -- unbounded in magnitude for any
    realistic session-time-seconds value, but a non-finite float (here,
    infinity) or a wrong type is exactly the "syntactically fine Python
    value, wrong for the column" case this whole guard class exists for."""
    from wl_preproc.responder.jobs import accept

    subject = "jbrng05"
    naive_dt = datetime.datetime(2027, 5, 21, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbrng05-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": float("inf")}],
    )

    with pytest.raises(ValueError, match="end_s"):
        accept(job, prefix=prefix)


def test_accept_rejects_an_unknown_block_id(landed_session, prefix):
    """I4: a `block_ids` entry naming no `Block` anywhere -- neither already
    on record nor supplied in this same request's `metadata.blocks` -- is a
    `ValueError`, not silently excluded from the window check nor left to
    surface later as `ActivationBlock`'s own foreign-key error."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    subject = "jbunk01"
    naive_dt = datetime.datetime(2027, 5, 22, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbunk01-k1",
        montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
        block_ids=[7],  # no Block anywhere named 7
    )

    with pytest.raises(ValueError, match="no Block on record"):
        accept(job, prefix=prefix)

    assert len(schema_request.Request & {"idempotency_key": "jbunk01-k1"}) == 0
