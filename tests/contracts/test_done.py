"""The DONE marker's body. Its *existence* is the completion signal and always
was; the body is what makes transfer integrity checkable at the destination."""

from __future__ import annotations

import datetime

import pytest
from pydantic import ValidationError

from wl_preproc.contracts.done import (
    DONE_SCHEMA_VERSION,
    DoneMarker,
    FileEntry,
    blake3_file,
)


def _marker() -> DoneMarker:
    return DoneMarker(
        schema_version=DONE_SCHEMA_VERSION,
        system="spikeglx",
        transfer_finished_at=datetime.datetime(2027, 3, 14, 19, 4, 11, tzinfo=datetime.UTC),
        files=[FileEntry(path="run0_g0_t0.imec0.ap.bin", bytes=1024, blake3="9f2c")],
    )


def test_round_trips_through_yaml():
    assert DoneMarker.from_yaml(_marker().to_yaml()) == _marker()


def test_rejects_unknown_keys():
    """extra="forbid", for the same reason session_params.yaml rejects them:
    a typo must fail loudly rather than silently defaulting."""
    text = _marker().to_yaml() + "\nnfiles: 3\n"
    with pytest.raises(ValidationError):
        DoneMarker.from_yaml(text)


def test_rejects_naive_transfer_time():
    with pytest.raises(ValidationError):
        DoneMarker(
            schema_version=DONE_SCHEMA_VERSION,
            system="spikeglx",
            transfer_finished_at=datetime.datetime(2027, 3, 14, 19, 4, 11),
            files=[],
        )


def test_rejects_unknown_system():
    with pytest.raises(ValidationError):
        DoneMarker(
            schema_version=DONE_SCHEMA_VERSION,
            system="not_a_system",
            transfer_finished_at=datetime.datetime(2027, 3, 14, tzinfo=datetime.UTC),
            files=[],
        )


def test_rejects_a_path_traversal_component():
    """verify.py joins `path` onto a system directory unchecked
    (`system_dir / entry.path`), so a `..` component is not hypothetical: a
    transfer script with a reversed `relpath` call can emit exactly this,
    with no malice involved, and walk the read straight out of the system
    directory."""
    with pytest.raises(ValidationError):
        FileEntry(path="../../etc/passwd", bytes=1024, blake3="9f2c")


def test_rejects_an_absolute_path():
    """The sharper variant of the traversal above: an absolute path needs no
    `..` at all. `Path.__truediv__` discards the left operand entirely when
    the right one is absolute, so `system_dir / "/etc/passwd"` *is*
    `/etc/passwd` — silently, with `system_dir` playing no role."""
    with pytest.raises(ValidationError):
        FileEntry(path="/etc/passwd", bytes=1024, blake3="9f2c")


def test_blake3_file_matches_the_reference_digest(tmp_path):
    """Pinned against blake3's own digest of the same bytes, so a chunking bug
    in the streaming read cannot pass. A self-consistent test that hashed the
    file twice with the same helper would prove nothing."""
    import blake3

    payload = b"x" * (9 * 1024 * 1024 + 7)  # spans chunks, ends ragged
    target = tmp_path / "big.bin"
    target.write_bytes(payload)

    assert blake3_file(target, chunk_bytes=4 * 1024 * 1024) == blake3.blake3(payload).hexdigest()
