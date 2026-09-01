# wl_preproc/schema/detect.py
"""The validity mask and detected events, stored as runs.

Design spec `docs/superpowers/specs/2026-08-31-saccade-detection-design.md`.

**The first stored derived array in this pipeline, and it is not a blob.** A
per-sample label trace is piecewise constant, so it is stored as maximal runs
in rows: the same information losslessly, the guardrail satisfied by
construction rather than by a round-trip test someone must remember, "total
microsaccade time this month" as a WHERE clause, and a tiling invariant a blob
cannot have.

**Task 7: both tables now define `make()`, and `EyeDetection` a real
`key_source`.** Task 6 left both empty on purpose -- a `dj.Computed` with a
real `key_source` and no `make()` raises `NotImplementedError`
(`dj.AutoPopulate`'s own unconditional base `make()`) the moment
`daemon.run_once()` reaches it, for every session already landed in the
process -- confirmed there directly against `datajoint/autopopulate.py` at
this project's pinned 2.3.2, and preserved in this file's own git history on
each class's former `key_source`. `EyeValidity.make()` reads a session's raw
ohDPI file once per session (not once per eye -- `read_ohdpi`'s `fs_hz`/
`frame_gaps` describe the FILE, not either eye's own gaze), computes each
eye's validity mask via `wl_preproc.eye.detect.validity.validity_labels`, and
writes a refused row with a stated reason rather than raising when a
session's calibration is unusable. `EyeDetection.make()` reads that mask
back, runs the registered detector over each eye independently, takes the
INTERSECTION of the two eyes' detected spans as the conjunction (Engbert &
Kliegl's own binocular noise-suppression criterion), and measures every
detected event once via `wl_preproc.eye.detect.measure`.

**`EyeDetection.key_source` is not `EyeValidity * (paramset.ParamSet & ...)`,**
despite that being the natural-looking join. Two reasons, both confirmed
directly against a live MySQL 8 container running this project's pinned
DataJoint 2.3.2 before this module was written this way:

1. `EyeValidity`'s own foreign key to `ParamSet` renames only `paramset_idx`
   (`paramset_type` stays bare, correctly -- see `EyeValidity.key_source`'s
   own docstring, and `EyeValidity`'s `# Key:` comment). A literal
   `EyeValidity * (paramset.ParamSet & {"paramset_type": "eye_detection"})`
   would therefore try to match `EyeValidity`'s bare `paramset_type` (always
   `'eye_validity'` on every real row) against the right operand's OWN bare
   `paramset_type` (always `'eye_detection'`) -- DataJoint joins match same-
   named columns for equality, and these two values can never agree. The
   join is permanently empty.
2. Worse: `EyeValidity`'s own primary key includes `eye`, which propagates
   into that join's primary key -- and `EyeDetection` has no `eye` column at
   all (`trace` is native, filled by `make()`, not inherited from any FK).
   `.populate()` does not merely find nothing; it raises outright:
   `DataJointError: The populate target lacks attribute eye from the primary
   key of key_source`.

Fixed by collapsing `EyeValidity` down to one row per (session, validity
paramset) via `dj.U(...)` before the join -- dropping `eye` entirely, the way
a `GROUP BY` would -- with `paramset_type` renamed to `validity_paramset_type`
on the way so the two `ParamSet` references never share a bare column name.
See `EyeDetection.key_source`'s own docstring.
"""

from __future__ import annotations

from pathlib import Path

import datajoint as dj
import numpy as np

from wl_preproc.eye.detect.labels import Label, Run, labels_from_runs, runs_from_labels
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
        """Landed sessions whose ohDPI recording `core.Segment` aligned, times
        the registered `eye_validity` paramsets.

        The FINE-grained `core.Segment` check, like `EyeQuality`'s: with no
        aligned recording there is no sample to mask and no row this table
        could honestly write. A session whose CALIBRATION is unusable still
        reaches `make()` and gets a refused row -- that is a different fact,
        and it is the one `EyeDetection` needs to be able to report.
        """
        from wl_preproc.schema import ingest

        return (
            pipeline.Session
            & ingest.Ingestion
            & (core.Segment & '`system` = "ohdpi"')
        ) * (paramset.ParamSet & {"paramset_type": "eye_validity"}).proj(
            validity_paramset_idx="paramset_idx"
        )

    def make(self, key: dict) -> None:
        """Both eyes' mask for one session and one paramset."""
        from wl_preproc.eye.detect.validity import ValidityParams, validity_labels
        from wl_preproc.eye.detect.velocity import velocity
        from wl_preproc.eye.gaze import gaze_trace
        from wl_preproc.eye.ohdpi import read_columns, read_ohdpi
        from wl_preproc.schema import eye as eye_schema, ingest

        session_key = {k: key[k] for k in pipeline.Session.primary_key}
        # `ParamSet`'s own primary key is `(paramset_type, paramset_idx)`,
        # not `paramset_idx` alone (`paramset.py`'s own `# Key:` comment) --
        # `register()` allocates indices independently PER `paramset_type`,
        # so an `eye_validity` paramset and an `eye_detection` paramset
        # routinely share the same raw index (both start at 0). Restricting
        # by `validity_paramset_idx` alone would match BOTH once an
        # `eye_detection` paramset exists, and `.fetch1()` would raise on
        # the ambiguity. `key["paramset_type"]` is already `'eye_validity'`,
        # straight from this table's own `key_source`.
        params = ValidityParams(**(paramset.ParamSet & {
            "paramset_type": key["paramset_type"],
            "paramset_idx": key["validity_paramset_idx"],
        }).fetch1("params"))
        session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
        segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
        path = session_dir / "ohdpi" / segment["file_path"]
        # `fs_hz`/`frame_gaps` describe the FILE's own sync line, not either
        # eye's gaze -- read once per session rather than once per eye.
        recording = read_ohdpi(path)

        for eye_value, file_eye in (("left", "Left"), ("right", "Right")):
            calibration = (eye_schema.EyeCalibration & {**session_key, "eye": eye_value})
            row = {**key, "eye": eye_value}
            map_ = eye_schema._map_from_row(calibration.fetch1()) if calibration else None
            if map_ is None:
                # Three of the five criteria need degrees, so without a
                # calibration there is no mask -- refused with a reason,
                # never a mask built from raw pixels pretending to be one.
                self.insert1({**row, "status": "refused", "reason":
                              "no usable calibration, so gaze is undefined"})
                continue

            gaze = gaze_trace(path, file_eye, map_)
            quality = read_columns(path, [f"{file_eye}DataQuality"])[f"{file_eye}DataQuality"]
            labels = validity_labels(
                gaze, velocity(gaze, recording.fs_hz), quality,
                recording.frame_gaps, params,
            )
            # `validity_labels` returns `None` for a usable sample; encoded
            # here as `FIXATION` ONLY for storage, because the mask's own
            # runs must still tile `[0, n_samples)` -- `runs_from_labels`
            # cannot encode a `None`. Unambiguous on read only because
            # `validity_labels` never emits a REAL `fixation` verdict of its
            # own (asserted directly, not relied on silently:
            # `tests/schema/test_detect_populate.py::
            # test_the_validity_mask_never_emits_a_real_fixation_label`).
            # `runs_from_labels` normalises every element through `Label(...)`
            # on read, so this is correct regardless of whether `np.where`'s
            # scalar fill happens to preserve the enum wrapper or not.
            runs = runs_from_labels(np.where(labels == None, Label.FIXATION, labels))  # noqa: E711
            self.insert1({
                **row, "status": "computed", "n_samples": len(labels),
                "frac_blink": float(np.mean(labels == Label.BLINK)),
                # The other four criteria are not separately recoverable:
                # `validity_labels` folds `outside`/`too_fast`/`across_gap`
                # into one combined `unusable` mask before returning, so
                # their individual fractions are not something this method
                # can honestly compute from its return value alone --
                # `None` here is the same "not yet knowable" this codebase
                # uses elsewhere (e.g. `EyeCalibration.conditioning_second_
                # order`) rather than a fabricated number.
                "frac_out_of_region": None, "frac_too_fast": None,
                "frac_frame_gap": None, "frac_short_epoch": None,
                "reason": "",
            })
            self.Run.insert(
                {**row, "run_index": index, "run_start": run.start,
                 "run_stop": run.stop, "label": run.label.value}
                for index, run in enumerate(runs)
            )


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
    reason=''            : varchar(255)
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
        """Every validity row -- INCLUDING refused ones -- times the
        `eye_detection` paramsets, collapsed to one candidate per (session,
        validity paramset) BEFORE the join.

        Refused rows are included deliberately: a session whose calibration
        failed must still produce a detection row saying so. Excluding them
        would make "no calibration" and "detector never ran" render
        identically, which is the distinction this table exists to keep.

        **Not `EyeValidity * (paramset.ParamSet & {"paramset_type":
        "eye_detection"})`** -- this module's own docstring has the two
        reasons that literal join is broken (a permanent bare-`paramset_type`
        mismatch, and a hard `DataJointError` from `eye` propagating into a
        table that has no such column). `dj.U(...)` is what drops `eye`
        before the join ever happens, exactly the way a `GROUP BY` would:
        `EyeValidity.proj(validity_paramset_type="paramset_type")` keeps
        `eye` (proj renames one primary-key attribute without being asked to
        drop any other), and `dj.U(...) & (...)` is what actually discards
        it, collapsing however many eyes' rows exist for one (session,
        validity paramset) combination down to exactly one candidate key --
        confirmed directly (this module's own live-container check, before
        this key_source was written this way) that `.populate()` then calls
        `make()` exactly once per session, not once per eye.
        """
        return (
            dj.U("subject", "session_datetime", "validity_paramset_type", "validity_paramset_idx")
            & EyeValidity.proj(validity_paramset_type="paramset_type")
        ) * (paramset.ParamSet & {"paramset_type": "eye_detection"})

    def make(self, key: dict) -> None:
        """All three traces for one session, one mask and one detector.

        **Each eye's own `EyeValidity` status is read and acted on
        independently here** -- echoing `EyeValidity.make()`'s own per-eye
        loop one table earlier, which already refuses a single eye whose
        calibration is unusable and moves on rather than failing the whole
        session. Fix round (reviewer finding 1): the previous version
        tested the SESSION (`EyeValidity & validity_key & 'status =
        "refused"'`, no `eye` in the restriction), so one refused eye
        discarded the OTHER eye's genuinely computed trace and stamped the
        survivor with whichever eye `to_arrays("reason")[0]` happened to
        return first -- no `ORDER BY` made that deterministic.
        `EyeCalibration.make()` fits and refuses each eye independently, so
        a session with exactly one usable eye is reachable, not contrived,
        and design spec section 4 requires it to yield that eye's own
        trace, with the other eye's own refusal and reason -- never a
        refused session wearing one eye's excuse for the other's silence.
        """
        from wl_preproc.eye.detect.registry import get_detector
        from wl_preproc.eye.detect.velocity import velocity
        from wl_preproc.eye.gaze import gaze_trace
        from wl_preproc.eye.ohdpi import read_ohdpi
        from wl_preproc.schema import eye as eye_schema, ingest

        session_key = {k: key[k] for k in pipeline.Session.primary_key}
        validity_key = {**session_key, "validity_paramset_idx": key["validity_paramset_idx"]}
        # Same composite-key reasoning as `EyeValidity.make()`'s own comment:
        # `key["paramset_type"]` is already `'eye_detection'`, straight from
        # this table's own `key_source` -- restricting by `paramset_idx`
        # alone would match whatever OTHER paramset_type has also claimed
        # that raw index (routinely `eye_validity`, since both start at 0).
        params = (paramset.ParamSet & {
            "paramset_type": key["paramset_type"],
            "paramset_idx": key["paramset_idx"],
        }).fetch1("params")
        detector = get_detector(params["detector"])

        session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
        segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
        path = session_dir / "ohdpi" / segment["file_path"]
        fs_hz = read_ohdpi(path).fs_hz

        spans: dict[str, list[tuple[int, int]]] = {}
        per_eye: dict[str, tuple] = {}
        refused_reason: dict[str, str] = {}
        for eye_value, file_eye in (("left", "Left"), ("right", "Right")):
            # `EyeValidity` rows are always `paramset_type='eye_validity'` by
            # construction (nothing else ever writes one), so omitting it
            # from this restriction -- unlike the `ParamSet` fetch above --
            # names no real ambiguity.
            status, reason = (EyeValidity & {**validity_key, "eye": eye_value}).fetch1(
                "status", "reason"
            )
            if status == "refused":
                # THIS eye, and only this eye, stops here: `_map_from_row`,
                # `gaze_trace` and `detector.run` are never reached for it.
                # `EyeCalibration` may itself be refused for this same eye
                # (`EyeValidity.make()`'s own per-eye loop already refuses
                # whenever its own `_map_from_row` comes back `None`), and
                # there is no map for a refused eye to read -- `gaze_trace`
                # would be handed a `None` map and fail trying to apply it,
                # not silently produce an empty trace.
                refused_reason[eye_value] = reason or "validity refused"
                continue

            map_ = eye_schema._map_from_row(
                (eye_schema.EyeCalibration & {**session_key, "eye": eye_value}).fetch1()
            )
            gaze = gaze_trace(path, file_eye, map_)
            v = velocity(gaze, fs_hz)
            available = labels_from_runs(
                [Run(r["run_start"], r["run_stop"], Label(r["label"]))
                 for r in (EyeValidity.Run & {**validity_key, "eye": eye_value}).to_dicts(
                     order_by="run_index")],
                len(gaze),
            )
            # The mask stored `fixation` where a sample is available.
            offered = np.where(available == Label.FIXATION, None, available)
            spans[eye_value] = detector.run(gaze, v, offered, _params_for(detector, params))
            per_eye[eye_value] = (gaze, v, offered)

        for eye_value in ("left", "right"):
            if eye_value in refused_reason:
                self.insert1({**key, "trace": eye_value, "status": "refused",
                              "reason": refused_reason[eye_value]})
            else:
                gaze, v, offered = per_eye[eye_value]
                self._insert_trace(key, eye_value, gaze, v, offered, spans[eye_value], fs_hz, params)

        if refused_reason:
            # The conjunction gets a REFUSED ROW here, never an absent one --
            # design spec section 4's own words used to say "no `conjunction`
            # row"; corrected by this fix round to say a REFUSED row instead,
            # because the same sentence also requires "the reason recorded",
            # and a row's own `reason` column is the only place this schema
            # can ever record one. An absent row cannot be told apart from a
            # key not yet populated -- exactly the ambiguity this module's
            # own docstring's refusal idiom ("writes a refused row with a
            # stated reason rather than raising") exists to remove. The
            # reason below names the actual cause -- a conjunction needs
            # BOTH eyes' spans and at least one is unusable -- rather than
            # repeating either eye's own reason verbatim, which would
            # misreport WHY the conjunction specifically is unusable.
            if len(refused_reason) == 2:
                reason = (
                    "conjunction needs both eyes' detected spans, and both "
                    "the left and right eyes are unusable -- see each eye's "
                    "own trace for its reason"
                )
            else:
                (bad_eye,) = refused_reason
                reason = (
                    f"conjunction needs both eyes' detected spans, and the "
                    f"{bad_eye} eye is unusable -- see that eye's own trace "
                    "for its reason"
                )
            self.insert1({**key, "trace": "conjunction", "status": "refused", "reason": reason})
        else:
            conjunction_spans = _overlapping(spans["left"], spans["right"])
            # The conjunction's TIMING is binocular (`_overlapping`'s own
            # intersection, just above), but a measurement still needs ONE
            # eye's actual gaze -- there is no cyclopean trace any
            # calibration in this codebase ever validated, so averaging the
            # two eyes would measure a position nothing here calibrated
            # against. The LEFT eye is named here rather than averaged for
            # exactly that reason: a real, stated choice, not one the design
            # spec makes for us.
            gaze, v, offered = per_eye["left"]
            self._insert_trace(
                key, "conjunction", gaze, v, offered, conjunction_spans, fs_hz, params
            )

    def _insert_trace(self, key, trace, gaze, v, offered, spans, fs_hz, params) -> None:
        """One trace's master row and its runs.

        Classifies and writes each span's label onto the mask BEFORE
        encoding -- not encode-then-classify -- then measures every event
        run a SECOND time, over its own final `[start, stop)`, rather than
        reusing whichever measurement produced its classification.

        Today's one registered detector cannot make that second measurement
        redundant: `engbert_kliegl.py::_true_runs` only ever returns MAXIMAL
        runs, so two of `spans` are always separated by at least one sample
        neither claims, `_overlapping` inherits that same separation from
        both eyes, and `runs_from_labels` can therefore never merge two of
        this call's own spans into one run. But nothing in `registry.py::
        Detector.run`'s own contract requires a future detector (`wl.yaml`'s
        own "Otero-Millan and U'n'Eye" -- neither written yet) to leave such
        a gap, and if two adjacent spans ever DID classify to the same
        label, `runs_from_labels` would merge them into one run whose real
        `[start, stop)` matches neither original span. Measuring the
        FINAL run rather than trusting either input span is what keeps the
        stored measurement correct regardless of whether that gap holds.
        """
        from wl_preproc.eye.detect.measure import classify, measure

        labels = offered.copy()
        for start, stop in spans:
            amplitude_deg = measure(gaze, v, start, stop, fs_hz).amplitude_deg
            labels[start:stop] = classify(amplitude_deg, params["microsaccade_max_deg"])
        labels = np.where(labels == None, Label.FIXATION, labels)  # noqa: E711
        runs = runs_from_labels(labels)

        row = {**key, "trace": trace}
        self.insert1({
            **row, "status": "computed", "n_samples": len(labels),
            "n_saccades": sum(1 for run in runs if run.label is Label.SACCADE),
            "n_microsaccades": sum(1 for run in runs if run.label is Label.MICROSACCADE),
            "reason": "",
        })

        def _run_row(index: int, run: Run) -> dict:
            amplitude_deg = peak_velocity_deg_s = None
            if run.label in (Label.SACCADE, Label.MICROSACCADE):
                measurement = measure(gaze, v, run.start, run.stop, fs_hz)
                amplitude_deg = measurement.amplitude_deg
                peak_velocity_deg_s = measurement.peak_velocity_deg_s
            return {
                **row, "run_index": index, "run_start": run.start, "run_stop": run.stop,
                "label": run.label.value, "amplitude_deg": amplitude_deg,
                "peak_velocity_deg_s": peak_velocity_deg_s, "reliability": None,
            }

        self.Run.insert(_run_row(index, run) for index, run in enumerate(runs))


def _overlapping(left, right):
    """Spans present in BOTH eyes with temporal overlap -- Engbert & Kliegl's
    own binocular criterion, applied uniformly to every detector. The
    intersection, never the union: an event in one eye alone is noise, which is
    the whole point of the criterion."""
    return [
        (max(ls, rs), min(lstop, rstop))
        for ls, lstop in left
        for rs, rstop in right
        if ls < rstop and rs < lstop
    ]


def _params_for(detector, params: dict):
    """`detector`'s own params dataclass, built from the registered paramset
    dict.

    Not `{k: v for k, v in params.items() if k != "detector"}` -- the
    paramset also carries `microsaccade_max_deg` (`register_default_
    paramsets`'s own reasoning: `classify`'s threshold lives in the SAME
    `eye_detection` paramset, not in any one detector's own parameters), and
    the detector's dataclass declares neither `detector` nor
    `microsaccade_max_deg`. Filtering down to exactly the dataclass's OWN
    declared field names -- rather than naming every key to drop -- is what
    keeps this correct as the paramset's shared vocabulary grows, with no
    detector needing to know what else it should ignore.

    The type comes from `detector.run`'s own `params` argument, read via
    `typing.get_type_hints` rather than a bare `__annotations__`/`inspect.
    signature` lookup: every detector module (`engbert_kliegl.py` included)
    starts `from __future__ import annotations`, which makes every
    annotation a STRING at runtime (PEP 563) -- `get_type_hints` is what
    resolves it back to the real class, in the function's own module
    namespace, confirmed directly against this project's registered
    detector before this helper was written this way.
    """
    import typing
    from dataclasses import fields

    params_cls = typing.get_type_hints(detector.run)["params"]
    known = {f.name for f in fields(params_cls)}
    return params_cls(**{k: v for k, v in params.items() if k in known})


def register_default_paramsets() -> dict[str, int]:
    """One `eye_validity` paramset and one `eye_detection` paramset per
    registered detector, returned by detector name.

    Set equality against `DETECTORS` is this subsystem's completeness claim,
    in the shape `EXTRACTORS` already uses: a detector with no paramset never
    runs, and a paramset naming no detector fails on the first session that
    reaches it.

    **`microsaccade_max_deg` is registered here, in the `eye_detection`
    paramset dict, not read as a bare module constant at classification
    time.** Without it, `classify` would have no threshold a paramset could
    ever revise -- the one thing paramsets exist for (spec section 5.3) --
    and every future `eye_detection` paramset would silently share whatever
    default happened to be hardcoded elsewhere. Defaulted to `measure.
    MICROSACCADE_MAX_DEG`, that module's own conventional cut.
    """
    from dataclasses import asdict

    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS
    from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS

    paramset.register("eye_validity", asdict(DEFAULT_VALIDITY_PARAMS))
    defaults = {"engbert_kliegl": asdict(DEFAULT_EK_PARAMS)}
    return {
        name: paramset.register(
            "eye_detection",
            {"detector": name, "microsaccade_max_deg": MICROSACCADE_MAX_DEG, **defaults[name]},
        )
        for name in DETECTORS
    }


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}detect`. Idempotent."""
    core.activate(prefix=prefix)
    paramset.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}detect", create_tables=True)
