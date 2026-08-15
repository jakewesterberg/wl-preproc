"""One pass over a storage root. The only public entry point in `ingest`.

Polling rather than inotify: a scan cannot miss an event that fired while
nothing was listening, has no platform surface, and sessions arrive a few times
a week so latency is irrelevant.

Each immediate child of `root` is one candidate session; this does not recurse.
A session directory's internal structure is `SessionLayout`'s business.
"""

from __future__ import annotations

import datetime
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

import blake3 as _blake3

from wl_preproc.contracts.manifest import SCHEMA_VERSION, SessionManifest
from wl_preproc.contracts.paths import MANIFEST_FILENAME, SessionLayout
from wl_preproc.ingest import landing
from wl_preproc.ingest.discover import discover_topology
from wl_preproc.ingest.params import register_session_params
from wl_preproc.ingest.sentinel import is_stalled, session_complete
from wl_preproc.ingest.verify import verify_session
from wl_preproc.schema import DEFAULT_PREFIX
from wl_preproc.schema.paramset import ContentionExhausted


class Outcome(StrEnum):
    INGESTED = "ingested"
    ALREADY = "already"
    INCOMPLETE = "incomplete"
    STALLED = "stalled"
    QUARANTINED = "quarantined"
    # `register_session_params` raises `ContentionExhausted` -- not the
    # `ValueError` every other rejection in `_evaluate_session` raises --
    # when `paramset.register`'s bounded retry loop
    # (`wl_preproc/schema/paramset.py`, `_MAX_REGISTER_ATTEMPTS`) exhausts
    # every attempt to allocate a fresh `paramset_idx` under real concurrent
    # registration. That is a database contention condition, not a defect in
    # this session's own files, so it must not become a `params_invalid`
    # quarantine -- that would blame the wrong thing and park a perfectly
    # good session behind a human-triage queue for a condition the next scan
    # is very likely to find already cleared. Named for what THIS SCAN does
    # about it -- defer the session to the next pass -- rather than for the
    # specific subsystem that hiccupped, so the name stays correct if some
    # other transient infrastructure fault ever needs the identical
    # treatment. Deliberately distinct from QUARANTINED (a session-caused
    # defect a human must triage) and from INCOMPLETE/STALLED (which
    # describe the session's own on-disk state, not the pipeline's). See
    # `_evaluate_session`'s params-registration branch.
    #
    # This is safe ONLY because the catch that produces it is narrow
    # (`except ContentionExhausted`, not `except dj.DataJointError`, which
    # is the root of DataJoint's entire error tree). DEFERRED has no
    # visible row anywhere -- no Ingestion, no Quarantine -- and a
    # permanent condition wrongly caught here would retry forever, silently,
    # invisible to every report section. See `_scan_one`'s outer boundary,
    # which is what every OTHER unclassified failure reaches instead.
    DEFERRED = "deferred"


class ScanResult(NamedTuple):
    outcomes: dict[str, Outcome]
    # None for the ordinary case: root listed cleanly, whether or not it held
    # anything. Set to a description of whatever stopped `_candidate_dirs`
    # from listing `root` itself (as opposed to one child faulting) --
    # distinguishing "this root is genuinely empty" from "this root could not
    # be read at all", which an empty `outcomes` dict cannot: `wlpp ingest
    # --root /typo/path` and `--root <empty dir>` would otherwise be
    # byte-identical, exit 0, no output. A typo, an unmounted NAS, or an ACL
    # slip on the storage root reporting silent success is exactly "a
    # weekend's recording simply never appears" arriving through the front
    # door instead of as a stalled transfer.
    root_error: str | None = None


def _candidate_dirs(root: Path) -> tuple[list[Path], Exception | None]:
    """Immediate children holding a manifest. Not every directory under a
    storage root is a session, and a scratch folder must not become a
    quarantine row.

    Every filesystem call here is guarded. This feeds a dict comprehension in
    `scan_once`, one entry per session directory: an exception anywhere in
    this function would abort that comprehension and lose every session under
    `root`, not just the one entry that actually faulted -- the worst blast
    radius anywhere in this phase, since everything downstream depends on
    this function returning at all.

    Returns `(candidates, root_fault)`. Four calls here can raise, and not
    identically:

    - `root.resolve()`: default `strict=False` swallows `OSError` from a
      missing or permission-denied path component (confirmed by reading
      `posixpath._joinrealpath`'s source, then empirically -- see verify.py's
      own extensive documentation of this exact call), but not a genuine
      symlink loop, which it reports as `RuntimeError` instead, and does so
      only on this project's 3.11 floor -- on 3.13 the identical loop returns
      the unresolved path rather than raising at all. Resolved so
      `result.outcomes`' keys, and every `session_dir` this module writes to
      `Quarantine`/`Ingestion`, are absolute and meaningful regardless of the
      caller's current working directory -- a relative `--root` used to
      record a `session_dir` with no directory information in it at all.
    - `root.iterdir()` is a raw passthrough that swallows nothing on its own,
      and confirmed directly (not assumed) to behave differently across this
      project's two supported interpreters: on 3.11 it is a generator, so the
      call itself never raises and a fault on an unreadable `root` surfaces
      on the first `next()`; on 3.13 the call itself raises immediately for
      the identical input. The loop below does not assume which behaviour it
      is running under -- both the call and every `next()` are guarded.
    - `child.is_dir()` and `(child / MANIFEST_FILENAME).is_file()` swallow
      ENOENT/ENOTDIR/EBADF/ELOOP on their own -- pathlib's own ignored-errno
      table, confirmed to live at a different module path on 3.11
      (`pathlib._IGNORED_ERRNOS`) versus 3.13 (`pathlib._abc._IGNORED_ERRNOS`),
      which is exactly why this function does not reference that table by
      name and instead catches the one errno neither version swallows:
      EACCES. A permission fault on one child -- an rsync run as the wrong
      user, an ACL slip -- still raises there even though the directory
      itself is real.

    A fault on `root.resolve()`, `root.iterdir()` itself, or any later
    `next()`, stops the walk and returns whatever `candidates` already holds
    (empty if nothing was found yet) alongside the fault that stopped it --
    `scan_once` turns that into `ScanResult.root_error`. A fault checking one
    child skips only that child and keeps going, and never touches
    `root_fault`: that is a per-session failure `_scan_one` already reports
    through `outcomes`, not a root-level one.
    """
    try:
        resolved = root.resolve()
    except (OSError, RuntimeError) as exc:
        return [], exc

    candidates: list[Path] = []
    try:
        iterator = iter(resolved.iterdir())
    except OSError as exc:
        return [], exc

    root_fault: Exception | None = None
    while True:
        try:
            child = next(iterator)
        except StopIteration:
            break
        except OSError as exc:
            root_fault = exc
            break
        try:
            if child.is_dir() and (child / MANIFEST_FILENAME).is_file():
                candidates.append(child)
        except OSError:
            continue

    return sorted(candidates), root_fault


def _evaluate_session(
    session_dir: Path,
    prefix: str,
    verify: bool,
    now: datetime.datetime,
) -> Outcome:
    """Every check for one session directory. May raise: `_scan_one` below is
    the boundary that turns an escape here into a quarantine rather than a
    crash of the whole scan.

    Check order, corrected in round 3 review after an over-broad round-2
    fix: `session_id_mismatch` and the `session_dir`-length check just below
    it both run BEFORE `already_ingested`; `manifest_schema_version` and
    `subject_unrepresentable` both run AFTER it. This is not one rule
    ("early checks first") -- it is two different, deliberately separate
    reasons landing on two different answers:

    - `already_ingested` keys on `(subject, session_datetime)` alone, never
      on the directory. Round 2 moved it to run immediately after the parse
      so an already-landed session is a true no-op on every later scan
      (design section 8.3): `manifest_schema_version` reads a CODE-level
      constant (`SCHEMA_VERSION`) that is deliberately bumped over the
      pipeline's own deployed lifetime, so a session landed under an OLDER
      version used to fail it freshly on every poll after a version bump,
      while its `Ingestion` row stayed -- the same session listed under
      both Ingested and Quarantined, contradicting section 9.
      `subject_unrepresentable` is the identical shape for the SAME reason,
      one layer down: `landing.SUBJECT_MAX_LEN` is a property of an
      installed dependency (element-animal), not of this directory, and
      could in principle narrow under a future dependency bump the same way
      `SCHEMA_VERSION` deliberately widens. Both stay after
      `already_ingested` on purpose: an already-landed session must never
      be re-judged against a global that has moved on without it.
    - `session_id_mismatch` and the `session_dir`-length check are not
      globals at all -- they are properties of THIS directory and THIS
      manifest, fixed for as long as the directory exists, and moving
      `already_ingested` above them was the actual round-2 bug: it keys on
      identity, not on path, so a directory whose manifest happened to
      declare an ALREADY-landed identity (a renamed or duplicated session
      directory -- precisely the case `session_id_mismatch` exists to
      catch) returned ALREADY before that check ever ran, silently
      disabling it. Proven live with a revert-control: the identical probe
      (an already-landed identity, a manifest claiming a different
      `session_id` than the directory it sits in) gave `quarantined` under
      the pre-round-2 order and `already` under round 2's, with zero
      Quarantine rows written either way in the broken case. Neither of
      these two needs a database or `SCHEMA_VERSION` to evaluate, so
      nothing is lost by running them first, and the identity confusion
      they exist to catch cannot be un-caught by something that happens to
      have already landed under the WRONG identity.
    """
    try:
        raw = (session_dir / MANIFEST_FILENAME).read_bytes()
        text = raw.decode("utf-8")
        manifest = SessionManifest.from_yaml(text)
    except Exception as exc:
        # The read sits INSIDE this guard, not outside it: invalid UTF-8
        # bytes or a permissions fault on the manifest itself used to raise
        # before SessionManifest.from_yaml was ever reached, mirroring the
        # identical fix already made in sentinel.py's read_marker, which
        # wraps its own read_text() and parse in one broad except for the
        # same reason. Handled here, specifically, as manifest_invalid --
        # not left to _scan_one's outer boundary and its generic
        # unexpected_failure -- because this IS a known, anticipated shape
        # with an informative name, unlike what that boundary exists for.
        #
        # read_bytes(), decoded separately, not read_text(encoding="utf-8"):
        # read_text()'s default newline=None enables universal-newline
        # translation ABOVE the codec layer, collapsing \r\n and bare \r to
        # \n in the returned str regardless of encoding validity -- a
        # transformation the UTF-8 round-trip reasoning that justified
        # text.encode("utf-8") at landing time (see the land_session call
        # below) never accounted for. Proven end to end on a CRLF-authored
        # manifest, identically on 3.11.15 and 3.13.9: the stored hash and
        # the real file's own blake3 came out completely different. SpikeGLX
        # and Intan both run on Windows, so CRLF manifests are not a
        # hypothetical this pipeline can assume away. `raw`, the untranslated
        # bytes, is what `Ingestion.manifest_hash` is hashed from -- see
        # `wl_preproc/schema/ingest.py`'s own declaration, "blake3 of the
        # manifest FILE'S BYTES", literally.
        landing.quarantine(
            str(session_dir),
            reason="manifest_invalid",
            detail={"error": str(exc)[:2000]},
            prefix=prefix,
            now=now,
        )
        return Outcome.QUARANTINED

    if manifest.session_id != session_dir.name:
        landing.quarantine(
            str(session_dir),
            reason="session_id_mismatch",
            detail={"manifest": manifest.session_id, "directory": session_dir.name},
            prefix=prefix,
            subject=manifest.subject,
            session_dt=manifest.started_at,
            now=now,
        )
        return Outcome.QUARANTINED

    if len(str(session_dir)) > landing.INGESTION_SESSION_DIR_MAX_LEN:
        # Ingestion.session_dir : varchar(255) (wl_preproc/schema/ingest.py),
        # a plain provenance column with no length check of its own before
        # this. Unlike Quarantine.session_dir (the identical width, but a
        # PRIMARY KEY that landing.quarantine() already clamps defensively --
        # see that function's docstring), a session this long reaching
        # land_session's own insert would raise a raw pymysql.err.DataError,
        # caught only by _scan_one's generic outer boundary and quarantined
        # as unexpected_failure -- forever, on every single poll, since
        # nothing about a directory's own path shortens on a later scan and
        # already_ingested can never say True for a session that has never
        # once landed. A completely valid, complete session parked behind an
        # unclassified reason it can never age out of deserves the same
        # source-level, specifically-named treatment `subject_unrepresentable`
        # and PARAMSET_TYPE_MAX_LEN already get, not a permanent stay in the
        # catch-all. Checked here, alongside session_id_mismatch: both are
        # properties of this directory alone, need no database, and (see
        # this function's own docstring) are safe to check before
        # already_ingested for the identical reason.
        landing.quarantine(
            str(session_dir),
            reason="session_dir_unrepresentable",
            detail={
                "session_dir": str(session_dir),
                "len": len(str(session_dir)),
                "max_len": landing.INGESTION_SESSION_DIR_MAX_LEN,
                "note": "Ingestion.session_dir : varchar(255)",
            },
            prefix=prefix,
            subject=manifest.subject,
            session_dt=manifest.started_at,
            now=now,
        )
        return Outcome.QUARANTINED

    session_key = landing.manifest_session_key(manifest)
    if landing.already_ingested(session_key, prefix=prefix):
        return Outcome.ALREADY

    if manifest.schema_version != SCHEMA_VERSION:
        landing.quarantine(
            str(session_dir),
            reason="manifest_schema_version",
            detail={"declared": manifest.schema_version, "implemented": SCHEMA_VERSION},
            prefix=prefix,
            subject=manifest.subject,
            session_dt=manifest.started_at,
            now=now,
        )
        return Outcome.QUARANTINED

    if len(manifest.subject) > landing.SUBJECT_MAX_LEN:
        landing.quarantine(
            str(session_dir),
            reason="subject_unrepresentable",
            detail={
                "subject": manifest.subject,
                "max_len": landing.SUBJECT_MAX_LEN,
                "note": "element-animal declares subject : varchar(8)",
            },
            prefix=prefix,
            session_dt=manifest.started_at,
            now=now,
        )
        return Outcome.QUARANTINED

    layout = SessionLayout(session_dir.parent, manifest.session_id)

    if not session_complete(layout, manifest):
        return (
            Outcome.STALLED
            if is_stalled(layout, manifest, now=now)
            else Outcome.INCOMPLETE
        )

    integrity, mismatches = verify_session(layout, manifest, enabled=verify)
    if mismatches:
        landing.quarantine(
            str(session_dir),
            reason="checksum_mismatch",
            detail={"mismatches": [m._asdict() for m in mismatches][:200]},
            prefix=prefix,
            subject=manifest.subject,
            session_dt=manifest.started_at,
            now=now,
        )
        return Outcome.QUARANTINED

    try:
        register_session_params(layout, prefix=prefix)
    except ValueError as exc:
        landing.quarantine(
            str(session_dir),
            reason="params_invalid",
            detail={"error": str(exc)[:2000]},
            prefix=prefix,
            subject=manifest.subject,
            session_dt=manifest.started_at,
            now=now,
        )
        return Outcome.QUARANTINED
    except ContentionExhausted:
        # See Outcome.DEFERRED's own docstring: a database contention
        # condition, not a defect in this session's params file, so this
        # session is left for the next scan_once call to retry from scratch
        # rather than quarantined -- no Quarantine row, and the rest of this
        # scan continues unaffected. Narrow on purpose: every OTHER
        # DataJointError -- and everything else that is not a ValueError --
        # is deliberately left to propagate to _scan_one's outer boundary,
        # which quarantines it as unexpected_failure instead of silently
        # deferring a condition that might never clear.
        return Outcome.DEFERRED

    landing.land_session(
        layout,
        manifest,
        discover_topology(layout, manifest),
        integrity,
        # `raw`, not `text.encode("utf-8")`: see the manifest-read guard's
        # own comment above for why the two are not the same bytes whenever
        # the file was authored with \r\n or bare \r line endings.
        _blake3.blake3(raw).hexdigest(),
        prefix=prefix,
        now=now,
    )
    return Outcome.INGESTED


def _scan_one(
    session_dir: Path,
    prefix: str,
    verify: bool,
    now: datetime.datetime,
) -> Outcome:
    """The boundary around `_evaluate_session`.

    `_evaluate_session` handles every check the brief and prior review rounds
    named explicitly -- an unreadable or malformed manifest, a bad
    schema_version, an unrepresentable subject, a session_id mismatch, a
    session_dir too long for Ingestion's own column, a checksum mismatch, an
    invalid params file, contention exhaustion -- each with its own specific,
    informative Quarantine reason (or DEFERRED). What it does not, and
    structurally cannot, enumerate in advance is everything ELSE that can go
    wrong: a `session_params.yaml` value YAML resolves to something
    `json.dumps` cannot serialize (a mixed int/str key, a bare date scalar --
    both now caught earlier, as `params_invalid`, but the NEXT unanticipated
    shape will not be), a manifest read racing a concurrent delete, a
    `DataJointError` that is not `ContentionExhausted`, anything not yet
    named. This feeds a dict comprehension in `scan_once`, exactly like
    `_candidate_dirs` above it: an exception escaping ONE session here would
    abort that comprehension and lose every OTHER session in the same root,
    the identical worst-blast-radius shape `_candidate_dirs`'s own three
    guarded calls were hardened against first. The same reasoning reaches
    every unguarded call in `_evaluate_session`'s body at once here, rather
    than requiring a new, specific guard for each new way something can
    fail — which is exactly why this boundary exists at all: not every
    failure mode can be anticipated and named in advance, and new ones keep
    surfacing as this module gets exercised harder. Both `session_dir_
    unrepresentable` and the params-serialisability check `register_
    session_params` now runs were found exactly this way, in round 3
    review, by asking what else could still reach this boundary that
    deserved a more specific name instead.

    Quarantined, not deferred: an unrecognised failure is exactly what
    `Outcome.DEFERRED` is not -- there is no reason to believe it is
    transient, and treating it as if it were would retry it forever,
    silently, with no visible row in any report. `unexpected_failure` is a
    real `QUARANTINE_REASONS` member (`wl_preproc/schema/ingest.py`) with the
    exception type and message recorded in `detail`, for a human to triage
    and, ideally, turn into a new named, specific check the next time this
    shape of failure recurs.

    The quarantine write itself is wrapped too: if THAT is what fails (an
    oversized `session_dir` even after `landing.quarantine`'s own
    truncation, a genuine loss of database connectivity), this must not
    raise a second, different exception out of the boundary that exists
    specifically to stop one session's failure from taking down the scan --
    that failure is swallowed, and the session is reported QUARANTINED
    without a row existing for it. That is a worse outcome than a row that
    exists, but a strictly better one than losing every sibling session in
    the same root over it.
    """
    try:
        return _evaluate_session(session_dir, prefix, verify, now)
    except Exception as exc:
        try:
            landing.quarantine(
                str(session_dir),
                reason="unexpected_failure",
                detail={"error_type": type(exc).__name__, "error": str(exc)[:2000]},
                prefix=prefix,
                now=now,
            )
        except Exception:
            pass
        return Outcome.QUARANTINED


def scan_once(
    root: Path,
    prefix: str = DEFAULT_PREFIX,
    verify: bool = True,
    now: datetime.datetime | None = None,
) -> ScanResult:
    """One pass. Safe to run concurrently with itself — see `landing`."""
    at = now or datetime.datetime.now(datetime.UTC)
    if at.tzinfo is None or at.utcoffset() is None:
        # landing's own `_now`/`to_naive_utc` tolerate a naive `now` (a naive
        # value is already what DataJoint's columns store), but this
        # function's one in-memory use of it -- `is_stalled`'s subtraction
        # from `last_change_at`'s always-aware return -- needs `at` itself to
        # be aware, and raised TypeError deep inside it otherwise, crashing
        # every session in the same scan rather than just the stalled check.
        # Coerced with `.replace`, not `.astimezone()`: `.astimezone()` on a
        # naive input assumes the *system* timezone, not UTC, which would
        # silently shift a caller's intended instant -- the identical warning
        # `landing.to_naive_utc`'s own docstring gives for the opposite
        # direction.
        at = at.replace(tzinfo=datetime.UTC)

    candidates, root_fault = _candidate_dirs(Path(root))
    return ScanResult(
        outcomes={
            str(session_dir): _scan_one(session_dir, prefix, verify, at)
            for session_dir in candidates
        },
        root_error=f"{type(root_fault).__name__}: {root_fault}" if root_fault else None,
    )
