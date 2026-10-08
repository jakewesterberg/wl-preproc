"""`SaccadeMainSequence` populates (design spec
`docs/superpowers/specs/2026-10-07-main-sequence-design.md` sections 3, 4 and
7), through `daemon.run_once()` as production runs it, on a synthetic session
whose saccades follow a planted main sequence.

**The planted session.** Four calibration trials, as `test_detect_populate.py`
builds them, then two blocks of eight 3 s trials holding 161 horizontal
saccades of 1.2-10 deg. Each is a raised cosine lasting what `PLANTED` gives
its size, drawn one frame at a time from back-to-back holds
(`test_detect_populate.py`'s module docstring says why a single jump will not
do). Trials in condition `contrast-50` are planted 10% faster, so a condition's
gain has something to find. The trial numbers name the conditions through the
generator's rig record (`synth/peripherals.py::rig_condition`): block 2
alternates `contrast-10` and `contrast-50`, about 40 saccades each; block 3
cycles all four, about 20 each.

**Three things a real session can do, planted.** Sixty frames (120 ms) are
dropped from the recording in a hold in block 2, so a saccade's session time
must come from its frame number, not its row. Block 3's sixth trial faults:
no outcome and no line in the rig record, so no condition, though its
saccades still count toward the block. And block 1's `contrast-100` trial is
renamed `Contrast-10` in the rig record, beside its `contrast-10` trial: a
name differing only in case, which MySQL's default collation compares equal
(spec amendment 4).
"""

from __future__ import annotations

import datetime
import json
import math

import numpy as np
import pytest

from tests.identities import new_animal
from tests.schema.test_detect_populate import CAL_SCALE, TRIAL_DURATION_S, _build_mixed_eye_session
from wl_preproc.eye.detect.main_sequence import DEFAULT_MAIN_SEQUENCE_PARAMS, Curve, fit_session

PLANTED = Curve(v_max_deg_s=300.0, saturation_deg=6.0)
#: How much faster `contrast-50`'s saccades are planted.
FASTER = 1.1
_CAL_TRIALS = 4
_BLOCK_TRIALS = 8
# Block 1 (calibration) 1-4; block 2 alternates 0 and 2 mod 4 (`contrast-10`,
# `contrast-50`); block 3 runs through all four.
_TRIAL_NUMBERS = (1, 2, 3, 4, 8, 10, 12, 14, 16, 18, 20, 22, *range(23, 31))
#: Block 3's sixth trial, counted from 1 across the session.
_FAULTED_TRIAL = 18
_DROPPED_FRAMES = 60
#: Renamed in the rig record: block 1's `contrast-100` trial, by number.
_RENAMED_TRIAL = 3
_RENAMED_CONDITION = "Contrast-10"
# The most the fitted curve may stray from the planted one over 2-8 deg. The
# detectors clip each raised cosine's slow tails, so amplitudes come out a
# little short, and the session's own calibration is 4% under `CAL_SCALE`.
# Measured: 1.4-9.2%, the most NSLR's.
_CURVE_TOLERANCE = 0.12


def _condition(trial_number: int) -> str:
    from wl_preproc.synth.peripherals import rig_condition

    return rig_condition(trial_number)[0]


def _rig_name(trial_number: int) -> str:
    """The condition the planted session's rig record names a trial."""
    return _RENAMED_CONDITION if trial_number == _RENAMED_TRIAL else _condition(trial_number)


def _planted_saccades(start_s: float, end_s: float, seed: int) -> list[tuple[float, float, float, float]]:
    """`(onset_s, duration_s, from_x_px, to_x_px)`: horizontal saccades of
    1.2-10 deg, log-uniform, 0.20-0.30 s apart, each turning back toward the
    centre. A raised cosine of amplitude `A` lasting `T` peaks at
    `pi * A / (2 * T)`, so each lasts what puts its peak on `PLANTED`, or
    `FASTER` times it in a `contrast-50` trial."""
    rng = np.random.default_rng(seed)
    out, x_px, onset_s = [], 0.0, start_s + 0.2
    while True:
        amplitude_deg = math.exp(rng.uniform(math.log(1.2), math.log(10.0)))
        trial_number = _TRIAL_NUMBERS[int(onset_s // TRIAL_DURATION_S)]
        peak = float(PLANTED(amplitude_deg)) * (FASTER if _condition(trial_number) == "contrast-50" else 1.0)
        duration_s = math.pi * amplitude_deg / (2.0 * peak)
        if onset_s + duration_s + 0.3 > end_s:
            return out
        step_px = amplitude_deg / CAL_SCALE * (-1.0 if x_px > 0 else 1.0)
        out.append((onset_s, duration_s, x_px, x_px + step_px))
        x_px += step_px
        onset_s += duration_s + rng.uniform(0.20, 0.30)


def _fixations(saccades, start_s: float, end_s: float) -> list:
    """Back-to-back holds: one between saccades, and one per frame through
    each saccade, at its raised cosine's value at the frame's centre."""
    from wl_preproc.synth.ohdpi import OHDPI_FPS
    from wl_preproc.synth.recipe import EyeFixationSpec

    out, cursor = [], start_s
    for onset_s, duration_s, from_px, to_px in saccades:
        out.append(EyeFixationSpec(start_s=cursor, end_s=onset_s, x_px=from_px, y_px=0.0))
        n_frames = max(1, round(duration_s * OHDPI_FPS))
        for k in range(n_frames):
            phase = min((k + 0.5) / OHDPI_FPS / duration_s, 1.0)
            out.append(EyeFixationSpec(
                start_s=onset_s + k / OHDPI_FPS, end_s=onset_s + (k + 1) / OHDPI_FPS,
                x_px=from_px + (to_px - from_px) * (1.0 - math.cos(math.pi * phase)) / 2.0, y_px=0.0))
        cursor = onset_s + n_frames / OHDPI_FPS
    out.append(EyeFixationSpec(start_s=cursor, end_s=end_s, x_px=saccades[-1][3], y_px=0.0))
    return out


def _build_planted_session(tmp_path_factory, session, seed: int):
    """Generate, land and calibrate the planted session, stopping short of
    the daemon. Returns `(session_key, saccades)`."""
    from wl_preproc.contracts.events import TaskTypeCode
    from wl_preproc.schema import core, timebase
    from wl_preproc.synth.recipe import BlockSpec, MontageSpec, SessionRecipe
    from wl_preproc.synth.session import generate_session

    from tests.schema.test_eye_populate import _expected_raw_points, _land, _write_fixations

    from wl_preproc.synth.ohdpi import OHDPI_FPS, OHDPI_PRE_ROLL_S

    n_trials = _CAL_TRIALS + 2 * _BLOCK_TRIALS
    start_s, end_s = _CAL_TRIALS * TRIAL_DURATION_S, n_trials * TRIAL_DURATION_S
    saccades = _planted_saccades(start_s, end_s, seed)
    # The middle of the hold before the first saccade from 18 s, in block 2.
    after = next(index for index, saccade in enumerate(saccades) if saccade[0] >= 18.0)
    hold_s = (saccades[after - 1][0] + saccades[after - 1][1] + saccades[after][0]) / 2.0
    first_dropped = int((hold_s + OHDPI_PRE_ROLL_S) * OHDPI_FPS) - _DROPPED_FRAMES // 2
    recipe = SessionRecipe(
        session_id=session.session_id, subject=session.subject, rig="rig-a", systems=("syncbox", "ohdpi"),
        blocks=tuple(BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=n, trial_duration_s=TRIAL_DURATION_S)
                     for n in (_CAL_TRIALS, _BLOCK_TRIALS, _BLOCK_TRIALS)),
        montages=(MontageSpec(start_s=0.0, end_s=end_s),),
        n_ap_channels=4, ap_sample_rate_hz=30_000.0, seed=seed,
        trial_numbers=_TRIAL_NUMBERS, faulted_trials=(_FAULTED_TRIAL,),
        ohdpi_dropped_frames=tuple(range(first_dropped, first_dropped + _DROPPED_FRAMES)),
        eye_fixations=tuple(_fixations(saccades, start_s, end_s)),
    )
    root = tmp_path_factory.mktemp(f"mainseq{seed}")
    truth = generate_session(root, recipe)
    session_dir = root / recipe.session_id
    record = session_dir / "xcon" / "trials.jsonl"
    lines = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines() if line.strip()]
    for line in lines:
        if line["trial_number"] == _RENAMED_TRIAL:
            line["condition"] = _RENAMED_CONDITION
    record.write_text("".join(json.dumps(line, sort_keys=True) + "\n" for line in lines), encoding="utf-8")
    session_key = _land(root, recipe, session.session_datetime, acquisition_systems=("syncbox", "ohdpi"))
    timebase.SystemTimebase.populate()
    core.Segment.populate()
    segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
    # Calibrated as `test_detect_populate.py::_build_stepped_session` is: an
    # affine against the untouched drift of the first four trials.
    window_starts = [index * TRIAL_DURATION_S + 1.0 for index in range(_CAL_TRIALS)]
    raw_points = _expected_raw_points(session_dir, segment, "Left", [(s + 0.2, s + 0.8) for s in window_starts])
    targets = [(CAL_SCALE * raw[0], CAL_SCALE * raw[1]) for raw in raw_points]
    _write_fixations(session_dir, recipe, truth, list(zip(window_starts, targets, strict=True)))
    return session_key, saccades


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    """Activation only: `daemon.run_once()` registers the paramsets."""
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


@pytest.fixture(scope="module")
def planted_session(daemon_module, prefix, tmp_path_factory):
    session_key, saccades = _build_planted_session(tmp_path_factory, new_animal().session(), seed=1007)
    daemon_module.run_once(prefix=prefix)
    return session_key, saccades


@pytest.fixture(scope="module")
def refused_session(daemon_module, prefix, tmp_path_factory):
    """`test_detect_populate.py`'s mixed-eye session: the left eye's
    calibration refused, so its trace and the both-eyes trace are refused
    for every detector, and the right eye's three planted transitions are far
    too few for a fit."""
    session = new_animal().session()
    session_key, _report, _onsets = _build_mixed_eye_session(
        daemon_module, prefix, tmp_path_factory, dirname="mainseqmixed", session_id=session.session_id,
        subject=session.subject, session_datetime=session.session_datetime, seed=1008, refused_eye="left")
    return session_key


def _default_fit() -> dict:
    from wl_preproc.schema import main_sequence

    return {"fit_paramset_type": "main_sequence",
            "fit_paramset_idx": main_sequence.register_default_paramsets()["default"]}


def _detector_names() -> dict[int, str]:
    from wl_preproc.schema import paramset

    return {row["paramset_idx"]: row["params"]["detector"]
            for row in (paramset.ParamSet & {"paramset_type": "eye_detection"}).to_dicts()}


def _stored_runs(row: dict) -> list[dict]:
    from wl_preproc.schema import detect

    key = {name: row[name] for name in detect.EyeDetection.primary_key}
    return (detect.EyeDetection.Run & key & 'label in ("saccade", "microsaccade")').to_dicts(order_by="run_index")


def test_every_trace_of_every_detection_gets_one_row(planted_session):
    from wl_preproc.schema import detect, main_sequence

    session_key, _saccades = planted_session
    detections = {(row["trace"], row["paramset_idx"]) for row in (detect.EyeDetection & session_key).to_dicts()}
    rows = (main_sequence.SaccadeMainSequence & session_key & _default_fit()).to_dicts()
    assert len(detections) == 21
    assert sorted((row["trace"], row["paramset_idx"]) for row in rows) == sorted(detections)


def test_each_stored_fit_is_the_fit_of_its_traces_selected_runs(planted_session):
    """The selection, the rate and the columns: every stored value is what
    `fit_session` gives the stored runs `selected_runs` picks."""
    from wl_preproc.eye.ohdpi import read_ohdpi
    from wl_preproc.schema import core, ingest, main_sequence

    session_key, _saccades = planted_session
    segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
    session_dir = (ingest.Ingestion & session_key).fetch1("session_dir")
    fs_hz = read_ohdpi(f"{session_dir}/ohdpi/{segment['file_path']}").fs_hz
    rows = (main_sequence.SaccadeMainSequence & session_key & _default_fit()).to_dicts()
    for row in rows:
        assert row["fs_hz"] == fs_hz
        runs = main_sequence.selected_runs(_stored_runs(row), fs_hz, DEFAULT_MAIN_SEQUENCE_PARAMS)
        fit = fit_session([run["amplitude_deg"] for run in runs], [run["peak_velocity_deg_s"] for run in runs],
                          DEFAULT_MAIN_SEQUENCE_PARAMS)
        assert row["fit_status"] == ("refused" if fit.curve is None else "computed")
        assert (row["n_saccades"], row["reason"]) == (fit.n_saccades, fit.reason)
        assert row["amplitude_min_deg"] == pytest.approx(fit.amplitude_min_deg)
        assert row["amplitude_max_deg"] == pytest.approx(fit.amplitude_max_deg)
        if fit.curve is not None:
            assert row["v_max_deg_s"] == pytest.approx(fit.curve.v_max_deg_s)
            assert row["saturation_deg"] == pytest.approx(fit.curve.saturation_deg)
            assert row["v_max_se_deg_s"] == pytest.approx(fit.v_max_se_deg_s)
            assert row["saturation_se_deg"] == pytest.approx(fit.saturation_se_deg)
            assert row["r_squared"] == pytest.approx(fit.r_squared)


def test_a_session_fit_recovers_the_planted_main_sequence(planted_session):
    """Each eye's fit, for every detector that computed one, against the fit
    to the planted saccades themselves: within `_CURVE_TOLERANCE` over 2-8
    deg. Engbert-Kliegl, the baseline, must compute on both eyes."""
    from wl_preproc.schema import main_sequence

    session_key, saccades = planted_session
    amplitude = np.array([abs(to_px - from_px) * CAL_SCALE for _onset, _duration, from_px, to_px in saccades])
    peak = np.pi * amplitude / (2.0 * np.array([duration for _onset, duration, _from, _to in saccades]))
    planted = fit_session(amplitude, peak, DEFAULT_MAIN_SEQUENCE_PARAMS).curve
    grid = np.linspace(2.0, 8.0, 61)
    names = _detector_names()
    computed = {}
    for row in (main_sequence.SaccadeMainSequence & session_key & _default_fit()
                & 'trace in ("left", "right")' & 'fit_status = "computed"').to_dicts():
        fitted = Curve(row["v_max_deg_s"], row["saturation_deg"])
        computed[(names[row["paramset_idx"]], row["trace"])] = float(np.max(np.abs(fitted(grid) / planted(grid) - 1.0)))
    assert {("engbert_kliegl", "left"), ("engbert_kliegl", "right")} <= set(computed)
    assert max(computed.values()) <= _CURVE_TOLERANCE, computed


def test_the_fit_paramset_is_in_the_key(planted_session):
    """A second `main_sequence` paramset writes a row of its own for every
    trace, the old rows untouched. From 2 deg no detector takes more
    saccades, and Engbert-Kliegl takes fewer."""
    import dataclasses

    from wl_preproc.schema import main_sequence, paramset

    session_key, _saccades = planted_session
    second = dataclasses.replace(DEFAULT_MAIN_SEQUENCE_PARAMS, min_amplitude_deg=2.0)
    index = paramset.register("main_sequence", dataclasses.asdict(second))
    second_fit = {"fit_paramset_type": "main_sequence", "fit_paramset_idx": index}
    try:
        before = (main_sequence.SaccadeMainSequence & session_key & _default_fit()).to_dicts()
        main_sequence.SaccadeMainSequence.populate({**session_key, **second_fit})
        rows = (main_sequence.SaccadeMainSequence & session_key & second_fit).to_dicts()
        assert len(rows) == len(before) == 21
        assert (main_sequence.SaccadeMainSequence & session_key & _default_fit()).to_dicts() == before
        by_trace = {(row["trace"], row["paramset_idx"]): row["n_saccades"] for row in before}
        names = _detector_names()
        for row in rows:
            runs = main_sequence.selected_runs(_stored_runs(row), row["fs_hz"], second)
            assert row["n_saccades"] == len(runs) <= by_trace[(row["trace"], row["paramset_idx"])]
            if names[row["paramset_idx"]] == "engbert_kliegl":
                assert row["n_saccades"] < by_trace[(row["trace"], row["paramset_idx"])]
    finally:
        (paramset.ParamSet & {"paramset_type": "main_sequence", "paramset_idx": index}).delete()


def test_a_refused_detection_gets_a_refused_row_quoting_it(refused_session):
    from wl_preproc.schema import detect, main_sequence

    rows = (main_sequence.SaccadeMainSequence & refused_session & _default_fit()
            & 'trace in ("left", "conjunction")').to_dicts()
    assert len(rows) == 14
    for row in rows:
        detection = (detect.EyeDetection & {name: row[name] for name in detect.EyeDetection.primary_key}).fetch1()
        assert detection["status"] == "refused"
        assert (row["fit_status"], row["fs_hz"], row["n_saccades"]) == ("refused", None, 0)
        assert row["reason"] == f"detection refused: {detection['reason']}"[:255]


def test_too_few_saccades_are_refused_with_their_count(refused_session):
    from wl_preproc.schema import main_sequence

    rows = (main_sequence.SaccadeMainSequence & refused_session & _default_fit() & {"trace": "right"}).to_dicts()
    assert len(rows) == 7
    for row in rows:
        runs = main_sequence.selected_runs(_stored_runs(row), row["fs_hz"], DEFAULT_MAIN_SEQUENCE_PARAMS)
        assert row["fit_status"] == "refused"
        assert row["fs_hz"] is not None
        assert row["reason"] == f"{len(runs)} saccades; a session fit needs at least 100"


def _computed_masters(session_key) -> list[dict]:
    from wl_preproc.schema import main_sequence

    return (main_sequence.SaccadeMainSequence & session_key & _default_fit() & 'fit_status = "computed"').to_dicts()


def _master_key(row: dict) -> dict:
    from wl_preproc.schema import main_sequence

    return {name: row[name] for name in main_sequence.SaccadeMainSequence.primary_key}


def _selected_with_starts(session_key, master: dict):
    """The saccades a master's fit took, their session start times, sizes and
    peak speeds."""
    from wl_preproc.eye.ohdpi import read_ohdpi
    from wl_preproc.schema import core, ingest, main_sequence
    from wl_preproc.schema import eye as eye_schema

    segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
    session_dir = (ingest.Ingestion & session_key).fetch1("session_dir")
    recording = read_ohdpi(f"{session_dir}/ohdpi/{segment['file_path']}")
    times = eye_schema.row_session_times(segment, recording.frame_numbers - recording.frame_numbers[0])
    runs = main_sequence.selected_runs(_stored_runs(master), master["fs_hz"], DEFAULT_MAIN_SEQUENCE_PARAMS)
    return (times[[run["run_start"] for run in runs]], np.array([run["amplitude_deg"] for run in runs]),
            np.array([run["peak_velocity_deg_s"] for run in runs]))


def _planted_groups(start_s: np.ndarray):
    """Each block's and each block's conditions' saccades, placed from where
    the recipe put its blocks and trials (back to back from session time 0,
    `synth/timeline.py::build_timeline`) and named from the generator's rig
    record -- not read back from the database. The faulted trial has no
    condition."""
    bounds = [0, _CAL_TRIALS, _CAL_TRIALS + _BLOCK_TRIALS, _CAL_TRIALS + 2 * _BLOCK_TRIALS]
    blocks, conditions = {}, {}
    for block_id in (1, 2, 3):
        first, last = bounds[block_id - 1], bounds[block_id]
        blocks[block_id] = (start_s >= first * TRIAL_DURATION_S) & (start_s < last * TRIAL_DURATION_S)
        for index in range(first, last):
            if index + 1 == _FAULTED_TRIAL:
                continue
            inside = (start_s >= index * TRIAL_DURATION_S) & (start_s < (index + 1) * TRIAL_DURATION_S)
            group = (block_id, _rig_name(_TRIAL_NUMBERS[index]))
            conditions[group] = conditions.get(group, np.zeros(len(start_s), dtype=bool)) | inside
    return blocks, conditions


def test_every_block_gets_a_row_and_one_without_saccades_is_refused(planted_session):
    """Block 1 holds the calibration trials and no planted saccade."""
    from wl_preproc.schema import main_sequence

    session_key, _saccades = planted_session
    masters = _computed_masters(session_key)
    assert len(masters) >= 2
    for master in masters:
        rows = {row["block_id"]: row for row in (main_sequence.SaccadeMainSequence.Block & _master_key(master)).to_dicts()}
        assert sorted(rows) == [1, 2, 3]
        assert {name: rows[1][name] for name in ("gain_status", "n_saccades", "amplitude_min_deg", "gain", "reason")} == {
            "gain_status": "refused", "n_saccades": 0, "amplitude_min_deg": None, "gain": None,
            "reason": "0 saccades; a gain needs at least 30"}


def test_each_gain_is_taken_over_the_saccades_its_block_or_condition_holds(planted_session):
    from wl_preproc.eye.detect.main_sequence import gain
    from wl_preproc.schema import main_sequence

    session_key, _saccades = planted_session
    for master in _computed_masters(session_key):
        start_s, amplitude, peak = _selected_with_starts(session_key, master)
        curve = Curve(master["v_max_deg_s"], master["saturation_deg"])
        blocks, conditions = _planted_groups(start_s)
        stored_blocks = {row["block_id"]: row
                         for row in (main_sequence.SaccadeMainSequence.Block & _master_key(master)).to_dicts()}
        stored_conditions = {(row["block_id"], row["condition"]): row
                             for row in (main_sequence.SaccadeMainSequence.Condition & _master_key(master)).to_dicts()}
        assert set(stored_conditions) == set(conditions)
        for stored, planted in ((stored_blocks, blocks), (stored_conditions, conditions)):
            for group, inside in planted.items():
                expected = gain(amplitude[inside], peak[inside], curve, DEFAULT_MAIN_SEQUENCE_PARAMS)
                row = stored[group]
                assert (row["n_saccades"], row["reason"]) == (expected.n_saccades, expected.reason), group
                assert row["gain"] == pytest.approx(expected.gain), group


def test_a_faster_condition_shows_in_its_gain(planted_session):
    """Block 2's `contrast-50` saccades were planted 10% faster than its
    `contrast-10` ones: measured, 1.095 times. Block 3's four conditions hold
    about 20 saccades each, too few for a gain."""
    from wl_preproc.schema import main_sequence

    session_key, _saccades = planted_session
    names = _detector_names()
    for master in _computed_masters(session_key):
        if names[master["paramset_idx"]] != "engbert_kliegl" or master["trace"] == "conjunction":
            continue
        rows = (main_sequence.SaccadeMainSequence.Condition & _master_key(master)).to_dicts()
        second = {row["condition"]: row["gain"] for row in rows if row["block_id"] == 2}
        assert sorted(second) == ["contrast-10", "contrast-50"]
        assert second["contrast-50"] / second["contrast-10"] == pytest.approx(FASTER, abs=0.03)
        third = [row for row in rows if row["block_id"] == 3]
        assert len(third) == 4
        assert all(row["gain_status"] == "refused" and row["n_saccades"] < 30 for row in third)


def test_condition_names_differing_only_in_case_are_two_conditions(planted_session):
    """`Contrast-10` and `contrast-10` in block 1: two rows, not a duplicate
    key that rolls back the whole fit (the review's finding; amendment 4)."""
    from wl_preproc.schema import main_sequence

    session_key, _saccades = planted_session
    masters = _computed_masters(session_key)
    assert len(masters) >= 2
    for master in masters:
        rows = (main_sequence.SaccadeMainSequence.Condition & _master_key(master) & {"block_id": 1}).to_dicts()
        assert {"Contrast-10", "contrast-10"} <= {row["condition"] for row in rows}
        assert sorted(row["condition_index"] for row in rows) == list(range(1, len(rows) + 1))


def test_a_refused_session_fit_has_no_block_or_condition_rows(refused_session):
    from wl_preproc.schema import main_sequence

    assert len(main_sequence.SaccadeMainSequence & refused_session & 'fit_status = "refused"') == 21
    assert len(main_sequence.SaccadeMainSequence.Block & refused_session) == 0
    assert len(main_sequence.SaccadeMainSequence.Condition & refused_session) == 0


def _planted_events(tmp_path, *, blocks, trials, block_trials, rig, codes=()) -> dict:
    """A bare session holding only what `_groups` reads: `blocks` as
    `(block_id, start_s, stop_s)`, `trials` as `(trial_id, start_s, stop_s)`,
    `block_trials` as `(block_id, trial_id)`, the rig record's names as
    `(trial_number, condition)`, and stream `CONDITION` numbers as
    `(time_s, code)`. Returns the session's key."""
    import json

    from wl_preproc.schema import pipeline

    session = new_animal().session()
    key = session.key
    pipeline.lab.Lab.insert1({"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
                             skip_duplicates=True)
    pipeline.subject.Subject.insert1({"subject": session.subject, "sex": "M", "subject_description": "",
                                      "subject_birth_date": datetime.date(2020, 1, 1)})
    pipeline.Session.insert1(key)
    pipeline.event.BehaviorRecording.insert1(key)
    pipeline.trial.Block.insert([{**key, "block_id": block_id, "block_start_time": start, "block_stop_time": stop}
                                 for block_id, start, stop in blocks], allow_direct_insert=True)
    pipeline.trial.Trial.insert([{**key, "trial_id": trial_id, "trial_start_time": start, "trial_stop_time": stop}
                                 for trial_id, start, stop in trials], allow_direct_insert=True)
    pipeline.trial.BlockTrial.insert([{**key, "block_id": block_id, "trial_id": trial_id}
                                      for block_id, trial_id in block_trials], allow_direct_insert=True)
    pipeline.event.EventType.insert1({"event_type": "CONDITION", "event_type_description": ""}, skip_duplicates=True)
    for time_s, code in codes:
        event = {**key, "event_type": "CONDITION", "event_start_time": time_s}
        pipeline.event.Event.insert1(event, allow_direct_insert=True)
        pipeline.event.Event.Attribute.insert1({**event, "attribute_name": "condition", "attribute_value": code})
    (tmp_path / "xcon").mkdir()
    (tmp_path / "xcon" / "trials.jsonl").write_text("".join(json.dumps(
        {"index": number - 1, "trial_number": number, "subject": session.subject, "outcome": "correct",
         "block": "block", "condition": condition, "params": {}}) + "\n" for number, condition in rig),
        encoding="utf-8")
    return key


def test_placement_reads_times_as_doubles_and_names_by_record_then_stream_code(daemon_module, tmp_path):
    """`_groups` on planted rows, an hour into a session (Review Focus 2, 3
    and 5). `block_start_time` and `trial_start_time` are MySQL `FLOAT`s,
    which read back to six significant digits: 3700.0012 as 3700.00, which
    would put a saccade at 3700.0011 in block 2. Trial 1 is named by the rig
    record; trial 2 by the stream's `CONDITION` number alone; trial 3's name
    is longer than the column, so it has no condition."""
    from wl_preproc.schema import main_sequence

    key = _planted_events(
        tmp_path, blocks=((1, 3600.0, 3700.0012), (2, 3700.0012, 3800.0)),
        trials=((1, 3600.0, 3650.0), (2, 3650.0, 3700.0012), (3, 3700.0012, 3800.0)),
        block_trials=((1, 1), (1, 2), (2, 3)), rig=((1, "rig-name"), (3, "x" * 256)), codes=((3660.0, "7"),))

    start_s = np.array([3610.0, 3655.0, 3700.0011, 3700.0013, 3750.0])
    blocks, conditions = main_sequence._groups(key, tmp_path, start_s)
    assert [(block_id, inside.tolist()) for block_id, inside in blocks] == [
        (1, [True, True, True, False, False]), (2, [False, False, False, True, True])]
    assert [(group, inside.tolist()) for group, inside in conditions] == [
        ((1, "7"), [False, True, True, False, False]), ((1, "rig-name"), [True, False, False, False, False])]


def test_a_condition_holds_only_saccades_inside_its_block(daemon_module, tmp_path):
    """Three paths a real session takes (the main-sequence review's minors;
    amendment 5):
    - trial 1 runs past its block's end, as a trial does whose `TRIAL_END`
      comes after its `BLOCK_END` (`schema/events.py::_trial_stop_time`): a
      saccade in that tail is block 2's, and has no condition;
    - a saccade between two trials counts toward its block only;
    - trial 2 has no `BlockTrial` row, so its saccades have no condition."""
    from wl_preproc.schema import main_sequence

    key = _planted_events(
        tmp_path, blocks=((1, 3600.0, 3700.0), (2, 3700.0, 3800.0)), trials=((1, 3600.0, 3710.0), (2, 3720.0, 3800.0)),
        block_trials=((1, 1),), rig=((1, "rig-name"), (2, "other")))

    start_s = np.array([3650.0, 3705.0, 3715.0, 3750.0])
    blocks, conditions = main_sequence._groups(key, tmp_path, start_s)
    assert [(block_id, inside.tolist()) for block_id, inside in blocks] == [
        (1, [True, False, False, False]), (2, [False, True, True, True])]
    assert [(group, inside.tolist()) for group, inside in conditions] == [
        ((1, "rig-name"), [True, False, False, False])]


_MAIN_SEQUENCE_PROBE = """
import json
import os
import sys
from pathlib import Path

import datajoint as dj

sys.path.insert(0, os.getcwd())

from wl_preproc.schema._compat import apply_datajoint_compat

apply_datajoint_compat()
dj.config["database.host"] = os.environ["WLPP_PROBE_HOST"]
dj.config["database.port"] = int(os.environ["WLPP_PROBE_PORT"])
dj.config["database.user"] = os.environ["WLPP_PROBE_USER"]
dj.config["database.password"] = os.environ["WLPP_PROBE_PASSWORD"]
dj.config["safemode"] = False
dj.logger.setLevel("ERROR")

from wl_preproc import daemon
from wl_preproc.cli.main import main
from wl_preproc.schema import main_sequence

from tests.identities import new_animal
from tests.schema.test_detect_populate import _build_stepped_session


class _Factory:
    def __init__(self, root):
        self.root = Path(root)

    def mktemp(self, name):
        made = self.root / name
        made.mkdir(parents=True, exist_ok=True)
        return made


prefix = os.environ["WLPP_PROBE_PREFIX"]
# Activation only. Nothing here registers a paramset: that is the question.
daemon.activate_all(prefix=prefix)
session = new_animal().session()
_build_stepped_session(
    _Factory(os.environ["WLPP_PROBE_ROOT"]), dirname="probe", session_id=session.session_id,
    subject=session.subject, session_datetime=session.session_datetime, seed=1009,
)
assert main(["daemon", "--prefix", prefix]) == 0
rows = main_sequence.SaccadeMainSequence().to_dicts()
print("PROBE " + json.dumps({"rows": len(rows), "traces": sorted({row["trace"] for row in rows})}))
"""


def test_a_real_wlpp_daemon_pass_writes_main_sequence_rows_registering_nothing_itself(dj_conn, tmp_path):
    """`wlpp daemon`, the real entry point, against a prefix where nothing has
    ever registered a paramset: one row for each trace of each detector. It
    fails if the table is not a daemon stage, runs before the detections, or
    has no paramset registered in production (finding H1's shape)."""
    import json
    import os
    import subprocess
    import sys

    import datajoint as dj

    from wl_preproc.eye.detect.registry import DETECTORS

    result = subprocess.run(
        [sys.executable, "-c", _MAIN_SEQUENCE_PROBE], capture_output=True, text=True,
        env={**os.environ,
             "WLPP_PROBE_HOST": str(dj.config["database.host"]),
             "WLPP_PROBE_PORT": str(dj.config["database.port"]),
             "WLPP_PROBE_USER": str(dj.config["database.user"]),
             "WLPP_PROBE_PASSWORD": str(dj.config["database.password"]),
             "WLPP_PROBE_PREFIX": "ms_",
             "WLPP_PROBE_ROOT": str(tmp_path),
             "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    marker = next((line for line in result.stdout.splitlines() if line.startswith("PROBE ")), None)
    assert marker is not None, f"probe printed no result:\n{result.stdout}\n{result.stderr}"
    assert json.loads(marker[len("PROBE "):]) == {"rows": 3 * len(DETECTORS),
                                                  "traces": ["conjunction", "left", "right"]}
