"""`GET /sessions` over real HTTP, with a stand-in callable (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 2.1). The
database half is in `tests/schema/test_session_listing.py`."""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from tests.responder.test_http import TOKEN, _health_ok, _request, _unused
from wl_preproc.responder.handler import make_handler


@pytest.fixture
def serve_sessions():
    started = []

    def _start(sessions_list_fn=_unused, *, with_sessions=True):
        handler_cls = (make_handler(TOKEN, _health_ok, _unused, sessions_list_fn=sessions_list_fn)
                       if with_sessions else make_handler(TOKEN, _health_ok, _unused))
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield _start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def test_the_listing_is_behind_the_token_and_takes_an_optional_cursor(serve_sessions):
    calls = []
    base = serve_sessions(lambda since: calls.append(since) or {"cursor": 3, "sessions": []})
    assert _request(f"{base}/sessions", method="GET")[0] == 401
    status, body = _request(f"{base}/sessions", method="GET", token=TOKEN)
    assert (status, json.loads(body)) == (200, {"cursor": 3, "sessions": []})
    assert _request(f"{base}/sessions?since=2", method="GET", token=TOKEN)[0] == 200
    assert calls == [None, 2]


@pytest.mark.parametrize("query", ["since=x", "since=-1", "since=1&since=2", "other=1", "since="])
def test_a_cursor_that_is_not_one_non_negative_integer_is_422(serve_sessions, query):
    base = serve_sessions()
    status, body = _request(f"{base}/sessions?{query}", method="GET", token=TOKEN)
    assert status == 422 and "since" in json.loads(body)["error"]


def test_a_host_without_the_listing_does_not_answer_it(serve_sessions):
    base = serve_sessions(with_sessions=False)
    assert _request(f"{base}/sessions", method="GET", token=TOKEN)[0] == 404
    assert _request(f"{base}/sessions?since=1", method="GET", token=TOKEN)[0] == 404


def test_only_get_answers_it_and_a_query_elsewhere_is_still_404(serve_sessions):
    base = serve_sessions(lambda since: {"cursor": 0, "sessions": []})
    assert _request(f"{base}/sessions", method="POST", token=TOKEN, body={})[0] == 405
    assert _request(f"{base}/health?since=1", method="GET", token=TOKEN)[0] == 404


def test_a_failure_behind_it_is_a_clean_500(serve_sessions):
    def broken(_since):
        raise RuntimeError("the database went away")

    status, body = _request(f"{serve_sessions(broken)}/sessions", method="GET", token=TOKEN)
    assert (status, json.loads(body)) == (500, {"error": "RuntimeError: the database went away"})
