# Detection Stage 2A: Otero-Millan and the Consensus Suite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second detector and make detector agreement measurable — the
thing the whole detection design exists for, and which one detector cannot do.

**Architecture:** A pure-function consensus library (`eye/detect/consensus.py`)
tested against constructed label traces, a reimplemented Otero-Millan detector
(`eye/detect/otero_millan.py`) joining the existing registry, a
`DetectorAgreement` table computing pairwise scores, and an agreement
subsection in the daily report. Each lands independently testable.

**Tech Stack:** Python 3.11 (CI also runs 3.13), numpy, DataJoint 2.3.2,
pytest, testcontainers + MySQL 8.0 for schema tests.

**Spec:** `docs/superpowers/specs/2026-08-31-saccade-detection-design.md` —
§3.1, §3.2, §5.1, §6, §6.1, §9, all as amended 2026-09-01 (commit `3e857e1`).
Read §6.1 in full before Task 1; it is the argument this plan implements.

## Global Constraints

- Python floor **>=3.11**; CI runs **3.11 and 3.13**. A green local run is
  evidence about 3.11 alone. Run `pytest --noconftest tests/eye tests/contracts`
  under a 3.13 interpreter before the final commit.
- **No new declared dependency.** `scipy` and `scikit-learn` are installed but
  arrive TRANSITIVELY via `kilosort` / `spikeinterface` / `networkx`, which are
  `where: serv`. Neither is in `pyproject.toml`'s dependency list nor
  `wl.yaml`'s `third_party`. The eye path needs no container and no GPU and
  must keep it that way — see Task 3's own note.
- Comments explain **why**, and cite **by symbol name, never line number**.
- Conventional-commit subjects, lowercase after the colon.
- `wl-check` clean before every commit.
- A detector's `vocabulary` is DECLARED and enforced at
  `registry.Detector.detect`. Never widen it to make a test pass.
- `blink` and `invalid` are never in any detector's vocabulary: they come from
  the validity mask, and two sources must not write one fact.

## Where this plan gives complete code, and where it deliberately does not

Tasks 1-3 carry every literal: the lattice, both metrics, the detector's
parameters and its full test bodies. Those are novel algorithms with exact
values an implementer cannot derive from the codebase.

**Tasks 4-6 name the pattern to follow instead of transcribing fresh code, and
that is a considered departure from this project's usual plan style.** The
reason is measured, not stylistic. Stage 1's plan specified complete code for
all nine tasks; a whole-branch review then found **ten defects, three of them
serious, every one of them in code the plan had written and never executed** —
a `key_source` that was permanently empty, a refusal gate that discarded a
usable eye, a conjunction that manufactured zero-amplitude events. The tasks
that went best were the ones where the implementer worked from a stated
requirement against an existing pattern in the repository.

So where a task has a working precedent in the tree — a gated real-file test
(`tests/schema/test_detect_populate.py`), a `dj.Computed` table with a
`key_source` and refusal handling (`wl_preproc/schema/detect.py`), a report
subsection (`_detection_rows` / `_unusable_per_eye` in
`wl_preproc/cli/report.py`) — this plan points at that precedent and states
what must be true of the result. The precedent has run; my transcription of it
would not have.

What those tasks do carry in full is the part a pattern cannot supply: the
exact table key, the invariants, the mutations that must fail, and the traps
already paid for once.

---

### Task 1: The coarsening lattice and the comparability rule

**Files:**
- Create: `wl_preproc/eye/detect/consensus.py`
- Test: `tests/eye/detect/test_consensus.py`

**Interfaces:**
- Consumes: `labels.Label` (StrEnum, 8 members).
- Produces: `COARSENING: dict[Label, frozenset[Label]]`;
  `PSO_AS_SACCADE: str`, `PSO_AS_FIXATION: str`;
  `coarsen(label: Label, target: frozenset[Label], pso_as: str) -> Label | None`;
  `comparable(a: Label, b_vocabulary: frozenset[Label], pso_as: str) -> bool`;
  `comparison_mask(a_labels: np.ndarray, b_labels: np.ndarray, a_vocabulary: frozenset[Label], b_vocabulary: frozenset[Label], pso_as: str) -> np.ndarray`;
  `shared_vocabulary(a: frozenset[Label], b: frozenset[Label], pso_as: str) -> frozenset[Label]`.

- [ ] **Step 1: Write the failing tests**

```python
import numpy as np
import pytest

from wl_preproc.eye.detect.consensus import (
    PSO_AS_FIXATION,
    PSO_AS_SACCADE,
    coarsen,
    comparable,
    comparison_mask,
    shared_vocabulary,
)
from wl_preproc.eye.detect.labels import Label

_EK = frozenset({Label.SACCADE, Label.MICROSACCADE})
_UNEYE = frozenset({Label.SACCADE})
_BMD = frozenset({Label.MICROSACCADE, Label.DRIFT})
_NSLR = frozenset({Label.SACCADE, Label.PSO, Label.PURSUIT, Label.FIXATION})


def test_a_label_already_in_the_target_vocabulary_is_unchanged():
    """Coarsening is only ever applied where it is needed. A label the other
    side already speaks is passed through, not rewritten -- rewriting it would
    lose the distinction the other side CAN express."""
    assert coarsen(Label.SACCADE, _UNEYE, PSO_AS_SACCADE) is Label.SACCADE


def test_microsaccade_coarsens_to_saccade_when_the_other_side_cannot_split():
    """Design spec section 6.1's `microsaccade -> saccade` edge. U'n'Eye calls
    a microsaccade a saccade because it does not split, so the pair is
    comparable once Engbert-Kliegl's finer label is coarsened into its."""
    assert coarsen(Label.MICROSACCADE, _UNEYE, PSO_AS_SACCADE) is Label.SACCADE


def test_a_saccade_cannot_reach_a_microsaccade_only_vocabulary():
    """**The rule this module exists for.** The lattice's edges run
    `microsaccade -> saccade`, never the reverse, so a stored `saccade` has no
    path into BMD's `{microsaccade, drift}`. BMD has no word for a large
    saccade -- it never looks for one -- and scoring that as disagreement is
    the failure design spec section 6.1 opens by naming."""
    assert coarsen(Label.SACCADE, _BMD, PSO_AS_SACCADE) is None
    assert not comparable(Label.SACCADE, _BMD, PSO_AS_SACCADE)


def test_fixation_is_implicitly_in_every_vocabulary():
    """A sample is `fixation` when no detector claimed it, so it is never what
    makes a pair incomparable. Design spec section 6.1 states this because a
    literal reading of `vocabulary` -- which never contains `fixation`, since
    detectors declare only what they EMIT -- would exclude every non-event
    sample and leave nothing to score."""
    assert coarsen(Label.FIXATION, _UNEYE, PSO_AS_SACCADE) is Label.FIXATION
    assert comparable(Label.FIXATION, _BMD, PSO_AS_SACCADE)


def test_drift_and_pursuit_coarsen_to_fixation():
    """The lattice's other two fixed edges: slow motion is still not an event."""
    assert coarsen(Label.DRIFT, _EK, PSO_AS_SACCADE) is Label.FIXATION
    assert coarsen(Label.PURSUIT, _EK, PSO_AS_SACCADE) is Label.FIXATION


def test_pso_follows_the_stated_parameter_and_has_no_default():
    """Design spec section 2.5: the glissade assignment is "an explicit
    parameter, never a default". Nystrom & Holmqvist found glissades in about
    half of all saccades and concluded the assignment is "largely arbitrary"
    in current algorithms; making it a parameter is what turns the arbitrary
    choice into a stated one."""
    assert coarsen(Label.PSO, _EK, PSO_AS_SACCADE) is Label.SACCADE
    assert coarsen(Label.PSO, _EK, PSO_AS_FIXATION) is Label.FIXATION


def test_pso_as_must_be_supplied_explicitly():
    """No default argument, so a caller cannot omit the choice and get one
    silently. The signature is the enforcement."""
    with pytest.raises(TypeError):
        coarsen(Label.PSO, _EK)  # type: ignore[call-arg]


def test_equal_vocabularies_need_no_coarsening_at_all():
    """Engbert-Kliegl and Otero-Millan both declare `saccade / microsaccade`
    (design spec section 3.1, corrected 2026-09-01 by reading the reference
    implementation). This is stage 2A's own first pair, and it is the simplest
    case: nothing is coarsened, nothing is excluded, and any disagreement is
    about METHOD rather than about coverage or convention."""
    assert shared_vocabulary(_EK, _EK, PSO_AS_SACCADE) == _EK | {Label.FIXATION}
    for label in (Label.SACCADE, Label.MICROSACCADE, Label.FIXATION):
        assert coarsen(label, _EK, PSO_AS_SACCADE) is label


def test_the_shared_vocabulary_of_a_scope_mismatch_is_the_narrower_one():
    """EK vs BMD: `microsaccade` is shared, `drift` coarsens to `fixation`, and
    EK's `saccade` reaches neither. The row must record `{microsaccade,
    fixation}` so a reader never compares this score against a full-range one."""
    assert shared_vocabulary(_EK, _BMD, PSO_AS_SACCADE) == frozenset(
        {Label.MICROSACCADE, Label.FIXATION}
    )


def test_the_comparison_mask_excludes_what_either_side_cannot_claim():
    """The mask is what `n_samples_compared` counts. Design spec section 6.1
    already excludes `blink` and `invalid` because they come from the shared
    mask and are identical by construction; this extends the same treatment to
    samples the other detector is not responsible for."""
    a = np.array([Label.SACCADE, Label.MICROSACCADE, Label.FIXATION, Label.BLINK])
    b = np.array([Label.DRIFT, Label.MICROSACCADE, Label.FIXATION, Label.BLINK])

    mask = comparison_mask(a, b, _EK, _BMD, PSO_AS_SACCADE)

    # index 0: a's `saccade` cannot reach BMD's vocabulary -> excluded
    # index 1: shared `microsaccade` -> compared
    # index 2: `fixation` both ways -> compared
    # index 3: `blink` comes from the shared validity mask -> excluded
    assert mask.tolist() == [False, True, True, False]


def test_the_mask_is_symmetric():
    """Both metrics this suite ships are symmetric and the table's key is
    canonically ordered `a < b`, so a mask that depended on argument order
    would make one stored row mean two different things."""
    a = np.array([Label.SACCADE, Label.MICROSACCADE, Label.FIXATION])
    b = np.array([Label.DRIFT, Label.MICROSACCADE, Label.FIXATION])

    forward = comparison_mask(a, b, _EK, _BMD, PSO_AS_SACCADE)
    backward = comparison_mask(b, a, _BMD, _EK, PSO_AS_SACCADE)

    assert forward.tolist() == backward.tolist()


def test_pso_as_changes_which_samples_are_comparable():
    """Not only how a `pso` is scored, but whether it is scored at all: against
    a vocabulary containing `saccade` but not `fixation`-reachable classes the
    two settings differ. This is why `pso_as` is in the table's KEY, not a
    column -- two rows differing only in it are two different measurements."""
    a = np.array([Label.PSO])
    b = np.array([Label.SACCADE])

    as_saccade = comparison_mask(a, b, _NSLR, _EK, PSO_AS_SACCADE)
    as_fixation = comparison_mask(a, b, _NSLR, _EK, PSO_AS_FIXATION)

    assert as_saccade.tolist() == [True]
    assert as_fixation.tolist() == [True]
    # ...and the COARSENED value differs, which is what the metrics will see.
    assert coarsen(Label.PSO, _EK, PSO_AS_SACCADE) is not coarsen(
        Label.PSO, _EK, PSO_AS_FIXATION
    )
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_consensus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.eye.detect.consensus'`

- [ ] **Step 3: Implement**

Create `wl_preproc/eye/detect/consensus.py`:

```python
"""Comparing two detectors' label traces -- the coarsening lattice, the
comparability rule, and the metrics registry.

Design spec section 6.1. Pure functions over label arrays: nothing here reads
a table, so the whole rule set is testable against constructed traces with
known agreement rather than against whatever two real detectors happen to do.

**Two different things look alike and must not be conflated.** A detector can
be COARSER than another -- U'n'Eye calls a microsaccade a `saccade` because it
does not split -- or NARROWER IN SCOPE, having no word for a class of event
because it never looks for one. Coarsening handles the first. Applied to the
second it does the opposite of what is wanted: its edges run `microsaccade ->
saccade`, so coarsening a `microsaccade`-only detector WIDENS its apparent
claim to cover saccades it never sought, and every large saccade the other
detector found scores as disagreement.

The direction of the lattice's edges is what separates the cases, which is why
one graph does both jobs.
"""

from __future__ import annotations

import numpy as np

from wl_preproc.eye.detect.labels import Label

#: How `pso` is scored, stated per comparison and never defaulted (design spec
#: section 2.5). These are the two values `DetectorAgreement.pso_as` stores.
PSO_AS_SACCADE = "saccade"
PSO_AS_FIXATION = "fixation"

#: Coarsening edges. A label may be rewritten as any label reachable from it.
#: `pso`'s two edges are the stated parameter; every other edge is fixed.
#:
#: **Direction is load-bearing.** There is no `saccade -> microsaccade` edge,
#: because a detector that only emits `microsaccade` cannot express a large
#: saccade at all -- and inventing that edge is exactly how the scope mismatch
#: this module exists to catch would be silently scored as disagreement.
COARSENING: dict[Label, frozenset[Label]] = {
    Label.MICROSACCADE: frozenset({Label.SACCADE}),
    Label.DRIFT: frozenset({Label.FIXATION}),
    Label.PURSUIT: frozenset({Label.FIXATION}),
    Label.PSO: frozenset({Label.SACCADE, Label.FIXATION}),
}

#: Never in any detector's declared vocabulary, and never absent from an
#: effective one. A sample is `fixation` when no detector claimed it, so
#: excluding it would leave nothing to score; `blink` and `invalid` come from
#: the shared validity mask (design spec section 2) and are identical by
#: construction, so counting them would inflate every score toward agreement.
_ALWAYS_COMPARABLE = frozenset({Label.FIXATION})
_FROM_THE_MASK = frozenset({Label.BLINK, Label.INVALID})


def _reachable(label: Label, pso_as: str) -> frozenset[Label]:
    """`label` and everything it can be coarsened into, following edges."""
    if label is Label.PSO:
        target = Label.SACCADE if pso_as == PSO_AS_SACCADE else Label.FIXATION
        return frozenset({Label.PSO, target})
    return frozenset({label}) | COARSENING.get(label, frozenset())


def coarsen(label: Label, target: frozenset[Label], pso_as: str) -> Label | None:
    """`label` expressed in `target`'s vocabulary, or `None` if it cannot be.

    `pso_as` has no default: design spec section 2.5 requires the glissade
    assignment to be stated per comparison, and a default argument is how a
    caller would omit it and get one anyway.
    """
    effective = target | _ALWAYS_COMPARABLE
    if label in effective:
        return label
    for candidate in _reachable(label, pso_as):
        if candidate in effective:
            return candidate
    return None


def comparable(label: Label, b_vocabulary: frozenset[Label], pso_as: str) -> bool:
    """Whether `label` says anything `b_vocabulary` could be responsible for."""
    if label in _FROM_THE_MASK:
        return False
    return coarsen(label, b_vocabulary, pso_as) is not None


def shared_vocabulary(
    a: frozenset[Label], b: frozenset[Label], pso_as: str
) -> frozenset[Label]:
    """The vocabulary a pair is scored in. Stored on the row, because a score
    computed in a coarse vocabulary is not comparable to one computed in a fine
    one and any report aggregating across pairs must group by it."""
    shared = {
        coarsened
        for label in a | b
        if (coarsened := coarsen(label, a & b | _ALWAYS_COMPARABLE, pso_as)) is not None
    }
    return frozenset(shared) | _ALWAYS_COMPARABLE


def comparison_mask(
    a_labels: np.ndarray,
    b_labels: np.ndarray,
    a_vocabulary: frozenset[Label],
    b_vocabulary: frozenset[Label],
    pso_as: str,
) -> np.ndarray:
    """Which samples `n_samples_compared` counts.

    Symmetric by construction -- both shipped metrics are symmetric and the
    table's key is canonically ordered, so an asymmetric mask would make one
    stored row mean two different things depending on which detector happened
    to sort first.
    """
    keep = np.ones(len(a_labels), dtype=bool)
    for index, (left, right) in enumerate(zip(a_labels, b_labels, strict=True)):
        keep[index] = comparable(Label(left), b_vocabulary, pso_as) and comparable(
            Label(right), a_vocabulary, pso_as
        )
    return keep
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_consensus.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Mutation-check the rule that matters**

The comparability rule is the whole point of this task, so prove its tests can
fail. With `PYTHONDONTWRITEBYTECODE=1`, add a `saccade -> microsaccade` edge to
`COARSENING`:

```python
    Label.SACCADE: frozenset({Label.MICROSACCADE}),
```

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/eye/detect/test_consensus.py -q`
Expected: FAIL on `test_a_saccade_cannot_reach_a_microsaccade_only_vocabulary`
and `test_the_comparison_mask_excludes_what_either_side_cannot_claim`.

Restore with `git checkout -- wl_preproc/eye/detect/consensus.py`, and confirm
`git status --porcelain` shows only the new files before continuing.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/eye/detect/consensus.py tests/eye/detect/test_consensus.py
git commit -m "feat: the coarsening lattice, and what it cannot express"
```

---

### Task 2: The two metrics and the registry

**Files:**
- Modify: `wl_preproc/eye/detect/consensus.py`
- Test: `tests/eye/detect/test_consensus.py`

**Interfaces:**
- Consumes: Task 1's `comparison_mask`, `coarsen`, `shared_vocabulary`.
- Produces: `Metric` (frozen dataclass: `name: str`, `compute: MetricFn`);
  `CONSENSUS_METRICS: dict[str, Metric]`;
  `event_f1(a: np.ndarray, b: np.ndarray, mask: np.ndarray, tolerance_samples: int) -> float`;
  `cohen_kappa(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float`;
  `DEFAULT_EVENT_F1_TOLERANCE_SAMPLES: int = 10`.

- [ ] **Step 1: Write the failing tests**

```python
def test_identical_traces_score_one_on_both_metrics():
    """The trivial anchor. Without it a metric that always returned 0.0 would
    pass every disagreement test below."""
    from wl_preproc.eye.detect.consensus import cohen_kappa, event_f1

    labels = np.array(
        [Label.FIXATION] * 20 + [Label.SACCADE] * 10 + [Label.FIXATION] * 20
    )
    mask = np.ones(len(labels), dtype=bool)

    assert event_f1(labels, labels, mask, tolerance_samples=5) == 1.0
    assert cohen_kappa(labels, labels, mask) == 1.0


def test_cohen_kappa_is_zero_for_chance_agreement_not_for_disagreement():
    """**Why both metrics ship rather than one.** Kappa is chance-corrected, so
    two traces that agree only as often as their base rates predict score ~0
    even though raw agreement is high. A raw-agreement metric would read that
    as success."""
    from wl_preproc.eye.detect.consensus import cohen_kappa

    rng = np.random.default_rng(0)
    a = np.where(rng.random(4000) < 0.1, Label.SACCADE, Label.FIXATION)
    b = np.where(rng.random(4000) < 0.1, Label.SACCADE, Label.FIXATION)
    mask = np.ones(len(a), dtype=bool)

    assert abs(cohen_kappa(a, b, mask)) < 0.1


def test_event_f1_forgives_a_boundary_shift_that_kappa_punishes():
    """The other half of the same argument, in the other direction: an event
    both detectors found but bounded slightly differently is one event, and
    `event_f1`'s tolerance window says so. Per-sample kappa cannot -- which is
    why a pair scoring high on one and low on the other is informative rather
    than contradictory."""
    from wl_preproc.eye.detect.consensus import cohen_kappa, event_f1

    a = np.array([Label.FIXATION] * 20 + [Label.SACCADE] * 10 + [Label.FIXATION] * 20)
    b = np.array([Label.FIXATION] * 23 + [Label.SACCADE] * 10 + [Label.FIXATION] * 17)
    mask = np.ones(len(a), dtype=bool)

    assert event_f1(a, b, mask, tolerance_samples=5) == 1.0
    assert cohen_kappa(a, b, mask) < 0.8


def test_event_f1_does_not_match_beyond_its_tolerance():
    """The tolerance is a real window, not a licence to match anything. Same
    two traces as above, scored with a tolerance narrower than the shift."""
    from wl_preproc.eye.detect.consensus import event_f1

    a = np.array([Label.FIXATION] * 20 + [Label.SACCADE] * 10 + [Label.FIXATION] * 20)
    b = np.array([Label.FIXATION] * 23 + [Label.SACCADE] * 10 + [Label.FIXATION] * 17)
    mask = np.ones(len(a), dtype=bool)

    assert event_f1(a, b, mask, tolerance_samples=1) == 0.0


def test_an_event_only_one_detector_found_lowers_event_f1():
    """A false positive and a false negative are both real disagreement, and
    F1 counts both -- which precision or recall alone would not."""
    from wl_preproc.eye.detect.consensus import event_f1

    a = np.array([Label.FIXATION] * 10 + [Label.SACCADE] * 5 + [Label.FIXATION] * 35)
    b = np.array(
        [Label.FIXATION] * 10
        + [Label.SACCADE] * 5
        + [Label.FIXATION] * 10
        + [Label.SACCADE] * 5
        + [Label.FIXATION] * 20
    )
    mask = np.ones(len(a), dtype=bool)

    score = event_f1(a, b, mask, tolerance_samples=3)
    assert 0.6 < score < 0.7  # 1 matched, 1 unmatched in b: F1 = 2/3


def test_masked_out_samples_change_neither_metric():
    """`n_samples_compared` is what the row reports, and the metrics must be
    computed over exactly those samples. If an excluded sample could move a
    score, the stored `n_samples_compared` would describe a different
    computation from the one that produced the number beside it."""
    from wl_preproc.eye.detect.consensus import cohen_kappa, event_f1

    a = np.array([Label.SACCADE] * 5 + [Label.FIXATION] * 20)
    b = np.array([Label.SACCADE] * 5 + [Label.FIXATION] * 20)
    poisoned_a = np.concatenate([a, np.array([Label.SACCADE] * 10)])
    poisoned_b = np.concatenate([b, np.array([Label.FIXATION] * 10)])
    mask = np.concatenate([np.ones(25, dtype=bool), np.zeros(10, dtype=bool)])

    assert event_f1(poisoned_a, poisoned_b, mask, tolerance_samples=3) == 1.0
    assert cohen_kappa(poisoned_a, poisoned_b, mask) == 1.0


def test_both_metrics_are_symmetric():
    """The table's key orders the pair canonically `a < b`, so an asymmetric
    metric would store one number for what are really two measurements."""
    from wl_preproc.eye.detect.consensus import cohen_kappa, event_f1

    a = np.array([Label.FIXATION] * 10 + [Label.SACCADE] * 5 + [Label.FIXATION] * 35)
    b = np.array([Label.FIXATION] * 12 + [Label.SACCADE] * 6 + [Label.FIXATION] * 32)
    mask = np.ones(len(a), dtype=bool)

    assert event_f1(a, b, mask, 5) == event_f1(b, a, mask, 5)
    assert cohen_kappa(a, b, mask) == cohen_kappa(b, a, mask)


def test_an_empty_comparison_returns_nan_rather_than_a_confident_number():
    """A pair with nothing comparable has no score, and `0.0` would read as
    total disagreement while `1.0` would read as perfect agreement. Both are
    claims the data cannot support."""
    from wl_preproc.eye.detect.consensus import cohen_kappa, event_f1

    a = np.array([Label.SACCADE, Label.SACCADE])
    b = np.array([Label.FIXATION, Label.FIXATION])
    mask = np.zeros(2, dtype=bool)

    assert np.isnan(event_f1(a, b, mask, tolerance_samples=3))
    assert np.isnan(cohen_kappa(a, b, mask))


def test_the_metric_registry_names_exactly_what_ships():
    """The same completeness shape `DETECTORS` uses. A metric with no registry
    entry never runs; an entry naming no function fails on the first pair."""
    from wl_preproc.eye.detect.consensus import CONSENSUS_METRICS

    assert set(CONSENSUS_METRICS) == {"event_f1", "cohen_kappa"}
    for name, metric in CONSENSUS_METRICS.items():
        assert metric.name == name
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_consensus.py -k "metric or kappa or f1 or empty or masked" -v`
Expected: FAIL — `ImportError: cannot import name 'event_f1'`

- [ ] **Step 3: Implement**

Append to `wl_preproc/eye/detect/consensus.py`:

```python
#: `event_f1`'s matching window, in samples. **The metric's own parameter, not
#: a detection paramset's** (design spec section 6.1): it describes how a
#: comparison is made, not how either trace was produced, and putting it in a
#: detection paramset would make every detector's rows depend on a number no
#: detector uses. 10 samples is 20 ms at 500 Hz -- the same order as the ~24 ms
#: mean glissade duration section 2.5 quotes, so a boundary disagreement of
#: roughly one glissade is forgiven and a whole missed event is not.
DEFAULT_EVENT_F1_TOLERANCE_SAMPLES = 10

_EVENT_LABELS = frozenset(
    {Label.SACCADE, Label.MICROSACCADE, Label.PSO, Label.PURSUIT, Label.DRIFT}
)


def _event_starts(labels: np.ndarray, mask: np.ndarray) -> list[int]:
    """Onset index of each event run, over masked-in samples only."""
    starts: list[int] = []
    previous_was_event = False
    for index, label in enumerate(labels):
        is_event = bool(mask[index]) and Label(label) in _EVENT_LABELS
        if is_event and not previous_was_event:
            starts.append(index)
        previous_was_event = is_event
    return starts


def event_f1(
    a: np.ndarray, b: np.ndarray, mask: np.ndarray, tolerance_samples: int
) -> float:
    """Events matched within a tolerance window, as F1.

    What the U'n'Eye paper itself reports, so these numbers are comparable to
    published benchmarks rather than only to each other (design spec section
    6.1). Greedy nearest-first matching, each event used at most once.

    Returns `nan` when nothing is comparable: a pair with no shared samples has
    no score, and both `0.0` and `1.0` would be claims the data cannot support.
    """
    if not mask.any():
        return float("nan")
    a_starts, b_starts = _event_starts(a, mask), _event_starts(b, mask)
    if not a_starts and not b_starts:
        return 1.0
    if not a_starts or not b_starts:
        return 0.0

    unmatched_b = set(range(len(b_starts)))
    matched = 0
    for start in a_starts:
        best, best_distance = None, tolerance_samples + 1
        for candidate in unmatched_b:
            distance = abs(b_starts[candidate] - start)
            if distance < best_distance:
                best, best_distance = candidate, distance
        if best is not None:
            unmatched_b.discard(best)
            matched += 1

    precision = matched / len(a_starts)
    recall = matched / len(b_starts)
    if precision + recall == 0:
        return 0.0
    return float(2 * precision * recall / (precision + recall))


def cohen_kappa(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    """Per-sample agreement, chance-corrected.

    Catches boundary disagreement that `event_f1`'s tolerance window hides,
    which is why both ship rather than one (design spec section 6.1). Computed
    on the stored labels directly, over masked-in samples only.
    """
    if not mask.any():
        return float("nan")
    left, right = np.asarray(a)[mask], np.asarray(b)[mask]
    n = len(left)
    observed = float(np.sum(left == right)) / n

    expected = 0.0
    for label in set(left.tolist()) | set(right.tolist()):
        expected += (np.sum(left == label) / n) * (np.sum(right == label) / n)
    if expected == 1.0:
        # Both traces are one constant label. They agree perfectly and chance
        # explains all of it; kappa is undefined (0/0) rather than 1.0.
        return float("nan")
    return float((observed - expected) / (1.0 - expected))


@dataclass(frozen=True, slots=True)
class Metric:
    name: str
    #: `(a, b, mask, tolerance_samples) -> float`. Every metric takes the
    #: tolerance even where it ignores it, so `DetectorAgreement.make` needs no
    #: per-metric branch -- the same reason `_params_for` filters by declared
    #: field rather than by a list of keys to drop.
    compute: Callable[[np.ndarray, np.ndarray, np.ndarray, int], float]


CONSENSUS_METRICS: dict[str, Metric] = {
    "event_f1": Metric(
        name="event_f1",
        compute=lambda a, b, mask, tol: event_f1(a, b, mask, tol),
    ),
    "cohen_kappa": Metric(
        name="cohen_kappa",
        compute=lambda a, b, mask, _tol: cohen_kappa(a, b, mask),
    ),
}
```

Add to the module's imports: `from collections.abc import Callable` and
`from dataclasses import dataclass`.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/eye/detect/test_consensus.py -v`
Expected: PASS, 20 tests.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/eye/detect/consensus.py tests/eye/detect/test_consensus.py
git commit -m "feat: two agreement metrics, and why one is not enough"
```

---

### Task 3: The Otero-Millan detector

**Files:**
- Create: `wl_preproc/eye/detect/otero_millan.py`
- Modify: `wl_preproc/eye/detect/registry.py` (add the `DETECTORS` entry)
- Modify: `wl_preproc/eye/detect/engbert_kliegl.py` (one stale comment)
- Modify: `wl_preproc/schema/detect.py::register_default_paramsets` (`defaults` dict)
- Test: `tests/eye/detect/test_otero_millan.py`

**Interfaces:**
- Consumes: `labels.Run`/`LabelledInterval`, `measure.amplitude`,
  `measure.classify`, `measure.MICROSACCADE_MAX_DEG`.
- Produces: `OteroMillanParams` (frozen dataclass);
  `DEFAULT_OM_PARAMS: OteroMillanParams`;
  `detect_otero_millan(gaze_deg, velocity_deg_s, available, params) -> list[LabelledInterval]`;
  a `DETECTORS["otero_millan"]` entry with
  `vocabulary=frozenset({Label.SACCADE, Label.MICROSACCADE})`.

**Read before starting.** The reference implementation is MATLAB and carries
**no licence**, so it is readable as a specification of the algorithm and must
not be copied, vendored, or redistributed. Design spec §3.2 (corrected
2026-09-01) records this. The algorithm, from that reference:

1. Find velocity peaks as candidate events.
2. Per peak compute: amplitude, displacement, peak velocity, peak acceleration
   at onset and at braking, duration.
3. Feature vector is **`log(peak velocity)`, `log(peak acceleration at onset)`,
   `log(peak acceleration at brake)`** — the reference's own
   `featureSelection`. Amplitude is computed but deliberately not a clustering
   feature.
4. Z-score each feature; whiten by `P = V @ diag(sqrt(1/(D + 0.1)))` from the
   eigendecomposition of the covariance; keep components where
   `diag(D)/D[-1] > 0.05`.
5. k-means for `Nc` in 2..4, seeded deterministically from velocity-sorted
   quantile means. Renumber clusters by **descending** mean velocity, so
   cluster 1 holds the fastest peaks.
6. Choose `Nc` by mean silhouette computed on the BINARISED partition
   `min(idx, 2)` — saccade versus everything else — stopping when silhouette
   fails to improve by more than 1%.
7. Clusters `1 .. Nc-1` are accepted as saccadic when their **mean
   displacement exceeds 0.2°**. The last cluster is the noise cluster and is
   never accepted.

**Implement k-means and silhouette in numpy, in this module.** Do not import
`scikit-learn` or `scipy`. Two reasons, both load-bearing:

- Neither is a declared dependency. They arrive transitively via `kilosort`,
  `spikeinterface` and `networkx`, all `where: serv` in `wl.yaml`. The eye path
  needs no container and no GPU, and importing them here would give it a
  dependency that exists only because the ephys stack happens to be installed —
  invisible until a machine provisioned for eye work alone runs it.
- **Determinism.** `sklearn.cluster.KMeans` defaults to random initialisation
  with `n_init=10`. The reference seeds from velocity-sorted quantile means and
  is deterministic, which is what an agreement metric needs: a detector whose
  output varies run to run would make every score irreproducible and every
  disagreement unattributable. The reference's seeding is not a limitation to
  work around; it is the property to preserve.

- [ ] **Step 1: Write the failing tests**

```python
import numpy as np
import pytest

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.otero_millan import (
    DEFAULT_OM_PARAMS,
    OteroMillanParams,
    detect_otero_millan,
)


def _trace(events, n_samples=4000, seed=0):
    """A gaze trace with planted step events, plus low-amplitude noise.

    Returns `(gaze_deg, velocity_deg_s, available)` in the detector contract's
    own shapes. Velocity is the 5-point estimator the whole subsystem shares,
    so this fixture cannot disagree with production about what velocity is.
    """
    from wl_preproc.eye.detect.velocity import velocity

    rng = np.random.default_rng(seed)
    gaze = rng.normal(0.0, 0.02, size=(n_samples, 2))
    for start, stop, size in events:
        gaze[start:, 0] += size
        ramp = np.linspace(0.0, size, stop - start)
        gaze[start:stop, 0] += ramp - size
    v = velocity(gaze, fs_hz=500.0)
    available = np.full(n_samples, None, dtype=object)
    return gaze, v, available


def test_a_planted_large_saccade_is_detected_and_labelled_saccade():
    """Design spec section 3.1, corrected 2026-09-01: this detector's
    vocabulary is `saccade / microsaccade`, not `microsaccade` alone. The
    reference's only amplitude threshold is a 0.2 degree LOWER noise floor on a
    cluster's mean displacement -- there is no upper bound anywhere in it, and
    a 4 degree event must come back labelled `saccade`."""
    gaze, v, available = _trace([(1000, 1020, 4.0), (2000, 2020, 4.0), (3000, 3020, 4.0)])

    intervals = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)

    assert intervals
    assert any(1000 <= interval.start <= 1030 for interval in intervals)
    assert all(interval.label is Label.SACCADE for interval in intervals)


def test_a_planted_small_event_is_labelled_microsaccade():
    """The other half of the declared vocabulary, and the reason
    `microsaccade_max_deg` is declared on this detector's params."""
    gaze, v, available = _trace([(1000, 1015, 0.4), (2000, 2015, 0.4), (3000, 3015, 0.4)])

    intervals = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)

    assert intervals
    assert all(interval.label is Label.MICROSACCADE for interval in intervals)


def test_both_sizes_in_one_trace_are_split_at_the_shared_threshold():
    """The split comes from `measure.classify` against the SHARED
    `microsaccade_max_deg`, not from anything private to this detector --
    design spec section 3's argument for measuring centrally applies to the
    threshold that measurement is compared against."""
    gaze, v, available = _trace(
        [(800, 820, 4.0), (1600, 1615, 0.4), (2400, 2420, 4.0), (3200, 3215, 0.4)]
    )

    intervals = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)
    labels = {interval.label for interval in intervals}

    assert Label.SACCADE in labels
    assert Label.MICROSACCADE in labels


def test_it_returns_nothing_on_a_trace_with_no_events():
    """Pure noise has no cluster whose mean displacement clears 0.2 degrees.
    A detector that returns events from noise would make every agreement score
    meaningless, and this is the cheapest place to catch it."""
    gaze, v, available = _trace([])

    assert detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS) == []


def test_unavailable_samples_are_never_claimed():
    """The validity mask (design spec section 2) owns those samples. A blink's
    velocity spike must not become an event, and must not inflate the
    clustering's feature distribution either."""
    gaze, v, available = _trace([(1000, 1020, 4.0), (2000, 2020, 4.0), (3000, 3020, 4.0)])
    available[990:1030] = Label.BLINK

    intervals = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)

    for interval in intervals:
        assert not (interval.start < 1030 and 990 < interval.stop)


def test_it_is_deterministic():
    """**The property that makes an agreement metric meaningful.** The
    reference seeds k-means from velocity-sorted quantile means rather than
    randomly, and this reimplementation preserves that: a detector whose output
    varied run to run would make every score irreproducible and every
    disagreement unattributable to a method."""
    gaze, v, available = _trace([(1000, 1020, 4.0), (2000, 2020, 4.0), (3000, 3020, 4.0)])

    first = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)
    second = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)

    assert [(i.start, i.stop, i.label) for i in first] == [
        (i.start, i.stop, i.label) for i in second
    ]


def test_reliability_is_populated_per_detection():
    """`EyeDetection.Run.reliability` exists for this detector (design spec
    section 5) and has been null for every row so far. `silhouette()` is
    inherently per-observation; the MATLAB reference computes it per peak and
    keeps only `mean(...)` as a session statistic, so the per-detection value
    is available in the method and this reimplementation retains it."""
    gaze, v, available = _trace([(1000, 1020, 4.0), (2000, 2020, 4.0), (3000, 3020, 4.0)])

    intervals = detect_otero_millan(gaze, v, available, DEFAULT_OM_PARAMS)

    assert intervals
    assert all(interval.reliability is not None for interval in intervals)
    assert all(-1.0 <= interval.reliability <= 1.0 for interval in intervals)


def test_the_noise_floor_is_a_lower_bound_and_is_the_only_amplitude_rule():
    """Guards the correction this task exists to honour. Raising the floor
    above a planted event's size must silence it; there is no upper bound that
    could silence a large one."""
    gaze, v, available = _trace([(1000, 1015, 0.4), (2000, 2015, 0.4), (3000, 3015, 0.4)])
    strict = OteroMillanParams(
        min_cluster_displacement_deg=2.0,
        max_clusters=DEFAULT_OM_PARAMS.max_clusters,
        min_isi_samples=DEFAULT_OM_PARAMS.min_isi_samples,
        microsaccade_max_deg=DEFAULT_OM_PARAMS.microsaccade_max_deg,
    )

    assert detect_otero_millan(gaze, v, available, strict) == []


def test_it_is_registered_with_the_corrected_vocabulary():
    """Design spec section 3.1 as amended 2026-09-01. Declaring `microsaccade`
    alone here would make `registry.Detector.detect` refuse every large saccade
    this detector legitimately finds."""
    from wl_preproc.eye.detect.registry import DETECTORS

    detector = DETECTORS["otero_millan"]
    assert detector.vocabulary == frozenset({Label.SACCADE, Label.MICROSACCADE})


def test_the_registry_and_the_paramsets_still_agree():
    """The completeness claim, now over two detectors rather than one."""
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import detect

    assert set(detect.register_default_paramsets()) == set(DETECTORS)
```

- [ ] **Step 2: Run to verify they fail**

Two of these fail for a reason worth predicting so it does not read as a
surprise: `test_reliability_is_populated_per_detection` and
`test_a_merged_run_reports_no_reliability_rather_than_a_borrowed_one` cannot
pass until the step below adds `reliability` to `labels.Run`. That is
ordinary TDD, not a defect in the plan.

Run: `.venv/bin/python -m pytest tests/eye/detect/test_otero_millan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named
'wl_preproc.eye.detect.otero_millan'`

- [ ] **Step 3: Add `reliability` to `Run`, and carry it to the stored row**

`labels.Run` is `(start, stop, label)` and carries no reliability, while
`EyeDetection.Run.reliability` is a stored column that has been null on every
row ever written (design spec §5 reserves it for exactly this detector). Two
changes connect them, and the second is the one with a trap in it.

**Add the field to `Run`:**

```python
    start: int
    stop: int
    label: Label
    #: Otero-Millan's per-detection silhouette (design spec section 5); `None`
    #: for every other detector and for every run this subsystem reconstructs
    #: rather than detects. Defaulted so `Run(start, stop, label)` keeps
    #: working everywhere -- `runs_from_labels` builds runs from a label array
    #: and has no reliability to give them.
    reliability: float | None = None
```

`None` rather than `float("nan")` is deliberate and load-bearing. This
repository has twice had CI go red on 3.13 alone because a dataclass whose
field defaulted to `nan` compared unequal to itself once `__eq__` stopped
going through `tuple.__eq__`'s identity check (`27917b4`, and again in
`ea9b94b`). `None is None` is true on every interpreter.

**Then carry it across `_insert_trace`'s re-derivation, which is where the
trap is.** That method deliberately does NOT trust the detector's spans: it
writes each span's label onto the mask and then re-derives final runs with
`runs_from_labels`, so a stored run's `(start, stop)` need not equal any
detector interval's. Read its docstring before changing it — the second
measurement is there for a stated reason and must survive.

Map by **exact `(start, stop)` match**, and store `None` otherwise:

```python
        reliability_by_span = {
            (interval.start, interval.stop): interval.reliability
            for interval in intervals
        }
        # ... in `_run_row`:
        reliability = reliability_by_span.get((run.start, run.stop))
```

A final run that merged two detector intervals corresponds to neither, and
attributing one of their reliabilities to it would be a fabricated number in a
column whose whole purpose is to say how much to trust a detection. `None` is
the honest answer there. The conjunction trace gets `None` for the same reason
it gets its label derived rather than checked: no detector produced it.

- [ ] **Step 4: Implement the detector**

Create `wl_preproc/eye/detect/otero_millan.py` implementing steps 1-7 above.
`OteroMillanParams` carries exactly these fields:

```python
@dataclass(frozen=True, slots=True)
class OteroMillanParams:
    #: The reference's only amplitude rule, and it is a LOWER bound: a
    #: candidate cluster is accepted when its mean displacement exceeds this.
    #: There is no upper bound in the method -- the "microsaccade" framing in
    #: the reference's own Example.m comes from its plot limits, not from the
    #: detector (design spec section 3.1, corrected 2026-09-01).
    min_cluster_displacement_deg: float
    #: The reference's `NumMaxClusters`. k-means is run for 2..this many and
    #: the count is chosen by silhouette.
    max_clusters: int
    #: The reference's `MIN_ISI`, in samples rather than milliseconds: this
    #: detector's contract (design spec section 3) carries no sampling rate,
    #: and converting here would require inventing one.
    min_isi_samples: int
    #: Declared for the same reason `EngbertKlieglParams` declares it -- this
    #: detector's vocabulary splits by amplitude, so it consumes the SHARED
    #: cut rather than owning one. See `schema/detect.py::_params_for`.
    microsaccade_max_deg: float = MICROSACCADE_MAX_DEG


DEFAULT_OM_PARAMS = OteroMillanParams(
    min_cluster_displacement_deg=0.2,
    max_clusters=4,
    min_isi_samples=15,  # 30 ms at 500 Hz, the reference's own MIN_ISI
)
```

Label each accepted interval with `classify(amplitude(gaze_deg, start, stop),
params.microsaccade_max_deg)` — the shared functions, never a private formula,
so design spec §3's guarantee that a disagreement is "never a disagreement
about measurement" holds literally. Set each interval's `reliability` to that
peak's own silhouette value.

- [ ] **Step 5: Test that the carry works, and that the honest gap stays honest**

```python
def test_reliability_survives_the_run_re_derivation(stepped_session):
    """`_insert_trace` re-derives runs with `runs_from_labels` rather than
    trusting detector spans, so a per-detection value attached upstream is easy
    to lose silently -- the stored column would simply stay null, which looks
    exactly like a detector having nothing to say.
    """
    from wl_preproc.schema import detect

    session_key, _report, _ = stepped_session
    rows = (detect.EyeDetection.Run & {**session_key, "trace": "left"}).to_dicts()
    events = [r for r in rows if r["label"] in ("saccade", "microsaccade")]

    assert events
    assert any(r["reliability"] is not None for r in events)


def test_a_merged_run_reports_no_reliability_rather_than_a_borrowed_one():
    """Design spec section 5's column says how much to trust a detection. A run
    corresponding to no single detector interval has no such number, and
    borrowing one from either half would be a fabrication in the one column a
    reader consults to decide what to believe.
    """
    from wl_preproc.eye.detect.labels import Run

    reliability_by_span = {(10, 20): 0.8, (20, 30): 0.9}
    merged = Run(10, 30, Label.SACCADE)

    assert reliability_by_span.get((merged.start, merged.stop)) is None
```

- [ ] **Step 6: Register it**

In `registry.py`'s `DETECTORS`:

```python
    "otero_millan": Detector(
        name="otero_millan",
        vocabulary=frozenset({Label.SACCADE, Label.MICROSACCADE}),
        run=detect_otero_millan,
    ),
```

In `schema/detect.py::register_default_paramsets`, extend `defaults`:

```python
    defaults = {
        "engbert_kliegl": asdict(DEFAULT_EK_PARAMS),
        "otero_millan": asdict(DEFAULT_OM_PARAMS),
    }
```

- [ ] **Step 7: Correct one stale comment**

`engbert_kliegl.py`'s `microsaccade_max_deg` field comment reads "a detector
with no amplitude-derived labels (Otero-Millan emits `microsaccade` alone,
design spec section 3.1) simply does not declare it". That example is now
wrong — Otero-Millan declares the whole split and DOES declare this field.
Replace the example with U'n'Eye, whose declared `{saccade}` genuinely has no
amplitude split. Do not change the reasoning; only the example.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -q && wl-check`
Expected: PASS, and `wl.yaml: no findings`.

- [ ] **Step 9: Confirm no new dependency was added**

Run: `grep -rn "sklearn\|scikit\|scipy" wl_preproc/eye/`
Expected: no matches. If there are, the constraint above was violated.

- [ ] **Step 10: Commit**

```bash
git add wl_preproc/eye/detect/otero_millan.py wl_preproc/eye/detect/registry.py \
        wl_preproc/eye/detect/engbert_kliegl.py wl_preproc/schema/detect.py \
        tests/eye/detect/test_otero_millan.py
git commit -m "feat: a second detector, and it detects saccades of any size"
```

---

### Task 4: Validate the reimplementation against the paper

**Files:**
- Test: `tests/eye/detect/test_otero_millan_validation.py`
- Modify: `docs/superpowers/specs/2026-08-31-saccade-detection-design.md` (§11 item 5)

**Interfaces:** Consumes Task 3's `detect_otero_millan`.

**Why this is its own task.** Design spec §3.2: *"a buggy reimplementation is
indistinguishable from a genuine detector disagreement… it looks like exactly
the finding this subsystem exists to surface."* Otero-Millan has **no usable
oracle** — the reference is MATLAB and unlicensed (§3.2, corrected
2026-09-01) — so it joins BMD as a detector validated against its paper's own
reported statistics. Without this task, Task 3 ships a detector whose
disagreement with Engbert-Kliegl cannot be told from a bug.

- [ ] **Step 1: Write the validation tests**

Against the real reference recording, gated on `WLPP_OHDPI_REFERENCE` exactly
as `tests/schema/test_detect_populate.py`'s own gated test is, checking the
published predictions that a wrong implementation would fail:

- **Microsaccade rate** falls in a plausible physiological band (roughly 0.5-3
  per second of usable trace). A detector firing far outside it is wrong
  regardless of whether it runs.
- **The main sequence holds**: across detected events, `log(peak velocity)`
  correlates with `log(amplitude)` at r > 0.9. This is the strongest single
  check available without an oracle — it is a property of real saccades that no
  clustering artefact reproduces.
- **Overlap with Engbert-Kliegl is substantial but not total**: the two
  methods should agree on most large events and differ at the margins. Assert
  `event_f1` between them exceeds 0.5 and is below 1.0. Both bounds matter: a
  score of 1.0 means the reimplementation has collapsed into the other
  detector, which would make the whole suite vacuous.

Print the measured figures with `capsys.disabled()`, the way Task 8 of the
stage-1 plan does, so the numbers are visible rather than only asserted.

- [ ] **Step 2: Run it and read the numbers**

Run: `WLPP_OHDPI_REFERENCE=~/Downloads/Tutorial/OpenIris-2024Jul31-114628/OpenIris-2024Jul31-114628.txt \
  .venv/bin/python -m pytest tests/eye/detect/test_otero_millan_validation.py -s -v`

- [ ] **Step 3: Record what was measured in the spec**

Update §11 item 5 with the measured figures and what they do and do not
establish. **If any check fails, that is the finding** — report it and do not
adjust the bounds to pass. A reimplementation that cannot reproduce the
paper's own statistics is not ready to have its rows compared.

- [ ] **Step 4: Commit**

```bash
git add tests/eye/detect/test_otero_millan_validation.py \
        docs/superpowers/specs/2026-08-31-saccade-detection-design.md
git commit -m "test: validate otero-millan against the paper, having no oracle"
```

---

### Task 5: The `DetectorAgreement` table

**Files:**
- Create: `wl_preproc/schema/consensus.py`
- Modify: `wl_preproc/daemon.py` (`_computed_tables`, `_PROJECT_SCHEMA_MODULES`)
- Test: `tests/schema/test_consensus_schema.py`, `tests/schema/test_consensus_populate.py`

**Interfaces:**
- Consumes: Task 1's `comparison_mask`/`shared_vocabulary`, Task 2's
  `CONSENSUS_METRICS`/`DEFAULT_EVENT_F1_TOLERANCE_SAMPLES`,
  `schema.detect.EyeDetection`.
- Produces: `DetectorAgreement` (`dj.Computed`), `activate(prefix)`.

**Key**, exactly as design spec §6.1 specifies: `(subject,
session_datetime, trace, validity_paramset_idx, paramset_a, paramset_b,
metric, vocabulary, pso_as)`, canonically ordered `paramset_a < paramset_b`.
Columns: `value : double`, `n_samples_compared : int`.

**Read `schema/detect.py` before writing `key_source`.** Two defects that
subsystem shipped are directly relevant: a `key_source` joining on a bare
`paramset_type` is permanently empty and silently produces zero rows forever,
and nothing in production registered the paramsets, so the whole table was
inert while every test passed because the tests registered them themselves.
Whatever registers this table's own defaults must be reachable from
`daemon.run_once`, and a test must fail if that production call is removed.

**Refusal is first-class**, as everywhere else in this subsystem: a session
whose `EyeDetection` rows are refused for either detector yields no agreement
row rather than a zero, because "the detectors disagreed completely" and "one
of them never ran" must never render identically.

- [ ] **Step 1: Write the failing schema tests**

Assert: the table joins `daemon._computed_tables()`; the key is exactly the
nine attributes above; `# Key:` is documented in the definition as
`test_guardrails.py::test_every_table_documents_its_key_in_schema` requires;
no bare `longblob`; `paramset_a < paramset_b` holds on every stored row.

- [ ] **Step 2: Write the failing populate tests**

Against a real container, on a synthetic session with both detectors
registered: rows exist for both metrics; `vocabulary` reads
`microsaccade,saccade,fixation` (EK and OM declare the same split, so nothing
is coarsened and nothing excluded — design spec §6.1's simplest case);
`n_samples_compared` equals the count of samples neither trace called `blink`
or `invalid`; a session with a refused detection for either detector yields no
row for that pair.

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run against a real container**

Run: `.venv/bin/python -m pytest tests/schema/test_consensus_schema.py tests/schema/test_consensus_populate.py -v`

- [ ] **Step 5: Mutation-check the key and the mask**

With `PYTHONDONTWRITEBYTECODE=1`: drop `vocabulary` from the key and confirm a
test fails; make `comparison_mask` return all-ones and confirm
`n_samples_compared` is wrong. Restore each with `git checkout --` and confirm
`git status` is clean between them.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/consensus.py wl_preproc/daemon.py tests/schema/test_consensus_*.py
git commit -m "schema: pairwise detector agreement, keyed by the vocabulary it was scored in"
```

---

### Task 6: The report's agreement line, and the stage handoff

**Files:**
- Modify: `wl_preproc/cli/report.py`
- Modify: `tests/cli/test_detect_report.py` (Step 2 rewrites the stage-1
  absence test that lives there; leaving it standing would make the suite
  assert both that the agreement line exists and that it does not)
- Test: `tests/cli/test_consensus_report.py`
- Create: `docs/handoffs/YYYY-MM-DD-consensus-and-otero-millan-built.md`

**Interfaces:** Consumes Task 5's `DetectorAgreement`.

The stage-1 report deliberately has **no** agreement line, and
`test_no_agreement_line_exists_in_this_stage` asserts its absence — because one
detector cannot disagree with anything and a line always reading `1.00` would
look like a measurement. That test must now be replaced, not deleted quietly:
its docstring records why it existed, and the replacement should record why the
condition no longer holds.

- [ ] **Step 1: Write the failing tests**

Assert: a `### Detector agreement` subsection exists; each line names both
paramsets, the metric, the vocabulary and `pso_as`; a pair scored in a coarse
vocabulary is never rendered beside one scored in a fine vocabulary without the
vocabulary shown, since §6.1 requires any aggregation to group by it;
`n_samples_compared` appears, so a score from a heavily-invalid session is not
read as though computed over a whole one.

- [ ] **Step 2: Replace the stage-1 absence test**

Rewrite `test_no_agreement_line_exists_in_this_stage` as
`test_the_agreement_line_appears_once_a_second_detector_exists`, keeping its
original reasoning in the docstring and stating what changed.

- [ ] **Step 3: Implement in `build_report`**

Follow `_detection_rows`/`_unusable_per_eye`'s existing shape, including their
reason for living outside `gather_readings` — read their docstrings. Aggregate
in the database rather than fetching the table, as `_unusable_per_eye` now does.

- [ ] **Step 4: Full suite, `wl-check`, and Python 3.13**

```bash
.venv/bin/python -m pytest -q && wl-check
```

Then, under a 3.13 interpreter:

```bash
pytest --noconftest tests/eye tests/contracts -q
```

`.venv` is 3.11 and CI runs both. This repository has already shipped two
merges that were green on 3.11 and red on 3.13 — the second was caught only
because a branch was merged forward before landing. Report what you actually
ran; if the 3.13 environment cannot be created, say so rather than reporting
the 3.11 run as though it covered both.

- [ ] **Step 5: Commit and write the handoff**

```bash
git add wl_preproc/cli/report.py tests/cli/test_consensus_report.py
git commit -m "report: what the two detectors agreed about"
```

Then write the handoff recording: the measured agreement between Engbert-Kliegl
and Otero-Millan on the reference recording, what Task 4's validation did and
did not establish, which of the eight labels are now produced, **any mutation
that survived**, and what stage 2B inherits. A surviving mutation is a gap in
the tests, and naming it is what stops the next stage building on ground nobody
checked.

---

## Not in this plan

- **`blended_agreement`, the N-way metric** (§6). With two detectors it equals
  the pairwise number, and machinery whose only consumer is a future task is
  the unexercised-fallback defect this project's checkpoint records three times
  over. It lands with the third detector.
- **The PSO-capable detectors** — Nyström-Holmqvist, NSLR, REMoDNaV. All three
  emit `pso`/`pursuit`/`fixation`, and `schema/detect.py::_conjunction_label`
  raises `UndecidedConjunctionLabel` for any vocabulary that is not a subset of
  `{saccade, microsaccade}`. That is deliberate — §2.5 forbids defaulting the
  glissade assignment — so registering any of them requires resolving it first.
  Stage 2B, with its own design conversation.
- **U'n'Eye, `torch`, and vendoring** (§8). Its own infrastructure, its own
  licence question, and the first piece of this project that genuinely wants a
  GPU.
- **Saccade vigor and the main-sequence fits** (§6.5). The condition grain
  needs a generator that emits `CONDITION` payloads, and none exists — verified
  2026-09-01.
- **Gap-aware segmentation** (timebase), which validity criterion 4 is blocked
  on. Ruled 2026-09-01, spec not yet written.
