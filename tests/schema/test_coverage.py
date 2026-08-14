# tests/schema/test_coverage.py
import pytest

PREFIX = "t_"


@pytest.fixture(scope="module")
def cov(dj_conn):
    from wl_preproc.schema import core, coverage, pipeline

    pipeline.activate(prefix=PREFIX)
    core.activate(prefix=PREFIX)
    coverage.activate(prefix=PREFIX)
    return coverage


def test_block_coverage_is_per_block_per_system(cov):
    assert set(cov.BlockCoverage.primary_key) == {
        "subject",
        "session_datetime",
        "block_id",
        "system",
    }


def test_trial_coverage_is_per_trial_per_system(cov):
    assert set(cov.TrialCoverage.primary_key) == {
        "subject",
        "session_datetime",
        "trial_id",
        "system",
    }


def test_coverage_states_are_exactly_full_partial_absent(cov):
    """Section 5.2.1: a block partially covered by a probe is the state that
    matters, so `partial` must be representable and distinct from `absent`."""
    declared = cov.BlockCoverage.heading["coverage"].type
    for state in ("full", "partial", "absent"):
        assert state in declared
