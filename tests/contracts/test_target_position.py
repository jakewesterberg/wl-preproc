import pytest

from wl_preproc.contracts.events import (
    DVA_OFFSET,
    PAYLOAD_WORD_COUNTS,
    Escape,
    TargetRole,
    TaskEvent,
    TaskTypeCode,
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


# --- Calibration blocks and calibration epochs ------------------------------
#
# Ruled 2026-08-31: BOTH mechanisms, for two different situations. A whole
# dedicated block declares itself in its own `BLOCK_START` payload
# (`TaskTypeCode.CALIBRATION`); an epoch inside any other task is bounded by a
# marker pair (`TaskEvent.CALIBRATION_START`/`CALIBRATION_END`), the case a
# block type alone cannot express since a block has exactly one type.


def test_no_pre_existing_task_type_code_moved():
    """This is a frozen interface a separate piece of software is written
    against. Adding `CALIBRATION` must not renumber anything: a shifted value
    silently relabels every block in every recording made before the shift,
    and nothing downstream could detect it.

    Every pre-existing member is pinned by name AND value, not merely counted,
    so an insertion in the middle fails here rather than in the field.
    """
    assert TaskTypeCode.RESTING_DARK == 1
    assert TaskTypeCode.RF_MAP == 2
    assert TaskTypeCode.PASSIVE_FLASH == 3
    assert TaskTypeCode.SHAPE_MAP == 4
    assert TaskTypeCode.COLOR_MAP == 5
    assert TaskTypeCode.MEMORY_GUIDED_SACCADE == 6


def test_the_calibration_block_takes_the_next_free_task_type_code():
    assert TaskTypeCode.CALIBRATION == 7
    # Still inside the reserved standing-task range: this class's own
    # docstring puts lab-defined tasks at 100 and above, so 7 cannot collide
    # with one the lab defines later.
    assert TaskTypeCode.CALIBRATION < 100


def test_no_pre_existing_task_event_moved():
    assert TaskEvent.FIXATION_ACQUIRED == 256
    assert TaskEvent.FIXATION_END == 257


def test_the_calibration_epoch_markers_take_the_next_free_task_events():
    assert TaskEvent.CALIBRATION_START == 258
    assert TaskEvent.CALIBRATION_END == 259


def test_every_task_event_sits_in_its_own_allocated_range():
    """256-4095 (module docstring's range allocation). Below 256 collides with
    `Marker`'s session/block/trial namespace, which shares the same wire and
    is decoded by value alone -- `decode_stream` has no way to tell the two
    apart, so an out-of-range task event would decode AS a marker."""
    for event in TaskEvent:
        assert 256 <= event.value <= 4095


def test_task_type_codes_and_task_events_are_each_distinct():
    """An IntEnum silently ALIASES a duplicate value rather than refusing it:
    a second member declared `= 7` would become `TaskTypeCode.CALIBRATION`
    under a different name, and iteration would not even list it. Comparing
    the value set's size against the declared-member count is what catches
    that, since `len(TaskTypeCode)` counts canonical members only."""
    for enum_type in (TaskTypeCode, TaskEvent):
        values = [member.value for member in enum_type]
        assert len(set(values)) == len(values)
        assert len(values) == len(enum_type.__members__)
