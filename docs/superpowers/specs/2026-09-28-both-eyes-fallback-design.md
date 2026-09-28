# The both-eyes trace falls back to the usable eye, and says which eye it used

**Design spec, 2026-09-28.** It amends the conjunction trace of design spec
`2026-08-31-saccade-detection-design.md` §4 and conjunction-shape spec
`2026-09-05-conjunction-shape-design.md` §1.

**Why.** Measured on 2026-09-28 (handoff
`docs/handoffs/2026-09-28-why-one-eye-alone.md`):
- **Half of the saccades the binocular rule drops were in stretches where the
  other eye had no usable data.** That is 121 of 225 left-eye saccades and 169
  of 337 right-eye ones, most of them at blink edges.
- **The both-eyes trace labels its gaps from the left eye's mask alone.**
  Where only the right eye was withheld, it reads `fixation`: 1.80% of the
  reference recording.

**The requester's decisions, 2026-09-28:**
- **Fall back to the usable eye** where only one eye has data.
- **Keep a one-eye event whole** when the other eye's data was missing at any
  point during it.
- **Store one "which eye" trace** with the both-eyes detection, saying for
  every moment which eye or eyes its label came from.

---

## 1. The both-eyes trace, moment by moment

Which eyes are usable at a sample is read from the two eyes' validity masks
(`offered`, `None` where usable).

| Usable | The both-eyes trace |
|---|---|
| both | **unchanged**: an event only where both eyes found one of the same kind, overlapping by at least the detector's floor (`_conjunction_runs`); `fixation` otherwise |
| one | that eye's own labels: its events, and `fixation` between them |
| neither | the left eye's mask label, as today |

§2 adds the one-eye events that start or end in a both-usable stretch.

## 2. One-eye events at a mask edge

**An own-eye run is kept, whole, when:**
- its kind is one the conjunction carries (`labels.py::kind_of` is not
  `None`);
- the other eye has no run of the same kind overlapping it by the floor, so
  it is unmatched in `_kind_agreement`'s sense;
- the other eye's mask withholds at least one of its samples.

**A kept run is stored exactly as that eye's own row stores it:**
- its own label;
- measured on its own eye's gaze, velocity and mask;
- under the detector's per-eye rules, including NSLR's landing sample and
  BMD's take-off sample;
- with its own `reliability`.

So the per-eye table and the both-eyes table never disagree about one event.

**It cannot overlap a two-eye event.** A two-eye event needs the kept run's
own eye to carry a same-kind label over the same samples, which would have
made it matched.

**Ruling (this spec's author): two kept runs of different kinds from the two
eyes that overlap are both dropped.** Over their overlap both eyes were
usable and named different kinds: a kind disagreement, which the binocular
rule already drops. Cost if wrong: a rare event lost at a blink edge. How
often it happens is measured on the reference recording and recorded (§6).

*Widened 2026-09-28, while building: **any** two candidate runs from the two
eyes that overlap or touch are both dropped, whatever their kinds.*
- *A same-kind pair overlapping by less than the floor was not a match. It
  is dropped for the same reason as a kind disagreement.*
- *Two runs of one label that merely touch would merge into one stored run
  (`runs_from_labels`), attributed to neither eye.*

*Measured on the reference recording, after the usable-data guard: 0–36
pairs per detector, under 1% of the candidates (Engbert–Kliegl 26,
Otero-Millan 2, Nyström–Holmqvist 0, REMoDNaV 26, NSLR 36, BMD 24).*

**Ruling (this spec's author, 2026-09-28): an eye with no calibration keeps
today's refused both-eyes row.** §1 reads which eyes are usable from each
eye's validity mask. An eye whose calibration was refused has no mask and
no gaze at all, so the session is monocular. A "both-eyes" trace there
would be a relabelled copy of the other eye's own trace. Cost if wrong: a
monocular session has no both-eyes trace, only the usable eye's own.

**Ruling (this spec's author, 2026-09-28, after the final review): the
second condition is dropped.** An own-eye run of a carried kind is kept
whole whenever the other eye's mask withholds at least one of its samples,
whether or not the other eye saw part of it. The two-eye span inside it,
the intersection of that run with the other eye's, gives way to it.
- *Why:* the review (its C1) found that a two-eye event whose other eye
  drops out mid-flight was stored as two events: the intersection, labelled
  from its own amplitude, and a one-eye fragment, carrying the whole run's
  label but measured on the fragment alone. On the reference recording that
  was 356 Engbert–Kliegl events, and 67 Engbert–Kliegl, 34 Otero-Millan and
  38 BMD rows whose label contradicted their amplitude, where before this
  spec there were none.
- *It is the requester's decision applied once more:* the other eye's data
  was missing during the event, so the event is kept whole, as the eye that
  saw all of it.
- *With it,* every event where one eye alone is usable is kept whole, so §1's
  one-eye row needs no separate rule: the kept runs are that eye's events,
  and `fixation` fills between them.
- *Cost if wrong:* such an event's extent and measurement come from the one
  eye that saw it whole, not from the binocular intersection. A matched
  pair whose eyes drop out at opposite ends clashes and keeps only its
  intersection, as before this spec.
- *Confirmed by the requester the same day, before merge.*

## 3. Measurement

- **Two-eye events** are measured on the left eye's gaze, unchanged.
- **One-eye events,** whether in a one-eye stretch or kept at a mask edge,
  are measured as their own eye's row is (§2).

*Ruled 2026-09-28, while building: a two-eye event can continue into a
stretch where one eye is withheld.*
- *There, §1 gives the conjunction the usable eye's label, so the two-eye
  run and that continuation share a label and are stored as one run.*
- *Such a run is measured on whichever eye was usable throughout it, the left
  first as for every two-eye event.*
- *If neither eye was usable throughout it, it is stored unmeasured, never
  measured on a withheld sample.*
- *Cost if wrong: such a run is measured on the right eye where it would
  otherwise have been the left, or left unmeasured.*

*Since the final review's fix (§2, the last ruling), a two-eye event no
longer continues into a one-eye stretch: the eye that saw it whole is kept
instead. A stored run now joins two intervals only where a kept run touches
a same-label run from its own eye, which that eye's own trace merges the same
way: 23 rows for Nyström–Holmqvist on the reference recording, none for the
other five detectors. Six of them are a two-eye span that `_conjunction_runs`
coalesced across two touching runs of one eye; it gives way only where the
kept run covers it.*

The per-eye traces are unchanged.

## 4. The "which eye" trace

A new part table beside `EyeDetection.Run`, `EyeDetection.Source`, written
for the `conjunction` trace only. It stores maximal runs tiling `[0,
n_samples)`, each carrying one of:

| `source` | Meaning |
|---|---|
| `both` | the label is the two-eye rule's (both eyes usable) |
| `left` | the label came from the left eye alone |
| `right` | the label came from the right eye alone |
| `neither` | neither eye was usable |

**A kept one-eye event is marked with its own eye along its whole length**,
including samples where the other eye was usable. The trace records where
each label's data came from, not only which eyes were usable.

## 5. What changes for a reader

- **The both-eyes trace carries one-eye events** in one-eye stretches and at
  mask edges. Some of these are blink-edge artifacts, which the requester
  accepted. On the reference recording that is about 290 more saccades for
  Nyström–Holmqvist: the 121 + 169 above, before one-eye stretches are
  counted.

  *Measured 2026-09-28, after the usable-data guard (handoff
  `2026-09-28-runs-stay-on-usable-data.md`): kept one-eye saccades,
  left/right, per detector.*

  | Detector | Left | Right |
  |---|---|---|
  | Engbert–Kliegl | 793 | 513 |
  | Otero-Millan | 599 | 201 |
  | Nyström–Holmqvist | 542 | 186 |
  | REMoDNaV | 587 | 191 |
  | NSLR | 1,091 | 785 |
  | BMD | 762 | 382 |

  *The which-eye trace is `neither` on 2.20% of samples for every detector,
  the masks' own share. It is `left` on 2.0–2.8% and `right` on 1.5–1.9%,
  since kept events add to the masks' 1.80% and 1.45%.*

  *Re-measured 2026-09-28 after the final review's fix (§2, the last
  ruling), which also keeps events the other eye saw only in part:*

  | Detector | Left | Right | Runs dropped by the clash rule |
  |---|---|---|---|
  | Engbert–Kliegl | 1,218 | 748 | 108 |
  | Otero-Millan | 642 | 228 | 10 |
  | Nyström–Holmqvist | 587 | 205 | 0 |
  | REMoDNaV | 594 | 193 | 37 |
  | NSLR | 1,277 | 955 | 106 |
  | BMD | 969 | 533 | 144 |

  *Label/amplitude contradictions in the both-eyes trace stay at 0 for every
  detector that splits by amplitude. The which-eye trace is still `neither`
  on 2.20%. It is `both` on 92.7–93.8% for five detectors and 87.3% for
  BMD, whose kept runs include its background label, `drift`.*
- **Its `n_saccades` and `n_microsaccades`, and agreement scores computed on
  it, mix two-eye and one-eye events.** `EyeDetection.Source` separates them.
- **Unchanged:**
  - the per-eye traces;
  - every both-usable stretch without a kept event;
  - `docs/schemas` (no exported schema includes these tables);
  - `wlpp report`.

**No migration** (the requester, 2026-09-27: nothing real is stored yet).

## 6. Validation

- **Unit, without the database (`_insert_trace`'s in-memory harness):**
  - a one-eye stretch takes that eye's labels;
  - a kept event is stored whole with its own label, and its measurements
    equal its per-eye row's;
  - an unmatched run with no withheld sample in the other eye is still
    dropped;
  - neither usable gives the left mask's label;
  - two-eye events are unchanged;
  - the §2 ruling's overlapping pair is dropped;
  - `EyeDetection.Source` tiles `[0, n)` with the labels §4 defines.
- **Stored, through `daemon.run_once()`:**
  - a synthetic session withholds one eye over a planted step, and the other
    eye's step appears in the both-eyes trace, `source` that eye, measured as
    that eye's row;
  - the existing right-eye-only phantom step, with both eyes usable, is
    still not in the both-eyes trace;
  - every per-eye row is unchanged.
- **On the reference recording (gated):**
  - per detector, the number of kept events and of §2's overlapping pairs;
  - the `Source` fractions against the masks' own (94.55% both usable, 1.45%
    and 1.80% one eye, 2.20% neither), moved only by kept events.
  - All recorded, not gated.

## 7. Out of scope

- Sharing the two eyes' blink masks, which would remove most blink-edge
  events rather than keep them.
- Changing the two-eye rule where both eyes are usable.
- Measuring two-eye events on anything but the left eye.
