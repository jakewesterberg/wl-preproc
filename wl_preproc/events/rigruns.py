"""The rig's own record of every run: wl-xcon's `xcon/runs.jsonl` (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 2.3).

**The recording measures a run; the rig's record names it.** `core.Run` is
measured from the run's `RUN_START` and `RUN_END`; the task that ran and why it
stopped are in wl-xcon's record. `wl_xcon/record.py::run_row` writes one row
per event, `{"event": "start" | "end", "run": <0-based run in session>, ...}`:
the start row carries the `task`, the end row `stopped_because` and
`stop_kind`. Since wl-xcon's session-levels change a start row also carries
`run_in_session`, the number `RUN_START` strobes; before it, that number is
`run` + 1. A start row and its end row share `run`. Since wl-xcon's engine
build B a start row also names the display calibration the run ran against
(design spec `2026-10-14-session-kind-and-warnings-design.md` section 1).

**Read, never repaired.** A row that is not a JSON object naming its event and
run is reported by file and line and left out: the file named, since the rig's
record is more than its runs (design spec
`2026-10-14-session-kind-and-warnings-design.md`, amendment 2). wl-xcon writes a run's end row on every
way out, a fault included (`stop_kind` "fault"); only a killed process leaves
none, and that run's stop reason is absent, not guessed.

**And what the rows say beyond these** (design spec
`2026-11-07-rig-record-fields-design.md` section 5): since wl-xcon's engine
build C2b, the start row's plan and the plan as declared, and the end row's
reason a completed run ended and how each block ended (`block_ends`); since
C2a, the end row's trials and presentations, what each condition left unrun
(`shortfalls`) and each condition's counts (`conditions`); since C3, the
start row's declared draws, the conditions a factorial generated, and the
settings that place a direction; and the task's content hash and the run's
seed. Each is optional and read by `events/rigfields.py`'s rule: absent, it
reads as before; unreadable, it is none, said once per field.

**One record is one wl-xcon session, which is one animal** (wl-xcon's own
vocabulary). Its rows carry no subject, so the session's `config.json` is
checked instead: a record whose `config.json` names another subject is not
read at all.
"""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path

from wl_preproc.contracts.paths import XCON_DIRNAME
from wl_preproc.events.rigfields import ANY, BOOLEAN, MAPPING, OBJECTS, TEXT, TEXTS, WHOLE, Fields

RECORD_NAME = "runs.jsonl"
CONFIG_NAME = "config.json"


@dataclasses.dataclass(frozen=True, slots=True)
class BlockEnd:
    """How one block the run opened ended (wl-xcon's engine build C2b): its
    type, `ended` (`rule`, `time`, `fall_back`, `next`, `back`, `run_time`,
    `run`), who moved it by kind, the run's last trial before it ended (an
    index in the run, from 0), its number in the stream (None for a block that
    ran no trial), its place in the run's sequence, and, for a training
    progression's top stage, the trial after which its rule was first met; and
    since C3 what varied in it, its planned sequence and its counts, as
    written."""

    block: str | None
    ended: str | None
    by_kind: str | None
    after_trial: int | None
    block_in_session: int | None
    position: int | None
    passed_at: int | None
    varied: object = None
    planned: object = None
    counts: object = None


@dataclasses.dataclass(frozen=True, slots=True)
class Shortfall:
    """What a condition left unrun, and why (`cap`, `rig`, `cleared`, `time`,
    `run_time`, `designed`); `after_trial` an index in the run."""

    block: str | None
    condition: str | None
    trials: int | None
    because: str | None
    possible_avoidance: bool | None
    after_trial: int | None
    by_kind: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class ConditionCount:
    """One condition's counts in one block of the run, as the end row writes
    them (wl-xcon's engine build C2a)."""

    block: str | None
    condition: str | None
    trials: int | None
    presentations: int | None
    right: int | None
    wrong: int | None
    right_first: int | None
    owed: int | None
    repeats: int | None
    moved_past: int | None
    possible_avoidance: bool | None
    drummed: int | None
    failing: int | None


_COUNTS = ("trials", "presentations", "right", "wrong", "right_first", "owed", "repeats", "moved_past", "drummed",
           "failing")


def _by_kind(entry: dict, fields: Fields, field: str) -> str | None:
    by = fields.read(entry, "by", MAPPING, field=f"{field}.by")
    return None if by is None else fields.read(by, "kind", TEXT, field=f"{field}.by.kind")


def _entries(row: dict, name: str, fields: Fields) -> list[dict]:
    return fields.read(row, name, OBJECTS) or []


def _block_ends(row: dict, fields: Fields) -> tuple[BlockEnd, ...]:
    field = "block_ends[]"
    return tuple(BlockEnd(
        block=fields.read(entry, "block", TEXT, field=f"{field}.block"),
        ended=fields.read(entry, "ended", TEXT, field=f"{field}.ended"), by_kind=_by_kind(entry, fields, field),
        **{name: fields.read(entry, name, WHOLE, field=f"{field}.{name}")
           for name in ("after_trial", "block_in_session", "position", "passed_at")},
        varied=entry.get("varied"), planned=entry.get("planned"), counts=entry.get("counts"))
        for entry in _entries(row, "block_ends", fields))


def _shortfalls(row: dict, fields: Fields) -> tuple[Shortfall, ...]:
    field = "shortfalls[]"
    return tuple(Shortfall(
        block=fields.read(entry, "block", TEXT, field=f"{field}.block"),
        condition=fields.read(entry, "condition", TEXT, field=f"{field}.condition"),
        trials=fields.read(entry, "trials", WHOLE, field=f"{field}.trials"),
        because=fields.read(entry, "because", TEXT, field=f"{field}.because"),
        possible_avoidance=fields.read(entry, "possible_avoidance", BOOLEAN, field=f"{field}.possible_avoidance"),
        after_trial=fields.read(entry, "after_trial", WHOLE, field=f"{field}.after_trial"),
        by_kind=_by_kind(entry, fields, field))
        for entry in _entries(row, "shortfalls", fields))


def _conditions(row: dict, fields: Fields) -> tuple[ConditionCount, ...]:
    field = "conditions[]"
    return tuple(ConditionCount(
        block=fields.read(entry, "block", TEXT, field=f"{field}.block"),
        condition=fields.read(entry, "condition", TEXT, field=f"{field}.condition"),
        possible_avoidance=fields.read(entry, "possible_avoidance", BOOLEAN, field=f"{field}.possible_avoidance"),
        **{name: fields.read(entry, name, WHOLE, field=f"{field}.{name}") for name in _COUNTS})
        for entry in _entries(row, "conditions", fields))


@dataclasses.dataclass(frozen=True, slots=True)
class RigRun:
    number: int  # the run's number in the session, as RUN_START strobes it
    task: str | None
    stopped_because: str | None
    stop_kind: str | None
    calibration: str | None = None  # the start row's display calibration id
    # Its start and end rows' instants on the rig's anchored wall clock, POSIX
    # seconds: what the training records' clock fit matches to the run's codes
    # (design spec `2026-10-18-training-records-and-backup-clock-design.md`
    # section 3.3). None where a row has none.
    start_at: float | None = None
    end_at: float | None = None
    # wl-xcon's `run`, from 0: how the rows name it, not what it is, so it is
    # not compared, nor is `carries` below.
    index: int | None = dataclasses.field(default=None, compare=False)
    # The start row's, since C2b and C3 (`plan_form`, `draws` and `generated`
    # as written).
    plan: str | None = None
    plan_form: object = None
    task_hash: str | None = None  # `versions.task` where it is the task's content hash (`_content_hash`)
    seed: int | None = None
    draws: object = None
    generated: object = None
    directions: tuple[str, ...] = ()
    # The end row's, since C2a and C2b.
    rig_trials: int | None = None  # a trial and its drummed repeats one
    presentations: int | None = None  # its trial lines
    end_reason: str | None = None
    block_ends: tuple[BlockEnd, ...] = ()
    shortfalls: tuple[Shortfall, ...] = ()
    conditions: tuple[ConditionCount, ...] = ()
    # The optional fields its rows carry, by name; `stopped_because` and
    # `stop_kind` when it has an end row.
    carries: frozenset[str] = dataclasses.field(default=frozenset(), compare=False)


@dataclasses.dataclass(frozen=True, slots=True)
class RigRuns:
    runs: tuple[RigRun, ...]
    problems: tuple[str, ...]
    # wl-xcon's 0-based `run` -> the run's number, which joins an accepted
    # warning, named by the former, to the measured run: for runs with a start
    # row only. A run whose start line was lost keeps its stop reason by `run`
    # + 1, a guess no warning is joined by, since wl-xcon's numbers part from
    # it once a session resumes.
    run_numbers: dict[int, int] = dataclasses.field(default_factory=dict)


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _instant(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _content_hash(version: str | None) -> str | None:
    """A start row's `versions.task` where it is the task's content hash,
    `sha256:` and its digest (wl-xcon's `declarations.content_hash`); from
    2026-09-29 until wl-xcon's engine build C1 (`ef23b25`, 2026-10-11) it was
    the task's path, which is no hash (the final review's I2)."""
    return version if version is not None and version.startswith("sha256:") else None


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
    optional = Fields(("run row", "run rows"))
    for number_of_line, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"{RECORD_NAME} line {number_of_line}: not JSON")
            continue
        if not isinstance(row, dict) or row.get("event") not in ("start", "end") or not _integer(row.get("run")):
            problems.append(f"{RECORD_NAME} line {number_of_line}: not a start or end row naming its run")
            continue
        # A start row and its end row share `run`; only the start row is
        # sure to carry `run_in_session`, so the number is taken from it. A
        # second row of either kind for one run is reported, and the first
        # kept, as everywhere a number repeats here.
        run = fields.setdefault(row["run"], {"number": row["run"] + 1, "index": row["run"], "carries": set()})
        if row["event"] in run.setdefault("seen", set()):
            problems.append(f"{RECORD_NAME} line {number_of_line}: run {row['run']} has a second {row['event']} row; "
                            "the first is kept")
            continue
        run["seen"].add(row["event"])
        if row["event"] == "start":
            run["task"] = _text(row.get("task"))
            run["calibration"] = _text(row.get("calibration"))
            run["start_at"] = _instant(row.get("at"))
            if _integer(row.get("run_in_session")):
                run["number"] = row["run_in_session"]
            versions = optional.read(row, "versions", MAPPING) or {}
            task_hash = _content_hash(optional.read(versions, "task", TEXT, field="versions.task"))
            run.update(plan=optional.read(row, "plan", TEXT), plan_form=optional.read(row, "plan_form", ANY),
                       task_hash=task_hash,
                       seed=optional.read(row, "seed", WHOLE), draws=optional.read(row, "draws", ANY),
                       generated=optional.read(row, "generated", ANY),
                       directions=optional.read(row, "directions", TEXTS))
            run["carries"] |= {name for name in ("plan", "plan_form", "seed", "draws", "generated", "directions")
                               if name in row} | ({"task_hash"} if task_hash is not None else set())
        else:
            run["stopped_because"] = _text(row.get("stopped_because"))
            run["stop_kind"] = _text(row.get("stop_kind"))
            run["end_at"] = _instant(row.get("at"))
            run.update(rig_trials=optional.read(row, "trials", WHOLE),
                       presentations=optional.read(row, "presentations", WHOLE),
                       end_reason=optional.read(row, "end_reason", TEXT), block_ends=_block_ends(row, optional),
                       shortfalls=_shortfalls(row, optional), conditions=_conditions(row, optional))
            run["carries"] |= {"stopped_because", "stop_kind"} | {
                {"trials": "rig_trials"}.get(name, name)
                for name in ("trials", "presentations", "end_reason", "block_ends", "shortfalls", "conditions")
                if name in row}
    runs = tuple(sorted((RigRun(**{"task": None, "stopped_because": None, "stop_kind": None,
                                   **{name: value for name, value in run.items() if name not in ("seen", "carries")}},
                                carries=frozenset(run["carries"]))
                         for run in fields.values()), key=lambda run: run.number))
    return RigRuns(runs=runs, problems=(*problems, *optional.problems()),
                   run_numbers={index: run["number"] for index, run in sorted(fields.items())
                                if "start" in run["seen"]})
