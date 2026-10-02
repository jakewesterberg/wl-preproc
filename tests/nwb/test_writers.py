"""The NWB writers, on plain data (design spec
`2026-09-28-nwb-builder-design.md` sections 3, 6 and 11)."""

from __future__ import annotations

import datetime

import h5py
import numpy as np
import pytest

T0 = datetime.datetime(2026, 9, 28, 12, 0, tzinfo=datetime.timezone.utc)
SESSION = {
    "identifier": "2026-09-28_01.montage-0.activation-0",
    "session_id": "2026-09-28_01",
    "description": "canonical activation, montage 0",
    "reference_time": T0,
    "experimenter": "jw",
    "subject": {"subject_id": "monk01", "species": "Macaca mulatta", "sex": "F",
                "date_of_birth": datetime.date(2016, 3, 2)},
}
RUNS = [{"run_number": 1, "start_s": 0.0, "end_s": 30.0, "task_type": 2, "task": "rf_map", "works_run_id": "wr-1",
         "closed": True, "coverage": {"ohdpi": ("full", 30.0), "spikeglx": ("partial", 12.0)}},
        {"run_number": 2, "start_s": 30.0, "end_s": 60.0, "task_type": 0, "task": None, "works_run_id": None,
         "closed": False, "coverage": {"ohdpi": ("full", 30.0)}}]
BLOCKS = [{"block_number": 1, "run_number": 1, "block_in_run": 1, "block_type": "Bt1", "start_s": 0.5,
           "stop_s": 29.5, "closed": True, "n_trials": 1},
          {"block_number": 2, "run_number": 2, "block_in_run": 1, "block_type": None, "start_s": 30.5,
           "stop_s": 59.5, "closed": None, "n_trials": 0}]
TRIALS = [{"trial_id": 1, "start_s": 1.0, "stop_s": 3.0, "outcome": "correct", "run_number": 1, "block_id": 1,
           "coverage": {"ohdpi": ("full", 2.0)}}]
EVENTS = [{"time_s": 1.0, "event_type": "TRIAL_START", "trial_id": 1, "block_id": None, "condition": None},
          {"time_s": 2.5, "event_type": "CODE_256", "trial_id": None, "block_id": None, "condition": None}]


def _write(tmp_path, build):
    from pynwb import NWBHDF5IO

    from wl_preproc.nwb.session import new_file
    from wl_preproc.nwb.write import write_atomically

    nwb = new_file(SESSION)
    build(nwb)
    path = tmp_path / "f.nwb"
    write_atomically(nwb, path)
    io = NWBHDF5IO(str(path), "r")
    return path, io, io.read()


def test_the_file_is_one_activation_on_session_time(tmp_path):
    _path, io, nwb = _write(tmp_path, lambda nwb: None)
    with io:
        assert nwb.identifier == SESSION["identifier"]
        assert nwb.session_start_time == T0 and nwb.timestamps_reference_time == T0
        assert tuple(nwb.experimenter) == ("jw",)
        assert (nwb.subject.species, nwb.subject.sex) == ("Macaca mulatta", "F")
        assert nwb.subject.date_of_birth.date() == datetime.date(2016, 3, 2)


def test_runs_blocks_trials_and_events(tmp_path):
    from wl_preproc.nwb.intervals import add_blocks, add_runs, add_task_events, add_trials

    def build(nwb):
        add_runs(nwb, RUNS, ["ohdpi", "spikeglx"])
        add_blocks(nwb, BLOCKS)
        add_trials(nwb, TRIALS, ["ohdpi"])
        add_task_events(nwb, EVENTS)

    _path, io, nwb = _write(tmp_path, build)
    with io:
        runs = nwb.intervals["runs"].to_dataframe()
        assert runs["run_number"].tolist() == [1, 2]
        assert runs["works_run_id"].tolist() == ["wr-1", ""]
        assert (runs["task"].tolist(), runs["task_code"].tolist(), runs["closed"].tolist()) == (
            ["rf_map", ""], [2, 0], [True, False])
        assert runs["coverage_spikeglx"].tolist() == ["partial", ""]
        assert runs["covered_s_spikeglx"].iloc[0] == 12.0 and np.isnan(runs["covered_s_spikeglx"].iloc[1])
        blocks = nwb.intervals["blocks"].to_dataframe()
        assert blocks[["block_number", "run_number", "block_in_run", "block_type", "closed", "n_trials"]].values.tolist() == [
            [1, 1, 1, "Bt1", 1, 1], [2, 2, 1, "", -1, 0]]
        assert blocks["start_time"].tolist() == [0.5, 30.5]
        trials = nwb.trials.to_dataframe()
        assert trials[["trial_id", "outcome", "block_id", "run_number", "coverage_ohdpi"]].iloc[0].tolist() == [
            1, "correct", 1, 1, "full"]
        events = nwb.intervals["task_events"].to_dataframe()
        assert events["start_time"].tolist() == events["stop_time"].tolist() == [1.0, 2.5]
        assert events["event_type"].tolist() == ["TRIAL_START", "CODE_256"]
        assert events["trial_id"].tolist() == [1, -1]


def test_the_trials_carry_their_condition_and_the_settings_that_varied(tmp_path):
    """Design spec `2026-09-29-nwb-publishing-design.md` section 2.2: the
    condition's name, and one column per setting that varied."""
    from wl_preproc.nwb.intervals import add_trials

    trials = [{**TRIALS[0], "condition": "contrast-50", "settings": {"contrast": 0.5, "xy": "[0, 5]"}},
              {**TRIALS[0], "trial_id": 2, "start_s": 4.0, "stop_s": 5.0, "condition": "",
               "settings": {"contrast": np.nan, "xy": ""}}]
    _path, io, nwb = _write(tmp_path, lambda nwb: add_trials(nwb, trials, ["ohdpi"]))
    with io:
        frame = nwb.trials.to_dataframe()
        assert frame["condition"].tolist() == ["contrast-50", ""]
        assert frame["setting_contrast"].iloc[0] == 0.5 and np.isnan(frame["setting_contrast"].iloc[1])
        assert frame["setting_xy"].tolist() == ["[0, 5]", ""]


def test_the_conditions_table_and_none_when_no_condition_is_known(tmp_path):
    from wl_preproc.nwb.intervals import add_conditions

    conditions = [{"name": "contrast-50", "code": None, "settings": {"contrast": 0.5},
                   "varying": {"hold": {"min": 0.3, "max": 0.35}}, "trials": {"total": 2, "by_outcome": {"correct": 2}}},
                  {"name": None, "code": 7, "settings": None, "varying": None,
                   "trials": {"total": 1, "by_outcome": {"correct": 1}}}]
    _path, io, nwb = _write(tmp_path, lambda nwb: add_conditions(nwb, conditions))
    with io:
        frame = nwb.processing["behavior"]["conditions"].to_dataframe()
        assert frame["condition"].tolist() == ["", "contrast-50"]
        assert frame["code"].tolist() == [7, -1]
        assert frame["settings"].tolist() == ["", '{"contrast": 0.5}']
        assert frame["varying"].tolist() == ["", '{"hold": {"max": 0.35, "min": 0.3}}']
        assert frame["n_trials"].tolist() == [1, 2]
    (tmp_path / "empty").mkdir()
    _path, io, nwb = _write(tmp_path / "empty", lambda nwb: add_conditions(nwb, []))
    with io:
        assert "behavior" not in nwb.processing

def test_the_timebase_tables(tmp_path):
    from wl_preproc.nwb.timebase import add_timebase

    provenance = {"tier": "A", "n_systems_aligned": 2, "n_segments": 2, "n_rejected_segments": 0,
                  "worst_residual_us": 12.0, "worst_drift_ppm": 3.0}
    clocks = [{"system": "ohdpi", "fit_status": "fitted", "nominal_rate_hz": 500.0, "fitted_rate_hz": 498.55,
               "drift_ppm": 2.0, "residual_us_rms": 10.0}]
    segments = [{"system": "ohdpi", "file_path": "ohdpi/x.txt", "first_sample": 7, "offset_s": 0.1,
                 "start_s": 0.1, "end_s": 60.1, "n_samples": 30000}]
    reference = {"source": "barcode", "reference_datetime": T0.isoformat(), "manifest_started_at": T0.isoformat(),
                 "started_at_difference_s": 0.4}
    _path, io, nwb = _write(tmp_path, lambda nwb: add_timebase(nwb, provenance, clocks, segments, reference))
    with io:
        module = nwb.processing["timebase"]
        assert module["timing_provenance"].to_dataframe()["tier"].tolist() == ["A"]
        assert module["system_clocks"].to_dataframe()["fitted_rate_hz"].tolist() == [498.55]
        assert module["segments"].to_dataframe()["n_samples"].tolist() == [30000]
        assert module["clock_reference"].to_dataframe()["source"].tolist() == ["barcode"]


def _eye_data(n=2000, eyes=("left", "right")):
    times = 1.0 + np.arange(n) / 500.0
    gaze = {eye: np.column_stack([np.sin(times), np.cos(times)]) for eye in eyes}
    pupil = {eye: np.ones((n, 5)) for eye in ("left", "right")}
    return times, gaze, pupil


def test_the_eye_series_share_one_timestamps_dataset(tmp_path):
    from wl_preproc.nwb.eye import add_eye_series

    times, gaze, pupil = _eye_data()
    path, io, nwb = _write(tmp_path, lambda nwb: add_eye_series(nwb, times, gaze, pupil))
    with io:
        eye = nwb.processing["behavior"]["EyeTracking"]
        np.testing.assert_allclose(eye["gaze_left"].timestamps[:], times)
        np.testing.assert_allclose(eye["gaze_right"].data[:], gaze["right"].astype(np.float32))
        assert nwb.processing["behavior"]["PupilTracking"]["pupil_right"].data.shape == (2000, 5)
    with h5py.File(path) as handle:
        base = "processing/behavior"
        left = handle[f"{base}/EyeTracking/gaze_left/timestamps"]
        for other in ("EyeTracking/gaze_right", "PupilTracking/pupil_left", "PupilTracking/pupil_right"):
            assert handle[f"{base}/{other}/timestamps"].id == left.id, other
        data = handle[f"{base}/EyeTracking/gaze_left/data"]
        assert data.compression == "gzip" and data.chunks == (2000, 2)


def test_continuous_chunks_are_about_256_kb():
    from wl_preproc.nwb.columns import CHUNK_BYTES, continuous

    io = continuous(np.zeros((1_000_000, 2), dtype=np.float32))
    assert io.io_settings["chunks"] == (CHUNK_BYTES // 8, 2)
    assert io.io_settings["compression"] == "gzip"


def test_an_eye_with_no_calibration_has_no_gaze(tmp_path):
    """Design spec section 10's one partial case."""
    from wl_preproc.nwb.eye import add_eye_series

    times, gaze, pupil = _eye_data(eyes=("left",))
    _path, io, nwb = _write(tmp_path, lambda nwb: add_eye_series(nwb, times, gaze, pupil))
    with io:
        assert list(nwb.processing["behavior"]["EyeTracking"].spatial_series) == ["gaze_left"]


def test_the_eye_tables(tmp_path):
    from wl_preproc.nwb.eye import add_eye_tables

    calibration = [{"eye": "left", "calibration_source": "fitted", "calibration_model": "affine",
                    "gx_const": 0.1, "gx_dx": 1.0, "gx_dy": 0.0, "gx_dx2": None, "gx_dy2": None, "gx_dxdy": None,
                    "gy_const": 0.0, "gy_dx": 0.0, "gy_dy": 1.0, "gy_dx2": None, "gy_dy2": None, "gy_dxdy": None,
                    "validation_error_deg": 0.4, "n_points": 9, "residual_deg_rms": 0.3, "residual_deg_max": 0.8,
                    "reason": ""}]
    validity = {"left": [(3.0, 3.2, "blink")], "right": []}
    repairs = {"left": [(5.0, 5.004)], "right": []}
    _path, io, nwb = _write(tmp_path, lambda nwb: add_eye_tables(nwb, calibration, validity, repairs))
    with io:
        module = nwb.processing["behavior"]
        cal = module["eye_calibration"].to_dataframe()
        assert cal["gx_const"].tolist() == [0.1] and np.isnan(cal["gx_dx2"].iloc[0])
        assert module["eye_validity_left"].to_dataframe()["label"].tolist() == ["blink"]
        assert len(module["eye_validity_right"]) == 0
        assert module["eye_glitch_repairs_left"].to_dataframe()["stop_time"].tolist() == [5.004]


def test_every_detectors_events_and_sources_and_agreement(tmp_path):
    from wl_preproc.nwb.eye_events import add_agreement, add_detections, add_sources

    runs = [{"start_s": 1.0, "stop_s": 1.04, "label": "saccade", "amplitude_deg": 2.0, "peak_velocity_deg_s": 150.0,
             "start_x_deg": 0.0, "start_y_deg": 0.0, "end_x_deg": 2.0, "end_y_deg": 0.0, "direction_deg": 0.0,
             "reliability": None},
            {"start_s": 1.04, "stop_s": 2.0, "label": "fixation"}]
    tables = [{"name": f"engbert_kliegl_{trace}", "description": "d", "runs": runs} for trace in ("left", "right", "conjunction")]
    sources = [{"name": "engbert_kliegl_source", "description": "d", "runs": [(0.0, 60.0, "both")]}]
    agreement = [{"detector_a": "engbert_kliegl", "detector_b": "bmd", "trace": "left", "metric": "event_f1",
                  "vocabulary": "saccadic", "pso_as": "", "value": 0.8, "n_samples_compared": 1000}]

    def build(nwb):
        add_detections(nwb, tables)
        add_sources(nwb, sources)
        add_agreement(nwb, agreement)

    _path, io, nwb = _write(tmp_path, build)
    with io:
        module = nwb.processing["eye_events"]
        left = module["engbert_kliegl_left"].to_dataframe()
        assert left["label"].tolist() == ["saccade", "fixation"]
        assert left["amplitude_deg"].iloc[0] == 2.0 and np.isnan(left["amplitude_deg"].iloc[1])
        assert np.isnan(left["reliability"].iloc[0])
        assert module["engbert_kliegl_source"].to_dataframe()["source"].tolist() == ["both"]
        assert module["detector_agreement"].to_dataframe()["value"].tolist() == [0.8]


def test_a_table_with_no_rows_writes(tmp_path):
    from wl_preproc.nwb.intervals import add_task_events

    _path, io, nwb = _write(tmp_path, lambda nwb: add_task_events(nwb, []))
    with io:
        assert len(nwb.intervals["task_events"]) == 0


# -- Probes and areas (design spec `2026-09-30-nwb-probes-design.md` section 3.1)


def _probe(serial, **fields):
    return {"serial": serial, "probe_type": "NP1000", "insertion_number": None, "trajectory_id": None,
            "target": None, "assignment": None, "area_from": "unknown", "area": "unknown", "electrodes": [],
            **fields}


def test_a_probe_the_recording_does_not_name_has_no_model(tmp_path):
    """An Intan probe is listed from wl.works' report alone (section 5)."""
    from wl_preproc.nwb.probes import add_probes

    intan = _probe("R-7", probe_type=None, insertion_number=3)
    _path, io, nwb = _write(tmp_path, lambda nwb: add_probes(nwb, [intan]))
    with io:
        device = nwb.devices["probe-R-7"]
        assert (device.serial_number, device.model) == ("R-7", None)
        assert nwb.electrode_groups["insertion-3"].location == "unknown"
        assert nwb.electrodes is None  # no geometry, so no electrode table at all


@pytest.mark.parametrize("source, words", [
    ("histology", "assigned by histology: V4v"), ("functional_mapping", "assigned by functional mapping: V4v"),
    ("waveform_depth", "assigned by waveform depth: V4v"), ("structural_imaging", "assigned by structural imaging: V4v"),
    ("at_rig", "assigned at rig: V4v"), ("other", "assigned otherwise: V4v"),
])
def test_a_groups_description_names_both_areas(source, words):
    from wl_preproc.nwb.probes import area_text

    probe = _probe("1", target={"area": "V4d", "atlas": "CHARM", "atlas_level": 6},
                   assignment={"area": "V4v", "source": source, "asserted_at": T0})
    assert area_text(probe) == (f"target: V4d (CHARM, level 6); {words} (2026-09-28). "
                                "The insertion's area, not depth-resolved.")
    assert area_text(_probe("2")).startswith("no area reported")


def test_a_serial_an_hdf5_name_cannot_hold_is_kept_whole_under_a_safe_name(tmp_path):
    """The final review's M4: HDMF refuses `/` and `:` in an object's name,
    and an Intan probe's serial comes from wl.works unchecked. The device
    and group names are made safe; the serial itself is kept whole."""
    from wl_preproc.nwb.probes import add_probes

    odd = _probe("A1x32/5:mm", probe_type=None)
    _path, io, nwb = _write(tmp_path, lambda nwb: add_probes(nwb, [odd]))
    with io:
        assert list(nwb.devices) == ["probe-A1x32_5_mm"]
        assert nwb.devices["probe-A1x32_5_mm"].serial_number == "A1x32/5:mm"
        assert list(nwb.electrode_groups) == ["probe-A1x32_5_mm"]


def test_runs_that_hold_no_block_add_no_block_table(tmp_path):
    """A run that stopped before its first trial has no block; an empty
    table is not written."""
    from wl_preproc.nwb.intervals import add_blocks, add_runs

    def build(nwb):
        add_runs(nwb, RUNS[:1], ["ohdpi"])
        add_blocks(nwb, [])

    _path, io, nwb = _write(tmp_path, build)
    with io:
        assert "blocks" not in nwb.intervals and "runs" in nwb.intervals
