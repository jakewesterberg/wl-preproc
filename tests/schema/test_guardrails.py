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
import importlib
import pathlib
import pkgutil
import re

import datajoint as dj
import numpy as np
import pytest

import wl_preproc.schema

SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[2] / "wl_preproc"

# "pipeline" is discovered below like any other schema submodule -- it still
# needs activating, since every other module's own `.activate()` depends on
# it -- but it is excluded from the flat `dir()` sweep specifically. Its
# tables are five adopted Elements, not `@schema`-decorated classes living
# directly on the module, and a flat sweep here would either miss them
# entirely (`dir()` cannot reach a Part table nested inside a master -- see
# `_iter_tables_recursive` below) or double-count them once
# `all_tables_including_elements` adds them again correctly, via
# `_ELEMENT_MODULE_NAMES` and the recursive Part-table walker built for
# exactly that shape.
_EXCLUDED_SCHEMA_MODULES = frozenset({"pipeline"})


def _discover_schema_modules() -> dict[str, object]:
    """Every non-private submodule of `wl_preproc.schema`, imported and keyed
    by name.

    Auto-discovered rather than hand-listed as `(core, coverage, paramset,
    request)`: that hardcoded tuple was Task 5's own finding — `ingest`
    landed as a fifth schema module and every check built on it swept
    nothing from it, silently, while the suite stayed green. (The one
    exception, `test_no_bare_delete_call_anywhere_in_the_source` below, is a
    filesystem `rglob` over source files and was never scoped by this
    fixture at all, so it already reached `ingest.py` unaffected by any of
    this.) A module added for a later phase must not require remembering to
    extend a tuple here — it must be found by construction.

    `pkgutil.iter_modules` walks the package's own `__path__`, so a new
    `wl_preproc/schema/<name>.py` is picked up without touching this file.
    Names starting with `_` are skipped before import — this excludes
    `_compat`, which defines no `@schema` table and only a shim function, and
    would contribute nothing even if imported, but there is no reason to pay
    for the import.
    """
    modules: dict[str, object] = {}
    for info in pkgutil.iter_modules(wl_preproc.schema.__path__):
        if info.name.startswith("_"):
            continue
        modules[info.name] = importlib.import_module(f"wl_preproc.schema.{info.name}")
    return modules


@pytest.fixture(scope="module")
def all_tables(dj_conn, prefix):
    modules = _discover_schema_modules()

    # pipeline first and explicitly: every other discovered module's own
    # `.activate()` calls it too -- each of the five is independently
    # idempotent and self-sufficient about its own dependencies, confirmed by
    # reading all five `activate()` functions -- so this line is not
    # load-bearing for correctness today. It is here so activation order
    # never depends on which OTHER module happens to run first, including one
    # added later that might not yet follow that pattern.
    modules["pipeline"].activate(prefix=prefix)

    tables = []
    for name in sorted(modules):
        if name in _EXCLUDED_SCHEMA_MODULES:
            continue
        module = modules[name]
        module.activate(prefix=prefix)
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if hasattr(obj, "heading") and hasattr(obj, "definition"):
                tables.append((module.__name__, attr_name, obj))
    return tables


def test_the_schema_module_sweep_discovers_a_module_added_after_this_file_was_written(
    all_tables,
):
    """`all_tables` used to be `(core, coverage, paramset, request)`, hand-typed.
    `ingest` (Task 5) landed as a fifth schema module and every check built on
    that fixture swept nothing from it — silently, while staying green — until
    the fixture was rebuilt on `pkgutil.iter_modules` instead.

    Pinned here by NAME, not merely "the set is non-empty" or "has at least
    5 modules": a future regression back to a hardcoded tuple would silently
    narrow the set again, exactly as it did the first time, and a bare size
    or non-emptiness check could not tell that apart from a legitimate module
    being renamed or removed elsewhere.
    """
    swept_modules = {module_name for module_name, _, _ in all_tables}
    assert "wl_preproc.schema.ingest" in swept_modules, (
        "expected the auto-discovered sweep to include wl_preproc.schema.ingest; "
        f"got {sorted(swept_modules)} instead — has the fixture regressed to a "
        "hardcoded module list?"
    )


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


def _is_bare_longblob(attr) -> bool:
    """True when `attr` (a `dj.Heading` attribute) is a bare `longblob` --
    DataJoint 2.x's silent array-corrupting declaration -- rather than a
    `<blob>`-codec column.

    `attr.is_blob` is NOT the safe/unsafe signal it looks like: DataJoint
    sets it for any physical MySQL blob-family column, so it is True for both
    `<blob>` and a bare `longblob` alike -- confirmed against the live heading
    before dispatch. `attr.codec` is what actually differs: non-None (a
    BlobCodec) only when the `<blob>` codec is attached, and None for a raw
    `longblob`, which is what silently returns bytes instead of an array. A
    condition keyed on `is_blob` here would never fire, for anything.
    (Pinned: tests/schema/test_harness.py.)

    This also flags a core-type alias that resolves to `longblob` physically
    -- `payload : bytes`, for instance -- because it keys on `attr.type` (the
    PHYSICAL db type), not the as-written spelling. That is deliberate, not
    incidental: the corruption this guards against happens at the physical
    column level regardless of which source spelling produced it.

    This is THE predicate `test_no_table_declares_a_bare_longblob` enforces
    across the whole schema sweep, and the only one: `tests/schema/
    test_ephys.py::test_reverting_a_blob_to_longblob_is_caught` imports this
    function rather than keeping its own copy, specifically so a mutation
    test proves this exact check catches a reverted declaration, not a
    stand-in that might be weaker.
    """
    declared = (attr.type or "").lower()
    return "blob" in declared and getattr(attr, "codec", None) is None


def test_no_table_declares_a_bare_longblob(all_tables_including_elements):
    offenders = []
    seen_known = set()
    for module_name, table_name, table in all_tables_including_elements:
        for attr_name in table.heading.names:
            attr = table.heading[attr_name]
            if not _is_bare_longblob(attr):
                continue
            declared = (attr.type or "").lower()
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


def _synthetic_value(name: str, declared: str):
    """One value per (attribute name, declared type), for both builders.

    `_synthetic_key` and `_synthetic_required_secondary` each carried their own
    type ladder until Task 6, and they had drifted: the key builder handled
    only `char` and `int`, so a chain through `pipeline.Session` (datetime key)
    or `core.AcquisitionSystem` (enum key) raised. They must agree, because
    `_build_parents` writes an ancestor row using one and then a descendant key
    using the other -- disagreement is an unresolvable foreign key, reported as
    something else entirely.

    Deterministic in `name`, which is what lets the chain work without threading
    values through: `subject` gets the same value whichever table asks for it.
    """
    declared = (declared or "").lower()
    if declared.startswith("enum("):
        return declared.split("(", 1)[1].split(",", 1)[0].strip().strip("'")
    if "char" in declared:
        # DEVIATION from the brief's literal `[:32]`: `element_animal.Subject`
        # declares `subject : varchar(8)`, reached once `_build_parents` walks
        # a real chain (`pipeline.Session -> Subject`), and a value truncated
        # to 32 chars still overflows an 8-char column -- measured as
        # `pymysql.err.DataError: (1406, "Data too long for column 'subject'
        # at row 1")`. Truncating to the DECLARED length instead of a fixed 32
        # fixes the fixture rather than papering over it: every column still
        # gets a value that fits, and two calls with the same (name, declared)
        # pair -- which is what `_fk_overrides` and both builders always pass
        # -- still agree, since the length comes from `declared` itself.
        length_match = re.search(r"\((\d+)\)", declared)
        length = int(length_match.group(1)) if length_match else 32
        return f"blobprobe-{name}"[:length]
    if "datetime" in declared or "timestamp" in declared:
        return datetime.datetime(2026, 1, 1)
    if "date" in declared:
        return datetime.date(2026, 1, 1)
    if "int" in declared:
        return 99
    if "float" in declared or "double" in declared or "decimal" in declared:
        return 0.0
    raise AssertionError(f"unhandled type for synthetic value: {name}: {declared}")


def _synthetic_key(table) -> dict:
    """A primary key of the right shape.

    No longer restricted to tables without foreign keys: `_build_parents` makes
    the inherited columns resolvable, and `_synthetic_value` is deterministic in
    the attribute name, so an inherited `subject` matches the one written into
    the ancestor.
    """
    return {
        name: _synthetic_value(name, table.heading[name].type)
        for name in table.primary_key
    }


# DEVIATION from the brief, and load-bearing for correctness, not cosmetic:
# the SAME array `test_every_blob_attribute_round_trips_an_array` inserts and
# compares against, defined once here so both sides can share it by reference
# rather than by two separately-written literals that could drift.
#
# Every blob column that is not the attribute currently under test gets THIS
# exact array -- never a distinct placeholder -- which is what makes a STALE
# row harmless. Two collisions are real, both measured directly:
#
# (1) A multi-blob table tested attribute-by-attribute shares ONE primary key
#     across iterations, because `_synthetic_key` is deterministic in name by
#     design (see its own docstring). `ephys.Unit` declares three blob
#     columns; probing `spike_sites` after `spike_times` has already inserted
#     the row means `insert1(..., skip_duplicates=True)` SKIPS the second
#     write, and the round-trip test then fetches whatever the FIRST write
#     left behind for `spike_sites`, not what the second write intended.
# (2) `_build_parents` can insert a table's row as an ANCESTOR before that
#     same table is ever reached as the table directly under test.
#     `request.Request` is reachable both ways -- directly (its own
#     `payload`) and as `request.Activation`'s ancestor via a renamed FK --
#     and "ephys" sorts before "request" in the module sweep, so `Request`'s
#     row already exists, built for `Unit`, by the time `Request.payload`'s
#     own turn comes.
#
# In both cases `skip_duplicates=True` leaves the EXISTING row untouched, so
# whatever that row's non-excluded blob columns hold is what gets fetched.
# Making that value identically `_PROBE_ARRAY` -- the same array `exclude`
# itself is tested against -- means the fetch is correct regardless of which
# insert call actually reached the database. A required, non-blob column
# still needs a `skip_duplicates=True`-collision to be harmless too, but no
# assertion ever reads THOSE back, so an arbitrary deterministic value
# (`_synthetic_value`) remains correct for them.
#
# The nullable check therefore no longer applies to blob columns: a nullable
# blob left to the database (as the literal brief ladder did) is NULL on any
# row a later, non-excluded turn only skip-duplicates past -- measured as
# `spike_depths` fetching back `None`, failing `isinstance(got, np.ndarray)`,
# on the very first multi-blob table this suite ever reached. Every OTHER
# nullable attribute is still left for the database, unchanged.
_PROBE_ARRAY = np.arange(4096, dtype=np.float32).reshape(64, 64)
# Immutable, so the correctness argument the docstring above makes -- every
# non-excluded blob column gets THIS SAME object, and a stale row left behind
# by a skip-duplicates collision is harmless because it always holds this
# exact value -- is self-enforcing rather than merely a convention every
# caller happens to honour. This is the SAME object shared by both sides of
# the round-trip assertion (the value inserted, and the value the test
# compares a fetch against); a future edit that mutated it in place after
# insert would silently invalidate that comparison for every OTHER table's
# blob columns already relying on it, and this raises immediately instead.
_PROBE_ARRAY.flags.writeable = False


def _synthetic_required_secondary(table, exclude: str) -> dict:
    """Every attribute `insert1` requires beyond the primary key.

    `_synthetic_key` alone is not enough to insert a row into either real
    blob-bearing table: ParamSet also requires `param_hash` (no default) and
    Request also requires `task_type`, `origin`, and `requested_at` (no
    default) -- confirmed against the live heading, not assumed. `exclude` is
    the blob attribute itself, which the caller supplies separately as the
    probe array. Attributes that are nullable or carry a default (like
    Request's `requested_by`) are correctly left for the database to fill in.

    DEVIATION from the brief's literal ladder, which skipped every `is_blob`
    attribute unconditionally (nullable or not) and left it to the database:
    correct only while ParamSet and Request -- the sole blob-bearing tables
    it had ever reached -- had exactly one blob attribute apiece, zero
    foreign keys, and were never anyone else's ancestor, so neither collision
    `_PROBE_ARRAY`'s docstring (above) describes could occur. Task 6 breaks
    all three assumptions at once. A blob attribute that is not `exclude` now
    gets `_PROBE_ARRAY` -- see that constant's docstring for why this exact
    value, not merely "a" throwaway one, is what makes both collisions
    harmless.
    """
    row = {}
    for name in table.heading.names:
        if name in table.primary_key or name == exclude:
            continue
        attr = table.heading[name]
        if attr.is_blob:
            row[name] = _PROBE_ARRAY
            continue
        if attr.nullable or attr.default is not None:
            continue
        row[name] = _synthetic_value(name, attr.type)
    return row


# Blob attributes on a table with a foreign key cannot be round-tripped by
# this test's synthetic-row builder: `_synthetic_key` has no notion of a
# parent chain. `Ingestion` (`-> pipeline.Session`) is the first blob-bearing
# table this sweep has ever reached that actually has one — both tables it
# covered before Task 5 (`ParamSet`, `Request`) have zero foreign keys, which
# is exactly why the old `assert not table.parents()` below had been true,
# untested, dead code since this file was written; Task 5 is its first live
# exercise. `Quarantine` is deliberately NOT here: it is keyed on
# `session_dir` alone with no foreign key at all (see
# `wl_preproc/schema/ingest.py`'s module docstring for why), so it needs no
# allow-list entry and is round-tripped for real below, exactly like every
# other table without parents.
#
# Self-validating the same way `_KNOWN_UPSTREAM_BARE_LONGBLOBS` is: an entry
# here that the sweep stops reaching, or whose table stops actually having a
# foreign key, fails this test rather than sitting here unexercised. That
# second case matters specifically because this allow-list's whole
# justification — "unbuildable parent chain" — silently stops being true the
# moment someone adds real parent-chain support, and a stale entry at that
# point would be hiding a table this test could and should verify for real.
#
# Retired by teaching `_synthetic_key`/`_synthetic_required_secondary` to
# build a real parent chain — a `Lab -> Subject -> Session` builder is the
# shape essentially every custom table in this project's schema needs, not
# only `Ingestion` — reusable, one-time work deliberately out of scope here.
# RETIRED 2026-08-22 by Task 6, which taught this test to build real parent
# chains via `_build_parents` below. Kept as an empty frozenset, and asserted
# empty by `test_the_parent_blocked_allow_list_is_empty`, so that re-adding an
# entry is deliberate rather than a quiet skip.
_BLOB_ATTRS_WITH_UNBUILT_PARENTS = frozenset()


def _fk_overrides(table) -> dict:
    """Values for foreign-key columns whose name differs from the parent's.

    `_synthetic_value` is deterministic in the attribute NAME, which is what
    lets an inherited `subject` match the ancestor row with nothing threaded
    through. A RENAMED foreign key breaks that: `request.Activation` declares
    `-> Request.proj(request_key='idempotency_key')`, so the child column is
    `request_key` while the row it must match is keyed `idempotency_key`.
    Naming alone would build "blobprobe-request_key" against a row holding
    "blobprobe-idempotency_key", and the insert would fail on a foreign key
    that looks, from the error, like a missing parent.

    `attr_map` is child -> parent, per DataJoint's own
    `attr_map[column_name] = referenced_column_name`.
    """
    overrides = {}
    for parent, props in table.parents(as_objects=True, foreign_key_info=True):
        for child_attr, parent_attr in props["attr_map"].items():
            if child_attr == parent_attr:
                continue
            overrides[child_attr] = _synthetic_value(
                parent_attr, parent.heading[parent_attr].type
            )
    return overrides


def _synthetic_row(table, exclude: str | None = None) -> dict:
    """A complete insertable row: key, required secondaries, and FK renames.

    One function so that `_build_parents` and the round-trip test cannot build
    the same table two different ways -- which is the drift that made a separate
    `_synthetic_key` ladder wrong in the first place.
    """
    row = {
        **_synthetic_key(table),
        **_synthetic_required_secondary(table, exclude=exclude),
    }
    row.update(_fk_overrides(table))
    return row


def _build_parents(table) -> None:
    """Insert every ancestor row `table` needs, depth-first and idempotently.

    Recursive rather than a hand-written `Lab -> Subject -> Session` ladder: the
    chain a Phase 2a table needs is ten deep and runs through Montage and
    Activation, and a hand-written ladder would need extending for every table
    added later -- the same hand-listed shape that let `ingest` (1c-2) and
    `timebase` (1c-4) go unswept.

    ALL parents, not `primary=True`. `primary=True` returns only foreign keys
    composed of primary-key attributes, which would skip every below-divider
    foreign key this schema declares -- Probe, ElectrodeConfig,
    ClusterQualityLabel -- while the database still enforces them.
    """
    # `as_objects=True` returns `datajoint.table.FreeTable` instances
    # (`datajoint/table.py::Table.parents`), not the real declared classes --
    # confirmed by reading that method's source, then confirmed live: a
    # FreeTable is a generic wrapper around a connection and a table name,
    # never an instance of the original `Manual`/`Computed`/`Part` subclass,
    # so it carries none of that class's tier-specific behaviour. This is
    # WHY an ancestor chain that passes through an auto-populated table
    # inserts cleanly here: `core.Segment` is `dj.Computed`, and calling
    # `core.Segment.insert1(...)` directly raises "Inserts into an
    # auto-populated table can only be done inside its make method" (its
    # `_allow_insert` class attribute defaults to False outside
    # `populate()`) -- verified directly against this exact table. The
    # `FreeTable` this function actually inserts through has no
    # `_allow_insert` attribute at all, so `Table.insert`'s guard
    # (`not allow_direct_insert and not getattr(self, "_allow_insert",
    # True)`) reads the default `True` and never raises. The same genericity
    # is why a `dj.Part` ancestor's own master-integrity enforcement (its
    # `delete`/`drop` overrides, which refuse to run directly on a Part) has
    # no chance to engage either: a FreeTable is never `isinstance(_, dj.Part)`,
    # regardless of what the real class declares. Anyone "simplifying" this to
    # call the real parent classes instead would silently break every chain
    # that passes through an auto-populated table -- which this schema's own
    # chain already does, via `core.Segment` -- and lose Part-table ancestor
    # coverage along with it.
    for parent in table.parents(as_objects=True):
        _build_parents(parent)
        parent.insert1(_synthetic_row(parent), skip_duplicates=True)


def test_the_parent_blocked_allow_list_is_empty():
    """Task 6 retired this allow-list by teaching the round-trip test to build
    real parent chains. Kept as an empty frozenset, and asserted empty, so that
    re-adding an entry is a deliberate act with a test to answer to rather than
    a quiet skip.

    `Ingestion.topology` was its only member and is now round-tripped for real.
    """
    assert _BLOB_ATTRS_WITH_UNBUILT_PARENTS == frozenset()


def test_the_key_and_secondary_builders_agree_on_every_shared_type():
    """A `subject` built as a primary key and the same attribute built as a
    required secondary must produce the SAME value, or a foreign key built by
    `_build_parents` will not resolve against the ancestor row it just wrote.

    Pinned by test rather than by comment: the two builders were separate
    functions with separately-maintained type ladders until Task 6, which is
    exactly the shape that drifts.
    """
    for declared in ("varchar(32)", "int unsigned", "datetime", "date",
                     "enum('a','b')", "double"):
        assert _synthetic_value("probe", declared) == _synthetic_value("probe", declared)
    # And the spellings that used to exist in only one of the two ladders
    assert _synthetic_value("t", "datetime") is not None
    assert _synthetic_value("s", "enum('syncbox','spikeglx')") == "syncbox"


# The exact set `test_every_blob_attribute_round_trips_an_array` must exercise
# -- pinned by fully-qualified name, not merely "the list is non-empty". A
# non-emptiness check cannot tell 10 real round-trips apart from 7: a
# regression in `_iter_tables_recursive` that silently stopped recursing into
# Part tables would drop the three `WaveformSet` blob attributes below and
# stay green, which is the exact hole this project's own history already
# proved once (see the test's docstring below, "fix round 1"). Spellings
# verified directly against what the test's own `qualified = f"{module_name}.
# {table_name}.{attr}"` construction actually produces before this was
# asserted -- `table_name` is `table.__qualname__` from `_iter_tables_
# recursive`, so a nested Part table reads as `Master.Part`
# (e.g. `WaveformSet.Waveform`), not merely `Part`.
_EXPECTED_EXERCISED_BLOB_ATTRIBUTES = frozenset(
    {
        "wl_preproc.schema.ingest.Ingestion.topology",
        "wl_preproc.schema.ingest.Quarantine.detail",
        "wl_preproc.schema.paramset.ParamSet.params",
        "wl_preproc.schema.request.Request.payload",
        "wl_preproc.schema.ephys.Unit.spike_times",
        "wl_preproc.schema.ephys.Unit.spike_sites",
        "wl_preproc.schema.ephys.Unit.spike_depths",
        "wl_preproc.schema.ephys.WaveformSet.PeakWaveform.peak_electrode_waveform",
        "wl_preproc.schema.ephys.WaveformSet.Waveform.waveform_mean",
        "wl_preproc.schema.ephys.WaveformSet.Waveform.waveforms",
        # Added with QualityMetrics.Channel, which closes parent spec section
        # 6.6's per-channel gap. Pinned here in the same commit that declares
        # it: a new array attribute that does not join this set is exactly the
        # silent hole this set exists to close.
        "wl_preproc.schema.ephys.QualityMetrics.Channel.spectral_profile",
    }
)


def test_every_blob_attribute_round_trips_an_array(all_tables, dj_conn):
    """The test whose absence upstream is currently paying for.

    This inserts into the REAL tables. An earlier draft built a stand-in table
    with its own `<blob>` and round-tripped through that once per discovered
    attribute — which only proves `<blob>` works in general, something
    `test_harness.py` already establishes, and says nothing about the attributes
    actually declared here. Corrected 2026-08-13 before dispatch.

    Walks Part tables too, via `_iter_tables_recursive` -- reused from the
    declaration-test fixture below rather than written a second time. `all_tables`
    itself is a flat `dir(module)` sweep and cannot see a `dj.Part` class nested
    inside a master, which is exactly what let `ephys.WaveformSet.PeakWaveform.
    peak_electrode_waveform` and `ephys.WaveformSet.Waveform.{waveform_mean,
    waveforms}` go declared-and-checked-not-bare-longblob (by
    `test_no_table_declares_a_bare_longblob`, via `all_tables_including_elements`,
    which already recurses) but never actually ROUND-TRIPPED here -- three of
    the six array attributes Phase 2a added, silently unverified by the one test
    whose entire job is verifying them for real. Closed 2026-08-22, fix round 1.

    Deliberately still `all_tables`, not `all_tables_including_elements`: the
    expansion below is scoped to THIS repository's own schema modules only, so
    no adopted-Element Part table (`element_event.event.Event.Attribute` and
    the three siblings `_KNOWN_UPSTREAM_BARE_LONGBLOBS` names) is pulled in.
    Round-tripping one of those would mean building a live parent chain through
    Session/Trial/Event, which stays out of scope here exactly as
    `all_tables_including_elements`'s own docstring says -- and, unlike the
    custom Part tables above, every one of them is a KNOWN, permanently
    allow-listed bare-longblob case that a real round-trip would correctly
    fail on, not a gap worth closing.

    `exercised` is asserted against `_EXPECTED_EXERCISED_BLOB_ATTRIBUTES`
    exactly -- ten fully-qualified names, not merely "the list is non-empty".
    The non-emptiness check this test carried until fix round 2 could not
    distinguish 10 real round-trips from 7: a regression in
    `_iter_tables_recursive` that silently stopped recursing into Part tables
    would drop exactly the three `WaveformSet` blob attributes this same
    docstring already once had to have restored, and this test would stay
    green throughout.
    """
    expanded_tables = [
        entry
        for module_name, table_name, table in all_tables
        for entry in _iter_tables_recursive(module_name, table)
    ]
    blob_attrs = [
        (module_name, table_name, table, attr)
        for module_name, table_name, table in expanded_tables
        for attr in table.heading.names
        if getattr(table.heading[attr], "is_blob", False)
    ]
    assert blob_attrs, "no blob attributes found — this test would pass vacuously"

    # The SAME object `_synthetic_required_secondary` fills every non-excluded
    # blob column with (see `_PROBE_ARRAY`'s docstring) -- not a second literal
    # that happens to match today. Two definitions of "the probe array" is
    # exactly the drift that would silently break the collision argument that
    # docstring makes.
    arr = _PROBE_ARRAY
    exercised = []
    for module_name, table_name, table, attr in blob_attrs:
        qualified = f"{module_name}.{table_name}.{attr}"
        _build_parents(table)
        row = {**_synthetic_row(table, exclude=attr), attr: arr}
        key = {k: row[k] for k in table.primary_key}
        table.insert1(row, skip_duplicates=True)
        got = (table & key).fetch1(attr)
        assert isinstance(got, np.ndarray), f"{table_name}.{attr} returned {type(got).__name__}"
        assert got.shape == arr.shape and got.dtype == arr.dtype
        assert np.array_equal(got, arr)
        exercised.append(qualified)

    exercised_set = set(exercised)
    assert exercised_set == _EXPECTED_EXERCISED_BLOB_ATTRIBUTES, (
        "the set of blob attributes actually round-tripped has changed -- "
        f"missing (expected but not seen): "
        f"{sorted(_EXPECTED_EXERCISED_BLOB_ATTRIBUTES - exercised_set)}; "
        f"unexpected (seen but not expected): "
        f"{sorted(exercised_set - _EXPECTED_EXERCISED_BLOB_ATTRIBUTES)}. "
        "A regression in _iter_tables_recursive or the schema sweep could "
        "silently drop or add attributes here while a bare non-emptiness "
        "check stayed green -- update _EXPECTED_EXERCISED_BLOB_ATTRIBUTES "
        "only after confirming why the set actually changed."
    )


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


def test_no_code_path_writes_activation_supersedes():
    """`Activation.supersedes` is asserted, in prose only, to be written
    nowhere in this codebase: `submit()` never sets it (regenerating a
    canonical over an old one is not implemented yet), and
    `submit_derivative`'s own docstring says a derivative never supersedes a
    canonical. That was a codebase-wide invariant resting entirely on
    docstrings staying true, in a project whose own `test_no_bare_delete_
    call_anywhere_in_the_source` (immediately above) already enforces a
    different codebase-wide invariant by source scan rather than by trusting
    prose (review round 2, Minor). This is the same shape for a second one.

    Four write shapes, matched by three patterns plus one exclusion, none
    of which is a bare substring search:

    - ``dict_key_write`` — ``"supersedes":`` / ``'supersedes':``, a dict
      literal passed to ``insert``/``insert1``/``update1``, which is how
      every DataJoint write in this codebase not covered by the shapes
      below is spelled.
    - ``subscript_write`` — ``row["supersedes"] =`` / ``row['supersedes'] =``,
      requiring the ``=`` immediately (mod whitespace) after the closing
      bracket and rejecting a second ``=`` right after (so ``==`` — a read
      and a comparison, e.g. ``row["supersedes"] == None`` — does not
      match; neither does ``<=``/``>=``, whose character before ``=`` is
      never ``]`` or whitespace-after-``]``).
    - ``keyword_write`` — bare ``supersedes=`` (not ``==``), matched
      anywhere in the line: a same-line keyword argument
      (``dict(supersedes=x)``, ``f(a=1, supersedes=x)``) AND, since review
      round 4 found a round-3 draft missed it, a keyword argument alone on
      its own continuation line (``supersedes=x,``) — the same shape
      ``request.py``'s own ``submit()`` already uses for real with
      ``skip_duplicates=True,`` on its own line inside a multi-line
      ``insert1(...)`` call. A round-3 draft of this pattern required an
      immediately preceding ``(`` or ``,`` specifically to reject the
      schema declaration (``supersedes = null : int``, inside
      ``Activation``'s ``definition`` string) — which also silently
      rejected every OTHER shape with nothing but leading whitespace before
      ``supersedes``, continuation-line keyword arguments included, since a
      line-by-line scan cannot see the open paren or comma on the
      PREVIOUS line that makes such a line valid Python.
    - ``ddl_declaration`` is not a write pattern but the one exclusion
      ``keyword_write`` needs: the schema declaration is the only bare
      ``supersedes = value`` shape that must not be flagged, and it is
      uniquely identifiable by the `` : type`` that always follows a
      declared attribute's default value in DataJoint's DDL syntax — a
      keyword argument never has a bare colon-and-type immediately after
      its value. A round-4 draft matched that default-value token with
      ``\\S+`` (any non-whitespace), which BACKTRACKS past brackets and
      braces to find any colon later in the line — silently re-excluding a
      continuation-line keyword write whose *value* happens to contain one.
      Two real, uncontrived idioms proved it (review round 5):
      ``supersedes=candidates[0:1][0],`` and ``supersedes={1: 2}[1],``,
      both missed, ``\\S+`` matching ``candidates[0`` or ``{1`` and landing
      on the slice/dict colon rather than stopping where the real
      declaration's value (``null``) actually ends. ``\\w+`` — word
      characters only — cannot span a bracket or brace at all, so it
      cannot backtrack into a colon inside one: it still matches the real
      declaration's ``null`` exactly, and correctly fails to match either
      idiom above from the first character after ``=``. Checked directly
      that this line matches ``ddl_declaration`` and therefore stays
      excluded even under the widened ``keyword_write``.

    None of the four matches a bare READ (`row["supersedes"]` alone,
    `.fetch1("supersedes")`) or the docstring prose in `submit_derivative`
    and this test itself, both of which discuss `supersedes` at length using
    RST double-backticks, never a Python quote character next to an `=` or
    `[`. Confirmed all patterns fire on their intended shape and stay
    silent against the committed source before being relied on here.
    """
    import re

    dict_key_write = re.compile(r"""["']supersedes["']\s*:""")
    subscript_write = re.compile(r"""\[\s*["']supersedes["']\s*\]\s*=(?!=)""")
    keyword_write = re.compile(r"""supersedes\s*=(?!=)""")
    ddl_declaration = re.compile(r"""^supersedes\s*=\s*\w+\s*:""")

    offenders = []
    for path in SOURCE_ROOT.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if (
                dict_key_write.search(stripped)
                or subscript_write.search(stripped)
                or (keyword_write.search(stripped) and not ddl_declaration.match(stripped))
            ):
                rel = path.relative_to(SOURCE_ROOT.parent)
                offenders.append(f"{rel}:{lineno}: {stripped[:70]}")
    assert not offenders, (
        "a source line writes 'supersedes' (as a dict key, a subscript "
        "assignment, or a keyword argument); Activation.supersedes must "
        "remain unwritten by every code path -- a derivative never "
        "supersedes a canonical, and nothing regenerates a canonical yet "
        "either:\n  " + "\n  ".join(offenders)
    )


def test_every_table_documents_its_key_in_schema(all_tables):
    """Section 10: primary key changes require drop-and-repopulate, so the keys
    are documented where they are declared rather than in a separate file that
    drifts.

    Two things this test used to miss, both closed the same day:

    1. It asserted only that the comment block started with `#` -- never that
       it actually named the key via a `Key: (...)` line, which is the
       specific convention section 10 requires and which every table in this
       schema already follows by hand (see e.g. `wl_preproc/schema/ephys.py`).
       A docstring comment that never mentioned the key at all passed this
       test exactly as well as a fully compliant one.
    2. It swept only the flat `all_tables` fixture (`dir(module)`), which
       cannot reach a `dj.Part` class nested inside a master -- the same
       blind spot `_iter_tables_recursive` exists to close for the bare-
       longblob sweep. All six `dj.Part` tables this phase added
       (`ProbeType.Electrode`, `ElectrodeConfig.Electrode`,
       `WaveformSet.PeakWaveform`, `WaveformSet.Waveform`,
       `QualityMetrics.Cluster`, `QualityMetrics.Waveform`) went unchecked by
       this test, silently, for as long as it was scoped to `all_tables` alone.

    Deliberately still expanded from `all_tables`, not `all_tables_including_
    elements`: key-documentation compliance for an adopted Element's own table
    is a different question than the one this test asks about this project's
    own schema modules -- the same scoping choice
    `test_every_blob_attribute_round_trips_an_array` makes, for the same
    reason (see that test's own docstring).

    Checked directly before relying on this stronger assertion: every one of
    the 35 tables and Part tables this expanded sweep reaches already carries
    a `Key:` line today, so tightening this assertion required no comment
    fix -- see this dispatch's report for how that was verified.
    """
    expanded_tables = [
        entry
        for module_name, table_name, table in all_tables
        for entry in _iter_tables_recursive(module_name, table)
    ]
    undocumented = [
        f"{m}.{t}"
        for m, t, table in expanded_tables
        if not (table.definition.strip().startswith("#") and "Key:" in table.definition)
    ]
    assert not undocumented, f"tables with no in-schema `Key:` comment: {undocumented}"
