"""Session-complete detection: every system the manifest declared has a DONE.

The alarm half matters as much as the trigger half. A rig transfer that dies at
80% leaves a directory indistinguishable from one still working, and without
`is_stalled` a weekend's recording simply never appears with nothing saying so.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.sentinel import (
    MarkerState,
    is_stalled,
    last_change_at,
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


def test_a_valid_marker_parses_with_its_contents_intact(session):
    """Nothing before this checked what a *successful* parse returns:
    session_complete and missing_systems only ever ask whether a marker is
    ABSENT or INVALID, so PARSED and EMPTY are interchangeable to both of
    them. This is the one place PARSED itself, and the DoneMarker it carries,
    are checked directly — against a marker generate_session actually wrote,
    not a hand-built one."""
    layout, manifest = session

    state, marker = read_marker(layout, "spikeglx")

    assert state is MarkerState.PARSED
    assert marker is not None
    assert marker.system == "spikeglx"
    assert marker.files, "the generator must have hashed at least one real file"
    system_dir = layout.system_dir("spikeglx")
    for entry in marker.files:
        assert (system_dir / entry.path).stat().st_size == entry.bytes


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


def test_a_marker_with_invalid_utf8_bytes_is_invalid_rather_than_crashing(session):
    """A process killed mid-write, or disk corruption, can leave bytes that
    are not valid UTF-8 at all — a different failure than bytes that decode
    fine but parse as bad YAML, which is all the corrupt-marker test above
    exercises. Both are "the transfer finished and wrote something wrong":
    read_marker must land on INVALID for either, and must never let the
    decode error itself escape."""
    layout, manifest = session
    layout.done_marker("spikeglx").write_bytes(b"schema_version: 1\nsystem: spikeglx\xff\x80\xfe")

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


def test_last_change_at_skips_a_dangling_symlink(session):
    """`Path.stat()` follows symlinks, so a dangling one raises
    FileNotFoundError — the same failure a transfer's own
    write-to-temp-then-rename produces if it lands mid-walk. last_change_at
    runs only on incomplete sessions, directories that are by definition
    still being written to, so this is the ordinary case rather than an edge
    one: one vanished entry must not crash the scan for every session after
    it."""
    layout, manifest = session
    (layout.dir / "dangling").symlink_to(layout.dir / "does-not-exist")

    result = last_change_at(layout.dir)

    assert result.tzinfo is datetime.UTC


def test_a_permission_error_checking_the_marker_reads_as_absent(session, monkeypatch):
    """Round-3 finding: `exists()` shares `is_dir()`'s ENOENT/ENOTDIR/EBADF/
    ELOOP swallowing but not EACCES, so a bare permission error checking for
    the marker still raises. This call is reached unconditionally, for
    every system, before completeness is even decided, so left unguarded it
    would crash session_complete/missing_systems outright rather than
    report one system as missing. ABSENT is the same answer a genuinely
    missing marker produces, so the downstream consequences -- the session
    reading incomplete, this system named as missing -- are checked too,
    not just the state tag alone."""
    layout, manifest = session
    marker_path = layout.done_marker("spikeglx")
    real_exists = Path.exists

    def failing_exists(self):
        if self == marker_path:
            raise PermissionError(f"simulated: permission denied for {self}")
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", failing_exists)

    assert read_marker(layout, "spikeglx")[0] is MarkerState.ABSENT
    assert session_complete(layout, manifest) is False
    assert missing_systems(layout, manifest) == ["spikeglx"]


def test_last_change_at_survives_a_failed_stat_on_the_directory_itself(session, monkeypatch):
    """Round-3 finding, narrower than the rglob gap below: the very first
    line -- session_dir.stat() -- was unguarded too. `Path.stat()` is a bare
    `os.stat()` with no swallowing of anything, on any Python version, so
    any OSError there propagated straight out. Unlike the rglob fallback
    below, there is no already-known real timestamp to prefer here -- the
    directory itself is what could not be read -- so the fallback is "just
    touched": chosen so a transient failure to observe this directory does
    not manufacture a false stall report from data this function never
    actually saw."""
    layout, manifest = session
    layout.done_marker("spikeglx").unlink()
    real_stat = Path.stat

    def failing_stat(self, *, follow_symlinks=True):
        if self == layout.dir:
            raise PermissionError(f"simulated: permission denied for {self}")
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", failing_stat)

    before = datetime.datetime.now(datetime.UTC)
    result = last_change_at(layout.dir)
    after = datetime.datetime.now(datetime.UTC)

    assert result.tzinfo is datetime.UTC
    assert before <= result <= after, "fallback must be 'just touched', not an arbitrary timestamp"
    assert is_stalled(layout, manifest, now=after) is False


def test_last_change_at_falling_walk_keeps_a_genuine_stall_visible(session, monkeypatch):
    """Round-3 finding: the rglob walk itself -- not just candidate.stat()
    inside it -- can raise, the same Python-3.11-only gap
    discover._has_content documents in full. Its failure mode here is the
    opposite of _has_content's: an artificially fresh fallback would make a
    genuinely stalled session look merely active and delay the report that
    should fire, rather than drop a database row. Backdating the
    directory's own mtime past STALL_AFTER_S and confirming is_stalled
    still fires under a failed walk proves the fallback keeps that real,
    aged value rather than resetting to "now" -- the one wrong answer that
    would silently defeat this alarm's whole purpose."""
    layout, manifest = session
    layout.done_marker("spikeglx").unlink()
    old = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=5)).timestamp()
    os.utime(layout.dir, (old, old))

    real_rglob = Path.rglob

    def failing_rglob(self, pattern):
        if self == layout.dir:
            raise FileNotFoundError(f"simulated: {self} unreadable mid-walk")
        yield from real_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", failing_rglob)

    assert is_stalled(layout, manifest, now=datetime.datetime.now(datetime.UTC)) is True
