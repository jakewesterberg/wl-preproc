"""The fields of wl-xcon's record that no row must carry (design spec
`2026-11-07-rig-record-fields-design.md` section 2): what its engine builds
C2a, C2b and C3 and its XC-399 added to a trial line and a run's rows.

**Every one is optional.** A row written before the build that added it has
none, and reads as it did. **Read, never repaired:** a value of the wrong type
is read as none and counted by field, so a reader says once per file which
field it could not read and on how many rows. It never drops the row: that is
for the row's own required fields to decide."""

from __future__ import annotations

import collections
import dataclasses
import math
from collections.abc import Callable


@dataclasses.dataclass(frozen=True, slots=True)
class Kind:
    """What a field holds: `read` gives `(value, readable)`, the value none
    (or empty) when unreadable; `says` names the kind in a problem."""

    read: Callable[[object], tuple[object, bool]]
    says: str


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _boolean(value):
    return (value, True) if value is None or isinstance(value, bool) else (None, False)


def _whole(value):
    return (value, True) if value is None or _integer(value) else (None, False)


def _number(value):
    if value is None:
        return None, True
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None, False
    return float(value), True


def _text(value):
    return (value, True) if value is None or isinstance(value, str) else (None, False)


def _texts(value):
    if value is None:
        return (), True
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value), True
    return (), False


def _objects(value):
    if value is None:
        return None, True
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value, True
    return None, False


def _source(value: object) -> bool:
    return isinstance(value, str) or _integer(value)


def _drawn(value):
    if value is None:
        return None, True
    if isinstance(value, dict) and all(isinstance(name, str) and _source(source) for name, source in value.items()):
        return dict(value), True
    return None, False


BOOLEAN = Kind(_boolean, "true, false or null")
WHOLE = Kind(_whole, "a whole number or null")
NUMBER = Kind(_number, "a finite number or null")
TEXT = Kind(_text, "text or null")
TEXTS = Kind(_texts, "a list of text")
OBJECTS = Kind(_objects, "a list of objects or null")
DRAWN = Kind(_drawn, "an object naming each drawn setting's source")
ANY = Kind(lambda value: (value, True), "a value")


class Fields:
    """One file's optional fields, read row by row, and what could not be
    read. `rows` names what a row is, singular and plural."""

    def __init__(self, rows: tuple[str, str]) -> None:
        self._rows = rows
        self._wrong: collections.Counter[tuple[str, str]] = collections.Counter()

    def read(self, row: dict, name: str, kind: Kind, *, field: str | None = None):
        """`row[name]` as `kind`; its none (or empty) value when the row does
        not carry it, or carries it unreadable. `field` names it in a problem
        when it sits inside another (`block_ends[].ended`)."""
        value, readable = kind.read(row.get(name))
        if not readable:
            self.wrong(field or name, kind.says)
        return value

    def wrong(self, field: str, says: str) -> None:
        """One row whose `field` is not `says`."""
        self._wrong[(field, says)] += 1

    def problems(self) -> list[str]:
        """One sentence per field that some row carried unreadable."""
        out = []
        for (field, says), n in sorted(self._wrong.items()):
            what = self._rows[0] if n == 1 else self._rows[1]
            out.append(f"{n} {what}: `{field}` is not {says}, so it is read as none")
        return out
