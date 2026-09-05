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
itself. U'n'Eye (`{saccade}`) and BMD (`{microsaccade}`) are the two
degenerate cases and the existing reasoning covers both unchanged.

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
2. **The row-count effect is unmeasured.** §5's measured 36,101 rows covers
   one session, one detector, three traces, one kind. A multi-kind detector's
   conjunction carries runs this figure does not describe.
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
