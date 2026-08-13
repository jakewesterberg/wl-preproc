# Phase 1b2 — A Real Intan RHS Header: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase 1b's 20-byte `info.rhs` identification stub with a byte-correct Standard Intan RHS header, so `spikeinterface.extractors.read_intan` can open the synthetic sessions and the reader-as-oracle test that verified SpikeGLX finally applies to RHS too.

**Architecture:** A new `wl_preproc/synth/rhs_header.py` owns the header format — QString encoding, the 28-field global block, eight signal groups, fifteen fields per channel — leaving `rhs.py` responsible for waveforms. Channel *identity* (name, impedance) becomes device-neutral data on `SessionRecipe`; Intan's hardware bookkeeping (chip channel, board stream, spike-scope defaults, port grouping) derives inside the header writer and never touches the shared recipe.

**Tech Stack:** Python 3.11+, NumPy, pytest, SpikeInterface/neo (test-only, as the format oracle)

**Spec:** [`../specs/2026-08-12-wl-preproc-design.md`](../specs/2026-08-12-wl-preproc-design.md) §6.3, §9, §11.6

**Vendor document:** Intan *RHS Application Note: Data File Formats*, 7 July 2017 / updated 29 April 2022, <https://intantech.com/files/Intan_RHS2000_data_file_formats.pdf>. Header layout on pages 2–4; One File Per Signal Type on pages 6–8. **Field order below was transcribed from that document and cross-checked field-by-field against `neo.rawio.intanrawio`'s `rhs_global_header` / `rhs_signal_group_header` / `rhs_signal_channel_header` tables; they agree exactly.** That cross-check is what makes validating against neo non-circular.

**Depends on:** Phase 1b (merged, `b884439`) — `write_rhs`, `STIM_RECIPE`, and the strict-xfail tripwire this plan flips to a real assertion.

## Global Constraints

- **Python ≥3.11**, no upper cap.
- **Run the suite as `.venv/bin/python -m pytest` from the repo root.** The `.venv/bin/pytest` console script cannot import `wl_preproc` in this checkout.
- **Little-endian throughout.** Every integer and float is little-endian; `struct` format strings use the `<` prefix.
- **`single` in the vendor document means `float32`** (`struct` code `f`), never `float64`.
- **QString is a `uint32` *byte* length followed by UTF-16 characters**, with `0xFFFFFFFF` meaning null. The length is in **bytes**, not characters — the vendor's own MATLAB divides it by 2 to get the character count.
- **`board_mode` must be `14`.** The vendor document states this is set by hardware and "should always be 14 for the Intan Stim/Record Controller".
- **`dc_amplifier_data_saved` must be `0`.** §6.3 keeps `dcamplifier.dat` unwritten because its dtype is genuinely unresolved. This field is the format's own way of declaring that, so the header must *say* it rather than leaving it implied by the file's absence.
- **Declare only signal types whose `.dat` file we actually write.** neo maps declared signal types to expected filenames (`rhs_signal_channel_header` consumers: amplifier → `amplifier.dat`, ADC in → `analogin.dat`, digital in → `digitalin.dat`, …). Declaring a board ADC input channel makes the reader look for `analogin.dat`, which this generator does not emit. **Only signal type 0 (RHS2000 amplifier) and signal type 5 (digital input) may appear.**
- **The stim file is `stim.dat`.** The vendor document contradicts itself — the section heading on page 8 reads `stim.data` while its own MATLAB on page 9 opens `stim.dat`, and neo expects `stim.dat`. Two sources against one heading; Phase 1b already writes `stim.dat` and that is correct. **Do not rename it.**
- **Ground truth is returned, never re-derived.** Tests assert recovery through the reader, not against generator internals.
- **Determinism is a test.** Same recipe plus seed produces byte-identical output.

---

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/synth/rhs_header.py` | **New.** QString encoding, the global header block, signal groups, per-channel records. Owns the Intan header format and nothing else. |
| Modify `wl_preproc/synth/recipe.py` | `ChannelSpec`; `SessionRecipe.channels`; a validator tying it to `n_ap_channels`. |
| Modify `wl_preproc/synth/rhs.py` | Delete `_write_header`; call the new writer. Waveform emission is untouched. |
| `tests/synth/test_rhs_header.py` | **New.** Byte-level tests: QString, field offsets, group structure. |
| Modify `tests/synth/test_recipe.py` | `ChannelSpec` defaults and validation. |
| Modify `tests/synth/test_rhs.py` | Flip the strict xfail into a real reader-as-oracle test. |

**Why a separate module.** `rhs.py` is ~180 lines and currently has one job: render waveforms into flat arrays. The header is ~150 lines of unrelated binary-format machinery with its own vocabulary. Keeping them apart means a reviewer can hold either in context alone, and a future traditional-format emitter can reuse the header writer unchanged.

---

### Task 1: QString encoding and the primitive writers

**Files:**
- Create: `wl_preproc/synth/rhs_header.py`
- Test: `tests/synth/test_rhs_header.py`

**Interfaces:**
- Consumes: nothing
- Produces: `qstring(text: str | None) -> bytes`; `NULL_QSTRING_LENGTH = 0xFFFFFFFF`

- [ ] **Step 1: Write the failing test**

```python
# tests/synth/test_rhs_header.py
import struct

from wl_preproc.synth.rhs_header import NULL_QSTRING_LENGTH, qstring


def _decode_like_neo(raw: bytes) -> tuple[str, int]:
    """Decode exactly the way neo.rawio.intanrawio.read_qstring does, so these
    tests fail if our encoder and the reader ever disagree."""
    length = struct.unpack("<I", raw[:4])[0]
    if length in (NULL_QSTRING_LENGTH, 0):
        return "", 4
    return raw[4 : 4 + length].decode("utf-16"), 4 + length


def test_ascii_round_trips():
    text, consumed = _decode_like_neo(qstring("A-000"))
    assert text == "A-000"
    assert consumed == 4 + 10


def test_length_is_in_bytes_not_characters():
    """The vendor document's own MATLAB divides this by 2 to get the character
    count, so a character count here would halve every string on read."""
    raw = qstring("ABCD")
    assert struct.unpack("<I", raw[:4])[0] == 8


def test_none_is_the_null_sentinel():
    raw = qstring(None)
    assert struct.unpack("<I", raw[:4])[0] == NULL_QSTRING_LENGTH
    assert len(raw) == 4


def test_empty_string_is_not_the_null_sentinel():
    raw = qstring("")
    assert struct.unpack("<I", raw[:4])[0] == 0
    assert len(raw) == 4


def test_non_ascii_survives():
    text, _ = _decode_like_neo(qstring("Port Å"))
    assert text == "Port Å"


def test_no_byte_order_mark_is_emitted():
    """neo decodes with codec 'utf-16', which honours a BOM if present. Emitting
    one would prepend a zero-width character to every string it reads."""
    raw = qstring("AB")
    assert raw[4:] == b"A\x00B\x00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/synth/test_rhs_header.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.synth.rhs_header'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/synth/rhs_header.py
"""The Standard Intan RHS header, written byte-for-byte.

Field order and types are transcribed from Intan's *RHS Application Note: Data
File Formats* (7 July 2017, updated 29 April 2022), pages 2-4, and were
cross-checked field-by-field against neo's ``rhs_global_header``,
``rhs_signal_group_header`` and ``rhs_signal_channel_header`` tables. The two
agree exactly, which is what makes validating the output with neo a real check
rather than a circular one.

Everything is little-endian. The document's ``single`` is float32.
"""

from __future__ import annotations

import struct

NULL_QSTRING_LENGTH = 0xFFFFFFFF


def qstring(text: str | None) -> bytes:
    """Qt-style length-prefixed Unicode string.

    A uint32 byte length followed by UTF-16 characters. ``None`` encodes the
    null sentinel 0xFFFFFFFF; an empty string encodes a zero length, and the
    two are distinct on the wire even though neo maps both to "".

    The length is in BYTES. The vendor document's MATLAB divides it by two to
    recover the character count, so writing a character count would truncate
    every string in half on read. Encoded UTF-16-LE with no byte-order mark,
    because neo decodes with the ``utf-16`` codec, which would consume a BOM as
    content-affecting metadata.
    """
    if text is None:
        return struct.pack("<I", NULL_QSTRING_LENGTH)
    payload = text.encode("utf-16-le")
    return struct.pack("<I", len(payload)) + payload
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/synth/test_rhs_header.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/synth/rhs_header.py tests/synth/test_rhs_header.py
git commit -m "feat(synth): QString encoding for the Intan RHS header"
```

---

### Task 2: Device-neutral channel identity on the recipe

**Files:**
- Modify: `wl_preproc/synth/recipe.py`
- Test: `tests/synth/test_recipe.py`

**Interfaces:**
- Consumes: nothing
- Produces: `ChannelSpec(name: str, impedance_ohms: float = 1_000_000.0, impedance_phase_deg: float = 0.0, enabled: bool = True)`; `SessionRecipe.channels: tuple[ChannelSpec, ...] = ()`; `SessionRecipe.resolved_channels() -> tuple[ChannelSpec, ...]`

**Why this is on the recipe and the rest is not.** `SessionRecipe`'s docstring says a fixture should be data rather than a call with thirty keyword arguments, and which channels exist is fixture data — §11.6's derived-vs-recorded channel map comparison will eventually want to emit a deliberately *wrong* map, which means the map has to be declarable. But Intan's `chip_channel`, `command_stream`, `board_stream` and spike-scope fields are one vendor's internal wiring, and `SessionRecipe` also describes Neuropixels and eye-tracking sessions. The test applied throughout: *could another emitter use this field?* Impedance yes; `command_stream` no.

`SessionRecipe` is **not** a frozen §3.5 interface and has no exported JSON schema — `write_manifest` translates it into `SessionManifest`, which is the published contract. Adding an optional field here costs nothing downstream.

- [ ] **Step 1: Write the failing test**

Append to `tests/synth/test_recipe.py`:

```python
def test_channels_default_to_empty_and_resolve_from_the_channel_count():
    from wl_preproc.synth.recipe import CI_RECIPE

    assert CI_RECIPE.channels == ()
    resolved = CI_RECIPE.resolved_channels()
    assert len(resolved) == CI_RECIPE.n_ap_channels
    assert [c.name for c in resolved] == ["A-000", "A-001", "A-002", "A-003"]
    assert all(c.enabled for c in resolved)


def test_explicit_channels_are_returned_unchanged():
    from wl_preproc.synth.recipe import CI_RECIPE, ChannelSpec

    named = tuple(
        ChannelSpec(name=f"B-{i:03d}", impedance_ohms=2.5e6) for i in range(4)
    )
    recipe = CI_RECIPE.model_copy(update={"channels": named})
    assert recipe.resolved_channels() == named


def test_channel_count_must_match_the_channel_number():
    """A recipe whose declared channels disagree with n_ap_channels would emit a
    header describing a different array than amplifier.dat actually contains."""
    import pytest
    from pydantic import ValidationError

    from wl_preproc.synth.recipe import (
        BlockSpec,
        ChannelSpec,
        MontageSpec,
        SessionRecipe,
    )
    from wl_preproc.contracts.events import TaskTypeCode

    with pytest.raises(ValidationError):
        SessionRecipe(
            session_id="2027-03-14_09",
            subject="pico",
            rig="rig-a",
            systems=("syncbox", "rhs"),
            blocks=(
                BlockSpec(
                    task_type=TaskTypeCode.RF_MAP, n_trials=1, trial_duration_s=3.0
                ),
            ),
            montages=(MontageSpec(start_s=0.0, end_s=3.0),),
            n_ap_channels=4,
            ap_sample_rate_hz=30_000.0,
            seed=1,
            channels=(ChannelSpec(name="A-000"),),  # 1 channel, 4 declared
        )


def test_impedance_must_be_positive():
    import pytest
    from pydantic import ValidationError

    from wl_preproc.synth.recipe import ChannelSpec

    with pytest.raises(ValidationError):
        ChannelSpec(name="A-000", impedance_ohms=-1.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/synth/test_recipe.py -v`
Expected: FAIL — `ImportError: cannot import name 'ChannelSpec'`

- [ ] **Step 3: Write the implementation**

In `wl_preproc/synth/recipe.py`, add `Field` to the pydantic import, then add `ChannelSpec` immediately before `BlockSpec`:

```python
class ChannelSpec(BaseModel):
    """One recorded channel's identity, in device-neutral terms.

    Name and impedance are things any acquisition system has. Intan's wiring
    bookkeeping — chip channel, command and board stream, spike-scope defaults —
    is deliberately absent: it is one vendor's internal model, and this recipe
    also describes Neuropixels sessions. The RHS header writer derives those.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    impedance_ohms: float = Field(default=1_000_000.0, gt=0.0)
    impedance_phase_deg: float = 0.0
    enabled: bool = True
```

Add the field to `SessionRecipe`, after `n_ap_channels`:

```python
    channels: tuple[ChannelSpec, ...] = ()
```

Add the resolver as a method on `SessionRecipe`, beside `duration_s`:

```python
    def resolved_channels(self) -> tuple[ChannelSpec, ...]:
        """The channels this session records, defaulting to Intan's Port A
        naming when the recipe does not declare them explicitly.

        Defaulting here rather than in the emitter means every consumer sees the
        same list, and a recipe that *does* declare channels overrides it wholly.
        """
        if self.channels:
            return self.channels
        return tuple(
            ChannelSpec(name=f"A-{i:03d}") for i in range(self.n_ap_channels)
        )
```

Extend the existing `_coherent` validator, before its `return self`:

```python
        if self.channels and len(self.channels) != self.n_ap_channels:
            raise ValueError(
                f"channels declares {len(self.channels)} channels but "
                f"n_ap_channels is {self.n_ap_channels}; a header describing a "
                f"different array than amplifier.dat contains is unreadable"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/synth/test_recipe.py -v && .venv/bin/python -m pytest -q`
Expected: PASS — the 4 new tests, and the existing suite (148 passed, 1 xfailed) unchanged

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/synth/recipe.py tests/synth/test_recipe.py
git commit -m "feat(synth): device-neutral channel identity on the session recipe"
```

---

### Task 3: The header itself

**Files:**
- Modify: `wl_preproc/synth/rhs_header.py`
- Test: `tests/synth/test_rhs_header.py`

**Interfaces:**
- Consumes: `qstring`; `SessionRecipe`, `ChannelSpec`
- Produces: `write_rhs_header(path: Path, recipe: SessionRecipe, sample_rate_hz: float, stim_step_size_a: float, digital_input_bits: tuple[int, ...]) -> None`; `SIGNAL_TYPE_AMPLIFIER = 0`; `SIGNAL_TYPE_DIGITAL_IN = 5`; `BOARD_MODE_STIM_RECORD = 14`; `MAGIC = 0xD69127AC`; `GROUP_NAMES` (the eight ports)

**The one structural rule.** neo turns each declared channel's signal type into an expected `.dat` filename. Declare a board ADC input and it will look for `analogin.dat`, which this generator never writes. Only signal type 0 (amplifier → `amplifier.dat`) and 5 (digital input → `digitalin.dat`) may appear. The other six groups are still written — the vendor document says a file typically has eight — but with `enabled = 0` and zero channels, so no channel records follow them.

- [ ] **Step 1: Write the failing test**

Append to `tests/synth/test_rhs_header.py`:

Put these imports at the top of the file beside the existing `import struct`,
not in the middle where the appended tests begin:

```python
import pytest

from wl_preproc.synth.recipe import STIM_RECIPE
from wl_preproc.synth.rhs_header import (
    BOARD_MODE_STIM_RECORD,
    MAGIC,
    SIGNAL_TYPE_AMPLIFIER,
    SIGNAL_TYPE_DIGITAL_IN,
    write_rhs_header,
)


@pytest.fixture
def header_bytes(tmp_path):
    path = tmp_path / "info.rhs"
    write_rhs_header(
        path,
        STIM_RECIPE,
        sample_rate_hz=30_000.0,
        stim_step_size_a=10e-6,
        digital_input_bits=(0, 1),
    )
    return path.read_bytes()


def test_begins_with_the_magic_number_and_version(header_bytes):
    magic, major, minor = struct.unpack("<Ihh", header_bytes[:8])
    assert magic == MAGIC
    assert (major, minor) >= (1, 0)


def test_sample_rate_is_float32_at_offset_eight(header_bytes):
    """The vendor document puts sample rate immediately after the version pair.
    Reading it as float64 here would shift every subsequent field."""
    assert struct.unpack("<f", header_bytes[8:12])[0] == pytest.approx(30_000.0)


def test_neo_parses_the_whole_header_without_running_off_the_end(header_bytes):
    """Parse with neo's own field tables. This is the check that the field order
    and types match the reader exactly — a wrong dtype anywhere desynchronises
    everything after it, and the group count comes out absurd."""
    import io

    from neo.rawio.intanrawio import (
        read_variable_header,
        rhs_global_header,
        rhs_signal_channel_header,
        rhs_signal_group_header,
    )

    stream = io.BytesIO(header_bytes)
    info = read_variable_header(stream, rhs_global_header)
    assert info["magic_number"] == MAGIC
    assert info["nb_signal_group"] == 8
    assert info["board_mode"] == BOARD_MODE_STIM_RECORD
    assert info["dc_amplifier_data_saved"] == 0
    assert info["stim_step_size"] == pytest.approx(10e-6)

    seen_types = []
    for _ in range(int(info["nb_signal_group"])):
        group = read_variable_header(stream, rhs_signal_group_header)
        if group["signal_group_enabled"] and group["channel_num"] > 0:
            for _ in range(int(group["channel_num"])):
                channel = read_variable_header(stream, rhs_signal_channel_header)
                seen_types.append(int(channel["signal_type"]))

    assert stream.read() == b"", "trailing bytes: the header is longer than parsed"
    assert seen_types.count(SIGNAL_TYPE_AMPLIFIER) == STIM_RECIPE.n_ap_channels
    assert seen_types.count(SIGNAL_TYPE_DIGITAL_IN) == 2


def test_only_signal_types_we_write_files_for_are_declared(header_bytes):
    """neo maps a declared signal type to an expected .dat filename. Declaring a
    board ADC input makes it look for analogin.dat, which is never written."""
    import io

    from neo.rawio.intanrawio import (
        read_variable_header,
        rhs_global_header,
        rhs_signal_channel_header,
        rhs_signal_group_header,
    )

    stream = io.BytesIO(header_bytes)
    info = read_variable_header(stream, rhs_global_header)
    for _ in range(int(info["nb_signal_group"])):
        group = read_variable_header(stream, rhs_signal_group_header)
        if group["signal_group_enabled"] and group["channel_num"] > 0:
            for _ in range(int(group["channel_num"])):
                channel = read_variable_header(stream, rhs_signal_channel_header)
                assert int(channel["signal_type"]) in (
                    SIGNAL_TYPE_AMPLIFIER,
                    SIGNAL_TYPE_DIGITAL_IN,
                )


def test_channel_names_come_from_the_recipe(tmp_path):
    import io

    from neo.rawio.intanrawio import (
        read_variable_header,
        rhs_global_header,
        rhs_signal_channel_header,
        rhs_signal_group_header,
    )

    from wl_preproc.synth.recipe import ChannelSpec

    named = tuple(
        ChannelSpec(name=f"C-{i:03d}", impedance_ohms=3.0e6)
        for i in range(STIM_RECIPE.n_ap_channels)
    )
    recipe = STIM_RECIPE.model_copy(update={"channels": named})
    path = tmp_path / "info.rhs"
    write_rhs_header(
        path, recipe, sample_rate_hz=30_000.0, stim_step_size_a=10e-6,
        digital_input_bits=(0, 1),
    )

    stream = io.BytesIO(path.read_bytes())
    info = read_variable_header(stream, rhs_global_header)
    found = []
    for _ in range(int(info["nb_signal_group"])):
        group = read_variable_header(stream, rhs_signal_group_header)
        if group["signal_group_enabled"] and group["channel_num"] > 0:
            for _ in range(int(group["channel_num"])):
                channel = read_variable_header(stream, rhs_signal_channel_header)
                if int(channel["signal_type"]) == SIGNAL_TYPE_AMPLIFIER:
                    found.append(
                        (
                            channel["native_channel_name"],
                            float(channel["electrode_impedance_magnitude"]),
                        )
                    )

    assert [n for n, _ in found] == [c.name for c in named]
    assert all(z == pytest.approx(3.0e6) for _, z in found)


def test_digital_channels_are_named_for_their_bit_positions(tmp_path):
    """digitalin.dat packs all 16 inputs per word; a reader needs the bit index
    to slice the barcode out of bit 0 and the strobe out of bit 1."""
    import io

    from neo.rawio.intanrawio import (
        read_variable_header,
        rhs_global_header,
        rhs_signal_channel_header,
        rhs_signal_group_header,
    )

    path = tmp_path / "info.rhs"
    write_rhs_header(
        path, STIM_RECIPE, sample_rate_hz=30_000.0, stim_step_size_a=10e-6,
        digital_input_bits=(0, 1),
    )
    stream = io.BytesIO(path.read_bytes())
    info = read_variable_header(stream, rhs_global_header)
    digital = []
    for _ in range(int(info["nb_signal_group"])):
        group = read_variable_header(stream, rhs_signal_group_header)
        if group["signal_group_enabled"] and group["channel_num"] > 0:
            for _ in range(int(group["channel_num"])):
                channel = read_variable_header(stream, rhs_signal_channel_header)
                if int(channel["signal_type"]) == SIGNAL_TYPE_DIGITAL_IN:
                    digital.append(
                        (channel["native_channel_name"], int(channel["native_order"]))
                    )

    assert digital == [("DIN-00", 0), ("DIN-01", 1)]


def test_header_is_deterministic(tmp_path):
    first, second = tmp_path / "a.rhs", tmp_path / "b.rhs"
    for path in (first, second):
        write_rhs_header(
            path, STIM_RECIPE, sample_rate_hz=30_000.0, stim_step_size_a=10e-6,
            digital_input_bits=(0, 1),
        )
    assert first.read_bytes() == second.read_bytes()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/synth/test_rhs_header.py -v`
Expected: FAIL — `ImportError: cannot import name 'write_rhs_header'`

- [ ] **Step 3: Write the implementation**

Append to `wl_preproc/synth/rhs_header.py`:

```python
from pathlib import Path

from wl_preproc.synth.recipe import ChannelSpec, SessionRecipe

MAGIC = 0xD69127AC
MAJOR_VERSION = 3
MINOR_VERSION = 0

SIGNAL_TYPE_AMPLIFIER = 0
SIGNAL_TYPE_DIGITAL_IN = 5

BOARD_MODE_STIM_RECORD = 14  # vendor document: always 14 for the Stim/Record Controller
CHANNELS_PER_CHIP = 16       # one RHS2116 chip, and one USB data stream, per 16 channels

# The vendor document says a file typically carries eight signal groups. The six
# we do not populate are still declared, with enabled = 0 and no channels, so the
# structure matches a real file without claiming data we never wrote.
GROUP_NAMES: tuple[tuple[str, str], ...] = (
    ("Port A", "A"),
    ("Port B", "B"),
    ("Port C", "C"),
    ("Port D", "D"),
    ("Board ADC Inputs", "ADC"),
    ("Board Digital Inputs", "DIN"),
    ("Board DAC Outputs", "DAC"),
    ("Board Digital Outputs", "DOUT"),
)

# Filter and impedance settings. These describe a plausible recording rather than
# a measured one; nothing downstream reads them, but a reader expects the fields
# and a zero everywhere would look like a malformed file.
_DSP_ENABLED = 1
_DSP_CUTOFF_HZ = 1.0
_LOWER_BANDWIDTH_HZ = 0.1
_LOWER_SETTLE_BANDWIDTH_HZ = 1000.0
_UPPER_BANDWIDTH_HZ = 7500.0
_NOTCH_MODE_DISABLED = 0
_IMPEDANCE_TEST_HZ = 1000.0
_AMP_SETTLE_MODE_SWITCH_LOWER_BANDWIDTH = 0
_CHARGE_RECOVERY_MODE_CURRENT_LIMITED = 0
_RECOVERY_CURRENT_LIMIT_A = 1e-6
_RECOVERY_TARGET_VOLTAGE_V = 0.0


def _global_block(recipe: SessionRecipe, sample_rate_hz: float, stim_step_size_a: float) -> bytes:
    """The 28 fields of the Standard Intan RHS Header, in document order.

    Order is load-bearing: this is a sequential binary read with no field tags,
    so a single misplaced or mistyped field desynchronises everything after it
    and the reader fails somewhere unrelated.
    """
    out = bytearray()
    out += struct.pack("<Ihh", MAGIC, MAJOR_VERSION, MINOR_VERSION)
    out += struct.pack("<f", sample_rate_hz)
    out += struct.pack("<h", _DSP_ENABLED)
    out += struct.pack(
        "<ffff",
        _DSP_CUTOFF_HZ,
        _LOWER_BANDWIDTH_HZ,
        _LOWER_SETTLE_BANDWIDTH_HZ,
        _UPPER_BANDWIDTH_HZ,
    )
    out += struct.pack(
        "<ffff",
        _DSP_CUTOFF_HZ,
        _LOWER_BANDWIDTH_HZ,
        _LOWER_SETTLE_BANDWIDTH_HZ,
        _UPPER_BANDWIDTH_HZ,
    )
    out += struct.pack("<h", _NOTCH_MODE_DISABLED)
    out += struct.pack("<ff", _IMPEDANCE_TEST_HZ, _IMPEDANCE_TEST_HZ)
    out += struct.pack("<h", _AMP_SETTLE_MODE_SWITCH_LOWER_BANDWIDTH)
    out += struct.pack("<h", _CHARGE_RECOVERY_MODE_CURRENT_LIMITED)
    out += struct.pack(
        "<fff", stim_step_size_a, _RECOVERY_CURRENT_LIMIT_A, _RECOVERY_TARGET_VOLTAGE_V
    )
    out += qstring(f"synthetic session {recipe.session_id} (wl-preproc)")
    out += qstring("")
    out += qstring("")
    # 0 declares that dcamplifier.dat is absent. Spec section 6.3 leaves that file
    # unwritten because the vendor document contradicts itself on its dtype; this
    # field is the format's own way of saying so, rather than leaving it implied.
    out += struct.pack("<h", 0)
    out += struct.pack("<h", BOARD_MODE_STIM_RECORD)
    out += qstring("n/a")  # hardware referencing, per the vendor document
    out += struct.pack("<h", len(GROUP_NAMES))
    return bytes(out)


def _channel_record(
    channel: ChannelSpec, index: int, signal_type: int, chip_channel: int
) -> bytes:
    """The 15 per-channel fields, in document order."""
    out = bytearray()
    out += qstring(channel.name)
    out += qstring(channel.name)
    out += struct.pack("<hh", index, index)          # native and custom order
    out += struct.pack("<h", signal_type)
    out += struct.pack("<h", 1 if channel.enabled else 0)
    out += struct.pack("<h", chip_channel)
    stream = index // CHANNELS_PER_CHIP
    out += struct.pack("<hh", stream, stream)        # command stream, board stream
    out += struct.pack("<hhhh", 0, 0, 0, 1)          # spike-scope defaults
    out += struct.pack(
        "<ff", channel.impedance_ohms, channel.impedance_phase_deg
    )
    return bytes(out)


def write_rhs_header(
    path: Path,
    recipe: SessionRecipe,
    sample_rate_hz: float,
    stim_step_size_a: float,
    digital_input_bits: tuple[int, ...],
) -> None:
    """Write a byte-correct Standard Intan RHS header to ``path``.

    ``digital_input_bits`` are the bit positions used within each ``digitalin.dat``
    word — the barcode and the strobe. A reader needs one declared channel per bit
    to slice them back out, and the channel's native order carries the bit index.

    Only amplifier and digital-input channels are declared, because a reader turns
    each declared signal type into an expected .dat filename and this generator
    writes only ``amplifier.dat`` and ``digitalin.dat``.
    """
    amplifier_channels = recipe.resolved_channels()
    digital_channels = tuple(
        ChannelSpec(name=f"DIN-{bit:02d}") for bit in digital_input_bits
    )

    out = bytearray(_global_block(recipe, sample_rate_hz, stim_step_size_a))

    for name, prefix in GROUP_NAMES:
        if name == "Port A":
            channels, signal_type = amplifier_channels, SIGNAL_TYPE_AMPLIFIER
        elif name == "Board Digital Inputs":
            channels, signal_type = digital_channels, SIGNAL_TYPE_DIGITAL_IN
        else:
            channels, signal_type = (), SIGNAL_TYPE_AMPLIFIER

        out += qstring(name)
        out += qstring(prefix)
        out += struct.pack("<h", 1 if channels else 0)
        out += struct.pack("<h", len(channels))
        out += struct.pack(
            "<h", len(channels) if signal_type == SIGNAL_TYPE_AMPLIFIER else 0
        )

        for index, channel in enumerate(channels):
            chip_channel = (
                digital_input_bits[index]
                if signal_type == SIGNAL_TYPE_DIGITAL_IN
                else index % CHANNELS_PER_CHIP
            )
            order = (
                digital_input_bits[index]
                if signal_type == SIGNAL_TYPE_DIGITAL_IN
                else index
            )
            out += _channel_record(channel, order, signal_type, chip_channel)

    path.write_bytes(bytes(out))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/synth/test_rhs_header.py -v`
Expected: PASS, 13 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/synth/rhs_header.py tests/synth/test_rhs_header.py
git commit -m "feat(synth): byte-correct Standard Intan RHS header"
```

---

### Task 4: Wire it in, and flip the tripwire to a real oracle

**Files:**
- Modify: `wl_preproc/synth/rhs.py`
- Modify: `tests/synth/test_rhs.py`

**Interfaces:**
- Consumes: `write_rhs_header`
- Produces: no new public names; `_write_header` and `_MAGIC` are deleted from `rhs.py`

- [ ] **Step 1: Write the failing test**

In `tests/synth/test_rhs.py`, **replace** the strict-xfail test
`test_spikeinterface_can_open_the_emitted_session` with the real assertion below.
Delete the `@pytest.mark.xfail` decorator entirely — its whole purpose was to
fail loudly at this moment.

> **One value below is unverified, and you should expect to correct it.** The
> stream selector `stream_name="RHS2000 amplifier channel"` is taken from neo's
> internal signal-type-to-filename mapping in `neo/rawio/intanrawio.py`, but the
> exact keyword SpikeInterface exposes (`stream_name` versus `stream_id`, and the
> precise string) could not be confirmed before a real header existed to open. If
> it raises, list the available streams —
> `spikeinterface.extractors.IntanRecordingExtractor.get_streams(file_path=out / "info.rhs")`
> — pick the amplifier stream, use whatever selector that call reports, and
> **record in your report which selector was correct**. This is the one place in
> this plan where the value was inferred rather than checked, and it is flagged
> so you do not spend time assuming the header is wrong when the selector is.

```python
def test_spikeinterface_can_open_the_emitted_session(tmp_path):
    """The reader-as-oracle test, matching the one that verifies SpikeGLX.

    Phase 1b could not have this: info.rhs was a 20-byte identification stub and
    read_intan failed parsing channel definitions. This passing is the whole
    point of writing a real header.
    """
    extractors = pytest.importorskip("spikeinterface.extractors")

    truth, out, _ = emit(tmp_path)
    recording = extractors.read_intan(
        file_path=out / "info.rhs", stream_name="RHS2000 amplifier channel"
    )

    assert recording.get_num_channels() == STIM_RECIPE.n_ap_channels
    assert recording.get_sampling_frequency() == pytest.approx(RHS_SAMPLE_RATE_HZ)
    assert list(recording.get_channel_ids()) == [
        c.name for c in STIM_RECIPE.resolved_channels()
    ]


def test_the_reader_returns_the_samples_we_wrote(tmp_path):
    """Opening is not enough — a header that parses but describes the array
    wrongly would slice amplifier.dat into the wrong shape."""
    extractors = pytest.importorskip("spikeinterface.extractors")

    truth, out, _ = emit(tmp_path)
    recording = extractors.read_intan(
        file_path=out / "info.rhs", stream_name="RHS2000 amplifier channel"
    )
    raw = np.fromfile(out / "amplifier.dat", dtype=np.int16).reshape(
        -1, STIM_RECIPE.n_ap_channels
    )
    assert recording.get_num_samples() == raw.shape[0]

    event = truth.stim_events[0]
    sample = int((event.onset_s + RHS_PRE_ROLL_S) * RHS_SAMPLE_RATE_HZ)
    channel_name = STIM_RECIPE.resolved_channels()[event.channel].name
    through_reader = recording.get_traces(
        start_frame=sample,
        end_frame=sample + 1,
        channel_ids=[channel_name],
    )
    assert int(through_reader[0, 0]) == int(raw[sample, event.channel])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/synth/test_rhs.py -v`
Expected: FAIL — `rhs.py` still writes the stub, so `read_intan` raises `IndexError` while parsing channel definitions

- [ ] **Step 3: Write the implementation**

In `wl_preproc/synth/rhs.py`:

Delete the `_MAGIC` constant and the entire `_write_header` function.

Replace the `import struct` line with the new import (nothing else in the module uses `struct` once `_write_header` is gone — remove the import if the module no longer references it):

```python
from wl_preproc.synth.rhs_header import write_rhs_header
```

Replace the call site inside `write_rhs`:

```python
    _write_header(out / "info.rhs", recipe)
```

with:

```python
    write_rhs_header(
        out / "info.rhs",
        recipe,
        sample_rate_hz=fs,
        stim_step_size_a=STIM_STEP_SIZE_A,
        digital_input_bits=(BARCODE_DIGITAL_BIT, STROBE_DIGITAL_BIT),
    )
```

Update the module docstring's `info.rhs` line, which currently describes a stub:

```
    info.rhs        Standard Intan RHS header, byte-correct (see rhs_header.py)
```

and remove the paragraph stating that a real header is deliberately deferred —
it no longer is.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. The previously-xfailed test now passes as a normal test, so expect **no xfailed count at all** — if pytest still reports `1 xfailed`, the decorator was not removed.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/synth/rhs.py tests/synth/test_rhs.py
git commit -m "feat(synth): emit a real Intan header and turn on the reader oracle"
```

---

## Definition of done

- `pytest` green across the whole suite, with **zero xfails** — the tripwire has fired and been replaced
- `spikeinterface.extractors.read_intan` opens a `wlpp synth generate --profile stim` session
- Channel count, names and sample rate read back through the reader match what the recipe declared
- A sample read through the reader equals the same sample read raw from `amplifier.dat`
- `dc_amplifier_data_saved` is `0` in the emitted header, declaring §6.3's ruling rather than implying it

## What this unblocks

- **Phase 1c tier-B provenance** — the standalone-Intan ingest path can be tested through the reader the pipeline will actually use, rather than through `np.fromfile`
- **§11.6's derived-vs-recorded channel map check** — `SessionRecipe.channels` makes a deliberately wrong map expressible, which is what testing a comparison requires
- **`pyproject.toml`'s oracle claim** becomes true for both emitters; its qualifying clause about RHS should be removed when this lands

## Deliberately excluded

- **`dcamplifier.dat`** — dtype still genuinely unresolved (§6.3). The header now declares its absence rather than leaving it implied, which is as far as this can honestly go.
- **The traditional interleaved format.** The flat layout is a supported RHX option and is what these fixtures use. The header writer is deliberately format-agnostic, so a traditional emitter could reuse it unchanged.
- **`analogin.dat` / `analogout.dat` and their channel declarations.** The photodiode lands on an Intan analog input (§4.3), but nothing consumes it until Phase 3, and declaring the channels without writing the file would break the reader.
- **`spike.dat`.** Written by RHX's spike-detection feature; nothing in this pipeline reads it.
- **Realistic filter and impedance values.** The bandwidth and impedance fields describe a plausible recording, not a measured one. Nothing downstream reads them; if something later does, they become recipe data by the same argument that moved channel identity there.
