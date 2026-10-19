"""wl-xcon's training records as it writes them today: `xcon/parameter_changes.jsonl`,
one row per live change the rig applied, and `xcon/controls.jsonl`, one row per
console control (design spec `2026-10-18-training-records-and-backup-clock-design.md`
section 3.1; wl-xcon's `wl_xcon/record.py`, `parameter_change` and `control`).

**Kept as written.** Each row's line is kept whole, and its values as JSON text,
so a field wl-xcon adds (XC-288's change id, instant, trial, meaning and
direction) is kept before any reader here knows it. Who acted is kept as
`core.AcceptedWarning` keeps it.

**Read, never repaired, by `events/rigruns.py`'s rules.** A line that is not a
JSON object naming its setting (a change) or its kind (a control) is reported by
file and line and left out. A value that is not what its field holds (a count
that is not a whole number, an instant that is not a finite number) is read as
none.

**`event_code`, the number the row's act strobed** (spec amendment 2): an
integer, or null when it strobed nothing. A row written before wl-xcon carried
it has no key, which is read as unknown, never as null; so is a value that is
not a 16-bit code. `events/rigtiming.py` joins the rows to the recording by it.

**This session's only:** the caller reads these for a session whose
`config.json` is its own (`events/rigsession.py`'s `of_this_session`)."""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path

from wl_preproc.contracts.paths import XCON_DIRNAME

CHANGES_NAME = "parameter_changes.jsonl"
CONTROLS_NAME = "controls.jsonl"
# What every control row carries (`record.py::control`, its `run` passed by
# `taskd.py::_control`); the rest of a row is the control's own detail.
_CONTROL_FIELDS = frozenset({"kind", "by", "at", "at_local", "trial_index", "event_code", "run"})
_COUNT_MAX = 2**32 - 1  # an `int unsigned` column
_CODE_MAX = 0xFFFF


@dataclasses.dataclass(frozen=True, slots=True)
class ParameterChange:
    """One row of `parameter_changes.jsonl`. `was` and `now` are JSON text,
    None when the row has no such key. `coded` is False when the row does not
    say which code it strobed."""

    line: int
    sequence: int | None
    name: str
    was: str | None
    now: str | None
    run_index: int | None
    event_code: int | None
    coded: bool
    by_kind: str | None
    by_name: str | None
    by_account: str | None
    by_issuer: str | None
    by_token_id: str | None
    row: str


@dataclasses.dataclass(frozen=True, slots=True)
class Control:
    """One row of `controls.jsonl`. `at` is the rig's anchored wall clock,
    POSIX seconds; `trial_index` is wl-xcon's trial within the run, from 0,
    not a trial number (`taskd.py` counts from 0 at each run's start);
    `detail` is the row's own fields as JSON text."""

    line: int
    kind: str
    at: float | None
    at_local: str | None
    trial_index: int | None
    run_index: int | None
    event_code: int | None
    coded: bool
    detail: str
    by_kind: str | None
    by_name: str | None
    by_account: str | None
    by_issuer: str | None
    by_token_id: str | None
    row: str


@dataclasses.dataclass(frozen=True, slots=True)
class RigRecords:
    changes: tuple[ParameterChange, ...]
    controls: tuple[Control, ...]
    problems: tuple[str, ...]


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _count(value: object) -> int | None:
    number = _integer(value)
    return number if number is not None and 0 <= number <= _COUNT_MAX else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _json(value: object) -> str:
    """The value's JSON text, keys sorted and ASCII: a lone UTF-16 surrogate
    (half an emoji, from a `\\ud83d` escape) stays escaped, since utf8mb4
    cannot store it (the final review's I1)."""
    return json.dumps(value, sort_keys=True)


def _code(row: dict) -> tuple[int | None, bool]:
    """The row's code and whether it says one: `(None, True)` for a row whose
    act strobed nothing, `(None, False)` for one that does not say."""
    if "event_code" not in row:
        return None, False
    value = row["event_code"]
    if value is None:
        return None, True
    code = _integer(value)
    return (code, True) if code is not None and 1 <= code <= _CODE_MAX else (None, False)


def _actor(row: dict) -> dict:
    by = row.get("by") if isinstance(row.get("by"), dict) else {}
    return {f"by_{field}": _text(by.get(field)) for field in ("kind", "name", "account", "issuer", "token_id")}


def _rows(path: Path, problems: list[str], field: str, what: str) -> list[tuple[int, str, dict]]:
    """Each line of `path` that is a JSON object naming its `field`, with its
    number and its text; each other line is a problem, `what` saying what it
    is not."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    rows = []
    for number_of_line, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"{path.name} line {number_of_line}: not JSON")
            continue
        if not isinstance(row, dict) or not isinstance(row.get(field), str) or not row[field]:
            problems.append(f"{path.name} line {number_of_line}: {what}")
            continue
        rows.append((number_of_line, line, row))
    return rows


def read_rig_records(session_dir: Path) -> RigRecords:
    """The session's training records from `<session>/xcon/`; none where a
    file is absent."""
    xcon = Path(session_dir) / XCON_DIRNAME
    problems: list[str] = []
    changes = []
    for line, text, row in _rows(xcon / CHANGES_NAME, problems, "name", "not a change naming its setting"):
        code, coded = _code(row)
        changes.append(ParameterChange(
            line=line, sequence=_count(row.get("sequence")), name=row["name"],
            was=_json(row["was"]) if "was" in row else None, now=_json(row["now"]) if "now" in row else None,
            run_index=_integer(row.get("run")), event_code=code, coded=coded, **_actor(row), row=text))
    controls = []
    for line, text, row in _rows(xcon / CONTROLS_NAME, problems, "kind", "not a control naming its kind"):
        code, coded = _code(row)
        controls.append(Control(
            line=line, kind=row["kind"], at=_number(row.get("at")), at_local=_text(row.get("at_local")),
            trial_index=_count(row.get("trial_index")), run_index=_integer(row.get("run")), event_code=code,
            coded=coded, detail=_json({key: value for key, value in row.items() if key not in _CONTROL_FIELDS}),
            **_actor(row), row=text))
    return RigRecords(changes=tuple(changes), controls=tuple(controls), problems=tuple(problems))
