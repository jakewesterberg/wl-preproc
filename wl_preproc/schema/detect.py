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
back, runs the registered detector over each eye independently, and builds
the conjunction via `_conjunction_runs`, which groups both eyes' runs by
KIND before intersecting -- `saccade` and `microsaccade` share one kind,
every other emitted label is its own kind, and `fixation`/`blink`/`invalid`
are never intersected at all -- then calls `_overlapping`, the single-kind
intersection primitive, once per kind and concatenates the result, subject
throughout to the detector's own minimum event duration. Every detected
event is then measured once via `wl_preproc.eye.detect.measure`. That
duration floor is what keeps the criterion a FILTER rather than a generator
-- see `_overlapping`. Design spec `2026-09-05-conjunction-shape-design.md`
section 1 states the per-kind rule this replaces a blind time-only
intersection with, and section 2 states the defect that made the
replacement necessary: an ungrouped intersection would cross a left
`fixation` with a right `saccade` and keep the result, which no stage-1 or
stage-2A test could reach because neither stage registers a detector
emitting more than one kind.

**Labels come from the detector, never from this module -- and the
conjunction's comes from its own measurement, or from the two eyes' own
agreement on kind.** Design spec section 3: "Detectors return labelled
intervals. Shared code measures them." `_insert_trace` writes the label
each interval already carries and measures the final run for storage. The
conjunction has no detector interval to carry one, so within the SACCADIC
kind `_overlapping` labels each intersection by the detector's OWN
labelling rule applied to THAT intersection -- `classify` over its own
amplitude, the same `[start, stop)` on the same gaze that `_insert_trace`
then stores, where the detector declares the whole amplitude split; the
detector's single declared class where it declares half of one
(`_conjunction_label`). **Every other kind labels itself**: when both eyes
independently call a stretch `pso`, or `pursuit`, or `drift`, that agreement
on kind IS the label, with no detector rule to apply and nothing to
arbitrate (`_conjunction_runs`' own `_always(Label(kind))`). Never, in any
kind, by arbitrating between the two eyes' own labels -- see
`_conjunction_label` for the three defects that arbitration caused when it
applied to the saccadic split, and for why half a split is not `classify`'s
question to answer. `_insert_trace` used to assign every span's label
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

from wl_preproc.eye.detect.labels import (
    Label,
    Run,
    kind_of,
    labels_from_runs,
    runs_from_labels,
    true_runs,
)
from wl_preproc.schema import DEFAULT_PREFIX, core, paramset, pipeline

schema = dj.Schema()

_LABEL_ENUM = ",".join(f"'{label.value}'" for label in Label)


class UndecidedConjunctionLabel(ValueError):
    """`_conjunction_label` raises this for the two cases left where a
    conjunction genuinely cannot say what a run's label is: a detector
    declaring an EMPTY vocabulary (no kind at all, saccadic or otherwise, so
    there is nothing to produce a conjunction for), and -- only if actually
    asked, never eagerly -- a detector whose vocabulary has no saccadic label
    when a saccadic conjunction is requested of it.

    **Until 2026-09-05 this was also raised for any vocabulary mixing a
    saccadic and a non-saccadic label**, because one label had to cover the
    whole mixed vocabulary. Per-kind intersection (`_conjunction_runs`,
    design spec `2026-09-05-conjunction-shape-design.md` section 1) means
    each kind labels itself, so that mixed case no longer reaches here at
    all. See `_conjunction_label`."""


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
    # WHICH criterion did it rather than only that something did. The first
    # five match OpenIrisDPI's own five (design spec section 2): eye open,
    # gaze within a plausible region, plausible speed, no frame
    # discontinuity, and short surviving epochs dropped. The sixth,
    # `non_finite`, is a data-integrity guard added 2026-09-27: a gaze,
    # velocity or quality value that is not a finite number.
    #
    # RAW per-criterion counts over all `n_samples`, never apportioned
    # shares: one sample can be rejected by two criteria at once, so these
    # can sum ABOVE the fraction of samples the mask actually rejects -- and
    # all but `short_epoch` are counted before dilation grows each rejected
    # region, so they can sum BELOW it too. `eye/detect/validity.py::
    # ValidityMask` states both in full. `NULL` on a refused row, which has
    # no mask at all, and only there.
    frac_blink=null         : double
    frac_out_of_region=null : double
    frac_too_fast=null      : double
    frac_frame_gap=null     : double
    frac_short_epoch=null   : double
    frac_non_finite=null    : double
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
                # Every one of design spec section 7's "per-criterion
                # rejected fractions" (five, and six since `non_finite`
                # joined on 2026-09-27). Four were `None` here, under
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
        # measurements; every other label leaves them NULL. So does a saccade
        # run too brief for its detector's paramset to measure (NSLR's
        # `min_measured_saccade_ms`, on every trace; `_insert_trace`).
        # `reliability` is Otero-Millan's per-detection index, and for an
        # event Bayesian microsaccade detection found itself its mean
        # posterior probability of a microsaccade (0.5 to 1; BMD design spec
        # section 3.4) -- a different quantity on a different scale. Null for
        # every detector that has neither -- declared now because the
        # migration window closes January.
        amplitude_deg=null       : double
        peak_velocity_deg_s=null : double
        reliability=null         : double
        # Where the event starts and ends, and its direction: the gaze at
        # the two samples `amplitude_deg` is measured between, so the
        # amplitude is exactly their distance. Those samples are the run's
        # first and last, except that NSLR's per-eye saccades end at
        # `gaze[run_stop]` (its landing sample) and BMD's own per-eye events
        # start at `gaze[run_start - 1]` (its take-off sample), each when
        # that sample was offered and is finite (`measure.py::
        # measure_event_run`). Degrees of visual angle,
        # positive x rightward and y upward; the direction counterclockwise
        # from rightward, in [-180, 180], NULL for a zero displacement. On
        # exactly the rows that carry `amplitude_deg` (design spec
        # `2026-09-28-saccade-geometry-design.md`).
        start_x_deg=null   : double
        start_y_deg=null   : double
        end_x_deg=null     : double
        end_y_deg=null     : double
        direction_deg=null : double
        """

    class Source(dj.Part):
        definition = """
        # Which eye each stretch of the CONJUNCTION trace's labels came from
        # (design spec `2026-09-28-both-eyes-fallback-design.md` section 4):
        # `both` where the two-eye rule labelled it, `left` or `right` where
        # that eye alone did -- a one-eye stretch, or a one-eye event kept
        # whole at a mask edge -- and `neither` where no eye was usable.
        # Written for the `conjunction` trace only; the runs tile
        # [0, n_samples), and `source_stop` is EXCLUSIVE.
        # Key: (subject, session_datetime, trace, validity_paramset_type,
        # validity_paramset_idx, paramset_type, paramset_idx, source_index).
        -> master
        source_index : int unsigned
        ---
        source_start : int unsigned
        source_stop  : int unsigned
        source       : enum('both','left','right','neither')
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
            spans[eye_value] = detector.detect(gaze, v, offered, fs_hz, detector_params)
            per_eye[eye_value] = (gaze, v, offered)

        for eye_value in ("left", "right"):
            if eye_value in refused_reason:
                self.insert1({**key, "trace": eye_value, "status": "refused",
                              "reason": refused_reason[eye_value]})
            else:
                gaze, v, offered = per_eye[eye_value]
                self._insert_trace(key, eye_value, gaze, v, offered, spans[eye_value], fs_hz,
                                   detector, detector_params)

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
            # `_conjunction_runs`, not `_overlapping`: the binocular criterion
            # applies WITHIN a kind, so the conjunction trace carries the same
            # vocabulary as the two eyes it is built from. `_overlapping` is
            # the single-kind primitive underneath it.
            floor = _min_duration_samples(detector_params, fs_hz)
            conjunction_spans = _conjunction_runs(
                spans["left"],
                spans["right"],
                floor,
                _conjunction_label(detector, params, gaze),
            )
            # Where only one eye is usable, the trace falls back to it, and an
            # event the other eye could not see whole is kept whole as the
            # eye that did (design spec `2026-09-28-both-eyes-fallback-design.md`,
            # the requester's decisions of 2026-09-28). A gap is filled from the
            # left eye's mask only where NEITHER eye is usable; one usable
            # eye makes the gap that eye's `fixation`.
            intervals, kept, source, fill = _conjunction_parts(
                conjunction_spans, spans["left"], spans["right"], per_eye["left"][2], per_eye["right"][2],
            )
            self._insert_trace(
                key, "conjunction", gaze, v, fill, intervals,
                fs_hz, detector, detector_params,
                kept={(run.start, run.stop): (eye, *per_eye[eye]) for eye, run in kept},
                eyes=per_eye,
            )
            self.Source.insert(
                {**key, "trace": "conjunction", "source_index": index,
                 "source_start": start, "source_stop": stop, "source": value}
                for index, (start, stop, value) in enumerate(_value_runs(source))
            )

    def _insert_trace(self, key, trace, gaze, v, offered, intervals, fs_hz,
                      detector, detector_params, kept=None, eyes=None) -> None:
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

        **How an event run is measured is its detector's declared rule**
        (`measure.py::measure_event_run`; the requester's decisions of
        2026-09-27, NSLR design spec section 4). For every detector but NSLR
        and BMD that rule is `measure`, unchanged, on every trace. BMD's
        runs start one sample after the eye takes off
        (`registry.Detector.runs_start_after_takeoff`), so each of its own
        per-eye events, `microsaccade` or `saccade`, is measured from that
        take-off sample (BMD design spec section 3.5); the saccades it copies
        from Engbert-Kliegl are measured as `measure` measures them
        (`_measured_from_takeoff`). *Until 2026-09-27 this said "every
        detector but NSLR"; true when written.*
        NSLR's runs end one
        sample before the eye lands
        (`registry.Detector.runs_end_before_landing`), so each of its
        per-eye saccade runs is measured up to the landing sample. An NSLR
        saccade run shorter than its paramset's `min_measured_saccade_ms`,
        on any trace, is stored with both measurements NULL, and a NULL here means "too brief to measure",
        never zero. Before that decision `measure` missed the last step of
        every NSLR saccade run, and read a one-sample run as exactly 0.0 deg.
        On the reference recording that was 542 left-eye and 666 right-eye
        rows, at a median peak velocity of 234 deg/s on the left.

        **The conjunction takes the floor, not the landing rule.** Its runs
        are intersections of the two eyes' spans, not one detector's runs,
        so a conjunction span does not end on an NSLR knot and the landing
        rule is off here. The floor applies, by the requester's second
        decision of 2026-09-27: an NSLR conjunction saccade run shorter than
        `min_measured_saccade_ms` is stored with both measurements NULL, and
        a longer one keeps `measure`. The floor was needed here too.
        `_overlapping`'s own floor guarantees only `stop > start`, not a
        nonzero amplitude, and NSLR's is one sample. Before that decision,
        219 of NSLR's 3,230 conjunction saccade rows on the reference
        recording were stored at 0.0 deg, at a median 150 deg/s. Every other
        detector declares no floor, so its conjunction keeps `measure`
        exactly. Whether a conjunction needs a minimum event duration at all
        is still open (NSLR design spec section 8.4).

        *Until the conjunction round of 2026-09-27 this paragraph said the
        conjunction keeps `measure` whatever the detector, so that NSLR's
        could still store one-sample saccades at 0.0 deg, open for the
        requester. Superseded by the requester's decision that day; true
        when written.*

        Five of the six registered detectors make that second measurement
        redundant on their own: `labels.py::true_runs` only ever
        returns MAXIMAL runs and `otero_millan.py::_merge` guarantees a gap,
        so two of `intervals` are always separated by at least one sample
        neither claims, and `runs_from_labels` can therefore never merge two
        of THEIR call's own intervals into one run. REMoDNaV's saccade and
        the `pso` after it are adjacent, as Nystrom-Holmqvist's are, but its
        proximity rule means it never emits two adjacent runs with the same
        label, so `runs_from_labels` never merges two of its intervals
        either. NSLR's runs within a piece are adjacent too, but
        `detect_nslr` merges consecutive segments that share a state before
        returning, and two pieces are always separated by at least one
        sample no run claims, so it never emits two adjacent runs with the
        same label either. BMD's runs within a fixation stretch come from
        `true_runs`, so they are maximal, and every own event is bordered by
        `drift`: each chain's first and last runs, which are excluded from
        the probability, always cover a stretch's first and last samples.
        Two stretches are always separated by at least one sample that is a
        copied Engbert-Kliegl saccade or unclaimed. So BMD never emits two
        adjacent runs with the same label either.

        *Until BMD was registered (2026-09-27, `spec/bmd`) this said "Four of
        the five registered detectors" and did not mention BMD; true when
        written.*

        *Until 2026-09-27 this said "Two of the three registered detectors"
        and did not mention REMoDNaV; true when written.*

        *Until NSLR's final fix wave (2026-09-27) this said "Three of the
        four registered detectors" and did not mention NSLR; true when
        written.*

        **The remaining one does not, and this is no longer a hypothetical
        about some future detector -- it is true of Nystrom-Holmqvist today.**
        `nystrom_holmqvist.py::_glissade_bounds` returns `(saccade_offset,
        stop)` for its glissade, so a saccade and the `pso` that follows it
        are ADJACENT with no gap between them; and two SACCADE candidates
        whose independent bounds searches land exactly `S1.stop == S2.start`
        apart do not overlap under `_merged_bounds`' own half-open test
        (`run.start < offset and onset < run.stop`, both strict), so both
        survive as separate, touching runs carrying the SAME label. Nothing
        in `registry.py::DetectFn`'s own contract requires ANY detector --
        registered or still unwritten (U'n'Eye; BMD until 2026-09-27) -- to
        leave such a gap, and if two adjacent intervals ever DO carry the
        same label, `runs_from_labels` merges them into one run whose real
        `[start, stop)` matches neither original interval. Measuring the
        FINAL run rather than trusting either input interval is what keeps
        the stored measurement correct regardless of whether that gap holds
        -- for Nystrom-Holmqvist, that is not insurance against a future
        case, it is load-bearing today.

        *Until 2026-09-27 this said "The third does not" and listed REMoDNaV
        as still unwritten; true when written.*

        *Until NSLR's final fix wave (2026-09-27) this listed NSLR as still
        unwritten; true when written, before NSLR was registered on
        `spec/nslr`.*

        **For `conjunction` that gap is guaranteed rather than inherited, and
        it now holds WITHIN a kind by construction and ACROSS kinds by a fact
        stated nowhere else: no two different kinds ever share a label.**
        `labels.py::KIND_OF` maps `saccade`/`microsaccade` to
        `"saccadic"` and every other kind to its own single label (`pso` ->
        `pso`, `pursuit` -> `pursuit`, `drift` -> `drift`), so the label sets
        the four kinds can
        produce -- `{saccade, microsaccade}`, `{pso}`, `{pursuit}`,
        `{drift}` -- are pairwise disjoint. Two runs from DIFFERENT
        kind-groups can therefore sit adjacent in `_conjunction_runs`'s
        concatenated, re-sorted output without ever sharing a label, so
        `runs_from_labels` can never merge across a kind boundary. WITHIN one
        kind the guarantee is `_overlapping`'s own: it coalesces touching
        intersections before labelling them, so no two of its returned spans
        ever touch. Either way it has to hold: the conjunction's label is
        `classify` over the amplitude of the interval it was assigned to
        (or, for a non-saccadic kind, that kind's own label), so a merge that
        moved the boundaries would store a label and an amplitude that
        disagree -- the exact defect this round exists to close. The run
        measured here is always the span labelled there.

        *Since the both-eyes fallback (design spec
        `2026-09-28-both-eyes-fallback-design.md`) the conjunction's
        intervals also include own-eye runs kept whole
        (`_conjunction_parts`), each carrying its own eye's label and
        measured as that eye's row (`kept`). Kept runs from the two eyes
        never overlap or touch (`_conjunction_fallback`'s clash rule), and a
        two-eye span inside a kept run gives way to it. One merge remains
        possible: a kept run touching a same-label kept run or two-eye span
        from the adjacent run of its OWN eye. That eye's own trace merges
        the two the same way. Such a run is measured on the eye usable
        throughout it (`eyes`, spec section 3), and for a detector
        declaring the amplitude split its label is its parts' label, not
        `classify` over the merged amplitude -- as in that eye's own trace.
        The final review of 2026-09-28 found the rule before it split one
        event into two rows instead (its C1); true of the code before that
        fix.*

        **`reliability` is mapped back onto the final runs by EXACT
        `(start, stop)` match, and is `None` for anything else.** It is a
        per-DETECTION value (Otero-Millan's own silhouette, design spec
        section 5), and the re-derivation above is precisely what can leave a
        final run corresponding to no single detector interval: two adjacent
        intervals carrying the same label merge into one run whose span
        matches neither. Attributing either half's reliability to that run
        would put a fabricated number in the one column a reader consults to
        decide how much to trust a detection -- so the map simply misses and
        `None` is stored, which is the honest answer. The conjunction trace's
        two-eye runs get `None` for the same reason they get their label
        derived rather than checked: no detector produced them. A one-eye
        event the conjunction keeps (`kept`) is that eye's own run, and
        carries its own reliability (design spec
        `2026-09-28-both-eyes-fallback-design.md` section 2).
        """
        from wl_preproc.eye.detect.measure import measure_event_run

        # Read the way `_min_duration_samples` reads a detector's params: a
        # field only NSLR's params declare, so every other detector has no
        # floor on any trace.
        min_measured_ms = getattr(detector_params, "min_measured_saccade_ms", None)

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

        # A one-eye event kept in the conjunction (`kept`, keyed by span) is
        # measured exactly as its own eye's row is: that eye's gaze, velocity
        # and mask, and that eye's rules. Every other conjunction run, given
        # both eyes' inputs (`eyes`), is measured on the eye usable
        # throughout it, the left first; with neither, it is stored
        # unmeasured (design spec `2026-09-28-both-eyes-fallback-design.md`
        # section 3).
        kept = kept or {}

        def _measured_on(run: Run):
            span = (run.start, run.stop)
            if span in kept:
                return kept[span]
            if eyes is None:
                return (trace, gaze, v, offered)
            for eye in ("left", "right"):
                eye_gaze, eye_v, eye_offered = eyes[eye]
                if all(label is None for label in eye_offered[run.start:run.stop]):
                    return (trace, eye_gaze, eye_v, offered)
            return None

        def _run_row(index: int, run: Run) -> dict:
            measurement = None
            source = _measured_on(run) if run.label in (Label.SACCADE, Label.MICROSACCADE) else None
            if source is not None:
                span = (run.start, run.stop)
                measured_as, run_gaze, run_v, run_offered = source
                # The landing rule is per-eye only: a two-eye span is an
                # intersection and does not end on a detector's knot. The
                # floor applies to every trace (see this docstring's
                # conjunction paragraph).
                measurement = measure_event_run(
                    run_gaze, run_v, run_offered, run.start, run.stop, fs_hz,
                    runs_end_before_landing=(detector.runs_end_before_landing
                                             and measured_as != "conjunction"),
                    min_measured_ms=min_measured_ms,
                    runs_start_after_takeoff=_measured_from_takeoff(
                        detector, measured_as, run.label, reliability_by_span.get(span)
                    ),
                )
            return {
                **row, "run_index": index, "run_start": run.start, "run_stop": run.stop,
                "label": run.label.value,
                **{column: None if measurement is None else getattr(measurement, column)
                   for column in _STORED_MEASUREMENTS},
                "reliability": reliability_by_span.get((run.start, run.stop)),
            }

        self.Run.insert(_run_row(index, run) for index, run in enumerate(runs))


#: The `Measurement` fields `EyeDetection.Run` stores, each under its own
#: name, all NULL for a run that is not measured. `duration_s` is not stored:
#: it is `(run_stop - run_start) / fs_hz` on every row.
_STORED_MEASUREMENTS = (
    "amplitude_deg", "peak_velocity_deg_s",
    "start_x_deg", "start_y_deg", "end_x_deg", "end_y_deg", "direction_deg",
)


def _measured_from_takeoff(detector, trace: str, label: Label, reliability: float | None) -> bool:
    """Whether a stored run is measured from its take-off sample: BMD's
    take-off rule (design spec `2026-09-27-bmd-design.md` section 3.5), per
    eye only for the same reason as NSLR's landing rule.

    It applies to BMD's OWN events, `microsaccade` or, at the amplitude cut
    or above, `saccade` (the requester's decision of 2026-09-27, final
    review I1). They are the ones carrying a reliability, their mean
    posterior probability (`bmd.py::_by_size`). The saccades BMD copies from
    Engbert-Kliegl carry none and are measured as `measure` measures them.
    *Until then it applied to `microsaccade` runs only; true when written.*"""
    return (
        detector.runs_start_after_takeoff
        and trace != "conjunction"
        and label in (Label.SACCADE, Label.MICROSACCADE)
        and reliability is not None
    )


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
    already supplies that separation (`labels.py::true_runs`
    returns maximal runs, and intersecting two separated families keeps them
    separated), but `registry.py::DetectFn`'s contract does not require it,
    and agreement between a stored label and a stored amplitude must not
    rest on a property only one detector happens to have. The sort is for
    the same reason: `left` and `right` arrive sorted from today's detector,
    and nothing in that contract says they must.

    *Since the both-eyes fallback (design spec
    `2026-09-28-both-eyes-fallback-design.md`) these spans are not the
    conjunction's only intervals: `_conjunction_parts` adds the own-eye runs
    it keeps whole, and a span inside one gives way to it. The guarantee
    above is between these spans; `_insert_trace`'s docstring states what
    holds between a span and a kept run.*

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

    **This function sees ONE kind.** `_conjunction_runs` groups both eyes'
    runs by kind and calls this once per group, so the two eyes' labels ARE
    consulted -- one level up, to decide what intersects with what. Within a
    kind they are not, and `label_for` remains the only source of a label.
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
# one is a DEGENERATE split.** U'n'Eye (`{saccade}`) and the three
# `pso`-capable detectors each declare one side of the cut and cannot emit
# the other, so `classify` -- which answers both sides for any detector --
# would put a word in their mouths that `registry.Detector.detect` refuses
# from the detector itself. Four of the seven land there; Engbert-Kliegl,
# Otero-Millan and Bayesian microsaccade detection declare both sides. See
# `_conjunction_label`, which reads this set rather than restating it.
#
# *Until 2026-09-27 this gave BMD as `{microsaccade, drift}`, one of five on
# the degenerate branch. BMD as built declares `{saccade, microsaccade,
# drift}`, because it stores Engbert-Kliegl's saccades as its own (BMD
# design spec section 4); true when written.*
#
# **This set is no longer a gate on whether a conjunction can be built at
# all.** It was, until 2026-09-05: a vocabulary that was not a subset raised,
# because ONE label had to cover a mixed vocabulary. Per-kind intersection
# (`_conjunction_runs`) means each kind labels itself, so the mixed case does
# not arise and there is nothing left to refuse.
_AMPLITUDE_DERIVED_VOCABULARY = frozenset({Label.SACCADE, Label.MICROSACCADE})


def _always(label: Label) -> Callable[[int, int], Label]:
    """A `label_for` answering one label whatever the span.

    Two callers. `_conjunction_runs` uses it for every kind that labels
    itself -- one that is not the amplitude split has exactly one label, so
    there is no rule to apply. The duration-floor tests use it where the
    FLOOR is what is under test and which label a surviving span carries is
    not."""
    return lambda _start, _stop: label


def _conjunction_parts(two_eye, left, right, left_offered, right_offered):
    """Everything the conjunction trace is built from besides its
    measurements: its intervals, the one-eye runs kept whole, which eye each
    sample draws on, and the fill (design spec
    `2026-09-28-both-eyes-fallback-design.md`). `two_eye` is
    `_conjunction_runs`' output; `left_offered` is also the left eye's mask,
    whose label fills a sample where NEITHER eye is usable -- one usable eye
    makes a gap that eye's `fixation`.

    **A two-eye span gives way to a kept run wherever the kept run covers
    it.** The span is the intersection of that run with the other eye's, so
    the kept run is the same event, whole. Storing both would split one
    event into two rows -- the intersection, labelled from its own
    amplitude, and a one-eye fragment beside it, whose label could
    contradict its own (the final review's C1, 2026-09-28). A span reaches
    past its kept run only where `_conjunction_runs` coalesced it across two
    touching runs of one eye; the part over the other run stands. It keeps
    the span's label, touches the kept run, and is stored with it as one
    run when the two share a label, as that eye's own trace stores its two
    runs. Six such spans on the reference recording, all
    Nystrom-Holmqvist's, whose label does not depend on amplitude.

    **Where one eye alone is usable, the kept runs ARE that eye's events**:
    every run of a carried kind there has the other eye withheld during it.
    Between them the fill paints `fixation`, as it paints a both-usable
    gap. A run dropped by `_conjunction_fallback`'s clash rule leaves
    `fixation` over its one-eye stretch.

    Returns `(intervals, kept, source, fill)`, `kept` as
    `_conjunction_fallback` returns it."""
    kept, source = _conjunction_fallback(left, right, left_offered, right_offered)
    in_kept = np.zeros(len(source), dtype=bool)
    for _, run in kept:
        in_kept[run.start:run.stop] = True
    fill = np.array(
        [label if source_label == "neither" else None for label, source_label in zip(left_offered, source)],
        dtype=object,
    )
    intervals = [Run(span.start + start, span.start + stop, span.label)
                 for span in two_eye for start, stop in true_runs(~in_kept[span.start:span.stop])]
    return [*intervals, *(run for _, run in kept)], kept, source, fill


def _conjunction_fallback(left, right, left_offered, right_offered):
    """The own-eye runs the conjunction keeps whole, and which eye each of
    its samples draws on (design spec `2026-09-28-both-eyes-fallback-design.md`
    sections 2 and 4).

    **An own-eye run is kept, whole,** when its kind is one the conjunction
    carries and the other eye's mask withholds at least one of its samples:
    the other eye could not have seen all of it. That holds whether or not
    the other eye saw part of it -- a two-eye event whose other eye drops
    out mid-flight is kept as the eye that saw it whole
    (`_conjunction_parts`). Two such runs from the two eyes that overlap or
    touch are both dropped. Over an overlap both eyes were usable and named
    two events; touching runs of one label would merge into one stored run.

    *Until the final review of 2026-09-28 a run was kept only when the
    other eye had no same-kind run overlapping it by the detector's floor.
    That split a two-eye event whose other eye dropped out mid-flight into
    two stored rows; true when written.*

    Returns `(kept, source)`: `kept` as `(eye, run)` in time order, and
    `source` a per-sample object array of `both`, `left`, `right` or
    `neither` -- which eyes were usable, with each kept run marked by its
    own eye along its whole length."""
    usable = {"left": np.array([label is None for label in left_offered], dtype=bool),
              "right": np.array([label is None for label in right_offered], dtype=bool)}
    runs = {"left": sorted(left, key=lambda run: run.start), "right": sorted(right, key=lambda run: run.start)}
    withheld_before = {eye: np.concatenate(([0], np.cumsum(~usable[eye]))) for eye in usable}

    candidates = {"left": [], "right": []}
    for eye, other_eye in (("left", "right"), ("right", "left")):
        for run in runs[eye]:
            if (kind_of(run.label) is not None
                    and withheld_before[other_eye][run.stop] > withheld_before[other_eye][run.start]):
                candidates[eye].append(run)

    candidate_stops = {eye: np.array([run.stop for run in candidates[eye]], dtype=np.int64)
                       for eye in candidates}

    def clashes(run, other_eye) -> bool:
        index = int(np.searchsorted(candidate_stops[other_eye], run.start, side="left"))
        return index < len(candidates[other_eye]) and candidates[other_eye][index].start <= run.stop

    kept = sorted(
        [(eye, run) for eye, other_eye in (("left", "right"), ("right", "left"))
         for run in candidates[eye] if not clashes(run, other_eye)],
        key=lambda pair: pair[1].start,
    )

    source = np.full(len(usable["left"]), "neither", dtype=object)
    source[usable["left"] & usable["right"]] = "both"
    source[usable["left"] & ~usable["right"]] = "left"
    source[~usable["left"] & usable["right"]] = "right"
    for eye, run in kept:
        source[run.start:run.stop] = eye
    return kept, source


def _value_runs(values: np.ndarray) -> list[tuple[int, int, object]]:
    """Maximal `(start, stop, value)` runs of a per-sample array, tiling it."""
    runs, start = [], 0
    for index in range(1, len(values) + 1):
        if index == len(values) or values[index] != values[start]:
            runs.append((start, index, values[start]))
            start = index
    return runs


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
    of thing -- true of Engbert-Kliegl and Otero-Millan, and false for
    Nystrom-Holmqvist (registered 2026-09-06), for REMoDNaV (registered
    2026-09-26), for NSLR (registered 2026-09-27, on `spec/nslr`) and for
    BMD (registered 2026-09-27, on `spec/bmd`; until then this called it
    the one detector still BLOCKED, unwritten). Nystrom-Holmqvist, NSLR
    and REMoDNaV all emit `pso` and `fixation` alongside `saccade`; BMD
    emits `drift` instead of `pso`. `fixation` TILES the recording, so an
    ungrouped intersection would have crossed a left fixation with a right
    saccade and kept it.

    *Until 2026-09-27 this listed REMoDNaV among three detectors still
    BLOCKED (unwritten); true when written.*

    *Until NSLR's final fix wave (2026-09-27) this listed NSLR as still
    BLOCKED (unwritten); true when written, before NSLR was registered.*

    **Grouping first also makes the loop cheaper -- though no longer for
    every registered detector.** `_overlapping` is `O(|left| x |right|)`;
    summing that over kinds is strictly less than the product of the totals
    whenever more than one kind is present, and identical when only one is
    -- which is the case for Engbert-Kliegl and Otero-Millan (each single-
    kind, `saccadic` alone), whose rows are therefore unchanged. It is NOT
    the case for Nystrom-Holmqvist: its own per-eye trace can carry both a
    `saccadic` run and a `pso` run at once, so `by_kind` genuinely has more
    than one entry there, and grouping does real algorithmic work rather
    than costing nothing extra for it, for the first time among the
    registered detectors.

    Not folded into `_overlapping`: that function is the single-kind
    primitive and every one of its call sites, in production and in tests,
    passes single-kind input. Keeping the two separate is what lets the H3
    duration-floor tests keep testing the floor rather than the grouping."""
    by_kind: dict[str, tuple[list[Run], list[Run]]] = {}
    for side, runs in ((0, left), (1, right)):
        for run in runs:
            kind = kind_of(run.label)
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
    generalization, for the three `pso`-capable detectors as well -- all
    three reach THIS branch, degenerately, because none of them declares
    both `saccade` and `microsaccade`. (*Until 2026-09-27 this also named
    BMD's `{microsaccade, drift}`; BMD as built declares both sides and
    reaches `classify`. True when written.*)

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
    by neither eye's opinion nor by `classify` (`labels.py::KIND_OF`), and
    `fixation` is not intersected at all, being the synthesized background
    rather than a detector's finding (`labels.py::NOT_INTERSECTED`). None of
    the four detectors it blocked needs THIS function to say anything about
    `pso`, `pursuit`, `drift` or `fixation` any more -- only about the
    SACCADIC SLICE of its vocabulary, which is what
    `_AMPLITUDE_DERIVED_VOCABULARY`'s own comment and the code below this
    docstring now compute.

    *Until 2026-09-27 this said "the four blocked detectors"; true when
    written, before Nystrom-Holmqvist and REMoDNaV were registered.*

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
    # declares `{saccade, pso, fixation}`: not size one, yet it makes only
    # half the amplitude cut. Testing the whole vocabulary -- which is what
    # stage 1 did, correctly, when the whole vocabulary WAS the cut -- would
    # send it to `classify` and put a label in its mouth that
    # `registry.Detector.detect` refuses from the detector itself. (*Until
    # 2026-09-27 this also gave Bayesian microsaccade detection as
    # `{microsaccade, drift}`; as built it declares both halves of the cut.
    # True when written.*)
    #
    # Four of the seven detectors land here; Engbert-Kliegl, Otero-Millan
    # and Bayesian microsaccade detection reach `classify`. The degenerate
    # branch arrived as a fix-round finding about U'n'Eye and is now the
    # majority path. *Until 2026-09-27 this said "Five of the seven" and left
    # BMD out of those reaching `classify`; true when written.*
    saccadic = detector.vocabulary & _AMPLITUDE_DERIVED_VOCABULARY

    if not saccadic:
        # No saccadic label at all, so `_conjunction_runs` builds no saccadic
        # group and never calls this IN PRODUCTION -- but that guarantee is
        # not a property of this function or of `_conjunction_runs`'s
        # grouping (`by_kind` keys off each RUN's own label via `kind_of`,
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


def _min_duration_samples(detector_params, fs_hz: float) -> int:
    """The floor `_overlapping` inherits from the detector that produced both
    eyes' spans -- the SAME params object the detector actually ran with, not
    a second reading of the paramset dict.

    **The detector's own declared minimum, and never less than 2 samples**
    (the requester's decision of 2026-09-28):
    - `min_duration_samples` where the params declare it (Engbert-Kliegl's
      6);
    - otherwise `min_saccade_duration_ms`, counted in samples as the
      detectors that declare it count it, `round(ms * fs_hz / 1000)`
      (Nystrom-Holmqvist's and REMoDNaV's 10 ms: 5 samples at the rig's
      498.55 Hz);
    - otherwise nothing, so the lab-wide 2.

    A single-sample event's amplitude is 0.0 deg by construction
    (`measure`'s `gaze_deg[stop - 1] - gaze_deg[start]`), so no two-eye
    event may be one sample, whatever its detector would accept.

    *Until 2026-09-28 only `min_duration_samples` was read, with a default
    of 1: the other five detectors' two-eye events could be one sample
    long, and Nystrom-Holmqvist's and REMoDNaV's 10 ms minimums, declared
    in milliseconds, were never seen. On the reference recording that
    stored 3-26 two-eye saccades per detector at exactly 0.0 deg; true when
    written.*
    """
    declared = getattr(detector_params, "min_duration_samples", None)
    if declared is None:
        minimum_ms = getattr(detector_params, "min_saccade_duration_ms", None)
        declared = 1 if minimum_ms is None else int(round(minimum_ms * fs_hz / 1000.0))
    return max(int(declared), 2)


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

    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS

    paramset.register("eye_validity", asdict(DEFAULT_VALIDITY_PARAMS))
    return {
        name: paramset.register("eye_detection", _eye_detection_params(detector))
        for name, detector in DETECTORS.items()
    }


def _eye_detection_params(detector) -> dict:
    """One detector's `eye_detection` paramset dict: its own declared
    defaults, plus the keys the whole subsystem shares.

    **Split out of `register_default_paramsets` so the merge ORDER has a test
    of its own.** That order is load-bearing and invisible in every stored
    row -- see the shared-key paragraph below -- and the two values it
    arbitrates between are equal today, so reversing it would fail nothing.
    `test_the_shared_threshold_still_wins_over_a_detectors_own_field` is what
    would.

    **The defaults come from `registry.Detector.defaults`, never from a table
    keyed by detector name here.** They lived in exactly such a table until
    2026-09-05, which made them a THIRD thing that had to agree with the
    registry and with the registered paramsets while being checked against
    neither: a detector present in `DETECTORS` and absent from that dict
    raised `KeyError` inside a dict comprehension, uncaught, from
    `daemon.run_once()` -- before `reap_stale_jobs` and before the
    try-wrapped `_computed_tables()` loop, so the entire pass died rather
    than the one detector. Design spec section 3.1 plans five more detectors;
    it would have fired on the first.

    **`microsaccade_max_deg` is written LAST, after the detector's own
    defaults, so a detector can never shadow it.** A detector that consumes
    the shared key declares a field of that name on its own params dataclass
    (`_params_for` explains why that is how a shared key reaches a detector at
    all), so `asdict` of its defaults carries a `microsaccade_max_deg` of its
    own. Merged in the other order THAT value would win and each detector
    would quietly get to pick the amplitude cut its own rows are split at --
    exactly the per-detector threshold this paramset shape exists to prevent.

    `detector` is the whole `registry.Detector`, not its name, because the
    name alone is what required a lookup table in the first place."""
    from dataclasses import asdict

    from wl_preproc.eye.detect.measure import MICROSACCADE_MAX_DEG

    return {
        "detector": detector.name,
        **asdict(detector.defaults),
        "microsaccade_max_deg": MICROSACCADE_MAX_DEG,
    }


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}detect`. Idempotent."""
    core.activate(prefix=prefix)
    paramset.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}detect", create_tables=True)
