# tests/conftest.py
"""A real MySQL for the schema suite, and the one prefix every test activates.

DataJoint needs a database; there is no in-memory backend. testcontainers is
DataJoint's own test dependency, works locally wherever Docker runs, and works
on GitHub Actions' ubuntu-latest, which ships Docker. One container is started
per session and every schema test shares it.
"""

from __future__ import annotations

import datajoint as dj
import numpy as np
import pytest
from testcontainers.community.mysql import MySqlContainer

from wl_preproc.schema._compat import apply_datajoint_compat


@pytest.fixture(scope="session")
def dj_conn():
    apply_datajoint_compat()
    with MySqlContainer("mysql:8.0", root_password="simple") as container:
        dj.config["database.host"] = container.get_container_host_ip()
        dj.config["database.port"] = int(container.get_exposed_port(3306))
        dj.config["database.user"] = "root"
        dj.config["database.password"] = "simple"
        dj.config["safemode"] = False
        dj.logger.setLevel("ERROR")
        yield dj.conn()


@pytest.fixture(scope="session")
def prefix() -> str:
    """The one database-name prefix this suite activates.

    Moved here from `tests/schema/conftest.py` (Task 6): that module resolves
    for `tests/schema/` only, and `tests/ingest/` needs this fixture too, so a
    single definition one level up — where pytest resolves fixtures for every
    directory below it — replaces the directory-scoped one rather than adding
    a second copy beside it. One schema prefix per process is a standing
    constraint: each Element/module owns exactly one module-level `dj.Schema`
    object, and DataJoint itself raises if that object is ever asked to
    `.activate()` a second, different name — confirmed directly in
    `tests/schema/test_pipeline.py::test_activation_is_idempotent`'s docstring
    ("dying with 'The schema is already activated for schema u_lab'"). A
    single fixture definition is what keeps that constraint from having more
    than one place to drift.

    It carries its own separator, exactly as `wl_preproc.schema.DEFAULT_PREFIX`
    does — and that is why the missing separator in the production default went
    unseen for the whole of 1c-1: nothing here exercises it. See
    `tests/schema/test_pipeline.py::test_the_default_prefix_builds_the_schema_names_the_spec_names`.
    """
    return "t_"


@pytest.fixture(scope="session")
def table_snapshot():
    """A deterministic, order-independent snapshot of every row and column a
    table currently holds, for an exact before/after equality check.

    Moved here from `tests/cli/test_report.py` (Phase 1c-3, Task 1): Task 5
    needs the identical helper from `tests/responder/`, and a fixture -- not a
    module-level import -- is how it gets to both, for the same reason
    `enum_values` above is a fixture rather than a constant: a `conftest.py`
    is not importable by name, and there are two of them in this suite, both
    called `conftest`. Every consuming test takes this as a parameter and
    calls it, exactly as it would `enum_values`.

    Returns the callable, not a snapshot itself: the whole point is calling it
    twice -- once before and once after the code under test -- so a single
    computed value would be useless to either caller.

    Sorted by primary key alone (stringified for uniform comparability),
    never by a whole row: MySQL gives no ordering guarantee across two
    separate queries with no `ORDER BY`, and every row's own set of primary
    keys is, by definition, unique -- so sorting on it never needs to fall
    back to comparing a later, possibly-unorderable column (`Ingestion.
    topology` is a dict, and `sorted()` comparing two dicts with `<` raises
    TypeError; sorting by primary key alone never reaches that comparison).
    The returned dicts still carry every column, key and non-key alike, so
    comparing two snapshots (via `deep_equal`, below -- not bare `==`, see
    its own docstring) catches a changed VALUE on an existing row (an
    `insert(replace=True)`, which `ingest.quarantine()` uses by design -- see
    `wl_preproc/ingest/landing.py`) exactly as it catches an added or removed
    row.
    """

    def snapshot(table):
        key_fields = table.primary_key
        return sorted(table.to_dicts(), key=lambda row: tuple(str(row[f]) for f in key_fields))

    return snapshot


@pytest.fixture(scope="session")
def deep_equal():
    """`==` that does not choke on a NumPy array anywhere inside a snapshot.

    Moved here from `tests/cli/test_report.py` (Phase 1c-3, Task 1), for the
    same reason as `table_snapshot` above.

    This suite shares one database across every test file (this fixture's own
    `dj_conn`/`prefix`, one prefix per process), and
    `tests/schema/test_guardrails.py`'s own
    `test_every_blob_attribute_round_trips_an_array` deliberately plants a
    real 64x64 `float32` array into `ingest.Quarantine.detail` -- `Quarantine`
    has no foreign key, so that test round-trips it "for real" -- with no
    cleanup afterward, by design (that file's own docstring: "This inserts
    into the REAL tables"). Found by running the report suite alongside
    `tests/schema` and `tests/ingest`: bare `==` on two snapshots containing
    that row raised `ValueError: The truth value of an array with more than
    one element is ambiguous`, not a clean pass or fail -- because
    `numpy.ndarray.__eq__` returns an array of booleans, and Python's dict/
    list equality cannot collapse that into one verdict. Every value the
    report's OWN tables ever store (str, datetime, a dict of strings) never
    hits this path; a different test's fixture, sharing this suite's one
    database, does -- and a snapshot comparison must still work cleanly around
    it rather than assume it will never be there.
    """

    def equal(a, b) -> bool:
        if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
            return isinstance(a, np.ndarray) and isinstance(b, np.ndarray) and np.array_equal(a, b)
        if isinstance(a, dict) and isinstance(b, dict):
            return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
        if isinstance(a, list | tuple) and isinstance(b, list | tuple):
            return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b, strict=True))
        return a == b

    return equal
