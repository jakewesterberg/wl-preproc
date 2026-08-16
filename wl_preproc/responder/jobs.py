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
   `[0.0, 12.0)`). See `_blocks_outside_window`.

**Existing `Montage`/`Block` rows are never overwritten.** Both inserts below
use `skip_duplicates=True`: wl.works owns these records, and a later request
naming different boundaries for a montage or block already on file is
wl.works correcting its own record -- their call to make explicitly, not
something to infer from whichever payload happened to arrive most recently.

**Review round 1 (2026-08-16) found four more things, addressed here:**

- **C1 -- validate before writing, not after.** The first draft inserted
  `Montage`/`Block` and only THEN checked the window, so a rejected request
  permanently planted the very row that caused its own rejection --
  `skip_duplicates=True` then discarded every later correction, so the
  request that fixed the boundary was rejected too, citing the stale values
  it refused to replace. `accept()` now builds every candidate row, checks
  montage-existence/window/unknown-block validity against those candidates
  PLUS whatever is already on record, and only writes anything once every
  check has passed -- so a rejected request leaves no residue at all. See
  `test_a_rejected_request_leaves_no_montage_or_block_row_and_a_correction_
  then_succeeds`.
- **C2 -- `selection["session_datetime"]` is coerced, not assumed to already
  be a `datetime`.** JSON has no datetime type and `docs/schemas/
  job_request.json` declares `selection` as a bare
  `{"type": "object", "additionalProperties": true}` with no `session_
  datetime` property at all, so real wire traffic hands this function a
  plain `str`. `_coerce_session_datetime` accepts a `datetime.datetime` or
  an ISO-8601 string and raises `ValueError` for anything else -- see that
  function's own docstring for the `AttributeError` this replaces.
- **I1 -- the whole payload is normalised, not one key.** The tzinfo-drop
  finding (see `_coerce_session_datetime`'s neighbour, the payload
  construction near the bottom of `accept()`) is not scoped to `selection.
  session_datetime`: any aware `datetime` anywhere in the stored payload
  reproduces the identical defect on a retry. `request.model_dump(mode=
  "json")` -- this project's own existing convention in `contracts/
  manifest.py`, `contracts/done.py` and `synth/peripherals.py`, the only one
  of four `model_dump()` call sites that had omitted it -- serialises every
  nested datetime to a deterministic ISO-8601 string before it is ever
  packed into the blob column, so nothing in the payload can carry a live
  datetime object for the codec to round-trip incorrectly in the first
  place. Confirmed directly: `model_dump(mode="json")` on the same aware
  input produces the identical string on every call, and that string
  round-trips through `datajoint.blob.pack`/`unpack` unchanged.
- **I2/I4 -- the columns this function writes are guarded before insert,
  matching `ingest/params.py`'s own stated convention** ("checked here,
  inside the validation step, rather than left for ... insert to discover as
  a raw `pymysql.err.DataError` -- which Task 8's watcher does not
  special-case"). `montage_id`/`block_id` range, `task_type`/`works_block_id`
  length, and `start_s`/`end_s` finiteness are all checked against the real
  column bounds before any insert is attempted; `selection["block_ids"]`
  naming an id with no `Block` anywhere (I4) raises `ValueError` rather than
  being silently excluded from the window check.

**A design gap this task does not fix, recorded on the `Block` write below
where it applies:** `core.Block`'s own comment says its boundaries are
"decoded from event codes and cross-validated against those rows", and the
frozen parent spec section 4.2 says the same -- `start_s`/`end_s` are
specified as wl-preproc's own MEASUREMENT. This function instead writes
wl.works' ASSERTED numbers into that same column, and `skip_duplicates`
makes that permanent. Flagged, not solved, here.
"""

from __future__ import annotations

import datetime
import math

from wl_preproc.contracts.protocol import JobRequest
from wl_preproc.ingest import landing
from wl_preproc.schema import DEFAULT_PREFIX, core
from wl_preproc.schema import request as schema_request

# The two keys `accept()` itself reads out of an arbitrary, wl.works-supplied
# `selection` dict. `subject` is deliberately NOT among them: session identity
# comes from `metadata.subject` (the ELN's own record), never from anything
# named "subject" inside `selection` -- see `accept()`'s own docstring.
_REQUIRED_SELECTION_KEYS = ("session_datetime", "montage_id")

# Column bounds, read directly from wl_preproc/schema/core.py's declared
# types -- neither `montage_id` nor `block_id` declares "unsigned" (unlike
# core.Segment.segment_barcode's "int unsigned"), so both are SIGNED ranges.
# Checked before any insert, matching ingest/params.py's own stated
# convention for paramset_type/PARAMSET_TYPE_MAX_LEN: a value that is a
# syntactically fine Python int/str can still be too big/long for the column
# it is about to be inserted into, and finding that out from a raw
# pymysql.err.DataError leaves Task 8's handler with an exception type its
# documented ValueError/DataJointError contract does not cover -- confirmed
# with each guard removed in turn against a live column: 1406 "Data too
# long" (task_type, works_block_id), 1264 "Out of range value" (block_id),
# and 1265 "Data truncated" (a non-finite float into start_s/end_s -- not
# 1264, corrected here after actually reproducing it rather than assuming
# the same errno as the integer case). None of the three appears in
# DataJoint's MySQL adapter's translated-error list.
_MONTAGE_ID_RANGE = (-128, 127)  # core.Montage.montage_id : tinyint
_BLOCK_ID_RANGE = (-32768, 32767)  # core.Block.block_id : smallint
_TASK_TYPE_MAX_LEN = 32  # core.Block.task_type : varchar(32)
_WORKS_BLOCK_ID_MAX_LEN = 64  # core.Block.works_block_id : varchar(64)
# MySQL's DATETIME floor. Python's own datetime.MINYEAR (1) is far below it,
# and DataJoint's bare `datetime` column type validates nothing on its own.
_DATETIME_MIN_YEAR = 1000


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


def _coerce_session_datetime(value) -> datetime.datetime:
    """A `datetime.datetime`, or an ISO-8601 string, become the value
    `landing.to_naive_utc` accepts. Anything else raises `ValueError`.

    Real wire traffic never carries a `datetime.datetime` at all: JSON has no
    datetime type, `docs/schemas/job_request.json` declares `selection` as a
    bare `{"type": "object", "additionalProperties": true}` with no
    `session_datetime` property (let alone a `format: date-time`), and
    `selection: dict[str, Any]` makes pydantic coerce nothing inside it --
    confirmed directly against the committed schema. So
    `selection["session_datetime"]` arrives as a plain `str` over HTTP, and
    handing that straight to `to_naive_utc` (whose own signature is typed
    `datetime.datetime`) fails with `AttributeError: 'str' object has no
    attribute 'tzinfo'` -- outside this module's documented `ValueError`
    contract, and Task 8's design (a `JobRequest` that validates against
    `JobRequest`'s own schema is, by definition, not "a malformed body")
    would have to map an `AttributeError` it never expected.

    `datetime.fromisoformat` (Python 3.11+, this project's floor) parses a
    "Z" suffix, a numeric UTC offset, or no offset at all -- whatever real
    wl.works traffic sends. A value already naive (no offset in the string)
    is returned as `fromisoformat` parses it and then passed through
    `to_naive_utc` unchanged, exactly as that function's own docstring
    describes for an already-naive input.
    """
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"selection['session_datetime'] {value!r} is not a valid "
                f"ISO-8601 datetime string: {exc}"
            ) from exc
    raise ValueError(
        "selection['session_datetime'] must be a datetime.datetime or an "
        f"ISO-8601 string, got {type(value).__name__}: {value!r}"
    )


def _reject_out_of_range_int(value, *, name: str, bounds: tuple[int, int]) -> None:
    low, high = bounds
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    if not (low <= value <= high):
        raise ValueError(f"{name}={value} is outside the column's range [{low}, {high}]")


def _reject_oversized_str(value, *, name: str, max_len: int) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string, got {value!r}")
    if len(value) > max_len:
        raise ValueError(
            f"{name} is {len(value)} characters, over the {max_len}-character column limit"
        )


def _reject_non_finite(value, *, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{name}={value} is not a finite number")


def _build_montage_rows(session_key: dict, montage_boundaries: list[dict]) -> list[dict]:
    """`Montage` rows this request WOULD write -- validated, not yet
    inserted. See `accept()`'s two-phase structure (review C1)."""
    rows = []
    for boundary in montage_boundaries:
        b_montage_id = boundary["montage_id"]
        _reject_out_of_range_int(
            b_montage_id, name="montage_boundaries[].montage_id", bounds=_MONTAGE_ID_RANGE
        )
        _reject_non_finite(boundary["start_s"], name="montage_boundaries[].start_s")
        _reject_non_finite(boundary["end_s"], name="montage_boundaries[].end_s")
        rows.append(
            {
                **session_key,
                "montage_id": b_montage_id,
                "start_s": boundary["start_s"],
                "end_s": boundary["end_s"],
            }
        )
    return rows


def _build_block_rows(session_key: dict, blocks: list[dict]) -> list[dict]:
    """`Block` rows this request WOULD write -- validated, not yet inserted.
    See `accept()`'s two-phase structure (review C1)."""
    rows = []
    for block in blocks:
        b_block_id = block["block_id"]
        _reject_out_of_range_int(
            b_block_id, name="metadata.blocks[].block_id", bounds=_BLOCK_ID_RANGE
        )
        _reject_oversized_str(
            block["task_type"], name="metadata.blocks[].task_type", max_len=_TASK_TYPE_MAX_LEN
        )
        _reject_non_finite(block["start_s"], name="metadata.blocks[].start_s")
        _reject_non_finite(block["end_s"], name="metadata.blocks[].end_s")
        works_block_id = block.get("works_block_id")
        if works_block_id is not None:
            _reject_oversized_str(
                works_block_id,
                name="metadata.blocks[].works_block_id",
                max_len=_WORKS_BLOCK_ID_MAX_LEN,
            )
        rows.append(
            {
                **session_key,
                "block_id": b_block_id,
                "task_type": block["task_type"],
                "start_s": block["start_s"],
                "end_s": block["end_s"],
                "works_block_id": works_block_id,
            }
        )
    return rows


def _effective_block_rows(
    block_ids: list[int], *, session_key: dict, candidate_blocks: dict[int, dict]
) -> tuple[dict[int, dict], list[int]]:
    """For every id in `block_ids`: the `Block` row already on record, or --
    when none exists yet -- the one this SAME request would insert (still
    unwritten at this point; see `accept()`'s two-phase structure). Returns
    `(found, unknown)` rather than raising, so `accept()` can tell "no Block
    anywhere names this id" (review I4, its own `ValueError`) apart from "this
    Block exists and is outside the window" instead of one case silently
    hiding inside the other.
    """
    existing = {row["block_id"]: row for row in (core.Block & session_key).to_dicts()}
    found: dict[int, dict] = {}
    unknown: list[int] = []
    for block_id in sorted(set(block_ids)):
        row = existing.get(block_id) or candidate_blocks.get(block_id)
        if row is None:
            unknown.append(block_id)
        else:
            found[block_id] = row
    return found, unknown


def _blocks_outside_window(effective: dict[int, dict], montage_row: dict) -> list[str]:
    """Every entry in `effective` whose own `[start_s, end_s)` is not fully
    contained in the montage's. `ActivationBlock`'s own comment: a block the
    montage does not cover in time is a block the sort must not cover
    either, and nothing below the responder checks this (`submit_derivative`
    has no `Montage` to compare against).
    """
    return [
        f"block {block_id} [{row['start_s']}, {row['end_s']})"
        for block_id, row in effective.items()
        if row["start_s"] < montage_row["start_s"] or row["end_s"] > montage_row["end_s"]
    ]


def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
    """A validated `JobRequest` becomes `Montage`/`Block`/`Request`/`Activation`
    rows. Design spec section 6.1. Returns the `Activation` primary key.

    Raises `ValueError` for a request that cannot be honoured: a `selection`
    missing `session_datetime` or `montage_id`; a `metadata.subject` longer
    than `landing.SUBJECT_MAX_LEN`; a `session_datetime` that is neither a
    `datetime.datetime` nor a parseable ISO-8601 string; a `montage_id`,
    `block_id`, `task_type`, `works_block_id`, `start_s` or `end_s` that
    cannot fit the column it would be written to; a `montage_id` with no
    boundary on record and none supplied in this request either; a
    `block_ids` entry naming no `Block` anywhere; or a `block_ids` entry
    naming a block outside its montage's window. All of these are checked
    against BUILT-BUT-NOT-YET-WRITTEN candidate rows before anything is
    actually inserted (review C1): a rejected request leaves no `Montage` or
    `Block` residue behind for a later, corrected request to trip over.

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

    session_datetime = landing.to_naive_utc(_coerce_session_datetime(selection["session_datetime"]))
    if session_datetime.year < _DATETIME_MIN_YEAR:
        raise ValueError(
            f"selection['session_datetime'] year {session_datetime.year} is "
            f"before {_DATETIME_MIN_YEAR}, MySQL DATETIME's floor"
        )

    montage_id = selection["montage_id"]
    _reject_out_of_range_int(montage_id, name="selection['montage_id']", bounds=_MONTAGE_ID_RANGE)

    session_key = {"subject": metadata.subject, "session_datetime": session_datetime}
    montage_key = {**session_key, "montage_id": montage_id}

    # ---- Phase 1: build every candidate row and validate everything
    # against them plus what already exists, before writing anything at all
    # (review C1). ----

    montage_rows = _build_montage_rows(session_key, metadata.montage_boundaries)
    block_rows = _build_block_rows(session_key, metadata.blocks)
    candidate_montages = {row["montage_id"]: row for row in montage_rows}
    candidate_blocks = {row["block_id"]: row for row in block_rows}

    schema_request.activate(prefix=prefix)

    existing_montage_row = (
        (core.Montage & montage_key).fetch1() if (core.Montage & montage_key) else None
    )
    montage_row = existing_montage_row or candidate_montages.get(montage_id)
    if montage_row is None:
        raise ValueError(
            f"no montage {montage_id} is on record for {session_key} and "
            "metadata.montage_boundaries did not supply one either"
        )

    block_ids = selection.get("block_ids") or []

    # Correction 2: accept() owns the montage window (module docstring).
    if block_ids:
        effective, unknown = _effective_block_rows(
            block_ids, session_key=session_key, candidate_blocks=candidate_blocks
        )
        if unknown:
            raise ValueError(
                "selection names block id(s) with no Block on record and "
                f"none supplied in this request either: {unknown}"
            )
        offending = _blocks_outside_window(effective, montage_row)
        if offending:
            raise ValueError(
                f"selection names block(s) outside montage {montage_id}'s window "
                f"[{montage_row['start_s']}, {montage_row['end_s']}): " + "; ".join(offending)
            )

    # ---- Phase 2: every check above passed. Only now does anything get
    # written. ----

    # Step 1 (design spec section 6.1): Montage rows, insert-if-absent.
    if montage_rows:
        core.Montage.insert(montage_rows, skip_duplicates=True)

    # Step 2: Block rows, insert-if-absent, works_block_id set -- the link
    # back to wl.works' own authored row (core.Block's own comment).
    #
    # NOTE -- a design gap this task does not fix, flagged rather than
    # solved: core.Block's own comment says its boundaries are "decoded from
    # event codes and cross-validated against those rows", and the frozen
    # parent spec section 4.2 says the same -- start_s/end_s are specified as
    # wl-preproc's own MEASUREMENT. This writes wl.works' ASSERTED numbers
    # into that same column instead, and skip_duplicates makes that
    # permanent: 1c-4's decoder, when built, will find the slot already
    # occupied by an asserted value rather than a decoded one.
    if block_rows:
        core.Block.insert(block_rows, skip_duplicates=True)

    # The payload stored as evidence ("the request as received", Request's
    # own comment). mode="json" -- this project's own existing convention in
    # contracts/manifest.py, contracts/done.py and synth/peripherals.py --
    # serialises every nested value, including any datetime anywhere in
    # `selection`/`parameters`/`metadata`, to its deterministic JSON form
    # before it is ever packed into the blob column (review I1). Left as
    # plain Python objects, an aware datetime specifically would compare
    # unequal to itself on a genuine retry: DataJoint's blob codec drops a
    # datetime's tzinfo on its very first round trip through the database
    # (confirmed directly -- pack/unpack an aware value and it comes back
    # naive), so a second accept() call for the same idempotency_key would
    # build a fresh, still-aware payload and compare it against the first
    # call's now-naive stored copy -- and Python treats an aware and a naive
    # datetime as unequal under `==`/`!=` (it never raises, it just compares
    # false), so `_reject_key_reuse`'s `stored != given` would report a
    # difference that is not real and refuse an honest retry as key reuse.
    # mode="json" sidesteps this entirely rather than patching around it:
    # confirmed directly that it renders the same aware datetime to the
    # identical ISO-8601 string on every call, and that string round-trips
    # through datajoint.blob.pack/unpack byte-for-byte, because a string
    # carries no tzinfo for the codec to drop in the first place. See
    # test_accept_is_idempotent_on_the_same_key and
    # test_accept_normalises_an_aware_datetime_anywhere_in_the_stored_payload.
    payload = request.model_dump(mode="json")

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
