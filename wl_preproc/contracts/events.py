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


class Escape(IntEnum):
    """Escape codes introducing multi-word payloads. Range 32768+."""

    TRIAL_NUMBER = 0x8001
    BLOCK_START = 0x8002
    CONDITION = 0x8003


PAYLOAD_WORD_COUNTS: dict[Escape, int] = {
    Escape.TRIAL_NUMBER: 2,  # uint32, high word first
    Escape.BLOCK_START: 2,  # (block_number, task_type_code)
    Escape.CONDITION: 2,  # uint32, high word first
}


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
