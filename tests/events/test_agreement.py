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
    only training, where the Pi is the sole recorder."""
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
