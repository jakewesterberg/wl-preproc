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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known defect in the rule, not in this implementation of it. Design "
        "spec section 6.1 says a pair is scored in 'the coarsest vocabulary "
        "both declare', and read plainly that is the INTERSECTION of the two "
        "declarations -- which is what this module computes and what the "
        "spec's own two worked examples require. It is wrong for a pair whose "
        "declarations are DISJOINT. Strict, so that whoever fixes the rule is "
        "told by a failing test rather than leaving this passing quietly."
    ),
)
def test_disjoint_vocabularies_should_meet_at_their_common_coarsening():
    """U'n'Eye and BMD agree about a microsaccade and are scored as if silent.

    U'n'Eye declares `{saccade}` because it does not SPLIT (design spec section
    3.1), so it labels a microsaccade `saccade`. BMD declares
    `{microsaccade, drift}`. The two share no declared label, so the
    intersection is empty and the pair is scored in `{fixation}` alone.

    But `coarsen(MICROSACCADE, {SACCADE}) is SACCADE`: BMD's microsaccade CAN
    be expressed in U'n'Eye's vocabulary. The two detectors, having found the
    same small event, meet perfectly at `{saccade, fixation}` -- and the rule
    drops every such sample instead, because U'n'Eye's `saccade` cannot travel
    DOWN an edge into BMD's declaration.

    The fix is a change to section 6.1's rule and belongs with the detector
    that first makes the pair reachable, not here: stage 2A registers only
    Engbert-Kliegl and Otero-Millan, which declare the same vocabulary and
    need neither coarsening nor exclusion. Recorded executably rather than in
    prose so it cannot be lost between stages.
    """
    assert shared_vocabulary(_UNEYE, _BMD, PSO_AS_SACCADE) == frozenset(
        {Label.SACCADE, Label.FIXATION}
    )


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
    metric would store one number for what are really two measurements.

    Three events a side, not one. With a single event each, `event_f1`
    reduces to `2*matched/(len(a_starts)+len(b_starts))`, which is symmetric
    by arithmetic alone whatever the matching does -- that shape is exactly
    what let a real matching-order asymmetry ship undetected (see
    `test_event_f1_does_not_depend_on_which_trace_is_named_a`), so a fixture
    that cannot exercise contention between events cannot stand as this
    property's test. This fixture can: under the matching algorithm this
    module shipped with first, it scored 1.0 one way and 0.667 the other."""
    from wl_preproc.eye.detect.consensus import cohen_kappa, event_f1

    a = np.array(
        [Label.FIXATION] * 5 + [Label.SACCADE] * 1 + [Label.FIXATION] * 12
        + [Label.SACCADE] * 1 + [Label.FIXATION] * 5 + [Label.SACCADE] * 1
        + [Label.FIXATION] * 1
    )
    b = np.array(
        [Label.FIXATION] * 9 + [Label.SACCADE] * 1 + [Label.FIXATION] * 12
        + [Label.SACCADE] * 1 + [Label.FIXATION] * 2 + [Label.SACCADE] * 1
    )
    mask = np.ones(len(a), dtype=bool)

    assert event_f1(a, b, mask, 4) == event_f1(b, a, mask, 4)
    assert cohen_kappa(a, b, mask) == cohen_kappa(b, a, mask)


def test_event_f1_does_not_depend_on_which_trace_is_named_a():
    """Regression for a real asymmetry, found by a 20000-trial randomized
    check after this module first shipped (~0.1% of random multi-event
    pairs) and independently reproduced (~0.6% of 4000 pairs) rather than
    theorised: `a`'s events at samples 18 and 24 against `b`'s at 22 and 25,
    tolerance 4, scored 1.0 as `event_f1(a, b, ...)` and 0.5 as
    `event_f1(b, a, ...)` -- same two traces, same tolerance, different
    answer depending only on which was passed first.

    Design spec section 6.1 justifies `DetectorAgreement`'s canonical
    `a < b` pairwise key by this metric's symmetry, so this was not merely a
    failed test -- it was a stored score that would have depended on which
    paramset happened to sort first.

    Root cause: the old matching let each of `a`'s events greedily grab its
    own nearest still-free `b` event, processed in `a`-list order. Event 22
    (in `b`) is 4 away from 18 and 2 away from 24; taking the nearer (24)
    looks locally right but strands 25, whose only reachable partner
    (distance 1) was 24 -- and swapping which trace drives the loop changes
    who grabs 24 first. The fix scores every `(a_start, b_start)` pair up
    front instead of deciding one event's match before its alternatives are
    known, which is what makes the outcome independent of argument order."""
    from wl_preproc.eye.detect.consensus import event_f1

    a = np.array(
        [Label.FIXATION] * 18 + [Label.SACCADE] * 1 + [Label.FIXATION] * 5
        + [Label.SACCADE] * 1 + [Label.FIXATION] * 1
    )
    b = np.array(
        [Label.FIXATION] * 22 + [Label.SACCADE] * 1 + [Label.FIXATION] * 2
        + [Label.SACCADE] * 1
    )
    mask = np.ones(len(a), dtype=bool)

    assert event_f1(a, b, mask, tolerance_samples=4) == 1.0
    assert event_f1(b, a, mask, tolerance_samples=4) == 1.0


def test_event_f1_symmetry_holds_across_randomized_multi_event_traces():
    """The property-level version of the same regression, fixed-seed so it
    keeps checking rather than having only been checked once by hand. A
    single adversarial fixture pins the mechanism that was found; this pins
    the property itself over traces neither the author nor the reviewer
    hand-picked, which is exactly the kind of case a hand-picked fixture
    cannot be trusted to represent."""
    from wl_preproc.eye.detect.consensus import event_f1

    rng = np.random.default_rng(1)
    for _ in range(4000):
        n = int(rng.integers(20, 80))
        a = np.full(n, Label.FIXATION, dtype=object)
        b = np.full(n, Label.FIXATION, dtype=object)
        for arr in (a, b):
            for _ in range(int(rng.integers(0, 5))):
                start = int(rng.integers(0, n))
                length = int(rng.integers(1, 6))
                arr[start : start + length] = Label.SACCADE
        mask = np.ones(n, dtype=bool)
        tolerance_samples = int(rng.integers(0, 6))

        forward = event_f1(a, b, mask, tolerance_samples)
        backward = event_f1(b, a, mask, tolerance_samples)
        assert forward == backward, (a.tolist(), b.tolist(), tolerance_samples)


def test_event_f1_finds_a_match_the_distance_sorted_matching_missed():
    """Regression for a second, more serious defect than the symmetry bug
    above: the distance-sorted matching that fixed it was itself symmetric
    and still wrong. Confirmed against the module as it stood at commit
    `af29479` (the symmetry fix, before this test's fix): it scored this
    exact pair `0.5` (2 matched), not `0.75` (3 matched), in both
    directions -- symmetric is not the same claim as correct.

    `a`'s events at samples 1, 3, 19, 22 against `b`'s at 6, 8, 11, 28,
    tolerance 6. Sorted by distance, `1`-`6` and `3`-`8` (each distance 5,
    each side's nearest reachable partner) both get taken, leaving `19`
    stranded (11 and 28 are both out of tolerance) even though a matching of
    3 exists: `1`-`6`, `3`-`8`, `22`-`28`. Nothing about maximizing each
    event's own closeness finds that `22` needed `28` and `19` needed
    nothing else available -- distance-sorted greedy has no way to know that
    ahead of committing `3` to `8`. This under-counts agreement, which is
    the wrong failure direction for a suite meant to flag degraded tracking
    rather than manufacture disagreement no detector is responsible for."""
    from wl_preproc.eye.detect.consensus import event_f1

    a = np.array(
        [Label.FIXATION] * 1 + [Label.SACCADE] * 1 + [Label.FIXATION] * 1
        + [Label.SACCADE] * 1 + [Label.FIXATION] * 15 + [Label.SACCADE] * 1
        + [Label.FIXATION] * 2 + [Label.SACCADE] * 1 + [Label.FIXATION] * 6
    )
    b = np.array(
        [Label.FIXATION] * 6 + [Label.SACCADE] * 1 + [Label.FIXATION] * 1
        + [Label.SACCADE] * 1 + [Label.FIXATION] * 2 + [Label.SACCADE] * 1
        + [Label.FIXATION] * 16 + [Label.SACCADE] * 1
    )
    mask = np.ones(len(a), dtype=bool)

    assert event_f1(a, b, mask, tolerance_samples=6) == 0.75
    assert event_f1(b, a, mask, tolerance_samples=6) == 0.75


def test_event_f1_matched_count_equals_a_maximum_bipartite_matching():
    """The assertion that would have caught the distance-sorted defect from
    the start: not that the matching is symmetric (it was, and was still
    wrong), but that it finds as many matches as are achievable AT ALL.
    Checked against an independent maximum-matching oracle -- Kuhn's
    augmenting-path algorithm, written out here rather than imported, since
    this project takes no `scipy`/`networkx` dependency for it -- rather
    than against `event_f1`'s own logic restated in different words.

    Event starts are spaced at least 2 samples apart so two adjacent chosen
    samples never merge into one run and silently change the event count
    the oracle is told about."""
    from wl_preproc.eye.detect.consensus import event_f1

    def max_matching_size(a_starts, b_starts, tolerance_samples):
        adjacency = [
            [
                j
                for j, b_start in enumerate(b_starts)
                if abs(a_start - b_start) <= tolerance_samples
            ]
            for a_start in a_starts
        ]
        match_of_b = [-1] * len(b_starts)

        def augment(i, visited):
            for j in adjacency[i]:
                if j in visited:
                    continue
                visited.add(j)
                if match_of_b[j] == -1 or augment(match_of_b[j], visited):
                    match_of_b[j] = i
                    return True
            return False

        return sum(augment(i, set()) for i in range(len(a_starts)))

    rng = np.random.default_rng(2)
    for _ in range(4000):
        n = int(rng.integers(20, 80))
        n_a_events = int(rng.integers(0, 6))
        n_b_events = int(rng.integers(0, 6))
        a_starts = sorted(2 * x for x in rng.choice(n // 2, size=n_a_events, replace=False))
        b_starts = sorted(2 * x for x in rng.choice(n // 2, size=n_b_events, replace=False))
        tolerance_samples = int(rng.integers(0, 10))

        a = np.full(n, Label.FIXATION, dtype=object)
        a[a_starts] = Label.SACCADE
        b = np.full(n, Label.FIXATION, dtype=object)
        b[b_starts] = Label.SACCADE
        mask = np.ones(n, dtype=bool)

        score = event_f1(a, b, mask, tolerance_samples)

        if not a_starts and not b_starts:
            expected = 1.0
        elif not a_starts or not b_starts:
            expected = 0.0
        else:
            matched = max_matching_size(a_starts, b_starts, tolerance_samples)
            precision, recall = matched / len(a_starts), matched / len(b_starts)
            expected = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)

        assert score == expected, (a_starts, b_starts, tolerance_samples, score, expected)


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


def test_a_multi_sample_run_counts_as_one_event_not_one_per_sample():
    """`_event_starts` collapses a run to its onset, and nothing in this file
    pinned that directly until now.

    Task 2's review mutated the collapsing away -- every event SAMPLE becoming
    its own event -- and only one test in the file failed, incidentally: a
    zero-match tolerance case. Every other multi-event fixture is structurally
    blind to it. Most plant single-sample events, where a length-1 run is
    identical collapsed or not. The one that does use length-5 runs survives by
    arithmetic accident: 5 matched of 5 and of 10 gives exactly the ratio 1 of
    1 and of 2 does, so its F1 is unchanged either way.

    That is the same shape as the symmetry tests that could not see a
    non-maximum matching, one level further down -- a property everything
    depends on, asserted by nothing. `event_f1` counts EVENTS; if a run stopped
    collapsing, a single long saccade would count as dozens and every score
    built on it would be wrong while the suite stayed green.
    """
    from wl_preproc.eye.detect.consensus import _event_starts

    labels = np.array([Label.FIXATION] * 10 + [Label.SACCADE] * 5 + [Label.FIXATION] * 10)
    mask = np.ones(len(labels), dtype=bool)

    assert _event_starts(labels, mask) == [10]


def test_two_runs_separated_by_one_fixation_sample_are_two_events():
    """The other side of the same boundary: collapsing must not swallow a real
    gap. One `fixation` sample is the least separation that can exist, so it is
    the case worth pinning -- a fixture author who plants two events one sample
    apart gets two, and one who plants them adjacent gets one."""
    from wl_preproc.eye.detect.consensus import _event_starts

    labels = np.array(
        [Label.FIXATION] * 5
        + [Label.SACCADE] * 3
        + [Label.FIXATION]
        + [Label.SACCADE] * 3
        + [Label.FIXATION] * 5
    )
    mask = np.ones(len(labels), dtype=bool)

    assert _event_starts(labels, mask) == [5, 9]


def test_adjacent_runs_of_different_event_labels_are_one_event():
    """A `saccade` immediately followed by a `microsaccade` is ONE event here,
    because `event_f1` asks when the eye moved, not what the movement was
    called. Recorded as a test because it is a real semantic choice rather than
    an accident of the loop: `cohen_kappa` is the metric that scores the label,
    and design spec section 6.1 ships both precisely so each answers the
    question the other cannot.
    """
    from wl_preproc.eye.detect.consensus import _event_starts

    labels = np.array(
        [Label.FIXATION] * 5 + [Label.SACCADE] * 3 + [Label.MICROSACCADE] * 3 + [Label.FIXATION] * 5
    )
    mask = np.ones(len(labels), dtype=bool)

    assert _event_starts(labels, mask) == [5]
