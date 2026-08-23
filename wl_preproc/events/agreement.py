# wl_preproc/events/agreement.py
"""The three inputs section 4.7's tiers turn on, and the verdict.

`TimingProvenance` in 1c-4 recorded `tier = 'pending'` and named exactly what
it was waiting for: `event_code_agreement,trial_count_agreement,
camera_trigger_count`. This module supplies them.

**The tier is derived, never asserted** (section 4.7): every underlying count
is retained on the row so the verdict can be re-derived under different
thresholds later. `resolve_tier` therefore takes only the measured inputs and
holds no state of its own.
"""

from __future__ import annotations

from dataclasses import dataclass

# Two independent records must agree on this fraction of their codes to count
# as agreeing at all. Stated here rather than inlined so the threshold is one
# named number a later session can move without hunting -- section 4.7's whole
# point about re-derivation.
AGREEMENT_THRESHOLD = 0.999


@dataclass(frozen=True, slots=True)
class TierInputs:
    event_code_agreement: float | None
    trial_count_agreement: bool | None
    camera_trigger_count: int | None
    n_full_code_records: int
    n_strobe_witnesses: int
    decode_errors: int


def resolve_tier(inputs: TierInputs) -> str:
    """A, B, C or D, per spec section 4.7's table.

    **D is checked first and wins outright.** Section 4.7 defines D as "any
    check failed", so a failure is not a demotion to the next tier down -- two
    records that disagree is a failed check, not a session with one good
    record, and treating it as B would silently prefer whichever record was
    read first.
    """
    if inputs.decode_errors:
        return "D"
    if inputs.trial_count_agreement is False:
        return "D"
    if inputs.event_code_agreement is not None and (
        inputs.event_code_agreement < AGREEMENT_THRESHOLD
    ):
        return "D"

    if inputs.n_full_code_records >= 2:
        return "A"
    if inputs.n_full_code_records == 1 and inputs.n_strobe_witnesses >= 1:
        return "B"
    if inputs.n_full_code_records == 1:
        return "C"
    return "D"
