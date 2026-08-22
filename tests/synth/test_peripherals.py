import json

import pytest

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.sidecar import BehaviorCameraSidecar
from wl_preproc.synth.peripherals import (
    CAMERA_FPS,
    camera_frame_count,
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
    write_camera_sidecar(path, CI_RECIPE, build_timeline(CI_RECIPE))
    sidecar = BehaviorCameraSidecar.from_yaml(path.read_text())
    assert sidecar.trigger_source == "syncbox"
    assert sidecar.frame_count == camera_frame_count(CI_RECIPE)


def test_sidecar_records_dropped_frames(tmp_path):
    path = tmp_path / "frames.yaml"
    write_camera_sidecar(path, CI_RECIPE, build_timeline(CI_RECIPE), dropped=(17, 402))
    assert BehaviorCameraSidecar.from_yaml(path.read_text()).dropped_frame_ids == [17, 402]


def test_the_sidecars_digital_line_carries_decodable_barcodes(tmp_path):
    """The camera aligns the same way every other system does — by decoding the
    barcode off its own digital line (design spec section 2). At 500 Hz that is
    2.5 samples per 5 ms bit, the thinnest margin in the design, so this is the
    fixture that proves the margin is real rather than arithmetic.
    """
    from wl_sync.barcode import decode_edges, edges_from_samples

    truth = build_timeline(CI_RECIPE)
    path = tmp_path / "frames.yaml"
    write_camera_sidecar(path, CI_RECIPE, truth)

    sidecar = BehaviorCameraSidecar.from_yaml(path.read_text())
    decoded = decode_edges(edges_from_samples(sidecar.digital_line, CAMERA_FPS))

    assert [b.value for b in decoded] == [v for v, _ in truth.barcodes]


def test_the_camera_has_its_own_tick_origin(tmp_path):
    """A fourth distinct origin (design spec section 10): identical ones would
    let a pipeline that never computes an offset pass every alignment test.

    It must also clear `IDLE_MIN_US`, or the first barcode silently fails to
    decode for want of a preceding idle — the trap that has now cost this
    project twice.
    """
    from wl_sync.barcode import IDLE_MIN_US, decode_edges, edges_from_samples

    from wl_preproc.synth.peripherals import BCAM_PRE_ROLL_S
    from wl_preproc.synth.rhs import RHS_PRE_ROLL_S
    from wl_preproc.synth.spikeglx import SPIKEGLX_PRE_ROLL_S
    from wl_preproc.synth.syncbox import SYNCBOX_PRE_ROLL_S

    origins = (SYNCBOX_PRE_ROLL_S, SPIKEGLX_PRE_ROLL_S, RHS_PRE_ROLL_S, BCAM_PRE_ROLL_S)
    assert len(set(origins)) == len(origins)
    assert BCAM_PRE_ROLL_S > IDLE_MIN_US / 1_000_000.0

    truth = build_timeline(CI_RECIPE)
    path = tmp_path / "frames.yaml"
    write_camera_sidecar(path, CI_RECIPE, truth)
    sidecar = BehaviorCameraSidecar.from_yaml(path.read_text())

    first = decode_edges(edges_from_samples(sidecar.digital_line, CAMERA_FPS))[0]
    assert first.start_us == pytest.approx(BCAM_PRE_ROLL_S * 1_000_000, abs=1e6 / CAMERA_FPS)


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


def test_camera_fps_clears_the_barcode_decoding_floor():
    """The behaviour camera carries the barcode like every other system
    (design spec section 2), so its frame rate IS its sampling rate for that
    line. At the previous 200 Hz it was exactly 1.0 samples per 5 ms bit —
    undecodable — which is a fixture contradicting the design.

    Asserted against the derived floor rather than a literal, so this fails if
    either the rate or wl-sync's bit slot moves.
    """
    from wl_preproc.timebase.extract import min_sample_rate_hz

    assert CAMERA_FPS >= min_sample_rate_hz()


def test_camera_fps_has_margin_over_the_floor():
    """400 Hz is the boundary, where samples can land on transitions. The spec
    records 500 Hz as the rate with a published system behind it, so the
    fixture runs with margin rather than at the edge."""
    from wl_preproc.timebase.extract import min_sample_rate_hz

    assert CAMERA_FPS >= 1.25 * min_sample_rate_hz()
