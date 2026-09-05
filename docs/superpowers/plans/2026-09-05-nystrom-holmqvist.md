# Nyström–Holmqvist Detector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Nyström–Holmqvist detector — the third registered detector, the first to emit `pso` and `fixation`, and the one whose rows finally make the eye kind-disagreement rate measurable.

**Architecture:** A new `eye/detect/nystrom_holmqvist.py` built from four pure functions — adaptive peak threshold, saccade bounds, glissade search, assembly — behind the existing `DetectFn` contract. That contract gains `fs_hz` (Task 1), because every NH duration is specified in milliseconds and the detector currently has no way to learn the sampling rate. The shared velocity estimator is used deliberately in place of the paper's Savitzky–Golay; see spec §2.

**Tech Stack:** Python 3.11 (floor; CI also runs 3.13), numpy, DataJoint 2.3.2, MySQL 8 via `testcontainers`, pytest. REMoDNaV (MIT, PyPI) as a development-only oracle in Task 7.

**Spec:** `docs/superpowers/specs/2026-09-05-nystrom-holmqvist-design.md`

## Global Constraints

- **Run the suite as `.venv/bin/python -m pytest`, from the repo root.** Not bare `pytest`, not `.venv/bin/pytest` — the macOS `.pth` hidden-flag trap makes those unreliable.
- **Develop against 3.11. CI runs 3.11 AND 3.13.** A green local run is evidence about 3.11 on macOS arm64 and nothing else. Push and read `gh run list` before claiming CI green.
- **Zero warnings.** DataJoint 2.3.2 deprecates bare `fetch()`; use `to_arrays` / `to_dicts` / `fetch1`.
- **Populate tests go through `daemon.run_once()`, never `make()` by hand.**
- **Every constant comes from the paper.** Spec §7 lists each with its Table 2 or page source. **Nothing is tuned against the synthetic generator** — it carries planted ground truth, so tuning fits the fixture rather than the eye.
- **`velocity_deg_s` is a TWO-COLUMN array** (x, y per sample), not a scalar speed. NH's `θ̇` is its Euclidean norm: `np.hypot(v[:, 0], v[:, 1])`.
- **`available` is an object array** of `None` (detector may label) or a `Label` (the validity mask already claimed it).
- **Branch is `spec/nystrom-holmqvist`**, spec already committed at `75ebde4`.
- **Every commit message ends with:**
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01RTPFVKnR7yeEYsC1CYuiaH
  ```

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `wl_preproc/eye/detect/registry.py` | `DetectFn` gains `fs_hz`; NH registry entry | Modify |
| `wl_preproc/eye/detect/engbert_kliegl.py` | accept and ignore `fs_hz` | Modify |
| `wl_preproc/eye/detect/otero_millan.py` | accept and ignore `fs_hz` | Modify |
| `wl_preproc/schema/detect.py` | pass `fs_hz` at the `detector.detect(...)` call site | Modify |
| `wl_preproc/eye/detect/nystrom_holmqvist.py` | the detector: params, threshold, bounds, glissades, assembly | **Create** |
| `tests/eye/detect/test_nystrom_holmqvist.py` | unit tests for the four pure functions | **Create** |
| `tests/eye/detect/test_nystrom_holmqvist_validation.py` | spec §5's published statistics, each with a null first | **Create** |
| `tests/schema/test_detect_populate.py` | NH populates; the conjunction carries `pso` | Modify |
| `docs/CHECKPOINT.md`, `wl.yaml`, `docs/handoffs/` | status | Modify / Create |

One module, not four. `otero_millan.py` is 840 lines and is this repo's precedent for a single-detector file holding its own helpers.

---

### Task 1: `fs_hz` reaches the detector

**Files:**
- Modify: `wl_preproc/eye/detect/registry.py` (`DetectFn.__call__`, `Detector.detect`)
- Modify: `wl_preproc/eye/detect/engbert_kliegl.py::detect_engbert_kliegl`
- Modify: `wl_preproc/eye/detect/otero_millan.py::detect_otero_millan`
- Modify: `wl_preproc/schema/detect.py:522`
- Test: `tests/eye/detect/test_registry.py`

**Interfaces:**
- Produces: `DetectFn.__call__(gaze_deg, velocity_deg_s, available, fs_hz: float, params) -> list[LabelledInterval]`, and `Detector.detect` with the same new parameter. Every later task depends on this signature.

**Why this task exists.** Spec §7 expresses every NH duration in milliseconds — `min_saccade_duration_ms`, `min_fixation_duration_ms` — and states why: "a sample count is wrong at a different `fs_hz`." But `DetectFn` currently ends at `params`, so a detector cannot learn the sampling rate. Three of the four remaining detectors (NH, NSLR, REMoDNaV) specify durations in time, so this is the shared contract's gap, not NH's.

`fs_hz` is a property of the RECORDING, so it is a positional argument and **not** a paramset key. Putting it in the paramset would make an immutable, content-addressed row carry a per-session fact, and a session at a different rate would need a different paramset.

- [ ] **Step 1: Write the failing test**

```python
def test_a_detector_receives_the_sampling_rate():
    """Design spec `2026-09-05-nystrom-holmqvist-design.md` §7 expresses NH's
    durations in MILLISECONDS -- `min_saccade_duration_ms`,
    `min_fixation_duration_ms` -- and says why: a sample count is wrong at a
    different `fs_hz`. Three of the four remaining detectors (NH, NSLR,
    REMoDNaV) specify durations in time, so the sampling rate belongs in the
    shared contract.

    Positional, not a paramset key: `fs_hz` is a property of the RECORDING.
    A paramset is immutable and content-addressed, so a rate stored there
    would make two sessions recorded at different rates need two paramsets
    for one set of parameters."""
    import numpy as np

    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.eye.detect.registry import Detector

    seen = {}

    def _record_fs(gaze_deg, velocity_deg_s, available, fs_hz, params):
        seen["fs_hz"] = fs_hz
        return [Run(start=0, stop=2, label=Label.SACCADE)]

    detector = Detector(
        name="records_fs", vocabulary=frozenset({Label.SACCADE}),
        run=_record_fs, defaults=_NoParams(),
    )
    gaze = np.zeros((4, 2))
    available = np.array([None] * 4, dtype=object)

    detector.detect(gaze, gaze, available, 500.0, _NoParams())

    assert seen["fs_hz"] == 500.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_registry.py -k "receives_the_sampling_rate" -v`
Expected: FAIL — `Detector.detect() takes 5 positional arguments but 6 were given`.

- [ ] **Step 3: Implement**

In `registry.py`, add `fs_hz: float` to `DetectFn.__call__` between `available` and `params`, and to `Detector.detect`, forwarding it to `self.run`. Document it on `DetectFn`:

```python
        # The RECORDING's sampling rate, not a parameter. Positional rather
        # than a paramset key because a paramset is immutable and
        # content-addressed: a rate stored there would make two sessions
        # recorded at different rates need two paramsets for one set of
        # parameters. Detectors that express durations in samples
        # (Engbert-Kliegl) accept and ignore it; those that express them in
        # time (design spec section 3.1's Nystrom-Holmqvist, NSLR and
        # REMoDNaV) need it to convert.
        fs_hz: float,
```

Add `fs_hz: float,` to both existing detector functions between `available` and `params`, each with a one-line comment: `# Accepted and unused: this detector's minimum duration is in SAMPLES.`

At `schema/detect.py:522`, pass it: `detector.detect(gaze, v, offered, fs_hz, detector_params)`. `fs_hz` is already in scope there — `make()` reads it from `read_ohdpi(path).fs_hz`.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS. Every call site is updated; a missed one raises `TypeError`, not a silent wrong answer.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc tests
git commit -m "detect: the sampling rate reaches the detector

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RTPFVKnR7yeEYsC1CYuiaH"
```

---

### Task 2: The adaptive peak threshold

**Files:**
- Create: `wl_preproc/eye/detect/nystrom_holmqvist.py`
- Test: `tests/eye/detect/test_nystrom_holmqvist.py`

**Interfaces:**
- Produces: `NystromHolmqvistParams` (frozen dataclass, spec §7's twelve fields), `DEFAULT_NH_PARAMS`, `PeakThreshold` (frozen: `peak_deg_s`, `onset_deg_s`, `iterations`, `converged`), `_peak_threshold(speed_deg_s: np.ndarray, usable: np.ndarray, params) -> PeakThreshold`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_threshold_converges_to_the_papers_own_arithmetic():
    """Spec §1.1, from the paper p. 193 and Figure 4. Iterate
    `PTn = mu(n-1) + 6*sigma(n-1)` over samples BELOW the previous threshold,
    stopping when `|PTn - PT(n-1)| < 1 deg/s`.

    On a normal noise floor with a few large peaks, the sub-threshold
    population IS the noise, so the converged value must land at
    `mu + 6*sigma` of that noise -- which is a number this test computes
    independently rather than reading back from the implementation."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, _peak_threshold,
    )

    rng = np.random.default_rng(0)
    noise = np.abs(rng.normal(5.0, 2.0, 5000))
    speed = noise.copy()
    speed[1000:1010] = 400.0          # a saccade, far above any noise level
    usable = np.ones(speed.size, dtype=bool)

    result = _peak_threshold(speed, usable, DEFAULT_NH_PARAMS)

    expected = noise.mean() + 6.0 * noise.std()
    assert abs(result.peak_deg_s - expected) < 2.0, (result.peak_deg_s, expected)
    assert result.converged
    assert result.onset_deg_s < result.peak_deg_s


def test_the_starting_value_does_not_matter():
    """The paper, p. 193: the initial threshold "could be in the range
    100-300 deg/sec, but the choice is not critical as long as there are
    saccades with peak velocities reaching this threshold." A converged
    result that moved with the start would mean the iteration is not
    converging at all."""
    import dataclasses

    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, _peak_threshold,
    )

    rng = np.random.default_rng(1)
    speed = np.abs(rng.normal(5.0, 2.0, 5000))
    speed[2000:2012] = 500.0
    usable = np.ones(speed.size, dtype=bool)

    results = [
        _peak_threshold(
            speed, usable,
            dataclasses.replace(DEFAULT_NH_PARAMS, initial_peak_threshold_deg_s=start),
        ).peak_deg_s
        for start in (100.0, 200.0, 300.0)
    ]

    assert max(results) - min(results) < 1.0, results


def test_an_oscillating_distribution_terminates():
    """Spec §9 item 2: **the paper states no iteration cap.** It reports
    convergence "in about two iterations" and gives a criterion, but a
    distribution that never satisfies it would loop forever. `max_iterations`
    is this implementation's own guard, not the paper's, and the result says
    so rather than pretending to have converged."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, _peak_threshold,
    )

    # Uniform speed: every sample equals the mean, sigma is 0, so the update
    # collapses the threshold onto the data and no sample ever falls below it.
    speed = np.full(1000, 50.0)
    usable = np.ones(1000, dtype=bool)

    result = _peak_threshold(speed, usable, DEFAULT_NH_PARAMS)

    assert result.iterations <= DEFAULT_NH_PARAMS.max_iterations
    assert not result.converged


def test_unusable_samples_are_excluded_from_the_estimate():
    """The same reasoning `detect_engbert_kliegl`'s own docstring gives: a
    blink's velocity spike inside the estimate would inflate the noise scale
    and desensitise the detector for the whole recording."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, _peak_threshold,
    )

    rng = np.random.default_rng(2)
    speed = np.abs(rng.normal(5.0, 2.0, 3000))
    speed[500:600] = 900.0                     # a blink, masked below
    usable = np.ones(3000, dtype=bool)
    usable[500:600] = False
    speed[1500:1512] = 400.0                   # a real saccade, kept

    masked = _peak_threshold(speed, usable, DEFAULT_NH_PARAMS).peak_deg_s
    unmasked = _peak_threshold(
        speed, np.ones(3000, dtype=bool), DEFAULT_NH_PARAMS
    ).peak_deg_s

    assert masked < unmasked, (masked, unmasked)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_nystrom_holmqvist.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wl_preproc.eye.detect.nystrom_holmqvist'`.

- [ ] **Step 3: Implement**

Create the module with its header docstring, `NystromHolmqvistParams` carrying spec §7's twelve fields with the paper's values, `DEFAULT_NH_PARAMS`, and:

```python
@dataclass(frozen=True, slots=True)
class PeakThreshold:
    """The converged thresholds, plus whether the iteration actually got
    there. `converged=False` is not a failure to hide: the paper gives no
    iteration cap, so a caller must be able to tell a converged threshold
    from one that hit this implementation's own guard."""

    peak_deg_s: float
    onset_deg_s: float
    iterations: int
    converged: bool


def _peak_threshold(speed_deg_s, usable, params) -> PeakThreshold:
    """The adaptive velocity threshold, iterated to convergence.

    The paper's central novelty (p. 193, Figure 4), and what makes the
    algorithm "settings-free for the user": rather than an experimenter
    choosing a velocity threshold, it is derived from the data's own noise.

    `PT1` is `params.initial_peak_threshold_deg_s`. For all samples with
    velocity below the current threshold, the mean and standard deviation are
    computed and the threshold updated as `PTn = mu + 6*sigma`, iterating
    until `|PTn - PT(n-1)| < 1 deg/s`.

    **The 6 is not arbitrary.** The paper calls it "a good robust level" and
    notes it "is also used in microsaccade detection algorithms (Engbert &
    Kliegl, 2003)" -- the same 6 `engbert_kliegl.py::DEFAULT_LAMBDA` already
    carries.

    **Unusable samples are excluded**, for `detect_engbert_kliegl`'s own
    stated reason: a blink's velocity spike would inflate the scale and
    desensitise the detector for the whole recording.
    """
    sub = speed_deg_s[usable]
    if sub.size == 0:
        return PeakThreshold(0.0, 0.0, 0, False)

    threshold = float(params.initial_peak_threshold_deg_s)
    onset = threshold
    for iteration in range(1, params.max_iterations + 1):
        below = sub[sub < threshold]
        if below.size == 0:
            return PeakThreshold(threshold, onset, iteration, False)
        mu, sigma = float(below.mean()), float(below.std())
        updated = mu + params.peak_threshold_sigma * sigma
        onset = mu + params.onset_threshold_sigma * sigma
        if abs(updated - threshold) < params.convergence_deg_s:
            return PeakThreshold(updated, onset, iteration, True)
        threshold = updated
    return PeakThreshold(threshold, onset, params.max_iterations, False)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_nystrom_holmqvist.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/eye/detect/nystrom_holmqvist.py tests/eye/detect/test_nystrom_holmqvist.py
git commit -m "nh: the adaptive peak threshold, iterated to convergence

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RTPFVKnR7yeEYsC1CYuiaH"
```

---

### Task 3: Saccade onset and offset

**Files:**
- Modify: `wl_preproc/eye/detect/nystrom_holmqvist.py`
- Test: `tests/eye/detect/test_nystrom_holmqvist.py`

**Interfaces:**
- Consumes: `PeakThreshold`, `_peak_threshold`, `NystromHolmqvistParams` (Task 2).
- Produces: `_saccade_bounds(speed_deg_s, peak_start: int, peak_stop: int, thresholds: PeakThreshold, fs_hz: float, params) -> tuple[int, int, float] | None`, returning `(onset, offset, offset_threshold_deg_s)` or `None` when the saccade is rejected. **It returns the offset THRESHOLD as well as the bounds**, because Task 4's `_glissade_bounds` needs the same number and recomputing it there would be a second place the alpha/beta weighting could drift to.

**The rule most easily inverted.** Spec §1.2: the local-noise window **precedes** the saccade, and the paper says why — "To avoid contamination from glissadic movements." A window placed after would measure the glissade and raise the very threshold meant to find it. Step 1's second test exists to catch that inversion.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_local_noise_window_precedes_the_saccade():
    """Spec §1.2, from the paper p. 194: the local noise factor is computed
    "over the velocity samples within a window with size tau_min msec ... and
    PRECEDING the saccade currently being processed. To avoid contamination
    from glissadic movements."

    This fixture is quiet BEFORE the saccade and noisy AFTER it -- the shape
    of a real glissade. A window taken after the saccade would measure the
    glissade, raise the offset threshold, and cut the saccade short or
    swallow the glissade entirely. A window taken before measures the quiet
    and puts the offset where it belongs."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _saccade_bounds,
    )

    fs = 500.0
    speed = np.full(600, 2.0)               # quiet baseline
    speed[300:320] = 300.0                  # the saccade
    speed[320:340] = 40.0                   # a glissade right after it
    thresholds = PeakThreshold(
        peak_deg_s=100.0, onset_deg_s=20.0, iterations=2, converged=True
    )

    bounds = _saccade_bounds(speed, 300, 320, thresholds, fs, DEFAULT_NH_PARAMS)

    assert bounds is not None
    onset, offset, offset_threshold = bounds
    assert offset_threshold > 0.0
    # The offset lands at the saccade's own end, NOT extended through the
    # glissade and not pulled inside the saccade by a glissade-inflated
    # threshold.
    assert 295 <= onset <= 300, onset
    assert 318 <= offset <= 325, offset


def test_a_saccade_shorter_than_the_minimum_is_rejected():
    """Table 2: minimum saccade duration 10 msec -- "large enough to avoid
    noise being falsely categorized as saccades but small enough to include
    short saccades (~1 deg)". At 500 Hz that is 5 samples."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _saccade_bounds,
    )

    fs = 500.0
    speed = np.full(400, 2.0)
    speed[200:202] = 300.0                  # 2 samples = 4 ms, below 10 ms
    thresholds = PeakThreshold(100.0, 20.0, 2, True)

    assert _saccade_bounds(speed, 200, 202, thresholds, fs, DEFAULT_NH_PARAMS) is None


def test_a_saccade_not_preceded_by_stillness_is_rejected():
    """The paper, p. 195: "we exclude saccades that are preceded by a period
    where mu_t > theta_PT, since this indicates that there was no period of
    stillness prior to the saccade onset (most often, indicating recording
    imperfections)"."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _saccade_bounds,
    )

    fs = 500.0
    speed = np.full(600, 2.0)
    speed[260:300] = 250.0                  # no stillness before the saccade
    speed[300:320] = 300.0
    thresholds = PeakThreshold(100.0, 20.0, 2, True)

    assert _saccade_bounds(speed, 300, 320, thresholds, fs, DEFAULT_NH_PARAMS) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_nystrom_holmqvist.py -k "local_noise or shorter_than or stillness" -v`
Expected: FAIL — `cannot import name '_saccade_bounds'`.

- [ ] **Step 3: Implement**

```python
def _saccade_bounds(speed_deg_s, peak_start, peak_stop, thresholds, fs_hz, params):
    """`[onset, offset)` for one velocity peak, or `None` if it is rejected.

    **Onset** (paper p. 194, Figure 5A): search backward from the peak to the
    first sample below `theta_ST_onset = mu_z + 3*sigma_z` where
    `(theta_i - theta_(i+1)) >= 0` -- "until the first local minimum is
    found."

    **Offset** is the adaptive half, and the reason this algorithm exists. It
    weights the trial-wide onset threshold against a LOCAL noise estimate:

        theta_t          = mu_t + 3*sigma_t     over tau_min PRECEDING the saccade
        theta_ST_offset  = alpha*theta_ST_onset + beta*theta_t

    with `alpha = 0.7`, `beta = 0.3` (Table 2). Offset is the first sample
    below that where `(theta_i - theta_(i+1)) <= 0`.

    **The window PRECEDES the saccade, and inverting that is the single
    easiest way to break this detector.** The paper's reason: "To avoid
    contamination from glissadic movements." A following window measures the
    glissade and raises the threshold meant to find it.
    """
    tau = max(int(round(params.min_fixation_duration_ms * fs_hz / 1000.0)), 1)

    onset = peak_start
    while onset > 0:
        if speed_deg_s[onset] <= thresholds.onset_deg_s and (
            speed_deg_s[onset] - speed_deg_s[onset + 1] >= 0
        ):
            break
        onset -= 1

    window = speed_deg_s[max(onset - tau, 0):onset]
    if window.size == 0:
        return None
    local = float(window.mean()) + params.local_noise_sigma * float(window.std())
    # No period of stillness before the saccade (paper p. 195).
    if float(window.mean()) > thresholds.peak_deg_s:
        return None

    offset_threshold = (
        params.offset_alpha * thresholds.onset_deg_s + params.offset_beta * local
    )
    offset = peak_stop
    limit = speed_deg_s.size - 1
    while offset < limit:
        if speed_deg_s[offset] <= offset_threshold and (
            speed_deg_s[offset] - speed_deg_s[offset + 1] <= 0
        ):
            break
        offset += 1

    min_samples = max(int(round(params.min_saccade_duration_ms * fs_hz / 1000.0)), 1)
    if offset - onset < min_samples:
        return None
    return onset, offset, offset_threshold
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_nystrom_holmqvist.py -v`
Expected: PASS (7 tests: Task 2's four plus these three).

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/eye/detect/nystrom_holmqvist.py tests/eye/detect/test_nystrom_holmqvist.py
git commit -m "nh: saccade bounds, with the local noise window before the saccade

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RTPFVKnR7yeEYsC1CYuiaH"
```

---

### Task 4: Glissade detection, both criteria

**Files:**
- Modify: `wl_preproc/eye/detect/nystrom_holmqvist.py`
- Test: `tests/eye/detect/test_nystrom_holmqvist.py`

**Interfaces:**
- Consumes: `PeakThreshold`, `_saccade_bounds`, `NystromHolmqvistParams`.
- Produces: `_glissade_bounds(speed_deg_s, gaze_deg, saccade_onset: int, saccade_offset: int, offset_threshold_deg_s: float, thresholds: PeakThreshold, fs_hz: float, params) -> tuple[int, int] | None`.

**Both criteria, per spec §3.** The paper defines low- and high-velocity glissades as mutually exclusive, and Table 3's 47.8% is their union. A detector implementing only one would produce roughly two thirds of the published rate and look broken while being correct.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_high_velocity_glissade_is_found():
    """Paper p. 195: the high-velocity criterion "requires that the velocity
    curve within a tau_min (40) msec window after the saccadic offset raises
    above the peak saccade threshold, theta_PT, and down below it, at least
    once. In other words, a high-velocity glissade has a velocity peak that
    would qualify it for saccadic status"."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _glissade_bounds,
    )

    fs = 500.0
    speed = np.full(600, 2.0)
    speed[300:320] = 300.0                       # saccade
    speed[320:330] = 150.0                       # above peak threshold (100)
    gaze = np.zeros((600, 2))
    gaze[320:, 0] = 0.4                          # small, smaller than the saccade
    gaze[300:320, 0] = np.linspace(0.0, 0.4, 20)
    gaze[:300, 0] = 0.0
    thresholds = PeakThreshold(100.0, 20.0, 2, True)

    bounds = _glissade_bounds(
        speed, gaze, 300, 320, 25.0, thresholds, fs, DEFAULT_NH_PARAMS
    )

    assert bounds is not None
    start, stop = bounds
    assert start == 320, "the glissade's onset IS the saccade's offset"
    assert stop > start


def test_a_low_velocity_glissade_is_found():
    """Same criterion, except the curve need only rise above the saccade
    OFFSET threshold rather than the peak threshold (paper p. 195, Figure
    5B). This is the one that catches the small post-saccadic wobbles that
    §2.5 argues a dual-Purkinje tracker shows after every saccade."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _glissade_bounds,
    )

    fs = 500.0
    speed = np.full(600, 2.0)
    speed[300:320] = 300.0
    speed[320:328] = 40.0                        # above offset (25), below peak (100)
    gaze = np.zeros((600, 2))
    gaze[300:320, 0] = np.linspace(0.0, 0.4, 20)
    gaze[320:, 0] = 0.45
    thresholds = PeakThreshold(100.0, 20.0, 2, True)

    bounds = _glissade_bounds(
        speed, gaze, 300, 320, 25.0, thresholds, fs, DEFAULT_NH_PARAMS
    )

    assert bounds is not None
    assert bounds[0] == 320


def test_no_glissade_when_the_window_stays_quiet():
    """Below the offset threshold is not a glissade -- it is the fixation
    that follows the saccade."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _glissade_bounds,
    )

    fs = 500.0
    speed = np.full(600, 2.0)
    speed[300:320] = 300.0
    gaze = np.zeros((600, 2))
    gaze[300:320, 0] = np.linspace(0.0, 0.4, 20)
    gaze[320:, 0] = 0.4
    thresholds = PeakThreshold(100.0, 20.0, 2, True)

    assert _glissade_bounds(
        speed, gaze, 300, 320, 25.0, thresholds, fs, DEFAULT_NH_PARAMS
    ) is None


def test_one_saccade_yields_at_most_one_glissade():
    """Spec §8 item 4 asks that the two criteria be "mutually exclusive."
    Under §3's resolution that is not a test of two code paths: both criteria
    emit `pso`, and their UNION is what Table 3's 47.8% measures. What
    remains checkable, and what matters to storage, is that one saccade never
    yields two overlapping glissade runs -- `_insert_trace` paints intervals
    onto one array, so a duplicate would silently overwrite itself.

    A window holding both a high-velocity excursion and a later low-velocity
    one must still produce a single run."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _glissade_bounds,
    )

    fs = 500.0
    speed = np.full(600, 2.0)
    speed[300:320] = 300.0
    speed[320:324] = 150.0                       # above peak (100)
    speed[324:330] = 40.0                        # above offset (25), below peak
    gaze = np.zeros((600, 2))
    gaze[300:320, 0] = np.linspace(0.0, 0.4, 20)
    gaze[320:, 0] = 0.45
    thresholds = PeakThreshold(100.0, 20.0, 2, True)

    bounds = _glissade_bounds(
        speed, gaze, 300, 320, 25.0, thresholds, fs, DEFAULT_NH_PARAMS
    )

    assert bounds is not None
    start, stop = bounds
    assert start == 320
    assert stop >= 330, "one run must span both excursions, not two"


def test_a_glissade_larger_than_its_saccade_is_omitted():
    """Paper p. 196: "Glissades with an amplitude larger than their
    preceeding saccades were omitted." A post-saccadic movement bigger than
    the saccade it follows is not lens wobble."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, PeakThreshold, _glissade_bounds,
    )

    fs = 500.0
    speed = np.full(600, 2.0)
    speed[300:320] = 300.0
    speed[320:330] = 150.0
    gaze = np.zeros((600, 2))
    gaze[300:320, 0] = np.linspace(0.0, 0.2, 20)   # a 0.2 deg saccade
    gaze[320:330, 0] = np.linspace(0.2, 3.0, 10)   # a 2.8 deg "glissade"
    gaze[330:, 0] = 3.0
    thresholds = PeakThreshold(100.0, 20.0, 2, True)

    assert _glissade_bounds(
        speed, gaze, 300, 320, 25.0, thresholds, fs, DEFAULT_NH_PARAMS
    ) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_nystrom_holmqvist.py -k glissade -v`
Expected: FAIL — `cannot import name '_glissade_bounds'`.

- [ ] **Step 3: Implement**

```python
def _glissade_bounds(
    speed_deg_s, gaze_deg, saccade_onset, saccade_offset,
    offset_threshold_deg_s, thresholds, fs_hz, params,
):
    """`[start, stop)` of the glissade following one saccade, or `None`.

    **Both of the paper's criteria, because Table 3's 47.8% is their union**
    (design spec §3, an inference from Figure 10 and marked as one). The two
    are defined as mutually exclusive -- "low-velocity glissades are not a
    subset of high-velocity glissades" -- so detecting only one produces
    roughly two thirds of the published rate.

    - HIGH-velocity: the curve rises above `theta_PT` within `tau_min` of the
      saccade offset. "A high-velocity glissade has a velocity peak that
      would qualify it for saccadic status."
    - LOW-velocity: identical, but only above `theta_ST_offset`.

    Onset is the saccade's offset. Offset is where
    `(theta_i - theta_(i+1)) <= 0` after the last velocity peak in the
    glissade. A glissade whose amplitude exceeds its preceding saccade's is
    omitted (p. 196).
    """
    from wl_preproc.eye.detect.measure import amplitude

    tau = max(int(round(params.min_fixation_duration_ms * fs_hz / 1000.0)), 1)
    end = min(saccade_offset + tau, speed_deg_s.size)
    window = speed_deg_s[saccade_offset:end]
    if window.size == 0 or not (window > offset_threshold_deg_s).any():
        return None

    above = np.flatnonzero(window > offset_threshold_deg_s)
    last_peak = saccade_offset + int(above[-1])
    stop = last_peak
    limit = speed_deg_s.size - 1
    while stop < limit and speed_deg_s[stop] - speed_deg_s[stop + 1] > 0:
        stop += 1
    stop = min(stop + 1, speed_deg_s.size)
    if stop <= saccade_offset:
        return None

    saccade_amp = amplitude(gaze_deg, saccade_onset, saccade_offset)
    if amplitude(gaze_deg, saccade_offset, stop) > saccade_amp:
        return None
    return saccade_offset, stop
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_nystrom_holmqvist.py -v`
Expected: PASS (12 tests: Task 2's four, Task 3's three, these five).

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/eye/detect/nystrom_holmqvist.py tests/eye/detect/test_nystrom_holmqvist.py
git commit -m "nh: glissade detection, both criteria

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RTPFVKnR7yeEYsC1CYuiaH"
```

---

### Task 5: Assemble the detector and register it

**Files:**
- Modify: `wl_preproc/eye/detect/nystrom_holmqvist.py`, `wl_preproc/eye/detect/registry.py`
- Test: `tests/eye/detect/test_nystrom_holmqvist.py`, `tests/eye/detect/test_registry.py`

**Interfaces:**
- Produces: `detect_nystrom_holmqvist(gaze_deg, velocity_deg_s, available, fs_hz, params) -> list[Run]`, and `DETECTORS["nystrom_holmqvist"]` declaring `frozenset({Label.SACCADE, Label.PSO, Label.FIXATION})` with `defaults=DEFAULT_NH_PARAMS`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_detector_emits_saccade_pso_and_fixation():
    """The first registered detector to emit anything beyond the amplitude
    split. Its declared vocabulary is parent design spec §3.1's own:
    `{saccade, pso, fixation}`."""
    import numpy as np

    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, detect_nystrom_holmqvist,
    )

    fs, n = 500.0, 3000
    rng = np.random.default_rng(3)
    gaze = np.cumsum(rng.normal(0.0, 0.002, (n, 2)), axis=0)
    for onset in (600, 1400, 2200):
        gaze[onset:onset + 12, 0] += np.linspace(0.0, 3.0, 12)
        gaze[onset + 12:, 0] += 3.0
        gaze[onset + 12:onset + 22, 0] += np.concatenate(
            [np.linspace(0.0, 0.25, 5), np.linspace(0.25, 0.0, 5)]
        )
    velocity = np.gradient(gaze, axis=0) * fs
    available = np.array([None] * n, dtype=object)

    runs = detect_nystrom_holmqvist(gaze, velocity, available, fs, DEFAULT_NH_PARAMS)

    labels = {run.label for run in runs}
    assert Label.SACCADE in labels
    assert Label.PSO in labels, "no glissade found on a trace built to contain three"
    assert labels <= {Label.SACCADE, Label.PSO, Label.FIXATION}


def test_no_run_overlaps_another():
    """`_insert_trace` paints intervals onto one array and lets a later
    interval overwrite an earlier one, so overlapping runs would silently
    lose whichever was painted first."""
    import numpy as np

    from wl_preproc.eye.detect.nystrom_holmqvist import (
        DEFAULT_NH_PARAMS, detect_nystrom_holmqvist,
    )

    fs, n = 500.0, 3000
    rng = np.random.default_rng(4)
    gaze = np.cumsum(rng.normal(0.0, 0.002, (n, 2)), axis=0)
    for onset in (600, 1400, 2200):
        gaze[onset:onset + 12, 0] += np.linspace(0.0, 3.0, 12)
        gaze[onset + 12:, 0] += 3.0
    velocity = np.gradient(gaze, axis=0) * fs
    available = np.array([None] * n, dtype=object)

    runs = sorted(
        detect_nystrom_holmqvist(gaze, velocity, available, fs, DEFAULT_NH_PARAMS),
        key=lambda run: run.start,
    )

    for earlier, later in zip(runs, runs[1:]):
        assert earlier.stop <= later.start, (earlier, later)


def test_nystrom_holmqvist_is_registered_with_its_vocabulary_and_defaults():
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.nystrom_holmqvist import NystromHolmqvistParams
    from wl_preproc.eye.detect.registry import get_detector

    detector = get_detector("nystrom_holmqvist")

    assert detector.vocabulary == frozenset(
        {Label.SACCADE, Label.PSO, Label.FIXATION}
    )
    assert isinstance(detector.defaults, NystromHolmqvistParams)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/eye/detect -k "emits_saccade_pso or no_run_overlaps or is_registered_with_its_vocabulary" -v`
Expected: FAIL — `cannot import name 'detect_nystrom_holmqvist'`.

- [ ] **Step 3: Implement**

```python
def detect_nystrom_holmqvist(gaze_deg, velocity_deg_s, available, fs_hz, params):
    """Labelled half-open `[start, stop)` intervals, in sample indices.

    The first registered detector to emit anything beyond the amplitude
    split. Its saccadic slice is `{saccade}` alone, so its conjunction runs
    take `_conjunction_label`'s DEGENERATE branch and `classify` is never
    asked -- which is why this params dataclass declares no
    `microsaccade_max_deg` and never receives one.
    """
    speed = np.hypot(velocity_deg_s[:, 0], velocity_deg_s[:, 1])
    # A LOCAL first difference of the shared estimator's own output, used
    # only for Table 2's acceleration rejection. Not a second shared
    # estimator: design spec section 3.2's "one shared velocity estimator
    # across all seven" is about the velocity every detector reads, and this
    # derives from that one rather than replacing it.
    acceleration = np.abs(np.gradient(speed)) * fs_hz

    usable = np.array([entry is None for entry in available], dtype=bool)
    usable &= speed <= params.max_velocity_deg_s
    usable &= acceleration <= params.max_acceleration_deg_s2
    if not usable.any():
        return []

    thresholds = _peak_threshold(speed, usable, params)
    if thresholds.peak_deg_s <= 0:
        return []

    runs: list[Run] = []
    claimed = np.zeros(speed.size, dtype=bool)
    for peak_start, peak_stop in _true_runs((speed > thresholds.peak_deg_s) & usable):
        bounds = _saccade_bounds(speed, peak_start, peak_stop, thresholds, fs_hz, params)
        if bounds is None:
            continue
        onset, offset, offset_threshold = bounds
        if claimed[onset:offset].any():
            continue          # an earlier saccade already owns these samples
        runs.append(Run(start=onset, stop=offset, label=Label.SACCADE))
        claimed[onset:offset] = True

        glissade = _glissade_bounds(
            speed, gaze_deg, onset, offset, offset_threshold, thresholds, fs_hz, params
        )
        if glissade is not None and not claimed[glissade[0]:glissade[1]].any():
            runs.append(Run(start=glissade[0], stop=glissade[1], label=Label.PSO))
            claimed[glissade[0]:glissade[1]] = True

    # Fixations are "everything that is not noise, saccades, or glissades"
    # (paper p. 195), subject to tau_min.
    min_fixation = max(int(round(params.min_fixation_duration_ms * fs_hz / 1000.0)), 1)
    for start, stop in _true_runs(~claimed & usable):
        if stop - start >= min_fixation:
            runs.append(Run(start=start, stop=stop, label=Label.FIXATION))

    return sorted(runs, key=lambda run: run.start)


def _true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Maximal `True` stretches as half-open intervals. Same shape as
    `engbert_kliegl.py`'s own private helper; duplicated rather than shared
    because that one is private to its module and this detector's is the
    second use, not yet a third."""
    padded = np.concatenate(([False], mask, [False]))
    edges = np.diff(padded.astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True))
```

The `claimed` mask is what makes Step 1's `test_no_run_overlaps_another` pass
by construction: `_insert_trace` paints intervals onto one array and lets a
later one overwrite an earlier one, so two runs covering the same sample
would silently lose whichever was painted first.

Then register it:

```python
    "nystrom_holmqvist": Detector(
        name="nystrom_holmqvist",
        vocabulary=frozenset({Label.SACCADE, Label.PSO, Label.FIXATION}),
        run=detect_nystrom_holmqvist,
        defaults=DEFAULT_NH_PARAMS,
    ),
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS. `register_default_paramsets` now registers three detectors — this is the first real exercise of the fix that made a third detector possible.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc tests
git commit -m "nh: assemble the detector and register it

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RTPFVKnR7yeEYsC1CYuiaH"
```

---

### Task 6: It populates, and the conjunction carries `pso`

**Files:**
- Test: `tests/schema/test_detect_populate.py`

**Interfaces:** consumes everything above. No production code.

**This is the first production exercise of per-kind conjunction intersection.** Everything merged on `76a8199` has been tested only against fixture detectors.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py -k nystrom -v`
Expected: FAIL before Task 5, PASS after. Run once with Tasks 2-5 stashed to confirm it genuinely fails, then unstash.

- [ ] **Step 3: No implementation.** If these fail after Task 5, the defect is in Tasks 1-5.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/schema/test_detect_populate.py
git commit -m "test: Nystrom-Holmqvist populates, and its conjunction carries pso

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RTPFVKnR7yeEYsC1CYuiaH"
```

---

### Task 7: Validation against the paper, nulls first

**Files:**
- Create: `tests/eye/detect/test_nystrom_holmqvist_validation.py`
- Modify: `pyproject.toml` (REMoDNaV as a `dev` extra only)

**Interfaces:** consumes `detect_nystrom_holmqvist`.

**The rule that binds this task, from the Otero-Millan round:** *an oracle-free statistic is worthless until a null has been run against it.* Stage 2A withdrew a check rather than relaxing it when a duration-matched random-span control and a deliberately broken detector both scored higher than the correct one. **Build the null before the check, not after.**

- [ ] **Step 1: Write the null FIRST, and prove it fails the check**

```python
def _random_span_null(runs, n_samples, rng):
    """A duration-matched random-span control: the same number of spans, with
    the same durations, placed uniformly at random.

    **This exists before the checks below, not after them.** Stage 2A adopted
    a main-sequence statistic because it "is a property of real saccades that
    no artefact reproduces", then measured that a random control AND a
    detector with both acceptance gates removed both scored HIGHER than the
    correct detector. The check was withdrawn as invalid rather than relaxed.
    The rule that episode left: an oracle-free statistic is worthless until a
    null has been run against it, and building the null is cheap."""
    durations = [run.stop - run.start for run in runs]
    spans = []
    for duration in durations:
        start = int(rng.integers(0, max(n_samples - duration, 1)))
        spans.append((start, start + duration))
    return sorted(spans)


def test_the_null_fails_the_glissade_rate_check():
    """If the null passes, the check does not discriminate and must be
    WITHDRAWN, not relaxed. This test is what says the checks below mean
    something."""
    import numpy as np

    rng = np.random.default_rng(7)
    # A random control has no saccade-glissade adjacency at all, so the
    # fraction of its "saccades" followed within tau_min by a "glissade" is
    # chance-level, far below the paper's 47.8%.
    n_samples = 500_000
    fake_saccades = _random_span_null(
        [_Span(0, 20)] * 500, n_samples, rng
    )
    fake_glissades = _random_span_null(
        [_Span(0, 12)] * 500, n_samples, rng
    )

    rate = _glissadic_fraction(fake_saccades, fake_glissades, tau_samples=20)

    assert rate < 0.10, (
        f"a random control scored {rate:.3f} on the glissade-rate check; the "
        "check does not discriminate and must be withdrawn, not relaxed"
    )
```

`_Span` is a two-field `namedtuple("_Span", "start stop")` defined at module
scope. `_glissadic_fraction(saccades, glissades, tau_samples)` returns the
fraction of saccades followed within `tau_samples` by a glissade starting at
that saccade's offset.

- [ ] **Step 2: Run the null test and confirm it passes**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_nystrom_holmqvist_validation.py -k null -v`
Expected: PASS. If it FAILS — if the random control scores near 47.8% — stop
and report: the statistic is invalid and the remaining steps must not be
written against it.

- [ ] **Step 3: The glissade rate, against the reference recording**

```python
@pytest.mark.skipif(
    not os.environ.get("WLPP_OHDPI_REFERENCE"),
    reason="needs the real reference recording",
)
def test_the_glissade_rate_is_in_the_papers_band():
    """Spec §5. Table 3: 47.8% of saccades carry a glissade in reading, 59.1%
    in scene perception, as the union of both criteria.

    A BAND, not the point value, and the reason is stated rather than
    assumed: the paper's data are HUMAN, reading and scene perception, at
    1250 Hz on an SMI HiSpeed. This rig is NHP at 500 Hz on a dual-Purkinje
    tracker. What has a mechanistic reason to transfer is the glissade
    statistics -- a glissade is lens wobble, a property of the eye and the
    instrument rather than the task -- and §2.5 argues a DPI should show MORE
    of it, not less.

    **A rate near zero indicts the velocity estimator first** (spec §2, §9
    item 1): the shared five-point differentiator may be smoothing ~20 ms
    wobbles away, which is the one consequence of not using the paper's
    Savitzky-Golay. Do not switch the estimator on any other evidence."""
    rate = _measured_glissade_rate()

    assert 0.20 <= rate <= 0.90, (
        f"measured {rate:.3f}; the paper reports 0.478 (reading) and 0.591 "
        "(scene perception). Near zero indicts the velocity estimator "
        "(spec §2); far above 0.9 suggests the offset threshold is too low."
    )
```

- [ ] **Step 4: Glissade duration**

Same gating. Spec §5: 22.2 ± 9.8 ms (reading), 25.0 ± 9.8 (scene perception).
Assert the measured mean lands in `5.0 <= mean_ms <= 60.0` — the low tens of
milliseconds. Hundreds of milliseconds means glissades are being merged with
the fixations that follow them; single-digit means they are being truncated
at the offset threshold. State both failure directions in the message, the
way Step 3 does.

- [ ] **Step 5: REMoDNaV as an oracle, gated and dev-only**

Add to `pyproject.toml` under the existing `dev` extra only:

```toml
remodnav = ">=1.1"
```

**Never a runtime dependency and never shipped** (parent §3.2). Then:

```python
remodnav = pytest.importorskip("remodnav")
```

Compare event boundaries on the same trace, and assert only that the two
find a COMPARABLE NUMBER of saccades — within a factor of two. Disagreement
is evidence, not a defect: REMoDNaV deliberately changed parts of the method
(spec §5), so a tighter assertion would be pinning our detector to theirs.

- [ ] **Step 6: Record every measured number in the spec**

Replace spec §5's predictions with the measurements, each with its
configuration stated beside it — the discipline
`test_otero_millan_validation.py` follows because that document twice carried
a number whose configuration was not recorded. Update spec §9 items 1 and 3
with what the measurement settled.

- [ ] **Step 6: Commit**

```bash
git add tests/eye/detect/test_nystrom_holmqvist_validation.py pyproject.toml docs
git commit -m "nh: validate against the paper's own statistics, nulls first

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RTPFVKnR7yeEYsC1CYuiaH"
```

---

### Task 8: The documents

**Files:** `docs/CHECKPOINT.md`, `wl.yaml`, `docs/handoffs/2026-09-05-nystrom-holmqvist-built.md`

- [ ] **Step 1: Update `wl.yaml`.** `status.phase`, `status.next` and `status.describes` only. Per CLAUDE.md this adds no deployment and no runtime dependency — REMoDNaV is a `dev` extra — so **`runs_on` and `builds_on` are unchanged**. Whether a dev-only test oracle belongs in `third_party` is a judgement: read CLAUDE.md's own table (*"a dependency added, dropped, or version-pinned → `third_party`"*) and record the decision either way.
- [ ] **Step 2: Update `docs/CHECKPOINT.md`**, header included — its own note says the header is part of every update.
- [ ] **Step 3: Write the handoff**, recording: the measured glissade rate and duration; whether the shared velocity estimator preserved glissades (spec §9 item 1); whether Table 3's 47.8% really is the union (spec §9 item 3); what was decided about the paper's noise rule versus the validity mask (spec §9 item 4); and that **the kind-disagreement measurement is now possible and is the next thing to do**, from the PER-EYE traces, never the conjunction.
- [ ] **Step 4: Validate the manifest**

```bash
pip install git+https://github.com/jakewesterberg/wl-manifest.git && wl-check
```

- [ ] **Step 5: Commit**

---

## Before opening a PR

- [ ] **Whole-branch review.** Stage 1's nine per-task reviews were all green and a whole-branch review then found ten defects.
- [ ] **Push and read CI off the run**, both interpreters: `gh run list --branch spec/nystrom-holmqvist`.
- [ ] **Confirm every constant traces to the paper.** Spec §7 is the table; each value must match Table 2 or the page cited. The one exception is `max_iterations`, which spec §9 item 2 records as this implementation's own guard.
