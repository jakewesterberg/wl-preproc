"""A session whose sync box restarted partway through (design spec
`2026-10-22-syncbox-records-and-segments-design.md` section 4): its second
segment is on another clock, so nothing in it is mixed into session time. A
daemon pass rejects it with a reason and completes, the other devices are
timed by their own fits, and the session is tier D until part 2 bridges it."""

from __future__ import annotations

import pytest

from tests.identities import new_animal


@pytest.fixture(scope="module")
def restarted(dj_conn, prefix, tmp_path_factory):
    """A `ci` session whose box restarted at 7.5 s for 1.2 s, landed by hand as
    `test_eye_populate.py` lands one, after one daemon pass; deleted after the
    module, so no later module's unrestricted populate meets it."""
    from wl_preproc import daemon
    from wl_preproc.schema import pipeline
    from wl_preproc.synth.recipe import RECIPES, SyncBoxRestartSpec
    from wl_preproc.synth.session import generate_session

    from tests.schema.test_eye_populate import _land

    daemon.activate_all(prefix=prefix)
    root = tmp_path_factory.mktemp("restarted")
    session = new_animal().session()
    recipe = RECIPES["ci"].model_copy(update={"subject": session.subject, "session_id": session.session_id,
                                              "syncbox_restart": SyncBoxRestartSpec(at_s=7.5, gap_s=1.2)})
    generate_session(root, recipe)
    key = _land(root, recipe, session.session_datetime, acquisition_systems=recipe.systems)
    report = daemon.run_once(prefix=prefix)
    yield key, root / recipe.session_id, report
    (pipeline.Session & key).delete(prompt=False)


def test_the_boxs_segment_after_a_restart_is_rejected_with_its_reason(restarted):
    from wl_preproc.schema import core

    key, _, _ = restarted
    rejected = (core.RejectedSegment & key).to_dicts()
    assert [(row["system"], row["file_path"], row["reason"]) for row in rejected] == [
        ("syncbox", "seg-001.log", "no_shared_barcode")]


def test_the_stage_completes_and_every_device_is_timed(restarted):
    """`fit_offset` raised on a segment sharing no barcode with session time,
    and nothing caught it, so the whole stage failed instead of recording why."""
    from wl_preproc.schema import core

    key, _, _ = restarted
    assert not len(core.Segment.jobs & key & "status = 'error'")
    assert {(row["system"], row["file_path"]) for row in (core.Segment & key).to_dicts()} >= {
        ("syncbox", "seg-000.log"), ("bcam", "frames.yaml")}
    assert "spikeglx" in {row["system"] for row in (core.Segment & key).to_dicts()}


def test_a_session_whose_box_restarted_is_tier_d(restarted):
    from wl_preproc.schema import timebase

    key, _, _ = restarted
    assert (timebase.TimingProvenance & key).fetch1("tier") == "D"
