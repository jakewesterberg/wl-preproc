"""How much of an interval a system's segments actually cover.

Section 5.2.1: a block partially covered by a probe is the state that matters --
it is what wl.works asserts `block_neural_assertion` against, and what excludes
a block from a sort. So `partial` is a first-class state and is **never**
collapsed into `absent`; section 4.6 states the same rule from the other side,
that a recording which stopped mid-trial must never be silently treated as
complete.

Pure interval arithmetic: no DataJoint, no I/O. `coverage.BlockCoverage.make()`
and 1c-5's `TrialCoverage.make()` both call this, so the rule has one
definition rather than one per table.
"""

from __future__ import annotations

from collections.abc import Sequence

FULL = "full"
PARTIAL = "partial"
ABSENT = "absent"

# Coverage is compared as a duration, and both sides are sums of float64
# subtractions, so exact equality would make `full` unreachable for a block
# whose segments happen to accumulate a few ulps short. One microsecond is far
# below anything this pipeline can measure -- the coarsest system's own sample
# period is 2 ms -- and far above the accumulated representation error.
_FULL_TOLERANCE_S = 1e-6


def _merge(intervals: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Overlapping and touching intervals, unioned.

    Without this, two segments overlapping by a second count that second twice,
    and `covered_s` can exceed the block's own duration -- at which point `full`
    is unreachable by comparison and every fully covered block reads `partial`.
    Overlap is not hypothetical: one system's segments are disjoint, but a
    block's coverage is computed from whatever segments exist, and a re-ingested
    or duplicated file produces exactly this shape.
    """
    ordered = sorted(intervals)
    merged: list[tuple[float, float]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def classify_coverage(
    block: tuple[float, float], segments: Sequence[tuple[float, float]]
) -> tuple[str, float]:
    """`(state, covered_s)` for one interval against a set of segment extents.

    Every interval is half-open: a segment ending exactly where the block begins
    shares an instant, not an interval, and counting it would make `partial`
    reachable by a recording that captured none of the block.

    Raises on a zero-length block. Coverage is a fraction of the block's own
    duration, so a zero-length one has none -- and both plausible answers are
    wrong in opposite directions: `full` says nothing is missing, `absent` says
    nothing was recorded. The row is malformed, and wl.works authored it
    (spec section 9), so it is surfaced rather than absorbed.
    """
    block_start, block_end = block
    duration_s = block_end - block_start
    if duration_s <= 0.0:
        raise ValueError(
            f"block spans {block_start} to {block_end}, a zero or negative "
            "duration; coverage is a fraction of that duration and there is "
            "none to take a fraction of"
        )

    clipped = [
        (max(start, block_start), min(end, block_end))
        for start, end in segments
        if min(end, block_end) > max(start, block_start)
    ]
    covered_s = sum(end - start for start, end in _merge(clipped))

    if covered_s <= 0.0:
        return ABSENT, 0.0
    if covered_s >= duration_s - _FULL_TOLERANCE_S:
        # Clipping guarantees covered_s <= duration_s, so this is "the whole
        # block" rather than "at least the whole block". Returning the block's
        # own duration keeps a few ulps of float error out of the stored value.
        return FULL, duration_s
    return PARTIAL, covered_s
