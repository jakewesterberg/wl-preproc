# wl_preproc/schema/paramset.py
"""Parameter sets, keyed by content hash.

Section 5.3: computed tables are keyed on (…, paramset_idx), so re-running with
new parameters ADDS rows rather than overwriting. Three sortings of one session
with different drift settings coexist permanently with full provenance.

Paramsets are immutable once used. The content hash does **not** enforce that,
and this module's first draft claimed it did — parent spec section 5.3 now
quotes that sentence back as wrong. The hash makes *registration* idempotent
and nothing more: the unique index is on `(paramset_type, param_hash)`, not on
`params`, so a plain `update1` rewrites `params` while leaving `param_hash`
describing the parameters that used to be there. That is worse than a
permitted edit, because every provenance claim keyed on the hash silently
becomes false.

What actually enforces immutability is a refusal on the table itself:
`ParamSet.update1` raises, and `ParamSet.insert` refuses `replace=True` (a
separate dispatch entry, and the more dangerous of the two — `REPLACE INTO`
rewrites `params` and `param_hash` together, leaving nothing inconsistent to
detect). Both are below, with their reasoning. Their disclosed limit: they
hold at the DataJoint layer only. Raw SQL, or a `dj.FreeTable` handle to the
same physical table, bypasses both — enforcing this in the CLI alone would
have left the hole open to every caller that is not the CLI, which is most of
them.
"""

from __future__ import annotations

import hashlib
import json

import datajoint as dj

from wl_preproc.schema import DEFAULT_PREFIX, pipeline

schema = dj.Schema()

# Read directly from ParamSet's own declaration below (`paramset_type :
# varchar(32)`), not assumed -- the same discipline landing.SUBJECT_MAX_LEN
# documents for element-animal's `subject` column. Task 8's watcher imports
# this to reject an over-length paramset_type before ever calling register(),
# the same shape as the subject_unrepresentable check it already runs: a
# value that validates against SessionParams (an unconstrained str) but
# cannot fit the column it is about to be inserted into.
PARAMSET_TYPE_MAX_LEN = 32


class ContentionExhausted(dj.DataJointError):
    """register()'s bounded retry loop exhausted every attempt to allocate a
    fresh paramset_idx under real concurrent registration -- a designed-for,
    transient condition (see register()'s own docstring and
    `_MAX_REGISTER_ATTEMPTS`), not a defect in what was asked to be
    registered.

    A dedicated subclass, not a bare `dj.DataJointError`: `DataJointError` is
    the *root* of DataJoint's entire error tree -- `AccessError`,
    `MissingTableError`, `IntegrityError`, `QuerySyntaxError`,
    `DuplicateError` all derive from it (confirmed against the installed
    2.3.2 package). Task 8's watcher needs to catch exactly the transient,
    self-clearing condition this type names and defer that one session to
    the next scan -- silently retrying forever is the correct response only
    because contention is guaranteed to clear. A `dj.ParamSet` dropped out
    from under a live prefix, a stale connection, a genuine access fault --
    every other member of that tree -- is a permanent condition an identical
    catch would defer identically and silently, forever, which is the
    opposite of correct: see `wl_preproc/ingest/watcher.py`'s
    `Outcome.DEFERRED` docstring for why that distinction matters enough to
    need its own type rather than a message-text match.
    """


def content_hash(params: dict) -> str:
    """A stable hash of a parameter mapping.

    Canonicalised with sorted keys so that `{"a": 1, "b": 2}` and
    `{"b": 2, "a": 1}` are the same paramset — otherwise key order would silently
    create duplicates that differ in nothing that matters.
    """
    payload = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()


@schema
class ParamSet(dj.Manual):
    definition = """
    # One immutable parameter set. Key: (paramset_type, paramset_idx).
    # param_hash carries the uniqueness that makes re-registration idempotent.
    paramset_type : varchar(32)
    paramset_idx  : int
    ---
    param_hash : varchar(32)
    params     : <blob>
    unique index (paramset_type, param_hash)
    """

    def update1(self, row):
        """Refuse every update. DataJoint's base `update1` only requires the
        primary key and otherwise performs a plain SQL UPDATE of whatever
        non-key columns are given — nothing in this table's declaration stops
        it from rewriting `params` while leaving `param_hash` (keyed only on
        `(paramset_type, param_hash)`, not on `params`) untouched, which would
        desynchronise the two and break the invariant `register()` and every
        downstream reader rely on. So immutability is enforced here, in code,
        rather than left to the unique index, which cannot see this case.

        Blanket by design: every non-key column here (`param_hash`, `params`)
        is part of this row's content-addressed identity, so nothing on this
        table is a mutable-metadata exception. Revisit this if that ever
        changes (e.g. a column added later that is genuinely just metadata)."""
        raise dj.DataJointError(
            "ParamSet rows are immutable once registered (spec section 5.3): "
            "call register() with the edited params instead of updating this row."
        )

    def insert(self, rows, replace=False, **kwargs):
        """Refuse `replace=True`. `update1` alone does not close this: DataJoint
        dispatches `insert`/`insert1`/`update1` as three separate entries in
        `datajoint.user_tables.supported_class_attrs`, so blocking one does
        nothing to the others — and `Table.insert1` is exactly
        `self.insert((row,), **kwargs)`, so overriding `insert` here covers
        both call spellings with one guard.

        This bypass is worse than the one `update1` closes: `replace=True`
        compiles to `REPLACE INTO`, which overwrites `params` AND `param_hash`
        together, so the row stays internally consistent — no stale hash, no
        detectable symptom. A `paramset_idx` would simply, silently start
        meaning different parameters than every computed row already produced
        against it. `register()`'s own insert never sets `replace`, so it is
        unaffected by this guard."""
        if replace:
            raise dj.DataJointError(
                "ParamSet rows are immutable once registered (spec section 5.3): "
                "insert(replace=True) would silently overwrite an existing row's "
                "params and param_hash together, leaving nothing to detect it "
                "happened. Call register() with the new params instead."
            )
        super().insert(rows, replace=replace, **kwargs)


# register()'s allocate-then-insert step reads the current max paramset_idx
# and writes max+1 with no lock, so concurrent registration of DIFFERENT
# params under the same paramset_type (spec section 11.3 expects concurrent
# requests) can race for the same index. A bounded retry resolves it -- see
# register()'s docstring -- rather than a lock, which DataJoint's Manual
# tables have no first-class support for anyway.
_MAX_REGISTER_ATTEMPTS = 10


def _insert_new(row: dict) -> None:
    """The single write register() uses to claim a fresh paramset_idx.

    Split out from register() purely so tests can simulate the concurrent-
    registration collision race by making this one call raise a duplicate-key
    error, without reaching into DataJoint's table-class dispatch machinery
    to intercept insert1 directly. See test_paramset.py.
    """
    ParamSet.insert1(row, skip_duplicates=False)


def register(paramset_type: str, params: dict) -> int:
    """Register a paramset and return its index, reusing an identical one.

    This never asks "does this paramset exist?" and then writes. The hash is the
    identity, so an insert of an identical paramset is a duplicate and is
    skipped — the same reasoning wl.works applies to content-addressed rows.

    Index allocation below is a read (the current max) then a write (max+1),
    with no lock between them, so two callers registering DIFFERENT params
    under the same paramset_type can read the same snapshot and compute the
    same paramset_idx. Critically, the insert that claims it must NOT use
    skip_duplicates=True: that would translate the primary-key collision into
    a silent no-op, discarding the loser's row under no index at all, and the
    final re-read below would then fetch1() zero rows and raise unrelated-
    looking `fetch1`-requires-exactly-one-tuple noise instead of a clear
    error. So the collision is left to raise DuplicateError, which is caught:
    if the other writer happened to register the SAME content (the identical-
    params race), that is not contention, just a lost race to write something
    equivalent -- return their index, same as the fast path above. If they
    registered DIFFERENT content, retry the allocation against a fresh
    snapshot, bounded, so genuine contention raises `ContentionExhausted`
    rather than spinning forever.
    """
    digest = content_hash(params)
    existing = ParamSet & {"paramset_type": paramset_type, "param_hash": digest}
    if existing:
        return int(existing.fetch1("paramset_idx"))

    for _ in range(_MAX_REGISTER_ATTEMPTS):
        # to_arrays, not fetch: DataJoint 2.3.2 deprecates bare fetch() (it
        # warns on every call), and this project's suite must stay at zero
        # warnings.
        used = (ParamSet & {"paramset_type": paramset_type}).to_arrays("paramset_idx")
        idx = int(max(used) + 1) if len(used) else 0
        try:
            _insert_new(
                {
                    "paramset_type": paramset_type,
                    "paramset_idx": idx,
                    "param_hash": digest,
                    "params": params,
                }
            )
            break
        except dj.errors.DuplicateError:
            existing = ParamSet & {"paramset_type": paramset_type, "param_hash": digest}
            if existing:
                return int(existing.fetch1("paramset_idx"))
            continue  # someone else's different params claimed `idx` first
    else:
        raise ContentionExhausted(
            f"paramset registration contention: exhausted {_MAX_REGISTER_ATTEMPTS} "
            f"attempts to allocate an index for paramset_type={paramset_type!r}"
        )

    return int(
        (ParamSet & {"paramset_type": paramset_type, "param_hash": digest}).fetch1(
            "paramset_idx"
        )
    )


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind this table to `{prefix}paramset`. Idempotent."""
    pipeline.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}paramset", create_tables=True)
