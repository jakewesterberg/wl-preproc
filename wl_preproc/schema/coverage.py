# wl_preproc/schema/coverage.py
"""Per-trial and per-block coverage, one row per system.

Section 5.2.1: a block partially covered by a probe is the state that matters —
it is what wl.works asserts block_neural_assertion against, and what excludes a
block from a sort. So `partial` is a first-class state, never collapsed into
`absent`.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.schema import DEFAULT_PREFIX, core, pipeline

schema = dj.Schema()

_COVERAGE_ENUM = "enum('full','partial','absent')"


@schema
class BlockCoverage(dj.Computed):
    definition = f"""
    # Coverage of one block by one system. Computed, not Manual: declared
    # Manual in 1c-1 when nothing computed it, and it is the intersection of a
    # block's interval with this system's segment extents.
    # Key: (subject, session_datetime, block_id, system).
    -> core.Block
    -> core.AcquisitionSystem
    ---
    coverage  : {_COVERAGE_ENUM}
    covered_s : double  # seconds of the block this system actually recorded
    """

    @property
    def key_source(self):
        """Every (block, system) pair of a session, whatever each system did.

        The cross product rather than a join through `Segment`: a system that
        recorded NONE of a block still needs a row saying `absent`, and joining
        through segments would silently omit exactly the systems whose absence
        matters most. Section 5.2.1 is about blocks a system did not fully
        cover; a missing row is not the same statement as `absent`.
        """
        return core.Block * core.AcquisitionSystem

    def make(self, key: dict) -> None:
        """Intersect this block's interval with this system's segment extents.

        **`Block.start_s`/`end_s` are wl.works' assertion, not our
        measurement.** Closed open item 9: block rows are authored by wl.works'
        session planner and wl-preproc never writes them. The MEASURED boundary
        is a different quantity, decoded from event codes, and belongs to 1c-5
        in its own table. A reader who assumes these boundaries were decoded
        here will misread every row in this table, which is why it is said at
        the point where they are read rather than only in the spec.

        The segment extents ARE ours, and they are already in session time --
        `Segment.start_s`/`end_s` carry each recording's extent after its own
        offset fit, so no conversion happens here.
        """
        from wl_preproc.timebase.coverage import classify_coverage

        block = (core.Block & key).fetch1("start_s", "end_s")
        extents = [
            (row["start_s"], row["end_s"])
            for row in (core.Segment & key).to_dicts()
        ]
        state, covered_s = classify_coverage(block, extents)
        self.insert1({**key, "coverage": state, "covered_s": covered_s})


@schema
class TrialCoverage(dj.Computed):
    definition = f"""
    # Coverage of one trial by one system. Trial comes from element-event's
    # `trial` module — NOT its `event` module; they are separate.
    # Computed, not Manual. It converts in 1c-4 despite belonging to 1c-5,
    # because converting it later costs a migration and converting it now, with
    # no row anywhere, costs nothing.
    # Key: (subject, session_datetime, trial_id, system).
    -> pipeline.trial.Trial
    -> core.AcquisitionSystem
    ---
    coverage  : {_COVERAGE_ENUM}
    covered_s : double
    """

    @property
    def key_source(self):
        """Every (trial, system) pair of a session, whatever each system did.

        The cross product, mirroring `BlockCoverage.key_source` exactly and
        for the identical reason: a system that recorded NONE of a trial still
        needs a row saying `absent`, and joining through `Segment` would
        silently omit exactly the systems whose absence matters most.
        """
        return pipeline.trial.Trial * core.AcquisitionSystem

    def make(self, key: dict) -> None:
        """Intersect this trial's interval with this system's segment extents.

        The same rule `BlockCoverage.make()` calls -- `timebase/coverage.py`'s
        own docstring already names this table by name as the second caller,
        "so the rule has one definition rather than one per table." Nothing
        here reimplements it.

        `trial.Trial.trial_stop_time` is not nullable (1c-5 Task 8: the
        synthetic generator now emits `Marker.TRIAL_END` for every trial, so no
        trial comes back with `end_s=None`), so a zero-or-negative-duration
        trial is not papered over here either -- `classify_coverage` raises,
        and that is surfaced rather than caught, exactly as `BlockCoverage.
        make()` leaves it.
        """
        from wl_preproc.timebase.coverage import classify_coverage

        trial = (pipeline.trial.Trial & key).fetch1("trial_start_time", "trial_stop_time")
        extents = [
            (row["start_s"], row["end_s"])
            for row in (core.Segment & key).to_dicts()
        ]
        state, covered_s = classify_coverage(trial, extents)
        self.insert1({**key, "coverage": state, "covered_s": covered_s})


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}coverage`. Idempotent."""
    core.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}coverage", create_tables=True)
