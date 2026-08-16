"""The HTTP surface: routing, auth and status codes. Design spec section 4, 9.

Every test here runs against a real `ThreadingHTTPServer` on an ephemeral
port, never a mocked handler -- a mock reproduces "the route dispatched
correctly" and "the header was parsed correctly" by construction, which is
exactly the class of defect this file exists to catch (design spec section
9, task 8's own brief, step 1).

Most tests below wire `make_handler` to plain Python functions for
`health_fn`/`accept_fn` rather than the real `responder.health.build_health`/
`responder.jobs.accept` -- proving directly that `handler.py` imports no
DataJoint and needs no database for routing, auth, body parsing or status
codes (there are more of those cases than there are cases that genuinely
need a schema). The handful of tests that do need one are marked by taking
the `dj_conn`/`prefix` fixtures, exactly as `tests/responder/test_jobs.py`
already does.
"""

from __future__ import annotations

import datetime
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import datajoint as dj
import pymysql.err
import pytest

from wl_preproc.contracts.protocol import Action, HealthResponse, Reading
from wl_preproc.responder.handler import make_handler

TOKEN = "test-bearer-token-1"


# --------------------------------------------------------------------------
# A real server on a real, ephemeral port -- started fresh per test and
# always torn down, so no thread or bound port outlives the test that
# created it.
# --------------------------------------------------------------------------


@pytest.fixture
def start_server():
    started: list[ThreadingHTTPServer] = []

    def _start(token: str, health_fn, accept_fn) -> str:
        handler_cls = make_handler(token, health_fn, accept_fn)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        started.append(httpd)
        port = httpd.server_address[1]
        return f"http://127.0.0.1:{port}"

    yield _start

    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def _unused(*_args, **_kwargs):
    """A callable neither endpoint's routing should ever reach in tests that
    pass this for the OTHER endpoint's `health_fn`/`accept_fn` -- auth and
    routing both resolve before either real callable is ever invoked, so a
    404/405/401 test can safely assert its callable was never touched."""
    raise AssertionError("this endpoint's callable should not have been invoked in this test")


def _request(url: str, *, method: str, token: str | None = None, body=None):
    """`(status, raw_body_bytes)`. `body` may be a dict (JSON-encoded here)
    or raw bytes (sent verbatim, for the deliberately-malformed-JSON cases)."""
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _health_ok() -> HealthResponse:
    return HealthResponse(
        verdict="ok",
        readings=[Reading(key="ingested_24h", label="Ingested (24 h)", value="0", featured=True)],
        actions=[],
    )


def _valid_job_payload() -> dict:
    """A `JobRequest`-shaped dict that clears `do_POST`'s own parsing
    (`json.loads` + `JobRequest.model_validate`) so `accept_fn` genuinely
    gets invoked -- used by tests that care about what `accept_fn` does,
    not about parsing, and that therefore stub `accept_fn` rather than
    touching a real database."""
    return {
        "domain": "neural",
        "selection": {"session_datetime": "2027-06-01T09:00:00+00:00", "montage_id": 0},
        "parameters": {},
        "idempotency_key": "http-probe-key-1",
        "metadata": {
            "blocks": [],
            "montage_boundaries": [],
            "probes": [],
            "experimenter": "jw",
            "subject": "htprobe",
            "task_types": [],
        },
    }


# --------------------------------------------------------------------------
# Auth. Design spec section 4.3, section 9's own named cases.
# --------------------------------------------------------------------------


def test_missing_token_is_401_not_403(start_server):
    base = start_server(TOKEN, _health_ok, _unused)
    status, body = _request(f"{base}/health", method="GET")
    assert status == 401
    assert json.loads(body) == {"error": "unauthorized"}


def test_wrong_token_is_401_with_no_hint_which_part_was_wrong(start_server):
    base = start_server(TOKEN, _health_ok, _unused)
    missing_status, missing_body = _request(f"{base}/health", method="GET")
    wrong_status, wrong_body = _request(f"{base}/health", method="GET", token="not-the-real-token")

    assert wrong_status == 401, "wrong token must still be 401, not 403"
    # Byte-identical to the missing-token response: nothing in the status or
    # the body distinguishes "you sent nothing" from "you sent the wrong
    # thing" -- design spec section 4.3 and section 9's own named test.
    assert (wrong_status, wrong_body) == (missing_status, missing_body)


def test_a_malformed_scheme_is_also_401_with_the_same_body(start_server):
    """`Authorization: <token>` with no `Bearer ` scheme at all -- still
    unauthorized, still 401, still the identical generic body as a missing
    header entirely."""
    base = start_server(TOKEN, _health_ok, _unused)
    req = urllib.request.Request(f"{base}/health", headers={"Authorization": TOKEN})
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)
    assert exc_info.value.code == 401
    assert json.loads(exc_info.value.read()) == {"error": "unauthorized"}


def test_auth_is_required_on_health_too(start_server):
    """Design spec section 4.3: "The health endpoint is included not
    because its readings are sensitive but because one rule is easier to
    hold than two." """
    base = start_server(TOKEN, _health_ok, _unused)
    status, _ = _request(f"{base}/health", method="GET")
    assert status == 401


def test_a_valid_token_is_accepted(start_server):
    base = start_server(TOKEN, _health_ok, _unused)
    status, _ = _request(f"{base}/health", method="GET", token=TOKEN)
    assert status == 200


# --------------------------------------------------------------------------
# Routing. Design spec section 4.2's table: "Anything else is 404."
# --------------------------------------------------------------------------


def test_unknown_path_is_404(start_server):
    base = start_server(TOKEN, _health_ok, _unused)
    status, body = _request(f"{base}/nope", method="GET", token=TOKEN)
    assert status == 404
    assert json.loads(body) == {"error": "not found"}


def test_get_jobs_is_405(start_server):
    base = start_server(TOKEN, _health_ok, _unused)
    status, body = _request(f"{base}/jobs", method="GET", token=TOKEN)
    assert status == 405
    assert json.loads(body) == {"error": "method not allowed"}


def test_post_health_is_405(start_server):
    """Beyond the brief's own named case (`GET /jobs`), but the identical
    rule: design spec section 4.2's table names exactly one verb per path,
    so `POST /health` is exactly as much "a real path, wrong verb" as
    `GET /jobs` is."""
    base = start_server(TOKEN, _health_ok, _unused)
    status, body = _request(f"{base}/health", method="POST", token=TOKEN, body={})
    assert status == 405
    assert json.loads(body) == {"error": "method not allowed"}


# --------------------------------------------------------------------------
# GET /health -- the success path and its own defensive failure path.
# --------------------------------------------------------------------------


def test_a_valid_health_request_returns_200_with_the_response_shape(start_server):
    response = HealthResponse(
        verdict="degraded",
        readings=[
            Reading(key="stuck_jobs", label="Stuck jobs", value="3 stale reservation(s)", featured=True)
        ],
        actions=[Action(name="neural", label="Spike sorting")],
    )
    base = start_server(TOKEN, lambda: response, _unused)

    status, body = _request(f"{base}/health", method="GET", token=TOKEN)

    assert status == 200
    # model_dump_json() is HealthResponse's own canonical wire form --
    # comparing against it directly (rather than reconstructing the dict
    # by hand) is the same check as "this round-trips through the real
    # contract", not a paraphrase of it.
    assert json.loads(body) == json.loads(response.model_dump_json())


def test_health_fns_exception_is_500_not_a_crash(start_server):
    """`build_health` is documented to never raise for a database fault --
    it returns a `down` verdict instead. This handler does not trust that
    invariant blindly: "nothing may return a traceback under any input" is
    unconditional, so even a `health_fn` that DOES raise must still fail
    safely here."""

    def health_fn():
        raise RuntimeError("boom")

    base = start_server(TOKEN, health_fn, _unused)
    status, body = _request(f"{base}/health", method="GET", token=TOKEN)
    assert status == 500
    text = body.decode("utf-8")
    assert "Traceback" not in text
    assert json.loads(body)["error"] == "RuntimeError: boom"


# --------------------------------------------------------------------------
# POST /jobs -- malformed bodies. All 422, all "never a traceback"
# (design spec section 4.2).
# --------------------------------------------------------------------------


def test_valid_token_malformed_json_body_is_422_never_a_traceback(start_server):
    base = start_server(TOKEN, _health_ok, _unused)
    status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=b"{not valid json at all")
    assert status == 422
    text = body.decode("utf-8")
    assert "Traceback" not in text
    assert "traceback" not in text.lower()
    assert "error" in json.loads(body)


def test_valid_token_body_failing_pydantic_validation_is_422_with_the_pydantic_error(start_server):
    """Design spec section 4.2: "A malformed body is 422 with the pydantic
    error, never a traceback." -- checked here as the actual structured
    `.errors()` shape, not merely a generic message."""
    base = start_server(TOKEN, _health_ok, _unused)
    status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body={"not": "a job request"})
    assert status == 422
    parsed = json.loads(body)
    assert parsed["error"] == "invalid request body"
    assert isinstance(parsed["detail"], list) and parsed["detail"]
    # pydantic v2's own error shape -- "domain" is JobRequest's first
    # required field, and an entirely wrong-shaped body is missing all of
    # them.
    assert any(entry["loc"] == ["domain"] for entry in parsed["detail"])


def test_empty_body_is_422(start_server):
    base = start_server(TOKEN, _health_ok, _unused)
    status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=b"")
    assert status == 422
    assert "Traceback" not in body.decode("utf-8")


def test_non_utf8_body_is_422(start_server):
    base = start_server(TOKEN, _health_ok, _unused)
    status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=b"\xff\xfe\x00\x01")
    assert status == 422
    assert "Traceback" not in body.decode("utf-8", errors="replace")


def test_accept_fns_value_error_is_422_with_its_message(start_server):
    """`responder/jobs.py::accept`'s own documented contract: "Raises
    ValueError for every rejection it owns." A well-formed body that
    `accept()` itself refuses gets the same 422 as one pydantic refuses."""

    def accept_fn(request):
        raise ValueError("selection is missing required key(s) ['montage_id']")

    base = start_server(TOKEN, _health_ok, accept_fn)
    status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=_valid_job_payload())
    assert status == 422
    assert json.loads(body) == {"error": "selection is missing required key(s) ['montage_id']"}


# --------------------------------------------------------------------------
# POST /jobs -- everything past accept()'s own ValueError contract. All
# 500, all handled, none a traceback. Task 8's own dispatch: map every
# exception accept() can raise, including the ones the brief's headline
# 401/404/405/422/200 list does not enumerate.
# --------------------------------------------------------------------------


def test_a_datajoint_error_from_accept_fn_is_500_not_a_crash(start_server):
    """`schema/request.py::_reject_key_reuse` raises `dj.DataJointError`,
    not `ValueError`, for a reused idempotency key naming materially
    different content -- a real, reachable path through `accept()`, not a
    contrived one. `handler.py` imports no DataJoint (module docstring) so
    it cannot -- and does not -- special-case this by type; it falls into
    the same generic backstop as every other non-ValueError failure."""

    def accept_fn(request):
        raise dj.DataJointError(
            "idempotency key 'x' is already recorded against a different request"
        )

    base = start_server(TOKEN, _health_ok, accept_fn)
    status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=_valid_job_payload())
    assert status == 500
    text = body.decode("utf-8")
    assert "Traceback" not in text
    assert json.loads(body)["error"].startswith("DataJointError:")


def test_a_raw_pymysql_data_error_from_accept_fn_is_500_not_a_crash(start_server):
    """Task 8's own dispatch, verbatim: `pymysql.err.DataError` is NOT a
    `DataJointError` -- DataJoint's MySQL adapter passes 1264/1265/1406
    through untranslated (confirmed at source by Task 7's review, and
    directly here: `pymysql.err.DataError.__mro__` shares no class with
    `dj.DataJointError.__mro__` but `Exception`). `responder/jobs.py`
    guards every column it writes before insert specifically so this
    should never fire for real -- but "should not" is not "cannot", and
    this proves the HANDLER's own mapping, independent of whether every one
    of jobs.py's guards holds up over time, turns it into a HANDLED 500
    rather than an unhandled one with a real traceback.
    """
    assert not set(pymysql.err.DataError.__mro__) & set(dj.DataJointError.__mro__) - {Exception, BaseException, object}

    def accept_fn(request):
        raise pymysql.err.DataError(1406, "Data too long for column 'task_type' at row 1")

    base = start_server(TOKEN, _health_ok, accept_fn)
    status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=_valid_job_payload())
    assert status == 500
    text = body.decode("utf-8")
    assert "Traceback" not in text
    assert "DataError" in json.loads(body)["error"]


def test_an_unexpected_bug_from_accept_fn_is_500_not_a_crash(start_server):
    """The blanket backstop, proven against a type that is neither
    `ValueError` nor any database exception at all -- a genuine bug, the
    class of failure nobody explicitly enumerated."""

    def accept_fn(request):
        raise AttributeError("'NoneType' object has no attribute 'x'")

    base = start_server(TOKEN, _health_ok, accept_fn)
    status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=_valid_job_payload())
    assert status == 500
    assert "Traceback" not in body.decode("utf-8")
    assert json.loads(body)["error"].startswith("AttributeError:")


def test_nothing_ever_returns_a_traceback_regardless_of_input(start_server):
    """The project's own blanket rule ("nothing may return a traceback to
    the client under any input"), checked directly against a sweep of
    inputs designed to hit every branch in `handler.py`, rather than
    trusted from the individual tests above in isolation. `accept_fn`
    itself raises an exception whose OWN message contains text that would
    look like part of a traceback, to make sure nothing downstream
    re-embeds it in a way that could be mistaken for one.
    """

    def accept_fn(request):
        raise RuntimeError('nested failure: File "/x.py", line 1, in <module>')

    base = start_server(TOKEN, _health_ok, accept_fn)
    probes = [
        (f"{base}/health", "GET", None, None),
        (f"{base}/health", "GET", "wrong-token", None),
        (f"{base}/nope", "GET", TOKEN, None),
        (f"{base}/jobs", "GET", TOKEN, None),
        (f"{base}/jobs", "POST", TOKEN, b"not json"),
        (f"{base}/jobs", "POST", TOKEN, {"bad": "shape"}),
        (f"{base}/jobs", "POST", TOKEN, _valid_job_payload()),
    ]
    for url, method, token, body in probes:
        status, raw = _request(url, method=method, token=token, body=body)
        text = raw.decode("utf-8", errors="replace")
        assert "Traceback (most recent call last)" not in text, (url, method, status, text)
        json.loads(raw)  # every body, success or failure, is valid JSON


# --------------------------------------------------------------------------
# The real path: a live schema, a live jobs.accept, a live 200. Needs
# Docker (dj_conn/prefix, tests/conftest.py).
# --------------------------------------------------------------------------


@pytest.fixture
def landed_session(dj_conn, prefix):
    """A `(subject, session_datetime)` with Lab/Subject/Session already on
    file -- the precondition `accept()` itself assumes (see
    `tests/responder/test_jobs.py`'s fixture of the same name and shape;
    this is a local copy since pytest fixtures do not cross test modules
    without living in a shared `conftest.py`, and duplicating four lines
    here was judged cheaper than relocating a fixture Task 7 did not need
    to share)."""
    from wl_preproc.schema import pipeline
    from wl_preproc.schema import request as schema_request

    schema_request.activate(prefix=prefix)

    def _land(subject: str, session_datetime: datetime.datetime) -> None:
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
        pipeline.Session.insert1(
            {"subject": subject, "session_datetime": session_datetime}, skip_duplicates=True
        )

    return _land


def _real_job_payload(*, subject: str, session_datetime_iso: str, idempotency_key: str) -> dict:
    return {
        "domain": "neural",
        "selection": {"session_datetime": session_datetime_iso, "montage_id": 0},
        "parameters": {},
        "idempotency_key": idempotency_key,
        "metadata": {
            "blocks": [],
            "montage_boundaries": [{"montage_id": 0, "start_s": 0.0, "end_s": 12.0}],
            "probes": [],
            "experimenter": "jw",
            "subject": subject,
            "task_types": [],
        },
    }


def test_a_valid_job_request_is_accepted_and_returns_the_activation_key(
    start_server, landed_session, prefix
):
    """Task 8's own brief, verbatim: "a valid request -> 200 with the
    activation key." Exercises the true wire format end to end -- a real
    HTTP POST carrying `session_datetime` as a JSON STRING, which is what
    `responder/jobs.py`'s own C2 fix (coercing it via `fromisoformat`
    rather than assuming a live `datetime.datetime`) exists for. Every
    other `accept()` test in this phase, including all of
    `tests/responder/test_jobs.py`, constructs a `JobRequest` directly in
    Python with a real `datetime.datetime` object already inside it -- this
    is the first test anywhere in the phase that goes through `json.dumps`
    first, which is what real wl.works traffic actually does.
    """
    from wl_preproc.responder import jobs as jobs_module

    subject = "htpjob1"
    naive_dt = datetime.datetime(2027, 6, 1, 9, 0)
    landed_session(subject, naive_dt)

    def accept_fn(request):
        return jobs_module.accept(request, prefix=prefix)

    base = start_server(TOKEN, _health_ok, accept_fn)
    payload = _real_job_payload(
        subject=subject,
        session_datetime_iso=naive_dt.replace(tzinfo=datetime.UTC).isoformat(),
        idempotency_key="htpjob1-k1",
    )

    status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=payload)

    assert status == 200
    parsed = json.loads(body)
    assert parsed["accepted"] is True
    assert parsed["activation"]["subject"] == subject
    assert parsed["activation"]["montage_id"] == 0
    assert parsed["activation"]["activation_id"] == 0
    # session_datetime round-trips through _json_default's isoformat() --
    # proof the datetime.datetime Activation's primary key actually carries
    # made it through json.dumps at all, rather than crashing _send_json.
    assert parsed["activation"]["session_datetime"].startswith("2027-06-01T09:00:00")


def test_the_same_request_posted_twice_over_http_returns_the_same_activation(
    start_server, landed_session, prefix
):
    """Idempotency proven over the real wire format, not only at
    `jobs.accept`'s own Python-level boundary (`tests/responder/
    test_jobs.py::test_accept_is_idempotent_on_the_same_key`)."""
    from wl_preproc.responder import jobs as jobs_module

    subject = "htpjob2"
    naive_dt = datetime.datetime(2027, 6, 2, 9, 0)
    landed_session(subject, naive_dt)

    def accept_fn(request):
        return jobs_module.accept(request, prefix=prefix)

    base = start_server(TOKEN, _health_ok, accept_fn)
    payload = _real_job_payload(
        subject=subject,
        session_datetime_iso=naive_dt.replace(tzinfo=datetime.UTC).isoformat(),
        idempotency_key="htpjob2-k1",
    )

    first_status, first_body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=payload)
    second_status, second_body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=payload)

    assert (first_status, second_status) == (200, 200)
    assert json.loads(first_body) == json.loads(second_body)


# --------------------------------------------------------------------------
# serve() itself: the real lock, the real socket, wired to a real schema.
# --------------------------------------------------------------------------


def test_serve_wires_health_through_a_real_lock_on_a_real_server(monkeypatch, dj_conn, prefix, tmp_path):
    """`make_handler` above is exercised directly, with fakes, for routing
    and status codes -- this is the one test of `server.py::serve` itself:
    that it builds real, lock-wrapped callables from `root`/`prefix`, binds
    a real socket, signals `ready`, and answers a real request correctly.

    `ThreadingHTTPServer` is monkeypatched to a subclass that captures the
    constructed instance, purely so this test can call `httpd.shutdown()`
    cleanly afterward -- `serve()`'s own public contract (`port`, `token`,
    `root`, `prefix`, `ready`) returns `None` and hands back no server
    object, which is correct for its real caller (Task 9's CLI, which runs
    it in the foreground forever) but leaves a test needing some other way
    to stop the accept loop instead of leaking a bound port and a thread
    for the rest of the suite.
    """
    import wl_preproc.responder.server as server_module

    captured: dict = {}
    real_cls = server_module.ThreadingHTTPServer

    class _CapturingServer(real_cls):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captured["httpd"] = self

    monkeypatch.setattr(server_module, "ThreadingHTTPServer", _CapturingServer)

    root = tmp_path / "scratch"
    root.mkdir()
    ready = threading.Event()
    thread = threading.Thread(
        target=server_module.serve,
        args=(0, "serve-token", root, prefix),
        kwargs={"ready": ready},
        daemon=True,
    )
    thread.start()
    try:
        assert ready.wait(timeout=5), "serve() never signalled ready"
        httpd = captured["httpd"]
        port = httpd.server_address[1]

        status, body = _request(f"http://127.0.0.1:{port}/health", method="GET", token="serve-token")
        assert status == 200
        assert json.loads(body)["verdict"] in ("ok", "degraded", "down")

        # The real, fully-wired path still enforces auth -- not just the
        # make_handler-direct tests above.
        status, _ = _request(f"http://127.0.0.1:{port}/health", method="GET", token="wrong")
        assert status == 401
    finally:
        captured["httpd"].shutdown()
        thread.join(timeout=5)
