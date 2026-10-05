"""One wlpp process at a time builds, corrects, publishes and moves NWB files
(the final review of NWB publishing, M4).

`daemon.run_once`'s own docstring says nothing enforces a single runner, and
a cron pass that copies 25 GB files over the network can outlive its
interval. Two processes in the NWB stages at once would copy the same file
to the same `.partial`, record it twice, and leave holes in `GET /nwb`'s
cursor (a sequence number allocated first but committed last). A MySQL
named lock, held by one database session for the stages' duration, lets
the second process skip them and say why; the lock goes with the session if
the process dies.

**The listing stage takes its own lock the same way** (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 2.1): it is
`GET /sessions`' one writer, and two would leave holes in that cursor too.
Neither lock is ever waited for, so holding both in one pass cannot
deadlock."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator


class Busy(Exception):
    """Another wlpp process holds a stage's lock."""


# Each lock's name in a message, and what is left to the process holding it.
_LOCKS = {"nwb": ("NWB", "the NWB stages are"), "listing": ("listing", "the listing stage is")}


def lock_name(prefix: str, what: str = "nwb") -> str:
    """One lock per database prefix: a test suite's and a deployment's do
    not collide. MySQL allows 64 characters."""
    return f"wlpp_{what}_{prefix}"[:64]


@contextlib.contextmanager
def exclusive(prefix: str, what: str = "nwb") -> Iterator[None]:
    """Hold the `what` lock, or raise `Busy` at once: never wait for it."""
    import datajoint as dj

    label, left = _LOCKS[what]
    connection = dj.conn()
    name = lock_name(prefix, what)
    if connection.query("SELECT GET_LOCK(%s, 0)", args=(name,)).fetchone()[0] != 1:
        raise Busy(f"another wlpp process holds the {label} lock {name}; {left} left to it")
    try:
        yield
    finally:
        connection.query("SELECT RELEASE_LOCK(%s)", args=(name,))
