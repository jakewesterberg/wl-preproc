from wl_preproc.archive.tape import TapeEntry, staging_manifest


def test_the_manifest_names_every_entry_with_its_digest():
    """A person carries this to the machine with the drive. Without the digest
    they cannot check the write, and checking the write is the point."""
    entries = [
        TapeEntry("session_001", "/nas/archive/2027-03-14_01.zarr", 104_857_600, "ab" * 32),
        TapeEntry("session_002", "/nas/archive/2027-03-14_02.zarr", 209_715_200, "cd" * 32),
    ]
    text = staging_manifest(entries)
    for entry in entries:
        assert entry.session_id in text
        assert entry.artifact_path in text
        assert entry.manifest_digest in text


def test_an_empty_manifest_says_so_rather_than_being_blank():
    text = staging_manifest([])
    assert text.strip()
    assert "no sessions" in text.lower()
