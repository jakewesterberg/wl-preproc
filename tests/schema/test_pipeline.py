# tests/schema/test_pipeline.py
import pytest


def test_all_four_elements_activate(dj_conn):
    from wl_preproc.schema import pipeline

    pipeline.activate(prefix="tp_")
    for name in ("lab", "subject", "session", "event"):
        assert getattr(pipeline, name) is not None, name


def test_session_table_exists_and_is_keyed_as_elements_expects(dj_conn):
    from wl_preproc.schema import pipeline

    pipeline.activate(prefix="tp_")
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


def test_activation_is_idempotent(dj_conn):
    """The suite activates repeatedly against one container."""
    from wl_preproc.schema import pipeline

    pipeline.activate(prefix="tp_")
    pipeline.activate(prefix="tp_")
