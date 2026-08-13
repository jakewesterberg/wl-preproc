# tests/schema/test_paramset.py
import pytest

PREFIX = "t_"


@pytest.fixture(scope="module")
def ps(dj_conn):
    from wl_preproc.schema import paramset, pipeline

    pipeline.activate(prefix=PREFIX)
    paramset.activate(prefix=PREFIX)
    return paramset


def test_registering_the_same_params_twice_returns_one_index(ps):
    """Content-hash uniqueness (section 5.3): an identical paramset is the same
    paramset, so registration is idempotent rather than a check-then-write."""
    a = ps.register("clustering", {"drift": "aggressive", "n_blocks": 5})
    b = ps.register("clustering", {"n_blocks": 5, "drift": "aggressive"})
    assert a == b, "key order changed the hash; params must be canonicalised"


def test_different_params_get_different_indices(ps):
    a = ps.register("clustering", {"drift": "aggressive"})
    b = ps.register("clustering", {"drift": "conservative"})
    assert a != b


def test_paramsets_are_immutable_once_registered(ps):
    """An edit yields a different hash, which is a NEW paramset. In-place
    modification is refused structurally rather than by convention."""
    import datajoint as dj

    idx = ps.register("clustering", {"drift": "aggressive"})
    with pytest.raises((dj.DataJointError, ValueError)):
        ps.ParamSet.update1(
            {"paramset_type": "clustering", "paramset_idx": idx, "params": {"drift": "x"}}
        )


def test_the_hash_is_recorded_so_provenance_survives(ps):
    idx = ps.register("clustering", {"drift": "aggressive"})
    row = (ps.ParamSet & {"paramset_type": "clustering", "paramset_idx": idx}).fetch1()
    assert row["param_hash"]
    assert row["params"]["drift"] == "aggressive"
