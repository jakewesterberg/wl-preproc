"""Probes and areas in the NWB file, through the builder (design spec
`2026-09-30-nwb-probes-design.md` sections 3.1, 3.3 and 5). Subjects, dates
and serials checked unclaimed across `tests/` on 2026-09-30; the dates are in
the PAST, since `nwbinspector` calls a future `session_start_time` critical."""

from __future__ import annotations

import datetime

import pytest

from tests.schema.test_spikeglx_restart import RESTART, _session

_S1, _S2, _S3 = "19011110011", "19011110012", "19011110013"
_AIM = {"area": "V4d", "atlas": "CHARM", "atlas_level": 6}
_ASSIGNED = {"area": "V4v", "source": "at_rig", "asserted_at": "2025-06-22T11:00:00Z"}


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


def _bad_part(directory):
    """imec2's probe becomes a type probeinterface does not know."""
    for meta in directory.glob("*imec2.ap.meta"):
        meta.write_text(meta.read_text().replace("imDatPrb_pn=NP1000", "imDatPrb_pn=NP9999"))


def _canonical(daemon_module, prefix, key, idempotency_key, probes):
    """wl.works' canonical over the session's measured blocks, as
    `tests/schema/test_nwb_build.py`'s fixture asks for it, then a pass."""
    from wl_preproc.contracts.protocol import JobRequest, MetadataBundle
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import pipeline

    blocks = [{"block_id": index, "task_type": (pipeline.trial.Block.Attribute & row & {
                   "attribute_name": "task_type"}).fetch1("attribute_value"),
               "start_s": float(row["block_start_time"]), "end_s": float(row["block_stop_time"]),
               "works_block_id": f"wb-{index}"}
              for index, row in enumerate((pipeline.trial.Block & key).to_dicts(order_by="block_start_time"), 1)]
    activation = accept(JobRequest(
        domain="neural", parameters={}, idempotency_key=idempotency_key,
        selection={"session_datetime": key["session_datetime"].replace(tzinfo=datetime.UTC), "montage_id": 0},
        metadata=MetadataBundle(
            blocks=blocks, montage_boundaries=[{"montage_id": 0, "start_s": 0.0, "end_s": 16.0}], probes=probes,
            experimenter="jw", subject=key["subject"], task_types=[],
            subject_details={"species": "Macaca mulatta", "sex": "F", "date_of_birth": datetime.date(2016, 3, 2)}),
    ), prefix=prefix)
    daemon_module.run_once(prefix=prefix)
    return activation


@pytest.fixture(scope="module")
def three_probes(daemon_module, prefix, tmp_path_factory):
    """Three probes, one per area label: imec0 has an aim and an
    assignment, imec1 an aim only, and imec2 no report and a part number
    probeinterface does not know. A restart that keeps every bank makes two
    segments with one map each."""
    _recipe, key = _session(
        tmp_path_factory, _bad_part, subject="pnwb1", session_id="2025-06-22_01", probe_serial=_S1,
        extra_probes=[{"serial": _S2, "part_number": "NP1032", "bank": 1}, {"serial": _S3}],
        spikeglx_restart=RESTART)
    daemon_module.run_once(prefix=prefix)
    activation = _canonical(daemon_module, prefix, key, "pnwb1-k1", [
        {"serial": _S1, "insertion_number": 1, "trajectory_id": "T-1", "target": _AIM, "area_assignment": _ASSIGNED},
        {"serial": _S2, "insertion_number": 2, "target": _AIM},
    ])
    return key, activation


@pytest.fixture(scope="module")
def built(three_probes, tmp_path_factory):
    from wl_preproc.nwb.build import build

    _key, activation = three_probes
    return build(activation, tmp_path_factory.mktemp("nwb-probes"))


def test_each_probe_is_a_device_named_for_its_serial(built):
    from pynwb import NWBHDF5IO

    assert built.status == "written", (built.reason, built.findings)
    with NWBHDF5IO(str(built.path), "r") as handle:
        nwb = handle.read()
        devices = {name: (device.serial_number, device.model.name) for name, device in nwb.devices.items()}
        assert devices == {f"probe-{_S1}": (_S1, "NP1000"), f"probe-{_S2}": (_S2, "NP1032"),
                           f"probe-{_S3}": (_S3, "NP9999")}
        groups = {name: (group.device.name, group.location) for name, group in nwb.electrode_groups.items()}
        assert groups == {"insertion-1": (f"probe-{_S1}", "V4v"), "insertion-2": (f"probe-{_S2}", "V4d"),
                          f"probe-{_S3}": (f"probe-{_S3}", "unknown")}
        described = nwb.electrode_groups["insertion-1"].description
        assert "target: V4d (CHARM, level 6)" in described and "assigned at rig: V4v (2025-06-22)" in described


def test_every_active_site_is_one_row_carrying_its_probes_areas(built):
    """Once per probe, not once per segment: the restart kept the bank.
    The label is the assignment, else the aim, else `unknown`, and both
    areas stay visible on every row."""
    from pynwb import NWBHDF5IO

    from wl_preproc.ephys.geometry import electrode_rows

    with NWBHDF5IO(str(built.path), "r") as handle:
        electrodes = handle.read().electrodes
        table = electrodes.to_dataframe()
        described = {column: electrodes[column].description for column in ("target_area", "assigned_area")}
    rows = {group: frame for group, frame in table.groupby("group_name")}
    assert set(rows) == {"insertion-1", "insertion-2"}  # imec2 has no geometry
    first, second = rows["insertion-1"], rows["insertion-2"]
    assert first["electrode"].tolist() == [0, 1, 2, 3] and second["electrode"].tolist() == [384, 385, 386, 387]
    sites = {row["electrode"]: row for row in electrode_rows("NP1032")}
    assert second["rel_x"].tolist() == [sites[e]["x_coord"] for e in range(384, 388)]
    assert second["rel_y"].tolist() == [sites[e]["y_coord"] for e in range(384, 388)]
    assert set(zip(first["location"], first["target_area"], first["assigned_area"], first["assigned_area_source"])) == {
        ("V4v", "V4d", "V4v", "at_rig")}
    assert set(zip(second["location"], second["target_area"], second["assigned_area"])) == {("V4d", "V4d", "")}
    # pynwb fixes the table's own description (NWB 2.9's ElectrodesTable),
    # so each area column says it is per insertion.
    assert all("not depth-resolved" in text for text in described.values())


def test_an_intan_probe_is_listed_from_its_report_alone(daemon_module, prefix, tmp_path_factory):
    """Section 5: an RHS header does not name its probe, so wl.works' report
    is the only record of it. It is listed with no type and no electrodes,
    and the file says why. Landed by hand with `syncbox` and `rhs`:
    `_session` declares SpikeGLX."""
    from pynwb import NWBHDF5IO

    from tests.schema.test_eye_populate import _land
    from wl_preproc.nwb.build import build
    from wl_preproc.nwb.gather import gather
    from wl_preproc.synth.recipe import CI_RECIPE
    from wl_preproc.synth.session import generate_session

    recipe = CI_RECIPE.model_copy(update={"subject": "pnwb4", "session_id": "2025-06-25_01",
                                          "systems": ("syncbox", "rhs")})
    root = tmp_path_factory.mktemp("pnwb4")
    generate_session(root, recipe)
    key = _land(root, recipe, datetime.datetime(2025, 6, 25, 9), acquisition_systems=("syncbox", "rhs"))
    daemon_module.run_once(prefix=prefix)
    activation = _canonical(daemon_module, prefix, key, "pnwb4-k1",
                            [{"serial": "R-19", "insertion_number": 1, "target": _AIM}])

    assert gather(activation).probe_notes == [
        "probe R-19 (insertion 1) is listed from wl.works' report alone: no SpikeGLX recording names it, and an "
        "Intan (RHS) header does not name its probe"]
    result = build(activation, tmp_path_factory.mktemp("nwb-intan"))
    assert result.status == "written", (result.reason, result.findings)
    with NWBHDF5IO(str(result.path), "r") as handle:
        nwb = handle.read()
        assert (nwb.devices["probe-R-19"].serial_number, nwb.devices["probe-R-19"].model) == ("R-19", None)
        assert nwb.electrode_groups["insertion-1"].location == "V4d" and nwb.electrodes is None


def _no_serial(directory):
    for meta in directory.glob("*_imec0.ap.meta"):
        meta.write_text("".join(line for line in meta.read_text().splitlines(keepends=True)
                                if not line.startswith("imDatPrb_sn=")))


def test_a_meta_naming_no_serial_is_noted_by_its_segment(daemon_module, prefix, tmp_path_factory):
    """Section 5: the probe cannot be named, so it is not listed; the note
    says which segment's stream it was."""
    from wl_preproc.nwb.gather import gather

    recipe, key = _session(tmp_path_factory, _no_serial, subject="pnwb5", session_id="2025-06-26_01")
    daemon_module.run_once(prefix=prefix)
    activation = _canonical(daemon_module, prefix, key, "pnwb5-k1", [])

    data = gather(activation)
    assert data.probes == []
    assert data.probe_notes == [f"{recipe.session_id}.nidq.bin imec0: the .meta names no serial (imDatPrb_sn); the "
                                "probe it recorded is unknown"]


def test_a_bank_change_inside_a_montage_refuses_the_file(daemon_module, prefix, tmp_path_factory):
    """Section 5's one refusing case: a file across it would later be
    sorted across it. The reason names both segments."""
    from wl_preproc.nwb.build import build
    from wl_preproc.schema import core

    _recipe, key = _session(tmp_path_factory, subject="pnwb2", session_id="2025-06-23_01", probe_serial="19011110014",
                            spikeglx_restart={**RESTART, "probe_bank": 1})
    daemon_module.run_once(prefix=prefix)
    activation = _canonical(daemon_module, prefix, key, "pnwb2-k1", [])

    result = build(activation, tmp_path_factory.mktemp("nwb-refused"))

    assert result.status == "refused"
    assert "19011110014" in result.reason and "two active-site maps" in result.reason
    for path in (core.Segment & key & {"system": "spikeglx"}).to_arrays("file_path"):
        assert path in result.reason


def test_a_file_waits_for_the_census_of_its_session(three_probes):
    """Section 3.3: readiness names the stage it waits on, and a session
    with no SpikeGLX segment has nothing to wait for."""
    from wl_preproc.nwb.gather import readiness
    from wl_preproc.schema import ephys

    key, activation = three_probes
    assert readiness(activation) is None
    last = (ephys.ProbeCensus & key).keys(order_by="segment_barcode")[-1]
    (ephys.ProbeCensus & last).delete(prompt=False)
    try:
        assert readiness(activation).startswith("waiting on ProbeCensus")
    finally:
        ephys.ProbeCensus.populate(last)
    assert readiness(activation) is None
