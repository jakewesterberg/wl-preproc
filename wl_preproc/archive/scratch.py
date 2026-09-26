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
    from wl_preproc.archive.verify import _expected_digests
    from wl_preproc.schema import archive

    recorded = recorded_session_dir(key, prefix=prefix)
    if str(Path(session_dir)) != recorded:
        raise Refused(
            f"{session_dir} is not this session's recorded directory {recorded}; "
            "rehydration restores to the recorded one, so only that copy may be freed"
        )

    try:
        expected_file_count = len(_expected_digests(session_dir))
    except ValueError as exc:
        raise Refused(str(exc)) from exc
    predicate = reclaim.reclaim_conditions(key, expected_file_count, prefix=prefix)
    if not reclaim.reclaimable(predicate):
        raise Refused("blocked on: " + ", ".join(reclaim.blocking(predicate)))

    refuse_leftovers(session_dir)

    artifact, digest = recorded_artifact(key, nas_root, prefix=prefix)
    proof = prove_artifact(artifact, digest, recorded_digests(key, prefix=prefix))
    if not proof.passed:
        raise Refused(proof.reason)

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
