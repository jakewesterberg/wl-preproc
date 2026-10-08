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
        detections=detect.register_default_paramsets(),
        validity=paramset.register("eye_validity", asdict(DEFAULT_VALIDITY_PARAMS)),
        fit=main_sequence.register_default_paramsets()["default"],
    )


def _detect(schema, session, trace, *, detector="engbert_kliegl", status="computed", saccades=(), master=True,
            own_fit_refused=False):
    """One detection of `trace` (Engbert-Kliegl's unless `detector` says),
    its runs and, with `master`, its `SaccadeMainSequence` row: computed on
    `CURVE` over 1-12 deg, or with `own_fit_refused` refused for too few
    saccades. Each of `saccades`, an `(amplitude, peak)` pair, is a 20-sample
    (40 ms at 500 Hz) run every 100 samples, with `fixation` between, so the
    runs tile the trace as a real detection's do: two detectors of one
    session are a pair `DetectorAgreement` will score in a later daemon
    pass, and an untiled trace makes that pass fail."""
    from wl_preproc.schema import detect, main_sequence

    row = _detection_row(session.subject, session.session_datetime, trace, schema.validity, schema.detections[detector],
                         status=status, n_samples=100 * (len(saccades) + 1), reason="planted refusal"
                         if status == "refused" else "")
    detect.EyeDetection.insert1(row, allow_direct_insert=True)
    key = {name: row[name] for name in detect.EyeDetection.primary_key}
    runs = []
    for index, (amplitude, peak) in enumerate(saccades):
        runs.append({"run_start": 100 * index, "run_stop": 100 * index + 20, "label": "saccade",
                     "amplitude_deg": amplitude, "peak_velocity_deg_s": peak})
        runs.append({"run_start": 100 * index + 20, "run_stop": 100 * index + 100, "label": "fixation",
                     "amplitude_deg": None, "peak_velocity_deg_s": None})
    runs.append({"run_start": 100 * len(saccades), "run_stop": 100 * len(saccades) + 100, "label": "fixation",
                 "amplitude_deg": None, "peak_velocity_deg_s": None})
    detect.EyeDetection.Run.insert({**key, "run_index": index, **run} for index, run in enumerate(runs))
    if master and status == "computed" and own_fit_refused:
        main_sequence.SaccadeMainSequence.insert1(
            {**key, "fit_paramset_type": "main_sequence", "fit_paramset_idx": schema.fit, "fit_status": "refused",
             "fs_hz": 500.0, "n_saccades": len(saccades),
             "reason": f"{len(saccades)} saccades; a session fit needs at least 100"}, allow_direct_insert=True)
    elif master and status == "computed":
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


def _animal_with_history(schema, n_earlier: int, detectors=("engbert_kliegl",)):
    """An animal with `n_earlier` sessions ingested long ago, each fitted on
    `CURVE` for both eyes by each of `detectors`, and a session ingested now.
    Returns that session, landed but not yet detected."""
    animal = new_animal()
    for _ in range(n_earlier):
        earlier = animal.session()
        _land_session(earlier.subject, earlier.session_datetime, ingested_at=_LONG_AGO)
        for trace in ("left", "right"):
            for detector in detectors:
                _detect(schema, earlier, trace, detector=detector)
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


def test_a_line_carries_every_detectors_figure_in_paramset_order(vigor_schema, tmp_path, prefix):
    """The main-sequence review's minor: until now no test had two
    detectors on one line. BMD is registered after Engbert-Kliegl and sorts
    before it by name, so the order shown is the paramsets' and not the
    names'."""
    session = _animal_with_history(vigor_schema, 3, detectors=("engbert_kliegl", "bmd"))
    _detect(vigor_schema, session, "left", saccades=_saccades(40, 0.9))
    _detect(vigor_schema, session, "left", detector="bmd", saccades=_saccades(40, 1.1))
    figures = {"engbert_kliegl": "90% (3 earlier)", "bmd": "110% (3 earlier)"}
    in_order = sorted(figures, key=vigor_schema.detections.get)
    assert in_order != sorted(figures)
    assert _line(tmp_path, prefix, session, "left").endswith(
        ": " + ", ".join(f"`{name}` {figures[name]}" for name in in_order))


def test_vigor_is_shown_where_the_sessions_own_fit_was_refused(vigor_schema, tmp_path, prefix):
    """Vigor needs only the earlier sessions' fits (spec section 5): 40
    saccades are too few for this session's own fit, and enough for its
    vigor. The main-sequence review's minor."""
    session = _animal_with_history(vigor_schema, 3)
    _detect(vigor_schema, session, "left", saccades=_saccades(40, 0.9), own_fit_refused=True)
    assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` 90% (3 earlier)")
