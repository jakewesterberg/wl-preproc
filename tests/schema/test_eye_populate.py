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


def _inject_fixations(session_dir, recipe, truth, targets_deg: list[tuple[float, float]]) -> None:
    """One `TARGET_POSITION(FIXATION_POINT)` + `FIXATION_ACQUIRED` +
    `FIXATION_END` window per trial, one entry of `targets_deg` each.

    Placed at `trial_start + [1.0, 1.2, 1.8]` -- comfortably inside a 3.0 s
    trial and clear of `synth/timeline.py::_emit`'s own two word clusters
    (TRIAL_START/TRIAL_NUMBER within ~5 ms of `trial_start`,
    TRIAL_CORRECT/TRIAL_END within ~2 ms of `trial_start + TRIAL_DURATION_S`)
    -- so the merged, re-sorted stream can never interleave one payload's
    words with another's on the shared strobed bus (`_emit`'s own docstring
    names exactly this hazard).
    """
    assert len(targets_deg) == N_TRIALS
    extra: list[tuple[float, int]] = []
    for trial_index, (x_deg, y_deg) in enumerate(targets_deg):
        trial_start = trial_index * TRIAL_DURATION_S
        target_words = encode_payload(
            Escape.TARGET_POSITION,
            [int(TargetRole.FIXATION_POINT), encode_dva(x_deg), encode_dva(y_deg)],
        )
        t = trial_start + 1.0
        for offset, word in enumerate(target_words):
            extra.append((t + offset * CODE_WORD_SPACING_S, word))
        extra.append((trial_start + 1.2, int(TaskEvent.FIXATION_ACQUIRED)))
        extra.append((trial_start + 1.8, int(TaskEvent.FIXATION_END)))

    merged = tuple(sorted((*truth.code_words, *extra), key=lambda pair: pair[0]))
    new_truth = dataclasses.replace(truth, code_words=merged)
    write_syncbox_log(session_dir / "syncbox" / "syncbox.log", recipe, new_truth, drift_ppm=0.0)


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
        assert row["a00"] is not None
        assert row["conditioning"] is not None and row["conditioning"] > 0.0
        assert row["residual_deg_rms"] is not None
        assert row["reason"] == ""

    # Mutation guard named directly in this task's brief: "the synthetic
    # generator emits the real ohDPI format with genuinely different per-eye
    # geometry, so a per-eye test can discriminate." A `make()` that read
    # `file_eye="Left"` unconditionally regardless of `eye` would pass every
    # assertion above for BOTH rows and still be broken -- this is what
    # catches it.
    left_params = (by_eye["left"]["a00"], by_eye["left"]["b0"], by_eye["left"]["b1"])
    right_params = (by_eye["right"]["a00"], by_eye["right"]["b0"], by_eye["right"]["b1"])
    assert left_params != right_params


def test_a_central_target_only_session_falls_through_to_refused(degenerate_session):
    from wl_preproc.schema import eye

    session_key, _report = degenerate_session
    rows = (eye.EyeCalibration & session_key).to_dicts()
    assert len(rows) == 2
    for row in rows:
        assert row["calibration_source"] == "refused"
        assert row["a00"] is None
        assert row["n_points"] == N_TRIALS
        # `_conditioning` on four coincident points: the centred spread is
        # exactly zero, so this is not merely "small" but the guard's own
        # floor case.
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
        "a00": 1.0, "a01": 0.0, "b0": 0.0,
        "a10": 0.0, "a11": 1.0, "b1": 0.0,
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
            "a00": None, "a01": None, "b0": None,
            "a10": None, "a11": None, "b1": None,
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
    assert winning_map.a == (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)

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
