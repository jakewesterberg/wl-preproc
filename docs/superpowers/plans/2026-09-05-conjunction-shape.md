# Conjunction Shape Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a conjunction run the intersection of two runs of the same
kind, so the conjunction trace has the same vocabulary as the per-eye traces
it is built from, and four blocked detectors unblock.

**Architecture:** `_overlapping` keeps its exact signature and becomes the
single-kind primitive — every one of its 16 existing test call sites passes
single-kind input already, so none change. A new `_conjunction_runs` groups
both eyes' runs by kind, calls `_overlapping` once per kind, and concatenates.
`_conjunction_label` narrows from the whole declared vocabulary to its
saccadic slice, and its subset raise is replaced by an exhaustiveness guard on
the kind map.

**Tech Stack:** Python 3.11 (floor; CI also runs 3.13), DataJoint 2.3.2,
MySQL 8 via `testcontainers`, pytest, numpy.

**Spec:** `docs/superpowers/specs/2026-09-05-conjunction-shape-design.md`

## Global Constraints

- **Run the suite as `.venv/bin/python -m pytest`, from the repo root.** Not
  `pytest`, not `.venv/bin/pytest` — the `.pth` trap makes those unreliable.
- **Develop against 3.11. CI runs 3.11 AND 3.13.** A green local run is
  evidence about 3.11 on macOS arm64 and nothing else. Push and read
  `gh run list --branch spec/conjunction-shape` before claiming CI green.
- **The suite must stay at zero warnings.** DataJoint 2.3.2 deprecates bare
  `fetch()`; use `to_arrays` / `to_dicts` / `fetch1`.
- **Populate tests go through `daemon.run_once()`, never `make()` by hand.**
  An empty `key_source` passes every test that calls `make()` directly while
  proving nothing computes in production. This is how stage 1's worst defect
  stayed invisible.
- **Branch is `spec/conjunction-shape`**, already created, spec already
  committed at `59026cf`.
- **Every commit message ends with:**
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01RTPFVKnR7yeEYsC1CYuiaH
  ```

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `wl_preproc/schema/detect.py` | The kind map, `_conjunction_runs`, `_conjunction_label`, the `make()` call site | Modify |
| `tests/schema/test_detect_populate.py` | Unit tests for the three functions above, and the end-to-end multi-kind fixture | Modify |
| `docs/CHECKPOINT.md` | "What is next" item 4 currently says four detectors are blocked on a decision | Modify |
| `wl.yaml` | `status.next` says the same | Modify |
| `docs/handoffs/2026-09-02-the-conjunction-label-decision.md` | Reads as live; is superseded | Modify |

No new module. The three new functions are private helpers beside the ones
they replace, in the file that already owns conjunction construction.

---

### Task 1: The kind map and its exhaustiveness guard

**Files:**
- Modify: `wl_preproc/schema/detect.py` (add beside `_AMPLITUDE_DERIVED_VOCABULARY`, around line 790)
- Test: `tests/schema/test_detect_populate.py`

**Interfaces:**
- Consumes: `Label` from `wl_preproc.eye.detect.labels`
- Produces: `_KIND_OF: dict[Label, str]`, `_NOT_INTERSECTED: frozenset[Label]`,
  `_kind_of(label: Label) -> str | None`, `UnknownLabelKind(ValueError)`

- [ ] **Step 1: Write the failing test**

```python
def test_every_label_has_a_kind_or_is_deliberately_excluded():
    """Design spec section 3.1's exhaustiveness guard. The label enum is
    closed because the migration window shuts January 2027, so a ninth label
    added without updating the kind map must fail loudly rather than be
    silently dropped from every conjunction."""
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.schema.detect import _KIND_OF, _NOT_INTERSECTED, _kind_of

    assert set(_KIND_OF) | _NOT_INTERSECTED == set(Label)
    assert not (set(_KIND_OF) & _NOT_INTERSECTED)

    # saccade and microsaccade are ONE kind: design spec section 1 calls them
    # "a split, not a ranking" -- the same event distinguished only by size.
    assert _kind_of(Label.SACCADE) == _kind_of(Label.MICROSACCADE)
    # Every other emitted label is its own kind.
    assert len({_kind_of(Label.PSO), _kind_of(Label.PURSUIT),
                _kind_of(Label.DRIFT), _kind_of(Label.SACCADE)}) == 4
    # fixation is the synthesized background (spec section 1.2); blink and
    # invalid come from the validity mask and are in no vocabulary.
    for label in (Label.FIXATION, Label.BLINK, Label.INVALID):
        assert _kind_of(label) is None


def test_a_single_label_kind_is_keyed_by_its_own_label_value():
    """`_conjunction_runs` labels a non-saccadic kind with `Label(kind)`, so
    the kind key and the label value must agree. A kind named anything else
    would raise `ValueError` deep inside the grouping loop, for one detector,
    only once a real recording produced that label."""
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.schema.detect import _KIND_OF

    for label, kind in _KIND_OF.items():
        if kind != "saccadic":
            assert Label(kind) is label, f"kind {kind!r} does not name {label!r}"


def test_a_label_with_no_kind_raises():
    """The guard has teeth: it is reachable if the enum grows and the map
    does not."""
    import pytest
    from wl_preproc.schema.detect import UnknownLabelKind, _kind_of

    with pytest.raises(UnknownLabelKind, match="no conjunction kind"):
        _kind_of("nystagmus")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py -k "kind" -v`
Expected: FAIL with `ImportError: cannot import name '_KIND_OF'`

- [ ] **Step 3: Write minimal implementation**

Add immediately after `_AMPLITUDE_DERIVED_VOCABULARY`:

```python
class UnknownLabelKind(ValueError):
    """A label reached the conjunction with no kind assigned to it."""


#: Which labels intersect with which. A conjunction run is the intersection of
#: two runs of the SAME kind, and carries that kind's label (design spec
#: `2026-09-05-conjunction-shape-design.md` section 1).
#:
#: **`saccade` and `microsaccade` share a kind**, because section 1 of the
#: detection spec calls them "a split, not a ranking" -- one event
#: distinguished only by size. They intersect together and the surviving span
#: is labelled by `classify` on its OWN measured amplitude, which is what
#: stage 1 already did and what keeps label and amplitude derived once, from
#: one interval. Every other emitted label is its own kind and intersects only
#: with itself, so a binocular glissade is stored as `pso` rather than folded
#: into a saccade or dropped.
_KIND_OF: dict[Label, str] = {
    Label.SACCADE: "saccadic",
    Label.MICROSACCADE: "saccadic",
    # **Every non-saccadic kind's key IS its label's own value**, and
    # `_conjunction_runs` relies on it: `Label(kind)` is how such a kind
    # labels itself. Tested, not trusted -- see
    # `test_a_single_label_kind_is_keyed_by_its_own_label_value`. "saccadic"
    # is deliberately not a `Label`, because that kind has two of them and no
    # single label could name it.
    Label.PSO: Label.PSO.value,
    Label.PURSUIT: Label.PURSUIT.value,
    Label.DRIFT: Label.DRIFT.value,
}

#: Labels that are never intersected, and why -- listed rather than left as
#: absences, so `_kind_of`'s guard can tell "deliberately excluded" from "a
#: ninth label nobody mapped".
#:
#: `fixation` is the synthesized background: `_insert_trace` paints every
#: sample no interval claimed, so a region survives as `fixation` whether an
#: intersection painted it or the fill did. Intersecting it would run the
#: nested loop over the largest runs in the trace for no observable difference
#: (spec section 1.2). `blink` and `invalid` come from the validity mask,
#: never from a detector, and are in no detector's vocabulary at all.
_NOT_INTERSECTED = frozenset({Label.FIXATION, Label.BLINK, Label.INVALID})


def _kind_of(label) -> str | None:
    """`label`'s conjunction kind, or `None` if it is deliberately not
    intersected.

    Raises rather than returning `None` for an unmapped label. Design spec
    section 1 declares all eight labels because the migration window closes
    January 2027; this is what catches a ninth added without updating
    `_KIND_OF`, which would otherwise vanish from every conjunction with
    nothing to show for it."""
    if label in _NOT_INTERSECTED:
        return None
    try:
        return _KIND_OF[label]
    except KeyError as exc:
        raise UnknownLabelKind(
            f"{label!r} has no conjunction kind. Every label is either in "
            f"`_KIND_OF` or deliberately in `_NOT_INTERSECTED`; a new one is "
            f"in neither until someone decides which it is"
        ) from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py -k "kind" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/schema/detect.py tests/schema/test_detect_populate.py
git commit -m "detect: the conjunction kind map, and a guard on a closed enum

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RTPFVKnR7yeEYsC1CYuiaH"
```

---

### Task 2: `_conjunction_runs` — group by kind, intersect within

**Files:**
- Modify: `wl_preproc/schema/detect.py` (add after `_overlapping`, around line 778)
- Test: `tests/schema/test_detect_populate.py`

**Interfaces:**
- Consumes: `_kind_of` and `_KIND_OF` (Task 1); `_overlapping(left, right, min_duration_samples, label_for) -> list[Run]`, **unchanged**
- Produces: `_conjunction_runs(left: list[Run], right: list[Run], min_duration_samples: int, saccadic_label_for: Callable[[int, int], Label]) -> list[Run]`

**`_overlapping` is not modified.** All 16 of its existing call sites pass
single-kind input and keep passing it. It is now the single-kind primitive
and this function is the only thing that groups.

- [ ] **Step 1: Write the failing test**

```python
def test_a_left_fixation_never_crosses_a_right_saccade():
    """THE defect this change exists to fix. `_overlapping` intersects on
    time alone and never reads a label -- correct only while every emitted
    label is the same kind of thing, which is true of the two registered
    detectors and false for all four blocked ones.

    Three of the four emit `fixation`, which TILES the recording, so a left
    fixation would have crossed a right saccade and been kept as a binocular
    event. No stage-1 or stage-2A test could reach this: finding it needs a
    detector that emits more than one kind, and neither stage has one."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_runs

    left = [Run(0, 1000, Label.FIXATION)]
    right = [Run(400, 460, Label.SACCADE)]
    assert _conjunction_runs(left, right, 6, _always(Label.SACCADE)) == []


def test_a_binocular_glissade_survives_as_pso():
    """The shape rule. Both eyes called it `pso`; the conjunction says `pso`,
    not `saccade` and not nothing."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_runs

    left = [Run(100, 130, Label.SACCADE), Run(130, 160, Label.PSO)]
    right = [Run(105, 135, Label.SACCADE), Run(135, 165, Label.PSO)]
    runs = _conjunction_runs(left, right, 6, _always(Label.SACCADE))

    assert runs == [Run(105, 130, Label.SACCADE), Run(135, 160, Label.PSO)]


def test_kinds_that_disagree_produce_no_conjunction_run_of_either():
    """Agreement on kind is what supplies the label, so disagreement supplies
    nothing. This is NOT the arbitration stage 1 removed: that rule RANKED
    the two eyes' labels through `PRECEDENCE`; this requires agreement and
    ranks nothing."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_runs

    left = [Run(100, 160, Label.SACCADE)]
    right = [Run(100, 160, Label.PSO)]
    assert _conjunction_runs(left, right, 6, _always(Label.SACCADE)) == []


def test_the_amplitude_split_is_one_kind_not_two():
    """Design spec section 1: `saccade` and `microsaccade` are "a split, not a
    ranking". A left saccade meeting a right microsaccade is two eyes
    agreeing that an event happened and differing only about its size -- which
    the conjunction settles by measuring its OWN amplitude, exactly as stage 1
    did. Dropping this pair would be a behaviour change for the two shipped
    detectors."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_runs

    left = [Run(100, 160, Label.SACCADE)]
    right = [Run(100, 160, Label.MICROSACCADE)]
    runs = _conjunction_runs(left, right, 6, _always(Label.MICROSACCADE))

    assert runs == [Run(100, 160, Label.MICROSACCADE)]


def test_spans_of_different_kinds_never_overlap():
    """Load-bearing for `_insert_trace`, which paints intervals onto one
    array and lets a later interval overwrite an earlier one. Two kinds'
    spans cannot overlap because each is a subset of a left run of its own
    kind, and one eye's runs are disjoint by construction -- but that is an
    argument, and this is the test that makes it a fact."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_runs

    left = [Run(0, 50, Label.SACCADE), Run(50, 100, Label.PSO),
            Run(100, 150, Label.PURSUIT)]
    right = [Run(10, 60, Label.SACCADE), Run(45, 110, Label.PSO),
             Run(90, 140, Label.PURSUIT)]
    runs = sorted(_conjunction_runs(left, right, 1, _always(Label.SACCADE)),
                  key=lambda run: run.start)

    for earlier, later in zip(runs, runs[1:]):
        assert earlier.stop <= later.start, f"{earlier} overlaps {later}"


def test_conjunction_runs_come_back_sorted_by_start():
    """Concatenating per-kind results would otherwise return them grouped by
    kind, which makes every assertion about them depend on dict ordering."""
    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.schema.detect import _conjunction_runs

    left = [Run(200, 260, Label.PSO), Run(0, 60, Label.SACCADE)]
    right = [Run(200, 260, Label.PSO), Run(0, 60, Label.SACCADE)]
    runs = _conjunction_runs(left, right, 6, _always(Label.SACCADE))

    assert [run.start for run in runs] == [0, 200]
```

**`_always` currently lives in the TEST module** (`tests/schema/
test_detect_populate.py:1301`), not in `detect.py`. `_conjunction_runs` needs
it in production, so **move it** to `wl_preproc/schema/detect.py` beside
`_conjunction_runs` and have the test module import it from there. Do not
define a second one: two definitions of one fact is precisely what this
repo's `LabelledInterval = Run` alias comment warns against.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py -k "conjunction_runs or glissade or disagree or one_kind or different_kinds or sorted_by_start" -v`
Expected: FAIL with `ImportError: cannot import name '_conjunction_runs'`

- [ ] **Step 3: Write minimal implementation**

```python
def _conjunction_runs(
    left: list[Run],
    right: list[Run],
    min_duration_samples: int,
    saccadic_label_for: Callable[[int, int], Label],
) -> list[Run]:
    """The binocular criterion, applied WITHIN each kind.

    A conjunction run is the temporal intersection of two runs of the same
    kind, and it carries that kind's label. `saccade` and `microsaccade` are
    one kind, labelled by `saccadic_label_for` on the intersection's own
    interval; every other emitted label is its own kind and labels itself.

    **This is what makes the conjunction trace the same shape as the per-eye
    traces.** `_overlapping` intersects on time alone and never reads a
    label, which is correct only while every emitted label is the same kind
    of thing -- true of Engbert-Kliegl and Otero-Millan, and false for all
    four detectors that emit `pso`. Three of them also emit `fixation`, which
    tiles the recording, so an ungrouped intersection would have crossed a
    left fixation with a right saccade and kept it.

    **Grouping first also makes the loop cheaper.** `_overlapping` is
    `O(|left| x |right|)`; summing that over kinds is strictly less than the
    product of the totals whenever more than one kind is present, and
    identical when only one is -- which is the case for both registered
    detectors, whose rows are therefore unchanged.

    Not folded into `_overlapping`: that function is the single-kind
    primitive and every one of its call sites, in production and in tests,
    passes single-kind input. Keeping the two separate is what lets the H3
    duration-floor tests keep testing the floor rather than the grouping."""
    by_kind: dict[str, tuple[list[Run], list[Run]]] = {}
    for side, runs in ((0, left), (1, right)):
        for run in runs:
            kind = _kind_of(run.label)
            if kind is None:
                continue
            by_kind.setdefault(kind, ([], []))[side].append(run)

    out: list[Run] = []
    for kind, (left_runs, right_runs) in by_kind.items():
        if kind == "saccadic":
            label_for = saccadic_label_for
        else:
            # The kind labels itself. No rule to write, no arbitration, and
            # no convention stated anywhere -- both eyes already agreed.
            label_for = _always(Label(kind))
        out.extend(_overlapping(left_runs, right_runs, min_duration_samples, label_for))

    return sorted(out, key=lambda run: run.start)
```

Move `_always` out of the test module and into `detect.py`, beside
`_conjunction_runs`, keeping its existing docstring and extending it to name
its new production caller:

```python
def _always(label: Label) -> Callable[[int, int], Label]:
    """A `label_for` answering one label whatever the span.

    Two callers. `_conjunction_runs` uses it for every kind that labels
    itself -- one that is not the amplitude split has exactly one label, so
    there is no rule to apply. The duration-floor tests use it where the
    FLOOR is what is under test and which label a surviving span carries is
    not."""
    return lambda _start, _stop: label
```

Then delete the copy at `tests/schema/test_detect_populate.py:1301` and import
this one, leaving the H3 tests otherwise untouched.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py -k "conjunction_runs or glissade or disagree or one_kind or different_kinds or sorted_by_start" -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the whole detect suite — nothing else may move**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py tests/schema/test_detect_schema.py -v`
Expected: PASS, same count as before this task. `_overlapping`'s 16 call
sites are untouched, so any failure here means the primitive changed when it
should not have.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/detect.py tests/schema/test_detect_populate.py
git commit -m "detect: intersect within a kind, so the conjunction keeps the eyes' shape

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RTPFVKnR7yeEYsC1CYuiaH"
```

---

### Task 3: `_conjunction_label` narrows to the saccadic slice

**Files:**
- Modify: `wl_preproc/schema/detect.py:791-953` (`_conjunction_label`, and the comment block above `_AMPLITUDE_DERIVED_VOCABULARY`)
- Test: `tests/schema/test_detect_populate.py`

**Interfaces:**
- Consumes: `_AMPLITUDE_DERIVED_VOCABULARY`, `UndecidedConjunctionLabel`
- Produces: `_conjunction_label(detector, params: dict, gaze) -> Callable[[int, int], Label]` — same signature, new rule

- [ ] **Step 1: Write the failing test**

```python
def test_a_pso_capable_detector_no_longer_raises():
    """The raise that blocked four detectors. It fired on any vocabulary that
    was not a subset of the amplitude split -- because ONE label had to cover
    a mixed vocabulary. Under per-kind intersection each kind labels itself,
    so the mixed case does not arise."""
    import numpy as np
    from dataclasses import replace
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema.detect import _conjunction_label

    nystrom = replace(
        DETECTORS["engbert_kliegl"],
        name="nystrom_holmqvist",
        vocabulary=frozenset({Label.SACCADE, Label.PSO, Label.FIXATION}),
    )
    gaze = np.zeros(1000)
    label_for = _conjunction_label(nystrom, {"microsaccade_max_deg": 1.0}, gaze)

    # `{saccade, pso, fixation}` has a saccadic slice of `{saccade}` alone --
    # a DEGENERATE split, so the constant, never `classify`'s other answer.
    assert label_for(0, 10) is Label.SACCADE


def test_the_saccadic_slice_decides_degeneracy_not_the_whole_vocabulary():
    """Five of the seven detectors take the degenerate branch, and only
    Engbert-Kliegl and Otero-Millan declare BOTH sides of the amplitude cut.
    Testing `len(vocabulary) == 1` -- what stage 1 did -- would send
    Nystrom-Holmqvist's three-label vocabulary to `classify`, which would put
    `microsaccade` in the mouth of a detector that cannot emit it."""
    import numpy as np
    from dataclasses import replace
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema.detect import _conjunction_label

    gaze = np.linspace(0.0, 9.0, 1000)  # a large amplitude on any interval
    bmd = replace(
        DETECTORS["engbert_kliegl"],
        name="bayesian_microsaccade",
        vocabulary=frozenset({Label.MICROSACCADE, Label.DRIFT}),
    )
    label_for = _conjunction_label(bmd, {"microsaccade_max_deg": 1.0}, gaze)

    # Amplitude across this interval is far above the 1.0 deg cut, so
    # `classify` would answer `saccade`. The degenerate branch must not ask.
    assert label_for(0, 999) is Label.MICROSACCADE


def test_the_full_split_still_classifies_by_amplitude():
    """Engbert-Kliegl and Otero-Millan are unchanged, and this is the
    assertion that says so at the unit level."""
    import numpy as np
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema.detect import _conjunction_label

    gaze = np.linspace(0.0, 9.0, 1000)
    label_for = _conjunction_label(
        DETECTORS["engbert_kliegl"], {"microsaccade_max_deg": 1.0}, gaze
    )

    assert label_for(0, 999) is Label.SACCADE       # ~9 deg
    assert label_for(0, 10) is Label.MICROSACCADE   # ~0.08 deg


def test_an_empty_vocabulary_still_raises():
    """Unchanged, and for its own reason: `frozenset() <= anything` is True,
    so a detector declaring nothing passes any subset test while
    `registry.Detector.detect` refuses every label it emits."""
    import numpy as np, pytest
    from dataclasses import replace
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema.detect import UndecidedConjunctionLabel, _conjunction_label

    empty = replace(DETECTORS["engbert_kliegl"], vocabulary=frozenset())
    with pytest.raises(UndecidedConjunctionLabel, match="empty vocabulary"):
        _conjunction_label(empty, {"microsaccade_max_deg": 1.0}, np.zeros(10))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py -k "pso_capable or saccadic_slice or full_split or empty_vocabulary" -v`
Expected: FAIL — `test_a_pso_capable_detector_no_longer_raises` raises
`UndecidedConjunctionLabel`, and `test_the_saccadic_slice_decides_degeneracy`
returns `Label.SACCADE` from `classify`.

- [ ] **Step 3: Write minimal implementation**

In `_conjunction_label`, **delete** the subset raise:

```python
    if not detector.vocabulary <= _AMPLITUDE_DERIVED_VOCABULARY:
        raise UndecidedConjunctionLabel(
            f"detector {detector.name!r} declares vocabulary "
            ...
        )
```

and **replace** the degeneracy test:

```python
    if len(detector.vocabulary) == 1:
        (declared,) = detector.vocabulary
        return lambda _start, _stop: declared
```

with:

```python
    # The SACCADIC SLICE, not the whole vocabulary. Nystrom-Holmqvist
    # declares `{saccade, pso, fixation}` and Bayesian microsaccade detection
    # `{microsaccade, drift}`; neither is size one, and both make only half
    # the amplitude cut. Testing the whole vocabulary -- which is what stage 1
    # did, correctly, when the whole vocabulary WAS the cut -- would send both
    # to `classify` and put a label in each one's mouth that
    # `registry.Detector.detect` refuses from the detector itself.
    #
    # Five of the seven detectors land here and only Engbert-Kliegl and
    # Otero-Millan reach `classify`. The degenerate branch arrived as a
    # fix-round finding about U'n'Eye and is now the majority path.
    saccadic = detector.vocabulary & _AMPLITUDE_DERIVED_VOCABULARY

    if not saccadic:
        # No saccadic label at all, so `_conjunction_runs` builds no saccadic
        # group and never calls this. Returned rather than raised here so the
        # detector's OTHER kinds still produce a conjunction: it is only the
        # amplitude split that has nothing to say.
        def _no_saccadic_label(_start: int, _stop: int) -> Label:
            raise UndecidedConjunctionLabel(
                f"detector {detector.name!r} declares no saccadic label, so it "
                f"can produce no saccadic conjunction run -- and one was asked "
                f"to be labelled anyway, which is a bug in `_conjunction_runs`' "
                f"grouping rather than a question about this detector"
            )

        return _no_saccadic_label

    if len(saccadic) == 1:
        # The degenerate split. Returned from the DECLARATION rather than
        # from any amplitude, so the label is in the detector's vocabulary by
        # construction and not by a check that could be removed.
        (declared,) = saccadic
        return lambda _start, _stop: declared

    microsaccade_max_deg = params["microsaccade_max_deg"]
    ...
```

Then update the comment block above `_AMPLITUDE_DERIVED_VOCABULARY`, which
still describes a subset test that no longer exists. Replace its last
paragraph ("**A PROPER subset is a DEGENERATE split...**") with:

```python
# **The SACCADIC SLICE of a vocabulary decides the label, and a slice of size
# one is a DEGENERATE split.** U'n'Eye (`{saccade}`), Bayesian microsaccade
# detection (`{microsaccade, drift}`) and the three `pso`-capable detectors
# each declare one side of the cut and cannot emit the other, so `classify` --
# which answers both sides for any detector -- would put a word in their
# mouths that `registry.Detector.detect` refuses from the detector itself.
# Five of the seven land there; only Engbert-Kliegl and Otero-Millan declare
# both sides. See `_conjunction_label`, which reads this set rather than
# restating it.
#
# **This set is no longer a gate on whether a conjunction can be built at
# all.** It was, until 2026-09-05: a vocabulary that was not a subset raised,
# because ONE label had to cover a mixed vocabulary. Per-kind intersection
# (`_conjunction_runs`) means each kind labels itself, so the mixed case does
# not arise and there is nothing left to refuse.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py -k "pso_capable or saccadic_slice or full_split or empty_vocabulary" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Find and update the tests that asserted the old raise**

Run: `grep -n "UndecidedConjunctionLabel" tests/ -r`

Any test asserting the subset raise now tests behaviour the design has
removed. **Do not delete it — rewrite it** to assert the new rule, and say in
its docstring what it used to assert and why that changed. A deleted test is
indistinguishable from a test that never existed.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/detect.py tests/schema/test_detect_populate.py
git commit -m "detect: the saccadic slice decides the label, not the whole vocabulary

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RTPFVKnR7yeEYsC1CYuiaH"
```

---

### Task 4: Wire it into `make()`, and prove the shipped detectors did not move

**Files:**
- Modify: `wl_preproc/schema/detect.py:576-580` (the `make()` call site) and `_overlapping`'s docstring
- Test: `tests/schema/test_detect_populate.py`

**Interfaces:**
- Consumes: `_conjunction_runs` (Task 2), `_conjunction_label` (Task 3)
- Produces: no new names. `EyeDetection.make()` now calls `_conjunction_runs`.

- [ ] **Step 1: Write the failing test**

```python
def test_engbert_kliegl_conjunction_rows_are_unchanged(stepped_session):
    """Design spec section 4's bar. Engbert-Kliegl emits ONE kind, so
    grouping partitions into one group and the loop is stage 1's loop.

    Anchored on the THREE planted transitions this fixture exists to plant --
    the same count `test_a_binocular_overlap_below_the_floor_is_not_an_event`
    already asserts at line 1089 -- rather than on a recomputation, which
    would move with the code."""
    from wl_preproc.schema import detect

    session_key, _report = stepped_session

    rows = (
        detect.EyeDetection.Run
        & {**session_key, "trace": "conjunction", **_detector("engbert_kliegl")}
    ).to_dicts(order_by="run_index")
    events = [r for r in rows if r["label"] in ("saccade", "microsaccade")]

    assert len(events) == 3, "the three planted transitions must survive unchanged"
    # No `pso`, `pursuit` or `drift` run can exist for a detector that emits
    # none -- the shape rule cuts both ways.
    assert {r["label"] for r in rows} <= {
        "saccade", "microsaccade", "fixation", "blink", "invalid"
    }
```

**Do not invent a count.** Three is what this fixture plants and what line
1089 already pins; reuse it rather than adding a second constant for one
fact. The reference-recording figures (4,550 intersections, 4,700
Otero-Millan events) come from `WLPP_OHDPI_REFERENCE`-gated tests and are not
available in a default run.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py -k "engbert_kliegl_conjunction_rows_are_unchanged" -v`
Expected: FAIL — `make()` still calls `_overlapping` directly, so nothing is
grouped. (It may PASS by accident, since EK is single-kind; if so, that is
the point of the test and Step 3 must keep it passing.)

- [ ] **Step 3: Change the call site**

At `wl_preproc/schema/detect.py:576`, replace:

```python
            conjunction_spans = _overlapping(
                spans["left"],
                spans["right"],
                _min_duration_samples(detector_params),
                _conjunction_label(detector, params, gaze),
            )
```

with:

```python
            # `_conjunction_runs`, not `_overlapping`: the binocular criterion
            # applies WITHIN a kind, so the conjunction trace carries the same
            # vocabulary as the two eyes it is built from. `_overlapping` is
            # the single-kind primitive underneath it.
            conjunction_spans = _conjunction_runs(
                spans["left"],
                spans["right"],
                _min_duration_samples(detector_params),
                _conjunction_label(detector, params, gaze),
            )
```

Then amend `_overlapping`'s docstring, whose second paragraph currently says
the two eyes' own labels "are never consulted here". That is still true of
this function and no longer true of the conjunction as a whole. Append:

```
    **This function sees ONE kind.** `_conjunction_runs` groups both eyes'
    runs by kind and calls this once per group, so the two eyes' labels ARE
    consulted -- one level up, to decide what intersects with what. Within a
    kind they are not, and `label_for` remains the only source of a label.
```

- [ ] **Step 4: Run the full detect and consensus suites**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py tests/schema/test_detect_schema.py tests/schema/test_consensus_populate.py tests/cli/test_detect_report.py tests/cli/test_consensus_report.py -v`
Expected: PASS, with the same test count as `main` plus this branch's new
tests. Any pre-existing test that changes result is a behaviour change the
spec says must not happen — stop and report rather than updating the test.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/schema/detect.py tests/schema/test_detect_populate.py
git commit -m "detect: make() builds the conjunction per kind

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RTPFVKnR7yeEYsC1CYuiaH"
```

---

### Task 5: A multi-kind detector, end to end through `run_once()`

**Files:**
- Test: `tests/schema/test_detect_populate.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4; `daemon.run_once()`;
  `dataclasses.replace` on a `registry.Detector`, the pattern
  `tests/schema/test_consensus_populate.py:461` already uses
- Produces: no production names. This is the test that proves the four
  blocked detectors are unblocked.

**Why a fixture and not a real detector:** no registered detector emits
anything but saccadic events, so multi-kind behaviour cannot be exercised
against real data until stage 2B writes one. This task is what makes stage 2B
a detector-writing job rather than a design job.

- [ ] **Step 1: Write the failing test**

```python
def test_a_multi_kind_detector_populates_and_keeps_its_vocabulary(stepped_session):
    """The four blocked detectors, in the only form that exists today.

    Registered through `DETECTORS` and driven by `daemon.run_once()`, never
    by `make()` -- stage 1's worst defect was that nothing in production
    registered the detection paramsets, so both `key_source`s named zero
    candidates and the subsystem wrote no rows while every test that
    registered its own passed."""
    from dataclasses import replace
    from wl_preproc import daemon
    from wl_preproc.eye.detect.labels import Label, LabelledInterval
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import detect

    def detect_two_kinds(gaze_deg, velocity_deg_s, available, params):
        """A saccade with a glissade on its tail, in both eyes, at the same
        samples -- the shape section 2.5 says follows EVERY saccade."""
        return [
            LabelledInterval(1000, 1060, Label.SACCADE),
            LabelledInterval(1060, 1090, Label.PSO),
        ]

    fake = replace(
        DETECTORS["engbert_kliegl"],
        name="two_kinds",
        vocabulary=frozenset({Label.SACCADE, Label.PSO, Label.FIXATION}),
        run=detect_two_kinds,
    )
    session_key, _report = stepped_session
    DETECTORS["two_kinds"] = fake
    try:
        daemon.run_once()

        rows = (
            detect.EyeDetection.Run
            & {**session_key, "trace": "conjunction", **_detector("two_kinds")}
        ).to_dicts(order_by="run_index")

        labels = {r["label"] for r in rows}
        # THE shape rule: the conjunction's vocabulary is the detector's.
        assert "pso" in labels, "the binocular glissade was dropped or renamed"
        assert "saccade" in labels

        pso_rows = [r for r in rows if r["label"] == "pso"]
        assert len(pso_rows) == 1
        assert (pso_rows[0]["run_start"], pso_rows[0]["run_stop"]) == (1060, 1090)

        # A `pso` run stores NULL amplitude -- `_run_row` measures only
        # `saccade`/`microsaccade` -- so the 12.3% label/amplitude
        # contradiction stage 1 found is unreachable for it. There is no
        # amplitude to contradict.
        assert pso_rows[0]["amplitude_deg"] is None
        assert pso_rows[0]["peak_velocity_deg_s"] is None

        saccade_rows = [r for r in rows if r["label"] == "saccade"]
        assert saccade_rows[0]["amplitude_deg"] is not None
    finally:
        del DETECTORS["two_kinds"]


def test_a_multi_kind_detector_writes_all_three_traces(stepped_session):
    """`EyeDetection.make()` inserts `left` and `right` BEFORE the conjunction
    branch, and DataJoint's `AutoPopulate._populate1` wraps the whole call in
    a transaction and cancels it on any exception. So the old raise did not
    yield per-eye data awaiting a conjunction -- it yielded NO rows at all,
    silently, with the failure visible only in `run_once`'s error list.

    This asserts the outcome that failure mode denied."""
    from dataclasses import replace
    from wl_preproc import daemon
    from wl_preproc.eye.detect.labels import Label, LabelledInterval
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import detect

    def detect_two_kinds(gaze_deg, velocity_deg_s, available, params):
        return [
            LabelledInterval(1000, 1060, Label.SACCADE),
            LabelledInterval(1060, 1090, Label.PSO),
        ]

    fake = replace(
        DETECTORS["engbert_kliegl"],
        name="two_kinds_traces",
        vocabulary=frozenset({Label.SACCADE, Label.PSO, Label.FIXATION}),
        run=detect_two_kinds,
    )
    session_key, _report = stepped_session
    DETECTORS["two_kinds_traces"] = fake
    try:
        errors = daemon.run_once()

        traces = set(
            (detect.EyeDetection & {**session_key, **_detector("two_kinds_traces")})
            .to_arrays("trace")
        )
        assert traces == {"left", "right", "conjunction"}
        assert not [e for e in errors if "two_kinds_traces" in str(e)]
    finally:
        del DETECTORS["two_kinds_traces"]
```

**Fixture facts, verified rather than assumed.** `stepped_session` is a
module-scoped fixture at `tests/schema/test_detect_populate.py:402` returning
a `(session_key, report)` tuple — unpack it, do not use it as a key.
`_detector(name)` at line 691 is the restriction that selects one detector's
rows. There is no `landed_session`. Registering into `DETECTORS` before
`daemon.run_once()` is what makes the daemon register a paramset for the new
detector; the `try/finally` matters because `DETECTORS` is module state shared
across the whole session and a leaked entry breaks the registry's set-equality
completeness claim in unrelated tests.

**`stepped_session` is module-scoped and already populated**, so a second
`run_once()` inside these tests populates only the newly-registered detector's
keys, leaving the existing rows alone. If that turns out not to hold, give
these two tests their own function-scoped session built the way
`_build_stepped_session` builds one, rather than weakening the assertions.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_detect_populate.py -k "multi_kind" -v`
Expected: FAIL. Before Tasks 1–4 it fails with `UndecidedConjunctionLabel`
and zero rows; with them, it should pass — so if this is run last it verifies
rather than drives. Run it against `git stash`ed changes once to confirm it
genuinely fails on `main`, then unstash.

- [ ] **Step 3: No implementation**

If these fail after Tasks 1–4, the defect is in Tasks 1–4 — fix it there, not
here. This task adds no production code.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS. Compare the count against `main`'s **1284 tests, 5 skipped,
1 deselected, 1 xfailed** (1288 passed with `WLPP_OHDPI_REFERENCE` set, which
flips four gated real-recording tests) — the total should be that plus this
branch's new tests and nothing else.

- [ ] **Step 5: Commit**

```bash
git add tests/schema/test_detect_populate.py
git commit -m "test: a multi-kind detector populates all three traces

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RTPFVKnR7yeEYsC1CYuiaH"
```

---

### Task 6: The documents that still say four detectors are blocked

**Files:**
- Modify: `docs/CHECKPOINT.md` ("What is next", item 4, around line 219)
- Modify: `wl.yaml` (`status.next`, and `status.describes`)
- Modify: `docs/handoffs/2026-09-02-the-conjunction-label-decision.md`
- Create: `docs/handoffs/2026-09-05-conjunction-shape-built.md`

**Interfaces:** none. Documentation only, and it is not optional: CHECKPOINT's
own header records that this file went nine days and three merged subsystems
stale, and CLAUDE.md makes `wl.yaml` this package's declaration to
`wl-orchestrator`, where a wrong line puts wrong software on a real machine.

- [ ] **Step 1: Supersede the handoff rather than deleting it**

Add at the very top of
`docs/handoffs/2026-09-02-the-conjunction-label-decision.md`:

```markdown
> **SUPERSEDED 2026-09-05** by
> `docs/superpowers/specs/2026-09-05-conjunction-shape-design.md`. **None of
> the four conventions below was adopted, and the question was withdrawn
> rather than answered.**
>
> The premise was wrong. This brief argues that a conjunction run's label
> cannot come from a detector because the conjunction is derived. The first
> half is true; the second does not follow. When both eyes independently call
> the same stretch `pso`, their agreement on KIND supplies the label, and the
> conjunction stores `pso` as `pso`.
>
> What actually blocked the four detectors was a defect: `_overlapping`
> intersected every left run against every right run on time alone and never
> read a label, which made the label question look unanswerable.
>
> **What below is still accurate and worth reading:** the account of what a
> glissade is, Deubel & Bridgeman's 0.5 deg against a 1.0 deg microsaccade
> threshold, Nystrom & Holmqvist's finding that the assignment is made
> "largely arbitrarily", and the transaction behaviour that made a blocked
> detector write nothing at all.
```

CHECKPOINT's own rule: a stale routing document is how a decision gets
deferred forever, and this one now points four detectors at a question that
does not exist.

- [ ] **Step 2: Update CHECKPOINT's "What is next" item 4**

It currently reads that four detectors are "blocked on a DECISION, not on
code" and that deciding "unblocks four detectors at once and is the cheapest
item on this list". Replace with what happened: the decision was withdrawn,
the block was a defect in `_overlapping`, and the four are unblocked. Name the
spec, and update the header's `describes` commit.

- [ ] **Step 3: Update `wl.yaml`**

`status.next` currently says four of five detectors "can be registered but
cannot produce a CONJUNCTION trace until the glissade assignment is decided".
That is no longer true. Per CLAUDE.md, `third_party`, `runs_on` and
`builds_on` need **no** change — this adds no dependency and moves no
deployment. Update `status.phase`, `status.next` and `status.describes` only.

- [ ] **Step 4: Validate the manifest**

```bash
pip install git+https://github.com/jakewesterberg/wl-manifest.git && wl-check
```

Expected: passes. `wl-check` reads only this repository's manifest and, once
installed, never touches the network.

- [ ] **Step 5: Write the handoff**

`docs/handoffs/2026-09-05-conjunction-shape-built.md`, covering: what changed
and why the decision was withdrawn; that five of seven detectors take the
degenerate branch and only two reach `classify`; and **the open question that
matters** — how often the two eyes disagree on kind is unmeasured and
unmeasurable until a pso-capable detector exists, so §1's agreement
requirement rests on reasoning rather than a number. Nyström–Holmqvist is
what measures it, and that measurement is the first thing stage 2B should do.

- [ ] **Step 6: Commit**

```bash
git add docs/ wl.yaml
git commit -m "docs: the conjunction decision was withdrawn, not answered

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RTPFVKnR7yeEYsC1CYuiaH"
```

---

## Before opening a PR

- [ ] **Whole-branch review.** Stage 1's nine per-task reviews were all green
  and a whole-branch review then found ten defects, the worst being that
  nothing in production registered the detection paramsets — so both
  `key_source`s named zero candidates, the subsystem wrote no rows, and the
  report rendered an empty pipeline identically to a clean one. This branch
  touches the same seam.
- [ ] **Push and read CI off the run**, both interpreters:
  `git push -u origin spec/conjunction-shape && gh run list --branch spec/conjunction-shape`.
  A green local run is evidence about 3.11 on macOS arm64 and nothing else;
  the eye merge left CI red on 3.13 alone for a day while every local run
  passed.
- [ ] **Confirm no `.pyc` staleness** if any mutation check was used to verify
  a test actually fails: a same-length mutation restored within a second keeps
  running from cache.
