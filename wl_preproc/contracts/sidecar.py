"""Behavior-camera sidecar. Frozen interface — see spec section 4.6.

This is the contract the separate FLIR behaviour-camera project builds against.
Nothing here may change without that project agreeing, which is why it is a
frozen interface rather than an implementation detail.
"""

from __future__ import annotations

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

SCHEMA_VERSION = 1
REQUIRED_TRIGGER_SOURCE = "syncbox"


class VideoFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    first_frame_index: int
    last_frame_index: int
    codec: str
    checksum: str

    @model_validator(mode="after")
    def _indices_ordered(self) -> VideoFile:
        if self.last_frame_index < self.first_frame_index:
            raise ValueError(f"{self.path}: last_frame_index precedes first_frame_index")
        return self


class BehaviorCameraSidecar(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    system: str
    trigger_source: str
    frame_count: int
    dropped_frame_ids: list[int]
    video_files: list[VideoFile]

    # Proposed 2026-08-16 for Phase 1c-4, and OPTIONAL by design.
    #
    # This is a PUBLISHED contract the separate FLIR project builds against, so
    # the field is added in a backward-compatible shape: existing sidecars
    # without it still validate, and the FLIR project is not broken by a change
    # it has not agreed to. Until it emits the field, bcam alignment is
    # specified and unavailable rather than silently wrong.
    #
    # One 0/1 sample per frame, in frame order: the camera's frame rate IS its
    # sampling rate for this line, which is what lets the barcode be decoded
    # from it like any other system's (design spec section 2).
    #
    # See the 1c-4 design spec section 13. This is a proposal, not an applied
    # amendment.
    digital_line: list[int] | None = None

    # Proposed with `digital_line` and optional for the same reason. Design spec
    # section 13 names BOTH as missing from this contract: the line is useless
    # without the rate that sampled it, because a frame index only becomes a
    # time when the rate is known.
    #
    # It is a field rather than a constant on our side because it is the
    # CAMERA's rate, and the camera is not ours: `wl_preproc.synth.CAMERA_FPS`
    # is what the FIXTURE runs at, and a consumer that reads one and means the
    # other is wrong by exactly the ratio nobody checked. A sidecar without it
    # is not guessed at — see `timebase.extract.extract_bcam`, which refuses
    # rather than assuming, so bcam alignment stays specified-and-unavailable
    # instead of silently wrong.
    frame_rate_hz: float | None = None

    @model_validator(mode="after")
    def _check_coverage(self) -> BehaviorCameraSidecar:
        if self.trigger_source != REQUIRED_TRIGGER_SOURCE:
            raise ValueError(
                f"trigger_source must be {REQUIRED_TRIGGER_SOURCE!r}: frame times are "
                "known by construction only when the sync box triggers the camera"
            )
        expected_next = 0
        for video in sorted(self.video_files, key=lambda f: f.first_frame_index):
            if video.first_frame_index != expected_next:
                raise ValueError(
                    f"{video.path}: video files must be contiguous, expected first frame "
                    f"{expected_next}, got {video.first_frame_index}"
                )
            expected_next = video.last_frame_index + 1
        if expected_next != self.frame_count:
            raise ValueError(
                f"frame_count {self.frame_count} does not match segment coverage {expected_next}"
            )
        if self.frame_rate_hz is not None and self.frame_rate_hz <= 0.0:
            raise ValueError(
                f"frame_rate_hz {self.frame_rate_hz} cannot time anything"
            )
        if self.digital_line is not None and len(self.digital_line) != self.frame_count:
            # One sample per frame is what makes the index a time. A shorter or
            # longer array does not fail on read — it shifts every sample after
            # the discrepancy against its frame, which moves the decoded barcode
            # times and therefore this camera's whole offset fit, silently.
            raise ValueError(
                f"digital_line has {len(self.digital_line)} samples for "
                f"{self.frame_count} frames; it carries one sample per frame, "
                "and a mismatch mis-times every frame after the discrepancy"
            )
        return self

    @classmethod
    def from_yaml(cls, text: str) -> BehaviorCameraSidecar:
        return cls.model_validate(yaml.safe_load(text))
