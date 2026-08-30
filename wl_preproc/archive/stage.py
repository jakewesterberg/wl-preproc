"""compress -> verify -> publish -> confirm -> sentinel.

**The sentinel is written last and that is its entire purpose.** wl.works'
prober records `complete` per observation under the rule "positive observations
only; absence renders unknown, never 'no data'". Without a sentinel a
half-copied artifact and a finished one are the same observation -- and a
sentinel written on an artifact that failed verification is worse than none,
because the app reads it and believes it.
"""

from __future__ import annotations

import datetime
import shutil
from dataclasses import dataclass
from pathlib import Path

from wl_preproc.archive.store import StoreResult, manifest_digest, write_store
from wl_preproc.archive.verify import FileVerdict, verify_store

# Cheap: `wl_preproc.schema.__init__` is "a constant and nothing else"
# (`cli/main.py`'s own comment) -- no datajoint import comes with it, unlike
# `record_archive_outcome`'s own `wl_preproc.schema.archive`/`wl_preproc.
# ingest.landing` imports below, which stay local to that function for
# exactly the reason every other DataJoint-heavy import in this codebase
# does (`archive/reclaim.py::reclaim_conditions` is the precedent).
from wl_preproc.schema import DEFAULT_PREFIX

SENTINEL_NAME = ".wlpp-archive-complete"


@dataclass(frozen=True, slots=True)
class ArchiveOutcome:
    artifact_path: Path
    verdicts: list[FileVerdict]
    all_matched: bool
    # Carried whole, not copied into separate fields: Task 9's ArchiveArtifact
    # row needs codec, clevel, compressed_bytes and manifest_digest, and it
    # cannot re-derive them by calling write_store a second time -- Blosc's
    # multi-threaded compression is not reproducible (store.py's own
    # docstring, spec section 10 item 4), so a second call yields a
    # different, equally valid digest that no longer matches the artifact on
    # the NAS. The only run that has these values is this one.
    store: StoreResult


def archive_session(
    session_dir: Path, nas_root: Path, key: dict, *, prefix: str = DEFAULT_PREFIX
) -> ArchiveOutcome:
    """Compress to scratch, verify there, publish, confirm, then sentinel.

    `key` is the `(subject, session_datetime)` this session's `ArchiveArtifact`
    row is keyed on -- needed here, not just by `record_archive_outcome`, for
    the invalidation below. `host`/`share` are deliberately NOT parameters:
    this function never used them (found in review, cheap correction) --
    they exist only for `record_archive_outcome`'s own `ArchiveArtifact` row,
    and a reader seeing them here would reasonably think the publish path
    itself is host-aware, which it never was.
    """
    scratch = session_dir.parent / f".{session_dir.name}.archiving"
    result = write_store(session_dir, scratch)
    verdicts = verify_store(result.path, session_dir)
    all_matched = all(v.matched for v in verdicts)

    published = nas_root / result.path.name

    # Invalidated HERE -- immediately before any NAS mutation below -- not
    # in `record_archive_outcome` after this function returns, which is
    # where it used to live. Found in review (Task 10 whole-branch pass,
    # BLOCKING): `copytree` and `manifest_digest` (both below) are exactly
    # the calls this design most expects to raise -- a full share, a
    # dropped mount, a permission fault, an IO error -- and a raise from
    # either used to leave `record_archive_outcome` never called at all, so
    # the PRIOR row survived describing bytes that might no longer exist.
    # `_verified_archives` (`cli/report.py`) reads only that row, never the
    # filesystem, so the stale row kept telling "Sessions whose rig may
    # clear its copy" and `wlpp tape-manifest` that this session's archive
    # was still verified -- the exact thing this whole subsystem exists to
    # prevent: a rig told to delete the only other copy of an irreplaceable
    # recording over an artifact that was never actually confirmed whole.
    #
    # Placed HERE specifically, not any earlier: a raise from `write_store`/
    # `verify_store` above (a bad SCRATCH read, nothing to do with the NAS)
    # must NOT invalidate a still-good prior row -- the OLD NAS artifact is
    # untouched in that case, and deleting its row would be the identical
    # false claim in the opposite direction. The invariant this function
    # must keep true is "the row survives exactly as long as the NAS bytes
    # it describes do", which means invalidating at the one moment this
    # function is about to start being untrue to that -- not before, not
    # after.
    #
    # `.delete(prompt=False)`, unconditional -- the same reasoning
    # `record_archive_outcome` used to carry for this exact call (moved
    # here with it): every foreign key DataJoint declares is `ON DELETE
    # RESTRICT`, so a plain `REPLACE INTO` while `ArchiveVerification`
    # children from a PRIOR run still exist raises `IntegrityError`
    # (reproduced directly against a real MySQL container, Task 9 review,
    # CRITICAL); `prompt=False` because `dj.config["safemode"]`'s default
    # (`True` outside this project's own test fixtures) would hang a
    # non-interactive cron/systemd/daemon invocation of this forever.
    from wl_preproc.schema import archive as archive_schema

    archive_schema.activate(prefix=prefix)
    (archive_schema.ArchiveArtifact & key).delete(prompt=False)

    if published.exists():
        shutil.rmtree(published)
    published.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(result.path, published)

    # Confirm the copy: cheaper than re-verifying, and it catches the failure
    # publishing can introduce that verification cannot see -- transfer
    # corruption between scratch and the NAS.
    if manifest_digest(published) != result.manifest_digest:
        all_matched = False

    # Scratch is reclaimed only once the artifact is known-good, gated the
    # same way as the sentinel and for the same reason: found in review,
    # rmtree(scratch) used to run unconditionally *before* the confirm check
    # above even executed, so a transfer corruption confirm exists to catch
    # had already destroyed the one pre-transfer copy that could show which
    # side -- scratch or NAS -- was wrong. Losing it means re-running the
    # whole compression to get back to where this run already was, which
    # spec section 10 item 5 already flags as heavy on a real session.
    if all_matched:
        shutil.rmtree(scratch)
        (published / SENTINEL_NAME).write_text(
            datetime.datetime.now(datetime.UTC).isoformat(), encoding="utf-8"
        )
    return ArchiveOutcome(published, verdicts, all_matched, result)


def nas_root_for_subject(nas_root: Path, key: dict) -> Path:
    """`nas_root`, namespaced by subject -- the directory `archive_session`
    should actually publish under, not the bare share root.

    `archive_session`'s own publish path (above: `nas_root / result.path.name`,
    where `result.path.name` is a bare `SessionId` -- date plus index,
    `wl_sync.session`) carries no subject at all. Two different subjects
    sharing one session id -- a real, reachable state; nothing anywhere
    enforces that session ids are subject-scoped -- would otherwise publish to
    the IDENTICAL NAS path, the second silently `rmtree`-ing and replacing the
    first (`cli/main.py`'s `archive` dispatch, Task 9 review). Every caller of
    `archive_session` needs this exact namespacing, so it is a function of its
    own rather than a comment repeated at each call site.
    """
    return nas_root / key["subject"]


def record_archive_outcome(
    key: dict,
    outcome: ArchiveOutcome,
    nas_root: Path,
    host: str,
    share: str,
    *,
    prefix: str = DEFAULT_PREFIX,
) -> str | None:
    """The database half of an archive attempt: write a fresh
    `ArchiveArtifact` row -- with its `ArchiveVerification` children -- only
    when `outcome.all_matched`.

    **Does NOT delete a stale prior row itself.** That used to happen here,
    unconditionally, before this function's own insert -- but this function
    only ever runs AFTER `archive_session` has returned, and a raise from
    `archive_session` (a full share, a dropped mount, a permission fault
    mid-`copytree`/`manifest_digest`) meant this function was never called
    at all, leaving the prior row describing bytes that might no longer
    exist (Task 10 whole-branch review, BLOCKING). The invalidation moved
    into `archive_session` itself, at the one moment it is about to start
    mutating the NAS -- see that function's own comment. This function
    therefore ASSUMES `archive_session` has already invalidated any stale
    row for `key` by the time it runs; it must not be called on its own,
    only immediately after the `archive_session` call whose `outcome` it is
    given.

    Takes an already-computed `ArchiveOutcome` rather than calling
    `archive_session` itself, deliberately: `wl_preproc.daemon`'s archival
    stage imports `archive_session` directly into its OWN module namespace so
    that `wl_preproc.daemon.archive_session` is a real, patchable name --
    `tests/ingest/test_archive_trigger.py` patches exactly that, and a call to
    `archive_session` buried inside a shared helper here would be invisible to
    it. So both `cli/main.py`'s `archive` dispatch and `daemon.py`'s archival
    stage call `archive_session` themselves and pass its result here -- this
    function is the part that stays byte-identical between them regardless of
    where `outcome` came from, factored out so the daemon runs the same
    bookkeeping Task 9's CLI dispatch already shipped and reviewed, rather
    than a second, divergent copy of it (this task's own brief).

    `nas_root` is the SHARE root (what `nas_root_for_subject` above was
    called with), not the per-subject directory nested under it --
    `archive_path` is computed relative to it, so it stays "relative to the
    share" (Controller ruling C, Task 9) with the subject as its own leading
    path component, not baked into the root itself.

    Returns the `archive_path` written (relative to `nas_root`), or `None`
    when nothing was written.
    """
    if not outcome.all_matched:
        return None

    import datajoint as dj

    from wl_preproc.ingest import landing
    from wl_preproc.schema import archive as archive_schema

    archive_schema.activate(prefix=prefix)
    archive_path = str(outcome.artifact_path.relative_to(nas_root))
    # One instant reused for every row this call writes, not a fresh
    # `datetime.now()` per row: `StoreResult` carries no timestamp of its
    # own -- compression already finished by the time this line runs -- so
    # "now" is this pipeline's best honest record of when the artifact was
    # CONFIRMED good.
    now = landing.to_naive_utc(datetime.datetime.now(datetime.UTC))
    # Both inserts in one transaction (Task 10 whole-branch review, cheap
    # correction; `daemon.py`'s own `_populate_event_stage` is this
    # codebase's existing precedent for `dj.conn().transaction`). Without
    # it, an `ArchiveArtifact` insert that succeeds followed by a failing
    # `ArchiveVerification` batch left a parent row with NO children --
    # which `_verified_archives`' own `len(verifications) == 0` guard
    # correctly reads as "not verified" (so no false rig-may-clear claim),
    # but `daemon.py`'s `_archive_stage_keys()` subtracts `ArchiveArtifact`
    # alone, not "`ArchiveArtifact` with verified children" -- so the
    # session then left that key source permanently, never retried, the
    # same never-retried trap the row-survival finding names for a
    # different reason. No `replace=True` needed: `archive_session` already
    # invalidated any prior row for this key before it ever mutated the
    # NAS, so this is always a fresh insert.
    with dj.conn().transaction:
        archive_schema.ArchiveArtifact.insert1(
            {
                **key,
                "archive_host": host,
                "archive_share": share,
                "archive_path": archive_path,
                "codec": outcome.store.codec,
                "clevel": outcome.store.clevel,
                "compressed_bytes": outcome.store.compressed_bytes,
                "manifest_digest": outcome.store.manifest_digest,
                "compressed_at": now,
            }
        )
        archive_schema.ArchiveVerification.insert(
            [
                {
                    **key,
                    "relative_path": verdict.relative_path,
                    "expected_blake3": verdict.expected,
                    "actual_blake3": verdict.actual,
                    "matched": 1 if verdict.matched else 0,
                    "verified_at": now,
                }
                for verdict in outcome.verdicts
            ]
        )
    return archive_path
