"""A sync box that recorded part of an NI-timed session (part 3b-3; design spec
`2026-10-18-training-records-and-backup-clock-design.md`, amendments 54 to
64). Under the NI's clock the systems that carry the box's barcodes, the box
itself, the eye tracker and the cameras, are fitted by today's barcode path
against the NI's own barcodes. Every session here is generated, hand-landed
and taken through one daemon pass."""

from __future__ import annotations

import pytest

from tests.identities import new_animal, new_serial
from tests.schema.test_ni_clock import _errors_of

# Drifting, so the eye tracker's and the camera's fits find a rate of their own.
DRIFT_PPM = (("ohdpi", 30.0), ("bcam", -25.0))
# The box stops inside the session, as a Pi that lost power.
STOP_S = 9.0
# The NI starts half a millisecond after the box's first barcode began, and
# before the first code word the run needs; the camera stops a little after
# the second barcode's lead edge, so the first barcode is the one it decodes
# (`wl_sync.barcode.decode_edges` needs an edge past a frame's end).
NI_STARTS_AT_S = 0.0005
CAMERA_STOPS_AT_S = 1.1


def _start_the_ni_late(session_dir, at_s: float) -> None:
    """The NI's file as one SpikeGLX started at session time `at_s`: every
    sample before it gone, at a whole sample, its `.meta` untouched, as
    `tests/timebase/test_clock.py::_cut_the_ni` leaves it."""
    from wl_preproc.synth.spikeglx import NIDQ_SAMPLE_RATE_HZ, SPIKEGLX_PRE_ROLL_S
    from wl_preproc.timebase._nidq_meta import read_nidq_meta
    from wl_preproc.timebase.clock import nidq_files

    (path,) = nidq_files(session_dir)
    n_channels = read_nidq_meta(path.with_suffix(".meta")).n_channels
    first = round((at_s + SPIKEGLX_PRE_ROLL_S) * NIDQ_SAMPLE_RATE_HZ)
    path.write_bytes(path.read_bytes()[first * n_channels * 2:])


def _stop_the_camera(session_dir, at_s: float) -> None:
    """The camera's sidecar as one whose recording stopped at session time
    `at_s`: its frames, its barcode line and its video's last frame cut
    there."""
    import yaml

    from wl_preproc.synth.peripherals import BCAM_PRE_ROLL_S, CAMERA_FPS

    path = session_dir / "bcam" / "frames.yaml"
    sidecar = yaml.safe_load(path.read_text())
    kept = int((at_s + BCAM_PRE_ROLL_S) * CAMERA_FPS)
    sidecar["digital_line"] = sidecar["digital_line"][:kept]
    sidecar["frame_count"] = kept
    sidecar["video_files"][0]["last_frame_index"] = kept - 1
    path.write_text(yaml.safe_dump(sidecar, sort_keys=False))


def _clear_the_ni_barcode_line(session_dir) -> None:
    """Bit 0 of the NI's digital word 0, its barcode line, cleared on every
    sample, as a dead cable leaves it; its strobe and data lines untouched."""
    import numpy as np

    from wl_preproc.timebase._nidq_meta import read_nidq_meta
    from wl_preproc.timebase.clock import nidq_files

    (path,) = nidq_files(session_dir)
    meta = read_nidq_meta(path.with_suffix(".meta"))
    samples = np.memmap(path, dtype=np.int16, mode="r+").reshape(-1, meta.n_channels)
    samples[:, meta.n_analog_channels] &= ~np.int16(1)
    samples.flush()


def _kill_the_camera_cable(session_dir) -> None:
    """The camera's barcode line low on every frame, as a dead cable leaves
    it: its frames all there, none carrying a barcode."""
    import yaml

    path = session_dir / "bcam" / "frames.yaml"
    sidecar = yaml.safe_load(path.read_text())
    sidecar["digital_line"] = [0] * len(sidecar["digital_line"])
    path.write_text(yaml.safe_dump(sidecar, sort_keys=False))


class _Landed(dict):
    report: dict

    def key(self, name):
        return self[name][2]

    def truth(self, name):
        return self[name][1]

    def session_dir(self, name):
        return self[name][3]


@pytest.fixture(scope="module")
def landed(dj_conn, prefix, tmp_path_factory):
    """Each session by name, `(recipe, truth, key, session_dir)`, after one
    daemon pass. Each is the NI with the sync box, the eye tracker and a
    camera: `partial`, the box stopping at `STOP_S`; `before_ni`, the same
    with the NI started after the box's first barcode and the camera stopped
    before its third, so the camera's barcodes all precede the NI; `dead_ni_line`,
    the same as `partial` with the NI's barcode line dead; `dead_cable`, the
    same as `partial` with the camera's barcode line dead; and `synced`, the
    box running the whole session and keeping time. Deleted afterwards."""
    from wl_preproc import daemon
    from wl_preproc.schema import pipeline
    from wl_preproc.synth.recipe import NI_CLOCK_RECIPE
    from wl_preproc.synth.session import generate_session

    from tests.schema.test_eye_populate import _land

    daemon.activate_all(prefix=prefix)
    serial = new_serial()
    sessions = _Landed()
    sessions.animals = {}

    def land(name: str, **update):
        animal = new_animal()
        sessions.animals[name] = animal
        session = animal.session()
        recipe = NI_CLOCK_RECIPE.model_copy(update={
            "subject": session.subject, "session_id": session.session_id, "probe_serial": serial,
            "systems": ("syncbox", "spikeglx", "ohdpi", "bcam"), "system_drift_ppm": DRIFT_PPM, **update})
        root = tmp_path_factory.mktemp(name)
        truth = generate_session(root, recipe)
        session_dir = root / recipe.session_id
        if name == "before_ni":
            _start_the_ni_late(session_dir, NI_STARTS_AT_S)
            _stop_the_camera(session_dir, CAMERA_STOPS_AT_S)
        if name == "dead_ni_line":
            _clear_the_ni_barcode_line(session_dir)
        if name == "dead_cable":
            _kill_the_camera_cable(session_dir)
        key = _land(root, recipe, session.session_datetime, acquisition_systems=recipe.systems)
        sessions[name] = (recipe, truth, key, session_dir)

    land("partial", syncbox_stop_s=STOP_S)
    land("before_ni", syncbox_stop_s=STOP_S)
    land("dead_ni_line", syncbox_stop_s=STOP_S)
    land("dead_cable", syncbox_stop_s=STOP_S)
    land("synced")
    sessions.report = daemon.run_once(prefix=prefix)
    yield sessions
    for _recipe, _truth, key, _session_dir in sessions.values():
        (pipeline.Session & key).delete(prompt=False)


def _clock(key) -> str:
    from wl_preproc.schema import timebase

    return (timebase.SessionClock & key).fetch1("source")


def _fit(key, system) -> dict:
    from wl_preproc.schema import timebase

    return (timebase.SystemTimebase & key & {"system": system}).fetch1()


def test_the_eye_tracker_and_camera_are_fitted_through_the_nis_barcodes(landed):
    """Amendment 56: each is fitted by its own barcodes against the NI's, and
    each segment's offset carries the true time of its first barcode on the
    device onto the true time the NI sampled it, to one of the device's
    frames: a system aligned by barcode is precise to one frame period, 2 ms
    at 500 Hz (`schema/timebase.py`'s `_TIME_SOURCE_ENUM` note; design spec
    section 3.1), which is why `tests/timebase/test_fit.py` holds the
    cameras' offsets to 2.5 ms. A camera whose clock runs slow places every
    barcode after the first a frame early, so the fit's offset there is off
    by more than 100 us however well it is made."""
    from wl_preproc.schema import core
    from wl_preproc.synth.ohdpi import OHDPI_PRE_ROLL_S
    from wl_preproc.synth.peripherals import BCAM_PRE_ROLL_S
    from wl_preproc.synth.spikeglx import SPIKEGLX_PRE_ROLL_S
    from wl_preproc.synth.timeline import apply_drift
    from wl_preproc.timebase import segments

    key = landed.key("partial")
    assert _clock(key) == "nidq"
    truth_s = dict(landed.truth("partial").barcodes)
    drift_ppm = dict(DRIFT_PPM)
    for system, pre_roll_s in (("ohdpi", OHDPI_PRE_ROLL_S), ("bcam", BCAM_PRE_ROLL_S)):
        fit = _fit(key, system)
        assert (fit["fit_status"], fit["time_source"]) == ("fitted", "barcode"), system
        assert fit["n_barcodes_matched"] == len(truth_s)
        rate = segments.rate_fit_from_row(fit)
        (scan,) = segments.scan_system(system, landed.session_dir("partial") / system)
        first = min(scan.barcodes, key=lambda barcode: barcode.start_us)
        segment = (core.Segment & key & {"system": system}).fetch1()
        native_s = apply_drift(truth_s[first.value], drift_ppm[system]) + pre_roll_s
        assert native_s / rate.scale + segment["offset_s"] == pytest.approx(
            truth_s[first.value] + SPIKEGLX_PRE_ROLL_S, abs=1.0 / fit["nominal_rate_hz"]), system
    assert not len(core.RejectedSegment & key)


def test_the_sync_box_is_fitted_through_the_nis_barcodes(landed):
    """Amendment 56: under the NI the box is fitted like any system carrying
    its barcodes, not given the identity row of the system that keeps time.
    Its tick 0 is `SYNCBOX_PRE_ROLL_S` before session time's, and the NI's
    first sample `SPIKEGLX_PRE_ROLL_S` before it."""
    from wl_preproc.schema import core
    from wl_preproc.synth.spikeglx import NIDQ_SAMPLE_RATE_HZ, SPIKEGLX_PRE_ROLL_S
    from wl_preproc.synth.syncbox import SYNCBOX_PRE_ROLL_S

    key = landed.key("partial")
    fit = _fit(key, "syncbox")
    assert (fit["fit_status"], fit["time_source"]) == ("fitted", "barcode")
    assert fit["n_barcodes_matched"] == len(landed.truth("partial").barcodes)
    # Neither the box nor the NI drifts, so what is measured is the NI's
    # sampling: less than one of its samples across the box's barcodes.
    covered_s = max(truth_s for _value, truth_s in landed.truth("partial").barcodes)
    assert abs(fit["drift_ppm"]) * 1e-6 * covered_s < 1.0 / NIDQ_SAMPLE_RATE_HZ
    segment = (core.Segment & key & {"system": "syncbox"}).fetch1()
    assert segment["offset_s"] == pytest.approx(SPIKEGLX_PRE_ROLL_S - SYNCBOX_PRE_ROLL_S, abs=100e-6)
    assert segment["n_barcodes"] == len(landed.truth("partial").barcodes)
    # SpikeGLX stays the clock, on the identity map.
    assert (_fit(key, "spikeglx")["fit_status"], _fit(key, "spikeglx")["time_source"]) == ("fitted", "clock")


def test_a_partial_sync_box_session_is_tier_n_and_records_its_span(landed):
    """Amendments 55, 60 and 61: nothing failed, the box's own losses are not
    asked, and the span is stored as the clock computes it."""
    from wl_preproc.schema import timebase
    from wl_preproc.timebase.clock import clock_for

    key = landed.key("partial")
    row = (timebase.TimingProvenance & key).fetch1()
    assert row["tier"] == "N", row
    assert (row["n_systems_aligned"], row["n_rejected_segments"]) == (4, 0)
    assert row["n_barcodes_emitted"] is None
    span = clock_for(key, landed.session_dir("partial")).covered_span()
    assert span is not None
    assert row["covered_start_s"] == pytest.approx(span[0], abs=1e-9)
    assert row["covered_end_s"] == pytest.approx(span[1], abs=1e-9)
    # The NI's codes are the session's: the box's are not read as them.
    assert row["n_full_code_records"] == 1
    assert row["event_code_agreement"] is None


def test_a_file_whose_barcodes_precede_the_ni_is_outside_and_does_not_fail(landed):
    """Amendment 57: the camera's one barcode was sent before the NI's first
    sample, so none is in the NI's reference. Its file is
    `outside_covered_span`, its system unfittable and left out, and the
    session is still N."""
    from wl_preproc.schema import core, timebase
    from wl_preproc.timebase import segments
    from wl_preproc.timebase.clock import clock_for

    key = landed.key("before_ni")
    session_dir = landed.session_dir("before_ni")
    assert _clock(key) == "nidq"
    reference = clock_for(key, session_dir).barcode_reference()
    (scan,) = segments.scan_system("bcam", session_dir / "bcam")
    assert scan.barcodes and not any(barcode.value in reference for barcode in scan.barcodes)
    start_s, _end_s = clock_for(key, session_dir).covered_span()
    first_value = min(barcode.value for barcode in scan.barcodes)
    assert dict(landed.truth("before_ni").barcodes)[first_value] < NI_STARTS_AT_S

    fit = _fit(key, "bcam")
    assert (fit["fit_status"], fit["time_source"]) == ("unfittable", "barcode")
    assert [row["reason"] for row in (core.RejectedSegment & key).to_dicts()] == ["outside_covered_span"]
    for system in ("syncbox", "spikeglx", "ohdpi"):
        assert _fit(key, system)["fit_status"] == "fitted", system
    row = (timebase.TimingProvenance & key).fetch1()
    assert row["tier"] == "N", row
    assert (row["n_systems_aligned"], row["n_rejected_segments"]) == (3, 1)
    assert row["covered_start_s"] == pytest.approx(start_s, abs=1e-9)


def test_an_empty_reference_places_nothing_by_barcode(landed):
    """Decision 2: with the NI's barcode line dead its reference is empty, so
    nothing is placed by barcode, even though every device decoded the box's
    barcodes. Each is unfittable by no mechanism, its files an unfitted
    system's, never outside the span, and the session is D."""
    from wl_preproc.schema import core, timebase
    from wl_preproc.timebase.clock import clock_for

    key = landed.key("dead_ni_line")
    assert _clock(key) == "nidq"
    assert clock_for(key, landed.session_dir("dead_ni_line")).barcode_reference() == {}
    for system in ("syncbox", "ohdpi", "bcam"):
        fit = _fit(key, system)
        assert (fit["fit_status"], fit["time_source"]) == ("unfittable", "none"), system
        assert fit["n_barcodes_decoded"] > 0, system
        reasons = {row["reason"] for row in (core.RejectedSegment & key & {"system": system}).to_dicts()}
        assert reasons == {"unfitted_system"}, system
    row = (timebase.TimingProvenance & key).fetch1()
    assert row["tier"] == "D"
    assert row["n_systems_aligned"] == 1
    assert (row["covered_start_s"], row["covered_end_s"]) == (None, None)


def test_under_the_sync_box_nothing_changes(landed):
    """A session the box times keeps every field this part could touch: the
    box's identity row, every other system fitted by barcode, no file
    rejected, the tier it had, and no span."""
    from wl_preproc.schema import core, timebase

    key = landed.key("synced")
    assert _clock(key) == "syncbox"
    box = _fit(key, "syncbox")
    assert (box["fit_status"], box["time_source"]) == ("fitted", "barcode")
    assert (box["drift_ppm"], box["residual_us_rms"], box["residual_us_max"]) == (0.0, 0.0, 0.0)
    for system in ("spikeglx", "ohdpi", "bcam"):
        fit = _fit(key, system)
        assert (fit["fit_status"], fit["time_source"]) == ("fitted", "barcode"), system
    assert not len(core.RejectedSegment & key)
    row = (timebase.TimingProvenance & key).fetch1()
    assert row["tier"] == "A", row
    assert row["n_full_code_records"] == 2
    assert row["n_barcodes_emitted"] > 0
    assert (row["covered_start_s"], row["covered_end_s"]) == (None, None)


def test_a_dead_barcode_cable_under_the_ni_fails_the_session(landed):
    """Amendment 57: a file that decoded no barcode at all stays `no_barcode`
    and fails the session, though the NI holds barcodes. A camera whose cable
    was dead and one that ran wholly after the box stopped look the same, and
    neither is left out as outside the span."""
    from wl_preproc.schema import core, timebase
    from wl_preproc.timebase.clock import clock_for

    key = landed.key("dead_cable")
    assert _clock(key) == "nidq"
    assert clock_for(key, landed.session_dir("dead_cable")).barcode_reference()
    fit = _fit(key, "bcam")
    assert (fit["fit_status"], fit["time_source"]) == ("unfittable", "barcode")
    assert fit["n_barcodes_decoded"] == 0
    assert [row["reason"] for row in (core.RejectedSegment & key).to_dicts()] == ["no_barcode"]
    # Not left out: everything else was placed and the NI's codes are clean,
    # so the camera is the only thing D can be for.
    for system in ("syncbox", "spikeglx", "ohdpi"):
        assert _fit(key, system)["fit_status"] == "fitted", system
    row = (timebase.TimingProvenance & key).fetch1()
    assert (row["n_systems_aligned"], row["n_rejected_segments"], row["decode_errors"]) == (3, 1, 0)
    assert row["tier"] == "D", row


def test_the_passes_report_no_error(landed):
    """Every stage runs on these sessions without an error: the pass's report
    holds no error but those naming another session, a stage-wide one
    included."""
    for name in ("partial", "before_ni", "synced"):
        assert _errors_of(landed.report, landed.key(name)) == [], name
