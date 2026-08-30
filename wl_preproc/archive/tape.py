"""What a person carries to the machine with the drive.

**No bin-packing, deliberately.** At CR ~3.59 a session is ~100 GB and at two
sessions a week that is ~10.4 TB/year, so an LTO-9 cartridge at 18 TB native
holds roughly eighteen months of output. Building a capacity-fitting algorithm
would be inventing work for a constraint that does not bind (design spec
section 7).

**No tape table either.** wl.works Plan 25 section 4 creates
`cold_storage_medium` and `animal_session_cold_copy`. Two records of one
cartridge are two records free to disagree.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TapeEntry:
    session_id: str
    artifact_path: str
    bytes: int
    manifest_digest: str


def staging_manifest(entries: list[TapeEntry]) -> str:
    """A manifest a human can act on and check a write against."""
    if not entries:
        return "No sessions are staged for tape.\n"
    total = sum(e.bytes for e in entries)
    # Decimal GB here (1e9, not 2**30 GiB) — tape capacity is quoted decimal
    # (LTO-9's 18 TB is 18e12). This is the only decimal byte figure in the
    # codebase; all other byte quantities are binary GiB (doctor.py, report.py,
    # watcher.py).
    lines = [
        "Sessions staged for tape",
        f"{len(entries)} session(s), {total / 1e9:.1f} GB total",
        "",
        "Verify each write against its manifest digest before shelving the "
        "cartridge -- the digest is over sorted (relative path, blake3) pairs "
        "of every file in the store.",
        "",
    ]
    for entry in entries:
        lines.append(f"{entry.session_id}")
        lines.append(f"    path   {entry.artifact_path}")
        lines.append(f"    bytes  {entry.bytes}")
        lines.append(f"    digest {entry.manifest_digest}")
        lines.append("")
    return "\n".join(lines)
