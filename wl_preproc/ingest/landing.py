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

Combined, these two shapes leave a real inconsistency behind after a genuine
race with differing topology, and it deserves naming explicitly rather than
leaving each half documented only in isolation: `Ingestion.topology` freezes
at whichever call landed first, while `AcquisitionSystem` keeps unioning in
every system any racing call ever observed. So after such a race,
`Ingestion.topology["rhs"]` can go on reading `"absent"` indefinitely even
once a real `AcquisitionSystem` row for `rhs` exists -- contradicting
`topology`'s own declared promise in `schema/ingest.py` ("the full per-system
state map, read as a unit") from that moment on. This is documentation, not a
fix: closing it for real needs a lock or a cross-table transaction, and the
design spec excludes a lock deliberately (section 13). `topology` is
authoritative for a session landed by exactly one call -- which is every
session, absent an actual race -- and after a race it is evidence of what the
*first* call saw, not a live cross-table guarantee. `AcquisitionSystem` (and,
downstream, `Segment`/`RejectedSegment`) is what reflects what is currently
true; `topology` does not update to match it.

Neither idempotence shape needs a transaction. Each insert stands on its own
primary key, so a partial run followed by a re-run (by the same watcher, or a
second one racing it) converges on the same rows without one. A transaction
here would add rollback semantics that buy nothing and would forbid this being
called from inside another one.
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
    that it happened. (This is the INSERT path specifically. The restriction
    path -- `table & {...}` -- goes through a different DataJoint code path
    with a different failure mode; see `already_ingested`'s docstring, which
    is where that one actually matters.)

    Every key this module builds from a manifest goes through this one
    function -- see `manifest_session_key` below -- rather than each call
    site converting it separately, because two call sites that convert this
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


def manifest_session_key(manifest: SessionManifest) -> dict:
    """The (subject, session_datetime) key every session-scoped table uses,
    built the one way this module builds it.

    Named `manifest_session_key`, not `session_key`, specifically so it does
    not share a name with `already_ingested`'s `session_key` PARAMETER (that
    parameter name is mandated by this task's interface and is not this
    function's to rename). Shadowing a module-level function with a same-named
    local is harmless by itself, but a future edit inside that function
    reaching for this helper by its old name would silently resolve to the
    dict parameter instead and fail with `TypeError: 'dict' object is not
    callable` -- a needless trap for one function to leave lying around for
    the next person to step in, closed by giving the two truly different
    names.

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

    `session_datetime` is normalized through `to_naive_utc` here too, in case
    the caller built `session_key` some other way than `manifest_session_key`
    -- e.g. straight off a manifest's aware `started_at`. What this
    specifically protects against on this RESTRICTION path is narrower than
    what `to_naive_utc` protects the INSERT path against, and is worth
    stating precisely rather than by analogy: an earlier version of this
    docstring named the insert-path risk here too, which review caught as the
    wrong risk for this particular call site.

    DataJoint's dict-restriction (`table & {...}`) serializes a datetime via
    bare `str()` (`datajoint/condition.py`'s `prep_value`) -- unlike
    `pymysql.converters.escape_datetime` on the insert path, this does NOT
    drop an aware value's UTC-offset suffix. MySQL's own literal parser then
    resolves that suffix against `@@session.time_zone` before comparing it to
    the naive column -- confirmed directly against a live container: forcing
    `SET time_zone='+05:00'` makes the identical literal string parse to a
    value five hours later. Under a UTC session tz -- including this
    project's own testcontainers image, whose session tz reports `SYSTEM` and
    resolves to UTC -- a `+00:00` suffix shifts nothing, so the restriction
    matches correctly whether or not this normalization runs at all:
    confirmed by calling the restriction with the normalization removed under
    that default tz, which still finds the row every time. Under any OTHER
    session tz -- a real, uncontrolled production possibility this module
    cannot rule out -- the identical aware value parses to a genuinely
    different instant than what was actually stored, and the restriction
    silently stops matching. Converting to naive here removes the offset
    suffix outright, which sidesteps the tz-dependent parse entirely rather
    than depending on it resolving to UTC. See
    `test_already_ingested_survives_a_non_utc_session_time_zone`, which
    forces a non-UTC session tz to exercise this for real rather than relying
    on the container's default to happen to be UTC.
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

    key = manifest_session_key(manifest)

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
    silently-wrong-for-non-UTC value than `manifest_session_key` would have
    produced for the same manifest.
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
