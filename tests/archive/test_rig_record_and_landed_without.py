"""The archive of a session taken in without the sync box (design spec
`2026-10-18-training-records-and-backup-clock-design.md`, amendments 33 and 35;
part 3a's final review, C1, I1 and I2): the rig's record is archived against
its own marker, a session with nothing else is archived too, and a system a
person landed the session without can neither stop the archive nor be
verified against a marker that never finished."""

from __future__ import annotations

import datetime

import pytest
import yaml

from wl_preproc.archive.stage import archive_session
from wl_preproc.archive.verify import verify_store
from wl_preproc.contracts.done import blake3_file
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def _session(tmp_path):
    generate_session(tmp_path / "in", CI_RECIPE)
    return SessionLayout(tmp_path / "in", CI_RECIPE.session_id)


def _expect_the_rigs_record(layout):
    text = layout.manifest_path.read_text().replace("expects_rig_record: false", "expects_rig_record: true")
    layout.manifest_path.write_text(text)
    files = [{"path": str(path.relative_to(layout.xcon_dir)), "bytes": path.stat().st_size,
              "blake3": blake3_file(path)}
             for path in sorted(layout.xcon_dir.rglob("*")) if path.is_file() and path.name != "DONE"]
    layout.rig_done_marker.write_text(yaml.safe_dump({
        "schema_version": 1, "system": "xcon",
        "transfer_finished_at": datetime.datetime(2027, 3, 14, 10, tzinfo=datetime.UTC).isoformat(),
        "files": files}, sort_keys=False))


def _landed_without(key, system, session_dir, prefix):
    from wl_preproc.schema import ingest

    ingest.activate(prefix=prefix)
    ingest.LandedWithout.insert1({**key, "system": system, "actor": "J. W.", "reason": "r",
                                  "recorded_at": datetime.datetime(2027, 3, 14, 11), "session_dir": str(session_dir)})


def test_a_session_expecting_the_rigs_record_archives_it_against_its_marker(tmp_path, dj_conn, prefix):
    layout = _session(tmp_path)
    _expect_the_rigs_record(layout)
    key = {"subject": "arcrig1", "session_datetime": datetime.datetime(2027, 3, 14, 9)}
    outcome = archive_session(layout.dir, tmp_path / "nas", key, prefix=prefix)
    assert outcome.all_matched
    assert "xcon/trials.jsonl" in {verdict.relative_path for verdict in verify_store(outcome.artifact_path,
                                                                                     layout.dir)}


def test_a_session_with_only_the_rigs_record_lands_verified_and_archives(tmp_path, dj_conn, prefix):
    """Section 5's session with no acquisition system: its record alone, checked on arrival and archived,
    so nothing is lost. It is deleted afterwards: it has no clock."""
    import shutil

    from wl_preproc.ingest.watcher import Outcome, scan_once
    from wl_preproc.schema import ingest, pipeline

    layout = _session(tmp_path)
    text = layout.manifest_path.read_text().replace(f"subject: {CI_RECIPE.subject}", "subject: arcrig2")
    for system in CI_RECIPE.systems:
        text = text.replace(f"- {system}\n", "")
        shutil.rmtree(layout.system_dir(system))
    layout.manifest_path.write_text(text.replace("expected_systems:\n", "expected_systems: []\n"))
    _expect_the_rigs_record(layout)
    ingest.activate(prefix=prefix)
    try:
        assert scan_once(layout.root, prefix=prefix).outcomes[str(layout.dir)] is Outcome.INGESTED
        row = (ingest.Ingestion & {"session_dir": str(layout.dir)}).fetch1()
        assert row["integrity"] == "verified"
        key = {"subject": row["subject"], "session_datetime": row["session_datetime"]}
        assert archive_session(layout.dir, tmp_path / "nas", key, prefix=prefix).all_matched
    finally:
        (pipeline.Session & {"subject": "arcrig2"}).delete(prompt=False)


def test_a_corrupt_marker_of_a_system_landed_without_does_not_stop_the_archive(tmp_path, dj_conn, prefix):
    layout = _session(tmp_path)
    layout.done_marker("bcam").write_text("{this is: [not a marker", encoding="utf-8")
    key = {"subject": "arcwvd1", "session_datetime": datetime.datetime(2027, 3, 14, 9)}
    _landed_without(key, "bcam", layout.dir, prefix)
    assert archive_session(layout.dir, tmp_path / "nas", key, prefix=prefix).all_matched


def test_partial_files_of_a_system_landed_without_are_stored_as_they_are(tmp_path, dj_conn, prefix):
    """A SpikeGLX transfer that stalled can leave a `.bin` without its `.meta`:
    stored byte for byte, never laid out as a stream it cannot be."""
    from wl_preproc.archive.verify import stored_paths

    layout = _session(tmp_path)
    meta = next(layout.system_dir("spikeglx").rglob("*.meta"))
    meta.unlink()
    key = {"subject": "arcwvd2", "session_datetime": datetime.datetime(2027, 3, 14, 9)}
    _landed_without(key, "spikeglx", layout.dir, prefix)
    outcome = archive_session(layout.dir, tmp_path / "nas", key, prefix=prefix)
    assert outcome.all_matched
    binary = meta.with_suffix(".bin").relative_to(layout.dir)
    assert str(binary) in stored_paths(outcome.artifact_path)


def test_a_partial_intan_recording_of_a_system_landed_without_is_stored_as_it_is(tmp_path, dj_conn, prefix):
    """An RHS transfer that stalled can leave `amplifier.dat` without its
    `time.dat`: stored byte for byte too."""
    from wl_preproc.archive.verify import stored_paths

    layout = _session(tmp_path)
    amplifier = layout.system_dir("rhs") / "amplifier.dat"
    amplifier.parent.mkdir()
    amplifier.write_bytes(bytes(range(256)) * 4)
    key = {"subject": "arcwvd3", "session_datetime": datetime.datetime(2027, 3, 14, 9)}
    _landed_without(key, "rhs", layout.dir, prefix)
    outcome = archive_session(layout.dir, tmp_path / "nas", key, prefix=prefix)
    assert outcome.all_matched
    assert str(amplifier.relative_to(layout.dir)) in stored_paths(outcome.artifact_path)


@pytest.fixture(autouse=True)
def _forget_what_was_landed_without(dj_conn, prefix):
    yield
    from wl_preproc.schema import ingest

    ingest.activate(prefix=prefix)
    (ingest.LandedWithout & "subject in ('arcwvd1', 'arcwvd2', 'arcwvd3')").delete(prompt=False)
