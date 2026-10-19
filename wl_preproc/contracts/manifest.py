"""What a rig declares about a session. Frozen interface — see spec section 3.5."""

from __future__ import annotations

import datetime
from enum import Enum

import yaml
from pydantic import BaseModel, ConfigDict, field_validator
from wl_sync.session import SessionId

from wl_preproc.contracts.paths import SYSTEMS

SCHEMA_VERSION = 1


class StartedAtSource(str, Enum):
    """Which clock stamped the session's label.

    The behavioural control system where present; the sync box's NTP-stamped
    start otherwise, since anaesthetised mapping and spontaneous-activity
    sessions have no task PC. This is the session *label* only — which clock
    keeps the session's time is `timebase.SessionClock`'s choice (design spec
    `2026-10-18-training-records-and-backup-clock-design.md` section 4).

    *Until part 3a of that design this said the timebase is always the sync
    box.*
    """

    BEHAVIORAL_CONTROL = "behavioral_control"
    SYNCBOX_NTP = "syncbox_ntp"


class SessionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    session_id: str
    subject: str
    rig: str
    started_at: datetime.datetime
    started_at_source: StartedAtSource
    expected_systems: list[str]
    # wl-xcon's record of the session: when true, the session lands only once
    # `xcon/DONE` is written as well as every listed system's marker, so a
    # session with no acquisition system does not land while the rig still
    # writes (design spec `2026-10-18-training-records-and-backup-clock-design.md`
    # section 5). A manifest without it behaves exactly as before.
    expects_rig_record: bool = False
    acquisition_build_id: str | None = None
    stimulus_calibration_id: str | None = None
    notes: str | None = None

    @field_validator("session_id")
    @classmethod
    def _session_id_well_formed(cls, value: str) -> str:
        SessionId.parse(value)
        return value

    @field_validator("started_at")
    @classmethod
    def _must_be_aware(cls, value: datetime.datetime) -> datetime.datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")
        return value

    @field_validator("expected_systems")
    @classmethod
    def _known(cls, value: list[str]) -> list[str]:
        """Every listed system is one this pipeline knows. The sync box is
        listed when it is running at the session's open, which it should be
        at every session; one without it is still taken in (design spec
        `2026-10-18-training-records-and-backup-clock-design.md` section 5).
        *Until part 3a this refused a manifest that did not list it.*"""
        unknown = [system for system in value if system not in SYSTEMS]
        if unknown:
            raise ValueError(f"unknown systems: {unknown}, expected from {list(SYSTEMS)}")
        return value

    @classmethod
    def from_yaml(cls, text: str) -> SessionManifest:
        return cls.model_validate(yaml.safe_load(text))

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False)
