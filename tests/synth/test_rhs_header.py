import struct

import pytest

from wl_preproc.synth.recipe import STIM_RECIPE, ChannelSpec
from wl_preproc.synth.rhs_header import (
    BOARD_MODE_STIM_RECORD,
    MAGIC,
    MAJOR_VERSION,
    MINOR_VERSION,
    NULL_QSTRING_LENGTH,
    SIGNAL_TYPE_AMPLIFIER,
    SIGNAL_TYPE_DIGITAL_IN,
    qstring,
    write_rhs_header,
)


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


@pytest.fixture
def header_bytes(tmp_path):
    path = tmp_path / "info.rhs"
    write_rhs_header(
        path,
        STIM_RECIPE,
        sample_rate_hz=30_000.0,
        stim_step_size_a=10e-6,
        digital_input_bits=(0, 1),
    )
    return path.read_bytes()


def test_begins_with_the_magic_number_and_version(header_bytes):
    """Exact, not a lower bound. The version pair is a choice this module makes
    rather than a value the vendor document supplies, and a reader branches on
    it — neo labels the notch filter differently at major_version >= 3 — so a
    version that merely sorts above (1, 0) is not the same as the one we wrote."""
    magic, major, minor = struct.unpack("<Ihh", header_bytes[:8])
    assert magic == MAGIC
    assert (major, minor) == (MAJOR_VERSION, MINOR_VERSION)


def test_sample_rate_is_float32_at_offset_eight(header_bytes):
    """The vendor document puts sample rate immediately after the version pair.
    Reading it as float64 here would shift every subsequent field."""
    assert struct.unpack("<f", header_bytes[8:12])[0] == pytest.approx(30_000.0)


def test_neo_parses_the_whole_header_without_running_off_the_end(header_bytes, tmp_path):
    """Parse with neo's own field tables. This is the check that the field order
    and types match the reader exactly — a wrong dtype anywhere desynchronises
    everything after it, and the group count comes out absurd."""
    from neo.rawio.intanrawio import (
        read_variable_header,
        rhs_global_header,
        rhs_signal_channel_header,
        rhs_signal_group_header,
    )

    # neo's read_variable_header reads every non-QString field with np.fromfile,
    # which requires a real file descriptor: io.BytesIO raises
    # "UnsupportedOperation: fileno" no matter what bytes it holds. Round-trip
    # header_bytes through disk so neo is reading a real file, as it would in
    # production.
    replay_path = tmp_path / "replay.rhs"
    replay_path.write_bytes(header_bytes)
    with open(replay_path, "rb") as stream:
        info = read_variable_header(stream, rhs_global_header)
        assert info["magic_number"] == MAGIC
        assert info["nb_signal_group"] == 8
        assert info["board_mode"] == BOARD_MODE_STIM_RECORD
        assert info["dc_amplifier_data_saved"] == 0
        assert info["stim_step_size"] == pytest.approx(10e-6)

        seen_types = []
        for _ in range(int(info["nb_signal_group"])):
            group = read_variable_header(stream, rhs_signal_group_header)
            if group["signal_group_enabled"] and group["channel_num"] > 0:
                for _ in range(int(group["channel_num"])):
                    channel = read_variable_header(stream, rhs_signal_channel_header)
                    seen_types.append(int(channel["signal_type"]))

        assert stream.read() == b"", "trailing bytes: the header is longer than parsed"
    assert seen_types.count(SIGNAL_TYPE_AMPLIFIER) == STIM_RECIPE.n_ap_channels
    assert seen_types.count(SIGNAL_TYPE_DIGITAL_IN) == 2


def test_only_signal_types_we_write_files_for_are_declared(header_bytes, tmp_path):
    """neo maps a declared signal type to an expected .dat filename. Declaring a
    board ADC input makes it look for analogin.dat, which is never written."""
    from neo.rawio.intanrawio import (
        read_variable_header,
        rhs_global_header,
        rhs_signal_channel_header,
        rhs_signal_group_header,
    )

    # See test_neo_parses_the_whole_header_without_running_off_the_end: neo
    # needs a real file descriptor, which io.BytesIO cannot provide.
    replay_path = tmp_path / "replay.rhs"
    replay_path.write_bytes(header_bytes)
    seen_types = []
    with open(replay_path, "rb") as stream:
        info = read_variable_header(stream, rhs_global_header)
        for _ in range(int(info["nb_signal_group"])):
            group = read_variable_header(stream, rhs_signal_group_header)
            if group["signal_group_enabled"] and group["channel_num"] > 0:
                for _ in range(int(group["channel_num"])):
                    channel = read_variable_header(stream, rhs_signal_channel_header)
                    seen_types.append(int(channel["signal_type"]))

    # Collected rather than asserted in the loop: a header that declared no
    # channels at all — or one whose groups all read as disabled because a field
    # ahead of them desynchronised — would satisfy an in-loop assertion by never
    # reaching it, which is the one outcome this test must not call a pass.
    assert seen_types, "no channel records were parsed; the assertion never ran"
    assert set(seen_types) <= {SIGNAL_TYPE_AMPLIFIER, SIGNAL_TYPE_DIGITAL_IN}


def test_channel_names_come_from_the_recipe(tmp_path):
    from neo.rawio.intanrawio import (
        read_variable_header,
        rhs_global_header,
        rhs_signal_channel_header,
        rhs_signal_group_header,
    )

    from wl_preproc.synth.recipe import ChannelSpec

    named = tuple(
        ChannelSpec(name=f"C-{i:03d}", impedance_ohms=3.0e6)
        for i in range(STIM_RECIPE.n_ap_channels)
    )
    recipe = STIM_RECIPE.model_copy(update={"channels": named})
    path = tmp_path / "info.rhs"
    write_rhs_header(
        path, recipe, sample_rate_hz=30_000.0, stim_step_size_a=10e-6,
        digital_input_bits=(0, 1),
    )

    # neo's read_variable_header needs a real file descriptor (np.fromfile), not
    # an in-memory io.BytesIO — see
    # test_neo_parses_the_whole_header_without_running_off_the_end.
    found = []
    with open(path, "rb") as stream:
        info = read_variable_header(stream, rhs_global_header)
        for _ in range(int(info["nb_signal_group"])):
            group = read_variable_header(stream, rhs_signal_group_header)
            if group["signal_group_enabled"] and group["channel_num"] > 0:
                for _ in range(int(group["channel_num"])):
                    channel = read_variable_header(stream, rhs_signal_channel_header)
                    if int(channel["signal_type"]) == SIGNAL_TYPE_AMPLIFIER:
                        found.append(
                            (
                                channel["native_channel_name"],
                                float(channel["electrode_impedance_magnitude"]),
                            )
                        )

    assert [n for n, _ in found] == [c.name for c in named]
    assert all(z == pytest.approx(3.0e6) for _, z in found)


def test_digital_channels_are_named_for_their_bit_positions(tmp_path):
    """digitalin.dat packs all 16 inputs per word; a reader needs the bit index
    to slice the barcode out of bit 0 and the strobe out of bit 1."""
    from neo.rawio.intanrawio import (
        read_variable_header,
        rhs_global_header,
        rhs_signal_channel_header,
        rhs_signal_group_header,
    )

    path = tmp_path / "info.rhs"
    write_rhs_header(
        path, STIM_RECIPE, sample_rate_hz=30_000.0, stim_step_size_a=10e-6,
        digital_input_bits=(0, 1),
    )
    # See test_neo_parses_the_whole_header_without_running_off_the_end: neo
    # needs a real file descriptor, not an in-memory io.BytesIO.
    digital = []
    with open(path, "rb") as stream:
        info = read_variable_header(stream, rhs_global_header)
        for _ in range(int(info["nb_signal_group"])):
            group = read_variable_header(stream, rhs_signal_group_header)
            if group["signal_group_enabled"] and group["channel_num"] > 0:
                for _ in range(int(group["channel_num"])):
                    channel = read_variable_header(stream, rhs_signal_channel_header)
                    if int(channel["signal_type"]) == SIGNAL_TYPE_DIGITAL_IN:
                        digital.append(
                            (
                                channel["native_channel_name"],
                                int(channel["native_order"]),
                                float(channel["electrode_impedance_magnitude"]),
                            )
                        )

    # Impedance zero, not ChannelSpec's 1 MΩ amplifier default: there is no
    # electrode on a TTL line, and a real .rhs writes zero on every non-amplifier
    # record. A 1 MΩ reading on a digital input is a measurement that cannot exist.
    assert digital == [("DIN-00", 0, 0.0), ("DIN-01", 1, 0.0)]


def test_a_model_copy_channel_count_mismatch_is_rejected(tmp_path):
    """`SessionRecipe._coherent` cannot catch this and `write_rhs_header` must.

    `model_copy(update=...)` does not re-run validators, and it is this branch's
    own idiom for building a variant recipe — so the field validator that guards
    the constructor is simply not in the path. Left unguarded, one declared
    channel against the 4-column amplifier.dat that `write_rhs` writes had
    `read_intan` report 1,494,000 samples instead of 373,500, with no error.
    """
    recipe = STIM_RECIPE.model_copy(update={"channels": (ChannelSpec(name="A-000"),)})
    with pytest.raises(ValueError) as exc:
        write_rhs_header(
            tmp_path / "info.rhs", recipe, sample_rate_hz=30_000.0,
            stim_step_size_a=10e-6, digital_input_bits=(0, 1),
        )
    assert "1 amplifier channels" in str(exc.value)
    assert "n_ap_channels=4" in str(exc.value)


def test_a_disabled_amplifier_channel_is_rejected(tmp_path):
    """Nothing in the recipe validates `enabled`, and a reader reshapes
    amplifier.dat by the *enabled* count rather than the declared one.

    One channel disabled on STIM_RECIPE declares 3 enabled against the 4 columns
    `write_rhs` writes, and `read_intan` then reported 498,000 samples instead of
    373,500 — interleaved garbage from the second sample on, silently. Sizing the
    array from the enabled set instead would be a design change; rejecting is the
    honest answer, since `write_rhs` writes every channel.
    """
    channels = (
        ChannelSpec(name="A-000"),
        ChannelSpec(name="A-001", enabled=False),
        ChannelSpec(name="A-002"),
        ChannelSpec(name="A-003"),
    )
    recipe = STIM_RECIPE.model_copy(update={"channels": channels})
    with pytest.raises(ValueError) as exc:
        write_rhs_header(
            tmp_path / "info.rhs", recipe, sample_rate_hz=30_000.0,
            stim_step_size_a=10e-6, digital_input_bits=(0, 1),
        )
    assert "enabled=False" in str(exc.value)
    assert "['A-001']" in str(exc.value)


def test_a_digital_input_bit_outside_the_word_is_rejected(tmp_path):
    """digitalin.dat is uint16, so a reader's `1 << 16` mask is zero at every
    sample: the channel reads as a flat line rather than failing."""
    with pytest.raises(ValueError) as exc:
        write_rhs_header(
            tmp_path / "info.rhs", STIM_RECIPE, sample_rate_hz=30_000.0,
            stim_step_size_a=10e-6, digital_input_bits=(0, 16),
        )
    assert "0..15" in str(exc.value)


def test_a_repeated_digital_input_bit_is_rejected(tmp_path):
    """Two records named DIN-01 collapse to one channel in a reader keyed on
    native_channel_name, so a bit silently loses its declaration."""
    with pytest.raises(ValueError) as exc:
        write_rhs_header(
            tmp_path / "info.rhs", STIM_RECIPE, sample_rate_hz=30_000.0,
            stim_step_size_a=10e-6, digital_input_bits=(1, 1),
        )
    assert "repeats [1]" in str(exc.value)


def test_header_is_deterministic(tmp_path):
    first, second = tmp_path / "a.rhs", tmp_path / "b.rhs"
    for path in (first, second):
        write_rhs_header(
            path, STIM_RECIPE, sample_rate_hz=30_000.0, stim_step_size_a=10e-6,
            digital_input_bits=(0, 1),
        )
    assert first.read_bytes() == second.read_bytes()
