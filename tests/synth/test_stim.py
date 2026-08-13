import pytest

from wl_preproc.synth.stim import (
    AMP_SETTLE_BIT,
    CHARGE_RECOVERY_BIT,
    COMPLIANCE_BIT,
    MAGNITUDE_MASK,
    SIGN_BIT,
    UNUSED_MASK,
    pack_stim_word,
    unpack_stim_word,
)


def test_magnitude_round_trips():
    assert unpack_stim_word(pack_stim_word(200)).magnitude == 200


def test_sign_is_separate_from_magnitude():
    word = unpack_stim_word(pack_stim_word(200, negative=True))
    assert word.magnitude == 200
    assert word.negative is True


def test_amp_settle_is_bit_13_zero_based():
    """Intan numbers bits from 1: its "bit 14" is bit 13 zero-based. Reading
    the document literally keys artifact blanking to charge recovery instead."""
    assert AMP_SETTLE_BIT == 1 << 13
    word = pack_stim_word(0, amp_settle=True)
    assert word == 0x2000
    assert unpack_stim_word(word).amp_settle is True
    assert unpack_stim_word(word).charge_recovery is False
    assert unpack_stim_word(word).compliance is False


def test_charge_recovery_is_bit_14_zero_based():
    assert CHARGE_RECOVERY_BIT == 1 << 14
    word = pack_stim_word(0, charge_recovery=True)
    assert unpack_stim_word(word).charge_recovery is True
    assert unpack_stim_word(word).amp_settle is False


def test_compliance_is_the_msb():
    assert COMPLIANCE_BIT == 1 << 15
    word = pack_stim_word(0, compliance=True)
    assert unpack_stim_word(word).compliance is True
    assert unpack_stim_word(word).charge_recovery is False


def test_flags_are_independent():
    word = pack_stim_word(37, negative=True, amp_settle=True, compliance=True)
    unpacked = unpack_stim_word(word)
    assert unpacked.magnitude == 37
    assert unpacked.negative is True
    assert unpacked.amp_settle is True
    assert unpacked.charge_recovery is False
    assert unpacked.compliance is True


def test_unused_bits_are_never_set():
    """Bits 9-12 zero-based are documented as always zero. A packer that leaks
    into them would be writing a word no real device produces."""
    for magnitude in (0, 1, 127, 255):
        for flags in range(8):
            word = pack_stim_word(
                magnitude,
                amp_settle=bool(flags & 1),
                charge_recovery=bool(flags & 2),
                compliance=bool(flags & 4),
            )
            assert word & UNUSED_MASK == 0


def test_the_bit_regions_tile_the_word():
    """Every bit of the 16 belongs to exactly one named region. Stated as a sum
    because the layout was transcribed from a document that numbers bits from
    1: an off-by-one in any single mask leaves a gap or an overlap here, rather
    than silently keying a flag to its neighbour."""
    regions = (
        MAGNITUDE_MASK,
        SIGN_BIT,
        UNUSED_MASK,
        AMP_SETTLE_BIT,
        CHARGE_RECOVERY_BIT,
        COMPLIANCE_BIT,
    )
    assert sum(regions) == 0xFFFF


def test_magnitude_out_of_range_rejected():
    with pytest.raises(ValueError):
        pack_stim_word(256)
