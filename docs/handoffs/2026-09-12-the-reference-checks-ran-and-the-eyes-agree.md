# The reference-gated checks ran, and the two eyes agree on kind

Branch `measure/eye-kind-disagreement`, forked from `main` at `5969c60`.

> **MERGED 2026-09-13 as `1dc1676`, and CI green on both interpreters at
> `ea08678`** — `gh run view 34761368613`, `test (3.11): success`,
> `test (3.13): success`, Manifest green on the same push.
>
> *This document opened with "Not merged, not pushed, CI has not run — and by
> this project's own recurring lesson, assume that sentence is false the
> moment the branch lands." It was false within the day, which is the fifth
> instance of that lesson on this project and the first time the sentence
> warning about it was in the same paragraph.*
>
> **The merge turned `main` red, and not because of this work.** `1dc1676`
> failed CI on both interpreters in `tests/schema/test_harness.py::test_a_
> bare_longblob_corrupts_silently` — DataJoint 2.3.3, released between the
> last green run and this one, made bare `longblob` refuse an ndarray instead
> of silently corrupting it. That test existed to catch exactly that and its
> docstring asked for the rule to be revisited deliberately; `ea08678` is the
> revisit. 1,344 of 1,345 tests passed on the red run, every one of this
> branch's own among them.

Two things happened, and the first is not code at all.

---

## 1. The three reference-gated checks had never run. They have now.

`WLPP_OHDPI_REFERENCE` was unset in every environment this subsystem was
built in, so three checks written across two branches had never executed
once. The recording was on this machine the whole time, at
`~/Downloads/Tutorial/OpenIris-2024Jul31-114628/`. Nothing had to be built to
run them.

| check | measured | the paper |
|---|---|---|
| glissade rate | 0.399 left / 0.416 right | 0.478 reading, 0.591 scene |
| glissade duration | 18.4 ± 10.8 ms / 19.5 ± 10.5 ms | 22.2 ± 9.8 ms reading |
| REMoDNaV saccade count | 571 vs 574 (L), 570 vs 565 (R) | oracle, 2× tolerance |

**Design spec §9 item 1 is answered, and the answer is the good one.** That
item asked whether the shared five-point velocity differentiator — used
deliberately in place of the paper's Savitzky-Golay, so that detector
disagreement means algorithmic rather than filter disagreement — smooths
~20 ms glissades away. It does not. 2,017 left and 2,167 right glissades on
5,062 / 5,213 saccades.

**One observation, offered as an observation and not a defect.** Both the
rate and the duration land below the paper in the *same* direction. A single
mechanism would explain that: mild truncation of every glissade by the
smoother, which lowers the mean duration and pushes the shortest below the
minimum-duration criterion entirely, lowering the rate too. Both sit inside
the paper's own sd, and this tutorial recording's task is neither reading nor
scene perception, so nothing here demands action. It is worth knowing before
anyone reads the 0.399 as agreement with 0.478.

**REMoDNaV's integration is verified end to end for the first time.** Its API
had been read off a downloaded wheel and never executed, because — per
`CHECKPOINT` and `wl.yaml` both — "this `.venv` has no `pip`, so `remodnav`
cannot be installed into it". That inference is wrong:
`uv pip install --python .venv/bin/python 'remodnav>=1.1'` installs into a
venv with no `pip`, and `uv` is on this machine. Five packages added
(`remodnav`, `statsmodels`, `formulaic`, `patsy`, `interface-meta`), nothing
upgraded or downgraded. The oracle then agreed within 1% on a check that
tolerates a factor of two.

---

## 2. The two eyes agree on kind far better than chance

Conjunction-shape design spec §6 open question 1, called there "the largest
piece of unquantified reasoning in this spec", and unmeasurable until a
pso-capable detector existed. Nyström–Holmqvist is that detector.

| direction | agree | disagree | alone | kind disagreement | drop rate |
|---|---|---|---|---|---|
| left→right | 5,763 | 721 | 595 | **0.1112** of 6,484 | **0.1859** of 7,079 |
| right→left | 5,792 | 855 | 733 | **0.1286** of 6,647 | **0.2152** of 7,380 |

**The null ran before the check, and the check survived it.** Randomly placed
runs preserving each eye's durations *and* kinds disagree 0.376–0.470 across
seeds 0–19. The real eyes disagree ~0.12 — roughly three and a half times
better than chance — so the statistic is measuring binocularity rather than
the vocabulary's own kind mix. Retained, not withdrawn: the opposite outcome
from the Otero-Millan round, and for the same reason that round was
withdrawn.

**§1's agreement requirement costs about a fifth**, 18.6% and 21.5% of each
eye's detected events. That is the answer to the spec's own "conservative or
costly" framing, as a number rather than an argument.

**What was not expected.** In both directions there are MORE stretches where
both eyes detected an event and named it differently (721, 855) than
stretches where one eye found nothing at all (595, 733). The eyes nearly
always agree that *something* happened; the fifth that gets dropped is mostly
them disagreeing about *what*.

---

## Findings worth carrying forward

**The conjunction's duration floor is per-detector, and nobody had written
that down.** `schema/detect.py::_min_duration_samples` reads
`min_duration_samples` off the detector's own params with `getattr(..., 1)`.
That field belongs to `EngbertKlieglParams`; `NystromHolmqvistParams` states
its durations in milliseconds and has none. So **Nyström–Holmqvist's
conjunction admits a one-sample binocular event where Engbert–Kliegl's
requires six**, and the "6-sample floor" the run-count measurement reports is
Engbert–Kliegl's alone, not a property of the conjunction. Every future
detector inherits whichever it happens to declare, silently.

**Two tests that looked adequate survived mutation.** After the statistic was
written and covered, mutating `alone += 1` to `disagree += 1` and deleting
the duration-floor comparison both left the whole file green. The cause is
worth knowing generally: the fixation test's `other` side filters to empty,
so it returns through the early-return path and never executes the main
loop's own `alone` branch. Coverage of a *bucket* is not coverage of the
*path that fills it*. Two tests were added and all four mutations now fail a
named test, each verified by literal source mutation and revert.

**`_KIND_OF` now exists in two places, and nothing can catch drift.**
`tests/eye/` imports nothing from `wl_preproc.schema`, because the 3.13
cross-check runs it with `--noconftest` in a venv with no DataJoint. So the
conjunction's kind mapping is restated in the test file. A ninth label, or a
remapped kind, would change the conjunction and leave this measurement
quietly reporting the old rule's number. **The fix is to move `_KIND_OF` into
`eye/detect/labels.py`** — it is vocabulary knowledge, not schema knowledge —
where both sides can import one definition. Deliberately not done here: it is
a production change to the conjunction, outside this measurement's scope.

---

## What is still NOT measured

- **Which kind pairs disagree.** Almost certainly `saccade` against `pso`,
  the two eyes placing a saccade's trailing glissade boundary differently,
  but that is a guess. It is the obvious next question and it is cheap.
- **The conjunction row count for a multi-kind detector** — spec §6 open
  question 2's actual subject. The per-eye counts are measured (left 12,092:
  5,013 fixation / 2,017 pso / 5,062 saccade; right 12,446: 5,066 / 2,167 /
  5,213) and are close to Engbert–Kliegl's totals with a different
  composition. The conjunction half needs the schema and so belongs beside
  `test_the_run_count_measured_against_the_reference_recording` in
  `tests/schema/`, not in the file this work touched.
- **Spec §9 item 3** — whether the paper's 47.8% is the union of both
  glissade criteria. Untouched by any of this.
- **Everything on 3.13 in CI.** The hand-run cross-check is green and returns
  byte-identical numbers, but CI has not seen this branch.

## What is next

Unchanged by this work, except that the item that stood at the top of it is
now done: NSLR, REMoDNaV (the detector, distinct from the PyPI oracle) and
Bayesian microsaccade detection remain unwritten; U'n'Eye still wants PyTorch
and a GPU. Gap-aware segmentation and rehydration remain the hardware-free
pieces outside detection. The compute machine exists but is not assembled, so
Phase 2b is still blocked.
