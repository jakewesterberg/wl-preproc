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


# Most specific first, `fixation` last as the default. **`BLINK` outranks
# `INVALID` and the order is load-bearing**: a blink IS a validity failure, so
# generic-first would mean no sample is ever labelled `blink`.
#
# `SACCADE` and `MICROSACCADE` are adjacent rather than ranked -- they are a
# split by amplitude, never in contention for the same sample (design spec
# section 1).
PRECEDENCE: tuple[Label, ...] = (
    Label.BLINK,
    Label.INVALID,
    Label.SACCADE,
    Label.MICROSACCADE,
    Label.PSO,
    Label.PURSUIT,
    Label.DRIFT,
    Label.FIXATION,
)


def higher_precedence(first: Label, second: Label) -> Label:
    """Whichever of two labels `PRECEDENCE` ranks higher.

    **This is what makes `PRECEDENCE` operative rather than declarative.** Its
    live consumer is `schema/detect.py::_overlapping`, which needs a rule for
    combining the two eyes' labels wherever they disagree over one binocular
    event -- a rule that must generalise to all seven detectors (design spec
    section 3.1), not only to the amplitude-derived vocabularies where
    re-classifying the conjunction's own amplitude would happen to work.

    Order, not distance: the tuple's positions are a ranking, so nothing here
    reads `PRECEDENCE.index` as a magnitude.
    """
    return min(first, second, key=PRECEDENCE.index)


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
