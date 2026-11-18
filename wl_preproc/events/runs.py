"""Which run a block or a trial belongs to (design spec
`2026-10-01-session-listing-and-run-requests-design.md` amendment 11).

A trial's and a block's start are read as measured, doubles
(`core.TrialTime`, `core.BlockTime`; design spec
`2026-11-17-full-precision-trial-times-design.md`), as `core.Run` stores a
run's bounds, so `starts_inside` compares them exactly. An event's time is
stored as `decimal(10, 4)` (`event.Event`), and `event_bounds` rounds a run's
bounds the same way first: rounding is monotonic, so an event that lies inside
its run is found inside it, at any magnitude. The listing and the NWB builder
both ask here, so they cannot disagree.

**A FLOAT is read back as the double it stores** (`stored_doubles`), for any
reader of one that remains. MySQL's text protocol, the one DataJoint reads
through, returns a FLOAT to six significant digits: 18000.124, stored as
18000.123046875, comes back as 18000.1. Measured against MySQL 8.0 (Plan B's
final review, C1).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def stored_doubles(query, *names: str) -> list[dict]:
    """`query`'s rows, with each FLOAT attribute in `names` read as the double
    MySQL stores, through `CAST(... AS DOUBLE)`, rather than the six
    significant digits its text protocol returns."""
    computed = {f"{name}_as_double": f"CAST({name} AS DOUBLE)" for name in names}
    rows = query.proj(..., **computed).to_dicts()
    for row in rows:
        for name in names:
            row[name] = row.pop(f"{name}_as_double")
    return rows


def starts_inside(start_s: float, run_start_s: float, run_stop_s: float) -> bool:
    """Whether a measured trial or block start lies in a run's bounds, both
    doubles, compared exactly."""
    return run_start_s <= start_s <= run_stop_s


def run_of(start_s: float, runs: list[dict]) -> int | None:
    """The `run_number` of the first of `runs` (each with `start_s`, `end_s`)
    that `start_s` lies in, or None."""
    for run in runs:
        if starts_inside(start_s, run["start_s"], run["end_s"]):
            return run["run_number"]
    return None


def _as_event_time(value_s: float) -> float:
    """`value_s` rounded as MySQL stores it in `decimal(10, 4)`: to 0.1 ms,
    half away from zero."""
    return float(Decimal(repr(float(value_s))).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def event_bounds(run_start_s: float, run_stop_s: float) -> tuple[float, float]:
    """A run's double bounds rounded as `event.Event` stores a time, once, for
    comparing a session's events with them."""
    return _as_event_time(run_start_s), _as_event_time(run_stop_s)


def event_inside(time_s: float, run_start_s: float, run_stop_s: float) -> bool:
    """Whether an `event.Event` time lies in a run's double bounds, both ends
    included: an event is an instant, and `RUN_START`/`RUN_END` sit exactly
    on them."""
    low, high = event_bounds(run_start_s, run_stop_s)
    return low <= time_s <= high
