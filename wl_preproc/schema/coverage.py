# wl_preproc/schema/coverage.py
"""Per-run and per-trial coverage, one row per system.

Section 5.2.1: a stretch partially covered by a probe is the state that
matters -- it is what excludes it from a sort, and since design spec
`2026-10-01-session-listing-and-run-requests-design.md` the stretch a file is
built from is a measured run. So `partial` is a first-class state, never
collapsed into `absent`.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.schema import DEFAULT_PREFIX, core, pipeline

schema = dj.Schema()

_COVERAGE_ENUM = "enum('full','partial','absent')"


@schema
class RunCoverage(dj.Computed):
    definition = f"""
    # How much of one measured run each system recorded (design spec
    # 2026-10-01-session-listing-and-run-requests-design.md section 5), which
    # a file's readiness waits on. It replaced BlockCoverage, which did the
    # same for wl.works' asserted blocks. The intersection of a run's
    # interval with this system's segment extents.
    # Key: (subject, session_datetime, run_number, system).
    -> core.Run
    -> core.AcquisitionSystem
    ---
    coverage  : {_COVERAGE_ENUM}
    covered_s : double  # seconds of the run this system actually recorded
    """

    @property
    def key_source(self):
        """Every (run, system) pair of a session, whatever each system did.

        The cross product rather than a join through `Segment`: a system that
        recorded NONE of a run still needs a row saying `absent`, and joining
        through segments would silently omit exactly the systems whose absence
        matters most. Section 5.2.1 is about stretches a system did not fully
        cover; a missing row is not the same statement as `absent`.
        """
        return core.Run * core.AcquisitionSystem

    def make(self, key: dict) -> None:
        """Intersect this run's measured interval with this system's segment
        extents, by `timebase/coverage.py`'s one rule. A run that faulted at
        once can have no length: it covers nothing, and is `absent` rather
        than a failure on every pass.

        Both sides are ours and already in session time: `core.Run` is
        decoded from the event codes, and `Segment.start_s`/`end_s` carry each
        recording's extent after its own offset fit, so no conversion happens
        here.
        """
        from wl_preproc.timebase.coverage import classify_coverage

        run = (core.Run & key).fetch1("run_start_time", "run_stop_time")
        if run[1] <= run[0]:
            self.insert1({**key, "coverage": "absent", "covered_s": 0.0})
            return
        extents = [
            (row["start_s"], row["end_s"])
            for row in (core.Segment & key).to_dicts()
        ]
        state, covered_s = classify_coverage(run, extents)
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

        The cross product, mirroring `RunCoverage.key_source` exactly and
        for the identical reason: a system that recorded NONE of a trial still
        needs a row saying `absent`, and joining through `Segment` would
        silently omit exactly the systems whose absence matters most.
        """
        return pipeline.trial.Trial * core.AcquisitionSystem

    def make(self, key: dict) -> None:
        """Intersect this trial's interval with this system's segment extents.

        The same rule `RunCoverage.make()` calls -- `timebase/coverage.py`'s
        own docstring names this table as a caller, "so the rule has one
        definition rather than one per table." Nothing here reimplements it.

        `trial.Trial.trial_stop_time` is not nullable (1c-5 Task 8: the
        synthetic generator now emits `Marker.TRIAL_END` for every trial, so no
        trial comes back with `end_s=None`), so a zero-or-negative-duration
        trial is not papered over here either -- `classify_coverage` raises,
        and that is surfaced rather than caught. (`RunCoverage.make()` answers
        a zero-length run itself, because a faulted run can have one.)
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
