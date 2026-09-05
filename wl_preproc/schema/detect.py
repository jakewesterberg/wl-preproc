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
INTERSECTION of the two eyes' detected intervals as the conjunction (Engbert
& Kliegl's own binocular noise-suppression criterion) subject to the
detector's own minimum event duration, and measures every detected event
once via `wl_preproc.eye.detect.measure`. That duration floor is what keeps
the criterion a FILTER rather than a generator -- see `_overlapping`.

**Labels come from the detector, never from this module -- and the
conjunction's comes from its own measurement.** Design spec section 3:
"Detectors return labelled intervals. Shared code measures them."
`_insert_trace` writes the label each interval already carries and measures
the final run for storage. The conjunction has no detector interval to carry
one, so `_overlapping` labels each intersection by the detector's OWN
labelling rule applied to THAT intersection -- `classify` over its own
amplitude, the same `[start, stop)` on the same gaze that `_insert_trace`
then stores, where the detector declares the whole amplitude split; the
detector's single declared class where it declares half of one. Never by
arbitrating between the two eyes' labels. See `_conjunction_label` for the
three defects that arbitration caused, and for why half a split is not
`classify`'s question to answer. `_insert_trace` used to assign every span's label
itself, from amplitude, via `classify` -- which can only answer `saccade` or
`microsaccade`, so a stage-2 detector declaring `{saccade, pso, fixation}`
would have had everything it found relabelled by amplitude and its declared
vocabulary would have been unenforceable.

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

from collections.abc import Callable
from pathlib import Path

import datajoint as dj
import numpy as np

from wl_preproc.eye.detect.labels import Label, Run, labels_from_runs, runs_from_labels
from wl_preproc.schema import DEFAULT_PREFIX, core, paramset, pipeline

schema = dj.Schema()

_LABEL_ENUM = ",".join(f"'{label.value}'" for label in Label)


class UndecidedConjunctionLabel(ValueError):
    """Nothing states what a conjunction run's label should be for this
    detector's declared vocabulary, and design spec section 2.5 forbids
    inventing an answer by default. See `_conjunction_label`."""


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
    #
    # RAW per-criterion counts over all `n_samples`, never apportioned
    # shares: one sample can be rejected by two criteria at once, so these
    # five can sum ABOVE the fraction of samples the mask actually rejects
    # -- and the first four are counted before dilation grows each rejected
    # region, so they can sum BELOW it too. `eye/detect/validity.py::
    # ValidityMask` states both in full. `NULL` on a refused row, which has
    # no mask at all, and only there.
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

        **`eye.EyeCalibration` is required to have RUN, not to have
        succeeded** (whole-branch review, finding M5). `EyeCalibration.
        key_source` additionally requires `pipeline.event.BehaviorRecording`,
        which `daemon._populate_event_stage()` writes -- so a session whose
        event decode failed while its barcode alignment succeeded satisfies
        THIS table's restriction and not that one. Reached in that state
        (`SystemTimebase` -> `Segment` -> `EyeValidity`, no event stage),
        `make()`'s own `_map_from_row(...) is None` branch refused both eyes
        with "no usable calibration, so gaze is undefined" -- and that row is
        PERMANENT. On the next pass both eyes calibrate perfectly, but
        DataJoint never recomputes a populated key, so three traces stay
        refused forever with a reason that is now false, and `run_once`
        reports no error. Nothing but the in-pass ordering of
        `daemon._computed_tables()` kept the two tables in step; this
        restriction is what makes that state unreachable rather than merely
        unlikely.

        **Presence, not success, and that is the whole point.** A session
        whose calibration was GENUINELY refused still has rows here, so it
        still reaches `make()` and still gets the refused mask row design
        spec section 2 wants -- the refused path is preserved exactly.

        **Presence is a sound gate because `EyeCalibration.make()` writes
        BOTH eyes' rows in every terminal case, verified by reading it
        rather than assumed**: its `if not segments` branch inserts two
        refused rows and returns, its `if windows and not row_ranges` branch
        does the same, and its main path appends one row per eye inside a
        loop over a fixed `("left", "right")` pair before a single
        `self.insert(rows)`. There is no path that returns having written
        nothing. If `make()` RAISES, no row is written and this key simply
        stays outstanding until a later pass -- which is the correct
        outcome, not a false refusal.

        A RESTRICTION (`&`), never a join: `EyeCalibration`'s own primary
        key carries `eye`, and joining it in would propagate that attribute
        into this key_source exactly as `EyeDetection.key_source`'s own
        docstring records happening one table later. A restriction keeps
        this operand's heading and asks only whether a matching row exists.
        """
        from wl_preproc.schema import eye as eye_schema, ingest

        return (
            pipeline.Session
            & ingest.Ingestion
            & (core.Segment & '`system` = "ohdpi"')
            & eye_schema.EyeCalibration
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
            mask = validity_labels(
                gaze, velocity(gaze, recording.fs_hz), quality,
                recording.frame_gaps, params,
            )
            labels = mask.labels
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
                # All five of design spec section 7's "per-criterion
                # rejected fractions". Four of them were `None` here, under
                # a comment claiming `validity_labels` folded them into one
                # combined mask before returning and so made them "not
                # separately recoverable" -- true of the RETURN SHAPE, never
                # of the data: all five were already named locals in that
                # function. It now returns a `ValidityMask` carrying them.
                #
                # ONE spread, not five hand-written lines. `ValidityMask.
                # fractions` is keyed by criterion name and this table's
                # columns are `frac_` + that name, so the pairing happens
                # once, in the function that owns the criteria. Five columns
                # written out by hand from a single source is the shape a
                # copy-paste error survives in: every column still looks
                # populated. A key naming no column raises on insert here
                # rather than disappearing.
                #
                # These are RAW per-criterion counts. They overlap, and they
                # are taken at two different stages of the mask's own
                # construction, so they do not sum to the rejected fraction
                # from either side -- `ValidityMask`'s docstring states both,
                # and this table's own column comment above carries the
                # short version for a reader looking only at the schema.
                **{f"frac_{criterion}": value
                   for criterion, value in mask.fractions.items()},
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
    # **On a `conjunction` row these two count a DIFFERENT POPULATION from
    # the same session's per-eye rows, and `cli/report.py::build_report`'s
    # "Events per session per trace" list renders all three side by side.**
    # A per-eye row counts full detected events; a conjunction row counts
    # the INTERSECTIONS `_overlapping` kept, and design spec section 5.1
    # records that an intersection is a strictly shorter interval whose
    # amplitude -- hence its saccade/microsaccade class -- is measured over
    # that shorter span: "it makes it a different population from either
    # eye's". So summing `n_microsaccades` across one session's three
    # traces aggregates two populations, and reading a conjunction count
    # beside that session's left count compares two different things that
    # share a column name. Section 5.1 names the same hazard for
    # `SaccadeMainSequence`, where that table's own `trace` key prevents an
    # accidental pooling; nothing prevents it here, which is why it is
    # stated where the columns are declared.
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
        # Built ONCE, above the per-eye loop rather than inside it, because
        # the conjunction's own duration floor is read off this same object
        # (`_min_duration_samples`, below): both eyes and the intersection of
        # their spans are then demonstrably talking about one set of detector
        # parameters rather than about two separate constructions of it.
        detector_params = _params_for(detector, params)

        session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
        segment = (core.Segment & {**session_key, "system": "ohdpi"}).fetch1()
        path = session_dir / "ohdpi" / segment["file_path"]
        fs_hz = read_ohdpi(path).fs_hz

        spans: dict[str, list[Run]] = {}
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
            # `detector.detect`, never `detector.run`: the wrapper is what
            # holds the detector to its own declared `vocabulary`, and this
            # is the one place in production that runs a detector at all.
            spans[eye_value] = detector.detect(gaze, v, offered, detector_params)
            per_eye[eye_value] = (gaze, v, offered)

        for eye_value in ("left", "right"):
            if eye_value in refused_reason:
                self.insert1({**key, "trace": eye_value, "status": "refused",
                              "reason": refused_reason[eye_value]})
            else:
                gaze, v, offered = per_eye[eye_value]
                self._insert_trace(key, eye_value, gaze, v, offered, spans[eye_value], fs_hz)

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
            # The conjunction's TIMING is binocular (`_overlapping`'s own
            # intersection, below), but a measurement still needs ONE eye's
            # actual gaze -- there is no cyclopean trace any calibration in
            # this codebase ever validated, so averaging the two eyes would
            # measure a position nothing here calibrated against. The LEFT
            # eye is named here rather than averaged for exactly that
            # reason. This comment used to add "not one the design spec
            # makes for us"; that is no longer true, and the spec agrees
            # rather than being silent -- design spec section 5.1 measures
            # the conjunction "on the left eye's gaze", on this same
            # reasoning ("One eye is named rather than the two averaged
            # because no cyclopean trace has ever been calibrated in this
            # repository").
            #
            # Read BEFORE `_overlapping` rather than after it, because this
            # same trace is now what the conjunction's LABEL comes from as
            # well as its measurement (`_conjunction_label`). One trace
            # answering for both is the whole fix: label and amplitude
            # derived from two different things is what made them contradict
            # each other on 12.3% of conjunction event rows.
            #
            # **Which means naming `"left"` here now decides conjunction
            # LABELS and not only amplitudes, and the two eyes do not always
            # agree about them.** Measured, not supposed: the whole-branch
            # review's finding M4 labelled from the RIGHT eye's gaze while
            # measuring on the left, against the reference recording, and
            # got a contradicting class on 242 of 4,550 conjunction event
            # rows -- roughly 5% would carry a different saccade/
            # microsaccade verdict had this line named the other eye.
            # Nothing is wrong today; label and amplitude still come from
            # one trace, which is the point. But "the conjunction is the
            # LEFT eye's opinion of a binocular event" is a larger claim
            # than the amplitude-only one this comment used to justify, and
            # it is recorded here rather than resolved -- section 5.1 states
            # the same asymmetry without resolving it either.
            gaze, v, offered = per_eye["left"]
            # The floor is the DETECTOR's own, inherited from the params it
            # just ran with -- the binocular criterion filters what both eyes
            # already found, and must never manufacture an event shorter than
            # either eye's detector would have accepted. See `_overlapping`.
            conjunction_spans = _overlapping(
                spans["left"],
                spans["right"],
                _min_duration_samples(detector_params),
                _conjunction_label(detector, params, gaze),
            )
            self._insert_trace(key, "conjunction", gaze, v, offered, conjunction_spans, fs_hz)

    def _insert_trace(self, key, trace, gaze, v, offered, intervals, fs_hz) -> None:
        """One trace's master row and its runs.

        **This method assigns no labels of its own.** Each interval arrives
        already labelled -- by the detector for `left` and `right` (design
        spec section 3: "Detectors return labelled intervals. Shared code
        measures them."), by `_overlapping`'s `label_for` for `conjunction`,
        which is the detector's own labelling rule over that same
        intersection (`_conjunction_label`). It used to call `classify` on
        every span itself, which could only ever return `saccade` or
        `microsaccade`: a
        stage-2 detector declaring `{saccade, pso, fixation}` would have had
        every span it detected relabelled by amplitude regardless of what it
        actually found, and four of design spec section 3.1's seven declare
        exactly such vocabularies.

        Writes each interval's label onto the mask BEFORE encoding -- not
        encode-then-label -- then measures every event run over its own
        final `[start, stop)`, rather than reusing whichever measurement the
        detector made while labelling it.

        Neither registered detector can make that second measurement
        redundant: `engbert_kliegl.py::_true_runs` only ever returns MAXIMAL
        runs and `otero_millan.py::_merge` guarantees a gap, so two of
        `intervals` are always separated by at least one sample neither
        claims, and `runs_from_labels` can therefore never merge two of this
        call's own intervals into one run. But nothing in
        `registry.py::DetectFn`'s own contract requires a future detector
        (the five still unwritten -- Nystrom-Holmqvist, NSLR, REMoDNaV, BMD,
        U'n'Eye) to leave such a gap, and if two adjacent intervals ever DID
        carry
        the same label, `runs_from_labels` would merge them into one run
        whose real `[start, stop)` matches neither original interval.
        Measuring the FINAL run rather than trusting either input interval
        is what keeps the stored measurement correct regardless of whether
        that gap holds.

        **For `conjunction` that gap is guaranteed rather than inherited**,
        and it has to be: the conjunction's label is `classify` over the
        amplitude of the interval it was assigned to, so a merge that moved
        the boundaries would store a label and an amplitude that disagree --
        the exact defect this round exists to close. `_overlapping`
        coalesces touching intersections before labelling them for that
        reason, so the run measured here is always the span labelled there.

        **`reliability` is mapped back onto the final runs by EXACT
        `(start, stop)` match, and is `None` for anything else.** It is a
        per-DETECTION value (Otero-Millan's own silhouette, design spec
        section 5), and the re-derivation above is precisely what can leave a
        final run corresponding to no single detector interval: two adjacent
        intervals carrying the same label merge into one run whose span
        matches neither. Attributing either half's reliability to that run
        would put a fabricated number in the one column a reader consults to
        decide how much to trust a detection -- so the map simply misses and
        `None` is stored, which is the honest answer. The conjunction trace
        gets `None` throughout for the same reason it gets its label derived
        rather than checked: no detector produced it.
        """
        from wl_preproc.eye.detect.measure import measure

        reliability_by_span = {
            (interval.start, interval.stop): interval.reliability for interval in intervals
        }

        labels = offered.copy()
        for interval in intervals:
            labels[interval.start : interval.stop] = interval.label
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
                "peak_velocity_deg_s": peak_velocity_deg_s,
                "reliability": reliability_by_span.get((run.start, run.stop)),
            }

        self.Run.insert(_run_row(index, run) for index, run in enumerate(runs))


def _overlapping(
    left: list[Run],
    right: list[Run],
    min_duration_samples: int,
    label_for: Callable[[int, int], Label],
) -> list[Run]:
    """Labelled intervals present in BOTH eyes with temporal overlap, no
    shorter than the detector's own minimum event duration -- Engbert &
    Kliegl's own binocular criterion, applied uniformly to every detector.
    The intersection, never the union: an event in one eye alone is noise,
    which is the whole point of the criterion.

    **The label is `label_for` applied to the intersection's own
    `[start, stop)`, and the two eyes' own labels are never consulted here.**
    `_conjunction_label` builds that callable and carries the whole of the
    reasoning; this function only applies it. It is a REQUIRED argument for
    the same reason `min_duration_samples` is: the rule is not this
    function's to default, and a default is what would let a caller quietly
    reintroduce an arbitration the design spec forbids.

    **Touching or overlapping intersections are coalesced BEFORE they are
    labelled**, and that is what makes the label agree with the amplitude
    `_insert_trace` stores. That method writes each interval's label onto the
    mask and re-derives runs from it, so two intervals that touch and carry
    the same label would come back as ONE run whose `[start, stop)` -- and
    therefore whose measured amplitude -- matches neither input. Coalescing
    first leaves every returned span separated from the next by at least one
    sample, so `runs_from_labels` cannot merge any of them and the span
    labelled here is exactly the run measured there. Today's one detector
    already supplies that separation (`engbert_kliegl.py::_true_runs`
    returns maximal runs, and intersecting two separated families keeps them
    separated), but `registry.py::DetectFn`'s contract does not require it,
    and agreement between a stored label and a stored amplitude must not
    rest on a property only one detector happens to have. The sort is for
    the same reason: `left` and `right` arrive sorted from today's detector,
    and nothing in that contract says they must.

    **The floor is here because the binocular criterion is a FILTER, not a
    generator** (whole-branch review, finding H3). It must never produce an
    event shorter than either eye's own detector would have accepted. Without
    it, the intersection of two 6-sample Engbert-Kliegl events overlapping by
    ONE sample is a one-sample event -- and `measure`'s own
    `gaze_deg[stop - 1] - gaze_deg[start]` is then identically zero, so
    `classify` stores it as a 0.0-degree `microsaccade` carrying a peak
    velocity of up to 864 deg/s. `measure`'s `stop > start` precondition
    catches `stop <= start`, not `stop - start == 1`. Measured through this
    function on the reference recording at default parameters: 4,952
    conjunction spans, of which 402 (8.1%) fell below the 6-sample floor and
    44 were exactly one sample long.

    **Not in `measure`, and not in `classify`.** Special-casing
    `stop - start == 1` inside `measure` would fix the 44 most visible cases
    and leave the other 358 -- 2 to 5 samples each -- still stored as events
    neither eye's own detector considered real. `classify` is handed an
    amplitude and a threshold and has no duration to reject. And the damage
    is downstream of both: design spec section 6.5 fits the main sequence
    from exactly the `amplitude_deg`/`peak_velocity_deg_s` pair these rows
    carry, where a zero-amplitude, high-velocity point is maximally damaging
    to a saturating fit -- section 6.5.3 already argues that saccade-boundary
    contamination "shifts the whole main sequence".

    `min_duration_samples` is a REQUIRED argument rather than a defaulted
    one: a default is exactly what would let a future caller silently
    reintroduce the unfiltered intersection this fix exists to remove. See
    `_min_duration_samples` for where the value comes from and why it is
    floored at 1 below -- the condition this replaces was
    `ls < rstop and rs < lstop`, which is precisely `stop > start`, and a
    paramset naming 0 must not weaken it into spans `measure` refuses.
    """
    floor = max(int(min_duration_samples), 1)
    intersections = []
    for left_run in left:
        for right_run in right:
            start = max(left_run.start, right_run.start)
            stop = min(left_run.stop, right_run.stop)
            if stop - start >= floor:
                intersections.append((start, stop))

    spans: list[list[int]] = []
    for start, stop in sorted(intersections):
        # `<=`, not `<`: a span STARTING where the previous one stops touches
        # it, and two touching runs of one label are one run to
        # `runs_from_labels`. Merging them here is what keeps the interval
        # labelled below identical to the interval `_insert_trace` measures.
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], stop)
        else:
            spans.append([start, stop])

    return [Run(start=start, stop=stop, label=label_for(start, stop)) for start, stop in spans]


# The vocabularies whose labels ARE a split by amplitude, so a conjunction run
# can be labelled from its own measurement. Design spec section 3.1 gives this
# to Engbert-Kliegl and Otero-Millan (both `{saccade, microsaccade}`) and, in
# stage 2, to U'n'Eye (`{saccade}`) -- a SUBSET test rather than equality, so a
# detector declaring HALF the split qualifies too.
#
# **Otero-Millan was named here as the `{microsaccade}` half-split case until
# 2026-09-01.** It is not one: reading the reference implementation showed it
# detects saccades of ANY amplitude and its only amplitude rule is a 0.2 degree
# LOWER noise floor (design spec section 3.1's correction). U'n'Eye is the
# surviving half-split example.
#
# **The SACCADIC SLICE of a vocabulary decides the label, and a slice of size
# one is a DEGENERATE split.** U'n'Eye (`{saccade}`), Bayesian microsaccade
# detection (`{microsaccade, drift}`) and the three `pso`-capable detectors
# each declare one side of the cut and cannot emit the other, so `classify` --
# which answers both sides for any detector -- would put a word in their
# mouths that `registry.Detector.detect` refuses from the detector itself.
# Five of the seven land there; only Engbert-Kliegl and Otero-Millan declare
# both sides. See `_conjunction_label`, which reads this set rather than
# restating it.
#
# **This set is no longer a gate on whether a conjunction can be built at
# all.** It was, until 2026-09-05: a vocabulary that was not a subset raised,
# because ONE label had to cover a mixed vocabulary. Per-kind intersection
# (`_conjunction_runs`) means each kind labels itself, so the mixed case does
# not arise and there is nothing left to refuse.
_AMPLITUDE_DERIVED_VOCABULARY = frozenset({Label.SACCADE, Label.MICROSACCADE})


class UnknownLabelKind(ValueError):
    """A label reached the conjunction with no kind assigned to it."""


#: Which labels intersect with which. A conjunction run is the intersection of
#: two runs of the SAME kind, and carries that kind's label (design spec
#: `2026-09-05-conjunction-shape-design.md` section 1).
#:
#: **`saccade` and `microsaccade` share a kind**, because section 1 of the
#: detection spec calls them "a split, not a ranking" -- one event
#: distinguished only by size. They intersect together and the surviving span
#: is labelled by `classify` on its OWN measured amplitude, which is what
#: stage 1 already did and what keeps label and amplitude derived once, from
#: one interval. Every other emitted label is its own kind and intersects only
#: with itself, so a binocular glissade is stored as `pso` rather than folded
#: into a saccade or dropped.
_KIND_OF: dict[Label, str] = {
    Label.SACCADE: "saccadic",
    Label.MICROSACCADE: "saccadic",
    # **Every non-saccadic kind's key IS its label's own value**, and
    # `_conjunction_runs` relies on it: `Label(kind)` is how such a kind
    # labels itself. Tested, not trusted -- see
    # `test_a_single_label_kind_is_keyed_by_its_own_label_value`. "saccadic"
    # is deliberately not a `Label`, because that kind has two of them and no
    # single label could name it.
    Label.PSO: Label.PSO.value,
    Label.PURSUIT: Label.PURSUIT.value,
    Label.DRIFT: Label.DRIFT.value,
}

#: Labels that are never intersected, and why -- listed rather than left as
#: absences, so `_kind_of`'s guard can tell "deliberately excluded" from "a
#: ninth label nobody mapped".
#:
#: `fixation` is the synthesized background: `_insert_trace` paints every
#: sample no interval claimed, so a region survives as `fixation` whether an
#: intersection painted it or the fill did. Intersecting it would run the
#: nested loop over the largest runs in the trace for no observable difference
#: (spec section 1.2). `blink` and `invalid` come from the validity mask,
#: never from a detector, and are in no detector's vocabulary at all.
_NOT_INTERSECTED = frozenset({Label.FIXATION, Label.BLINK, Label.INVALID})


def _kind_of(label) -> str | None:
    """`label`'s conjunction kind, or `None` if it is deliberately not
    intersected.

    Raises rather than returning `None` for an unmapped label. Design spec
    section 1 declares all eight labels because the migration window closes
    January 2027; this is what catches a ninth added without updating
    `_KIND_OF`, which would otherwise vanish from every conjunction with
    nothing to show for it."""
    if label in _NOT_INTERSECTED:
        return None
    try:
        return _KIND_OF[label]
    except KeyError as exc:
        raise UnknownLabelKind(
            f"{label!r} has no conjunction kind. Every label is either in "
            f"`_KIND_OF` or deliberately in `_NOT_INTERSECTED`; a new one is "
            f"in neither until someone decides which it is"
        ) from exc


def _always(label: Label) -> Callable[[int, int], Label]:
    """A `label_for` answering one label whatever the span.

    Two callers. `_conjunction_runs` uses it for every kind that labels
    itself -- one that is not the amplitude split has exactly one label, so
    there is no rule to apply. The duration-floor tests use it where the
    FLOOR is what is under test and which label a surviving span carries is
    not."""
    return lambda _start, _stop: label


def _conjunction_runs(
    left: list[Run],
    right: list[Run],
    min_duration_samples: int,
    saccadic_label_for: Callable[[int, int], Label],
) -> list[Run]:
    """The binocular criterion, applied WITHIN each kind.

    A conjunction run is the temporal intersection of two runs of the same
    kind, and it carries that kind's label. `saccade` and `microsaccade` are
    one kind, labelled by `saccadic_label_for` on the intersection's own
    interval; every other emitted label is its own kind and labels itself.

    **This is what makes the conjunction trace the same shape as the per-eye
    traces.** `_overlapping` intersects on time alone and never reads a
    label, which is correct only while every emitted label is the same kind
    of thing -- true of Engbert-Kliegl and Otero-Millan, and false for all
    four BLOCKED detectors. Three of them (Nystrom-Holmqvist, NSLR, REMoDNaV)
    emit `pso` and `fixation`; the fourth (BMD) emits `drift` instead and
    never `pso` at all. `fixation` TILES the recording, so an ungrouped
    intersection would have crossed a left fixation with a right saccade and
    kept it.

    **Grouping first also makes the loop cheaper.** `_overlapping` is
    `O(|left| x |right|)`; summing that over kinds is strictly less than the
    product of the totals whenever more than one kind is present, and
    identical when only one is -- which is the case for both registered
    detectors, whose rows are therefore unchanged.

    Not folded into `_overlapping`: that function is the single-kind
    primitive and every one of its call sites, in production and in tests,
    passes single-kind input. Keeping the two separate is what lets the H3
    duration-floor tests keep testing the floor rather than the grouping."""
    by_kind: dict[str, tuple[list[Run], list[Run]]] = {}
    for side, runs in ((0, left), (1, right)):
        for run in runs:
            kind = _kind_of(run.label)
            if kind is None:
                continue
            by_kind.setdefault(kind, ([], []))[side].append(run)

    out: list[Run] = []
    for kind, (left_runs, right_runs) in by_kind.items():
        if kind == "saccadic":
            label_for = saccadic_label_for
        else:
            # The kind labels itself. No rule to write, no arbitration, and
            # no convention stated anywhere -- both eyes already agreed.
            label_for = _always(Label(kind))
        out.extend(_overlapping(left_runs, right_runs, min_duration_samples, label_for))

    return sorted(out, key=lambda run: run.start)


def _conjunction_label(detector, params: dict, gaze: np.ndarray) -> Callable[[int, int], Label]:
    """How a conjunction run gets its label: the DETECTOR's own labelling
    rule, applied to that run's own interval on the gaze the conjunction is
    measured from. Returned as a callable for `_overlapping` to apply to each
    span it keeps.

    For a detector declaring the whole amplitude split that rule is
    `classify` over the run's own amplitude -- literally the rule
    `engbert_kliegl.py::detect_engbert_kliegl` labels its own intervals with,
    so all three of its traces are labelled the same way. For a detector
    declaring HALF of the split it is that half, constantly; see the
    degenerate-split section below.

    **The conjunction's label comes from its own measurement, over its own
    interval -- never from arbitrating between the two eyes.** The rule this
    replaces ranked the two eyes' labels through `labels.py::PRECEDENCE`,
    and was wrong three ways at once, all from one cause: the label was
    derived from the two eyes' full-event amplitudes and the amplitude from
    the left eye's, over the shorter intersection.

    1. Design spec section 1 says `saccade` and `microsaccade` are "a split,
       not a ranking" -- and a tuple has a total order, so ranking them
       decided a pair the spec says is never in contention. Measured on the
       reference recording at default parameters: it fired on 593 of 4,550
       intersections, 13.0%.
    2. `saccade` outranking `pso` assigns the glissade silently, on an
       instrument where section 2.5 argues PSO follows EVERY saccade and
       requires that assignment to be "an explicit parameter, never a
       default".
    3. The stored `label` contradicted the stored `amplitude_deg` on 12.3%
       of conjunction event rows -- 518 of 2,209 `saccade` rows below the
       threshold, 40 of 2,341 `microsaccade` rows at or above it, against 0
       of 5,972 on the left trace and 0 of 5,592 on the right. Section 6.5
       fits the main sequence from exactly those two columns, selecting rows
       by label, so those 518 sub-degree points were headed into a saccade
       fit.

    Deriving the label ONCE, from the amplitude that is actually stored,
    ends all three: `_overlapping` labels each span with this callable and
    `_insert_trace` measures that same `[start, stop)` on this same `gaze`,
    so the two agree by construction on every row rather than by luck.
    Deriving it twice -- once from the eyes, once from the measurement -- is
    what let them diverge in the first place, so the eyes' own labels are
    not consulted here at all.

    That paragraph is about the detectors whose vocabulary IS the amplitude
    split; below it is scoped, because a detector that declares half the
    split makes no cut for a stored amplitude to agree or disagree with.

    **A vocabulary whose SACCADIC SLICE is HALF the split gets that half,
    and never `classify`'s other answer** (fix round, reviewer finding 2).
    Finding 2 was first fixed as a subset test on the detector's WHOLE
    vocabulary -- correct as long as every registered vocabulary was
    entirely saccadic, which was true of design spec section 3.1's U'n'Eye
    (`{saccade}`) and of Engbert-Kliegl/Otero-Millan's shared full split
    (`{saccade, microsaccade}`), the only detectors registered at the time.
    `_conjunction_label` now intersects `detector.vocabulary` with
    `_AMPLITUDE_DERIVED_VOCABULARY` instead (see this function's own
    SACCADIC SLICE comment below) -- the same answer for those three, and
    additionally correct for a vocabulary that mixes a saccadic label with a
    non-saccadic one, which a whole-vocabulary subset test cannot admit at
    all. `classify` answers both sides of the cut for any detector, and a
    conjunction interval is the INTERSECTION of the two eyes' events --
    shorter than either, and systematically smaller in amplitude (section
    5.1) -- so short intersections fall below the cut routinely. Left
    ungated, U'n'Eye would have stored `microsaccade` rows: a label its own
    detector declares it cannot emit.

    **This paragraph named Otero-Millan as the second half-split case until
    2026-09-01.** It is not one -- it declares the whole split, so `classify`
    is called for its conjunctions and both answers are in vocabulary. The
    guard still matters for U'n'Eye and, since 2026-09-05's SACCADIC SLICE
    generalization, for BMD's `{microsaccade, drift}` and the three
    `pso`-capable detectors as well -- all four reach THIS branch,
    degenerately, because none of them declares both `saccade` and
    `microsaccade`:

    - `registry.Detector.detect` refuses exactly that label from the
      detector itself, and its own docstring is why -- the declaration is
      "enforced, not merely recorded", because "every consumer of this one
      reads the claim rather than the output". The conjunction's labels
      never pass through `detect`, so this is the one place that claim can
      be broken without anything noticing.
    - Section 6.1's coarsening lattice is the consumer that would be
      misled. It picks "the coarsest vocabulary both declare" and coarsens
      the STORED labels into it, and its only amplitude-split rule runs
      `microsaccade -> saccade`. A stored `saccade` on a trace declared
      `{microsaccade}` has no rule to place it, and the pair is scored in a
      vocabulary that trace does not speak -- the precise failure section
      6.1 exists to prevent.
    - Both eyes' OWN traces already carry the detector's single class for
      that event, whatever its amplitude, because that is all the detector
      can say. A conjunction disagreeing with both eyes about a class
      neither eye can express is not a binocular finding.

    So the split is DEGENERATE for such a detector, and a degenerate split
    has one answer. This is not `classify`'s output being overridden: it is
    the amplitude cut not being asked, because the detector does not make
    it. `classify` itself is left alone -- it is `measure.py`'s shared
    function, called by detectors as well as by this one, and teaching it
    about registry vocabularies would put a detector concept in the module
    section 3 keeps deliberately free of them.

    **`microsaccade_max_deg` comes from the PARAMSET dict, not from the
    detector's own params dataclass, and is read only where a cut is
    actually made.** It is the shared key `register_default_paramsets`
    writes once beside `detector`, and `_params_for` hands it to a detector
    only if that detector declares a field of the name -- which a detector
    declaring half the split has no reason to do, and now no reason to
    need. A paramset that names no threshold raises `KeyError` here rather
    than falling back to `measure.MICROSACCADE_MAX_DEG`: a silent module
    default is exactly what would let two `eye_detection` paramsets split
    their conjunctions at different amplitudes with nothing on record. That
    `KeyError` is not raised for a degenerate split, where no paramset
    could have changed the answer -- demanding a number and then ignoring
    it would tell a reader the threshold governs those rows.

    **Until 2026-09-05, any vocabulary not entirely within
    `_AMPLITUDE_DERIVED_VOCABULARY` RAISED here** -- a subset test on the
    WHOLE vocabulary, which blocked four of design spec section 3.1's seven
    detectors: Nystrom-Holmqvist, NSLR and REMoDNaV all declare `pso`
    alongside `saccade`, and BMD declares `drift` alongside `microsaccade`.
    The guard existed because ONE label had to cover a mixed vocabulary, and
    `pso`, `pursuit` and `drift` are all labels no amplitude cut has a rule
    for -- design spec section 2.5 forbids answering `pso`'s fate by default,
    and an amplitude cut simply offers no rule for `pursuit` or `drift`
    either.

    **It is removed, not merely widened, because per-kind intersection
    (`_conjunction_runs`, design spec `2026-09-05-conjunction-shape-design.
    md`) removes the reason it existed.** Each conjunction kind now labels
    itself: `pso`, `pursuit` and `drift` each get their OWN kind, labelled
    by neither eye's opinion nor by `classify` (`_KIND_OF`), and `fixation`
    is not intersected at all, being the synthesized background rather than
    a detector's finding (`_NOT_INTERSECTED`). None of the four blocked
    detectors needs THIS function to say anything about `pso`, `pursuit`,
    `drift` or `fixation` any more -- only about the SACCADIC SLICE of its
    vocabulary, which is what `_AMPLITUDE_DERIVED_VOCABULARY`'s own comment
    and the code below this docstring now compute.

    **A vocabulary whose saccadic slice is EMPTY -- no `saccade`, no
    `microsaccade` at all -- still cannot answer the amplitude question, but
    now says so by returning a callable that raises if ever called, rather
    than raising eagerly here.** Eager raising would refuse a conjunction
    for that detector's OTHER kinds too, over a question `_conjunction_
    runs`'s own grouping was never going to ask of THIS callable. See this
    function's own SACCADIC SLICE comment below for that callable. An EMPTY
    vocabulary -- no labels declared, of any kind -- is different again and
    still raises eagerly, for the distinct reason stated at its own guard
    below.
    """
    from wl_preproc.eye.detect.measure import amplitude, classify

    if not detector.vocabulary:
        # `frozenset() & anything` is `frozenset()`, so the saccadic-slice
        # computation below would treat a detector that declares NOTHING
        # exactly like one that declares only non-saccadic labels -- and
        # hand back a callable that raises only if a saccadic conjunction is
        # ever asked for, rather than refusing outright. That is the wrong
        # answer for this detector specifically: it has no OTHER kind either,
        # so there is nothing it could produce a conjunction for at all.
        # Separate from the ruling below because the reason is different:
        # there is no undecided question here, only a detector
        # `registry.Detector.detect` would refuse every interval from.
        raise UndecidedConjunctionLabel(
            f"detector {detector.name!r} declares an empty vocabulary, so there is no "
            "label a conjunction run could carry that the detector itself would be "
            "allowed to emit -- `registry.Detector.detect` refuses every label for "
            "such a detector. Declare what it emits before asking for its conjunction"
        )
    # The SACCADIC SLICE, not the whole vocabulary. Nystrom-Holmqvist
    # declares `{saccade, pso, fixation}` and Bayesian microsaccade detection
    # `{microsaccade, drift}`; neither is size one, and both make only half
    # the amplitude cut. Testing the whole vocabulary -- which is what stage 1
    # did, correctly, when the whole vocabulary WAS the cut -- would send both
    # to `classify` and put a label in each one's mouth that
    # `registry.Detector.detect` refuses from the detector itself.
    #
    # Five of the seven detectors land here and only Engbert-Kliegl and
    # Otero-Millan reach `classify`. The degenerate branch arrived as a
    # fix-round finding about U'n'Eye and is now the majority path.
    saccadic = detector.vocabulary & _AMPLITUDE_DERIVED_VOCABULARY

    if not saccadic:
        # No saccadic label at all, so `_conjunction_runs` builds no saccadic
        # group and never calls this IN PRODUCTION -- but that guarantee is
        # not a property of this function or of `_conjunction_runs`'s
        # grouping (`by_kind` keys off each RUN's own label via `_kind_of`,
        # never off `detector.vocabulary`). It holds because
        # `registry.Detector.detect` refuses any label outside
        # `detector.vocabulary`, so a run this detector actually produces can
        # never carry `saccade`/`microsaccade` when `saccadic` is empty, AND
        # because `EyeDetection.make()` sources both eyes' spans from that
        # SAME detector's `.detect()` before passing this same `detector` to
        # `_conjunction_label`. Break either half -- call `.run()` instead of
        # `.detect()`, or hand-build spans that disagree with the detector --
        # and this callable is exactly what stands between that mistake and
        # a silently wrong label, which is why it is tested directly
        # (`test_a_detector_declaring_no_saccadic_label_raises_only_if_
        # invoked`) rather than left to this reachability argument alone.
        # Returned rather than raised here so the detector's OTHER kinds
        # still produce a conjunction: it is only the amplitude split that
        # has nothing to say.
        def _no_saccadic_label(_start: int, _stop: int) -> Label:
            raise UndecidedConjunctionLabel(
                f"detector {detector.name!r} declares no saccadic label, so it "
                f"can produce no saccadic conjunction run -- and one was asked "
                f"to be labelled anyway, which is a bug in `_conjunction_runs`' "
                f"grouping rather than a question about this detector"
            )

        return _no_saccadic_label

    if len(saccadic) == 1:
        # The degenerate split. Returned from the DECLARATION rather than
        # from any amplitude, so the label is in the detector's vocabulary by
        # construction and not by a check that could be removed.
        (declared,) = saccadic
        return lambda _start, _stop: declared

    # `saccadic` is `detector.vocabulary & _AMPLITUDE_DERIVED_VOCABULARY`, not
    # empty (ruled out above) and not size one (ruled out above), so on a
    # two-label universe it IS `_AMPLITUDE_DERIVED_VOCABULARY` -- exactly the
    # set of answers `classify` can give. In-vocabulary by construction here
    # too. (This no longer says `detector.vocabulary` itself is that set --
    # only its saccadic slice is, which is the whole point of slicing.)
    microsaccade_max_deg = params["microsaccade_max_deg"]

    def label_for(start: int, stop: int) -> Label:
        return classify(amplitude(gaze, start, stop), microsaccade_max_deg)

    return label_for


def _min_duration_samples(detector_params) -> int:
    """The floor `_overlapping` inherits from the detector that produced both
    eyes' spans -- the SAME params object the detector actually ran with, not
    a second reading of the paramset dict.

    `getattr` with a default of 1, rather than a bare attribute access,
    because `min_duration_samples` is a field of `EngbertKlieglParams` and
    not part of `registry.Detector.run`'s own contract: stage 2's six other
    detectors (design spec section 3.1) each bring their own params
    dataclass, and some of them have no minimum duration to declare. 1 is
    the honest floor for such a detector -- it would itself have accepted a
    one-sample run, so the rule "never shorter than either eye's own
    detector would have accepted" is satisfied by admitting one here too --
    and it is also the weakest value that keeps `measure`'s `stop > start`
    precondition true.
    """
    return int(getattr(detector_params, "min_duration_samples", 1))


def _params_for(detector, params: dict):
    """`detector`'s own params dataclass, built from the registered paramset
    dict.

    Not `{k: v for k, v in params.items() if k != "detector"}`: the paramset
    is a SHARED vocabulary (`register_default_paramsets`'s own reasoning),
    carrying both a `detector` selector no detector's dataclass declares and
    subsystem-wide keys some declare and others do not. Filtering down to
    exactly the dataclass's OWN declared field names -- rather than naming
    every key to drop -- is what keeps this correct as that vocabulary grows,
    with no detector needing to know what else it should ignore.

    **That filter is also how a shared key REACHES a detector that needs
    it.** `microsaccade_max_deg` is the live case: it is not any one
    detector's parameter (design spec section 3's argument for measuring
    centrally applies just as much to the threshold a measurement is
    compared against), and it is registered once, beside `detector`, at the
    top of the paramset. `EngbertKlieglParams` declares a field of that name
    because Engbert-Kliegl's own declared vocabulary is the amplitude split
    (design spec section 3.1) and it cannot label its intervals without the
    threshold. `OteroMillanParams` declares it for the same reason: reading
    that detector's reference on 2026-09-01 corrected its vocabulary from
    `microsaccade` alone to the same split, so it too consumes the shared
    cut. U'n'Eye, whose declared vocabulary is `saccade` alone, will declare
    no such field and be handed no such value -- a detector with no
    amplitude-derived labels is never forced to accept a parameter it has no
    use for. Declaring the field is the detector's statement that it
    consumes a shared key, not a claim to own one.

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

    **And it is written LAST, after each detector's own defaults, so a
    detector can never shadow it.** A detector that consumes the shared key
    declares a field of that name on its own params dataclass (`_params_for`
    explains why that is how a shared key reaches a detector at all), so
    `asdict` of its defaults carries a `microsaccade_max_deg` of its own.
    Merged in the other order, THAT value would win and each detector would
    quietly get to pick the amplitude cut its own rows are split at --
    exactly the per-detector threshold this paramset shape exists to
    prevent. The two values happen to be equal today, so the ordering is
    load-bearing without being visible in any stored row.
    """
    from dataclasses import asdict

    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS
    from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG
    from wl_preproc.eye.detect.otero_millan import DEFAULT_OM_PARAMS
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS

    paramset.register("eye_validity", asdict(DEFAULT_VALIDITY_PARAMS))
    defaults = {
        "engbert_kliegl": asdict(DEFAULT_EK_PARAMS),
        "otero_millan": asdict(DEFAULT_OM_PARAMS),
    }
    return {
        name: paramset.register(
            "eye_detection",
            {"detector": name, **defaults[name], "microsaccade_max_deg": MICROSACCADE_MAX_DEG},
        )
        for name in DETECTORS
    }


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}detect`. Idempotent."""
    core.activate(prefix=prefix)
    paramset.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}detect", create_tables=True)
