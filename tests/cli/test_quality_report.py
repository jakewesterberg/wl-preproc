"""The report's `### Seven-way agreement per session per eye (24 h)`
subsection (design spec `2026-10-08-seven-way-agreement-design.md` section 5).

Rows are planted directly, as `test_vigor_report.py` plants its own;
`tests/schema/test_detection_quality_populate.py` drives the table end to end.
Each test plants its own animal, so no line is another test's. Earlier
sessions are ingested long ago, outside the window, so only the session under
test gets a line of its own.
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from tests.cli.test_detect_report import _detection_row, _land_session, _line_for, _section, _subsection
from tests.identities import new_animal
from wl_preproc.cli.report import build_report

_LONG_AGO = datetime.datetime(2025, 1, 1)
_HEADING = "Seven-way agreement per session per eye (24 h)"
_VOCABULARY = "saccade,fixation"


@pytest.fixture(scope="module")
def quality_schema(dj_conn, prefix):
    from dataclasses import asdict

    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS
    from wl_preproc.schema import consensus, detect, ingest, main_sequence, paramset, timebase

    consensus.activate(prefix=prefix)
    main_sequence.activate(prefix=prefix)
    ingest.activate(prefix=prefix)
    timebase.activate(prefix=prefix)
    detections = detect.register_default_paramsets()
    return SimpleNamespace(
        detection=detections["engbert_kliegl"],
        detectors=",".join(str(idx) for idx in sorted(detections.values())),
        validity=paramset.register("eye_validity", asdict(DEFAULT_VALIDITY_PARAMS)),
        fit=main_sequence.register_default_paramsets()["default"],
    )


def _detect(schema, session, trace, *, status="computed"):
    """Engbert-Kliegl's detection of `trace`: one `fixation` run over 100
    samples, so it tiles, and its `SaccadeMainSequence` row, so no later
    daemon pass in the suite tries to fit it. One detector is no pair, and
    not every registered one, so `DetectorAgreement` and `DetectionQuality`
    never take it up."""
    from wl_preproc.schema import detect, main_sequence

    row = _detection_row(session.subject, session.session_datetime, trace, schema.validity, schema.detection,
                         status=status, n_samples=100 if status == "computed" else None,
                         reason="planted refusal" if status == "refused" else "")
    detect.EyeDetection.insert1(row, allow_direct_insert=True)
    key = {name: row[name] for name in detect.EyeDetection.primary_key}
    if status == "computed":
        detect.EyeDetection.Run.insert1({**key, "run_index": 0, "run_start": 0, "run_stop": 100, "label": "fixation",
                                         "amplitude_deg": None, "peak_velocity_deg_s": None})
    main_sequence.SaccadeMainSequence.insert1(
        {**key, "fit_paramset_type": "main_sequence", "fit_paramset_idx": schema.fit, "fit_status": "refused",
         "n_saccades": 0, "reason": "planted"}, allow_direct_insert=True)


def _quality(schema, session, trace, as_saccade, as_fixation, *, metric="krippendorff_alpha", vocabulary=_VOCABULARY,
             detectors=None, validity=None):
    """`DetectionQuality`'s two rows for `trace`, one per glissade
    convention; a value of None is an undefined score."""
    from wl_preproc.schema import consensus

    consensus.DetectionQuality.insert(
        ({**session.key, "trace": trace, "validity_paramset_type": "eye_validity",
          "validity_paramset_idx": schema.validity if validity is None else validity, "metric": metric,
          "vocabulary": vocabulary,
          "pso_as": pso_as, "value": value, "n_samples_compared": 100,
          "detectors": detectors or schema.detectors}
         for pso_as, value in (("saccade", as_saccade), ("fixation", as_fixation))),
        allow_direct_insert=True)


def _animal_with_history(schema, history):
    """An animal with one session ingested long ago per `(as_saccade,
    as_fixation)` pair in `history`, each with both eyes' rows, and a session
    ingested now. Returns that session, landed but not yet detected."""
    animal = new_animal()
    for as_saccade, as_fixation in history:
        earlier = animal.session()
        _land_session(earlier.subject, earlier.session_datetime, ingested_at=_LONG_AGO)
        for trace in ("left", "right"):
            _quality(schema, earlier, trace, as_saccade, as_fixation)
    session = animal.session()
    _land_session(session.subject, session.session_datetime)
    return animal, session


_HISTORY = [(0.70, 0.66), (0.68, 0.67), (0.60, 0.65)]


def _line(root, prefix, session, trace) -> str:
    subsection = _subsection(_section(build_report(root, prefix=prefix), "Detection"), _HEADING)
    return _line_for(subsection, f"`{session.subject}` @ {session.session_datetime:%Y-%m-%d %H:%M} — {trace} ")


def test_a_session_gets_its_agreement_against_its_earlier_sessions(quality_schema, tmp_path, prefix):
    _animal, session = _animal_with_history(quality_schema, _HISTORY)
    _detect(quality_schema, session, "left")
    _quality(quality_schema, session, "left", 0.63, 0.62)
    assert _line(tmp_path, prefix, session, "left").endswith(
        f"— left (validity paramset {quality_schema.validity}): glissades as saccade 0.63 (usual 0.68, 3 earlier); "
        "as fixation 0.62 (usual 0.66, 3 earlier)")


def test_fewer_than_three_earlier_sessions_is_no_history_yet(quality_schema, tmp_path, prefix):
    _animal, session = _animal_with_history(quality_schema, _HISTORY[:2])
    _detect(quality_schema, session, "left")
    _quality(quality_schema, session, "left", 0.63, 0.62)
    assert _line(tmp_path, prefix, session, "left").endswith(
        "glissades as saccade 0.63 (no history yet, 2 earlier); as fixation 0.62 (no history yet, 2 earlier)")


def test_an_undefined_score_says_so(quality_schema, tmp_path, prefix):
    _animal, session = _animal_with_history(quality_schema, _HISTORY)
    _detect(quality_schema, session, "left")
    _quality(quality_schema, session, "left", None, 0.62)
    assert _line(tmp_path, prefix, session, "left").endswith(
        "glissades as saccade undefined (usual 0.68, 3 earlier); as fixation 0.62 (usual 0.66, 3 earlier)")


def test_a_refused_detection_says_so(quality_schema, tmp_path, prefix):
    _animal, session = _animal_with_history(quality_schema, _HISTORY)
    _detect(quality_schema, session, "right", status="refused")
    assert _line(tmp_path, prefix, session, "right").endswith(
        f"— right (validity paramset {quality_schema.validity}): detection refused")


def test_a_detection_without_its_rows_is_not_computed_yet(quality_schema, tmp_path, prefix):
    _animal, session = _animal_with_history(quality_schema, _HISTORY)
    _detect(quality_schema, session, "left")
    assert _line(tmp_path, prefix, session, "left").endswith(
        f"— left (validity paramset {quality_schema.validity}): not computed yet")


def test_the_both_eyes_trace_is_left_out(quality_schema, tmp_path, prefix):
    _animal, session = _animal_with_history(quality_schema, _HISTORY)
    _detect(quality_schema, session, "conjunction")
    _quality(quality_schema, session, "conjunction", 0.63, 0.62)
    subsection = _subsection(_section(build_report(tmp_path, prefix=prefix), "Detection"), _HEADING)
    assert f"`{session.subject}` @" not in subsection


def test_the_history_is_earlier_sessions_scored_alike(quality_schema, tmp_path, prefix):
    """The median reads only the same animal's earlier sessions with the same
    trace, validity paramset, metric, vocabulary and detectors, and a
    defined score. Each session that must not count scores 0.10, so any one
    let in moves the count. The second validity paramset is deleted
    afterwards, with its row, so no later daemon pass computes a mask for
    it."""
    from dataclasses import asdict

    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS
    from wl_preproc.schema import paramset

    animal = new_animal()

    def past(trace, as_saccade, as_fixation, **unlike):
        earlier = animal.session()
        _land_session(earlier.subject, earlier.session_datetime, ingested_at=_LONG_AGO)
        _quality(quality_schema, earlier, trace, as_saccade, as_fixation, **unlike)

    other_mask = paramset.register("eye_validity", {**asdict(DEFAULT_VALIDITY_PARAMS), "max_glitch_ms": 99.0})
    try:
        for as_saccade, as_fixation in _HISTORY:
            past("left", as_saccade, as_fixation)
        past("right", 0.10, 0.10)
        past("left", 0.10, 0.10, metric="fleiss_kappa")
        past("left", 0.10, 0.10, vocabulary="saccade,microsaccade,fixation")
        past("left", 0.10, 0.10, detectors="0,1")
        past("left", 0.10, 0.10, validity=other_mask)
        past("left", None, None)
        session = animal.session()
        _land_session(session.subject, session.session_datetime)
        _detect(quality_schema, session, "left")
        _quality(quality_schema, session, "left", 0.63, 0.62)
        past("left", 0.10, 0.10)
        assert _line(tmp_path, prefix, session, "left").endswith(
            "glissades as saccade 0.63 (usual 0.68, 3 earlier); as fixation 0.62 (usual 0.66, 3 earlier)")
    finally:
        (paramset.ParamSet & {"paramset_type": "eye_validity", "paramset_idx": other_mask}).delete()
