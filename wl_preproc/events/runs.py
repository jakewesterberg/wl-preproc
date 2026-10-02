"""Which run a block or a trial belongs to (design spec
`2026-10-01-session-listing-and-run-requests-design.md` amendment 11).

element-event stores a block's and a trial's start as a MySQL FLOAT
(`trial.Block`, `trial.Trial`), and an event's time as `decimal(10, 4)`
(`event.Event`); `core.Run` stores a run's bounds as doubles. Either rounding
can put a start that lies just after its run's `RUN_START` -- or the
`RUN_START` event itself, which lies exactly on it -- before the run. The
run's bounds are rounded the same way first: rounding is monotonic, so
anything that starts inside its run is found inside it, at any magnitude.
The listing and the NWB builder both ask here, so they cannot disagree.

**A FLOAT is read back as the double it stores** (`stored_doubles`). MySQL's
text protocol, the one DataJoint reads through, returns a FLOAT to six
significant digits: 18000.124, stored as 18000.123046875, comes back as
18000.1, before a `RUN_START` at 18000.1234, and a 20 ms trial hours in comes
back with no length. Measured against MySQL 8.0 (Plan B's final review, C1).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import numpy as np


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
    """Whether a float32-stored start lies in a run's double bounds."""
    return float(np.float32(run_start_s)) <= start_s <= float(np.float32(run_stop_s))


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


def event_inside(time_s: float, run_start_s: float, run_stop_s: float) -> bool:
    """Whether an `event.Event` time lies in a run's double bounds, both ends
    included: an event is an instant, and `RUN_START`/`RUN_END` sit exactly
    on them."""
    return _as_event_time(run_start_s) <= time_s <= _as_event_time(run_stop_s)
