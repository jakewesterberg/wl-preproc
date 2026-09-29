"""`GET /nwb` and `PUT /nwb/active` over real HTTP, with stand-in callables
(design spec `2026-09-29-nwb-publishing-design.md` section 6). The database
half is exercised end to end in `tests/schema/test_nwb_build.py`."""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from tests.responder.test_http import TOKEN, _health_ok, _request, _unused
from wl_preproc.responder.handler import make_handler

KEY = {"subject": "monk01", "session_datetime": "2027-01-12T09:00:00", "montage_id": 0, "activation_id": 0}


@pytest.fixture
def serve_nwb():
    started = []

    def _start(nwb_list_fn=_unused, nwb_active_fn=_unused, *, with_nwb=True):
        handler_cls = (make_handler(TOKEN, _health_ok, _unused, nwb_list_fn, nwb_active_fn) if with_nwb
                       else make_handler(TOKEN, _health_ok, _unused))
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield _start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def test_the_listing_is_behind_the_token_and_takes_an_optional_cursor(serve_nwb):
    calls = []
    base = serve_nwb(nwb_list_fn=lambda since: calls.append(since) or {"cursor": 7, "files": []})
    assert _request(f"{base}/nwb", method="GET")[0] == 401
    status, body = _request(f"{base}/nwb", method="GET", token=TOKEN)
    assert (status, json.loads(body)) == (200, {"cursor": 7, "files": []})
    assert _request(f"{base}/nwb?since=5", method="GET", token=TOKEN)[0] == 200
    assert calls == [None, 5]


@pytest.mark.parametrize("query", ["since=x", "since=-1", "since=1&since=2", "other=1", "since=",
                                   "since=%C2%B2", "since=%D9%A3"])
def test_a_cursor_that_is_not_one_non_negative_integer_is_422(serve_nwb, query):
    base = serve_nwb(nwb_list_fn=_unused)
    status, body = _request(f"{base}/nwb?{query}", method="GET", token=TOKEN)
    assert status == 422 and "since" in json.loads(body)["error"]


def test_the_active_set_is_accepted_whole(serve_nwb):
    received = []

    def active(request):
        received.append(request)
        return {"accepted": len(request.activations), "unknown": []}

    base = serve_nwb(nwb_active_fn=active)
    status, body = _request(f"{base}/nwb/active", method="PUT", token=TOKEN,
                            body={"activations": [KEY], "requested_by": "jw"})
    assert (status, json.loads(body)) == (202, {"accepted": 1, "unknown": []})
    (request,) = received
    assert request.requested_by == "jw" and request.activations[0].subject == "monk01"


@pytest.mark.parametrize("body", [{"activations": [KEY], "extra": 1}, {"activations": [{**KEY, "activation_id": -1}]},
                                  b"not json"])
def test_a_malformed_active_set_is_422(serve_nwb, body):
    base = serve_nwb(nwb_active_fn=_unused)
    assert _request(f"{base}/nwb/active", method="PUT", token=TOKEN, body=body)[0] == 422


def test_a_failure_behind_either_endpoint_is_a_clean_500(serve_nwb):
    def broken(*_args):
        raise RuntimeError("the database went away")

    base = serve_nwb(nwb_list_fn=broken, nwb_active_fn=broken)
    status, body = _request(f"{base}/nwb", method="GET", token=TOKEN)
    assert (status, json.loads(body)) == (500, {"error": "RuntimeError: the database went away"})
    assert _request(f"{base}/nwb/active", method="PUT", token=TOKEN, body={"activations": []})[0] == 500


def test_the_older_endpoints_answer_as_before(serve_nwb):
    """A query string anywhere but `/nwb` is still a path this host does not
    answer, PUT anywhere but `/nwb/active` is still 405, and a handler built
    without the NWB callables has no NWB paths at all."""
    base = serve_nwb()
    assert _request(f"{base}/health?x=1", method="GET", token=TOKEN)[0] == 404
    assert _request(f"{base}/health", method="PUT", token=TOKEN)[0] == 405
    assert _request(f"{base}/nwb/active", method="GET", token=TOKEN)[0] == 405
    bare = serve_nwb(with_nwb=False)
    assert _request(f"{bare}/nwb", method="GET", token=TOKEN)[0] == 404
    assert _request(f"{bare}/nwb/active", method="PUT", token=TOKEN, body={"activations": []})[0] == 404
