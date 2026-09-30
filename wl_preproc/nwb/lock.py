"""One wlpp process at a time builds, publishes and moves NWB files (the
final review of NWB publishing, M4).

`daemon.run_once`'s own docstring says nothing enforces a single runner, and
a cron pass that copies 25 GB files over the network can outlive its
interval. Two processes in the NWB stages at once would copy the same file
to the same `.partial`, record it twice, and leave holes in `GET /nwb`'s
cursor (a sequence number allocated first but committed last). A MySQL
named lock, held by one database session for the stages' duration, lets
the second process skip them and say why; the lock goes with the session if
the process dies."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator


class Busy(Exception):
    """Another wlpp process holds the NWB lock."""


def lock_name(prefix: str) -> str:
    """One lock per database prefix: a test suite's and a deployment's do
    not collide. MySQL allows 64 characters."""
    return f"wlpp_nwb_{prefix}"[:64]


@contextlib.contextmanager
def exclusive(prefix: str) -> Iterator[None]:
    """Hold the NWB lock, or raise `Busy` at once: never wait for it."""
    import datajoint as dj

    connection = dj.conn()
    name = lock_name(prefix)
    if connection.query("SELECT GET_LOCK(%s, 0)", args=(name,)).fetchone()[0] != 1:
        raise Busy(f"another wlpp process holds the NWB lock {name}; the NWB stages are left to it")
    try:
        yield
    finally:
        connection.query("SELECT RELEASE_LOCK(%s)", args=(name,))
