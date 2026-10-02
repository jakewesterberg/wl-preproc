"""Linking wl.works' report of each insertion to the probe the recording
names (design spec `2026-09-30-nwb-probes-design.md` section 2.3): each
daemon pass writes `ProbeInsertion`, `InsertionLocation` and `SegmentConfig`
from `InsertionReport` and `ProbeCensus`, in either arrival order, and a
corrected report relinks on the next pass. Subjects and dates checked
unclaimed across `tests/` on 2026-09-30."""

from __future__ import annotations

import datetime

import pytest

from tests.schema.test_spikeglx_restart import RESTART, _session

_SERIAL_1, _SERIAL_2 = "19011110001", "19011110002"
_TWO_PROBES = {"extra_probes": [{"serial": _SERIAL_2, "part_number": "NP1032", "bank": 1}],
               "spikeglx_restart": {**RESTART, "probe_bank": 1}}
_AIM = {"area": "V4d", "atlas": "CHARM", "atlas_level": 6}


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


def _measured(key):
    """The session's runs read by the event stage, which runs before the
    census in a daemon pass: a request names measured runs (design spec
    `2026-10-01-session-listing-and-run-requests-design.md` section 3.2), so
    the earliest a report can arrive is after this and before the census."""
    from wl_preproc import daemon

    daemon._populate_event_stage()
    return key


def _landed(tmp_path_factory, subject, session_id, **update):
    _recipe, key = _session(tmp_path_factory, subject=subject, session_id=session_id, runs=True, **update)
    return _measured(key)


def _report(key, idempotency_key, probes, prefix):
    """wl.works' job request for the session's canonical, carrying `probes`,
    each sorting every run. A second one for the same montage returns the
    same activation, and its report is recorded all the same."""
    from wl_preproc.contracts.protocol import JobRequest, MetadataBundle
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core

    runs = [{"run_number": row["run_number"], "start_s": row["run_start_time"], "end_s": row["run_stop_time"],
             "works_run_id": f"wr-{row['run_number']}"} for row in (core.Run & key).to_dicts(order_by="run_number")]
    return accept(JobRequest(
        domain="neural", parameters={}, idempotency_key=idempotency_key,
        selection={"session_datetime": key["session_datetime"].replace(tzinfo=datetime.UTC), "montage_id": 0,
                   "probe_runs": {probe["serial"]: [run["run_number"] for run in runs] for probe in probes}},
        metadata=MetadataBundle(runs=runs, montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 16.0}],
                                probes=probes, experimenter="jw", subject=key["subject"], task_types=[]),
    ), prefix=prefix)


def _links(key):
    """`{insertion_number: (serial, trajectory, aim, {segment: config})}`."""
    from wl_preproc.schema import ephys

    links = {}
    for row in (ephys.ProbeInsertion & key).to_dicts():
        where = {**key, "insertion_number": row["insertion_number"]}
        location = (ephys.InsertionLocation & where).to_dicts()
        aim = (location[0]["area"], location[0]["atlas"], location[0]["atlas_level"]) if location else None
        configs = {c["segment_barcode"]: c["electrode_config_hash"] for c in (ephys.SegmentConfig & where).to_dicts()}
        links[row["insertion_number"]] = (row["probe_serial"], row["trajectory_id"], aim, configs)
    return links


def _recorded(key, serial):
    """`{segment: config}` for what the census recorded of `serial`."""
    from wl_preproc.schema import ephys

    parts = ephys.ProbeCensus.Probe & key & {"probe_serial": serial}
    return {p["segment_barcode"]: p["electrode_config_hash"] for p in parts.to_dicts()}


@pytest.mark.parametrize("report_first", [True, False], ids=["report-first", "recording-first"])
def test_a_report_links_to_the_probe_the_recording_names(daemon_module, prefix, tmp_path_factory, report_first):
    subject, session_id = ("plink1", "2025-06-15_01") if report_first else ("plink2", "2025-06-16_01")
    key = _landed(tmp_path_factory, subject, session_id, **_TWO_PROBES)
    probes = [{"serial": _SERIAL_1, "insertion_number": 1, "trajectory_id": "T-1", "target": _AIM},
              {"serial": _SERIAL_2, "insertion_number": 2}]
    if report_first:
        _report(key, f"{subject}-k1", probes, prefix)
    daemon_module.run_once(prefix=prefix)
    if not report_first:
        _report(key, f"{subject}-k1", probes, prefix)
        daemon_module.run_once(prefix=prefix)

    assert _links(key) == {
        1: (_SERIAL_1, "T-1", ("V4d", "CHARM", 6), _recorded(key, _SERIAL_1)),
        2: (_SERIAL_2, None, None, _recorded(key, _SERIAL_2)),
    }
    # One config per segment: imec0 changed bank at the restart.
    assert len(set(_recorded(key, _SERIAL_1).values())) == 2


def test_a_corrected_report_relinks_on_the_next_pass(daemon_module, prefix, tmp_path_factory):
    """wl.works swapped the two serials and changed the first insertion's
    trajectory and aim; the second loses its aim. The link moves with the
    serial, and what changed is updated in place."""
    key = _landed(tmp_path_factory, "plink3", "2025-06-17_01", **_TWO_PROBES)
    _report(key, "plink3-k1", [{"serial": _SERIAL_1, "insertion_number": 1, "target": _AIM},
                               {"serial": _SERIAL_2, "insertion_number": 2, "target": _AIM}], prefix)
    daemon_module.run_once(prefix=prefix)
    _report(key, "plink3-k2", [
        {"serial": _SERIAL_2, "insertion_number": 1, "trajectory_id": "T-9",
         "target": {"area": "V4v", "atlas": "CHARM", "atlas_level": 6}},
        {"serial": _SERIAL_1, "insertion_number": 2}], prefix)
    daemon_module.run_once(prefix=prefix)

    assert _links(key) == {
        1: (_SERIAL_2, "T-9", ("V4v", "CHARM", 6), _recorded(key, _SERIAL_2)),
        2: (_SERIAL_1, None, None, _recorded(key, _SERIAL_1)),
    }


def test_a_serial_reported_for_two_insertions_links_neither(daemon_module, prefix, tmp_path_factory):
    """A moved probe is a second insertion with the same serial, and the
    request does not say which segments each covers. Neither is guessed at,
    and a link an earlier report made is taken back."""
    key = _landed(tmp_path_factory, "plink4", "2025-06-18_01", **_TWO_PROBES)
    _report(key, "plink4-k1", [{"serial": _SERIAL_1, "insertion_number": 1}], prefix)
    daemon_module.run_once(prefix=prefix)
    assert set(_links(key)) == {1}

    _report(key, "plink4-k2", [{"serial": _SERIAL_1, "insertion_number": 1},
                               {"serial": _SERIAL_1, "insertion_number": 3}], prefix)
    report = daemon_module.run_once(prefix=prefix)
    assert _links(key) == {}
    assert not [error for error in report["errors"] if "plink4" in error]


def test_a_serial_the_recording_never_named_links_nothing(daemon_module, prefix, tmp_path_factory):
    """A typo, or a swapped probe: `ProbeInsertion` needs a recorded
    `Probe`, and there is none to point at."""
    key = _landed(tmp_path_factory, "plink5", "2025-06-19_01", probe_serial="19011110006")
    _report(key, "plink5-k1", [{"serial": "19011119999", "insertion_number": 1}], prefix)
    report = daemon_module.run_once(prefix=prefix)
    assert _links(key) == {}
    # Nothing linked because nothing should be, not because linking failed.
    assert not [error for error in report["errors"] if "plink5" in error]


def test_a_probe_without_geometry_is_not_linked(daemon_module, prefix, tmp_path_factory):
    """An unknown part number has no `Probe` row and no config (Phase 2a's
    invariant), so its report stays unlinked, and the file says why."""
    from tests.schema.test_probe_census import _unknown_type

    _recipe, key = _session(tmp_path_factory, _unknown_type, subject="plink7", session_id="2025-06-21_01",
                            probe_serial="19011110004", runs=True)
    _report(_measured(key), "plink7-k1", [{"serial": "19011110004", "insertion_number": 1}], prefix)
    daemon_module.run_once(prefix=prefix)
    assert _links(key) == {}


def test_an_insertion_no_report_made_is_left_alone(daemon_module, prefix, tmp_path_factory):
    """The linker takes back only links it could have made. Reports are never
    deleted, so every link it made has one; an insertion with none was made
    some other way -- by hand, or a synthetic one a test's sort points at --
    and is not its to remove."""
    from wl_preproc.schema import ephys

    key = _landed(tmp_path_factory, "plink8", "2025-06-08_01", probe_serial="19011110018")
    daemon_module.run_once(prefix=prefix)
    ephys.ProbeInsertion.insert1({**key, "insertion_number": 5, "probe_serial": "19011110018"})

    report = daemon_module.run_once(prefix=prefix)

    assert _links(key) == {5: ("19011110018", None, None, {})}
    assert not [error for error in report["errors"] if "plink8" in error]
