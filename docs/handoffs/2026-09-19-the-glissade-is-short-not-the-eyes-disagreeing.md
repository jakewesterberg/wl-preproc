# The mechanism holds for three quarters of the events, and not for the reason predicted

Branch `measure/eye-saccade-offset-difference`, forked from `main` at
`668637a`.

> **MERGED 2026-09-19 as `eb5a979`, pushed, and CI GREEN on both
> interpreters.** Read off the run: `gh run view 35444119197` reports
> `test (3.11): success` and `test (3.13): success`, with Manifest green on
> the same push (`35444119159`).
>
> *This document opened with "Not merged, not pushed, CI has not run" and
> the observation that the sentence goes stale the moment the branch lands.
> Seventh instance, third one in a row where the paragraph naming the lesson
> was itself the one that went stale.*
>
> Exact CI counts are not quoted — the API truncates a log this size, so the
> summary line is not readable off the run. The job status is, and the count
> evidence is the pre-merge 3.13 run in §3.

This measures the mechanism hypothesis that
`handoffs/2026-09-19-which-kinds-disagree.md` and conjunction-shape spec §6
both state and deliberately do not assert. That hypothesis:

> A glissade is short and sits immediately after a saccade; a saccade is
> long. If the two eyes place a saccade's OFFSET differently by about a
> glissade's duration, one eye is already in its `pso` while the other is
> still inside its `saccade`. **What would settle it** is the distribution
> of (other eye's saccade offset − own eye's saccade offset) over these 711
> and 835 events; if the mechanism is right it is centred near one glissade
> duration and not near zero.

It is measured. The shape of the answer is right, the magnitude is wrong,
and the wrong magnitude is the part that changes what to do next.

---

## 1. The measurement

| direction | population | n | share | median | mean | sd |
|---|---|---|---|---|---|---|
| left→right | all disagreeing glissades | 711 | — | 10.03 ms | 23.01 ms | 48.28 |
| left→right | counterpart ends INSIDE the glissade | 497 | **0.699** | 8.02 ms | 8.35 ms | 6.64 |
| left→right | counterpart ends BEYOND it | 214 | 0.301 | 24.07 ms | 57.07 ms | 77.36 |
| left→right | *control*: agreed saccades, other ends later | 1,827 | — | **6.02 ms** | 13.45 ms | 34.17 |
| left→right | *reference*: this eye's glissade durations | 2,017 | — | 14.04 ms | 18.42 ms | 10.80 |
| right→left | all disagreeing glissades | 835 | — | 10.03 ms | 30.08 ms | 77.58 |
| right→left | counterpart ends INSIDE the glissade | 632 | **0.757** | 8.02 ms | 8.66 ms | 7.26 |
| right→left | counterpart ends BEYOND it | 203 | 0.243 | 30.09 ms | 96.76 ms | 136.80 |
| right→left | *control*: agreed saccades, other ends later | 2,063 | — | **6.02 ms** | 19.86 ms | 59.02 |
| right→left | *reference*: this eye's glissade durations | 2,167 | — | 16.05 ms | 19.47 ms | 10.45 |

`unpaired == 0` in both directions: every one of the 711 and 835 glissades
had an own saccadic run ending exactly at its start, so `_glissade_bounds`'
construction (`return saccade_offset, stop`) held on every measured event
and the statistic read the boundary it claims to. That is checked per event
rather than assumed, and asserted.

---

## 2. What it says, in the order that matters

**The displacement is about 1.7× ordinary boundary jitter, not one glissade
duration — and that is enough, because the glissade is short.** Over
disagreeing glissades the other eye's saccade ends a median 10.03 ms later.
On saccades the two eyes AGREE about, when the other eye ends later, it
ends 6.02 ms later. That is the like-for-like control, and it is a modestly
larger displacement rather than a different regime. What destroys the
agreement is the glissade's own length: at a median 14.04/16.05 ms, an
ordinary 6–10 ms offset displacement lands *inside* it, and that is all it
takes.

**This is the correction that matters.** The previous handoff said a better
glissade offset criterion should shrink the asymmetry, reasoning from the
eyes disagreeing unusually about glissade boundaries. **They do not.** They
disagree about saccade offsets roughly as much as they always do. No
detector change removes this: it is a property of demanding sample-level
binocular kind agreement on an event a few samples long. The lever is §1's
rule — for instance admitting a `pso` whose counterpart `saccadic` run ends
within it — and that is a design question, not an implementation defect.

**A quarter to a third of the disagreements are not boundary placement at
all.** Where the counterpart ends BEYOND the glissade (0.301 / 0.243), the
other eye's saccade runs a median 24–30 ms and a mean 57–97 ms past this
eye's — a longer or merged saccade covering the whole event, not the same
saccade's offset placed later. A different defect with a different fix, and
it is what pulls the whole population's mean to two and three times its
median. The two were split precisely because their mean together describes
neither.

**The sign is forced and is not evidence.** A counterpart only reaches the
disagreement bucket by overlapping the glissade, which requires the other
eye's saccade to end after this one's. Every difference is at least one
sample whatever the eyes did. "The other eye ends later, every single time"
reads like a finding and is a tautology; only the magnitude, against the
control, carries anything.

**A caveat on the INSIDE row, which is the one worth quoting carefully.**
That population is *defined* by the counterpart ending at or before the
glissade's end, so its values are bounded above by the glissade duration by
construction. Its 8.02 ms median is truncated and cannot be read as "the
displacement is 8 ms". The untruncated comparison is 10.03 ms against the
control's 6.02 ms.

---

## 3. How it was verified

**Written test-first.** Twelve new tests, each watched failing before the
code existed — `AttributeError: '_Agreement' object has no attribute
'agreements'`, then `NameError: name '_glissade_offset_differences' is not
defined`, then a `TypeError` when `within` changed from a tally to a mask.

**Mutation battery: eleven mutations, by literal source mutation and revert.
Nine caught immediately; the two survivors were real gaps and are now
closed.** Both were reachable — not with Nyström–Holmqvist, which emits
neither `pursuit` nor a second saccadic label, but with the next detector
that does:

| mutation | caught by |
|---|---|
| offset difference sign flipped | 3 tests |
| adjacency looks up the glissade END not its start | 3 tests |
| unpaired glissade silently skipped | `..._is_counted_unpaired` |
| `within` boundary `<=` relaxed to `<` | `..._exactly_at_the_glissade_end_...` |
| `pso`-only filter dropped | `test_only_a_glissade_over_...` |
| **counterpart-must-be-`saccadic` filter dropped** | **SURVIVED → new test** |
| baseline sign flipped | `test_the_baseline_is_the_same_difference_...` |
| **baseline kind filter dropped** | **SURVIVED → new test** |
| agree bucket keeps FIRST counterpart, not largest | `test_each_bucket_records_...` |
| disagree bucket keeps FIRST counterpart, not largest | `..._attributed_to_the_larger_overlap` |
| `pairs` derived from `agreements` not `disagreements` | 3 tests |

After the two new tests, **all eleven are caught and none survive.**

**`_kind_agreement` stopped breaking early on a same-kind match**, because
the matched run is now READ rather than only counted, and an own-run
straddling two same-kind counterparts would otherwise record a clipped edge.
Counts are untouched by that change — verified not by argument but by
re-running the kind-disagreement measurement against the real recording and
getting **byte-identical numbers** to the merged `1dc1676`/`b996447` run.

**`pairs` is now DERIVED from the recorded disagreements** rather than
tallied beside them, so the partition it rests on is structural instead of
an invariant two accumulators have to maintain.

**3.13**: `tests/eye tests/contracts --noconftest` in a venv with no
DataJoint — **372 passed, 9 skipped, 1 xfailed** — and the gated measurement
re-run there returns byte-identical numbers to 3.11.

**Suite counts**, `__pycache__` cleared, `PYTHONDONTWRITEBYTECODE=1`:

| interpreter | dependencies | result |
|---|---|---|
| 3.11 | development venv | **1366 passed**, 10 skipped, 1 deselected, 1 xfailed |
| 3.13 | fresh CI resolution | **1365 passed**, 12 skipped, 1 xfailed |

Both are the prior counts plus this branch's twelve new tests and its one
new gated test, which skips without the recording.

**The dependency set was re-resolved before merging**, per the rule
2026-09-13 left behind and the practice `668637a` recorded. `uv pip compile
pyproject.toml --extra dev --python-version 3.13` produced a resolution
**identical to this morning's** — nothing moved upstream between the two
merges — and the full suite was run against it on 3.13 rather than against
the venv.

---

## 4. What is next

1. **§1's rule is now a design question, not a measurement gap.** Better
   than a third of glissades are dropped; the cause is understood; and the
   measurement says no detector change fixes it. Whether to admit a `pso`
   whose counterpart `saccadic` run ends within it — or to accept the cost —
   is the decision this leaves on the table, and it belongs in the spec
   rather than in a test file.
2. **The BEYOND population, separately.** A quarter to a third of these are
   the other eye carrying a much longer saccade over the whole event. That
   is a detector question and it is untouched: whether those are merged
   saccades in one eye, or missed glissades in the other.
3. **Move `_KIND_OF` to `eye/detect/labels.py`.** Unchanged, and now with a
   fourth consumer of the duplicated copy in `tests/eye/`.
4. Four detectors still unwritten: NSLR, REMoDNaV (the detector, not the
   PyPI oracle), Bayesian microsaccade, U'n'Eye. Gap-aware segmentation and
   rehydration remain the hardware-free pieces outside detection.

The compute machine exists but is not assembled, so Phase 2b stays blocked.
