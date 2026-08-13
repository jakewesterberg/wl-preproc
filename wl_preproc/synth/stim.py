"""Intan RHS stimulation: the wire format and the timing that shapes it.

Two things live here. The first is the stim word codec — `pack_stim_word`,
`unpack_stim_word` and the bit-region constants below — which is what
`write_rhs` emits into `stim.dat` and what a consumer reads back out.

The second is stimulation timing: `SETTLE_DURATION_S`, `STIM_PULSE_DURATION_S`,
`STIM_GUARD_S` and the `StimEvent` record that `build_timeline` plants and
`write_rhs` renders. These constants sit here rather than beside the other
timeline constants because `recipe.SessionRecipe` validates planting geometry
against them and `timeline` already imports `recipe`, so putting them in
`timeline` would make that import circular. This module imports nothing from
the package, which is what keeps it available to both.

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
# Bits 9-12 carry nothing, but naming them completes the map: the six regions
# sum to 0xFFFF, so any off-by-one introduced while transcribing a document
# that numbers bits from 1 shows up as a gap or an overlap. It is also the mask
# a reader tests to tell a garbled word from a real one — which is how
# test_stim.py's "unused bits are never set" invariant is stated.
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


SETTLE_DURATION_S = 0.002
STIM_PULSE_DURATION_S = 0.0005
STIM_GUARD_S = 0.05


@dataclass(frozen=True, slots=True)
class StimEvent:
    """One biphasic pulse. Duration covers the pulse itself; amplifier settle is
    asserted for SETTLE_DURATION_S afterwards, which is the window the pipeline
    blanks (spec section 6.3)."""

    onset_s: float
    duration_s: float
    channel: int
    magnitude: int
    negative: bool
