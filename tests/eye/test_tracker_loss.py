"""Where the tracker lost the eye, read from what OpenIrisDPI actually writes
(design spec `2026-10-11-eye-loss-design.md` section 2).

`DataQuality` is 100 on every frame of the reference recording, blinks
included; a feature the plugin did not find is written as exactly 0. Every
test here builds the columns by hand, because the committed 200-row fixture
loses nothing and the synthetic generator never loses the eye."""

from pathlib import Path

import numpy as np

from wl_preproc.eye.ohdpi import read_tracker_loss, tracker_loss

FIXTURE = Path(__file__).parent.parent / "fixtures" / "ohdpi" / "OpenIris-sample.txt"

_NAMES = ("DataQuality", "PupilWidth", "PupilHeight", "CR1X", "CR1Y", "CR4X", "CR4Y")
# Typical values from the reference recording: nothing here is 0 or below 100.
_TRACKED = (100.0, 526.5, 112.3, 346.3, 289.8, 396.9, 196.8)


def _columns(n: int, eye: str = "Left") -> dict[str, np.ndarray]:
    return {f"{eye}{name}": np.full(n, value) for name, value in zip(_NAMES, _TRACKED)}


def _rows(mask: np.ndarray) -> list[int]:
    return [int(i) for i in np.flatnonzero(mask)]


def test_a_fully_tracked_frame_is_neither_lost_nor_unreadable():
    loss = tracker_loss(_columns(5), "Left")

    assert not loss.blink.any() and not loss.feature_lost.any() and not loss.unreadable.any()
    assert not loss.lost.any()


def test_a_pupil_not_found_is_a_blink():
    """The closed phase of every blink on the reference recording: the
    pupil's width and height read 0 while `DataQuality` stays 100."""
    columns = _columns(6)
    columns["LeftPupilWidth"][2] = 0.0
    columns["LeftPupilHeight"][4] = 0.0

    loss = tracker_loss(columns, "Left")

    assert _rows(loss.blink) == [2, 4]
    assert not loss.feature_lost.any()
    assert _rows(loss.lost) == [2, 4]


def test_data_quality_below_one_hundred_is_still_a_blink():
    """Kept for any tracker or version that does declare a failure."""
    columns = _columns(5)
    columns["LeftDataQuality"][1] = 50.0
    columns["LeftDataQuality"][3] = 0.0

    assert _rows(tracker_loss(columns, "Left").blink) == [1, 3]


def test_p1_or_p4_at_zero_is_a_lost_feature_not_a_blink():
    """P1 off the camera's view at upward gaze reads `CR1Y` 0 with the pupil
    still found: the gaze vector is not a position there, but the eye is
    open."""
    columns = _columns(8)
    columns["LeftCR1X"][1] = 0.0
    columns["LeftCR1Y"][3] = 0.0
    columns["LeftCR4X"][5] = 0.0
    columns["LeftCR4Y"][7] = 0.0

    loss = tracker_loss(columns, "Left")

    assert _rows(loss.feature_lost) == [1, 3, 5, 7]
    assert not loss.blink.any()
    assert _rows(loss.lost) == [1, 3, 5, 7]


def test_a_real_shaped_blink_is_a_blink_with_p1_lost_too():
    """As the reference recording has it: pupil and P1 at 0, P4 still
    reported, `DataQuality` 100."""
    columns = _columns(6)
    for name in ("PupilWidth", "PupilHeight", "CR1X", "CR1Y"):
        columns[f"Left{name}"][2:4] = 0.0

    loss = tracker_loss(columns, "Left")

    assert _rows(loss.blink) == [2, 3]
    assert _rows(loss.feature_lost) == [2, 3]
    assert _rows(loss.lost) == [2, 3]


def test_a_missing_value_is_unreadable_and_nothing_else():
    """A NaN compares false against both tests, so it is neither a blink nor
    a lost feature; it declares nothing, and the mask withholds it as
    `invalid` (as it withholds a missing quality flag)."""
    columns = _columns(len(_NAMES))
    for row, name in enumerate(_NAMES):
        columns[f"Left{name}"][row] = np.nan

    loss = tracker_loss(columns, "Left")

    assert _rows(loss.unreadable) == list(range(len(_NAMES)))
    assert not loss.blink.any() and not loss.feature_lost.any()


def test_the_two_eyes_columns_are_not_confused():
    columns = {**_columns(4, "Left"), **_columns(4, "Right")}
    columns["LeftPupilWidth"][0] = 0.0
    columns["RightCR1Y"][3] = 0.0

    left, right = tracker_loss(columns, "Left"), tracker_loss(columns, "Right")

    assert _rows(left.blink) == [0] and not left.feature_lost.any()
    assert _rows(right.feature_lost) == [3] and not right.blink.any()


def test_the_file_is_read_by_the_same_rule(tmp_path):
    """The seven columns per eye, read from a file in OpenIris's
    whitespace-separated shape; a column of another eye is not read."""
    columns = {**_columns(3, "Left"), **_columns(3, "Right")}
    columns["LeftPupilHeight"][1] = 0.0
    columns["RightCR4X"][2] = 0.0
    names = list(columns)
    path = tmp_path / "loss.txt"
    path.write_text(
        " ".join(names) + "\n"
        + "".join(" ".join(f"{columns[name][row]:.4f}" for name in names) + "\n" for row in range(3))
    )

    left, right = read_tracker_loss(path, "Left"), read_tracker_loss(path, "Right")

    assert _rows(left.blink) == [1] and not left.feature_lost.any()
    assert _rows(right.feature_lost) == [2] and not right.blink.any()


def test_the_real_fixture_loses_nothing():
    """The committed slice of a real recording: no 0 in any of the seven
    columns, and `DataQuality` 100 throughout (checked 2026-10-11)."""
    for eye in ("Left", "Right"):
        loss = read_tracker_loss(FIXTURE, eye)
        assert loss.blink.size == 200
        assert not loss.lost.any() and not loss.unreadable.any()
