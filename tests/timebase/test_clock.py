"""Which clock keeps a session's time, and the one door every stage reads it
through (design spec `2026-10-18-training-records-and-backup-clock-design.md`
section 4, and amendments 41-44, 47 and 50): the sync box where its codes hold
every run or it restarted within the session, else the NI where its codes do,
else no clock until lab time (part 4)."""

from __future__ import annotations

import datetime
import re

import pytest


def _session(tmp_path, recipe_name: str = "ci", **update):
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session

    recipe = RECIPES[recipe_name].model_copy(update=update) if update else RECIPES[recipe_name]
    generate_session(tmp_path, recipe)
    return tmp_path / recipe.session_id


def _generated(tmp_path, recipe):
    from wl_preproc.synth.session import generate_session

    truth = generate_session(tmp_path, recipe)
    return tmp_path / recipe.session_id, truth


def _cut_the_sync_box_log(session_dir, at_s: float) -> None:
    """The box's log as one that stopped at session time `at_s`: every record
    ticked after it gone, its trailer too, as a Pi that lost power leaves it.
    The synthetic box ticks session t = 0 at 1 s (`synth/syncbox.py`)."""
    from wl_preproc.timebase.extract import find_recordings

    (log,) = find_recordings("syncbox", session_dir / "syncbox")
    header, *records = log.read_text().splitlines()
    cut_us = round((at_s + 1.0) * 1e6)
    log.write_text("\n".join([header, *(record for record in records
                                        if record[0] in "BW" and int(record.split(",")[1]) < cut_us)]) + "\n")


def _cut_the_ni(session_dir, at_s: float) -> None:
    """The NI's file as one that stopped at session time `at_s`, cut at a
    whole sample, its `.meta` untouched."""
    from wl_preproc.synth.spikeglx import NIDQ_SAMPLE_RATE_HZ, SPIKEGLX_PRE_ROLL_S
    from wl_preproc.timebase._nidq_meta import read_nidq_meta
    from wl_preproc.timebase.clock import nidq_files

    (path,) = nidq_files(session_dir)
    n_channels = read_nidq_meta(path.with_suffix(".meta")).n_channels
    samples = int((at_s + SPIKEGLX_PRE_ROLL_S) * NIDQ_SAMPLE_RATE_HZ)
    with path.open("r+b") as handle:
        handle.truncate(samples * n_channels * 2)


def test_a_full_sync_box_keeps_time(tmp_path):
    from wl_preproc.timebase.clock import Choice, choose

    assert choose(_session(tmp_path, runs=True), "pico") == Choice(
        source="syncbox", reason="the sync box's codes hold every run the rig recorded")


def test_a_sync_box_that_stops_inside_a_run_hands_the_session_to_the_ni(tmp_path):
    """The plan's Review Focus 1: a log that ends inside the last run (run 2,
    9 s to 15 s), with no later segment, ends before the session's last run
    does (amendment 42), and the NI's codes hold every run, so the NI keeps
    time and its reason names what of that run is missing."""
    from wl_preproc.timebase.clock import choose

    session_dir = _session(tmp_path, runs=True)
    _cut_the_sync_box_log(session_dir, 12.0)
    choice = choose(session_dir, "pico")
    assert choice.source == "nidq"
    assert choice.reason.startswith("the NI's codes hold every run the rig recorded; the sync box does not: run ")
    assert choice.reason == ("the NI's codes hold every run the rig recorded; the sync box does not: run 2's "
                             "trial 4 does not end in its codes")


def test_a_sync_box_with_a_gap_before_the_last_run_stays_the_clock(tmp_path):
    """Amendment 42: the NI keeps time only when the box's log ends before
    the session's last run does, with no later segment. A gap within the
    session (the box recording elsewhere from 5.5 s to 8.5 s, inside run 1)
    keeps the box, and its reason names the first thing its codes lack."""
    from wl_preproc.synth.recipe import SyncBoxRollSpec
    from wl_preproc.timebase.clock import Choice, choose

    session_dir = _session(tmp_path, runs=True, syncbox_roll=SyncBoxRollSpec(at_s=5.5, gap_s=3.0))
    assert choose(session_dir, "pico") == Choice(
        source="syncbox", reason="the sync box's codes reach the end of the rig's last run; run 1's trial 3 is not in "
                                 "its codes")


def test_a_sync_box_with_a_gap_inside_the_last_run_stays_the_clock(tmp_path):
    """Amendment 42's "its log ends before the session's last run does": a
    gap inside the last run (8.5 s to 10 s, losing its second trial) that
    resumes before its last trial reaches the run's end, so the box stays
    the clock. Its reason names the trial lost inside that run, not "a run
    before it" (the final review's M3)."""
    from wl_preproc.contracts.events import TaskTypeCode
    from wl_preproc.synth.recipe import BlockSpec, SyncBoxRollSpec
    from wl_preproc.timebase.clock import Choice, choose

    session_dir = _session(tmp_path, runs=True, syncbox_roll=SyncBoxRollSpec(at_s=8.5, gap_s=1.5), blocks=(
        BlockSpec(task_type=TaskTypeCode.RESTING_DARK, n_trials=1, trial_duration_s=6.0),
        BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=3, trial_duration_s=3.0)))
    assert choose(session_dir, "pico") == Choice(
        source="syncbox", reason="the sync box's codes reach the end of the rig's last run; run 2's trial 3 is not in "
                                 "its codes")


def test_with_no_rig_record_a_sync_box_reaching_session_end_says_so(tmp_path):
    """With no rig record, rule 1 needs only SESSION_END; a log missing
    SESSION_START keeps the box, and its reason names neither the rig's
    last run nor a run (the final review's M3, Task 3's two deferred
    minors)."""
    from wl_preproc.events.rigruns import read_rig_runs
    from wl_preproc.timebase.clock import Choice, choose
    from wl_preproc.timebase.extract import find_recordings

    session_dir = _session(tmp_path)
    rig_runs = read_rig_runs(session_dir, "pico")
    assert rig_runs is None or not rig_runs.runs
    (log,) = find_recordings("syncbox", session_dir / "syncbox")
    text = log.read_text()
    # SESSION_START, the log's first code, ticked at session t = 0.
    assert text.count("W,1000000,1\n") == 1
    log.write_text(text.replace("W,1000000,1\n", ""))
    assert choose(session_dir, "pico") == Choice(
        source="syncbox", reason="the sync box's codes decode SESSION_END, and there is no rig record; its codes "
                                 "hold no SESSION_START")


def test_a_reaching_reason_is_clipped_to_its_column():
    """The reaching reason carries `unheld`'s sentence, so it is clipped as
    every reason is."""
    from wl_preproc.timebase import clock

    assert len(clock.reaching_reason("x" * 300, has_rig_record=True)) == 255
    assert clock.reaching_reason("x" * 300, has_rig_record=True).endswith("…")
    assert clock.reaching_reason("run 1 is not in its codes", has_rig_record=True) == (
        "the sync box's codes reach the end of the rig's last run; run 1 is not in its codes")


def test_a_corrupt_word_in_the_last_trial_keeps_the_sync_box(tmp_path):
    """The final review's I1: one word of the last trial's TRIAL_NUMBER
    flipped (run 2's trial 4), its checksum failing, with the run's RUN_END
    and SESSION_END decoded after it. The log did not end before the last
    run did (amendment 42), so the box keeps time, with or without an NI: a
    training session with only the box is still timed, as on main."""
    import shutil

    from wl_preproc.timebase.clock import choose
    from wl_preproc.timebase.extract import find_recordings

    session_dir = _session(tmp_path, runs=True)
    (log,) = find_recordings("syncbox", session_dir / "syncbox")
    text = log.read_text()
    # Trial 4's TRIAL_NUMBER payload: escape, high word 0, low word 4, checksum.
    assert text.count("W,10013000,4\n") == 1
    log.write_text(text.replace("W,10013000,4\n", "W,10013000,5\n"))
    choice = choose(session_dir, "pico")
    assert choice.source == "syncbox"
    assert choice.reason == ("the sync box's codes reach the end of the rig's last run; run 2's trial 4 is not in its "
                             "codes")
    shutil.rmtree(session_dir / "spikeglx")
    assert choose(session_dir, "pico") == choice


def test_a_sync_box_that_restarted_stays_the_clock(tmp_path):
    """Amendment 42: its restart is bridged by a device that recorded through
    it (wl-sync part 2), so the NI is not chosen, though its codes hold more."""
    from wl_preproc.synth.recipe import SyncBoxRestartSpec
    from wl_preproc.timebase.clock import Choice, choose

    session_dir = _session(tmp_path, syncbox_restart=SyncBoxRestartSpec(at_s=7.5, gap_s=1.2))
    assert choose(session_dir, "pico") == Choice(
        source="syncbox",
        reason="the sync box restarted within the session; it stays the clock, its restart bridged by wl-sync part 2")


def test_no_sync_box_log_and_an_ni_is_the_ni(tmp_path):
    from wl_preproc.timebase.clock import Choice, choose

    assert choose(_session(tmp_path, "ni-clock"), "pico") == Choice(
        source="nidq", reason="the NI's codes hold every run the rig recorded; the sync box recorded no log")


def test_a_sync_box_landed_without_hands_the_session_to_the_ni(tmp_path):
    """Decision 8 and amendment 47: whatever its partial log holds, a sync box
    a person landed without is never the clock."""
    from wl_preproc.timebase.clock import Choice, choose

    assert choose(_session(tmp_path, runs=True), "pico", {"syncbox": "J. W."}) == Choice(
        source="nidq",
        reason="the NI's codes hold every run the rig recorded; the sync box was landed without by J. W.")


def test_a_landed_without_spikeglx_is_not_the_clock(tmp_path):
    from wl_preproc.timebase.clock import NoClock, choose

    with pytest.raises(NoClock, match=r"the sync box recorded no log, and SpikeGLX was landed without by J\. W\.; "
                                      r"lab time is part 4"):
        choose(_session(tmp_path, "ni-clock"), "pico", {"spikeglx": "J. W."})


def test_neither_clock_holds_the_runs_is_no_clock(tmp_path):
    from wl_preproc.timebase.clock import NoClock, choose

    session_dir = _session(tmp_path, runs=True)
    _cut_the_sync_box_log(session_dir, 12.0)
    _cut_the_ni(session_dir, 5.0)
    with pytest.raises(NoClock) as raised:
        choose(session_dir, "pico")
    message = str(raised.value)
    assert "the sync box does not: run 2's trial 4 does not end in its codes" in message
    assert "the NI's codes do not: run 1's trial 2 does not end in its codes" in message
    assert "lab time is part 4" in message


def test_no_sync_box_log_and_no_ni_is_no_clock(tmp_path):
    import shutil

    from wl_preproc.timebase.clock import NoClock, choose

    session_dir = _session(tmp_path)
    shutil.rmtree(session_dir / "syncbox")
    shutil.rmtree(session_dir / "spikeglx")
    with pytest.raises(NoClock, match="the sync box recorded no log, and there is no NI recording; lab time is part 4"):
        choose(session_dir, "pico")


def test_a_reason_is_clipped_to_its_column(tmp_path):
    """`timebase.SessionClock.reason` is varchar(255): a long name would fail
    the insert on every pass."""
    from wl_preproc.timebase.clock import choose

    reason = choose(_session(tmp_path), "pico", {"syncbox": "J" * 300}).reason
    assert len(reason) == 255 and reason.endswith("…")
    assert reason.startswith("the NI's codes hold every run the rig recorded; the sync box was landed without by JJJ")


def test_the_ni_files_are_in_spikeglxs_gate_and_trigger_order(tmp_path):
    """By gate, then trigger, as integers: text order puts g10 before g2. A
    name without them, as the synthetic NI's first file, comes first."""
    from wl_preproc.timebase.clock import nidq_files

    folder = tmp_path / "s" / "spikeglx"
    folder.mkdir(parents=True)
    names = ["s_g10_t0.nidq.bin", "s_g2_t1.nidq.bin", "s.nidq.bin", "s_g2_t0.nidq.bin", "s_g2_t10.nidq.bin",
             "s_imec0.ap.bin"]
    for name in names:
        (folder / name).write_bytes(b"")
    assert [path.name for path in nidq_files(tmp_path / "s")] == [
        "s.nidq.bin", "s_g2_t0.nidq.bin", "s_g2_t1.nidq.bin", "s_g2_t10.nidq.bin", "s_g10_t0.nidq.bin"]


def test_the_ni_clock_gives_the_ni_codes(tmp_path):
    """Section 4.5 under the NI (amendment 50): t = 0 is the NI's first
    sample, so each code sits at its emission plus SpikeGLX's pre-roll, timed
    at its strobe's rising edge (`events/extract.py::extract_nidq_words`)."""
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.spikeglx import NIDQ_SAMPLE_RATE_HZ, SPIKEGLX_PRE_ROLL_S
    from wl_preproc.synth.timeline import apply_drift
    from wl_preproc.timebase.clock import Clock

    recipe = RECIPES["ni-clock"]
    session_dir, truth = _generated(tmp_path, recipe)
    drift = dict(recipe.system_drift_ppm).get("spikeglx", recipe.drift_ppm)
    codes = Clock(source="nidq", session_dir=session_dir).codes()
    assert [code for _time, code in codes] == [word for _time, word in truth.code_words]
    for (time_s, _code), (emitted_s, _word) in zip(codes, truth.code_words, strict=True):
        assert time_s == pytest.approx(apply_drift(emitted_s, drift) + SPIKEGLX_PRE_ROLL_S,
                                       abs=1 / NIDQ_SAMPLE_RATE_HZ)


def test_the_ni_clock_without_an_ni_recording_has_no_reference(tmp_path):
    from wl_preproc.timebase.clock import Clock, NoClock

    with pytest.raises(NoClock, match="has no NI recording"):
        Clock(source="nidq", session_dir=tmp_path).barcode_reference()


def test_the_sync_box_clock_answers_with_its_own_barcodes(tmp_path):
    from wl_preproc.timebase.clock import Clock
    from wl_preproc.timebase.segments import session_reference

    session_dir = _session(tmp_path)
    assert Clock(source="syncbox", session_dir=session_dir).barcode_reference() == session_reference(session_dir)


def test_the_sync_box_clocks_codes_are_its_log_in_session_time(tmp_path):
    """Every code the sync box logged, in emission order, at session times
    starting at its first barcode: the canonical stream the events stage
    decodes."""
    from wl_preproc.contracts.events import Marker, decode_stream
    from wl_preproc.events.extract import extract_syncbox_words
    from wl_preproc.timebase.clock import Clock
    from wl_preproc.timebase.extract import find_recordings

    session_dir = _session(tmp_path)
    codes = Clock(source="syncbox", session_dir=session_dir).codes()
    (log,) = find_recordings("syncbox", session_dir / "syncbox")
    assert [code for _time, code in codes] == [code for _position, code in extract_syncbox_words(log).words]
    times = [time for time, _code in codes]
    assert times == sorted(times) and 0.0 <= times[0] < 1.0
    assert any(getattr(event, "code", None) == Marker.SESSION_END for event in decode_stream(codes))


def test_the_sync_box_clock_places_t0_by_its_first_barcode(tmp_path):
    """The NWB file's wall-clock time of t = 0 (the NWB builder spec section
    4.2), as `nwb/gather.py` took it before."""
    from wl_preproc.timebase.clock import BARCODE_EPOCH, Clock
    from wl_preproc.timebase.segments import session_reference

    session_dir = _session(tmp_path)
    reference = session_reference(session_dir)
    first = min(reference, key=reference.get)
    started_at = BARCODE_EPOCH + datetime.timedelta(seconds=first + 3)
    clock = Clock(source="syncbox", session_dir=session_dir).wall_reference(started_at)
    assert clock["source"] == "barcode"
    assert clock["reference_time"] == BARCODE_EPOCH + datetime.timedelta(seconds=first)


def test_the_sync_box_ignores_the_rig_clock_and_the_ni_file(tmp_path, monkeypatch):
    """Under the sync box t = 0 is placed as it always was, the barcode's or
    the manifest's, whatever the rig's clock says and whether or not the NI's
    zone is set: the two other sources are the NI's (amendment 46). The
    synthetic session's NI file was created 0.7 s before t = 0, and its
    barcodes are counters that disagree with any wall clock, so `started_at`
    places t = 0 even with both of those agreeing."""
    from wl_preproc.synth.recipe import SYNTH_EPOCH
    from wl_preproc.timebase import clock
    from wl_preproc.timebase.segments import session_reference

    monkeypatch.setattr(clock, "NIDQ_TIMEZONE", None)
    clock.set_nidq_timezone("America/New_York")
    session_dir = _session(tmp_path)
    started_at = SYNTH_EPOCH.replace(tzinfo=None)
    reference = session_reference(session_dir)
    expected = clock.reference_from(min(reference, key=reference.get), started_at)
    assert expected["source"] == "manifest"
    sync_box = clock.Clock(source="syncbox", session_dir=session_dir)
    assert sync_box.wall_reference(started_at, rig_at_zero=SYNTH_EPOCH.timestamp() + 5.0) == expected
    assert sync_box.wall_reference(started_at) == expected


@pytest.fixture(scope="module")
def ni_session(tmp_path_factory):
    """An NI-only session's SpikeGLX folder, as the synthetic NI writes it:
    its `fileCreateTime` is 2027-03-14T04:59:59, New York's time 0.7 s before
    `SYNTH_EPOCH` (`tests/synth/test_spikeglx.py::test_the_ni_meta_names_its_creation_time`)."""
    from wl_preproc.synth.recipe import NI_CLOCK_RECIPE

    return _ni_folder(tmp_path_factory.mktemp("ni"), NI_CLOCK_RECIPE)


def _ni_folder(root, recipe):
    from wl_preproc.synth.spikeglx import write_spikeglx
    from wl_preproc.synth.timeline import build_timeline

    session_dir = root / recipe.session_id
    (session_dir / "spikeglx").mkdir(parents=True)
    write_spikeglx(session_dir / "spikeglx", recipe, build_timeline(recipe))
    return session_dir


# The manifest's started_at, naive as the database holds it: the synthetic
# session's start, 09:00 UTC on 2027-03-14.
_STARTED_AT = datetime.datetime(2027, 3, 14, 9, 0, 0)
_UTC = datetime.timezone.utc


@pytest.fixture
def zone(monkeypatch):
    """The NI's zone unset for the test, and unset again after it."""
    from wl_preproc.timebase import clock

    monkeypatch.setattr(clock, "NIDQ_TIMEZONE", None)
    return clock


def test_the_ni_meta_gives_its_creation_time(ni_session, tmp_path):
    """SpikeGLX's `fileCreateTime`, naive, as the recording machine's local
    time; a `.meta` without one gives None."""
    from wl_preproc.timebase._nidq_meta import read_nidq_meta
    from wl_preproc.timebase.clock import nidq_files

    meta_path = nidq_files(ni_session)[0].with_suffix(".meta")
    assert read_nidq_meta(meta_path).file_created == datetime.datetime(2027, 3, 14, 4, 59, 59)
    without = tmp_path / meta_path.name
    without.write_text("".join(line + "\n" for line in meta_path.read_text().splitlines()
                               if not line.startswith("fileCreateTime=")))
    assert read_nidq_meta(without).file_created is None


def test_t_zero_comes_from_the_rig_clock_when_it_agrees(ni_session, zone):
    """Amendment 46, first: the rig's clock fit, 20 s from the manifest's
    start, places t = 0, ahead of the NI file's creation time though that
    agrees too."""
    zone.set_nidq_timezone("America/New_York")
    rig_at_zero = _STARTED_AT.replace(tzinfo=_UTC).timestamp() + 20.0
    reference = zone.Clock(source="nidq", session_dir=ni_session).wall_reference(_STARTED_AT, rig_at_zero=rig_at_zero)
    assert reference == {
        "reference_time": datetime.datetime(2027, 3, 14, 9, 0, 20, tzinfo=_UTC),
        "source": "rig_clock",
        "manifest_started_at": _STARTED_AT.replace(tzinfo=_UTC),
        "started_at_difference_s": -20.0,
    }


def test_a_rig_clock_past_a_minute_falls_back_to_the_ni_file(ni_session, zone):
    """Amendment 46, second: the rig's clock 61 s off is not trusted, so the
    first NI file's `fileCreateTime`, read in the lab's NI zone, places t = 0:
    the NI's first sample, which is t = 0 under the NI."""
    from zoneinfo import ZoneInfo

    from wl_preproc.timebase.clock import nidq_files

    zone.set_nidq_timezone("America/New_York")
    fields = dict(line.split("=", 1) for line in nidq_files(ni_session)[0].with_suffix(".meta").read_text().splitlines())
    created = datetime.datetime.fromisoformat(fields["fileCreateTime"]).replace(tzinfo=ZoneInfo("America/New_York"))
    rig_at_zero = _STARTED_AT.replace(tzinfo=_UTC).timestamp() + 61.0
    reference = zone.Clock(source="nidq", session_dir=ni_session).wall_reference(_STARTED_AT, rig_at_zero=rig_at_zero)
    assert reference["source"] == "nidq_meta"
    assert reference["reference_time"] == created.astimezone(_UTC) == datetime.datetime(2027, 3, 14, 8, 59, 59,
                                                                                       tzinfo=_UTC)
    assert reference["reference_time"].tzinfo == _UTC
    assert reference["started_at_difference_s"] == 1.0
    # With no rig clock fitted, the same.
    assert zone.Clock(source="nidq", session_dir=ni_session).wall_reference(_STARTED_AT) == reference


def test_without_a_zone_the_manifest_places_t_zero(ni_session, zone):
    """Amendment 46, last: with the NI's zone unset its file's time places
    nothing, so the manifest's `started_at` does. Its difference is from the
    first clock that disagreed, or null where none gave a time."""
    ni = zone.Clock(source="nidq", session_dir=ni_session)
    reference = ni.wall_reference(_STARTED_AT)
    assert reference == {"reference_time": _STARTED_AT.replace(tzinfo=_UTC), "source": "manifest",
                         "manifest_started_at": _STARTED_AT.replace(tzinfo=_UTC), "started_at_difference_s": None}
    far = ni.wall_reference(_STARTED_AT, rig_at_zero=_STARTED_AT.replace(tzinfo=_UTC).timestamp() + 90.0)
    assert (far["source"], far["reference_time"], far["started_at_difference_s"]) == (
        "manifest", _STARTED_AT.replace(tzinfo=_UTC), -90.0)


def test_both_clocks_past_a_minute_leave_the_manifest(ni_session, zone):
    """The NI file's time too is checked against the manifest's start: a
    session started an hour after its NI file was created is placed by the
    manifest, its difference the rig's clock's, the first that disagreed."""
    zone.set_nidq_timezone("America/New_York")
    started_at = _STARTED_AT + datetime.timedelta(hours=1)
    rig_at_zero = started_at.replace(tzinfo=_UTC).timestamp() - 120.0
    reference = zone.Clock(source="nidq", session_dir=ni_session).wall_reference(started_at, rig_at_zero=rig_at_zero)
    assert (reference["source"], reference["reference_time"], reference["started_at_difference_s"]) == (
        "manifest", started_at.replace(tzinfo=_UTC), 120.0)
    alone = zone.Clock(source="nidq", session_dir=ni_session).wall_reference(started_at)
    assert (alone["source"], alone["started_at_difference_s"]) == ("manifest", 3601.0)


def test_the_ni_file_is_read_in_the_labs_zone(tmp_path, zone):
    """A Berlin NI machine writes 09:59:59 for the same instant (CET, UTC+1,
    before Europe's summer time on 2027-03-28); in Berlin's zone it is
    08:59:59 UTC, and read as New York's it is five hours off and places
    nothing."""
    from wl_preproc.synth.recipe import NI_CLOCK_RECIPE

    session_dir = _ni_folder(tmp_path, NI_CLOCK_RECIPE.model_copy(update={"nidq_timezone": "Europe/Berlin"}))
    zone.set_nidq_timezone("Europe/Berlin")
    reference = zone.Clock(source="nidq", session_dir=session_dir).wall_reference(_STARTED_AT)
    assert (reference["source"], reference["reference_time"]) == (
        "nidq_meta", datetime.datetime(2027, 3, 14, 8, 59, 59, tzinfo=_UTC))
    zone.set_nidq_timezone("America/New_York")
    assert zone.Clock(source="nidq", session_dir=session_dir).wall_reference(_STARTED_AT)["source"] == "manifest"


@pytest.mark.parametrize("name", ["Mars/Base", "../etc/passwd", "", "/etc/localtime"])
def test_an_unknown_zone_is_refused_at_the_daemon(name, zone, capsys):
    """An unknown or malformed zone is refused by name, one `ValueError`
    whatever `zoneinfo` raised, and leaves the zone unset; `wlpp daemon`
    refuses it before a pass."""
    from wl_preproc.cli.main import main

    with pytest.raises(ValueError, match=f"^{re.escape(repr(name))} is not a time zone"):
        zone.set_nidq_timezone(name)
    assert zone.NIDQ_TIMEZONE is None
    with pytest.raises(SystemExit) as raised:
        main(["daemon", "--nidq-timezone", name])
    assert raised.value.code == 2
    assert f"{name!r} is not a time zone" in capsys.readouterr().err
    assert zone.NIDQ_TIMEZONE is None


def test_a_clock_is_one_of_the_sources_and_lab_time_is_not_built_yet(tmp_path):
    """`SOURCES` is what `timebase.SessionClock` declares; lab time (part 4)
    is declared and not built, and says so rather than answering as another
    clock."""
    from wl_preproc.timebase.clock import Clock, NoClock

    with pytest.raises(NoClock, match="lab time is not built yet"):
        Clock(source="lab", session_dir=tmp_path)
    with pytest.raises(ValueError, match="not a clock"):
        Clock(source="rhs", session_dir=tmp_path)


def test_a_sync_box_log_with_no_barcode_places_no_session_time_and_the_ni_keeps_it(tmp_path):
    """A log holding no barcode record places nothing in session time, so its
    codes hold no run, and the NI's do (amendment 41). The barcodes are its
    `B` records (design spec `2026-10-22-syncbox-records-and-segments-design.md`
    section 3); asked directly, the sync box still cannot time the session."""
    from wl_preproc.timebase.clock import Clock, choose
    from wl_preproc.timebase.extract import find_recordings

    session_dir = _session(tmp_path)
    (log,) = find_recordings("syncbox", session_dir / "syncbox")
    header, *records = log.read_text().splitlines()
    log.write_text("\n".join([header, *(record for record in records if not record.startswith("B,"))]) + "\n")
    choice = choose(session_dir, "pico")
    assert (choice.source, choice.reason) == (
        "nidq", "the NI's codes hold every run the rig recorded; the sync box does not: its log holds no barcode, so "
                "it places nothing in session time")
    with pytest.raises(ValueError, match="the sync box log decoded no barcodes"):
        Clock(source="syncbox", session_dir=session_dir).codes()


# The names that reach the sync box's barcode reference or its codes
# directly: `timebase/segments.py` defines the reference, `events/extract.py`
# reads the log's words, and `timebase/extract.py` its edges, which
# `decode_edges` makes barcodes (amendment 50).
_DIRECT = {"session_reference", "syncbox_codes", "decode_syncbox_in_session_time", "extract_syncbox_words"}


def _reaching(source: str) -> list[int]:
    """The lines of `source` that name a way to the sync box directly: any
    use or import of a `_DIRECT` name, aliased or not, and `extract_syncbox`
    (or `EXTRACTORS["syncbox"]`) in a module that decodes edges. Read by
    `ast`, so a docstring or comment naming one does not count."""
    import ast

    tree = ast.parse(source)

    def named(node) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.alias):
            return node.name.rsplit(".", 1)[-1]
        return None

    nodes = list(ast.walk(tree))
    decodes_edges = any(named(node) == "decode_edges" for node in nodes)
    lines = set()
    for node in nodes:
        name = named(node)
        syncbox_edges = name == "extract_syncbox" or (
            isinstance(node, ast.Subscript) and named(node.value) == "EXTRACTORS"
            and isinstance(node.slice, ast.Constant) and node.slice.value == "syncbox")
        if name in _DIRECT or (decodes_edges and syncbox_edges):
            # An alias carries its line from Python 3.10.
            lines.add(node.lineno)
    return sorted(lines)


def test_the_guard_reads_code_not_text():
    """What the guard below counts: an aliased import, an attribute, the
    sync box's edges decoded; never a docstring or a comment."""
    assert _reaching('"""syncbox_codes(d) is reached through the clock."""\n# session_reference(d)\n') == []
    assert _reaching("from wl_preproc.timebase.clock import syncbox_codes as codes\n") == [1]
    assert _reaching("import wl_preproc.timebase.clock as c\nc.syncbox_codes(d)\n") == [2]
    assert _reaching("from wl_preproc.events.extract import extract_syncbox_words as words\nwords(p)\n") == [1]
    decoding = "from wl_sync.barcode import decode_edges\n"
    assert _reaching(decoding + "from wl_preproc.timebase.extract import extract_syncbox as edges\n") == [2]
    assert _reaching(decoding + "stream = EXTRACTORS['syncbox'](p)\n") == [2]
    assert _reaching("from wl_preproc.timebase.extract import extract_syncbox\n") == []


def test_every_stage_reads_the_sessions_time_through_the_clock():
    """Section 4.3 and amendment 50: nothing outside `timebase/clock.py`
    reaches the sync box's barcode reference or its codes directly, so a
    second clock is one module's change (`timebase/segments.py` defines the
    reference and decodes every system's edges)."""
    from pathlib import Path

    import wl_preproc

    package = Path(wl_preproc.__file__).parent
    allowed = {package / "timebase" / "clock.py", package / "timebase" / "segments.py"}
    reaching = sorted(
        f"{path.relative_to(package)}:{number}"
        for path in package.rglob("*.py")
        if path not in allowed
        for number in _reaching(path.read_text(encoding="utf-8"))
    )
    assert reaching == []


def test_the_nis_covered_span_decodes_only_its_first_file(tmp_path, monkeypatch):
    """Ruling R16: the NI's barcode reference is its first file's, so a
    session with a second NI file, a restart, decodes the first alone. Each
    NI file's barcode line is extracted through `extract.EXTRACTORS`, counted
    here by a wrapper that still extracts, and the cache keeps enough files
    for a backlog of sessions."""
    from wl_preproc.synth.recipe import NI_CLOCK_RECIPE, RestartSpec
    from wl_preproc.timebase import clock, extract

    session_dir, _truth = _generated(tmp_path, NI_CLOCK_RECIPE.model_copy(
        update={"systems": ("syncbox", "spikeglx"), "spikeglx_restart": RestartSpec(at_s=11.0, gap_s=0.5)}))
    first, _second = clock.nidq_files(session_dir)
    extracted = []
    real = extract.EXTRACTORS["spikeglx"]

    def counted(path):
        extracted.append(path)
        return real(path)

    monkeypatch.setitem(extract.EXTRACTORS, "spikeglx", counted)
    clock._ni_barcodes.cache_clear()
    span = clock.Clock(source="nidq", session_dir=session_dir).covered_span()
    assert span is not None
    assert extracted == [first]
    assert clock._ni_barcodes.cache_info().maxsize >= 256
