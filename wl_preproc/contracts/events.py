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

**Ownership splits on decodability versus meaning**, per wl-expcontroller's
ADR-0007 (`docs/design/decisions/ADR-0007-event-vocabulary-ownership.md`,
accepted 2026-08-31). If getting it wrong makes a recording UNDECODABLE it is
this module's; if it makes the recording UNINTERPRETABLE it is `wl-mllib`'s:

    framing, escapes, checksum, payload word counts, DVA encoding   here
    Marker 1-255, session/block/trial structure                     here
    TaskEvent 256-4095, lab-wide task-event semantics               wl-mllib
    TaskTypeCode 100+, lab-defined task identities                  wl-mllib
    task-specific / condition 4096-32767                            wl-mllib

That ADR was written because two manifests contradicted each other:
`wl-mllib/wl.yaml` published `task-event-vocabulary` claiming "wl-preproc reads
event handling from here rather than defining it", while this file was already
a frozen interface defining it. `wlo validate` cannot catch that -- it checks
that a published name resolves to exactly one publisher, not that a description
is true -- so it was found by reading both repositories.

**One clause is not settled.** Moving `TaskEvent` 256-4095 to `wl-mllib` needs
this repository's agreement, because `TaskEvent` 256-259 are allocated HERE
(below) and ownership moving is explicitly not permission to renumber. Until
that is answered, wl-expcontroller allocates only in 4096-32767, whose
ownership is undisputed, so a decline costs no rework there.

Consequences of the rule that bind this module either way: no value is ever
renumbered; a NEW ESCAPE is an amendment to a frozen layer, while a new task
event is not; and nobody writes a second decoder -- wl-expcontroller tests
conformance by round-tripping its emitted streams through `decode_stream`
below, which is why its `wl_expcontroller/encode.py` may mirror
`PAYLOAD_WORD_COUNTS` without becoming a second source of truth.
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
    """Standing mapping tasks get reserved codes; lab-defined tasks start at 100.

    **`CALIBRATION` is one of two mechanisms, for two different situations**
    (ruled 2026-08-31; second-order design spec section 7 open question 2).
    This one marks a WHOLE BLOCK as a calibration block: it declares itself in
    its own `BLOCK_START` payload and needs no extra channel, which is what
    makes a dedicated block self-describing in the recording. For a
    calibration epoch that sits INSIDE another task, see
    `TaskEvent.CALIBRATION_START`/`CALIBRATION_END`.

    This is what `schema/eye.py` splits `n_from_calibration_block` from
    `n_from_task_fixation` on. Before it existed, that split was a provisional
    reading of `MEMORY_GUIDED_SACCADE` -- a task the design spec motivates the
    `role` word with, and never characterises as a calibration block.

    **Values are frozen and never renumbered.** A separate piece of software
    is written against these numbers, and a renumbering silently relabels
    every block in every recording made before it.
    """

    RESTING_DARK = 1
    RF_MAP = 2
    PASSIVE_FLASH = 3
    SHAPE_MAP = 4
    COLOR_MAP = 5
    MEMORY_GUIDED_SACCADE = 6
    CALIBRATION = 7


class TargetRole(IntEnum):
    """Which on-screen target a `TARGET_POSITION` payload describes.

    `TaskTypeCode.MEMORY_GUIDED_SACCADE` puts a fixation point and a target on
    screen simultaneously; without a role two payloads are ambiguous. It also
    tells calibration which target the animal was demonstrably looking at.
    """

    FIXATION_POINT = 0
    SACCADE_TARGET = 1


class Escape(IntEnum):
    """Escape codes introducing multi-word payloads. Range 32768+.

    **`PARAM_CHANGE` carries a sequence number, not the values that
    changed** (HANDOVER-wl-expcontroller.md Ask 2). wl-expcontroller
    supports live parameter editing between trials -- their own example is
    changing a search array's eccentricity from 0 to 10 degrees while the
    animal works -- and a change at trial 300 is otherwise invisible at
    analysis: the per-trial snapshot holds the content, but not the moment
    on the recorded clock it took effect.

    Follows this module's own reasoning for `BLOCK_START` (module
    docstring: a block is self-describing "even when the ELN is wrong or
    late"): content belongs in the stream when the recording must stay
    interpretable without external files. A task type qualifies; parameter
    VALUES do not, since they always travel with the session directory --
    the same shape `ingest/params.py`'s own `session_params.yaml` already
    gives session parameters that reach this pipeline outside the code
    stream -- and encoding floats into 16-bit words would cost precision
    and buy nothing the session directory does not already have. Two words
    for a uint32 reuses the shape `TRIAL_NUMBER` and `CONDITION` already
    have, below.
    """

    TRIAL_NUMBER = 0x8001
    BLOCK_START = 0x8002
    CONDITION = 0x8003
    TARGET_POSITION = 0x8004
    PARAM_CHANGE = 0x8005


PAYLOAD_WORD_COUNTS: dict[Escape, int] = {
    Escape.TRIAL_NUMBER: 2,  # uint32, high word first
    Escape.BLOCK_START: 2,  # (block_number, task_type_code)
    Escape.CONDITION: 2,  # uint32, high word first
    Escape.TARGET_POSITION: 3,  # (role, x_dva, y_dva)
    Escape.PARAM_CHANGE: 2,  # uint32 sequence number, high word first
}


class TaskEvent(IntEnum):
    """Task events. Range 256-4095.

    `Marker.TRIAL_FIXATION_BREAK` already covers a failed hold;
    `FIXATION_ACQUIRED`/`FIXATION_END` bound a successful one, which is the
    window calibration fits against.

    **`CALIBRATION_START`/`CALIBRATION_END` are the second of the two
    calibration mechanisms** (ruled 2026-08-31; the first is
    `TaskTypeCode.CALIBRATION`). They bound a calibration epoch WITHIN any
    task, so gathering calibration points does not require giving them their
    own block -- the case a `TaskTypeCode` alone cannot express, since a block
    has exactly one type.

    The two are complementary, not alternatives: a dedicated block reliably
    supplies six well-spread targets, which is what decides whether a session
    reaches the second-order rung at all, while an in-task epoch is what makes
    the points a normal task already produces attributable.

    **Values are frozen and never renumbered**, for the reason
    `TaskTypeCode`'s own docstring gives.
    """

    FIXATION_ACQUIRED = 256
    FIXATION_END = 257
    CALIBRATION_START = 258
    CALIBRATION_END = 259


# Degrees of visual angle, offset-binary, hundredths of a degree.
#
# **Degrees, not pixels:** this pipeline holds no screen geometry -- no viewing
# distance, no pixel pitch -- and acquiring it would mean a second transport for
# numbers that differ per rig and change whenever a monitor moves. Whatever
# renders the stimulus knows the geometry; this pipeline deliberately holds
# none. (This used to name MonkeyLogic's own `ScreenInfo.PixelsPerDegree` as
# the system that holds it. Under wl-expcontroller's ADR-0005 MonkeyLogic is
# not deployed at all, so that clause named a system that would not exist --
# and separately, `bhv2.py`'s own module docstring ("Which top-level block,
# and which of its fields") already found no `ScreenInfo` block exists even
# in MonkeyLogic itself: the real block is `MLConfig`. The argument does not
# need either name: it holds for whichever task renders the stimulus.)
#
# **Not even "the" pixels-per-degree of a rig is one number, which only
# strengthens the case above.** HANDOVER-wl-expcontroller.md Ask 5: these rigs
# run a split-screen mirror stereoscope, so one screen carries two viewports
# with their own centres and their own folded optical path lengths, and the
# display itself runs in one of two modes with a different deg/pixel each.
# Degrees stay well-defined regardless of which viewport or mode is active;
# a single PixelsPerDegree for "the rig" would already be the wrong shape to
# put on the wire even if this pipeline wanted screen geometry at all.
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
