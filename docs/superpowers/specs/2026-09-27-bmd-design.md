# Bayesian microsaccade detection: the sixth detector, held to its authors' code

**Design spec, 2026-09-27.** Implements the Bayesian microsaccade detection
(BMD) row of design spec `2026-08-31-saccade-detection-design.md` §3.1, which
the parent lists as "reimplemented". It follows NSLR-HMM (merged `c7903dd`).
That spec, `2026-09-27-nslr-design.md`, is the model for this one: an exact,
sample-for-sample match with the authors' own code, established before any
production code is written.

**The paper was read, and so was the code.**
- **The paper.** Mihali, van Opheusden & Ma (2017), *Journal of Vision*
  17(1):13, [10.1167/17.1.13](https://doi.org/10.1167/17.1.13). It was read in
  full from Europe PMC (PMC5256468). The paper is CC BY-NC-ND 4.0, so its
  prose is not reproduced here; its equations are.
- **The code.** `github.com/basvanopheusden/BMD` at
  `1fab6355e57d6a7410c743ca2790d3efd502981a` (2019-12-12), `bmd.cpp` and
  `bmd.h`. The paper names this repository as its software. Every `bmd.cpp`
  line number below refers to that commit.

**The code has no licence file, and the requester has the authors'
permission to use it for testing.** GitHub reports no licence for the
repository, so by default all rights are reserved. That is why parent §3.2 says
BMD "has no usable oracle". On 2026-09-27 the requester stated that the
authors have given permission to use their code and data for testing. So,
exactly as with NSLR's AGPL reference:
- the tests build and run the authors' code;
- the pipeline never runs it;
- nothing from it (code, lookup table, example data) is committed, installed
  or shipped;
- and nothing of it is transcribed into this repository.

This supersedes parent §3.2's "validated against the paper's own
simulated-data claims instead", for BMD.

---

## 0. Why this detector, and why now

- **The requester chose it on 2026-09-27**, as the next hardware-free detector
  after NSLR.
- **It covers exactly what NSLR does not.** NSLR was ruled not for use below 1°
  that same day (NSLR spec §4). BMD is the one method in parent §3.1's table
  built for the sub-degree regime: a probabilistic model of fixation in which
  the eye is either drifting or making a microsaccade.
- **It was validated on the same class of instrument.** The paper's strongest
  validation used a dual-Purkinje-image tracker. This lab's OpenIris is one.

**Its reference turned out to be exactly reproducible, and that changed the
design.** Built from source on this machine (Apple clang 21, arm64, Boost 1.86
headers), `bmd` run on the repository's own example `x1.txt` (78,167 samples)
with the seed its stored output records (`1473448196`) reproduces all three of
the authors' stored files **byte for byte**: `output1.txt`, `params1.txt` and
`changepoints1.txt`. Run time is 10.2 s.

A throwaway Python/numba port, written during this design and following
`bmd.cpp` operation for operation (§1), then reproduced those files exactly:
all six iterations of estimated parameters and all 240 change-point samples.
It also reproduced the reference's output exactly at a 500 Hz rate on the
lab's own recording (§5.1). So BMD gets NSLR's standard of validation, not the
paper-claims fallback the parent planned.

---

## 1. The algorithm

### 1.1 The model (paper, Generative model)

The eye is in a hidden state C_t: 0 (drift) or 1 (microsaccade).
- **Durations.** State durations are independent, each Gamma-distributed with
  shape 2 (a hidden semi-Markov model). The per-sample rates are λ0 (drift)
  and λ1 (microsaccade).
- **Velocity.** Velocity is constant within a state run. It is redrawn at each
  change point, with a uniform direction and a generalized-gamma speed (shape
  d, scale σ). Drift has d0 = 1 fixed, and scale σ0. Microsaccades have d1 and
  σ1.
- **Position.** Position z accumulates the velocity plus Gaussian motor noise
  (σz per √sample).
- **Measurement.** The measurement x is z plus independent Gaussian noise (σx).
- **Isotropy.** Both noises are assumed isotropic, which is why §1.2 rescales
  the vertical axis.

### 1.2 Preprocessing (`preprocess_data.m` 12–20; paper, Preprocessing)

**The vertical axis is rescaled** by `sqrt(median(Δ²x²)) / sqrt(median(Δ²y²))`,
where Δ² is the second difference. The noise in both axes then matches.

**The origin convention.** The paper shifts each block so that the position
before its first sample is 0: x0 = x1 − ε, with ε = (1e-4, 1e-4)°, and x0 is
subtracted from every sample. `bmd.cpp` assumes this at both ends.
- **Before the first sample.** Its Kalman filter reads `zf[-1]` (line 182).
  Its displacement for a segment starting at sample 0 is measured from the
  origin (line 322). Its initial change points measure the first step from the
  origin (line 270).
- **After the last sample.** Its residual reads `x[T]` (line 714) for the last
  segment's slope.
- **Both reads fall outside the array**, and both read 0 on this machine. The
  authors' shipped `preprocess_data.m` never performs the shift.

This implementation performs the paper's shift in preprocessing. It treats the
position outside a trace as 0 explicitly, never by reading past an array:
- **At the start** that is the paper's own convention.
- **At the end** it reproduces what every published result was computed with.
  The paper instead states xT+1 = xT + ε. The effect is confined to the last
  segment's residual in the noise estimate (§1.4). It is reproduced so that
  the fidelity check holds, and it is recorded in §2.

### 1.3 Initialization (`bmd.cpp` 105–119, 266–284, 801–822)

1. **The speed parameters start from a separate generator.** d1, σ0 and σ1 are
   drawn uniformly from [1.1, 5], [1e-4, 0.005] and [0.005, 0.1], in that
   order. They come from the `params` object's own `mt19937_64`. That
   generator is never seeded, so it always starts at the standard default,
   5489. The draws are therefore the same every run. σz starts at 0.015 and σx
   at 0.02.
2. **The initial state sequence** marks as microsaccade every sample whose
   squared step is at or above the 99th percentile of all squared steps. The
   percentile is `sorted[floor(0.99·T)]`.
3. **The run seed.** The run's `mt19937_64` is seeded with the run seed.
4. **The noise is estimated from that one initial sequence** (§1.4), and the
   eye position is smoothed (§1.5).
5. **The chain's generator is seeded** with one draw from the run's generator.

### 1.4 Noise estimate (`bmd.cpp` 705–763)

For each state sequence in the current sample set:
1. **Residuals.** Fit x as a piecewise-linear function with breaks at the
   change points, and take the residual (lines 705–721).
2. **Autocovariance.** For lags s = 1…19, compute the mean squared residual
   difference.
3. **Linear fit.** Fit that as a line in s:
   - σz = √(slope/2);
   - σx = √(max(1e-5, intercept)/4);
   - an intercept within 1e-5 of 0 is set to 1e-5.

The medians over the sample set, taken as `sorted[n/2]`, become σz and σx.

### 1.5 Kalman smoother (`bmd.cpp` 174–195)

This is the steady-state Rauch–Tung–Striebel form:
- P = ½(√(σz⁴ + 4σz²σx²) − σz²);
- K = (P + σz²)/(P + σz² + σx²);
- Ks = P/(P + σz²).

The forward filter starts from 0 (§1.2), and the backward pass follows. The
sum of squared smoothed steps is kept for the likelihood's constant term.

### 1.6 Likelihood and prior (`bmd.cpp` 320–360; table 26–63)

**The log likelihood is −½·Σ|Δẑ|²/σz² over the whole trace, plus, for each
state run [t1, t2), three terms:**
- a state-specific constant;
- −(ln 2π + 2 ln σz)·Δt;
- −½(d+1) ln|Δz|², plus log A(α, d).

**The run's displacement Δz is z[t2−1] − z[t1−1].** The run carries the eye
from the sample before it to its own last sample. The scalar α is
½σz⁴/|Δz|² · (1/σ² + Δt/σz²).

**A(α, d) = ∫₀^∞ s^d e^(−αs²) I₀(s) ds.** It is looked up in a 1000 × 1000
table of log A over a log-spaced α grid and a d grid, with bilinear
interpolation. Outside per-d cutoffs, the code uses the two asymptotic forms
the paper's Figure A1 describes:
- small α: ¼α⁻¹ − d ln α − d ln 2;
- large α: −ln 2 − ½(d+1) ln α + ln(Γ((d+1)/2) + ¼Γ((d+3)/2)/α).

The prior is the Gamma(2, ·) duration density per run (lines 351–360).

### 1.7 The sampler (`bmd.cpp` 393–562, 589–643)

**Metropolis–Hastings over the change points.** The state sequence is stored as
change-point pairs t01/t10. It always starts in state 1 at sample 0 and always
ends in state 1 at T; §3.4 says what that means for the output.

**Each step does four things, in this order:**
1. **Draws a move type** from a discrete distribution weighted
   {1, 1, 1, 1, 2, 2}:
   - shift a 0→1 change point left or right;
   - shift a 1→0 change point right or left;
   - insert a drift sample inside a microsaccade run;
   - insert a microsaccade sample inside a drift run.
2. **Draws the index**, and for insertion moves the position.
3. **Computes the change** in log prior and log likelihood, with the
   proposal's Hastings term. A shift that would empty a run removes the pair
   instead, with its own Hastings term.
4. **Accepts** if −E < Δ, where E ~ Exponential(1).

**A sweep is T steps.** Each iteration runs 40 sweeps of burn-in, then 40
sweeps each followed by keeping the current state sequence as a sample.

**The random numbers are libc++'s, over `mt19937_64`, and are reproduced
exactly.** These are the algorithms of the libc++ headers this machine's
reference was built against:
- **`generate_canonical`.** One 64-bit draw, as a double, divided by 2⁶⁴.
- **`uniform_real_distribution`.** (b − a)·u + a.
- **`uniform_int_distribution`.** Mask the low w bits, where w =
  ⌈log₂(b − a + 1)⌉, and reject values ≥ b − a + 1. For a 64-bit engine the
  independent-bits engine takes exactly one draw per attempt.
- **`exponential_distribution`.** −ln(1 − u).
- **`discrete_distribution`.** An `upper_bound` over the normalised partial
  sums.

libstdc++ uses different algorithms for some of these, which is why §5.1
builds the reference against libc++.

### 1.8 Speed parameters (`bmd.cpp` 655–703)

**Microsaccade (d1, σ1).** For each of the 40 samples, a grid search takes the
first maximum of the log posterior over d = 1.1 + i·3.9/100 and σ =
10^(−4 + 4j/100), with i and j from 0 to 99. The medians over the samples
become d1 and σ1.

**Drift (σ0).** Then σ0 is found the same way over the σ grid alone.

### 1.9 The outer loop and the output (`bmd.cpp` 783–794; `BMD_vis.m` 66–67)

**Six iterations**, each of:
1. burn-in and sampling (§1.7);
2. d1 and σ1 (§1.8);
3. σ0;
4. σz and σx (§1.4);
5. a new smoothed eye position (§1.5).

**The output** is the final iteration's 40 samples, averaged into P(microsaccade)
per sample. It is thresholded at **P ≥ 0.5**, which is `BMD_vis.m`'s own
`Cm>=0.5` and the paper's "threshold at 0.5".

### 1.10 What the reference does that the paper does not say

Each of these is reproduced, because the fidelity check (§5.1) requires it,
and each is recorded here.
1. **Outside the trace, the position is 0.** Two out-of-bounds reads,
   `zf[-1]` and `x[T]`, both return 0. See §1.2 for what that means.
2. **The residual's `t01[i]` read past the last change point is never
   matched.** It reads a stale or uninitialized vector element. On a real
   state sequence that is either equal to the last change point or outside the
   last run, and it never changes the residual.
3. **The speed parameters' first values come from an unseeded generator** at
   its default seed (§1.3).
4. **A run always starts and ends in state 1** (§1.7).
5. **`long double` is `double`** on the platform that reproduces the authors'
   files. The test build patches the settings' `long double` to `double`
   (§2), so every platform reproduces them.
6. **The table's interpolation is not bilinear** (`bmd.cpp` 60–61). The
   corners (αᵢ, dⱼ₊₁) and (αᵢ₊₁, dⱼ) carry each other's weights. Measured
   2026-09-27 against a true bilinear, over the cells BMD reads:
   - median 0.017 in log A, maximum 1.2, the largest at small α, where large
     microsaccade segments land;
   - near d = 1, the drift column, at most 0.0065.

   Every published result was computed this way, and the fidelity check
   requires it. It is recorded as an open question (§8).
7. **A division by zero is IEEE, not an error.** A run whose smoothed
   displacement is exactly 0 divides by zero in the likelihood, and C++
   carries on with infinities and NaNs. The port is compiled with numba's
   numpy error model, never its Python one, which would raise. This was found
   on the reference recording's right eye, where the tracker repeats a
   position long enough for the smoother to reach an exact fixed point.

---

## 2. The reference, and how the tests obtain it

**What the tests do with it:**
- They clone `github.com/basvanopheusden/BMD` at `1fab635` and fetch the
  Boost headers.
- They build `bmd.cpp` with clang against libc++, patched in the smallest way
  that makes it testable. The patch is applied by a committed script, and no
  line of theirs is committed:
  - the seed is read from the environment, never the clock;
  - λ0, λ1 and the σ caps (§3.3) are compile-time constants;
  - the arrays get one element of 0 before and after, so the two reads in
    §1.10 item 1 are defined and return what they already return;
  - the settings' `long double` becomes `double` (below).
- The authors' own example files are read by path from that checkout.

**Their example data is itself a test.** A second, build-free check compares
this implementation against the authors' stored `changepoints1.txt` and
`params1.txt`. It needs only the clone.

**Why build, rather than use the committed `bmd` binary:** that binary is
x86_64 macOS only. This machine has no Rosetta, and CI runs Linux.

**Why libc++, and `double` for `long double`:** the stored example was
produced by a libc++ build with a 64-bit `long double`. A default Linux build
uses libstdc++'s distributions and 80-bit `long double`, so its random stream
would differ.

*This first said CI would force the 64-bit `long double` with clang's
`-mlong-double-64`. Corrected 2026-09-27, before any CI ran. On x86-64 that
flag changes the compiler's type, but glibc's `logl`, `powl` and `lgammal`
are built for the 80-bit one. Every maths call the source makes on a setting
would then hand the library a value it misreads. This was reasoned, not
observed; the patch avoids the question. Patching the source's six
`long double` settings to `double` means the same thing, with no mismatch. Measured the same day: built on x86-64 Ubuntu
24.04 with apt's clang 18, libc++ and Boost, the patched reference matched
this implementation exactly on the authors' example, on 1 kHz and 500 Hz
simulations, and on repeated positions.*

---

## 3. Inputs, and how BMD meets this pipeline

### 3.1 Large saccades come from Engbert–Kliegl (the requester, 2026-09-27)

BMD's model knows only drift and microsaccades. On task data it would put a
10° saccade in the microsaccade state. **The requester chose to analyse only
fixation stretches, found by removing Engbert–Kliegl's saccades.**
- **BMD runs EK itself**, calling `detect_engbert_kliegl` on the same gaze,
  velocity and mask, with EK parameters carried in BMD's own paramset. It does
  not read EK's stored rows. BMD's output is therefore a function of its own
  paramset alone, and it has no table-ordering dependency.
- **EK's `saccade` runs are stored as BMD's `saccade`.** EK's `microsaccade`
  runs are ignored, because BMD analyses those stretches itself.
- **The cost, stated to the requester when chosen:** BMD's saccades are EK's
  saccades. On `saccade`, agreement between EK and BMD is total by
  construction, and the consensus tables must say so (§4).

### 3.2 Fixation stretches, pooled per block (the requester, 2026-09-27)

**A fixation stretch** is a maximal run of samples that the validity mask
offers and that no gate saccade covers. Stretches shorter than
`min_stretch_ms` (default 200 ms, about 100 samples) are left unclaimed, and
the pipeline stores them as `fixation`. The default has no measured basis; it
is a starting point.

**The settings are pooled; each stretch has its own state sequence.** The
requester chose to estimate σz, σx, d1, σ0 and σ1 from stretches together, and
then sample each stretch's state sequence with the shared settings. The pool is
a **block of about 1 minute of fixation data**, which is the paper's own unit
("blocks of ∼1 min, which we process independently").
- **Building blocks.** Stretches are taken in time order. A block closes once
  its stretches total at least `block_s` (default 60 s) of samples. A final
  remainder shorter than half a block joins the block before it. An eye with
  less than one block's worth of fixation data is one block.
- **Seeds.** Block b's run generator is seeded with the paramset's `seed + b`.
  Blocks are independent, so they may run in parallel without changing any
  result.

*Decided at spec review, 2026-09-27. The design approved earlier the same day
said "once per eye"; the requester chose the paper's 1-minute blocks instead,
because they follow slow changes in tracker noise across a session and they
parallelise.* The probe that led to this ran on 2 minutes of the lab's
recording with the reference itself:
- **Whole recording, saccades included, settings capped (§3.3).** 92 events
  between saccades. σ0 sat at its floor. Only 46% of EK's saccade samples
  landed in the fast state.
- **Each stretch fully separate.** 433 events with a median of 6 ms (3
  samples) uncapped, and 244 with stretches of at least 1 s and the caps.
- **Engbert–Kliegl**, for comparison, found 146 microsaccades.

**How pooling works within a block, stage by stage:**
1. **Isotropy rescale (§1.2).** The medians are taken over every stretch's
   second differences together.
2. **The origin shift (§1.2).** Applied per stretch.
3. **Initialization (§1.3).** The speed parameters' draws happen once. Each
   stretch gets its own initial state sequence and its own chain. The chains'
   generators are seeded in stretch order, one draw each from the block's
   generator.
4. **Noise (§1.4).** Each lag's mean squared difference is summed over every
   stretch, and divided by the total pair count across stretches.
5. **Smoothing (§1.5).** Per stretch, with the shared σz and σx.
6. **Sampling (§1.7).** Per stretch, in stretch order, 40 + 40 sweeps of that
   stretch's own length.
7. **Speed parameters (§1.8).** For sample j, the log posterior is the sum
   over stretches, in stretch order, of each stretch's j-th sample. Then as
   §1.8.

**With one stretch in block 0, every stage is `bmd.cpp`'s**, and §5.1 checks
exactly that.

### 3.3 Two adaptations, both parameters

**The sampling rate.** The reference's λ0 = 0.004 and λ1 = 0.1 are per-sample
rates at 1 kHz. Under the code's Gamma(2, λ) prior (`bmd.cpp` 351–360) the
modes are 1/λ, 250 ms and 10 ms, and the medians about 420 ms and 17 ms. The
paper calls 260 ms and 10 ms the medians, which are nearer the modes. This implementation states them per second (k0 = 4/s, k1 =
100/s) and divides by the recording's rate. At 1 kHz that gives the
reference's values exactly. At the rig's 498.55 Hz it gives 0.00802 and 0.2006.

**The speed caps.** The authors' README, under "Additional assumptions", says
BMD "might need additional constraints ... on particular datasets". It
suggests:
- σ0 ≤ 0.0013 °/ms, which gives a mean drift speed of 1.5 °/s;
- σ1 ≤ 0.1 °/ms.

These are the defaults here, stated in °/s (1.3 and 100). They are converted
to per-sample values at the recording's rate. Grid points above a cap are
skipped. The σ grid's first point is always kept, so the "first maximum" rule
is unchanged.

The probe (§3.2) ran with and without the caps. Uncapped, the whole-recording
fit put σ0 at 0.25 °/sample, a drift speed of over 100 °/s.

### 3.4 From samples to labels

- **The two end runs are not detections.** A run's first and last state-1 runs
  are the representation's fixed ends (§1.7). They are excluded from
  P(microsaccade): in each sample, their samples count as state 0.
- **Labels.** Within a stretch, samples with P ≥ 0.5 are `microsaccade`, and
  the rest are `drift`.
- **Reliability.** A microsaccade run carries the mean of P over the run as its
  `reliability`. Drift and saccade runs carry none.
- **Seeds.** The run seed is a paramset field, so a given paramset on a given
  recording always gives the same labels.

### 3.5 Measurement from the take-off sample (the requester, 2026-09-27)

`measure` reads a run's amplitude as `gaze[stop−1] − gaze[start]`. **BMD's own
geometry is different: a state-1 run [t1, t2) moves the eye from t1−1 to
t2−1** (§1.6). Measured the shared way, a one-sample BMD microsaccade reads 0°,
and every run misses its take-off step. This is NSLR's defect at the other end
(NSLR spec §4).

**The requester's decision, at spec review:**
- a `Detector` field, `runs_start_after_takeoff`, is true for BMD alone;
- with it, per-eye microsaccade rows are measured over [start−1, stop) when
  sample start−1 is in the same stretch;
- BMD's saccade rows are EK's and are measured as EK's;
- the conjunction keeps the shared measurement, since a conjunction span does
  not start on a BMD change point (NSLR spec §4's reasoning for its landing
  rule).

---

## 4. What it emits, and what that reaches

**The vocabulary is `{saccade, microsaccade, drift}`.** This amends parent
§3.1's `microsaccade / drift`, because BMD carries EK's saccades (§3.1).
`drift` has its own kind in `KIND_OF`.

- **The conjunction.** BMD declares both halves of the amplitude split, so its
  conjunction's saccadic label is `classify` over the intersection's
  amplitude, as for Engbert–Kliegl and Otero-Millan.
- **The one-sample conjunction floor.** EK's parameters sit in BMD's paramset
  under distinct names (`gate_*`), so `_min_duration_samples` does not pick up
  EK's 6 samples. BMD's conjunction therefore gets the one-sample floor, like
  the four other ms-based detectors. The floor is still out of scope (NSLR
  spec §8 item 4).
- **Consensus.** EK↔BMD agreement on `saccade` is total by construction, and
  on `microsaccade` it is genuine. The rows record this.
- **Provisional.** BMD's rows stay marked provisional until a calibrated
  session exists (parent §3.2). The fidelity check proves the reimplementation
  is right; it cannot prove the model fits monkey data.

### 4.1 Where it lives

- **`wl_preproc/eye/detect/bmd_rng.py`.** The random-number layer: libc++'s
  `mt19937_64` and the four distributions the reference draws from, compiled.
  *Split from `bmd.py` on 2026-09-27, while building: it is pinned against
  libc++ on its own (§5.6), and nothing in it knows about eyes.*
- **`wl_preproc/eye/detect/bmd.py`.** The smoother, the likelihood, the
  numba-compiled sampler and grid search, the pooling, and `detect_bmd`, the
  registered `DetectFn`.
  - Registry key `bmd`.
  - No transcendental function is added inside the compiled loops beyond
    `log`, `exp` and `pow`. Those are the platform's own libm calls, which are
    the reference's too.
  - Γ and ln Γ are computed outside compiled code, through the C library.
- **`wl_preproc/eye/detect/bmd_table.py`.** This implementation's own table of
  log A(α, d), computed from the formula in §1.6 on the reference's grid, with
  per-d cutoffs chosen by the paper's criterion (Figure A1: total
  approximation error below 0.003).
  - It is computed once per process, deterministically, by fixed-order
    quadrature.
  - It is never read from the authors' file. The fidelity tests inject the
    authors' table instead (§5.1).

---

## 5. Validation

### 5.1 Fidelity: is the algorithm the authors'? (in CI)

**Nulls first.** Each of these deliberately broken implementations must fail
the check before it counts:
- chains seeded with the run seed itself, not with one draw from it;
- the speed settings started from the run's generator, not from the `params`
  object's own unseeded one.

*This list first named libstdc++'s `generate_canonical`, no smoothing, and a
dropped Hastings term. Those live inside compiled code, which a test cannot
substitute. The two above live in the Python that orchestrates it, and a
wrong random stream or a wrong start breaks every sample after it. The
repository's own random-number tests pin each distribution against libc++
separately (§5.6).*

**Exact, sample for sample.** Comparisons are exact, never within a tolerance.
Each takes the authors' table injected:
1. **The authors' example.** Against their stored `changepoints1.txt` and
   `params1.txt`. It needs no build.
2. **Simulated data, against the reference build.** At 1 kHz with the
   reference's constants, and at the rig's rate with §3.3's λ and caps, over
   several seeds. The data come from this repository's own simulator of the
   §1.1 model, not the authors' MATLAB.
3. **Real stretches, against the reference build.** Gap-free stretches of the
   lab's reference recording (gated on `WLPP_OHDPI_REFERENCE`), at the rig's
   498.55 Hz with the caps.
4. **Exactly repeated positions** (§1.10 item 7), against the reference build.

**The planted steps, on a drifting eye (the requester, 2026-09-27).** On the
still synthetic sessions every other detector is held to, BMD split the
0.75° planted step into three microsaccades and added a spurious one.

The cause is the fixture, not BMD. Between steps the synthetic eye is
perfectly still, and BMD's model assumes a drifting eye, as every real one is.
Measured on standalone traces with 0.03° measurement noise:
- with no drift, BMD found exactly the planted step in 3 of 8;
- with the reference recording's drift, in 8 of 8, as Engbert–Kliegl did in
  both.

Giving every session realistic drift broke two checks for existing
detectors:
- Otero-Millan fired about 40 times on one eye;
- a consensus test built on the glissade session failed.

**The requester chose to give BMD its own drifting check:**
- the five existing detectors keep the still sessions, unchanged;
- BMD is held to the planted steps on a drifting copy of the stepped
  session, and every planted step must be found within 5 samples;
- its detections at no planted step are counted and printed, not failed.

On pure synthetic drift over 60 s its false alarms were 0–1 a minute. On the
drifting stepped session it made one extra detection in 15.6 s.

**The drift is the reference recording's own.** BMD's noise estimate over
the reference recording's fixation stretches gave motor noise 0.05–0.06 px
per √frame against measurement noise 0.12–0.13 px, in three windows of each
eye. That is a ratio of about 0.4, applied to the fixture's own noise:
`synth/ohdpi.py::FIXATIONAL_DRIFT_PX_PER_SQRT_FRAME` = 0.4 × 6 = 2.4 px per
√frame. It is a new `SessionRecipe` field, `fixational_drift_px_per_sqrt_frame`,
defaulting to 0. Every existing fixture is byte-identical.

**Measured during design (throwaway prototype):**
- identical to the authors' stored files on their example;
- identical to the reference built with 500 Hz rates (λ0 = 0.008, λ1 = 0.2)
  on an 11,643-sample stretch of the reference recording (seed `987654321`).

Timings on this machine:

| | C++ reference | Prototype |
|---|---|---|
| Authors' example | 10.2 s | 12.9 s |
| Lab stretch | 14.0 s | 18.3 s |

### 5.2 Pooling (unit)

With one stretch in block 0, pooled equals the reference, and §5.1 covers
that. With several stretches, the pooled noise estimate and grid search each
equal a direct computation over the concatenated sums. A mutation that pools
per stretch fails.

Blocks: the block boundaries follow §3.2's rule, including the remainder
joining the block before it, and block b's generator is seeded with `seed +
b`. A block run on its own with its seed gives the labels it gets inside
`detect_bmd`. That independence is what would let blocks run in parallel;
this build runs them in order, and parallelises the grid search instead.

### 5.3 This implementation's table against the authors'

This measures the largest |Δ log A| over the grid, and the cutoffs. It then
runs §5.1's cases with this implementation's table in place of the authors':
- **The labels must agree** on at least 99.9% of samples, on a gap-free
  stretch of each eye of the reference recording.

**Measured 2026-09-27.**
- **The table itself.** Median |Δ log A| is 3.4e-7. Where BMD reads, 99.999%
  of cells are identical once this table is printed at the authors' seven
  significant digits. The cutoffs are the same grid points for every column
  from d = 0.99. One cell in use, (523, 612), is wrong in the authors' file
  by 0.0103; quadrature agrees with this table there.
- **The output.** With this table in place of the authors', the
  probabilities were **identical** at every sample on the authors' example
  and on a lab stretch.

### 5.4 Real data (gated on `WLPP_OHDPI_REFERENCE`; recorded, not gated)

On the lab's reference recording, per eye, this records:
- runtime;
- microsaccades per second;
- P's distribution;
- agreement with EK's microsaccades.

The recording is uncalibrated (a p99→15° scale), so these numbers describe the
pipeline, not the eye.

**Measured 2026-09-27, the full recording (1,177,799 samples):**

| | Runtime | Microsaccades (first 120,000 samples) | Microsaccade κ vs EK |
|---|---|---|---|
| Left eye | 261 s | 1.35/s | 0.569 |
| Right eye | 280 s | 1.43/s | 0.569 |

That is about 14 minutes per eye for a two-hour session.

### 5.5 The paper's simulated-data claims (recorded)

On simulated data at the paper's parameters (σ0 = 0.3 °/s, d1 = 4.4, σ1 = 30
°/s; 20,000 samples at 1 kHz), this records BMD's hit and false-alarm rates
beside this repository's EK. The paper reports these as figures, so only the
direction of its headline claim is gated: at high measurement noise, BMD's hit
rate exceeds EK's.

Measured 2026-09-27, with motor noise 0.003:

| Measurement noise | BMD hit | EK hit |
|---|---|---|
| 0.03 | 0.995 | 0.766 |
| 0.06 | 0.979 | 0.103 |

At 0.01 both are near 0.99. The README gives 0.01° as BMD's floor, and below
it BMD's hit rate falls: 0.68 at 0.005.

### 5.6 Unit tests

- The generator: `mt19937_64` against the standard's 10,000th output.
- Each distribution: against its libc++ algorithm on known draws.
- The table: against adaptive quadrature at six cells; the cutoffs against
  the paper's criterion; the lookup's crosswise interpolation and both
  asymptotic branches.
- `detect_bmd`: the gate, the stretches, the minimum length, the blocks and
  their seeds, the isotropy rescale, the origin, the end runs excluded, the
  threshold, and the reliability.
- The all-detector invariants in `tests/schema/`.

*This list first also named the smoother, the likelihood and each move's
Hastings term, each against a direct computation. They are held by §5.1
instead (2026-09-27). §5.1 compares every change point of every sample, so an
error in any of the three fails it. A second transcription of the same
reference, written to check the first, would share its misreadings.*

---

## 6. Parameters

| Field | Default | Source |
|---|---|---|
| `seed` | 1473448196 | the authors' stored example's seed; block b uses `seed + b` |
| `drift_rate_per_s` (k0) | 4.0 | `bmd.h` 28, λ0 = 0.004 at 1 kHz |
| `microsaccade_rate_per_s` (k1) | 100.0 | `bmd.h` 28, λ1 = 0.1 at 1 kHz |
| `drift_scale_cap_deg_s` | 1.3 | README, "Additional assumptions" |
| `microsaccade_scale_cap_deg_s` | 100.0 | README, "Additional assumptions" |
| `burn_in_sweeps` | 40 | `bmd.cpp` 786 |
| `samples` | 40 | `bmd.cpp` 787 |
| `iterations` | 6 | `bmd.cpp` 784 |
| `threshold` | 0.5 | `BMD_vis.m` 67 |
| `min_stretch_ms` | 200 | this implementation's; no measured basis |
| `block_s` | 60 | the paper's "blocks of ∼1 min" (Preprocessing) |
| `gate_lambda`, `gate_min_duration_samples` | EK's defaults | EK's own paramset |

The grids (§1.8), the initial ranges (§1.3), the noise lags (§1.4) and the
table's axes are code, citing their lines. They are the model's structure, not
settings.

---

## 7. Dependencies

- **No new runtime dependency.** numba and scipy are already declared.
- **Test-time only:**
  - the authors' repository at `1fab635`;
  - Boost headers;
  - clang with libc++, in CI.

  `wl.yaml` records the repository under `third_party` with its SHA in the
  `why`, and states the permission and that it is never installed, as it does
  for `nslr`.
- **CI** gains a step that clones the repository, installs clang, libc++ and
  the Boost headers, applies the patch script and builds.

---

## 8. Open questions

1. **Runtime.** The reference ran at roughly real time on the lab's
   recording: 2 minutes of one eye took 57–145 s depending on the caps.
   - **Speed-ups that keep exactness.** The grid search dominates, and its 40
     samples are independent. Blocks are independent too (§3.2). Both
     parallelise across cores without changing a single result.
   - **The decision.** Whether BMD runs nightly or only on demand is the
     requester's, once this implementation's runtime is measured.

   *Measured 2026-09-27, with the grid search in parallel: 261 s and 280 s per
   eye on the 39-minute reference recording (§5.4). That is about 14 minutes
   per eye for a two-hour session.*
2. *Per eye or per block: decided at spec review, per 1-minute block (§3.2).*
3. **Stretch length and saccade tails.** `min_stretch_ms` has no measured
   basis. Nothing pads EK's saccades, so a post-saccadic oscillation just after
   one falls inside a stretch.
4. *The measurement rule: decided at spec review, from the take-off sample
   (§3.5).*
5. **A calibrated session**, which is what would lift the provisional marking
   (parent §3.2). It would also give degrees for which the §3.3 caps mean what
   the authors meant.
6. **The reference's interpolation** (§1.10 item 6). Should BMD ever
   interpolate its table correctly, as a second parameter set beside the
   faithful one? That would trade exactness against the authors' code for a
   likelihood without the defect.
7. **Otero-Millan and a drifting eye** (§5.1). On a synthetic session with the
   reference recording's drift it reported about 40 microsaccades on one eye.
   On pure synthetic drift it reported none, and on the real recording its
   microsaccade rate is within its band. Why is unmeasured. This is a finding
   about an existing detector, recorded here because this work found it.
8. **BMD's one-sample conjunction rows.** On the drifting session one BMD
   conjunction microsaccade was one sample long, and so stored at 0.0°: the
   shared one-sample floor (NSLR spec §8 item 4), now met by BMD too.
9. **Small steps can still split on a drifting eye.** Drift reduces BMD's
   splitting of a small step; it does not remove it. On one drifting seed the
   0.75° step came back as three detections.

## 9. Out of scope

- The parallel-tempering variant (`bmd_pt.cpp`) and "BMD reduced plus
  threshold".
- The paper's binocular extension (Discussion).
- Any tuning for macaque data.
- U'n'Eye.
- Storing each event's direction and start/end positions. The requester chose
  on 2026-09-27 to add these for every detector right after BMD, on their
  own branch.

## 10. References

- Mihali, A., van Opheusden, B. & Ma, W. J. (2017). Bayesian microsaccade
  detection. *Journal of Vision* 17(1):13.
  [10.1167/17.1.13](https://doi.org/10.1167/17.1.13)
- `github.com/basvanopheusden/BMD` at
  `1fab6355e57d6a7410c743ca2790d3efd502981a`. There is no licence file. It is
  used for testing under the authors' permission, as stated by the requester
  on 2026-09-27.
- Engbert, R. & Kliegl, R. (2003). Microsaccades uncover the orientation of
  covert attention. *Vision Research* 43:1035–1045.
