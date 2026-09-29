"""The NWB builder end to end, on a synthetic session through the daemon
(design spec `2026-09-28-nwb-builder-design.md` section 11)."""

from __future__ import annotations

import datetime
import io

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
        assert nwb.identifier == "2025-07-20_01.montage-0.activation-0"
        assert (nwb.subject.species, nwb.subject.sex) == ("Macaca mulatta", "F")
        assert tuple(nwb.experimenter) == ("jw",)
        assert nwb.session_start_time == built.clock["reference_time"]


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
        assert set(nwb.processing) == {"timebase"}
        assert len(nwb.trials) and len(nwb.intervals["task_events"])


def test_the_stage_skips_a_freed_session(activation, tmp_path_factory):
    """A freed session's files are gone from scratch; the stage records
    nothing for it until it is rehydrated, like every other stage."""
    from wl_preproc.nwb.build import run_stage
    from wl_preproc.schema import nwb as nwb_schema

    session_key, _key, _blocks = activation
    before = len(nwb_schema.NwbFile & session_key)
    recorded, errors = run_stage(tmp_path_factory.mktemp("nwb-freed"), freed=[session_key])
    assert errors == []
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
    assert len(nwb_schema.NwbFile.Dataset & canonical) > 50
    assert rows[(1, 0)]["status"] == "refused"


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
