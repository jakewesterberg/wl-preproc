"""The `probes` stage, `ephys.ProbeCensus`, through the daemon (design spec
`2026-09-30-nwb-probes-design.md` section 2.1): each SpikeGLX segment's run is
read once, every probe it recorded is a part row, and a known probe fills
Phase 2a's `ProbeType`, `Probe` and `ElectrodeConfig`.

Sessions are hand-landed (`test_spikeglx_restart.py::_session`), as the
NWB builder's own tests land theirs, so a later file can build one with a
past date. Subjects, dates and serials checked unclaimed across `tests/` on
2026-09-30."""

from __future__ import annotations

import pytest

from tests.schema.test_spikeglx_restart import RESTART, _session

_TWO_PROBES = {"subject": "pcens1", "session_id": "2025-06-11_01"}
_UNKNOWN_TYPE = {"subject": "pcens2", "session_id": "2025-06-12_01", "probe_serial": "19011110009"}
_NO_PROBE = {"subject": "pcens3", "session_id": "2025-06-13_01", "probe_serial": "19011110008"}
_CONFLICT = {"subject": "pcens4", "session_id": "2025-06-14_01", "probe_serial": "19011110007",
             "probe_part_number": "NP1032"}
_MIXED = {"subject": "pcens5", "session_id": "2025-06-09_01", "probe_serial": "19011110015",
          "extra_probes": [{"serial": "19011110016"}]}


def _unknown_type(directory):
    (meta,) = directory.glob("*_imec0.ap.meta")
    meta.write_text(meta.read_text().replace("imDatPrb_pn=NP1000", "imDatPrb_pn=NP9999"))


def _unknown_second(directory):
    (meta,) = directory.glob("*_imec1.ap.meta")
    meta.write_text(meta.read_text().replace("imDatPrb_pn=NP1000", "imDatPrb_pn=NP9999"))


def _no_probe(directory):
    for meta in directory.glob("*_imec0.*.meta"):
        meta.unlink()


@pytest.fixture(scope="module")
def landed(dj_conn, prefix, tmp_path_factory):
    """Four sessions, then ONE daemon pass: a census row after one pass is
    what proves the stage runs after `core.Segment`, which it reads.
    `_CONFLICT`'s serial is registered first as another type, by hand, as an
    earlier session would have registered it."""
    from wl_preproc import daemon
    from wl_preproc.schema import ephys

    daemon.activate_all(prefix=prefix)
    ephys.register_probe_type("NP1000")
    ephys.Probe.insert1({"probe_serial": _CONFLICT["probe_serial"], "probe_type": "NP1000"}, skip_duplicates=True)
    sessions = {
        "two": _session(tmp_path_factory, **_TWO_PROBES,
                        extra_probes=[{"serial": "19011110002", "part_number": "NP1032", "bank": 1}],
                        spikeglx_restart={**RESTART, "probe_bank": 1}),
        "unknown": _session(tmp_path_factory, _unknown_type, **_UNKNOWN_TYPE),
        "none": _session(tmp_path_factory, _no_probe, **_NO_PROBE),
        "conflict": _session(tmp_path_factory, **_CONFLICT),
        "mixed": _session(tmp_path_factory, _unknown_second, **_MIXED),
    }
    daemon.run_once(prefix=prefix)
    return sessions


def _parts(key):
    from wl_preproc.schema import ephys

    return (ephys.ProbeCensus.Probe & key).to_dicts(order_by=("segment_barcode", "stream"))


def _electrodes(part):
    from wl_preproc.schema import ephys

    config = {"electrode_config_hash": part["electrode_config_hash"], "probe_type": part["probe_type"]}
    return sorted(int(e) for e in (ephys.ElectrodeConfig.Electrode & config).to_arrays("electrode"))


def test_each_segment_records_every_probe_its_run_recorded(landed):
    from wl_preproc.schema import core, ephys

    _recipe, key = landed["two"]
    segments = (core.Segment & key & {"system": "spikeglx"}).to_dicts(order_by="segment_barcode")
    assert len(segments) == 2  # the restart
    assert [row["n_probes"] for row in (ephys.ProbeCensus & key).to_dicts(order_by="segment_barcode")] == [2, 2]

    parts = _parts(key)
    assert [(p["segment_barcode"], p["stream"], p["probe_serial"], p["part_number"], p["problem"]) for p in parts] == [
        (segments[0]["segment_barcode"], "imec0", "19011110001", "NP1000", ""),
        (segments[0]["segment_barcode"], "imec1", "19011110002", "NP1032", ""),
        (segments[1]["segment_barcode"], "imec0", "19011110001", "NP1000", ""),
        (segments[1]["segment_barcode"], "imec1", "19011110002", "NP1032", ""),
    ]
    # imec0 changed bank at the restart; imec1 did not.
    assert [_electrodes(p) for p in parts] == [[0, 1, 2, 3], [384, 385, 386, 387], [384, 385, 386, 387],
                                                [384, 385, 386, 387]]
    assert parts[0]["electrode_config_hash"] != parts[2]["electrode_config_hash"]
    assert parts[1]["electrode_config_hash"] == parts[3]["electrode_config_hash"]


def test_a_recorded_probe_is_registered_by_serial_with_every_site_of_its_type(landed):
    from wl_preproc.ephys.geometry import electrode_rows
    from wl_preproc.schema import ephys

    assert (ephys.Probe & {"probe_serial": "19011110001"}).fetch1("probe_type") == "NP1000"
    assert (ephys.Probe & {"probe_serial": "19011110002"}).fetch1("probe_type") == "NP1032"
    assert len(ephys.ProbeType.Electrode & {"probe_type": "NP1032"}) == len(electrode_rows("NP1032"))


def test_an_unknown_part_number_is_recorded_without_geometry(landed):
    """Phase 2a's invariant holds: no `ProbeType` without its electrodes, so
    the probe is recorded here, with its part number and the reason, and
    nowhere else."""
    from wl_preproc.schema import ephys

    _recipe, key = landed["unknown"]
    (part,) = _parts(key)
    assert (part["probe_serial"], part["part_number"], part["electrode_config_hash"]) == (
        "19011110009", "NP9999", None)
    assert "NP9999" in part["problem"]
    assert not ephys.ProbeType & {"probe_type": "NP9999"}
    assert not ephys.Probe & {"probe_serial": "19011110009"}


def test_a_run_with_no_probe_is_still_marked_read(landed):
    """The master row is what the NWB builder waits on (section 3.3), so a
    run that recorded no probe must still have one."""
    from wl_preproc.schema import ephys

    _recipe, key = landed["none"]
    assert (ephys.ProbeCensus & key).fetch1("n_probes") == 0
    assert not _parts(key)


def test_a_serial_registered_as_another_type_is_a_problem_not_a_second_identity(landed):
    """A serial is one physical probe, so its type cannot change. The
    registered type stands, and this recording's sites are not recorded
    under a type the serial does not have."""
    from wl_preproc.schema import ephys

    _recipe, key = landed["conflict"]
    (part,) = _parts(key)
    assert (part["part_number"], part["electrode_config_hash"]) == ("NP1032", None)
    assert "registered as NP1000" in part["problem"]
    assert (ephys.Probe & {"probe_serial": "19011110007"}).fetch1("probe_type") == "NP1000"


def test_a_run_mixing_a_placed_and_an_unplaced_probe_records_both(landed):
    """Each part row carries every field, placed or not: a run of one of
    each is what a single-probe fixture cannot reach."""
    _recipe, key = landed["mixed"]
    placed, unplaced = _parts(key)
    assert (placed["stream"], placed["part_number"], placed["problem"]) == ("imec0", "NP1000", "")
    assert _electrodes(placed) == [0, 1, 2, 3]
    assert (unplaced["stream"], unplaced["part_number"], unplaced["electrode_config_hash"]) == (
        "imec1", "NP9999", None)
