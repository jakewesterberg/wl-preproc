"""Shared fixtures for `tests/responder/`."""

from __future__ import annotations

import pytest

from wl_preproc.ingest.watcher import scan_once
from wl_preproc.schema import ingest
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


@pytest.fixture
def scanned(tmp_path, dj_conn, prefix):
    """Factory: land a CI_RECIPE-shaped session under a caller-chosen subject.

    Copied from `tests/cli/test_report.py`'s fixture of the same name and
    shape, for the identical reason: `dj_conn`/`prefix` are session-scoped
    (`tests/conftest.py`) and shared by the whole suite, so a single
    fixed subject baked into this fixture would let one test in this file
    see a session an earlier test already landed under it -- moving the
    exact fragility this exists to remove from "shared with
    tests/schema/test_core.py" to "shared with the next test in this file"
    is not a fix. Every caller below states its own subject.
    """

    def _land(subject: str):
        ingest.activate(prefix=prefix)
        root = tmp_path / "scratch"
        root.mkdir()
        generate_session(root, CI_RECIPE.model_copy(update={"subject": subject}))
        scan_once(root, prefix=prefix)
        return root, prefix

    return _land
