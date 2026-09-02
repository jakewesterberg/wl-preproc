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

    **Matching is global, not per-event-greedy, because per-event-greedy is
    not symmetric.** An earlier version had `a`'s events each grab their own
    nearest still-free `b` event, processed in `a`-list order; that lets an
    early event take a candidate a *later* event needed as its only option,
    and which event goes first depends on which trace is named `a`. Found by
    a 20000-trial randomized check after this module shipped: `a` at samples
    `[18, 24]` against `b` at `[22, 25]`, tolerance 4, scored 1.0 one way and
    0.5 the other, on the same two traces. Fixed here by scoring every
    candidate pair up front rather than deciding one event's match before the
    next event's alternatives are even known.

    So instead: every `(a_start, b_start)` pair within tolerance is a
    candidate, sorted by distance, and the walk takes a candidate only when
    both its events are still free. This is symmetric because the candidate
    *set* and every candidate's distance are properties of the pair, not of
    which trace is named `a` -- swapping `a` and `b` only relabels which end
    of each pair is which, it cannot change the set of pairs, their
    distances, or the order the walk considers them in.

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

    candidates = [
        (abs(a_start - b_start), a_start, b_start)
        for a_start in a_starts
        for b_start in b_starts
        if abs(a_start - b_start) <= tolerance_samples
    ]
    # Tiebreak by the pair's two sample indices, smaller-first -- NOT by
    # `(a_start, b_start)` in that argument order. `a_start`/`b_start` are
    # labels this function's CALLER chose, not a property of the pair: they
    # swap whenever the caller swaps which trace is `a`, so `(a_start,
    # b_start)` is a different tuple for the same pair depending on argument
    # order, and CAN reorder two candidates that tie on distance. `(min,
    # max)` is a property of the pair itself and is identical under that
    # relabelling -- correct by construction, not by an empirical property of
    # today's inputs. This looks like it could be simplified back to
    # `(distance, a_start, b_start)`; tried, and found no input, including an
    # exhaustive search over small instances and >10^6 random and targeted
    # trials, where that reordering changes which SAMPLES end up matched
    # (this graph -- points on a line, compatible within a fixed distance --
    # is more constrained than a general bipartite graph, which is likely why
    # not). Kept `(min, max)` anyway: it is provably order-independent rather
    # than order-independent-so-far, and costs nothing.
    candidates.sort(key=lambda pair: (pair[0], min(pair[1], pair[2]), max(pair[1], pair[2])))

    used_a: set[int] = set()
    used_b: set[int] = set()
    matched = 0
    for _distance, a_start, b_start in candidates:
        if a_start in used_a or b_start in used_b:
            continue
        used_a.add(a_start)
        used_b.add(b_start)
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
