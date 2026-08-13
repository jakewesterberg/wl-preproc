# tests/schema/test_pipeline.py
import datajoint as dj


def test_all_four_elements_activate(dj_conn):
    from wl_preproc.schema import pipeline

    pipeline.activate(prefix="t_")
    for name in ("lab", "subject", "session", "event"):
        assert getattr(pipeline, name) is not None, name

    # `trial` lives in its own submodule with its own activate() call, separate
    # from `event` -- the easy mistake this plan calls out, and Task 4 keys
    # TrialCoverage off pipeline.trial.Trial. A non-None module attribute is a
    # weaker claim than a resolvable table, so check it resolves to a real
    # DataJoint table class.
    assert issubclass(pipeline.trial.Trial, dj.Table)


def test_session_table_exists_and_is_keyed_as_elements_expects(dj_conn):
    from wl_preproc.schema import pipeline

    pipeline.activate(prefix="t_")
    assert set(pipeline.session.Session.primary_key) == {"subject", "session_datetime"}


def test_experimenter_is_supplied_to_element_session(dj_conn):
    """element-session references `Experimenter`; element-lab provides `User`.
    Supplying the name is exactly what a linking module is for, and without it
    activation fails with an unresolved foreign key."""
    from wl_preproc.schema import pipeline

    assert pipeline.Experimenter is pipeline.lab.User


def test_array_ephys_is_not_activated(dj_conn):
    """Phase 2 precondition: element-array-ephys declares 14 longblob attributes
    that silently destroy array data under DataJoint 2.x (upstream issue #230).
    It must not appear until that is fixed."""
    from wl_preproc.schema import pipeline

    assert not hasattr(pipeline, "ephys")
    assert not hasattr(pipeline, "probe")


def test_activation_is_idempotent(dj_conn, monkeypatch):
    """Activating an already-activated prefix must be a no-op.

    `_activated` is a module-level set that lives for the whole process, and
    this suite's other tests also activate "t_" -- by the time this test
    runs, that may already have happened. So two bare `activate()` calls with
    nothing raising doesn't prove *this* test exercised the real-activation-
    then-no-op transition; it may just be two early returns, which would pass
    just as silently if the guard were broken. Instead, spy on `lab.activate`,
    the first Element call inside `pipeline.activate()` after the `_activated`
    guard: whichever call -- this test's first, or an earlier test's -- does
    the real activation, a *second* call for the same prefix must not reach
    `lab.activate()` again. This is order-independent: it holds no matter
    which test in the suite happens to activate "t_" first.
    """
    from wl_preproc.schema import pipeline

    pipeline.activate(prefix="t_")  # real activation, or already done -- either is fine here

    calls = []
    monkeypatch.setattr(pipeline.lab, "activate", lambda *a, **k: calls.append((a, k)))

    pipeline.activate(prefix="t_")

    assert calls == []
