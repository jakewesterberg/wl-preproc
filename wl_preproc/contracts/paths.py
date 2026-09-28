"""Session directory layout on a storage root. Frozen interface — see spec section 3.5.

Session *identity* belongs to wl-sync, because the sync box mints it. This module
only decides where a session's files sit under a root.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wl_sync.session import SessionId

SYSTEMS: tuple[str, ...] = ("syncbox", "spikeglx", "rhs", "ohdpi", "bcam")

# Where the experiment controller writes its own log for a session.
#
# **Named for the ROLE, not the vendor**, the same split
# `eye.calibration.CalibrationSource.ONLINE` draws: MonkeyLogic writes a
# `.bhv2` here today, and `wl-xcon` will write whatever it writes.
# A directory called `monkeylogic/` would need renaming the day the second
# controller lands, and every path already written against it would be wrong.
#
# *Renamed 2026-09-28 from `expcontroller` to `xcon`, the requester's decision
# when wl-expcontroller became wl-xcon. `xcon` is the controller's name and
# also short for "experiment controller", so the role reading above still
# holds. No session on disk used the old name. wl-xcon's coordinating
# session said the same day that its session writer switches to `xcon/` only
# once this rename is on wl-preproc's `main`, and that its tests will import
# this constant.*
#
# **Deliberately NOT a `SYSTEMS` entry**, and that is a real distinction
# rather than a naming preference. `SYSTEMS` members are acquisition systems:
# `ingest/discover.py` expects a `DONE` marker under each, `core.
# AcquisitionSystem` records one row per member, and `timebase/extract.py`
# asserts `set(EXTRACTORS) == set(SYSTEMS)` as its own completeness claim --
# a system with no extractor is one that silently never aligns. An experiment
# controller's log carries no barcode and needs no alignment, so adding it
# there would demand an extractor that cannot exist and break that assertion.
# Discovery iterates `SYSTEMS` explicitly, so an extra directory beside them
# is simply ignored rather than treated as an unknown system.
XCON_DIRNAME = "xcon"

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

    @property
    def xcon_dir(self) -> Path:
        """The experiment controller's own log directory for this session.

        Not reached through `system_dir`, which validates against `SYSTEMS`
        and would reject this name -- correctly, for the reason
        `XCON_DIRNAME` gives.
        """
        return self.dir / XCON_DIRNAME

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
