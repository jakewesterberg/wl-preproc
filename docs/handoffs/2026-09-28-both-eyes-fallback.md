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
- `_conjunction_fallback` decides which one-eye events are kept whole, and
  which eye each sample draws on. `_one_eye_pieces` cuts each eye's runs to
  where it alone is usable. `_value_runs` turns the per-sample array into
  stored runs.
- `EyeDetection.make()` hands those runs to `_insert_trace` beside the
  two-eye runs, which are unchanged. It fills a gap from the left eye's
  mask only where neither eye is usable.
- `EyeDetection.Source`, a new part table, tiles the conjunction trace
  with `both`, `left`, `right` or `neither`.
- `_insert_trace` measures a kept event as its own eye's row, with that
  eye's rules and its own reliability. It measures every other conjunction
  run on the eye usable throughout it, the left first, and leaves one with
  neither unmeasured.

## 2. What it changes, on the reference recording

Measured after the usable-data guard. One-eye saccades now kept whole in
the both-eyes trace, left/right:

| Detector | Left | Right |
|---|---|---|
| Engbert–Kliegl | 793 | 513 |
| Otero-Millan | 599 | 201 |
| Nyström–Holmqvist | 542 | 186 |
| REMoDNaV | 587 | 191 |
| NSLR | 1,091 | 785 |
| BMD | 762 | 382 |

For Nyström–Holmqvist the which-eye trace is `both` on 93.84% of samples,
`left` 2.36%, `right` 1.59% and `neither` 2.20%. For every detector it
is `neither` on 2.20%, where both masks withhold.

## 3. Rulings

- **Any two candidates from the two eyes that overlap or touch are both
  dropped** (spec §2, widened while building): 0–36 pairs per detector.
- **A refused eye keeps the refused both-eyes row** (spec §2).
- **A two-eye run joined by its one-eye continuation is one run,**
  measured on the eye usable throughout it (spec §3).
- **The other eye's run of another kind is no counterpart** (this plan's
  Review Focus 2). For BMD, NSLR and REMoDNaV it is usually that eye's
  background label, `drift` or pursuit, which the conjunction intersects:
  451, 257 and 71 kept runs overlap one. Dropping them would make the
  fallback depend on which detector ran.
- **The gated check covers Nyström–Holmqvist alone** (plan, Task 3).

## 4. Tests

- 11 tests of the three functions, and 8 of `_insert_trace` and the stored
  rows, including a session with the right eye withheld over a planted step
  and both eyes over a quiet stretch.
- One existing test changed its expectation:
  `test_no_detector_labels_a_missing_gaze_value`. The missing value is in
  the left eye alone, so the both-eyes trace now carries the right eye's
  label there.
- A gated check on the reference recording,
  `test_the_both_eyes_fallback_on_the_reference_recording`.

## 5. What is next

The requester's choice. The known open items in `docs/CHECKPOINT.md`:
- the one-sample conjunction floor that three detectors inherit, a
  cross-detector decision still open;
- the suite's missing session-date allocator;
- the items blocked on the rig and the compute machine.
