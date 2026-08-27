import pytest

from wl_preproc.schema import archive


def test_the_four_tables_exist():
    for name in (
        "ArchiveArtifact",
        "ArchiveVerification",
        "ReclamationHold",
        "ScratchReclamation",
    ):
        assert hasattr(archive, name), name


def test_verification_is_keyed_per_file_not_per_session():
    """Design spec section 4: when it fails the question is WHICH file, and a
    per-session boolean cannot answer it."""
    assert "relative_path" in archive.ArchiveVerification.definition


def test_no_status_column_on_the_artifact():
    """Plan 10 section 1 forbids a status column; the answer is derived from
    these four tables (design spec section 8)."""
    assert "status" not in archive.ArchiveArtifact.definition
