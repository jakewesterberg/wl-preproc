# tests/schema/test_coverage.py
import pytest

@pytest.fixture(scope="module")
def cov(dj_conn, prefix):
    from wl_preproc.schema import core, coverage, pipeline

    pipeline.activate(prefix=prefix)
    core.activate(prefix=prefix)
    coverage.activate(prefix=prefix)
    return coverage


def test_block_coverage_is_per_block_per_system(cov):
    assert set(cov.BlockCoverage.primary_key) == {
        "subject",
        "session_datetime",
        "block_id",
        "system",
    }


def test_trial_coverage_is_per_trial_per_system(cov):
    assert set(cov.TrialCoverage.primary_key) == {
        "subject",
        "session_datetime",
        "trial_id",
        "system",
    }


def test_trial_coverage_populates_a_row_for_every_trial_and_system(
    cov, dj_conn, prefix, tmp_path
):
    """1c-5 Task 9: `TrialCoverage.make()` mirrors `BlockCoverage.make()`
    exactly, calling the same `timebase.coverage.classify_coverage` rather
    than a second interval rule (`tests/timebase/test_coverage_rules.py::
    test_block_coverage_populates_a_row_for_every_block_and_system` is this
    test's own sibling, for the other table).

    **What this fixture actually produces, checked rather than assumed:**
    the `ci` recipe's `syncbox` segment is `[-1.0, 14.2]` -- its own
    `core.Segment` reflects only its BARCODE emissions (`timebase/extract.py::
    extract_syncbox` reads barcode entries, not code words), and the last
    barcode is emitted at `floor(duration_s - epsilon)` rather than at
    `duration_s` itself, so it falls about 0.8s short of the session's own
    end at 15.0s. `spikeglx` and `bcam`, by contrast, size their own buffers
    from `code_word_span_s`/`camera_frame_count`, both of which reach past
    15.0s. So of the 4 trials (3 RF_MAP + 1 RESTING_DARK), the first three
    (ending at 2.999s, 5.999s, 8.999s -- all short of 14.2) are `"full"` for
    every system, and the fourth (`[9.005, 14.999)`) is `"full"` for
    `spikeglx`/`bcam` but `"partial"` for `syncbox` specifically -- covering
    only `[9.005, 14.2]` of it. That is a real, deterministic property of this
    fixture, not a fault to paper over: it is exactly `partial`, the
    first-class state section 5.2.1 exists for, produced honestly rather than
    assumed to be `full` everywhere. Verified directly against a live run
    before writing this assertion, rather than guessed.
    """
    import datetime

    from wl_preproc.schema import core, events, ingest, pipeline, timebase
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session

    ingest.activate(prefix=prefix)
    timebase.activate(prefix=prefix)
    events.activate(prefix=prefix)

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
        "session_datetime": datetime.datetime(2027, 3, 24, 9, 0),
    }
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    ingest.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": datetime.datetime(2027, 3, 24, 19, 0),
            "session_dir": str(session_dir),
            "integrity": "verified",
            "topology": {system: "present" for system in recipe.systems},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    core.AcquisitionSystem.insert(
        [{**session_key, "system": system} for system in recipe.systems],
        skip_duplicates=True,
    )

    timebase.SystemTimebase.populate()
    core.Segment.populate()
    events.populate_session(session_key, session_dir)
    cov.TrialCoverage.populate()

    rows = (cov.TrialCoverage & session_key).to_dicts()
    assert len(rows) == 4 * len(recipe.systems)

    by_trial_system = {(row["trial_id"], row["system"]): row for row in rows}
    for trial_id in (1, 2, 3):
        for system in recipe.systems:
            row = by_trial_system[(trial_id, system)]
            assert row["coverage"] == "full", row
            assert row["covered_s"] > 0.0

    assert by_trial_system[(4, "spikeglx")]["coverage"] == "full"
    assert by_trial_system[(4, "bcam")]["coverage"] == "full"
    # `partial`, not `absent`: syncbox's own segment still covers most of
    # this trial ([9.005, 14.2] of [9.005, 14.999)) -- collapsing that into
    # `absent` is exactly the mistake section 5.2.1 warns against.
    last_syncbox = by_trial_system[(4, "syncbox")]
    assert last_syncbox["coverage"] == "partial", last_syncbox
    assert 0.0 < last_syncbox["covered_s"] < 5.994


def test_coverage_states_are_exactly_full_partial_absent(cov, enum_values):
    """Section 5.2.1: a block partially covered by a probe is the state that
    matters, so `partial` must be representable and distinct from `absent`.

    "Exactly", as the name says, so the enum is parsed and compared as a SET in
    both directions. Until 2026-08-14 this looped `assert state in declared`
    over the raw declaration string, which `enum('fullx','partialx','absentx')`
    satisfies, and which a fourth state added later — the thing that would
    actually collapse `partial` back into a spectrum — could not fail.

    Both coverage tables are checked, not just BlockCoverage: they share
    `_COVERAGE_ENUM` today and that is exactly the assumption worth pinning.
    """
    expected = {"full", "partial", "absent"}
    for table in (cov.BlockCoverage, cov.TrialCoverage):
        declared = table.heading["coverage"].type
        assert enum_values(declared) == expected, (
            f"{table.__name__}.coverage declares {enum_values(declared)}, "
            f"not exactly {expected}"
        )
