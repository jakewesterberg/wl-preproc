# wl_preproc/responder/jobs.py
"""`JobRequest` -> rows. Design spec section 6.1. The only module here that writes.

Two sub-projects treated montage boundaries as a blocker before this module
existed: 1c-1 narrowed `submit()` to canonical activations because it had no
block set, and 1c-2 avoided `submit()` entirely because timebase and coverage
populate from `Session` keys alone. **The answer was already in the frozen
contract** (design spec section 1): `contracts.protocol.MetadataBundle`
carries `blocks` and `montage_boundaries` inbound with EVERY request, and its
own docstring says why -- "everything wl-preproc needs from the ELN arrives in
the request payload." `core.Montage`'s own comment says it is "Sourced from
wl.works `item_insertion` and nothing else", and `core.Block` carries
`works_block_id` as the link. So recording them here is not measuring or
guessing at a boundary; it is wl.works' own authored record, arriving by the
exact route its frozen contract already describes.

**Two corrections carried from Task 4's review, applied here rather than
re-derived:**

1. **`accept()` opens no transaction of its own.** `submit()`/
   `submit_derivative()` each already guard on `dj.conn().in_transaction` and
   raise -- DataJoint transactions do not nest -- and `submit()`'s own
   docstring says directly that neither the ingest watcher nor the responder
   may wrap it to bundle it with other writes. So `accept()` writes `Montage`
   and `Block` as two independently idempotent, un-transacted inserts
   (`skip_duplicates=True`, exactly `ingest/landing.py`'s own shape and
   reasoning -- "a partial run followed by a re-run converges on the same
   rows" without one), and only then calls `submit`/`submit_derivative`,
   which open and own their own transaction for the `Request`+`Activation`
   pair. See `test_accept_refuses_to_run_inside_a_transaction`.
2. **`accept()` owns the montage window.** `ActivationBlock`'s own comment
   names this module's function as "its first writer" and says it "owns
   enforcing the window" between a montage's `[start_s, end_s)` and the
   blocks a derivative selects -- a check `submit_derivative` itself does not
   make (it has no `Montage`/`Block` timing to compare against; verified it
   currently accepts a block at `[20.0, 24.0)` against a montage of
   `[0.0, 12.0)`). See `_reject_blocks_outside_montage_window`.

**Existing `Montage`/`Block` rows are never overwritten.** Both inserts below
use `skip_duplicates=True`: wl.works owns these records, and a later request
naming different boundaries for a montage or block already on file is
wl.works correcting its own record -- their call to make explicitly, not
something to infer from whichever payload happened to arrive most recently.
"""

from __future__ import annotations

from wl_preproc.contracts.protocol import JobRequest
from wl_preproc.ingest import landing
from wl_preproc.schema import DEFAULT_PREFIX, core
from wl_preproc.schema import request as schema_request

# The two keys `accept()` itself reads out of an arbitrary, wl.works-supplied
# `selection` dict. `subject` is deliberately NOT among them: session identity
# comes from `metadata.subject` (the ELN's own record), never from anything
# named "subject" inside `selection` -- see `accept()`'s own docstring.
_REQUIRED_SELECTION_KEYS = ("session_datetime", "montage_id")


def _require_selection_keys(selection: dict) -> None:
    missing = [key for key in _REQUIRED_SELECTION_KEYS if key not in selection]
    if missing:
        raise ValueError(
            f"selection is missing required key(s) {missing}; got {sorted(selection)}"
        )


def _reject_oversized_subject(subject: str) -> None:
    if len(subject) > landing.SUBJECT_MAX_LEN:
        raise ValueError(
            f"metadata.subject {subject!r} is {len(subject)} characters, over "
            f"the {landing.SUBJECT_MAX_LEN}-character limit element-animal's "
            "Subject.subject column enforces (landing.SUBJECT_MAX_LEN)"
        )


def _reject_blocks_outside_montage_window(
    block_ids: list[int], *, session_key: dict, montage_id: int, montage_row: dict
) -> None:
    """Raise if any of `block_ids` names a `core.Block` row whose own
    `[start_s, end_s)` is not fully contained in the montage's.

    `ActivationBlock`'s own comment: a block the montage does not cover in
    time is a block the sort must not cover either, and nothing below the
    responder checks this (`submit_derivative` has no `Montage` to compare
    against). One query for every block on this session rather than one
    `fetch1()` per named id: cheap given a session's block count, and a
    `block_id` this session has no `Block` row for at all is simply absent
    from `rows` -- silently not flagged as "outside the window" here, since
    that is a different failure (an unknown block) than the one this
    function exists to catch, and it still surfaces on its own once
    `submit_derivative` tries to write `ActivationBlock`'s real foreign key
    to `core.Block`.
    """
    wanted = sorted(set(block_ids))
    rows = {row["block_id"]: row for row in (core.Block & session_key).to_dicts()}
    offending = [
        f"block {block_id} [{rows[block_id]['start_s']}, {rows[block_id]['end_s']})"
        for block_id in wanted
        if block_id in rows
        and (
            rows[block_id]["start_s"] < montage_row["start_s"]
            or rows[block_id]["end_s"] > montage_row["end_s"]
        )
    ]
    if offending:
        raise ValueError(
            f"selection names block(s) outside montage {montage_id}'s window "
            f"[{montage_row['start_s']}, {montage_row['end_s']}): " + "; ".join(offending)
        )


def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
    """A validated `JobRequest` becomes `Montage`/`Block`/`Request`/`Activation`
    rows. Design spec section 6.1. Returns the `Activation` primary key.

    Raises `ValueError` for a request that cannot be honoured: a `selection`
    missing `session_datetime` or `montage_id`; a `metadata.subject` longer
    than `landing.SUBJECT_MAX_LEN`; a `montage_id` with no boundary on record
    and none supplied in this request either; or a `block_ids` entry naming a
    block outside its montage's window (see `_reject_blocks_outside_montage_
    window`). These four checks run against plain input and the two DB-backed
    ones below, in that order -- the two structural checks need no database
    connection at all, so a malformed request is rejected before this
    function ever touches one.

    Session identity is `metadata.subject` plus `selection["session_datetime"]`,
    normalised through `landing.to_naive_utc` -- the one conversion every
    other datetime key in this codebase goes through, so this key lands on
    the exact naive value `ingest/landing.py` would already have written for
    the same session (see that module's own docstring on why two call sites
    converting differently is how two "equal" keys stop being equal).
    `selection`'s own `subject`, if wl.works ever sends one, is not read: the
    ELN's record of who this is is `metadata.subject`.

    `selection["block_ids"]`, when present and non-empty, makes this a
    derivative (`submit_derivative`); its absence, or an empty list, makes it
    canonical (`submit`).
    """
    selection = request.selection
    _require_selection_keys(selection)

    metadata = request.metadata
    _reject_oversized_subject(metadata.subject)

    schema_request.activate(prefix=prefix)

    session_datetime = landing.to_naive_utc(selection["session_datetime"])
    montage_id = selection["montage_id"]
    session_key = {"subject": metadata.subject, "session_datetime": session_datetime}
    montage_key = {**session_key, "montage_id": montage_id}

    # Step 1 (design spec section 6.1): Montage rows, insert-if-absent.
    montage_rows = [
        {
            **session_key,
            "montage_id": boundary["montage_id"],
            "start_s": boundary["start_s"],
            "end_s": boundary["end_s"],
        }
        for boundary in metadata.montage_boundaries
    ]
    if montage_rows:
        core.Montage.insert(montage_rows, skip_duplicates=True)

    # Step 2: Block rows, insert-if-absent, works_block_id set -- the link
    # back to wl.works' own authored row (core.Block's own comment).
    block_rows = [
        {
            **session_key,
            "block_id": block["block_id"],
            "task_type": block["task_type"],
            "start_s": block["start_s"],
            "end_s": block["end_s"],
            "works_block_id": block.get("works_block_id"),
        }
        for block in metadata.blocks
    ]
    if block_rows:
        core.Block.insert(block_rows, skip_duplicates=True)

    if not (core.Montage & montage_key):
        raise ValueError(
            f"no montage {montage_id} is on record for {session_key} and "
            "metadata.montage_boundaries did not supply one either"
        )

    block_ids = selection.get("block_ids") or []

    # Correction 2: accept() owns the montage window (module docstring).
    if block_ids:
        montage_row = (core.Montage & montage_key).fetch1()
        _reject_blocks_outside_montage_window(
            list(block_ids),
            session_key=session_key,
            montage_id=montage_id,
            montage_row=montage_row,
        )

    # The payload stored as evidence ("the request as received", Request's
    # own comment), with `selection.session_datetime` normalised the same
    # way the key above is. Left as the caller's raw value, this would
    # compare unequal to itself on a genuine retry: DataJoint's blob codec
    # drops a datetime's tzinfo on its very first round trip through the
    # database (confirmed directly -- pack/unpack an aware value and it
    # comes back naive), so a second accept() call for the same
    # idempotency_key would build a fresh, still-aware payload and compare
    # it against the first call's now-naive stored copy.
    # `_reject_key_reuse`'s `stored != given` treats an aware and a naive
    # datetime as unequal (Python: "aware and naive datetime objects are
    # never equal"), so an honest retry would be refused as key reuse.
    # Normalising here, the same way on every call, keeps both sides naive
    # and therefore equal to each other on a genuine retry. See
    # test_accept_is_idempotent_on_the_same_key.
    payload = request.model_dump()
    payload["selection"] = {**payload["selection"], "session_datetime": session_datetime}

    if block_ids:
        return schema_request.submit_derivative(
            idempotency_key=request.idempotency_key,
            task_type=request.domain,
            origin="wl_works",
            selection=montage_key,
            block_ids=list(block_ids),
            payload=payload,
            requested_by=metadata.experimenter,
        )

    return schema_request.submit(
        idempotency_key=request.idempotency_key,
        task_type=request.domain,
        origin="wl_works",
        selection=montage_key,
        payload=payload,
        requested_by=metadata.experimenter,
    )
