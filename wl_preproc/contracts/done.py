"""What a transfer declares when it finishes a system's files. Frozen interface
— see spec section 3.5, amended 2026-08-15 by spec section 5.

The marker's *existence* is the session-complete signal and that is unchanged:
wl.works' `nas_artifact_observation.complete` reads presence, and presence still
means exactly what it meant. The body is additional, and an empty DONE stays
legal — it means "complete, no integrity data", recorded as `declared_only`
rather than silently treated as verified.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import blake3 as _blake3
import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from wl_preproc.contracts.paths import SYSTEMS

DONE_SCHEMA_VERSION = 1


class FileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str  # relative to the system directory
    bytes: int
    blake3: str


class DoneMarker(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    system: str
    transfer_finished_at: datetime.datetime
    files: list[FileEntry]

    @field_validator("system")
    @classmethod
    def _known_system(cls, value: str) -> str:
        if value not in SYSTEMS:
            raise ValueError(f"unknown system: {value!r}, expected from {list(SYSTEMS)}")
        return value

    @field_validator("transfer_finished_at")
    @classmethod
    def _must_be_aware(cls, value: datetime.datetime) -> datetime.datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("transfer_finished_at must be timezone-aware")
        return value

    @classmethod
    def from_yaml(cls, text: str) -> DoneMarker:
        return cls.model_validate(yaml.safe_load(text))

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False)


def blake3_file(path: Path, chunk_bytes: int = 4 * 1024 * 1024) -> str:
    """The digest this contract's `blake3` field means.

    Streamed rather than read whole: a SpikeGLX .bin is hundreds of gigabytes
    and `Path.read_bytes()` on one would be an out-of-memory bug rather than a
    slow path.
    """
    digest = _blake3.blake3()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()
