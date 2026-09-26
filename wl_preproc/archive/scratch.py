"""Freeing a session's scratch copy, and the names and lookups that freeing
and restoring share (2026-09-26 rehydration design, sections 4 and 5).

**Staging one level down.** `ingest/watcher.py::_candidate_dirs` takes any
direct child of the storage root that holds a manifest for a session, dot
directories included, and does not recurse. So a session on its way out or
back in is always moved through `root/.<name><suffix>/<name>`: the root's
child is the dot directory, which holds no manifest of its own, and the
watcher never sees the session inside it (design section 1, fact 4).

**A person frees scratch.** Nothing in `daemon.py` calls `free_session`
(design section 0, ruling 1; `tests/cli/test_reclaim_and_rehydrate.py::
test_the_daemon_never_frees_a_session`).
"""

from __future__ import annotations

import datetime
import os
import shutil
from pathlib import Path

from wl_preproc.schema import DEFAULT_PREFIX

RECLAIMING = ".reclaiming"
REHYDRATING = ".rehydrating"


class Refused(Exception):
    """Nothing was changed, and the message says why. The CLI prints it after
    "refusing: " and exits 1."""


def staging_dir(session_dir: Path, suffix: str) -> Path:
    """`root/.<name><suffix>` -- see the module docstring."""
    return session_dir.parent / f".{session_dir.name}{suffix}"


def refuse_leftovers(session_dir: Path) -> None:
    """Refuse while a staging directory from an interrupted reclaim or
    rehydrate exists. `lexists`, so a dangling symlink counts too. No
    automatic clean-up: a leftover from an interrupted deletion is exactly
    the thing a person should look at (design section 4)."""
    for suffix in (RECLAIMING, REHYDRATING):
        leftover = staging_dir(session_dir, suffix)
        if os.path.lexists(leftover):
            raise Refused(
                f"{leftover} is left over from an interrupted reclaim or rehydrate; "
                "inspect it and remove it by hand first"
            )


def now_utc() -> datetime.datetime:
    """Naive UTC now, the form every DataJoint datetime here stores."""
    from wl_preproc.ingest import landing

    return landing.to_naive_utc(datetime.datetime.now(datetime.UTC))


def currently_freed(*, prefix: str = DEFAULT_PREFIX) -> list[dict]:
    """Every session whose scratch copy is freed right now: its latest
    `ScratchReclamation` is newer than its latest `ScratchRehydration`, or it
    has none. `daemon.run_once` skips these until they are rehydrated
    (decided by the requester 2026-09-26).

    Read from the two history tables `free_session` and `rehydrate_session`
    write, never from the disk: a session whose directory is missing but
    which was never reclaimed -- a moved scratch root, a fixture that plants
    rows -- is not freed, and the daemon keeps treating it exactly as before.
    A reclamation and a rehydration stamped in the same whole second read as
    rehydrated, which fails toward attempting the session rather than hiding
    it; rehydrating a real session takes minutes, so the tie is not reached.
    """
    from wl_preproc.schema import archive

    archive.activate(prefix=prefix)
    last_freed: dict[tuple, datetime.datetime] = {}
    for row in archive.ScratchReclamation.to_dicts():
        session = (row["subject"], row["session_datetime"])
        last_freed[session] = max(row["reclaimed_at"], last_freed.get(session, row["reclaimed_at"]))
    last_restored: dict[tuple, datetime.datetime] = {}
    for row in archive.ScratchRehydration.to_dicts():
        session = (row["subject"], row["session_datetime"])
        last_restored[session] = max(
            row["rehydrated_at"], last_restored.get(session, row["rehydrated_at"])
        )
    return [
        {"subject": subject, "session_datetime": session_datetime}
        for (subject, session_datetime), freed_at in sorted(last_freed.items())
        if (subject, session_datetime) not in last_restored
        or freed_at > last_restored[(subject, session_datetime)]
    ]


def recorded_session_dir(key: dict, *, prefix: str = DEFAULT_PREFIX) -> str:
    """`Ingestion.session_dir` -- where every downstream stage reads this
    session's files, and so where rehydration restores it."""
    from wl_preproc.schema import ingest

    ingest.activate(prefix=prefix)
    rows = (ingest.Ingestion & key).to_dicts()
    if len(rows) != 1:
        raise Refused(f"no Ingestion row for {key['subject']} @ {key['session_datetime']}")
    return rows[0]["session_dir"]


def recorded_artifact(
    key: dict, nas_root: Path, *, prefix: str = DEFAULT_PREFIX
) -> tuple[Path, str]:
    """`(artifact path under nas_root, recorded manifest digest)`.

    `nas_root` is the SHARE root, the same one `wlpp archive` was given;
    `archive_path` already carries the subject as its leading component
    (`archive/stage.py::record_archive_outcome`)."""
    from wl_preproc.schema import archive

    archive.activate(prefix=prefix)
    rows = (archive.ArchiveArtifact & key).to_dicts()
    if not rows:
        raise Refused(f"no ArchiveArtifact row for {key['subject']} @ {key['session_datetime']}")
    return nas_root / rows[0]["archive_path"], rows[0]["manifest_digest"]


def recorded_digests(key: dict, *, prefix: str = DEFAULT_PREFIX) -> dict[str, str]:
    """The rig's own blake3 for every file its DONE markers named, as
    `record_archive_outcome` stored them at archive time -- independent of
    the artifact, and the one reference for both reclaim and rehydrate
    (design section 6)."""
    from wl_preproc.schema import archive

    archive.activate(prefix=prefix)
    return {
        row["relative_path"]: row["expected_blake3"]
        for row in (archive.ArchiveVerification & key).to_dicts()
    }


def free_session(
    session_dir: Path, key: dict, nas_root: Path, *, prefix: str = DEFAULT_PREFIX
) -> int:
    """Delete the scratch copy of a reclaimable session whose archive is proven
    right now. Returns the bytes freed.

    Design section 4, steps 2-7; step 1, the dry-run/confirm guardrail, is the
    CLI's. Raises `Refused`, having changed nothing, at the first check that
    fails. Self-contained on purpose: the predicate is re-evaluated here
    rather than trusted from a caller, so no caller can free a session the
    predicate blocks.
    """
    import datajoint as dj

    from wl_preproc.archive import reclaim
    from wl_preproc.archive.proof import prove_artifact
    from wl_preproc.archive.verify import _expected_digests, hash_reconstruction, stored_sizes
    from wl_preproc.contracts.done import blake3_file
    from wl_preproc.schema import archive

    recorded = recorded_session_dir(key, prefix=prefix)
    if Path(session_dir) != Path(recorded):
        raise Refused(
            f"{session_dir} is not this session's recorded directory {recorded}; "
            "rehydration restores to the recorded one, so only that copy may be freed"
        )

    try:
        listed = _expected_digests(session_dir)
    except ValueError as exc:
        raise Refused(str(exc)) from exc
    predicate = reclaim.reclaim_conditions(key, len(listed), prefix=prefix)
    if not reclaim.reclaimable(predicate):
        raise Refused("blocked on: " + ", ".join(reclaim.blocking(predicate)))

    refuse_leftovers(session_dir)

    artifact, digest = recorded_artifact(key, nas_root, prefix=prefix)
    rig_digests = recorded_digests(key, prefix=prefix)
    proof = prove_artifact(artifact, digest, rig_digests)
    if not proof.passed:
        raise Refused(proof.reason)

    # The scratch copy is only a cache if the archive holds all of it, as it
    # is NOW. A file added or changed on scratch after archiving is not in
    # the artifact, and freeing it would lose it (2026-09-26 rehydration
    # design, section 4, amended by Task 5's review). Three checks, cheapest
    # first, all before anything is changed.
    #
    # 1. Sizes, from array shapes alone: catches an added file or a changed
    #    length without reading a byte of either copy.
    held = stored_sizes(artifact)
    unarchived = sorted(
        str(p.relative_to(session_dir))
        for p in session_dir.rglob("*")
        if p.is_file() and held.get(str(p.relative_to(session_dir))) != p.stat().st_size
    )
    if unarchived:
        shown = ", ".join(unarchived[:5]) + (", ..." if len(unarchived) > 5 else "")
        raise Refused(
            f"{len(unarchived)} file(s) on scratch are not in the archive as they are "
            f"now, so freeing would lose them: {shown}; re-archive with `wlpp archive` first"
        )

    # 2. The DONE markers. A same-size re-sent file arrives with a new digest
    #    in its marker; the proof above rebuilt every file to the digests
    #    RECORDED at archive time, so the archive holds the old bytes and the
    #    markers now say otherwise (whole-branch review, finding I1).
    if listed != rig_digests:
        raise Refused(
            "the DONE markers on scratch no longer list what was archived, so the "
            "scratch copy has changed since archiving; re-archive with `wlpp archive` first"
        )

    # 3. Every file no rig digest names -- the manifest, the DONE markers,
    #    operator files -- hashed on scratch and rebuilt from the artifact.
    #    The proof never checked these (it has no reference digest for them),
    #    so a same-size edit to one would otherwise be freed unseen. Usually
    #    small; on a session whose integrity is `declared_only` a system's
    #    whole recording is unchecksummed, and hashing it here is the only
    #    content check it gets. A rebuild that raises is a disagreement, not
    #    a crash: nothing has been changed yet, and the refusal says so.
    #
    #    Not covered, and stated in the spec's amendment block: a
    #    rig-checksummed file edited in place WITHOUT its DONE marker being
    #    updated. The archive holds the rig's version of it, which is the one
    #    rehydration restores; hashing every scratch file to see the edit
    #    would double reclaim's reading.
    changed = []
    for p in sorted(session_dir.rglob("*")):
        relative = str(p.relative_to(session_dir))
        if not p.is_file() or relative in rig_digests:
            continue
        try:
            same = blake3_file(p) == hash_reconstruction(artifact, relative)
        except Exception:
            same = False
        if not same:
            changed.append(relative)
    if changed:
        shown = ", ".join(changed[:5]) + (", ..." if len(changed) > 5 else "")
        raise Refused(
            f"{len(changed)} file(s) on scratch that no rig digest names differ from the "
            f"archive's copy, so the scratch copy has changed since archiving: {shown}; "
            "re-archive with `wlpp archive` first"
        )

    bytes_freed = sum(p.stat().st_size for p in session_dir.rglob("*") if p.is_file())
    staging = staging_dir(session_dir, RECLAIMING)
    staging.mkdir()
    try:
        # One transaction: the row says the session is off its path, and the
        # rename is what takes it off. An insert that fails means no rename;
        # a rename that fails rolls the insert back.
        with dj.conn().transaction:
            archive.ScratchReclamation.insert1(
                {**key, "reclaimed_at": now_utc(), "bytes_freed": bytes_freed}
            )
            os.rename(session_dir, staging / session_dir.name)
    except BaseException:
        # `rmdir` succeeds only on an empty directory -- that is, only when the
        # rename never happened. If it did and the commit then failed, the
        # session sits inside the staging directory, and this leaves it there
        # for a person rather than deleting it.
        try:
            staging.rmdir()
        except OSError:
            pass
        raise
    shutil.rmtree(staging)
    return bytes_freed
