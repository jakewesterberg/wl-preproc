import json

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.sidecar import BehaviorCameraSidecar
from wl_preproc.synth.peripherals import (
    CAMERA_FPS,
    write_camera_sidecar,
    write_manifest,
    write_task_file,
)
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.timeline import build_timeline


def test_manifest_validates_against_the_real_contract(tmp_path):
    """The generator must not be able to emit something the pipeline rejects.
    If it can, one of the two is wrong."""
    path = tmp_path / "session_manifest.yaml"
    write_manifest(path, CI_RECIPE)
    manifest = SessionManifest.from_yaml(path.read_text())
    assert manifest.session_id == CI_RECIPE.session_id
    assert manifest.expected_systems == list(CI_RECIPE.systems)


def test_sidecar_validates_against_the_real_contract(tmp_path):
    path = tmp_path / "frames.yaml"
    write_camera_sidecar(path, CI_RECIPE)
    sidecar = BehaviorCameraSidecar.from_yaml(path.read_text())
    assert sidecar.trigger_source == "syncbox"
    assert sidecar.frame_count == int(CI_RECIPE.duration_s * CAMERA_FPS)


def test_sidecar_records_dropped_frames(tmp_path):
    path = tmp_path / "frames.yaml"
    write_camera_sidecar(path, CI_RECIPE, dropped=(17, 402))
    assert BehaviorCameraSidecar.from_yaml(path.read_text()).dropped_frame_ids == [17, 402]


def test_task_file_lists_every_trial(tmp_path):
    truth = build_timeline(CI_RECIPE)
    path = tmp_path / "task.json"
    write_task_file(path, truth)
    payload = json.loads(path.read_text())
    assert len(payload["trials"]) == len(truth.trials)
    assert payload["trials"][0]["trial_id"] == truth.trials[0].trial_id


def test_task_file_carries_parameters_the_codes_do_not(tmp_path):
    """Codes own timing, the task file owns parameters — spec section 4.2. The
    fixture has to actually exercise that split."""
    truth = build_timeline(CI_RECIPE)
    path = tmp_path / "task.json"
    write_task_file(path, truth)
    trial = json.loads(path.read_text())["trials"][0]
    assert "condition" in trial
    assert "reward_ms" in trial
