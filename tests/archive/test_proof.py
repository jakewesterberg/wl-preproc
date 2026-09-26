"""The checks run against the NAS copy before scratch is freed, and before a
session is restored (2026-09-26 rehydration design, sections 3 and 5.2). No
database: `prove_artifact` takes the recorded digest and the rig's digests as
arguments."""

from wl_preproc.archive.proof import prove_artifact
from wl_preproc.archive.stage import SENTINEL_NAME
from wl_preproc.archive.store import manifest_digest, write_store
from wl_preproc.archive.verify import _expected_digests
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def _published(tmp_path):
    """A store as `archive_session` leaves it on the NAS: the digest taken
    first, THEN the sentinel written -- so the published tree holds one file
    the recorded digest never covered."""
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    result = write_store(session, tmp_path / "nas")
    (result.path / SENTINEL_NAME).write_text("2027-03-14T10:00:00+00:00", encoding="utf-8")
    return session, result.path, result.manifest_digest


def _corrupt_a_chunk(artifact):
    victim = next(
        p
        for p in sorted((artifact / "streams").rglob("*"))
        if p.is_file() and not p.name.startswith(".")
    )
    victim.write_bytes(b"\x00" * victim.stat().st_size)


def test_an_intact_artifact_is_proven_file_by_file(tmp_path):
    session, artifact, digest = _published(tmp_path)
    expected = _expected_digests(session)

    proof = prove_artifact(artifact, digest, expected)

    assert proof.passed, proof.reason
    assert proof.reason == ""
    assert len(proof.verdicts) == len(expected)
    assert all(v.matched for v in proof.verdicts)


def test_without_expected_digests_only_the_sentinel_and_digest_run(tmp_path):
    _session, artifact, digest = _published(tmp_path)

    proof = prove_artifact(artifact, digest, None)

    assert proof.passed, proof.reason
    assert proof.verdicts == ()


def test_the_digest_leaves_out_the_sentinel_and_nothing_else(tmp_path):
    _session, artifact, digest = _published(tmp_path)

    assert manifest_digest(artifact) != digest
    assert manifest_digest(artifact, exclude=frozenset({SENTINEL_NAME})) == digest
    (artifact / "stray.txt").write_text("x", encoding="utf-8")
    assert manifest_digest(artifact, exclude=frozenset({SENTINEL_NAME})) != digest


def test_no_sentinel_is_refused_and_the_path_is_named(tmp_path):
    _session, artifact, digest = _published(tmp_path)
    (artifact / SENTINEL_NAME).unlink()

    proof = prove_artifact(artifact, digest, None)

    assert not proof.passed
    assert f"no completion sentinel at {artifact / SENTINEL_NAME}" in proof.reason


def test_a_missing_nas_mount_is_refused_not_raised(tmp_path):
    """Review Focus 2: an unmounted share is a refusal naming the path, never
    a traceback."""
    artifact = tmp_path / "not-mounted" / "subj" / "2027-03-14_01.zarr"

    proof = prove_artifact(artifact, "0" * 64, {"a.bin": "0" * 64})

    assert not proof.passed
    assert f"no completion sentinel at {artifact / SENTINEL_NAME}" in proof.reason


def test_an_artifact_changed_since_archiving_is_refused_before_rebuilding(tmp_path):
    session, artifact, digest = _published(tmp_path)
    _corrupt_a_chunk(artifact)

    proof = prove_artifact(artifact, digest, _expected_digests(session))

    assert not proof.passed
    assert "has changed since it was archived" in proof.reason
    assert proof.verdicts == ()


def test_a_file_that_no_longer_rebuilds_is_refused_even_when_the_digest_matches(tmp_path):
    """Why the third check exists: the digest proves the bytes are unchanged,
    not that this build can still read them. Simulated by recording the
    damaged tree's own digest, so check 2 passes and only check 3 can fail."""
    session, artifact, _digest = _published(tmp_path)
    _corrupt_a_chunk(artifact)
    recorded_now = manifest_digest(artifact, exclude=frozenset({SENTINEL_NAME}))

    proof = prove_artifact(artifact, recorded_now, _expected_digests(session))

    assert not proof.passed
    assert "did not rebuild to the rig's digest" in proof.reason
    assert any(not v.matched for v in proof.verdicts)


def test_nothing_to_rebuild_against_is_never_proven(tmp_path):
    _session, artifact, digest = _published(tmp_path)

    proof = prove_artifact(artifact, digest, {})

    assert not proof.passed
    assert "no recorded rig digests" in proof.reason
