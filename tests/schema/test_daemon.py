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


def test_run_once_reports_what_it_did(daemon_env, prefix):
    """With nothing to compute — `_computed_tables()` is empty in 1c-1 — the
    report must say so in values, not merely carry the right key names. Key
    presence alone was the whole of this assertion until 2026-08-14, and a dict
    literal with three hardcoded zeroes would have satisfied it."""
    report = daemon_env.run_once(prefix=prefix)
    assert set(report) == {"populated", "errors", "stale_jobs_reaped"}
    assert report["populated"] == 0
    assert report["errors"] == []
    assert isinstance(report["stale_jobs_reaped"], int)


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
