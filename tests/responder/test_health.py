"""The health response. Reads only, and never claims `unknown`."""

from __future__ import annotations

import dataclasses
import datetime
from pathlib import Path

import pytest

from wl_preproc.cli.doctor import scratch_headroom
from wl_preproc.cli.report import Readings
from wl_preproc.responder.health import build_health


def _base_readings(**overrides) -> Readings:
    """A fully healthy `Readings`, overridable per test.

    Bypasses the real filesystem/database walk entirely. `build_health`
    consumes whatever `gather_readings` returns and does not care how it was
    produced, so testing ITS OWN mapping (verdict, `featured`) is cleanest
    done directly against a `Readings` value with exactly the fields a test
    wants to control -- the same idiom the brief's own
    `test_a_stuck_job_degrades_the_verdict` already uses for `stale_jobs`
    via `count_stale_jobs`, extended here to the fields (`disk_error`,
    `walk_error`, `quarantined`, `stalled`) that have no equally convenient
    function to monkeypatch instead.
    """
    base: dict = dict(
        at=datetime.datetime.now(datetime.UTC),
        ingested=[],
        quarantined=[],
        stalled=[],
        walk_error=None,
        stale_jobs=0,
        free_gib=2000,
        headroom_ok=True,
        disk_error=None,
    )
    base.update(overrides)
    return Readings(**base)


def test_a_healthy_root_is_ok(scanned, monkeypatch):
    """Health is a `Readings`-shaped claim about the ONE session `scanned`
    just landed, not a claim about the whole shared test database or the
    real host's disk -- and `gather_readings`'s own queries are broader than
    that. Two facts confirmed directly, empirically, rather than assumed:

    1. `ingest.Quarantine`'s 7-day query is unscoped by `root` and this
       suite shares one never-cleaned database across every test file in
       the run (`tests/schema/test_guardrails.py`'s own "no cleanup
       afterward" convention). By the time this file runs after
       `tests/cli/test_report.py`, `Quarantine` already holds real rows
       with recent `failed_at` timestamps that file planted on purpose, and
       `tests/schema/test_ingest.py` plants one more with a fixed
       `2027-03-14` timestamp -- confirmed live: this exact test failed
       with `verdict == "degraded"` before this fix, and a debug print
       showed 3 real, unrelated rows in `readings.quarantined`.
    2. `scratch_headroom` measures the REAL filesystem `root` sits on, and
       this project's own dev/CI hosts commonly report less than the 800
       GiB production floor free (confirmed directly against this sandbox:
       ~630 GiB).

    Neither is a defect in `build_health` -- both mappings are correct
    per design spec section 5.1, and `test_real_low_scratch_degrades_the_
    verdict_on_this_host` below proves the disk half fires for real. This
    test's job is different: prove `ok` is reachable when NOTHING about
    THIS session or host is actually wrong. So the real `gather_readings`
    call still runs -- the walk, the ingest count and the stalled check
    are all genuine, scoped to this test's own fresh `root` and therefore
    already immune to cross-test pollution (confirmed: `stalled` and
    `walk_error` were unaffected even before this fix) -- and only the two
    fields a shared, uncleaned database and a real disk can contaminate are
    neutralized afterward, on the real result rather than a fabricated one.
    """
    root, prefix = scanned("rsphlth1")
    from wl_preproc.cli.report import gather_readings as real_gather_readings

    def clean(*args, **kwargs):
        readings = real_gather_readings(*args, **kwargs)
        return dataclasses.replace(
            readings,
            quarantined=[],
            stale_jobs=0,
            free_gib=2000,
            headroom_ok=True,
            disk_error=None,
        )

    monkeypatch.setattr("wl_preproc.cli.report.gather_readings", clean)

    health = build_health(root, prefix=prefix)

    assert health.verdict == "ok"
    # Design spec section 5.2: "...or the ingest count when everything is
    # fine." Pinned here, specifically, since this is the one scenario the
    # spec names that fallback for.
    featured = [r.key for r in health.readings if r.featured]
    assert featured == ["ingested_24h"]


def test_exactly_one_reading_is_featured(scanned):
    """Plan 10 section 4 settles the ambiguity — more than one featured
    reading and the first wins — so emitting more than one would let their
    renderer pick for us. Deliberately unpatched (unlike the test above):
    whichever way this host's real disk happens to fall, exactly one
    reading must still be featured, and running this against whatever
    `scratch_headroom` actually measures here is free extra coverage of
    that invariant under a real, not synthetic, condition.
    """
    root, prefix = scanned("rspftr1")
    health = build_health(root, prefix=prefix)

    assert sum(1 for r in health.readings if r.featured) == 1


def test_a_stuck_job_degrades_the_verdict(scanned, monkeypatch):
    root, prefix = scanned("rspstk1")
    # Isolates the claim under test: without this, a real disk below the
    # 800 GiB floor (see test_a_healthy_root_is_ok's docstring) would ALSO
    # make the verdict degraded here, and the assertions below would pass
    # for a reason unrelated to the stuck job this test is named for.
    monkeypatch.setattr("wl_preproc.cli.report.scratch_headroom", lambda path: (2000, True))
    monkeypatch.setattr("wl_preproc.daemon.count_stale_jobs", lambda *a, **k: 3)
    health = build_health(root, prefix=prefix)

    assert health.verdict == "degraded"
    assert any("stuck" in r.key for r in health.readings)


def test_an_unreachable_database_is_down(scanned, monkeypatch):
    root, prefix = scanned("rspdwn1")

    def boom(*args, **kwargs):
        raise RuntimeError("no database")

    monkeypatch.setattr("wl_preproc.cli.report.gather_readings", boom)
    health = build_health(root, prefix=prefix)

    assert health.verdict == "down"
    # Nothing else CAN be computed on this path -- there is no Readings to
    # build a real reading list from -- so the one explanatory reading is
    # both the whole list and, structurally, the featured one.
    assert [r.featured for r in health.readings] == [True]
    assert health.actions == []


def test_this_host_never_claims_unknown(scanned, monkeypatch):
    """`unknown` is what wl.works records when a host goes silent past its
    stale_after_seconds. It is their word for our absence and we are never in
    a position to assert it about ourselves — claiming it would be asserting
    knowledge of our own silence."""
    root, prefix = scanned("rspunk1")

    def boom(*args, **kwargs):
        raise RuntimeError("no database")

    monkeypatch.setattr("wl_preproc.cli.report.gather_readings", boom)
    assert build_health(root, prefix=prefix).verdict != "unknown"


def test_the_action_list_is_empty_until_a_stage_exists(scanned):
    root, prefix = scanned("rspact1")
    assert build_health(root, prefix=prefix).actions == []


# --- Beyond the brief: the two independent fault fields, and the featured
# invariant under more than one simultaneous fault. Both use `_base_readings`
# rather than real filesystem tricks, since the fault ITSELF (a chmod-000
# root, a missing mount) is already covered by tests/cli/test_report.py --
# what is untested anywhere else is how `build_health` maps an already-
# computed `Readings` onto a verdict and a `featured` reading. ---


def test_a_disk_fault_degrades_the_verdict_and_never_fabricates_a_number(scanned, monkeypatch):
    """`disk_error` (Task 1) is independent of `walk_error` — a root the
    walk cannot list can still be measured for free space, and vice versa —
    and the 800 GiB floor `cli/doctor.py` treats as safety-critical depends
    on this reading actually being taken. So an unmeasured disk is treated
    as seriously as a measured-and-low one (both degrade, both feature the
    same `disk_headroom` reading), and neither ever renders a fabricated
    GiB number in place of the fault: `gather_readings` sets `free_gib=0` as
    a placeholder exactly when `disk_error` is set, and that placeholder
    must never reach a reader as though it were real.
    """
    root, prefix = scanned("rspdsk1")
    monkeypatch.setattr(
        "wl_preproc.cli.report.gather_readings",
        lambda *a, **k: _base_readings(disk_error="OSError: [Errno 13] Permission denied"),
    )

    health = build_health(root, prefix=prefix)

    assert health.verdict == "degraded"
    disk = next(r for r in health.readings if r.key == "disk_headroom")
    assert disk.featured is True
    assert "GiB" not in disk.value, "an unmeasured disk must never report a free-space number"
    assert sum(1 for r in health.readings if r.featured) == 1


def test_a_walk_fault_degrades_the_verdict_and_is_its_own_reading(scanned, monkeypatch):
    """`walk_error` gets a reading distinct from `stalled_transfers` rather
    than folded into its value, mirroring `build_report`'s own Stalled
    section: the count line and the "was not fully scanned" line coexist
    there, because a root the walk could not fully list and a root with
    zero stalled transfers are different facts that must not render
    identically.
    """
    root, prefix = scanned("rspwlk1")
    monkeypatch.setattr(
        "wl_preproc.cli.report.gather_readings",
        lambda *a, **k: _base_readings(walk_error="PermissionError: [Errno 13] denied"),
    )

    health = build_health(root, prefix=prefix)

    assert health.verdict == "degraded"
    keys = {r.key for r in health.readings}
    assert {"stalled_transfers", "walk_fault"} <= keys, "the fault must not replace the count"
    fault = next(r for r in health.readings if r.key == "walk_fault")
    assert fault.featured is True
    assert sum(1 for r in health.readings if r.featured) == 1


def test_quarantined_and_stalled_sessions_also_degrade(scanned, monkeypatch):
    """The brief's own given example exercises stuck jobs; spec section 5.1
    names two more conditions this host must react to identically. Proven
    with quarantined alone, then stalled alone, so a bug that wires up only
    one of the two remaining branches is caught rather than passing on the
    strength of the other.
    """
    root, prefix = scanned("rspqst1")

    monkeypatch.setattr(
        "wl_preproc.cli.report.gather_readings",
        lambda *a, **k: _base_readings(quarantined=[{"session_dir": "x"}]),
    )
    quarantined_health = build_health(root, prefix=prefix)
    assert quarantined_health.verdict == "degraded"
    assert next(r for r in quarantined_health.readings if r.key == "quarantined_7d").featured

    monkeypatch.setattr(
        "wl_preproc.cli.report.gather_readings",
        lambda *a, **k: _base_readings(stalled=[(Path("/x"), ["spikeglx"])]),
    )
    stalled_health = build_health(root, prefix=prefix)
    assert stalled_health.verdict == "degraded"
    assert next(r for r in stalled_health.readings if r.key == "stalled_transfers").featured


def test_exactly_one_reading_is_featured_even_with_multiple_faults(scanned, monkeypatch):
    """Plan 10 section 4: more than one featured reading lets wl.works'
    renderer pick one for us instead of us picking. The brief's own
    `test_exactly_one_reading_is_featured` exercises only the fully-healthy
    path (or, incidentally on a low-disk host, exactly one real fault) --
    either way there is at most one bad condition in play, so a naive "mark
    every non-ok reading featured" implementation would happen to look
    correct there too. This forces two genuinely independent conditions bad
    at once — a wedged queue AND an unmeasured disk — so that bug shape is
    actually reachable, and pins the documented priority (disk over stuck
    jobs) as the tiebreak.
    """
    root, prefix = scanned("rspmul1")
    monkeypatch.setattr(
        "wl_preproc.cli.report.gather_readings",
        lambda *a, **k: _base_readings(stale_jobs=5, disk_error="OSError: boom"),
    )

    health = build_health(root, prefix=prefix)

    assert health.verdict == "degraded"
    featured = [r for r in health.readings if r.featured]
    assert len(featured) == 1, f"more than one reading is featured: {featured}"
    assert featured[0].key == "disk_headroom"
    assert any(r.key == "stuck_jobs" for r in health.readings), "the other bad reading still appears"


def test_real_low_scratch_degrades_the_verdict_on_this_host(scanned):
    """Not synthetic: proves the disk-floor rule fires for real, against
    whatever `scratch_headroom` actually measures on the host running this
    suite, rather than only against a hand-built `Readings`. Skips instead
    of asserting a fixed direction, since the floor is a real filesystem
    measurement this test does not control — but on this project's own
    sandbox (confirmed directly: ~630 GiB free against an 800 GiB floor) it
    exercises the real, non-synthetic path every time it runs there.
    """
    root, prefix = scanned("rspflr1")
    free_gib, headroom_ok = scratch_headroom(str(root))
    if headroom_ok:
        pytest.skip(f"this host clears the 800 GiB floor ({free_gib} GiB free)")

    health = build_health(root, prefix=prefix)

    assert health.verdict == "degraded"
    disk = next(r for r in health.readings if r.key == "disk_headroom")
    assert disk.featured is True
    assert "LOW" in disk.value


def test_build_health_does_not_write(scanned, table_snapshot, deep_equal):
    """Same guarantee `gather_readings`/`build_report` carry. `in_transaction`
    cannot detect a write here — DataJoint's `insert()` never touches it, so
    it reads `False` for a writing function and a reading one alike — so
    this snapshots rows before and after, exactly as
    `tests/cli/test_report.py::test_gather_readings_does_not_write` does.
    """
    root, prefix = scanned("rspwrt1")
    from wl_preproc.schema import core, ingest, pipeline

    watched = [ingest.Ingestion, ingest.Quarantine, pipeline.Session, core.AcquisitionSystem]
    before = [table_snapshot(t) for t in watched]

    build_health(root, prefix=prefix)

    after = [table_snapshot(t) for t in watched]
    assert deep_equal(after, before), "build_health wrote or changed at least one row"
