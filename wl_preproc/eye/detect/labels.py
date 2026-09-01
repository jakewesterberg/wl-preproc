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
