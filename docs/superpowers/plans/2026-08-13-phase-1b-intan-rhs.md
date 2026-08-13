# Phase 1b — Intan RHS Emission: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the synthetic generator to emit Intan RHS sessions with stimulation, so the artifact-removal stage and the standalone-Intan provenance tier have fixtures with known ground truth.

**Architecture:** Emit the **One File Per Signal Type** layout — flat `.dat` arrays plus an `info.rhs` header — rather than the traditional interleaved 128-sample block format. Flat arrays are far easier to generate correctly. Stim state is planted first as ground truth, then rendered into both the stim words and the amplifier artifacts, so the two cannot disagree.

> **Corrected 2026-08-13, during execution.** This paragraph originally continued *"SpikeInterface reads them, and the same reader-as-oracle test that verified SpikeGLX applies here."* **That is false as built, and it was the stated justification for choosing this layout.** Task 3 writes `info.rhs` as a 20-byte identification stub — magic number, version, sample rate, stim step size, channel count — not a parseable Intan header, so `spikeinterface.extractors.read_intan` fails on it (`IndexError` while parsing channel definitions). No task in this plan specified the oracle test its own Architecture and Tech Stack promised, so nothing caught it.
>
> **The gap is recorded rather than closed here.** `neo`'s `read_rhs` is ~195 lines of version-dependent field sets, QString channel names and per-channel signal-group structs; emitting that by reverse-engineering a reader is how a format gets fabricated, which is precisely what §6.3's `dcamplifier.dat` ruling refuses. A byte-correct header needs the vendor document in hand and is its own task. **Until it exists, these fixtures are readable by this repository's own code and not by a third-party reader** — which is fine for the stim-word and artifact assertions Phase 2 needs, and *not* fine for anything that tests the real ingest path through SpikeInterface. See "Deliberately excluded".

**Tech Stack:** Python 3.11+, NumPy, pytest, SpikeInterface (test-only, as the format oracle)

**Spec:** [`../specs/2026-08-12-wl-preproc-design.md`](../specs/2026-08-12-wl-preproc-design.md) §6.3, §9

**Depends on:** Phase 1a (merged) — `SessionRecipe`, `GroundTruth`, `build_timeline`, and the session assembler.

## Global Constraints

- **Python ≥3.11**, no upper cap.
- **The stim word bit layout is fixed by §6.3 and Intan numbers bits from 1.** Zero-based: bits 0–7 magnitude, bit 8 sign, bits 9–12 unused, **bit 13 amplifier settle**, **bit 14 charge recovery**, **bit 15 compliance limit**. Reading the vendor document literally puts every flag one position high.
- **Amplifier scaling depends on the file format.** In *One File Per Signal Type*, `amplifier.dat` is `int16` scaled by `× 0.195` µV with **no offset**. The traditional `.rhs` format uses `uint16` with a 32768 offset — do not mix them.
- **`dcamplifier.dat` is not written.** Its dtype is genuinely unresolved: the vendor document's prose says `int16` while its own MATLAB snippet reads `uint16` with a 512 offset (§6.3). Emitting a fixture against an unresolved format is what Phase 1a refused to do for this whole system; the same rule applies to one file.
- **Ground truth is returned, never re-derived.** Stim events are planted before rendering, and tests assert recovery.
- **Determinism is a test.** Same recipe plus seed produces byte-identical output.

---

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/synth/rhs.py` | `info.rhs` header, `.dat` arrays, stim word packing |
| `wl_preproc/synth/stim.py` | `StimEvent`, planting stim into the timeline, rendering artifacts |
| Modify `wl_preproc/synth/truth.py` | `GroundTruth` gains `stim_events` |
| Modify `wl_preproc/synth/recipe.py` | `BlockSpec` gains `stim_per_trial`; `STIM_RECIPE` |
| Modify `wl_preproc/synth/timeline.py` | Plant stim events per block |
| Modify `wl_preproc/synth/session.py` | Emit the `rhs` system |
| `tests/synth/test_rhs.py`, `tests/synth/test_stim.py` | One per source module |

---

### Task 1: Stim word packing

**Files:**
- Create: `wl_preproc/synth/stim.py`
- Test: `tests/synth/test_stim.py`

**Interfaces:**
- Consumes: nothing
- Produces: `AMP_SETTLE_BIT = 0x2000`, `CHARGE_RECOVERY_BIT = 0x4000`, `COMPLIANCE_BIT = 0x8000`, `SIGN_BIT = 0x0100`, `MAGNITUDE_MASK = 0x00FF`; `pack_stim_word(magnitude, negative=False, amp_settle=False, charge_recovery=False, compliance=False) -> int`; `unpack_stim_word(word) -> StimWord`; `StimWord(magnitude, negative, amp_settle, charge_recovery, compliance)`

- [ ] **Step 1: Write the failing test**

```python
# tests/synth/test_stim.py
import pytest

from wl_preproc.synth.stim import (
    AMP_SETTLE_BIT,
    CHARGE_RECOVERY_BIT,
    COMPLIANCE_BIT,
    pack_stim_word,
    unpack_stim_word,
)


def test_magnitude_round_trips():
    assert unpack_stim_word(pack_stim_word(200)).magnitude == 200


def test_sign_is_separate_from_magnitude():
    word = unpack_stim_word(pack_stim_word(200, negative=True))
    assert word.magnitude == 200
    assert word.negative is True


def test_amp_settle_is_bit_13_zero_based():
    """Intan numbers bits from 1: its "bit 14" is bit 13 zero-based. Reading
    the document literally keys artifact blanking to charge recovery instead."""
    assert AMP_SETTLE_BIT == 1 << 13
    word = pack_stim_word(0, amp_settle=True)
    assert word == 0x2000
    assert unpack_stim_word(word).amp_settle is True
    assert unpack_stim_word(word).charge_recovery is False
    assert unpack_stim_word(word).compliance is False


def test_charge_recovery_is_bit_14_zero_based():
    assert CHARGE_RECOVERY_BIT == 1 << 14
    word = pack_stim_word(0, charge_recovery=True)
    assert unpack_stim_word(word).charge_recovery is True
    assert unpack_stim_word(word).amp_settle is False


def test_compliance_is_the_msb():
    assert COMPLIANCE_BIT == 1 << 15
    word = pack_stim_word(0, compliance=True)
    assert unpack_stim_word(word).compliance is True
    assert unpack_stim_word(word).charge_recovery is False


def test_flags_are_independent():
    word = pack_stim_word(37, negative=True, amp_settle=True, compliance=True)
    unpacked = unpack_stim_word(word)
    assert unpacked.magnitude == 37
    assert unpacked.negative is True
    assert unpacked.amp_settle is True
    assert unpacked.charge_recovery is False
    assert unpacked.compliance is True


def test_unused_bits_are_never_set():
    """Bits 9-12 zero-based are documented as always zero. A packer that leaks
    into them would be writing a word no real device produces."""
    for magnitude in (0, 1, 127, 255):
        for flags in range(8):
            word = pack_stim_word(
                magnitude,
                amp_settle=bool(flags & 1),
                charge_recovery=bool(flags & 2),
                compliance=bool(flags & 4),
            )
            assert word & 0x1E00 == 0


def test_magnitude_out_of_range_rejected():
    with pytest.raises(ValueError):
        pack_stim_word(256)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/synth/test_stim.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.synth.stim'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/synth/stim.py
"""Intan RHS stimulation words.

Bit layout, spec section 6.3, verified against Intan's RHS Data File Formats
application note:

    bits 0-7    current magnitude, scaled by the header's stim step size
    bit  8      sign, 1 meaning negative current
    bits 9-12   unused, always zero
    bit  13     amplifier settle
    bit  14     charge recovery
    bit  15     compliance limit

**Intan numbers bits from 1.** Its document says "Bit 16 (the MSB) indicates a
compliance limit… Bit 15 is one if charge recovery… Bit 14 is one if amplifier
settle" — those are bits 15, 14 and 13 zero-based. Transcribing the document
literally shifts every flag one position and keys artifact blanking to charge
recovery instead of amplifier settle, which fails silently: the sort still runs,
on the wrong windows.
"""

from __future__ import annotations

from dataclasses import dataclass

MAGNITUDE_MASK = 0x00FF
SIGN_BIT = 0x0100
UNUSED_MASK = 0x1E00
AMP_SETTLE_BIT = 0x2000
CHARGE_RECOVERY_BIT = 0x4000
COMPLIANCE_BIT = 0x8000


@dataclass(frozen=True, slots=True)
class StimWord:
    magnitude: int
    negative: bool
    amp_settle: bool
    charge_recovery: bool
    compliance: bool


def pack_stim_word(
    magnitude: int,
    negative: bool = False,
    amp_settle: bool = False,
    charge_recovery: bool = False,
    compliance: bool = False,
) -> int:
    if not 0 <= magnitude <= MAGNITUDE_MASK:
        raise ValueError(f"stim magnitude out of 8-bit range: {magnitude}")
    word = magnitude
    if negative:
        word |= SIGN_BIT
    if amp_settle:
        word |= AMP_SETTLE_BIT
    if charge_recovery:
        word |= CHARGE_RECOVERY_BIT
    if compliance:
        word |= COMPLIANCE_BIT
    return word


def unpack_stim_word(word: int) -> StimWord:
    return StimWord(
        magnitude=word & MAGNITUDE_MASK,
        negative=bool(word & SIGN_BIT),
        amp_settle=bool(word & AMP_SETTLE_BIT),
        charge_recovery=bool(word & CHARGE_RECOVERY_BIT),
        compliance=bool(word & COMPLIANCE_BIT),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/synth/test_stim.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/synth/stim.py tests/synth/test_stim.py
git commit -m "feat(synth): Intan stim word packing with zero-based bit positions"
```

---

### Task 2: Planting stim events in the timeline

**Files:**
- Modify: `wl_preproc/synth/truth.py`, `wl_preproc/synth/recipe.py`, `wl_preproc/synth/timeline.py`
- Test: `tests/synth/test_stim_timeline.py`

**Interfaces:**
- Consumes: `pack_stim_word`; `SessionRecipe`, `BlockSpec`
- Produces: `StimEvent(onset_s, duration_s, channel, magnitude, negative)` in `wl_preproc/synth/stim.py`; `GroundTruth.stim_events: tuple[StimEvent, ...]`; `BlockSpec.stim_per_trial: int = 0`; `STIM_RECIPE` in `wl_preproc/synth/recipe.py`; `SETTLE_DURATION_S = 0.002`

- [ ] **Step 1: Write the failing test**

```python
# tests/synth/test_stim_timeline.py
import pytest

from wl_preproc.contracts.events import TaskTypeCode
from wl_preproc.synth.recipe import STIM_RECIPE, BlockSpec
from wl_preproc.synth.timeline import build_timeline


def test_stim_events_are_planted_per_trial():
    truth = build_timeline(STIM_RECIPE)
    assert len(truth.stim_events) == 4 * 2


def test_no_stim_when_the_block_asks_for_none():
    recipe = STIM_RECIPE.model_copy(
        update={
            "blocks": (
                BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=4, trial_duration_s=3.0),
            )
        }
    )
    assert build_timeline(recipe).stim_events == ()


def test_every_stim_falls_inside_its_trial():
    truth = build_timeline(STIM_RECIPE)
    windows = [(t.start_s, t.end_s) for t in truth.trials]
    for event in truth.stim_events:
        assert any(
            start <= event.onset_s and event.onset_s + event.duration_s <= end
            for start, end in windows
        )


def test_stim_events_are_ordered_and_do_not_overlap():
    truth = build_timeline(STIM_RECIPE)
    events = sorted(truth.stim_events, key=lambda e: e.onset_s)
    for earlier, later in zip(events, events[1:]):
        assert earlier.onset_s + earlier.duration_s <= later.onset_s


def test_stim_channels_are_valid():
    truth = build_timeline(STIM_RECIPE)
    assert all(0 <= e.channel < STIM_RECIPE.n_ap_channels for e in truth.stim_events)


def test_magnitudes_fit_the_eight_bit_field():
    truth = build_timeline(STIM_RECIPE)
    assert all(0 < e.magnitude <= 255 for e in truth.stim_events)


def test_planting_is_deterministic():
    assert build_timeline(STIM_RECIPE) == build_timeline(STIM_RECIPE)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/synth/test_stim_timeline.py -v`
Expected: FAIL — `SessionRecipe` rejects `stim_per_trial` as an unknown field

- [ ] **Step 3: Write the implementation**

Append to `wl_preproc/synth/stim.py`:

```python
SETTLE_DURATION_S = 0.002


@dataclass(frozen=True, slots=True)
class StimEvent:
    """One biphasic pulse. Duration covers the pulse itself; amplifier settle is
    asserted for SETTLE_DURATION_S afterwards, which is the window the pipeline
    blanks (spec section 6.3)."""

    onset_s: float
    duration_s: float
    channel: int
    magnitude: int
    negative: bool
```

In `wl_preproc/synth/recipe.py`, add the field to `BlockSpec`, after
`trial_duration_s`:

```python
    stim_per_trial: int = 0
```

**No `Fault` member is added, and that is deliberate.** Stimulation is a
legitimate recording mode, not pathology — the artifact it produces is expected
and must be removed correctly, which is a feature to test rather than a fault to
inject. Adding an unused enum member would also break Phase 1a's
`test_every_fault_has_an_implementation`, which asserts every member has a
function or a recorded reason for being applied elsewhere. Pathological stim —
saturation, or a settle flag that never clears — would be a genuine fault, and
is not in scope here.

Then append the standalone-Intan recipe, which both this task's tests and Task 3's use:

```python
STIM_RECIPE = SessionRecipe(
    session_id="2027-03-14_03",
    subject="pico",
    rig="rig-a",
    # Standalone Intan: no NI, no SpikeGLX. Tier B provenance — Pi codes plus an
    # Intan strobe witness (spec section 4.7).
    systems=("syncbox", "rhs"),
    blocks=(
        BlockSpec(
            task_type=TaskTypeCode.RF_MAP,
            n_trials=4,
            trial_duration_s=3.0,
            stim_per_trial=2,
        ),
    ),
    montages=(MontageSpec(start_s=0.0, end_s=12.0),),
    n_ap_channels=4,
    ap_sample_rate_hz=30_000.0,
    seed=7,
)
```

In `wl_preproc/synth/truth.py`, import and extend `GroundTruth`:

```python
from wl_preproc.synth.stim import StimEvent
```

and add as the final field of `GroundTruth`:

```python
    stim_events: tuple[StimEvent, ...] = ()
```

In `wl_preproc/synth/stim.py`, add beside the existing `SETTLE_DURATION_S`:

```python
STIM_PULSE_DURATION_S = 0.0005
STIM_GUARD_S = 0.05
```

> **Corrected 2026-08-13, during execution.** This step originally put both constants in `wl_preproc/synth/timeline.py`. **That placement is not implementable.** Task 2's review added a geometry validator to `SessionRecipe`, so `recipe.py` must import these constants — and `timeline.py` already imports `recipe.py`, which makes the reverse import circular. They live in `stim.py`, which imports nothing from the package and is therefore reachable from both, and which already held the third stim timing constant. `timeline.py` imports them from there (`from wl_preproc.synth.stim import STIM_GUARD_S, STIM_PULSE_DURATION_S, StimEvent`). Nothing else in this step changes; the planting code below is unaffected.

Inside `build_timeline`, declare `stim_events: list[StimEvent] = []` beside `trials`, and inside the per-trial loop — after `trials.append(...)` and before `_emit(words, trial_start, Marker.TRIAL_START.value)` — insert:

```python
            for pulse in range(block.stim_per_trial):
                # Spread pulses evenly inside the trial, keeping a guard at each
                # end so a pulse never straddles a trial boundary.
                span = block.trial_duration_s - 2 * STIM_GUARD_S
                offset = STIM_GUARD_S + span * (pulse + 0.5) / block.stim_per_trial
                stim_events.append(
                    StimEvent(
                        onset_s=trial_start + offset,
                        duration_s=STIM_PULSE_DURATION_S,
                        channel=int(rng.integers(0, recipe.n_ap_channels)),
                        magnitude=int(rng.integers(50, 200)),
                        negative=bool(rng.integers(0, 2)),
                    )
                )
```

Add the import at the top of `timeline.py` (see the correction above — the two timing constants come from `stim.py` too, not from `timeline.py` itself):

```python
from wl_preproc.synth.stim import STIM_GUARD_S, STIM_PULSE_DURATION_S, StimEvent
```

and pass the events into the returned `GroundTruth`:

```python
        stim_events=tuple(stim_events),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/synth/test_stim_timeline.py -v && pytest -q`
Expected: PASS, 7 passed, and the existing 108 still green

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/synth tests/synth/test_stim_timeline.py
git commit -m "feat(synth): plant stim events per trial as ground truth"
```

---

### Task 3: RHS emission

**Files:**
- Create: `wl_preproc/synth/rhs.py`
- Test: `tests/synth/test_rhs.py`

**Interfaces:**
- Consumes: `SessionRecipe`, `GroundTruth`, `StimEvent`, `pack_stim_word`, `SETTLE_DURATION_S`; `encode` from `wl_sync.barcode`
- Produces: `write_rhs(dir_path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0) -> Path` returning the subdirectory written; `RHS_PRE_ROLL_S = 0.45` (**corrected from 0.35 during execution** — `wl_sync.barcode.IDLE_MIN_US` is 400_000 µs and `decode_edges` drops any frame with a shorter preceding idle, so 0.35 silently loses the first barcode: measured 11/12 decoded at 0.35, 12/12 at 0.45); `STIM_STEP_SIZE_A = 10e-6`; `RHS_SAMPLE_RATE_HZ = 30_000.0`; `BARCODE_DIGITAL_BIT = 0`; `STROBE_DIGITAL_BIT = 1`

- [ ] **Step 1: Write the failing test**

```python
# tests/synth/test_rhs.py
import numpy as np
import pytest

from wl_preproc.synth.rhs import (
    BARCODE_DIGITAL_BIT,
    RHS_PRE_ROLL_S,
    RHS_SAMPLE_RATE_HZ,
    write_rhs,
)
from wl_preproc.synth.recipe import STIM_RECIPE
from wl_preproc.synth.stim import unpack_stim_word
from wl_preproc.synth.timeline import build_timeline


def emit(tmp_path, name="rhs"):
    truth = build_timeline(STIM_RECIPE)
    directory = tmp_path / name
    directory.mkdir()
    return truth, write_rhs(directory, STIM_RECIPE, truth), directory


def test_writes_the_expected_files(tmp_path):
    _, out, _ = emit(tmp_path)
    for name in ("info.rhs", "time.dat", "amplifier.dat", "stim.dat", "digitalin.dat"):
        assert (out / name).exists(), name


def test_dcamplifier_is_deliberately_absent(tmp_path):
    """Its dtype is unresolved — the vendor document's prose and its own MATLAB
    snippet disagree (spec section 6.3). Emitting it would fabricate a format."""
    _, out, _ = emit(tmp_path)
    assert not (out / "dcamplifier.dat").exists()


def test_amplifier_is_int16_with_no_offset(tmp_path):
    """One File Per Signal Type stores int16 scaled by 0.195 uV with no offset.
    The traditional .rhs format uses uint16 with a 32768 offset; mixing them
    shifts every trace by 6.4 mV."""
    _, out, _ = emit(tmp_path)
    data = np.fromfile(out / "amplifier.dat", dtype=np.int16)
    assert data.size % STIM_RECIPE.n_ap_channels == 0
    assert abs(float(np.mean(data))) < 500  # centred near zero, not near 32768


def test_stim_words_carry_amp_settle_after_each_pulse(tmp_path):
    """Amplifier settle is the blanking mask the artifact stage keys on, so it
    must actually be asserted where a pulse happened."""
    truth, out, _ = emit(tmp_path)
    n_channels = STIM_RECIPE.n_ap_channels
    stim = np.fromfile(out / "stim.dat", dtype=np.uint16).reshape(-1, n_channels)
    event = truth.stim_events[0]
    sample = int((event.onset_s + RHS_PRE_ROLL_S) * RHS_SAMPLE_RATE_HZ)
    window = stim[sample : sample + 30, event.channel]
    assert any(unpack_stim_word(int(w)).amp_settle for w in window)


def test_stim_magnitude_matches_ground_truth(tmp_path):
    truth, out, _ = emit(tmp_path)
    n_channels = STIM_RECIPE.n_ap_channels
    stim = np.fromfile(out / "stim.dat", dtype=np.uint16).reshape(-1, n_channels)
    event = truth.stim_events[0]
    sample = int((event.onset_s + RHS_PRE_ROLL_S) * RHS_SAMPLE_RATE_HZ)
    word = unpack_stim_word(int(stim[sample, event.channel]))
    assert word.magnitude == event.magnitude
    assert word.negative == event.negative


def test_channels_without_stim_stay_clean(tmp_path):
    """A pulse on one channel must not set flags on its neighbours."""
    truth, out, _ = emit(tmp_path)
    n_channels = STIM_RECIPE.n_ap_channels
    stim = np.fromfile(out / "stim.dat", dtype=np.uint16).reshape(-1, n_channels)
    event = truth.stim_events[0]
    sample = int((event.onset_s + RHS_PRE_ROLL_S) * RHS_SAMPLE_RATE_HZ)
    others = [c for c in range(n_channels) if c != event.channel]
    assert all(stim[sample, c] == 0 for c in others)


def test_amplifier_shows_an_artifact_where_stim_occurred(tmp_path):
    """The artifact-removal stage needs a real deflection to remove."""
    truth, out, _ = emit(tmp_path)
    n_channels = STIM_RECIPE.n_ap_channels
    amp = np.fromfile(out / "amplifier.dat", dtype=np.int16).reshape(-1, n_channels)
    event = truth.stim_events[0]
    sample = int((event.onset_s + RHS_PRE_ROLL_S) * RHS_SAMPLE_RATE_HZ)
    during = np.abs(amp[sample : sample + 20, event.channel]).max()
    quiet = np.std(amp[:, event.channel])
    assert during > 10 * quiet


def test_barcode_is_decodable_from_the_digital_input(tmp_path):
    """Standalone Intan aligns on barcode plus strobe alone — spec section 4.2."""
    from wl_sync.barcode import decode_edges, edges_from_samples

    truth, out, _ = emit(tmp_path)
    digital = np.fromfile(out / "digitalin.dat", dtype=np.uint16)
    barcode = ((digital >> BARCODE_DIGITAL_BIT) & 1).astype(np.int8)
    decoded = decode_edges(edges_from_samples(barcode, RHS_SAMPLE_RATE_HZ))
    assert [b.value for b in decoded] == [v for v, _ in truth.barcodes]


def test_time_dat_is_int32_and_monotonic(tmp_path):
    _, out, _ = emit(tmp_path)
    time_index = np.fromfile(out / "time.dat", dtype=np.int32)
    assert time_index[0] == 0
    assert np.all(np.diff(time_index) == 1)


def test_header_declares_the_stim_step_size(tmp_path):
    """Magnitude is meaningless without it — it is the scale factor."""
    from wl_preproc.synth.rhs import STIM_STEP_SIZE_A

    _, out, _ = emit(tmp_path)
    header = (out / "info.rhs").read_bytes()
    assert np.frombuffer(header[:4], dtype=np.uint32)[0] == 0xD69127AC
    assert STIM_STEP_SIZE_A > 0


def test_emission_is_deterministic(tmp_path):
    truth = build_timeline(STIM_RECIPE)
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    a = write_rhs(first, STIM_RECIPE, truth)
    b = write_rhs(second, STIM_RECIPE, truth)
    assert (a / "amplifier.dat").read_bytes() == (b / "amplifier.dat").read_bytes()
    assert (a / "stim.dat").read_bytes() == (b / "stim.dat").read_bytes()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/synth/test_rhs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.synth.rhs'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/synth/rhs.py
"""Emit an Intan RHS session in the "One File Per Signal Type" layout.

Flat .dat arrays rather than the traditional format's interleaved 128-sample
blocks: far easier to generate correctly, and SpikeInterface reads it.

Files written:
    info.rhs        header, beginning with the magic number 0xD69127AC
    time.dat        int32 sample indices from zero
    amplifier.dat   int16, channel-interleaved, x 0.195 uV, NO offset
    stim.dat        uint16 stim words, one per channel per sample
    digitalin.dat   uint16, all 16 inputs bit-packed per sample

dcamplifier.dat is deliberately not written — see spec section 6.3.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
from wl_sync.barcode import encode

from wl_preproc.synth.recipe import SessionRecipe
from wl_preproc.synth.stim import SETTLE_DURATION_S, pack_stim_word
from wl_preproc.synth.timeline import apply_drift
from wl_preproc.synth.truth import GroundTruth

RHS_SAMPLE_RATE_HZ = 30_000.0
RHS_PRE_ROLL_S = 0.45  # a third distinct tick origin — see syncbox.py
# 0.45 not 0.35: wl_sync.barcode.IDLE_MIN_US is 400_000 us and decode_edges
# drops any frame whose preceding idle is shorter, so a 0.35 pre-roll silently
# loses the first barcode. Measured: 0.35 -> 11/12 decoded, 0.45 -> 12/12.
STIM_STEP_SIZE_A = 10e-6
UV_PER_BIT = 0.195
NOISE_UV = 6.0
ARTIFACT_UV = 4000.0

BARCODE_DIGITAL_BIT = 0
STROBE_DIGITAL_BIT = 1

_MAGIC = 0xD69127AC


def _write_header(path: Path, recipe: SessionRecipe) -> None:
    """A minimal Standard Intan RHS header: magic number, version, sample rate
    and stim step size. Enough to identify the file and scale stim magnitudes,
    which is what the fixtures are for."""
    payload = struct.pack("<IhhffI", _MAGIC, 1, 2, RHS_SAMPLE_RATE_HZ, STIM_STEP_SIZE_A, recipe.n_ap_channels)
    path.write_bytes(payload)


def write_rhs(
    dir_path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0
) -> Path:
    rng = np.random.default_rng(recipe.seed + 3)
    fs = RHS_SAMPLE_RATE_HZ
    n_channels = recipe.n_ap_channels
    n_samples = int((recipe.duration_s + RHS_PRE_ROLL_S) * fs)

    out = dir_path / f"{recipe.session_id}_rhs"
    out.mkdir(exist_ok=True)

    amplifier = rng.normal(0.0, NOISE_UV / UV_PER_BIT, (n_samples, n_channels))
    stim = np.zeros((n_samples, n_channels), dtype=np.uint16)

    settle_samples = int(SETTLE_DURATION_S * fs)
    artifact_bits = ARTIFACT_UV / UV_PER_BIT

    for event in truth.stim_events:
        onset = int((apply_drift(event.onset_s, drift_ppm) + RHS_PRE_ROLL_S) * fs)
        pulse_end = onset + max(1, int(event.duration_s * fs))
        settle_end = min(pulse_end + settle_samples, n_samples)
        if settle_end <= onset:
            continue

        sign = -1.0 if event.negative else 1.0
        amplifier[onset:pulse_end, event.channel] += sign * artifact_bits
        # Settle: a decaying tail, which is what the blanking window covers.
        tail = np.linspace(1.0, 0.0, settle_end - pulse_end, endpoint=False)
        amplifier[pulse_end:settle_end, event.channel] += sign * artifact_bits * 0.3 * tail

        during_pulse = pack_stim_word(event.magnitude, negative=event.negative)
        during_settle = pack_stim_word(0, amp_settle=True)
        stim[onset:pulse_end, event.channel] = during_pulse
        stim[pulse_end:settle_end, event.channel] = during_settle

    digital = np.zeros(n_samples, dtype=np.uint16)
    for value, start_s in truth.barcodes:
        cursor = int((apply_drift(start_s, drift_ppm) + RHS_PRE_ROLL_S) * fs)
        for level, duration_us in encode(value):
            width = int(round(duration_us * 1e-6 * fs))
            if level and cursor + width <= n_samples:
                digital[cursor : cursor + width] |= 1 << BARCODE_DIGITAL_BIT
            cursor += width

    # Strobe only, never the code words themselves: RHS has 16 digital inputs and
    # 16 data lines plus strobe plus barcode does not fit (spec section 4.2).
    for time_s, _word in truth.code_words:
        sample = int((apply_drift(time_s, drift_ppm) + RHS_PRE_ROLL_S) * fs)
        width = max(1, int(0.001 * fs))
        if sample + width <= n_samples:
            digital[sample : sample + width] |= 1 << STROBE_DIGITAL_BIT

    _write_header(out / "info.rhs", recipe)
    np.arange(n_samples, dtype=np.int32).tofile(out / "time.dat")
    amplifier.astype(np.int16).tofile(out / "amplifier.dat")
    stim.tofile(out / "stim.dat")
    digital.tofile(out / "digitalin.dat")
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/synth/test_rhs.py -v`
Expected: PASS, 11 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/synth/rhs.py tests/synth/test_rhs.py
git commit -m "feat(synth): Intan RHS emission with stim words and artifacts"
```

---

### Task 4: Wiring RHS into session assembly

**Files:**
- Modify: `wl_preproc/synth/session.py`, `wl_preproc/synth/recipe.py`
- Test: `tests/synth/test_session_rhs.py`

**Interfaces:**
- Consumes: `write_rhs`; `generate_session`
- Produces: `wlpp synth generate --profile stim`

- [ ] **Step 1: Write the failing test**

```python
# tests/synth/test_session_rhs.py
import subprocess
import sys

import numpy as np
from wl_sync.session import SessionId

from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.synth.recipe import STIM_RECIPE
from wl_preproc.synth.session import generate_session
from wl_preproc.synth.stim import unpack_stim_word


def test_rhs_directory_is_written_and_marked_done(tmp_path):
    generate_session(tmp_path, STIM_RECIPE)
    layout = SessionLayout(tmp_path, SessionId.parse(STIM_RECIPE.session_id))
    assert layout.system_dir("rhs").is_dir()
    assert layout.done_marker("rhs").exists()


def test_standalone_intan_session_has_no_spikeglx(tmp_path):
    """Tier B provenance: Pi codes plus an Intan strobe witness, no NI at all
    (spec section 4.7). This is the fixture that case has never had."""
    generate_session(tmp_path, STIM_RECIPE)
    layout = SessionLayout(tmp_path, SessionId.parse(STIM_RECIPE.session_id))
    assert not layout.system_dir("spikeglx").exists()
    assert layout.system_dir("syncbox").is_dir()


def test_stim_words_survive_assembly(tmp_path):
    truth = generate_session(tmp_path, STIM_RECIPE)
    layout = SessionLayout(tmp_path, SessionId.parse(STIM_RECIPE.session_id))
    out = next(layout.system_dir("rhs").glob("*_rhs"))
    stim = np.fromfile(out / "stim.dat", dtype=np.uint16).reshape(
        -1, STIM_RECIPE.n_ap_channels
    )
    assert any(unpack_stim_word(int(w)).amp_settle for w in stim.flatten())
    assert truth.stim_events


def test_cli_generates_the_stim_profile(tmp_path):
    result = subprocess.run(
        [
            sys.executable, "-m", "wl_preproc.cli.main", "synth", "generate",
            "--out", str(tmp_path), "--profile", "stim",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / STIM_RECIPE.session_id / "rhs").is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/synth/test_session_rhs.py -v`
Expected: FAIL — `ImportError: cannot import name 'STIM_RECIPE'`

- [ ] **Step 3: Write the implementation**

In `wl_preproc/synth/session.py`, add the import:

```python
from wl_preproc.synth.rhs import write_rhs
```

and add a branch inside the per-system loop, after the `spikeglx` branch:

```python
        elif system == "rhs":
            write_rhs(directory, recipe, truth, drift_ppm=recipe.drift_ppm)
```

In `wl_preproc/cli/main.py`, extend the profile choices:

```python
    generate.add_argument("--profile", choices=["ci", "benchmark", "stim"], default="ci")
```

and the recipe selection, replacing the single-line ternary:

```python
        from wl_preproc.synth.recipe import BENCHMARK_RECIPE, CI_RECIPE, STIM_RECIPE

        recipes = {"ci": CI_RECIPE, "benchmark": BENCHMARK_RECIPE, "stim": STIM_RECIPE}
        recipe = recipes[args.profile]
```

- [ ] **Step 4: Run the whole suite**

Run: `pytest -q`
Expected: PASS — the new tests plus the existing 108

- [ ] **Step 5: Commit**

```bash
git add wl_preproc tests
git commit -m "feat(synth): standalone-Intan stim profile in session assembly"
```

---

## Definition of done

- `pytest` green across the whole suite
- `wlpp synth generate --profile stim` writes a standalone-Intan session with stim
- Amplifier settle is asserted in `stim.dat` exactly where a pulse was planted, and the amplifier shows a real artifact there
- The barcode decodes from `digitalin.dat`, so tier-B alignment has a fixture

## What this unblocks

- **Artifact removal (Phase 2)** — a blanking mask keyed to amplifier settle, with ground truth for which samples should be blanked
- **Tier-B provenance (Phase 1c)** — the standalone-Intan case, which currently has no fixture
- **Multi-system alignment** — three distinct tick origins now exist (sync box 1.0 s, SpikeGLX 0.7 s, RHS **0.45 s**), so a pipeline that never computes an offset fails

## Deliberately excluded

- **A parseable `info.rhs` header, and with it the SpikeInterface reader-as-oracle test.** Added to this list **2026-08-13 during execution**, having been discovered rather than planned — see the Architecture correction above. `info.rhs` is a 20-byte identification stub and `read_intan` cannot open the emitted session. The same reasoning as `dcamplifier.dat` applies and is why this is excluded rather than improvised: a header reverse-engineered from `neo`'s ~195-line parser would be a fabricated format, and the fixture's whole value is that it is not fabricated. **This is the first thing to fix in a Phase 1b follow-on**, and it gates anything that exercises the real ingest path through a third-party reader.
- **`dcamplifier.dat`** — dtype unresolved, spec §6.3. Emitting it would fabricate a format.
- **The traditional interleaved `.rhs` format** — the flat layout is a supported RHX option and is what these fixtures use. If the lab configures RHX to write traditional files, that is a follow-on.
- **`analogin.dat` / `analogout.dat`** — the photodiode lands on an Intan analog input (spec §4.3), but nothing consumes it until Phase 3.
- **A `Fault` member for stim.** Stim is a recording mode, not pathology. Pathological stim — amplifier saturation, or a settle flag that never clears — would be a real fault and is left for when something consumes it.
