"""`timebase.SessionClock`: which clock keeps each session's time, chosen
once, by the first stage that asks (design spec
`2026-10-18-training-records-and-backup-clock-design.md` section 4.1)."""

from __future__ import annotations

import shutil

import datajoint as dj
import pytest

from tests.identities import new_animal


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


@pytest.fixture
def landed(daemon_module, tmp_path):
    """Land a `ci` session, generated and landed by hand as
    `test_eye_populate.py` lands one; returns `(key, session_dir)`. Each
    session is deleted afterwards: one whose sync box log a test removed would
    otherwise break any later module's unrestricted populate."""
    from wl_preproc.schema import pipeline
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session

    from tests.schema.test_eye_populate import _land

    keys = []

    def land() -> tuple[dict, object]:
        session = new_animal().session()
        recipe = RECIPES["ci"].model_copy(update={"subject": session.subject, "session_id": session.session_id})
        generate_session(tmp_path, recipe)
        key = _land(tmp_path, recipe, session.session_datetime, acquisition_systems=recipe.systems)
        keys.append(key)
        return key, tmp_path / recipe.session_id

    yield land
    for key in keys:
        (pipeline.Session & key).delete(prompt=False)


def test_the_clock_is_keyed_by_the_session_and_read_from_its_folder(daemon_module, enum_values):
    """Imported, not Computed: the choice reads the session folder."""
    from wl_preproc.schema import pipeline, timebase

    assert issubclass(timebase.SessionClock, dj.Imported)
    assert timebase.SessionClock.primary_key == pipeline.Session.primary_key
    assert enum_values(timebase.SessionClock.heading.attributes["source"].type) == {"syncbox", "nidq", "lab"}


def test_the_first_stage_to_ask_records_the_clock(landed):
    from wl_preproc.schema import timebase
    from wl_preproc.timebase.clock import clock_for

    key, _session_dir = landed()
    assert not len(timebase.SessionClock & key)
    assert clock_for(key).source == "syncbox"
    assert (timebase.SessionClock & key).fetch1("source", "reason") == (
        "syncbox", "the sync box recorded this session")


def test_the_clock_is_chosen_once(landed):
    """A recorded choice stands: removing the sync box's log afterwards does
    not choose again."""
    from wl_preproc.schema import timebase
    from wl_preproc.timebase.clock import clock_for

    key, session_dir = landed()
    clock_for(key)
    shutil.rmtree(session_dir / "syncbox")
    assert clock_for(key).source == "syncbox"
    assert len(timebase.SessionClock & key) == 1


def test_a_session_no_clock_can_time_records_none(landed):
    from wl_preproc.schema import timebase
    from wl_preproc.timebase.clock import NoClock, clock_for

    key, session_dir = landed()
    shutil.rmtree(session_dir / "syncbox")
    with pytest.raises(NoClock, match="no sync box log, so no clock is built for this session yet"):
        clock_for(key)
    assert not len(timebase.SessionClock & key)


def test_populate_makes_the_same_choice(landed):
    from wl_preproc.schema import timebase

    key, _session_dir = landed()
    timebase.SessionClock.populate(key, suppress_errors=False)
    assert (timebase.SessionClock & key).fetch1("source") == "syncbox"


def test_a_daemon_pass_chooses_the_clock_in_its_events_stage(daemon_module, prefix, landed):
    """One pass chooses the clock and, reading through it, builds the
    session's trial list."""
    from wl_preproc.schema import pipeline, timebase

    key, _session_dir = landed()
    report = daemon_module.run_once(prefix=prefix)
    assert (timebase.SessionClock & key).fetch1("source") == "syncbox"
    assert len(pipeline.event.BehaviorRecording & key), report["errors"]
