# Saccade main sequence and vigor

**Branch:** `spec/main-sequence`, forked from `main` at `a699dd6`.
- **Design:** `docs/superpowers/specs/2026-10-07-main-sequence-design.md`, an addendum to the
  saccade-detection spec's §6.5 and §9, with amendments 1–3.
- **Plan:** `docs/superpowers/plans/2026-10-07-main-sequence.md`.
- **The requester's choices,** on 2026-10-07:
  - this work next, from what was open;
  - vigor worked out per saccade, the session's figure their median;
  - saccades of 1° and up in the fit;
  - each eye on the report's line, with every detector's figure;
  - a gain against the session's curve for blocks and conditions, rather than fits of their own;
  - the design, then the written spec;
  - the plan run in one session ("Native"), with one whole-branch review (2026-10-08).

## 1. What was built

- **`eye/detect/main_sequence.py`,** pure functions:
  - `selected`: which runs a fit takes, by size (1° and up) and duration (150 ms at most), not by
    label;
  - `fit_session`: the saturating curve through a session's saccades, refused for fewer than 100,
    for a middle 80% of sizes spanning less than a factor of 3, or for a V_max or C known to no
    better than half (amendment 1);
  - `gain`: a group's median ratio of peak speed to the session's curve, from 30 saccades;
  - `vigor`: a session's median ratio against the earlier sessions' curves that cover each
    saccade's size, from 3 earlier sessions and 30 saccades.
- **`schema/main_sequence.py`:** `SaccadeMainSequence`, one row per detection trace and fit
  paramset (amendment 2), with `.Block` and `.Condition` gains.
  - A refused detection gets a refused row quoting it.
  - Saccades are placed by where they start, in session time through the recording's frame
    numbers. Block and trial times are read as the doubles MySQL stores.
  - A trial's condition is the rig record's name, else the stream's `CONDITION` number, as the NWB
    export resolves it.
- **The daemon** runs it after `DetectorAgreement` and registers the default `main_sequence`
  paramset.
- **The report** has a new subsection, "Saccade vigor per session per eye (24 h)": one line per
  session per eye, with each detector's figure or the reason there is none.

## 2. What it measured

- **On the reference recording** (spec §1): each detector's sizes and durations, what the 1°
  floor changes, how far the detectors disagree about the curve, and why a block cannot hold a fit
  of its own.
- **On the planted session** (Task 2's fixture):
  - every detector that computes a fit lies within 1.4–9.2% of the planted curve over 2–8°
    (amendment 3);
  - a condition planted 10% faster comes out at 1.095 times its neighbour;
  - Nyström–Holmqvist keeps only the saccades over 7° there, so its fits are refused for too few.

## 3. Tests

- `tests/eye/detect/test_main_sequence.py` (18): the defaults, the selection rule, the fit's
  recovery and each refusal, gains and vigor.
- `tests/schema/test_main_sequence_populate.py` (12), on a synthetic session of 161 saccades on a
  planted main sequence, with 60 frames dropped and a trial faulted:
  - a row for every trace, each stored fit equal to the fit of its own selected runs, the planted
    curve recovered, and the fit paramset in the key;
  - a refused detection's row, and too few saccades refused with their count;
  - every block's and condition's gain, against where the recipe put them, and the faster
    condition;
  - placement an hour into a session, by record name and by stream number;
  - a real `wlpp daemon` pass writing rows with nothing registered beforehand.
- `tests/cli/test_vigor_report.py` (6): a figure, too little history, too few saccades, a refused
  detection, a missing row, and the both-eyes trace left out.

## 4. The full suite

Run once, on both interpreters, with every task applied and every reference variable set except
`WLPP_OHDPI_REFERENCE`:
- **3.11:** 2270 passed, 26 skipped, 1 deselected, 1 xfailed.
- **3.13:** 2270 passed, 27 skipped, 1 xfailed.

That is 36 more passed on each than `main`'s 2234 at `22dbb70`: this branch's new tests.

## 5. What is next

- **The whole-branch review, then the merge question.** After the merge: CI on both
  interpreters, and the pointer commit (the checkpoint's header and `wl.yaml`'s `describes`).
- **Not in this round** (spec §8): the fits in the NWB file, the stream's `CONDITION` number end
  to end (wl-xcon's XC-197), other fit forms, and a rolling history.
- **The numbers mean degrees only once sessions are calibrated.** The reference recording's
  degrees are a guessed scale. The defaults rest on its sizes and should be checked against the
  lab's first calibrated sessions.
