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

import datetime

import numpy as np
import pytest

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
    from wl_preproc.synth.ohdpi import HEADER, OHDPI_FPS, OHDPI_PRE_ROLL_S

    (ohdpi_txt,) = (session_dir / "ohdpi").glob("*.txt")
    column = HEADER.index("RightCR4X")
    lines = ohdpi_txt.read_text(encoding="utf-8").splitlines()
    header_line, data_lines = lines[0], lines[1:]

    row_start = round((_PHANTOM_ONSET_S + OHDPI_PRE_ROLL_S) * OHDPI_FPS)
    for offset in range(_PHANTOM_N_SUBSTEPS):
        frac = (offset + 0.5) / _PHANTOM_N_SUBSTEPS
        fields = data_lines[row_start + offset].split(" ")
        fields[column] = f"{_PHANTOM_STEP_PX * frac:.4f}"
        data_lines[row_start + offset] = " ".join(fields)

    ohdpi_txt.write_text("\n".join([header_line, *data_lines]) + "\n", encoding="utf-8")


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
    """
    from wl_preproc import daemon
    from wl_preproc.schema import detect

    daemon.activate_all(prefix=prefix)
    detect.register_default_paramsets()
    return daemon


@pytest.fixture(scope="module")
def stepped_session(daemon_module, prefix, tmp_path_factory):
    """A hold, a ramp, a hold, a ramp, a hold, a ramp, a hold -- three
    planted, known-onset transitions in an otherwise still trace, following
    a calibration region built exactly like `test_eye_populate.py::
    round_trip_session` (a fitted affine against the ordinary, untouched
    two-frequency drift).

    Five trials of `TRIAL_DURATION_S` each. Trials 0-3 supply calibration
    windows (natural drift, no `eye_fixations` override, matching `round_trip
    _session`'s own construction so calibration success is proven behaviour
    rather than a fresh guess). Trial 4 is reserved entirely for detection:
    held at A, ramped to B (big), held at B, ramped to C (small -- a genuine
    microsaccade), held at C, ramped back to A (big) -- three transitions,
    this fixture's three planted onsets.
    """
    from wl_preproc.contracts.events import TaskTypeCode
    from wl_preproc.schema import core, timebase
    from wl_preproc.synth.recipe import BlockSpec, EyeFixationSpec, MontageSpec, SessionRecipe
    from wl_preproc.synth.session import generate_session

    from tests.schema.test_eye_populate import _expected_raw_points, _land, _row_for_time, _write_fixations

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
        session_id="2027-06-01_01",
        subject="detstep1",
        rig="rig-a",
        systems=("syncbox", "ohdpi"),
        blocks=(
            BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=n_trials, trial_duration_s=TRIAL_DURATION_S),
        ),
        montages=(MontageSpec(start_s=0.0, end_s=n_trials * TRIAL_DURATION_S),),
        n_ap_channels=4,
        ap_sample_rate_hz=30_000.0,
        seed=601,
        eye_fixations=tuple(detect_fixations),
    )

    root = tmp_path_factory.mktemp("detectstep")
    truth = generate_session(root, recipe)
    session_dir = root / recipe.session_id
    _inject_right_eye_only_step(session_dir)
    session_key = _land(
        root, recipe, datetime.datetime(2027, 6, 1, 9, 0),
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

    report = daemon_module.run_once(prefix=prefix)

    planted_onsets = [_row_for_time(segment, onset_s) for onset_s in onset_times]
    return session_key, report, planted_onsets


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


def test_a_planted_step_is_detected_at_its_planted_time(stepped_session):
    """The round-trip that matters. `SessionRecipe.eye_fixations` holds gaze
    at stated raw positions, so a hold-step-hold session has ground-truth
    onsets, and a detector that found events at the wrong times passes every
    count-based test and fails this one."""
    from wl_preproc.schema import detect

    session_key, _report, planted_onsets = stepped_session
    runs = (detect.EyeDetection.Run & {**session_key, "trace": "left"}).to_dicts(
        order_by="run_index"
    )
    onsets = [r["run_start"] for r in runs if r["label"] in ("saccade", "microsaccade")]

    assert len(onsets) == len(planted_onsets)
    for got, want in zip(onsets, planted_onsets, strict=True):
        assert abs(got - want) <= 5


def test_the_runs_tile_the_whole_trace(stepped_session):
    """The structural invariant, asserted on real populated rows and not only
    in the encoder's unit tests."""
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session
    row = (detect.EyeDetection & {**session_key, "trace": "left"}).fetch1()
    runs = (detect.EyeDetection.Run & {**session_key, "trace": "left"}).to_dicts(
        order_by="run_index"
    )

    assert runs[0]["run_start"] == 0
    assert runs[-1]["run_stop"] == row["n_samples"]
    for earlier, later in zip(runs, runs[1:], strict=False):
        assert earlier["run_stop"] == later["run_start"]
        assert earlier["label"] != later["label"]


def test_saccade_runs_carry_measurements_and_others_do_not(stepped_session):
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session
    for run in (detect.EyeDetection.Run & {**session_key, "trace": "left"}).to_dicts():
        if run["label"] in ("saccade", "microsaccade"):
            assert run["amplitude_deg"] is not None
            assert run["peak_velocity_deg_s"] is not None
        else:
            assert run["amplitude_deg"] is None


def test_the_middle_planted_step_is_classified_as_a_microsaccade(stepped_session):
    """The brief's own mutation check ("drop the `classify` call so every
    event is a `saccade`") is invisible to every OTHER test in this file --
    none of them distinguishes the two labels, only groups them together.
    `stepped_session`'s own middle transition (`_STEPS_PX[1]`, ~0.7 deg) is
    planted specifically to be classified `microsaccade`, and the two either
    side `saccade`, so this is the assertion that mutation actually breaks.
    """
    from wl_preproc.schema import detect

    session_key, _report, planted_onsets = stepped_session
    runs = (detect.EyeDetection.Run & {**session_key, "trace": "left"}).to_dicts(
        order_by="run_index"
    )
    events = [r for r in runs if r["label"] in ("saccade", "microsaccade")]

    assert len(events) == len(planted_onsets) == 3
    assert [e["label"] for e in events] == ["saccade", "microsaccade", "saccade"]
    assert events[1]["amplitude_deg"] < 1.0


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
            for r in (detect.EyeDetection.Run & {**session_key, "trace": trace}).to_dicts()
            if r["label"] in ("saccade", "microsaccade")
        ]

    both = saccade_spans("conjunction")
    left = saccade_spans("left")
    right = saccade_spans("right")
    assert both
    for start, stop in both:
        assert any(ls < stop and start < lstop for ls, lstop in left)

    # The phantom event: present in `right` alone.
    phantom_row = round(
        (_PHANTOM_ONSET_S + 0.6) * 500.0
    )  # OHDPI_PRE_ROLL_S, OHDPI_FPS -- see `_inject_right_eye_only_step`.
    assert any(rs < phantom_row + _PHANTOM_N_SUBSTEPS and phantom_row < rstop for rs, rstop in right)
    assert not any(ls < phantom_row + _PHANTOM_N_SUBSTEPS and phantom_row < lstop for ls, lstop in left)
    assert not any(
        cs < phantom_row + _PHANTOM_N_SUBSTEPS and phantom_row < cstop for cs, cstop in both
    )


def test_a_session_with_no_calibration_is_refused_with_a_reason(uncalibrated_session):
    """Detection reads gaze as a computation, so no calibration means no
    gaze. A refused row with a stated reason, never an error and never an
    empty success."""
    from wl_preproc.schema import detect

    session_key, report = uncalibrated_session
    assert not any(
        "EyeValidity" in message or "EyeDetection" in message for message in report["errors"]
    )

    rows = (detect.EyeDetection & session_key).to_dicts()

    assert rows
    for row in rows:
        assert row["status"] == "refused"
        assert "calibration" in row["reason"]
        assert row["n_samples"] is None


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

    labels = validity_labels(gaze, velocity_deg_s, quality, (), DEFAULT_VALIDITY_PARAMS)

    present = {label for label in labels if label is not None}
    assert Label.FIXATION not in present
    assert present <= {Label.BLINK, Label.INVALID}
    # Not vacuous: both real verdicts this fixture plants actually appear.
    assert Label.BLINK in present
    assert Label.INVALID in present


def test_params_for_builds_the_detectors_own_dataclass_and_drops_extra_keys():
    """Ruling 1 (`_params_for` is undefined in the brief) and Ruling 3
    (`microsaccade_max_deg` lives in the SAME `eye_detection` paramset dict
    as the detector's own parameters) interact: a real paramset carries a
    `detector` selector AND a subsystem-wide key the detector's own dataclass
    does not declare, and `_params_for` must drop both -- not only
    `detector`, which is all the brief's own prose names."""
    from dataclasses import asdict

    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS, EngbertKlieglParams
    from wl_preproc.eye.detect.registry import get_detector
    from wl_preproc.schema.detect import _params_for

    raw = {"detector": "engbert_kliegl", "microsaccade_max_deg": 1.0, **asdict(DEFAULT_EK_PARAMS)}
    built = _params_for(get_detector("engbert_kliegl"), raw)

    assert isinstance(built, EngbertKlieglParams)
    assert built == DEFAULT_EK_PARAMS


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
