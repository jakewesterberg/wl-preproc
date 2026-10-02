# wl_preproc/events/agreement.py
"""The three inputs parent spec section 4.7's tiers turn on, and the verdict.

`TimingProvenance` in 1c-4 recorded `tier = 'pending'` and named exactly what
it was waiting for: `event_code_agreement,trial_count_agreement,
camera_trigger_count`. This module supplies them.

**The tier is derived, never asserted** (parent spec section 4.7): every
underlying count is retained on the row so the verdict can be re-derived
under different thresholds later. `resolve_tier` therefore takes only the
measured inputs and holds no state of its own.

**A fourth input, `block_agreement`, is retired** (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 5). It
compared the measured block boundary with wl.works' assertion of it
(`core.Block`), but `TimingProvenance` computes once per session, before any
request exists, so it never ran in wl.works' flow. A request's runs are
checked as it arrives (`responder/jobs.py::_check_runs`), within
`RUN_AGREEMENT_TOLERANCE_S` below.

**`code_agreement`, fix round 2.** A pure function added after review: how
two independent full-code records are compared (content-matched, tolerant
of a dropped or inserted word -- design spec section 4.2 requirement 1's own
property, applied one level up from the codec). Called from
`schema/timebase.py::TimingProvenance.make()`, which supplies the raw
decoded code lists; it touches neither DataJoint nor a file, matching
`resolve_tier`'s own pure, stateless shape.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher


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


# The resolution of a boundary carried on the event codes: the shared strobe
# bus carries one code word at a time (`synth/timeline.py`'s own `_emit`:
# "words can never overlap ... if two logical events want the same instant,
# the second waits"), and a run or block boundary is transported as one
# specific code word in one specific slot -- so it cannot be measured any
# finer than the spacing between slots.
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

# How close a request's run must land to the measured `core.Run` to count as
# the same run (design spec `2026-10-01-session-listing-and-run-requests-design.md`
# section 3.2: "within the agreement tolerance ..., about 2 ms"). wl.works
# asserts its copy of `GET /sessions`' measured value, and the value is a
# double the whole way -- `core.Run`, the listing's JSON, wl.works' own record
# -- so an honest request agrees exactly and no storage rounding needs
# absorbing. Two code-word slots, the floor the retired block check derived
# (one slot of transport quantization, doubled so a comparison never sits on
# its boundary to the last bit). What it refuses is a run measured
# differently from the one wl.works copied: a stale listing.
RUN_AGREEMENT_TOLERANCE_S = 2 * MIN_CODE_WORD_SLOT_S
