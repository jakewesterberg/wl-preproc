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
from wl_sync.barcode import BIT_SLOT_US, FRAME_US, N_BITS, WRAPPER_US, decode_edges, encode

from wl_preproc.timebase.extract import barcode_clear_of_gaps, extract_ohdpi

FS_HZ = 500.0
#: One 1 bit surrounded by zeros, in the middle of the data region. `encode`
#: emits bits MSB-first, so bit position 15 is the 17th of the 32 data slots
#: -- far from every wrapper and trailer check.
VALUE = 1 << 15


def _render(value: int, pre_roll_frames: int) -> list[int]:
    """`value` as one 0/1 sample per frame, the way `synth/ohdpi.py` does it.

    Boundaries are rounded from the CUMULATIVE elapsed time, never from each
    pulse's own duration in isolation. `BIT_SLOT_US` is exactly 2.5 samples at
    500 Hz, and rounding that in isolation -- once per data bit, 32 times --
    rounds the same half-sample down every single time (Python's
    round-half-to-even sends 2.5 to 2, not 3), leaving the rendered frame 16
    samples short of `FRAME_US` regardless of which value is encoded. That is
    enough to fail `decode_edges`'s own completeness check
    (`ordered[-1][0] < start + FRAME_US`) on every frame this would render,
    corrupted or not -- caught here because the CONTROL test below failed
    the same way the corruption test did. `wl_preproc/synth/ohdpi.py
    ::_digital_line` avoids exactly this already, by converting a running
    cursor to a frame index at each pulse boundary instead of rounding each
    pulse alone; this mirrors that, which is what this docstring's second
    half has claimed all along.
    """
    line = [0] * pre_roll_frames
    cursor_us = 0.0
    written = pre_roll_frames
    for level, duration_us in encode(value):
        cursor_us += duration_us
        stop = pre_roll_frames + round(cursor_us * 1e-6 * FS_HZ)
        line.extend([level] * (stop - written))
        written = stop
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
