"""One landed session's entry in `GET /sessions`, built from rows made by hand
(design spec `2026-10-01-session-listing-and-run-requests-design.md` sections
2.2 and 2.4)."""

from __future__ import annotations

import dataclasses
import datetime

WHEN = datetime.datetime(2027, 7, 22, 9, 0)


def _probe(barcode, config, serial="19011110001"):
    return {"segment_barcode": barcode, "stream": "imec0", "probe_serial": serial, "part_number": "NP1000",
            "electrode_config_hash": config, "probe_type": "NP1000", "problem": "",
            "imro_table": f"(0,384)({config})"}


def _facts(**changes):
    """Two runs of one block each; one SpikeGLX segment spanning both."""
    from wl_preproc.listing.entry import SessionFacts

    facts = SessionFacts(
        subject="pico", session_datetime=WHEN, session_name="2027-07-22_01", tier="A", rejected=[],
        runs=[{"run_number": 1, "task_type": 0, "run_start_time": 1.0, "run_stop_time": 10.0, "closed": 1},
              {"run_number": 2, "task_type": 0, "run_start_time": 12.0, "run_stop_time": 20.0, "closed": 1}],
        records={1: {"task": "rf_map", "stopped_because": "every block is finished", "stop_kind": "completed"},
                 2: {"task": "fixation", "stopped_because": "stopped by jw", "stop_kind": "operator"}},
        blocks=[{"block_id": 1, "block_start_time": 1.004, "block_stop_time": 9.9},
                {"block_id": 2, "block_start_time": 12.004, "block_stop_time": 19.9}],
        block_attributes={1: {"closed": "1", "block_type": "Bt1"}, 2: {"closed": "1", "block_type": "Bt2"}},
        trial_counts={1: 3, 2: 1},
        segments=[{"segment_barcode": 100, "start_s": 0.5, "end_s": 21.0}],
        census=[_probe(100, "a" * 32)],
        electrodes={("a" * 32, "NP1000"): [0, 1, 2]},
        strobed_runs=[1, 2], strobed_blocks=[1, 2],
    )
    return dataclasses.replace(facts, **changes)


def test_a_run_lists_its_task_its_stop_its_segments_and_its_blocks():
    from wl_preproc.listing.entry import build_entry

    entry = build_entry(_facts())
    assert (entry["session_name"], entry["tier"], entry["probes"], entry["flags"]) == (
        "2027-07-22_01", "A", ["19011110001"], [])
    first = entry["runs"][0]
    assert {name: first[name] for name in ("run_number", "task", "start_s", "end_s", "closed", "stopped_because",
                                           "stop_kind", "segments")} == {
        "run_number": 1, "task": "rf_map", "start_s": 1.0, "end_s": 10.0, "closed": True,
        "stopped_because": "every block is finished", "stop_kind": "completed", "segments": [100]}
    assert first["blocks"] == [{"block_number": 1, "block_in_run": 1, "block_type": "Bt1", "start_s": 1.004,
                                "end_s": 9.9, "closed": True, "n_trials": 3}]
    (segment,) = entry["segments"]
    assert segment["probes"] == [{"stream": "imec0", "serial": "19011110001", "part_number": "NP1000",
                                  "probe_type": "NP1000", "electrode_config_hash": "a" * 32, "n_electrodes": 3,
                                  "electrodes": [0, 1, 2], "imro_table": f"(0,384)({'a' * 32})", "problem": ""}]


def test_a_recording_with_no_measured_run_is_listed_as_waiting():
    from wl_preproc.listing.entry import build_entry

    entry = build_entry(_facts(runs=[], records={}, strobed_runs=[]))
    assert entry["runs"] == []
    assert [flag["code"] for flag in entry["flags"]] == ["waiting_for_run_markers"]


def test_a_repeated_run_or_block_number_is_named_once():
    """A crash restart before wl-xcon's XC-026 (the requester's decision 2):
    the tables keep the first; every occurrence is in `Event`."""
    from wl_preproc.listing.entry import build_entry

    entry = build_entry(_facts(strobed_runs=[1, 2, 1, 2, 1], strobed_blocks=[1, 2, 1]))
    assert [(flag["code"], flag["run_number"], flag["block_number"]) for flag in entry["flags"]] == [
        ("repeated_run_number", 1, None), ("repeated_run_number", 2, None), ("repeated_block_number", None, 1)]
    assert entry["flags"][0]["message"] == ("run number 1 appears 3 times in the recording; only the first is "
                                            "listed, and the restarted runs wait for wl-xcon's XC-026")


def test_a_run_that_stopped_before_its_first_trial_has_no_block():
    from wl_preproc.listing.entry import build_entry

    entry = build_entry(_facts(blocks=[{"block_id": 1, "block_start_time": 1.004, "block_stop_time": 9.9}],
                               strobed_blocks=[1]))
    assert entry["runs"][1]["blocks"] == []
    assert [(flag["code"], flag["run_number"]) for flag in entry["flags"]] == [("run_without_block", 2)]


def test_a_bank_change_inside_a_run_is_flagged_and_one_between_runs_is_not():
    """Runs and segments do not align: a segment boundary inside run 2 with a
    different site map is a bank change in that run; the same boundary in the
    gap between runs is not."""
    from wl_preproc.listing.entry import build_entry

    inside = _facts(segments=[{"segment_barcode": 100, "start_s": 0.5, "end_s": 15.0},
                              {"segment_barcode": 200, "start_s": 15.5, "end_s": 21.0}],
                    census=[_probe(100, "a" * 32), _probe(200, "b" * 32)],
                    electrodes={("a" * 32, "NP1000"): [0, 1, 2], ("b" * 32, "NP1000"): [384, 385, 386]})
    entry = build_entry(inside)
    assert [run["segments"] for run in entry["runs"]] == [[100], [100, 200]]
    assert [(flag["code"], flag["run_number"]) for flag in entry["flags"]] == [("bank_change_in_run", 2)]

    between = dataclasses.replace(inside, segments=[{"segment_barcode": 100, "start_s": 0.5, "end_s": 11.0},
                                                    {"segment_barcode": 200, "start_s": 11.5, "end_s": 21.0}])
    assert build_entry(between)["flags"] == []


def test_a_run_or_block_the_rig_did_not_name_is_flagged():
    from wl_preproc.listing.entry import build_entry

    entry = build_entry(_facts(records={1: {"task": "rf_map", "stopped_because": None, "stop_kind": None}},
                               block_attributes={1: {"closed": "0"}, 2: {"closed": "1", "block_type": "Bt2"}}))
    assert [(flag["code"], flag["run_number"], flag["block_number"]) for flag in entry["flags"]] == [
        ("block_type_unknown", 1, 1), ("task_unknown", 2, None)]
    assert entry["runs"][0]["blocks"][0]["closed"] is False


def test_a_block_in_no_run_is_flagged_when_the_session_has_runs():
    """Its run's RUN_START was lost. With no runs at all, the session is
    waiting for its run markers instead."""
    from wl_preproc.listing.entry import build_entry

    stray = {"block_id": 3, "block_start_time": 25.0, "block_stop_time": 26.0}
    entry = build_entry(_facts(blocks=[*_facts().blocks, stray], strobed_blocks=[1, 2, 3]))
    assert [(flag["code"], flag["block_number"]) for flag in entry["flags"]] == [("block_outside_runs", 3)]
    waiting = build_entry(_facts(runs=[], records={}, strobed_runs=[], blocks=[stray], strobed_blocks=[3]))
    assert [flag["code"] for flag in waiting["flags"]] == ["waiting_for_run_markers"]


def test_a_probe_the_census_could_not_name_is_listed_with_its_problem():
    """It is in its segment, with no site map, and among no serials; it
    cannot make a bank change."""
    from wl_preproc.listing.entry import build_entry

    unnamed = {"segment_barcode": 100, "stream": "imec1", "probe_serial": None, "part_number": None,
               "electrode_config_hash": None, "probe_type": None, "problem": "the .meta names no serial (imDatPrb_sn)",
               "imro_table": None}
    entry = build_entry(_facts(census=[_probe(100, "a" * 32), unnamed]))
    assert entry["segments"][0]["probes"][1] == {
        "stream": "imec1", "serial": None, "part_number": None, "probe_type": None, "electrode_config_hash": None,
        "n_electrodes": None, "electrodes": None, "imro_table": None,
        "problem": "the .meta names no serial (imDatPrb_sn)"}
    assert (entry["probes"], entry["flags"]) == (["19011110001"], [])


def test_a_session_listed_before_its_timing_and_census_has_neither():
    """The event stage is done, and the computed tables are not yet: the
    session is listed now, and again when they are."""
    from wl_preproc.listing.entry import build_entry

    entry = build_entry(_facts(tier=None, segments=[], census=[], electrodes={}))
    assert (entry["tier"], entry["segments"], entry["probes"], entry["flags"]) == (None, [], [], [])
    assert [run["segments"] for run in entry["runs"]] == [[], []]
