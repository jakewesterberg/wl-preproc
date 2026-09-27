# REMoDNaV: the first detector with a runnable oracle, and the first that says `pursuit`

**Design spec, 2026-09-26.** Implements the fifth row of design spec
`2026-08-31-saccade-detection-design.md` §3.1's table — REMoDNaV,
`saccade / pso / pursuit / fixation`, "reimplemented".

**Built 2026-09-26 on `spec/remodnav`.** What the build measured is recorded
where this spec predicted it: §3 item 2, §5.1–§5.4, §8 items 4–6 and §10.
Each amendment keeps a note of what the text said before.

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
The code computes a *second* velocity, from 50 ms median-filtered positions
that it has already Savitzky–Golay-smoothed (`clf.py` 858–863, ahead of the
median filter at 869–883). It uses that velocity only to locate the major
saccades that chunk the recording.
Table 1: "for initial data chunking only". The median filter is the method's
device for "emphasiz[ing] large amplitude saccades" (paper, Preprocessing)
against sporadic noise, which is REMoDNaV's reason to exist. Without it,
chunking would pick the noisiest spikes rather than the largest saccades. The
differentiator is still the shared one, so this is the method's own
segmentation step, not a second velocity estimator. **This goes beyond what
was proposed in conversation and is flagged for review.**

*Until 2026-09-26 this said "from 50 ms median-filtered positions" and did
not mention the smoothing that comes first; Task 5 found the omission.*
- **The window** is 50 ms rounded to a sample count, plus one if that count
  is even, so the filter is centred: 25 at 500 Hz, 51 at 1000 Hz.
- **What is filtered: raw positions.** The oracle median-filters
  Savitzky–Golay-smoothed positions. This implementation median-filters raw
  ones, which follows from item 1's dropping the Savitzky–Golay filter. The
  difference is one of those §5.2's end-to-end numbers include. Task 5 found
  that it did not change §8 item 6's 5 °/s outcome, since both implementations
  chop that ramp.
- **Unusable samples:** a median-filtered sample whose window touches an
  unusable sample is itself unusable, so blink positions never reach the
  candidate speed. This replaces the code's handling (`clf.py` 875–883),
  which runs the median filter over positions containing NaN.
- **Unusable positions are zeroed before the filter, not left as NaN**
  (`_candidate_speed`). The zero never reaches a speed the detector reads:
  every speed within reach of a withheld sample is masked. A NaN would not
  stay within that reach. scipy's 1-D `median_filter` keeps a running median,
  and a NaN inside it changes samples well past its own window. Task 5
  measured this on scipy 1.17.1:
  - in white noise at width 25, a 100-sample NaN block changed samples up to
    88 past its end (the Task 5 review measured 48 on another trace);
  - on `gaze_trace(500, 9)` with samples 1200–1300 withheld, the mask's
    reach ends at 1313. Beyond it, 27 candidate-speed samples (1314–1342)
    changed by up to 1.53 °/s when the withheld positions were NaN rather
    than finite.

  This is latent in production, where withheld positions are finite: gaze is
  an affine map of Purkinje differences read from text. It is pinned by
  `test_what_the_mask_withholds_never_reaches_the_candidate_speed_even_as_nan`.

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

**Measured 2026-09-26 (Task 4, `test_remodnav_fidelity.py`):**
- **0 samples differ in all five cases**: synthetic `gaze_trace` at 500 Hz
  (seeds 1–3) and 1000 Hz (seeds 4–5), against `remodnav` 1.1.2. The check
  passes on scipy 1.17.1 (Python 3.11) and on scipy 1.18.1 (Python 3.13).
- **Both nulls fail the check**, as they must.
- **Every case exercises every path.** The oracle's own events in each case
  include `SACC`, `ISAC`, `FIXA`, `PURS` and at least one PSO label.
- **At 1000 Hz, seeds 4 and 5, the oracle's major pass absorbs the planted
  pursuit into a saccade** (§8 item 6). Given the same signals, `classify`
  reproduces that absorption sample for sample.

### 5.2 Deviation — what does the shared preprocessing cost? (gated on `WLPP_OHDPI_REFERENCE`)

The comparison runs end to end on the reference recording, with the same
p99→15° scale and leading 120,000-sample slice as Nyström–Holmqvist's oracle
check. Ours runs with the shared estimator; the oracle runs its own
preprocessing, given degrees with `px2deg = 1`.

**Reported, per eye and per kind:**
- event counts;
- sample-level Cohen's kappa of each label against the rest.

**Asserted loosely, as Nyström–Holmqvist's check is:**
- saccade counts within a factor of two;
- saccade kappa above the null's ceiling, `NULL_KAPPA_CEILING = 0.1`, the
  same ceiling the null below must stay under.

The PSO, fixation and pursuit kappas are a measurement, recorded in
CHECKPOINT and not gated.

*Until 2026-09-26 this said all the kappas were "not gated". That was true
when written. The plan's ruling kept a saccade floor, because a null means
something only if the real comparison must beat it.*

**Measured 2026-09-26** (Task 6, `test_remodnav_validation.py`).
- **Recording:** the reference recording `OpenIris-2024Jul31-114628`, its
  leading 120,000-sample slice, p99→15° scale, the file's own 498.55 Hz.
- **Ours:** the shared estimator and the validity mask.
- **Oracle:** `remodnav` 1.1.2 end to end, on its own preprocessing, given
  degrees with `px2deg = 1`.
- **Environment:** scipy 1.17.1, Python 3.11.

| | left | right |
|---|---|---|
| saccades, ours | 519 | 488 |
| saccades, oracle (`SACC` + `ISAC`) | 574 | 565 |
| count ratio | 1.106 | 1.158 |
| kappa, saccade | 0.764 | 0.724 |
| kappa, PSO | 0.391 | 0.438 |
| kappa, fixation | 0.823 | 0.816 |
| kappa, pursuit | 0.267 | −0.008 |

- **Both gated assertions pass.**
- **The prediction's order held in both eyes**: saccade above PSO above
  pursuit. On the right eye, pursuit agreement is at chance.
- **Ours finds fewer saccades than the oracle** in both eyes: 55 fewer on the
  left and 77 on the right, over this slice.
- **Full recording, 1,177,799 samples per eye:** classified in 2.5 s (left,
  12,613 runs) and 2.6 s (right, 12,158 runs). This answers §8 item 5.
- **What these numbers cannot say** is which side is right, because the
  recording has no ground truth. They measure every §3 difference at once and
  do not apportion the cost among them:
  - the shared five-point estimator in place of Savitzky–Golay;
  - the validity mask in place of the Stampe filter, dilation and `max_vel`;
  - a candidate speed median-filtered from raw rather than smoothed positions
    (§3 item 2).

*This listed the prediction, stated before measuring: saccade agreement
high, PSO lower, pursuit lowest (a 2 °/s threshold on a low-passed trace).
True when written; the measurement held its order.*

**Null:** a duration-matched random-span control must score near-zero kappa.
It does. `test_the_null_random_spans_score_near_zero_kappa` stays under the
0.1 ceiling. It runs ungated, in CI, on a synthetic `gaze_trace` (500 Hz,
seed 12) against the oracle's own saccades.

### 5.3 Human coders — does it do what the paper reports? (gated on `WLPP_ANDERSSON_DATA`)

The dataset the paper validated against is Andersson et al. (2017):
- human data, monocular, at 500 Hz;
- every sample hand-labelled by two expert coders, MN and RA;
- 28 image, 22 moving-dot and 18 video files in `annotated_data/data used in
  the article/`;
- one corrected MN file in `annotated_data/fix_by_Zemblys2018/`.

**The paper says it applied that correction. The script that computed Table 3
never loads it.** The paper's words: "A minor labeling mistake reported in
Zemblys et al. (2018) was fixed prior to this validation analysis." The
script is `psychoinformatics-de/paper-remodnav`'s `code/mk_figuresnstats.py`:
- **`load_anderson` reads from `fix_by_Zemblys2018/` only when it is given
  the name `UH29_img_Europe_labelled_FIX_MN.mat`** (lines 51–60 of the copy
  read for Task 7, re-read for Task 8).
- **`confusion()` renames the file to that before loading it** (310–312).
- **`kappa()`, which produced Table 3, never does.** It passes
  `UH29_img_Europe_labelled_MN.mat` straight from its file list, and that
  list never produces the `FIX` name (1218–1222).

So Table 3 was computed on the uncorrected file, and this harness loads what
`kappa()` loaded.

*This said the paper applied the correction, which is true of the paper's
prose. It is not true of the script behind Table 3: Task 7 read
`mk_figuresnstats.py`, and Task 8 re-read it.*

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

**Measured 2026-09-26 (Task 7). The harness is proved twice.**
- **The coders' own agreement (MN-RA) reproduces Table 3 within ±0.006 on
  all 9 cells, over all 34 files.** It touches no algorithm. The largest
  difference is 0.0045.
- **The oracle's agreement (AL-RA, AL-MN) reproduces all 18 cells within
  ±0.05 in the suite**: scipy 1.17.1, edge-trimmed, over 33 files (below).
- **Out of the suite, the untrimmed oracle reproduces Table 3 within 0.005
  on every one of the 18 cells** (scipy 1.13.1, all 34 files). The trim
  changes those kappas by 0.001 at most.

So the harness and the installed oracle reproduce the paper, and nothing
between the paper's code and `remodnav` 1.1.2 moved its Table 3 numbers
beyond 0.005.

**In the suite, the oracle runs on trimmed recordings, and one file is
excluded.**
- **Why a trim is needed.** `remodnav` 1.1.2's `preproc` calls
  `savgol_filter` with its default edge mode, which fits a polynomial to each
  edge window. From scipy 1.17 that fit raises `ValueError` when the window
  holds a missing sample; older scipy let the NaN through. Untrimmed, 9 of
  the 34 RA files raised.
- **What the trim does.** Each end of a recording is trimmed until the
  oracle's own Savitzky–Golay edge window holds no missing sample. That
  window is 9 samples at 500 Hz, `int(0.019 * fs)` (`clf.py` 841). The oracle
  classifies what remains, its labels are painted back at their offset, and
  the trimmed samples stay unlabelled.
- **One file still raises:** `video/UL31_video_triple_jump_labelled_RA.mat`.
  Its 91-sample interior gap ends 10 samples before the recording does.
  `preproc`'s own `dilate_nan` (`clf.py` 846–863) widens the gap by 5 samples
  on each side, inside `preproc`, which leaves 4 clean samples — fewer than
  the 9-sample edge window.
- **The exclusion is bounded.** The file is declared in
  `EXPECTED_ORACLE_FAILURES`. `test_the_oracle_fails_only_on_the_diagnosed_file`
  fails if another file fails, if this one stops failing, or if its message
  no longer matches.

The file sets follow from that:
- **AL-RA, AL-MN, US-RA and US-MN are all over the same 33 files**, so ours
  and the oracle are compared on identical data.
- **MN-RA stays over all 34.** Dropping this file moves MN-RA's own (video,
  Fix) cell from 0.6527 to 0.6374, outside its ±0.006, for a reason that has
  nothing to do with what that pair measures.

**The kappas.** From Task 7's final run: scipy 1.17.1, Python 3.11, MN-RA
over 34 files and every other pair over the same 33. Table 3's value is in
parentheses. AL is the oracle; US is ours.

| stimulus | event | MN-RA | AL-RA / AL-MN | US-RA / US-MN |
|---|---|---|---|---|
| img | Sac | 0.906 (0.91) | 0.761 / 0.759 (0.78 / 0.78) | 0.809 / 0.802 |
| dots | Sac | 0.813 (0.81) | 0.721 / 0.777 (0.72 / 0.78) | 0.756 / 0.829 |
| video | Sac | 0.875 (0.87) | 0.779 / 0.811 (0.76 / 0.79) | 0.798 / 0.832 |
| img | Fix | 0.840 (0.84) | 0.547 / 0.527 (0.55 / 0.52) | 0.524 / 0.503 |
| dots | Fix | 0.652 (0.65) | 0.397 / 0.436 (0.37 / 0.45) | 0.383 / 0.447 |
| video | Fix | 0.653 (0.65) | 0.413 / 0.371 (0.44 / 0.39) | 0.386 / 0.341 |
| img | PSO | 0.762 (0.76) | 0.591 / 0.581 (0.59 / 0.58) | 0.573 / 0.562 |
| dots | PSO | 0.621 (0.62) | 0.376 / 0.409 (0.38 / 0.41) | 0.434 / 0.468 |
| video | PSO | 0.645 (0.65) | 0.470 / 0.522 (0.45 / 0.51) | 0.478 / 0.558 |

- **The saccade prediction holds, with room to spare: ours is at or above
  the oracle on every saccade cell.** The smallest margin is video against
  RA, +0.019.
- **Fixation, not gated:** ours is below the oracle in five of six cells, by
  at most 0.030 (video, against MN).
- **PSO, not gated:** ours is above the oracle on dots and video, and below
  it on images by at most 0.019.
- **The null passes.** As built, it draws both label sets at a fixed 30%
  proportion rather than at proportions measured from the coders' files, and
  scores |kappa| < 0.05.
- **Secondary: scipy 1.17.1 and 1.13.1 do not give the oracle identical
  output on every file.** On `dots/UL31_trial1` (66 missing samples across
  several runs), the fixation/pursuit split differs by about 60–80 samples.
  That moves (dots, Fix) AL-RA from about 0.372 to 0.397. Both values are
  within ±0.05 of the paper's 0.37.

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
    - **Its measured premise** (Task 5 Step 5): the ramp is 2.1 °/s for 2.9 s,
      5% above the 2 °/s pursuit threshold (`_PURSUIT_STEP_PX = 1218` px over
      `_PURSUIT_DURATION_S = 2.9`, 420 px/s at `CAL_SCALE`). The check ran on
      the fixture's own generated session with the real validity mask, seeds
      621–640 × 2 eyes:
      - pursuit covers 96.1–98.3% of the ramp in every eye, with no saccade
        inside it;
      - the two eyes' pursuit overlaps on 94–98% of the ramp.

      The test itself is deterministic at seed 621.
    - **The margin is narrow.** Over seeds 621–720, 7 of 200 eyes carry one
      or two 5–7-sample saccades inside the ramp. The conjunction still
      carried pursuit on all 100 seeds.
    - **Faster is not an option.** The plan's 5 °/s × 1.5 s ramp is not
      pursuit to REMoDNaV on this session (§8 item 6).
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

   **Answered by the requester on 2026-09-26: "Not yet, maybe later."** No
   current task moves a target, but a future one might. So:
   - **The daemon runs the §6 defaults, with pursuit on.** Nothing a future
     task produces is lost to a paramset that could not say `pursuit`.
   - **A `pursuit` label on a current task's session is suspect.** It is far
     more likely to be a fixation or drift that the 2 °/s threshold caught,
     item 2's open question, than a pursuit.
   - **No pursuit-disabled paramset is registered now.** It is the §6
     remedy to reach for if suspect `pursuit` labels start costing something
     downstream.
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
   decision. It is now observed, not only inferred. On `near_miss_session`,
   REMoDNaV's conjunction keeps a 5-sample binocular overlap (5811–5816)
   (Task 5). `test_a_sub_floor_binocular_overlap_is_no_conjunction_event`
   checks Engbert–Kliegl only, so nothing fails. Two millisecond-based
   detectors now inherit the one-sample floor: Nyström–Holmqvist and
   REMoDNaV.
5. **Runtime. Answered 2026-09-26 (Task 6): it is fast.** The whole reference
   recording is 1,177,799 samples per eye at 498.55 Hz, about 39 minutes.
   `detect_remodnav` classified it in 2.5 s (left, 12,613 runs) and 2.6 s
   (right, 12,158 runs). That was timed inside §5.2's test, on this project's
   macOS development machine. A two-hour session at 500 Hz has about three
   times as many samples; none has been timed. No design question arises.

   *This said the plan would measure runtime on the reference recording, and
   that it would become a design question only if slow; true when written.*
6. **REMoDNaV reads moderate pursuit as saccades when holds dominate a
   recording.** The build found this in Tasks 3–5. It is REMoDNaV's own
   behaviour, and the oracle does the same, so it is not a defect of this
   reimplementation.
   - **The synthetic ohDPI session chops a pursuit into short saccades.**
     This is §5.4 item 12's generator: 0.03° per-eye jitter, the real
     validity mask, seeds 621–640 × 2 eyes. A 5 °/s, 1.5 s pursuit becomes
     10–18 short saccades per eye (5–12 samples each), with 0–38% of the
     ramp left as pursuit, in all 40 eyes. The oracle, end to end on its own
     preprocessing, does the same in all 40 eyes: 10–18 saccades inside the
     ramp, and 0–42% pursuit. At the fixture's 2.1 °/s the oracle still finds
     1–8 saccades inside the ramp in 39 of 40 eyes, where ours finds none.
   - **The mechanism.**
     1. The major pass (§1.2) thresholds the candidate speed across the whole
        recording.
     2. On holds, the 50 ms median filter suppresses the jitter, so the holds
        set the threshold: 6.17–6.82 °/s here.
     3. A ramp is monotone. Above about 0.6 °/s it spans more than the
        jitter's SD across the 25-sample window, so the median passes its
        jitter straight through. The ramp's candidate speed peaks at
        11.1–15.6 °/s and is above the threshold on 17–27% of its samples.
     4. Every crossing is a major-saccade candidate. The on/offset search on
        the primary speed gives it at least 5 samples, and REMoDNaV has no
        maximum saccade duration.
   - **On clean data, the same property turns a pursuit into a saccade.** The
     synthetic `gaze_trace` at 1000 Hz has 0.01° noise and a planted 1.5 s,
     8 °/s pursuit over samples 8000–9500. There the oracle's major pass
     labels the ramp a saccade:
     - seed 7: one `SACC` over 8002–9503;
     - seed 4: 80 samples of fixation, then 1420 of saccade;
     - seed 5: 238 samples of pursuit, then 1262 of saccade.

     Of seeds 1–39, only 24, 26 and 37 kept the ramp separate. `classify`
     reproduces this exactly (§5.1).
   - **Speed decides it.** Task 5 scanned ramp speed and duration on the
     fixture's session, seeds 621–640 × 2 eyes, counting eyes that fail the
     fixture's premise:
     - 1.88 °/s × 2.9 s: all 40, since this is below the pursuit threshold;
     - 2.05–2.15 °/s × 2.9 s: none;
     - 2.5 °/s × 1.5 s: 11 of 40.

     The window between not-pursuit and saccade trains is narrow. That is why
     the fixture's pursuit is 2.1 °/s (§5.4 item 12).
   - **Its bearing on macaque data (items 1 and 2).** A macaque session is
     mostly fixations on a low-noise DPI, which is the condition above.
     - No current task moves a target (item 1), so today this cannot cost a
       pursuit.
     - A future task that moves one at a few °/s may be stored as trains of
       short saccades, inflating saccade counts, rather than as pursuit.
       Whether it is on real macaque data is unmeasured.
     - The method has no maximum saccade duration to set, and tuning is out
       of scope (§9). This stays open until such a task exists.
   - **Its bearing on §5.2:** the reference recording's pursuit kappas (0.267
     and −0.008) are the lowest there. Whether this mechanism contributes to
     them is not measured.

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
- Zemblys, R., Niehorster, D. C., & Holmqvist, K. (2018). gazeNet:
  end-to-end eye-movement event detection with deep neural networks.
  *Behavior Research Methods*.
  [10.3758/s13428-018-1133-5](https://doi.org/10.3758/s13428-018-1133-5).
  This is the Zemblys et al. (2018) that the REMoDNaV paper cites for the
  label correction: its reference list (PMC7880959) has no other Zemblys
  entry. The dataset holds that correction in `fix_by_Zemblys2018/`, and the
  script behind Table 3 never loads it (§5.3). Which Zemblys paper first
  reported the mistake was not checked.

  *This entry named Zemblys, Niehorster, Komogortsev & Holmqvist (2018),
  "Using machine learning to detect events in eye-tracking data",
  10.3758/s13428-017-0860-3, as "the source of the one label correction §5.3
  applies". The REMoDNaV paper does not cite that paper, and §5.3 does not
  apply the correction.*
