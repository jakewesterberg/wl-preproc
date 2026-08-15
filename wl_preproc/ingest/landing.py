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
import hashlib

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

# `ingest.Quarantine`'s OWN columns, read directly from its declaration
# (wl_preproc/schema/ingest.py) -- deliberately separate constants from
# SUBJECT_MAX_LEN above, which is a different table's different, narrower
# limit (element-animal's `pipeline.Subject.subject : varchar(8)`).
# Quarantine.subject is a wider, best-effort provenance column
# (`varchar(32)`), not the landing decision. Both are enforced inside
# `quarantine()` itself, not by its callers: the checks Task 8's watcher runs
# BEFORE calling `quarantine()` (schema_version, session_id) can reach it with
# an oversized `subject` before SUBJECT_MAX_LEN is ever consulted -- a
# manifest with schema_version wrong AND a 40-character subject quarantines
# on the schema_version branch first, passing that subject straight through
# -- and `session_dir` is the primary key on every single call, with no
# length check anywhere else in this pipeline at all. A single defended choke
# point is simpler and more robust than auditing every call site's position
# relative to a check that could move (see check-order history in
# wl_preproc/ingest/watcher.py's `_scan_one`).
QUARANTINE_SUBJECT_MAX_LEN = 32
QUARANTINE_SESSION_DIR_MAX_LEN = 255

# `Ingestion.session_dir`'s own column (`ingest.py`), a SEPARATE constant
# from `QUARANTINE_SESSION_DIR_MAX_LEN` above even though both happen to
# read 255 today: two different tables' columns that could diverge
# independently, the identical reasoning that keeps SUBJECT_MAX_LEN and
# QUARANTINE_SUBJECT_MAX_LEN apart despite element-animal's Subject and this
# table's Quarantine both describing a "subject". Unlike Quarantine.session_dir
# -- a primary key `quarantine()` itself clamps defensively, since it must
# always be written and truncation is the only option available -- there is
# no equivalent clamp for Ingestion.session_dir: truncating a session's
# permanent identity in the row that IS its provenance record would silently
# misname it forever, not degrade gracefully. So this is a REJECT threshold,
# checked by Task 8's watcher before land_session is ever called, not a
# write-time clamp.
INGESTION_SESSION_DIR_MAX_LEN = 255


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


def _clamp_key(value: str, max_len: int) -> str:
    """Shorten `value` to at most `max_len` characters, folding a short
    digest of the FULL, untruncated value into the tail so two different
    values sharing a long common prefix produce DIFFERENT results, not the
    same one.

    A naive `value[:max_len]` slice -- what this function replaced -- is
    exactly the wrong choice for what this project's own nested-storage-root
    layout makes ROUTINE, not pathological, and round 2/3 review reasoned
    about it backwards: `session_dir_unrepresentable`
    (`wl_preproc/ingest/watcher.py`) fires only for a session whose storage
    root is already close to or past `max_len` characters long, and every
    OTHER session under that SAME root shares that identical over-long
    prefix -- their entire distinguishing suffix (a short session_id,
    `/2027-03-14_01` versus `/2027-03-14_02`) sits PAST the point a plain
    slice ever reaches. Confirmed directly: a 305-character path and a
    sibling differing only in its final 14 characters truncate to the
    IDENTICAL first 255 characters. Under the OLD scheme, `replace=True`
    then silently collapsed two real sessions' quarantine records into
    one, with only the last-scanned session's `untruncated_session_dir`
    recoverable -- the other's real path gone from the table entirely, not
    merely shortened in it.

    A digest of the FULL string breaks that tie: two different full values
    produce different digests with overwhelming probability
    (`digest_size=8`, 64 bits -- the same size `paramset.content_hash`
    already uses for an unrelated short-string-to-stable-identifier
    problem), so `replace=True` on the resulting key keeps meaning "the same
    directory, scanned again" rather than "some other directory sharing this
    root's long prefix". Deterministic in `value` alone, so re-quarantining
    the identical directory on a later scan still produces the identical
    clamped key and still updates the same row, exactly as before this fix.
    """
    if len(value) <= max_len:
        return value
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=8).hexdigest()
    suffix = f"~{digest}"
    return value[: max_len - len(suffix)] + suffix


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

    Both string columns are clamped to what they can actually hold before the
    insert is attempted, rather than left to raise a raw `pymysql` `DataError`.
    Every check-branch caller in `wl_preproc/ingest/watcher.py`'s
    `_evaluate_session` (schema_version, subject length, session_id, ...)
    calls this function directly, with no per-call guard of its own, on the
    reasoning that quarantining is itself the failure-handling path and
    should not need a second one at every call site -- clamping here, once,
    is what makes that reasoning actually hold. `_scan_one`'s OUTER boundary
    does additionally wrap its own, separate call to this function (see that
    module), but only as a second, more general line of defense against
    whatever else could still fail there (a lost database connection, for
    instance) -- not a substitute for this function being safe on its own
    against the two specific, anticipated overflow shapes below. The two
    columns are not equally safe to clamp:

    - `subject` (`QUARANTINE_SUBJECT_MAX_LEN`, 32) is not part of this
      table's key -- see `ingest.Quarantine`'s own docstring, "NOT keyed on
      (subject, session_datetime)" -- so it is OMITTED rather than truncated
      when it does not fit. A truncated subject is worse than a missing one:
      it looks like real, if short, provenance, and two different oversized
      subjects sharing a 32-character prefix would render identically. `None`
      is what this column already means for "unparseable" (see
      `test_an_unparseable_manifest_quarantines_with_no_session_key`); an
      oversized subject is the same honest admission for a different reason.
    - `session_dir` (`QUARANTINE_SESSION_DIR_MAX_LEN`, 255) IS this table's
      primary key and cannot be null, so it is shortened via `_clamp_key`
      (above) rather than omitted -- the only structurally available option
      for a value that must be written and cannot fit, made DISTINGUISHABLE
      rather than merely short, for the reason `_clamp_key`'s own docstring
      gives in full. The full path is also, separately, still recorded in
      `detail["untruncated_session_dir"]`, but only when clamping actually
      happened -- every other quarantine call's `detail` is passed through
      byte-for-byte, unchanged, exactly as the caller built it. `replace=True`
      still means a second write under the same clamped key describes its
      own latest failure rather than raising a second, different exception.
    """
    ingest.activate(prefix=prefix)
    subject_fits = subject is None or len(subject) <= QUARANTINE_SUBJECT_MAX_LEN
    clamped_subject = subject if subject_fits else None
    clamped_session_dir = _clamp_key(session_dir, QUARANTINE_SESSION_DIR_MAX_LEN)
    stored_detail = detail
    if clamped_session_dir != session_dir:
        stored_detail = {**detail, "untruncated_session_dir": session_dir}
    ingest.Quarantine.insert1(
        {
            "session_dir": clamped_session_dir,
            "failed_at": _now(now),
            "reason": reason,
            "detail": stored_detail,
            "subject": clamped_subject,
            "session_dt": to_naive_utc(session_dt) if session_dt is not None else None,
        },
        replace=True,
    )
