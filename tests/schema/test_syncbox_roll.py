"""A session whose sync box rolled in place, its process going on in a second
segment of the same folder (design spec `2026-11-04-syncbox-header-design.md`
sections 4 and 10): one clock, so a daemon pass rejects nothing, places every
code, and tiers the session A, as it would without the roll."""

from __future__ import annotations

import pytest

from tests.identities import new_animal


@pytest.fixture(scope="module")
def landed(dj_conn, prefix, tmp_path_factory):
    """Two `ci` sessions, one plain and one whose box rolled in place at 7.5 s,
    after one daemon pass; deleted after the module."""
    from wl_preproc import daemon
    from wl_preproc.schema import pipeline
    from wl_preproc.synth.recipe import RECIPES, SyncBoxRollSpec
    from wl_preproc.synth.session import generate_session

    from tests.schema.test_eye_populate import _land

    daemon.activate_all(prefix=prefix)
    keys = []
    for name, update in (("plain", {}), ("rolled", {"syncbox_roll": SyncBoxRollSpec(at_s=7.5)})):
        root = tmp_path_factory.mktemp(name)
        session = new_animal().session()
        recipe = RECIPES["ci"].model_copy(update={"subject": session.subject, "session_id": session.session_id,
                                                  **update})
        generate_session(root, recipe)
        keys.append(_land(root, recipe, session.session_datetime, acquisition_systems=recipe.systems))
    report = daemon.run_once(prefix=prefix)
    yield *keys, report
    for key in keys:
        (pipeline.Session & key).delete(prompt=False)


def test_a_roll_in_place_rejects_nothing_and_both_segments_keep_session_time(landed):
    from wl_preproc.schema import core, timebase

    _plain, rolled, report = landed
    assert not (core.RejectedSegment & rolled).to_dicts()
    assert [(row["file_path"], row["role"]) for row in (timebase.SyncBoxSegment & rolled).to_dicts(
        order_by="segment_index")] == [("seg-000.log", "session_time"), ("seg-001.log", "session_time")]
    assert not [error for error in report["errors"] if rolled["subject"] in str(error)]


def test_a_roll_in_place_places_every_code_and_tiers_the_session_as_without_it(landed):
    from wl_preproc.schema import pipeline, timebase

    plain, rolled, _ = landed
    assert len(pipeline.trial.Trial & rolled) == len(pipeline.trial.Trial & plain) > 0
    assert [(timebase.TimingProvenance & key).fetch1("tier") for key in (plain, rolled)] == ["A", "A"]
