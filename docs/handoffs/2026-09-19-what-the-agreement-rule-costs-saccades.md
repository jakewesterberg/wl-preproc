# What the agreement rule costs saccades: 4.6–6.9%, and almost none of it is disagreement

Branch `measure/what-the-agreement-rule-costs-saccades`, forked from `main`
at `ba71bfd`.

> **Not merged, not pushed, CI has not run.** Eighth time this project has
> written that sentence knowing it goes stale on landing; check
> `git log --oneline -1`.

This branch exists because the day's two earlier findings were both about
`pso`, and the question that followed was whether any of it touched
`saccadic`. The honest answer at that point was a band 0.2%–12% wide,
because the `alone` bucket — own-runs the other eye did not detect at all —
had never been attributed by kind, while being dropped by §1's rule exactly
as a kind disagreement is.

It is attributed now, and both of the day's earlier numbers were
understatements.

---

## 1. The measurement

| direction | kind | disagreed | unmatched | total dropped | of its own runs |
|---|---|---|---|---|---|
| left→right | `pso` | 711 | 370 | 1,081 of 2,017 | **0.5359** |
| left→right | `saccadic` | 10 | 225 | 235 of 5,062 | **0.0464** |
| right→left | `pso` | 835 | 396 | 1,231 of 2,167 | **0.5681** |
| right→left | `saccadic` | 20 | 337 | 357 of 5,213 | **0.0685** |

**Glissades: better than a half, not better than a third.** **Saccades:
4.6–6.9%, not two to four tenths of a percent** — an order of magnitude out,
and out in the direction that matters.

---

## 2. What it changes

**The saccade cost is real but moderate, and it is a different phenomenon
from everything measured earlier today.** For `saccadic`, kind disagreement
is 10 and 20 runs — negligible, exactly as reported. Essentially the whole
cost is **225 and 337 saccades that one eye detected and the other did not
detect at all.** Not a labelling disagreement. Not boundary placement.
Nothing the glissade mechanism explains, and nothing a glissade-offset
criterion touches.

**That is the only part of §1's cost that reaches a pipeline with no
interest in glissades**, which is what makes it worth having. Whether ~5% is
acceptable is a judgement about the downstream use, not something this
measurement settles.

**It does not disturb the earlier mechanism finding.** The 711 and 835
pso-over-saccadic disagreements, the 10.03 ms offset displacement against a
6.02 ms control, the 70/76% inside-the-glissade split — all unchanged and
all still about the disagreement bucket, which is measured correctly. What
changed is that the disagreement bucket is not the whole of what §1 drops.

---

## 3. How it was missed, which is the part worth keeping

The gated report printed two numbers derived from the same statistic:

- `drop_rate`, for the population as a whole, **correctly** adding
  `disagree + alone`;
- a per-kind "cost to each kind" block, two lines below it, dividing **only
  `pairs`** by each kind's population.

Both were printed in the same block, on the same run, and the narrower one
carried the per-kind label and so was the one quoted into the spec, the
handoff, `wl.yaml` and `CHECKPOINT`. Nothing was wrong with either
computation; the label on the second was wrong, and a reader had no way to
see it without deriving `alone`'s split themselves — which was impossible,
because the statistic did not record it.

**Two things changed so it cannot recur.** The per-kind block now prints
`disagreed + unmatched = total` explicitly rather than a single number whose
provenance is invisible. And `len(unmatched) == alone` is asserted on the
real recording, alongside the two partition identities the earlier rounds
added, so all three buckets are now provably accounted for by the lists that
describe them.

---

## 4. How it was verified

**Test-first.** Four new tests, each watched failing on
`AttributeError: '_Agreement' object has no attribute 'unmatched'`.

**Both paths to `alone` are tested separately**, because this file has
already been caught by exactly that gap: the 2026-09-12 round found a
surviving mutation of the `alone` branch, because the only test expecting
`alone` had an `other` side that filtered to empty and so returned early
without ever executing the loop. `_kind_agreement` still has those two
paths, and the early return needed its own fix to populate `unmatched` —
a mutation reverting just that half is caught only by the early-path test.

**Mutation battery: five mutations, by literal source mutation and revert,
all caught.**

| mutation | caught by |
|---|---|
| unmatched never recorded in the main loop | 2 tests |
| unmatched not populated on the early-return path | `..._on_the_early_return_path_too` |
| `dropped_by_kind` counts disagreements only | `..._counts_both_reasons_...` |
| `dropped_by_kind` counts unmatched only | `..._counts_both_reasons_...` |
| `dropped_by_kind` charges the OTHER eye's kind | `..._counts_both_reasons_...` |

**Suite counts**, `__pycache__` cleared, `PYTHONDONTWRITEBYTECODE=1`:
**1370 passed** on 3.11 in the development venv (10 skipped, 1 deselected, 1
xfailed); **1369 passed** on 3.13 against a fresh CI resolution (12 skipped,
1 xfailed). The resolution was re-compiled before merging, per the rule
2026-09-13 left behind, and came back identical to both earlier compiles
today — nothing moved upstream across the day's three merges.

**Every earlier number on this statistic is unchanged**, confirmed by
re-running the kind-disagreement measurement against the real recording:
agree/disagree/alone, the pair breakdown, the chance baseline and the offset
measurement all return byte-identical values to `ba71bfd`.

---

## 5. What is next

1. **The 225/337 unmatched saccades are their own question**, and the first
   one on this list in a while that is not about glissades. One eye detects
   a saccade, the other detects nothing there. Whether that is a threshold
   asymmetry between the eyes, genuinely monocular events, or one eye's
   signal quality is unmeasured — and it is what a saccade-only consumer of
   this pipeline would want to know.
2. **Whether ~5% is acceptable** is a judgement about the downstream use of
   the conjunction, not a measurement. It belongs in the spec.
3. **Move `_KIND_OF` to `eye/detect/labels.py`.** Unchanged.
4. Four detectors still unwritten: NSLR, REMoDNaV (the detector, not the
   PyPI oracle), Bayesian microsaccade, U'n'Eye.

The compute machine exists but is not assembled, so Phase 2b stays blocked.
