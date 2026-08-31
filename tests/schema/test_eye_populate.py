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
    `resolve_calibration` refuses with `calibration.py::fit_affine`'s own
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
