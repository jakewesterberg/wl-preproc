"""Re-hash what landed and compare it against what the transfer declared.

Spec section 5. This is the one place the watcher renders a verdict, and the
verdict is about *transfer* rather than about science — which is exactly the
distinction discover.py depends on to never refuse a session for what it lacks.
"""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

from wl_preproc.contracts.done import blake3_file
from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.discover import SystemState, discover_topology
from wl_preproc.ingest.sentinel import MarkerState, read_marker


class Integrity(StrEnum):
    VERIFIED = "verified"
    DECLARED_ONLY = "declared_only"
    SKIPPED = "skipped"


class Mismatch(NamedTuple):
    system: str
    path: str
    problem: str  # "missing" | "size" | "blake3" | "unreadable: <the OSError>"


def verify_session(
    layout: SessionLayout,
    manifest: SessionManifest,
    enabled: bool = True,
) -> tuple[Integrity, list[Mismatch]]:
    """Verify every system that carries integrity data.

    Returns DECLARED_ONLY if *any* system's marker was empty, because a session
    is verified only if everything in it was — reporting VERIFIED when one
    system carried no data would be the record claiming more than was checked.

    `discover_topology` and `read_marker` need no guarding at their call sites
    here: both are already total over their own filesystem calls (see their
    own docstrings) and are guaranteed never to raise. This function runs only
    on sessions `sentinel.session_complete` has already found complete, so
    every file below has finished landing and the tree is no longer being
    actively written to — a materially smaller race window than discover.py's,
    which scans directories still receiving new files. But "smaller" is not
    "zero": `candidate.is_file()`, `candidate.stat()`, and `blake3_file`'s
    open-and-read are all guarded below, because a permissions fault (an ACL
    change, a remount) or a hardware read error on the storage array is still
    entirely possible on a file that finished transferring, and an uncaught
    raise here would abort verification of every remaining file and every
    remaining system in the loop, rather than report the one file that could
    not be checked.
    """
    if not enabled:
        return Integrity.SKIPPED, []

    mismatches: list[Mismatch] = []
    saw_empty = False

    topology = discover_topology(layout, manifest)
    for system, state in sorted(topology.items()):
        if state not in (SystemState.PRESENT, SystemState.UNDECLARED):
            # discover_topology's PENDING collapses two different reasons a
            # declared system isn't "marked" into one state, because spec
            # section 7's job for that module — "only PENDING blocks ingest"
            # — does not need to tell them apart: an ABSENT marker (transfer
            # not finished, nothing to check yet) and an INVALID one
            # (transfer finished and wrote something unreadable, including a
            # marker that failed FileEntry.path's own containment check —
            # contracts/done.py) both land here identically. Verification
            # does need to tell them apart: an INVALID marker is a system
            # whose integrity is unknowable, the same situation an EMPTY
            # marker already downgrades a session for, and silently treating
            # it as "nothing to check yet" would render VERIFIED on a session
            # that had a system nothing was actually able to check — worse
            # than merely incomplete information, an active misstatement.
            if (
                state is SystemState.PENDING
                and read_marker(layout, system)[0] is MarkerState.INVALID
            ):
                saw_empty = True
            continue

        marker_state, marker = read_marker(layout, system)
        if marker_state is not MarkerState.PARSED or marker is None:
            saw_empty = True
            continue

        system_dir = layout.system_dir(system)
        for entry in marker.files:
            candidate = system_dir / entry.path
            try:
                if not candidate.is_file():
                    mismatches.append(Mismatch(system, entry.path, "missing"))
                elif candidate.stat().st_size != entry.bytes:
                    # Checked before hashing: a size mismatch is decisive and
                    # cheap, and re-reading a truncated 384 GB file to reach the
                    # same conclusion by digest would cost minutes to learn nothing.
                    mismatches.append(Mismatch(system, entry.path, "size"))
                elif blake3_file(candidate) != entry.blake3:
                    mismatches.append(Mismatch(system, entry.path, "blake3"))
            except OSError as exc:
                # candidate.is_file() swallows ENOENT/ENOTDIR/EBADF/ELOOP on its
                # own but not EACCES (pathlib._IGNORED_ERRNOS, identical on 3.11
                # and 3.13); candidate.stat() and blake3_file's open()+read()
                # are raw passthroughs that swallow nothing at all. A file that
                # raises here is not "missing" (the manifest is contradicted by
                # an absence), nor "size"/"blake3" (the check ran and
                # disagreed) — the check could not run at all, and that is a
                # different claim from either, so it gets its own name rather
                # than being folded into one of theirs.
                #
                # The exception is bound and its text kept rather than thrown
                # away: Task 8 surfaces `problem` directly in a quarantine
                # record for a human to triage, and errno is exactly what
                # separates "chmod a directory, minutes, self-service" (EACCES)
                # from "the storage array has a bad block, escalate to infra"
                # (EIO) — the latter being this module's own opening scenario,
                # a file that "met a bad block on the scratch array." Collapsing
                # both to the bare string "unreadable" would hide the one piece
                # of information that tells those two apart, for free, sitting
                # right here at the catch site.
                mismatches.append(Mismatch(system, entry.path, f"unreadable: {exc}"))

    integrity = Integrity.DECLARED_ONLY if saw_empty else Integrity.VERIFIED
    return integrity, mismatches
