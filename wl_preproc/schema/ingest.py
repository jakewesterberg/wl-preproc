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

`Ingestion.topology`'s comment below promises "the full per-system state map,
read as a unit" — true for a session landed by exactly one call, which is
every session absent an actual race. `wl_preproc/ingest/landing.py`'s
`land_session` is idempotent by construction rather than locked (design spec
section 13 excludes a lock deliberately), and after a genuine race with
differing topology this column freezes at whichever call landed first while
`core.AcquisitionSystem` keeps unioning in every system any racing call ever
saw — so this column can go on describing a system as absent after a real
`AcquisitionSystem` row for it already exists. See `landing.py`'s module
docstring for the mechanism. Read `topology` as a unit for the ordinary,
race-free case; it is not a live cross-table guarantee.
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
        # The identical shape one column over: Ingestion.session_dir is
        # varchar(255), and a storage root nested deep enough produces a
        # session_dir string this long with nothing else about the session
        # being wrong at all. Named and checked at source (Task 8's watcher,
        # `landing.INGESTION_SESSION_DIR_MAX_LEN`) specifically so a
        # completely valid session does not land in `unexpected_failure`
        # forever -- session_dir does not shorten between polls, so an
        # unclassified catch-all quarantine would never resolve on its own,
        # unlike a genuinely transient condition.
        "session_dir_unrepresentable",
        # The watcher's outer exception boundary (wl_preproc/ingest/watcher.py,
        # `_scan_one`): every failure above is a known, classified shape this
        # pipeline anticipates and names. This one is not -- a session-params
        # dict with mixed int/str keys that json.dumps(sort_keys=True) cannot
        # sort, a genuinely unclassified DataJoint fault, anything else that
        # slips past every earlier, specific check. Recorded rather than
        # raised, so it costs one session, not every session in the same
        # scan -- and recorded under its own name rather than folded into
        # `params_invalid` or another existing reason, because collapsing an
        # unanticipated failure into a reason that implies a specific, known
        # cause would misdiagnose it the next time someone reads this table.
        "unexpected_failure",
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
