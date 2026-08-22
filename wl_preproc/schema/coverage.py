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


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}coverage`. Idempotent."""
    core.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}coverage", create_tables=True)
