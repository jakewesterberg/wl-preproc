# Padding a blink's flanks in the validity mask

**The eye-loss fix's follow-up** (`handoffs/2026-10-11-eye-loss.md` §3, partial blinks). The mask
marked a blink only over its closed phase, the frames whose pupil is lost, and padded it by 10 ms;
the gaze vector is disturbed from about 50 ms before the pupil goes to about 80 ms after it
returns.
- **Branch:** `fix/blink-padding`, forked from `main` after the eye-loss merge.
- **Spec:** `docs/superpowers/specs/2026-10-11-blink-padding-design.md`, approved 2026-10-11, with
  amendments 1–4 made while proving the plan.
- **Plan:** `docs/superpowers/plans/2026-10-11-blink-padding.md`.
- **The requester's choices:** a fixed, asymmetric pad, 50 ms before and 80 ms after, the padded
  frames `invalid`; the adaptive pad to try once there is data to validate it against.

Every line of the plan was proven in a scratch branch, and replayed from the plan's own text,
before the plan was written.

---

## 1. What was built

- **Two `ValidityParams` fields,** `blink_pad_before_ms` and `blink_pad_after_ms`: 0 by default,
  so a paramset stored before them loads unchanged; 50 and 80 in `DEFAULT_VALIDITY_PARAMS`.
- **`validity_labels` pads each blink from its edges** (`_blink_pads`), after the 10 ms margin,
  which sits inside the pad, and before `short_epoch`. It takes the recording's rate as the
  keyword `fs_hz`; every caller passes it.
- **The pads are `invalid` and count toward no criterion:** `frac_blink` and the other fractions
  are unchanged, and there is no new column.
- **On the reference recording** the mask keeps 0.60% fewer of the left eye's frames and 0.44%
  fewer of the right's; Engbert–Kliegl finds 248 fewer events in the left eye, and its agreement
  with Otero-Millán rises from 0.843 to 0.863 (spec amendments 2 and 3).

## 2. What the other repositories must do

Nothing. The NWB file's format, the report's and wl.works' contracts are unchanged.

**A development database** holding the old default validity paramset registers the new one
beside it, and computes masks, detections, scores and fits for both, as it did when the glitch
repair added `max_glitch_ms`. No real database exists yet.

## 3. Still open

- **The adaptive pad** (the requester's reminder, spec §0): extending each blink while the pupil's
  height or the gaze's steps stay abnormal, once there is data to validate it against, such as
  hand-labelled blinks or raw eye video (`docs/pending-wl-xcon-amendments.md`, the asks of
  2026-10-11).
- **The movement after a blink is kept:** from about 80 ms after the pupil returns, the gaze's
  steps rise again, peaking near 200 ms. That is read as the eyes moving again, real movement the
  detectors should see; nothing in the data file alone can prove it.
- **Only blinks are padded:** a lost reflection, P1 off camera, keeps the 10 ms margin.

## 4. The rulings

The spec's amendment 4: `fs_hz` keyword-only, the database test with the unit tests, and the
recording's edge tested.

## 5. What execution measured

Executed on 2026-10-12, task by task: Task 1 `c6c0e80`. Every count matched the plan's:
- **Task 1's tests failed first:** 38 failed.
- **Then passed, with their neighbours:** 48 and 697.
- **All nine mutation checks the plan names failed their tests.**
- **The seven tests gated on the reference recording:** 69 passed, as before (amendment 3).
- **The full suite, once, on both interpreters,** with the BMD, NSLR and Andersson references
  set and `WLPP_OHDPI_REFERENCE` unset:
  - 3.11: **2349 passed, 27 skipped, 1 deselected, 1 xfailed**;
  - 3.13: **2349 passed, 28 skipped, 1 xfailed**.

  That is 10 more than the eye-loss fix's 2339: this piece's new tests. CI, without the references, should show about 2340 (`main`'s 2330 and these 10).
- **`wl-check`:** `wl.yaml: no findings`.
