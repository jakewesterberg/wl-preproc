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
`config.json` is its own (`events/rigsession.py`'s `of_this_session`).

**The change log by kind** (design spec `2026-11-07-rig-record-fields-design.md`
section 7): since wl-xcon's engine build C3 a change row names its `kind`
(`set`, `hand_back`, `range`, `revert`, `clear`, `drum`, `training`; a row
written before has none, and is a `set`), the act it belongs to (`change`, a
revert's rows sharing one), the first trial it applies to, which way it went,
the kind's own fields, and its instant (`at`). Its instant times a row that
strobed nothing through the rig's clock fit, as a control's does; the rest is
read from the stored line when the file is built (`change_fields`), so no
column is added. `acts` counts a person's acts on a setting."""

from __future__ import annotations

import collections
import dataclasses
import json
import math
from pathlib import Path

from wl_preproc.contracts.paths import XCON_DIRNAME
from wl_preproc.events.rigfields import NUMBER, TEXT, WHOLE, Fields

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
    at: float | None = None  # its instant on the rig's anchored clock, since C3


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
            run_index=_integer(row.get("run")), event_code=code, coded=coded, **_actor(row), row=text,
            at=_number(row.get("at"))))
    controls = []
    for line, text, row in _rows(xcon / CONTROLS_NAME, problems, "kind", "not a control naming its kind"):
        code, coded = _code(row)
        controls.append(Control(
            line=line, kind=row["kind"], at=_number(row.get("at")), at_local=_text(row.get("at_local")),
            trial_index=_count(row.get("trial_index")), run_index=_integer(row.get("run")), event_code=code,
            coded=coded, detail=_json({key: value for key, value in row.items() if key not in _CONTROL_FIELDS}),
            **_actor(row), row=text))
    return RigRecords(changes=tuple(changes), controls=tuple(controls), problems=tuple(problems))


# The kinds that change a setting, as a person's act; a clear and the two
# switches are not (the requester's decision of 2026-11-07).
SETTING_KINDS = ("set", "hand_back", "range", "revert")
# Every kind wl-xcon's change log holds (`wl_xcon/record.py::KINDS`, read at
# engine-c3 `7ea7913`); one outside them is published as written and counted
# nowhere.
KINDS = (*SETTING_KINDS, "clear", "drum", "training")
# What every change row carries (`wl_xcon/record.py`'s change row, read at
# engine-c3 `7ea7913`); the rest of a row is its kind's own fields, a clear's
# and a switch's being its control row's.
_CHANGE_FIELDS = frozenset({"change", "kind", "name", "was", "now", "by", "at", "at_local", "trial_number", "run",
                            "meaning", "offered", "odds", "event_code", "sequence"})


@dataclasses.dataclass(frozen=True, slots=True)
class ChangeFields:
    """What a stored change line says beyond its setting and values (C3):
    `kind` (`set` for a row written before C3), the act's id, the first trial
    it applies to, which way it went and by how much, a hand-back's `to`, a
    range's draw (`from`) and `block`, a revert's `moment` (0 the run's
    start), whether its act strobed nothing, and `detail`, the row's own
    fields beyond those every change row carries as JSON text, a clear's and
    a switch's control fields among them (`{}` if none)."""

    kind: str
    change: int | None
    trial_number: int | None
    went: str | None
    step: float | None
    to: str | None
    from_draw: str | None
    block: str | None
    moment: int | None
    strobed_nothing: bool
    detail: str = "{}"


def change_fields(rows: list[str]) -> tuple[list[ChangeFields], list[str]]:
    """Each stored line's `ChangeFields`, and what could not be read, once
    per field (`events/rigfields.py`'s rule), and each kind outside `KINDS`,
    once: published as written and counted nowhere (`acts`)."""
    fields = Fields(("change row", "change rows"))
    out, unknown = [], collections.Counter()
    for text in rows:
        row = json.loads(text)
        meaning = row.get("meaning") if isinstance(row.get("meaning"), dict) else {}
        # No `kind`: written before C3, when every row was a set. One it
        # cannot read is none, published as '' (the final review's M2).
        kind = "set" if "kind" not in row else fields.read(row, "kind", TEXT) or ""
        if kind and kind not in KINDS:
            unknown[kind] += 1
        out.append(ChangeFields(
            kind=kind, change=fields.read(row, "change", WHOLE), trial_number=fields.read(row, "trial_number", WHOLE),
            went=fields.read(meaning, "went", TEXT, field="meaning.went"),
            step=fields.read(meaning, "step", NUMBER, field="meaning.step"), to=fields.read(row, "to", TEXT),
            from_draw=fields.read(row, "from", TEXT), block=fields.read(row, "block", TEXT),
            moment=fields.read(row, "moment", WHOLE),
            strobed_nothing="event_code" in row and row["event_code"] is None,
            detail=_json({key: value for key, value in row.items() if key not in _CHANGE_FIELDS})))
    return out, [*fields.problems(), *(
        f"{n} change {'row' if n == 1 else 'rows'}: `kind` is {kind!r}, which no build of wl-xcon this reader knows "
        "writes, so it is published as written and not counted as a person's act" for kind, n in sorted(unknown.items()))]


def acts(changes: list[ChangeFields]) -> int:
    """A person's acts on a setting: one per act id among rows of
    `SETTING_KINDS`, a row with no id (written before C3) an act of its own."""
    ids, alone = set(), 0
    for change in changes:
        if change.kind not in SETTING_KINDS:
            continue
        if change.change is None:
            alone += 1
        else:
            ids.add(change.change)
    return len(ids) + alone
