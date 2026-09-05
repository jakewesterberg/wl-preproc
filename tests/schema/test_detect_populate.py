# tests/schema/test_detect_populate.py
"""`EyeValidity`/`EyeDetection` actually populate: the mask, the per-trace
detection, and the binocular conjunction, run for real over a landed
synthetic session through `daemon.run_once()` -- never `make()` called by
hand (the same discipline `test_eye_populate.py`'s own module docstring
states, for the identical reason: an empty `key_source` would pass every
test that calls `make()` directly while never proving these tables compute
anything in production).

Reuses `tests/schema/test_eye_populate.py`'s own plain helper functions
(`_land`, `_recipe`, `_inject_fixations`, `_row_for_time`,
`_expected_raw_points`, `_write_fixations`), following the precedent
`tests/schema/test_ephys.py` already sets for importing another test
module's helpers directly (`pyproject.toml`'s own `pythonpath = ["."]`
comment: "The test suite imports shared helpers across test files"). Fixtures
themselves are NOT imported -- `degenerate_session`/`fitted_session` etc. are
`@pytest.fixture`-decorated, and pytest fixture objects refuse direct
invocation outside pytest's own resolution machinery (see
`tests/conftest.py::table_snapshot`'s own docstring) -- so this file defines
its own, mirroring the shape rather than the object.

**The planted-step fixture does not reuse `_held_gaze_recipe`'s own hold
shape.** That helper isolates one held target inside a 3 s trial with ~2.3 s
of ordinary two-frequency drift on either side of it -- exactly right for
calibration (design spec's own reasoning: a held window's mean must be a
held value, not a blend of hold and drift), and exactly wrong for a
ground-truth SACCADE ONSET: the drift's own value at the instant a hold
begins is uncontrolled, so the transition into and out of the hold would be
an uncontrolled-amplitude, single-frame jump. Measured directly (this
docstring's numbers come from `_ramp_fixations`'s own construction, verified
against the real `wl_preproc.eye.detect.velocity`/`engbert_kliegl` code
against a real generated file before this file was written): a single-frame
position jump elevates the five-point estimator's velocity over exactly 4
consecutive samples, one short of Engbert-Kliegl's own default 6-sample
floor -- so an instantaneous step is invisible to the detector at defaults,
regardless of amplitude. `stepped_session`'s own detection region is
therefore built from CONTIGUOUS, back-to-back `EyeFixationSpec` entries: a
hold, a short multi-sample RAMP to a new position, another hold, a ramp to a
third position, another hold, and a ramp back to the start -- real, if
brief, transitions, exactly as `_held_gaze_recipe`'s own docstring says a
calibration hold is "the eye stopping", never "jumping".
Calibration itself is `round_trip_session`'s own approach: an affine fit
against the natural, UNTOUCHED two-frequency drift in the earlier part of
the same recording, so the detection region's own held positions never have
to double as calibration targets.
"""

from __future__ import annotations

import dataclasses
import datetime
import os
import time

import numpy as np
import pytest

# Module-level, like `_always`'s import far below (`from wl_preproc.schema.
# detect import _always`, ahead of `test_the_conjunction_inherits_the_
# detectors_own_minimum_duration`) -- though for an unrelated reason, not
# "unlike every other import in this file": most test functions here do
# still import what they need locally. This one cannot, because of this
# file's own `from __future__ import annotations` (above), which makes every
# annotation a STRING (PEP 563). `typing.get_type_hints` -- which
# `schema/detect.py::_params_for` calls on a detector's `run` to find its
# params dataclass -- resolves such a string against the function's OWN
# `__globals__`, which for any function defined in this file is THIS
# module's globals, regardless of how deeply the function is nested inside
# a test body. A fixture detector's `run` needs a `params: EngbertKlieglParams`
# annotation for `_params_for` to build one (test_a_multi_kind_detector_
# populates_and_keeps_its_vocabulary/test_a_multi_kind_detector_writes_all_
# three_traces, below) -- confirmed directly: a local import of the same
# name, inside the test function that defines the annotated callable, left
# it unresolvable (`NameError: name 'EngbertKlieglParams' is not defined`,
# raised from inside `_params_for` and caught as a per-key `EyeDetection`
# error rather than failing the import itself) because a NESTED function's
# `__globals__` is still the ENCLOSING MODULE's namespace, never the
# enclosing function's own locals.
from wl_preproc.eye.detect.engbert_kliegl import EngbertKlieglParams

TRIAL_DURATION_S = 3.0

# The affine's own slope, `degrees = CAL_SCALE * raw_px` (no cross terms, no
# offset) applied uniformly to the whole recording once fitted -- chosen, not
# measured, so the detection region's own degree-space numbers below are
# exact multiples of it rather than something to re-derive per run.
#
# Small enough that the MIDDLE planted transition (below) lands under
# `measure.MICROSACCADE_MAX_DEG`: a genuine microsaccade needs low velocity
# relative to this fixture's own noise floor, but `min_duration_samples`
# forces every detectable event to last at least 12 ms -- so a small enough
# CAL_SCALE is what lets a real, reliably-detected raw displacement still
# translate to a sub-1-degree amplitude, rather than amplitude and
# detectability trading off against each other at any one scale.
CAL_SCALE = 0.005

# Three planted transitions in a row -- big, small, big -- not two: nothing
# in this file's own tests otherwise distinguishes a `saccade` from a
# `microsaccade` specifically, so the brief's own mutation check ("drop the
# `classify` call so every event is a saccade") would be invisible without a
# genuinely sub-threshold event to reclassify. `_STEPS_PX`/`_DURATIONS_S`/
# `_N_SUBSTEPS` are paired by position: step 0 is A->B, step 1 is B->C, step
# 2 is C->A (back to the start).
#
# Measured directly (this file's own dispatch investigation, five different
# seeds, against real generated recordings, before committing to these
# numbers -- see task-7-report.md): amplitudes land at ~2.4, ~0.7 and ~3.2
# degrees respectively (comfortably either side of the 1.0 deg threshold),
# every transition clears Engbert-Kliegl's own default lambda*median-scale
# threshold by 2-5x, and every peak velocity (45-115 deg/s) stays far under
# `ValidityParams.max_speed_deg_s`'s default 1000 deg/s cap.
_STEPS_PX = (500.0, 150.0, 650.0)
_DURATIONS_S = (0.032, 0.016, 0.032)
_N_SUBSTEPS = (16, 8, 16)

# Onset times, relative to trial 4's own start, chosen so every ramp
# (`_DURATIONS_S` above) finishes with room to spare before the trial ends at
# `+3.0 s`: the last ramp starts at `+2.9 s` and ends at `+2.932 s`, leaving a
# short trailing hold rather than running past the trial boundary.
_ONSET_OFFSETS_S = (1.0, 1.9, 2.9)

# A step in the RIGHT eye's own raw trace ALONE, edited directly into the
# generated file after the fact (`test_eye_populate.py::lossy_quality_
# session`'s own technique -- split each affected line by column, replace
# one field, rewrite). Nothing built from `recipe.eye_fixations` can produce
# this: `write_ohdpi` derives the right eye's rotation term from the left's,
# offset by a constant (`RIGHT_EYE_ROTATION_OFFSET_PX`), so a held or ramped
# position in `EyeFixationSpec` always moves BOTH eyes together. Placed in
# trial 3, at `+2.0 s` (session time 11.0 s) -- clear of that trial's own
# calibration window (`[10.2, 10.8)`) and well before trial 4's own planted
# sequence starts at 12.0 s -- so it perturbs neither. Same magnitude and
# duration as the first planted transition (`_STEPS_PX[0]`, verified
# detectable), so a genuine, clean event, not a marginal one: the point is
# to prove the CONJUNCTION excludes it, not to also litigate detectability.
_PHANTOM_ONSET_S = 3 * TRIAL_DURATION_S + 2.0
_PHANTOM_STEP_PX = 500.0
_PHANTOM_N_SUBSTEPS = 16

# A LEFT-eye ramp and a RIGHT-eye ramp, each in that eye's raw trace ALONE
# (the same editing technique as the phantom above, applied twice) and
# planted `_NEAR_MISS_SHIFT_SAMPLES` frames apart. Both eyes therefore detect
# a real event here, and their two detected spans OVERLAP BY FEWER SAMPLES
# THAN EITHER EYE'S OWN DETECTOR WOULD HAVE ACCEPTED -- the one geometry that
# tells `EyeDetection.make()`'s own conjunction duration floor apart from no
# floor at all. Nothing else in this file has it: every planted transition is
# synchronised in both eyes by construction, so every other intersection is
# nearly a whole span, and the phantom above has no left-eye counterpart at
# all, so its intersection is empty at any floor.
#
# Same magnitude, duration and placement rules as the phantom -- trial 3, at
# `+2.0 s`, clear of that trial's calibration window (`[10.2, 10.8)`) and of
# trial 4's planted sequence from 12.0 s.
_NEAR_MISS_ONSET_S = 3 * TRIAL_DURATION_S + 2.0
_NEAR_MISS_STEP_PX = 500.0
_NEAR_MISS_N_SUBSTEPS = 16
# 14, measured on the stored rows rather than chosen: at these ramp
# parameters the detector returns a span of about 17 samples in each eye, so
# a shift of `k` frames leaves an overlap of about `17 - k`. 14 puts the
# measured overlap at 3 -- the middle of the sub-floor range
# `[1, min_duration_samples)`, so a two-sample drift in either direction
# still leaves the fixture saying what it claims.
# `test_a_sub_floor_binocular_overlap_is_no_conjunction_event` ASSERTS that
# range on the stored rows rather than trusting this number, and fails
# naming it if a detector change ever moves the overlap out of it.
_NEAR_MISS_SHIFT_SAMPLES = 14


def _first_row_at(onset_s: float) -> int:
    """The file row an injection at session time `onset_s` starts on.

    `OHDPI_PRE_ROLL_S`/`OHDPI_FPS` read off `wl_preproc.synth.ohdpi` rather
    than written out as 0.6 and 500.0, so the row an injector writes and the
    row a test looks for it at cannot drift apart from the writer that
    produced the file.
    """
    from wl_preproc.synth.ohdpi import OHDPI_FPS, OHDPI_PRE_ROLL_S

    return round((onset_s + OHDPI_PRE_ROLL_S) * OHDPI_FPS)


def _inject_single_eye_ramps(session_dir, ramps) -> None:
    """Overwrite one Purkinje column per `(column, row_start, step_px,
    n_substeps)` entry of `ramps`, directly in the generated ohDPI `.txt`.

    `test_eye_populate.py::lossy_quality_session`'s own technique -- split
    each affected line by column, replace one field, rewrite. Nothing built
    from `recipe.eye_fixations` can produce a SINGLE-eye event: `write_ohdpi`
    derives the right eye's rotation term from the left's, offset by a
    constant (`RIGHT_EYE_ROTATION_OFFSET_PX`), so a held or ramped position
    in `EyeFixationSpec` always moves both eyes together.

    Every ramp is applied in ONE pass over the file, rather than one pass
    each: `_inject_near_miss_pair` below plants two, and re-reading and
    re-writing a whole recording per ramp would double a fixture's own cost
    for nothing.
    """
    from wl_preproc.synth.ohdpi import HEADER

    (ohdpi_txt,) = (session_dir / "ohdpi").glob("*.txt")
    lines = ohdpi_txt.read_text(encoding="utf-8").splitlines()
    header_line, data_lines = lines[0], lines[1:]

    for column_name, row_start, step_px, n_substeps in ramps:
        column = HEADER.index(column_name)
        for offset in range(n_substeps):
            frac = (offset + 0.5) / n_substeps
            fields = data_lines[row_start + offset].split(" ")
            fields[column] = f"{step_px * frac:.4f}"
            data_lines[row_start + offset] = " ".join(fields)

    ohdpi_txt.write_text("\n".join([header_line, *data_lines]) + "\n", encoding="utf-8")


def _inject_right_eye_only_step(session_dir) -> None:
    """See `_PHANTOM_ONSET_S`'s own comment: a step in `RightCR4X` alone,
    leaving every `Left*` column untouched, so the resulting event exists in
    the right eye's own detected spans and nowhere in the left's. Engbert &
    Kliegl's own binocular criterion -- the intersection, never the union --
    must exclude it from the conjunction; `test_the_conjunction_requires_
    temporal_overlap_in_both_eyes` is the one test in this file a UNION
    mutation cannot pass with this phantom event present, since every OTHER
    planted transition is synchronised in both eyes by construction and
    cannot, on its own, tell a union from an intersection."""
    _inject_single_eye_ramps(
        session_dir,
        [("RightCR4X", _first_row_at(_PHANTOM_ONSET_S), _PHANTOM_STEP_PX,
          _PHANTOM_N_SUBSTEPS)],
    )


def _inject_near_miss_pair(session_dir) -> None:
    """See `_NEAR_MISS_ONSET_S`'s own comment: one ramp in `LeftCR4X` and one
    in `RightCR4X`, the second starting `_NEAR_MISS_SHIFT_SAMPLES` frames
    later, so each eye detects a real event and the two overlap by only a
    few samples.

    Two SEPARATE single-eye injections rather than one shifted pair of held
    positions, for the reason `_inject_single_eye_ramps` states: anything
    built from `recipe.eye_fixations` moves both eyes together, so a
    recipe cannot express two eyes doing different things at the same
    instant at all."""
    left_row = _first_row_at(_NEAR_MISS_ONSET_S)
    _inject_single_eye_ramps(session_dir, [
        ("LeftCR4X", left_row, _NEAR_MISS_STEP_PX, _NEAR_MISS_N_SUBSTEPS),
        ("RightCR4X", left_row + _NEAR_MISS_SHIFT_SAMPLES, _NEAR_MISS_STEP_PX,
         _NEAR_MISS_N_SUBSTEPS),
    ])


def _ramp_fixations(start_s, from_xy, to_xy, dur_s, n_substeps):
    """`n_substeps` consecutive, back-to-back one-frame holds approximating a
    linear ramp from `from_xy` to `to_xy` -- see this module's own docstring
    for why a single instantaneous jump will not do."""
    from wl_preproc.synth.recipe import EyeFixationSpec

    step_dur = dur_s / n_substeps
    fixations = []
    for index in range(n_substeps):
        frac = (index + 0.5) / n_substeps
        x_px = from_xy[0] + (to_xy[0] - from_xy[0]) * frac
        y_px = from_xy[1] + (to_xy[1] - from_xy[1]) * frac
        fixations.append(
            EyeFixationSpec(
                start_s=start_s + index * step_dur,
                end_s=start_s + (index + 1) * step_dur,
                x_px=x_px,
                y_px=y_px,
            )
        )
    return fixations


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    """Mirrors `test_eye_populate.py`'s own fixture of the same name (module
    fixtures resolve per REQUESTING file, not per definition site -- each
    test file that wants one defines its own, the same way `test_eye_
    populate.py` is this project's only other definition today).

    Also registers this task's own default paramsets, exactly once: `Eye
    Validity`/`EyeDetection`'s `key_source`s (below) both join against
    registered `paramset.ParamSet` rows, so with nothing registered neither
    key_source ever names a candidate session at all -- a silent, empty
    no-op rather than an error, and the trap `test_eye_populate.py`'s own
    module docstring names for an empty `key_source`.

    **Still needed even though `daemon.run_once()` now registers these
    itself** (whole-branch review, finding H1 -- production had no such call
    at all, and this fixture is why nothing here noticed). Do not delete it:
    `out_of_order_session` below reaches `EyeValidity.populate()` WITHOUT a
    preceding `run_once()`, deliberately, and would silently find no
    candidate at all if this were the only place registration happened.
    `daemon.register_default_paramsets` is where production's own claim is
    tested, by a subprocess that registers nothing itself.
    """
    from wl_preproc import daemon
    from wl_preproc.schema import detect

    daemon.activate_all(prefix=prefix)
    detect.register_default_paramsets()
    return daemon


def _build_stepped_session(
    tmp_path_factory, *, dirname, session_id, subject, session_datetime, seed,
    after_generate=None,
):
    """The construction behind `stepped_session`, and -- without
    `after_generate` -- behind the mixed-eye fixtures below (`left_refused_
    session`/`right_refused_session`): a hold, a ramp, a hold, a ramp, a
    hold, a ramp, a hold -- three planted, known-onset transitions in an
    otherwise still trace, following a calibration region built exactly
    like `test_eye_populate.py::round_trip_session` (a fitted affine
    against the ordinary, untouched two-frequency drift).

    Five trials of `TRIAL_DURATION_S` each. Trials 0-3 supply calibration
    windows (natural drift, no `eye_fixations` override, matching `round_trip
    _session`'s own construction so calibration success is proven behaviour
    rather than a fresh guess). Trial 4 is reserved entirely for detection:
    held at A, ramped to B (big), held at B, ramped to C (small -- a genuine
    microsaccade), held at C, ramped back to A (big) -- three transitions,
    this fixture's three planted onsets.

    **Stops SHORT of running the daemon.** `stepped_session` runs it
    immediately below, having nothing to intervene on; `left_refused_
    session`/`right_refused_session` need `EyeCalibration` to compute for
    BOTH eyes first, then one eye's row replaced with a refused one, before
    `EyeValidity`/`EyeDetection` ever see this session -- see those
    fixtures' own docstrings for why a single `daemon.run_once()` call
    cannot do both (`_computed_tables()`'s own list runs every stage
    back-to-back in one call, with no pause in between to intervene).

    `after_generate`, if given, runs on the fresh `session_dir` right after
    `generate_session` and before landing -- `stepped_session`'s own
    `_inject_right_eye_only_step` hook. Kept OUT of this shared function by
    default: the phantom event it plants is specific to that one fixture's
    own conjunction test and would otherwise surface as an unplanned fourth
    event in whichever eye survives a mixed-eye fixture built from here.

    Returns `(session_key, segment, onset_times)`.
    """
    from wl_preproc.contracts.events import TaskTypeCode
    from wl_preproc.schema import core, timebase
    from wl_preproc.synth.recipe import BlockSpec, EyeFixationSpec, MontageSpec, SessionRecipe
    from wl_preproc.synth.session import generate_session

    from tests.schema.test_eye_populate import _expected_raw_points, _land, _write_fixations

    n_cal_trials = 4
    n_trials = n_cal_trials + 1
    detect_trial_start = n_cal_trials * TRIAL_DURATION_S

    # A -> B -> C -> A, all on the X axis alone (Y stays 0 throughout).
    positions = [(0.0, 0.0)]
    for step in _STEPS_PX:
        positions.append((positions[-1][0] + step, positions[-1][1]))
    positions[-1] = positions[0]  # closes the loop: C -> A returns exactly to A

    onset_times = [detect_trial_start + offset for offset in _ONSET_OFFSETS_S]

    detect_fixations: list = []
    cursor_s = detect_trial_start
    for index, (start_s, duration_s, n_substeps) in enumerate(
        zip(onset_times, _DURATIONS_S, _N_SUBSTEPS, strict=True)
    ):
        from_xy, to_xy = positions[index], positions[index + 1]
        # Hold at `from_xy` until this transition's own onset, then ramp --
        # CONTIGUOUS throughout: each entry's `end_s` equals the next one's
        # `start_s`, so there is no gap for the ordinary drift to reappear in
        # (this module's own docstring on why `_held_gaze_recipe`'s own
        # trial-isolated shape will not do).
        detect_fixations.append(
            EyeFixationSpec(start_s=cursor_s, end_s=start_s, x_px=from_xy[0], y_px=from_xy[1])
        )
        detect_fixations.extend(_ramp_fixations(start_s, from_xy, to_xy, duration_s, n_substeps))
        cursor_s = start_s + duration_s
    detect_fixations.append(
        EyeFixationSpec(
            start_s=cursor_s, end_s=n_trials * TRIAL_DURATION_S,
            x_px=positions[-1][0], y_px=positions[-1][1],
        )
    )

    recipe = SessionRecipe(
        session_id=session_id,
        subject=subject,
        rig="rig-a",
        systems=("syncbox", "ohdpi"),
        blocks=(
            BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=n_trials, trial_duration_s=TRIAL_DURATION_S),
        ),
        montages=(MontageSpec(start_s=0.0, end_s=n_trials * TRIAL_DURATION_S),),
        n_ap_channels=4,
        ap_sample_rate_hz=30_000.0,
        seed=seed,
        eye_fixations=tuple(detect_fixations),
    )

    root = tmp_path_factory.mktemp(dirname)
    truth = generate_session(root, recipe)
    session_dir = root / recipe.session_id
    if after_generate is not None:
        after_generate(session_dir)
    session_key = _land(
        root, recipe, session_datetime,
        acquisition_systems=("syncbox", "ohdpi"),
    )

    # Align FIRST -- exactly `round_trip_session`'s own ordering -- so
    # calibration targets and the planted onsets' expected rows are both
    # resolved through the segment's OWN real fit rather than assumed.
    timebase.SystemTimebase.populate()
    core.Segment.populate()
    segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()

    window_starts = [index * TRIAL_DURATION_S + 1.0 for index in range(n_cal_trials)]
    fixation_windows = [(start + 0.2, start + 0.8) for start in window_starts]
    raw_points = _expected_raw_points(session_dir, segment, "Left", fixation_windows)
    targets = [(CAL_SCALE * raw[0], CAL_SCALE * raw[1]) for raw in raw_points]
    _write_fixations(session_dir, recipe, truth, list(zip(window_starts, targets, strict=True)))

    return session_key, segment, onset_times


@pytest.fixture(scope="module")
def stepped_session(daemon_module, prefix, tmp_path_factory):
    """`_build_stepped_session`'s own construction, with the phantom
    right-eye-only step planted before landing (`_inject_right_eye_only_
    step`, needed only by this fixture's own `test_the_conjunction_requires_
    temporal_overlap_in_both_eyes`) and the daemon run immediately after --
    this fixture has nothing to intervene on between `EyeCalibration` and
    `EyeValidity`/`EyeDetection`, unlike `left_refused_session`/`right_
    refused_session` below.
    """
    from tests.schema.test_eye_populate import _row_for_time

    session_key, segment, onset_times = _build_stepped_session(
        tmp_path_factory,
        dirname="detectstep", session_id="2027-06-01_01", subject="detstep1",
        session_datetime=datetime.datetime(2027, 6, 1, 9, 0), seed=601,
        after_generate=_inject_right_eye_only_step,
    )

    report = daemon_module.run_once(prefix=prefix)

    planted_onsets = [_row_for_time(segment, onset_s) for onset_s in onset_times]
    return session_key, report, planted_onsets


@pytest.fixture(scope="module")
def near_miss_session(daemon_module, prefix, tmp_path_factory):
    """`_build_stepped_session`'s own construction with the near-miss pair
    planted instead of the phantom (`_inject_near_miss_pair`), and the daemon
    run immediately after -- the same shape as `stepped_session`, differing
    only in what is injected.

    **A separate session rather than a fourth injection into
    `stepped_session`.** The left-eye half of the pair is a FOURTH event in
    that session's left trace, and two of its tests assert exactly three by
    count (`test_a_planted_step_is_detected_at_its_planted_time`,
    `test_the_middle_planted_step_is_classified_as_a_microsaccade`) --
    deliberately, since a count is what pins the planted onsets to the
    detected ones. Weakening those to accommodate a fixture whose whole
    subject is a different table's floor would trade a strong assertion for
    a weaker one; a second session costs one more `run_once()` and keeps
    both.
    """
    session_key, _segment, _onset_times = _build_stepped_session(
        tmp_path_factory,
        # `subject` is `varchar(8)` (element-animal's own limit, restated at
        # `wl_preproc/schema/ingest.py`) -- "detnear1" is exactly 8.
        dirname="detectnear", session_id="2027-06-06_01", subject="detnear1",
        session_datetime=datetime.datetime(2027, 6, 6, 9, 0), seed=606,
        after_generate=_inject_near_miss_pair,
    )

    report = daemon_module.run_once(prefix=prefix)
    return session_key, report


def _build_mixed_eye_session(
    daemon_module, prefix, tmp_path_factory, *,
    dirname, session_id, subject, session_datetime, seed, refused_eye,
):
    """Finding 1's own required construction: "land the session, let both
    eyes calibrate, then replace one eye's `EyeCalibration` row with a
    refused one before `EyeDetection` populates" -- built on top of
    `_build_stepped_session` (no phantom step: irrelevant here, and it
    would otherwise read as an unplanned fourth event in whichever eye
    survives).

    **Why two `run_once()` calls, with a delete in between, rather than
    populating `EyeCalibration` alone first.** `EyeCalibration.key_source`
    requires `pipeline.event.BehaviorRecording` -- assembled only by
    `daemon._populate_event_stage()`, which `run_once()` runs internally
    before its own `_computed_tables()` loop even starts. Calling
    `eye_schema.EyeCalibration.populate()` directly, the way `stepped_
    session` calls `core.Segment.populate()` directly, would therefore find
    no candidate at all before that stage has run. So this calls
    `run_once()` ONCE, letting the WHOLE pipeline compute normally from a
    genuinely, symmetrically fully-calibrated session (`EyeValidity`/
    `EyeDetection` included); patches `refused_eye`'s `EyeCalibration` row
    in place (`update1`, mirroring exactly the shape `EyeCalibration.
    make()`'s own refused-row branches write, via that module's own
    `_coefficient_columns(None)`); deletes the now-stale `EyeValidity`/
    `EyeDetection` rows for this session so their keys read as pending
    again (bare `.delete()` -- safe here: safemode is off for this whole
    suite, per `tests/schema/test_eye_populate.py`'s own `no_ohdpi_
    acquisition_system_session` fixture, and neither table has any
    dependent of its own beside its own `Run` part, so the cascade reaches
    no further than intended); and calls `run_once()` a SECOND time.
    `EyeCalibration`/`EyeQuality`'s keys are already populated by then and
    stay untouched -- ordinary DataJoint `.populate()` behaviour, not
    special-cased here -- while `EyeValidity`/`EyeDetection` recompute
    fresh, this time reading the genuinely refused calibration.

    `EyeCalibration.BlockResidual` rows from the eye's original real fit
    are deliberately left in place rather than also deleted: nothing this
    fixture or `EyeDetection.make()` reads touches that part table, and
    scrubbing it would only be cosmetic.

    Returns `(session_key, report, onset_times)` -- `report` from the
    SECOND `run_once()` call, the one whose errors this fixture's own
    callers actually care about.
    """
    from tests.schema.test_eye_populate import _row_for_time
    from wl_preproc.schema import detect, eye as eye_schema
    from wl_preproc.schema.eye import _coefficient_columns

    session_key, segment, onset_times = _build_stepped_session(
        tmp_path_factory,
        dirname=dirname, session_id=session_id, subject=subject,
        session_datetime=session_datetime, seed=seed,
    )
    daemon_module.run_once(prefix=prefix)

    eye_schema.EyeCalibration.update1({
        **session_key, "eye": refused_eye,
        "calibration_source": "refused",
        "calibration_model": None,
        **_coefficient_columns(None),
        "validation_error_deg": None,
        "conditioning": None,
        "conditioning_second_order": None,
        "residual_deg_rms": None,
        "residual_deg_max": None,
        "carried_from_session_datetime": None,
        "reason": (
            f"test fixture: {refused_eye} eye's real fitted calibration "
            "replaced with a refused row"
        ),
    })
    (detect.EyeValidity & session_key).delete()
    (detect.EyeDetection & session_key).delete()

    report = daemon_module.run_once(prefix=prefix)
    onsets = [_row_for_time(segment, onset_s) for onset_s in onset_times]
    return session_key, report, onsets


@pytest.fixture(scope="module")
def left_refused_session(daemon_module, prefix, tmp_path_factory):
    """`_build_mixed_eye_session`'s own construction, LEFT refused: Finding
    1's own table, row 2 (`refused | computed`)."""
    return _build_mixed_eye_session(
        daemon_module, prefix, tmp_path_factory,
        # `subject` is `varchar(8)` (element-animal's own limit, restated at
        # `wl_preproc/schema/ingest.py`'s own comment) -- "detmixl1" is
        # exactly 8 characters; "detrightleft1"-style spelled-out names are
        # not an option here.
        dirname="detectleft", session_id="2027-06-03_01", subject="detmixl1",
        session_datetime=datetime.datetime(2027, 6, 3, 9, 0), seed=603,
        refused_eye="left",
    )


@pytest.fixture(scope="module")
def right_refused_session(daemon_module, prefix, tmp_path_factory):
    """`_build_mixed_eye_session`'s own construction, RIGHT refused: Finding
    1's own table, row 3 (`computed | refused`) -- the mirror direction of
    `left_refused_session`, because a fix that hardcodes an eye passes one
    direction and fails the other."""
    return _build_mixed_eye_session(
        daemon_module, prefix, tmp_path_factory,
        dirname="detectright", session_id="2027-06-04_01", subject="detmixr1",
        session_datetime=datetime.datetime(2027, 6, 4, 9, 0), seed=604,
        refused_eye="right",
    )


@pytest.fixture(scope="module")
def out_of_order_session(daemon_module, prefix, tmp_path_factory):
    """Finding M5's own reproduction: `EyeValidity` reached BEFORE the event
    stage has run, which is the state of any session whose event decode
    failed while its barcode alignment succeeded.

    `_build_stepped_session` stops exactly there -- it populates
    `SystemTimebase` and `core.Segment` and nothing else -- so the broken
    order needs only one extra `EyeValidity.populate()` call, with no
    `daemon.run_once()` in between to assemble events. `EyeCalibration.
    key_source` requires `pipeline.event.BehaviorRecording`, so at this
    point that table cannot name this session at all while `EyeValidity`'s
    own `pipeline.Session & Ingestion & Segment(ohdpi)` restriction is
    already satisfied.

    Returns `(session_key, rows written out of order, report from the full
    pass that follows, planted onsets)`.
    """
    from tests.schema.test_eye_populate import _row_for_time
    from wl_preproc.schema import detect

    session_key, segment, onset_times = _build_stepped_session(
        tmp_path_factory,
        # `subject` is `varchar(8)` -- "detordr1" is exactly 8 characters.
        dirname="detectorder", session_id="2027-06-05_01", subject="detordr1",
        session_datetime=datetime.datetime(2027, 6, 5, 9, 0), seed=605,
    )

    detect.EyeValidity.populate()
    out_of_order_rows = (detect.EyeValidity & session_key).to_dicts()

    report = daemon_module.run_once(prefix=prefix)
    onsets = [_row_for_time(segment, onset_s) for onset_s in onset_times]
    return session_key, out_of_order_rows, report, onsets


def test_populating_out_of_order_writes_nothing_rather_than_a_false_refusal(
    out_of_order_session,
):
    """Finding M5. Before the fix, `EyeValidity.key_source` was satisfied by
    `Session & Ingestion & Segment(ohdpi)` alone, so reaching it before the
    event stage found no `EyeCalibration` row, took `make()`'s
    `_map_from_row(...) is None` branch, and wrote BOTH eyes refused with
    "no usable calibration, so gaze is undefined". That row is permanent --
    DataJoint never recomputes a populated key -- so on the next pass both
    eyes calibrated successfully and three `EyeDetection` traces stayed
    refused forever with a reason that had become false, while `run_once`
    reported no error at all.

    Requiring `eye.EyeCalibration` to have RUN (not to have succeeded) makes
    that state unreachable: the session simply stays outstanding until the
    stage it depends on has produced its rows.
    """
    _session_key, out_of_order_rows, _report, _onsets = out_of_order_session

    assert out_of_order_rows == [], (
        "EyeValidity populated before EyeCalibration could run, and wrote a "
        "PERMANENT refusal that the very next pass makes false: "
        f"{[(row['eye'], row['status'], row['reason']) for row in out_of_order_rows]}"
    )


def test_the_next_full_pass_then_computes_both_eyes(out_of_order_session):
    """The other half of M5: staying outstanding must not mean staying
    outstanding forever. One ordinary `run_once()` -- which assembles
    events, calibrates, and only then reaches this table -- computes both
    eyes and all three traces, with the planted onsets intact.
    """
    from wl_preproc.schema import detect

    session_key, _out_of_order_rows, report, onsets = out_of_order_session
    assert not any(
        "EyeValidity" in message or "EyeDetection" in message for message in report["errors"]
    )

    for eye_value in ("left", "right"):
        assert (detect.EyeValidity & {**session_key, "eye": eye_value}).fetch1(
            "status"
        ) == "computed"
    for name in _detector_names():
        for trace in ("left", "right", "conjunction"):
            assert (
                detect.EyeDetection & {**session_key, "trace": trace, **_detector(name)}
            ).fetch1("status") == "computed", (name, trace)

    # Not merely "a row exists": the same planted-onset round trip
    # `stepped_session` asserts, so a session that recovered from the
    # out-of-order state is proven to have detected the real events rather
    # than to have written an empty success.
    for name in _detector_names():
        runs = (
            detect.EyeDetection.Run & {**session_key, "trace": "left", **_detector(name)}
        ).to_dicts(order_by="run_index")
        detected = [r["run_start"] for r in runs if r["label"] in ("saccade", "microsaccade")]
        assert len(detected) == len(onsets) == 3, name
        for got, want in zip(detected, onsets, strict=True):
            assert abs(got - want) <= 5, name


@pytest.fixture(scope="module")
def uncalibrated_session(daemon_module, prefix, tmp_path_factory):
    """Both eyes' calibration REFUSED -- `degenerate_session`'s own shape
    (four coincident targets, conditioning exactly 0.0, no `.bhv2` and no
    earlier same-day session to fall back to), reproduced here rather than
    imported: `degenerate_session` is a fixture, not a plain function, and
    pytest fixtures cannot be called directly (see this module's own
    docstring)."""
    from tests.schema.test_eye_populate import N_TRIALS, _inject_fixations, _land, _recipe
    from wl_preproc.synth.session import generate_session

    root = tmp_path_factory.mktemp("detectuncal")
    recipe = _recipe("2027-06-02_01", "detuncl1", seed=602, include_ohdpi=True)
    truth = generate_session(root, recipe)
    targets = [(0.0, 0.0)] * N_TRIALS
    _inject_fixations(root / recipe.session_id, recipe, truth, targets)
    session_key = _land(
        root, recipe, datetime.datetime(2027, 6, 2, 9, 0),
        acquisition_systems=("syncbox", "ohdpi"),
    )
    report = daemon_module.run_once(prefix=prefix)
    return session_key, report


def _detector(name):
    """The restriction that selects ONE detector's rows.

    **Every query below needed this the moment a second detector was
    registered** (Otero-Millan, 2026-09-02). `EyeDetection`'s `key_source`
    crosses each validity row with every `eye_detection` paramset, so one
    session now populates three traces per DETECTOR: `fetch1()` on a
    `{session, trace}` restriction stopped finding exactly one row, and a
    count of detected events over such a restriction doubled. Neither is a
    defect -- it is the completeness claim working -- but a test that means
    "what Engbert-Kliegl found here" has to say so.

    Reached through `register_default_paramsets`, which is idempotent by
    content hash, rather than by assuming an index: the paramset a detector
    owns is whatever that function registered for it, and hardcoding 0 or 1
    would make these tests depend on `DETECTORS`' insertion order.
    """
    from wl_preproc.schema import detect

    return {"paramset_idx": detect.register_default_paramsets()[name]}


def _detector_names():
    """Every registered detector, for the invariants that hold across all of
    them. Reading `DETECTORS` rather than naming two keeps these tests
    covering detector seven on the day it lands."""
    from wl_preproc.eye.detect.registry import DETECTORS

    return sorted(DETECTORS)


def test_a_planted_step_is_detected_at_its_planted_time(stepped_session):
    """The round-trip that matters. `SessionRecipe.eye_fixations` holds gaze
    at stated raw positions, so a hold-step-hold session has ground-truth
    onsets, and a detector that found events at the wrong times passes every
    count-based test and fails this one.

    **Asserted for EVERY registered detector, not just the baseline.** The
    fixture's ground truth is a property of the session, not of a method, so
    a detector that cannot find three planted steps in an otherwise-still
    trace is broken whichever one it is -- and this is the cheapest place a
    new detector's reimplementation defect surfaces as a defect rather than
    as design spec section 3.2's "genuine detector disagreement".
    """
    from wl_preproc.schema import detect

    session_key, _report, planted_onsets = stepped_session
    for name in _detector_names():
        runs = (
            detect.EyeDetection.Run & {**session_key, "trace": "left", **_detector(name)}
        ).to_dicts(order_by="run_index")
        onsets = [r["run_start"] for r in runs if r["label"] in ("saccade", "microsaccade")]

        assert len(onsets) == len(planted_onsets), name
        for got, want in zip(onsets, planted_onsets, strict=True):
            assert abs(got - want) <= 5, name


def test_the_baseline_detector_finds_the_planted_steps(stepped_session):
    """`test_a_planted_step_is_detected_at_its_planted_time`, pinned to the
    one detector whose numbers design spec section 5 actually measured. Kept
    separate so a failure names which claim broke: the shared ground truth, or
    Engbert-Kliegl specifically."""
    from wl_preproc.schema import detect

    session_key, _report, planted_onsets = stepped_session
    runs = (
        detect.EyeDetection.Run
        & {**session_key, "trace": "left", **_detector("engbert_kliegl")}
    ).to_dicts(order_by="run_index")
    onsets = [r["run_start"] for r in runs if r["label"] in ("saccade", "microsaccade")]

    assert len(onsets) == len(planted_onsets)
    for got, want in zip(onsets, planted_onsets, strict=True):
        assert abs(got - want) <= 5


def test_the_runs_tile_the_whole_trace(stepped_session):
    """The structural invariant, asserted on real populated rows and not
    only in the encoder's unit tests -- on every trace of every fixture
    (spec section 10), not `left` alone (fix round, reviewer finding 2).
    `conjunction` is the trace that most needs this checked: its run
    boundaries come from `_overlapping`'s intersection of the two eyes'
    spans, not straight from a detector, so it is the one trace where
    `runs_from_labels` re-tiling the result correctly is least obvious.

    **Every trace of every DETECTOR.** The tiling invariant is a property of
    the storage encoding, not of any one method, so a second detector whose
    intervals overlapped -- which `runs_from_labels` cannot express and
    `_insert_trace` would silently resolve by last-write-wins -- has to break
    this here rather than three tables downstream."""
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session
    for name in _detector_names():
        for trace in ("left", "right", "conjunction"):
            where = {**session_key, "trace": trace, **_detector(name)}
            row = (detect.EyeDetection & where).fetch1()
            runs = (detect.EyeDetection.Run & where).to_dicts(order_by="run_index")

            assert runs[0]["run_start"] == 0, (name, trace)
            assert runs[-1]["run_stop"] == row["n_samples"], (name, trace)
            for earlier, later in zip(runs, runs[1:], strict=False):
                assert earlier["run_stop"] == later["run_start"], (name, trace)
                assert earlier["label"] != later["label"], (name, trace)


_VALIDITY_FRACTION_COLUMNS = (
    "frac_blink", "frac_out_of_region", "frac_too_fast",
    "frac_frame_gap", "frac_short_epoch",
)


def test_every_per_criterion_fraction_reaches_the_master_row(stepped_session):
    """Finding M6. Design spec section 7 asks `EyeValidity` for
    "per-criterion rejected fractions", plural; four of the five shipped as
    hardcoded `None` under a comment calling them "not separately
    recoverable", though `validity_labels` computed all five as named locals
    and simply did not return them.

    `tests/eye/detect/test_validity.py` is where each fraction is pinned to
    its own criterion, one planted criterion at a time. What this test adds
    is the half that runs through a real database: five populated columns on
    a really-populated row, for both eyes, with `frac_blink` checked against
    the stored RUNS rather than against itself. That cross-check is exact
    and not approximate-by-luck: `Label.BLINK` is assigned last
    (`MASK_PRECEDENCE`), so the stored `blink` runs cover exactly the raw
    blink criterion's samples and nothing else.
    """
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session
    for eye_value in ("left", "right"):
        row = (detect.EyeValidity & {**session_key, "eye": eye_value}).fetch1()
        assert row["status"] == "computed"

        for column in _VALIDITY_FRACTION_COLUMNS:
            assert row[column] is not None, f"{eye_value}: {column} still NULL"
            assert 0.0 <= row[column] <= 1.0, f"{eye_value}: {column} = {row[column]}"

        blink_samples = sum(
            run["run_stop"] - run["run_start"]
            for run in (detect.EyeValidity.Run & {**session_key, "eye": eye_value}).to_dicts()
            if run["label"] == "blink"
        )
        assert row["frac_blink"] == pytest.approx(blink_samples / row["n_samples"])


def test_a_refused_mask_leaves_every_fraction_null(uncalibrated_session):
    """The other half of the column comment: `NULL` on a refused row, and
    only there. A refused row has no mask at all, so there is no criterion
    to attribute anything to -- and `0.0` would read as "this criterion
    rejected nothing", which is a measurement this row never made."""
    from wl_preproc.schema import detect

    session_key, _report = uncalibrated_session
    for eye_value in ("left", "right"):
        row = (detect.EyeValidity & {**session_key, "eye": eye_value}).fetch1()
        assert row["status"] == "refused"
        for column in _VALIDITY_FRACTION_COLUMNS:
            assert row[column] is None, f"{eye_value}: refused row has {column} populated"


def test_saccade_runs_carry_measurements_and_others_do_not(stepped_session):
    """A saccade or microsaccade run IS an event and carries its own
    measurements; every other label leaves them NULL (design spec section 5).
    Asserted per detector so a failure names which one wrote the odd row."""
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session
    for name in _detector_names():
        where = {**session_key, "trace": "left", **_detector(name)}
        for run in (detect.EyeDetection.Run & where).to_dicts():
            if run["label"] in ("saccade", "microsaccade"):
                assert run["amplitude_deg"] is not None, name
                assert run["peak_velocity_deg_s"] is not None, name
            else:
                assert run["amplitude_deg"] is None, name


def test_reliability_survives_the_run_re_derivation(stepped_session):
    """`_insert_trace` re-derives runs with `runs_from_labels` rather than
    trusting detector spans, so a per-detection value attached upstream is easy
    to lose silently -- the stored column would simply stay null, which looks
    exactly like a detector having nothing to say.

    Otero-Millan is the one detector that has something to say here (design
    spec section 5 reserved the column for it), so this reads ITS rows. The
    companion assertion below -- that Engbert-Kliegl's stay null -- is what
    makes a non-null value evidence of the carry rather than of a default
    someone changed.
    """
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session
    rows = (
        detect.EyeDetection.Run
        & {**session_key, "trace": "left", **_detector("otero_millan")}
    ).to_dicts()
    events = [r for r in rows if r["label"] in ("saccade", "microsaccade")]

    assert events
    assert any(r["reliability"] is not None for r in events)
    assert all(-1.0 <= r["reliability"] <= 1.0 for r in events if r["reliability"] is not None)


def test_a_detector_with_no_reliability_stores_none_rather_than_a_number(stepped_session):
    """The other half of the column's meaning. `reliability` is nullable
    because six of the seven planned detectors have no such index -- a stored
    number there for Engbert-Kliegl would be invented, and would make the
    column unreadable for the one detector that does compute one."""
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session
    rows = (
        detect.EyeDetection.Run
        & {**session_key, "trace": "left", **_detector("engbert_kliegl")}
    ).to_dicts()

    assert rows
    assert all(r["reliability"] is None for r in rows)


def test_the_conjunction_trace_never_carries_a_borrowed_reliability(stepped_session):
    """No detector produced the conjunction trace -- `_overlapping` intersects
    two eyes' spans and `_conjunction_label` derives its label -- so there is
    no per-detection index for it, on any detector. Attributing either eye's
    would be a fabricated number in the one column a reader consults to decide
    what to believe."""
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session
    for name in _detector_names():
        where = {**session_key, "trace": "conjunction", **_detector(name)}
        rows = (detect.EyeDetection.Run & where).to_dicts()

        assert rows, name
        assert all(r["reliability"] is None for r in rows), name


def test_the_middle_planted_step_is_classified_as_a_microsaccade(stepped_session):
    """The brief's own mutation check ("drop the `classify` call so every
    event is a `saccade`") is invisible to every OTHER test in this file --
    none of them distinguishes the two labels, only groups them together.
    `stepped_session`'s own middle transition (`_STEPS_PX[1]`, ~0.7 deg) is
    planted specifically to be classified `microsaccade`, and the two either
    side `saccade`, so this is the assertion that mutation actually breaks.

    **`conjunction` as well as `left`, and that trace is what watches the
    CALL SITE** (fix round, reviewer finding 1). `_conjunction_label` itself
    is thoroughly covered -- `test_the_conjunction_labels_each_span_from_its
    _own_amplitude` and `test_the_conjunction_label_matches_the_amplitude_
    that_gets_stored` both fail if the rule inside it changes -- but each of
    them BUILDS the callable itself and hands it to `_overlapping`, and so
    does the gated `test_the_run_count_measured_against_the_reference_
    recording`. None of them can see `EyeDetection.make()` handing
    `_overlapping` a DIFFERENT one, and replacing that argument with a
    constant `lambda _s, _e: Label.MICROSACCADE` passed the whole suite,
    reference recording included. Reading the conjunction's own stored
    labels here is what closes that: its three events are the same three
    planted transitions (synchronised in both eyes by construction, so each
    intersection is very nearly the whole of each eye's own span), measured
    on the LEFT eye's gaze as `make()` chooses, at 2.5 / 0.7 / 3.3 deg --
    so they carry the same three labels, and a constant label rule stores
    three `microsaccade`s instead.
    """
    from wl_preproc.schema import detect

    session_key, _report, planted_onsets = stepped_session
    for trace in ("left", "conjunction"):
        runs = (
            detect.EyeDetection.Run
            & {**session_key, "trace": trace, **_detector("engbert_kliegl")}
        ).to_dicts(order_by="run_index")
        events = [r for r in runs if r["label"] in ("saccade", "microsaccade")]

        assert len(events) == len(planted_onsets) == 3, trace
        assert [e["label"] for e in events] == ["saccade", "microsaccade", "saccade"], trace
        assert events[1]["amplitude_deg"] < 1.0, trace


def test_the_conjunction_requires_temporal_overlap_in_both_eyes(stepped_session):
    """Engbert-Kliegl's own noise suppression, applied uniformly. An event in
    one eye alone is not in the conjunction.

    The three planted transitions alone cannot tell a union from an
    intersection: `write_ohdpi` moves both eyes together for anything built
    from `recipe.eye_fixations`, so every planted event is synchronised in
    both eyes and a union of `left`/`right` would satisfy the overlap
    assertion just as well as an intersection. `_inject_right_eye_only_step`
    is what actually discriminates: RIGHT alone has a fourth event
    (`_PHANTOM_ONSET_S`) that LEFT never sees, so this test also asserts
    directly that the phantom event's OWN span is present in `right` and
    absent from both `left` and `conjunction` -- not only that every
    conjunction span happens to overlap something in `left`.
    """
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session

    def saccade_spans(trace):
        return [
            (r["run_start"], r["run_stop"])
            for r in (
                detect.EyeDetection.Run
                & {**session_key, "trace": trace, **_detector("engbert_kliegl")}
            ).to_dicts()
            if r["label"] in ("saccade", "microsaccade")
        ]

    both = saccade_spans("conjunction")
    left = saccade_spans("left")
    right = saccade_spans("right")
    assert both
    for start, stop in both:
        assert any(ls < stop and start < lstop for ls, lstop in left)

    # The phantom event: present in `right` alone. `_first_row_at` rather
    # than `(onset + 0.6) * 500.0` spelled out, so this row and the row
    # `_inject_right_eye_only_step` actually wrote come from one place.
    phantom_row = _first_row_at(_PHANTOM_ONSET_S)
    assert any(rs < phantom_row + _PHANTOM_N_SUBSTEPS and phantom_row < rstop for rs, rstop in right)
    assert not any(ls < phantom_row + _PHANTOM_N_SUBSTEPS and phantom_row < lstop for ls, lstop in left)
    assert not any(
        cs < phantom_row + _PHANTOM_N_SUBSTEPS and phantom_row < cstop for cs, cstop in both
    )


def test_engbert_kliegl_conjunction_rows_are_unchanged(stepped_session):
    """Design spec section 4's bar ("Engbert-Kliegl, Otero-Millan and U'n'Eye
    produce byte-identical conjunction rows... This is a test, not an
    expectation"). Engbert-Kliegl emits ONE kind (saccadic), so
    `_conjunction_runs`' grouping partitions it into a single group and the
    loop that group runs through is `_overlapping`'s own loop -- wiring
    `make()` to the grouped entry point must not move the three events this
    fixture plants.

    **Pinned against a capture taken at the pre-wiring commit (`6bc3704`,
    the parent this task's wiring change landed on), not recomputed from the
    code under test.** A recomputation would move right along with a
    regression it exists to catch. The three tuples below were produced by
    running the identical query below, unmodified, against a `git worktree`
    checked out at `6bc3704`, and cross-checked against an identical capture
    at this task's own commit before it was ever compared to these literals
    -- both are recorded in the Task 4 fix-round report rather than trusted
    from a single run. Re-derive them the same way against any future commit
    under suspicion rather than trusting these forever.

    **Exact boundaries and labels, not only a count.** A prior version of
    this test asserted `len(events) == 3` plus a label superset that
    Engbert-Kliegl's own declared vocabulary (`{saccade, microsaccade}`)
    already guarantees on its own -- `registry.Detector.detect` refuses
    `pso`/`pursuit`/`drift` from this detector before a row is ever stored,
    so that check could barely fail. A boundary shift of a few samples, or a
    swapped saccade/microsaccade verdict, would have left the count at three
    and passed unnoticed (fix-round review finding, Task 4). This version
    pins `run_start`, `run_stop` and `label` for all three events instead.

    **Still not the fully exhaustive check.** `amplitude_deg`,
    `peak_velocity_deg_s`, `reliability`, the master row's own
    `n_saccades`/`n_microsaccades`, and every OTHER detector, are not pinned
    here -- that exhaustive byte-identical comparison is what
    `test_the_run_count_measured_against_the_reference_recording` is for,
    and it is skipped by default (it requires `WLPP_OHDPI_REFERENCE` to
    point at a real recording -- see that test's own skip message). What
    this test proves, every default run, is exact timing and labelling for
    the one fixture in this suite built to plant known transitions."""
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session

    rows = (
        detect.EyeDetection.Run
        & {**session_key, "trace": "conjunction", **_detector("engbert_kliegl")}
    ).to_dicts(order_by="run_index")
    events = [
        (r["run_start"], r["run_stop"], r["label"])
        for r in rows
        if r["label"] in ("saccade", "microsaccade")
    ]

    # Captured 2026-09-05 at 6bc3704 (immediately before `make()` was wired
    # to `_conjunction_runs`) via this identical query run against a
    # throwaway `git worktree` checkout, then confirmed identical against
    # the same query at this task's own commit -- see the Task 4 fix-round
    # report for both captures and the exact commands.
    assert events == [
        (6800, 6816, "saccade"),
        (7250, 7258, "microsaccade"),
        (7749, 7767, "saccade"),
    ]
    # No `pso`, `pursuit` or `drift` run can exist for a detector that emits
    # none of the three -- the shape rule cuts both ways. Weaker than the
    # pin above (see the docstring), kept as a cheap second signal.
    assert {r["label"] for r in rows} <= {
        "saccade", "microsaccade", "fixation", "blink", "invalid"
    }


def test_a_sub_floor_binocular_overlap_is_no_conjunction_event(near_miss_session):
    """Finding H3's floor, asserted where `EyeDetection.make()` actually
    applies it -- on a stored row, through `daemon.run_once()`.

    **The rule was covered; `make()`'s USE of it was not** (fix round,
    reviewer finding 1). `test_the_conjunction_inherits_the_detectors_own_
    minimum_duration` pins `_overlapping`'s floor and `test_a_detector_
    declaring_no_minimum_duration_gets_the_weakest_honest_floor` pins
    `_min_duration_samples`, but both HAND `_overlapping` a floor
    themselves, and so does the gated `test_the_run_count_measured_against_
    the_reference_recording`. None of them can see `make()` passing the
    wrong one: replacing `_min_duration_samples(detector_params)` with a
    literal `1` at that call site passed the entire suite, reference
    recording included.

    `near_miss_session` plants the one geometry that separates the two
    floors: a real event in each eye, overlapping by fewer samples than
    either eye's own detector would have accepted. At the detector's real
    floor that intersection is not an event; at a floor of 1 it is stored as
    one -- carrying, per `_overlapping`'s own docstring, an amplitude near
    zero beside a peak velocity of hundreds of degrees per second, which is
    the pair design spec section 6.5 fits a main sequence from.
    """
    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS
    from wl_preproc.schema import detect

    session_key, _report = near_miss_session
    floor = DEFAULT_EK_PARAMS.min_duration_samples

    def event_spans(trace):
        return [
            (r["run_start"], r["run_stop"])
            for r in (
                detect.EyeDetection.Run
                & {**session_key, "trace": trace, **_detector("engbert_kliegl")}
            ).to_dicts(order_by="run_index")
            if r["label"] in ("saccade", "microsaccade")
        ]

    def touching(spans, start, stop):
        return [(s, t) for s, t in spans if s < stop and start < t]

    left_row = _first_row_at(_NEAR_MISS_ONSET_S)
    right_row = left_row + _NEAR_MISS_SHIFT_SAMPLES
    (left_event,) = touching(event_spans("left"), left_row, left_row + _NEAR_MISS_N_SUBSTEPS)
    (right_event,) = touching(event_spans("right"), right_row, right_row + _NEAR_MISS_N_SUBSTEPS)

    # The fixture saying what it claims, and the assertion below not being
    # vacuous: both eyes really do detect an event here, and the two really
    # do overlap -- by fewer samples than the floor. A detector change that
    # moved this out of range must retune `_NEAR_MISS_SHIFT_SAMPLES` (see
    # its own comment for the arithmetic), never widen this range: an
    # overlap of 0 would make the conjunction empty here for the wrong
    # reason, and one at or above the floor for no reason at all.
    overlap = min(left_event[1], right_event[1]) - max(left_event[0], right_event[0])
    assert 1 <= overlap < floor, (
        f"the near-miss pair overlaps by {overlap} samples, outside the [1, {floor}) "
        f"range this fixture exists to plant -- left {left_event}, right {right_event}"
    )

    intruders = touching(
        event_spans("conjunction"), left_row, right_row + _NEAR_MISS_N_SUBSTEPS
    )
    assert not intruders, (
        f"a {overlap}-sample binocular overlap was stored as conjunction event(s) "
        f"{intruders}, though the detector's own floor is {floor} samples"
    )

    # And the three planted transitions are still there, so the assertion
    # above is not passing because this session's conjunction is empty.
    assert len(event_spans("conjunction")) == 3


def _assert_reason_echoes_validity(session_key, trace, eye):
    """`EyeDetection`'s refused `trace` row must carry `EyeValidity`'s OWN
    `eye` row's own reason -- fetched per eye, never picked arbitrarily from
    a query spanning both (the exact shape of the original defect, fix
    round reviewer finding 1)."""
    from wl_preproc.schema import detect

    validity_reason = (detect.EyeValidity & {**session_key, "eye": eye}).fetch1("reason")
    # A refusal comes from the CALIBRATION, not from any detector, so every
    # registered detector's row for this trace must carry it -- a refusal that
    # reached only one detector's rows would leave the others claiming a
    # computed result for a session that has no gaze.
    rows = []
    for name in _detector_names():
        row = (detect.EyeDetection & {**session_key, "trace": trace, **_detector(name)}).fetch1()
        assert row["status"] == "refused", name
        assert row["reason"] == validity_reason, name
        assert row["n_samples"] is None, name
        rows.append(row)
    return rows[0]


def test_a_refused_left_eye_leaves_the_right_eye_computed(left_refused_session):
    """Fix round, reviewer finding 1, row 2 of the required table: `left`
    refused (with ITS OWN `EyeValidity` reason, not `right`'s), `right`
    still the genuine computed trace with its three planted events intact,
    and `conjunction` refused too -- but never silently wearing `left`'s
    reason verbatim.

    Mutation check (required by the brief): restoring the session-wide
    `refused = EyeValidity & validity_key & 'status = "refused"'` gate
    makes this test fail, because it refuses `right` too -- confirmed by
    hand against the actual reverted code before this file was committed;
    see this task's own fix report for the pasted failure.
    """
    from wl_preproc.schema import detect

    session_key, report, onsets = left_refused_session
    assert not any(
        "EyeValidity" in message or "EyeDetection" in message for message in report["errors"]
    )

    left_row = _assert_reason_echoes_validity(session_key, "left", "left")

    right_status = (detect.EyeValidity & {**session_key, "eye": "right"}).fetch1("status")
    assert right_status == "computed"
    right_row = (
        detect.EyeDetection & {**session_key, "trace": "right", **_detector("engbert_kliegl")}
    ).fetch1()
    assert right_row["status"] == "computed"
    right_runs = (
        detect.EyeDetection.Run
        & {**session_key, "trace": "right", **_detector("engbert_kliegl")}
    ).to_dicts(order_by="run_index")
    right_onsets = [r["run_start"] for r in right_runs if r["label"] in ("saccade", "microsaccade")]
    assert len(right_onsets) == len(onsets) == 3
    for got, want in zip(right_onsets, onsets, strict=True):
        assert abs(got - want) <= 5

    conjunction_row = (
        detect.EyeDetection
        & {**session_key, "trace": "conjunction", **_detector("engbert_kliegl")}
    ).fetch1()
    assert conjunction_row["status"] == "refused"
    assert conjunction_row["reason"]
    assert conjunction_row["reason"] != left_row["reason"]


def test_a_refused_right_eye_leaves_the_left_eye_computed(right_refused_session):
    """The mirror of the test above -- Finding 1's own row 3 (`computed |
    refused`). Required alongside the LEFT direction because a fix that
    hardcodes an eye (e.g. always trusting `left`, the same shortcut
    `_insert_trace`'s own conjunction measurement takes deliberately and
    states in its own comment) passes one direction and fails the other."""
    from wl_preproc.schema import detect

    session_key, report, onsets = right_refused_session
    assert not any(
        "EyeValidity" in message or "EyeDetection" in message for message in report["errors"]
    )

    right_row = _assert_reason_echoes_validity(session_key, "right", "right")

    left_status = (detect.EyeValidity & {**session_key, "eye": "left"}).fetch1("status")
    assert left_status == "computed"
    left_row = (
        detect.EyeDetection & {**session_key, "trace": "left", **_detector("engbert_kliegl")}
    ).fetch1()
    assert left_row["status"] == "computed"
    left_runs = (
        detect.EyeDetection.Run
        & {**session_key, "trace": "left", **_detector("engbert_kliegl")}
    ).to_dicts(order_by="run_index")
    left_onsets = [r["run_start"] for r in left_runs if r["label"] in ("saccade", "microsaccade")]
    assert len(left_onsets) == len(onsets) == 3
    for got, want in zip(left_onsets, onsets, strict=True):
        assert abs(got - want) <= 5

    conjunction_row = (
        detect.EyeDetection
        & {**session_key, "trace": "conjunction", **_detector("engbert_kliegl")}
    ).fetch1()
    assert conjunction_row["status"] == "refused"
    assert conjunction_row["reason"]
    assert conjunction_row["reason"] != right_row["reason"]


def test_a_session_with_no_calibration_is_refused_with_a_reason(uncalibrated_session):
    """Detection reads gaze as a computation, so no calibration means no
    gaze. A refused row with a stated reason, never an error and never an
    empty success.

    `conjunction`'s own reason is checked only for PRESENCE here, not for
    the word "calibration": fix round (reviewer finding 1) gives it its own
    wording -- "conjunction needs both eyes' ... spans", naming that a
    conjunction needs both eyes rather than repeating either eye's
    calibration-flavoured reason verbatim (`test_both_eyes_refused_each_
    trace_carries_its_own_eyes_reason`, below, checks that wording and its
    provenance directly)."""
    from wl_preproc.schema import detect

    session_key, report = uncalibrated_session
    assert not any(
        "EyeValidity" in message or "EyeDetection" in message for message in report["errors"]
    )

    rows = (detect.EyeDetection & session_key).to_dicts()

    assert rows
    for row in rows:
        assert row["status"] == "refused"
        assert row["reason"]
        if row["trace"] in ("left", "right"):
            assert "calibration" in row["reason"]
        assert row["n_samples"] is None


def test_both_eyes_refused_each_trace_carries_its_own_eyes_reason(uncalibrated_session):
    """Fix round, reviewer finding 1, row 4 of the required table: both
    eyes refused, `left`'s trace carrying `left`'s own `EyeValidity` reason
    and `right`'s carrying `right`'s -- each fetched per eye via
    `_assert_reason_echoes_validity`, not read off one shared, unordered
    `EyeValidity & 'status = "refused"'` query the way the original defect
    did. `conjunction` is refused too, with its own reason stated (never
    left blank, and never simply equal to either eye's)."""
    from wl_preproc.schema import detect

    session_key, _report = uncalibrated_session

    _assert_reason_echoes_validity(session_key, "left", "left")
    _assert_reason_echoes_validity(session_key, "right", "right")

    conjunction_row = (
        detect.EyeDetection
        & {**session_key, "trace": "conjunction", **_detector("engbert_kliegl")}
    ).fetch1()
    assert conjunction_row["status"] == "refused"
    assert conjunction_row["reason"]


def test_the_registered_paramsets_match_the_detector_registry(daemon_module):
    """The completeness claim, in the shape `EXTRACTORS` already uses. A
    detector with no paramset never runs; a paramset with no detector fails
    on the session that reaches it.

    Takes `daemon_module` (unused directly) rather than nothing, unlike this
    task's own brief: `register_default_paramsets()` writes real
    `paramset.ParamSet` rows, which needs an activated schema, and every
    other database-touching test in this project's suite depends on its own
    activation explicitly (`tests/schema/test_paramset.py`'s own `ps`
    fixture) rather than relying on test execution order to have activated
    one first -- the brief's own bare signature would pass only because
    THIS file's own earlier tests happen to activate one first, and fail run
    in isolation.
    """
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import detect

    registered = detect.register_default_paramsets()
    assert set(registered) == set(DETECTORS)


def test_the_validity_mask_never_emits_a_real_fixation_label():
    """`EyeDetection.make()` reads the stored mask back, translating
    `FIXATION` -> `None` (available). That translation is unambiguous only
    because `validity_labels` itself never emits `Label.FIXATION` as a real
    verdict -- asserted directly here (Task 7 preflight finding 2) rather
    than relied on silently."""
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS, validity_labels

    n = 50
    rng = np.random.default_rng(0)
    gaze = rng.normal(0, 1, (n, 2))
    velocity_deg_s = rng.normal(0, 1, (n, 2))
    quality = np.full(n, 100.0)
    quality[10:15] = 0.0  # a blink
    gaze[30:35, 0] = 100.0  # outside the region -> invalid

    labels = validity_labels(gaze, velocity_deg_s, quality, (), DEFAULT_VALIDITY_PARAMS).labels

    present = {label for label in labels if label is not None}
    assert Label.FIXATION not in present
    assert present <= {Label.BLINK, Label.INVALID}
    # Not vacuous: both real verdicts this fixture plants actually appear.
    assert Label.BLINK in present
    assert Label.INVALID in present


def test_every_label_has_a_kind_or_is_deliberately_excluded():
    """Design spec section 3.1's exhaustiveness guard. The label enum is
    closed because the migration window shuts January 2027, so a ninth label
    added without updating the kind map must fail loudly rather than be
    silently dropped from every conjunction."""
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.schema.detect import _KIND_OF, _NOT_INTERSECTED, _kind_of

    assert set(_KIND_OF) | _NOT_INTERSECTED == set(Label)
    assert not (set(_KIND_OF) & _NOT_INTERSECTED)

    # saccade and microsaccade are ONE kind: design spec section 1 calls them
    # "a split, not a ranking" -- the same event distinguished only by size.
    assert _kind_of(Label.SACCADE) == _kind_of(Label.MICROSACCADE)
    # Every other emitted label is its own kind.
    assert len({_kind_of(Label.PSO), _kind_of(Label.PURSUIT),
                _kind_of(Label.DRIFT), _kind_of(Label.SACCADE)}) == 4
    # fixation is the synthesized background (spec section 1.2); blink and
    # invalid come from the validity mask and are in no vocabulary.
    for label in (Label.FIXATION, Label.BLINK, Label.INVALID):
        assert _kind_of(label) is None


def test_a_single_label_kind_is_keyed_by_its_own_label_value():
    """`_conjunction_runs` labels a non-saccadic kind with `Label(kind)`, so
    the kind key and the label value must agree. A kind named anything else
    would raise `ValueError` deep inside the grouping loop, for one detector,
    only once a real recording produced that label."""
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.schema.detect import _KIND_OF

    for label, kind in _KIND_OF.items():
        if kind != "saccadic":
            assert Label(kind) is label, f"kind {kind!r} does not name {label!r}"


def test_a_label_with_no_kind_raises():
    """The guard has teeth: it is reachable if the enum grows and the map
    does not."""
    import pytest
    from wl_preproc.schema.detect import UnknownLabelKind, _kind_of

    with pytest.raises(UnknownLabelKind, match="no conjunction kind"):
        _kind_of("nystagmus")


# Moved to `wl_preproc.schema.detect` alongside `_conjunction_runs`, its new
# production caller -- a second definition here would be exactly the "two
# definitions of one fact" `labels.py::LabelledInterval`'s own comment warns
# against. Still used bare below: the H3 duration-floor tests (unchanged) and
# the `_conjunction_runs` tests both call it without a local import, exactly
# as they did when it was defined in this module.
from wl_preproc.schema.detect import _always


def test_the_conjunction_inherits_the_detectors_own_minimum_duration():
    """Finding H3: `_overlapping` intersected the two eyes' spans with no
    duration floor of any kind, so the intersection of two 6-sample
    Engbert-Kliegl events overlapping by ONE sample was stored as a 1-sample
    event. `measure`'s `gaze_deg[stop - 1] - gaze_deg[start]` is identically
    zero for such a span, so `classify` labelled it a 0.0-degree
    `microsaccade` carrying a peak velocity of up to 864 deg/s -- and design
    spec section 6.5 fits the main sequence from exactly those two columns.

    Measured on the reference recording through the real `_overlapping`
    before the fix: 4,952 conjunction spans, 402 of them (8.1%) below the
    6-sample floor, 44 exactly one sample. The boundary cases below are the
    two the ruling names -- fewer than the floor yields nothing, exactly the
    floor yields the intersection -- so the 2-to-5-sample spans a
    `stop - start == 1` special case in `measure` would have missed are
    covered too, not only the most visible ones.
    """
    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _min_duration_samples, _overlapping

    floor = DEFAULT_EK_PARAMS.min_duration_samples
    # Read off the detector's own defaults, then pinned: the arithmetic below
    # is written against a floor of 6 and would be silently vacuous at 1.
    assert floor == 6
    assert _min_duration_samples(DEFAULT_EK_PARAMS) == floor

    def sacc(start, stop):
        return Run(start, stop, Label.SACCADE)

    saccade = _always(Label.SACCADE)
    # Both eyes' own events comfortably clear the floor (10 and 21 samples);
    # only their OVERLAP is short. This is the shape that manufactured the 44.
    assert _overlapping([sacc(100, 110)], [sacc(109, 130)], floor, saccade) == []
    # One sample short of the floor: still nothing. This is the shape a
    # `stop - start == 1` guard inside `measure` would have let through.
    assert _overlapping([sacc(100, 110)], [sacc(110 - (floor - 1), 130)], floor, saccade) == []
    # Exactly the floor: a real event, and it is the intersection itself.
    assert _overlapping([sacc(100, 110)], [sacc(110 - floor, 130)], floor, saccade) == [
        sacc(110 - floor, 110)
    ]
    # Comfortably over it, unchanged by the filter.
    assert _overlapping([sacc(100, 130)], [sacc(105, 140)], floor, saccade) == [sacc(105, 130)]


def test_a_detector_declaring_no_minimum_duration_gets_the_weakest_honest_floor():
    """Stage 2's six other detectors (design spec section 3.1) each bring
    their own params dataclass, and `min_duration_samples` is a field of
    `EngbertKlieglParams` rather than part of `registry.Detector.run`'s
    contract -- so `_min_duration_samples` must answer for a params object
    that has no such field.

    1, not 0 and not 6: such a detector would itself have accepted a
    one-sample run, so the rule the fix enforces ("never shorter than either
    eye's own detector would have accepted") admits one here too. It is also
    the weakest value that keeps `measure`'s own `stop > start` precondition
    true, which is why `_overlapping` floors at 1 rather than trusting the
    number it is handed.
    """
    from dataclasses import dataclass

    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _min_duration_samples, _overlapping

    @dataclass(frozen=True, slots=True)
    class _NoFloorParams:
        threshold_deg_s: float

    assert _min_duration_samples(_NoFloorParams(threshold_deg_s=30.0)) == 1

    def sacc(start, stop):
        return Run(start, stop, Label.SACCADE)

    saccade = _always(Label.SACCADE)
    # A floor of 1 is not "no filter": it is exactly the `stop > start` test
    # this fix replaced, so abutting spans still yield nothing.
    assert _overlapping([sacc(0, 10)], [sacc(10, 20)], 1, saccade) == []
    assert _overlapping([sacc(0, 10)], [sacc(9, 20)], 1, saccade) == [sacc(9, 10)]
    # And a paramset naming 0 cannot weaken it into a span `measure` refuses.
    assert _overlapping([sacc(0, 10)], [sacc(10, 20)], 0, saccade) == []


def test_a_left_fixation_never_crosses_a_right_saccade():
    """THE defect this change exists to fix. `_overlapping` intersects on
    time alone and never reads a label -- correct only while every emitted
    label is the same kind of thing, which is true of the two registered
    detectors and false for all four blocked ones.

    Three of the four emit `fixation`, which TILES the recording, so a left
    fixation would have crossed a right saccade and been kept as a binocular
    event. No stage-1 or stage-2A test could reach this: finding it needs a
    detector that emits more than one kind, and neither stage has one."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_runs

    left = [Run(0, 1000, Label.FIXATION)]
    right = [Run(400, 460, Label.SACCADE)]
    assert _conjunction_runs(left, right, 6, _always(Label.SACCADE)) == []


def test_a_binocular_glissade_survives_as_pso():
    """The shape rule. Both eyes called it `pso`; the conjunction says `pso`,
    not `saccade` and not nothing."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_runs

    left = [Run(100, 130, Label.SACCADE), Run(130, 160, Label.PSO)]
    right = [Run(105, 135, Label.SACCADE), Run(135, 165, Label.PSO)]
    runs = _conjunction_runs(left, right, 6, _always(Label.SACCADE))

    assert runs == [Run(105, 130, Label.SACCADE), Run(135, 160, Label.PSO)]


def test_kinds_that_disagree_produce_no_conjunction_run_of_either():
    """Agreement on kind is what supplies the label, so disagreement supplies
    nothing. This is NOT the arbitration stage 1 removed: that rule RANKED
    the two eyes' labels through `PRECEDENCE`; this requires agreement and
    ranks nothing."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_runs

    left = [Run(100, 160, Label.SACCADE)]
    right = [Run(100, 160, Label.PSO)]
    assert _conjunction_runs(left, right, 6, _always(Label.SACCADE)) == []


def test_the_amplitude_split_is_one_kind_not_two():
    """Design spec section 1: `saccade` and `microsaccade` are "a split, not a
    ranking". A left saccade meeting a right microsaccade is two eyes
    agreeing that an event happened and differing only about its size -- which
    the conjunction settles by measuring its OWN amplitude, exactly as stage 1
    did. Dropping this pair would be a behaviour change for the two shipped
    detectors."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_runs

    left = [Run(100, 160, Label.SACCADE)]
    right = [Run(100, 160, Label.MICROSACCADE)]
    runs = _conjunction_runs(left, right, 6, _always(Label.MICROSACCADE))

    assert runs == [Run(100, 160, Label.MICROSACCADE)]


def test_spans_of_different_kinds_never_overlap():
    """Load-bearing for `_insert_trace`, which paints intervals onto one
    array and lets a later interval overwrite an earlier one. Two kinds'
    spans cannot overlap because each is a subset of a left run of its own
    kind, and one eye's runs are disjoint by construction -- but that is an
    argument, and the equality below is what makes it a fact.

    **The equality is what carries this test, not the loop.** Grouped, this
    fixture's three kinds intersect to exactly the three runs asserted
    below. Skip the grouping -- call `_overlapping` directly over all three
    kinds at once, the regression this test exists to catch -- and its own
    touching-span coalescing, which never reads a label, merges every
    overlap into ONE run, `Run(10, 140, ...)`. A single run makes
    `zip(runs, runs[1:])` iterate zero times, so the loop below would have
    passed on that broken result without ever executing. It stays anyway,
    now that the equality above it has already proved there are three runs
    to check."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_runs

    left = [Run(0, 50, Label.SACCADE), Run(50, 100, Label.PSO),
            Run(100, 150, Label.PURSUIT)]
    right = [Run(10, 60, Label.SACCADE), Run(45, 110, Label.PSO),
             Run(90, 140, Label.PURSUIT)]
    runs = sorted(_conjunction_runs(left, right, 1, _always(Label.SACCADE)),
                  key=lambda run: run.start)

    assert runs == [
        Run(10, 50, Label.SACCADE),
        Run(50, 100, Label.PSO),
        Run(100, 140, Label.PURSUIT),
    ]
    for earlier, later in zip(runs, runs[1:]):
        assert earlier.stop <= later.start, f"{earlier} overlaps {later}"


def test_conjunction_runs_come_back_sorted_by_start():
    """Concatenating per-kind results would otherwise return them grouped by
    kind, which makes every assertion about them depend on dict ordering."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_runs

    left = [Run(200, 260, Label.PSO), Run(0, 60, Label.SACCADE)]
    right = [Run(200, 260, Label.PSO), Run(0, 60, Label.SACCADE)]
    runs = _conjunction_runs(left, right, 6, _always(Label.SACCADE))

    assert [run.start for run in runs] == [0, 200]


@dataclasses.dataclass(frozen=True, slots=True)
class _AmplitudeBlindParams:
    """Stand-in for a detector with no amplitude-derived labels -- U'n'Eye
    emits `saccade` alone (design spec section 3.1), so no amplitude cut tells
    it anything and it declares no `microsaccade_max_deg` field.

    **Otero-Millan was this example until 2026-09-01.** Reading its reference
    corrected its vocabulary to the full `saccade / microsaccade` split, so it
    DOES declare that field -- see `otero_millan.OteroMillanParams`. A stand-in
    is still the right shape here: the detector this describes is not written
    yet, and the point is the filtering rule, not any one detector.

    Module level, not inside the test that uses it: this file starts
    `from __future__ import annotations`, so `_params_for`'s own
    `typing.get_type_hints` call resolves the stub detector's `params`
    annotation against the module's globals (a class defined inside a
    function body is not there, and the lookup raises `NameError`).
    """

    threshold_deg_s: float


def _amplitude_blind_detect(
    gaze_deg, velocity_deg_s, available, params: _AmplitudeBlindParams
) -> list:
    return []


def test_the_shared_amplitude_cut_reaches_otero_millan_through_the_paramset():
    """`_params_for` hands a detector exactly the paramset keys its own
    dataclass names. Otero-Millan declares `microsaccade_max_deg` because its
    vocabulary IS the amplitude split (design spec section 3.1 as corrected
    2026-09-01), so the shared cut must actually arrive there -- and a paramset
    that moved it must move that detector's labels with it.

    Lives here rather than in `tests/eye/detect/test_otero_millan.py` because
    `_params_for` is schema-layer code: no other test under `tests/eye/`
    imports `wl_preproc.schema`, and one that did would make that tier require
    DataJoint, which the 3.13 cross-check environment does not install.
    """
    from wl_preproc.eye.detect.otero_millan import OteroMillanParams
    from wl_preproc.eye.detect.registry import get_detector
    from wl_preproc.schema.detect import _params_for

    raw = {
        "detector": "otero_millan",
        "min_cluster_displacement_deg": 0.2,
        "max_clusters": 4,
        "min_isi_samples": 10,
        "microsaccade_max_deg": 3.0,
    }

    built = _params_for(get_detector("otero_millan"), raw)

    assert isinstance(built, OteroMillanParams)
    assert built.microsaccade_max_deg == 3.0
    assert not hasattr(built, "detector")


def test_params_for_drops_keys_the_detector_does_not_declare():
    """A real paramset is a SHARED vocabulary: a `detector` selector no
    detector's dataclass declares, each detector's own parameters, and
    subsystem-wide keys some detectors consume and others cannot. `_params_
    for` filters to exactly the dataclass's own field names rather than
    naming keys to drop, so a detector never has to know what else the
    paramset carries."""
    from dataclasses import asdict

    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS, EngbertKlieglParams
    from wl_preproc.eye.detect.registry import get_detector
    from wl_preproc.schema.detect import _params_for

    raw = {
        "detector": "engbert_kliegl",
        # A key no dataclass in this subsystem declares, standing in for
        # whatever the shared vocabulary grows next.
        "pso_refractory_samples": 12,
        **asdict(DEFAULT_EK_PARAMS),
    }
    built = _params_for(get_detector("engbert_kliegl"), raw)

    assert isinstance(built, EngbertKlieglParams)
    assert built == DEFAULT_EK_PARAMS


def test_a_shared_paramset_key_reaches_the_detector_that_declares_it():
    """`microsaccade_max_deg` is not any one detector's parameter -- it is
    registered once at the top of the shared `eye_detection` paramset, so
    every detector that splits by amplitude splits at the same place. It
    reaches Engbert-Kliegl because `EngbertKlieglParams` DECLARES a field of
    that name, which is the detector stating that it consumes the shared key.

    The value must come from the PARAMSET, not from the dataclass default: a
    non-default threshold below, so `_params_for` falling back to
    `MICROSACCADE_MAX_DEG` -- or dropping the key, as it did before this
    contract was finished -- fails rather than passing by coincidence.
    """
    from dataclasses import asdict

    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS
    from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG
    from wl_preproc.eye.detect.registry import get_detector
    from wl_preproc.schema.detect import _params_for

    revised = 0.25
    assert revised != MICROSACCADE_MAX_DEG
    raw = {"detector": "engbert_kliegl", **asdict(DEFAULT_EK_PARAMS)}
    raw["microsaccade_max_deg"] = revised

    built = _params_for(get_detector("engbert_kliegl"), raw)

    assert built.microsaccade_max_deg == revised


@dataclasses.dataclass(frozen=True, slots=True)
class _NoParams:
    """What a detector with no tunable parameters declares.
    `registry.Detector.defaults` is required rather than defaulting to `{}` --
    registry.py's own comment says why a silent empty default would be the
    same class of quiet failure one layer down -- so a stub still says it has
    none, rather than being allowed to omit the question."""


def test_a_detector_with_no_amplitude_labels_is_not_handed_the_threshold():
    """Design spec section 3.1: U'n'Eye emits `saccade` alone, so there is no
    amplitude split for a threshold to place and no field for it to arrive
    in. (Otero-Millan is not this example -- corrected 2026-09-01, it
    declares the full `saccade / microsaccade` split and DOES declare the
    field, matching `registry.DETECTORS["otero_millan"]`.) The same filter
    that DELIVERS the shared key to a detector that declares it must
    withhold it from one that does not -- otherwise every future detector is
    forced to accept a parameter it cannot use, and
    `TypeError: unexpected keyword argument` is what a real paramset would
    hand the first session that reached it."""
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.registry import Detector
    from wl_preproc.schema.detect import _params_for

    blind = Detector(
        name="amplitude_blind",
        vocabulary=frozenset({Label.MICROSACCADE}),
        run=_amplitude_blind_detect,
        defaults=_NoParams(),
    )
    raw = {
        "detector": "amplitude_blind",
        "microsaccade_max_deg": 1.0,
        "threshold_deg_s": 30.0,
    }

    built = _params_for(blind, raw)

    assert built == _AmplitudeBlindParams(threshold_deg_s=30.0)
    assert not hasattr(built, "microsaccade_max_deg")


def test_the_shared_threshold_is_registered_once_and_no_detector_shadows_it(monkeypatch):
    """`register_default_paramsets` merges each detector's own defaults and
    the shared `microsaccade_max_deg` into one dict. Because a detector that
    consumes the shared key declares a field of that name (`_params_for`
    explains why that is how a shared key reaches a detector at all), `asdict`
    of its defaults carries a value of its own -- and merged in the wrong
    order that value would win, letting each detector quietly pick the
    amplitude cut its own rows are split at.

    Run through the REAL `register_default_paramsets`, with `paramset.
    register` captured rather than reaching a database and with this
    detector's own default moved off the shared value. Rebuilding the same
    merge here and asserting on it would be a copy of the code under test:
    the merge order is the whole subject, so a test that performs it itself
    cannot fail when production's order changes -- and the two values are
    equal in reality, so the ordering is load-bearing without being visible
    in any stored row.
    """
    from dataclasses import replace

    from wl_preproc.eye.detect import engbert_kliegl, otero_millan
    from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG
    from wl_preproc.schema import detect as detect_schema, paramset

    captured: dict[str, list[dict]] = {}

    def _capture(paramset_type, params):
        captured.setdefault(paramset_type, []).append(params)
        return 0

    monkeypatch.setattr(paramset, "register", _capture)
    monkeypatch.setattr(
        engbert_kliegl,
        "DEFAULT_EK_PARAMS",
        replace(engbert_kliegl.DEFAULT_EK_PARAMS, microsaccade_max_deg=99.0),
    )
    # **Both detectors moved off the shared value, because there are now two
    # that declare it.** Moving one alone would leave the other's own default
    # equal to the shared one, and its paramset would satisfy the assertion
    # below no matter which order the merge ran in.
    monkeypatch.setattr(
        otero_millan,
        "DEFAULT_OM_PARAMS",
        replace(otero_millan.DEFAULT_OM_PARAMS, microsaccade_max_deg=98.0),
    )

    detect_schema.register_default_paramsets()

    registered = {row["detector"]: row for row in captured["eye_detection"]}
    assert set(registered) == set(_detector_names())
    for name, row in registered.items():
        assert row["microsaccade_max_deg"] == MICROSACCADE_MAX_DEG, name
    # Not vacuous: each detector's own default really is another value, so the
    # assertions above decide between live candidates rather than comparing
    # the shared value with itself.
    assert engbert_kliegl.DEFAULT_EK_PARAMS.microsaccade_max_deg == 99.0
    assert otero_millan.DEFAULT_OM_PARAMS.microsaccade_max_deg == 98.0
    # Each detector's OWN parameters still arrive -- the ordering must not
    # drop them on its way to protecting the shared key.
    assert registered["engbert_kliegl"]["lambda_"] == (
        engbert_kliegl.DEFAULT_EK_PARAMS.lambda_
    )
    assert registered["otero_millan"]["min_cluster_displacement_deg"] == (
        otero_millan.DEFAULT_OM_PARAMS.min_cluster_displacement_deg
    )


class _CapturedInserts:
    """Enough of an `EyeDetection` for `_insert_trace` to run against without
    a database: it touches `self.insert1` and `self.Run.insert` and nothing
    else. Borrowing the real unbound method (rather than reimplementing it)
    is what makes the test below a test of production code."""

    def __init__(self):
        self.master: list[dict] = []
        self.rows: list[dict] = []
        outer = self

        class _Part:
            @staticmethod
            def insert(rows):
                outer.rows.extend(rows)

        self.Run = _Part

    def insert1(self, row):
        self.master.append(row)


def _run_insert_trace(intervals, n_samples=200, fs_hz=500.0):
    """`EyeDetection._insert_trace` over a flat trace and a fully-available
    mask, returning `(master_row, run_rows)`."""
    from wl_preproc.schema import detect as detect_schema

    gaze = np.zeros((n_samples, 2))
    gaze[:, 0] = np.linspace(0.0, 4.0, n_samples)  # a real, measurable ramp
    v = np.zeros((n_samples, 2))
    v[:, 0] = 100.0
    offered = np.full(n_samples, None, dtype=object)

    sink = _CapturedInserts()
    detect_schema.EyeDetection._insert_trace(
        sink, {"subject": "s"}, "left", gaze, v, offered, intervals, fs_hz
    )
    (master,) = sink.master
    return master, sink.rows


def test_insert_trace_stores_the_label_each_interval_carries():
    """The stage-2 blocker this whole contract exists to remove, asserted
    directly: `_insert_trace` used to call `classify` on every span, which can
    only answer `saccade` or `microsaccade`, so a detector declaring
    `{saccade, pso, fixation}` would have had everything it found relabelled
    by amplitude.

    A `pso` interval is what makes this test fail against the old code and
    impossible to satisfy by re-classifying: no amplitude threshold produces
    `pso`, and design spec section 3.1 gives four of the seven planned
    detectors vocabularies that include it. Without an interval carrying a
    label outside the amplitude split, reverting `_insert_trace` to
    `classify` is an EQUIVALENT mutation for the one detector shipped today,
    since `classify` over the detector's own span reproduces exactly the
    label the detector already assigned.
    """
    from wl_preproc.eye.detect.labels import Label, Run

    master, rows = _run_insert_trace(
        [Run(20, 40, Label.PSO), Run(60, 80, Label.MICROSACCADE), Run(100, 140, Label.SACCADE)]
    )

    stored = [row["label"] for row in rows if row["label"] != "fixation"]
    assert stored == ["pso", "microsaccade", "saccade"]
    # The counts are of the labels actually stored, not of intervals seen.
    assert master["n_saccades"] == 1
    assert master["n_microsaccades"] == 1


def test_insert_trace_measures_only_the_event_labels():
    """A `pso` run is not an event row: design spec section 5 gives
    `amplitude_deg`/`peak_velocity_deg_s` to "a run of a `saccade` or
    `microsaccade` label", and section 6.5 fits the main sequence from
    exactly those two columns. A `pso` carrying them would put lens ringing
    into that fit."""
    from wl_preproc.eye.detect.labels import Label, Run

    _master, rows = _run_insert_trace([Run(20, 40, Label.PSO), Run(60, 80, Label.SACCADE)])

    by_label = {row["label"]: row for row in rows}
    assert by_label["pso"]["amplitude_deg"] is None
    assert by_label["saccade"]["amplitude_deg"] is not None
    assert by_label["fixation"]["amplitude_deg"] is None


def _ramp_gaze(n_samples, deg_per_sample):
    """A gaze trace moving `deg_per_sample` along x each sample, so any span's
    `amplitude` is exactly `(stop - 1 - start) * deg_per_sample` -- an
    interval's amplitude is arithmetic here rather than something a test has
    to measure to know."""
    gaze = np.zeros((n_samples, 2))
    gaze[:, 0] = np.arange(n_samples) * deg_per_sample
    return gaze


def test_the_conjunction_labels_each_span_from_its_own_amplitude():
    """The rule that replaced `labels.py::PRECEDENCE`. The conjunction has no
    detector to speak for it, so its label comes from `classify` over the
    intersection's OWN amplitude -- the same `[start, stop)`, on the same
    gaze, that `_insert_trace` measures and stores.

    **The two eyes' own labels are not consulted, and this test is built so
    that a precedence rule gives a different answer.** Each case below pairs
    a left-eye `saccade` with a right-eye `microsaccade` -- the pair
    `PRECEDENCE` used to resolve to `saccade` every time -- while the
    intersection's own amplitude decides one way in the first case and the
    other in the second. A reversion to ranking the eyes' labels fails the
    second assertion; a reversion that ranked the other way fails the first.
    """
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.eye.detect.registry import get_detector
    from wl_preproc.schema.detect import _conjunction_label, _overlapping

    detector = get_detector("engbert_kliegl")
    params = {"detector": "engbert_kliegl", "microsaccade_max_deg": 1.0}

    # 0.1 deg per sample: a 30-sample intersection spans 2.9 deg, a 6-sample
    # one spans 0.5 deg -- either side of the 1.0 deg cut, from one trace.
    gaze = _ramp_gaze(200, 0.1)
    label_for = _conjunction_label(detector, params, gaze)

    (big,) = _overlapping(
        [Run(100, 130, Label.SACCADE)], [Run(100, 130, Label.MICROSACCADE)], 6, label_for
    )
    assert big == Run(100, 130, Label.SACCADE)

    (small,) = _overlapping(
        [Run(100, 106, Label.SACCADE)], [Run(100, 106, Label.MICROSACCADE)], 6, label_for
    )
    assert small == Run(100, 106, Label.MICROSACCADE)

    # It is the INTERSECTION that is classified, not either eye's own span:
    # both eyes' events are long enough to clear the cut on their own, and
    # only their overlap is small.
    (overlap,) = _overlapping(
        [Run(100, 130, Label.SACCADE)], [Run(124, 160, Label.SACCADE)], 6, label_for
    )
    assert overlap == Run(124, 130, Label.MICROSACCADE)

    # Symmetric: which eye is named first cannot change an answer that never
    # reads either eye's label.
    (swapped,) = _overlapping(
        [Run(124, 160, Label.SACCADE)], [Run(100, 130, Label.SACCADE)], 6, label_for
    )
    assert swapped == overlap


def test_the_conjunction_label_matches_the_amplitude_that_gets_stored():
    """The defect this rule closes, asserted through the real
    `_insert_trace`: a conjunction run whose stored `label` says `saccade`
    and whose stored `amplitude_deg` is 0.8 deg is not a measurement design
    spec section 6.5 can fit a main sequence from, and 12.3% of conjunction
    event rows were exactly that.

    Label and amplitude now come from one interval on one trace, so the
    agreement is structural. Checked on the STORED row rather than on
    `_overlapping`'s output, because it is the stored pair section 6.5
    reads.
    """
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.eye.detect.measure import classify
    from wl_preproc.eye.detect.registry import get_detector
    from wl_preproc.schema import detect as detect_schema
    from wl_preproc.schema.detect import _conjunction_label, _overlapping

    n_samples = 400
    gaze = _ramp_gaze(n_samples, 0.1)
    v = np.zeros((n_samples, 2))
    v[:, 0] = 100.0
    offered = np.full(n_samples, None, dtype=object)
    params = {"detector": "engbert_kliegl", "microsaccade_max_deg": 1.0}
    label_for = _conjunction_label(get_detector("engbert_kliegl"), params, gaze)

    spans = _overlapping(
        [Run(20, 60, Label.SACCADE), Run(100, 108, Label.MICROSACCADE),
         Run(200, 260, Label.MICROSACCADE)],
        [Run(30, 80, Label.MICROSACCADE), Run(100, 110, Label.SACCADE),
         Run(190, 240, Label.SACCADE)],
        6,
        label_for,
    )

    sink = _CapturedInserts()
    detect_schema.EyeDetection._insert_trace(
        sink, {"subject": "s"}, "conjunction", gaze, v, offered, spans, 500.0
    )
    events = [row for row in sink.rows if row["label"] in ("saccade", "microsaccade")]

    # Not vacuous: both labels are actually produced, so the assertion below
    # is deciding between two live answers on every row.
    assert {row["label"] for row in events} == {"saccade", "microsaccade"}
    for row in events:
        assert row["label"] == classify(row["amplitude_deg"], 1.0).value


def test_two_touching_intersections_become_one_conjunction_event():
    """`_overlapping` coalesces touching intersections BEFORE labelling them,
    and this is why. `_insert_trace` writes each interval's label onto the
    mask and re-derives runs from it, so two touching intervals carrying the
    same label come back as ONE run -- whose amplitude is measured over the
    whole of it, not over either half.

    The gaze below is built so those are different answers: each half spans
    0.95 deg and the whole spans 1.95 deg, either side of the 1.0 deg cut.
    Without the coalescing, `_overlapping` labels each half `microsaccade`
    from its own 0.95 deg, `runs_from_labels` merges them, and one row is
    stored saying `microsaccade` beside an `amplitude_deg` of 1.95 -- the
    exact label/amplitude contradiction this round exists to make
    impossible, reachable through a detector that returns abutting
    intervals. `registry.py::DetectFn` permits one; today's does not, which
    is why this is asserted rather than left to the reference recording.
    """
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.eye.detect.registry import get_detector
    from wl_preproc.schema import detect as detect_schema
    from wl_preproc.schema.detect import _conjunction_label, _overlapping

    n_samples = 200
    gaze = _ramp_gaze(n_samples, 0.05)
    v = np.zeros((n_samples, 2))
    v[:, 0] = 100.0
    offered = np.full(n_samples, None, dtype=object)
    label_for = _conjunction_label(
        get_detector("engbert_kliegl"), {"microsaccade_max_deg": 1.0}, gaze
    )

    # One left-eye event meeting two abutting right-eye events, so the two
    # intersections touch at sample 120.
    spans = _overlapping(
        [Run(100, 140, Label.SACCADE)],
        [Run(100, 120, Label.SACCADE), Run(120, 140, Label.SACCADE)],
        6,
        label_for,
    )
    assert spans == [Run(100, 140, Label.SACCADE)]

    sink = _CapturedInserts()
    detect_schema.EyeDetection._insert_trace(
        sink, {"subject": "s"}, "conjunction", gaze, v, offered, spans, 500.0
    )
    (event,) = [row for row in sink.rows if row["label"] != "fixation"]
    assert (event["run_start"], event["run_stop"]) == (100, 140)
    assert event["label"] == "saccade"
    assert event["amplitude_deg"] == pytest.approx(1.95)


def test_a_pso_capable_detector_no_longer_raises():
    """The raise that blocked four detectors. It fired on any vocabulary that
    was not a subset of the amplitude split -- because ONE label had to cover
    a mixed vocabulary. Under per-kind intersection each kind labels itself,
    so the mixed case does not arise."""
    import numpy as np
    from dataclasses import replace
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema.detect import _conjunction_label

    nystrom = replace(
        DETECTORS["engbert_kliegl"],
        name="nystrom_holmqvist",
        vocabulary=frozenset({Label.SACCADE, Label.PSO, Label.FIXATION}),
    )
    # 1-D, deliberately: `measure.amplitude` reads `gaze_deg[stop - 1] -
    # gaze_deg[start]` as an (x, y) pair (`test_the_full_split_still_
    # classifies_by_amplitude` documents this) and raises `IndexError` on a
    # 1-D difference before `classify` ever runs. So a degenerate branch that
    # mistakenly asked `classify` anyway would crash here rather than
    # quietly answer -- this shape is a poison pill, not an oversight.
    gaze = np.zeros(1000)
    label_for = _conjunction_label(nystrom, {"microsaccade_max_deg": 1.0}, gaze)

    # `{saccade, pso, fixation}` has a saccadic slice of `{saccade}` alone --
    # a DEGENERATE split, so the constant, never a call to `classify` at all.
    assert label_for(0, 10) is Label.SACCADE


def test_the_saccadic_slice_decides_degeneracy_not_the_whole_vocabulary():
    """Five of the seven detectors take the degenerate branch, and only
    Engbert-Kliegl and Otero-Millan declare BOTH sides of the amplitude cut.
    Testing `len(vocabulary) == 1` -- what stage 1 did -- would send
    Nystrom-Holmqvist's three-label vocabulary to `classify`, which would put
    `microsaccade` in the mouth of a detector that cannot emit it."""
    import numpy as np
    from dataclasses import replace
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema.detect import _conjunction_label

    # 1-D, like the sibling test above -- deliberately, not an oversight.
    # `measure.amplitude` reads `gaze_deg[stop - 1] - gaze_deg[start]` as an
    # (x, y) pair (`test_the_full_split_still_classifies_by_amplitude`
    # documents this exact hazard) and raises `IndexError` on a 1-D
    # difference before `classify` ever runs. So this is not "the degenerate
    # branch overrides an answer `classify` would otherwise have given" --
    # reaching `classify` here would crash, not answer `saccade`. The
    # degenerate branch must not ask at all.
    gaze = np.linspace(0.0, 9.0, 1000)  # a large amplitude on any interval
    bmd = replace(
        DETECTORS["engbert_kliegl"],
        name="bayesian_microsaccade",
        vocabulary=frozenset({Label.MICROSACCADE, Label.DRIFT}),
    )
    label_for = _conjunction_label(bmd, {"microsaccade_max_deg": 1.0}, gaze)

    assert label_for(0, 999) is Label.MICROSACCADE


def test_the_full_split_still_classifies_by_amplitude():
    """Engbert-Kliegl and Otero-Millan are unchanged, and this is the
    assertion that says so at the unit level."""
    import numpy as np
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema.detect import _conjunction_label

    # `amplitude()` reads `gaze_deg[stop - 1] - gaze_deg[start]` as an (x, y)
    # pair -- `measure.py::amplitude`'s own `displacement[0], displacement[1]`
    # -- matching every real gaze array in this codebase (`eye/calibration.py
    # ::apply_map`, which is what `eye/gaze.py::gaze_trace` returns, ends in
    # `np.column_stack([x, y])`) and this file's own `_ramp_gaze` helper. A
    # bare 1-D array is not a shape `amplitude` ever receives outside a test,
    # and indexing `displacement[1]` on a 1-D difference raises `IndexError`
    # before `classify` is ever reached. The y column is all zero, so the x
    # column alone -- unchanged from a plain `linspace` -- still carries the
    # ~9 deg and ~0.08 deg amplitudes asserted below.
    gaze = np.column_stack([np.linspace(0.0, 9.0, 1000), np.zeros(1000)])
    label_for = _conjunction_label(
        DETECTORS["engbert_kliegl"], {"microsaccade_max_deg": 1.0}, gaze
    )

    assert label_for(0, 999) is Label.SACCADE       # ~9 deg
    assert label_for(0, 10) is Label.MICROSACCADE   # ~0.08 deg


def test_an_empty_vocabulary_still_raises():
    """Unchanged, and for its own reason: `frozenset() <= anything` is True,
    so a detector declaring nothing passes any subset test while
    `registry.Detector.detect` refuses every label it emits."""
    import numpy as np, pytest
    from dataclasses import replace
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema.detect import UndecidedConjunctionLabel, _conjunction_label

    empty = replace(DETECTORS["engbert_kliegl"], vocabulary=frozenset())
    with pytest.raises(UndecidedConjunctionLabel, match="empty vocabulary"):
        _conjunction_label(empty, {"microsaccade_max_deg": 1.0}, np.zeros(10))


def test_a_detector_declaring_no_saccadic_label_raises_only_if_invoked():
    """`_no_saccadic_label`, added alongside the SACCADIC SLICE
    generalization and, until this test, verified only by reading the code.

    Unreachable in production for both registered detectors
    (`engbert_kliegl`, `otero_millan` -- both declare the full split, so
    `saccadic` is never empty for either) and, by the argument
    `_conjunction_label`'s own comment above this branch makes, for ANY
    detector reached through `EyeDetection.make()`'s real wiring:
    `_conjunction_runs` groups runs by each run's OWN label
    (`_kind_of`), never by `detector.vocabulary`, and
    `registry.Detector.detect` refuses a run outside its detector's declared
    vocabulary -- so a detector with no saccadic label in its vocabulary can
    never produce a run for `_conjunction_runs` to route to this callable.

    None of that is enforced by a type, only by two things agreeing
    (`EyeDetection.make()` sourcing both eyes' spans from the SAME
    detector's `.detect()`, and passing that same detector here) that a
    future change could break quietly -- calling `.run()` instead of
    `.detect()`, or wiring `_conjunction_runs` to a different detector than
    the one passed to `_conjunction_label`. This test does not exercise
    that real wiring (Task 4's); it pins the one thing `_conjunction_label`
    itself is responsible for regardless: that the callable it hands back
    for an all-non-saccadic vocabulary is silent until asked, and correct
    -- `UndecidedConjunctionLabel`, naming the detector -- once it is.
    """
    from dataclasses import replace
    import numpy as np
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema.detect import UndecidedConjunctionLabel, _conjunction_label

    pso_only = replace(
        DETECTORS["engbert_kliegl"],
        name="pso_only_hypothetical",
        vocabulary=frozenset({Label.PSO}),
    )

    # Returned, not raised: a detector's non-saccadic kinds are none of this
    # function's business (`_conjunction_label`'s own comment, above), so
    # getting the callable back must not itself fail.
    label_for = _conjunction_label(pso_only, {"microsaccade_max_deg": 1.0}, np.zeros((10, 2)))

    with pytest.raises(UndecidedConjunctionLabel, match="declares no saccadic label"):
        label_for(0, 5)


def test_a_vocabulary_beyond_the_amplitude_split_no_longer_refuses_to_label_the_conjunction():
    """Until 2026-09-05 this test asserted that `_conjunction_label` RAISED
    `UndecidedConjunctionLabel` for `{saccade, pso, fixation}` -- the guard
    fired on any vocabulary that was not a SUBSET of `{saccade, microsaccade}`,
    because ONE label had to cover a mixed vocabulary. Per-kind intersection
    (`_conjunction_runs`, Task 2) means each kind labels itself, so the mixed
    case that guard was refusing does not arise any more, and the guard is
    gone. `test_a_pso_capable_detector_no_longer_raises` and
    `test_the_saccadic_slice_decides_degeneracy_not_the_whole_vocabulary`
    assert the replacement rule directly; this test keeps its other two
    assertions below, which that change did not touch and which are not
    duplicated there.
    """
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.registry import Detector
    from wl_preproc.schema.detect import _conjunction_label

    glissade_aware = Detector(
        name="nystrom_holmqvist",
        vocabulary=frozenset({Label.SACCADE, Label.PSO, Label.FIXATION}),
        run=_amplitude_blind_detect,
        defaults=_NoParams(),
    )

    # The saccadic slice of `{saccade, pso, fixation}` is `{saccade}` alone --
    # a degenerate split -- so this no longer raises and never reaches
    # `classify`.
    label_for = _conjunction_label(
        glissade_aware, {"microsaccade_max_deg": 1.0}, _ramp_gaze(50, 0.1)
    )
    assert label_for(0, 6) is Label.SACCADE

    # A detector declaring `{microsaccade}` ALONE is a SUBSET of the
    # amplitude split, not equal to it, and must still be labelled rather
    # than refused. Named "otero_millan" here as a hypothetical rather than
    # as the real detector: design spec section 3.1, corrected 2026-09-01,
    # gives the real Otero-Millan the FULL split (`{saccade, microsaccade}`,
    # matching `registry.DETECTORS["otero_millan"]`) -- U'n'Eye (`{saccade}`)
    # is the only half-split case among the seven, on the amplitude cut's
    # other side. This name predates that correction and stands in for "some
    # detector declaring microsaccade alone", not for the real one.
    subset = Detector(
        name="otero_millan",
        vocabulary=frozenset({Label.MICROSACCADE}),
        run=_amplitude_blind_detect,
        defaults=_NoParams(),
    )
    subset_label = _conjunction_label(subset, {"microsaccade_max_deg": 1.0}, _ramp_gaze(50, 0.1))
    assert subset_label(0, 6) is Label.MICROSACCADE

    # And the threshold is never defaulted from `measure.MICROSACCADE_MAX_DEG`:
    # a paramset that names none is a paramset nothing can honestly classify
    # against, so it raises rather than picking a module constant. Asserted on
    # the detector that actually classifies -- a degenerate split reads no
    # threshold at all, which `test_a_degenerate_amplitude_split_never_labels_
    # outside_the_declared_vocabulary` pins directly.
    from wl_preproc.eye.detect.registry import get_detector

    with pytest.raises(KeyError, match="microsaccade_max_deg"):
        _conjunction_label(
            get_detector("engbert_kliegl"), {"detector": "engbert_kliegl"}, _ramp_gaze(50, 0.1)
        )


def test_a_degenerate_amplitude_split_never_labels_outside_the_declared_vocabulary():
    """Reviewer finding 2. `_AMPLITUDE_DERIVED_VOCABULARY` is a SUBSET test,
    so a detector declaring `{microsaccade}` alone or `{saccade}` alone both
    reach the conjunction rule -- and `classify` answers both sides of the
    cut for any detector. Before the fix a `{microsaccade}`-only detector's
    2.9 deg intersection came back `Run(100, 130, Label.SACCADE)`, a label
    `registry.Detector.detect` refuses from that same detector's own
    intervals one function earlier.

    **The loop below names its two cases "otero_millan" and "uneye"; only
    the second name matches its real detector's real vocabulary.** This test
    was added `4d66502`, 2026-09-01 13:52, when Otero-Millan was still
    believed to be the `{microsaccade}`-only case -- corrected the same day
    at 16:06 (`3e857e1`; design spec section 3.1's own correction) once
    reading the reference implementation showed it declares the FULL split
    instead, `{saccade, microsaccade}`, matching
    `registry.DETECTORS["otero_millan"]`. "otero_millan" survives here as a
    name for that now-superseded belief, not as a claim about the real
    detector; U'n'Eye (`{saccade}`) is unaffected by the correction and is
    the one real detector this test's other case models.

    Unreachable in stage 1 -- the one registered detector declares the whole
    split -- and reachable today for U'n'Eye's real `{saccade}` the moment
    it lands. The `{microsaccade}`-only case no longer matches any of
    section 3.1's seven now that Otero-Millan's vocabulary is corrected, but
    is kept as a hypothetical: the guard it tests -- a size-one vocabulary
    getting its one label degenerately, never `classify`'s other answer --
    is the same guard a real `{microsaccade}`-only detector would need. It
    matters because section 6.1's coarsening lattice reads the DECLARATION
    and coarsens the STORED labels into it, with `microsaccade -> saccade`
    its only amplitude-split rule: a stored `saccade` on a trace declared
    `{microsaccade}` has no rule to place it, and the pair gets scored in a
    vocabulary that trace does not speak.

    **Both directions, and on both sides of the cut**, because a fix that
    pinned one class rather than reading the declaration would pass half of
    this. The conjunction interval is the intersection and so systematically
    shorter than either eye's event (section 5.1), which is why the U'n'Eye
    direction -- small intersections falling below the cut on a detector
    that cannot say `microsaccade` -- is the one most likely to fire first.
    """
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.eye.detect.registry import Detector
    from wl_preproc.schema.detect import _conjunction_label, _overlapping

    # 0.1 deg per sample, so a 30-sample span is 2.9 deg and a 6-sample span
    # 0.5 deg -- either side of the 1.0 deg cut, as in
    # `test_the_conjunction_labels_each_span_from_its_own_amplitude`.
    gaze = _ramp_gaze(200, 0.1)
    big, small = Run(100, 130, Label.SACCADE), Run(100, 106, Label.SACCADE)

    for name, declared in (
        ("otero_millan", Label.MICROSACCADE),
        ("uneye", Label.SACCADE),
    ):
        detector = Detector(
            name=name, vocabulary=frozenset({declared}),
            run=_amplitude_blind_detect, defaults=_NoParams(),
        )
        # Both paramsets, and each says something different. WITH the
        # threshold is the reviewer's own reproduction, and the case that
        # fails on the LABEL rather than on a missing key if the degenerate
        # split is ever dropped. WITHOUT it pins that a degenerate split
        # reads no threshold at all: demanding a number that cannot change
        # the answer would tell a reader the paramset governs these rows.
        for params in ({"detector": name, "microsaccade_max_deg": 1.0}, {"detector": name}):
            label_for = _conjunction_label(detector, params, gaze)

            for span in (big, small):
                (run,) = _overlapping([span], [span], 6, label_for)
                assert run.label is declared, (
                    f"{name} at {span.stop - span.start} samples, params {sorted(params)}"
                )
                assert run.label in detector.vocabulary

    # Not vacuous: over these same two spans the FULL split really does
    # answer both ways, so the loop above is overriding a live disagreement
    # rather than agreeing with `classify` by coincidence.
    from wl_preproc.eye.detect.registry import get_detector

    full = _conjunction_label(
        get_detector("engbert_kliegl"), {"microsaccade_max_deg": 1.0}, gaze
    )
    assert full(big.start, big.stop) is Label.SACCADE
    assert full(small.start, small.stop) is Label.MICROSACCADE


def test_a_detector_declaring_nothing_cannot_label_a_conjunction():
    """`frozenset() & _AMPLITUDE_DERIVED_VOCABULARY` is `frozenset()`, exactly
    as it would be for a detector that declares only non-saccadic labels --
    so without its own guard, a detector declaring NOTHING would fall into
    the same lazy-raising path `test_a_detector_declaring_no_saccadic_label_
    raises_only_if_invoked` exercises for a genuine `{pso}`-only detector,
    and would look fine until its callable was actually asked for a saccadic
    label.

    Raised eagerly instead, with its own reason, rather than folded into
    that lazy path: nothing is undecided here for ANY kind, saccadic or not.
    A detector that declares nothing is one `registry.Detector.detect`
    refuses every interval from, so it can have no per-eye spans for a
    conjunction of any kind to intersect -- unlike a genuine `{pso}`-only
    detector, whose non-saccadic kinds are real and simply none of this
    function's business.
    """
    from wl_preproc.eye.detect.registry import Detector
    from wl_preproc.schema.detect import UndecidedConjunctionLabel, _conjunction_label

    silent = Detector(
        name="declares_nothing", vocabulary=frozenset(),
        run=_amplitude_blind_detect, defaults=_NoParams(),
    )

    with pytest.raises(UndecidedConjunctionLabel) as excinfo:
        _conjunction_label(silent, {"microsaccade_max_deg": 1.0}, _ramp_gaze(50, 0.1))

    message = str(excinfo.value)
    assert "declares_nothing" in message and "empty vocabulary" in message


def test_the_detection_key_source_has_no_stray_eye_attribute(daemon_module):
    """The brief's own literal `key_source` (`EyeValidity * (paramset.
    ParamSet & {"paramset_type": "eye_detection"})`) is broken two ways,
    both confirmed directly against a live MySQL 8 container running this
    project's pinned DataJoint 2.3.2, before this fix was written:

    1. `EyeValidity`'s own FK to `ParamSet` renames only `paramset_idx`
       (`paramset_type` stays bare -- `EyeValidity`'s own `key_source`
       docstring already explains why that renaming is correct THERE, since
       it has only one `ParamSet` reference). Joining that bare `paramset_
       type` (always `'eye_validity'` on every real row) against `(paramset.
       ParamSet & {"paramset_type": "eye_detection"})` -- which ALSO has a
       bare `paramset_type`, always `'eye_detection'` -- makes DataJoint
       match the two same-named columns for equality. They can never agree,
       so the join is permanently empty.
    2. Worse: `EyeValidity`'s own primary key includes `eye`, which has no
       counterpart on `EyeDetection` (native `trace` instead) -- so even
       fixed for (1), `.populate()` raises outright:
       `DataJointError: The populate target lacks attribute eye from the
       primary key of key_source`.

    Fixed by collapsing `EyeValidity` down to ONE row per (session,
    validity paramset) via `dj.U(...)`, dropping `eye` entirely before the
    join, with `paramset_type` renamed on the way so the two `ParamSet`
    references never share a bare column name. Pinned here so a regression
    fails with this direct, targeted message rather than the crash three
    tables away that a bare `daemon.run_once()` call would otherwise raise.
    """
    from wl_preproc.schema import detect

    key_source_pk = set(detect.EyeDetection.key_source.primary_key)
    assert "eye" not in key_source_pk
    # `trace` is native to `EyeDetection` -- filled by `make()`'s own three
    # inserts, never supplied by `key_source` -- exactly the shape `eye` is
    # for `EyeCalibration`/`EyeQuality` (`wl_preproc/schema/eye.py`'s own
    # `key_source`s carry no `eye` either, for the identical reason). So
    # `key_source`'s own primary key is the table's own MINUS `trace`, not
    # equal to it outright.
    assert key_source_pk == set(detect.EyeDetection.primary_key) - {"trace"}


# ---------------------------------------------------------------------------
# Task 8: the run count design spec section 5 only estimated -- "roughly
# 14,000 runs per eye per detector ... extrapolated from typical saccade
# rates, not measured." Measured here, once, against the one real recording
# every "1,177,799" reference in this repository points at
# (`~/Downloads/Tutorial/OpenIris-2024Jul31-114628/OpenIris-2024Jul31-114628.txt`,
# 633 MB -- never committed, never copied into this tree).
#
# **Drives the library functions directly, not `EyeValidity`/`EyeDetection`.**
# Both tables need a landed session and a validated `EyeCalibration` row, and
# nothing validates a calibration for this recording: it has no `.bhv2` and no
# known fixation-target positions, so there is nothing for `fit_map` to fit
# against. Landing a session just to reach `make()` would be strictly more
# machinery for a LESS honest number -- the schema path would still need a
# scale from somewhere, and would hide where it came from behind a database
# round trip. Calling `validity_labels`/`velocity`/the registered detector's
# own `run`/`runs_from_labels` directly is simpler and says plainly, in one
# place, exactly what is and is not known about the scale (see the test's own
# docstring, below).
# ---------------------------------------------------------------------------


def _scaled_affine_map(scale: float):
    """`degrees = scale * raw_px` on both axes, no cross terms, no offset --
    the same shape `CAL_SCALE` uses above (module docstring), for the same
    reason: a diagonal scale is the simplest map that lets a plausible degree
    range be CHOSEN when nothing has fit a real one. `CalibrationMap` (not a
    bare tuple), so `apply_map`/`basis` are exercised exactly as every real
    caller exercises them."""
    from wl_preproc.eye.calibration import CalibrationMap, CalibrationModel

    return CalibrationMap(model=CalibrationModel.AFFINE, x=(0.0, scale, 0.0), y=(0.0, 0.0, scale))


def _mask_and_velocity(raw_xy, quality, fs_hz, frame_gaps, scale):
    """One eye's gaze at `scale`, its velocity, and its validity mask -- the
    three real functions `EyeValidity.make()` itself calls (`apply_map`,
    `velocity`, `validity_labels`), run here without a database.

    The MASK, meaning `ValidityMask.labels` alone: this test measures run
    counts and detected spans, and `ValidityMask`'s per-criterion fractions
    are `EyeValidity.make()`'s own bookkeeping, not an input to anything
    below. Returning the labels array keeps every helper downstream
    (`_detect`, `_encode`) taking the per-sample mask they always took."""
    from wl_preproc.eye.calibration import apply_map
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS, validity_labels
    from wl_preproc.eye.detect.velocity import velocity

    gaze = apply_map(_scaled_affine_map(scale), raw_xy)
    v = velocity(gaze, fs_hz)
    mask = validity_labels(gaze, v, quality, frame_gaps, DEFAULT_VALIDITY_PARAMS)
    return gaze, v, mask.labels


def _bounds(intervals):
    """Just the `[start, stop)` boundaries of a list of labelled intervals.

    Detectors return labelled `Run`s, and the LABEL is not scale-invariant
    even where the boundaries are (`classify` thresholds an absolute degree
    amplitude) -- so a scale-invariance assertion has to compare boundaries
    explicitly rather than whole `Run`s, or it would claim something this
    subsystem never argued.
    """
    return [(interval.start, interval.stop) for interval in intervals]


def _detect(gaze, v, mask, fs_hz):
    """The registered Engbert-Kliegl detector (`registry.get_detector`, the
    same lookup `EyeDetection.make()` uses), through `Detector.detect` -- the
    vocabulary-checked entry point `make()` itself calls, not the raw `run` --
    at its default parameters. Returns labelled `Run`s."""
    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS
    from wl_preproc.eye.detect.registry import get_detector

    return get_detector("engbert_kliegl").detect(gaze, v, mask, fs_hz, DEFAULT_EK_PARAMS)


def _encode(mask, intervals):
    """Write each interval's own label onto the mask and encode the result to
    runs -- `EyeDetection._insert_trace`'s own label-then-encode step
    (`runs_from_labels`), minus the two inserts and the per-run measurement.
    Returns the runs and the saccade/microsaccade split.

    **No `classify` call, deliberately.** The labels come from the detector
    (or, for the conjunction, from the `label_for` `_overlapping` was handed):
    this helper mirrors `_insert_trace`, and `_insert_trace` assigns no
    labels of its own."""
    from wl_preproc.eye.detect.labels import Label, runs_from_labels

    labels = mask.copy()
    n_saccade = n_microsaccade = 0
    for interval in intervals:
        labels[interval.start : interval.stop] = interval.label
        if interval.label is Label.SACCADE:
            n_saccade += 1
        else:
            n_microsaccade += 1
    labels = np.where(labels == None, Label.FIXATION, labels)  # noqa: E711
    return runs_from_labels(labels), n_saccade, n_microsaccade


def _event_amplitudes(runs, gaze, v, fs_hz):
    """`(run, amplitude_deg)` for every event run, measured exactly as
    `EyeDetection._insert_trace::_run_row` measures it -- `measure` over the
    FINAL run, on the trace's own gaze. The STORED amplitude, in other words,
    which is the only one that can contradict a stored label and the one
    design spec section 6.5 fits a main sequence from."""
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.measure import measure

    return [
        (run, measure(gaze, v, run.start, run.stop, fs_hz).amplitude_deg)
        for run in runs
        if run.label in (Label.SACCADE, Label.MICROSACCADE)
    ]


def test_the_run_count_measured_against_the_reference_recording(capsys):
    """Design spec section 5, verbatim: "That figure is extrapolated from
    typical saccade rates, not measured -- nothing has run a detector on a
    real recording yet." This runs one, following `tests/eye/test_bhv2.py::
    test_a_real_monkeylogic_file_parses_to_the_observed_values`'s own idiom:
    an env-var-gated real-file test, skipped everywhere the file is not
    present, and never committed regardless.

    **Timing, measured directly, so nobody mistakes this for a hang.** The
    file is 633 MB, 1,177,799 rows. Reading it takes four column-selective
    passes -- `read_ohdpi` once (frame numbers, seconds, the sync word),
    `purkinje_vector` once per eye, `read_columns` once for both eyes'
    `DataQuality` -- and this test times and prints that total. Measured on
    this machine: ~8 s for all four passes combined. Everything downstream
    (masking, detecting, encoding, at every scale this test uses) runs in
    well under a second per pass, because it is pure in-memory numpy once the
    columns are in hand -- which is also why the sweep below re-reads
    nothing: `raw["left"]`/`raw["right"]`/`quality` are read ONCE and every
    scale after that is `apply_map` on the same arrays.

    **Why a scale can be chosen at all without a validated calibration for
    this recording.** `detect_engbert_kliegl` thresholds
    `(v_x/eta_x)**2 + (v_y/eta_y)**2` against 1, where
    `eta_x = lambda_ * _median_scale(v_x)` (and likewise for y).
    `velocity()` is linear in gaze and `_median_scale` is homogeneous of
    degree 1 (`sqrt(median(v**2) - median(v)**2)` scales exactly with `|v|`),
    so scaling gaze by any positive constant scales `v` and both `eta`s by
    that same constant and the ratio -- hence the detected span set -- is
    unchanged, GIVEN A FIXED validity mask. That is verified directly below,
    not assumed. `validity_labels` is a different story: its region and
    speed criteria (`ValidityParams.region_half_width_deg`/
    `region_half_height_deg`/`max_speed_deg_s`) are ABSOLUTE degrees, so a
    different scale changes which samples the mask claims, which can change
    what the detector ever sees. Part 1 below demonstrates the invariant
    that IS true (fixed mask, two scales, byte-identical spans); Part 2
    measures the sensitivity that remains once the mask is allowed to
    respond to scale, which is the real uncertainty on the number this test
    surfaces, honestly, alongside it.

    **Part 3 checks every stored event row's label against its own stored
    amplitude**, which is the defect the conjunction's old precedence rule
    left behind: the label was the two eyes' consensus of two full-event
    amplitudes and the amplitude was the left eye's over the shorter
    intersection, so 12.3% of conjunction event rows stored a pair that
    contradicted itself while both eyes' own traces stored 0. A sample
    would have missed it -- seven rows in eight agreed -- so this asserts
    over every row of all three traces. It also prints each trace's median
    event amplitude and duration, which is where the conjunction's
    systematically SMALLER amplitudes (its interval is the intersection, and
    so shorter than either eye's own event) stop being an argument and
    become a measurement.

    **What is not measured here.** One detector of seven (Engbert-Kliegl,
    this subsystem's zero-dependency baseline); the other six may disagree
    substantially, in either direction, on run count as well as on
    agreement. And the saccade/microsaccade SPLIT -- unlike the run count --
    is not scale-invariant: `classify` thresholds absolute degrees, so it
    depends on exactly the calibration scale this recording does not have.
    Part 1 measures that dependence directly too.
    """
    sample = os.environ.get("WLPP_OHDPI_REFERENCE")
    if not sample:
        pytest.skip(
            "WLPP_OHDPI_REFERENCE is not set. Point it at a real OpenIrisDPI "
            "recording's raw .txt file to run this test -- the reference "
            "recording this repository's design spec measures against is "
            "OpenIris-2024Jul31-114628.txt (633 MB, 1,177,799 rows, ~39.3 "
            "minutes at ~500 Hz), from this lab's own "
            "~/Downloads/Tutorial/OpenIris-2024Jul31-114628/ tutorial "
            "materials -- see design spec section 5. Never commit that file."
        )

    from wl_preproc.eye.calibration import apply_map
    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.measure import classify
    from wl_preproc.eye.detect.registry import get_detector
    from wl_preproc.eye.detect.velocity import velocity
    from wl_preproc.eye.gaze import purkinje_vector
    from wl_preproc.eye.ohdpi import read_columns, read_ohdpi
    from wl_preproc.schema.detect import _conjunction_label, _conjunction_runs, _overlapping

    t0 = time.monotonic()
    recording = read_ohdpi(sample)
    raw = {"left": purkinje_vector(sample, "Left"), "right": purkinje_vector(sample, "Right")}
    quality = read_columns(sample, ["LeftDataQuality", "RightDataQuality"])
    read_s = time.monotonic() - t0

    # The scale heuristic (this test's own honesty requirement: STATE it,
    # rather than just using it). Chosen, not fit: the pooled 99th percentile
    # of |raw Purkinje difference|, over both eyes, is placed at 15 degrees --
    # inside both `region_half_width_deg=20.0` and the tighter
    # `region_half_height_deg=15.0`, with margin, so "the bulk of the trace"
    # sits inside the plausible region rather than at its very edge.
    pooled_abs_x = np.concatenate([np.abs(raw["left"][:, 0]), np.abs(raw["right"][:, 0])])
    pooled_abs_y = np.concatenate([np.abs(raw["left"][:, 1]), np.abs(raw["right"][:, 1])])
    p99_x, p99_y = float(np.percentile(pooled_abs_x, 99)), float(np.percentile(pooled_abs_y, 99))
    scale_ref = 15.0 / max(p99_x, p99_y)

    left_gaze, left_v, left_mask = _mask_and_velocity(
        raw["left"], quality["LeftDataQuality"], recording.fs_hz, recording.frame_gaps, scale_ref
    )
    right_gaze, right_v, right_mask = _mask_and_velocity(
        raw["right"], quality["RightDataQuality"], recording.fs_hz, recording.frame_gaps, scale_ref
    )
    left_spans = _detect(left_gaze, left_v, left_mask, recording.fs_hz)
    right_spans = _detect(right_gaze, right_v, right_mask, recording.fs_hz)
    # Both the filtered conjunction and the raw intersection, so the finding
    # this floor closes stays MEASURED on real data rather than remembered
    # from a fix report: `floor=1` is exactly the `stop > start` test
    # `_overlapping` applied before finding H3, so their difference IS the
    # set of spans that used to be stored as events neither eye's own
    # detector would have accepted. Printed and asserted below.
    # `unfloored_spans` stays on `_overlapping` directly, deliberately: it
    # exists to isolate the H3 floor's own effect, and Engbert-Kliegl's
    # single kind makes `_overlapping` and `_conjunction_runs` produce the
    # same spans at any floor (`_conjunction_runs`'s own docstring), so
    # nothing about the kind-grouping question below is exercised either way
    # here.
    #
    # The conjunction's LABEL rule is the real one, built the way
    # `EyeDetection.make()` builds it -- `_conjunction_label` over the LEFT
    # eye's gaze, since that is the trace the conjunction is measured from.
    # `conjunction_spans` itself now goes through `_conjunction_runs`, not
    # `_overlapping` directly (conjunction-shape fix round, finding 6): that
    # is the function `EyeDetection.make()` actually calls, so the agreement
    # asserted at the end of this test is asserted against production's own
    # path end to end, not a pre-branch stand-in that happens to agree with
    # it only because this detector emits one kind.
    conjunction_label = _conjunction_label(
        get_detector("engbert_kliegl"),
        {"microsaccade_max_deg": DEFAULT_EK_PARAMS.microsaccade_max_deg},
        left_gaze,
    )
    conjunction_spans = _conjunction_runs(
        left_spans, right_spans, DEFAULT_EK_PARAMS.min_duration_samples, conjunction_label
    )
    unfloored_spans = _overlapping(left_spans, right_spans, 1, conjunction_label)

    left_runs, left_n_sacc, left_n_micro = _encode(left_mask, left_spans)
    right_runs, right_n_sacc, right_n_micro = _encode(right_mask, right_spans)
    # Conjunction measurement borrows the LEFT eye's own gaze/velocity, exactly
    # as `EyeDetection.make()`'s own comment states: "there is no cyclopean
    # trace any calibration in this codebase ever validated."
    conj_runs, conj_n_sacc, conj_n_micro = _encode(left_mask, conjunction_spans)
    total_runs = len(left_runs) + len(right_runs) + len(conj_runs)

    # --- Part 1: fixed-mask scale invariance (left eye), and what it costs to
    # give the fixed mask up. `scale_b` is `3 * scale_ref` -- an arbitrary but
    # clearly different positive constant; the argument in this test's own
    # docstring holds for ANY positive constant, so nothing here is tuned to
    # make it come out identical.
    scale_b = 3.0 * scale_ref
    gaze_b = apply_map(_scaled_affine_map(scale_b), raw["left"])
    v_b = velocity(gaze_b, recording.fs_hz)
    # `left_mask` REUSED, not recomputed.
    spans_b_fixed_mask = _detect(gaze_b, v_b, left_mask, recording.fs_hz)

    # The saccade/microsaccade split at the two scales, over the SAME fixed
    # spans -- the concrete demonstration that the split (unlike the count)
    # depends on scale: `classify` sees 3x the amplitude at `scale_b`.
    _, n_sacc_b, n_micro_b = _encode(left_mask, spans_b_fixed_mask)

    # --- Part 3: the label/amplitude agreement this round exists to close,
    # and the amplitude shift it makes visible. Every event run's STORED
    # amplitude, measured the way `_run_row` measures it, against the label
    # stored beside it.
    threshold = DEFAULT_EK_PARAMS.microsaccade_max_deg
    measured = {
        "left": _event_amplitudes(left_runs, left_gaze, left_v, recording.fs_hz),
        "right": _event_amplitudes(right_runs, right_gaze, right_v, recording.fs_hz),
        "conjunction": _event_amplitudes(conj_runs, left_gaze, left_v, recording.fs_hz),
    }
    contradictions = {
        trace_name: [
            (run, amplitude_deg)
            for run, amplitude_deg in events
            if classify(amplitude_deg, threshold) is not run.label
        ]
        for trace_name, events in measured.items()
    }

    with capsys.disabled():
        print(f"\n  reference recording: {sample}")
        print(
            f"  {recording.n_frames} frames, {recording.fs_hz:.2f} Hz, "
            f"{recording.n_frames / recording.fs_hz / 60:.1f} min, "
            f"{len(recording.frame_gaps)} frame gap(s)"
        )
        print(f"  file read: {read_s:.1f}s over 4 column-selective passes")
        print(
            f"  scale heuristic: {scale_ref:.6g} deg/px ({1 / scale_ref:.2f} px/deg) -- "
            f"pooled p99 |raw| is ({p99_x:.1f}, {p99_y:.1f}) px, placed at 15 deg"
        )
        print(
            f"  Part 1 -- fixed mask, scale x1 vs x3: spans identical = "
            f"{_bounds(spans_b_fixed_mask) == _bounds(left_spans)} "
            f"({len(left_spans)} spans each); "
            f"saccade/microsaccade split at x1 = {left_n_sacc}/{left_n_micro}, at x3 = "
            f"{n_sacc_b}/{n_micro_b} (same events, different degrees, different split)"
        )
        floor = DEFAULT_EK_PARAMS.min_duration_samples
        print(
            f"  conjunction duration floor (finding H3): {len(unfloored_spans)} raw "
            f"intersections, {len(unfloored_spans) - len(conjunction_spans)} dropped "
            f"below the {floor}-sample floor "
            f"({sum(1 for r in unfloored_spans if r.stop - r.start == 1)} exactly one "
            f"sample, "
            f"{sum(1 for r in unfloored_spans if 1 < r.stop - r.start < floor)} of "
            f"2-{floor - 1})"
        )
        print("  runs measured at the reference scale, engbert_kliegl, default params:")
        for trace_name, runs, n_sacc, n_micro in (
            ("left", left_runs, left_n_sacc, left_n_micro),
            ("right", right_runs, right_n_sacc, right_n_micro),
            ("conjunction", conj_runs, conj_n_sacc, conj_n_micro),
        ):
            other = len(runs) - n_sacc - n_micro
            print(
                f"    {trace_name:11s} {len(runs):6d} runs "
                f"({n_sacc} saccade, {n_micro} microsaccade, {other} fixation)"
            )
        print(f"    TOTAL (1 detector x 3 traces): {total_runs}")

        print(
            "  Part 3 -- stored label vs stored amplitude, and the "
            f"conjunction's amplitude shift (cut at {threshold} deg):"
        )
        for trace_name in ("left", "right", "conjunction"):
            events = measured[trace_name]
            amplitudes = np.array([amplitude_deg for _run, amplitude_deg in events])
            durations = np.array([run.stop - run.start for run, _a in events])
            print(
                f"    {trace_name:11s} {len(contradictions[trace_name])}/{len(events)} event "
                f"rows whose label contradicts their own amplitude; "
                f"median amplitude {np.median(amplitudes):.3f} deg over "
                f"{np.median(durations):.0f} samples"
            )

        # --- Part 2: scale-sensitivity sweep, mask RECOMPUTED per scale
        # (left eye only -- right/conjunction agree with it within a few
        # percent at the reference scale printed above, and are not re-swept
        # here to keep this test's own runtime small).
        print("  Part 2 -- scale-sensitivity sweep, left eye, mask recomputed per scale:")
        for mult in (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            scale = scale_ref * mult
            gaze, v, mask = _mask_and_velocity(
                raw["left"], quality["LeftDataQuality"], recording.fs_hz, recording.frame_gaps, scale
            )
            spans = _detect(gaze, v, mask, recording.fs_hz)
            runs, _, _ = _encode(mask, spans)
            frac_invalid = float(np.mean(mask == Label.INVALID))
            print(
                f"    x{mult:<4.2f} scale={scale:.6g}  frac_invalid={frac_invalid:.4f}  "
                f"runs={len(runs)}"
            )

    # The claim this whole test exists to check: identical spans under a
    # fixed mask, regardless of scale. If this ever fails, STOP -- per this
    # task's own brief -- and do not trust any number here until it is
    # explained; it would mean the scale-invariance argument in this test's
    # own docstring is wrong.
    # BOUNDARIES, not whole `Run`s: the detector now labels its own
    # intervals, and `classify` thresholds an ABSOLUTE degree amplitude, so
    # the same events split differently at 3x the scale -- printed above, and
    # exactly what this test's own docstring says is not scale-invariant.
    # Comparing whole `Run`s here would conflate the two claims and fail on
    # the one that was never made.
    assert _bounds(spans_b_fixed_mask) == _bounds(left_spans)

    # Finding H3, on REAL data rather than on the two synthetic boundary
    # cases `test_the_conjunction_inherits_the_detectors_own_minimum_
    # duration` covers: no stored conjunction event may be shorter than the
    # detector's own floor, and the raw intersection genuinely contains such
    # spans (402 of 4,952 when this was measured), so this is not vacuous.
    assert all(
        stop - start >= DEFAULT_EK_PARAMS.min_duration_samples
        for start, stop in _bounds(conjunction_spans)
    )
    assert any(
        stop - start < DEFAULT_EK_PARAMS.min_duration_samples
        for start, stop in _bounds(unfloored_spans)
    ), "the raw intersection has no sub-floor span, so the floor above proves nothing here"

    # THE assertion this fix round exists for, and it is over EVERY event row
    # of every trace rather than a sample of them: the previous rule's
    # contradiction rate was 12.3% on the conjunction and 0% on both eyes, so
    # a spot check would have found agreement on 7 rows out of 8 and reported
    # the trace healthy.
    #
    # `classify` over the STORED amplitude, not over a re-derivation of it:
    # design spec section 6.5 fits the main sequence from the stored
    # `amplitude_deg`/`peak_velocity_deg_s` pair while selecting rows by the
    # stored `label`, so a row saying `saccade` and storing 0.8 deg is what
    # actually poisons the fit, whatever any intermediate value said.
    for trace_name in ("left", "right", "conjunction"):
        offenders = contradictions[trace_name]
        assert not offenders, (
            f"{len(offenders)} of {len(measured[trace_name])} {trace_name} event rows store a "
            f"label their own amplitude contradicts, e.g. {offenders[0][0].label.value} at "
            f"{offenders[0][1]:.3f} deg against a {threshold} deg cut"
        )
    # Not vacuous: both labels really are produced on every trace, so the
    # loop above is deciding between two live answers on every row rather
    # than confirming a trace that only ever says one thing.
    for n_sacc, n_micro in (
        (left_n_sacc, left_n_micro), (right_n_sacc, right_n_micro), (conj_n_sacc, conj_n_micro)
    ):
        assert n_sacc and n_micro

    # A generous ceiling, not a pin (the measured total is printed above, and
    # is far below this): the point of this test is to SURFACE the number,
    # and asserting a tight figure nobody has measured before would invent a
    # precision this measurement does not have. 100,000 is roughly 2.7x the
    # measured total -- enough headroom for the sweep's own plausible range,
    # nowhere near the ~3.5M a truly broken encoder (one run per sample, on
    # every trace) would produce.
    assert total_runs < 100_000


def test_the_run_encoding_stays_far_below_one_run_per_sample(stepped_session):
    """A guard on the ENCODING alone, not on biological event rates: CI never
    has the real recording (`WLPP_OHDPI_REFERENCE`, above, is unset there), so
    without this the run/sample ratio is unguarded in CI specifically. This
    says nothing about how many runs a real session should have, and nothing
    about the figure `test_the_run_count_measured_against_the_reference_
    recording` measures above -- only that `runs_from_labels`' own
    maximal-run guarantee holds on a real populated table, so a regression
    that stopped merging adjacent same-label samples (e.g. one run per
    sample) would fail here, loudly, long before anyone reached for the
    design spec's own number.
    """
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session
    # Every detector: the run/sample ratio is a claim about the ENCODING, and
    # a detector that fired on nearly every sample would blow past this bound
    # whichever one it is.
    for name, trace in [(n, t) for n in _detector_names()
                        for t in ("left", "right", "conjunction")]:
        where = {**session_key, "trace": trace, **_detector(name)}
        row = (detect.EyeDetection & where).fetch1()
        runs = (detect.EyeDetection.Run & where).to_dicts()
        # "Far below" deliberately loose: `stepped_session` plants exactly 3
        # (left/conjunction) or 4 (right, via `_inject_right_eye_only_step`)
        # events in an otherwise-still trace, so a healthy encoding merges
        # long fixation stretches into single runs and lands near a dozen
        # runs total, not thousands -- only a broken encoder, or a detector
        # firing on nearly every sample, would come anywhere near this bound.
        assert len(runs) < row["n_samples"] / 10, (name, trace)


def _delete_fixture_paramset(name: str) -> None:
    """Both multi-kind tests' own cleanup, keyed by the fixture's NAME rather
    than a paramset index the caller captured earlier -- and that difference
    is the fix, not a stylistic choice (fix round 2, on top of fix round 1's
    own pin).

    **Round 1 captured `idx` by calling the patched `register_default_
    paramsets()` as the first statement inside the `with` block, BEFORE
    `try`.** That call is exactly what INSERTS this fixture's `ParamSet` row
    (`_register_default_paramsets_including`'s own dict comprehension), and
    it runs the round-1 pin assertion LAST, after the insert. So the one
    scenario the pin exists to catch -- the real `register_default_
    paramsets` diverging from this hand copy -- raised from BEFORE `try`,
    `finally` never ran, and the just-inserted row leaked. Confirmed
    directly (review's own reproduction, and re-confirmed here before this
    fix was written): mutate the copy's `engbert_kliegl` defaults, force the
    pin to fire, and the fixture's `ParamSet` row survives the test.

    **The fix moves `idx`'s own capture to the FIRST statement INSIDE
    `try`** (see both tests below), so a pin failure there -- which happens
    strictly after the insert, since the insert is what the very call
    computing `idx` performs -- now unwinds through `finally`. But `finally`
    itself must not need to already know `idx`: if the pin failed while
    computing it, the LOCAL VARIABLE `idx` in the test's own frame was never
    assigned (the assignment's right-hand side raised before binding), so a
    `finally` that referenced it would raise a SECOND, masking exception
    (`UnboundLocalError`) instead of running the delete at all -- exactly
    the "early failure must not raise a second exception out of `finally`"
    hazard this round's own instructions warn about.

    **So this cleanup does not take `idx` as an argument, and does not
    write anything before it deletes.** It reads `paramset.ParamSet`
    (`eye_detection` rows only), filters in Python for whichever one (if
    any) has THIS fixture's own name in its stored `params['detector']`, and
    deletes that row if one exists. A pure SELECT plus a conditional
    DELETE-of-what-was-found: nothing here can insert, so nothing here can
    leave a partial write for a later step to depend on (fix round 1's own
    concern, about `_detector`/`register_default_paramsets` as `finally`'s
    first statement, stays fixed rather than reappearing in a new shape) --
    and if the fixture's row was never inserted at all, the filter simply
    finds nothing and this returns having done nothing, rather than raising.
    """
    from wl_preproc.schema import paramset

    rows = (paramset.ParamSet & {"paramset_type": "eye_detection"}).to_dicts()
    stale = [row for row in rows if row["params"].get("detector") == name]
    for row in stale:
        (paramset.ParamSet
         & {"paramset_type": "eye_detection", "paramset_idx": row["paramset_idx"]}).delete()


def test_a_multi_kind_detector_populates_and_keeps_its_vocabulary(stepped_session, prefix):
    """The four blocked detectors, in the only form that exists today.

    Registered through `DETECTORS` and driven by `daemon.run_once()`, never
    by `make()` -- stage 1's worst defect was that nothing in production
    registered the detection paramsets, so both `key_source`s named zero
    candidates and the subsystem wrote no rows while every test that
    registered its own paramsets passed.

    The fixture's vocabulary below, `{saccade, pso, fixation}`, is not an
    arbitrary shape: it is exactly Nystrom-Holmqvist's own declared
    vocabulary (design spec `2026-09-05-conjunction-shape-design.md` section
    3, "What each detector produces") -- one of the four detectors this task
    exists to prove are unblocked.

    **`register_default_paramsets` is patched for the span of this test, and
    that means this test does NOT drive the real function's own handling of
    a newly registered name -- only Tasks 1-4's own conjunction-shape code
    does, through the real, unpatched `daemon.run_once()`.** The real
    function's `defaults` dict cannot know this fixture's name (see
    `_register_default_paramsets_including`'s own docstring for why, and for
    the `KeyError` that is out of this task's scope to fix); the patch exists
    only so a fixture detector CAN be registered at all. What is not skipped:
    the patch's own `engbert_kliegl`/`otero_millan` handling is asserted
    equal to the real function's, every time it runs, so a future change to
    the real function that this hand copy fails to follow is caught here
    rather than silently trusted.

    **`DETECTORS` and `register_default_paramsets` are both restored via
    `unittest.mock.patch` context managers, not a bare try/finally.**
    `DETECTORS` is process-wide module state shared across the whole test
    session -- `test_the_registered_paramsets_match_the_detector_registry`
    and every `_detector_names()`-driven test in this file read it live -- so
    a leaked entry here would corrupt the registry's own completeness claim
    in unrelated tests, and could make test order significant. A correctly
    written `try/finally` around the body below would restore just as
    reliably on any exception raised inside it; `patch.dict`/`patch.object`
    are used instead because they give that identical guarantee while
    matching a precedent this codebase already has for the exact same
    dict (`tests/schema/test_consensus_populate.py:466`'s
    `patch.dict(DETECTORS, {"otero_millan": narrowed})`), rather than
    hand-rolling a second mechanism, with a second chance to misname the key
    in a `del`, for the same job.

    **The fixture's OWN `EyeDetection` rows are deleted before this test
    ends, GLOBALLY rather than scoped to `stepped_session` alone, and that
    is load-bearing rather than tidiness.** `EyeDetection.key_source` crosses
    every `eye_validity` row EVER computed in this shared `t_` schema against
    every `eye_detection` paramset -- not only `stepped_session`'s own -- so
    registering `"two_kinds"` and calling `daemon.run_once()` computes it for
    every OTHER module-scoped session this file's own earlier tests already
    realized too (confirmed directly: `populated` on this call is not
    explained by `stepped_session`'s three traces alone). `DETECTORS` itself
    unwinds cleanly on its own, but the ROWS those other sessions' detections
    wrote do not, and `consensus.DetectorAgreement.key_source` crosses every
    PAIR of `EyeDetection` rows regardless of which session they came from --
    so a later test in THIS file registering a different fixture name found
    `DetectorNotRegistered` on `"two_kinds"` (this task's own investigation,
    before this cleanup existed): a stale `EyeDetection` row this fixture
    left behind was still `status='computed'`, `DetectorAgreement`'s
    key_source paired it against the newer fixture, and `get_detector
    ("two_kinds")` failed because `DETECTORS` -- correctly -- no longer had
    it. Deleting by this fixture's own registered `paramset_idx` alone, with
    no session restriction, removes every `EyeDetection` row this fixture
    ever wrote and cascades (a real DataJoint dependency, not an ad hoc
    join: `consensus.py::DetectorAgreement` declares `-> detect.EyeDetection
    .proj(paramset_a='paramset_idx')` and `-> ...proj(paramset_b=...)`
    twice) to every `DetectorAgreement` row that named it, in either
    position, for any session -- which is what makes this fixture leave no
    candidate for a later `key_source` to ever cross again.
    """
    from dataclasses import replace
    from unittest.mock import patch

    from wl_preproc import daemon
    from wl_preproc.eye.detect.labels import Label, LabelledInterval
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import detect

    def detect_two_kinds(
        gaze_deg, velocity_deg_s, available, fs_hz, params: EngbertKlieglParams
    ):
        """A saccade with a glissade on its tail, in both eyes, at the same
        samples -- the lens ringing this instrument's own design spec says
        this rig records after every real saccade (`2026-08-31-saccade-
        detection-design.md` section 2.5)."""
        return [
            LabelledInterval(1000, 1060, Label.SACCADE),
            LabelledInterval(1060, 1090, Label.PSO),
        ]

    fake = replace(
        DETECTORS["engbert_kliegl"],
        name="two_kinds",
        vocabulary=frozenset({Label.SACCADE, Label.PSO, Label.FIXATION}),
        run=detect_two_kinds,
    )
    session_key, _report, _ = stepped_session

    with (
        patch.dict(DETECTORS, {"two_kinds": fake}),
    ):
        try:
            # First statement INSIDE `try`, not before it: this call is
            # what INSERTS the fixture's own `ParamSet` row, and an insert
            # that happens outside the region `finally` protects leaks the
            # row when anything after it raises.
            #
            # **This is now the REAL `register_default_paramsets`.** It was a
            # hand copy of that function, patched over it for the span of
            # these two tests, because the real one kept a hardcoded
            # `defaults` dict that raised `KeyError` for any name not in it.
            # A test driving a copy of the thing it claims to exercise is
            # stage 1's worst defect in miniature -- there, every test
            # registered its own paramsets, which is exactly why nobody
            # noticed production registered none. `registry.Detector` now
            # carries its own defaults, so the real function handles a
            # fixture detector with no special case and the copy is gone.
            idx = detect.register_default_paramsets()["two_kinds"]

            daemon.run_once(prefix=prefix)

            rows = (
                detect.EyeDetection.Run
                & {**session_key, "trace": "conjunction", "paramset_idx": idx}
            ).to_dicts(order_by="run_index")

            labels = {r["label"] for r in rows}
            # THE shape rule: the conjunction's vocabulary is the detector's.
            assert "pso" in labels, "the binocular glissade was dropped or renamed"
            assert "saccade" in labels

            pso_rows = [r for r in rows if r["label"] == "pso"]
            assert len(pso_rows) == 1
            assert (pso_rows[0]["run_start"], pso_rows[0]["run_stop"]) == (1060, 1090)

            # A `pso` run stores NULL amplitude -- `_run_row` measures only
            # `saccade`/`microsaccade` (design spec section 4) -- so the
            # 12.3% label/amplitude contradiction stage 1 found (section 1.1)
            # is unreachable for it. There is no amplitude to contradict.
            assert pso_rows[0]["amplitude_deg"] is None
            assert pso_rows[0]["peak_velocity_deg_s"] is None

            saccade_rows = [r for r in rows if r["label"] == "saccade"]
            assert saccade_rows[0]["amplitude_deg"] is not None
        finally:
            # By NAME, not by the `idx` above -- which this block must not
            # depend on, since a pin failure while computing it means `idx`
            # was never bound in this frame at all. Deleting `EyeDetection`
            # alone (this task's own first attempt, before either fix round)
            # leaves the paramset registered with no detection to show for
            # it, so `EyeDetection.key_source` reads it as PENDING again on
            # every later `daemon.run_once()` in the suite -- and forever
            # failing, since `DETECTORS["two_kinds"]` is gone by then.
            # Deleting the `ParamSet` row instead cascades (a real
            # dependency: `EyeDetection` declares a bare `-> paramset.
            # ParamSet` for its detector reference) through `EyeDetection`,
            # `EyeDetection.Run` and any `DetectorAgreement` row that named
            # it, in one call, and removes it from `key_source` entirely
            # rather than merely un-computing it.
            _delete_fixture_paramset("two_kinds")


def test_a_multi_kind_detector_writes_all_three_traces(stepped_session, prefix):
    """`EyeDetection.make()` inserts `left` and `right` BEFORE the conjunction
    branch, and DataJoint's `AutoPopulate._populate1` wraps the whole call in
    a transaction and cancels it on any exception. So the old raise did not
    yield per-eye data awaiting a conjunction -- it yielded NO rows at all,
    silently, with the failure visible only in `run_once`'s error list.

    This asserts the outcome that failure mode denied. See
    `test_a_multi_kind_detector_populates_and_keeps_its_vocabulary` above for
    why the fixture's vocabulary is shaped the way it is, why
    `register_default_paramsets` and `DETECTORS` are both patched here rather
    than mutated with a bare try/finally, and why this fixture's own
    `EyeDetection` rows are deleted globally (not scoped to `stepped_session`)
    before the test ends -- a distinct name from that test's own `"two_kinds"`
    (`"two_kinds_traces"`) so the two never collide mid-suite, but the exact
    same GLOBAL cleanup either one skipping would leave for the other to find
    as a stale, no-longer-registered `DetectorAgreement` pairing.
    """
    from dataclasses import replace
    from unittest.mock import patch

    from wl_preproc import daemon
    from wl_preproc.eye.detect.labels import Label, LabelledInterval
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import detect

    def detect_two_kinds(
        gaze_deg, velocity_deg_s, available, fs_hz, params: EngbertKlieglParams
    ):
        return [
            LabelledInterval(1000, 1060, Label.SACCADE),
            LabelledInterval(1060, 1090, Label.PSO),
        ]

    fake = replace(
        DETECTORS["engbert_kliegl"],
        name="two_kinds_traces",
        vocabulary=frozenset({Label.SACCADE, Label.PSO, Label.FIXATION}),
        run=detect_two_kinds,
    )
    session_key, _report, _ = stepped_session

    # See the sibling test's own comment above its identical line for why
    # this is captured before `"two_kinds_traces"` is added to `DETECTORS`.
    real_registered = detect.register_default_paramsets()

    with (
        patch.dict(DETECTORS, {"two_kinds_traces": fake}),
    ):
        try:
            # See the sibling test's own comment above its identical line
            # (fix round 2) for why this is the first statement INSIDE
            # `try` rather than before it.
            idx = detect.register_default_paramsets()["two_kinds_traces"]

            report = daemon.run_once(prefix=prefix)
            errors = report["errors"]

            traces = set(
                (detect.EyeDetection & {**session_key, "paramset_idx": idx})
                .to_arrays("trace")
            )
            assert traces == {"left", "right", "conjunction"}
            # `daemon.py::run_once` formats each error as
            # `f"{table.__name__} {key}: {err}"` (confirmed by reading it),
            # and `key` carries `paramset_idx` (on an `EyeDetection` error) or
            # `paramset_a`/`paramset_b` (on a `DetectorAgreement` one) -- an
            # INT, never this fixture's NAME. Matching on `"two_kinds_traces"`
            # itself (this task's first draft) happens to catch the exact
            # regression this test is named for -- `DetectorNotRegistered`
            # spells out every currently-registered name, including this
            # one, in its own message -- but would silently let through any
            # OTHER error this fixture caused, `UnknownLabelKind` among them,
            # since nothing about that exception mentions this fixture by
            # name. Matching on `idx` the way the daemon actually renders it
            # is what makes this assertion mean what it appears to mean; the
            # `traces` assertion above remains the one that actually pins
            # ALL THREE traces got written, which no error-string match can
            # substitute for.
            assert not [
                e for e in errors
                if f"'paramset_idx': {idx}" in e
                or f"'paramset_a': {idx}" in e
                or f"'paramset_b': {idx}" in e
            ]
        finally:
            # See the sibling test's own `finally`, and `_delete_fixture_
            # paramset`'s own docstring, for why this is by NAME rather
            # than by the `idx` above, which this block must not depend on.
            _delete_fixture_paramset("two_kinds_traces")


def test_nystrom_holmqvist_populates_all_three_traces(stepped_session, prefix):
    """The first REAL multi-kind detector to reach the conjunction. Until
    2026-09-05 a `pso`-emitting detector could not produce a conjunction
    trace at all -- `_conjunction_label` raised, and because DataJoint wraps
    `make()` in a transaction and cancels it on any exception, it wrote no
    rows whatsoever, not even the per-eye traces inserted before the raise.

    Through `daemon.run_once()`, never `make()` by hand: stage 1's worst
    defect was that nothing in production registered the detection paramsets,
    and it stayed invisible because every test registered its own."""
    from wl_preproc import daemon
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session

    daemon.run_once(prefix=prefix)

    traces = set(
        (detect.EyeDetection & {**session_key, **_detector("nystrom_holmqvist")})
        .to_arrays("trace")
    )
    assert traces == {"left", "right", "conjunction"}


def test_the_conjunction_of_a_pso_detector_can_carry_pso(stepped_session, prefix):
    """The shape rule, on a real detector: the conjunction's vocabulary is
    the detector's. A binocular glissade is stored as `pso`, not folded into
    a saccade and not dropped.

    Asserted as a SUBSET rather than a presence, because whether the planted
    fixture produces a BINOCULAR glissade is a property of the fixture, not
    of the rule under test. `test_a_multi_kind_detector_populates_and_keeps_
    its_vocabulary` already pins presence on a fixture built to guarantee
    it."""
    from wl_preproc import daemon
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session

    daemon.run_once(prefix=prefix)

    labels = set(
        (
            detect.EyeDetection.Run
            & {**session_key, "trace": "conjunction",
               **_detector("nystrom_holmqvist")}
        ).to_arrays("label")
    )
    assert labels <= {"saccade", "pso", "fixation", "blink", "invalid"}
    assert "microsaccade" not in labels, (
        "this detector's saccadic slice is `{saccade}` alone, so `classify` "
        "must never be asked and `microsaccade` must never be stored"
    )


def test_registering_a_third_detector_does_not_raise(daemon_module):
    """The defect this fix exists to close. `register_default_paramsets` kept
    a hardcoded `defaults` dict of the two real detector names and did
    `defaults[name]` in a comprehension over `DETECTORS`, so a third detector
    raised `KeyError` -- uncaught, from `daemon.run_once()` at
    `daemon.py`'s `register_default_paramsets()` call, which sits BEFORE
    `reap_stale_jobs` and before the try-wrapped `_computed_tables()` loop.
    The whole pass died, not one detector.

    Stage 2B registers five more (design spec section 3.1), so this fired on
    the first one. It stayed invisible because the registry's own
    completeness claim -- set equality against `DETECTORS` -- did not cover
    the defaults, which were a THIRD thing that had to agree with the other
    two and that nothing checked.

    Defaults now live on `registry.Detector` itself, so a detector that has
    none cannot be constructed at all, and the failure is a `TypeError` at
    import naming the detector rather than a `KeyError` at daemon time
    naming nothing."""
    from dataclasses import dataclass, replace

    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import detect, paramset

    @dataclass(frozen=True, slots=True)
    class _ThirdParams:
        some_threshold: float = 0.25

    third = replace(
        DETECTORS["engbert_kliegl"],
        name="a_third_detector",
        vocabulary=frozenset({Label.SACCADE}),
        defaults=_ThirdParams(),
    )
    DETECTORS["a_third_detector"] = third
    try:
        registered = detect.register_default_paramsets()

        assert set(registered) == set(DETECTORS), (
            "the returned mapping is the subsystem's completeness claim and must "
            "cover every registered detector"
        )
        params = (paramset.ParamSet & {
            "paramset_type": "eye_detection",
            "paramset_idx": registered["a_third_detector"],
        }).fetch1("params")
        assert params["detector"] == "a_third_detector"
        assert params["some_threshold"] == 0.25
    finally:
        del DETECTORS["a_third_detector"]


def test_the_shared_threshold_still_wins_over_a_detectors_own_field():
    """`microsaccade_max_deg` is merged LAST, after the detector's own
    defaults, and that ordering is load-bearing while being invisible in any
    stored row.

    A detector that consumes the shared key declares a field of that name on
    its own params dataclass (`_params_for` explains why that is how a shared
    key reaches a detector at all), so `asdict` of its defaults carries a
    `microsaccade_max_deg` of its own. Merged in the other order THAT value
    would win, and each detector would quietly pick the amplitude cut its own
    rows are split at -- the per-detector threshold this paramset shape exists
    to prevent.

    The two values are equal today, so reversing the merge order would fail
    NO existing test. This is the test that would."""
    from dataclasses import dataclass, replace

    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import detect

    shadowing_value = MICROSACCADE_MAX_DEG + 4.0

    @dataclass(frozen=True, slots=True)
    class _ShadowingParams:
        microsaccade_max_deg: float = shadowing_value

    shadower = replace(
        DETECTORS["engbert_kliegl"],
        name="tries_to_shadow",
        vocabulary=frozenset({Label.SACCADE}),
        defaults=_ShadowingParams(),
    )
    DETECTORS["tries_to_shadow"] = shadower
    try:
        built = detect._eye_detection_params(shadower)

        assert built["microsaccade_max_deg"] == MICROSACCADE_MAX_DEG, (
            f"the detector's own {shadowing_value} shadowed the shared cut; the "
            "merge order is reversed"
        )
    finally:
        del DETECTORS["tries_to_shadow"]
