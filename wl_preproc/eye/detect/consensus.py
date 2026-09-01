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
