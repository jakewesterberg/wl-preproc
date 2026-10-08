"""The report's `### Saccade vigor per session per eye (24 h)` subsection
(main-sequence design spec `2026-10-07-main-sequence-design.md` section 5).

Rows are planted directly, as `test_detect_report.py` plants its own;
`tests/schema/test_main_sequence_populate.py` drives the table end to end.
Each test plants its own animal, so no line is another test's. Earlier
sessions are ingested long ago, outside the window, so only the session under
test gets a line of its own.
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from tests.cli.test_detect_report import _detection_row, _land_session, _line_for, _section, _subsection
from tests.identities import new_animal
from wl_preproc.cli.report import build_report
from wl_preproc.eye.detect.main_sequence import Curve

CURVE = Curve(v_max_deg_s=400.0, saturation_deg=5.0)
_LONG_AGO = datetime.datetime(2025, 1, 1)
_HEADING = "Saccade vigor per session per eye (24 h)"


@pytest.fixture(scope="module")
def vigor_schema(dj_conn, prefix):
    from dataclasses import asdict

    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS
    from wl_preproc.schema import detect, ingest, main_sequence, paramset, timebase

    main_sequence.activate(prefix=prefix)
    ingest.activate(prefix=prefix)
    timebase.activate(prefix=prefix)
    return SimpleNamespace(
        detection=detect.register_default_paramsets()["engbert_kliegl"],
        validity=paramset.register("eye_validity", asdict(DEFAULT_VALIDITY_PARAMS)),
        fit=main_sequence.register_default_paramsets()["default"],
    )


def _detect(schema, session, trace, *, status="computed", saccades=(), master=True):
    """One Engbert-Kliegl detection of `trace`, its saccade runs
    (`(amplitude, peak)` pairs, 40 ms each at 500 Hz) and, with `master`, its
    `SaccadeMainSequence` row: computed on `CURVE` over 1-12 deg."""
    from wl_preproc.schema import detect, main_sequence

    row = _detection_row(session.subject, session.session_datetime, trace, schema.validity, schema.detection,
                         status=status, n_samples=100 * (len(saccades) + 1), reason="planted refusal"
                         if status == "refused" else "")
    detect.EyeDetection.insert1(row, allow_direct_insert=True)
    key = {name: row[name] for name in detect.EyeDetection.primary_key}
    detect.EyeDetection.Run.insert(
        {**key, "run_index": index, "run_start": 100 * index, "run_stop": 100 * index + 20, "label": "saccade",
         "amplitude_deg": amplitude, "peak_velocity_deg_s": peak}
        for index, (amplitude, peak) in enumerate(saccades))
    if master and status == "computed":
        main_sequence.SaccadeMainSequence.insert1(
            {**key, "fit_paramset_type": "main_sequence", "fit_paramset_idx": schema.fit, "fit_status": "computed",
             "fs_hz": 500.0, "n_saccades": len(saccades), "amplitude_min_deg": 1.0, "amplitude_max_deg": 12.0,
             "v_max_deg_s": CURVE.v_max_deg_s, "saturation_deg": CURVE.saturation_deg, "v_max_se_deg_s": 1.0,
             "saturation_se_deg": 0.1, "r_squared": 0.9, "reason": ""}, allow_direct_insert=True)
    elif master:
        main_sequence.SaccadeMainSequence.insert1(
            {**key, "fit_paramset_type": "main_sequence", "fit_paramset_idx": schema.fit, "fit_status": "refused",
             "n_saccades": 0, "reason": "detection refused: planted refusal"}, allow_direct_insert=True)
    return key


def _animal_with_history(schema, n_earlier: int):
    """An animal with `n_earlier` sessions ingested long ago, each fitted on
    `CURVE` for both eyes, and a session ingested now. Returns the animal and
    that session, landed but not yet detected."""
    animal = new_animal()
    for _ in range(n_earlier):
        earlier = animal.session()
        _land_session(earlier.subject, earlier.session_datetime, ingested_at=_LONG_AGO)
        for trace in ("left", "right"):
            _detect(schema, earlier, trace)
    session = animal.session()
    _land_session(session.subject, session.session_datetime)
    return session


def _saccades(n: int, scale: float):
    amplitude = np.linspace(2.0, 10.0, n)
    return list(zip(amplitude.tolist(), (scale * CURVE(amplitude)).tolist(), strict=True))


def _line(root, prefix, session, trace) -> str:
    subsection = _subsection(_section(build_report(root, prefix=prefix), "Detection"), _HEADING)
    return _line_for(subsection, f"`{session.subject}` @ {session.session_datetime:%Y-%m-%d %H:%M} — {trace} ")


def test_a_session_gets_each_detectors_vigor_against_its_earlier_sessions(vigor_schema, tmp_path, prefix):
    session = _animal_with_history(vigor_schema, 3)
    _detect(vigor_schema, session, "left", saccades=_saccades(40, 0.9))
    line = _line(tmp_path, prefix, session, "left")
    assert line.endswith(f"(validity paramset {vigor_schema.validity}, fit paramset {vigor_schema.fit}): "
                         "`engbert_kliegl` 90% (3 earlier)")


def test_fewer_than_three_earlier_sessions_is_no_history_yet(vigor_schema, tmp_path, prefix):
    session = _animal_with_history(vigor_schema, 2)
    _detect(vigor_schema, session, "left", saccades=_saccades(40, 0.9))
    assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` no history yet (2 earlier)")


def test_too_few_saccades_say_how_many(vigor_schema, tmp_path, prefix):
    session = _animal_with_history(vigor_schema, 3)
    _detect(vigor_schema, session, "left", saccades=_saccades(10, 0.9))
    assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` too few saccades (10)")


def test_a_refused_detection_says_so(vigor_schema, tmp_path, prefix):
    session = _animal_with_history(vigor_schema, 3)
    _detect(vigor_schema, session, "right", status="refused")
    assert _line(tmp_path, prefix, session, "right").endswith("`engbert_kliegl` detection refused")


def test_a_detection_without_its_row_is_not_computed_yet(vigor_schema, tmp_path, prefix):
    from wl_preproc.schema import detect

    session = _animal_with_history(vigor_schema, 3)
    key = _detect(vigor_schema, session, "left", saccades=_saccades(40, 0.9), master=False)
    try:
        assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` not computed yet")
    finally:
        # A later `daemon.run_once()` in this suite would try to fit it.
        (detect.EyeDetection & key).delete()


def test_the_both_eyes_trace_is_left_out(vigor_schema, tmp_path, prefix):
    session = _animal_with_history(vigor_schema, 3)
    _detect(vigor_schema, session, "conjunction", saccades=_saccades(40, 0.9))
    subsection = _subsection(_section(build_report(tmp_path, prefix=prefix), "Detection"), _HEADING)
    assert f"`{session.subject}` @" not in subsection
