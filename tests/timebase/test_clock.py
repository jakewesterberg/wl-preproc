"""Which clock keeps a session's time, and the one door every stage reads it
through (design spec `2026-10-18-training-records-and-backup-clock-design.md`
section 4, and amendments 41-44, 47 and 50): the sync box where its codes hold
every run or it restarted within the session, else the NI where its codes do,
else no clock until lab time (part 4)."""

from __future__ import annotations

import datetime

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
    keeps the box, and its reason says a run before the last is not held."""
    from wl_preproc.synth.recipe import SyncBoxRollSpec
    from wl_preproc.timebase.clock import Choice, choose

    session_dir = _session(tmp_path, runs=True, syncbox_roll=SyncBoxRollSpec(at_s=5.5, gap_s=3.0))
    assert choose(session_dir, "pico") == Choice(
        source="syncbox", reason="the sync box's codes reach the rig's last run; a run before it is not in its codes")


def test_a_sync_box_with_a_gap_inside_the_last_run_stays_the_clock(tmp_path):
    """Amendment 42's "its log ends before the session's last run does": a
    gap inside the last run (8.5 s to 10 s, losing its second trial) that
    resumes before its last trial reaches the run's end, so the box stays
    the clock."""
    from wl_preproc.contracts.events import TaskTypeCode
    from wl_preproc.synth.recipe import BlockSpec, SyncBoxRollSpec
    from wl_preproc.timebase.clock import Choice, choose

    session_dir = _session(tmp_path, runs=True, syncbox_roll=SyncBoxRollSpec(at_s=8.5, gap_s=1.5), blocks=(
        BlockSpec(task_type=TaskTypeCode.RESTING_DARK, n_trials=1, trial_duration_s=6.0),
        BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=3, trial_duration_s=3.0)))
    assert choose(session_dir, "pico") == Choice(
        source="syncbox", reason="the sync box's codes reach the rig's last run; a run before it is not in its codes")


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
    sample, so each code sits at its emission plus SpikeGLX's pre-roll, read
    at the strobe's far edge (`events/extract.py::extract_nidq_words`)."""
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.rhs import STROBE_WIDTH_S
    from wl_preproc.synth.spikeglx import NIDQ_SAMPLE_RATE_HZ, SPIKEGLX_PRE_ROLL_S
    from wl_preproc.synth.timeline import apply_drift
    from wl_preproc.timebase.clock import Clock

    recipe = RECIPES["ni-clock"]
    session_dir, truth = _generated(tmp_path, recipe)
    drift = dict(recipe.system_drift_ppm).get("spikeglx", recipe.drift_ppm)
    codes = Clock(source="nidq", session_dir=session_dir).codes()
    assert [code for _time, code in codes] == [word for _time, word in truth.code_words]
    latch_s = (round(STROBE_WIDTH_S * NIDQ_SAMPLE_RATE_HZ) - 1) / NIDQ_SAMPLE_RATE_HZ
    for (time_s, _code), (emitted_s, _word) in zip(codes, truth.code_words, strict=True):
        assert time_s == pytest.approx(apply_drift(emitted_s, drift) + SPIKEGLX_PRE_ROLL_S + latch_s,
                                       abs=1 / NIDQ_SAMPLE_RATE_HZ)


def test_the_ni_clock_places_no_barcode(tmp_path):
    from wl_preproc.timebase.clock import Clock, NoClock

    with pytest.raises(NoClock, match="the NI's clock places no barcode"):
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
