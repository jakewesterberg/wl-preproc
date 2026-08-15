# tests/conftest.py
"""A real MySQL for the schema suite, and the one prefix every test activates.

DataJoint needs a database; there is no in-memory backend. testcontainers is
DataJoint's own test dependency, works locally wherever Docker runs, and works
on GitHub Actions' ubuntu-latest, which ships Docker. One container is started
per session and every schema test shares it.
"""

from __future__ import annotations

import datajoint as dj
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
