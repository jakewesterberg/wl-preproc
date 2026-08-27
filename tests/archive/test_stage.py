from wl_preproc.archive.stage import SENTINEL_NAME, archive_session
from wl_preproc.contracts.paths import DONE_MARKER_FILENAME
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def test_a_verified_session_gets_a_sentinel(tmp_path):
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    outcome = archive_session(session, tmp_path / "nas", "wl-nas", "archive")

    assert outcome.all_matched
    assert (outcome.artifact_path / SENTINEL_NAME).exists()
    # Mirrors the survives-a-failure assertion in the test below: on success,
    # scratch is reclaimed. Computed the same way archive_session computes
    # it -- a private detail with no public accessor, duplicated here rather
    # than exposed from the function just to make a test's job easier.
    scratch = session.parent / f".{session.name}.archiving"
    assert not scratch.exists()


def test_the_sentinel_is_absent_when_verification_fails(tmp_path):
    """Its whole purpose is telling a prober whole from partial. A sentinel on
    an unverified artifact is worse than none -- wl.works reads `complete` and
    believes it.

    Scratch must survive this path too, and that is pinned below rather than
    left as an unasserted side effect: a confirm-caught transfer corruption
    needs the pre-transfer copy on scratch to show which side -- scratch or
    the NAS -- is actually wrong, and losing it means re-running a
    compression the design flags as heavy on a real session (spec section 10
    item 5). Without this assertion, moving `rmtree(scratch)` back outside
    the `all_matched` gate in stage.py would still pass every other test in
    this file."""
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    marker = next(session.rglob(DONE_MARKER_FILENAME))
    text = marker.read_text(encoding="utf-8")
    # YAML, not JSON: `blake3: <hex>` on its own line.
    marker.write_text(text.replace("blake3: ", "blake3: 0"), encoding="utf-8")

    outcome = archive_session(session, tmp_path / "nas", "wl-nas", "archive")
    assert not outcome.all_matched
    assert not (outcome.artifact_path / SENTINEL_NAME).exists()
    # Computed the same way archive_session computes it -- see the comment
    # in test_a_verified_session_gets_a_sentinel above.
    scratch = session.parent / f".{session.name}.archiving"
    assert scratch.exists()
