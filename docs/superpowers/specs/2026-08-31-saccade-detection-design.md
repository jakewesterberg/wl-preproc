# Saccade detection, and what seven detectors agreeing is worth

**Design spec, 2026-08-31.** Implements the second half of parent design spec
§7.2, which the eye spec
(`2026-08-30-eye-ohdpi-calibration-and-gaze-design.md`) deliberately excluded:
"Saccade detection is deliberately not in this spec."

Depends on the eye subsystem and the second-order calibration, both merged.
Gaze is a computation, never a stored array, and this spec does not change
that — but it does introduce the pipeline's **first stored derived array**, in
a form chosen so it is not a blob.

---

## 0. What this is for, and why it is not one number

Parent §7.2 states the purpose in one sentence: *"Their agreement rate is a
data-quality metric. Sessions where the two detectors diverge indicate
degraded tracking, surfaced automatically in the daily report rather than
discovered during analysis months later."*

That sentence was written for two detectors. **Seven run now** (ruled
2026-08-31), and that breaks the sentence's arithmetic completely: with two,
"they disagree" has one meaning; with seven there are twenty-one pairs, and
blending them into a single number destroys every reading that matters.

The reason is specific and known in advance. Parent §7.2 records it as a
caveat: *"U'n'Eye's pretrained weights come from datasets dominated by
video-based trackers. A dual-Purkinje tracker at 500 Hz has far lower noise,
so the input distribution differs meaningfully. Expect to fine-tune on
hand-labeled lab data (a post-January task)."* Until that fine-tuning
happens, U'n'Eye may disagree because **it** is miscalibrated for this rig,
not because tracking degraded — and a blended number would fire on every good
session in January, which is exactly when the metric needs to be trusted.

So agreement is a **suite**, computed pairwise, with the n-way blended number
kept beside it rather than instead of it. "Engbert–Kliegl and Otero-Millan
agree at 0.94 and both disagree with the CNN" is a diagnosis. "Agreement:
0.71" is not.

---

## 1. The label taxonomy

Eight labels per sample, assigned by **strict precedence**, because a sample
can qualify for several and an unstated overlap is how two definitions of one
fact are created.

| Order | Label | Where it comes from |
|---|---|---|
| 1 | `blink` | `DataQuality < 100` — the tracker's own stated failure |
| 2 | `invalid` | any other validity-mask criterion (§2) |
| 3 | `saccade` *or* `microsaccade` | a detected event, split by amplitude at the threshold |
| 4 | `pso` | post-saccadic oscillation, in the window following a saccade (§2.5) |
| 5 | `pursuit` | a detected smooth-pursuit segment |
| 6 | `drift` | slow fixational motion, where a detector distinguishes it |
| 7 | `fixation` | everything else |

`saccade` and `microsaccade` share one precedence level deliberately: they are
a **split**, not a ranking. A sample inside a detected event is one or the
other by that event's amplitude — never both, and never in contention.

**`pso` sits below the saccade it follows and above everything slow**, which
is what makes lens wobble nameable instead of arriving as a spurious
microsaccade (§2.5). **`drift` and `pursuit` are refinements of what was
previously all `fixation`** — a still eye, a slowly drifting one and a
pursuing one are three states, and on a 500 Hz dual-Purkinje tracker the
difference is measurable rather than notional.

**Not every detector can emit every label**, and that is a first-class fact
rather than a wrinkle — see §3.1 and §6.1.

**`blink` outranks `invalid`, and the order is load-bearing.** A blink *is* a
validity failure, so generic-first would mean no sample is ever labelled
`blink` and the label would be dead code that looks alive.

**`blink` reuses `EyeQuality`'s existing definition exactly** — a sample whose
`{eye}DataQuality` is below 100, that table's own `_FULL_TRACKING_QUALITY`.
Restated, never re-derived: a second blink definition free to drift from the
one the daily report already publishes is precisely the defect this repository
names most often.

**Microsaccade is an event property, not a sample property.** You cannot
classify a sample by amplitude; only an event has one. So detection runs
first, amplitude is measured second (§3), and every sample of an event
inherits the event's class. The threshold is a paramset parameter, default
1.0° — the conventional cut, and one this lab will want to move.

---

## 2. The validity mask, and why it gets its own table

OpenIrisDPI's tutorial notebook lists five criteria: eye open, gaze within a
plausible region, plausible speed, no frame discontinuity, and then invalid
regions expanded with short surviving epochs dropped.

**None of them involves a detector.** They depend on the raw recording and the
calibration alone. That is why the mask is its own Computed table
(`EyeValidity`) with its own `eye_validity` paramset, rather than living
inside each detector's paramset.

The alternative was considered and rejected: with mask parameters inside each
detection paramset, seven detectors can silently run against seven different
masks, and the agreement metric would then be comparing **masks as well as
detections** — measuring the thing it exists to hold constant. Sharing one
mask makes `invalid` and `blink` identical across every trace *by
construction*, so a disagreement can only be a detector disagreement. The
cost, stated: detection's primary key carries two paramset columns, and
detection rows multiply with validity paramsets. With one mask, seven
detectors and three traces that is 21 rows per session.

**Criterion 4 already has its input.** `read_ohdpi` reports `frame_gaps` as
`(row, n_missing)` pairs rather than refusing a recording over a dropped
frame — built 2026-08-31 for exactly this consumer. A velocity computed
across a gap is a spurious saccade, which is why detection must run per valid
epoch rather than over a trace with the gaps papered over.

Criterion 2's "plausible region" and criterion 3's speed ceiling are rig
parameters with no measured value yet; §11 records that.

---

## 2.5 Post-saccadic oscillation is not an edge case on this instrument

**Measured on a dual-Purkinje tracker, against a scleral search coil.** Deubel
& Bridgeman recorded saccades simultaneously with both and found "considerable
dynamic deviations during and immediately after the saccade, which we ascribe
to the movements of the eye lens relative to the optical axis of the eye",
with retinal image displacement from lens movement alone "as large as 0.5 deg"
— larger at near accommodation, smaller in older subjects
(*Vision Research* 1995, [10.1016/0042-6989(94)00146-d](https://doi.org/10.1016/0042-6989(94)00146-d)).

That is this rig's instrument and P4 is this pipeline's signal. **0.5° is half
the default microsaccade threshold**, so lens ringing after every real saccade
lands squarely in microsaccade territory. A detector that does not model it
will report those samples as a microsaccade, and it will do so immediately
after every saccade, systematically, in a way no amount of averaging removes.

**And the disagreement it causes is arbitrary rather than informative.**
Nyström & Holmqvist found glissades in "about half of the saccades", mean
duration close to 24 ms, and concluded that researchers "must actively choose
whether to assign the glissades to saccades or fixations; the choice affects
dependent variables such as fixation and saccade duration significantly.
Current algorithms do not offer this choice, and their assignments of each
glissade are largely arbitrary" (*Behavior Research Methods* 2010,
[10.3758/BRM.42.1.188](https://doi.org/10.3758/BRM.42.1.188)).

Two consequences for this design, and they are the reason `pso` is a label
rather than a footnote:

1. **The assignment is an explicit parameter, never a default.** Whether a
   `pso` run counts as saccade or as fixation is stated per comparison (§6.1),
   which is precisely the choice Nyström & Holmqvist say algorithms fail to
   offer.
2. **Without it, the agreement metric measures glissade-handling conventions
   rather than tracking quality** — in half of all saccades. The metric exists
   to flag degraded tracking; a systematic artifact of the instrument firing it
   on every good session would make it worthless in exactly the month it is
   needed.

The refractory window has an empirical basis rather than a guessed one: ~24 ms
mean glissade duration, against ≤0.5° of lens displacement. Its value is a
paramset parameter and the lab's own recordings settle it, since Deubel &
Bridgeman found the magnitude varies with accommodation and with age.

---

## 3. The detector interface

A registry, following `timebase/extract.py::EXTRACTORS`' precedent, whose set
equality against the registered paramsets is this subsystem's completeness
claim:

```python
DETECTORS: dict[str, Detector]              # name -> callable + declared vocabulary
def detect(gaze_deg, velocity, valid, params) -> list[LabelledInterval]
```

**Velocity is computed once, upstream, and passed in.** Every
threshold-based method inherits its differentiator, and if each filtered its
own way the agreement metric would compare *differentiators* as well as
detections — the same argument as measuring amplitude centrally, one level
further upstream. The validity mask's speed criterion needs velocity anyway
(§2), so it exists before any detector runs. **This is the single most
consequential preprocessing decision in this spec**: it is what makes a
disagreement attributable to a method rather than to a smoothing window.

**Detectors return labelled intervals. Shared code measures them.** Amplitude,
peak velocity and duration are computed once, downstream, identically for all
seven, so a disagreement is never a disagreement about measurement.

### 3.1 Seven detectors, and what each can say

| Detector | Vocabulary it can emit | Source |
|---|---|---|
| Engbert–Kliegl | saccade / microsaccade | reimplemented |
| Otero-Millan | microsaccade | ported from a BSD-3 reference |
| Nyström–Holmqvist | saccade / pso / fixation | reimplemented |
| NSLR | saccade / pso / pursuit / fixation | reimplemented |
| REMoDNaV | saccade / pso / pursuit / fixation | reimplemented |
| Bayesian microsaccade detection | microsaccade / drift | reimplemented |
| U'n'Eye | saccade | **vendored** (§8) |

**Vocabularies differ, and that is a first-class fact.** A detector that
cannot emit `pso` is not disagreeing with one that can; it has nothing to say.
Each registry entry therefore declares the labels it produces, and §6.1 makes
every comparison happen in a vocabulary both sides can express.

The three that were in this spec's first draft carry their original
justification: **Engbert–Kliegl** is the zero-dependency baseline every other
method is benchmarked against; **Otero-Millan** is threshold-free with a
per-detection reliability index, by the author of OpenIris itself; **U'n'Eye**
is a CNN at human-level accuracy validated on *Macaca mulatta*. The four added
2026-08-31 each close a specific gap:

- **Nyström–Holmqvist** ([10.3758/BRM.42.1.188](https://doi.org/10.3758/BRM.42.1.188))
  — adaptive thresholds, settings-free, and the first method here that names
  glissades at all. On this instrument that is not a refinement (§2.5).
- **NSLR** ([10.1038/s41598-017-17983-x](https://doi.org/10.1038/s41598-017-17983-x))
  — segmented linear regression that "simultaneously denoises the signal and
  determines event boundaries", O(n), adding pursuit and PSO.
- **REMoDNaV** ([10.3758/s13428-020-01428-x](https://doi.org/10.3758/s13428-020-01428-x))
  — adaptive, robust to temporally varying noise, saccades/PSO/fixation/pursuit.
- **Bayesian microsaccade detection**
  ([10.1167/17.1.13](https://doi.org/10.1167/17.1.13)) — the only probabilistic
  member. It "returns probabilities rather than binary judgments", models
  **drift** as an explicit state, and was validated on Dual Purkinje Image
  data, "whose higher precision justifies defining the inferred microsaccades
  as ground truth"; at EyeLink-comparable noise it recovered true microsaccades
  with 54% fewer errors than velocity thresholding. Its posteriors are what
  let the consensus suite carry soft measures rather than hard-label votes
  alone.

### 3.2 Reimplemented, not vendored — and the risk that carries

**Everything except U'n'Eye is this project's own code.** The principle:
vendor what cannot be reproduced, reimplement what a paper fully specifies. A
trained CNN's weights are the artifact and cannot be rewritten from the paper,
which is why U'n'Eye is vendored and nothing else is. Three things this buys:
one shared velocity estimator across all seven rather than seven private ones;
no licence question in any code that runs (NSLR's classification half is
AGPL-3.0 and its segmentation half declares no licence — reimplementing
dissolves that entirely); and no dependency on repositories last touched in
2019 and 2020.

**The risk it carries is specific and must be designed against: a buggy
reimplementation is indistinguishable from a genuine detector disagreement.**
Seven methods whose whole purpose is to be compared, six of them written here,
means a reimplementation defect does not look like a defect — it looks like a
finding, and it looks like exactly the finding this subsystem exists to
surface. Mitigation is not optional:

- **Published output statistics are checkable predictions.** Nyström &
  Holmqvist report glissades in about half of saccades at ~24 ms mean
  duration; a reimplementation that produces neither is wrong regardless of
  whether it runs.
- **Permissively licensed implementations are test-time oracles**, the pattern
  this repository already uses when SpikeInterface validates the synthetic
  generator's output. REMoDNaV is MIT and on PyPI; Otero-Millan's reference is
  BSD-3-Clause. Both are development dependencies used to check our output,
  never runtime dependencies and never shipped.
- **BMD has no usable oracle** — its reference is C++ and unlicensed — so it
  is validated against the paper's own simulated-data claims instead, and its
  rows are marked provisional until it is.

Each detector is one `ParamSet` row of type `eye_detection`, using the
existing `(paramset_type, paramset_idx)` table. Parent §7.2 puts revisability
here deliberately: *"Detection lives in its own Computed table keyed by
`paramset_idx`, so detection parameters are revisable per project without
recomputing gaze or touching anything upstream."*

**Detection reads gaze as a computation** — the raw `.txt` plus the
calibration map, through `gaze_trace`, never a stored trace. A session whose
calibration was `refused` has no gaze, so detection is refused too, with its
own stated reason, in the same first-class-refusal discipline the rest of
`eye/` uses.

---

## 4. Per eye, and the conjunction

Engbert & Kliegl's binocular criterion — an event must appear in both eyes
with temporal overlap — is the method's noise-suppression mechanism, not an
optional extra, and microsaccades are conventionally required to be binocular.
But every eye table here is keyed per eye, calibration can succeed for one eye
and be refused for the other, and running only binocularly would throw away a
one-eye session entirely.

So **both**, in one table: `trace : enum('left','right','conjunction')`.
Named `trace` rather than `eye` because a conjunction is honestly not an eye.

`key_source` yields `(session, validity_paramset, detection_paramset)` and a
single `make()` computes all three traces, mirroring `EyeCalibration.make()`,
which already computes both eyes in one call for the same reason: the raw
file is read once.

The conjunction is derived, never independently detected: an event survives
when the two eyes' events of the same detector overlap in time. A session with
one usable eye yields `left` or `right` and **no** `conjunction` row, with the
reason recorded — never a silent monocular fallback wearing a binocular name.

---

## 5. Storage: runs as rows

This is the pipeline's first stored derived array, and it lands on the
guardrail this repository states most emphatically. From
`tests/schema/test_guardrails.py`: *"under DataJoint 2.x a bare `longblob`
declares a raw binary column, an inserted numpy array is stored as its string
repr — elided by numpy above ~1000 elements — and nothing raises on insert or
on fetch. Measured: 31,488 float32 values stored as 488 bytes,
unrecoverable."*

**So the trace is stored as runs, as rows** — `(run_start_row, run_end_row,
label)` — which is the same information losslessly, since a label trace is
piecewise constant. The per-sample array is `np.repeat`. Three consequences,
each of which is why this beats a blob:

- **No blob at all**, so the guardrail is satisfied by construction rather
  than by a round-trip test someone has to remember to write.
- **Queryable.** "Total microsaccade time this month", "sessions where
  `invalid` exceeds 20%" are `WHERE` clauses instead of a full-table scan and
  a decode. This is the same argument §4 of the second-order spec made for
  naming coefficient columns rather than storing a blob, and it held.
- **A structural invariant.** Runs must tile `[0, n_samples)` exactly, with no
  gap and no overlap, checkable on insert. A blob has no such property.

And a run of a `saccade` or `microsaccade` label **is** an event, so the run
row carries `amplitude_deg`, `peak_velocity_deg_s` and a nullable
`reliability` (Otero-Millan's; null for the other two). The runs table is
therefore strictly more informative than the per-sample trace it encodes, not
a lossy substitute for it.

**Size, and one number that is an estimate rather than a measurement.** The
reference recording is 1,177,799 rows, 39.3 minutes at 500 Hz. As a uint8
per-sample array that is 1.18 MB per eye per detector — for contrast, the
gaze array the eye spec refused to store is ~38 MB, so the label trace is
~32× smaller and the objection does not transfer at the same magnitude. As
runs, at a typical 3 detected events per second — saccades and microsaccades
together, each contributing its own run plus the fixation run that follows it
— roughly **14,000 runs per eye per detector**, so ~294,000 rows per session
across three traces and seven detectors. **That
figure is extrapolated from typical saccade rates, not measured** — nothing
has run a detector on a real recording yet, and the implementation plan must
measure it against one before this design is trusted on storage grounds.

---

## 6. The consensus suite

```python
CONSENSUS_METRICS: dict[str, Metric]
```

A registry, so **adding a metric after January is a registry entry and new
rows, never a schema migration**. The migration window closes in January
(second-order spec §4.1); a metrics registry is how this subsystem stays
extensible past that date without one.

### 6.1 Comparisons happen in a vocabulary both sides can express

With seven detectors emitting between one and four label classes each (§3.1),
the naive comparison is broken before it starts: Engbert–Kliegl saying
`saccade` where NSLR says `pso` is not a disagreement about the data, it is
Engbert–Kliegl having no word for what NSLR saw. Scored literally, the most
capable detectors would look like the least reliable ones.

So every pair is compared in the **coarsest vocabulary both declare**, and the
row records which vocabulary that was. The coarsening lattice:

```
microsaccade -> saccade          (the amplitude split collapses)
drift        -> fixation         (slow motion is still not an event)
pursuit      -> fixation         (where one side cannot see pursuit)
pso          -> saccade | fixation      <-- a stated parameter, never a default
```

**The `pso` coarsening is the one that is deliberately not defaulted.** It is
exactly the choice Nyström & Holmqvist say current algorithms fail to offer
and assign "largely arbitrarily", affecting saccade and fixation durations
significantly (§2.5). Making it a comparison parameter means the arbitrary
choice becomes a stated one, and a pair can be scored both ways to show how
much of the disagreement was only ever a convention.

Two consequences worth stating plainly. A pair scored in a coarse vocabulary
is **not** comparable to a pair scored in a fine one, so the vocabulary is in
the row and any report that aggregates across pairs must group by it. And
`n_samples_compared` excludes samples either side called `blink` or `invalid`,
since those come from the shared mask (§2) and are identical by construction —
counting them would inflate every score toward agreement for reasons no
detector is responsible for.

Two arities, kept in two places rather than in one table with nullable halves:

**Pairwise**, keyed `(session, trace, validity_paramset_idx, paramset_a,
paramset_b, metric, vocabulary, pso_as)` with a canonical `a < b` ordering,
since both shipped metrics are symmetric. The validity paramset belongs in the key for the same
reason it belongs in `EyeDetection`'s: two traces are comparable only if they
were masked identically, and a key omitting it could not say which mask a
score was computed under. Ships with:

- `event_f1` — events matched within a tolerance window. What the U'n'Eye
  paper itself reports, so the numbers are comparable to published benchmarks
  rather than only to each other. **The tolerance is the metric's own
  parameter, not a detection paramset's**: it describes how the comparison is
  made, not how either trace was produced, and putting it in a detection
  paramset would make every detector's rows depend on a number no detector
  uses.
- `cohen_kappa` — per-sample and chance-corrected, computed on the stored
  labels directly. It catches boundary disagreement that `event_f1`'s
  tolerance window hides, which is why both ship rather than one.

Each row records `n_samples_compared`, so a pair computed over a
heavily-invalid session is not read as though it were computed over a whole
one.

**N-way**: a per-session `blended_agreement`, kept because parent §7.2 asks
for it, sitting beside the pairwise rows. It is the number an untuned U'n'Eye
will drag down, and the pairwise Engbert–Kliegl ↔ Otero-Millan row is what
stays readable when it does.

---

## 6.5 Saccade vigor

**Vigor is peak velocity relative to what the main sequence predicts for that
amplitude**, and it costs almost nothing to add here because both inputs are
already columns on every saccade run row (§5), measured centrally so they are
comparable across all seven detectors.

It is worth having as more than a curiosity. Shadmehr et al. frame vigor as "a
new, real-time metric with which to quantify subjective utility", where
"expectation of reward increases speed of saccadic eye movements, whereas
expectation of effort decreases this speed"
([10.1016/j.tins.2019.02.003](https://doi.org/10.1016/j.tins.2019.02.003)).
And Choi, Vaswani & Shadmehr measured saccadic vigor "as much as 50% greater
in one subject than another"
([10.1523/JNEUROSCI.2798-13.2014](https://doi.org/10.1523/JNEUROSCI.2798-13.2014))
— so it is a between-individual trait as well as a within-session state, and
what it is normalised against decides which of the two you can see.

### 6.5.1 Store the fit, compute the vigor

The main-sequence fit is stored; **per-saccade vigor is not**. Vigor is a pure
function of `(amplitude, peak_velocity, fit)` and all three are stored, so this
is the same call gaze already gets.

That is not only tidiness. A fit pooled across a subject's sessions would be a
row needing **recomputation every time a new session lands**, and DataJoint
never recomputes a populated key — the permanence trap `EyeCalibration.
key_source` already documents at length. Storing per-session fits sidesteps it
completely: any pooled or rolling normalisation remains computable, forever,
without a single recompute.

Fitted per **trace** and per **detection paramset**, which makes the main
sequence another consensus axis at no extra cost: seven detectors disagreeing
about a subject's main sequence is a different and more interesting signal than
seven disagreeing about individual events.

### 6.5.2 Three grains, and a degenerate-fit guard that is not new

Fits at three grains — **session, block, and trial condition** — as a master
and two parts, mirroring `EyeCalibration`/`BlockResidual`:

| Table | Key | |
|---|---|---|
| `SaccadeMainSequence` | `(session, trace, validity_ps, detection_ps)` | the session-level fit |
| `.Block` | `+ block_id` | per block |
| `.Condition` | `+ block_id, condition` | per condition |

Each row holds `v_max`, the saturation constant, `n_saccades`,
`amplitude_min_deg`, `amplitude_max_deg`, `r_squared`, and a
`fit_status`/`reason` pair.

**The finest grain will often be underpowered, and that is handled by refusing
rather than by fitting anyway.** A saturating two-parameter fit over twenty
saccades spanning 6–9° returns a plausible-looking `v_max` that means nothing —
which is this project's signature defect, and one it has already solved once in
a different costume. **The amplitude span of a set of saccades is the
conditioning of a main-sequence fit**, exactly as the spread of a target
constellation is the conditioning of a calibration: a narrow span cannot pin
the saturation constant for the same reason collinear targets cannot pin an
affine map, and least squares returns a confident answer in both cases.

So the guard has the same shape as `eye/calibration.py`'s, deliberately: a
minimum saccade count **and** a minimum amplitude span, checked in that order,
with a refusal carrying a stated reason. A refused fit is a first-class outcome
here too, never a fabricated one, and `amplitude_min_deg`/`amplitude_max_deg`
are stored so a reader can judge a fit that passed.

Two parameters, both stated rather than assumed: the **fit form** (a saturating
exponential by default) and whether **microsaccades join the fit**. Including
them extends the amplitude range downward and stabilises the saturation
constant; excluding them matches the classical saccade literature. It is a
paramset choice because it changes what the number means.

### 6.5.3 Two consequences worth naming

**PSO handling decides whether vigor means anything.** If a detector's saccade
offset runs into the glissade, the amplitude is inflated and the whole main
sequence shifts. §2.5 was argued from the agreement metric; vigor is a second,
independent reason it matters, and one that bites even if only a single
detector were ever used.

**The synthetic generator cannot express a trial condition yet.**
`Escape.CONDITION` exists in the protocol and `schema/events.py` stores a
`condition` trial attribute, but that module records the gap directly:
"checked: `synth/timeline.py` builds no CONDITION payload". So the
condition-grain fit has nothing to test against until the generator emits
conditions — the same shape as the gap that `SessionRecipe.eye_fixations`
closed for calibration, and it is fixed the same way: correct the fixture.

---

## 7. Schema

| Table | Key | Holds |
|---|---|---|
| `EyeValidity` | `(subject, session_datetime, eye, paramset_idx)` | the mask, as runs; per-criterion rejected fractions |
| `EyeDetection` | `(subject, session_datetime, trace, validity_paramset_idx, paramset_idx)` | status, reason, event counts, label fractions |
| `EyeDetection.Run` | `+ run_index` | `run_start_row, run_end_row, label, amplitude_deg, peak_velocity_deg_s, reliability` |
| `DetectorAgreement` | `(…, trace, validity_paramset_idx, paramset_a, paramset_b, metric, vocabulary, pso_as)` | `value, n_samples_compared` |
| `DetectionQuality` | `(subject, session_datetime)` | `blended_agreement`, session summary |
| `SaccadeMainSequence` | `(…, trace, validity_paramset_idx, paramset_idx)` | `v_max`, saturation constant, `n_saccades`, amplitude span, `r_squared`, `fit_status`, `reason` |
| `SaccadeMainSequence.Block` | `+ block_id` | the same, per block |
| `SaccadeMainSequence.Condition` | `+ block_id, condition` | the same, per condition |

Every one is a `dj.Computed` and every one joins `daemon._computed_tables()` —
the sweep that exists because `TrialCoverage` was once missing from it and
silently returned tier D for every session.

A refused detection is a first-class outcome with a stated reason, never an
error and never a fabricated event list, exactly as `EyeCalibration`'s
`refused` is.

---

## 8. U'n'Eye: the one thing that cannot be reimplemented

**Measured 2026-08-31 against the repository itself**, not assumed:
`berenslab/uneye` declares **`license: null`** — no licence file, no licence
statement in the README. Its last commit is `f97ca88`, 2020-02-29, six and a
half years ago. It has **zero tags or releases**, which is what parent §7.2
means by "no version pins". It is **not on PyPI** (404). The package is
~57 KB of Python (`classifier.py`, `functions.py`) plus six pretrained weight
files of 82–85 KB in `training/`.

**Ruled 2026-08-31: vendor it at that pinned commit anyway** — and it is the
ONLY vendored detector (§3.2). The principle that separates it from the other
six is that its weights are the artifact: an architecture can be rewritten
from a paper, a trained network cannot. Recorded here as a
deliberate departure rather than an oversight, because this repository has
already ruled the other way on the same facts: `eye/bhv2.py` records refusing
to commit 6.5 KB of real `.bhv2` test data because the mirror "declares no
licence… so redistribution rights are unclear". The next person to find both
decisions will otherwise read the difference as an accident. The distinction
being drawn is that the `.bhv2` files were data this project could synthesize
instead, whereas U'n'Eye is the method itself and has no substitute.

- Vendored to `wl_preproc/eye/vendor/uneye/` with a `PROVENANCE.md` recording
  the source URL, the commit, the fetch date, the absent licence, the paper as
  the citation, and that this copy is never redistributed.
- Vendored code is excluded from this repository's own linting and formatting:
  it is third-party, and reformatting it destroys the ability to diff against
  upstream.
- **`torch` is declared in both `wl.yaml` and `pyproject.toml`.** It is
  installed at 2.13.0 today and declared in neither — it arrives transitively
  — which is exactly the case `wlo stack` gets wrong silently. `wl.yaml` gets
  `where: serv` with a `why`, following `kilosort`'s precedent: a CNN detector
  belongs on the preprocessing server, not a rig.
- The weight file is a paramset parameter defaulting to `weights_1+2+3`, and
  which one produced a row is recorded on it. All six are video-tracker
  trained; the distribution-shift caveat lives in the code beside the default.

**Environment isolation is noted, not solved.** Parent §7.2 records that
U'n'Eye and Kilosort are two independently-pinned PyTorch dependents and that
§6.6.1 resolves this with one container per stage. Containers are 2b-1 and
hardware-blocked. Today only one of those two dependents exists, so the
conflict is not live; when Kilosort lands, containers will exist too.

---

## 9. The daily report

A detection section beside the existing eye section, showing the **pairwise**
agreement rows per detector pair, the sessions whose detection was refused
with their stated reasons, and the fraction of each session's samples labelled
`invalid` or `blink`.

**Vigor appears here as a session-versus-history line**, not as a bare number:
this session's fitted main sequence against the median of that subject's prior
sessions. That comparison is computed at report time from stored per-session
fits (§6.5.1), which is exactly why the fits are stored per session and the
normalisation is not — a subject-pooled reference changes with every new
session, and a stored one would go stale the moment it was written. A session
whose vigor drops against its own subject's history is fatigued, disengaged, or
mis-tracked, and the point of putting it beside the agreement rows is that
those three look different from each other there.

Computed in `build_report`, never `gather_readings` — that runs on every
wl.works poll under the lock that also serialises job accepts, and the
responder reads none of these.

---

## 10. Testing

**The synthetic generator can already hold gaze at a stated position**
(`SessionRecipe.eye_fixations`, 2026-08-31), which is what makes a detection
fixture possible at all: a session that holds, steps, and holds again has
ground-truth saccade onsets. That is the fixture this subsystem is tested
against, and the generator gains a step primitive if holds alone prove too
coarse.

**Detection tests must assert event times against planted truth**, not merely
that events were found. The eye plan shipped a suite in which gutting the
entire session-time-to-row alignment left every test green, precisely because
nothing asserted a fitted map was numerically correct; a detector suite that
only counts events has the same hole.

**The tiling invariant is a test, not only an insert check** — runs covering
`[0, n_samples)` exactly, on every trace of every fixture.

**Agreement must be tested against a known answer**: two identical traces
score 1.0, two disjoint traces score 0.0, and a trace offset by less than the
tolerance window scores 1.0 on `event_f1` while scoring below 1.0 on
`cohen_kappa` — which is the specific difference the two metrics exist to
expose.

**U'n'Eye is exercised on both interpreters.** The local suite runs 3.11
alone; CI runs 3.11 and 3.13, and a 2020 package that pins nothing is exactly
the kind that breaks on one and not the other.

**The main sequence is tested against a planted one.** The generator's held
fixations (`SessionRecipe.eye_fixations`) already let a fixture step the gaze
between known positions; giving those steps known peak velocities plants a
main sequence whose parameters the fit must recover — the same
recover-a-known-map discipline the calibration round-trip uses, and the only
thing that distinguishes a fit from a plausible number.

**The degenerate-fit guard needs its own fixture**: a condition whose saccades
all span 6-9 degrees must be REFUSED with a stated reason, not fitted. Without
that test the guard is the kind of code that looks alive and never fires.

**Condition-grain fits cannot be tested until the generator emits conditions**
(§6.5.3). That fixture gap is part of this work, not a prerequisite someone
else supplies.

---

## 11. Open questions

1. **The plausible-region and speed-ceiling values** (§2, criteria 2 and 3)
   have no measured value for this rig. The reference recording can supply a
   first estimate; the lab's own geometry settles it.
2. **U'n'Eye CPU inference time** over a 1.18M-sample recording is unmeasured,
   and this machine has no CUDA. If it is minutes rather than seconds, the
   detector belongs behind the same "slow" marker `test_kilosort_defaults_
   split_units.py` already uses.
3. **The run-count estimate in §5 is not a measurement.** ~126,000 rows per
   session is extrapolated. Measure it before trusting the storage argument.
4. **~~Post-saccadic oscillation.~~ Addressed 2026-08-31**, in §2.5 and as
   the `pso` label — after Deubel & Bridgeman's ≤0.5° lens displacement
   measured on a DPI turned it from a refinement into the artifact most likely
   to poison the metric. What remains open is its magnitude *on this rig*:
   that figure came from a fifth-generation tracker and varies with
   accommodation and age.
5. **Six reimplementations are six opportunities to disagree with the
   literature rather than with each other** (§3.2). Each needs validating
   against published statistics or a permissively-licensed oracle before its
   rows are trusted, and BMD has no oracle at all.
6. **Whether seven detectors is too many to run nightly.** Seven per eye plus
   three conjunctions, over a 1.18M-sample recording, on every session. The
   plan must measure total runtime before this is a nightly stage rather than
   an on-demand one.
5. **Fine-tuning U'n'Eye** on hand-labelled lab data is post-January, per
   parent §7.2. Until then its rows are provisional and the pairwise design
   is what keeps that from contaminating the readable metrics.

---

## 12. Explicitly not in this spec

- **Fine-tuning or retraining U'n'Eye**, and the hand-labelling workflow it
  needs. Parent §7.2 and the phase-4 timeline both place it after January.
- **Drift correction.** Per-block residual measures drift; correcting it is a
  later decision from that evidence, and unchanged by this spec.
- **Gaze-corrected receptive field mapping** (parent §8), which consumes
  detection but is its own subsystem.
- **An eighth detector.** The registry makes one cheap, and adding one is
  now a demonstrated operation rather than a claimed one — four were added to
  this spec on the day it was written. Nothing here argues for another.
- **Tremor.** The third fixational eye movement, alongside microsaccades and
  drift. At 500 Hz it sits at or below the sampling limit, and none of the
  seven detectors claims it.
- **Deconvolving lens wobble from P1 − P4.** Deubel & Bridgeman characterise
  the artifact well enough that modelling it is conceivable; that is research,
  not a pipeline stage. §2.5 excludes the affected window instead.

---

## 13. References

Every figure quoted in this spec was verified against PubMed on 2026-08-31
rather than recalled — this repository's own history records a quotation that
appears in no source, and these numbers drive design decisions.

- Deubel & Bridgeman (1995), *Fourth Purkinje image signals reveal eye-lens
  deviations and retinal image distortions during saccades*, Vision Research
  35(4):529–38. [10.1016/0042-6989(94)00146-d](https://doi.org/10.1016/0042-6989(94)00146-d)
- Nyström & Holmqvist (2010), *An adaptive algorithm for fixation, saccade,
  and glissade detection in eyetracking data*, Behavior Research Methods
  42(1):188–204. [10.3758/BRM.42.1.188](https://doi.org/10.3758/BRM.42.1.188)
- Mihali, van Opheusden & Ma (2017), *Bayesian microsaccade detection*,
  Journal of Vision 17(1):13. [10.1167/17.1.13](https://doi.org/10.1167/17.1.13)
- Pekkanen & Lappi (2017), *A new and general approach to signal denoising and
  eye movement classification based on segmented linear regression*,
  Scientific Reports 7(1):17726.
  [10.1038/s41598-017-17983-x](https://doi.org/10.1038/s41598-017-17983-x)
- Dar, Wagner & Hanke (2021), *REMoDNaV: robust eye-movement classification
  for dynamic stimulation*, Behavior Research Methods 53(1):399–414.
  [10.3758/s13428-020-01428-x](https://doi.org/10.3758/s13428-020-01428-x)
- Bellet, Bellet, Nienborg, Hafed & Berens (2019), *Human-level saccade
  detection performance using deep neural networks*, J Neurophysiol.
  [10.1152/jn.00601.2018](https://doi.org/10.1152/jn.00601.2018) — cited by
  parent §7.2; not re-verified here.
- Shadmehr, Reppert, Summerside, Yoon & Ahmed (2019), *Movement Vigor as a
  Reflection of Subjective Economic Utility*, Trends in Neurosciences
  42(5):323-336. [10.1016/j.tins.2019.02.003](https://doi.org/10.1016/j.tins.2019.02.003)
- Choi, Vaswani & Shadmehr (2014), *Vigor of movements and the cost of time in
  decision making*, J Neurosci 34(4):1212-23.
  [10.1523/JNEUROSCI.2798-13.2014](https://doi.org/10.1523/JNEUROSCI.2798-13.2014)
- Engbert & Kliegl (2003) and Otero-Millan et al. (2014)
  [10.1167/14.2.18](https://doi.org/10.1167/14.2.18) — both cited by parent
  §7.2; not re-verified here.

Licence and maintenance facts (§3.2, §8) were read from the GitHub and PyPI
APIs on 2026-08-31, not assumed.
