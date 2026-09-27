# NSLR-HMM: the fifth detector, which segments instead of differentiating

**Design spec, 2026-09-27.** Implements the NSLR row of design spec
`2026-08-31-saccade-detection-design.md` §3.1's table: NSLR,
`saccade / pso / pursuit / fixation`, "reimplemented". It follows REMoDNaV
(merged `cef2ce4`), whose spec, `2026-09-26-remodnav-design.md`, is the model
for this one, and whose human-coder harness this one reuses.

**The paper was read, and so was the code.**
- **The paper.** Pekkanen & Lappi (2017), *Scientific Reports* 7, 17726,
  [10.1038/s41598-017-17983-x](https://doi.org/10.1038/s41598-017-17983-x),
  read in full from PubMed Central (PMC5735175).
- **The code.** `gitlab.com/nslr/nslr` at `67d03f8` (2023-02-23),
  `nslr/slow_nslr.py`, and `gitlab.com/nslr/nslr-hmm` at `3598fee`
  (2018-04-26), `nslr_hmm.py`. Every `slow_nslr.py` and `nslr_hmm.py` line
  number below refers to those commits.

The paper points at the code itself: the method is "presented more formally
along with a Python implementation in a Supplementary Note". `slow_nslr.py` is
that implementation.

**Both halves are AGPL-3.0, and this corrects the parent spec.** Parent §3.2
says NSLR's "segmentation half declares no licence". Both repositories carry
an AGPL-3.0 `LICENSE`, and `slow_nslr.py` opens "Released under AGPL-3.0". The
PyPI package `nslr` 0.0.5 does leave its licence field empty, which is
plausibly what the parent spec read. **The requester approved test-only use on
2026-09-27**: the reference may be run by the test suite but is never shipped,
never run by the pipeline and never copied into it (§7).

---

## 0. Why this detector, and why now

**The requester chose it on 2026-09-27**, over Bayesian microsaccade detection.
Hardware is unchanged: the rig is not ready and the compute machine is not
assembled, so hardware-free work continues.

**It has a runnable reference, which BMD does not.** The reference is the
paper's own pure-Python implementation. It is far too slow for production
(§8 item 2) but fine on the short traces a fidelity check needs. A throwaway
scalar rewrite of its segmenter matched the reference's split indices exactly
on 3,000 samples during design. BMD's reference is C++ with no licence (parent
§3.2), and its discriminating checks need a calibrated session the lab does
not have.

**It is a genuinely different method, which is what the consensus suite
needs.** Every registered detector so far thresholds a velocity signal. NSLR
never computes a sample-level velocity. It fits the position signal as a
continuous chain of straight segments and classifies whole segments. Where it
agrees with the threshold detectors, that agreement is evidence. Where it
disagrees, the disagreement is about method, not smoothing, which is the kind
parent §6 exists to measure.

## 1. The algorithm

### 1.1 Segmentation (`slow_nslr.py` 23–71)

A pruned search over segmentation hypotheses, PELT-style (the paper's
"Denoising by Naive Segmented Linear Regression").

- **What a hypothesis carries.** It is a candidate last segment, with running
  sums per axis: the count, the elapsed time `t`, `Σt`, `Σt²`, `Σx`, `Σx²` and
  `Σtx`. It also carries a parent, a log-likelihood and an intercept `b`.
- **Intercepts.**
  - The root hypothesis fits its intercept by least squares (`slow_nslr.py`
    37–41).
  - Every later hypothesis takes its intercept from its parent's predicted
    endpoint — the "greedy continuity" that makes the problem decomposable
    (`slow_nslr.py` 58–60).
- **Per sample**, every hypothesis:
  1. updates its sums;
  2. refits its slope;
  3. recomputes its residual sum of squares `S`;
  4. adds to its likelihood `Σ log(1/(√(2π)σ)) + Σ (S_prev − S)/(2σ²)`
     (`slow_nslr.py` 43–52).
- **Branching and pruning.** After each sample from the second on, the most
  likely hypothesis spawns a new-segment hypothesis whose likelihood is its own
  plus the split prior (§1.2). Every hypothesis less likely than the new one is
  then pruned, except the one that spawned it (`slow_nslr.py` 56–64).
- **The result.** Backtracking parents from the most likely final hypothesis
  gives the split indices `J` (`slow_nslr.py` 66–71).

### 1.2 The split prior (`slow_nslr.py` 158–172)

```
logit_pinc = 0.5/σ̄ + 0.5·ln(2A) − 1.0·ln(2D) + 0.1·ln(2V) − 3.0
split(dt)  = ln sigmoid(logit_pinc − ln(1/dt))
```

`σ̄` is the mean noise level over the two axes. `A` = 3.0° (saccade
amplitude), `D` = 0.3 s (slow-phase duration) and `V` = 5.0 °/s (slow-phase
speed) are the code's defaults. The paper says they were "numerically
estimated … based on simulated eye movement data" (Supplementary Methods).

### 1.3 The continuous fit (`slow_nslr.py` 75–124)

Given `J`, the segment endpoints are the least-squares continuous
piecewise-linear fit, solved as a tridiagonal system. The segmentation from
§1.1 is greedy; this fit is the "optimal continuous regression to the
segmentation obtained using the greedy continuity" (paper).

### 1.4 Noise estimation (`fit_gaze`, `slow_nslr.py` 174–191)

The noise levels `σ` (one per axis) are estimated, not given.
1. **Start** at the signal's own standard deviation per axis.
2. **Each pass:**
   - add the structural error, 0.1° per axis;
   - segment (§1.1) and fit (§1.3);
   - set `σ` to the residuals' standard deviation.
3. **Stop** when a `σ` pair recurs exactly — a float-tuple membership test
   (`slow_nslr.py` 189).

Measured during design, on synthetic traces: 5–6 passes. The structural error
exists, the paper says, so that "minute eye movements (e.g. microsaccades or
tremors)" are treated as noise. **This detector does not detect
microsaccades**, by construction.

### 1.5 Segment features (`nslr_hmm.py` 292–311)

For each segment:
- **The speed.** The segment's speed is the norm of its endpoint difference
  over its duration. The first feature is `log10` of that speed, clipped
  below at 1e−6 (`safelog`, `nslr_hmm.py` 67–68).
- **The turn.** The second feature is the cosine between this segment's
  direction and the previous segment's, scaled by `(1 − 1e−6)`, then
  Fisher-transformed with `arctanh`. A NaN becomes 0.

The previous direction starts at `(0, 0)`. A zero-speed segment's direction is
NaN, which makes its own cosine and the next segment's cosine 0.

### 1.6 The hidden Markov model (`nslr_hmm.py` 36–93, 313–323)

- **States:** fixation, saccade, PSO and smooth pursuit.
- **Emissions** are bivariate Gaussians over §1.5's two features. Their means
  and (diagonal) covariances are the published values, "estimated from data
  by doi:10.3758/s13428-016-0738-9" (`nslr_hmm.py` 37). That is the Andersson
  et al. (2017) human-coded dataset, which §5.3 uses too.
- **Transitions** (`nslr_hmm.py` 53–62) are equal weights, except:
  - fixation → PSO, PSO → saccade and pursuit → PSO are forbidden;
  - fixation ↔ pursuit carries half weight.

  Rows are normalised. The paper calls this "labeling inertia" (1/5 against
  2/5).
- **Initial state** probabilities are uniform.
- **Decoding** is Viterbi, in `log10`, with `log10` clipped at 1e−6
  (`nslr_hmm.py` 70–93). **Every emission after the first is normalised to sum
  to 1 before use; the first is not** (`nslr_hmm.py` 79). This implementation
  reproduces that asymmetry.
- **Samples** take their segment's class (`nslr_hmm.py` 325–337).

## 2. Paper against code

**The code is followed**, for REMoDNaV's reason (that spec, §2): it is what
produced the paper's numbers, and the paper defers to it for the formal
statement. No behavioural disagreement between the two was found in reading.
Two points outside the algorithm matter here:

- **The GitLab version is followed, not PyPI's.** PyPI's `nslr` 0.0.5 tests the
  root intercept's denominator with `> 0`, where GitLab `67d03f8` has `!= 0`
  (`slow_nslr.py` 38). The denominator `(Σt)² − nΣt²` is never positive, so the
  PyPI version always falls back to the mean and never fits the root intercept
  by least squares.
- **The README's pure-Python fallback does not exist.** The README says an
  install "will (silently, sadly) use the very much slower Python version if
  the building fails". At `67d03f8`, `nslr/__init__.py` imports only the C++
  module, and `setup.py` raises `BuildFailed`. The C++ does not build with
  current compilers either: its bundled Eigen 3.3.4 fails under this machine's
  clang, with `-std=c++14` too. §7 follows from this.
- **The reference no longer runs unmodified on current numpy.** Found while
  proving the design's prototype.
  - `nslr_hmm.py` 298, 300 and 302 call `float()` on one-element arrays, which
    numpy 2.4 rejects with a `TypeError`.
  - Its Viterbi (`nslr_hmm.py` 80) calls `np.row_stack`, which numpy 2.5
    removed. CI's Python 3.13 resolution now has numpy 2.5.3.

  The test loader supplies both (§7), and neither changes any arithmetic.
  `float` becomes the array's single element, which is what older numpy's
  `float()` returned. `row_stack` becomes `np.vstack`, identical for the single
  array it is given. With both in place, a prototype of this design matched
  the reference exactly — split indices, endpoints, features and every
  sample's label — on three synthetic traces, on Python 3.11 with numpy 2.4.6
  and on 3.13 with numpy 2.5.3.

## 3. Inputs: what is shared, and what does not apply

**No velocity estimator — the shared one does not apply.** NSLR never
differentiates. A segment's slope is its velocity, and that is the method.
Parent §3.2's "one shared velocity estimator across all seven" exists so that
detectors do not disagree about smoothing. NSLR has nothing to share, and
feeding it a velocity would not be NSLR. `detect_nslr` accepts the shared
velocity, because the `DetectFn` signature requires it, and ignores it.

**The validity mask splits the recording into pieces.** The reference cannot
take missing samples. The paper's own denoising benchmark filled them by
linear interpolation, and its classification benchmark does not say. Here:
- **Pieces.** A sample the mask withholds ends a piece. Each maximal run of
  usable samples is segmented on its own, so no segment spans a gap.
- **Noise.** Noise is estimated jointly over every piece of one eye: one `σ`
  per axis for the eye, as the reference estimates one per recording. Each pass
  (§1.4) segments every piece, and the residuals are pooled.
- **Decoding.** The HMM is decoded per piece, and §1.5's previous direction
  restarts at `(0, 0)` at each piece's start.
- **Short pieces.** A piece shorter than 2 samples is left unlabelled, since
  the segmenter needs two.

**Time.** Within a piece, `t = sample index / fs_hz`. The reference takes
timestamps; this pipeline's samples are uniform.

**The cost is stated plainly.** On a recording with no withheld samples, this
is exactly the reference. On one with gaps, the pieces and the pooled noise
are this implementation's own. §5.2 checks exactness on real data where there
are no gaps.

## 4. What it emits, and what that reaches

The declared vocabulary is `{saccade, pso, fixation, pursuit}`, parent §3.1's
row. The reference's classes map as:

| Reference | Emitted |
|---|---|
| `FIXATION` | `fixation` |
| `SACCADE` | `saccade` |
| `PSO` | `pso` |
| `SMOOTH_PURSUIT` | `pursuit` |

- **The conjunction.** Its saccadic slice is `{saccade}`, so conjunction runs
  take `_conjunction_label`'s degenerate branch, as Nyström–Holmqvist's and
  REMoDNaV's do.
- **No minimum duration.** NSLR has none, so `_min_duration_samples` gives its
  conjunction the one-sample floor. It is the third detector with that floor,
  and the floor is still out of scope (REMoDNaV spec §9).
- **Every usable sample gets a label.** Unlike REMoDNaV, NSLR labels every
  sample of every piece of two or more samples, so `_insert_trace`'s
  `fixation` fill touches only withheld samples and pieces of one sample.
- **Pursuit is suspect on the current tasks.** The requester said on
  2026-09-26 that no current task moves a target. The paper names
  fixation-versus-pursuit as "the majority of total classification
  disagreement".
- **Not used below 1°.** The requester decided this on 2026-09-27.

  *What the shared fixture showed.* On `stepped_session`, NSLR finds the 2.5°
  and 3.25° steps and calls the 0.75° step fixation.
  - It does segment that step, exactly: 18 ms.
  - The step's mean speed, about 42 °/s, is 2.5 standard deviations below the
    published saccade class (log10 speed 2.33, standard deviation 0.28). It is
    just as far above fixation.
  - Fixation→PSO is forbidden, so the step cannot be PSO either.
  - The authors' own code gives identical labels on the same traces.

  *Why "below 1°".* The limit is speed, not amplitude, but small saccades are
  slow in any primate. So in practice NSLR is not relied on for movements under
  about 1°. This agrees with §1.4.

  *What the shared test does.* The planted-step invariant holds NSLR to the
  steps of 1° or more, and pins the miss.

### 4.1 Where it lives

- **Module:** `wl_preproc/eye/detect/nslr.py`. **Registry key:** `nslr`.
  **Params:** `NslrParams`, `DEFAULT_NSLR_PARAMS`.

  The oracle modules are imported by path in tests, never as a package named
  `nslr` (§7). This module is only ever `wl_preproc.eye.detect.nslr`, so the
  names do not collide.
- **Core functions mirror the reference's stages**, so §5.1 can compare each
  stage against the reference:
  - segment, as in §1.1;
  - fit, as in §1.3;
  - estimate noise over pieces, as in §1.4;
  - features, as in §1.5;
  - decode, as in §1.6.

  `detect_nslr` builds pieces from the mask, runs them, and returns runs.
- **Arithmetic mirrors the reference.** §1.4's stopping rule compares floats
  exactly, so a result one ULP off can change the pass count and, with it, the
  segmentation. So the implementation performs the reference's operations in
  the reference's order:
  - every `v**2` written as `v*v` — numpy's square, which is what the
    reference's arrays compute;
  - the same accumulation order;
  - scipy's `interp1d` for the residuals, as `Segmentation.__call__` uses;
  - numpy with the reference's shapes for the features and the decode.

  §5.1 is what proves it. Any residual difference is explained or fixed, never
  tolerated.
- **The segmentation loop is compiled with numba** (§7; the requester's choice
  on 2026-09-27):
  - it is `@numba.njit` without `fastmath`, so nothing is reassociated or fused;
  - live hypotheses are held in preallocated arrays, compacted in order on
    pruning, so "the first maximum" means what it means in the reference's
    list;
  - parents are an array from each hypothesis's start index to its parent's.

  **No transcendental function runs inside compiled code.** The likelihood
  constant `Σ log(1/(√(2π)σ))` and the split prior's value for each sample's
  `dt` are computed outside, in numpy, exactly as the reference computes them,
  and passed in, because a compiled `log` need not agree with numpy's to the
  last bit. The compiled loop sees only `+ − × ÷` and comparisons.

  A prototype of this loop produced split indices identical to a
  reference-exact pure-Python one on four traces, the longest 59,970 samples.
  It was 50–75 times faster.
- **Nothing is copied from the reference.** It is AGPL, and this
  implementation follows its behaviour, not its text. The segmentation
  hypothesis loop, like REMoDNaV's two-line on/offset loops, can only be
  written one way. Its citations to `slow_nslr.py` lines are the record of
  that.

## 5. Validation

### 5.1 Fidelity — is the algorithm right? (in CI)

The reference is loaded from pinned checkouts that CI fetches (§7). The check
is gated on `WLPP_NSLR_REFERENCE`, which CI sets, and it skips without it.

- **Inputs** are short synthetic traces, a few seconds each, because the
  reference is slow: fixations with drift and noise, saccades with and without
  a post-saccadic wobble, and a slow pursuit ramp. They are in degrees, at 500
  and 1000 Hz.
- **Expected: identical results at every stage.** That means:
  - the same split indices;
  - bit-identical endpoints;
  - the same pass count and final `σ`;
  - the same features;
  - the same Viterbi path;
  - the same label at every sample.
- **Nulls come first.** Three broken implementations must fail it:
  - a child hypothesis fitting its own intercept (no greedy continuity);
  - Viterbi without per-step emission normalisation;
  - the turn feature without its Fisher transform.

### 5.2 Real data (gated on `WLPP_OHDPI_REFERENCE`)

- **Exactness on real data.** Each eye's first run of at least 5,000 usable
  samples in the reference recording is classified by both ours and the
  reference. The labels must be identical. The p99→15° scale is REMoDNaV's §5.2
  scale.
- **Runtime, measured.** Each eye over the full recording (1,177,799 samples).
  The prediction from the design's prototype: the compiled segmenter took
  0.15–0.39 s per pass over 59,970 samples. With 5–6 passes plus the
  per-segment numpy stages, that is about a minute per eye. It is recorded, not
  gated.
- **Agreement with the registered detectors, reported.** Saccade counts and
  sample-level kappa against REMoDNaV and Nyström–Holmqvist on REMoDNaV's
  120,000-sample slice. These are recorded, not gated: there is no oracle for
  what they should be.

### 5.3 The paper's human coders (gated on `WLPP_ANDERSSON_DATA`)

The paper's Table 1 is the target, **computed the paper's way**, which differs
from REMoDNaV's `kappa()` script:
- Cohen's kappa per class, as class against the rest (the paper's "Binary"
  row equals its "Saccade" row, which is what dichotomising gives);
- **samples either coder labelled blink (code 5) or undefined (code 6)
  omitted**;
- **averaged over the two coders.** These are the paper's "Benchmarking
  methodology" words.

The steps:
1. **The harness is proved first.** The two coders against each other must
   reproduce Table 1's "Human" column within ±0.01: saccade 0.90, fixation
   0.81, smooth pursuit 0.79, PSO 0.73. The paper does not say which
   recordings it used. The harness tries REMoDNaV's 34 "data used in the
   article" files first, and then the dataset's full annotated set, and keeps
   the one that reproduces. It records which.
2. **Then ours is compared** with the "NSLR-HMM" column within ±0.05: saccade
   0.82, fixation 0.51, smooth pursuit 0.42, PSO 0.53.
3. **Nulls:** random labels at a fixed proportion must score kappa near zero.

**This check is in-sample, and says so.** The HMM's emissions were estimated
from this same labelled data (`nslr_hmm.py` 37; paper, "For NSLR, the feature
distributions of the classes were estimated from the human labeled data"). So
it tests the reimplementation against the paper, not NSLR's generalisation.
The paper's handling of missing samples in classification is unstated. Ours
splits pieces (§3), a difference the ±0.05 is expected to absorb, and if it
does not, that is reported, not tolerated.

### 5.4 Unit tests

Each stage gets its own tests, and every test that pins a rule gets a mutation
check that fails it:
1. **Segmentation:**
   - greedy continuity (a child's intercept equals its parent's predicted
     endpoint);
   - the pruning rule (a hypothesis less likely than the new branch is
     dropped, the winner never);
   - the backtracked splits cover `[0, n)`.
2. **The split prior**, against §1.2's formula.
3. **The continuous fit:** it is continuous, and exact on a noiseless
   piecewise-linear signal.
4. **Noise estimation:**
   - the stopping rule;
   - the guard (§6's `max_noise_passes`), with the pass count reported;
   - pooling across pieces.
5. **Features:** the `log10` clip, the Fisher transform, zero-speed segments,
   and the reset at each piece's start.
6. **The HMM:**
   - forbidden transitions are never taken;
   - the first emission is not normalised and later ones are;
   - the uniform start.
7. **Pieces:** a withheld sample splits segmentation, no run contains a
   withheld sample, and a one-sample piece is unlabelled.
8. **Review Focus inputs:** an empty or all-withheld trace, a trace shorter
   than one segment, a perfectly still trace, and 498.55 Hz.
9. **Registration:** it is registered with its vocabulary and defaults, and it
   meets every all-detector invariant in `tests/schema/test_detect_populate.py`
   (REMoDNaV's Task 5 rule: fix the fixture or the detector, never exempt it).

## 6. Parameters

One frozen dataclass, `NslrParams`, carried as `registry.Detector.defaults`.
Scalar fields only, so a paramset stores and restores them without nesting.

| Field | Value | Source |
|---|---|---|
| `structural_error_deg` | 0.1 | `slow_nslr.py` 174; paper |
| `saccade_amplitude_deg` | 3.0 | `slow_nslr.py` 159 |
| `slow_phase_duration_ms` | 300.0 | `slow_nslr.py` 160 (0.3 s) |
| `slow_phase_speed_deg_s` | 5.0 | `slow_nslr.py` 161 |
| `max_noise_passes` | 50 | not in the reference — a guard (below) |
| `fixation_log_speed_mean`, `…_var`, `fixation_turn_mean`, `…_var` | the published values | `nslr_hmm.py` 39 |
| `saccade_…` (the same four) | the published values | `nslr_hmm.py` 40 |
| `pso_…` (the same four) | the published values | `nslr_hmm.py` 41 |
| `pursuit_…` (the same four) | the published values | `nslr_hmm.py` 42 |

The published emission parameters are exact floats, so the plan copies them
verbatim from `nslr_hmm.py` 39–42.

- **The transition structure is not a parameter.** Which transitions are
  forbidden, and the half weight, are the paper's stated model, not estimates.
  They are code, citing `nslr_hmm.py` 53–62.
- **The guard is this implementation's own.** §1.4's loop ends only when a
  `σ` pair recurs exactly. That always happens for the reference on the data
  measured, but nothing guarantees it. `max_noise_passes` stops the loop there
  and keeps the last pass, as REMoDNaV's iteration cap does. The reference has
  no cap, so hitting it is reported (§5.4 item 4). A detector that silently
  never terminated would stall the daemon.
- **Nothing is tuned.** The emission parameters were fitted on human data.
  Re-estimating them for macaques would be a new paramset with its reason, and
  it is out of scope (§9).

## 7. Dependencies

**Runtime: numba, and nothing else new.**
- **numba.** It becomes a runtime dependency, which brings llvmlite with it
  (§4.1; the requester's choice on 2026-09-27).
  - **The floor.** `numba>=0.67`: 0.67.0 is the version the design's prototype
    ran on, with Python 3.11 and numpy 2.4.6, and with Python 3.13 and numpy
    2.5.3.
  - **Wheels.** 0.67.0 and llvmlite 0.49.0 publish wheels for CPython 3.11 and
    3.13 on macOS arm64 and Linux x86_64.
  - **Its own ceiling.** numba declares `numpy<2.6,>=1.22`, so this pipeline's
    numpy is capped wherever numba is, and a numpy release beyond that waits
    on numba. `wl.yaml`'s `why` says so.
- **numpy and scipy** are already runtime dependencies.
  `scipy.interpolate.interp1d` and `scipy.stats.multivariate_normal` are what
  the reference uses, and this implementation uses them too, for §4.1's
  arithmetic.

**Test time: the two AGPL-3.0 references, fetched, never installed.**
- **Why not install.** `nslr` cannot be installed as a package here: its C++
  does not build, and it has no pure-Python fallback (§2). PyPI's 0.0.5 has the
  root-intercept defect.
- **How they are loaded.** CI clones `gitlab.com/nslr/nslr` at `67d03f8` and
  `gitlab.com/nslr/nslr-hmm` at `3598fee`, and sets `WLPP_NSLR_REFERENCE` to
  their parent directory. A test helper loads `nslr/nslr/slow_nslr.py` by path
  and registers it as the module `nslr`, so that `nslr_hmm.py`'s `import nslr`
  resolves to it. Then it loads `nslr_hmm.py`, and applies §2's two numpy
  adapters to that module alone: a `float` that takes a one-element array's
  element, and an `np` whose `row_stack` is `np.vstack`. numpy itself is never
  patched.
- **Where they live.** Locally, the checkouts live outside the repository.
  Nothing from them is committed.
- **The fork.** `github.com/pupil-labs/nslr-hmm`'s `nslr_hmm.py` is
  byte-identical to GitLab's at `3598fee` (checked during design), so either
  source is the same code. GitLab is the authors'.

**Consequences:**
- `pyproject.toml`'s runtime dependencies gain `numba>=0.67`, with `wl.yaml`'s
  `third_party` entry and its `why`;
- `.github/workflows/` gains the clone step;
- `wl.yaml`'s `third_party` gains `nslr` and `nslr-hmm`, test-time only, with
  their `why`: AGPL-3.0, test-only by the requester's decision, the pinned
  commits, and why they are not installed;
- `wl-check` runs.

## 8. Open questions

1. **Human priors on macaque data.** The split prior's 3° saccade, 0.3 s slow
   phase and 5 °/s slow-phase speed, and every HMM emission parameter, come
   from human data. The paper itself names the fixation/pursuit overlap.

   **Unsupervised re-estimation is not the route.** The reference ships
   Baum–Welch and a robust Viterbi variant. But the paper reports that such
   re-estimation "tended to converge to rather bad solutions", and the
   reference's own demo calls it "not recommended for real data analysis".

   **The route that follows the authors is supervised.** Their parameters came
   from segments labelled by human coders: each class's sample mean and
   variance.
   - Hand-label macaque recordings.
   - Recompute the 16 emission values the same way.
   - Register them as a second parameter set beside the published one.

   The requester noted on 2026-09-27, when deciding §4's "not used below 1°", that NSLR
   may need this. It needs labelled monkey data, and it is out of scope here.

   *This item first said only that the reference offers re-estimation. It
   omitted its authors' warnings against it.*
2. **Runtime.** With the compiled loop, predicted at about a minute per eye
   for the 39-minute reference recording (§5.2 measures it).

   *This item first said plain Python, at a predicted 10 minutes per eye for a
   two-hour session. That prediction came from a quick test on unrealistically
   simple data. Measured on realistic synthetic data, plain Python was about 25
   minutes per eye for a two-hour session: some 94 hypotheses stay alive per
   sample, not a handful. Given the corrected number, the requester chose numba
   on 2026-09-27. The first choice was true when written.*
3. **Pursuit on the current tasks** (§4): suspect until a task moves a target.
4. **The one-sample conjunction floor**, now shared by three detectors.
5. **Exact-float termination across platforms.** §1.4's stopping rule is exact,
   so a platform whose `log` differs by an ULP could take a different number of
   passes. Fidelity is proved on CI's platform and this machine's; the guard
   bounds the worst case.

## 9. Out of scope

- Bayesian microsaccade detection and U'n'Eye.
- HMM re-estimation, and any tuning for macaque data.
- Compiling the inner loop.
- The conjunction's duration floor.

## 10. References

- Pekkanen, J., & Lappi, O. (2017). A new and general approach to signal
  denoising and eye movement classification based on segmented linear
  regression. *Scientific Reports*, 7, 17726.
  [10.1038/s41598-017-17983-x](https://doi.org/10.1038/s41598-017-17983-x).
  Full text: PMC5735175.
- `gitlab.com/nslr/nslr` at `67d03f8`, `nslr/slow_nslr.py`, AGPL-3.0, and
  `gitlab.com/nslr/nslr-hmm` at `3598fee`, `nslr_hmm.py`, AGPL-3.0. Read as a
  specification here, run as the test-time oracle in §5, and not transcribed.
- Andersson, R., Larsson, L., Holmqvist, K., Stridh, M., & Nyström, M.
  (2017). *Behavior Research Methods*, 49(2), 616–637.
  [10.3758/s13428-016-0738-9](https://doi.org/10.3758/s13428-016-0738-9). The
  dataset NSLR-HMM's emissions were fitted on and §5.3 reads (GPL-3.0; used as
  in REMoDNaV's §5.3).
