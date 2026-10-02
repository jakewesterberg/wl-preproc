"""The file's description, on plain data (design spec
`2026-09-29-nwb-publishing-design.md` section 2)."""

from __future__ import annotations

import datetime

import pytest

BUILT_AT = datetime.datetime(2026, 9, 29, 12, 0, tzinfo=datetime.timezone.utc)
SESSION = {
    "identifier": "monk01.2026-09-28_01.montage-0.activation-0",
    "session_id": "2026-09-28_01",
    "session_datetime": datetime.datetime(2026, 9, 28, 9, 0, tzinfo=datetime.timezone.utc),
    "montage_id": 0,
    "activation_id": 0,
    "role": "canonical",
    "supersedes_activation_id": None,
    "rig": "rig-a",
    "timing_tier": "B",
    "clock": {"source": "manifest"},
    "subject": {"subject_id": "monk01", "species": "Macaca mulatta", "sex": "F",
                "date_of_birth": datetime.date(2016, 3, 2)},
}
CONDITION = {"name": "contrast-50", "code": None, "settings": {"contrast": 0.5}, "varying": {},
             "trials": {"total": 1, "by_outcome": {"correct": 1}}}
RUN = {"run_number": 1, "start_s": 0.5, "end_s": 29.5, "task_type": 2, "task": None, "works_run_id": "wr-1",
       "closed": True, "coverage": {"ohdpi": ("full", 29.0)},
       "trials": {"total": 1, "by_outcome": {"correct": 1}}, "conditions": [CONDITION]}
BLOCK = {"block_number": 1, "run_number": 1, "block_in_run": 1, "block_type": "Bt1", "start_s": 0.6,
         "stop_s": 29.4, "closed": None, "n_trials": 1}
CHECKSUM = {"dataset_path": "/intervals/trials/start_time", "dtype": "float64", "shape": "(1,)",
            "sha256": "0" * 64, "paired_with": ""}


def _gathered(eye=None, notes=(), probes=(), probe_notes=(), trial_notes=()):
    from wl_preproc.nwb.gather import Gathered

    return Gathered(session=SESSION, systems=["ohdpi"], runs=[RUN], blocks=[BLOCK], trials=[{"trial_id": 1}],
                    events=[{}, {}], timebase={}, eye=eye, conditions=[CONDITION], condition_notes=list(notes),
                    probes=list(probes), probe_notes=list(probe_notes), trial_notes=list(trial_notes))


def test_the_description_of_a_file_without_eye_data():
    from wl_preproc.nwb.describe import describe

    out = describe(_gathered(notes=["no rig trial record (xcon/trials.jsonl)"]), status="written", n_critical=0,
                   checksums=[CHECKSUM], built_at=BUILT_AT)
    assert out["schema_version"] == 3
    assert out["identity"]["identifier"] == SESSION["identifier"]
    assert (out["identity"]["rig"], out["identity"]["role"], out["identity"]["status"]) == ("rig-a", "canonical", "written")
    assert out["identity"]["pipeline"]["name"] == "wl-preproc"
    assert out["subject"] == {"species": "Macaca mulatta", "sex": "F", "date_of_birth": "2016-03-02",
                              "age_days": (datetime.date(2026, 9, 28) - datetime.date(2016, 3, 2)).days}
    assert out["data_types"]["eye"] is None and out["data_types"]["eye_events"] is None
    assert out["data_types"]["behaviour"] == {"trials": 1, "events": 2}
    assert out["data_types"]["ephys"] is None and out["probes"] == [] and out["processing"] == {}
    (run,) = out["runs"]
    assert (run["run_number"], run["works_run_id"], run["closed"]) == (1, "wr-1", True)
    assert run["task"] == {"code": "2", "name": "rf_map"}
    assert run["measured"] == {"start_s": 0.5, "stop_s": 29.5}
    assert run["coverage"] == {"ohdpi": {"coverage": "full", "covered_s": 29.0}}
    assert run["conditions"] == [CONDITION]
    # The measured blocks inside the run (design spec
    # `2026-10-01-session-listing-and-run-requests-design.md` section 4).
    assert run["blocks"] == [{"block_number": 1, "block_in_run": 1, "block_type": "Bt1",
                              "measured": {"start_s": 0.6, "stop_s": 29.4}, "closed": None, "trials": 1}]
    assert out["quality"] == {"timing_tier": "B", "reference_source": "manifest",
                              "eye_usable_fraction": {"left": None, "right": None}}
    assert out["notes"] == ["no rig trial record (xcon/trials.jsonl)"]
    assert out["checksums"] == {"algorithm": "sha256", "datasets": [CHECKSUM]}


def test_the_eye_is_described_by_the_series_and_detectors_it_has():
    from wl_preproc.nwb.describe import describe

    eye = {"gaze": {"left": [1], "right": None}, "pupil": {"left": [1], "right": [1]},
           "detections": [{"name": "engbert_kliegl_left"}, {"name": "engbert_kliegl_conjunction"}, {"name": "bmd_left"}],
           "usable_fraction": {"left": 0.9, "right": None}}
    out = describe(_gathered(eye=eye), status="invalid", n_critical=1, checksums=[], built_at=BUILT_AT)
    assert out["data_types"]["eye"] == {"gaze": ["left"], "pupil": ["left", "right"]}
    assert out["data_types"]["eye_events"] == {"detectors": ["bmd", "engbert_kliegl"]}
    assert out["quality"]["eye_usable_fraction"] == {"left": 0.9, "right": None}
    assert (out["identity"]["status"], out["identity"]["n_critical"]) == ("invalid", 1)


def test_the_description_is_held_to_its_contract():
    """Strict on this side, so what is published is exactly the exported
    schema; a reader ignores what it does not know."""
    from pydantic import ValidationError

    from wl_preproc.nwb.describe import describe

    with pytest.raises(ValidationError):
        describe(_gathered(), status="refused", n_critical=0, checksums=[], built_at=BUILT_AT)


def test_each_probe_is_described_with_both_areas_and_where_its_label_came_from():
    """Design spec `2026-09-30-nwb-probes-design.md` section 3.2: version 2
    gives `probes` its shape, and the probe notes follow the condition
    notes."""
    from wl_preproc.nwb.describe import describe

    assigned = {"serial": "19011110001", "probe_type": "NP1032", "insertion_number": 1, "trajectory_id": "T-1",
                "target": {"area": "V4d", "atlas": "CHARM", "atlas_level": 6},
                "assignment": {"area": "V4v", "source": "histology", "asserted_at": BUILT_AT},
                "area_from": "assignment", "area": "V4v",
                "electrodes": [{"electrode": 0, "shank": 0, "x": 11.0, "y": 0.0}]}
    unknown = {"serial": "R-7", "probe_type": None, "insertion_number": None, "trajectory_id": None,
               "target": None, "assignment": None, "area_from": "unknown", "area": "unknown", "electrodes": []}
    out = describe(_gathered(notes=["a condition note"], probes=[assigned, unknown], probe_notes=["a probe note"]),
                   status="written", n_critical=0, checksums=[], built_at=BUILT_AT)
    assert out["probes"] == [
        {"serial": "19011110001", "probe_type": "NP1032", "insertion_number": 1, "trajectory_id": "T-1",
         "n_electrodes": 1, "target": {"area": "V4d", "atlas": "CHARM", "atlas_level": 6},
         "assignment": {"area": "V4v", "source": "histology", "asserted_at": "2026-09-29T12:00:00Z"},
         "area_from": "assignment", "sorted_runs": []},
        {"serial": "R-7", "probe_type": None, "insertion_number": None, "trajectory_id": None, "n_electrodes": 0,
         "target": None, "assignment": None, "area_from": "unknown", "sorted_runs": []},
    ]
    assert out["notes"] == ["a condition note", "a probe note"]


def test_the_trial_notes_follow_the_condition_notes():
    """Design spec `2026-10-01-runs-and-trials-design.md` sections 3.2 and
    3.4: what the stored trials leave out, between the condition notes and
    the probe notes."""
    from wl_preproc.nwb.describe import describe

    out = describe(_gathered(notes=["a condition note"], trial_notes=["a trial note"], probe_notes=["a probe note"]),
                   status="written", n_critical=0, checksums=[], built_at=BUILT_AT)
    assert out["notes"] == ["a condition note", "a trial note", "a probe note"]


def test_a_run_takes_the_rigs_name_for_its_task_and_a_probe_its_sorted_runs():
    """The rig's record names a run's task (`core.RunRecord`); each probe
    carries the runs its sort covers, as the request stated them."""
    from wl_preproc.nwb.describe import describe

    probe = {"serial": "19011110001", "probe_type": "NP1000", "insertion_number": 1, "trajectory_id": None,
             "electrodes": [0, 1], "target": None, "assignment": None, "area_from": "unknown", "sorted_runs": [1]}
    data = _gathered(probes=[probe])
    data.runs[0] = {**RUN, "task": "fixation_detection"}
    out = describe(data, status="written", n_critical=0, checksums=[], built_at=BUILT_AT)
    assert out["runs"][0]["task"] == {"code": "2", "name": "fixation_detection"}
    assert out["probes"][0]["sorted_runs"] == [1]
