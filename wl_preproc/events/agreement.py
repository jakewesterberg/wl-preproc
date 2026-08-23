# wl_preproc/events/agreement.py
"""The three inputs section 4.7's tiers turn on, and the verdict.

`TimingProvenance` in 1c-4 recorded `tier = 'pending'` and named exactly what
it was waiting for: `event_code_agreement,trial_count_agreement,
camera_trigger_count`. This module supplies them.

**The tier is derived, never asserted** (section 4.7): every underlying count
is retained on the row so the verdict can be re-derived under different
thresholds later. `resolve_tier` therefore takes only the measured inputs and
holds no state of its own.

**`block_agreement` is a fourth, later addition -- not one of "the three".**
Section 5 makes a disagreement between the measured block boundary
(`trial.Block`) and wl.works' own assertion (`core.Block`) its own tier-D
condition, distinct from section 7's three. 1c-5 Task 9 found `TierInputs` had
no field for it -- a gap between the design spec and the plan that built this
module -- and closed it here, following the exact precedent
`trial_count_agreement` already set for "nothing to compare against" rather
than inventing a second convention.
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
    # Design spec section 5: "A disagreement between `trial.Block` (measured)
    # and `core.Block` (asserted) is a tier-D condition, not a silent
    # reconciliation." Added in 1c-5 Task 9 -- the original TierInputs had no
    # field for this, so the condition the spec names could not be expressed
    # at all. `None` when there is nothing to compare against (wl.works
    # asserted no blocks for this session), exactly the same convention
    # `trial_count_agreement` uses for "no task file at all": a tier is a
    # published quality claim, and `None` must never be silently treated as
    # agreement any more than an unmeasured trial count is.
    block_agreement: bool | None = None


def resolve_tier(inputs: TierInputs) -> str:
    """A, B, C or D, per spec section 4.7's table.

    **D is checked first and wins outright.** Section 4.7 defines D as "any
    check failed", so a failure is not a demotion to the next tier down -- two
    records that disagree is a failed check, not a session with one good
    record, and treating it as B would silently prefer whichever record was
    read first.

    **C requires the task-file cross-check to have actually succeeded --
    fix round 1.** `trial_count_agreement` is `None` when there was no task
    file to compare against at all: not a pass, not a failure, simply nothing
    measured. Section 4.7 defines C as "cross-checked ... against task file",
    so `None` must not earn C -- nothing corroborated the session, and a tier
    is a published quality claim, not a default one falls into. A session with
    one full-code record, no strobe witness, and no successful task-file check
    satisfies none of A, B or C. That is exactly D's job: the tiers have no
    fifth state for "never checked", and D is the quarantined tier that is not
    auto-published, which is the correct home for both "checked and failed"
    and "never checked at all".

    **`block_agreement is False` is a D condition too -- 1c-5 Task 9, spec
    section 5.** "A disagreement between `trial.Block` (measured) and
    `core.Block` (asserted) is a tier-D condition, not a silent
    reconciliation." `None` is treated exactly as `trial_count_agreement`'s
    `None` is: wl.works asserted no blocks to compare against, which is
    nothing measured, not an agreement -- so it must not gate anything here,
    for the identical reason an absent task file must not earn C.
    """
    if inputs.decode_errors:
        return "D"
    if inputs.trial_count_agreement is False:
        return "D"
    if inputs.block_agreement is False:
        return "D"
    if inputs.event_code_agreement is not None and (
        inputs.event_code_agreement < AGREEMENT_THRESHOLD
    ):
        return "D"

    if inputs.n_full_code_records >= 2:
        return "A"
    if inputs.n_full_code_records == 1 and inputs.n_strobe_witnesses >= 1:
        return "B"
    if inputs.n_full_code_records == 1 and inputs.trial_count_agreement is True:
        return "C"
    return "D"
