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
        (1, recipe.blocks[0].task_type.name.lower(), False, None),
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
