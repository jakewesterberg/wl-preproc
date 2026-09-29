"""What `GET /nwb` and `PUT /nwb/active` do (design spec
`2026-09-29-nwb-publishing-design.md` sections 5 and 6).

`handler.py` owns HTTP and imports no DataJoint; this module owns the
database, as `jobs.py` does for `POST /jobs`, and `server.py` wires the two
together under the responder's one lock."""

from __future__ import annotations

import datetime

from wl_preproc.contracts.protocol import ActiveSetRequest, ActiveSetResponse, NwbListing
from wl_preproc.schema import DEFAULT_PREFIX


def _key_json(key: dict) -> dict:
    moment = key["session_datetime"]
    if isinstance(moment, datetime.datetime) and moment.tzinfo is not None:
        moment = moment.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return {"subject": key["subject"], "session_datetime": moment.isoformat(),
            "montage_id": int(key["montage_id"]), "activation_id": int(key["activation_id"])}


def list_files(since: int | None, prefix: str = DEFAULT_PREFIX) -> dict:
    """Every activation whose file changed after `since` (all of them when
    None): its status, where its file is now, and its description."""
    from wl_preproc.nwb.publish import activation_tuple, current_placement, key_of
    from wl_preproc.schema import nwb as nwb_schema

    nwb_schema.activate(prefix=prefix)
    changes = nwb_schema.NwbChange.to_dicts() if since is None else \
        (nwb_schema.NwbChange & f"change_seq > {int(since)}").to_dicts()
    cursor = max((change["change_seq"] for change in changes), default=since or 0)
    keys = {activation_tuple(change): key_of(change) for change in changes}
    files = []
    for _tuple, key in sorted(keys.items()):
        rows = (nwb_schema.NwbFile & key).to_dicts()
        if not rows:
            continue
        row, placement = rows[0], current_placement(key)
        files.append({
            "activation": _key_json(key),
            "identifier": row["nwb_identifier"],
            "status": row["status"],
            "reason": row["reason"],
            "placement": None if placement is None else {
                field: placement[field] for field in ("tier", "host", "share", "path", "n_bytes")},
            "description": row["description"],
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
