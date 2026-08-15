"""What is on disk versus what the manifest declared.

Spec section 7: only PENDING blocks ingest, and it blocks by meaning "not
complete yet" rather than by judgment. ABSENT never blocks — a session with no
eye tracker ingests exactly like one that has it, and the absence surfaces later
as coverage rather than as a refusal here.
"""

from __future__ import annotations

from pathlib import Path

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


def test_top_level_content_survives_a_failed_recursive_walk(session, monkeypatch):
    """`_has_content`'s recursive walk can raise before yielding anything --
    on Python 3.11 (this project's floor) the `scandir` it opens on a
    subdirectory it has already discovered is guarded only against
    PermissionError, unlike `Path.is_file()`. Losing that race must not
    turn a real recording into ABSENT: a file sitting directly in the
    directory has to survive even when the walk that would have found it
    dies first. `systems_with_data` is checked too, since that is the
    concrete consequence -- a dropped AcquisitionSystem row, not just a
    wrong enum value."""
    layout, manifest = session
    rhs_dir = layout.system_dir("rhs")
    rhs_dir.mkdir()
    (rhs_dir / "amplifier.dat").write_bytes(b"0" * 16)

    real_rglob = Path.rglob

    def failing_rglob(self, pattern):
        if self == rhs_dir:
            raise FileNotFoundError(f"simulated: {self} unreadable mid-walk")
        yield from real_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", failing_rglob)

    topology = discover_topology(layout, manifest)

    assert topology["rhs"] is SystemState.UNDECLARED
    assert "rhs" in systems_with_data(topology)


def test_a_failed_recursive_walk_over_a_truly_empty_directory_is_still_absent(
    session, monkeypatch
):
    """The fallback that recovers top-level content (previous test) must not
    turn every failed walk into a false UNDECLARED: an undeclared directory
    that genuinely holds nothing has to read as ABSENT even when the walk
    that would have confirmed that also raises."""
    layout, manifest = session
    rhs_dir = layout.system_dir("rhs")
    rhs_dir.mkdir()

    real_rglob = Path.rglob

    def failing_rglob(self, pattern):
        if self == rhs_dir:
            raise FileNotFoundError(f"simulated: {self} unreadable mid-walk")
        yield from real_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", failing_rglob)

    assert discover_topology(layout, manifest)["rhs"] is SystemState.ABSENT


def test_a_vanished_directory_does_not_crash_the_whole_scan(session, monkeypatch):
    """The `is_dir()` check and the two read attempts inside `_has_content`
    are not atomic, so `directory` itself -- not merely something under it
    -- can be gone by the time both `rglob()` and the non-recursive
    `iterdir()` fallback run, and both raise. Unguarded, that second failure
    would propagate out of `_has_content` into `discover_topology`, which
    has no try/except around this call: one system's bad timing would crash
    the scan for every system, not just misclassify the one that raced. The
    dict coming back complete for every system -- not the `ABSENT` value
    alone -- is the assertion that actually matters here, since the whole
    scan's availability is what was at risk."""
    layout, manifest = session
    rhs_dir = layout.system_dir("rhs")
    rhs_dir.mkdir()
    (rhs_dir / "amplifier.dat").write_bytes(b"0" * 16)

    real_rglob = Path.rglob
    real_iterdir = Path.iterdir

    def failing_rglob(self, pattern):
        if self == rhs_dir:
            raise FileNotFoundError(f"simulated: {self} removed before rglob")
        yield from real_rglob(self, pattern)

    def failing_iterdir(self):
        if self == rhs_dir:
            raise FileNotFoundError(f"simulated: {self} removed before iterdir")
        yield from real_iterdir(self)

    monkeypatch.setattr(Path, "rglob", failing_rglob)
    monkeypatch.setattr(Path, "iterdir", failing_iterdir)

    topology = discover_topology(layout, manifest)

    assert set(topology) == set(SYSTEMS)
    assert topology["rhs"] is SystemState.ABSENT


def test_a_permission_error_on_is_dir_does_not_crash_the_whole_scan(session, monkeypatch):
    """Round-3 finding: `is_dir()` swallows ENOENT/ENOTDIR/EBADF/ELOOP on its
    own but not EACCES, so a bare permission error checking `directory`
    still raises -- and unlike the `rglob`/`iterdir` exposures, this one is
    not Python-3.11-specific; it reproduces identically on 3.13, the version
    the preprocessing server actually runs. Left unguarded it would crash
    `discover_topology` for every system, not just the one that raced. As in
    the previous test, the dict coming back complete for every system is the
    assertion that matters -- reaching it at all is most of the proof."""
    layout, manifest = session
    rhs_dir = layout.system_dir("rhs")
    rhs_dir.mkdir()
    (rhs_dir / "amplifier.dat").write_bytes(b"0" * 16)

    real_is_dir = Path.is_dir

    def failing_is_dir(self):
        if self == rhs_dir:
            raise PermissionError(f"simulated: permission denied for {self}")
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", failing_is_dir)

    topology = discover_topology(layout, manifest)

    assert set(topology) == set(SYSTEMS)
    assert topology["rhs"] is SystemState.ABSENT
