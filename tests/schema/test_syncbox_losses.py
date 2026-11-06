"""The sync box's losses, stored and checked (design spec
`2026-11-04-syncbox-header-design.md` sections 6 and 8): each `L` record kept
as the box wrote it, and each gap, in `timebase.SyncBoxSegment.Loss`, placed in
session time; a desync, or code words lost in a measured run, fail the
session's timing check, and a lost edge does not. A database declared before
the header named its process is refused, and told how to alter it."""

from __future__ import annotations

import pytest

from tests.identities import new_animal


@pytest.fixture(scope="module")
def landed(dj_conn, prefix, tmp_path_factory):
    """Six `ci` sessions of one animal on one day, in runs, after one daemon
    pass, deleted after the module: the box's word pump stopped by a desync at
    6 s; an edge lost on a photodiode inside the first run; a gap of 3 s from
    5.5 s, inside a run; a box whose code words are on a split clock; a box
    that restarted at 7.5 s; and a photodiode line that garbled three pairs
    of edges. Yields their keys, by name, the daemon's report, and the folder
    they were landed from."""
    from wl_sync.pins import GPIO_MAP

    from wl_preproc import daemon
    from wl_preproc.schema import pipeline
    from wl_preproc.synth.recipe import RECIPES, SyncBoxLossSpec, SyncBoxRestartSpec, SyncBoxRollSpec
    from wl_preproc.synth.session import generate_session

    from tests.schema.test_eye_populate import _land

    daemon.activate_all(prefix=prefix)
    animal = new_animal()
    root = tmp_path_factory.mktemp("losses")
    keys = {}
    for name, update in (
        ("desync", {"syncbox_losses": (SyncBoxLossSpec(cause="desync", count=2, from_s=6.0),)}),
        ("edge", {"syncbox_losses": (SyncBoxLossSpec(cause="buffer_overflow", count=3, from_s=1.2, to_s=1.4,
                                                     gpio=GPIO_MAP["pd1_comp"]),)}),
        ("gap", {"syncbox_roll": SyncBoxRollSpec(at_s=5.5, gap_s=3.0)}),
        ("split", {"syncbox_timebase": "split"}),
        ("restart", {"syncbox_restart": SyncBoxRestartSpec(at_s=7.5, gap_s=1.2)}),
        ("noisy", {"syncbox_losses": tuple(
            SyncBoxLossSpec(cause="coalesced", from_s=from_s, to_s=to_s, gpio=GPIO_MAP["pd1_comp"])
            for from_s, to_s in ((1.21, 1.22), (1.5, 1.51), (2.0, 2.01)))}),
    ):
        session = animal.session(on=animal.first_day)
        recipe = RECIPES["ci"].model_copy(update={"subject": session.subject, "session_id": session.session_id,
                                                  "runs": True, **update})
        generate_session(root, recipe)
        keys[name] = _land(root, recipe, session.session_datetime, acquisition_systems=recipe.systems)
    report = daemon.run_once(prefix=prefix)
    yield keys, report, root
    for key in keys.values():
        (pipeline.Session & key).delete(prompt=False)


def test_each_loss_is_stored_as_the_box_wrote_it_and_placed_in_session_time(landed):
    from wl_sync.pins import GPIO_MAP

    from wl_preproc.schema import timebase

    keys, _, _ = landed
    (row,) = (timebase.SyncBoxSegment.Loss & keys["edge"]).to_dicts()
    assert ({field: row[field] for field in ("segment_index", "loss_number", "cause", "path", "gpio", "lost",
                                             "bound", "tick_us", "before_us", "after_us")}
            == {"segment_index": 0, "loss_number": 1, "cause": "buffer_overflow", "path": "E",
                "gpio": GPIO_MAP["pd1_comp"], "lost": 3, "bound": "exact", "tick_us": 2_400_000,
                "before_us": 2_200_000, "after_us": 2_400_000})
    assert (row["start_s"], row["stop_s"]) == pytest.approx((1.2, 1.4))
    (gap,) = (timebase.SyncBoxSegment.Loss & keys["gap"]).to_dicts()
    assert (gap["segment_index"], gap["cause"], gap["lost"]) == (1, "gap", None)
    assert gap["start_s"] < 5.5 and 8.5 <= gap["stop_s"] < 9.5


def test_a_desync_or_a_gap_in_a_run_fails_the_timing_check_and_a_lost_edge_does_not(landed):
    from wl_preproc.schema import timebase

    keys, report, _ = landed
    assert {name: (timebase.TimingProvenance & keys[name]).fetch1("tier")
            for name in ("desync", "edge", "gap", "split", "noisy")} == {
        "desync": "D", "edge": "A", "gap": "D", "split": "A", "noisy": "A"}
    assert not [error for error in report["errors"] if any(key["subject"] in str(error) for key in keys.values())]


def test_each_segment_records_its_process_its_clocks_and_how_it_follows(landed):
    from wl_preproc.schema import timebase
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.syncbox import process_uuid

    keys, _, _ = landed
    rows = (timebase.SyncBoxSegment & keys["gap"]).to_dicts(order_by="segment_index")
    process = process_uuid(RECIPES["ci"], 0)
    assert [(row["process_uuid"], row["timebase"], row["follows"], row["role"]) for row in rows] == [
        (process, "fake", None, "session_time"), (process, "fake", "gap", "session_time")]
    assert all(row["last_code_tick_us"] > 0 for row in rows)
    assert (timebase.SyncBoxSegment & keys["split"]).fetch1("timebase") == "split"


def test_a_database_without_the_header_columns_records_nothing_and_names_the_statement(landed, monkeypatch):
    """As `EyeValidity` refuses without `frac_feature_lost`: DataJoint adds
    no column to an existing table and reads a table's columns once per
    process, so the stage refuses before any job is reserved and says how to
    fix it."""
    from wl_preproc.schema import timebase

    assert not timebase._lacks_header_columns()
    monkeypatch.setattr(timebase, "_lacks_header_columns", lambda: True)
    with pytest.raises(RuntimeError) as raised:
        timebase.SyncBoxSegment.populate()
    assert timebase._header_statement() in str(raised.value)
    assert "restart" in str(raised.value)


def test_a_table_read_without_the_columns_is_refused(landed, monkeypatch):
    from types import SimpleNamespace

    from wl_preproc.schema import timebase

    names = [name for name in timebase.SyncBoxSegment.heading.names if name != "follows"]
    monkeypatch.setattr(timebase.SyncBoxSegment, "_heading", SimpleNamespace(names=names))
    assert timebase._lacks_header_columns()
    with pytest.raises(RuntimeError, match="has no columns"):
        timebase.SyncBoxSegment.populate()


def test_a_faulted_runs_stop_reaches_the_timing_check_as_unknown(landed):
    """A run with no RUN_END stops, in `core.Run`, at its last event, which
    is not its end: the timing check is handed None for it."""
    from wl_preproc.schema import core, timebase

    keys, _, _ = landed
    first, *rest = (core.Run & keys["gap"]).to_dicts(order_by="run_number")
    key = {name: first[name] for name in core.Run.primary_key}
    core.Run.update1({**key, "closed": 0})
    try:
        runs = timebase.measured_runs(keys["gap"])
    finally:
        core.Run.update1({**key, "closed": 1})
    assert runs == [(first["run_start_time"], None), *((run["run_start_time"], run["run_stop_time"]) for run in rest)]


def test_a_segment_keeps_more_losses_than_a_smallint_counts(landed):
    """A chattering line writes a `coalesced` record for each pair of edges
    it garbles, so one segment can hold far more than 65,535."""
    from wl_preproc.schema import timebase

    keys, _, _ = landed
    row = {**(timebase.SyncBoxSegment.Loss & keys["edge"]).to_dicts()[0], "loss_number": 70_000}
    key = {name: row[name] for name in timebase.SyncBoxSegment.Loss.primary_key}
    try:
        timebase.SyncBoxSegment.Loss.insert1(row, allow_direct_insert=True)
        assert len(timebase.SyncBoxSegment.Loss & key) == 1
    finally:
        (timebase.SyncBoxSegment.Loss & key).delete_quick()


def test_the_statement_restores_the_columns_as_datajoint_declares_them(landed):
    """Dropped and restored by the named statement on a copy of the table:
    type, nullability, default, comment and place match what declaring it
    wrote, and no row of the table itself is touched."""
    import datajoint as dj

    from wl_preproc.schema import timebase

    table = timebase.SyncBoxSegment.full_table_name
    copy = table[:-1] + '_restore_check`'
    statement = timebase._header_statement()
    assert statement.count(table) == 1

    def columns(name):
        return [row[:2] + row[3:6] + row[8:9] for row in dj.conn().query(f"SHOW FULL COLUMNS FROM {name}").fetchall()]

    dj.conn().query(f"CREATE TABLE {copy} LIKE {table}")
    try:
        dj.conn().query(f"ALTER TABLE {copy} " + ", ".join(
            f"DROP COLUMN {column}" for column in timebase._HEADER_COLUMNS))
        dj.conn().query(statement.replace(table, copy))
        restored = columns(copy)
    finally:
        dj.conn().query(f"DROP TABLE IF EXISTS {copy}")
    assert restored == columns(table)


def _report(landed, prefix):
    """The daily report two hours after the fixture's last session, so all
    four are in its 24 h."""
    import datetime

    from wl_preproc.cli.report import build_report

    keys, _, root = landed
    at = max(key["session_datetime"] for key in keys.values()) + datetime.timedelta(hours=2)
    return build_report(root, prefix=prefix, now=at.replace(tzinfo=datetime.UTC))


def _at(key) -> str:
    return f"@ {key['session_datetime']:%Y-%m-%d %H:%M}"


def test_the_report_names_each_loss_and_what_it_did_to_its_session(landed, prefix):
    """Section 6: path, cause, count with its bound, window in session time,
    and whether it failed the timing check."""
    from wl_sync.pins import GPIO_MAP

    from tests.cli.test_report import _declared_count, _line_for, _section

    keys, _, _ = landed
    section = _section(_report(landed, prefix), "Sync box losses (24 h)")
    assert ("at least 2 code words lost (desync), from 6.000 s on; fails the session's timing check (tier D)"
            in _line_for(section, _at(keys["desync"])))
    assert (f"3 edges lost on GPIO{GPIO_MAP['pd1_comp']} (buffer_overflow), from 1.200 s to 1.400 s; reported only"
            in _line_for(section, _at(keys["edge"])))
    gap = _line_for(section, _at(keys["gap"]))
    assert "the box recorded elsewhere" in gap and gap.endswith("fails the session's timing check (tier D)")
    assert not [line for line in section.splitlines() if _at(keys["split"]) in line]
    assert _declared_count(section) == len([line for line in section.splitlines() if line.startswith("- ")])


def test_the_report_names_each_session_whose_code_words_are_on_a_split_clock(landed, prefix):
    """Section 7: with the most its code words can have drifted from its
    barcodes at 50 ppm; a box on the test backend is not named."""
    from tests.cli.test_report import _declared_count, _line_for, _section

    keys, _, _ = landed
    section = _section(_report(landed, prefix), "Code words on a split clock (24 h)")
    assert ("timebase `split`: its code words can lie up to 0.001 s from its barcodes at 50 ppm, the rate "
            "unmeasured; nothing is corrected" in _line_for(section, _at(keys["split"])))
    assert not [line for line in section.splitlines() if _at(keys["edge"]) in line]
    assert _declared_count(section) == len([line for line in section.splitlines() if line.startswith("- ")])


def test_a_loss_or_a_split_clock_older_than_a_day_is_not_listed(landed, prefix):
    """Both lists are the report's 24 h, as its per-session lists are: a loss
    is a fact of its session, not a state that clears."""
    import datetime

    from wl_preproc.cli.report import build_report

    from tests.cli.test_report import _section

    keys, _, root = landed
    later = max(key["session_datetime"] for key in keys.values()) + datetime.timedelta(days=3)
    body = build_report(root, prefix=prefix, now=later.replace(tzinfo=datetime.UTC))
    for heading in ("Sync box losses (24 h)", "Code words on a split clock (24 h)"):
        section = _section(body, heading)
        assert not [line for line in section.splitlines() if any(_at(key) in line for key in keys.values())]


def test_a_lines_many_edge_losses_are_one_line_of_the_report(landed, prefix):
    """A chattering line writes a record for each pair of edges it garbles:
    the report names the line once, with how many records and their span,
    their counts not added, as wl-sync's counts do not add across records."""
    from wl_sync.pins import GPIO_MAP

    from tests.cli.test_report import _declared_count, _line_for, _section

    keys, _, _ = landed
    section = _section(_report(landed, prefix), "Sync box losses (24 h)")
    assert (f"3 records of edges lost on GPIO{GPIO_MAP['pd1_comp']} (coalesced), from 1.210 s to 2.010 s; "
            "reported only" in _line_for(section, _at(keys["noisy"])))
    assert _declared_count(section) == len([line for line in section.splitlines() if line.startswith("- ")])


def test_the_report_on_a_table_read_without_the_columns_names_the_statement(landed, prefix, monkeypatch):
    """A database declared before the header named its process, its rows read
    without the new columns, as DataJoint reads a table's columns once per
    process: the report still lists the box's restarts, and in place of the
    split clocks it cannot read names the statement that alters the table
    (section 8)."""
    from wl_preproc.schema import timebase

    from tests.cli.test_report import _line_for, _section

    keys, _, _ = landed
    heading = timebase.SyncBoxSegment.heading
    monkeypatch.setattr(timebase.SyncBoxSegment, "_heading", heading.select(
        [name for name in heading.names if name not in timebase._HEADER_COLUMNS]))
    report = _report(landed, prefix)
    assert "the sync box restarted" in _line_for(_section(report, "Sync box restarts"), _at(keys["restart"]))
    split = _section(report, "Code words on a split clock (24 h)")
    assert split.startswith(" — not read\n")
    assert timebase._header_statement() in split
