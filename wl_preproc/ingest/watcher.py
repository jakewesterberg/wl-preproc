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

import datajoint as dj

from wl_preproc.contracts.done import blake3_file
from wl_preproc.contracts.manifest import SCHEMA_VERSION, SessionManifest
from wl_preproc.contracts.paths import MANIFEST_FILENAME, SessionLayout
from wl_preproc.ingest import landing
from wl_preproc.ingest.discover import discover_topology
from wl_preproc.ingest.params import register_session_params
from wl_preproc.ingest.sentinel import is_stalled, session_complete
from wl_preproc.ingest.verify import verify_session
from wl_preproc.schema import DEFAULT_PREFIX


class Outcome(StrEnum):
    INGESTED = "ingested"
    ALREADY = "already"
    INCOMPLETE = "incomplete"
    STALLED = "stalled"
    QUARANTINED = "quarantined"
    # `register_session_params` raises a bare `dj.DataJointError` -- not the
    # `ValueError` every other rejection in `_scan_one` raises -- when
    # `paramset.register`'s bounded retry loop
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
    # `_scan_one`'s params-registration branch.
    DEFERRED = "deferred"


class ScanResult(NamedTuple):
    outcomes: dict[str, Outcome]


def _candidate_dirs(root: Path) -> list[Path]:
    """Immediate children holding a manifest. Not every directory under a
    storage root is a session, and a scratch folder must not become a
    quarantine row.

    Every filesystem call here is guarded. This feeds a dict comprehension in
    `scan_once`, one entry per session directory: an exception anywhere in
    this function would abort that comprehension and lose every session under
    `root`, not just the one entry that actually faulted -- the worst blast
    radius anywhere in this phase, since everything downstream depends on
    this function returning at all.

    Three calls here can raise, and not identically:

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

    A fault on `root.iterdir()` itself, or on any later `next()`, stops the
    walk and returns whatever `candidates` already holds -- the empty list if
    nothing was found yet, which is the honest answer for "nothing could be
    listed this pass," not a crash. A fault checking one child skips only
    that child and keeps going. Either way, a session invisible to one scan
    because of a transient or permissions fault is picked up by the next one,
    exactly like a session that simply has not finished transferring yet --
    losing the *whole* scan over it would not be.
    """
    candidates: list[Path] = []
    try:
        iterator = iter(root.iterdir())
    except OSError:
        return []

    while True:
        try:
            child = next(iterator)
        except StopIteration:
            break
        except OSError:
            break
        try:
            if child.is_dir() and (child / MANIFEST_FILENAME).is_file():
                candidates.append(child)
        except OSError:
            continue

    return sorted(candidates)


def _scan_one(
    session_dir: Path,
    prefix: str,
    verify: bool,
    now: datetime.datetime,
) -> Outcome:
    try:
        text = (session_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
        manifest = SessionManifest.from_yaml(text)
    except Exception as exc:
        # The read used to sit outside this guard, so invalid UTF-8 bytes or
        # a permissions fault on the manifest itself raised before
        # SessionManifest.from_yaml was ever reached -- the identical defect
        # already fixed in sentinel.py's read_marker, which wraps its own
        # read_text() and parse in one broad except for the same reason.
        # Bringing the read inside the guard means an unreadable manifest
        # quarantines like any other invalid one instead of crashing the scan
        # for every other session in `root`.
        landing.quarantine(
            str(session_dir),
            reason="manifest_invalid",
            detail={"error": str(exc)[:2000]},
            prefix=prefix,
            now=now,
        )
        return Outcome.QUARANTINED

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

    layout = SessionLayout(session_dir.parent, manifest.session_id)

    # Through landing's own helper, not built inline: manifest.started_at is
    # timezone-aware and DataJoint's `datetime` columns are naive, and
    # `manifest_session_key` -- which `land_session` itself calls -- is the
    # one place that conversion happens. A second, independent
    # implementation of "the key for this manifest" here is exactly how it
    # would eventually disagree with land_session's own, and if it ever did,
    # this check would never find what land_session actually wrote and the
    # session would re-ingest forever.
    session_key = landing.manifest_session_key(manifest)
    if landing.already_ingested(session_key, prefix=prefix):
        return Outcome.ALREADY

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
    except dj.DataJointError:
        # See Outcome.DEFERRED's own docstring: a database contention
        # condition, not a defect in this session's params file, so this
        # session is left for the next scan_once call to retry from scratch
        # rather than quarantined -- no Quarantine row, and the rest of this
        # scan continues unaffected.
        return Outcome.DEFERRED

    landing.land_session(
        layout,
        manifest,
        discover_topology(layout, manifest),
        integrity,
        blake3_file(layout.manifest_path),
        prefix=prefix,
        now=now,
    )
    return Outcome.INGESTED


def scan_once(
    root: Path,
    prefix: str = DEFAULT_PREFIX,
    verify: bool = True,
    now: datetime.datetime | None = None,
) -> ScanResult:
    """One pass. Safe to run concurrently with itself — see `landing`."""
    at = now or datetime.datetime.now(datetime.UTC)
    return ScanResult(
        outcomes={
            str(session_dir): _scan_one(session_dir, prefix, verify, at)
            for session_dir in _candidate_dirs(Path(root))
        }
    )
