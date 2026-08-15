# tests/schema/conftest.py
"""What every schema test module shares: an enum parser.

The `prefix` fixture used to live here too, declared separately in six modules
and hardcoded as a bare `"t_"` literal in a seventh before it was consolidated
into one fixture in this file. It has since moved up to `tests/conftest.py`:
`tests/ingest/` (Task 6 on) needs it and pytest only resolves fixtures upward,
never sideways between sibling directories, so a fixture that lives only here
resolves for `tests/schema/` and nowhere else. The move is transparent to every
test in this directory — pytest finds it one level up exactly as it found it
here — and having exactly one definition, now unambiguously the one every
directory shares, is what the one-schema-prefix-per-process constraint this
fixture exists to protect actually wants.
"""

from __future__ import annotations

import pytest


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
