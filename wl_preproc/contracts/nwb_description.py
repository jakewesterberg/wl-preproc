"""What an NWB file holds, for wl.works' dataset builder to select on without
opening it. Frozen interface: exported to `docs/schemas/nwb_description.json`
and checked in CI (design spec `2026-09-29-nwb-publishing-design.md`
section 2).

**Versioned and open.** `schema_version` is 1. Later pieces add groups and
fields -- the probes and areas, a processing summary with unit counts, the
photodiode, video and stimulation -- and never rename or remove one within a
version. This side validates strictly (`extra="forbid"`), so what it publishes
is exactly this; a READER must ignore fields it does not know, which is what
lets a later version add them.

**Not here:** the file's location, which changes when it moves between the
fast and slow shares (`GET /nwb` reports it), and the experimenter's notes and
experiment links, which wl.works already holds."""

from __future__ import annotations

import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = 1


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Pipeline(_Frozen):
    """The code that built the file. The commit is the provenance: the
    package's own version is a constant that has never moved, and reading it
    needs `importlib`, which `tests/test_cli_guardrails.py` bans outright."""

    name: str
    commit: str | None


class Identity(_Frozen):
    identifier: str
    subject: str
    session_id: str
    session_datetime: datetime.datetime
    rig: str | None
    montage_id: int
    activation_id: int
    role: Literal["canonical", "derivative"]
    # The activation this one supersedes, or null (never `supersedes`: the
    # guardrail that nothing writes `Activation.supersedes` scans for it).
    supersedes_activation_id: int | None
    built_at: datetime.datetime
    pipeline: Pipeline
    status: Literal["written", "invalid"]
    n_critical: int


class SubjectInfo(_Frozen):
    species: str | None
    sex: Literal["M", "F", "U"]
    date_of_birth: datetime.date | None
    age_days: int | None


class EyeData(_Frozen):
    gaze: list[Literal["left", "right"]]
    pupil: list[Literal["left", "right"]]


class EyeEvents(_Frozen):
    detectors: list[str]


class Behaviour(_Frozen):
    trials: int
    events: int


class DataTypes(_Frozen):
    eye: EyeData | None
    eye_events: EyeEvents | None
    behaviour: Behaviour
    # Present and null until their pieces fill them.
    ephys: dict[str, Any] | None = None
    photodiode: dict[str, Any] | None = None
    video: dict[str, Any] | None = None
    stimulation: dict[str, Any] | None = None


class TrialCounts(_Frozen):
    total: int
    by_outcome: dict[str, int]


class Condition(_Frozen):
    """One condition that ran in a block: by its name in the rig's record,
    with the settings constant across its trials and a summary of those that
    varied; or, without that record, by the stream's CONDITION number with
    settings unknown (null)."""

    name: str | None
    code: int | None
    settings: dict[str, Any] | None
    varying: dict[str, dict[str, Any]] | None
    trials: TrialCounts


class Interval(_Frozen):
    start_s: float
    stop_s: float


class Coverage(_Frozen):
    coverage: str
    covered_s: float


class Task(_Frozen):
    code: str
    name: str


class Block(_Frozen):
    block_id: int
    works_block_id: str | None
    task: Task
    asserted: Interval
    measured: Interval | None
    trials: TrialCounts
    coverage: dict[str, Coverage]
    conditions: list[Condition]


class Quality(_Frozen):
    timing_tier: str
    reference_source: Literal["barcode", "manifest"]
    eye_usable_fraction: dict[Literal["left", "right"], float | None]


class DatasetChecksum(_Frozen):
    dataset_path: str
    dtype: str
    shape: str
    sha256: str
    paired_with: str


class Checksums(_Frozen):
    algorithm: Literal["sha256"]
    datasets: list[DatasetChecksum]


class NwbDescription(_Frozen):
    schema_version: Literal[1] = SCHEMA_VERSION
    identity: Identity
    subject: SubjectInfo
    data_types: DataTypes
    # Empty in version 1; piece 3 describes each probe (type, serial,
    # insertion, trajectory, target areas, per-channel areas).
    probes: list[dict[str, Any]] = []
    blocks: list[Block]
    quality: Quality
    # Empty in version 1; piece 3 adds the processing summary (for example
    # the number of single units, and whether any narrow-waveform units).
    processing: dict[str, Any] = {}
    # Why any trial's condition or settings are unknown.
    notes: list[str]
    checksums: Checksums
