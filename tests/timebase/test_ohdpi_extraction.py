from pathlib import Path

import numpy as np
import pytest

from wl_preproc.timebase.extract import extract_ohdpi, find_recordings

FIXTURE = Path(__file__).parent.parent / "fixtures" / "ohdpi" / "OpenIris-sample.txt"

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
    after the gap is timed from its TRUE sample position, not from its row.

    The leading `(0, 0)` is `edges_from_samples`' own: it starts with
    `previous = None`, so the FIRST sample always emits an edge whatever its
    level. Pinned here rather than indexed past, because filtering it out
    costs a barcode -- `decode_edges` with `start_us=None` needs a first
    transition to anchor the idle before the first frame."""
    numbers = list(range(0, 10)) + list(range(13, 20))
    bits = [0] * 10 + [0, 0, 1, 1, 1, 1, 1]  # rises at frame number 15

    stream = extract_ohdpi(_write_ohdpi(tmp_path / "after.txt", numbers, bits))

    assert stream.edges == ((0, 0), (round(15 / 500.0 * 1e6), 1))


def test_the_glob_matches_a_real_recording_and_not_its_events_sibling(tmp_path):
    """OpenIris writes `<session>.txt` AND `<session>-events.txt` into the same
    folder. The shipped glob was `*.csv`, which matches neither -- a real
    session would have yielded no ohDPI recording at all.

    Goes through `find_recordings`, not a bare `tmp_path.glob(_RECORDING_GLOBS[...])`:
    the Controller ruling keeps `_RECORDING_GLOBS["ohdpi"]` broad (`"*.txt"`,
    matching both files) and does the exclusion in `find_recordings` instead
    (`_RECORDING_EXCLUDE_SUFFIXES`), so the glob pattern alone no longer tells
    the whole story -- only the discovery function's OUTPUT does. Checked by
    running the original brief's version of this test (raw `tmp_path.glob`)
    against this implementation: it fails, because the glob by itself still
    matches the events file too.
    """
    (tmp_path / "OpenIris-2024Jul31-114628.txt").write_text("x", encoding="utf-8")
    (tmp_path / "OpenIris-2024Jul31-114628-events.txt").write_text("x", encoding="utf-8")
    (tmp_path / "OpenIris-2024Jul31-114628-log.log").write_text("x", encoding="utf-8")

    matched = sorted(p.name for p in find_recordings("ohdpi", tmp_path))

    assert matched == ["OpenIris-2024Jul31-114628.txt"]


def test_a_session_legitimately_named_pass_is_still_found(tmp_path):
    """Pins WHY the exclusion is a suffix list and not `"*[!s].txt"`: that
    glob's criterion is "the stem does not end in s", which has nothing to do
    with the actual reason (excluding the `-events.txt` sibling) and would
    silently drop a session honestly named `...-pass.txt`. This is the case
    that distinguishes the two: it fails under the clever glob and passes
    under the explicit exclusion list (Controller ruling)."""
    (tmp_path / "OpenIris-2024Jul31-114628-pass.txt").write_text("x", encoding="utf-8")

    matched = sorted(p.name for p in find_recordings("ohdpi", tmp_path))

    assert matched == ["OpenIris-2024Jul31-114628-pass.txt"]


def test_it_extracts_a_bitstream_from_the_real_fixture():
    """`n_samples` and `fs_hz` alone do not exercise the sync-bit mask: both
    are copied straight through from `OhdpiRecording.n_frames`/`.fs_hz`,
    unrelated to whether `Int0` was ever shifted by `SYNC_BIT_INDEX` (checked
    by mutation -- dropping the mask, `n_samples`/`fs_hz` still passed). `Int0`
    is {12, 13} across the fixture (`tests/eye/test_ohdpi_reader.py`), both
    truthy, so a caller that forgot the mask would see every sample as HIGH
    and `edges_from_samples` would collapse the whole recording to the one
    edge at its own start -- which is what the extra assertion below rules
    out.
    """
    stream = extract_ohdpi(FIXTURE)

    assert stream.n_samples == 200
    assert 495.0 < stream.fs_hz < 502.0
    # Rules out the un-masked collapse: Int0's low bits (0 and 1) both toggle
    # on the reference recording, so a correctly masked bit 0 must produce
    # more than the single start-of-recording edge an always-truthy raw
    # sample would.
    assert len(stream.edges) > 1
