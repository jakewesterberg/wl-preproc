"""The rig's own record of every run: wl-xcon's `xcon/runs.jsonl` (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 2.3).

**The recording measures a run; the rig's record names it.** `core.Run` is
measured from the run's `RUN_START` and `RUN_END`; the task that ran and why it
stopped are in wl-xcon's record. `wl_xcon/record.py::run_row` writes one row
per event, `{"event": "start" | "end", "run": <0-based run in session>, ...}`:
the start row carries the `task`, the end row `stopped_because` and
`stop_kind`. Since wl-xcon's session-levels change a start row also carries
`run_in_session`, the number `RUN_START` strobes; before it, that number is
`run` + 1. A start row and its end row share `run`.

**Read, never repaired.** A row that is not a JSON object naming its event and
run is reported by line and left out. wl-xcon writes a run's end row on every
way out, a fault included (`stop_kind` "fault"); only a killed process leaves
none, and that run's stop reason is absent, not guessed.

**One record is one wl-xcon session, which is one animal** (wl-xcon's own
vocabulary). Its rows carry no subject, so the session's `config.json` is
checked instead: a record whose `config.json` names another subject is not
read at all.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from wl_preproc.contracts.paths import XCON_DIRNAME

RECORD_NAME = "runs.jsonl"
CONFIG_NAME = "config.json"


@dataclasses.dataclass(frozen=True, slots=True)
class RigRun:
    number: int  # the run's number in the session, as RUN_START strobes it
    task: str | None
    stopped_because: str | None
    stop_kind: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class RigRuns:
    runs: tuple[RigRun, ...]
    problems: tuple[str, ...]


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def read_rig_runs(session_dir: Path, subject: str) -> RigRuns | None:
    """This session's runs from `<session>/xcon/runs.jsonl`, or `None` when
    the session has no such record."""
    xcon = Path(session_dir) / XCON_DIRNAME
    path = xcon / RECORD_NAME
    if not path.is_file():
        return None
    try:
        recorded = json.loads((xcon / CONFIG_NAME).read_text(encoding="utf-8")).get("subject")
    except (OSError, ValueError, AttributeError):
        recorded = None
    if recorded is not None and recorded != subject:
        return RigRuns(runs=(), problems=(f"{CONFIG_NAME} names subject {recorded!r}, not {subject!r}, so "
                                          "its runs are not this session's",))
    fields: dict[int, dict] = {}
    problems = []
    for number_of_line, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {number_of_line}: not JSON")
            continue
        if not isinstance(row, dict) or row.get("event") not in ("start", "end") or not _integer(row.get("run")):
            problems.append(f"line {number_of_line}: not a start or end row naming its run")
            continue
        # A start row and its end row share `run`; only the start row is
        # sure to carry `run_in_session`, so the number is taken from it.
        run = fields.setdefault(row["run"], {"number": row["run"] + 1})
        if row["event"] == "start":
            run["task"] = _text(row.get("task"))
            if _integer(row.get("run_in_session")):
                run["number"] = row["run_in_session"]
        else:
            run["stopped_because"] = _text(row.get("stopped_because"))
            run["stop_kind"] = _text(row.get("stop_kind"))
    runs = tuple(sorted((RigRun(number=run["number"], task=run.get("task"),
                                stopped_because=run.get("stopped_because"), stop_kind=run.get("stop_kind"))
                         for run in fields.values()), key=lambda run: run.number))
    return RigRuns(runs=runs, problems=tuple(problems))
