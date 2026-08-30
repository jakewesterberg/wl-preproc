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
    """`LeftSeconds` and `RightSeconds` differ by a constant 49.48 ms while
    frame numbers agree exactly -- the cameras are frame-locked, their clocks
    are not. At 500 Hz that offset is ~25 frames.

    `OhdpiRecording` therefore exposes no per-eye timestamp at all. Frame
    number is the index; the rate is derived internally and `Seconds` does not
    escape this module.
    """
    rec = read_ohdpi(FIXTURE)

    assert not hasattr(rec, "seconds")
    assert not hasattr(rec, "left_seconds")
