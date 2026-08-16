"""Routing, auth and status codes for the two endpoints wl.works polls. Design
spec section 4. **Imports no DataJoint** -- the same separation `ingest/`
already uses between its watcher and its landing logic, and for the same
reason: routing, header parsing and status-code mapping are testable with no
database at all, and there are more of those cases here than there are cases
that actually need one (design spec section 8). Every test in
`tests/responder/test_http.py` that does not say otherwise runs with no
Docker container and no schema activated.

`make_handler(token, health_fn, accept_fn)` closes over the bearer token and
two plain callables and returns a `BaseHTTPRequestHandler` subclass. Neither
callable is inspected or called differently based on what it is -- `server.py`
is the only module that knows `health_fn`/`accept_fn` are, in production,
`responder.health.build_health`/`responder.jobs.accept` wrapped in the one
process-wide lock design spec section 4.4 requires. This module never
imports `wl_preproc.responder.health`, `wl_preproc.responder.jobs`,
`wl_preproc.schema`, or `datajoint` -- directly or transitively -- so nothing
here can accidentally reach for a second, unlocked path to the database.

## Auth

Both endpoints require `Authorization: Bearer <token>` (design spec section
4.3). The header is compared against the configured token with
`hmac.compare_digest`, not `==` -- a plain string comparison on a bearer
token is a timing side-channel textbook example, and this is the one place
in the whole pipeline an arbitrary device on the lab LAN can reach unasked.
A missing header, a header with the wrong scheme, and a header carrying the
wrong token all take the identical code path to the identical `401` response
body: **there is no hint anywhere in the response about which part was
wrong** (design spec section 4.3, section 9's own enumerated test). Getting
this right is `make_handler`'s Step 5 of its own dispatch: remove the
`compare_digest` check, watch the missing-token and wrong-token tests fail,
restore it -- proving the auth tests actually exercise the check rather than
passing by construction.

## Status codes -- every exception this boundary can see, and why

`POST /jobs` is the only endpoint that can fail on account of what the
*caller* sent, so it is the only endpoint with a `422` branch at all;
`GET /health` takes no body, so nothing about a request to it can be
"malformed" -- it can only succeed or fail on this host's own account (`500`).

| Source | Exception(s) | Status | Why |
|---|---|---|---|
| Auth | missing/malformed/wrong `Authorization` | `401`, generic body | section 4.3; never `403` -- absence of *any* credential and presession of the *wrong* one are the same failure from the caller's side, not two |
| Routing | path not `/health` or `/jobs` | `404` | design spec section 4.2 table: "Anything else is 404" |
| Routing | right path, wrong method (`GET /jobs`, `POST /health`) | `405` | section 9's own named case; a path that exists but does not answer this verb is a different fact than a path that does not exist |
| Body parsing | `json.JSONDecodeError` (bad JSON), `UnicodeDecodeError` (non-UTF-8 body), a garbled `Content-Length` (`int(...)` raising) | `422` | all three are stdlib `ValueError` subclasses -- confirmed directly against this project's installed Python/pydantic -- so a single `except ValueError` catches every one of them along with the two rows below, with no special-casing needed |
| Body parsing | `pydantic.ValidationError` from `JobRequest.model_validate` | `422`, with `.errors()` as `detail` | design spec section 4.2: "A malformed body is 422 with the pydantic error, never a traceback." `ValidationError` **is** a `ValueError` subclass in pydantic v2 -- confirmed directly -- so it falls through the identical `except ValueError` as the row above; no separate branch was needed, only a richer body for this one type |
| `accept_fn` | `ValueError` (bad `selection` keys, an oversized subject, a block outside its montage window, an unknown block, a non-finite boundary, a malformed `session_datetime`, ...) | `422` | `responder/jobs.py::accept`'s own documented contract: "Raises `ValueError` for every rejection it owns." A well-formed JSON body asking for something `accept()` refuses is exactly as much "the caller's mistake" as a body that fails pydantic, so it shares the same status and the same catch clause |
| `accept_fn` | `dj.DataJointError` and every subclass (`DuplicateError`, `IntegrityError`, ...) | `500` | See "Why `DataJointError` is 500, not 422" below |
| `accept_fn` | `pymysql.err.DataError` / any other raw, untranslated `pymysql` exception | `500` | Not a `DataJointError` at all -- DataJoint's MySQL adapter passes 1264/1265/1406 through untranslated, confirmed at source by Task 7's review. `responder/jobs.py` guards every column it writes before insert specifically so this should never fire, but "should not reach here" is not "cannot" is not a license to let it become an *unhandled* `500` with a real traceback if a future column guard is ever missed. It falls into the same generic, final `except Exception` as the row below, which is what makes it a **handled** 500 -- a clean, safe body, still no traceback -- rather than an unhandled one |
| `accept_fn` / `health_fn` | anything else (`AttributeError`, `KeyError`, a genuine bug) | `500` | the blanket backstop. "Nothing may return a traceback to the client under any input" is unconditional, not "under every input this module's author thought of" |

**Why `DataJointError` is `500`, not `422`.** Two reasons, and the first
holds even without the second:

1. This module imports no DataJoint (see above), so it has no
   `dj.DataJointError` to `except` by name in the first place -- it can only
   ever distinguish `ValueError` (stdlib, already reached through
   `json`/`pydantic`) from "everything else". Special-casing `DataJointError`
   would mean importing DataJoint into the one module whose entire testing
   story depends on not doing that.
2. Even granting the import: the realistic `DataJointError` that can reach
   this boundary is `schema/request.py`'s `_reject_key_reuse` refusing a
   reused `idempotency_key` for materially different content. `422` tells a
   caller "fix this request and resend it" -- which is actively wrong advice
   here, since resending the *identical* body fails identically; what the
   caller needs is a *new* idempotency key, a different kind of correction
   than anything else in the `422` row above asks for. The other two
   `DataJointError`s `submit`/`submit_derivative` can raise -- "activate()
   must run first", "cannot nest a transaction" -- are internal-invariant
   guards that `accept()`'s own call to `schema_request.activate(prefix=...)`
   and the single process-wide lock (design spec section 4.4, section 6.4)
   should make unreachable through this path at all; if one fires anyway it
   is a real bug on this host's side, and `500` is the honest signal for
   that too, not `422`.

Nothing above inspects a traceback or ever sends one: every `500` body is
`f"{type(exc).__name__}: {exc}"` -- the exact shape `responder/health.py`'s
own "down" verdict already uses for the identical reason (an operator or
wl.works reading the response can see *what* broke without this host handing
out its own call stack). See `_error_body`/`_send_json`.
"""

from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable

from pydantic import ValidationError

from wl_preproc.contracts.protocol import JobRequest

_HEALTH_PATH = "/health"
_JOBS_PATH = "/jobs"

# The two known paths, mapped to which HTTP method each one answers -- used
# by both do_GET and do_POST to decide 404 (path unknown) vs. 405 (path
# known, wrong verb) with one shared table rather than two independently
# maintained if/elif chains that could drift apart on a third endpoint.
_PATH_METHODS = {_HEALTH_PATH: "GET", _JOBS_PATH: "POST"}

# A single, fixed, no-detail body for every authentication failure -- missing
# header, wrong scheme, wrong token -- so "which part was wrong" is
# structurally unanswerable from the response alone (design spec section
# 4.3, section 9's own named test), not merely unanswered by convention.
_UNAUTHORIZED_BODY = {"error": "unauthorized"}


def _json_default(value: Any) -> str:
    """`json.dumps(..., default=_json_default)`'s fallback for the one type
    a `dict` returned by `accept_fn` can legitimately hold that raw `json`
    cannot serialise on its own: `Activation`'s primary key includes
    `session_datetime`, a `datetime.datetime`. Isoformat is unambiguous and
    needs no second contract beyond what `datetime.datetime.isoformat`
    already documents; nothing here claims to match `jobs.py`'s own stored
    payload encoding (`model_dump(mode="json")`) byte-for-byte, since that is
    a *storage* format this function has no access to and no need to mirror.
    """
    from datetime import date, datetime

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def _error_body(exc: Exception) -> dict[str, Any]:
    """The `422` response body for a `ValueError`-family exception.

    `pydantic.ValidationError` gets its own structured `detail` -- design
    spec section 4.2 says the `422` body **is** "the pydantic error" for a
    malformed body, not a paraphrase of it -- `include_url=False` drops
    pydantic's own doc-link from each entry, which is an internal library
    detail with nothing to do with this contract. Every other `ValueError`
    (a plain `json.JSONDecodeError`, `jobs.accept`'s own domain rejections)
    gets `str(exc)`, which is already a complete, safe, human-readable
    message -- none of this project's `ValueError`s carry structured detail
    of their own (see `responder/jobs.py`, which raises every one of them
    with a fully-formed message string).
    """
    if isinstance(exc, ValidationError):
        return {"error": "invalid request body", "detail": exc.errors(include_url=False)}
    return {"error": str(exc)}


def make_handler(
    token: str,
    health_fn: Callable[[], Any],
    accept_fn: Callable[[JobRequest], dict],
) -> type[BaseHTTPRequestHandler]:
    """Build a `BaseHTTPRequestHandler` subclass closing over `token` and the
    two callables. A fresh class per call -- not a module-level singleton --
    because `token`/`health_fn`/`accept_fn` are closed over as class
    attributes (see below) rather than threaded through some other
    mechanism, and two different tests (or two different `serve()` calls
    with two different tokens) must never share one.

    `health_fn` is called as `health_fn()`, `accept_fn` as
    `accept_fn(request)` where `request` is an already-validated
    `JobRequest` -- this module owns turning raw bytes into that object
    (`_parse_job_request` below) precisely because doing so needs only
    `json` and `contracts.protocol.JobRequest`, neither of which is
    DataJoint, so there is no reason to push it below this boundary.
    """

    class ResponderHandler(BaseHTTPRequestHandler):
        # Class attributes, not instance attributes set in an overridden
        # __init__: BaseHTTPRequestHandler (via socketserver.BaseRequestHandler)
        # calls self.handle() from INSIDE its own __init__, before a
        # subclass's __init__ body would get a chance to run any code
        # placed after a super().__init__() call -- so anything this handler
        # needs during a request must already exist before __init__ is
        # ever invoked, which is exactly what a class attribute guarantees.
        #
        # health_fn/accept_fn specifically are wrapped in `staticmethod(...)`
        # rather than assigned bare. A bare function object stored as a
        # class attribute is a descriptor: `self._health_fn` would trigger
        # Python's normal method-binding machinery and silently call
        # `health_fn(self)` instead of `health_fn()`, passing this handler
        # instance where `server.py`'s zero-argument closure expects
        # nothing at all. `staticmethod` is exactly the stdlib mechanism
        # for "store this callable on a class without binding it".
        _token = token
        _health_fn = staticmethod(health_fn)
        _accept_fn = staticmethod(accept_fn)

        # -- auth --------------------------------------------------------

        def _bearer_token(self) -> str | None:
            """The token in `Authorization: Bearer <token>`, or `None` for
            anything else -- header absent, wrong scheme, or an empty
            token. `None` is the one value `_authorized` below never hands
            to `hmac.compare_digest`, which raises `TypeError` on `None`
            rather than returning `False`.
            """
            value = self.headers.get("Authorization")
            if value is None:
                return None
            scheme, _, candidate = value.partition(" ")
            if scheme != "Bearer" or not candidate:
                return None
            return candidate

        def _authorized(self) -> bool:
            candidate = self._bearer_token()
            if candidate is None:
                return False
            # Both operands must already be str (never bytes-vs-str) for
            # compare_digest's constant-time guarantee to apply as
            # documented; both are str here (self._token, the configured
            # token; candidate, parsed above out of a str header value).
            return hmac.compare_digest(candidate, self._token)

        # -- response plumbing --------------------------------------------

        def _write_response(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            """`payload` as a JSON body. Cannot itself raise past this
            method: if `payload` somehow fails to serialise -- not reachable
            by anything this module builds today, but "nothing may return a
            traceback under any input" is a claim about every input, not
            just the ones this module's author enumerated -- the fallback
            is still a clean, generic `500`, never a Python exception
            propagating back out of a response-writing call.
            """
            try:
                body = json.dumps(payload, default=_json_default).encode("utf-8")
            except (TypeError, ValueError):
                status = 500
                body = b'{"error": "internal error"}'
            self._write_response(status, body)

        # -- routing --------------------------------------------------------

        def _route_or_none(self, method: str) -> int | None:
            """`None` if `self.path` is a known endpoint answering `method`
            (the caller should proceed to handle it); otherwise the status
            already written to the client (`404` for an unknown path, `405`
            for a known path answering a different method) -- section 4.2's
            "Anything else is 404" plus section 9's explicit `GET /jobs`
            `405` case, both driven off the one `_PATH_METHODS` table so a
            third endpoint only has to be added in one place.
            """
            expected = _PATH_METHODS.get(self.path)
            if expected is None:
                self._send_json(404, {"error": "not found"})
                return 404
            if expected != method:
                self._send_json(405, {"error": "method not allowed"})
                return 405
            return None

        def do_GET(self) -> None:
            if not self._authorized():
                self._send_json(401, _UNAUTHORIZED_BODY)
                return
            if self._route_or_none("GET") is not None:
                return
            # Only _HEALTH_PATH answers GET (see _PATH_METHODS) -- reached
            # only when self.path == _HEALTH_PATH.
            try:
                response = self._health_fn()
            except Exception as exc:  # noqa: BLE001 -- see module docstring's table
                # No ValueError/422 branch here on purpose: GET carries no
                # body, so nothing about this request can be malformed --
                # every failure on this path is this host's own, hence
                # always 500, never 422.
                self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
                return
            # response is a pydantic HealthResponse; model_dump_json() is
            # its own canonical serialisation -- the same one
            # `wlpp schemas export` derives docs/schemas/health_response.json
            # from -- so this writes it directly rather than round-tripping
            # it through _send_json's dict-shaped path.
            self._write_response(200, response.model_dump_json().encode("utf-8"))

        def do_POST(self) -> None:
            if not self._authorized():
                self._send_json(401, _UNAUTHORIZED_BODY)
                return
            if self._route_or_none("POST") is not None:
                return
            # Only _JOBS_PATH answers POST (see _PATH_METHODS) -- reached
            # only when self.path == _JOBS_PATH.
            try:
                request = self._parse_job_request()
                result = self._accept_fn(request)
            except ValueError as exc:
                # json.JSONDecodeError, UnicodeDecodeError, a garbled
                # Content-Length's int(...), pydantic.ValidationError, and
                # jobs.accept's own ValueError are ALL ValueError subclasses
                # (confirmed directly against this project's installed
                # Python/pydantic) -- one branch, not four. See the module
                # docstring's table.
                self._send_json(422, _error_body(exc))
            except Exception as exc:  # noqa: BLE001 -- see module docstring's table
                # dj.DataJointError and every subclass, a raw untranslated
                # pymysql error, or a genuine bug -- all land here, all
                # become a HANDLED 500 with a safe body. See "Why
                # DataJointError is 500, not 422" above.
                self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
            else:
                self._send_json(200, {"activation": result, "accepted": True})

        def _parse_job_request(self) -> JobRequest:
            """The request body as a validated `JobRequest`. Raises
            `ValueError` (via `json.loads`, `bytes.decode`, or
            `JobRequest.model_validate`) for anything malformed -- never
            anything else, so `do_POST`'s single `except ValueError` is a
            complete catch for every way this can fail.

            A missing `Content-Length` reads as `0` (`self.headers.get`'s own
            default, an int, needs no `int(...)` call at all in that branch)
            rather than raising `TypeError` from `int(None)` -- an empty body
            then fails at `json.loads(b"")`, still a `ValueError`, just one
            step later.
            """
            length = self.headers.get("Content-Length", 0)
            if length:
                length = int(length)
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw.decode("utf-8"))
            return JobRequest.model_validate(payload)

        # log_message is deliberately NOT overridden: BaseHTTPRequestHandler's
        # default writes one access-log line per request to stderr, which is
        # the closest thing a "no dedicated sysadmin" lab server (design spec
        # section 4.1) gets to an access log for free. Nothing in this task
        # asks for quieter logs, and pytest already captures stderr per test
        # and shows it only on failure -- exactly when an access-log line is
        # useful, not when it is noise.

    return ResponderHandler
