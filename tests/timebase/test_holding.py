"""Whether a code stream holds every run the rig recorded (design spec
`2026-10-18-training-records-and-backup-clock-design.md` section 4.2,
amendments 41 and 44): what decides which clock keeps a session's time. Each
case is a hand-built decoded stream against a hand-built rig record."""

from __future__ import annotations

from wl_preproc.contracts.events import Escape, Marker, TaskTypeCode, decode_stream, encode_payload
from wl_preproc.events.rigruns import RigRun, RigRuns
from wl_preproc.events.rigtrials import RigRecord, RigTrial
from wl_preproc.timebase.holding import unheld


def _payload(time_s: float, escape: Escape, words: list[int]) -> list[tuple[float, int]]:
    return [(time_s + 0.001 * offset, word) for offset, word in enumerate(encode_payload(escape, words))]


def _trial(time_s: float, number: int, *, closed: bool = True) -> list[tuple[float, int]]:
    codes = [(time_s, Marker.TRIAL_START.value),
             *_payload(time_s + 0.001, Escape.TRIAL_NUMBER, [number >> 16, number & 0xFFFF]),
             (time_s + 0.5, Marker.TRIAL_CORRECT.value)]
    return [*codes, (time_s + 0.6, Marker.TRIAL_END.value)] if closed else codes


def _run(time_s: float, number: int, trials: list[int], *, ended: bool = True,
         open_last: bool = False) -> list[tuple[float, int]]:
    codes = _payload(time_s, Escape.RUN_START, [number, TaskTypeCode.RF_MAP.value])
    for offset, trial in enumerate(trials, start=1):
        codes += _trial(time_s + offset, trial, closed=not (open_last and offset == len(trials)))
    return [*codes, (time_s + len(trials) + 1, Marker.RUN_END.value)] if ended else codes


def _session(*runs: list[tuple[float, int]], start: bool = True, end: bool = True):
    codes = [(0.0, Marker.SESSION_START.value)] if start else []
    for run in runs:
        codes += run
    if end:
        codes.append((100.0, Marker.SESSION_END.value))
    return decode_stream(codes)


def _rig(runs: dict[int, list[int]]) -> tuple[RigRuns, RigRecord]:
    """The rig's record of `{run number: [trial numbers]}`: wl-xcon's `run`
    from 0, its number `run` + 1, as a record before `run_in_session` gives
    it."""
    rig_runs = RigRuns(runs=tuple(RigRun(number=number, task="rf_map", stopped_because="every block is finished",
                                         stop_kind="completed", index=number - 1) for number in runs),
                       problems=())
    trials = tuple(RigTrial(number=trial, index=position, outcome="correct", block="block-1", condition="c",
                            params={}, run=number - 1)
                   for number, numbers in runs.items() for position, trial in enumerate(numbers))
    return rig_runs, RigRecord(trials=trials, problems=())


def test_every_run_and_trial_present_is_held():
    decoded = _session(_run(1.0, 1, [1, 2]), _run(10.0, 2, [3]))
    assert unheld(decoded, *_rig({1: [1, 2], 2: [3]})) is None


def test_a_run_without_its_end_code_is_still_held():
    """Amendment 41: a faulted run sends no RUN_END (`events.py:625`), and a
    missing end code alone does not hand a healthy session to the NI."""
    decoded = _session(_run(1.0, 1, [1, 2], ended=False), _run(10.0, 2, [3]))
    assert unheld(decoded, *_rig({1: [1, 2], 2: [3]})) is None


def test_a_missing_trial_is_not_held_and_says_which():
    rig = _rig({1: [1, 2], 3: [40, 41]})
    assert unheld(_session(_run(1.0, 1, [1, 2]), _run(10.0, 3, [40])), *rig) == "run 3's trial 41 is not in its codes"
    # Present but never closed, as a stream that stops inside it leaves it.
    assert unheld(_session(_run(1.0, 1, [1, 2]), _run(10.0, 3, [40, 41], ended=False, open_last=True), end=False),
                  *rig) == "run 3's trial 41 does not end in its codes"


def test_a_missing_run_is_not_held():
    decoded = _session(_run(1.0, 1, [1, 2]))
    assert unheld(decoded, *_rig({1: [1, 2], 2: [3]})) == "run 2 is not in its codes"


def test_without_a_rig_record_the_session_markers_decide():
    assert unheld(_session(_run(1.0, 1, [1])), None, None) is None
    assert unheld(_session(_run(1.0, 1, [1]), end=False), None, None) == "its codes hold no SESSION_END"
    assert unheld(_session(_run(1.0, 1, [1]), start=False), None, None) == "its codes hold no SESSION_START"
    # A record naming no run, as one for another subject is read, is no record.
    assert unheld(_session(_run(1.0, 1, [1]), end=False), RigRuns(runs=(), problems=()), None) == (
        "its codes hold no SESSION_END")


def test_codes_before_the_ni_started_are_not_required():
    """Amendment 44: the rig may strobe SESSION_START before SpikeGLX starts,
    and its end after it stops; with the rig's record, only the runs count."""
    decoded = _session(_run(1.0, 1, [1, 2]), _run(10.0, 2, [3]), start=False, end=False)
    assert unheld(decoded, *_rig({1: [1, 2], 2: [3]})) is None


def test_the_sync_box_needs_only_to_reach_the_end_of_the_last_run():
    """Amendment 42: the sync box keeps time unless its log ends before the
    session's last run does. A gap before that run, a log that starts late,
    or a gap inside the last run that resumes before its last trial all
    reach its end; one that stops inside the last trial does not. Every
    log here decodes neither the last run's `RUN_END` nor `SESSION_END`, so
    its last trial line decides (either end code alone reaches the end:
    `test_a_lost_or_corrupt_word_in_the_last_trial_still_reaches_the_end`)."""
    from wl_preproc.timebase.holding import unreached_end

    rig = _rig({1: [1, 2], 2: [3], 3: [4, 5, 6]})
    gap = _session(_run(1.0, 1, [1]), _run(20.0, 3, [4, 5, 6], ended=False), end=False)
    assert unheld(gap, *rig) == "run 1's trial 2 is not in its codes"
    assert unreached_end(gap, *rig) is None
    assert unreached_end(_session(_run(20.0, 3, [4, 5, 6], ended=False), start=False, end=False), *rig) is None
    inside = [code for code in _run(20.0, 3, [4, 5, 6], ended=False) if not 21.5 <= code[0] < 22.7]
    assert unheld(_session(inside, end=False), *rig) == "run 1 is not in its codes"
    assert unreached_end(_session(inside, end=False), *rig) is None
    stopped = _session(_run(1.0, 1, [1, 2]), _run(10.0, 2, [3]), _run(20.0, 3, [4, 5, 6], ended=False, open_last=True),
                       end=False)
    assert unreached_end(stopped, *rig) == "run 3's trial 6 does not end in its codes"
    assert unreached_end(_session(_run(20.0, 3, [4, 5], ended=False), end=False), *rig) == (
        "run 3's trial 6 is not in its codes")
    assert unreached_end(_session(_run(1.0, 1, [1, 2]), _run(10.0, 2, [3]), end=False), *rig) == (
        "run 3's trial 6 is not in its codes")


def test_a_last_run_with_no_trial_lines_is_reached_when_it_is_present():
    from wl_preproc.timebase.holding import unreached_end

    rig_runs, _ = _rig({1: [1], 2: []})
    assert unreached_end(_session(_run(10.0, 2, [], ended=False), end=False), rig_runs, None) is None
    assert unreached_end(_session(_run(1.0, 1, [1]), end=False), rig_runs, None) == "run 2 is not in its codes"
    # With no rig record, the session's end decides.
    assert unreached_end(_session(_run(1.0, 1, [1]), start=False), None, None) is None
    assert unreached_end(_session(_run(1.0, 1, [1]), end=False), None, None) == "its codes hold no SESSION_END"


def test_a_lost_or_corrupt_word_in_the_last_trial_still_reaches_the_end():
    """Amendment 42 speaks of the log *ending*: a log whose last trial lost a
    word or failed its checksum, but which goes on to decode the last run's
    `RUN_END` or the session's `SESSION_END`, did not end before the last run
    did (the final review's I1; §4.2, "a disagreement alone is not taken to
    mean the sync box stopped")."""
    from wl_preproc.timebase.holding import unreached_end

    rig = _rig({1: [1], 2: [4]})
    corrupt = [(time_s, 5 if (time_s, word) == (11.003, 4) else word) for time_s, word in _run(10.0, 2, [4])]
    assert (11.003, 5) in corrupt
    both = _session(_run(1.0, 1, [1]), corrupt)
    assert unheld(both, *rig) == "run 2's trial 4 is not in its codes"
    assert unreached_end(both, *rig) is None
    # Either end code alone shows the log ran past the last trial.
    assert unreached_end(_session(_run(1.0, 1, [1]), corrupt, end=False), *rig) is None
    without_run_end = [code for code in corrupt if code[1] != Marker.RUN_END.value]
    assert unreached_end(_session(_run(1.0, 1, [1]), without_run_end), *rig) is None
    # With neither, the trial line decides, as before.
    assert unreached_end(_session(_run(1.0, 1, [1]), without_run_end, end=False), *rig) == (
        "run 2's trial 4 is not in its codes")
    # A RUN_END of an earlier run is not the last run's.
    assert unreached_end(_session(_run(1.0, 1, [1]), end=False), *rig) == "run 2's trial 4 is not in its codes"
