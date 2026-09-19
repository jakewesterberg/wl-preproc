# Gap-Aware Barcode Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A recording with dropped frames yields a normal eye pipeline, with only the barcode words a gap actually corrupts discarded.

**Architecture:** `extract_ohdpi` stops refusing gapped files. It rebuilds the sync trace at its true length from the file's own frame-number column (`np.repeat`, which is hold-previous fill), so `wl_sync.barcode.edges_from_samples` — which assigns time from row position — is correct unmodified. Because hold-previous is conservative rather than correct, any barcode word overlapping a gap is then dropped: `decode_edges` discards *unverifiable* frames, and a transition lost inside a gap yields a *plausible* frame with a wrong bit.

**Tech Stack:** Python 3.11 (CI also runs 3.13), NumPy, DataJoint 2.3.x, pytest, `wl-sync` as an external contract.

**Spec:** `docs/superpowers/specs/2026-09-19-gap-aware-barcode-extraction-design.md`

## Global Constraints

- **Never modify or vendor `wl_sync`.** `edges_from_samples`, `decode_edges` and `encode` are wl-sync's. CI's "contracts came from wl-sync" step checks `wl_sync.session` and `wl_sync.log` only — the barcode codec is *not* covered, so nothing would catch a vendored copy. The rule binds anyway.
- **`tests/eye/` must import nothing from `wl_preproc.schema`.** The 3.13 cross-check runs `tests/eye` and `tests/contracts` with `--noconftest` in a venv with no DataJoint. Work in this plan lives in `tests/timebase/`, `tests/schema/` and `tests/synth/`, which are not under that constraint.
- **A green local run proves nothing about 3.13.** Local venv is 3.11; CI runs 3.11 and 3.13. Re-resolve dependencies before merging (`uv pip compile pyproject.toml --extra dev --python-version 3.13`) and run the suite against that resolution.
- **Clear `__pycache__` and set `PYTHONDONTWRITEBYTECODE=1` for every mutation check.** A same-length mutation restored within a second otherwise keeps running from a stale `.pyc`.
- **Barcode constants** (from `wl_sync.barcode`, do not restate as literals in production code): `BIT_SLOT_US = 5_000`, `WRAPPER_US = 10_000`, `N_BITS = 32`, `FRAME_US = 200_000`, `IDLE_MIN_US = 400_000`.
- **The reference recording** is at `$WLPP_OHDPI_REFERENCE` and has **zero gaps across 1,177,799 rows**. Tests touching it are `pytest.mark.skipif` on that variable. Never commit the file.

---

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/timebase/extract.py` | `BitStream` gains `gaps` and `n_frames_missing`; `extract_ohdpi` reconstructs instead of raising; the overlap predicate lives here beside `BitStream` |
| `wl_preproc/timebase/segments.py` | `scan_system` applies the filter; `RecordingScan` carries what it cost |
| `wl_preproc/schema/core.py` | `Segment` gains three columns; `Segment.make` writes them; `RejectedSegment` reason gains gap detail |
| `wl_preproc/synth/recipe.py` | `SessionRecipe` carries which eye-camera frames to drop |
| `wl_preproc/synth/ohdpi.py` | `write_ohdpi` omits those rows so a synthetic session carries a real gap |
| `wl_preproc/synth/faults.py` | the dropped-frame fault, beside the existing restart fault |
| `docs/schemas/` | re-exported; CI asserts it is current |

---

## Task 1: `extract_ohdpi` reconstructs instead of refusing

**Files:**
- Modify: `wl_preproc/timebase/extract.py:35-55` (`BitStream`), `:239-291` (`extract_ohdpi`)
- Test: `tests/timebase/test_ohdpi_extraction.py`

**Interfaces:**
- Produces: `BitStream.gaps: tuple[tuple[int, int], ...]` — `(start_us, end_us)` spans, last-known sample to next-known, empty on a clean stream. `BitStream.n_frames_missing: int` — total dropped frames, 0 on a clean stream. `extract_ohdpi(path: Path) -> BitStream` no longer raises on gaps.

**Confirmed before starting:** no existing test pins the gap refusal (`grep -rn "dropped-frame gap" tests/` is empty). If one appears, convert it to assert the new behaviour rather than deleting it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/timebase/test_ohdpi_extraction.py`:

```python
import numpy as np
import pytest

from wl_preproc.timebase.extract import extract_ohdpi

_HEADER = " ".join(["LeftFrameNumber", "LeftSeconds", "Int0", "LeftCR1X", "LeftCR4X"])


def _write_ohdpi(path, frame_numbers, sync_bits, fs_hz=500.0):
    """A minimal OpenIrisDPI file. `Seconds` is derived from the frame NUMBER,
    so a dropped frame costs real time exactly as it does on the instrument --
    a fixture that timestamped by row would hide the bug under test."""
    from wl_preproc.eye.ohdpi import SYNC_BIT_INDEX

    lines = [_HEADER]
    first = frame_numbers[0]
    for number, bit in zip(frame_numbers, sync_bits):
        seconds = (number - first) / fs_hz
        lines.append(f"{number} {seconds:.6f} {bit << SYNC_BIT_INDEX} 1.0 1.0")
    path.write_text("\n".join(lines) + "\n")
    return path


def test_a_clean_recording_has_no_gaps_and_spans_its_own_rows(tmp_path):
    """`np.repeat` with all-ones counts returns its input, so nothing about a
    gap-free file may change. This is the pin that says the whole change is
    invisible to every recording that works today."""
    numbers = list(range(1000, 1200))
    bits = [0] * 200
    stream = extract_ohdpi(_write_ohdpi(tmp_path / "clean.txt", numbers, bits))

    assert stream.gaps == ()
    assert stream.n_frames_missing == 0
    assert stream.n_samples == 200


def test_a_dropped_frame_no_longer_refuses_the_recording(tmp_path):
    """The behaviour this whole plan exists to change. Before it, one dropped
    frame anywhere cost the session its entire eye pipeline -- no
    SystemTimebase fit, no Segment row, not even a RejectedSegment."""
    numbers = list(range(1000, 1100)) + list(range(1103, 1200))  # 3 missing
    bits = [0] * len(numbers)

    stream = extract_ohdpi(_write_ohdpi(tmp_path / "gapped.txt", numbers, bits))

    assert stream.n_frames_missing == 3
    assert len(stream.gaps) == 1
    assert stream.n_samples == 200, (
        "the true span, not the row count: 197 rows covering 200 frames"
    )


def test_the_gap_span_brackets_the_two_known_samples(tmp_path):
    """The level is known AT the last sample before the gap and AT the first
    after it, and unknown strictly between. Bracketing is what makes a word
    overlapping either half-interval untrustworthy; spanning only the missing
    slots would leave those halves looking sound."""
    numbers = list(range(0, 10)) + list(range(13, 20))  # rows 0..9, then 13..19
    bits = [0] * len(numbers)

    stream = extract_ohdpi(_write_ohdpi(tmp_path / "bracket.txt", numbers, bits))

    # fs is derived from the file's own timestamps; at 500 Hz a frame is 2000 us.
    assert stream.gaps == ((round(9 / 500.0 * 1e6), round(13 / 500.0 * 1e6)),)


def test_holding_the_level_across_a_gap_invents_no_transition(tmp_path):
    """Hold-previous fill is the conservative reconstruction: the line appears
    to have held. Anything cleverer -- interpolating, or emitting an edge at
    the gap -- would manufacture evidence about samples nobody has."""
    numbers = list(range(0, 10)) + list(range(13, 20))
    bits = [1] * len(numbers)  # same level either side of the gap

    stream = extract_ohdpi(_write_ohdpi(tmp_path / "hold.txt", numbers, bits))

    assert len(stream.edges) == 1, "one rising edge at sample 0 and nothing else"


def test_an_edge_after_a_gap_gets_the_time_it_would_have_had(tmp_path):
    """The whole point of reconstructing rather than splitting: a transition
    after the gap is timed from its TRUE sample position, not from its row."""
    numbers = list(range(0, 10)) + list(range(13, 20))
    bits = [0] * 10 + [0, 0, 1, 1, 1, 1, 1]  # rises at frame number 15

    stream = extract_ohdpi(_write_ohdpi(tmp_path / "after.txt", numbers, bits))

    assert stream.edges[0] == (round(15 / 500.0 * 1e6), 1)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/timebase/test_ohdpi_extraction.py -q -k "gap or clean_recording or transition or after"`
Expected: FAIL — `ValueError: ... dropped-frame gap(s)` on the gapped cases and `AttributeError: 'BitStream' object has no attribute 'gaps'` on the clean one.

- [ ] **Step 3: Add the two fields to `BitStream`**

In `wl_preproc/timebase/extract.py`, inside the `BitStream` dataclass after `n_samples`:

```python
    #: `(start_us, end_us)` per dropped-frame gap, in this stream's own time
    #: base -- the same one `edges` and `Barcode.start_us` use. Each spans the
    #: LAST KNOWN sample to the NEXT KNOWN one: the level is known at both
    #: ends and unknown strictly between, so bracketing is the conservative
    #: choice and is what makes a word overlapping either half-interval
    #: untrustworthy. Empty for every extractor but `extract_ohdpi`.
    gaps: tuple[tuple[int, int], ...] = ()
    #: Total frames absent across `gaps`. Carried rather than derived from the
    #: spans, which are rounded microseconds and cannot be inverted exactly at
    #: an arbitrary rate.
    n_frames_missing: int = 0
```

- [ ] **Step 4: Replace the refusal with the reconstruction**

In `extract_ohdpi`, delete the whole `if recording.frame_gaps: ... raise ValueError(...)` block and its comment, and replace the body after `recording = read_ohdpi(path)` with:

```python
    # **The sample-index-to-time map is CORRECTED, not refused.**
    # `wl_sync.barcode.edges_from_samples` times each edge as
    # `round(index / fs_hz * 1e6)` -- a row's POSITION. Drop a frame and every
    # later edge is early by the missing frames' duration. The file states
    # exactly which frames are absent, in its own frame-number column, so the
    # trace is rebuilt at true length and the position becomes the true sample
    # index again. wl-sync is untouched: it owns the format the hardware
    # emits, and a second copy of that codec here would be free to drift.
    #
    # `np.repeat` IS hold-previous fill -- a row spanning a gap repeats its own
    # level across the missing slots, so NO TRANSITION IS INVENTED. That is
    # conservative rather than correct (the line may well have transitioned in
    # the hole), which is exactly why `segments.scan_system` then discards any
    # barcode overlapping a gap. See the design spec, sections 2 and 3.
    #
    # `read_ohdpi` already refuses a recording too short to establish a rate,
    # so `offsets[-1]` is always defined here.
    offsets = recording.frame_numbers - recording.frame_numbers[0]
    counts = np.diff(np.append(offsets, offsets[-1] + 1))
    bits = np.repeat((recording.digital >> SYNC_BIT_INDEX) & 1, counts)

    def _at(sample: int) -> int:
        return round(int(sample) / recording.fs_hz * 1_000_000)

    gaps = tuple(
        (_at(offsets[index]), _at(offsets[index + 1]))
        for index in np.flatnonzero(counts > 1)
    )
    return BitStream(
        edges=tuple(edges_from_samples(list(bits), fs_hz=recording.fs_hz)),
        fs_hz=recording.fs_hz,
        n_samples=int(bits.size),
        gaps=gaps,
        n_frames_missing=int(counts.sum() - counts.size),
    )
```

Add `import numpy as np` to the module's imports if it is not already there.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/timebase/ -q`
Expected: PASS, and no other test in `tests/timebase/` regresses.

- [ ] **Step 6: Run the whole suite**

Run: `find . -name __pycache__ -type d -prune -exec rm -rf {} + ; PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q`
Expected: PASS. `tests/timebase/test_ohdpi_extraction.py::test_it_extracts_a_bitstream_from_the_real_fixture` asserts `n_samples == 200` on a clean fixture and must still pass unchanged.

- [ ] **Step 7: Commit**

```bash
git add wl_preproc/timebase/extract.py tests/timebase/test_ohdpi_extraction.py
git commit -m "timebase: correct the sample index across a gap instead of refusing the file"
```

---

## Task 2: drop the barcode words a gap corrupts

**Files:**
- Modify: `wl_preproc/timebase/extract.py` (add `barcode_clear_of_gaps`), `wl_preproc/timebase/segments.py:135-173`
- Test: `tests/timebase/test_segments.py`

**Interfaces:**
- Consumes: `BitStream.gaps`, `BitStream.n_frames_missing` (Task 1).
- Produces: `barcode_clear_of_gaps(barcode, gaps) -> bool`. `RecordingScan.n_frame_gaps: int`, `.n_frames_missing: int`, `.n_barcodes_dropped: int`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/timebase/test_segments.py`:

```python
from wl_sync.barcode import FRAME_US, Barcode

from wl_preproc.timebase.extract import barcode_clear_of_gaps


def test_a_word_overlapping_a_gap_is_not_clear():
    """The word under test starts 1000 us before the gap opens, so the gap
    falls inside its 200 ms extent."""
    word = Barcode(value=7, start_us=0)

    assert not barcode_clear_of_gaps(word, ((1_000, 3_000),))


def test_a_word_wholly_before_or_after_a_gap_is_clear():
    early = Barcode(value=7, start_us=0)
    late = Barcode(value=8, start_us=1_000_000)

    assert barcode_clear_of_gaps(early, ((500_000, 600_000),))
    assert barcode_clear_of_gaps(late, ((500_000, 600_000),))


def test_the_boundaries_are_half_open_at_both_ends():
    """A word ending exactly where a gap opens, or opening exactly where one
    closes, is KEPT. Both cases are sound: the sample at each gap boundary is
    a known one, so no bit of that word was reconstructed.

    Pinned because `<` and `<=` are both defensible-looking here and they
    disagree on precisely these two words."""
    ends_at_gap_start = Barcode(value=1, start_us=0)
    starts_at_gap_end = Barcode(value=2, start_us=300_000)

    assert barcode_clear_of_gaps(ends_at_gap_start, ((FRAME_US, 300_000),))
    assert barcode_clear_of_gaps(starts_at_gap_end, ((200_001, 300_000),))


def test_a_word_is_dropped_for_any_one_of_several_gaps():
    word = Barcode(value=7, start_us=0)

    assert not barcode_clear_of_gaps(word, ((900_000, 910_000), (1_000, 3_000)))


def test_a_stream_with_no_gaps_clears_every_word():
    assert barcode_clear_of_gaps(Barcode(value=7, start_us=0), ())
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/timebase/test_segments.py -q -k "clear or half_open or overlapping"`
Expected: FAIL with `ImportError: cannot import name 'barcode_clear_of_gaps'`.

- [ ] **Step 3: Implement the predicate**

In `wl_preproc/timebase/extract.py`, directly below the `BitStream` class:

```python
def barcode_clear_of_gaps(barcode, gaps) -> bool:
    """Whether every sample this word was decoded from was actually recorded.

    **`decode_edges` cannot answer this, and that is the whole reason this
    exists.** It discards *unverifiable* frames -- ones failing its wrapper
    and trailer checks. A transition lost inside a gap produces no edge at
    all, so `_level_at` simply reports the level that was held; if the lost
    transition is in the data region rather than a wrapper, every structural
    check still passes and one bit decodes to the WRONG VALUE. The frame is
    not unverifiable. It is plausible and wrong. At 500 Hz a bit slot is 2.5
    samples, so a single dropped frame is about four tenths of a bit --
    enough to swallow a narrow pulse whole.

    Half-open at both ends: a word ending exactly where a gap opens, or
    opening exactly where one closes, is kept, because the sample at each gap
    boundary is a known one.
    """
    stop_us = barcode.start_us + FRAME_US
    return not any(
        barcode.start_us < gap_end and gap_start < stop_us
        for gap_start, gap_end in gaps
    )
```

`FRAME_US` is already importable from `wl_sync.barcode`; add it to this module's existing import line.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/timebase/test_segments.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing test for the wiring**

Add to `tests/timebase/test_segments.py`:

```python
def test_a_scan_records_what_the_gaps_cost_it(tmp_path):
    """The counts are evidence and are carried, not discarded. A gap that cost
    nothing and a gap that cost three barcodes are different facts about a
    session, and `Segment` stores both (Task 4)."""
    from wl_preproc.timebase.segments import RecordingScan
    from wl_preproc.timebase.extract import BitStream

    stream = BitStream(
        edges=(), fs_hz=500.0, n_samples=200,
        gaps=((10_000, 14_000),), n_frames_missing=2,
    )
    scan = RecordingScan(
        path=tmp_path / "x.txt", stream=stream, barcodes=(), n_barcodes_dropped=3
    )

    assert (scan.n_frame_gaps, scan.n_frames_missing, scan.n_barcodes_dropped) == (1, 2, 3)
```

- [ ] **Step 6: Run it to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/timebase/test_segments.py -q -k what_the_gaps_cost`
Expected: FAIL — `RecordingScan.__init__() got an unexpected keyword argument 'n_barcodes_dropped'`.

- [ ] **Step 7: Carry the counts on `RecordingScan` and apply the filter**

In `wl_preproc/timebase/segments.py`, add to the `RecordingScan` dataclass after `barcodes`:

```python
    #: How many decoded words were discarded because a gap fell inside them.
    #: Zero on every clean recording, which is all of them until a real
    #: session drops a frame.
    n_barcodes_dropped: int = 0

    @property
    def n_frame_gaps(self) -> int:
        return len(self.stream.gaps)

    @property
    def n_frames_missing(self) -> int:
        return self.stream.n_frames_missing
```

Then in `scan_system`, replace the `scans.append(...)` block with:

```python
        stream = EXTRACTORS[system](path)
        decoded = decode_edges(list(stream.edges))
        kept = tuple(b for b in decoded if barcode_clear_of_gaps(b, stream.gaps))
        scans.append(
            RecordingScan(
                path=path,
                stream=stream,
                barcodes=kept,
                n_barcodes_dropped=len(decoded) - len(kept),
            )
        )
```

Import `barcode_clear_of_gaps` alongside the existing `EXTRACTORS, find_recordings` import.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/timebase/ -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add wl_preproc/timebase/extract.py wl_preproc/timebase/segments.py tests/timebase/test_segments.py
git commit -m "timebase: discard the barcode words a gap falls inside"
```

---

## Task 3: the corruption test — the evidence the exclusion rule rests on

**Files:**
- Test: `tests/timebase/test_gap_corruption.py` (create)

**Interfaces:**
- Consumes: `extract_ohdpi` (Task 1), `barcode_clear_of_gaps` (Task 2).

This task adds no production code. It exists because without it the exclusion rule in Task 2 reads like belt-and-braces over `decode_edges` and will eventually be simplified away by someone who checks that `decode_edges` "already validates".

- [ ] **Step 1: Write the test**

Create `tests/timebase/test_gap_corruption.py`:

```python
"""A gap can produce a barcode that is PLAUSIBLE and WRONG.

`wl_sync.barcode.decode_edges` discards partial and unverifiable frames, and
it checks hard: an idle before the start, four wrapper levels, two trailer
levels. It is correct for what it was written against -- a trace with no
holes, where an absent edge means the line did not transition.

This module demonstrates the case it was not written against, because the
exclusion rule in `timebase/extract.py::barcode_clear_of_gaps` is only
justified if that case is real.
"""

import numpy as np
import pytest
from wl_sync.barcode import BIT_SLOT_US, N_BITS, WRAPPER_US, decode_edges, encode

from wl_preproc.timebase.extract import barcode_clear_of_gaps, extract_ohdpi

FS_HZ = 500.0
#: One 1 bit surrounded by zeros, in the middle of the data region. `encode`
#: emits bits MSB-first, so bit position 15 is the 17th of the 32 data slots
#: -- far from every wrapper and trailer check.
VALUE = 1 << 15


def _render(value: int, pre_roll_frames: int) -> list[int]:
    """`value` as one 0/1 sample per frame, the way `synth/ohdpi.py` does it."""
    line = [0] * pre_roll_frames
    for level, duration_us in encode(value):
        line.extend([level] * round(duration_us * 1e-6 * FS_HZ))
    line.extend([0] * pre_roll_frames)
    return line


def _write(path, sync_bits, dropped: set[int]):
    from wl_preproc.eye.ohdpi import SYNC_BIT_INDEX

    rows = ["LeftFrameNumber LeftSeconds Int0 LeftCR1X LeftCR4X"]
    for index, bit in enumerate(sync_bits):
        if index in dropped:
            continue
        rows.append(f"{index} {index / FS_HZ:.6f} {bit << SYNC_BIT_INDEX} 1.0 1.0")
    path.write_text("\n".join(rows) + "\n")
    return path


def test_a_gap_inside_a_data_bit_decodes_to_the_wrong_value(tmp_path):
    """The failure `decode_edges` cannot see. Delete the frames carrying the
    one 1 bit: hold-previous fill reads it as 0, every wrapper and trailer
    check still passes, and the word decodes to a value that was never sent.

    If this test ever fails because the value now comes back CORRECT, the
    reconstruction has started inventing transitions and section 2.1 of the
    spec has been violated -- do not relax this test."""
    # 400 ms of pre-roll satisfies IDLE_MIN_US before the frame starts.
    pre_roll = round(0.4 * FS_HZ)
    line = _render(VALUE, pre_roll)

    # The 17th data slot, after the two leading wrappers.
    bit_start_us = 2 * WRAPPER_US + 16 * BIT_SLOT_US
    first = pre_roll + round(bit_start_us * 1e-6 * FS_HZ)
    last = pre_roll + round((bit_start_us + BIT_SLOT_US) * 1e-6 * FS_HZ)
    dropped = {index for index in range(first, last) if line[index] == 1}
    assert dropped, "the fixture must actually remove a HIGH sample"

    stream = extract_ohdpi(_write(tmp_path / "corrupt.txt", line, dropped))
    decoded = decode_edges(list(stream.edges))

    assert len(decoded) == 1, "the frame still passes every structural check"
    assert decoded[0].value != VALUE, (
        "the whole premise of the exclusion rule: a gap in the data region "
        "produces a plausible frame carrying a value that was never sent"
    )
    assert not barcode_clear_of_gaps(decoded[0], stream.gaps), (
        "and the exclusion rule catches it"
    )


def test_the_same_word_decodes_correctly_with_no_frames_dropped(tmp_path):
    """The control. Without it the test above could pass because the fixture
    never encoded `VALUE` properly in the first place."""
    pre_roll = round(0.4 * FS_HZ)
    line = _render(VALUE, pre_roll)

    stream = extract_ohdpi(_write(tmp_path / "clean.txt", line, set()))
    decoded = decode_edges(list(stream.edges))

    assert [b.value for b in decoded] == [VALUE]
    assert stream.gaps == ()
    assert barcode_clear_of_gaps(decoded[0], stream.gaps)
```

- [ ] **Step 2: Run both tests**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/timebase/test_gap_corruption.py -q -v`
Expected: PASS. If `test_a_gap_inside_a_data_bit_decodes_to_the_wrong_value` fails at `len(decoded) == 1`, the dropped frames broke a structural check instead — move the target bit further from the wrappers (raise the shift in `VALUE`) until a frame still decodes, because a word that fails validation is not the case under test.

- [ ] **Step 3: Write the two cases the exclusion rule must NOT over-reach on**

Append to `tests/timebase/test_gap_corruption.py`:

```python
def test_a_gap_in_the_idle_between_words_costs_nothing(tmp_path):
    """The common case, and the point of the whole exercise. Words are 200 ms
    and separated by at least 400 ms of idle, so most dropped frames land
    between them and cost nothing at all. A rule that dropped words for a gap
    ANYWHERE in the recording would throw away the session it was written to
    save."""
    pre_roll = round(0.4 * FS_HZ)
    line = _render(VALUE, pre_roll) + _render(VALUE + 1, pre_roll)

    # Three frames from the middle of the trailing idle of the first word.
    first_word_end = pre_roll + round(FRAME_US * 1e-6 * FS_HZ)
    dropped = {first_word_end + 10, first_word_end + 11, first_word_end + 12}

    stream = extract_ohdpi(_write(tmp_path / "idle.txt", line, dropped))
    decoded = decode_edges(list(stream.edges))
    kept = [b for b in decoded if barcode_clear_of_gaps(b, stream.gaps)]

    assert stream.gaps != (), "the fixture must actually contain a gap"
    assert [b.value for b in kept] == [VALUE, VALUE + 1]


def test_a_gap_inside_one_word_leaves_its_neighbour_alone(tmp_path):
    """Exclusion is per-word, not per-recording. Pinned because the cheapest
    wrong implementation -- drop every word once any gap exists -- passes the
    corruption test above and loses everything else."""
    pre_roll = round(0.4 * FS_HZ)
    line = _render(VALUE, pre_roll) + _render(VALUE + 1, pre_roll)

    bit_start_us = 2 * WRAPPER_US + 16 * BIT_SLOT_US
    first = pre_roll + round(bit_start_us * 1e-6 * FS_HZ)
    last = pre_roll + round((bit_start_us + BIT_SLOT_US) * 1e-6 * FS_HZ)
    dropped = {index for index in range(first, last) if line[index] == 1}

    stream = extract_ohdpi(_write(tmp_path / "neighbour.txt", line, dropped))
    kept = [b for b in decode_edges(list(stream.edges))
            if barcode_clear_of_gaps(b, stream.gaps)]

    assert [b.value for b in kept] == [VALUE + 1], (
        "the damaged word is dropped and the intact one survives"
    )
```

Add `FRAME_US` to this module's `wl_sync.barcode` import.

- [ ] **Step 4: Run them**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/timebase/test_gap_corruption.py -q -v`
Expected: PASS, all four.

- [ ] **Step 5: Commit**

```bash
git add tests/timebase/test_gap_corruption.py
git commit -m "test: a gap in the data region decodes to a plausible wrong value"
```

---

## Task 4: `Segment` stores what the gaps cost

**Files:**
- Modify: `wl_preproc/schema/core.py:74-94` (`Segment.definition`), `:182-204` (`Segment.make`)
- Modify: `docs/schemas/` (re-export)
- Test: `tests/schema/test_segment_populate.py`

**Interfaces:**
- Consumes: `RecordingScan.n_frame_gaps`, `.n_frames_missing`, `.n_barcodes_dropped` (Task 2).

- [ ] **Step 1: Write the failing test**

Add to `tests/schema/test_segment_populate.py` (create if absent, following the fixtures in `tests/schema/test_detect_populate.py`):

```python
def test_a_segment_records_the_gaps_its_recording_had(dj_conn, prefix, gapped_session):
    """A session that previously produced NOTHING now produces eye data, and
    its alignment rests on a trace with holes. A consumer must be able to
    separate those sessions from clean ones -- spec section 5, and the same
    'derived, not asserted' rule Phase 1c-5's TimingProvenance follows."""
    from wl_preproc.schema import core

    core.Segment.populate(gapped_session, suppress_errors=False)
    row = (core.Segment & gapped_session).fetch1()

    assert row["n_frame_gaps"] == 1
    assert row["n_frames_missing"] == 3
    assert row["n_barcodes_dropped"] >= 0


def test_a_clean_segment_records_zero_for_all_three(dj_conn, prefix, stepped_session):
    """The columns must be zero rather than null on every recording that works
    today, so a query for 'sessions with gaps' cannot accidentally match one."""
    from wl_preproc.schema import core

    core.Segment.populate(stepped_session, suppress_errors=False)
    for row in (core.Segment & stepped_session).fetch(as_dict=True):
        assert (row["n_frame_gaps"], row["n_frames_missing"], row["n_barcodes_dropped"]) == (0, 0, 0)
```

The `gapped_session` fixture comes from Task 6; until then, mark this test `pytest.mark.xfail(reason="needs the dropped-frame fault, Task 6", strict=True)` and remove the marker in Task 6.

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/schema/test_segment_populate.py -q -k clean_segment`
Expected: FAIL with `KeyError: 'n_frame_gaps'`.

- [ ] **Step 3: Add the three columns**

In `wl_preproc/schema/core.py`, append to `Segment.definition` after `n_barcodes`:

```
    n_frame_gaps      : int unsigned  # dropped-frame discontinuities in this recording
    n_frames_missing  : int unsigned  # frames absent across those gaps
    n_barcodes_dropped: int unsigned  # words discarded because a gap fell inside them
```

- [ ] **Step 4: Write them in `Segment.make`**

In the `accepted.append({...})` dict, after `"n_barcodes": offset.n_barcodes,`:

```python
                    # Three columns rather than one summary, for the reason
                    # Phase 1c-5's TimingProvenance gives: spec section 4.7's
                    # "derived, not asserted" holds on the row itself only if
                    # each input is stored separately. They answer different
                    # questions -- how fragmented, how much time is absent,
                    # what it cost the alignment -- and none is derivable from
                    # the others.
                    "n_frame_gaps": scan.n_frame_gaps,
                    "n_frames_missing": scan.n_frames_missing,
                    "n_barcodes_dropped": scan.n_barcodes_dropped,
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/schema/ -q`
Expected: PASS.

- [ ] **Step 6: Re-export the schema**

Run: `.venv/bin/python -m wl_preproc.cli.main schemas export --out docs/schemas && git diff --stat -- docs/schemas`
Expected: `docs/schemas` shows the three added columns. CI asserts this file is current, and wl.works and the behaviour-camera project build against it — the columns are purely additive, so nothing breaks, but the re-export must ride in the same commit.

- [ ] **Step 7: Commit**

```bash
git add wl_preproc/schema/core.py tests/schema/test_segment_populate.py docs/schemas
git commit -m "schema: Segment records the gaps its recording had"
```

---

## Task 5: a gapped file that still cannot align says so

**Files:**
- Modify: `wl_preproc/timebase/segments.py:55-69` (the reason vocabulary), `wl_preproc/schema/core.py:169-175` (`Segment.make`'s reject branch)
- Test: `tests/timebase/test_segments.py`, `tests/schema/test_segment_populate.py`

**Interfaces:**
- Consumes: `RecordingScan.n_frame_gaps`, `.n_barcodes_dropped` (Task 2).
- Produces: `segments.GAP_CORRUPTED = "gap_corrupted"`, added to `REJECTION_REASONS`.

**`RejectedSegment.reason` is a CLOSED VOCABULARY, not free text.**
`segments.REJECTION_REASONS` documents every value it can hold
(`too_short`, `no_barcode`, `unfitted_system`). Appending
`"(3 dropped-frame gap(s))"` to an existing reason would make that constant a
lie — so a gapped file gets its own reason instead, which is also the
queryable form. Nothing currently reads `REJECTION_REASONS`; it is
documentation-as-code, and that is precisely why it must not be allowed to
drift.

- [ ] **Step 1: Write the failing test**

Add to `tests/timebase/test_segments.py`:

```python
def test_the_gap_reason_is_in_the_documented_vocabulary():
    """`REJECTION_REASONS` states every value `RejectedSegment.reason` can
    hold. Nothing reads it today, which is exactly how it would rot: a reason
    written to the table but missing from the set is a silent lie in the one
    document a reader would trust."""
    from wl_preproc.timebase import segments

    assert segments.GAP_CORRUPTED in segments.REJECTION_REASONS
```

Add to `tests/schema/test_segment_populate.py`:

```python
def test_a_file_gapped_below_the_floor_names_the_gaps_as_the_reason(
    dj_conn, prefix, heavily_gapped_session
):
    """A file whose surviving barcodes fall below the alignment floor is
    already rejected. Without this it is rejected as `no_barcode`, which is
    true of the file and wrong about the cause -- the barcodes were there and
    the gaps removed them."""
    from wl_preproc.schema import core
    from wl_preproc.timebase import segments

    core.Segment.populate(heavily_gapped_session, suppress_errors=False)
    reason = (core.RejectedSegment & heavily_gapped_session).fetch1("reason")

    assert reason == segments.GAP_CORRUPTED
```

Mark the second `pytest.mark.xfail(reason="needs the dropped-frame fault, Task 6", strict=True)` until Task 6 lands.

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/timebase/test_segments.py -q -k gap_reason`
Expected: FAIL with `AttributeError: module 'wl_preproc.timebase.segments' has no attribute 'GAP_CORRUPTED'`.

- [ ] **Step 3: Add the reason**

In `wl_preproc/timebase/segments.py`, beside `UNFITTED_SYSTEM`:

```python
# Also not a verdict `classify_segment` can reach: the file DID carry
# barcodes and dropped frames removed them, so both `no_barcode` and
# `too_short` are true of what is left and wrong about why. Distinguished
# because the two call for different actions -- a short file is a recording
# that did not happen, a gap-corrupted one is a recording the camera failed
# to keep up with.
GAP_CORRUPTED = "gap_corrupted"
```

and add it to the `REJECTION_REASONS` frozenset.

- [ ] **Step 4: Use it in the reject branch**

In `wl_preproc/schema/core.py`, replace the first reject branch:

```python
            if scan.verdict != segments.ALIGNABLE:
                # Gaps outrank the verdict as the REASON: `classify_segment`
                # sees only how many barcodes survived, and when gaps are what
                # removed them it names the symptom rather than the cause.
                reason = (
                    segments.GAP_CORRUPTED
                    if scan.n_barcodes_dropped
                    else scan.verdict
                )
                rejected.append({**key, "file_path": file_path, "reason": reason})
                continue
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/timebase tests/schema -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/timebase/segments.py wl_preproc/schema/core.py tests/timebase/test_segments.py tests/schema/test_segment_populate.py
git commit -m "timebase: a gap-corrupted file is its own rejection reason"
```

---

## Task 6: the synthetic generator can drop frames

**Files:**
- Modify: `wl_preproc/synth/ohdpi.py:210` (`write_ohdpi`), `wl_preproc/synth/faults.py`
- Test: `tests/synth/test_faults.py`, and the fixtures in `tests/schema/conftest.py`

**Interfaces:**
- Produces: `SessionRecipe.ohdpi_dropped_frames: tuple[int, ...] = ()`; `faults.drop_ohdpi_frames(frame_count, at_s, n_frames, fps) -> tuple[int, ...]`; fixtures `gapped_session` and `heavily_gapped_session`.

`synth/faults.py` has a mid-session *restart* fault (`split_into_segments`) and a behaviour-camera `drop_camera_frames`, but nothing that drops frames from an OpenIrisDPI recording — so no synthetic session can carry a gap end to end today.

- [ ] **Step 1: Write the failing test**

Add to `tests/synth/test_faults.py`:

```python
def test_a_dropped_frame_fault_leaves_a_real_gap_in_the_written_file(tmp_path):
    """The fixture must drop the ROW and keep the frame NUMBER sequence's
    hole, which is what the reader detects. A fixture that renumbered rows
    contiguously would produce a file with no gap at all and would make every
    gap test pass against nothing."""
    from wl_preproc.eye.ohdpi import read_ohdpi
    from wl_preproc.synth import faults
    from wl_preproc.synth.ohdpi import write_ohdpi
    from wl_preproc.synth.recipe import SessionRecipe
    from wl_preproc.synth.truth import build_truth

    recipe = SessionRecipe(seed=1)
    truth = build_truth(recipe)
    dropped = faults.drop_ohdpi_frames(frame_count=1000, at_s=1.0, n_frames=3)

    recipe = dataclasses.replace(recipe, ohdpi_dropped_frames=dropped)
    path = write_ohdpi(tmp_path, recipe, truth)
    recording = read_ohdpi(path)

    assert len(recording.frame_gaps) == 1
    assert recording.frame_gaps[0].n_missing == 3
```

Adjust `SessionRecipe(...)`/`build_truth(...)` to whatever the existing tests in `tests/synth/` construct — copy their call, do not invent arguments.

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/synth/test_faults.py -q -k dropped_frame_fault`
Expected: FAIL with `AttributeError: module 'wl_preproc.synth.faults' has no attribute 'drop_ohdpi_frames'`.

- [ ] **Step 3: Add the fault**

In `wl_preproc/synth/faults.py`, beside `drop_camera_frames`:

```python
def drop_ohdpi_frames(
    frame_count: int, at_s: float, n_frames: int, fps: float = OHDPI_FPS
) -> tuple[int, ...]:
    """Row indices for one contiguous run of dropped eye-camera frames.

    A contiguous run rather than a scatter, because that is what the failure
    looks like: OpenIrisDPI's own notebook describes it as the computer being
    too slow to process images in time, which loses a burst. `split_into_
    segments` above models a mid-session RESTART, which is a different fault
    -- that one shifts time, this one removes it.
    """
    first = int(at_s * fps)
    stop = min(first + n_frames, frame_count)
    return tuple(range(first, stop))
```

Import `OHDPI_FPS` from `wl_preproc.synth.ohdpi` (or wherever the existing modules take it from).

- [ ] **Step 4: Honour it in `write_ohdpi`**

Carry it on `SessionRecipe` rather than threading a parameter through the
session writer: add `ohdpi_dropped_frames: tuple[int, ...] = ()` to
`SessionRecipe` (`wl_preproc/synth/recipe.py`), defaulted empty so every
existing profile is unchanged, and read it in `write_ohdpi` as
`dropped = set(recipe.ohdpi_dropped_frames)`. Where the rows are emitted,
skip those indices while leaving the frame NUMBER derived from the original
index:

```python
    # Skip the ROW, keep the numbering: the frame counter is the camera's own
    # and a dropped frame leaves a hole in it. Renumbering contiguously would
    # produce a file with no detectable gap, which would make every gap test
    # in the suite pass against a fixture that has none.
    dropped = set(dropped_frames)
```

and guard the per-row write with `if index in dropped: continue`. `Seconds` is already derived from the frame index, so it stays consistent automatically.

- [ ] **Step 5: Run the test to verify it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/synth/ -q`
Expected: PASS, and no existing synth test regresses (the default `()` leaves every current caller unchanged).

- [ ] **Step 6: Add the two session fixtures and un-xfail Tasks 4 and 5**

`tests/schema/conftest.py` holds only `enum_values`; the session builders live in the test files themselves. Copy the `SessionRecipe(...)` builder at `tests/schema/test_eye_populate.py:55` — its real arguments are `session_id`, `subject`, `rig`, `systems`, `blocks`, `montages`, `n_ap_channels`, `ap_sample_rate_hz` — and add two fixtures beside it, differing only in `ohdpi_dropped_frames`:

- `gapped_session` — one 3-frame gap placed in the idle BETWEEN barcode words, so the recording stays alignable and the columns from Task 4 are exercised on a session that still works.
- `heavily_gapped_session` — gaps frequent enough that the surviving barcodes fall below the alignment floor, so Task 5's rejection path is reached.

Place the first gap deliberately, not at an arbitrary frame: `truth.barcodes` gives each word's start time, and a gap must land in the idle to leave the recording alignable. Assert in the fixture itself that `read_ohdpi(...).frame_gaps` is non-empty, so a fixture that silently stopped planting a gap fails loudly rather than making every gap test pass against nothing.

Then remove the `xfail` markers added in Tasks 4 and 5.

- [ ] **Step 7: Run the whole suite**

Run: `find . -name __pycache__ -type d -prune -exec rm -rf {} + ; PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q`
Expected: PASS, with Tasks 4 and 5's tests now passing for real rather than xfailing.

- [ ] **Step 8: Commit**

```bash
git add wl_preproc/synth/ tests/synth/ tests/schema/
git commit -m "synth: a session can carry dropped eye-camera frames"
```

---

## Task 7: validity criterion 4 fires end to end

**Files:**
- Test: `tests/schema/test_eye_populate.py`

**Interfaces:**
- Consumes: `gapped_session` (Task 6).

Criterion 4 — the frame-gap window that marks eye samples around a gap
invalid — is written and unit-tested in `tests/eye/detect/test_validity.py`,
and is **dead code in production**: a gapped session raised in
`extract_ohdpi` and never reached detection, so this criterion has never run
against a stored trace however well its unit tests pass.

`EyeValidity` stores it directly as `frac_frame_gap`, so the check is exact
rather than a proxy.

- [ ] **Step 1: Write the failing test**

```python
def test_the_frame_gap_criterion_fires_on_a_stored_trace(dj_conn, prefix, gapped_session):
    """Validity criterion 4's first run in production. `frac_frame_gap` is a
    RAW per-criterion count over all samples (see `EyeValidity`'s own
    definition), so a non-zero value means this criterion rejected something
    rather than that the mask rejected something for any reason."""
    from wl_preproc.schema import detect

    detect.EyeValidity.populate(gapped_session, suppress_errors=False)
    rows = (detect.EyeValidity & gapped_session).fetch(as_dict=True)

    assert rows, "the gapped session must produce a validity row at all"
    assert all(row["status"] == "computed" for row in rows), (
        "a gapped recording must no longer refuse: that was the old behaviour"
    )
    assert any(row["frac_frame_gap"] > 0 for row in rows)


def test_the_frame_gap_criterion_is_silent_on_a_clean_session(dj_conn, prefix, stepped_session):
    """The control. Without it the test above could pass because the criterion
    fires on every session, which would mean it is measuring nothing."""
    from wl_preproc.schema import detect

    detect.EyeValidity.populate(stepped_session, suppress_errors=False)
    rows = (detect.EyeValidity & stepped_session).fetch(as_dict=True)

    assert rows
    assert all(row["frac_frame_gap"] == 0 for row in rows)
```

Use whichever clean-session fixture that file already has in place of
`stepped_session` if the name differs — copy it from a neighbouring test
rather than introducing a new one.

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/schema/test_eye_populate.py -q -k frame_gap_criterion`
Expected: before Tasks 1-6, FAIL — the populate raises on the gapped fixture.
After them, PASS. If the first test passes but the control also reports a
non-zero `frac_frame_gap`, the criterion is firing on clean data and the
finding is about the criterion, not this branch — stop and report it.

- [ ] **Step 3: Run the suite and commit**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q`

```bash
git add tests/schema/test_eye_populate.py
git commit -m "test: the frame-gap validity criterion fires on a stored trace"
```

---

## Task 8: the reference recording is unchanged

**Files:**
- Test: `tests/timebase/test_ohdpi_extraction.py`

**Interfaces:**
- Consumes: everything above.

The strongest evidence available that this change is invisible to every recording that works today: the real reference recording has zero gaps across 1,177,799 rows, so `counts` is all ones and `np.repeat` returns its input.

- [ ] **Step 1: Write the test**

```python
@pytest.mark.skipif(
    not os.environ.get("WLPP_OHDPI_REFERENCE"),
    reason="needs the real reference recording",
)
def test_the_reference_recording_is_untouched_by_gap_handling(capsys):
    """1,177,799 rows and zero gaps, so the reconstruction is the identity and
    every edge, barcode and sample count must be exactly what it was.

    Asserted on the gap fields rather than against a stored golden: a golden
    would pin this file's contents, and what is being checked is that the CODE
    PATH is inert, not that the recording never changes."""
    from wl_sync.barcode import decode_edges

    from wl_preproc.timebase.extract import barcode_clear_of_gaps, extract_ohdpi

    stream = extract_ohdpi(pathlib.Path(os.environ["WLPP_OHDPI_REFERENCE"]))
    barcodes = decode_edges(list(stream.edges))

    with capsys.disabled():
        print(
            f"\n  reference: {stream.n_samples} samples, {len(stream.edges)} edges, "
            f"{len(barcodes)} barcodes, {len(stream.gaps)} gaps"
        )

    assert stream.gaps == ()
    assert stream.n_frames_missing == 0
    assert all(barcode_clear_of_gaps(b, stream.gaps) for b in barcodes)
    assert len(barcodes) > 0, "the recording must still decode barcodes at all"
```

- [ ] **Step 2: Run it against the recording**

Run: `WLPP_OHDPI_REFERENCE=<path> PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/timebase/test_ohdpi_extraction.py -q -s -k reference_recording_is_untouched`
Expected: PASS, printing the counts. Record them in the handoff.

- [ ] **Step 3: Commit**

```bash
git add tests/timebase/test_ohdpi_extraction.py
git commit -m "test: the reference recording is inert to gap handling"
```

---

## Task 9: mutation verification and the close-out

**Files:**
- Modify: `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-09-19-gap-aware-barcode-extraction-built.md`

- [ ] **Step 1: Run the mutation battery**

For each mutation: apply it by literal source edit, clear `__pycache__`, run `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/timebase tests/schema -q`, record which named test fails, then revert.

| # | mutation | expected to be caught by |
|---|---|---|
| 1 | `barcode.start_us < gap_end` → `<=` | `test_the_boundaries_are_half_open_at_both_ends` |
| 2 | `gap_start < stop_us` → `<=` | `test_the_boundaries_are_half_open_at_both_ends` |
| 3 | the predicate inverted (`return any(...)`) | `test_a_word_wholly_before_or_after_a_gap_is_clear` |
| 4 | gap span → `(_at(offsets[i] + 1), _at(offsets[i + 1] - 1))` (missing slots only) | `test_the_gap_span_brackets_the_two_known_samples` |
| 5 | `np.append(offsets, offsets[-1] + 1)` → `np.append(offsets, offsets[-1])` | `test_a_dropped_frame_no_longer_refuses_the_recording` |
| 6 | `n_samples=int(bits.size)` → `n_samples=recording.n_frames` | `test_a_dropped_frame_no_longer_refuses_the_recording` |
| 7 | `n_frames_missing` → `0` | `test_a_dropped_frame_no_longer_refuses_the_recording` |
| 8 | `Segment.make` writes `0` for `n_barcodes_dropped` | `test_a_segment_records_the_gaps_its_recording_had` |

Any mutation that SURVIVES is a coverage gap: add the test that catches it, or — if the difference is genuinely unreachable — say so at the line with the reason, rather than leaving it looking covered.

- [ ] **Step 2: Run both interpreters**

```bash
find . -name __pycache__ -type d -prune -exec rm -rf {} +
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
uv pip compile pyproject.toml --extra dev --python-version 3.13 -o /tmp/ci.txt
uv venv --python 3.13 /tmp/venv_ci && uv pip install --python /tmp/venv_ci/bin/python -r /tmp/ci.txt
uv pip install --python /tmp/venv_ci/bin/python -e . --no-deps
PYTHONDONTWRITEBYTECODE=1 /tmp/venv_ci/bin/python -m pytest -q
```

Expected: both green. Record both counts.

- [ ] **Step 3: Write the handoff and update the records**

`docs/handoffs/2026-09-19-gap-aware-barcode-extraction-built.md` carries: the suite counts from Step 2, the mutation table with what each was caught by, the reference-recording counts from Task 8, and any mutation that survived with the reason. Update `docs/CHECKPOINT.md`'s header and its "what is next" item 5 — which currently states the superseded 2026-09-01 remedy — and `wl.yaml`'s `status`. Run `wl-check`.

- [ ] **Step 4: Commit, merge, and read CI off the run**

```bash
git add docs/ wl.yaml && git commit -m "docs: gap-aware barcode extraction built"
git checkout main && git merge --no-ff <branch> && git push origin main
gh run list --branch main --limit 2
```

Do not record CI as green from the absence of a failure notice — read the job statuses off `gh run view <id>`.
