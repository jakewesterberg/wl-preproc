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
``Activation`` key; the second insert is skipped because the selection already
has an activation — *not* because the idempotency keys matched, which they do
not have to — and ``populate()`` then computes that key exactly once. The
idempotency key distinguishes a network retry from a new request, which is its
documented job, and is *not* what prevents a second run. Reusing one for a
different request is refused rather than absorbed; see ``_reject_key_reuse``.

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

from wl_preproc.schema import DEFAULT_PREFIX, core

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
    # Provenance: which request produced this. A projected foreign key, and
    # deliberately BELOW the divider -- a plain `-> Request` would drag
    # idempotency_key into this table's primary key and contradict section 5.2
    # exactly the way making Activation `Computed` would. Declared as a bare
    # `varchar(64)` until 2026-08-14, which made section 4.3's provenance claim
    # a convention rather than a constraint: an Activation could name a Request
    # that does not exist, verified. Confirmed on DataJoint 2.3.2 that this form
    # declares below the divider and leaves the primary key untouched.
    -> Request.proj(request_key='idempotency_key')
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
    # NOT ENFORCED HERE: both foreign keys reach the same Session, so a block
    # is guaranteed to belong to the right session -- and to nothing narrower.
    # Nothing stops pairing an activation with a block lying outside its
    # montage's [start_s, end_s) window, which is a block the sort must not
    # cover. The check needs Montage.start_s/end_s against Block.start_s/end_s
    # and so cannot be a foreign key. Nothing writes this table in 1c-1; the
    # responder (1c-3) is its first writer and owns enforcing the window.
    -> Activation
    -> core.Block
    """


def _canonicalise(value):
    """`value` with every dict's keys in sorted order, at every depth.

    `datajoint.blob.pack` serialises a dict in *insertion* order, so two
    payloads that differ in nothing but key order pack to different bytes.
    This is the same canonicalisation `paramset.content_hash` performs with
    `json.dumps(..., sort_keys=True)` and for the same reason, including the
    depth: `sort_keys` sorts at every level, not just the top, because a
    payload's nested dicts were built in whatever order their producer chose
    too. Lists and tuples keep their type and their order — order is meaning
    there, not incidental.
    """
    if isinstance(value, dict):
        return {key: _canonicalise(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonicalise(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_canonicalise(item) for item in value)
    return value


def _payload_differs(stored, given) -> bool:
    """True if two `<blob>` payloads are not the same value.

    `stored != given` is the right comparison for the JSON-shaped payloads
    this boundary actually carries — two dicts built in different key orders
    are the same request, and `!=` says so where a serialized-bytes comparison
    would not. But `!=` on a payload containing a numpy array returns an array
    rather than a bool, and `bool()` of that raises, so the fallback compares
    exactly what the column would store.

    The fallback canonicalises key order first, because `pack` is key-order
    sensitive. Without that it contradicted the paragraph above for precisely
    the payloads that reach it: a genuine retry carrying a numpy array, whose
    dict happened to be built in a different order, packed to different bytes
    and was refused as a different request — the inverse of the defect
    `_reject_key_reuse` exists to fix, and unreachable by the `!=` path that
    documents the intended behaviour.
    """
    try:
        return bool(stored != given)
    except (ValueError, TypeError):
        from datajoint.blob import pack

        return pack(_canonicalise(stored)) != pack(_canonicalise(given))


def _reject_key_reuse(
    stored: dict,
    *,
    idempotency_key: str,
    task_type: str,
    origin: str,
    payload: dict,
    requested_by: str | None,
    selection_key: dict,
) -> None:
    """Raise if an existing `Request` under this key is a different request.

    `Request.insert1(..., skip_duplicates=True)` used to make a reused key a
    silent no-op *on Request only* — execution continued, so a second
    `Activation` was created naming a key whose recorded payload belongs to
    somebody else's request, and the second ask went unrecorded entirely. That
    contradicts section 4.2's "records what was asked", and both future
    consumers take keys from outside this process, which is exactly where
    reuse happens.

    **An idempotency key is caller-scoped.** `requested_by` is compared like
    any other field: the same key presented by a *different* requester is a
    collision between two people's key spaces, not one person's retry. It went
    uncompared until 2026-08-14, so the second person's ask was absorbed into
    the first person's row and went unrecorded — the same section 4.2 failure
    this whole function exists to prevent, arriving through the one field that
    identifies who asked.

    **What cannot be checked here.** The selection is compared against the
    activations this key actually produced. If the original submission deduped
    onto a pre-existing `Activation` (see
    `test_two_requests_for_one_selection_yield_one_activation`), no row names
    that request at all, so there is nothing recorded to compare a selection
    against and only `(task_type, origin, payload, requested_by)` is checked.
    Closing that needs a selection recorded on `Request` or `Activation`;
    adding a column nothing consumes was declined for 1c-1 and is equally free
    in 1c-3, which is pre-data too.
    """
    conflicts = []
    if stored["task_type"] != task_type:
        conflicts.append(f"task_type {stored['task_type']!r} vs {task_type!r}")
    if stored["origin"] != origin:
        conflicts.append(f"origin {stored['origin']!r} vs {origin!r}")
    if stored["requested_by"] != requested_by:
        conflicts.append(f"requested_by {stored['requested_by']!r} vs {requested_by!r}")
    if _payload_differs(stored["payload"], payload):
        conflicts.append("payload differs from the one already recorded")

    produced = (Activation & {"request_key": idempotency_key}).keys()
    if produced and not any(
        all(row[attr] == value for attr, value in selection_key.items()) for row in produced
    ):
        asked_before = [{attr: row[attr] for attr in selection_key} for row in produced]
        conflicts.append(f"selection {selection_key} vs {asked_before}")

    if conflicts:
        raise dj.DataJointError(
            f"idempotency key {idempotency_key!r} is already recorded against a "
            "different request, so this is key reuse rather than a retry: "
            + "; ".join(conflicts)
            + ". Accepting it would leave this ask unrecorded (section 4.2: Request "
            "records what was asked) and point its Activation at the earlier ask's "
            "payload. Mint a new idempotency key."
        )


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

    **Never call this from inside a transaction.** DataJoint transactions do
    not nest — ``Connection.start_transaction`` raises outright if one is
    already open — and this function opens its own, because the whole point is
    that the ``Request`` and its ``Activation`` land together or not at all.
    So ``submit()`` is the outermost unit of work, and neither the ingest
    watcher (1c-2) nor the responder (1c-3) may wrap it in a transaction of
    their own to bundle it with other writes. That constraint is checked below
    rather than left to a plan, for the same reason the ``is_activated()``
    guard is: the failure otherwise surfaces from inside DataJoint as a
    connection-level complaint that says nothing about which call was wrong.

    Reusing an ``idempotency_key`` for a *different* request raises
    ``DataJointError``; an identical resubmission is a retry and returns the
    same key. A *different* ``requested_by`` under the same key counts as a
    different request: the key space is caller-scoped. See
    ``_reject_key_reuse``, including what it cannot check.
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

    if dj.conn().in_transaction:
        raise dj.DataJointError(
            "submit() opens its own transaction and DataJoint transactions do "
            "not nest, so it cannot be called from inside one. Call it as its "
            "own unit of work; see submit()'s docstring."
        )

    import datetime as _dt

    selection_key = {
        k: selection[k] for k in ("subject", "session_datetime", "montage_id")
    }

    with dj.conn().transaction:
        # Read before writing, rather than `insert1(..., skip_duplicates=True)`:
        # skip_duplicates cannot tell a retry from key reuse, and silently chose
        # "retry" for both. See _reject_key_reuse.
        prior = Request & {"idempotency_key": idempotency_key}
        if prior:
            _reject_key_reuse(
                prior.fetch1(),
                idempotency_key=idempotency_key,
                task_type=task_type,
                origin=origin,
                payload=payload,
                requested_by=requested_by,
                selection_key=selection_key,
            )
        else:
            # No skip_duplicates: the only duplicate reachable here is another
            # writer inserting the same key between the read above and this
            # write, which must fail loudly and roll back the whole submission
            # rather than quietly proceed on somebody else's Request row.
            Request.insert1(
                {
                    "idempotency_key": idempotency_key,
                    "task_type": task_type,
                    "origin": origin,
                    "payload": payload,
                    "requested_by": requested_by,
                    "requested_at": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None),
                }
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


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}request`. Idempotent."""
    core.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}request", create_tables=True)
