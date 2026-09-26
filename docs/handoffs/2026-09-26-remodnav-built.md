# REMoDNaV is written, tested and registered, and measured against its oracle and the paper's coders

**Built 2026-09-26 on `spec/remodnav`.**
- **Branch:** fourteen commits past `main` at `5ba4d01`
  (`git log --oneline main..HEAD`), counting the spec (`750b10a`,
  `b467003`), the plan (`c63c485`), ten task commits (`02402f3` through
  `70db8dd`), and this records commit.
- **Documents:** design spec
  `docs/superpowers/specs/2026-09-26-remodnav-design.md`; plan
  `docs/superpowers/plans/2026-09-26-remodnav.md`, eight tasks. The per-task
  reports and the ledger are in `.superpowers/sdd/2026-09-26-remodnav/`.
- **This is Task 8**: records only, with no production code and no tests.
- **NOT merged and NOT pushed.** `origin` has no `spec/remodnav` ref.
- **Not whole-branch reviewed.** Tasks 1–7 each passed their own review.
  On the last two detector branches the whole-branch review is what caught
  the defects the task reviews missed.

**Suite, at this commit** (run from the repo root with `-p no:cacheprovider`):
- **3.11** (`.venv`): **1570 passed, 17 skipped, 1 deselected, 1 xfailed**,
  19 warnings.
- **3.13** (Python 3.13.9, the scratch `venv_ci`): **1569 passed, 19
  skipped, 1 xfailed**, 19 warnings.

Both have 0 failed, and 1589 tests were collected on each. The two gated
recordings are not set in either run, so their checks skip. The numbers
below come from the gated runs recorded in Tasks 6 and 7.

---

## What was built

**REMoDNaV is the fourth registered detector and the first real producer of
`pursuit`.** `wl_preproc/eye/detect/remodnav.py` reimplements the method of
Dar, Wagner & Hanke (2021), following `remodnav` 1.1.2's `clf.py` wherever
the code defines behaviour (spec §2):
- the σ-scaled adaptive threshold;
- the major pass, which chunks the recording at its largest saccades with a
  1 s context window;
- asymmetric onset and offset;
- PSOs;
- the intersaccadic recursion that finds small saccades with a local
  threshold;
- the 4 Hz fixation-or-pursuit split.

`registry.py::DETECTORS["remodnav"]` declares
`{saccade, pso, fixation, pursuit}`, with defaults `DEFAULT_REMODNAV_PARAMS`,
the paper's Table 1. Its saccadic slice is `{saccade}`, so its conjunction
runs take `_conjunction_label`'s degenerate branch, as Nyström–Holmqvist's
do.

The production signals follow spec §3:
- the primary speed is the shared estimator's;
- the candidate speed is the shared estimator's speed of 50 ms
  median-filtered positions;
- the validity mask is the only noise definition;
- the Stampe filter, the oracle's own dilation and `max_vel` are dropped.

Along the way:
- **`true_runs` moved to `eye/detect/labels.py`** at its third use (Task 1).
  Engbert–Kliegl and Nyström–Holmqvist each had a private copy.
- **scipy became a runtime dependency**, `scipy>=1.17`, in `pyproject.toml`
  and `wl.yaml` (Task 3). The detector calls `median_filter`, `butter` and
  `filtfilt(method='gust')`, as the oracle does.
- **`pursuit` reaches production for the first time.**
  `tests/schema/test_detect_populate.py::test_a_binocular_slow_ramp_produces_a_pursuit_conjunction_run`
  stores it binocularly through `daemon.run_once()`, on a new
  `pursuit_session` fixture (seed 621). The test was verified to fail when
  pursuit detection is mutated off.

## What was measured

Three checks, each answering a different question (spec §5).

**1. Is the algorithm right?** Task 4, `test_remodnav_fidelity.py`, runs in
CI. Given the oracle's own preprocessed signals, `classify` labels every
sample exactly as `remodnav` 1.1.2 does, with **0 differing samples in all
five cases**: synthetic `gaze_trace`, 500 Hz seeds 1–3 and 1000 Hz seeds
4–5. Both nulls fail the check, as they must: a raw MAD, and on/offset
searched at the peak threshold.

**2. What does the shared preprocessing cost?** Task 6, gated on
`WLPP_OHDPI_REFERENCE`.
- **Recording:** `OpenIris-2024Jul31-114628`, its leading 120,000-sample
  slice, p99→15° scale, 498.55 Hz.
- **Ours:** the shared estimator and mask.
- **Oracle:** `remodnav` 1.1.2 end to end, on its own preprocessing.
- **Environment:** scipy 1.17.1, Python 3.11.

| | left | right |
|---|---|---|
| saccades, ours / oracle | 519 / 574 | 488 / 565 |
| count ratio | 1.106 | 1.158 |
| kappa, saccade | 0.764 | 0.724 |
| kappa, PSO | 0.391 | 0.438 |
| kappa, fixation | 0.823 | 0.816 |
| kappa, pursuit | 0.267 | −0.008 |

- **The prediction's order held**: saccade above PSO above pursuit.
- **The null passed.** Duration-matched random spans stay under the 0.1
  ceiling.
- **Runtime:** the whole recording, 1,177,799 samples per eye, classifies in
  2.5 s (left, 12,613 runs) and 2.6 s (right, 12,158 runs). That answers
  spec §8 item 5: fast.

**3. Does it do what the paper reports?** Task 7, gated on
`WLPP_ANDERSSON_DATA`, uses Andersson et al. (2017), human data, 500 Hz.

The harness proves itself first:
- **The coders' own agreement (MN-RA) reproduces Table 3 within ±0.006 on
  all 9 cells, over all 34 files.**
- **The oracle's reproduces all 18 cells within ±0.05 in the suite**
  (scipy 1.17.1, 33 files).
- **Out of the suite, on scipy 1.13.1, the untrimmed oracle reproduces all
  18 within 0.005, over all 34 files.** So the harness is right, and
  `remodnav` 1.1.2 has not drifted from the paper's numbers.

Then ours:

| saccade kappa | ours (US-RA / US-MN) | oracle (AL-RA / AL-MN) |
|---|---|---|
| images | 0.809 / 0.802 | 0.761 / 0.759 |
| dots | 0.756 / 0.829 | 0.721 / 0.777 |
| videos | 0.798 / 0.832 | 0.779 / 0.811 |

- **Ours is at or above the oracle on every saccade cell.** The prediction
  allowed it to be as much as 0.05 below.
- **Fixation and PSO kappas are recorded, not gated.** Spec §5.3 has the
  full table, beside Table 3.

## The rulings made during execution, and what each costs if wrong

There were four. The ledger (`.superpowers/sdd/2026-09-26-remodnav/progress.md`)
records each in its own lines: Task 7's trim took three rulings and then a
widening.

1. **Task 6's saccade-kappa floor**, ruled in the preflight scan, before
   Task 6 ran.
   - **The ruling:** on the reference recording, saccade kappa is asserted
     above the null's ceiling (`NULL_KAPPA_CEILING = 0.1`). Spec §5.2 had
     said the kappas were "not gated".
   - **Why:** a null means something only if the real comparison must beat
     it.
   - **Cost if wrong:** a legitimately low saccade kappa, on a future
     recording or after an estimator change, fails a gated test. That would
     surface a finding rather than hide one.
   - **Status:** spec §5.2 now states the floor. The PSO, fixation and
     pursuit kappas stay ungated.
2. **The test baseline `_pattern` became period-5 `[1, 2, 3, 4, 5]`**
   (Task 3), in place of Task 2's period-4 `[1, 2, 3, 2]`.
   - **Why:** Task 2's reviewer confirmed that the period-4 baseline's median
     absolute deviation sits on an exact 50/50 split. A bump's parity tips
     that split into a degenerate threshold, and Task 3 added five more tests
     on the same baseline. The period-5 baseline holds median 3 and median
     |deviation| 1 with a 10-point margin, and Task 2's parity workarounds
     were reverted.
   - **Cost if wrong:** churn in Task 2's test comments. Task 2's tests
     still gate the code.
3. **The oracle's edge trim (Task 7): window-wide, with ours and the oracle
   over the same files.**
   - **The problem:** under scipy ≥ 1.17, `remodnav` 1.1.2's `preproc`
     raises in `savgol_filter`'s edge fit whenever a recording's edge window
     holds a missing sample. Untrimmed, 9 of the 34 RA files raised.
   - **The ruling came in steps:**
     - trim the leading and trailing missing samples;
     - measure the paper's exact computation once, out of suite, on old
       scipy;
     - never widen a tolerance;
     - then widen the trim, so that each edge's 9-sample Savitzky–Golay
       window (`int(0.019 * fs)` at 500 Hz) holds no missing sample.
   - **How the file sets ended up:** AL-RA, AL-MN, US-RA and US-MN share 33
     files. MN-RA stays on all 34, because dropping the one excluded file
     moves MN-RA's own (video, Fix) cell from 0.6527 to 0.6374, outside its
     ±0.006.
   - **Cost if wrong:** a few edge samples per affected file enter as
     unlabelled, so the in-suite oracle numbers differ from the paper's
     untrimmed computation at those samples. Measured out of suite on
     scipy 1.13.1, the trim moves no kappa by more than 0.001.
4. **The bounded oracle-failure exclusion** (Task 7's review fix).
   - **The ruling:** one file,
     `video/UL31_video_triple_jump_labelled_RA.mat`, still raises after the
     trim. `preproc`'s internal `dilate_nan` (`clf.py` 846–863) widens its
     91-sample interior gap by 5 samples on each side, leaving 4 clean
     samples at the end. The file is declared in `EXPECTED_ORACLE_FAILURES`,
     and `test_the_oracle_fails_only_on_the_diagnosed_file` fails if another
     file fails, if this one stops failing, or if its message changes.
     `_oracle` now raises `_NoCleanEdgeWindow` instead of returning all-zero
     labels.
   - **Cost if wrong:** the video AL and US numbers permanently exclude one
     of nine video files. A scipy or `remodnav` change that fixes or moves
     the failure turns a named test red, and the constant is then edited by
     hand. The `_NoCleanEdgeWindow` path is verified only by mutating the
     constant; no real file reaches it.

The preflight scan made one more ruling, about a test rather than a
measurement.
`test_a_trace_shorter_than_every_window_classifies_without_error` stands as
a no-crash pin: its assertion is vacuous when the output is empty, by
design. Cost if wrong: a reviewer flags it.

## Findings

1. **REMoDNaV reads moderate pursuit as saccades when holds dominate a
   recording.** This is spec §8 item 6. It is REMoDNaV's own behaviour, and
   the oracle does the same.
   - **The synthetic ohDPI session chops pursuit into saccades.** 0.03°
     per-eye jitter, the real validity mask, seeds 621–640 × 2 eyes. A
     5 °/s, 1.5 s pursuit becomes 10–18 short saccades per eye, in all 40
     eyes, from ours and from the oracle alike.
   - **The mechanism:**
     - the major pass thresholds the median-filtered candidate speed across
       the whole recording;
     - the holds set that threshold, at 6.17–6.82 °/s;
     - the 50 ms median passes a monotone ramp's jitter straight through;
     - REMoDNaV has no maximum saccade duration.
   - **On clean data, the same property turns a pursuit into a saccade.** On
     `gaze_trace` at 1000 Hz, the oracle absorbed the planted 1.5 s, 8 °/s
     pursuit into a saccade on seeds 4, 5 and 7. On seed 7 it became one
     saccade; on seeds 4 and 5, 1420 and 1262 of its 1500 samples became
     saccade. Of seeds 1–39, only 24, 26 and 37 kept the ramp separate.
   - **The pursuit fixture therefore runs at 2.1 °/s over 2.9 s**, 5% above
     the pursuit threshold. It is deterministic at seed 621, but the margin
     is narrow. Over seeds 621–720, 7 of 200 eyes carry one or two short
     saccades inside the ramp, though the conjunction still carried pursuit
     on all 100 seeds.
   - **It bears on macaque data**, where fixations dominate on a low-noise
     DPI. No current task moves a target (spec §8 item 1, answered by the
     requester). A future task that does may have its pursuit stored as
     saccade trains, which would inflate saccade counts. That is unmeasured.
2. **Table 3's script never loads the Zemblys et al. (2018) correction**,
   though the paper says it applied it.
   - **What the script does:** in `paper-remodnav`'s
     `code/mk_figuresnstats.py`, `load_anderson` reads
     `fix_by_Zemblys2018/` only for the name
     `UH29_img_Europe_labelled_FIX_MN.mat`. `confusion()` renames the file to
     that before loading it. `kappa()`, which produced Table 3, never does.
   - **So:** Table 3 was computed on the uncorrected file, and this harness
     follows `kappa()`. Re-read for Task 8: `confusion()` does load the fix,
     so within one script the correction reaches `confusion()` and not
     `kappa()`. Which of the paper's figures and tables `confusion()` feeds
     was not traced.
   - **A second error, in the spec's reference list:** it named a different
     Zemblys 2018 paper ("Using machine learning to detect events in
     eye-tracking data", 10.3758/s13428-017-0860-3) from the one the REMoDNaV
     paper cites (gazeNet, 10.3758/s13428-018-1133-5, its only Zemblys
     entry). Corrected in spec §10.
3. **The installed oracle and scipy ≥ 1.17 do not fully agree.**
   - **What breaks:** `remodnav` 1.1.2 raises on recordings whose edges hold
     missing samples (ruling 3 above).
   - **Where the output differs:** where both scipy versions run, the
     output mostly matches. It is not identical on `dots/UL31_trial1`, where
     the fixation/pursuit split differs by about 60–80 samples between scipy
     1.17.1 and 1.13.1. Both results are within tolerance.
4. **Spec §3 item 2 omitted one detail of the oracle.** The oracle
   median-filters Savitzky–Golay-smoothed positions (`clf.py` 858–863,
   ahead of 869–883); ours median-filters raw positions. The spec now says
   so.
5. **`_candidate_speed` zeroes withheld positions before scipy's
   `median_filter`.** scipy's running median otherwise lets a NaN change
   samples far past its window: up to 88 samples past a NaN block on one
   trace (scipy 1.17.1), and 48 on another in the Task 5 review. It is
   latent in production, where withheld positions are finite, and pinned by
   a test.
6. **Every all-detector invariant held with no ruling needed.**
   - Planted-step onsets are within 1 sample.
   - The consensus suite now runs C(4,2) = 6 pairs.
   - `test_daemon.py`'s detector count is 4.
   - On `near_miss_session`, REMoDNaV's conjunction keeps a 5-sample
     binocular overlap (5811–5816). That is the inherited one-sample floor,
     observed rather than only inferred (spec §8 item 4).
7. **The tests now depend on `remodnav` 1.1.2's exact behaviour and on its
   internals**, and the "no ceiling" reasoning written before this branch no
   longer holds.
   - **What they call:** the private `_detect_saccades` and
     `_fix_or_pursuit`, plus `get_adaptive_saccade_velocity_velthresh`,
     `find_peaks`, `find_movement_onsetidx` and `find_movement_offsetidx`.
   - **What was updated:** `wl.yaml`'s `remodnav` `why` now says so.
   - **Resolved 2026-09-27, in the final-review fix wave:** `remodnav` is
     pinned `==1.1.2` in `pyproject.toml`'s `dev` extra and in `wl.yaml`,
     and both now give the reason.

   *Until 2026-09-27 this said `pyproject.toml`'s comment still gave the
   old reason and that whether to pin 1.1.2 was still open; true when
   written.*

## What has NOT been measured

- **Anything on macaque data.** The reference recording is OpenIrisDPI's
  tutorial recording, not a session from this lab's rig. The coders' data
  are human.
- **Which side is right on the reference recording.** It has no ground
  truth; §5.2 measures agreement between two preprocessings of one
  algorithm.
- **Whether finding 1's mechanism contributes to the reference recording's
  pursuit kappas** (0.267, −0.008).
- **Runtime on a two-hour session.** It has about three times the reference
  recording's samples, and none has been timed.
- **Spec §8 items 2 and 3.** Neither has been measured on macaque data:
  whether fixational drift stays under 2 °/s after the 4 Hz low-pass, and
  whether `max_initial_saccade_freq` behaves on macaque tasks.

## Deferred, and NOT done

These are for the whole-branch review. The first two were fixed on this
branch on 2026-09-27, in the final-review fix wave; the rest were not.

*Until 2026-09-27 this said none was fixed on this branch; true when
written.*

- **DONE 2026-09-27: only the position mask was pinned at the
  `detect_remodnav` level.** The primary-speed and candidate masks were not
  pinned: dropping either changed the runs, and no test caught it. The
  Task 5 reviewer **recommended fixing this before merge**, with an
  invariance test that perturbs gaze and velocity at withheld samples and
  asserts identical output. `test_nothing_the_mask_withholds_changes_a_single_run`
  now does, and each mask's removal fails it.
- **DONE 2026-09-27: `remodnav.py`'s `_candidate_speed` docstring carried a
  one-trace "88 samples" figure.** The review measured 48 on another trace,
  and CI runs scipy 1.18.1. The docstring now says "well past its own
  window", and the test's docstring keeps both numbers.
- **Unused imports.** Task 6 flagged `math` and `Path` as unused in
  `test_remodnav_validation.py`. Task 7's harness now uses both, and a crude
  scan for Task 8 found no other unused import in the REMoDNaV files. Left
  for the review to confirm.
- **The oracle's `numpy.core` DeprecationWarnings** (`clf.py` 915). They are
  third-party and dev-only. A numpy release that removes the alias breaks
  every oracle comparison.
- **Smaller ledger minors:**
  - `_onset`/`_offset` are near-literal restatements of `clf.py` 86–112,
    which is worth a glance against "no oracle code transcribed";
  - `test_a_long_wobble_is_cut_at_the_pso_window` asserts a bound that
    slicing already guarantees;
  - `classify`'s "pairwise disjoint" claim is to be confirmed;
  - both fidelity nulls run only at 500 Hz, seed 1;
  - the pursuit fixture's margin is narrow;
  - the suites' warning count is unexplained;
  - the reference recording's velocity and mask are computed whole and then
    sliced;
  - `_oracle` takes a redundant `n`, and `mn_t`/`ra_t` are sliced twice;
  - `pyproject.toml`'s stale `remodnav` comment (finding 7).

## What is unaffected

- **`runs_on` and `builds_on` are unchanged.**
- **`third_party`:** `scipy` was added in Task 3. This task changed only the
  `remodnav` entry's `why`, and neither constraint.
- **No table, column or published schema changed.** `pursuit` was already
  its own kind in `KIND_OF`.
- **U'n'Eye's obstacles are unchanged:** `torch`, vendoring and a GPU.

## What is next

1. **This branch.** The whole-branch review comes first, then a push, and CI
   read on both interpreters. Merging is the requester's call. At merge,
   `docs/CHECKPOINT.md`'s header and `wl.yaml`'s `status.describes` are
   re-pointed with CI read off the merge. The requester decided on
   2026-09-26 that `rename-wl-exptasks` merges after this branch; it renames
   the task library to wl-exptasks.
2. **NSLR and Bayesian microsaccade detection**, the two remaining
   hardware-free detectors, which matter as saccade detectors. Neither has a
   runnable oracle. NSLR's classification half is AGPL-3.0 and its
   segmentation half declares no licence; BMD's reference is C++ and
   unlicensed (parent spec §3.2). So their validation will look like
   Nyström–Holmqvist's, with nulls first, not like REMoDNaV's. U'n'Eye stays
   blocked on the GPU.
3. **The conjunction's duration floor, now inherited by two
   millisecond-based detectors.** `_min_duration_samples` reads
   `min_duration_samples` off a detector's params with a default of 1.
   Nyström–Holmqvist and REMoDNaV state durations in milliseconds, so both
   admit one-sample binocular events. A 5-sample one is already observed. It
   is a cross-detector decision, out of scope on this branch.
4. **An open question for macaque data: REMoDNaV has no maximum saccade
   duration.** When a task first moves a target, measure whether its
   pursuit is stored as pursuit or as trains of short saccades (finding 1).
   The method has no maximum saccade duration to set, so any remedy — a
   second paramset, or a duration cap the method lacks — is a design
   decision, not tuning. Nothing is tuned now.
5. **DONE 2026-09-27: `remodnav` is pinned `==1.1.2`** as the dev oracle
   (finding 7). *Until then this item asked whether to pin it; true when
   written.*

## Read these, in this order

1. `docs/superpowers/specs/2026-09-26-remodnav-design.md`: §2 (paper
   against code), §3 (what is shared), §5 (the three checks, now with their
   measurements), §8 (open questions, items 5 and 6 new).
2. `.superpowers/sdd/2026-09-26-remodnav/progress.md`: the ledger, with
   every ruling and every deferred minor.
3. `.superpowers/sdd/2026-09-26-remodnav/task-5-report.md` for the pursuit
   finding, and `task-7-report.md` for the Table 3 harness. For Task 7, read
   "Fix round 2" and "Review fix"; the earlier rounds are the history.
