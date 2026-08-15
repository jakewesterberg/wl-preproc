"""Session-complete detection: every system the manifest declared has a DONE.

The alarm half matters as much as the trigger half. A rig transfer that dies at
80% leaves a directory indistinguishable from one still working, and without
`is_stalled` a weekend's recording simply never appears with nothing saying so.
"""

from __future__ import annotations

import datetime

import pytest

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.sentinel import (
    MarkerState,
    is_stalled,
    missing_systems,
    read_marker,
    session_complete,
)
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


@pytest.fixture
def session(tmp_path):
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, CI_RECIPE.session_id)
    manifest = SessionManifest.from_yaml(layout.manifest_path.read_text())
    return layout, manifest


def test_a_generated_session_is_complete(session):
    layout, manifest = session
    assert session_complete(layout, manifest) is True
    assert missing_systems(layout, manifest) == []


def test_a_missing_marker_makes_it_incomplete(session):
    layout, manifest = session
    layout.done_marker("spikeglx").unlink()

    assert session_complete(layout, manifest) is False
    assert missing_systems(layout, manifest) == ["spikeglx"]


def test_an_empty_marker_still_counts_as_complete(session):
    """Spec section 5.2: an empty DONE means "complete, no integrity data".
    Completeness and integrity are separate questions and this is where they
    separate."""
    layout, manifest = session
    layout.done_marker("spikeglx").write_text("")

    assert read_marker(layout, "spikeglx")[0] is MarkerState.EMPTY
    assert session_complete(layout, manifest) is True


def test_an_undeclared_system_never_affects_completeness(session):
    """A system on disk that the manifest never promised cannot hold up a
    session — nothing was waiting for it."""
    layout, manifest = session
    rhs_dir = layout.system_dir("rhs")
    rhs_dir.mkdir()
    (rhs_dir / "stray.dat").write_bytes(b"0")

    assert "rhs" not in manifest.expected_systems
    assert session_complete(layout, manifest) is True


def test_a_corrupt_marker_is_invalid_rather_than_absent(session):
    """These must not collapse: absent means the transfer has not finished,
    invalid means it finished and wrote something wrong. They are different
    problems with different reports."""
    layout, manifest = session
    layout.done_marker("spikeglx").write_text("{{{ not yaml")

    assert read_marker(layout, "spikeglx")[0] is MarkerState.INVALID
    assert session_complete(layout, manifest) is False


def test_stalled_only_when_incomplete_and_quiet(session):
    layout, manifest = session
    layout.done_marker("spikeglx").unlink()
    quiet_since = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=3)

    assert is_stalled(layout, manifest, now=quiet_since) is True


def test_a_complete_session_is_never_stalled_however_old(session):
    layout, manifest = session
    ancient = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=400)

    assert is_stalled(layout, manifest, now=ancient) is False


def test_a_recently_touched_incomplete_session_is_not_stalled(session):
    """Still transferring is not stalled. The threshold is what separates
    them, and getting it wrong must only ever change a report."""
    layout, manifest = session
    layout.done_marker("spikeglx").unlink()
    just_now = datetime.datetime.now(datetime.UTC)

    assert is_stalled(layout, manifest, now=just_now) is False
