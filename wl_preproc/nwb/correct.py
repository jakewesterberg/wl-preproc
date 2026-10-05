"""Subject corrections in built files (design spec
`2026-10-05-subject-corrections-design.md`).

An animal's species, sex or date of birth corrected in wl.works reaches this
host in the next request, which writes it into element-animal's `Subject` for
the whole animal. Every `written` file of the animal built with other details
is then corrected where it is: a copy is patched, checked and swapped in, so
the live file is never written into and the lab's annotations travel inside
the copy. Only the subject's datasets change, with a note of what did."""

from __future__ import annotations

import copy
import datetime
import os
import shutil
import stat
from pathlib import Path

import h5py

from wl_preproc.nwb.validate import inspect_file, n_critical

SUBJECT = "/general/subject"
# The four datasets a correction rewrites, and the only ones it may: every
# other dataset under the subject -- `subject_id`, written once by the build,
# or one the lab appended -- is not a correction's to change or to record
# (the final review's I2).
SUBJECT_DATASETS = frozenset(f"{SUBJECT}/{name}" for name in ("species", "sex", "date_of_birth", "description"))
# The details a correction may change, in the order its note names them, with
# the words it uses. The subject's description carries the note.
_DETAILS = (("species", "species"), ("sex", "sex"), ("date_of_birth", "date of birth"))
_ENCODING = {"species": "utf-8", "sex": "utf-8", "date_of_birth": "ascii", "description": "utf-8"}


class CorrectionRefused(Exception):
    """A correction this pass will not make (spec section 5): the share is
    not there or is full, the subject's date of birth is now unknown, or the
    corrected copy fails validation. The file keeps the details it has, and
    each pass says so."""


class ChangedWhileCorrecting(Exception):
    """The live file changed while it was copied: someone is writing to it.
    The copy is dropped and nothing recorded; the next pass tries again."""


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


def _key(row: dict) -> dict:
    return {k: row[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")}


def _live(key: dict, row: dict, shares: dict) -> tuple[Path, dict | None, object]:
    """Where the file is now, its placement and its share: on its share once
    published, in scratch (no placement, no share) until then."""
    from wl_preproc.nwb.publish import current_placement

    placement = current_placement(key)
    if placement is None:
        return Path(row["path"]), None, None
    share = shares.get(placement["tier"])
    if share is None:
        raise CorrectionRefused(f"the {placement['tier']} share is not configured; not corrected")
    if reason := share.unreachable():
        raise CorrectionRefused(f"{reason}; not corrected")
    return share.local(placement["path"]), placement, share


def _keep_access(live: Path, partial: Path) -> None:
    """The live file's mode and group onto the copy, just before it is
    swapped in: the copy is patched first with its own default mode, so a
    file a person made read only is still corrected and stays read only, and
    one opened to the lab's group keeps it (the minors' review, M-a). Best
    effort: a mount that refuses a chmod or chown does not stop a correction."""
    status = live.stat()
    try:
        os.chmod(partial, stat.S_IMODE(status.st_mode))
    except OSError:
        pass
    try:
        os.chown(partial, -1, status.st_gid)
    except OSError:
        pass


def _subject_paths(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """`rows` split into the four datasets a correction rewrites and the rest."""
    inside = [row for row in rows if row["dataset_path"] in SUBJECT_DATASETS]
    return inside, [row for row in rows if row["dataset_path"] not in SUBJECT_DATASETS]


def correct(key: dict, shares: dict, day: datetime.date) -> str | None:
    """Correct one stale file where it is (spec section 3): copy, patch,
    check, swap, record, and rewrite the description beside it. `shares`
    maps a tier to its `publish.Share`. Returns the note, or None when the
    file already held the details and only its records were brought up to
    them. Raises `CorrectionRefused`, `ChangedWhileCorrecting` or
    `publish.ChangedData`, leaving the live file as it was."""
    from wl_preproc.nwb.gather import _subject
    from wl_preproc.nwb.publish import ChangedData, _stamp, mismatches
    from wl_preproc.schema import nwb as nwb_schema

    key = _key(key)
    row = (nwb_schema.NwbFile & key).fetch1()
    current = _subject(key["subject"])
    current = {field: current[field] for field, _words in _DETAILS}
    if current["date_of_birth"] is None:
        raise CorrectionRefused("the subject's date of birth is now unknown, and a file without one fails "
                                "validation; it keeps the details it has")
    live, placement, share = _live(key, row, shares)
    if not live.exists():
        raise FileNotFoundError(f"{live}: the file is missing; not corrected")
    if file_subject(live) == current:
        # A pass after a crash that followed the swap: only the records are
        # behind, and the file, perhaps gigabytes, is not copied (the final
        # review's M9).
        _record(key, row, live, placement)
        return None
    # Only a copy needs room: the records-only catch-up above goes ahead on a
    # fast share at its headroom (the minors' review, I-1).
    if placement is not None and placement["tier"] == "fast" and not share.has_room(live.stat().st_size):
        raise CorrectionRefused("the fast share is at its headroom; not corrected")
    checksums = (nwb_schema.NwbFile.Dataset & key).to_dicts()
    _subjects, others = _subject_paths(checksums)
    before = _stamp(live)
    partial = live.with_name(live.name + ".partial")
    try:
        shutil.copyfile(live, partial)
        note = patch_subject(partial, current, day)
        changed = mismatches(partial, others)
        if changed:
            raise ChangedData(f"{live}: {len(changed)} written-once dataset(s) changed, first {changed[0]}; "
                              "not corrected")
        if note is not None and (critical := n_critical(inspect_file(partial))):
            raise CorrectionRefused(f"{live}: the corrected copy has {critical} critical nwbinspector "
                                    "finding(s); not corrected")
        if note is not None:
            with open(partial, "rb+") as handle:
                os.fsync(handle.fileno())
        # Checked after the copy is flushed, so a write landing during the
        # flush is seen rather than lost under the swap (the final review's M2).
        if _stamp(live) != before:
            raise ChangedWhileCorrecting(f"{live}: changed while it was being corrected (someone is writing "
                                         "to it); tried again next pass")
        if note is not None:
            _keep_access(live, partial)
            os.replace(partial, live)
    finally:
        partial.unlink(missing_ok=True)
    _record(key, row, live, placement)
    return note


def subject_records(key: dict, description: dict, path: Path) -> tuple[dict, list[dict]]:
    """A description and the four subject datasets' checksums brought up to
    the file at `path`: its subject as the file holds it (with `age_days`),
    its correction lines past those the description already has, and the
    four checksums. Read from the file, so a pass after a crash that
    followed the swap records the note the crashed pass wrote. Shared by a
    correction's records and by publishing when it adopts a file a
    correction may have changed (`publish._adopt`, the final review's I1)."""
    from wl_preproc.contracts.nwb_description import NwbDescription
    from wl_preproc.nwb.checksums import dataset_checksums
    from wl_preproc.nwb.describe import _age_days

    held = file_subject(path)
    born = held["date_of_birth"]
    described = copy.deepcopy(description)
    described["subject"] = {"species": held["species"], "sex": held["sex"],
                            "date_of_birth": None if born is None else born.isoformat(),
                            "age_days": _age_days(born, key["session_datetime"])}
    with h5py.File(path, "r") as handle:
        stated = handle[SUBJECT]["description"][()].decode() if "description" in handle[SUBJECT] else ""
    # Counted rather than compared, so the same correction twice is noted
    # twice.
    corrections = [line for line in stated.split("\n") if line.startswith("Corrected ")]
    recorded = sum(1 for note in described["notes"] if note.startswith("Corrected "))
    described["notes"] = [*described["notes"], *corrections[recorded:]]
    fresh = dataset_checksums(path, only=SUBJECT_DATASETS)
    _old, kept = _subject_paths(described["checksums"]["datasets"])
    described["checksums"]["datasets"] = sorted([*kept, *fresh], key=lambda item: item["dataset_path"])
    return NwbDescription.model_validate(described).model_dump(mode="json"), fresh


def write_subject_records(key: dict, description: dict, fresh: list[dict], change: dict | None = None,
                          n_bytes: int | None = None) -> None:
    """`subject_records`' result into the records, in one transaction: the
    description, the four datasets' `NwbFile.Dataset` rows, the scratch
    copy's size when given, and -- for a correction -- its `corrected`
    change, with `change` as its placement (or `{}` for none)."""
    import datajoint as dj

    from wl_preproc.nwb.publish import insert_change
    from wl_preproc.schema import nwb as nwb_schema

    with dj.conn().transaction:
        nwb_schema.NwbFile.update1({**key, "description": description,
                                    **({} if n_bytes is None else {"n_bytes": n_bytes})})
        (nwb_schema.NwbFile.Dataset & key & [{"dataset_path": path} for path in SUBJECT_DATASETS]).delete_quick()
        nwb_schema.NwbFile.Dataset.insert({**key, **item} for item in fresh)
        if change is not None:
            insert_change(key, "corrected", change or None)


def _record(key: dict, row: dict, live: Path, placement: dict | None) -> None:
    """The records brought up to the file (spec section 3, steps 5 and 6):
    the description beside a published file first, then, in one
    transaction, the description's subject and notes, the four checksums,
    the size, and a `corrected` change -- with the placement again, same
    share and path, for a published file. Written in that order, a failed
    description file leaves the records stale, so the next pass writes it
    (the final review's I3)."""
    from wl_preproc.nwb.publish import write_description

    description, fresh = subject_records(key, row["description"], live)
    n_bytes = live.stat().st_size
    if placement is not None:
        write_description(live, description)
    again = {} if placement is None else {
        **{field: placement[field] for field in ("tier", "host", "share", "path")}, "n_bytes": n_bytes}
    write_subject_records(key, description, fresh, change=again, n_bytes=n_bytes if placement is None else None)


def _change_kinds() -> str:
    """`NwbChange.kind`'s type as the database declares it now. A database
    declared before `corrected` would refuse each correction's records after
    its swap, on every pass (the final review's M5)."""
    import datajoint as dj

    from wl_preproc.schema import nwb as nwb_schema

    return dj.conn().query(f"SHOW COLUMNS FROM {nwb_schema.NwbChange.full_table_name} LIKE 'kind'").fetchone()[1]


def _clear_partials(shares: dict) -> list[str]:
    """Every `.partial` a crash left beside a file or its description, at
    each published file's path on every reachable share and beside each
    written file's scratch copy (the final review's M6). The NWB stages
    alone write `.partial` names, under the NWB lock, so one found while the
    lock is held is a leftover: of a correction, of a move, or of a publish
    once that file is recorded (a first publish's own leftover is
    overwritten by its retry). One that cannot be removed is reported and
    the rest are still cleared (the minors' review, M-b). Returns the
    reports."""
    from wl_preproc.nwb.publish import _published, activation_tuple, description_path, placement_history
    from wl_preproc.schema import nwb as nwb_schema

    leftovers, history = [], placement_history()
    reachable = [share for share in shares.values() if share.unreachable() is None]
    for key in _published().keys():
        placement = history.get(activation_tuple(key), [None])[-1]
        for share in reachable if placement is not None else ():
            path = share.local(placement["path"])
            leftovers += [beside.with_name(beside.name + ".partial") for beside in (path, description_path(path))]
    leftovers += [Path(row["path"] + ".partial")
                  for row in (nwb_schema.NwbFile & {"status": "written"}).proj("path").to_dicts() if row["path"]]
    reports = []
    for leftover in leftovers:
        try:
            leftover.unlink(missing_ok=True)
        except OSError as exc:
            reports.append(f"NwbCorrection: {leftover}: a leftover copy that could not be removed: {exc}")
    return reports


def run_corrections(slow, fast=None, freed: list[dict] | None = None,
                    day: datetime.date | None = None) -> tuple[int, list[str]]:
    """The daemon's correction stage (spec section 2): every stale written
    file, corrected where it is, after building and before publishing and
    placement (amendment 11). A file not yet published in a freed session
    waits, as publishing does. Leftover `.partial` copies are cleared first.
    A share that is not reachable is reported once, with the number of files
    it holds back; any other failure is reported per file and retried next
    pass, and never stops another file. Returns `(corrected, failures)`."""
    from wl_preproc.nwb.publish import current_placement

    freed = freed or []
    day = day or datetime.datetime.now(datetime.timezone.utc).date()
    shares = {tier: share for tier, share in (("slow", slow), ("fast", fast)) if share is not None}
    try:
        kinds = _change_kinds()
    except Exception as exc:  # the daemon's other stages must still run
        return 0, [f"NwbCorrection: {exc}"]
    if "'corrected'" not in kinds:
        from wl_preproc.schema import nwb as nwb_schema

        table = nwb_schema.NwbChange.full_table_name
        kind = "enum('built','published','moved','superseded','corrected')"
        # DataJoint keeps the declared type in the column's comment.
        comment = f":{kind}:".replace("'", "''")
        return 0, [f"NwbCorrection: {table}.kind is {kinds}, without 'corrected': a database declared before "
                   "subject corrections; nothing is corrected until it is altered: ALTER TABLE "
                   f"{table} MODIFY kind {kind} NOT NULL COMMENT '{comment}'"]
    try:
        stale = stale_files()
    except Exception as exc:  # the daemon's other stages must still run
        return 0, [f"NwbCorrection: {exc}"]
    corrected, errors = 0, []
    try:
        errors += _clear_partials(shares)
    except Exception as exc:  # the corrections below must still run
        errors.append(f"NwbCorrection: clearing leftover copies: {exc}")
    # Said once per share, as publishing and placement say it, not once per
    # file it holds back: a share not reachable (the final review's M11), or
    # one a file is placed on that this pass was not given (the minors'
    # review, M-c).
    down = {tier: reason for tier, share in shares.items() if (reason := share.unreachable()) is not None}
    down.update({tier: f"the {tier} share is not configured" for tier in ("slow", "fast") if tier not in shares})
    held_back = dict.fromkeys(down, 0)
    for key in stale:
        try:
            session = {"subject": key["subject"], "session_datetime": key["session_datetime"]}
            placement = current_placement(key)
            if session in freed and placement is None:
                continue
            if placement is not None and placement["tier"] in down:
                held_back[placement["tier"]] += 1
                continue
            correct(key, shares, day)
            corrected += 1
        except Exception as exc:  # one file must not stop the others; retried next pass
            errors.append(f"NwbCorrection {key}: {exc}")
    errors.extend(f"NwbCorrection: {down[tier]}; {count} file(s) on it not corrected"
                  for tier, count in held_back.items() if count)
    return corrected, errors
