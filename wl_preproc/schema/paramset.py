# wl_preproc/schema/paramset.py
"""Parameter sets, keyed by content hash.

Section 5.3: computed tables are keyed on (…, paramset_idx), so re-running with
new parameters ADDS rows rather than overwriting. Three sortings of one session
with different drift settings coexist permanently with full provenance.

Paramsets are immutable once used, and the content hash enforces it
structurally: an edit yields a different hash, which is a different paramset.
"""

from __future__ import annotations

import hashlib
import json

import datajoint as dj

from wl_preproc.schema import pipeline

schema = dj.Schema()


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
        rather than left to the unique index, which cannot see this case."""
        raise dj.DataJointError(
            "ParamSet rows are immutable once registered (spec section 5.3): "
            "call register() with the edited params instead of updating this row."
        )


def register(paramset_type: str, params: dict) -> int:
    """Register a paramset and return its index, reusing an identical one.

    This never asks "does this paramset exist?" and then writes. The hash is the
    identity, so an insert of an identical paramset is a duplicate and is
    skipped — the same reasoning wl.works applies to content-addressed rows.
    """
    digest = content_hash(params)
    existing = ParamSet & {"paramset_type": paramset_type, "param_hash": digest}
    if existing:
        return int(existing.fetch1("paramset_idx"))

    # to_arrays, not fetch: DataJoint 2.3.2 deprecates bare fetch() (it warns on
    # every call), and this project's suite must stay at zero warnings.
    used = (ParamSet & {"paramset_type": paramset_type}).to_arrays("paramset_idx")
    idx = int(max(used) + 1) if len(used) else 0
    ParamSet.insert1(
        {
            "paramset_type": paramset_type,
            "paramset_idx": idx,
            "param_hash": digest,
            "params": params,
        },
        skip_duplicates=True,
    )
    return int(
        (ParamSet & {"paramset_type": paramset_type, "param_hash": digest}).fetch1(
            "paramset_idx"
        )
    )


def activate(prefix: str = "wlpp") -> None:
    """Bind this table to `{prefix}paramset`. Idempotent."""
    pipeline.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}paramset", create_tables=True)
