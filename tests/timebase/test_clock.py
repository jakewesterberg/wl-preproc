"""Which clock keeps a session's time, and the one door every stage reads it
through (design spec `2026-10-18-training-records-and-backup-clock-design.md`
section 4). Part 2 of that design: the sync box is still the only clock, so
each answer here is the sync box's, as the stages computed it before."""

from __future__ import annotations

import datetime

import pytest


def _session(tmp_path, recipe_name: str = "ci"):
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session

    recipe = RECIPES[recipe_name]
    generate_session(tmp_path, recipe)
    return tmp_path / recipe.session_id


def test_a_session_the_sync_box_recorded_is_timed_by_it(tmp_path):
    from wl_preproc.timebase.clock import Choice, choose

    assert choose(_session(tmp_path)) == Choice(source="syncbox", reason="the sync box recorded this session")


def test_a_session_without_a_sync_box_log_has_no_clock_yet(tmp_path):
    """Section 4.2: the NI (part 3) and lab time (part 4) are not built, so a
    session the sync box did not record has no clock, and says why."""
    import shutil

    from wl_preproc.timebase.clock import NoClock, choose

    session_dir = _session(tmp_path)
    shutil.rmtree(session_dir / "syncbox")
    with pytest.raises(NoClock, match="no sync box log, so no clock is built for this session yet"):
        choose(session_dir)


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


def test_only_the_sync_box_clock_is_built_yet(tmp_path):
    """Part 2 builds one clock; a row naming another cannot be answered for
    until its part is built, and says so rather than answering as the sync
    box."""
    from wl_preproc.timebase.clock import Clock, NoClock

    with pytest.raises(NoClock, match="the nidq clock is not built yet"):
        Clock(source="nidq", session_dir=tmp_path)


def test_a_sync_box_log_with_no_barcode_is_the_clock_and_cannot_time_the_session(tmp_path):
    """A log holding no barcode record is still the sync box's: the log is
    there, so the sync box is the clock, and asking it the time raises, as the
    events stage raised before. Nothing chooses another clock (the plan's
    Review Focus 1). The barcodes are its `B` records (design spec
    `2026-10-22-syncbox-records-and-segments-design.md` section 3)."""
    from wl_preproc.timebase.clock import Clock, choose
    from wl_preproc.timebase.extract import find_recordings

    session_dir = _session(tmp_path)
    (log,) = find_recordings("syncbox", session_dir / "syncbox")
    header, *records = log.read_text().splitlines()
    log.write_text("\n".join([header, *(record for record in records if not record.startswith("B,"))]) + "\n")
    assert choose(session_dir).source == "syncbox"
    with pytest.raises(ValueError, match="the sync box log decoded no barcodes"):
        Clock(source="syncbox", session_dir=session_dir).codes()


def test_every_stage_reads_the_sessions_time_through_the_clock():
    """Section 4.3: nothing outside `timebase/clock.py` reaches the sync box's
    barcode reference or its codes directly, so a second clock is one module's
    change (`timebase/segments.py` defines the reference)."""
    import re
    from pathlib import Path

    import wl_preproc

    package = Path(wl_preproc.__file__).parent
    direct = re.compile(r"\b(session_reference|syncbox_codes|decode_syncbox_in_session_time)\(")
    allowed = {package / "timebase" / "clock.py", package / "timebase" / "segments.py"}
    reaching = sorted(
        f"{path.relative_to(package)}:{number}"
        for path in package.rglob("*.py")
        if path not in allowed
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if direct.search(line) and not line.lstrip().startswith("#")
    )
    assert reaching == []
