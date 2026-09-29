"""Publishing built files to the NAS, and where each one is (design spec
`2026-09-29-nwb-publishing-design.md` sections 3, 4 and 9).

**Two shares, one live copy.** A file is on the slow long-term share or the
fast active one, never both: the lab appends annotations into its NWBs, and two
copies would diverge at the first one. On either share it lives at
`nwb/<subject>/<session_id>/<identifier>.nwb`, its description beside it as
`<identifier>.json`, and the recorded path is relative to the share, the
triple wl.works' Plan 23 section 10.1 names.

**Nothing carries its final name until it is verified.** A copy is written to
`.partial`, every dataset the build checksummed is hashed again and compared,
and only then renamed; the description is written last, the "complete"
signal wl.works' Plan 20 asks for.

**Nothing is deleted that the record does not account for.** A file already
at an activation's path that no placement records is adopted if its
written-once data is this row's, and refused otherwise; a copy on the other
share is removed only as the leftover of a recorded move, beside a present
current copy, and unchanged since the move (the final review's C1, I2 and
I5). A share is used only when its `nwb/` folder is there, which a person
makes once: an unmounted mount point is an empty directory, or none (I3)."""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import shutil
from pathlib import Path

from wl_preproc.nwb.checksums import dataset_checksums

# The top-level folder NWB files get on either share, apart from the raw
# archive's `<subject>/<session>` folders.
NWB_DIR = "nwb"


class VerificationError(Exception):
    """A copy whose written-once datasets do not match the build's checksums."""


class ChangedData(Exception):
    """A written-once dataset that no longer matches its checksum on the NAS:
    something changed data that must never change. The file is not moved,
    for a person to look at (design spec
    `2026-09-29-nwb-publishing-design.md` section 5)."""


class ChangedWhileMoving(Exception):
    """The file changed while it was being copied: someone is writing to it.
    The copy is removed and nothing recorded; the next pass tries again."""


class OldCopyLeft(Exception):
    """A move that is recorded and stands, whose old copy could not be
    deleted (a reader holds it, or the share refused): the next pass's
    sweep finishes it. Counted as a move, and reported."""


class PublishConflict(Exception):
    """A file already at the target that no placement records. Never
    overwritten: the lab's annotations live only inside published files
    (parent spec section 8.3, "regeneration supersedes; it never
    overwrites")."""


@dataclasses.dataclass(frozen=True)
class Share:
    """One NAS share, as the daemon is given it: its tier, where it is
    mounted here, and the host and share names wl.works' triple uses."""

    tier: str  # "slow" or "fast"
    mount: Path
    host: str
    name: str
    # Free space a move to this share must leave. None: a tenth of the
    # share's size (the requester's default, 2026-09-29).
    headroom_bytes: int | None = None

    def relative(self, subject: str, session_id: str, identifier: str) -> str:
        return f"{NWB_DIR}/{subject}/{session_id}/{identifier}.nwb"

    def local(self, relative: str) -> Path:
        return Path(self.mount) / relative

    def has_room(self, n_bytes: int) -> bool:
        """Whether `n_bytes` more still leaves the configured headroom free."""
        usage = shutil.disk_usage(self.mount)
        headroom = self.headroom_bytes if self.headroom_bytes is not None else usage.total // 10
        return usage.free - n_bytes >= headroom

    def unreachable(self) -> str | None:
        """Why this share cannot be used now, or None. Its `nwb/` folder is
        made once, by a person, when the share is set up, and never by wlpp:
        an unmounted mount point is an empty directory, or none, and writing
        there would fill this host's own disk under the NAS's name."""
        folder = Path(self.mount) / NWB_DIR
        try:
            if folder.is_dir():
                return None
        except OSError as exc:
            return f"the {self.tier} share is not reachable: {folder}: {exc}"
        return (f"the {self.tier} share is not reachable: {folder} is not there (the share is not mounted, "
                f"or its {NWB_DIR}/ folder was never made)")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def key_of(row: dict) -> dict:
    return {k: row[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")}


def mismatches(path: Path, checksums: list[dict]) -> list[str]:
    """The recorded datasets whose contents in `path` no longer hash the same.
    Datasets appended since (annotations) are not written-once and not
    compared."""
    actual = {row["dataset_path"]: row["sha256"] for row in dataset_checksums(path)}
    return [row["dataset_path"] for row in checksums if actual.get(row["dataset_path"]) != row["sha256"]]


def copy_verified(source: Path, target: Path, checksums: list[dict]) -> None:
    """`source` to `target` by way of `target.partial`, verified before the
    rename. Raises `VerificationError`, leaving nothing behind, on a
    mismatch."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    try:
        shutil.copyfile(source, partial)
        with open(partial, "rb+") as handle:
            os.fsync(handle.fileno())
        bad = mismatches(partial, checksums)
        if bad:
            raise VerificationError(f"{target}: {len(bad)} dataset(s) differ after copying, first {bad[0]}")
        os.replace(partial, target)
    finally:
        if partial.exists():
            partial.unlink()


def description_path(nwb_path: Path) -> Path:
    return nwb_path.with_suffix(".json")


def write_description(nwb_path: Path, description: dict) -> None:
    """The description beside the file, written last and atomically."""
    target = description_path(nwb_path)
    partial = target.with_name(target.name + ".partial")
    try:
        with open(partial, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(description, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, target)
    finally:
        if partial.exists():
            partial.unlink()


def placements(key: dict) -> list[dict]:
    """Every place an activation's file has been, oldest first: each
    `NwbPlacement` with its change."""
    from wl_preproc.schema import nwb as nwb_schema

    rows = (nwb_schema.NwbPlacement * nwb_schema.NwbChange & key_of(key)).to_dicts()
    return sorted(rows, key=lambda row: row["change_seq"])


def current_placement(key: dict) -> dict | None:
    """Where an activation's file is now: its latest `NwbPlacement`, or None
    if it was never published."""
    rows = placements(key)
    return rows[-1] if rows else None


def record_change(key: dict, kind: str, placement: dict | None = None) -> int:
    """One `NwbChange`, and its `NwbPlacement` when it moved a file, in one
    transaction. Returns the change's sequence number."""
    import datajoint as dj

    from wl_preproc.schema import nwb as nwb_schema

    connection = dj.conn()
    with connection.transaction:
        nwb_schema.NwbChange.insert1({**key_of(key), "kind": kind, "changed_at": _now()})
        sequence = int(connection.query("SELECT LAST_INSERT_ID()").fetchone()[0])
        if placement is not None:
            nwb_schema.NwbPlacement.insert1({"change_seq": sequence, **placement})
    return sequence


def active_keys() -> set[tuple]:
    """The activations the latest `PUT /nwb/active` wants on the fast share."""
    from wl_preproc.schema import nwb as nwb_schema

    rows = nwb_schema.ActiveSet.to_dicts(order_by="set_seq DESC", limit=1)
    return {activation_tuple(item) for item in rows[0]["activations"]} if rows else set()


def activation_tuple(key: dict) -> tuple:
    """An activation key in one comparable form, whatever carried it:
    `session_datetime` as naive-UTC ISO text."""
    moment = key["session_datetime"]
    if isinstance(moment, str):
        moment = datetime.datetime.fromisoformat(moment)
    if moment.tzinfo is not None:
        moment = moment.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return (str(key["subject"]), moment.isoformat(), int(key["montage_id"]), int(key["activation_id"]))


def place(key: dict, source: Path, share: Share, checksums: list[dict], description: dict) -> dict:
    """Copy one file, verified, and its description onto `share`. Returns
    the placement (not yet recorded)."""
    identity = description["identity"]
    relative = share.relative(identity["subject"], identity["session_id"], identity["identifier"])
    target = share.local(relative)
    if target.exists() or description_path(target).exists():
        raise PublishConflict(f"{target} already exists and no placement of this activation records it; "
                              "not overwritten")
    copy_verified(source, target, checksums)
    write_description(target, description)
    return {"tier": share.tier, "host": share.host, "share": share.name, "path": relative,
            "n_bytes": target.stat().st_size}


def _adopt(key: dict, target: Path, share: Share, checksums: list[dict], description: dict) -> dict:
    """A file already at this activation's path that no placement records:
    its own, if every written-once dataset is this row's -- left by a
    publish that failed after the rename, or by a row deleted and rebuilt.
    Taken over where it is, annotations and all; refused otherwise."""
    bad = mismatches(target, checksums)
    if bad:
        raise PublishConflict(f"{target} already exists with other written-once data ({len(bad)} dataset(s), "
                              f"first {bad[0]}) and no placement of this activation records it; not overwritten")
    write_description(target, description)
    return {"tier": share.tier, "host": share.host, "share": share.name,
            "path": target.relative_to(share.mount).as_posix(), "n_bytes": target.stat().st_size}


def publish(key: dict, slow: Share, fast: Share | None, wanted_fast: set[tuple]) -> dict:
    """Publish one `written` file from scratch: to the fast share if it is in
    the active set and the fast share has room, otherwise to the slow one.
    A file already at its path on either share is adopted or refused, never
    published beside. The scratch copy is deleted once the placement is
    recorded."""
    from wl_preproc.schema import nwb as nwb_schema

    row = (nwb_schema.NwbFile & key_of(key)).fetch1()
    checksums = (nwb_schema.NwbFile.Dataset & key_of(key)).to_dicts()
    source = Path(row["path"])
    identity = row["description"]["identity"]
    relative = slow.relative(identity["subject"], identity["session_id"], identity["identifier"])
    found = [share for share in (slow, fast) if share is not None and share.local(relative).exists()]
    if len(found) > 1:
        raise PublishConflict(f"{relative} already exists on both shares and no placement of this activation "
                              "records either; not overwritten")
    if found:
        placement = _adopt(key, found[0].local(relative), found[0], checksums, row["description"])
    else:
        share = slow
        if fast is not None and activation_tuple(key) in wanted_fast and fast.has_room(source.stat().st_size):
            share = fast
        placement = place(key, source, share, checksums, row["description"])
    record_change(key, "published", placement)
    source.unlink(missing_ok=True)
    return placement


def _published():
    """Every activation with a published file."""
    from wl_preproc.schema import nwb as nwb_schema

    return nwb_schema.NwbFile & (nwb_schema.NwbChange & {"kind": "published"}).proj(
        "subject", "session_datetime", "montage_id", "activation_id")


def missing_copies(shares: dict[str, Share | None]) -> list[str]:
    """Every published file whose current copy is not on its share, by path:
    reported each pass, whatever else happens to it (design spec section 5's
    amendment; the final review's I1)."""
    reports = []
    for key in _published().keys():
        try:
            placement = current_placement(key)
            share = shares.get(placement["tier"]) if placement is not None else None
            if share is not None and not share.local(placement["path"]).exists():
                reports.append(f"NwbPlacement {key}: {share.local(placement['path'])}: the published file is "
                               "missing from its share")
        except Exception as exc:  # one file must not stop the others
            reports.append(f"NwbPlacement {key}: {exc}")
    return reports


def run_publish(slow: Share, fast: Share | None = None, freed: list[dict] | None = None) -> tuple[int, list[str]]:
    """The daemon's publishing stage: every `written` file not yet
    published, skipping freed sessions. Returns `(published, failures)`."""
    from wl_preproc.schema import nwb as nwb_schema

    freed = freed or []
    reason = slow.unreachable()
    if reason:
        return 0, [f"NwbPlacement: {reason}; nothing published"]
    published, errors = 0, []
    if fast is not None and (reason := fast.unreachable()):
        errors.append(f"NwbPlacement: {reason}; publishing to the slow share only")
        fast = None
    wanted_fast = active_keys()
    published_keys = (nwb_schema.NwbChange & {"kind": "published"}).proj(
        "subject", "session_datetime", "montage_id", "activation_id")
    pending = (nwb_schema.NwbFile & {"status": "written"}) - published_keys
    for key in pending.keys():
        if {"subject": key["subject"], "session_datetime": key["session_datetime"]} in freed:
            continue
        try:
            publish(key, slow, fast, wanted_fast)
            published += 1
        except Exception as exc:  # one file must not stop the others; retried next pass
            errors.append(f"NwbPlacement {key}: {exc}")
    errors.extend(missing_copies({"slow": slow, "fast": fast}))
    return published, errors


def move(key: dict, placement: dict, source_share: Share, target_share: Share) -> dict:
    """Move one published file, and its description, to the other share:
    checked, copied and verified, recorded, then the old copy deleted.
    Annotations the lab appended travel with it."""
    from wl_preproc.schema import nwb as nwb_schema

    source = source_share.local(placement["path"])
    if not source.exists():
        raise FileNotFoundError(f"{source}: the published file is missing from its share; not moved")
    before = _stamp(source)
    checksums = (nwb_schema.NwbFile.Dataset & key_of(key)).to_dicts()
    changed = mismatches(source, checksums)
    if changed:
        raise ChangedData(f"{source}: {len(changed)} written-once dataset(s) changed on the NAS, first "
                          f"{changed[0]}; not moved")
    description = (nwb_schema.NwbFile & key_of(key)).fetch1("description")
    moved = place(key, source, target_share, checksums, description)
    if _stamp(source) != before:
        _remove_old_copy(target_share.local(moved["path"]))
        raise ChangedWhileMoving(f"{source}: changed while it was being moved (someone is writing to it); "
                                 "not moved, tried again next pass")
    record_change(key, "moved", moved)
    try:
        _remove_old_copy(source)
    except OSError as exc:
        raise OldCopyLeft(f"moved; the old copy {source} is left for the next pass: {exc}") from exc
    return moved


def _stamp(path: Path) -> tuple[int, int]:
    """What changes when anyone writes to a file: its size and mtime."""
    status = path.stat()
    return status.st_size, status.st_mtime_ns


def _remove_old_copy(path: Path) -> None:
    """A moved file's old copy, and its description. A deletion that fails
    (the share refuses, a reader holds it) leaves the move recorded; the
    placement stage finishes it on a later pass."""
    path.unlink(missing_ok=True)
    description_path(path).unlink(missing_ok=True)


def _sweep(key: dict, placement: dict, shares: dict[str, Share]) -> str | None:
    """One live copy: a copy on the other share is the leftover of a move
    whose old copy could not be deleted then, and is removed now -- only if
    an earlier placement of this activation put it there, the current copy
    is present, and nobody wrote to it after the move was recorded.
    Anything else is left for a person, and the report says why."""
    other_tier = "fast" if placement["tier"] == "slow" else "slow"
    other = shares[other_tier].local(placement["path"])
    if not (other.exists() or description_path(other).exists()):
        return None
    recorded = any(row["change_seq"] < placement["change_seq"] and row["tier"] == other_tier
                   and row["path"] == placement["path"] for row in placements(key))
    if not recorded:
        return f"{other}: a copy no placement of this activation put there; left for a person"
    current = shares[placement["tier"]].local(placement["path"])
    if not current.exists():
        return f"{other}: left in place, because the current copy {current} is missing"
    moved_at = placement["changed_at"].replace(tzinfo=datetime.timezone.utc).timestamp()
    if other.exists() and other.stat().st_mtime > moved_at:
        return f"{other}: written to after the file was moved from it; left for a person"
    _remove_old_copy(other)
    return None


def run_placement(slow: Share, fast: Share) -> tuple[int, list[str]]:
    """The daemon's placement stage: every published file whose share is not
    the one the latest active set wants is moved, one live copy at a time;
    moves to the fast share stop at its headroom. Freed sessions are not
    skipped: placement never reads scratch. Returns `(moved, failures)`."""
    for share in (slow, fast):
        if reason := share.unreachable():
            return 0, [f"NwbPlacement: {reason}; nothing moved"]
    wanted_fast = active_keys()
    shares = {"slow": slow, "fast": fast}
    moved, errors = 0, []
    for key in _published().keys():
        try:
            placement = current_placement(key)
            if placement is None:
                continue
            report = _sweep(key, placement, shares)
            if report is not None:
                errors.append(f"NwbPlacement {key}: {report}")
                continue
            wanted = "fast" if activation_tuple(key) in wanted_fast else "slow"
            if placement["tier"] == wanted:
                continue
            if wanted == "fast" and not fast.has_room(placement["n_bytes"]):
                errors.append(f"NwbPlacement {key}: the fast share is at its headroom; not moved")
                continue
            move(key, placement, shares[placement["tier"]], shares[wanted])
            moved += 1
        except OldCopyLeft as left:
            moved += 1
            errors.append(f"NwbPlacement {key}: {left}")
        except Exception as exc:  # one file must not stop the others; retried next pass
            errors.append(f"NwbPlacement {key}: {exc}")
    return moved, errors
