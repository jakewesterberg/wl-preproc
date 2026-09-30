"""What `GET /nwb` and `PUT /nwb/active` do (design spec
`2026-09-29-nwb-publishing-design.md` sections 5 and 6).

`handler.py` owns HTTP and imports no DataJoint; this module owns the
database, as `jobs.py` does for `POST /jobs`, and `server.py` wires the two
together under the responder's one lock."""

from __future__ import annotations

import datetime

from wl_preproc.contracts.protocol import ActiveSetRequest, ActiveSetResponse, NwbListing
from wl_preproc.schema import DEFAULT_PREFIX

_KEY = ("subject", "session_datetime", "montage_id", "activation_id")


def _key_json(key: dict) -> dict:
    moment = key["session_datetime"]
    if isinstance(moment, datetime.datetime) and moment.tzinfo is not None:
        moment = moment.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return {"subject": key["subject"], "session_datetime": moment.isoformat(),
            "montage_id": int(key["montage_id"]), "activation_id": int(key["activation_id"])}


def list_files(since: int | None, prefix: str = DEFAULT_PREFIX) -> dict:
    """Every activation whose file changed after `since` (all of them when
    None): its status, where its file is now, and its description."""
    from wl_preproc.nwb.publish import activation_tuple, key_of
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema import request as request_schema

    nwb_schema.activate(prefix=prefix)
    changes = nwb_schema.NwbChange.proj(*_KEY).to_dicts() if since is None else \
        (nwb_schema.NwbChange & f"change_seq > {int(since)}").proj(*_KEY).to_dicts()
    cursor = max((change["change_seq"] for change in changes), default=since or 0)
    keys = list({activation_tuple(change): key_of(change) for change in changes}.values())
    # Two queries for the whole listing, not two per file, and only the
    # columns it returns (the final review's M2).
    rows = {activation_tuple(row): row for row in (nwb_schema.NwbFile & keys).proj(
        "nwb_identifier", "status", "reason", "description").to_dicts()} if keys else {}
    # Which activation supersedes which: the old file stays, marked.
    successor = {}
    montages = list({(k["subject"], k["session_datetime"], k["montage_id"]): {
        "subject": k["subject"], "session_datetime": k["session_datetime"], "montage_id": k["montage_id"]}
        for k in keys}.values())
    if montages:
        for row in (request_schema.Activation & montages & "supersedes IS NOT NULL").to_dicts():
            successor[(row["subject"], row["session_datetime"], row["montage_id"], row["supersedes"])] = \
                row["activation_id"]
    latest = {}
    for placement in ((nwb_schema.NwbPlacement * nwb_schema.NwbChange & keys).to_dicts() if keys else []):
        held = latest.get(activation_tuple(placement))
        if held is None or placement["change_seq"] > held["change_seq"]:
            latest[activation_tuple(placement)] = placement
    files = []
    for moment in sorted(rows):
        row, placement, key = rows[moment], latest.get(moment), key_of(rows[moment])
        files.append({
            "activation": _key_json(key),
            "identifier": row["nwb_identifier"],
            "status": row["status"],
            "reason": row["reason"],
            "placement": None if placement is None else {
                field: placement[field] for field in ("tier", "host", "share", "path", "n_bytes")},
            "description": row["description"],
            "superseded_by": successor.get((key["subject"], key["session_datetime"], key["montage_id"],
                                            key["activation_id"])),
        })
    return NwbListing.model_validate({"cursor": cursor, "files": files}).model_dump(mode="json")


def set_active(request: ActiveSetRequest, prefix: str = DEFAULT_PREFIX) -> dict:
    """Record the whole active set as received; name the activations this
    host has no row for yet. The placement stage acts on it next pass."""
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema import request as request_schema

    nwb_schema.activate(prefix=prefix)
    activations = [_key_json(key.model_dump()) for key in request.activations]
    nwb_schema.ActiveSet.insert1({
        "received_at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
        "requested_by": request.requested_by,
        "activations": activations,
    })
    unknown = [key for key in activations
               if not request_schema.Activation & {**key, "session_datetime": datetime.datetime.fromisoformat(
                   key["session_datetime"])}]
    return ActiveSetResponse.model_validate({"accepted": len(activations), "unknown": unknown}).model_dump(mode="json")
