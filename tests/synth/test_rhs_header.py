import struct

from wl_preproc.synth.rhs_header import NULL_QSTRING_LENGTH, qstring


def _decode_like_neo(raw: bytes) -> tuple[str, int]:
    """Decode exactly the way neo.rawio.intanrawio.read_qstring does, so these
    tests fail if our encoder and the reader ever disagree."""
    length = struct.unpack("<I", raw[:4])[0]
    if length in (NULL_QSTRING_LENGTH, 0):
        return "", 4
    return raw[4 : 4 + length].decode("utf-16"), 4 + length


def test_ascii_round_trips():
    text, consumed = _decode_like_neo(qstring("A-000"))
    assert text == "A-000"
    assert consumed == 4 + 10


def test_length_is_in_bytes_not_characters():
    """The vendor document's own MATLAB divides this by 2 to get the character
    count, so a character count here would halve every string on read."""
    raw = qstring("ABCD")
    assert struct.unpack("<I", raw[:4])[0] == 8


def test_none_is_the_null_sentinel():
    raw = qstring(None)
    assert struct.unpack("<I", raw[:4])[0] == NULL_QSTRING_LENGTH
    assert len(raw) == 4


def test_empty_string_is_not_the_null_sentinel():
    raw = qstring("")
    assert struct.unpack("<I", raw[:4])[0] == 0
    assert len(raw) == 4


def test_non_ascii_survives():
    text, _ = _decode_like_neo(qstring("Port Å"))
    assert text == "Port Å"


def test_no_byte_order_mark_is_emitted():
    """neo decodes with codec 'utf-16', which honours a BOM if present. Emitting
    one would prepend a zero-width character to every string it reads."""
    raw = qstring("AB")
    assert raw[4:] == b"A\x00B\x00"
