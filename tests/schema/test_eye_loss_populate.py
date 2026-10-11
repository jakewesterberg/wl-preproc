"""The eye lost, as OpenIrisDPI writes it, through a real daemon pass (design
spec `docs/superpowers/specs/2026-10-11-eye-loss-design.md` sections 3, 4 and
6).

The synthetic generator writes `DataQuality` 100 and a constant pupil on every
frame, as OpenIrisDPI writes `DataQuality` through every blink, so nothing it
writes is ever lost. The losses here are written into the generated file before
landing, as every loss fixture in this suite is: blinks with the pupil and P1
at 0 and `DataQuality` left at 100, P1 off camera with the pupil found, and a
pupil width that is not a number."""

from __future__ import annotations

import pytest

from tests.identities import new_animal
from tests.schema.test_detect_populate import TRIAL_DURATION_S, _build_stepped_session, _first_row_at

# Session times in the detection trial (the fifth), before its first planted
# step at 1.0 s into it.
_TRIAL_S = 4 * TRIAL_DURATION_S
# 40 ms at 500 Hz: the closed phase of a real blink (spec section 1).
_BLINK_ROWS = 20
_BLINK = ("PupilWidth", "PupilHeight", "CR1X", "CR1Y")
# Both eyes.
_FIRST_BLINK_S = _TRIAL_S + 0.50
# The left eye only: 60 ms after the first ends, so the same blink...
_SECOND_BLINK_S = _TRIAL_S + 0.60
# ...and 150 ms after the second ends, so a blink of its own.
_THIRD_BLINK_S = _TRIAL_S + 0.79
# The left eye only: P1 above the camera's view, the pupil found.
_P1_OFF_S = (_TRIAL_S + 0.86, _TRIAL_S + 0.91)
# The right eye only: five frames whose pupil width is not a number.
_UNREADABLE_S = _TRIAL_S + 0.70
_UNREADABLE_ROWS = 5


def _blink(eye: str, start_s: float) -> tuple[str, tuple[str, ...], int, int, str]:
    start = _first_row_at(start_s)
    return eye, _BLINK, start, start + _BLINK_ROWS, "0.0000"


def _losses() -> list[tuple[str, tuple[str, ...], int, int, str]]:
    """`(eye, columns, first row, row after the last, value written)`."""
    unreadable = _first_row_at(_UNREADABLE_S)
    return [
        _blink("Left", _FIRST_BLINK_S), _blink("Right", _FIRST_BLINK_S),
        _blink("Left", _SECOND_BLINK_S), _blink("Left", _THIRD_BLINK_S),
        ("Left", ("CR1Y",), _first_row_at(_P1_OFF_S[0]), _first_row_at(_P1_OFF_S[1]), "0.0000"),
        ("Right", ("PupilWidth",), unreadable, unreadable + _UNREADABLE_ROWS, "NaN"),
    ]


def _plant_losses(session_dir) -> None:
    from wl_preproc.synth.ohdpi import HEADER

    (ohdpi_txt,) = (session_dir / "ohdpi").glob("*.txt")
    lines = ohdpi_txt.read_text(encoding="utf-8").splitlines()
    header_line, data_lines = lines[0], lines[1:]
    for eye, names, start, stop, value in _losses():
        for name in names:
            column = HEADER.index(f"{eye}{name}")
            for row in range(start, stop):
                fields = data_lines[row].split(" ")
                fields[column] = value
                data_lines[row] = " ".join(fields)
    ohdpi_txt.write_text("\n".join([header_line, *data_lines]) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    """Activation only: `daemon.run_once()` registers the paramsets."""
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


@pytest.fixture(scope="module")
def lost_session(daemon_module, prefix, tmp_path_factory):
    session = new_animal().session()
    session_key, segment, _onsets = _build_stepped_session(
        tmp_path_factory, dirname="eyeloss", session_id=session.session_id, subject=session.subject,
        session_datetime=session.session_datetime, seed=1111, after_generate=_plant_losses)
    report = daemon_module.run_once(prefix=prefix)
    return session_key, segment, report


def _stored_mask(session_key: dict, eye: str) -> list[str]:
    """The stored mask, one label per sample; `fixation` is the storage
    encoding of a usable sample (`EyeValidity.make()`)."""
    from wl_preproc.schema import detect

    where = {**session_key, "eye": eye}
    labels = [""] * (detect.EyeValidity & where).fetch1("n_samples")
    for run in (detect.EyeValidity.Run & where).to_dicts():
        labels[run["run_start"]:run["run_stop"]] = [run["label"]] * (run["run_stop"] - run["run_start"])
    return labels


def test_a_blink_written_as_openirisdpi_writes_it_is_stored_as_a_blink(lost_session):
    session_key, _segment, _report = lost_session
    for eye, starts in (("left", (_FIRST_BLINK_S, _SECOND_BLINK_S, _THIRD_BLINK_S)),
                        ("right", (_FIRST_BLINK_S,))):
        labels = _stored_mask(session_key, eye)
        for start_s in starts:
            start = _first_row_at(start_s)
            assert set(labels[start:start + _BLINK_ROWS]) == {"blink"}, (eye, start_s)


def test_p1_off_camera_is_stored_as_invalid_in_its_eye_only(lost_session):
    session_key, _segment, _report = lost_session
    start, stop = _first_row_at(_P1_OFF_S[0]), _first_row_at(_P1_OFF_S[1])

    assert set(_stored_mask(session_key, "left")[start:stop]) == {"invalid"}
    assert set(_stored_mask(session_key, "right")[start:stop]) == {"fixation"}


def test_an_unreadable_pupil_is_stored_as_invalid_not_a_blink(lost_session):
    session_key, _segment, _report = lost_session
    start = _first_row_at(_UNREADABLE_S)

    assert set(_stored_mask(session_key, "right")[start:start + _UNREADABLE_ROWS]) == {"invalid"}


def test_the_mask_counts_the_lost_features_and_the_blinks(lost_session):
    """Raw counts: a blink's P1 is lost too, so each planted blink counts in
    `frac_feature_lost` as well as in `frac_blink`."""
    from wl_preproc.schema import detect

    session_key, _segment, _report = lost_session
    p1_off = _first_row_at(_P1_OFF_S[1]) - _first_row_at(_P1_OFF_S[0])
    for eye, blinks, lost, unreadable in (("left", 3 * _BLINK_ROWS, 3 * _BLINK_ROWS + p1_off, 0),
                                          ("right", _BLINK_ROWS, _BLINK_ROWS, _UNREADABLE_ROWS)):
        row = (detect.EyeValidity & {**session_key, "eye": eye}).fetch1()
        assert row["frac_blink"] == pytest.approx(blinks / row["n_samples"]), eye
        assert row["frac_feature_lost"] == pytest.approx(lost / row["n_samples"]), eye
        assert row["frac_non_finite"] == pytest.approx(unreadable / row["n_samples"]), eye


def test_the_two_stages_raised_nothing_for_this_session(lost_session):
    """`EyeValidity` and `EyeQuality`, for this session's subject. Filtered
    both ways: the suite's other modules leave their own sessions in the
    database, and paramsets for a detector since removed, which
    `EyeDetection` reports for every session."""
    session_key, _segment, report = lost_session
    assert [error for error in report["errors"] if session_key["subject"] in error
            and error.startswith(("EyeValidity ", "EyeQuality "))] == []


def test_a_database_without_the_column_masks_nothing_and_names_the_statement(lost_session, monkeypatch):
    """Spec section 3. DataJoint adds no column to an existing table, and reads
    a table's columns once per process, so the stage refuses while this
    process reads `EyeValidity` without `frac_feature_lost`, and says how to
    fix it: the statement, then a restart."""
    from wl_preproc.schema import detect

    assert not detect._lacks_feature_lost()
    monkeypatch.setattr(detect, "_lacks_feature_lost", lambda: True)

    with pytest.raises(RuntimeError) as raised:
        detect.EyeValidity.populate()

    assert detect._feature_lost_statement() in str(raised.value)
    assert "restart" in str(raised.value)


def test_a_daemon_pass_reports_the_statement_and_parks_no_key(lost_session, daemon_module, prefix, monkeypatch):
    """Refused before any job is reserved: a key whose `make()` raised would be
    parked as an error and never retried (`daemon.reap_stale_jobs`), so the
    statement alone would not bring the stage back."""
    from wl_preproc.schema import detect

    errored = len(detect.EyeValidity.jobs & {"status": "error"})
    monkeypatch.setattr(detect, "_lacks_feature_lost", lambda: True)

    report = daemon_module.run_once(prefix=prefix)

    assert any(error.startswith("EyeValidity: ") and detect._feature_lost_statement() in error
               for error in report["errors"])
    assert len(detect.EyeValidity.jobs & {"status": "error"}) == errored


def test_the_statement_restores_the_column_as_datajoint_declares_it(lost_session):
    """Dropped and restored by the named statement on the test database:
    type, nullability, default, comment and place match what declaring the
    table wrote. This process's DataJoint keeps the columns it read, so the
    rest of the suite is unaffected either way."""
    import datajoint as dj

    from wl_preproc.schema import detect

    table = detect.EyeValidity.full_table_name

    def columns():
        return [row[:2] + row[3:6] + row[8:9] for row in dj.conn().query(f"SHOW FULL COLUMNS FROM {table}").fetchall()]

    declared = columns()
    dj.conn().query(f"ALTER TABLE {table} DROP COLUMN frac_feature_lost")
    try:
        dj.conn().query(detect._feature_lost_statement())
    finally:
        if len(columns()) < len(declared):
            dj.conn().query(f"ALTER TABLE {table} ADD COLUMN frac_feature_lost double DEFAULT NULL AFTER frac_blink")

    assert columns() == declared


def test_eye_quality_counts_the_loss_and_merges_a_blink_that_reopens_briefly(lost_session):
    """Tracking loss is every lost or unreadable frame; blinks less than 100
    ms apart are one, 150 ms apart two (spec section 4). Left: three blinks
    planted, two counted, and P1 off camera as loss but no blink. Right: one
    blink, and five unreadable frames as loss but no blink."""
    from wl_preproc.schema import eye as eye_schema

    session_key, segment, _report = lost_session
    duration_s = segment["end_s"] - segment["start_s"]
    p1_off = _first_row_at(_P1_OFF_S[1]) - _first_row_at(_P1_OFF_S[0])
    rows = {row["eye"]: row for row in (eye_schema.EyeQuality & session_key).to_dicts()}

    assert rows["left"]["tracking_loss_fraction"] == pytest.approx((3 * _BLINK_ROWS + p1_off) / segment["n_samples"])
    assert rows["right"]["tracking_loss_fraction"] == pytest.approx(
        (_BLINK_ROWS + _UNREADABLE_ROWS) / segment["n_samples"])
    assert rows["left"]["blink_rate_hz"] == pytest.approx(2 / duration_s)
    assert rows["right"]["blink_rate_hz"] == pytest.approx(1 / duration_s)
