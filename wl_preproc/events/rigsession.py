"""What wl-xcon's record says the session was: its kind and the display
calibration it opened with, from `xcon/config.json`, and the warnings it
accepted, from `xcon/warnings.jsonl` (design spec
`2026-10-14-session-kind-and-warnings-design.md` sections 1 and 2).

**Carried, never acted on.** wl-xcon's engine build B writes `session_kind`
(training, piloting or recording) and `calibration` into `config.json`, and one
row per accepted warning into `warnings.jsonl` (`wl_xcon/record.py`,
`ACCEPTED_WARNINGS`). Nothing in this pipeline behaves differently by kind;
the published record says what the rig said.

**Read, never repaired, by `events/rigruns.py`'s rules.** A warning line that
is not a JSON object naming its `code` is reported by file and line and left
out. A kind outside the three, or a calibration that is neither an object nor
null, is reported and read as none. A `config.json` written before build B has
neither field: not recorded, which is not a problem. A record whose
`config.json` names another animal is not read at all.

**The record is `config.json`.** A session without one was not run by wl-xcon,
and has none, whatever else its `xcon/` folder holds.
"""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path

from wl_preproc.contracts.paths import XCON_DIRNAME
from wl_preproc.events.rigruns import CONFIG_NAME

WARNINGS_NAME = "warnings.jsonl"
# wl-xcon's `wl_xcon/findings.py::SESSION_KINDS`.
SESSION_KINDS = ("training", "piloting", "recording")


@dataclasses.dataclass(frozen=True, slots=True)
class RigCalibration:
    """The display calibration a session opened with: its id, whether it is
    the sRGB standard nobody measured, the day it was measured (empty for the
    standard), and how its cone colours became light, as given."""

    id: str | None
    standard: bool | None
    measured_on: str | None
    cones: dict | None


@dataclasses.dataclass(frozen=True, slots=True)
class RigWarning:
    """One accepted warning, as wl-xcon wrote it. `run_index` is wl-xcon's
    0-based run, null for one accepted at the session's open. `at` is the
    rig's wall clock, its bookkeeping, not a time on the recording's clock."""

    code: str
    detail: str | None
    accepted_in: tuple[str, ...]
    session_kind: str | None
    how: str | None
    run_index: int | None
    at: float | None
    at_local: str | None
    by_kind: str | None
    by_name: str | None
    by_account: str | None
    by_issuer: str | None
    by_token_id: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class RigSession:
    kind: str | None
    calibration: RigCalibration | None
    warnings: tuple[RigWarning, ...]
    problems: tuple[str, ...]
    # False for a record whose `config.json` names another animal: nothing of
    # it is kept but its problem.
    of_this_session: bool = True


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: object) -> float | None:
    """A finite number, or None: MySQL refuses an infinite or NaN double, and
    one would fail the session's whole event stage on every pass."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _calibration(value: object, problems: list[str]) -> RigCalibration | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        problems.append(f"{CONFIG_NAME}: calibration is neither an object nor null")
        return None
    standard = value.get("standard")
    cones = value.get("cones")
    return RigCalibration(id=_text(value.get("id")), standard=standard if isinstance(standard, bool) else None,
                          measured_on=_text(value.get("measured_on")), cones=cones if isinstance(cones, dict) else None)


def _warning(row: dict) -> RigWarning:
    accepted_in = row.get("accepted_in")
    by = row.get("by") if isinstance(row.get("by"), dict) else {}
    return RigWarning(
        code=str(row["code"]), detail=_text(row.get("detail")),
        accepted_in=tuple(str(kind) for kind in accepted_in) if isinstance(accepted_in, list) else (),
        session_kind=_text(row.get("session_kind")), how=_text(row.get("how")),
        run_index=_integer(row.get("run")), at=_number(row.get("at")), at_local=_text(row.get("at_local")),
        by_kind=_text(by.get("kind")), by_name=_text(by.get("name")), by_account=_text(by.get("account")),
        by_issuer=_text(by.get("issuer")), by_token_id=_text(by.get("token_id")),
    )


def read_rig_session(session_dir: Path, subject: str) -> RigSession | None:
    """This session's kind, calibration and accepted warnings from
    `<session>/xcon/`, or `None` when it has no `config.json`."""
    xcon = Path(session_dir) / XCON_DIRNAME
    path = xcon / CONFIG_NAME
    if not path.is_file():
        return None
    problems: list[str] = []
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        config = None
    if not isinstance(config, dict):
        problems.append(f"{CONFIG_NAME}: not a JSON object")
        config = {}
    recorded = config.get("subject")
    if recorded is not None and recorded != subject:
        return RigSession(kind=None, calibration=None, warnings=(), of_this_session=False, problems=(
            f"{CONFIG_NAME} names subject {recorded!r}, not {subject!r}, so its kind, calibration and warnings "
            "are not this session's",))
    kind = config.get("session_kind")
    if kind is not None and kind not in SESSION_KINDS:
        problems.append(f"{CONFIG_NAME}: session kind {kind!r} is not one of {', '.join(SESSION_KINDS)}")
        kind = None
    calibration = _calibration(config.get("calibration"), problems)
    warnings = []
    record = xcon / WARNINGS_NAME
    lines = record.read_text(encoding="utf-8").splitlines() if record.is_file() else []
    for number_of_line, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"{WARNINGS_NAME} line {number_of_line}: not JSON")
            continue
        if not isinstance(row, dict) or not isinstance(row.get("code"), str) or not row["code"]:
            problems.append(f"{WARNINGS_NAME} line {number_of_line}: not a warning naming its code")
            continue
        warnings.append(_warning(row))
    return RigSession(kind=kind, calibration=calibration, warnings=tuple(warnings), problems=tuple(problems))
