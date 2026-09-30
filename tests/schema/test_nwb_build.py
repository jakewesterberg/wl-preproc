"""The NWB builder end to end, on a synthetic session through the daemon
(design spec `2026-09-28-nwb-builder-design.md` section 11)."""

from __future__ import annotations

import datetime
import io
import json
from pathlib import Path

import h5py
import numpy as np
import pytest

_SESSION_DATETIME = datetime.datetime(2025, 7, 20, 9, 0)
_SUBJECT = "nwbstep1"
_SPLIT_S = 7.5


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon
    from wl_preproc.schema import detect

    daemon.activate_all(prefix=prefix)
    detect.register_default_paramsets()
    return daemon


def _request(key, idempotency_key, montage, blocks, block_ids=None):
    from wl_preproc.contracts.protocol import JobRequest, MetadataBundle

    selection = {"session_datetime": key["session_datetime"].replace(tzinfo=datetime.UTC),
                 "montage_id": montage["montage_id"]}
    if block_ids is not None:
        selection["block_ids"] = block_ids
    return JobRequest(
        domain="neural", selection=selection, parameters={}, idempotency_key=idempotency_key,
        metadata=MetadataBundle(
            blocks=blocks, montage_boundaries=[montage], probes=[], experimenter="jw", subject=key["subject"],
            task_types=[], subject_details={"species": "Macaca mulatta", "sex": "F",
                                            "date_of_birth": datetime.date(2016, 3, 2)},
        ),
    )


def _derivative(session_key, blocks, prefix, block):
    """The one-block derivative over `block`. `accept` returns the
    activation it already holds for a block set, so every caller gets the
    same one."""
    from wl_preproc.responder.jobs import accept

    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
    return accept(_request(session_key, f"nwbstep1-block-{block['block_id']}", montage, blocks,
                           block_ids=[block["block_id"]]), prefix=prefix)


def _command(key, nwb_root, prefix):
    return ["nwb", "build", "--subject", key["subject"], "--session-datetime", key["session_datetime"].isoformat(),
            "--montage-id", str(key["montage_id"]), "--activation-id", str(key["activation_id"]),
            "--nwb-root", str(nwb_root), "--prefix", prefix]


@pytest.fixture(scope="module")
def activation(daemon_module, prefix, tmp_path_factory):
    """`stepped_session`'s construction (tests/schema/test_detect_populate.py),
    run through the daemon; then wl.works' job request for a canonical
    activation over its measured blocks, as `responder/jobs.py::accept`
    records it. The session's timing is computed before that request
    arrives, as a daemon pass before wl.works asks would leave it. Date,
    subject and seed checked unclaimed across `tests/` on 2026-09-28. In the
    PAST, unlike most fixtures here: `nwbinspector` calls
    a future `session_start_time` critical. Returns `(session_key,
    activation_key, blocks)`."""
    from tests.schema.test_detect_populate import _build_stepped_session
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import pipeline

    session_key, _segment, _onsets = _build_stepped_session(
        tmp_path_factory, dirname="nwbstep", session_id="2025-07-20_01", subject=_SUBJECT,
        session_datetime=_SESSION_DATETIME, seed=720,
    )
    daemon_module.run_once(prefix=prefix)
    # The session has one measured block; wl.works asserts it as two, split at
    # `_SPLIT_S`, which it is entitled to do. So a derivative over the second
    # holds only half the session, and every trimming assertion can fail.
    (measured,) = (pipeline.trial.Block & session_key).to_dicts()
    task_type = (pipeline.trial.Block.Attribute & measured & {"attribute_name": "task_type"}).fetch1("attribute_value")
    blocks = [
        {"block_id": 1, "task_type": task_type, "start_s": float(measured["block_start_time"]), "end_s": _SPLIT_S,
         "works_block_id": "wb-1"},
        {"block_id": 2, "task_type": task_type, "start_s": _SPLIT_S, "end_s": float(measured["block_stop_time"]),
         "works_block_id": "wb-2"},
    ]
    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
    key = accept(_request(session_key, "nwbstep1-canonical", montage, blocks), prefix=prefix)
    # The next pass computes what wl.works' blocks add: their coverage.
    # *Added with the final review's I4: until then every file here was built
    # before it, without block coverage, and nothing noticed; true when
    # written.*
    daemon_module.run_once(prefix=prefix)
    return session_key, key, blocks


@pytest.fixture(scope="module")
def built(activation, tmp_path_factory):
    from wl_preproc.nwb.build import build

    _session_key, key, _blocks = activation
    return build(key, tmp_path_factory.mktemp("nwb"))


def test_the_file_is_written_valid_and_describes_the_activation(built):
    from pynwb import NWBHDF5IO

    assert built.status == "written", built.findings
    assert not [f for f in built.findings if f["importance"] == "CRITICAL"]
    with NWBHDF5IO(str(built.path), "r") as handle:
        nwb = handle.read()
        assert nwb.identifier == "nwbstep1.2025-07-20_01.montage-0.activation-0"
        assert (nwb.subject.species, nwb.subject.sex) == ("Macaca mulatta", "F")
        assert tuple(nwb.experimenter) == ("jw",)
        assert nwb.session_start_time == built.clock["reference_time"]
    # The subject scopes the path as well as the identifier (the final
    # review's C1): {nwb_root}/{subject}/{session_id}/{identifier}.nwb.
    assert (built.path.parent.parent.name, built.path.parent.name) == ("nwbstep1", "2025-07-20_01")


def test_a_synthetic_sessions_clock_falls_back_to_the_manifest(activation, built):
    """Section 4.2's fallback, end to end: the synthetic sync box's barcodes
    are counters from 1,000,000, not seconds since 2020, so they disagree
    with the manifest by years and `started_at` places t = 0."""
    assert built.clock["source"] == "manifest"
    assert built.clock["reference_time"] == _SESSION_DATETIME.replace(tzinfo=datetime.timezone.utc)
    assert abs(built.clock["started_at_difference_s"]) > 60.0


def test_blocks_trials_and_events_carry_the_tables_times(activation, built):
    from pynwb import NWBHDF5IO

    from wl_preproc.schema import pipeline

    session_key, _key, blocks = activation
    with NWBHDF5IO(str(built.path), "r") as handle:
        nwb = handle.read()
        stored = nwb.intervals["blocks"].to_dataframe()
        assert stored["block_id"].tolist() == [1, 2]
        assert stored["works_block_id"].tolist() == ["wb-1", "wb-2"]
        assert stored["stop_time"].tolist() == [_SPLIT_S, blocks[1]["end_s"]]
        trials = nwb.trials.to_dataframe()
        expected = sorted(float(r["trial_start_time"]) for r in (pipeline.trial.Trial & session_key).to_dicts())
        np.testing.assert_allclose(sorted(trials["start_time"]), expected)
        # Every event inside a block, the block's end included (an event is
        # an instant, and BLOCK_END sits exactly on it); SESSION_START and
        # SESSION_END lie outside every block and are not this file's.
        events = nwb.intervals["task_events"].to_dataframe()
        times = [float(r["event_start_time"]) for r in (pipeline.event.Event & session_key).to_dicts()]
        inside = sorted(t for t in times if any(b["start_s"] <= t <= b["end_s"] for b in blocks))
        np.testing.assert_allclose(sorted(events["start_time"]), inside)
        assert "BLOCK_END" in set(events["event_type"])
        assert not {"SESSION_START", "SESSION_END"} & set(events["event_type"])


def test_the_trials_carry_the_rigs_conditions_and_settings(activation, built):
    """Design spec `2026-09-29-nwb-publishing-design.md` sections 2.1 and
    2.2: the synthetic rig record (`synth/peripherals.py::rig_condition`),
    joined by trial number. A setting constant across the session is the
    conditions table's, never a trials column."""
    from pynwb import NWBHDF5IO

    from wl_preproc.synth.peripherals import rig_condition

    with NWBHDF5IO(str(built.path), "r") as handle:
        nwb = handle.read()
        trials = nwb.trials.to_dataframe()
        expected = [rig_condition(int(trial_id)) for trial_id in trials["trial_id"]]
        assert trials["condition"].tolist() == [name for name, _ in expected]
        assert trials["setting_contrast"].tolist() == [params["contrast"] for _, params in expected]
        assert "setting_orientation_deg" not in trials.columns
        conditions = nwb.processing["behavior"]["conditions"].to_dataframe()
        assert sorted(conditions["condition"]) == sorted({name for name, _ in expected})
        assert all(json.loads(settings)["orientation_deg"] == 45.0 for settings in conditions["settings"])


def test_the_description_describes_the_file(activation, built):
    """Design spec `2026-09-29-nwb-publishing-design.md` section 2, on the
    synthetic session: what is in the file, by what actually ran."""
    from wl_preproc.contracts.nwb_description import NwbDescription

    description = built.description
    NwbDescription.model_validate(description)
    assert description["identity"]["identifier"] == built.identifier
    assert (description["identity"]["rig"], description["identity"]["role"]) == ("rig-a", "canonical")
    assert description["subject"]["age_days"] == (_SESSION_DATETIME.date() - datetime.date(2016, 3, 2)).days
    assert description["data_types"]["eye"] == {"gaze": ["left", "right"], "pupil": ["left", "right"]}
    assert len(description["data_types"]["eye_events"]["detectors"]) == 6
    assert [block["block_id"] for block in description["blocks"]] == [1, 2]
    assert all(block["task"]["name"] == "rf_map" for block in description["blocks"])
    names = {condition["name"] for block in description["blocks"] for condition in block["conditions"]}
    assert names and names <= {"contrast-10", "contrast-25", "contrast-50", "contrast-100"}
    assert description["checksums"]["datasets"] == built.checksums
    assert description["notes"] == []


def test_the_eye_is_on_session_time_and_every_detector_is_there(activation, built):
    from pynwb import NWBHDF5IO

    from wl_preproc.eye.detect.registry import DETECTORS

    with NWBHDF5IO(str(built.path), "r") as handle:
        nwb = handle.read()
        gaze = nwb.processing["behavior"]["EyeTracking"]["gaze_left"]
        times = gaze.timestamps[:]
        assert np.all(np.diff(times) > 0)
        assert gaze.data.shape == (len(times), 2)
        events = nwb.processing["eye_events"]
        for name in DETECTORS:
            for trace in ("left", "right", "conjunction"):
                assert f"{name}_{trace}" in events.data_interfaces, (name, trace)
            assert f"{name}_source" in events.data_interfaces
        saccades = events["engbert_kliegl_left"].to_dataframe()
        assert (saccades["label"] == "saccade").sum() >= 2


def test_every_dataset_is_checksummed_and_a_rebuild_matches(activation, built, tmp_path_factory):
    from wl_preproc.nwb.build import build

    _session_key, key, _blocks = activation
    with h5py.File(built.path) as handle:
        names = []
        handle.visititems(lambda name, obj: names.append("/" + name) if isinstance(obj, h5py.Dataset) else None)
    hashed = {row["dataset_path"] for row in built.checksums}
    assert hashed == {n for n in names if not n.startswith(("/specifications", "/file_create_date"))}
    again = build(key, tmp_path_factory.mktemp("nwb-again"))
    assert again.checksums == built.checksums


def test_one_second_of_gaze_reads_a_small_fraction_of_the_file(built):
    """Parent spec section 8.1.2: a window of time is a few chunks, which is
    what an HTTP range request fetches. Read through a file-like object that
    counts the bytes it serves."""

    class Counting(io.RawIOBase):
        def __init__(self, path):
            self.handle = open(path, "rb")
            self.served = 0

        def readable(self):
            return True

        def seekable(self):
            return True

        def seek(self, offset, whence=0):
            return self.handle.seek(offset, whence)

        def tell(self):
            return self.handle.tell()

        def readinto(self, buffer):
            count = self.handle.readinto(buffer)
            self.served += count
            return count

    size = built.path.stat().st_size
    source = Counting(built.path)
    with h5py.File(source, "r") as handle:
        opened = source.served
        data = handle["processing/behavior/EyeTracking/gaze_left/data"]
        data[1000:1500]
    assert source.served - opened < size / 10, (source.served - opened, size)


def test_a_derivative_holds_only_its_own_block(activation, prefix, tmp_path_factory):
    """Section 5: continuous samples and trial starts inside the block,
    events inside it or on its end, and nothing from the other block."""
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb.build import build
    from wl_preproc.responder.jobs import accept

    session_key, _key, blocks = activation
    second = max(blocks, key=lambda b: b["start_s"])
    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
    key = accept(_request(session_key, "nwbstep1-derivative", montage, blocks, block_ids=[second["block_id"]]),
                 prefix=prefix)
    result = build(key, tmp_path_factory.mktemp("nwb-derivative"))
    with NWBHDF5IO(str(result.path), "r") as handle:
        nwb = handle.read()
        assert nwb.intervals["blocks"].to_dataframe()["block_id"].tolist() == [second["block_id"]]
        times = nwb.processing["behavior"]["EyeTracking"]["gaze_left"].timestamps[:]
        assert times.min() >= second["start_s"] and times.max() < second["end_s"]
        assert times.min() - second["start_s"] < 0.01
        trials = nwb.trials.to_dataframe()["start_time"]
        assert len(trials) and ((trials >= second["start_s"]) & (trials < second["end_s"])).all()
        events = nwb.intervals["task_events"].to_dataframe()["start_time"]
        assert len(events) and ((events >= second["start_s"]) & (events <= second["end_s"])).all()
        runs = nwb.processing["eye_events"]["engbert_kliegl_left"].to_dataframe()["start_time"]
        assert ((runs >= second["start_s"]) & (runs < second["end_s"])).all()


def test_a_run_that_crosses_its_blocks_end_keeps_its_true_end(activation, built, prefix, tmp_path_factory):
    """Section 5: a detected run is kept when it starts in a block and is
    never cut, so one that ends after its block keeps its true end. Every
    planted step is in the second block, so for this one build wl.works'
    boundary is moved into the middle of the first planted saccade, and
    restored after."""
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb.build import build
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core

    session_key, _key, blocks = activation
    first, second = sorted(blocks, key=lambda b: b["start_s"])
    with NWBHDF5IO(str(built.path), "r") as handle:
        events = handle.read().processing["eye_events"]
        saccade = events["engbert_kliegl_left"].to_dataframe().sort_values("start_time").iloc[0]
        split = float(saccade["start_time"] + saccade["stop_time"]) / 2
        crossing = {}
        for name, table in events.data_interfaces.items():
            if name == "detector_agreement" or name.endswith("_source"):
                continue
            frame = table.to_dataframe()
            frame = frame[(frame["start_time"] < split) & (frame["stop_time"] > split)]
            if len(frame):
                crossing[name] = list(zip(frame["start_time"], frame["stop_time"], strict=True))
    assert "engbert_kliegl_left" in crossing
    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
    key = accept(_request(session_key, "nwbstep1-first-block", montage, blocks, block_ids=[first["block_id"]]),
                 prefix=prefix)
    core.Block.update1({**session_key, "block_id": first["block_id"], "end_s": split})
    core.Block.update1({**session_key, "block_id": second["block_id"], "start_s": split})
    try:
        result = build(key, tmp_path_factory.mktemp("nwb-first-block"))
    finally:
        core.Block.update1({**session_key, "block_id": first["block_id"], "end_s": first["end_s"]})
        core.Block.update1({**session_key, "block_id": second["block_id"], "start_s": second["start_s"]})
    with NWBHDF5IO(str(result.path), "r") as handle:
        events = handle.read().processing["eye_events"]
        for name, runs in crossing.items():
            kept = events[name].to_dataframe()
            for start, stop in runs:
                assert kept.loc[kept["start_time"] == start, "stop_time"].tolist() == [stop], name


def test_an_activation_with_no_blocks_is_refused(activation, prefix, tmp_path_factory):
    from wl_preproc.nwb.build import build
    from wl_preproc.responder.jobs import accept

    session_key, _key, blocks = activation
    end = max(b["end_s"] for b in blocks) + 1.0
    empty = {"montage_id": 1, "start_s": end, "end_s": end + 10.0}
    key = accept(_request(session_key, "nwbstep1-empty", empty, []), prefix=prefix)
    result = build(key, tmp_path_factory.mktemp("nwb-empty"))
    assert (result.status, result.path) == ("refused", None)
    assert "no blocks" in result.reason


def test_an_eye_without_calibration_is_left_out_and_the_rest_is_built(activation, monkeypatch, tmp_path_factory):
    """Section 10's one partial case: that eye's gaze, validity, repairs and
    calibration row are absent, the file is still built, and its
    description says which eye is missing."""
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb.build import build
    from wl_preproc.schema import eye as eye_schema

    real = eye_schema._map_from_row
    monkeypatch.setattr(eye_schema, "_map_from_row", lambda row: None if row["eye"] == "right" else real(row))
    _session_key, key, _blocks = activation
    result = build(key, tmp_path_factory.mktemp("nwb-one-eye"))
    assert result.status == "written", result.findings
    with NWBHDF5IO(str(result.path), "r") as handle:
        nwb = handle.read()
        behavior = nwb.processing["behavior"]
        assert list(behavior["EyeTracking"].spatial_series) == ["gaze_left"]
        assert "eye_validity_right" not in behavior.data_interfaces
        assert behavior["eye_calibration"].to_dataframe()["eye"].tolist() == ["left"]
        assert "pupil_right" in behavior["PupilTracking"].time_series
        assert "right eye" in nwb.session_description


def test_a_session_without_an_eye_recording_is_built_without_one(activation, monkeypatch, tmp_path_factory):
    """No ohDPI recording at all (`gather._eye` returns None): the file is
    still the activation's blocks, trials, events and timing, with no eye
    modules."""
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb import gather
    from wl_preproc.nwb.build import build

    monkeypatch.setattr(gather, "_eye", lambda *args: None)
    _session_key, key, _blocks = activation
    result = build(key, tmp_path_factory.mktemp("nwb-no-eye"))
    assert result.status == "written", result.findings
    with NWBHDF5IO(str(result.path), "r") as handle:
        nwb = handle.read()
        assert "eye_events" not in nwb.processing
        assert not {"EyeTracking", "PupilTracking"} & set(nwb.processing["behavior"].data_interfaces)
        assert len(nwb.trials) and len(nwb.intervals["task_events"])


def test_the_stage_skips_a_freed_session(activation, tmp_path_factory):
    """A freed session's files are gone from scratch; the stage records
    nothing for it until it is rehydrated, like every other stage."""
    from wl_preproc.nwb.build import run_stage
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, _blocks = activation
    before = len(nwb_schema.NwbFile & session_key)
    recorded, errors = run_stage(tmp_path_factory.mktemp("nwb-freed"), freed=[session_key])
    # Nothing is recorded for this session (other sessions in the suite's
    # shared database may build); each of its unbuilt activations is reported
    # as waiting on a rehydration (the 2b final review's M9), and nothing
    # else is reported.
    assert all("rehydrate" in error for error in errors), errors
    assert len(nwb_schema.NwbFile & session_key) == before


def test_the_command_builds_once_and_rebuilds_only_when_its_row_is_deleted(activation, prefix, tmp_path_factory,
                                                                          capsys):
    """`wlpp nwb build` records what it wrote, refuses to overwrite a
    recorded activation and says how to rebuild; with the row deleted it
    rebuilds over the same path, to the same checksums."""
    from wl_preproc.cli.main import main
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, blocks = activation
    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
    key = accept(_request(session_key, "nwbstep1-command", montage, blocks, block_ids=[blocks[0]["block_id"]]),
                 prefix=prefix)
    argv = ["nwb", "build", "--subject", key["subject"], "--session-datetime", key["session_datetime"].isoformat(),
            "--montage-id", str(key["montage_id"]), "--activation-id", str(key["activation_id"]),
            "--nwb-root", str(tmp_path_factory.mktemp("nwb-command")), "--prefix", prefix]

    assert main(argv) == 0
    row = (nwb_schema.NwbFile & key).fetch1()
    assert f"written: {row['path']}" in capsys.readouterr().out
    checksums = (nwb_schema.NwbFile.Dataset & key).to_dicts(order_by="dataset_path")

    assert main(argv) == 1
    assert "already recorded: written; delete the row to rebuild" in capsys.readouterr().out

    (nwb_schema.NwbFile & key).delete(prompt=False)
    assert main(argv) == 0
    assert (nwb_schema.NwbFile & key).fetch1("path") == row["path"]
    assert (nwb_schema.NwbFile.Dataset & key).to_dicts(order_by="dataset_path") == checksums


def test_the_daemon_stage_records_every_activation(activation, daemon_module, prefix, tmp_path_factory):
    """`run_once(nwb_root=...)`: one `NwbFile` row per activation, with its
    datasets; without a root the stage is skipped and says so."""
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema import request

    assert daemon_module.run_once(prefix=prefix)["nwb"] is None
    report = daemon_module.run_once(prefix=prefix, nwb_root=tmp_path_factory.mktemp("nwb-daemon"))
    assert not [e for e in report["errors"] if "NwbFile" in e], report["errors"]
    session_key, key, _blocks = activation
    rows = {(r["montage_id"], r["activation_id"]): r for r in (nwb_schema.NwbFile & session_key).to_dicts()}
    assert set(rows) == {(r["montage_id"], r["activation_id"]) for r in (request.Activation & session_key).to_dicts()}
    canonical = rows[(key["montage_id"], key["activation_id"])]
    assert canonical["status"] == "written" and canonical["reference_source"] == "manifest"
    assert canonical["description"]["identity"]["identifier"] == canonical["nwb_identifier"]
    assert len(nwb_schema.NwbFile.Dataset & canonical) > 50
    assert rows[(1, 0)]["status"] == "refused"


@pytest.fixture(scope="module")
def slow_share(tmp_path_factory):
    """One slow share for the whole module, as a real deployment has: a file
    published in one test is where the next one looks. Its `nwb/` folder is
    made once, as a person sets a share up."""
    from wl_preproc.nwb.publish import NWB_DIR, Share

    mount = tmp_path_factory.mktemp("nwb-slow")
    (mount / NWB_DIR).mkdir()
    return Share(tier="slow", mount=mount, host="wl-nas", name="hdd")


@pytest.fixture(scope="module")
def fast_share(tmp_path_factory):
    from wl_preproc.nwb.publish import NWB_DIR, Share

    mount = tmp_path_factory.mktemp("nwb-fast")
    (mount / NWB_DIR).mkdir()
    return Share(tier="fast", mount=mount, host="wl-nas", name="nvme")


def _set_active(*keys):
    """What PUT /nwb/active records: the whole set, as JSON carries it."""
    from wl_preproc.schema import nwb as nwb_schema

    nwb_schema.ActiveSet.insert1({
        "received_at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
        "activations": [{**key, "session_datetime": key["session_datetime"].isoformat()} for key in keys],
    })


def _placed_path(share, key):
    from wl_preproc.schema import nwb as nwb_schema

    identifier = (nwb_schema.NwbFile & key).fetch1("nwb_identifier")
    subject, session_id = identifier.split(".")[:2]
    return share.local(share.relative(subject, session_id, identifier))


def _unrecord(key, *shares):
    """Delete an activation's row and any file it published: what an
    operator does before rebuilding it."""
    from wl_preproc.schema import nwb as nwb_schema

    if nwb_schema.NwbFile & key:
        for share in shares:
            path = _placed_path(share, key)
            for leftover in path.parent.glob(path.stem + ".*"):
                leftover.unlink()
        (nwb_schema.NwbFile & key).delete(prompt=False)


def test_the_daemon_publishes_every_written_file_to_the_slow_share(activation, daemon_module, prefix, slow_share,
                                                                    tmp_path_factory):
    """Design spec `2026-09-29-nwb-publishing-design.md` section 4: verified,
    described, recorded as a change and a placement, and the scratch copy
    deleted. Only `written` files are published."""
    from wl_preproc.nwb.publish import current_placement, description_path, mismatches
    from wl_preproc.schema import nwb as nwb_schema

    session_key, key, _blocks = activation
    slow = slow_share
    report = daemon_module.run_once(prefix=prefix, nwb_root=tmp_path_factory.mktemp("nwb-publish-build"),
                                    nwb_slow=slow)
    assert not [e for e in report["errors"] if "NwbPlacement" in e], report["errors"]
    assert report["nwb_published"] >= 1
    row = (nwb_schema.NwbFile & key).fetch1()
    placement = current_placement(key)
    assert (placement["tier"], placement["host"], placement["share"]) == ("slow", "wl-nas", "hdd")
    assert placement["path"] == f"nwb/nwbstep1/2025-07-20_01/{row['nwb_identifier']}.nwb"
    published = slow.local(placement["path"])
    assert mismatches(published, (nwb_schema.NwbFile.Dataset & key).to_dicts()) == []
    assert json.loads(description_path(published).read_text())["identity"]["identifier"] == row["nwb_identifier"]
    assert not Path(row["path"]).exists()
    assert sorted(change["kind"] for change in (nwb_schema.NwbChange & key).to_dicts()) == ["built", "published"]
    for unpublished in (nwb_schema.NwbFile & session_key & "status != 'written'").keys():
        assert current_placement(unpublished) is None


def test_publishing_skips_a_freed_session(activation, prefix, slow_share, tmp_path_factory):
    from wl_preproc.nwb import build as build_module
    from wl_preproc.nwb import publish as publish_module
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, blocks = activation
    key = _derivative(session_key, blocks, prefix, blocks[-1])
    _unrecord(key, slow_share)
    build_module.record(key, build_module.build(key, tmp_path_factory.mktemp("nwb-freed-publish-build")))
    publish_module.run_publish(slow_share, freed=[session_key])
    assert publish_module.current_placement(key) is None


def test_a_publish_that_fails_verification_records_nothing_and_retries(activation, prefix, monkeypatch, slow_share,
                                                                       tmp_path_factory):
    """Section 4: a copy that does not verify is deleted and reported, no
    placement is recorded, and the next pass publishes it."""
    from wl_preproc.nwb import build as build_module
    from wl_preproc.nwb import publish as publish_module
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, blocks = activation
    key = _derivative(session_key, blocks, prefix, blocks[-1])
    _unrecord(key, slow_share)
    build_module.record(key, build_module.build(key, tmp_path_factory.mktemp("nwb-verify-build")))
    slow = slow_share
    real = publish_module.mismatches
    monkeypatch.setattr(publish_module, "mismatches", lambda path, checksums: ["/forced"])
    _published, errors = publish_module.run_publish(slow)
    assert [error for error in errors if "differ after copying" in error]
    assert publish_module.current_placement(key) is None
    target = _placed_path(slow, key)
    assert not list(target.parent.glob(target.name + "*"))
    monkeypatch.setattr(publish_module, "mismatches", real)
    publish_module.run_publish(slow)
    assert publish_module.current_placement(key)["tier"] == "slow"


def test_publishing_never_overwrites_a_file_no_placement_records(activation, prefix, slow_share, tmp_path_factory):
    """Parent spec section 8.3: regeneration never overwrites. A row deleted
    without its published file, then rebuilt, must not replace that file:
    the lab's annotations exist only inside it. Here the published file's
    written-once data differs from the rebuild's, so it is not adopted
    either (the final review's C1 and I2: a matching one is)."""
    from wl_preproc.nwb import build as build_module
    from wl_preproc.nwb import publish as publish_module
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, blocks = activation
    key = _derivative(session_key, blocks, prefix, blocks[0])
    _unrecord(key, slow_share)
    build_module.record(key, build_module.build(key, tmp_path_factory.mktemp("nwb-overwrite-first")))
    publish_module.run_publish(slow_share)
    published = _placed_path(slow_share, key)
    with h5py.File(published, "r+") as handle:
        handle["/intervals/trials/start_time"][0] += 1.0
    before = published.read_bytes()
    (nwb_schema.NwbFile & key).delete(prompt=False)
    build_module.record(key, build_module.build(key, tmp_path_factory.mktemp("nwb-overwrite-second")))
    _published, errors = publish_module.run_publish(slow_share)
    assert [error for error in errors if "not overwritten" in error]
    assert published.read_bytes() == before
    assert publish_module.current_placement(key) is None


def test_the_active_set_moves_a_file_to_the_fast_share_and_back_with_its_annotations(
        activation, daemon_module, prefix, slow_share, fast_share):
    """Design spec `2026-09-29-nwb-publishing-design.md` section 5: one live
    copy, moved by the daemon to whichever share the latest active set
    wants; an annotation appended on the fast share travels back with it."""
    from wl_preproc.nwb.publish import current_placement, description_path, run_placement
    from wl_preproc.schema import nwb as nwb_schema

    _session_key, key, _blocks = activation
    _set_active(key)
    report = daemon_module.run_once(prefix=prefix, nwb_slow=slow_share, nwb_fast=fast_share)
    assert report["nwb_moved"] >= 1
    placement = current_placement(key)
    assert placement["tier"] == "fast"
    on_fast = fast_share.local(placement["path"])
    assert on_fast.exists() and description_path(on_fast).exists()
    assert not slow_share.local(placement["path"]).exists()
    with h5py.File(on_fast, "a") as handle:
        handle.create_dataset("/lab_annotation", data=[1, 2, 3])
    _set_active()
    run_placement(slow_share, fast_share)
    back = current_placement(key)
    assert back["tier"] == "slow" and not on_fast.exists()
    with h5py.File(slow_share.local(back["path"]), "r") as handle:
        assert handle["/lab_annotation"][:].tolist() == [1, 2, 3]
    kinds = [change["kind"] for change in sorted((nwb_schema.NwbChange & key).to_dicts(), key=lambda c: c["change_seq"])]
    assert kinds[-2:] == ["moved", "moved"]


def test_changed_written_once_data_stops_a_move(activation, slow_share, fast_share):
    """Section 5: something changed data that must never change; the file
    stays where it is, and the report names the dataset."""
    from wl_preproc.nwb.publish import current_placement, run_placement

    _session_key, key, _blocks = activation
    path = slow_share.local(current_placement(key)["path"])
    with h5py.File(path, "r+") as handle:
        original = handle["/intervals/trials/start_time"][0]
        handle["/intervals/trials/start_time"][0] = original + 1.0
    try:
        _set_active(key)
        _moved, errors = run_placement(slow_share, fast_share)
        assert [error for error in errors if "changed on the NAS" in error and "start_time" in error]
        assert current_placement(key)["tier"] == "slow" and path.exists()
    finally:
        with h5py.File(path, "r+") as handle:
            handle["/intervals/trials/start_time"][0] = original
        _set_active()


def test_the_fast_share_headroom_stops_a_move(activation, slow_share, fast_share):
    import dataclasses

    from wl_preproc.nwb.publish import current_placement, run_placement

    _session_key, key, _blocks = activation
    _set_active(key)
    try:
        _moved, errors = run_placement(slow_share, dataclasses.replace(fast_share, headroom_bytes=10**18))
        assert [error for error in errors if "headroom" in error]
        assert current_placement(key)["tier"] == "slow"
    finally:
        _set_active()


def test_an_old_copy_that_could_not_be_deleted_is_removed_on_the_next_pass(activation, slow_share, fast_share,
                                                                           monkeypatch):
    """Review Focus 1 (the plan): a reader holds the old copy, or the share
    refuses the delete. The move stands; the next pass finishes it, so one
    live copy remains and a later move back is not stuck on a conflict."""
    from wl_preproc.nwb import publish as publish_module

    _session_key, key, _blocks = activation
    old = slow_share.local(publish_module.current_placement(key)["path"])
    real = publish_module._remove_old_copy

    def in_use(path):
        raise PermissionError(f"{path} is in use")

    monkeypatch.setattr(publish_module, "_remove_old_copy", in_use)
    _set_active(key)
    try:
        moved, errors = publish_module.run_placement(slow_share, fast_share)
        assert moved == 1 and [error for error in errors if "is in use" in error and "left for the next pass" in error]
        assert publish_module.current_placement(key)["tier"] == "fast" and old.exists()
        monkeypatch.setattr(publish_module, "_remove_old_copy", real)
        publish_module.run_placement(slow_share, fast_share)
        assert not old.exists()
    finally:
        monkeypatch.setattr(publish_module, "_remove_old_copy", real)
        _set_active()
        publish_module.run_placement(slow_share, fast_share)
    assert publish_module.current_placement(key)["tier"] == "slow"


def test_an_active_set_naming_a_refused_file_changes_nothing(activation, slow_share, fast_share):
    """Review Focus 4 (the plan): wl.works may name any activation. A refused
    one is accepted, never published or moved, and costs no error a pass."""
    from wl_preproc.nwb.publish import current_placement, run_placement, run_publish
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, _blocks = activation
    refused = (nwb_schema.NwbFile & session_key & {"status": "refused"}).keys()
    assert refused
    _set_active(*refused)
    try:
        _published, publish_errors = run_publish(slow_share, fast_share)
        _moved, placement_errors = run_placement(slow_share, fast_share)
        assert not [error for error in publish_errors + placement_errors if "'montage_id': 1," in error]
        assert all(current_placement(key) is None for key in refused)
    finally:
        _set_active()


def test_a_published_file_deleted_by_hand_is_reported_not_moved(activation, slow_share, fast_share):
    """Review Focus 5 (the plan): the file is gone from its share. The
    placement stays as recorded and every pass says so, by path."""
    from wl_preproc.nwb.publish import current_placement, run_placement

    _session_key, key, _blocks = activation
    path = slow_share.local(current_placement(key)["path"])
    kept = path.read_bytes()
    path.unlink()
    _set_active(key)
    try:
        _moved, errors = run_placement(slow_share, fast_share)
        assert [error for error in errors if "missing from its share" in error and str(path) in error]
        assert current_placement(key)["tier"] == "slow"
    finally:
        path.write_bytes(kept)
        _set_active()


def test_an_active_activation_publishes_straight_to_the_fast_share(activation, prefix, slow_share, fast_share,
                                                                   tmp_path_factory):
    """Section 5: a dataset can be marked active before its files exist."""
    from wl_preproc.nwb import build as build_module
    from wl_preproc.nwb import publish as publish_module

    session_key, _key, blocks = activation
    key = _derivative(session_key, blocks, prefix, blocks[-1])
    _unrecord(key, slow_share, fast_share)
    build_module.record(key, build_module.build(key, tmp_path_factory.mktemp("nwb-straight-to-fast")))
    _set_active(key)
    try:
        publish_module.run_publish(slow_share, fast_share)
        assert publish_module.current_placement(key)["tier"] == "fast"
    finally:
        _set_active()


def test_the_listing_and_the_active_set_through_the_responders_functions(activation, prefix, slow_share,
                                                                         fast_share):
    """Design spec `2026-09-29-nwb-publishing-design.md` section 6, against
    the database: what GET /nwb lists, what its cursor holds back, and what
    PUT /nwb/active records, unknown activations named."""
    from wl_preproc.contracts.protocol import ActiveSetRequest
    from wl_preproc.nwb.publish import activation_tuple, active_keys, run_placement
    from wl_preproc.responder.nwb import list_files, set_active
    from wl_preproc.schema import nwb as nwb_schema

    _session_key, key, _blocks = activation
    identifier = (nwb_schema.NwbFile & key).fetch1("nwb_identifier")
    everything = list_files(None, prefix=prefix)
    (entry,) = [item for item in everything["files"] if item["identifier"] == identifier]
    assert entry["status"] == "written" and entry["placement"]["tier"] == "slow"
    assert entry["description"]["identity"]["identifier"] == identifier
    cursor = everything["cursor"]
    assert list_files(cursor, prefix=prefix)["files"] == []
    answer = set_active(ActiveSetRequest.model_validate(
        {"activations": [key, {**key, "activation_id": 99}], "requested_by": "jw"}), prefix=prefix)
    assert answer["accepted"] == 2 and [item["activation_id"] for item in answer["unknown"]] == [99]
    assert activation_tuple(key) in active_keys()
    try:
        run_placement(slow_share, fast_share)
        changed = list_files(cursor, prefix=prefix)
        assert [item["placement"]["tier"] for item in changed["files"] if item["identifier"] == identifier] == ["fast"]
        assert changed["cursor"] > cursor
    finally:
        _set_active()
        run_placement(slow_share, fast_share)

def _annotate(path, name="/lab_annotation"):
    with h5py.File(path, "a") as handle:
        handle.create_dataset(name, data=[1, 2, 3])


def _annotation(path, name="/lab_annotation"):
    with h5py.File(path, "r") as handle:
        return handle[name][:].tolist() if name in handle else None


def _fresh(key, prefix, slow_share, fast_share, tmp_path_factory, label):
    """An activation with no row and no published file, then built and
    recorded: a clean start whatever earlier tests left."""
    from wl_preproc.nwb import build as build_module

    _unrecord(key, slow_share, fast_share)
    build_module.record(key, build_module.build(key, tmp_path_factory.mktemp(label)))


@pytest.mark.parametrize("old_tier", ["fast", "slow"])
def test_a_rebuilt_row_takes_over_its_annotated_published_file_on_either_share(
        activation, prefix, slow_share, fast_share, tmp_path_factory, old_tier):
    """The final review's C1. `wlpp nwb build` says to delete a row to
    rebuild it. The rebuild must not publish beside the old, annotated copy
    on the other share, and placement must not then delete that copy as a
    leftover: the matching copy is adopted where it is, and moved with its
    annotations if the active set wants it elsewhere."""
    from wl_preproc.nwb import build as build_module
    from wl_preproc.nwb import publish as publish_module
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, blocks = activation
    key = _derivative(session_key, blocks, prefix, blocks[0])
    _fresh(key, prefix, slow_share, fast_share, tmp_path_factory, f"nwb-takeover-{old_tier}")
    old_share, new_tier = (fast_share, "slow") if old_tier == "fast" else (slow_share, "fast")
    try:
        _set_active(*([key] if old_tier == "fast" else []))
        publish_module.run_publish(slow_share, fast_share)
        assert publish_module.current_placement(key)["tier"] == old_tier
        _annotate(_placed_path(old_share, key))
        (nwb_schema.NwbFile & key).delete(prompt=False)
        scratch = build_module.build(key, tmp_path_factory.mktemp(f"nwb-takeover-rebuild-{old_tier}"))
        build_module.record(key, scratch)
        _set_active(*([key] if new_tier == "fast" else []))
        _published, publish_errors = publish_module.run_publish(slow_share, fast_share)
        _moved, placement_errors = publish_module.run_placement(slow_share, fast_share)
        mine = f"'activation_id': {key['activation_id']}}}"
        assert not [e for e in publish_errors + placement_errors if mine in e], publish_errors + placement_errors
        placement = publish_module.current_placement(key)
        assert placement["tier"] == new_tier
        new_share = slow_share if new_tier == "slow" else fast_share
        assert _annotation(new_share.local(placement["path"])) == [1, 2, 3]
        assert not old_share.local(placement["path"]).exists()
        assert not Path(scratch.path).exists()
    finally:
        _set_active()


def test_an_unrecorded_copy_with_other_data_on_the_other_share_is_refused(activation, prefix, slow_share,
                                                                           fast_share, tmp_path_factory):
    """C1's other half: a copy on the share publishing did not choose, whose
    written-once data differs, is neither adopted nor published beside."""
    from wl_preproc.nwb import build as build_module
    from wl_preproc.nwb import publish as publish_module
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, blocks = activation
    key = _derivative(session_key, blocks, prefix, blocks[0])
    _fresh(key, prefix, slow_share, fast_share, tmp_path_factory, "nwb-other-data")
    _set_active(key)
    try:
        publish_module.run_publish(slow_share, fast_share)
        on_fast = _placed_path(fast_share, key)
        with h5py.File(on_fast, "r+") as handle:
            handle["/intervals/trials/start_time"][0] += 1.0
        before = on_fast.read_bytes()
        (nwb_schema.NwbFile & key).delete(prompt=False)
        build_module.record(key, build_module.build(key, tmp_path_factory.mktemp("nwb-other-data-rebuild")))
        _set_active()
        _published, errors = publish_module.run_publish(slow_share, fast_share)
        assert [e for e in errors if "not overwritten" in e and str(on_fast) in e], errors
        assert publish_module.current_placement(key) is None
        assert on_fast.read_bytes() == before and not _placed_path(slow_share, key).exists()
    finally:
        _set_active()
        _unrecord(key, slow_share, fast_share)


def test_the_sweep_deletes_only_a_leftover_its_history_records_beside_a_present_copy(
        activation, prefix, slow_share, fast_share, tmp_path_factory):
    """C1's sweep half. A copy on the other share that no earlier placement
    of this activation put there is reported and kept; so is a recorded
    leftover while the current copy is missing, since it may be the only
    one."""
    import shutil

    from wl_preproc.nwb import publish as publish_module

    session_key, key, blocks = activation
    stranger_key = _derivative(session_key, blocks, prefix, blocks[-1])
    _fresh(stranger_key, prefix, slow_share, fast_share, tmp_path_factory, "nwb-stranger")
    publish_module.run_publish(slow_share, fast_share)
    stranger = _placed_path(fast_share, stranger_key)
    stranger.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_placed_path(slow_share, stranger_key), stranger)

    current = _placed_path(slow_share, key)
    assert publish_module.current_placement(key)["tier"] == "slow"
    recorded_leftover = _placed_path(fast_share, key)
    shutil.copyfile(current, recorded_leftover)
    aside = current.with_name(current.name + ".aside")
    current.rename(aside)
    try:
        _moved, errors = publish_module.run_placement(slow_share, fast_share)
        assert stranger.exists() and [e for e in errors if str(stranger) in e], errors
        assert recorded_leftover.exists() and [e for e in errors if str(recorded_leftover) in e], errors
    finally:
        aside.rename(current)
        stranger.unlink(missing_ok=True)
        recorded_leftover.unlink(missing_ok=True)


def test_placement_moves_a_freed_sessions_file(activation, daemon_module, prefix, slow_share, fast_share,
                                               monkeypatch):
    """C2. Placement touches only the NAS and the database, never scratch,
    and Task 8 makes a published session freeable: the freed skip that
    stages reading scratch keep must not leave the active set on slow."""
    from wl_preproc.archive import scratch
    from wl_preproc.nwb.publish import current_placement, run_placement

    session_key, key, _blocks = activation
    monkeypatch.setattr(scratch, "currently_freed", lambda *, prefix=None: [dict(session_key)])
    _set_active(key)
    try:
        daemon_module.run_once(prefix=prefix, nwb_slow=slow_share, nwb_fast=fast_share)
        assert current_placement(key)["tier"] == "fast"
    finally:
        _set_active()
        run_placement(slow_share, fast_share)
    assert current_placement(key)["tier"] == "slow"


def test_a_published_file_missing_where_it_belongs_is_reported_each_pass(activation, slow_share):
    """I1. No move is wanted and only the slow share is configured; the
    file is gone from it. Publishing's pass says so, by path."""
    from wl_preproc.nwb.publish import current_placement, run_publish

    _session_key, key, _blocks = activation
    path = slow_share.local(current_placement(key)["path"])
    aside = path.with_name(path.name + ".aside")
    path.rename(aside)
    try:
        _published, errors = run_publish(slow_share)
        assert [e for e in errors if "missing from its share" in e and str(path) in e], errors
    finally:
        aside.rename(path)


def test_a_file_left_unrecorded_after_its_rename_is_adopted_next_pass(activation, prefix, monkeypatch,
                                                                      slow_share, fast_share, tmp_path_factory):
    """I2. The description write fails after the file took its final name:
    no placement is recorded. The next pass adopts the verified file rather
    than refusing it for ever."""
    from wl_preproc.nwb import publish as publish_module
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, blocks = activation
    key = _derivative(session_key, blocks, prefix, blocks[-1])
    _fresh(key, prefix, slow_share, fast_share, tmp_path_factory, "nwb-adopt")
    real = publish_module.write_description

    def share_fills(nwb_path, description):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(publish_module, "write_description", share_fills)
    _published, errors = publish_module.run_publish(slow_share)
    assert [e for e in errors if "Input/output error" in e]
    assert publish_module.current_placement(key) is None and _placed_path(slow_share, key).exists()
    monkeypatch.setattr(publish_module, "write_description", real)
    _published, errors = publish_module.run_publish(slow_share)
    assert not [e for e in errors if "not overwritten" in e], errors
    assert publish_module.current_placement(key)["tier"] == "slow"
    assert publish_module.description_path(_placed_path(slow_share, key)).exists()
    assert not Path((nwb_schema.NwbFile & key).fetch1("path")).exists()


@pytest.mark.parametrize("state", ["empty mount point", "no mount point"])
def test_a_share_that_is_not_mounted_is_not_published_to(activation, prefix, slow_share, fast_share,
                                                         tmp_path_factory, state):
    """I3. An unmounted share's mount point is an empty directory, or none.
    Publishing onto it would fill the host's own disk and record a place
    the NAS does not have; a share is used only when its `nwb/` folder is
    there, and wlpp never makes that folder."""
    from wl_preproc.nwb import publish as publish_module
    from wl_preproc.nwb.publish import NWB_DIR, Share
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, blocks = activation
    key = _derivative(session_key, blocks, prefix, blocks[-1])
    _fresh(key, prefix, slow_share, fast_share, tmp_path_factory, f"nwb-unmounted-{state.replace(' ', '-')}")
    mount = tmp_path_factory.mktemp("nwb-unmounted")
    if state == "no mount point":
        mount = mount / "gone"
    unmounted = Share(tier="slow", mount=mount, host="wl-nas", name="hdd")
    published, errors = publish_module.run_publish(unmounted)
    assert published == 0 and [e for e in errors if "not reachable" in e and str(mount) in e], errors
    assert not (mount / NWB_DIR).exists()
    assert publish_module.current_placement(key) is None
    assert Path((nwb_schema.NwbFile & key).fetch1("path")).exists()


def test_an_unreachable_fast_share_fails_its_moves_not_the_pass(activation, daemon_module, prefix, slow_share,
                                                                tmp_path_factory):
    """I4. The fast share is down. Its moves fail with a reason, publishing
    still reaches the slow share, and the daemon pass goes on."""
    from wl_preproc.nwb.publish import Share, current_placement

    _session_key, key, _blocks = activation
    gone = Share(tier="fast", mount=tmp_path_factory.mktemp("nwb-fast-down") / "gone", host="wl-nas", name="nvme")
    _set_active(key)
    try:
        report = daemon_module.run_once(prefix=prefix, nwb_slow=slow_share, nwb_fast=gone)
        assert [e for e in report["errors"] if "not reachable" in e and str(gone.mount) in e], report["errors"]
        assert current_placement(key)["tier"] == "slow"
    finally:
        _set_active()


def test_a_failing_nwb_stage_does_not_stop_the_pass(activation, daemon_module, prefix, slow_share, fast_share,
                                                    monkeypatch):
    """I4: `run_once`'s own rule, "one stage failing must not stop the
    others", for the two NWB stages."""
    from wl_preproc.nwb import publish as publish_module

    def broken(*args, **kwargs):
        raise RuntimeError("the NAS went away")

    monkeypatch.setattr(publish_module, "run_publish", broken)
    monkeypatch.setattr(publish_module, "run_placement", broken)
    report = daemon_module.run_once(prefix=prefix, nwb_slow=slow_share, nwb_fast=fast_share)
    assert len([e for e in report["errors"] if "the NAS went away" in e]) == 2, report["errors"]


def test_a_file_changed_while_it_is_moved_is_not_moved(activation, monkeypatch, slow_share, fast_share):
    """I5. A colleague appends to the file while it is copied. The copy
    would lack the annotation and the original would then be deleted: the
    move is abandoned instead, and retried when the file is still."""
    from wl_preproc.nwb import publish as publish_module

    _session_key, key, _blocks = activation
    source = slow_share.local(publish_module.current_placement(key)["path"])
    real = publish_module.copy_verified

    def copy_while_annotated(src, target, checksums):
        real(src, target, checksums)
        _annotate(src, "/lab_note_during_move")

    monkeypatch.setattr(publish_module, "copy_verified", copy_while_annotated)
    _set_active(key)
    try:
        _moved, errors = publish_module.run_placement(slow_share, fast_share)
        assert [e for e in errors if "changed while it was being moved" in e], errors
        assert publish_module.current_placement(key)["tier"] == "slow"
        assert _annotation(source, "/lab_note_during_move") == [1, 2, 3]
        assert not _placed_path(fast_share, key).exists()
    finally:
        _set_active()


def test_a_leftover_annotated_after_its_move_is_left_for_a_person(activation, monkeypatch, slow_share,
                                                                  fast_share):
    """I5's sweep half: the old copy could not be deleted because someone
    had it open, and they wrote to it. The next pass must not delete it."""
    from wl_preproc.nwb import publish as publish_module

    _session_key, key, _blocks = activation
    old = slow_share.local(publish_module.current_placement(key)["path"])
    real = publish_module._remove_old_copy

    def in_use(path):
        raise PermissionError(f"{path} is in use")

    monkeypatch.setattr(publish_module, "_remove_old_copy", in_use)
    _set_active(key)
    try:
        publish_module.run_placement(slow_share, fast_share)
        assert publish_module.current_placement(key)["tier"] == "fast" and old.exists()
        _annotate(old, "/lab_note_after_move")
        monkeypatch.setattr(publish_module, "_remove_old_copy", real)
        _moved, errors = publish_module.run_placement(slow_share, fast_share)
        assert old.exists() and [e for e in errors if str(old) in e], errors
    finally:
        monkeypatch.setattr(publish_module, "_remove_old_copy", real)
        old.unlink(missing_ok=True)
        publish_module.description_path(old).unlink(missing_ok=True)
        _set_active()
        publish_module.run_placement(slow_share, fast_share)
    assert publish_module.current_placement(key)["tier"] == "slow"



def test_a_second_wlpp_process_leaves_the_nwb_stages_alone(activation, daemon_module, prefix, slow_share,
                                                            fast_share, tmp_path_factory, capsys):
    """The final review's M4. A daemon pass that outlives its cron interval,
    or `wlpp nwb build` beside a pass, would publish, move and record the
    same files at once: one process holds a database lock for the NWB
    stages, and the other skips them and says why."""
    import datajoint as dj
    import pymysql

    from wl_preproc.cli.main import main
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, blocks = activation
    key = _derivative(session_key, blocks, prefix, blocks[-1])
    _unrecord(key, slow_share, fast_share)
    other = pymysql.connect(host=dj.config["database.host"], port=int(dj.config["database.port"]),
                            user=dj.config["database.user"], password=dj.config["database.password"])
    try:
        with other.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 0)", (f"wlpp_nwb_{prefix}",))
            assert cursor.fetchone()[0] == 1
        report = daemon_module.run_once(prefix=prefix, nwb_root=tmp_path_factory.mktemp("nwb-locked"),
                                        nwb_slow=slow_share, nwb_fast=fast_share)
        assert [e for e in report["errors"] if "another wlpp process" in e], report["errors"]
        assert (report["nwb"], report["nwb_published"], report["nwb_moved"]) == (0, 0, 0)
        assert not nwb_schema.NwbFile & key
        assert main(_command(key, tmp_path_factory.mktemp("nwb-locked-command"), prefix)) == 1
        assert "another wlpp process" in capsys.readouterr().out
    finally:
        other.close()
    report = daemon_module.run_once(prefix=prefix, nwb_root=tmp_path_factory.mktemp("nwb-unlocked"),
                                    nwb_slow=slow_share, nwb_fast=fast_share)
    assert not [e for e in report["errors"] if "another wlpp process" in e]
    assert nwb_schema.NwbFile & key


def test_a_superseded_activation_that_was_never_built_is_not_built(activation, prefix, tmp_path_factory):
    """Design spec `2026-09-30-canonical-lifecycle-design.md` section 4: a
    replacement that arrives before its predecessor was built leaves the
    predecessor unbuilt."""
    from wl_preproc.nwb.build import run_stage
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema import request

    session_key, _key, _blocks = activation
    now = datetime.datetime(2027, 6, 1, 12, 0)
    old = {**session_key, "montage_id": 1, "activation_id": 50}
    new = {**session_key, "montage_id": 1, "activation_id": 51}
    request.Request.insert1({"idempotency_key": "nwbstep1-superseded", "task_type": "neural",
                             "origin": "wl_works", "payload": {}, "requested_at": now})
    request.Activation.insert1({**old, "role": "canonical", "request_key": "nwbstep1-superseded", "created_at": now})
    request.Activation.insert1({**new, "role": "canonical", "request_key": "nwbstep1-superseded", "created_at": now,
                                "supersedes": 50})
    try:
        run_stage(tmp_path_factory.mktemp("nwb-superseded"))
        assert not nwb_schema.NwbFile & old
        assert nwb_schema.NwbFile & new
    finally:
        (nwb_schema.NwbFile & [old, new]).delete(prompt=False)
        (request.Activation & [new, old]).delete(prompt=False)
        (request.Request & {"idempotency_key": "nwbstep1-superseded"}).delete(prompt=False)


def test_the_listing_marks_a_superseded_file_through_the_cursor(activation, prefix, tmp_path_factory):
    """Design spec `2026-09-30-canonical-lifecycle-design.md` section 4: the
    build stage records a `superseded` change for an activation with a row
    once a replacement names it, exactly once, and `GET /nwb` with a cursor
    then lists it with `superseded_by`."""
    from wl_preproc.nwb.build import run_stage
    from wl_preproc.responder.nwb import list_files
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema import request

    session_key, _key, _blocks = activation
    now = datetime.datetime(2027, 6, 1, 12, 0)
    old = {**session_key, "montage_id": 1, "activation_id": 60}
    new = {**session_key, "montage_id": 1, "activation_id": 61}
    request.Request.insert1({"idempotency_key": "nwbstep1-listing-superseded", "task_type": "neural",
                             "origin": "wl_works", "payload": {}, "requested_at": now})
    request.Activation.insert1({**old, "role": "canonical", "request_key": "nwbstep1-listing-superseded",
                                "created_at": now})
    nwb_schema.NwbFile.insert1({**old, "status": "refused", "built_at": now, "reason": "no blocks"})
    cursor = list_files(None, prefix=prefix)["cursor"]
    request.Activation.insert1({**new, "role": "canonical", "request_key": "nwbstep1-listing-superseded",
                                "created_at": now, "supersedes": 60})
    try:
        run_stage(tmp_path_factory.mktemp("nwb-listing-superseded"))
        run_stage(tmp_path_factory.mktemp("nwb-listing-superseded-again"))
        assert len(nwb_schema.NwbChange & old & {"kind": "superseded"}) == 1
        listed = {item["activation"]["activation_id"]: item for item in list_files(cursor, prefix=prefix)["files"]
                  if item["activation"]["montage_id"] == 1}
        assert listed[60]["superseded_by"] == 61
        assert listed[61]["superseded_by"] is None
    finally:
        (nwb_schema.NwbFile & [old, new]).delete(prompt=False)
        (request.Activation & [new, old]).delete(prompt=False)
        (request.Request & {"idempotency_key": "nwbstep1-listing-superseded"}).delete(prompt=False)


def test_an_invalid_file_is_rebuilt_once_its_missing_subject_details_arrive(activation, prefix, slow_share,
                                                                            fast_share, tmp_path_factory):
    """Design spec `2026-09-30-canonical-lifecycle-design.md` section 5.
    Built without a date of birth, the file is `invalid`; a pass with nothing
    changed leaves it alone; once the date arrives, the next pass rebuilds
    it, and it is `written`. It was never published, so nothing is lost."""
    from wl_preproc.ingest.landing import SUBJECT_BIRTH_DATE_UNKNOWN
    from wl_preproc.nwb.build import run_stage
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema import pipeline

    session_key, _key, blocks = activation
    key = _derivative(session_key, blocks, prefix, blocks[0])
    _unrecord(key, slow_share, fast_share)
    birth = (pipeline.subject.Subject & {"subject": _SUBJECT}).fetch1("subject_birth_date")
    pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": SUBJECT_BIRTH_DATE_UNKNOWN})
    try:
        run_stage(tmp_path_factory.mktemp("nwb-rebuild-first"))
        first = (nwb_schema.NwbFile & key).fetch1()
        assert first["status"] == "invalid"
        run_stage(tmp_path_factory.mktemp("nwb-rebuild-unchanged"))
        assert (nwb_schema.NwbFile & key).fetch1("built_at") == first["built_at"]
        pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": birth})
        run_stage(tmp_path_factory.mktemp("nwb-rebuild-after"))
        rebuilt = (nwb_schema.NwbFile & key).fetch1()
        assert rebuilt["status"] == "written"
        assert rebuilt["description"]["subject"]["date_of_birth"] == birth.isoformat()
        assert not Path(first["path"]).exists()
    finally:
        pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": birth})


def _lifecycle_rows(session_key, request_key, rows):
    """`Activation` rows written directly, each over block 2; tests/ is
    outside the supersedes guardrail's scan."""
    from wl_preproc.schema import request

    now = datetime.datetime(2027, 6, 1, 12, 0)
    request.Request.insert1({"idempotency_key": request_key, "task_type": "neural", "origin": "wl_works",
                             "payload": {}, "requested_at": now})
    for row in rows:
        request.Activation.insert1({"request_key": request_key, "created_at": now, **session_key, **row})
        request.ActivationBlock.insert1({**session_key, "montage_id": row["montage_id"],
                                         "activation_id": row["activation_id"], "block_id": 2})


def _drop_lifecycle_rows(session_key, request_key, keys):
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema import request

    (nwb_schema.NwbFile & keys).delete(prompt=False)
    (request.Activation & keys).delete(prompt=False)
    (request.Request & {"idempotency_key": request_key}).delete(prompt=False)


@pytest.mark.parametrize("why_unbuilt", ["superseded", "freed"])
def test_an_invalid_row_the_stage_will_not_rebuild_survives_its_details_arriving(
        activation, prefix, tmp_path_factory, why_unbuilt):
    """The final review's I1. Discarding an `invalid` row whose details have
    arrived is only right when the same pass rebuilds it. A superseded
    activation (spec section 4: it "stays as it is") and one in a freed
    session are never rebuilt, so their rows, the only record of their
    files, must survive."""
    from wl_preproc.ingest.landing import SUBJECT_BIRTH_DATE_UNKNOWN
    from wl_preproc.nwb.build import build, record, run_stage
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema import pipeline

    session_key, _key, _blocks = activation
    old = {**session_key, "montage_id": 0, "activation_id": 90}
    new = {**session_key, "montage_id": 0, "activation_id": 91}
    rows = [{"montage_id": 0, "activation_id": 90, "role": "canonical"}]
    if why_unbuilt == "superseded":
        rows.append({"montage_id": 0, "activation_id": 91, "role": "canonical", "supersedes": 90})
    _lifecycle_rows(session_key, f"survive-{why_unbuilt}", rows)
    birth = (pipeline.subject.Subject & {"subject": _SUBJECT}).fetch1("subject_birth_date")
    pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": SUBJECT_BIRTH_DATE_UNKNOWN})
    try:
        record(old, build(old, tmp_path_factory.mktemp(f"survive-{why_unbuilt}-a")))
        assert (nwb_schema.NwbFile & old).fetch1("status") == "invalid"
        pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": birth})
        run_stage(tmp_path_factory.mktemp(f"survive-{why_unbuilt}-b"),
                  freed=[dict(session_key)] if why_unbuilt == "freed" else None)
        row = (nwb_schema.NwbFile & old).to_dicts()
        assert row and row[0]["status"] == "invalid" and Path(row[0]["path"]).exists()
        if why_unbuilt == "superseded":
            assert (nwb_schema.NwbFile & new).fetch1("status") == "written"
    finally:
        pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": birth})
        _drop_lifecycle_rows(session_key, f"survive-{why_unbuilt}", [old, new])


def test_a_file_invalid_for_another_reason_is_not_rebuilt_every_pass(activation, prefix, monkeypatch,
                                                                     tmp_path_factory):
    """The final review's I3, Review Focus 4 pinned properly: the subject's
    details are all present, the file is invalid for another reason, and
    each pass must find its recorded subject equal to the current one."""
    import wl_preproc.nwb.build as build_module
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, _blocks = activation
    key = {**session_key, "montage_id": 0, "activation_id": 97}
    _lifecycle_rows(session_key, "loop-invalid", [{"montage_id": 0, "activation_id": 97, "role": "derivative",
                                                   "selection_hash": "loop-invalid"}])
    monkeypatch.setattr(build_module, "n_critical", lambda findings: 1)
    try:
        build_module.record(key, build_module.build(key, tmp_path_factory.mktemp("loop-invalid-a")))
        first = (nwb_schema.NwbFile & key).fetch1()
        assert first["status"] == "invalid" and first["description"]["subject"]["date_of_birth"] is not None
        build_module.run_stage(tmp_path_factory.mktemp("loop-invalid-b"))
        build_module.run_stage(tmp_path_factory.mktemp("loop-invalid-c"))
        assert (nwb_schema.NwbFile & key).fetch1("built_at") == first["built_at"]
    finally:
        _drop_lifecycle_rows(session_key, "loop-invalid", [key])


def test_the_invalid_check_reads_each_subject_once(activation, prefix, monkeypatch, tmp_path_factory):
    """The 2b final review's M5: the check runs every pass over every
    invalid file, so a subject's details are read once per pass, not once
    per file."""
    from wl_preproc.ingest.landing import SUBJECT_BIRTH_DATE_UNKNOWN
    from wl_preproc.nwb import gather
    from wl_preproc.nwb.build import build, record, resolved_invalid
    from wl_preproc.schema import pipeline

    session_key, _key, _blocks = activation
    keys = [{**session_key, "montage_id": 0, "activation_id": i} for i in (80, 81)]
    _lifecycle_rows(session_key, "subject-once", [{"montage_id": 0, "activation_id": i, "role": "derivative",
                                                   "selection_hash": f"subject-once-{i}"} for i in (80, 81)])
    birth = (pipeline.subject.Subject & {"subject": _SUBJECT}).fetch1("subject_birth_date")
    pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": SUBJECT_BIRTH_DATE_UNKNOWN})
    try:
        for key in keys:
            record(key, build(key, tmp_path_factory.mktemp("subject-once")))
        calls, real = [], gather._subject
        monkeypatch.setattr(gather, "_subject", lambda subject: calls.append(subject) or real(subject))
        resolved_invalid()
        assert calls.count(_SUBJECT) == 1
    finally:
        pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": birth})
        _drop_lifecycle_rows(session_key, "subject-once", keys)


def test_the_command_refuses_a_superseded_activation(activation, prefix, tmp_path_factory, capsys):
    """The 2b final review's M8: the stage never builds a superseded
    activation (spec section 4), and the command says why it will not
    either, naming the replacement."""
    from wl_preproc.cli.main import main
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, _blocks = activation
    old = {**session_key, "montage_id": 0, "activation_id": 70}
    new = {**session_key, "montage_id": 0, "activation_id": 71}
    _lifecycle_rows(session_key, "command-superseded", [
        {"montage_id": 0, "activation_id": 70, "role": "canonical"},
        {"montage_id": 0, "activation_id": 71, "role": "canonical", "supersedes": 70}])
    try:
        assert main(_command(old, tmp_path_factory.mktemp("command-superseded"), prefix)) == 1
        assert "superseded by activation 71" in capsys.readouterr().out
        assert not nwb_schema.NwbFile & old
    finally:
        _drop_lifecycle_rows(session_key, "command-superseded", [old, new])


def test_an_activation_waiting_on_a_freed_session_is_reported(activation, prefix, tmp_path_factory):
    """The 2b final review's M9: a replacement or derivative accepted after
    its session was reclaimed is never built until someone rehydrates it,
    and each pass says so."""
    from wl_preproc.nwb.build import run_stage

    session_key, _key, _blocks = activation
    key = {**session_key, "montage_id": 0, "activation_id": 75}
    _lifecycle_rows(session_key, "freed-waiting", [{"montage_id": 0, "activation_id": 75, "role": "derivative",
                                                    "selection_hash": "freed-waiting"}])
    try:
        _recorded, errors = run_stage(tmp_path_factory.mktemp("freed-waiting"), freed=[dict(session_key)])
        assert [e for e in errors if "rehydrate" in e and "'activation_id': 75" in e], errors
    finally:
        _drop_lifecycle_rows(session_key, "freed-waiting", [key])

def test_one_failing_activation_does_not_stop_the_stage(activation, prefix, monkeypatch, tmp_path_factory):
    """The stage catches a failure per activation, as the archive stage
    does: the others are recorded, the failure is reported, and the failed
    activation gets no row, so the next pass tries it again. The two
    one-block derivatives (`accept` returns the activation it already holds
    for a block set) have their rows cleared, and the first one fails."""
    from wl_preproc.nwb import build as build_module
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, blocks = activation
    montage = {"montage_id": 0, "start_s": 0.0, "end_s": max(b["end_s"] for b in blocks) + 1.0}
    failing, fine = (accept(_request(session_key, f"nwbstep1-stage-{block['block_id']}", montage, blocks,
                                     block_ids=[block["block_id"]]), prefix=prefix)
                     for block in sorted(blocks, key=lambda b: b["start_s"]))
    assert failing != fine
    for key in (failing, fine):
        (nwb_schema.NwbFile & key).delete(prompt=False)
    real = build_module.build

    def build(key, nwb_root):
        if {k: key[k] for k in failing} == failing:
            raise OSError("the raw ohDPI file is unreadable")
        return real(key, nwb_root)

    monkeypatch.setattr(build_module, "build", build)
    recorded, errors = build_module.run_stage(tmp_path_factory.mktemp("nwb-stage-errors"))
    assert recorded >= 1
    assert len([e for e in errors if "the raw ohDPI file is unreadable" in e]) == 1, errors
    assert len(nwb_schema.NwbFile & failing) == 0
    assert (nwb_schema.NwbFile & fine).fetch1("status") == "written"


def test_the_command_refuses_a_freed_session(activation, prefix, monkeypatch, tmp_path_factory, capsys):
    """The final review's I5: a freed session's files are gone from scratch,
    and another session may since have landed at its path, so the command
    refuses it, as the stage skips it."""
    from wl_preproc.archive import scratch
    from wl_preproc.cli.main import main
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, blocks = activation
    key = _derivative(session_key, blocks, prefix, blocks[-1])
    (nwb_schema.NwbFile & key).delete(prompt=False)
    monkeypatch.setattr(scratch, "currently_freed", lambda *, prefix=None: [dict(session_key)])
    assert main(_command(key, tmp_path_factory.mktemp("nwb-freed-command"), prefix)) == 1
    assert "freed" in capsys.readouterr().out
    assert len(nwb_schema.NwbFile & key) == 0


def test_a_path_recorded_for_another_activation_is_refused(activation, prefix, monkeypatch, tmp_path_factory):
    """The final review's C1, second line: should two activations ever
    resolve to one file (one subject's two rigs numbering the same day's
    sessions alike), the second is refused rather than replace the first's
    recorded file. Forced here by pointing a derivative at the canonical
    activation's recorded path."""
    from wl_preproc.nwb import build as build_module
    from wl_preproc.schema import nwb as nwb_schema

    session_key, canonical, blocks = activation
    if not nwb_schema.NwbFile & canonical:
        build_module.record(canonical, build_module.build(canonical, tmp_path_factory.mktemp("nwb-canonical")))
    recorded = Path((nwb_schema.NwbFile & canonical).fetch1("path"))
    # Publishing deletes the scratch copy once it is on a share (design spec
    # `2026-09-29-nwb-publishing-design.md` section 4); the row still names
    # the path, which is what the refusal reads, so a stand-in file keeps the
    # "left untouched" half of this test meaningful.
    if not recorded.exists():
        recorded.parent.mkdir(parents=True, exist_ok=True)
        recorded.write_bytes(b"another activation's file")
    before = recorded.read_bytes()
    key = _derivative(session_key, blocks, prefix, blocks[-1])
    monkeypatch.setattr(build_module, "nwb_path", lambda *args: recorded)
    result = build_module.build(key, tmp_path_factory.mktemp("nwb-collision"))
    assert (result.status, result.path) == ("refused", None)
    assert "already recorded" in result.reason
    assert recorded.read_bytes() == before


def test_blocks_the_eye_recording_does_not_reach_are_built_without_eye_data(activation, prefix, tmp_path_factory):
    """The final review's I2: an activation whose blocks hold no eye sample
    (the tracker started late, or stopped early) is built without eye data,
    and its description says so. For one build the first block is moved past
    the end of the recording, and restored after."""
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb.build import build
    from wl_preproc.schema import core

    session_key, _key, blocks = activation
    first = min(blocks, key=lambda b: b["start_s"])
    past = max(b["end_s"] for b in blocks) + 2.0
    key = _derivative(session_key, blocks, prefix, first)
    core.Block.update1({**session_key, "block_id": first["block_id"], "start_s": past, "end_s": past + 1.0})
    try:
        result = build(key, tmp_path_factory.mktemp("nwb-no-samples"))
    finally:
        core.Block.update1({**session_key, "block_id": first["block_id"], "start_s": first["start_s"],
                            "end_s": first["end_s"]})
    assert result.status == "written", (result.reason, result.findings)
    with NWBHDF5IO(str(result.path), "r") as handle:
        nwb = handle.read()
        assert "behavior" not in nwb.processing and "eye_events" not in nwb.processing
        assert "no sample in these blocks" in nwb.session_description


def test_the_stage_waits_for_upstream_keys_not_yet_computed(activation, prefix, tmp_path_factory):
    """The final review's I4(a): a key of this session that an upstream table
    has not computed yet (still to run, or errored) would leave the file
    without it, recorded as final. One `BlockCoverage` row is held back, and
    the stage waits until it is back."""
    from wl_preproc.nwb.build import run_stage
    from wl_preproc.schema import coverage
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, blocks = activation
    key = _derivative(session_key, blocks, prefix, blocks[-1])
    (nwb_schema.NwbFile & key).delete(prompt=False)
    saved = (coverage.BlockCoverage & session_key).to_dicts()[0]
    (coverage.BlockCoverage & {k: saved[k] for k in coverage.BlockCoverage.primary_key}).delete(prompt=False)
    try:
        run_stage(tmp_path_factory.mktemp("nwb-wait-upstream"))
        assert len(nwb_schema.NwbFile & key) == 0
    finally:
        coverage.BlockCoverage.insert1(saved, allow_direct_insert=True)
    run_stage(tmp_path_factory.mktemp("nwb-wait-upstream-after"))
    assert (nwb_schema.NwbFile & key).fetch1("status") == "written"


def test_the_stage_waits_only_on_what_the_file_reads(activation, prefix):
    """A detection paramset the file does not read -- here one for a
    detector that does not exist, so its key can only ever error -- must
    not hold the file back. Found in the full suite, where another module's
    registration left such a key outstanding for every session."""
    from wl_preproc.nwb.gather import readiness
    from wl_preproc.schema import detect, paramset

    session_key, key, _blocks = activation
    extra = paramset.register("eye_detection", {"detector": "not_a_detector_nwb_build"})
    try:
        assert len((detect.EyeDetection().key_source & session_key) - detect.EyeDetection.proj()) >= 1
        assert readiness(key) is None
    finally:
        (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": extra}).delete()


def test_the_stage_waits_for_session_time_rather_than_refusing(activation, prefix, tmp_path_factory, capsys):
    """The final review's I4(b): no `TimingProvenance` row means "not yet",
    not a refusal. The stage records nothing and tries again next pass, and
    the command says what it is waiting on. The timing row is restored as it
    was, as in the refusal tests."""
    from wl_preproc.cli.main import main
    from wl_preproc.nwb.build import run_stage
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema import timebase

    session_key, _key, blocks = activation
    key = _derivative(session_key, blocks, prefix, blocks[-1])
    (nwb_schema.NwbFile & key).delete(prompt=False)
    saved = (timebase.TimingProvenance & session_key).fetch1()
    (timebase.TimingProvenance & session_key).delete(prompt=False)
    try:
        run_stage(tmp_path_factory.mktemp("nwb-wait-time"))
        assert len(nwb_schema.NwbFile & key) == 0
        assert main(_command(key, tmp_path_factory.mktemp("nwb-wait-command"), prefix)) == 1
        assert "not ready: waiting on TimingProvenance" in capsys.readouterr().out
        assert len(nwb_schema.NwbFile & key) == 0
    finally:
        timebase.TimingProvenance.insert1(saved, allow_direct_insert=True)
    run_stage(tmp_path_factory.mktemp("nwb-wait-time-after"))
    assert (nwb_schema.NwbFile & key).fetch1("status") == "written"


def test_a_file_without_the_subjects_date_of_birth_is_invalid(activation, tmp_path_factory):
    """Sections 8 and 9: `nwbinspector` rates a subject with no age and no
    date of birth as critical, so the file is built but `invalid`, and piece
    2 will not publish it. Restored after."""
    from wl_preproc.ingest.landing import SUBJECT_BIRTH_DATE_UNKNOWN
    from wl_preproc.nwb.build import build
    from wl_preproc.schema import pipeline

    _session_key, key, _blocks = activation
    birth = (pipeline.subject.Subject & {"subject": _SUBJECT}).fetch1("subject_birth_date")
    pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": SUBJECT_BIRTH_DATE_UNKNOWN})
    try:
        result = build(key, tmp_path_factory.mktemp("nwb-no-birth"))
        assert result.status == "invalid" and result.path.exists()
        assert [f["check"] for f in result.findings if f["importance"] == "CRITICAL"] == ["check_subject_age"]
    finally:
        pipeline.subject.Subject.update1({"subject": _SUBJECT, "subject_birth_date": birth})


def test_a_session_without_session_time_is_refused(activation, tmp_path_factory):
    """Section 10: no `TimingProvenance` row means no trustworthy session
    time. The row is restored as it was, not recomputed: recomputing now
    would compare wl.works' two asserted blocks with the one measured and
    rightly fail the session to tier D (parent spec section 8.3.1)."""
    from wl_preproc.nwb.build import build
    from wl_preproc.schema import timebase

    session_key, key, _blocks = activation
    saved = (timebase.TimingProvenance & session_key).fetch1()
    (timebase.TimingProvenance & session_key).delete(prompt=False)
    try:
        result = build(key, tmp_path_factory.mktemp("nwb-untimed"))
        assert (result.status, result.path) == ("refused", None)
        assert "TimingProvenance" in result.reason
    finally:
        timebase.TimingProvenance.insert1(saved, allow_direct_insert=True)


def test_a_session_at_timing_tier_d_is_refused(activation, tmp_path_factory):
    """Section 10: tier D means the session's time is not trustworthy. The
    row is swapped for a tier-D copy and restored after."""
    from wl_preproc.nwb.build import build
    from wl_preproc.schema import timebase

    session_key, key, _blocks = activation
    saved = (timebase.TimingProvenance & session_key).fetch1()
    (timebase.TimingProvenance & session_key).delete(prompt=False)
    timebase.TimingProvenance.insert1({**saved, "tier": "D"}, allow_direct_insert=True)
    try:
        result = build(key, tmp_path_factory.mktemp("nwb-tier-d"))
        assert (result.status, result.path) == ("refused", None)
        assert "tier D" in result.reason
    finally:
        (timebase.TimingProvenance & session_key).delete(prompt=False)
        timebase.TimingProvenance.insert1(saved, allow_direct_insert=True)


def test_a_session_with_two_ohdpi_segments_is_refused(activation, tmp_path_factory):
    """Section 10: the eye tables assume one ohDPI segment, so the builder
    refuses rather than guess which file the eye rows index."""
    from wl_preproc.nwb.build import build
    from wl_preproc.schema import core

    session_key, key, _blocks = activation
    segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
    extra = {**segment, "segment_barcode": segment["segment_barcode"] + 1_000}
    core.Segment.insert1(extra, allow_direct_insert=True)
    try:
        result = build(key, tmp_path_factory.mktemp("nwb-two"))
        assert result.status == "refused"
        assert "2 ohDPI segments" in result.reason
    finally:
        (core.Segment & {k: extra[k] for k in core.Segment.primary_key}).delete(prompt=False)
