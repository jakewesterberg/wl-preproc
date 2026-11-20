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

Pure: no DataJoint, no I/O, as `timebase/coverage.py` is."""

from __future__ import annotations

from wl_preproc.contracts.events import DecodedEvent, Marker, SimpleEvent
from wl_preproc.events.assemble import assemble
from wl_preproc.events.rigruns import RigRuns
from wl_preproc.events.rigtrials import RigRecord


def unheld(decoded: list[DecodedEvent], rig_runs: RigRuns | None, rig_trials: RigRecord | None) -> str | None:
    """None when `decoded` holds every run the rig recorded, else one
    sentence saying the first thing it does not hold, e.g. "run 3's trial 41
    is not in its codes"."""
    named = rig_runs.runs if rig_runs is not None else ()
    if not named:
        markers = {event.code for event in decoded if isinstance(event, SimpleEvent)}
        for marker in (Marker.SESSION_START, Marker.SESSION_END):
            if marker not in markers:
                return f"its codes hold no {marker.name}"
        return None

    assembly = assemble(decoded)
    runs = {run.run_number for run in assembly.runs}
    trials: dict[int, bool] = {}
    for trial in assembly.trials:
        trials[trial.trial_id] = trials.get(trial.trial_id, False) or trial.end_s is not None
    # wl-xcon's `run`, from 0, names a run in both files; the run's number is
    # what `RUN_START` strobes.
    numbers = {run.index: run.number for run in named if run.index is not None}
    given: dict[int, list[int]] = {}
    for trial in rig_trials.trials if rig_trials is not None else ():
        if trial.run in numbers:
            given.setdefault(numbers[trial.run], []).append(trial.number)
    for run in sorted(named, key=lambda run: run.number):
        if run.number not in runs:
            return f"run {run.number} is not in its codes"
        for number in given.get(run.number, ()):
            if number not in trials:
                return f"run {run.number}'s trial {number} is not in its codes"
            if not trials[number]:
                return f"run {run.number}'s trial {number} does not end in its codes"
    return None
