"""True visual angle in the detection stage (design spec
`2026-10-30-true-angle-saccades-design.md` sections 3 to 6): every default
paramset detects in true angle; Engbert-Kliegl is handed the local-frame
velocity and every run is measured on it; a per-axis paramset, registered
only on request, detects and measures as v1 did."""

from __future__ import annotations

import pytest

from tests.identities import new_animal
from tests.schema.test_detect_populate import _build_stepped_session, _stored_inputs
from wl_preproc.eye.detect.geometry import PER_AXIS, TRUE_ANGLE

_EVENTS = ("saccade", "microsaccade")


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    """Activation only: `daemon.run_once()` registers the default paramsets."""
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


@pytest.fixture(scope="module")
def session(daemon_module, prefix, tmp_path_factory):
    """A stepped session detected under the defaults, then under a per-axis
    Engbert-Kliegl paramset registered for it alone. The paramset is removed
    afterwards, with everything computed from it, so no other module's pass
    meets it."""
    from wl_preproc.schema import detect, paramset

    animal = new_animal().session()
    session_key, _segment, _onsets = _build_stepped_session(
        tmp_path_factory, dirname="trueangle", session_id=animal.session_id, subject=animal.subject,
        session_datetime=animal.session_datetime, seed=1301)
    daemon_module.run_once(prefix=prefix)
    per_axis = detect.register_per_axis_paramsets(["engbert_kliegl"])["engbert_kliegl"]
    try:
        detect.EyeDetection.populate({**session_key, "paramset_type": "eye_detection", "paramset_idx": per_axis})
        yield session_key, per_axis
    finally:
        (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": per_axis}).delete(
            part_integrity="cascade")


def _events(session_key, trace, idx):
    from wl_preproc.schema import detect

    return [row for row in (detect.EyeDetection.Run & {**session_key, "trace": trace, "paramset_type": "eye_detection",
                                                      "paramset_idx": idx}).to_dicts(order_by="run_index")
            if row["label"] in _EVENTS]


def test_the_registered_default_is_true_angle_over_a_detectors_own_per_axis():
    """Section 3: written after a detector's own defaults, so a detector
    declaring the field -- its own default per-axis, what a paramset without
    the key means (amendment 3) -- never shadows it."""
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import detect

    declaring = 0
    for name, detector in DETECTORS.items():
        assert detect._eye_detection_params(detector)["geometry"] == TRUE_ANGLE, name
        if hasattr(detector.defaults, "geometry"):
            assert detector.defaults.geometry == PER_AXIS, name
            declaring += 1
    assert declaring


def test_every_default_paramset_detects_in_true_angle(session):
    from wl_preproc.schema import detect, paramset

    for name, idx in detect.register_default_paramsets().items():
        params = (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": idx}).fetch1("params")
        assert params["geometry"] == TRUE_ANGLE, name


def test_a_per_axis_paramset_is_the_default_with_the_other_setting(session):
    """Registered on request, idempotently, for the detectors named alone."""
    from wl_preproc.eye.detect.registry import get_detector
    from wl_preproc.schema import detect, paramset

    _key, per_axis = session
    assert detect.register_per_axis_paramsets(["engbert_kliegl"]) == {"engbert_kliegl": per_axis}
    params = (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": per_axis}).fetch1("params")
    assert params == {**detect._eye_detection_params(get_detector("engbert_kliegl")), "geometry": PER_AXIS}


def test_engbert_kliegl_detects_on_the_local_velocity_and_every_run_is_measured_on_it(session):
    """Sections 4 to 6.1: under the default, Engbert-Kliegl's runs are its
    detector's over `local_velocity`, and each is measured in true angle,
    its peak speed the local velocity's. Not vacuous: some size differs from
    its per-axis measurement."""
    import dataclasses

    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS, detect_engbert_kliegl
    from wl_preproc.eye.detect.measure import measure
    from wl_preproc.eye.detect.velocity import local_velocity, velocity
    from wl_preproc.schema import detect

    session_key, _per_axis = session
    idx = detect.register_default_paramsets()["engbert_kliegl"]
    true = dataclasses.replace(DEFAULT_EK_PARAMS, geometry=TRUE_ANGLE)
    differs = 0
    for trace in ("left", "right"):
        gaze, _v, offered, fs_hz = _stored_inputs(session_key, trace)
        local = local_velocity(gaze, fs_hz)
        rows = _events(session_key, trace, idx)
        assert rows
        assert [(r["run_start"], r["run_stop"], r["label"]) for r in rows] == [
            (run.start, run.stop, run.label.value) for run in detect_engbert_kliegl(gaze, local, offered, fs_hz, true)]
        for row in rows:
            expected = measure(gaze, local, row["run_start"], row["run_stop"], fs_hz, geometry=TRUE_ANGLE)
            assert (row["amplitude_deg"], row["peak_velocity_deg_s"], row["direction_deg"]) == (
                expected.amplitude_deg, expected.peak_velocity_deg_s, expected.direction_deg), (trace, row["run_start"])
            per_axis = measure(gaze, velocity(gaze, fs_hz), row["run_start"], row["run_stop"], fs_hz, geometry=PER_AXIS)
            differs += per_axis.amplitude_deg != expected.amplitude_deg
    assert differs


def test_each_detector_is_handed_the_velocity_its_geometry_asks_for(session, monkeypatch):
    """Section 6.1, amendment 2: under true angle Engbert-Kliegl (and BMD,
    whose gate it is) is handed the local-frame velocity, every other
    detector the per-axis one. Recorded where the stage hands it over,
    because near the centre, where this session's gaze is, the two detect
    alike. Each spied detector runs under a fresh paramset, removed after."""
    import dataclasses
    import functools

    import numpy as np

    from wl_preproc.eye.detect import registry
    from wl_preproc.eye.detect.velocity import local_velocity, velocity
    from wl_preproc.schema import detect, paramset

    session_key, _per_axis = session
    spied = ("engbert_kliegl", "otero_millan")
    handed = {}
    for name in spied:
        original = registry.DETECTORS[name]

        @functools.wraps(original.run)
        def spy(gaze, v, available, fs_hz, params, _run=original.run, _name=name):
            handed.setdefault(_name, []).append((gaze, v, fs_hz))
            return _run(gaze, v, available, fs_hz, params)

        monkeypatch.setitem(registry.DETECTORS, name, dataclasses.replace(original, run=spy))
    fresh = [paramset.register("eye_detection", {**detect._eye_detection_params(registry.DETECTORS[name]),
                                                 "probe": "the velocity handed over"}) for name in spied]
    try:
        for idx in fresh:
            detect.EyeDetection.populate({**session_key, "paramset_type": "eye_detection", "paramset_idx": idx})
        assert sorted(handed) == list(spied)
        for name, calls in handed.items():
            assert len(calls) == 2, name
            for gaze, v, fs_hz in calls:
                want = local_velocity(gaze, fs_hz) if name == "engbert_kliegl" else velocity(gaze, fs_hz)
                assert np.array_equal(v, want), name
    finally:
        for idx in fresh:
            (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": idx}).delete(
                part_integrity="cascade")


def test_a_per_axis_paramset_detects_and_measures_as_v1(session):
    """Section 3: the per-axis setting is today's behaviour exactly."""
    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS, detect_engbert_kliegl
    from wl_preproc.eye.detect.measure import measure
    from wl_preproc.eye.detect.velocity import velocity

    session_key, per_axis = session
    for trace in ("left", "right"):
        gaze, _v, offered, fs_hz = _stored_inputs(session_key, trace)
        v = velocity(gaze, fs_hz)
        rows = _events(session_key, trace, per_axis)
        assert rows
        assert [(r["run_start"], r["run_stop"], r["label"]) for r in rows] == [
            (run.start, run.stop, run.label.value) for run in detect_engbert_kliegl(gaze, v, offered, fs_hz,
                                                                                  DEFAULT_EK_PARAMS)]
        for row in rows:
            expected = measure(gaze, v, row["run_start"], row["run_stop"], fs_hz, geometry=PER_AXIS)
            assert (row["amplitude_deg"], row["peak_velocity_deg_s"]) == (
                expected.amplitude_deg, expected.peak_velocity_deg_s), (trace, row["run_start"])


_POSITIONS = ("start_x_deg", "start_y_deg", "end_x_deg", "end_y_deg", "start_eccentricity_deg", "end_eccentricity_deg")


def test_a_fixation_row_carries_its_positions_and_no_size(session):
    """Decision 5: from its first sample to its last, the positions, their
    eccentricity and polar angle, and its direction under the setting; no
    size and no peak speed."""
    from wl_preproc.eye.detect.measure import measure_positions
    from wl_preproc.schema import detect

    session_key, _per_axis = session
    idx = detect.register_default_paramsets()["engbert_kliegl"]
    measured = 0
    for trace in ("left", "right"):
        gaze, _v, _offered, fs_hz = _stored_inputs(session_key, trace)
        rows = (detect.EyeDetection.Run & {**session_key, "trace": trace, "paramset_type": "eye_detection",
                                          "paramset_idx": idx, "label": "fixation"}).to_dicts()
        for row in rows:
            expected = measure_positions(gaze, row["run_start"], row["run_stop"], fs_hz, geometry=TRUE_ANGLE)
            assert (row["amplitude_deg"], row["peak_velocity_deg_s"]) == (None, None)
            for column in (*_POSITIONS, "start_polar_deg", "end_polar_deg", "direction_deg"):
                assert row[column] == getattr(expected, column), (trace, row["run_start"], column)
            measured += 1
    assert measured


def test_an_event_row_carries_its_endpoints_eccentricity_and_polar_angle(session):
    from wl_preproc.eye.detect.geometry import eccentricity_polar_deg
    from wl_preproc.schema import detect

    session_key, _per_axis = session
    rows = _events(session_key, "left", detect.register_default_paramsets()["engbert_kliegl"])
    for row in rows:
        import numpy as np

        e, polar = eccentricity_polar_deg(np.array([[row["start_x_deg"], row["start_y_deg"]],
                                                    [row["end_x_deg"], row["end_y_deg"]]]))
        assert (row["start_eccentricity_deg"], row["end_eccentricity_deg"]) == (float(e[0]), float(e[1]))
        assert (row["start_polar_deg"], row["end_polar_deg"]) == (float(polar[0]), float(polar[1]))


def test_the_main_sequence_takes_the_rows_it_took_before(session):
    """A fixation row now has positions but still no size, so the fit picks
    the same rows from every row as from the event rows alone."""
    from wl_preproc.eye.detect.main_sequence import DEFAULT_MAIN_SEQUENCE_PARAMS
    from wl_preproc.schema import detect, main_sequence

    session_key, _per_axis = session
    idx = detect.register_default_paramsets()["engbert_kliegl"]
    every = (detect.EyeDetection.Run & {**session_key, "trace": "left", "paramset_type": "eye_detection",
                                       "paramset_idx": idx}).to_dicts(order_by="run_index")
    params = DEFAULT_MAIN_SEQUENCE_PARAMS
    assert main_sequence.selected_runs(every, 500.0, params) == main_sequence.selected_runs(
        [row for row in every if row["label"] in _EVENTS], 500.0, params)


def test_a_database_without_the_new_columns_detects_nothing_and_names_the_statement(session, monkeypatch):
    """As `EyeValidity` refuses without `frac_feature_lost`: DataJoint adds
    no column to an existing table and reads a table's columns once per
    process, so the stage refuses before any job is reserved and says how to
    fix it."""
    from wl_preproc.schema import detect

    assert not detect._lacks_polar_columns()
    monkeypatch.setattr(detect, "_lacks_polar_columns", lambda: True)
    with pytest.raises(RuntimeError) as raised:
        detect.EyeDetection.populate()
    assert detect._polar_statement() in str(raised.value)
    assert "restart" in str(raised.value)


def test_a_part_table_read_without_the_columns_is_refused(session, monkeypatch):
    from types import SimpleNamespace

    from wl_preproc.schema import detect

    names = [name for name in detect.EyeDetection.Run.heading.names if name != "end_polar_deg"]
    monkeypatch.setattr(detect.EyeDetection.Run, "_heading", SimpleNamespace(names=names))
    assert detect._lacks_polar_columns()
    with pytest.raises(RuntimeError, match="has no columns"):
        detect.EyeDetection.populate()


def test_the_statement_restores_the_columns_as_datajoint_declares_them(session):
    """Dropped and restored by the named statement on a copy of the part
    table: type, nullability, default, comment and place match what
    declaring it wrote, and no row of the table itself is touched."""
    import datajoint as dj

    from wl_preproc.schema import detect

    table = detect.EyeDetection.Run.full_table_name
    copy = table[:-1] + '_restore_check`'
    statement = detect._polar_statement()
    assert statement.count(table) == 1

    def columns(name):
        return [row[:2] + row[3:6] + row[8:9] for row in dj.conn().query(f"SHOW FULL COLUMNS FROM {name}").fetchall()]

    dj.conn().query(f"CREATE TABLE {copy} LIKE {table}")
    try:
        dj.conn().query(f"ALTER TABLE {copy} " + ", ".join(
            f"DROP COLUMN {column}" for column in detect._POLAR_COLUMNS))
        dj.conn().query(statement.replace(table, copy))
        restored = columns(copy)
    finally:
        dj.conn().query(f"DROP TABLE IF EXISTS {copy}")
    assert restored == columns(table)
