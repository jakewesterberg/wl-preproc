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
    session_dir: Path, nas_root: Path, host: str, share: str
) -> ArchiveOutcome:
    """Compress to scratch, verify there, publish, confirm, then sentinel."""
    scratch = session_dir.parent / f".{session_dir.name}.archiving"
    result = write_store(session_dir, scratch)
    verdicts = verify_store(result.path, session_dir)
    all_matched = all(v.matched for v in verdicts)

    published = nas_root / result.path.name
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
    """The database half of an archive attempt: delete any stale
    `ArchiveArtifact` row for `key`, then write a fresh one -- with its
    `ArchiveVerification` children -- only when `outcome.all_matched`.

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
    from wl_preproc.ingest import landing
    from wl_preproc.schema import archive as archive_schema

    archive_schema.activate(prefix=prefix)
    # `archive_session` has ALREADY overwritten the NAS artifact
    # unconditionally by this point, regardless of `outcome.all_matched`
    # (its own publish step, above, runs before the confirm/all_matched
    # check, not after it). So any PRIOR `ArchiveArtifact` row for this
    # session now describes bytes that no longer exist on the NAS, whether or
    # not THIS run verified. Deleted unconditionally, before the
    # `all_matched` branch below, so "no row" always honestly means "not
    # archived" -- never "was archived once, may or may not still be" (Task 9
    # review, Important).
    #
    # `.delete(prompt=False)`, not `replace=True` on the insert below: every
    # foreign key DataJoint declares is `ON DELETE RESTRICT`
    # (`datajoint/declare.py`), so a plain `REPLACE INTO` on `ArchiveArtifact`
    # (MySQL's `REPLACE` is DELETE-then-INSERT) raises `IntegrityError` while
    # its `ArchiveVerification` children from a PRIOR run still exist --
    # reproduced directly against a real MySQL container before this fix
    # (Task 9 review, CRITICAL). `.delete()` cascades through the real
    # dependency graph instead. `prompt=False` is required explicitly, not
    # left to `dj.config["safemode"]`'s default (`True` outside this
    # project's own test fixtures): an interactive y/n confirmation would
    # hang a non-interactive cron/systemd/daemon invocation of this forever.
    (archive_schema.ArchiveArtifact & key).delete(prompt=False)

    if not outcome.all_matched:
        return None

    archive_path = str(outcome.artifact_path.relative_to(nas_root))
    # One instant reused for every row this call writes, not a fresh
    # `datetime.now()` per row: `StoreResult` carries no timestamp of its
    # own -- compression already finished by the time this line runs -- so
    # "now" is this pipeline's best honest record of when the artifact was
    # CONFIRMED good.
    now = landing.to_naive_utc(datetime.datetime.now(datetime.UTC))
    # No `replace=True` needed: the prior row for this key, if any, was just
    # deleted above, so this is always a fresh insert.
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
