# Saccade Main Sequence and Vigor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** fit each detection trace's main sequence, take each block's and condition's gain against it, and show each session's saccade vigor against the same animal's earlier sessions in the daily report.

**Architecture:**
- **Pure functions** (Task 1): `wl_preproc/eye/detect/main_sequence.py` picks the saccades a fit takes, fits the saturating curve with its guard, takes a group's gain against a curve, and works out a session's vigor against earlier curves. No DataJoint, so `tests/eye/` tests it alone.
- **The table** (Task 2): `wl_preproc/schema/main_sequence.py` declares `SaccadeMainSequence` with its `.Block` and `.Condition` parts, one key per detection trace and fit paramset. It stores the session fit or a refusal. The daemon runs it after `DetectorAgreement` and registers its default paramset.
- **The gains** (Task 3): the same `make()` places each fitted session's saccades in blocks and conditions by where they start in session time, and stores each group's gain.
- **The report** (Task 4): `cli/report.py` gains "Saccade vigor per session per eye (24 h)", computed when the report is built, never stored.
- **The records** (Task 5): the spec's amendments 1–3, the handoff, the checkpoint, `wl.yaml`, and the full suite.

**Tech Stack:** Python ≥3.11; numpy and SciPy's `curve_fit`; DataJoint 2.3 with MySQL; pytest. No dependency is added.

**Spec:** `docs/superpowers/specs/2026-10-07-main-sequence-design.md` (`7661352`), an addendum to `docs/superpowers/specs/2026-08-31-saccade-detection-design.md` §6.5 and §9. It is binding. The requester approved the design in chat, then the written spec ("Approved, write the plan"), on 2026-10-07. Task 5 adds its dated amendments 1–3, which record the rulings below.

**Every piece of code below was proven before this plan was written.** It was built on a scratch branch, `proof/main-sequence`, one commit per task, from `spec/main-sequence` at `7661352`.
- **Each task's failing run** was measured on the previous task's code plus this task's tests.
- **Its passing runs** were measured on its own commit.
- **Every mutation check named here** was run against the final tree, one at a time, and each failed the test named beside it.
- **The full suite** was run on both interpreters with every task applied (Task 5 quotes it).
- **The plan replays exactly:** its diffs, applied in order to `7661352` (and this plan's own commit, which touches nothing else), reproduce the proof's final tree.

## Global Constraints

- **The spec is binding,** including the dated amendments Task 5 adds. Where it and this plan disagree, the spec wins; record a ruling.
- **Vigor is never stored.** The report computes it; the table stores fits and gains.
- **`tests/eye/` imports nothing from `wl_preproc.schema`,** so `eye/detect/main_sequence.py` imports no DataJoint.
- **Each new database test takes its animal from `tests/identities.py`** (`new_animal().session()`), never a hand-written subject or date.
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code, data or tables.
- **Leave the stray symlink `wl-preproc` at the repository root alone,** and keep it out of commits.
- **Tests.**
  - Run with `.venv/bin/python -m pytest <files> -q -p no:cacheprovider` from the repository root. In a git worktree, prefix `PYTHONPATH=$PWD`.
  - Database tests need Docker (OrbStack on this machine: `open -a OrbStack` after a reboot). A module-wide setup error on a 120 s container timeout is the known flake: re-run it.
  - The planted session takes about a minute to build and run through the daemon, so `test_main_sequence_populate.py` takes about 100 s.
  - Mutation checks:
    - clear `__pycache__` first, and again after restoring;
    - run with `PYTHONDONTWRITEBYTECODE=1`;
    - make one mutation at a time, and restore the file afterwards.
  - **Run each task's own test files.** The full suite runs once, on both interpreters, in Task 5.
  - The shell is zsh:
    - an unquoted `$VAR` holding several arguments is not split;
    - a pipe through `tail` or `grep` hides the test command's exit status, so gate on the command's own status.
- **`wl-check` after `wl.yaml` changes** (Task 5).
- **Every commit message ends with** the trailer lines the harness gives the executing session.
- **Date what Task 5 records** (the checkpoint's and `wl.yaml`'s lines, the handoff) with the day the plan is executed; this plan proved them on 2026-10-07.

## Review Focus

Five inputs a real session can bring that the spec implies but does not test, most likely first. Each is pinned by a test named here, in the task that owns the code.

1. **Frames dropped from the recording mid-session:** a saccade's session time must come from its frame number, not its row, or every saccade after the gap is placed late. Task 2's planted session drops 60 frames in block 2; Task 3's `test_each_gain_is_taken_over_the_saccades_its_block_or_condition_holds` pins it (mutation T3a).
2. **Block and trial times hours into a session,** which element-event stores as MySQL `FLOAT`s that read back to six significant digits: a saccade 0.1 ms inside a block edge must stay in that block. Task 3: `test_placement_reads_times_as_doubles_and_names_by_record_then_stream_code` (T3c).
3. **A trial the rig record does not name but the stream's `CONDITION` number does:** the number names its condition. Same test (T3d).
4. **A trial that faulted,** with no outcome and no rig line: it has no condition, and its saccades still count toward its block. Task 2's planted session faults trial 18; Task 3's `test_each_gain_is_taken_over_the_saccades_its_block_or_condition_holds` pins it.
5. **A condition name longer than the 255-character column:** no condition, rather than a failed insert that would retry every pass. `test_placement_reads_times_as_doubles_and_names_by_record_then_stream_code` (T3e).

## The rulings this plan carries

Each of the first three is a dated amendment Task 5 adds to the spec.
- **1 (Task 1): the session fit's third check refuses a fit whose parameters are not known.** As written, a fit that does not converge or whose covariance is not finite; on input passing the first two checks that never fires, even with no saturation in range (V_max 9,352 ± 8,466 °/s). Now: peak speeds that do not vary, no convergence, or V_max's or C's standard error at least `max_relative_se` (0.5, a new paramset field) of its value. Whole reference sessions come to 3–5%, nine in ten 100-saccade samples under 28%.
- **2 (Task 2): one key per trace.** DataJoint 2.3 keys a job queue on every primary-key attribute inherited through a foreign key; `trace` comes through `-> detect.EyeDetection`, and with it missing from `key_source` the daemon wrote the left trace alone. Three reads of the recording's sync line per detection, not one.
- **3 (Task 2): the planted session's tolerance.** Each eye's fit within 12% of the fit to the planted saccades over 2–8 deg; measured 1.4–9.2%, the most NSLR's. Nyström–Holmqvist keeps only the planted saccades over 7 deg, so its fits there are refused for too few.
- **4 (Task 2): the planted main sequence is slower than a monkey's,** V_max 300 °/s and C 6 deg, so each saccade lasts 31–73 ms (16 samples or more) and the shared five-point velocity estimator reads its peak within about 1–2%. The calibration is `test_detect_populate.py`'s, against the first four trials' untouched drift.
- **5 (Task 2): the refused-detection fixture is `test_detect_populate.py::_build_mixed_eye_session`,** whose left eye's calibration is refused. Its right eye's three planted transitions are also the too-few case.
- **6 (Task 4): one report line per session, eye, validity paramset and fit paramset.** The spec says one per session per eye; with the defaults alone, as today, that is the same. A detector with no `EyeDetection` row for the session is not listed.

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/eye/detect/main_sequence.py` (new) | `MainSequenceParams`, `Curve`, `selected`, `fit_session`, `gain`, `vigor` (Task 1) |
| `wl_preproc/schema/main_sequence.py` (new) | `SaccadeMainSequence` and its parts, `selected_runs`, `register_default_paramsets`, `activate` (Tasks 2, 3) |
| `wl_preproc/daemon.py` | the table as a daemon stage, its module in the schema and paramset lists (Task 2) |
| `wl_preproc/cli/report.py` | `_vigor_lines` and the new subsection (Task 4) |
| `tests/eye/detect/test_main_sequence.py` (new) | the pure functions (Task 1) |
| `tests/schema/test_main_sequence_populate.py` (new) | the planted session and the table's tests (Tasks 2, 3) |
| `tests/cli/test_vigor_report.py` (new) | the report's vigor lines (Task 4) |
| docs, `wl.yaml` | the amendments, the handoff, the records (Task 5) |

---

### Task 1: The main sequence and vigor, as pure functions

**Files:**
- Create: `wl_preproc/eye/detect/main_sequence.py`, `tests/eye/detect/test_main_sequence.py`

**Interfaces — produces:**
- `FIT_FORMS = ("saturating_exponential",)`; `MIN_HISTORY_SESSIONS = 3`.
- `MainSequenceParams(fit_form: str, min_amplitude_deg: float, max_duration_ms: float, min_session_saccades: int, min_amplitude_ratio: float, max_relative_se: float, min_group_saccades: int)`, frozen; an unknown `fit_form` raises `ValueError`. `DEFAULT_MAIN_SEQUENCE_PARAMS` is `("saturating_exponential", 1.0, 150.0, 100, 3.0, 0.5, 30)`.
- `Curve(v_max_deg_s: float, saturation_deg: float)`, callable on sizes: `v_max * (1 - exp(-a / saturation))`.
- `SessionFit(curve: Curve | None, n_saccades, amplitude_min_deg, amplitude_max_deg, v_max_se_deg_s, saturation_se_deg, r_squared, reason: str)`; `Gain(gain, n_saccades, amplitude_min_deg, amplitude_max_deg, reason)`; `Vigor(value, n_saccades, n_history, reason)`. A `None` value means refused, and `reason` says why; a computed one has `reason == ""`.
- `selected(labels, amplitude_deg, n_samples, fs_hz, params) -> np.ndarray[bool]`.
- `fit_session(amplitude_deg, peak_velocity_deg_s, params) -> SessionFit`, taking the saccades already selected.
- `gain(amplitude_deg, peak_velocity_deg_s, curve, params) -> Gain`.
- `vigor(amplitude_deg, peak_velocity_deg_s, history: Sequence[SessionFit], params, min_history=MIN_HISTORY_SESSIONS) -> Vigor`.

- [ ] **Step 1: Write the failing tests.** Apply this diff:

```diff
--- /dev/null
+++ b/tests/eye/detect/test_main_sequence.py
@@ -0,0 +1,207 @@
+"""The main sequence and vigor, as pure functions (design spec
+`2026-10-07-main-sequence-design.md` sections 4 and 5): which saccades a fit
+takes, the session's fit and its guard, a group's gain, and a session's vigor
+against earlier sessions."""
+
+from __future__ import annotations
+
+import dataclasses
+
+import numpy as np
+import pytest
+
+from wl_preproc.eye.detect.main_sequence import (
+    DEFAULT_MAIN_SEQUENCE_PARAMS,
+    Curve,
+    MainSequenceParams,
+    SessionFit,
+    fit_session,
+    gain,
+    selected,
+    vigor,
+)
+
+PARAMS = DEFAULT_MAIN_SEQUENCE_PARAMS
+PLANTED = Curve(v_max_deg_s=400.0, saturation_deg=5.0)
+
+
+def _planted(n, low, high, *, seed, noise=0.05, curve=PLANTED, scale=1.0):
+    """`n` saccades with sizes uniform in `[low, high)` and peak speeds on
+    `curve` times `scale`, each off it by `noise` (a fraction, Gaussian)."""
+    rng = np.random.default_rng(seed)
+    amplitude = rng.uniform(low, high, n)
+    return amplitude, scale * curve(amplitude) * (1.0 + rng.normal(0.0, noise, n))
+
+
+def _history(*curves_and_ranges):
+    return [SessionFit(curve=curve, n_saccades=200, amplitude_min_deg=low, amplitude_max_deg=high,
+                       v_max_se_deg_s=1.0, saturation_se_deg=0.1, r_squared=0.9, reason="")
+            for curve, (low, high) in curves_and_ranges]
+
+
+def test_the_defaults_are_the_specs():
+    assert dataclasses.asdict(PARAMS) == {
+        "fit_form": "saturating_exponential", "min_amplitude_deg": 1.0, "max_duration_ms": 150.0,
+        "min_session_saccades": 100, "min_amplitude_ratio": 3.0, "max_relative_se": 0.5,
+        "min_group_saccades": 30,
+    }
+
+
+def test_an_unknown_fit_form_is_refused():
+    with pytest.raises(ValueError, match="power_law"):
+        dataclasses.replace(PARAMS, fit_form="power_law")
+
+
+def test_selection_takes_saccadic_runs_from_the_floor_up_to_the_ceiling():
+    """By size, not label: a 1 deg `microsaccade` is taken (section 4.1)."""
+    labels = np.array(["saccade", "microsaccade", "fixation", "saccade", "saccade", "saccade", "pso"])
+    amplitude = np.array([1.0, 1.5, 3.0, 0.99, np.nan, 2.0, 2.0])
+    n_samples = np.array([10, 10, 10, 10, 10, 75, 10])
+    assert selected(labels, amplitude, n_samples, 500.0, PARAMS).tolist() == [
+        True, True, False, False, False, True, False]
+    assert not selected(labels, amplitude, n_samples + 1, 500.0, PARAMS)[5]  # 152 ms
+
+
+def test_a_session_fit_recovers_a_planted_curve():
+    amplitude, peak = _planted(400, 1.0, 12.0, seed=1)
+    fit = fit_session(amplitude, peak, PARAMS)
+    assert fit.reason == ""
+    assert fit.curve.v_max_deg_s == pytest.approx(400.0, rel=0.05)
+    assert fit.curve.saturation_deg == pytest.approx(5.0, rel=0.10)
+    assert fit.n_saccades == 400
+    assert (fit.amplitude_min_deg, fit.amplitude_max_deg) == (amplitude.min(), amplitude.max())
+    assert 0.0 < fit.v_max_se_deg_s < 0.05 * 400.0
+    assert 0.0 < fit.saturation_se_deg < 0.10 * 5.0
+    assert fit.r_squared > 0.8
+
+
+def test_too_few_saccades_are_refused_naming_both_counts():
+    amplitude, peak = _planted(99, 1.0, 12.0, seed=2)
+    fit = fit_session(amplitude, peak, PARAMS)
+    assert fit.curve is None
+    assert fit.reason == "99 saccades; a session fit needs at least 100"
+    assert fit.n_saccades == 99
+    # Counted before the range is judged (section 4.2's order).
+    amplitude, peak = _planted(50, 6.0, 9.0, seed=2)
+    assert fit_session(amplitude, peak, PARAMS).reason == "50 saccades; a session fit needs at least 100"
+
+
+def test_saccades_spanning_6_to_9_deg_are_refused_naming_their_range():
+    """The saccade-detection spec's own section 10 fixture: fitted, they give
+    a plausible V_max that means nothing (this spec's section 1.4)."""
+    amplitude, peak = _planted(300, 6.0, 9.0, seed=3)
+    fit = fit_session(amplitude, peak, PARAMS)
+    assert fit.curve is None
+    low, high = np.percentile(amplitude, [10, 90])
+    assert fit.reason == (f"the middle 80% of their sizes spans {low:.2f}-{high:.2f} deg, a factor of "
+                          f"{high / low:.2f}; a session fit needs 3")
+    assert (fit.amplitude_min_deg, fit.amplitude_max_deg) == (amplitude.min(), amplitude.max())
+
+
+def test_one_stray_large_saccade_does_not_pass_the_range_check():
+    amplitude, peak = _planted(150, 1.0, 2.0, seed=4)
+    amplitude, peak = np.append(amplitude, 20.0), np.append(peak, PLANTED(20.0))
+    fit = fit_session(amplitude, peak, PARAMS)
+    assert fit.curve is None
+    assert fit.reason.startswith("the middle 80% of their sizes spans")
+
+
+def test_a_fit_that_does_not_determine_its_parameters_is_refused():
+    """Peak speed in proportion to size, with no saturation in range: V_max
+    and C grow together without bound, each known to about 90% (amendment
+    1)."""
+    rng = np.random.default_rng(9)
+    amplitude = np.linspace(1.0, 12.0, 200)
+    fit = fit_session(amplitude, 30.0 * amplitude * (1.0 + rng.normal(0.0, 0.05, 200)), PARAMS)
+    assert fit.curve is None
+    assert fit.reason.startswith("the fit did not determine V_max and C: V_max ")
+    assert fit.reason.endswith("; a session fit needs each known to within 50%")
+    assert len(fit.reason) <= 255
+
+
+def test_peak_speeds_that_do_not_vary_are_refused():
+    """No curve can be judged against no variation, and r-squared would be
+    minus infinity, which no column can hold."""
+    fit = fit_session(np.linspace(1.0, 12.0, 200), np.full(200, 300.0), PARAMS)
+    assert (fit.curve, fit.reason) == (None, "their peak speeds do not vary")
+
+
+def test_a_fit_that_does_not_converge_is_refused(monkeypatch):
+    """SciPy's own failure, stood in for: no input reliably makes its
+    trust-region solver give up."""
+    import scipy.optimize
+
+    def gives_up(*_args, **_kwargs):
+        raise RuntimeError("Optimal parameters not found: the maximum number of function evaluations is exceeded.")
+
+    monkeypatch.setattr(scipy.optimize, "curve_fit", gives_up)
+    amplitude, peak = _planted(200, 1.0, 12.0, seed=10)
+    fit = fit_session(amplitude, peak, PARAMS)
+    assert fit.curve is None
+    assert fit.reason == ("the fit did not converge: Optimal parameters not found: the maximum number of "
+                          "function evaluations is exceeded.")
+
+
+def test_a_gain_recovers_a_planted_speed_up():
+    amplitude, peak = _planted(60, 1.0, 10.0, seed=5, noise=0.02, scale=1.1)
+    result = gain(amplitude, peak, PLANTED, PARAMS)
+    assert result.reason == ""
+    assert result.gain == pytest.approx(1.1, abs=0.01)
+    assert result.n_saccades == 60
+    assert (result.amplitude_min_deg, result.amplitude_max_deg) == (amplitude.min(), amplitude.max())
+
+
+def test_a_gain_is_a_median_so_a_few_outliers_do_not_move_it():
+    amplitude, peak = _planted(40, 1.0, 10.0, seed=11, noise=0.0)
+    peak[:5] *= 3.0
+    assert gain(amplitude, peak, PLANTED, PARAMS).gain == pytest.approx(1.0)
+
+
+def test_a_gain_on_too_few_saccades_is_refused():
+    amplitude, peak = _planted(29, 1.0, 10.0, seed=6)
+    result = gain(amplitude, peak, PLANTED, PARAMS)
+    assert result.gain is None
+    assert result.reason == "29 saccades; a gain needs at least 30"
+
+
+def test_a_gain_on_no_saccades_is_refused_without_a_range():
+    result = gain(np.array([]), np.array([]), PLANTED, PARAMS)
+    assert (result.gain, result.n_saccades, result.amplitude_min_deg, result.amplitude_max_deg) == (
+        None, 0, None, None)
+    assert result.reason == "0 saccades; a gain needs at least 30"
+
+
+def test_vigor_compares_each_saccade_with_the_earlier_sessions_that_cover_its_size():
+    """Two earlier sessions twice as fast, fitted only up to 5 deg, and one
+    as fast, fitted to 12: at 8 deg only the third covers, so vigor is 100%.
+    Extrapolating the other two would give 50%."""
+    history = _history((Curve(800.0, 5.0), (1.0, 5.0)), (Curve(800.0, 5.0), (1.0, 5.0)), (PLANTED, (1.0, 12.0)))
+    amplitude = np.full(40, 8.0)
+    result = vigor(amplitude, PLANTED(amplitude), history, PARAMS)
+    assert result.reason == ""
+    assert result.value == pytest.approx(1.0)
+    assert (result.n_saccades, result.n_history) == (40, 3)
+
+
+def test_vigor_is_the_median_ratio_against_the_earlier_sessions_median():
+    history = _history((PLANTED, (1.0, 12.0)), (Curve(440.0, 5.0), (1.0, 12.0)), (Curve(480.0, 5.0), (1.0, 12.0)))
+    amplitude, peak = _planted(50, 1.0, 10.0, seed=7, noise=0.0, scale=0.9 * 1.1)
+    peak[:5] *= 3.0  # a few outliers do not move a median
+    result = vigor(amplitude, peak, history, PARAMS)
+    assert result.value == pytest.approx(0.9)
+
+
+def test_vigor_needs_three_earlier_sessions():
+    history = _history((PLANTED, (1.0, 12.0)), (PLANTED, (1.0, 12.0)))
+    amplitude, peak = _planted(50, 1.0, 10.0, seed=8)
+    result = vigor(amplitude, peak, history, PARAMS)
+    assert result.value is None
+    assert result.reason == "no history yet (2 earlier)"
+
+
+def test_vigor_needs_enough_saccades_the_history_covers():
+    history = _history(*[(PLANTED, (1.0, 5.0))] * 3)
+    amplitude = np.concatenate([np.full(40, 8.0), np.full(10, 3.0)])
+    result = vigor(amplitude, PLANTED(amplitude), history, PARAMS)
+    assert result.value is None
+    assert result.reason == "too few saccades (10)"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_main_sequence.py -q --tb=line -p no:cacheprovider`
Expected: 1 error during collection: `wl_preproc.eye.detect.main_sequence` does not exist.

- [ ] **Step 3: Implement.** Apply this diff:

```diff
--- /dev/null
+++ b/wl_preproc/eye/detect/main_sequence.py
@@ -0,0 +1,228 @@
+"""The main sequence and vigor (design spec
+`2026-10-07-main-sequence-design.md`): which saccades a fit takes, the
+session's fit, a group's gain against it, and a session's vigor against the
+same animal's earlier sessions.
+
+Pure functions over arrays. `schema/main_sequence.py` stores what the first
+three return; `cli/report.py` computes vigor at report time and never stores
+it, because the history it is measured against grows with every session
+(saccade-detection design spec section 6.5.1).
+
+**Only a whole session is fitted.** Measured on the reference recording, a
+two-parameter fit over a block's or a condition's 20-100 saccades is 12-55%
+wrong at the median, while one gain against the session's own curve is within
+4-7% (spec section 1.4). So blocks and conditions get a gain: the requester's
+decision of 2026-10-07.
+"""
+
+from __future__ import annotations
+
+import warnings
+from collections.abc import Sequence
+from dataclasses import dataclass
+
+import numpy as np
+
+from wl_preproc.eye.detect.labels import Label
+
+#: The fit forms built. A paramset names one, so another is new rows later.
+FIT_FORMS = ("saturating_exponential",)
+
+#: The report's minimum history: below it a session's vigor is not shown
+#: (spec section 5). A report constant, not a paramset value: it decides what
+#: is shown, not what is stored.
+MIN_HISTORY_SESSIONS = 3
+
+_SACCADIC = (Label.SACCADE.value, Label.MICROSACCADE.value)
+
+
+@dataclass(frozen=True, slots=True)
+class MainSequenceParams:
+    """The `main_sequence` paramset (spec section 4.4).
+    - `min_amplitude_deg`, `max_duration_ms`: which saccades are taken
+      (section 4.1). The floor replaces the saccade-detection spec's
+      "include microsaccades" switch.
+    - `min_session_saccades`, `min_amplitude_ratio`, `max_relative_se`: the
+      session fit's guard, checked in that order (section 4.2; the third,
+      amendment 1). The ratio is the middle 80% of the sizes', 90th
+      percentile over 10th; the relative error is a parameter's standard
+      error over its value.
+    - `min_group_saccades`: the fewest saccades a block's or condition's gain,
+      or a session's vigor, is taken over."""
+
+    fit_form: str
+    min_amplitude_deg: float
+    max_duration_ms: float
+    min_session_saccades: int
+    min_amplitude_ratio: float
+    max_relative_se: float
+    min_group_saccades: int
+
+    def __post_init__(self) -> None:
+        if self.fit_form not in FIT_FORMS:
+            raise ValueError(f"no main-sequence fit form {self.fit_form!r}; have {list(FIT_FORMS)}")
+
+
+DEFAULT_MAIN_SEQUENCE_PARAMS = MainSequenceParams(
+    fit_form="saturating_exponential", min_amplitude_deg=1.0, max_duration_ms=150.0,
+    min_session_saccades=100, min_amplitude_ratio=3.0, max_relative_se=0.5, min_group_saccades=30,
+)
+
+
+@dataclass(frozen=True, slots=True)
+class Curve:
+    """`peak_velocity = v_max * (1 - exp(-amplitude / saturation))`, degrees
+    and degrees per second."""
+
+    v_max_deg_s: float
+    saturation_deg: float
+
+    def __call__(self, amplitude_deg):
+        return self.v_max_deg_s * (1.0 - np.exp(-np.asarray(amplitude_deg, dtype=float) / self.saturation_deg))
+
+
+@dataclass(frozen=True, slots=True)
+class SessionFit:
+    """One session's fit, or its refusal: `curve` is None and `reason` says
+    why. The size range is stored either way, so a reader can judge a fit
+    that passed and see what a refused one had."""
+
+    curve: Curve | None
+    n_saccades: int
+    amplitude_min_deg: float | None
+    amplitude_max_deg: float | None
+    v_max_se_deg_s: float | None
+    saturation_se_deg: float | None
+    r_squared: float | None
+    reason: str
+
+
+@dataclass(frozen=True, slots=True)
+class Gain:
+    """A group's gain against its session's curve, or its refusal."""
+
+    gain: float | None
+    n_saccades: int
+    amplitude_min_deg: float | None
+    amplitude_max_deg: float | None
+    reason: str
+
+
+@dataclass(frozen=True, slots=True)
+class Vigor:
+    """A session's vigor against its earlier sessions, or why there is none."""
+
+    value: float | None
+    n_saccades: int
+    n_history: int
+    reason: str
+
+
+def selected(labels, amplitude_deg, n_samples, fs_hz: float, params: MainSequenceParams) -> np.ndarray:
+    """Which stored runs a fit takes (spec section 4.1): a `saccade` or
+    `microsaccade` of at least `min_amplitude_deg`, lasting at most
+    `max_duration_ms`. By size, not label: five of the seven detectors call
+    every saccadic event `saccade`, whatever its size. A NULL amplitude, an
+    unmeasured run, arrives as NaN and is never taken."""
+    amplitude = np.asarray(amplitude_deg, dtype=float)
+    duration_ms = 1000.0 * np.asarray(n_samples, dtype=float) / fs_hz
+    with np.errstate(invalid="ignore"):
+        return (np.isin(np.asarray(labels, dtype=object), _SACCADIC)
+                & (amplitude >= params.min_amplitude_deg)
+                & (duration_ms <= params.max_duration_ms))
+
+
+def _range(amplitude: np.ndarray) -> tuple[float | None, float | None]:
+    return (float(amplitude.min()), float(amplitude.max())) if amplitude.size else (None, None)
+
+
+def fit_session(amplitude_deg, peak_velocity_deg_s, params: MainSequenceParams) -> SessionFit:
+    """The saturating curve through one session's selected saccades, or a
+    refusal, checked in this order (spec section 4.2):
+    1. fewer than `min_session_saccades`;
+    2. the middle 80% of their sizes spanning less than a factor of
+       `min_amplitude_ratio`, so one stray large saccade cannot pass a session
+       of small ones;
+    3. peak speeds that do not vary, a fit that does not converge, or a V_max
+       or C whose standard error is `max_relative_se` of its value or more
+       (amendment 1). Measured, whole sessions on the reference recording
+       come to 3-5%, and nine in ten 100-saccade samples of them under 28%;
+       a relation with no saturation in range comes to 90%.
+
+    Least squares on peak velocity, both parameters held positive, started
+    from the fastest saccade and the median size."""
+    from scipy.optimize import curve_fit
+
+    amplitude = np.asarray(amplitude_deg, dtype=float)
+    peak = np.asarray(peak_velocity_deg_s, dtype=float)
+    n = int(amplitude.size)
+    low, high = _range(amplitude)
+
+    def refused(reason: str) -> SessionFit:
+        return SessionFit(None, n, low, high, None, None, None, reason)
+
+    if n < params.min_session_saccades:
+        return refused(f"{n} saccades; a session fit needs at least {params.min_session_saccades}")
+    p10, p90 = np.percentile(amplitude, [10, 90])
+    if p90 < params.min_amplitude_ratio * p10:
+        return refused(f"the middle 80% of their sizes spans {p10:.2f}-{p90:.2f} deg, a factor of "
+                       f"{p90 / p10:.2f}; a session fit needs {params.min_amplitude_ratio:g}")
+
+    def form(a, v_max, saturation):
+        return v_max * (1.0 - np.exp(-a / saturation))
+
+    total = float(np.sum((peak - peak.mean()) ** 2))
+    if total == 0.0:
+        return refused("their peak speeds do not vary")
+    try:
+        with warnings.catch_warnings():
+            warnings.simplefilter("ignore")
+            (v_max, saturation), covariance = curve_fit(
+                form, amplitude, peak, p0=(float(peak.max()), float(np.median(amplitude))),
+                bounds=([0.0, 0.0], [np.inf, np.inf]), maxfev=10_000)
+    except (RuntimeError, ValueError) as exc:
+        return refused(f"the fit did not converge: {exc}"[:255])
+    v_max_se, saturation_se = np.sqrt(np.diag(covariance))
+    if not (v_max_se < params.max_relative_se * v_max and saturation_se < params.max_relative_se * saturation):
+        return refused(f"the fit did not determine V_max and C: V_max {v_max:.4g} +/- {v_max_se:.2g} deg/s, "
+                       f"C {saturation:.4g} +/- {saturation_se:.2g} deg; a session fit needs each known to "
+                       f"within {params.max_relative_se:.0%}")
+    residual = peak - form(amplitude, v_max, saturation)
+    r_squared = 1.0 - float(np.sum(residual**2)) / total
+    return SessionFit(Curve(float(v_max), float(saturation)), n, low, high,
+                      float(v_max_se), float(saturation_se), r_squared, "")
+
+
+def gain(amplitude_deg, peak_velocity_deg_s, curve: Curve, params: MainSequenceParams) -> Gain:
+    """The median of a group's peak speeds over what `curve`, its session's
+    own fit, predicts for their sizes: 1.06 is 6% faster than the session's
+    norm (spec section 4.3). Refused below `min_group_saccades`."""
+    amplitude = np.asarray(amplitude_deg, dtype=float)
+    peak = np.asarray(peak_velocity_deg_s, dtype=float)
+    n = int(amplitude.size)
+    low, high = _range(amplitude)
+    if n < params.min_group_saccades:
+        return Gain(None, n, low, high, f"{n} saccades; a gain needs at least {params.min_group_saccades}")
+    return Gain(float(np.median(peak / curve(amplitude))), n, low, high, "")
+
+
+def vigor(amplitude_deg, peak_velocity_deg_s, history: Sequence[SessionFit], params: MainSequenceParams,
+          min_history: int = MIN_HISTORY_SESSIONS) -> Vigor:
+    """One session's vigor against `history`, its earlier sessions' computed
+    fits (spec section 5): each saccade's peak speed over the median of the
+    curves whose own fitted size range covers its size, and the median of
+    those ratios. A saccade no earlier curve covers is left out, so no curve is
+    extrapolated."""
+    amplitude = np.asarray(amplitude_deg, dtype=float)
+    peak = np.asarray(peak_velocity_deg_s, dtype=float)
+    fits = [fit for fit in history if fit.curve is not None]
+    if len(fits) < min_history:
+        return Vigor(None, 0, len(fits), f"no history yet ({len(fits)} earlier)")
+    predicted = np.array([fit.curve(amplitude) for fit in fits])
+    covers = np.array([(amplitude >= fit.amplitude_min_deg) & (amplitude <= fit.amplitude_max_deg)
+                       for fit in fits])
+    covered = covers.any(axis=0)
+    if int(covered.sum()) < params.min_group_saccades:
+        return Vigor(None, int(covered.sum()), len(fits), f"too few saccades ({int(covered.sum())})")
+    reference = np.nanmedian(np.where(covers[:, covered], predicted[:, covered], np.nan), axis=0)
+    return Vigor(float(np.median(peak[covered] / reference)), int(covered.sum()), len(fits), "")
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 18 passed.

- [ ] **Step 5: Mutation checks.** Each was measured, against the final tree, to fail exactly the tests in brackets.
  - T1a: the range from the extremes, `p10, p90 = amplitude.min(), amplitude.max()` [`test_saccades_spanning_6_to_9_deg_are_refused_naming_their_range`, `test_one_stray_large_saccade_does_not_pass_the_range_check`].
  - T1b: the range checked before the count [`test_too_few_saccades_are_refused_naming_both_counts`].
  - T1c: the relative-error check removed (`if False:`) [`test_a_fit_that_does_not_determine_its_parameters_is_refused`].
  - T1d: `gain`'s median becomes a mean [`test_a_gain_is_a_median_so_a_few_outliers_do_not_move_it`].
  - T1e: `vigor`'s median becomes a mean [`test_vigor_is_the_median_ratio_against_the_earlier_sessions_median`].
  - T1f: every earlier curve counted as covering every size [`test_vigor_compares_each_saccade_with_the_earlier_sessions_that_cover_its_size`, `test_vigor_needs_enough_saccades_the_history_covers`].
  - T1g: the label rule dropped from `selected` [`test_selection_takes_saccadic_runs_from_the_floor_up_to_the_ceiling`].
  - T1h: `duration_ms <= params.max_duration_ms` becomes `<` [same].
  - T1i: `amplitude >= params.min_amplitude_deg` becomes `>` [same].
  - T1j: `if len(fits) < min_history:` becomes `if not fits:` [`test_vigor_needs_three_earlier_sessions`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/eye/detect/main_sequence.py tests/eye/detect/test_main_sequence.py
git commit -m "feat(detect): the main sequence and vigor as pure functions -- which saccades a fit takes (by size, from 1 deg, at most 150 ms), the session's saturating fit and its guard (count, middle-80% range, then each parameter known to within half), a group's gain against the session's curve, and a session's vigor against earlier sessions' curves that cover each saccade's size

<trailer lines>"
```

---

### Task 2: `SaccadeMainSequence`, the session fit

**Files:**
- Create: `wl_preproc/schema/main_sequence.py`, `tests/schema/test_main_sequence_populate.py`
- Modify: `wl_preproc/daemon.py`

**Interfaces — consumes:** Task 1's `DEFAULT_MAIN_SEQUENCE_PARAMS`, `MainSequenceParams`, `Curve`, `fit_session`, `selected`. From the codebase: `detect.EyeDetection` and `.Run`; `paramset.register`; `read_ohdpi(path).fs_hz`; `tests/schema/test_detect_populate.py`'s `CAL_SCALE`, `TRIAL_DURATION_S`, `_build_mixed_eye_session` and `_build_stepped_session`; `tests/schema/test_eye_populate.py`'s `_expected_raw_points`, `_land`, `_write_fixations`.

**Interfaces — produces:**
- `main_sequence.SaccadeMainSequence`, keyed by `EyeDetection`'s key plus `fit_paramset_type`, `fit_paramset_idx`; columns `fit_status`, `fs_hz`, `n_saccades`, `amplitude_min_deg`, `amplitude_max_deg`, `v_max_deg_s`, `saturation_deg`, `v_max_se_deg_s`, `saturation_se_deg`, `r_squared`, `reason`. Its parts `.Block` (`+ block_id`) and `.Condition` (`+ block_id, condition`) are declared here and filled in Task 3: `gain_status`, `n_saccades`, `amplitude_min_deg`, `amplitude_max_deg`, `gain`, `reason`.
- `main_sequence.selected_runs(runs: list[dict], fs_hz: float, params) -> list[dict]`: the `EyeDetection.Run` rows a fit takes.
- `main_sequence.register_default_paramsets() -> {"default": int}`; `main_sequence.activate(prefix)`.
- `main_sequence._VARCHAR_LEN = 255`.
- In the test file: `PLANTED = Curve(300.0, 6.0)`, `FASTER = 1.1`, `_CAL_TRIALS`, `_BLOCK_TRIALS`, `_TRIAL_NUMBERS`, `_FAULTED_TRIAL = 18`, `_condition(trial_number)`, the fixtures `daemon_module`, `planted_session` (returns `(session_key, saccades)`) and `refused_session`, and the helpers `_default_fit()`, `_detector_names()`, `_stored_runs(row)`.

- [ ] **Step 1: Write the failing tests.** Apply this diff:

```diff
--- /dev/null
+++ b/tests/schema/test_main_sequence_populate.py
@@ -0,0 +1,389 @@
+"""`SaccadeMainSequence` populates (design spec
+`docs/superpowers/specs/2026-10-07-main-sequence-design.md` sections 3, 4 and
+7), through `daemon.run_once()` as production runs it, on a synthetic session
+whose saccades follow a planted main sequence.
+
+**The planted session.** Four calibration trials, as `test_detect_populate.py`
+builds them, then two blocks of eight 3 s trials holding 161 horizontal
+saccades of 1.2-10 deg. Each is a raised cosine lasting what `PLANTED` gives
+its size, drawn one frame at a time from back-to-back holds
+(`test_detect_populate.py`'s module docstring says why a single jump will not
+do). Trials in condition `contrast-50` are planted 10% faster, so a condition's
+gain has something to find. The trial numbers name the conditions through the
+generator's rig record (`synth/peripherals.py::rig_condition`): block 2
+alternates `contrast-10` and `contrast-50`, about 40 saccades each; block 3
+cycles all four, about 20 each.
+
+**Two things a real session can do, planted.** Sixty frames (120 ms) are
+dropped from the recording in a hold in block 2, so a saccade's session time
+must come from its frame number, not its row. And block 3's sixth trial
+faults: no outcome and no line in the rig record, so no condition, though its
+saccades still count toward the block.
+"""
+
+from __future__ import annotations
+
+import math
+
+import numpy as np
+import pytest
+
+from tests.identities import new_animal
+from tests.schema.test_detect_populate import CAL_SCALE, TRIAL_DURATION_S, _build_mixed_eye_session
+from wl_preproc.eye.detect.main_sequence import DEFAULT_MAIN_SEQUENCE_PARAMS, Curve, fit_session
+
+PLANTED = Curve(v_max_deg_s=300.0, saturation_deg=6.0)
+#: How much faster `contrast-50`'s saccades are planted.
+FASTER = 1.1
+_CAL_TRIALS = 4
+_BLOCK_TRIALS = 8
+# Block 1 (calibration) 1-4; block 2 alternates 0 and 2 mod 4 (`contrast-10`,
+# `contrast-50`); block 3 runs through all four.
+_TRIAL_NUMBERS = (1, 2, 3, 4, 8, 10, 12, 14, 16, 18, 20, 22, *range(23, 31))
+#: Block 3's sixth trial, counted from 1 across the session.
+_FAULTED_TRIAL = 18
+_DROPPED_FRAMES = 60
+# The most the fitted curve may stray from the planted one over 2-8 deg. The
+# detectors clip each raised cosine's slow tails, so amplitudes come out a
+# little short, and the session's own calibration is 4% under `CAL_SCALE`.
+# Measured: 1.4-9.2%, the most NSLR's.
+_CURVE_TOLERANCE = 0.12
+
+
+def _condition(trial_number: int) -> str:
+    from wl_preproc.synth.peripherals import rig_condition
+
+    return rig_condition(trial_number)[0]
+
+
+def _planted_saccades(start_s: float, end_s: float, seed: int) -> list[tuple[float, float, float, float]]:
+    """`(onset_s, duration_s, from_x_px, to_x_px)`: horizontal saccades of
+    1.2-10 deg, log-uniform, 0.20-0.30 s apart, each turning back toward the
+    centre. A raised cosine of amplitude `A` lasting `T` peaks at
+    `pi * A / (2 * T)`, so each lasts what puts its peak on `PLANTED`, or
+    `FASTER` times it in a `contrast-50` trial."""
+    rng = np.random.default_rng(seed)
+    out, x_px, onset_s = [], 0.0, start_s + 0.2
+    while True:
+        amplitude_deg = math.exp(rng.uniform(math.log(1.2), math.log(10.0)))
+        trial_number = _TRIAL_NUMBERS[int(onset_s // TRIAL_DURATION_S)]
+        peak = float(PLANTED(amplitude_deg)) * (FASTER if _condition(trial_number) == "contrast-50" else 1.0)
+        duration_s = math.pi * amplitude_deg / (2.0 * peak)
+        if onset_s + duration_s + 0.3 > end_s:
+            return out
+        step_px = amplitude_deg / CAL_SCALE * (-1.0 if x_px > 0 else 1.0)
+        out.append((onset_s, duration_s, x_px, x_px + step_px))
+        x_px += step_px
+        onset_s += duration_s + rng.uniform(0.20, 0.30)
+
+
+def _fixations(saccades, start_s: float, end_s: float) -> list:
+    """Back-to-back holds: one between saccades, and one per frame through
+    each saccade, at its raised cosine's value at the frame's centre."""
+    from wl_preproc.synth.ohdpi import OHDPI_FPS
+    from wl_preproc.synth.recipe import EyeFixationSpec
+
+    out, cursor = [], start_s
+    for onset_s, duration_s, from_px, to_px in saccades:
+        out.append(EyeFixationSpec(start_s=cursor, end_s=onset_s, x_px=from_px, y_px=0.0))
+        n_frames = max(1, round(duration_s * OHDPI_FPS))
+        for k in range(n_frames):
+            phase = min((k + 0.5) / OHDPI_FPS / duration_s, 1.0)
+            out.append(EyeFixationSpec(
+                start_s=onset_s + k / OHDPI_FPS, end_s=onset_s + (k + 1) / OHDPI_FPS,
+                x_px=from_px + (to_px - from_px) * (1.0 - math.cos(math.pi * phase)) / 2.0, y_px=0.0))
+        cursor = onset_s + n_frames / OHDPI_FPS
+    out.append(EyeFixationSpec(start_s=cursor, end_s=end_s, x_px=saccades[-1][3], y_px=0.0))
+    return out
+
+
+def _build_planted_session(tmp_path_factory, session, seed: int):
+    """Generate, land and calibrate the planted session, stopping short of
+    the daemon. Returns `(session_key, saccades)`."""
+    from wl_preproc.contracts.events import TaskTypeCode
+    from wl_preproc.schema import core, timebase
+    from wl_preproc.synth.recipe import BlockSpec, MontageSpec, SessionRecipe
+    from wl_preproc.synth.session import generate_session
+
+    from tests.schema.test_eye_populate import _expected_raw_points, _land, _write_fixations
+
+    from wl_preproc.synth.ohdpi import OHDPI_FPS, OHDPI_PRE_ROLL_S
+
+    n_trials = _CAL_TRIALS + 2 * _BLOCK_TRIALS
+    start_s, end_s = _CAL_TRIALS * TRIAL_DURATION_S, n_trials * TRIAL_DURATION_S
+    saccades = _planted_saccades(start_s, end_s, seed)
+    # The middle of the hold before the first saccade from 18 s, in block 2.
+    after = next(index for index, saccade in enumerate(saccades) if saccade[0] >= 18.0)
+    hold_s = (saccades[after - 1][0] + saccades[after - 1][1] + saccades[after][0]) / 2.0
+    first_dropped = int((hold_s + OHDPI_PRE_ROLL_S) * OHDPI_FPS) - _DROPPED_FRAMES // 2
+    recipe = SessionRecipe(
+        session_id=session.session_id, subject=session.subject, rig="rig-a", systems=("syncbox", "ohdpi"),
+        blocks=tuple(BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=n, trial_duration_s=TRIAL_DURATION_S)
+                     for n in (_CAL_TRIALS, _BLOCK_TRIALS, _BLOCK_TRIALS)),
+        montages=(MontageSpec(start_s=0.0, end_s=end_s),),
+        n_ap_channels=4, ap_sample_rate_hz=30_000.0, seed=seed,
+        trial_numbers=_TRIAL_NUMBERS, faulted_trials=(_FAULTED_TRIAL,),
+        ohdpi_dropped_frames=tuple(range(first_dropped, first_dropped + _DROPPED_FRAMES)),
+        eye_fixations=tuple(_fixations(saccades, start_s, end_s)),
+    )
+    root = tmp_path_factory.mktemp(f"mainseq{seed}")
+    truth = generate_session(root, recipe)
+    session_dir = root / recipe.session_id
+    session_key = _land(root, recipe, session.session_datetime, acquisition_systems=("syncbox", "ohdpi"))
+    timebase.SystemTimebase.populate()
+    core.Segment.populate()
+    segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
+    # Calibrated as `test_detect_populate.py::_build_stepped_session` is: an
+    # affine against the untouched drift of the first four trials.
+    window_starts = [index * TRIAL_DURATION_S + 1.0 for index in range(_CAL_TRIALS)]
+    raw_points = _expected_raw_points(session_dir, segment, "Left", [(s + 0.2, s + 0.8) for s in window_starts])
+    targets = [(CAL_SCALE * raw[0], CAL_SCALE * raw[1]) for raw in raw_points]
+    _write_fixations(session_dir, recipe, truth, list(zip(window_starts, targets, strict=True)))
+    return session_key, saccades
+
+
+@pytest.fixture(scope="module")
+def daemon_module(dj_conn, prefix):
+    """Activation only: `daemon.run_once()` registers the paramsets."""
+    from wl_preproc import daemon
+
+    daemon.activate_all(prefix=prefix)
+    return daemon
+
+
+@pytest.fixture(scope="module")
+def planted_session(daemon_module, prefix, tmp_path_factory):
+    session_key, saccades = _build_planted_session(tmp_path_factory, new_animal().session(), seed=1007)
+    daemon_module.run_once(prefix=prefix)
+    return session_key, saccades
+
+
+@pytest.fixture(scope="module")
+def refused_session(daemon_module, prefix, tmp_path_factory):
+    """`test_detect_populate.py`'s mixed-eye session: the left eye's
+    calibration refused, so its trace and the both-eyes trace are refused
+    for every detector, and the right eye's three planted transitions are far
+    too few for a fit."""
+    session = new_animal().session()
+    session_key, _report, _onsets = _build_mixed_eye_session(
+        daemon_module, prefix, tmp_path_factory, dirname="mainseqmixed", session_id=session.session_id,
+        subject=session.subject, session_datetime=session.session_datetime, seed=1008, refused_eye="left")
+    return session_key
+
+
+def _default_fit() -> dict:
+    from wl_preproc.schema import main_sequence
+
+    return {"fit_paramset_type": "main_sequence",
+            "fit_paramset_idx": main_sequence.register_default_paramsets()["default"]}
+
+
+def _detector_names() -> dict[int, str]:
+    from wl_preproc.schema import paramset
+
+    return {row["paramset_idx"]: row["params"]["detector"]
+            for row in (paramset.ParamSet & {"paramset_type": "eye_detection"}).to_dicts()}
+
+
+def _stored_runs(row: dict) -> list[dict]:
+    from wl_preproc.schema import detect
+
+    key = {name: row[name] for name in detect.EyeDetection.primary_key}
+    return (detect.EyeDetection.Run & key & 'label in ("saccade", "microsaccade")').to_dicts(order_by="run_index")
+
+
+def test_every_trace_of_every_detection_gets_one_row(planted_session):
+    from wl_preproc.schema import detect, main_sequence
+
+    session_key, _saccades = planted_session
+    detections = {(row["trace"], row["paramset_idx"]) for row in (detect.EyeDetection & session_key).to_dicts()}
+    rows = (main_sequence.SaccadeMainSequence & session_key & _default_fit()).to_dicts()
+    assert len(detections) == 21
+    assert sorted((row["trace"], row["paramset_idx"]) for row in rows) == sorted(detections)
+
+
+def test_each_stored_fit_is_the_fit_of_its_traces_selected_runs(planted_session):
+    """The selection, the rate and the columns: every stored value is what
+    `fit_session` gives the stored runs `selected_runs` picks."""
+    from wl_preproc.eye.ohdpi import read_ohdpi
+    from wl_preproc.schema import core, ingest, main_sequence
+
+    session_key, _saccades = planted_session
+    segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
+    session_dir = (ingest.Ingestion & session_key).fetch1("session_dir")
+    fs_hz = read_ohdpi(f"{session_dir}/ohdpi/{segment['file_path']}").fs_hz
+    rows = (main_sequence.SaccadeMainSequence & session_key & _default_fit()).to_dicts()
+    for row in rows:
+        assert row["fs_hz"] == fs_hz
+        runs = main_sequence.selected_runs(_stored_runs(row), fs_hz, DEFAULT_MAIN_SEQUENCE_PARAMS)
+        fit = fit_session([run["amplitude_deg"] for run in runs], [run["peak_velocity_deg_s"] for run in runs],
+                          DEFAULT_MAIN_SEQUENCE_PARAMS)
+        assert row["fit_status"] == ("refused" if fit.curve is None else "computed")
+        assert (row["n_saccades"], row["reason"]) == (fit.n_saccades, fit.reason)
+        assert row["amplitude_min_deg"] == pytest.approx(fit.amplitude_min_deg)
+        assert row["amplitude_max_deg"] == pytest.approx(fit.amplitude_max_deg)
+        if fit.curve is not None:
+            assert row["v_max_deg_s"] == pytest.approx(fit.curve.v_max_deg_s)
+            assert row["saturation_deg"] == pytest.approx(fit.curve.saturation_deg)
+            assert row["v_max_se_deg_s"] == pytest.approx(fit.v_max_se_deg_s)
+            assert row["saturation_se_deg"] == pytest.approx(fit.saturation_se_deg)
+            assert row["r_squared"] == pytest.approx(fit.r_squared)
+
+
+def test_a_session_fit_recovers_the_planted_main_sequence(planted_session):
+    """Each eye's fit, for every detector that computed one, against the fit
+    to the planted saccades themselves: within `_CURVE_TOLERANCE` over 2-8
+    deg. Engbert-Kliegl, the baseline, must compute on both eyes."""
+    from wl_preproc.schema import main_sequence
+
+    session_key, saccades = planted_session
+    amplitude = np.array([abs(to_px - from_px) * CAL_SCALE for _onset, _duration, from_px, to_px in saccades])
+    peak = np.pi * amplitude / (2.0 * np.array([duration for _onset, duration, _from, _to in saccades]))
+    planted = fit_session(amplitude, peak, DEFAULT_MAIN_SEQUENCE_PARAMS).curve
+    grid = np.linspace(2.0, 8.0, 61)
+    names = _detector_names()
+    computed = {}
+    for row in (main_sequence.SaccadeMainSequence & session_key & _default_fit()
+                & 'trace in ("left", "right")' & 'fit_status = "computed"').to_dicts():
+        fitted = Curve(row["v_max_deg_s"], row["saturation_deg"])
+        computed[(names[row["paramset_idx"]], row["trace"])] = float(np.max(np.abs(fitted(grid) / planted(grid) - 1.0)))
+    assert {("engbert_kliegl", "left"), ("engbert_kliegl", "right")} <= set(computed)
+    assert max(computed.values()) <= _CURVE_TOLERANCE, computed
+
+
+def test_the_fit_paramset_is_in_the_key(planted_session):
+    """A second `main_sequence` paramset writes a row of its own for every
+    trace, the old rows untouched. From 2 deg no detector takes more
+    saccades, and Engbert-Kliegl takes fewer."""
+    import dataclasses
+
+    from wl_preproc.schema import main_sequence, paramset
+
+    session_key, _saccades = planted_session
+    second = dataclasses.replace(DEFAULT_MAIN_SEQUENCE_PARAMS, min_amplitude_deg=2.0)
+    index = paramset.register("main_sequence", dataclasses.asdict(second))
+    second_fit = {"fit_paramset_type": "main_sequence", "fit_paramset_idx": index}
+    try:
+        before = (main_sequence.SaccadeMainSequence & session_key & _default_fit()).to_dicts()
+        main_sequence.SaccadeMainSequence.populate({**session_key, **second_fit})
+        rows = (main_sequence.SaccadeMainSequence & session_key & second_fit).to_dicts()
+        assert len(rows) == len(before) == 21
+        assert (main_sequence.SaccadeMainSequence & session_key & _default_fit()).to_dicts() == before
+        by_trace = {(row["trace"], row["paramset_idx"]): row["n_saccades"] for row in before}
+        names = _detector_names()
+        for row in rows:
+            runs = main_sequence.selected_runs(_stored_runs(row), row["fs_hz"], second)
+            assert row["n_saccades"] == len(runs) <= by_trace[(row["trace"], row["paramset_idx"])]
+            if names[row["paramset_idx"]] == "engbert_kliegl":
+                assert row["n_saccades"] < by_trace[(row["trace"], row["paramset_idx"])]
+    finally:
+        (paramset.ParamSet & {"paramset_type": "main_sequence", "paramset_idx": index}).delete()
+
+
+def test_a_refused_detection_gets_a_refused_row_quoting_it(refused_session):
+    from wl_preproc.schema import detect, main_sequence
+
+    rows = (main_sequence.SaccadeMainSequence & refused_session & _default_fit()
+            & 'trace in ("left", "conjunction")').to_dicts()
+    assert len(rows) == 14
+    for row in rows:
+        detection = (detect.EyeDetection & {name: row[name] for name in detect.EyeDetection.primary_key}).fetch1()
+        assert detection["status"] == "refused"
+        assert (row["fit_status"], row["fs_hz"], row["n_saccades"]) == ("refused", None, 0)
+        assert row["reason"] == f"detection refused: {detection['reason']}"[:255]
+
+
+def test_too_few_saccades_are_refused_with_their_count(refused_session):
+    from wl_preproc.schema import main_sequence
+
+    rows = (main_sequence.SaccadeMainSequence & refused_session & _default_fit() & {"trace": "right"}).to_dicts()
+    assert len(rows) == 7
+    for row in rows:
+        runs = main_sequence.selected_runs(_stored_runs(row), row["fs_hz"], DEFAULT_MAIN_SEQUENCE_PARAMS)
+        assert row["fit_status"] == "refused"
+        assert row["fs_hz"] is not None
+        assert row["reason"] == f"{len(runs)} saccades; a session fit needs at least 100"
+
+
+_MAIN_SEQUENCE_PROBE = """
+import json
+import os
+import sys
+from pathlib import Path
+
+import datajoint as dj
+
+sys.path.insert(0, os.getcwd())
+
+from wl_preproc.schema._compat import apply_datajoint_compat
+
+apply_datajoint_compat()
+dj.config["database.host"] = os.environ["WLPP_PROBE_HOST"]
+dj.config["database.port"] = int(os.environ["WLPP_PROBE_PORT"])
+dj.config["database.user"] = os.environ["WLPP_PROBE_USER"]
+dj.config["database.password"] = os.environ["WLPP_PROBE_PASSWORD"]
+dj.config["safemode"] = False
+dj.logger.setLevel("ERROR")
+
+from wl_preproc import daemon
+from wl_preproc.cli.main import main
+from wl_preproc.schema import main_sequence
+
+from tests.identities import new_animal
+from tests.schema.test_detect_populate import _build_stepped_session
+
+
+class _Factory:
+    def __init__(self, root):
+        self.root = Path(root)
+
+    def mktemp(self, name):
+        made = self.root / name
+        made.mkdir(parents=True, exist_ok=True)
+        return made
+
+
+prefix = os.environ["WLPP_PROBE_PREFIX"]
+# Activation only. Nothing here registers a paramset: that is the question.
+daemon.activate_all(prefix=prefix)
+session = new_animal().session()
+_build_stepped_session(
+    _Factory(os.environ["WLPP_PROBE_ROOT"]), dirname="probe", session_id=session.session_id,
+    subject=session.subject, session_datetime=session.session_datetime, seed=1009,
+)
+assert main(["daemon", "--prefix", prefix]) == 0
+rows = main_sequence.SaccadeMainSequence().to_dicts()
+print("PROBE " + json.dumps({"rows": len(rows), "traces": sorted({row["trace"] for row in rows})}))
+"""
+
+
+def test_a_real_wlpp_daemon_pass_writes_main_sequence_rows_registering_nothing_itself(dj_conn, tmp_path):
+    """`wlpp daemon`, the real entry point, against a prefix where nothing has
+    ever registered a paramset: one row for each trace of each detector. It
+    fails if the table is not a daemon stage, runs before the detections, or
+    has no paramset registered in production (finding H1's shape)."""
+    import json
+    import os
+    import subprocess
+    import sys
+
+    import datajoint as dj
+
+    from wl_preproc.eye.detect.registry import DETECTORS
+
+    result = subprocess.run(
+        [sys.executable, "-c", _MAIN_SEQUENCE_PROBE], capture_output=True, text=True,
+        env={**os.environ,
+             "WLPP_PROBE_HOST": str(dj.config["database.host"]),
+             "WLPP_PROBE_PORT": str(dj.config["database.port"]),
+             "WLPP_PROBE_USER": str(dj.config["database.user"]),
+             "WLPP_PROBE_PASSWORD": str(dj.config["database.password"]),
+             "WLPP_PROBE_PREFIX": "ms_",
+             "WLPP_PROBE_ROOT": str(tmp_path),
+             "PYTHONDONTWRITEBYTECODE": "1"},
+    )
+    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
+    marker = next((line for line in result.stdout.splitlines() if line.startswith("PROBE ")), None)
+    assert marker is not None, f"probe printed no result:\n{result.stdout}\n{result.stderr}"
+    assert json.loads(marker[len("PROBE "):]) == {"rows": 3 * len(DETECTORS),
+                                                  "traces": ["conjunction", "left", "right"]}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_main_sequence_populate.py -q --tb=line -p no:cacheprovider`
Expected: 7 failed: `wl_preproc.schema.main_sequence` does not exist (the probe's subprocess reports the same import).

- [ ] **Step 3: Implement the table.** Apply this diff:

```diff
--- /dev/null
+++ b/wl_preproc/schema/main_sequence.py
@@ -0,0 +1,180 @@
+# wl_preproc/schema/main_sequence.py
+"""`SaccadeMainSequence`: each detection trace's main-sequence fit, and each
+block's and condition's gain against it (design spec
+`docs/superpowers/specs/2026-10-07-main-sequence-design.md` section 3).
+
+What is fitted, and how, is `eye/detect/main_sequence.py`'s. This module picks
+the stored runs, places them in blocks and conditions, and stores what comes
+back. Vigor is never stored: `cli/report.py` computes it from these rows,
+because the history it is measured against grows with every session.
+"""
+
+from __future__ import annotations
+
+from dataclasses import asdict
+from pathlib import Path
+
+import datajoint as dj
+import numpy as np
+
+from wl_preproc.eye.detect.main_sequence import (
+    DEFAULT_MAIN_SEQUENCE_PARAMS,
+    MainSequenceParams,
+    fit_session,
+    selected,
+)
+from wl_preproc.schema import DEFAULT_PREFIX, detect, paramset, pipeline
+
+schema = dj.Schema()
+
+#: The width of `reason`, and of `.Condition`'s `condition`: a longer reason
+#: is cut to it, and a longer condition name is no condition.
+_VARCHAR_LEN = 255
+
+
+@schema
+class SaccadeMainSequence(dj.Computed):
+    definition = """
+    # One detection trace's main sequence: its saturating fit, or why there is none.
+    # Key: (subject, session_datetime, trace, validity_paramset_type,
+    # validity_paramset_idx, paramset_type, paramset_idx, fit_paramset_type,
+    # fit_paramset_idx).
+    -> detect.EyeDetection
+    # Both columns renamed, not only the index: `EyeDetection`'s own
+    # definition records what a bare `paramset_type` shared by two references
+    # to one table does.
+    -> paramset.ParamSet.proj(fit_paramset_type='paramset_type', fit_paramset_idx='paramset_idx')
+    ---
+    fit_status : enum('computed','refused')
+    # The rate durations were counted at, as `EyeDetection.make` counts them
+    # (`read_ohdpi(...).fs_hz`), so the report picks exactly the saccades the
+    # fit picked. NULL where this trace's detection was refused.
+    fs_hz=null             : double
+    n_saccades             : int unsigned
+    amplitude_min_deg=null : double
+    amplitude_max_deg=null : double
+    # peak_velocity = v_max * (1 - exp(-amplitude / saturation)); NULL when refused.
+    v_max_deg_s=null       : double
+    saturation_deg=null    : double
+    v_max_se_deg_s=null    : double
+    saturation_se_deg=null : double
+    # On peak velocity, not its logarithm.
+    r_squared=null         : double
+    reason=''              : varchar(255)
+    """
+
+    class Block(dj.Part):
+        definition = """
+        # One block's gain against its session's fit: the median of its
+        # saccades' peak speed over the fit's prediction for their size.
+        # Key: the master's, and block_id.
+        -> master
+        -> pipeline.trial.Block
+        ---
+        gain_status            : enum('computed','refused')
+        n_saccades             : int unsigned
+        amplitude_min_deg=null : double
+        amplitude_max_deg=null : double
+        gain=null              : double
+        reason=''              : varchar(255)
+        """
+
+    class Condition(dj.Part):
+        definition = """
+        # One condition's gain within one block, as `.Block`'s.
+        # Key: the master's, block_id, and condition.
+        -> master
+        -> pipeline.trial.Block
+        # The rig record's name, else the stream's CONDITION number as text.
+        condition : varchar(255)
+        ---
+        gain_status            : enum('computed','refused')
+        n_saccades             : int unsigned
+        amplitude_min_deg=null : double
+        amplitude_max_deg=null : double
+        gain=null              : double
+        reason=''              : varchar(255)
+        """
+
+    @property
+    def key_source(self):
+        """Every `EyeDetection` row, refused ones included, times every
+        `main_sequence` paramset: one key per trace.
+
+        **Not collapsed over `trace`** as `EyeDetection.key_source` collapses
+        over `eye`. DataJoint 2.3 keys a table's job queue on every
+        primary-key attribute inherited through a foreign key, and `trace`
+        comes here through `-> detect.EyeDetection`; with it missing from
+        this `key_source`, the daemon's pass wrote the left trace alone
+        (design spec amendment 2).
+
+        No events requirement is needed: `EyeDetection` needs `EyeValidity`,
+        which needs `EyeCalibration` to have run, and `EyeCalibration.
+        key_source` requires `pipeline.event.BehaviorRecording`. A session's
+        blocks and trials are assembled before its first key appears, which
+        matters because a populated key is never revisited."""
+        fits = (paramset.ParamSet & {"paramset_type": "main_sequence"}).proj(
+            fit_paramset_type="paramset_type", fit_paramset_idx="paramset_idx")
+        return detect.EyeDetection.proj() * fits
+
+    def make(self, key: dict) -> None:
+        """One trace's fit, or its refusal. A trace whose detection was
+        refused gets a refused row quoting it."""
+        from wl_preproc.eye.ohdpi import read_ohdpi
+        from wl_preproc.schema import core, ingest
+
+        params = MainSequenceParams(**(paramset.ParamSet & {
+            "paramset_type": key["fit_paramset_type"], "paramset_idx": key["fit_paramset_idx"],
+        }).fetch1("params"))
+        detection_key = {name: key[name] for name in detect.EyeDetection.primary_key}
+        detection = (detect.EyeDetection & detection_key).fetch1()
+        if detection["status"] == "refused":
+            self.insert1({**key, "fit_status": "refused", "n_saccades": 0,
+                          "reason": f"detection refused: {detection['reason']}"[:_VARCHAR_LEN]})
+            return
+
+        session_key = {name: key[name] for name in pipeline.Session.primary_key}
+        session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
+        segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
+        fs_hz = read_ohdpi(session_dir / "ohdpi" / segment["file_path"]).fs_hz
+        runs = selected_runs((detect.EyeDetection.Run & detection_key & 'label in ("saccade", "microsaccade")')
+                             .to_dicts(order_by="run_index"), fs_hz, params)
+        fit = fit_session([run["amplitude_deg"] for run in runs], [run["peak_velocity_deg_s"] for run in runs],
+                          params)
+        curve = fit.curve
+        self.insert1({
+            **key, "fit_status": "refused" if curve is None else "computed", "fs_hz": fs_hz,
+            "n_saccades": fit.n_saccades, "amplitude_min_deg": fit.amplitude_min_deg,
+            "amplitude_max_deg": fit.amplitude_max_deg,
+            "v_max_deg_s": None if curve is None else curve.v_max_deg_s,
+            "saturation_deg": None if curve is None else curve.saturation_deg,
+            "v_max_se_deg_s": fit.v_max_se_deg_s, "saturation_se_deg": fit.saturation_se_deg,
+            "r_squared": fit.r_squared, "reason": fit.reason,
+        })
+
+
+def selected_runs(runs: list[dict], fs_hz: float, params: MainSequenceParams) -> list[dict]:
+    """The `EyeDetection.Run` rows a fit takes (`main_sequence.selected`),
+    the one place the stored columns meet that rule: `make()` and the report
+    both pick saccades through here."""
+    if not runs:
+        return []
+    take = selected(
+        [run["label"] for run in runs],
+        [np.nan if run["amplitude_deg"] is None else run["amplitude_deg"] for run in runs],
+        [run["run_stop"] - run["run_start"] for run in runs],
+        fs_hz, params,
+    )
+    return [run for run, taken in zip(runs, take, strict=True) if taken]
+
+
+def register_default_paramsets() -> dict[str, int]:
+    """The default `main_sequence` paramset (spec section 4.4), by name."""
+    return {"default": paramset.register("main_sequence", asdict(DEFAULT_MAIN_SEQUENCE_PARAMS))}
+
+
+def activate(prefix: str = DEFAULT_PREFIX) -> None:
+    """Bind this table to `{prefix}main_sequence`. Idempotent."""
+    detect.activate(prefix=prefix)
+    if not schema.is_activated():
+        schema.activate(f"{prefix}main_sequence", create_tables=True)
```

- [ ] **Step 4: Run the daemon's discovering tests to verify they catch the missing wiring**

Run: `.venv/bin/python -m pytest tests/schema/test_daemon.py -q --tb=line -p no:cacheprovider`
Expected: 1 failed, 16 passed: `test_every_schema_module_is_swept_for_job_tables` finds `main_sequence` unlisted.

- [ ] **Step 5: Wire it into the daemon.** Apply this diff:

```diff
--- a/wl_preproc/daemon.py
+++ b/wl_preproc/daemon.py
@@ -45,6 +45,7 @@ from wl_preproc.schema import (
     events,
     eye,
     ingest,
+    main_sequence,
     nwb,
     paramset,
     # Imported, but deliberately NOT one of `_PROJECT_SCHEMA_MODULES` below --
@@ -202,6 +203,10 @@ def _computed_tables() -> list:
         # populated is never revisited, so the lag would be permanent per
         # session rather than self-correcting within a pass.
         consensus.DetectorAgreement,
+        # BELOW `detect.EyeDetection` for `DetectorAgreement`'s reason: its
+        # `key_source` is that table's rows, so above it a session's first
+        # pass would name no key and its fits would wait a whole pass.
+        main_sequence.SaccadeMainSequence,
         # Last: it counts segments and rejections, so it must run after
         # whatever produces them or it records a session as cleaner than it is.
         timebase.TimingProvenance,
@@ -321,6 +326,7 @@ _PROJECT_SCHEMA_MODULES: tuple[tuple[str, object], ...] = (
     ("events", events),
     ("eye", eye),
     ("ingest", ingest),
+    ("main_sequence", main_sequence),
     ("nwb", nwb),
     ("paramset", paramset),
     ("request", request),
@@ -368,14 +374,15 @@ def activate_all(prefix: str = DEFAULT_PREFIX) -> None:
 # shape `_PROJECT_SCHEMA_MODULES` above uses, and for the same reason: a
 # `pkgutil`/`getattr` sweep inside `wl_preproc/` would be the dynamic import
 # the outbound guardrail bans, and a written list is what makes this
-# auditable by reading. One entry today.
+# auditable by reading. Two entries: `detect`'s detectors and validity mask,
+# and `main_sequence`'s fit.
 #
 # `test_every_schema_module_that_declares_default_paramsets_is_registered`
 # is the completeness claim: it DISCOVERS which modules declare such a
 # function and fails if one is missing here. `detect`'s own omission -- from
 # production entirely, not merely from a list -- is finding H1, and this
 # tuple exists so the second such module cannot repeat it silently.
-_PARAMSET_MODULES: tuple[tuple[str, object], ...] = (("detect", detect),)
+_PARAMSET_MODULES: tuple[tuple[str, object], ...] = (("detect", detect), ("main_sequence", main_sequence))
 
 
 def register_default_paramsets() -> dict[str, dict[str, int]]:
```

- [ ] **Step 6: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/schema/test_main_sequence_populate.py tests/schema/test_daemon.py -q --tb=line -p no:cacheprovider`
Expected: 24 passed.

- [ ] **Step 7: Mutation checks.** Each was measured, against the final tree, to fail the test in brackets.
  - T2a: `key_source` collapsed over `trace`, `(dj.U("subject", "session_datetime", "validity_paramset_type", "validity_paramset_idx", "paramset_type", "paramset_idx") & detect.EyeDetection) * fits` [`test_every_trace_of_every_detection_gets_one_row`].
  - T2b: the rate from the segment, `fs_hz = segment["n_samples"] / (segment["end_s"] - segment["start_s"])` [`test_each_stored_fit_is_the_fit_of_its_traces_selected_runs`].
  - T2c: a refused detection's reason not quoted, `"reason": "detection refused"` [`test_a_refused_detection_gets_a_refused_row_quoting_it`].
  - T2d: every fit stored as computed [`test_each_stored_fit_is_the_fit_of_its_traces_selected_runs`].
  - T2e: `main_sequence.SaccadeMainSequence` removed from `_computed_tables()` [`test_a_real_wlpp_daemon_pass_writes_main_sequence_rows_registering_nothing_itself`].
  - T2f: `("main_sequence", main_sequence)` removed from `_PARAMSET_MODULES` [same].

- [ ] **Step 8: Commit**

```bash
git add wl_preproc/schema/main_sequence.py wl_preproc/daemon.py tests/schema/test_main_sequence_populate.py
git commit -m "feat(schema): SaccadeMainSequence -- each detection trace's session fit, one key per trace, or a refusal quoting the detection's or the fit's reason; the default main_sequence paramset registered in production, the table a daemon stage below the detections; tested on a synthetic session whose saccades follow a planted main sequence, with frames dropped and a trial faulted as a real session can

<trailer lines>"
```

---

### Task 3: Each block's and condition's gain

**Files:**
- Modify: `wl_preproc/schema/main_sequence.py`, `tests/schema/test_main_sequence_populate.py`

**Interfaces — consumes:** Task 1's `gain` and `Gain`; Task 2's table, parts, `selected_runs`, `_VARCHAR_LEN`, fixtures and helpers. From the codebase: `eye.row_session_times(segment, offsets)`; `events/runs.py::stored_doubles`; `events/rigtrials.py::read_rig_trials`; `nwb/conditions.py::join`, `stream_codes` and `trial_columns`; `pipeline.trial.Block`, `.Trial`, `.BlockTrial`; `pipeline.event.Event.Attribute`.

**Interfaces — produces:**
- `main_sequence._groups(session_key, session_dir, start_s) -> ([(block_id, mask)], [((block_id, condition), mask)])`, the conditions sorted.
- `main_sequence._gain_row(key, result: Gain) -> dict`.
- `.Block` and `.Condition` rows for every computed session fit.

- [ ] **Step 1: Write the failing tests.** Apply this diff:

```diff
--- a/tests/schema/test_main_sequence_populate.py
+++ b/tests/schema/test_main_sequence_populate.py
@@ -23,6 +23,7 @@ saccades still count toward the block.
 
 from __future__ import annotations
 
+import datetime
 import math
 
 import numpy as np
@@ -305,6 +306,165 @@ def test_too_few_saccades_are_refused_with_their_count(refused_session):
         assert row["reason"] == f"{len(runs)} saccades; a session fit needs at least 100"
 
 
+def _computed_masters(session_key) -> list[dict]:
+    from wl_preproc.schema import main_sequence
+
+    return (main_sequence.SaccadeMainSequence & session_key & _default_fit() & 'fit_status = "computed"').to_dicts()
+
+
+def _master_key(row: dict) -> dict:
+    from wl_preproc.schema import main_sequence
+
+    return {name: row[name] for name in main_sequence.SaccadeMainSequence.primary_key}
+
+
+def _selected_with_starts(session_key, master: dict):
+    """The saccades a master's fit took, their session start times, sizes and
+    peak speeds."""
+    from wl_preproc.eye.ohdpi import read_ohdpi
+    from wl_preproc.schema import core, ingest, main_sequence
+    from wl_preproc.schema import eye as eye_schema
+
+    segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
+    session_dir = (ingest.Ingestion & session_key).fetch1("session_dir")
+    recording = read_ohdpi(f"{session_dir}/ohdpi/{segment['file_path']}")
+    times = eye_schema.row_session_times(segment, recording.frame_numbers - recording.frame_numbers[0])
+    runs = main_sequence.selected_runs(_stored_runs(master), master["fs_hz"], DEFAULT_MAIN_SEQUENCE_PARAMS)
+    return (times[[run["run_start"] for run in runs]], np.array([run["amplitude_deg"] for run in runs]),
+            np.array([run["peak_velocity_deg_s"] for run in runs]))
+
+
+def _planted_groups(start_s: np.ndarray):
+    """Each block's and each block's conditions' saccades, placed from where
+    the recipe put its blocks and trials (back to back from session time 0,
+    `synth/timeline.py::build_timeline`) and named from the generator's rig
+    record -- not read back from the database. The faulted trial has no
+    condition."""
+    bounds = [0, _CAL_TRIALS, _CAL_TRIALS + _BLOCK_TRIALS, _CAL_TRIALS + 2 * _BLOCK_TRIALS]
+    blocks, conditions = {}, {}
+    for block_id in (1, 2, 3):
+        first, last = bounds[block_id - 1], bounds[block_id]
+        blocks[block_id] = (start_s >= first * TRIAL_DURATION_S) & (start_s < last * TRIAL_DURATION_S)
+        for index in range(first, last):
+            if index + 1 == _FAULTED_TRIAL:
+                continue
+            inside = (start_s >= index * TRIAL_DURATION_S) & (start_s < (index + 1) * TRIAL_DURATION_S)
+            group = (block_id, _condition(_TRIAL_NUMBERS[index]))
+            conditions[group] = conditions.get(group, np.zeros(len(start_s), dtype=bool)) | inside
+    return blocks, conditions
+
+
+def test_every_block_gets_a_row_and_one_without_saccades_is_refused(planted_session):
+    """Block 1 holds the calibration trials and no planted saccade."""
+    from wl_preproc.schema import main_sequence
+
+    session_key, _saccades = planted_session
+    masters = _computed_masters(session_key)
+    assert len(masters) >= 2
+    for master in masters:
+        rows = {row["block_id"]: row for row in (main_sequence.SaccadeMainSequence.Block & _master_key(master)).to_dicts()}
+        assert sorted(rows) == [1, 2, 3]
+        assert {name: rows[1][name] for name in ("gain_status", "n_saccades", "amplitude_min_deg", "gain", "reason")} == {
+            "gain_status": "refused", "n_saccades": 0, "amplitude_min_deg": None, "gain": None,
+            "reason": "0 saccades; a gain needs at least 30"}
+
+
+def test_each_gain_is_taken_over_the_saccades_its_block_or_condition_holds(planted_session):
+    from wl_preproc.eye.detect.main_sequence import gain
+    from wl_preproc.schema import main_sequence
+
+    session_key, _saccades = planted_session
+    for master in _computed_masters(session_key):
+        start_s, amplitude, peak = _selected_with_starts(session_key, master)
+        curve = Curve(master["v_max_deg_s"], master["saturation_deg"])
+        blocks, conditions = _planted_groups(start_s)
+        stored_blocks = {row["block_id"]: row
+                         for row in (main_sequence.SaccadeMainSequence.Block & _master_key(master)).to_dicts()}
+        stored_conditions = {(row["block_id"], row["condition"]): row
+                             for row in (main_sequence.SaccadeMainSequence.Condition & _master_key(master)).to_dicts()}
+        assert set(stored_conditions) == set(conditions)
+        for stored, planted in ((stored_blocks, blocks), (stored_conditions, conditions)):
+            for group, inside in planted.items():
+                expected = gain(amplitude[inside], peak[inside], curve, DEFAULT_MAIN_SEQUENCE_PARAMS)
+                row = stored[group]
+                assert (row["n_saccades"], row["reason"]) == (expected.n_saccades, expected.reason), group
+                assert row["gain"] == pytest.approx(expected.gain), group
+
+
+def test_a_faster_condition_shows_in_its_gain(planted_session):
+    """Block 2's `contrast-50` saccades were planted 10% faster than its
+    `contrast-10` ones: measured, 1.095 times. Block 3's four conditions hold
+    about 20 saccades each, too few for a gain."""
+    from wl_preproc.schema import main_sequence
+
+    session_key, _saccades = planted_session
+    names = _detector_names()
+    for master in _computed_masters(session_key):
+        if names[master["paramset_idx"]] != "engbert_kliegl" or master["trace"] == "conjunction":
+            continue
+        rows = (main_sequence.SaccadeMainSequence.Condition & _master_key(master)).to_dicts()
+        second = {row["condition"]: row["gain"] for row in rows if row["block_id"] == 2}
+        assert sorted(second) == ["contrast-10", "contrast-50"]
+        assert second["contrast-50"] / second["contrast-10"] == pytest.approx(FASTER, abs=0.03)
+        third = [row for row in rows if row["block_id"] == 3]
+        assert len(third) == 4
+        assert all(row["gain_status"] == "refused" and row["n_saccades"] < 30 for row in third)
+
+
+def test_a_refused_session_fit_has_no_block_or_condition_rows(refused_session):
+    from wl_preproc.schema import main_sequence
+
+    assert len(main_sequence.SaccadeMainSequence & refused_session & 'fit_status = "refused"') == 21
+    assert len(main_sequence.SaccadeMainSequence.Block & refused_session) == 0
+    assert len(main_sequence.SaccadeMainSequence.Condition & refused_session) == 0
+
+
+def test_placement_reads_times_as_doubles_and_names_by_record_then_stream_code(daemon_module, tmp_path):
+    """`_groups` on planted rows, an hour into a session (Review Focus 2, 3
+    and 5). `block_start_time` and `trial_start_time` are MySQL `FLOAT`s,
+    which read back to six significant digits: 3700.0012 as 3700.00, which
+    would put a saccade at 3700.0011 in block 2. Trial 1 is named by the rig
+    record; trial 2 by the stream's `CONDITION` number alone; trial 3's name
+    is longer than the column, so it has no condition."""
+    import json
+
+    from wl_preproc.schema import main_sequence, pipeline
+
+    session = new_animal().session()
+    key = session.key
+    pipeline.lab.Lab.insert1({"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
+                             skip_duplicates=True)
+    pipeline.subject.Subject.insert1({"subject": session.subject, "sex": "M", "subject_description": "",
+                                      "subject_birth_date": datetime.date(2020, 1, 1)})
+    pipeline.Session.insert1(key)
+    pipeline.event.BehaviorRecording.insert1(key)
+    pipeline.trial.Block.insert([{**key, "block_id": 1, "block_start_time": 3600.0, "block_stop_time": 3700.0012},
+                                 {**key, "block_id": 2, "block_start_time": 3700.0012, "block_stop_time": 3800.0}],
+                                allow_direct_insert=True)
+    pipeline.trial.Trial.insert([{**key, "trial_id": 1, "trial_start_time": 3600.0, "trial_stop_time": 3650.0},
+                                 {**key, "trial_id": 2, "trial_start_time": 3650.0, "trial_stop_time": 3700.0012},
+                                 {**key, "trial_id": 3, "trial_start_time": 3700.0012, "trial_stop_time": 3800.0}],
+                                allow_direct_insert=True)
+    pipeline.trial.BlockTrial.insert([{**key, "block_id": 1, "trial_id": 1}, {**key, "block_id": 1, "trial_id": 2},
+                                      {**key, "block_id": 2, "trial_id": 3}], allow_direct_insert=True)
+    pipeline.event.EventType.insert1({"event_type": "CONDITION", "event_type_description": ""}, skip_duplicates=True)
+    event = {**key, "event_type": "CONDITION", "event_start_time": 3660.0}
+    pipeline.event.Event.insert1(event, allow_direct_insert=True)
+    pipeline.event.Event.Attribute.insert1({**event, "attribute_name": "condition", "attribute_value": "7"})
+    (tmp_path / "xcon").mkdir()
+    (tmp_path / "xcon" / "trials.jsonl").write_text("\n".join(json.dumps(
+        {"index": number - 1, "trial_number": number, "subject": session.subject, "outcome": "correct",
+         "block": "block", "condition": condition, "params": {}})
+        for number, condition in ((1, "rig-name"), (3, "x" * 256))) + "\n", encoding="utf-8")
+
+    start_s = np.array([3610.0, 3655.0, 3700.0011, 3700.0013, 3750.0])
+    blocks, conditions = main_sequence._groups(key, tmp_path, start_s)
+    assert [(block_id, inside.tolist()) for block_id, inside in blocks] == [
+        (1, [True, True, True, False, False]), (2, [False, False, False, True, True])]
+    assert [(group, inside.tolist()) for group, inside in conditions] == [
+        ((1, "7"), [False, True, True, False, False]), ((1, "rig-name"), [True, False, False, False, False])]
+
+
 _MAIN_SEQUENCE_PROBE = """
 import json
 import os
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_main_sequence_populate.py -q --tb=line -p no:cacheprovider`
Expected: 4 failed, 8 passed. The four are the block, gain, faster-condition and placement tests (the last on `_groups` not existing). `test_a_refused_session_fit_has_no_block_or_condition_rows` already passes, since Task 2 writes no part rows; it pins that this task's code keeps it so (mutation T3f).

- [ ] **Step 3: Implement.** Apply this diff:

```diff
--- a/wl_preproc/schema/main_sequence.py
+++ b/wl_preproc/schema/main_sequence.py
@@ -19,8 +19,10 @@ import numpy as np
 
 from wl_preproc.eye.detect.main_sequence import (
     DEFAULT_MAIN_SEQUENCE_PARAMS,
+    Gain,
     MainSequenceParams,
     fit_session,
+    gain,
     selected,
 )
 from wl_preproc.schema import DEFAULT_PREFIX, detect, paramset, pipeline
@@ -118,10 +120,12 @@ class SaccadeMainSequence(dj.Computed):
         return detect.EyeDetection.proj() * fits
 
     def make(self, key: dict) -> None:
-        """One trace's fit, or its refusal. A trace whose detection was
-        refused gets a refused row quoting it."""
+        """One trace's fit, or its refusal, and with a fit each block's and
+        condition's gain. A trace whose detection was refused gets a refused
+        row quoting it."""
         from wl_preproc.eye.ohdpi import read_ohdpi
         from wl_preproc.schema import core, ingest
+        from wl_preproc.schema import eye as eye_schema
 
         params = MainSequenceParams(**(paramset.ParamSet & {
             "paramset_type": key["fit_paramset_type"], "paramset_idx": key["fit_paramset_idx"],
@@ -136,7 +140,8 @@ class SaccadeMainSequence(dj.Computed):
         session_key = {name: key[name] for name in pipeline.Session.primary_key}
         session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
         segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
-        fs_hz = read_ohdpi(session_dir / "ohdpi" / segment["file_path"]).fs_hz
+        recording = read_ohdpi(session_dir / "ohdpi" / segment["file_path"])
+        fs_hz = recording.fs_hz
         runs = selected_runs((detect.EyeDetection.Run & detection_key & 'label in ("saccade", "microsaccade")')
                              .to_dicts(order_by="run_index"), fs_hz, params)
         fit = fit_session([run["amplitude_deg"] for run in runs], [run["peak_velocity_deg_s"] for run in runs],
@@ -151,6 +156,72 @@ class SaccadeMainSequence(dj.Computed):
             "v_max_se_deg_s": fit.v_max_se_deg_s, "saturation_se_deg": fit.saturation_se_deg,
             "r_squared": fit.r_squared, "reason": fit.reason,
         })
+        if curve is None:
+            return
+
+        # Each saccade is placed by where it starts, in session time.
+        times = eye_schema.row_session_times(segment, recording.frame_numbers - recording.frame_numbers[0])
+        start_s = times[[run["run_start"] for run in runs]]
+        amplitude = np.array([run["amplitude_deg"] for run in runs], dtype=float)
+        peak = np.array([run["peak_velocity_deg_s"] for run in runs], dtype=float)
+        blocks, conditions = _groups(session_key, session_dir, start_s)
+        self.Block.insert(
+            _gain_row({**key, "block_id": block_id}, gain(amplitude[inside], peak[inside], curve, params))
+            for block_id, inside in blocks)
+        self.Condition.insert(
+            _gain_row({**key, "block_id": block_id, "condition": condition},
+                      gain(amplitude[inside], peak[inside], curve, params))
+            for (block_id, condition), inside in conditions)
+
+
+def _groups(session_key: dict, session_dir: Path, start_s: np.ndarray):
+    """Which of the saccades starting at `start_s` each block holds, and each
+    condition within a block (spec section 4.3): `[(block_id, mask)]` and
+    `[((block_id, condition), mask)]`.
+
+    A block holds what starts in `[block_start_time, block_stop_time)`; a
+    condition, what starts in a trial of that block (`BlockTrial`) run under
+    it. A trial's condition is resolved as the NWB export resolves it: the rig
+    record's name, else the stream's `CONDITION` number as text
+    (`nwb/conditions.py`). A trial with neither, or with a name longer than
+    the column, has none. Every condition a block's trials ran under gets a
+    group, so one whose trials hold no saccade is refused rather than absent."""
+    from wl_preproc.events.rigtrials import read_rig_trials
+    from wl_preproc.events.runs import stored_doubles
+    from wl_preproc.nwb.conditions import join, stream_codes, trial_columns
+
+    blocks = [
+        (row["block_id"], (start_s >= row["block_start_time"]) & (start_s < row["block_stop_time"]))
+        for row in sorted(stored_doubles(pipeline.trial.Block & session_key, "block_start_time", "block_stop_time"),
+                          key=lambda row: row["block_id"])
+    ]
+    block_of = {row["trial_id"]: row["block_id"] for row in (pipeline.trial.BlockTrial & session_key).to_dicts()}
+    trials = [
+        {"trial_id": row["trial_id"], "start_s": row["trial_start_time"], "stop_s": row["trial_stop_time"]}
+        for row in sorted(stored_doubles(pipeline.trial.Trial & session_key, "trial_start_time", "trial_stop_time"),
+                          key=lambda row: row["trial_start_time"])
+    ]
+    events = [
+        {"time_s": row["event_start_time"], "event_type": "CONDITION", "condition": row["attribute_value"]}
+        for row in stored_doubles(pipeline.event.Event.Attribute & session_key
+                                  & {"event_type": "CONDITION", "attribute_name": "condition"}, "event_start_time")
+    ]
+    matched, _notes = join(trials, read_rig_trials(session_dir, session_key["subject"]))
+    names, _settings = trial_columns(trials, matched, stream_codes(trials, events))
+    conditions: dict = {}
+    for trial, name in zip(trials, names, strict=True):
+        if not name or len(name) > _VARCHAR_LEN or trial["trial_id"] not in block_of:
+            continue
+        group = (block_of[trial["trial_id"]], name)
+        inside = (start_s >= trial["start_s"]) & (start_s < trial["stop_s"])
+        conditions[group] = conditions.get(group, np.zeros(len(start_s), dtype=bool)) | inside
+    return blocks, sorted(conditions.items())
+
+
+def _gain_row(key: dict, result: Gain) -> dict:
+    return {**key, "gain_status": "refused" if result.gain is None else "computed",
+            "n_saccades": result.n_saccades, "amplitude_min_deg": result.amplitude_min_deg,
+            "amplitude_max_deg": result.amplitude_max_deg, "gain": result.gain, "reason": result.reason}
 
 
 def selected_runs(runs: list[dict], fs_hz: float, params: MainSequenceParams) -> list[dict]:
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 12 passed.

- [ ] **Step 5: Mutation checks.** Each was measured, against the final tree, to fail the test in brackets.
  - T3a: placed by row rather than frame, `row_session_times(segment, np.arange(len(recording.frame_numbers)))` [`test_each_gain_is_taken_over_the_saccades_its_block_or_condition_holds`].
  - T3b: placed by where a saccade ends, `times[[run["run_stop"] - 1 for run in runs]]` [same].
  - T3c: block times read as text, `(pipeline.trial.Block & session_key).to_dicts()` in place of `stored_doubles(...)` [`test_placement_reads_times_as_doubles_and_names_by_record_then_stream_code`].
  - T3d: the stream's number ignored, `trial_columns(trials, matched, {})` [same].
  - T3e: the name-length check dropped [same].
  - T3f: `if curve is None: return` removed, so a refused fit's gains are taken [`test_a_refused_session_fit_has_no_block_or_condition_rows`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/main_sequence.py tests/schema/test_main_sequence_populate.py
git commit -m "feat(schema): each block's and condition's gain against its session's fit -- saccades placed by where they start in session time, through the recording's frame numbers; block and trial times read as the doubles MySQL stores; a trial's condition resolved as the NWB export resolves it (the rig record's name, else the stream's CONDITION number); every block and every condition a block ran under given a row, refused below 30 saccades

<trailer lines>"
```

---

### Task 4: Vigor in the daily report

**Files:**
- Modify: `wl_preproc/cli/report.py`
- Create: `tests/cli/test_vigor_report.py`

**Interfaces — consumes:** Task 1's `Curve`, `MainSequenceParams`, `SessionFit`, `vigor`; Task 2's table and `selected_runs`. From the codebase: `tests/cli/test_detect_report.py`'s `_detection_row`, `_land_session`, `_line_for`, `_section`, `_subsection`.

**Interfaces — produces:** `report._vigor_lines(ingested_keys: set, prefix) -> list[str]`, and in `build_report` the subsection `### Saccade vigor per session per eye (24 h) — N`, after the detector agreement and before `## Not yet reported`. Each line: ``- `<subject>` @ <YYYY-mm-dd HH:MM> — <left|right> (validity paramset V, fit paramset F): `<detector>` <figure>, ...``, the figure `94% (12 earlier)` or one of `no history yet (N earlier)`, `too few saccades (N)`, `detection refused`, `not computed yet`.

- [ ] **Step 1: Write the failing tests.** Apply this diff:

```diff
--- /dev/null
+++ b/tests/cli/test_vigor_report.py
@@ -0,0 +1,141 @@
+"""The report's `### Saccade vigor per session per eye (24 h)` subsection
+(main-sequence design spec `2026-10-07-main-sequence-design.md` section 5).
+
+Rows are planted directly, as `test_detect_report.py` plants its own;
+`tests/schema/test_main_sequence_populate.py` drives the table end to end.
+Each test plants its own animal, so no line is another test's. Earlier
+sessions are ingested long ago, outside the window, so only the session under
+test gets a line of its own.
+"""
+
+from __future__ import annotations
+
+import datetime
+from types import SimpleNamespace
+
+import numpy as np
+import pytest
+
+from tests.cli.test_detect_report import _detection_row, _land_session, _line_for, _section, _subsection
+from tests.identities import new_animal
+from wl_preproc.cli.report import build_report
+from wl_preproc.eye.detect.main_sequence import Curve
+
+CURVE = Curve(v_max_deg_s=400.0, saturation_deg=5.0)
+_LONG_AGO = datetime.datetime(2025, 1, 1)
+_HEADING = "Saccade vigor per session per eye (24 h)"
+
+
+@pytest.fixture(scope="module")
+def vigor_schema(dj_conn, prefix):
+    from dataclasses import asdict
+
+    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS
+    from wl_preproc.schema import detect, ingest, main_sequence, paramset, timebase
+
+    main_sequence.activate(prefix=prefix)
+    ingest.activate(prefix=prefix)
+    timebase.activate(prefix=prefix)
+    return SimpleNamespace(
+        detection=detect.register_default_paramsets()["engbert_kliegl"],
+        validity=paramset.register("eye_validity", asdict(DEFAULT_VALIDITY_PARAMS)),
+        fit=main_sequence.register_default_paramsets()["default"],
+    )
+
+
+def _detect(schema, session, trace, *, status="computed", saccades=(), master=True):
+    """One Engbert-Kliegl detection of `trace`, its saccade runs
+    (`(amplitude, peak)` pairs, 40 ms each at 500 Hz) and, with `master`, its
+    `SaccadeMainSequence` row: computed on `CURVE` over 1-12 deg."""
+    from wl_preproc.schema import detect, main_sequence
+
+    row = _detection_row(session.subject, session.session_datetime, trace, schema.validity, schema.detection,
+                         status=status, n_samples=100 * (len(saccades) + 1), reason="planted refusal"
+                         if status == "refused" else "")
+    detect.EyeDetection.insert1(row, allow_direct_insert=True)
+    key = {name: row[name] for name in detect.EyeDetection.primary_key}
+    detect.EyeDetection.Run.insert(
+        {**key, "run_index": index, "run_start": 100 * index, "run_stop": 100 * index + 20, "label": "saccade",
+         "amplitude_deg": amplitude, "peak_velocity_deg_s": peak}
+        for index, (amplitude, peak) in enumerate(saccades))
+    if master and status == "computed":
+        main_sequence.SaccadeMainSequence.insert1(
+            {**key, "fit_paramset_type": "main_sequence", "fit_paramset_idx": schema.fit, "fit_status": "computed",
+             "fs_hz": 500.0, "n_saccades": len(saccades), "amplitude_min_deg": 1.0, "amplitude_max_deg": 12.0,
+             "v_max_deg_s": CURVE.v_max_deg_s, "saturation_deg": CURVE.saturation_deg, "v_max_se_deg_s": 1.0,
+             "saturation_se_deg": 0.1, "r_squared": 0.9, "reason": ""}, allow_direct_insert=True)
+    elif master:
+        main_sequence.SaccadeMainSequence.insert1(
+            {**key, "fit_paramset_type": "main_sequence", "fit_paramset_idx": schema.fit, "fit_status": "refused",
+             "n_saccades": 0, "reason": "detection refused: planted refusal"}, allow_direct_insert=True)
+    return key
+
+
+def _animal_with_history(schema, n_earlier: int):
+    """An animal with `n_earlier` sessions ingested long ago, each fitted on
+    `CURVE` for both eyes, and a session ingested now. Returns the animal and
+    that session, landed but not yet detected."""
+    animal = new_animal()
+    for _ in range(n_earlier):
+        earlier = animal.session()
+        _land_session(earlier.subject, earlier.session_datetime, ingested_at=_LONG_AGO)
+        for trace in ("left", "right"):
+            _detect(schema, earlier, trace)
+    session = animal.session()
+    _land_session(session.subject, session.session_datetime)
+    return session
+
+
+def _saccades(n: int, scale: float):
+    amplitude = np.linspace(2.0, 10.0, n)
+    return list(zip(amplitude.tolist(), (scale * CURVE(amplitude)).tolist(), strict=True))
+
+
+def _line(root, prefix, session, trace) -> str:
+    subsection = _subsection(_section(build_report(root, prefix=prefix), "Detection"), _HEADING)
+    return _line_for(subsection, f"`{session.subject}` @ {session.session_datetime:%Y-%m-%d %H:%M} — {trace} ")
+
+
+def test_a_session_gets_each_detectors_vigor_against_its_earlier_sessions(vigor_schema, tmp_path, prefix):
+    session = _animal_with_history(vigor_schema, 3)
+    _detect(vigor_schema, session, "left", saccades=_saccades(40, 0.9))
+    line = _line(tmp_path, prefix, session, "left")
+    assert line.endswith(f"(validity paramset {vigor_schema.validity}, fit paramset {vigor_schema.fit}): "
+                         "`engbert_kliegl` 90% (3 earlier)")
+
+
+def test_fewer_than_three_earlier_sessions_is_no_history_yet(vigor_schema, tmp_path, prefix):
+    session = _animal_with_history(vigor_schema, 2)
+    _detect(vigor_schema, session, "left", saccades=_saccades(40, 0.9))
+    assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` no history yet (2 earlier)")
+
+
+def test_too_few_saccades_say_how_many(vigor_schema, tmp_path, prefix):
+    session = _animal_with_history(vigor_schema, 3)
+    _detect(vigor_schema, session, "left", saccades=_saccades(10, 0.9))
+    assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` too few saccades (10)")
+
+
+def test_a_refused_detection_says_so(vigor_schema, tmp_path, prefix):
+    session = _animal_with_history(vigor_schema, 3)
+    _detect(vigor_schema, session, "right", status="refused")
+    assert _line(tmp_path, prefix, session, "right").endswith("`engbert_kliegl` detection refused")
+
+
+def test_a_detection_without_its_row_is_not_computed_yet(vigor_schema, tmp_path, prefix):
+    from wl_preproc.schema import detect
+
+    session = _animal_with_history(vigor_schema, 3)
+    key = _detect(vigor_schema, session, "left", saccades=_saccades(40, 0.9), master=False)
+    try:
+        assert _line(tmp_path, prefix, session, "left").endswith("`engbert_kliegl` not computed yet")
+    finally:
+        # A later `daemon.run_once()` in this suite would try to fit it.
+        (detect.EyeDetection & key).delete()
+
+
+def test_the_both_eyes_trace_is_left_out(vigor_schema, tmp_path, prefix):
+    session = _animal_with_history(vigor_schema, 3)
+    _detect(vigor_schema, session, "conjunction", saccades=_saccades(40, 0.9))
+    subsection = _subsection(_section(build_report(tmp_path, prefix=prefix), "Detection"), _HEADING)
+    assert f"`{session.subject}` @" not in subsection
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/cli/test_vigor_report.py -q --tb=line -p no:cacheprovider`
Expected: 6 failed: no subsection headed `Saccade vigor per session per eye (24 h)`.

- [ ] **Step 3: Implement.** Apply this diff:

```diff
--- a/wl_preproc/cli/report.py
+++ b/wl_preproc/cli/report.py
@@ -1022,6 +1022,72 @@ def _agreement_line(row: dict, detector_names: dict[int, str]) -> str:
     )
 
 
+
+def _vigor_lines(ingested_keys: set, prefix: str = DEFAULT_PREFIX) -> list[str]:
+    """`### Saccade vigor per session per eye (24 h)`'s lines (main-sequence
+    design spec `2026-10-07-main-sequence-design.md` section 5): for each
+    session in `ingested_keys`, each eye and each validity and fit paramset,
+    every detector's vigor against the same animal's earlier sessions.
+
+    **Computed here, never stored**: the history grows with every session.
+    Not in `gather_readings`, for `_detection_rows`' reason.
+
+    A detector's figure is `main_sequence.vigor` over this session's saccades,
+    picked as its fit picked them (`schema/main_sequence.py::selected_runs`,
+    with the row's own `fs_hz`), against the earlier sessions' computed fits.
+    In its place: "detection refused", "not computed yet" where no row exists,
+    or `vigor`'s own reason. The both-eyes trace is left out (the requester's
+    decision of 2026-10-07)."""
+    from wl_preproc.eye.detect.main_sequence import Curve, MainSequenceParams, SessionFit, vigor
+    from wl_preproc.schema import detect as detect_schema
+    from wl_preproc.schema import main_sequence
+    from wl_preproc.schema import paramset as paramset_schema
+
+    main_sequence.activate(prefix=prefix)
+    table = main_sequence.SaccadeMainSequence
+    names = {row["paramset_idx"]: row["params"]["detector"]
+             for row in (paramset_schema.ParamSet & {"paramset_type": "eye_detection"}).to_dicts()}
+    fit_params = {row["paramset_idx"]: MainSequenceParams(**row["params"])
+                  for row in (paramset_schema.ParamSet & {"paramset_type": "main_sequence"}).to_dicts()}
+
+    def figure(detection: dict, fit_idx: int) -> str:
+        if detection["status"] == "refused":
+            return "detection refused"
+        key = {**{name: detection[name] for name in detect_schema.EyeDetection.primary_key},
+               "fit_paramset_type": "main_sequence", "fit_paramset_idx": fit_idx}
+        rows = (table & key).to_dicts()
+        if not rows:
+            return "not computed yet"
+        params = fit_params[fit_idx]
+        runs = main_sequence.selected_runs(
+            (detect_schema.EyeDetection.Run & {name: detection[name] for name in detect_schema.EyeDetection.primary_key}
+             & 'label in ("saccade", "microsaccade")').to_dicts(order_by="run_index"), rows[0]["fs_hz"], params)
+        earlier = (table & {name: value for name, value in key.items() if name != "session_datetime"}
+                   & f"session_datetime < '{detection['session_datetime']:%Y-%m-%d %H:%M:%S}'"
+                   & 'fit_status = "computed"').to_dicts()
+        history = [SessionFit(Curve(row["v_max_deg_s"], row["saturation_deg"]), row["n_saccades"],
+                              row["amplitude_min_deg"], row["amplitude_max_deg"], row["v_max_se_deg_s"],
+                              row["saturation_se_deg"], row["r_squared"], "") for row in earlier]
+        result = vigor([run["amplitude_deg"] for run in runs], [run["peak_velocity_deg_s"] for run in runs],
+                       history, params)
+        return result.reason if result.value is None else f"{result.value:.0%} ({result.n_history} earlier)"
+
+    lines = []
+    for subject, session_datetime in sorted(ingested_keys):
+        detections = (detect_schema.EyeDetection & {"subject": subject, "session_datetime": session_datetime}
+                      & 'trace in ("left", "right")').to_dicts()
+        groups: dict = {}
+        for detection in detections:
+            for fit_idx in sorted(fit_params):
+                groups.setdefault((detection["trace"], detection["validity_paramset_idx"], fit_idx), []).append(
+                    detection)
+        for (trace, validity_idx, fit_idx), members in sorted(groups.items()):
+            figures = ", ".join(f"`{names[detection['paramset_idx']]}` {figure(detection, fit_idx)}"
+                                for detection in sorted(members, key=lambda row: row["paramset_idx"]))
+            lines.append(f"- `{subject}` @ {session_datetime:%Y-%m-%d %H:%M} — {trace} (validity paramset "
+                         f"{validity_idx}, fit paramset {fit_idx}): {figures}")
+    return lines
+
 @dataclasses.dataclass(frozen=True, slots=True)
 class Readings:
     """Everything both renderings need, computed once.
@@ -1730,6 +1796,13 @@ def build_report(
         "- none"
     ]
 
+    # Main-sequence design spec section 5: each eye's vigor against the same
+    # animal's earlier sessions, every detector on one line. Windowed to the
+    # 24 h `ingested_keys` the per-session lists above use.
+    vigor_lines = _vigor_lines(ingested_keys, prefix=prefix)
+    lines += ["", f"### Saccade vigor per session per eye (24 h) — {len(vigor_lines)}", ""]
+    lines += vigor_lines or ["- none"]
+
     lines += ["", "## Not yet reported", ""]
     lines += [f"- **{name}** — {why}" for name, why in _NOT_YET_REPORTED]
 
```

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command. Expected: 6 passed.

Then the report's other tests: `.venv/bin/python -m pytest tests/cli -q -p no:cacheprovider`. Expected: 162 passed.

- [ ] **Step 5: Mutation checks.** Each was measured, against the final tree, to fail the test in brackets.
  - T4a: the both-eyes trace shown, the `trace in ("left", "right")` restriction dropped [`test_the_both_eyes_trace_is_left_out`].
  - T4b: the session in its own history, `session_datetime <` becomes `<=` [`test_a_session_gets_each_detectors_vigor_against_its_earlier_sessions`].
  - T4c: a missing row read as `detection refused` [`test_a_detection_without_its_row_is_not_computed_yet`].

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/cli/report.py tests/cli/test_vigor_report.py
git commit -m "feat(report): saccade vigor per session per eye -- each detector's vigor against the same animal's earlier sessions, computed when the report is built from the stored fits and this session's saccades picked as its fit picked them; \"no history yet\", \"too few saccades\", \"detection refused\" and \"not computed yet\" in place of a figure; the both-eyes trace left out

<trailer lines>"
```

---

### Task 5: The records, and the full suite

**Files:**
- Modify: `docs/superpowers/specs/2026-10-07-main-sequence-design.md`, `docs/CHECKPOINT.md`, `wl.yaml`
- Create: `docs/handoffs/2026-10-07-main-sequence.md`

- [ ] **Step 1: The spec's amendments.** Apply this diff:

```diff
--- a/docs/superpowers/specs/2026-10-07-main-sequence-design.md
+++ b/docs/superpowers/specs/2026-10-07-main-sequence-design.md
@@ -170,6 +170,8 @@ One row for every condition that ran in a block, when the session's own fit was
 One `make()` writes all three traces' rows: the gaze file is read once per session, detector and
 fit paramset, not once per trace.
 
+*Superseded by amendment 2: one key, and one `make()`, per trace.*
+
 No events requirement is needed in `key_source`. An `EyeDetection` row needs an `EyeValidity`
 row, which needs `EyeCalibration` to have run, and `EyeCalibration.key_source` requires
 `pipeline.event.BehaviorRecording`. So a session's blocks and trials are always assembled before
@@ -210,6 +212,8 @@ data. Its standard errors come from the fit's covariance.
 
 Count first, then range, the order the parent's §6.5.2 set to mirror `eye/calibration.py`'s guard.
 
+*Item 3 is superseded by amendment 1, which refuses a fit whose parameters are not known.*
+
 ### 4.3 Blocks and conditions
 
 - **A saccade belongs to a block** if it starts (its `run_start` in session time) inside that
@@ -244,6 +248,8 @@ Its dataclass, `MainSequenceParams`, carries the defaults:
 
 A changed default is a new paramset and new rows, never a rewrite of old ones.
 
+*Amendment 1 adds a field, `max_relative_se`, 0.5.*
+
 ## 5. Vigor in the report
 
 A new subsection of `## Detection` in `cli/report.py::build_report`: **"Saccade vigor per session
@@ -295,7 +301,8 @@ every wl.works poll (the parent's §9).
 **Database** (`tests/schema/test_main_sequence_populate.py`), on a synthetic session whose gaze
 makes raised-cosine saccades of planted sizes and durations in two blocks, its conditions named by
 the generator's rig record (`synth/peripherals.py::write_rig_trials`):
-- the session fit recovers the planted curve within a tolerance the plan measures;
+- the session fit recovers the planted curve within a tolerance the plan measures (amendment 3
+  records it);
 - every trace gets a row, and a refused detection trace a refused row quoting its reason;
 - every block gets a `.Block` row, and a block with too few saccades a refused one;
 - `.Condition` rows are keyed by the rig record's names;
@@ -334,3 +341,31 @@ Each carries a dated pointer in the parent:
   saccade's size, shown per eye for every detector.
 - **§10:** the planted main sequence and the degenerate-fit fixture are §7's tests; the
   condition-grain gap is closed as §6.5.3's amendment says.
+
+## Amendments, 2026-10-07, made while proving the plan
+
+1. **The session fit's third check refuses a fit whose parameters are not known** (§4.2 item 3,
+   §4.4; plan Task 1). As §4.2 had it, the check was a fit that does not converge or whose
+   covariance is not finite. On input that passes the first two checks it never fires: SciPy
+   converges, with a finite covariance, even for peak speed in proportion to size with no
+   saturation in range, where V_max comes to 9,352 ± 8,466 °/s. The check is now:
+   - peak speeds that do not vary, since r² would be minus infinity, which no column can hold;
+   - a fit that does not converge;
+   - V_max's or C's standard error at least `max_relative_se` of its value, a new paramset field,
+     0.5 by default.
+
+   Measured on the reference recording, whole sessions come to 3–5%, and nine in ten random
+   100-saccade samples of them under 28%: one in 2,800 reached 50%. The case with no saturation
+   comes to 90%.
+2. **One key per trace** (§3.4; plan Task 2). DataJoint 2.3 keys a table's job queue on every
+   primary-key attribute inherited through a foreign key, and `trace` comes into this table through
+   `-> detect.EyeDetection`. With `trace` missing from `key_source`, as §3.4 had it, the daemon's
+   pass wrote the left trace alone. Each trace's `make()` now reads the recording's sync line:
+   three reads per detection rather than one.
+3. **The planted session's tolerance** (§7; plan Task 2). Each eye's fit, for every detector that
+   computes one, must lie within 12% of the fit to the planted saccades themselves over 2–8°.
+   Measured: 1.4–9.2%, the most NSLR's. The detectors clip each raised cosine's slow tails, so
+   amplitudes come out a little short, and the session's own calibration is 4% under the
+   generator's scale. Nyström–Holmqvist's adaptive threshold settles near 200 °/s on the planted
+   session's slow saccades and keeps only those over 7°, so its fits there are refused for too
+   few saccades. That is the detector's behaviour, not this table's.
```

- [ ] **Step 2: The handoff, the checkpoint and `wl.yaml`.** Apply these diffs, dated the day the plan is executed:

```diff
--- a/docs/CHECKPOINT.md
+++ b/docs/CHECKPOINT.md
@@ -168,12 +168,18 @@ requester chose to merge the same day; true when written.*
 >   `request.ActivationProbeRun`). It is the bulk of what remains, and outranks
 >   every hardware-free item once the machine exists.
 >
-> **Hardware-free:** the list the requester chose on 2026-10-05 is done. Ask
-> what comes next. Open and small: M4 (left open by his choice), and the
-> U'n'Eye minors branch's own three deferred minors
-> (`handoffs/2026-10-06-uneye.md` §7). The U'n'Eye review's four minors are
-> fixed (`22dbb70`). Fine-tuning U'n'Eye to the DPI tracker waits for
-> hand-labelled lab data.
+> **Hardware-free:** the saccade main sequence and vigor (the saccade spec's
+> §6.5), the requester's choice of 2026-10-07: built on `spec/main-sequence`,
+> not merged (`handoffs/2026-10-07-main-sequence.md`). Each detection trace's
+> session fit, each block's and condition's gain against it, and the report's
+> vigor line, per eye and detector. Next: the whole-branch review, then the
+> merge question. Open and small: M4 (left open by the requester's choice),
+> and the U'n'Eye minors branch's own three deferred minors
+> (`handoffs/2026-10-06-uneye.md` §7). Fine-tuning U'n'Eye to the DPI tracker
+> waits for hand-labelled lab data.
+>
+> *Until 2026-10-07 this said the requester's list of 2026-10-05 was done, and
+> to ask what comes next; true when written.*
 >
 > *Until 2026-10-07 this named U'n'Eye as item 1, built on `spec/uneye` and
 > not merged; true when written.*
--- /dev/null
+++ b/docs/handoffs/2026-10-07-main-sequence.md
@@ -0,0 +1,82 @@
+# Saccade main sequence and vigor
+
+**Branch:** `spec/main-sequence`, forked from `main` at `a699dd6`.
+- **Design:** `docs/superpowers/specs/2026-10-07-main-sequence-design.md`, an addendum to the
+  saccade-detection spec's §6.5 and §9, with amendments 1–3.
+- **Plan:** `docs/superpowers/plans/2026-10-07-main-sequence.md`.
+- **The requester's choices,** on 2026-10-07:
+  - this work next, from what was open;
+  - vigor worked out per saccade, the session's figure their median;
+  - saccades of 1° and up in the fit;
+  - each eye on the report's line, with every detector's figure;
+  - a gain against the session's curve for blocks and conditions, rather than fits of their own;
+  - the design, then the written spec.
+
+## 1. What was built
+
+- **`eye/detect/main_sequence.py`,** pure functions:
+  - `selected`: which runs a fit takes, by size (1° and up) and duration (150 ms at most), not by
+    label;
+  - `fit_session`: the saturating curve through a session's saccades, refused for fewer than 100,
+    for a middle 80% of sizes spanning less than a factor of 3, or for a V_max or C known to no
+    better than half (amendment 1);
+  - `gain`: a group's median ratio of peak speed to the session's curve, from 30 saccades;
+  - `vigor`: a session's median ratio against the earlier sessions' curves that cover each
+    saccade's size, from 3 earlier sessions and 30 saccades.
+- **`schema/main_sequence.py`:** `SaccadeMainSequence`, one row per detection trace and fit
+  paramset (amendment 2), with `.Block` and `.Condition` gains.
+  - A refused detection gets a refused row quoting it.
+  - Saccades are placed by where they start, in session time through the recording's frame
+    numbers. Block and trial times are read as the doubles MySQL stores.
+  - A trial's condition is the rig record's name, else the stream's `CONDITION` number, as the NWB
+    export resolves it.
+- **The daemon** runs it after `DetectorAgreement` and registers the default `main_sequence`
+  paramset.
+- **The report** has a new subsection, "Saccade vigor per session per eye (24 h)": one line per
+  session per eye, with each detector's figure or the reason there is none.
+
+## 2. What it measured
+
+- **On the reference recording** (spec §1): each detector's sizes and durations, what the 1°
+  floor changes, how far the detectors disagree about the curve, and why a block cannot hold a fit
+  of its own.
+- **On the planted session** (Task 2's fixture):
+  - every detector that computes a fit lies within 1.4–9.2% of the planted curve over 2–8°
+    (amendment 3);
+  - a condition planted 10% faster comes out at 1.095 times its neighbour;
+  - Nyström–Holmqvist keeps only the saccades over 7° there, so its fits are refused for too few.
+
+## 3. Tests
+
+- `tests/eye/detect/test_main_sequence.py` (18): the defaults, the selection rule, the fit's
+  recovery and each refusal, gains and vigor.
+- `tests/schema/test_main_sequence_populate.py` (12), on a synthetic session of 161 saccades on a
+  planted main sequence, with 60 frames dropped and a trial faulted:
+  - a row for every trace, each stored fit equal to the fit of its own selected runs, the planted
+    curve recovered, and the fit paramset in the key;
+  - a refused detection's row, and too few saccades refused with their count;
+  - every block's and condition's gain, against where the recipe put them, and the faster
+    condition;
+  - placement an hour into a session, by record name and by stream number;
+  - a real `wlpp daemon` pass writing rows with nothing registered beforehand.
+- `tests/cli/test_vigor_report.py` (6): a figure, too little history, too few saccades, a refused
+  detection, a missing row, and the both-eyes trace left out.
+
+## 4. The full suite
+
+Run once, on both interpreters, with every task applied and every reference variable set except
+`WLPP_OHDPI_REFERENCE`:
+- **3.11:** 2270 passed, 26 skipped, 1 deselected, 1 xfailed.
+- **3.13:** 2270 passed, 27 skipped, 1 xfailed.
+
+That is 36 more passed on each than `main`'s 2234 at `22dbb70`: this branch's new tests.
+
+## 5. What is next
+
+- **The whole-branch review, then the merge question.** After the merge: CI on both
+  interpreters, and the pointer commit (the checkpoint's header and `wl.yaml`'s `describes`).
+- **Not in this round** (spec §8): the fits in the NWB file, the stream's `CONDITION` number end
+  to end (wl-xcon's XC-197), other fit forms, and a rolling history.
+- **The numbers mean degrees only once sessions are calibrated.** The reference recording's
+  degrees are a guessed scale. The defaults rest on its sizes and should be checked against the
+  lab's first calibrated sessions.
--- a/wl.yaml
+++ b/wl.yaml
@@ -32,8 +32,10 @@ status:
     rig: record a few minutes with the sync box live, since the barcode and
     timebase alignment path has never met real data and cannot with the
     recording the lab has. With the compute machine: Phase 2b and the NWB's
-    units come before everything below. Hardware-free: the requester's list
-    of 2026-10-05 is done; ask what comes next. Open and small: M4, and the
+    units come before everything below. Hardware-free: the saccade main
+    sequence and vigor, the requester's choice of 2026-10-07, built on
+    spec/main-sequence and not merged (docs/handoffs/2026-10-07-main-sequence.md);
+    its whole-branch review, then the merge question. Open and small: M4, and the
     U'n'Eye minors branch's three deferred minors
     (docs/handoffs/2026-10-06-uneye.md section 7). The U'n'Eye review's four
     minors are fixed (22dbb70). Fine-tuning U'n'Eye waits for hand-labelled
```

- [ ] **Step 3: Check the manifest.** Run `.venv/bin/wl-check`. Expected: `wl.yaml: no findings`.

- [ ] **Step 4: The full suite, on both interpreters,** with every reference variable set except `WLPP_OHDPI_REFERENCE`:

```bash
R=$HOME/.cache/wl-preproc-references
export WLPP_NSLR_REFERENCE=$R WLPP_BMD_REFERENCE=$R/bmd/BMD WLPP_BMD_BOOST_INCLUDE=$R/bmd/boost_1_86_0 WLPP_ANDERSSON_DATA=$R/andersson
unset WLPP_OHDPI_REFERENCE
.venv/bin/python -m pytest -q -p no:cacheprovider -o faulthandler_timeout=1800
~/.cache/wl-preproc-venv313/bin/python -m pytest -q -p no:cacheprovider -o faulthandler_timeout=1800
```

A test still running after 30 minutes prints every thread's stack to the log, so a hang shows itself.

Expected: 3.11, 2270 passed, 26 skipped, 1 deselected, 1 xfailed; 3.13, 2270 passed, 27 skipped, 1 xfailed. That is 36 more passed on each than `main`'s 2234 at `22dbb70`: this plan's new tests, 18 + 12 + 6.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-10-07-main-sequence-design.md docs/handoffs/2026-10-07-main-sequence.md docs/CHECKPOINT.md wl.yaml
git commit -m "docs: the saccade main sequence and vigor -- the spec's amendments 1-3, the handoff, and the checkpoint's and wl.yaml's lines

<trailer lines>"
```
