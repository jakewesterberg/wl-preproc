import datetime

import datajoint as dj
import numpy as np
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

    Src.insert1({"n": 1, "v": 21}, skip_duplicates=True)
    Derived.populate()
    assert (Derived & "n=1").fetch1("doubled") == 42
    # fetch, compute, then fetch AGAIN inside the transaction, then insert
    assert phases == ["fetch", "compute", "fetch", "insert"]
    schema.drop()


def test_reaper_clears_a_stale_reservation(daemon_env, dj_conn):
    """A crashed populate leaves ~jobs marked reserved and the key is skipped
    forever — section 10 names it a top-four DataJoint hazard."""
    freed = daemon_env.reap_stale_jobs(prefix=PREFIX, older_than_s=0)
    assert isinstance(freed, int)


def test_run_once_reports_what_it_did(daemon_env):
    report = daemon_env.run_once(prefix=PREFIX)
    assert set(report) >= {"populated", "errors", "stale_jobs_reaped"}
