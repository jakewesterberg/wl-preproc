"""What is on disk versus what the manifest declared.

Spec section 7: only PENDING blocks ingest, and it blocks by meaning "not
complete yet" rather than by judgment. ABSENT never blocks — a session with no
eye tracker ingests exactly like one that has it, and the absence surfaces later
as coverage rather than as a refusal here.
"""

from __future__ import annotations

import pytest
from wl_sync.session import SessionId

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SYSTEMS, SessionLayout
from wl_preproc.ingest.discover import SystemState, discover_topology, systems_with_data
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


@pytest.fixture
def session(tmp_path):
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, SessionId.parse(CI_RECIPE.session_id))
    manifest = SessionManifest.from_yaml(layout.manifest_path.read_text())
    return layout, manifest


def test_every_known_system_gets_exactly_one_state(session):
    layout, manifest = session
    topology = discover_topology(layout, manifest)

    assert set(topology) == set(SYSTEMS)


def test_a_generated_session_is_all_present_or_absent(session):
    layout, manifest = session
    topology = discover_topology(layout, manifest)

    for system in CI_RECIPE.systems:
        assert topology[system] is SystemState.PRESENT
    for system in set(SYSTEMS) - set(CI_RECIPE.systems):
        assert topology[system] is SystemState.ABSENT


def test_declared_without_a_marker_is_pending(session):
    layout, manifest = session
    layout.done_marker("spikeglx").unlink()

    assert discover_topology(layout, manifest)["spikeglx"] is SystemState.PENDING


def test_data_on_disk_that_was_never_declared_is_undeclared(session):
    layout, manifest = session
    rhs_dir = layout.system_dir("rhs")
    rhs_dir.mkdir()
    (rhs_dir / "amplifier.dat").write_bytes(b"0" * 16)

    assert discover_topology(layout, manifest)["rhs"] is SystemState.UNDECLARED


def test_an_empty_undeclared_directory_is_absent_not_undeclared(session):
    """An empty directory is not a recording. Treating it as one would create
    an AcquisitionSystem row for a device that produced nothing."""
    layout, manifest = session
    layout.system_dir("rhs").mkdir()

    assert discover_topology(layout, manifest)["rhs"] is SystemState.ABSENT


def test_systems_with_data_includes_undeclared(session):
    """Spec section 8.1: undeclared data is real. Hiding a recording from every
    downstream stage to punish a manifest bug is the wrong trade."""
    layout, manifest = session
    rhs_dir = layout.system_dir("rhs")
    rhs_dir.mkdir()
    (rhs_dir / "amplifier.dat").write_bytes(b"0" * 16)

    assert systems_with_data(discover_topology(layout, manifest)) == sorted(
        [*CI_RECIPE.systems, "rhs"]
    )
