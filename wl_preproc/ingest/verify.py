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
    problem: str
    # "missing" | "size" | "blake3" | "unreadable: <exc>"
    # | "escapes_system_dir: <resolved path>"


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
    "zero": `candidate.resolve()`, `candidate.is_file()`, `candidate.stat()`,
    and `blake3_file`'s open-and-read are all guarded below, because a
    permissions fault (an ACL change, a remount) or a hardware read error on
    the storage array is still entirely possible on a file that finished
    transferring, and an uncaught raise here would abort verification of
    every remaining file and every remaining system in the loop, rather than
    report the one file that could not be checked.

    `candidate.resolve()` is also compared against `system_dir.resolve()`
    before anything else touches the file, because `contracts/done.py`'s
    `FileEntry.path` validator can only reject a bare string — it has no
    filesystem access and cannot see a symlink planted inside a real
    `system_dir` that a clean, `..`-free relative path resolves straight
    through to somewhere else. This transfer runs as `rsync -a` (archive
    mode), which preserves source-side symlinks verbatim unless `--safe-links`
    is passed, so a leftover convenience link on a rig — pointing at a shared
    calibration file, an old mount, anything — reaches this function with no
    malice involved, the same accident-not-attack shape as the reversed
    `relpath` call that justified the string-level containment check in the
    first place. Both sides are resolved, not just the candidate, because the
    storage root itself may sit behind a symlinked NAS mount — resolving only
    one side would reject every file in production while passing on a local
    disk in every test.
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
                resolved = candidate.resolve()
                if not resolved.is_relative_to(system_dir.resolve()):
                    # Checked before anything else touches the file: is_file(),
                    # .stat(), and blake3_file would all happily follow the
                    # same symlink right back to wherever it actually points,
                    # so containment has to be decided first, not used to
                    # explain a result after the fact.
                    mismatches.append(
                        Mismatch(system, entry.path, f"escapes_system_dir: {resolved}")
                    )
                elif not candidate.is_file():
                    mismatches.append(Mismatch(system, entry.path, "missing"))
                elif candidate.stat().st_size != entry.bytes:
                    # Checked before hashing: a size mismatch is decisive and
                    # cheap, and re-reading a truncated 384 GB file to reach the
                    # same conclusion by digest would cost minutes to learn nothing.
                    mismatches.append(Mismatch(system, entry.path, "size"))
                elif blake3_file(candidate) != entry.blake3:
                    mismatches.append(Mismatch(system, entry.path, "blake3"))
            except ValueError:
                # Found while adding the resolve()-first ordering above, not
                # asked for: a non-encodable path -- e.g. entry.path containing
                # an embedded NUL byte -- used to be harmless before this
                # round, because candidate.is_file() was the first call made
                # on it and pathlib.Path.is_file has its own
                # `except ValueError: return False` (a path the OS cannot even
                # represent is treated the same as one that does not exist).
                # candidate.resolve() now runs first and has no equivalent
                # catch -- confirmed directly from source and empirically --
                # so without this clause, the exact case the round-3 review
                # explicitly named as harmless and told me not to touch would
                # have silently become fatal to the whole scan as a side
                # effect of reordering the checks around it. "missing" is not
                # a new answer invented to paper over that: a path that
                # cannot be represented to the OS provably cannot exist on
                # disk either, so it is the same honest answer is_file()
                # already gave this exact input, reached by construction
                # rather than by is_file()'s own internal handling now that
                # it never gets the chance to run.
                mismatches.append(Mismatch(system, entry.path, "missing"))
            except (OSError, RuntimeError) as exc:
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
                # RuntimeError, not just OSError: candidate.resolve()'s default
                # strict=False swallows OSError from a missing or permission-
                # denied component internally (confirmed by reading
                # posixpath._joinrealpath's source, then empirically on both
                # 3.11 and 3.13 — chmod 0o000 on the file or its parent does
                # not make resolve() raise at all, which is *why* it is safe to
                # call unconditionally, before the missing-file check, without
                # a separate guard breaking the existing "missing" case). The
                # one thing resolve() does not swallow is a genuine symlink
                # loop, and pathlib deliberately reports that as RuntimeError,
                # not OSError -- confirmed empirically too, and confirmed to be
                # a live 3.11-vs-3.13 gap the same shape as the rglob one
                # discover.py already documents: on 3.11.15 a two-symlink loop
                # (a -> b -> a) raises RuntimeError("Symlink loop from ...")
                # uncaught by a bare `except OSError`, which would have crashed
                # this entire scan over one broken symlink; on 3.13.9 the same
                # loop raises nothing at all and resolve() returns the
                # unresolved candidate path instead, which falls through to
                # is_file() correctly returning False. Both are handled here
                # either way, but only 3.11 actually depends on this branch to
                # avoid crashing, and 3.11 is this project's floor and half of
                # CI's matrix.
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
