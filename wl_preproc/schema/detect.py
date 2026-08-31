# wl_preproc/schema/detect.py
"""The validity mask and detected events, stored as runs.

Design spec `docs/superpowers/specs/2026-08-31-saccade-detection-design.md`.

**The first stored derived array in this pipeline, and it is not a blob.** A
per-sample label trace is piecewise constant, so it is stored as maximal runs
in rows: the same information losslessly, the guardrail satisfied by
construction rather than by a round-trip test someone must remember, "total
microsaccade time this month" as a WHERE clause, and a tiling invariant a blob
cannot have.

**Task 6: both tables ship schema-only, with a permanently empty
`key_source`.** `make()` is Task 7. This is deliberate, not a placeholder
forgotten in place -- `wl_preproc/schema/eye.py`'s own git history (Task 9,
commit 3a8a121) shows the identical shape and states the cost of skipping it:
a `dj.Computed` with a real `key_source` and no `make()` raises
`NotImplementedError` (`dj.AutoPopulate`'s own unconditional base `make()`)
the moment `daemon.run_once()` reaches it, for every session already landed
in the process -- confirmed there directly against `datajoint/
autopopulate.py` at this project's pinned 2.3.2. An empty `key_source` is
what keeps registering these two tables in `daemon._computed_tables()`
harmless in the meantime, exactly as it did for `eye.EyeCalibration`/
`eye.EyeQuality` before Task 10 gave both a real restriction and a real
`make()`.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.eye.detect.labels import Label
from wl_preproc.schema import DEFAULT_PREFIX, core, paramset, pipeline

schema = dj.Schema()

_LABEL_ENUM = ",".join(f"'{label.value}'" for label in Label)


@schema
class EyeValidity(dj.Computed):
    definition = f"""
    # Which samples are usable, per eye. Design spec section 2.
    # Key: (subject, session_datetime, eye, paramset_type, validity_paramset_idx).
    -> pipeline.Session
    eye : enum('left','right')
    -> paramset.ParamSet.proj(validity_paramset_idx='paramset_idx')
    ---
    # Read this before any column below: a refused mask has no runs and a
    # stated reason, exactly as a refused calibration has no map.
    status : enum('computed','refused')
    n_samples=null : int unsigned
    # Per-criterion bookkeeping, so a mask that rejects most of a session says
    # WHICH criterion did it rather than only that something did. The five
    # match OpenIrisDPI's own five (design spec section 2): eye open, gaze
    # within a plausible region, plausible speed, no frame discontinuity, and
    # short surviving epochs dropped.
    frac_blink=null         : double
    frac_out_of_region=null : double
    frac_too_fast=null      : double
    frac_frame_gap=null     : double
    frac_short_epoch=null   : double
    reason='' : varchar(255)
    """

    class Run(dj.Part):
        definition = f"""
        # One maximal stretch of a single mask label. `run_stop` is EXCLUSIVE.
        # Key: (subject, session_datetime, eye, paramset_type,
        # validity_paramset_idx, run_index).
        -> master
        run_index : int unsigned
        ---
        run_start : int unsigned
        run_stop  : int unsigned
        label     : enum({_LABEL_ENUM})
        """

    @property
    def key_source(self):
        """Empty until Task 7 gives this table a `make()`.

        Deliberate, and the eye subsystem records the cost of the
        alternative: a `dj.Computed` with a real `key_source` and no
        `make()` raises on every already-landed session the moment it joins
        `daemon._computed_tables()`. See this module's own docstring.
        """
        return pipeline.Session & "FALSE"


@schema
class EyeDetection(dj.Computed):
    definition = f"""
    # Detected events as a label trace, per trace and per detector.
    # Key: (subject, session_datetime, trace, validity_paramset_type,
    # validity_paramset_idx, paramset_type, paramset_idx).
    -> pipeline.Session
    # `trace`, not `eye`: a conjunction is honestly not an eye.
    trace : enum('left','right','conjunction')
    # BOTH of ParamSet's own primary-key columns are renamed here, not only
    # `paramset_idx`. Renaming only `paramset_idx` and leaving `paramset_type`
    # bare on this line -- while the DETECTOR reference below also declares a
    # bare `paramset_type` -- does not raise at declaration time (confirmed
    # directly against a live MySQL 8, this project's pinned DataJoint
    # 2.3.2): DataJoint silently treats the unrenamed `paramset_type` as ONE
    # SHARED COLUMN feeding both foreign keys, so `primary_key` still lists
    # `validity_paramset_idx` and a declaration-only check would stay green.
    # Reproduced directly: inserting a row naming two DIFFERENT
    # `paramset_type` values through the two references raises
    # `IntegrityError`, because the shared column cannot equal both at once.
    # Renaming both columns here makes the two references independent
    # physical columns -- what a validity-mask paramset (`eye_validity`,
    # design spec section 2: "its own eye_validity paramset, rather than
    # living inside each detector's paramset") and a detector paramset
    # actually are: unrelated vocabularies that happen to share one lookup
    # table, never required to name the same `paramset_type` string.
    -> paramset.ParamSet.proj(validity_paramset_type='paramset_type', validity_paramset_idx='paramset_idx')
    -> paramset.ParamSet
    ---
    status : enum('computed','refused')
    n_samples=null       : int unsigned
    n_saccades=null      : int unsigned
    n_microsaccades=null : int unsigned
    reason='' : varchar(255)
    """

    class Run(dj.Part):
        definition = f"""
        # One maximal stretch of a single label. `run_stop` is EXCLUSIVE, and
        # the runs of one master row tile [0, n_samples) exactly.
        # Key: (subject, session_datetime, trace, validity_paramset_type,
        # validity_paramset_idx, paramset_type, paramset_idx, run_index).
        -> master
        run_index : int unsigned
        ---
        run_start : int unsigned
        run_stop  : int unsigned
        label     : enum({_LABEL_ENUM})
        # A saccade or microsaccade run IS an event, so it carries its own
        # measurements; every other label leaves them NULL. `reliability` is
        # Otero-Millan's per-detection index, null for every detector that has
        # none -- declared now because the migration window closes January.
        amplitude_deg=null       : double
        peak_velocity_deg_s=null : double
        reliability=null         : double
        """

    @property
    def key_source(self):
        """Empty until Task 7. See `EyeValidity.key_source`."""
        return pipeline.Session & "FALSE"


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}detect`. Idempotent."""
    core.activate(prefix=prefix)
    paramset.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}detect", create_tables=True)
