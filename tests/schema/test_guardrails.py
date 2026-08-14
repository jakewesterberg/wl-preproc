# tests/schema/test_guardrails.py
"""Guardrails that section 10 states as rules, enforced as tests.

The blob rule is the one that matters most: under DataJoint 2.x a bare
`longblob` declares a raw binary column, an inserted numpy array is stored as
its string repr — elided by numpy above ~1000 elements — and nothing raises on
insert or on fetch. Measured: 31,488 float32 values stored as 488 bytes,
unrecoverable. A declaration test cannot see this. Only a round-trip can.
"""

from __future__ import annotations

import datetime
import pathlib

import numpy as np
import pytest

PREFIX = "t_"

SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[2] / "wl_preproc"


@pytest.fixture(scope="module")
def all_tables(dj_conn):
    from wl_preproc.schema import core, coverage, paramset, pipeline, request

    pipeline.activate(prefix=PREFIX)
    core.activate(prefix=PREFIX)
    coverage.activate(prefix=PREFIX)
    paramset.activate(prefix=PREFIX)
    request.activate(prefix=PREFIX)

    tables = []
    for module in (core, coverage, paramset, request):
        for name in dir(module):
            obj = getattr(module, name)
            if hasattr(obj, "heading") and hasattr(obj, "definition"):
                tables.append((module.__name__, name, obj))
    return tables


def test_no_table_declares_a_bare_longblob(all_tables):
    offenders = []
    for module_name, table_name, table in all_tables:
        for attr_name in table.heading.names:
            attr = table.heading[attr_name]
            declared = (attr.type or "").lower()
            # `attr.is_blob` is NOT the safe/unsafe signal it looks like: DataJoint
            # sets it for any physical MySQL blob-family column, so it is True for
            # both `<blob>` and a bare `longblob` alike -- confirmed against the
            # live heading before dispatch. `attr.codec` is what actually differs:
            # non-None (a BlobCodec) only when the `<blob>` codec is attached, and
            # None for a raw `longblob`, which is what silently returns bytes
            # instead of an array. A condition keyed on `is_blob` here would never
            # fire, for anything.
            if "blob" in declared and getattr(attr, "codec", None) is None:
                offenders.append(f"{module_name}.{table_name}.{attr_name} -> {declared}")
    assert not offenders, (
        "bare longblob attributes found; under DataJoint 2.x these silently "
        "destroy array data. Declare <blob> instead:\n  " + "\n  ".join(offenders)
    )


def _synthetic_key(table) -> dict:
    """A primary key of the right shape, for a table with no foreign keys."""
    key = {}
    for name in table.primary_key:
        declared = (table.heading[name].type or "").lower()
        if "char" in declared:
            key[name] = f"blobprobe-{name}"[:32]
        elif "int" in declared:
            key[name] = 99
        else:  # pragma: no cover - a new key type should fail loudly, not silently
            raise AssertionError(f"unhandled key type for {name}: {declared}")
    return key


def _synthetic_required_secondary(table, exclude: str) -> dict:
    """Every attribute `insert1` requires beyond the primary key.

    `_synthetic_key` alone is not enough to insert a row into either real
    blob-bearing table: ParamSet also requires `param_hash` (no default) and
    Request also requires `task_type`, `origin`, and `requested_at` (no
    default) -- confirmed against the live heading, not assumed. `exclude` is
    the blob attribute itself, which the caller supplies separately as the
    probe array. Attributes that are nullable or carry a default (like
    Request's `requested_by`) are correctly left for the database to fill in.
    """
    row = {}
    for name in table.heading.names:
        if name in table.primary_key or name == exclude:
            continue
        attr = table.heading[name]
        if attr.nullable or attr.default is not None:
            continue
        declared = (attr.type or "").lower()
        if declared.startswith("enum("):
            row[name] = declared.split("(", 1)[1].split(",", 1)[0].strip().strip("'")
        elif "char" in declared:
            row[name] = f"blobprobe-{name}"[:32]
        elif "datetime" in declared or "timestamp" in declared:
            row[name] = datetime.datetime(2026, 1, 1)
        elif "date" in declared:
            row[name] = datetime.date(2026, 1, 1)
        elif "int" in declared:
            row[name] = 99
        elif "float" in declared or "double" in declared or "decimal" in declared:
            row[name] = 0.0
        else:  # pragma: no cover - a new secondary type should fail loudly, not silently
            raise AssertionError(f"unhandled type for synthetic value: {name}: {declared}")
    return row


def test_every_blob_attribute_round_trips_an_array(all_tables, dj_conn):
    """The test whose absence upstream is currently paying for.

    This inserts into the REAL tables. An earlier draft built a stand-in table
    with its own `<blob>` and round-tripped through that once per discovered
    attribute — which only proves `<blob>` works in general, something
    `test_harness.py` already establishes, and says nothing about the attributes
    actually declared here. Corrected 2026-08-13 before dispatch.
    """
    blob_attrs = [
        (module_name, table_name, table, attr)
        for module_name, table_name, table in all_tables
        for attr in table.heading.names
        if getattr(table.heading[attr], "is_blob", False)
    ]
    assert blob_attrs, "no blob attributes found — this test would pass vacuously"

    arr = np.arange(4096, dtype=np.float32).reshape(64, 64)
    exercised = []
    for module_name, table_name, table, attr in blob_attrs:
        assert not table.parents(), (
            f"{module_name}.{table_name} has a foreign key, so a synthetic row "
            "cannot be inserted without building its parents first. Extend this "
            "test to construct them — do not skip the table, or the attribute "
            "goes unverified and this guard stops guarding."
        )
        key = _synthetic_key(table)
        row = {**key, **_synthetic_required_secondary(table, exclude=attr), attr: arr}
        table.insert1(row, skip_duplicates=True)
        got = (table & key).fetch1(attr)
        assert isinstance(got, np.ndarray), f"{table_name}.{attr} returned {type(got).__name__}"
        assert got.shape == arr.shape and got.dtype == arr.dtype
        assert np.array_equal(got, arr)
        exercised.append(f"{table_name}.{attr}")

    assert exercised, "no blob attribute was actually round-tripped"


def test_no_bare_delete_call_anywhere_in_the_source():
    """Section 10: cascading deletes reach further than expected, so no bare
    .delete() exists in this codebase. wlpp delete prints the cascade and
    defaults to a dry run instead."""
    offenders = []
    for path in SOURCE_ROOT.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if ".delete()" in stripped and "delete_quick" not in stripped:
                rel = path.relative_to(SOURCE_ROOT.parent)
                offenders.append(f"{rel}:{lineno}: {stripped[:70]}")
    assert not offenders, (
        "bare .delete() found; section 10 forbids it because cascades reach "
        "further than expected:\n  " + "\n  ".join(offenders)
    )


def test_every_table_documents_its_key_in_schema(all_tables):
    """Section 10: primary key changes require drop-and-repopulate, so the keys
    are documented where they are declared rather than in a separate file that
    drifts."""
    undocumented = [
        f"{m}.{t}"
        for m, t, table in all_tables
        if not table.definition.strip().startswith("#")
    ]
    assert not undocumented, f"tables with no in-schema comment: {undocumented}"
