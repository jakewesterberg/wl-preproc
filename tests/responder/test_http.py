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

**Review round 1 (2026-08-16)** found one Critical and six Important issues,
all addressed here alongside the fixes in `handler.py`/`server.py`. Each new
test below names which finding it covers.
"""

from __future__ import annotations

import datetime
import hmac
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import datajoint as dj
import pymysql.err
import pytest

from wl_preproc.contracts.protocol import Action, HealthResponse, Reading
from wl_preproc.responder.handler import ConflictError, make_handler

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


def _raw_http_response(port: int, request_bytes: bytes, *, timeout: float = 3.0) -> bytes:
    """Send `request_bytes` verbatim over a real TCP socket to
    `127.0.0.1:port` and return whatever comes back within `timeout`
    seconds -- possibly nothing, if the server hangs (review Important 4's
    pre-fix behaviour for a malformed `Content-Length`). `timeout` bounds
    that at 3 s per call rather than truly forever, so a regression here
    fails a test rather than hanging the suite.

    Used instead of `urllib.request` for the `Content-Length` probes below
    because `urllib`/`http.client` compute their OWN `Content-Length` from
    the body they send and give a caller no way to lie about it -- these
    tests need to send a header value the client itself would never
    construct, exactly as a malformed or malicious peer could.
    """
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.sendall(request_bytes)
        sock.settimeout(timeout)
        chunks = []
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except OSError:
            pass  # timed out or reset -- return whatever arrived, even nothing
        return b"".join(chunks)


def _raw_post_with_content_length(port: int, token: str, content_length: str) -> bytes:
    # Deliberately shorter than 12 bytes: "1_2" misparses to the integer 12
    # (see the test below), and a body with 12+ bytes ALREADY available lets
    # a bare `read(12)` return immediately with a truncated-but-present
    # prefix rather than genuinely blocking for bytes that never arrive --
    # which would make that one case "pass" for the wrong reason (a JSON
    # parse failure on truncated input) regardless of whether the
    # Content-Length validation this test targets exists at all. A short
    # body makes every one of the four cases below fail for the reason its
    # own docstring actually describes.
    body = b"{}"
    request = (
        f"POST /jobs HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"Authorization: Bearer {token}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {content_length}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode("utf-8") + body
    return _raw_http_response(port, request)


def _status_line(raw_response: bytes) -> str:
    return raw_response.split(b"\r\n", 1)[0].decode("ascii", errors="replace")


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


def test_a_lowercase_bearer_scheme_still_authenticates(start_server):
    """Review Minor: RFC 7235 section 2.1 defines `auth-scheme` as a
    case-insensitive token, so `Authorization: bearer <token>` (lowercase)
    must authenticate exactly as `Bearer` does, not be treated as a
    malformed scheme and refused."""
    base = start_server(TOKEN, _health_ok, _unused)
    req = urllib.request.Request(f"{base}/health", headers={"Authorization": f"bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200


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


def test_a_non_ascii_candidate_token_is_401_not_a_crash(start_server):
    """Review Critical. `hmac.compare_digest` raises `TypeError: comparing
    strings with non-ASCII characters is not supported` for either `str`
    operand containing one, and `_authorized()` used to hand it two bare
    `str` values -- called at the very first line of `do_GET`/`do_POST`,
    OUTSIDE every `try` in `handler.py`, so that `TypeError` escaped
    uncaught: no HTTP response at all, just a closed socket (confirmed
    directly before the fix -- `_request` below raised
    `http.client.RemoteDisconnected` rather than returning a status). Both
    sides are now encoded to UTF-8 bytes before comparison, which has no
    such restriction. A CANDIDATE (attacker- or typo-supplied) non-ASCII
    token specifically -- the CONFIGURED-token direction is
    `test_serve_refuses_to_start_on_a_non_ascii_token` below.
    """
    base = start_server(TOKEN, _health_ok, _unused)
    status, body = _request(f"{base}/health", method="GET", token="café-töken")
    assert status == 401
    assert json.loads(body) == {"error": "unauthorized"}


def test_auth_uses_hmac_compare_digest_not_plain_equality(start_server, monkeypatch):
    """Review Important 6: replacing `hmac.compare_digest` with `==` passes
    every OTHER test in this file unchanged -- neither missing- nor
    wrong-token behaviour differs OBSERVABLY at the HTTP layer between the
    two, so only spying on the call itself can tell them apart. Design spec
    section 4.3 names `compare_digest` specifically, and `handler.py`'s own
    module docstring spends a paragraph on why `==` would be a timing side
    channel -- this is what actually pins that choice rather than merely
    documenting it.
    """
    calls = []
    real_compare_digest = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real_compare_digest(a, b)

    monkeypatch.setattr("hmac.compare_digest", spy)

    base = start_server(TOKEN, _health_ok, _unused)
    status, _ = _request(f"{base}/health", method="GET", token=TOKEN)

    assert status == 200
    assert len(calls) == 1, "hmac.compare_digest must be called exactly once per authorized request"
    assert calls[0] == (TOKEN.encode("utf-8"), TOKEN.encode("utf-8"))


def test_auth_resolves_before_routing_and_before_reading_the_body(start_server):
    """Review Important 6, second half: swapping the order (route/parse the
    body first, authenticate second) passes every OTHER test in this file
    unchanged -- each one only ever names a real, correctly-methoded path
    with a well-formed-or-absent body, so only a request combining bad auth
    with bad routing/a bad body can tell the two orderings apart. This is
    the property the coordinator's own review called out as its single
    largest severity reduction: an unauthenticated caller must learn
    nothing about which paths exist, which methods they answer, or cause
    this host to spend any effort on a body it was never entitled to send.
    """
    base = start_server(TOKEN, _health_ok, _unused)

    # No token, path does not exist: still 401, not 404.
    status, body = _request(f"{base}/does-not-exist", method="GET")
    assert status == 401
    assert json.loads(body) == {"error": "unauthorized"}

    # No token, path exists but wrong verb: still 401, not 405.
    status, body = _request(f"{base}/jobs", method="GET")
    assert status == 401
    assert json.loads(body) == {"error": "unauthorized"}

    # No token, right path and verb, but a deliberately malformed body:
    # still 401, not 422 -- the body must never be read before auth passes.
    status, body = _request(f"{base}/jobs", method="POST", body=b"{not json at all")
    assert status == 401
    assert json.loads(body) == {"error": "unauthorized"}


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


@pytest.mark.parametrize("method", ["PUT", "DELETE", "HEAD", "OPTIONS", "PATCH", "TRACE"])
def test_unsupported_verbs_authenticate_first_and_answer_in_json(start_server, method):
    """Review Important 5. `BaseHTTPRequestHandler`'s own default handling
    for a `do_*` method this class does not define runs BEFORE any
    subclass code at all, answering UNAUTHENTICATED with a non-JSON HTML
    error page that discloses this host's exact interpreter patch version
    -- see `test_the_server_header_never_discloses_the_python_version`
    below for that half. All six of these verbs are now explicitly handled,
    routed through this module's own auth-then-route logic exactly like
    `GET`/`POST`.

    `http.client.HTTPResponse` forces a HEAD response's body length to `0`
    regardless of what the server actually sent (confirmed directly against
    this project's installed Python) -- `resp.read()` on a HEAD response is
    therefore never a reliable way to see the real body, so only status
    codes are asserted for that one case.
    """
    base = start_server(TOKEN, _health_ok, _unused)

    status, body = _request(f"{base}/health", method=method)
    assert status == 401, f"{method} with no token: expected 401, got {status}"
    if method != "HEAD":
        assert json.loads(body) == {"error": "unauthorized"}

    status, body = _request(f"{base}/health", method=method, token=TOKEN)
    assert status in (404, 405), f"{method} with a valid token: expected 404/405, got {status}"
    if method != "HEAD":
        assert json.loads(body)["error"] in ("not found", "method not allowed")


def test_the_server_header_never_discloses_the_python_version(start_server):
    """Review Important 5, second half. `BaseHTTPRequestHandler.
    version_string()` joins `server_version`/`sys_version` into the
    `Server` response header sent on EVERY response -- 401s included, since
    `send_response` calls it unconditionally before this module's own code
    ever runs. Both are now emptied on `ResponderHandler`.
    """
    base = start_server(TOKEN, _health_ok, _unused)

    req_no_token = urllib.request.Request(f"{base}/health", method="GET")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_no_token, timeout=5)
    header = exc_info.value.headers.get("Server", "")
    assert "Python" not in header, f"the 401's Server header leaks version info: {header!r}"

    req_ok = urllib.request.Request(f"{base}/health", headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req_ok, timeout=5) as resp:
        header = resp.headers.get("Server", "")
    assert "Python" not in header, f"the 200's Server header leaks version info: {header!r}"


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


def test_a_health_response_that_fails_to_serialise_is_500_not_a_crash(start_server):
    """Review Minor: `response.model_dump_json()` used to run AFTER
    `do_GET`'s own `try`/`except`, so a hypothetical serialisation failure
    there would have escaped this module's own "no traceback under any
    input" backstop one line after the comment stating it -- trusting an
    invariant ("a `HealthResponse` always serialises cleanly") the
    surrounding code otherwise declines to trust for anything else. A fake
    object standing in for `HealthResponse`, whose `model_dump_json()`
    itself raises, proves the call is now actually inside the guarded
    section rather than merely near it.
    """

    class _BrokenResponse:
        def model_dump_json(self):
            raise RuntimeError("serialisation exploded")

    base = start_server(TOKEN, lambda: _BrokenResponse(), _unused)
    status, body = _request(f"{base}/health", method="GET", token=TOKEN)
    assert status == 500
    assert "Traceback" not in body.decode("utf-8")
    assert json.loads(body)["error"] == "RuntimeError: serialisation exploded"


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


@pytest.mark.parametrize(
    "content_length",
    ["-1", "1_2", "8000000000", "100000000000000000000"],
    ids=["negative", "underscore", "huge-in-range", "past-sys-maxsize"],
)
def test_a_malformed_or_oversized_content_length_is_422_not_a_hang(start_server, content_length):
    """Review Important 4. Each of these four `Content-Length` values used
    to hang the request thread forever instead of answering at all -- never
    reaching any of this module's status-code handling:

    - `-1`: `int("-1")` succeeds, and `self.rfile.read(-1)` reads until the
      PEER closes the connection, which a live client never does on its own.
    - `1_2`: `int("1_2") == 12` (Python's underscore-in-int-literal grammar
      applies to `int(str)` too), silently reinterpreting a header that is
      not valid HTTP (`Content-Length = 1*DIGIT`, no underscore) as "read 12
      bytes" of an actually-shorter body -- `read()` blocks waiting for
      bytes that are never coming.
    - `8000000000` (8 GB, in-range for a Python int): `self.rfile.read(...)`
      tries to buffer that many bytes.
    - `100000000000000000000` (10**20, past `sys.maxsize`): `read(...)`
      converts its argument to a C `Py_ssize_t` internally and raises
      `OverflowError`.

    All four now get a fast, clean `422` instead -- `str.isdigit()` rejects
    the first two before `int()` is ever called (a leading `-` and an
    underscore are both not digits), and a length cap rejects the last two
    before `self.rfile.read(...)` is ever called with an oversized value at
    all, regardless of whether that value would have hung, raised, or
    merely consumed unreasonable memory.
    """
    base = start_server(TOKEN, _health_ok, _unused)
    port = int(base.rsplit(":", 1)[1])

    raw = _raw_post_with_content_length(port, TOKEN, content_length)

    assert raw, f"Content-Length {content_length!r} got no response at all -- looks like a hang"
    status_line = _status_line(raw)
    assert " 422 " in status_line, f"Content-Length {content_length!r}: expected 422, got {status_line!r}"
    assert b"Traceback" not in raw


# --------------------------------------------------------------------------
# POST /jobs -- everything past accept()'s own ValueError contract:
# ConflictError -> 409 (review Important 3), and 500 for everything else,
# all handled, none a traceback.
# --------------------------------------------------------------------------


def test_a_conflict_error_from_accept_fn_is_409(start_server):
    """`handler.py`'s OWN mapping of its own `ConflictError` type -- no
    `server.py`, no DataJoint, no database. See
    `test_translate_accept_errors_turns_a_datajoint_error_into_a_conflict_error`
    and `test_a_real_key_reuse_conflict_through_the_translation_seam_is_409`
    below for the seam that actually PRODUCES a `ConflictError` from a real
    `dj.DataJointError` in production.
    """

    def accept_fn(request):
        raise ConflictError("idempotency key 'x' is already recorded against a different request")

    base = start_server(TOKEN, _health_ok, accept_fn)
    status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=_valid_job_payload())
    assert status == 409
    assert json.loads(body) == {
        "error": "idempotency key 'x' is already recorded against a different request"
    }


def test_translate_accept_errors_turns_a_datajoint_error_into_a_conflict_error(monkeypatch, prefix):
    """`server.py`'s own translation seam, tested directly -- no lock, no
    thread, no HTTP, no real database. Review Important 3's fix: this is
    the "four lines in the module that is permitted to import
    DataJoint" the review pointed at.
    """
    import wl_preproc.responder.server as server_module

    def boom(request, prefix=None):
        raise dj.DataJointError("idempotency key 'x' is already recorded against a different request")

    monkeypatch.setattr("wl_preproc.responder.jobs.accept", boom)

    with pytest.raises(ConflictError, match="already recorded"):
        server_module._translate_accept_errors(object(), prefix=prefix)


def test_a_datajoint_error_from_accept_fn_bypassing_translation_is_still_a_safe_500(start_server):
    """Defense in depth, not the documented mapping: `handler.py` imports
    no DataJoint, so it cannot recognise a raw `dj.DataJointError` by name
    at all -- `server.py`'s translation seam is what turns it into
    `ConflictError` in production (`serve()` always wires `accept_fn`
    through `_translate_accept_errors`), and this test deliberately
    constructs the one shape that bypasses that seam, to prove the outcome
    is still safe (a handled `500`, not a crash) rather than undefined.
    See `test_a_conflict_error_from_accept_fn_is_409` for the actual `409`
    mapping and `test_a_real_key_reuse_conflict_through_the_translation_
    seam_is_409` for the real, translated, end-to-end path.
    """

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
        (f"{base}/health", "GET", "café-töken", None),  # review Critical
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


def test_a_real_key_reuse_conflict_through_the_translation_seam_is_409(
    start_server, landed_session, prefix
):
    """End-to-end proof of review Important 3's fix: a REAL
    `dj.DataJointError` from `schema/request.py::_reject_key_reuse`,
    through `server.py`'s real `_translate_accept_errors`, mapped by
    `handler.py` to `409` -- the actual production seam, not the fake
    `accept_fn` in `test_a_conflict_error_from_accept_fn_is_409` above.
    """
    import wl_preproc.responder.server as server_module

    subject = "htpconf1"
    naive_dt = datetime.datetime(2027, 6, 3, 9, 0)
    landed_session(subject, naive_dt)

    def accept_fn(request):
        return server_module._translate_accept_errors(request, prefix=prefix)

    base = start_server(TOKEN, _health_ok, accept_fn)
    payload_a = _real_job_payload(
        subject=subject,
        session_datetime_iso=naive_dt.replace(tzinfo=datetime.UTC).isoformat(),
        idempotency_key="htpconf1-k1",
    )
    # Same idempotency_key, a materially different domain -> task_type:
    # _reject_key_reuse compares (task_type, origin, payload, requested_by)
    # against what this key already produced, and origin is always
    # "wl_works" (accept() hardcodes it), so varying domain is enough to
    # trip it without needing a second subject or session.
    payload_b = dict(payload_a, domain="a-materially-different-domain")

    first_status, _ = _request(f"{base}/jobs", method="POST", token=TOKEN, body=payload_a)
    second_status, second_body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=payload_b)

    assert first_status == 200
    assert second_status == 409
    assert "error" in json.loads(second_body)


# --------------------------------------------------------------------------
# The lock. Review Important 2: the prior `serve()` test below proves
# end-to-end wiring but passes even with the lock deleted entirely, or with
# two SEPARATE locks instead of one shared -- its assertions never look at
# the lock itself. These two tests check the actual structural claims
# design spec section 4.4 makes.
# --------------------------------------------------------------------------


def test_the_overlap_probe_itself_can_detect_concurrency_with_no_lock():
    """The control for the lock-serialisation proof below: without this, a
    lock-wrapped test showing "no overlap observed" could just as easily
    mean the probe is incapable of detecting overlap at all (mistaking, say,
    the GIL for something that already serialises this) rather than meaning
    the lock is doing real work. `time.sleep()` releases the GIL, so
    unsynchronised threads reliably DO overlap -- proven here with no lock,
    no server, no database at all.
    """
    state = {"current": 0, "max_concurrent": 0}
    state_lock = threading.Lock()  # protects the counters, not the thing under test

    def slow():
        with state_lock:
            state["current"] += 1
            state["max_concurrent"] = max(state["max_concurrent"], state["current"])
        time.sleep(0.05)
        with state_lock:
            state["current"] -= 1

    threads = [threading.Thread(target=slow) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert state["max_concurrent"] > 1, (
        "the overlap probe itself never detected any concurrency -- "
        "the methodology below cannot be trusted"
    )


def test_serves_lock_prevents_any_overlap_between_concurrent_accept_calls(monkeypatch, tmp_path, prefix):
    """The actual claim design spec section 4.4 makes -- ONE lock
    serialises every call through it -- checked with real concurrent
    threads calling `serve()`'s real `locked_accept_fn`, using the same
    overlap-detection method the control test above just proved can detect
    concurrency when it is genuinely present. `ThreadingHTTPServer` is
    monkeypatched to a fake that never binds a socket or calls
    `serve_forever`, so this needs no real network I/O at all -- only
    `serve()`'s own closure-building logic, which is the part under test.
    """
    import wl_preproc.responder.server as server_module

    captured: dict = {}

    class _FakeServer:
        def __init__(self, address, handler_cls):
            captured["handler_cls"] = handler_cls

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr(server_module, "ThreadingHTTPServer", _FakeServer)

    state = {"current": 0, "max_concurrent": 0}
    state_lock = threading.Lock()

    def slow_accept(request, prefix=None):
        with state_lock:
            state["current"] += 1
            state["max_concurrent"] = max(state["max_concurrent"], state["current"])
        time.sleep(0.05)
        with state_lock:
            state["current"] -= 1
        return {}

    monkeypatch.setattr("wl_preproc.responder.jobs.accept", slow_accept)

    root = tmp_path / "scratch"
    root.mkdir()
    server_module.serve(0, "tok", root, prefix)  # returns at once -- serve_forever is a no-op
    accept_fn = captured["handler_cls"]._accept_fn

    threads = [threading.Thread(target=accept_fn, args=(object(),)) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert state["max_concurrent"] == 1, (
        f"the lock failed to serialise concurrent calls: max concurrent = {state['max_concurrent']}"
    )


def test_serve_shares_one_lock_held_for_the_whole_call_and_released_on_error(monkeypatch, tmp_path, prefix):
    """Single-threaded, no HTTP, no real concurrency needed: proves the
    STRUCTURE the concurrency proof above cannot, by itself, distinguish
    from "got lucky" -- that `health_fn`/`accept_fn` close over the SAME
    `Lock` object (not two separate ones, which would pass the concurrency
    proof above for accept-vs-accept overlap while leaving health-vs-accept
    completely unserialised), that it is genuinely HELD (not merely
    constructed) for the whole duration of the wrapped call, and that an
    exception from the wrapped call still RELEASES it rather than leaking
    it held forever -- a lock leaked by an exception wedges every later
    request on this responder permanently, and the only thing preventing
    that is `with`.
    """
    import wl_preproc.responder.server as server_module

    captured: dict = {}

    class _FakeServer:
        def __init__(self, address, handler_cls):
            captured["handler_cls"] = handler_cls

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr(server_module, "ThreadingHTTPServer", _FakeServer)

    root = tmp_path / "scratch"
    root.mkdir()
    server_module.serve(0, "tok", root, prefix)
    handler_cls = captured["handler_cls"]
    health_fn = handler_cls._health_fn
    accept_fn = handler_cls._accept_fn

    lock_type = type(threading.Lock())

    def _closed_over_lock(fn):
        cells = fn.__closure__ or ()
        locks = [c.cell_contents for c in cells if isinstance(c.cell_contents, lock_type)]
        assert len(locks) == 1, f"expected exactly one Lock in {fn!r}'s closure, found {len(locks)}"
        return locks[0]

    health_lock = _closed_over_lock(health_fn)
    accept_lock = _closed_over_lock(accept_fn)
    assert health_lock is accept_lock, (
        "health_fn and accept_fn must share ONE lock, not two separate ones -- design spec "
        "section 4.4: 'The health endpoint's reads and the job endpoint's writes take the same lock'"
    )

    # Held for the whole call: the wrapped function, while running, must
    # find the lock ALREADY acquired.
    observed = {}

    def spy_build_health(*a, **k):
        observed["locked_during_call"] = health_lock.locked()
        return _health_ok()

    monkeypatch.setattr("wl_preproc.responder.health.build_health", spy_build_health)
    assert not health_lock.locked(), "the lock must not be held before any call"
    health_fn()
    assert observed["locked_during_call"] is True, "the lock was not held while the wrapped call ran"
    assert not health_lock.locked(), "the lock must be released after a successful call"

    # Released on exception: accept_fn's wrapped call raising must still
    # release the lock, not leak it held.
    def boom(*a, **k):
        raise ValueError("boom")

    monkeypatch.setattr("wl_preproc.responder.jobs.accept", boom)
    assert not accept_lock.locked()
    with pytest.raises(ValueError):
        accept_fn(object())
    assert not accept_lock.locked(), "an exception from accept() must not leave the lock held"


# --------------------------------------------------------------------------
# serve() itself: real socket, real auth, wired to a real schema.
# --------------------------------------------------------------------------


def test_serve_wires_health_end_to_end_on_a_real_server(monkeypatch, dj_conn, prefix, tmp_path):
    """The one test of `server.py::serve` itself using a REAL, bound socket
    and a real accept loop (the lock proofs above use a faked
    `ThreadingHTTPServer` and never bind one at all): that `serve()` builds
    real callables from `root`/`prefix`, binds a real socket, signals
    `ready`, and answers a real request correctly end to end. Deliberately
    NOT named "...through a real lock..." -- review found the previous name
    claimed a property this test's assertions never checked; the lock
    itself is `test_serve_shares_one_lock_held_for_the_whole_call_and_
    released_on_error` and `test_serves_lock_prevents_any_overlap_between_
    concurrent_accept_calls` above.

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


def test_serve_refuses_to_start_on_a_non_ascii_token(tmp_path, prefix):
    """Review Critical, the worse direction: a non-ASCII CONFIGURED token
    used to make every request fail authentication silently, correct token
    included, for as long as the process ran, with no HTTP-level error to
    diagnose ("an en-dash pasted from a document"). `serve()` now refuses
    to start at all, loudly, before binding a socket or building anything
    -- confirmed here that the refusal happens with no server left running
    and no port left bound (a synchronous call that raises, not a
    background thread that silently never becomes ready).
    """
    import wl_preproc.responder.server as server_module

    root = tmp_path / "scratch"
    root.mkdir()
    with pytest.raises(ValueError, match="non-ASCII"):
        server_module.serve(0, "en–dash-token", root, prefix)
