"""The landed-session listing's entries, read from what one daemon pass
measured (design spec `2026-10-01-session-listing-and-run-requests-design.md`
section 2). Subjects and dates checked unclaimed across `tests/` on
2026-10-01."""

from __future__ import annotations

import pytest

from tests.schema.test_spikeglx_restart import RESTART, _session


@pytest.fixture(scope="module")
def listed(dj_conn, prefix, tmp_path_factory):
    """Two sessions, then one daemon pass. `runs`: its blocks wrapped in
    runs, run 1 faulted (its block unclosed), and SpikeGLX restarted at 11 s
    with a new bank, inside run 2. `plain`: no run markers."""
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    sessions = {
        "runs": _session(tmp_path_factory, subject="sllist1", session_id="2025-07-21_01", runs=True,
                         unclosed_blocks=[1], spikeglx_restart={**RESTART, "probe_bank": 1}),
        "plain": _session(tmp_path_factory, subject="sllist2", session_id="2025-07-22_01"),
    }
    daemon.run_once(prefix=prefix)
    return sessions


def test_an_entry_reads_what_the_daemon_measured(listed):
    from wl_preproc.listing.entry import session_entry
    from wl_preproc.schema import ephys

    recipe, key = listed["runs"]
    entry = session_entry(key)
    assert (entry["subject"], entry["session_name"], entry["probes"]) == (
        "sllist1", recipe.session_id, [recipe.probe_serial])
    assert entry["tier"] in ("A", "B", "C", "D")
    assert [(run["run_number"], run["task"], run["closed"], run["stop_kind"]) for run in entry["runs"]] == [
        (1, recipe.blocks[0].task_type.name.lower(), False, "fault"),
        (2, recipe.blocks[1].task_type.name.lower(), True, "completed")]
    assert [[(block["block_number"], block["block_type"], block["closed"], block["n_trials"])
             for block in run["blocks"]] for run in entry["runs"]] == [[(1, "block-1", False, 3)],
                                                                      [(2, "block-2", True, 1)]]
    first, second = entry["segments"]
    assert [run["segments"] for run in entry["runs"]] == [[first["segment_barcode"]],
                                                          [first["segment_barcode"], second["segment_barcode"]]]
    census = (ephys.ProbeCensus.Probe & key).to_dicts(order_by="segment_barcode")
    assert [segment["probes"][0]["imro_table"] for segment in entry["segments"]] == [
        part["imro_table"] for part in census]
    assert [segment["probes"][0]["electrodes"][0] for segment in entry["segments"]] == [0, 384]
    assert [(flag["code"], flag["run_number"]) for flag in entry["flags"]] == [("bank_change_in_run", 2)]


def test_a_session_without_run_markers_is_listed_waiting(listed):
    from wl_preproc.listing.entry import session_entry

    _recipe, key = listed["plain"]
    entry = session_entry(key)
    assert entry["runs"] == []
    assert [flag["code"] for flag in entry["flags"]] == ["waiting_for_run_markers"]



def test_get_sessions_lists_each_logged_session_as_it_stands_and_its_cursor(listed, prefix):
    from wl_preproc.listing.entry import session_entry
    from wl_preproc.responder.sessions import list_sessions
    from wl_preproc.schema import ingest

    listing = list_sessions(None, prefix=prefix)
    assert listing["cursor"] == max(ingest.SessionChange.to_arrays("change_seq"))
    by_subject = {entry["subject"]: entry for entry in listing["sessions"]}
    for _recipe, key in listed.values():
        assert by_subject[key["subject"]] == session_entry(key)
    assert list_sessions(listing["cursor"], prefix=prefix) == {"cursor": listing["cursor"], "sessions": []}


# -- The change log and the listing stage (section 2.1). Run after the
# entries' tests: these append changes and restore what they change.


def _changes(key):
    from wl_preproc.schema import ingest

    return (ingest.SessionChange & key).to_dicts(order_by="change_seq")


def test_one_pass_logs_each_listed_session_once_with_its_entrys_digest(listed):
    from wl_preproc.listing.entry import session_entry
    from wl_preproc.listing.stage import digest

    for _recipe, key in listed.values():
        (change,) = _changes(key)
        assert change["digest"] == digest(session_entry(key))


def test_an_unchanged_entry_is_not_logged_again(listed):
    from wl_preproc.listing.stage import run_stage

    before = {name: len(_changes(key)) for name, (_recipe, key) in listed.items()}
    _appended, errors = run_stage()
    assert errors == []
    assert {name: len(_changes(key)) for name, (_recipe, key) in listed.items()} == before


def test_a_fact_that_arrives_later_lists_the_session_again(listed, prefix):
    """A rejected segment recorded after the first listing changes the entry,
    so that session alone is logged again; removing it logs it once more."""
    from wl_preproc.listing.stage import run_stage
    from wl_preproc.schema import core

    _recipe, key = listed["runs"]
    _plain, other = listed["plain"]
    row = {**key, "system": "spikeglx", "file_path": "late/run_g9_t0.nidq.bin", "reason": "late"}
    from wl_preproc.responder.sessions import list_sessions
    from wl_preproc.schema import ingest

    before, other_before = len(_changes(key)), len(_changes(other))
    cursor = max(ingest.SessionChange.to_arrays("change_seq"))
    core.RejectedSegment.insert1(row)
    try:
        run_stage()
        assert (len(_changes(key)), len(_changes(other))) == (before + 1, other_before)
    finally:
        (core.RejectedSegment & row).delete_quick()
    run_stage()
    assert len(_changes(key)) == before + 2
    # A reader holding the cursor from before sees that session alone.
    assert [entry["subject"] for entry in list_sessions(cursor, prefix=prefix)["sessions"]] == ["sllist1"]


def test_a_pass_leaves_the_listing_to_a_process_holding_its_lock(listed, prefix):
    from tests.schema.test_request import _raw_connection
    from wl_preproc import daemon
    from wl_preproc.nwb.lock import lock_name
    from wl_preproc.schema import core

    _recipe, key = listed["runs"]
    row = {**key, "system": "spikeglx", "file_path": "late/run_g8_t0.nidq.bin", "reason": "late"}
    before = len(_changes(key))
    core.RejectedSegment.insert1(row)
    other = _raw_connection()
    try:
        with other.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 0)", (lock_name(prefix, "listing"),))
            assert cursor.fetchone()[0] == 1
        report = daemon.run_once(prefix=prefix)
    finally:
        other.close()
        (core.RejectedSegment & row).delete_quick()
    assert len(_changes(key)) == before
    assert any(error.startswith("SessionChange: another wlpp process holds the listing lock")
               for error in report["errors"])
