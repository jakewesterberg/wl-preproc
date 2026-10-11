from pathlib import Path

import numpy as np
import pytest

from wl_preproc.eye.calibration import CalibrationMap, CalibrationModel, apply_map
from wl_preproc.eye.gaze import gaze_trace, purkinje_vector, tracking_loss_fraction
from wl_preproc.eye.ohdpi import read_columns

FIXTURE = Path(__file__).parent.parent / "fixtures" / "ohdpi" / "OpenIris-sample.txt"
_AFFINE = CalibrationModel.AFFINE

# `basis(_, AFFINE)` column order: (const, dx, dy) per axis.
IDENTITY = CalibrationMap(
    model=_AFFINE, x=(0.0, 1.0, 0.0), y=(0.0, 0.0, 1.0), n_points=4, conditioning=0.9
)

# Scale, shear AND offset all nonzero on both rows, so a map applied to any
# nonzero vector moves it off the original -- unlike IDENTITY, which cannot
# tell "the map was applied" from "the map was skipped".
SCALE_SHEAR = CalibrationMap(
    model=_AFFINE, x=(1.0, 2.0, 0.5), y=(-2.0, -0.5, 3.0), n_points=4, conditioning=0.9
)


def test_the_feature_is_p1_minus_p4():
    """Design spec section 3.2, and the reason a DPI tracker exists: both
    images move together under translation, so the difference isolates
    rotation."""
    cols = read_columns(FIXTURE, ["LeftCR1X", "LeftCR1Y", "LeftCR4X", "LeftCR4Y"])
    expected = np.column_stack([
        cols["LeftCR1X"] - cols["LeftCR4X"],
        cols["LeftCR1Y"] - cols["LeftCR4Y"],
    ])

    assert purkinje_vector(FIXTURE, "Left") == pytest.approx(expected)


def test_purkinje_vector_discriminates_eyes():
    """The synthetic generator aside, THIS fixture is real bytes: every one of
    the 200 rows gives Left and Right genuinely different P1-P4 geometry with
    zero exact ties (verified directly against the fixture). A reader that
    silently ignored `eye` and always read Left would pass a same-eye test but
    fails this one."""
    left = purkinje_vector(FIXTURE, "Left")
    right = purkinje_vector(FIXTURE, "Right")

    assert left.shape == right.shape == (200, 2)
    assert not np.any(left == right)


def test_gaze_is_the_map_applied_to_the_feature():
    trace = gaze_trace(FIXTURE, "Left", IDENTITY)

    assert trace.shape == (200, 2)
    assert trace == pytest.approx(purkinje_vector(FIXTURE, "Left"))


def test_gaze_trace_discriminates_eyes():
    """None of this module's other tests ever call `gaze_trace` with
    `eye="Right"` -- caught by mutation, not assumed: hardcoding `gaze_trace`
    to always read "Left" internally (ignoring its own `eye` argument) still
    passed every other test in this file. Reuses the fixture's real,
    zero-tie Left/Right geometry (see `test_purkinje_vector_discriminates_eyes`)
    under IDENTITY, where `gaze_trace` reduces to `purkinje_vector`."""
    left = gaze_trace(FIXTURE, "Left", IDENTITY)
    right = gaze_trace(FIXTURE, "Right", IDENTITY)

    assert not np.any(left == right)


def test_gaze_actually_applies_the_map_not_just_the_feature():
    """The identity-map test above proves the plumbing (path, eye, and column
    selection all line up) but NOT that `apply_map` runs at all -- an
    implementation that returns `purkinje_vector(...)` unchanged, skipping the
    map entirely, would still satisfy it under IDENTITY. `SCALE_SHEAR` has
    nonzero scale, shear and offset on both output rows, so gaze_trace can
    only match `apply_map(SCALE_SHEAR, feature)` if the map was genuinely
    applied, and cannot equal the untransformed feature itself."""
    feature = purkinje_vector(FIXTURE, "Left")

    trace = gaze_trace(FIXTURE, "Left", SCALE_SHEAR)

    assert trace == pytest.approx(apply_map(SCALE_SHEAR, feature))
    assert not np.allclose(trace, feature)


def test_tracking_loss_is_zero_on_this_clean_fixture():
    """Pins what the committed 200-row fixture actually contains (checked
    directly: every row of both `LeftDataQuality` and `RightDataQuality` is
    100, and no pupil, P1 or P4 value is 0) rather than the vacuous `0.0 <= x <= 1.0`, which a broken function
    returning any constant in range would also satisfy."""
    assert tracking_loss_fraction(FIXTURE, "Left") == 0.0
    assert tracking_loss_fraction(FIXTURE, "Right") == 0.0


def test_tracking_loss_counts_every_frame_the_tracker_lost_per_eye(tmp_path):
    """The share of frames lost or unreadable by `ohdpi.TrackerLoss`'s rule
    (design spec `2026-10-11-eye-loss-design.md` section 4): a pupil not
    found, `DataQuality` below 100, P1 or P4 at 0, or a value that is not a
    number. `DataQuality` alone said 0 on every real recording, since
    OpenIrisDPI writes 100 through every blink. Still a LOWER bound: the
    plugin can report a confident, wrong position, which no 0 marks.

    Built rather than read from the real fixture, which loses nothing (see
    the clean-fixture test above). Left and Right are given DIFFERENT loss
    patterns -- 2/5 vs 4/5 -- so a mix-up between the two eyes' columns, not
    just a wrong rule, is caught.
    """
    names = ("DataQuality", "PupilWidth", "PupilHeight", "CR1X", "CR1Y", "CR4X", "CR4Y")
    tracked = ("100", "526.5", "112.3", "346.3", "289.8", "396.9", "196.8")
    rows = [{f"{eye}{name}": value for eye in ("Left", "Right") for name, value in zip(names, tracked)}
            for _ in range(5)]
    rows[1]["LeftDataQuality"] = "50"
    rows[3]["LeftPupilWidth"] = "0"
    rows[0]["RightCR1Y"] = "0"
    rows[1]["RightCR4X"] = "0"
    rows[2]["RightPupilHeight"] = "NaN"
    rows[4]["RightDataQuality"] = "0"
    header = list(rows[0])
    path = tmp_path / "partial_loss.txt"
    path.write_text(" ".join(header) + "\n" + "".join(" ".join(row[name] for name in header) + "\n" for row in rows))

    assert tracking_loss_fraction(path, "Left") == pytest.approx(2 / 5)
    assert tracking_loss_fraction(path, "Right") == pytest.approx(4 / 5)
