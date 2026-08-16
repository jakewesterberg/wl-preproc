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
4.3), checked BEFORE routing and before any request body is ever read
(`do_GET`/`do_POST` both call `_authorized()` as their first line) -- an
unauthenticated caller must learn nothing about which paths exist, which
methods they answer, or have this host spend any effort parsing a body it
was never entitled to send in the first place.

The header is compared against the configured token with
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
passing by construction. The auth-scheme token (`Bearer`) is compared
case-insensitively (`scheme.lower()`) -- RFC 7235 section 2.1 defines
`auth-scheme` as a case-insensitive token, so `Authorization: bearer <token>`
must authenticate exactly as `Bearer` does.

**`_authorized()` itself must never raise.** It runs before either `try` in
`do_GET`/`do_POST`, so any exception it raises escapes both this module's
`422`/`500` handling AND `BaseHTTPRequestHandler`'s own error page entirely
-- straight into `socketserver`'s handler-error path, which prints a
traceback to stderr and closes the socket with no HTTP response sent at
all. A CANDIDATE token containing non-ASCII characters used to do exactly
that: `hmac.compare_digest` raises `TypeError: comparing strings with
non-ASCII characters is not supported` for either `str` argument containing
one, and both operands here were plain `str`. See `_authorized`'s own
docstring for the fix (encode both sides to `bytes` first) and
`server.py::serve`'s token validation, which now refuses to start at all on
a non-ASCII CONFIGURED token rather than accepting one that silently bricks
every request -- correct tokens included -- for as long as the process runs.

## Status codes -- every exception this boundary can see, and why

`POST /jobs` is the only endpoint that can fail on account of what the
*caller* sent, so it is the only endpoint with a `422`/`409` branch at all;
`GET /health` takes no body, so nothing about a request to it can be
"malformed" -- it can only succeed or fail on this host's own account (`500`).

| Source | Exception(s) | Status | Why |
|---|---|---|---|
| Auth | missing/malformed-scheme/wrong `Authorization` | `401`, generic body | section 4.3; never `403` -- absence of *any* credential and presence of the *wrong* one are the same failure from the caller's side, not two |
| Routing | path not `/health` or `/jobs` | `404` | design spec section 4.2 table: "Anything else is 404" |
| Routing | right path, wrong method (including `GET /jobs`, `POST /health`, and every verb NEITHER endpoint answers -- `PUT`/`DELETE`/`HEAD`/`OPTIONS`/`PATCH`/`TRACE`) | `405` | section 9's own named case; a path that exists but does not answer this verb is a different fact than a path that does not exist. Every verb is explicitly handled (see `do_PUT` and its five aliases below) rather than left to fall through to `BaseHTTPRequestHandler`'s own default, which answers unauthenticated and discloses this host's interpreter version -- see "The other six verbs" below |
| Body parsing | `json.JSONDecodeError` (bad JSON), `UnicodeDecodeError` (non-UTF-8 body), a non-digit or oversized `Content-Length` | `422` | all are `ValueError` -- confirmed directly against this project's installed Python/pydantic -- so a single `except ValueError` catches every one of them along with the two rows below, with no special-casing needed. See `_parse_job_request`'s own docstring for why `Content-Length` is validated with `str.isdigit()` rather than a bare `int(...)` |
| Body parsing | `pydantic.ValidationError` from `JobRequest.model_validate` | `422`, with `.errors()` as `detail` | design spec section 4.2: "A malformed body is 422 with the pydantic error, never a traceback." `ValidationError` **is** a `ValueError` subclass in pydantic v2 -- confirmed directly -- so it falls through the identical `except ValueError` as the row above; no separate branch was needed, only a richer body for this one type |
| `accept_fn` | `ValueError` (bad `selection` keys, an oversized subject, a block outside its montage window, an unknown block, a non-finite boundary, a malformed `session_datetime`, ...) | `422` | `responder/jobs.py::accept`'s own documented contract: "Raises `ValueError` for every rejection it owns." A well-formed JSON body asking for something `accept()` refuses is exactly as much "the caller's mistake" as a body that fails pydantic, so it shares the same status and the same catch clause |
| `accept_fn` | `ConflictError` (this module's own type -- `dj.DataJointError` translated at `server.py`'s seam) | `409` | See "Why `DataJointError` maps to `409`" below |
| `accept_fn` | `pymysql.err.DataError` / any other raw, untranslated `pymysql` exception, or a raw `dj.DataJointError` that reaches this module WITHOUT going through `server.py`'s translation seam | `500`, *handled* | Not a `ConflictError`, and this module still imports no DataJoint to recognise a raw `dj.DataJointError` by name -- both fall into the same generic, final `except Exception`, which is what makes either a **handled** 500 with a clean body, never an unhandled one with a real traceback |
| `accept_fn` / `health_fn` | anything else (`AttributeError`, `KeyError`, a genuine bug) | `500` | the blanket backstop. "Nothing may return a traceback to the client under any input" is unconditional, not "under every input this module's author thought of" |

**Why `DataJointError` maps to `409`, not `422` or `500` -- corrected
2026-08-16 by review.** This module's first draft mapped it to `500`, for
two reasons review found both wrong:

1. *"This module cannot special-case it without importing DataJoint."* True
   and irrelevant: `server.py` already imports DataJoint and already wraps
   `jobs.accept` in a lock-holding closure before this module ever sees the
   result (`locked_accept_fn`). That SAME seam can translate a real
   `dj.DataJointError` into `ConflictError` -- a plain exception THIS
   module defines with zero DataJoint dependency of its own -- so
   `handler.py` stays exactly as DataJoint-free as before while still
   distinguishing the case by name. See `server.py::_translate_accept_errors`.
2. *"A caller needs a NEW idempotency key, which `422`'s 'fix and resend'
   phrasing does not capture -- so it should be `500` instead."* Correct
   that resending is not the remedy; wrong that `500` is neutral about it.
   wl.works' own Plan 10 section 6.1 specifies the idempotency key is
   *"generated once when the confirmation dialog is accepted and reused
   across retries of that same intent, never regenerated per click."* Their
   client cannot mint a new key on its own initiative -- so a `500`, which
   invites exactly the retry their own design already commits to, would
   retry with the SAME key by construction and hit `_reject_key_reuse`'s
   refusal identically forever. `409 Conflict` (RFC 9110 section 15.5.10:
   "the request could not be completed due to a conflict with the current
   state of the target resource... the user might be able to resolve the
   conflict") is the status built for precisely this -- a request that
   cannot succeed as sent, whose remedy is outside the retry loop, giving
   wl.works' UI a basis to tell a human to reopen the confirmation dialog
   rather than to keep retrying automatically.

**Only through the translation seam.** `ConflictError` is a real, catchable
type in this module's own `except` clause, but nothing here ever raises it
directly, and nothing here recognises a raw `dj.DataJointError` by name
(this module still imports no DataJoint). An `accept_fn` that raised a raw
`dj.DataJointError` WITHOUT going through `server.py`'s translation --
`serve()` never does this, but a test can construct exactly that shape on
purpose -- falls to the generic `except Exception` below and becomes a
handled `500`, same as any other untranslated exception. That is deliberate
defense in depth, not a second documented mapping: the one correct,
intended path is `dj.DataJointError` -> `ConflictError` -> `409`, and it
exists only because `server.py` performs that translation before this
module ever sees the exception.

**The other six verbs.** `GET`/`POST` are the only two `do_*` methods
`BaseHTTPRequestHandler` would otherwise find defined here; every other
verb (`PUT`, `DELETE`, `HEAD`, `OPTIONS`, `PATCH`, `TRACE`, or anything else
a client sends as a request line's method token) falls through to its own
`send_error(501, "Unsupported method (...)")`, which runs BEFORE this
module's auth check ever gets a chance to run, and which sends an HTML
body containing `self.version_string()` -- this host's exact interpreter
patch version -- in both the response body and every response's `Server`
header, unauthenticated. From a module whose sibling frozen contract
(`contracts/protocol.py`) states "We refuse to emit markup at all," an
HTML error page is already wrong in kind; doing it without checking the
bearer token first, and with a version-fingerprintable header on literally
every response including a `401`, compounds it. `do_PUT` and its five
aliases below run this module's own auth-then-route logic for every one of
those six verbs instead, and `server_version`/`sys_version` are both
emptied so `version_string()` (used by the `Server` header on every
response, this file's own included) discloses nothing.

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
# by both do_GET and do_POST (and do_PUT's five aliases) to decide 404 (path
# unknown) vs. 405 (path known, wrong verb) with one shared table rather
# than several independently maintained if/elif chains that could drift
# apart on a third endpoint.
_PATH_METHODS = {_HEALTH_PATH: "GET", _JOBS_PATH: "POST"}

# A single, fixed, no-detail body for every authentication failure -- missing
# header, wrong scheme, wrong token -- so "which part was wrong" is
# structurally unanswerable from the response alone (design spec section
# 4.3, section 9's own named test), not merely unanswered by convention.
_UNAUTHORIZED_BODY = {"error": "unauthorized"}

# Content-Length cap -- review Important 4. A real JobRequest's metadata
# (a session's montage boundaries and block list) is at most a few hundred
# small JSON entries; 10 MB is generously larger than any legitimate body
# while still being far too small for reading up to that many bytes to
# itself become the resource-exhaustion problem this cap exists to prevent.
_MAX_CONTENT_LENGTH = 10_000_000


class ConflictError(Exception):
    """Raised by an `accept_fn` -- via `server.py`'s translation seam,
    never directly by this DataJoint-free module -- for a request that
    conflicts with state already on record: today, specifically,
    `schema/request.py::_reject_key_reuse` refusing an idempotency key
    reused for materially different content. Maps to `409`, not `422` or
    `500` -- see the module docstring's "Why `DataJointError` maps to
    `409`" section for the full reasoning.

    Deliberately a plain `Exception` subclass with no DataJoint dependency
    of its own, defined HERE rather than in `server.py`: this module owns
    the vocabulary of exceptions it maps to specific status codes (a bare
    `ValueError` -> `422`, this -> `409`, anything else -> `500`), and
    `server.py` imports this one name to translate a real
    `dj.DataJointError` into it at the one seam permitted to know both
    vocabularies.
    """


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

        # Review Important 5: BaseHTTPRequestHandler's version_string() joins
        # these two into the Server header sent on EVERY response (this
        # file's own 401/404/405/409/422/500 included) and into the body of
        # its own default "unsupported method" error page. Emptying both is
        # the documented, minimal way to stop either from disclosing this
        # host's exact interpreter patch version to an unauthenticated caller.
        server_version = ""
        sys_version = ""

        # -- auth --------------------------------------------------------

        def _bearer_token(self) -> str | None:
            """The token in `Authorization: Bearer <token>`, or `None` for
            anything else -- header absent, wrong scheme, or an empty
            token. `None` is the one value `_authorized` below never hands
            to `hmac.compare_digest`, which raises `TypeError` on `None`
            rather than returning `False`.

            The scheme token is matched case-INsensitively
            (`scheme.lower() != "bearer"`) -- review Minor: RFC 7235
            section 2.1 defines `auth-scheme` as a case-insensitive token,
            so `Authorization: bearer <token>` (lowercase) must authenticate
            exactly as `Bearer` does, not be treated as a malformed scheme.
            """
            value = self.headers.get("Authorization")
            if value is None:
                return None
            scheme, _, candidate = value.partition(" ")
            if scheme.lower() != "bearer" or not candidate:
                return None
            return candidate

        def _authorized(self) -> bool:
            """`True` only for a `Bearer` token matching `self._token` under
            `hmac.compare_digest`.

            Both sides are encoded to UTF-8 `bytes` before comparison --
            review Critical. `hmac.compare_digest` raises `TypeError:
            comparing strings with non-ASCII characters is not supported`
            when either `str` argument contains one, confirmed directly on
            this project's Python 3.11 (and reported reproduced on 3.13 and
            3.14). This method is called at the very first line of
            `do_GET`/`do_POST`, OUTSIDE every `try` in this file, so that
            `TypeError` used to escape uncaught -- past this module's own
            `401`/`422`/`500` handling entirely, into `socketserver`'s
            handler-error path: a traceback to stderr, the socket closed,
            and the client seeing a bare connection reset rather than any
            HTTP response. Three things broke on that one input: it was
            not `401` (section 4.3's own promise for a wrong token), it was
            not the documented `500` backstop either (both `except` blocks
            in this file begin AFTER `_authorized()` returns), and it
            proved the module docstring's "no traceback under any input"
            claim false for exactly the input class that claim exists to
            cover. `bytes` values carry no such restriction -- confirmed
            directly -- so encoding here closes the gap structurally rather
            than special-casing non-ASCII candidates one input at a time.

            The WORSE direction was operational, not a client mistake: a
            non-ASCII CONFIGURED token (an en-dash pasted from a document,
            say) made every request fail this exact way, correct token
            included, silently and permanently for as long as the process
            ran. `server.py::serve` now refuses to start at all on such a
            token rather than accepting one that bricks the endpoint with
            no HTTP-level error to diagnose.
            """
            candidate = self._bearer_token()
            if candidate is None:
                return False
            return hmac.compare_digest(candidate.encode("utf-8"), self._token.encode("utf-8"))

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
            third endpoint only has to be added in one place. Also what
            `do_PUT` and its five aliases fall through to for every verb
            neither endpoint answers -- every one of those always resolves
            to 404 or 405 here, never `None`, since `_PATH_METHODS` never
            maps any path to any of those six verbs.
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
                # response.model_dump_json() moved INSIDE this try -- review
                # Minor. It previously ran after the except/return below,
                # so a hypothetical serialisation failure here would have
                # escaped this module's own "no traceback under any input"
                # backstop one line after the comment that states it,
                # trusting an invariant ("HealthResponse always serialises
                # cleanly") the surrounding code otherwise declines to trust.
                body = response.model_dump_json().encode("utf-8")
            except Exception as exc:  # noqa: BLE001 -- see module docstring's table
                # No ValueError/422 branch here on purpose: GET carries no
                # body, so nothing about this request can be malformed --
                # every failure on this path is this host's own, hence
                # always 500, never 422.
                self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
                return
            # model_dump_json() is HealthResponse's own canonical
            # serialisation -- the same one `wlpp schemas export` derives
            # docs/schemas/health_response.json from -- so this writes it
            # directly rather than round-tripping it through _send_json's
            # dict-shaped path.
            self._write_response(200, body)

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
            except ConflictError as exc:
                # Review Important 3 -- see module docstring's "Why
                # DataJointError maps to 409" section. ConflictError and
                # ValueError both inherit only from Exception, so their
                # relative order below does not change which one catches
                # what; it is ordered to match the docstring table's own
                # row order, nothing more.
                self._send_json(409, {"error": str(exc)})
            except ValueError as exc:
                # json.JSONDecodeError, UnicodeDecodeError, a non-digit or
                # oversized Content-Length, pydantic.ValidationError, and
                # jobs.accept's own ValueError are ALL ValueError subclasses
                # (confirmed directly against this project's installed
                # Python/pydantic) -- one branch, not several. See the
                # module docstring's table.
                self._send_json(422, _error_body(exc))
            except Exception as exc:  # noqa: BLE001 -- see module docstring's table
                # dj.DataJointError NOT translated to ConflictError (defense
                # in depth -- see "Only through the translation seam" in the
                # module docstring), a raw untranslated pymysql error, or a
                # genuine bug -- all land here, all become a HANDLED 500
                # with a safe body.
                self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
            else:
                self._send_json(200, {"activation": result, "accepted": True})

        def _parse_job_request(self) -> JobRequest:
            """The request body as a validated `JobRequest`. Raises
            `ValueError` (via this method's own `Content-Length` checks,
            `json.loads`, `bytes.decode`, or `JobRequest.model_validate`)
            for anything malformed -- never anything else, so `do_POST`'s
            single `except ValueError` is a complete catch for every way
            this can fail.

            `Content-Length` is validated with `str.isdigit()` before
            `int(...)` is ever called on it, and the resulting integer is
            capped at `_MAX_CONTENT_LENGTH` before `self.rfile.read(...)`
            is ever called with it -- review Important 4, three real,
            ordinary-looking inputs that used to each hang this request's
            handler thread forever (never responding, not even with an
            error) rather than reaching any of this module's status-code
            handling at all:

            - `Content-Length: -1` -- `int("-1")` succeeds (`-1`), and
              `self.rfile.read(-1)` reads until the PEER closes the
              connection, which a live HTTP client never does on its own.
            - `Content-Length: 1_2` -- `int("1_2") == 12` (Python's
              underscore-in-int-literal grammar applies to `int(str)` too),
              silently reinterpreting a header that is not valid HTTP
              (whose grammar is `Content-Length = 1*DIGIT`, no underscore)
              as a request to read 12 bytes of what is actually a 7-byte
              body -- `read()` then blocks waiting for 5 bytes that are
              never coming.
            - `Content-Length: 100000000000000000000` (10**20) -- `int(...)`
              itself succeeds (Python integers are arbitrary-precision), but
              `self.rfile.read(...)` converts its argument to a C
              `Py_ssize_t` internally and raises `OverflowError` for a value
              past `sys.maxsize` -- confirmed directly. A merely large but
              in-range value (`8000000000`, 8 GB) instead raises `OSError`
              from the underlying buffered reader trying to allocate that
              much.

            `str.isdigit()` rejects the first two outright (a leading `-`
            and an underscore are both not digits) before `int()` is ever
            called; the cap rejects the third and fourth before
            `self.rfile.read(...)` is ever called with an attacker- or
            bug-controlled size, regardless of whether that size would have
            raised, hung, or merely consumed unreasonable memory. A missing
            header reads as `"0"` (`self.headers.get`'s own default, a
            digit string) -- an empty body then fails at `json.loads(b"")`,
            still a `ValueError`, just one step later.
            """
            raw_length = self.headers.get("Content-Length", "0")
            if not raw_length.isdigit():
                raise ValueError(
                    f"Content-Length {raw_length!r} is not a valid non-negative integer"
                )
            length = int(raw_length)
            if length > _MAX_CONTENT_LENGTH:
                raise ValueError(
                    f"Content-Length {length} exceeds the {_MAX_CONTENT_LENGTH}-byte limit"
                )
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw.decode("utf-8"))
            return JobRequest.model_validate(payload)

        # Review Important 5: every verb neither endpoint answers still
        # authenticates FIRST, exactly like do_GET/do_POST -- the
        # alternative is BaseHTTPRequestHandler's own default "unsupported
        # method" handling, which runs before any subclass code at all and
        # sends an UNAUTHENTICATED, non-JSON HTML error page disclosing
        # this host's exact interpreter patch version (see the module
        # docstring's "The other six verbs"). Every one of these always
        # ends in 401, 404 or 405, never 200: _PATH_METHODS never maps any
        # path to any of these six verbs, so _route_or_none can never
        # return None for them.
        def do_PUT(self) -> None:
            if not self._authorized():
                self._send_json(401, _UNAUTHORIZED_BODY)
                return
            self._route_or_none(self.command)

        do_DELETE = do_PUT
        do_HEAD = do_PUT
        do_OPTIONS = do_PUT
        do_PATCH = do_PUT
        do_TRACE = do_PUT

        # log_message is deliberately NOT overridden: BaseHTTPRequestHandler's
        # default writes one access-log line per request to stderr, which is
        # the closest thing a "no dedicated sysadmin" lab server (design spec
        # section 4.1) gets to an access log for free. Nothing in this task
        # asks for quieter logs, and pytest already captures stderr per test
        # and shows it only on failure -- exactly when an access-log line is
        # useful, not when it is noise.

    return ResponderHandler
