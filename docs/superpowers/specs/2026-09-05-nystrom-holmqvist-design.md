# Nyström–Holmqvist: the first detector that produces a glissade

**Design spec, 2026-09-05.** Implements the third of design spec
`2026-08-31-saccade-detection-design.md` §3.1's seven detectors, and the
first one that emits any label beyond the amplitude split.

Depends on the per-kind conjunction (`2026-09-05-conjunction-shape-design.md`,
merged `76a8199`) and on `registry.Detector` carrying its own defaults
(merged `19daf07`). Without the first, a `pso`-emitting detector could not
produce a conjunction trace; without the second, registering a third detector
raised `KeyError` and killed the whole daemon pass.

**The paper was read, not recalled.** Nyström & Holmqvist (2010),
*Behavior Research Methods* 42(1), 188–204,
[10.3758/BRM.42.1.188](https://doi.org/10.3758/BRM.42.1.188). Every constant
and rule in §1 is quoted or paraphrased from its Tables 1–2 and its
"A New Algorithm" section, with the page noted. §3.2's warning is the reason:
*"a buggy reimplementation is indistinguishable from a genuine detector
disagreement... it does not look like a defect — it looks like a finding, and
it looks like exactly the finding this subsystem exists to surface."*

---

## 0. Why this detector, and why now

Three reasons it is the right third detector rather than any of the other
four outstanding.

**It is the one that measures the open question.** The conjunction spec's
open question 1 — how often the two eyes disagree about an event's KIND — is
the largest piece of unquantified reasoning in the merged design. Per-kind
intersection drops those spans, and nobody knows whether that is 0.1% or 10%.
Measuring it requires a detector that emits more than one kind, and this is
the simplest of the four that do. See §6.

**It is the first detector to exercise the conjunction machinery in
production.** Everything merged on `76a8199` — the kind map, per-kind
grouping, the saccadic slice — has been tested only against fixture
detectors. This is the first real one.

**It names the artifact this rig actually has.** §2.5 of the parent spec
argues from Deubel & Bridgeman that post-saccadic oscillation reaches 0.5° of
retinal image displacement on a dual-Purkinje tracker, against a 1.0°
microsaccade threshold, after every saccade. Engbert–Kliegl and Otero-Millan
cannot say the word. This detector can.

## 1. The algorithm, as the paper specifies it

Table 1's pseudocode gives five steps: filter and denoise, iteratively find
velocity peaks, detect saccades, detect glissades, detect fixations.

### 1.1 The adaptive peak threshold (p. 193, Figure 4)

The algorithm's central novelty, and what makes it "settings-free for the
user":

1. Choose an initial peak velocity detection threshold `PT₁`. The paper puts
   it "in the range 100°–300°/sec, but the choice is not critical as long as
   there are saccades with peak velocities reaching this threshold."
2. Over all samples with velocity below `PTₙ₋₁`, compute the mean `μ` and
   standard deviation `σ`.
3. Update `PTₙ = μₙ₋₁ + 6σₙ₋₁`.
4. Iterate until `|PTₙ − PTₙ₋₁| < 1°/sec`.
5. The converged value is the peak threshold `θ̇_PT`.

The paper reports this converging in about two iterations on its reading
data, where fixation velocity was 5.44 ± 4.55°/sec, "giving peak velocity
thresholds around 33°/sec (but the individual variation was large across
participants)."

**The 6σ margin is not arbitrary**: the paper calls it "a good robust level"
and notes it "is also used in microsaccade detection algorithms (Engbert &
Kliegl, 2003)" — the same 6 this repository's own Engbert–Kliegl detector
already uses.

### 1.2 Saccade onset and offset (p. 194, Figure 5)

For each velocity peak above `θ̇_PT`, search backward from its leftmost
sample and forward from its rightmost.

**Onset** is the first sample going below
`θ̇_ST^onset = μ_z + 3σ_z` where `(θ̇ᵢ − θ̇ᵢ₊₁) ≥ 0` — Figure 5A describes
this as searching backward "until the first local minimum is found."

**Offset is the adaptive part, and the reason this algorithm exists.** The
threshold combines the trial-wide noise with a LOCAL estimate:

```
θ̇_t          = μ_t + 3σ_t          over a τ_min window PRECEDING the saccade
θ̇_ST^offset  = α·θ̇_ST^onset + β·θ̇_t      α = 0.7, β = 0.3   (Table 2)
```

Offset is the first sample below `θ̇_ST^offset` where `(θ̇ᵢ − θ̇ᵢ₊₁) ≤ 0`.

**The window PRECEDES the saccade, and the paper says why:** "To avoid
contamination from glissadic movements." A window after the saccade would
measure the glissade and raise the threshold that is supposed to find it.
This is the single easiest thing to get backwards, and getting it backwards
would suppress exactly the events this detector exists to produce.

### 1.3 Rejections (Table 2, p. 194)

- Velocity above **1,000°/sec** or acceleration above **100,000°/sec²** is
  "not physiologically possible" and is dropped before detection.
- Saccades shorter than the **minimum saccade duration, 10 msec**, are
  discarded — "large enough to avoid noise being falsely categorized as
  saccades but small enough to include short saccades (~1°)."
- Saccades "preceded by a period where `μ_t > θ̇_PT`" are excluded, "since
  this indicates that there was no period of stillness prior to the saccade
  onset (most often, indicating recording imperfections)."

### 1.4 Glissade detection (p. 195)

Within a window of `τ_min` (40 msec) after the saccade offset, the paper
gives **two definitions, and defines them as mutually exclusive** — "that is,
low-velocity glissades are not a subset of high-velocity glissades":

- **High-velocity**: the velocity curve rises above the peak saccade
  threshold `θ̇_PT` and back below it at least once. "In other words, a
  high-velocity glissade has a velocity peak that would qualify it for
  saccadic status."
- **Low-velocity**: identical, except the curve need only rise above the
  saccade offset threshold `θ̇_ST^offset`.

Onset is the offset of the preceding saccade. Offset is where
`(θ̇ᵢ − θ̇ᵢ₊₁) ≤ 0` after the last velocity peak sample in the glissade.
**Glissades with an amplitude larger than their preceding saccade are
omitted.**

**Both criteria are implemented, and both emit `pso`.** This spec originally
proposed a paramset key choosing between them; reading Figure 10 removed the
need. See §3.

### 1.5 Fixation detection (pp. 195–197)

"Fixations are everything that is not noise, saccades, or glissades" —
subject to a minimum duration `τ_min`. The paper sets **`τ_min` = 40 msec**,
having "manually identified several oculomotor fixations in the data,
especially during reading, with durations below 50 msec."

## 2. The velocity estimator: the shared one, deliberately

**The paper specifies Savitzky–Golay** (Table 2: `sgolay`, order 2, length
2× minimum saccade duration), chosen because it "is reported to have a good
performance in terms of preserving high-frequency detail in the signal."

**This implementation uses the repository's shared estimator instead**
(`eye/detect/velocity.py`, Engbert & Kliegl's five-point differentiator), and
that is a deliberate, recorded deviation.

Parent §3.2's first stated benefit of reimplementing rather than vendoring is
"one shared velocity estimator across all seven rather than seven private
ones." That rule exists so that a disagreement between two detectors is an
ALGORITHMIC disagreement. Give this detector its own filter and every
Nyström–Holmqvist-versus-Engbert–Kliegl number in the consensus suite becomes
partly a comparison of two filters — which is precisely the confound §3.2
warns that a reimplementation defect hides behind.

**Why the deviation is expected to be tolerable:** this algorithm is adaptive
by construction. `θ̇_PT` converges to `μ + 6σ` of whatever noise distribution
it is handed, so a different estimator moves the threshold rather than
breaking the method. That property is the paper's own selling point.

**Why it might not be, and how that will be visible:** glissades are
low-amplitude wobbles of ~20 msec. If the shared estimator smooths them away,
this detector finds none. §5's checkable prediction is what surfaces that —
a near-zero glissade rate on the reference recording indicts the estimator
first, and switching to Savitzky–Golay then becomes evidence-driven rather
than pre-emptive. **Do not switch it pre-emptively.**

## 3. Both glissade criteria, and the number that says so

**Table 3 reports a single "% glissadic saccades" — 47.8 for reading, 59.1
for scene perception — and those are the union of the two criteria, not
either one alone.**

This is an INFERENCE from Figure 10, not a quotation, and it is marked as
such because the paper does not state it in words. Figure 10A plots the two
criteria separately per participant for reading: low-velocity proportions run
roughly 0.20–0.44, high-velocity roughly 0.05–0.30. Neither alone reaches
0.478; summed, they land about there, which is what "mutually exclusive"
predicts.

**The consequence for this implementation is concrete.** A detector
implementing only the low-velocity criterion would produce roughly two thirds
of the published rate and would look broken against §5's check while being
correct. So both are detected, and both emit `pso` — a distinction our
eight-label vocabulary cannot express anyway (parent §1).

**If §5's measurement lands far from 47.8% with both criteria implemented,
this inference is the first thing to re-examine**, before the estimator and
before the algorithm.

## 4. What it emits, and what that reaches

Declared vocabulary `{saccade, pso, fixation}` — parent §3.1's own table,
unchanged by this spec.

**It emits `fixation` explicitly**, which no registered detector has done.
`_insert_trace` already synthesizes `fixation` for unclaimed samples, so an
explicitly emitted one is not new to storage. What IS new is that
`_conjunction_runs` must treat it as background: `_KIND_OF` maps it into
`_NOT_INTERSECTED`, so left-fixation never crosses right-saccade. That path
is built and tested (conjunction spec §1.2, §2) and this is its first
production exercise.

**Its saccadic slice is `{saccade}`**, so its conjunction runs take the
DEGENERATE branch of `_conjunction_label` — the constant, never `classify`'s
other answer. It joins U'n'Eye and BMD there; five of the seven land in that
branch and only Engbert–Kliegl and Otero-Millan reach `classify`.

**No `microsaccade`.** This detector cannot say the word, so `classify` is
never asked and `microsaccade_max_deg`'s `KeyError` guard is deliberately not
reached — `_conjunction_label`'s own reasoning, unchanged.

## 5. Validation: the paper's own numbers, and what transfers

Parent §3.2: "Published output statistics are checkable predictions. Nyström
& Holmqvist report glissades in about half of saccades at ~24 ms mean
duration; a reimplementation that produces neither is wrong regardless of
whether it runs."

Table 3's figures, reading / scene perception:

| Measure | Reading | Scene perception |
|---|---|---|
| % glissadic saccades | 47.8 | 59.1 |
| Glissade duration (msec) | 22.2 ± 9.8 | 25.0 ± 9.8 |
| Fixation duration (msec) | 193.7 ± 100.0 | 263.6 ± 185.4 |
| Saccade duration (msec) | 42.5 ± 18.0 | 47.2 ± 16.8 |
| Fixation velocity (°/sec) | 5.44 ± 4.55 | 5.40 ± 3.97 |

**What transfers and what does not.** The paper's data are HUMAN, reading and
scene perception, at 1250 Hz on an SMI HiSpeed. This rig is NHP, on a
free-viewing or task paradigm, at 500 Hz on a dual-Purkinje tracker. Fixation
and saccade DURATIONS are behaviour and will not transfer. **The glissade
statistics are the ones with a mechanistic reason to transfer** — a glissade
is lens wobble, a property of the eye and the instrument rather than the
task, and §2.5 argues a DPI should show MORE of it than the video-based
systems the paper used, not less.

**So the checkable prediction, stated before measuring**: glissades in a
substantial minority-to-majority of saccades, with durations in the low tens
of milliseconds. A rate near zero, or durations of hundreds of milliseconds,
falsifies the implementation.

**And the rule that binds this, from the Otero-Millan round:** *an
oracle-free statistic is worthless until a null has been run against it.*
Before any of the above is used to accept the detector, a null must be run —
a duration-matched random-span control, at minimum. Stage 2A withdrew a check
rather than relaxing it when a random control and a deliberately broken
detector both scored higher than the correct one. Build the null first.

**REMoDNaV as a test-time oracle.** Parent §3.2: "REMoDNaV is MIT and on
PyPI: a development dependency used to check our output, never a runtime
dependency and never shipped." Parent §11 adds that it "remains a genuine
runnable oracle" — the surviving one, after Otero-Millan's and BMD's
references were found to be unusable as such. **It is an oracle, not a
specification** — REMoDNaV deliberately changed parts of the method, so
agreement with it is evidence and disagreement with it is not automatically a
defect.

**Task 7's status, 2026-09-06: the null was run and measured; the reference-
recording checks were not.** All four checks above are implemented in
`tests/eye/detect/test_nystrom_holmqvist_validation.py` — the null first, as
this section requires, then the glissade-rate band, the glissade-duration
band, and the REMoDNaV comparison, each gated on `WLPP_OHDPI_REFERENCE`.

The null (`test_the_null_fails_the_glissade_rate_check`) is the one measurement
this task actually made, and it passes: a duration-matched random-span
control, 500 fake saccades and 500 fake glissades placed uniformly at random
over 500,000 samples, scores 0.016 at the test's own pinned seed (7), and
0.008–0.030 across seeds 0–19 measured directly — both far under the 0.10
ceiling and nowhere near the paper's 47.8%. Mutating the check to drop its
`tau_samples` window (counting any later glissade anywhere in the recording,
not one within the paper's own adjacency window) raises the same control to
0.998, confirming the test would catch a version of this statistic that no
longer discriminates, not only pass one that does. The glissade-rate check is
therefore established as real by this section's own standard, before it is
ever pointed at a real recording.

**The three reference-recording checks were not executed.**
`WLPP_OHDPI_REFERENCE` was unset in this task's environment, so all three are
correctly SKIPPED rather than run — this was expected going in, not
discovered partway through, and the task that wrote them was explicit that
claiming a measurement never made would be worse than leaving one out.
Separately, and for the same reason REMoDNaV's own check could not be run
either way here: this task's project `.venv` has no `pip` installed at all,
so REMoDNaV could not be installed into it regardless of the recording's
availability. The table above therefore still records PREDICTIONS, not
measurements — nothing in it has been confirmed or refuted against this rig's
own data. Whoever next has the reference recording and a normal venv should
run

    WLPP_OHDPI_REFERENCE=/path/to/OpenIris-2024Jul31-114628.txt \
        .venv/bin/python -m pytest tests/eye/detect/test_nystrom_holmqvist_validation.py -v

and record what comes back here, replacing this paragraph rather than
appending beside it — the discipline `test_otero_millan_validation.py` and
this file's own §9 item 3 both name: a number without its configuration
stated beside it is how this document has twice carried a wrong one.

## 6. The measurement this unblocks

The conjunction spec's open question 1: how often do the two eyes disagree
about an event's KIND? Per-kind intersection drops those spans, and the rate
is unmeasured.

**It must be measured from the PER-EYE traces, never from the conjunction.**
When the eyes disagree on kind, no intersection covers those samples, so
`_insert_trace`'s fill paints them `fixation` — indistinguishable in the
stored conjunction trace from genuine binocular fixation. Measuring off the
conjunction returns zero, silently. The conjunction spec records this; it is
repeated here because this is the spec whose detector finally makes the
measurement possible.

**The measurement is not in this spec.** It is the first thing to do once
this detector produces rows, and it wants its own design once there is data
to look at. Recorded here so it is not lost.

## 7. Parameters

One frozen dataclass, `NystromHolmqvistParams`, carried on the registry entry
per `registry.Detector.defaults`. Every value below is the paper's, from
Table 2, and none is tuned:

| Field | Value | Source |
|---|---|---|
| `initial_peak_threshold_deg_s` | 200.0 | midpoint of the paper's stated 100–300 range; "not critical" |
| `peak_threshold_sigma` | 6.0 | Table 2, `θ̇_PT = μ_z + 6σ_z` |
| `onset_threshold_sigma` | 3.0 | Table 2, `θ̇_ST^onset = μ_z + 3σ_z` |
| `local_noise_sigma` | 3.0 | Table 2, `θ̇_t = μ_t + 3σ_t` |
| `offset_alpha` | 0.7 | Table 2, α |
| `offset_beta` | 0.3 | Table 2, β |
| `convergence_deg_s` | 1.0 | p. 194, iterate until `|PTₙ − PTₙ₋₁| < 1°/sec` |
| `max_iterations` | 100 | NOT from the paper — see §9 item 2 |
| `min_saccade_duration_ms` | 10.0 | Table 2 |
| `min_fixation_duration_ms` | 40.0 | Table 2, `τ_min` |
| `max_velocity_deg_s` | 1000.0 | Table 2 |
| `max_acceleration_deg_s2` | 100000.0 | Table 2 |

`min_duration_samples` is NOT a field here. `_min_duration_samples` reads it
off the params object with a `getattr` default of 1 and its own docstring
already states that stage 2's detectors "each bring their own params
dataclass, and some of them have no minimum duration to declare." This
detector's minimum is expressed in milliseconds, because the paper expresses
it that way and because a sample count is wrong at a different `fs_hz`.

**Nothing here is tuned against the synthetic generator.** The generator
carries planted ground truth, which makes tuning easy and wrong: it would fit
the fixture rather than the eye. Published values go in; measurement comes
out; any change to a value is a new paramset with its reason recorded.

## 8. Testing

Test-driven, following `test_otero_millan.py`'s split between unit tests of
the algorithm and a separate validation module.

1. **The threshold iteration converges**, and to the paper's own arithmetic:
   on synthetic velocity with known μ and σ, `θ̇_PT` lands at `μ + 6σ` within
   the 1°/sec criterion, from several starting values across 100–300, since
   the paper says the start "is not critical."
2. **The iteration terminates on data that cannot converge** — all-noise, or
   a distribution where the update oscillates. §9 item 2.
3. **The offset window precedes the saccade, not follows it.** A fixture with
   a large glissade after the saccade and quiet before it: a following window
   raises `θ̇_ST^offset` and truncates or loses the glissade. This is §1.2's
   easiest-to-invert rule and the test exists to catch the inversion.
4. **Both glissade criteria fire, and are mutually exclusive** — a
   high-velocity glissade is not also counted as a low-velocity one.
5. **A glissade larger in amplitude than its preceding saccade is omitted.**
6. **A saccade not preceded by stillness is excluded** (`μ_t > θ̇_PT`).
7. **Emitted labels are a subset of the declared vocabulary** — enforced by
   `registry.Detector.detect`, asserted here so the declaration is checked
   against real output rather than a fixture's.
8. **The conjunction is produced, and carries `pso`** — the first production
   exercise of per-kind intersection. Through `daemon.run_once()`, never
   `make()` by hand.
9. **Validation, in its own module**: the §5 statistics against the reference
   recording, each with a null run against it first.

## 9. Open questions

1. **Whether the shared velocity estimator preserves glissades on this rig**
   (§2). Unmeasurable until this detector runs. The §5 glissade rate is what
   answers it. **Still open after task 7**: the check exists
   (`test_the_glissade_rate_is_in_the_papers_band`) and its null passed, but
   `WLPP_OHDPI_REFERENCE` was unset in that task's environment, so it has
   never been run against the reference recording — see §5's own "Task 7's
   status" note. **Not answered by the `stepped_session` synthetic fixture
   either**, and this is worth stating explicitly because it is easy to
   mistake for an answer: task 6 found that fixture's conjunction carries no
   `pso` at all, but its planted transitions are constant-velocity ramps
   with a hard stop and no post-saccadic excursion, so a glissade is
   impossible there by construction. A zero rate on a fixture built without
   any wobble to find and a zero rate from a velocity estimator that smooths
   real wobble away would look identical from that fixture alone — which is
   exactly why this item can only be closed by the reference recording, not
   by synthetic data.
2. **The paper states no iteration cap.** It reports convergence "in about
   two iterations" and gives a convergence criterion, but a distribution that
   oscillates would loop forever. `max_iterations = 100` is this
   implementation's own guard, not the paper's, and what to do on reaching it
   — raise, or accept the last value — is decided in the plan, not here.
   Flagged because it is the one place this implementation adds a rule the
   paper does not have.
3. **Whether Table 3's 47.8% is the union of both criteria** (§3). Inferred
   from Figure 10, not stated. If the measured rate is far off, re-examine
   this before the estimator or the algorithm. **Still open after task 7**,
   for the identical reason item 1 is: the check that would settle it ran its
   null successfully but never ran against the reference recording in that
   task's environment. Nothing in task 7 either confirms or refutes the
   inference.
4. **How the paper's noise on/offset rule interacts with this repository's
   validity mask.** The paper detects noise on/offset "when the velocity
   reaches the median value of the velocities over the whole trial";
   `eye/detect/validity.py` already computes a five-criterion mask that
   `detect()` receives as `available`. Two noise definitions now exist for
   one trace. The plan must decide whether the paper's rule is applied on top
   of the mask or dropped in favour of it, and record which.
5. **NHP versus human.** Every published number in §5 comes from humans
   reading and viewing scenes. Saccade and fixation durations will not
   transfer; the glissade statistics have a mechanistic reason to (§5), but
   "reason to" is not "will."

## 10. References

- Nyström, M., & Holmqvist, K. (2010). An adaptive algorithm for fixation,
  saccade, and glissade detection in eyetracking data. *Behavior Research
  Methods*, 42(1), 188–204. [10.3758/BRM.42.1.188](https://doi.org/10.3758/BRM.42.1.188)
- Deubel, H., & Bridgeman, B. (1995). Fourth Purkinje image signals reveal
  eye-lens deviations and retinal image distortions during saccades. Parent
  spec §2.5's source for the 0.5° figure.
- Niehorster, D. C. `dcnieho/NystromHolmqvist2010` — a MATLAB implementation
  from the authors' own institution, "with extensions." **Declares no
  licence**, so it is readable as a specification exactly as Otero-Millan's
  MATLAB was (parent §3.2's correction of 2026-09-01: "reading it is not
  redistributing it") and usable as neither dependency nor oracle. Not
  consulted for this spec, which was written from the paper.
