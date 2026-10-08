# Saccade main sequence and vigor

**Branch:** `spec/main-sequence`, forked from `main` at `a699dd6`.
- **Design:** `docs/superpowers/specs/2026-10-07-main-sequence-design.md`, an addendum to the
  saccade-detection spec's §6.5 and §9, with amendments 1–4.
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
- `tests/schema/test_main_sequence_populate.py` (13), on a synthetic session of 161 saccades on a
  planted main sequence, with 60 frames dropped, a trial faulted, and two condition names that
  differ only in case:
  - a row for every trace, each stored fit equal to the fit of its own selected runs, the planted
    curve recovered, and the fit paramset in the key;
  - a refused detection's row, and too few saccades refused with their count;
  - every block's and condition's gain, against where the recipe put them, and the faster
    condition;
  - placement an hour into a session, by record name and by stream number;
  - `Contrast-10` and `contrast-10` in one block as two conditions (amendment 4);
  - a real `wlpp daemon` pass writing rows with nothing registered beforehand.
- `tests/cli/test_vigor_report.py` (6): a figure, too little history, too few saccades, a refused
  detection, a missing row, and the both-eyes trace left out.

## 4. The full suite

Run on both interpreters, with every reference variable set except `WLPP_OHDPI_REFERENCE`.
- **With every task applied:** 2270 passed on each; 3.11 26 skipped, 1 deselected, 1 xfailed;
  3.13 27 skipped, 1 xfailed.
- **After the review's fix** (§6):
  - **3.11:** 2271 passed, 26 skipped, 1 deselected, 1 xfailed.
  - **3.13:** 2271 passed, 27 skipped, 1 xfailed.

That is 37 more passed on each than `main`'s 2234 at `22dbb70`: this branch's new tests.

## 5. What is next

- **The merge question.** After the merge: CI on both interpreters, and the pointer commit (the
  checkpoint's header and `wl.yaml`'s `describes`).
- **Not in this round** (spec §8): the fits in the NWB file, the stream's `CONDITION` number end
  to end (wl-xcon's XC-197), other fit forms, and a rolling history.
- **The numbers mean degrees only once sessions are calibrated.** The reference recording's
  degrees are a guessed scale. The defaults rest on its sizes and should be checked against the
  lab's first calibrated sessions.

## 6. The whole-branch review (2026-10-08)

One fresh reviewer on the whole branch: "with fixes". No Critical findings, one Important, four
Minor.
- **Fixed: condition names that differ only in case or accent** (amendment 4). `.Condition` was
  keyed by the name, and MySQL's default collation ignores case and accents. Two rig names such as
  `contrast-50` and `Contrast-50` in one block made the insert fail, which rolled back the
  session's whole fit and errored its key for good: every detector, every trace. It is now keyed
  by the condition's place in its block, the name a column beside it.
  `test_condition_names_differing_only_in_case_are_two_conditions` failed first, on the duplicate
  key, then passed.
- **The five Review Focus inputs** were each checked and hold.
- **Deferred minors:**
  - A condition's saccades are not always a subset of its block's: a trial whose `TRIAL_END` comes
    after its `BLOCK_END` counts its late saccades toward its condition and not its block (§4.3
    read literally).
  - The saccade spec's §6.5.2 pointer names "a non-converging fit" but not amendment 1's
    relative-error check.
  - `cli/report.py` has three blank lines before `_vigor_lines` and one after it.
  - Untested paths: a saccade between trials, a trial with no `BlockTrial`, a report line with
    several detectors, and vigor for a session whose own fit was refused.
- **Set aside by the reviewer, and ruled to stand,** among sixteen:
  - An errored key reads "not computed yet" in the report, as the daemon's every stage does.
  - A fit paramset registered later computes nothing for a reclaimed session until it is
    rehydrated, since `make()` reads the raw recording and rig record.
  - Other test modules plant computed `EyeDetection` rows with no recording, so this stage may log
    an error on them once per test run; the suite is green in its order on both interpreters.

## 7. The deferred minors, fixed

**Branch:** `fix/main-sequence-minors`. The requester chose this work on 2026-10-08 and approved
its design the same day.

- **A condition holds only the saccades inside its own block** (spec amendment 5). A trial whose
  `TRIAL_END` comes after its `BLOCK_END` no longer counts the saccades in its tail toward its
  condition; they count toward the block they fall in, if any. Repeated block numbers, after a
  crash restart before wl-xcon's XC-026, change too: amendment 5 says how.
- **The saccade spec's §6.5.2 pointer** names amendment 1's checks: a fit that does not converge,
  or a V_max or C with a standard error of `max_relative_se` (0.5 by default) of its value or
  more. The main-sequence spec's own §9 says so too.
- **`cli/report.py`'s blank lines** around `_vigor_lines` follow PEP 8.
- **The four untested paths are tested:** a saccade between two trials of a block that has
  conditions; a trial running past its block's end; a trial with no `BlockTrial` row (all in
  `test_a_condition_holds_only_saccades_inside_its_block`); a report line with two detectors in
  paramset order; and vigor shown where the session's own fit was refused.

**The branch's review** (Opus, fresh): "with fixes", one Important finding, fixed. The
between-trials saccade sat in a block with no conditions, so the test could not catch it leaking
into the trial before; it now sits between two named trials. Its three minors were fixed at the
requester's choice: BMD paired with Engbert-Kliegl so the order test tells paramset order from
name order; both specs' wording of amendment 1; and amendment 5's two further cases.

**Found by the full suite, not by the review:** the two-detector report test planted a pair of
detections whose runs did not tile their traces. `DetectorAgreement` scored that pair in a later
module's daemon pass and raised `TilingError`, erroring two `test_consensus_populate.py` tests.
The planted runs now tile their traces, with `fixation` between saccades, as a real detection's
do.

**Full suite on the branch's final code,** with every reference variable set except
`WLPP_OHDPI_REFERENCE`:
- **3.11:** 2276 passed, 26 skipped, 1 deselected, 1 xfailed, no errors.
- **3.13:** 2276 passed, 27 skipped, 1 xfailed, no errors.
