# Phase 0 — Frozen Contracts: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every interface the pipeline depends on executable and machine-readable, so wl.works and the behaviour-camera project can build against them before any hardware exists.

**Architecture:** Each contract is a Pydantic v2 model that rejects unknown keys and exports JSON Schema. The sync box's own contracts — session identity, barcode codec, log format — live in the separate **`wl-sync`** package and are consumed here; the dependency runs one way only, because the sync box owns everything it produces.

**Tech Stack:** Python 3.11, Pydantic v2, pytest, PyYAML, `wl-sync`

**Spec:** [`../specs/2026-08-12-wl-preproc-design.md`](../specs/2026-08-12-wl-preproc-design.md)

**Companion plan:** [`wl-sync` sync box](../../../../wl-sync/docs/superpowers/plans/2026-08-13-sync-box.md) — **implement that first**; Task 1 here imports from it.

## Global Constraints

- **Python 3.11.** Not 3.12+ — the Kilosort4/PyTorch pin for Pascal (`sm_61`, CUDA 12.x) constrains the environment later, and Phase 0 must not diverge.
- **All contract models set `model_config = ConfigDict(extra="forbid")`.** Silent acceptance of a mistyped key is the failure this exists to prevent (spec §5.4).
- **Event codes:** 16-bit parallel plus strobe. Full width to sync box and NI; **strobe only** to Intan RHS, whose 16 digital inputs cannot fit 16 data lines plus strobe plus barcode.
- **No markup in any string wl.works renders.** Plan 10 §4 treats this host as untrusted and renders labels as escaped plain text.
- Timestamps in contracts are timezone-aware ISO 8601. Naive datetimes are rejected.
- **Session identity, the barcode codec and the sync box log format are `wl-sync`'s.** Never reimplement them here.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, deps including `wl-sync`, pytest config |
| `wl_preproc/contracts/paths.py` | Session directory layout on a storage root |
| `wl_preproc/contracts/manifest.py` | `SessionManifest` — what the rig declares about a session |
| `wl_preproc/contracts/events.py` | Event code ranges, task-type codes, escape codes, payload codec |
| `wl_preproc/contracts/sidecar.py` | Behavior-camera sidecar — the contract the FLIR project builds against |
| `wl_preproc/contracts/protocol.py` | wl.works↔wl-preproc responder and request payloads |
| `wl_preproc/cli/main.py` | `wlpp` entry point; `wlpp schemas export` |
| `docs/schemas/*.json` | Exported JSON Schema, committed, for wl.works |

---

### Task 1: Scaffold and session directory layout

**Files:**
- Create: `pyproject.toml`, `wl_preproc/__init__.py`, `wl_preproc/contracts/__init__.py`, `wl_preproc/contracts/paths.py`
- Test: `tests/contracts/test_paths.py`

**Interfaces:**
- Consumes: `SessionId` from `wl_sync.session`
- Produces: constant `SYSTEMS: tuple[str, ...]`; `SessionLayout(root: Path, session_id: SessionId)` with `.dir`, `.manifest_path`, `.system_dir(system) -> Path`, `.done_marker(system) -> Path`; constants `MANIFEST_FILENAME`, `DONE_MARKER_FILENAME`

- [ ] **Step 1: Write the failing test**

```python
# tests/contracts/test_paths.py
from pathlib import Path

import pytest
from wl_sync.session import SessionId

from wl_preproc.contracts.paths import SYSTEMS, SessionLayout


def layout():
    return SessionLayout(Path("/scratch"), SessionId.parse("2027-03-14_01"))


def test_session_dir():
    assert layout().dir == Path("/scratch/2027-03-14_01")


def test_manifest_path():
    assert layout().manifest_path == Path("/scratch/2027-03-14_01/session_manifest.yaml")


def test_system_dir():
    assert layout().system_dir("spikeglx") == Path("/scratch/2027-03-14_01/spikeglx")


def test_done_marker():
    assert layout().done_marker("spikeglx") == Path("/scratch/2027-03-14_01/spikeglx/DONE")


def test_unknown_system_rejected():
    with pytest.raises(ValueError):
        layout().system_dir("telepathy")


def test_systems_are_the_spec_five():
    assert SYSTEMS == ("syncbox", "spikeglx", "rhs", "ohdpi", "bcam")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/contracts/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc'`

- [ ] **Step 3: Write the scaffold and implementation**

```toml
# pyproject.toml
[project]
name = "wl-preproc"
version = "0.1.0"
requires-python = ">=3.11,<3.12"
dependencies = ["pydantic>=2.6", "pyyaml>=6.0", "wl-sync"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
wlpp = "wl_preproc.cli.main:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

```python
# wl_preproc/__init__.py
__all__: list[str] = []
```

```python
# wl_preproc/contracts/__init__.py
__all__: list[str] = []
```

```python
# wl_preproc/contracts/paths.py
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

        Session-complete detection waits for every expected system's marker;
        wl.works' nas_artifact_observation.complete reads the same signal.
        """
        return self.system_dir(system) / DONE_MARKER_FILENAME
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pip install -e ../wl-sync && pip install -e ".[dev]" && pytest tests/contracts/test_paths.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml wl_preproc tests
git commit -m "feat(contracts): session directory layout over wl-sync identity"
```

---

### Task 2: Session manifest schema

**Files:**
- Create: `wl_preproc/contracts/manifest.py`
- Test: `tests/contracts/test_manifest.py`

**Interfaces:**
- Consumes: `SYSTEMS` from `wl_preproc.contracts.paths`; `SessionId` from `wl_sync.session`
- Produces: enum `StartedAtSource` (`BEHAVIORAL_CONTROL`, `SYNCBOX_NTP`); `SessionManifest` (Pydantic: `schema_version: int`, `session_id: str`, `subject: str`, `rig: str`, `started_at: datetime`, `started_at_source: StartedAtSource`, `expected_systems: list[str]`, `acquisition_build_id: str | None`, `stimulus_calibration_id: str | None`, `notes: str | None`); `SessionManifest.from_yaml(text) -> SessionManifest`; `.to_yaml() -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/contracts/test_manifest.py
import datetime

import pytest
from pydantic import ValidationError

from wl_preproc.contracts.manifest import SessionManifest, StartedAtSource

VALID = """
schema_version: 1
session_id: 2027-03-14_01
subject: pico
rig: rig-a
started_at: 2027-03-14T09:12:03+01:00
started_at_source: behavioral_control
expected_systems: [syncbox, spikeglx, ohdpi]
acquisition_build_id: "blake3:0c1f2e"
stimulus_calibration_id: "NIN-RN3_134@2025-01-15"
notes: null
"""


def test_valid_manifest_parses():
    m = SessionManifest.from_yaml(VALID)
    assert m.subject == "pico"
    assert m.started_at_source is StartedAtSource.BEHAVIORAL_CONTROL
    assert m.started_at.utcoffset() == datetime.timedelta(hours=1)


def test_unknown_key_is_rejected():
    """The typo-protection requirement: a near-miss key must fail loudly."""
    with pytest.raises(ValidationError) as exc:
        SessionManifest.from_yaml(VALID + "\nexpceted_systems: [syncbox]\n")
    assert "expceted_systems" in str(exc.value)


def test_naive_datetime_is_rejected():
    with pytest.raises(ValidationError):
        SessionManifest.from_yaml(
            VALID.replace("2027-03-14T09:12:03+01:00", "2027-03-14T09:12:03")
        )


def test_syncbox_must_always_be_expected():
    """The sync box is present at every session type — spec section 1.1."""
    with pytest.raises(ValidationError) as exc:
        SessionManifest.from_yaml(
            VALID.replace("[syncbox, spikeglx, ohdpi]", "[spikeglx, ohdpi]")
        )
    assert "syncbox" in str(exc.value)


def test_unknown_system_is_rejected():
    with pytest.raises(ValidationError):
        SessionManifest.from_yaml(
            VALID.replace("[syncbox, spikeglx, ohdpi]", "[syncbox, telepathy]")
        )


def test_malformed_session_id_is_rejected():
    with pytest.raises(ValidationError):
        SessionManifest.from_yaml(VALID.replace("2027-03-14_01", "March the 14th"))


def test_behavior_only_session_is_valid():
    """A training day has no ephys at all and must still validate."""
    m = SessionManifest.from_yaml(
        VALID.replace("[syncbox, spikeglx, ohdpi]", "[syncbox, ohdpi, bcam]")
    )
    assert m.expected_systems == ["syncbox", "ohdpi", "bcam"]


def test_yaml_round_trip():
    m = SessionManifest.from_yaml(VALID)
    assert SessionManifest.from_yaml(m.to_yaml()) == m
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/contracts/test_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.contracts.manifest'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/contracts/manifest.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/contracts/test_manifest.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/contracts/manifest.py tests/contracts/test_manifest.py
git commit -m "feat(contracts): session manifest with unknown-key rejection"
```

---

### Task 3: Event code protocol

**Files:**
- Create: `wl_preproc/contracts/events.py`
- Test: `tests/contracts/test_events.py`

**Interfaces:**
- Consumes: nothing
- Produces: enums `Marker` (1–255), `TaskTypeCode` (1–255), `Escape` (0x8001+); dataclasses `SimpleEvent(time_s, code)`, `PayloadEvent(time_s, escape, words)`, `DecodeError(time_s, reason)`; `encode_payload(escape, words) -> list[int]`; `decode_stream(words: Sequence[tuple[float, int]]) -> list[SimpleEvent | PayloadEvent | DecodeError]`; `PAYLOAD_WORD_COUNTS: dict[Escape, int]`

- [ ] **Step 1: Write the failing test**

```python
# tests/contracts/test_events.py
import pytest

from wl_preproc.contracts.events import (
    DecodeError,
    Escape,
    Marker,
    PayloadEvent,
    SimpleEvent,
    TaskTypeCode,
    decode_stream,
    encode_payload,
)


def stream(words, t0=0.0):
    return [(t0 + index * 0.001, word) for index, word in enumerate(words)]


def test_payload_round_trip():
    events = decode_stream(stream(encode_payload(Escape.TRIAL_NUMBER, [0x0001, 0x86A0]), 1.0))
    assert events == [
        PayloadEvent(time_s=1.0, escape=Escape.TRIAL_NUMBER, words=(0x0001, 0x86A0))
    ]


def test_trial_number_reconstructs_uint32():
    (event,) = decode_stream(stream(encode_payload(Escape.TRIAL_NUMBER, [0x0001, 0x86A0])))
    assert (event.words[0] << 16) | event.words[1] == 100000


def test_simple_markers_pass_through():
    assert decode_stream([(0.5, Marker.TRIAL_START.value), (0.9, Marker.TRIAL_CORRECT.value)]) == [
        SimpleEvent(time_s=0.5, code=Marker.TRIAL_START.value),
        SimpleEvent(time_s=0.9, code=Marker.TRIAL_CORRECT.value),
    ]


def test_corrupt_checksum_yields_error_not_exception():
    """A corrupt payload must not kill the session's decode."""
    words = encode_payload(Escape.TRIAL_NUMBER, [0x0001, 0x0002])
    words[-1] ^= 0xFFFF
    events = decode_stream(stream(words, 2.0))
    assert len(events) == 1 and isinstance(events[0], DecodeError)
    assert "checksum" in events[0].reason


def test_truncated_payload_yields_error():
    words = encode_payload(Escape.TRIAL_NUMBER, [0x0001, 0x0002])[:-1]
    events = decode_stream(stream(words, 3.0))
    assert len(events) == 1 and isinstance(events[0], DecodeError)
    assert "truncated" in events[0].reason


def test_decode_continues_after_an_error():
    words = encode_payload(Escape.TRIAL_NUMBER, [0x0001, 0x0002])
    words[-1] ^= 0xFFFF
    events = decode_stream(stream(words, 4.0) + [(5.0, Marker.TRIAL_START.value)])
    assert isinstance(events[0], DecodeError)
    assert events[1] == SimpleEvent(time_s=5.0, code=Marker.TRIAL_START.value)


def test_block_start_carries_task_type():
    (event,) = decode_stream(
        stream(encode_payload(Escape.BLOCK_START, [3, TaskTypeCode.RF_MAP.value]), 6.0)
    )
    assert event.escape is Escape.BLOCK_START
    assert TaskTypeCode(event.words[1]) is TaskTypeCode.RF_MAP


def test_wrong_word_count_rejected_at_encode():
    with pytest.raises(ValueError):
        encode_payload(Escape.TRIAL_NUMBER, [0x0001])


def test_word_out_of_16_bit_range_rejected_at_encode():
    with pytest.raises(ValueError):
        encode_payload(Escape.TRIAL_NUMBER, [0x0001, 0x1FFFF])


def test_standing_mapping_tasks_have_reserved_codes():
    """A block stays self-describing in the recording even if the ELN is wrong."""
    assert TaskTypeCode.RESTING_DARK.value == 1
    assert TaskTypeCode.RF_MAP.value == 2
    assert TaskTypeCode.PASSIVE_FLASH.value == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/contracts/test_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.contracts.events'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/contracts/events.py
"""16-bit strobed event code protocol. Frozen interface — see spec section 4.2.

Range allocation:
    1-255       session/block markers and trial outcomes  (Marker)
    256-4095    task events
    4096-32767  task-specific / condition encoding
    32768+      escape codes introducing multi-word payloads  (Escape)

Task type codes are a separate 1-255 namespace carried *inside* a BLOCK_START
payload, so a block is self-describing in the recording even when the ELN is
wrong or late.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum

WORD_MASK = 0xFFFF


class Marker(IntEnum):
    """Session, block and trial markers. Range 1-255."""

    SESSION_START = 1
    SESSION_END = 2
    BLOCK_END = 3
    TRIAL_START = 32
    TRIAL_END = 33
    TRIAL_CORRECT = 34
    TRIAL_ERROR = 35
    TRIAL_ABORT = 36
    TRIAL_FIXATION_BREAK = 37
    TRIAL_NO_RESPONSE = 38


class TaskTypeCode(IntEnum):
    """Standing mapping tasks get reserved codes; lab-defined tasks start at 100."""

    RESTING_DARK = 1
    RF_MAP = 2
    PASSIVE_FLASH = 3
    SHAPE_MAP = 4
    COLOR_MAP = 5
    MEMORY_GUIDED_SACCADE = 6


class Escape(IntEnum):
    """Escape codes introducing multi-word payloads. Range 32768+."""

    TRIAL_NUMBER = 0x8001
    BLOCK_START = 0x8002
    CONDITION = 0x8003


PAYLOAD_WORD_COUNTS: dict[Escape, int] = {
    Escape.TRIAL_NUMBER: 2,  # uint32, high word first
    Escape.BLOCK_START: 2,  # (block_number, task_type_code)
    Escape.CONDITION: 2,  # uint32, high word first
}


@dataclass(frozen=True, slots=True)
class SimpleEvent:
    time_s: float
    code: int


@dataclass(frozen=True, slots=True)
class PayloadEvent:
    time_s: float
    escape: Escape
    words: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DecodeError:
    time_s: float
    reason: str


DecodedEvent = SimpleEvent | PayloadEvent | DecodeError


def _checksum(escape: Escape, words: Sequence[int]) -> int:
    accumulator = int(escape)
    for word in words:
        accumulator ^= word
    return accumulator & WORD_MASK


def encode_payload(escape: Escape, words: Sequence[int]) -> list[int]:
    """Return the full word sequence: escape, payload words, checksum."""
    expected = PAYLOAD_WORD_COUNTS[escape]
    if len(words) != expected:
        raise ValueError(f"{escape.name} takes {expected} words, got {len(words)}")
    for word in words:
        if not 0 <= word <= WORD_MASK:
            raise ValueError(f"payload word out of 16-bit range: {word}")
    return [int(escape), *words, _checksum(escape, words)]


def decode_stream(words: Sequence[tuple[float, int]]) -> list[DecodedEvent]:
    """Decode a strobed word stream into events.

    Never raises on malformed input: a corrupt or truncated payload yields a
    DecodeError and decoding continues, so one bad trial cannot lose a session.
    """
    events: list[DecodedEvent] = []
    index = 0
    while index < len(words):
        time_s, code = words[index]
        try:
            escape = Escape(code)
        except ValueError:
            events.append(SimpleEvent(time_s=time_s, code=code))
            index += 1
            continue

        count = PAYLOAD_WORD_COUNTS[escape]
        needed = count + 1  # payload words plus checksum
        available = words[index + 1 : index + 1 + needed]
        if len(available) < needed:
            events.append(DecodeError(time_s=time_s, reason=f"truncated {escape.name} payload"))
            break

        payload = tuple(word for _, word in available[:count])
        if available[count][1] != _checksum(escape, payload):
            events.append(DecodeError(time_s=time_s, reason=f"{escape.name} checksum mismatch"))
        else:
            events.append(PayloadEvent(time_s=time_s, escape=escape, words=payload))
        index += 1 + needed
    return events
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/contracts/test_events.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/contracts/events.py tests/contracts/test_events.py
git commit -m "feat(contracts): 16-bit event code protocol with checksummed payloads"
```

---

### Task 4: Behavior-camera sidecar contract

**Files:**
- Create: `wl_preproc/contracts/sidecar.py`
- Test: `tests/contracts/test_sidecar.py`

**Interfaces:**
- Consumes: nothing
- Produces: `VideoFile` (Pydantic: `path`, `first_frame_index`, `last_frame_index`, `codec`, `checksum`); `BehaviorCameraSidecar` (Pydantic: `schema_version`, `system`, `trigger_source`, `frame_count`, `dropped_frame_ids`, `video_files`); `.from_yaml(text)`

- [ ] **Step 1: Write the failing test**

```python
# tests/contracts/test_sidecar.py
import pytest
from pydantic import ValidationError

from wl_preproc.contracts.sidecar import BehaviorCameraSidecar

VALID = """
schema_version: 1
system: bcam0
trigger_source: syncbox
frame_count: 1000
dropped_frame_ids: [17, 402]
video_files:
  - path: bcam0_seg000.mp4
    first_frame_index: 0
    last_frame_index: 499
    codec: h264
    checksum: "blake3:aa11"
  - path: bcam0_seg001.mp4
    first_frame_index: 500
    last_frame_index: 999
    codec: h264
    checksum: "blake3:bb22"
"""


def test_valid_sidecar_parses():
    sidecar = BehaviorCameraSidecar.from_yaml(VALID)
    assert sidecar.frame_count == 1000
    assert len(sidecar.video_files) == 2


def test_unknown_key_is_rejected():
    with pytest.raises(ValidationError):
        BehaviorCameraSidecar.from_yaml(VALID + "\nfrmae_rate: 200\n")


def test_trigger_source_must_be_syncbox():
    """Frame times are known by construction only if the sync box triggers them."""
    with pytest.raises(ValidationError):
        BehaviorCameraSidecar.from_yaml(
            VALID.replace("trigger_source: syncbox", "trigger_source: free_running")
        )


def test_video_files_must_be_contiguous():
    with pytest.raises(ValidationError) as exc:
        BehaviorCameraSidecar.from_yaml(VALID.replace("first_frame_index: 500", "first_frame_index: 501"))
    assert "contiguous" in str(exc.value)


def test_frame_count_must_match_segment_coverage():
    with pytest.raises(ValidationError) as exc:
        BehaviorCameraSidecar.from_yaml(VALID.replace("frame_count: 1000", "frame_count: 999"))
    assert "frame_count" in str(exc.value)


def test_reversed_segment_indices_rejected():
    bad = VALID.replace("last_frame_index: 499", "last_frame_index: 0").replace(
        "first_frame_index: 500", "first_frame_index: 1"
    )
    with pytest.raises(ValidationError):
        BehaviorCameraSidecar.from_yaml(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/contracts/test_sidecar.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.contracts.sidecar'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/contracts/sidecar.py
"""Behavior-camera sidecar. Frozen interface — see spec section 4.6.

This is the contract the separate FLIR behaviour-camera project builds against.
Nothing here may change without that project agreeing, which is why it is a
frozen interface rather than an implementation detail.
"""

from __future__ import annotations

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

SCHEMA_VERSION = 1
REQUIRED_TRIGGER_SOURCE = "syncbox"


class VideoFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    first_frame_index: int
    last_frame_index: int
    codec: str
    checksum: str

    @model_validator(mode="after")
    def _indices_ordered(self) -> VideoFile:
        if self.last_frame_index < self.first_frame_index:
            raise ValueError(f"{self.path}: last_frame_index precedes first_frame_index")
        return self


class BehaviorCameraSidecar(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    system: str
    trigger_source: str
    frame_count: int
    dropped_frame_ids: list[int]
    video_files: list[VideoFile]

    @model_validator(mode="after")
    def _check_coverage(self) -> BehaviorCameraSidecar:
        if self.trigger_source != REQUIRED_TRIGGER_SOURCE:
            raise ValueError(
                f"trigger_source must be {REQUIRED_TRIGGER_SOURCE!r}: frame times are "
                "known by construction only when the sync box triggers the camera"
            )
        expected_next = 0
        for video in sorted(self.video_files, key=lambda f: f.first_frame_index):
            if video.first_frame_index != expected_next:
                raise ValueError(
                    f"{video.path}: video files must be contiguous, expected first frame "
                    f"{expected_next}, got {video.first_frame_index}"
                )
            expected_next = video.last_frame_index + 1
        if expected_next != self.frame_count:
            raise ValueError(
                f"frame_count {self.frame_count} does not match segment coverage {expected_next}"
            )
        return self

    @classmethod
    def from_yaml(cls, text: str) -> BehaviorCameraSidecar:
        return cls.model_validate(yaml.safe_load(text))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/contracts/test_sidecar.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/contracts/sidecar.py tests/contracts/test_sidecar.py
git commit -m "feat(contracts): behavior-camera sidecar schema"
```

---

### Task 5: wl.works protocol payloads

**Files:**
- Create: `wl_preproc/contracts/protocol.py`
- Test: `tests/contracts/test_protocol.py`

**Interfaces:**
- Consumes: nothing
- Produces: `contains_markup(text) -> bool`; `Reading(key, label, value, featured)`; `Action(name, label)`; `HealthResponse(verdict, readings, actions)`; `MetadataBundle(blocks, montage_boundaries, probes, experimenter, subject, task_types)`; `JobRequest(domain, selection, parameters, idempotency_key, metadata)`

- [ ] **Step 1: Write the failing test**

```python
# tests/contracts/test_protocol.py
import pytest
from pydantic import ValidationError

from wl_preproc.contracts.protocol import (
    Action,
    HealthResponse,
    JobRequest,
    MetadataBundle,
    Reading,
    contains_markup,
)

# Verbatim from wl.works Plan 10 section 4.
PLAN_10_EXAMPLE = {
    "verdict": "ok",
    "readings": [
        {"key": "transfer", "label": "Latest transfer", "value": "complete", "featured": False},
        {"key": "spike-sort", "label": "Spike sorting", "value": "4 of 7 sessions", "featured": True},
    ],
    "actions": [{"name": "start-preproc", "label": "Start preprocessing run"}],
}


def test_plan_10_example_validates():
    response = HealthResponse.model_validate(PLAN_10_EXAMPLE)
    assert response.verdict == "ok"
    assert response.readings[1].featured is True
    assert response.actions[0].name == "start-preproc"


@pytest.mark.parametrize("text", ["<b>bold</b>", "plain <img src=x>", "a & b", "line<br>break"])
def test_contains_markup_detects(text):
    assert contains_markup(text) is True


@pytest.mark.parametrize("text", ["complete", "4 of 7 sessions", "rig-a: 12 units"])
def test_contains_markup_allows_plain(text):
    assert contains_markup(text) is False


def test_reading_rejects_markup_in_label():
    """wl.works treats this host as untrusted and renders labels as escaped text."""
    with pytest.raises(ValidationError):
        Reading(key="k", label="<b>Spike sorting</b>", value="ok", featured=False)


def test_reading_rejects_markup_in_value():
    with pytest.raises(ValidationError):
        Reading(key="k", label="Spike sorting", value="4 <b>of</b> 7", featured=False)


def test_action_rejects_markup_in_label():
    with pytest.raises(ValidationError):
        Action(name="start-preproc", label="Start <i>preprocessing</i>")


def test_health_response_rejects_unknown_key():
    with pytest.raises(ValidationError):
        HealthResponse.model_validate({**PLAN_10_EXAMPLE, "verdcit": "ok"})


def test_job_request_carries_the_metadata_bundle():
    """wl-preproc cannot fetch from wl.works, so metadata must arrive inbound."""
    request = JobRequest(
        domain="neural",
        selection={"session_id": "2027-03-14_01", "montage_id": 1},
        parameters={"clustering_paramset": "ks4_default"},
        idempotency_key="a1b2c3",
        metadata=MetadataBundle(
            blocks=[{"block_id": 1, "task_type": "rf_map"}],
            montage_boundaries=[{"montage_id": 1, "start_s": 0.0, "end_s": 3600.0}],
            probes=[{"serial": "NP-1234", "insertion_number": 1}],
            experimenter="jw",
            subject="pico",
            task_types=["rf_map", "resting_dark"],
        ),
    )
    assert request.metadata.subject == "pico"
    assert request.metadata.montage_boundaries[0]["montage_id"] == 1


def test_job_request_rejects_unknown_key():
    with pytest.raises(ValidationError):
        JobRequest.model_validate(
            {
                "domain": "neural",
                "selection": {},
                "parameters": {},
                "idempotency_key": "x",
                "metadata": {
                    "blocks": [],
                    "montage_boundaries": [],
                    "probes": [],
                    "experimenter": "jw",
                    "subject": "pico",
                    "task_types": [],
                },
                "priorty": 3,
            }
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/contracts/test_protocol.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.contracts.protocol'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/contracts/protocol.py
"""wl.works <-> wl-preproc protocol. Frozen interface — see spec section 11.2.

Transport is pull-only: wl.works opens every connection and this host never
initiates, because the app binds only to a WireGuard interface and this machine
is on the lab LAN. Consequently everything wl-preproc needs from the ELN arrives
in the request payload, which is why JobRequest carries a MetadataBundle.

wl.works renders our strings as escaped plain text because a compromised host
controls its UI. We refuse to emit markup at all rather than relying on that.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

SCHEMA_VERSION = 1

_MARKUP_RE = re.compile(r"[<>&]")


def contains_markup(text: str) -> bool:
    """True if text holds any character that could be interpreted as markup."""
    return _MARKUP_RE.search(text) is not None


def _reject_markup(value: str) -> str:
    if contains_markup(value):
        raise ValueError(f"markup is not permitted in rendered strings: {value!r}")
    return value


class Reading(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    value: str
    featured: bool

    @field_validator("label", "value")
    @classmethod
    def _plain_text_only(cls, value: str) -> str:
        return _reject_markup(value)


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    label: str

    @field_validator("label")
    @classmethod
    def _plain_text_only(cls, value: str) -> str:
        return _reject_markup(value)


class HealthResponse(BaseModel):
    """Served at the health check URL wl.works polls.

    The host publishes its own action list, so adding a sixth job type needs no
    change on the wl.works side.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: str
    readings: list[Reading]
    actions: list[Action]


class MetadataBundle(BaseModel):
    """Everything wl-preproc needs from the ELN, carried inbound with the request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    blocks: list[dict[str, Any]]
    montage_boundaries: list[dict[str, Any]]
    probes: list[dict[str, Any]]
    experimenter: str
    subject: str
    task_types: list[str]


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: str
    selection: dict[str, Any]
    parameters: dict[str, Any]
    idempotency_key: str
    metadata: MetadataBundle
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/contracts/test_protocol.py -v`
Expected: PASS, 13 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/contracts/protocol.py tests/contracts/test_protocol.py
git commit -m "feat(contracts): wl.works protocol payloads with markup rejection"
```

---

### Task 6: Schema export CLI

**Files:**
- Create: `wl_preproc/cli/__init__.py`, `wl_preproc/cli/main.py`
- Test: `tests/cli/test_schemas_export.py`

**Interfaces:**
- Consumes: `SessionManifest`, `BehaviorCameraSidecar`, `HealthResponse`, `JobRequest`; `SyncBoxLogHeader` from `wl_sync.log`
- Produces: `EXPORTED_MODELS: dict[str, type[BaseModel]]`; `export_schemas(out_dir: Path) -> list[Path]`; `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_schemas_export.py
import json

from wl_preproc.cli.main import EXPORTED_MODELS, export_schemas, main


def test_exports_one_file_per_model(tmp_path):
    written = export_schemas(tmp_path)
    assert {p.name for p in written} == {f"{name}.json" for name in EXPORTED_MODELS}


def test_exported_files_are_valid_json_schema(tmp_path):
    for path in export_schemas(tmp_path):
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert "properties" in schema
        assert schema["title"]


def test_sidecar_schema_is_exported_for_the_camera_project(tmp_path):
    export_schemas(tmp_path)
    schema = json.loads((tmp_path / "behavior_camera_sidecar.json").read_text())
    assert "video_files" in schema["properties"]


def test_job_request_schema_is_exported_for_wl_works(tmp_path):
    export_schemas(tmp_path)
    schema = json.loads((tmp_path / "job_request.json").read_text())
    assert "idempotency_key" in schema["properties"]


def test_cli_writes_to_requested_directory(tmp_path):
    assert main(["schemas", "export", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "session_manifest.json").exists()


def test_cli_unknown_command_returns_nonzero():
    assert main(["nonsense"]) != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cli/test_schemas_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.cli'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/cli/__init__.py
__all__: list[str] = []
```

```python
# wl_preproc/cli/main.py
"""wlpp entry point.

`wlpp schemas export` writes JSON Schema for every frozen contract. wl.works
builds its half of the protocol against these, and its 18b tests run against a
fake wl-preproc — which is only possible if the contract is machine-readable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel
from wl_sync.log import SyncBoxLogHeader

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.protocol import HealthResponse, JobRequest
from wl_preproc.contracts.sidecar import BehaviorCameraSidecar

EXPORTED_MODELS: dict[str, type[BaseModel]] = {
    "session_manifest": SessionManifest,
    "behavior_camera_sidecar": BehaviorCameraSidecar,
    "syncbox_log_header": SyncBoxLogHeader,
    "health_response": HealthResponse,
    "job_request": JobRequest,
}


def export_schemas(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in EXPORTED_MODELS.items():
        path = out_dir / f"{name}.json"
        schema = model.model_json_schema()
        schema.setdefault("title", model.__name__)
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wlpp")
    subparsers = parser.add_subparsers(dest="group", required=True)

    schemas = subparsers.add_parser("schemas", help="contract schema tools")
    schemas_sub = schemas.add_subparsers(dest="action", required=True)
    export = schemas_sub.add_parser("export", help="write JSON Schema for every contract")
    export.add_argument("--out", default="docs/schemas", type=Path)

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 2)

    if args.group == "schemas" and args.action == "export":
        for path in export_schemas(args.out):
            print(path)
        return 0
    return 2
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest -v`
Expected: PASS, all tests across six modules

- [ ] **Step 5: Export the schemas and commit them**

```bash
python -m wl_preproc.cli.main schemas export --out docs/schemas
git add wl_preproc/cli tests/cli docs/schemas
git commit -m "feat(cli): wlpp schemas export, and commit the exported contracts"
```

---

## Definition of done for Phase 0

- `pytest` green in both `wl-sync` and `wl-preproc`
- `docs/schemas/*.json` committed, so wl.works and the camera project can build against them
- Every frozen interface in spec §3.5 has an executable schema except the NWB target layout, which lands in Phase 3 with the export code

## Deliberately not in this plan

- **Session identity, barcode codec, sync box log format, firmware** — the [`wl-sync`](../../../../wl-sync/docs/superpowers/plans/2026-08-13-sync-box.md) plan
- **Timebase fitting** (pooled rate per system, offset per segment) — Phase 1, needs the ingest watcher
- **DataJoint schemas, the ingest watcher, the responder HTTP surface** — Phase 1; only the responder's payload schemas are frozen here
- **The synthetic session generator** — Phase 1, consumes every contract frozen here
- **The breakout PCB** — hardware design, tracked in spec §4.4
- **NWB target layout** — Phase 3

## Open items this plan does not resolve

Spec §13 items 4, 10 and 17 are inputs to Phase 1: the MonkeyLogic 16-line DAQ configuration, the X-hour canonical delay, and whether the event-code range allocation survives contact with real tasks. Task 3 commits a *starting* allocation; the reserved task-type codes are the part hardest to change later, which is why they are pinned now.
