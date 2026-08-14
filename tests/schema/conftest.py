# tests/schema/conftest.py
"""What every schema test module shares: the test prefix, and an enum parser.

`PREFIX = "t_"` was declared separately in six modules and hardcoded as a bare
`"t_"` literal in a seventh. That is the exact constant this branch already had
to repair by hand once — one schema prefix per process is a standing constraint,
so six copies of it are six chances to drift into a second one. It is a fixture
rather than a module constant because a `conftest.py` is not importable by name
(there are two of them in this suite, both called `conftest`), and a fixture is
the one sharing mechanism pytest actually guarantees.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def prefix() -> str:
    """The one database-name prefix this suite activates.

    It carries its own separator, exactly as `wl_preproc.schema.DEFAULT_PREFIX`
    does — and that is why the missing separator in the production default went
    unseen for the whole of 1c-1: nothing here exercises it. See
    `test_pipeline.py::test_the_default_prefix_builds_the_schema_names_the_spec_names`.
    """
    return "t_"


@pytest.fixture(scope="session")
def enum_values():
    """Parse `enum('a','b')` into `{"a", "b"}`.

    Membership tests against the raw declared string are not the exactness they
    look like: `"full" in "enum('fullx','partial')"` is True, and a stale extra
    value the spec no longer names is invisible to them entirely. Two test
    modules made exactly that claim in their docstrings while checking
    substrings, so the parser lives once, here.
    """

    def parse(declared: str) -> set[str]:
        text = declared.strip()
        assert text.lower().startswith("enum(") and text.endswith(")"), (
            f"not an enum declaration: {declared!r}"
        )
        body = text[len("enum(") : -1]
        return {value.strip().strip("'\"") for value in body.split(",")}

    return parse
