"""What the session directory actually contains, versus what it promised.

Spec section 7. This module classifies and never judges. wl.works' governing
rule for every dispatch domain is "silence is `unknown`, never `failed`", and a
device that was not recorded is silence.
"""

from __future__ import annotations

from enum import StrEnum

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
    """
    directory = layout.system_dir(system)
    if not directory.is_dir():
        return False
    marker = layout.done_marker(system)
    return any(p.is_file() and p != marker for p in directory.rglob("*"))


def discover_topology(
    layout: SessionLayout, manifest: SessionManifest
) -> dict[str, SystemState]:
    """One state per member of SYSTEMS. Total by construction, so a caller
    cannot silently skip a system by forgetting it exists."""
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
