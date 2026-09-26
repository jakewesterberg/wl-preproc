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

from wl_preproc.eye.detect.labels import Label, kind_of
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


#: The conjunction's own kind mapping, imported rather than restated: it moved
#: out of `schema/detect.py` into `eye/detect/labels.py` on 2026-09-26, so this
#: file -- which imports nothing from `wl_preproc.schema`, because the 3.13
#: cross-check runs `tests/eye` with no DataJoint -- can use the one definition
#: the conjunction uses. It used to carry its own copy, with nothing able to
#: catch the two drifting apart.
_kind_of = kind_of


class _Agreement:
    """One eye's detected runs, bucketed by what the other eye said about the
    same stretch of time. Three buckets, and every own-run lands in exactly
    one.

    `agree` -- an overlapping run of the same kind.
    `disagree` -- overlapping run(s), none of the same kind.
    `alone` -- no overlapping detected run at all; the other eye calls that
    stretch `fixation`, which `_conjunction_runs` never intersects.

    `agreements` and `disagreements` record WHICH run on the other side each
    own-run matched and `unmatched` records the own-runs with no counterpart
    at all; `pairs` derives the kind breakdown from the second, and
    `dropped_by_kind` the per-kind cost from the second and third together.

    **Both of the last two are dropped by the binocular agreement rule**
    (conjunction-shape design spec section 1), which is why `drop_rate` adds
    them and `kind_disagreement_rate` does not. The spec's open question 1 is
    literally about the middle bucket; its "conservative or costly" framing
    needs both.
    """

    def __init__(
        self,
        agree: int,
        disagree: int,
        alone: int,
        agreements=None,
        disagreements=None,
        unmatched=None,
    ):
        self.agree = agree
        self.disagree = disagree
        self.alone = alone
        #: `(own run, other run)` for each own-run that found a same-kind
        #: counterpart, and for each that found only a different-kind one.
        #: The COUNTS above answer "how often"; these answer "which runs",
        #: which is what a measurement reading an actual event BOUNDARY off
        #: the other eye needs. Both hold the LARGEST-OVERLAP counterpart.
        self.agreements = list(agreements or ())
        self.disagreements = list(disagreements or ())
        #: The own-runs with no counterpart at all -- no pair to record,
        #: since there is no second run. Kept for the same reason as the
        #: other two: `alone` is dropped by section 1's rule exactly as a
        #: kind disagreement is, so a per-kind cost that cannot see this
        #: bucket is not a cost at all.
        self.unmatched = list(unmatched or ())

    def dropped_by_kind(self) -> Counter:
        """Own-runs section 1's agreement rule discards, tallied by KIND and
        counting BOTH reasons it discards one: named differently by the other
        eye, or not found by it at all.

        The two buckets are dropped alike, so attributing only one of them
        answers a question nobody asked. `drop_rate` above already adds them
        for the whole population; this is the same sum per kind.
        """
        tally = Counter(_kind_of(own.label) for own, _other in self.disagreements)
        tally.update(_kind_of(own.label) for own in self.unmatched)
        return tally

    @property
    def pairs(self) -> Counter:
        """The `disagree` bucket by WHICH two kinds disagreed -- a `Counter`
        keyed by the ordered pair `(own kind, other kind)`.

        DERIVED from `disagreements` rather than tallied alongside it, so the
        breakdown cannot drift from the runs it describes; that it partitions
        `disagree` is then structural rather than a coincidence two
        accumulators have to maintain. Ordered, not symmetric: the
        measurement runs both directions and "left said saccadic where right
        said pso" is a different finding from its mirror.
        """
        return Counter(
            (_kind_of(own.label), _kind_of(other.label))
            for own, other in self.disagreements
        )

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
    conjunction (`labels.py::KIND_OF`), so a left `saccade` over a right `microsaccade`
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
        # Every own-run is unmatched, and the list must be populated HERE as
        # well as in the loop below: this early return is a second path to
        # the same bucket, and it is the path that hid a surviving mutation
        # in the 2026-09-12 round.
        return _Agreement(0, 0, len(own_runs), unmatched=own_runs)

    starts = np.array([run.start for run in other_runs], dtype=np.int64)
    stops = np.array([run.stop for run in other_runs], dtype=np.int64)
    kinds = [_kind_of(run.label) for run in other_runs]
    widest = int((stops - starts).max())

    agree = disagree = alone = 0
    agreements: list = []
    disagreements: list = []
    unmatched: list = []
    for run in own_runs:
        own_kind = _kind_of(run.label)
        lo = int(np.searchsorted(starts, run.start - widest, side="left"))
        hi = int(np.searchsorted(starts, run.stop, side="left"))
        # `is not None` stands in for the two booleans this loop used to
        # carry, so `-1` rather than `0`: the first ADMITTED counterpart must
        # take its slot whatever `floor` is, and a `0` start would reclassify
        # a zero-sample counterpart as `alone` under a `floor` of 0. **No
        # test discriminates the two, and that is stated rather than left to
        # look like coverage** -- every caller's floor is at least 1
        # (`_min_duration_samples` defaults to 1), so the case is unreachable
        # today. Mutating either to `0` survives the whole file.
        best_same_overlap, best_same = -1, None
        best_other_overlap, best_other = -1, None
        for index in range(lo, hi):
            overlap = min(run.stop, int(stops[index])) - max(
                run.start, int(starts[index])
            )
            if overlap < floor:
                continue
            # **The same-kind side no longer stops at the first match.** It
            # used to `break`, which was correct while only the COUNT
            # mattered -- an agreement is an agreement whichever run supplies
            # it. It is not correct now that the matched run is READ: an
            # own-run straddling two same-kind counterparts would record a
            # clipped edge instead of its real counterpart, and the offset
            # measurement reads a boundary off exactly that run. Counts are
            # untouched by the change, since `agree` still means "some
            # same-kind counterpart exists".
            #
            # Strictly greater on both, so equal overlaps keep the FIRST --
            # and `other_runs` is sorted by `(start, stop)`, so "first" is a
            # stated order rather than whatever the caller passed in.
            if kinds[index] == own_kind:
                if overlap > best_same_overlap:
                    best_same_overlap, best_same = overlap, other_runs[index]
            elif overlap > best_other_overlap:
                best_other_overlap, best_other = overlap, other_runs[index]
        if best_same is not None:
            agree += 1
            agreements.append((run, best_same))
        elif best_other is not None:
            disagree += 1
            disagreements.append((run, best_other))
        else:
            alone += 1
            unmatched.append(run)
    return _Agreement(agree, disagree, alone, agreements, disagreements, unmatched)


def _kind_mix(runs) -> Counter:
    """`runs` tallied by conjunction KIND, with the labels the conjunction
    never intersects dropped -- the marginal both the chance baseline and the
    per-kind report line are built from, defined once so the two cannot come
    apart."""
    return Counter(k for k in (_kind_of(run.label) for run in runs) if k is not None)


def _expected_pair_shares(own, other) -> dict:
    """What fraction of disagreements each ordered kind pair would take if the
    two eyes' kinds were independent -- the baseline `_kind_agreement`'s
    `pairs` is read against.

    The two marginals with the agreeing diagonal removed and the rest
    renormalised, since a pair only reaches the breakdown when the kinds
    differ. Same kind filter as the statistic itself, so both describe the
    same population.

    Returns `{}` where no pair is reachable -- either eye empty, or both
    carrying only one kind and the same one -- rather than raising: an empty
    baseline is the correct answer to "how would chance divide zero
    disagreements", and the callers below print it beside a `pairs` that is
    empty for the same reason.
    """
    own_mix, other_mix = _kind_mix(own), _kind_mix(other)
    own_total, other_total = sum(own_mix.values()), sum(other_mix.values())
    if not own_total or not other_total:
        return {}
    weights = {
        (a, b): (own_mix[a] / own_total) * (other_mix[b] / other_total)
        for a in own_mix
        for b in other_mix
        if a != b
    }
    denominator = sum(weights.values())
    if not denominator:
        return {}
    return {pair: weight / denominator for pair, weight in weights.items()}


#: `_glissade_offset_differences`' result: the per-event differences, the
#: count of glissades whose own saccade could not be located, and how many
#: counterparts ended INSIDE the glissade, as a per-event MASK over
#: `samples` rather than a tally, so that subpopulation's own distribution
#: can be read. The second is not a diagnostic to
#: be discarded -- it is what says whether the first measured the boundary it
#: claims to. The third separates the hypothesis from its alternative, which
#: share a sign and differ only in magnitude.
_OffsetDifferences = namedtuple("_OffsetDifferences", "samples unpaired within")


def _glissade_offset_differences(own_runs, disagreements) -> _OffsetDifferences:
    """How much later the OTHER eye's saccade ends, for each stretch this eye
    calls a glissade while the other still calls it a saccade.

    Conjunction-shape design spec section 6's mechanism hypothesis, stated
    there and deliberately not asserted: if the two eyes place a saccade's
    OFFSET about a glissade's duration apart, then over those samples one eye
    has already begun its `pso` while the other is still inside its
    `saccade`. This is the quantity that settles it -- centred near one
    glissade duration if the mechanism is right, near the floor if it is not.

    **The own eye's saccade offset is the glissade's own start.**
    `_glissade_bounds` (`eye/detect/nystrom_holmqvist.py`) returns
    `(saccade_offset, stop)`, so a `pso` begins exactly where its saccade
    ended. Rather than assume that, each event looks up an own saccadic run
    ending exactly at the glissade's start; a glissade with none is counted
    `unpaired` and contributes no difference. On this detector's output that
    count should be zero, and printing it is how that stays a checked claim.

    **Only `pso`-over-`saccadic` disagreements are candidates.** The mirror
    is a different event with a different boundary story, and it is not
    counted `unpaired` either -- it was never a candidate.

    **The sign is not evidence.** A counterpart only reaches the disagreement
    bucket by overlapping the glissade, which forces the other eye's saccade
    to end after this one's; every difference is at least 1 whatever the eyes
    did. Only the magnitude means anything, and only against the agreeing
    baseline below.

    **`within` is what separates the hypothesis from its alternative**, since
    the two share that forced sign. A counterpart ending inside the glissade
    is the same saccade's offset placed later -- section 6's proposal. One
    ending well past it is not a boundary disagreement at all: the other eye
    has a longer or merged saccade covering the whole event, a different
    defect with a different fix. Averaging the two populations together
    describes neither, and on this recording the second is a long enough tail
    to pull the mean to twice the median.
    """
    saccade_ends = {}
    for run in own_runs:
        if _kind_of(run.label) == "saccadic":
            saccade_ends.setdefault(run.stop, run)

    samples, unpaired, within = [], 0, []
    for own, other in disagreements:
        if own.label is not Label.PSO or _kind_of(other.label) != "saccadic":
            continue
        own_saccade = saccade_ends.get(own.start)
        if own_saccade is None:
            unpaired += 1
            continue
        samples.append(int(other.stop) - int(own_saccade.stop))
        # `<=`, not `<`: a counterpart ending exactly where the glissade ends
        # covered it and stopped, which is still one saccade's offset placed
        # later rather than an unrelated longer saccade.
        within.append(int(other.stop) <= int(own.stop))
    return _OffsetDifferences(
        np.array(samples, dtype=np.int64),
        unpaired,
        np.array(within, dtype=bool),
    )


def _agreeing_saccade_offset_differences(agreements) -> np.ndarray:
    """The same offset difference, on saccades the two eyes DO agree about --
    ordinary binocular boundary jitter, and what the glissade number above is
    read against.

    Without it the glissade figure is uninterpretable: "the other eye ends N
    samples later" means one thing if agreeing saccades differ by 1 and
    another if they differ by N-1. Unsigned differences are returned as
    measured; the caller takes magnitudes, because here -- unlike above --
    both signs are reachable and the spread is the point.
    """
    return np.array(
        [
            int(other.stop) - int(own.stop)
            for own, other in agreements
            if _kind_of(own.label) == "saccadic"
        ],
        dtype=np.int64,
    )


def test_saccade_and_microsaccade_are_one_kind_to_the_agreement_statistic():
    """`KIND_OF` (`eye/detect/labels.py`) maps both to `"saccadic"`, so the
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
    """`NOT_INTERSECTED` is `{fixation, blink, invalid}` (`eye/detect/labels.py`):
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


def test_a_kind_disagreement_records_which_two_kinds_disagreed():
    """The headline rate says HOW OFTEN the eyes name a stretch differently;
    it does not say WHICH names. Conjunction-shape design spec section 6's
    open question 1 is answered by the rate, but what to DO about the cost it
    measures depends entirely on the pair: `saccadic` against `pso` is the
    two eyes placing one glissade boundary differently, which is a tolerance
    question, while `saccadic` against `pursuit` would be the two eyes
    disagreeing about what the animal did, which is not.

    The breakdown is a `Counter` keyed by the ordered pair, so it is read
    directly against `disagree` -- see
    `test_the_pair_breakdown_accounts_for_every_disagreement`.
    """
    counts = _kind_agreement(
        [_LabelledSpan(0, 20, Label.SACCADE)],
        [_LabelledSpan(0, 20, Label.PSO)],
        floor=1,
    )

    assert counts.disagree == 1
    assert counts.pairs == Counter({("saccadic", Label.PSO.value): 1})


def test_the_disagreeing_pair_names_the_own_eyes_kind_first():
    """The pair is ORDERED, own kind first, and the two directions are read
    separately because they are different questions: "left called it a
    saccade where right called it a glissade" is not the same finding as its
    mirror, and the measurement below runs `_kind_agreement` both ways
    precisely so the asymmetry is visible.

    A key built with `frozenset` or `tuple(sorted(...))` would pass every
    other test in this file and silently merge the two directions into one
    number.
    """
    left_first = _kind_agreement(
        [_LabelledSpan(0, 20, Label.SACCADE)],
        [_LabelledSpan(0, 20, Label.PSO)],
        floor=1,
    )
    right_first = _kind_agreement(
        [_LabelledSpan(0, 20, Label.PSO)],
        [_LabelledSpan(0, 20, Label.SACCADE)],
        floor=1,
    )

    assert list(left_first.pairs) == [("saccadic", Label.PSO.value)]
    assert list(right_first.pairs) == [(Label.PSO.value, "saccadic")]


def test_an_agreement_and_an_unmatched_run_contribute_no_pair():
    """Only the `disagree` bucket has a pair to report. An agreement has one
    kind and no disagreement; an `alone` run has no counterpart at all, so
    there is no second kind to name -- recording either would put runs into
    the breakdown that section 1 does not drop for a naming reason, and the
    breakdown would stop summing to `disagree`.
    """
    agreed = _kind_agreement(
        [_LabelledSpan(0, 20, Label.SACCADE)],
        [_LabelledSpan(0, 20, Label.SACCADE)],
        floor=1,
    )
    assert (agreed.agree, agreed.disagree) == (1, 0)
    assert agreed.pairs == Counter()

    unmatched = _kind_agreement(
        [_LabelledSpan(0, 20, Label.SACCADE)],
        [_LabelledSpan(100, 120, Label.SACCADE)],
        floor=1,
    )
    assert (unmatched.alone, unmatched.disagree) == (1, 0)
    assert unmatched.pairs == Counter()


def test_a_run_disagreeing_with_two_kinds_is_attributed_to_the_larger_overlap():
    """One own-run can overlap several counterparts of different kinds, and
    it is still exactly ONE disagreement -- `disagree` counts own-runs, not
    counterparts, so the breakdown must too or it stops summing to it.

    Which of the two names it: the counterpart sharing the most samples. The
    alternative -- first by start time -- would attribute this run to a
    10-sample `pso` clipping its leading edge over a 70-sample `pursuit`
    covering most of it, which is the wrong one of the two to report and the
    one an unsorted scan happens to reach first.
    """
    counts = _kind_agreement(
        [_LabelledSpan(0, 100, Label.SACCADE)],
        [
            _LabelledSpan(0, 10, Label.PSO),  # 10 samples of overlap
            _LabelledSpan(20, 90, Label.PURSUIT),  # 70 samples of overlap
        ],
        floor=1,
    )

    assert counts.disagree == 1, "one own-run is one disagreement, not two"
    assert counts.pairs == Counter({("saccadic", Label.PURSUIT.value): 1})


def test_two_counterparts_tied_on_overlap_are_broken_by_start_order():
    """`best_overlap` is taken with a STRICT comparison, so two counterparts
    sharing the same number of samples keep the earlier one -- and
    `_kind_agreement` sorts `other` by `(start, stop)` first, so "earlier"
    is a rule rather than whatever order the caller happened to build its
    list in.

    Pinned because the rule is otherwise invisible: relaxing the comparison
    to `>=` changes which kind this run is attributed to and passes every
    other test in this file. A tie is not exotic here -- an overlap is a
    count of samples, runs are tens of samples long at this rate, and two
    small integers land equal often enough that the rule has to be stated
    rather than left to the iteration order.
    """
    counts = _kind_agreement(
        [_LabelledSpan(0, 100, Label.SACCADE)],
        [
            _LabelledSpan(0, 50, Label.PSO),  # 50 samples, starts first
            _LabelledSpan(50, 100, Label.PURSUIT),  # 50 samples, starts second
        ],
        floor=1,
    )

    assert counts.disagree == 1
    assert counts.pairs == Counter({("saccadic", Label.PSO.value): 1})


def test_the_pair_breakdown_accounts_for_every_disagreement():
    """`sum(pairs.values()) == disagree`, over a population large enough that
    the multi-counterpart and floor paths both fire.

    This is the invariant that lets the breakdown be read beside the headline
    rate: if it holds, the pairs are a PARTITION of the disagreements and
    their fractions are fractions of `disagree`. If it does not, the two
    numbers in the report describe different populations and neither can be
    quoted against the other. Run against the same null generator the chance
    level uses, so the population has three kinds, real overlaps and real
    ties rather than a hand-built pair.
    """
    rng = np.random.default_rng(11)
    n_samples = 50_000
    template = (
        [_LabelledSpan(0, 20, Label.SACCADE)] * 400
        + [_LabelledSpan(0, 12, Label.PSO)] * 400
        + [_LabelledSpan(0, 60, Label.PURSUIT)] * 200
    )

    left = _random_labelled_span_null(template, n_samples, rng)
    right = _random_labelled_span_null(template, n_samples, rng)

    counts = _kind_agreement(left, right, floor=1)

    assert counts.disagree > 0, "the null produced no disagreement to partition"
    assert sum(counts.pairs.values()) == counts.disagree, (
        f"the breakdown accounts for {sum(counts.pairs.values())} of "
        f"{counts.disagree} disagreements; the pairs are not a partition of "
        "them and their fractions cannot be read against the headline rate"
    )


def test_each_bucket_records_the_counterpart_it_matched():
    """`agree` and `disagree` are counts; the OFFSET measurement needs to know
    WHICH run on the other side each own-run was matched to, so both buckets
    record the pair rather than only tallying it.

    Both record the LARGEST-OVERLAP counterpart, the agreeing bucket included.
    Pinned here on an own-run straddling two same-kind counterparts: a scan
    that stopped at the first match would record the 10-sample one over the
    70-sample one, and the offset measurement built on it would then be
    reading a clipped edge rather than the real counterpart.
    """
    own = _LabelledSpan(0, 100, Label.SACCADE)
    small, large = (
        _LabelledSpan(0, 10, Label.SACCADE),
        _LabelledSpan(20, 90, Label.SACCADE),
    )

    counts = _kind_agreement([own], [small, large], floor=1)

    assert counts.agree == 1
    assert counts.agreements == [(own, large)]
    assert counts.disagreements == []


def test_a_disagreement_records_both_runs_not_just_their_kinds():
    """The pair of KINDS answers "which kinds disagree"; the pair of RUNS is
    what the saccade-offset measurement needs, since it has to read the other
    eye's saccade boundary off the actual run.
    """
    own = _LabelledSpan(30, 40, Label.PSO)
    other = _LabelledSpan(0, 45, Label.SACCADE)

    counts = _kind_agreement([own], [other], floor=1)

    assert counts.disagreements == [(own, other)]
    assert counts.pairs == Counter({(Label.PSO.value, "saccadic"): 1})


def test_every_bucket_records_exactly_as_many_pairs_as_it_counts():
    """`len(agreements) == agree` and `len(disagreements) == disagree`, over a
    population large enough that the multi-counterpart path fires.

    The same partition argument the pair breakdown already rests on, now for
    the recorded runs: if either list drifts from its count, every statistic
    built on it is describing a different population from the one the
    headline rate reports.
    """
    rng = np.random.default_rng(13)
    template = (
        [_LabelledSpan(0, 20, Label.SACCADE)] * 400
        + [_LabelledSpan(0, 12, Label.PSO)] * 400
        + [_LabelledSpan(0, 60, Label.PURSUIT)] * 200
    )
    left = _random_labelled_span_null(template, 50_000, rng)
    right = _random_labelled_span_null(template, 50_000, rng)

    counts = _kind_agreement(left, right, floor=1)

    assert counts.agree > 0 and counts.disagree > 0
    assert len(counts.agreements) == counts.agree
    assert len(counts.disagreements) == counts.disagree


def test_the_glissade_offset_difference_is_measured_from_the_two_saccade_ends():
    """The quantity conjunction-shape spec section 6's mechanism hypothesis
    turns on: when this eye has begun a glissade and the other is still
    inside its saccade, HOW MUCH LATER does the other eye's saccade end?

    Measured as `other saccade's stop - own saccade's stop`, and the own
    saccade's stop is the glissade's own start: `_glissade_bounds` returns
    `(saccade_offset, stop)`, so a `pso` begins exactly where its saccade
    ended. That is a property of the detector rather than an assumption of
    this statistic, so it is CHECKED per event rather than trusted -- see
    `test_a_glissade_not_adjacent_to_an_own_saccade_is_counted_unpaired`.

    Hand-derived: the own saccade ends at 30, the other eye's at 45, so the
    other eye is still saccading 15 samples into this eye's glissade.
    """
    own = [_LabelledSpan(0, 30, Label.SACCADE), _LabelledSpan(30, 40, Label.PSO)]
    other = [_LabelledSpan(0, 45, Label.SACCADE)]

    counts = _kind_agreement(own, other, floor=1)
    measured = _glissade_offset_differences(own, counts.disagreements)

    assert list(measured.samples) == [15]
    assert measured.unpaired == 0


def test_a_glissade_not_adjacent_to_an_own_saccade_is_counted_unpaired():
    """`pso.start == saccade.stop` holds by construction in this detector,
    and the statistic verifies it per event instead of assuming it. A
    glissade with no own saccade ending exactly at its start contributes NO
    difference and is counted separately.

    Counted rather than skipped silently, and counted rather than raising:
    the number is itself evidence. Zero unpaired on the real recording is
    what says the construction held across every measured event; a nonzero
    count would mean this statistic is reading a boundary that is not the
    one it claims to read, and the measurement below prints it for exactly
    that reason.
    """
    own = [_LabelledSpan(0, 25, Label.SACCADE), _LabelledSpan(30, 40, Label.PSO)]
    other = [_LabelledSpan(0, 45, Label.SACCADE)]

    counts = _kind_agreement(own, other, floor=1)
    measured = _glissade_offset_differences(own, counts.disagreements)

    assert counts.disagree == 1, "the glissade still disagrees; only its pairing fails"
    assert list(measured.samples) == []
    assert measured.unpaired == 1


def test_only_a_glissade_over_the_other_eyes_saccade_is_measured():
    """The mirror disagreement -- this eye calling a saccade what the other
    calls a glissade -- is a different event with a different boundary story
    and is not this measurement's subject. It contributes nothing, and it is
    not counted `unpaired` either: it was never a candidate.
    """
    own = [_LabelledSpan(0, 30, Label.SACCADE)]
    other = [_LabelledSpan(0, 30, Label.PSO)]

    counts = _kind_agreement(own, other, floor=1)
    measured = _glissade_offset_differences(own, counts.disagreements)

    assert counts.pairs == Counter({("saccadic", Label.PSO.value): 1})
    assert list(measured.samples) == []
    assert measured.unpaired == 0


def test_a_counterpart_ending_inside_the_glissade_is_counted_separately():
    """The mechanism hypothesis and its alternative produce the same sign and
    are told apart only by MAGNITUDE, so the two are counted apart.

    If the other eye merely placed the same saccade's offset a little later,
    its saccade ends INSIDE this eye's glissade -- a boundary disagreement
    about one event, which is what section 6 proposes. If it ends well past
    the glissade, the two eyes are not disagreeing about a boundary at all;
    the other eye has a longer or merged saccade covering the whole thing,
    which is a different defect with a different fix. The mean of the two
    populations together is a number describing neither.

    Both cases below overlap the glissade identically at the floor, so only
    the counting rule can tell them apart.
    """
    own = [_LabelledSpan(0, 30, Label.SACCADE), _LabelledSpan(30, 40, Label.PSO)]

    inside = _glissade_offset_differences(
        own, _kind_agreement(own, [_LabelledSpan(0, 35, Label.SACCADE)], floor=1).disagreements
    )
    assert (list(inside.samples), list(inside.within)) == ([5], [True])

    beyond = _glissade_offset_differences(
        own, _kind_agreement(own, [_LabelledSpan(0, 60, Label.SACCADE)], floor=1).disagreements
    )
    assert (list(beyond.samples), list(beyond.within)) == ([30], [False])


def test_a_counterpart_ending_exactly_at_the_glissade_end_counts_as_inside():
    """The boundary of the boundary rule, pinned because `<=` and `<` are
    both defensible-looking here and they disagree on exactly this event: the
    other eye's saccade ends where this eye's glissade ends, having covered
    it entirely and stopped. That is still one saccade's offset placed later
    rather than an unrelated longer saccade, so it counts as inside.
    """
    own = [_LabelledSpan(0, 30, Label.SACCADE), _LabelledSpan(30, 40, Label.PSO)]
    other = [_LabelledSpan(0, 40, Label.SACCADE)]

    measured = _glissade_offset_differences(
        own, _kind_agreement(own, other, floor=1).disagreements
    )

    assert (list(measured.samples), list(measured.within)) == ([10], [True])


def test_a_glissade_disagreeing_with_a_non_saccadic_counterpart_is_not_measured():
    """The measurement is `pso`-over-`saccadic` specifically, not
    `pso`-over-anything. A glissade the other eye calls a PURSUIT is still a
    kind disagreement and still dropped by section 1, but the difference
    between a glissade's start and a pursuit's end is not a saccade-offset
    difference and means nothing in this statistic's terms.

    **Not reachable with Nystrom-Holmqvist, which emits no `pursuit`, and
    pinned anyway** -- the statistic is stated generically over kinds, and a
    mutation dropping this half of the filter survived every other test in
    this file. The detector that registers `pursuit` would otherwise silently
    fold those events into this number.
    """
    own = [_LabelledSpan(0, 30, Label.SACCADE), _LabelledSpan(30, 40, Label.PSO)]
    other = [_LabelledSpan(0, 45, Label.PURSUIT)]

    counts = _kind_agreement(own, other, floor=1)
    measured = _glissade_offset_differences(own, counts.disagreements)

    assert counts.disagree == 2, "both own runs disagree with the pursuit"
    assert list(measured.samples) == []
    assert measured.unpaired == 0, (
        "the glissade was never a candidate, so it is not an unpaired one"
    )


def test_the_baseline_excludes_agreements_that_are_not_saccades():
    """The baseline is binocular jitter on SACCADE boundaries, because that
    is the quantity the glissade figure is a version of. Two eyes agreeing on
    a glissade is a different event with a different boundary, and letting it
    into the baseline would mix the control with the thing being controlled
    for -- glissade boundaries are exactly what is under suspicion.

    A mutation dropping this filter survived every other test in this file.
    """
    own = [_LabelledSpan(0, 30, Label.SACCADE), _LabelledSpan(50, 70, Label.PSO)]
    other = [_LabelledSpan(0, 34, Label.SACCADE), _LabelledSpan(50, 70, Label.PSO)]

    counts = _kind_agreement(own, other, floor=1)

    assert counts.agree == 2, "both own runs found a same-kind counterpart"
    assert list(_agreeing_saccade_offset_differences(counts.agreements)) == [4], (
        "only the saccade pair belongs to the baseline"
    )


def test_the_difference_is_positive_by_construction_so_only_its_size_is_evidence():
    """**The sign of this quantity carries no information and must not be
    read as if it did.** A counterpart only reaches the disagreement bucket
    by overlapping the glissade for at least `floor` samples, which forces
    the other eye's saccade to end AFTER the own eye's -- so every
    difference is at least 1, whatever the two eyes actually did. "The other
    eye ends later" is therefore not a finding; only HOW MUCH later is.

    Pinned at the floor itself: the other eye's saccade ends one sample past
    this eye's, overlapping the glissade by exactly the one sample that
    admits it, and the measurement bottoms out at 1 rather than at 0.
    """
    own = [_LabelledSpan(0, 30, Label.SACCADE), _LabelledSpan(30, 40, Label.PSO)]
    other = [_LabelledSpan(0, 31, Label.SACCADE)]

    measured = _glissade_offset_differences(
        own, _kind_agreement(own, other, floor=1).disagreements
    )

    assert list(measured.samples) == [1]


def test_the_baseline_is_the_same_difference_on_saccades_the_eyes_agree_about():
    """What the glissade number is read against: the offset difference the
    two eyes show on saccades they DO agree about. That is ordinary binocular
    boundary jitter, and it is the only honest comparison -- "15 samples"
    means nothing until it is known whether agreeing saccades differ by 1 or
    by 14.

    Same quantity, `other.stop - own.stop`, on the agreeing bucket, where no
    adjacency lookup is needed because both runs ARE the saccades.
    """
    own = [_LabelledSpan(0, 30, Label.SACCADE)]
    other = [_LabelledSpan(0, 34, Label.SACCADE)]

    counts = _kind_agreement(own, other, floor=1)

    assert counts.agree == 1
    assert list(_agreeing_saccade_offset_differences(counts.agreements)) == [4]


def test_an_unmatched_run_is_recorded_not_only_counted():
    """`alone` is a count; which KIND each unmatched run was is what says who
    pays for it.

    This matters more than the tally suggests. The binocular agreement rule
    (conjunction-shape spec section 1) drops `alone` runs exactly as it drops
    kind disagreements, so a per-kind cost that counts only disagreements
    understates the real one -- and on this recording the disagreements are
    almost entirely `pso`, which would leave `saccadic`'s true cost looking
    like 0.2% when the `alone` bucket might hold most of it.
    """
    own = _LabelledSpan(0, 20, Label.SACCADE)

    counts = _kind_agreement([own], [_LabelledSpan(100, 120, Label.SACCADE)], floor=1)

    assert counts.alone == 1
    assert counts.unmatched == [own]


def test_an_unmatched_run_is_recorded_on_the_early_return_path_too():
    """The same, reached through the empty-`other` EARLY RETURN rather than
    through the main loop.

    **This test exists because this file has already been caught by exactly
    this gap.** The 2026-09-12 round found that a mutation of the `alone`
    branch survived, because the only test expecting `alone` had an `other`
    side that filtered to empty and so never executed the loop at all. The
    two paths both produce `alone` and are written separately, so they are
    tested separately.
    """
    own = _LabelledSpan(0, 20, Label.SACCADE)

    no_other = _kind_agreement([own], [], floor=1)
    assert (no_other.alone, no_other.unmatched) == (1, [own])

    only_fixation = _kind_agreement([own], [_LabelledSpan(0, 20, Label.FIXATION)], floor=1)
    assert (only_fixation.alone, only_fixation.unmatched) == (1, [own]), (
        "a counterpart the conjunction never intersects filters to empty, "
        "which reaches the same early return"
    )


def test_the_unmatched_list_accounts_for_every_alone_run():
    """`len(unmatched) == alone`, over a population large enough that both
    the main loop and the floor path fire.

    The third of the three partition identities this statistic rests on --
    `pairs` partitions `disagree`, `agreements` partitions `agree`, and this
    one partitions `alone`. Together they are what lets a per-kind cost be
    read against the headline drop rate rather than merely printed beside it.
    """
    rng = np.random.default_rng(17)
    template = (
        [_LabelledSpan(0, 20, Label.SACCADE)] * 400
        + [_LabelledSpan(0, 12, Label.PSO)] * 400
    )
    left = _random_labelled_span_null(template, 200_000, rng)
    right = _random_labelled_span_null(template, 200_000, rng)

    counts = _kind_agreement(left, right, floor=1)

    assert counts.alone > 0, "the null produced no unmatched run to partition"
    assert len(counts.unmatched) == counts.alone


def test_the_dropped_tally_counts_both_reasons_the_rule_discards_a_run():
    """What section 1 actually costs each kind: kind disagreements AND
    unmatched runs, which the rule discards alike.

    Counting only disagreements is the error this whole branch exists to
    correct -- it is what made `saccadic` look like it paid 0.2% while
    `pso` paid 35%, when the two buckets are dropped by the same rule and
    only one of them had been attributed.

    Hand-derived: the saccade disagrees (the other eye calls it a glissade)
    and the glissade is unmatched (the other eye found nothing there), so
    each kind is charged exactly one.
    """
    own = [_LabelledSpan(0, 30, Label.SACCADE), _LabelledSpan(60, 70, Label.PSO)]
    other = [_LabelledSpan(0, 30, Label.PSO)]

    counts = _kind_agreement(own, other, floor=1)

    assert (counts.disagree, counts.alone) == (1, 1)
    assert counts.dropped_by_kind() == Counter({"saccadic": 1, Label.PSO.value: 1})


def test_the_expected_pair_shares_are_the_product_of_the_two_kind_mixes():
    """What the breakdown must be read against.

    **A dominant pair is not automatically a finding.** If one eye's runs are
    mostly `saccadic` and the other's carry plenty of `pso`, then
    `saccadic`-over-`pso` is the most common disagreement for a reason that
    has nothing to do with binocularity: it is the most common way to draw
    two different kinds out of those two mixes. This is the same rule the
    headline rate already obeys -- read against chance, not against zero --
    applied to the breakdown, and the reason the Otero-Millan round's
    main-sequence check was withdrawn rather than relaxed.

    Chance share of the ordered pair `(a, b)`, given a disagreement, is

        p(a) * q(b) / sum over every a' != b' of p(a') * q(b')

    where `p` is the OWN eye's kind mix and `q` the OTHER eye's -- the same
    two marginals, with the agreeing diagonal removed, since a pair only
    reaches the breakdown when the kinds differ.

    Expectations below are hand-derived from a deliberately asymmetric pair
    of mixes, not computed by the helper: own is half `saccadic` and half
    `pso`, other is one quarter `saccadic` and three quarters `pursuit`, so
    the three reachable pairs weigh .5*.75, .5*.25 and .5*.75 -- 3/7, 1/7
    and 3/7 of a .875 total.
    """
    own = [_LabelledSpan(0, 1, Label.SACCADE)] * 2 + [_LabelledSpan(0, 1, Label.PSO)] * 2
    other = [_LabelledSpan(0, 1, Label.SACCADE)] + [
        _LabelledSpan(0, 1, Label.PURSUIT)
    ] * 3

    shares = _expected_pair_shares(own, other)

    assert shares == pytest.approx(
        {
            ("saccadic", Label.PURSUIT.value): 3 / 7,
            (Label.PSO.value, "saccadic"): 1 / 7,
            (Label.PSO.value, Label.PURSUIT.value): 3 / 7,
        }
    )


def test_the_expected_pair_shares_ignore_the_labels_the_conjunction_drops():
    """`fixation`/`blink`/`invalid` are not detected events and never reach
    the conjunction (`labels.py::NOT_INTERSECTED`), so they must not enter either
    marginal -- counting them would shrink every real kind's share and make
    the observed breakdown look inflated against chance across the board.

    Own here is half `fixation` by count and the expectation is unchanged
    from the test above's `saccadic`/`pso` half.
    """
    own = (
        [_LabelledSpan(0, 1, Label.SACCADE)] * 2
        + [_LabelledSpan(0, 1, Label.PSO)] * 2
        + [_LabelledSpan(0, 1, Label.FIXATION)] * 4
    )
    other = [_LabelledSpan(0, 1, Label.SACCADE)] + [
        _LabelledSpan(0, 1, Label.PURSUIT)
    ] * 3

    shares = _expected_pair_shares(own, other)

    assert shares == pytest.approx(
        {
            ("saccadic", Label.PURSUIT.value): 3 / 7,
            (Label.PSO.value, "saccadic"): 1 / 7,
            (Label.PSO.value, Label.PURSUIT.value): 3 / 7,
        }
    )


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


@pytest.mark.skipif(
    not os.environ.get("WLPP_OHDPI_REFERENCE"),
    reason="needs the real reference recording",
)
def test_how_far_apart_the_two_eyes_place_a_saccades_offset(reference, capsys):
    """Conjunction-shape design spec section 6's mechanism hypothesis, which
    that section states and deliberately does not assert.

    The finding it explains: better than a third of every detected glissade
    is discarded by the binocular agreement rule, almost always because the
    other eye is still calling that stretch a saccade. The proposed cause is
    boundary placement -- the two eyes ending one saccade about a glissade's
    duration apart. This measures the difference directly.

    **Read against two things, neither of them zero.** Against the AGREEING
    baseline, because the sign is forced (see
    `_glissade_offset_differences`' own docstring) and "the other eye ends
    later" is therefore not a finding -- only how much later, compared with
    the jitter the two eyes show on saccades they agree about. And against
    this eye's own GLISSADE DURATIONS, because the hypothesis is specific:
    about one glissade, not merely "more than the baseline".

    **Nothing here is asserted but identities**, for the reason the kind
    breakdown records: a bound would be tuned to the run that produced it,
    and would fire on an improvement. `unpaired == 0` is not a bound -- it
    is the check that `pso.start == saccade.stop` held on every measured
    event, which is the construction this whole statistic reads the own
    eye's saccade offset from. A nonzero count would mean the numbers below
    describe a boundary other than the one they claim.
    """
    fs_hz = reference["recording"].fs_hz
    left_trace, right_trace = reference["traces"]
    directions = [
        ("left->right", left_trace, right_trace),
        ("right->left", right_trace, left_trace),
    ]

    measured = []
    for name, own_trace, other_trace in directions:
        counts = _kind_agreement(
            own_trace.runs, other_trace.runs, NH_CONJUNCTION_FLOOR_SAMPLES
        )
        measured.append(
            (
                name,
                own_trace,
                _glissade_offset_differences(own_trace.runs, counts.disagreements),
                _agreeing_saccade_offset_differences(counts.agreements),
            )
        )

    with capsys.disabled():
        print(
            "\n  saccade-offset difference between the eyes -- "
            "conjunction-shape spec section 6's mechanism hypothesis. How "
            "much LATER the other eye's saccade ends:"
        )
        for name, own_trace, offsets, baseline in measured:
            ms = offsets.samples / fs_hz * 1000.0
            jitter = np.abs(baseline) / fs_hz * 1000.0
            durations = own_trace.glissade_durations_ms
            print(f"    {name}")
            print(
                f"      over this eye's glissades  n={offsets.samples.size:5d}  "
                f"median {np.median(ms):6.2f} ms  mean {ms.mean():6.2f} ms  "
                f"sd {ms.std():5.2f}  (unpaired {offsets.unpaired})"
            )
            # The two populations the mean above averages together, split.
            # Only the first is section 6's proposed mechanism; the second
            # is a different defect and is what makes that mean twice that
            # median.
            inside, beyond = ms[offsets.within], ms[~offsets.within]
            print(
                f"        ends INSIDE the glissade n={inside.size:5d}  "
                f"median {np.median(inside):6.2f} ms  mean {inside.mean():6.2f} ms"
                f"  sd {inside.std():5.2f}   "
                f"{inside.size / offsets.samples.size:.3f} of them"
            )
            if beyond.size:
                print(
                    f"        ends BEYOND it           n={beyond.size:5d}  "
                    f"median {np.median(beyond):6.2f} ms  mean {beyond.mean():6.2f} ms"
                    f"  sd {beyond.std():5.2f}   "
                    f"{beyond.size / offsets.samples.size:.3f} of them"
                )
            print(
                f"      baseline, agreed saccades  n={baseline.size:5d}  "
                f"median {np.median(jitter):6.2f} ms  mean {jitter.mean():6.2f} ms  "
                f"sd {jitter.std():5.2f}  (unsigned)"
            )
            # **The like-for-like control.** The glissade figure above is
            # forced positive by the overlap rule, so comparing it against
            # an UNSIGNED baseline compares a half-distribution to a whole
            # one and overstates the gap. This restricts the baseline the
            # same way: agreed saccades where the other eye also ends later.
            later = baseline[baseline > 0] / fs_hz * 1000.0
            print(
                f"        other eye ends later     n={later.size:5d}  "
                f"median {np.median(later):6.2f} ms  mean {later.mean():6.2f} ms"
                f"  sd {later.std():5.2f}   <- compare the INSIDE row to this"
            )
            print(
                f"      this eye's glissades       n={durations.size:5d}  "
                f"median {np.median(durations):6.2f} ms  "
                f"mean {durations.mean():6.2f} ms  sd {durations.std():5.2f}"
            )

    for name, _own_trace, offsets, baseline in measured:
        assert offsets.samples.size > 0, (
            f"{name}: no glissade-over-saccade disagreement produced a "
            "measurable offset difference; the statistic measured nothing"
        )
        assert baseline.size > 0, (
            f"{name}: no agreed saccade pair produced a baseline, so the "
            "figure above cannot be read against anything"
        )
        assert offsets.unpaired == 0, (
            f"{name}: {offsets.unpaired} glissades had no own saccade ending "
            "at their start. `_glissade_bounds` returns `(saccade_offset, "
            "stop)`, so that should be impossible -- the offset differences "
            "above are reading a boundary other than the one they claim, and "
            "the detector's run assembly is what changed"
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

    **WHICH kinds disagree is reported and deliberately NOT asserted.** The
    breakdown and its chance baseline are printed, and the one assertion
    added for them is an identity -- that the pairs partition `disagree` --
    rather than a bound on what the pairs turn out to be. A bound would be
    tuned to a number this run just produced, which is the failure the
    Otero-Millan round left a rule about; worse, the obvious bound here
    would fire on an IMPROVEMENT, since the whole asymmetry below is a
    boundary-placement artefact that a better glissade offset criterion
    should shrink. What is measured belongs in the spec and the handoff,
    not in an assertion.

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
        ("left->right", left_trace.runs, right_trace.runs),
        ("right->left", right_trace.runs, left_trace.runs),
    ]
    measured = [
        (
            name,
            _kind_agreement(own, other, NH_CONJUNCTION_FLOOR_SAMPLES),
            _expected_pair_shares(own, other),
            _kind_mix(own),
        )
        for name, own, other in directions
    ]

    with capsys.disabled():
        print(
            f"\n  eye KIND disagreement -- conjunction-shape spec section 6 "
            f"open question 1, first measurement. Floor "
            f"{NH_CONJUNCTION_FLOOR_SAMPLES} sample (this detector's own, not "
            f"Engbert-Kliegl's 6); null measures "
            f"{NULL_KIND_DISAGREEMENT_FLOOR}+ by chance:"
        )
        for name, counts, expected, own_mix in measured:
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
            # WHICH kinds disagreed, against what the two kind mixes alone
            # predict. A pair that merely tracks its own chance share is the
            # vocabulary talking, not the eyes -- the same rule the headline
            # rate is read under, applied to the breakdown.
            if counts.disagree:
                print(
                    "                 which kinds -- observed share of "
                    f"{counts.disagree}, vs the kind mix's own expectation:"
                )
                for pair, n in counts.pairs.most_common():
                    observed = n / counts.disagree
                    chance = expected.get(pair, 0.0)
                    ratio = f"{observed / chance:.2f}x" if chance else "n/a"
                    print(
                        f"                   {pair[0]:>9s} over "
                        f"{pair[1]:<9s} {n:5d}  observed {observed:.4f}  "
                        f"chance {chance:.4f}  {ratio}"
                    )
        # **What section 1 costs each kind, counting BOTH reasons it drops a
        # run.** This block used to divide only the DISAGREEMENTS by each
        # kind's population, and that understated every kind's cost by
        # whatever share of the `alone` bucket it owned -- on this recording
        # the disagreements are almost entirely `pso`, so `saccadic` read as
        # paying 0.2% while its `alone` share was never attributed at all.
        # Outside the `if counts.disagree` guard above, because a kind can
        # be dropped for being unmatched without disagreeing with anything.
        for name, counts, _expected, own_mix in measured:
            dropped = counts.dropped_by_kind()
            unmatched = Counter(_kind_of(run.label) for run in counts.unmatched)
            disagreed = Counter()
            for (own_kind, _other_kind), n in counts.pairs.items():
                disagreed[own_kind] += n
            print(
                f"    {name}  what the agreement rule costs each kind "
                f"(disagreed + unmatched, of that kind's own runs):"
            )
            for kind in sorted(own_mix):
                population = own_mix[kind]
                print(
                    f"      {kind:>9s}  disagreed {disagreed[kind]:5d}  "
                    f"unmatched {unmatched[kind]:5d}  = {dropped[kind]:5d} "
                    f"of {population:6d}   {dropped[kind] / population:.4f}"
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

    for name, counts, _expected, _own_mix in measured:
        assert len(counts.unmatched) == counts.alone, (
            f"{name}: {len(counts.unmatched)} unmatched runs recorded for "
            f"{counts.alone} counted; the per-kind cost above is missing "
            "part of the bucket it divides"
        )
        assert sum(counts.pairs.values()) == counts.disagree, (
            f"{name}: the breakdown accounts for "
            f"{sum(counts.pairs.values())} of {counts.disagree} "
            "disagreements on the real recording; the pairs printed above "
            "are not a partition of them and their shares cannot be read "
            "against the headline rate"
        )
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
