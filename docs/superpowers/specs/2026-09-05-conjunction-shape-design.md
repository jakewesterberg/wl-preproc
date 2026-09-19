# The conjunction has the same shape as the eyes it is made from

**Design spec, 2026-09-05.** Amends §4 and §5.1 of
`2026-08-31-saccade-detection-design.md`, and **withdraws** the question
`docs/handoffs/2026-09-02-the-conjunction-label-decision.md` briefed rather
than answering it.

This spec revises **already-merged, already-reviewed code** — stage 1
(`d224632`) and stage 2A (`a8fd3cb`), CI green on 3.11 and 3.13. It adds no
table, no column, no paramset key and no parameter.

---

## 0. The decision that turned out not to need making

The handoff asked: *what label should a `conjunction` run carry when the
detector can emit `pso`?* It listed four candidate conventions and recorded
that four detectors — Nyström–Holmqvist, NSLR, REMoDNaV and Bayesian
microsaccade detection — were blocked until one was chosen.

**The premise was wrong, and the ruling is that none of the four is needed.**

The question rests on the claim that the conjunction is *derived*, so no
detector supplies its label. The first half is true. The second does not
follow: when both eyes independently call the same stretch `pso`, their
**agreement on kind** supplies the label. The conjunction stores `pso` as
`pso`, and there is no glissade assignment to state because nothing is being
assigned.

What actually blocked the four detectors was a defect in
`schema/detect.py::_overlapping`, which made the label question look
unanswerable. §2.

**Ruled by the lab owner, 2026-09-05**: the conjunction trace must have the
same shape as the per-eye traces it is built from, and `pso_as` stays where
§6.1 already put it — a comparison parameter, and nothing else.

## 1. The rule

**A conjunction run is the temporal intersection of two runs of the same
kind, and it carries that kind's label.**

`saccade` and `microsaccade` are **one** kind. §1 calls them *"a split, not a
ranking"* — the same event distinguished only by size — so they intersect
together and the surviving span is labelled by `classify` on its own measured
amplitude, exactly as today. Every other emitted label is its own kind and
intersects only with itself.

The conjunction's vocabulary is therefore the detector's vocabulary. Nothing
a detector found in both eyes is dropped, folded into a neighbour, or
renamed.

### 1.1 Why this is not the arbitration that was removed

Stage 1's review removed a rule that ranked the two eyes' labels through
`labels.py::PRECEDENCE`. That rule was wrong three ways, all measured against
the reference recording: it ranked a split §1 says is never in contention
(fired on 593 of 4,550 intersections, 13.0%); it let `saccade` outrank `pso`
and so assigned the glissade silently; and it left the stored `label`
contradicting the stored `amplitude_deg` on 12.3% of conjunction event rows
(518 of 2,209 `saccade` rows below threshold, 40 of 2,341 `microsaccade` rows
at or above).

**Requiring agreement is a different operation from ranking disagreement**,
and it avoids all three:

1. Nothing is ranked. Two kinds that differ produce no conjunction run of
   either kind.
2. Nothing is assigned. A glissade is stored as a glissade.
3. Label and amplitude are still derived once, from the same interval, on the
   same trace — the invariant that fix established is untouched.

### 1.2 `fixation` is not intersected

`fixation` is the synthesized background: `_insert_trace` paints every sample
no interval claimed, and that is unchanged. Intersecting it as a kind would
be redundant — a region survives as `fixation` either way, whether it is
painted by an intersection or by the fill — and it would run the nested loop
over the largest runs in the trace for no observable difference.

Every other emitted label **is** intersected, including `drift`, which is
Bayesian microsaccade detection's own background. The shape rule does not
have exceptions for labels that happen to be common; §1.1's principle is that
the conjunction says what the eyes said.

`blink` and `invalid` never appear here at all — they come from the validity
mask, never from a detector, and are not in any vocabulary.

### 1.3 Grouping by kind makes the intersection cheaper

`_overlapping`'s nested loop is `O(|left| x |right|)` today, over every run in
both eyes. Grouping by kind first partitions both sides, so the work becomes
the sum over kinds of the product within each kind — strictly less than the
product of the totals whenever more than one kind is present, and identical
when only one is.

For Engbert–Kliegl and Otero-Millan, which emit one kind, this is exactly
today's loop.

## 2. The defect this fixes, and why no existing test could catch it

`_overlapping` intersects on time and never reads a label:

```python
for left_run in left:
    for right_run in right:
        start = max(left_run.start, right_run.start)
        stop = min(left_run.stop, right_run.stop)
        if stop - start >= floor:
            intersections.append((start, stop))
```

That is correct **only** while every emitted label is the same kind of thing.
Engbert–Kliegl and Otero-Millan emit nothing but saccadic events, so a blind
time-intersection is the binocular criterion, and stage 1 and stage 2A are
right.

§3.1 gives Nyström–Holmqvist, NSLR and REMoDNaV `fixation` in their emitted
vocabularies, and fixations tile the recording. Blind intersection would
cross a left fixation with a right saccade and keep the result as a binocular
event. All four blocked detectors emit at least one non-saccadic label, so
every one of them would have hit this.

**No stage-1 or stage-2A test could have found it**, because finding it
requires a detector that emits more than one kind and neither stage has one.
The subsystem's own completeness claim — set equality between `DETECTORS` and
the registered paramsets — is silent here too: both detectors are registered
and both are correct.

This is the second defect in this subsystem visible only from outside a
per-task green. The first was stage 1's whole-branch review finding that
nothing in production registered the detection paramsets at all.

## 3. What each detector produces

| Detector | Emits (§3.1) | Conjunction kinds | Saccadic label |
|---|---|---|---|
| Engbert–Kliegl | saccade / microsaccade | saccadic | `classify` — unchanged |
| Otero-Millan | saccade / microsaccade | saccadic | `classify` — unchanged |
| U'n'Eye | saccade | saccadic | degenerate — unchanged |
| Nyström–Holmqvist | saccade / pso / fixation | saccadic, pso | degenerate |
| NSLR | saccade / pso / pursuit / fixation | saccadic, pso, pursuit | degenerate |
| REMoDNaV | saccade / pso / pursuit / fixation | saccadic, pso, pursuit | degenerate |
| Bayesian microsaccade | microsaccade / drift | saccadic, drift | degenerate |

**Five of the seven take the degenerate branch**, and only Engbert-Kliegl and
Otero-Millan declare both sides of the amplitude cut. That branch arrived as a
fix-round finding about U'n'Eye and was reasoned about as an edge case; under
this spec it is the majority path, and the three detectors joining U'n'Eye and
BMD there all declare `saccade` without `microsaccade`. Two consequences worth
stating rather than discovering: `classify` governs two detectors' conjunction
rows, not seven; and `microsaccade_max_deg`'s `KeyError` guard is by design not
reached for a degenerate split, so five of seven `eye_detection` paramsets are
never asked for a threshold that could not have changed their answer.

The saccadic kind keeps the labelling rule `_conjunction_label` already has,
including its **degenerate-split** branch: a detector declaring one side of
the amplitude cut gets that side constantly, never `classify`'s other answer,
because `registry.Detector.detect` would refuse that answer from the detector
itself. U'n'Eye (`{saccade}`), Nyström–Holmqvist, NSLR and REMoDNaV (each
declaring `saccade` without `microsaccade`), and BMD (declaring
`microsaccade` without `saccade`, alongside `drift` — its vocabulary is
`{microsaccade, drift}`, not `{microsaccade}` alone) are the five degenerate
cases the table above counts, and the existing reasoning covers all of them
unchanged.

Every non-saccadic kind labels itself. There is no rule to write.

### 3.1 The subset raise is replaced, not kept

`_conjunction_label` raises `UndecidedConjunctionLabel` when a vocabulary is
not a subset of `{saccade, microsaccade}`. That guard existed because **one**
label had to cover a mixed vocabulary. Under §1 each kind labels itself, so
the mixed case does not arise and the guard is unreachable — dead code
asserting a constraint the design no longer has.

It is replaced by an **exhaustiveness guard on the kind map**: a label that
reaches `_overlapping` with no kind assigned raises. That is reachable and
worth having. §1 declares all eight labels because the migration window
closes January 2027, and this catches a ninth added without updating the map
— exactly the partial change the closed enum exists to make loud.

**The empty-vocabulary guard stays as it is.** Its reasoning is different and
still holds: `frozenset() <= anything` is `True`, so a detector declaring
nothing would pass any subset test while `registry.Detector.detect` refuses
every label it emits.

## 4. What does not change

The bar for this change is that it is invisible where it should be
invisible:

- **Engbert–Kliegl, Otero-Millan and U'n'Eye produce byte-identical
  conjunction rows.** All three emit one kind, so §1's grouping is a
  partition into one group and the loop is today's loop. Stage 2A's measured
  figures — 4,700 Otero-Millan events against Engbert–Kliegl's 5,972,
  `event_f1` 0.8012 left and 0.7834 right — are merged and reported, and
  moving them silently would invalidate a published comparison. This is a
  test, not an expectation.
- **`pso_as` remains a comparison parameter only**, in
  `DetectorAgreement`'s primary key, applied to stored labels at scoring
  time. Because the conjunction now stores `pso` natively, exactly as the
  per-eye traces do, every trace reaching the comparison layer carries raw
  labels and the convention is applied **once**, in one place. Storing a
  converted label would have been the second place.
- **No paramset change.** No new key, no new paramset, no new hash, no
  re-population, no migration.
- **The duration floor**, `_min_duration_samples`, and the coalescing of
  touching spans — now applied within each kind, where the reasoning holds
  unchanged: two touching runs of one label are one run to
  `runs_from_labels`, so coalescing first is what keeps the labelled span
  identical to the measured one. Across kinds the labels differ, so
  `runs_from_labels` cannot merge them and no coalescing is wanted.
- **The left eye deciding the conjunction's gaze**, with §5.1's recorded and
  unresolved asymmetry — measured at 242 of 4,550 conjunction event rows
  (~5%) carrying a different verdict had the right eye been named.
- **`_run_row` measuring amplitude for `saccade`/`microsaccade` only.** A
  `pso`, `pursuit` or `drift` run stores `NULL` amplitude, so §1.1's third
  defect is unreachable for them: there is no amplitude to contradict.

## 5. Testing

Test-driven, per the repo's workflow. The load-bearing cases:

1. **Byte-identical conjunction rows** for the three single-kind detectors,
   before and after. §4's bar, asserted directly against the reference
   recording.
2. **A left fixation crossed with a right saccade produces no conjunction
   span** — the §2 defect, which no existing test could reach.
3. **A binocular glissade survives as `pso`**, not as a saccade and not
   dropped: a synthetic fixture where both eyes carry an overlapping `pso`
   run.
4. **A left `saccade` overlapping a right `pso` produces no conjunction run
   of either kind** — §1's agreement requirement, stated as behaviour rather
   than left implicit.
5. **The conjunction's vocabulary equals the detector's** on a multi-kind
   fixture, minus labels the detector emitted nowhere. This is the shape rule
   itself and is the test this spec exists to make pass.
6. **A `pso` run stores `NULL` amplitude** (§4).
7. **The exhaustiveness guard raises** for a label with no kind (§3.1).

A fixture, not the reference recording, for 3–5 and 7: **no registered
detector emits anything but saccadic events**, so multi-kind behaviour cannot
be exercised against real data until stage 2B writes one.

**A whole-branch review before merge**, and **CI on both interpreters before
believing any of it.** The venv is 3.11; CI runs 3.11 and 3.13; the eye merge
left CI red on 3.13 alone for a day while every local run was green.

## 6. Open questions

1. **How often the two eyes disagree on kind is unmeasured**, and
   unmeasurable until a pso-capable detector exists. §1's agreement
   requirement drops those spans. The rate is the number that says whether
   that is conservative or costly, and Nyström–Holmqvist — the simplest of
   the four — is what measures it. **This is the largest piece of
   unquantified reasoning in this spec.**

   **And it is not merely unmeasured — it is unrecoverable from the
   conjunction trace itself, which is worth stating rather than
   discovering.** `_insert_trace` paints `fixation` over every sample no
   surviving interval claims. A stretch where the left eye says `saccade`
   and the right says `pso` produces no conjunction interval of either
   kind — the two runs are different kinds, so neither eye's group in
   `_conjunction_runs` intersects the other's — and that stretch is
   therefore stored as `fixation`, indistinguishable in the conjunction
   trace from a stretch where both eyes genuinely found nothing. A query
   against the conjunction trace alone would report this rate as exactly
   zero, silently, rather than as unmeasured. The rate can only be
   recovered by comparing the two PER-EYE traces directly — which is
   exactly what Nyström–Holmqvist's own measurement, above, has to do.
   **ANSWERED 2026-09-12, against the real reference recording. The
   question above is left standing rather than rewritten, per this
   repository's convention; everything from here down is the answer.**

   Measured by `tests/eye/detect/test_nystrom_holmqvist_validation.py::
   test_the_two_eyes_agree_on_kind_far_better_than_chance`, from the PER-EYE
   Nyström–Holmqvist traces exactly as this section requires, on
   `OpenIris-2024Jul31-114628.txt` (1,177,799 frames, 498.55 Hz):

   | direction | agree | disagree | alone | kind disagreement | drop rate |
   |---|---|---|---|---|---|
   | left→right | 5,763 | 721 | 595 | **0.1112** of 6,484 compared | **0.1859** of 7,079 detected |
   | right→left | 5,792 | 855 | 733 | **0.1286** of 6,647 compared | **0.2152** of 7,380 detected |

   **The null was built and run first**, per the rule the Otero-Millan round
   left behind. Two traces sharing only a kind mix already agree by accident:
   randomly placed runs preserving each eye's durations AND kinds disagree
   **0.376–0.470** of the time across seeds 0–19 (0.446 at the pinned seed
   7). The real eyes disagree 0.111–0.129 — about three and a half times less
   often than chance — so the statistic is measuring binocularity rather than
   the vocabulary's own kind mix, and the check **discriminates and is
   RETAINED**. `test_the_null_fails_the_kind_agreement_check` is what says so,
   and a label-blind mutation of the statistic drives the null to 0.000 and
   fails it.

   **The answer to this section's own framing — conservative or costly — is
   about a fifth.** §1's agreement requirement discards 18.6% of the left
   eye's detected events and 21.5% of the right's. That is the cost, and it
   is now a number rather than an argument.

   **What was not expected: disagreement dominates absence.** In both
   directions there are MORE stretches where the two eyes both detected
   something and named it differently (721, 855) than stretches where one eye
   detected nothing at all (595, 733). The eyes nearly always agree that
   *something* happened; the fifth that §1 drops is mostly the two of them
   disagreeing about *what*. Which kind pairs those are — almost certainly
   `saccade` against `pso`, the two eyes placing a saccade's own trailing
   glissade boundary differently — is NOT measured, and is the obvious next
   question.

   **ANSWERED 2026-09-19. The pair was guessed right and the DIRECTION was
   not guessed at all, which is the whole finding.** Measured by the same
   test, on the same recording, extended to break `disagree` down by the
   ordered pair `(own kind, other kind)`:

   | direction | pair | n | share of disagreements | chance share | ratio |
   |---|---|---|---|---|---|
   | left→right | `pso` over `saccadic` | 711 | **0.9861** | 0.4894 | **2.01×** |
   | left→right | `saccadic` over `pso` | 10 | 0.0139 | 0.5106 | 0.03× |
   | right→left | `pso` over `saccadic` | 835 | **0.9766** | 0.5106 | **1.91×** |
   | right→left | `saccadic` over `pso` | 20 | 0.0234 | 0.4894 | 0.05× |

   The guess above named `saccade` against `pso` and is confirmed: those two
   kinds are essentially the ONLY disagreement, 721 of 721 and 855 of 855.
   But the two eyes do not disagree symmetrically. Nearly every disagreement
   is one eye calling a stretch a glissade while the other is still calling
   it a saccade; the mirror — one eye calling a saccade what the other calls
   a glissade — is not merely rarer, it is *depleted to a thirtieth of its
   chance share*.

   **The chance share is what makes that a finding rather than a restatement
   of the kind mix**, and it is computed rather than assumed: the two
   marginals with the agreeing diagonal removed and the rest renormalised
   (`_expected_pair_shares`, hand-derived expectations pinned in
   `test_the_expected_pair_shares_are_the_product_of_the_two_kind_mixes`).
   With roughly two `pso` for every five `saccade` in each eye, chance
   divides the disagreements almost evenly between the two directions —
   0.489/0.511. The measurement finds 0.986/0.014. Without that baseline,
   "`pso` over `saccadic` dominates" would have been the vocabulary talking.

   **Restated as a cost, §1's agreement requirement is not a uniform tax.**
   The fifth this section already reported is an average over two kinds that
   are not affected alike:

   | direction | kind | dropped for disagreement | of its own runs | rate |
   |---|---|---|---|---|
   | left→right | `pso` | 711 | 2,017 | **0.3525** |
   | left→right | `saccadic` | 10 | 5,062 | 0.0020 |
   | right→left | `pso` | 835 | 2,167 | **0.3853** |
   | right→left | `saccadic` | 20 | 5,213 | 0.0038 |

   **Better than a third of every detected glissade is discarded by the
   binocular agreement rule, against two to four tenths of a percent of
   saccades.** The cost of §1 falls almost entirely on `pso` — the one kind
   this spec exists to store. That is the sentence this section's "conservative
   or costly" framing was asking for, and the aggregate fifth concealed it.

   **BOTH NUMBERS IN THE PARAGRAPH ABOVE ARE UNDERSTATEMENTS, corrected
   2026-09-19 and left standing rather than edited away.** They counted only
   the runs section 1 drops for a KIND DISAGREEMENT. Section 1 drops the
   `alone` bucket too — an own-run the other eye did not detect at all — and
   that bucket had never been attributed by kind. Measured:

   | direction | kind | disagreed | unmatched | total dropped | of its own runs |
   |---|---|---|---|---|---|
   | left→right | `pso` | 711 | 370 | 1,081 of 2,017 | **0.5359** |
   | left→right | `saccadic` | 10 | 225 | 235 of 5,062 | **0.0464** |
   | right→left | `pso` | 835 | 396 | 1,231 of 2,167 | **0.5681** |
   | right→left | `saccadic` | 20 | 337 | 357 of 5,213 | **0.0685** |

   So the glissade cost is better than **a half**, not better than a third;
   and the saccade cost is **4.6–6.9%**, not two to four tenths of a percent
   — an order of magnitude out, and out in the direction that matters, since
   `saccadic` is the kind most consumers of this pipeline actually care
   about.

   **The correction reverses which of the two findings is load-bearing.**
   For `saccadic` the disagreement half is negligible (10 and 20 runs) and
   essentially ALL of the cost is `alone`: 225 and 337 saccades that one eye
   detected and the other did not detect at all. That is not a labelling
   disagreement, not a boundary-placement question, and nothing the glissade
   mechanism above explains. It is the two eyes disagreeing about whether a
   saccade happened, and it is the only part of this section's cost that
   touches a pipeline with no interest in glissades.

   **How it was missed:** the per-kind block printed alongside the pair
   breakdown divided only `pairs` by each kind's population, while the
   `drop_rate` printed two lines above it correctly added `disagree + alone`
   for the population as a whole. Two numbers in one report, one counting a
   bucket the other omitted, and the narrower one was the one quoted. The
   report now prints `disagreed + unmatched = total` per kind so the two
   cannot diverge again, and `len(unmatched) == alone` is asserted.

   **A mechanism is available and is NOT measured — stated as the leading
   hypothesis, not as a finding.** A glissade is short (18–20 ms, §5's own
   measurement) and sits immediately after a saccade; a saccade is long. If
   the two eyes place a saccade's OFFSET differently by about a glissade's
   duration, then over those samples one eye has already begun its `pso`
   while the other is still inside its `saccade` — producing exactly this
   pair in exactly this direction. The asymmetry follows too: a long own
   `saccade` nearly always finds SOME same-kind counterpart and agrees,
   while a short own `pso` swallowed by the other eye's saccade has none.
   **What would settle it** is the distribution of (other eye's saccade
   offset − own eye's saccade offset) over these 711 and 835 events; if the
   mechanism is right it is centred near one glissade duration and not near
   zero. That is a new measurement, and it is the next question rather than
   part of this answer.

   **Deliberately not asserted.** The breakdown is printed and the only new
   assertion is an identity — that the pairs partition `disagree`. A bound
   on the shares would be tuned to the run that produced them, and worse, it
   would fire on an IMPROVEMENT: if the mechanism above is right, a better
   glissade offset criterion should SHRINK this asymmetry. The finding lives
   here and in the handoff, not in an assertion.

   **THE MECHANISM WAS MEASURED 2026-09-19, and it is right about most of
   the events and wrong about why.** The paragraph above predicted the
   offset difference would be "centred near one glissade duration and not
   near zero". Measured by `test_how_far_apart_the_two_eyes_place_a_saccades_
   offset`, on the same recording, with `unpaired == 0` in both directions —
   so `pso.start == saccade.stop` held on every one of the 711 and 835
   events, and the statistic read the boundary it claims to:

   | direction | population | n | share | median | mean |
   |---|---|---|---|---|---|
   | left→right | all disagreeing glissades | 711 | — | 10.03 ms | 23.01 ms |
   | left→right | counterpart ends INSIDE the glissade | 497 | **0.699** | 8.02 ms | 8.35 ms |
   | left→right | counterpart ends BEYOND it | 214 | 0.301 | 24.07 ms | 57.07 ms |
   | right→left | all disagreeing glissades | 835 | — | 10.03 ms | 30.08 ms |
   | right→left | counterpart ends INSIDE the glissade | 632 | **0.757** | 8.02 ms | 8.66 ms |
   | right→left | counterpart ends BEYOND it | 203 | 0.243 | 30.09 ms | 96.76 ms |

   **Three corrections to the prediction, in descending order of how much
   they change what to do.**

   **1. The displacement is NOT one glissade duration. It is about 1.7× the
   eyes' ordinary boundary jitter, and that is enough because the glissade
   is short.** The control is the same quantity on saccades the two eyes DO
   agree about, restricted the same way the glissade figure is forced
   (counterpart ends later) — comparing a forced-positive number against an
   unsigned one overstates the gap. Agreeing saccades: **median 6.02 ms**.
   Disagreeing glissades: **10.03 ms**. Not a different regime, a modestly
   larger displacement. What destroys the agreement is the glissade's own
   length: at a median 14.04/16.05 ms, an ordinary 6–10 ms offset
   displacement lands *inside* it, and that is all it takes.

   **This is the correction that matters for the fix.** The earlier reading
   — that a better glissade offset criterion should shrink the asymmetry —
   followed from the eyes disagreeing unusually about glissade boundaries.
   They do not. They disagree about saccade offsets roughly as much as they
   always do, and the glissade is simply short enough for that to matter.
   **No detector change removes this**; it is a property of requiring
   sample-level binocular kind agreement on an event a few samples long. The
   lever is §1's rule rather than the detector — for instance admitting a
   `pso` whose counterpart `saccadic` run ends within it — and that is a
   design question this section should now ask, not an implementation defect
   to go and fix.

   **2. A quarter to a third of the disagreements are not boundary placement
   at all.** Where the counterpart ends BEYOND the glissade (0.301 / 0.243),
   the other eye's saccade runs a median 24–30 ms and a mean 57–97 ms past
   this eye's: not the same saccade's offset placed later, but a longer or
   merged saccade covering the whole event. A different defect with a
   different fix, and what pulls the whole population's mean to two and
   three times its median. Any statement about "the" offset difference that
   does not split these two describes neither.

   **3. The sign of this quantity is forced, and is not evidence.** A
   counterpart only reaches the disagreement bucket by overlapping the
   glissade, which requires the other eye's saccade to end after this one's.
   Every difference is at least one sample whatever the eyes did. Only the
   magnitude carries anything, and only against the control. Stated because
   "the other eye ends later, every single time" reads like a finding and is
   a tautology.

   **A caveat on the INSIDE row, which is the one a reader will want to
   quote.** That population is defined by the counterpart ending at or
   before the glissade's end, so its values are bounded above by the
   glissade duration by construction. Its 8.02 ms median is a truncated
   statistic and cannot be read as "the displacement is 8 ms". The
   untruncated comparison is the 10.03 ms against the control's 6.02 ms,
   which is the one quoted in correction 1.

   **Still not asserted**, for the reason the paragraph above gives. The new
   assertions are identities: that the measurement measured something, that
   a baseline exists to read it against, and that `unpaired == 0` — the last
   a check that `_glissade_bounds`' construction still holds, not a bound on
   any result.

   **A detector asymmetry found while measuring, and not previously written
   down anywhere.** The conjunction's duration floor is the detector's own:
   `schema/detect.py::_min_duration_samples` reads `min_duration_samples` off
   the params with `getattr(..., 1)`, and that field belongs to
   `EngbertKlieglParams`. `NystromHolmqvistParams` states its durations in
   milliseconds and has none — so **Nyström–Holmqvist's conjunction admits a
   one-sample binocular event where Engbert–Kliegl's requires six**, and the
   6-sample floor §5 reports is Engbert–Kliegl's alone, not a property of the
   conjunction. The measurement above uses 1, matching what the conjunction
   really does for this detector.

   **One drift risk taken on deliberately.** The measurement lives in
   `tests/eye/`, which imports nothing from `wl_preproc.schema` (that file's
   own docstring: the 3.13 cross-check runs it with `--noconftest` in a venv
   with no DataJoint), so `_KIND_OF` and `_NOT_INTERSECTED` are RESTATED
   there rather than imported. Two definitions of one rule is how they come
   apart, and nothing can currently catch it: a ninth label, or a remapped
   kind, would change the conjunction and leave this measurement quietly
   reporting the old rule's number. The durable fix is to move `_KIND_OF`
   into `eye/detect/labels.py` — it is vocabulary knowledge, not schema
   knowledge — which both sides could then import. Recorded as a follow-up
   rather than done, being a production change to the conjunction outside the
   measurement's scope.

2. **The row-count effect is unmeasured.** §5's measured 36,101 rows covers
   one session, one detector, three traces, one kind. A multi-kind detector's
   conjunction carries runs this figure does not describe.

   **PARTIALLY answered 2026-09-12 — the per-eye half only, and the
   distinction matters.** The same measurement printed Nyström–Holmqvist's
   own per-eye counts against the reference recording:

   | eye | runs | fixation | pso | saccade |
   |---|---|---|---|---|
   | left | 12,092 | 5,013 | 2,017 | 5,062 |
   | right | 12,446 | 5,066 | 2,167 | 5,213 |

   Against Engbert–Kliegl's 12,767 / 12,631 on the same recording (§5), a
   multi-kind detector's per-eye trace is very close in TOTAL and quite
   different in composition — the extra kind does not multiply the rows.

   **Its CONJUNCTION row count is still unmeasured**, and that is the half
   this question actually asks about. Measuring it needs the schema, which
   the test file holding this measurement deliberately cannot import, so it
   belongs with `test_the_run_count_measured_against_the_reference_recording`
   in `tests/schema/` rather than here. Not done.
3. **Pursuit as a data-quality signal.** The lab's paradigms produce no
   smooth pursuit (ruled 2026-09-05), so a `pursuit` run from NSLR or
   REMoDNaV is more likely a misfire on noise than a finding — and under §1 a
   *binocular* pursuit run is a stronger signal still, since both eyes had to
   agree. Counting them in the daily report is a genuine opportunity and is
   **explicitly not in this spec**; it is recorded so it does not evaporate.
4. **`_overlapping` is still quadratic within a kind.** §1.3 makes it
   cheaper, not asymptotically better. Unmeasured for a detector that emits
   many runs of one kind.

## 7. Amendments to `2026-08-31-saccade-detection-design.md`

| § | Amendment |
|---|---|
| §4 | The binocular criterion intersects runs **of the same kind**, where `saccade` and `microsaccade` are one kind and every other emitted label is its own. `fixation` is not intersected (§1.2). |
| §5.1 | The conjunction trace's vocabulary is the detector's vocabulary, not a subset of it. The section's shorter-interval and non-poolability reasoning is unchanged and carries over to every kind. |
| §2.5 | **Unchanged, and satisfied more strictly.** The glissade assignment is stated in exactly one place — `DetectorAgreement.pso_as` — and no stored row embodies a convention. The rule "an explicit parameter, never a default" now has one site rather than two. |
| §6.1 | Unchanged. `pso_as` remains a comparison parameter; the lattice is not used at storage time. |
| §3.1 | Unchanged. |

**The handoff `2026-09-02-the-conjunction-label-decision.md` is superseded.**
Its four candidate conventions are not adopted, its premise is corrected in
§0, and its account of what a glissade is remains accurate and worth reading.
