"""Assembling decoded events into trials and measured blocks."""

from __future__ import annotations

from wl_preproc.contracts.events import Escape, Marker, encode_payload
from wl_preproc.events import assemble


def _stream(pairs):
    """(time_s, word) pairs -> decoded events, through the frozen codec."""
    from wl_preproc.contracts.events import decode_stream

    return decode_stream(pairs)


def _trial(t0: float, trial_id: int, outcome: Marker):
    """The three codes a trial start is at minimum: marker, id payload, checksum."""
    words = [(t0, Marker.TRIAL_START.value)]
    for offset, word in enumerate(
        encode_payload(Escape.TRIAL_NUMBER, [trial_id >> 16, trial_id & 0xFFFF])
    ):
        words.append((t0 + 0.001 * (offset + 1), word))
    words.append((t0 + 0.5, outcome.value))
    words.append((t0 + 0.51, Marker.TRIAL_END.value))
    return words


def test_trials_are_matched_by_id_not_by_position():
    """Spec section 4.2 requirement 1, and the whole reason payloads carry an
    explicit trial number: "one dropped code must not shift every subsequent
    trial."

    A dropped TRIAL_START marker must lose ONE trial, not renumber the rest.
    An ordinal implementation passes the happy path and fails here.
    """
    words = []
    for trial_id in (1, 2, 3):
        words.extend(_trial(t0=trial_id * 10.0, trial_id=trial_id, outcome=Marker.TRIAL_CORRECT))

    # Drop trial 2's opening marker -- the code that a lossy line loses.
    words = [w for w in words if not (w[0] == 20.0 and w[1] == Marker.TRIAL_START.value)]

    result = assemble.assemble(_stream(words))
    ids = [t.trial_id for t in result.trials]
    assert 1 in ids and 3 in ids, f"trials 1 and 3 must survive; got {ids}"
    assert 3 in ids, "trial 3 must keep its OWN id, not inherit trial 2's slot"


def test_a_block_start_payload_carries_its_task_type():
    """BLOCK_START's payload is (block_number, task_type_code), so a block is
    self-describing in the recording "even when the ELN is wrong or late" --
    contracts/events.py's own words."""
    from wl_preproc.contracts.events import TaskTypeCode

    words = [(0.0, w) for w in encode_payload(
        Escape.BLOCK_START, [7, TaskTypeCode.RF_MAP.value]
    )]
    words = [(0.001 * i, w) for i, (_, w) in enumerate(words)]
    words.append((5.0, Marker.BLOCK_END.value))

    result = assemble.assemble(_stream(words))
    assert len(result.blocks) == 1
    assert result.blocks[0].block_id == 7
    assert result.blocks[0].task_type == TaskTypeCode.RF_MAP.value


def test_decode_errors_are_kept_rather_than_dropped():
    """decode_stream never raises: "a corrupt or truncated payload yields a
    DecodeError and decoding continues, so one bad trial cannot lose a
    session." The assembly must surface those, because a session with decode
    errors is a tier-D candidate and silence would hide it."""
    words = [(0.0, Escape.TRIAL_NUMBER.value), (0.001, 1), (0.002, 0xDEAD)]  # bad checksum
    result = assemble.assemble(_stream(words))
    assert result.errors, "a checksum failure must reach the assembly's error list"
