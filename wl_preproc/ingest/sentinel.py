"""Is this session finished landing, and if not, has it given up?

Spec section 4. `contracts/paths.py` has described the aggregate since 1c-1 —
"Session-complete detection waits for every expected system's marker" — and
nothing had ever read a marker back. This is that reader.

Quiescence was rejected as a *trigger* because a stalled transfer and a
finished one are both quiet, and no threshold distinguishes them. It survives
as an *alarm*, where being wrong changes a report and never an ingest.
"""

from __future__ import annotations

import datetime
from enum import StrEnum

from wl_preproc.contracts.done import DoneMarker
from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SessionLayout

STALL_AFTER_S = 7200  # 2 h. A reporting threshold; see spec section 14.


class MarkerState(StrEnum):
    ABSENT = "absent"
    EMPTY = "empty"
    PARSED = "parsed"
    INVALID = "invalid"


def read_marker(layout: SessionLayout, system: str) -> tuple[MarkerState, DoneMarker | None]:
    """Read one system's DONE marker.

    ABSENT and INVALID are deliberately distinct: the first means the transfer
    has not finished, the second means it finished and wrote something wrong.
    Collapsing them would report a broken producer as a slow one forever.
    """
    path = layout.done_marker(system)
    if not path.exists():
        return MarkerState.ABSENT, None
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return MarkerState.EMPTY, None
        return MarkerState.PARSED, DoneMarker.from_yaml(text)
    except Exception:
        return MarkerState.INVALID, None


def missing_systems(layout: SessionLayout, manifest: SessionManifest) -> list[str]:
    """Declared systems without a usable marker, in `expected_systems` order."""
    return [
        system
        for system in manifest.expected_systems
        if read_marker(layout, system)[0] in (MarkerState.ABSENT, MarkerState.INVALID)
    ]


def session_complete(layout: SessionLayout, manifest: SessionManifest) -> bool:
    """Every system the manifest declared has a marker. Nothing else is consulted.

    A system present on disk but never declared cannot hold this up: nothing was
    waiting for it.
    """
    return not missing_systems(layout, manifest)


def last_change_at(session_dir) -> datetime.datetime:
    """Newest mtime anywhere in the tree, as tz-aware UTC.

    The directory's own mtime is included, so a session whose only activity was
    creating an empty subdirectory still counts as recently touched.

    A candidate can stop existing between being listed by `rglob` and being
    `stat`'d — a dangling symlink, or a transfer's own write-to-temp-then-rename
    landing mid-walk — and this function runs only on incomplete sessions,
    directories that are by definition still being written to. That is the
    ordinary case here, not an edge one, so one vanished entry is skipped
    rather than allowed to crash the scan for every session after it.
    """
    newest = session_dir.stat().st_mtime
    for candidate in session_dir.rglob("*"):
        try:
            newest = max(newest, candidate.stat().st_mtime)
        except OSError:
            continue
    return datetime.datetime.fromtimestamp(newest, tz=datetime.UTC)


def is_stalled(
    layout: SessionLayout,
    manifest: SessionManifest,
    now: datetime.datetime,
    stall_after_s: int = STALL_AFTER_S,
) -> bool:
    """Incomplete, and quiet for long enough that it is not merely slow.

    `now` is injected rather than read from the clock so the tests state the
    elapsed time they mean instead of sleeping.
    """
    if session_complete(layout, manifest):
        return False
    return (now - last_change_at(layout.dir)).total_seconds() >= stall_after_s
