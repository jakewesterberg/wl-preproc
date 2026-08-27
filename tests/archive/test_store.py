import numpy as np
import zarr

from wl_preproc.archive.layout import bulk_streams
from wl_preproc.archive.store import manifest_digest, write_store
from wl_preproc.contracts.paths import DONE_MARKER_FILENAME
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def test_every_bulk_stream_becomes_an_array(tmp_path):
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    result = write_store(session, tmp_path / "out")

    root = zarr.open(str(result.path), mode="r")
    expected = {s.path.name for s in bulk_streams(session)}
    assert set(root["streams"].array_keys()) == expected


def test_a_non_stream_file_is_stored_verbatim(tmp_path):
    """Verbatim, not re-encoded: the manifest and the DONE markers are what
    verification reads its reference digests out of."""
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    result = write_store(session, tmp_path / "out")

    original = next(session.rglob(DONE_MARKER_FILENAME))
    relative = str(original.relative_to(session))
    root = zarr.open(str(result.path), mode="r")
    stored = bytes(root["verbatim"][relative][:])
    assert stored == original.read_bytes()


def test_the_store_is_smaller_than_the_session(tmp_path):
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    raw = sum(p.stat().st_size for p in session.rglob("*") if p.is_file())
    result = write_store(session, tmp_path / "out")
    assert result.compressed_bytes < raw
    assert result.codec == "zstd"


def test_the_manifest_digest_is_stable_and_order_independent(tmp_path):
    """A Zarr store is a directory tree with no single hash. The digest is over
    sorted (path, blake3) pairs, so two identical copies agree regardless of
    the order a walk happens to return."""
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    a = write_store(session, tmp_path / "a")
    b = write_store(session, tmp_path / "b")
    assert a.manifest_digest == b.manifest_digest
    assert manifest_digest(a.path) == a.manifest_digest


def test_a_changed_byte_changes_the_manifest_digest(tmp_path):
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    result = write_store(session, tmp_path / "out")
    before = manifest_digest(result.path)

    victim = next(p for p in sorted(result.path.rglob("*")) if p.is_file())
    victim.write_bytes(victim.read_bytes() + b"\x00")
    assert manifest_digest(result.path) != before
