# The both-eyes trace falls back to the usable eye, and says which eye it used

Branch `spec/both-eyes-fallback`, forked from `main` at `978a3a3`. Spec
`docs/superpowers/specs/2026-09-28-both-eyes-fallback-design.md`; plan
`docs/superpowers/plans/2026-09-28-both-eyes-fallback.md`.

The requester's decisions of 2026-09-28, after handoff
`2026-09-28-why-one-eye-alone.md` found that half of the saccades the
binocular rule drops sat where the other eye had no usable data:
- **fall back to the usable eye** where only one eye has data;
- **keep a one-eye event whole** when the other eye's data was missing at
  any point during it;
- **store one "which eye" trace** with the both-eyes detection.

---

## 1. What was built

`schema/detect.py`:
- `_conjunction_fallback` decides which own-eye runs are kept whole: every
  run of a carried kind the other eye's data was withheld during, unless it
  overlaps or touches such a run from the other eye. It also says which eye
  each sample draws on. `_value_runs` turns that per-sample array into
  stored runs.
- `_conjunction_parts` assembles the trace: the two-eye spans, each giving
  way wherever a kept run covers it, then the kept runs. It fills a gap from
  the left eye's mask only where neither eye is usable.
- `EyeDetection.Source`, a new part table, tiles the conjunction trace
  with `both`, `left`, `right` or `neither`.
- `_insert_trace` measures a kept event as its own eye's row, with that
  eye's rules and its own reliability. It measures every other conjunction
  run on the eye usable throughout it, the left first, and leaves one with
  neither unmeasured.

## 2. What it changes, on the reference recording

Measured after the usable-data guard and the final review's fix. Saccades
now kept whole in the both-eyes trace, left/right, and the runs the clash
rule drops:

| Detector | Left | Right | Dropped by the clash rule |
|---|---|---|---|
| Engbert–Kliegl | 1,218 | 748 | 108 |
| Otero-Millan | 642 | 228 | 10 |
| Nyström–Holmqvist | 587 | 205 | 0 |
| REMoDNaV | 594 | 193 | 37 |
| NSLR | 1,277 | 955 | 106 |
| BMD | 969 | 533 | 144 |

- For Nyström–Holmqvist the which-eye trace is `both` on 93.76% of samples,
  `left` 2.42%, `right` 1.62% and `neither` 2.20%.
- For every detector it is `neither` on 2.20%, where both masks withhold.
- It is `both` on 92.7–93.8% for five detectors, and 87.3% for BMD, whose
  kept runs include its background label, `drift`.
- Label/amplitude contradictions in the both-eyes trace stay at 0 for every
  detector that splits by amplitude.

## 3. Rulings

- **Any two candidates from the two eyes that overlap or touch are both
  dropped** (spec §2, widened while building).
- **A refused eye keeps the refused both-eyes row** (spec §2).
- **An event the other eye saw only in part is kept whole too** (spec §2,
  ruled after the final review). See §5.
- **A stored run joining two intervals is measured on the eye usable
  throughout it** (spec §3). Since the fix this happens only where a kept
  run touches a same-label run of its own eye: 23 Nyström–Holmqvist rows,
  none elsewhere.
- **The other eye's run of another kind does not stop an event being
  kept** (the plan's Review Focus 2). For BMD, NSLR and REMoDNaV it is
  usually that eye's background label, `drift` or pursuit, which the
  conjunction intersects. Refusing to keep those events would make the
  fallback depend on which detector ran.
- **The gated check covers Nyström–Holmqvist alone** (plan, Task 3).

## 4. Tests

- 24 tests in `tests/schema/test_detect_populate.py`:
  - 10 of the fallback's rule and assembly;
  - 8 of `_insert_trace` and the stored rows, including a session with the
    right eye withheld over a planted step and both eyes over a quiet
    stretch;
  - 6 from the final review: an event the other eye loses mid-flight, and
    a coalesced two-eye span that reaches past its kept run.
- One existing test changed its expectation:
  `test_no_detector_labels_a_missing_gaze_value`. The missing value is in
  the left eye alone, so the both-eyes trace now carries the right eye's
  label there.
- A gated check on the reference recording,
  `test_the_both_eyes_fallback_on_the_reference_recording`.

## 5. The final review

One fresh reviewer read the whole branch. Its findings, and what became of
them:

- **C1, fixed.** Under the plan's rule, a two-eye event whose other eye
  dropped out mid-flight was stored as two events: the intersection, labelled
  from its own amplitude, and a one-eye fragment carrying the whole run's
  label. On the recording that split 356 Engbert–Kliegl events, and 67
  Engbert–Kliegl, 34 Otero-Millan and 38 BMD rows contradicted their own
  amplitude, from 0.
  - Spec §2's condition that a kept run be unmatched is dropped. The eye
    that saw the event whole is kept, and the two-eye span inside it gives
    way (spec §2, the last ruling).
  - `_one_eye_pieces` is gone: every event where one eye alone is usable is
    now a kept run.
  - While fixing it, six coalesced two-eye spans (all Nyström–Holmqvist)
    were found to reach past their kept run. They now give way only where
    it covers them, so their binocular part is not lost.
- **I1, fixed.** Two docstrings, in `_insert_trace` and `_overlapping`, now
  state what holds between a two-eye span and a kept run.
- **I2, fixed.** The assembly is a pure function, `_conjunction_parts`,
  tested without a database, including the input the review found failing.
- **Deferred minors:**
  - `_measured_on` passes the conjunction's fill mask rather than the
    chosen eye's own. No rule reads it today.
  - A NULL `amplitude_deg` also means "no eye usable throughout" for a
    joined run, which the `Run` definition comment does not list.
  - The clash rule drops some saccades whose overlapping partner was the
    other eye's pursuit (REMoDNaV), which sits uneasily with the ruling on
    background labels.
  - The gated check does not print the clash rule's count.

## 6. What is next

The requester's choice. The known open items in `docs/CHECKPOINT.md`:
- the one-sample conjunction floor that three detectors inherit, a
  cross-detector decision still open;
- the suite's missing session-date allocator;
- the items blocked on the rig and the compute machine.
