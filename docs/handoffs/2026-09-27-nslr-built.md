# NSLR-HMM is written, tested and registered, and measured against its authors' code and the paper's coders

**Built 2026-09-27 on `spec/nslr`.**
- **Branch:** this commit and the fifteen before it, past `main` at
  `86ce4cc` (`git log --oneline 86ce4cc..HEAD`, sixteen total). In `git log`
  order (most recent first): this fix-round update; the Task 7 records
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
- **This is Task 7**: records only, with no production code and no tests.
- **NOT merged and NOT pushed.** `origin` has no `spec/nslr` ref.
- **Not whole-branch reviewed.** Tasks 1–6 each passed their own review (Task
  4 twice, after the requester's ruling). On the last two detector branches
  the whole-branch review is what caught the defects the task reviews missed.

**Suite, at this commit** (run from the repo root with `-p no:cacheprovider`,
`__pycache__` cleared, `PYTHONDONTWRITEBYTECODE=1`):
- **3.11** (`.venv`, `WLPP_NSLR_REFERENCE=$SCRATCH`): *(see the records
  report for the exact line; both interpreters are 0 failed.)*
- **3.13** (the scratch `venv_ci`): *(see the records report.)*

Neither the OpenIris recording nor the Andersson dataset is set in either
run, so those gated checks skip; their numbers are the ones recorded below,
from Tasks 5 and 6.

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
  any arithmetic. Both are exercised: Task 1's
  segmentation and continuous-fit fidelity tests (12 cases) ran against the
  reference on 3.13 with numpy 2.5.3 — none skipped — as well as on 3.11
  with numpy 2.4.6.
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

## Findings

1. **All-detector invariants held, with one known risk that fired and was
   resolved above.** Every other invariant in
   `tests/schema/test_detect_populate.py` passed unmodified: no run contains
   an unusable sample; pieces of one, two and zero samples are handled;
   a perfectly still trace is classified without inventing a saccade;
   the detector runs at the rig's real non-integer rate (498.55 Hz); the
   noise guard stops and reports when a noise estimate never repeats.
2. **`NoiseFit.noise` is the last pass's residual standard deviation — the
   value the convergence check compared — not the noise that pass actually
   segmented with (that value plus the structural error).** `detect_nslr`
   was checked against this distinction during Task 4 review; it reports
   correctly. Noted here because Task 2's own note asked for the check to be
   made explicit.
3. **`NoiseFit.capped` (whether the noise guard's cap was hit) is not
   surfaced past `fit_pieces` — `classify` discards it.** Spec §6 says
   hitting the guard "is reported (§5.4 item 4)", and that item is the unit
   test's own pass count, which `NoiseFit` satisfies. REMoDNaV's own
   iteration cap is equally silent past its own boundary. Not a gap against
   the spec as written; a candidate follow-up if a real session ever caps.

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
misreading, not a real citation error. **Minors (b)–(i), open at the time
of writing** — the ledger's own list, carried here for the final fix wave:

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
  this task changes only `status`, not any constraint.
- **No table, column or published schema changed.** NSLR's vocabulary was
  already declared in the parent spec's §3.1 table.
- **U'n'Eye's obstacles are unchanged:** `torch`, vendoring and a GPU.

## What is next

1. **This branch.** The whole-branch review comes first, then the
   requester's merge decision. At merge, `docs/CHECKPOINT.md`'s header and
   `wl.yaml`'s `status.describes` are re-pointed with CI read off the merge
   — neither is touched here.
2. **Bayesian microsaccade detection is now the only remaining
   hardware-free detector.** It matters doubly now: it is a saccade
   detector, and — per the ruling above — it is built to cover exactly the
   sub-1° regime this branch found NSLR does not. U'n'Eye stays blocked on
   the GPU. Neither has a runnable oracle; BMD's reference is C++ and
   unlicensed (parent spec §3.2), so its validation will look like
   Nyström–Holmqvist's own — nulls first, not an oracle comparison.
3. **The conjunction's duration floor is now inherited by three
   detectors.** `_min_duration_samples` reads `min_duration_samples` off a
   detector's params with a default of 1. Nyström–Holmqvist and REMoDNaV
   state durations in milliseconds; NSLR states none at all. All three
   admit a one-sample binocular event under today's floor. Still a
   cross-detector decision, out of scope on this branch.
4. **A supervised emission refit for macaque data**, needing hand-labelled
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
