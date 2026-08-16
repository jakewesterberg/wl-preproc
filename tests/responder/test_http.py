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

**Review round 2 (2026-08-16)** found a regression round 1 introduced (the
`409` seam catching the root of DataJoint's whole error tree), four
residuals, and two prose defects. Two of its findings are about THIS file
rather than the modules it tests, and both are the same defect in different
clothes -- a test that passes while proving nothing:

- `test_serve_refuses_to_start_on_a_non_ascii_token`'s docstring claimed the
  refusal leaves "no server left running and no port left bound", but
  `pytest.raises(ValueError)` was its only assertion, which a guard placed
  AFTER the socket bind satisfies just as well. It now records constructor
  calls. That is this phase's third test whose NAME asserted a property its
  body never checked.
- `test_serve_shares_one_lock_held_for_the_whole_call_and_released_on_error`
  proved release-on-raise for `accept_fn` only; mutating `locked_health_fn`
  to leak the lock left all 45 tests passing. It now checks both callables.

Every test added or changed in round 2 was mutation-proven: the code was
broken, the test confirmed failing, the code restored.
"""

from __future__ import annotations

import contextlib
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

    def _start(token: str, health_fn, accept_fn, *, timeout: float | None = None) -> str:
        """`timeout`, when given, subclasses the class `make_handler`
        returns purely to shorten `BaseHTTPRequestHandler.timeout` -- the
        socket timeout review round 2's Important 3 added. It is a CLASS
        attribute read by `socketserver.StreamRequestHandler.setup()`, so
        overriding it needs no signature change anywhere in `handler.py` and
        lets the timeout tests below run in a quarter of a second instead of
        the shipped 30. Every other behaviour is inherited verbatim; the
        SHIPPED value is pinned separately and structurally by
        `test_the_shipped_handler_carries_the_production_request_timeout`.
        """
        handler_cls = make_handler(token, health_fn, accept_fn)
        if timeout is not None:
            handler_cls = type("FastTimeoutHandler", (handler_cls,), {"timeout": timeout})
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


@contextlib.contextmanager
def _serving(handler_cls):
    """A real `ThreadingHTTPServer` bound to an ephemeral port running
    `handler_cls` verbatim, torn down on exit -- for the handful of tests
    below that need a handler subclass `start_server` has no parameter for
    (one whose `handle_one_request` raises unconditionally, one that calls
    `send_error` with a code `_SEND_ERROR_BODIES` does not list). Yields
    the bound port. `start_server`'s own `_start` is not reused here
    because its only subclassing hook is `timeout`; a second parameter for
    every future one-off shape would grow it past what any test actually
    needs generically.
    """
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
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


@pytest.mark.parametrize(
    "method", ["CONNECT", "PROPFIND", "LINK", "FOO", "BREW", "M-SEARCH", "get"]
)
def test_any_foreign_method_token_is_401_json_not_stdlibs_html_501(start_server, method):
    """Review round 2's Minor 6. Round 1 closed review Important 5 for the
    six verbs it could name -- `PUT`, `DELETE`, `HEAD`, `OPTIONS`, `PATCH`,
    `TRACE`, each given its own `do_*` alias. But a method token is
    arbitrary, so six aliases were never the class: measured against round
    1's code, every one of these seven still reached
    `BaseHTTPRequestHandler`'s default `send_error(501, "Unsupported method
    (%r)")` and got an UNAUTHENTICATED HTML page -- from a module whose
    sibling frozen contract (`contracts/protocol.py`) says "We refuse to
    emit markup at all". The page's distinctive shape also fingerprints the
    stack even with the version string emptied, and stdlib formats the
    caller's own method token into both the body and the STATUS LINE's
    reason phrase (measured: `HTTP/1.0 501 Unsupported method ('CONNECT')`).

    `401`, not `501`, and identical to the six explicit aliases: those
    already answer `401` before routing precisely so an unauthenticated
    caller learns nothing about which verbs this host answers. A seventh,
    unknown verb replying `501` is what would be inconsistent -- `501` says
    "that method is not implemented here", which is the exact fact the other
    six decline to disclose.

    Sent over a raw socket rather than `urllib`, because `http.client`
    refuses to send some of these tokens at all.
    """
    base = start_server(TOKEN, _unused, _unused)
    port = int(base.rsplit(":", 1)[1])

    # Probed WITH a valid token as well as without. Measured, and asserted
    # rather than glossed: an unknown verb answers 401 either way, because
    # `send_error` runs on paths where it cannot know whether auth passed --
    # on `parse_request`'s failure paths `self.headers` is exactly what
    # failed to parse. That leaves one asymmetry against the six aliased
    # verbs, which DO run the real auth-then-route logic: authenticated
    # `PUT /health` is 405 (see
    # `test_unsupported_verbs_authenticate_first_and_answer_in_json`),
    # authenticated `BREW /health` is 401. 401 is the safe direction --
    # it discloses less, to a caller asking for a method that does not exist.
    for token_header in ("", f"Authorization: Bearer {TOKEN}\r\n"):
        raw = _raw_http_response(
            port,
            (
                f"{method} /health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                f"{token_header}Connection: close\r\n\r\n"
            ).encode(),
        )

        assert raw, f"{method}: no response at all"
        status_line = _status_line(raw)
        assert " 401 " in status_line, f"{method}: expected 401, got {status_line!r}"
        body = raw.split(b"\r\n\r\n", 1)[1]
        assert json.loads(body) == {"error": "unauthorized"}
        assert b"<html" not in raw.lower(), f"{method}: markup in the response: {raw[:200]!r}"
        assert b"DOCTYPE" not in raw
        # Neither the body NOR the status line's reason phrase may echo the
        # caller's own token back -- stdlib's default put it in both.
        assert method.encode() not in raw, (
            f"{method}: the response echoes the request's method token"
        )


@pytest.mark.parametrize(
    ("label", "request_bytes", "expected_status", "expected_body"),
    [
        ("malformed request line", b"GARBAGE\r\n\r\n", None, {"error": "bad request"}),
        ("bad http version", b"GET /health HTTP/9.9.9\r\n\r\n", None, {"error": "bad request"}),
        ("http 2.0", b"GET /health HTTP/2.0\r\n\r\n", None, {"error": "http version not supported"}),
        (
            "oversized header",
            b"GET /health HTTP/1.1\r\nX-Big: " + b"a" * 70000 + b"\r\n\r\n",
            431,
            {"error": "request header fields too large"},
        ),
        (
            "over-long request line",
            b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\n\r\n",
            414,
            {"error": "request line too long"},
        ),
    ],
)
def test_stdlibs_own_framing_errors_answer_in_json_never_markup(
    start_server, label, request_bytes, expected_status, expected_body
):
    """Review round 2's Minor 6, the half no `do_*` alias could ever have
    reached: `BaseHTTPRequestHandler` calls `send_error` from inside
    `parse_request` and `handle_one_request`, BEFORE any `do_*` method
    exists to dispatch to. All five of these produced an HTML page quoting
    the caller's own bytes back at them; all five now answer in JSON.

    **The ordering trap, checked rather than assumed.** `parse_request`
    calls `send_error` while `self.command` is still `None`, so the override
    must depend on nothing it has not yet set. It reaches `send_response`,
    which reads `self.requestline` (via `log_request`) and
    `self.request_version` (via `send_response_only`) -- both assigned in
    `parse_request`'s first three lines. These probes are what actually
    exercise that; a `TypeError` or `AttributeError` there would surface as
    a traceback and an empty response.

    **`expected_status is None` is a measured stdlib behaviour, not a
    shrug.** On the two paths where `parse_request` fails before it has
    learned the request version -- a malformed request line, and a bad
    version string -- `self.request_version` is still the `HTTP/0.9`
    placeholder, and stdlib's `send_response_only` and `end_headers` are
    BOTH no-ops for HTTP/0.9. Measured directly, before this fix and after:
    those responses carry no status line and no headers at all, only a body.
    So there is no status code on the wire to assert. What this fix changes
    for them is the body -- a fixed JSON object instead of an HTML page
    echoing the caller's bytes -- and that is what is asserted. Conveying a
    status code on those two paths would mean overriding stdlib's version
    semantics, which is a different change from this one.

    **`expected_body` pins `_SEND_ERROR_BODIES` -- review round 3's Minor 3.**
    Every case above used to check only `"error" in parsed`, which
    `_SEND_ERROR_FALLBACK_BODY` (`{"error": "request rejected"}`) satisfies
    just as well as any of the four real entries: replacing the whole
    `_SEND_ERROR_BODIES` table with `{}` left 67 tests passing, this one
    among them, because nothing checked which body came back, only that
    SOME body with an `"error"` key did. `expected_body` traces each case to
    the specific status `send_error` is actually called with, derived from
    `http.server.BaseHTTPRequestHandler.parse_request`'s own source rather
    than assumed: "malformed request line" and "bad http version" both hit
    the SAME `send_error(400, "Bad request ...")` call (one via the
    `len(words)` check, one via the version-parse `except`), so both pin
    `{"error": "bad request"}`; "http 2.0" is the one case that reaches
    `send_error(505, ...)` (`version_number >= (2, 0)`), pinning
    `{"error": "http version not supported"}`; "oversized header" and
    "over-long request line" pin the 431 and 414 bodies respectively. All
    four real table entries are covered between them, so replacing the
    table with `{}` now fails every parametrization: each one still gets
    *a* JSON body with an `"error"` key (`_SEND_ERROR_FALLBACK_BODY`), but
    the wrong one.
    """
    base = start_server(TOKEN, _unused, _unused)
    port = int(base.rsplit(":", 1)[1])

    raw = _raw_http_response(port, request_bytes)

    assert raw, f"{label}: no response at all"
    assert b"<html" not in raw.lower(), f"{label}: markup in the response: {raw[:200]!r}"
    assert b"DOCTYPE" not in raw, f"{label}: markup in the response: {raw[:200]!r}"
    assert b"Traceback" not in raw
    assert b"GARBAGE" not in raw and b"9.9.9" not in raw, f"{label}: echoes request data"

    body = raw.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in raw else raw
    parsed = json.loads(body)
    assert parsed == expected_body, f"{label}: expected body {expected_body!r}, got {parsed!r}"

    if expected_status is not None:
        assert f" {expected_status} " in _status_line(raw), (
            f"{label}: expected {expected_status}, got {_status_line(raw)!r}"
        )


def test_send_errors_fallback_body_is_used_for_a_code_the_table_does_not_list():
    """The fifth string review round 3's Minor 3 named as untested:
    `_SEND_ERROR_FALLBACK_BODY` (`{"error": "request rejected"}`), what
    `send_error`'s override falls back to for a status code
    `_SEND_ERROR_BODIES` does not enumerate -- "anything else stdlib might
    route through `send_error` in a future Python", per that constant's own
    comment. Nothing today drives `send_error` with such a code over a real
    socket: `parse_request`/`handle_one_request` raise only
    400/414/431/505/501 (confirmed at source), and 501 is special-cased
    before the table lookup even runs. So this string had zero test
    coverage of its own -- `test_stdlibs_own_framing_errors_answer_in_json_
    never_markup`'s five real cases can only ever reach the four entries
    the table actually has.

    Pinned directly, over a real socket, the same "subclass the shipped
    handler" shape `test_a_real_escape_past_every_guard_prints_cpython3s_
    own_banner` above uses -- here to reach a `send_error` call stdlib
    itself never makes, by having `do_GET` call it directly with a code
    that is not `501` and not one of the four listed.
    """
    handler_cls = make_handler(TOKEN, _health_ok, _unused)

    class UnlistedCodeHandler(handler_cls):
        def do_GET(self) -> None:
            # Bypasses this module's own auth/routing on purpose -- this
            # test is about send_error's fallback lookup, not about what
            # reaches it in production (nothing does; see docstring above).
            self.send_error(599, "made up for this test, not a real stdlib code")

    with _serving(UnlistedCodeHandler) as port:
        raw = _raw_http_response(
            port,
            f"GET /health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode(),
        )

    assert raw, "no response at all"
    assert " 599 " in _status_line(raw), f"expected 599, got {_status_line(raw)!r}"
    body = raw.split(b"\r\n\r\n", 1)[1]
    assert json.loads(body) == {"error": "request rejected"}


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
    """Review round 1's Important 4. **Two of these four hung the request
    thread forever; the other two answered `500` in 0.00 s.** Round 2's
    Minor 4 corrected an earlier version of this docstring which said all
    four "used to each hang this request's handler thread forever (never
    responding, not even with an error)" -- the original review finding had
    it right ("two more shapes returned 500") and round 1's prose lost it.
    Re-measured against unfixed parsing (no `isdigit()` guard, no cap), a
    2-byte body, a 3 s client deadline:

    ```
    CL='-1'                    -> no response at all (hang)
    CL='1_2'                   -> no response at all (hang)
    CL='8000000000'            -> 500 in 0.00s  OSError: [Errno 22] Invalid argument
    CL='100000000000000000000' -> 500 in 0.00s  OverflowError: cannot fit 'int' into
                                                an index-sized integer
    ```

    - `-1` -- **hang.** `int("-1")` succeeds, and `self.rfile.read(-1)` reads
      until the PEER closes the connection, which a live client never does
      on its own.
    - `1_2` -- **hang.** `int("1_2") == 12` (Python's
      underscore-in-int-literal grammar applies to `int(str)` too), silently
      reinterpreting a header that is not valid HTTP (`Content-Length =
      1*DIGIT`, no underscore) as "read 12 bytes" of an actually-shorter
      body -- `read()` blocks waiting for bytes that are never coming.
    - `8000000000` (8 GB, in range for a Python int) -- **`500`, not a
      hang.** `read()` raises `OSError: [Errno 22] Invalid argument` at once.
      Not an allocation failure, despite what an earlier version of
      `handler.py`'s matching paragraph said: `io.BytesIO(b"hi").read(
      8000000000)` returns immediately with no error and no allocation.
    - `100000000000000000000` (10**20, past `sys.maxsize`) -- **`500`, not a
      hang.** `read(...)` converts its argument to a C `Py_ssize_t`
      internally and raises `OverflowError`.

    So this fix closed two different defects at once: two genuine hangs, and
    two host-fault `500`s for what is plainly a malformed request from the
    caller. All four now get a fast, clean `422` -- `str.isdigit()` rejects
    the first two before `int()` is ever called (a leading `-` and an
    underscore are both not digits), and the cap rejects the last two before
    `self.rfile.read(...)` is ever called with them.

    What this cap does NOT close is a `Content-Length` that lies while
    staying at or under the cap; that is bounded by the socket timeout
    instead -- see
    `test_a_content_length_that_overstates_the_body_is_408_not_a_hang`.
    """
    base = start_server(TOKEN, _health_ok, _unused)
    port = int(base.rsplit(":", 1)[1])

    raw = _raw_post_with_content_length(port, TOKEN, content_length)

    assert raw, f"Content-Length {content_length!r} got no response at all -- looks like a hang"
    status_line = _status_line(raw)
    assert " 422 " in status_line, f"Content-Length {content_length!r}: expected 422, got {status_line!r}"
    assert b"Traceback" not in raw


# --------------------------------------------------------------------------
# The request timeout -- review round 2's Important 3. The Content-Length
# cap above bounds MEMORY and only memory; a plain-digit value at or below
# the cap that overstates the body still blocks `self.rfile.read(length)`
# until the peer closes. These two pin the mechanism that bounds THREAD
# OCCUPANCY instead: a socket timeout, and a 408 rather than a dropped
# connection.
# --------------------------------------------------------------------------


def test_the_shipped_handler_carries_the_production_request_timeout():
    """The structural half. Every behavioural timeout test below shortens
    `timeout` to run fast, so without this one the whole mechanism could
    ship with `timeout = None` -- stdlib's default, i.e. block forever --
    and every behavioural test would still pass, because each supplies its
    own value.

    `socketserver.StreamRequestHandler.setup()` calls
    `self.connection.settimeout(self.timeout)` only when it is not `None`
    (confirmed at source on this project's Python 3.11 and on 3.13, which
    CI also runs), so `None` is not "some other timeout", it is no timeout
    at all -- asserted explicitly below rather than left implicit in the
    equality check.
    """
    from wl_preproc.responder import handler as handler_module

    handler_cls = make_handler(TOKEN, _health_ok, _unused)

    assert handler_cls.timeout is not None, (
        "timeout is None -- StreamRequestHandler.setup() skips settimeout() entirely "
        "for None, so the handler would block forever exactly as it did before this fix"
    )
    assert handler_cls.timeout == handler_module._REQUEST_TIMEOUT_S
    assert handler_module._REQUEST_TIMEOUT_S == 30.0


def test_a_content_length_that_overstates_the_body_is_408_not_a_hang(start_server):
    """The behavioural half, and the point of the whole fix. `Content-Length:
    5000` with a 2-byte body is a plain digit string well under
    `_MAX_CONTENT_LENGTH`, so it clears BOTH of round 1's guards --
    `str.isdigit()` passes, the cap passes -- and is handed straight to
    `self.rfile.read(5000)`, which blocks until the peer closes. Measured
    pre-fix: 60 such requests parked 60 handler threads on an unbounded
    `ThreadingHTTPServer`, released only when the client finally closed.

    Answering 408 rather than dropping the connection is the other half.
    `TimeoutError` is an `OSError` (`socket.timeout` has been an alias of it
    since 3.10), so `do_POST`'s `except ValueError` does not catch it, and
    CPython's own `handle_one_request` has an `except TimeoutError` that
    logs one line and closes the socket with NO HTTP RESPONSE AT ALL --
    which is the same client-visible shape as the hang this fix exists to
    end. `handler.py` catches it first and answers.

    `timeout=0.25` on the handler class, not 30 s of real waiting: `timeout`
    is a class attribute read in `setup()`, so a subclass sets it with no
    signature change anywhere. The shipped value is pinned above.
    """
    base = start_server(TOKEN, _health_ok, _unused, timeout=0.25)
    port = int(base.rsplit(":", 1)[1])

    request = (
        f"POST /jobs HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"Authorization: Bearer {TOKEN}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: 5000\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode("utf-8") + b"{}"

    started = time.monotonic()
    raw = _raw_http_response(port, request)
    elapsed = time.monotonic() - started

    assert raw, "an overstated Content-Length got no response at all -- still a hang"
    status_line = _status_line(raw)
    assert " 408 " in status_line, f"expected 408, got {status_line!r}"
    assert json.loads(raw.split(b"\r\n\r\n", 1)[1]) == {"error": "request timed out"}
    assert b"Traceback" not in raw
    assert elapsed < 1.0, f"the timeout did not fire promptly: {elapsed:.2f}s"


def test_a_timeout_produces_no_traceback_on_stderr(start_server, capfd):
    """The standing "no traceback under any input" rule, extended to the one
    new input class this round adds. A `TimeoutError` escaping `do_POST`
    uncaught would reach `socketserver`'s handler-error path and print a
    real traceback to stderr -- the same failure mode the non-ASCII token
    had before round 1's Critical fix.

    Asserts the ABSENCE OF A TRACEBACK, not an empty stderr, and that
    distinction is deliberate -- the brief for this round asked it to be
    checked rather than assumed. Measured stderr for the two probes below,
    verbatim:

    ```
    127.0.0.1 - - [...] "POST /jobs HTTP/1.1" 408 -
    127.0.0.1 - - [...] Request timed out: TimeoutError('timed out')
    ```

    The first is `BaseHTTPRequestHandler`'s ordinary per-request access-log
    line (deliberately not suppressed -- see `handler.py`'s closing
    comment). The second is stdlib's own `log_error` on the request-LINE
    timeout path, which never reaches this module at all. Both are single
    lines; neither is a traceback; and an `assert captured.err == ""` here
    would fail on entirely correct behaviour. So the assertion below names
    the two things that WOULD indicate an unhandled exception: CPython's
    traceback header, and `socketserver.BaseServer.handle_error`'s own
    banner, which is what actually prints when a handler raises past every
    guard.
    """
    base = start_server(TOKEN, _health_ok, _unused, timeout=0.25)
    port = int(base.rsplit(":", 1)[1])

    request = (
        f"POST /jobs HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"Authorization: Bearer {TOKEN}\r\n"
        f"Content-Length: 5000\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode("utf-8") + b"{}"
    raw = _raw_http_response(port, request)
    assert " 408 " in _status_line(raw)

    # A connection that sends NOTHING at all times out on the request-LINE
    # read instead, which never reaches this module: stdlib's own
    # handle_one_request catches that one. Included because it is the other
    # half of "a timeout", and it must not print a traceback either.
    with socket.create_connection(("127.0.0.1", port), timeout=3.0) as sock:
        sock.settimeout(3.0)
        try:
            sock.recv(4096)
        except OSError:
            pass

    time.sleep(0.2)  # let both handler threads finish writing their log lines
    captured = capfd.readouterr()
    assert "Traceback (most recent call last)" not in captured.err, captured.err
    assert "Exception occurred during processing of request" not in captured.err, captured.err


def test_a_real_escape_past_every_guard_prints_cpython3s_own_banner(capfd):
    """Mutation proof for review round 3's Important 1.

    `test_a_timeout_produces_no_traceback_on_stderr`'s second assertion
    named the string `"Exception happened during processing of request"`
    -- Python 2's `SocketServer.py` wording. CPython 3's own
    `socketserver.BaseServer.handle_error` prints `"Exception occurred
    during processing of request from"` instead: confirmed by reading
    `inspect.getsource(socketserver.BaseServer.handle_error)` directly on
    both CI Pythons, 3.11 and 3.13 (identical source on both, not merely
    the same behaviour). The old literal has zero hits in either
    interpreter's stdlib, so that assertion could not fail under any
    input, despite its own docstring calling it stdlib's "own banner,
    which is what actually prints when a handler raises past every guard."

    Forces a REAL escape to prove the corrected literal actually is one --
    not read off the source and trusted, but produced and observed. A
    subclass of the shipped handler whose `handle_one_request` raises
    unconditionally bypasses `_authorized`, both `try` blocks in
    `do_GET`/`do_POST`, and `BaseHTTPRequestHandler.handle_one_request`'s
    own `except TimeoutError` -- the identical escape shape the non-ASCII
    token bug took by accident before round 1's Critical fix, reproduced
    here on purpose. Traced at source: `finish_request` instantiates the
    handler, whose `BaseRequestHandler.__init__` runs `self.handle()` (->
    this override) inside a bare `try/finally`, so the raise propagates
    out of `__init__` itself, back through `finish_request`, into
    `ThreadingMixIn.process_request_thread`'s `except Exception:
    self.handle_error(...)` -- the only remaining catch, and stdlib's own,
    unoverridden `BaseServer.handle_error`, which prints the banner and a
    traceback, then keeps serving.
    """
    handler_cls = make_handler(TOKEN, _health_ok, _unused)

    class ForcedEscapeHandler(handler_cls):
        def handle_one_request(self) -> None:
            raise RuntimeError("forced escape -- review round 3's Important 1")

    with _serving(ForcedEscapeHandler) as port:
        with socket.create_connection(("127.0.0.1", port), timeout=3.0):
            pass  # handle_one_request raises before reading anything at all
        time.sleep(0.3)  # let the handler thread finish printing

    captured = capfd.readouterr()

    # The escape really happened, and really looks like what both
    # assertions in test_a_timeout_produces_no_traceback_on_stderr claim to
    # detect.
    assert "Traceback (most recent call last)" in captured.err
    assert "RuntimeError: forced escape" in captured.err
    assert "Exception occurred during processing of request from" in captured.err

    # The OLD literal, searched for in the SAME captured output a real
    # escape just produced: not there. This is what proves
    # test_a_timeout_produces_no_traceback_on_stderr's pre-fix assertion
    # would have silently passed on exactly this input rather than
    # catching it.
    assert "Exception happened during processing of request" not in captured.err

    # The CORRECTED literal, applied the same way (`assert ... not in
    # captured.err`) test_a_timeout_produces_no_traceback_on_stderr applies
    # it: on this same real-escape input it DOES fail. That is what makes
    # it a live marker rather than a decoration.
    with pytest.raises(AssertionError):
        assert "Exception occurred during processing of request" not in captured.err


# --------------------------------------------------------------------------
# POST /jobs -- everything past accept()'s own ValueError contract:
# ConflictError -> 409 (review Important 3), and 500 for everything else,
# all handled, none a traceback.
# --------------------------------------------------------------------------


def test_a_conflict_error_from_accept_fn_is_409(start_server):
    """`handler.py`'s OWN mapping of its own `ConflictError` type -- no
    `server.py`, no DataJoint, no database. See
    `test_translate_accept_errors_turns_key_reuse_into_a_conflict_error`
    and `test_a_real_key_reuse_conflict_through_the_translation_seam_is_409`
    below for the seam that actually PRODUCES a `ConflictError` from a real
    `KeyReuseError` in production.
    """

    def accept_fn(request):
        raise ConflictError("idempotency key 'x' is already recorded against a different request")

    base = start_server(TOKEN, _health_ok, accept_fn)
    status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=_valid_job_payload())
    assert status == 409
    assert json.loads(body) == {
        "error": "idempotency key 'x' is already recorded against a different request"
    }


def test_translate_accept_errors_turns_key_reuse_into_a_conflict_error(monkeypatch, prefix):
    """`server.py`'s own translation seam, tested directly -- no lock, no
    thread, no HTTP, no real database. Review round 1's Important 3 fix:
    this is the "four lines in the module that is permitted to import
    DataJoint" the review pointed at.

    Half of a pair: the other half,
    `test_translate_accept_errors_leaves_every_other_datajoint_error_alone`,
    is what makes this one mean anything. On its own this assertion is
    satisfied identically by a seam that catches ONLY `KeyReuseError` and by
    one that catches the entire DataJoint error tree -- which is exactly the
    over-broad seam review round 2 found (Important 1), and exactly what
    this test, alone, failed to catch.
    """
    import wl_preproc.responder.server as server_module
    from wl_preproc.schema.request import KeyReuseError

    def boom(request, prefix=None):
        raise KeyReuseError("idempotency key 'x' is already recorded against a different request")

    monkeypatch.setattr("wl_preproc.responder.jobs.accept", boom)

    with pytest.raises(ConflictError, match="already recorded"):
        server_module._translate_accept_errors(object(), prefix=prefix)


@pytest.mark.parametrize(
    "error_name",
    ["LostConnectionError", "AccessError", "MissingTableError", "IntegrityError", "ThreadSafetyError"],
)
def test_translate_accept_errors_leaves_every_other_datajoint_error_alone(
    monkeypatch, prefix, error_name
):
    """Review round 2's Important 1, the direction round 1 left unpinned.
    `_translate_accept_errors` used to catch `dj.DataJointError` -- the ROOT
    of DataJoint's entire error tree -- so every one of these five answered
    `409` when driven through the real seam. `409` is documented by
    `handler.py` as "a request that cannot succeed as sent, whose remedy is
    outside the retry loop", so wl.works stops retrying and escalates to a
    human.

    `LostConnectionError` is the one that makes this a real regression rather
    than a taxonomy quibble: `jobs.accept` writes inside a transaction, so a
    MySQL restart or a LAN blip mid-POST raises it on the PRODUCTION path.
    Pre-round-1 that was a `500`, which wl.works' retry loop handles
    correctly; round 1's fix made it strictly worse. Each of these must now
    propagate untouched and become `handler.py`'s handled `500` -- see
    `test_a_lost_connection_error_through_the_real_seam_reaches_the_client_as_500`
    for that half over a real socket.
    """
    import wl_preproc.responder.server as server_module
    from wl_preproc.schema.request import KeyReuseError

    error_cls = getattr(dj.errors, error_name)
    # Asserted, not assumed: the whole point is that these ARE DataJointErrors
    # and so WOULD have been swallowed by the previous root-level catch.
    assert issubclass(error_cls, dj.DataJointError)
    assert not issubclass(error_cls, KeyReuseError)

    def boom(request, prefix=None):
        raise error_cls("the database went away mid-write")

    monkeypatch.setattr("wl_preproc.responder.jobs.accept", boom)

    with pytest.raises(error_cls):
        server_module._translate_accept_errors(object(), prefix=prefix)


def test_a_lost_connection_error_through_the_real_seam_reaches_the_client_as_500(
    start_server, monkeypatch, prefix
):
    """Review round 2's Important 1, end to end over a real socket through
    the real `_translate_accept_errors` -- the production wiring, not a fake
    `accept_fn`. A MySQL restart or a LAN blip mid-POST raises
    `dj.errors.LostConnectionError` out of `jobs.accept`'s transaction; the
    client must see `500` (which wl.works retries) and not `409` (which it
    escalates to a human).

    The counterpart is
    `test_a_real_key_reuse_conflict_through_the_translation_seam_is_409`
    below, which drives a REAL `KeyReuseError` out of a real
    `_reject_key_reuse` against a real database and gets `409` through the
    same seam. Together they pin both sides of the boundary end to end.
    """
    import wl_preproc.responder.server as server_module

    def boom(request, prefix=None):
        raise dj.errors.LostConnectionError("Connection was lost during a transaction.")

    monkeypatch.setattr("wl_preproc.responder.jobs.accept", boom)

    def accept_fn(request):
        return server_module._translate_accept_errors(request, prefix=prefix)

    base = start_server(TOKEN, _health_ok, accept_fn)
    status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=_valid_job_payload())

    assert status == 500, f"a lost connection must be a retryable 500, not {status}"
    text = body.decode("utf-8")
    assert "Traceback" not in text
    assert json.loads(body)["error"].startswith("LostConnectionError:")


def test_a_datajoint_error_from_accept_fn_bypassing_translation_is_still_a_safe_500(start_server):
    """Defense in depth, not the documented mapping: `handler.py` imports
    no DataJoint, so it cannot recognise a `dj.DataJointError` by name at
    all. A bare `dj.DataJointError` is not what the seam translates anyway
    (round 2's Important 1 narrowed that to `KeyReuseError` -- see
    `test_translate_accept_errors_leaves_every_other_datajoint_error_alone`),
    so this is `500` twice over: the seam declines to convert it AND the
    handler could not recognise it if it arrived unconverted. This test
    constructs the second shape directly -- an `accept_fn` that never went
    through `server.py` at all -- to prove the outcome is a HANDLED `500`,
    not a crash. See `test_a_conflict_error_from_accept_fn_is_409` for the
    actual `409` mapping and
    `test_a_real_key_reuse_conflict_through_the_translation_seam_is_409`
    for the real, translated, end-to-end path.
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


def test_a_timeout_error_from_accept_fn_is_500_not_408(start_server):
    """Review round 3's Minor 2. `do_POST` used to wrap `_parse_job_request()`
    and `self._accept_fn(request)` in the SAME `try`, so `except
    TimeoutError` caught a `TimeoutError` from either one and answered 408
    for both -- even though the branch's own comment, `_parse_job_request`'s
    docstring and the module docstring's status table all scope 408 to "the
    body never arrived within `_REQUEST_TIMEOUT_S`", a claim only true of
    the first call. A `TimeoutError` out of `accept_fn` would have told
    wl.works ITS body arrived late, when in fact it arrived completely and
    something downstream of parsing was slow (or timed out) instead --
    discarding the real diagnostic.

    `_parse_job_request()` now runs in its own `try`, scoped exactly to the
    body read, so a `TimeoutError` from `accept_fn` falls through to the
    generic `except Exception` below and answers `500`, the same as any
    other infrastructure fault `accept_fn` can raise -- proven here, not
    assumed from the diff: before the fix this test failed with the wrong
    status (408); after it, 500.
    """

    def accept_fn(request):
        raise TimeoutError("simulated: accept_fn itself timed out, not the body read")

    base = start_server(TOKEN, _health_ok, accept_fn)
    status, body = _request(f"{base}/jobs", method="POST", token=TOKEN, body=_valid_job_payload())
    assert status == 500, f"expected 500, got {status} -- body {body!r}"
    assert "Traceback" not in body.decode("utf-8")
    assert json.loads(body)["error"].startswith("TimeoutError:")


def test_nothing_ever_returns_a_traceback_regardless_of_input(start_server):
    """The project's own blanket rule ("nothing may return a traceback to
    the client under any input"), checked directly against a sweep of
    inputs designed to hit every branch in `handler.py`, rather than
    trusted from the individual tests above in isolation. `accept_fn`
    itself raises an exception whose OWN message contains text that would
    look like part of a traceback, to make sure nothing downstream
    re-embeds it in a way that could be mistaken for one.

    Round 2 extended the sweep past what `urllib` can construct: an
    arbitrary method token (`BREW`, and a non-ASCII one), the four framing
    errors stdlib raises from inside `parse_request`/`handle_one_request`
    before any `do_*` method exists, and the `Content-Length` cap boundary
    -- exactly `_MAX_CONTENT_LENGTH`, the largest value that clears both of
    round 1's guards and still reaches `read()`.
    """
    from wl_preproc.responder import handler as handler_module

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

    # Round 2's new input classes, over a raw socket because `urllib`
    # constructs none of them: an arbitrary method token (including a
    # non-ASCII one) and stdlib's own framing errors.
    port = int(base.rsplit(":", 1)[1])
    host = f"Host: 127.0.0.1:{port}\r\n"
    auth = f"Authorization: Bearer {TOKEN}\r\n"
    raw_probes = [
        ("foreign verb", f"BREW /health HTTP/1.1\r\n{host}{auth}Connection: close\r\n\r\n".encode()),
        ("non-ASCII verb", f"ÄÖ /health HTTP/1.1\r\n{host}{auth}Connection: close\r\n\r\n".encode()),
        ("malformed request line", b"GARBAGE\r\n\r\n"),
        ("bad http version", f"GET /health HTTP/9.9.9\r\n{host}\r\n".encode()),
        ("http 2.0", f"GET /health HTTP/2.0\r\n{host}\r\n".encode()),
        ("oversized header", f"GET /health HTTP/1.1\r\n{host}X-Big: ".encode() + b"a" * 70000 + b"\r\n\r\n"),
        ("over-long request line", b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\n\r\n"),
    ]

    # The Content-Length cap BOUNDARY -- exactly `_MAX_CONTENT_LENGTH`, the
    # largest value that still clears BOTH of round 1's guards and is handed
    # to `read()`. It needs its own server: on the shipped 30 s timeout this
    # probe correctly outlives `_raw_http_response`'s own 3 s client
    # deadline, which would read as "no response" here for the one reason
    # that is not a defect. 0.25 s makes the 408 observable without changing
    # which code path runs.
    fast_base = start_server(TOKEN, _health_ok, accept_fn, timeout=0.25)
    fast_port = int(fast_base.rsplit(":", 1)[1])
    raw_probes.append(
        (
            "cap boundary, understated body",
            f"POST /jobs HTTP/1.1\r\nHost: 127.0.0.1:{fast_port}\r\n{auth}"
            f"Content-Length: {handler_module._MAX_CONTENT_LENGTH}\r\n"
            f"Connection: close\r\n\r\n".encode() + b"{}",
        )
    )

    for label, request_bytes in raw_probes:
        target = fast_port if label.startswith("cap boundary") else port
        raw = _raw_http_response(target, request_bytes)
        assert raw, f"{label}: no response at all"
        assert b"Traceback" not in raw, (label, raw[:300])
        assert b"<html" not in raw.lower() and b"DOCTYPE" not in raw, (label, raw[:300])
        payload = raw.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in raw else raw
        json.loads(payload)  # every body, on every one of these paths, is valid JSON


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
    """End-to-end proof of review round 1's Important 3 fix: a REAL
    `KeyReuseError` from `schema/request.py::_reject_key_reuse`, through
    `server.py`'s real `_translate_accept_errors`, mapped by `handler.py` to
    `409` -- the actual production seam, not the fake `accept_fn` in
    `test_a_conflict_error_from_accept_fn_is_409` above. The other side of
    the boundary, over the same real seam, is
    `test_a_lost_connection_error_through_the_real_seam_reaches_the_client_as_500`.
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

    # ...and the SAME property for health_fn -- review round 2's Important 2.
    # Round 1 proved release-on-raise for accept_fn only, so mutating
    # locked_health_fn into acquire / call / release-without-`try` left all
    # 45 tests passing. Not hypothetical: `health.build_health`'s own
    # `except Exception` wraps only `gather_readings`, while
    # `available_actions(prefix=prefix)` touches the database entirely
    # outside it -- a raise there leaks the process-wide lock and wedges
    # every later request on this responder permanently, which is the exact
    # failure this test's name promises to prevent.
    monkeypatch.setattr("wl_preproc.responder.health.build_health", boom)
    assert not health_lock.locked()
    with pytest.raises(ValueError):
        health_fn()
    assert not health_lock.locked(), "an exception from build_health() must not leave the lock held"


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


def test_serve_refuses_to_start_on_a_non_ascii_token(monkeypatch, tmp_path, prefix):
    """Review Critical, the worse direction: a non-ASCII CONFIGURED token
    used to make every request fail authentication silently, correct token
    included, for as long as the process ran, with no HTTP-level error to
    diagnose ("an en-dash pasted from a document"). `serve()` now refuses
    to start at all, loudly, before binding a socket or building anything.

    The construction spy is review round 2's Minor 5. This test's docstring
    already claimed "the refusal happens with no server left running and no
    port left bound", but `pytest.raises(ValueError)` was its ONLY
    assertion -- and that assertion is satisfied just as well by a guard
    placed AFTER `ThreadingHTTPServer(...)`, which destroys exactly the
    property being claimed and leaks a bound socket on every rejected
    token. A test whose name and docstring assert a property its
    assertions never check is this phase's third such test. Recording the
    constructor calls is what makes the claim real.

    It matters more now than it did in round 1: Minor 7 moved the guard
    into `make_handler`, so `serve()`'s "nothing was constructed" property
    now depends on `make_handler` being called before `ThreadingHTTPServer`
    rather than on a raise in `serve()`'s own first statement. This test is
    what holds that ordering in place.
    """
    import wl_preproc.responder.server as server_module

    constructed = []
    real_cls = server_module.ThreadingHTTPServer

    class _RecordingServer(real_cls):
        def __init__(self, *args, **kwargs):
            constructed.append(args)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(server_module, "ThreadingHTTPServer", _RecordingServer)

    root = tmp_path / "scratch"
    root.mkdir()
    with pytest.raises(ValueError, match="non-ASCII"):
        server_module.serve(0, "en–dash-token", root, prefix)

    assert constructed == [], (
        "serve() constructed a ThreadingHTTPServer before refusing the token -- "
        f"a socket was bound and leaked on the rejected path: {constructed!r}"
    )


def test_make_handler_itself_refuses_a_non_ascii_token(tmp_path):
    """Review round 2's Minor 7: the guard belongs to `make_handler`, the
    layer that cannot tolerate the value, not to `serve()`.

    Before this, `make_handler` accepted a non-ASCII token without
    complaint and handed back a handler that 401s every request -- INCLUDING
    one carrying the correct token, since `hmac.compare_digest` raises
    `TypeError` on the configured side too. Only `serve()` refused, so every
    other caller (every test in this file among them) could build the
    bricked handler silently. One guard, at the layer that owns the
    constraint; `serve()` inherits it and keeps no copy of its own.
    """
    with pytest.raises(ValueError, match="non-ASCII"):
        make_handler("en–dash-token", _health_ok, _unused)

    # The ASCII neighbour still builds, so the guard is not simply refusing
    # everything.
    assert make_handler("en-dash-token", _health_ok, _unused) is not None
