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
