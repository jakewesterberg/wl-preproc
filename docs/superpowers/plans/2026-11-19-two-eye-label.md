# A Two-Eye Row's Label From the Measurement It Stores: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** every stored saccadic row of the two-eye (conjunction) trace carries `classify` over its own stored
amplitude. This must hold beside a carried loss, on either eye, in either geometry.

**Architecture:** one module-level function in `schema/detect.py` measures a two-eye span exactly as `_run_row`
stores it: the same eye choice, the same `measure_event_run` call. `_conjunction_label` labels from it, and
`_run_row` stores it. `make()`'s conjunction assembly moves into one function, so the stage, the synthetic tests
and the reference test all build the trace with the same code.

**Tech Stack:** Python 3.11 and 3.13, numpy, DataJoint 2.3.3 (no schema change), pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-two-eye-label-design.md` (approved 2026-11-19).

## Global Constraints

- No change to how any row is measured, which eye measures it, the per-eye traces, the fallback rules or span
  intersection (spec Non-goals).
- Never measure two-eye rows on the bare intersection to make a label right. Never `classify` rows after encoding
  (spec Anti-goals).
- The reference test's assertion stays at 0 contradictions on every trace; it is never loosened.
- A span no eye can measure keeps today's label: `classify` over the bare intersection on the left eye (spec
  Decided).
- Compare floats that pass through trig (true-angle) to a tolerance, never exactly (lab memory: CI rounds trig
  otherwise).
- Never commit the OpenIris recording, the Andersson data set or the NSLR/BMD references. Leave the stray
  `wl-preproc` symlink at the repo root out of commits.
- The reference test runs with `WLPP_OHDPI_REFERENCE=$HOME/.cache/wl-preproc-references/OpenIris-2024Jul31-114628.txt`.
- Clear `__pycache__` before a mutation run (stale `.pyc`).

## Review Focus

1. A two-eye span whose left eye lost data inside it while the right measured throughout. The row is labelled from
   the right eye's stored size. This is 5 of the 7 real offenders; Task 2's `test_..._on_the_eye_that_measures_it`.
2. A span whose take-off a carried loss hides on the eye that measures it. The row is labelled from the widened
   size. This is the other 2; Task 2's `test_..._widened_across_a_loss`, in both geometries.
3. A span no eye can measure. It is stored unmeasured, labelled as today; Task 2's `test_a_span_no_eye_measures_...`.
4. A detector declaring half the cut (U'n'Eye `{saccade}`) is unchanged when both eyes are given. Task 2's
   `test_a_half_cut_detector_...`.
5. A split piece of a two-eye span (`_conjunction_parts`, around a kept run) can't occur for a size-cut detector:
   Engbert-Kliegl's runs are maximal (`true_runs`), Otero-Millan's `_merge` leaves a gap, and BMD's own events are
   bordered by `drift`. So no saccadic runs of one eye touch, and `_conjunction_runs` never coalesces across two.
   Ruling, stated in `_conjunction_parts`' docstring; no code. If wrong, a piece's label is its span's.

---

### Task 1: the conjunction built by one function, and the reference test counting stored rows

**Files:**
- Modify: `wl_preproc/schema/detect.py` (`EyeDetection.make`'s conjunction block, about lines 795-830).
- Modify: `tests/schema/test_detect_populate.py` (`_event_amplitudes`, Part 3 of
  `test_the_run_count_measured_against_the_reference_recording`, its docstring's Part 3 paragraph).

**Interfaces:**
- Produces: `_conjunction_trace(spans: dict[str, list[Run]], per_eye: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]], detector, params: dict, detector_params, fs_hz: float) -> tuple[list[Run], list[tuple[str, Run]], np.ndarray, np.ndarray]`.
  It returns `_conjunction_parts`' `(intervals, kept, source, fill)`, built exactly as `make()` builds it today:
  the floor `_min_duration_samples(detector_params, fs_hz)`, `_conjunction_runs` with
  `_conjunction_label(detector, params, per_eye["left"][0])`, then `_conjunction_parts`. `per_eye[eye]` is
  `(gaze, measured_v, offered)`, as `make()` holds it. `make()` calls it, then `_insert_trace` and the `Source`
  insert as now.
- Produces (tests): `_stored_rows(detector_name: str, raw, loss, recording, scale: float, geometry: str) -> dict[str, list[dict]]`
  in the test module. Each trace's captured `Run` rows (`_CapturedInserts`) come from the stage's own path:
  - `apply_map`, then `validity_labels` with `DEFAULT_VALIDITY_PARAMS`;
  - `fill_lost(gaze, offered, frame_numbers(...), rows_across_gaps(...))`, then `velocity`;
  - `local_velocity` under true-angle;
  - `detector.detect` with `_params_for(detector, params)`, where `params = {**_eye_detection_params(detector), "geometry": geometry}`;
  - `EyeDetection._insert_trace` per eye;
  - `_conjunction_trace`, then `_insert_trace(..., "conjunction", left gaze, left v, fill, intervals, ..., kept={(r.start, r.stop): (e, *per_eye[e]) for e, r in kept}, eyes=per_eye, geometry=geometry)`.

  The spike `~/.cache/wl-preproc-scratch/2026-11-19-conjunction-label/spike-contradictions.py` is this, outside
  the test.

- [ ] **Step 1: Rewrite Part 3 to count stored rows**

Replace `measured = {...}` and `contradictions = {...}` with rows from
`_stored_rows("engbert_kliegl", raw, loss, recording, scale_ref, DEFAULT_GEOMETRY)`. A row contradicts when its
`label` is `saccade` or `microsaccade`, its `amplitude_deg` is not None, and
`classify(amplitude_deg, threshold).value != label`. Keep the printed line per trace (count over events, median
amplitude and duration from the rows' `amplitude_deg` and `run_stop - run_start`) and the final `assert not offenders`
loop over `("left", "right", "conjunction")`. Delete `_event_amplitudes`, which is then unused. Parts 1, 2 and H3
are unchanged.

- [ ] **Step 2: Run it to see it fail on the conjunction alone**

Run: `WLPP_OHDPI_REFERENCE=$HOME/.cache/wl-preproc-references/OpenIris-2024Jul31-114628.txt .venv/bin/python -m pytest tests/schema/test_detect_populate.py::test_the_run_count_measured_against_the_reference_recording -q -s`
Expected: FAIL with `6 of 4710 conjunction event rows store a label their own amplitude contradicts`. The printed
Part 3 lines show left `0/5012` and right `0/4865`.

- [ ] **Step 3: Extract `_conjunction_trace` and call it from `make()`**

This is a move, not a change. `make()`'s behaviour is identical.

- [ ] **Step 4: Run the eye-detection tests**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py tests/eye/detect -q -p no:cacheprovider`
Expected: all pass, except the reference test failing exactly as in Step 2 when the variable is set.

- [ ] **Step 5: Update the docstring's Part 3 paragraph**

The old figures (12.3%, "seven rows in eight") stay as history. Add that since 2026-11-19 Part 3 counts the rows
the stage's own code stores, on gaze filled as the stage fills it. A test helper measuring on its own had drifted
twice: plain `measure` where the stage calls `measure_event_run`, and unfilled gaze.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/detect.py tests/schema/test_detect_populate.py
git commit -m "test(detect): the reference recording's label check counts the rows the stage's own code stores ..."
```

### Task 2: a two-eye span labelled from the measurement it stores

**Files:**
- Modify: `wl_preproc/schema/detect.py` (`_conjunction_label`, `_insert_trace`'s `_measured_on`/`_run_row`,
  `_conjunction_trace`, the docstrings of `_conjunction_label`, `_insert_trace` and `_conjunction_parts`).
- Test: `tests/schema/test_detect_populate.py`, beside `test_the_conjunction_label_matches_the_amplitude_that_gets_stored`.

**Interfaces:**
- Consumes: `_conjunction_trace` (Task 1).
- Produces:
  - `_two_eye_eye(start: int, stop: int, eyes: dict) -> str | None`: the eye `_measured_on` picks for a span that
    no kept run covers. That is the left eye if its mask is all `None` over `[start, stop)`, else the right one;
    failing both, the left then the right eye whose `may_label` is all true there; otherwise `None`. It is moved
    out of `_measured_on`, which calls it.
  - `_two_eye_measurement(start: int, stop: int, eyes: dict, fs_hz: float, *, geometry: str, min_measured_ms: float | None) -> Measurement | None`:
    `None` when `_two_eye_eye` is `None`. Otherwise it returns `measure_event_run` on that eye's
    `(gaze, v, offered)` with `runs_end_before_landing=False`, `runs_start_after_takeoff=False` and the floor given.
    These are exactly the arguments `_run_row` passes for a two-eye span today (`_measured_from_takeoff` is false
    for `"conjunction"`).
  - `_conjunction_label(detector, params, gaze, *, eyes=None, fs_hz=None, min_measured_ms=None)`. Called without
    `eyes`, it labels as today (the existing tests call it that way). With them, for a size-cut detector, it
    returns `classify(_two_eye_measurement(...).amplitude_deg, microsaccade_max_deg)`, falling back to today's
    bare-left amplitude when that measurement is `None`. The half-cut branches are unchanged.
  - `_conjunction_trace` passes `eyes=per_eye`, `fs_hz` and
    `min_measured_ms=getattr(detector_params, "min_measured_saccade_ms", None)`. `_run_row` measures a two-eye
    event span with `_two_eye_measurement`. Fixation positions and kept runs are unchanged.

Every test below:
- builds `per_eye` by hand at 500 Hz, per-axis unless stated, with `v = velocity(gaze, 500.0)`;
- calls `_conjunction_trace` with Engbert-Kliegl's `_eye_detection_params` (geometry set) and the hand-made runs;
- stores through `EyeDetection._insert_trace` into `_CapturedInserts`, exactly as `make()` does.

Gaze moves along x only, so per-axis amplitude is the x difference.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_two_eye_span_is_labelled_from_its_size_on_the_eye_that_measures_it():
    # left run [100,136), right run [100,107); left LOST [104,114), right INVALID [107,113), so both runs
    # clash and the span [100,107) stands. Left x +0.3/sample over 100..106 (filled), right +0.15/sample.
    # Left is not usable throughout, so the right eye measures it: 0.9 deg.
    (row,) = events
    assert (row["run_start"], row["run_stop"], row["label"]) == (100, 107, "microsaccade")
    assert row["amplitude_deg"] == pytest.approx(0.9, abs=1e-9)


@pytest.mark.parametrize("geometry", [PER_AXIS, TRUE_ANGLE])
def test_a_two_eye_span_whose_take_off_a_loss_hides_is_labelled_from_its_size_widened_across_a_loss(geometry):
    # left run [90,110), left LOST [94,100); right run [100,112), right all measured; nothing kept.
    # Left x: 0 through 93, linear to 1.2 at 100, then +0.05/sample to 1.65 at 109. Bare [100,110) is 0.45;
    # widened from 93 it is 1.65.
    (row,) = events
    assert (row["run_start"], row["run_stop"], row["label"]) == (100, 110, "saccade")
    assert row["amplitude_deg"] == pytest.approx(1.65, rel=1e-6)   # true-angle: tolerance, not equality


def test_a_span_no_eye_measures_is_stored_unmeasured_and_labelled_as_before():
    # both runs [100,110); left INVALID [104,106), right INVALID [105,107); left x +0.2/sample.
    (row,) = events
    assert (row["label"], row["amplitude_deg"]) == ("saccade", None)


def test_a_half_cut_detector_keeps_its_constant_two_eye_label():
    # the first test's inputs with U'n'Eye (`{saccade}`): "saccade" whatever the size.
    assert [row["label"] for row in events] == ["saccade"]
```

Each also asserts, over every saccadic row with an amplitude, `row["label"] == classify(row["amplitude_deg"], 1.0).value`.

- [ ] **Step 2: Run them to see the first two fail**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py -k "two_eye_span or no_eye_measures or half_cut_detector" -q -p no:cacheprovider`
Expected: the eye-choice test and both geometries of the take-off test FAIL. Their labels are `saccade` and
`microsaccade`, from the bare left intersection. The other two pass, since they pin behaviour that must not change.

- [ ] **Step 3: Implement `_two_eye_eye`, `_two_eye_measurement`, the `eyes` path of `_conjunction_label`, and `_run_row`'s use of it**

Update three docstrings:
- `_conjunction_label`'s "agree by construction" paragraph: the label comes from the measurement `_run_row`
  stores, on the eye it stores it on (since 2026-11-19), and the earlier text is kept as history.
- `_insert_trace`: the sentence saying the conjunction's label is `classify` over "the interval it was assigned to".
- `_conjunction_parts`: Review Focus 5's ruling.

- [ ] **Step 4: Run them, the eye-detection tests and the reference test**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py tests/eye/detect -q -p no:cacheprovider`
Expected: all pass.

Run: the reference test command from Task 1, Step 2.
Expected: PASS. Part 3 prints `0/` on all three traces.

- [ ] **Step 5: Prove the tests against the old code**

Restore `_conjunction_label`'s body to its Task 1 form and clear `__pycache__`. The eye-choice test, the take-off
test and the reference test must fail. Then restore the fix.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/detect.py tests/schema/test_detect_populate.py
git commit -m "fix(detect): a two-eye span is labelled from the measurement it stores ..."
```
