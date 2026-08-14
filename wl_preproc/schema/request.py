# wl_preproc/schema/request.py
"""The protocol boundary, and the activations it fans into.

Both entry points — the ingest watcher (1c-2) and the responder (1c-3) — call
``submit()``. Neither computes: section 11.3 says the responder "inserts a
Manual-tier request row and the daemon picks it up, exactly as the ingest
watcher does", and the daemon's picking-up means populating everything
downstream of ``Activation``, not materialising ``Activation`` itself.

**One door, including the automatic one.** Section 8.3 describes canonical
activations as automatic and derivatives as requested, which reads like two
mechanisms. Section 5.4 forbids that outright — "there is no 'manual mode' that
behaves differently from automatic mode" — so the 12-hour canonical trigger
submits a request with ``origin='auto'`` like everything else. It also gives
section 8.3.1's re-fire requirement somewhere to live: re-firing is re-submitting.

**Dedupe is structural, never a lock.** Nothing here asks whether a run is in
flight. Two requests naming the same selection resolve to the same
``Activation`` key; the second insert is a duplicate and is skipped, and
``populate()`` then computes that key exactly once. The idempotency key
distinguishes a network retry from a new request, which is its documented job,
and is *not* what prevents a second run.

**``submit()`` only ever produces canonical activations.** The dedupe above
returns on ANY existing ``Activation`` row for the selection, so a second
``activation_id`` is never allocated — every activation this function writes
is ``activation_id=0``. That is correct for a canonical activation (parent
spec section 8.3: exactly one current per (session, montage)) and wrong for a
derivative (section 8.3: "any hand-picked subset… unbounded, additive"), which
needs a real allocator and a block set to key on. ``submit()``'s selection
carries no block set, so it has no way to form a derivative — accepting a
``role`` parameter here would silently hand back the canonical activation's
key for any caller that asked for a derivative. So the parameter is gone
rather than half-supported; making derivatives real belongs to the responder
(design spec section 9.1), once it can supply a block set.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.schema import core

schema = dj.Schema()

_ORIGIN_ENUM = "enum('ingest','wl_works','cli','auto')"


@schema
class Request(dj.Manual):
    definition = f"""
    # What was asked, exactly as received. Append-only; the protocol boundary.
    # Key: (idempotency_key) — supplied by wl.works, or minted locally.
    idempotency_key : varchar(64)
    ---
    task_type    : varchar(32)     # a domain from the published action list
    origin       : {_ORIGIN_ENUM}
    payload      : <blob>          # the request as received, kept as evidence
    requested_by = null : varchar(64)  # null for machine origins; see item 12
    requested_at : datetime
    """


@schema
class Activation(dj.Manual):
    definition = """
    # One NWB's worth of work: what will be computed over. Manual, not Computed —
    # a computed table inherits its parents' primary key, which would drag
    # idempotency_key into this key and contradict section 5.2.
    # Key: (subject, session_datetime, montage_id, activation_id).
    -> core.Montage
    activation_id : int
    ---
    role        : enum('canonical','derivative')
    request_key : varchar(64)   # provenance: which request produced this
    created_at  : datetime
    supersedes = null : int     # a regenerated canonical points at the old one
    """


@schema
class ActivationBlock(dj.Manual):
    definition = """
    # The block set this activation covers. Unit identity is a product of the
    # sort, so two activations over different block sets produce genuinely
    # different units and nothing may imply otherwise.
    # Key: (subject, session_datetime, montage_id, activation_id, block_id).
    -> Activation
    -> core.Block
    """


def submit(
    idempotency_key: str,
    task_type: str,
    origin: str,
    selection: dict,
    payload: dict,
    requested_by: str | None = None,
) -> dict:
    """Record a request and the canonical activation it selects, atomically.

    Returns the ``Activation`` key. Both rows land or neither does: a ``Request``
    without its ``Activation`` is an accepted request that will never run, which
    wl.works experiences as a silent hang.

    Always writes ``role='canonical'`` at ``activation_id=0`` — the only value
    the dedupe below can ever produce, since it returns early on any
    pre-existing ``Activation`` for the selection. See the module docstring.
    """
    if not schema.is_activated():
        # Both future entry points (the ingest watcher and the responder) call
        # submit() as their first contact with this module. Without this
        # check, calling it before activate() fails inside the transaction
        # below with DataJoint's "Cannot declare new tables inside a
        # transaction" — true, but useless to whoever is debugging it.
        raise dj.DataJointError(
            "request.activate(prefix) must run before submit() — the "
            "Request/Activation tables are not yet bound to a database."
        )

    import datetime as _dt

    selection_key = {
        k: selection[k] for k in ("subject", "session_datetime", "montage_id")
    }

    with dj.conn().transaction:
        Request.insert1(
            {
                "idempotency_key": idempotency_key,
                "task_type": task_type,
                "origin": origin,
                "payload": payload,
                "requested_by": requested_by,
                "requested_at": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None),
            },
            skip_duplicates=True,
        )

        existing = Activation & selection_key
        if existing:
            # One fetch1() for the whole row rather than one per key
            # attribute — Activation's key has four parts, so the brief's
            # dict-comprehension form issued four SELECTs for this branch.
            row = existing.fetch1()
            return {k: row[k] for k in Activation.primary_key}

        key = {**selection_key, "activation_id": 0}
        Activation.insert1(
            {
                **key,
                "role": "canonical",
                "request_key": idempotency_key,
                "created_at": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None),
            },
            skip_duplicates=True,
        )
        return key


def activate(prefix: str = "wlpp") -> None:
    """Bind these tables to `{prefix}request`. Idempotent."""
    core.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}request", create_tables=True)
