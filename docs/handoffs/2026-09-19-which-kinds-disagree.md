# Which kinds disagree: the pair was guessed right, the direction was not

Branch `measure/which-kinds-disagree`, forked from `main` at `5f68161`.

> **Not merged, not pushed, CI has not run.** By this project's own recurring
> lesson — now on its fifth recorded instance — assume that sentence is false
> the moment the branch lands, and check `git log --oneline -1` rather than
> trusting it.

The question this answers is the one
`handoffs/2026-09-12-the-reference-checks-ran-and-the-eyes-agree.md` left at
the top of its own "what is still NOT measured" list, and the one
conjunction-shape design spec §6 open question 1 ends on:

> Which kind pairs those are — almost certainly `saccade` against `pso`, the
> two eyes placing a saccade's own trailing glissade boundary differently —
> is NOT measured, and is the obvious next question.

It is measured now. The pair was guessed right. The direction was not
guessed at all, and the direction is the finding.

---

## 1. What was measured

`_kind_agreement` (`tests/eye/detect/test_nystrom_holmqvist_validation.py`)
already bucketed each eye's runs into `agree` / `disagree` / `alone`. It now
also records WHICH two kinds disagreed, as a `Counter` keyed by the ordered
pair `(own kind, other kind)`, partitioning `disagree` exactly.

Three things had to come with it or the number would not have been readable:

**A chance baseline** (`_expected_pair_shares`). A dominant pair is not
automatically a finding: if one eye is mostly `saccadic` and the other
carries plenty of `pso`, then `saccadic`-over-`pso` is the commonest
disagreement for a reason that has nothing to do with binocularity. The
baseline is the two kind marginals with the agreeing diagonal removed and
the rest renormalised — the same rule the headline rate already obeys, read
against chance rather than against zero, applied to the breakdown.

**An attribution rule.** One own-run can overlap several counterparts of
different kinds and is still exactly one disagreement. It is attributed to
the counterpart sharing the most samples, ties broken by start order.
Otherwise the breakdown stops summing to `disagree` and its shares cannot be
quoted against the headline rate.

**A per-kind denominator.** A share of disagreements says which pair is
common; only a share of the kind's OWN population says what §1 costs that
kind. That distinction turned out to carry the whole result.

---

## 2. The finding

Measured on `OpenIris-2024Jul31-114628.txt`, the same recording and the same
per-eye Nyström–Holmqvist traces as the 2026-09-12 round, so the headline
numbers below are unchanged from it.

| direction | pair | n | share | chance | ratio |
|---|---|---|---|---|---|
| left→right | `pso` over `saccadic` | 711 | **0.9861** | 0.4894 | **2.01×** |
| left→right | `saccadic` over `pso` | 10 | 0.0139 | 0.5106 | 0.03× |
| right→left | `pso` over `saccadic` | 835 | **0.9766** | 0.5106 | **1.91×** |
| right→left | `saccadic` over `pso` | 20 | 0.0234 | 0.4894 | 0.05× |

`saccade` against `pso` is not the commonest disagreement, it is
**essentially the only one** — 721 of 721 and 855 of 855. Nothing else
appears, because Nyström–Holmqvist emits no other non-fixation kind.

**The two eyes do not disagree symmetrically.** Nearly every disagreement is
one eye calling a stretch a glissade while the other is still calling it a
saccade. The mirror is depleted to a thirtieth of its chance share. Chance
would split these almost evenly (0.489 / 0.511); the measurement finds 0.986
/ 0.014.

**Restated as a cost, §1's agreement requirement is not a uniform tax:**

| direction | kind | dropped | of its own runs | rate |
|---|---|---|---|---|
| left→right | `pso` | 711 | 2,017 | **0.3525** |
| left→right | `saccadic` | 10 | 5,062 | 0.0020 |
| right→left | `pso` | 835 | 2,167 | **0.3853** |
| right→left | `saccadic` | 20 | 5,213 | 0.0038 |

**Better than a third of every detected glissade is discarded by the
binocular agreement rule, against two to four tenths of a percent of
saccades.** The cost falls almost entirely on `pso` — the one kind the
conjunction-shape spec exists to store. The "about a fifth" this project
recorded on 2026-09-12 is a true average over two kinds that are not
affected alike, and it concealed this.

**The mechanism is a hypothesis, not a measurement, and is labelled that way
in the spec.** A glissade is short (18–20 ms, measured) and sits immediately
after a saccade; a saccade is long. If the two eyes place a saccade's offset
differently by about a glissade's duration, one eye is already in its `pso`
while the other is still inside its `saccade` — exactly this pair, exactly
this direction. The asymmetry follows: a long own `saccade` nearly always
finds some same-kind counterpart and agrees, while a short own `pso`
swallowed by the other eye's saccade has none. **What would settle it** is
the distribution of (other eye's saccade offset − own eye's saccade offset)
over these 711 and 835 events — centred near one glissade duration if the
mechanism is right, near zero if it is not.

---

## 3. How it was verified

**Written test-first.** Every one of the eight new tests was watched failing
before the code that satisfies it existed — the first five on
`AttributeError: '_Agreement' object has no attribute 'pairs'`, the two
baseline tests on `NameError: name '_expected_pair_shares' is not defined`.

**Mutation battery, by literal source mutation and revert**, because this
file has already shipped two tests that looked adequate and survived
mutation (2026-09-12). Ten mutations, nine caught, each by a named test:

| mutation | caught by |
|---|---|
| pair direction reversed | `..._names_the_own_eyes_kind_first` (+3 more) |
| tie rule relaxed to `>=` | `..._tied_on_overlap_are_broken_by_start_order` |
| first counterpart wins instead of largest | `..._attributed_to_the_larger_overlap` |
| breakdown never recorded | 5 tests |
| constructor drops the passed pairs | 5 tests |
| agreeing diagonal left in the baseline | both baseline tests |
| the two marginals swapped | both baseline tests |
| kind filter dropped from a marginal | both baseline tests |
| baseline shares left unnormalised | both baseline tests |

**The tenth SURVIVED, and is recorded rather than quietly dropped.**
Changing the `best_overlap` sentinel from `-1` to `0` passes the whole file.
It is unreachable: the two differ only for a `floor` below 1, and
`_min_duration_samples` defaults to 1, so no caller can produce one. The
sentinel is kept at `-1` for exact equivalence with the boolean it replaced,
and the comment at that line says plainly that no test discriminates it —
rather than leaving it looking covered.

**Suite counts, freshly run for this document**, `__pycache__` cleared,
`PYTHONDONTWRITEBYTECODE=1`, Python 3.11 from the repo root. Without the
recording: **1354 passed, 9 skipped, 1 deselected, 1 xfailed**. With
`WLPP_OHDPI_REFERENCE` set: **1362 passed, 1 skipped, 1 deselected, 1
xfailed, 2 warnings**. Both are the prior round's counts plus this branch's
eight new tests, none removed.

**Those 2 warnings are not new and are not ours.** Both come from REMoDNaV's
own `clf.py:915` (`numpy.core is deprecated`, raised twice by
`test_remodnav_finds_a_comparable_number_of_saccades`), and they appear only
under `WLPP_OHDPI_REFERENCE` because that is the only run in which the
oracle test executes at all. This project records suites as "zero warnings"
elsewhere, so the number is stated here rather than left for a reader to
find and wonder about: it is a third-party deprecation inside a `dev`-only
test oracle, on a code path this repository does not own.

**3.13 cross-check, hand-run**, per this file family's standing convention:
`tests/eye tests/contracts --noconftest` in a 3.13 venv with no DataJoint —
**360 passed, 8 skipped, 1 xfailed** — and the gated measurement re-run there
against the real recording returns **byte-identical numbers** to 3.11. A
green 3.11 run is evidence about 3.11 and nothing else; this project has
already lost a day to that.

---

## 4. Findings worth carrying forward

**`_KIND_OF` is still restated in two places**, and this branch adds a third
consumer of the restated copy (`_kind_mix`). The fix has not moved: put it in
`eye/detect/labels.py`, where `tests/eye/` and `schema/detect.py` can both
import one definition. It is vocabulary knowledge, not schema knowledge.
Deliberately not done here for the same reason as last time — it is a
production change to the conjunction, outside a measurement's scope — but the
drift surface is now larger than when that was written.

**The breakdown is printed, not asserted, and the reason is a general one.**
A bound on these shares would be tuned to the run that produced them. Worse,
it would fire on an *improvement*: if the mechanism above is right, a better
glissade offset criterion should shrink this asymmetry. The only new
assertion is an identity — that the pairs partition `disagree` — which cannot
fire on a detector getting better.

---

## 5. What is next

1. **The offset-difference measurement named in §2**, which settles the
   mechanism. It is the cheapest item and it is what turns "a third of
   glissades are dropped" into either a tolerance to widen or a detector to
   fix.
2. **Move `_KIND_OF` to `eye/detect/labels.py`.** Now has three consumers of
   the duplicated copy rather than two.
3. Four detectors still unwritten: NSLR, REMoDNaV (the detector, not the PyPI
   oracle), Bayesian microsaccade, U'n'Eye (which wants the GPU). Gap-aware
   segmentation and rehydration remain the hardware-free pieces outside
   detection.

The compute machine exists but is not assembled, so Phase 2b stays blocked
and hardware-free work is still the right call.
