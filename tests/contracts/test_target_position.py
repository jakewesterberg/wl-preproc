import pytest

from wl_preproc.contracts.events import (
    DVA_OFFSET,
    PAYLOAD_WORD_COUNTS,
    Escape,
    TargetRole,
    decode_dva,
    encode_dva,
)


def test_screen_centre_is_the_offset():
    """Design spec section 4.1: straight ahead is 32768 on both axes."""
    assert encode_dva(0.0) == DVA_OFFSET == 32768


def test_the_worked_example_from_the_spec():
    """A target 10 degrees right and 5 up -- section 4.1's own table."""
    assert encode_dva(10.0) == 33768
    assert encode_dva(5.0) == 33268


def test_it_round_trips_across_the_range():
    for deg in (-327.0, -10.5, -0.01, 0.0, 0.01, 10.5, 327.0):
        assert decode_dva(encode_dva(deg)) == pytest.approx(deg, abs=0.005)


def test_a_word_always_fits_sixteen_bits():
    """A payload word wider than the bus would be silently truncated by the
    sync box, and the truncation is not detectable downstream."""
    for deg in (-327.68, 327.67):
        assert 0 <= encode_dva(deg) <= 0xFFFF


def test_out_of_range_is_refused_not_clamped():
    """Clamping would place a target at the edge of the screen and report
    success -- a plausible number, which is this project's signature defect."""
    with pytest.raises(ValueError, match="out of range"):
        encode_dva(400.0)


def test_the_escape_declares_three_payload_words():
    assert PAYLOAD_WORD_COUNTS[Escape.TARGET_POSITION] == 3
    assert Escape.TARGET_POSITION == 0x8004


def test_roles_are_distinct_and_start_at_the_fixation_point():
    assert TargetRole.FIXATION_POINT == 0
    assert TargetRole.SACCADE_TARGET == 1
    assert len(set(TargetRole)) == len(list(TargetRole))
