# Phase 1c-4 — Timebase and Block Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert each acquisition system's recording files into session time — decoding barcodes, fitting rate per system and offset per segment — and intersect the result with block intervals to produce per-block coverage.

**Architecture:** All five systems carry the same barcode, so the only per-system code is one extraction function returning `(edges, fs_hz)`; decode, fit, residual, rejection and tier are shared. Rate is fitted once per `(system, session)` pooling every barcode; offset is fitted per `(system, segment)` with that rate held fixed. Results land in DataJoint **Computed** tables — this project's first — populated by the existing daemon.

**Tech Stack:** Python ≥3.11, DataJoint 2.3.2, `wl_sync.barcode` (the codec, owned by wl-sync), numpy, pydantic. Tests: pytest against a real MySQL under Docker, with the synthetic generator as ground-truth oracle.

**Spec:** `docs/superpowers/specs/2026-08-16-phase-1c4-timebase-design.md`

## Global Constraints

- Python `>=3.11`; CI runs 3.11 **and** 3.13.
- The **five git dependency pins** in `pyproject.toml` do not move.
- **No new runtime dependency.** numpy and pydantic are already present; nothing else gets added.
- **Never a bare `longblob`.** Any array-valued attribute is declared `<blob>`. A bare `longblob` under DataJoint 2.x stores a numpy array as its truncated string repr and nothing raises.
- **No bare `.delete()`**; `.fetch()` is deprecated — use `.to_dicts()`.
- **One schema prefix per process.**
- **Zero warnings.** Suite floor is 593 passing at the start of this phase.
- **Test subjects ≤8 characters** (`element-animal` declares `subject : varchar(8)`).
- `wl-sync` owns the barcode codec. **Consume `wl_sync.barcode`; never reimplement `decode_edges` or restate its constants.**
- Nothing under `wl_preproc/` may open an outbound connection — enforced by the AST guardrail in `tests/test_cli_guardrails.py`.
- **`in_transaction` is not a read-only check.** To prove a function writes only what it should, snapshot rows using `table_snapshot` / `deep_equal` from `tests/conftest.py`.
- Run the suite as `.venv/bin/python -m pytest` from the repo root.

## Interfaces that already exist

```python
# wl_sync.barcode  — the codec. Consume, never reimplement.
BIT_SLOT_US: int = 5000
IDLE_MIN_US: int = 400000
INTERVAL_US: int = 1000000
@dataclass
class Barcode:
    value: int
    start_us: int
def decode_edges(edges: Sequence[tuple[int, int]], start_us: int | None = None) -> list[Barcode]
def edges_from_samples(trace: Sequence[int], fs_hz: float, t0_us: int = 0) -> list[tuple[int, int]]
def encode(value: int) -> list[tuple[int, int]]

# wl_preproc.synth.truth
GroundTruth.barcodes: tuple[tuple[int, float], ...]   # (value, session-time SECONDS)

# wl_preproc.synth emitters
write_syncbox_log(path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0) -> None
write_spikeglx(dir_path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0) -> Path
write_rhs(dir_path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0) -> Path
write_camera_sidecar(path: Path, recipe: SessionRecipe, dropped: Sequence[int] = ()) -> None
wl_preproc.synth.peripherals.CAMERA_FPS: float = 200.0

# wl_preproc.schema.core  (tables declared in 1c-1, no rows anywhere)
AcquisitionSystem   # -> pipeline.Session, system (enum of contracts.paths.SYSTEMS)
Segment             # -> AcquisitionSystem, segment_barcode; start_s, end_s, n_samples
RejectedSegment     # -> AcquisitionSystem, file_path; reason
Block               # -> pipeline.Session, block_id; task_type, start_s, end_s, works_block_id
# wl_preproc.schema.coverage
BlockCoverage       # -> core.Block, core.AcquisitionSystem; coverage, covered_s
TrialCoverage       # -> pipeline.trial.Trial, core.AcquisitionSystem; coverage, covered_s
# wl_preproc.daemon
def _computed_tables() -> list          # returns [] today
def count_stale_jobs(...)               # reads DataJoint's internal ~jobs tables
```

---

## File Structure

**Create:**
- `wl_preproc/timebase/__init__.py` — package marker, exports nothing.
- `wl_preproc/timebase/extract.py` — `BitStream`, `min_sample_rate_hz()`, and one extractor per system. The **only** per-system code in the phase.
- `wl_preproc/timebase/fit.py` — `fit_rate()`, `fit_offset()`, and their result types. Pure numeric, no I/O, no DataJoint.
- `wl_preproc/timebase/segments.py` — §4.1's alignment rules: which files become `Segment` and which become `RejectedSegment`.
- `wl_preproc/timebase/coverage.py` — interval intersection producing `full`/`partial`/`absent` + `covered_s`.
- `wl_preproc/schema/timebase.py` — `SystemTimebase` and `TimingProvenance` Computed tables.
- `wl_preproc/synth/ohdpi.py` — the ohdpi emitter, which has no fixture today.
- `tests/timebase/test_extract.py`, `test_fit.py`, `test_segments.py`, `test_coverage.py`
- `tests/schema/test_timebase.py`
- `tests/synth/test_ohdpi.py`

**Modify:**
- `wl_preproc/schema/core.py` — `Segment` becomes `dj.Computed` and gains fields; `Block`'s comment is corrected.
- `wl_preproc/schema/coverage.py` — both tables become `dj.Computed`.
- `wl_preproc/synth/peripherals.py` — `CAMERA_FPS`, and the camera's digital line.
- `wl_preproc/contracts/sidecar.py` — the proposed digital-line field, **optional**.
- `wl_preproc/synth/recipe.py`, `wl_preproc/synth/session.py` — the ohdpi profile and dispatch.
- `wl_preproc/daemon.py` — `_computed_tables()` and the `~jobs` snapshot gap.

---

## Task 1: The extraction contract, the Nyquist floor, and syncbox

**Files:**
- Create: `wl_preproc/timebase/__init__.py`, `wl_preproc/timebase/extract.py`
- Test: `tests/timebase/test_extract.py`

**Interfaces:**
- Consumes: `wl_sync.barcode.{BIT_SLOT_US, edges_from_samples, decode_edges}`; `wl_sync.log` for the syncbox reader.
- Produces:
  ```python
  @dataclass(frozen=True)
  class BitStream:
      edges: tuple[tuple[int, int], ...]   # (timestamp_us, level) in NATIVE device time
      fs_hz: float                          # sampling rate that produced them
      n_samples: int
  def min_sample_rate_hz() -> float
  def extract_syncbox(path: Path) -> BitStream
  ```

- [x] **Step 1: Write the failing tests**

```python
# tests/timebase/test_extract.py
import pytest
from wl_sync.barcode import BIT_SLOT_US
from wl_preproc.timebase.extract import BitStream, min_sample_rate_hz


def test_min_sample_rate_is_derived_from_the_bit_slot_not_written_down():
    """Two samples per bit slot is the Nyquist floor for decoding a sampled
    digital line. Deriving it means a change to `BIT_SLOT_US` in wl-sync moves
    this number rather than silently invalidating it.

    The literal 400.0 appears here ONLY as the value the derivation must
    currently produce. If wl-sync changes the bit slot, this assertion is the
    thing that should fail, and it should fail loudly enough to send someone
    to every system's assumed rate.
    """
    assert min_sample_rate_hz() == 2.0 / (BIT_SLOT_US / 1_000_000.0)
    assert min_sample_rate_hz() == 400.0


def test_bitstream_rejects_a_sample_rate_below_the_floor():
    """A system sampled below the floor cannot decode a barcode at all, so
    constructing a BitStream that claims to is a programming error, not a
    data condition. It fails at construction rather than producing an empty
    decode that reads like "this file had no barcodes"."""
    with pytest.raises(ValueError, match="below the 400.0 Hz floor"):
        BitStream(edges=(), fs_hz=200.0, n_samples=0)


def test_bitstream_accepts_the_floor_exactly():
    """400 Hz is the boundary, and the boundary is inclusive: 2.0 samples per
    bit is decodable in principle. The spec records separately that it is not a
    comfortable operating point."""
    stream = BitStream(edges=(), fs_hz=400.0, n_samples=0)
    assert stream.fs_hz == 400.0
```

- [x] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/timebase/test_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.timebase'`

- [x] **Step 3: Implement `BitStream` and the floor**

```python
# wl_preproc/timebase/extract.py
"""Per-system extraction of a barcode bit stream.

This module is the ONLY per-system code in Phase 1c-4. Everything downstream —
decode, rate fit, offset fit, residual, rejection, tier — is shared across all
five systems, because all five carry the same barcode (design spec section 2).

A sixth system costs one function here and one synthetic emitter. It touches no
table and no fit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wl_sync.barcode import BIT_SLOT_US


def min_sample_rate_hz() -> float:
    """The lowest sampling rate that can decode a barcode, derived rather than
    written down.

    Decoding needs at least two samples per bit slot. `BIT_SLOT_US` is
    wl-sync's, so deriving from it means a change there fails a test here
    instead of silently invalidating every camera system's assumed rate. Design
    spec section 3: this project has twice shipped timing arithmetic that no
    code had executed.
    """
    return 2.0 / (BIT_SLOT_US / 1_000_000.0)


@dataclass(frozen=True)
class BitStream:
    """A digital line's edges in the device's OWN time, plus the rate that
    sampled them.

    Native time, not session time: converting to session time is the fit's job
    (`timebase/fit.py`), and keeping the two apart is what makes the transform
    reversible as design spec section 4.5 requires.
    """

    edges: tuple[tuple[int, int], ...]
    fs_hz: float
    n_samples: int

    def __post_init__(self) -> None:
        floor = min_sample_rate_hz()
        if self.fs_hz < floor:
            raise ValueError(
                f"{self.fs_hz} Hz is below the {floor} Hz floor for decoding a "
                f"{BIT_SLOT_US} us bit slot: at least two samples per bit are "
                "needed, so this stream cannot yield a barcode at all"
            )
```

- [x] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/timebase/test_extract.py -v`
Expected: PASS (3 tests)

- [x] **Step 5: Write the failing syncbox test**

```python
# append to tests/timebase/test_extract.py
from pathlib import Path

from wl_preproc.synth.recipe import RECIPES
from wl_preproc.synth.session import generate_session
from wl_preproc.timebase.extract import extract_syncbox


def test_syncbox_extraction_recovers_every_ground_truth_barcode(tmp_path):
    """The sync box is the reference: session time is t=0 at its first barcode
    (spec section 4.5), so its own log needs no decode — the values and times
    are already in it.

    Checked against GroundTruth rather than against a re-decode of the same
    file, because a reader that mis-parses consistently agrees with itself.
    """
    truth = generate_session(tmp_path, RECIPES["ci"])
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    stream = extract_syncbox(session_dir / "syncbox" / "syncbox.log")

    assert stream.n_samples > 0
    assert len(stream.edges) > 0
    # Every ground-truth barcode is present, by value.
    from wl_sync.barcode import decode_edges

    recovered = {b.value for b in decode_edges(list(stream.edges))}
    expected = {value for value, _ in truth.barcodes}
    assert expected - recovered == set(), f"missing barcodes: {expected - recovered}"
```

- [x] **Step 6: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/timebase/test_extract.py -k syncbox -v`
Expected: FAIL — `ImportError: cannot import name 'extract_syncbox'`

- [x] **Step 7: Implement `extract_syncbox`**

Read the log with `wl_sync.log`, and turn its recorded barcode entries into the same `(timestamp_us, level)` edge form every other system produces, so the shared path downstream sees one shape. Use `wl_sync.barcode.encode(value)` to render each barcode's edges at its logged time — the codec owns that rendering, and reproducing it here would be the reimplementation the constraints forbid.

```python
# append to wl_preproc/timebase/extract.py
from wl_sync.barcode import encode
from wl_sync.log import SyncBoxLogHeader  # noqa: F401  (header validation on read)

# The sync box logs at microsecond resolution, so its "sampling rate" is
# nominal: it is not a sampled line at all. 1 MHz is stated rather than
# measured, and is only ever used to satisfy BitStream's floor check.
_SYNCBOX_NOMINAL_FS_HZ = 1_000_000.0


def extract_syncbox(path: Path) -> BitStream:
    """The sync box's own log, rendered into the same edge form every other
    system produces.

    Rendering through `wl_sync.barcode.encode` rather than writing edges by
    hand: the codec owns the frame's shape, and a second copy of it here is the
    reimplementation this phase's constraints forbid.
    """
    from wl_preproc.timebase._syncbox_log import read_barcode_entries

    entries = read_barcode_entries(path)
    edges: list[tuple[int, int]] = []
    for value, t_us in entries:
        edges.extend((t_us + rel_us, level) for rel_us, level in encode(value))
    edges.sort()
    last_us = edges[-1][0] if edges else 0
    return BitStream(
        edges=tuple(edges),
        fs_hz=_SYNCBOX_NOMINAL_FS_HZ,
        n_samples=last_us,
    )
```

Create `wl_preproc/timebase/_syncbox_log.py` with `read_barcode_entries(path: Path) -> list[tuple[int, int]]` returning `(value, timestamp_us)`, parsing the log format `wl_sync.log` defines. Keep it in its own module so the log format's shape has exactly one reader.

- [x] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 593 + 4 passing, **0 warnings**

- [x] **Step 9: Commit**

```bash
git add wl_preproc/timebase tests/timebase
git commit -m "feat(timebase): the extraction contract and the Nyquist floor

Two samples per bit slot is derived from wl_sync's BIT_SLOT_US rather than
written down, so a change there fails a test instead of silently invalidating
every camera system's assumed rate.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: SpikeGLX and RHS extraction

> **Done 2026-08-22, with one correction to the plan.** Step 1's test globbed
> `*.nidq.bin` and matched no file: the Phase 1a generator emitted only an imec
> AP pair and drove the barcode onto the **imec SY channel**, contradicting
> §4.5 ("one NI digital line... the imec SMA stays free") and §12's reason for
> ordering a 32-line card. The fixture was corrected rather than the spec —
> `write_spikeglx` now also emits a `.nidq.bin`/`.nidq.meta` pair carrying the
> barcode, SY is emitted but undriven, and `read_spikeglx(stream_id="nidq")`
> is the acceptance test for it, as `read_intan` was for `info.rhs` in 1b2.
> Recorded as a trap in `docs/CHECKPOINT.md`.
>
> Also beyond the plan's text, because the plan's versions could not fail:
> `extract_rhs` reads its rate from `info.rhs` and `extract_spikeglx` from
> `.nidq.meta`, each proven by a fixture declaring a rate that is **not** 30 kHz
> — every fixture in the repo is 30 kHz, so a hardcoded rate passes any test
> written against the emitted one. Step 5's mutation was run for all three:
> bit+1, a hardcoded rate, and a wrong reshape stride each fail exactly the
> tests that should catch them.


**Files:**
- Modify: `wl_preproc/timebase/extract.py`
- Test: `tests/timebase/test_extract.py`

**Interfaces:**
- Consumes: `BitStream`, `wl_sync.barcode.edges_from_samples`.
- Produces:
  ```python
  def extract_spikeglx(nidq_bin: Path) -> BitStream
  def extract_rhs(session_dir: Path) -> BitStream
  ```

- [x] **Step 1: Write the failing tests**

```python
# append to tests/timebase/test_extract.py
from wl_preproc.timebase.extract import extract_rhs, extract_spikeglx


def test_spikeglx_extraction_recovers_ground_truth_barcodes(tmp_path):
    """SpikeGLX carries the barcode on one NI digital line (spec section 4.5).
    Recovery is checked against GroundTruth, and the ORIGIN is checked too:
    the generator gives SpikeGLX a 0.7 s tick origin distinct from the sync
    box's 1.0 s, so an extractor that ignores the device's own clock still
    recovers the values and lands them at the wrong native time."""
    truth = generate_session(tmp_path, RECIPES["ci"])
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    bin_path = next((session_dir / "spikeglx").glob("*.nidq.bin"))

    stream = extract_spikeglx(bin_path)
    from wl_sync.barcode import decode_edges

    decoded = decode_edges(list(stream.edges))
    recovered = {b.value for b in decoded}
    expected = {value for value, _ in truth.barcodes}
    assert expected - recovered == set()
    # Native time, not session time: the first barcode does NOT sit at t=0.
    assert decoded[0].start_us > 0


def test_rhs_extraction_recovers_ground_truth_barcodes(tmp_path):
    """The standalone-Intan topology: no NI, no SpikeGLX (`--profile stim`).
    Its tick origin is 0.45 s, a third distinct value, so a pipeline that never
    computes an offset fails."""
    truth = generate_session(tmp_path, RECIPES["stim"])
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())

    stream = extract_rhs(session_dir / "rhs")
    from wl_sync.barcode import decode_edges

    recovered = {b.value for b in decode_edges(list(stream.edges))}
    expected = {value for value, _ in truth.barcodes}
    assert expected - recovered == set()
```

- [x] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/timebase/test_extract.py -k "spikeglx or rhs" -v`
Expected: FAIL — `ImportError: cannot import name 'extract_spikeglx'`

- [x] **Step 3: Implement both extractors**

Both read a digital word stream, select the barcode's bit, and hand the resulting 0/1 trace to `edges_from_samples`. Read the generator's own emitters (`wl_preproc/synth/spikeglx.py`, `wl_preproc/synth/rhs.py`) to learn which bit each writes and at what rate — **do not assume; the emitters are the specification of the fixture.**

```python
# append to wl_preproc/timebase/extract.py
import numpy as np

from wl_sync.barcode import edges_from_samples


def _edges_from_bit(words: np.ndarray, bit: int, fs_hz: float) -> tuple[tuple[int, int], ...]:
    """One digital word stream, one bit, into edges.

    `edges_from_samples` is wl-sync's and does the level-change detection; this
    only isolates the line. Note `bit` is ZERO-BASED here. Intan's own
    documentation numbers bits from 1, and reading it literally keys a mask to
    the wrong signal silently — parent spec section 6.3 records that trap.
    """
    trace = ((words >> bit) & 1).astype(np.uint8)
    return tuple(edges_from_samples(trace.tolist(), fs_hz=fs_hz))


def extract_spikeglx(nidq_bin: Path) -> BitStream:
    """The barcode line out of a SpikeGLX `.nidq.bin`.

    The rate comes from the companion `.nidq.meta`, never from a constant here:
    a rate assumed rather than read is a fit that is wrong by exactly the ratio
    nobody checked.
    """
    ...


def extract_rhs(session_dir: Path) -> BitStream:
    """The barcode line out of an Intan RHS session's digital-in file."""
    ...
```

- [x] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/timebase/test_extract.py -v`
Expected: PASS

- [x] **Step 5: Prove the tests are not vacuous**

Change `_edges_from_bit`'s `bit` to `bit + 1` in one extractor. Both that system's tests must fail. Restore.

Run: `.venv/bin/python -m pytest tests/timebase/test_extract.py -v`

- [x] **Step 6: Run the full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add wl_preproc/timebase/extract.py tests/timebase/test_extract.py
git commit -m "feat(timebase): SpikeGLX and RHS barcode extraction

Rates are read from the file's own metadata, never assumed: a rate taken
as a constant is a fit wrong by exactly the ratio nobody checked.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Raise the camera frame rate, and give the camera a digital line

> **Done 2026-08-22, with one addition.** Step 5 adds `digital_line`; the design spec's §13,
> which that step cites, names **two** missing fields — the line "and... nor a frame-rate
> field". `frame_rate_hz` is added alongside it, on the same optional-because-published
> terms, because a per-frame line is useless without the rate that sampled it and Task 5's
> `extract_bcam` would otherwise have to assume `synth.CAMERA_FPS` — the fixture's rate, not
> the camera's. §13 now records the status of both.
>
> The camera also gains `BCAM_PRE_ROLL_S = 0.85`, the fourth distinct tick origin design spec
> §10 asks for, checked against `IDLE_MIN_US` rather than chosen. Frame counts include it, so
> `camera_frame_count()` exists to keep `session.py` and the emitter from holding two copies of
> that arithmetic. `write_camera_sidecar` gained `truth` and `drift_ppm` to render the line.


**Files:**
- Modify: `wl_preproc/synth/peripherals.py`, `wl_preproc/contracts/sidecar.py`
- Test: `tests/synth/test_peripherals.py`, `tests/contracts/test_sidecar.py`

**Interfaces:**
- Produces: `CAMERA_FPS` at a rate clearing `min_sample_rate_hz()`; `BehaviorCameraSidecar.digital_line` (optional); the sidecar's per-frame digital samples.

**Why this task exists.** `CAMERA_FPS` is `200.0` today. Against a 5 ms bit slot that is **exactly 1.0 samples per bit** — below the floor, so the shipped fixture cannot decode a barcode at all. The spec is written against ≥400 Hz. **This is a fixture that contradicts the design, found before implementation rather than during it.**

- [x] **Step 1: Write the failing test**

```python
# tests/synth/test_peripherals.py
from wl_preproc.synth.peripherals import CAMERA_FPS
from wl_preproc.timebase.extract import min_sample_rate_hz


def test_camera_fps_clears_the_barcode_decoding_floor():
    """The behaviour camera carries the barcode like every other system
    (design spec section 2), so its frame rate IS its sampling rate for that
    line. At the previous 200 Hz it was exactly 1.0 samples per 5 ms bit —
    undecodable — which is a fixture contradicting the design.

    Asserted against the derived floor rather than a literal, so this fails if
    either the rate or wl-sync's bit slot moves.
    """
    assert CAMERA_FPS >= min_sample_rate_hz()


def test_camera_fps_has_margin_over_the_floor():
    """400 Hz is the boundary, where samples can land on transitions. The spec
    records 500 Hz as the rate with a published system behind it, so the
    fixture runs with margin rather than at the edge."""
    assert CAMERA_FPS >= 1.25 * min_sample_rate_hz()
```

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/synth/test_peripherals.py -v`
Expected: FAIL — `assert 200.0 >= 400.0`

- [x] **Step 3: Raise the rate and record why**

```python
# wl_preproc/synth/peripherals.py
# 500 Hz, raised from 200 Hz in Phase 1c-4. The behaviour camera carries the
# barcode like every other system, so its frame rate is its sampling rate for
# that line — and 200 Hz gave exactly 1.0 samples per 5 ms bit slot, which
# cannot decode. 500 Hz gives 2.5 and is the rate with a published system
# behind it (the ohDPI cameras). See `timebase.extract.min_sample_rate_hz`.
CAMERA_FPS = 500.0
```

- [x] **Step 4: Run the full suite and fix the ripple**

Run: `.venv/bin/python -m pytest -q`

`CAMERA_FPS` feeds `frame_count = int(recipe.duration_s * CAMERA_FPS)`, so sidecar frame counts change. Update any test asserting a literal frame count to derive it from `CAMERA_FPS` instead — a literal that has to be edited when the rate changes is the same defect one layer down.

- [x] **Step 5: Add the sidecar's digital-line field as OPTIONAL**

```python
# wl_preproc/contracts/sidecar.py
    # Proposed 2026-08-16 for Phase 1c-4, and OPTIONAL by design.
    #
    # This is a PUBLISHED contract the separate FLIR project builds against, so
    # the field is added in a backward-compatible shape: existing sidecars
    # without it still validate, and the FLIR project is not broken by a change
    # it has not agreed to. Until it emits the field, bcam alignment is
    # specified and unavailable rather than silently wrong.
    #
    # See the 1c-4 design spec section 13. This is a proposal, not an applied
    # amendment.
    digital_line: list[int] | None = None
```

- [x] **Step 6: Write the failing test for backward compatibility**

```python
# append to tests/contracts/test_sidecar.py
def test_a_sidecar_without_a_digital_line_still_validates():
    """The field is optional BECAUSE this contract is published. A sidecar
    written by the FLIR project before it adopts the field must still parse —
    otherwise adding the field breaks a consumer that never agreed to it."""
    sidecar = BehaviorCameraSidecar.from_yaml(VALID_SIDECAR_YAML)
    assert sidecar.digital_line is None
```

- [x] **Step 7: Emit the digital line from the generator, re-export schemas, commit**

Have `write_camera_sidecar` render the barcode into `digital_line` at `CAMERA_FPS`, using `wl_sync.barcode.encode` for the frame shape and the recipe's `drift_ppm` for the camera's own clock.

```bash
.venv/bin/python -m pytest -q
.venv/bin/wlpp schemas export --out docs/schemas
git add -A
git commit -m "feat(synth): the camera carries the barcode, at a rate that can decode it

CAMERA_FPS was 200 Hz against a 5 ms bit slot — exactly 1.0 samples per bit,
undecodable. The shipped fixture contradicted the design it was meant to
exercise. Now 500 Hz, asserted against the derived floor rather than a literal.

The sidecar's digital_line field is OPTIONAL because that contract is
published: existing sidecars validate unchanged, and the FLIR project is not
broken by a change it has not agreed to.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: The ohdpi emitter and profile

> **Done 2026-08-22, with one addition.** Adding `RECIPES["eye"]` was not enough to make the
> profile reachable: `wl_preproc/cli/main.py` held a **second** copy of the name-to-recipe
> mapping and a hardcoded `choices=["ci", "benchmark", "stim"]`, so the new profile existed,
> `generate_session` handled it, and the CLI rejected it as invalid. Both copies are gone —
> `--profile` now derives its choices from `RECIPES` — and a test asserts every key is offered,
> run through the **shipped console script** rather than `-m`, per the `.pth` trap.
>
> The two existing subprocess tests in that file had no `timeout=`, which the checkpoint
> records as "a test that reds by timeout is not a test". They have one now.


**Files:**
- Create: `wl_preproc/synth/ohdpi.py`, `tests/synth/test_ohdpi.py`
- Modify: `wl_preproc/synth/recipe.py`, `wl_preproc/synth/session.py`

**Interfaces:**
- Produces: `write_ohdpi(dir_path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0) -> Path`, and a `RECIPES["eye"]` profile whose `systems` include `ohdpi`.

**Context.** `ohdpi` is OpenIrisDPI, a dual-Purkinje eye tracker at 500 Hz. It appears in the `SYSTEMS` tuple and **nowhere else** — no emitter, no profile, no fixture. Its real per-frame file format is an open question (spec §12.1), so this emitter writes the *proposed* shape: one row per frame carrying a frame index, a native timestamp and a digital sample. **Isolate every format assumption in this one file** so a real file can settle them without touching the extractor's logic.

- [x] **Step 1: Write the failing test**

```python
# tests/synth/test_ohdpi.py
from wl_preproc.synth.ohdpi import OHDPI_FPS, write_ohdpi
from wl_preproc.timebase.extract import min_sample_rate_hz


def test_ohdpi_fps_clears_the_decoding_floor():
    assert OHDPI_FPS >= min_sample_rate_hz()


def test_ohdpi_emits_one_row_per_frame_with_a_digital_sample(tmp_path):
    """The proposed shape, isolated here so a real file can replace it without
    touching the extractor. Spec section 12.1 records that the true column
    names are unknown — this test pins what the FIXTURE does, not what
    OpenIris does."""
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.truth import GroundTruth  # noqa: F401

    recipe = RECIPES["eye"]
    ...
```

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/synth/test_ohdpi.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.synth.ohdpi'`

- [x] **Step 3: Write the emitter, the profile, and the dispatch branch**

Add `ohdpi` to `session.py`'s per-system `elif` chain beside `bcam`, and a `RECIPES["eye"]` profile with `systems=("syncbox", "spikeglx", "ohdpi")`.

- [x] **Step 4: Run, then commit**

```bash
.venv/bin/python -m pytest -q
git add -A
git commit -m "feat(synth): an ohdpi emitter, because there was no fixture at all

ohdpi is OpenIrisDPI, and it appeared in the SYSTEMS tuple and nowhere else —
no emitter, no profile, nothing to test against. Every format assumption is
isolated in this one file so a real recording can settle them without
touching the extractor.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: ohdpi and bcam extraction

> **Done 2026-08-22.** `extract_ohdpi` takes the per-frame recording FILE, not the directory
> the plan's signature sketched: the registry's unit is **one recording**, because §4.1 accepts
> or rejects files individually and one recording is one `Segment` candidate. That is a file for
> four systems and a directory for `rhs` only, because Intan's layout makes a recording a
> directory. The registry's docstring carries the table.
>
> `extract_ohdpi` measures its rate from the file's own timestamps rather than taking
> `OHDPI_FPS`, over the whole span rather than an adjacent difference — one interval is
> quantised to the timestamp resolution, which at 500 Hz is a percent-level error the entire
> fit would inherit. `extract_bcam` refuses a sidecar missing either proposed field instead of
> falling back on `synth.CAMERA_FPS`.
>
> Step 5's measurement prints per-system counts in the suite output: **100% on clean fixtures
> for all five**, including ohdpi at its 2.5 samples/bit margin.


**Files:**
- Modify: `wl_preproc/timebase/extract.py`
- Test: `tests/timebase/test_extract.py`

**Interfaces:**
- Produces: `extract_ohdpi(dir_path: Path) -> BitStream`, `extract_bcam(sidecar_path: Path) -> BitStream`, and `EXTRACTORS: dict[str, Callable[[Path], BitStream]]` keyed by the `SYSTEMS` names.

- [x] **Step 1: Write the failing tests**

```python
# append to tests/timebase/test_extract.py
from wl_preproc.contracts.paths import SYSTEMS
from wl_preproc.timebase.extract import EXTRACTORS


def test_every_system_has_an_extractor():
    """The registry is the phase's completeness claim. A system in SYSTEMS with
    no extractor is a system that silently never aligns."""
    assert set(EXTRACTORS) == set(SYSTEMS)


def test_ohdpi_extraction_recovers_ground_truth_barcodes(tmp_path):
    """At 500 Hz this is 2.5 samples per 5 ms bit — the thinnest margin in the
    design. Recovery is checked against GroundTruth, and the decode RATE is
    reported so a regression in margin shows up as a number rather than as a
    flaky test."""
    ...


def test_bcam_extraction_recovers_ground_truth_barcodes(tmp_path):
    ...
```

- [x] **Step 2–4: Run, implement, run**

Run: `.venv/bin/python -m pytest tests/timebase/test_extract.py -v`

- [x] **Step 5: Measure and record decode reliability**

The spec requires decode reliability be **measured, not asserted**. Add a test that reports recovered-versus-emitted counts per system and asserts 100% on clean fixtures, so the margin at 2.5 samples/bit is a measured number in the suite output rather than a claim in a docstring.

- [x] **Step 6: Commit**

```bash
.venv/bin/python -m pytest -q
git add -A
git commit -m "feat(timebase): ohdpi and bcam extraction, and the system registry

The registry is the completeness claim: a system in SYSTEMS with no extractor
is one that silently never aligns, so the set equality is asserted.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Fitting — rate per session, offset per segment

> **Done 2026-08-22. Step 4 could not be written as specified, and finding out why is the
> task's main result.** Two fixture defects and one impossible tolerance:
>
> 1. **No fixture had any relative drift.** `session.py` gave `recipe.drift_ppm` to every
>    emitter *including the sync box* — and session time is the sync box's timeline, so it
>    cancelled exactly. Invisible for three phases because all four recipes left it at `0.0`.
>    The sync box now gets `0.0` unconditionally, `system_drift_ppm` overrides per system, and
>    a `drift` profile carries all five systems with four distinct clocks.
> 2. **The plan's flat `abs=1.0` ppm is not achievable on a short fixture.** A sampled edge is
>    known to one sample period, so a slope over span `T` cannot beat `period / T`: 2.4 ppm at
>    30 kHz over 14 s, 143 ppm at 500 Hz. §4.5's "well under 1 ppm" is a claim about a
>    *session*, where the quantity is 0.01 ppm. Tolerance is now derived per system.
> 3. **A realistic camera drift is unmeasurable here.** At 47 ppm ohdpi fitted **0.000 ppm with
>    a zero residual** — every barcode landed in the frame it would have occupied undrifted. The
>    camera fixtures now carry deliberately unrealistic magnitudes, which the recipe says at
>    length, because the alternative was a camera test that cannot fail.
>
> `fit_rate` raises below two matched barcodes rather than returning nominal-and-zero, and the
> plan's own last test was rewritten: it passed a `fit_rate([], {}, ...)` inside the
> `pytest.raises`, so the rate fit raising would have satisfied it and the offset guard under
> test would never have run.


**Files:**
- Create: `wl_preproc/timebase/fit.py`, `tests/timebase/test_fit.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class RateFit:
      fitted_rate_hz: float
      drift_ppm: float
      n_matched: int
      residual_us_rms: float
      residual_us_max: float

  @dataclass(frozen=True)
  class OffsetFit:
      offset_s: float
      residual_us: float
      n_barcodes: int

  def fit_rate(device_barcodes: Sequence[Barcode], reference_s: Mapping[int, float],
               nominal_rate_hz: float) -> RateFit
  def fit_offset(device_barcodes: Sequence[Barcode], reference_s: Mapping[int, float],
                 rate_fit: RateFit) -> OffsetFit
  ```
  `reference_s` maps barcode value → sync-box session time in seconds. Matching is **by value, never by ordinal position** — one dropped barcode must not shift every later one (parent spec §4.2 requirement 1 applies the same rule to trials).

- [x] **Step 1: Write the failing tests**

```python
# tests/timebase/test_fit.py
import pytest
from wl_sync.barcode import Barcode
from wl_preproc.timebase.fit import fit_offset, fit_rate


def test_rate_is_fitted_from_pooled_barcodes_to_under_one_ppm():
    """Spec section 4.5: a full session fits rate to well under 1 ppm."""
    reference = {v: float(v) for v in range(0, 600)}
    true_ppm = 12.0
    device = [
        Barcode(value=v, start_us=int(v * 1_000_000 * (1 + true_ppm / 1e6)))
        for v in reference
    ]
    fit = fit_rate(device, reference, nominal_rate_hz=1_000_000.0)
    assert fit.n_matched == 600
    assert abs(fit.drift_ppm - true_ppm) < 1.0


def test_matching_is_by_value_so_a_dropped_barcode_shifts_nothing():
    """One missing barcode must not shift every later one. Ordinal matching
    passes on clean data and is catastrophically wrong on exactly the data that
    matters, which is why this test drops one from the middle."""
    reference = {v: float(v) for v in range(0, 600)}
    device = [Barcode(value=v, start_us=v * 1_000_000) for v in reference if v != 300]
    fit = fit_rate(device, reference, nominal_rate_hz=1_000_000.0)
    assert fit.n_matched == 599
    assert abs(fit.drift_ppm) < 0.1
    assert fit.residual_us_max < 10.0


def test_offset_uses_the_session_rate_rather_than_estimating_its_own():
    """A short segment inherits the session rate. Spec section 4.5: fitting rate
    locally from two barcodes spanning ~2 s yields ~16 ppm, WORSE than
    inheriting. So the offset fit takes a RateFit and does not re-estimate."""
    reference = {v: float(v) for v in range(0, 600)}
    rate = fit_rate(
        [Barcode(value=v, start_us=v * 1_000_000) for v in reference],
        reference,
        nominal_rate_hz=1_000_000.0,
    )
    segment = [Barcode(value=300, start_us=300_000_000 + 5_000_000)]
    off = fit_offset(segment, reference, rate)
    assert off.n_barcodes == 1
    assert off.offset_s == pytest.approx(-5.0, abs=1e-6)


def test_a_segment_with_no_barcodes_cannot_be_offset():
    """Zero barcodes is not an offset of zero. It is unalignable, and spec
    section 4.1 sends it to RejectedSegment — so this raises rather than
    returning a number that reads like a measurement."""
    with pytest.raises(ValueError, match="no barcodes"):
        fit_offset([], {}, fit_rate([], {}, nominal_rate_hz=1.0))
```

- [x] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/timebase/test_fit.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Implement `fit.py`**

Least-squares regression of device time against reference session time, matched by value. No DataJoint import, no file I/O — this module is pure numeric so it is testable without a database.

- [x] **Step 4: Run to verify they pass, then check against the real fixtures**

Add a test that runs the whole chain — extract, decode, fit — for every system in a generated session, and asserts each system's fitted drift matches the recipe's `drift_ppm` within 1 ppm. **This is the test that proves the phase works**; the unit tests above prove the arithmetic.

- [x] **Step 5: Commit**

```bash
.venv/bin/python -m pytest -q
git add wl_preproc/timebase/fit.py tests/timebase/test_fit.py
git commit -m "feat(timebase): pooled rate fit and per-segment offset

Matching is by barcode value, never by ordinal position: ordinal matching
passes on clean data and is catastrophically wrong on exactly the data that
matters.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Schema — Computed tables, new fields, and the Block comment

**Files:**
- Create: `wl_preproc/schema/timebase.py`, `tests/schema/test_timebase.py`
- Modify: `wl_preproc/schema/core.py`, `wl_preproc/schema/coverage.py`

**Interfaces:**
- Produces: `SystemTimebase`, `TimingProvenance`, `activate(prefix)`; `core.Segment` as `dj.Computed` with `file_path`, `first_sample`, `offset_s`, `residual_us`, `n_barcodes`; `coverage.BlockCoverage` / `TrialCoverage` as `dj.Computed`.

**This task changes declarations on tables that have no rows.** That is why it is free today and needs a migration after January — the same argument that forced the `<blob>` fix in parent spec §5.1.1.

- [ ] **Step 1: Write the failing tests**

```python
# tests/schema/test_timebase.py
import datajoint as dj

from wl_preproc.schema import core, coverage, timebase


def test_derived_tables_are_computed_not_manual():
    """These are derived quantities with no human author. Declared Manual in
    1c-1 when nothing computed them; Computed now that something does. Free
    while no row exists anywhere, a migration once one does."""
    assert issubclass(core.Segment, dj.Computed)
    assert issubclass(coverage.BlockCoverage, dj.Computed)
    assert issubclass(coverage.TrialCoverage, dj.Computed)


def test_rejected_segment_stays_manual_because_its_key_cannot_be_computed():
    """RejectedSegment is keyed on file_path precisely because a file yielding
    zero barcodes has no segment_barcode to key on. It records a fact about a
    file, not a computation over one."""
    assert issubclass(core.RejectedSegment, dj.Manual)


def test_segment_carries_what_makes_the_transform_reversible():
    """Spec section 4.5 requires fit parameters, residuals and native stream
    timestamps be retained so every transform is reversible and auditable.
    Storing them on the row makes that a property of the data rather than a
    promise in a document."""
    attrs = set(core.Segment.heading.attributes)
    assert {"offset_s", "residual_us", "n_barcodes", "first_sample", "file_path"} <= attrs


def test_no_bare_longblob_in_the_new_schema_module():
    """The guardrail sweep auto-discovers schema modules, so this is belt and
    braces — but a bare longblob silently stores a numpy array as its truncated
    string repr and nothing raises on insert or fetch."""
    assert "longblob" not in timebase.SystemTimebase.definition
    assert "longblob" not in timebase.TimingProvenance.definition
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_timebase.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.schema.timebase'`

- [ ] **Step 3: Declare the new tables**

```python
# wl_preproc/schema/timebase.py
@schema
class SystemTimebase(dj.Computed):
    definition = """
    # The once-per-(system, session) clock fit. Rate is pooled across every
    # barcode in the session; offset is per-segment and lives on Segment.
    -> core.AcquisitionSystem
    ---
    time_source        : enum('barcode','trigger')
    nominal_rate_hz    : double
    fitted_rate_hz     : double
    drift_ppm          : double
    n_barcodes_decoded : int unsigned
    n_barcodes_matched : int unsigned
    residual_us_rms    : double
    residual_us_max    : double
    """
```

`time_source` exists because a camera system aligned by barcode is precise to one frame period (~2 ms at 500 Hz), while one aligned by an external trigger is exact. A downstream analysis that cares about 2 ms must be able to tell which it got.

- [ ] **Step 4: Convert the three tables and correct the Block comment**

`Block`'s comment currently reads *"boundaries are decoded from event codes and cross-validated against those rows"*, which contradicts closed open item 9 (wl-preproc **never authors** block rows). Correct it to say the boundaries are wl.works' assertion, recorded through `accept()`, and that the measured boundary is a separate quantity owned by 1c-5.

- [ ] **Step 5: Run the schema tests and the guardrail sweep**

```bash
.venv/bin/python -m pytest tests/schema -q
.venv/bin/python -m pytest tests/schema/test_guardrails.py -q
```

- [ ] **Step 6: Commit**

```bash
.venv/bin/python -m pytest -q
git add -A
git commit -m "feat(schema): timebase tables, and three declarations that were always derived

Segment, BlockCoverage and TrialCoverage were declared Manual in 1c-1 when
nothing computed them. Converting now is free because no row exists anywhere;
after January it is a migration.

Block's comment claimed its boundaries are decoded and cross-validated, which
contradicts closed open item 9 — wl-preproc never authors block rows. The
comment was the thing that was wrong.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Populate segments and timebases

**Files:**
- Create: `wl_preproc/timebase/segments.py`, `tests/timebase/test_segments.py`
- Modify: `wl_preproc/schema/core.py`, `wl_preproc/schema/timebase.py`

**Interfaces:**
- Consumes: `EXTRACTORS`, `fit_rate`, `fit_offset`.
- Produces: `classify_segment(duration_s: float, n_barcodes: int) -> str` returning `"alignable"` or a rejection reason; `SystemTimebase.make()`, `Segment.make()`.

- [ ] **Step 1: Write the failing tests for §4.1's rules**

```python
# tests/timebase/test_segments.py
import pytest
from wl_preproc.timebase.segments import classify_segment


@pytest.mark.parametrize(
    "duration_s, n_barcodes, expected",
    [
        (3.5, 2, "alignable"),      # >=3.0s: two barcodes, local rate verification
        (2.5, 1, "alignable"),      # >=2.0s: one barcode, rate inherited
        (1.5, 0, "too_short"),      # <2.0s: may contain zero
        (5.0, 0, "no_barcode"),     # long enough, but decoded nothing
    ],
)
def test_segment_classification_follows_the_alignment_table(duration_s, n_barcodes, expected):
    """Spec section 4.1's table, and its bounds are consequences of the DECODER
    requiring a preceding idle of >=400 ms to identify a lead pulse — not of
    frame geometry. That correction is recorded in the parent spec because the
    original numbers were right about the format and wrong about the thing that
    would read it.

    `no_barcode` is distinct from `too_short`: a long file that decoded nothing
    is a different fault (wrong line, wrong bit, dead cable) from a file that
    was never long enough, and collapsing them loses the diagnosis.
    """
    assert classify_segment(duration_s, n_barcodes) == expected
```

- [ ] **Step 2–4: Run, implement, run.**

- [ ] **Step 5: Write the populate tests, proving what is written and what is not**

```python
# append to tests/timebase/test_segments.py
def test_an_unalignable_file_lands_in_rejected_segment_with_its_reason(...):
    """Recorded rather than dropped, so "why is this session short" has an
    answer — 1c-1's own table comment states the point."""


def test_a_file_with_no_barcodes_cannot_be_inserted_into_segment(...):
    """Segment is keyed on segment_barcode, "the first barcode value in the
    segment", so a file yielding zero barcodes has NO KEY and structurally
    cannot go there. An implementation that wants to invent a placeholder
    barcode has found the rule, not a limitation."""


def test_populate_writes_only_timebase_and_segment_rows(table_snapshot, deep_equal):
    """Proven by row snapshot, NOT by `in_transaction` — which is not a
    read-only check, since DataJoint's insert() calls connection.query()
    directly and never touches the transaction machinery. This project has been
    misled by that assumption five times."""
```

- [ ] **Step 6: Commit**

```bash
.venv/bin/python -m pytest -q
git add -A
git commit -m "feat(timebase): populate segments and per-system timebases

A file that decodes nothing is a different fault from a file that was never
long enough, and collapsing them loses the diagnosis.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Block coverage

**Files:**
- Create: `wl_preproc/timebase/coverage.py`, `tests/timebase/test_coverage.py`
- Modify: `wl_preproc/schema/coverage.py`

**Interfaces:**
- Produces: `classify_coverage(block: tuple[float, float], segments: Sequence[tuple[float, float]]) -> tuple[str, float]` returning `(coverage, covered_s)`; `BlockCoverage.make()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/timebase/test_coverage.py
import pytest
from wl_preproc.timebase.coverage import classify_coverage


@pytest.mark.parametrize(
    "block, segments, expected_state, expected_s",
    [
        ((0.0, 10.0), [(0.0, 10.0)], "full", 10.0),
        ((0.0, 10.0), [(0.0, 6.0)], "partial", 6.0),
        ((0.0, 10.0), [], "absent", 0.0),
        ((0.0, 10.0), [(0.0, 4.0), (6.0, 10.0)], "partial", 8.0),   # a gap in the middle
        ((0.0, 10.0), [(0.0, 5.0), (4.0, 10.0)], "full", 10.0),     # overlapping, not double-counted
        ((0.0, 10.0), [(-5.0, 15.0)], "full", 10.0),                # clipped to the block
    ],
)
def test_coverage_classification(block, segments, expected_state, expected_s):
    """`partial` is never collapsed into `absent`. Spec section 4.6: a recording
    that stopped mid-trial must never be silently treated as complete, and
    section 5.2.1 adds that partial is what wl.works asserts
    block_neural_assertion against and what excludes a block from a sort.

    Overlapping segments must not double-count, and segments extending past the
    block must clip — otherwise covered_s can exceed the block's own duration
    and `full` becomes unreachable by comparison.
    """
    state, covered_s = classify_coverage(block, segments)
    assert state == expected_state
    assert covered_s == pytest.approx(expected_s)


def test_covered_s_never_exceeds_the_block_duration():
    _, covered_s = classify_coverage((0.0, 10.0), [(-100.0, 100.0), (0.0, 10.0)])
    assert covered_s <= 10.0
```

- [ ] **Step 2–4: Run, implement, run.**

- [ ] **Step 5: Add the populate test, and state where block boundaries come from**

`BlockCoverage.make()` reads `Block.start_s`/`end_s`, which are **wl.works' assertion, not our measurement** (spec §9). Say so in the table's docstring where a reader will meet it — otherwise a reader assumes the boundaries were decoded.

- [ ] **Step 6: Commit**

```bash
.venv/bin/python -m pytest -q
git add -A
git commit -m "feat(coverage): per-block coverage as interval intersection

partial is never collapsed into absent: a recording that stopped mid-block
must never read as complete. Overlapping segments do not double-count and
segments are clipped to the block, or covered_s could exceed the block's own
duration and full would become unreachable.

Block boundaries here are wl.works' assertion, not our measurement.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Provenance, tier, daemon wiring, and the `~jobs` gap

**Files:**
- Modify: `wl_preproc/schema/timebase.py`, `wl_preproc/daemon.py`, `wl_preproc/cli/report.py`
- Test: `tests/schema/test_timebase.py`, `tests/schema/test_daemon.py`

**Interfaces:**
- Produces: `TimingProvenance.make()`; `daemon._computed_tables()` returning the four tables in dependency order.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/schema/test_timebase.py
def test_tier_is_pending_not_a_passing_grade_when_inputs_are_missing(...):
    """Spec section 8. Tiers A/B/C each need event-code agreement or trial
    counts, both of which are 1c-5. A tier derived from absent inputs treated
    as passing is a FALSE CLAIM OF VALIDATION, so this phase emits 'pending'
    and names what it could not compute — mirroring 1c-2's report, which names
    the categories it cannot yet count rather than omitting them."""


def test_tier_d_is_fully_derivable_now(...):
    """Any timing check failed. Quarantine and surface in the daily report,
    reusing 1c-2's path."""


def test_provenance_stores_the_inputs_so_the_tier_can_be_re_derived(...):
    """Spec section 4.7 requires the tier be derived, not asserted, and
    re-derivable under different thresholds later."""
```

```python
# append to tests/schema/test_daemon.py
def test_computed_tables_is_no_longer_empty():
    """1c-1 left this returning [] with a comment naming 1c-4 as what extends
    it. The ordering lives there so later phases extend one list rather than
    inventing their own traversal."""
    assert daemon._computed_tables() != []


def test_count_stale_jobs_sees_the_jobs_tables_it_reads(...):
    """This is the FIRST Computed table this project has ever declared, which
    makes live a path that was inert: count_stale_jobs reads DataJoint's
    internal ~jobs tables, and the 1c-2 handoff records that the report's
    write-detection snapshot does not cover them. A snapshot that silently
    misses a table is worse than no snapshot, because it reads as coverage."""
```

- [ ] **Step 2–4: Run, implement, run.**

- [ ] **Step 5: Close the `~jobs` snapshot gap**

Extend the report's write-detection snapshot to include the `~jobs` tables of every activated project schema, and add a test that a `populate` failure leaving a stale job row is both counted and visible.

- [ ] **Step 6: Run the full suite, export schemas, commit**

```bash
.venv/bin/python -m pytest -q
.venv/bin/wlpp schemas export --out docs/schemas
git diff --stat -- docs/schemas
git add -A
git commit -m "feat(timebase): provenance, a partial tier, and the daemon's first stage

Tier A/B/C each need event-code agreement or trial counts, both of which are
1c-5, so this phase emits 'pending' and names what it could not compute. A
tier derived from absent inputs treated as passing is a false claim of
validation.

_computed_tables() stops being empty, which makes count_stale_jobs's read of
DataJoint's ~jobs tables live for the first time — a path the report's
write-detection snapshot did not cover. A snapshot that silently misses a
table is worse than no snapshot, because it reads as coverage.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage.** §1 → Tasks 1–10 collectively. §2 (one family) → Tasks 1, 2, 5 plus the `EXTRACTORS` completeness test. §2.1 (why not the analog path) → no task; it is a rejected alternative, correctly carrying no code. §3 (Nyquist floor, executable) → Task 1 Step 1, Task 3 Step 1. §3.1 (edge quantisation, `time_source`) → Task 7 Step 3. §4.1 (three declaration changes) → Task 7. §4.2 (three new tables) → Tasks 7, 10. §4.3 (daemon, `~jobs`) → Task 10. §5 (fitting) → Task 6. §6 (segments, rejection) → Task 8. §7 (coverage) → Task 9. §8 (provenance, partial tier) → Task 10. §9 (`Block` comment) → Task 7 Step 4. §10 (testing) → distributed, with the ground-truth chain test in Task 6 Step 4. §11 (constraints) → Global Constraints. §12 (open questions) → Task 4 isolates the format assumptions §12.1 names. §13 (sidecar amendment) → Task 3 Steps 5–6, added as **optional** so the published contract is not broken.

**Placeholder scan.** Tasks 2, 4, 5, 8, 9 and 10 contain `...` in implementation bodies and some test bodies. **This is deliberate and is the plan's one known weakness**: the extractors' bodies depend on file layouts the implementer must read out of the corresponding synth emitter, and writing a guessed body here would be a plan asserting a format nobody checked — the exact defect this project tracks. Every such step names the file to read and what to look for. The *test* bodies left as `...` in Tasks 5, 8, 9 and 10 carry full docstrings stating the property; the implementer writes the assertion against the interface the task defines.

**Type consistency.** `BitStream(edges, fs_hz, n_samples)` is produced by Task 1 and consumed by Tasks 2, 5, 8. `RateFit` / `OffsetFit` are produced by Task 6 and consumed by Task 8. `EXTRACTORS` is produced by Task 5 and consumed by Task 8. `classify_segment` (Task 8) and `classify_coverage` (Task 9) are consumed only by their own `make()`. `min_sample_rate_hz()` is produced by Task 1 and consumed by Tasks 3 and 4. `CAMERA_FPS` is modified in Task 3 and consumed by Task 5's bcam extractor.

**Ordering dependency worth stating:** Task 3 must precede Task 5, because bcam cannot be extracted from a 200 Hz fixture. Task 4 must precede Task 5 for the same reason — there is no ohdpi fixture at all until it exists.
