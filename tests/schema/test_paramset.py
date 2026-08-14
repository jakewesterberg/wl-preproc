# tests/schema/test_paramset.py
import pytest

@pytest.fixture(scope="module")
def ps(dj_conn, prefix):
    from wl_preproc.schema import paramset, pipeline

    pipeline.activate(prefix=prefix)
    paramset.activate(prefix=prefix)
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


def test_insert1_replace_is_refused_even_when_internally_consistent(ps):
    """insert1(..., replace=True) is a second way to hit the row update1
    blocks: DataJoint dispatches insert/insert1/update1 as three separate
    entries in its class-attribute machinery, so blocking update1 alone
    leaves this open. It is the more dangerous bypass of the two: replace
    overwrites params AND param_hash together, so even a "consistent" replace
    (matching hash for the new params, as built here) must still be refused
    -- there would otherwise be no stale hash and no other trace to detect
    that paramset_idx quietly started meaning something else."""
    import datajoint as dj

    idx = ps.register("clustering", {"drift": "aggressive"})
    new_params = {"drift": "very-aggressive"}
    with pytest.raises((dj.DataJointError, ValueError)):
        ps.ParamSet.insert1(
            {
                "paramset_type": "clustering",
                "paramset_idx": idx,
                "param_hash": ps.content_hash(new_params),
                "params": new_params,
            },
            replace=True,
        )


def test_register_recovers_from_a_concurrent_index_collision(ps, monkeypatch):
    """Simulates the race spec section 11.3 expects: two callers computing the
    same paramset_idx for genuinely DIFFERENT params under the same
    paramset_type. True concurrency is not reproducible in one pytest process
    on one connection, so this reproduces the one thing register() actually
    has to survive -- a real primary-key collision on its own insert -- by
    intercepting _insert_new, the single write register() uses to claim a
    fresh index (split out from register() for exactly this purpose): on the
    first call, it inserts a "winning" row under the SAME idx register() just
    computed, with different params, for real, and only then attempts
    register()'s own insert. That second insert collides for real (a genuine
    MySQL 1062, translated to dj.errors.DuplicateError by DataJoint's own
    error path -- nothing about the exception is fabricated), which is
    exactly the failure a genuine race would produce; who the winner is and
    how it got there is invisible to register() either way. A faithful retry
    must then recompute against a fresh snapshot and land on a new index, not
    just blindly retry the same stale one -- which this proves, because the
    winner's row is real and still occupies the first index afterwards.
    """
    real_insert_new = ps._insert_new
    calls = {"n": 0}

    def flaky_insert_new(row):
        calls["n"] += 1
        if calls["n"] == 1:
            winner = {
                **row,
                "param_hash": ps.content_hash({"drift": "winner"}),
                "params": {"drift": "winner"},
            }
            real_insert_new(winner)  # claims register()'s computed idx first, for real
        return real_insert_new(row)  # collides on attempt 1, succeeds on the retry

    monkeypatch.setattr(ps, "_insert_new", flaky_insert_new)

    idx = ps.register("concurrency-probe", {"drift": "simulated-race"})

    assert calls["n"] == 2, "must retry exactly once after the simulated collision"
    winner_row = (ps.ParamSet & {"paramset_type": "concurrency-probe", "paramset_idx": 0}).fetch1()
    assert winner_row["params"]["drift"] == "winner", "the winner's row must survive untouched"
    loser_row = (ps.ParamSet & {"paramset_type": "concurrency-probe", "paramset_idx": idx}).fetch1()
    assert loser_row["params"]["drift"] == "simulated-race"
    assert idx != 0, "the retry must land on a fresh index, not the one the winner took"
