"""Rehydration's pieces that need no database: the staging names, the
watcher's blindness to them, the scratch floor, and the checks on the four
files no rig digest names (2026-09-26 rehydration design, sections 1 and 5.4).
"""

from types import SimpleNamespace

import blake3
import pytest

from wl_preproc.archive.rehydrate import _check
from wl_preproc.archive.scratch import (
    RECLAIMING,
    REHYDRATING,
    Refused,
    refuse_leftovers,
    staging_dir,
)
from wl_preproc.archive.verify import _expected_digests
from wl_preproc.cli import doctor
from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import DONE_MARKER_FILENAME, MANIFEST_FILENAME
from wl_preproc.ingest.landing import manifest_session_key
from wl_preproc.ingest.watcher import _candidate_dirs
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def _session(tmp_path):
    generate_session(tmp_path / "root", CI_RECIPE)
    return tmp_path / "root" / CI_RECIPE.session_id


def _key(session):
    manifest = SessionManifest.from_yaml((session / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    return manifest_session_key(manifest)


def _written(session):
    return {
        str(p.relative_to(session)): blake3.blake3(p.read_bytes()).hexdigest()
        for p in session.rglob("*")
        if p.is_file()
    }


def test_staging_directories_are_hidden_siblings(tmp_path):
    session = tmp_path / "root" / "2027-03-14_01"
    assert staging_dir(session, RECLAIMING) == tmp_path / "root" / ".2027-03-14_01.reclaiming"
    assert staging_dir(session, REHYDRATING) == tmp_path / "root" / ".2027-03-14_01.rehydrating"


def test_the_watcher_never_takes_a_staged_session_for_a_session(tmp_path):
    """Design section 1, fact 4: this is why both commands stage one level
    down. A staged session directly under the root would be scanned, and
    quarantined as `session_id_mismatch`."""
    session = _session(tmp_path)
    staging = staging_dir(session, REHYDRATING)
    staging.mkdir()
    session.rename(staging / session.name)

    candidates, fault = _candidate_dirs(tmp_path / "root")

    assert fault is None
    assert candidates == []


def test_a_leftover_is_refused_even_as_a_dangling_symlink(tmp_path):
    session = tmp_path / "root" / "2027-03-14_01"
    session.parent.mkdir()
    refuse_leftovers(session)  # nothing there: no refusal
    staging_dir(session, RECLAIMING).symlink_to(tmp_path / "gone")
    with pytest.raises(Refused, match="left over"):
        refuse_leftovers(session)


def test_headroom_after_keeps_the_floor_clear(monkeypatch):
    gib = 2**30
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda path: SimpleNamespace(free=900 * gib))
    assert doctor.headroom_after("/scratch", 0) is True
    assert doctor.headroom_after("/scratch", 100 * gib) is True  # leaves exactly the floor
    assert doctor.headroom_after("/scratch", 100 * gib + 1) is False


def test_a_faithful_restore_passes_every_check(tmp_path):
    session = _session(tmp_path)
    verdicts = _check(session, _key(session), _expected_digests(session), _written(session))
    assert verdicts
    assert all(v.matched for v in verdicts)


def test_a_file_missing_from_the_artifact_fails(tmp_path):
    session = _session(tmp_path)
    expected = _expected_digests(session)
    written = _written(session)
    missing = sorted(expected)[0]
    del written[missing]

    failures = [v for v in _check(session, _key(session), expected, written) if not v.matched]

    assert [v.relative_path for v in failures] == [missing]
    assert failures[0].actual == "not in the artifact"


def test_a_manifest_naming_another_session_fails(tmp_path):
    session = _session(tmp_path)
    other = {**_key(session), "subject": "someone"}

    failures = [
        v for v in _check(session, other, _expected_digests(session), _written(session))
        if not v.matched
    ]

    assert [v.relative_path for v in failures] == [MANIFEST_FILENAME]


def test_a_done_marker_that_disagrees_with_the_recorded_digests_fails(tmp_path):
    """The markers carry no rig digest of their own; what proves them is that
    together they list exactly the digests recorded at archive time."""
    session = _session(tmp_path)
    expected = _expected_digests(session)
    marker = next(session.rglob(DONE_MARKER_FILENAME))
    marker.write_text(
        marker.read_text(encoding="utf-8").replace("blake3: ", "blake3: 0", 1), encoding="utf-8"
    )

    failures = [v for v in _check(session, _key(session), expected, _written(session)) if not v.matched]

    assert [v.relative_path for v in failures] == ["DONE markers"]
