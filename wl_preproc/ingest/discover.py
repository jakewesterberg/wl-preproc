"""What the session directory actually contains, versus what it promised.

Spec section 7. This module classifies and never judges. wl.works' governing
rule for every dispatch domain is "silence is `unknown`, never `failed`", and a
device that was not recorded is silence.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SYSTEMS, SessionLayout
from wl_preproc.ingest.sentinel import MarkerState, read_marker


class SystemState(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNDECLARED = "undeclared"
    PENDING = "pending"


def _has_content(layout: SessionLayout, system: str) -> bool:
    """A directory holding at least one file that is not the marker itself.

    An empty directory is not a recording; counting it as one would create an
    AcquisitionSystem row for a device that produced nothing.

    Every filesystem call below is guarded, because `directory` can be
    actively written to -- or, at the limit, removed entirely -- by a
    concurrent transfer while this runs, and this function is guaranteed to
    never raise.

    `is_dir()` swallows ENOENT/ENOTDIR/EBADF/ELOOP on its own (confirmed
    directly from source on both this project's 3.11.15 venv and 3.13) but
    not EACCES, so a bare permission error still raises there. Unlike the
    `rglob` exposure below, this one reproduces identically on 3.13 -- the
    version the preprocessing server actually runs -- so it cannot be left
    to the stdlib the way the exposures below legitimately can be.

    `rglob()`'s recursive walk can separately raise partway through if a
    subdirectory it has already discovered is removed before being scanned;
    on Python 3.11 (this project's floor) the inner `scandir` it opens is
    guarded only against `PermissionError`, fixed upstream in 3.13's
    rewritten `glob.py`, so that exposure is CI's 3.11 leg and developer
    machines, not the server. Simply returning False here would trade a
    crash for something worse: a recording sitting in plain sight in
    `directory` would silently read as ABSENT instead of UNDECLARED, and
    `systems_with_data` would drop a real AcquisitionSystem row over nothing
    but unlucky scan timing -- exactly what this module exists to prevent.
    So a failed recursive walk falls back to one cheap, non-recursive
    listing of `directory` itself: a file sitting directly in it still
    counts even when whatever actually vanished was deeper in the tree.

    That fallback listing (`iterdir()`, a bare `os.listdir()` with no
    swallowing of its own -- any `OSError` at all propagates) is guarded the
    same way, for a related but distinct reason: the `is_dir()` check above
    and these two calls are not atomic, so `directory` itself -- not merely
    something under it -- can be what is gone by the time `iterdir()` runs.
    Left unguarded, that failure would propagate out of this function into
    `discover_topology`, which has no try/except around this call: one
    system's bad timing would crash the scan for every system, not just
    misclassify the one that raced. `False` is the honest answer once
    `directory` itself is gone -- there is nothing left on disk to lose.

    The one thing this function can still get wrong, not by raising but by
    answering imprecisely: a recording that exists *only* under a
    subdirectory that is itself the one lost to the `rglob` race reads as
    ABSENT rather than UNDECLARED, since the fallback only lists
    `directory`'s immediate children. That is a deliberately modest fix
    rather than a hardened traversal -- a full recovery would mean not using
    `rglob` at all -- and, like the `rglob` exposure itself, it is bounded to
    CI's 3.11 leg and developer machines.
    """
    directory = layout.system_dir(system)
    try:
        if not directory.is_dir():
            return False
    except OSError:
        return False
    marker = layout.done_marker(system)

    def qualifies(candidate: Path) -> bool:
        return candidate.is_file() and candidate != marker

    try:
        return any(qualifies(p) for p in directory.rglob("*"))
    except OSError:
        try:
            return any(qualifies(p) for p in directory.iterdir())
        except OSError:
            return False


def discover_topology(
    layout: SessionLayout, manifest: SessionManifest
) -> dict[str, SystemState]:
    """One state per member of SYSTEMS. Total by construction, so a caller
    cannot silently skip a system by forgetting it exists.

    Every filesystem call this makes, directly or through `_has_content` and
    `sentinel.read_marker`, is guarded: one system's bad timing classifies
    that system, at worst, and never raises out of this loop to cost every
    other system its classification too."""
    declared = set(manifest.expected_systems)
    topology: dict[str, SystemState] = {}

    for system in SYSTEMS:
        marked = read_marker(layout, system)[0] not in (
            MarkerState.ABSENT,
            MarkerState.INVALID,
        )
        if system in declared:
            topology[system] = SystemState.PRESENT if marked else SystemState.PENDING
        elif _has_content(layout, system):
            topology[system] = SystemState.UNDECLARED
        else:
            topology[system] = SystemState.ABSENT

    return topology


def systems_with_data(topology: dict[str, SystemState]) -> list[str]:
    """Systems that get an AcquisitionSystem row.

    UNDECLARED is included: spec section 8.1 rules its data real, and omitting
    the row would hide a recording from every downstream stage in order to
    punish a manifest bug.
    """
    return sorted(
        system
        for system, state in topology.items()
        if state in (SystemState.PRESENT, SystemState.UNDECLARED)
    )
