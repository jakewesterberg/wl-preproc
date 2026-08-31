# tests/schema/test_eye_schema.py
"""EyeCalibration and EyeQuality: what a session's calibration cost it, and
how good its tracking was -- both keyed per eye, both daemon stages.

Controller ruling A: the brief's own Step 1 wrote
`from wl_preproc.schema._testing import enum_values`. No such module exists --
`enum_values` is the `tests/schema/conftest.py` fixture every other schema test
module already takes as a parameter (`test_timebase.py`'s
`test_the_tier_enum_no_longer_holds_pending` is the precedent). Taken as a
fixture here too, not imported.

Controller ruling B: the brief's tests took an invented `schemas_eye` fixture.
This file follows the pattern `test_timebase.py` and `tests/schema/
test_coverage.py` already use instead -- a small, module-scoped, locally
defined `schemas` fixture that activates exactly the schema module(s) this
file needs and returns it. No new fixture was added to `tests/schema/
conftest.py`: nothing here is shared by another test module, which is the bar
`enum_values`' own docstring sets for living there instead.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def schemas(dj_conn, prefix):
    from wl_preproc.schema import eye

    eye.activate(prefix=prefix)
    return eye


def test_no_bare_longblob(schemas):
    """The repo-wide sweep (`tests/schema/test_guardrails.py`) covers this
    too, but state it here: a bare longblob stores a numpy array as its
    truncated string repr and nothing raises on insert or fetch."""
    eye = schemas
    assert "longblob" not in eye.EyeCalibration.definition
    assert "longblob" not in eye.EyeQuality.definition


def test_calibration_source_names_all_four_chain_steps(schemas, enum_values):
    attr = schemas.EyeCalibration.heading.attributes["calibration_source"]
    assert enum_values(attr.type) == {
        "fitted", "monkeylogic", "carried_forward", "refused"
    }


def test_the_affine_parameters_are_nullable(schemas):
    """A refused calibration is a first-class outcome with a stated reason,
    not an error and not a fabricated map."""
    for name in ("a00", "a01", "b0", "a10", "a11", "b1"):
        assert schemas.EyeCalibration.heading.attributes[name].nullable


def test_both_computed_tables_are_daemon_stages():
    """`test_every_computed_table_is_a_daemon_stage` exists because
    `TrialCoverage` was once missing from `_computed_tables()`, which silently
    returned tier D for every session. Two new computed tables land here."""
    from wl_preproc import daemon

    names = {t.__name__ for t in daemon._computed_tables()}
    assert {"EyeCalibration", "EyeQuality"} <= names


def test_registering_them_does_not_break_a_clean_daemon_pass(schemas, dj_conn, prefix):
    """Not one of the brief's own four tests -- added because registering
    these tables surfaced a real hazard the brief does not describe (reported
    separately): at the time this test was written, neither table defined
    `make()` (Task 9 was schema only), and DataJoint's default `key_source`
    for a Computed table is the join of its FK parents projected to their
    primary key -- here, bare `pipeline.Session` -- with nothing about the
    extra `eye` attribute. Left at that default, `daemon.run_once()` would
    call `.populate()` against every session already in this suite's shared
    database (`tests/conftest.py`'s session-scoped `dj_conn`/`prefix`) and
    hit `dj.AutoPopulate`'s own base `make()`, which raises
    `NotImplementedError` unconditionally -- confirmed directly against
    `datajoint/autopopulate.py` for this pinned version (2.3.2). Task 9's own
    `key_source` was overridden on both tables to stay empty until a later
    task supplied the real restriction design spec section 6 names ("the
    session restricted to those with an ohDPI recording and assembled
    events") alongside `make()` itself.

    **Task 10 is that later task, and both now have a real `key_source` and
    `make()` (`wl_preproc/schema/eye.py`).** This test still passes, but for
    a different reason than the paragraph above: the bare `pipeline.Session`
    row the fixture below plants has no `ingest.Ingestion`, no
    `core.AcquisitionSystem` and no `pipeline.event.BehaviorRecording`, so
    `EyeCalibration.key_source`'s real restriction (design spec section 6)
    excludes it on its own merits now, the same way it excludes any session
    missing either half -- not because there was nothing yet defined to run
    against it. Kept rather than rewritten from scratch: a bare-session
    fixture with zero errors is still the right regression guard for "does
    registering these two tables break a clean daemon pass", regardless of
    which `key_source` is doing the excluding underneath it.

    This is what actually proves that stays true end to end, through the real
    `daemon.run_once()` path -- not through `EyeCalibration.key_source` in
    isolation, which could pass while some OTHER interaction still broke the
    daemon. `activate_all` runs first so every schema `run_once` touches is
    bound, exactly as `tests/schema/test_daemon.py`'s own daemon tests do.

    **Mutation-checked, and the first draft had none.** With no `pipeline.
    Session` row anywhere yet, DataJoint's own default `key_source` -- bare
    `pipeline.Session.proj()` -- is EMPTY too, so a version of this test that
    ran before any session existed passed whether or not `EyeCalibration.
    key_source`/`EyeQuality.key_source` were overridden at all: confirmed
    directly by temporarily reverting both to `pipeline.Session.proj()`
    (DataJoint's own default shape) and re-running this test alone -- it still
    passed, proving nothing. A bare `pipeline.Session` row -- nothing from
    `ingest`, `core.AcquisitionSystem` or any recipe; that is all the default
    `key_source` this hazard is about would ever see -- is what makes the
    difference observable, and re-running the same reverted mutant against
    the fixture below does fail it, with `NotImplementedError` from `dj.
    AutoPopulate`'s own base `make()` inside `report["errors"]`.
    """
    import datetime

    from wl_preproc import daemon
    from wl_preproc.schema import pipeline

    daemon.activate_all(prefix=prefix)

    # A minimal, standalone Session -- not a landed one. This table's
    # (mis)behaviour under the DEFAULT key_source turns on `pipeline.Session`
    # alone (see `EyeCalibration.key_source`'s own docstring: the default is
    # "the join of FK parents ... here bare `pipeline.Session`"), so that is
    # exactly what this fixture supplies and nothing more -- an `Ingestion`
    # row would exercise every OTHER table's own key_source too, which is not
    # what this test is isolating. `2099-01-01` is a sentinel outside every
    # date any other fixture in this shared, session-scoped database uses, so
    # this cannot collide with a session another test file already landed.
    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": "eyeprobe",
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    pipeline.Session.insert1(
        {"subject": "eyeprobe", "session_datetime": datetime.datetime(2099, 1, 1, 0, 0)},
        skip_duplicates=True,
    )

    report = daemon.run_once(prefix=prefix)
    assert report["errors"] == [], (
        "registering EyeCalibration/EyeQuality broke a clean daemon pass: "
        f"{report['errors']}"
    )
