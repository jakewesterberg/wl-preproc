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
        return self

    @classmethod
    def from_yaml(cls, text: str) -> BehaviorCameraSidecar:
        return cls.model_validate(yaml.safe_load(text))
