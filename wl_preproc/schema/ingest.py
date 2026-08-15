# wl_preproc/schema/ingest.py
"""What was ingested, and what refused to be.

`Session` records when the recording *happened*; nothing recorded when it was
*ingested*, and the daily report's first line is "ingested in the last 24 h".
Deriving that from `session_datetime` answers a different question, and answers
it wrongly for any backfill.

`Quarantine` is keyed on the session **directory path** rather than on
(subject, session_datetime). The worst failure this pipeline has is an
unparseable manifest, and the manifest is precisely what yields that key — so a
key-addressed row cannot represent the failures that most need recording. The
path is available in every case, because the watcher is standing in it.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.schema import DEFAULT_PREFIX, pipeline

schema = dj.Schema()

QUARANTINE_REASONS: frozenset[str] = frozenset(
    {
        "manifest_invalid",
        "manifest_schema_version",
        "session_id_mismatch",
        "checksum_mismatch",
        "params_invalid",
        # element-animal declares `subject : varchar(8)`. The manifest's
        # `subject` is an unconstrained `str`, so a longer name validates
        # cleanly and then fails at the insert. Caught as a manifest problem
        # rather than surfacing as a MySQL error mid-landing.
        "subject_unrepresentable",
    }
)

_REASON_ENUM = "enum(" + ",".join(f"'{r}'" for r in sorted(QUARANTINE_REASONS)) + ")"


@schema
class Ingestion(dj.Manual):
    definition = """
    # One row per session successfully landed. Key: (subject, session_datetime).
    -> pipeline.Session
    ---
    ingested_at   : datetime      # when this row was written, not when the session ran
    session_dir   : varchar(255)  # where it came from; provenance, never a key
    integrity     : enum('verified','declared_only','skipped')
    topology      : <blob>        # the full per-system state map, read as a unit
    manifest_hash : varchar(64)   # blake3 of the manifest FILE'S BYTES
    """


@schema
class Quarantine(dj.Manual):
    definition = f"""
    # A session directory that failed validation. Key: session_dir.
    # NOT keyed on (subject, session_datetime) — see this module's docstring.
    session_dir  : varchar(255)
    ---
    failed_at    : datetime
    reason       : {_REASON_ENUM}
    detail       : <blob>
    subject=null    : varchar(32)   # best effort; may be unparseable
    session_dt=null : datetime      # best effort; may be unparseable
    """


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}ingest`. Idempotent."""
    pipeline.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}ingest", create_tables=True)
