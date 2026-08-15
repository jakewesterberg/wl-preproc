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
    it.

    Asserting only `result.tzinfo is datetime.UTC` proved nothing, and review
    caught it by proof: that is true of every value this function can return,
    on every path through it, so deleting the inner per-candidate guard
    (sentinel.py's `except OSError: continue` around `candidate.stat()`)
    while keeping the outer one left the whole 366-test suite green. The two
    guards are not redundant. Without the inner one, the dangling entry's
    OSError escapes to the outer `except OSError: pass`, which ABANDONS the
    walk and keeps whatever `newest` held at that moment -- here the
    backdated directory's own mtime -- so a session actively receiving files
    reports two weeks of quiet against a 2 h threshold and lands in the daily
    report as a stalled transfer.

    So the layout is the assertion: the dangling symlink sits at the top
    level and the only fresh file one level down, because `rglob("*")` yields
    every entry of a directory before descending into its subdirectories
    (verified directly on 3.11.15 and 3.13.12, whose glob implementations are
    otherwise unrelated). The walk must therefore survive the symlink to
    reach the fresh file at all, and the returned mtime says whether it did.
    """
    layout, manifest = session
    layout.done_marker("spikeglx").unlink()  # incomplete, as this function's callers are
    now = datetime.datetime.now(datetime.UTC)
    old = int((now - datetime.timedelta(days=14)).timestamp())
    fresh = int((now - datetime.timedelta(minutes=1)).timestamp())

    still_arriving = layout.system_dir("spikeglx") / "probe0.imec0.ap.bin.partial"
    still_arriving.write_bytes(b"a transfer in progress")
    (layout.dir / "dangling").symlink_to(layout.dir / "does-not-exist")
    for path in [layout.dir, *layout.dir.rglob("*")]:
        if not path.is_symlink():
            os.utime(path, (old, old))
    os.utime(still_arriving, (fresh, fresh))

    result = last_change_at(layout.dir)

    assert result.tzinfo is datetime.UTC
    assert result == datetime.datetime.fromtimestamp(fresh, tz=datetime.UTC), (
        "the walk stopped at the dangling symlink instead of skipping it, so the "
        "newest mtime it returned is the backdated directory's, not the file still "
        "being written"
    )
    # The consequence, not just the value: this session is one minute quiet,
    # not two weeks quiet, and must not be reported as a stalled transfer.
    assert is_stalled(layout, manifest, now=now) is False


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


def test_last_change_at_falls_back_to_ancient_under_an_ancestor_permission_fault(session):
    """Round-4 finding: round 3 shipped "just touched" as the fallback for a
    failed leading session_dir.stat(); it should have been ancient
    (datetime.min). A permission fault on the session directory itself does
    not even reach this branch -- stat() only needs search permission on the
    *parent*, not the target (proven in the companion test below) -- so what
    actually triggers this is an ancestor-level fault: a broken NFS export,
    an ACL misconfiguration, an autofs hiccup at the storage root. That is a
    standing condition hitting every sibling session at once, not a narrow
    one-directory race, and newest is only ever raised by max(), never
    lowered, so this fallback is not one input among several -- it becomes
    the walk's permanent floor. A false "stalled" clears itself the moment
    the fault is fixed; "just touched" would instead make a whole storage
    root's worth of dead transfers read as merely active for as long as the
    misconfiguration lasts -- the exact invisible dead transfer this module
    exists to surface.

    This must be a real os.chmod, not a Path.stat monkeypatch: Python 3.13's
    rewritten glob.py calls os.scandir on raw path strings, bypassing any
    patched Path method entirely, so a monkeypatch would let the real,
    unpatched walk find real, fresh files on 3.13 and overwrite the fallback
    via max() before the assertion ever ran -- passing for the wrong reason
    on exactly the interpreter the preprocessing server runs."""
    layout, manifest = session
    layout.done_marker("spikeglx").unlink()
    old = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=10)).timestamp()
    os.utime(layout.dir, (old, old))

    parent = layout.dir.parent
    original_mode = parent.stat().st_mode
    os.chmod(parent, 0o000)
    try:
        result = last_change_at(layout.dir)
        stalled = is_stalled(layout, manifest, now=datetime.datetime.now(datetime.UTC))
    finally:
        os.chmod(parent, original_mode)

    assert result == datetime.datetime.min.replace(tzinfo=datetime.UTC)
    assert stalled is True


def test_a_permission_fault_on_the_session_directory_itself_still_finds_its_real_mtime(session):
    """Companion to the test above, proving the specific claim its docstring
    makes rather than asserting it: stat() needs search permission on the
    *parent*, not the target, so denying all permissions on the session
    directory itself does not reach the ancient-fallback branch at all --
    the leading stat() still succeeds and returns the real mtime. (rglob's
    later attempt to list the directory's own now-unreadable contents finds
    nothing, silently, via pathlib's own PermissionError handling -- not a
    failure this function's guards need to intervene on.)"""
    layout, manifest = session
    real_mtime = layout.dir.stat().st_mtime
    original_mode = layout.dir.stat().st_mode
    os.chmod(layout.dir, 0o000)
    try:
        result = last_change_at(layout.dir)
    finally:
        os.chmod(layout.dir, original_mode)

    assert result == datetime.datetime.fromtimestamp(real_mtime, tz=datetime.UTC)


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
