"""Intan RHS stimulation words.

Bit layout, spec section 6.3, verified against Intan's RHS Data File Formats
application note:

    bits 0-7    current magnitude, scaled by the header's stim step size
    bit  8      sign, 1 meaning negative current
    bits 9-12   unused, always zero
    bit  13     amplifier settle
    bit  14     charge recovery
    bit  15     compliance limit

**Intan numbers bits from 1.** Its document says "Bit 16 (the MSB) indicates a
compliance limit… Bit 15 is one if charge recovery… Bit 14 is one if amplifier
settle" — those are bits 15, 14 and 13 zero-based. Transcribing the document
literally shifts every flag one position and keys artifact blanking to charge
recovery instead of amplifier settle, which fails silently: the sort still runs,
on the wrong windows.
"""

from __future__ import annotations

from dataclasses import dataclass

MAGNITUDE_MASK = 0x00FF
SIGN_BIT = 0x0100
UNUSED_MASK = 0x1E00
AMP_SETTLE_BIT = 0x2000
CHARGE_RECOVERY_BIT = 0x4000
COMPLIANCE_BIT = 0x8000


@dataclass(frozen=True, slots=True)
class StimWord:
    magnitude: int
    negative: bool
    amp_settle: bool
    charge_recovery: bool
    compliance: bool


def pack_stim_word(
    magnitude: int,
    negative: bool = False,
    amp_settle: bool = False,
    charge_recovery: bool = False,
    compliance: bool = False,
) -> int:
    if not 0 <= magnitude <= MAGNITUDE_MASK:
        raise ValueError(f"stim magnitude out of 8-bit range: {magnitude}")
    word = magnitude
    if negative:
        word |= SIGN_BIT
    if amp_settle:
        word |= AMP_SETTLE_BIT
    if charge_recovery:
        word |= CHARGE_RECOVERY_BIT
    if compliance:
        word |= COMPLIANCE_BIT
    return word


def unpack_stim_word(word: int) -> StimWord:
    return StimWord(
        magnitude=word & MAGNITUDE_MASK,
        negative=bool(word & SIGN_BIT),
        amp_settle=bool(word & AMP_SETTLE_BIT),
        charge_recovery=bool(word & CHARGE_RECOVERY_BIT),
        compliance=bool(word & COMPLIANCE_BIT),
    )
