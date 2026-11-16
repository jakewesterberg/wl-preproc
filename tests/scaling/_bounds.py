"""How a scaling fix is held (spec section 3): time and memory at `n` against `4n`.

The ratio, not seconds, is what is bounded: linear is about 4 and quadratic 16, so a bound of 6 holds on a loaded CI
machine and fails on a regression to quadratic. Memory is `tracemalloc`'s peak, which numpy reports to.
"""

from __future__ import annotations

import time
import tracemalloc
from collections.abc import Callable

TIME_RATIO_BOUND = 6.0
PEAK_RATIO_BOUND = 1.5


def _best_time(fn: Callable[[int], object], n: int, repeats: int = 5) -> float:
    best = float("inf")
    for _ in range(repeats):
        start = time.perf_counter()
        fn(n)
        best = min(best, time.perf_counter() - start)
    return best


def time_ratio(fn: Callable[[int], object], n: int) -> float:
    """The best of five timings of `fn(4n)` over the best of five of `fn(n)` (five, not the plan's three: a few-ms
    timing on a loaded machine read 6.6 once where it reads 4.0-4.2). `fn` builds its own input, so
    building it is part of what is timed: keep it linear."""
    fn(n)  # warm imports and caches
    return _best_time(fn, 4 * n) / _best_time(fn, n)


def _peak(fn: Callable[[int], object], n: int) -> int:
    tracemalloc.start()
    try:
        fn(n)
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def peak_ratio(fn: Callable[[int], object], n: int) -> float:
    """`tracemalloc`'s peak during `fn(4n)` over its peak during `fn(n)`."""
    fn(n)
    return _peak(fn, 4 * n) / _peak(fn, n)
