# tests/schema/test_pipeline.py


def test_all_four_elements_activate(dj_conn, prefix):
    from wl_preproc.schema import pipeline

    pipeline.activate(prefix=prefix)
    for name in ("lab", "subject", "session", "event"):
        assert getattr(pipeline, name) is not None, name

    # `trial` lives in its own submodule with its own activate() call, separate
    # from `event` -- the easy mistake this plan calls out, and Task 4 keys
    # TrialCoverage off pipeline.trial.Trial.
    #
    # The assertion is the primary key, NOT `issubclass(..., dj.Table)`, which
    # this checked until 2026-08-14: a DataJoint table class is a subclass of
    # dj.Table from the moment its module is imported, activated or not, so
    # that assertion was true before `activate()` ran and could never fail for
    # the dropped-activation regression it exists to catch. The primary key is
    # only resolvable once the table is declared against a database, so it
    # actually depends on the activation above.
    assert set(pipeline.trial.Trial.primary_key) == {
        "subject",
        "session_datetime",
        "trial_id",
    }


def test_session_table_exists_and_is_keyed_as_elements_expects(dj_conn, prefix):
    from wl_preproc.schema import pipeline

    pipeline.activate(prefix=prefix)
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


def test_the_default_prefix_builds_the_schema_names_the_spec_names(monkeypatch):
    """Design spec section 3 names `wlpp_lab`, `wlpp_subject`, `wlpp_session`,
    `wlpp_event`, `wlpp_coverage`, `wlpp_request`, `wlpp_paramset`.

    Every `activate()` interpolates `f"{prefix}lab"` with **no separator in the
    f-string**, so the prefix has to carry one. It did not until 2026-08-14 —
    the default was `"wlpp"`, which builds `wlpplab`, `wlppsubject`,
    `wlppcore` and six more. Nothing on this branch could see it: the whole
    suite runs at `prefix="t_"`, which supplies its own separator, so the
    production default was never once exercised. The first `wlpp daemon` in the
    lab would have created nine wrongly-named databases, and renaming a schema
    after that is a migration on live foreign-keyed tables.

    This asserts the *names*, not just the constant, and does so without
    activating a second prefix — one prefix per process is a standing
    constraint and a real second activation raises. Every `activate()` this
    reaches is replaced by a recorder, so the database names below are the f-
    strings the production code actually builds, not a restatement of them.
    """
    from wl_preproc.schema import DEFAULT_PREFIX, core, coverage, paramset, pipeline, request

    assert DEFAULT_PREFIX == "wlpp_", (
        "the prefix must carry its own separator; the f-strings in each "
        "activate() do not supply one"
    )

    built: list[str] = []

    # pipeline.activate() fans into five Element activate() calls; each takes
    # the database name as its first positional argument (trial takes two:
    # its own, then the event schema it links to).
    for element in ("lab", "subject", "session", "event", "trial"):
        monkeypatch.setattr(
            getattr(pipeline, element),
            "activate",
            lambda *args, **kwargs: built.extend(a for a in args if isinstance(a, str)),
        )
    # A fresh set, so the "t_" this process has already activated does not
    # short-circuit the call below; monkeypatch restores the real one after.
    monkeypatch.setattr(pipeline, "_activated", set())

    # The four project schemas are already bound to "t_", so their real
    # `schema.activate` would be skipped by their own `is_activated()` guard.
    # Force the guard open and record the name each would have declared.
    for module in (core, coverage, paramset, request):
        monkeypatch.setattr(module.schema, "is_activated", lambda: False)
        monkeypatch.setattr(
            module.schema,
            "activate",
            lambda name, **kwargs: built.append(name),
        )

    request.activate()  # fans into core -> pipeline
    coverage.activate()
    paramset.activate()

    assert set(built) == {
        # Spec section 3, exactly.
        "wlpp_lab",
        "wlpp_subject",
        "wlpp_session",
        "wlpp_event",
        "wlpp_coverage",
        "wlpp_request",
        "wlpp_paramset",
        # Two the spec's table does not list by these names, pinned so that the
        # full set is asserted rather than a convenient subset: `wlpp_trial` is
        # element-event's second schema (its `trial` submodule activates
        # separately from `event`), and `wlpp_core` consolidates the spec's
        # `block.py`/`montage.py`/`sync.py` rows into one module, as built.
        "wlpp_trial",
        "wlpp_core",
    }


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
