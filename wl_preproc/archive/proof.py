"""The checks run against the NAS copy immediately before scratch is freed,
and the two run before a session is restored (2026-09-26 rehydration design,
sections 3 and 5.2).

**Reads the NAS copy, never a row alone.** The reclamation predicate is
computed from rows written at archive time -- possibly days earlier, possibly
by an older build -- and its `artifact_present` condition is a row. These
checks read the artifact itself: the completion sentinel is there, the tree
is byte-for-byte the one confirmed at archive time, and every file the rig
checksummed still rebuilds, with THIS build's code, to the rig's digest. No
force can skip them: a force changes whether a session is ready, and nothing
changes whether its archive is proven.

No database here. Callers pass the recorded digest and the rig's digests in,
so every check runs against a plain directory in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wl_preproc.archive.stage import SENTINEL_NAME
from wl_preproc.archive.store import manifest_digest
from wl_preproc.archive.verify import FileVerdict, verify_against


@dataclass(frozen=True, slots=True)
class Proof:
    passed: bool
    # Empty when `passed`. Otherwise which check failed and what it saw, as a
    # sentence the CLI prints after "refusing: ".
    reason: str
    verdicts: tuple[FileVerdict, ...] = ()


def prove_artifact(
    artifact: Path, recorded_digest: str, expected: dict[str, str] | None
) -> Proof:
    """Sentinel, then manifest digest, then -- when `expected` is given --
    every rig-checksummed file rebuilt and hashed. Stops at the first failure.

    `expected=None` runs only the first two. That is rehydration's preflight:
    rehydration rebuilds every file anyway, writing it, and checks each one
    then. `expected={}` is refused rather than vacuously proven: "nothing to
    check" must never read as "proven", the same rule
    `verify.py::_expected_digests` applies at archive time.
    """
    sentinel = artifact / SENTINEL_NAME
    # Guarded like `cli/report.py::_verified_archives`' own probe: a
    # permission fault on a NAS mount is "not confirmed", never a crash.
    try:
        present = sentinel.is_file()
    except OSError:
        present = False
    if not present:
        return Proof(False, f"no completion sentinel at {sentinel}")

    try:
        actual = manifest_digest(artifact, exclude=frozenset({SENTINEL_NAME}))
    except OSError as exc:
        return Proof(False, f"could not read {artifact}: {exc}")
    if actual != recorded_digest:
        return Proof(
            False,
            f"{artifact} has changed since it was archived: its manifest digest "
            f"is {actual}, the recorded one {recorded_digest}",
        )

    if expected is None:
        return Proof(True, "")
    if not expected:
        return Proof(
            False,
            "no recorded rig digests to rebuild against; nothing to check is never proven",
        )
    verdicts = tuple(verify_against(artifact, expected))
    failed = [v for v in verdicts if not v.matched]
    if failed:
        return Proof(
            False,
            f"{len(failed)} of {len(verdicts)} file(s) did not rebuild to the rig's digest",
            verdicts,
        )
    return Proof(True, "", verdicts)
