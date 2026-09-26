"""Restore a reclaimed session from its NAS artifact to the directory ingest
recorded for it -- "decompress to scratch", parent spec section 3.3
(2026-09-26 rehydration design, section 5).

**Back to the recorded path, exactly.** Every downstream stage finds a
session's files through `Ingestion.session_dir`, so restoring there is what
makes reprocessing need no change anywhere else.

**Staged where the watcher cannot see it, then moved in one step.** Files are
rebuilt into `root/.<name>.rehydrating/<name>` (see `archive/scratch.py`'s
module docstring for why that name is invisible to the watcher), checked, and
renamed into place in one transaction with the `ScratchRehydration` row. On any
failure the staging directory is removed: the NAS copy is untouched, so a
partial copy on scratch is evidence of nothing the printed verdicts do not
already say.

Imports `cli.doctor`, the same layering inversion `ingest/watcher.py` documents
for `scratch_headroom`, and for the same reason: the scratch floor has one
definition, and it lives there.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from wl_preproc.archive.proof import prove_artifact
from wl_preproc.archive.scratch import (
    REHYDRATING,
    Refused,
    now_utc,
    recorded_artifact,
    recorded_digests,
    refuse_leftovers,
    staging_dir,
)
from wl_preproc.archive.verify import (
    FileVerdict,
    _expected_digests,
    iter_reconstruct,
    stored_paths,
    stored_size,
)
from wl_preproc.cli.doctor import headroom_after
from wl_preproc.schema import DEFAULT_PREFIX


@dataclass(frozen=True, slots=True)
class Rehydrated:
    session_dir: Path
    bytes_written: int


class NotRestored(Exception):
    """Every file was rebuilt, and at least one check failed. Nothing was left
    on scratch; `verdicts` names what failed."""

    def __init__(self, verdicts: list[FileVerdict]):
        super().__init__(f"{len(verdicts)} check(s) failed")
        self.verdicts = verdicts


def session_for_path(session_path: Path, *, prefix: str = DEFAULT_PREFIX) -> dict:
    """The session whose `Ingestion.session_dir` is `session_path`, compared
    after `Path` normalisation, with no symlink or working-directory
    resolution -- the directory no longer exists, so its manifest cannot be
    read the way `cli/main.py::_session_key_from_dir` reads it."""
    from wl_preproc.schema import ingest

    ingest.activate(prefix=prefix)
    rows = (ingest.Ingestion & {"session_dir": str(session_path)}).to_dicts()
    # Exact, in Python: MySQL's `=` under the server's default collation
    # (utf8mb4_0900_ai_ci) ignores case and accents, and a restore to a
    # differently spelled path is not a restore to the recorded one.
    rows = [row for row in rows if row["session_dir"] == str(session_path)]
    if not rows:
        raise Refused(
            f"no landed session was recorded at {session_path}; "
            "give the path exactly as ingest recorded it"
        )
    if len(rows) > 1:
        raise Refused(
            f"{len(rows)} landed sessions were recorded at {session_path}; "
            "which one to restore is ambiguous"
        )
    if not session_path.is_absolute():
        raise Refused(
            f"the recorded path {session_path} is relative; "
            "it cannot be restored to a place anyone can name"
        )
    return {"subject": rows[0]["subject"], "session_datetime": rows[0]["session_datetime"]}


def _write(store: Path, relative: str, target_root: Path) -> tuple[str, int]:
    """Rebuild one file under `target_root`, hashing as it is written.
    Returns `(blake3 hex, bytes written)`. Peak memory is a few copies of one
    stored chunk (the block, its dtype cast and its bytes -- see
    `verify.iter_reconstruct`), never the whole file."""
    import blake3 as _blake3

    destination = target_root / relative
    # The artifact's own paths come from `store.write_store`, relative to the
    # session; one that climbs out of the target is refused rather than
    # followed -- the same rule `DoneMarker.from_yaml` applies to `..`.
    if not destination.resolve().is_relative_to(target_root.resolve()):
        raise ValueError(f"{relative!r} would be written outside {target_root}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = _blake3.blake3()
    size = 0
    with destination.open("wb") as out:
        for block in iter_reconstruct(store, relative):
            out.write(block)
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _check(
    target: Path, key: dict, expected: dict[str, str], written: dict[str, str]
) -> list[FileVerdict]:
    """Design section 5.4. One verdict per rig-checksummed file, plus one for
    each other check that failed, named for what it checked.

    The four files no rig digest names -- the session manifest and the
    per-system `DONE` markers -- are proven by what they SAY: the manifest
    must describe this session, and the markers together must list exactly
    the digests recorded at archive time. Beyond that they came through the
    same rebuild code as the checksummed files, from a NAS copy whose
    manifest digest was just re-checked.
    """
    from wl_preproc.contracts.manifest import SessionManifest
    from wl_preproc.contracts.paths import MANIFEST_FILENAME
    from wl_preproc.ingest.landing import manifest_session_key

    verdicts = [
        FileVerdict(
            path,
            digest,
            written.get(path, "not in the artifact"),
            written.get(path) == digest,
        )
        for path, digest in sorted(expected.items())
    ]

    try:
        manifest = SessionManifest.from_yaml(
            (target / MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        found = manifest_session_key(manifest)
        seen = f"{found['subject']} @ {found['session_datetime']}"
        manifest_ok = found == key
    except Exception as exc:
        seen, manifest_ok = f"error: {type(exc).__name__}: {exc}", False
    if not manifest_ok:
        verdicts.append(
            FileVerdict(
                MANIFEST_FILENAME,
                f"{key['subject']} @ {key['session_datetime']}",
                seen,
                False,
            )
        )

    try:
        markers = _expected_digests(target)
    except Exception as exc:
        markers = {"<unreadable>": f"{type(exc).__name__}: {exc}"}
    if markers != expected:
        verdicts.append(
            FileVerdict(
                "DONE markers",
                f"{len(expected)} recorded digests",
                f"{len(markers)} listed, not the same set",
                False,
            )
        )
    return verdicts


def rehydrate_session(
    session_path: Path, nas_root: Path, *, prefix: str = DEFAULT_PREFIX
) -> Rehydrated:
    """Restore the session recorded at `session_path` from its NAS artifact.

    Raises `Refused`, having written nothing, when any section 5.2 check
    fails; `NotRestored` when every file was rebuilt and a section 5.4 check
    failed; anything else a write raises, after removing the staging
    directory. On success the session is at `session_path`, byte-identical
    to what was archived, and a `ScratchRehydration` row records it.
    """
    import datajoint as dj

    from wl_preproc.schema import archive

    session_path = Path(session_path)
    key = session_for_path(session_path, prefix=prefix)
    if os.path.lexists(session_path):
        raise Refused(f"{session_path} already exists; rehydration never overwrites or merges")
    if not session_path.parent.is_dir():
        raise Refused(
            f"{session_path.parent} does not exist; the scratch root has moved "
            "since this session was ingested"
        )
    refuse_leftovers(session_path)

    artifact, digest = recorded_artifact(key, nas_root, prefix=prefix)
    preflight = prove_artifact(artifact, digest, None)
    if not preflight.passed:
        raise Refused(preflight.reason)

    need = stored_size(artifact)
    if not headroom_after(str(session_path.parent), need):
        raise Refused(
            f"restoring {need} bytes would leave {session_path.parent} below the scratch floor"
        )
    expected = recorded_digests(key, prefix=prefix)
    if not expected:
        raise Refused(
            "no recorded rig digests for this session; nothing to check a "
            "restore against is never proven"
        )

    staging = staging_dir(session_path, REHYDRATING)
    target = staging / session_path.name
    # `staging.mkdir()` stays OUTSIDE the try/finally below: if it raises (a
    # race with another process, since `refuse_leftovers` above only checked
    # a moment ago -- or any other fault), THIS call never created that
    # directory, and must not be the one to remove it.
    #
    # `target.mkdir()` moved INSIDE the try, as its first statement, rather
    # than sitting beside `staging.mkdir()` above (round 1 review, Minor 4,
    # fixed a different bug at that same spot; round 2 review caught what
    # that fix introduced): a fault creating it -- ENOSPC, a permission or
    # quota fault, any filesystem error -- used to propagate before the try
    # was ever entered, so the `finally`'s cleanup never ran and the now-
    # empty staging directory THIS call DID create was left behind for
    # `refuse_leftovers` to block every later rehydrate of this session on,
    # by hand, forever. Deliberately still two separate mkdirs, not
    # `target.mkdir(parents=True)`: `parents=True` would silently recreate
    # `session_path.parent` if it vanished between the `is_dir()` check above
    # and here, exactly what that check exists to refuse rather than paper
    # over.
    staging.mkdir()
    restored = False
    try:
        target.mkdir()
        written: dict[str, str] = {}
        total = 0
        # Exactly the files the artifact holds, and no others: nothing else
        # writes under `target`.
        for relative in stored_paths(artifact):
            written[relative], size = _write(artifact, relative, target)
            total += size
        failures = [v for v in _check(target, key, expected, written) if not v.matched]
        if failures:
            raise NotRestored(failures)
        with dj.conn().transaction:
            archive.ScratchRehydration.insert1(
                {**key, "rehydrated_at": now_utc(), "bytes_written": total}
            )
            os.rename(target, session_path)
        restored = True
    finally:
        if restored:
            staging.rmdir()
        else:
            shutil.rmtree(staging, ignore_errors=True)
    return Rehydrated(session_path, total)
