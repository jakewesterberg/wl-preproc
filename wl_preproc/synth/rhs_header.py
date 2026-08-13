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
from pathlib import Path

from wl_preproc.synth.recipe import ChannelSpec, SessionRecipe

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


MAGIC = 0xD69127AC
MAJOR_VERSION = 3
MINOR_VERSION = 0

SIGNAL_TYPE_AMPLIFIER = 0
SIGNAL_TYPE_DIGITAL_IN = 5

BOARD_MODE_STIM_RECORD = 14  # vendor document: always 14 for the Stim/Record Controller
CHANNELS_PER_CHIP = 16       # one RHS2116 chip, and one USB data stream, per 16 channels

# The vendor document says a file typically carries eight signal groups. The six
# we do not populate are still declared, with enabled = 0 and no channels, so the
# structure matches a real file without claiming data we never wrote.
GROUP_NAMES: tuple[tuple[str, str], ...] = (
    ("Port A", "A"),
    ("Port B", "B"),
    ("Port C", "C"),
    ("Port D", "D"),
    ("Board ADC Inputs", "ADC"),
    ("Board Digital Inputs", "DIN"),
    ("Board DAC Outputs", "DAC"),
    ("Board Digital Outputs", "DOUT"),
)

# Filter and impedance settings. These describe a plausible recording rather than
# a measured one; nothing downstream reads them, but a reader expects the fields
# and a zero everywhere would look like a malformed file.
_DSP_ENABLED = 1
_DSP_CUTOFF_HZ = 1.0
_LOWER_BANDWIDTH_HZ = 0.1
_LOWER_SETTLE_BANDWIDTH_HZ = 1000.0
_UPPER_BANDWIDTH_HZ = 7500.0
_NOTCH_MODE_DISABLED = 0
_IMPEDANCE_TEST_HZ = 1000.0
_AMP_SETTLE_MODE_SWITCH_LOWER_BANDWIDTH = 0
_CHARGE_RECOVERY_MODE_CURRENT_LIMITED = 0
_RECOVERY_CURRENT_LIMIT_A = 1e-6
_RECOVERY_TARGET_VOLTAGE_V = 0.0


def _global_block(recipe: SessionRecipe, sample_rate_hz: float, stim_step_size_a: float) -> bytes:
    """The 28 fields of the Standard Intan RHS Header, in document order.

    Order is load-bearing: this is a sequential binary read with no field tags,
    so a single misplaced or mistyped field desynchronises everything after it
    and the reader fails somewhere unrelated to the actual mistake.
    """
    out = bytearray()
    out += struct.pack("<Ihh", MAGIC, MAJOR_VERSION, MINOR_VERSION)
    out += struct.pack("<f", sample_rate_hz)
    out += struct.pack("<h", _DSP_ENABLED)
    out += struct.pack(
        "<ffff",
        _DSP_CUTOFF_HZ,
        _LOWER_BANDWIDTH_HZ,
        _LOWER_SETTLE_BANDWIDTH_HZ,
        _UPPER_BANDWIDTH_HZ,
    )
    out += struct.pack(
        "<ffff",
        _DSP_CUTOFF_HZ,
        _LOWER_BANDWIDTH_HZ,
        _LOWER_SETTLE_BANDWIDTH_HZ,
        _UPPER_BANDWIDTH_HZ,
    )
    out += struct.pack("<h", _NOTCH_MODE_DISABLED)
    out += struct.pack("<ff", _IMPEDANCE_TEST_HZ, _IMPEDANCE_TEST_HZ)
    out += struct.pack("<h", _AMP_SETTLE_MODE_SWITCH_LOWER_BANDWIDTH)
    out += struct.pack("<h", _CHARGE_RECOVERY_MODE_CURRENT_LIMITED)
    out += struct.pack(
        "<fff", stim_step_size_a, _RECOVERY_CURRENT_LIMIT_A, _RECOVERY_TARGET_VOLTAGE_V
    )
    out += qstring(f"synthetic session {recipe.session_id} (wl-preproc)")
    out += qstring("")
    out += qstring("")
    # 0 declares that dcamplifier.dat is absent. Spec section 6.3 leaves that file
    # unwritten because the vendor document contradicts itself on its dtype; this
    # field is the format's own way of saying so, rather than leaving it implied.
    out += struct.pack("<h", 0)
    out += struct.pack("<h", BOARD_MODE_STIM_RECORD)
    out += qstring("n/a")  # hardware referencing, per the vendor document
    out += struct.pack("<h", len(GROUP_NAMES))
    return bytes(out)


def _channel_record(
    channel: ChannelSpec, index: int, signal_type: int, chip_channel: int
) -> bytes:
    """The 15 per-channel fields, in document order."""
    out = bytearray()
    out += qstring(channel.name)
    out += qstring(channel.name)
    out += struct.pack("<hh", index, index)          # native and custom order
    out += struct.pack("<h", signal_type)
    out += struct.pack("<h", 1 if channel.enabled else 0)
    out += struct.pack("<h", chip_channel)
    stream = index // CHANNELS_PER_CHIP
    out += struct.pack("<hh", stream, stream)        # command stream, board stream
    out += struct.pack("<hhhh", 0, 0, 0, 1)          # spike-scope defaults
    out += struct.pack(
        "<ff", channel.impedance_ohms, channel.impedance_phase_deg
    )
    return bytes(out)


def write_rhs_header(
    path: Path,
    recipe: SessionRecipe,
    sample_rate_hz: float,
    stim_step_size_a: float,
    digital_input_bits: tuple[int, ...],
) -> None:
    """Write a byte-correct Standard Intan RHS header to ``path``.

    ``digital_input_bits`` are the bit positions used within each ``digitalin.dat``
    word — the barcode and the strobe. A reader needs one declared channel per bit
    to slice them back out, and the channel's native order carries the bit index.

    Only amplifier and digital-input channels are declared, because a reader turns
    each declared signal type into an expected .dat filename and this generator
    writes only ``amplifier.dat`` and ``digitalin.dat``.
    """
    amplifier_channels = recipe.resolved_channels()
    digital_channels = tuple(
        ChannelSpec(name=f"DIN-{bit:02d}") for bit in digital_input_bits
    )

    out = bytearray(_global_block(recipe, sample_rate_hz, stim_step_size_a))

    for name, prefix in GROUP_NAMES:
        if name == "Port A":
            channels, signal_type = amplifier_channels, SIGNAL_TYPE_AMPLIFIER
        elif name == "Board Digital Inputs":
            channels, signal_type = digital_channels, SIGNAL_TYPE_DIGITAL_IN
        else:
            channels, signal_type = (), SIGNAL_TYPE_AMPLIFIER

        out += qstring(name)
        out += qstring(prefix)
        out += struct.pack("<h", 1 if channels else 0)
        out += struct.pack("<h", len(channels))
        out += struct.pack(
            "<h", len(channels) if signal_type == SIGNAL_TYPE_AMPLIFIER else 0
        )

        for index, channel in enumerate(channels):
            chip_channel = (
                digital_input_bits[index]
                if signal_type == SIGNAL_TYPE_DIGITAL_IN
                else index % CHANNELS_PER_CHIP
            )
            order = (
                digital_input_bits[index]
                if signal_type == SIGNAL_TYPE_DIGITAL_IN
                else index
            )
            out += _channel_record(channel, order, signal_type, chip_channel)

    path.write_bytes(bytes(out))
