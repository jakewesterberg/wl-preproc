# REMoDNaV: the first detector with a runnable oracle, and the first that says `pursuit`

**Design spec, 2026-09-26.** Implements the fifth row of design spec
`2026-08-31-saccade-detection-design.md` §3.1's table — REMoDNaV,
`saccade / pso / pursuit / fixation`, "reimplemented".

It depends only on what is merged:
- `registry.Detector` carrying its own defaults (`19daf07`);
- the per-kind conjunction (`76a8199`);
- the kind map living in `eye/detect/labels.py` (`fbfb19f`), where `pursuit`
  is already its own kind.

**The paper was read, and so was the code — and where they differ, this
spec says which it follows and why (§2).**
- The paper: Dar, Wagner & Hanke (2021), *Behavior Research Methods* 53(1),
  399–414, [10.3758/s13428-020-01428-x](https://doi.org/10.3758/s13428-020-01428-x),
  read in full from its PubMed Central text (PMC7880959).
- The code: `remodnav` 1.1.2's `clf.py` (MIT), as installed in this
  project's `.venv`. Every `clf.py` line number below refers to that file.

Both are needed because the paper describes saccade classification as
"identical to the NH algorithm, only the data context and metrics for
determining the velocity thresholds differ". The remaining detail lives only
in the code, and the code is what produced the paper's published validation
numbers.

---

## 0. Why this detector, and why now

**It is the only unwritten detector with a runnable oracle.** Parent §11
item 5: "REMoDNaV (MIT, on PyPI) remains a genuine runnable oracle".
Otero-Millan's and BMD's references cannot be run from this suite, and NSLR's
carry licence problems (parent §3.2). The situation with Nyström–Holmqvist
was weaker: REMoDNaV is a *modification* of that method, so it could serve
only as a count check (`test_remodnav_finds_a_comparable_number_of_saccades`,
within a factor of two). Here the oracle implements the same algorithm. That
lets §5 separate "is the reimplementation right" from "what does this
repository's shared preprocessing change". Parent §3.2 says that separation
cannot otherwise be made: "a buggy reimplementation is indistinguishable from
a genuine detector disagreement".

**It is a saccade detector first.** The requester said on 2026-09-19 that
glissades are peripheral. REMoDNaV's contribution is robust saccade detection
under temporally varying noise:
- it chunks the recording between its largest saccades;
- it re-estimates thresholds locally within each chunk.

That is the part this rig's long sessions most need.

**It is the first producer of `pursuit`.** `KIND_OF` already gives `pursuit`
its own kind, and the conjunction intersects it, but only fixture detectors
have exercised that path — as `pso` had been until Nyström–Holmqvist.

## 1. The algorithm

Organised as the paper's Figure 1 organises it: E1 thresholds, E2 chunking,
E3 saccades and PSOs, E4 fixation and pursuit.

### 1.1 The adaptive threshold (paper eq. 3; `clf.py` 285–332)

```
PT_n = median(V_{n-1}) + F · MAD(V_{n-1})      V_{n-1}: speeds below PT_{n-1}
```

- **MAD is σ-scaled.** `clf.py` 11 and 311 call `statsmodels.robust.scale.mad`,
  whose default divides the median absolute deviation by 0.6745. Measured
  here: 1.0 on unit-normal noise. The paper says "MAD" and does not say
  which. **A raw MAD would put every threshold at 0.6745 of the oracle's**,
  which is the kind of defect that looks like a finding.
- **Two thresholds from one iteration.**
  - Peak: `F = 2 · noise_factor`.
  - On/offset: `median + noise_factor · MAD`, taken from the last iteration's
    median and MAD (`clf.py` 312, 332).

  Table 1 states it: "peak velocity threshold is twice the onset velocity".
- **Start at 300 °/s; stop when `|PT_n − PT_{n−1}| ≤ 1 °/s` or after 30
  iterations, keeping the last value** (`clf.py` 305, 317). The cap is the
  code's. The paper states none, the same gap the Nyström–Holmqvist spec's
  §9 item 2 flagged, and here the code answers it.
- **No speeds below the current threshold means no threshold, and nothing is
  detected in that window.** In the code an empty set yields NaN, which the
  zero guard at `clf.py` 320–324 does not catch. The NaN then fails every
  comparison, with the same outcome. This implementation says so explicitly
  rather than inheriting it by accident.

### 1.2 Chunking: the major saccades (paper, "Time series chunking"; `clf.py` 346–368, 389–512)

1. One threshold (§1.1) over the whole recording, computed on the
   **candidate speed**: the speed of 50 ms median-filtered positions (§3).
2. Candidates are the maximal runs of candidate speed above that peak
   threshold. They are taken in order of the run's summed speed, largest
   first (`clf.py` 423–424): "longer and faster goes first".
3. For each candidate, thresholds are recomputed on the **primary speed**
   within a 1 s context window (`clf.py` 433–447):

   ```
   win_start = max(0, run_start − L/2)
   win_end   = min(n, run_end + L − (run_start − win_start))
   ```

   The paper calls this "centered on the peak velocity". The code anchors it
   on the run's start (§2).
4. Onset and offset are searched on the primary speed with that window's
   on/offset threshold (§1.3).
5. The candidate is rejected if it is:
   - shorter than `min_saccade_duration`;
   - missing any position;
   - within `min_intersaccade_duration` of a saccade or PSO already accepted
     (`clf.py` 462–474).
6. An accepted candidate is a saccade, and §1.4 looks for its PSO.
7. The loop stops once the number accepted exceeds
   `max_initial_saccade_freq` × the recording's duration (`clf.py` 508–512).

### 1.3 Onset and offset, as coded — and they are not symmetric

**Offset** (`clf.py` 101–112) walks forward from the run's end while the speed
is above threshold *or* the next sample is lower. It lands on the first local
minimum at or below threshold, which is the paper's "following velocity
minim[um]".

**Onset** (`clf.py` 86–98) walks backward from the run's start while the speed
is above threshold *or* `speed[i] <= speed[i−1]`. Once below threshold it
stops at the first sample whose predecessor is *lower*. So on a monotone
approach it stops at the first sub-threshold sample, not at the preceding
minimum.

Worked example: speeds `[5, 3, 4, 10, 50, 100]`, threshold 20, run starting
at index 4. The onset comes out at 3; the preceding minimum is at 1. The
paper's prose ("the immediately preceding and the following velocity
minima") describes a symmetric rule, and the code's onset is not that rule.

**This implementation follows the code** (§2 says why). A unit test pins the
example above, so the asymmetry is a visible choice rather than a surprise.

### 1.4 Post-saccadic oscillations (`clf.py` 115–138, 486–506)

- **Window:** `max_pso_duration` after the saccade's offset, on the primary
  speed.
- **Kind:** high-velocity if any run in the window exceeds the peak threshold;
  otherwise low-velocity if any exceeds the on/offset threshold; otherwise
  there is no PSO. **Both emit `pso`.** The paper collapsed them for its own
  validation ("All high- and low-velocity PSOs classified by REMoDNaV were
  therefore collapsed into a single PSO category"), and this vocabulary has
  one word for both.
- **End:** the §1.3 offset search, starting from the end of the *last* run in
  the window, using the on/offset threshold and bounded by the window.
- **Rejected:**
  - if any primary speed in it is missing (`clf.py` 131–133);
  - if its amplitude is at least its saccade's (`clf.py` 496). Amplitude is
    the distance between an event's first and last usable positions
    (`clf.py` 275–280).
- An accepted PSO joins the proximity mask, as a saccade does.

### 1.5 Intersaccadic periods (`clf.py` 514–670)

1. **The periods:**
   - from the recording's start to the first major saccade;
   - from each major saccade's end (or its PSO's) to the next one's start;
   - from the last to the recording's end.
2. Each period splits into maximal runs of usable positions (`clf.py`
   581–605). No event ever spans a gap.
3. **A piece longer than `2·min_intersaccade + min_saccade + max_pso`** (120 ms
   at the defaults; `clf.py` 627–629) is searched for further saccades, using
   §1.2's rules with these differences:
   - thresholds come from the *whole piece*, on the primary speed, with no
     context window;
   - candidates are runs of *primary* speed above the piece's peak threshold;
   - the proximity mask starts empty;
   - a saccade or PSO within `min_intersaccade` of either piece edge is
     rejected, and a rejected saccade takes its PSO with it (`clf.py` 639–649).
4. **If it finds any, the intervals between them are classified the same way,
   recursively** (`clf.py` 655–666). This is how the method finds a small
   saccade between two large ones, with a threshold from its own local noise
   rather than the recording's. `clf.py` 612 accepts a `saccade_detection`
   argument that its body never reads, so the recursion continues until a
   piece yields no saccade.
5. A piece with no saccade, or too short to search, goes to §1.6.

The §1.2 maximum-frequency stop also runs inside pieces, but its denominator
is the whole recording's length (`clf.py` 509), so it effectively never fires
there. It is reproduced, and nothing relies on it.

### 1.6 Fixation or pursuit (paper, "Pursuit and fixation classification"; `clf.py` 672–782)

- **A piece shorter than `min_fixation_duration` emits nothing.** Storage
  paints it `fixation` anyway (§4).
- **Positions are low-passed**, then differentiated:
  - Butterworth, order 5 (`clf.py` 680 — the paper states no order);
  - cutoff 4 Hz;
  - zero-phase `filtfilt` with Gustafsson initial conditions (`clf.py`
    691–692).

  The paper says "velocities are low-pass filtered"; the code filters
  positions (§2).
- **Pursuit candidates** are runs above `pursuit_velthresh`, largest summed
  speed first. Each gets the §1.3 onset and offset searches with
  `pursuit_velthresh` as the threshold. Those shorter than
  `min_pursuit_duration` are dropped; the survivors are marked.
- **The marked and unmarked stretches are then tidied** (`clf.py` 728–764):
  1. Stretches shorter than their type's minimum are dropped (the code's
     comparison is `last − first ≥ min` over inclusive indices — §2).
  2. Neighbours of the same type merge.
  3. A boundary between different types falls at the midpoint of the gap
     between them.
  4. The first stretch starts at the piece's start and the last ends at its
     end.
  5. If nothing survives, the whole piece is one fixation.

## 2. Where the paper and the code disagree, and which this follows

**The rule: follow the code where it defines behaviour.** The paper's
published validation — its Tables 2, 3 and 4 — was produced by the authors'
code, and the prose describes it. 1.1.2 is the installed release of that
code; whether anything between the paper's version and it moved those
numbers is exactly what §5.3's harness check would show. Three exceptions:
- a sampling-rate artefact (§2.1);
- a defect that changes output on inputs this pipeline produces (§2.2);
- a step this repository already does once, for every detector (§3).

| Point | Paper | Code (1.1.2) | This implementation |
|---|---|---|---|
| MAD | "MAD" | σ-scaled, ÷0.6745 | code |
| iteration cap | none | 30, keep last | code |
| saccade onset | "preceding ... minima" | first sub-threshold sample (§1.3) | code |
| context window | "centered on the peak velocity" | 1 s, anchored on the run's start | code |
| low-pass target | velocities | positions, then differentiate | code |
| Butterworth order | unstated | 5 | code |
| fixation/pursuit minimum | a duration | `last − first ≥ min`, one sample longer | code |
| `max_vel` | "replaced by this set value" | replaced by the *previous* velocity (`clf.py` 885–898) | neither — the validity mask excludes those samples (§3) |
| duration → samples | seconds | `int()` truncation (`clf.py` 259–270) | **rounded** (§2.1) |
| a run starting at sample 0 | — | dropped by `if sac_on:` (`clf.py` 77) | **kept** (§2.2) |

### 2.1 Rounding, not truncation

At 500 Hz every default is a whole number of samples, and rounding and
truncation agree. **At this rig's measured 498.55 Hz they do not.**
Truncation turns the 10 ms minimum saccade into 4 samples (8.0 ms) instead of
5, and each 40 ms minimum into 19 samples instead of 20. That is a different
minimum saccade on the actual instrument, caused by nothing but float
arithmetic.

Nyström–Holmqvist already rounds (`nystrom_holmqvist.py`'s `min_fixation`).
The fidelity check (§5.1) runs at exactly 500 and 1000 Hz, where the two
agree, so this costs it nothing.

### 2.2 The sample-0 defect

`find_peaks` closes a run still open at the window's end with `if sac_on:`,
which is false when the run began at index 0. So a window that starts above
threshold and never drops below loses its only run.
- **In a PSO window this is unreachable**: the window starts at the
  saccade's offset, which is at or below threshold by construction, and a
  run starts only above it.
- **In an intersaccadic piece it is reachable**: a piece that begins at a
  gap's edge mid-movement.

The same branch has a second slip: when it does close an open run, it ends
it at `len(vels) − 1`, one sample short of the window's end. Both are fixed
here, pinned by unit tests, and kept out of the fidelity fixture. Both were
confirmed against the installed oracle rather than read off the source:
`find_peaks([30, 40, 50], 20)` returns no run, and `find_peaks([1, 40, 50],
20)` returns `[1, 2)`.

## 3. Preprocessing: what is shared, what is kept, what is dropped

**The ruling agreed on 2026-09-26** (the requester's "go ahead" to the
proposal): saccades and PSOs use the shared velocity estimate, and the 4 Hz
low-pass stays inside the detector. Reading the code refined that into four
decisions.

**1. The primary speed is the shared estimator's** — `|velocity_deg_s|` from
`eye/detect/velocity.py` — in place of a 19 ms, order-2 Savitzky–Golay filter
followed by a two-point difference. This is the ruling itself, and
Nyström–Holmqvist's §2 made the same one. That detector's measured glissade
rate (0.40 left, 0.42 right, against the reference recording) shows the
shared estimator does not erase short events.

**2. The candidate speed is kept, and differentiated by the shared estimator.**
The code computes a *second* velocity, from 50 ms median-filtered positions,
and uses it only to locate the major saccades that chunk the recording.
Table 1: "for initial data chunking only". The median filter is the method's
device for "emphasiz[ing] large amplitude saccades" (paper, Preprocessing)
against sporadic noise, which is REMoDNaV's reason to exist. Without it,
chunking would pick the noisiest spikes rather than the largest saccades. The
differentiator is still the shared one, so this is the method's own
segmentation step, not a second velocity estimator. **This goes beyond what
was proposed in conversation and is flagged for review.**
- **The window** is 50 ms rounded to a sample count, plus one if that count
  is even, so the filter is centred: 25 at 500 Hz, 51 at 1000 Hz.
- **Unusable samples:** a median-filtered sample whose window touches an
  unusable sample is itself unusable, so blink positions never reach the
  candidate speed. This replaces the code's handling (`clf.py` 875–883),
  which runs the median filter over positions containing NaN.

**3. The 4 Hz low-pass is kept**, as agreed: it is applied to positions and
differentiated with the shared estimator.

**4. Three preprocessing steps are dropped.**
- **The Stampe spike filter** (`clf.py` 141–167, 844). It is a private
  position smoother run ahead of every velocity, which is exactly what parent
  §3.2's "one shared velocity estimator across all seven" excludes. The
  method's own defences against spikes — robust statistics and the
  median-filtered candidate speed — stay.
- **Dilation around lost data, and `max_vel`.** The validity mask already
  does both:
  - `DEFAULT_VALIDITY_PARAMS.max_speed_deg_s` is 1000.0, the paper's
    `max_vel` exactly;
  - `dilate_samples` is 5, which is 10 ms at 500 Hz, the paper's `dilate_nan`.

  Unusable samples reach the algorithm as missing, which is REMoDNaV's own
  representation of signal loss. The method's handling of missing data
  (§1.4's PSO rejection, §1.2's missing-position rejection, §1.5's split into
  pieces) does the rest. The paper's `min_blink_duration` (dilate only
  losses longer than 20 ms) has no counterpart, because the mask dilates
  every invalid epoch.

That answers Nyström–Holmqvist's §9 item 4 the same way for this detector:
there are not two noise definitions for one trace, because **the mask is the
only one**.

**The cost is stated plainly: this detector's output will not match the
oracle's end to end.** §5 separates what that costs from whether the
algorithm is right.

## 4. What it emits, and what that reaches

The declared vocabulary is `{saccade, pso, fixation, pursuit}`, parent
§3.1's own row, unchanged. The code's labels map as follows:

| Code | Emitted |
|---|---|
| `SACC`, `ISAC` | `saccade` |
| `HPSO`, `LPSO`, `IHPS`, `ILPS` | `pso` |
| `FIXA` | `fixation` |
| `PURS` | `pursuit` |

**`pursuit` reaches production for the first time.** `KIND_OF` maps it to its
own kind, outside `NOT_INTERSECTED`. So a stretch both eyes call pursuit
survives into the conjunction as `pursuit`. The path is built, and tested
with fixture detectors (conjunction spec §1). This is its first real
producer, exactly as Nyström–Holmqvist was for `pso`.

**Its saccadic slice is `{saccade}`**, so its conjunction runs take
`_conjunction_label`'s degenerate branch, like Nyström–Holmqvist's. There is
no `microsaccade`, because REMoDNaV has no amplitude split.

**Samples it does not label are painted `fixation` by `_insert_trace`:**
short pieces, and events it rejected. This is the same as for
Nyström–Holmqvist.

**The conjunction's duration floor is inherited, not fixed.**
`_min_duration_samples` reads `min_duration_samples` off a detector's params
with a `getattr` default of 1. This detector states its durations in
milliseconds, so its conjunction admits one-sample binocular events — the
trap CHECKPOINT already records for Nyström–Holmqvist. It is a cross-detector
decision and is out of scope here (§9). It is recorded so the plan does not
paper over it.

### 4.1 Where it lives

- **Module:** `wl_preproc/eye/detect/remodnav.py`. **Registry key:**
  `remodnav`. **Params:** `RemodnavParams`, `DEFAULT_REMODNAV_PARAMS`.

  The other detectors are named for their authors. This one is named for
  the method, as parent §3.1 names it and as everyone who cites it does. The
  module shares a name with the PyPI oracle, but inside the package it is
  only ever imported as `wl_preproc.eye.detect.remodnav`, and the tests'
  `pytest.importorskip("remodnav")` resolves to the top-level package.
- **Two layers:**
  - `detect_remodnav(gaze_deg, velocity_deg_s, available, fs_hz, params)` is
    the registered `DetectFn`. It builds §3's signals — primary speed,
    candidate speed and positions, each missing wherever the sample is
    unusable — and hands them to the core.
  - The core classifies from those signals and a differentiator (§1.6), and
    knows nothing about the validity mask or the shared estimator.

  §5.1 drives the core directly.
- **No oracle code is copied.** The reimplementation follows `clf.py`'s
  behaviour and cites its lines. Where the oracle loops in Python per
  sample, this implementation may vectorise, provided §5.1 still holds.

## 5. Validation: three checks, each answering a different question

### 5.1 Fidelity — is the algorithm right? (in CI; needs no recording)

**The detector's core takes its signals as inputs**: primary speed,
candidate speed, positions, and a differentiator. Production passes the
shared ones (§3). The fidelity test instead passes the oracle's *own*
preprocessed signals — the `vel`, `med_vel`, `x` and `y` fields of
`EyegazeClassifier.preproc()`'s output — together with the oracle's
two-point differentiator for §1.6. It then runs both classifiers and compares
their outputs.

**Inputs are synthetic traces generated in the test**, in pure numpy with no
DataJoint, so the 3.13 cross-check runs them too. They contain:
- fixations with drift and noise;
- saccades of several amplitudes, with and without a post-saccadic wobble;
- a slow pursuit ramp;
- a stretch of missing samples;

at 500 and 1000 Hz (§2.1).

**The expected result is identical labels at every sample.** The fixture is
built not to trigger the §2 exceptions. Any residual difference is a defect
in one implementation or the other until explained. The explanation goes into
§2, or the code is fixed. The tolerance is never widened to absorb a
difference.

**The null comes first.** Two deliberately broken cores must *fail* the
check, proving it can see the class of defect it exists to catch:
- onset and offset searched with the peak threshold instead of the on/offset
  one;
- a raw MAD instead of the σ-scaled one.

### 5.2 Deviation — what does the shared preprocessing cost? (gated on `WLPP_OHDPI_REFERENCE`)

The comparison runs end to end on the reference recording, with the same
p99→15° scale and leading 120,000-sample slice as Nyström–Holmqvist's oracle
check. Ours runs with the shared estimator; the oracle runs its own
preprocessing, given degrees with `px2deg = 1`.

**Reported, per eye and per kind:**
- event counts;
- sample-level Cohen's kappa of each label against the rest.

**Asserted loosely, as Nyström–Holmqvist's check is:** saccade counts within a
factor of two. The kappas are a measurement, recorded in CHECKPOINT and not
gated.

**The prediction, stated before measuring:**
- saccade agreement high, since the estimator changes smoothing, not method;
- PSO agreement lower, since short events are the most sensitive to
  smoothing;
- pursuit agreement lowest, since it is a 2 °/s threshold on a low-passed
  trace.

**Null:** a duration-matched random-span control must score near-zero kappa.

### 5.3 Human coders — does it do what the paper reports? (gated on `WLPP_ANDERSSON_DATA`)

The dataset the paper validated against is Andersson et al. (2017):
- human data, monocular, at 500 Hz;
- every sample hand-labelled by two expert coders, MN and RA;
- 28 image, 22 moving-dot and 18 video files in `annotated_data/data used in
  the article/`;
- plus the correction in `annotated_data/fix_by_Zemblys2018/` that the paper
  applied: "A minor labeling mistake reported in Zemblys et al. (2018) was
  fixed prior to this validation analysis."

**GPL-3.0** is the licence in the dataset repository's own `LICENSE`. The
test reads a local clone that the environment variable names. Nothing from
it is committed or vendored — the rule the OpenIris reference recording
already follows.

**First, the harness is proved.** The oracle, run through it, must reproduce
the paper's own Table 3 — Cohen's kappa of REMoDNaV against each coder, for
fixations, saccades and PSOs, on images, dots and videos — within ±0.05.
Andersson et al.'s definition of per-event kappa is read from that paper
before the harness is written, not assumed. A harness that cannot reproduce
the published oracle numbers is measuring something else.

**Then ours runs through the same harness.** Prediction: in each stimulus
category, saccade kappa no more than 0.05 below the oracle's. PSO and
fixation kappas are recorded, not gated, for §5.2's reason.

**Null:** random labels drawn at the coders' own label proportions must score
kappa near zero.

**What it shows, and what it does not.** This is human data. It shows the
reimplementation behaves like REMoDNaV on the data REMoDNaV was validated on,
and it says nothing about macaques (§8).

### 5.4 Unit tests, and the first `pursuit` in production

Test-driven, following `test_nystrom_holmqvist.py`'s split between unit
tests and a separate validation module. Each item names what it pins:

1. **The threshold.**
   - σ-scaled MAD.
   - `F = 2 · noise_factor` for the peak threshold, and `noise_factor` for
     on/offset, both from the last iteration.
   - The 30-iteration cap keeps the last value.
   - No speeds below the threshold means no threshold (§1.1).
2. **The asymmetric onset.** §1.3's worked example gives 3. The offset
   reaches the local minimum.
3. **The context window's arithmetic**, including clipping at both ends of
   the recording (§1.2).
4. **Proximity.** A candidate within 40 ms of an accepted saccade or PSO is
   rejected.
5. **The maximum-frequency stop.**
6. **PSOs.**
   - High- and low-velocity both emit `pso`.
   - One with amplitude at or above its saccade's is dropped.
   - Missing speed inside the window drops it.
   - It never extends past `max_pso_duration`.
7. **Recursion finds the small saccade.** A small saccade sits between two
   large ones: the whole recording's threshold misses it, and the piece's
   own threshold finds it. This is the method's point, and the test that
   fails if §1.5 is flattened into one pass.
8. **Gaps.** A stretch of unusable samples splits a period, and no event
   spans it.
9. **Fixation or pursuit.**
   - A slow ramp is `pursuit`, and stillness is `fixation`.
   - A piece shorter than 40 ms emits nothing.
   - The boundary between the two falls at the midpoint.
10. **The two sample-0 fixes** (§2.2).
11. **The output's shape.** Labels are a subset of the declared vocabulary,
    and runs are disjoint and sorted.
12. **The first `pursuit` conjunction, through `daemon.run_once()`.** A new
    fixture holds a slow ramp that both eyes follow, built from
    `EyeFixationSpec` ramps as `_build_glissade_session` builds its wobble.
    Its conjunction must carry a `pursuit` run. The test is verified to fail
    when pursuit detection is mutated off.
13. **The paramset and registry.** `register_default_paramsets` registers
    `remodnav`, and the registry's completeness claim holds.

## 6. Parameters

One frozen dataclass, `RemodnavParams`, carried on the registry entry as
`registry.Detector.defaults`. Values are the paper's Table 1, with durations
in milliseconds (Nyström–Holmqvist's convention, for the same reason: a
sample count is wrong at a different `fs_hz`).

| Field | Value | Source |
|---|---|---|
| `noise_factor` | 5.0 | Table 1 |
| `start_velocity_deg_s` | 300.0 | Table 1, `velthresh_startvelocity` |
| `convergence_deg_s` | 1.0 | eq. 2 |
| `max_iterations` | 30 | `clf.py` 317 — not in the paper |
| `min_saccade_duration_ms` | 10.0 | Table 1 |
| `min_intersaccade_duration_ms` | 40.0 | Table 1 |
| `max_pso_duration_ms` | 40.0 | Table 1 |
| `min_fixation_duration_ms` | 40.0 | Table 1 |
| `min_pursuit_duration_ms` | 40.0 | Table 1 |
| `max_initial_saccade_freq_hz` | 2.0 | Table 1 |
| `saccade_context_window_ms` | 1000.0 | Table 1 |
| `median_filter_ms` | 50.0 | Table 1 |
| `lowpass_cutoff_hz` | 4.0 | Table 1 |
| `lowpass_order` | 5 | `clf.py` 680 — not in the paper |
| `pursuit_velocity_deg_s` | 2.0 | Table 1, `pursuit_velthresh` |

**These parameters are not fields, and each is covered elsewhere:**
- `px2deg`: gaze arrives in degrees;
- `sampling_rate`: `fs_hz` is positional;
- `min_blink_duration`, `dilate_nan` and `max_vel`: the validity mask (§3);
- `savgol_length` and `savgol_polyord`: the shared estimator (§3).

**Nothing is tuned.** If §8 item 1's answer is "no moving targets", the
paper's own remedy for static stimuli is to disable pursuit with a high
threshold: "pursuit classification could be disabled (by setting a high
pursuit velocity threshold)". That would be a second paramset with its reason
recorded, not a change to this default.

## 7. scipy becomes a runtime dependency

The detector needs three things:
- a median filter;
- a Butterworth design;
- zero-phase filtering with Gustafsson initial conditions.

The oracle calls `scipy.ndimage.median_filter`, `scipy.signal.butter` and
`scipy.signal.filtfilt(..., method='gust')` (`clf.py` 12–15, 680–692), and
this implementation calls the same functions. **scipy is not a runtime
dependency today.** The 3.13 resolution of `pyproject.toml`'s runtime
dependencies contains no scipy, and nothing under `wl_preproc/` imports it.
It is in the development environment only, through `remodnav` and its
dependencies: the dev resolution for 3.13 lists scipy "via formulaic,
remodnav, statsmodels".

**The alternative, reimplementing them in numpy, is rejected.** Gustafsson's
initial-condition method is an algorithm in its own right. Hand-writing it
would add one more reimplementation needing validation, in a spec whose whole
design is about not adding unvalidated ones. A mismatch would then surface in
§5.1 as a pursuit disagreement, which is to say as a finding.

**The consequences:**
- `pyproject.toml`'s runtime dependencies gain `scipy`, with the floor
  chosen in the plan;
- `wl.yaml`'s `third_party` gains `scipy` with its `why`, and `wl-check`
  runs;
- `wlo stack serv` will then put scipy on the preprocessing server.

Nothing else is claimed as a reason for it here.

## 8. Open questions

1. **Do this lab's tasks ever show a moving target?** This is the
   requester's knowledge, not the code's. If the answer is never, every
   `pursuit` label is a misclassified fixation or drift. The paper saw
   exactly this on static images — "a substantial confusion of fixation and
   pursuit for the static images" — and §6 records its remedy. The answer
   decides which paramset the daemon runs.
2. **Macaque drift against a 2 °/s threshold.** The threshold is set "higher
   than natural ocular drift velocities during fixations", citing Goltz et
   al. (1997) and Cherici et al. (2012). Whether macaque fixational drift on
   a DPI stays under 2 °/s after a 4 Hz low-pass is unmeasured here.
3. **`max_initial_saccade_freq` came from humans watching a movie**:
   Amit et al. (2017), 1.7 ± 0.3 Hz. The paper says it "should be smaller than
   an expected (natural) saccade frequency in a particular context". If this
   lab's tasks saccade less often than 2 Hz, the cap is never reached, and
   every candidate above the global threshold is treated as a major saccade
   with a 1 s context. That is how the oracle behaves on short recordings, so
   it is not a failure, but it is untested on macaque tasks.
4. **The conjunction's duration floor** (§4). This is a cross-detector
   decision.
5. **Runtime.** The oracle is pure Python loops; this implementation must
   process a two-hour session per eye. The plan measures it on the reference
   recording. It becomes a design question only if it turns out slow.

## 9. Out of scope

- NSLR, Bayesian microsaccade detection and U'n'Eye.
- The 225/337 unmatched saccades measurement.
- Any tuning for macaque data.
- Changing `_min_duration_samples`.

## 10. References

- Dar, A. H., Wagner, A. S., & Hanke, M. (2021). REMoDNaV: robust
  eye-movement classification for dynamic stimulation. *Behavior Research
  Methods*, 53(1), 399–414.
  [10.3758/s13428-020-01428-x](https://doi.org/10.3758/s13428-020-01428-x).
  Full text: PMC7880959.
- `remodnav` 1.1.2, `remodnav/clf.py`, MIT. A dev-only dependency already
  (`pyproject.toml`, `wl.yaml`). Read as a specification here and run as the
  oracle in §5. **Reimplemented, not transcribed**: this implementation
  follows its behaviour, not its text.
- Nyström, M., & Holmqvist, K. (2010). *Behavior Research Methods*, 42(1),
  188–204. [10.3758/BRM.42.1.188](https://doi.org/10.3758/BRM.42.1.188).
  The method REMoDNaV modifies.
- Andersson, R., Larsson, L., Holmqvist, K., Stridh, M., & Nyström, M.
  (2017). One algorithm to rule them all? An evaluation and discussion of ten
  eye movement event-detection algorithms. *Behavior Research Methods*,
  49(2), 616–637.
  [10.3758/s13428-016-0738-9](https://doi.org/10.3758/s13428-016-0738-9).
  Dataset: `github.com/richardandersson/EyeMovementDetectorEvaluation`,
  GPL-3.0.
- Zemblys, R., Niehorster, D. C., Komogortsev, O., & Holmqvist, K. (2018).
  Using machine learning to detect events in eye-tracking data. *Behavior
  Research Methods*, 50(1), 160–181.
  [10.3758/s13428-017-0860-3](https://doi.org/10.3758/s13428-017-0860-3).
  The source of the one label correction §5.3 applies.
