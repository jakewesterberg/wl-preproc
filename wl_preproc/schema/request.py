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
returns on any existing CANONICAL ``Activation`` row for the selection, so a
second ``activation_id`` is never allocated — every activation this function
writes is ``activation_id=0``. That is correct for a canonical activation
(parent spec section 8.3: exactly one current per (session, montage)) and
wrong for a derivative (section 8.3: "any hand-picked subset… unbounded,
additive"), which needs a real allocator and a block set to key on.
``submit()``'s selection carries no block set, so it has no way to form a
derivative — accepting a ``role`` parameter here would silently hand back the
canonical activation's key for any caller that asked for a derivative. So the
parameter is gone rather than half-supported; making derivatives real belongs
to the responder (design spec section 9.1), once it can supply a block set —
which is what ``submit_derivative``, below, does. Because a derivative can
now exist on a selection with no canonical yet, the dedupe's own query is
scoped to ``role="canonical"`` (added alongside ``submit_derivative`` —
before it existed, every ``Activation`` row sharing a selection was
necessarily a canonical, so the scope was implicit rather than written down).
"""

from __future__ import annotations

import hashlib
import json

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
    # Null for a canonical activation, whose identity is (session, montage)
    # per section 8.3. Set for a derivative, whose identity is its block set --
    # which is why section 11.3's "a request whose (selection, task type) is
    # already in flight returns the running one" can be a lookup here rather
    # than a lock. 1c-1 deferred this column to 1c-3 on the grounds that both
    # are pre-data and adding it later is equally cheap; 1c-2 then recorded a
    # residual that only this column closes -- a reused idempotency key whose
    # first submission deduped onto an existing activation recorded nowhere
    # which selection it had asked for.
    selection_hash = null : varchar(64)
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


def selection_hash(task_type: str, block_ids: list[int]) -> str:
    """Content hash of a derivative's identity: its task type and block set.

    Sorted and de-duplicated first, because the block set is a *set*: two
    requests naming the same blocks in a different order, or naming the same
    block twice, are the same selection, and hashing them differently would
    start a second run for work already in flight (section 11.3). `set(...)`
    is what collapses a repeat; `sorted()` alone would not, since `[1, 1, 2]`
    is already sorted and stays three elements long.

    Same primitive and digest size as `paramset.content_hash` -- blake2b at
    digest_size=16 -- and the same canonicalisation,
    `json.dumps(..., sort_keys=True, separators=(",", ":"))`, read from that
    function rather than assumed: this project has already spent a review
    round on a digest-size claim that drifted from the code it described.
    """
    payload = json.dumps(
        {"task_type": task_type, "block_ids": sorted(set(block_ids))},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()


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

    **What this also checks — and does not — for a derivative.**
    `selection_key` is an arbitrary dict compared attribute-by-attribute
    against each produced row, so `submit_derivative` widens the check for
    free by naming one more attribute: `selection_hash`. This closes the gap
    only when the key's OWN submission is what produced the row being
    compared against — i.e., when `produced` above is non-empty because
    THIS key's earlier call inserted a fresh derivative `Activation`, rather
    than deduping onto one created under a different key. In that case, a
    later call under the same key naming a different block set is now
    caught, not just a different session or montage.

    It does **not** close the residual the previous paragraph describes. If
    the key's first submission instead deduped onto a pre-existing
    derivative created by some OTHER key, no row names this key at all —
    `produced` is empty, exactly as above — and a second call under this key
    with a *different* block set is silently accepted, allocating its own
    activation, rather than refused. That is the same hole the previous
    paragraph describes, for a derivative rather than a canonical, and this
    column alone cannot close it: closing it needs a selection recorded on
    `Request` itself, which remains out of scope here, exactly as the
    previous paragraph already says.

    `submit()`'s canonical `selection_key` never names `selection_hash` (a
    canonical's is always null — see `Activation`'s definition), so this
    paragraph is inert for canonical submissions; it only fires when a caller
    (only `submit_derivative` does) asks for it.
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

    # .to_dicts(), not .keys(): keys() returns primary-key columns only, and
    # selection_key may now name selection_hash -- a secondary column, added
    # so submit_derivative's reused-key check can see it too (see the
    # docstring paragraph above). Harmless for submit()'s canonical callers:
    # their selection_key never names a column outside the primary key, so
    # the extra columns fetched here simply go unread.
    produced = (Activation & {"request_key": idempotency_key}).to_dicts()
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
    pre-existing CANONICAL ``Activation`` for the selection (a derivative on
    the same selection, with no canonical yet, does not count — see the
    dedupe's own comment). See the module docstring.

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
    # role="canonical" added alongside the fix below (Task 4, review round 2):
    # a bare selection_key names only the three montage attributes, which a
    # derivative sharing that montage satisfies trivially. Before
    # submit_derivative existed there was nothing else a produced row could
    # be, so the gap was invisible; now, the same key used first for
    # submit_derivative() and then for submit() found its own derivative row
    # in _reject_key_reuse's `produced`, matched on those three attributes,
    # and was accepted as a retry it never was -- creating a second
    # Activation (a canonical one, at activation_id 0) under a key that had
    # already produced a derivative. `role` closes it the same way
    # submit_derivative's `selection_hash` addition closes the analogous gap
    # in the other direction: naming one more attribute in the same generic,
    # already-generic comparison. See _reject_key_reuse's docstring and
    # test_reusing_a_derivative_key_for_submit_is_refused /
    # test_reusing_a_submit_key_for_a_derivative_is_refused.
    canonical_selection_key = {**selection_key, "role": "canonical"}

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
                selection_key=canonical_selection_key,
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

        # role="canonical" (canonical_selection_key, built above): before
        # submit_derivative existed, submit() was the only writer of
        # Activation, so every row sharing selection_key was necessarily a
        # canonical and an unscoped match could not find anything else.
        # submit_derivative can now leave a derivative on this exact
        # (subject, session_datetime, montage_id) with no canonical yet —
        # see its own docstring on why activation_id 0 is reserved for
        # exactly this reason — and an unscoped match here would fetch1()
        # that derivative and return ITS activation_id as though it were the
        # canonical result: caught live by
        # test_a_derivative_before_any_canonical_does_not_claim_activation_id_zero
        # in tests/schema/test_request.py.
        existing = Activation & canonical_selection_key
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


# submit_derivative's allocate-then-insert step reads the current max
# activation_id and writes max+1 with no lock, so concurrent derivative
# submissions on the same montage can race for the same id -- section 11.3
# expects exactly this ("concurrent runs are legitimate and expected"). A
# bounded retry resolves it, mirroring paramset.register's own retry loop;
# see submit_derivative's docstring ("2. Index allocation...") for why the
# two reads inside that retry cannot be DataJoint's normal (non-locking)
# read here, unlike in paramset.register.
_MAX_DERIVATIVE_ALLOCATE_ATTEMPTS = 10


def _insert_new_derivative(row: dict) -> None:
    """The single write submit_derivative uses to claim a fresh activation_id.

    Split out from submit_derivative purely so tests can simulate the
    concurrent-allocation collision race by making this one call raise a
    duplicate-key error, without reaching into DataJoint's table-class
    dispatch machinery to intercept insert1 directly. Exactly
    `paramset._insert_new`'s reason for existing, and `paramset.register`'s
    own test (`test_register_recovers_from_a_concurrent_index_collision`)
    already demonstrates one way to exercise the seam it creates: a
    same-connection call that plants a rival row before calling through to
    the real insert. submit_derivative's own tests additionally use a
    genuinely separate connection for this, because the same-connection
    trick cannot exercise the locking-read fix `_locking_read` exists for —
    a single connection always sees its own writes regardless of isolation
    level, which is exactly what would hide a bug there. See test_request.py.
    """
    Activation.insert1(row, skip_duplicates=False)


def _locking_read(query_expression) -> list[dict]:
    """`query_expression`'s rows, read via `LOCK IN SHARE MODE` rather than
    DataJoint's normal (non-locking) read.

    DataJoint opens every transaction with `START TRANSACTION WITH
    CONSISTENT SNAPSHOT` (confirmed in the installed 2.3.2 package,
    `datajoint/adapters/mysql.py`), which fixes an ordinary SELECT's view of
    the database to what existed when the transaction opened. Verified live
    against a real MySQL 8.0 instance before writing this: inside an open
    transaction, a plain SELECT did not see a row a SEPARATE connection
    committed afterward, while the identical query with `LOCK IN SHARE MODE`
    appended did — and the INSERT-level uniqueness check that raises
    `DuplicateError` is unaffected either way, since it is never
    snapshot-limited. `submit_derivative`'s retry loop needs to see a
    rival's just-committed row for real, to tell "identical selection, not
    contention" from "genuine contention" the way section 11.3 requires —
    which a plain re-read cannot do under this project's actual transaction
    semantics, and which the same-connection trick
    `tests/schema/test_paramset.py` uses for `paramset.register`'s own retry
    test cannot expose either: a single connection always sees its own
    writes, committed or not, regardless of isolation level.

    Built on `make_sql()` — DataJoint's own SELECT-generation — rather than
    hand-written SQL, so quoting, datetime formatting, and NULL comparisons
    stay exactly what DataJoint would already produce for this query;
    `LOCK IN SHARE MODE` is the only part added here. DataJoint has no
    locking-read method of its own to call instead (checked: no `for_update`
    argument or equivalent anywhere in its public API), which is why this
    drops to `dj.conn().query()`, the same primitive `Connection.transaction`
    itself is built on.
    """
    cursor = dj.conn().query(query_expression.make_sql() + " LOCK IN SHARE MODE", as_dict=True)
    return list(cursor.fetchall())


def submit_derivative(
    idempotency_key: str,
    task_type: str,
    origin: str,
    selection: dict,
    block_ids: list[int],
    payload: dict,
    requested_by: str | None = None,
) -> dict:
    """Record a request and the derivative activation its block set selects.

    Mirrors ``submit()``'s structure — the activation guard, the no-nesting
    guard, ``_reject_key_reuse``, one transaction — and differs in exactly
    three places, below. See the module docstring's "``submit()`` only ever
    produces canonical activations" paragraph for why ``submit()`` itself
    cannot do this: its selection carries no block set, so it has nothing to
    key a derivative's identity on.

    **1. The dedupe is on the selection, not the key.**
    ``digest = selection_hash(task_type, block_ids)`` is this derivative's
    identity (parent spec section 8.3: a derivative's identity is its block
    set, unlike a canonical's, which is its (session, montage)). Before
    inserting anything, an existing ``Activation`` already carrying that hash
    for this session and montage is returned as-is — section 11.3's "a
    request whose (selection, task type) is already in flight returns the
    running one instead of starting a second" — the same structural-lookup
    shape ``submit()`` uses for canonical dedupe, keyed on the hash instead
    of on (session, montage) alone.

    **2. Index allocation retries on a real collision, resolving it the way
    section 11.3 requires.** A derivative needs a real ``activation_id``
    allocator — ``submit()`` can pin ``0`` only because its dedupe returns
    early on ANY existing canonical, so it never allocates a second one.
    Here, ``activation_id`` is read as the current max for this montage and
    written as ``max + 1``, inside this same transaction, with NO
    ``skip_duplicates``: a primary-key collision must raise rather than be
    silently absorbed, the identical reasoning ``paramset.register`` documents
    at length for its own index allocation — ``skip_duplicates`` cannot tell
    "someone else already claimed this id for the same selection" from
    "someone else claimed it for a DIFFERENT one", and would discard this
    submission's row under no index at all rather than surface the collision.

    A raised collision is then handled the way ``paramset.register`` handles
    its own, bounded at ``_MAX_DERIVATIVE_ALLOCATE_ATTEMPTS`` attempts:
    re-check whether the rival that just won this id shares this exact
    selection (``selection_hash`` included). If it does, that is not
    contention — just a lost race to write something equivalent — and
    section 11.3's "returns the running one instead of starting a second"
    applies exactly as it does for the sequential case, so the rival's key
    is returned rather than raising. A previous version of this function
    stopped here and let the collision raise unconditionally, which is wrong
    for exactly this case: two researchers submitting the identical
    selection concurrently is the ordinary shape of "legitimate and
    expected" concurrent runs section 11.3 describes, not an error. If the
    rival's selection differs, this is genuine contention over the same id
    for two different asks, so the allocation retries against a fresh read
    of the current max, so the second researcher's distinct, legitimate
    submission gets its own id rather than being destroyed outright.

    Two differences from ``paramset.register``'s version of this loop, both
    because ``submit_derivative`` runs its whole body inside one open
    transaction and ``paramset.register`` does not. First: ``paramset.
    register``'s retries are free fresh reads — each attempt is its own
    auto-committing statement, so a plain re-read there already sees the
    latest committed state. Here, DataJoint opens every transaction with
    ``START TRANSACTION WITH CONSISTENT SNAPSHOT`` (confirmed in the
    installed 2.3.2 package), which fixes an ordinary SELECT's view of the
    database for the whole transaction's lifetime — verified live against a
    real MySQL 8.0 instance that a plain re-read here would still show the
    pre-collision state even after a genuinely separate connection commits a
    rival row, while a locking read (``LOCK IN SHARE MODE``) sees it. Both
    re-reads inside the retry therefore go through ``_locking_read`` instead
    of DataJoint's normal query interface; see its own docstring for the
    live verification and why the same-connection trick
    ``tests/schema/test_paramset.py`` uses for ``paramset.register``'s own
    retry test could not have caught a plain re-read being wrong here (a
    single connection always sees its own writes, which is exactly what
    would hide this). Second: exhausting the bound raises a bare
    ``DataJointError`` here rather than a dedicated exception type, matching
    this module's own existing convention (the ``is_activated()`` and
    ``in_transaction`` guards above raise the same way) rather than
    borrowing ``paramset.ContentionExhausted``, which names a different
    module's registration path and would be a strange type for a caller of
    this function to have to catch.

    Zero is reserved for canonical even when none has been submitted yet for
    this montage: a montage with no ``Activation`` rows at all allocates its
    first derivative at ``1``, not ``0``. ``submit()`` always targets
    ``activation_id=0`` with ``skip_duplicates=True`` for its canonical row;
    if a derivative had already claimed ``0``, a later canonical submission
    would silently no-op against the derivative's row instead of inserting
    its own, and hand its caller a key that names somebody else's derivative
    under the role it asked for. Reserving ``0`` keeps that collision
    unreachable regardless of which kind of activation a montage sees first —
    a derivative may legitimately be requested before any canonical exists.

    **3. What gets written.** ``role='derivative'``, ``selection_hash=digest``,
    and one ``ActivationBlock`` row per distinct block id — sorted and
    de-duplicated the same way ``selection_hash`` itself canonicalises
    ``block_ids``, since ``ActivationBlock`` carries no columns beyond its
    primary key: a block is either in the selection or it is not, and there
    is nothing multiplicity could mean here.

    **A derivative never supersedes a canonical.** ``supersedes`` is written
    nowhere in this function, and nowhere else in this codebase. It exists on
    ``Activation`` for a regenerated canonical to point at the one it
    replaces (see ``Activation``'s definition) — a relationship that only
    ever holds between two canonicals. A derivative's provenance is fully
    carried by ``request_key`` and ``selection_hash``; it replaces nothing,
    and no code path should ever write its ``supersedes``.

    Returns the ``Activation`` key. Reusing an ``idempotency_key`` for a
    different request — now including one that names a different block set —
    raises ``DataJointError``; see ``_reject_key_reuse``.
    """
    if not schema.is_activated():
        # Same reasoning as submit()'s guard: without this, calling before
        # activate() fails inside the transaction below with DataJoint's
        # "Cannot declare new tables inside a transaction", which says
        # nothing about which call was actually wrong.
        raise dj.DataJointError(
            "request.activate(prefix) must run before submit_derivative() — "
            "the Request/Activation tables are not yet bound to a database."
        )

    if dj.conn().in_transaction:
        raise dj.DataJointError(
            "submit_derivative() opens its own transaction and DataJoint "
            "transactions do not nest, so it cannot be called from inside "
            "one. Call it as its own unit of work; see submit()'s docstring."
        )

    if not block_ids:
        # Checked before opening the transaction, alongside the two guards
        # above: block_ids=[] is not a smaller selection, it is not a
        # selection at all. Without this, selection_hash("neural", [])
        # still produces a stable digest, so it would silently succeed —
        # writing a role='derivative' Activation with zero ActivationBlock
        # rows, a derivative covering nothing, which section 8.3's "any
        # hand-picked subset" does not describe (review round 2, Minor).
        raise dj.DataJointError(
            "submit_derivative() needs at least one block id: block_ids=[] "
            "would create a derivative covering nothing, which is not a "
            "valid selection."
        )

    import datetime as _dt

    montage_key = {
        k: selection[k] for k in ("subject", "session_datetime", "montage_id")
    }
    digest = selection_hash(task_type, block_ids)
    selection_key = {**montage_key, "selection_hash": digest}

    with dj.conn().transaction:
        # Read before writing, rather than `insert1(..., skip_duplicates=True)`:
        # skip_duplicates cannot tell a retry from key reuse. See
        # _reject_key_reuse, including what the derivative-specific
        # selection_hash comparison closes.
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
            # write, which must fail loudly and roll back the whole
            # submission rather than quietly proceed on somebody else's
            # Request row.
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
            # attribute, matching submit()'s own dedupe branch above.
            row = existing.fetch1()
            return {k: row[k] for k in Activation.primary_key}

        # max(existing activation_id for this montage) + 1, reserving 0 for
        # canonical -- see this function's "Zero is reserved" docstring
        # paragraph. to_arrays(), not fetch(): DataJoint 2.3.2 deprecates
        # bare fetch() outright, and this project's suite must stay at zero
        # warnings -- the same reason paramset.register reads its own max
        # this way. An ordinary (non-locking) read is correct for this FIRST
        # attempt -- nothing has raced this transaction yet in the
        # overwhelmingly common case -- unlike the retries below; see
        # _locking_read's docstring for why those cannot use this same read.
        used = (Activation & montage_key).to_arrays("activation_id")
        activation_id = int(max(used) + 1) if len(used) else 1

        for _ in range(_MAX_DERIVATIVE_ALLOCATE_ATTEMPTS):
            key = {**montage_key, "activation_id": activation_id}
            try:
                _insert_new_derivative(
                    {
                        **key,
                        "role": "derivative",
                        "request_key": idempotency_key,
                        "created_at": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None),
                        "selection_hash": digest,
                    }
                )
                break
            except dj.errors.DuplicateError:
                # See this function's docstring, "2. Index allocation
                # retries...", for why both reads below must be locking
                # reads rather than DataJoint's normal query interface.
                rival = _locking_read(Activation & selection_key)
                if rival:
                    # The rival that won this id shares this exact
                    # selection: not contention, just a lost race to write
                    # something equivalent. Return their key, same as the
                    # sequential dedupe above -- section 11.3.
                    return {k: rival[0][k] for k in Activation.primary_key}
                # The rival claimed this id for a DIFFERENT selection.
                # Genuine contention: retry against a fresh (locking) read
                # of the current max rather than the stale one that chose
                # the id that just collided.
                used = [row["activation_id"] for row in _locking_read(Activation & montage_key)]
                activation_id = max(used) + 1 if used else 1
                continue
        else:
            raise dj.DataJointError(
                f"submit_derivative: exhausted {_MAX_DERIVATIVE_ALLOCATE_ATTEMPTS} "
                f"attempts to allocate an activation_id for {montage_key!r} -- "
                "either sustained genuine contention or a stuck retry loop."
            )

        ActivationBlock.insert(
            [{**key, "block_id": block_id} for block_id in sorted(set(block_ids))]
        )
        return key


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}request`. Idempotent."""
    core.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}request", create_tables=True)
