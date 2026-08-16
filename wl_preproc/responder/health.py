"""The health response wl.works polls for. Design spec section 5. Reads only.

`build_health` never queries anything itself. `cli/report.py`'s
`gather_readings` is the one computation the daily report and this responder
both render (`Readings`' own docstring: "Neither computes anything itself"),
so this module's whole job is turning the numbers `gather_readings` already
holds into the wire shapes `contracts/protocol.py` defines, plus the verdict
rule spec section 5.1 gives:

| Verdict    | When                                                                   |
|------------|-------------------------------------------------------------------------|
| `down`     | The database is unreachable. Nothing else can be computed.            |
| `degraded` | Reachable, but something needs a human: stuck jobs, quarantined       |
|            | sessions, stalled transfers, or scratch below the floor.              |
| `ok`       | Reachable and none of the above.                                      |

This host never emits `unknown` — see `contracts.protocol.Verdict`'s own
docstring. `unknown` is wl.works' word for OUR silence, recorded on THEIR
side once a host goes quiet past `stale_after_seconds`. A host that is still
running and answering `/health` is, by construction, never in a position to
assert that about itself — doing so would be claiming knowledge of our own
absence.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from wl_preproc.cli.report import Readings
from wl_preproc.contracts.protocol import HealthResponse, Reading, Verdict
from wl_preproc.schema import DEFAULT_PREFIX

# Every key `build_health` can emit below, and the priority order it uses to
# pick `featured` when more than one condition is bad at once. Spec section
# 5.1's degraded row lists "stuck jobs, quarantined sessions, stalled
# transfers, or scratch below the floor" with no stated priority among them
# -- so this ordering is this module's own call, not the spec's, and is
# recorded here because a real host can have two independent problems at
# once (a wedged queue AND a full disk, say) and still needs exactly one
# answer for which reading gets `featured=True`. wl.works takes the first
# featured reading if more than one is marked (design spec section 5.2,
# "Plan 10 section 4 settles the ambiguity"), so "exactly one" is not a
# nicety -- publishing two lets their renderer choose FOR us, silently,
# rather than us choosing.
#
# Disk conditions rank highest, and the two disk fault fields Task 1 split
# apart (`walk_error`, `disk_error` -- previously one collapsed `root_error`
# that hid a real defect: a root the walk could not list but whose free
# space could still be measured) are treated as ONE tier here, not two,
# because they name the same slot in the readings surface (`disk_headroom`)
# rather than two different questions the way `walk_error` and
# `stalled_transfers` do (see below). Within that tier: `cli/doctor.py`'s
# 800 GiB floor is what stands between this host and a mid-sort stall on the
# *next* session -- a worse failure than a delayed session (a stuck job or a
# quarantined session is late; a disk that fills mid-sort can corrupt the
# one in progress). `disk_error` -- the probe itself failing, so there is no
# number at all -- is ranked with rather than below a confirmed-low reading:
# an unmeasured disk might already be below the floor and this host has no
# way to rule that out, so treating "could not check" as more comfortable
# than "checked and low" would be the same overclaim `Verdict` itself
# refuses to make about our own silence.
def _featured_key(readings: Readings) -> str:
    """Which single reading key drove the verdict away from `ok`, or the
    ingest count when nothing did.

    This function IS the verdict rule as well as the `featured` rule --
    `build_health` derives `ok` vs. `degraded` from whether this returns
    anything other than the ingest-count fallback, rather than keeping a
    second, separately-maintained condition list that could disagree with
    this one. Two definitions of "is this host degraded" that could drift
    apart is exactly the defect `Readings`' own docstring says this project
    has already found in four separate shapes.

    `readings.stale_jobs is None` ("no schema activated in this process") is
    deliberately NOT a bad condition here, matching `wlpp doctor`'s own
    `count_stale_jobs` precedent exactly (`report("stale jobs", True, "not
    checked...")`): it means nothing was actually inspected, not that
    something was inspected and found stuck, and reporting a fabricated
    problem from an absence of a check would be the same overclaim
    `unknown` itself is refused for.
    """
    if readings.disk_error is not None or not readings.headroom_ok:
        return "disk_headroom"
    if readings.walk_error is not None:
        return "walk_fault"
    if readings.stale_jobs:
        return "stuck_jobs"
    if readings.quarantined:
        return "quarantined_7d"
    if readings.stalled:
        return "stalled_transfers"
    return "ingested_24h"


def _disk_reading_value(readings: Readings) -> str:
    """Never a fabricated number. `gather_readings` sets `free_gib=0,
    headroom_ok=False` as placeholders when `disk_error` is set -- values
    its own docstring says are "never rendered on their own, only ever
    behind `disk_error`" -- so `disk_error` is checked first here too, and
    this never falls through to that placeholder pair. Phrasing matches
    `build_report`'s Disk section exactly, since both renderings ultimately
    describe the same measurement.
    """
    if readings.disk_error is not None:
        return f"not measured — {readings.disk_error}"
    return f"{readings.free_gib} GiB free {'(ok)' if readings.headroom_ok else '(LOW)'}"


def _stuck_jobs_value(readings: Readings) -> str:
    """`None` reads as "not checked", never as "checked and clean" (which
    would overclaim) nor "checked and stuck" (which would fabricate a
    problem) -- see `_featured_key`'s docstring for why `None` also never
    degrades the verdict."""
    if readings.stale_jobs is None:
        return "not checked (no schema activated in this process)"
    return f"{readings.stale_jobs} stale reservation(s)"


def build_health(
    root: Path,
    prefix: str = DEFAULT_PREFIX,
    now: datetime.datetime | None = None,
) -> HealthResponse:
    """The response served at wl.works' polled health check URL.

    Reads only -- see `tests/responder/test_health.py::
    test_build_health_does_not_write`, which snapshots rows rather than
    trusting `in_transaction` (DataJoint's `insert()` never touches it, so
    it reads `False` for a writing function and a reading one alike).

    `gather_readings` is reached through a function-local import rather than
    one bound at module load, so `monkeypatch.setattr("wl_preproc.cli.
    report.gather_readings", ...)` reaches this call the same way it already
    reaches `gather_readings`'s own local imports of `count_stale_jobs` and
    friends: a module-level `from ... import gather_readings` here would
    bind its own separate name at THIS module's import time and never
    observe a patch applied to `wl_preproc.cli.report`'s attribute
    afterward.
    """
    from wl_preproc.cli.report import gather_readings

    try:
        readings = gather_readings(root, prefix=prefix, now=now)
    except Exception as exc:
        # Nothing else can be computed -- there is no `Readings` to render a
        # reading from, so this is the only reading this function ever
        # fabricates outside of a real `Readings`. It exists solely to say
        # what broke, and it is `featured` by construction: it is the only
        # reading there is to choose from, so `sum(featured) == 1` still
        # holds even on this path.
        return HealthResponse(
            verdict="down",
            readings=[
                Reading(
                    key="database",
                    label="Database",
                    value=f"{type(exc).__name__}: {exc}",
                    featured=True,
                )
            ],
            actions=[],
        )

    featured_key = _featured_key(readings)
    verdict: Verdict = "ok" if featured_key == "ingested_24h" else "degraded"

    def reading(key: str, label: str, value: str) -> Reading:
        return Reading(key=key, label=label, value=value, featured=key == featured_key)

    readings_out = [
        reading("ingested_24h", "Ingested (24 h)", str(len(readings.ingested))),
        reading("quarantined_7d", "Quarantined (7 d)", str(len(readings.quarantined))),
        reading("stalled_transfers", "Stalled transfers", str(len(readings.stalled))),
    ]
    if readings.walk_error is not None:
        # Its own reading, not folded into `stalled_transfers`'s value, for
        # the same reason `Readings` carries `walk_error` as its own field
        # rather than merged into the stall count (see Task 1: a single
        # merged fault field once made a genuinely-measured disk reading
        # render as unmeasured because of an unrelated walk fault). A root
        # the walk could not fully list, and a root with zero stalled
        # transfers, must never render identically -- only a reading present
        # if and only if the fault is can say so. Mirrors `build_report`'s
        # own Stalled section, which prints the count AND, separately, a
        # "was not fully scanned" line when this fires.
        readings_out.append(
            reading(
                "walk_fault",
                "Storage root scan",
                f"root not fully scanned — {readings.walk_error}",
            )
        )
    readings_out.append(reading("stuck_jobs", "Stuck jobs", _stuck_jobs_value(readings)))
    readings_out.append(reading("disk_headroom", "Disk headroom", _disk_reading_value(readings)))

    # Empty until Task 6 (`responder/actions.py`) wires in `available_actions`.
    # Spec section 3: publishing an action before its stage exists is worse
    # than publishing none, since wl.works renders every action as a button
    # any lab member can press.
    return HealthResponse(verdict=verdict, readings=readings_out, actions=[])
