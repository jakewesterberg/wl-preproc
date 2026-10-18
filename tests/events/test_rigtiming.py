"""Placing wl-xcon's training records on the session's clock: each row by the
code its act strobed, and a control that strobed nothing through the rig's
clock fit (design spec `2026-10-18-training-records-and-backup-clock-design.md`
section 3.3, amendment 2)."""

from __future__ import annotations

import random

import pytest

# The rig's anchored wall clock at session t = 0, POSIX seconds, and its rate.
ZERO = 1_773_478_790.0
DRIFT_PPM = 20.0


def _rig(session_s: float, jitter: float = 0.0) -> float:
    return ZERO + session_s * (1 + DRIFT_PPM / 1e6) + jitter


def _change(line, code=4130, coded=True):
    from wl_preproc.events.rigrecords import ParameterChange

    return ParameterChange(line=line, sequence=line, name="reward_ml", was="0.2", now="0.25", run_index=0,
                           event_code=code, coded=coded, by_kind="box", by_name="rig-a", by_account=None,
                           by_issuer=None, by_token_id=None, row="{}")


def _control(line, kind="pause", code=4131, at=None, coded=True):
    from wl_preproc.events.rigrecords import Control

    return Control(line=line, kind=kind, at=at, at_local=None, trial_index=line, run_index=0, event_code=code,
                   coded=coded, detail="{}", by_kind="box", by_name="rig-a", by_account=None, by_issuer=None,
                   by_token_id=None, row="{}")


def _records(changes=(), controls=()):
    from wl_preproc.events.rigrecords import RigRecords

    return RigRecords(changes=tuple(changes), controls=tuple(controls), problems=())


def test_the_sessions_codes_are_its_simple_events_by_number_not_a_payloads_words():
    """A payload word equal to a framework code's number is not that code:
    `decode_stream` reads payloads by position, and only its simple events
    are codes a row can have strobed."""
    from wl_preproc.contracts.events import Escape, decode_stream, encode_payload
    from wl_preproc.events.rigtiming import code_times

    words = [(1.0, 4130), *((2.0 + 0.001 * i, word) for i, word in enumerate(encode_payload(
        Escape.TRIAL_NUMBER, [0, 4130]))), (3.0, 4131), (4.0, 4130)]
    assert code_times(decode_stream(words)) == {4130: [1.0, 4.0], 4131: [3.0]}


def test_each_row_takes_the_time_of_its_numbers_occurrence_in_order():
    """The n-th row of a file carrying a number takes the n-th occurrence of
    that number; each file's rows are counted alone."""
    from wl_preproc.events.rigtiming import time_records

    codes = {4130: [10.0, 20.0], 4131: [12.0, 30.0], 4132: [14.0], 4134: [13.0]}
    timing = time_records(_records(
        changes=[_change(1), _change(2)],
        controls=[_control(1), _control(2, "reward", 4134), _control(3, "resume", 4132), _control(4)]), codes, [])
    assert timing.changes == (10.0, 20.0)
    assert timing.controls == ((12.0, "code"), (13.0, "code"), (14.0, "code"), (30.0, "code"))
    assert timing.problems == ()


def test_a_count_that_disagrees_leaves_that_numbers_rows_untimed_and_names_both_counts():
    """A code the recording lost, or one strobed with no row (wl-xcon's
    corrections 2 and 3), leaves that number's rows untimed; the other
    numbers' rows are timed."""
    from wl_preproc.events.rigtiming import time_records

    codes = {4130: [10.0, 20.0, 25.0], 4131: [12.0], 4134: [13.0, 15.0]}
    timing = time_records(_records(
        changes=[_change(1), _change(2)],
        controls=[_control(1), _control(2, "reward", 4134)]), codes, [])
    assert timing.changes == (None, None)
    assert timing.controls == ((12.0, "code"), (None, None))
    assert timing.problems == (
        "parameter_changes.jsonl: 2 rows carry code 4130 and the recording holds 3, so none of them is timed",
        "controls.jsonl: 1 row carries code 4134 and the recording holds 2, so it is not timed",
    )


def test_a_tasks_own_code_with_a_framework_codes_number_untimes_that_numbers_rows():
    """The plan's Review Focus 4: a task's mark strobing `PARAM_CHANGED`'s
    number (wl-xcon's XC-382, a load-time warning there) is one more such
    code than the change log has rows, so no change is timed by it; the
    pause, another number, still is."""
    from wl_preproc.events.rigtiming import time_records

    timing = time_records(_records(changes=[_change(1), _change(2)], controls=[_control(1)]),
                          {4130: [5.0, 10.0, 20.0], 4131: [12.0]}, [])
    assert (timing.changes, timing.controls) == ((None, None), ((12.0, "code"),))
    assert timing.problems == (
        "parameter_changes.jsonl: 2 rows carry code 4130 and the recording holds 3, so none of them is timed",)


def test_a_row_that_does_not_say_its_code_is_untimed():
    """Amendment 2: written before wl-xcon carried `event_code`, it is
    unknown, so neither joined nor fitted; one problem per file."""
    from wl_preproc.events.rigtiming import time_records

    timing = time_records(_records(changes=[_change(1, None, coded=False), _change(2, None, coded=False)],
                                   controls=[_control(1, "note", None, at=_rig(5.0), coded=False)]),
                          {4130: [10.0]}, [(_rig(0.0), 0.0), (_rig(100.0), 100.0)])
    assert (timing.changes, timing.controls) == ((None, None), ((None, None),))
    assert timing.problems == (
        "parameter_changes.jsonl: 2 rows do not say which code they strobed, so they are not timed",
        "controls.jsonl: 1 row does not say which code it strobed, so it is not timed",
    )


def test_a_control_that_strobed_nothing_is_timed_through_the_rigs_clock():
    """The run rows' instants and the coded controls' instants, against
    their codes, fit the rig's clock; a note, which strobes nothing, is
    timed through it."""
    from wl_preproc.events.rigtiming import time_records

    timing = time_records(_records(controls=[_control(1, at=_rig(12.0)), _control(2, "note", None, at=_rig(40.0))]),
                          {4131: [12.0]}, [(_rig(0.0), 0.0), (_rig(60.0), 60.0)])
    assert timing.controls[0] == (12.0, "code")
    time, source = timing.controls[1]
    assert source == "rig_clock" and time == pytest.approx(40.0, abs=1e-6)
    clock = timing.clock
    assert (clock.n_matched, clock.drift_ppm, clock.rig_at_zero) == (3, pytest.approx(DRIFT_PPM, abs=0.01),
                                                                     pytest.approx(ZERO, abs=1e-5))
    assert timing.problems == ()


def test_the_rigs_clock_is_fitted_within_its_stated_jitter():
    """Spec section 10, item 10: instants stamped up to 5 ms either side of
    their codes (`synth/peripherals.py`'s `RIG_STAMP_JITTER_S`), over a short
    session and an hour, fit within the limit, the drift found to the ppm
    the jitter allows."""
    from wl_preproc.events.rigtiming import RIG_CLOCK_LIMIT_S, fit_rig_clock

    generator = random.Random(20271018)
    for duration_s, n in ((15.0, 4), (3600.0, 60)):
        times = sorted(generator.uniform(0.0, duration_s) for _ in range(n - 2)) + [0.0, duration_s]
        clock = fit_rig_clock([(_rig(time, generator.uniform(-0.005, 0.005)), time) for time in times])
        assert clock.residual_ms_max <= 10.0 < RIG_CLOCK_LIMIT_S * 1e3
        assert clock.drift_ppm == pytest.approx(DRIFT_PPM, abs=0.01 / duration_s * 1e6)


def test_a_rig_clock_re_anchored_mid_session_has_no_fit():
    """A resumed session's process anchors the rig's clock again; one line
    cannot follow the step, so there is no fit, and the controls that needed
    it say why."""
    from wl_preproc.events.rigtiming import time_records

    step = 2.0
    instants = [(_rig(0.0), 0.0), (_rig(30.0), 30.0), (_rig(40.0) + step, 40.0), (_rig(90.0) + step, 90.0)]
    timing = time_records(_records(controls=[_control(1, "note", None, at=_rig(50.0) + step),
                                             _control(2, "schedule", None, at=_rig(60.0) + step)]), {}, instants)
    assert timing.clock is None
    assert timing.controls == ((None, None), (None, None))
    (problem,) = timing.problems
    assert problem.startswith("controls.jsonl: 2 rows strobed nothing, and the rig's clock could not be fitted "
                              "to the recording (its instants miss their codes by up to ")
    assert problem.endswith(" s, past the 0.05 s limit), so they are not timed")


def test_fewer_than_two_matched_instants_is_no_fit():
    from wl_preproc.events.rigtiming import fit_rig_clock

    assert fit_rig_clock([]) == "none of its instants matched a code"
    assert fit_rig_clock([(_rig(3.0), 3.0)]) == "only 1 of its instants matched a code, and a fit needs 2"
    assert fit_rig_clock([(_rig(3.0), 3.0), (_rig(3.0) + 0.001, 3.0)]) == (
        "its instants matched codes at a single time")


def test_the_measured_runs_give_the_rigs_start_and_end_instants():
    """Each run's start row against its measured `RUN_START`; its end row
    against its `RUN_END`, only when the run closed: a run that faulted sent
    none, and its stop is its last event."""
    from wl_preproc.events.rigruns import RigRun
    from wl_preproc.events.rigtiming import run_instants

    runs = [RigRun(number=1, task=None, stopped_because=None, stop_kind=None, start_at=_rig(0.5), end_at=_rig(9.5)),
            RigRun(number=2, task=None, stopped_because=None, stop_kind=None, start_at=_rig(10.0),
                   end_at=_rig(15.0)),
            RigRun(number=3, task=None, stopped_because=None, stop_kind=None, start_at=_rig(16.0), end_at=None),
            RigRun(number=4, task=None, stopped_because=None, stop_kind=None, start_at=_rig(20.0),
                   end_at=_rig(25.0))]
    measured = {1: (0.5, 9.5), 2: (10.0, None), 3: (16.0, 18.0)}
    assert run_instants(runs, measured) == [(_rig(0.5), 0.5), (_rig(9.5), 9.5), (_rig(10.0), 10.0),
                                            (_rig(16.0), 16.0)]
