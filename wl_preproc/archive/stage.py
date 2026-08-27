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

from wl_preproc.archive.store import manifest_digest, write_store
from wl_preproc.archive.verify import FileVerdict, verify_store

SENTINEL_NAME = ".wlpp-archive-complete"


@dataclass(frozen=True, slots=True)
class ArchiveOutcome:
    artifact_path: Path
    verdicts: list[FileVerdict]
    all_matched: bool


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
    shutil.rmtree(scratch)

    # Confirm the copy: cheaper than re-verifying, and it catches the failure
    # publishing can introduce that verification cannot see -- transfer
    # corruption between scratch and the NAS.
    if manifest_digest(published) != result.manifest_digest:
        all_matched = False

    if all_matched:
        (published / SENTINEL_NAME).write_text(
            datetime.datetime.now(datetime.UTC).isoformat(), encoding="utf-8"
        )
    return ArchiveOutcome(published, verdicts, all_matched)
