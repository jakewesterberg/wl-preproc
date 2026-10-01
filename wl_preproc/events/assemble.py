# wl_preproc/events/assemble.py
"""Decoded events -> trials and measured blocks.

**Matching is by ID and never by ordinal position.** Spec section 4.2
requirement 1 is explicit about why every trial start carries an explicit
trial-number payload: "one dropped code must not shift every subsequent trial."
An implementation that counted TRIAL_START markers would pass every happy-path
test and silently renumber a whole session the first time a line glitched --
and the renumbering would be invisible, because the trial count would still
look plausible.

DecodeErrors are carried through rather than dropped. `decode_stream` "never
raises on malformed input... so one bad trial cannot lose a session", and a
session with decode errors is a tier-D candidate that silence would hide.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from wl_preproc.contracts.events import (
    DecodeError,
    DecodedEvent,
    Escape,
    Marker,
    PayloadEvent,
    SimpleEvent,
)

_OUTCOMES = {
    Marker.TRIAL_CORRECT: "correct",
    Marker.TRIAL_ERROR: "error",
    Marker.TRIAL_ABORT: "abort",
    Marker.TRIAL_FIXATION_BREAK: "fixation_break",
    Marker.TRIAL_NO_RESPONSE: "no_response",
}


@dataclass(frozen=True, slots=True)
class AssembledTrial:
    trial_id: int
    start_s: float
    end_s: float | None
    outcome: str | None


@dataclass(frozen=True, slots=True)
class AssembledBlock:
    block_id: int
    task_type: int
    start_s: float
    end_s: float | None  # its BLOCK_END, or None when it never closed
    # The last event received while it was open: its BLOCK_END when it closed,
    # otherwise the last code before the next BLOCK_START or the end of the
    # stream. For a run that faulted -- wl-xcon sends no BLOCK_END then -- it
    # is the tightest bound the recording gives on its end (design spec
    # `2026-10-01-runs-and-trials-design.md` section 2.3).
    last_s: float


@dataclass(frozen=True, slots=True)
class AssembledRun:
    """One run, measured as a block is: from `RUN_START` to `RUN_END`, or to
    its last event when it faulted and sent no `RUN_END` (design spec
    `2026-10-01-runs-and-trials-design.md` sections 2.1 and 2.3)."""

    run_number: int
    task_type: int
    start_s: float
    end_s: float | None
    last_s: float


@dataclass
class Assembly:
    trials: list[AssembledTrial] = field(default_factory=list)
    blocks: list[AssembledBlock] = field(default_factory=list)
    errors: list[DecodeError] = field(default_factory=list)
    runs: list[AssembledRun] = field(default_factory=list)


def _u32(words: tuple[int, ...]) -> int:
    """Two 16-bit words, high first -- contracts/events.py's own convention."""
    return (words[0] << 16) | words[1]


def assemble(events: list[DecodedEvent]) -> Assembly:
    """Trials and measured blocks from a decoded stream."""
    result = Assembly()

    open_trial_start: float | None = None
    open_trial_id: int | None = None
    open_outcome: str | None = None
    open_block: AssembledBlock | None = None
    open_run: AssembledRun | None = None
    last_s = 0.0  # the time of the last event before the one in hand

    def close_trial(end_s: float | None) -> None:
        nonlocal open_trial_start, open_trial_id, open_outcome
        if open_trial_id is not None and open_trial_start is not None:
            result.trials.append(
                AssembledTrial(
                    trial_id=open_trial_id,
                    start_s=open_trial_start,
                    end_s=end_s,
                    outcome=open_outcome,
                )
            )
        open_trial_start = open_trial_id = open_outcome = None

    for event in events:
        if isinstance(event, DecodeError):
            result.errors.append(event)
            continue

        if isinstance(event, PayloadEvent):
            if event.escape is Escape.TRIAL_NUMBER:
                # The ID arrives here, never from a running count. A TRIAL_START
                # whose payload was lost therefore yields NO trial rather than a
                # misnumbered one.
                open_trial_id = _u32(event.words)
                if open_trial_start is None:
                    open_trial_start = event.time_s
            elif event.escape is Escape.BLOCK_START:
                if open_block is not None:
                    result.blocks.append(replace(open_block, last_s=last_s))
                open_block = AssembledBlock(
                    block_id=event.words[0],
                    task_type=event.words[1],
                    start_s=event.time_s,
                    end_s=None,
                    last_s=event.time_s,
                )
            elif event.escape is Escape.RUN_START:
                if open_run is not None:
                    result.runs.append(replace(open_run, last_s=last_s))
                open_run = AssembledRun(run_number=event.words[0], task_type=event.words[1], start_s=event.time_s,
                                        end_s=None, last_s=event.time_s)
            last_s = event.time_s
            continue

        if isinstance(event, SimpleEvent):
            try:
                marker = Marker(event.code)
            except ValueError:
                last_s = event.time_s
                continue  # a task event, not a marker; Event rows keep it
            if marker is Marker.TRIAL_START:
                close_trial(end_s=None)
                open_trial_start = event.time_s
            elif marker in _OUTCOMES:
                open_outcome = _OUTCOMES[marker]
            elif marker is Marker.TRIAL_END:
                close_trial(end_s=event.time_s)
            elif marker is Marker.BLOCK_END and open_block is not None:
                result.blocks.append(replace(open_block, end_s=event.time_s, last_s=event.time_s))
                open_block = None
            elif marker is Marker.RUN_END and open_run is not None:
                result.runs.append(replace(open_run, end_s=event.time_s, last_s=event.time_s))
                open_run = None
            last_s = event.time_s

    close_trial(end_s=None)
    if open_block is not None:
        result.blocks.append(replace(open_block, last_s=last_s))
    if open_run is not None:
        result.runs.append(replace(open_run, last_s=last_s))
    return result
