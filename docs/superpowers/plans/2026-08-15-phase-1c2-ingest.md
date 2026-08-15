# Phase 1c-2 — Ingest watcher and daily report: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a session directory that has finished landing on the server into `Subject`/`Session`/`AcquisitionSystem`/`Ingestion` rows the populate daemon computes from, and make stalled transfers and failed validation visible in a daily report.

**Architecture:** A polling `scan_once(root, prefix)` examines each immediate child of a storage root. A session is complete when every system in the manifest's `expected_systems` has a `DONE` marker; the marker's body lists that system's files with size and blake3, which is re-verified at the destination. Filesystem logic (`sentinel`, `verify`, `discover`) is separated from the single module that touches DataJoint (`landing`), so most of the sub-project tests without a database.

**Tech Stack:** Python ≥3.11, DataJoint 2.3.x, pydantic v2, blake3, pytest, testcontainers[mysql].

**Spec:** [`docs/superpowers/specs/2026-08-15-phase-1c2-ingest-design.md`](../specs/2026-08-15-phase-1c2-ingest-design.md) — read it before Task 1; the plan argues from it.

## Global Constraints

- Python `>=3.11`; `datajoint>=2.3,<3`. All five git dependencies are commit-pinned in `pyproject.toml`; do not change a pin.
- **Never a bare `longblob` — always `<blob>`.** `tests/schema/test_guardrails.py` sweeps every declared attribute and fails otherwise.
- **No bare `.delete()` anywhere in `wl_preproc/`.** `.delete_quick()` is permitted. A guardrail test enforces this.
- `.fetch()` is deprecated in DataJoint 2.3 — use `.to_arrays()`, `.keys()`, or `.fetch1()`.
- **One schema prefix per process.** Activating a second raises. Tests share the `prefix` fixture from `tests/schema/conftest.py` (`"t_"`); production default is `DEFAULT_PREFIX = "wlpp_"` from `wl_preproc/schema/__init__.py`.
- **Test output must stay pristine — 0 warnings.** The suite is at 238 passing.
- `submit()` is **never called** by anything in this plan (spec §2). A test pins that `Request.origin='ingest'` has no writer.
- Run tests as `.venv/bin/python -m pytest` from the repo root — never `.venv/bin/pytest`. Use `uv pip`, not `.venv/bin/pip`.
- Commit messages end with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## File structure

| File | Responsibility |
|---|---|
| `wl_preproc/contracts/done.py` | **New frozen interface.** `DoneMarker`/`FileEntry` models; `blake3_file()` defines what the `blake3` field means. |
| `wl_preproc/ingest/sentinel.py` | Read `DONE` markers; is the session complete; is it stalled. No hashing, no DB. |
| `wl_preproc/ingest/discover.py` | Per-system `present`/`absent`/`undeclared`/`pending`. No DB. |
| `wl_preproc/ingest/verify.py` | Re-hash and compare against the `DONE` body. No DB. |
| `wl_preproc/ingest/params.py` | `session_params.yaml` validation and paramset registration. |
| `wl_preproc/ingest/landing.py` | **The only ingest module that imports `datajoint`.** |
| `wl_preproc/ingest/watcher.py` | `scan_once()` — orchestrator and sole public entry point. |
| `wl_preproc/schema/ingest.py` | Schema `{prefix}ingest`: `Ingestion`, `Quarantine`. |
| `wl_preproc/cli/report.py` | The daily report. Reads; never writes. |

---

### Task 1: The `DONE` marker contract, blake3, and the generator that writes it

Spec §5. This is a frozen-interface change (§3.5 #1), so the contract, its JSON Schema export, and the generator that produces it move together — a contract with no producer cannot be tested.

**Files:**
- Create: `wl_preproc/contracts/done.py`
- Create: `tests/contracts/test_done.py`
- Modify: `pyproject.toml` (add `blake3` to `dependencies`)
- Modify: `wl_preproc/cli/main.py` (register in `EXPORTED_MODELS`)
- Modify: `wl_preproc/synth/session.py` (write the marker body)
- Generated: `docs/schemas/done_marker.json`

**Interfaces:**
- Consumes: `wl_preproc.contracts.paths.SessionLayout`, `DONE_MARKER_FILENAME`, `SYSTEMS`.
- Produces:
  - `class FileEntry(BaseModel)` — `path: str`, `bytes: int`, `blake3: str`
  - `class DoneMarker(BaseModel)` — `schema_version: int`, `system: str`, `transfer_finished_at: datetime`, `files: list[FileEntry]`; `.from_yaml(text) -> DoneMarker`, `.to_yaml() -> str`
  - `DONE_SCHEMA_VERSION: int = 1`
  - `blake3_file(path: Path, chunk_bytes: int = 4 * 1024 * 1024) -> str`

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add to the `dependencies` list, keeping the existing style:

```toml
    # blake3 rather than hashlib.blake2b: spec section 4.6's behaviour-camera
    # sidecar is a frozen interface that already specifies `checksum: <blake3>`,
    # so the algorithm was committed to before ingest existed. Measured
    # 2026-08-15 at 1.83 GB/s vs blake2b's 1.15 GB/s; a 360 GB session verifies
    # in ~3.3 min. Wheels published for cp311 and cp313, which is what CI runs.
    "blake3>=1.0,<2",
```

Then run: `uv pip install -e ".[dev]"`

- [ ] **Step 2: Write the failing test**

Create `tests/contracts/test_done.py`:

```python
"""The DONE marker's body. Its *existence* is the completion signal and always
was; the body is what makes transfer integrity checkable at the destination."""

from __future__ import annotations

import datetime

import pytest
from pydantic import ValidationError

from wl_preproc.contracts.done import (
    DONE_SCHEMA_VERSION,
    DoneMarker,
    FileEntry,
    blake3_file,
)


def _marker() -> DoneMarker:
    return DoneMarker(
        schema_version=DONE_SCHEMA_VERSION,
        system="spikeglx",
        transfer_finished_at=datetime.datetime(2027, 3, 14, 19, 4, 11, tzinfo=datetime.UTC),
        files=[FileEntry(path="run0_g0_t0.imec0.ap.bin", bytes=1024, blake3="9f2c")],
    )


def test_round_trips_through_yaml():
    assert DoneMarker.from_yaml(_marker().to_yaml()) == _marker()


def test_rejects_unknown_keys():
    """extra="forbid", for the same reason session_params.yaml rejects them:
    a typo must fail loudly rather than silently defaulting."""
    text = _marker().to_yaml() + "\nnfiles: 3\n"
    with pytest.raises(ValidationError):
        DoneMarker.from_yaml(text)


def test_rejects_naive_transfer_time():
    with pytest.raises(ValidationError):
        DoneMarker(
            schema_version=DONE_SCHEMA_VERSION,
            system="spikeglx",
            transfer_finished_at=datetime.datetime(2027, 3, 14, 19, 4, 11),
            files=[],
        )


def test_rejects_unknown_system():
    with pytest.raises(ValidationError):
        DoneMarker(
            schema_version=DONE_SCHEMA_VERSION,
            system="not_a_system",
            transfer_finished_at=datetime.datetime(2027, 3, 14, tzinfo=datetime.UTC),
            files=[],
        )


def test_blake3_file_matches_the_reference_digest(tmp_path):
    """Pinned against blake3's own digest of the same bytes, so a chunking bug
    in the streaming read cannot pass. A self-consistent test that hashed the
    file twice with the same helper would prove nothing."""
    import blake3

    payload = b"x" * (9 * 1024 * 1024 + 7)  # spans chunks, ends ragged
    target = tmp_path / "big.bin"
    target.write_bytes(payload)

    assert blake3_file(target, chunk_bytes=4 * 1024 * 1024) == blake3.blake3(payload).hexdigest()
```

- [ ] **Step 3: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/contracts/test_done.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.contracts.done'`

- [ ] **Step 4: Write the contract**

Create `wl_preproc/contracts/done.py`:

```python
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
```

- [ ] **Step 5: Run the test again**

Run: `.venv/bin/python -m pytest tests/contracts/test_done.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 6: Register the JSON Schema export**

In `wl_preproc/cli/main.py`, add the import beside the existing contract imports and one entry to `EXPORTED_MODELS`:

```python
from wl_preproc.contracts.done import DoneMarker
```

```python
    "done_marker": DoneMarker,
```

- [ ] **Step 7: Regenerate and verify the export is current**

Run: `.venv/bin/python -m wl_preproc.cli.main schemas export`
Run: `git status --porcelain docs/schemas/`
Expected: `docs/schemas/done_marker.json` appears as a new file. CI runs this same export and `git diff --exit-code`s the directory, so an unregenerated export is a red build.

- [ ] **Step 8: Make the generator write the body**

In `wl_preproc/synth/session.py`, the loop currently writes an empty `DONE` after each system's files. Replace that write with one that hashes what it just wrote. The generator is the fixture source for every later task, so this is not optional polish.

```python
from wl_preproc.contracts.done import DONE_SCHEMA_VERSION, DoneMarker, FileEntry, blake3_file

def _write_done_marker(layout: SessionLayout, system: str, finished_at: datetime.datetime) -> None:
    """Hash every file this system wrote, and declare them.

    `rglob` rather than a caller-supplied list: the rhs writer emits a nested
    directory, and a marker that silently omitted those files would make the
    verification step pass while proving nothing.
    """
    system_dir = layout.system_dir(system)
    marker_path = layout.done_marker(system)
    entries = [
        FileEntry(
            path=str(candidate.relative_to(system_dir)),
            bytes=candidate.stat().st_size,
            blake3=blake3_file(candidate),
        )
        for candidate in sorted(system_dir.rglob("*"))
        if candidate.is_file() and candidate != marker_path
    ]
    marker = DoneMarker(
        schema_version=DONE_SCHEMA_VERSION,
        system=system,
        transfer_finished_at=finished_at,
        files=entries,
    )
    marker_path.write_text(marker.to_yaml(), encoding="utf-8")
```

Call it where the empty write was. For `finished_at`, use the recipe's session start plus its duration — the generator must not call `datetime.now()`, because two runs of the same recipe must produce identical trees for the existing determinism tests.

- [ ] **Step 9: Add the generator test**

Append to `tests/synth/test_session.py`:

```python
def test_every_system_declares_the_files_it_wrote(tmp_path):
    """The marker must list what is actually on disk. A marker listing nothing
    would satisfy verification trivially, which is the failure this catches."""
    from wl_preproc.contracts.done import DoneMarker

    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, CI_RECIPE.session_id)

    for system in CI_RECIPE.systems:
        marker = DoneMarker.from_yaml(layout.done_marker(system).read_text())
        system_dir = layout.system_dir(system)
        on_disk = {
            str(p.relative_to(system_dir))
            for p in system_dir.rglob("*")
            if p.is_file() and p.name != "DONE"
        }
        assert {entry.path for entry in marker.files} == on_disk
        for entry in marker.files:
            assert (system_dir / entry.path).stat().st_size == entry.bytes
```

- [ ] **Step 10: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, 0 warnings. Existing synth determinism tests must still pass — if one fails, `finished_at` is being taken from the clock rather than the recipe.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(contracts): the DONE marker gains a body, and blake3 arrives with it

Its existence stays the session-complete signal, unchanged, so wl.works'
nas_artifact_observation.complete reads exactly what it read before. The body
lists each file with size and blake3 so the destination can verify what the
transfer claims it sent — rsync verifies in flight, not at rest, and a file
that landed correctly and then met a bad block passes every check the transfer
tool makes.

blake3 rather than the stdlib blake2b already used for paramset hashing:
section 4.6's behaviour-camera sidecar is a frozen interface that specifies
blake3, so hashing the same pipeline's data two ways would be the worse choice.

The generator writes real markers now, because every later task in this phase
uses generated sessions as its fixture and a contract with no producer cannot
be tested.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Session-complete detection and the stalled-transfer alarm

Spec §4. The `DONE` primitive has existed since 1c-1 and **nothing has ever read one back** — this is its first reader.

**Files:**
- Create: `wl_preproc/ingest/__init__.py` (empty, `__all__: list[str] = []`)
- Create: `wl_preproc/ingest/sentinel.py`
- Create: `tests/ingest/test_sentinel.py`

**Interfaces:**
- Consumes: `SessionLayout`, `SessionManifest`, `DoneMarker` (Task 1).
- Produces:
  - `class MarkerState(StrEnum)` — `ABSENT`, `EMPTY`, `PARSED`, `INVALID`
  - `read_marker(layout, system) -> tuple[MarkerState, DoneMarker | None]`
  - `session_complete(layout, manifest) -> bool`
  - `missing_systems(layout, manifest) -> list[str]`
  - `last_change_at(session_dir) -> datetime` — newest mtime anywhere in the tree, tz-aware UTC
  - `is_stalled(layout, manifest, now, stall_after_s=STALL_AFTER_S) -> bool`
  - `STALL_AFTER_S: int = 7200`

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/test_sentinel.py`:

```python
"""Session-complete detection: every system the manifest declared has a DONE.

The alarm half matters as much as the trigger half. A rig transfer that dies at
80% leaves a directory indistinguishable from one still working, and without
`is_stalled` a weekend's recording simply never appears with nothing saying so.
"""

from __future__ import annotations

import datetime

import pytest

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.sentinel import (
    MarkerState,
    is_stalled,
    missing_systems,
    read_marker,
    session_complete,
)
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


@pytest.fixture
def session(tmp_path):
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, CI_RECIPE.session_id)
    manifest = SessionManifest.from_yaml(layout.manifest_path.read_text())
    return layout, manifest


def test_a_generated_session_is_complete(session):
    layout, manifest = session
    assert session_complete(layout, manifest) is True
    assert missing_systems(layout, manifest) == []


def test_a_missing_marker_makes_it_incomplete(session):
    layout, manifest = session
    layout.done_marker("spikeglx").unlink()

    assert session_complete(layout, manifest) is False
    assert missing_systems(layout, manifest) == ["spikeglx"]


def test_an_empty_marker_still_counts_as_complete(session):
    """Spec section 5.2: an empty DONE means "complete, no integrity data".
    Completeness and integrity are separate questions and this is where they
    separate."""
    layout, manifest = session
    layout.done_marker("spikeglx").write_text("")

    assert read_marker(layout, "spikeglx")[0] is MarkerState.EMPTY
    assert session_complete(layout, manifest) is True


def test_an_undeclared_system_never_affects_completeness(session):
    """A system on disk that the manifest never promised cannot hold up a
    session — nothing was waiting for it."""
    layout, manifest = session
    rhs_dir = layout.system_dir("rhs")
    rhs_dir.mkdir()
    (rhs_dir / "stray.dat").write_bytes(b"0")

    assert "rhs" not in manifest.expected_systems
    assert session_complete(layout, manifest) is True


def test_a_corrupt_marker_is_invalid_rather_than_absent(session):
    """These must not collapse: absent means the transfer has not finished,
    invalid means it finished and wrote something wrong. They are different
    problems with different reports."""
    layout, manifest = session
    layout.done_marker("spikeglx").write_text("{{{ not yaml")

    assert read_marker(layout, "spikeglx")[0] is MarkerState.INVALID
    assert session_complete(layout, manifest) is False


def test_stalled_only_when_incomplete_and_quiet(session):
    layout, manifest = session
    layout.done_marker("spikeglx").unlink()
    quiet_since = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=3)

    assert is_stalled(layout, manifest, now=quiet_since) is True


def test_a_complete_session_is_never_stalled_however_old(session):
    layout, manifest = session
    ancient = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=400)

    assert is_stalled(layout, manifest, now=ancient) is False


def test_a_recently_touched_incomplete_session_is_not_stalled(session):
    """Still transferring is not stalled. The threshold is what separates
    them, and getting it wrong must only ever change a report."""
    layout, manifest = session
    layout.done_marker("spikeglx").unlink()
    just_now = datetime.datetime.now(datetime.UTC)

    assert is_stalled(layout, manifest, now=just_now) is False
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/ingest/test_sentinel.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.ingest'`

- [ ] **Step 3: Write the implementation**

Create `wl_preproc/ingest/__init__.py` containing only `__all__: list[str] = []`, then `wl_preproc/ingest/sentinel.py`:

```python
"""Is this session finished landing, and if not, has it given up?

Spec section 4. `contracts/paths.py` has described the aggregate since 1c-1 —
"Session-complete detection waits for every expected system's marker" — and
nothing had ever read a marker back. This is that reader.

Quiescence was rejected as a *trigger* because a stalled transfer and a
finished one are both quiet, and no threshold distinguishes them. It survives
as an *alarm*, where being wrong changes a report and never an ingest.
"""

from __future__ import annotations

import datetime
from enum import StrEnum

from wl_preproc.contracts.done import DoneMarker
from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SessionLayout

STALL_AFTER_S = 7200  # 2 h. A reporting threshold; see spec section 14.


class MarkerState(StrEnum):
    ABSENT = "absent"
    EMPTY = "empty"
    PARSED = "parsed"
    INVALID = "invalid"


def read_marker(layout: SessionLayout, system: str) -> tuple[MarkerState, DoneMarker | None]:
    """Read one system's DONE marker.

    ABSENT and INVALID are deliberately distinct: the first means the transfer
    has not finished, the second means it finished and wrote something wrong.
    Collapsing them would report a broken producer as a slow one forever.
    """
    path = layout.done_marker(system)
    if not path.exists():
        return MarkerState.ABSENT, None
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return MarkerState.EMPTY, None
    try:
        return MarkerState.PARSED, DoneMarker.from_yaml(text)
    except Exception:
        return MarkerState.INVALID, None


def missing_systems(layout: SessionLayout, manifest: SessionManifest) -> list[str]:
    """Declared systems without a usable marker, in `expected_systems` order."""
    return [
        system
        for system in manifest.expected_systems
        if read_marker(layout, system)[0] in (MarkerState.ABSENT, MarkerState.INVALID)
    ]


def session_complete(layout: SessionLayout, manifest: SessionManifest) -> bool:
    """Every system the manifest declared has a marker. Nothing else is consulted.

    A system present on disk but never declared cannot hold this up: nothing was
    waiting for it.
    """
    return not missing_systems(layout, manifest)


def last_change_at(session_dir) -> datetime.datetime:
    """Newest mtime anywhere in the tree, as tz-aware UTC.

    The directory's own mtime is included, so a session whose only activity was
    creating an empty subdirectory still counts as recently touched.
    """
    newest = session_dir.stat().st_mtime
    for candidate in session_dir.rglob("*"):
        newest = max(newest, candidate.stat().st_mtime)
    return datetime.datetime.fromtimestamp(newest, tz=datetime.UTC)


def is_stalled(
    layout: SessionLayout,
    manifest: SessionManifest,
    now: datetime.datetime,
    stall_after_s: int = STALL_AFTER_S,
) -> bool:
    """Incomplete, and quiet for long enough that it is not merely slow.

    `now` is injected rather than read from the clock so the tests state the
    elapsed time they mean instead of sleeping.
    """
    if session_complete(layout, manifest):
        return False
    return (now - last_change_at(layout.dir)).total_seconds() >= stall_after_s
```

- [ ] **Step 4: Run the test again**

Run: `.venv/bin/python -m pytest tests/ingest/test_sentinel.py -q`
Expected: PASS, 8 tests.

- [ ] **Step 5: Prove the stall test is not vacuous**

Temporarily change `is_stalled`'s final line to `return True`.
Run: `.venv/bin/python -m pytest tests/ingest/test_sentinel.py -q`
Expected: `test_a_recently_touched_incomplete_session_is_not_stalled` FAILS.
Then restore the line and re-run — 8 pass. **Four tests on the previous phase passed while proving nothing; do not skip this step.**

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(ingest): read the DONE markers nothing has ever read

contracts/paths.py has described this aggregate since 1c-1 — "session-complete
detection waits for every expected system's marker" — and no code anywhere had
ever read one back. This is that reader, and it implements exactly the rule the
docstring already stated.

Quiescence survives as an alarm rather than a trigger. As a trigger it cannot
work: a stalled transfer and a finished one are both quiet, and no threshold
separates them. As an alarm it catches the failure every other option leaves
silent — a transfer that dies at 80% otherwise produces a directory that looks
exactly like one still working.

ABSENT and INVALID are kept distinct. The first means the transfer has not
finished; the second means it finished and wrote something wrong. Collapsing
them would report a broken producer as a slow one forever.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Topology discovery

Spec §7. This is where the pipeline declines to render a verdict.

**Files:**
- Create: `wl_preproc/ingest/discover.py`
- Create: `tests/ingest/test_discover.py`

**Interfaces:**
- Consumes: `SessionLayout`, `SessionManifest`, `sentinel.read_marker`, `MarkerState`.
- Produces:
  - `class SystemState(StrEnum)` — `PRESENT`, `ABSENT`, `UNDECLARED`, `PENDING`
  - `discover_topology(layout, manifest) -> dict[str, SystemState]` — one entry per member of `SYSTEMS`
  - `systems_with_data(topology) -> list[str]` — `PRESENT` and `UNDECLARED`, sorted

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/test_discover.py`:

```python
"""What is on disk versus what the manifest declared.

Spec section 7: only PENDING blocks ingest, and it blocks by meaning "not
complete yet" rather than by judgment. ABSENT never blocks — a session with no
eye tracker ingests exactly like one that has it, and the absence surfaces later
as coverage rather than as a refusal here.
"""

from __future__ import annotations

import pytest

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SYSTEMS, SessionLayout
from wl_preproc.ingest.discover import SystemState, discover_topology, systems_with_data
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


@pytest.fixture
def session(tmp_path):
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, CI_RECIPE.session_id)
    manifest = SessionManifest.from_yaml(layout.manifest_path.read_text())
    return layout, manifest


def test_every_known_system_gets_exactly_one_state(session):
    layout, manifest = session
    topology = discover_topology(layout, manifest)

    assert set(topology) == set(SYSTEMS)


def test_a_generated_session_is_all_present_or_absent(session):
    layout, manifest = session
    topology = discover_topology(layout, manifest)

    for system in CI_RECIPE.systems:
        assert topology[system] is SystemState.PRESENT
    for system in set(SYSTEMS) - set(CI_RECIPE.systems):
        assert topology[system] is SystemState.ABSENT


def test_declared_without_a_marker_is_pending(session):
    layout, manifest = session
    layout.done_marker("spikeglx").unlink()

    assert discover_topology(layout, manifest)["spikeglx"] is SystemState.PENDING


def test_data_on_disk_that_was_never_declared_is_undeclared(session):
    layout, manifest = session
    rhs_dir = layout.system_dir("rhs")
    rhs_dir.mkdir()
    (rhs_dir / "amplifier.dat").write_bytes(b"0" * 16)

    assert discover_topology(layout, manifest)["rhs"] is SystemState.UNDECLARED


def test_an_empty_undeclared_directory_is_absent_not_undeclared(session):
    """An empty directory is not a recording. Treating it as one would create
    an AcquisitionSystem row for a device that produced nothing."""
    layout, manifest = session
    layout.system_dir("rhs").mkdir()

    assert discover_topology(layout, manifest)["rhs"] is SystemState.ABSENT


def test_systems_with_data_includes_undeclared(session):
    """Spec section 8.1: undeclared data is real. Hiding a recording from every
    downstream stage to punish a manifest bug is the wrong trade."""
    layout, manifest = session
    rhs_dir = layout.system_dir("rhs")
    rhs_dir.mkdir()
    (rhs_dir / "amplifier.dat").write_bytes(b"0" * 16)

    assert systems_with_data(discover_topology(layout, manifest)) == sorted(
        [*CI_RECIPE.systems, "rhs"]
    )
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/ingest/test_discover.py -q`
Expected: FAIL — no module `wl_preproc.ingest.discover`.

- [ ] **Step 3: Write the implementation**

Create `wl_preproc/ingest/discover.py`:

```python
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
```

- [ ] **Step 4: Run the test again**

Run: `.venv/bin/python -m pytest tests/ingest/test_discover.py -q`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(ingest): topology discovery that classifies and never judges

Four states, one per known system. Only PENDING holds up an ingest, and it does
so by meaning "not finished landing" rather than by any judgment about whether
the session is scientifically complete — a session with no eye tracker ingests
exactly like one that has it, and the absence surfaces later as coverage.

UNDECLARED data still earns an AcquisitionSystem row. Hiding a real recording
from every downstream stage in order to punish a manifest bug would destroy the
more valuable thing to protect the less.

An empty directory reads as ABSENT rather than UNDECLARED, so a device that
produced nothing does not acquire a row claiming it recorded.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Transfer integrity verification

Spec §5.3–5.4. The one place the watcher renders a verdict, and it is about transfer rather than science.

**Files:**
- Create: `wl_preproc/ingest/verify.py`
- Create: `tests/ingest/test_verify.py`

**Interfaces:**
- Consumes: `SessionLayout`, `SessionManifest`, `blake3_file`, `read_marker`, `MarkerState`, `discover_topology`, `SystemState`.
- Produces:
  - `class Integrity(StrEnum)` — `VERIFIED`, `DECLARED_ONLY`, `SKIPPED`
  - `class Mismatch(NamedTuple)` — `system: str`, `path: str`, `problem: str`
  - `verify_session(layout, manifest, enabled=True) -> tuple[Integrity, list[Mismatch]]`

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/test_verify.py`:

```python
"""Does what arrived match what was sent?

rsync verifies in flight, not at rest. A file that transferred correctly and
then met a bad block on the scratch array passes every check the transfer tool
makes, and ingest is the last moment where catching that is cheap.
"""

from __future__ import annotations

import pytest

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.verify import Integrity, verify_session
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


@pytest.fixture
def session(tmp_path):
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, CI_RECIPE.session_id)
    manifest = SessionManifest.from_yaml(layout.manifest_path.read_text())
    return layout, manifest


def test_an_untouched_session_verifies(session):
    layout, manifest = session
    integrity, mismatches = verify_session(layout, manifest)

    assert integrity is Integrity.VERIFIED
    assert mismatches == []


def test_a_truncated_file_is_caught(session):
    """The pathology the generator already injects, exercised end to end."""
    layout, manifest = session
    target = layout.system_dir("spikeglx") / f"{CI_RECIPE.session_id}_imec0.ap.bin"
    original = target.read_bytes()
    target.write_bytes(original[: len(original) // 2])

    integrity, mismatches = verify_session(layout, manifest)

    assert mismatches
    assert {m.problem for m in mismatches} == {"size"}
    assert mismatches[0].system == "spikeglx"


def test_a_same_size_corruption_is_caught_by_the_hash(session):
    """Size alone would pass this. It is the case that justifies hashing at all
    rather than comparing sizes and calling it verified."""
    layout, manifest = session
    target = layout.system_dir("spikeglx") / f"{CI_RECIPE.session_id}_imec0.ap.meta"
    payload = bytearray(target.read_bytes())
    payload[0] ^= 0xFF
    target.write_bytes(bytes(payload))

    _, mismatches = verify_session(layout, manifest)

    assert [m.problem for m in mismatches] == ["blake3"]


def test_a_file_declared_but_absent_is_caught(session):
    layout, manifest = session
    (layout.system_dir("spikeglx") / f"{CI_RECIPE.session_id}_imec0.ap.meta").unlink()

    _, mismatches = verify_session(layout, manifest)

    assert [m.problem for m in mismatches] == ["missing"]


def test_an_empty_marker_yields_declared_only_rather_than_verified(session):
    """Spec section 5.2. The record must never claim a check that did not run."""
    layout, manifest = session
    for system in CI_RECIPE.systems:
        layout.done_marker(system).write_text("")

    integrity, mismatches = verify_session(layout, manifest)

    assert integrity is Integrity.DECLARED_ONLY
    assert mismatches == []


def test_disabling_verification_says_skipped_not_verified(session):
    layout, manifest = session
    integrity, mismatches = verify_session(layout, manifest, enabled=False)

    assert integrity is Integrity.SKIPPED
    assert mismatches == []


def test_one_empty_marker_among_several_downgrades_the_whole_session(session):
    """A session is verified only if everything in it was. Reporting VERIFIED
    when one system carried no integrity data would be the record claiming more
    than was checked."""
    layout, manifest = session
    layout.done_marker("bcam").write_text("")

    integrity, _ = verify_session(layout, manifest)

    assert integrity is Integrity.DECLARED_ONLY
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/ingest/test_verify.py -q`
Expected: FAIL — no module `wl_preproc.ingest.verify`.

- [ ] **Step 3: Write the implementation**

Create `wl_preproc/ingest/verify.py`:

```python
"""Re-hash what landed and compare it against what the transfer declared.

Spec section 5. This is the one place the watcher renders a verdict, and the
verdict is about *transfer* rather than about science — which is exactly the
distinction discover.py depends on to never refuse a session for what it lacks.
"""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

from wl_preproc.contracts.done import blake3_file
from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.discover import SystemState, discover_topology
from wl_preproc.ingest.sentinel import MarkerState, read_marker


class Integrity(StrEnum):
    VERIFIED = "verified"
    DECLARED_ONLY = "declared_only"
    SKIPPED = "skipped"


class Mismatch(NamedTuple):
    system: str
    path: str
    problem: str  # "missing" | "size" | "blake3"


def verify_session(
    layout: SessionLayout,
    manifest: SessionManifest,
    enabled: bool = True,
) -> tuple[Integrity, list[Mismatch]]:
    """Verify every system that carries integrity data.

    Returns DECLARED_ONLY if *any* system's marker was empty, because a session
    is verified only if everything in it was — reporting VERIFIED when one
    system carried no data would be the record claiming more than was checked.
    """
    if not enabled:
        return Integrity.SKIPPED, []

    mismatches: list[Mismatch] = []
    saw_empty = False

    topology = discover_topology(layout, manifest)
    for system, state in sorted(topology.items()):
        if state not in (SystemState.PRESENT, SystemState.UNDECLARED):
            continue

        marker_state, marker = read_marker(layout, system)
        if marker_state is not MarkerState.PARSED or marker is None:
            saw_empty = True
            continue

        system_dir = layout.system_dir(system)
        for entry in marker.files:
            candidate = system_dir / entry.path
            if not candidate.is_file():
                mismatches.append(Mismatch(system, entry.path, "missing"))
            elif candidate.stat().st_size != entry.bytes:
                # Checked before hashing: a size mismatch is decisive and
                # cheap, and re-reading a truncated 384 GB file to reach the
                # same conclusion by digest would cost minutes to learn nothing.
                mismatches.append(Mismatch(system, entry.path, "size"))
            elif blake3_file(candidate) != entry.blake3:
                mismatches.append(Mismatch(system, entry.path, "blake3"))

    integrity = Integrity.DECLARED_ONLY if saw_empty else Integrity.VERIFIED
    return integrity, mismatches
```

- [ ] **Step 4: Run the test again**

Run: `.venv/bin/python -m pytest tests/ingest/test_verify.py -q`
Expected: PASS, 7 tests.

- [ ] **Step 5: Prove the hash check is load-bearing**

Temporarily change the `elif blake3_file(...)` branch to `elif False:`.
Run: `.venv/bin/python -m pytest tests/ingest/test_verify.py -q`
Expected: `test_a_same_size_corruption_is_caught_by_the_hash` FAILS. Restore and re-run.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(ingest): verify at rest what the transfer verified in flight

rsync checks bytes as they move; it cannot check a file that landed correctly
and then met a bad block on the scratch array. Re-hashing at the destination is
the only step that catches that, and ingest is the last moment where catching
it is cheap — 3.3 minutes for a 360 GB session, measured.

Size is compared before the digest, because a size mismatch is decisive and
free while re-reading a truncated 384 GB file to reach the same conclusion by
hash would cost minutes to learn nothing.

A session is VERIFIED only if every system in it carried integrity data. One
empty marker downgrades the whole session to DECLARED_ONLY rather than letting
the record claim more than was actually checked.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: The ingest schema — `Ingestion` and `Quarantine`

Spec §8.2, §9. Two tables in a new schema module, following `coverage.py`'s shape exactly.

**Files:**
- Create: `wl_preproc/schema/ingest.py`
- Create: `tests/schema/test_ingest.py`

**Interfaces:**
- Consumes: `DEFAULT_PREFIX`, `wl_preproc.schema.pipeline`.
- Produces: `Ingestion`, `Quarantine`, `activate(prefix=DEFAULT_PREFIX)`, `QUARANTINE_REASONS: frozenset[str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/schema/test_ingest.py`:

```python
"""Ingestion and Quarantine.

Quarantine is keyed by directory path, not by (subject, session_datetime),
because the worst failure is an unparseable manifest and the manifest is what
yields that key. A key-addressed quarantine row cannot represent the failures
that most need recording, which is the whole reason the table exists.
"""

from __future__ import annotations

import datetime

import datajoint as dj
import pytest

from wl_preproc.schema import ingest


@pytest.fixture(scope="module")
def activated(dj_conn, prefix):
    ingest.activate(prefix=prefix)
    return ingest


def test_quarantine_is_keyed_by_path_not_by_session(activated):
    assert activated.Quarantine.primary_key == ["session_dir"]


def test_quarantine_records_a_failure_with_no_derivable_session_key(activated):
    """The case the table exists for: a manifest so broken that neither the
    subject nor the datetime can be read out of it."""
    activated.Quarantine.insert1(
        {
            "session_dir": "/scratch/2027-03-14_01",
            "failed_at": datetime.datetime(2027, 3, 14, 20, 0, 0),
            "reason": "manifest_invalid",
            "detail": {"error": "while parsing a block mapping"},
            "subject": None,
            "session_dt": None,
        }
    )
    row = (activated.Quarantine & {"session_dir": "/scratch/2027-03-14_01"}).fetch1()

    assert row["subject"] is None
    assert row["detail"] == {"error": "while parsing a block mapping"}


def test_every_reason_the_code_uses_is_declared(activated, enum_values):
    declared = enum_values(activated.Quarantine.heading.attributes["reason"].type)

    assert declared == set(ingest.QUARANTINE_REASONS)


def test_integrity_states_match_the_verifier(activated, enum_values):
    """The enum and the StrEnum must not drift apart — a state the verifier can
    return but the column cannot store is an insert that fails in production
    and nowhere else."""
    from wl_preproc.ingest.verify import Integrity

    declared = enum_values(activated.Ingestion.heading.attributes["integrity"].type)

    assert declared == {member.value for member in Integrity}


def test_ingestion_hangs_off_session(activated):
    assert activated.Ingestion.primary_key == ["subject", "session_datetime"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/schema/test_ingest.py -q`
Expected: FAIL — no module `wl_preproc.schema.ingest`.

- [ ] **Step 3: Write the implementation**

Create `wl_preproc/schema/ingest.py`:

```python
# wl_preproc/schema/ingest.py
"""What was ingested, and what refused to be.

`Session` records when the recording *happened*; nothing recorded when it was
*ingested*, and the daily report's first line is "ingested in the last 24 h".
Deriving that from `session_datetime` answers a different question, and answers
it wrongly for any backfill.

`Quarantine` is keyed on the session **directory path** rather than on
(subject, session_datetime). The worst failure this pipeline has is an
unparseable manifest, and the manifest is precisely what yields that key — so a
key-addressed row cannot represent the failures that most need recording. The
path is available in every case, because the watcher is standing in it.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.schema import DEFAULT_PREFIX, pipeline

schema = dj.Schema()

QUARANTINE_REASONS: frozenset[str] = frozenset(
    {
        "manifest_invalid",
        "manifest_schema_version",
        "session_id_mismatch",
        "checksum_mismatch",
        "params_invalid",
        # element-animal declares `subject : varchar(8)`. The manifest's
        # `subject` is an unconstrained `str`, so a longer name validates
        # cleanly and then fails at the insert. Caught as a manifest problem
        # rather than surfacing as a MySQL error mid-landing.
        "subject_unrepresentable",
    }
)

_REASON_ENUM = "enum(" + ",".join(f"'{r}'" for r in sorted(QUARANTINE_REASONS)) + ")"


@schema
class Ingestion(dj.Manual):
    definition = """
    # One row per session successfully landed. Key: (subject, session_datetime).
    -> pipeline.Session
    ---
    ingested_at   : datetime      # when this row was written, not when the session ran
    session_dir   : varchar(255)  # where it came from; provenance, never a key
    integrity     : enum('verified','declared_only','skipped')
    topology      : <blob>        # the full per-system state map, read as a unit
    manifest_hash : varchar(64)   # blake3 of the manifest FILE'S BYTES
    """


@schema
class Quarantine(dj.Manual):
    definition = f"""
    # A session directory that failed validation. Key: session_dir.
    # NOT keyed on (subject, session_datetime) — see this module's docstring.
    session_dir  : varchar(255)
    ---
    failed_at    : datetime
    reason       : {_REASON_ENUM}
    detail       : <blob>
    subject=null    : varchar(32)   # best effort; may be unparseable
    session_dt=null : datetime      # best effort; may be unparseable
    """


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}ingest`. Idempotent."""
    pipeline.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}ingest", create_tables=True)
```

- [ ] **Step 4: Run the test and the guardrails**

Run: `.venv/bin/python -m pytest tests/schema/test_ingest.py tests/schema/test_guardrails.py -q`
Expected: PASS. The guardrail sweep must accept both `<blob>` declarations — if it reports a bare `longblob`, the `<blob>` codec is missing from a definition.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(schema): Ingestion and Quarantine

Ingestion exists because Session records when the recording happened and
nothing recorded when it was ingested, while the daily report's first line is
"ingested in the last 24 h". Deriving that from session_datetime answers a
different question and answers it wrongly for any backfill.

Quarantine is keyed on the session directory path rather than on
(subject, session_datetime), and that is the whole design. The worst failure
here is an unparseable manifest — and the manifest is exactly what yields the
session key, so a key-addressed row cannot represent the failures that most
need recording. The path is available in every case, because the watcher is
standing in it. Subject and datetime are recorded when they parse, nullable,
and nothing may key on them.

A test pins the integrity enum against the verifier's StrEnum, so a state the
code can return but the column cannot store fails here rather than in
production.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Landing — the only module that writes rows

Spec §8. Every write is idempotent by construction, because there is no lock and `daemon.py` already documents that gap.

**Files:**
- Create: `wl_preproc/ingest/landing.py`
- Create: `tests/ingest/test_landing.py`

**Interfaces:**
- Consumes: `SessionManifest`, `SystemState`, `Integrity`, `schema.ingest`, `schema.pipeline`, `schema.core`.
- Produces:
  - `land_session(layout, manifest, topology, integrity, manifest_hash, prefix=DEFAULT_PREFIX, now=None) -> dict` — returns the `Session` key
  - `quarantine(session_dir, reason, detail, subject=None, session_dt=None, prefix=DEFAULT_PREFIX, now=None) -> None`
  - `already_ingested(session_key, prefix=DEFAULT_PREFIX) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/test_landing.py`:

```python
"""The schema writes, and the idempotence that stands in for a lock.

There is no lock. `daemon.py` says so in its own docstring — "nothing here
enforces the single-runner invariant ... no lock file, no advisory lock" — and
a watcher invoked from cron inherits that exactly. So every write here is
idempotent by construction, and two watchers racing one directory produce the
same rows in either order.
"""

from __future__ import annotations

import datetime

import pytest

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.discover import discover_topology
from wl_preproc.ingest.landing import already_ingested, land_session, quarantine
from wl_preproc.ingest.verify import Integrity
from wl_preproc.schema import core, ingest, pipeline
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


@pytest.fixture(scope="module")
def activated(dj_conn, prefix):
    ingest.activate(prefix=prefix)
    return prefix


@pytest.fixture
def landed(tmp_path, activated):
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, CI_RECIPE.session_id)
    manifest = SessionManifest.from_yaml(layout.manifest_path.read_text())
    topology = discover_topology(layout, manifest)
    key = land_session(
        layout, manifest, topology, Integrity.VERIFIED, "abc123", prefix=activated
    )
    return key, layout, manifest, topology, activated


def test_it_creates_the_whole_ancestor_chain(landed):
    key, *_ = landed

    assert len(pipeline.Subject & {"subject": key["subject"]}) == 1
    assert len(pipeline.Session & key) == 1
    assert len(ingest.Ingestion & key) == 1


def test_one_acquisition_system_row_per_system_with_data(landed):
    key, _, _, topology, _ = landed
    rows = (core.AcquisitionSystem & key).fetch("system")

    assert set(rows) == set(CI_RECIPE.systems)


def test_landing_twice_changes_nothing(landed):
    """The property that stands in for a lock."""
    key, layout, manifest, topology, prefix = landed
    before = len(ingest.Ingestion & key), len(core.AcquisitionSystem & key)

    land_session(layout, manifest, topology, Integrity.VERIFIED, "abc123", prefix=prefix)

    assert (len(ingest.Ingestion & key), len(core.AcquisitionSystem & key)) == before


def test_already_ingested_is_false_before_and_true_after(tmp_path, activated):
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, CI_RECIPE.session_id)
    manifest = SessionManifest.from_yaml(layout.manifest_path.read_text())
    key = {"subject": manifest.subject, "session_datetime": manifest.started_at}

    assert already_ingested(key, prefix=activated) is False

    land_session(
        layout,
        manifest,
        discover_topology(layout, manifest),
        Integrity.VERIFIED,
        "abc123",
        prefix=activated,
    )

    assert already_ingested(key, prefix=activated) is True


def test_the_topology_blob_round_trips_as_a_dict(landed):
    """The blob audit's whole point: a dict must come back a dict. Under a bare
    longblob this would return the string repr of one, silently."""
    key, _, _, topology, _ = landed
    stored = (ingest.Ingestion & key).fetch1("topology")

    assert stored == {system: str(state) for system, state in topology.items()}


def test_quarantine_records_a_directory_with_no_session_key(activated):
    quarantine(
        "/scratch/2027-03-14_99",
        reason="manifest_invalid",
        detail={"error": "truncated"},
        prefix=activated,
    )
    row = (ingest.Quarantine & {"session_dir": "/scratch/2027-03-14_99"}).fetch1()

    assert row["reason"] == "manifest_invalid"
    assert row["subject"] is None


def test_quarantining_twice_updates_rather_than_raising(activated):
    """A directory that fails, is half-fixed, and fails differently must end up
    describing the latest failure — not raise a duplicate-key error that stops
    the whole scan."""
    quarantine("/scratch/2027-03-14_98", reason="manifest_invalid", detail={}, prefix=activated)
    quarantine(
        "/scratch/2027-03-14_98", reason="checksum_mismatch", detail={}, prefix=activated
    )
    row = (ingest.Quarantine & {"session_dir": "/scratch/2027-03-14_98"}).fetch1()

    assert row["reason"] == "checksum_mismatch"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/ingest/test_landing.py -q`
Expected: FAIL — no module `wl_preproc.ingest.landing`.

- [ ] **Step 3: Write the implementation**

Create `wl_preproc/ingest/landing.py`:

```python
"""The only module in `ingest` that touches DataJoint.

Every write is idempotent by construction rather than guarded by a lock.
`daemon.py` already records that no lock exists — "nothing here enforces the
single-runner invariant ... no lock file, no advisory lock" — and a watcher run
from cron inherits that. Adding a lock would need crash cleanup, which is the
same stale-reservation problem `reap_stale_jobs` exists for; solving it twice
differently is worse than solving it once.

This is also wl.works' most-repeated defect lesson applied here: check-then-write
is "the single largest source of real defects", and the answer is an
unconditional idempotent write rather than a read followed by a conditional one.
"""

from __future__ import annotations

import datetime

import datajoint as dj

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.discover import SystemState, systems_with_data
from wl_preproc.ingest.verify import Integrity
from wl_preproc.schema import DEFAULT_PREFIX, core, ingest, pipeline

# element-animal requires a birth date and does not allow null. This machine
# cannot know one — it is wl.works' authored record — so an obviously-sentinel
# value is used rather than a plausible-looking guess. A date nobody could
# mistake for real is safer than one somebody might.
SUBJECT_BIRTH_DATE_UNKNOWN = datetime.date(1900, 1, 1)

# element-animal declares `subject : varchar(8)`.
SUBJECT_MAX_LEN = 8


def _now(now: datetime.datetime | None) -> datetime.datetime:
    return now or datetime.datetime.now(datetime.UTC)


def already_ingested(session_key: dict, prefix: str = DEFAULT_PREFIX) -> bool:
    """An Ingestion row is what marks a session done."""
    ingest.activate(prefix=prefix)
    return len(ingest.Ingestion & session_key) > 0


def land_session(
    layout: SessionLayout,
    manifest: SessionManifest,
    topology: dict[str, SystemState],
    integrity: Integrity,
    manifest_hash: str,
    prefix: str = DEFAULT_PREFIX,
    now: datetime.datetime | None = None,
) -> dict:
    """Write Subject, Session, AcquisitionSystem and Ingestion. Returns the Session key.

    Not wrapped in one transaction: each insert is independently idempotent, so
    a partial run followed by a re-run converges on the same rows. A transaction
    would add rollback semantics that buy nothing here and would forbid this
    being called from inside another one.
    """
    ingest.activate(prefix=prefix)

    session_key = {"subject": manifest.subject, "session_datetime": manifest.started_at}

    # element-animal's Subject, verified against the installed package:
    #   subject : varchar(8)          <- 8 characters, and the manifest's is unbounded
    #   sex : enum('M','F','U')       <- required, no default
    #   subject_birth_date : date     <- required, NO DEFAULT and NOT nullable
    #
    # The birth date is an authored record that lives in wl.works' `animal`
    # table, and this machine can never read it (section 11.1). It cannot be
    # omitted either. So a stub is written with a sentinel date and a
    # description that says so in words — recording that the date is unknown,
    # rather than asserting a false one. The authoritative record stays
    # wl.works'; this row exists because Session needs a parent.
    pipeline.Subject.insert1(
        {
            "subject": manifest.subject,
            "subject_nickname": manifest.subject,
            "sex": "U",
            "subject_birth_date": SUBJECT_BIRTH_DATE_UNKNOWN,
            "subject_description": (
                "stub created at ingest; sex and birth date unknown here. "
                "The authoritative animal record is wl.works' `animal` table."
            ),
        },
        skip_duplicates=True,
    )
    pipeline.Session.insert1(session_key, skip_duplicates=True)

    core.AcquisitionSystem.insert(
        [{**session_key, "system": system} for system in systems_with_data(topology)],
        skip_duplicates=True,
    )

    ingest.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": _now(now),
            "session_dir": str(layout.dir),
            "integrity": str(integrity),
            "topology": {system: str(state) for system, state in topology.items()},
            "manifest_hash": manifest_hash,
        },
        skip_duplicates=True,
    )

    return session_key


def quarantine(
    session_dir: str,
    reason: str,
    detail: dict,
    subject: str | None = None,
    session_dt: datetime.datetime | None = None,
    prefix: str = DEFAULT_PREFIX,
    now: datetime.datetime | None = None,
) -> None:
    """Record a directory that failed validation.

    `replace=True` rather than `skip_duplicates`: a directory that fails, is
    half-fixed, and fails differently must end up describing its *latest*
    failure. Skipping would leave a stale reason on the record, and raising
    would abort the whole scan over one bad session.
    """
    ingest.activate(prefix=prefix)
    ingest.Quarantine.insert1(
        {
            "session_dir": session_dir,
            "failed_at": _now(now),
            "reason": reason,
            "detail": detail,
            "subject": subject,
            "session_dt": session_dt,
        },
        replace=True,
    )
```

> **Note for the implementer:** `pipeline.Subject`'s exact attribute names come from
> `element-animal`. Confirm them before writing the insert — run
> `.venv/bin/python -c "from wl_preproc.schema import pipeline; pipeline.activate('t_'); print(pipeline.Subject.heading)"`
> against a live container and match what it prints. If a required attribute is not
> listed above, add it with a defensible default and say so in the commit message.
> Do **not** guess and leave it: a wrong attribute name here fails at the first real
> ingest, and nothing before that point exercises it.

- [ ] **Step 4: Run the test**

Run: `.venv/bin/python -m pytest tests/ingest/test_landing.py -q`
Expected: PASS, 7 tests.

- [ ] **Step 5: Prove the idempotence test is real**

Temporarily change `ingest.Ingestion.insert1(...)`'s `skip_duplicates=True` to `replace=True`, and add a second `land_session` call with a different `manifest_hash` in a scratch check — confirm `test_landing_twice_changes_nothing` still passes (row count is what it asserts) but that the row's content changed. Then restore. This documents that the test asserts *count*, not *content*, so a later reviewer knows its exact reach.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(ingest): land the rows, idempotently, without a lock

There is no lock and daemon.py already says so — "nothing here enforces the
single-runner invariant ... no lock file, no advisory lock" — and a watcher run
from cron inherits that exactly. Rather than add one, every write here is
idempotent by construction: two watchers racing the same directory produce the
same rows in either order. A lock would need crash cleanup, which is the stale
reservation problem reap_stale_jobs already exists for, and solving that twice
differently is worse than solving it once.

Quarantine inserts with replace rather than skip_duplicates, so a directory
that fails, is half fixed, and fails differently ends up describing its latest
failure instead of keeping a stale reason — and so one bad session cannot abort
a whole scan with a duplicate-key error.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `session_params.yaml` registration

Spec §10. Registration only — no request row, for §2's reason.

**Files:**
- Create: `wl_preproc/ingest/params.py`
- Create: `tests/ingest/test_params.py`

**Interfaces:**
- Consumes: `wl_preproc.schema.paramset.register(paramset_type: str, params: dict) -> int`.
- Produces:
  - `PARAMS_FILENAME: str = "session_params.yaml"`
  - `class SessionParams(BaseModel)` — `extra="forbid"`; `paramset_type: str`, `params: dict`
  - `register_session_params(layout, prefix=DEFAULT_PREFIX) -> int | None` — `None` when no file is present; raises `ValueError` on an invalid one

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/test_params.py`:

```python
"""Parameters that travelled with the raw data.

Spec section 5.4's requirement is that unknown keys are REJECTED — "so `nblocks`
vs `n_blocks` fails loudly rather than silently defaulting". Silently defaulting
is the failure this whole file exists to prevent.
"""

from __future__ import annotations

import pytest

from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.params import PARAMS_FILENAME, register_session_params
from wl_preproc.schema import paramset
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


@pytest.fixture
def layout(tmp_path, dj_conn, prefix):
    paramset.activate(prefix=prefix)
    generate_session(tmp_path, CI_RECIPE)
    return SessionLayout(tmp_path, CI_RECIPE.session_id), prefix


def test_no_params_file_is_not_an_error(layout):
    """Most sessions carry none, and lab defaults are the ordinary case."""
    session_layout, prefix = layout

    assert register_session_params(session_layout, prefix=prefix) is None


def test_a_valid_file_registers_and_returns_an_id(layout):
    session_layout, prefix = layout
    (session_layout.dir / PARAMS_FILENAME).write_text(
        "paramset_type: clustering\nparams:\n  n_blocks: 4\n"
    )

    paramset_id = register_session_params(session_layout, prefix=prefix)

    assert isinstance(paramset_id, int)


def test_an_unknown_top_level_key_is_rejected(layout):
    session_layout, prefix = layout
    (session_layout.dir / PARAMS_FILENAME).write_text(
        "paramset_type: clustering\nparams:\n  n_blocks: 4\nnblocks: 4\n"
    )

    with pytest.raises(ValueError):
        register_session_params(session_layout, prefix=prefix)


def test_registering_the_same_content_twice_returns_the_same_id(layout):
    """Paramset identity is its content hash, which is why nothing is lost by
    registering here and submitting the request later."""
    session_layout, prefix = layout
    (session_layout.dir / PARAMS_FILENAME).write_text(
        "paramset_type: clustering\nparams:\n  n_blocks: 7\n"
    )

    first = register_session_params(session_layout, prefix=prefix)
    second = register_session_params(session_layout, prefix=prefix)

    assert first == second


def test_malformed_yaml_raises_rather_than_defaulting(layout):
    session_layout, prefix = layout
    (session_layout.dir / PARAMS_FILENAME).write_text("{{{ not yaml")

    with pytest.raises(ValueError):
        register_session_params(session_layout, prefix=prefix)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/ingest/test_params.py -q`
Expected: FAIL — no module `wl_preproc.ingest.params`.

- [ ] **Step 3: Write the implementation**

Create `wl_preproc/ingest/params.py`:

```python
"""Parameters that travelled with the raw data, per spec section 5.4.

Registration only. Section 5.4 also says "and inserts the request row", and this
does not: a request means an activation, and an activation needs a montage this
component cannot measure (spec section 2). Nothing is lost, because a paramset's
identity is its content hash — whenever the request is eventually made it
resolves to this same set.
"""

from __future__ import annotations

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.schema import DEFAULT_PREFIX, paramset

PARAMS_FILENAME = "session_params.yaml"


class SessionParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    paramset_type: str
    params: dict


def register_session_params(
    layout: SessionLayout, prefix: str = DEFAULT_PREFIX
) -> int | None:
    """Validate, content-hash and register. Returns None when there is no file.

    Raises ValueError on anything malformed, which the caller turns into a
    `params_invalid` quarantine. Processing under lab defaults because the
    session's own parameter file was broken is exactly the silent-default
    failure section 5.4 exists to prevent.
    """
    path = layout.dir / PARAMS_FILENAME
    if not path.exists():
        return None

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        declared = SessionParams.model_validate(loaded)
    except (yaml.YAMLError, ValidationError, TypeError) as exc:
        raise ValueError(f"{PARAMS_FILENAME} is not valid: {exc}") from exc

    paramset.activate(prefix=prefix)
    return paramset.register(declared.paramset_type, declared.params)
```

- [ ] **Step 4: Run the test**

Run: `.venv/bin/python -m pytest tests/ingest/test_params.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(ingest): register session_params.yaml, and only register it

Section 5.4's load-bearing requirement is that unknown keys are rejected, so
that nblocks versus n_blocks fails loudly rather than silently defaulting. That
is what extra="forbid" buys, and a test states it directly.

Section 5.4 also says "and inserts the request row", and this deliberately does
not. A request means an activation and an activation needs a montage this
component cannot measure. Nothing is lost by waiting: a paramset's identity is
its content hash, so whenever the request is eventually made it resolves to the
same set that was registered here.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: The watcher, and `wlpp ingest`

Spec §4, §6, §8. The orchestrator that puts Tasks 2–7 together, plus the manifest checks that only exist here.

**Files:**
- Create: `wl_preproc/ingest/watcher.py`
- Create: `tests/ingest/test_watcher.py`
- Modify: `wl_preproc/cli/main.py`

**Interfaces:**
- Consumes: everything from Tasks 2–7.
- Produces:
  - `class Outcome(StrEnum)` — `INGESTED`, `ALREADY`, `INCOMPLETE`, `STALLED`, `QUARANTINED`
  - `class ScanResult(NamedTuple)` — `outcomes: dict[str, Outcome]` keyed by directory path
  - `scan_once(root, prefix=DEFAULT_PREFIX, verify=True, now=None) -> ScanResult`

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/test_watcher.py`:

```python
"""scan_once: one pass over a storage root.

The two manifest checks that exist nowhere else are here — schema_version, which
has been declared and never compared since 1c-1, and session_id agreeing with
the directory it sits in.
"""

from __future__ import annotations

import datetime

import pytest

from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.watcher import Outcome, scan_once
from wl_preproc.schema import ingest
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


@pytest.fixture
def root(tmp_path, dj_conn, prefix):
    ingest.activate(prefix=prefix)
    generate_session(tmp_path, CI_RECIPE)
    return tmp_path, prefix, str(SessionLayout(tmp_path, CI_RECIPE.session_id).dir)


def test_a_good_session_ingests(root):
    tmp_path, prefix, session_dir = root

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.INGESTED


def test_scanning_twice_reports_already_the_second_time(root):
    tmp_path, prefix, session_dir = root
    scan_once(tmp_path, prefix=prefix)

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.ALREADY


def test_an_incomplete_session_is_not_ingested(root):
    tmp_path, prefix, session_dir = root
    SessionLayout(tmp_path, CI_RECIPE.session_id).done_marker("spikeglx").unlink()

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.INCOMPLETE
    assert len(ingest.Ingestion()) == 0


def test_an_incomplete_and_quiet_session_reports_stalled(root):
    tmp_path, prefix, session_dir = root
    SessionLayout(tmp_path, CI_RECIPE.session_id).done_marker("spikeglx").unlink()
    later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=3)

    result = scan_once(tmp_path, prefix=prefix, now=later)

    assert result.outcomes[session_dir] is Outcome.STALLED
    assert len(ingest.Ingestion()) == 0


def test_a_future_schema_version_quarantines(root):
    """Declared since 1c-1 and never once compared against SCHEMA_VERSION. A
    manifest claiming version 7 parses cleanly today."""
    tmp_path, prefix, session_dir = root
    manifest_path = SessionLayout(tmp_path, CI_RECIPE.session_id).manifest_path
    manifest_path.write_text(
        manifest_path.read_text().replace("schema_version: 1", "schema_version: 7")
    )

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    assert (ingest.Quarantine & {"session_dir": session_dir}).fetch1(
        "reason"
    ) == "manifest_schema_version"


def test_a_subject_name_too_long_for_element_animal_quarantines(root):
    """element-animal declares `subject : varchar(8)` while the manifest's
    subject is an unconstrained str, so "Wilhelmina" validates cleanly and then
    fails at the insert. Caught here as a manifest problem rather than surfacing
    as a MySQL error halfway through landing."""
    tmp_path, prefix, session_dir = root
    manifest_path = SessionLayout(tmp_path, CI_RECIPE.session_id).manifest_path
    manifest_path.write_text(
        manifest_path.read_text().replace(
            f"subject: {CI_RECIPE.subject}", "subject: Wilhelmina"
        )
    )

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    row = (ingest.Quarantine & {"session_dir": session_dir}).fetch1()
    assert row["reason"] == "subject_unrepresentable"
    assert row["detail"]["subject"] == "Wilhelmina"


def test_a_session_id_disagreeing_with_its_directory_quarantines(root):
    """Silently trusting either one files the session under a wrong identity."""
    tmp_path, prefix, session_dir = root
    manifest_path = SessionLayout(tmp_path, CI_RECIPE.session_id).manifest_path
    manifest_path.write_text(
        manifest_path.read_text().replace(str(CI_RECIPE.session_id), "2027-03-14_09")
    )

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    assert (ingest.Quarantine & {"session_dir": session_dir}).fetch1(
        "reason"
    ) == "session_id_mismatch"


def test_an_unparseable_manifest_quarantines_with_no_session_key(root):
    """The case Quarantine's path key exists for."""
    tmp_path, prefix, session_dir = root
    SessionLayout(tmp_path, CI_RECIPE.session_id).manifest_path.write_text("{{{ nope")

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    row = (ingest.Quarantine & {"session_dir": session_dir}).fetch1()
    assert row["reason"] == "manifest_invalid"
    assert row["subject"] is None


def test_a_corrupted_file_quarantines_as_checksum_mismatch(root):
    tmp_path, prefix, session_dir = root
    target = (
        SessionLayout(tmp_path, CI_RECIPE.session_id).system_dir("spikeglx")
        / f"{CI_RECIPE.session_id}_imec0.ap.meta"
    )
    target.write_bytes(target.read_bytes() + b"extra")

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    assert (ingest.Quarantine & {"session_dir": session_dir}).fetch1(
        "reason"
    ) == "checksum_mismatch"


def test_a_directory_without_a_manifest_is_ignored_entirely(root):
    """Not every directory under a storage root is a session. A scratch folder
    must not become a quarantine row."""
    tmp_path, prefix, _ = root
    (tmp_path / "some_scratch_dir").mkdir()

    result = scan_once(tmp_path, prefix=prefix)

    assert str(tmp_path / "some_scratch_dir") not in result.outcomes


def test_nothing_ever_writes_a_request_row(root):
    """Spec section 2: the watcher never calls submit(). Request.origin='ingest'
    stays reserved and unused, and this is what makes that a fact rather than
    an intention."""
    from wl_preproc.schema import request

    tmp_path, prefix, _ = root
    request.activate(prefix=prefix)
    scan_once(tmp_path, prefix=prefix)

    assert len(request.Request()) == 0
    assert len(request.Activation()) == 0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/ingest/test_watcher.py -q`
Expected: FAIL — no module `wl_preproc.ingest.watcher`.

- [ ] **Step 3: Write the implementation**

Create `wl_preproc/ingest/watcher.py`:

```python
"""One pass over a storage root. The only public entry point in `ingest`.

Polling rather than inotify: a scan cannot miss an event that fired while
nothing was listening, has no platform surface, and sessions arrive a few times
a week so latency is irrelevant.

Each immediate child of `root` is one candidate session; this does not recurse.
A session directory's internal structure is `SessionLayout`'s business.
"""

from __future__ import annotations

import datetime
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

from wl_preproc.contracts.done import blake3_file
from wl_preproc.contracts.manifest import SCHEMA_VERSION, SessionManifest
from wl_preproc.contracts.paths import MANIFEST_FILENAME, SessionLayout
from wl_preproc.ingest import landing
from wl_preproc.ingest.discover import discover_topology
from wl_preproc.ingest.params import register_session_params
from wl_preproc.ingest.sentinel import is_stalled, session_complete
from wl_preproc.ingest.verify import verify_session
from wl_preproc.schema import DEFAULT_PREFIX


class Outcome(StrEnum):
    INGESTED = "ingested"
    ALREADY = "already"
    INCOMPLETE = "incomplete"
    STALLED = "stalled"
    QUARANTINED = "quarantined"


class ScanResult(NamedTuple):
    outcomes: dict[str, Outcome]


def _candidate_dirs(root: Path) -> list[Path]:
    """Immediate children holding a manifest. Not every directory under a
    storage root is a session, and a scratch folder must not become a
    quarantine row."""
    return sorted(
        child
        for child in root.iterdir()
        if child.is_dir() and (child / MANIFEST_FILENAME).is_file()
    )


def _scan_one(
    session_dir: Path,
    prefix: str,
    verify: bool,
    now: datetime.datetime,
) -> Outcome:
    text = (session_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")

    try:
        manifest = SessionManifest.from_yaml(text)
    except Exception as exc:
        landing.quarantine(
            str(session_dir),
            reason="manifest_invalid",
            detail={"error": str(exc)[:2000]},
            prefix=prefix,
            now=now,
        )
        return Outcome.QUARANTINED

    if manifest.schema_version != SCHEMA_VERSION:
        landing.quarantine(
            str(session_dir),
            reason="manifest_schema_version",
            detail={"declared": manifest.schema_version, "implemented": SCHEMA_VERSION},
            prefix=prefix,
            subject=manifest.subject,
            session_dt=manifest.started_at,
            now=now,
        )
        return Outcome.QUARANTINED

    if len(manifest.subject) > landing.SUBJECT_MAX_LEN:
        landing.quarantine(
            str(session_dir),
            reason="subject_unrepresentable",
            detail={
                "subject": manifest.subject,
                "max_len": landing.SUBJECT_MAX_LEN,
                "note": "element-animal declares subject : varchar(8)",
            },
            prefix=prefix,
            session_dt=manifest.started_at,
            now=now,
        )
        return Outcome.QUARANTINED

    if manifest.session_id != session_dir.name:
        landing.quarantine(
            str(session_dir),
            reason="session_id_mismatch",
            detail={"manifest": manifest.session_id, "directory": session_dir.name},
            prefix=prefix,
            subject=manifest.subject,
            session_dt=manifest.started_at,
            now=now,
        )
        return Outcome.QUARANTINED

    layout = SessionLayout(session_dir.parent, manifest.session_id)

    session_key = {"subject": manifest.subject, "session_datetime": manifest.started_at}
    if landing.already_ingested(session_key, prefix=prefix):
        return Outcome.ALREADY

    if not session_complete(layout, manifest):
        return (
            Outcome.STALLED
            if is_stalled(layout, manifest, now=now)
            else Outcome.INCOMPLETE
        )

    integrity, mismatches = verify_session(layout, manifest, enabled=verify)
    if mismatches:
        landing.quarantine(
            str(session_dir),
            reason="checksum_mismatch",
            detail={"mismatches": [m._asdict() for m in mismatches][:200]},
            prefix=prefix,
            subject=manifest.subject,
            session_dt=manifest.started_at,
            now=now,
        )
        return Outcome.QUARANTINED

    try:
        register_session_params(layout, prefix=prefix)
    except ValueError as exc:
        landing.quarantine(
            str(session_dir),
            reason="params_invalid",
            detail={"error": str(exc)[:2000]},
            prefix=prefix,
            subject=manifest.subject,
            session_dt=manifest.started_at,
            now=now,
        )
        return Outcome.QUARANTINED

    landing.land_session(
        layout,
        manifest,
        discover_topology(layout, manifest),
        integrity,
        blake3_file(layout.manifest_path),
        prefix=prefix,
        now=now,
    )
    return Outcome.INGESTED


def scan_once(
    root: Path,
    prefix: str = DEFAULT_PREFIX,
    verify: bool = True,
    now: datetime.datetime | None = None,
) -> ScanResult:
    """One pass. Safe to run concurrently with itself — see `landing`."""
    at = now or datetime.datetime.now(datetime.UTC)
    return ScanResult(
        outcomes={
            str(session_dir): _scan_one(session_dir, prefix, verify, at)
            for session_dir in _candidate_dirs(Path(root))
        }
    )
```

- [ ] **Step 4: Run the test**

Run: `.venv/bin/python -m pytest tests/ingest/test_watcher.py -q`
Expected: PASS, 10 tests.

- [ ] **Step 5: Register the CLI subcommand**

In `wl_preproc/cli/main.py`, add a parser beside the existing ones and a dispatch branch, importing lazily inside the branch as the existing subcommands do:

```python
    ingest_parser = subparsers.add_parser("ingest", help="scan a storage root once")
    ingest_parser.add_argument("--root", required=True, help="directory holding session dirs")
    ingest_parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    ingest_parser.add_argument(
        "--no-verify",
        action="store_true",
        help="skip checksum verification; records integrity as 'skipped' rather than "
        "claiming a check that did not run",
    )
```

```python
    elif args.group == "ingest":
        from pathlib import Path

        from wl_preproc.ingest.watcher import scan_once

        result = scan_once(Path(args.root), prefix=args.prefix, verify=not args.no_verify)
        for session_dir, outcome in sorted(result.outcomes.items()):
            print(f"  [{outcome}] {session_dir}")
        return 0
```

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, 0 warnings.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(ingest): scan_once, and the two manifest checks that existed nowhere

schema_version has been a required manifest field since 1c-1 with SCHEMA_VERSION
declared beside it, and nothing has ever compared the two — a manifest claiming
version 7 parses cleanly today, which makes the field decoration rather than a
version. It is now enforced.

session_id is checked against the directory it sits in. SessionLayout derives
the directory from the id, so a manifest naming a different one is incoherent,
and silently trusting either files the session under a wrong identity.

Polling rather than inotify: a scan cannot miss an event that fired while
nothing was listening, it has no platform surface, and sessions arrive a few
times a week so latency is irrelevant.

A directory without a manifest is ignored rather than quarantined. Not every
folder under a storage root is a session, and a scratch directory must not
acquire a quarantine row.

A test asserts nothing here writes a Request or an Activation, which is what
makes "the watcher never calls submit()" a fact rather than an intention.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: The daily report, and the headroom check it reuses

Spec §11. `doctor.run_checks()` is monolithic — it prints and returns failure names — so the headroom check must be extracted before it can be reused rather than reimplemented.

**Files:**
- Modify: `wl_preproc/cli/doctor.py` (extract `scratch_headroom`)
- Create: `wl_preproc/cli/report.py`
- Create: `tests/cli/test_report.py`
- Modify: `wl_preproc/cli/main.py`

**Interfaces:**
- Consumes: `ingest.Ingestion`, `ingest.Quarantine`, `daemon.count_stale_jobs`, `sentinel.is_stalled`.
- Produces:
  - `doctor.scratch_headroom(path="/") -> tuple[int, bool]` — free GiB, and whether it clears `_MIN_SCRATCH_FREE_GIB`
  - `report.build_report(root, prefix=DEFAULT_PREFIX, now=None) -> str` — the Markdown body
  - `report.write_report(out_dir, root, prefix=DEFAULT_PREFIX, now=None) -> Path`

- [ ] **Step 1: Extract the headroom check**

In `wl_preproc/cli/doctor.py`, add above `run_checks`:

```python
def scratch_headroom(path: str = "/") -> tuple[int, bool]:
    """Free GiB at `path`, and whether it clears the floor.

    Extracted from `run_checks` so the daily report reuses this rather than
    reimplementing it — two definitions of "enough disk" that could disagree is
    exactly the drift worth preventing while there is still only one.

    `/` rather than a dedicated scratch mount remains a proxy: there is no
    scratch-root configuration to check instead, since SessionLayout takes its
    root as a caller-supplied argument rather than a resolved constant.
    """
    free_gib = shutil.disk_usage(path).free // 2**30
    return free_gib, free_gib >= _MIN_SCRATCH_FREE_GIB
```

Then replace the inline `usage = shutil.disk_usage("/")` / `free_gib = ...` lines inside `run_checks` with a call to it, keeping the existing `report(...)` call and its detail string exactly as they are.

- [ ] **Step 2: Run doctor's existing tests**

Run: `.venv/bin/python -m pytest tests/cli -q -k doctor`
Expected: PASS, unchanged. The extraction must not alter behaviour.

- [ ] **Step 3: Write the failing report test**

Create `tests/cli/test_report.py`:

```python
"""The daily report.

Its hardest requirement is negative: a category that cannot be counted yet must
say so, because "no failures" and "failures are not counted" must never render
identically.
"""

from __future__ import annotations

import datetime

import pytest

from wl_preproc.cli.report import build_report, write_report
from wl_preproc.ingest.watcher import scan_once
from wl_preproc.schema import ingest
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


@pytest.fixture
def scanned(tmp_path, dj_conn, prefix):
    ingest.activate(prefix=prefix)
    root = tmp_path / "scratch"
    root.mkdir()
    generate_session(root, CI_RECIPE)
    scan_once(root, prefix=prefix)
    return root, prefix


def test_it_counts_what_was_ingested(scanned):
    root, prefix = scanned

    body = build_report(root, prefix=prefix)

    assert "Ingested (24 h)" in body
    assert str(CI_RECIPE.session_id) in body


def test_it_names_the_categories_it_cannot_yet_count(scanned):
    """The negative requirement. A silently omitted category is
    indistinguishable from an empty one."""
    root, prefix = scanned

    body = build_report(root, prefix=prefix)

    assert "not yet reported" in body.lower()
    for missing in ("populated", "tier-d", "eye-detector"):
        assert missing in body.lower()


def test_a_quarantined_session_appears_with_its_reason(scanned):
    root, prefix = scanned
    ingest.Quarantine.insert1(
        {
            "session_dir": str(root / "2027-03-14_77"),
            "failed_at": datetime.datetime.now(),
            "reason": "checksum_mismatch",
            "detail": {},
            "subject": None,
            "session_dt": None,
        }
    )

    body = build_report(root, prefix=prefix)

    assert "checksum_mismatch" in body
    assert "2027-03-14_77" in body


def test_a_stalled_transfer_appears(scanned):
    root, prefix = scanned
    generate_session(root, CI_RECIPE.model_copy(update={"session_id": "2027-03-14_05"}))
    from wl_preproc.contracts.paths import SessionLayout

    SessionLayout(root, "2027-03-14_05").done_marker("spikeglx").unlink()
    later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=5)

    body = build_report(root, prefix=prefix, now=later)

    assert "Stalled transfers" in body
    assert "2027-03-14_05" in body


def test_it_writes_a_dated_file_and_returns_its_path(scanned, tmp_path):
    root, prefix = scanned
    out = tmp_path / "reports"

    path = write_report(out, root, prefix=prefix, now=datetime.datetime(2027, 3, 15, 7, 0))

    assert path == out / "2027-03-15.md"
    assert path.read_text().startswith("# wl-preproc")


def test_the_report_opens_no_write_transaction(scanned):
    """The same read-only guarantee `wlpp doctor` carries, so anyone can run it
    at any time without considering what else is running."""
    import datajoint as dj

    root, prefix = scanned
    build_report(root, prefix=prefix)

    assert dj.conn().in_transaction is False
```

- [ ] **Step 4: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/cli/test_report.py -q`
Expected: FAIL — no module `wl_preproc.cli.report`.

- [ ] **Step 5: Write the report**

Create `wl_preproc/cli/report.py`:

```python
"""The daily status report, per spec section 11.

Writes a dated file and prints it. Both, so a future `cron ... | mail` needs no
change here and the accumulating history answers "when did scratch start
filling up?" without anyone having planned for the question.

Reads and never writes.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from wl_preproc.cli.doctor import scratch_headroom
from wl_preproc.schema import DEFAULT_PREFIX

# Categories spec section 10 names that nothing can count yet. Listed rather
# than omitted: a silently missing section is indistinguishable from an empty
# one, and "no failures" must never render the same as "failures are not
# counted".
_NOT_YET_REPORTED = (
    ("Populated / failed", "1c-4 — nothing computes until the timebase stage exists"),
    ("Tier-D sessions", "1c-4 — the tier is derived from fit residuals"),
    ("Eye-detector outliers", "Phase 3 — the eye branch"),
)


def build_report(
    root: Path,
    prefix: str = DEFAULT_PREFIX,
    now: datetime.datetime | None = None,
) -> str:
    from wl_preproc.contracts.manifest import SessionManifest
    from wl_preproc.contracts.paths import MANIFEST_FILENAME, SessionLayout
    from wl_preproc.daemon import count_stale_jobs
    from wl_preproc.ingest.sentinel import is_stalled
    from wl_preproc.schema import ingest

    at = now or datetime.datetime.now(datetime.UTC)
    ingest.activate(prefix=prefix)

    since = at - datetime.timedelta(hours=24)
    recent = (ingest.Ingestion & f"ingested_at > '{since:%Y-%m-%d %H:%M:%S}'").fetch(
        as_dict=True
    )
    quarantined = ingest.Quarantine.fetch(as_dict=True)

    stalled: list[str] = []
    for child in sorted(Path(root).iterdir()):
        manifest_path = child / MANIFEST_FILENAME
        if not (child.is_dir() and manifest_path.is_file()):
            continue
        try:
            manifest = SessionManifest.from_yaml(manifest_path.read_text())
        except Exception:
            continue  # already a quarantine row; not also a stall
        layout = SessionLayout(Path(root), manifest.session_id)
        if is_stalled(layout, manifest, now=at):
            stalled.append(str(child))

    free_gib, headroom_ok = scratch_headroom()
    stale = count_stale_jobs()

    lines = [f"# wl-preproc daily — {at:%Y-%m-%d}", ""]

    lines += [f"## Ingested (24 h) — {len(recent)}", ""]
    lines += [f"- `{row['session_dir']}` ({row['integrity']})" for row in recent] or ["- none"]

    lines += ["", f"## Quarantined — {len(quarantined)}", ""]
    lines += [
        f"- `{row['session_dir']}` — **{row['reason']}**" for row in quarantined
    ] or ["- none"]

    lines += ["", f"## Stalled transfers — {len(stalled)}", ""]
    lines += [f"- `{path}`" for path in stalled] or ["- none"]

    lines += ["", "## Stuck jobs", ""]
    lines += [
        "- not checked (no schema activated in this process)"
        if stale is None
        else f"- {stale} stale reservation(s)"
    ]

    lines += ["", "## Disk", ""]
    lines += [f"- scratch: {free_gib} GiB free {'(ok)' if headroom_ok else '(LOW)'}"]

    lines += ["", "## Not yet reported", ""]
    lines += [f"- **{name}** — {why}" for name, why in _NOT_YET_REPORTED]

    return "\n".join(lines) + "\n"


def write_report(
    out_dir: Path,
    root: Path,
    prefix: str = DEFAULT_PREFIX,
    now: datetime.datetime | None = None,
) -> Path:
    """Write `out_dir/YYYY-MM-DD.md` and return its path."""
    at = now or datetime.datetime.now(datetime.UTC)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{at:%Y-%m-%d}.md"
    path.write_text(build_report(root, prefix=prefix, now=at), encoding="utf-8")
    return path
```

- [ ] **Step 6: Run the test**

Run: `.venv/bin/python -m pytest tests/cli/test_report.py -q`
Expected: PASS, 6 tests. `SessionRecipe` is a pydantic `BaseModel` (verified), so `model_copy(update=...)` is the way to vary one field — `_replace` does not exist on it.

- [ ] **Step 7: Register the CLI subcommand**

In `wl_preproc/cli/main.py`:

```python
    report_parser = subparsers.add_parser("report", help="write the daily status report")
    report_parser.add_argument("--root", required=True)
    report_parser.add_argument("--out", default="/var/lib/wlpp/reports")
    report_parser.add_argument("--prefix", default=DEFAULT_PREFIX)
```

```python
    elif args.group == "report":
        from pathlib import Path

        from wl_preproc.cli.report import write_report

        path = write_report(Path(args.out), Path(args.root), prefix=args.prefix)
        print(path.read_text(), end="")
        return 0
```

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, 0 warnings.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(cli): the daily report, and the headroom check it reuses

doctor's scratch check was inline in a function that prints and returns failure
names, so it could not be reused as it stood. Extracting it means the report and
the doctor share one definition of "enough disk" rather than growing two that
can disagree — worth doing while there is still only one.

The report's hardest requirement is negative. Three categories section 10 names
cannot be counted until 1c-4 and Phase 3 exist, and they are listed explicitly
under "Not yet reported" rather than omitted, because a silently missing section
is indistinguishable from an empty one and "no failures" must never render the
same as "failures are not counted".

It writes a dated file and prints it. Both, so a later cron pipe to mail needs
no change here, and the accumulating history answers "when did scratch start
filling up?" without anyone having planned for the question. A test asserts it
opens no write transaction, the same guarantee wlpp doctor already carries.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review

**Spec coverage.** §2 → Task 8's `test_nothing_ever_writes_a_request_row`. §4 → Task 2. §5 → Tasks 1, 4. §6 → Task 8. §7 → Task 3. §8 → Tasks 5, 6. §9 → Tasks 5, 6. §10 → Task 7. §11 → Task 9. §12's fixture note → Tasks 1, 4. §13's "no lock" → Task 6. No section is unimplemented.

**Type consistency.** `Integrity` is defined in `verify.py` (Task 4) and consumed by `landing.py` (Task 6) and the `Ingestion` enum (Task 5), which Task 5's `test_integrity_states_match_the_verifier` pins against drift. `SystemState` is defined in `discover.py` (Task 3) and consumed by Tasks 4 and 6. `MarkerState` is defined in `sentinel.py` (Task 2) and consumed by Tasks 3 and 4. `scan_once` returns `ScanResult`, whose `outcomes` is keyed by `str(session_dir)` in both the implementation and every test that reads it.

**Three things that were verified rather than assumed, and corrected the plan.**
`pipeline.Subject` was read from the installed `element-animal`: `subject` is
`varchar(8)`, and `subject_birth_date` is a required `date` with **no default and not
nullable** — an earlier draft of Task 6 passed `None` and would have failed at the first
insert. Task 6 now writes an explicit sentinel with a description saying the date is
unknown, and Task 8 quarantines a subject name too long to represent rather than letting
it surface as a MySQL error mid-landing. `SessionRecipe` was checked and is a pydantic
`BaseModel`, so Task 9 uses `model_copy(update=...)`; `_replace` does not exist on it.

**And one that was checked rather than left as a caveat.** `paramset.register` returns the
existing index when the content hash already exists — its own docstring states the rule
("the hash is the identity") and the fast path returns `existing.fetch1("paramset_idx")`
before attempting any insert. So Task 7's
`test_registering_the_same_content_twice_returns_the_same_id` asserts the contract that
module actually offers, and nothing in this phase needs to revise paramset immutability,
which 1c-1 settled.
