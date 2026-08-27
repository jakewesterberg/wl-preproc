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
