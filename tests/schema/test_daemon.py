import datajoint as dj
import pytest


@pytest.fixture(scope="module")
def daemon_env(dj_conn, prefix):
    from wl_preproc import daemon
    from wl_preproc.schema import core, pipeline, request

    pipeline.activate(prefix=prefix)
    core.activate(prefix=prefix)
    request.activate(prefix=prefix)
    return daemon


def test_three_part_make_runs_compute_outside_the_transaction(dj_conn, prefix):
    """The mechanism section 10 depends on: a 4 h sort in a plain make holds a
    MySQL connection to wait_timeout. Splitting it puts compute outside the
    transaction, with a re-fetch-and-compare inside so integrity survives.

    Each phase records `self.connection.in_transaction` alongside its name.
    Until 2026-08-14 this asserted the phase ORDER alone — `["fetch",
    "compute", "fetch", "insert"]` — which the three-part protocol produces
    identically whether or not a transaction is open around any of it. The
    order is a fact about the generator protocol; it cannot distinguish the
    property this test is named for, and would have passed unchanged if
    DataJoint had wrapped the whole sequence in one transaction, which is
    exactly the regression that matters here. This is the fourth test on this
    branch that passed while proving nothing; the other three were each hiding
    a real defect.
    """
    schema = dj.Schema(f"{prefix}tpm")
    phases = []

    @schema
    class Src(dj.Manual):
        definition = """
        # source for the three-part make probe
        n : int
        ---
        v : int
        """

    @schema
    class Derived(dj.Computed):
        definition = """
        # computed via the three-part make
        -> Src
        ---
        doubled : int
        """

        def make_fetch(self, key):
            phases.append(("fetch", self.connection.in_transaction))
            return ((Src & key).fetch1("v"),)

        def make_compute(self, key, v):
            phases.append(("compute", self.connection.in_transaction))
            return (v * 2,)

        def make_insert(self, key, doubled):
            phases.append(("insert", self.connection.in_transaction))
            self.insert1({**key, "doubled": doubled})

    try:
        Src.insert1({"n": 1, "v": 21}, skip_duplicates=True)
        Derived.populate()
        assert (Derived & "n=1").fetch1("doubled") == 42
        # fetch and compute OUTSIDE any transaction -- the whole point, since
        # compute is the 4 h one -- then fetch AGAIN and insert inside it.
        assert phases == [
            ("fetch", False),
            ("compute", False),
            ("fetch", True),
            ("insert", True),
        ]
    finally:
        # In `finally`, not at the end of the happy path: a bare call here
        # leaks `t_tpm` into the shared, session-scoped container for the
        # rest of the run if an assertion above raises first.
        schema.drop()


def test_a_plain_make_runs_entirely_inside_the_transaction(dj_conn, prefix):
    """The contrast case, without which the test above proves only that
    `in_transaction` returns *something*. A plain `make` is what the three-part
    form exists to avoid: it runs inside the transaction, so a long compute
    holds a MySQL connection open for its whole duration and hits
    `wait_timeout` — section 10's hazard, stated here as an executable fact
    about this DataJoint version rather than a claim in a docstring.
    """
    schema = dj.Schema(f"{prefix}pm")
    phases = []

    @schema
    class PlainSrc(dj.Manual):
        definition = """
        # source for the plain-make contrast probe
        n : int
        ---
        v : int
        """

    @schema
    class PlainDerived(dj.Computed):
        definition = """
        # computed via a plain make
        -> PlainSrc
        ---
        doubled : int
        """

        def make(self, key):
            phases.append(("make", self.connection.in_transaction))
            self.insert1({**key, "doubled": (PlainSrc & key).fetch1("v") * 2})

    try:
        PlainSrc.insert1({"n": 1, "v": 21}, skip_duplicates=True)
        PlainDerived.populate()
        assert (PlainDerived & "n=1").fetch1("doubled") == 42
        assert phases == [("make", True)]
    finally:
        schema.drop()


def test_reaper_clears_a_stale_reservation(daemon_env, dj_conn, prefix):
    """A crashed populate leaves ~jobs marked reserved and the key is skipped
    forever — section 10 names it a top-four DataJoint hazard.

    This only exercises the zero-computed-tables path: `daemon_env` activates
    no table richer than `dj.Manual`, so `reap_stale_jobs` finds no job
    tables at all and the assertion below is "does not crash, returns an
    int" — a real regression guard for the `_activated_job_tables` iteration
    itself (an unactivated schema, or a schema with no Computed/Imported
    table, must not raise), but not a guard on the reaping logic. See
    `test_reaper_frees_a_stale_reservation_not_a_fresh_one` below for that.
    """
    freed = daemon_env.reap_stale_jobs(prefix=prefix, older_than_s=0)
    assert isinstance(freed, int)


def test_reaper_frees_a_stale_reservation_not_a_fresh_one(daemon_env, dj_conn, prefix):
    """The real mechanism, end to end — not just "returns an int" (see
    `test_reaper_clears_a_stale_reservation` above, which only exercises the
    zero-computed-tables path because nothing computes in 1c-1).

    `daemon.reap_stale_jobs` is hardcoded to sweep exactly `core`, `coverage`,
    `paramset`, `request` (see its docstring) — it does not take a schema
    list — so a throwaway `dj.Computed` table has to live inside one of
    those four schemas, not a standalone probe schema, to be seen by it at
    all. `core` is already activated by `daemon_env`.

    Also proves the negative direction: `older_than_s` large enough that the
    reservation is not actually stale must free nothing and must not touch
    it. That assertion is what stops a future "simplification" back to
    `Job.refresh(orphan_timeout=older_than_s)` — whose `orphan_timeout=0`
    gate silently no-ops for exactly the value this file's other reaper test
    uses, which is the regression this test exists to make impossible to
    reintroduce unnoticed — or any other change that frees a reservation
    that is not actually stale.
    """
    from wl_preproc.schema import core

    @core.schema
    class ReaperProbeSource(dj.Manual):
        definition = """
        # throwaway source for the reaper end-to-end probe
        n : int
        ---
        v : int
        """

    @core.schema
    class ReaperProbeDerived(dj.Computed):
        definition = """
        # throwaway computed table for the reaper end-to-end probe
        -> ReaperProbeSource
        ---
        doubled : int
        """

        def make(self, key):
            v = (ReaperProbeSource & key).fetch1("v")
            self.insert1({**key, "doubled": v * 2})

    try:
        ReaperProbeSource.insert1({"n": 1, "v": 21})
        jobs = ReaperProbeDerived.jobs
        jobs.refresh()
        key = {"n": 1}
        assert jobs.reserve(key), "expected the one pending job to reserve cleanly"
        assert jobs.progress()["reserved"] == 1

        # Simulates the crash: the reservation is never completed or errored.
        # A normal populate() does not recover it -- confirming the hazard is
        # real here, not just assuming the fix works because it looks right.
        ReaperProbeDerived.populate(reserve_jobs=True)
        assert len(ReaperProbeDerived & key) == 0
        assert jobs.progress()["reserved"] == 1

        # Negative direction: the reservation is milliseconds old, nowhere
        # near 3600s stale, so a generous threshold must free nothing and
        # must not steal it.
        freed_none = daemon_env.reap_stale_jobs(prefix=prefix, older_than_s=3600)
        assert freed_none == 0
        assert jobs.progress()["reserved"] == 1
        assert len(ReaperProbeDerived & key) == 0

        # The real fix: older_than_s=0 -- no grace period -- frees exactly
        # the one stuck reservation.
        freed = daemon_env.reap_stale_jobs(prefix=prefix, older_than_s=0)
        assert freed == 1
        assert jobs.progress()["reserved"] == 0
        assert jobs.progress()["pending"] == 1

        # And the freed key actually computes.
        ReaperProbeDerived.populate(reserve_jobs=True)
        assert (ReaperProbeDerived & key).fetch1("doubled") == 42
    finally:
        # In `finally`, not at the end of the happy path, for the same
        # reason as the three-part-make test above -- and there are three
        # objects to clean up here, not one: the job queue table DataJoint
        # creates the first time `.jobs` is accessed, then the computed
        # table itself, then its source, in FK-safe (child-before-parent)
        # order. `drop_quick()`/`Job.drop()` (which wraps `drop_quick()`) ask
        # no interactive confirmation and do not cascade -- appropriate here
        # since nothing else in the shared container references either
        # table.
        ReaperProbeDerived.jobs.drop()
        ReaperProbeDerived.drop_quick()
        ReaperProbeSource.drop_quick()


def test_run_once_reports_what_it_did(daemon_env, prefix, tmp_path):
    """The report must say what happened in VALUES, not merely carry the right
    key names. Key presence alone was the whole of this assertion until
    2026-08-14, and a dict literal with three hardcoded zeroes would have
    satisfied it.

    Until 1c-4 the check for that was `populated == 0`, on the premise that
    `_computed_tables()` was empty. That premise is gone — the daemon computes
    now — and `== 0` would have gone on passing on a hardcoded zero for the
    rest of the project's life.

    So it is checked in BOTH directions, and against a BASELINE rather than
    against zero, because the shared test database is not drainable to zero: a
    system whose files are all rejected produces no `Segment` row, so its key
    stays outstanding and is re-attempted every pass. That is deliberate — it
    is what lets a corrected file be picked up with no manual step — and it
    means the invariant here is *steady state is stable*, not *steady state is
    empty*. See `core.Segment.key_source`.
    """
    import datetime

    from wl_preproc.schema import core, ingest, pipeline
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session

    # Whatever earlier tests landed is not this test's subject: drain it, then
    # measure the steady state twice to establish that it IS steady.
    daemon_env.run_once(prefix=prefix)
    first = daemon_env.run_once(prefix=prefix)
    baseline = daemon_env.run_once(prefix=prefix)

    assert set(baseline) == {"populated", "errors", "stale_jobs_reaped"}
    assert baseline["populated"] == first["populated"], (
        "the daemon does not reach a steady state: two consecutive idle passes "
        f"computed {first['populated']} then {baseline['populated']} keys"
    )
    assert baseline["errors"] == []
    assert isinstance(baseline["stale_jobs_reaped"], int)

    recipe = RECIPES["ci"]
    generate_session(tmp_path, recipe)
    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": recipe.subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    session_key = {
        "subject": recipe.subject,
        "session_datetime": datetime.datetime(2027, 3, 22, 9, 0),
    }
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    ingest.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": datetime.datetime(2027, 3, 22, 19, 0),
            "session_dir": str(tmp_path / recipe.session_id),
            "integrity": "verified",
            "topology": {system: "present" for system in recipe.systems},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    core.AcquisitionSystem.insert(
        [{**session_key, "system": system} for system in recipe.systems],
        skip_duplicates=True,
    )

    did_work = daemon_env.run_once(prefix=prefix)
    assert did_work["errors"] == [], did_work["errors"]
    assert did_work["populated"] > baseline["populated"], (
        "landing a session computed no more keys than an idle pass"
    )

    # And back to the same steady state, not to some new one.
    assert daemon_env.run_once(prefix=prefix)["populated"] == baseline["populated"]


# Every probe below is declared inside `core.schema`, not a standalone one:
# `run_once` and `reap_stale_jobs` sweep `core`/`coverage`/`paramset`/`request`
# by name and take no schema list, so a probe anywhere else is invisible to
# them -- the same constraint
# `test_reaper_frees_a_stale_reservation_not_a_fresh_one` documents. `core` is
# the one `daemon_env` activates. Each is dropped in a `finally`, child before
# parent, with the job queue table DataJoint creates on first `.jobs` access
# dropped first.


def test_run_once_counts_keys_computed_not_tables_attempted(
    daemon_env, dj_conn, monkeypatch, prefix
):
    """`populated` counted *tables attempted* (`populated += 1` per table in the
    loop), so one table computing three keys reported 1 and a table computing
    none also reported 1. 1c-2's daily report is specified to be built on this
    dict."""
    from wl_preproc.schema import core

    @core.schema
    class RunOnceOkSource(dj.Manual):
        definition = """
        # throwaway source for the run_once accounting probe
        n : int
        ---
        v : int
        """

    @core.schema
    class RunOnceOkDerived(dj.Computed):
        definition = """
        # throwaway computed table for the run_once accounting probe
        -> RunOnceOkSource
        ---
        doubled : int
        """

        def make(self, key):
            v = (RunOnceOkSource & key).fetch1("v")
            self.insert1({**key, "doubled": v * 2})

    try:
        RunOnceOkSource.insert(({"n": 1, "v": 1}, {"n": 2, "v": 2}, {"n": 3, "v": 3}))
        monkeypatch.setattr(daemon_env, "_computed_tables", lambda: [RunOnceOkDerived])

        report = daemon_env.run_once(prefix=prefix)

        assert report["populated"] == 3, "must count keys computed, not tables attempted"
        assert report["errors"] == []
        assert len(RunOnceOkDerived()) == 3
    finally:
        RunOnceOkDerived.jobs.drop()
        RunOnceOkDerived.drop_quick()
        RunOnceOkSource.drop_quick()


def test_run_once_reports_the_per_key_failures_populate_swallows(
    daemon_env, dj_conn, monkeypatch, prefix
):
    """`run_once` could never report an error. `populate()` returns
    `{"success_count", "error_list"}` and, with `suppress_errors=True`, a
    per-key failure lands in `error_list` instead of raising — but the return
    value was discarded, so `errors` only ever collected exceptions that
    escaped, which is precisely what `suppress_errors=True` exists to prevent.
    A stage failing on every one of its keys printed `errors: none`. Same class
    as the doctor's hardcoded `ok=True` already ruled Important.
    """
    from wl_preproc.schema import core

    @core.schema
    class RunOnceBoomSource(dj.Manual):
        definition = """
        # throwaway source for the run_once error-reporting probe
        n : int
        ---
        v : int
        """

    @core.schema
    class RunOnceBoomDerived(dj.Computed):
        definition = """
        # throwaway computed table that fails on every key
        -> RunOnceBoomSource
        ---
        doubled : int
        """

        def make(self, key):
            raise RuntimeError(f"probe stage failure on n={key['n']}")

    try:
        RunOnceBoomSource.insert(({"n": 1, "v": 1}, {"n": 2, "v": 2}))
        monkeypatch.setattr(daemon_env, "_computed_tables", lambda: [RunOnceBoomDerived])

        report = daemon_env.run_once(prefix=prefix)

        assert report["populated"] == 0
        assert len(report["errors"]) == 2, f"expected one error per key, got {report['errors']}"
        assert all("probe stage failure" in err for err in report["errors"])
        # the failing table is named, so a daily report says WHICH stage broke
        assert all("RunOnceBoomDerived" in err for err in report["errors"])
    finally:
        RunOnceBoomDerived.jobs.drop()
        RunOnceBoomDerived.drop_quick()
        RunOnceBoomSource.drop_quick()


def test_doctor_reports_each_stale_job_state_and_reaps_nothing(
    daemon_env, dj_conn, monkeypatch, capsys, prefix
):
    """`wlpp doctor`'s three stale-job states, and its read-only guarantee.

    A previous fix round established all of this — the shipped check called
    `reap_stale_jobs` directly, so running the diagnostic deleted and re-pended
    job rows, and an earlier draft reported a hardcoded all-clear when nothing
    had been inspected at all — and none of the proof shipped with it. The
    found-N state uses a genuinely backdated reservation rather than a
    monkeypatched counter, so `count_stale_jobs`'s real 24 h default decides
    it.
    """
    from wl_preproc.cli import doctor
    from wl_preproc.schema import core

    @core.schema
    class DoctorProbeSource(dj.Manual):
        definition = """
        # throwaway source for the doctor stale-job probe
        n : int
        ---
        v : int
        """

    @core.schema
    class DoctorProbeDerived(dj.Computed):
        definition = """
        # throwaway computed table for the doctor stale-job probe
        -> DoctorProbeSource
        ---
        doubled : int
        """

        def make(self, key):
            v = (DoctorProbeSource & key).fetch1("v")
            self.insert1({**key, "doubled": v * 2})

    try:
        DoctorProbeSource.insert1({"n": 1, "v": 21})
        jobs = DoctorProbeDerived.jobs

        # State: checked, found nothing. Distinct from "not checked" below.
        doctor.run_checks()
        assert "0 stale reservation(s) found" in capsys.readouterr().out

        jobs.refresh()
        assert jobs.reserve({"n": 1}), "expected the one pending job to reserve cleanly"
        # Backdate past the 24 h default, so the REAL threshold classifies it —
        # a fresh reservation is correctly not stale, which is the negative
        # direction test_reaper_frees_a_stale_reservation_not_a_fresh_one pins.
        dj_conn.query(
            f"UPDATE {jobs.full_table_name} "
            "SET reserved_time = reserved_time - INTERVAL 48 HOUR"
        )

        # State: checked, found N. And FAIL, not a note.
        failures = doctor.run_checks()
        assert "1 stale reservation(s) found" in capsys.readouterr().out
        assert "stale jobs" in failures

        # The read-only guarantee: a diagnostic must not mutate what it inspects.
        assert jobs.progress()["reserved"] == 1
        assert daemon_env.count_stale_jobs(prefix=prefix, older_than_s=0) == 1

        # State: not checked. `count_stale_jobs` returns None -- not 0 -- when no
        # schema is activated in this process, which is every bare `wlpp doctor`
        # today. Produced through the real mechanism, by making the activation
        # probe report what a bare invocation would.
        monkeypatch.setattr(daemon_env, "_any_schema_activated", lambda: False)
        failures = doctor.run_checks()
        out = capsys.readouterr().out
        assert "not checked: no schema activated in this process" in out
        assert "stale jobs" not in failures, (
            "nothing was inspected, so this must not be reported as a failure -- "
            "nor as a fabricated all-clear"
        )
    finally:
        DoctorProbeDerived.jobs.drop()
        DoctorProbeDerived.drop_quick()
        DoctorProbeSource.drop_quick()


def test_computed_tables_is_no_longer_empty():
    """1c-1 left this returning [] with a comment naming 1c-4 as what extends
    it. The ordering lives there so later phases extend one list rather than
    inventing their own traversal."""
    from wl_preproc import daemon

    assert daemon._computed_tables() != []


def test_computed_tables_are_in_dependency_order():
    """Ordering is load-bearing rather than tidy. `Segment.make()` needs its
    system's rate to already exist, and `BlockCoverage.make()` needs the
    segments — and neither dependency is expressed as a `key_source`, precisely
    so that a system with no fit still records why (see `Segment.key_source`).
    So this list IS the ordering, and nothing else enforces it.

    `TrialCoverage` was added to the list in 1c-5's fix round and is asserted
    after `Segment` for exactly `BlockCoverage`'s reason: its `make()`
    intersects a trial's interval with `core.Segment`'s extents, so running it
    first would record a session as less covered than it is. That is the ONE
    ordering this test adds; its other dependency, `pipeline.trial.Trial`, is
    not in this list at all and cannot be checked here — `run_once` fills it
    from `_populate_event_stage()` before this whole loop, which
    `test_the_daemon_path_alone_lifts_a_good_session_off_tier_d` is what
    actually proves.
    """
    from wl_preproc import daemon
    from wl_preproc.schema import core, coverage, timebase

    names = [table.__name__ for table in daemon._computed_tables()]

    assert names.index(timebase.SystemTimebase.__name__) < names.index(core.Segment.__name__)
    assert names.index(core.Segment.__name__) < names.index(coverage.BlockCoverage.__name__)
    assert names.index(core.Segment.__name__) < names.index(coverage.TrialCoverage.__name__)
    assert names.index(core.Segment.__name__) < names.index(
        timebase.TimingProvenance.__name__
    )


def test_every_computed_table_is_a_daemon_stage():
    """A `dj.Computed` this project declares and `_computed_tables()` does not
    name never runs in production, and nothing else says so.

    `coverage.TrialCoverage` was absent for the whole of 1c-5, so
    `pipeline.trial.Trial` was never filled outside tests,
    `TimingProvenance.make()` counted zero trials from the codes against a
    non-zero task file, and `resolve_tier` returned D for every session in
    production. It is the same hand-maintained-list shape `daemon.py`'s
    `_PROJECT_SCHEMA_MODULES` comment records going stale four times over, and
    `activate_all`'s docstring once more. That tuple already has a discovering
    guard -- `test_every_schema_module_is_swept_for_job_tables`, right above --
    and the list of computed tables had none; every fixture in this suite that
    needed a trial list called `events.populate_session` by hand, so no test
    exercised the daemon's own path.

    **It discovers rather than lists.** It walks `daemon._project_schema_
    modules()` and keeps every class that is both a `dj.Computed` subclass and
    DEFINED in that module (`obj.__module__` check) rather than imported into
    it — a hand-written list of the hand-written list would fix nothing.

    **Absence is read as a defect, never as an intention.** A table that
    genuinely belongs outside the daemon's traversal has to be named in
    `daemon._COMPUTED_TABLES_EXEMPT`, and an exemption naming something no
    module declares fails the first assertion, so an exemption cannot outlive
    its table either.

    **What this test actually produces today:** five discovered tables —
    `core.Segment`, `coverage.BlockCoverage`, `coverage.TrialCoverage`,
    `timebase.SystemTimebase`, `timebase.TimingProvenance` — an empty
    exemption set, and set equality with `_computed_tables()`. Confirmed to
    fail with `TrialCoverage` removed from `_computed_tables()` (the state
    this branch shipped) and to fail on a stale exemption.

    **Scope, stated exactly:** `dj.Computed` only, across the eight modules
    `_PROJECT_SCHEMA_MODULES` names. `dj.Imported` is not swept and neither is
    `pipeline` — element-event's Imported tables live there, this project fills
    them by direct insert rather than by `populate()`, and `schema/events.py`'s
    module docstring records why. Neither is a claim this test makes.
    """
    from wl_preproc import daemon

    short_name = {module.__name__: name for name, module in daemon._project_schema_modules()}

    discovered = {
        f"{name}.{attribute}"
        for name, module in daemon._project_schema_modules()
        for attribute, obj in vars(module).items()
        if isinstance(obj, type)
        and issubclass(obj, dj.Computed)
        and obj.__module__ == module.__name__
    }
    registered = {
        f"{short_name.get(table.__module__, table.__module__)}.{table.__name__}"
        for table in daemon._computed_tables()
    }
    exempt = set(daemon._COMPUTED_TABLES_EXEMPT)

    assert exempt <= discovered, (
        "_COMPUTED_TABLES_EXEMPT names something no schema module declares as a "
        f"dj.Computed: {sorted(exempt - discovered)}"
    )
    assert registered == discovered - exempt, (
        "every dj.Computed in this project's schema modules must be a daemon "
        "stage, or be exempted by name in daemon._COMPUTED_TABLES_EXEMPT. "
        f"Declared but never populated: {sorted(discovered - exempt - registered)}. "
        f"Populated but not declared here: {sorted(registered - discovered)}."
    )


def test_every_schema_module_is_swept_for_job_tables():
    """`_PROJECT_SCHEMAS` was a hand-listed tuple of four. This project has
    already been bitten by exactly that shape once: `tests/schema/
    test_guardrails.py` records that a hardcoded module tuple silently swept
    nothing from `ingest` when it landed as a fifth module, while the suite
    stayed green.

    It bit again here. 1c-4 adds `timebase`, whose two tables are the FIRST
    Computed tables this project has declared — so a hand-listed tuple missing
    it would mean the one schema that can actually have `~jobs` tables is the
    one never checked for stale reservations. Discovered by construction now,
    so a sixth module needs no one to remember this file.
    """
    import pkgutil

    import wl_preproc.schema
    from wl_preproc import daemon

    expected = {
        name
        for _finder, name, _ispkg in pkgutil.iter_modules(wl_preproc.schema.__path__)
        if not name.startswith("_") and name != "pipeline"
    }
    assert {name for name, _schema in daemon._project_schemas()} == expected


def test_count_stale_jobs_sees_the_jobs_tables_it_reads(dj_conn, prefix, tmp_path):
    """This is the FIRST Computed table this project has ever declared, which
    makes live a path that was inert: `count_stale_jobs` reads DataJoint's
    internal `~jobs` tables, and the 1c-2 handoff records that the report's
    write-detection snapshot does not cover them. A snapshot that silently
    misses a table is worse than no snapshot, because it reads as coverage.

    A reservation is left behind deliberately and must be BOTH counted by
    `count_stale_jobs` AND visible in `job_tables()` — the accessor the report's
    snapshot now uses. Counted but invisible is the exact shape of the gap.
    """
    from wl_preproc import daemon
    from wl_preproc.schema import timebase

    timebase.activate(prefix=prefix)

    # A throwaway Computed table inside the newly-discovered schema, so the
    # reservation exists in exactly the module a hand-listed tuple would miss.
    @timebase.schema
    class JobsProbeSource(dj.Manual):
        definition = """
        # throwaway source for the ~jobs visibility probe
        n : int
        """

    @timebase.schema
    class JobsProbeDerived(dj.Computed):
        definition = """
        # throwaway computed table for the ~jobs visibility probe
        -> JobsProbeSource
        ---
        doubled : int
        """

        def make(self, key):
            self.insert1({**key, "doubled": key["n"] * 2})

    try:
        JobsProbeSource.insert1({"n": 1})
        jobs = JobsProbeDerived.jobs
        jobs.refresh()
        assert jobs.reserve({"n": 1}), "expected the one pending job to reserve cleanly"

        assert daemon.count_stale_jobs(prefix=prefix, older_than_s=0) >= 1

        # The gap this closes: counted but invisible. `job_tables()` is what
        # the report's write-detection snapshot covers, so a reservation the
        # counter can see and the snapshot cannot is the exact failure shape.
        visible = daemon.job_tables()
        assert visible, "count_stale_jobs read job tables the snapshot cannot see"
        assert any(len(table) >= 1 for table in visible)
    finally:
        JobsProbeDerived.drop_quick()
        JobsProbeSource.drop_quick()


def test_the_daemon_path_alone_lifts_a_good_session_off_tier_d(
    daemon_env, dj_conn, prefix, tmp_path
):
    """The Critical itself: a clean session must not be quarantined at D by the
    daemon's own path.

    Every other test of the trial list and the tier calls
    `events.populate_session` BY HAND first -- `tests/schema/test_coverage.py`,
    `tests/schema/test_events.py`, and `tests/schema/test_timebase.py`'s
    `provenance_session` fixture all do -- which is exactly why nothing caught
    that production called it nowhere: `wl_preproc.schema.events.
    populate_session` had zero non-test callers, and `coverage.TrialCoverage`
    was absent from `daemon._computed_tables()`.

    So this test lands only what the ingest watcher writes -- `Session`,
    `Ingestion`, `AcquisitionSystem`, plus the `Lab`/`Subject` those need --
    and then calls **`daemon.run_once` and nothing else**. No populate and no
    `populate_session` call appears below.

    **What it actually produces, read off a live run rather than assumed.**
    One `run_once` pass over the `ci` recipe (4 trials; systems `syncbox`,
    `spikeglx`, `bcam`) yields 4 `pipeline.trial.Trial` rows, 12
    `coverage.TrialCoverage` rows, `trial_count_agreement` true, and tier
    **A** -- the `n_full_code_records >= 2` branch, since `ci` carries both the
    Pi and an NI with its own code lines, and no D guard trips. Tier A rather
    than merely "not D" is asserted because a bare `!= "D"` would also pass on
    a session that reached B or C for the wrong reason.

    **Confirmed to fail in both halves of the defect, separately:** with
    `_populate_event_stage()` removed from `run_once`, this session produces 0
    trials, 0 `TrialCoverage` rows, `trial_count_agreement` false and tier
    `D` -- the exact production verdict this fix exists to end. With only
    `coverage.TrialCoverage` removed from `_computed_tables()`, the trial list
    is built but its coverage rows stay at 0.

    `activate_all` runs first because this test reads `coverage`/`timebase`/
    `ingest` tables that `daemon_env` alone does not bind, and it must insert
    into `ingest.Ingestion` before `run_once` would bind them itself.
    """
    import datetime

    from wl_preproc.schema import core, coverage, ingest, pipeline, timebase
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session

    daemon_env.activate_all(prefix=prefix)

    recipe = RECIPES["ci"]
    generate_session(tmp_path, recipe)

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": recipe.subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    session_key = {
        "subject": recipe.subject,
        "session_datetime": datetime.datetime(2027, 3, 27, 9, 0),
    }
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    ingest.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": datetime.datetime(2027, 3, 27, 19, 0),
            "session_dir": str(tmp_path / recipe.session_id),
            "integrity": "verified",
            "topology": {system: "present" for system in recipe.systems},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    core.AcquisitionSystem.insert(
        [{**session_key, "system": system} for system in recipe.systems],
        skip_duplicates=True,
    )

    report = daemon_env.run_once(prefix=prefix)

    n_trials = sum(block.n_trials for block in recipe.blocks)
    assert len(pipeline.trial.Trial & session_key) == n_trials, report["errors"]
    assert len(coverage.TrialCoverage & session_key) == n_trials * len(recipe.systems)

    row = (timebase.TimingProvenance & session_key).fetch1()
    assert row["trial_count_agreement"], row
    assert row["tier"] != "D", (
        "a clean session is still quarantined by the daemon's own path, which "
        f"is the Critical this fix exists to close: {row}"
    )
    # Stricter than `!= "D"` deliberately: B or C here would mean the session
    # reached a passing tier for the wrong reason.
    assert row["tier"] == "A", row
