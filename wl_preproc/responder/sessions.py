"""What `GET /sessions` does (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 2.1).

`handler.py` owns HTTP and imports no DataJoint; this module owns the
database, as `nwb.py` does for `GET /nwb`. **It never writes**: the daemon's
listing stage is `ingest.SessionChange`'s one writer. An entry is the session
as it stands now, assembled by the same `session_entry` the stage hashes.
"""

from __future__ import annotations

from wl_preproc.contracts.protocol import SessionListing
from wl_preproc.schema import DEFAULT_PREFIX


def list_sessions(since: int | None, prefix: str = DEFAULT_PREFIX) -> dict:
    """Every session whose entry changed after `since` (all of them when
    None), in the order of their latest change, and the cursor to send next."""
    from wl_preproc.listing import entry
    from wl_preproc.schema import core, ephys, ingest, timebase

    for module in (ingest, core, ephys, timebase):
        module.activate(prefix=prefix)
    changes = ingest.SessionChange if since is None else ingest.SessionChange & f"change_seq > {int(since)}"
    latest: dict[tuple, int] = {}
    for change in changes.proj("subject", "session_datetime").to_dicts():
        session = (change["subject"], change["session_datetime"])
        latest[session] = max(latest.get(session, 0), change["change_seq"])
    cursor = max(latest.values(), default=since or 0)
    sessions = []
    for (subject, moment), _seq in sorted(latest.items(), key=lambda item: item[1]):
        try:
            sessions.append(entry.session_entry({"subject": subject, "session_datetime": moment}))
        except Exception as exc:
            # Still a 500, which wl.works retries; naming the session is what
            # lets someone fix it (final review M5).
            raise RuntimeError(f"session {subject} at {moment.isoformat()}: {type(exc).__name__}: {exc}") from exc
    return SessionListing.model_validate({"cursor": cursor, "sessions": sessions}).model_dump(mode="json")
