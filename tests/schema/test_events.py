"""Populating element-event's tables from a decoded session."""

from __future__ import annotations

import datetime
import itertools

import pytest

from wl_preproc.schema import events


@pytest.fixture(scope="module")
def events_activated(dj_conn, prefix):
    events.activate(prefix=prefix)
    return events


def test_event_types_are_projected_from_the_frozen_marker_enum(events_activated):
    """EventType is a projection of contracts/events.Marker, not a hand-typed
    second list. A marker added to the frozen contract must appear here by
    construction -- a hand-listed copy is the shape that has been missed three
    times in this repository (ingest, timebase, ephys)."""
    from wl_preproc.contracts.events import Marker
    from wl_preproc.schema import pipeline

    events.sync_event_types()
    stored = set(pipeline.event.EventType.to_arrays("event_type"))
    assert {m.name for m in Marker} <= stored


def test_behavior_recording_is_one_per_session_by_construction(events_activated):
    """element-event declares BehaviorRecording as `-> Session` with NO
    additional key attribute, so its primary key IS the session key. That is
    what makes 'relative to recording start' the same number as session time
    (t=0 at the first barcode) -- and a second recording per session is
    unrepresentable rather than merely discouraged."""
    from wl_preproc.schema import pipeline

    assert pipeline.event.BehaviorRecording.primary_key == [
        "subject",
        "session_datetime",
    ]


# A distinct session_datetime per call, so each test using `ci_session` below
# lands its own row in the one database this module's tests share, rather than
# colliding with another test's session on (subject, session_datetime).
_session_datetime_offset = itertools.count()


@pytest.fixture
def ci_session(events_activated, dj_conn, prefix, tmp_path):
    """A landed CI-recipe session, with Lab/Subject/Session in place and its
    files on disk, but nothing from this module populated yet.
    """
    from wl_preproc.schema import pipeline
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session

    recipe = RECIPES["ci"]
    generate_session(tmp_path, recipe)
    session_dir = tmp_path / recipe.session_id

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": recipe.subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    session_key = {
        "subject": recipe.subject,
        "session_datetime": datetime.datetime(2027, 3, 22, 9, 0)
        + datetime.timedelta(minutes=next(_session_datetime_offset)),
    }
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    return session_key, session_dir, recipe


def test_populate_session_builds_the_canonical_trial_and_block_lists(ci_session):
    """The core deliverable, exercised end to end against a real synthetic
    session rather than merely 'some rows exist': the CI recipe's own two
    blocks (3 trials, then 1) land as the exact trial/block ids and the exact
    block-to-trial associations `synth/timeline.py` planted, recovered purely
    from the sync box's own decoded code stream.
    """
    from wl_preproc.schema import pipeline

    session_key, session_dir, recipe = ci_session

    events.populate_session(session_key, session_dir)

    assert len(pipeline.event.BehaviorRecording & session_key) == 1

    trial_ids = sorted((pipeline.trial.Trial & session_key).to_arrays("trial_id"))
    assert trial_ids == [1, 2, 3, 4], "CI_RECIPE plants 3 + 1 = 4 trials, ids 1..4"

    block_ids = sorted((pipeline.trial.Block & session_key).to_arrays("block_id"))
    assert block_ids == [1, 2], "CI_RECIPE plants exactly two blocks"

    # Every trial in the block synth/timeline.py actually assigned it to --
    # not merely "every trial landed in some block".
    block_trial_pairs = {
        (row["block_id"], row["trial_id"])
        for row in (pipeline.trial.BlockTrial & session_key).to_dicts()
    }
    assert block_trial_pairs == {(1, 1), (1, 2), (1, 3), (2, 4)}

    # Every trial's outcome is TRIAL_CORRECT -- the only outcome
    # synth/timeline.py ever emits -- so TrialType carries exactly that.
    outcomes = set((pipeline.trial.Trial & session_key).to_arrays("trial_type"))
    assert outcomes == {"correct"}

    # The full decoded stream landed in Event, escape payloads included --
    # not only the Marker subset that feeds trial/block assembly.
    event_types = set((pipeline.event.Event & session_key).to_arrays("event_type"))
    assert {"SESSION_START", "SESSION_END", "TRIAL_START", "BLOCK_END",
            "TRIAL_CORRECT", "TRIAL_NUMBER", "BLOCK_START"} <= event_types

    # BLOCK_START's own payload (block_id, task_type) reached Event.Attribute
    # as scalars, and the measured Block row also carries task_type, per
    # block.
    block_start_task_types = sorted(
        int(value)
        for value in (
            pipeline.event.Event.Attribute
            & session_key
            & {"attribute_name": "task_type"}
        ).to_arrays("attribute_value")
    )
    assert block_start_task_types == sorted(int(block.task_type) for block in recipe.blocks)

    block_task_types = {
        row["block_id"]: row["attribute_value"]
        for row in (
            pipeline.trial.Block.Attribute & session_key & {"attribute_name": "task_type"}
        ).to_dicts()
    }
    assert block_task_types == {
        1: str(int(recipe.blocks[0].task_type)),
        2: str(int(recipe.blocks[1].task_type)),
    }


def test_populate_session_is_idempotent(ci_session):
    """Calling it twice for the same session must not raise and must not
    duplicate rows -- every insert carries skip_duplicates=True so a retried
    ingest, or a second daemon pass, does not fail or double the trial list.
    """
    from wl_preproc.schema import pipeline

    session_key, session_dir, _recipe = ci_session

    events.populate_session(session_key, session_dir)
    events.populate_session(session_key, session_dir)

    assert len(pipeline.trial.Trial & session_key) == 4
    assert len(pipeline.trial.Block & session_key) == 2
    assert len(pipeline.event.BehaviorRecording & session_key) == 1


def test_no_array_is_ever_written_to_event_attribute_blob(ci_session):
    """Constraint: scalars only into Event. attribute_blob is never touched by
    this module -- every attribute row it builds carries a stringified scalar
    in attribute_value, and attribute_blob is left NULL throughout."""
    from wl_preproc.schema import pipeline

    session_key, session_dir, _recipe = ci_session

    events.populate_session(session_key, session_dir)

    blobs = (pipeline.event.Event.Attribute & session_key).to_arrays("attribute_blob")
    assert all(value is None for value in blobs)
