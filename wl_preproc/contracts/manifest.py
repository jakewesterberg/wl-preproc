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
    sessions have no task PC. This is the session *label* only — the timebase is
    always the sync box.
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
    def _known_and_include_syncbox(cls, value: list[str]) -> list[str]:
        unknown = [system for system in value if system not in SYSTEMS]
        if unknown:
            raise ValueError(f"unknown systems: {unknown}, expected from {list(SYSTEMS)}")
        if "syncbox" not in value:
            raise ValueError("syncbox must be present at every session")
        return value

    @classmethod
    def from_yaml(cls, text: str) -> SessionManifest:
        return cls.model_validate(yaml.safe_load(text))

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False)
