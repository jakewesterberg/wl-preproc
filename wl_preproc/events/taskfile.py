# wl_preproc/events/taskfile.py
"""Reading the task file, behind a seam, because the stack is unchosen.

Spec section 4.2 states it outright: `acquisitionBuildId` "is a content hash of
a free-text {component: version} set, deliberately assuming no git -- so
{matlab, psychtoolbox, wl-bhvtask} and {bonsai, workflow} are the same shape.
The behavioural stack is unchosen and the design must not presume a resolvable
commit." The synthetic fixture says the same: it "stands in for MonkeyLogic's
.bhv2 until the task stack is chosen."

So this is a Protocol with one implementation today. A real `.bhv2` reader is a
SECOND implementation rather than a rewrite.

**What the seam is allowed to answer is deliberately narrow.** Codes own
timing; the task file owns parameters (spec section 2). So the protocol returns
ids, conditions and outcomes -- and NOT trial intervals. A reader that returned
times would invite someone to prefer them over the recorded strobe edges, which
is exactly the inversion section 4.2 requirement 5 forbids: the recorded edge
establishes true event time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class UnsupportedTaskFile(ValueError):
    """A task file this reader does not understand.

    Refused rather than parsed on a best-effort basis: a wrong trial list does
    not fail loudly, it disagrees with the codes -- and section 2 makes that
    disagreement a hard failure, which only works if the list is trustworthy.
    """


@dataclass(frozen=True, slots=True)
class TaskTrial:
    trial_id: int
    block_id: int
    condition: int
    outcome: str


class TaskFileReader(Protocol):
    """What this phase needs from a task file, and nothing else."""

    def trials(self, path: Path) -> list[TaskTrial]: ...


class SyntheticTaskFileReader:
    """Reads `synth/peripherals.py::write_task_file`'s format."""

    FORMAT = "synthetic-task-file"

    def trials(self, path: Path) -> list[TaskTrial]:
        payload = json.loads(path.read_text())
        declared = payload.get("format")
        if declared != self.FORMAT:
            raise UnsupportedTaskFile(
                f"{path} declares format {declared!r}; this reader handles "
                f"{self.FORMAT!r} only. A real .bhv2 reader is a second "
                "implementation of TaskFileReader, not a widening of this one."
            )
        return [
            TaskTrial(
                trial_id=int(t["trial_id"]),
                block_id=int(t["block_id"]),
                condition=int(t["condition"]),
                outcome=str(t["outcome"]),
            )
            for t in payload["trials"]
        ]
