import datajoint as dj
import pytest

PREFIX = "t_"


@pytest.fixture(scope="module")
def daemon_env(dj_conn):
    from wl_preproc import daemon
    from wl_preproc.schema import core, pipeline, request

    pipeline.activate(prefix=PREFIX)
    core.activate(prefix=PREFIX)
    request.activate(prefix=PREFIX)
    return daemon


def test_three_part_make_runs_compute_outside_the_transaction(dj_conn):
    """The mechanism section 10 depends on: a 4 h sort in a plain make holds a
    MySQL connection to wait_timeout. Splitting it puts compute outside the
    transaction, with a re-fetch-and-compare inside so integrity survives."""
    schema = dj.Schema(f"{PREFIX}tpm")
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
            phases.append("fetch")
            return ((Src & key).fetch1("v"),)

        def make_compute(self, key, v):
            phases.append("compute")
            return (v * 2,)

        def make_insert(self, key, doubled):
            phases.append("insert")
            self.insert1({**key, "doubled": doubled})

    try:
        Src.insert1({"n": 1, "v": 21}, skip_duplicates=True)
        Derived.populate()
        assert (Derived & "n=1").fetch1("doubled") == 42
        # fetch, compute, then fetch AGAIN inside the transaction, then insert
        assert phases == ["fetch", "compute", "fetch", "insert"]
    finally:
        # In `finally`, not at the end of the happy path: a bare call here
        # leaks `t_tpm` into the shared, session-scoped container for the
        # rest of the run if an assertion above raises first.
        schema.drop()


def test_reaper_clears_a_stale_reservation(daemon_env, dj_conn):
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
    freed = daemon_env.reap_stale_jobs(prefix=PREFIX, older_than_s=0)
    assert isinstance(freed, int)


def test_reaper_frees_a_stale_reservation_not_a_fresh_one(daemon_env, dj_conn):
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
        freed_none = daemon_env.reap_stale_jobs(prefix=PREFIX, older_than_s=3600)
        assert freed_none == 0
        assert jobs.progress()["reserved"] == 1
        assert len(ReaperProbeDerived & key) == 0

        # The real fix: older_than_s=0 -- no grace period -- frees exactly
        # the one stuck reservation.
        freed = daemon_env.reap_stale_jobs(prefix=PREFIX, older_than_s=0)
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


def test_run_once_reports_what_it_did(daemon_env):
    report = daemon_env.run_once(prefix=PREFIX)
    assert set(report) >= {"populated", "errors", "stale_jobs_reaped"}
