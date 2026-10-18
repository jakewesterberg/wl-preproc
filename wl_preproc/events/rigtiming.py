"""Placing wl-xcon's training records on the session's clock (design spec
`2026-10-18-training-records-and-backup-clock-design.md` section 3.3, amendment 2).

**Never guessed.** A row is timed by the code its act strobed, its
`event_code`: the n-th row of a file carrying a number takes the time of the
n-th occurrence of that number in the session's codes. A count that disagrees
leaves that number's rows in that file untimed, and one problem names both
counts. A code the recording lost reads so, as does one strobed with no row (a
`wlx run` session's `HEAD_RELEASED`, a manual reward's pump fault: wl-xcon's
corrections 2 and 3) and a task's own code with a framework code's number (its
XC-382). A row that does not say which code it strobed is untimed.

**The rig's clock fit.** wl-xcon stamps its run rows and its controls with its
anchored wall clock. Matched to their codes -- each run's start row to its
measured `RUN_START`, its end row to its `RUN_END` when it closed, and each
control timed by its code to that code -- they fit the rig's clock as a rate and
an offset, residuals kept. A control that strobed nothing (a note, a schedule,
a pause its allocation has no code for) is timed through the fit, and is
otherwise untimed. Fewer than two matches, a residual past `RIG_CLOCK_LIMIT_S`,
or a rate no clock runs at (`RIG_CLOCK_DRIFT_LIMIT_PPM`), and there is no fit:
a resumed session's process anchors the rig's clock again (wl-xcon's
`welfare.SessionClock`), which one line cannot follow.

Pure: no database and no file I/O, as `timebase/fit.py`."""

from __future__ import annotations

import collections
import dataclasses
from collections.abc import Iterable, Mapping, Sequence

from wl_preproc.contracts.events import SimpleEvent
from wl_preproc.events.rigrecords import CHANGES_NAME, CONTROLS_NAME, RigRecords
from wl_preproc.events.rigruns import RigRun

# The largest miss between a rig instant and its code that a fit may have
# (spec section 10, item 10): ten times the jitter the synthetic rig record
# states (`synth/peripherals.py::RIG_STAMP_JITTER_S`, 5 ms either side), under
# which the largest miss measured was 6.8 ms over 2000 synthetic sessions of
# 15 s to two hours. A step in the rig's clock of 2 s was refused in each of
# 1000 sessions, one of 0.5 s in 998 and one of 0.1 s in 819: a step at a
# session's last instants tilts the line instead, within the step.
RIG_CLOCK_LIMIT_S = 0.05
# The largest rate a fit may find, either way, in parts per million. A quartz
# oscillator is specified to within 100 ppm, and the synthetic rig runs 20 ppm
# fast; a rate further off is a step in the rig's clock that too few instants
# show as a rate rather than a miss: two always fit exactly (the final
# review's M2).
RIG_CLOCK_DRIFT_LIMIT_PPM = 1000


@dataclasses.dataclass(frozen=True, slots=True)
class RigClock:
    """The rig's anchored wall clock against session time:
    `rig_s = rig_at_zero + session_s * (1 + drift_ppm / 1e6)`."""

    rig_at_zero: float  # POSIX seconds on the rig's clock at session t = 0
    drift_ppm: float
    n_matched: int
    residual_ms_rms: float
    residual_ms_max: float

    def session_time(self, at: float) -> float:
        return (at - self.rig_at_zero) / (1.0 + self.drift_ppm / 1e6)


@dataclasses.dataclass(frozen=True, slots=True)
class Timing:
    """Each change's session time, in the file's order; each control's,
    with `code` or `rig_clock` for how it was found; the fit, if one was
    made; and what could not be timed, and why."""

    changes: tuple[float | None, ...]
    controls: tuple[tuple[float | None, str | None], ...]
    clock: RigClock | None
    problems: tuple[str, ...]


def code_times(decoded: Iterable) -> dict[int, list[float]]:
    """Every simple event's time, by its code, in the stream's order. A
    payload's words are not codes: `decode_stream` reads them by position."""
    times: dict[int, list[float]] = collections.defaultdict(list)
    for item in decoded:
        if isinstance(item, SimpleEvent):
            times[item.code].append(item.time_s)
    return dict(times)


def run_instants(runs: Iterable[RigRun],
                 measured: Mapping[int, tuple[float, float | None]]) -> list[tuple[float, float]]:
    """`(rig instant, session time)` for each measured run's start row, and
    its end row when the run closed. `measured` maps a run's number to its
    measured start and its `RUN_END`'s time, None for a run that faulted."""
    pairs = []
    for run in runs:
        if run.number not in measured:
            continue
        start_s, end_s = measured[run.number]
        if run.start_at is not None:
            pairs.append((run.start_at, start_s))
        if run.end_at is not None and end_s is not None:
            pairs.append((run.end_at, end_s))
    return pairs


def fit_rig_clock(pairs: Sequence[tuple[float, float]]) -> RigClock | str:
    """The rig's clock from `(rig instant, session time)` pairs, by centred
    least squares as `timebase/fit.py` fits a device, residuals in the
    session's frame; or why there is none, as a phrase."""
    n = len(pairs)
    if n == 0:
        return "none of its instants matched a code"
    if n < 2:
        return f"only {n} of its instants matched a code, and a fit needs 2"
    mean_rig = sum(rig for rig, _ in pairs) / n
    mean_session = sum(session for _, session in pairs) / n
    variance = sum((session - mean_session) ** 2 for _, session in pairs)
    if variance <= 0.0:
        return "its instants matched codes at a single time"
    scale = sum((session - mean_session) * (rig - mean_rig) for rig, session in pairs) / variance
    if scale <= 0.0:
        return "its instants do not advance with the recording's"
    rig_at_zero = mean_rig - scale * mean_session
    misses = [abs((rig - rig_at_zero) / scale - session) for rig, session in pairs]
    if max(misses) > RIG_CLOCK_LIMIT_S:
        return f"its instants miss their codes by up to {max(misses):.3f} s, past the {RIG_CLOCK_LIMIT_S} s limit"
    drift_ppm = (scale - 1.0) * 1e6
    if abs(drift_ppm) > RIG_CLOCK_DRIFT_LIMIT_PPM:
        return (f"its instants run {drift_ppm:+,.0f} ppm against the recording's, past the "
                f"{RIG_CLOCK_DRIFT_LIMIT_PPM:,} ppm a clock may")
    return RigClock(rig_at_zero=rig_at_zero, drift_ppm=drift_ppm, n_matched=n,
                    residual_ms_rms=(sum(miss * miss for miss in misses) / n) ** 0.5 * 1e3,
                    residual_ms_max=max(misses) * 1e3)


def _rows(n: int) -> str:
    return "1 row" if n == 1 else f"{n} rows"


def _join(rows: Sequence, codes: Mapping[int, list[float]], name: str) -> tuple[list[float | None], list[str]]:
    """Each row's time by its code, and the counts that disagree."""
    times: list[float | None] = [None] * len(rows)
    carrying: dict[int, list[int]] = collections.defaultdict(list)
    unsaid = 0
    for position, row in enumerate(rows):
        if not row.coded:
            unsaid += 1
        elif row.event_code is not None:
            carrying[row.event_code].append(position)
    problems = []
    for code, positions in sorted(carrying.items()):
        found = codes.get(code, [])
        if len(found) != len(positions):
            one = len(positions) == 1
            problems.append(f"{name}: {_rows(len(positions))} carr{'ies' if one else 'y'} code {code} and the "
                            f"recording holds {len(found)}, so {'it is not' if one else 'none of them is'} timed")
            continue
        for position, time in zip(positions, found, strict=True):
            times[position] = time
    if unsaid:
        one = unsaid == 1
        problems.append(f"{name}: {_rows(unsaid)} do{'es' if one else ''} not say which code {'it' if one else 'they'} "
                        f"strobed, so {'it is' if one else 'they are'} not timed")
    return times, problems


def time_records(records: RigRecords, codes: Mapping[int, list[float]],
                 instants: Sequence[tuple[float, float]]) -> Timing:
    """Each record's session time. `codes` is `code_times` of the session's
    decoded codes; `instants` is `run_instants` of its runs."""
    change_times, change_problems = _join(records.changes, codes, CHANGES_NAME)
    coded_times, control_problems = _join(records.controls, codes, CONTROLS_NAME)
    pairs = [*instants, *((control.at, time) for control, time in zip(records.controls, coded_times, strict=True)
                          if time is not None and control.at is not None)]
    fitted = fit_rig_clock(pairs)
    clock = fitted if isinstance(fitted, RigClock) else None
    controls, unfitted = [], 0
    for control, time in zip(records.controls, coded_times, strict=True):
        strobed_nothing = control.coded and control.event_code is None and control.at is not None
        if time is not None:
            controls.append((time, "code"))
        elif strobed_nothing and clock is not None:
            controls.append((clock.session_time(control.at), "rig_clock"))
        else:
            unfitted += strobed_nothing
            controls.append((None, None))
    problems = [*change_problems, *control_problems]
    if unfitted:
        one = unfitted == 1
        problems.append(f"{CONTROLS_NAME}: {_rows(unfitted)} strobed nothing, and the rig's clock could not be fitted "
                        f"to the recording ({fitted}), so {'it is' if one else 'they are'} not timed")
    return Timing(changes=tuple(change_times), controls=tuple(controls), clock=clock, problems=tuple(problems))
