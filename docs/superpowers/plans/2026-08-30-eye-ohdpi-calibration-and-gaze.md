# Eye: ohDPI reader, calibration and canonical gaze — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read the real OpenIrisDPI recording format, fit a per-eye calibration from known target positions, and expose canonical gaze — correcting five wrong format assumptions shipped in Phase 1c-4.

**Architecture:** One reader at `wl_preproc/eye/ohdpi.py` owns the format for both timebase and eye. Calibration is a per-eye affine over the P1 − P4 vector, with a validated four-step fallback chain. Gaze is a computation over (raw file, calibration), never a stored array.

**Tech Stack:** Python 3.13 (3.11 in this venv), NumPy 2.4, pandas 3.0.5 (newly declared), DataJoint 2.3.2, pytest.

**Spec:** `docs/superpowers/specs/2026-08-30-eye-ohdpi-calibration-and-gaze-design.md`

## Global Constraints

- **The reference recording** lives at `~/Downloads/Tutorial/OpenIris-2024Jul31-114628/OpenIris-2024Jul31-114628.txt` — 633 MB, 1,177,799 rows. Never commit it. Task 1 extracts a slice.
- **`.txt`, space-delimited**, ~100 columns. Header verified identical between OpenIris source and the real file.
- **P1 → `CR1`, P4 → `CR4`.** `CR2`, `CR3`, `CR5` are identically zero.
- **`DataQuality` is `0`, `50` or `100`** — `50·P1_valid + 50·P4_valid`.
- **`Seconds` is seconds**, not microseconds. Measured rate 498.554 Hz.
- **`Int0` carries the sync line**, values `{12, 13}`; bit 0 toggles. The bit index is rig wiring and must be a named constant, not a literal.
- **Frame numbers are contiguous but do not start at zero** (308788 → 1486586, zero gaps). `FrameNumberRaw = FrameNumber − 1`.
- **`LeftSeconds` and `RightSeconds` differ by a DRIFTING offset** — 49.50 ms at the start of the reference recording, 45.80 ms at its end (~1.6 ppm of relative camera clock skew). Frame number is the index; `Seconds` is never session time. (Corrected 2026-08-30: this plan first said "constant", from a 10,000-row measurement generalised to the whole file.)
- **No bare `longblob`.** A repo-wide guardrail sweep enforces this; `<blob>` codec is the safe form. This plan stores no arrays.
- **Test module basenames must be globally unique** across `tests/` — the layout is deliberately `__init__.py`-free.
- Comments explain **why**, and are held to the same truth standard as code. Cite by **symbol name, not line number** — line citations in this repo have gone stale three times, twice in the commit that fixed the previous one.
- Conventional-commit subjects, lowercase after the colon.
- Venv is `.venv/bin/python`. Baseline at plan start: **885 passed, 1 deselected**.

---

### Task 1: The real-bytes fixture and the corrected reader

**Files:**
- Create: `wl_preproc/eye/__init__.py`, `wl_preproc/eye/ohdpi.py`
- Create: `tests/fixtures/ohdpi/OpenIris-sample.txt` (extracted, ~200 rows)
- Test: `tests/eye/test_ohdpi_reader.py`

**Interfaces:**
- Produces: `OhdpiRecording` (frozen: `frame_numbers: np.ndarray`, `digital: np.ndarray`, `fs_hz: float`, `n_frames: int`); `read_ohdpi(path, columns=None) -> OhdpiRecording`; `read_columns(path, columns) -> dict[str, np.ndarray]`; `SYNC_WORD_COLUMN = "Int0"`; `SYNC_BIT_INDEX = 0`; `EYES = ("Left", "Right")`.

- [ ] **Step 1: Extract the fixture**

```bash
SRC=~/Downloads/Tutorial/OpenIris-2024Jul31-114628/OpenIris-2024Jul31-114628.txt
mkdir -p tests/fixtures/ohdpi
head -201 "$SRC" > tests/fixtures/ohdpi/OpenIris-sample.txt
wc -l tests/fixtures/ohdpi/OpenIris-sample.txt   # expect 201 (header + 200 rows)
du -h tests/fixtures/ohdpi/OpenIris-sample.txt   # expect ~140K
```

If `~/Downloads/Tutorial/...` is absent, STOP and report BLOCKED — this fixture is the whole point of the task and must not be synthesised.

- [ ] **Step 2: Write the failing test**

Create `tests/eye/test_ohdpi_reader.py`:

```python
from pathlib import Path

import numpy as np
import pytest

from wl_preproc.eye.ohdpi import SYNC_BIT_INDEX, SYNC_WORD_COLUMN, read_ohdpi

FIXTURE = Path(__file__).parent.parent / "fixtures" / "ohdpi" / "OpenIris-sample.txt"


def test_it_reads_bytes_openiris_actually_wrote():
    """The fixture is a slice of a real recording, not something we emitted.

    Three format assumptions survived since August because `synth/ohdpi.py`
    wrote a guessed format and the reader read the same guess -- they agreed by
    construction. Real bytes cannot be talked into agreeing with us.
    """
    rec = read_ohdpi(FIXTURE)

    assert rec.n_frames == 200
    # 308788 is where this recording's camera counter happened to be. The point
    # is that it is NOT zero: the shipped reader required `frame_index ==
    # position` and would reject every real file.
    assert rec.frame_numbers[0] == 308788
    assert np.all(np.diff(rec.frame_numbers) == 1)


def test_the_rate_comes_from_seconds_not_microseconds():
    """`LeftSeconds` is SECONDS. The shipped reader assumed microseconds, which
    is a rate wrong by 10**6 -- the exact failure its own comment predicted,
    off by a larger factor than it guessed."""
    rec = read_ohdpi(FIXTURE)

    assert 495.0 < rec.fs_hz < 502.0, rec.fs_hz


def test_the_sync_line_is_int0():
    """1c-4's open question 1, closed by measurement: the digital line is
    `Int0`, which takes only 12 and 13 across the whole recording -- bit 0
    toggling, bits 2 and 3 constant-high."""
    rec = read_ohdpi(FIXTURE)

    assert set(np.unique(rec.digital)) <= {12, 13}
    bits = (rec.digital >> SYNC_BIT_INDEX) & 1
    assert set(np.unique(bits)) == {0, 1}, "the sync bit must actually toggle"
    assert SYNC_WORD_COLUMN == "Int0"


def test_a_file_with_the_wrong_header_is_refused(tmp_path):
    """We know the true header now. Parsing an unrecognised one optimistically
    is how the shipped defect survived for two weeks."""
    bad = tmp_path / "bad.txt"
    bad.write_text("frame_index timestamp_us digital\n0 0 1\n1 2000 0\n", encoding="utf-8")

    with pytest.raises(ValueError, match="header"):
        read_ohdpi(bad)


def test_seconds_is_never_offered_as_session_time():
    """`LeftSeconds` and `RightSeconds` differ by an offset that DRIFTS --
    49.50 ms at the start of the reference recording, 45.80 ms at its end --
    while frame numbers agree exactly. The cameras are frame-locked; their
    clocks are not, and the disagreement is not even a fixed one that could be
    subtracted.

    `OhdpiRecording` therefore exposes no per-eye timestamp at all. Frame
    number is the index; the rate is derived internally and `Seconds` does not
    escape this module.
    """
    rec = read_ohdpi(FIXTURE)

    assert not hasattr(rec, "seconds")
    assert not hasattr(rec, "left_seconds")
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/eye/test_ohdpi_reader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.eye'`.

- [ ] **Step 4: Implement**

Create `wl_preproc/eye/__init__.py`:

```python
"""Eye tracking: the ohDPI reader, calibration, and canonical gaze.

Design spec `docs/superpowers/specs/2026-08-30-eye-ohdpi-calibration-and-gaze-design.md`;
parent design spec section 7. Saccade detection is a separate spec.
"""
```

Create `wl_preproc/eye/ohdpi.py`:

```python
"""The OpenIrisDPI recording's one reader, for every consumer.

**Every column name here was verified against a real recording** on
2026-08-30 (`OpenIris-2024Jul31-114628`, 1,177,799 rows) and against
OpenIris's own `EyeTrackerData.cs::GetStringHeader()`. That replaces the
proposal this project shipped in Phase 1c-4, whose three guesses were all
wrong -- see the design spec's section 0.

Lives in `eye/` rather than `timebase/` because parent design spec section 3.4's
module layout assigns the ohDPI reader here, and because two subsystems now
consume it. This module imports nothing from `timebase`: it reads a file and
knows nothing of session time, so `timebase/extract.py` can import it without a
cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

EYES: tuple[str, ...] = ("Left", "Right")

# The generic extra-data slot OpenIris writes the digital input into. Measured:
# `Int0` takes exactly {12, 13} across the whole reference recording, while
# every other Int slot is 0.
SYNC_WORD_COLUMN = "Int0"

# **Rig wiring, not a property of the format.** On the reference recording bit 0
# toggles and bits 2 and 3 sit constant-high, so 12/13 is bit 0 carrying the
# signal. Another rig may wire it elsewhere; that is a one-constant change, and
# this is the constant.
SYNC_BIT_INDEX = 0

_FRAME_NUMBER = "LeftFrameNumber"
_SECONDS = "LeftSeconds"

# The columns every caller can rely on. Not the whole header -- the file has
# about a hundred columns and no consumer wants them all -- but enough to
# recognise the format and refuse anything else.
_REQUIRED = (_FRAME_NUMBER, _SECONDS, SYNC_WORD_COLUMN, "LeftCR1X", "LeftCR4X")


@dataclass(frozen=True, slots=True)
class OhdpiRecording:
    """The sync line per frame, and the rate that sampled it.

    **No timestamp column is exposed, deliberately.** `LeftSeconds` and
    `RightSeconds` differ by a constant 49.48 ms on the reference recording
    (min 49.40, max 49.50 -- a spread of one timestamp tick, so an origin
    offset, not jitter), while `LeftFrameNumber` and `RightFrameNumber` are
    identical in every row. The cameras are frame-locked by the trigger chain;
    their clocks are not, and at 500 Hz that offset is ~25 frames.

    So frame number is the index and `Seconds` is used only to derive `fs_hz`
    inside this module. Parent design spec section 7.1 puts eye frame times on
    the sync-box clock by construction via the Pi trigger, which is where
    session time comes from -- never from here.
    """

    frame_numbers: np.ndarray
    digital: np.ndarray
    fs_hz: float
    n_frames: int


def read_columns(path: Path, columns: list[str]) -> dict[str, np.ndarray]:
    """Named columns from one recording, as arrays.

    Column-selective because the file has ~100 columns and no caller wants
    them all: timebase needs 2 and gaze needs about 10. Measured on the
    reference recording, reading 10 columns takes 2.5 s and 94 MB against
    roughly a gigabyte for the whole file.
    """
    frame = pd.read_csv(path, sep=r"\s+", usecols=columns, engine="c")
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(
            f"{path}: header is missing {sorted(missing)}. This is not an "
            "OpenIris recording, or its format has changed since 2026-08-30"
        )
    return {name: frame[name].to_numpy() for name in columns}


def read_ohdpi(path: Path) -> OhdpiRecording:
    """Read the sync line and establish the recording's own rate."""
    try:
        data = read_columns(path, list(_REQUIRED))
    except ValueError as exc:
        raise ValueError(f"{path}: unrecognised header -- {exc}") from exc

    frames = data[_FRAME_NUMBER]
    if frames.size < 2:
        raise ValueError(
            f"{path}: {frames.size} frames cannot establish a sampling rate, "
            "so nothing here can be timed"
        )

    # Contiguity, NOT a zero start. The shipped reader required
    # `frame_index == position`; the reference recording runs 308788 to
    # 1486586, so that check rejects every real file. A gap is still a dropped
    # frame the file does not declare, and reading past it shifts every later
    # sample against its true time.
    gaps = np.flatnonzero(np.diff(frames) != 1)
    if gaps.size:
        first = int(gaps[0])
        raise ValueError(
            f"{path}: frame numbers jump from {frames[first]} to "
            f"{frames[first + 1]} at row {first}; a gap is a dropped frame the "
            "file does not declare"
        )

    seconds = data[_SECONDS]
    span_s = float(seconds[-1] - seconds[0])
    if span_s <= 0:
        raise ValueError(
            f"{path}: the last frame's timestamp does not follow the first "
            f"({seconds[0]} to {seconds[-1]}), so the file carries no usable clock"
        )

    # The rate over the whole span rather than an adjacent difference: one
    # interval is quantised to the timestamp's own resolution (0.1 ms here),
    # and at 500 Hz that quantisation is a percent-level error the entire fit
    # would inherit.
    fs_hz = (frames.size - 1) / span_s

    return OhdpiRecording(
        frame_numbers=frames,
        digital=data[SYNC_WORD_COLUMN],
        fs_hz=fs_hz,
        n_frames=int(frames.size),
    )
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/eye/test_ohdpi_reader.py -v`
Expected: 5 passed.

- [ ] **Step 6: Probe each test**

For each of the five, make the named production change, confirm the test fails, revert:

| Test | Mutation |
|---|---|
| `reads_bytes_openiris_actually_wrote` | require `frames[0] == 0` |
| `rate_comes_from_seconds` | multiply `fs_hz` by 1e6 |
| `sync_line_is_int0` | set `SYNC_WORD_COLUMN = "Int1"` |
| `wrong_header_is_refused` | drop the `missing` check in `read_columns` |
| `seconds_never_session_time` | add a `seconds` field to `OhdpiRecording` |

- [ ] **Step 7: Commit**

```bash
git add wl_preproc/eye tests/eye tests/fixtures/ohdpi
git commit -m "eye: the ohDPI reader, against bytes OpenIris actually wrote"
```

---

### Task 2: Point timebase at the one reader

**Files:**
- Modify: `wl_preproc/timebase/extract.py` (`extract_ohdpi`, `_RECORDING_GLOBS`)
- Delete: `wl_preproc/timebase/_ohdpi_file.py`
- Test: `tests/timebase/test_ohdpi_extraction.py`

**Interfaces:**
- Consumes: `wl_preproc.eye.ohdpi.read_ohdpi`, `SYNC_BIT_INDEX`.

- [ ] **Step 1: Write the failing test**

Create `tests/timebase/test_ohdpi_extraction.py`:

```python
from pathlib import Path

from wl_preproc.timebase.extract import _RECORDING_GLOBS, extract_ohdpi

FIXTURE = Path(__file__).parent.parent / "fixtures" / "ohdpi" / "OpenIris-sample.txt"


def test_the_glob_matches_a_real_recording_and_not_its_events_sibling(tmp_path):
    """OpenIris writes `<session>.txt` AND `<session>-events.txt` into the same
    folder. The shipped glob was `*.csv`, which matches neither -- a real
    session would have yielded no ohDPI recording at all."""
    (tmp_path / "OpenIris-2024Jul31-114628.txt").write_text("x", encoding="utf-8")
    (tmp_path / "OpenIris-2024Jul31-114628-events.txt").write_text("x", encoding="utf-8")
    (tmp_path / "OpenIris-2024Jul31-114628-log.log").write_text("x", encoding="utf-8")

    matched = sorted(p.name for p in tmp_path.glob(_RECORDING_GLOBS["ohdpi"]))

    assert matched == ["OpenIris-2024Jul31-114628.txt"]


def test_it_extracts_a_bitstream_from_the_real_fixture():
    stream = extract_ohdpi(FIXTURE)

    assert stream.n_samples == 200
    assert 495.0 < stream.fs_hz < 502.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/timebase/test_ohdpi_extraction.py -v`
Expected: FAIL — the glob is `*.csv`, so `matched == []`.

- [ ] **Step 3: Fix the glob**

In `wl_preproc/timebase/extract.py`'s `_RECORDING_GLOBS`, replace the `ohdpi` entry:

```python
    # NOT "*.csv" (which this shipped with and which matches nothing real) and
    # not a bare "*.txt": OpenIris writes `<session>.txt` beside
    # `<session>-events.txt` in the same folder, and the events file is a
    # different format entirely. Same reasoning as the spikeglx entry above --
    # a glob that quietly matches the wrong sibling is a diagnosis pointing at
    # the wrong fault.
    "ohdpi": "*[!s].txt",
```

Verify the pattern excludes only the events sibling: `-events.txt` ends in `s.txt`, `<session>.txt` does not.

- [ ] **Step 4: Rewrite `extract_ohdpi`**

Replace its body and the "proposal" paragraph in its docstring:

```python
    Columns were **verified against a real recording** on 2026-08-30; they live
    in `wl_preproc/eye/ohdpi.py`, which is the format's only reader. The sync
    line is `Int0` and the bit within it is `eye.ohdpi.SYNC_BIT_INDEX` -- rig
    wiring, and the one thing here another rig can change.
    """
    from wl_preproc.eye.ohdpi import SYNC_BIT_INDEX, read_ohdpi

    recording = read_ohdpi(path)
    bits = (recording.digital >> SYNC_BIT_INDEX) & 1
    return BitStream(
        edges=tuple(edges_from_samples(list(bits), fs_hz=recording.fs_hz)),
        fs_hz=recording.fs_hz,
        n_samples=recording.n_frames,
    )
```

- [ ] **Step 5: Delete the old reader and its now-dead references**

```bash
git rm wl_preproc/timebase/_ohdpi_file.py
grep -rn "_ohdpi_file" wl_preproc/ tests/    # must return nothing
```

Any docstring still citing `_ohdpi_file.py` gets updated to `wl_preproc/eye/ohdpi.py`. `wl_preproc/synth/ohdpi.py` has two such references; Task 3 rewrites that file, so leave them.

- [ ] **Step 6: Run**

Run: `.venv/bin/python -m pytest tests/timebase tests/eye -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "timebase: read ohDPI through the one reader, and fix the glob"
```

---

### Task 3: The generator emits the real format

**Files:**
- Modify: `wl_preproc/synth/ohdpi.py` (whole-file rewrite)
- Modify: `tests/synth/test_ohdpi.py`

**Interfaces:**
- Produces: `write_ohdpi(dir_path, recipe, truth, drift_ppm=0.0) -> Path` (unchanged signature); `HEADER: tuple[str, ...]`; `FILENAME` now `"OpenIris-synthetic.txt"`.

**Why this task exists:** the generator currently writes `frame_index,timestamp_us,digital` as CSV. Every downstream fixture is therefore in a format OpenIris does not produce. Gaze work in later tasks needs synthetic pupil and Purkinje data that does not exist today.

- [ ] **Step 1: Write the failing test**

Add to `tests/synth/test_ohdpi.py`:

```python
def test_the_fixture_is_readable_by_the_production_reader(tmp_path):
    """The generator and the reader must agree because both match OpenIris,
    not because they share constants. `wl_preproc/eye/ohdpi.py` restates its
    own column names and is additionally pinned by a slice of a real
    recording, so this passing means the fixture matches the real format."""
    from wl_preproc.eye.ohdpi import read_ohdpi
    from wl_preproc.synth.recipe import CI_RECIPE
    from wl_preproc.synth.truth import GroundTruth

    truth = GroundTruth.for_recipe(CI_RECIPE)
    path = write_ohdpi(tmp_path, CI_RECIPE, truth)

    rec = read_ohdpi(path)
    assert rec.n_frames > 0
    assert set(np.unique(rec.digital)) <= {12, 13}


def test_it_emits_purkinje_and_pupil_columns(tmp_path):
    """Gaze needs P1 (CR1) and P4 (CR4). A fixture carrying only the sync line
    cannot exercise calibration at all."""
    from wl_preproc.eye.ohdpi import read_columns
    from wl_preproc.synth.recipe import CI_RECIPE
    from wl_preproc.synth.truth import GroundTruth

    truth = GroundTruth.for_recipe(CI_RECIPE)
    path = write_ohdpi(tmp_path, CI_RECIPE, truth)

    cols = read_columns(path, ["LeftCR1X", "LeftCR1Y", "LeftCR4X", "LeftCR4Y", "LeftDataQuality"])
    assert cols["LeftCR1X"].std() > 0, "P1 must move"
    assert cols["LeftCR4X"].std() > 0, "P4 must move"
    assert set(np.unique(cols["LeftDataQuality"])) <= {0.0, 50.0, 100.0}
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/synth/test_ohdpi.py -v`
Expected: FAIL — the reader rejects the header.

- [ ] **Step 3: Rewrite the generator**

Replace `wl_preproc/synth/ohdpi.py`'s module docstring, constants and `write_ohdpi`. Keep `_digital_line` unchanged — it already produces the barcode line correctly.

The header is the full OpenIris header, in order. Build it programmatically to keep it readable:

```python
_PER_EYE = (
    "FrameNumber", "FrameNumberRaw", "Seconds",
    "PupilX", "PupilY", "PupilWidth", "PupilHeight", "PupilAngle",
    "IrisRadius", "Torsion", "UpperEyelid", "LowerEyelid", "DataQuality",
    "CR1X", "CR1Y", "CR2X", "CR2Y", "CR3X", "CR3Y",
    "CR4X", "CR4Y", "CR5X", "CR5Y",
)
_IMU = tuple(
    f"{sensor}{axis}"
    for sensor in ("Accelerometer", "Gyro", "Magnetometer")
    for axis in ("X", "Y", "Z")
)
_EXTRA = tuple(f"Int{i}" for i in range(8)) + tuple(f"Double{i}" for i in range(8))
_DEBUG = ("DebugTimeGrabbedLeft", "DebugTimeGrabbedRight", "DebugTimeProcessed")

HEADER: tuple[str, ...] = (
    tuple(f"Left{name}" for name in _PER_EYE)
    + tuple(f"Right{name}" for name in _PER_EYE)
    + _IMU + _EXTRA + _DEBUG
)

FILENAME = "OpenIris-synthetic.txt"

# The reference recording's constant left/right timestamp offset. Reproduced so
# a consumer that wrongly treats `Seconds` as session time is wrong here in the
# same way it would be wrong in production, rather than passing on a fixture
# where the two clocks happen to agree.
RIGHT_SECONDS_OFFSET_S = -0.04948

# `Int0` = 12 with bit 0 carrying the barcode, matching the reference
# recording's {12, 13}. The constant-high bits 2 and 3 are reproduced because a
# reader that masks wrongly must fail on the fixture too.
_INT0_BASE = 0b1100

SAMPLE_DTYPE_DECIMALS = 4
```

`write_ohdpi` writes space-delimited rows with a synthetic eye trajectory. Pupil and Purkinje positions follow a slow drift plus per-frame noise; P1 and P4 share the drift (translation) and differ by a rotation term, so `P1 − P4` carries the signal a calibration can recover:

```python
def write_ohdpi(
    dir_path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0
) -> Path:
    """Render one session's eye recording in OpenIris's real format.

    `Seconds` is derived from the frame index and `OHDPI_FPS` so the two
    columns cannot disagree, and the right eye's column carries the reference
    recording's constant offset (`RIGHT_SECONDS_OFFSET_S`).
    """
    line = _digital_line(recipe, truth, drift_ppm)
    n = len(line)
    rng = np.random.default_rng(recipe.seed)

    frame0 = 308788  # not zero, matching a real camera counter
    t = np.arange(n) / OHDPI_FPS
    # Slow common motion (translation) plus a rotation term only P4 sees.
    common_x = 500.0 + 20.0 * np.sin(2 * np.pi * 0.05 * t)
    common_y = 220.0 + 15.0 * np.cos(2 * np.pi * 0.03 * t)
    rot_x = 40.0 * np.sin(2 * np.pi * 0.20 * t) + rng.normal(0, 0.5, n)
    rot_y = 30.0 * np.cos(2 * np.pi * 0.17 * t) + rng.normal(0, 0.5, n)

    path = dir_path / FILENAME
    with path.open("w", encoding="utf-8") as handle:
        handle.write(" ".join(HEADER) + "\n")
        for i in range(n):
            row: list[str] = []
            for eye_index in range(2):
                offset = 0.0 if eye_index == 0 else RIGHT_SECONDS_OFFSET_S
                p1x, p1y = common_x[i], common_y[i]
                p4x, p4y = common_x[i] - rot_x[i], common_y[i] - rot_y[i]
                row += [
                    str(frame0 + i), str(frame0 + i - 1), f"{t[i] + offset:.4f}",
                    f"{p1x:.4f}", f"{p1y:.4f}", "60.0000", "58.0000", "0.0000",
                    "180.0000", "0.0000", "0.0000", "0.0000", "100.0000",
                    f"{p1x:.4f}", f"{p1y:.4f}", "0.0000", "0.0000", "0.0000", "0.0000",
                    f"{p4x:.4f}", f"{p4y:.4f}", "0.0000", "0.0000",
                ]
            row += ["0.0000"] * 9
            row += [str(_INT0_BASE | line[i])] + ["0"] * 7
            row += ["0.0000"] * 8
            row += [f"{t[i]:.4f}", f"{t[i]:.4f}", f"{t[i]:.4f}"]
            handle.write(" ".join(row) + "\n")
    return path
```

Delete `read_ohdpi_rows`, `OhdpiRow`, `COLUMNS`, `COLUMN_*` and `TIMESTAMP_UNITS_PER_SECOND` — the production reader is now the only reader, which is what the old docstring said it wanted.

- [ ] **Step 4: Update the existing tests in the file**

The four tests calling `read_ohdpi_rows` must use `wl_preproc.eye.ohdpi.read_columns` instead. Keep every assertion's intent; only the access path changes.

- [ ] **Step 5: Run**

Run: `.venv/bin/python -m pytest tests/synth tests/timebase tests/eye -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "synth: emit OpenIris's real format, with pupil and Purkinje data"
```

---

### Task 4: `TARGET_POSITION` and the fixation markers

**Files:**
- Modify: `wl_preproc/contracts/events.py`
- Test: `tests/contracts/test_target_position.py`

**Interfaces:**
- Produces: `Escape.TARGET_POSITION = 0x8004`; `Marker`-range constants `FIXATION_ACQUIRED = 256`, `FIXATION_END = 257`; `encode_dva(degrees) -> int`; `decode_dva(word) -> float`; `DVA_OFFSET = 32768`; `DVA_SCALE = 100`; `TargetRole` IntEnum.

- [ ] **Step 1: Write the failing test**

Create `tests/contracts/test_target_position.py`:

```python
import pytest

from wl_preproc.contracts.events import (
    DVA_OFFSET,
    PAYLOAD_WORD_COUNTS,
    Escape,
    TargetRole,
    decode_dva,
    encode_dva,
)


def test_screen_centre_is_the_offset():
    """Design spec section 4.1: straight ahead is 32768 on both axes."""
    assert encode_dva(0.0) == DVA_OFFSET == 32768


def test_the_worked_example_from_the_spec():
    """A target 10 degrees right and 5 up -- section 4.1's own table."""
    assert encode_dva(10.0) == 33768
    assert encode_dva(5.0) == 33268


def test_it_round_trips_across_the_range():
    for deg in (-327.0, -10.5, -0.01, 0.0, 0.01, 10.5, 327.0):
        assert decode_dva(encode_dva(deg)) == pytest.approx(deg, abs=0.005)


def test_a_word_always_fits_sixteen_bits():
    """A payload word wider than the bus would be silently truncated by the
    sync box, and the truncation is not detectable downstream."""
    for deg in (-327.68, 327.67):
        assert 0 <= encode_dva(deg) <= 0xFFFF


def test_out_of_range_is_refused_not_clamped():
    """Clamping would place a target at the edge of the screen and report
    success -- a plausible number, which is this project's signature defect."""
    with pytest.raises(ValueError, match="out of range"):
        encode_dva(400.0)


def test_the_escape_declares_three_payload_words():
    assert PAYLOAD_WORD_COUNTS[Escape.TARGET_POSITION] == 3
    assert Escape.TARGET_POSITION == 0x8004


def test_roles_are_distinct_and_start_at_the_fixation_point():
    assert TargetRole.FIXATION_POINT == 0
    assert TargetRole.SACCADE_TARGET == 1
    assert len(set(TargetRole)) == len(list(TargetRole))
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/contracts/test_target_position.py -v`
Expected: FAIL — `ImportError: cannot import name 'DVA_OFFSET'`.

- [ ] **Step 3: Implement**

In `wl_preproc/contracts/events.py`, extend `Escape` and `PAYLOAD_WORD_COUNTS`, and add:

```python
class TargetRole(IntEnum):
    """Which on-screen target a `TARGET_POSITION` payload describes.

    `TaskTypeCode.MEMORY_GUIDED_SACCADE` puts a fixation point and a target on
    screen simultaneously; without a role two payloads are ambiguous. It also
    tells calibration which target the animal was demonstrably looking at.
    """

    FIXATION_POINT = 0
    SACCADE_TARGET = 1


# Degrees of visual angle, offset-binary, hundredths of a degree.
#
# **Degrees, not pixels:** this pipeline holds no screen geometry -- no viewing
# distance, no pixel pitch -- and acquiring it would mean a second transport for
# numbers that differ per rig and change whenever a monitor moves. The task
# already knows the geometry because it renders the stimulus, and MonkeyLogic
# holds `ScreenInfo.PixelsPerDegree`.
#
# **Offset-binary, not two's complement:** no sign-extension convention to get
# wrong across the task, the sync box and this decoder.
DVA_SCALE = 100
DVA_OFFSET = 32768
_DVA_MIN = -DVA_OFFSET / DVA_SCALE
_DVA_MAX = (0xFFFF - DVA_OFFSET) / DVA_SCALE


def encode_dva(degrees: float) -> int:
    """One axis of a target position, as a payload word."""
    if not _DVA_MIN <= degrees <= _DVA_MAX:
        raise ValueError(
            f"target position {degrees} deg is out of range "
            f"[{_DVA_MIN}, {_DVA_MAX}]; refused rather than clamped, because a "
            "clamped target reports a position the task did not use"
        )
    return round(degrees * DVA_SCALE) + DVA_OFFSET


def decode_dva(word: int) -> float:
    """The inverse of `encode_dva`."""
    return (word - DVA_OFFSET) / DVA_SCALE
```

Add the two task-event markers. They belong to the 256–4095 range, which has no enum today, so create one beside `Marker`:

```python
class TaskEvent(IntEnum):
    """Task events. Range 256-4095.

    `Marker.TRIAL_FIXATION_BREAK` already covers a failed hold; these bound a
    successful one, which is the window calibration fits against.
    """

    FIXATION_ACQUIRED = 256
    FIXATION_END = 257
```

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests/contracts -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "contracts: target positions in the code stream, in degrees"
```

---

### Task 5: The affine fit and the degeneracy refusal

**Files:**
- Create: `wl_preproc/eye/calibration.py`
- Test: `tests/eye/test_calibration_fit.py`

**Interfaces:**
- Produces: `AffineMap` (frozen: `a: tuple[float, ...]` of six, `n_points: int`, `conditioning: float`); `fit_affine(raw_xy, target_xy) -> AffineMap`; `apply_affine(map_, raw_xy) -> np.ndarray`; `DegenerateGeometry` exception; `MIN_CONDITIONING`.

- [ ] **Step 1: Write the failing test**

Create `tests/eye/test_calibration_fit.py`:

```python
import numpy as np
import pytest

from wl_preproc.eye.calibration import (
    DegenerateGeometry,
    apply_affine,
    fit_affine,
)


def _known_map(raw):
    """gx = 0.05*dx + 0.002*dy + 1.0 ; gy = -0.001*dx + 0.06*dy - 0.5"""
    return np.column_stack([
        0.05 * raw[:, 0] + 0.002 * raw[:, 1] + 1.0,
        -0.001 * raw[:, 0] + 0.06 * raw[:, 1] - 0.5,
    ])


def test_it_recovers_a_known_affine():
    raw = np.array([[-100.0, -80.0], [100.0, -80.0], [0.0, 90.0], [60.0, 40.0]])
    fitted = fit_affine(raw, _known_map(raw))

    assert apply_affine(fitted, raw) == pytest.approx(_known_map(raw), abs=1e-9)


def test_a_single_target_location_is_refused():
    """THE load-bearing safety property. Six parameters need three
    non-collinear points; given one, least squares still returns a minimum-norm
    solution that looks like a calibration and means nothing.

    A session whose only fixation is central must get no map, not a plausible
    one.
    """
    raw = np.array([[10.0, 10.0], [10.4, 9.6], [9.7, 10.2], [10.1, 10.1]])
    target = np.zeros((4, 2))

    with pytest.raises(DegenerateGeometry, match="spread"):
        fit_affine(raw, target)


def test_collinear_targets_are_refused():
    """Three points on a line constrain the map along it and nothing across
    it -- underdetermined in exactly the direction a horizontal-only task
    would produce."""
    raw = np.array([[-100.0, 0.0], [0.0, 0.0], [100.0, 0.0], [50.0, 0.0]])
    target = np.array([[-5.0, 0.0], [0.0, 0.0], [5.0, 0.0], [2.5, 0.0]])

    with pytest.raises(DegenerateGeometry):
        fit_affine(raw, target)


def test_too_few_points_is_refused_before_conditioning_is_computed():
    raw = np.array([[0.0, 0.0], [1.0, 1.0]])
    with pytest.raises(DegenerateGeometry, match="at least"):
        fit_affine(raw, np.zeros((2, 2)))
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/eye/test_calibration_fit.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `wl_preproc/eye/calibration.py`:

```python
"""Raw Purkinje geometry to degrees of visual angle.

**The feature is P1 - P4.** Both Purkinje images move together under
translation of the eye or camera; their difference cancels it and isolates
rotation. Measured on the reference recording: `corr(P1, P4)` is +0.923 in x
and +0.682 in y -- the shared translational component the difference removes.

**The map is affine, six parameters per eye.** Not scale-plus-offset, because
the camera is never perfectly aligned to the eye's axes and the cross-terms are
real. Not a polynomial, because parent design spec section 7.2 makes gaze
canonical and computed once, and puts revisability in detection instead.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# The smallest ratio of the target constellation's minor to major spread that
# still constrains both axes. A single fixation point gives ~0; a horizontal-only
# task gives ~0; a proper calibration grid gives ~1.
MIN_CONDITIONING = 0.05

_MIN_POINTS = 3


class DegenerateGeometry(ValueError):
    """The target positions cannot constrain six parameters."""


@dataclass(frozen=True, slots=True)
class AffineMap:
    """`[gx, gy] = A @ [dx, dy] + b`, flattened as (a00, a01, b0, a10, a11, b1)."""

    a: tuple[float, float, float, float, float, float]
    n_points: int
    conditioning: float


def _conditioning(target_xy: np.ndarray) -> float:
    """How well the target constellation spans two dimensions.

    Singular values of the mean-centred positions: their ratio is 1 for a
    circular spread and 0 for points on a line or on top of each other. This is
    a property of the TARGETS, not of the raw signal -- a well-spread raw
    cloud from a single target location is noise, not information.
    """
    centred = target_xy - target_xy.mean(axis=0)
    singular = np.linalg.svd(centred, compute_uv=False)
    if singular[0] <= 0:
        return 0.0
    return float(singular[-1] / singular[0])


def fit_affine(raw_xy: np.ndarray, target_xy: np.ndarray) -> AffineMap:
    """Least squares, after refusing geometry that cannot constrain the fit."""
    if raw_xy.shape[0] < _MIN_POINTS:
        raise DegenerateGeometry(
            f"{raw_xy.shape[0]} points; a six-parameter affine needs at least "
            f"{_MIN_POINTS} non-collinear target positions"
        )

    conditioning = _conditioning(target_xy)
    if conditioning < MIN_CONDITIONING:
        raise DegenerateGeometry(
            f"target spread {conditioning:.4f} is below {MIN_CONDITIONING}: the "
            "targets are collinear or coincident, so a fit would be "
            "underdetermined in at least one direction and least squares would "
            "return a minimum-norm solution that looks like a calibration"
        )

    design = np.column_stack([raw_xy, np.ones(raw_xy.shape[0])])
    solution, *_ = np.linalg.lstsq(design, target_xy, rcond=None)
    return AffineMap(
        a=(
            float(solution[0, 0]), float(solution[1, 0]), float(solution[2, 0]),
            float(solution[0, 1]), float(solution[1, 1]), float(solution[2, 1]),
        ),
        n_points=int(raw_xy.shape[0]),
        conditioning=conditioning,
    )


def apply_affine(map_: AffineMap, raw_xy: np.ndarray) -> np.ndarray:
    """Degrees of visual angle for each raw (dx, dy)."""
    a00, a01, b0, a10, a11, b1 = map_.a
    return np.column_stack([
        a00 * raw_xy[:, 0] + a01 * raw_xy[:, 1] + b0,
        a10 * raw_xy[:, 0] + a11 * raw_xy[:, 1] + b1,
    ])
```

- [ ] **Step 4: Run and probe**

Run: `.venv/bin/python -m pytest tests/eye/test_calibration_fit.py -v`
Expected: 4 passed.

Then mutation-check the load-bearing one: delete the `conditioning < MIN_CONDITIONING` branch and confirm `test_a_single_target_location_is_refused` and `test_collinear_targets_are_refused` both fail. Revert.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "eye: the affine fit, and the refusal that makes it safe"
```

---

### Task 6: A narrow `.bhv2` reader

**Files:**
- Create: `wl_preproc/eye/bhv2.py`
- Test: `tests/eye/test_bhv2.py`

**Interfaces:**
- Produces: `Bhv2Calibration` (frozen: `present: bool`, `a: tuple[float, ...] | None`, `pixels_per_degree: float | None`); `read_calibration(path) -> Bhv2Calibration`; `Bhv2Unreadable` exception.

**Scope:** the calibration and `ScreenInfo` only. `BehavioralCodes`, `AnalogData` and trial structure stay out — the code stream already carries what calibration needs, from a source this pipeline trusts more.

- [ ] **Step 1: Write the failing test**

Create `tests/eye/test_bhv2.py`:

```python
import pytest

from wl_preproc.eye.bhv2 import Bhv2Unreadable, read_calibration


def test_a_missing_file_is_absence_not_an_error(tmp_path):
    """Design spec section 4.5: a missing `.bhv2` skips step 2 of the fallback
    chain. It is not a fault -- MonkeyLogic's log is a cross-check, and
    calibration works from the code stream alone."""
    result = read_calibration(tmp_path / "nope.bhv2")

    assert result.present is False
    assert result.a is None


def test_a_truncated_file_raises_rather_than_returning_absence(tmp_path):
    """A file that exists but cannot be parsed is a different fact from a file
    that is not there, and the two must not render identically -- the caller
    decides what to do, but it must be able to tell them apart."""
    bad = tmp_path / "truncated.bhv2"
    bad.write_bytes(b"\x04\x00\x00\x00test\xff\xff")

    with pytest.raises(Bhv2Unreadable):
        read_calibration(bad)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/eye/test_bhv2.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Read the format specification first**

**This is the one task in this plan whose byte-level details were not verified against a real file**, because no sample `.bhv2` was obtainable. Do not guess them.

Open <https://monkeylogic.nimh.nih.gov/docs_BHV2BinaryStructure.html> and read it before writing code. What is known from it: the file is **headerless** and begins directly with variable blocks; each block has **six fields**, where the 1st, 3rd and 5th give the lengths of the 2nd, 4th and 6th; and for MATLAB primitive types the content follows those six fields in **column-major order**.

Write down in your report the exact field widths and type tags you read there, so a later reader can check them against the source rather than against this plan.

- [ ] **Step 4: Implement, narrowly**

Create `wl_preproc/eye/bhv2.py`: walk the top-level blocks, descend only into the two the caller needs, and skip every other block by its declared length without parsing it. On any structural inconsistency — a length running past EOF, an unknown type tag where one is required — raise `Bhv2Unreadable` naming the byte offset.

```python
class Bhv2Unreadable(ValueError):
    """The file exists but its structure could not be walked.

    Distinct from absence on purpose: a missing `.bhv2` is an ordinary skip of
    the fallback chain's step 2 (design spec section 4.5), while a present but
    unparseable one means either a format change or a corrupt transfer, and the
    two must not render identically in the daily report.
    """
```

**Do not** attempt a general MATLAB reader. Struct and cell handling needs to go only as deep as `ScreenInfo` and the calibration; anything else is skipped by length.

- [ ] **Step 5: Write the round-trip test**

Since no real `.bhv2` is available, add a minimal writer in the test module (not in production) that emits the block structure, and assert the reader recovers what it wrote. Name it `_write_minimal_bhv2` and state in its docstring that it exists because no sample file was obtainable, and that a real file must replace it when one is.

**This is the one fixture in this plan that agrees with its own reader by construction** — exactly the circularity Task 1 exists to break for the ohDPI format. Say so in the docstring. It is acceptable here only because the fallback chain validates whatever this reader returns (Task 7), so a wrong parse produces a map that fails validation rather than a wrong gaze.

- [ ] **Step 6: Run and commit**

```bash
.venv/bin/python -m pytest tests/eye -q
git add -A
git commit -m "eye: read MonkeyLogic's calibration, and nothing else from bhv2"
```

---

### Task 7: The fallback chain

**Files:**
- Modify: `wl_preproc/eye/calibration.py`
- Test: `tests/eye/test_calibration_chain.py`

**Interfaces:**
- Consumes: `fit_affine`, `DegenerateGeometry`, `apply_affine`, `read_calibration`.
- Produces: `CalibrationSource` (StrEnum: `FITTED`, `MONKEYLOGIC`, `CARRIED_FORWARD`, `REFUSED`); `Calibration` (frozen: `source`, `map_`, `validation_error_deg`, `reason`, `carried_from`); `validate_map(map_, raw_xy, target_xy) -> float`; `resolve_calibration(...) -> Calibration`; `MAX_VALIDATION_ERROR_DEG`.

- [ ] **Step 1: Write the failing test**

Create `tests/eye/test_calibration_chain.py`:

```python
import numpy as np
import pytest

from wl_preproc.eye.calibration import (
    AffineMap,
    CalibrationSource,
    MAX_VALIDATION_ERROR_DEG,
    validate_map,
)

GOOD = AffineMap(a=(0.05, 0.0, 0.0, 0.0, 0.05, 0.0), n_points=4, conditioning=0.9)


def test_one_point_cannot_fit_a_map_but_can_falsify_one():
    """The asymmetry the whole chain rests on (design spec section 3.5).

    A single central fixation cannot constrain six parameters -- Task 5 refuses
    it. It is entirely adequate to TEST a candidate map: apply it and see where
    the target lands.
    """
    raw = np.array([[0.0, 0.0]])
    target = np.array([[0.0, 0.0]])

    assert validate_map(GOOD, raw, target) == pytest.approx(0.0, abs=1e-9)


def test_a_map_from_the_wrong_input_space_fails_validation_enormously():
    """MonkeyLogic's map is in whatever space MonkeyLogic receives -- pixels
    over the OpenIrisDPI UDP path, volts over ACCESIO, and both may be in use.
    We do not need to know which: a volts-to-degrees map fed pixel differences
    misses by an enormous margin and the chain falls through.
    """
    volts_map = AffineMap(a=(4000.0, 0.0, 0.0, 0.0, 4000.0, 0.0), n_points=4, conditioning=0.9)
    raw = np.array([[10.0, 10.0]])
    target = np.array([[0.5, 0.5]])

    assert validate_map(volts_map, raw, target) > MAX_VALIDATION_ERROR_DEG


def test_a_drifted_map_is_rejected_by_the_same_check():
    drifted = AffineMap(a=(0.05, 0.0, 8.0, 0.0, 0.05, 0.0), n_points=4, conditioning=0.9)
    raw = np.array([[0.0, 0.0]])
    target = np.array([[0.0, 0.0]])

    assert validate_map(drifted, raw, target) > MAX_VALIDATION_ERROR_DEG


def test_the_source_enum_names_all_four_steps():
    assert [s.value for s in CalibrationSource] == [
        "fitted", "monkeylogic", "carried_forward", "refused"
    ]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/eye/test_calibration_chain.py -v`
Expected: FAIL — `ImportError: cannot import name 'CalibrationSource'`.

- [ ] **Step 3: Implement**

Add to `wl_preproc/eye/calibration.py`:

```python
# How far a candidate map may place the session's own fixation before it is
# rejected. Generous relative to a good calibration's residual (well under a
# degree) and far below the error a wrong-input-space map produces (hundreds).
MAX_VALIDATION_ERROR_DEG = 3.0


class CalibrationSource(StrEnum):
    FITTED = "fitted"
    MONKEYLOGIC = "monkeylogic"
    CARRIED_FORWARD = "carried_forward"
    REFUSED = "refused"


def validate_map(map_: AffineMap, raw_xy: np.ndarray, target_xy: np.ndarray) -> float:
    """RMS error in degrees when `map_` is applied to this session's own points.

    **One point cannot fit six parameters but is entirely adequate to test
    them.** That asymmetry is what makes borrowing a calibration safe rather
    than blind, and it is why a session too degenerate to fit is not
    automatically a session with no gaze.
    """
    predicted = apply_affine(map_, raw_xy)
    return float(np.sqrt(np.mean(np.sum((predicted - target_xy) ** 2, axis=1))))
```

Then the chain itself:

```python
@dataclass(frozen=True, slots=True)
class Calibration:
    source: CalibrationSource
    map_: AffineMap | None
    validation_error_deg: float | None
    reason: str
    carried_from: str | None = None


def resolve_calibration(
    raw_xy: np.ndarray,
    target_xy: np.ndarray,
    monkeylogic: AffineMap | None,
    carried: tuple[AffineMap, str] | None,
) -> Calibration:
    """Design spec section 3.5's four steps, in order.

    **Every candidate is validated against this session's own points before
    being accepted**, including a map that came from MonkeyLogic and one
    carried forward from another session. That is what makes borrowing safe:
    one point cannot fit six parameters but is entirely adequate to falsify a
    candidate.
    """
    if raw_xy.shape[0] == 0:
        return Calibration(
            CalibrationSource.REFUSED, None, None,
            "no fixation epoch named a target position",
        )

    try:
        fitted = fit_affine(raw_xy, target_xy)
    except DegenerateGeometry as exc:
        degenerate_reason = str(exc)
    else:
        return Calibration(
            CalibrationSource.FITTED, fitted,
            validate_map(fitted, raw_xy, target_xy), "",
        )

    # MonkeyLogic's precedes carry-forward: it comes from the SAME session and
    # is the map the animal was actually held to, since a gaze-contingent task
    # cannot define a fixation window without one.
    for source, candidate, origin in (
        (CalibrationSource.MONKEYLOGIC, monkeylogic, None),
        (CalibrationSource.CARRIED_FORWARD,
         carried[0] if carried else None,
         carried[1] if carried else None),
    ):
        if candidate is None:
            continue
        error = validate_map(candidate, raw_xy, target_xy)
        if error <= MAX_VALIDATION_ERROR_DEG:
            return Calibration(source, candidate, error, "", origin)

    return Calibration(
        CalibrationSource.REFUSED, None, None,
        f"{degenerate_reason}; no fallback map validated",
    )
```

- [ ] **Step 4: Run, probe, commit**

Mutation-check: raise `MAX_VALIDATION_ERROR_DEG` to 1e9 and confirm both rejection tests fail. Revert.

```bash
.venv/bin/python -m pytest tests/eye -q
git add -A
git commit -m "eye: the validated fallback chain for degenerate calibration"
```

---

### Task 8: Gaze as a computation

**Files:**
- Create: `wl_preproc/eye/gaze.py`
- Test: `tests/eye/test_gaze.py`

**Interfaces:**
- Consumes: `read_columns`, `apply_affine`, `AffineMap`.
- Produces: `purkinje_vector(path, eye) -> np.ndarray`; `gaze_trace(path, eye, map_) -> np.ndarray`; `tracking_loss_fraction(path, eye) -> float`.

- [ ] **Step 1: Write the failing test**

Create `tests/eye/test_gaze.py`:

```python
from pathlib import Path

import numpy as np
import pytest

from wl_preproc.eye.calibration import AffineMap
from wl_preproc.eye.gaze import gaze_trace, purkinje_vector, tracking_loss_fraction

FIXTURE = Path(__file__).parent.parent / "fixtures" / "ohdpi" / "OpenIris-sample.txt"
IDENTITY = AffineMap(a=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0), n_points=4, conditioning=0.9)


def test_the_feature_is_p1_minus_p4():
    """Design spec section 3.2, and the reason a DPI tracker exists: both
    images move together under translation, so the difference isolates
    rotation."""
    from wl_preproc.eye.ohdpi import read_columns

    cols = read_columns(FIXTURE, ["LeftCR1X", "LeftCR1Y", "LeftCR4X", "LeftCR4Y"])
    expected = np.column_stack([
        cols["LeftCR1X"] - cols["LeftCR4X"],
        cols["LeftCR1Y"] - cols["LeftCR4Y"],
    ])

    assert purkinje_vector(FIXTURE, "Left") == pytest.approx(expected)


def test_gaze_is_the_map_applied_to_the_feature():
    trace = gaze_trace(FIXTURE, "Left", IDENTITY)

    assert trace.shape == (200, 2)
    assert trace == pytest.approx(purkinje_vector(FIXTURE, "Left"))


def test_tracking_loss_comes_from_the_file_not_from_a_heuristic():
    """`DataQuality` is 50*P1_valid + 50*P4_valid, so loss is stated by the
    recording rather than inferred from missing values."""
    assert 0.0 <= tracking_loss_fraction(FIXTURE, "Left") <= 1.0
```

- [ ] **Step 2: Run, implement, run**

Expected first: FAIL, `ModuleNotFoundError`.

`gaze.py` reads exactly the columns it needs for the requested eye, computes `CR1 − CR4`, and applies the map. `tracking_loss_fraction` is the fraction of frames whose `{eye}DataQuality` is below 100.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "eye: gaze as a computation over the raw file and its map"
```

---

### Task 9: Schema

**Files:**
- Create: `wl_preproc/schema/eye.py`
- Modify: `wl_preproc/daemon.py` (`_computed_tables`, `_SCHEMA_MODULES`)
- Test: `tests/schema/test_eye_schema.py`

**Interfaces:**
- Produces: `EyeCalibration` (`dj.Computed`), `EyeCalibration.BlockResidual` (part), `EyeQuality` (`dj.Computed`), `activate(prefix=DEFAULT_PREFIX)`.

- [ ] **Step 1: Write the failing test**

Create `tests/schema/test_eye_schema.py`:

```python
import datajoint as dj


def test_no_bare_longblob(schemas_eye):
    """The repo-wide sweep covers this too, but state it here: a bare longblob
    stores a numpy array as its truncated string repr and nothing raises."""
    eye = schemas_eye
    assert "longblob" not in eye.EyeCalibration.definition
    assert "longblob" not in eye.EyeQuality.definition


def test_calibration_source_names_all_four_chain_steps(schemas_eye):
    from wl_preproc.schema._testing import enum_values

    attr = schemas_eye.EyeCalibration.heading.attributes["calibration_source"]
    assert enum_values(attr.type) == {
        "fitted", "monkeylogic", "carried_forward", "refused"
    }


def test_the_affine_parameters_are_nullable(schemas_eye):
    """A refused calibration is a first-class outcome with a stated reason,
    not an error and not a fabricated map."""
    for name in ("a00", "a01", "b0", "a10", "a11", "b1"):
        assert schemas_eye.EyeCalibration.heading.attributes[name].nullable


def test_both_computed_tables_are_daemon_stages():
    """`test_every_computed_table_is_a_daemon_stage` exists because
    `TrialCoverage` was once missing from `_computed_tables()`, which silently
    returned tier D for every session. Two new computed tables land here."""
    from wl_preproc import daemon

    names = {t.__name__ for t in daemon._computed_tables()}
    assert {"EyeCalibration", "EyeQuality"} <= names
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_eye_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.schema.eye'`.

- [ ] **Step 3: Implement the tables**

```python
@schema
class EyeCalibration(dj.Computed):
    definition = """
    # Raw Purkinje geometry to degrees, per eye. Design spec section 6.
    # Key: (subject, session_datetime, eye).
    -> pipeline.Session
    eye : enum('left','right')
    ---
    # Which step of section 3.5's chain produced this map. A borrowed map must
    # never be mistaken for a fitted one.
    calibration_source : enum('fitted','monkeylogic','carried_forward','refused')
    # The six affine parameters, NULL when refused. A refused calibration is a
    # first-class outcome with a stated reason -- not an error, and never a
    # fabricated map.
    a00 = null : double
    a01 = null : double
    b0  = null : double
    a10 = null : double
    a11 = null : double
    b1  = null : double
    # Where this session's own fixation lands under the accepted map. Populated
    # for EVERY source including 'fitted', because it is the one number
    # comparable across all four.
    validation_error_deg = null : double
    n_points                    : int unsigned
    n_from_calibration_block    : int unsigned
    n_from_task_fixation        : int unsigned
    # The target constellation's minor/major spread ratio, which section 3.5's
    # refusal is keyed on.
    conditioning = null         : double
    # A fitted map only: how well it explains the points it was fitted from.
    residual_deg_rms = null     : double
    residual_deg_max = null     : double
    carried_from_session_datetime = null : datetime
    reason = ''                 : varchar(255)
    """

    class BlockResidual(dj.Part):
        definition = """
        # Per-block residual. Section 3.6 measures drift here rather than
        # correcting it: over a ~40 minute session, drift appears as a residual
        # that grows, at no additional cost and without pre-empting the
        # decision to correct it.
        -> master
        -> pipeline.trial.Block
        ---
        n_points         : int unsigned
        residual_deg_rms : double
        """


@schema
class EyeQuality(dj.Computed):
    definition = """
    # Parent design spec section 10's eye-quality line. Key: (subject,
    # session_datetime, eye).
    -> pipeline.Session
    eye : enum('left','right')
    ---
    # From the file's own DataQuality column (0/50/100), so tracking loss is
    # STATED by the recording rather than inferred from missing values.
    tracking_loss_fraction : double
    blink_rate_hz          : double
    """
```

Then `activate(prefix=DEFAULT_PREFIX)`, matching every other schema module.

- [ ] **Step 4: Register both as daemon stages**

Add both to `daemon._computed_tables()` and `eye` to the daemon's schema-module list. `test_every_computed_table_is_a_daemon_stage` will fail until you do — that test exists because `TrialCoverage` was once missing and silently returned tier D for every session.

- [ ] **Step 5: Run and commit**

```bash
.venv/bin/python -m pytest tests/schema -q
git add -A
git commit -m "schema: eye calibration and quality, both daemon stages"
```

---

### Task 10: Populate — the `make()` and its key source

**Files:**
- Modify: `wl_preproc/schema/eye.py`
- Test: `tests/schema/test_eye_populate.py`

- [ ] **Step 1: Write the failing test**

Cover, against a landed synthetic session: a well-conditioned session yields `calibration_source == 'fitted'`; a session with only a central target falls through the chain; a session with no ohDPI file gets `refused` with that specific reason, distinct from the degenerate one.

- [ ] **Step 2: Implement**

Three-part make: `make_fetch` gathers the target positions from decoded events and the raw file path; `make_compute` runs `resolve_calibration`; `make_insert` writes the row and its part rows. Key source is `pipeline.Session` restricted to sessions with an ohDPI recording and assembled events — a session whose events failed to assemble has no gaze and must report *that* reason.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "eye: populate calibration through the fallback chain"
```

---

### Task 11: The daily report

**Files:**
- Modify: `wl_preproc/cli/report.py`
- Test: `tests/cli/test_eye_report.py`

- [ ] **Step 1: Write the failing test**

Assert the Eye section carries tracking-loss, blink rate and validation error; that the `calibration_source` breakdown appears; and that a session with no gaze names its specific reason. Include the negative: two different reasons must not render identically.

- [ ] **Step 2: Implement and commit**

Follow `report.py`'s existing section style. Compute in `build_report`, **not** in `gather_readings` — that function runs on every wl.works poll under the global lock, and the responder reads none of these values.

```bash
git add -A
git commit -m "report: eye quality, and how each calibration was obtained"
```

---

### Task 12: Declarations and the 1c-4 amendments

**Files:**
- Modify: `pyproject.toml`, `wl.yaml`
- Modify: `docs/superpowers/specs/2026-08-16-phase-1c4-timebase-design.md`

- [ ] **Step 1: Declare pandas**

In `pyproject.toml` dependencies and `wl.yaml` `third_party`, with a `why` stating what is checkable from this repository: `wl_preproc/eye/ohdpi.py` imports pandas directly; it arrives unconditionally with `datajoint`, which is a hard dependency; `wlo stack` reads `third_party` alone. Do **not** assert a ceiling you have not verified — check the installed metadata.

- [ ] **Step 2: Amend the 1c-4 spec**

Appended dated blocks, originals left visible, per this repository's correction convention:

- **§12 item 1 is discharged.** The columns are measured; the digital line is `Int0`.
- **Five assumptions were wrong**, including a timestamp unit off by 10⁶ and a glob (`*.csv`) that matches no real file. Record that the section's own reasoning about which assumption would fail silently was correct.

- [ ] **Step 3: Verify and commit**

```bash
.venv/bin/python -m pytest -q          # expect 885 + the new tests
wl-check                                # expect "wl.yaml: no findings"
git add -A
git commit -m "deps: declare pandas, and amend 1c-4 for five wrong assumptions"
```

---

## Not in this plan

- **Saccade detection** — Engbert–Kliegl, U'n'Eye, the agreement metric. Its own spec, pending the U'n'Eye vendoring decision.
- **Drift correction** — spec §3.6 measures drift; correcting it is a later decision from that evidence.
- **`bcam`** — 1c-4's open question 1 named it alongside `ohdpi`; only the latter is settled.
- **Reading `.bhv2` beyond calibration and `ScreenInfo`.**
