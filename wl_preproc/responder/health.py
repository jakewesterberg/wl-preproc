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
from wl_preproc.contracts.protocol import HealthResponse, Reading, Verdict, plain_text
from wl_preproc.responder.actions import available_actions
from wl_preproc.schema import DEFAULT_PREFIX

# Every key `build_health` can emit below, and the priority order it uses to
# pick `featured` when more than one condition is bad at once. Spec section
# 5.1's degraded row lists "stuck jobs, quarantined sessions, stalled
# transfers, or scratch below the floor" with no stated priority among them
# -- so this ordering is this module's own call, not the spec's, and is
# recorded here because a real host can have two independent problems at
# once and still needs exactly one answer for which reading gets
# `featured=True`. wl.works takes the first featured reading if more than
# one is marked (design spec section 5.2, "Plan 10 section 4 settles the
# ambiguity"), so "exactly one" is not a nicety -- publishing two lets their
# renderer choose FOR us, silently, rather than us choosing.
#
# The order separates EVENTS from the one LEVEL. `walk_error`, `disk_error`,
# `stale_jobs`, `quarantined` and `stalled` are each a fact about something
# that just happened or is currently stuck -- a countable, actionable
# incident. `not headroom_ok` (the disk measured fine and came back low) is
# different in kind: on a real host it can stay true for days or weeks at a
# stretch, long after the humans running the lab already know about it.
# Ranking it with -- let alone above -- the events would let an already-known
# chronic condition permanently occupy the one slot wl.works renders on its
# home page, so a NEW acute fault (the storage root vanishing, a batch of
# jobs freshly wedged) could never surface there for as long as the disk
# stays low. Confirmed, not hypothetical: on this project's own dev sandbox
# the real disk sits under the 800 GiB floor, so an earlier version of this
# ordering made `_featured_key` return `"disk_headroom"` on EVERY call,
# always, regardless of what else was wrong (see
# `tests/responder/test_health.py::test_a_chronic_low_disk_never_masks_an_
# acute_fault`). So every event outranks the level; the level still ranks
# above the ok fallback, since it is a real, standing condition a human
# should eventually see if nothing more acute is happening.
#
# `disk_error` -- the probe itself failing, not merely reporting low -- is an
# EVENT (it just started happening), so it ranks with the other events, not
# with `not headroom_ok`, even though both name the same `disk_headroom`
# reading slot below. It ranks ABOVE the other events except `walk_error`:
# an unmeasured disk might already be below the floor and this host has no
# way to rule that out, so treating "could not check" as more comfortable
# than "checked and low" would be the same overclaim `Verdict` itself
# refuses to make about our own silence. `walk_error` outranks even
# `disk_error`: when the storage root is simply gone, `_candidate_dirs` and
# `scratch_headroom` both fail from the very same cause, and "Storage root
# scan" is the more honest description of what broke than "Disk headroom:
# not measured", which reads as though the problem were specific to disk
# space rather than the whole root being unreachable.
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

    `readings.stale_jobs is None` and `readings.disk_error is not None` are
    both, in one sense, "this host does not have a number" -- and are
    treated oppositely on purpose, not by oversight. `stale_jobs is None`
    means no schema has been `.activate()`d in this process, so NOTHING was
    actually inspected -- a routine, expected state for plenty of legitimate
    callers (matching `wlpp doctor`'s own `count_stale_jobs` precedent
    exactly: `report("stale jobs", True, "not checked...")`), and reporting
    a fabricated problem from an absence of a check would be the same
    overclaim `unknown` itself is refused for. `disk_error is not None`
    means the OPPOSITE: a check was actively attempted and the attempt
    itself failed -- a live fault, not a benign not-applicable, so it is
    treated as one.
    """
    if readings.walk_error is not None:
        return "walk_fault"
    if readings.disk_error is not None:
        return "disk_headroom"
    if readings.stale_jobs:
        return "stuck_jobs"
    if readings.quarantined:
        return "quarantined_7d"
    if readings.stalled:
        return "stalled_transfers"
    if not readings.headroom_ok:
        return "disk_headroom"
    return "ingested_24h"


def _disk_reading_value(readings: Readings) -> str:
    """Never a fabricated number. `gather_readings` sets `free_gib=0,
    headroom_ok=False` as placeholders when `disk_error` is set -- values
    its own docstring says are "never rendered on their own, only ever
    behind `disk_error`" -- so `disk_error` is checked first here too, and
    this never falls through to that placeholder pair.

    **Wording follows `build_report`'s Disk section; the markdown does
    not** -- an earlier version of this docstring claimed the phrasing
    matched "exactly", and that was true of one branch and not the other:

    | branch | `build_report` | here |
    |---|---|---|
    | measured | `{N} GiB free (ok)`/`(LOW)` | identical |
    | fault | `**not measured** — {disk_error}` | `not measured — {disk_error}` |

    The dropped `**` is deliberate and required. A `Reading.value` is
    contracted as plain text -- `contracts/protocol.py`'s own module
    docstring, "We refuse to emit markup at all rather than relying on
    [wl.works escaping it]", and `docs/ops/lab-host-protocol.md`'s
    "Strings are plain text, never markup". Note that this one is a rule
    the PRODUCER keeps rather than one the validator enforces:
    `_MARKUP_RE` is `[<>&]`, so a stray `**` would sail through
    `_reject_markup` and land in wl.works' UI as literal asterisks. The
    same applies to `build_report`'s backticked `` `{root}` ``, which has
    no counterpart here at all.

    `disk_error` is an `OSError` message this host did not author -- a real
    path or a library's own text can contain `<`, `>` or `&`, which
    `Reading`'s validator does not sanitise but outright REJECTS. Piping it
    through `plain_text` here is what keeps a fault from turning into no
    response at all; see `plain_text`'s own docstring in `contracts/
    protocol.py` for why that is done here rather than as a fallback on the
    resulting `ValidationError`.
    """
    if readings.disk_error is not None:
        return plain_text(f"not measured — {readings.disk_error}")
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

    `_QUARANTINE_WINDOW_DAYS` rides the same local import, for the same
    reason one step further on: `cli/report.py` OWNS that number, and
    reading it here through the owning module rather than copying it means
    a test can move the window on the owner and watch this label follow
    (`test_the_quarantine_label_follows_the_window_it_names`). A module-
    level binding would make that test patch this module instead, which
    proves the copy is consistent with itself rather than with its source.
    """
    from wl_preproc.cli.report import _QUARANTINE_WINDOW_DAYS, gather_readings

    try:
        readings = gather_readings(root, prefix=prefix, now=now)
    except Exception as exc:
        # Nothing else can be computed -- there is no `Readings` to render a
        # reading from, so this is the only reading this function ever
        # fabricates outside of a real `Readings`. It exists solely to say
        # what broke, and it is `featured` by construction: it is the only
        # reading there is to choose from, so `sum(featured) == 1` still
        # holds even on this path.
        #
        # `str(exc)` is this path's own untrusted text -- and, unlike the
        # walk/disk faults below, this handler is the LAST line of defense:
        # if constructing ITS reading also raised, there would be no path
        # left that reliably answers wl.works at all. `plain_text` here for
        # the identical reason it is used below.
        #
        # `actions` is hardcoded `[]` here, not `available_actions(prefix=prefix)`:
        # this branch has just declared the database unreachable, and every
        # published action, once triggered, ultimately becomes an inserted
        # request row (spec section 11.3: "the responder does not compute;
        # it inserts a Manual-tier request row"). Whatever `available_actions`
        # would report about which stages exist says nothing about whether a
        # request could actually be accepted right now -- publishing one here
        # would be the same overclaim `unknown` itself is refused for, one
        # field over.
        return HealthResponse(
            verdict="down",
            readings=[
                Reading(
                    key="database",
                    label="Database",
                    value=plain_text(f"{type(exc).__name__}: {exc}"),
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
        # The KEY stays pinned to the literal `quarantined_7d`: it is a wire
        # name wl.works matches on, so it must not silently change shape the
        # day the window does. The LABEL is rendered text describing that
        # window, so it follows `_QUARANTINE_WINDOW_DAYS` -- the same
        # constant `build_report` already interpolates into its own
        # "## Quarantined (N d)" heading. Before this, `report.py` owned the
        # number and this line hardcoded a copy of it, so changing the
        # window moved the report's heading and left the responder's label
        # claiming 7 -- over a number this project HAS already changed
        # once: the Quarantined section shipped unwindowed (rendering the
        # whole table forever) until `aacd922` introduced
        # `_QUARANTINE_WINDOW_DAYS` at all. See that constant's own comment
        # for why seven and not one or none.
        reading(
            "quarantined_7d",
            f"Quarantined ({_QUARANTINE_WINDOW_DAYS} d)",
            str(len(readings.quarantined)),
        ),
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
        #
        # `readings.walk_error` is an exception message this host did not
        # author -- it can legitimately contain a real filesystem path, and
        # a path can legitimately contain `<`, `>` or `&`. `plain_text` here
        # for the same reason `_disk_reading_value` uses it: `Reading`'s
        # validator rejects those characters outright rather than
        # sanitising them, and this is exactly the fault line most likely to
        # need it -- reproduced directly against a real root named `A&B`,
        # with no monkeypatching, before this fix existed.
        readings_out.append(
            reading(
                "walk_fault",
                "Storage root scan",
                plain_text(f"root not fully scanned — {readings.walk_error}"),
            )
        )
    readings_out.append(reading("stuck_jobs", "Stuck jobs", _stuck_jobs_value(readings)))
    readings_out.append(reading("disk_headroom", "Disk headroom", _disk_reading_value(readings)))

    # `available_actions` derives this from which computed stages exist
    # (`responder/actions.py`), not from anything computed above -- spec
    # section 3: publishing an action before its stage exists is worse than
    # publishing none, since wl.works renders every action as a button any
    # lab member can press. Empty today because no stage exists yet.
    return HealthResponse(
        verdict=verdict, readings=readings_out, actions=available_actions(prefix=prefix)
    )
