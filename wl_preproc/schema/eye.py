# wl_preproc/schema/eye.py
"""How each session's eye calibration was obtained, and its quality.

Design spec `docs/superpowers/specs/2026-08-30-eye-ohdpi-calibration-and-gaze-
design.md` section 6. Calibration is fitted from this session's own targets,
borrowed from the map in use online during acquisition, carried forward from
another session the same day,
or refused (section 3.5's chain, `wl_preproc.eye.calibration.
CalibrationSource`). A refused calibration is a first-class outcome with a
stated reason -- not an error, and never a fabricated map -- which is why the
six affine parameters below are nullable rather than the row simply not
existing: "we could not calibrate this session, and here is why" has to be
expressible, the same discipline `schema/archive.py`'s own "not checked" vs.
"checked, nothing found" distinction protects.

**Task 10: both tables now define `make()`, and a real `key_source`.** Task 9
left both empty on purpose -- a `dj.Computed` table with no `make()` yet
would otherwise raise on every session already landed the moment it joined
`daemon._computed_tables()`, and an empty `key_source` is what kept that
harmless in the meantime (that reasoning is preserved in this file's git
history, on each class's own former `key_source`). This module is what
replaces both. `EyeCalibration.make()` re-decodes
the session's own sync-box code stream to recover the `TARGET_POSITION`
payloads and the `FIXATION_ACQUIRED`/`FIXATION_END` window they bound (design
spec section 4.3), samples the raw ohDPI Purkinje signal over that same
window via `core.Segment`'s own session-time alignment, and runs the result
through `wl_preproc.eye.calibration.resolve_calibration`. `EyeQuality.make()`
reads tracking loss and blink rate straight off the raw file's own
`DataQuality` column.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import datajoint as dj
import numpy as np

from wl_preproc.contracts.events import (
    Escape,
    PayloadEvent,
    SimpleEvent,
    TargetRole,
    TaskEvent,
    TaskTypeCode,
    decode_dva,
    decode_stream,
)
from wl_preproc.eye.calibration import (
    CalibrationMap,
    CalibrationModel,
    CalibrationSource,
    _conditioning,
    apply_map,
    basis,
    n_terms,
    read_online_map,
    resolve_calibration,
    validate_map,
)
from wl_preproc.eye.gaze import purkinje_vector
from wl_preproc.eye.ohdpi import read_columns
from wl_preproc.schema import DEFAULT_PREFIX, core, pipeline

schema = dj.Schema()

# DataQuality is exactly 50*P1_valid + 50*P4_valid (design spec section 1.1),
# matching `eye/gaze.py`'s own `_FULL_TRACKING_QUALITY`: anything short of
# this means at least one Purkinje image failed on that frame. Restated here
# rather than imported -- it is a private name in that module, and the value
# is the frozen recording format's, not a choice free to drift between the
# two places it is read.
_FULL_TRACKING_QUALITY = 100

# `EyeCalibration.reason` is `varchar(255)` (that class's own `definition`,
# below). Named against the column's own declared width, the same pattern
# `ingest/landing.py`'s `INGESTION_SESSION_DIR_MAX_LEN`/
# `QUARANTINE_SESSION_DIR_MAX_LEN` use for `Ingestion`/`Quarantine.
# session_dir` -- a magic `255` repeated at every call site is exactly the
# kind of number free to drift from the column it is supposed to describe.
#
# This is not a defensive margin against a hypothetical: a combined reason
# CAN exceed it for real. `calibration.py::fit_map`'s own
# collinear/coincident-target message is 221 characters on its own;
# `resolve_calibration`'s "; no fallback map validated" suffix makes 248;
# and `EyeCalibration.make()`'s own coverage-drop note (below) adds another
# ~45-50 -- 295+ characters against this 255-character column, for an
# entirely ordinary combination (a low-variety fixation task plus a
# recording coverage gap), not a contrived edge case. This project's own
# MySQL 8 test container runs in strict mode (`tests/schema/test_core.py::
# test_only_known_systems_are_accepted` already pins this for an
# out-of-range enum; the identical strictness applies to an over-length
# varchar), so an unbounded write here does not silently truncate --
# it raises `pymysql.err.DataError`, which `daemon.run_once()`'s
# `suppress_errors=True` turns into a daemon error instead of the graceful
# `refused`-with-a-reason row this module's own docstring calls a
# first-class outcome.
_REASON_MAX_LEN = 255

# Appended to a `reason` that had to be cut, so a reader can tell a
# truncated reason from a complete one rather than silently receiving a
# shorter string indistinguishable from the truth.
_REASON_TRUNCATED_SUFFIX = " [reason truncated]"


def _bounded_reason(text: str, max_len: int = _REASON_MAX_LEN) -> str:
    """`text`, shortened to fit `max_len` if it does not already, cutting
    from the END and marking the cut with `_REASON_TRUNCATED_SUFFIX`.

    Deliberately NOT `landing._clamp_key`'s digest-suffix scheme: that
    function's content digest exists so two DIFFERENT overlong values
    cannot truncate to the IDENTICAL string when the result is also a
    PRIMARY KEY written with `replace=True` -- there, a collision silently
    OVERWRITES and loses an entire row. `reason` is an ordinary column
    here, never a key, never looked up by value, and never written with
    `replace=True`; two rows sharing an identical truncated `reason` loses
    nothing a database operation could silently destroy. Only a marker
    that the stored text is incomplete is needed, and this is that marker.
    """
    if len(text) <= max_len:
        return text
    keep = max(max_len - len(_REASON_TRUNCATED_SUFFIX), 0)
    return text[:keep] + _REASON_TRUNCATED_SUFFIX


def _combine_reason(primary: str, note: str) -> str:
    """Combine `resolve_calibration`'s own `primary` reason (or `""`) with
    this method's own coverage-drop `note` (or `""`) into one string
    guaranteed to fit `_REASON_MAX_LEN`.

    **`primary` is truncated FIRST, from its own end -- not the already-
    joined string, from ITS end.** `note` is always short (`{n} of {m}
    fixation windows had no ohDPI coverage`, bounded by how many windows a
    real session's own code stream can hold, realistically well under 60
    characters), so reserving its own room and truncating `primary` to
    whatever remains is what keeps BOTH causes visible in the stored text:
    `primary`'s own beginning -- its most load-bearing text -- survives,
    and `note` is never silently dropped entirely the way slicing the
    combined string from the end would drop it (`note` is always the LAST
    thing appended, and `primary` alone already reaches 248 characters in
    a real refusal -- see `_REASON_MAX_LEN`'s own comment -- so a plain
    `combined[:255]` would cut `note` in full, every time, precisely when
    there are two distinct causes worth a reader seeing both of).
    """
    if not primary:
        return note
    if not note:
        return _bounded_reason(primary)
    joiner = "; "
    budget = max(_REASON_MAX_LEN - len(joiner) - len(note), 0)
    return f"{_bounded_reason(primary, budget)}{joiner}{note}"


# `basis(_, SECOND_ORDER)`'s own column order, which the affine basis is the
# first three of. One tuple, used to build BOTH the column names and the
# read-back, so a column can never be named for one basis term and filled from
# another.
_COEFF_SUFFIXES = ("const", "dx", "dy", "dx2", "dy2", "dxdy")


def _coefficient_columns(map_: CalibrationMap | None) -> dict[str, float | None]:
    """The twelve `gx_`/`gy_` columns for `map_`, or all NULL for no map.

    `n_terms(map_.model)` decides how many are filled; the rest stay NULL.
    Nothing here counts, slices or reshapes by a literal -- an affine map
    simply has three coefficients per axis and runs out, which is the same
    fact `CalibrationMap.__post_init__` already enforces.
    """
    columns: dict[str, float | None] = {
        f"g{axis}_{suffix}": None for axis in ("x", "y") for suffix in _COEFF_SUFFIXES
    }
    if map_ is None:
        return columns
    suffixes = _COEFF_SUFFIXES[: n_terms(map_.model)]
    for axis, coefficients in (("x", map_.x), ("y", map_.y)):
        # `strict=True` against the model's OWN term count, not against the
        # full six: it checks the map, and a map whose tuples disagree with
        # its model raises here rather than writing a short row.
        for suffix, value in zip(suffixes, coefficients, strict=True):
            columns[f"g{axis}_{suffix}"] = float(value)
    return columns


def _map_from_row(row: dict) -> CalibrationMap | None:
    """A stored row back into a `CalibrationMap`, branching on
    `calibration_model` and never on which columns happen to be NULL.

    A refused row has no model and no map. Anything else reads exactly
    `n_terms(model)` coefficients per axis, in `_COEFF_SUFFIXES` order.
    """
    if row["calibration_model"] is None:
        return None
    model = CalibrationModel(row["calibration_model"])
    suffixes = _COEFF_SUFFIXES[: n_terms(model)]
    return CalibrationMap(
        model=model,
        x=tuple(row[f"gx_{suffix}"] for suffix in suffixes),
        y=tuple(row[f"gy_{suffix}"] for suffix in suffixes),
        n_points=row["n_points"],
        conditioning=row["conditioning"] if row["conditioning"] is not None else float("nan"),
    )


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
    calibration_source : enum('fitted','online','carried_forward','refused')
    # WHAT SHAPE the map is, a different question from whose it is. NULL only
    # when refused. **Read this before any coefficient column below** --
    # `SystemTimebase.fit_status`'s own phrasing, and the same relationship:
    # the coefficients are this column's payload.
    #
    # This column is the AUTHORITY, never a derivation. Null quadratic columns
    # would IMPLY affine, but deriving the model from the nulls would put one
    # fact in two places -- the defect this repository names most often -- and
    # would silently misread a second-order fit whose `gx_dx2` happened to
    # come back exactly zero.
    calibration_model=null : enum('affine','second_order')
    # The coefficients, one column per basis term it multiplies, NULL when
    # refused. `gx_` is the gaze-x axis, `gy_` the gaze-y; the suffixes are
    # `basis(_, SECOND_ORDER)`'s own column order, and the affine basis is
    # that basis's first three columns -- so on an affine-tier fit the six
    # quadratic columns are NULL and the first three carry the whole map.
    #
    # Named columns rather than a blob because this is what makes the
    # nonlinearity queryable: "which sessions have a large dx^2 term" is the
    # question to ask when deciding whether it is real on this lab's own rig,
    # and it is a WHERE clause here and a full-table scan plus a decode
    # otherwise.
    #
    # A refused calibration is a first-class outcome with a stated reason --
    # not an error, and never a fabricated map.
    gx_const=null : double
    gx_dx=null    : double
    gx_dy=null    : double
    gx_dx2=null   : double
    gx_dy2=null   : double
    gx_dxdy=null  : double
    gy_const=null : double
    gy_dx=null    : double
    gy_dy=null    : double
    gy_dx2=null   : double
    gy_dy2=null   : double
    gy_dxdy=null  : double
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
        """Landed sessions with some ohDPI recording on record, and a
        completed event decode. Design spec section 6: "the session
        restricted to those with an ohDPI recording and assembled events,
        since calibration needs target positions from the decoded code
        stream."

        **"An ohDPI recording" is deliberately the COARSE, table-level
        signal -- `core.AcquisitionSystem`, exactly what `SystemTimebase.
        key_source` (this repo's own house style for this decision) joins
        against -- not a finer filesystem check.** A session can clear this
        and still have nothing `make()` can use: `AcquisitionSystem` records
        ANY non-marker file under `ohdpi/` (`ingest/discover.py`'s
        `_has_content`), satisfied by a stray `<session>-events.txt` alone
        even when the genuine per-frame recording `wl_preproc.timebase.
        extract.find_recordings` would need is entirely absent. `make()`
        below is what turns that gap into an explicit `refused` row with its
        own reason, via `core.Segment` -- which already resolves the
        fine-grained question of whether a usable, aligned recording exists
        -- rather than `key_source` silently absorbing it and leaving the
        session with no row and no explanation at all (Controller ruling D:
        "no ohDPI file" needs a reason distinguishable from a degenerate fit,
        and that is only possible if such a session still reaches `make()`).

        **"Assembled events" is `pipeline.event.BehaviorRecording`** -- the
        exact done-marker `daemon._event_stage_keys()` already uses for the
        identical fact, written only once `events.populate_session` commits
        its whole transaction (`schema/events.py`'s own "one per session
        (its primary key IS the session's)"). A session whose events failed
        to assemble has no such row, so it stays outside this key_source
        until a later pass fixes the underlying fault -- and that failure is
        already reported, through `_populate_event_stage`'s own error list,
        not fabricated here as a row for a session this table cannot yet
        read at all. Design spec section 6's "must report that reason rather
        than appearing uncalibrated for an unrelated one" is satisfied
        THERE.

        `ingest` is reached without a module-level import for the reason
        `SystemTimebase.key_source` gives: peer schema modules must not
        acquire an import-time ordering. `core` is imported at module level
        instead, matching `timebase.py`/`coverage.py`'s own precedent -- it
        sits structurally below every other schema module here, so no such
        hazard exists for it.

        **Cost, named rather than left implicit (fix round, reviewer
        finding 3): this coarse gate forfeits `core.Segment`'s own
        self-healing property.** That table's own `key_source` docstring
        states it as deliberate: a session whose files are ALL rejected
        stays outstanding on purpose, "what lets a corrected or
        re-transferred file be picked up with no manual step". Because
        `core.Segment` and this table populate inside the SAME
        `run_once()` pass, and this `key_source` needs only the coarse
        `AcquisitionSystem` signal, a session whose ohDPI file is rejected
        on pass 1 (`too_short`, `no_barcode`) gets `make()`'s own "no
        segments" refusal that SAME pass -- and once written, that row is
        PERMANENT: DataJoint never recomputes an already-populated key. If
        the file is later corrected or re-transferred, `core.Segment`
        would pick it up on a later pass (it keeps retrying until
        something is accepted); this table never will, short of someone
        deleting the row by hand. `EyeQuality`, keyed on `core.Segment`
        directly (see its own `key_source`), does not have this problem --
        it simply stays outstanding for as long as `core.Segment` does.

        Kept anyway, deliberately: gating THIS table on `core.Segment` too
        would swap this cost for the opposite one -- a session with a
        GENUINELY missing or permanently unusable ohDPI file would get no
        row here at all, ever, which is precisely the outcome this task's
        own brief and design spec section 6 refuse ("a session with no
        ohDPI file gets refused with that specific reason" is only
        reachable if the file's ABSENCE itself still produces a row). Both
        costs are real; this file accepts the one the brief states
        explicitly over the one it does not.
        """
        from wl_preproc.schema import ingest

        return (
            pipeline.Session
            & ingest.Ingestion
            # Backtick-quoted: `system` is a reserved word in MySQL 8 (an
            # unquoted `system = "ohdpi"` fails with a syntax error --
            # confirmed directly against this project's pinned MySQL 8.0
            # test container, not merely assumed from the SQL standard).
            & (core.AcquisitionSystem & '`system` = "ohdpi"')
            & pipeline.event.BehaviorRecording
        )

    def make(self, key: dict) -> None:
        """Both eyes' calibration for one session, through design spec
        section 3.5's fallback chain in full: fit our own, try MonkeyLogic's,
        try the best same-day carried-forward map, or refuse and say why.

        **Ruling A: `eye` is native to this table's own primary key, not
        inherited through a foreign key.** `key_source` yields one key per
        SESSION; this single `make()` call computes and inserts BOTH eyes'
        rows for it, each key extended with `eye="left"`/`eye="right"` --
        the standard DataJoint shape for a native (non-inherited) primary
        key attribute.

        **Target positions are read from the DECODED CODE STREAM, re-derived
        here rather than from `pipeline.event.Event`.** `schema/events.py::
        populate_session` stores no attribute row for a `TARGET_POSITION`
        payload's own words -- only `TRIAL_NUMBER`, `BLOCK_START` and
        `CONDITION` get one (see that function's own body) -- so the
        `(role, x, y)` triple this table needs survives only in the raw
        stream. `TimingProvenance.make()` re-decodes the identical stream
        for the identical reason; see `schema/events.py`'s own "Public, not
        module-private" paragraph on `decode_syncbox_in_session_time`, which
        is what makes this a second CALLER of one decode rather than a
        second, independently drifting implementation of it.
        """
        from wl_preproc.schema import ingest
        from wl_preproc.schema.events import decode_syncbox_in_session_time

        session_key = {k: key[k] for k in pipeline.Session.primary_key}
        session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))

        segments = (core.Segment & {**session_key, "system": "ohdpi"}).to_dicts(
            order_by="start_s"
        )

        if not segments:
            # `key_source` only proves `core.AcquisitionSystem` saw SOME file
            # under `ohdpi/` -- see that property's own docstring.
            # `core.Segment` is the fine-grained answer: no row here means
            # nothing under that directory survived alignment, whether
            # because the genuine per-frame recording was never written at
            # all or because every file that was there got rejected. Both
            # collapse to the same outcome for calibration's purposes --
            # there is no session-time-mapped raw signal to sample -- so
            # both get the SAME reason, distinct from every reason
            # `resolve_calibration` itself can produce (Controller ruling D).
            reason = (
                "no ohDPI recording could be aligned to session time "
                "(AcquisitionSystem recorded this system as present, but no "
                "file under it survived Segment's own alignment scan)"
            )
            self.insert(
                [
                    {
                        **session_key,
                        "eye": eye_value,
                        "calibration_source": "refused",
                        "calibration_model": None,
                        **_coefficient_columns(None),
                        "validation_error_deg": None,
                        "n_points": 0,
                        "n_from_calibration_block": 0,
                        "n_from_task_fixation": 0,
                        "conditioning": None,
                        "residual_deg_rms": None,
                        "residual_deg_max": None,
                        "carried_from_session_datetime": None,
                        "reason": _bounded_reason(reason),
                    }
                    for eye_value in ("left", "right")
                ]
            )
            return

        # -- Re-decode the sync box's own code stream (see this method's own
        # docstring for why this cannot instead read `pipeline.event.Event`).
        decoded = decode_stream(decode_syncbox_in_session_time(session_dir))

        # -- One pass over the stream: track the most recently announced
        # target per role and the most recently opened block, and close a
        # usable window whenever a FIXATION_END arrives with a
        # FIXATION_POINT target on offer. Design spec section 4.3: "The
        # calibration window is [FIXATION_ACQUIRED, FIXATION_END], paired
        # with the most recent TARGET_POSITION of the relevant role" -- the
        # relevant role being FIXATION_POINT, since a fixation HOLD is, by
        # construction, held on the fixation point rather than on whatever
        # secondary/saccade target a task also has on screen at once.
        windows: list[tuple[float, float, tuple[float, float], int | None, int | None]] = []
        current_targets: dict[int, tuple[float, float]] = {}
        current_block_id: int | None = None
        current_task_type: int | None = None
        open_start: float | None = None
        open_target: tuple[float, float] | None = None
        open_block_id: int | None = None
        open_task_type: int | None = None

        for event in decoded:
            if isinstance(event, PayloadEvent):
                if event.escape is Escape.TARGET_POSITION:
                    role, x_word, y_word = event.words
                    current_targets[role] = (decode_dva(x_word), decode_dva(y_word))
                elif event.escape is Escape.BLOCK_START:
                    current_block_id, current_task_type = event.words
                continue
            if isinstance(event, SimpleEvent):
                if event.code == TaskEvent.FIXATION_ACQUIRED:
                    open_start = event.time_s
                    open_target = current_targets.get(TargetRole.FIXATION_POINT)
                    open_block_id, open_task_type = current_block_id, current_task_type
                elif event.code == TaskEvent.FIXATION_END and open_start is not None:
                    if open_target is not None:
                        windows.append(
                            (open_start, event.time_s, open_target, open_block_id, open_task_type)
                        )
                    open_start = open_target = None
                    open_block_id = open_task_type = None
                continue
            # DecodeError: a corrupt payload elsewhere in the stream must not
            # cost this session its calibration -- ignored here exactly as
            # `events/assemble.py::assemble` ignores one for trial assembly.

        # -- Resolve each window to an ohDPI row range, once: identical for
        # both eyes (same file, same segment timing -- only the PURKINJE
        # VALUES sampled from it differ per eye, applied below).
        #
        # A window that named a real target but falls OUTSIDE every aligned
        # segment (ohDPI started late, a restart split the window, or
        # `core.Segment` simply never aligned that stretch) is dropped here
        # -- silently, at this point -- rather than guessed at. Fix round
        # (reviewer finding 2): dropping it with NO RECORD meant a session
        # where EVERY window dropped this way fell through to
        # `resolve_calibration`'s own "no fixation epoch named a target
        # position" -- which is FALSE. Epochs existed and named real
        # targets; the fault is recording coverage, not the task code, and
        # a `n_windows_found`/`len(row_ranges)` gap below is what makes that
        # distinction visible instead of silently absorbed.
        row_ranges: list[
            tuple[int, int, tuple[float, float], int | None, int | None, dict]
        ] = []
        for t_start, t_end, target_xy, block_id, task_type in windows:
            segment = _containing_segment(segments, t_start, t_end)
            if segment is None:
                continue
            row_start = _session_time_to_row(segment, t_start)
            row_end = _session_time_to_row(segment, t_end)
            if row_start is None or row_end is None:
                continue
            lo, hi = sorted((row_start, row_end))
            row_ranges.append((lo, hi, target_xy, block_id, task_type, segment))

        n_windows_found = len(windows)
        n_windows_dropped = n_windows_found - len(row_ranges)

        if windows and not row_ranges:
            # Every fixation epoch that named a target still had nowhere to
            # be sampled from. Bypassing `resolve_calibration` here matters:
            # its own "no fixation epoch named a target position" would be
            # ACTIVELY WRONG in this case and would point a design spec
            # section 8 report reader at the task code for a fault that is
            # actually the recording's own coverage.
            reason = (
                f"{n_windows_found} fixation epoch(s) named a target, but "
                "none fell within an aligned ohDPI segment (recording "
                "coverage gap: ohDPI starting late, a restart splitting the "
                "window, or a stretch core.Segment never aligned)"
            )
            self.insert(
                [
                    {
                        **session_key,
                        "eye": eye_value,
                        "calibration_source": "refused",
                        "calibration_model": None,
                        **_coefficient_columns(None),
                        "validation_error_deg": None,
                        "n_points": 0,
                        "n_from_calibration_block": 0,
                        "n_from_task_fixation": 0,
                        "conditioning": None,
                        "residual_deg_rms": None,
                        "residual_deg_max": None,
                        "carried_from_session_datetime": None,
                        "reason": _bounded_reason(reason),
                    }
                    for eye_value in ("left", "right")
                ]
            )
            return

        # -- The online candidate: one map for the whole session (Task 6's
        # reader has no per-eye split), tried identically for both eyes below.
        online = read_online_map(_find_bhv2(session_dir))

        rows = []
        block_rows = []
        for eye_value, file_eye in (("left", "Left"), ("right", "Right")):
            raw_points: list[np.ndarray] = []
            target_points: list[tuple[float, float]] = []
            n_from_calibration_block = 0
            n_from_task_fixation = 0
            cache: dict[Path, np.ndarray] = {}

            for lo, hi, target_xy, _block_id, task_type, segment in row_ranges:
                path = session_dir / "ohdpi" / segment["file_path"]
                trace = cache.get(path)
                if trace is None:
                    trace = purkinje_vector(path, file_eye)
                    cache[path] = trace
                raw_points.append(trace[lo : hi + 1].mean(axis=0))
                target_points.append(target_xy)
                # PROVISIONAL READING, NOT A SETTLED FACT (fix round,
                # reviewer finding 6): no literal "calibration" task type
                # exists in `contracts.events.TaskTypeCode`, and design spec
                # section 4.2 motivates the `role` word with
                # MEMORY_GUIDED_SACCADE -- "that task has a fixation point
                # and a target on screen simultaneously" -- WITHOUT ever
                # characterising it as a dedicated calibration block. Reading
                # it as one is this implementation's own choice, not
                # something section 3.4's "calibration block or a task
                # fixation" distinction independently settles. A future task
                # that names a real calibration task type, or decides
                # MEMORY_GUIDED_SACCADE is not one, changes only this `if`.
                #
                # Recorded, not fixed: for MEMORY_GUIDED_SACCADE specifically
                # -- the one type bucketed as "calibration block" -- every
                # window here is still paired with `TargetRole.
                # FIXATION_POINT` only (this method's own loop, above), never
                # `SACCADE_TARGET` (role 1), which is what a POST-saccade
                # hold would need. Whether MGS ever emits a
                # FIXATION_ACQUIRED/END pair around the saccade target rather
                # than the fixation point is undetermined until that task's
                # own code exists; if it does, this bucket is fit against the
                # wrong target for exactly the task type it claims to favour.
                if task_type == int(TaskTypeCode.MEMORY_GUIDED_SACCADE):
                    n_from_calibration_block += 1
                else:
                    n_from_task_fixation += 1

            raw_xy = np.array(raw_points, dtype=float) if raw_points else np.empty((0, 2))
            target_xy_arr = (
                np.array(target_points, dtype=float) if target_points else np.empty((0, 2))
            )

            carried = None
            carry_datetime = None
            candidate = _best_carry_forward_candidate(eye_value, session_key)
            if candidate is not None:
                carry_datetime, candidate_map = candidate
                carried = (candidate_map, carry_datetime.isoformat())

            result = resolve_calibration(raw_xy, target_xy_arr, online, carried)

            # **The AFFINE basis, for every row, whatever model was used.**
            # One definition of one column: how well this session's own target
            # constellation constrains a calibration at all -- the question a
            # refused or borrowed row raises. The second-order verdict needs no
            # column of its own because `calibration_model` already IS that
            # verdict, thresholded. Measured on the targets rather than on the
            # raw design matrix for the reason `_conditioning`'s own docstring
            # gives: a well-spread raw cloud from one target location scores
            # 0.857 and means nothing.
            conditioning = (
                float(_conditioning(basis(target_xy_arr, CalibrationModel.AFFINE)))
                if target_xy_arr.shape[0]
                else None
            )

            residual_rms = None
            residual_max = None
            if result.source == CalibrationSource.FITTED and result.map_ is not None:
                # The fitted map's own residual over the SAME points it was
                # fit from -- numerically identical to `validation_error_deg`
                # here, since a fitted calibration has only one point set
                # (unlike a borrowed map, validated against a session it did
                # not come from). The RMS is therefore not recomputed; the
                # MAX is, since `validate_map` reports only the RMS.
                residual_rms = result.validation_error_deg
                predicted = apply_map(result.map_, raw_xy)
                residual_max = float(
                    np.sqrt(np.max(np.sum((predicted - target_xy_arr) ** 2, axis=1)))
                )

            carried_from_dt = (
                carry_datetime if result.source == CalibrationSource.CARRIED_FORWARD else None
            )
            # A PARTIAL drop -- some windows sampled fine, at least one did
            # not -- must still be visible even when calibration otherwise
            # SUCCEEDS from what remained: a coverage gap that happens not
            # to change the verdict this time is still worth knowing about
            # before it grows into one that does. Combined via
            # `_combine_reason` rather than a bare f-string concatenation,
            # deliberately: `result.reason` alone already reaches 248
            # characters for a real refusal (a collinear/coincident-target
            # message from `calibration.py::fit_map`, plus
            # `resolve_calibration`'s own "; no fallback map validated"),
            # and this column is `varchar(255)` -- a bare concatenation
            # would either overflow the column outright (raising inside
            # `make()`, surfacing as a daemon error rather than the
            # graceful refused-with-a-reason row this module calls a
            # first-class outcome) or, naively sliced from the end, would
            # silently drop this method's OWN coverage note in full every
            # time, the one case where a reader most needs to see both
            # causes at once. See `_combine_reason`'s own docstring.
            coverage_note = (
                f"{n_windows_dropped} of {n_windows_found} fixation windows "
                "had no ohDPI coverage"
                if n_windows_dropped > 0
                else ""
            )
            reason = _combine_reason(result.reason, coverage_note)

            rows.append(
                {
                    **session_key,
                    "eye": eye_value,
                    "calibration_source": result.source.value,
                    # The map's own model, whichever rung produced it -- and
                    # for a borrowed map, the shape it arrived in. Written
                    # from the map rather than inferred from the source, so
                    # a carried-forward second-order map is not silently
                    # recorded as affine.
                    "calibration_model": (
                        result.map_.model.value if result.map_ is not None else None
                    ),
                    **_coefficient_columns(result.map_),
                    "validation_error_deg": result.validation_error_deg,
                    "n_points": len(raw_points),
                    "n_from_calibration_block": n_from_calibration_block,
                    "n_from_task_fixation": n_from_task_fixation,
                    "conditioning": conditioning,
                    "residual_deg_rms": residual_rms,
                    "residual_deg_max": residual_max,
                    "carried_from_session_datetime": carried_from_dt,
                    "reason": reason,
                }
            )

            if result.map_ is not None:
                # Design spec section 3.6: residual recorded PER BLOCK, so
                # drift over a long session shows as a residual that grows --
                # measured here, never corrected.
                by_block: dict[int, list[int]] = {}
                for index, (_lo, _hi, _target, block_id, _task_type, _segment) in enumerate(
                    row_ranges
                ):
                    if block_id is not None:
                        by_block.setdefault(block_id, []).append(index)
                for block_id, indices in by_block.items():
                    block_rows.append(
                        {
                            **session_key,
                            "eye": eye_value,
                            "block_id": block_id,
                            "n_points": len(indices),
                            "residual_deg_rms": validate_map(
                                result.map_, raw_xy[indices], target_xy_arr[indices]
                            ),
                        }
                    )

        self.insert(rows)
        if block_rows:
            self.BlockResidual.insert(block_rows)


def _containing_segment(segments: list[dict], t_start: float, t_end: float) -> dict | None:
    """The ohDPI segment whose own session-time extent covers this whole
    window, or `None` if no single segment does (a restart split it, or it
    falls in a gap `core.Segment` never aligned). Skipped rather than
    guessed: interpolating a window across two different files' own clocks
    would invent a sample that was never actually measured."""
    for segment in segments:
        if segment["start_s"] <= t_start and t_end <= segment["end_s"]:
            return segment
    return None


def _session_time_to_row(segment: dict, session_s: float) -> int | None:
    """The ohDPI file row (0-based) nearest `session_s`, by the same linear
    map `core.Segment.make()` fit -- `session_s = native_s/scale + offset_s`
    -- here inverted and expressed directly through the segment's own
    stored extent (`start_s` at row 0, `end_s` at the last row) rather than
    re-deriving `scale`/`offset_s` separately. The two are equivalent by
    construction, and this needs no rate or barcode reference of its own."""
    n_samples = segment["n_samples"]
    if n_samples <= 0:
        return None
    span = segment["end_s"] - segment["start_s"]
    frac = 0.0 if span <= 0 else (session_s - segment["start_s"]) / span
    row = int(round(frac * (n_samples - 1)))
    return min(max(row, 0), n_samples - 1)


def _find_bhv2(session_dir: Path) -> Path | None:
    """The session's own MonkeyLogic log, if one exists.

    No path convention for it exists anywhere in this repository yet --
    `.bhv2` names no `contracts.paths.SYSTEMS` entry (MonkeyLogic is a task
    stack, not an acquisition system this pipeline lays out a directory for),
    and nothing else in this codebase locates one either. Searching the
    whole session tree and taking the first match is the least assuming
    choice available; a session with none -- every session this project's
    synthetic generator produces, since `synth/peripherals.py::
    write_task_file` "stands in for MonkeyLogic's .bhv2 until the task stack
    is chosen" and writes `task.json`, never a real `.bhv2` -- finds nothing,
    which `read_online_map(None)` already treats as an ordinary skip
    (design spec section 4.5: "a missing or unreadable .bhv2 is not an
    error").
    """
    matches = sorted(session_dir.rglob("*.bhv2"))
    return matches[0] if matches else None


def _best_carry_forward_candidate(
    eye_value: str, session_key: dict
) -> tuple[datetime.datetime, CalibrationMap] | None:
    """Design spec section 3.5 step 3, and a real ambiguity in it, resolved
    rather than hidden (fix round, reviewer finding 6).

    **Section 3.5 states two different selection rules and I picked one.**
    Its summary table says step 3 offers "the best-conditioned map from the
    same subject and date"; its own "Carry-forward scope" paragraph says
    "the same `(subject, date)`, nearest in time, preferring a preceding
    session." These do not agree in general -- given an 08:30 candidate at
    `conditioning=0.06` and an 06:00 candidate at `0.95`, "best-conditioned"
    picks 06:00 and "nearest in time" picks 08:30. **This function sorts by
    nearest in time, preferring a preceding session on a tie**, and does
    NOT compare candidates by conditioning at all. Chosen over
    "best-conditioned" because the scope paragraph is the more specific of
    the two -- it names an actual total order (time delta, tie-break), where
    "best-conditioned" alone is silent on what breaks a tie in conditioning
    -- and because a calibration drifts with time (section 3.6), so the
    nearer session is the more defensible default borrow regardless of
    which one happened to fit slightly better.

    Separately, and not as a resolution of the disagreement above:
    candidates are restricted to `calibration_source='fitted'` rows, the
    only source with a real, non-NaN `conditioning` of its own
    (`CalibrationMap`'s own docstring: a borrowed map "was never fit by `fit_map` here and
    has no such history to report"). This filter stands on its own
    reasoning -- carrying forward a map that was ITSELF already borrowed
    would compound provenance across an unbounded chain, precisely what
    section 3.4's "recorded, not averaged away" (of which source contributed
    a point) argues against -- not because it makes "best-conditioned" and
    "nearest in time" agree; restricting to `fitted` candidates does not
    change which of the two rules above is being applied, only which rows
    are eligible for either.
    """
    candidates = (
        EyeCalibration
        & {
            "subject": session_key["subject"],
            "eye": eye_value,
            "calibration_source": "fitted",
        }
    ).to_dicts()

    this_datetime = session_key["session_datetime"]
    same_day = [
        row
        for row in candidates
        if row["session_datetime"] != this_datetime
        and row["session_datetime"].date() == this_datetime.date()
    ]
    if not same_day:
        return None

    def sort_key(row: dict) -> tuple[float, int]:
        delta = (row["session_datetime"] - this_datetime).total_seconds()
        # Nearest absolute delta first; an exact tie prefers the session that
        # PRECEDES this one (delta < 0), per section 3.5's own stated
        # preference.
        return (abs(delta), 0 if delta < 0 else 1)

    best = min(same_day, key=sort_key)
    # `_map_from_row` branches on `calibration_model`, so a carried-forward
    # candidate keeps whichever shape it was fitted at rather than being
    # flattened to affine on the way out.
    #
    # It can return `None` in general -- a refused row has no model -- but not
    # here: the restriction above is `calibration_source='fitted'`, and every
    # fitted row was written from a real map (`make()` sets the model from
    # `result.map_`, which `CalibrationSource.FITTED` guarantees is not None).
    # Asserted rather than assumed, because "a source implies a model" is
    # exactly the kind of cross-column invariant nothing in the schema
    # enforces.
    borrowed = _map_from_row(best)
    if borrowed is None:  # pragma: no cover -- see above
        raise ValueError(
            f"fitted calibration at {best['session_datetime']} has no "
            "calibration_model; a fitted row is always written from a real map"
        )
    return best["session_datetime"], borrowed


def _count_true_runs(mask: np.ndarray) -> int:
    """How many CONTIGUOUS `True` runs `mask` contains -- a rising-edge
    count, not a sum: `[T, T, F, T]` is two runs, not three frames.

    Runs are counted per FILE, not stitched across a multi-segment session's
    boundary: two files from a restart mid-blink would count as two runs
    rather than one continuation. Accepted rather than resolved -- a session
    whose ohDPI recording restarts mid-blink is rare, and stitching across
    two different files' own clocks is not otherwise needed anywhere in
    this module.
    """
    if mask.size == 0:
        return 0
    padded = np.concatenate(([False], mask, [False]))
    return int(np.sum(np.diff(padded.astype(np.int8)) == 1))


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
        """Landed sessions with an ohDPI recording that `core.Segment` was
        actually able to align -- deliberately the FINE-grained check,
        unlike `EyeCalibration.key_source`'s coarser `core.AcquisitionSystem`
        gate.

        `EyeCalibration` needs the coarse gate specifically so a session
        whose `ohdpi/` directory holds nothing usable still reaches `make()`
        and gets an explicit `refused` row with its own reason (see that
        property's own docstring). This table's schema has no such outcome
        to express: `tracking_loss_fraction`/`blink_rate_hz` are plain,
        non-nullable `double`s with no `calibration_source`/`reason` pair
        beside them, so there is nothing correct this table could write for
        a session that cleared the coarse gate but has no real file behind
        it. Keying off `core.Segment` instead means such a session is simply
        not yet due here -- the same reasoning `core.Segment.key_source`
        itself gives for excluding `fit_status == 'no_recording'`: "there is
        nothing to scan... a key that can never insert a row" would
        otherwise be re-attempted forever.

        No "assembled events" restriction, unlike `EyeCalibration`: Task 9's
        own docstring already named this difference ("tracking loss and
        blink rate need only an ohDPI recording, not assembled events"), and
        tracking loss reads straight off `DataQuality`, needing no decoded
        target at all.

        This is also why THIS table, unlike `EyeCalibration`, keeps
        `core.Segment`'s own self-healing property intact: a session whose
        ohDPI file is rejected on one pass simply stays outstanding here
        too, for as long as `core.Segment` itself does, and a later
        correction or re-transfer is picked up with no manual step. See
        `EyeCalibration.key_source`'s own docstring for the cost its
        coarser gate accepts in exchange for a row -- refused, with a
        reason -- that a permanently missing file still gets.
        """
        from wl_preproc.schema import ingest

        # Backtick-quoted for the same reason `EyeCalibration.key_source`
        # is: `system` is a reserved word in MySQL 8.
        return pipeline.Session & ingest.Ingestion & (core.Segment & '`system` = "ohdpi"')

    def make(self, key: dict) -> None:
        """Tracking loss and blink rate, per eye, straight off
        `DataQuality`'s 0/50/100 -- stated by the recording, never inferred.

        A "blink" is read here as one CONTIGUOUS run of tracking loss
        (`DataQuality < 100`; design spec section 1.1: "DataQuality =
        50*P1_valid + 50*P4_valid"). A real dual-Purkinje tracker loses both
        images together for the duration of an actual blink, and neither the
        design spec nor any task before this one names a more specific
        signal to use instead -- section 1.1 also names `UpperEyelid`/
        `LowerEyelid` as always zero ("the DPI pipeline does not compute
        them"), so there is no eyelid signal here to prefer even in
        principle, not merely one this pipeline happens not to read yet.
        """
        from wl_preproc.schema import ingest

        session_key = {k: key[k] for k in pipeline.Session.primary_key}
        session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
        segments = (core.Segment & {**session_key, "system": "ohdpi"}).to_dicts()

        rows = []
        for eye_value, file_eye in (("left", "Left"), ("right", "Right")):
            lost_frames = 0
            total_frames = 0
            blink_runs = 0
            total_duration_s = 0.0
            for segment in segments:
                path = session_dir / "ohdpi" / segment["file_path"]
                # Read once and derive both metrics from the same array,
                # rather than calling `gaze.tracking_loss_fraction` and then
                # re-reading the identical column separately: `eye/gaze.py`'s
                # own module docstring measures this read alone at ~2.5 s on
                # a real recording, dominating everything else that module
                # does with it -- reading the same column twice would double
                # exactly that cost for nothing.
                quality = read_columns(path, [f"{file_eye}DataQuality"])[
                    f"{file_eye}DataQuality"
                ]
                lost = quality < _FULL_TRACKING_QUALITY
                lost_frames += int(lost.sum())
                total_frames += lost.size
                blink_runs += _count_true_runs(lost)
                total_duration_s += segment["end_s"] - segment["start_s"]

            rows.append(
                {
                    **session_key,
                    "eye": eye_value,
                    "tracking_loss_fraction": (
                        (lost_frames / total_frames) if total_frames else 0.0
                    ),
                    "blink_rate_hz": (
                        (blink_runs / total_duration_s) if total_duration_s > 0 else 0.0
                    ),
                }
            )

        self.insert(rows)


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}eye`. Idempotent."""
    core.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}eye", create_tables=True)
