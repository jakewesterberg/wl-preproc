"""The three tier inputs, and the verdict they decide. Spec section 4.7."""

from __future__ import annotations

import pytest

from wl_preproc.events import agreement


def _inputs(**over):
    base = dict(
        event_code_agreement=1.0,
        trial_count_agreement=True,
        camera_trigger_count=0,
        n_full_code_records=2,
        n_strobe_witnesses=1,
        decode_errors=0,
    )
    base.update(over)
    return agreement.TierInputs(**base)


def test_tier_a_needs_two_full_code_records_that_agree():
    assert agreement.resolve_tier(_inputs()) == "A"


def test_one_full_code_record_plus_a_witness_is_b():
    """Spec section 4.7: "1 full-code record + >=1 independent strobe witness".
    The standalone-Intan topology."""
    assert agreement.resolve_tier(
        _inputs(n_full_code_records=1, event_code_agreement=None)
    ) == "B"


def test_one_full_code_record_alone_is_c():
    """"1 full-code record, cross-checked only against task file" -- behaviour-
    only training, where the Pi is the sole recorder.

    **`block_agreement=None` does not block C, unlike `trial_count_agreement
    =None` -- fix round 2, folded in after deleting a duplicate test.** A
    prior `test_c_requires_the_block_check_to_have_actually_happened`
    asserted this exact property with `block_agreement=None` passed
    explicitly, which is a no-op: `block_agreement` already defaults to
    `None`, so that test's inputs were this one's verbatim and it killed no
    mutant this one does not. Its own NAME also asserted the opposite of
    what it checked -- it claimed C "requires" the block check, while its
    body proved C does NOT require it -- copied from the genuinely-D
    `..._task_file_check_...` test below without updating for the fact that
    `block_agreement` and `trial_count_agreement` are NOT gated the same
    way: `trial_count_agreement` is a precondition for C's OWN branch
    (`n_full_code_records == 1 and trial_count_agreement is True`), so its
    `None` fails that branch and falls through toward D; `block_agreement`
    has no branch anywhere that requires it to be `True`, so its `None`
    simply never fires the one D-check it participates in and this fixture
    (which never sets it, leaving it `None`) reaches C exactly as it would
    without `block_agreement` existing at all. See `resolve_tier`'s own
    docstring for the fuller version of this distinction.
    """
    assert agreement.resolve_tier(
        _inputs(n_full_code_records=1, n_strobe_witnesses=0, event_code_agreement=None)
    ) == "C"


def test_disagreeing_trial_counts_are_D_not_a_lower_tier():
    """Spec section 2: "codes own timing; task file owns parameters;
    cross-validated, HARD-FAIL on mismatch." A disagreement is a failed check,
    and section 4.7 puts any failed check at D -- it does not demote to C."""
    assert agreement.resolve_tier(_inputs(trial_count_agreement=False)) == "D"


def test_decode_errors_are_D():
    assert agreement.resolve_tier(_inputs(decode_errors=1)) == "D"


def test_two_records_that_disagree_are_D_rather_than_B():
    """Two full-code records that disagree is a FAILED check, not a session
    with one usable record. Demoting to B would silently prefer whichever
    record the implementation happened to read first."""
    assert agreement.resolve_tier(_inputs(event_code_agreement=0.5)) == "D"


def test_c_requires_the_task_file_check_to_have_actually_happened():
    """`trial_count_agreement=None` means there was no task file at all --
    the cross-check spec section 4.7 requires for C never happened. Before
    fix round 1, `resolve_tier` only treated `is False` as a failure, so
    `None` slipped past into "C" here, as if an absent task file were an
    agreeing one. Only a genuinely successful cross-check
    (`trial_count_agreement is True`) may earn C; "never measured" falls to D
    same as "measured and failed", because a tier is a published quality
    claim and nothing corroborated this session."""
    assert agreement.resolve_tier(
        _inputs(
            n_full_code_records=1,
            n_strobe_witnesses=0,
            trial_count_agreement=None,
            event_code_agreement=None,
        )
    ) == "D"


def test_block_disagreement_is_D_even_with_two_agreeing_full_code_records():
    """Design spec section 5: "A disagreement between `trial.Block` (measured)
    and `core.Block` (asserted) is a tier-D condition, not a silent
    reconciliation." Overrides `block_agreement=False` only, everything else
    at base (`n_full_code_records=2`, `event_code_agreement=1.0`,
    `trial_count_agreement=True`) -- so this fixture would otherwise satisfy
    tier A outright. Produces: two full-code records that genuinely agree, a
    genuine task-file cross-check, and a genuine block-boundary disagreement.
    `resolve_tier` must reach the `block_agreement is False` line specifically,
    not merely land on D through some other guard -- confirmed by sabotage:
    deleting that one `if` from `resolve_tier` turns this fixture's verdict
    into "A", and nothing else in THIS file moves.

    (Fix round 2 correction: this docstring previously also claimed "the
    mutant survives every other test in this suite". Checked and false --
    `tests/schema/test_timebase.py::
    test_block_disagreement_forces_d_even_with_two_agreeing_full_code_records`
    exercises the identical `block_agreement is False` path end-to-end
    through `TimingProvenance.make()` and would fail under the same
    sabotage too. The claim was never verified against the whole suite, only
    against this one file; narrowed to what was actually checked.)
    """
    assert agreement.resolve_tier(_inputs(block_agreement=False)) == "D"


def test_block_agreement_true_does_not_block_tier_a():
    """The positive case: `block_agreement=True` alongside every other tier-A
    condition must still resolve to A -- a passing check must never be
    mistaken for a gating one. Produces: two agreeing full-code records, a
    genuine task-file cross-check, and a genuine, matching block boundary."""
    assert agreement.resolve_tier(_inputs(block_agreement=True)) == "A"


def test_no_full_code_record_at_all_is_D():
    """Zero full-code recorders present at all -- nothing decoded any event
    codes, so A, B and C's shared precondition (>=1 full-code record) is
    never met by any of them. Exercises resolve_tier's final bare
    `return "D"` -- but not alone, and not uniquely: fix round 2 corrected
    this docstring after a review found it falsely claimed "none of the
    other seven tests reach" that line. The task-file-check test above
    (n_full_code_records=1, trial_count_agreement=None) reaches the exact
    same final line too, by a different route: THIS test's fixture fails
    every guard's record-count term outright (n_full_code_records=0 satisfies
    neither A's `>= 2` nor B's and C's `== 1`), while THAT test's fixture
    satisfies the `== 1` half of both B's and C's guards and fails only the
    other half of each (no strobe witness for B; trial_count_agreement is
    not True for C). The two tests guard different conditions that happen to
    share a return statement, not the same one twice: this test covers "no
    full-code recorder exists at all", the other covers "a full-code
    recorder exists but its task-file cross-check was never performed".
    Both conditions are real and both are worth a named test; only the
    exclusivity claim about this line was wrong, not the coverage."""
    assert agreement.resolve_tier(
        _inputs(
            n_full_code_records=0,
            n_strobe_witnesses=0,
            event_code_agreement=None,
            trial_count_agreement=None,
        )
    ) == "D"


def test_code_agreement_tolerates_a_dropped_word_at_the_head():
    """Fix round 2: `TimingProvenance.make()` used to compare the Pi's and
    NI's decoded code lists by ordinal position (`zip`), which reintroduces
    -- one layer up -- the exact hazard design spec section 4.2 requirement 1
    exists to prevent: "one dropped code must not shift every subsequent
    trial", quoted directly in this repo's own `tests/events/
    test_assemble.py`. A word dropped at the head of either independent
    record is not hypothetical -- unequal extents are already first-class
    elsewhere in this pipeline (`partial` coverage, `Fault.STOP_MID_TRIAL`,
    `Fault.MID_SESSION_RESTART`) -- and no named recipe in `synth/recipe.py`
    sets `faults=`, so nothing ever exercised two genuinely unequal streams
    before this test.

    Produces: 2000 distinct code words as the Pi's record, and the identical
    2000 with the first removed as the NI's -- a dropped word at the head,
    the worst case for a position-matched comparison, since it misaligns
    every subsequent position. Content-matched agreement stays at 0.9995,
    comfortably above `AGREEMENT_THRESHOLD` (0.999) -- a session this would
    genuinely reach tier A on.

    Confirmed against the position-matched (`zip`) implementation this
    replaces: computed inline below rather than reintroduced as a second
    real implementation anywhere. That computation gives exactly `0.0` on
    this fixture -- every position mismatched, because `reference[i]` is
    always `1000 + i` and `dropped_at_head[i]` is always `1001 + i`, which
    can never be equal -- confirming the old implementation would have
    quarantined this genuinely-agreeing pair at tier D.
    """
    reference = list(range(1000, 3000))  # 2000 distinct code words
    dropped_at_head = reference[1:]  # the NI missed the very first one

    result = agreement.code_agreement(reference, dropped_at_head)
    assert result == pytest.approx(0.9995)
    assert result >= agreement.AGREEMENT_THRESHOLD, (
        "a single dropped word must not collapse agreement for a 2000-word "
        f"record: got {result}"
    )

    # The position-matched implementation this replaces, computed here only
    # to prove what it WOULD have said -- never reintroduced as production
    # code.
    total = max(len(reference), len(dropped_at_head))
    matched = sum(1 for a, b in zip(reference, dropped_at_head) if a == b)
    position_matched_result = (matched / total) if total else 1.0
    assert position_matched_result == pytest.approx(0.0), (
        "this fixture must exercise the old implementation's actual failure "
        f"mode, not merely a lower score: got {position_matched_result}"
    )


def test_block_agreement_tolerance_is_derived_from_float32_precision_not_chosen():
    """Fix round 2: a fixed `1e-3` tolerance in `TimingProvenance.make()`
    cited `timebase/segments.py`'s alignment durations as precedent for
    "chosen rather than derived" -- wrong, since that module derives its own
    numbers explicitly ("consequences of the decoder"), and a real budget
    exists to derive this one too. `pipeline.trial.Block` declares
    `block_start_time`/`block_stop_time` as `float` (single precision,
    confirmed directly against `element_event/trial.py`), so the MEASURED
    side of a block-boundary comparison always carries up to one float32
    half-ULP of pure storage rounding -- and that half-ULP grows with
    magnitude, consuming a fixed 1 ms tolerance's entire budget by 4.5h into
    a session and exceeding it past 9.1h.

    A schema-level test at that duration would need to generate hours of
    synthetic session data (`synth/recipe.py::BENCHMARK_RECIPE`'s own
    comment: "tens of megabytes per generation" for a session far shorter
    than this), so this tests the derivation function directly instead --
    honest about testing the unit rather than quietly avoiding the regime
    the schema-level fixtures never reach.

    Produces: `true_end_s = 42345.678`, a magnitude in the same binade
    `worst_drift_ppm`'s own review measured (past 32768s / 9.1h) and NOT
    exactly float32-representable, so storing it in a `float` column
    genuinely rounds it -- confirmed inline (`numpy.float32(true_end_s) !=
    true_end_s`) rather than assumed. The resulting rounding error, 1.6875
    ms, exceeds a fixed 1 ms tolerance outright (proving the fixed tolerance
    would have wrongly quarantined this honestly-agreeing pair at tier D),
    while the derived tolerance at this magnitude (3.90625 ms) comfortably
    covers it. A genuine several-second disagreement at the same magnitude
    is still correctly rejected, proving the derivation does not just grow
    permissive without bound.
    """
    import numpy as np

    true_end_s = 42345.678
    stored_measured_end_s = float(np.float32(true_end_s))
    rounding_error_s = abs(stored_measured_end_s - true_end_s)

    assert rounding_error_s > 0.0, (
        "this fixture must exercise genuine float32 rounding, not a value "
        "that happens to already be exactly representable"
    )

    fixed_tolerance_s = 1e-3
    assert rounding_error_s > fixed_tolerance_s, (
        "this test's whole point is a magnitude where a fixed 1 ms tolerance "
        f"is already insufficient for storage rounding alone: got "
        f"{rounding_error_s}s"
    )

    derived = agreement.block_agreement_tolerance_s(stored_measured_end_s, true_end_s)
    assert derived > fixed_tolerance_s
    assert rounding_error_s <= derived, (
        "the derived tolerance must cover pure storage rounding at this "
        f"magnitude: error {rounding_error_s}s, tolerance {derived}s"
    )

    # And a genuine disagreement -- not storage rounding -- at the same
    # magnitude must still be rejected: the derivation must not grow so
    # permissive that it stops meaning anything.
    genuinely_disagreeing = stored_measured_end_s + 1.0
    assert abs(stored_measured_end_s - genuinely_disagreeing) > agreement.block_agreement_tolerance_s(
        stored_measured_end_s, genuinely_disagreeing
    )


def test_min_code_word_slot_s_tracks_synth_timelines_spacing_or_flags_the_drift():
    """Fix round 4 (coordinator review): a drift detector, not a coupling.

    `events.agreement.MIN_CODE_WORD_SLOT_S` and `synth.timeline.
    CODE_WORD_SPACING_S` are two independent constants that happen to share
    a value (`0.001`) today, and nothing before this test noticed if they
    stopped agreeing. This project has already paid for exactly this shape
    once: Task 1 found the same 1-sample buffer bug shipped independently in
    two emitters, and the fix was a shared `code_word_span_s` helper built
    specifically so the two could not drift apart. A shared helper is not
    available here -- `wl_preproc.events` is production code and
    `wl_preproc.synth` is fixture generation only, so one may not import the
    other in either direction (`MIN_CODE_WORD_SLOT_S`'s own comment) -- so
    this test is the substitute: `tests/` is the one layer allowed to import
    both without running that architecture backwards.

    **What this guards, concretely.** `events.agreement.
    BLOCK_AGREEMENT_TOLERANCE_FLOOR_S` is derived from `MIN_CODE_WORD_SLOT_S`
    as "one code-word slot's worth of transport quantization, doubled for
    float32-rounding headroom" -- a derivation that is only true because
    `MIN_CODE_WORD_SLOT_S` matches the ACTUAL slot spacing the synthetic
    generator (this project's only behavioural-stack implementation) uses to
    place code words. If `synth.timeline.CODE_WORD_SPACING_S` is ever
    revised and `MIN_CODE_WORD_SLOT_S` is not updated to match, the floor
    silently stops covering the ratchet `tests/schema/test_timebase.py::
    provenance_session` measures (`block_start_time == 0.001`), and an
    honestly agreeing session starts reading `block_agreement=False` and
    getting quarantined at tier D -- silently, and in the single most
    consequential surface this phase produces.

    **This is a "decide, don't drift" gate, not a permanent lock.**
    Divergence is allowed: the production constant is meant to track a real
    system's own slot spacing once the behavioural stack is chosen, at which
    point it will legitimately stop matching the synthetic generator's own
    choice. What must not happen is divergence nobody decided -- so this
    test fails loudly, with a message that says a real change is fine, and
    names exactly what to go re-derive.
    """
    from wl_preproc.synth import timeline

    if agreement.MIN_CODE_WORD_SLOT_S != timeline.CODE_WORD_SPACING_S:
        pytest.fail(
            "events.agreement.MIN_CODE_WORD_SLOT_S "
            f"({agreement.MIN_CODE_WORD_SLOT_S}) no longer matches "
            "synth.timeline.CODE_WORD_SPACING_S "
            f"({timeline.CODE_WORD_SPACING_S}). This divergence may be "
            "correct and deliberate -- MIN_CODE_WORD_SLOT_S is documented "
            "to track a real system's own code-word slot spacing once one "
            "is chosen, not to stay locked to the synthetic generator "
            "forever. But it must be a DECISION, not an accident: "
            "block_agreement_tolerance_s's floor "
            "(BLOCK_AGREEMENT_TOLERANCE_FLOOR_S) is derived from "
            "MIN_CODE_WORD_SLOT_S as one code-word transport slot, and if "
            "that no longer reflects the slot spacing a real boundary is "
            "actually quantized to, the floor silently stops covering the "
            "ratchet and an honestly agreeing session starts getting "
            "quarantined at tier D. Whoever changed either constant must "
            "re-derive BLOCK_AGREEMENT_TOLERANCE_FLOOR_S against the new "
            "value (or explicitly confirm the old derivation still holds) "
            "before this assertion is updated to match."
        )
