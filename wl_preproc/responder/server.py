"""`ThreadingHTTPServer` wiring, and the one lock. Design spec section 4.4,
6.4. The only module in `responder/` that starts a server; `handler.py`
supplies the class, `health.py`/`jobs.py` supply the two callables, this
module ties them to a real socket and a real bearer token read from
configuration rather than the repository (design spec section 4.3).

## The lock is a correctness requirement, not only a thread-safety one

`ThreadingHTTPServer` hands every request its own thread. Naively, that
would mean two concurrent requests -- a `GET /health` and a `POST /jobs`, or
two `POST /jobs` calls -- touching the database from two different threads
at once. **This module serialises all of that behind one `threading.Lock`
instead of ever letting that happen**, for two independent reasons, both
verified this phase rather than assumed:

1. **DataJoint connections are not shareable across threads.** `dj.conn()`
   is a per-process singleton (`datajoint.instance`'s own docstring: "returns
   the singleton connection"), and handing two threads into it concurrently
   is not merely "racy" in the ordinary sense -- 1c-2's final review lost an
   afternoon to a real four-thread probe that produced
   `InternalError: Packet sequence number wrong`, and that failure was
   **misdiagnosed as an ordinary race twice** before redoing the same probe
   with separate OS *processes* (which do not share a connection) showed it
   never reproduces that way, confirming the connection object itself, not
   application logic, was what could not survive concurrent threads.
   DataJoint 2.3.x does carry an opt-in guard for exactly this
   (`dj.conn()` calls `_check_thread_safe()`, which raises `ThreadSafetyError`
   instead of silently handing out the singleton) -- but it is gated on the
   `DJ_THREAD_SAFE` environment variable, defaults to disabled, and nothing
   in this project sets it (confirmed directly: grepping the repo finds no
   reference, and calling `_check_thread_safe()` in this project's own
   configuration does not raise). So today there is no safety net below this
   lock at all; it is the only thing standing between two threads and one
   connection, not a second layer behind one DataJoint already provides.
2. **`responder/jobs.py::accept` validates an in-memory candidate before
   writing it** (its own module docstring, review round 1's C1 fix): it
   builds every `Montage`/`Block` row it would write, checks each one
   against what is already on record plus its own other candidates, and
   only then writes anything. That closes the earlier defect where a
   rejected request could permanently plant the very row that caused its
   own rejection -- but design spec section 6.4 records what it opens
   instead: "validated" and "what is on file" can **diverge** if a second,
   concurrent write lands in the gap between the check and the write.
   Reproduced deterministically (a rival connection inserting an
   out-of-window boundary between the two) -- not a hypothetical. Section
   6.4 also records why this cannot be fixed by re-ordering: validate-after
   -write is safe against exactly this race and unsafe against a bad first
   payload (that is the defect C1 already fixed); validate-before-write is
   the reverse; **neither ordering is safe against both without the ability
   to undo a write, and no undo mechanism exists anywhere in this
   codebase.** A single process-wide lock does not need one -- it removes
   the second writer instead of trying to recover from it, by making every
   `accept()` call within this one responder process run to completion,
   database work included, before the next one starts.

Reason 2 is why this lock **must not be "optimised" into a connection pool
or a per-request connection** later, however tempting that looks purely from
a throughput angle: a pool reintroduces the second concurrent writer that
closes the section 6.4 race, and does so *silently* -- nothing about a
connection pool looks wrong locally, the failure only shows up as a
corrupted `Block` boundary sometime after two overlapping calls land. One
known client (wl.works) polling every few minutes needs none of the
throughput a pool would buy (design spec section 4.4), so there is no
performance argument on the other side of this trade to begin with. Widening
this lock's scope to more than one *process* against one schema is a real
future need (design spec section 12, the automatic canonical trigger) --
but section 6.4 is explicit that whatever closes that must close this race
first, by some other means; it is not this lock's job to grow into that.

`GET /health`'s reads take the same lock as `POST /jobs`'s writes (design
spec section 4.4: "The health endpoint's reads and the job endpoint's writes
take the same lock") -- reason 1 above applies to any concurrent use of the
one shared connection, reads included, not only to writes.
"""

from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from wl_preproc.responder import health, jobs
from wl_preproc.responder.handler import make_handler
from wl_preproc.schema import DEFAULT_PREFIX


def serve(
    port: int,
    token: str,
    root: Path,
    prefix: str = DEFAULT_PREFIX,
    ready: threading.Event | None = None,
) -> None:
    """Run the responder in the foreground. Binds every interface (`("",
    port)`) -- this host is reachable only from the lab LAN wl.works polls
    over (design spec section 2/11.1); it does not sit behind a WireGuard
    interface of its own the way wl.works does.

    Blocks forever in `ThreadingHTTPServer.serve_forever()`. `ready`, when
    given, is set once the socket is bound and listening but before the
    accept loop starts -- a caller running this in a background thread (as
    `tests/responder/test_http.py` does, to exercise this exact function
    rather than only `make_handler`) can wait on it instead of polling or
    guessing when the port is live. `port=0` asks the OS for an ephemeral
    one, which is why `ready` alone does not tell a waiter *which* port --
    a caller needing that reads it off the constructed server, not off this
    function's return value (`None`).
    """
    lock = threading.Lock()

    def locked_health_fn():
        # See this module's docstring: the same lock every write below
        # takes, and for the identical reason (one shared DataJoint
        # connection, not per-request database traffic).
        with lock:
            return health.build_health(root, prefix=prefix)

    def locked_accept_fn(request):
        with lock:
            return jobs.accept(request, prefix=prefix)

    handler_cls = make_handler(token, locked_health_fn, locked_accept_fn)
    httpd = ThreadingHTTPServer(("", port), handler_cls)
    try:
        if ready is not None:
            ready.set()
        httpd.serve_forever()
    finally:
        httpd.server_close()
