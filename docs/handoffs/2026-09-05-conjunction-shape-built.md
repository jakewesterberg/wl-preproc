# Four detectors were blocked by a bug, not a decision

**Built 2026-09-05** on `spec/conjunction-shape`, ten commits,
`c0838b8..d396d85` (`docs/superpowers/plans/2026-09-05-conjunction-shape.md`,
six tasks). Design spec `docs/superpowers/specs/2026-09-05-conjunction-shape-design.md`,
amending §4 and §5.1 of `2026-08-31-saccade-detection-design.md`. This is
**Task 6 of that plan** — documentation only, no production code and no
tests in this commit.

**It withdraws the question** `docs/handoffs/2026-09-02-the-conjunction-label-decision.md`
briefed rather than answering — that file now carries a SUPERSEDED banner at
its own top, and this document is what replaces it.

Suite **1301 passed, 5 skipped, 1 deselected, 1 xfailed**, zero warnings, run
as `.venv/bin/python -m pytest` from the repo root on Python 3.11 (`main` was
1284 before this branch). `wl-check`: no findings. **This branch has not been
pushed to origin — `git ls-remote --heads origin` has no
`spec/conjunction-shape` — so CI on 3.11 or 3.13 has not run for any of it.**
Do not read a green local run as evidence about 3.13: the eye merge left CI
red on 3.13 alone for a day while every local run on this machine was green
(`docs/CHECKPOINT.md`'s own warning), and nothing here has been checked
against that fact yet. Pushing and reading `gh run list --branch
spec/conjunction-shape` is on the plan's own "before opening a PR" checklist,
not done.

---

## What changed, and why the decision was withdrawn

`docs/handoffs/2026-09-02-the-conjunction-label-decision.md` asked what label
a `conjunction` run should carry once a detector can emit `pso`, listed four
candidate conventions, and recorded that four detectors — Nyström–Holmqvist,
NSLR, REMoDNaV, Bayesian microsaccade detection (BMD) — were blocked until
one was chosen.

**None of the four conventions was adopted. The question dissolved.** Its
premise was that the conjunction is *derived*, so no detector supplies its
label — true — and that this means the label must therefore be assigned by
some external rule — which does not follow. When both eyes independently
call the same stretch the same **kind**, their agreement on kind supplies
the label with no assignment at all. A binocular glissade is stored as
`pso`, exactly as a binocular saccade is stored as `saccade`.

**What actually blocked the four detectors was a defect in
`_overlapping`, not an open question.** Read directly from
`wl_preproc/schema/detect.py`: `_overlapping` intersects every run in `left`
against every run in `right` on time alone,

```python
for left_run in left:
    for right_run in right:
        start = max(left_run.start, right_run.start)
        stop = min(left_run.stop, right_run.stop)
        if stop - start >= floor:
            intersections.append((start, stop))
```

and never reads either run's label. That is exactly right while every
emitted label is the same kind of thing — true of Engbert–Kliegl and
Otero-Millan, the only two ever registered, both `{saccade, microsaccade}`
— and silently wrong the moment a detector emits `fixation` alongside a
non-saccadic label, which all four blocked detectors do (spec §3.1). A left
`fixation` crossed with a right `saccade` would have intersected and
survived as a binocular event, because nothing in `_overlapping` ever looks
at what either side called it.

**No stage-1 or stage-2A test could have found this.** Finding it needs a
detector that emits more than one kind, and neither stage registers one —
confirmed against `wl_preproc/eye/detect/registry.py::DETECTORS`, which
today has exactly two entries, `engbert_kliegl` and `otero_millan`. The
subsystem's own completeness claim (set equality between `DETECTORS` and the
registered paramsets) is silent here too, because both registered detectors
are correct.

**The fix**: `_overlapping` keeps its exact signature and becomes the
single-kind primitive. A new `_conjunction_runs` groups both eyes' runs by
kind first (`_KIND_OF`: `saccade` and `microsaccade` share the kind
`"saccadic"`; every other emitted label — `pso`, `pursuit`, `drift` — is its
own kind, keyed by its own value; `fixation`, `blink` and `invalid` are
never intersected, listed in `_NOT_INTERSECTED`), then calls `_overlapping`
once per kind and concatenates. `EyeDetection.make()` now calls
`_conjunction_runs` where it used to call `_overlapping` directly.

`_conjunction_label`'s old guard — raise `UndecidedConjunctionLabel` when a
detector's whole vocabulary is not a subset of `{saccade, microsaccade}` —
existed because one label had to cover a mixed vocabulary. Per-kind
intersection means each kind labels itself, so that guard is now
unreachable and has been **replaced, not merely widened**: an
exhaustiveness guard on the kind map itself (`_kind_of`, raising
`UnknownLabelKind`) fires if a label reaches the conjunction with no entry
in `_KIND_OF` and none in `_NOT_INTERSECTED` — catching a ninth label added
without updating the map, which matters because the eight-label taxonomy's
migration window closes January 2027. The empty-vocabulary guard in
`_conjunction_label` is unchanged: it exists for a different reason
(`frozenset() <= anything` is `True`, so an empty-vocabulary detector would
pass any subset test while `registry.Detector.detect` refuses every label
it emits) and still holds.

## Byte-identical for every detector that shipped before this branch

Engbert–Kliegl, Otero-Millan and U'n'Eye each emit exactly one kind, so
grouping by kind partitions their runs into one group and the loop inside
that group is today's loop — nothing about their stored rows changes.

This is asserted directly, not just argued: `EyeDetection.Run` for
Engbert-Kliegl's conjunction trace against the suite's stepped-session
fixture is pinned to the exact tuples

```
(6800, 6816, "saccade")
(7250, 7258, "microsaccade")
(7749, 7767, "saccade")
```

(`tests/schema/test_detect_populate.py`, around line 1101) — a boundary
shift of a few samples or a swapped verdict would fail this, where a bare
count would not. The comment on that assertion records how it was pinned:
captured at `6bc3704`, immediately before `make()` was wired to
`_conjunction_runs`, then confirmed identical at this task's own commit.

## Five of the seven specified detectors never reach `classify`

Design spec §3.1's table, verified directly against
`wl_preproc/eye/detect/registry.py` and the docstrings in
`wl_preproc/schema/detect.py`:

| Detector | Emits | Conjunction kinds | Saccadic label |
|---|---|---|---|
| Engbert–Kliegl | saccade / microsaccade | saccadic | `classify` |
| Otero-Millan | saccade / microsaccade | saccadic | `classify` |
| U'n'Eye | saccade | saccadic | degenerate |
| Nyström–Holmqvist | saccade / pso / fixation | saccadic, pso | degenerate |
| NSLR | saccade / pso / pursuit / fixation | saccadic, pso, pursuit | degenerate |
| REMoDNaV | saccade / pso / pursuit / fixation | saccadic, pso, pursuit | degenerate |
| Bayesian microsaccade | microsaccade / drift | saccadic, drift | degenerate |

**`registry.py::DETECTORS` registers only the first two today — Engbert–Kliegl
and Otero-Millan. The other five, U'n'Eye included, are specified in the
design spec but not written.** Once they are, five of the seven will take
the **degenerate saccadic-slice branch** in `_conjunction_label`: a detector
whose vocabulary's saccadic slice (`detector.vocabulary &
{SACCADE, MICROSACCADE}`) has size one gets that one label constantly, never
`classify`'s other answer, because `registry.Detector.detect` refuses the
other answer from the detector itself. Only Engbert-Kliegl and Otero-Millan
declare both sides of the amplitude cut and actually reach `classify`. Two
consequences worth stating rather than discovering later: `classify`
governs two detectors' conjunction rows, not seven; and the
`microsaccade_max_deg` `KeyError` guard in `_conjunction_label` is by design
not reached for a degenerate split, so five of seven `eye_detection`
paramsets are never asked for a threshold that could not have changed their
answer.

---

## Two things for whoever writes the next detector

### 1. A production defect this branch found and deliberately did not fix

`wl_preproc/schema/detect.py::register_default_paramsets` (read directly,
not from memory):

```python
def register_default_paramsets() -> dict[str, int]:
    ...
    paramset.register("eye_validity", asdict(DEFAULT_VALIDITY_PARAMS))
    defaults = {
        "engbert_kliegl": asdict(DEFAULT_EK_PARAMS),
        "otero_millan": asdict(DEFAULT_OM_PARAMS),
    }
    return {
        name: paramset.register(
            "eye_detection",
            {"detector": name, **defaults[name], "microsaccade_max_deg": MICROSACCADE_MAX_DEG},
        )
        for name in DETECTORS
    }
```

`defaults` is a hardcoded two-entry dict, and the dict comprehension indexes
it as `defaults[name]` for every `name in DETECTORS`. **Add a third entry to
`registry.DETECTORS` without also adding its key to `defaults`, and this
raises `KeyError` on the first name that is missing.**

That is not a contained failure. `wl_preproc/daemon.py::register_default_paramsets`
(the daemon-level function of the same name) calls `detect.register_default_paramsets()`
inside its own dict comprehension over `_PARAMSET_MODULES`, and
`daemon.run_once()` calls that daemon-level function directly —

```python
activate_all(prefix=prefix)
register_default_paramsets()          # daemon.py line 911, no try/except here

reaped = reap_stale_jobs(prefix=prefix)
populated, errors = 0, []
...
for table in _computed_tables():
    try:
        result = table.populate(reserve_jobs=True, suppress_errors=True)
        ...
```

— **unguarded**, before `reap_stale_jobs`, before the event stage, before
the `_computed_tables()` loop that wraps each table's own `populate()` in a
`try`/`suppress_errors=True`. A `KeyError` here propagates straight out of
`run_once()` and aborts the entire daemon pass, not merely the registration
of the new detector: no stage populates, no job is reaped, nothing in that
invocation runs.

**Stage 2B is exactly where this fires.** The moment `registry.DETECTORS`
gains a third entry — Nyström–Holmqvist is the obvious first — without a
matching key added to `register_default_paramsets`' `defaults` dict, every
`daemon.run_once()` call breaks, for every session, until someone notices.
This is deliberately **not fixed on this branch**: it is pre-existing,
outside this spec's scope (nothing here touches `register_default_paramsets`
or `DETECTORS`), and it lives in the exact seam where stage 1's worst defect
hid — production code nobody's test exercised through the real entry point.
Whoever adds the next detector must extend `defaults` in the same commit
that extends `DETECTORS`, or add a test that would catch the omission
first.

### 2. The open question that matters: how often do the eyes disagree on kind?

Per-kind intersection means a conjunction run exists only where **both eyes
agree on kind**. A left `saccade` overlapping a right `pso` now produces no
conjunction run of either kind — dropped, not folded into either label
(design spec §5, test 4). That is the correct behaviour under §1's rule, but
**how often it happens is unmeasured, and it is unmeasurable with what is
registered today**: no registered detector emits anything but saccadic
events (Engbert–Kliegl and Otero-Millan, both `{saccade, microsaccade}`), so
there is no session anywhere in this pipeline where one eye could even call
something `pso` for the other to disagree with.

**And the conjunction trace cannot answer this later, either, once a
pso-capable detector is written — read the storage path before reaching for
it.** `EyeDetection._insert_trace` paints `fixation` over every sample no
surviving conjunction interval claims, and a kind disagreement produces no
interval to claim it: `_conjunction_runs` groups runs by kind, so a left
`saccade` run and a right `pso` run over the same samples land in different
kind-groups and neither intersects the other's. The stretch is therefore
stored as `fixation` in the conjunction trace — identical, byte for byte, to
a stretch where both eyes genuinely agreed there was nothing there. A query
against the conjunction trace's own `pso` (or `saccade`) fraction would
report this rate as exactly zero, not as unmeasured, and would look like a
real finding rather than an artifact of storage. The rate can only be
recovered by comparing the two PER-EYE traces directly — which is why the
paragraph below says "take its per-eye `pso` and `saccade` runs" rather than
"query the conjunction trace": that phrasing is deliberate, not incidental,
and whoever implements the measurement should not shortcut it.

This is the **largest piece of unquantified reasoning in the design spec**
(its own §6, open question 1): the agreement requirement is argued from
first principles — two eyes calling the same stretch different kinds is a
labelling disagreement, not two real events — but nothing says whether
dropping those spans is conservative (rare disagreement, few events lost) or
costly (frequent disagreement, a meaningful fraction of real binocular
events discarded). Every other number in this spec's testing section is
measured against the reference recording or a fixture; this one cannot be,
because measuring it needs a detector that can emit `pso` on real data, and
none is registered yet.

**Nyström–Holmqvist — the simplest of the four now-unblocked detectors — is
what measures it, and that measurement should be the first thing stage 2B
does once it is written**, before NSLR, REMoDNaV or BMD: run it over the
reference recording, take its per-eye `pso` and `saccade` runs, and count
how many temporally-overlapping pairs disagree on kind versus agree. That
number is what turns open question 1 from reasoning into evidence, one way
or the other.

---

## What is unaffected, and why this document does not re-litigate it

- **`pso_as` remains a comparison-only parameter** in `DetectorAgreement`'s
  primary key (§6.1, unchanged). The conjunction now stores `pso` natively,
  so every trace reaching the comparison layer carries raw labels and the
  convention is applied exactly once, at scoring time — not a second time
  at storage.
- **No paramset, column, table or dependency changed.** `third_party`,
  `runs_on` and `builds_on` in `wl.yaml` are untouched; only `status.phase`,
  `status.next` and `status.describes` moved, per `CLAUDE.md`'s own table of
  what an edit like this touches.
- **Otero-Millan's rows are still PROVISIONAL**
  (`docs/handoffs/2026-09-01-consensus-and-otero-millan-built.md`) — nothing
  here bears on that; a calibrated session remains the highest-value unblock
  for that separate question.
- **U'n'Eye's obstacles are unchanged.** It declares `{saccade}`, a subset
  of the amplitude-derived vocabulary, so it never depended on the glissade
  question in either direction. `torch`, vendoring and a GPU are still what
  stand between it and being written.

## What is still true from the superseded handoff

`docs/handoffs/2026-09-02-the-conjunction-label-decision.md` is marked
SUPERSEDED at its own top rather than deleted, because three things in it
are still accurate and still worth reading: the account of what a glissade
is (Deubel & Bridgeman's 0.5° against the 1.0° default microsaccade
threshold), Nyström & Holmqvist's finding that current algorithms assign
glissades to saccades or fixations "largely arbitrarily," and the
transaction behaviour that made — and still makes — a genuinely blocked
detector write no rows at all: `EyeDetection.make()` inserts `left` and
`right` before reaching the conjunction branch, but DataJoint's
`AutoPopulate._populate1` wraps the whole call in a transaction and cancels
it on any exception, so a `make()` that raises anywhere loses every insert
made before the raise, not just the part that failed.
