# Blink Padding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the validity mask withholds each blink's flanks, 50 ms before the pupil is lost and 80 ms after it returns, as `invalid`, so the detectors are not offered the gaze a blink disturbs.

**Architecture:**
- **Two `ValidityParams` fields,** `blink_pad_before_ms` and `blink_pad_after_ms`, 0 by default so a paramset stored before them loads unchanged; `DEFAULT_VALIDITY_PARAMS` sets 50 and 80.
- **`validity_labels` pads each run of `blink`** from its edges, after the margin (`_dilate`) and before `short_epoch`, through `_blink_pads(blink, before, after)`. It takes the recording's rate as a keyword, `fs_hz`, to turn milliseconds into samples, and every caller passes it.
- **The pads are `invalid` and count toward no criterion,** as the margin does: no fraction column, no schema change.
- **Nothing else changes:** `EyeQuality`, `TrackerLoss`, wl.works' contracts, the report's format, the NWB file.

**Tech Stack:** Python ≥3.11; numpy; DataJoint 2.3.3 with MySQL.

**Spec:** `docs/superpowers/specs/2026-10-11-blink-padding-design.md` (`a2bae25`). It is binding. The requester approved the design in conversation on 2026-10-11, then the written spec (*"go"*). Task 2 adds its dated amendments, which record the rulings below.

**Every piece of code below was proven before this plan was written.** It was built on a scratch branch, `proof/blink-padding`, from `fix/blink-padding` at `a2bae25`, and then replayed from this plan's own text in a second worktree, which reproduced the proof's tree exactly.
- **Each task's failing run** was measured on the previous task's code plus this task's tests.
- **Its passing runs** were measured with the task applied.
- **Every mutation check named here** was run against the final tree, and each failed its test.
- **The full suite and the reference-recording tests** were run with every task applied (Task 2 quotes them).

## Global Constraints

- **The spec is binding,** including the dated amendments Task 2 adds. Where it and this plan disagree, the spec wins; record a ruling.
- **The requester's decisions (spec §0):** a fixed, asymmetric pad, 50 ms before and 80 ms after, the padded frames `invalid`; not adapted to each blink, not a wider margin around every rejected frame. The adaptive pad is to be tried later, once there is data to validate it against.
- **No schema change:** no column, no statement to run. A database holding the old default paramset computes masks for both it and the new default (spec §3); no real database exists yet.
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code, data or tables.
- **Leave the stray symlink `wl-preproc` at the repository root alone,** and keep it out of commits.
- **Tests.**
  - Run with `.venv/bin/python -m pytest <files> -q -p no:cacheprovider` from the repository root. In a git worktree, use the main checkout's `.venv/bin/python` and prefix `PYTHONPATH=$PWD`.
  - Edit scripts below run from the repository root, `.venv/bin/python <<'PY' … PY`; each asserts its old text is found exactly once.
  - Database tests need Docker (OrbStack on this machine: `open -a OrbStack` after a reboot). A module-wide setup error on a 120 s container timeout is the known flake: re-run it.
  - `tests/schema/test_eye_loss_populate.py`'s fixture builds a stepped session and runs a daemon pass: about 35 s before its first test.
  - **The seven tests gated on `WLPP_OHDPI_REFERENCE`** skip without it, in CI and in the full suite; Task 2 runs them with it set.
  - Mutation checks:
    - clear `__pycache__` first, and again after restoring;
    - run with `PYTHONDONTWRITEBYTECODE=1`;
    - make one mutation at a time, and restore the file afterwards.
  - **Run each task's own test files.** The full suite runs once, on both interpreters, in Task 2.
  - The shell is zsh: an unquoted `$VAR` holding several arguments is not split; a pipe through `tail` or `grep` hides the test command's exit status.
- **`wl-check` after `wl.yaml` changes,** on its own (Task 2).
- **Every commit message ends with** the trailer lines the harness gives the executing session.
- **Date what Task 2 records** with the day the plan is executed; this plan proved it on 2026-10-11 and 2026-10-12.

## Review Focus

Five inputs the spec implies but its own testing section (§4) does not name, most likely first. Each is pinned by a test named here, in the task that owns it.

1. **A blink at the very start or end of a recording.** Its pad stops at the recording's edge rather than wrapping or raising: `_blink_pads` clips with `max(start - before, 0)` and `min(stop + after, n)`; Task 1, `test_a_blink_is_padded_by_the_stated_milliseconds_either_side` and its neighbours run blinks well inside the trace, and `test_a_blink_at_the_recording_s_edge_is_padded_only_inward` pins the edges.
2. **Pads of 0** (an old paramset): the mask is exactly as before the pads. Task 1, `test_a_paramset_stored_before_the_pads_loads_without_them`.
3. **A recording at another rate:** the pads are milliseconds. Task 1, `test_the_pad_is_turned_into_samples_at_the_rate_given`.
4. **A lost reflection beside a blink:** only the blink is padded. Task 1, `test_only_blinks_are_padded`.
5. **The stored mask, through a real daemon pass:** the pads are stored `invalid` and the blink stays `blink`. Task 1, `test_a_stored_blink_is_padded_either_side_as_invalid`.

## The spec's §5, verified at `a2bae25`

Answered while proving, and recorded by Task 2 as amendment 1:
1. **Every reader of a validity paramset builds `ValidityParams(**params)`:** `schema/detect.py::EyeValidity.make` and `EyeDetection.make`, and `nwb/gather.py`. An old paramset loads with both pads at 0: `test_a_paramset_stored_before_the_pads_loads_without_them`.
2. **Every caller of `validity_labels` has the rate:** `EyeValidity.make` (`recording.fs_hz`), the six gated validation tests (`rec.fs_hz`, `fs` or their helpers' `fs_hz`), and `tests/schema/test_detect_populate.py`'s two (one a helper's `fs_hz`; the other builds random arrays and states 500 Hz).
3. **What reads "the default validity paramset":** the NWB export registers the current default and uses its index (`nwb/gather.py`), so it follows the change; the seven-way scores, the main sequence and the report are keyed by validity paramset and are computed for each one registered; the tests that register a validity paramset register `DEFAULT_VALIDITY_PARAMS`, so they follow it too.
4. **The share the pads newly withhold** with every criterion counted: Task 2 (amendment 2).
5. **The guardrail tests pass** (`tests/schema/test_guardrails.py`).

## The rulings this plan carries

Each is a dated amendment Task 2 adds to the spec.
- **1 (Task 1):** `fs_hz` is a keyword-only argument of `validity_labels`, so no caller passes the rate by position by accident.
- **2 (Task 1):** the database test sits in Task 1 with the unit tests, since the code that passes the rate is Task 1's: in a later task it would pass before it could fail.

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/eye/detect/validity.py` | the two fields and their defaults; `_blink_pads`; `validity_labels`' `fs_hz` and the pad (Task 1) |
| `wl_preproc/schema/detect.py` | `EyeValidity.make` passes the rate (Task 1) |
| `tests/eye/detect/test_validity.py` | the pad's unit tests; every call passes the rate (Task 1) |
| `tests/schema/test_eye_loss_populate.py` | the stored blink padded (Task 1) |
| `tests/schema/test_detect_populate.py`, `tests/eye/detect/test_{nslr,bmd,remodnav,otero_millan,nystrom_holmqvist,seven_way}_validation.py` | the other callers pass the rate (Task 1); docstring figures (Task 2) |
| the specs, `docs/handoffs/2026-10-11-eye-loss.md`, `docs/handoffs/2026-10-11-blink-padding.md` (new), `docs/CHECKPOINT.md`, `wl.yaml` | the records (Task 2) |

---

### Task 1: The pad

**Files:**
- Modify: `wl_preproc/eye/detect/validity.py`, `wl_preproc/schema/detect.py`, `tests/eye/detect/test_validity.py`, `tests/schema/test_eye_loss_populate.py`, `tests/schema/test_detect_populate.py`, the six `tests/eye/detect/test_*_validation.py`

**Interfaces:**
- Produces: `ValidityParams.blink_pad_before_ms: float = 0.0`, `ValidityParams.blink_pad_after_ms: float = 0.0`; `DEFAULT_VALIDITY_PARAMS` with 50.0 and 80.0; `validity_labels(gaze_deg, velocity_deg_s, loss, frame_gaps, params, *, fs_hz: float) -> ValidityMask`; `validity._blink_pads(blink: np.ndarray, before: int, after: int) -> np.ndarray`.

- [ ] **Step 1: Write the failing tests.** The pad's unit tests, with every existing call given the rate, in `tests/eye/detect/test_validity.py`:

```bash
.venv/bin/python <<'PY'
import pathlib

p = pathlib.Path("tests/eye/detect/test_validity.py")
s = p.read_text()
lines = s.split("\n")
for i, line in enumerate(lines):
    if "validity_labels(" in line and "def " not in line and "import" not in line:
        for arg in ("_QUIET", "params"):
            if f", {arg})" in line:
                lines[i] = line.replace(f", {arg})", f", {arg}, fs_hz=FS_HZ)", 1)
                break
        else:
            raise AssertionError(line)
s = "\n".join(lines)


def once(old, new):
    global s
    assert s.count(old) == 1, old[:70]
    s = s.replace(old, new)


once("""import numpy as np
""", """from dataclasses import asdict

import numpy as np
""")
once("""    assert DEFAULT_VALIDITY_PARAMS.region_half_width_deg == 20.0
    assert DEFAULT_VALIDITY_PARAMS.max_speed_deg_s == 1000.0""", """    assert DEFAULT_VALIDITY_PARAMS.region_half_width_deg == 20.0
    assert DEFAULT_VALIDITY_PARAMS.max_speed_deg_s == 1000.0
    # Measured, not placeholders (design spec `2026-10-11-blink-padding-design.md`).
    assert DEFAULT_VALIDITY_PARAMS.blink_pad_before_ms == 50.0
    assert DEFAULT_VALIDITY_PARAMS.blink_pad_after_ms == 80.0""")
s = s.rstrip("\n") + '''


# -- A blink's flanks (design spec `2026-10-11-blink-padding-design.md`). The
# gaze vector is disturbed for tens of milliseconds before the pupil is lost
# and after it returns, so each blink is padded, `invalid`, from its edges.


def _padded(before_ms, after_ms, dilate=0, min_epoch=1):
    return ValidityParams(20.0, 15.0, 1000.0, dilate_samples=dilate, min_epoch_samples=min_epoch,
                          blink_pad_before_ms=before_ms, blink_pad_after_ms=after_ms)


def test_a_blink_is_padded_by_the_stated_milliseconds_either_side():
    """10 ms before and 20 ms after at 500 Hz: 5 and 10 samples, from the
    blink's edges, `invalid`; the blink's own frames stay `blink`."""
    gaze, vel, loss, gaps = _clean(40)
    loss.blink[15:20] = True

    labels = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 20.0), fs_hz=FS_HZ).labels

    assert [labels[i] for i in range(10, 15)] == [Label.INVALID] * 5
    assert [labels[i] for i in range(15, 20)] == [Label.BLINK] * 5
    assert [labels[i] for i in range(20, 30)] == [Label.INVALID] * 10
    assert labels[9] is None and labels[30] is None


def test_the_margin_sits_inside_the_pad_rather_than_adding_to_it():
    gaze, vel, loss, gaps = _clean(40)
    loss.blink[15:20] = True

    labels = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 20.0, dilate=2), fs_hz=FS_HZ).labels

    assert labels[10] is Label.INVALID and labels[29] is Label.INVALID
    assert labels[9] is None and labels[30] is None


def test_the_pad_is_turned_into_samples_at_the_rate_given():
    """The same milliseconds at 1 kHz: 10 and 20 samples."""
    gaze, vel, loss, gaps = _clean(60)
    loss.blink[20:25] = True

    labels = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 20.0), fs_hz=1000.0).labels

    assert labels[9] is None and labels[10] is Label.INVALID
    assert labels[44] is Label.INVALID and labels[45] is None


def test_two_blinks_whose_pads_meet_are_one_withheld_stretch():
    gaze, vel, loss, gaps = _clean(60)
    loss.blink[10:12] = True    # padded to 5..21
    loss.blink[25:27] = True    # padded to 20..36

    labels = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 20.0), fs_hz=FS_HZ).labels

    assert all(labels[i] is not None for i in range(5, 37))
    assert labels[4] is None and labels[37] is None


def test_a_short_epoch_stranded_between_two_pads_is_dropped():
    gaze, vel, loss, gaps = _clean(80)
    loss.blink[10:12] = True    # padded to 5..21
    loss.blink[30:32] = True    # padded to 25..41, leaving 22..24: three samples

    labels = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 20.0, min_epoch=5), fs_hz=FS_HZ).labels

    assert [labels[i] for i in (22, 23, 24)] == [Label.INVALID] * 3


def test_only_blinks_are_padded():
    """A lost reflection keeps the margin and nothing more."""
    gaze, vel, loss, gaps = _clean(40)
    loss.feature_lost[15:20] = True

    labels = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 20.0, dilate=2), fs_hz=FS_HZ).labels

    assert labels[13] is Label.INVALID and labels[21] is Label.INVALID
    assert labels[12] is None and labels[22] is None


def test_a_paramset_stored_before_the_pads_loads_without_them():
    """A stored paramset is a dict without the two fields: it loads with
    pads of 0, so its content hash still names what it does, and it masks a
    blink as before -- the blink and its margin."""
    stored = {name: value for name, value in asdict(DEFAULT_VALIDITY_PARAMS).items()
              if not name.startswith("blink_pad_")}
    params = ValidityParams(**stored)
    gaze, vel, loss, gaps = _clean(60)
    loss.blink[20:25] = True

    labels = validity_labels(gaze, vel, loss, gaps, params, fs_hz=FS_HZ).labels

    assert params.blink_pad_before_ms == 0.0 and params.blink_pad_after_ms == 0.0
    assert labels[14] is None and labels[15] is Label.INVALID
    assert labels[29] is Label.INVALID and labels[30] is None


def test_the_pads_count_toward_no_criterion():
    """As the margin does not: the raw criteria are unchanged."""
    gaze, vel, loss, gaps = _clean(40)
    loss.blink[15:20] = True

    fractions = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 20.0), fs_hz=FS_HZ).fractions

    assert fractions["blink"] == 5 / 40
    assert sum(fractions.values()) == 5 / 40


def test_a_blink_at_the_recording_s_edge_is_padded_only_inward():
    """A pad stops at the recording's first and last frames: a blink two
    frames in is padded over those two, and one two frames from the end
    over the last two."""
    gaze, vel, loss, gaps = _clean(40)
    loss.blink[2:5] = True
    loss.blink[35:38] = True

    labels = validity_labels(gaze, vel, loss, gaps, _padded(10.0, 20.0), fs_hz=FS_HZ).labels

    assert [labels[i] for i in (0, 1, 38, 39)] == [Label.INVALID] * 4
    assert [labels[i] for i in range(5, 15)] == [Label.INVALID] * 10
    assert [labels[i] for i in range(30, 35)] == [Label.INVALID] * 5
    assert all(labels[i] is None for i in range(15, 30))
'''
p.write_text(s)
PY
```

and the stored blink padded, in `tests/schema/test_eye_loss_populate.py`:

```bash
.venv/bin/python <<'PY'
import pathlib

p = pathlib.Path("tests/schema/test_eye_loss_populate.py")
s = p.read_text()
anchor = "def test_the_two_stages_raised_nothing_for_this_session(lost_session):"
assert s.count(anchor) == 1
s = s.replace(anchor, '''def test_a_stored_blink_is_padded_either_side_as_invalid(lost_session):
    """The right eye's blink, alone in that eye: 50 ms before it and 80 ms
    after, from its edges, stored as `invalid`, and the frames beyond them
    usable (design spec `2026-10-11-blink-padding-design.md`)."""
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS
    from wl_preproc.synth.ohdpi import OHDPI_FPS

    session_key, _segment, _report = lost_session
    before = round(DEFAULT_VALIDITY_PARAMS.blink_pad_before_ms * OHDPI_FPS / 1000)
    after = round(DEFAULT_VALIDITY_PARAMS.blink_pad_after_ms * OHDPI_FPS / 1000)
    start = _first_row_at(_FIRST_BLINK_S)
    stop = start + _BLINK_ROWS
    labels = _stored_mask(session_key, "right")

    assert set(labels[start - before:start]) == {"invalid"}
    assert set(labels[start:stop]) == {"blink"}
    assert set(labels[stop:stop + after]) == {"invalid"}
    assert labels[start - before - 1] == "fixation" and labels[stop + after] == "fixation"


''' + anchor)
p.write_text(s)
PY
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_validity.py tests/schema/test_eye_loss_populate.py::test_a_stored_blink_is_padded_either_side_as_invalid -q -p no:cacheprovider -o faulthandler_timeout=1800`
Expected: 38 failed: the 37 tests of `test_validity.py` on `TypeError: ValidityParams.__init__() got an unexpected keyword argument 'blink_pad_before_ms'`, `TypeError: validity_labels() got an unexpected keyword argument 'fs_hz'` or `AttributeError: 'ValidityParams' object has no attribute 'blink_pad_before_ms'`, and the stored blink's test on the last.

- [ ] **Step 3: The pad.** In `wl_preproc/eye/detect/validity.py`:

```bash
.venv/bin/python <<'PY'
import pathlib

p = pathlib.Path("wl_preproc/eye/detect/validity.py")
s = p.read_text()
for old, new in [
    ("""    # fields above, so a paramset written before it existed still loads.
    max_glitch_ms: float = 10.0
""", """    # fields above, so a paramset written before it existed still loads.
    max_glitch_ms: float = 10.0
    # A blink's flanks, withheld as `invalid` from its edges: the gaze vector
    # is disturbed before the pupil is lost and after it returns (design spec
    # `2026-10-11-blink-padding-design.md`). 0 by default, so a paramset
    # stored before them loads unchanged and its content hash still names
    # what it does; `DEFAULT_VALIDITY_PARAMS` sets the measured values.
    blink_pad_before_ms: float = 0.0
    blink_pad_after_ms: float = 0.0
"""),
    ("""    # movements (the requester's decision of 2026-09-28).
    max_glitch_ms=10.0,
)""", """    # movements (the requester's decision of 2026-09-28).
    max_glitch_ms=10.0,
    # Measured on the reference recording: the gaze vector's frame-to-frame
    # steps fall to 1.7x baseline 50 ms before the pupil is lost, and to 2.9x
    # 80 ms after it returns, where the real movement after a blink begins
    # (design spec `2026-10-11-blink-padding-design.md` section 1).
    blink_pad_before_ms=50.0,
    blink_pad_after_ms=80.0,
)"""),
    ("""    loss: TrackerLoss,
    frame_gaps: Sequence[FrameGap],
    params: ValidityParams,
) -> ValidityMask:
    \"\"\"One entry per sample: a `Label` where the sample is unusable, `None`
    where a detector may label it -- and beside it each criterion's own
    rejected fraction. See `ValidityMask` for what the seven fractions are,
    and for what they deliberately do not sum to.\"\"\"""", """    loss: TrackerLoss,
    frame_gaps: Sequence[FrameGap],
    params: ValidityParams,
    *,
    fs_hz: float,
) -> ValidityMask:
    \"\"\"One entry per sample: a `Label` where the sample is unusable, `None`
    where a detector may label it -- and beside it each criterion's own
    rejected fraction. See `ValidityMask` for what the seven fractions are,
    and for what they deliberately do not sum to. `fs_hz` is the
    recording's rate, which turns the blink pads' milliseconds into
    samples.\"\"\""""),
    ("""    unusable = blink | feature_lost | outside | too_fast | across_gap | non_finite
    unusable = _dilate(unusable, params.dilate_samples)
""", """    unusable = blink | feature_lost | outside | too_fast | across_gap | non_finite
    unusable = _dilate(unusable, params.dilate_samples)
    # Each blink's flanks, measured from its edges, after the margin so that
    # the margin sits inside a pad rather than adding to it, and before
    # `short_epoch`, so a fragment stranded between two pads is dropped as
    # any short epoch is (design spec `2026-10-11-blink-padding-design.md`).
    unusable = unusable | _blink_pads(
        blink,
        round(params.blink_pad_before_ms * fs_hz / 1000.0),
        round(params.blink_pad_after_ms * fs_hz / 1000.0),
    )
"""),
    ("""def _dilate(mask: np.ndarray, samples: int) -> np.ndarray:""", """def _blink_pads(blink: np.ndarray, before: int, after: int) -> np.ndarray:
    \"\"\"`before` samples ahead of each run of `blink` and `after` samples
    behind it, the run itself excluded: it keeps its own label. Pads that
    meet make one stretch.\"\"\"
    out = np.zeros(blink.shape[0], dtype=bool)
    if (before <= 0 and after <= 0) or not blink.any():
        return out
    edges = np.diff(np.concatenate(([0], blink.astype(np.int8), [0])))
    for start, stop in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
        out[max(start - before, 0):start] = True
        out[stop:min(stop + after, blink.shape[0])] = True
    return out


def _dilate(mask: np.ndarray, samples: int) -> np.ndarray:"""),
]:
    assert s.count(old) == 1, old[:60]
    s = s.replace(old, new)
p.write_text(s)
PY
```

- [ ] **Step 4: Every other caller passes the rate:** `EyeValidity.make`, the six gated validation tests and `tests/schema/test_detect_populate.py`'s two; and the mask's docstring names the pads among what belongs to no criterion:

```bash
.venv/bin/python <<'PY'
import pathlib


def edit(path, pairs):
    p = pathlib.Path(path)
    s = p.read_text()
    for old, new in pairs:
        assert s.count(old) == 1, (path, old[:60])
        s = s.replace(old, new)
    p.write_text(s)


edit("wl_preproc/eye/detect/validity.py", [(
    """      region: the halo dilation adds belongs to no one criterion, and
      attributing it to whichever criterion it happens to surround would be
      an invention.""",
    """      region: the halo dilation adds belongs to no one criterion, nor do
      the blink pads (`_blink_pads`), and attributing either to whichever
      criterion it happens to surround would be an invention.""")])
edit("wl_preproc/schema/detect.py", [(
    """                gaze, velocity(gaze, recording.fs_hz), read_tracker_loss(path, file_eye),
                recording.frame_gaps, params,
            )""",
    """                gaze, velocity(gaze, recording.fs_hz), read_tracker_loss(path, file_eye),
                recording.frame_gaps, params, fs_hz=recording.fs_hz,
            )""")])
edit("tests/schema/test_detect_populate.py", [
    ("    labels = validity_labels(gaze, velocity_deg_s, loss, (), DEFAULT_VALIDITY_PARAMS).labels",
     "    labels = validity_labels(gaze, velocity_deg_s, loss, (), DEFAULT_VALIDITY_PARAMS, fs_hz=500.0).labels"),
    ("    mask = validity_labels(gaze, v, loss, frame_gaps, DEFAULT_VALIDITY_PARAMS)\n",
     "    mask = validity_labels(gaze, v, loss, frame_gaps, DEFAULT_VALIDITY_PARAMS, fs_hz=fs_hz)\n"),
])
for name, rate in (("nslr", "rec.fs_hz"), ("bmd", "rec.fs_hz")):
    edit(f"tests/eye/detect/test_{name}_validation.py", [(
        "validity_labels(gaze, v, loss[eye], rec.frame_gaps, DEFAULT_VALIDITY_PARAMS).labels",
        f"validity_labels(gaze, v, loss[eye], rec.frame_gaps, DEFAULT_VALIDITY_PARAMS, fs_hz={rate}).labels")])
edit("tests/eye/detect/test_remodnav_validation.py", [(
    "validity_labels(gaze, v, loss[eye], recording.frame_gaps, DEFAULT_VALIDITY_PARAMS).labels",
    "validity_labels(gaze, v, loss[eye], recording.frame_gaps, DEFAULT_VALIDITY_PARAMS, fs_hz=fs).labels")])
for name in ("nystrom_holmqvist", "otero_millan"):
    edit(f"tests/eye/detect/test_{name}_validation.py", [(
        "validity_labels(gaze, v, loss, frame_gaps, DEFAULT_VALIDITY_PARAMS).labels",
        "validity_labels(gaze, v, loss, frame_gaps, DEFAULT_VALIDITY_PARAMS, fs_hz=fs_hz).labels")])
edit("tests/eye/detect/test_seven_way_validation.py", [(
    "validity_labels(gaze, v, loss, (), DEFAULT_VALIDITY_PARAMS).labels",
    "validity_labels(gaze, v, loss, (), DEFAULT_VALIDITY_PARAMS, fs_hz=fs_hz).labels")])
PY
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_validity.py tests/schema/test_eye_loss_populate.py -q -p no:cacheprovider -o faulthandler_timeout=1800`
Expected: 48 passed (37 and 11), in about 50 s, most of it the fixture's daemon pass.

- [ ] **Step 6: Run the task's neighbours**

Run: `.venv/bin/python -m pytest tests/eye tests/schema/test_detect_populate.py tests/schema/test_consensus_populate.py tests/schema/test_eye_loss_populate.py tests/cli/test_detect_report.py tests/schema/test_guardrails.py -q -p no:cacheprovider -o faulthandler_timeout=1800`
Expected: 697 passed, 74 skipped, 1 xfailed (the skips are tests gated on references, the OpenIris recording among them).

- [ ] **Step 7: Mutation checks,** each alone, restoring after each. Every one must fail the test named:
  - in `validity_labels`, `round(params.blink_pad_before_ms * fs_hz / 1000.0)` → `round(params.blink_pad_after_ms * fs_hz / 1000.0)`: `test_a_blink_is_padded_by_the_stated_milliseconds_either_side`;
  - `    unusable = _dilate(unusable, params.dilate_samples)` → the pads ORed in before `_dilate` (`_dilate(unusable | _blink_pads(blink, …), params.dilate_samples)`): `test_the_margin_sits_inside_the_pad_rather_than_adding_to_it`;
  - a line `blink = blink | _blink_pads(blink, …)` inserted before the pads join `unusable`, so they are labelled `blink`: `test_a_blink_is_padded_by_the_stated_milliseconds_either_side`;
  - `    blink_pad_before_ms: float = 0.0` → `= 50.0`: `test_a_paramset_stored_before_the_pads_loads_without_them`;
  - `round(params.blink_pad_before_ms * fs_hz / 1000.0)` → `round(params.blink_pad_before_ms * 500.0 / 1000.0)`: `test_the_pad_is_turned_into_samples_at_the_rate_given`;
  - `_blink_pads(\n        blink,` → `_blink_pads(\n        blink | feature_lost,`: `test_only_blinks_are_padded`;
  - `short_epoch` computed from the margin's mask alone, before the pads: `test_a_short_epoch_stranded_between_two_pads_is_dropped`;
  - in `_blink_pads`, `out[max(start - before, 0):start] = True` → `out[start - before:start] = True`: `test_a_blink_at_the_recording_s_edge_is_padded_only_inward`;
  - in `EyeValidity.make`, `fs_hz=recording.fs_hz` → `fs_hz=1000.0`: `test_a_stored_blink_is_padded_either_side_as_invalid`.

- [ ] **Step 8: Commit**

```bash
git add wl_preproc/eye/detect/validity.py wl_preproc/schema/detect.py tests/eye/detect tests/schema/test_eye_loss_populate.py tests/schema/test_detect_populate.py
git commit -m "feat(detect): the validity mask pads each blink's flanks -- 50 ms before the pupil is lost and 80 ms after it returns, from its edges, invalid; two ValidityParams fields in milliseconds, 0 for a paramset stored before them; validity_labels takes the recording's rate (spec section 2)"
```

---

### Task 2: The records, the reference recording, the full suite and `wl-check`

**Files:**
- Modify: `docs/superpowers/specs/2026-10-11-blink-padding-design.md` (amendments), `docs/superpowers/specs/2026-08-31-saccade-detection-design.md`, `docs/handoffs/2026-10-11-eye-loss.md`, `tests/eye/detect/test_nystrom_holmqvist_validation.py`, `tests/eye/detect/test_otero_millan_validation.py`, `tests/schema/test_detect_populate.py` (docstring figures), `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-10-11-blink-padding.md`

**Interfaces:** none.

- [ ] **Step 1: The tests gated on the reference recording,** with `WLPP_OHDPI_REFERENCE` set, logged in the plan's git-ignored workspace (the paths are this machine's; in a worktree, `.venv/bin/python` is the main checkout's):

```bash
R=$HOME/.cache/wl-preproc-references
WS=.superpowers/sdd/2026-10-11-blink-padding && mkdir -p $WS
env PYTHONPATH=$PWD PYTHONDONTWRITEBYTECODE=1 WLPP_OHDPI_REFERENCE=$R/OpenIris-2024Jul31-114628.txt WLPP_NSLR_REFERENCE=$R \
  WLPP_BMD_REFERENCE=$R/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$R/bmd/boost_1_86_0 WLPP_ANDERSSON_DATA=$R/andersson \
  .venv/bin/python -m pytest -s -rA -p no:cacheprovider -o faulthandler_timeout=3600 \
  tests/eye/detect/test_nslr_validation.py tests/eye/detect/test_bmd_validation.py tests/eye/detect/test_remodnav_validation.py \
  tests/eye/detect/test_otero_millan_validation.py tests/eye/detect/test_nystrom_holmqvist_validation.py tests/eye/detect/test_seven_way_validation.py \
  "tests/schema/test_detect_populate.py::test_the_run_count_measured_against_the_reference_recording" > $WS/gated.txt 2>&1
echo "exit $?"; tail -1 $WS/gated.txt
```

Expected: `exit 0` and `69 passed`, as before the change, with none of their assertions changed; the figures they print move as amendment 3 records. About 25 minutes.

- [ ] **Step 2: The figures it moved, beside the ones they replace,** in the gated tests' docstrings:

```bash
.venv/bin/python <<'PY'
import pathlib


def edit(path, pairs):
    p = pathlib.Path(path)
    s = p.read_text()
    for old, new in pairs:
        assert s.count(old) == 1, (path, old[:60])
        s = s.replace(old, new)
    p.write_text(s)


edit("tests/eye/detect/test_nystrom_holmqvist_validation.py", [
    ("""    (`eye/detect/glitch.py`), with the mask reading where the tracker lost
    the eye (design spec `2026-10-11-eye-loss-design.md`):

    | | left -> right | right -> left |
    |---|---|---|
    | unmatched, of the eye's saccades | 403 of 4,729 | 324 of 4,646 |
    | the other eye's data was withheld | 260 | 136 |
    | the other eye's saccade was just outside it | 24 | 26 |
    | the other eye moved too, undetected | 64 | 106 |
    | the other eye did not move | 55 | 56 |

    Only the middle two rows are real saccades the binocular rule loses:
    about 1.9% and 2.8% of each eye's saccades. The largest row is missing
    data, not disagreement, and it is asserted to stay the largest.
""", """    (`eye/detect/glitch.py`), with the mask reading where the tracker lost
    the eye and padding each blink's flanks (design specs
    `2026-10-11-eye-loss-design.md` and `2026-10-11-blink-padding-design.md`):

    | | left -> right | right -> left |
    |---|---|---|
    | unmatched, of the eye's saccades | 395 of 4,689 | 325 of 4,620 |
    | the other eye's data was withheld | 250 | 139 |
    | the other eye's saccade was just outside it | 25 | 23 |
    | the other eye moved too, undetected | 68 | 103 |
    | the other eye did not move | 52 | 60 |

    Only the middle two rows are real saccades the binocular rule loses:
    about 2.0% and 2.7% of each eye's saccades. The largest row is missing
    data, not disagreement, and it is asserted to stay the largest.

    *Measured the same day before the blink pads: 403 of 4,729 and 324 of
    4,646 unmatched (260/24/64/55 and 136/26/106/56), about 1.9% and 2.8%;
    true when written.*
"""),
    ("""    glitch-repaired gaze, with the mask reading where the tracker lost the
    eye (design spec `2026-10-11-eye-loss-design.md`): 306 left and 184
    right saccades kept whole, and the trace is `both` on 93.58%, `left`
    1.24%, `right` 1.15% and `neither` 4.04%.
""", """    glitch-repaired gaze, with the mask reading where the tracker lost the
    eye and padding each blink's flanks (design specs
    `2026-10-11-eye-loss-design.md` and `2026-10-11-blink-padding-design.md`):
    281 left and 177 right saccades kept whole, and the trace is `both` on
    93.05%, `left` 1.09%, `right` 1.15% and `neither` 4.70%.

    *Measured the same day before the blink pads: 306 and 184, with `both`
    on 93.58%, `left` 1.24%, `right` 1.15% and `neither` 4.04%; true when
    written.*
"""),
])
edit("tests/eye/detect/test_otero_millan_validation.py", [(
    """    amendment 6). On the mask since, this test prints Engbert-Kliegl 0.8067 /
    0.7826 against otero_millan's 0.8577 / 0.8046, the duration-matched null
    0.8674 / 0.8554, and 4,525 left-eye events, from 4,700: points 1 and 2
    stand. The figures below were not re-measured; true when written.*""",
    """    amendment 6). On the mask since, which also pads each blink's flanks
    (design spec `2026-10-11-blink-padding-design.md`), this test prints
    Engbert-Kliegl 0.8052 / 0.7850 against otero_millan's 0.8515 / 0.8070,
    the duration-matched null 0.8583 / 0.8439, and 4,469 left-eye events,
    from 4,700: points 1 and 2 stand. (Before the pads, the same day:
    0.8067 / 0.7826, 0.8577 / 0.8046, 0.8674 / 0.8554 and 4,525.) The
    figures below were not re-measured; true when written.*""")])
edit("tests/schema/test_detect_populate.py", [(
    """    # spans (402 of 4,952 when this was measured; 279 of 4,495 on the mask
    # since 2026-10-11), so this is not vacuous.""",
    """    # spans (402 of 4,952 when this was measured; 279 of 4,495 on the mask
    # since 2026-10-11, and 261 of 4,340 with each blink's flanks padded),
    # so this is not vacuous.""")])
PY
```

- [ ] **Step 3: The spec's amendments.** Append to `docs/superpowers/specs/2026-10-11-blink-padding-design.md`, with this execution's figures where they differ:

````markdown
---

## Amendments, 2026-10-12, made while proving the plan

The plan (`plans/2026-10-11-blink-padding.md`) was proven in code before it was written. These
settle what the sections above left open; §0 stands.

1. **§5's items, answered:**
   - **1:** every reader of a validity paramset builds `ValidityParams(**params)`
     (`schema/detect.py::EyeValidity.make` and `EyeDetection.make`, `nwb/gather.py`), so an old
     paramset loads with both pads at 0.
   - **2:** every caller of `validity_labels` has the rate: `EyeValidity.make`, the six gated
     validation tests and `tests/schema/test_detect_populate.py`'s two, one of which builds random
     arrays and states 500 Hz.
   - **3:** the NWB export registers the current default and uses its index, so it follows the
     change; the seven-way scores, the main sequence and the report are keyed by validity
     paramset and are computed for each one registered (§3); the tests that register a validity
     paramset register `DEFAULT_VALIDITY_PARAMS`.
   - **4:** amendment 2.
   - **5:** the guardrail tests pass.
2. **What the pads newly withhold, every criterion counted:** the mask keeps 1,108,196 of the
   left eye's 1,177,799 frames, from 1,115,287 (0.60% fewer), and 1,107,323 of the right's, from
   1,112,551 (0.44% fewer). §1's 0.95% and 0.80% counted only the tracker's losses and the
   margin; the region and speed criteria already caught some of the flank frames.
3. **The seven tests gated on the reference recording** pass before and after, 69 of 69, and none
   of their assertions changed. The larger shifts, left eye unless named:
   - **Engbert–Kliegl** finds 5,047 events, from 5,295 (right 4,826, from 5,029), and Otero-Millán
     4,469, from 4,525; their agreement (F1) is 0.863, from 0.843 (right 0.836, from 0.821);
   - **saccades one eye detects alone** are 395 of 4,689, from 403 of 4,729 (right 325 of 4,620,
     from 324 of 4,646), and the both-eyes trace has neither eye usable over 4.70% of samples,
     from 4.04%;
   - **the main sequence's r** is 0.8515 for Otero-Millán, from 0.8577, and 0.8052 for
     Engbert–Kliegl, from 0.8067; the duration-matched null 0.8583, from 0.8674. Both orderings
     its test records stand;
   - **the conjunction** has 4,340 raw intersections, from 4,495, 261 of them below its floor,
     from 279.
4. **The rulings:** `fs_hz` is keyword-only, so no caller passes the rate by position by
   accident; and the database test sits in the plan's first task with the unit tests, since the
   code that passes the rate is that task's, and in a later task it would pass before it could
   fail. A blink at the recording's very edge, which §4 did not name, is tested too: its pad
   stops at the first and last frames.
````

- [ ] **Step 4: The saccade spec, the eye-loss handoff, the checkpoint and `wl.yaml`:**

```bash
.venv/bin/python <<'PY'
import pathlib


def edit(path, pairs):
    p = pathlib.Path(path)
    s = p.read_text()
    for old, new in pairs:
        assert s.count(old) == 1, (path, old[:60])
        s = s.replace(old, new)
    p.write_text(s)


edit("docs/superpowers/specs/2026-08-31-saccade-detection-design.md", [(
    """written as 0, or `DataQuality` below 100 -- which is `invalid`
(`2026-10-11-eye-loss-design.md` §3 and amendment 8).*""",
    """written as 0, or `DataQuality` below 100 -- which is `invalid`
(`2026-10-11-eye-loss-design.md` §3 and amendment 8). Each blink's flanks are
padded as `invalid` too, 50 ms before the pupil is lost and 80 ms after it
returns (`2026-10-11-blink-padding-design.md`).*""")])
edit("docs/handoffs/2026-10-11-eye-loss.md", [(
    """- **Partial blinks:** the pupil's height falls in the 10 ms before a lost run and recovers over
  about 40 ms after it. The mask's dilation covers 10 ms either side; the rest of the recovery is
  offered to the detectors.""",
    """- **Partial blinks:** the pupil's height falls in the 10 ms before a lost run and recovers over
  about 40 ms after it. The mask's dilation covers 10 ms either side; the rest of the recovery is
  offered to the detectors. *Padded since, 50 ms before and 80 ms after
  (`handoffs/2026-10-11-blink-padding.md`); a pad adapted to each blink is still to try.*""")])
edit("docs/CHECKPOINT.md", [
    ("""> not a blink. **Next: blink padding, the requester's choice,** on
> `fix/blink-padding` (spec `specs/2026-10-11-blink-padding-design.md`): each
> blink's flanks padded in the mask, 50 ms before and 80 ms after; its plan is
> being written. Still to""",
     """> not a blink. **Blink padding, the requester's choice, is built on
> `fix/blink-padding`, not merged** (`handoffs/2026-10-11-blink-padding.md`):
> each blink's flanks padded in the mask, 50 ms before and 80 ms after. Next:
> the review. Still to"""),
    ("""> *Until 2026-10-12 this said the eye-loss fix was built, not merged,""",
     """> *Until 2026-10-12, later, this said the blink padding's plan was being
> written; true when written.*
>
> *Until 2026-10-12 this said the eye-loss fix was built, not merged,"""),
])
edit("wl.yaml", [(
    """    docs/handoffs/2026-10-11-eye-loss.md). Next, blink padding, the
    requester's choice, on fix/blink-padding: each blink's flanks padded in the
    mask, 50 ms before and 80 ms after; its plan is being written. Still to""",
    """    docs/handoffs/2026-10-11-eye-loss.md). Blink padding, the requester's
    choice, is built on fix/blink-padding and not merged
    (docs/handoffs/2026-10-11-blink-padding.md): each blink's flanks padded
    in the mask, 50 ms before and 80 ms after; next, the review. Still to""")])
PY
```

- [ ] **Step 5: This piece's handoff.** Create `docs/handoffs/2026-10-11-blink-padding.md`, with this execution's commits and counts:

````markdown
# Padding a blink's flanks in the validity mask

**The eye-loss fix's follow-up** (`handoffs/2026-10-11-eye-loss.md` §3, partial blinks). The mask
marked a blink only over its closed phase, the frames whose pupil is lost, and padded it by 10 ms;
the gaze vector is disturbed from about 50 ms before the pupil goes to about 80 ms after it
returns.
- **Branch:** `fix/blink-padding`, forked from `main` after the eye-loss merge.
- **Spec:** `docs/superpowers/specs/2026-10-11-blink-padding-design.md`, approved 2026-10-11, with
  amendments 1–4 made while proving the plan.
- **Plan:** `docs/superpowers/plans/2026-10-11-blink-padding.md`.
- **The requester's choices:** a fixed, asymmetric pad, 50 ms before and 80 ms after, the padded
  frames `invalid`; the adaptive pad to try once there is data to validate it against.

Every line of the plan was proven in a scratch branch, and replayed from the plan's own text,
before the plan was written.

---

## 1. What was built

- **Two `ValidityParams` fields,** `blink_pad_before_ms` and `blink_pad_after_ms`: 0 by default,
  so a paramset stored before them loads unchanged; 50 and 80 in `DEFAULT_VALIDITY_PARAMS`.
- **`validity_labels` pads each blink from its edges** (`_blink_pads`), after the 10 ms margin,
  which sits inside the pad, and before `short_epoch`. It takes the recording's rate as the
  keyword `fs_hz`; every caller passes it.
- **The pads are `invalid` and count toward no criterion:** `frac_blink` and the other fractions
  are unchanged, and there is no new column.
- **On the reference recording** the mask keeps 0.60% fewer of the left eye's frames and 0.44%
  fewer of the right's; Engbert–Kliegl finds 248 fewer events in the left eye, and its agreement
  with Otero-Millán rises from 0.843 to 0.863 (spec amendments 2 and 3).

## 2. What the other repositories must do

Nothing. The NWB file's format, the report's and wl.works' contracts are unchanged.

**A development database** holding the old default validity paramset registers the new one
beside it, and computes masks, detections, scores and fits for both, as it did when the glitch
repair added `max_glitch_ms`. No real database exists yet.

## 3. Still open

- **The adaptive pad** (the requester's reminder, spec §0): extending each blink while the pupil's
  height or the gaze's steps stay abnormal, once there is data to validate it against, such as
  hand-labelled blinks or raw eye video (`docs/pending-wl-xcon-amendments.md`, the asks of
  2026-10-11).
- **The movement after a blink is kept:** from about 80 ms after the pupil returns, the gaze's
  steps rise again, peaking near 200 ms. That is read as the eyes moving again, real movement the
  detectors should see; nothing in the data file alone can prove it.
- **Only blinks are padded:** a lost reflection, P1 off camera, keeps the 10 ms margin.

## 4. The rulings

The spec's amendment 4: `fs_hz` keyword-only, the database test with the unit tests, and the
recording's edge tested.

## 5. What execution measured

Executed on 2026-10-12, task by task: Task 1 `93ae310`. Every count matched the plan's:
- **Task 1's tests failed first:** 38 failed.
- **Then passed, with their neighbours:** 48 and 697.
- **All nine mutation checks the plan names failed their tests.**
- **The seven tests gated on the reference recording:** 69 passed, as before (amendment 3).
- **The full suite, once, on both interpreters,** with the BMD, NSLR and Andersson references
  set and `WLPP_OHDPI_REFERENCE` unset:
  - 3.11: **2349 passed, 27 skipped, 1 deselected, 1 xfailed**;
  - 3.13: **2349 passed, 28 skipped, 1 xfailed**.

  That is 10 more than the eye-loss fix's 2339: this piece's new tests. CI, without the references, should show about 2340 (`main`'s 2330 and these 10).
- **`wl-check`:** `wl.yaml: no findings`.
````

- [ ] **Step 6: The full suite, once, on both interpreters,** in parallel, with the references CI lacks set and `WLPP_OHDPI_REFERENCE` unset, each logged in the plan's workspace (in a worktree, `.venv/bin/python` is the main checkout's):

```bash
R=$HOME/.cache/wl-preproc-references
WS=.superpowers/sdd/2026-10-11-blink-padding && mkdir -p $WS
unset WLPP_OHDPI_REFERENCE
suite() { env PYTHONPATH=$PWD PYTHONDONTWRITEBYTECODE=1 WLPP_NSLR_REFERENCE=$R WLPP_BMD_REFERENCE=$R/bmd/BMD \
  WLPP_BMD_BOOST_INCLUDE=$R/bmd/boost_1_86_0 WLPP_ANDERSSON_DATA=$R/andersson \
  "$1" -m pytest -q -p no:cacheprovider -o faulthandler_timeout=1800 > "$WS/suite-$2.txt" 2>&1; echo "$2 exit $?"; }
suite .venv/bin/python 311 & suite $HOME/.cache/wl-preproc-venv313/bin/python 313 & wait
tail -1 $WS/suite-311.txt $WS/suite-313.txt
```

Expected, each exiting 0: 3.11, **2349 passed, 27 skipped, 1 deselected, 1 xfailed**; 3.13, **2349 passed, 28 skipped, 1 xfailed**. About 35 minutes, run in parallel. That is 10 more than the eye-loss fix's 2339: this piece's new tests.

- [ ] **Step 7: `wl-check`**

Run: `.venv/bin/wl-check`
Expected: `wl.yaml: no findings`

- [ ] **Step 8: Commit**

```bash
git add docs/superpowers/specs/2026-10-11-blink-padding-design.md docs/superpowers/specs/2026-08-31-saccade-detection-design.md \
  docs/handoffs/2026-10-11-eye-loss.md docs/handoffs/2026-10-11-blink-padding.md docs/CHECKPOINT.md wl.yaml \
  tests/eye/detect/test_nystrom_holmqvist_validation.py tests/eye/detect/test_otero_millan_validation.py tests/schema/test_detect_populate.py
git commit -m "docs: the blink padding's records -- spec amendments (section 5 answered; the share newly withheld; the reference-recording figures before and after; the rulings), the figures it moved beside the ones they replace, the saccade spec and the eye-loss handoff pointed here, this piece's handoff, and the checkpoint's and wl.yaml's lines: next, the review"
```
