# The one decision blocking four detectors

**Written 2026-09-02**, after stage 2A merged (`a8fd3cb`, CI green). This is a
brief for a design conversation, not a plan. Nothing here needs writing until
the question at the bottom is answered.

---

## First, the word this project kept using without defining

**A glissade is the eye's lens still wobbling after the eye has stopped.**

When the eye finishes a saccade, the lens keeps moving relative to the optical
axis for a few tens of milliseconds. A dual-Purkinje tracker — this rig's
instrument, and the one this pipeline's P4 signal comes from — sees that wobble
as real eye movement, because from the optics it is indistinguishable from one.

Deubel & Bridgeman measured it on a DPI against a scleral search coil and found
retinal image displacement from lens movement alone **as large as 0.5°**. The
default microsaccade threshold is **1.0°**. So the artifact is half the size of
the thing it gets mistaken for, and it appears **after every single saccade**,
systematically, in a way averaging cannot remove.

Nyström & Holmqvist call it a **glissade**, find it in about half of all
saccades with a mean duration near 24 ms, and conclude that researchers *"must
actively choose whether to assign the glissades to saccades or fixations; the
choice affects dependent variables such as fixation and saccade duration
significantly. Current algorithms do not offer this choice, and their
assignments of each glissade are largely arbitrary."*

Three names for one thing, used interchangeably across this repo: **glissade**
(the literature's word), **post-saccadic oscillation / PSO** (the mechanism),
and **`pso`** (this pipeline's label). Spec §2.5 is the section.

## What is already decided, so it is not reopened

**For COMPARING two detectors, the choice is already a stated parameter and it
works.** §6.1 puts `pso_as` in `DetectorAgreement`'s primary key with two
values, `saccade` and `fixation`; every pair is scored both ways and each row
records which convention produced it. That is exactly the choice Nyström &
Holmqvist say algorithms fail to offer, and it is implemented and tested.

So the decision below is **not** "does a glissade count as a saccade or a
fixation, in general". That question is answered: *both, stated per comparison.*

## What is actually undecided

**A `conjunction` run needs one label, and there is no per-comparison parameter
to hide behind.**

The `conjunction` trace is the binocular criterion: an event survives only where
both eyes saw one overlapping in time. It is *derived*, never independently
detected — so its label cannot come from a detector, because no detector
produced it.

For today's two detectors that is easy. Both declare `{saccade, microsaccade}`,
so a conjunction run's label is `classify()` of its own measured amplitude —
one rule, no arbitration, and label and amplitude agree by construction.

For a detector that can emit `pso`, amplitude cannot answer it. A 0.4° stretch
might be a microsaccade or the tail of a 6° saccade, and the amplitude is
identical either way. So `schema/detect.py::_conjunction_label` **raises**
rather than guessing:

```
UndecidedConjunctionLabel: detector 'nystrom_holmqvist' declares vocabulary
['fixation', 'pso', 'saccade'], which is not a subset of the amplitude-derived
vocabulary ['microsaccade', 'saccade'] ...
```

That raise is deliberate — §2.5 forbids inventing the answer — but it is
currently absolute.

## Why it blocks four detectors and not one

§3.1's table gives these vocabularies. Four trip the guard:

| Detector | Vocabulary | Blocked? |
|---|---|---|
| Engbert–Kliegl | saccade / microsaccade | no — shipped |
| Otero-Millan | saccade / microsaccade | no — shipped |
| Nyström–Holmqvist | saccade / **pso** / fixation | **yes** |
| NSLR | saccade / **pso** / **pursuit** / fixation | **yes** |
| REMoDNaV | saccade / **pso** / **pursuit** / fixation | **yes** |
| Bayesian microsaccade | microsaccade / **drift** | **yes** |
| U'n'Eye | saccade | no — blocked by `torch`/GPU instead |

**And the failure is worse than it looks.** `EyeDetection.make()` inserts the
`left` and `right` traces *before* reaching the conjunction branch — but
DataJoint's `AutoPopulate._populate1` wraps the whole call in a transaction and
cancels it on any exception. Verified empirically. So registering a blocked
detector today does not give you usable per-eye data while the conjunction
waits. **It gives you nothing, silently, on every pass**, with the failure
visible only in `run_once`'s error list.

## The question for the next session

**What label should a `conjunction` run carry when the detector can emit
`pso`?**

Some shapes the conversation might take, none of them researched yet:

- **Require agreement.** Both eyes must have called it the same thing, else the
  event is dropped from the conjunction. Conservative, and consistent with the
  conjunction already being a filter rather than a generator — but it discards
  real binocular events over a labelling difference.
- **Extend `pso_as` to storage.** The comparison layer already carries the
  parameter; the conjunction could too, making the stored label explicit about
  which convention produced it. Costs a column or a paramset key, and means the
  conjunction trace is no longer one thing.
- **Take one eye's label**, as the conjunction already takes the left eye's
  gaze for its measurement. Cheapest, and consistent — but §5.1 already records
  that this makes the left eye decide, and it would now decide labels for a
  class where the two eyes genuinely disagree about 5% of the time.
- **Keep the raise and never register these detectors binocularly** — per-eye
  traces only for pso-capable detectors. Honest, and it would need
  `EyeDetection.make()` to write the two eyes without the conjunction, which
  the transaction behaviour above currently prevents.

**What the decision must respect**, whichever way it goes:

1. §2.5's rule — the assignment is stated, never defaulted. A silent convention
   is the thing this whole design exists to avoid.
2. The stored `label` must not contradict the stored `amplitude_deg`. Getting
   that wrong once already produced 12.3% contradictory rows before it was
   caught.
3. Whatever is chosen has to be visible in the row, because §6.1 requires any
   report aggregating across pairs to group by the convention that produced them.

## Where to read the evidence

- **Spec §2.5** — post-saccadic oscillation, with both papers.
- **Spec §6.1** — the coarsening lattice, `pso_as`, and the comparability rule.
  Note its recorded **known gap**: the rule is wrong for a disjoint-vocabulary
  pair (U'n'Eye vs BMD), pinned by a strict `xfail` so a silent fix fails the
  suite.
- **`schema/detect.py::_conjunction_label`** — the raise, and why it exists.
- **`docs/handoffs/2026-09-01-consensus-and-otero-millan-built.md`** — what
  stage 2A measured, and the two constraints it puts on any validation.
