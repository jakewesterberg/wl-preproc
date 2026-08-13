# Phase 1a — Synthetic Session Generator: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate byte-format-correct session directories with planted ground truth and deliberately injected faults, so the pipeline can be tested end-to-end before any real recording exists.

**Architecture:** A declarative `SessionRecipe` describes what to generate; a generator materialises it and returns a `GroundTruth` alongside the files, so tests assert *recovery* rather than re-deriving expectations from the same code. One seed makes output byte-identical. The same code path produces a 2 KB CI fixture and a 300 GB benchmark — only the recipe changes.

**Tech Stack:** Python 3.11+, NumPy, Pydantic v2, pytest, SpikeInterface (test-only, as the format oracle)

**Spec:** [`../specs/2026-08-12-wl-preproc-design.md`](../specs/2026-08-12-wl-preproc-design.md) §9

**Depends on:** Phase 0 contracts (merged) and `wl-sync` (public, on PyPI-by-git).

## Global Constraints

- **Python ≥3.11**, no upper cap. PyTorch is not a dependency here (spec §6.6).
- **Every generated file validates against the Phase 0 contracts.** The generator must not be able to emit a manifest or sidecar the pipeline would reject — if it can, one of the two is wrong.
- **Determinism is a test, not an aspiration.** Same recipe plus same seed produces byte-identical output.
- **The barcode format belongs to `wl-sync`.** Import `encode`, never reimplement.
- **Alignment guarantees** (spec §4.1, as corrected): ≥1 barcode in any 2.0 s window, ≥2 in any 3.0 s. A segment under 1.2 s may contain none and is expected to be rejected.
- **Ground truth is returned, never re-derived.** A test that recomputes expected values from generator internals tests nothing.

## Scope: what this plan deliberately excludes

- **Intan `.rhs` and stim artifacts.** Spec open item 3 records the `.rhs` stim-flag layout as unverified. Generating against an unconfirmed binary format yields fixtures that look correct and encode the wrong thing. Phase 1b, after the format is checked against the RHX specification.
- **Biophysically realistic spikes.** Planted templates plus noise are enough to test ingest, sync and coverage. Sorting quality is Phase 2, where SpikeInterface's own ground-truth generators are the right tool.
- **ohDPI eye data content.** The frame *times* matter here and come from the sync box; gaze content is Phase 3.

## File Structure

The generator lives in `wl_preproc/synth/`, **not `tests/synth/` as spec §3.4 shows**. It is shipped code: `wlpp synth generate` produces the P6000 benchmark session, which is not a test. Spec §3.4 should be corrected when this lands.

| File | Responsibility |
|---|---|
| `wl_preproc/synth/recipe.py` | `SessionRecipe`, `BlockSpec`, `MontageSpec`, `Fault` |
| `wl_preproc/synth/truth.py` | `GroundTruth` and its members |
| `wl_preproc/synth/timeline.py` | Barcode, block, trial and code-word timing; drift |
| `wl_preproc/synth/syncbox.py` | Emits the sync box log |
| `wl_preproc/synth/spikeglx.py` | Emits `.bin` + `.meta` |
| `wl_preproc/synth/peripherals.py` | Manifest, camera sidecar, task file |
| `wl_preproc/synth/faults.py` | Fault application |
| `wl_preproc/synth/session.py` | Assembly; `generate_session()` |
| `tests/synth/*` | One module per source module |

---

### Task 1: Recipe and ground-truth models

**Files:**
- Create: `wl_preproc/synth/__init__.py`, `wl_preproc/synth/recipe.py`, `wl_preproc/synth/truth.py`
- Test: `tests/synth/test_recipe.py`

**Interfaces:**
- Consumes: `TaskTypeCode` from `wl_preproc.contracts.events`; `SYSTEMS` from `wl_preproc.contracts.paths`
- Produces: `Fault` (str Enum: `CLOCK_DRIFT`, `DROPPED_BARCODES`, `SHORT_SEGMENT`, `MID_SESSION_RESTART`, `STOP_MID_TRIAL`, `DROPPED_CAMERA_FRAMES`, `MISSING_DEVICE`, `TRIAL_COUNT_MISMATCH`, `TRUNCATED_FILE`); `BlockSpec(task_type, n_trials, trial_duration_s)`; `MontageSpec(start_s, end_s)`; `SessionRecipe(session_id, subject, rig, systems, blocks, montages, n_ap_channels, ap_sample_rate_hz, seed, faults, drift_ppm)` with `.duration_s` derived; `TrialTruth(trial_id, block_id, start_s, end_s)`; `BlockTruth(block_id, task_type, start_s, end_s)`; `GroundTruth(barcodes, code_words, trials, blocks, spikes)`; `CI_RECIPE` and `BENCHMARK_RECIPE` constants

- [ ] **Step 1: Write the failing test**

```python
# tests/synth/test_recipe.py
import pytest
from pydantic import ValidationError

from wl_preproc.contracts.events import TaskTypeCode
from wl_preproc.synth.recipe import BENCHMARK_RECIPE, CI_RECIPE, BlockSpec, MontageSpec, SessionRecipe


def test_duration_is_derived_from_blocks():
    recipe = SessionRecipe(
        session_id="2027-03-14_01",
        subject="pico",
        rig="rig-a",
        systems=("syncbox", "spikeglx"),
        blocks=(
            BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=2, trial_duration_s=3.0),
            BlockSpec(task_type=TaskTypeCode.RESTING_DARK, n_trials=1, trial_duration_s=4.0),
        ),
        montages=(MontageSpec(start_s=0.0, end_s=10.0),),
        n_ap_channels=4,
        ap_sample_rate_hz=30_000.0,
        seed=1,
    )
    assert recipe.duration_s == pytest.approx(10.0)


def test_syncbox_must_be_present():
    """The sync box is at every session — a recipe without it is not a session."""
    with pytest.raises(ValidationError):
        SessionRecipe(
            session_id="2027-03-14_01", subject="pico", rig="rig-a",
            systems=("spikeglx",),
            blocks=(BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=1, trial_duration_s=3.0),),
            montages=(MontageSpec(start_s=0.0, end_s=3.0),),
            n_ap_channels=4, ap_sample_rate_hz=30_000.0, seed=1,
        )


def test_montages_must_cover_the_session():
    """A gap in montage coverage would leave blocks belonging to no montage."""
    with pytest.raises(ValidationError) as exc:
        SessionRecipe(
            session_id="2027-03-14_01", subject="pico", rig="rig-a",
            systems=("syncbox",),
            blocks=(BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=2, trial_duration_s=3.0),),
            montages=(MontageSpec(start_s=0.0, end_s=3.0),),
            n_ap_channels=4, ap_sample_rate_hz=30_000.0, seed=1,
        )
    assert "cover" in str(exc.value)


def test_ci_recipe_is_small_enough_for_ci():
    """A CI fixture that takes minutes or gigabytes will be deleted by whoever
    inherits it. Keep it tiny by construction."""
    assert CI_RECIPE.duration_s <= 30.0
    assert CI_RECIPE.n_ap_channels <= 8


def test_benchmark_recipe_is_realistic():
    """The P6000 benchmark needs a realistic probe, not a toy."""
    assert BENCHMARK_RECIPE.n_ap_channels == 384
    assert BENCHMARK_RECIPE.ap_sample_rate_hz == 30_000.0


def test_recipes_are_frozen():
    with pytest.raises(ValidationError):
        CI_RECIPE.seed = 99
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/synth/test_recipe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.synth'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/synth/__init__.py
__all__: list[str] = []
```

```python
# wl_preproc/synth/recipe.py
"""What session to generate. Declarative, so a fixture is data rather than a
call with thirty keyword arguments."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator

from wl_preproc.contracts.events import TaskTypeCode
from wl_preproc.contracts.paths import SYSTEMS


class Fault(str, Enum):
    """Deliberate pathology. Each corresponds to a real failure the pipeline
    must survive, and each is a test that would otherwise wait for a bad day in
    the lab to write itself (spec section 9.1)."""

    CLOCK_DRIFT = "clock_drift"
    DROPPED_BARCODES = "dropped_barcodes"
    SHORT_SEGMENT = "short_segment"
    MID_SESSION_RESTART = "mid_session_restart"
    STOP_MID_TRIAL = "stop_mid_trial"
    DROPPED_CAMERA_FRAMES = "dropped_camera_frames"
    MISSING_DEVICE = "missing_device"
    TRIAL_COUNT_MISMATCH = "trial_count_mismatch"
    TRUNCATED_FILE = "truncated_file"


class BlockSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_type: TaskTypeCode
    n_trials: int
    trial_duration_s: float

    @property
    def duration_s(self) -> float:
        return self.n_trials * self.trial_duration_s


class MontageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start_s: float
    end_s: float


class SessionRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    subject: str
    rig: str
    systems: tuple[str, ...]
    blocks: tuple[BlockSpec, ...]
    montages: tuple[MontageSpec, ...]
    n_ap_channels: int
    ap_sample_rate_hz: float
    seed: int
    faults: tuple[Fault, ...] = ()
    drift_ppm: float = 0.0

    @property
    def duration_s(self) -> float:
        return sum(block.duration_s for block in self.blocks)

    @model_validator(mode="after")
    def _coherent(self) -> SessionRecipe:
        unknown = [s for s in self.systems if s not in SYSTEMS]
        if unknown:
            raise ValueError(f"unknown systems: {unknown}")
        if "syncbox" not in self.systems:
            raise ValueError("syncbox is present at every session")
        covered = sum(m.end_s - m.start_s for m in self.montages)
        if abs(covered - self.duration_s) > 1e-6:
            raise ValueError(
                f"montages must cover the session: {covered}s of {self.duration_s}s"
            )
        return self


CI_RECIPE = SessionRecipe(
    session_id="2027-03-14_01",
    subject="pico",
    rig="rig-a",
    systems=("syncbox", "spikeglx", "bcam"),
    blocks=(
        BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=3, trial_duration_s=3.0),
        BlockSpec(task_type=TaskTypeCode.RESTING_DARK, n_trials=1, trial_duration_s=6.0),
    ),
    montages=(MontageSpec(start_s=0.0, end_s=15.0),),
    n_ap_channels=4,
    ap_sample_rate_hz=30_000.0,
    seed=20270314,
)

BENCHMARK_RECIPE = SessionRecipe(
    session_id="2027-03-14_02",
    subject="pico",
    rig="rig-a",
    systems=("syncbox", "spikeglx", "bcam"),
    blocks=(BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=1200, trial_duration_s=3.0),),
    montages=(MontageSpec(start_s=0.0, end_s=3600.0),),
    n_ap_channels=384,
    ap_sample_rate_hz=30_000.0,
    seed=20270314,
)
```

```python
# wl_preproc/synth/truth.py
"""What was planted. Returned alongside the files so tests assert *recovery*.

A test that recomputes expectations from generator internals tests nothing —
it agrees with itself. Ground truth exists so the assertion crosses the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from wl_preproc.contracts.events import TaskTypeCode


@dataclass(frozen=True, slots=True)
class TrialTruth:
    trial_id: int
    block_id: int
    start_s: float
    end_s: float


@dataclass(frozen=True, slots=True)
class BlockTruth:
    block_id: int
    task_type: TaskTypeCode
    start_s: float
    end_s: float


@dataclass(frozen=True, slots=True)
class GroundTruth:
    barcodes: tuple[tuple[int, float], ...]      # (value, session-time seconds)
    code_words: tuple[tuple[float, int], ...]    # (session-time seconds, 16-bit word)
    trials: tuple[TrialTruth, ...]
    blocks: tuple[BlockTruth, ...]
    spikes: tuple[tuple[float, int], ...]        # (session-time seconds, channel)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/synth/test_recipe.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/synth tests/synth
git commit -m "feat(synth): session recipe and ground-truth models"
```

---

### Task 2: Timeline generation

**Files:**
- Create: `wl_preproc/synth/timeline.py`
- Test: `tests/synth/test_timeline.py`

**Interfaces:**
- Consumes: `SessionRecipe`, `Fault`; `GroundTruth`, `TrialTruth`, `BlockTruth`; `Marker`, `Escape`, `encode_payload` from `wl_preproc.contracts.events`; `INTERVAL_US` from `wl_sync.barcode`
- Produces: `build_timeline(recipe: SessionRecipe) -> GroundTruth`; `apply_drift(time_s: float, drift_ppm: float) -> float`

- [ ] **Step 1: Write the failing test**

```python
# tests/synth/test_timeline.py
import pytest

from wl_preproc.contracts.events import Escape, Marker, TaskTypeCode, decode_stream
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.timeline import apply_drift, build_timeline


def test_barcodes_are_emitted_once_per_second():
    truth = build_timeline(CI_RECIPE)
    times = [t for _, t in truth.barcodes]
    assert times[0] == pytest.approx(0.0)
    for earlier, later in zip(times, times[1:]):
        assert later - earlier == pytest.approx(1.0)


def test_barcode_values_are_monotonic():
    values = [v for v, _ in build_timeline(CI_RECIPE).barcodes]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_blocks_tile_the_session_without_gaps():
    truth = build_timeline(CI_RECIPE)
    assert truth.blocks[0].start_s == pytest.approx(0.0)
    for earlier, later in zip(truth.blocks, truth.blocks[1:]):
        assert later.start_s == pytest.approx(earlier.end_s)
    assert truth.blocks[-1].end_s == pytest.approx(CI_RECIPE.duration_s)


def test_trial_ids_are_unique_and_ascending():
    ids = [t.trial_id for t in build_timeline(CI_RECIPE).trials]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_every_trial_belongs_to_a_real_block():
    truth = build_timeline(CI_RECIPE)
    block_ids = {b.block_id for b in truth.blocks}
    assert all(t.block_id in block_ids for t in truth.trials)


def test_code_words_decode_back_to_the_planted_structure():
    """The generator emits through the real encoder, so the real decoder must
    recover it. This is the loop that catches a protocol mismatch."""
    truth = build_timeline(CI_RECIPE)
    events = decode_stream(list(truth.code_words))
    starts = [e for e in events if getattr(e, "escape", None) is Escape.BLOCK_START]
    assert len(starts) == len(truth.blocks)
    assert TaskTypeCode(starts[0].words[1]) is TaskTypeCode.RF_MAP

    numbers = [e for e in events if getattr(e, "escape", None) is Escape.TRIAL_NUMBER]
    assert len(numbers) == len(truth.trials)
    first = (numbers[0].words[0] << 16) | numbers[0].words[1]
    assert first == truth.trials[0].trial_id


def test_no_decode_errors_in_a_clean_session():
    from wl_preproc.contracts.events import DecodeError

    events = decode_stream(list(build_timeline(CI_RECIPE).code_words))
    assert not [e for e in events if isinstance(e, DecodeError)]


def test_trial_outcome_markers_are_present():
    truth = build_timeline(CI_RECIPE)
    codes = [w for _, w in truth.code_words]
    assert Marker.TRIAL_START.value in codes
    assert Marker.SESSION_START.value in codes
    assert Marker.SESSION_END.value in codes


def test_drift_is_proportional_and_signed():
    assert apply_drift(100.0, 0.0) == pytest.approx(100.0)
    assert apply_drift(100.0, 50.0) == pytest.approx(100.0 * (1 + 50e-6))
    assert apply_drift(100.0, -50.0) == pytest.approx(100.0 * (1 - 50e-6))


def test_timeline_is_deterministic():
    assert build_timeline(CI_RECIPE) == build_timeline(CI_RECIPE)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/synth/test_timeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.synth.timeline'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/synth/timeline.py
"""The session's ground-truth timeline in session time.

Everything here is exact. Faults are applied afterwards (faults.py), so the
timeline is always the answer the pipeline should recover, never the corrupted
version it is handed.
"""

from __future__ import annotations

import numpy as np

from wl_preproc.contracts.events import Escape, Marker, encode_payload
from wl_preproc.synth.recipe import SessionRecipe
from wl_preproc.synth.truth import BlockTruth, GroundTruth, TrialTruth

BARCODE_INTERVAL_S = 1.0
SPIKE_RATE_HZ = 5.0
CODE_WORD_SPACING_S = 0.001


def apply_drift(time_s: float, drift_ppm: float) -> float:
    """A device clock running fast or slow by drift_ppm parts per million."""
    return time_s * (1.0 + drift_ppm * 1e-6)


def _uint32_words(value: int) -> list[int]:
    return [(value >> 16) & 0xFFFF, value & 0xFFFF]


def build_timeline(recipe: SessionRecipe) -> GroundTruth:
    rng = np.random.default_rng(recipe.seed)

    barcodes: list[tuple[int, float]] = []
    time_s = 0.0
    value = 1_000_000
    while time_s < recipe.duration_s:
        barcodes.append((value, time_s))
        value += 1
        time_s += BARCODE_INTERVAL_S

    blocks: list[BlockTruth] = []
    trials: list[TrialTruth] = []
    words: list[tuple[float, int]] = []

    words.append((0.0, Marker.SESSION_START.value))
    cursor = 0.0
    trial_id = 1

    for block_index, block in enumerate(recipe.blocks, start=1):
        block_start = cursor
        for word_index, word in enumerate(
            encode_payload(Escape.BLOCK_START, [block_index, int(block.task_type)])
        ):
            words.append((block_start + word_index * CODE_WORD_SPACING_S, word))

        for _ in range(block.n_trials):
            trial_start = cursor
            trial_end = cursor + block.trial_duration_s
            trials.append(
                TrialTruth(
                    trial_id=trial_id,
                    block_id=block_index,
                    start_s=trial_start,
                    end_s=trial_end,
                )
            )
            words.append((trial_start, Marker.TRIAL_START.value))
            for word_index, word in enumerate(
                encode_payload(Escape.TRIAL_NUMBER, _uint32_words(trial_id))
            ):
                words.append(
                    (trial_start + (word_index + 1) * CODE_WORD_SPACING_S, word)
                )
            words.append((trial_end - CODE_WORD_SPACING_S, Marker.TRIAL_CORRECT.value))
            cursor = trial_end
            trial_id += 1

        blocks.append(
            BlockTruth(
                block_id=block_index,
                task_type=block.task_type,
                start_s=block_start,
                end_s=cursor,
            )
        )
        words.append((cursor - CODE_WORD_SPACING_S / 2, Marker.BLOCK_END.value))

    words.append((recipe.duration_s, Marker.SESSION_END.value))
    words.sort(key=lambda pair: pair[0])

    n_spikes = int(SPIKE_RATE_HZ * recipe.duration_s * recipe.n_ap_channels)
    spike_times = np.sort(rng.uniform(0.0, recipe.duration_s, n_spikes))
    spike_channels = rng.integers(0, recipe.n_ap_channels, n_spikes)
    spikes = tuple(
        (float(t), int(c)) for t, c in zip(spike_times, spike_channels)
    )

    return GroundTruth(
        barcodes=tuple(barcodes),
        code_words=tuple(words),
        trials=tuple(trials),
        blocks=tuple(blocks),
        spikes=spikes,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/synth/test_timeline.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/synth/timeline.py tests/synth/test_timeline.py
git commit -m "feat(synth): ground-truth timeline with blocks, trials and code words"
```

---

### Task 3: Sync box log emission

**Files:**
- Create: `wl_preproc/synth/syncbox.py`
- Test: `tests/synth/test_syncbox.py`

**Interfaces:**
- Consumes: `SessionRecipe`; `GroundTruth`; `encode`, `Barcode`, `decode_edges` from `wl_sync.barcode`; `Edge`, `CodeWord`, `SyncBoxLogHeader`, `write_log`, `read_log` from `wl_sync.log`
- Produces: `write_syncbox_log(path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0) -> None`; `BARCODE_GPIO = 17`; `SYNCBOX_PRE_ROLL_S = 1.0`

- [ ] **Step 1: Write the failing test**

```python
# tests/synth/test_syncbox.py
import datetime

import pytest
from wl_sync.barcode import decode_edges
from wl_sync.log import CodeWord, Edge, read_log

from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.syncbox import BARCODE_GPIO, SYNCBOX_PRE_ROLL_S, write_syncbox_log
from wl_preproc.synth.timeline import build_timeline


def emit(tmp_path, drift_ppm=0.0):
    truth = build_timeline(CI_RECIPE)
    path = tmp_path / "syncbox.log"
    write_syncbox_log(path, CI_RECIPE, truth, drift_ppm=drift_ppm)
    return truth, read_log(path)


def test_header_validates_and_names_the_session(tmp_path):
    _, (header, _) = emit(tmp_path)
    assert header.session_id == CI_RECIPE.session_id
    assert header.rig == CI_RECIPE.rig
    assert header.written_at.tzinfo is not None


def test_barcodes_round_trip_through_the_real_decoder(tmp_path):
    """Written with wl-sync's encoder, read with wl-sync's decoder. If the
    generator and the pipeline ever disagree about the format, this fails."""
    truth, (_, records) = emit(tmp_path)
    edges = [(r.tick_us, r.level) for r in records if isinstance(r, Edge) and r.gpio == BARCODE_GPIO]
    decoded = decode_edges(edges, start_us=0)
    assert [b.value for b in decoded] == [v for v, _ in truth.barcodes]


def test_barcode_times_match_ground_truth_after_removing_the_pre_roll(tmp_path):
    """The log's tick origin is deliberately not session time. A fixture where
    they coincide lets a pipeline bug that ignores the offset pass."""
    truth, (_, records) = emit(tmp_path)
    edges = [(r.tick_us, r.level) for r in records if isinstance(r, Edge) and r.gpio == BARCODE_GPIO]
    decoded = decode_edges(edges, start_us=0)
    for barcode, (_, expected_s) in zip(decoded, truth.barcodes):
        assert barcode.start_us / 1e6 - SYNCBOX_PRE_ROLL_S == pytest.approx(expected_s, abs=1e-3)


def test_first_barcode_is_decodable(tmp_path):
    """Without a pre-roll the first frame sits at tick 0 with no preceding idle,
    and wl-sync's decoder correctly refuses it."""
    truth, (_, records) = emit(tmp_path)
    edges = [(r.tick_us, r.level) for r in records if isinstance(r, Edge) and r.gpio == BARCODE_GPIO]
    assert decode_edges(edges, start_us=0)[0].value == truth.barcodes[0][0]


def test_every_code_word_is_logged(tmp_path):
    truth, (_, records) = emit(tmp_path)
    logged = [(r.tick_us / 1e6, r.word) for r in records if isinstance(r, CodeWord)]
    assert [w for _, w in logged] == [w for _, w in truth.code_words]


def test_records_are_in_tick_order(tmp_path):
    _, (_, records) = emit(tmp_path)
    ticks = [r.tick_us for r in records]
    assert ticks == sorted(ticks)


def test_drift_stretches_the_timeline(tmp_path):
    """A device running 100 ppm fast puts its last barcode measurably late."""
    truth, (_, clean) = emit(tmp_path, drift_ppm=0.0)
    _, (_, drifted) = emit(tmp_path, drift_ppm=100.0)
    last_clean = max(r.tick_us for r in clean)
    last_drifted = max(r.tick_us for r in drifted)
    assert last_drifted > last_clean
    assert last_drifted / last_clean == pytest.approx(1 + 100e-6, rel=1e-3)


def test_emission_is_deterministic(tmp_path):
    truth = build_timeline(CI_RECIPE)
    first, second = tmp_path / "a.log", tmp_path / "b.log"
    write_syncbox_log(first, CI_RECIPE, truth)
    write_syncbox_log(second, CI_RECIPE, truth)
    a = first.read_text().split("\n", 1)[1]  # drop the header, which stamps a time
    b = second.read_text().split("\n", 1)[1]
    assert a == b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/synth/test_syncbox.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.synth.syncbox'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/synth/syncbox.py
"""Emit a sync box log for a planted timeline.

Barcodes go through wl-sync's own encoder rather than a local reimplementation,
so a format change there breaks these fixtures loudly instead of letting the
generator and the pipeline drift into disagreeing.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from wl_sync.barcode import encode
from wl_sync.log import SCHEMA_VERSION, CodeWord, Edge, Record, SyncBoxLogHeader, write_log

from wl_preproc.synth.recipe import SessionRecipe
from wl_preproc.synth.timeline import apply_drift
from wl_preproc.synth.truth import GroundTruth

BARCODE_GPIO = 17
CODE_STROBE_GPIO = 18
CODE_DATA_BASE_GPIO = 2

# The log's tick origin is not session time, deliberately. Two reasons: the
# decoder needs an idle before the first frame or it correctly refuses it, and a
# fixture where tick == session time would let a pipeline bug that ignores the
# offset pass. Each system gets a *different* pre-roll for the same reason.
SYNCBOX_PRE_ROLL_S = 1.0

_EPOCH = datetime.datetime(2027, 3, 14, 9, 0, tzinfo=datetime.timezone.utc)


def write_syncbox_log(
    path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0
) -> None:
    records: list[Record] = []

    for value, start_s in truth.barcodes:
        tick = int(round((apply_drift(start_s, drift_ppm) + SYNCBOX_PRE_ROLL_S) * 1e6))
        for level, duration_us in encode(value):
            records.append(Edge(tick_us=tick, gpio=BARCODE_GPIO, level=level))
            tick += duration_us
        records.append(Edge(tick_us=tick, gpio=BARCODE_GPIO, level=0))

    for time_s, word in truth.code_words:
        records.append(
            CodeWord(
                tick_us=int(
                    round((apply_drift(time_s, drift_ppm) + SYNCBOX_PRE_ROLL_S) * 1e6)
                ),
                word=word,
            )
        )

    records.sort(key=lambda record: record.tick_us)

    header = SyncBoxLogHeader(
        schema_version=SCHEMA_VERSION,
        session_id=recipe.session_id,
        rig=recipe.rig,
        boot_id=f"synth{recipe.seed:08x}",
        written_at=_EPOCH,
        gpio_map={
            "barcode_out": BARCODE_GPIO,
            "code_strobe": CODE_STROBE_GPIO,
            "code_data_base": CODE_DATA_BASE_GPIO,
        },
    )
    write_log(path, header, records)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/synth/test_syncbox.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/synth/syncbox.py tests/synth/test_syncbox.py
git commit -m "feat(synth): sync box log emission through wl-sync's encoder"
```

---

### Task 4: SpikeGLX emission

**Files:**
- Create: `wl_preproc/synth/spikeglx.py`
- Modify: `pyproject.toml` — add `numpy>=1.26` to dependencies, `spikeinterface>=0.101` to `dev`
- Test: `tests/synth/test_spikeglx.py`

**Interfaces:**
- Consumes: `SessionRecipe`, `GroundTruth`; `encode` from `wl_sync.barcode`
- Produces: `write_spikeglx(dir_path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0) -> Path` returning the `.bin` path; `SPIKE_TEMPLATE_UV`; `AP_GAIN`; `SPIKEGLX_PRE_ROLL_S = 0.7`

**SpikeInterface is the format oracle here.** The acceptance test is behavioural — SpikeInterface opens the output and the data matches — rather than a guess at which `.meta` fields matter. Expect Step 3 to iterate against the reader's actual complaints; that is the test doing its job, not a plan defect.

- [ ] **Step 1: Write the failing test**

```python
# tests/synth/test_spikeglx.py
import numpy as np
import pytest

from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.spikeglx import write_spikeglx
from wl_preproc.synth.timeline import build_timeline

spikeinterface = pytest.importorskip("spikeinterface.extractors")


def emit(tmp_path):
    truth = build_timeline(CI_RECIPE)
    directory = tmp_path / "spikeglx"
    directory.mkdir()
    return truth, write_spikeglx(directory, CI_RECIPE, truth), directory


def test_bin_and_meta_are_written(tmp_path):
    _, bin_path, directory = emit(tmp_path)
    assert bin_path.exists()
    assert bin_path.with_suffix(".meta").exists()


def test_bin_size_matches_the_declared_shape(tmp_path):
    _, bin_path, _ = emit(tmp_path)
    from wl_preproc.synth.spikeglx import SPIKEGLX_PRE_ROLL_S

    expected_samples = int(
        (CI_RECIPE.duration_s + SPIKEGLX_PRE_ROLL_S) * CI_RECIPE.ap_sample_rate_hz
    )
    # int16, n_ap_channels plus one SY (sync) channel
    expected_bytes = expected_samples * (CI_RECIPE.n_ap_channels + 1) * 2
    assert bin_path.stat().st_size == expected_bytes


def test_spikeinterface_can_open_it(tmp_path):
    """The real acceptance criterion: the file is format-correct if the reader
    the pipeline will use can read it."""
    _, _, directory = emit(tmp_path)
    recording = spikeinterface.read_spikeglx(directory, stream_id="imec0.ap")
    assert recording.get_num_channels() == CI_RECIPE.n_ap_channels
    assert recording.get_sampling_frequency() == pytest.approx(CI_RECIPE.ap_sample_rate_hz)
    from wl_preproc.synth.spikeglx import SPIKEGLX_PRE_ROLL_S

    assert recording.get_total_duration() == pytest.approx(
        CI_RECIPE.duration_s + SPIKEGLX_PRE_ROLL_S, rel=1e-3
    )


def test_spikeglx_pre_roll_differs_from_the_sync_box(tmp_path):
    """Different tick origins per system is the point: identical ones would let
    a pipeline that never computes an offset pass every alignment test."""
    from wl_preproc.synth.spikeglx import SPIKEGLX_PRE_ROLL_S
    from wl_preproc.synth.syncbox import SYNCBOX_PRE_ROLL_S

    assert SPIKEGLX_PRE_ROLL_S != SYNCBOX_PRE_ROLL_S


def test_sync_channel_carries_decodable_barcodes(tmp_path):
    """The SY channel is how the pipeline aligns this stream to session time."""
    from wl_sync.barcode import decode_edges, edges_from_samples

    truth, bin_path, _ = emit(tmp_path)
    n_channels = CI_RECIPE.n_ap_channels + 1
    data = np.fromfile(bin_path, dtype=np.int16).reshape(-1, n_channels)
    sync = (data[:, -1] != 0).astype(np.int8)
    decoded = decode_edges(edges_from_samples(sync, CI_RECIPE.ap_sample_rate_hz))
    assert [b.value for b in decoded] == [v for v, _ in truth.barcodes]


def test_planted_spikes_are_present_where_promised(tmp_path):
    """A large deflection must exist near each planted spike, or the fixture is
    not testing what it claims to."""
    truth, bin_path, _ = emit(tmp_path)
    n_channels = CI_RECIPE.n_ap_channels + 1
    data = np.fromfile(bin_path, dtype=np.int16).reshape(-1, n_channels)
    time_s, channel = truth.spikes[0]
    sample = int(time_s * CI_RECIPE.ap_sample_rate_hz)
    window = data[sample : sample + 30, channel]
    baseline = np.std(data[:, channel])
    assert np.abs(window).max() > 3 * baseline


def test_emission_is_deterministic(tmp_path):
    truth = build_timeline(CI_RECIPE)
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    a = write_spikeglx(first, CI_RECIPE, truth)
    b = write_spikeglx(second, CI_RECIPE, truth)
    assert a.read_bytes() == b.read_bytes()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/synth/test_spikeglx.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.synth.spikeglx'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/synth/spikeglx.py
"""Emit a SpikeGLX imec AP stream: interleaved int16 .bin plus a .meta sidecar.

Channel layout is n_ap_channels of neural data followed by one SY channel
carrying the barcode, which is how the pipeline aligns this stream to session
time (spec section 4.5).

Field names in the .meta are what SpikeInterface's reader requires. Where this
disagrees with the reader, the reader wins — it is the thing the pipeline will
actually use.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from wl_sync.barcode import encode

from wl_preproc.synth.recipe import SessionRecipe
from wl_preproc.synth.timeline import apply_drift
from wl_preproc.synth.truth import GroundTruth

SPIKE_TEMPLATE_UV = np.array(
    [0, -10, -40, -120, -200, -140, -40, 30, 60, 45, 25, 10, 0], dtype=np.float64
)
AP_GAIN = 500.0
NOISE_UV = 8.0

# A different tick origin from the sync box, deliberately — see syncbox.py.
SPIKEGLX_PRE_ROLL_S = 0.7
UV_PER_BIT = 2.34375  # Neuropixels 1.0 at gain 500


def _meta_text(recipe: SessionRecipe, n_samples: int, n_channels: int) -> str:
    file_bytes = n_samples * n_channels * 2
    lines = [
        "typeThis=imec",
        f"imSampRate={recipe.ap_sample_rate_hz:g}",
        f"nSavedChans={n_channels}",
        f"fileSizeBytes={file_bytes}",
        f"fileTimeSecs={n_samples / recipe.ap_sample_rate_hz:.6f}",
        f"acqApLfSy={recipe.n_ap_channels},0,1",
        f"snsApLfSy={recipe.n_ap_channels},0,1",
        "imAiRangeMax=0.6",
        "imAiRangeMin=-0.6",
        "imMaxInt=512",
        f"~imroTbl=(0,{recipe.n_ap_channels})"
        + "".join(f"({c} 0 0 {int(AP_GAIN)} 250 1)" for c in range(recipe.n_ap_channels)),
        "~snsChanMap=("
        + f"{recipe.n_ap_channels},0,1)"
        + "".join(f"(AP{c};{c}:{c})" for c in range(recipe.n_ap_channels))
        + "(SY0;768:768)",
        "~snsGeomMap=(NP1000,1,0,70)"
        + "".join(f"(0:{16 if c % 2 else 48}:{20 * (c // 2)}:1)" for c in range(recipe.n_ap_channels)),
    ]
    return "\n".join(lines) + "\n"


def write_spikeglx(
    dir_path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0
) -> Path:
    rng = np.random.default_rng(recipe.seed + 1)
    fs = recipe.ap_sample_rate_hz
    n_samples = int((recipe.duration_s + SPIKEGLX_PRE_ROLL_S) * fs)
    n_channels = recipe.n_ap_channels + 1

    data = rng.normal(0.0, NOISE_UV / UV_PER_BIT, (n_samples, n_channels))

    template = SPIKE_TEMPLATE_UV / UV_PER_BIT
    for time_s, channel in truth.spikes:
        start = int((apply_drift(time_s, drift_ppm) + SPIKEGLX_PRE_ROLL_S) * fs)
        stop = start + template.size
        if stop < n_samples:
            data[start:stop, channel] += template

    sync = np.zeros(n_samples, dtype=np.int16)
    for value, start_s in truth.barcodes:
        cursor = int((apply_drift(start_s, drift_ppm) + SPIKEGLX_PRE_ROLL_S) * fs)
        for level, duration_us in encode(value):
            width = int(round(duration_us * 1e-6 * fs))
            if cursor + width <= n_samples:
                sync[cursor : cursor + width] = level
            cursor += width
    data[:, -1] = sync

    bin_path = dir_path / f"{recipe.session_id}_imec0.ap.bin"
    data.astype(np.int16).tofile(bin_path)
    bin_path.with_suffix(".meta").write_text(
        _meta_text(recipe, n_samples, n_channels), encoding="utf-8"
    )
    return bin_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pip install -e ".[dev]" && pytest tests/synth/test_spikeglx.py -v`
Expected: PASS, 6 passed. If `read_spikeglx` rejects the directory, read its error and correct `_meta_text` — the reader is the specification.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/synth/spikeglx.py tests/synth/test_spikeglx.py pyproject.toml
git commit -m "feat(synth): SpikeGLX emission, verified by SpikeInterface"
```

---

### Task 5: Peripheral emission

**Files:**
- Create: `wl_preproc/synth/peripherals.py`
- Test: `tests/synth/test_peripherals.py`

**Interfaces:**
- Consumes: `SessionRecipe`, `GroundTruth`; `SessionManifest`, `StartedAtSource`, `SCHEMA_VERSION` from `wl_preproc.contracts.manifest`; `BehaviorCameraSidecar` from `wl_preproc.contracts.sidecar`
- Produces: `write_manifest(path, recipe) -> None`; `write_camera_sidecar(path, recipe, dropped=()) -> None`; `write_task_file(path, truth) -> None`; `CAMERA_FPS = 200.0`

- [ ] **Step 1: Write the failing test**

```python
# tests/synth/test_peripherals.py
import json

import pytest
from pydantic import ValidationError

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.sidecar import BehaviorCameraSidecar
from wl_preproc.synth.peripherals import (
    CAMERA_FPS,
    write_camera_sidecar,
    write_manifest,
    write_task_file,
)
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.timeline import build_timeline


def test_manifest_validates_against_the_real_contract(tmp_path):
    """The generator must not be able to emit something the pipeline rejects.
    If it can, one of the two is wrong."""
    path = tmp_path / "session_manifest.yaml"
    write_manifest(path, CI_RECIPE)
    manifest = SessionManifest.from_yaml(path.read_text())
    assert manifest.session_id == CI_RECIPE.session_id
    assert manifest.expected_systems == list(CI_RECIPE.systems)


def test_sidecar_validates_against_the_real_contract(tmp_path):
    path = tmp_path / "frames.yaml"
    write_camera_sidecar(path, CI_RECIPE)
    sidecar = BehaviorCameraSidecar.from_yaml(path.read_text())
    assert sidecar.trigger_source == "syncbox"
    assert sidecar.frame_count == int(CI_RECIPE.duration_s * CAMERA_FPS)


def test_sidecar_records_dropped_frames(tmp_path):
    path = tmp_path / "frames.yaml"
    write_camera_sidecar(path, CI_RECIPE, dropped=(17, 402))
    assert BehaviorCameraSidecar.from_yaml(path.read_text()).dropped_frame_ids == [17, 402]


def test_task_file_lists_every_trial(tmp_path):
    truth = build_timeline(CI_RECIPE)
    path = tmp_path / "task.json"
    write_task_file(path, truth)
    payload = json.loads(path.read_text())
    assert len(payload["trials"]) == len(truth.trials)
    assert payload["trials"][0]["trial_id"] == truth.trials[0].trial_id


def test_task_file_carries_parameters_the_codes_do_not(tmp_path):
    """Codes own timing, the task file owns parameters — spec section 4.2. The
    fixture has to actually exercise that split."""
    truth = build_timeline(CI_RECIPE)
    path = tmp_path / "task.json"
    write_task_file(path, truth)
    trial = json.loads(path.read_text())["trials"][0]
    assert "condition" in trial
    assert "reward_ms" in trial
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/synth/test_peripherals.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.synth.peripherals'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/synth/peripherals.py
"""Manifest, camera sidecar and task file.

Each is written through its real contract model, so the generator cannot emit
something the pipeline would reject — if it could, the fixture would be testing
a format nothing else speaks.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import Sequence
from pathlib import Path

import yaml

from wl_preproc.contracts.manifest import SCHEMA_VERSION, SessionManifest, StartedAtSource
from wl_preproc.contracts.sidecar import BehaviorCameraSidecar, VideoFile
from wl_preproc.synth.recipe import SessionRecipe
from wl_preproc.synth.truth import GroundTruth

CAMERA_FPS = 200.0
_EPOCH = datetime.datetime(2027, 3, 14, 9, 0, tzinfo=datetime.timezone.utc)


def write_manifest(path: Path, recipe: SessionRecipe) -> None:
    manifest = SessionManifest(
        schema_version=SCHEMA_VERSION,
        session_id=recipe.session_id,
        subject=recipe.subject,
        rig=recipe.rig,
        started_at=_EPOCH,
        started_at_source=StartedAtSource.BEHAVIORAL_CONTROL,
        expected_systems=list(recipe.systems),
        acquisition_build_id=f"blake3:synth{recipe.seed:08x}",
        stimulus_calibration_id="SYNTH-MONITOR@2027-01-01",
        notes="synthetic session",
    )
    path.write_text(manifest.to_yaml(), encoding="utf-8")


def write_camera_sidecar(
    path: Path, recipe: SessionRecipe, dropped: Sequence[int] = ()
) -> None:
    frame_count = int(recipe.duration_s * CAMERA_FPS)
    sidecar = BehaviorCameraSidecar(
        schema_version=1,
        system="bcam0",
        trigger_source="syncbox",
        frame_count=frame_count,
        dropped_frame_ids=list(dropped),
        video_files=[
            VideoFile(
                path="bcam0_seg000.mp4",
                first_frame_index=0,
                last_frame_index=frame_count - 1,
                codec="h264",
                checksum=f"blake3:synth{recipe.seed:08x}",
            )
        ],
    )
    path.write_text(
        yaml.safe_dump(sidecar.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )


def write_task_file(path: Path, truth: GroundTruth) -> None:
    """Stands in for MonkeyLogic's .bhv2 until the task stack is chosen.

    Carries what the code stream deliberately does not: condition numbers and
    reward volumes. The pipeline joins the two and hard-fails on a trial-count
    mismatch, so the fixture must exercise both halves.
    """
    payload = {
        "format": "synthetic-task-file",
        "version": 1,
        "trials": [
            {
                "trial_id": trial.trial_id,
                "block_id": trial.block_id,
                "start_s": trial.start_s,
                "end_s": trial.end_s,
                "condition": (trial.trial_id % 4) + 1,
                "reward_ms": 120,
                "outcome": "correct",
            }
            for trial in truth.trials
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/synth/test_peripherals.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/synth/peripherals.py tests/synth/test_peripherals.py
git commit -m "feat(synth): manifest, camera sidecar and task file through their contracts"
```

---

### Task 6: Fault injection

**Files:**
- Create: `wl_preproc/synth/faults.py`
- Test: `tests/synth/test_faults.py`

**Interfaces:**
- Consumes: `Fault`, `SessionRecipe`; `GroundTruth`; `Record`, `Edge`, `CodeWord` from `wl_sync.log`
- Produces: `drop_barcodes(records, every) -> list[Record]`; `truncate_file(path, keep_fraction) -> None`; `split_into_segments(records, restart_at_s, gap_s) -> list[list[Record]]`; `stop_mid_trial(records, at_s) -> list[Record]`; `drop_camera_frames(frame_count, rng) -> tuple[int, ...]`; `corrupt_trial_count(truth) -> GroundTruth`

- [ ] **Step 1: Write the failing test**

```python
# tests/synth/test_faults.py
import numpy as np
import pytest
from wl_sync.log import CodeWord, Edge

from wl_preproc.synth.faults import (
    corrupt_trial_count,
    drop_barcodes,
    drop_camera_frames,
    split_into_segments,
    stop_mid_trial,
    truncate_file,
)
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.timeline import build_timeline


def some_records():
    return [Edge(tick_us=i * 1_000_000, gpio=17, level=i % 2) for i in range(20)]


def test_drop_barcodes_removes_every_nth():
    kept = drop_barcodes(some_records(), every=3)
    assert len(kept) < len(some_records())
    assert all(isinstance(r, Edge) for r in kept)


def test_drop_barcodes_leaves_code_words_alone():
    records = some_records() + [CodeWord(tick_us=500, word=0x8001)]
    kept = drop_barcodes(records, every=2)
    assert any(isinstance(r, CodeWord) for r in kept)


def test_split_produces_segments_with_a_real_gap():
    segments = split_into_segments(some_records(), restart_at_s=10.0, gap_s=5.0)
    assert len(segments) == 2
    first_end = max(r.tick_us for r in segments[0])
    second_start = min(r.tick_us for r in segments[1])
    assert (second_start - first_end) / 1e6 >= 5.0


def test_stop_mid_trial_truncates_the_record_stream():
    records = some_records()
    stopped = stop_mid_trial(records, at_s=5.0)
    assert max(r.tick_us for r in stopped) <= 5_000_000
    assert len(stopped) < len(records)


def test_truncate_file_shortens_but_does_not_empty(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"x" * 1000)
    truncate_file(path, keep_fraction=0.5)
    assert path.stat().st_size == 500


def test_dropped_camera_frames_are_unique_and_in_range():
    dropped = drop_camera_frames(1000, np.random.default_rng(0))
    assert len(set(dropped)) == len(dropped)
    assert all(0 <= frame < 1000 for frame in dropped)


def test_corrupt_trial_count_removes_exactly_one_trial():
    """The pipeline hard-fails when codes and the task file disagree on trial
    count. This makes them disagree by one, which is the subtle case."""
    truth = build_timeline(CI_RECIPE)
    corrupted = corrupt_trial_count(truth)
    assert len(corrupted.trials) == len(truth.trials) - 1
    assert corrupted.code_words == truth.code_words


def test_every_fault_has_an_implementation():
    """A Fault enum member with no function is a fixture that silently does
    nothing, which is worse than no fixture."""
    import wl_preproc.synth.faults as faults
    from wl_preproc.synth.recipe import Fault

    unimplemented = [
        fault for fault in Fault
        if fault not in {Fault.CLOCK_DRIFT, Fault.MISSING_DEVICE}
        and not hasattr(faults, faults.FAULT_FUNCTIONS.get(fault, ""))
    ]
    assert unimplemented == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/synth/test_faults.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.synth.faults'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/synth/faults.py
"""Deliberate pathology.

Each function corresponds to a real failure the pipeline must survive. They act
on emitted output rather than on the timeline, so ground truth stays the answer
the pipeline should recover — never the corrupted version it was handed.

CLOCK_DRIFT and MISSING_DEVICE are applied at emission rather than here: drift
is a parameter threaded through the writers, and a missing device is a system
absent from the recipe. FAULT_FUNCTIONS records that, so a reader can tell a
deliberate omission from a forgotten one.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from wl_sync.log import Edge, Record

from wl_preproc.synth.recipe import Fault
from wl_preproc.synth.truth import GroundTruth

FAULT_FUNCTIONS: dict[Fault, str] = {
    Fault.DROPPED_BARCODES: "drop_barcodes",
    Fault.SHORT_SEGMENT: "split_into_segments",
    Fault.MID_SESSION_RESTART: "split_into_segments",
    Fault.STOP_MID_TRIAL: "stop_mid_trial",
    Fault.DROPPED_CAMERA_FRAMES: "drop_camera_frames",
    Fault.TRIAL_COUNT_MISMATCH: "corrupt_trial_count",
    Fault.TRUNCATED_FILE: "truncate_file",
}


def drop_barcodes(records: Sequence[Record], every: int) -> list[Record]:
    """Remove every nth barcode edge, leaving code words untouched."""
    kept: list[Record] = []
    seen = 0
    for record in records:
        if isinstance(record, Edge):
            seen += 1
            if seen % every == 0:
                continue
        kept.append(record)
    return kept


def split_into_segments(
    records: Sequence[Record], restart_at_s: float, gap_s: float
) -> list[list[Record]]:
    """Split into two recording segments separated by a real gap."""
    cut = int(restart_at_s * 1e6)
    shift = int(gap_s * 1e6)
    first = [r for r in records if r.tick_us <= cut]
    second = [
        dataclasses.replace(r, tick_us=r.tick_us + shift)
        for r in records
        if r.tick_us > cut
    ]
    return [first, second]


def stop_mid_trial(records: Sequence[Record], at_s: float) -> list[Record]:
    """Cut the stream partway through a trial, leaving that trial incomplete."""
    cut = int(at_s * 1e6)
    return [r for r in records if r.tick_us <= cut]


def drop_camera_frames(frame_count: int, rng: np.random.Generator) -> tuple[int, ...]:
    n_dropped = max(1, frame_count // 200)
    return tuple(sorted(rng.choice(frame_count, n_dropped, replace=False).tolist()))


def corrupt_trial_count(truth: GroundTruth) -> GroundTruth:
    """Remove one trial from the task-file view while leaving the code stream
    intact, so the two disagree by exactly one."""
    return dataclasses.replace(truth, trials=truth.trials[:-1])


def truncate_file(path: Path, keep_fraction: float) -> None:
    size = path.stat().st_size
    with path.open("r+b") as handle:
        handle.truncate(int(size * keep_fraction))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/synth/test_faults.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/synth/faults.py tests/synth/test_faults.py
git commit -m "feat(synth): fault injection for every pathology in the spec table"
```

---

### Task 7: Session assembly and CLI

**Files:**
- Create: `wl_preproc/synth/session.py`
- Modify: `wl_preproc/cli/main.py` — add the `synth generate` subcommand
- Test: `tests/synth/test_session.py`, `tests/cli/test_synth_cli.py`

**Interfaces:**
- Consumes: everything above; `SessionLayout` from `wl_preproc.contracts.paths`
- Produces: `generate_session(root: Path, recipe: SessionRecipe) -> GroundTruth`

- [ ] **Step 1: Write the failing test**

```python
# tests/synth/test_session.py
import pytest
from wl_sync.session import SessionId

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.synth.recipe import CI_RECIPE, Fault
from wl_preproc.synth.session import generate_session


def test_produces_a_complete_session_directory(tmp_path):
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, SessionId.parse(CI_RECIPE.session_id))
    assert layout.manifest_path.exists()
    for system in CI_RECIPE.systems:
        assert layout.system_dir(system).is_dir()
        assert layout.done_marker(system).exists()


def test_manifest_matches_the_directories_present(tmp_path):
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, SessionId.parse(CI_RECIPE.session_id))
    manifest = SessionManifest.from_yaml(layout.manifest_path.read_text())
    assert set(manifest.expected_systems) == set(CI_RECIPE.systems)


def test_returns_ground_truth_covering_the_session(tmp_path):
    truth = generate_session(tmp_path, CI_RECIPE)
    assert len(truth.trials) == sum(b.n_trials for b in CI_RECIPE.blocks)
    assert len(truth.blocks) == len(CI_RECIPE.blocks)
    assert truth.barcodes


def test_missing_device_omits_its_directory_and_marker(tmp_path):
    recipe = CI_RECIPE.model_copy(
        update={"systems": ("syncbox", "bcam"), "faults": (Fault.MISSING_DEVICE,)}
    )
    generate_session(tmp_path, recipe)
    layout = SessionLayout(tmp_path, SessionId.parse(recipe.session_id))
    assert not layout.system_dir("spikeglx").exists()


def test_truncated_file_fault_shortens_the_binary(tmp_path):
    clean = tmp_path / "clean"
    broken = tmp_path / "broken"
    clean.mkdir()
    broken.mkdir()
    generate_session(clean, CI_RECIPE)
    generate_session(broken, CI_RECIPE.model_copy(update={"faults": (Fault.TRUNCATED_FILE,)}))
    good = next((clean / CI_RECIPE.session_id / "spikeglx").glob("*.bin"))
    bad = next((broken / CI_RECIPE.session_id / "spikeglx").glob("*.bin"))
    assert bad.stat().st_size < good.stat().st_size


def test_generation_is_byte_identical_for_one_seed(tmp_path):
    """Determinism is a test, not an aspiration — a fixture that changes between
    runs makes every downstream failure ambiguous."""
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    generate_session(first, CI_RECIPE)
    generate_session(second, CI_RECIPE)
    a = next((first / CI_RECIPE.session_id / "spikeglx").glob("*.bin"))
    b = next((second / CI_RECIPE.session_id / "spikeglx").glob("*.bin"))
    assert a.read_bytes() == b.read_bytes()
```

```python
# tests/cli/test_synth_cli.py
import subprocess
import sys


def test_cli_generates_a_session(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "wl_preproc.cli.main", "synth", "generate",
         "--out", str(tmp_path), "--profile", "ci"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "2027-03-14_01" / "session_manifest.yaml").exists()


def test_cli_rejects_an_unknown_profile(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "wl_preproc.cli.main", "synth", "generate",
         "--out", str(tmp_path), "--profile", "nonsense"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/synth/test_session.py tests/cli/test_synth_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.synth.session'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/synth/session.py
"""Assemble a complete session directory and return what was planted."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from wl_sync.session import SessionId

from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.synth.faults import (
    drop_camera_frames,
    truncate_file,
)
from wl_preproc.synth.peripherals import write_camera_sidecar, write_manifest, write_task_file
from wl_preproc.synth.recipe import Fault, SessionRecipe
from wl_preproc.synth.spikeglx import write_spikeglx
from wl_preproc.synth.syncbox import write_syncbox_log
from wl_preproc.synth.timeline import build_timeline
from wl_preproc.synth.truth import GroundTruth


def generate_session(root: Path, recipe: SessionRecipe) -> GroundTruth:
    truth = build_timeline(recipe)
    layout = SessionLayout(root, SessionId.parse(recipe.session_id))
    layout.dir.mkdir(parents=True, exist_ok=True)
    write_manifest(layout.manifest_path, recipe)

    rng = np.random.default_rng(recipe.seed + 2)

    for system in recipe.systems:
        directory = layout.system_dir(system)
        directory.mkdir(exist_ok=True)

        if system == "syncbox":
            write_syncbox_log(
                directory / "syncbox.log", recipe, truth, drift_ppm=recipe.drift_ppm
            )
            write_task_file(directory / "task.json", truth)
        elif system == "spikeglx":
            bin_path = write_spikeglx(directory, recipe, truth, drift_ppm=recipe.drift_ppm)
            if Fault.TRUNCATED_FILE in recipe.faults:
                truncate_file(bin_path, keep_fraction=0.6)
        elif system == "bcam":
            dropped = (
                drop_camera_frames(int(recipe.duration_s * 200.0), rng)
                if Fault.DROPPED_CAMERA_FRAMES in recipe.faults
                else ()
            )
            write_camera_sidecar(directory / "frames.yaml", recipe, dropped=dropped)

        layout.done_marker(system).write_text("", encoding="utf-8")

    return truth
```

Then extend `wl_preproc/cli/main.py`'s `main()` — add before the final `return 2`:

```python
    synth = subparsers.add_parser("synth", help="synthetic session tools")
    synth_sub = synth.add_subparsers(dest="action", required=True)
    generate = synth_sub.add_parser("generate", help="write a synthetic session")
    generate.add_argument("--out", required=True, type=Path)
    generate.add_argument("--profile", choices=["ci", "benchmark"], default="ci")
```

and the dispatch branch:

```python
    if args.group == "synth" and args.action == "generate":
        from wl_preproc.synth.recipe import BENCHMARK_RECIPE, CI_RECIPE
        from wl_preproc.synth.session import generate_session

        recipe = CI_RECIPE if args.profile == "ci" else BENCHMARK_RECIPE
        args.out.mkdir(parents=True, exist_ok=True)
        truth = generate_session(args.out, recipe)
        print(f"{args.out / recipe.session_id}: {len(truth.trials)} trials")
        return 0
```

- [ ] **Step 4: Run the whole suite**

Run: `pytest -v`
Expected: PASS, all synth tests plus the 55 existing

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/synth/session.py wl_preproc/cli/main.py tests/synth tests/cli
git commit -m "feat(synth): session assembly and wlpp synth generate"
```

---

## Definition of done

- `pytest` green, including the SpikeInterface round-trip
- `wlpp synth generate --profile ci` produces a directory the Phase 0 contracts all validate
- `wlpp synth generate --profile benchmark` produces a realistic 384-channel hour for the P6000 benchmark
- Every `Fault` member has an implementation or a recorded reason it is applied elsewhere

## Follow-on work this unblocks

- **Phase 1b** — Intan `.rhs` emission and stim artifacts, once spec open item 3 confirms the stim-flag layout
- **Phase 1c** — DataJoint schemas, ingest watcher, timebase fitting, coverage, responder: all testable against these fixtures
- **The P6000 benchmark** (spec §6.6) — the benchmark profile is what turns "is it fast enough" into a number

## Spec corrections this plan implies

- **§3.4's repo layout puts the generator under `tests/synth/`.** It is shipped code — `wlpp synth generate` produces the benchmark session, which is not a test — so it lives in `wl_preproc/synth/`. Correct §3.4 when this lands.
