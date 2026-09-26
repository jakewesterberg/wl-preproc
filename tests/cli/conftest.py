"""Fixtures shared by `tests/cli/test_archive_cli.py` and
`tests/cli/test_reclaim_and_rehydrate.py`."""

from __future__ import annotations

import pytest

from wl_preproc.ingest.watcher import scan_once
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


@pytest.fixture
def landed(tmp_path, dj_conn, prefix):
    """Factory: a real CI_RECIPE-shaped session, landed via real `scan_once`
    under a caller-chosen subject. Returns `(session_dir, key)`.

    `dj_conn`/`prefix` are session-scoped (`tests/conftest.py`) and shared by
    the whole suite, so CI_RECIPE's own fixed `subject="pico"` would collide
    with another test's row under the identical key -- `tests/cli/
    test_report.py`'s own `scanned` fixture documents the same trap and the
    same fix: every caller below names its own subject.
    """

    def _land(subject: str):
        # A root PER SUBJECT, not one shared `tmp_path / "scratch"`:
        # CI_RECIPE's `session_id` ("2027-03-14_01") is constant regardless
        # of subject, so a test landing two sessions (`test_tape_manifest_
        # lists_a_verified_session_and_excludes_an_unverified_one`) under one
        # shared root would have its second `generate_session` call
        # overwrite the first session's directory in place -- found by
        # running this fixture with a shared root: the second landed
        # session's manifest silently replaced the first's on disk, and the
        # `archive` command run against the first session's own `session_dir`
        # then archived the SECOND session's data under the first's path.
        root = tmp_path / f"scratch-{subject}"
        root.mkdir(exist_ok=True)
        recipe = CI_RECIPE.model_copy(update={"subject": subject})
        generate_session(root, recipe)
        session_dir = root / recipe.session_id
        scan_once(root, prefix=prefix)

        from wl_preproc.contracts.manifest import SessionManifest
        from wl_preproc.contracts.paths import MANIFEST_FILENAME
        from wl_preproc.ingest.landing import manifest_session_key

        manifest = SessionManifest.from_yaml(
            (session_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        key = manifest_session_key(manifest)
        return session_dir, key

    return _land
