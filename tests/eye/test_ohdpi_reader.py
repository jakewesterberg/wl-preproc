from pathlib import Path

import numpy as np
import pytest

from wl_preproc.eye.ohdpi import (
    SYNC_BIT_INDEX,
    SYNC_WORD_COLUMN,
    FrameGap,
    read_ohdpi,
)

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


def test_one_absent_required_column_is_named_specifically(tmp_path):
    """Pins the diagnostic, not just the refusal.

    Before this task's fix round, this test alone could not tell OUR check
    from pandas' own: pandas' `usecols` mismatch fires for ANY absent column
    before `read_columns` ever sees a DataFrame, and `read_ohdpi` used to
    re-wrap every failure as "unrecognised header" regardless of cause -- so
    deleting our check changed nothing observable (found by mutation, not
    assumed). Both are gone now: `read_columns` raises its own "header is
    missing [...]" naming exactly what's absent, and `read_ohdpi` no longer
    wraps anything, so a caller always sees the real cause. A header that is
    otherwise real OpenIris, short exactly one required column, is the case
    an actual format change would produce -- and the one where a generic
    message would waste someone's afternoon.
    """
    header = "LeftFrameNumber LeftSeconds Int0 LeftCR1X SomeOtherColumn\n"
    rows = "308788 5416.9374 12 434.8742 0\n308789 5416.9394 12 434.8742 0\n"
    bad = tmp_path / "one_column_short.txt"
    bad.write_text(header + rows, encoding="utf-8")

    # Matches on the two-word PHRASE "header is missing", never a bare word:
    # `tmp_path` names its own directory after this test function, and the
    # error message embeds the full file path -- so a bare `match="missing"`
    # here would be satisfied by a sanitised path segment like
    # "test_a_missing_required_column0" regardless of whether read_columns'
    # own diagnostic had even run. (Found the hard way: an earlier,
    # differently-named draft of this exact test did exactly that.) A phrase
    # with a literal space cannot come from a path segment -- pytest
    # sanitises those to identifier characters joined by "_", never " ".
    with pytest.raises(ValueError, match="header is missing") as excinfo:
        read_ohdpi(bad)
    assert "LeftCR4X" in str(excinfo.value)


def test_seconds_is_never_offered_as_session_time():
    """`LeftSeconds` and `RightSeconds` differ by 49.40-49.50 ms over this
    200-row fixture while frame numbers agree exactly. That gap is not fixed
    over a full session: across the whole 1,177,799-row reference
    recording it drifts smoothly from 49.5 ms to 45.8 ms -- the cameras are
    frame-locked by the trigger chain, their clocks are not. At 500 Hz the
    fixture's offset alone is ~25 frames.

    `OhdpiRecording` therefore exposes no per-eye timestamp at all. Frame
    number is the index; the rate is derived internally and `Seconds` does not
    escape this module.
    """
    rec = read_ohdpi(FIXTURE)

    assert not hasattr(rec, "seconds")
    assert not hasattr(rec, "left_seconds")


def test_the_arrays_reject_in_place_mutation():
    """`frozen=True` blocks `rec.frame_numbers = other_array` but not
    `rec.frame_numbers[0] = 0` -- frozen stops attribute reassignment, not
    array mutation, and a frozen dataclass holding a mutable array is only
    frozen in name. Task 2 is this dataclass's first consumer; closing this
    before there IS a consumer is what makes it cheap.

    Does NOT currently discriminate `read_ohdpi`'s own `.setflags(write=
    False)` calls from pandas' default behaviour -- checked by mutation, not
    assumed: removing them, this test still passes, because pandas >= 3.0
    makes copy-on-write mandatory (confirmed directly: pandas itself warns
    "Copy-on-Write can no longer be disabled" when asked to), and CoW's
    `to_numpy()` already returns a read-only view before this function ever
    calls `.setflags`. The explicit calls stay anyway: `pandas` is not yet a
    declared dependency here (arriving unconditionally via `datajoint` until
    Task 12 pins a floor), and nothing today stops an environment with
    pandas < 3.0, where `to_numpy()` returns a writeable array by default --
    at which point this is the only thing protecting Task 2 from a silent
    in-place mutation. A test that pins that outcome for the versions
    actually installed anywhere this runs is still worth having even though
    it cannot currently isolate which of the two mechanisms produced it.
    """
    rec = read_ohdpi(FIXTURE)

    assert rec.frame_numbers.flags.writeable is False
    assert rec.digital.flags.writeable is False

    with pytest.raises(ValueError, match="read-only"):
        rec.frame_numbers[0] = 0

    with pytest.raises(ValueError, match="read-only"):
        rec.digital[0] = 0


def _minimal_rows(frame_numbers, fs_hz=500.0) -> str:
    """The five columns `read_ohdpi` requires, for a hand-built file.

    `LeftSeconds` is derived from the FRAME NUMBER, not the row index, so a
    dropped frame costs real elapsed time exactly as it does on the
    instrument -- a fixture that timestamped by row would make a gapped file
    indistinguishable from a clean one on the rate, which is the thing these
    tests measure.
    """
    header = " ".join(["LeftFrameNumber", "LeftSeconds", "Int0", "LeftCR1X", "LeftCR4X"])
    first = frame_numbers[0]
    rows = [
        f"{number} {(number - first) / fs_hz:.4f} {12 + (index % 2)} 100.0000 40.0000"
        for index, number in enumerate(frame_numbers)
    ]
    return header + "\n" + "\n".join(rows) + "\n"


def test_a_clean_recording_reports_no_gaps():
    assert read_ohdpi(FIXTURE).frame_gaps == ()


def test_a_dropped_frame_is_a_gap_not_a_lost_session(tmp_path):
    """The shipped reader raised on any frame-number jump, so a 39-minute
    recording was lost over one dropped frame.

    OpenIrisDPI's own tutorial notebook treats gaps as ordinary -- "this can
    happen if the computer is too slow to process the image in time" -- and
    marks them as invalid REGIONS. The reference recording happened to have
    zero gaps across 1,177,799 rows, which is why nothing caught this.
    """
    path = tmp_path / "gapped.txt"
    # 308788..308792, then 308796..308798: three frames dropped after row 4.
    numbers = [308788, 308789, 308790, 308791, 308792, 308796, 308797, 308798]
    path.write_text(_minimal_rows(numbers), encoding="utf-8")

    rec = read_ohdpi(path)

    assert rec.n_frames == 8
    assert rec.frame_gaps == (FrameGap(row=4, n_missing=3),)
    # The gap is REPORTED, not repaired: the rows themselves are untouched, so
    # a consumer excludes the region rather than receiving a silently
    # renumbered trace.
    assert rec.frame_numbers[4] == 308792
    assert rec.frame_numbers[5] == 308796


def test_every_gap_is_reported_not_only_the_first(tmp_path):
    """A caller excluding invalid regions needs all of them. The shipped
    reader raised on `gaps[0]` and never looked further, so "the first gap" is
    the shape a careless port of that logic would keep."""
    path = tmp_path / "two_gaps.txt"
    numbers = [100, 101, 103, 104, 105, 110, 111]
    path.write_text(_minimal_rows(numbers), encoding="utf-8")

    assert read_ohdpi(path).frame_gaps == (
        FrameGap(row=1, n_missing=1),
        FrameGap(row=4, n_missing=4),
    )


def test_the_rate_counts_dropped_frames_as_elapsed_time(tmp_path):
    """Frame numbers, not rows. The camera kept running through a gap, so the
    elapsed span covers frame periods the file has no row for; dividing by the
    row count instead would UNDERSTATE the rate by exactly the dropped frames.

    Here 7 rows span 11 frame periods at 500 Hz. Rows would give
    `6 / 0.0220 = 272.7 Hz`; frame numbers give 500.
    """
    path = tmp_path / "gapped_rate.txt"
    numbers = [100, 101, 103, 104, 105, 110, 111]
    path.write_text(_minimal_rows(numbers, fs_hz=500.0), encoding="utf-8")

    rec = read_ohdpi(path)

    assert rec.fs_hz == pytest.approx(500.0, rel=1e-6)
    assert rec.n_frames == 7  # and NOT 12, the frame span -- rows are rows


def test_the_barcode_extractor_refuses_a_gapped_recording(tmp_path):
    """The refusal `read_ohdpi` used to make, moved to the one caller that
    genuinely cannot tolerate a gap.

    `edges_from_samples` turns a sample INDEX into a time by dividing by
    `fs_hz`, so every edge after a gap is early by the dropped frames'
    duration and the barcodes decoded from them would name sync times that
    never happened -- a silently wrong alignment for the whole session rather
    than a visibly absent one. The eye path has no such indexing and keeps the
    recording.
    """
    from wl_preproc.timebase.extract import extract_ohdpi

    path = tmp_path / "gapped_for_barcode.txt"
    path.write_text(_minimal_rows([100, 101, 103, 104, 105, 106]), encoding="utf-8")

    # The reader itself is content.
    assert read_ohdpi(path).frame_gaps == (FrameGap(row=1, n_missing=1),)

    with pytest.raises(ValueError, match="dropped-frame gap"):
        extract_ohdpi(path)
