# Phase 1c-5 — Event decoding, the canonical trial list, and the tier: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decode the 16-bit event-code stream into events, trials and measured block boundaries, then resolve `TimingProvenance.tier` from `'pending'` to A, B or C.

**Architecture:** A new pure-logic package `wl_preproc/events/` mirroring `wl_preproc/timebase/`, where `extract.py` is the ONLY per-system code and everything downstream is shared. It declares **no new table** — every table it populates already exists, in element-event or in this repo. The codec is already frozen in `wl_preproc/contracts/events.py` and is **not** reimplemented.

**Tech Stack:** Python 3.11/3.13, DataJoint 2.x, element-event, numpy, pytest, testcontainers.

**Spec:** `docs/superpowers/specs/2026-08-23-phase-1c5-events-design.md`
Parent spec: `docs/superpowers/specs/2026-08-12-wl-preproc-design.md` §4.2, §4.2.1, §4.6, §4.7

## Global Constraints

- **The codec is frozen and is not reimplemented.** `wl_preproc/contracts/events.py` owns `Marker`, `TaskTypeCode`, `Escape`, `PAYLOAD_WORD_COUNTS`, `encode_payload` and `decode_stream`. This phase feeds and consumes it.
- **Trial matching is by ID, never by ordinal position** — parent spec §4.2 requirement 1: *"one dropped code must not shift every subsequent trial."*
- **Scalars only into `event.Event`.** `Event.Attribute.attribute_blob` is one of four allow-listed bare `longblob`s; an array written there is stored as its string repr with nothing raising.
- **`core.Block` is never written** — parent spec §8.3.1. wl.works asserts it; this phase writes `trial.Block` (measured) and cross-validates.
- **Session time throughout.** `BehaviorRecording` is `-> Session` with no extra key attribute, so one row per session is structural and element-event's "relative to recording start" IS session time.
- **`extract.py` is the only per-system code**, as 1c-4 established.
- Run tests with `.venv/bin/python -m pytest`. There is no `pip` in that venv — install nothing.
- Zero warnings. Green on 3.11 and 3.13.

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/synth/spikeglx.py` | **Modified** — nidq gains strobe + 16 data lines |
| `wl_preproc/events/__init__.py` | New package marker |
| `wl_preproc/events/extract.py` | Per-system word/strobe extraction. The only per-system code |
| `wl_preproc/events/taskfile.py` | The task-file reader seam + synthetic implementation |
| `wl_preproc/events/assemble.py` | Decoded events → trials and measured blocks |
| `wl_preproc/events/agreement.py` | The three tier inputs, and the tier |
| `wl_preproc/schema/events.py` | Population of the element-event tables + `MeasuredBlock` wiring |
| `tests/events/` | Pure-logic tests, no database |
| `tests/schema/test_events.py` | Population and tier tests |

---

### Task 1: Give the synthetic NI its code lines

**Files:**
- Modify: `wl_preproc/synth/spikeglx.py`
- Test: `tests/synth/test_spikeglx.py`

**Interfaces:**
- Consumes: `GroundTruth.code_words: tuple[tuple[float, int], ...]` — (session-time seconds, 16-bit word).
- Produces: a nidq stream whose digital word carries **bit 0 = barcode, bit 1 = strobe, bits 2–17 = the 16 data lines**, and a `.meta` whose `niXDChans1` and `~snsChanMap` describe all 18.

**Why:** spec §2.1. §4.2 routes *"16 data + strobe"* to the NI and §12 picks the PXIe-6353 for the *"32 hardware-timed Port 0 lines"* that needs. The generator carries only the barcode today, so **tier A — "≥2 independent full-code records (Pi + NI)" — cannot be produced or tested at all.**

- [ ] **Step 1: Write the failing test**

Append to `tests/synth/test_spikeglx.py`:

```python
def test_nidq_carries_the_code_words_not_only_the_barcode(tmp_path):
    """Spec section 4.2 routes 16 data lines plus strobe to the NI, and section
    12 picks the PXIe-6353 for exactly the 32 Port 0 lines that needs.

    Until 2026-08-23 the generator emitted only the barcode here, which made
    tier A -- two independent full-code records, Pi and NI -- impossible to
    produce or test. That left NP+NI, the lab's main recording configuration,
    at the one tier nothing exercised.
    """
    import numpy as np

    from wl_preproc.synth.recipe import Recipe
    from wl_preproc.synth.spikeglx import write_spikeglx
    from wl_preproc.synth.timeline import build_truth

    recipe = Recipe(session_id="synth-ni-codes", rig="rigA", seed=7)
    truth = build_truth(recipe)
    assert truth.code_words, "the fixture must emit code words at all"

    out = tmp_path / "spikeglx"
    write_spikeglx(out, recipe, truth)

    meta = (out / "synth-ni-codes_t0.nidq.meta").read_text()
    assert "niXDChans1=0:17" in meta, (
        "the nidq meta must declare all 18 digital lines -- barcode, strobe and "
        f"16 data. Got: {[l for l in meta.splitlines() if l.startswith('niXDChans1')]}"
    )

    raw = np.fromfile(out / "synth-ni-codes_t0.nidq.bin", dtype=np.int16)
    digital = raw.reshape(-1, 1)[:, 0].astype(np.uint16)

    # Strobe is bit 1. Every rising strobe edge must expose the word on bits 2..17.
    strobe = (digital >> 1) & 1
    rising = np.flatnonzero((strobe[1:] == 1) & (strobe[:-1] == 0)) + 1
    assert len(rising) == len(truth.code_words), (
        f"{len(rising)} strobe edges for {len(truth.code_words)} emitted words"
    )

    latched = [(int(digital[i]) >> 2) & 0xFFFF for i in rising]
    assert latched == [word for _, word in truth.code_words]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/synth/test_spikeglx.py::test_nidq_carries_the_code_words_not_only_the_barcode -v`
Expected: FAIL — `niXDChans1=0` is present, not `0:17`.

- [ ] **Step 3: Widen the digital line allocation**

In `wl_preproc/synth/spikeglx.py`, beside `NIDQ_BARCODE_XD_LINE`, add:

```python
# Spec section 4.2's routing table gives the NI 16 data lines plus strobe, and
# section 12 picks the PXIe-6353 for the 32 hardware-timed Port 0 lines that
# requires. One digital word carries all of it:
#
#   bit 0        barcode          (section 4.5 -- the timebase's own line)
#   bit 1        code strobe      (T1 = 500 us, section 4.2.1)
#   bits 2..17   the 16 data lines, latched at the strobe's FAR edge
#
# The latch is the far edge of T1, so the data is sampled where the strobe
# falls -- not where it rises. Section 4.2.1: "T1 is the strobe pulse width,
# and the latching edge is its far end."
NIDQ_CODE_STROBE_XD_LINE = 1
NIDQ_CODE_DATA_BASE_XD_LINE = 2
NIDQ_N_XD_LINES = 18
```

- [ ] **Step 4: Emit the strobe and data**

In the function that builds the nidq digital array (the one that currently writes only the barcode), after the barcode loop add:

```python
    # Strobe must be strictly narrower than the word spacing, or consecutive
    # strobes merge into one long high with no falling edge between them and
    # the words become uncountable. Phase 1b shipped exactly that defect: a
    # 1 ms pulse at 1 ms spacing rendered 31 words as 5 countable edges.
    strobe_width = max(1, int(round(STROBE_WIDTH_S * fs)))
    for time_s, word in truth.code_words:
        sample = int(round((apply_drift(time_s, drift_ppm) + NIDQ_PRE_ROLL_S) * fs))
        if sample + strobe_width > n_samples:
            continue
        digital[sample : sample + strobe_width] |= 1 << NIDQ_CODE_STROBE_XD_LINE
        digital[sample : sample + strobe_width] |= (
            (word & 0xFFFF) << NIDQ_CODE_DATA_BASE_XD_LINE
        )
```

Import `STROBE_WIDTH_S` from wherever `synth/rhs.py` takes it, so both emitters use one definition rather than two.

- [ ] **Step 5: Widen the meta**

Replace the two `.meta` lines that describe digital channels:

```python
            f"niXDChans1=0:{NIDQ_N_XD_LINES - 1}",
            f"~snsChanMap=(0,0,0,{_NIDQ_N_CHANNELS},0)(XD0;0:{NIDQ_N_XD_LINES - 1})",
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/synth/ -q`
Expected: PASS. If an existing test asserted `niXDChans1=0`, update it — the old value was the defect, not the assertion's premise. Say which in your report.

- [ ] **Step 7: Commit**

```bash
git add wl_preproc/synth/spikeglx.py tests/synth/test_spikeglx.py
git commit -m "feat(synth): give the NI its 16 code lines and strobe, so tier A is testable"
```

---

### Task 2: The events package, and syncbox word extraction

**Files:**
- Create: `wl_preproc/events/__init__.py`, `wl_preproc/events/extract.py`
- Test: `tests/events/test_extract.py`

**Interfaces:**
- Consumes: `wl_sync.log.CodeWord(tick_us: int, word: int)` and the log reader used by `timebase/extract.py::extract_syncbox`.
- Produces: `wl_preproc.events.extract.WordStream(words: tuple[tuple[int, int], ...], fs_hz: float)` where each pair is `(tick_or_sample, word)` in the device's **native** time; and `extract_syncbox_words(path: Path) -> WordStream`.

- [ ] **Step 1: Write the failing test**

Create `tests/events/test_extract.py`:

```python
"""Per-system code extraction. Pure logic against synthetic fixtures."""

from __future__ import annotations

import pytest

from wl_preproc.events import extract


def test_syncbox_words_come_back_in_order_with_their_ticks(tmp_path):
    """The Pi decodes the 16 lines itself and logs a CodeWord record, so its
    extraction is a read rather than a decode. Native ticks, not session time:
    converting is the fit's job, exactly as timebase/extract.py keeps them
    apart so the transform stays reversible (spec section 4.5)."""
    from wl_sync.log import CodeWord, SyncBoxLogHeader, write_log

    header = SyncBoxLogHeader(
        schema_version=1,
        session_id="synth-words",
        rig="rigA",
        boot_id="synth00000001",
        written_at="2026-01-01T00:00:00Z",
        gpio_map={"barcode_out": 17, "code_strobe": 27, "code_data_base": 2},
    )
    records = [CodeWord(tick_us=1_000, word=32), CodeWord(tick_us=2_500, word=0x8001)]
    path = tmp_path / "sync.jsonl"
    write_log(path, header, records)

    stream = extract.extract_syncbox_words(path)
    assert stream.words == ((1_000, 32), (2_500, 0x8001))


def test_syncbox_ignores_edge_records(tmp_path):
    """The log interleaves barcode Edges with CodeWords. Only the latter are
    words; an Edge reaching decode_stream would be a code that was never sent."""
    from wl_sync.log import CodeWord, Edge, SyncBoxLogHeader, write_log

    header = SyncBoxLogHeader(
        schema_version=1,
        session_id="synth-mixed",
        rig="rigA",
        boot_id="synth00000002",
        written_at="2026-01-01T00:00:00Z",
        gpio_map={"barcode_out": 17, "code_strobe": 27, "code_data_base": 2},
    )
    records = [
        Edge(tick_us=500, gpio=17, level=1),
        CodeWord(tick_us=1_000, word=32),
        Edge(tick_us=1_200, gpio=17, level=0),
    ]
    path = tmp_path / "sync.jsonl"
    write_log(path, header, records)

    assert extract.extract_syncbox_words(path).words == ((1_000, 32),)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/events/test_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.events'`

- [ ] **Step 3: Create the package**

Create `wl_preproc/events/__init__.py`:

```python
# wl_preproc/events/__init__.py
"""Pure event-code logic, with no DataJoint import.

Sits beside `wl_preproc/schema/events.py` the way `wl_preproc/timebase/` sits
beside `wl_preproc/schema/timebase.py`: the tables are one module, the logic
that fills them is a package, and the logic is testable with no database.

The CODEC is not here. `wl_preproc/contracts/events.py` is a frozen interface
(design spec section 3.5 item 4) and owns Marker, Escape, encode_payload and
decode_stream. This package extracts words from recordings, feeds them to that
decoder, and assembles what comes back.
"""
```

- [ ] **Step 4: Implement syncbox extraction**

Create `wl_preproc/events/extract.py`:

```python
# wl_preproc/events/extract.py
"""Per-system extraction of the event-code stream.

This module is the ONLY per-system code in Phase 1c-5, exactly as
`timebase/extract.py` is the only per-system code in 1c-4. Everything
downstream -- decode, trial assembly, agreement, tier -- is shared, because
every system carries the same protocol (design spec section 4.2).

**The three systems do not carry the same thing, and the return types say so.**
The sync box and the NI carry 16 data lines plus a strobe and yield WORDS. The
Intan RHS carries the strobe ONLY -- its 16 digital inputs cannot fit 16 data
lines plus strobe plus barcode -- and yields a WITNESS: a count and its timing,
never content. Returning empty words for the RHS would make a correct
strobe-only recording indistinguishable from a decode failure.

Native time throughout, never session time. Converting is the fit's job
(`timebase/fit.py`), and keeping them apart is what makes the transform
reversible as spec section 4.5 requires.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wl_sync.log import CodeWord, read_log


@dataclass(frozen=True, slots=True)
class WordStream:
    """16-bit words with their native timestamps, from a full-code recorder."""

    words: tuple[tuple[int, int], ...]
    fs_hz: float


@dataclass(frozen=True, slots=True)
class StrobeWitness:
    """Strobe edges with no word content, from a strobe-only recorder.

    A distinct type from `WordStream` on purpose: spec section 4.7's tier B is
    "1 full-code record + >=1 independent strobe witness", so a witness is a
    real contribution rather than a degraded word stream, and the type system
    should not let one be mistaken for the other.
    """

    edge_samples: tuple[int, ...]
    fs_hz: float

    @property
    def n_edges(self) -> int:
        return len(self.edge_samples)


# The sync box logs at microsecond resolution and is not a sampled line at all;
# 1 MHz is nominal, matching `timebase/extract.py`'s own treatment of it.
_SYNCBOX_NOMINAL_FS_HZ = 1_000_000.0


def extract_syncbox_words(path: Path) -> WordStream:
    """Words from the sync box log.

    The Pi decodes the 16 data lines itself and writes a `CodeWord` record, so
    there is nothing to decode here -- this is a read. `Edge` records in the
    same log are the barcode and are not words; letting one through would be a
    code nobody sent.
    """
    _header, records = read_log(path)
    words = tuple(
        (record.tick_us, record.word) for record in records if isinstance(record, CodeWord)
    )
    return WordStream(words=words, fs_hz=_SYNCBOX_NOMINAL_FS_HZ)
```

If `read_log`'s return shape differs, adapt the two lines that use it and say so in your report — do not change the test.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/events/ -q`
Expected: PASS, 2 tests.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/events/ tests/events/
git commit -m "feat(events): the events package, and syncbox word extraction"
```

---

### Task 3: NI word extraction, latched at the strobe's far edge

**Files:**
- Modify: `wl_preproc/events/extract.py`
- Test: `tests/events/test_extract.py`

**Interfaces:**
- Consumes: `WordStream` from Task 2; the nidq digital word written by Task 1 (bit 0 barcode, bit 1 strobe, bits 2–17 data).
- Produces: `extract_nidq_words(nidq_bin: Path) -> WordStream`.

- [ ] **Step 1: Write the failing test**

Append to `tests/events/test_extract.py`:

```python
def test_nidq_latches_the_word_at_the_strobes_FAR_edge(tmp_path):
    """Spec section 4.2.1: "T1 is the strobe pulse width, and the latching edge
    is its far end. Data has therefore been stable for T1 when the receiver
    latches."

    Sampling at the RISING edge would still pass on a fixture where data and
    strobe assert together -- which they do. So this test makes the data CHANGE
    mid-pulse and asserts the far-edge value wins. Sampling the near edge
    returns the stale word and fails.
    """
    import numpy as np

    from wl_preproc.events import extract

    fs = 25_000.0
    n = 1_000
    digital = np.zeros(n, dtype=np.uint16)

    # One strobe from sample 100 to 120. Data is 0x00AA for the first half and
    # 0x00BB for the second; only the latter is latched.
    digital[100:120] |= 1 << 1
    digital[100:110] |= 0x00AA << 2
    digital[110:120] |= 0x00BB << 2

    path = tmp_path / "s_t0.nidq.bin"
    digital.astype(np.int16).tofile(path)
    (tmp_path / "s_t0.nidq.meta").write_text(
        f"niSampRate={fs}\nnSavedChans=1\nniXDChans1=0:17\n"
        "~snsChanMap=(0,0,0,1,0)(XD0;0:17)\nfileSizeBytes=" + str(n * 2) + "\n"
    )

    stream = extract.extract_nidq_words(path)
    assert len(stream.words) == 1
    _sample, word = stream.words[0]
    assert word == 0x00BB, (
        f"got 0x{word:04X}; 0x00AA means the near edge was sampled, and spec "
        "section 4.2.1 puts the latch at the far end of T1"
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/events/test_extract.py::test_nidq_latches_the_word_at_the_strobes_FAR_edge -v`
Expected: FAIL — `AttributeError: module 'wl_preproc.events.extract' has no attribute 'extract_nidq_words'`

- [ ] **Step 3: Implement**

Add to `wl_preproc/events/extract.py`:

```python
import numpy as np

# Mirrors the emitter's allocation in `wl_preproc/synth/spikeglx.py`, which is
# spec section 4.2's routing table made concrete.
_NIDQ_STROBE_BIT = 1
_NIDQ_DATA_SHIFT = 2
_WORD_MASK = 0xFFFF


def extract_nidq_words(nidq_bin: Path) -> WordStream:
    """Words from the NI digital line.

    **Latched at the strobe's FAR edge, not its rising one.** Spec section
    4.2.1: data and strobe assert together at the start of T1, and the latching
    edge is T1's far end -- so T1 IS the setup time rather than adding to it.
    Sampling the rising edge would read data that has been valid for zero
    microseconds; sampling the falling edge reads data valid for a full T1.
    """
    from wl_preproc.timebase._nidq_meta import read_nidq_meta

    meta = read_nidq_meta(nidq_bin.with_suffix(".meta"))
    raw = np.fromfile(nidq_bin, dtype=np.int16)
    digital = raw.reshape(-1, meta.n_saved_chans)[:, -1].astype(np.uint16)

    strobe = (digital >> _NIDQ_STROBE_BIT) & 1
    # Falling edges: high at i, low at i+1. The word is read at i, the last
    # sample the strobe was still asserted.
    falling = np.flatnonzero((strobe[:-1] == 1) & (strobe[1:] == 0))

    words = tuple(
        (int(index), (int(digital[index]) >> _NIDQ_DATA_SHIFT) & _WORD_MASK)
        for index in falling
    )
    return WordStream(words=words, fs_hz=meta.sample_rate_hz)
```

If `read_nidq_meta`'s attribute names differ, adapt them and say so — `wl_preproc/timebase/_nidq_meta.py` is the existing reader and must not be duplicated.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/events/ -q`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/events/extract.py tests/events/test_extract.py
git commit -m "feat(events): NI word extraction, latched at the strobe's far edge"
```

---

### Task 4: The RHS strobe witness

**Files:**
- Modify: `wl_preproc/events/extract.py`
- Test: `tests/events/test_extract.py`

**Interfaces:**
- Consumes: `StrobeWitness` from Task 2; `wl_preproc/timebase/_rhs_header.py` for the sample rate.
- Produces: `extract_rhs_witness(session_dir: Path) -> StrobeWitness`.

- [ ] **Step 1: Write the failing test**

Append to `tests/events/test_extract.py`:

```python
def test_the_rhs_witness_counts_edges_rather_than_merely_finding_them():
    """Spec section 4.2 gives the Intan RHS the strobe ONLY -- 16 digital
    inputs cannot fit 16 data lines plus strobe plus barcode -- so it is a
    witness for tier B, never a decoder.

    The assertion is on the COUNT, and that is the whole point. Phase 1b shipped
    a 1 ms strobe at 1 ms word spacing, so consecutive strobes were contiguous:
    they merged into one long high with no falling edge between, and 31 code
    words rendered as 5 countable edges. A test that only asked whether edges
    exist would have passed on that. Section 4.2.1 now pins T1 = 500 us against
    1 ms spacing precisely so the edges stay countable.
    """
    import numpy as np

    from wl_preproc.events.extract import StrobeWitness

    # Three 500 us pulses at 1 ms spacing, 30 kHz: 15 samples high, 15 low.
    fs = 30_000.0
    digital = np.zeros(100, dtype=np.uint16)
    for start in (10, 40, 70):
        digital[start : start + 15] |= 1 << 1

    strobe = (digital >> 1) & 1
    rising = tuple(int(i) + 1 for i in np.flatnonzero((strobe[1:] == 1) & (strobe[:-1] == 0)))
    witness = StrobeWitness(edge_samples=rising, fs_hz=fs)

    assert witness.n_edges == 3, (
        "three pulses must yield three edges; a merged strobe yields one, which "
        "is the Phase 1b defect this assertion exists to catch"
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/events/test_extract.py::test_the_rhs_witness_counts_edges_rather_than_merely_finding_them -v`
Expected: PASS if `StrobeWitness` from Task 2 exists — this test pins the TYPE's contract. If it passes immediately, that is correct; the extraction function is Step 3 and gets its own test below.

- [ ] **Step 3: Implement the extractor**

Add to `wl_preproc/events/extract.py`:

```python
def extract_rhs_witness(session_dir: Path) -> StrobeWitness:
    """Strobe edges from the Intan RHS. A witness, never words.

    Spec section 4.2: RHS receives the strobe only. Its 16 digital inputs
    cannot fit 16 data lines plus the strobe plus the barcode, and the design
    permits that because the Pi is always present as a full-code recorder --
    a rule that "does not generalize back to the Pi", which is the sole
    recorder on training days.
    """
    from wl_preproc.timebase.extract import _STROBE_DIGITAL_BIT, _read_rhs_digital

    digital, fs_hz = _read_rhs_digital(session_dir)
    strobe = (digital >> _STROBE_DIGITAL_BIT) & 1
    rising = np.flatnonzero((strobe[1:] == 1) & (strobe[:-1] == 0)) + 1
    return StrobeWitness(edge_samples=tuple(int(i) for i in rising), fs_hz=fs_hz)
```

`timebase/extract.py` already reads the RHS digital array for the barcode. **Reuse its reader rather than writing a second one** — if the helper is private or shaped differently, extract it to a shared name in that module and import it here, and say so in your report. Two readers of the same file is the duplication this repo has removed twice.

- [ ] **Step 4: Add an end-to-end test over a real synthetic session**

Append to `tests/events/test_extract.py`:

```python
def test_rhs_witness_matches_the_emitted_word_count(tmp_path):
    """The witness's whole value is that its count equals the number of codes
    the session emitted. Asserted against a real synthetic session rather than
    a hand-built array, because the emitter is what a real recording resembles.
    """
    from wl_preproc.events import extract
    from wl_preproc.synth.recipe import Recipe
    from wl_preproc.synth.rhs import write_rhs
    from wl_preproc.synth.timeline import build_truth

    recipe = Recipe(session_id="synth-witness", rig="rigA", seed=11)
    truth = build_truth(recipe)
    out = tmp_path / "rhs"
    write_rhs(out, recipe, truth)

    witness = extract.extract_rhs_witness(out)
    assert witness.n_edges == len(truth.code_words)
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/events/ -q`
Expected: PASS, 5 tests.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/events/extract.py tests/events/test_extract.py
git commit -m "feat(events): the RHS strobe witness, which counts rather than merely finds"
```

---

### Task 5: The task-file reader seam

**Files:**
- Create: `wl_preproc/events/taskfile.py`
- Test: `tests/events/test_taskfile.py`

**Interfaces:**
- Consumes: the synthetic task file written by `wl_preproc/synth/peripherals.py::write_task_file` — JSON with `format`, `version`, and `trials[]` carrying `trial_id`, `block_id`, `start_s`, `end_s`, `condition`, `reward_ms`, `outcome`.
- Produces: `wl_preproc.events.taskfile.TaskTrial(trial_id: int, block_id: int, condition: int, outcome: str)`; the `TaskFileReader` Protocol with one method `trials(path: Path) -> list[TaskTrial]`; and `SyntheticTaskFileReader` implementing it.

- [ ] **Step 1: Write the failing test**

Create `tests/events/test_taskfile.py`:

```python
"""The task-file reader seam."""

from __future__ import annotations

import json

import pytest

from wl_preproc.events import taskfile


def test_the_synthetic_reader_returns_trials_by_id(tmp_path):
    path = tmp_path / "task.json"
    path.write_text(
        json.dumps(
            {
                "format": "synthetic-task-file",
                "version": 1,
                "trials": [
                    {"trial_id": 1, "block_id": 1, "start_s": 0.0, "end_s": 1.0,
                     "condition": 2, "reward_ms": 120, "outcome": "correct"},
                    {"trial_id": 2, "block_id": 1, "start_s": 1.0, "end_s": 2.0,
                     "condition": 3, "reward_ms": 120, "outcome": "error"},
                ],
            }
        )
    )
    trials = taskfile.SyntheticTaskFileReader().trials(path)
    assert [t.trial_id for t in trials] == [1, 2]
    assert [t.outcome for t in trials] == ["correct", "error"]
    assert trials[0].condition == 2


def test_an_unknown_format_is_refused_rather_than_guessed(tmp_path):
    """The behavioural stack is deliberately unchosen (spec section 4.2), so a
    second format WILL arrive. Reading an unrecognised one on a best-effort
    basis would silently produce a trial list that disagrees with the codes --
    and the disagreement is supposed to be a hard failure, not a merge."""
    path = tmp_path / "task.json"
    path.write_text(json.dumps({"format": "monkeylogic-bhv2", "version": 1, "trials": []}))

    with pytest.raises(taskfile.UnsupportedTaskFile, match="monkeylogic-bhv2"):
        taskfile.SyntheticTaskFileReader().trials(path)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/events/test_taskfile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.events.taskfile'`

- [ ] **Step 3: Implement**

Create `wl_preproc/events/taskfile.py`:

```python
# wl_preproc/events/taskfile.py
"""Reading the task file, behind a seam, because the stack is unchosen.

Spec section 4.2 states it outright: `acquisitionBuildId` "is a content hash of
a free-text {component: version} set, deliberately assuming no git -- so
{matlab, psychtoolbox, wl-bhvtask} and {bonsai, workflow} are the same shape.
The behavioural stack is unchosen and the design must not presume a resolvable
commit." The synthetic fixture says the same: it "stands in for MonkeyLogic's
.bhv2 until the task stack is chosen."

So this is a Protocol with one implementation today. A real `.bhv2` reader is a
SECOND implementation rather than a rewrite.

**What the seam is allowed to answer is deliberately narrow.** Codes own
timing; the task file owns parameters (spec section 2). So the protocol returns
ids, conditions and outcomes -- and NOT trial intervals. A reader that returned
times would invite someone to prefer them over the recorded strobe edges, which
is exactly the inversion section 4.2 requirement 5 forbids: the recorded edge
establishes true event time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class UnsupportedTaskFile(ValueError):
    """A task file this reader does not understand.

    Refused rather than parsed on a best-effort basis: a wrong trial list does
    not fail loudly, it disagrees with the codes -- and section 2 makes that
    disagreement a hard failure, which only works if the list is trustworthy.
    """


@dataclass(frozen=True, slots=True)
class TaskTrial:
    trial_id: int
    block_id: int
    condition: int
    outcome: str


class TaskFileReader(Protocol):
    """What this phase needs from a task file, and nothing else."""

    def trials(self, path: Path) -> list[TaskTrial]: ...


class SyntheticTaskFileReader:
    """Reads `synth/peripherals.py::write_task_file`'s format."""

    FORMAT = "synthetic-task-file"

    def trials(self, path: Path) -> list[TaskTrial]:
        payload = json.loads(path.read_text())
        declared = payload.get("format")
        if declared != self.FORMAT:
            raise UnsupportedTaskFile(
                f"{path} declares format {declared!r}; this reader handles "
                f"{self.FORMAT!r} only. A real .bhv2 reader is a second "
                "implementation of TaskFileReader, not a widening of this one."
            )
        return [
            TaskTrial(
                trial_id=int(t["trial_id"]),
                block_id=int(t["block_id"]),
                condition=int(t["condition"]),
                outcome=str(t["outcome"]),
            )
            for t in payload["trials"]
        ]
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/events/ -q`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/events/taskfile.py tests/events/test_taskfile.py
git commit -m "feat(events): the task-file reader seam, with the synthetic implementation"
```

---

### Task 6: Trial and block assembly — by ID, never by ordinal

**Files:**
- Create: `wl_preproc/events/assemble.py`
- Test: `tests/events/test_assemble.py`

**Interfaces:**
- Consumes: `contracts.events.decode_stream`, `SimpleEvent`, `PayloadEvent`, `DecodeError`, `Marker`, `Escape`, `TaskTypeCode`.
- Produces: `AssembledTrial(trial_id: int, start_s: float, end_s: float | None, outcome: str | None)`; `AssembledBlock(block_id: int, task_type: int, start_s: float, end_s: float | None)`; `assemble(events: list) -> Assembly` where `Assembly` has `.trials: list[AssembledTrial]`, `.blocks: list[AssembledBlock]`, `.errors: list[DecodeError]`.

**This task carries the phase's central correctness property.** Parent spec §4.2 requirement 1: every trial start is followed by an explicit trial-number payload, and *"trial matching is by ID, never by ordinal position — one dropped code must not shift every subsequent trial."*

- [ ] **Step 1: Write the failing test**

Create `tests/events/test_assemble.py`:

```python
"""Assembling decoded events into trials and measured blocks."""

from __future__ import annotations

from wl_preproc.contracts.events import Escape, Marker, encode_payload
from wl_preproc.events import assemble


def _stream(pairs):
    """(time_s, word) pairs -> decoded events, through the frozen codec."""
    from wl_preproc.contracts.events import decode_stream

    return decode_stream(pairs)


def _trial(t0: float, trial_id: int, outcome: Marker):
    """The three codes a trial start is at minimum: marker, id payload, checksum."""
    words = [(t0, Marker.TRIAL_START.value)]
    for offset, word in enumerate(
        encode_payload(Escape.TRIAL_NUMBER, [trial_id >> 16, trial_id & 0xFFFF])
    ):
        words.append((t0 + 0.001 * (offset + 1), word))
    words.append((t0 + 0.5, outcome.value))
    words.append((t0 + 0.51, Marker.TRIAL_END.value))
    return words


def test_trials_are_matched_by_id_not_by_position():
    """Spec section 4.2 requirement 1, and the whole reason payloads carry an
    explicit trial number: "one dropped code must not shift every subsequent
    trial."

    A dropped TRIAL_START marker must lose ONE trial, not renumber the rest.
    An ordinal implementation passes the happy path and fails here.
    """
    words = []
    for trial_id in (1, 2, 3):
        words.extend(_trial(t0=trial_id * 10.0, trial_id=trial_id, outcome=Marker.TRIAL_CORRECT))

    # Drop trial 2's opening marker -- the code that a lossy line loses.
    words = [w for w in words if not (w[0] == 20.0 and w[1] == Marker.TRIAL_START.value)]

    result = assemble.assemble(_stream(words))
    ids = [t.trial_id for t in result.trials]
    assert 1 in ids and 3 in ids, f"trials 1 and 3 must survive; got {ids}"
    assert 3 in ids, "trial 3 must keep its OWN id, not inherit trial 2's slot"


def test_a_block_start_payload_carries_its_task_type():
    """BLOCK_START's payload is (block_number, task_type_code), so a block is
    self-describing in the recording "even when the ELN is wrong or late" --
    contracts/events.py's own words."""
    from wl_preproc.contracts.events import TaskTypeCode

    words = [(0.0, w) for w in encode_payload(
        Escape.BLOCK_START, [7, TaskTypeCode.RF_MAP.value]
    )]
    words = [(0.001 * i, w) for i, (_, w) in enumerate(words)]
    words.append((5.0, Marker.BLOCK_END.value))

    result = assemble.assemble(_stream(words))
    assert len(result.blocks) == 1
    assert result.blocks[0].block_id == 7
    assert result.blocks[0].task_type == TaskTypeCode.RF_MAP.value


def test_decode_errors_are_kept_rather_than_dropped():
    """decode_stream never raises: "a corrupt or truncated payload yields a
    DecodeError and decoding continues, so one bad trial cannot lose a
    session." The assembly must surface those, because a session with decode
    errors is a tier-D candidate and silence would hide it."""
    words = [(0.0, Escape.TRIAL_NUMBER.value), (0.001, 1), (0.002, 0xDEAD)]  # bad checksum
    result = assemble.assemble(_stream(words))
    assert result.errors, "a checksum failure must reach the assembly's error list"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/events/test_assemble.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.events.assemble'`

- [ ] **Step 3: Implement**

Create `wl_preproc/events/assemble.py`:

```python
# wl_preproc/events/assemble.py
"""Decoded events -> trials and measured blocks.

**Matching is by ID and never by ordinal position.** Spec section 4.2
requirement 1 is explicit about why every trial start carries an explicit
trial-number payload: "one dropped code must not shift every subsequent trial."
An implementation that counted TRIAL_START markers would pass every happy-path
test and silently renumber a whole session the first time a line glitched --
and the renumbering would be invisible, because the trial count would still
look plausible.

DecodeErrors are carried through rather than dropped. `decode_stream` "never
raises on malformed input... so one bad trial cannot lose a session", and a
session with decode errors is a tier-D candidate that silence would hide.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wl_preproc.contracts.events import (
    DecodeError,
    DecodedEvent,
    Escape,
    Marker,
    PayloadEvent,
    SimpleEvent,
)

_OUTCOMES = {
    Marker.TRIAL_CORRECT: "correct",
    Marker.TRIAL_ERROR: "error",
    Marker.TRIAL_ABORT: "abort",
    Marker.TRIAL_FIXATION_BREAK: "fixation_break",
    Marker.TRIAL_NO_RESPONSE: "no_response",
}


@dataclass(frozen=True, slots=True)
class AssembledTrial:
    trial_id: int
    start_s: float
    end_s: float | None
    outcome: str | None


@dataclass(frozen=True, slots=True)
class AssembledBlock:
    block_id: int
    task_type: int
    start_s: float
    end_s: float | None


@dataclass
class Assembly:
    trials: list[AssembledTrial] = field(default_factory=list)
    blocks: list[AssembledBlock] = field(default_factory=list)
    errors: list[DecodeError] = field(default_factory=list)


def _u32(words: tuple[int, ...]) -> int:
    """Two 16-bit words, high first -- contracts/events.py's own convention."""
    return (words[0] << 16) | words[1]


def assemble(events: list[DecodedEvent]) -> Assembly:
    """Trials and measured blocks from a decoded stream."""
    result = Assembly()

    open_trial_start: float | None = None
    open_trial_id: int | None = None
    open_outcome: str | None = None
    open_block: AssembledBlock | None = None

    def close_trial(end_s: float | None) -> None:
        nonlocal open_trial_start, open_trial_id, open_outcome
        if open_trial_id is not None and open_trial_start is not None:
            result.trials.append(
                AssembledTrial(
                    trial_id=open_trial_id,
                    start_s=open_trial_start,
                    end_s=end_s,
                    outcome=open_outcome,
                )
            )
        open_trial_start = open_trial_id = open_outcome = None

    for event in events:
        if isinstance(event, DecodeError):
            result.errors.append(event)
            continue

        if isinstance(event, PayloadEvent):
            if event.escape is Escape.TRIAL_NUMBER:
                # The ID arrives here, never from a running count. A TRIAL_START
                # whose payload was lost therefore yields NO trial rather than a
                # misnumbered one.
                open_trial_id = _u32(event.words)
                if open_trial_start is None:
                    open_trial_start = event.time_s
            elif event.escape is Escape.BLOCK_START:
                if open_block is not None:
                    result.blocks.append(open_block)
                open_block = AssembledBlock(
                    block_id=event.words[0],
                    task_type=event.words[1],
                    start_s=event.time_s,
                    end_s=None,
                )
            continue

        if isinstance(event, SimpleEvent):
            try:
                marker = Marker(event.code)
            except ValueError:
                continue  # a task event, not a marker; Event rows keep it
            if marker is Marker.TRIAL_START:
                close_trial(end_s=None)
                open_trial_start = event.time_s
            elif marker in _OUTCOMES:
                open_outcome = _OUTCOMES[marker]
            elif marker is Marker.TRIAL_END:
                close_trial(end_s=event.time_s)
            elif marker is Marker.BLOCK_END and open_block is not None:
                result.blocks.append(
                    AssembledBlock(
                        block_id=open_block.block_id,
                        task_type=open_block.task_type,
                        start_s=open_block.start_s,
                        end_s=event.time_s,
                    )
                )
                open_block = None

    close_trial(end_s=None)
    if open_block is not None:
        result.blocks.append(open_block)
    return result
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/events/ -q`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/events/assemble.py tests/events/test_assemble.py
git commit -m "feat(events): assemble trials and blocks, matched by ID and never by ordinal"
```

---

### Task 7: The three agreement metrics, and the tier

**Files:**
- Create: `wl_preproc/events/agreement.py`
- Test: `tests/events/test_agreement.py`

**Interfaces:**
- Consumes: `WordStream` / `StrobeWitness` (Tasks 2–4), `Assembly` (Task 6), `TaskTrial` (Task 5).
- Produces: `TierInputs(event_code_agreement: float | None, trial_count_agreement: bool | None, camera_trigger_count: int | None, n_full_code_records: int, n_strobe_witnesses: int, decode_errors: int)` and `resolve_tier(inputs: TierInputs) -> str` returning `"A" | "B" | "C" | "D"`.

- [ ] **Step 1: Write the failing test**

Create `tests/events/test_agreement.py`:

```python
"""The three tier inputs, and the verdict they decide. Spec section 4.7."""

from __future__ import annotations

import pytest

from wl_preproc.events import agreement


def _inputs(**over):
    base = dict(
        event_code_agreement=1.0,
        trial_count_agreement=True,
        camera_trigger_count=0,
        n_full_code_records=2,
        n_strobe_witnesses=1,
        decode_errors=0,
    )
    base.update(over)
    return agreement.TierInputs(**base)


def test_tier_a_needs_two_full_code_records_that_agree():
    assert agreement.resolve_tier(_inputs()) == "A"


def test_one_full_code_record_plus_a_witness_is_b():
    """Spec section 4.7: "1 full-code record + >=1 independent strobe witness".
    The standalone-Intan topology."""
    assert agreement.resolve_tier(
        _inputs(n_full_code_records=1, event_code_agreement=None)
    ) == "B"


def test_one_full_code_record_alone_is_c():
    """"1 full-code record, cross-checked only against task file" -- behaviour-
    only training, where the Pi is the sole recorder."""
    assert agreement.resolve_tier(
        _inputs(n_full_code_records=1, n_strobe_witnesses=0, event_code_agreement=None)
    ) == "C"


def test_disagreeing_trial_counts_are_D_not_a_lower_tier():
    """Spec section 2: "codes own timing; task file owns parameters;
    cross-validated, HARD-FAIL on mismatch." A disagreement is a failed check,
    and section 4.7 puts any failed check at D -- it does not demote to C."""
    assert agreement.resolve_tier(_inputs(trial_count_agreement=False)) == "D"


def test_decode_errors_are_D():
    assert agreement.resolve_tier(_inputs(decode_errors=1)) == "D"


def test_two_records_that_disagree_are_D_rather_than_B():
    """Two full-code records that disagree is a FAILED check, not a session
    with one usable record. Demoting to B would silently prefer whichever
    record the implementation happened to read first."""
    assert agreement.resolve_tier(_inputs(event_code_agreement=0.5)) == "D"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/events/test_agreement.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.events.agreement'`

- [ ] **Step 3: Implement**

Create `wl_preproc/events/agreement.py`:

```python
# wl_preproc/events/agreement.py
"""The three inputs section 4.7's tiers turn on, and the verdict.

`TimingProvenance` in 1c-4 recorded `tier = 'pending'` and named exactly what
it was waiting for: `event_code_agreement,trial_count_agreement,
camera_trigger_count`. This module supplies them.

**The tier is derived, never asserted** (section 4.7): every underlying count
is retained on the row so the verdict can be re-derived under different
thresholds later. `resolve_tier` therefore takes only the measured inputs and
holds no state of its own.
"""

from __future__ import annotations

from dataclasses import dataclass

# Two independent records must agree on this fraction of their codes to count
# as agreeing at all. Stated here rather than inlined so the threshold is one
# named number a later session can move without hunting -- section 4.7's whole
# point about re-derivation.
AGREEMENT_THRESHOLD = 0.999


@dataclass(frozen=True, slots=True)
class TierInputs:
    event_code_agreement: float | None
    trial_count_agreement: bool | None
    camera_trigger_count: int | None
    n_full_code_records: int
    n_strobe_witnesses: int
    decode_errors: int


def resolve_tier(inputs: TierInputs) -> str:
    """A, B, C or D, per spec section 4.7's table.

    **D is checked first and wins outright.** Section 4.7 defines D as "any
    check failed", so a failure is not a demotion to the next tier down -- two
    records that disagree is a failed check, not a session with one good
    record, and treating it as B would silently prefer whichever record was
    read first.
    """
    if inputs.decode_errors:
        return "D"
    if inputs.trial_count_agreement is False:
        return "D"
    if inputs.event_code_agreement is not None and (
        inputs.event_code_agreement < AGREEMENT_THRESHOLD
    ):
        return "D"

    if inputs.n_full_code_records >= 2:
        return "A"
    if inputs.n_full_code_records == 1 and inputs.n_strobe_witnesses >= 1:
        return "B"
    if inputs.n_full_code_records == 1:
        return "C"
    return "D"
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/events/ -q`
Expected: PASS, 16 tests.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/events/agreement.py tests/events/test_agreement.py
git commit -m "feat(events): the three tier inputs, and a D that wins outright"
```

---

### Task 8: Populate the element-event tables

**Files:**
- Create: `wl_preproc/schema/events.py`
- Test: `tests/schema/test_events.py`

**Interfaces:**
- Consumes: everything from Tasks 2–7.
- Produces: `wl_preproc.schema.events.activate(prefix)` and `populate_session(key: dict, session_dir: Path) -> None`, which inserts `BehaviorRecording`, `EventType`, `Event`, `Trial`, `TrialType`, `Block` and `BlockTrial` for one session.

**Read this before writing any insert.** `element_event.event.Event.make()` does not populate — it raises:

```
NotImplementedError("For `insert`, use `allow_direct_insert=True`")
```

Verified against the installed package. So these `Imported` tables are filled by **direct insert with `allow_direct_insert=True`**, which is element-event's own documented pattern, not by `populate()`. Do not write a `make()` for them and do not call `.populate()` on them.

- [ ] **Step 1: Write the failing test**

Create `tests/schema/test_events.py`:

```python
"""Populating element-event's tables from a decoded session."""

from __future__ import annotations

import pytest

from wl_preproc.schema import events


@pytest.fixture(scope="module")
def events_activated(dj_conn, prefix):
    events.activate(prefix=prefix)
    return events


def test_event_types_are_projected_from_the_frozen_marker_enum(events_activated):
    """EventType is a projection of contracts/events.Marker, not a hand-typed
    second list. A marker added to the frozen contract must appear here by
    construction -- a hand-listed copy is the shape that has been missed three
    times in this repository (ingest, timebase, ephys)."""
    from wl_preproc.contracts.events import Marker
    from wl_preproc.schema import pipeline

    events.sync_event_types()
    stored = set(pipeline.event.EventType.to_arrays("event_type"))
    assert {m.name for m in Marker} <= stored


def test_behavior_recording_is_one_per_session_by_construction(events_activated):
    """element-event declares BehaviorRecording as `-> Session` with NO
    additional key attribute, so its primary key IS the session key. That is
    what makes 'relative to recording start' the same number as session time
    (t=0 at the first barcode) -- and a second recording per session is
    unrepresentable rather than merely discouraged."""
    from wl_preproc.schema import pipeline

    assert pipeline.event.BehaviorRecording.primary_key == [
        "subject",
        "session_datetime",
    ]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_events.py -v`
Expected: FAIL — `ImportError: cannot import name 'events' from 'wl_preproc.schema'`

- [ ] **Step 3: Implement**

Create `wl_preproc/schema/events.py`:

```python
# wl_preproc/schema/events.py
"""Population of the adopted event and trial tables. Declares no table of its own.

**This module adds no `@schema` class**, and that is a finding rather than an
omission: every table Phase 1c-5 fills already exists. `BehaviorRecording`,
`EventType`, `Event`, `Trial`, `TrialType`, `Block` and `BlockTrial` come from
element-event; `TrialCoverage` was declared in 1c-1 and converted to `Computed`
in 1c-4 specifically so this phase would not have to migrate it.

**element-event's Imported tables are filled by direct insert, not by
`populate()`.** `Event.make()` raises `NotImplementedError("For `insert`, use
`allow_direct_insert=True`")` -- checked against the installed package. So this
module inserts, and nothing here defines a `make()` for them.
"""

from __future__ import annotations

from pathlib import Path

from wl_preproc.contracts.events import Marker
from wl_preproc.schema import DEFAULT_PREFIX, core, pipeline


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Ensure the tables this module fills are bound. Idempotent.

    No `schema.activate` of its own -- there is no schema to activate, because
    this module declares no table.
    """
    pipeline.activate(prefix=prefix)
    core.activate(prefix=prefix)


def sync_event_types() -> None:
    """Project `contracts.events.Marker` into element-event's `EventType`.

    A projection rather than a hand-written list: the contract is frozen (spec
    section 3.5 item 4), so a marker added there must appear here without
    anyone remembering to. The hand-listed alternative is the shape this
    repository has missed three times.
    """
    pipeline.event.EventType.insert(
        [{"event_type": marker.name, "event_type_description": ""} for marker in Marker],
        skip_duplicates=True,
    )
```

Then add `populate_session(key, session_dir)` wiring Tasks 2–7 together: extract per system, convert native time to session time via `timebase/fit.py`, decode, assemble, and insert `BehaviorRecording` → `Event` → `Trial`/`TrialType` → `Block`/`BlockTrial`, every insert carrying `allow_direct_insert=True` for the `Imported` ones.

**Scalars only into `Event`.** Never write an array to `Event.Attribute` — it is one of the four allow-listed bare `longblob`s and would be stored as its string repr with nothing raising.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/schema/test_events.py -v`
Expected: PASS.

- [ ] **Step 5: Check the module sweep, and do NOT hand-edit a list to fix it**

Run: `.venv/bin/python -m pytest tests/schema/test_daemon.py tests/schema/test_guardrails.py -q`

`wl_preproc/schema/events.py` is an **eighth** module under `wl_preproc/schema/`, so `_discover_schema_modules()` will find it and `test_every_schema_module_is_swept_for_job_tables` will compare it against `daemon.py`'s hand-written `_PROJECT_SCHEMA_MODULES`. **That tuple has now been missed three times** — `ingest` (1c-2), `timebase` (1c-4), `ephys` (Phase 2a).

If that test fails, add `("events", events)` to the tuple in alphabetical position **and** its import, and extend the comment above it. Add no populate stage: this module declares no Computed table, so it owns no `~jobs` table.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/events.py tests/schema/test_events.py wl_preproc/daemon.py
git commit -m "feat(events): populate the adopted event and trial tables by direct insert"
```

---

### Task 9: TrialCoverage, and the tier resolved

**Files:**
- Modify: `wl_preproc/schema/coverage.py` (add `TrialCoverage.make`), `wl_preproc/schema/timebase.py`
- Test: `tests/schema/test_coverage.py`, `tests/schema/test_timebase.py`

**Interfaces:**
- Consumes: `timebase.coverage.classify_coverage(block, segments) -> tuple[str, float]`; `events.agreement.resolve_tier`.
- Produces: a populated `TrialCoverage`, and `TimingProvenance.tier` in `{A,B,C,D}` with `pending_inputs == ""`.

- [ ] **Step 1: Write the failing test**

Append to `tests/schema/test_timebase.py`:

```python
def test_the_tier_leaves_pending_once_the_three_inputs_exist(dj_conn, prefix):
    """1c-4 recorded `tier='pending'` and named exactly what it waited for:
    event_code_agreement, trial_count_agreement, camera_trigger_count. This
    phase supplies them, so no session may still read 'pending' afterwards --
    and `pending_inputs` must be emptied rather than left describing a wait
    that is over."""
    from wl_preproc.schema import timebase

    rows = timebase.TimingProvenance.to_dicts()
    assert rows, "no provenance rows to check"
    for row in rows:
        assert row["tier"] in {"A", "B", "C", "D"}, f"still pending: {row}"
        if row["tier"] != "pending":
            assert row["pending_inputs"] == "", (
                "pending_inputs must be empty once the tier is decided; it "
                f"still reads {row['pending_inputs']!r}"
            )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_timebase.py -k tier_leaves_pending -v`
Expected: FAIL — rows still carry `'pending'`.

- [ ] **Step 3: Implement `TrialCoverage.make`**

In `wl_preproc/schema/coverage.py`, give `TrialCoverage` a `make()` and a `key_source` mirroring `BlockCoverage`'s, calling `timebase.coverage.classify_coverage` — **the same function `BlockCoverage` calls.** Do not write a second interval rule; that module's docstring already says both tables call it *"so the rule has one definition rather than one per table."*

- [ ] **Step 4: Resolve the tier**

In `wl_preproc/schema/timebase.py`, extend `TimingProvenance.make()` to call `events.agreement.resolve_tier` with the measured inputs, store them on the row, and set `pending_inputs = ""`. Every underlying count stays on the row: spec §4.7 requires the tier be *"derived, not asserted"* and re-derivable under different thresholds.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/schema/ -q`
Expected: PASS, fully green.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/coverage.py wl_preproc/schema/timebase.py tests/schema/
git commit -m "feat(events): TrialCoverage populated, and the tier resolved from pending"
```

---

### Task 10: Sweep the documents this phase makes false

**Files:**
- Modify: `docs/CHECKPOINT.md`, `wl_preproc/schema/timebase.py` (the `PENDING_TIER_INPUTS` comment), `docs/superpowers/specs/2026-08-12-wl-preproc-design.md` (§4.7)

**Why its own task:** *"fixing code without sweeping the document that describes it"* is a trap this project has paid for four times. Each item below is a statement that this phase makes untrue.

- [ ] **Step 1: CHECKPOINT — the measured block boundary**

It records the measured boundary as living *"in its own Computed table"*. It lives in element-event's `trial.Block`, cross-validated against `core.Block`. Correct it and cite spec §5.

- [ ] **Step 2: `PENDING_TIER_INPUTS` and its comment**

`wl_preproc/schema/timebase.py` carries the constant and a comment describing *"what this phase cannot compute"*. That wait is over. Rewrite both to say what the tier now derives from, keeping the record that it was once pending — the comment's reasoning about naming what is missing rather than omitting it is still the right principle and should survive.

- [ ] **Step 3: Parent spec §4.7**

Record that the tier is computed in 1c-5, and name each input's source: `event_code_agreement` from Pi versus NI word streams, `trial_count_agreement` from codes versus the task file, `camera_trigger_count` from the behaviour-camera sidecar.

- [ ] **Step 4: Full suite, both interpreters**

Run:
```bash
.venv/bin/python -m pytest -q
.venv/bin/pytest -q
```
Expected: fully green, zero warnings, both invocations. Then repeat on 3.13 however CI does it.

- [ ] **Step 5: Commit**

```bash
git add docs/ wl_preproc/schema/timebase.py
git commit -m "docs: sweep what 1c-5 makes false — the tier is no longer pending"
```

---

## Spec coverage

| Spec section | Task |
|---|---|
| §2.1 the missing NI fixture | 1 |
| §3 `BehaviorRecording` is the session | 8 |
| §3 `EventType` from `Marker` | 8 |
| §4.1 `Event`, `Trial`, `TrialType`, `Block`, `BlockTrial` | 8 |
| §4.2 the eighth module and the daemon list | 8 step 5 |
| §5 the measured block boundary | 6 (assembly), 8 (insert), 10 (CHECKPOINT) |
| §6 the task-file reader seam | 5 |
| §6 trial matching by ID never ordinal | 6 |
| §7 the three tier inputs | 7 |
| §7 the RHS witness counts | 4 |
| §7 tier resolution | 9 |
| §8 testing | throughout; each task carries its own |
| §11 amendments | 10 |

**Not covered, deliberately:** §10's two open questions — `EventType` descriptions and the full-code-record threshold — are decided at implementation and recorded then, per the spec's own framing.
