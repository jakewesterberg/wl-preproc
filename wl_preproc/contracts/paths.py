"""Session directory layout on a storage root. Frozen interface — see spec section 3.5.

Session *identity* belongs to wl-sync, because the sync box mints it. This module
only decides where a session's files sit under a root.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wl_sync.session import SessionId

SYSTEMS: tuple[str, ...] = ("syncbox", "spikeglx", "rhs", "ohdpi", "bcam")

MANIFEST_FILENAME = "session_manifest.yaml"
DONE_MARKER_FILENAME = "DONE"


@dataclass(frozen=True, slots=True)
class SessionLayout:
    root: Path
    session_id: SessionId

    @property
    def dir(self) -> Path:
        return self.root / str(self.session_id)

    @property
    def manifest_path(self) -> Path:
        return self.dir / MANIFEST_FILENAME

    def system_dir(self, system: str) -> Path:
        if system not in SYSTEMS:
            raise ValueError(f"unknown system: {system!r}, expected one of {SYSTEMS}")
        return self.dir / system

    def done_marker(self, system: str) -> Path:
        """Written by a transfer when that system's files are complete.

        Session-complete detection waits for every expected system's marker, and
        wl.works' nas_artifact_observation.complete reads the same signal.
        """
        return self.system_dir(system) / DONE_MARKER_FILENAME
