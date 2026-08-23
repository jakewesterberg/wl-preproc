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
    default autojunk heuristic applies to the SECOND sequence only (`b` --
    `other` here, never `reference`), and only once `len(b) >= 200`: an
    element of `b` appearing at more than `len(b) // 100 + 1` positions is
    marked "popular" and dropped from the match index. Read off
    `difflib.SequenceMatcher.__chain_b` directly, not paraphrased -- and the
    threshold is slightly ABOVE a flat 1% because of that `+ 1`. Code words
    repeat heavily over a real session (the same handful of marker and escape
    values, over and over, for as long as the session runs), so left at its
    default, autojunk would silently discard most genuine matches on exactly
    the long sessions this metric exists for.
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
# `block_agreement_tolerance_s`, below).
#
# **k=2 is conservative margin here, not a derived bound -- fix round 4
# correction.** Only ONE side of this comparison is stored as float32, so
# only one half-ULP is ever in play: `core.Block`'s `double` side carries no
# float32 rounding at all, by this comment's own premise two sentences up.
# The previous justification -- that "in the general case" the compared value
# can be off by a half-ULP too, so the pair's worst case is two of them --
# named a term it had already excluded. What k=2 actually buys is a full ULP
# of headroom over a term provably at most half of one: free against a
# genuine disagreement, which is orders of magnitude larger, and still
# correct if the asserted side is ever narrowed to float32 as well. Kept for
# that reason, and stated as margin rather than re-derived into a bound it
# is not.
BLOCK_AGREEMENT_TOLERANCE_K = 2

# Fix round 3 correction: this floor used to be `1e-6`, matched to
# `timebase/coverage.py`'s `_FULL_TOLERANCE_S` -- wrong, because that
# constant is a floor under float64 ACCUMULATION error, which is not a
# physical quantity, and this tolerance sits under a MEASUREMENT, which has
# a resolution. The resolution is the transport's: the shared strobe bus
# carries one code word at a time (`synth/timeline.py`'s own `_emit`:
# "words can never overlap ... if two logical events want the same instant,
# the second waits"), and a block boundary is transported as one specific
# code word in one specific slot -- so it cannot be measured any finer than
# the spacing between slots. That is the actual explanation for the 1 ms
# this project's very first fixed tolerance happened to cover: not
# "generous slack", but exactly one slot's worth of transport quantization,
# covered by accident rather than by argument.
#
# `MIN_CODE_WORD_SLOT_S` matches `synth/timeline.py`'s own
# `CODE_WORD_SPACING_S` (0.001) -- restated, not imported: `wl_preproc.
# events` is production code (this value feeds `TimingProvenance.make()` on
# every session, real or synthetic), and `wl_preproc.synth` is fixture
# generation only, so importing one from the other would run the
# architecture backwards in whichever direction it went. The synthetic
# generator is this project's only behavioural-stack implementation today
# (`events/taskfile.py`'s own "one implementation" framing for the same
# reason), so its transport timing is the only measured value there currently
# is to derive this from; update it to match a real system's own slot
# spacing once one is chosen, the same way `SyntheticTaskFileReader` gets a
# sibling implementation rather than a replacement then.
MIN_CODE_WORD_SLOT_S = 0.001

# How many slots the MEASURED block boundary can sit from the NOMINAL one --
# traced exhaustively through every transition `build_timeline` produces,
# not merely observed once. A block's own `BLOCK_START` escape word (whose
# time IS `AssembledBlock.start_s`) is ratcheted exactly one slot past its
# nominal instant: by `SESSION_START` for the very first block, or by the
# PREVIOUS block's own `BLOCK_END` for every block after it -- and both
# resolve to the identical one-slot displacement, by construction of
# `_emit`'s ratchet (`earliest = words[-1][0] + CODE_WORD_SPACING_S`), for
# any block with at least one trial.
#
# **A zero-trial block is outside this bound, and the refusal this comment
# claimed did not exist -- fix round 4.** The claim was that such a block
# "has zero duration and is refused elsewhere, by `classify_coverage`, before
# this comparison is ever reached". Both halves were false:
# `classify_coverage` is called only on `core.Block` and `trial.Trial`
# (`schema/coverage.py`), never on the measured `pipeline.trial.Block` this
# constant governs; and `daemon.run_once` populates with
# `suppress_errors=True`, so a raise there could not have stopped
# `TimingProvenance` even if it had been reached. Measured rather than
# argued: a nominal `(0.0, 0.0)` block lands at `(0.001, 0.005)`, with the
# FOLLOWING block starting at `0.006` -- six slots, three times the floor
# below, i.e. a spurious tier-D quarantine. The guard now genuinely exists,
# one level up, at the only place such a block can be described at all:
# `synth/recipe.py`'s `BlockSpec.n_trials` carries `ge=1`, so no
# `SessionRecipe` can express a zero-duration block and none reaches here.
#
# `BLOCK_END`, by contrast, lands EXACTLY at its nominal instant -- zero
# displacement: its own target (`cursor - CODE_WORD_SPACING_S/2`) is
# dominated by the ratchet from the preceding `TRIAL_END`
# (`cursor - CODE_WORD_SPACING_S`, one slot earlier), which resolves to
# exactly `cursor`.
#
# **That zero has a domain of its own, which the word "always" used to hide.**
# The `TRIAL_END` ratchet dominates only while each trial is long enough to
# carry its own seven code words (`TRIAL_START`, the four-word `TRIAL_NUMBER`
# payload, the outcome marker, `TRIAL_END`) AND repay the five the session
# head spends before the first trial (`SESSION_START` plus the four-word
# `BLOCK_START` payload). Swept over `trial_duration_s` and trial count: the
# displacement is zero at 0.012 s and above for every trial count tried (1,
# 2, 3, 5, 10, 40), and degrades below it -- a single-trial block is 2 slots
# off at 0.010 s, and every block is 5 slots off at 0.007 s, where a trial
# can no longer hold its own words at all. So the bound reads "for any trial
# longer than about a dozen code-word slots", not "always". No plausible
# behavioural trial is 12 ms long and all five shipped recipes use 3 s or
# 6 s, but the bound has an edge and a reader should be told where it is.
#
# Verified against a live session, not only traced by hand:
# `tests/schema/test_timebase.py`'s `provenance_session` fixture measures
# `block_start_time == 0.001` and `block_stop_time == recipe.duration_s`
# exactly.
#
# **This bias is real, systematic and ONE-SIDED, not symmetric noise.**
# `_emit`'s own `max(at_s, earliest)` can never return a time earlier than
# the nominal `at_s` it was asked for -- so the measured boundary is always
# >= the nominal one, never earlier, for any code word this generator
# places. A tolerance built from it is absorbing a known, signed
# quantization in one direction, not guarding against noise that could go
# either way; a reader relying on this constant should not mistake it for
# the latter.
_BLOCK_START_MAX_SLOTS = 1

# The floor doubles the proven 1-slot bound above rather than using it bare,
# for a separate and much smaller reason than the bound itself being
# uncertain: the transported VALUE is also subject to the same float32
# storage rounding `BLOCK_AGREEMENT_TOLERANCE_K` exists for, stacked on top
# of the slot quantization, and comparing two floats at an exact
# theoretical boundary (`diff == floor` to the last representable bit) is
# fragile regardless of how solid the derivation behind the boundary is.
# Doubling costs nothing against a genuine disagreement, which is orders of
# magnitude larger than either the slot quantization or the storage
# rounding on top of it.
BLOCK_AGREEMENT_TOLERANCE_FLOOR_S = 2 * _BLOCK_START_MAX_SLOTS * MIN_CODE_WORD_SLOT_S


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
    assertion, at these magnitudes, to count as agreeing -- two DERIVED
    terms, neither chosen. Fix round 2: a prior fixed `1e-3` cited
    `timebase/segments.py`'s alignment durations as "chosen rather than
    derived" precedent -- wrong, since that module derives its own numbers
    explicitly. Fix round 3: the float32 term that replaced it was right,
    but its floor (`1e-6`) was matched to `timebase/coverage.py`'s
    `_FULL_TOLERANCE_S` -- also wrong, because that constant floors float64
    ACCUMULATION error, not a physical quantity, while this tolerance sits
    under a MEASUREMENT with an actual resolution. See
    `BLOCK_AGREEMENT_TOLERANCE_FLOOR_S`'s own comment for that resolution
    (the strobe bus's one-code-word-per-slot transport) and the exhaustive
    trace behind it.

    Design spec section 5 makes a disagreement here its own tier-D
    condition. Two things make a FIXED tolerance wrong at some magnitude:
    `pipeline.trial.Block`'s own columns are float32 (see
    `BLOCK_AGREEMENT_TOLERANCE_K`'s comment), so the MEASURED side of this
    comparison always carries up to one float32 half-ULP of pure storage
    rounding, and that half-ULP DOUBLES every time the magnitude doubles --
    0.977 ms at 16384 s (4.5h into a session), 1.953 ms at 32768 s (9.1h).
    A fixed 1 ms tolerance already consumes nearly its entire budget on
    storage rounding alone at 4.5h and is provably too tight past 9.1h: a
    false tier-D quarantine on an honestly agreeing long session, for a
    reason that has nothing to do with whether the blocks actually agree --
    exactly the false-verdict shape parent spec section 4.7's whole
    apparatus exists to avoid, reintroduced by an underived term. And a
    fixed tolerance UNDER one slot's transport quantization is wrong at
    short magnitudes for the opposite reason: it would reject an honestly
    agreeing SHORT session too, quarantining it for exactly the same kind
    of reason -- a measurement resolution mistaken for a disagreement.

    Callers pass every value entering ONE boundary comparison (typically the
    measured time and the asserted time for a single endpoint), so the
    returned tolerance is scaled to what is actually being compared, never to
    the session's total duration or some other quantity nothing here checks.
    """
    return max(
        BLOCK_AGREEMENT_TOLERANCE_FLOOR_S,
        BLOCK_AGREEMENT_TOLERANCE_K * max(_float32_half_ulp(m) for m in magnitudes),
    )
