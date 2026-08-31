"""16-bit strobed event code protocol. Frozen interface — see spec section 4.2.

Range allocation:
    1-255       session/block markers and trial outcomes  (Marker)
    256-4095    task events
    4096-32767  task-specific / condition encoding
    32768+      escape codes introducing multi-word payloads  (Escape)

Task type codes are a separate 1-255 namespace carried *inside* a BLOCK_START
payload, so a block is self-describing in the recording even when the ELN is
wrong or late.

Full width goes to the sync box and NI; Intan RHS receives the strobe only,
because its 16 digital inputs cannot fit 16 data lines plus strobe plus barcode.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum

WORD_MASK = 0xFFFF


class Marker(IntEnum):
    """Session, block and trial markers. Range 1-255."""

    SESSION_START = 1
    SESSION_END = 2
    BLOCK_END = 3
    TRIAL_START = 32
    TRIAL_END = 33
    TRIAL_CORRECT = 34
    TRIAL_ERROR = 35
    TRIAL_ABORT = 36
    TRIAL_FIXATION_BREAK = 37
    TRIAL_NO_RESPONSE = 38


class TaskTypeCode(IntEnum):
    """Standing mapping tasks get reserved codes; lab-defined tasks start at 100."""

    RESTING_DARK = 1
    RF_MAP = 2
    PASSIVE_FLASH = 3
    SHAPE_MAP = 4
    COLOR_MAP = 5
    MEMORY_GUIDED_SACCADE = 6


class TargetRole(IntEnum):
    """Which on-screen target a `TARGET_POSITION` payload describes.

    `TaskTypeCode.MEMORY_GUIDED_SACCADE` puts a fixation point and a target on
    screen simultaneously; without a role two payloads are ambiguous. It also
    tells calibration which target the animal was demonstrably looking at.
    """

    FIXATION_POINT = 0
    SACCADE_TARGET = 1


class Escape(IntEnum):
    """Escape codes introducing multi-word payloads. Range 32768+."""

    TRIAL_NUMBER = 0x8001
    BLOCK_START = 0x8002
    CONDITION = 0x8003
    TARGET_POSITION = 0x8004


PAYLOAD_WORD_COUNTS: dict[Escape, int] = {
    Escape.TRIAL_NUMBER: 2,  # uint32, high word first
    Escape.BLOCK_START: 2,  # (block_number, task_type_code)
    Escape.CONDITION: 2,  # uint32, high word first
    Escape.TARGET_POSITION: 3,  # (role, x_dva, y_dva)
}


class TaskEvent(IntEnum):
    """Task events. Range 256-4095.

    `Marker.TRIAL_FIXATION_BREAK` already covers a failed hold; these bound a
    successful one, which is the window calibration fits against.
    """

    FIXATION_ACQUIRED = 256
    FIXATION_END = 257


# Degrees of visual angle, offset-binary, hundredths of a degree.
#
# **Degrees, not pixels:** this pipeline holds no screen geometry -- no viewing
# distance, no pixel pitch -- and acquiring it would mean a second transport for
# numbers that differ per rig and change whenever a monitor moves. The task
# already knows the geometry because it renders the stimulus, and MonkeyLogic
# holds `ScreenInfo.PixelsPerDegree`.
#
# **Offset-binary, not two's complement:** no sign-extension convention to get
# wrong across the task, the sync box and this decoder.
DVA_SCALE = 100
DVA_OFFSET = 32768
_DVA_MIN = -DVA_OFFSET / DVA_SCALE
_DVA_MAX = (0xFFFF - DVA_OFFSET) / DVA_SCALE


def encode_dva(degrees: float) -> int:
    """One axis of a target position, as a payload word."""
    if not _DVA_MIN <= degrees <= _DVA_MAX:
        raise ValueError(
            f"target position {degrees} deg is out of range "
            f"[{_DVA_MIN}, {_DVA_MAX}]; refused rather than clamped, because a "
            "clamped target reports a position the task did not use"
        )
    return round(degrees * DVA_SCALE) + DVA_OFFSET


def decode_dva(word: int) -> float:
    """The inverse of `encode_dva`."""
    return (word - DVA_OFFSET) / DVA_SCALE


@dataclass(frozen=True, slots=True)
class SimpleEvent:
    time_s: float
    code: int


@dataclass(frozen=True, slots=True)
class PayloadEvent:
    time_s: float
    escape: Escape
    words: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DecodeError:
    time_s: float
    reason: str


DecodedEvent = SimpleEvent | PayloadEvent | DecodeError


def _checksum(escape: Escape, words: Sequence[int]) -> int:
    accumulator = int(escape)
    for word in words:
        accumulator ^= word
    return accumulator & WORD_MASK


def encode_payload(escape: Escape, words: Sequence[int]) -> list[int]:
    """Return the full word sequence: escape, payload words, checksum."""
    expected = PAYLOAD_WORD_COUNTS[escape]
    if len(words) != expected:
        raise ValueError(f"{escape.name} takes {expected} words, got {len(words)}")
    for word in words:
        if not 0 <= word <= WORD_MASK:
            raise ValueError(f"payload word out of 16-bit range: {word}")
    return [int(escape), *words, _checksum(escape, words)]


def decode_stream(words: Sequence[tuple[float, int]]) -> list[DecodedEvent]:
    """Decode a strobed word stream into events.

    Never raises on malformed input: a corrupt or truncated payload yields a
    DecodeError and decoding continues, so one bad trial cannot lose a session.
    """
    events: list[DecodedEvent] = []
    index = 0
    while index < len(words):
        time_s, code = words[index]
        try:
            escape = Escape(code)
        except ValueError:
            events.append(SimpleEvent(time_s=time_s, code=code))
            index += 1
            continue

        count = PAYLOAD_WORD_COUNTS[escape]
        needed = count + 1  # payload words plus checksum
        available = words[index + 1 : index + 1 + needed]
        if len(available) < needed:
            events.append(DecodeError(time_s=time_s, reason=f"truncated {escape.name} payload"))
            break

        payload = tuple(word for _, word in available[:count])
        if available[count][1] != _checksum(escape, payload):
            events.append(DecodeError(time_s=time_s, reason=f"{escape.name} checksum mismatch"))
        else:
            events.append(PayloadEvent(time_s=time_s, escape=escape, words=payload))
        index += 1 + needed
    return events
