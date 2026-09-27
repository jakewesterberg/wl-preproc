# NSLR-HMM is written, tested and registered, and measured against its authors' code and the paper's coders

**Built 2026-09-27 on `spec/nslr`.**
- **Branch:** this commit and the twenty-two before it, past `main` at
  `86ce4cc` (`git log --oneline 86ce4cc..HEAD`, twenty-three total). In
  `git log` order (most recent first): this records commit for the
  conjunction round; the conjunction floor (`ca0a74b`); the final fix
  wave's records (`0912164`); the capped-warning wording (`8e3e784`); the test and code
  minors and `continuous_fit`'s rename (`4663caf`); non-finite gaze and the
  capped warning (`992aba5`); NSLR's measurement rule (`4483cc9`); the Task
  7 fix-round update (`d57f12b`); the Task 7 records
  commit (`a539b7b`: the measurements, the licence correction, this
  handoff); the human-coder harness (`cde2acd`); real-data measurement
  (`08c75c9`); the planted-step invariant, holding NSLR to steps at or
  above 1° (`4ce4ad0`); the requester's spec correction after that finding
  (`cc0a669`); registration (`93ef359`); the normalisation null
  (`099d949`); fidelity nulls (`467d3a1`); pooled noise, features and
  decode (`0c921d4`); segmentation and the continuous fit, numba added
  (`ad266d7`); the plan (`91b7f77`, `56f0dd0`, `8749d0e`); the spec
  (`1af89b3`, `e8cec1f`).
- **Documents:** design spec
  `docs/superpowers/specs/2026-09-27-nslr-design.md`; plan at
  `docs/superpowers/plans/2026-09-27-nslr.md` (path recorded in
  `.superpowers/sdd/2026-09-27-nslr/plan-path`), seven tasks. The per-task
  reports and the ledger are in `.superpowers/sdd/2026-09-27-nslr/`.
- **Last updated by the conjunction round's records commit.** Task 7 wrote
  this handoff (records only); the final fix wave and its conjunction round
  corrected it (below).
- **Merged and pushed 2026-09-27 as `c7903dd`**, the requester's choice. Its
  tree is byte-identical to the tested head `71a3bcc`; CI on `c7903dd` is green
  on both interpreters and the manifest check (run `36325492781`).

  *This said "NOT merged and NOT pushed. `origin` has no `spec/nslr` ref." until
  the merge; true when written.*
- **Whole-branch reviewed, and fixed.** Tasks 1–6 each passed their own
  review (Task 4 twice, after the requester's ruling). The final
  whole-branch review found one Critical defect, two Important ones (the
  second is parked minor (d)) and five minors that no task review could
  see. The final fix wave implements all of them, with the requester's
  decision on the Critical one (see "The final whole-branch review, and its
  fix wave"). Its scoped re-review approved it with residuals: a second
  requester decision, on NSLR's conjunction, and five wording slips. The
  conjunction round fixes both.

  *Until the conjunction round this said "A scoped re-review of the fix
  wave comes next"; true when written.*

  *Until the fix wave this said "Not whole-branch reviewed"; true when
  written.*

**Suite, at this commit** (run from the repo root with `-p no:cacheprovider`,
`__pycache__` cleared, `PYTHONDONTWRITEBYTECODE=1`):
- **3.11** (`.venv`, `WLPP_NSLR_REFERENCE=$SCRATCH`): 1642 passed, 21 skipped,
  1 deselected, 1 xfailed, 0 failed.
- **3.13** (the scratch `venv_ci`, no `WLPP_NSLR_REFERENCE`): 1612
  passed, 52 skipped, 1 xfailed, 0 failed.
- **The gated NSLR files on 3.13** (`WLPP_NSLR_REFERENCE=$SCRATCH`):
  `test_nslr.py`, `test_nslr_fidelity.py` and `test_nslr_validation.py`: 46
  passed, 4 skipped, 0 failed. The four skips are the OpenIris and
  Andersson gates; nothing gated on the reference skipped.

*The final fix wave's own records commit (`0912164`) recorded 1641 and 1611
passed here; the conjunction round added one test.*

Neither the OpenIris recording nor the Andersson dataset is set in these
runs, so those gated checks skip; their numbers are the ones recorded
below, from Tasks 5 and 6 and the fix wave's own probes.

*Until the fix wave these lines were placeholders pointing at the records
report, which sits in the gitignored `.superpowers/sdd/`; the ledger had
the numbers then: 1617 passed on 3.11 and 1587 on 3.13, 0 failed.*

---

## What was built

**NSLR-HMM is the fifth registered detector, and the only one that never
differentiates.** `wl_preproc/eye/detect/nslr.py` reimplements Pekkanen &
Lappi (2017)'s segmented-linear-regression method, following
`gitlab.com/nslr/nslr`'s `slow_nslr.py` (67d03f8) and
`gitlab.com/nslr/nslr-hmm`'s `nslr_hmm.py` (3598fee) operation for operation
(design spec §1, §2):

- the pruned, PELT-style segmentation search, with greedy continuity;
- the split prior;
- the continuous piecewise-linear fit;
- pooled noise estimation over pieces, with an exact-float-repeat stopping
  rule and this implementation's own guard (`max_noise_passes`);
- the two per-segment features (log-speed, Fisher-transformed turn);
- the published HMM (fixed emissions, forbidden transitions, half-weight
  fixation↔pursuit) decoded by Viterbi, with the reference's own asymmetric
  emission normalisation reproduced.

`registry.py::DETECTORS["nslr"]` declares
`{saccade, pso, fixation, pursuit}`, with defaults `DEFAULT_NSLR_PARAMS`, the
published values. Its saccadic slice is `{saccade}`, so its conjunction runs
take `_conjunction_label`'s degenerate branch, as Nyström–Holmqvist's and
REMoDNaV's do. It has no minimum duration, so it inherits the conjunction's
one-sample floor — now shared by three detectors (below).

**NSLR never computes velocity.** `detect_nslr` accepts the shared estimator
because `DetectFn` requires it, and ignores it: a segment's own slope is its
velocity, and that is the method. It never sees a stored trace either — like
every detector here, it reads gaze as a computation.

**The segmentation loop is compiled with `numba.njit`, no `fastmath`** — the
requester's choice on 2026-09-27, after measuring plain Python at about 25
minutes per eye for a two-hour session (spec §8 item 2's own correction). No
transcendental function runs inside the compiled loop: the likelihood
constant and the split prior's per-sample value are computed in numpy,
outside it, exactly as the reference computes them, and passed in.

**Along the way:**
- `numba>=0.67` became a runtime dependency (Task 1), bringing llvmlite.
  **numba's own ceiling matters beyond this branch**: its own metadata
  declares `numpy<2.6,>=1.22`, so this pipeline's numpy is now capped
  wherever numba is, and a numpy release past that waits on numba —
  `wl.yaml`'s `why` records the ceiling, `numpy<2.6`.
- **Two numpy adapters let the AGPL-3.0 reference run on current numpy**
  (spec §2, §7), applied to the loaded `nslr_hmm` module only, never to numpy
  itself: `float()` on a one-element array, which numpy 2.4 rejects with
  `TypeError` (the reference calls it at `nslr_hmm.py` 298, 300 and 302),
  becomes `.item()`; and `np.row_stack`, which numpy 2.5 removed, becomes
  `np.vstack`, identical for the single array it is given. Neither changes
  any arithmetic. Both adapters sit on `nslr_hmm` (its lines 80, 298, 300
  and 302), so they are exercised by the tests that call `nslr_hmm`: Task
  2's feature, decode and transition-matrix comparisons, Task 3's
  whole-algorithm fidelity check and Task 5's real-data exactness check.
  Tasks 2 and 3 ran against the reference on 3.13 with numpy 2.5.3, none
  skipped, as well as on 3.11 with numpy 2.4.6. Task 1's segmentation and
  continuous-fit tests call only `slow_nslr`, which needs neither adapter.

  *Until the fix wave this credited Task 1's tests with exercising the
  adapters; they cannot, since they never call `nslr_hmm`.*
- **The reference is never installed** — its C++ does not build with
  current compilers and has no working pure-Python fallback, and PyPI's own
  `nslr` 0.0.5 falls back to the mean intercept on every root hypothesis
  (spec §2). CI clones both checkouts at their pinned commits into
  `$RUNNER_TEMP` and sets `WLPP_NSLR_REFERENCE`; nothing from them is
  committed.
- **The loader for the Andersson human-coded dataset was extracted** into
  `tests/eye/detect/_andersson.py` (Task 6), shared between
  `test_remodnav_validation.py` and `test_nslr_validation.py`. REMoDNaV's own
  gated output was diffed before and after the move and is byte-identical.

## What was measured

Three checks, mirroring REMoDNaV's shape (design spec §5).

**1. Is the algorithm right?** Task 1–4, `test_nslr.py` /
`test_nslr_fidelity.py`, run in CI. Given the reference's own operations,
reproduced exactly, `classify` matches the reference at every stage — split
indices, endpoints, pass count and final noise, features, Viterbi path, and
every sample's label — on synthetic traces spanning all four classes, on
Python 3.11 with numpy 2.4.6 and 3.13 with numpy 2.5.3. Three nulls
(no greedy continuity, no emission normalisation, no Fisher transform on the
turn feature) each fail the check, as they must.

**2. What does real data show?** Task 5, gated on `WLPP_OHDPI_REFERENCE` and
`WLPP_NSLR_REFERENCE`.
- **Exactness.** Each eye's first gap-free run of at least 5,000 samples in
  the reference recording (`OpenIris-2024Jul31-114628`, p99→15° scale,
  498.55 Hz) is classified by both ours and the reference. Identical, both
  eyes, both interpreters — the numpy adapters hold on real data, not only
  on synthetic traces.
- **Runtime.** Each eye over the full recording (1,177,799 samples): **38.3 s
  left, 33.3 s right** — faster than spec §5.2's own "about a minute per eye"
  prediction, and identical to the reported precision on both interpreters.
- **Agreement, over each eye's first 120,000 samples, recorded rather than
  gated (there is no oracle for what it should be):**

  | | left | right |
  |---|---|---|
  | nslr saccades | 505 | 478 |
  | vs remodnav: saccades / kappa | 519 / 0.453 | 488 / 0.421 |
  | vs nystrom_holmqvist: saccades / kappa | 568 / 0.364 | 569 / 0.315 |

  Fewer saccades than either registered detector, and higher agreement with
  REMoDNaV than with Nyström–Holmqvist — the expected shape for a detector
  that treats slow sub-degree movement as fixation, not a defect.

  *Corrected 2026-09-27, by the final whole-branch review: that reading is
  true of the 120,000-sample slice above and false over the whole recording. Over all
  1,177,799 samples NSLR stores more saccade rows than either registered
  detector: 5,786 left and 5,216 right, against REMoDNaV's 4,814 and 4,493
  and Nyström–Holmqvist's 5,009 and 5,123. 55% of its left-eye saccade rows
  and 64% of its right-eye rows are shorter than 10 ms (spec §5.2).*

**3. Does it reproduce the paper's own numbers?** Task 6, gated on
`WLPP_ANDERSSON_DATA`, computed **the paper's own way** (per-class kappa
against the rest, blink/undefined samples omitted, averaged over the two
coders) — deliberately different from REMoDNaV's `kappa()` harness. **Pooled
over the 34 "data used in the article" files**, as measured — that file set
reproduced both bands directly, so the harness never needed the dataset's
full annotated set:

| | saccade | fixation | pursuit | PSO |
|---|---|---|---|---|
| coders (paper) | 0.898 (0.90) | 0.813 (0.81) | 0.791 (0.79) | 0.733 (0.73) |
| nslr (paper) | 0.826 (0.82) | 0.535 (0.51) | 0.460 (0.42) | 0.556 (0.53) |

Both bands held on both interpreters — the coders' within ±0.01 (largest
diff 0.003) and NSLR's within ±0.05 (largest diff 0.040, on pursuit, the
tightest band, exactly as predicted during design). The null (random labels)
scored kappa near zero. **This check is in-sample**: the HMM's emissions
were fitted on this same labelled data, so it tests the reimplementation
against the paper, not NSLR's generalisation (spec §5.3).

## The requester's ruling, and why it stands

**On 2026-09-27, after Task 4's known risk fired** (the planted-step
invariant, held for the first time to every registered detector, found NSLR
missing one of three planted saccades on the synthetic `stepped_session`
fixture), **the requester ruled: keep NSLR as published, do not use it for
movements under about 1°, and note it may need retuning.**

- **What actually happened, checked against the reference itself.** NSLR
  segments the missed step exactly (18 ms), and calls it fixation. At ~42°/s
  it sits 2.5 standard deviations below the published saccade class's mean,
  and just as far above fixation's; fixation→PSO is forbidden by the
  transition structure, so PSO is not available either. **The authors' own
  reference, run on the identical trace, gives identical labels.** This is
  the published model's limit, not a reimplementation defect — the same
  distinction Task 4's controller check existed to make.
- **The fixture was not changed and NSLR was not exempted** — per the
  requester's own long-standing rule ("fix the fixture or the detector,
  never exempt it"). Instead the invariant itself was corrected:
  `_NOT_USED_BELOW_1_DEG` (`tests/schema/test_detect_populate.py`, commit
  `4ce4ad0`) names every detector this limit applies to (today, only
  `nslr`), holds it to the planted steps at or above 1°, and pins its miss
  of the smaller one — so a future change either way (the miss disappearing,
  or a new detector also missing it unremarked) fails the suite.
- **Recorded in the spec** (`2026-09-27-nslr-design.md` §4, commit
  `cc0a669`): "Not used below 1°" — the limit is speed, not amplitude, but
  small saccades are slow in any primate, so in practice this reads as a
  size limit. §8 item 1 was corrected in the same commit: the reference's
  own Baum–Welch re-estimation is not the route, because the paper itself
  and the reference's own demo both warn it converges to bad solutions on
  real data.
- **What this raises.** A supervised refit — hand-label monkey recordings,
  recompute the sixteen emission values the authors' own way, register a
  second paramset — is the route past the limit, and it needs hand-labelled
  monkey data this lab does not yet have. Out of scope here. **This is also
  why Bayesian microsaccade detection, the one remaining hardware-free
  detector, rises in priority**: it is the one method in design spec §3.1
  built to model exactly the sub-1° regime NSLR now explicitly does not
  cover.

## The rulings made during execution, and what each costs if wrong

Three, all recorded in the ledger
(`.superpowers/sdd/2026-09-27-nslr/progress.md`).

1. **Task 2's commit trailer names "Claude Sonnet 5" — the implementer's own
   harness attribution — where `global-constraints.md` had given the
   "Claude Opus 5.5" fallback line.**
   - **The ruling:** accepted, and `global-constraints.md` now tells every
     implementer to use the attribution their own harness gives them, so
     the trailer names whichever model actually wrote the commit; the
     `Claude-Session` line is unchanged.
   - **Cost if wrong:** a cosmetic trailer difference, amendable before
     merge.
2. **Task 3's emission-normalisation null was re-pinned from a trace at
   seed 4 to one at seed 12.**
   - **The problem:** the null (Viterbi without per-step emission
     normalisation) is supposed to prove the fidelity check can see a
     missing normalisation. Dividing each step's emissions by their sum
     shifts every candidate equally in log space, so it only changes the
     decoded path where the 1e-6 clip bites unevenly across candidates —
     and seed 4 never exercises that; seed 12 does (Task 2's mutation row 2
     had already measured the path diverging at segment 22 on this exact
     trace).
   - **The ruling:** re-pin the null's trace to `(500.0, 12)`, with a
     comment explaining why. This was the plan's defect, not the
     implementation's.
   - **Cost if wrong:** a null that proves less than it claims — caught at
     re-review, which is what happened: fix round 1 re-pinned it and 203 of
     1,500 samples differ under the broken variant, on both interpreters.
3. **Parked minor (a) is withdrawn.** Task 1's reviewer had flagged
   `nslr_hmm.py`'s `float()` calls as being at lines 297, 299 and 301 in the
   pinned checkout, against 298, 300 and 302 cited by spec §2 and
   `_nslr_reference.py`.
   - **The ruling:** withdrawn, after this task's own grep of the pinned
     checkout (`$SCRATCH/nslr-hmm` at `3598fee`) confirmed the calls sit at
     298, 300 and 302 — the citation was right all along; the reviewer
     misread the lines.
   - **Cost if wrong:** one docstring cites lines one off from the pinned
     checkout, which is cosmetic.

**Three further notes, briefly**, not rulings:
- **Task 1's mutation rows 3–4 (the `>=` comparisons at the winner and the
  prune step) turned out to be tie-only** — a 720-case probe found no
  difference between `>` and `>=` there. Recorded as a plan error, not a
  defect: the tests still gate the code, they just do not discriminate that
  particular mutation.
- **Task 3's normalisation null, above, was re-pinned to seed 12** (folded
  into the ruling directly above; noted again here because the brief asked
  for it as its own line item).
- **The Andersson loader was extracted into `tests/eye/detect/_andersson.py`**
  (Task 6), shared between REMoDNaV's and NSLR's validation modules.
  REMoDNaV's gated output was diffed before and after the move, ignoring
  timing lines, and found byte-identical — every printed kappa, the oracle
  exclusion message, and the pass/skip/fail counts.

## The final whole-branch review, and its fix wave

The final review (opus, range `91b7f77..d57f12b`) found one Critical defect,
two Important ones (I1 below, and the parked minor (d)) and five minors
(M1–M5), and triaged the parked minors. The
requester decided the Critical one; the controller ruled on the rest. All
of it is in `4483cc9`, `992aba5`, `4663caf`, `8e3e784` and `0912164`. A
second decision by the requester the same day, extending the 10 ms floor
to NSLR's conjunction, is in `ca0a74b` and this commit (the conjunction
round, below).

**C1 (Critical): what an NSLR saccade row's measurement means.**
- **The defect.** `_insert_trace` measured every saccade run with
  `measure`, whose amplitude is `gaze[stop-1] - gaze[start]`. NSLR's run k
  is `[J[k], J[k+1])`, and the eye lands at `J[k+1]`, the next run's first
  sample. So `measure` missed the last step of every NSLR saccade row, and
  read a one-sample run as exactly 0.0°. On the full reference recording 542 left-eye and 666
  right-eye rows were stored at 0.0°, at a median 234 °/s on the left.
- **The requester's decision (2026-09-27).** NSLR's labels stay as the
  published model gives them. For its per-eye saccade runs only, a run of
  10 ms or more is measured up to where the eye lands (amplitude
  `gaze[stop] - gaze[start]`, peak velocity over `[start, stop]`
  inclusive), when `stop` is an interior knot. A briefer run is stored with
  both measurements NULL. The reason: an NSLR saccade under 10 ms is below
  the 1° scope already set, because a real saccade of 1° or more lasts well
  over 10 ms.
- **Where the two rules live (the controller's ruling).** The landing rule
  is structural: `registry.Detector.runs_end_before_landing`, true for NSLR
  and off for every other detector. The floor changes stored values, so it
  is a paramset field, `NslrParams.min_measured_saccade_ms`, set to 10.0 in
  `DEFAULT_NSLR_PARAMS` (the field itself has no default), in the hash. `measure.py::measure_event_run` is the pure rule, and
  `_insert_trace` now receives the detector and its params from `make()`.
- **Per-eye traces only.** The conjunction keeps `measure`.

  *Superseded 2026-09-27, the same day, by the requester's second decision:
  the 10 ms floor applies to NSLR's conjunction too, and the landing rule
  stays off there, because a conjunction span does not end on an NSLR knot.
  A longer NSLR conjunction row keeps `measure`.*
- **Measured after the fix** (full reference recording):

  | | left | right |
  |---|---|---|
  | NSLR saccade rows | 5,786 | 5,216 |
  | stored unmeasured (under 10 ms) | 3,186 (55.1%) | 3,355 (64.3%) |
  | measured to the landing sample | 2,355 | 1,640 |
  | measured at the piece's own last knot | 245 | 221 |
  | measured at exactly 0.0° | 7 | 3 |

  The 10 rows still at 0.0° are not lost landings. Each lasts 10 ms or
  more, and the gaze at its end is exactly the gaze at its start: an
  out-and-back on quantized data. REMoDNaV stores 19 saccade rows at
  exactly 0.0° on the same recording, and Nyström–Holmqvist 5.
- **Every other detector's rows are unchanged.**
  `test_every_other_detectors_measurements_are_unchanged` requires every
  other detector's stored saccade rows, on every trace, and NSLR's
  conjunction rows, to equal `measure` exactly. A full-precision dump of
  every stored run row on all six detection fixtures, before and after,
  differs only in NSLR's per-eye saccade rows.
- **What it did not reach.** NSLR's conjunction still stores one-sample
  saccade rows at 0.0°: 219 of 3,230 on the reference recording, at a
  median 150 °/s. `_overlapping`'s floor guarantees `stop > start`, not a
  nonzero amplitude. That is the conjunction's duration floor, out of scope
  here and open for the requester (spec §8 item 4).

  *Resolved 2026-09-27, the same day (the conjunction round), and the item
  above superseded, true when written.* After the requester's second
  decision, 2,023 of NSLR's 3,230 conjunction saccade rows are stored
  unmeasured, and none is at 0.0°. A full-precision dump of every stored
  run row (five detectors, three traces, six fixtures) is identical before
  and after that round. No fixture has an NSLR conjunction saccade under
  10 ms, so the conjunction floor is pinned without MySQL, through the real
  `_insert_trace`. Every other detector's conjunction is unchanged, and on
  the reference recording it still holds 36 rows at exactly 0.0°:
  Engbert–Kliegl 12, Otero-Millan 11, REMoDNaV 9 and Nyström–Holmqvist 4.
- **Downstream.** No production code reads `amplitude_deg` or
  `peak_velocity_deg_s` yet. `schema/consensus.py` reads only a run's
  bounds and label, and `cli/report.py` only the master rows' counts.
  `SaccadeMainSequence` (parent spec §6.5) is not built; when it is, it must
  read NULL as "too brief to measure" and leave those rows out of the fit.

**I1 (Important): one NaN corrupted a whole eye, silently.** The shared
mask passes NaN as usable, and NSLR's noise is pooled over the eye, so one
NaN made it NaN everywhere: all 50 passes ran, `capped` was discarded, and
pieces holding no NaN were relabelled. Now `detect_nslr` withholds
non-finite gaze itself, and `classify` warns (`RuntimeWarning`) when the
noise loop is capped, keeping the last pass. **The shared mask's NaN gap is
recorded, not changed** (spec §8 item 6): every other detector still takes
a NaN the mask offers, because changing the mask would change stored
validity under an unchanged paramset. The reference recording has no
non-finite gaze at a sample the mask offers.

**The minors.** (d), (h) and (i) are fixed (the dispositions are under
"Deferred, and NOT done"). M1 is this handoff's corrections, marked where
they stand. M2 put the pinned commits and numba's numpy ceiling into
`wl.yaml` and `pyproject.toml`. M3 corrected three stale docstrings in
`schema/detect.py`. M4 corrected spec §9 and the parent spec's citation.
M5 renamed `continuous_fit`'s locals in Thomas-algorithm terms, so it no
longer keeps the AGPL reference's identifiers. Its control flow stays
parallel to the reference's, because bit-exactness needs the same
operations in the same order. It is byte-identical to the previous version
on 3,012 segmentations, on both interpreters, and the gated fidelity tests
pass.

**The records.** The "fewer saccades" reading is corrected where it stood
(spec §5.2, this handoff, CHECKPOINT): it holds on the 120,000-sample slice
only.

**The conjunction round (2026-09-27).** The fix wave's scoped re-review
approved it with residuals.
- **The requester's second decision.** NSLR's 10 ms floor applies to its
  conjunction trace too. An NSLR conjunction saccade row under 10 ms is
  stored with both measurements NULL, and a longer one keeps `measure`. The
  landing rule stays off there.
- **Measured on the reference recording:** 2,023 of NSLR's 3,230
  conjunction saccade rows are now unmeasured, and none is at 0.0°. Every
  other detector's conjunction is unchanged, and still holds 36 rows at
  exactly 0.0°: Engbert–Kliegl 12, Otero-Millan 11, REMoDNaV 9 and
  Nyström–Holmqvist 4.
- **Every other detector's rows are unchanged.** A full-precision dump of
  every stored run row (five detectors, three traces, six fixtures) is
  identical before and after the round.
- **How it is pinned.** No fixture has an NSLR conjunction saccade under
  10 ms, so the floor is pinned through the real `_insert_trace` without
  MySQL, and turning it off for the conjunction alone fails that test.
- **Still open:** whether a conjunction needs a minimum event duration at
  all. It is shared by four detectors, not three: Otero-Millan's floor is
  one sample too (spec §8 item 4).
- **Five wording slips corrected in this handoff:**
  - the "stored measurements" invariant, whose contract changed;
  - "read 1/L short", which became "missed the last step": 5 of the 18
    re-measured fixture rows got smaller;
  - the undated "fewer saccades" corrections, now dated;
  - M5 changed identifiers, not control flow;
  - `min_measured_saccade_ms` has no class default.

## Findings

1. **All-detector invariants held, with one known risk that fired and was
   resolved above.** Every other all-detector invariant in
   `tests/schema/test_detect_populate.py` (planted onsets, tiling, the
   conjunction, the run-count bound) passed unmodified. The invariant on
   stored measurements passed too, and its contract then changed in the fix
   wave: an NSLR saccade run under 10 ms carries no measurement
   (`test_saccade_runs_carry_measurements_and_others_do_not`).
   Separately, NSLR's own Review Focus unit tests pass:
   `tests/eye/detect/test_nslr.py` checks that no run contains an unusable
   sample, that pieces of one, two and zero samples are handled, that the
   detector runs at the rig's real non-integer rate (498.55 Hz), and that
   the noise guard stops and reports when a noise estimate never repeats;
   `tests/eye/detect/test_nslr_fidelity.py` checks that a perfectly still
   trace is classified without inventing a saccade.

   *Until the fix wave this item attributed those NSLR unit tests to
   `tests/schema/test_detect_populate.py`; they live in `tests/eye/detect/`.*
2. **`NoiseFit.noise` is the last pass's residual standard deviation — the
   value the convergence check compared — not the noise that pass actually
   segmented with (that value plus the structural error).** `detect_nslr`
   reports no noise at all. What is reported now is `classify`'s
   `RuntimeWarning` when the noise loop hits `max_noise_passes`: it names
   the cap, the pass count and "that pass's residual noise", which is
   `NoiseFit.noise`.

   *Until the fix wave this said `detect_nslr` "reports correctly"; it
   reported nothing to be correct about.*
3. **`NoiseFit.capped` is now surfaced: `classify` warns when it is set**
   (the final review's finding I1, below), and keeps the last pass, as spec
   §6 says. REMoDNaV's own iteration cap is still silent past its own
   boundary, which is outside this branch.

   *Until the fix wave this said `classify` discards `capped`, "not a gap
   against the spec as written; a candidate follow-up if a real session
   ever caps". The final review found a single NaN makes it cap.*

## What has NOT been measured

- **Anything on macaque data.** The reference recording and the Andersson
  dataset are both human. The split prior and every HMM emission value are
  human priors (spec §8 item 1) — the same open question REMoDNaV's own
  handoff raised for its pursuit fixture, from the opposite direction.
- **Whether NSLR's own segmentation, run on a genuine monkey saccade
  distribution, needs retuning beyond the below-1° limit already found.**
  The requester's ruling names this as a live possibility, not a settled
  answer.
- **Cross-platform exact-float termination** beyond this machine and CI's
  platform (spec §8 item 5): the noise loop's stopping rule is an exact
  float-tuple repeat, so a platform whose `log` differs by an ULP could take
  a different number of passes. The guard bounds the worst case; nothing
  has exercised a platform where it differs.

## Deferred, and NOT done

**Parked minor (a) is withdrawn** (see the rulings above) — a reviewer
misreading, not a real citation error. **Minors (b)–(i)**, the ledger's own
list, were carried here for the final fix wave. Their dispositions, from
the final reviewer's triage and the controller's rulings:
- **(b) closed:** `gaze_trace` has been used since Task 4.
- **(c) deferred:** `divide` is never raised inside that block (0/0 raises
  `invalid`, and `arctanh`'s argument is scaled strictly inside (−1, 1)), so
  narrowing it is cosmetic.
- **(d) fixed:** the pooling test now requires the returned noise to be
  exactly the pooled residuals' standard deviation, and to differ from each
  piece fitted alone. A per-piece `fit_pieces` fails it.
- **(e) deferred:** cosmetic duplication.
- **(f) withdrawn:** pieces are maximal usable runs, so runs from two
  pieces can never touch; a test would pin a tautology.
- **(g) deferred:** the module-scoped recording fixture is needed by the
  runtime test anyway, and in CI `WLPP_OHDPI_REFERENCE` is unset, so the
  fixture skips first.
- **(h) fixed:** the exactness test reports the differing count and the
  first mismatches.
- **(i) fixed:** the module docstring names both gated sections and their
  gates, and that the Andersson data is GPL-3.0 and never committed.

The list as it stood before the fix wave:

- **(b)** `test_nslr.py` imports `gaze_trace` unused until Task 4's tests
  landed (preflight-known).
- **(c)** `features`'s `np.errstate` also ignores `divide`, where the
  comment names only the `0/0` case — narrow to `invalid="ignore"` alone if
  `divide` is never actually hit.
- **(d)** `test_the_noise_is_one_estimate_pooled_over_every_piece` asserts
  shape and the split only, not that the noise is genuinely pooled across
  pieces — strengthen it.
- **(e)** `features` repeats `_log10_clipped`'s formula rather than calling
  it.
- **(f)** No unit test pins that two adjacent pieces carrying the same label
  stay two separate runs (true by construction today, from Task 4's
  re-review).
- **(g)** The exactness test (Task 5) calls `nslr_reference()` after the
  module-scoped recording fixture has already loaded, so with only
  `WLPP_NSLR_REFERENCE` unset it still reads the ~633 MB recording before
  skipping.
- **(h)** That same test's failure message names only the eye, where
  `test_nslr_fidelity.py`'s own failure message reports the differing
  sample count and the first mismatches.
- **(i)** `test_nslr_validation.py`'s module docstring names only §5.2's
  gate (`WLPP_OHDPI_REFERENCE`), not §5.3's `WLPP_ANDERSSON_DATA` section.

**Pre-existing things seen in passing, not NSLR's:**
- `tests/cli/test_report.py::test_gather_readings_returns_the_values_build_report_renders`
  flaked once, attributed to free disk space (the same TOCTOU shape recorded
  on the REMoDNaV and Nyström–Holmqvist branches); it did not recur.
- An untracked, self-referential `./wl-preproc` symlink (the repo root
  pointing at itself) appeared at 09:43 on 2026-09-27, origin unknown. Left
  in place, excluded from every commit on this branch, same as Task 3 found
  and reported it.

## What is unaffected

- **`runs_on` and `builds_on` are unchanged.**
- **`third_party`:** `numba`, `nslr` and `nslr-hmm` were added in Task 1;
  Task 7 changed only `status`. The fix wave changed no constraint: it put
  each pinned commit into the `nslr` and `nslr-hmm` `why` (`wlo stack` does
  not read `pinned_at`, which stays), and the numpy<2.6 ceiling numba
  imposes into `numpy`'s `why` and beside `numba>=0.67` in
  `pyproject.toml`.
- **No table, column or published schema changed.** NSLR's vocabulary was
  already declared in the parent spec's §3.1 table. The fix wave changed
  what NSLR's per-eye saccade rows store in two existing, already nullable
  columns (`amplitude_deg`, `peak_velocity_deg_s`), and edited a comment
  line in `EyeDetection.Run`'s definition to say so.
- **Every other detector's stored rows are unchanged** by the fix wave,
  shown by test and by a before/after dump (below).
- **U'n'Eye's obstacles are unchanged:** `torch`, vendoring and a GPU.

## What is next

1. **This branch is merged** (`c7903dd`, 2026-09-27). The conjunction round's
   re-review approved it; `docs/CHECKPOINT.md`'s header and `wl.yaml`'s
   `status.describes` now name `c7903dd`, with CI read off it. The last parked
   minor, comment-only, landed with that re-pointing: `registry.py`'s
   REMoDNaV and NSLR entries now name Otero-Millan among the detectors
   sharing the one-sample conjunction floor.

   *This item said the conjunction round's re-review and the merge decision
   came next until the merge; true when written.*

   *Until the fix wave this said "The whole-branch review comes first"; it
   has happened. Until the conjunction round it said the fix wave's scoped
   re-review came first; that has happened too, and approved the wave with
   the residuals this round fixed.*
2. **Two open items for the requester**, recorded and not changed here:
   - **The shared mask passes NaN as usable, for every detector** (spec §8
     item 6). NSLR now withholds non-finite gaze itself; every other
     detector still takes a NaN the mask offers. Changing the mask would
     change stored validity under an unchanged paramset.
   - **NSLR's conjunction still stores one-sample saccade rows at 0.0°**:
     219 of 3,230 on the reference recording, at a median 150 °/s (spec §8
     item 4). The requester's measurement decision covers per-eye rows only,
     and the conjunction's duration floor is out of scope.

     *Resolved 2026-09-27, the same day, and superseded (true when
     written): the requester applied the 10 ms floor to NSLR's conjunction
     too. None of its conjunction rows is at 0.0° now. Whether a
     conjunction needs a minimum event duration at all is still open (item
     4 below).*
3. **Bayesian microsaccade detection is now the only remaining
   hardware-free detector.** It matters doubly now: it is a saccade
   detector, and — per the ruling above — it is built to cover exactly the
   sub-1° regime this branch found NSLR does not. U'n'Eye stays blocked on
   the GPU. Neither has a runnable oracle; BMD's reference is C++ and
   unlicensed (parent spec §3.2), so its validation will look like
   Nyström–Holmqvist's own — nulls first, not an oracle comparison.
4. **The conjunction's duration floor is now inherited by three
   detectors.** `_min_duration_samples` reads `min_duration_samples` off a
   detector's params with a default of 1. Nyström–Holmqvist and REMoDNaV
   state durations in milliseconds; NSLR states none at all. All three
   admit a one-sample binocular event under today's floor. Still a
   cross-detector decision, out of scope on this branch.

   *Corrected 2026-09-27 (the conjunction round): four detectors, not
   three. Otero-Millan's params declare no `min_duration_samples` either,
   and 7 of its 3,888 conjunction rows on the reference recording are one
   sample long. NSLR's conjunction now takes its 10 ms measurement floor,
   so none of NSLR's one-sample conjunction events carries a measurement;
   whether a conjunction needs a minimum event duration at all is still
   open.*
5. **A supervised emission refit for macaque data**, needing hand-labelled
   monkey recordings this lab does not yet have (spec §8 item 1). Not
   started.

## Read these, in this order

1. `docs/superpowers/specs/2026-09-27-nslr-design.md`: §2 (paper against
   code), §4 (what it emits, and the "not used below 1°" ruling), §5 (the
   three checks, now with their measurements), §8 (open questions).
2. `.superpowers/sdd/2026-09-27-nslr/progress.md`: the ledger, with the
   known-risk episode, both rulings and every parked minor.
3. `.superpowers/sdd/2026-09-27-nslr/task-4-report.md` for the planted-step
   finding and the controller's check against the reference; `task-5-report.md`
   for the real-data numbers; `task-6-report.md` for the Table 1 harness.
4. `.superpowers/sdd/2026-09-27-nslr/final-fix-brief.md` for the final
   review's findings and every ruling on them, and `final-fix-report.md` for
   what the fix wave did, its mutation checks and its measurements.
