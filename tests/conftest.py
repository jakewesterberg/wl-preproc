# tests/conftest.py
"""A real MySQL for the schema suite.

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
