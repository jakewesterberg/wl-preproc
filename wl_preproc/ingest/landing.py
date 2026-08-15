"""The only module in `ingest` that touches DataJoint.

Every write is idempotent by construction rather than guarded by a lock.
`daemon.py` already records that no lock exists -- "nothing here enforces the
single-runner invariant ... no lock file, no advisory lock" -- and a watcher run
from cron inherits that. Adding a lock would need crash cleanup, which is the
same stale-reservation problem `reap_stale_jobs` exists for; solving it twice
differently is worse than solving it once.

This is also wl.works' most-repeated defect lesson applied here: check-then-write
is "the single largest source of real defects", and the answer is an
unconditional idempotent write rather than a read followed by a conditional one.

Two idempotence shapes coexist below, deliberately different:

- `land_session` writes rows via `skip_duplicates=True`. The first call to
  reach a given key wins; every later call for the *same* key -- whether a
  genuine retry or a second watcher racing the first -- is a silent no-op,
  even where its payload would have differed (different `topology`, a
  different `manifest_hash`). `Ingestion`'s content is therefore frozen at
  whichever call inserts it first. `AcquisitionSystem` has no non-key
  attributes at all -- `system` is the only column beyond the foreign key --
  so for that table specifically "different content" can only mean a
  different *set* of systems, and a second call converges to the *union* of
  what any call observed rather than freezing anything: nothing already
  recorded is ever lost, dropped, or duplicated.
- `quarantine` writes via `replace=True` on purpose, the opposite choice: the
  *latest* call wins. A session directory that fails, is half fixed, and
  fails differently must end up describing its latest failure, not its
  first -- see `quarantine`'s own docstring.

Neither needs a transaction. Each insert stands on its own primary key, so a
partial run followed by a re-run (by the same watcher, or a second one racing
it) converges on the same rows without one. A transaction here would add
rollback semantics that buy nothing and would forbid this being called from
inside another one.
"""

from __future__ import annotations

import datetime

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.discover import SystemState, systems_with_data
from wl_preproc.ingest.verify import Integrity
from wl_preproc.schema import DEFAULT_PREFIX, core, ingest, pipeline

# element-animal requires a birth date and does not allow null. This machine
# cannot know one -- it is wl.works' authored record (section 11.1) -- so an
# obviously-sentinel value is used rather than a plausible-looking guess. A
# date nobody could mistake for real is safer than one somebody might.
SUBJECT_BIRTH_DATE_UNKNOWN = datetime.date(1900, 1, 1)

# element-animal declares `subject : varchar(8)` -- read directly from the
# installed package (.venv/.../element_animal/subject.py), not assumed. The
# manifest's own `subject` is an unconstrained str, so a name that validates
# there can still be too long for this column. `land_session` does not check
# this itself: Task 8's watcher is what sees a manifest before deciding
# whether to land or quarantine it, and imports this constant to do that
# check (`reason="subject_unrepresentable"` in QUARANTINE_REASONS) before
# ever calling `land_session` with a subject this table cannot hold.
SUBJECT_MAX_LEN = 8


def to_naive_utc(value: datetime.datetime) -> datetime.datetime:
    """The one place an aware datetime becomes the naive UTC value DataJoint's
    `datetime` type actually stores.

    `SessionManifest.started_at` is required timezone-aware (a validator on
    that model enforces it); element-session's `session_datetime` is a bare
    DataJoint `datetime`, which has no timezone concept at all. pymysql's own
    datetime encoder (`pymysql.converters.escape_datetime`, confirmed by
    reading its source) reads only `year`/`month`/`day`/`hour`/`minute`/
    `second`/`microsecond` off whatever it is given -- it never consults
    `tzinfo` -- so inserting an aware value does not raise; it silently keeps
    those wall-clock numbers and drops the offset that would have converted
    them to UTC. For a value already in UTC that happens to be harmless. For
    any other offset it is not: the row files under the wrong wall-clock time,
    hours from where the session actually happened, with nothing to signal
    that it happened.

    Every key this module builds from a manifest goes through this one
    function -- see `session_key` below -- rather than each call site
    converting it separately, because two call sites that convert this
    differently is exactly how two "equal" keys stop being equal to each
    other. See `already_ingested`'s docstring for what that costs.

    A value that is already naive is returned unchanged rather than run
    through `.astimezone()`: for a naive input, `.astimezone()` assumes the
    *system* timezone, not UTC (Python's own documented behaviour), which
    would silently corrupt a value that was already correctly naive UTC --
    such as one a caller read back out of the database and passed back in.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone(datetime.UTC).replace(tzinfo=None)


def session_key(manifest: SessionManifest) -> dict:
    """The (subject, session_datetime) key every session-scoped table uses,
    built the one way this module builds it.

    Task 8's watcher needs this identical key to ask `already_ingested`
    whether a session has already been landed. If it built the key a
    different way -- e.g. straight off the manifest's aware `started_at`,
    skipping `to_naive_utc` -- the two would disagree silently and
    permanently: every scan would derive a key that never matches what
    `land_session` actually wrote, and the session would look unlanded
    forever. Importing this function instead of reimplementing the two-line
    dict is how that stays impossible rather than merely avoided by
    convention.
    """
    return {"subject": manifest.subject, "session_datetime": to_naive_utc(manifest.started_at)}


def _now(now: datetime.datetime | None) -> datetime.datetime:
    return to_naive_utc(now or datetime.datetime.now(datetime.UTC))


def already_ingested(session_key: dict, prefix: str = DEFAULT_PREFIX) -> bool:
    """An Ingestion row is what marks a session done.

    `session_datetime` is normalized through `to_naive_utc` here too, rather
    than trusting the caller to have built `session_key` with the function of
    the same name above -- so a caller that passed a manifest's aware
    `started_at` straight through still matches what `land_session` actually
    wrote, instead of silently never matching and re-ingesting the same
    session on every single scan. Cheap insurance against exactly the
    disagreement described in `to_naive_utc`'s docstring, rather than a
    second place that same conversion could be gotten wrong.
    """
    ingest.activate(prefix=prefix)
    key = dict(session_key)
    if "session_datetime" in key:
        key["session_datetime"] = to_naive_utc(key["session_datetime"])
    return len(ingest.Ingestion & key) > 0


def land_session(
    layout: SessionLayout,
    manifest: SessionManifest,
    topology: dict[str, SystemState],
    integrity: Integrity,
    manifest_hash: str,
    prefix: str = DEFAULT_PREFIX,
    now: datetime.datetime | None = None,
) -> dict:
    """Write Subject, Session, AcquisitionSystem and Ingestion. Returns the Session key.

    Not wrapped in one transaction: each insert is independently idempotent, so
    a partial run followed by a re-run converges on the same rows. A transaction
    would add rollback semantics that buy nothing here and would forbid this
    being called from inside another one.

    Both `ingest.activate` and `core.activate` are called: `ingest.activate`
    only brings up `pipeline` and its own `{prefix}ingest` schema, and this
    function also writes `core.AcquisitionSystem` directly, which
    `core.activate` is what actually binds. Calling `ingest.activate` alone
    leaves `AcquisitionSystem` unconfigured and every insert into it raises
    `DataJointError` -- caught only by running this module's own tests in
    isolation, since any earlier test in the same process that happened to
    activate `core` for this prefix first (any of `tests/schema/test_core.py`,
    `test_request.py`, ...) would silently paper over the gap by leaving
    `core`'s module-level schema already activated before this function ever
    ran. Both calls are idempotent and each activates `pipeline` first, so
    calling both here is cheap and correct regardless of what has already run.
    """
    ingest.activate(prefix=prefix)
    core.activate(prefix=prefix)

    key = session_key(manifest)

    # element-animal's Subject, verified against the installed package:
    #   subject : varchar(8)          <- 8 characters, and the manifest's is unbounded
    #   sex : enum('M','F','U')       <- required, no default
    #   subject_birth_date : date     <- required, NO DEFAULT and NOT nullable
    #
    # The birth date is an authored record that lives in wl.works' `animal`
    # table, and this machine can never read it (section 11.1). It cannot be
    # omitted either. So a stub is written with a sentinel date and a
    # description that says so in words -- recording that the date is unknown,
    # rather than asserting a false one. The authoritative record stays
    # wl.works'; this row exists because Session needs a parent.
    pipeline.Subject.insert1(
        {
            "subject": manifest.subject,
            "subject_nickname": manifest.subject,
            "sex": "U",
            "subject_birth_date": SUBJECT_BIRTH_DATE_UNKNOWN,
            "subject_description": (
                "stub created at ingest; sex and birth date unknown here. "
                "The authoritative animal record is wl.works' `animal` table."
            ),
        },
        skip_duplicates=True,
    )
    pipeline.Session.insert1(key, skip_duplicates=True)

    core.AcquisitionSystem.insert(
        [{**key, "system": system} for system in systems_with_data(topology)],
        skip_duplicates=True,
    )

    ingest.Ingestion.insert1(
        {
            **key,
            "ingested_at": _now(now),
            "session_dir": str(layout.dir),
            "integrity": str(integrity),
            "topology": {system: str(state) for system, state in topology.items()},
            "manifest_hash": manifest_hash,
        },
        skip_duplicates=True,
    )

    return key


def quarantine(
    session_dir: str,
    reason: str,
    detail: dict,
    subject: str | None = None,
    session_dt: datetime.datetime | None = None,
    prefix: str = DEFAULT_PREFIX,
    now: datetime.datetime | None = None,
) -> None:
    """Record a directory that failed validation.

    `replace=True` rather than `skip_duplicates`: a directory that fails, is
    half-fixed, and fails differently must end up describing its *latest*
    failure. Skipping would leave a stale reason on the record, and raising
    would abort the whole scan over one bad session.

    `session_dt`, when given, goes through the same `to_naive_utc` conversion
    as every other datetime this module writes -- it is best-effort
    provenance on a row that is not keyed by it (see the module docstring on
    `ingest.Quarantine`), but there is no reason for it to carry a different,
    silently-wrong-for-non-UTC value than `session_key` would have produced
    for the same manifest.
    """
    ingest.activate(prefix=prefix)
    ingest.Quarantine.insert1(
        {
            "session_dir": session_dir,
            "failed_at": _now(now),
            "reason": reason,
            "detail": detail,
            "subject": subject,
            "session_dt": to_naive_utc(session_dt) if session_dt is not None else None,
        },
        replace=True,
    )
