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

import datajoint as dj
import numpy as np
import pytest

SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[2] / "wl_preproc"


@pytest.fixture(scope="module")
def all_tables(dj_conn, prefix):
    from wl_preproc.schema import core, coverage, paramset, pipeline, request

    pipeline.activate(prefix=prefix)
    core.activate(prefix=prefix)
    coverage.activate(prefix=prefix)
    paramset.activate(prefix=prefix)
    request.activate(prefix=prefix)

    tables = []
    for module in (core, coverage, paramset, request):
        for name in dir(module):
            obj = getattr(module, name)
            if hasattr(obj, "heading") and hasattr(obj, "definition"):
                tables.append((module.__name__, name, obj))
    return tables


# `pipeline.activate()` binds five Element modules, not just this project's
# own four (see `wl_preproc/schema/pipeline.py`). All five are swept below --
# not only `event`/`trial`, where the known offenders happen to live -- for
# the same reason the sweep must not be scoped to `core, coverage, paramset,
# request` alone: narrowing to the modules already known to be guilty is
# exactly the blind spot this fix exists to close.
_ELEMENT_MODULE_NAMES = ("lab", "subject", "session", "event", "trial")


def _iter_tables_recursive(module_name, table):
    """`table`, then every `dj.Part` nested inside it, at whatever depth
    DataJoint allows.

    `dir(module)` cannot reach a Part table -- only `dir()` on its master can
    -- which is exactly what let element_event's bare `longblob` Part
    attributes go unseen by a sweep scoped to `dir(module)` alone. Part
    classes are identified structurally (`issubclass(obj, dj.Part)`), not by
    name or `hasattr` duck-typing, so this cannot mistake an unrelated nested
    attribute for a table.

    Only DUNDERS are skipped, not every underscore-prefixed name. This skipped
    anything starting with `_` until 2026-08-14, which meant a `dj.Part`
    reachable from its master only under a single-underscore name -- an alias,
    a rename, a compatibility shim -- was invisible to the bare-longblob sweep.
    That is the same blind spot the recursion itself exists to close, one level
    down, and it is pinned by
    `test_the_part_sweep_reaches_a_part_table_bound_to_an_underscore_name`.

    That test's docstring also records the correction to this one: an Element
    *declaring* `class _Internal(dj.Part)` is NOT the case at issue, because
    DataJoint never declares such a class in the first place
    (`datajoint/schemas.py:261` requires an uppercase initial), so it has no
    heading and this walker would raise on it rather than sweep it.
    """
    yield module_name, table.__qualname__, table
    for name in dir(table):
        if name.startswith("__") and name.endswith("__"):
            continue
        obj = getattr(table, name)
        if isinstance(obj, type) and issubclass(obj, dj.Part):
            yield from _iter_tables_recursive(module_name, obj)


def test_the_part_sweep_reaches_a_part_table_bound_to_an_underscore_name(dj_conn, prefix):
    """The guard the `_iter_tables_recursive` fix did not have.

    That walker skipped every `_`-prefixed name until 2026-08-14, so a `dj.Part`
    reachable only under a name starting with a single underscore was invisible
    to the bare-longblob sweep. Reverting the fix left the whole suite green --
    no Element exposes one today -- so the fix was correct and completely
    unexercised, which is the same manufactured confidence the allow-list
    assertion above exists to prevent, one level down.

    **The probe is built by rebinding, not by declaring `class _Internal`, and
    that is a finding rather than a convenience.** DataJoint's schema decorator
    only treats a nested class as a Part if its name starts with an UPPERCASE
    letter (`datajoint/schemas.py:261`, `if part[0].isupper()`). A nested class
    literally named `_Internal` is therefore never declared at all: `_master`
    stays None, `table_name` is None, and touching `.heading` raises "Table
    `_Internal` is not properly configured" -- verified live on 2.3.2. So the
    scenario named in `_iter_tables_recursive`'s docstring, an Element
    *declaring* an underscore-named Part, cannot occur, and if one ever did
    appear the walker would raise on it rather than sweep it. What CAN occur,
    and is what this pins, is a properly declared Part reachable from its
    master under an underscore name: an alias, a rename, a compatibility shim.
    `Public` is the control -- without it, a walker that had stopped finding
    Part tables at all would be indistinguishable from one that skips
    underscores.
    """
    schema = dj.Schema(f"{prefix}pw")

    @schema
    class SweepProbe(dj.Manual):
        definition = """
        # master for the Part-table sweep probe
        n : int
        """

        class Public(dj.Part):
            definition = """
            # a conventionally named part, the control
            -> master
            i : int
            """

        class Internal(dj.Part):
            definition = """
            # declared normally, then reachable only under an underscore name
            -> master
            j : int
            """

    try:
        part = SweepProbe.Internal
        SweepProbe._Internal = part
        del SweepProbe.Internal

        found = {table for _, _, table in _iter_tables_recursive("probe", SweepProbe)}
        assert SweepProbe.Public in found, "the walker no longer finds Part tables at all"
        assert part in found, (
            "a Part table reachable only under a `_`-prefixed name was not "
            "reached -- only DUNDERS may be skipped, or its attributes go "
            "unswept by the bare-longblob guard above"
        )
    finally:
        # In `finally` for the reason test_daemon.py gives: a bare call here
        # leaks the probe schema into the shared, session-scoped container for
        # the rest of the run if an assertion above raises first.
        schema.drop()


def test_the_delete_previews_parent_graph_matches_the_real_foreign_keys(all_tables):
    """`wl_preproc/cli/deleting.py` hand-declares each table's parents and says
    they "must be kept in sync" with the `->` lines in the schema modules. Its
    own guard compared only the *key set*, which is structurally blind to a
    wrong value — and one was already sitting there: the foreign key added to
    `Activation` on 2026-08-14 made `Request` its second parent while the map
    still said `("Montage",)`.

    Called from here because the guard's parent half needs
    `connection.dependencies`, and so cannot run in `wlpp delete`'s
    no-database case (`tests/test_cli_guardrails.py` runs it as a bare
    subprocess). `all_tables` has the schemas activated, so this is the place
    where it does run, against DataJoint's own dependency graph rather than
    against a restatement of the declaration.
    """
    from wl_preproc.cli.deleting import _assert_known_tables_are_real

    _assert_known_tables_are_real()


@pytest.fixture(scope="module")
def all_tables_including_elements(all_tables):
    """Every table `all_tables` finds, plus every table -- and nested `dj.Part`
    table -- in the five Element modules `pipeline.activate()` actually binds.

    Used only by the declaration test. The round-trip and key-documentation
    tests deliberately keep the narrower `all_tables` scope: round-tripping an
    upstream Part table would mean building live parent chains through
    Session/Trial/Event that are out of scope here, and key-documentation
    compliance for tables this project does not author is a different
    question than the one that test asks.
    """
    from wl_preproc.schema import pipeline

    tables = []
    for module_name, table_name, table in all_tables:
        tables.extend(_iter_tables_recursive(module_name, table))
    for mod_name in _ELEMENT_MODULE_NAMES:
        module = getattr(pipeline, mod_name)
        for name in dir(module):
            obj = getattr(module, name)
            if hasattr(obj, "heading") and hasattr(obj, "definition"):
                tables.extend(_iter_tables_recursive(module.__name__, obj))
    return tables


# Spec section 5.1.1 (amended 2026-08-13): this is not four unlucky columns,
# it is an Elements idiom -- every `*.Attribute` part table in the pinned
# release declares `attribute_blob` as a bare `longblob`. element_lab and
# element_animal have zero occurrences not because they are safer, but
# because neither declares a `.Attribute` part table at all. Nothing in this
# pipeline writes to any of the four today, which is why this is a hazard
# rather than an incident -- but anything that later writes an array to one
# destroys it silently, so per-session/per-block/per-trial/per-event array
# attributes must go in a custom table declaring `<blob>`, never in these.
#
# Allow-listed by fully-qualified name, never by module: a NEW bare longblob
# anywhere this sweep reaches -- in element_event, element_session, or
# anywhere else -- must still trip this test. A fifth entry, if one is ever
# found, is very likely the same `*.Attribute` idiom in a newly-adopted
# Element -- check that pattern first. Do not add to this set without a
# corresponding spec amendment; see test_no_table_declares_a_bare_longblob's
# second assertion, which requires every entry here to actually be found by
# the sweep.
_KNOWN_UPSTREAM_BARE_LONGBLOBS = frozenset(
    {
        "element_event.event.Event.Attribute.attribute_blob",
        "element_event.trial.Block.Attribute.attribute_blob",
        "element_event.trial.Trial.Attribute.attribute_blob",
        "element_session.session_with_datetime.Session.Attribute.attribute_blob",
    }
)


def test_no_table_declares_a_bare_longblob(all_tables_including_elements):
    offenders = []
    seen_known = set()
    for module_name, table_name, table in all_tables_including_elements:
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
            # fire, for anything. (Pinned: tests/schema/test_harness.py.)
            if "blob" not in declared or getattr(attr, "codec", None) is not None:
                continue
            qualified = f"{module_name}.{table_name}.{attr_name}"
            if qualified in _KNOWN_UPSTREAM_BARE_LONGBLOBS:
                seen_known.add(qualified)
                continue
            # `attr.type` is the PHYSICAL db type, which is not always what the
            # author wrote: a core-type alias like `bytes` also resolves to
            # `longblob` (confirmed against datajoint/declare.py before
            # dispatch). `original_type` preserves the as-written spelling when
            # it differs, so the message never accuses an author of typing a
            # keyword they didn't.
            original = getattr(attr, "original_type", None)
            shown = f"{original} (resolves to {declared})" if original else declared
            offenders.append(f"{qualified} -> {shown}")
    assert not offenders, (
        "bare longblob attributes found; under DataJoint 2.x these silently "
        "destroy array data. Declare <blob> instead:\n  " + "\n  ".join(offenders)
    )
    # The allow-list is only honest if the sweep actually reaches every name in
    # it. Otherwise an entry could sit there excusing an attribute the sweep
    # has silently stopped seeing -- the same "manufactured confidence" failure
    # mode this fix exists to close, just moved one level up. A name that is
    # allow-listed but never encountered means either the sweep regressed, or
    # (best case) element_event fixed it upstream and the entry is stale --
    # either way it must be investigated, not left in place.
    missing = _KNOWN_UPSTREAM_BARE_LONGBLOBS - seen_known
    assert not missing, (
        "allow-listed as known upstream bare longblobs but never encountered by "
        f"the sweep: {sorted(missing)} -- the sweep no longer reaches them, or "
        "they no longer exist and the allow-list is stale"
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

    Every OTHER blob attribute (`is_blob`, not just `exclude`) is skipped too,
    not synthesized: neither table currently has a second one, but if one
    existed, no branch below knows how to fabricate array-shaped content for
    it, and it should fail on the resulting missing-field insert error rather
    than the more confusing "unhandled type for synthetic value: ... blob".
    """
    row = {}
    for name in table.heading.names:
        if name in table.primary_key or name == exclude:
            continue
        attr = table.heading[name]
        if attr.is_blob or attr.nullable or attr.default is not None:
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
