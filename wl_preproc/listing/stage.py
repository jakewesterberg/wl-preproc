"""The daemon's listing stage: `GET /sessions`' one writer (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 2.1).

**A change is logged when a session's entry changes.** Every pass assembles
the entry of every session the event stage has done, from one read of each
table for all of them (the listing's M9), hashes it, and appends
an `ingest.SessionChange` when the hash differs from the session's last. So a
fact that arrives later -- a timing tier, a probe census -- lists the session
again, without the stage that produced it knowing about the listing.

**One writer, under its lock** (`nwb/lock.py`, `what="listing"`). A sequence
number allocated first but committed last is a hole a reader's cursor can
step past and never see; one writer at a time has none.
"""

from __future__ import annotations

import datetime
import hashlib
import json

from wl_preproc.listing.entry import build_entry, gather_all


def digest(entry: dict) -> str:
    """The entry's sha256, over its JSON with keys sorted."""
    return hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def listable() -> list[dict]:
    """The sessions the event stage has done: their runs, if any, are measured."""
    from wl_preproc.schema import ingest, pipeline

    return sorted((pipeline.Session & ingest.Ingestion & pipeline.event.BehaviorRecording).keys(),
                  key=lambda key: (key["subject"], key["session_datetime"]))


def run_stage() -> tuple[int, list[str]]:
    """Append a change for each session whose entry changed. Returns
    `(changes appended, per-session failures)`, as every stage of
    `daemon.run_once` does."""
    from wl_preproc.schema import ingest

    try:
        changes, keys = ingest.SessionChange.to_dicts(order_by="change_seq"), listable()
        facts = gather_all(keys)
    except Exception as exc:  # the daemon's later stages must still run
        return 0, [f"SessionChange: the listing stage could not read the sessions: {type(exc).__name__}: {exc}"]
    last = {}
    for row in changes:
        last[(row["subject"], row["session_datetime"])] = row["digest"]
    appended, errors = 0, []
    for key in keys:
        try:
            entry_digest = digest(build_entry(facts[(key["subject"], key["session_datetime"])]))
            if last.get((key["subject"], key["session_datetime"])) != entry_digest:
                ingest.SessionChange.insert1({**key, "digest": entry_digest, "changed_at": _now()})
                appended += 1
        except Exception as exc:  # one session must not stop the others
            errors.append(f"SessionChange {key}: {exc}")
    return appended, errors


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
