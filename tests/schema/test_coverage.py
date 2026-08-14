# tests/schema/test_coverage.py
import pytest

@pytest.fixture(scope="module")
def cov(dj_conn, prefix):
    from wl_preproc.schema import core, coverage, pipeline

    pipeline.activate(prefix=prefix)
    core.activate(prefix=prefix)
    coverage.activate(prefix=prefix)
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


def test_coverage_states_are_exactly_full_partial_absent(cov, enum_values):
    """Section 5.2.1: a block partially covered by a probe is the state that
    matters, so `partial` must be representable and distinct from `absent`.

    "Exactly", as the name says, so the enum is parsed and compared as a SET in
    both directions. Until 2026-08-14 this looped `assert state in declared`
    over the raw declaration string, which `enum('fullx','partialx','absentx')`
    satisfies, and which a fourth state added later — the thing that would
    actually collapse `partial` back into a spectrum — could not fail.

    Both coverage tables are checked, not just BlockCoverage: they share
    `_COVERAGE_ENUM` today and that is exactly the assumption worth pinning.
    """
    expected = {"full", "partial", "absent"}
    for table in (cov.BlockCoverage, cov.TrialCoverage):
        declared = table.heading["coverage"].type
        assert enum_values(declared) == expected, (
            f"{table.__name__}.coverage declares {enum_values(declared)}, "
            f"not exactly {expected}"
        )
