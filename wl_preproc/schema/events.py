# wl_preproc/schema/events.py
"""Population of the adopted event and trial tables. Declares no table of its own.

**This module adds no `@schema` class**, and that is a finding rather than an
omission: every table Phase 1c-5 fills already exists. `BehaviorRecording`,
`EventType`, `Event`, `Trial`, `TrialType`, `Block` and `BlockTrial` come from
element-event; `TrialCoverage` was declared in 1c-1 and converted to `Computed`
in 1c-4 specifically so this phase would not have to migrate it.

**element-event's Imported tables are filled by direct insert, not by
`populate()`.** `Event.make()` raises `NotImplementedError("For `insert`, use
`allow_direct_insert=True`")` -- checked against the installed package. So this
module inserts, and nothing here defines a `make()` for them.

**`populate_session` builds the canonical trial list from the sync box (the
Pi) alone.** Design spec 2026-08-23-phase-1c5-events-design.md section 7 names
`event_code_agreement` -- the Pi-versus-NI comparison -- as a TIER input fed to
`TimingProvenance` by `events/agreement.py`, wired in by Task 9, not built here.
The sync box is the one system guaranteed present at every session
(`SessionRecipe._coherent`: "syncbox is present at every session") and its own
clock IS session time (spec section 4.5, and section 3's whole argument for
`BehaviorRecording` being one-per-session), so it is also the only system this
module needs in order to decode a complete, correctly-timed trial/block list on
its own, with no dependency on `core.Segment` / `timebase.SystemTimebase`
already having populated for this session -- this module owns no populate
stage of its own (see above) and so has no fixed place in the daemon's
ordering, unlike those two `Computed` tables.
"""

from __future__ import annotations

from pathlib import Path

from wl_preproc.contracts.events import (
    DecodeError,
    Escape,
    Marker,
    PayloadEvent,
    SimpleEvent,
    decode_stream,
)
from wl_preproc.events.assemble import AssembledBlock, AssembledTrial, assemble
from wl_preproc.events.extract import extract_syncbox_words
from wl_preproc.schema import DEFAULT_PREFIX, core, pipeline
from wl_preproc.timebase import segments
from wl_preproc.timebase.fit import RateFit, fit_offset


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Ensure the tables this module fills are bound. Idempotent.

    No `schema.activate` of its own -- there is no schema to activate, because
    this module declares no table.
    """
    pipeline.activate(prefix=prefix)
    core.activate(prefix=prefix)


def sync_event_types() -> None:
    """Project `contracts.events.Marker` into element-event's `EventType`.

    A projection rather than a hand-written list: the contract is frozen (spec
    section 3.5 item 4), so a marker added there must appear here without
    anyone remembering to. The hand-listed alternative is the shape this
    repository has missed three times.
    """
    pipeline.event.EventType.insert(
        [{"event_type": marker.name, "event_type_description": ""} for marker in Marker],
        skip_duplicates=True,
    )


def _ensure_event_type(event_type: str) -> None:
    """Make sure `event_type` exists in `EventType` before an `Event` row cites
    it, for a name `sync_event_types()` does not cover: an `Escape` payload's
    name, or a task-specific code outside the frozen `Marker` set (see
    `populate_session`).

    `test_event_types_are_projected_from_the_frozen_marker_enum` asserts a
    SUBSET (`<=`), by construction, of exactly the Marker names -- not
    equality -- precisely so this function's supplementary entries can coexist
    with `sync_event_types()` rather than contradict it.
    """
    pipeline.event.EventType.insert1(
        {"event_type": event_type, "event_type_description": ""}, skip_duplicates=True
    )


def _u32(words: tuple[int, ...]) -> int:
    """Two 16-bit words, high word first.

    Restated rather than imported from `events/assemble.py`'s own `_u32`:
    that name is private to its module, and the convention itself is the
    frozen protocol's (`contracts/events.py`'s `PAYLOAD_WORD_COUNTS` comment:
    "uint32, high word first"), not something free to drift independently
    between the two places it is used.
    """
    return (words[0] << 16) | words[1]


def decode_syncbox_in_session_time(session_dir: Path) -> list[tuple[float, int]]:
    """Every code word the sync box recorded, native time converted to session
    time via `timebase/fit.py` -- the same fit primitives `core.Segment` and
    `timebase.SystemTimebase` use, so this does not invent a second
    conversion.

    **Public, not module-private** (1c-5 Task 9): this docstring said all
    along that the NI's own code words are "used only for `event_code_agreement`
    ... which is `events/agreement.py` and `TimingProvenance`'s job ... not
    this module's" -- and `TimingProvenance.make()` needs the Pi's SAME
    canonical decode to compare the NI's record against. Restating this
    function there, the way `events.py`'s own `_u32` restates `assemble.py`'s,
    would duplicate the non-trivial half (barcode reference, identity rate,
    offset fit) that restating a two-word integer pack does not risk --
    `timebase/coverage.py`'s own rule against a second implementation applies
    here for the same reason.

    Sync box only, deliberately. The NI's own code words are a SECOND,
    independent record used only for `event_code_agreement` (tier A) and the
    tier B/C witness counts, which is `TimingProvenance`'s job, not this
    module's: the canonical trial list is built from exactly one full-code
    record.

    Identity rate, not `fit_rate`: session time IS the sync box's own
    timeline (spec section 4.5), the same special case `SystemTimebase.make()`
    applies to this same system for the same reason -- a fit of it against
    itself is exactly zero drift with zero residual, the truth rather than a
    placeholder.
    """
    reference = segments.session_reference(session_dir)
    scans = segments.scan_system("syncbox", session_dir / "syncbox")
    if not scans:
        raise FileNotFoundError(
            f"{session_dir}: no syncbox recording, so this session has no "
            "session time and no code stream to decode from -- every session "
            "has one (SessionRecipe requires it of the synthetic fixtures, and "
            "spec section 4.3 makes it essential sync infrastructure)"
        )

    converted: list[tuple[float, int]] = []
    for scan in scans:
        rate = RateFit(
            fitted_rate_hz=scan.stream.fs_hz,
            drift_ppm=0.0,
            n_matched=len(scan.barcodes),
            residual_us_rms=0.0,
            residual_us_max=0.0,
        )
        offset = fit_offset(scan.barcodes, reference, rate)
        word_stream = extract_syncbox_words(scan.path)
        converted.extend(
            (native_pos / word_stream.fs_hz / rate.scale + offset.offset_s, code)
            for native_pos, code in word_stream.words
        )

    # `decode_stream` reads an escape payload by POSITION, not by comparing
    # timestamps -- see its own docstring -- so a session recorded across more
    # than one sync box log file needs re-sorting to restore emission order
    # before decoding. A single file (the common case, and every current
    # fixture) is already sorted; this is then a no-op.
    converted.sort(key=lambda pair: pair[0])
    return converted


def _containing_block(
    trial: AssembledTrial, blocks: list[AssembledBlock], stream_end_s: float
) -> AssembledBlock | None:
    """Which measured block a trial falls inside, by interval containment.

    `AssembledTrial` carries no `block_id` of its own -- only the task file's
    `TaskTrial` does, and this module does not read the task file: spec
    section 4.1's populated-table list has no place for `condition` /
    `reward_ms`, and cross-checking trial counts against it is
    `events/agreement.py`'s job, fed into tier resolution by Task 9, not this
    module's. So the containing block is recovered from the two assembled
    interval lists directly.
    """
    for block in blocks:
        block_end = block.end_s if block.end_s is not None else stream_end_s
        if block.start_s <= trial.start_s < block_end:
            return block
    return None


def _trial_stop_time(
    trial: AssembledTrial,
    trial_index: int,
    ordered_trials: list[AssembledTrial],
    containing_block: AssembledBlock | None,
    stream_end_s: float,
) -> float:
    """`trial.end_s`, or the tightest verified bound available when the
    stream never explicitly closed it.

    **`end_s=None` arises from two distinct routes, not one -- fix round 2
    corrects a docstring that named only the first.** The first is a stream
    that ends mid-trial: a genuinely truncated recording (a killed process, a
    full disk), closed by the final, unconditional `close_trial(end_s=None)`
    in `events/assemble.py`, run once after its main loop. The second is a
    **dropped `TRIAL_END` mid-stream**: that same `close_trial(end_s=None)`
    also fires the moment the NEXT `TRIAL_START` arrives, whether or not that
    next trial belongs to the same block -- a dropped marker is exactly the
    lossy case this protocol is designed around (spec section 4.2
    requirement 1), not merely an end-of-recording artifact. `synth/
    timeline.py` now always emits `TRIAL_END` (fix round 1), so NEITHER route
    is reachable against any fixture in this repository today -- but both are
    real operational scenarios for this pipeline, which is why the fallback
    stays rather than being deleted.

    **Fix round 2: the containing block's own end is checked BEFORE the next
    trial's start, not after.** A previous version preferred "next trial's
    start" first, reasoning it was "exact, by synth/timeline.py's own
    construction" -- true only while every trial closed for real, which was
    the very premise fix round 1 removed. For a trial abandoned by a dropped
    `TRIAL_END`, checking "next trial's start" first is wrong exactly when
    the abandoned trial's OWN block closes (`BLOCK_END` received) before any
    later trial starts: `assemble()`'s `BLOCK_END` handling does not close an
    open trial (see that function's own docstring and its handling of
    `Marker.BLOCK_END`), so the abandoned trial stays open until the NEXT
    `TRIAL_START` -- which can belong to a LATER block. The old order would
    then return that later trial's start, overshooting the abandoned trial's
    own, already-known block boundary. Task 9 computes per-trial coverage as
    a fraction of a trial's own duration, so an overshot end silently
    UNDERSTATES every such trial's coverage, and nothing raises: the interval
    is still positive. A verified block boundary is a harder guarantee than a
    same-list index lookup, and checked first, it cannot place a trial's end
    outside its own block. `tests/schema/test_events.py::
    test_an_abandoned_trial_whose_block_closed_prefers_the_block_boundary`
    and `test_an_abandoned_trial_whose_block_never_closed_falls_to_the_next_trial`
    build both shapes through the real codec and `assemble()`, name which
    condition each produces, and are each confirmed to fail against the
    branch order this replaces.

    The order, tightest verified bound first:

    1. The trial's own `end_s`, when `TRIAL_END` (or the final flush) closed
       it for real.
    2. Its containing block's own end, when that block itself closed.
    3. The next trial's own start, when there is one.
    4. The last event time in the whole decoded stream -- the only branch a
       trial truncated with nothing recorded after it can ever reach.

    **Fix round 3: branch 1 is exempt from the invariant below, and returns
    before reaching it.** `_containing_block` decides containment by START
    time alone (`block.start_s <= trial.start_s < block_end`), and -- as fix
    round 2 already established -- `assemble()`'s `BLOCK_END` handling does
    not close an open trial. So a trial can legitimately start inside block
    N, have block N close around it, and only afterwards receive its own
    real `TRIAL_END` -- an ordinary behavioural-rig pattern (a block ending
    on a time or trial limit while a trial is still running), not a corrupt
    stream. Branch 1's `stop` is that transmitted value: ground truth from
    the recording, not this function's own inference, so it is accepted as
    authoritative and is not compared against the block boundary at all.
    Round 2's assertion did not distinguish this from the three inferred
    branches, so it would have crashed a real session's entire ingest on
    exactly this ordinary pattern the moment production data contained it
    (`tests/schema/test_events.py::
    test_a_trial_whose_real_end_arrives_after_its_block_closed` builds it and
    confirms both that no exception is raised and that reverting this
    exemption makes the test fail).

    **Invariant, asserted below on the INFERRED branches only (2-4):**
    whenever the containing block has closed, an inferred stop time can
    never exceed it -- the cheap check that turns a repeat of fix round 2's
    class of error from silent into loud, without also refusing branch 1's
    legitimate, transmitted values.
    """
    if trial.end_s is not None:
        # Ground truth from the recording, never this function's own
        # inference -- exempt from the invariant below by construction,
        # returned before that check is even reached. See the docstring's
        # "Fix round 3" paragraph for why this can legitimately fall after
        # the containing block's own end.
        return trial.end_s

    if containing_block is not None and containing_block.end_s is not None:
        stop = containing_block.end_s
    elif trial_index + 1 < len(ordered_trials):
        stop = ordered_trials[trial_index + 1].start_s
    else:
        stop = stream_end_s

    if containing_block is not None and containing_block.end_s is not None:
        assert stop <= containing_block.end_s, (
            f"trial {trial.trial_id} INFERRED stop time {stop} exceeds its own "
            f"containing block {containing_block.block_id}'s stop time "
            f"{containing_block.end_s} -- an inferred trial stop time can never "
            "exceed its containing block's stop time"
        )
    return stop


def _block_stop_time(block: AssembledBlock, stream_end_s: float) -> float:
    """`block.end_s`, or the last known event time when `BLOCK_END` never
    arrived.

    Every block in this project's current fixtures DOES carry an explicit
    `BLOCK_END` (checked in `synth/timeline.py`), so this path is defensive
    rather than exercised today -- kept for the same reason `_trial_stop_time`
    has one: `block_stop_time` is not nullable, so something concrete must be
    written regardless of how the stream ended.
    """
    return block.end_s if block.end_s is not None else stream_end_s


def populate_session(key: dict, session_dir: Path) -> None:
    """Populate one session's `BehaviorRecording`, `EventType`, `Event`,
    `Trial`, `TrialType`, `Block` and `BlockTrial` from the sync box's decoded
    code stream.

    `key` need only contain `pipeline.Session.primary_key` (`subject`,
    `session_datetime`); a caller passing a superset (e.g. an
    `AcquisitionSystem` key) is handled the same way `core.Segment.make()`
    narrows its own key.

    **Scalars only into `Event`/`Block`/`Trial` attribute rows.**
    `*.Attribute.attribute_blob` is a bare `longblob` on all three (one of
    four `_KNOWN_UPSTREAM_BARE_LONGBLOBS` entries `tests/schema/
    test_guardrails.py` allow-lists by exact name) -- nothing here ever writes
    to it; every attribute value below is a stringified scalar written to
    `attribute_value` instead.

    **`core.Block` is never written here.** It holds wl.works' own assertion,
    authored elsewhere (spec section 8.3.1); this function writes only the
    MEASURED boundary, into `trial.Block`.
    """
    session_key = {k: key[k] for k in pipeline.Session.primary_key}

    converted = decode_syncbox_in_session_time(session_dir)
    decoded = decode_stream(converted)
    assembly = assemble(decoded)

    stream_end_s = max(
        (item.time_s for item in decoded if not isinstance(item, DecodeError)),
        default=0.0,
    )

    # -- BehaviorRecording: one per session (its primary key IS the session's,
    # see tests/schema/test_events.py). "recording start" is the session's own
    # datetime, matching spec section 3's whole point: element-event's
    # "relative to recording start" and this project's session time (t=0 at
    # the sync box's first barcode) are then the same origin.
    pipeline.event.BehaviorRecording.insert1(
        {
            **session_key,
            "recording_start_time": session_key["session_datetime"],
            "recording_duration": stream_end_s,
        },
        skip_duplicates=True,
    )

    sync_event_types()

    # -- Event: the full decoded stream, one row per decoded event (not per
    # raw code word -- a payload's escape+words+checksum decodes to ONE
    # PayloadEvent). DecodeErrors are not events and have no element-event
    # table of their own; 1c-5 Task 9 (TimingProvenance) is what counts them
    # towards tier D, by re-decoding, not something this function persists.
    event_rows: list[dict] = []
    attribute_rows: list[dict] = []
    for item in decoded:
        if isinstance(item, DecodeError):
            continue

        if isinstance(item, SimpleEvent):
            try:
                event_type = Marker(item.code).name
            except ValueError:
                # contracts/events.py's own range table reserves 256-4095 for
                # task-specific events outside the frozen Marker set. No
                # fixture in this repository emits one today (checked:
                # synth/timeline.py names only Marker and Escape values), so
                # this fallback is defensive and, so far, unexercised.
                event_type = f"CODE_{item.code}"
                _ensure_event_type(event_type)
            event_rows.append(
                {**session_key, "event_type": event_type, "event_start_time": item.time_s}
            )
            continue

        # PayloadEvent: an escape's name is never a Marker name (Escape and
        # Marker are disjoint code ranges), so this cannot collide with a
        # `sync_event_types()` entry.
        event_type = item.escape.name
        _ensure_event_type(event_type)
        event_rows.append(
            {**session_key, "event_type": event_type, "event_start_time": item.time_s}
        )
        attribute_base = {
            **session_key,
            "event_type": event_type,
            "event_start_time": item.time_s,
        }
        if item.escape is Escape.TRIAL_NUMBER:
            attribute_rows.append(
                {**attribute_base, "attribute_name": "trial_id",
                 "attribute_value": str(_u32(item.words))}
            )
        elif item.escape is Escape.BLOCK_START:
            attribute_rows.append(
                {**attribute_base, "attribute_name": "block_id",
                 "attribute_value": str(item.words[0])}
            )
            attribute_rows.append(
                {**attribute_base, "attribute_name": "task_type",
                 "attribute_value": str(item.words[1])}
            )
        elif item.escape is Escape.CONDITION:
            # Never emitted by this project's synthetic generator today
            # (checked: synth/timeline.py builds no CONDITION payload); kept
            # for the same reason EventType covers every Marker rather than
            # only the ones a given fixture happens to exercise.
            attribute_rows.append(
                {**attribute_base, "attribute_name": "condition",
                 "attribute_value": str(_u32(item.words))}
            )

    if event_rows:
        pipeline.event.Event.insert(event_rows, allow_direct_insert=True, skip_duplicates=True)
    if attribute_rows:
        # Event.Attribute is a plain dj.Part (not also AutoPopulate), so
        # `_allow_insert`'s guard never applies to it -- verified directly
        # against datajoint/table.py before relying on it: only Event itself
        # needs `allow_direct_insert=True`.
        pipeline.event.Event.Attribute.insert(attribute_rows, skip_duplicates=True)

    # -- TrialType carries the outcome (design spec section 4, "4.1 Populated,
    # not declared": "trial.Trial -- the canonical trial list. TrialType
    # carries the outcome."), populated reactively from whatever outcomes this
    # session's trials actually produced rather than from a second hand-typed
    # vocabulary -- events/assemble.py's own `_OUTCOMES` mapping is private to
    # that module, and reactive population with skip_duplicates converges to
    # the same set across sessions without reaching into it.
    outcome_types = sorted({trial.outcome for trial in assembly.trials if trial.outcome is not None})
    if outcome_types:
        pipeline.trial.TrialType.insert(
            [{"trial_type": outcome, "trial_type_description": ""} for outcome in outcome_types],
            skip_duplicates=True,
        )

    # -- Trial (the canonical trial list) and BlockTrial together, since both
    # need each trial's containing block.
    trial_rows: list[dict] = []
    block_trial_rows: list[dict] = []
    for index, trial in enumerate(assembly.trials):
        containing_block = _containing_block(trial, assembly.blocks, stream_end_s)
        trial_rows.append(
            {
                **session_key,
                "trial_id": trial.trial_id,
                "trial_type": trial.outcome,
                "trial_start_time": trial.start_s,
                "trial_stop_time": _trial_stop_time(
                    trial, index, assembly.trials, containing_block, stream_end_s
                ),
            }
        )
        if containing_block is not None:
            block_trial_rows.append(
                {**session_key, "block_id": containing_block.block_id, "trial_id": trial.trial_id}
            )

    if trial_rows:
        pipeline.trial.Trial.insert(trial_rows, allow_direct_insert=True, skip_duplicates=True)

    # -- Block: the MEASURED boundary (design spec section 5). core.Block --
    # wl.works' own ASSERTION -- is never written here or anywhere in this
    # pipeline.
    block_rows = [
        {
            **session_key,
            "block_id": block.block_id,
            "block_start_time": block.start_s,
            "block_stop_time": _block_stop_time(block, stream_end_s),
        }
        for block in assembly.blocks
    ]
    if block_rows:
        pipeline.trial.Block.insert(block_rows, allow_direct_insert=True, skip_duplicates=True)

    # Block has no task_type column of its own (only block_start_time /
    # block_stop_time) -- BLOCK_START's own payload already carries it into
    # Event/Event.Attribute above, but the measured Block row is where a
    # consumer of trial.Block alone would look for it, so it is written here
    # too, as a scalar attribute rather than a schema change to an adopted
    # Element.
    block_attribute_rows = [
        {
            **session_key,
            "block_id": block.block_id,
            "attribute_name": "task_type",
            "attribute_value": str(block.task_type),
        }
        for block in assembly.blocks
    ]
    if block_attribute_rows:
        pipeline.trial.Block.Attribute.insert(block_attribute_rows, skip_duplicates=True)

    if block_trial_rows:
        pipeline.trial.BlockTrial.insert(
            block_trial_rows, allow_direct_insert=True, skip_duplicates=True
        )
