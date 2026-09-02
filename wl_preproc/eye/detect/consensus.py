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

from collections.abc import Callable
from dataclasses import dataclass

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
    6.1). Each event is used at most once.

    **This finds a MAXIMUM matching, not merely a symmetric one -- a prior
    version was symmetric and still wrong.** Two earlier algorithms shipped
    and were replaced in turn, each found by a randomized check rather than
    by inspection:

    1. Per-event-greedy (each of `a`'s events grabbing its own nearest
       still-free `b` event, in `a`-list order) was not even symmetric: an
       early event could take a candidate a *later* event needed as its only
       option, and which event went first depended on which trace was named
       `a`. `a` at `[18, 24]` against `b` at `[22, 25]`, tolerance 4, scored
       1.0 one way and 0.5 the other.
    2. Scoring every candidate pair globally and accepting them in ascending
       DISTANCE order fixed the symmetry -- candidates and their distances
       are properties of the pair, not of argument order -- but distance
       order is still the wrong thing to be greedy about: it can spend an
       event on its single closest partner when giving that partner to
       someone else would have freed up an additional match elsewhere. `a`
       at `[1, 3, 19, 22]` against `b` at `[6, 8, 11, 28]`, tolerance 6:
       sorted by distance, `3` claims `8` (distance 5, its nearest) and `1`
       claims `6` (also distance 5), leaving `19` unmatched -- 2 matched,
       symmetric both ways, and still only 2 when 3 is achievable (`1`-`6`,
       `3`-`8`, `22`-`28`). Symmetric is not the same claim as correct, and
       this UNDERSTATES agreement -- the wrong direction for a suite meant to
       flag degraded tracking rather than manufacture disagreement no
       detector is responsible for.

    The fix: sort each trace's own event starts (already ascending here --
    see `_event_starts`) and walk both with two pointers, each event taking
    the EARLIEST still-reachable partner, advancing whichever side is behind
    when the current pair is out of tolerance. This is optimal -- it reaches
    a matching of MAXIMUM size, not merely *a* matching -- because the
    compatibility graph is convex: plot `a`'s events on one axis and `b`'s on
    the other, and each event's admissible partners form a single contiguous
    run, since "within `tolerance_samples`" is an interval condition on a
    line. A sorted two-pointer scan is the standard optimal algorithm for
    maximum-cardinality matching on convex bipartite graphs. Symmetry then
    follows for a stronger reason than either earlier version had: maximum
    cardinality is a property of the GRAPH -- which pairs are within
    tolerance -- not of which side the traversal calls `a`, so it cannot
    depend on argument order. That retires the previous version's tiebreak
    entirely, along with the argument for why it had to be swap-invariant:
    there is no tie left to break.

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

    # `a_starts`/`b_starts` are already ascending (`_event_starts` scans
    # left to right); the two-pointer walk below requires that.
    i = j = matched = 0
    while i < len(a_starts) and j < len(b_starts):
        if abs(a_starts[i] - b_starts[j]) <= tolerance_samples:
            matched += 1
            i += 1
            j += 1
        elif a_starts[i] < b_starts[j]:
            i += 1
        else:
            j += 1

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
