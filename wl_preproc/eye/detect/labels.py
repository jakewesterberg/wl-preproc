"""The label vocabulary, and the run encoding that stores it.

**All eight labels are declared, and stage 1 produces five of them.** Adding
an enum value later is a schema change, and the migration window closes
January 2027 -- so the vocabulary is declared complete now and filled in as
detectors that can emit `pso`, `pursuit` and `drift` arrive.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np


class Label(StrEnum):
    BLINK = "blink"
    INVALID = "invalid"
    SACCADE = "saccade"
    MICROSACCADE = "microsaccade"
    PSO = "pso"
    PURSUIT = "pursuit"
    DRIFT = "drift"
    FIXATION = "fixation"


# **There is no whole-vocabulary precedence tuple here, and design spec
# section 1's eight-level table is not one.** Only ONE of its levels is ever a
# contest between two candidates for one sample, and it is settled where the
# candidates arise: `blink` over `invalid`, in `validity.py::validity_labels`.
# Below that, a detector returns disjoint intervals, so no sample is ever
# offered two detected labels at once -- and `saccade`/`microsaccade` share a
# level deliberately, being "a split, not a ranking" (section 1).
#
# A general `PRECEDENCE` tuple did live here, and `schema/detect.py::
# _overlapping` used it to arbitrate between the two eyes' labels over one
# binocular event. That was wrong: a tuple has a total order, so it ranked the
# split section 1 says is never in contention, and it defaulted the `pso`
# assignment section 2.5 says must never be defaulted. The conjunction now
# takes its label from its own measurement instead -- see
# `schema/detect.py::_conjunction_label` -- which left this constant with no
# consumer and no defensible general meaning.


# -- Conjunction kinds -------------------------------------------------------
#
# Which labels the binocular conjunction (`schema/detect.py::
# _conjunction_runs`) intersects with which. Here, not in `schema/detect.py`,
# because it is vocabulary knowledge, not schema knowledge -- and because
# `tests/eye/` imports nothing from `wl_preproc.schema` (the 3.13 cross-check
# runs it with no DataJoint), so while this lived in the schema module that
# suite had to restate it, two definitions of one rule with nothing able to
# catch them drifting apart (moved 2026-09-26; CHECKPOINT's "what is next").


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
KIND_OF: dict[Label, str] = {
    Label.SACCADE: "saccadic",
    Label.MICROSACCADE: "saccadic",
    # **Every non-saccadic kind's key IS its label's own value**, and
    # `schema/detect.py::_conjunction_runs` relies on it: `Label(kind)` is
    # how such a kind labels itself. Tested, not trusted -- see
    # `tests/eye/detect/test_labels.py::
    # test_a_single_label_kind_is_keyed_by_its_own_label_value`. "saccadic"
    # is deliberately not a `Label`, because that kind has two of them and no
    # single label could name it.
    Label.PSO: Label.PSO.value,
    Label.PURSUIT: Label.PURSUIT.value,
    Label.DRIFT: Label.DRIFT.value,
}

#: Labels that are never intersected, and why -- listed rather than left as
#: absences, so `kind_of`'s guard can tell "deliberately excluded" from "a
#: ninth label nobody mapped".
#:
#: `fixation` is the synthesized background: `schema/detect.py::_insert_trace` paints every
#: sample no interval claimed, so a region survives as `fixation` whether an
#: intersection painted it or the fill did. Intersecting it would run the
#: nested loop over the largest runs in the trace for no observable difference
#: (spec section 1.2). `blink` and `invalid` come from the validity mask,
#: never from a detector, and are in no detector's vocabulary at all.
NOT_INTERSECTED = frozenset({Label.FIXATION, Label.BLINK, Label.INVALID})


def kind_of(label) -> str | None:
    """`label`'s conjunction kind, or `None` if it is deliberately not
    intersected.

    Raises rather than returning `None` for an unmapped label. Design spec
    section 1 declares all eight labels because the migration window closes
    January 2027; this is what catches a ninth added without updating
    `KIND_OF`, which would otherwise vanish from every conjunction with
    nothing to show for it."""
    if label in NOT_INTERSECTED:
        return None
    try:
        return KIND_OF[label]
    except KeyError as exc:
        raise UnknownLabelKind(
            f"{label!r} has no conjunction kind. Every label is either in "
            f"`KIND_OF` or deliberately in `NOT_INTERSECTED`; a new one is "
            f"in neither until someone decides which it is"
        ) from exc


class TilingError(ValueError):
    """Runs do not tile the sample range exactly."""


@dataclass(frozen=True, slots=True)
class Run:
    """One maximal stretch of a single label. `stop` is EXCLUSIVE, so
    `labels[run.start:run.stop]` is the run and `stop - start` is its length
    in samples."""

    start: int
    stop: int
    label: Label
    #: Otero-Millan's per-detection silhouette (design spec section 5); `None`
    #: for every other detector and for every run this subsystem reconstructs
    #: rather than detects. Defaulted so `Run(start, stop, label)` keeps
    #: working everywhere -- `runs_from_labels` builds runs from a label array
    #: and has no reliability to give them.
    #:
    #: **`None` rather than `float("nan")`, and that is load-bearing.** This
    #: repository has twice had CI go red on 3.13 alone because a dataclass
    #: field defaulting to `nan` compared unequal to itself once `__eq__`
    #: stopped going through `tuple.__eq__`'s identity check (`27917b4`, and
    #: again in `ea9b94b`). `None is None` is true on every interpreter, and
    #: every comparison this subsystem makes between runs depends on it.
    reliability: float | None = None


# Design spec section 3 names a detector's return type `LabelledInterval`. It
# is this type, not a second one: `Run` is already `(start, stop, label)` with
# an exclusive stop, it is already what `runs_from_labels`/`labels_from_runs`
# speak, and it is already what the schema's own run rows store. An alias
# rather than a rename, so the spec's word and the code's word both resolve --
# a near-identical parallel type is how two definitions of one fact get made.
LabelledInterval = Run


def runs_from_labels(labels: np.ndarray) -> list[Run]:
    """Encode a per-sample label array as maximal runs.

    Maximal, so two adjacent runs never share a label: otherwise the encoding
    is not canonical and two equal traces could store differently, which would
    make every stored comparison depend on how a trace happened to be built.
    """
    if len(labels) == 0:
        return []
    boundaries = [0]
    for index in range(1, len(labels)):
        if labels[index] != labels[index - 1]:
            boundaries.append(index)
    boundaries.append(len(labels))
    return [
        Run(start=boundaries[i], stop=boundaries[i + 1], label=Label(labels[boundaries[i]]))
        for i in range(len(boundaries) - 1)
    ]


def labels_from_runs(runs: list[Run], n_samples: int) -> np.ndarray:
    """Decode runs back to a per-sample array, refusing anything that does not
    tile `[0, n_samples)` exactly.

    **This is the invariant that makes rows better than a blob.** A blob can
    be short, long, or internally inconsistent and nothing notices; runs
    either cover the range exactly or they do not, and that is checkable here
    and again on insert.
    """
    if n_samples == 0:
        if runs:
            raise TilingError(f"{len(runs)} run(s) for a zero-sample trace")
        return np.array([], dtype=object)

    if not runs:
        raise TilingError(f"no runs for a {n_samples}-sample trace")
    if runs[0].start != 0:
        raise TilingError(f"runs does not start at 0: first run starts at {runs[0].start}")

    out = np.empty(n_samples, dtype=object)
    cursor = 0
    for run in runs:
        if run.start > cursor:
            raise TilingError(f"gap between sample {cursor} and run starting at {run.start}")
        if run.start < cursor:
            raise TilingError(f"overlap: run starts at {run.start}, previous ended at {cursor}")
        if run.stop <= run.start:
            raise TilingError(f"run [{run.start}, {run.stop}) is empty or reversed")
        out[run.start : run.stop] = run.label
        cursor = run.stop
    if cursor != n_samples:
        raise TilingError(f"runs end at {cursor}, which does not reach {n_samples}")
    return out
