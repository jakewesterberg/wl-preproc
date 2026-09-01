import pytest

from wl_preproc.contracts.events import (
    DecodeError,
    Escape,
    Marker,
    PayloadEvent,
    SimpleEvent,
    TaskTypeCode,
    decode_stream,
    encode_payload,
)


def stream(words, t0=0.0):
    return [(t0 + index * 0.001, word) for index, word in enumerate(words)]


def test_payload_round_trip():
    events = decode_stream(stream(encode_payload(Escape.TRIAL_NUMBER, [0x0001, 0x86A0]), 1.0))
    assert events == [
        PayloadEvent(time_s=1.0, escape=Escape.TRIAL_NUMBER, words=(0x0001, 0x86A0))
    ]


def test_trial_number_reconstructs_uint32():
    (event,) = decode_stream(stream(encode_payload(Escape.TRIAL_NUMBER, [0x0001, 0x86A0])))
    assert (event.words[0] << 16) | event.words[1] == 100000


def test_simple_markers_pass_through():
    assert decode_stream([(0.5, Marker.TRIAL_START.value), (0.9, Marker.TRIAL_CORRECT.value)]) == [
        SimpleEvent(time_s=0.5, code=Marker.TRIAL_START.value),
        SimpleEvent(time_s=0.9, code=Marker.TRIAL_CORRECT.value),
    ]


def test_corrupt_checksum_yields_error_not_exception():
    """A corrupt payload must not kill the session's decode."""
    words = encode_payload(Escape.TRIAL_NUMBER, [0x0001, 0x0002])
    words[-1] ^= 0xFFFF
    events = decode_stream(stream(words, 2.0))
    assert len(events) == 1 and isinstance(events[0], DecodeError)
    assert "checksum" in events[0].reason


def test_truncated_payload_yields_error():
    words = encode_payload(Escape.TRIAL_NUMBER, [0x0001, 0x0002])[:-1]
    events = decode_stream(stream(words, 3.0))
    assert len(events) == 1 and isinstance(events[0], DecodeError)
    assert "truncated" in events[0].reason


def test_decode_continues_after_an_error():
    words = encode_payload(Escape.TRIAL_NUMBER, [0x0001, 0x0002])
    words[-1] ^= 0xFFFF
    events = decode_stream(stream(words, 4.0) + [(5.0, Marker.TRIAL_START.value)])
    assert isinstance(events[0], DecodeError)
    assert events[1] == SimpleEvent(time_s=5.0, code=Marker.TRIAL_START.value)


def test_param_change_round_trips_with_a_valid_checksum():
    """PARAM_CHANGE (HANDOVER-wl-expcontroller.md Ask 2): two payload words
    reconstruct the uint32 sequence number, the same shape
    `test_trial_number_reconstructs_uint32` proves for TRIAL_NUMBER --
    `encode_payload` computes the checksum itself, so a valid decode here
    also proves `PAYLOAD_WORD_COUNTS[Escape.PARAM_CHANGE]` and the checksum
    both agree on two words."""
    (event,) = decode_stream(stream(encode_payload(Escape.PARAM_CHANGE, [0x0002, 0x002C]), 7.0))
    assert event == PayloadEvent(time_s=7.0, escape=Escape.PARAM_CHANGE, words=(0x0002, 0x002C))
    assert (event.words[0] << 16) | event.words[1] == 131116


def test_block_start_carries_task_type():
    (event,) = decode_stream(
        stream(encode_payload(Escape.BLOCK_START, [3, TaskTypeCode.RF_MAP.value]), 6.0)
    )
    assert event.escape is Escape.BLOCK_START
    assert TaskTypeCode(event.words[1]) is TaskTypeCode.RF_MAP


def test_wrong_word_count_rejected_at_encode():
    with pytest.raises(ValueError):
        encode_payload(Escape.TRIAL_NUMBER, [0x0001])


def test_word_out_of_16_bit_range_rejected_at_encode():
    with pytest.raises(ValueError):
        encode_payload(Escape.TRIAL_NUMBER, [0x0001, 0x1FFFF])


def test_standing_mapping_tasks_have_reserved_codes():
    """A block stays self-describing in the recording even if the ELN is wrong."""
    assert TaskTypeCode.RESTING_DARK.value == 1
    assert TaskTypeCode.RF_MAP.value == 2
    assert TaskTypeCode.PASSIVE_FLASH.value == 3


def test_markers_and_escapes_occupy_disjoint_ranges():
    """Markers live in 1-255 and escapes in 32768+, so a marker can never be
    mistaken for the start of a payload."""
    assert all(1 <= m.value <= 255 for m in Marker)
    assert all(e.value >= 0x8000 for e in Escape)
