import datetime
import pytest

from wl_preproc.schema import archive


def test_the_five_tables_exist():
    for name in (
        "ArchiveArtifact",
        "ArchiveVerification",
        "ReclamationHold",
        "ScratchReclamation",
        "ScratchRehydration",
    ):
        assert hasattr(archive, name), name


def test_verification_is_keyed_per_file_not_per_session():
    """Design spec section 4: when it fails the question is WHICH file, and a
    per-session boolean cannot answer it."""
    assert "relative_path" in archive.ArchiveVerification.definition


def test_no_status_column_on_the_artifact():
    """Plan 10 section 1 forbids a status column; the answer is derived from
    these five tables (design spec section 8)."""
    assert "status" not in archive.ArchiveArtifact.definition


def _session_row(prefix, subject):
    from wl_preproc.schema import pipeline

    archive.activate(prefix=prefix)
    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": subject,
            "sex": "U",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    key = {"subject": subject, "session_datetime": datetime.datetime(2027, 5, 1, 9, 0)}
    pipeline.Session.insert1(key, skip_duplicates=True)
    return key


def test_a_session_can_be_reclaimed_more_than_once(dj_conn, prefix):
    """Freed, rehydrated, freed again: two rows, not a key collision
    (2026-09-26 rehydration design, section 7)."""
    key = _session_row(prefix, "tblrcl")
    for hour in (10, 12):
        archive.ScratchReclamation.insert1(
            {**key, "reclaimed_at": datetime.datetime(2027, 5, 1, hour, 0), "bytes_freed": 1024}
        )
    assert len(archive.ScratchReclamation & key) == 2


def test_rehydrations_are_a_history_too(dj_conn, prefix):
    key = _session_row(prefix, "tblrhy")
    for hour in (11, 13):
        archive.ScratchRehydration.insert1(
            {**key, "rehydrated_at": datetime.datetime(2027, 5, 1, hour, 0), "bytes_written": 2048}
        )
    assert len(archive.ScratchRehydration & key) == 2
