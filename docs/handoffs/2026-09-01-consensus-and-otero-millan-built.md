# A second detector exists, so agreement is measurable

**Built 2026-09-01** on `spec/consensus-and-otero-millan`, sixteen commits from
`fea978b`. Stage 2A of the saccade-detection design
(`docs/superpowers/specs/2026-08-31-saccade-detection-design.md`).

Suite **1284 passed, 5 skipped, 1 deselected, 1 xfailed**. Python 3.13 subset
(`--noconftest tests/eye tests/contracts`): 313 passed, 4 skipped, 1 xfailed.
`wl-check`: no findings. **Never pushed; CI has not seen any of it.**

---

## What it is for

Stage 1 shipped the substrate and one detector, which cannot disagree with
anything. This adds the second, and the machinery that makes a disagreement a
number: the coarsening lattice and comparability rule (§6.1), two metrics and
their registry (§6), Otero-Millan, the `DetectorAgreement` table, and the
report's agreement line — the one §9 deliberately left out of stage 1.

## What shipped

| | |
|---|---|
| `eye/detect/consensus.py` | lattice, comparability rule, `event_f1`, `cohen_kappa`, `CONSENSUS_METRICS` |
| `eye/detect/otero_millan.py` | the second detector, reimplemented from the paper and a read-only MATLAB reference |
| `schema/consensus.py` | `DetectorAgreement`, keyed per §6.1 with `vocabulary` and `pso_as` in the KEY |
| `cli/report.py` | `### Detector agreement`, replacing stage 1's deliberate absence |

Engbert–Kliegl and Otero-Millan declare the same vocabulary, so this first pair
exercises neither coarsening nor exclusion — §6.1's simplest case, and the
cleanest possible first comparison.

**Measured, on the reference recording:** Otero-Millan 4,724 events on the left
eye in 0.28 s against Engbert–Kliegl's 5,972 in 0.08 s; `event_f1` 0.801 (left)
and 0.783 (right); `cohen_kappa` well below both, which is the difference the
two metrics exist to expose. `comparison_mask` costs 0.55 s per call over
1,177,799 samples — about 1.2 min per session at seven detectors.

---

## Read this before trusting a number

**Otero-Millan's rows are PROVISIONAL**, on §3.2's BMD terms. Not because the
reimplementation is suspect — it was verified line by line against the
reference — but because **no oracle-free statistic on an uncalibrated recording
has been found that discriminates it.** A calibrated session is the
highest-value thing that would change this. The two statistics with real
potential are the paper's own: amplitude bimodality (needs a calibration to
place the modes) and its 62%/78% error reductions (needs labelled ground truth).

**The main-sequence bound was WITHDRAWN, not relaxed**, and the reason
generalises. Task 4 asserted that log-log amplitude against peak velocity
correlates at r > 0.9, on the argument that "no clustering artefact reproduces
it". Four independent measurements killed it: Engbert–Kliegl scores LOWER on
the identical trace (0.754); duration-matched random spans score HIGHER
(0.872, and 0.905 when spans are placed freely); r is scale-dependent on an
uncalibrated recording (0.574 to 0.881); and **a deliberately broken detector —
both acceptance gates removed, 11,590 events against 4,700 — scores 0.949, and
passes the bound the correct detector fails.**

Duration is one channel and not the whole mechanism: partialling it out of the
duration-matched control barely moves it, and within narrow duration strata the
control still beats the detector at every stratum. The statistic fails to
discriminate at ANY duration.

**The rule that follows, and it binds the next no-oracle validation: an
oracle-free statistic is worthless until a null has been run against it.**
Building the null took ten minutes and overturned the task's central check.
Bayesian microsaccade detection is the next detector with no oracle, and it is
the next case.

---

## What stage 2B inherits

- **The other five detectors** — Nyström–Holmqvist, NSLR, REMoDNaV, Bayesian
  microsaccade detection, U'n'Eye.
- **Three of them cannot be registered today.** `schema/detect.py::
  _conjunction_label` raises `UndecidedConjunctionLabel` for any vocabulary not
  a subset of `{saccade, microsaccade}`, deliberately, because §2.5 forbids
  defaulting the glissade assignment. NH, NSLR and REMoDNaV all emit `pso`.
  Resolving that is a design conversation, not an implementation detail.
- **§6.1's comparability rule is WRONG for a disjoint-vocabulary pair.**
  U'n'Eye `{saccade}` against BMD `{microsaccade, drift}`: the declarations
  share nothing, so the pair scores in `{fixation}` alone — yet
  `coarsen(microsaccade, {saccade})` is `saccade`, so the two meet perfectly at
  `{saccade, fixation}`. The rule requires each side's label to reach the
  OTHER's declaration, where a comparison needs one common vocabulary both map
  INTO. Recorded as a **strict `xfail`**
  (`test_disjoint_vocabularies_should_meet_at_their_common_coarsening`), so the
  suite fails the moment someone fixes the rule and leaves the spec's paragraph
  standing.
- **`blended_agreement`** (§6's N-way metric) — deferred deliberately. With two
  detectors it equals the pairwise number. Note that nothing yet says how it
  should treat a `nan` pairwise row: `np.mean` propagates, `np.nanmean` swallows.
- **§6.5 needs an amplitude floor AND a duration ceiling** in its paramset.
  18.2% and 20.4% of accepted events sit below the 0.2° floor they were
  accepted by — 17 at exactly 0.0°, longest 702 ms — because that floor is a
  cluster MEAN and sub-floor members ride in on the average. `reliability` does
  NOT reliably flag them; filtering by it makes the amplitude distribution
  worse (0.817 → 0.796 at ≥0.5 → 0.731 at ≥0.8). The existing "include
  microsaccades" switch does not cover this: that is about an event CLASS, this
  is about rows failing their own detector's acceptance rule.
- **The greedy silhouette stop DECIDES Otero-Millan's cluster count** on ~85%
  of traces rather than converging to it (167/200 measured, 173/200 on an
  independent fixture mix; the ~85% is robust, the exact count is not). This is
  faithful to the reference, not a defect introduced here — which is §3.2's
  warning one level deeper than it anticipated: the method itself, not only a
  reimplementation of it, can produce a disagreement that looks like a finding.

## Mutations that survive, named rather than counted

Five behaviour-changing mutations survive the suite in `otero_millan.py`,
recorded in its module docstring: the candidate-budget `ceil`, the
`_CLUSTER_CHUNK_EVENTS` value, and three others. Two tests are **canaries
rather than pins** — they fire for many unrelated mutations with messages like
`assert 0 == 3` that would not tell a maintainer which step broke. Two mutants
are genuinely equivalent and worth knowing: mean-centring in `_whiten` is a
no-op because features arrive globally z-scored, and `_silhouette`'s
multi-cluster branch is UNREACHABLE from `_cluster_peaks`, because
`np.minimum(assignment, 2)` leaves at most one other cluster. That is dead code
in a live function.

In `schema/consensus.py`, mutation 8's closure (deleting the coarsening step)
rests on a single test, because both registered detectors share a vocabulary.

## Two environment facts that cost time

**A DataJoint join between two projections of the same table behaves
differently depending on whether `~lineage` exists.** It raises where lineage
is available and **silently drops rows** where it is not —
`datajoint/condition.py` returns early with a warning, and
`expression.py` joins on all non-hidden namesakes regardless. Reproduced by
dropping the table: it dropped precisely the trace where the two detectors
disagreed. Nothing else in this repository does such a join today; this is a
note for whoever writes the next one.

**The Otero-Millan paper cannot be read by text extraction.** It is not in PMC,
the ARVO site refuses automated fetches, and the PDF maps `°` to `8` and drops
minus signs, so "0.2°" extracts as "0.28". Read it rendered. Note also that the
paper contains **no main sequence and no measured microsaccade rate** — the
1–2/s is the literature's range as the paper reports it — and that its
k-selection sentence ("smallest average silhouette") contradicts its own
Results and Figure 5A. The one genuine corroboration the paper gave: its
five-per-second is the CANDIDATE BUDGET, independently confirming
`_PEAK_BUDGET_PER_MIN_ISI`, which had been derived from the MATLAB alone.

## How this was built, since it is the transferable part

Across six tasks, the defects that mattered were overwhelmingly **untrue
sentences**: four stale comments, five numbers measured correctly and then
written down wrongly, one vacuous assertion whose fixture made it inert, one
test whose name claimed coverage it lacked, and one validation check that
licensed broken detectors while rejecting the correct one. The missing tests
were never the expensive part.

Two methods found nearly all of it. **Mutation with restore** found every rule
that another rule was silently covering for. **Building the null first** killed
the one check that would have shipped a wrong conclusion. Both are cheap; the
second took ten minutes.

One caution about fixtures, which produced three separate false results here: a
fixture whose shape does not match what the code consumes yields confident
wrong evidence. `runs_from_labels` returns MAXIMAL runs, so events planted at
adjacent samples merge into one; the synthetic generator writes `DataQuality`
at 100.0 everywhere, so a stepped session has zero unusable samples; and a
whole test file sharing one seed hid the step its module argues hardest for.

> **Task 6 was interrupted** by a connection loss after committing its code
> (`035da50`) but before writing its report. The suite, `wl-check` and the 3.13
> subset were verified afterward and are green. A measurement it had in flight
> — a stronger per-chunk restatement of the margin sensitivity — was lost with
> it; the recorded per-trace figure above stands and is what is evidenced.
