"""The Standard Intan RHS header, written byte-for-byte.

Field order and types are transcribed from Intan's *RHS Application Note: Data
File Formats* (7 July 2017, updated 29 April 2022), pages 2-4, and were
cross-checked field-by-field against neo's ``rhs_global_header``,
``rhs_signal_group_header`` and ``rhs_signal_channel_header`` tables. The two
agree exactly, which is what makes validating the output with neo a real check
rather than a circular one.

Everything is little-endian. The document's ``single`` is float32.
"""

from __future__ import annotations

import struct

NULL_QSTRING_LENGTH = 0xFFFFFFFF


def qstring(text: str | None) -> bytes:
    """Qt-style length-prefixed Unicode string.

    A uint32 byte length followed by UTF-16 characters. ``None`` encodes the
    null sentinel 0xFFFFFFFF; an empty string encodes a zero length, and the
    two are distinct on the wire even though neo maps both to "".

    The length is in BYTES. The vendor document's MATLAB divides it by two to
    recover the character count, so writing a character count would truncate
    every string in half on read. Encoded UTF-16-LE with no byte-order mark,
    because neo decodes with the ``utf-16`` codec, which would consume a BOM as
    content-affecting metadata.
    """
    if text is None:
        return struct.pack("<I", NULL_QSTRING_LENGTH)
    payload = text.encode("utf-16-le")
    return struct.pack("<I", len(payload)) + payload
