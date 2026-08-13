import pytest
from pydantic import ValidationError

from wl_preproc.contracts.sidecar import BehaviorCameraSidecar

VALID = """
schema_version: 1
system: bcam0
trigger_source: syncbox
frame_count: 1000
dropped_frame_ids: [17, 402]
video_files:
  - path: bcam0_seg000.mp4
    first_frame_index: 0
    last_frame_index: 499
    codec: h264
    checksum: "blake3:aa11"
  - path: bcam0_seg001.mp4
    first_frame_index: 500
    last_frame_index: 999
    codec: h264
    checksum: "blake3:bb22"
"""


def test_valid_sidecar_parses():
    sidecar = BehaviorCameraSidecar.from_yaml(VALID)
    assert sidecar.frame_count == 1000
    assert len(sidecar.video_files) == 2


def test_unknown_key_is_rejected():
    with pytest.raises(ValidationError):
        BehaviorCameraSidecar.from_yaml(VALID + "\nfrmae_rate: 200\n")


def test_trigger_source_must_be_syncbox():
    """Frame times are known by construction only if the sync box triggers them."""
    with pytest.raises(ValidationError):
        BehaviorCameraSidecar.from_yaml(
            VALID.replace("trigger_source: syncbox", "trigger_source: free_running")
        )


def test_video_files_must_be_contiguous():
    with pytest.raises(ValidationError) as exc:
        BehaviorCameraSidecar.from_yaml(
            VALID.replace("first_frame_index: 500", "first_frame_index: 501")
        )
    assert "contiguous" in str(exc.value)


def test_frame_count_must_match_segment_coverage():
    with pytest.raises(ValidationError) as exc:
        BehaviorCameraSidecar.from_yaml(VALID.replace("frame_count: 1000", "frame_count: 999"))
    assert "frame_count" in str(exc.value)


def test_reversed_segment_indices_rejected():
    """last_frame_index before first_frame_index is incoherent on its own,
    independently of contiguity."""
    bad = """
schema_version: 1
system: bcam0
trigger_source: syncbox
frame_count: 10
dropped_frame_ids: []
video_files:
  - path: bcam0.mp4
    first_frame_index: 9
    last_frame_index: 0
    codec: h264
    checksum: "blake3:dd44"
"""
    with pytest.raises(ValidationError) as exc:
        BehaviorCameraSidecar.from_yaml(bad)
    assert "precedes" in str(exc.value)


def test_single_segment_is_valid():
    """Not every session splits video. One file covering everything must pass."""
    single = """
schema_version: 1
system: bcam0
trigger_source: syncbox
frame_count: 10
dropped_frame_ids: []
video_files:
  - path: bcam0.mp4
    first_frame_index: 0
    last_frame_index: 9
    codec: h264
    checksum: "blake3:cc33"
"""
    assert BehaviorCameraSidecar.from_yaml(single).frame_count == 10
