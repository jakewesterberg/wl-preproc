# wl_preproc/schema/eye.py
"""How each session's eye calibration was obtained, and its quality.

Design spec `docs/superpowers/specs/2026-08-30-eye-ohdpi-calibration-and-gaze-
design.md` section 6. Calibration is fitted from this session's own targets,
borrowed from MonkeyLogic, carried forward from another session the same day,
or refused (section 3.5's chain, `wl_preproc.eye.calibration.
CalibrationSource`). A refused calibration is a first-class outcome with a
stated reason -- not an error, and never a fabricated map -- which is why the
six affine parameters below are nullable rather than the row simply not
existing: "we could not calibrate this session, and here is why" has to be
expressible, the same discipline `schema/archive.py`'s own "not checked" vs.
"checked, nothing found" distinction protects.

Neither table below defines `make()`. This module is schema only -- the real
`key_source` (section 6: "the session restricted to those with an ohDPI
recording and assembled events") and the population logic itself are later
work. See each class's own `key_source` for what that leaves safe to register
as a daemon stage today, and what it does not yet do.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.schema import DEFAULT_PREFIX, pipeline

schema = dj.Schema()


@schema
class EyeCalibration(dj.Computed):
    definition = """
    # Raw Purkinje geometry to degrees, per eye. Design spec section 6.
    # Key: (subject, session_datetime, eye).
    -> pipeline.Session
    eye : enum('left','right')
    ---
    # Which step of section 3.5's chain produced this map. A borrowed map must
    # never be mistaken for a fitted one.
    calibration_source : enum('fitted','monkeylogic','carried_forward','refused')
    # The six affine parameters, NULL when refused. A refused calibration is a
    # first-class outcome with a stated reason -- not an error, and never a
    # fabricated map.
    a00=null : double
    a01=null : double
    b0=null  : double
    a10=null : double
    a11=null : double
    b1=null  : double
    # Where this session's own fixation lands under the accepted map. Populated
    # for EVERY source including 'fitted', because it is the one number
    # comparable across all four.
    validation_error_deg=null : double
    n_points                    : int unsigned
    n_from_calibration_block    : int unsigned
    n_from_task_fixation        : int unsigned
    # The target constellation's minor/major spread ratio (`wl_preproc.eye.
    # calibration._conditioning`), which section 3.5's refusal is keyed on.
    conditioning=null           : double
    # A fitted map only: how well it explains the points it was fitted from.
    residual_deg_rms=null       : double
    residual_deg_max=null       : double
    carried_from_session_datetime=null : datetime
    reason=''                   : varchar(255)
    """

    class BlockResidual(dj.Part):
        definition = """
        # Per-block residual. Section 3.6 measures drift here rather than
        # correcting it: over a ~40 minute session, drift appears as a residual
        # that grows, at no additional cost and without pre-empting the
        # decision to correct it.
        # Key: (subject, session_datetime, eye, block_id).
        -> master
        -> pipeline.trial.Block
        ---
        n_points         : int unsigned
        residual_deg_rms : double
        """

    @property
    def key_source(self):
        """Empty, deliberately -- this task defines the schema, not `make()`.

        DataJoint's default `key_source` for a Computed table is the join of
        its FK parents projected to their primary key. Here that is bare
        `pipeline.Session` -- the extra `eye` attribute is native to this
        table, not inherited through a foreign key, so nothing supplies which
        values of it to iterate over. Left at that default, with no `make()`
        defined, `daemon.run_once()` populating this table (required so it can
        be registered in `daemon._computed_tables()` at all) would call
        `.populate()` against every session already landed anywhere in the
        process -- the whole point of registering it -- and hit
        `dj.AutoPopulate`'s own base `make()`, which raises
        `NotImplementedError` unconditionally for each one. Verified directly
        against `datajoint/autopopulate.py` at this project's pinned 2.3.2,
        and against a live `daemon.run_once()` pass over this suite's shared
        database (`tests/schema/test_eye_schema.py::
        test_registering_them_does_not_break_a_clean_daemon_pass`).

        Design spec section 6 names the real restriction: "the session
        restricted to those with an ohDPI recording and assembled events,
        since calibration needs target positions from the decoded code
        stream." Computing that, and writing `make()` itself, is later work;
        an empty `key_source` here means a `daemon.run_once()` pass computes
        nothing for this table meanwhile, honestly, rather than raising on
        every session it can already see.
        """
        return pipeline.Session.proj() & "1=0"


@schema
class EyeQuality(dj.Computed):
    definition = """
    # Tracking-loss fraction and blink rate, per eye. Design spec section 6
    # (schema) and section 8 (the daily report's "tracking-loss percentage
    # and blink rate" line -- not parent design spec section 10, which has no
    # eye-quality line of its own: its own eye-related report item is
    # "eye-detector disagreement outliers", the saccade-detector agreement
    # metric section 11 places out of THIS spec's scope entirely).
    # Key: (subject, session_datetime, eye).
    -> pipeline.Session
    eye : enum('left','right')
    ---
    # From the file's own DataQuality column (0/50/100), so tracking loss is
    # STATED by the recording rather than inferred from missing values.
    tracking_loss_fraction : double
    blink_rate_hz          : double
    """

    @property
    def key_source(self):
        """Empty, for the identical reason `EyeCalibration.key_source` is --
        see its own docstring. Not the same restriction by construction: this
        table's own real `key_source` is a later task's decision, not
        necessarily identical to `EyeCalibration`'s (tracking loss and blink
        rate need only an ohDPI recording, not assembled events)."""
        return pipeline.Session.proj() & "1=0"


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}eye`. Idempotent."""
    pipeline.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}eye", create_tables=True)
