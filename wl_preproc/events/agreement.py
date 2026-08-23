# wl_preproc/events/agreement.py
"""The three inputs parent spec section 4.7's tiers turn on, and the verdict.

`TimingProvenance` in 1c-4 recorded `tier = 'pending'` and named exactly what
it was waiting for: `event_code_agreement,trial_count_agreement,
camera_trigger_count`. This module supplies them.

**The tier is derived, never asserted** (parent spec section 4.7): every
underlying count is retained on the row so the verdict can be re-derived
under different thresholds later. `resolve_tier` therefore takes only the
measured inputs and holds no state of its own.

**`block_agreement` is a fourth, later addition -- not one of "the three".**
Design spec section 5 (the 1c-5 phase design, not the parent) makes a
disagreement between the measured block boundary (`trial.Block`) and
wl.works' own assertion (`core.Block`) its own tier-D condition, distinct
from design spec section 7's three. 1c-5 Task 9 found `TierInputs` had no
field for it -- a gap between the design spec and the plan that built this
module -- and closed it here, following the exact precedent
`trial_count_agreement` already set for "nothing to compare against" rather
than inventing a second convention.

**`code_agreement` and `block_agreement_tolerance_s`, fix round 2.** Two more
pure functions, added after review: how two independent full-code records
are compared (content-matched, tolerant of a dropped or inserted word --
design spec section 4.2 requirement 1's own property, applied one level up
from the codec), and how close a measured block boundary must land to
wl.works' own assertion to count as agreeing (derived from float32 storage
precision, not chosen). Both are called from `schema/timebase.py::
TimingProvenance.make()`, which supplies the raw decoded code lists and
boundary values respectively; neither touches DataJoint or a file, matching
`resolve_tier`'s own pure, stateless shape.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

import numpy as np

# Two independent records must agree on this fraction of their codes to count
# as agreeing at all. Stated here rather than inlined so the threshold is one
# named number a later session can move without hunting -- parent spec
# section 4.7's whole point about re-derivation.
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
    # asserted no blocks for this session): neither field's `None` is ever
    # read as agreement, the one principle genuinely shared with
    # `trial_count_agreement`'s own `None` -- see `resolve_tier`'s docstring
    # for where the two fields' EFFECTS actually diverge, which a fix round 2
    # correction found this comment had previously overstated.
    block_agreement: bool | None = None


def resolve_tier(inputs: TierInputs) -> str:
    """A, B, C or D, per parent spec section 4.7's table.

    **D is checked first and wins outright.** Parent spec section 4.7
    defines D as "any check failed", so a failure is not a demotion to the
    next tier down -- two records that disagree is a failed check, not a
    session with one good record, and treating it as B would silently
    prefer whichever record was read first.

    **C requires the task-file cross-check to have actually succeeded --
    fix round 1.** `trial_count_agreement` is `None` when there was no task
    file to compare against at all: not a pass, not a failure, simply nothing
    measured. Parent spec section 4.7 defines C as "cross-checked ... against
    task file", so `None` must not earn C -- nothing corroborated the
    session, and a tier is a published quality claim, not a default one
    falls into. A session with one full-code record, no strobe witness, and
    no successful task-file check satisfies none of A, B or C. That is
    exactly D's job: the tiers have no fifth state for "never checked", and D
    is the quarantined tier that is not auto-published, which is the correct
    home for both "checked and failed" and "never checked at all".

    **`block_agreement is False` is a D condition too -- 1c-5 Task 9, design
    spec section 5.** "A disagreement between `trial.Block` (measured) and
    `core.Block` (asserted) is a tier-D condition, not a silent
    reconciliation."

    **`block_agreement is None` is NOT treated exactly like
    `trial_count_agreement is None` -- fix round 2 correction.** A commit
    message once said it was; it wasn't, and review caught the sentence
    rather than the code, which was already right.
    `trial_count_agreement` has TWO jobs here: it forces D when `False`
    (above), and it is ALSO a precondition the C branch below requires to be
    `True` specifically -- so its `None` fails both the D-check and the
    C-requirement, and the session falls through to D by elimination.
    `block_agreement` has only the first job: nothing below ever requires it
    to be `True` to reach any tier. So its `None` is simply inert here -- it
    fails to trigger THIS one D-check and affects nothing else -- not
    "blocked from C the same way `trial_count_agreement`'s is". What the two
    fields genuinely share is narrower than "identical treatment": neither
    field's `None` is ever read as agreement, but only `trial_count_agreement`
    is also a precondition a tier can be earned by satisfying. Block-boundary
    corroboration is a check this protocol can FAIL, not one it can PASS.
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


def code_agreement(reference: Sequence[int], other: Sequence[int]) -> float:
    """Fraction of codes two independent full-code records agree on,
    content-matched rather than position-matched -- fix round 2, caught by
    review.

    **Position-matching (`zip`) reintroduces the exact hazard design spec
    section 4.2 requirement 1 exists to prevent, one layer up.** That
    requirement -- quoted directly in this repo's own `tests/events/
    test_assemble.py` -- is "one dropped code must not shift every
    subsequent trial", the entire reason a trial-number payload is
    transmitted explicitly rather than left to a running count. Comparing
    two independently decoded streams by ordinal position has the identical
    shape of bug: one code dropped at the head of EITHER stream misaligns
    every position after it, driving the computed agreement toward zero --
    and a genuinely agreeing, tier-A session would read as D. Confirmed
    against the position-matched implementation this replaces:
    `tests/events/test_agreement.py::
    test_code_agreement_tolerates_a_dropped_word_at_the_head` fails against
    it.

    `difflib.SequenceMatcher`, tolerant of insertions and deletions in
    either sequence -- unequal extents are already first-class elsewhere in
    this pipeline (`partial` coverage, `Fault.STOP_MID_TRIAL`,
    `Fault.MID_SESSION_RESTART`), not a hazard unique to this comparison.

    **`autojunk=False` is required, not incidental.** `SequenceMatcher`'s
    default autojunk heuristic treats any element occupying more than 1% of
    a sequence of length >= 200 as "popular" and excludes it from matching --
    and code words repeat heavily over a real session (the same handful of
    marker and escape values, over and over, for as long as the session
    runs). Left at its default, autojunk would silently discard most genuine
    matches on exactly the long sessions this metric exists for.
    """
    if not reference and not other:
        return 1.0
    matcher = SequenceMatcher(None, reference, other, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / max(len(reference), len(other))


# Half a float32 ULP is the exact bound a double-to-float32 storage
# round-trip can introduce -- element-event's own `trial.Block` declares
# `block_start_time`/`block_stop_time` as `float` (single precision), while
# `core.Block`'s `start_s`/`end_s` are `double` (confirmed directly against
# `element_event/trial.py`). k=2 covers the comparison itself, not the
# block's two endpoints (start_s and end_s each get their own call to
# `block_agreement_tolerance_s`, below): the measured value can be off by up
# to one half-ULP in either direction from rounding, and so, in the general
# case, can whatever it is compared against, so the pair's worst-case
# difference is two half-ULPs, not one.
BLOCK_AGREEMENT_TOLERANCE_K = 2

# Beneath this, float32's own ULP is far finer than anything this pipeline
# measures. Matches `timebase/coverage.py`'s own `_FULL_TOLERANCE_S`, chosen
# there for float64 accumulation error at a comparable order of magnitude --
# not the same justification (that one is about summed subtraction error;
# this one is a floor under a magnitude-scaled term), but the same
# reasoning about what "far finer than anything measured" means numerically
# in this codebase.
BLOCK_AGREEMENT_TOLERANCE_FLOOR_S = 1e-6


def _float32_half_ulp(value: float) -> float:
    """Half the gap between adjacent float32 values at `value`'s own
    magnitude.

    `numpy.spacing` on a float32 input, not `numpy.finfo(numpy.float32).eps
    * value`: `eps` is the ULP AT 1.0 specifically, and multiplying it by an
    arbitrary value reproduces that value's true ULP only at a power of two
    -- elsewhere in the binade it underestimates by up to 2x, silently
    licensing too tight a tolerance. `spacing` steps to the correct binade
    for any input and is exact there, which is what a bound needs to be.
    """
    return float(np.spacing(np.float32(value))) / 2.0


def block_agreement_tolerance_s(*magnitudes: float) -> float:
    """How close a measured block boundary must land to wl.works' own
    assertion, at these magnitudes, to count as agreeing -- derived from
    float32 storage precision, not chosen. Fix round 2: a prior fixed
    `1e-3` cited `timebase/segments.py`'s alignment durations as precedent
    for "chosen rather than derived" -- wrong, since that module derives its
    own numbers explicitly ("consequences of the decoder"), so a derived
    number was cited to license an underived one.

    Design spec section 5 makes a disagreement here its own tier-D
    condition. A FIXED tolerance cannot be right at every magnitude a
    session might reach: `pipeline.trial.Block`'s own columns are float32
    (see `BLOCK_AGREEMENT_TOLERANCE_K`'s comment), so the MEASURED side of
    this comparison always carries up to one float32 half-ULP of pure
    storage rounding, and that half-ULP DOUBLES every time the magnitude
    doubles -- 0.977 ms at 16384 s (4.5h into a session), 1.953 ms at
    32768 s (9.1h). A fixed 1 ms tolerance already consumes nearly its
    entire budget on storage rounding alone at 4.5h and is provably too
    tight past 9.1h: a false tier-D quarantine on an honestly agreeing long
    session, for a reason that has nothing to do with whether the blocks
    actually agree -- exactly the false-verdict shape section 4.7's whole
    apparatus exists to avoid, reintroduced by an underived constant.

    Callers pass every value entering ONE boundary comparison (typically the
    measured time and the asserted time for a single endpoint), so the
    returned tolerance is scaled to what is actually being compared, never to
    the session's total duration or some other quantity nothing here checks.
    """
    return max(
        BLOCK_AGREEMENT_TOLERANCE_FLOOR_S,
        BLOCK_AGREEMENT_TOLERANCE_K * max(_float32_half_ulp(m) for m in magnitudes),
    )
