"""Coverage as interval intersection.

Named `test_coverage_rules` rather than `test_coverage`: `tests/schema/` already
has a `test_coverage.py`, the test directories are not packages, and pytest
cannot tell two same-named modules apart — it fails collection for the WHOLE
suite rather than for the one file. The plan named this `test_coverage.py`.

Section 5.2.1: a block partially covered by a probe is the state that matters —
it is what wl.works asserts `block_neural_assertion` against, and what excludes
a block from a sort. So `partial` is a first-class state, never collapsed into
`absent`.
"""

import pytest

from wl_preproc.timebase.coverage import classify_coverage


@pytest.mark.parametrize(
    "block, segments, expected_state, expected_s",
    [
        ((0.0, 10.0), [(0.0, 10.0)], "full", 10.0),
        ((0.0, 10.0), [(0.0, 6.0)], "partial", 6.0),
        ((0.0, 10.0), [], "absent", 0.0),
        ((0.0, 10.0), [(0.0, 4.0), (6.0, 10.0)], "partial", 8.0),  # a gap in the middle
        ((0.0, 10.0), [(0.0, 5.0), (4.0, 10.0)], "full", 10.0),  # overlapping, not doubled
        ((0.0, 10.0), [(-5.0, 15.0)], "full", 10.0),  # clipped to the block
    ],
)
def test_coverage_classification(block, segments, expected_state, expected_s):
    """`partial` is never collapsed into `absent`. Spec section 4.6: a recording
    that stopped mid-trial must never be silently treated as complete.

    Overlapping segments must not double-count, and segments extending past the
    block must clip — otherwise covered_s can exceed the block's own duration
    and `full` becomes unreachable by comparison.
    """
    state, covered_s = classify_coverage(block, segments)
    assert state == expected_state
    assert covered_s == pytest.approx(expected_s)


def test_covered_s_never_exceeds_the_block_duration():
    _, covered_s = classify_coverage((0.0, 10.0), [(-100.0, 100.0), (0.0, 10.0)])
    assert covered_s <= 10.0


def test_a_segment_entirely_outside_the_block_covers_none_of_it():
    """A system that recorded a different part of the session covers this block
    zero, and must read `absent` — not `partial` on the strength of having a
    segment at all."""
    state, covered_s = classify_coverage((10.0, 20.0), [(0.0, 5.0), (25.0, 30.0)])
    assert state == "absent"
    assert covered_s == pytest.approx(0.0)


def test_a_segment_touching_the_block_edge_covers_nothing():
    """Half-open at the boundary: a segment ending exactly where the block
    begins shares an instant, not an interval. Counting it would make `partial`
    reachable by a recording that captured none of the block."""
    state, covered_s = classify_coverage((10.0, 20.0), [(5.0, 10.0)])
    assert state == "absent"
    assert covered_s == pytest.approx(0.0)


def test_nearly_full_coverage_is_partial_not_full():
    """The distinction this table exists for. A block missing its last 50 ms is
    not a block that was recorded — 5.2.1 says it is what excludes a block from
    a sort, so rounding it up to `full` silently readmits it."""
    state, covered_s = classify_coverage((0.0, 10.0), [(0.0, 9.95)])
    assert state == "partial"
    assert covered_s == pytest.approx(9.95)


def test_a_zero_length_block_is_refused_rather_than_divided_by():
    """Coverage is a fraction of the block's duration, so a zero-length block
    has none. Returning `full` (nothing missing) or `absent` (nothing covered)
    are both defensible and both wrong: the row is malformed, and wl.works
    authored it."""
    with pytest.raises(ValueError, match="zero"):
        classify_coverage((10.0, 10.0), [(0.0, 20.0)])


def test_run_coverage_populates_a_row_for_every_run_and_system(dj_conn, prefix, tmp_path):
    """The cross product, not a join through Segment, so a system that
    recorded none of a run says `absent`: a missing row is not the same
    statement. A run with no length, one that faulted at once, is
    `absent` too, never a failure on every pass (design spec
    `2026-10-01-session-listing-and-run-requests-design.md` section 5)."""
    import datetime

    from wl_preproc.schema import core, coverage, ingest, pipeline, timebase
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session

    coverage.activate(prefix=prefix)
    timebase.activate(prefix=prefix)
    ingest.activate(prefix=prefix)

    recipe = RECIPES["drift"]
    generate_session(tmp_path, recipe)
    pipeline.lab.Lab.insert1({"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
                             skip_duplicates=True)
    pipeline.subject.Subject.insert1({"subject": recipe.subject, "sex": "M",
                                      "subject_birth_date": datetime.date(2020, 1, 1), "subject_description": ""},
                                     skip_duplicates=True)
    session_key = {"subject": recipe.subject, "session_datetime": datetime.datetime(2027, 7, 26, 9, 0)}
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    ingest.Ingestion.insert1({**session_key, "ingested_at": datetime.datetime(2027, 7, 26, 19, 0),
                              "session_dir": str(tmp_path / recipe.session_id), "integrity": "verified",
                              "topology": {system: "present" for system in recipe.systems},
                              "manifest_hash": "blake3:test"}, skip_duplicates=True)
    core.AcquisitionSystem.insert([{**session_key, "system": system} for system in recipe.systems],
                                  skip_duplicates=True)
    core.Run.insert([
        {**session_key, "run_number": 1, "task_type": 0, "run_start_time": 0.0, "run_stop_time": 10.0, "closed": 1},
        {**session_key, "run_number": 2, "task_type": 0, "run_start_time": 1_000.0, "run_stop_time": 1_010.0,
         "closed": 1},
        {**session_key, "run_number": 3, "task_type": 0, "run_start_time": 5.0, "run_stop_time": 5.0, "closed": 0},
    ], skip_duplicates=True)

    timebase.SystemTimebase.populate()
    core.Segment.populate()
    coverage.RunCoverage.populate()

    rows = (coverage.RunCoverage & session_key).to_dicts()
    assert len(rows) == 3 * len(recipe.systems)
    by_run = {number: {row["system"]: row["coverage"] for row in rows if row["run_number"] == number}
              for number in (1, 2, 3)}
    assert by_run[1] == {system: "full" for system in recipe.systems}
    assert by_run[2] == by_run[3] == {system: "absent" for system in recipe.systems}
