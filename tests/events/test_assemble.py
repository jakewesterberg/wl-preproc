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
    """assemble() forwards a DecodeError into Assembly.errors rather than
    discarding it: decode_stream "never raises on malformed input... so one
    bad trial cannot lose a session," and a session with decode errors is a
    tier-D candidate that silence would hide.

    assemble() is deliberately reason-blind here -- it appends whatever
    DecodeError decode_stream produced, regardless of why decoding failed.
    WHICH codec-level failure occurred (a corrupted checksum vs. a truncated
    payload) is pinned down at the codec's own boundary, in
    tests/contracts/test_events_codec.py, and is not this layer's business to
    re-verify.

    History: fix round 1 briefly added two tests here --
    test_a_corrupted_checksum_word_is_caught_rather_than_decoded and
    test_a_truncated_payload_is_caught_rather_than_decoded -- meant to add
    checksum/truncation coverage "from the events package's own side."
    Neither actually called assemble.assemble(); both exercised decode_stream
    directly through the _stream() helper, so they duplicated
    tests/contracts/test_events_codec.py's existing coverage of those branches
    while reading, in this file, as assembly coverage. Removed in fix round 2
    rather than reworded, because the fix for a false claim is deletion, not
    a better label.
    """
    words = [(0.0, Escape.TRIAL_NUMBER.value), (0.001, 1), (0.002, 0xDEAD)]  # truncated: only 2 of the 3 required words follow the escape
    result = assemble.assemble(_stream(words))
    assert result.errors, "a decode error must reach the assembly's error list"


def test_an_unclosed_block_records_its_last_event():
    """A run that faults sends no BLOCK_END, so the next BLOCK_START finds it
    open. Its last event is what the recording proves of its end (design
    spec `2026-10-01-runs-and-trials-design.md` section 2.3)."""
    words = [*encode_payload(Escape.BLOCK_START, [1, 0]), Marker.TRIAL_START.value,
             *encode_payload(Escape.TRIAL_NUMBER, [0, 1]),
             *encode_payload(Escape.BLOCK_START, [2, 0]), Marker.TRIAL_START.value,
             *encode_payload(Escape.TRIAL_NUMBER, [0, 2]), Marker.TRIAL_END.value, Marker.BLOCK_END.value]
    pairs = [(0.001 * i, word) for i, word in enumerate(words)]
    first, second = assemble.assemble(_stream(pairs)).blocks
    # A payload event is timed at its escape word: trial 1's TRIAL_NUMBER.
    assert first.end_s is None and first.last_s == pairs[5][0]
    assert second.end_s == second.last_s == pairs[-1][0]


def test_runs_are_measured_like_blocks_and_an_unclosed_one_ends_at_its_last_event():
    """Design spec `2026-10-01-runs-and-trials-design.md` sections 2.1 and
    2.3: RUN_START opens a run with its number and task, RUN_END closes it,
    and a run that faulted -- no RUN_END -- records its last event."""
    words = [*encode_payload(Escape.RUN_START, [1, 0]), *encode_payload(Escape.BLOCK_START, [1, 0]),
             Marker.TRIAL_START.value, *encode_payload(Escape.TRIAL_NUMBER, [0, 1]),
             *encode_payload(Escape.RUN_START, [2, 5]), *encode_payload(Escape.BLOCK_START, [2, 5]),
             Marker.TRIAL_START.value, *encode_payload(Escape.TRIAL_NUMBER, [0, 2]), Marker.TRIAL_END.value,
             Marker.BLOCK_END.value, Marker.RUN_END.value]
    pairs = [(0.001 * i, word) for i, word in enumerate(words)]
    assembly = assemble.assemble(_stream(pairs))
    first, second = assembly.runs
    assert (first.run_number, first.task_type, first.start_s, first.end_s) == (1, 0, 0.0, None)
    assert first.last_s == pairs[9][0]  # trial 1's TRIAL_NUMBER, timed at its escape word
    assert (second.run_number, second.task_type, second.end_s, second.last_s) == (2, 5, pairs[-1][0], pairs[-1][0])
    # A block lies inside its run: the open block ends with its run, not at
    # the next run's RUN_START (final review C1).
    assert assembly.blocks[0].last_s == first.last_s


def test_a_block_left_open_ends_with_its_run():
    """A run that ended by design but whose BLOCK_END was lost: the block's
    stop is its own last event before RUN_END, not a code strobed between
    runs (MANUAL_REWARD, 4134, here) nor the next run's start."""
    words = [*encode_payload(Escape.RUN_START, [1, 0]), *encode_payload(Escape.BLOCK_START, [1, 0]),
             Marker.TRIAL_START.value, *encode_payload(Escape.TRIAL_NUMBER, [0, 1]), Marker.TRIAL_END.value,
             Marker.RUN_END.value, 4134,
             *encode_payload(Escape.RUN_START, [2, 0]), *encode_payload(Escape.BLOCK_START, [2, 0]),
             Marker.TRIAL_START.value, *encode_payload(Escape.TRIAL_NUMBER, [0, 2]), Marker.TRIAL_END.value,
             Marker.BLOCK_END.value, Marker.RUN_END.value]
    pairs = [(0.001 * i, word) for i, word in enumerate(words)]
    assembly = assemble.assemble(_stream(pairs))
    first_block, first_run = assembly.blocks[0], assembly.runs[0]
    assert first_block.end_s is None and first_block.last_s == pairs[13][0]  # trial 1's TRIAL_END
    assert first_block.last_s < first_run.end_s == pairs[14][0]
