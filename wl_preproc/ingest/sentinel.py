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

    `exists()` shares `is_dir()`'s ENOENT/ENOTDIR/EBADF/ELOOP swallowing but
    not EACCES (see `discover._has_content`, guarded against the identical
    gap -- confirmed from source to reproduce on Python 3.13 too, not just
    this project's 3.11 floor, since it is not the version-specific `rglob`
    gap documented there). This call is reached unconditionally, for every
    system, before the declared/undeclared branch is even decided, so it
    cannot be left to raise: ABSENT is the honest answer, the same one an
    actually-missing marker produces -- a transfer this function could not
    even check on has not been observed to finish.
    """
    path = layout.done_marker(system)
    try:
        marker_exists = path.exists()
    except OSError:
        return MarkerState.ABSENT, None
    if not marker_exists:
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

    Every filesystem call here is guarded, and this function is guaranteed to
    never raise. This feeds an *alarm* (`is_stalled`), and the two guards
    below do not resolve the same direction, because the two ways of being
    wrong here are not equivalent.

    A candidate can stop existing between being listed by `rglob` and being
    `stat`'d — a dangling symlink, or a transfer's own write-to-temp-then-rename
    landing mid-walk — and this function runs only on incomplete sessions,
    directories that are by definition still being written to. That is the
    ordinary case here, not an edge one, so one vanished entry is skipped
    rather than allowed to crash the scan for every session after it.

    The walk itself can also raise partway through -- the same
    Python-3.11-only `rglob` gap `discover._has_content` documents in full: a
    subdirectory it has already discovered can be removed before being
    scanned. A failed walk here keeps whatever `newest` already holds rather
    than resetting it, so it never has to invent a value: real progress
    already made survives as-is. This must never be a "nothing found"
    sentinel -- that would read as maximally fresh here by accident of
    implementation (an unset `max()` seed of 0) and delay a report that
    should fire.

    The initial `session_dir.stat()` is different in kind, not degree, and
    its fallback is deliberately the opposite direction: ancient
    (`datetime.min`), not fresh. A permission fault on the session directory
    itself does not even reach this line: `stat()` only needs search
    permission on the *parent*, not the target, so `session_dir.stat()`
    still succeeds and this function still returns the directory's real
    mtime in that case. What actually reaches this line is an
    *ancestor*-level fault instead: a broken NFS export, an ACL
    misconfiguration, an autofs hiccup at the storage root. That is not a
    one-directory race, the shape everything else in this function defends
    against -- it is a standing condition that hits every sibling session
    simultaneously and recurs identically on every subsequent scan for as
    long as it is misconfigured. `newest` is only ever raised by `max()`,
    never lowered, so whatever this line returns is not one input among
    several folded into a real computation -- it is the walk's floor, and a
    floor that `max()` cannot lower is the whole argument for which
    direction it points. Every real mtime the walk can find is older than
    "now", so a "just touched" fallback would sit ABOVE all of them and be
    returned unchanged: the fabricated value wins, and no amount of real
    evidence the walk goes on to collect can pull it back toward the truth.
    An ancient fallback is the exact opposite -- it sits below anything the
    walk could find, so the first real mtime replaces it outright and it
    survives only when the walk genuinely found nothing. A false "stalled"
    from the ancient fallback is a spurious report that clears itself the
    moment the fault is fixed and a later scan succeeds; "just touched"
    would instead make a whole storage root's worth of dead transfers read
    as merely active for as long as the misconfiguration lasts -- the exact
    invisible dead transfer this module exists to surface, not one narrow
    race away from it.
    """
    try:
        newest = session_dir.stat().st_mtime
    except OSError:
        newest = datetime.datetime.min.replace(tzinfo=datetime.UTC).timestamp()
    try:
        for candidate in session_dir.rglob("*"):
            try:
                newest = max(newest, candidate.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
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
