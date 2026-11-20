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
    """Land a session, `ci` unless named, generated and landed by hand as
    `test_eye_populate.py` lands one; returns `(key, session_dir)`. Each
    session is deleted afterwards: one whose sync box log a test removed would
    otherwise break any later module's unrestricted populate."""
    from wl_preproc.schema import pipeline
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session

    from tests.schema.test_eye_populate import _land

    keys = []

    def land(recipe_name: str = "ci") -> tuple[dict, object]:
        session = new_animal().session()
        recipe = RECIPES[recipe_name].model_copy(update={"subject": session.subject,
                                                         "session_id": session.session_id})
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
        "syncbox", "the sync box's codes hold every run the rig recorded")


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
    shutil.rmtree(session_dir / "spikeglx")
    with pytest.raises(NoClock, match="the sync box recorded no log, and there is no NI recording; lab time is part 4"):
        clock_for(key)
    assert not len(timebase.SessionClock & key)


def test_populate_makes_the_same_choice(landed):
    from wl_preproc.schema import timebase

    key, _session_dir = landed()
    timebase.SessionClock.populate(key, suppress_errors=False)
    assert (timebase.SessionClock & key).fetch1("source") == "syncbox"


def test_a_daemon_pass_chooses_the_clock_before_its_events_stage(daemon_module, prefix, landed, monkeypatch):
    """The daemon records each session's clock in its own short transaction
    before the events stage, so no stage holds the new row's lock for the
    length of a decode another pass may wait on (the final review's I1); the
    events stage then reads the clock it recorded."""
    from wl_preproc.schema import events, pipeline, timebase

    key, _session_dir = landed()
    seen = {}
    real = events.populate_session

    def spy(session_key, session_dir):
        if session_key == key:
            seen["recorded"] = len(timebase.SessionClock & key)
        return real(session_key, session_dir)

    monkeypatch.setattr(events, "populate_session", spy)
    report = daemon_module.run_once(prefix=prefix)
    assert seen == {"recorded": 1}, report["errors"]
    assert (timebase.SessionClock & key).fetch1("source") == "syncbox"
    assert len(pipeline.event.BehaviorRecording & key), report["errors"]


def test_a_clock_another_pass_records_during_this_stages_snapshot_is_read(landed):
    """The final review's I1: two daemon passes overlap. A stage's transaction
    opens, WITH CONSISTENT SNAPSHOT as every DataJoint transaction does, before
    the other pass records the session's clock; inside it the stage finds no
    row, its own insert changes nothing, and a plain re-read still sees the
    snapshot. It is given the clock recorded, and does not raise."""
    import pymysql

    from wl_preproc.schema import timebase
    from wl_preproc.timebase.clock import clock_for

    key, _session_dir = landed()
    other = pymysql.connect(host=dj.config["database.host"], port=dj.config["database.port"],
                            user=dj.config["database.user"], password=dj.config["database.password"],
                            autocommit=True)
    try:
        with dj.conn().transaction:
            assert not len(timebase.SessionClock & key)
            with other.cursor() as cursor:
                cursor.execute(f"INSERT INTO {timebase.SessionClock.full_table_name} "
                               "(subject, session_datetime, source, reason) VALUES (%s, %s, 'syncbox', %s)",
                               (key["subject"], key["session_datetime"],
                                "the sync box's codes hold every run the rig recorded"))
            assert clock_for(key).source == "syncbox"
    finally:
        other.close()


def test_an_ni_clock_another_pass_records_during_this_stages_snapshot_is_read(landed, monkeypatch):
    """The same overlap for a session the NI keeps: inside the stage's
    transaction choosing would decode the NI, which it never does there
    (amendment 43), so it reads the clock the other pass committed."""
    import pymysql

    from wl_preproc.events import extract
    from wl_preproc.schema import timebase
    from wl_preproc.timebase.clock import clock_for

    key, _session_dir = landed("ni-clock")
    monkeypatch.setattr(extract, "extract_nidq_words", lambda path: pytest.fail("decoded inside a transaction"))
    other = pymysql.connect(host=dj.config["database.host"], port=dj.config["database.port"],
                            user=dj.config["database.user"], password=dj.config["database.password"],
                            autocommit=True)
    try:
        with dj.conn().transaction:
            assert not len(timebase.SessionClock & key)
            with other.cursor() as cursor:
                cursor.execute(f"INSERT INTO {timebase.SessionClock.full_table_name} "
                               "(subject, session_datetime, source, reason) VALUES (%s, %s, 'nidq', %s)",
                               (key["subject"], key["session_datetime"], "the NI's codes hold every run"))
            assert clock_for(key).source == "nidq"
    finally:
        other.close()


# -- A session timed before its clock was recorded (design spec
# `2026-10-18-training-records-and-backup-clock-design.md`, amendment 48,
# which replaces amendment 30's rule).

def _staged_without_its_clock(landed):
    """A session whose events were staged, then its clock row deleted, as a
    session staged before part 2 has none."""
    from wl_preproc.schema import events, timebase

    key, session_dir = landed()
    events.populate_session(key, session_dir)
    (timebase.SessionClock & key).delete(prompt=False)
    return key


def test_a_timed_session_without_a_clock_is_refused(landed):
    """Which clock timed its events is not recorded, and choosing one now
    could choose the other: it is refused, naming what to delete, and never
    silently given a clock, though its sync box would hold every run."""
    from wl_preproc.schema import timebase
    from wl_preproc.timebase.clock import NoClock, clock_for

    key = _staged_without_its_clock(landed)
    with pytest.raises(NoClock, match=r"its events \(`pipeline\.event\.BehaviorRecording`\) and timing "
                                      r"\(`timebase\.TimingProvenance`\) must be deleted for it to be timed again"):
        clock_for(key)
    assert not len(timebase.SessionClock & key)


def test_populate_refuses_a_timed_session_without_a_clock(landed):
    """The daemon's first stage records nothing for it and parks no error:
    its events stage says why on each pass."""
    from wl_preproc.schema import timebase

    key = _staged_without_its_clock(landed)
    timebase.SessionClock.populate(key, suppress_errors=False)
    assert not len(timebase.SessionClock & key)


def test_the_backfill_records_the_sync_box_for_timed_sessions(daemon_module, prefix, landed):
    """Amendment 48's one-time back-fill at 3b-1's deploy: every session with
    a timing record and no clock was timed by the sync box, the only clock
    built before it. A session never timed is left to be chosen."""
    from wl_preproc.schema import timebase

    timed, _ = landed()
    daemon_module.run_once(prefix=prefix)
    assert len(timebase.TimingProvenance & timed)
    (timebase.SessionClock & timed).delete(prompt=False)
    untimed, _ = landed()
    assert timebase.backfill_session_clocks() == 1
    assert (timebase.SessionClock & timed).fetch1("source", "reason") == ("syncbox", timebase.BACKFILL_REASON)
    assert not len(timebase.SessionClock & untimed)
    assert timebase.backfill_session_clocks() == 0


def test_the_backfill_is_a_command(daemon_module, prefix, landed, capsys):
    from wl_preproc.cli.main import main

    import re

    assert main(["clocks", "backfill", "--prefix", prefix]) == 0
    assert re.fullmatch(r"\d+ sessions? given the sync box's clock \(amendment 48's back-fill\)",
                        capsys.readouterr().out.strip())


# -- The NI's codes are decoded outside `SessionClock`'s transaction
# (amendment 43).

def test_the_ni_is_decoded_outside_the_clocks_transaction(landed, monkeypatch):
    from wl_preproc.events import extract
    from wl_preproc.schema import timebase

    key, _session_dir = landed("ni-clock")
    seen = []
    real = extract.extract_nidq_words

    def spy(path):
        seen.append(dj.conn().in_transaction)
        return real(path)

    monkeypatch.setattr(extract, "extract_nidq_words", spy)
    timebase.SessionClock.populate(key, suppress_errors=False)
    assert seen == [False]
    assert (timebase.SessionClock & key).fetch1("source") == "nidq"


def test_a_stage_asking_inside_a_transaction_does_not_decode_the_ni(landed, monkeypatch):
    """Amendment 43, and the controller's ruling: inside a transaction the
    sync box is still chosen when it holds every run, as the stages above do;
    a session that needs the NI decoded waits for a daemon pass."""
    from wl_preproc.events import extract
    from wl_preproc.schema import timebase
    from wl_preproc.timebase.clock import NoClock, clock_for

    key, _session_dir = landed("ni-clock")
    monkeypatch.setattr(extract, "extract_nidq_words", lambda path: pytest.fail("decoded inside a transaction"))
    with dj.conn().transaction:
        with pytest.raises(NoClock, match="the session's clock is not recorded yet: a daemon pass records it first"):
            clock_for(key)
    assert not len(timebase.SessionClock & key)
    synced, _ = landed()
    with dj.conn().transaction:
        assert clock_for(synced).source == "syncbox"
    assert (timebase.SessionClock & synced).fetch1("source") == "syncbox"


def test_a_session_landed_without_its_sync_box_is_never_timed_by_it(landed):
    """Decision 8: a sync box that failed partway hands the whole session to
    the backup, so a person's record that the session landed without it
    keeps the sync box's own partial log from being its clock; the NI keeps
    its time, the reason naming who landed it without."""
    import datetime

    from wl_preproc.schema import ingest, timebase
    from wl_preproc.timebase.clock import clock_for

    key, session_dir = landed()
    ingest.LandedWithout.insert1({**key, "system": "syncbox", "actor": "J. W.", "reason": "the Pi's card failed",
                                  "recorded_at": datetime.datetime(2027, 3, 14, 11, 30),
                                  "session_dir": str(session_dir)})
    assert clock_for(key).source == "nidq"
    assert (timebase.SessionClock & key).fetch1("source", "reason") == (
        "nidq", "the NI's codes hold every run the rig recorded; the sync box was landed without by J. W.")


def test_a_daemon_pass_leaves_a_session_without_a_clock_untouched_until_it_has_one(daemon_module, prefix, landed):
    """Part 3a's final review, I3: a session no clock built so far can time
    (lab time is part 4) has none. A pass must not write it a tier-D timing
    record from checks that never ran, nor park any of its jobs in `error`,
    where nothing retries them once its clock exists; its events stage says
    why, on each pass."""
    import datetime

    from wl_preproc.schema import ingest, timebase

    from wl_preproc.schema import core

    # One landed without its sync box and SpikeGLX both.
    key, session_dir = landed()
    for system in ("syncbox", "spikeglx"):
        ingest.LandedWithout.insert1({**key, "system": system, "actor": "J. W.", "reason": "the Pi's card failed",
                                      "recorded_at": datetime.datetime(2027, 3, 14, 11, 30),
                                      "session_dir": str(session_dir)})
    # And one whose manifest listed neither: no system row, no log, no NI.
    unlisted, unlisted_dir = landed()
    (core.AcquisitionSystem & unlisted & [{"system": "syncbox"}, {"system": "spikeglx"}]).delete_quick()
    shutil.rmtree(unlisted_dir / "syncbox")
    shutil.rmtree(unlisted_dir / "spikeglx")
    report = daemon_module.run_once(prefix=prefix)
    for session in (key, unlisted):
        assert not len(timebase.TimingProvenance & session)
        assert not len(timebase.SessionClock & session)
        for table in (timebase.SessionClock, timebase.SystemTimebase, timebase.TimingProvenance):
            assert not len(table.jobs & session & "status = 'error'"), table.__name__
    assert any("the sync box was landed without by J. W., and SpikeGLX was landed without by J. W." in str(error)
               for error in report["errors"])
    assert any("the sync box recorded no log, and there is no NI recording" in str(error)
               for error in report["errors"])


# -- A session no clock can time keeps its reason (part 3a's I3, amendment
# 37): recorded in `timebase.ClockRefusal`, reported by its events stage on
# each pass, and decided again only when what the choice reads changes.

def _ni_that_does_not_hold(landed):
    """An `ni-clock` session, no sync box, its NI cut at 5 s, inside run 1's
    second trial: neither clock holds every run."""
    from tests.timebase.test_clock import _cut_the_ni

    key, session_dir = landed("ni-clock")
    _cut_the_ni(session_dir, 5.0)
    return key, session_dir


_NI_REASON = ("the sync box recorded no log, and the NI's codes do not: run 1's trial 2 does not end in its codes; "
              "lab time is part 4, so no clock built yet can time this session")


def test_a_session_no_clock_can_time_reports_its_reason_and_is_not_decoded_again(daemon_module, prefix, landed,
                                                                                  monkeypatch):
    from wl_preproc.events import extract
    from wl_preproc.schema import timebase

    key, session_dir = _ni_that_does_not_hold(landed)
    report = daemon_module.run_once(prefix=prefix)
    errors = [str(error) for error in report["errors"] if repr(key["subject"]) in str(error)]
    assert errors and all(_NI_REASON in error for error in errors), errors
    assert not any("not recorded yet" in error for error in errors)
    assert (timebase.ClockRefusal & key).fetch1("reason") == _NI_REASON
    assert not len(timebase.SessionClock & key)

    decoded = []
    real = extract.extract_nidq_words

    def spy(path):
        decoded.append(path)
        return real(path)

    monkeypatch.setattr(extract, "extract_nidq_words", spy)
    report = daemon_module.run_once(prefix=prefix)
    assert [path for path in decoded if session_dir in path.parents] == []
    assert any(_NI_REASON in str(error) for error in report["errors"] if repr(key["subject"]) in str(error))


def test_a_refusal_is_decided_again_when_what_it_read_changes(landed):
    """The NI's file restored: its size and time differ from the refused
    one's, so the choice is made again, the NI keeps time, and the refusal
    is cleared."""
    import shutil

    from wl_preproc.schema import timebase
    from wl_preproc.timebase.clock import nidq_files

    key, session_dir = landed("ni-clock")
    (path,) = nidq_files(session_dir)
    whole = session_dir.parent / "whole.nidq.bin"
    shutil.copyfile(path, whole)
    from tests.timebase.test_clock import _cut_the_ni

    _cut_the_ni(session_dir, 5.0)
    timebase.SessionClock.populate(key, suppress_errors=False)
    assert (timebase.ClockRefusal & key).fetch1("reason") == _NI_REASON
    shutil.copyfile(whole, path)
    timebase.SessionClock.populate(key, suppress_errors=False)
    assert (timebase.SessionClock & key).fetch1("source") == "nidq"
    assert not len(timebase.ClockRefusal & key)


def test_an_unreadable_ni_is_a_recorded_reason(landed):
    """A file cut mid-sample is refused by `extract_nidq_words`; the
    refusal says so, and is kept, never lost silently."""
    from wl_preproc.schema import timebase
    from wl_preproc.timebase.clock import nidq_files

    key, session_dir = landed("ni-clock")
    (path,) = nidq_files(session_dir)
    with path.open("ab") as handle:
        handle.write(b"\0\0")  # one int16 more: a sample split across channels
    timebase.SessionClock.populate(key, suppress_errors=False)
    reason = (timebase.ClockRefusal & key).fetch1("reason")
    assert reason.startswith("the NI's codes cannot be read: "), reason
    assert "do not divide into" in reason
    assert not len(timebase.SessionClock & key)


def test_a_stage_inside_a_transaction_is_given_the_recorded_reason(landed):
    from wl_preproc.schema import timebase
    from wl_preproc.timebase.clock import NoClock, clock_for

    key, _session_dir = _ni_that_does_not_hold(landed)
    timebase.SessionClock.populate(key, suppress_errors=False)
    with dj.conn().transaction:
        with pytest.raises(NoClock, match=r"the NI's codes do not: run 1's trial 2 does not end") as raised:
            clock_for(key)
    assert "not recorded yet" not in str(raised.value)
