"""Subject corrections in built files (design spec
`2026-10-05-subject-corrections-design.md`).

An animal's species, sex or date of birth corrected in wl.works reaches this
host in the next request, which writes it into element-animal's `Subject` for
the whole animal. Every `written` file of the animal built with other details
is then corrected where it is: a copy is patched, checked and swapped in, so
the live file is never written into and the lab's annotations travel inside
the copy. Only the subject's datasets change, with a note of what did."""

from __future__ import annotations

import datetime
from pathlib import Path

import h5py

SUBJECT = "/general/subject"
# The details a correction may change, in the order its note names them, with
# the words it uses. The subject's description carries the note.
_DETAILS = (("species", "species"), ("sex", "sex"), ("date_of_birth", "date of birth"))
_ENCODING = {"species": "utf-8", "sex": "utf-8", "date_of_birth": "ascii", "description": "utf-8"}


def stale_files() -> list[dict]:
    """Every `written` activation whose stored description names subject
    details other than the subject's current ones (spec section 2): by value,
    as `build.resolved_invalid` finds `invalid` files, since subject details
    carry no timestamp. Published or not, canonical or derivative, current or
    superseded."""
    from wl_preproc.nwb.gather import described_subject
    from wl_preproc.schema import nwb as nwb_schema

    stale, subjects = [], {}
    for row in (nwb_schema.NwbFile & {"status": "written"}).proj("description").to_dicts():
        if row["subject"] not in subjects:
            subjects[row["subject"]] = described_subject(row["subject"])
        now = subjects[row["subject"]]
        built_with = (row["description"] or {}).get("subject") or {}
        if {field: built_with.get(field) for field in now} != now:
            stale.append({k: row[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")})
    return stale


def _text(value) -> str:
    return "unknown" if value is None else value.isoformat() if isinstance(value, datetime.date) else str(value)


def correction_note(old: dict, new: dict, day: datetime.date) -> str | None:
    """The line a correction adds, naming each detail that changed, old to
    new, with the UTC date of the correction and `unknown` for a missing
    value: *"Corrected 2026-11-02: date of birth 2016-03-02 → 2016-03-01."*
    None when nothing differs."""
    changes = [f"{words} {_text(old[field])} → {_text(new[field])}" for field, words in _DETAILS
               if old[field] != new[field]]
    return f"Corrected {day.isoformat()}: {'; '.join(changes)}." if changes else None


def file_subject(path: Path) -> dict:
    """`species`, `sex` and `date_of_birth` (a date, or None) as the file
    holds them. The old values a note names are read here, never from the
    records, so a pass after a crash that followed the swap finds nothing
    to change (spec section 5)."""
    with h5py.File(path, "r") as handle:
        subject = handle[SUBJECT]

        def read(name: str):
            return subject[name][()].decode() if name in subject else None

        born = read("date_of_birth")
        return {"species": read("species"), "sex": read("sex"),
                "date_of_birth": None if born is None else datetime.datetime.fromisoformat(born).date()}


def _write(subject: h5py.Group, name: str, value: str | None) -> None:
    """One text dataset of the subject: set, written where the file had
    none, or removed for a value now unknown."""
    if value is None:
        if name in subject:
            del subject[name]
    elif name in subject:
        subject[name][()] = value
    else:
        subject.create_dataset(name, data=value, dtype=h5py.string_dtype(_ENCODING[name]))


def patch_subject(path: Path, new: dict, day: datetime.date) -> str | None:
    """Set the file's subject details to `new` and append the note to the
    subject's description (spec section 3, step 2). Only those datasets
    change; earlier notes stay. Returns the note, or None, without opening
    the file for writing, when it already holds `new`."""
    note = correction_note(file_subject(path), new, day)
    if note is None:
        return None
    born = new["date_of_birth"]
    with h5py.File(path, "r+") as handle:
        subject = handle[SUBJECT]
        _write(subject, "species", new["species"])
        _write(subject, "sex", new["sex"])
        _write(subject, "date_of_birth", None if born is None else datetime.datetime.combine(
            born, datetime.time(), tzinfo=datetime.timezone.utc).isoformat())
        stated = subject["description"][()].decode() if "description" in subject else ""
        _write(subject, "description", f"{stated}\n{note}" if stated else note)
    return note
