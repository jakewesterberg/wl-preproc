# tests/schema/test_eye_populate.py
"""EyeCalibration/EyeQuality actually populate: the fallback chain run for
real, over landed synthetic sessions, through `daemon.run_once()`.

Controller ruling B names the trap this file exists to close: Task 9 shipped
both tables with a DELIBERATELY EMPTY `key_source` so registering them as
daemon stages could not break `run_once()` before `make()` existed. Nothing
enforces that a later task actually replaces it -- a test that only calls
`make()` directly would pass with the empty `key_source` still in place,
proving nothing about whether these tables compute anything in production.
Every test below therefore goes through the real `EyeCalibration.key_source`/
`EyeQuality.key_source` properties and a real `daemon.run_once()` pass, never
`make()` called by hand.

No `TARGET_POSITION`/`FIXATION_ACQUIRED`/`FIXATION_END` codes exist anywhere
in `synth/timeline.py` yet -- grepped for it: nothing in `wl_preproc/synth/`
or any earlier test in this plan emits them. This file is the first to, by
taking a normal synthetic session's own `GroundTruth.code_words` and adding
its own, placed well inside each trial's otherwise-empty middle (clear of
`synth/timeline.py::_emit`'s own TRIAL_START/TRIAL_NUMBER cluster at the
start and TRIAL_CORRECT/TRIAL_END cluster at the end), then rewriting the
sync box log from the merged, re-sorted result.
"""

from __future__ import annotations

import dataclasses
import datetime

import numpy as np

import pytest

from wl_preproc.eye.calibration import CalibrationModel
from wl_preproc.contracts.events import (
    Escape,
    TargetRole,
    TaskEvent,
    TaskTypeCode,
    encode_dva,
    encode_payload,
)
from wl_preproc.synth.recipe import BlockSpec, MontageSpec, SessionRecipe
from wl_preproc.synth.session import generate_session
from wl_preproc.synth.syncbox import write_syncbox_log
from wl_preproc.synth.timeline import CODE_WORD_SPACING_S

TRIAL_DURATION_S = 3.0
N_TRIALS = 4


def _recipe(session_id: str, subject: str, seed: int, *, include_ohdpi: bool) -> SessionRecipe:
    """A minimal session: the sync box alone suffices for event assembly
    (`schema/events.py`'s own "the canonical trial list is built from the
    sync box... alone"), and `ohdpi` is the one other system this file's
    scenarios ever need -- included or not, per scenario."""
    systems = ("syncbox", "ohdpi") if include_ohdpi else ("syncbox",)
    return SessionRecipe(
        session_id=session_id,
        subject=subject,
        rig="rig-a",
        systems=systems,
        blocks=(
            BlockSpec(
                task_type=TaskTypeCode.RF_MAP, n_trials=N_TRIALS, trial_duration_s=TRIAL_DURATION_S
            ),
        ),
        montages=(MontageSpec(start_s=0.0, end_s=N_TRIALS * TRIAL_DURATION_S),),
        n_ap_channels=4,
        ap_sample_rate_hz=30_000.0,
        seed=seed,
    )


def _fixation_words(window_start_s: float, target_deg: tuple[float, float]) -> list[tuple[float, int]]:
    """The five code words for one calibration window: `TARGET_POSITION`
    (role=`FIXATION_POINT`) starting at `window_start_s`, `FIXATION_ACQUIRED`
    0.2 s later, `FIXATION_END` 0.8 s after THAT -- entirely independent of
    trial boundaries, so a window can be placed anywhere, including well
    past the session's own recorded extent (used by the coverage-gap tests
    below to build a window that genuinely has no ohDPI behind it).
    """
    x_deg, y_deg = target_deg
    target_words = encode_payload(
        Escape.TARGET_POSITION,
        [int(TargetRole.FIXATION_POINT), encode_dva(x_deg), encode_dva(y_deg)],
    )
    extra = [
        (window_start_s + offset * CODE_WORD_SPACING_S, word)
        for offset, word in enumerate(target_words)
    ]
    extra.append((window_start_s + 0.2, int(TaskEvent.FIXATION_ACQUIRED)))
    extra.append((window_start_s + 0.8, int(TaskEvent.FIXATION_END)))
    return extra


def _write_fixations(
    session_dir, recipe, truth, windows: list[tuple[float, tuple[float, float]]]
) -> None:
    """Rewrite the sync box log with `truth.code_words` plus one calibration
    window per `(window_start_s, target_deg)` pair in `windows`.

    Every window's five words are individually spaced (see
    `_fixation_words`), and this file's fixtures always place windows at
    least a second apart -- comfortably clear of `synth/timeline.py::
    _emit`'s own two word clusters per trial (TRIAL_START/TRIAL_NUMBER
    within ~5 ms of `trial_start`, TRIAL_CORRECT/TRIAL_END within ~2 ms of
    `trial_start + TRIAL_DURATION_S`) -- so the merged, re-sorted stream can
    never interleave one payload's words with another's on the shared
    strobed bus (`_emit`'s own docstring names exactly this hazard).
    """
    extra: list[tuple[float, int]] = []
    for window_start_s, target_deg in windows:
        extra.extend(_fixation_words(window_start_s, target_deg))

    merged = tuple(sorted((*truth.code_words, *extra), key=lambda pair: pair[0]))
    new_truth = dataclasses.replace(truth, code_words=merged)
    write_syncbox_log(session_dir / "syncbox" / "syncbox.log", recipe, new_truth, drift_ppm=0.0)


def _inject_fixations(session_dir, recipe, truth, targets_deg: list[tuple[float, float]]) -> None:
    """One calibration window per trial, at `trial_start + 1.0` (so the
    fixation itself sits at `[trial_start + 1.2, trial_start + 1.8]`),
    one entry of `targets_deg` per trial. A thin, trial-shaped wrapper
    around `_write_fixations` -- kept because most of this file's fixtures
    want exactly this shape and naming the trial index reads better at the
    call site than a bare list of absolute times.
    """
    assert len(targets_deg) == N_TRIALS
    windows = [
        (trial_index * TRIAL_DURATION_S + 1.0, target)
        for trial_index, target in enumerate(targets_deg)
    ]
    _write_fixations(session_dir, recipe, truth, windows)


def _row_for_time(segment: dict, session_s: float) -> int:
    """Ground truth for `eye._session_time_to_row`, computed here by an
    INDEPENDENT re-derivation of the same linear map -- not by calling that
    function -- so a broken production implementation cannot also corrupt
    the expected value a test compares it against. `session_s = start_s +
    (row / (n_samples - 1)) * (end_s - start_s)`, inverted."""
    n_samples = segment["n_samples"]
    span = segment["end_s"] - segment["start_s"]
    frac = (session_s - segment["start_s"]) / span
    row = round(frac * (n_samples - 1))
    return min(max(row, 0), n_samples - 1)


def _expected_raw_points(
    session_dir, segment: dict, file_eye: str, windows_session_time: list[tuple[float, float]]
) -> list:
    """The exact raw Purkinje point `EyeCalibration.make()` should read for
    each `(t_start, t_end)` window, computed by reading the real ohDPI file
    directly and averaging over the SAME row range `_row_for_time` (this
    file's own independent re-derivation, not `eye._session_time_to_row`)
    resolves each bound to."""
    from wl_preproc.eye.gaze import purkinje_vector

    trace = purkinje_vector(session_dir / "ohdpi" / segment["file_path"], file_eye)
    points = []
    for t_start, t_end in windows_session_time:
        row_start = _row_for_time(segment, t_start)
        row_end = _row_for_time(segment, t_end)
        lo, hi = sorted((row_start, row_end))
        points.append(trace[lo : hi + 1].mean(axis=0))
    return points


def _land(root, recipe, session_datetime, *, acquisition_systems: tuple[str, ...]) -> dict:
    """Hand-insert exactly what `EyeCalibration.key_source`/
    `EyeQuality.key_source` read: `Ingestion` for `session_dir`, and
    `AcquisitionSystem` for which systems were present -- following
    `tests/schema/test_timebase.py`'s own `provenance_session` fixture
    pattern rather than running the real ingest watcher.

    `acquisition_systems` is asserted INDEPENDENTLY of `recipe.systems`,
    deliberately: `_land`'s caller can assert `ohdpi` present here even when
    the recipe never wrote a real file for it, which is exactly how
    `test_no_ohdpi_file...` below builds "AcquisitionSystem says present,
    nothing usable is actually there" -- a real gap between the coarse,
    table-level presence check and a genuinely readable recording (see
    `EyeCalibration.key_source`'s own docstring).
    """
    from wl_preproc.schema import core, ingest, pipeline

    session_dir = root / recipe.session_id
    session_key = {"subject": recipe.subject, "session_datetime": session_datetime}

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": recipe.subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    ingest.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": session_datetime + datetime.timedelta(hours=1),
            "session_dir": str(session_dir),
            "integrity": "verified",
            "topology": {system: "present" for system in acquisition_systems},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    core.AcquisitionSystem.insert(
        [{**session_key, "system": system} for system in acquisition_systems],
        skip_duplicates=True,
    )
    return session_key


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


@pytest.fixture(scope="module")
def fitted_session(daemon_module, prefix, tmp_path_factory):
    """A well-conditioned session: four trials, four well-spread targets
    (not collinear, not coincident), a real ohDPI recording. Every point
    the fit needs, with nothing else standing between it and `fitted`."""
    root = tmp_path_factory.mktemp("eyefitted")
    recipe = _recipe("2027-04-01_01", "eyefit01", seed=401, include_ohdpi=True)
    truth = generate_session(root, recipe)
    targets = [(0.0, 0.0), (8.0, 8.0), (-8.0, 8.0), (8.0, -8.0)]
    _inject_fixations(root / recipe.session_id, recipe, truth, targets)
    session_key = _land(
        root, recipe, datetime.datetime(2027, 4, 1, 9, 0),
        acquisition_systems=("syncbox", "ohdpi"),
    )
    report = daemon_module.run_once(prefix=prefix)
    return session_key, report


@pytest.fixture(scope="module")
def degenerate_session(daemon_module, prefix, tmp_path_factory):
    """Only a central target: every trial's fixation targets the same
    (0, 0), so the constellation's spread is zero and section 3.5's
    conditioning guard refuses the fit outright. No `.bhv2` and no earlier
    same-day session exist for this subject, so the chain has nothing to
    fall back to either -- REFUSED is the only reachable outcome."""
    root = tmp_path_factory.mktemp("eyedegenerate")
    recipe = _recipe("2027-04-02_01", "eyedegn1", seed=402, include_ohdpi=True)
    truth = generate_session(root, recipe)
    targets = [(0.0, 0.0)] * N_TRIALS
    _inject_fixations(root / recipe.session_id, recipe, truth, targets)
    session_key = _land(
        root, recipe, datetime.datetime(2027, 4, 2, 9, 0),
        acquisition_systems=("syncbox", "ohdpi"),
    )
    report = daemon_module.run_once(prefix=prefix)
    return session_key, report


@pytest.fixture(scope="module")
def no_ohdpi_file_session(daemon_module, prefix, tmp_path_factory):
    """`AcquisitionSystem` says `ohdpi` is present; nothing usable is
    actually on disk -- the recipe never writes an `ohdpi/` directory at
    all, but `_land` still asserts the system present, exactly the gap
    `EyeCalibration.key_source`'s own docstring names between the coarse
    table check and a genuinely readable recording. Otherwise identical to
    `fitted_session`'s own well-conditioned targets, so if `make()` ever
    stopped checking for a usable `core.Segment` FIRST, this would silently
    fall through to `resolve_calibration`'s OWN "no fixation epoch" refusal
    instead -- a different, wrong reason -- rather than crashing loudly.
    """
    root = tmp_path_factory.mktemp("eyenofile")
    recipe = _recipe("2027-04-03_01", "eyenofil", seed=403, include_ohdpi=False)
    truth = generate_session(root, recipe)
    targets = [(0.0, 0.0), (8.0, 8.0), (-8.0, 8.0), (8.0, -8.0)]
    _inject_fixations(root / recipe.session_id, recipe, truth, targets)
    session_key = _land(
        root, recipe, datetime.datetime(2027, 4, 3, 9, 0),
        acquisition_systems=("syncbox", "ohdpi"),
    )
    report = daemon_module.run_once(prefix=prefix)
    return session_key, report


@pytest.fixture(scope="module")
def no_ohdpi_acquisition_system_session(daemon_module, prefix, tmp_path_factory):
    """A genuinely, honestly `ohdpi`-less session: the recipe never runs
    that system, and `_land` asserts only `("syncbox",)` present -- matching
    reality this time, unlike `no_ohdpi_file_session` (which deliberately
    asserts `ohdpi` present over nothing real, to test the OTHER gap).
    `EyeCalibration.key_source`'s `core.AcquisitionSystem` restriction is
    what has to exclude this session; nothing else does, since its events
    assemble perfectly normally from the syncbox alone."""
    root = tmp_path_factory.mktemp("eyenoacq")
    recipe = _recipe("2027-04-07_01", "eyenoacq", seed=409, include_ohdpi=False)
    generate_session(root, recipe)
    session_key = _land(
        root, recipe, datetime.datetime(2027, 4, 7, 9, 0),
        acquisition_systems=("syncbox",),
    )
    report = daemon_module.run_once(prefix=prefix)
    return session_key, report


@pytest.fixture(scope="module")
def events_not_assembled_session(daemon_module, prefix, tmp_path_factory):
    """`Ingestion.session_dir` points at a bare, empty directory -- no
    `syncbox/` under it at all, so `events.decode_syncbox_in_session_time`
    raises `FileNotFoundError` and `_populate_event_stage` catches it into
    `report["errors"]` without ever writing `pipeline.event.
    BehaviorRecording`. No `generate_session` call at all: this fixture
    exists to prove the "assembled events" restriction alone, and a real
    session would satisfy it, defeating the point. An `ohdpi`
    `AcquisitionSystem` row is still asserted, so the OTHER restriction is
    isolated as satisfied and cannot be what excludes this session.
    """
    root = tmp_path_factory.mktemp("eyenoevents")
    session_dir = root / "2027-04-07_02"
    session_dir.mkdir()
    session_key = {"subject": "eyenoevt", "session_datetime": datetime.datetime(2027, 4, 7, 14, 0)}

    from wl_preproc.schema import core, ingest, pipeline

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": session_key["subject"],
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    ingest.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": session_key["session_datetime"] + datetime.timedelta(hours=1),
            "session_dir": str(session_dir),
            "integrity": "verified",
            "topology": {"ohdpi": "present"},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    core.AcquisitionSystem.insert1({**session_key, "system": "ohdpi"}, skip_duplicates=True)

    report = daemon_module.run_once(prefix=prefix)

    # Clean up immediately, rather than leaving this session in the shared,
    # session-scoped database (`tests/conftest.py`'s `dj_conn`/`prefix`):
    # `populate_session` fails for it PERMANENTLY (no real syncbox log ever
    # arrives), so `_populate_event_stage` retries -- and re-reports the
    # identical error -- on every LATER `daemon.run_once()` call any other
    # test file in this suite makes, for the rest of the process. Found by
    # running the full suite, not assumed: it broke `tests/schema/
    # test_eye_schema.py::
    # test_registering_them_does_not_break_a_clean_daemon_pass`'s own
    # `report["errors"] == []` assertion, since that test's `daemon.
    # run_once()` call inherits whatever this fixture left behind. `session_key`
    # is a plain dict already captured above, so deleting the live rows here
    # does not affect anything this fixture or its own tests still read.
    # Safemode is off for this whole suite (`tests/conftest.py`'s `dj_conn`),
    # so this cascades through `Ingestion`/`AcquisitionSystem` with no prompt.
    (pipeline.Session & session_key).delete()

    return session_key, report


def test_a_session_with_no_ohdpi_acquisition_system_row_is_excluded(
    no_ohdpi_acquisition_system_session,
):
    """Reviewer mutation 1 of 2: replacing `EyeCalibration.key_source` with
    bare `pipeline.Session & ingest.Ingestion` (dropping BOTH of design spec
    section 6's restrictions) left every existing fixture green, because
    every one of them asserts an `ohdpi` `AcquisitionSystem` row regardless
    of scenario. This is the first test in this file for which that
    mutation is observable: it asserts EXCLUSION, and every fixture built
    before this fix round asserted only inclusion."""
    from wl_preproc.schema import eye

    session_key, report = no_ohdpi_acquisition_system_session
    assert _no_eye_stage_errors(report)
    assert len(eye.EyeCalibration.key_source & session_key) == 0
    assert len((eye.EyeCalibration & session_key).to_dicts()) == 0


def test_a_session_whose_events_never_assembled_is_excluded(events_not_assembled_session):
    """Reviewer mutation 2 of 2: the OTHER half of the same dropped
    restriction. `report["errors"]` must actually name the real fault
    (`populate_session`/no syncbox recording) -- proving this session is
    excluded because assembly genuinely failed, not because it never
    reached the daemon at all."""
    from wl_preproc.schema import eye

    session_key, report = events_not_assembled_session
    assert any("populate_session" in message for message in report["errors"])
    assert len(eye.EyeCalibration.key_source & session_key) == 0
    assert len((eye.EyeCalibration & session_key).to_dicts()) == 0


@pytest.fixture(scope="module")
def coverage_gap_session(daemon_module, prefix, tmp_path_factory):
    """A real ohDPI recording, real fixation epochs naming real,
    well-conditioned targets -- but every window sits far outside
    `core.Segment`'s own aligned extent (~12.6 s), so none can be sampled.

    Reviewer finding 2: distinct from `no_ohdpi_file_session` (no segment
    AT ALL) and from a session with truly no target named
    (`resolve_calibration`'s own "no fixation epoch..." refusal): here,
    epochs genuinely exist and name real targets, and the fault is
    recording coverage, not the task code. Before the fix round this
    fixture would have produced the SAME reason as a session with no
    fixation epoch at all -- a false diagnosis.
    """
    root = tmp_path_factory.mktemp("eyecoverage")
    recipe = _recipe("2027-04-08_01", "eyecovr1", seed=410, include_ohdpi=True)
    truth = generate_session(root, recipe)
    session_dir = root / recipe.session_id
    targets = [(0.0, 0.0), (8.0, 8.0), (-8.0, 8.0), (8.0, -8.0)]
    # 100 s, 103 s, 106 s, 109 s: all comfortably beyond recipe.duration_s
    # (12.0 s) plus OHDPI_PRE_ROLL_S (0.6 s), so core.Segment's own aligned
    # extent never reaches them, regardless of the exact fit.
    windows = [(100.0 + trial_index * 3.0, target) for trial_index, target in enumerate(targets)]
    _write_fixations(session_dir, recipe, truth, windows)
    session_key = _land(
        root, recipe, datetime.datetime(2027, 4, 8, 9, 0),
        acquisition_systems=("syncbox", "ohdpi"),
    )
    report = daemon_module.run_once(prefix=prefix)
    return session_key, report


@pytest.fixture(scope="module")
def partial_coverage_session(daemon_module, prefix, tmp_path_factory):
    """Four real, in-coverage windows (`fitted_session`'s own shape) plus a
    FIFTH, far outside `core.Segment`'s extent. Calibration should still
    succeed from the four good windows -- but the fifth's drop must remain
    visible on the row, not silently absorbed (reviewer finding 2's
    "partial case")."""
    root = tmp_path_factory.mktemp("eyepartial")
    recipe = _recipe("2027-04-08_02", "eyepart1", seed=411, include_ohdpi=True)
    truth = generate_session(root, recipe)
    session_dir = root / recipe.session_id
    targets = [(0.0, 0.0), (8.0, 8.0), (-8.0, 8.0), (8.0, -8.0)]
    windows = [
        (trial_index * TRIAL_DURATION_S + 1.0, target)
        for trial_index, target in enumerate(targets)
    ]
    windows.append((200.0, (5.0, 5.0)))  # out of coverage: must be dropped
    _write_fixations(session_dir, recipe, truth, windows)
    session_key = _land(
        root, recipe, datetime.datetime(2027, 4, 8, 14, 0),
        acquisition_systems=("syncbox", "ohdpi"),
    )
    report = daemon_module.run_once(prefix=prefix)
    return session_key, report


def test_windows_all_outside_coverage_get_their_own_reason(
    coverage_gap_session, no_ohdpi_file_session, degenerate_session
):
    from wl_preproc.schema import eye

    session_key, _report = coverage_gap_session
    rows = (eye.EyeCalibration & session_key).to_dicts()
    assert len(rows) == 2
    my_reasons = set()
    for row in rows:
        assert row["calibration_source"] == "refused"
        assert row["n_points"] == 0
        assert "coverage" in row["reason"].lower()
        assert "no fixation epoch" not in row["reason"]
        assert "no fallback map validated" not in row["reason"]
        my_reasons.add(row["reason"])

    # Distinct from BOTH other refusal reasons this file already covers,
    # not merely a substring difference.
    no_file_key, _ = no_ohdpi_file_session
    degenerate_key, _ = degenerate_session
    other_reasons = {r["reason"] for r in (eye.EyeCalibration & no_file_key).to_dicts()}
    other_reasons |= {r["reason"] for r in (eye.EyeCalibration & degenerate_key).to_dicts()}
    assert my_reasons.isdisjoint(other_reasons)


def test_a_partially_dropped_window_still_calibrates_and_says_so(partial_coverage_session):
    from wl_preproc.schema import eye

    session_key, _report = partial_coverage_session
    rows = (eye.EyeCalibration & session_key).to_dicts()
    assert len(rows) == 2
    for row in rows:
        # The dropped fifth window never counts toward n_points, and does
        # not stop calibration from succeeding on the four that remain.
        assert row["calibration_source"] == "fitted"
        assert row["n_points"] == 4
        assert row["reason"] == "1 of 5 fixation windows had no ohDPI coverage"


REASON_COLUMN_MAX_LEN = 255


@pytest.fixture(scope="module")
def overlong_reason_session(daemon_module, prefix, tmp_path_factory):
    """A session that combines TWO of this file's other fixtures' own
    shapes: `degenerate_session`'s four coincident central targets (so
    `resolve_calibration` refuses with `calibration.py::fit_map`'s own
    221-character collinear/coincident message, "; no fallback map
    validated" making 248) AND `partial_coverage_session`'s fifth,
    out-of-coverage window (so `make()`'s own coverage-drop note gets
    appended too). Coordinator's own worked case: 248 + "; " + a
    coverage note comfortably exceeds `EyeCalibration.reason`'s
    `varchar(255)`, and this is the first fixture in this file to
    reach it -- `degenerate_session` alone has zero dropped windows, and
    `partial_coverage_session` alone always succeeds as `fitted`, whose
    own `reason` never carries `resolve_calibration`'s own text at all.
    """
    root = tmp_path_factory.mktemp("eyeoverflow")
    recipe = _recipe("2027-04-12_01", "eyeoverf", seed=416, include_ohdpi=True)
    truth = generate_session(root, recipe)
    session_dir = root / recipe.session_id
    windows = [
        (trial_index * TRIAL_DURATION_S + 1.0, (0.0, 0.0)) for trial_index in range(N_TRIALS)
    ]
    windows.append((200.0, (5.0, 5.0)))  # out of coverage: dropped
    _write_fixations(session_dir, recipe, truth, windows)
    session_key = _land(
        root, recipe, datetime.datetime(2027, 4, 12, 9, 0),
        acquisition_systems=("syncbox", "ohdpi"),
    )
    report = daemon_module.run_once(prefix=prefix)
    return session_key, report


def test_a_combined_reason_that_would_overflow_is_bounded_and_marked(overlong_reason_session):
    """The fix round's own bug, reproduced: before it, this exact
    combination raised `pymysql.err.DataError` inside `make()`, which
    `daemon.run_once()`'s `suppress_errors=True` turned into a daemon
    error rather than the graceful `refused`-with-a-reason row this
    module's own docstring calls a first-class outcome. `report["errors"]`
    must therefore be clean for this session -- not merely the row
    existing -- and the stored `reason` must still name BOTH causes, not
    just whichever one happened to be truncated away.
    """
    from wl_preproc.schema import eye

    session_key, report = overlong_reason_session
    assert _no_eye_stage_errors(report)

    rows = (eye.EyeCalibration & session_key).to_dicts()
    assert len(rows) == 2
    for row in rows:
        reason = row["reason"]
        assert row["calibration_source"] == "refused"
        assert len(reason) <= REASON_COLUMN_MAX_LEN
        # The marker sits after the truncated PRIMARY reason, not at the
        # very end of the string: `_combine_reason` appends the coverage
        # note (kept in full) after it, so "marked as truncated" is a
        # substring check, not `.endswith(...)`.
        assert "[reason truncated]" in reason
        # Both causes survive: the degenerate-geometry text (its own
        # beginning, since that is what a from-the-end truncation of the
        # PRIMARY reason keeps) and the coverage-drop note IN FULL, since
        # `_combine_reason` reserves that note's own room rather than
        # letting a from-the-end slice of the whole combined string
        # silently drop it.
        assert "collinear, coincident or conic targets" in reason
        assert reason.endswith("1 of 5 fixation windows had no ohDPI coverage")


def _no_eye_stage_errors(report: dict) -> bool:
    return not any(
        "EyeCalibration" in message or "EyeQuality" in message for message in report["errors"]
    )


def test_key_source_is_not_empty_for_a_landed_session(fitted_session):
    """The trap Controller ruling B names directly: with the empty
    `key_source` Task 9 shipped still in place, this is `pipeline.Session &
    "1=0"` -- empty for every session, landed or not -- and every other test
    in this file would find zero rows without this one ever failing to say
    why."""
    from wl_preproc.schema import eye

    session_key, _report = fitted_session
    assert len(eye.EyeCalibration.key_source & session_key) > 0
    assert len(eye.EyeQuality.key_source & session_key) > 0


def test_daemon_run_once_populates_both_tables(fitted_session):
    """Ruling B's second half: not `make()` in isolation, but a real
    `daemon.run_once()` pass -- the only thing that can also prove
    registering these two tables in `daemon._computed_tables()` (Task 9)
    and giving them a real `key_source`/`make()` (this task) actually
    cooperate end to end."""
    from wl_preproc.schema import eye

    session_key, report = fitted_session
    assert _no_eye_stage_errors(report)

    calibration_rows = (eye.EyeCalibration & session_key).to_dicts()
    assert len(calibration_rows) == 2
    assert {row["eye"] for row in calibration_rows} == {"left", "right"}

    quality_rows = (eye.EyeQuality & session_key).to_dicts()
    assert len(quality_rows) == 2
    assert {row["eye"] for row in quality_rows} == {"left", "right"}


def test_a_well_conditioned_session_yields_fitted(fitted_session):
    from wl_preproc.schema import eye

    session_key, _report = fitted_session
    rows = (eye.EyeCalibration & session_key).to_dicts()
    by_eye = {row["eye"]: row for row in rows}

    for row in by_eye.values():
        assert row["calibration_source"] == "fitted"
        assert row["n_points"] == N_TRIALS
        assert row["calibration_model"] in ("affine", "second_order")
        assert row["gx_const"] is not None
        assert row["conditioning"] is not None and row["conditioning"] > 0.0
        assert row["residual_deg_rms"] is not None
        assert row["reason"] == ""

    # Mutation guard named directly in this task's brief: "the synthetic
    # generator emits the real ohDPI format with genuinely different per-eye
    # geometry, so a per-eye test can discriminate." A `make()` that read
    # `file_eye="Left"` unconditionally regardless of `eye` would pass every
    # assertion above for BOTH rows and still be broken -- this is what
    # catches it.
    left_params = (by_eye["left"]["gx_dx"], by_eye["left"]["gx_const"], by_eye["left"]["gy_const"])
    right_params = (
        by_eye["right"]["gx_dx"], by_eye["right"]["gx_const"], by_eye["right"]["gy_const"]
    )
    assert left_params != right_params


def test_a_central_target_only_session_falls_through_to_refused(degenerate_session):
    from wl_preproc.schema import eye

    session_key, _report = degenerate_session
    rows = (eye.EyeCalibration & session_key).to_dicts()
    assert len(rows) == 2
    for row in rows:
        assert row["calibration_source"] == "refused"
        assert row["calibration_model"] is None
        assert row["gx_const"] is None
        assert row["n_points"] == N_TRIALS
        # `_conditioning` on the affine basis of four COINCIDENT targets.
        # Identical target rows make all three normalised basis columns the
        # same column, so the smallest singular value is exactly zero -- not
        # merely "small", but the guard's own floor case.
        assert row["conditioning"] == pytest.approx(0.0)
        assert "no fallback map validated" in row["reason"]


def test_no_ohdpi_file_is_refused_with_a_distinct_reason(
    no_ohdpi_file_session, degenerate_session
):
    """Controller ruling D: two different causes must produce two different
    `reason` values, not one collapsed "no gaze" string."""
    from wl_preproc.schema import eye

    no_file_key, _report = no_ohdpi_file_session
    degenerate_key, _degenerate_report = degenerate_session

    no_file_rows = (eye.EyeCalibration & no_file_key).to_dicts()
    assert len(no_file_rows) == 2
    for row in no_file_rows:
        assert row["calibration_source"] == "refused"
        assert row["n_points"] == 0
        assert "ohDPI" in row["reason"]
        assert "no fallback map validated" not in row["reason"]
        assert "no fixation epoch" not in row["reason"]

    degenerate_rows = (eye.EyeCalibration & degenerate_key).to_dicts()
    no_file_reasons = {row["reason"] for row in no_file_rows}
    degenerate_reasons = {row["reason"] for row in degenerate_rows}
    assert no_file_reasons.isdisjoint(degenerate_reasons)


def test_no_ohdpi_file_session_gets_no_eye_quality_row_either(no_ohdpi_file_session):
    """`EyeQuality.key_source` is keyed off `core.Segment`, not the coarser
    `core.AcquisitionSystem` -- see that property's own docstring for why:
    this table's schema has no way to express "checked, nothing usable" the
    way `EyeCalibration.reason` can, so a session with no aligned recording
    must stay outstanding here rather than get a fabricated zero."""
    from wl_preproc.schema import eye

    session_key, _report = no_ohdpi_file_session
    assert len(eye.EyeQuality.key_source & session_key) == 0
    assert len((eye.EyeQuality & session_key).to_dicts()) == 0


def test_carry_forward_candidate_prefers_nearest_same_day_and_a_preceding_tie(
    daemon_module, prefix
):
    """Design spec section 3.5 step 3, tested directly against
    `eye._best_carry_forward_candidate` rather than through a full session
    (Controller ruling C's conversion is exercised end to end by no fixture
    in this file, since none of the three scenarios the brief names ever
    lets carried-forward win -- this closes that gap at the level the logic
    actually lives at, a helper Ruling B's own key_source/daemon.run_once
    requirement does not apply to: it is not a daemon stage, and no mutation
    of `key_source` could make this test pass vacuously).

    Five candidate rows, same subject and eye, hand-planted with
    `allow_direct_insert=True` (DataJoint's own guard for a bare `dj.
    Computed` table outside `populate()` -- confirmed directly against
    `datajoint/autopopulate.py`'s `_allow_insert` class default):

    - 06:00 and 08:30 the same day, both `fitted` -- 08:30 is nearer.
    - 09:30 the same day, `fitted` -- exactly as far from 09:00 as 08:30 is
      (30 minutes each), so THIS is what actually exercises "preferring a
      preceding session": an unbroken tie on `abs(delta)` alone would leave
      the winner to insertion order or dict iteration, neither a real rule.
    - 08:55, `refused` -- nearer in wall-clock time than every fitted
      candidate above, and excluded anyway: only a `fitted` row has a real
      conditioning to be "best" by (see the helper's own docstring), so if
      this wins, the fitted-only restriction was not actually applied.
    - the next day, `fitted` -- excluded by the "(subject, date)" scope
      alone, regardless of how close 24 hours reads as a raw delta.
    """
    from wl_preproc.schema import eye, pipeline

    subject = "eyecarry"
    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )

    this_datetime = datetime.datetime(2027, 5, 1, 9, 0)
    fitted_datetimes = {
        "far_before": datetime.datetime(2027, 5, 1, 6, 0),
        "near_before": datetime.datetime(2027, 5, 1, 8, 30),
        "near_after": datetime.datetime(2027, 5, 1, 9, 30),
        "other_day": datetime.datetime(2027, 5, 2, 9, 0),
    }
    refused_datetime = datetime.datetime(2027, 5, 1, 8, 55)

    session_rows = [
        {"subject": subject, "session_datetime": dt}
        for dt in (*fitted_datetimes.values(), refused_datetime)
    ]
    pipeline.Session.insert(session_rows, skip_duplicates=True)

    base_fitted = {
        "eye": "left",
        "calibration_source": "fitted",
        # The identity map, in `basis(_, AFFINE)` order; the six quadratic
        # columns stay NULL, which is what an affine-tier fit looks like.
        "calibration_model": "affine",
        "gx_const": 0.0, "gx_dx": 1.0, "gx_dy": 0.0,
        "gx_dx2": None, "gx_dy2": None, "gx_dxdy": None,
        "gy_const": 0.0, "gy_dx": 0.0, "gy_dy": 1.0,
        "gy_dx2": None, "gy_dy2": None, "gy_dxdy": None,
        "validation_error_deg": 0.1,
        "n_points": 4,
        "n_from_calibration_block": 0,
        "n_from_task_fixation": 4,
        "conditioning": 0.9,
        "residual_deg_rms": 0.1,
        "residual_deg_max": 0.2,
        "carried_from_session_datetime": None,
        "reason": "",
    }
    rows = [
        {**base_fitted, "subject": subject, "session_datetime": dt}
        for dt in fitted_datetimes.values()
    ]
    rows.append(
        {
            **base_fitted,
            "subject": subject,
            "session_datetime": refused_datetime,
            "calibration_source": "refused",
            "calibration_model": None,
            "gx_const": None, "gx_dx": None, "gx_dy": None,
            "gy_const": None, "gy_dx": None, "gy_dy": None,
            "validation_error_deg": None,
            "n_points": 0,
            "n_from_task_fixation": 0,
            "conditioning": None,
            "residual_deg_rms": None,
            "residual_deg_max": None,
            "reason": "no fixation epoch named a target position",
        }
    )
    eye.EyeCalibration.insert(rows, allow_direct_insert=True)

    candidate = eye._best_carry_forward_candidate(
        "left", {"subject": subject, "session_datetime": this_datetime}
    )
    assert candidate is not None
    winning_datetime, winning_map = candidate
    assert winning_datetime == fitted_datetimes["near_before"]
    # `basis(_, AFFINE)` order, (const, dx, dy) per axis: the identity map
    # the inserted row's own `gx_`/`gy_` columns describe.
    assert winning_map.model is CalibrationModel.AFFINE
    assert winning_map.x == (0.0, 1.0, 0.0)
    assert winning_map.y == (0.0, 0.0, 1.0)

    # A different eye at the identical set of datetimes has no candidates at
    # all: `eye` restricts the pool exactly as `subject` and the date do.
    assert (
        eye._best_carry_forward_candidate(
            "right", {"subject": subject, "session_datetime": this_datetime}
        )
        is None
    )


def test_count_true_runs_counts_contiguous_runs_not_frames():
    """`EyeQuality.make()`'s blink-rate computation needs this to count
    RUNS, not lost frames -- and no fixture anywhere in this file exercises
    it, since `synth/ohdpi.py::write_ohdpi` always writes `DataQuality=100`
    (checked directly: `_fmt(100.0)` is hardcoded there, no fault ever
    varies it). A pure numpy helper, so this needs no session, no database,
    and no file -- there is no reason its own correctness should wait on
    those."""
    import numpy as np

    from wl_preproc.schema.eye import _count_true_runs

    assert _count_true_runs(np.array([], dtype=bool)) == 0
    assert _count_true_runs(np.array([False, False, False])) == 0
    assert _count_true_runs(np.array([True, True, True])) == 1
    # [T, T, F, T]: two runs, not the three frames a bare `.sum()` would give.
    assert _count_true_runs(np.array([True, True, False, True])) == 2
    assert _count_true_runs(np.array([True, False, True, False, True])) == 3
    # A run touching either edge of the array still counts as exactly one.
    assert _count_true_runs(np.array([True, False, False, True, True])) == 2


# --- Round-trip precision, BlockResidual, carried-forward end to end, and
# real tracking loss. Fix round, reviewer findings 1 and 4: every fixture
# above only asserts `calibration_source == "fitted"` and a positive
# conditioning score -- both true of a rank-deficient fit over four
# IDENTICAL, mis-sampled raw points, so gutting `eye._session_time_to_row`
# to `lambda segment, session_s: 0` (destroying the entire session-time ->
# ohDPI-row alignment) left every test above green. Nothing above asserts a
# fitted map is numerically CORRECT, `EyeCalibration.BlockResidual` is
# never queried by any test, and no fixture ever lets `carried_forward` win
# through the real `make()` path (only through the isolated
# `_best_carry_forward_candidate` unit test above). The fixtures below close
# all three.


@pytest.fixture(scope="module")
def round_trip_session(daemon_module, prefix, tmp_path_factory):
    """A KNOWN, invertible affine relationship, round-tripped through the
    real fit -- not "some map, unchecked accuracy" but the actual recovered
    parameters and residual compared to ground truth.

    Requires the ohDPI file and its `core.Segment` alignment to exist
    BEFORE fixation windows can be chosen -- the opposite order every other
    fixture in this file uses. `SystemTimebase`/`core.Segment` are
    populated directly first (both depend only on the sync box's barcodes
    and the raw ohDPI file, neither touched by fixation injection); only
    once the real segment is known are targets computed and written into
    the sync box log, and `daemon.run_once()` runs -- once -- afterward.

    `raw_points` is read via `_expected_raw_points`, which resolves each
    window's row range through `_row_for_time` -- an INDEPENDENT
    re-derivation of `eye._session_time_to_row`'s own linear map, not a
    call to it -- so a broken production alignment cannot also corrupt this
    fixture's own idea of the truth. A window a WRONG `_session_time_to_row`
    would sample from row 0 every time is not what this fixture asks the
    production code to sample from; the targets below are built against
    where each window's raw signal REALLY is.
    """
    from wl_preproc.schema import core, timebase

    root = tmp_path_factory.mktemp("eyeroundtrip")
    recipe = _recipe("2027-04-09_01", "eyernd01", seed=412, include_ohdpi=True)
    truth = generate_session(root, recipe)
    session_dir = root / recipe.session_id
    session_key = _land(
        root, recipe, datetime.datetime(2027, 4, 9, 9, 0),
        acquisition_systems=("syncbox", "ohdpi"),
    )

    timebase.SystemTimebase.populate()
    core.Segment.populate()
    segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()

    window_starts = [trial_index * TRIAL_DURATION_S + 1.0 for trial_index in range(N_TRIALS)]
    fixation_windows = [(start + 0.2, start + 0.8) for start in window_starts]
    raw_points = _expected_raw_points(session_dir, segment, "Left", fixation_windows)

    # Deliberately non-trivial: nonzero scale, cross-term AND offset on
    # both axes, so a fit that recovered only PART of it (scale but not the
    # cross-term, say) would still be caught.
    true_a = (0.05, 0.01, 2.0, -0.02, 0.06, -1.5)

    def apply_true(raw):
        x, y = raw
        return (
            true_a[0] * x + true_a[1] * y + true_a[2],
            true_a[3] * x + true_a[4] * y + true_a[5],
        )

    targets = [apply_true(raw) for raw in raw_points]
    _write_fixations(session_dir, recipe, truth, list(zip(window_starts, targets, strict=True)))

    report = daemon_module.run_once(prefix=prefix)
    return session_key, report, true_a


def test_a_known_affine_round_trips_through_the_fit(round_trip_session):
    """Reviewer mutation 1 of 2: gutting `_session_time_to_row` to always
    return row 0 passed every other test in this file. It cannot pass this
    one: every window would then sample the SAME one raw point, while the
    four targets injected here are genuinely spread (they are an invertible
    affine of four DIFFERENT real raw points) -- so the fit would be trying
    to explain four different targets from ~one raw input, which is a large
    residual and a recovered map far from `true_a`, not merely "some
    residual, unchecked"."""
    from wl_preproc.schema import eye

    session_key, _report, true_a = round_trip_session
    row = (eye.EyeCalibration & {**session_key, "eye": "left"}).fetch1()

    assert row["calibration_source"] == "fitted"
    # `true_a` is in this fixture's own flat `(a00, a01, b0, a10, a11, b1)`
    # order; the columns are in `basis(_, AFFINE)` order, constant first.
    recovered = (
        row["gx_dx"], row["gx_dy"], row["gx_const"],
        row["gy_dx"], row["gy_dy"], row["gy_const"],
    )
    for got, want in zip(recovered, true_a, strict=True):
        assert got == pytest.approx(want, abs=0.01)
    # The only real error source is `encode_dva`/`decode_dva`'s 0.01-degree
    # quantisation -- comfortably inside this bound, and far below what a
    # genuinely broken alignment would produce (the "no coverage" fixtures
    # above show refusal outright; a constant-row-0 sample would show a
    # residual of several degrees, not a fraction of one).
    assert row["residual_deg_rms"] < 0.05
    assert row["validation_error_deg"] < 0.05


def test_block_residual_rows_are_actually_produced_and_checked(round_trip_session):
    """Reviewer finding 4: `BlockResidual` had zero references anywhere in
    this file -- design spec section 3.6's entire per-block drift mechanism
    could have been deleted with the suite staying green. All four windows
    here land in the single RF_MAP block (`block_id=1`), so its residual
    must equal the master row's own -- not merely "a row exists"."""
    from wl_preproc.schema import eye

    session_key, _report, _true_a = round_trip_session
    master = (eye.EyeCalibration & {**session_key, "eye": "left"}).fetch1()
    block_row = (
        eye.EyeCalibration.BlockResidual & {**session_key, "eye": "left", "block_id": 1}
    ).fetch1()

    assert block_row["n_points"] == 4
    assert block_row["residual_deg_rms"] == pytest.approx(master["residual_deg_rms"], abs=1e-9)


@pytest.fixture(scope="module")
def carried_forward_session(daemon_module, prefix, tmp_path_factory):
    """Session A: the same round-trip construction as `round_trip_session`,
    landed and fitted first, under a distinct subject reused below.
    Session B: same subject, same calendar date, later that day -- a SINGLE
    fixation window (so its own fit is degenerate by point count alone, no
    conditioning subtlety needed), whose target is session B's OWN real raw
    point run through session A's SAME true affine. Session A's fitted map
    (very close to that same affine) therefore validates against session
    B's own fixation within `calibration.MAX_VALIDATION_ERROR_DEG`, and the
    chain should reach step 3 and accept it.

    Two separate `daemon.run_once()` calls, not one: session A's own
    `EyeCalibration` row must already be COMMITTED before session B's
    `make()` queries `_best_carry_forward_candidate`, and DataJoint gives no
    guarantee about the order two sessions' keys are processed in within a
    single `populate()` call.
    """
    from wl_preproc.schema import core, timebase

    subject = "eyecary2"

    root_a = tmp_path_factory.mktemp("carrysrc")
    recipe_a = _recipe("2027-04-10_01", subject, seed=413, include_ohdpi=True)
    truth_a = generate_session(root_a, recipe_a)
    session_dir_a = root_a / recipe_a.session_id
    session_key_a = _land(
        root_a, recipe_a, datetime.datetime(2027, 4, 10, 9, 0),
        acquisition_systems=("syncbox", "ohdpi"),
    )
    timebase.SystemTimebase.populate()
    core.Segment.populate()
    segment_a = (core.Segment & {**session_key_a, "system": "ohdpi"}).fetch1()

    window_starts_a = [trial_index * TRIAL_DURATION_S + 1.0 for trial_index in range(N_TRIALS)]
    fixation_windows_a = [(start + 0.2, start + 0.8) for start in window_starts_a]
    raw_points_a = _expected_raw_points(session_dir_a, segment_a, "Left", fixation_windows_a)

    true_a = (0.04, 0.0, 1.0, 0.0, 0.04, -1.0)

    def apply_true(raw):
        x, y = raw
        return (true_a[0] * x + true_a[1] * y + true_a[2], true_a[3] * x + true_a[4] * y + true_a[5])

    targets_a = [apply_true(raw) for raw in raw_points_a]
    _write_fixations(
        session_dir_a, recipe_a, truth_a, list(zip(window_starts_a, targets_a, strict=True))
    )
    daemon_module.run_once(prefix=prefix)

    root_b = tmp_path_factory.mktemp("carrydst")
    recipe_b = _recipe("2027-04-10_02", subject, seed=414, include_ohdpi=True)
    truth_b = generate_session(root_b, recipe_b)
    session_dir_b = root_b / recipe_b.session_id
    session_key_b = _land(
        root_b, recipe_b, datetime.datetime(2027, 4, 10, 14, 0),
        acquisition_systems=("syncbox", "ohdpi"),
    )
    timebase.SystemTimebase.populate()
    core.Segment.populate()
    segment_b = (core.Segment & {**session_key_b, "system": "ohdpi"}).fetch1()

    window_start_b = 1.0
    raw_point_b = _expected_raw_points(
        session_dir_b, segment_b, "Left", [(window_start_b + 0.2, window_start_b + 0.8)]
    )[0]
    target_b = apply_true(raw_point_b)
    _write_fixations(session_dir_b, recipe_b, truth_b, [(window_start_b, target_b)])

    report_b = daemon_module.run_once(prefix=prefix)
    return session_key_a, session_key_b, report_b


def test_carried_forward_writes_the_candidates_real_datetime(carried_forward_session):
    """Reviewer finding 4: `carried_from_session_datetime` had exactly one
    reference anywhere in this file -- a `None` literal, never asserted --
    so removing Controller ruling C's own guard (using the candidate's
    session_datetime rather than parsing `Calibration.carried_from`'s
    string) would have changed no test. This is the first end-to-end
    `carried_forward` row this file produces."""
    from wl_preproc.eye.calibration import MAX_VALIDATION_ERROR_DEG
    from wl_preproc.schema import eye

    session_key_a, session_key_b, _report_b = carried_forward_session
    row = (eye.EyeCalibration & {**session_key_b, "eye": "left"}).fetch1()

    assert row["calibration_source"] == "carried_forward"
    assert row["carried_from_session_datetime"] == session_key_a["session_datetime"]
    assert row["gx_const"] is not None
    # A borrowed map keeps the shape it was fitted at, rather than being
    # flattened on the way through `_map_from_row`.
    assert row["calibration_model"] in ("affine", "second_order")
    assert row["validation_error_deg"] is not None
    assert row["validation_error_deg"] <= MAX_VALIDATION_ERROR_DEG


@pytest.fixture(scope="module")
def lossy_quality_session(daemon_module, prefix, tmp_path_factory):
    """A real, alignable ohDPI recording (so `EyeQuality.key_source` admits
    it) with REAL, per-eye-DISTINCT tracking loss injected directly into the
    generated file's own `LeftDataQuality`/`RightDataQuality` columns --
    `synth/ohdpi.py::write_ohdpi` always writes `DataQuality=100`, so no
    fixture anywhere else in this file (or this plan's own synthetic
    generator) ever gives `EyeQuality.make()`'s inline re-derivation of
    `gaze.tracking_loss_fraction` (and its own per-eye COLUMN SELECTION) a
    real loss to compute from, or a real run to count.

    Left: three separated single-frame drops -> 3 runs. Right: a 3-frame
    run and a 2-frame run at DIFFERENT rows -> 2 runs, 5 lost frames.
    Deliberately different fractions AND different run counts between the
    two eyes -- the same "genuinely different per-eye geometry" discipline
    `tests/eye/test_gaze.py::test_tracking_loss_counts_frames_below_100_per_eye`
    already uses (2/5 vs 4/5) -- so a hardcoded `"Left"` column read, a
    frames-vs-runs mixup, or a per-eye mixup are each independently caught.
    """
    from wl_preproc.schema import core, timebase
    from wl_preproc.synth.ohdpi import HEADER

    root = tmp_path_factory.mktemp("eyelossy")
    recipe = _recipe("2027-04-11_01", "eyelossy", seed=415, include_ohdpi=True)
    generate_session(root, recipe)
    session_dir = root / recipe.session_id
    ohdpi_dir = session_dir / "ohdpi"
    (ohdpi_txt,) = ohdpi_dir.glob("*.txt")

    left_idx = HEADER.index("LeftDataQuality")
    right_idx = HEADER.index("RightDataQuality")
    lines = ohdpi_txt.read_text(encoding="utf-8").splitlines()
    header_line, data_lines = lines[0], lines[1:]

    left_loss_rows = (500, 1500, 2500)
    right_loss_rows = (700, 701, 702, 3000, 3001)
    for row_index in left_loss_rows:
        fields = data_lines[row_index].split(" ")
        fields[left_idx] = "0.0000"
        data_lines[row_index] = " ".join(fields)
    for row_index in right_loss_rows:
        fields = data_lines[row_index].split(" ")
        fields[right_idx] = "50.0000"
        data_lines[row_index] = " ".join(fields)
    ohdpi_txt.write_text("\n".join([header_line, *data_lines]) + "\n", encoding="utf-8")

    session_key = _land(
        root, recipe, datetime.datetime(2027, 4, 11, 9, 0),
        acquisition_systems=("syncbox", "ohdpi"),
    )
    timebase.SystemTimebase.populate()
    core.Segment.populate()
    segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()

    report = daemon_module.run_once(prefix=prefix)
    return session_key, report, segment, len(left_loss_rows), 3, len(right_loss_rows), 2


def test_tracking_loss_and_blink_rate_are_real_and_discriminate_by_eye(lossy_quality_session):
    from wl_preproc.schema import eye

    (
        session_key,
        _report,
        segment,
        left_lost_frames,
        left_runs,
        right_lost_frames,
        right_runs,
    ) = lossy_quality_session
    rows = {
        row["eye"]: row for row in (eye.EyeQuality & session_key).to_dicts()
    }
    duration_s = segment["end_s"] - segment["start_s"]
    n_samples = segment["n_samples"]

    assert rows["left"]["tracking_loss_fraction"] == pytest.approx(left_lost_frames / n_samples)
    assert rows["right"]["tracking_loss_fraction"] == pytest.approx(right_lost_frames / n_samples)
    assert rows["left"]["blink_rate_hz"] == pytest.approx(left_runs / duration_s)
    assert rows["right"]["blink_rate_hz"] == pytest.approx(right_runs / duration_s)

    # Non-zero and genuinely different between eyes: the vacuous "always
    # 0.0" and the "hardcoded Left column" mutations are both unreachable
    # here, unlike every fixture built before this fix round.
    assert rows["left"]["tracking_loss_fraction"] > 0.0
    assert rows["right"]["tracking_loss_fraction"] > 0.0
    assert rows["left"]["tracking_loss_fraction"] != rows["right"]["tracking_loss_fraction"]
    assert rows["left"]["blink_rate_hz"] != rows["right"]["blink_rate_hz"]


# ---------------------------------------------------------------------------
# The second-order rung, end to end.
#
# The generator's ordinary eye signal is a slow two-frequency drift, and its
# window means all lie on a curve -- close enough to a conic that the quadratic
# design matrix is near rank-deficient. Measured over 40,000 window placements
# across sessions from 27 s to 120 s, the best quadratic conditioning reachable
# from it was 0.0739, against `MIN_CONDITIONING`'s 0.10: NO generated session
# could reach this rung, so the rung had no end-to-end test it could pass, and
# `make()` writing `calibration_model` as a constant `"affine"` survived the
# whole suite.
#
# `SessionRecipe.eye_fixations` is what fixes that -- the eye HELD at stated
# raw positions, which is what a calibration block actually is. Held at a 3x3
# grid, the recovered constellation scores 0.9921 affine / 0.2266 second-order,
# inside the measured good-geometry band.
# ---------------------------------------------------------------------------

_GRID_RAW_PX = [(x, y) for x in (-35.0, 0.0, 35.0) for y in (-25.0, 0.0, 25.0)]

# `basis(_, SECOND_ORDER)` order: const, dx, dy, dx^2, dy^2, dx*dy. Deliberately
# asymmetric between the axes and nonzero in every term, so a fit recovering
# only part of it -- the linear part, say, or the axes transposed -- is caught.
_TRUE_X = (2.0, 0.05, 0.01, 3e-4, -2e-4, 1e-4)
_TRUE_Y = (-1.5, -0.02, 0.06, 1e-4, 4e-4, -2e-4)


def _apply_true_second_order(raw) -> tuple[float, float]:
    """The known map, written longhand -- independent of `basis`, for the
    reason `tests/eye/test_calibration_fit.py` gives: an expectation built
    from the function under test agrees with it by construction."""
    dx, dy = raw
    return (
        _TRUE_X[0] + _TRUE_X[1] * dx + _TRUE_X[2] * dy
        + _TRUE_X[3] * dx * dx + _TRUE_X[4] * dy * dy + _TRUE_X[5] * dx * dy,
        _TRUE_Y[0] + _TRUE_Y[1] * dx + _TRUE_Y[2] * dy
        + _TRUE_Y[3] * dx * dx + _TRUE_Y[4] * dy * dy + _TRUE_Y[5] * dx * dy,
    )


def _held_gaze_recipe(
    session_id: str, subject: str, seed: int, *, holds_px, task_type: TaskTypeCode
) -> SessionRecipe:
    """One trial per held position, the gaze pinned across each trial's own
    calibration window.

    The hold spans `[trial_start + 1.15, trial_start + 1.85]`, strictly
    containing the `[trial_start + 1.2, trial_start + 1.8]` window
    `_fixation_words` opens, so every sampled frame is a held one and the
    window mean is the held position rather than a blend of hold and drift.
    """
    from wl_preproc.synth.recipe import EyeFixationSpec

    n_trials = len(holds_px)
    fixations = tuple(
        EyeFixationSpec(
            start_s=index * TRIAL_DURATION_S + 1.15,
            end_s=index * TRIAL_DURATION_S + 1.85,
            x_px=x_px,
            y_px=y_px,
        )
        for index, (x_px, y_px) in enumerate(holds_px)
    )
    return SessionRecipe(
        session_id=session_id,
        subject=subject,
        rig="rig-a",
        systems=("syncbox", "ohdpi"),
        blocks=(
            BlockSpec(
                task_type=task_type, n_trials=n_trials, trial_duration_s=TRIAL_DURATION_S
            ),
        ),
        montages=(MontageSpec(start_s=0.0, end_s=n_trials * TRIAL_DURATION_S),),
        n_ap_channels=4,
        ap_sample_rate_hz=30_000.0,
        seed=seed,
        eye_fixations=fixations,
    )


def _held_gaze_session(
    daemon_module, prefix, root, *, session_id, subject, seed, session_datetime,
    holds_px, task_type, target_of,
):
    """Generate, land, align, then write targets derived from the REAL raw
    points and run the daemon once.

    Same ordering as `round_trip_session` and for the same reason: the ohDPI
    file and its `core.Segment` alignment must exist before targets can be
    computed, because the targets are a known function of the raw points the
    pipeline will actually sample -- read here through `_expected_raw_points`,
    which resolves rows by this file's own independent re-derivation.
    """
    from wl_preproc.schema import core, timebase

    recipe = _held_gaze_recipe(session_id, subject, seed, holds_px=holds_px, task_type=task_type)
    truth = generate_session(root, recipe)
    session_dir = root / recipe.session_id
    session_key = _land(
        root, recipe, session_datetime, acquisition_systems=("syncbox", "ohdpi")
    )

    timebase.SystemTimebase.populate()
    core.Segment.populate()
    segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()

    window_starts = [index * TRIAL_DURATION_S + 1.0 for index in range(len(holds_px))]
    fixation_windows = [(start + 0.2, start + 0.8) for start in window_starts]
    raw_points = _expected_raw_points(session_dir, segment, "Left", fixation_windows)

    targets = [target_of(raw) for raw in raw_points]
    _write_fixations(session_dir, recipe, truth, list(zip(window_starts, targets, strict=True)))

    report = daemon_module.run_once(prefix=prefix)
    return session_key, report, raw_points


@pytest.fixture(scope="module")
def second_order_session(daemon_module, prefix, tmp_path_factory):
    root = tmp_path_factory.mktemp("eyesecondorder")
    return _held_gaze_session(
        daemon_module, prefix, root,
        session_id="2027-04-14_01", subject="eyeso001", seed=414,
        session_datetime=datetime.datetime(2027, 4, 14, 9, 0),
        holds_px=_GRID_RAW_PX,
        task_type=TaskTypeCode.CALIBRATION,
        target_of=_apply_true_second_order,
    )


def test_a_grid_session_reaches_the_second_order_rung(second_order_session):
    """The rung this whole plan exists for, end to end through `make()`."""
    from wl_preproc.schema import eye

    session_key, report, _raw = second_order_session
    assert _no_eye_stage_errors(report)

    row = (eye.EyeCalibration & {**session_key, "eye": "left"}).fetch1()
    assert row["calibration_source"] == "fitted"
    assert row["calibration_model"] == "second_order"
    assert row["n_points"] == len(_GRID_RAW_PX)


def test_the_second_order_coefficients_are_numerically_recovered(second_order_session):
    """Not "a map came back": all twelve coefficients, against ground truth.

    The previous plan shipped a suite in which gutting the entire
    session-time-to-row alignment left every test green, precisely because
    nothing asserted a fitted map was numerically correct. At twice the
    parameter count that must not recur.

    Tolerances are per basis term because the terms have wildly different
    lever arms: the constellation spans +-35 px in `dx`, so `dx^2` reaches
    1225 and a coefficient there is ~1e-4, while the constant is ~2. The one
    real error source is `encode_dva`/`decode_dva`'s 0.01-degree target
    quantisation (~0.003 deg standard deviation), which divides by each
    term's own spread on the way into the coefficient.
    """
    from wl_preproc.schema import eye

    session_key, _report, _raw = second_order_session
    row = (eye.EyeCalibration & {**session_key, "eye": "left"}).fetch1()

    recovered_x = tuple(row[f"gx_{s}"] for s in ("const", "dx", "dy", "dx2", "dy2", "dxdy"))
    recovered_y = tuple(row[f"gy_{s}"] for s in ("const", "dx", "dy", "dx2", "dy2", "dxdy"))

    tolerances = (2e-2, 5e-4, 5e-4, 2e-5, 2e-5, 2e-5)
    for got, want, tol in zip(recovered_x, _TRUE_X, tolerances, strict=True):
        assert got == pytest.approx(want, abs=tol)
    for got, want, tol in zip(recovered_y, _TRUE_Y, tolerances, strict=True):
        assert got == pytest.approx(want, abs=tol)

    assert row["residual_deg_rms"] < 0.02
    assert row["validation_error_deg"] < 0.02


def test_a_dedicated_calibration_block_is_counted_as_one(second_order_session):
    """Task 4's real `TaskTypeCode.CALIBRATION`, replacing the provisional
    `MEMORY_GUIDED_SACCADE` reading. Every window here sits in a block that
    declares itself a calibration block, so every point is attributed to one
    and none to an incidental task fixation."""
    from wl_preproc.schema import eye

    session_key, _report, _raw = second_order_session
    for row in (eye.EyeCalibration & session_key).to_dicts():
        assert row["n_from_calibration_block"] == len(_GRID_RAW_PX)
        assert row["n_from_task_fixation"] == 0


# --- The ring: the geometry that must fall to the affine rung ---------------

_RING_RAW_PX = [
    (30.0 * float(np.cos(angle)), 30.0 * float(np.sin(angle)))
    for angle in (np.arange(8) * 2 * np.pi / 8)
]


def _apply_true_affine(raw) -> tuple[float, float]:
    dx, dy = raw
    return (_TRUE_X[0] + _TRUE_X[1] * dx + _TRUE_X[2] * dy,
            _TRUE_Y[0] + _TRUE_Y[1] * dx + _TRUE_Y[2] * dy)


@pytest.fixture(scope="module")
def ring_session(daemon_module, prefix, tmp_path_factory):
    root = tmp_path_factory.mktemp("eyering")
    return _held_gaze_session(
        daemon_module, prefix, root,
        session_id="2027-04-15_01", subject="eyering1", seed=415,
        session_datetime=datetime.datetime(2027, 4, 15, 9, 0),
        holds_px=_RING_RAW_PX,
        task_type=TaskTypeCode.CALIBRATION,
        target_of=_apply_true_affine,
    )


def test_a_ring_session_lands_on_affine_with_null_quadratic_columns(ring_session):
    """Eight targets on a ring are an ordinary saccade-task geometry: they
    constrain an affine perfectly and a quadratic not at all, since points on
    a circle satisfy `x^2 + y^2 = r^2`. The session must therefore get its OWN
    affine map -- `fitted`, not borrowed -- and the six quadratic columns must
    be NULL, which is what makes `calibration_model` readable as the authority
    rather than guessable from the data.
    """
    from wl_preproc.schema import eye

    session_key, report, _raw = ring_session
    assert _no_eye_stage_errors(report)

    row = (eye.EyeCalibration & {**session_key, "eye": "left"}).fetch1()
    assert row["calibration_source"] == "fitted"
    assert row["calibration_model"] == "affine"
    assert row["n_points"] == len(_RING_RAW_PX)

    for suffix in ("dx2", "dy2", "dxdy"):
        assert row[f"gx_{suffix}"] is None
        assert row[f"gy_{suffix}"] is None
    for suffix in ("const", "dx", "dy"):
        assert row[f"gx_{suffix}"] is not None
        assert row[f"gy_{suffix}"] is not None

    # And the affine it did fit is the RIGHT one, not merely present.
    assert row["gx_const"] == pytest.approx(_TRUE_X[0], abs=2e-2)
    assert row["gx_dx"] == pytest.approx(_TRUE_X[1], abs=5e-4)
    assert row["gy_dy"] == pytest.approx(_TRUE_Y[2], abs=5e-4)
    assert row["residual_deg_rms"] < 0.02


def test_a_carried_forward_second_order_map_keeps_its_shape(daemon_module, prefix):
    """`_map_from_row` branches on `calibration_model`, and this is what makes
    that branch load-bearing rather than decorative.

    Found by mutation: with only affine rows on record, `_map_from_row`
    hardcoded to read three coefficients and label them `affine` passed the
    entire suite -- while `_best_carry_forward_candidate`'s own comment claimed
    a borrowed second-order map keeps its shape. A comment asserting behaviour
    nothing exercises is the defect this subsystem's handoff names twelve
    times over.

    Inserted directly rather than fitted, for the reason the nearest-tie test
    beside it gives: the selection logic is what is under test here, not the
    fit that produced a candidate.
    """
    from wl_preproc.schema import eye, pipeline

    subject = "eyeso2nd"
    source_datetime = datetime.datetime(2027, 4, 21, 8, 0)
    this_datetime = datetime.datetime(2027, 4, 21, 11, 0)

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    pipeline.Session.insert(
        [
            {"subject": subject, "session_datetime": dt}
            for dt in (source_datetime, this_datetime)
        ],
        skip_duplicates=True,
    )
    eye.EyeCalibration.insert1(
        {
            "subject": subject,
            "session_datetime": source_datetime,
            "eye": "left",
            "calibration_source": "fitted",
            "calibration_model": "second_order",
            "gx_const": 2.0, "gx_dx": 0.05, "gx_dy": 0.01,
            "gx_dx2": 3e-4, "gx_dy2": -2e-4, "gx_dxdy": 1e-4,
            "gy_const": -1.5, "gy_dx": -0.02, "gy_dy": 0.06,
            "gy_dx2": 1e-4, "gy_dy2": 4e-4, "gy_dxdy": -2e-4,
            "validation_error_deg": 0.1,
            "n_points": 9,
            "n_from_calibration_block": 9,
            "n_from_task_fixation": 0,
            "conditioning": 0.75,
            "residual_deg_rms": 0.1,
            "residual_deg_max": 0.2,
            "carried_from_session_datetime": None,
            "reason": "",
        },
        allow_direct_insert=True,
    )

    candidate = eye._best_carry_forward_candidate(
        "left", {"subject": subject, "session_datetime": this_datetime}
    )

    assert candidate is not None
    when, borrowed = candidate
    assert when == source_datetime
    assert borrowed.model is CalibrationModel.SECOND_ORDER
    assert borrowed.x == (2.0, 0.05, 0.01, 3e-4, -2e-4, 1e-4)
    assert borrowed.y == (-1.5, -0.02, 0.06, 1e-4, 4e-4, -2e-4)
    assert borrowed.n_points == 9
    assert borrowed.conditioning == pytest.approx(0.75)


# --- The experiment controller's log, and where it is allowed to be ---------


def _write_walkable_bhv2(path) -> None:
    """A structurally walkable `.bhv2` with no `MLConfig` in it.

    `read_calibration` returns absence for it rather than raising -- "a file
    that walks fine but simply has no MLConfig is ALSO absence, not an error"
    (that function's own docstring). That is all these tests need: they are
    about WHICH FILE gets found, never about what is inside one, which
    `tests/eye/test_bhv2.py` covers against a real binary layout.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_the_expcontroller_log_is_found_in_its_own_directory(tmp_path):
    """The first positive coverage `_find_expcontroller_log` has ever had.

    Every fixture in this file produces a session with no such log at all --
    the synthetic generator writes `task.json`, never a `.bhv2` -- so the
    lookup returned `None` in every test that existed and a lookup hardcoded
    to `return None` would have passed all of them.
    """
    from wl_preproc.contracts.paths import EXPCONTROLLER_DIRNAME
    from wl_preproc.schema import eye

    log = tmp_path / EXPCONTROLLER_DIRNAME / "session.bhv2"
    _write_walkable_bhv2(log)

    assert eye._find_expcontroller_log(tmp_path) == log


def test_a_log_outside_the_expcontroller_directory_is_not_found(tmp_path):
    """THE point of settling the convention.

    This function used to `rglob` the whole session tree and take the first
    match. Keeping that as a fallback would keep the ambiguity the convention
    exists to remove: a file somewhere unexpected would be used silently, and
    "no log" would stay indistinguishable from "log in the wrong place". A log
    the transfer put elsewhere is now a visibly absent one.
    """
    from wl_preproc.schema import eye

    _write_walkable_bhv2(tmp_path / "stray.bhv2")
    _write_walkable_bhv2(tmp_path / "ohdpi" / "misfiled.bhv2")
    _write_walkable_bhv2(tmp_path / "monkeylogic" / "old_convention.bhv2")

    assert eye._find_expcontroller_log(tmp_path) is None


def test_an_absent_or_empty_expcontroller_directory_is_an_ordinary_skip(tmp_path):
    """Neither is an error: design spec section 4.5, "a missing or unreadable
    .bhv2 is not an error"."""
    from wl_preproc.contracts.paths import EXPCONTROLLER_DIRNAME
    from wl_preproc.schema import eye

    assert eye._find_expcontroller_log(tmp_path) is None

    (tmp_path / EXPCONTROLLER_DIRNAME).mkdir()
    assert eye._find_expcontroller_log(tmp_path) is None


def test_the_first_log_by_name_is_taken_when_a_session_has_several(tmp_path):
    """Sorted, so the choice is stable rather than filesystem-order dependent
    -- the same guarantee the whole-tree search gave, kept."""
    from wl_preproc.contracts.paths import EXPCONTROLLER_DIRNAME
    from wl_preproc.schema import eye

    for name in ("c.bhv2", "a.bhv2", "b.bhv2"):
        _write_walkable_bhv2(tmp_path / EXPCONTROLLER_DIRNAME / name)

    found = eye._find_expcontroller_log(tmp_path)
    assert found is not None and found.name == "a.bhv2"


def _write_expcontroller_yaml(path) -> None:
    """A minimal, syntactically-valid YAML file at `path`.

    Deliberately not a full `read_expcontroller_map` contract (no
    `mapping_version`/`model`/... ) -- these tests are about WHICH FILE
    `_find_expcontroller_log` finds, never about what is inside one, exactly
    the same restriction `_write_walkable_bhv2`'s own docstring states for
    its `.bhv2` counterpart. `tests/eye/test_expcontroller.py` covers the
    contract itself.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("placeholder: true\n")


def test_the_expcontroller_log_is_found_when_it_is_the_new_format(tmp_path):
    """The second glob `_find_expcontroller_log`'s own docstring reserved a
    place for (HANDOVER-wl-expcontroller.md Ask 1): `*.yaml`, alongside the
    pre-existing `*.bhv2`, in the same role-named directory."""
    from wl_preproc.contracts.paths import EXPCONTROLLER_DIRNAME
    from wl_preproc.schema import eye

    log = tmp_path / EXPCONTROLLER_DIRNAME / "session.yaml"
    _write_expcontroller_yaml(log)

    assert eye._find_expcontroller_log(tmp_path) == log


def test_a_yaml_log_outside_the_expcontroller_directory_is_not_found(tmp_path):
    """The convention `test_a_log_outside_the_expcontroller_directory_is_not_
    found` proves for `.bhv2` applies identically to the new format: reading
    ONLY `EXPCONTROLLER_DIRNAME`, with no whole-tree fallback, is the point,
    regardless of which controller wrote the stray file."""
    from wl_preproc.schema import eye

    _write_expcontroller_yaml(tmp_path / "stray.yaml")
    _write_expcontroller_yaml(tmp_path / "ohdpi" / "misfiled.yaml")

    assert eye._find_expcontroller_log(tmp_path) is None


def test_matches_across_both_formats_are_sorted_together(tmp_path):
    """`_find_expcontroller_log`'s own docstring states the two globs are
    sorted TOGETHER, not tried one format after the other: the choice among
    matches is by name, never by format. Proven here with one file of each
    format, named so the alphabetically-first one is the `.yaml`, the
    opposite of the two globs' own textual order (`*.bhv2` before `*.yaml`)
    -- a test that would still pass if the implementation silently preferred
    `.bhv2` regardless of name would prove nothing.
    """
    from wl_preproc.contracts.paths import EXPCONTROLLER_DIRNAME
    from wl_preproc.schema import eye

    _write_walkable_bhv2(tmp_path / EXPCONTROLLER_DIRNAME / "z_session.bhv2")
    _write_expcontroller_yaml(tmp_path / EXPCONTROLLER_DIRNAME / "a_session.yaml")

    found = eye._find_expcontroller_log(tmp_path)
    assert found is not None and found.name == "a_session.yaml"


# --- The online candidate applied per eye (review round 1) ------------------
#
# `degenerate_session`'s own geometry (every trial fixates the same central
# (0, 0)) makes `resolve_calibration`'s step 1 (FITTED) refuse outright --
# section 3.5's conditioning guard -- which is exactly what is wanted here:
# it forces the chain down to step 2 (ONLINE) without needing a second
# fixture. The two online maps below are constant-only (AFFINE `[1, dx, dy]`
# with the `dx`/`dy` coefficients zeroed), so `apply_map` predicts the SAME
# point -- the constant term alone -- for every raw input regardless of what
# the real synthetic ohDPI trace actually contains, and that point is chosen
# close enough to (0, 0) to clear `MAX_VALIDATION_ERROR_DEG` for certain.
# This sidesteps needing to know the synthetic generator's exact raw output
# to build a map that provably validates -- the same trick `tests/eye/
# test_calibration_chain.py`'s own `_ZERO_MAP` uses, extended so LEFT and
# RIGHT are distinguishable from each other rather than both all-zero.

_ONLINE_LEFT_CONST = (0.001, -0.001)
_ONLINE_RIGHT_CONST = (0.002, -0.002)


def _write_per_eye_expcontroller_log(session_dir, *, left: bool, right: bool) -> None:
    from wl_preproc.contracts.paths import EXPCONTROLLER_DIRNAME

    def _eye_block(const: tuple[float, float]) -> str:
        gx0, gy0 = const
        return (
            "  model: affine\n"
            f"  coefficients:\n    x: [{gx0}, 0.0, 0.0]\n    y: [{gy0}, 0.0, 0.0]\n"
            "  conditioning: 0.9\n  rms_residual_deg: 0.1\n"
        )

    text = 'mapping_version: 1\nraw_definition: "CR1 - CR4"\ntargets:\n  - [0.0, 0.0]\n'
    if left:
        text += "left:\n" + _eye_block(_ONLINE_LEFT_CONST)
    if right:
        text += "right:\n" + _eye_block(_ONLINE_RIGHT_CONST)

    directory = session_dir / EXPCONTROLLER_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "calibration.yaml").write_text(text)


@pytest.fixture(scope="module")
def online_per_eye_session(daemon_module, prefix, tmp_path_factory):
    """`degenerate_session`'s own geometry, plus a two-eye expcontroller log
    written into `expcontroller/` BEFORE `daemon_module.run_once()` runs --
    the only difference from `degenerate_session` itself. FITTED still
    refuses (same central-only targets); with a `.bhv2`-shaped one-map-both-
    eyes candidate this would fall through to it identically for both rows,
    which is exactly the bug review round 1 found. A genuinely per-eye
    candidate is the only way to prove `EyeCalibration.make()` now selects
    the right one per eye rather than the same one for both.
    """
    root = tmp_path_factory.mktemp("eyeonline")
    recipe = _recipe("2027-04-08_01", "eyeonln1", seed=408, include_ohdpi=True)
    truth = generate_session(root, recipe)
    targets = [(0.0, 0.0)] * N_TRIALS
    _inject_fixations(root / recipe.session_id, recipe, truth, targets)
    _write_per_eye_expcontroller_log(root / recipe.session_id, left=True, right=True)
    session_key = _land(
        root, recipe, datetime.datetime(2027, 4, 8, 9, 0),
        acquisition_systems=("syncbox", "ohdpi"),
    )
    report = daemon_module.run_once(prefix=prefix)
    return session_key, report


def test_online_source_is_used_with_a_different_map_per_eye(online_per_eye_session):
    """The end-to-end proof review round 1 asked for: "a session gets the
    right one applied to each eye." Both rows must come from the ONLINE
    source (FITTED refused by construction, above) and must carry the
    coefficients FOR THAT EYE, not the other eye's, and not each other's --
    the exact bug a single shared candidate produced silently before this
    fix: whichever eye happened to validate first, or both eyes getting one
    map neither was really fit to.
    """
    from wl_preproc.schema import eye

    session_key, _report = online_per_eye_session
    rows = (eye.EyeCalibration & session_key).to_dicts()
    by_eye = {row["eye"]: row for row in rows}

    assert by_eye["left"]["calibration_source"] == "online"
    assert by_eye["right"]["calibration_source"] == "online"

    left_const = (by_eye["left"]["gx_const"], by_eye["left"]["gy_const"])
    right_const = (by_eye["right"]["gx_const"], by_eye["right"]["gy_const"])
    assert left_const == pytest.approx(_ONLINE_LEFT_CONST)
    assert right_const == pytest.approx(_ONLINE_RIGHT_CONST)
    assert left_const != right_const


@pytest.fixture(scope="module")
def online_left_only_session(daemon_module, prefix, tmp_path_factory):
    """The other half of review round 1's test list: "a single-eye file
    leaves the other eye without an online candidate rather than borrowing
    its sibling's." Identical to `online_per_eye_session` except the log
    offers `left` only."""
    root = tmp_path_factory.mktemp("eyeonlineleft")
    recipe = _recipe("2027-04-09_01", "eyeonln2", seed=409, include_ohdpi=True)
    truth = generate_session(root, recipe)
    targets = [(0.0, 0.0)] * N_TRIALS
    _inject_fixations(root / recipe.session_id, recipe, truth, targets)
    _write_per_eye_expcontroller_log(root / recipe.session_id, left=True, right=False)
    session_key = _land(
        root, recipe, datetime.datetime(2027, 4, 9, 9, 0),
        acquisition_systems=("syncbox", "ohdpi"),
    )
    report = daemon_module.run_once(prefix=prefix)
    return session_key, report


def test_a_single_eye_online_log_does_not_lend_its_map_to_the_other_eye(online_left_only_session):
    """`left` gets its own online map (proving the log was read and used at
    all -- a session where NEITHER eye used it would pass a weaker version
    of this test for the wrong reason). `right` has no candidate of its own
    and no earlier same-day session to carry forward from either (same
    absence `degenerate_session` itself relies on), so REFUSED is the only
    reachable outcome for it -- never `left`'s map borrowed across."""
    from wl_preproc.schema import eye

    session_key, _report = online_left_only_session
    rows = (eye.EyeCalibration & session_key).to_dicts()
    by_eye = {row["eye"]: row for row in rows}

    assert by_eye["left"]["calibration_source"] == "online"
    left_const = (by_eye["left"]["gx_const"], by_eye["left"]["gy_const"])
    assert left_const == pytest.approx(_ONLINE_LEFT_CONST)

    assert by_eye["right"]["calibration_source"] == "refused"
    assert by_eye["right"]["gx_const"] is None


# --- The second-order conditioning margin -----------------------------------


def test_the_second_order_margin_is_recorded_where_the_geometry_cleared_it(
    second_order_session,
):
    """`calibration_model` records the verdict; this column records the
    margin. A grid session clears the 0.10 bar with room."""
    from wl_preproc.eye.calibration import MIN_CONDITIONING, CalibrationModel
    from wl_preproc.schema import eye

    session_key, _report, _raw = second_order_session
    row = (eye.EyeCalibration & {**session_key, "eye": "left"}).fetch1()

    assert row["calibration_model"] == "second_order"
    assert row["conditioning_second_order"] > MIN_CONDITIONING[CalibrationModel.SECOND_ORDER]


def test_the_second_order_margin_shows_why_a_ring_fell_to_affine(ring_session):
    """The difference between "nudge the target grid" and "redesign the task".

    Points on a circle satisfy `x^2 + y^2 = r^2`, so the quadratic columns
    collapse onto the constant one and no amount of nudging the grid helps --
    the geometry itself has to change. Without this column the row says only
    `affine`, and a hopeless constellation reads identically to a near miss.

    **Measured 0.0055, not exactly zero**, and the difference is the point of
    testing this end to end rather than on paper: the held gaze carries 0.5 px
    of measurement noise, so the sampled window means sit slightly off the
    exact conic and the degeneracy is near-exact rather than algebraic.
    `tests/eye/test_calibration_fit.py::test_a_ring_of_eight_is_refused_for_
    second_order` pins the exact 0.0000 on exact coordinates; this pins that
    real noise does not lift it anywhere near the 0.10 threshold -- it stays
    more than an order of magnitude below.
    """
    from wl_preproc.eye.calibration import MIN_CONDITIONING, CalibrationModel
    from wl_preproc.schema import eye

    session_key, _report, _raw = ring_session
    row = (eye.EyeCalibration & {**session_key, "eye": "left"}).fetch1()

    assert row["calibration_model"] == "affine"
    assert row["conditioning_second_order"] < MIN_CONDITIONING[CalibrationModel.SECOND_ORDER] / 5
    # The affine measure is healthy on the same points -- which is exactly the
    # pair of numbers that says "the geometry is fine, the MODEL is not".
    assert row["conditioning"] > 0.5


def test_the_margin_is_null_below_the_point_count_rather_than_misleading(fitted_session):
    """NOT because it cannot be computed there -- it can, and the number is a
    trap. A 4x6 design yields only four singular values and their ratio is
    structurally blind to the two missing dimensions: four spread targets read
    a healthy 0.2787 while being underdetermined outright. Storing that would
    invite reading a point-count failure as a geometry success.
    """
    from wl_preproc.schema import eye

    session_key, _report = fitted_session
    for row in (eye.EyeCalibration & session_key).to_dicts():
        assert row["n_points"] == N_TRIALS < 6
        assert row["conditioning_second_order"] is None
        # The affine measure is still recorded: that one IS meaningful at four
        # points, and it is what says whether the fit that ran was constrained.
        assert row["conditioning"] is not None
