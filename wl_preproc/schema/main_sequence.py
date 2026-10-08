# wl_preproc/schema/main_sequence.py
"""`SaccadeMainSequence`: each detection trace's main-sequence fit, and each
block's and condition's gain against it (design spec
`docs/superpowers/specs/2026-10-07-main-sequence-design.md` section 3).

What is fitted, and how, is `eye/detect/main_sequence.py`'s. This module picks
the stored runs, places them in blocks and conditions, and stores what comes
back. Vigor is never stored: `cli/report.py` computes it from these rows,
because the history it is measured against grows with every session.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import datajoint as dj
import numpy as np

from wl_preproc.eye.detect.main_sequence import (
    DEFAULT_MAIN_SEQUENCE_PARAMS,
    MainSequenceParams,
    fit_session,
    selected,
)
from wl_preproc.schema import DEFAULT_PREFIX, detect, paramset, pipeline

schema = dj.Schema()

#: The width of `reason`, and of `.Condition`'s `condition`: a longer reason
#: is cut to it, and a longer condition name is no condition.
_VARCHAR_LEN = 255


@schema
class SaccadeMainSequence(dj.Computed):
    definition = """
    # One detection trace's main sequence: its saturating fit, or why there is none.
    # Key: (subject, session_datetime, trace, validity_paramset_type,
    # validity_paramset_idx, paramset_type, paramset_idx, fit_paramset_type,
    # fit_paramset_idx).
    -> detect.EyeDetection
    # Both columns renamed, not only the index: `EyeDetection`'s own
    # definition records what a bare `paramset_type` shared by two references
    # to one table does.
    -> paramset.ParamSet.proj(fit_paramset_type='paramset_type', fit_paramset_idx='paramset_idx')
    ---
    fit_status : enum('computed','refused')
    # The rate durations were counted at, as `EyeDetection.make` counts them
    # (`read_ohdpi(...).fs_hz`), so the report picks exactly the saccades the
    # fit picked. NULL where this trace's detection was refused.
    fs_hz=null             : double
    n_saccades             : int unsigned
    amplitude_min_deg=null : double
    amplitude_max_deg=null : double
    # peak_velocity = v_max * (1 - exp(-amplitude / saturation)); NULL when refused.
    v_max_deg_s=null       : double
    saturation_deg=null    : double
    v_max_se_deg_s=null    : double
    saturation_se_deg=null : double
    # On peak velocity, not its logarithm.
    r_squared=null         : double
    reason=''              : varchar(255)
    """

    class Block(dj.Part):
        definition = """
        # One block's gain against its session's fit: the median of its
        # saccades' peak speed over the fit's prediction for their size.
        # Key: the master's, and block_id.
        -> master
        -> pipeline.trial.Block
        ---
        gain_status            : enum('computed','refused')
        n_saccades             : int unsigned
        amplitude_min_deg=null : double
        amplitude_max_deg=null : double
        gain=null              : double
        reason=''              : varchar(255)
        """

    class Condition(dj.Part):
        definition = """
        # One condition's gain within one block, as `.Block`'s.
        # Key: the master's, block_id, and condition.
        -> master
        -> pipeline.trial.Block
        # The rig record's name, else the stream's CONDITION number as text.
        condition : varchar(255)
        ---
        gain_status            : enum('computed','refused')
        n_saccades             : int unsigned
        amplitude_min_deg=null : double
        amplitude_max_deg=null : double
        gain=null              : double
        reason=''              : varchar(255)
        """

    @property
    def key_source(self):
        """Every `EyeDetection` row, refused ones included, times every
        `main_sequence` paramset: one key per trace.

        **Not collapsed over `trace`** as `EyeDetection.key_source` collapses
        over `eye`. DataJoint 2.3 keys a table's job queue on every
        primary-key attribute inherited through a foreign key, and `trace`
        comes here through `-> detect.EyeDetection`; with it missing from
        this `key_source`, the daemon's pass wrote the left trace alone
        (design spec amendment 2).

        No events requirement is needed: `EyeDetection` needs `EyeValidity`,
        which needs `EyeCalibration` to have run, and `EyeCalibration.
        key_source` requires `pipeline.event.BehaviorRecording`. A session's
        blocks and trials are assembled before its first key appears, which
        matters because a populated key is never revisited."""
        fits = (paramset.ParamSet & {"paramset_type": "main_sequence"}).proj(
            fit_paramset_type="paramset_type", fit_paramset_idx="paramset_idx")
        return detect.EyeDetection.proj() * fits

    def make(self, key: dict) -> None:
        """One trace's fit, or its refusal. A trace whose detection was
        refused gets a refused row quoting it."""
        from wl_preproc.eye.ohdpi import read_ohdpi
        from wl_preproc.schema import core, ingest

        params = MainSequenceParams(**(paramset.ParamSet & {
            "paramset_type": key["fit_paramset_type"], "paramset_idx": key["fit_paramset_idx"],
        }).fetch1("params"))
        detection_key = {name: key[name] for name in detect.EyeDetection.primary_key}
        detection = (detect.EyeDetection & detection_key).fetch1()
        if detection["status"] == "refused":
            self.insert1({**key, "fit_status": "refused", "n_saccades": 0,
                          "reason": f"detection refused: {detection['reason']}"[:_VARCHAR_LEN]})
            return

        session_key = {name: key[name] for name in pipeline.Session.primary_key}
        session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
        segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
        fs_hz = read_ohdpi(session_dir / "ohdpi" / segment["file_path"]).fs_hz
        runs = selected_runs((detect.EyeDetection.Run & detection_key & 'label in ("saccade", "microsaccade")')
                             .to_dicts(order_by="run_index"), fs_hz, params)
        fit = fit_session([run["amplitude_deg"] for run in runs], [run["peak_velocity_deg_s"] for run in runs],
                          params)
        curve = fit.curve
        self.insert1({
            **key, "fit_status": "refused" if curve is None else "computed", "fs_hz": fs_hz,
            "n_saccades": fit.n_saccades, "amplitude_min_deg": fit.amplitude_min_deg,
            "amplitude_max_deg": fit.amplitude_max_deg,
            "v_max_deg_s": None if curve is None else curve.v_max_deg_s,
            "saturation_deg": None if curve is None else curve.saturation_deg,
            "v_max_se_deg_s": fit.v_max_se_deg_s, "saturation_se_deg": fit.saturation_se_deg,
            "r_squared": fit.r_squared, "reason": fit.reason,
        })


def selected_runs(runs: list[dict], fs_hz: float, params: MainSequenceParams) -> list[dict]:
    """The `EyeDetection.Run` rows a fit takes (`main_sequence.selected`),
    the one place the stored columns meet that rule: `make()` and the report
    both pick saccades through here."""
    if not runs:
        return []
    take = selected(
        [run["label"] for run in runs],
        [np.nan if run["amplitude_deg"] is None else run["amplitude_deg"] for run in runs],
        [run["run_stop"] - run["run_start"] for run in runs],
        fs_hz, params,
    )
    return [run for run, taken in zip(runs, take, strict=True) if taken]


def register_default_paramsets() -> dict[str, int]:
    """The default `main_sequence` paramset (spec section 4.4), by name."""
    return {"default": paramset.register("main_sequence", asdict(DEFAULT_MAIN_SEQUENCE_PARAMS))}


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind this table to `{prefix}main_sequence`. Idempotent."""
    detect.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}main_sequence", create_tables=True)
