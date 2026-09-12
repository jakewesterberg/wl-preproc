"""Nystrom-Holmqvist against the paper's own numbers -- nulls first.

Design spec `2026-09-05-nystrom-holmqvist-design.md`, section 5 (Table 3) and
section 9. The paper:

    Nystrom, M., & Holmqvist, K. (2010). An adaptive algorithm for fixation,
    saccade, and glissade detection in eyetracking data. Behavior Research
    Methods, 42(1), 188-204. 10.3758/BRM.42.1.188

**The rule that governs this whole file, carried forward from the
Otero-Millan round** (`test_otero_millan_validation.py`'s own module
docstring, and design spec section 5's own closing paragraph):

    an oracle-free statistic is worthless until a null has been run against
    it.

That file shipped a main-sequence bound for one commit on the reasoning that
a log-log velocity/amplitude correlation "is a property of real saccades
that no artefact reproduces" -- then measured that a duration-matched
random-span control AND a detector with both acceptance gates removed both
scored HIGHER than the correct detector. The check was WITHDRAWN as invalid,
not relaxed. So here: the null (`_random_span_null`, `_glissadic_fraction`,
`test_the_null_fails_the_glissade_rate_check`) is written and run FIRST, and
everything below it depends on that test passing.

**What transfers and what does not** (spec section 5). The paper's data are
HUMAN, reading and scene perception, at 1250 Hz on an SMI HiSpeed. This rig
is NHP at 500 Hz on a dual-Purkinje tracker. Fixation and saccade DURATIONS
are behaviour and would not transfer -- neither is checked here. The
GLISSADE statistics (rate, duration) have a mechanistic reason to transfer:
a glissade is lens wobble, a property of the eye and the instrument rather
than the task, and spec section 2.5 argues a DPI should show MORE of it, not
less. Those are the two paper statistics this file checks.

**Do not validate the glissade rate against a synthetic fixture.** Task 6
found that on this repository's own `stepped_session` fixture, this
detector's conjunction carries only `{fixation, saccade}` -- no `pso` at
all -- because that fixture's planted transitions are constant-velocity
ramps with a hard stop and no post-saccadic excursion: a glissade is
impossible there BY CONSTRUCTION. A zero rate on a fixture built without any
wobble to find is the fixture, not the detector, and would look identical to
the velocity-estimator failure spec section 9 item 1 predicts. The rate
check below runs only against the real reference recording.

**Gated on `WLPP_OHDPI_REFERENCE`**, following `test_otero_millan_
validation.py`'s own idiom exactly -- see `_skip_reason()`. Never commit
that file.

**This file lives in `tests/eye/`, so it imports nothing from
`wl_preproc.schema`** -- same constraint `test_otero_millan_validation.py`
records, for the same reason (the 3.13 cross-check runs `tests/eye` and
`tests/contracts` with `--noconftest`, in a venv with no DataJoint). The
conjunction trace is therefore not measured here: every check below reads
`detect_nystrom_holmqvist`'s own per-eye output directly, which is also the
correct thing for the glissade rate specifically -- design spec section 6
requires the KIND-disagreement measurement to come from the per-eye traces,
never the conjunction, since `_insert_trace` paints every un-intersected
sample `fixation` and a rate measured off the stored conjunction could
silently look like zero for a reason that has nothing to do with the
velocity estimator.

**REMoDNaV** (`test_remodnav_finds_a_comparable_number_of_saccades`) is a
`dev`-only, PyPI, MIT-licensed dependency (`pyproject.toml`), never a
runtime dependency and never shipped (parent design spec section 3.2). It is
an oracle, not a specification: it deliberately changed parts of
Nystrom-Holmqvist's own method, so agreement is evidence and disagreement is
not automatically a defect. Its actual Python API (`remodnav.
EyegazeClassifier`, `.preproc`, `.__call__`, the event `label` vocabulary)
was read directly from a DOWNLOADED remodnav 1.1.2 wheel's own `clf.py`
before being called here, not recalled -- NOT from an installed package.
An earlier version of this paragraph said "installed"; remodnav is not
installed in this project's `.venv` (`test_remodnav_finds_a_comparable_
number_of_saccades` skips via `pytest.importorskip`) or in any other
environment this repository's own tooling has checked. Reading the wheel
is the same rule this repository applies to a paper, applied to a
dependency's API surface.
"""

from __future__ import annotations

import os
import time
from collections import Counter, namedtuple

import numpy as np
import pytest

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.nystrom_holmqvist import (
    DEFAULT_NH_PARAMS,
    detect_nystrom_holmqvist,
)

# --------------------------------------------------------------------------
# What the paper reports (design spec section 5, Table 3, reading / scene
# perception), restated here only for the print statements below -- the
# actual asserted bounds are wider bands, not these point values, and the
# reason is stated at each assertion rather than assumed.
# --------------------------------------------------------------------------
PAPER_GLISSADIC_FRACTION_READING = 0.478
PAPER_GLISSADIC_FRACTION_SCENE = 0.591
PAPER_GLISSADE_DURATION_MS_READING = (22.2, 9.8)  # mean, sd
PAPER_GLISSADE_DURATION_MS_SCENE = (25.0, 9.8)  # mean, sd

#: A band, not the paper's point value -- see `test_the_glissade_rate_is_in_
#: the_papers_band`'s own docstring for why.
GLISSADE_RATE_MIN = 0.20
GLISSADE_RATE_MAX = 0.90

#: The low tens of milliseconds -- see `test_the_glissade_duration_is_in_
#: the_low_tens_of_milliseconds`'s own docstring for both failure directions.
GLISSADE_DURATION_MS_MIN = 5.0
GLISSADE_DURATION_MS_MAX = 60.0

#: `test_the_null_fails_the_glissade_rate_check`'s own ceiling: far below the
#: paper's 47.8%, and measured (see that test and this task's report) to sit
#: at 0.008-0.030 across seeds 0-19 with this same construction -- comfortable
#: headroom under 0.10, not a bound tuned to just barely clear it.
NULL_GLISSADE_RATE_CEILING = 0.10

#: `test_the_null_fails_the_kind_agreement_check`'s own FLOOR -- a floor, not
#: a ceiling, because this null runs in the opposite direction from the one
#: above: the finding here is LOW kind-disagreement, so chance has to be
#: measurably HIGH for the finding to mean anything. Chance disagreement is
#: the kind mix's own collision probability, about 0.5 for the 50/50 mix that
#: test plants; 0.25 is headroom under it rather than a bound tuned to clear.
NULL_KIND_DISAGREEMENT_FLOOR = 0.25

#: The conjunction's duration floor for THIS detector, in samples.
#: `schema/detect.py::_min_duration_samples` reads `min_duration_samples` off
#: the detector's own params with `getattr(..., 1)`. That field belongs to
#: `EngbertKlieglParams`; `NystromHolmqvistParams` states its durations in
#: milliseconds and has none -- so Nystrom-Holmqvist's conjunction admits a
#: ONE-sample binocular event where Engbert-Kliegl's requires six. Measured
#: rather than assumed (`_min_duration_samples(DEFAULT_NH_PARAMS)` returns 1),
#: and restated as a literal here because this file imports nothing from
#: `wl_preproc.schema`.
NH_CONJUNCTION_FLOOR_SAMPLES = 1

#: REMoDNaV oracle comparison, restricted to a leading slice of the
#: recording rather than the full ~39 minutes / 1.17M samples -- following
#: REMoDNaV's own test suite's precedent (`remodnav/tests/test_detect.py::
#: test_real_data` slices a ~1000 Hz recording to `p[:50000]` for the same
#: reason): its classifier is a pure-Python, windowed algorithm with no
#: vectorised fast path, and running it end-to-end on this rig's full
#: recording would make this one test dominate the suite's wall clock for a
#: comparison that does not need the whole recording to be meaningful.
REMODNAV_COMPARISON_SAMPLES = 120_000

#: The larger axis's 99th percentile of the raw Purkinje difference, placed
#: here in degrees. Restated from `test_otero_millan_validation.py`, which
#: documents the choice at length, for that file's own stated reason: two
#: independent definitions of one heuristic is how they drift apart.
_SCALE_P99_AT_DEG = 15.0


def _skip_reason() -> str:
    return (
        "WLPP_OHDPI_REFERENCE is not set. Point it at a real OpenIrisDPI "
        "recording's raw .txt file to run these tests -- the reference "
        "recording this repository's design spec measures against is "
        "OpenIris-2024Jul31-114628.txt (633 MB, 1,177,799 rows, ~39.4 "
        "minutes at ~498.55 Hz), from this lab's own "
        "~/Downloads/Tutorial/OpenIris-2024Jul31-114628/ tutorial materials "
        "-- see design spec section 5. Never commit that file."
    )


# --------------------------------------------------------------------------
# Step 1: the null. Written and run FIRST -- see module docstring.
# --------------------------------------------------------------------------

_Span = namedtuple("_Span", "start stop")

#: `_Span` plus the one field the kind-agreement statistic needs. A sibling
#: rather than a third field on `_Span`: the glissade null above builds
#: `_Span`s that have no kind at all, and widening that type would force a
#: placeholder label into a null where the concept does not apply. Field names
#: match `Run`'s (`labels.py`) for the reason `_random_span_null` documents --
#: one statistic function has to read both the real frozen dataclass and the
#: null's stand-ins, and `Run` has no `__getitem__`.
_LabelledSpan = namedtuple("_LabelledSpan", "start stop label")


def _random_span_null(runs, n_samples, rng):
    """A duration-matched random-span control: the same number of spans, with
    the same durations, placed uniformly at random.

    **This exists before the checks below, not after them.** Stage 2A adopted
    a main-sequence statistic because it "is a property of real saccades that
    no artefact reproduces", then measured that a random control AND a
    detector with both acceptance gates removed both scored HIGHER than the
    correct detector. The check was withdrawn as invalid rather than relaxed.
    The rule that episode left: an oracle-free statistic is worthless until a
    null has been run against it, and building the null is cheap.

    **Returns `_Span`s, not the bare `(start, start + duration)` tuples a
    first reading of this function's own name might suggest** -- a
    documented, deliberate choice (task 7 report): `_glissadic_fraction`
    below also has to accept the real `Run`s `detect_nystrom_holmqvist`
    returns, and `Run` (`labels.py`) is a frozen dataclass with `.start`/
    `.stop` attributes and no `__getitem__` at all. A bare 2-tuple supports
    only index access, which would force `_glissadic_fraction` into either
    two incompatible implementations or a runtime `isinstance` branch; a
    namedtuple with the same field names as `Run` lets one function read
    both.
    """
    durations = [run.stop - run.start for run in runs]
    spans = []
    for duration in durations:
        start = int(rng.integers(0, max(n_samples - duration, 1)))
        spans.append(_Span(start, start + duration))
    return sorted(spans)


def _random_labelled_span_null(runs, n_samples, rng):
    """`_random_span_null` preserving each run's KIND as well as its duration.

    **Preserving the kind is what makes this the right null for the question.**
    The measurement asks whether the two eyes agree on kind more often than
    they would by accident. An accident here still has each eye's real
    vocabulary mix in it -- a detector emitting mostly saccades will match
    another mostly-saccade trace often, for no binocular reason at all. A null
    that reassigned kinds would destroy that mix too, and would flatter the
    measurement by comparing it against something no detector could produce.
    Only the temporal relationship between the eyes is destroyed here.
    """
    spans = []
    for run in runs:
        duration = run.stop - run.start
        start = int(rng.integers(0, max(n_samples - duration, 1)))
        spans.append(_LabelledSpan(start, start + duration, run.label))
    return sorted(spans)


def _glissadic_fraction(saccades, glissades, tau_samples):
    """The fraction of `saccades` followed within `tau_samples` by a
    glissade starting at or after that saccade's own offset.

    Stated generically over anything exposing `.start`/`.stop` -- `_Span`s
    from the null above, or real `Run`s `detect_nystrom_holmqvist` returns --
    so the reference-recording checks run the IDENTICAL statistic the null
    is shown to fail, not a second, similar-looking one.

    Half-open, matching `Run`'s own convention: a glissade starting anywhere
    in `[saccade.stop, saccade.stop + tau_samples)` counts as a hit.

    **On real detector output this reduces to `len(glissades) /
    len(saccades)` exactly**, and that is expected rather than a loss of
    precision: `_glissade_bounds` (`nystrom_holmqvist.py`) always sets a
    glissade's own onset to its saccade's offset, one saccade produces at
    most one glissade, and two saccades' spans never overlap -- so every
    stored glissade is a hit for exactly the one saccade it followed, for
    any `tau_samples >= 1`. The generality is what lets the SAME function
    measure a null built from spans with no such relationship at all.
    """
    if not saccades:
        return 0.0
    starts = np.asarray(sorted(glissade.start for glissade in glissades), dtype=np.int64)
    hits = 0
    for saccade in saccades:
        lo, hi = saccade.stop, saccade.stop + tau_samples
        index = int(np.searchsorted(starts, lo, side="left"))
        if index < starts.size and starts[index] < hi:
            hits += 1
    return hits / len(saccades)


def test_the_null_fails_the_glissade_rate_check():
    """If the null passes, the check does not discriminate and must be
    WITHDRAWN, not relaxed. This test is what says the checks below mean
    something."""
    rng = np.random.default_rng(7)
    # A random control has no saccade-glissade adjacency at all, so the
    # fraction of its "saccades" followed within tau_min by a "glissade" is
    # chance-level, far below the paper's 47.8%.
    n_samples = 500_000
    fake_saccades = _random_span_null([_Span(0, 20)] * 500, n_samples, rng)
    fake_glissades = _random_span_null([_Span(0, 12)] * 500, n_samples, rng)

    rate = _glissadic_fraction(fake_saccades, fake_glissades, tau_samples=20)

    assert rate < NULL_GLISSADE_RATE_CEILING, (
        f"a random control scored {rate:.3f} on the glissade-rate check; the "
        "check does not discriminate and must be withdrawn, not relaxed"
    )


#: The conjunction's own kind mapping, RESTATED from `schema/detect.py`
#: (`_KIND_OF`, `_NOT_INTERSECTED`) rather than imported, for this file's
#: standing reason: it imports nothing from `wl_preproc.schema`, because the
#: 3.13 cross-check runs `tests/eye` with `--noconftest` in a venv with no
#: DataJoint (module docstring).
#:
#: **The drift risk is real and is named rather than left implicit.** Two
#: definitions of one rule is how they come apart, and nothing here can catch
#: it: a ninth label, or a remapped kind, would change the conjunction and
#: leave this measurement quietly reporting the old rule's number. The durable
#: fix is to move `_KIND_OF` into `eye/detect/labels.py`, which both sides can
#: import -- it is vocabulary knowledge, not schema knowledge -- and that is
#: recorded as a follow-up rather than done here, being a production change to
#: the conjunction outside this measurement's scope.
_NOT_INTERSECTED = frozenset({Label.FIXATION, Label.BLINK, Label.INVALID})
_KIND_OF = {
    Label.SACCADE: "saccadic",
    Label.MICROSACCADE: "saccadic",
    Label.PSO: Label.PSO.value,
    Label.PURSUIT: Label.PURSUIT.value,
    Label.DRIFT: Label.DRIFT.value,
}


def _kind_of(label):
    """`label`'s conjunction kind, or `None` where the conjunction never
    intersects it. Raises on an unmapped label for the same reason the
    original does -- a ninth label must not vanish silently."""
    if label in _NOT_INTERSECTED:
        return None
    try:
        return _KIND_OF[label]
    except KeyError as exc:
        raise AssertionError(
            f"{label!r} has no conjunction kind in this file's restated "
            "`_KIND_OF`. `schema/detect.py` has probably gained one; this "
            "copy must gain it too"
        ) from exc


class _Agreement:
    """One eye's detected runs, bucketed by what the other eye said about the
    same stretch of time. Three buckets, and every own-run lands in exactly
    one.

    `agree` -- an overlapping run of the same kind.
    `disagree` -- overlapping run(s), none of the same kind.
    `alone` -- no overlapping detected run at all; the other eye calls that
    stretch `fixation`, which `_conjunction_runs` never intersects.

    **Both of the last two are dropped by the binocular agreement rule**
    (conjunction-shape design spec section 1), which is why `drop_rate` adds
    them and `kind_disagreement_rate` does not. The spec's open question 1 is
    literally about the middle bucket; its "conservative or costly" framing
    needs both.
    """

    def __init__(self, agree: int, disagree: int, alone: int):
        self.agree = agree
        self.disagree = disagree
        self.alone = alone

    @property
    def total(self) -> int:
        return self.agree + self.disagree + self.alone

    @property
    def compared(self) -> int:
        """Own-runs the other eye also called an event -- the only ones on
        which a KIND comparison is defined at all."""
        return self.agree + self.disagree

    @property
    def kind_disagreement_rate(self) -> float:
        """Of the stretches where both eyes found something, the fraction
        where they named it differently. Spec section 6 open question 1."""
        return self.disagree / self.compared if self.compared else 0.0

    @property
    def drop_rate(self) -> float:
        """Of this eye's detected runs, the fraction the binocular agreement
        rule discards -- kind disagreements and unmatched runs together."""
        return (self.disagree + self.alone) / self.total if self.total else 0.0


def _kind_agreement(own, other, floor: int) -> _Agreement:
    """Bucket each run in `own` by what `other` says over the same samples.

    `floor` is the conjunction's own duration floor, in samples: an overlap
    shorter than it would not have survived `_overlapping` and so must not
    count here either. **For Nystrom-Holmqvist that floor is 1, not the 6 the
    run-count measurement reports** -- `schema/detect.py::_min_duration_
    samples` reads `min_duration_samples` off the detector's own params with
    `getattr(..., 1)`, and that field belongs to `EngbertKlieglParams`;
    `NystromHolmqvistParams` states its durations in milliseconds and has
    none. Passed in rather than computed here, so this file keeps importing
    nothing from `wl_preproc.schema` (module docstring).

    **KIND, not label.** `saccade` and `microsaccade` are one kind to the
    conjunction (`_KIND_OF`), so a left `saccade` over a right `microsaccade`
    is an AGREEMENT here exactly as it is there; and `fixation`/`blink`/
    `invalid` are dropped from both sides, since the conjunction never
    intersects them. An own-run overlapping only the other eye's `fixation`
    is therefore `alone` -- the other eye detected nothing -- and not a kind
    disagreement, which is the distinction spec section 6 needs kept apart.

    Stated generically over anything exposing `.start`/`.stop`/`.label`, so
    the null and the real traces run the IDENTICAL statistic rather than two
    similar-looking ones -- the same reason `_glissadic_fraction` is generic.

    **`other` is not assumed disjoint.** Real detector output is, but the null
    places spans independently and they can overlap each other, so candidates
    are found by a bounded window on start rather than by a neighbour walk.
    """
    own_runs = [run for run in own if _kind_of(run.label) is not None]
    other_runs = sorted(
        (run for run in other if _kind_of(run.label) is not None),
        key=lambda run: (run.start, run.stop),
    )
    if not own_runs:
        return _Agreement(0, 0, 0)
    if not other_runs:
        return _Agreement(0, 0, len(own_runs))

    starts = np.array([run.start for run in other_runs], dtype=np.int64)
    stops = np.array([run.stop for run in other_runs], dtype=np.int64)
    kinds = [_kind_of(run.label) for run in other_runs]
    widest = int((stops - starts).max())

    agree = disagree = alone = 0
    for run in own_runs:
        own_kind = _kind_of(run.label)
        lo = int(np.searchsorted(starts, run.start - widest, side="left"))
        hi = int(np.searchsorted(starts, run.stop, side="left"))
        same_kind = other_kind = False
        for index in range(lo, hi):
            overlap = min(run.stop, int(stops[index])) - max(
                run.start, int(starts[index])
            )
            if overlap < floor:
                continue
            if kinds[index] == own_kind:
                same_kind = True
                break
            other_kind = True
        if same_kind:
            agree += 1
        elif other_kind:
            disagree += 1
        else:
            alone += 1
    return _Agreement(agree, disagree, alone)


def test_saccade_and_microsaccade_are_one_kind_to_the_agreement_statistic():
    """`_KIND_OF` (`schema/detect.py`) maps both to `"saccadic"`, so the
    conjunction intersects a left `saccade` with a right `microsaccade` and
    labels the result by amplitude. A statistic comparing raw labels would
    score that same stretch a KIND DISAGREEMENT and report a rate the
    conjunction does not have.

    Nystrom-Holmqvist declares `{saccade}` as its saccadic slice and emits no
    `microsaccade` today, so nothing in the reference measurement exercises
    this. It is pinned anyway: the statistic is stated generically over
    labels, and the next detector to register both sides of the amplitude cut
    would otherwise change this number silently.
    """
    counts = _kind_agreement(
        [_LabelledSpan(0, 20, Label.SACCADE)],
        [_LabelledSpan(0, 20, Label.MICROSACCADE)],
        floor=1,
    )

    assert (counts.agree, counts.disagree, counts.alone) == (1, 0, 0)


def test_the_agreement_statistic_ignores_the_labels_the_conjunction_never_intersects():
    """`_NOT_INTERSECTED` is `{fixation, blink, invalid}` (`schema/detect.py`):
    `fixation` is the synthesized background `_insert_trace` paints, and
    `blink`/`invalid` come from the validity mask rather than from any
    detector.

    So an own-run overlapping only the other eye's `fixation` is ALONE, not a
    kind disagreement -- the other eye detected nothing there. Counting it as
    a disagreement would fold "the eyes named it differently" together with
    "one eye saw nothing", which is exactly the distinction spec section 6's
    open question needs kept apart. And a `fixation` run on the OWN side is
    not an own-run at all: it must not reach any denominator.
    """
    over_fixation = _kind_agreement(
        [_LabelledSpan(0, 20, Label.SACCADE)],
        [_LabelledSpan(0, 20, Label.FIXATION)],
        floor=1,
    )
    assert (over_fixation.agree, over_fixation.disagree, over_fixation.alone) == (0, 0, 1)

    own_fixation = _kind_agreement(
        [_LabelledSpan(0, 20, Label.FIXATION), _LabelledSpan(30, 50, Label.BLINK)],
        [_LabelledSpan(0, 20, Label.SACCADE)],
        floor=1,
    )
    assert own_fixation.total == 0, (
        "a fixation/blink/invalid run is not a detected event and must not "
        "reach the denominator of either rate"
    )


def test_an_own_run_the_other_eye_did_not_find_is_alone_not_a_disagreement():
    """The `alone` bucket, reached through the MAIN LOOP rather than through
    the empty-`other` early return.

    **This test exists because the obvious one does not cover it.**
    `test_the_agreement_statistic_ignores_the_labels_the_conjunction_never_
    intersects` also expects `alone`, but its `other` side is a lone
    `fixation` that the kind filter removes entirely, so it returns early and
    never executes the loop's own `alone` branch. A mutation counting
    unmatched runs as kind disagreements survived that test and was caught
    only by this one -- verified by literal source mutation and revert, not
    assumed.

    Keeping the two apart matters for the measurement: folding `alone` into
    `disagree` would inflate the kind-disagreement rate with stretches where
    one eye simply saw nothing, which is the distinction conjunction-shape
    design spec section 6 turns on.
    """
    counts = _kind_agreement(
        [_LabelledSpan(0, 20, Label.SACCADE)],
        [_LabelledSpan(100, 120, Label.SACCADE)],
        floor=1,
    )

    assert (counts.agree, counts.disagree, counts.alone) == (0, 0, 1)
    assert counts.compared == 0
    assert counts.drop_rate == 1.0


def test_an_overlap_shorter_than_the_floor_does_not_count_as_a_counterpart():
    """The duration floor is `_overlapping`'s own (`schema/detect.py`): an
    intersection shorter than it never becomes a conjunction run, so it must
    not count as a counterpart here either.

    Pinned in BOTH directions on one pair of runs -- the same one-sample
    overlap is a counterpart at `floor=1` and not one at `floor=6` -- so the
    floor is shown to do work rather than merely be passed. A mutation
    dropping the floor comparison survived every other test in this file.

    `floor=6` is Engbert-Kliegl's real value and `floor=1` is
    Nystrom-Holmqvist's (`NH_CONJUNCTION_FLOOR_SAMPLES`); both appear here
    because the statistic is generic over the detector.
    """
    own = [_LabelledSpan(0, 20, Label.SACCADE)]
    other = [_LabelledSpan(19, 40, Label.SACCADE)]  # one sample of overlap

    admitted = _kind_agreement(own, other, floor=1)
    assert (admitted.agree, admitted.disagree, admitted.alone) == (1, 0, 0)

    rejected = _kind_agreement(own, other, floor=6)
    assert (rejected.agree, rejected.disagree, rejected.alone) == (0, 0, 1)


def test_the_null_fails_the_kind_agreement_check():
    """Two eyes that share nothing but a kind mix still agree on kind a large
    fraction of the time. This test measures that chance level, so the
    binocular measurement below is read against it rather than against zero.

    **Same rule, opposite direction from the glissade null above.** There, a
    high rate was the finding, so the null had to score LOW. Here the finding
    will be LOW kind-disagreement -- the two eyes naming the same stretch the
    same thing -- so the null has to score HIGH. If randomly placed runs
    disagree about as rarely as the real eyes do, the measurement is
    describing the vocabulary's own kind mix and not binocularity at all, and
    the check must be WITHDRAWN rather than relaxed.

    Chance agreement here is the collision probability of the kind mix: with
    the 50/50 mix planted below, an overlapping run matches kind half the
    time, so chance DISAGREEMENT is about 0.5. The floor asserted is 0.25 --
    comfortable headroom under that, not a bound tuned to just clear it.
    """
    rng = np.random.default_rng(7)
    n_samples = 50_000
    template = [_LabelledSpan(0, 20, Label.SACCADE)] * 500 + [
        _LabelledSpan(0, 12, Label.PSO)
    ] * 500

    left = _random_labelled_span_null(template, n_samples, rng)
    right = _random_labelled_span_null(template, n_samples, rng)

    counts = _kind_agreement(left, right, floor=1)

    assert counts.compared > 0, (
        "no randomly placed run overlapped any other; the null measured "
        "nothing and cannot say whether the check discriminates"
    )
    assert counts.kind_disagreement_rate > NULL_KIND_DISAGREEMENT_FLOOR, (
        f"randomly placed runs disagreed on kind only "
        f"{counts.kind_disagreement_rate:.3f} of the time; chance agreement "
        "is already as good as binocular agreement, so the kind-disagreement "
        "measurement does not discriminate and must be withdrawn, not relaxed"
    )


# --------------------------------------------------------------------------
# Steps 3-5: against the reference recording. Every test below is gated on
# `WLPP_OHDPI_REFERENCE` and depends on the null above having already passed.
# --------------------------------------------------------------------------


def _scaled_affine_map(scale: float):
    """`degrees = scale * raw_px` on both axes, no cross terms, no offset --
    restated from `test_otero_millan_validation.py`, not imported, per that
    file's own stated reason."""
    from wl_preproc.eye.calibration import CalibrationMap, CalibrationModel

    return CalibrationMap(model=CalibrationModel.AFFINE, x=(0.0, scale, 0.0), y=(0.0, 0.0, scale))


def _gaze_velocity_mask(raw_xy, quality, fs_hz, frame_gaps, scale):
    """One eye's gaze at `scale`, its velocity, and its validity mask.
    Restated from `test_otero_millan_validation.py`, not imported."""
    from wl_preproc.eye.calibration import apply_map
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS, validity_labels
    from wl_preproc.eye.detect.velocity import velocity

    gaze = apply_map(_scaled_affine_map(scale), raw_xy)
    v = velocity(gaze, fs_hz)
    return gaze, v, validity_labels(gaze, v, quality, frame_gaps, DEFAULT_VALIDITY_PARAMS).labels


class _Trace:
    """One eye's own runs from `detect_nystrom_holmqvist`, at the reference
    recording's real sampling rate -- everything the checks below need,
    computed once.

    **Per-eye, not the conjunction** -- this module's own docstring states
    why: design spec section 6 requires it, and reading it off the
    conjunction instead would risk measuring `_insert_trace`'s own
    fixation-fill rather than this detector's real output.
    """

    def __init__(self, name: str, runs: list, fs_hz: float):
        self.name = name
        self.runs = runs
        self.fs_hz = fs_hz
        self.saccades = [run for run in runs if run.label == Label.SACCADE]
        self.glissades = [run for run in runs if run.label == Label.PSO]
        # The paper's own tau_min (Table 2), in samples at this recording's
        # rate -- the same window `_glissade_bounds` itself searches after a
        # saccade, so this asks the identical question the detector already
        # answered rather than a looser one.
        self.tau_samples = max(
            int(round(DEFAULT_NH_PARAMS.min_fixation_duration_ms * fs_hz / 1000.0)), 1
        )

    @property
    def glissade_rate(self) -> float:
        return _glissadic_fraction(self.saccades, self.glissades, self.tau_samples)

    @property
    def glissade_durations_ms(self) -> np.ndarray:
        return np.array(
            [(run.stop - run.start) / self.fs_hz * 1000.0 for run in self.glissades]
        )


@pytest.fixture(scope="module")
def reference():
    """The recording, read once, with both eyes run through
    `detect_nystrom_holmqvist`. Module-scoped so the (skipped, in this
    environment) three tests below share one 633 MB read rather than three.

    Mirrors `test_otero_millan_validation.py`'s own `reference` fixture --
    same recording, same restated scale heuristic -- detecting with
    `nystrom_holmqvist` instead, since this file's checks are about
    glissades, which only this detector emits.
    """
    sample = os.environ.get("WLPP_OHDPI_REFERENCE")
    if not sample:
        pytest.skip(_skip_reason())

    from wl_preproc.eye.gaze import purkinje_vector
    from wl_preproc.eye.ohdpi import read_columns, read_ohdpi

    started = time.monotonic()
    recording = read_ohdpi(sample)
    raw = {
        "left": purkinje_vector(sample, "Left"),
        "right": purkinje_vector(sample, "Right"),
    }
    quality = read_columns(sample, ["LeftDataQuality", "RightDataQuality"])
    read_s = time.monotonic() - started

    pooled_x = np.concatenate([np.abs(raw["left"][:, 0]), np.abs(raw["right"][:, 0])])
    pooled_y = np.concatenate([np.abs(raw["left"][:, 1]), np.abs(raw["right"][:, 1])])
    p99_x = float(np.percentile(pooled_x, 99))
    p99_y = float(np.percentile(pooled_y, 99))
    scale = _SCALE_P99_AT_DEG / max(p99_x, p99_y)

    def build(eye_name: str, column: str) -> _Trace:
        gaze, v, mask = _gaze_velocity_mask(
            raw[eye_name], quality[column], recording.fs_hz, recording.frame_gaps, scale
        )
        runs = detect_nystrom_holmqvist(gaze, v, mask, recording.fs_hz, DEFAULT_NH_PARAMS)
        return _Trace(eye_name, runs, recording.fs_hz)

    return {
        "sample": sample,
        "recording": recording,
        "raw": raw,
        "read_s": read_s,
        "scale": scale,
        "p99": (p99_x, p99_y),
        "traces": [build("left", "LeftDataQuality"), build("right", "RightDataQuality")],
    }


@pytest.mark.skipif(
    not os.environ.get("WLPP_OHDPI_REFERENCE"),
    reason="needs the real reference recording",
)
def test_the_glissade_rate_is_in_the_papers_band(reference, capsys):
    """Spec section 5, Table 3: 47.8% of saccades carry a glissade in
    reading, 59.1% in scene perception, as the union of both criteria.

    A BAND, not the point value, and the reason is stated rather than
    assumed: the paper's data are HUMAN, reading and scene perception, at
    1250 Hz on an SMI HiSpeed. This rig is NHP at 500 Hz on a dual-Purkinje
    tracker. What has a mechanistic reason to transfer is the glissade
    statistics -- a glissade is lens wobble, a property of the eye and the
    instrument rather than the task -- and section 2.5 argues a DPI should
    show MORE of it, not less.

    **A rate near zero indicts the velocity estimator first** (spec section
    2, section 9 item 1): the shared five-point differentiator may be
    smoothing ~20 ms wobbles away, which is the one consequence of not using
    the paper's Savitzky-Golay. Do not switch the estimator on any other
    evidence.

    **Adapted from the task brief's own no-argument sketch to this module's
    shared `reference` fixture** (task 7 report) -- the assertion and its
    message are unchanged; only the wiring that avoids re-reading a 633 MB
    file once per test is new, matching `test_otero_millan_validation.py`'s
    own established convention.
    """
    recording = reference["recording"]
    with capsys.disabled():
        print(f"\n  reference recording: {reference['sample']}")
        print(
            f"  {recording.n_frames} frames, {recording.fs_hz:.2f} Hz, "
            f"scale {reference['scale']:.6g} deg/px (pooled p99 raw "
            f"{reference['p99']} px)"
        )
        print(
            f"  glissade rate -- paper: {PAPER_GLISSADIC_FRACTION_READING:.3f} "
            f"reading / {PAPER_GLISSADIC_FRACTION_SCENE:.3f} scene perception "
            f"(spec section 5); band asserted here {GLISSADE_RATE_MIN}-"
            f"{GLISSADE_RATE_MAX}:"
        )
        for trace in reference["traces"]:
            print(
                f"    {trace.name:5s} {len(trace.saccades):6d} saccades, "
                f"{len(trace.glissades):6d} glissades -- rate "
                f"{trace.glissade_rate:.3f}"
            )

    for trace in reference["traces"]:
        rate = trace.glissade_rate
        assert GLISSADE_RATE_MIN <= rate <= GLISSADE_RATE_MAX, (
            f"measured {rate:.3f}; the paper reports 0.478 (reading) and 0.591 "
            "(scene perception). Near zero indicts the velocity estimator "
            "(spec §2); far above 0.9 suggests the offset threshold is too low."
        )


@pytest.mark.skipif(
    not os.environ.get("WLPP_OHDPI_REFERENCE"),
    reason="needs the real reference recording",
)
def test_the_glissade_duration_is_in_the_low_tens_of_milliseconds(reference, capsys):
    """Spec section 5: glissade duration 22.2 +/- 9.8 ms (reading), 25.0 +/-
    9.8 ms (scene perception) -- the low tens of milliseconds. Same gating
    and the same transfer argument as the rate check above: a glissade's
    DURATION, like its rate, is a property of the eye and the tracker
    rather than the task.

    Both failure directions are stated, the way the rate check's is:
    hundreds of milliseconds means glissades are being merged with the
    fixations that follow them; single-digit means they are being truncated
    at the offset threshold.
    """
    with capsys.disabled():
        print(
            f"\n  glissade duration -- paper: "
            f"{PAPER_GLISSADE_DURATION_MS_READING[0]}+/-"
            f"{PAPER_GLISSADE_DURATION_MS_READING[1]} ms reading, "
            f"{PAPER_GLISSADE_DURATION_MS_SCENE[0]}+/-"
            f"{PAPER_GLISSADE_DURATION_MS_SCENE[1]} ms scene perception "
            f"(spec section 5); band asserted here {GLISSADE_DURATION_MS_MIN}-"
            f"{GLISSADE_DURATION_MS_MAX} ms:"
        )
        for trace in reference["traces"]:
            durations = trace.glissade_durations_ms
            if durations.size:
                print(
                    f"    {trace.name:5s} n={durations.size:5d}  "
                    f"mean={durations.mean():.1f} ms  sd={durations.std():.1f} ms"
                )
            else:
                print(f"    {trace.name:5s} no glissades measured")

    for trace in reference["traces"]:
        durations = trace.glissade_durations_ms
        assert durations.size > 0, (
            f"{trace.name}: no glissades measured at all -- the rate check "
            "above is the one that should catch this; seeing it here too "
            "means the population is empty rather than merely out of band"
        )
        mean_ms = float(durations.mean())
        assert GLISSADE_DURATION_MS_MIN <= mean_ms <= GLISSADE_DURATION_MS_MAX, (
            f"{trace.name}: mean glissade duration {mean_ms:.1f} ms outside "
            f"{GLISSADE_DURATION_MS_MIN}-{GLISSADE_DURATION_MS_MAX} ms. Hundreds "
            "of milliseconds means glissades are being merged with the "
            "fixations that follow them; single-digit means they are being "
            "truncated at the offset threshold."
        )


def _remodnav_saccade_count(remodnav_module, raw_xy: np.ndarray, fs_hz: float, px2deg: float) -> int:
    """Run REMoDNaV's own `EyegazeClassifier` on `raw_xy` (raw pixels, the
    same trace `reference`'s own `gaze` is calibrated from) and count its
    saccade-labelled events.

    **Read directly from a DOWNLOADED remodnav 1.1.2 wheel's own
    `remodnav/clf.py` before being written, not recalled and not from an
    installed package** -- this file's own module docstring explains why
    "installed" is wrong (remodnav is not installed anywhere this
    repository's tooling has checked). Design spec section 3.2's rule about
    verifying a source before writing a claim about it applies to a
    dependency's API exactly as it does to a paper.
    `EyegazeClassifier(px2deg, sampling_rate)` takes a scalar, isotropic
    `px2deg` -- exactly what `reference`'s own `scale` already is, since it
    too maps raw pixels to degrees with one factor on both axes
    (`_scaled_affine_map`). `.preproc(data)` wants a structured array with
    `x`/`y` fields in raw pixels and returns one with `vel`/`med_vel` added;
    calling the classifier on that result returns a list of event dicts
    whose `label` is one of `{FIXA, PURS, SACC, ISAC, HPSO, IHPS, LPSO,
    ILPS}` -- `SACC` and `ISAC` are its own two saccade classes (a
    major-pass saccade and one its own intersaccade-refinement pass finds;
    both are "saccades" in its own vocabulary, per its `tests/test_detect.py
    ::test_real_data`, which asserts both labels are present in real data).
    """
    data = np.rec.fromarrays([raw_xy[:, 0], raw_xy[:, 1]], names=["x", "y"])
    classifier = remodnav_module.EyegazeClassifier(px2deg=px2deg, sampling_rate=fs_hz)
    preprocessed = classifier.preproc(data)
    events = classifier(preprocessed)
    return sum(1 for event in events if event["label"] in ("SACC", "ISAC"))


@pytest.mark.skipif(
    not os.environ.get("WLPP_OHDPI_REFERENCE"),
    reason="needs the real reference recording",
)
def test_the_two_eyes_agree_on_kind_far_better_than_chance(reference, capsys):
    """Conjunction-shape design spec `2026-09-05-conjunction-shape-design.md`
    section 6, open question 1 -- "the largest piece of unquantified reasoning
    in this spec", and unmeasurable until a pso-capable detector existed.

    Section 1 keeps a binocular event only where both eyes independently call
    the same stretch the same KIND. Everything else is dropped. This measures
    what that costs, in two numbers that answer two different questions:

    - **kind disagreement** -- of the stretches where BOTH eyes found an
      event, how often they named it differently. The spec's literal
      question.
    - **drop rate** -- of one eye's detected events, the fraction section 1
      discards for any reason: named differently, or not found by the other
      eye at all. The spec's "conservative or costly" framing needs this one,
      because the agreement rule drops both.

    **Measured from the PER-EYE traces, never the conjunction** (spec section
    6, and this module's own docstring). `_insert_trace` paints `fixation`
    over every sample no surviving interval claims, so a disagreement leaves
    no trace of either kind in the conjunction and a query against it would
    report this rate as exactly zero -- not as unmeasured, but as a wrong
    answer that looks like a finding.

    **Read against the null, not against zero.** Two traces sharing only a
    kind mix already agree often by accident;
    `test_the_null_fails_the_kind_agreement_check` measures that chance level
    at 0.376-0.470 across seeds 0-19. The bound asserted here is that same
    null's floor: if the real eyes do not disagree measurably LESS often than
    randomly placed runs, the statistic is describing the vocabulary's kind
    mix rather than binocularity, and it must be withdrawn rather than
    relaxed -- the rule the Otero-Millan round left behind.
    """
    left_trace, right_trace = reference["traces"]
    directions = [
        (
            "left->right",
            _kind_agreement(
                left_trace.runs, right_trace.runs, NH_CONJUNCTION_FLOOR_SAMPLES
            ),
        ),
        (
            "right->left",
            _kind_agreement(
                right_trace.runs, left_trace.runs, NH_CONJUNCTION_FLOOR_SAMPLES
            ),
        ),
    ]

    with capsys.disabled():
        print(
            f"\n  eye KIND disagreement -- conjunction-shape spec section 6 "
            f"open question 1, first measurement. Floor "
            f"{NH_CONJUNCTION_FLOOR_SAMPLES} sample (this detector's own, not "
            f"Engbert-Kliegl's 6); null measures "
            f"{NULL_KIND_DISAGREEMENT_FLOOR}+ by chance:"
        )
        for name, counts in directions:
            print(
                f"    {name}  agree={counts.agree:6d}  disagree="
                f"{counts.disagree:5d}  alone={counts.alone:6d}"
            )
            print(
                f"                 kind disagreement "
                f"{counts.kind_disagreement_rate:.4f} of "
                f"{counts.compared} compared -- drop rate "
                f"{counts.drop_rate:.4f} of {counts.total} detected"
            )
        # Spec section 6 open question 2 -- the row-count effect of a
        # multi-kind detector -- is not a separate measurement: these are the
        # per-eye counts it asks for, printed while they are in hand.
        print("  per-eye runs by label (spec section 6 open question 2):")
        for trace in reference["traces"]:
            tally = Counter(run.label.value for run in trace.runs)
            print(
                f"    {trace.name:5s} {len(trace.runs):6d} runs -- "
                + ", ".join(f"{label} {n}" for label, n in sorted(tally.items()))
            )

    for name, counts in directions:
        assert counts.compared > 0, (
            f"{name}: no detected run in one eye overlapped a detected run in "
            "the other; the statistic measured nothing"
        )
        assert counts.kind_disagreement_rate < NULL_KIND_DISAGREEMENT_FLOOR, (
            f"{name}: the two eyes disagreed on kind "
            f"{counts.kind_disagreement_rate:.4f} of the time, no better than "
            f"the {NULL_KIND_DISAGREEMENT_FLOOR} a random control reaches. "
            "Section 1's agreement requirement then rests on nothing, and "
            "this check must be withdrawn rather than relaxed"
        )


@pytest.mark.skipif(
    not os.environ.get("WLPP_OHDPI_REFERENCE"),
    reason="needs the real reference recording",
)
def test_remodnav_finds_a_comparable_number_of_saccades(reference, capsys):
    """Parent design spec section 3.2 / this spec's section 5: REMoDNaV is
    "a genuine runnable oracle" -- MIT, on PyPI, a `dev`-only dependency,
    never shipped and never a runtime dependency (`pyproject.toml`).

    **It is an oracle, not a specification.** REMoDNaV deliberately changed
    parts of Nystrom-Holmqvist's own method, so this does not compare event
    BOUNDARIES, only that the two find a COMPARABLE NUMBER of saccades on
    the identical raw trace -- within a factor of two. Disagreement beyond
    that is evidence, not automatically a defect; a tighter assertion would
    pin this detector's own output to REMoDNaV's choices, which is exactly
    the confound design spec section 3.2 warns a reimplementation must not
    manufacture.

    Restricted to a leading slice of the recording -- see
    `REMODNAV_COMPARISON_SAMPLES`'s own comment for why -- with this
    detector's own count taken over the SAME slice (saccades wholly
    contained in it), not its full-recording count, so both sides of the
    comparison see the same trace.
    """
    remodnav = pytest.importorskip("remodnav")
    recording = reference["recording"]
    fs_hz = recording.fs_hz
    limit = min(REMODNAV_COMPARISON_SAMPLES, recording.n_frames)

    with capsys.disabled():
        print(
            f"\n  REMoDNaV oracle comparison over the first {limit} samples "
            f"({limit / fs_hz:.0f}s of {recording.n_frames / fs_hz:.0f}s), "
            f"px2deg={reference['scale']:.6g}:"
        )

    for trace in reference["traces"]:
        raw_xy = reference["raw"][trace.name][:limit]
        theirs = _remodnav_saccade_count(remodnav, raw_xy, fs_hz, reference["scale"])
        ours = sum(1 for run in trace.saccades if run.stop <= limit)
        with capsys.disabled():
            print(
                f"    {trace.name:5s} nystrom_holmqvist={ours:5d}  remodnav={theirs:5d}"
            )
        ratio = max(ours, theirs) / max(min(ours, theirs), 1)
        assert ratio <= 2.0, (
            f"{trace.name}: nystrom_holmqvist found {ours} saccades, REMoDNaV "
            f"found {theirs}, over the same {limit}-sample slice -- more than "
            "a factor of two apart. REMoDNaV is an oracle, not a specification "
            "(spec section 5), so this is not automatically a defect, but a "
            "gap this wide is worth looking at before trusting either count."
        )
