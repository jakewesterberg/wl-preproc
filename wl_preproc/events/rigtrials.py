"""The rig's own record of every trial: wl-xcon's `xcon/trials.jsonl`
(design spec `2026-09-29-nwb-publishing-design.md` section 2.1).

**Codes own identity and timing; the rig's record owns content.** wl-xcon's own
allocation rule, verbatim: "Event codes carry identity and timing. The session
record carries content." (wl-xcon's S2 event-vocabulary design.) Each line of
the record is one trial -- `wl_xcon/record.py::Recorder.trial` -- with its
`index`, `subject`, `outcome`, the scheduler's `block` and `condition` names,
and `params`, "the whole resolved parameter set, per trial": the stimulus
settings it actually ran with.

**Read, never repaired.** A line that is not a JSON object with those fields is
reported as a problem, by line number, and left out; nothing is guessed. Two
animals routinely share one day's session directory, so the record carries the
subject on every line and only this subject's lines are kept.

**A record that numbers trials within each run is not read at all.** Since
wl-xcon's slice b3a-1 a session holds several runs, each line names its run,
and each run counts its trials from 0; the stream's `TRIAL_NUMBER` is numbered
across the session instead (wl-xcon XC-155). A per-run `index` is then not the
join key even where it is unique, so no line is read until this module reads
the key XC-155 records.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from wl_preproc.contracts.paths import XCON_DIRNAME

RECORD_NAME = "trials.jsonl"
_FIELDS = ("index", "subject", "outcome", "block", "condition", "params")


@dataclasses.dataclass(frozen=True, slots=True)
class RigTrial:
    index: int
    outcome: str
    block: str
    condition: str
    params: dict


@dataclasses.dataclass(frozen=True, slots=True)
class RigRecord:
    trials: tuple[RigTrial, ...]
    problems: tuple[str, ...]


def read_rig_trials(session_dir: Path, subject: str) -> RigRecord | None:
    """This subject's trials from `<session>/xcon/trials.jsonl`, or `None`
    when the session has no such record."""
    path = Path(session_dir) / XCON_DIRNAME / RECORD_NAME
    if not path.is_file():
        return None
    trials, problems, per_run = [], [], False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {number}: not JSON")
            continue
        if not isinstance(row, dict) or any(field not in row for field in _FIELDS):
            problems.append(f"line {number}: missing one of {', '.join(_FIELDS)}")
            continue
        if not isinstance(row["index"], int) or isinstance(row["index"], bool) or not isinstance(row["params"], dict):
            problems.append(f"line {number}: index is not an integer or params is not an object")
            continue
        if row["subject"] != subject:
            continue
        per_run = per_run or "run" in row
        trials.append(RigTrial(index=row["index"], outcome=str(row["outcome"]), block=str(row["block"]),
                               condition=str(row["condition"]), params=row["params"]))
    if per_run:
        return RigRecord(trials=(), problems=(*problems, (
            "the rig record numbers trials within each run (its lines name a run) and the stream "
            "numbers them across the session (wl-xcon XC-155), so no line is joined")))
    return RigRecord(trials=tuple(trials), problems=tuple(problems))
