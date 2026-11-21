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
        here. Each extent is cut to the covered span where the NI's barcodes
        placed the system (`_extents`, amendment 58).
        """
        from wl_preproc.timebase.coverage import classify_coverage

        run = (core.Run & key).fetch1("run_start_time", "run_stop_time")
        if run[1] <= run[0]:
            self.insert1({**key, "coverage": "absent", "covered_s": 0.0})
            return
        state, covered_s = classify_coverage(run, _extents(key))
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
        """Intersect this trial's interval with this system's segment extents,
        cut to the covered span as `RunCoverage.make()`'s are (`_extents`).

        The same rule `RunCoverage.make()` calls -- `timebase/coverage.py`'s
        own docstring names this table as a caller, "so the rule has one
        definition rather than one per table." Nothing here reimplements it.

        A trial's measured stop (`core.TrialTime.stop_s`) is not nullable (1c-5 Task 8: the
        synthetic generator now emits `Marker.TRIAL_END` for every trial, so no
        trial comes back with `end_s=None`), so a zero-or-negative-duration
        trial is not papered over here either -- `classify_coverage` raises,
        and that is surfaced rather than caught. (`RunCoverage.make()` answers
        a zero-length run itself, because a faulted run can have one.)
        """
        from wl_preproc.timebase.coverage import classify_coverage

        # The measured times, one query, as the per-trial key needs no more
        # (design spec 2026-11-17-full-precision-trial-times-design.md item 2).
        rows = (core.TrialTime & key).to_dicts()
        if not rows:
            raise core.MissingTimeRows(
                f"no measured time for {key['subject']} {key['session_datetime']}: trial {key['trial_id']}; the "
                "daemon does not re-run a session's event stage, so call schema.events.populate_session(key, "
                "session_dir) for it, then clear this table's errored jobs for the session")
        (row,) = rows
        trial = (row["start_s"], row["stop_s"])
        state, covered_s = classify_coverage(trial, _extents(key))
        self.insert1({**key, "coverage": state, "covered_s": covered_s})


def _extents(key: dict) -> list[tuple[float, float]]:
    """This system's segment extents in session time, each cut to the covered
    span where the NI's barcodes placed the system (amendment 58, ruling R5
    of part 3b-3).

    Under the NI with a sync box that stopped partway, the eye tracker's and
    the cameras' segments run on past the span, placed there by extrapolating
    their barcode fit, which nothing verified, so no stage uses those samples.
    Coverage says so: a run past the span reads those systems as not covered
    there, which is true of everything that reads them. A system the NI does
    not place by barcode, SpikeGLX itself or Intan on its strobes, keeps its
    extents, and under the sync box the span is the whole session."""
    from wl_preproc.eye.span import clip_to_span
    from wl_preproc.schema import timebase
    from wl_preproc.timebase.clock import clock_for

    extents = [(row["start_s"], row["end_s"]) for row in (core.Segment & key).to_dicts()]
    # Only the NI's clock has a span (`Clock.covered_span`), so a session the
    # sync box times never asks for one.
    clock = (timebase.SessionClock & key).to_dicts()
    if not extents or not clock or clock[0]["source"] != "nidq":
        return extents
    if (timebase.SystemTimebase & key).fetch1("time_source") != "barcode":
        return extents
    span = clock_for(key).covered_span()
    if span is None:
        return extents
    return [extent for extent in (clip_to_span(start_s, end_s, span) for start_s, end_s in extents)
            if extent is not None]


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}coverage`. Idempotent."""
    core.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}coverage", create_tables=True)
