"""Whether a code stream holds every run the rig recorded: what decides which
clock keeps a session's time (design spec
`2026-10-18-training-records-and-backup-clock-design.md` section 4.2,
amendments 41 and 44; `timebase/clock.py::choose`).

**With the rig's record,** a stream holds the session when every run the
record names (`events/rigruns.py::RigRun.number`) is a run of that number in
the stream's assembly, and every trial the rig's trial lines give for that run
(`events/rigtrials.py`, joined by wl-xcon's `run`) is an assembled trial of
that number that closes. A run missing only its `RUN_END` is held: a faulted
run sends none (amendment 41). The session's start and end codes are not
required, since the rig may strobe them before SpikeGLX starts or after it
stops (amendment 44). A trial line naming no run of the record is not counted
against any run.

**With no rig record** (none, or one naming no run), the stream holds the
session when it decodes both `SESSION_START` and `SESSION_END`.

**The sync box needs only to reach the end of the session's last run**
(`unreached_end`, amendment 42): the NI keeps time only when the box's log
ends before that run does, with no later segment; a gap within the session
keeps the box.

Pure: no DataJoint, no I/O, as `timebase/coverage.py` is."""

from __future__ import annotations

from wl_preproc.contracts.events import DecodedEvent, Marker, SimpleEvent
from wl_preproc.events.assemble import assemble
from wl_preproc.events.rigruns import RigRuns
from wl_preproc.events.rigtrials import RigRecord


def unheld(decoded: list[DecodedEvent], rig_runs: RigRuns | None, rig_trials: RigRecord | None) -> str | None:
    """None when `decoded` holds every run the rig recorded, else one
    sentence saying the first thing it does not hold, e.g. "run 3's trial 41
    is not in its codes". Rule 2's test, the NI's (amendment 44)."""
    named = rig_runs.runs if rig_runs is not None else ()
    if not named:
        return _markers_unheld(decoded, (Marker.SESSION_START, Marker.SESSION_END))
    assembly = assemble(decoded)
    runs = {run.run_number for run in assembly.runs}
    trials = _closed(assembly)
    given = _trial_lines(named, rig_trials)
    for run in sorted(named, key=lambda run: run.number):
        if run.number not in runs:
            return f"run {run.number} is not in its codes"
        for number in given.get(run.number, ()):
            sentence = _trial_unheld(run.number, number, trials)
            if sentence is not None:
                return sentence
    return None


def unreached_end(decoded: list[DecodedEvent], rig_runs: RigRuns | None, rig_trials: RigRecord | None) -> str | None:
    """None when `decoded` reaches the end of the session's last run, else
    one sentence saying what of it is missing. Rule 1's test, the sync
    box's (amendment 42: the NI keeps time only when its log "ends before the
    session's last run does"). A log that decodes `SESSION_END` did not end
    before it, nor, with the rig's record, one that decodes the last run's
    `RUN_END`, whatever was lost or failed its checksum before them (the
    final review's I1). Otherwise, with the rig's record, the last run is
    the one of the highest number it names: its last trial line must be an
    assembled trial that closes (a missing `RUN_END` allowed), or, with no
    trial lines, the run must be present. With none, `SESSION_END` must be
    decoded. A gap before the last run, a log that starts late, or a gap
    inside the last run that resumes before its last trial reach its end."""
    named = rig_runs.runs if rig_runs is not None else ()
    unended = _markers_unheld(decoded, (Marker.SESSION_END,))
    if not named or unended is None:
        return unended
    last = max(named, key=lambda run: run.number)
    assembly = assemble(decoded)
    if any(run.run_number == last.number and run.end_s is not None for run in assembly.runs):
        return None
    lines = _trial_lines(named, rig_trials).get(last.number, [])
    if lines:
        return _trial_unheld(last.number, lines[-1], _closed(assembly))
    if last.number not in {run.run_number for run in assembly.runs}:
        return f"run {last.number} is not in its codes"
    return None


def _markers_unheld(decoded: list[DecodedEvent], markers: tuple[Marker, ...]) -> str | None:
    decoded_markers = {event.code for event in decoded if isinstance(event, SimpleEvent)}
    return next((f"its codes hold no {marker.name}" for marker in markers if marker not in decoded_markers), None)


def _trial_lines(named, rig_trials: RigRecord | None) -> dict[int, list[int]]:
    """Each named run's trial numbers, in its trial lines' order, joined by
    wl-xcon's `run`, from 0, which names a run in both files; the run's
    number is what `RUN_START` strobes."""
    numbers = {run.index: run.number for run in named if run.index is not None}
    given: dict[int, list[int]] = {}
    for trial in rig_trials.trials if rig_trials is not None else ():
        if trial.run in numbers:
            given.setdefault(numbers[trial.run], []).append(trial.number)
    return given


def _closed(assembly) -> dict[int, bool]:
    """Each assembled trial's number: whether one of that number closes."""
    trials: dict[int, bool] = {}
    for trial in assembly.trials:
        trials[trial.trial_id] = trials.get(trial.trial_id, False) or trial.end_s is not None
    return trials


def _trial_unheld(run: int, number: int, trials: dict[int, bool]) -> str | None:
    if number not in trials:
        return f"run {run}'s trial {number} is not in its codes"
    if not trials[number]:
        return f"run {run}'s trial {number} does not end in its codes"
    return None
