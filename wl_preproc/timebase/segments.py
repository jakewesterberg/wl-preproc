"""Which recordings become segments, and which are recorded as rejected.

Spec section 4.1's alignment table, as executable rules:

| Segment length | Guarantee            | Handling                          |
|----------------|----------------------|-----------------------------------|
| >=3.0 s        | >=2 complete barcodes| offset fit + local rate check     |
| >=2.0 s        | >=1 complete barcode | offset from barcode, rate inherit |
| <2.0 s         | may contain zero     | unalignable -> RejectedSegment    |

**Those bounds are consequences of the decoder, not of frame geometry.** A 1 Hz
barcode guarantees one complete frame in any 1.0 s window -- but
`wl_sync.barcode.decode_edges` requires a preceding idle of >=400 ms to identify
a lead pulse, because a lead pulse and a run of two set bits are both 10 ms of
HIGH and are otherwise indistinguishable. So a frame decodes only if the window
*also* contains the end of the previous frame, which adds one inter-frame
interval to every bound. The parent spec records the original numbers as having
been right about the format and wrong about the thing that would read it.

The durations are **guarantees, not requirements**: below 2.0 s a file *may*
contain zero barcodes, not *does*. A short file that did decode one can be
positioned, and rejecting it would discard a segment that is in fact alignable.
So the barcode count decides first and the duration only explains a file that
has none.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from wl_sync.barcode import INTERVAL_US

from wl_preproc.timebase._syncbox_log import SyncBoxLog, read_syncbox_log

if TYPE_CHECKING:
    from wl_sync.barcode import Barcode

    from wl_preproc.timebase.extract import BitStream

_INTERVAL_S = INTERVAL_US / 1_000_000.0

# One interval to guarantee a complete frame, plus one more because the decoder
# needs the previous frame's end in the same window. Derived rather than
# written down: a change to wl-sync's barcode cadence must fail a test here
# rather than silently invalidate the table above.
MIN_ALIGNABLE_DURATION_S = 2 * _INTERVAL_S

# Two barcodes, which buys local rate VERIFICATION -- a QC nicety, not a
# requirement, since section 4.5 fits rate per (system, session) and a segment
# needs only an offset locally.
MIN_LOCAL_RATE_DURATION_S = 3 * _INTERVAL_S

ALIGNABLE = "alignable"
TOO_SHORT = "too_short"
NO_BARCODE = "no_barcode"

# Not a verdict `classify_segment` can reach, because it is not a fact about
# this file: the file decoded a barcode and could be positioned, but its SYSTEM
# never got a clock fit, so there is no rate to hold fixed. A whole session with
# one decodable barcode on a system does that -- a mostly-dead cable, not a
# short file -- and calling it `no_barcode` would misname it on the one file
# that did have one.
UNFITTED_SYSTEM = "unfitted_system"

# Also not a verdict `classify_segment` can reach: the file DID carry
# barcodes and dropped frames removed them, so both `no_barcode` and
# `too_short` are true of what is left and wrong about why. Distinguished
# because the two call for different actions -- a short file is a recording
# that did not happen, a gap-corrupted one is a recording the camera failed
# to keep up with.
GAP_CORRUPTED = "gap_corrupted"

# Not a verdict `classify_segment` can reach either: the file decoded barcodes,
# but none of them is in session time's reference, so nothing places it. The
# sync box's own segment after a restart is on another clock, and a device
# recording begun after the restart saw only barcodes that segment emitted;
# part 2 of the design bridges both (design spec
# `2026-10-22-syncbox-records-and-segments-design.md` sections 4 and 7). Until
# 2026-10-22 `timebase/fit.py::fit_offset` raised on such a file and nothing
# caught it, so the whole stage failed instead of saying why.
NO_SHARED_BARCODE = "no_shared_barcode"

#: Every reason `RejectedSegment.reason` can hold, and where each is
#: decided. `TOO_SHORT` and `NO_BARCODE` come from `classify_segment`.
#: `UNFITTED_SYSTEM` is decided per SYSTEM, above the file, when no rate fit
#: exists for it to inherit. `NO_SHARED_BARCODE` is decided per FILE, in
#: `Segment.make`, against session time's reference. `GAP_CORRUPTED` is
#: decided per FILE, in `Segment.make`, overriding the verdict
#: `classify_segment` returned for that file rather than adding a fact
#: `classify_segment` itself lacked the inputs to see.
REJECTION_REASONS: frozenset[str] = frozenset(
    {TOO_SHORT, NO_BARCODE, UNFITTED_SYSTEM, GAP_CORRUPTED, NO_SHARED_BARCODE}
)


def classify_segment(duration_s: float, n_barcodes: int) -> str:
    """`"alignable"`, or the reason this recording is not.

    `no_barcode` and `too_short` are deliberately distinct. A long file that
    decoded nothing is a different fault -- wrong line, wrong bit, dead cable,
    a device that was powered but not recording -- from a file that was never
    long enough to contain a barcode at all. Collapsing them into one reason
    loses the diagnosis, and the diagnosis is the entire purpose of recording a
    rejection rather than dropping the file (1c-1's own table comment: recorded
    "so that 'why is this session short' has an answer").
    """
    if n_barcodes >= 1:
        return ALIGNABLE
    if duration_s < MIN_ALIGNABLE_DURATION_S:
        return TOO_SHORT
    return NO_BARCODE


# --- What populate needs, kept out of the table classes themselves. ---
#
# The `make()` bodies in `schema/core.py` and `schema/timebase.py` delegate
# here, so the rules above and the work below sit in one module and the tables
# stay declarations. It also keeps this testable without a database.


def syncbox_logs(session_dir: Path) -> list[SyncBoxLog]:
    """Every segment of the session's sync box log, in the order of its
    header's `segment_index`: a file's name sorts `seg-1000.log` before
    `seg-999.log`. Each file is read once however many stages ask in one
    process (`_syncbox_log.read_syncbox_log`, part 1's deferred finding)."""
    from wl_preproc.timebase.extract import find_recordings

    logs = [read_syncbox_log(path) for path in find_recordings("syncbox", session_dir / "syncbox")]
    return sorted(logs, key=lambda log: (log.segment_index, log.path.name))


#: How a segment follows the segment before it on its clock (design spec
#: `2026-11-04-syncbox-header-design.md` section 4): the roll in place, with
#: nothing missing; a gap, the box recording elsewhere on the same clock; or a
#: contradiction of the box's claim of one clock, read as a restart.
FOLLOWS_ADJACENT = "adjacent"
FOLLOWS_GAP = "gap"
FOLLOWS_CONTRADICTION = "contradiction"
#: Across a roll in place the barcode value steps by one, or by two where the
#: frame grid was placed again after NTP's first sync stepped the clock
#: (wl-sync's `process_uuid` docstring).
ADJACENT_VALUE_STEPS = (1, 2)
#: The most a boundary's tick step may differ from its barcode value step
#: before one clock is contradicted: a roll in place's second frame comes
#: about 1.6 s after the first for a value step of 2, and NTP slews both.
MAX_STEP_DISAGREEMENT_S = 2.0


@dataclass(frozen=True, slots=True)
class SyncBoxSession:
    """A session's sync box segments as clocks (design spec
    `2026-11-04-syncbox-header-design.md` sections 4 and 5). A clock is one
    process of the box (`process_uuid`), its segments' ticks comparable by
    number, and a log written before that field is a process of its own; a
    boundary that contradicts one clock starts another. Per segment, in
    `logs`' order: its clock, how it follows the segment before it on that
    clock (None for a clock's first), and the barcodes that place it -- the
    box's own, less each process's first, whose frame goes out late by an
    amount nothing measures. Session time is the clock of the first segment
    whose barcodes place it, t = 0 at that clock's first."""

    logs: tuple[SyncBoxLog, ...]
    clocks: tuple[int, ...]
    follows: tuple[str | None, ...]
    kept: tuple[tuple[tuple[int, int], ...], ...]
    timed: int | None

    @classmethod
    def from_logs(cls, logs: list[SyncBoxLog]) -> SyncBoxSession:
        clocks: list[int] = []
        follows: list[str | None] = []
        current: dict[str, int] = {}
        for index, log in enumerate(logs):
            if log.process_uuid is None or log.process_uuid not in current:
                clock, how = len(set(clocks)), None
            else:
                clock = current[log.process_uuid]
                members = [logs[i] for i in range(index) if clocks[i] == clock]
                how = _follows(members, log)
                if how.startswith(FOLLOWS_CONTRADICTION):
                    clock = len(set(clocks))
            if log.process_uuid is not None:
                current[log.process_uuid] = clock
            clocks.append(clock)
            follows.append(how)
        dropped: set[str] = set()
        kept = []
        for log in logs:
            barcodes = log.barcodes
            if log.process_uuid is not None and barcodes and log.process_uuid not in dropped:
                dropped.add(log.process_uuid)
                barcodes = barcodes[1:]
            kept.append(barcodes)
        timed = next((clocks[i] for i in range(len(logs)) if kept[i]), None)
        return cls(logs=tuple(logs), clocks=tuple(clocks), follows=tuple(follows), kept=tuple(kept), timed=timed)

    def in_session_time(self, index: int) -> bool:
        return self.timed is not None and self.clocks[index] == self.timed

    @property
    def origin_us(self) -> int:
        """The tick of session t = 0: the first barcode that places session
        time's clock, on the wire."""
        if self.timed is None:
            raise ValueError("the sync box log decoded no barcodes, so session time cannot be established")
        return next(barcodes[0][1] for i, barcodes in enumerate(self.kept) if self.in_session_time(i) and barcodes)

    def seconds(self, tick_us: int) -> float:
        """A tick of session time's clock, in session seconds: the box's own
        timeline, so identity rate (spec section 4.5)."""
        return (tick_us - self.origin_us) / 1e6

    def reference(self) -> dict[int, float]:
        origin_us = self.origin_us
        return {value: (tick_us - origin_us) / 1e6
                for i, barcodes in enumerate(self.kept) if self.in_session_time(i) for value, tick_us in barcodes}

    def codes(self) -> list[tuple[float, int]]:
        """Every code word of session time's clock in session seconds, in
        emission order: a stable sort, since `decode_stream` reads an escape's
        payload by position."""
        origin_us = self.origin_us
        codes = [((tick_us - origin_us) / 1e6, word)
                 for i, log in enumerate(self.logs) if self.in_session_time(i) for tick_us, word in log.codes]
        return sorted(codes, key=lambda pair: pair[0])

    def gaps(self) -> list[tuple[int, int, int]]:
        """`(index, start_us, stop_us)` for each gap on session time's clock:
        the box recorded elsewhere from the last record of the segment before
        `index` to the first of `index`, and the code words of that stretch
        are not in this folder."""
        out = []
        for index, how in enumerate(self.follows):
            if how == FOLLOWS_GAP and self.in_session_time(index):
                before = max(tick_us for i in range(index) if self.clocks[i] == self.clocks[index]
                             for tick_us in _ticks(self.logs[i]))
                out.append((index, before, _ticks(self.logs[index])[0]))
        return out

    def roles(self) -> list[str]:
        """Each segment's role, as `timebase.SyncBoxSegment` stores it: every
        segment of session time's clock keeps session time, holding a barcode
        or not; any other holding one came after a restart, or a
        contradiction, and waits for a bridge; one holding none places
        nothing."""
        return [SEGMENT_SESSION_TIME if self.in_session_time(i) else SEGMENT_AFTER_RESTART if log.barcodes
                else SEGMENT_NO_BARCODE for i, log in enumerate(self.logs)]


def _ticks(log: SyncBoxLog) -> list[int]:
    return sorted([tick_us for _value, tick_us in log.barcodes] + [tick_us for tick_us, _word in log.codes])


def _follows(members: list[SyncBoxLog], log: SyncBoxLog) -> str:
    """How `log` follows the segments before it on its clock, `members`:
    by the barcode values across the boundary where each side has one,
    otherwise by its ticks alone."""
    before = sorted(tick for member in members for tick in _ticks(member))
    after = _ticks(log)
    if not before or not after:
        return FOLLOWS_ADJACENT
    if after[0] < before[-1]:
        return f"{FOLLOWS_CONTRADICTION}: its first tick is before the last of the segment it follows"
    last = max(((tick_us, value) for member in members for value, tick_us in member.barcodes), default=None)
    first = min(((tick_us, value) for value, tick_us in log.barcodes), default=None)
    if last is None or first is None:
        return FOLLOWS_ADJACENT if (after[0] - before[-1]) / 1e6 <= MAX_STEP_DISAGREEMENT_S else FOLLOWS_GAP
    value_step = first[1] - last[1]
    tick_step_s = (first[0] - last[0]) / 1e6
    if value_step <= 0:
        return f"{FOLLOWS_CONTRADICTION}: its barcode values do not go forward"
    if abs(tick_step_s - value_step) > MAX_STEP_DISAGREEMENT_S:
        return (f"{FOLLOWS_CONTRADICTION}: its ticks step {tick_step_s:.1f} s and its barcode values "
                f"{value_step} s")
    return FOLLOWS_ADJACENT if value_step in ADJACENT_VALUE_STEPS else FOLLOWS_GAP


def read_syncbox_session(session_dir: Path) -> SyncBoxSession:
    """The session's sync box as clocks. Raises if the sync box recorded
    nothing: without a reference there is no session time at all, and every
    other system's fit would silently become a fit against whichever device
    happened to be read first."""
    logs = syncbox_logs(session_dir)
    if not logs:
        raise FileNotFoundError(
            f"{session_dir}: no sync box log, so this session has no session "
            "time. Every other system is positioned against it, so there is "
            "nothing to align to rather than one system missing"
        )
    return SyncBoxSession.from_logs(logs)


#: A sync box segment's role, as `timebase.SyncBoxSegment` stores it: session
#: time's clock, holding a barcode or not; no barcode, outside it; and after a
#: restart, not yet bridged (design specs
#: `2026-10-22-syncbox-records-and-segments-design.md` section 6 and
#: `2026-11-04-syncbox-header-design.md` section 4). Part 2 adds a role for
#: each bridge.
SEGMENT_SESSION_TIME = "session_time"
SEGMENT_NO_BARCODE = "no_barcode"
SEGMENT_AFTER_RESTART = "after_restart_unbridged"
SEGMENT_ROLES = (SEGMENT_SESSION_TIME, SEGMENT_NO_BARCODE, SEGMENT_AFTER_RESTART)


def segment_roles(logs: list[SyncBoxLog]) -> list[str]:
    """Each segment's role, in the order of `logs` (`syncbox_logs`'s)."""
    return SyncBoxSession.from_logs(logs).roles()


def session_reference(session_dir: Path) -> dict[int, float]:
    """Barcode value -> session-time seconds, from the sync box's own log.

    Session time is t = 0 at the first barcode that places session time's
    clock (spec section 4.5; design spec
    `2026-11-04-syncbox-header-design.md` section 5): every segment of that
    clock, and no other, read from its `B` records with the barcode delay
    added -- never from `GroundTruth`, which exists only in the synthetic
    generator and will not exist in January.
    """
    session = read_syncbox_session(session_dir)
    if session.timed is None:
        raise ValueError(
            f"{session_dir}: the sync box log decoded no barcodes, so session "
            "time cannot be established"
        )
    return session.reference()


def syncbox_scans(session_dir: Path) -> list[RecordingScan]:
    """The sync box's own scans, one per segment, from its records: its
    barcodes those that place it, as on the wire, and its extent from tick 0
    to its last frame's end (`timebase/extract.py::syncbox_stream`). Nothing is
    rendered into edges and decoded back, which lost a barcode within 400 ms
    of tick 0 (part 1's deferred finding). A segment of session time's clock
    that no barcode places is not scanned: its records are placed by its
    clock, and it is neither a segment nor a rejection."""
    from wl_sync.barcode import Barcode

    from wl_preproc.timebase.extract import syncbox_stream

    session = read_syncbox_session(session_dir)
    return [
        RecordingScan(path=log.path, stream=syncbox_stream(log),
                      barcodes=tuple(Barcode(value=value, start_us=tick_us) for value, tick_us in kept))
        for index, (log, kept) in enumerate(zip(session.logs, session.kept, strict=True))
        if kept or not session.in_session_time(index)
    ]


@dataclass(frozen=True, slots=True)
class RecordingScan:
    """One recording, extracted and decoded, before anything is fitted."""

    path: Path
    stream: BitStream
    barcodes: tuple[Barcode, ...]
    #: How many decoded words were discarded because a gap fell inside them.
    #: Zero on every clean recording, which is all of them until a real
    #: session drops a frame.
    n_barcodes_dropped: int = 0

    @property
    def n_frame_gaps(self) -> int:
        return len(self.stream.gaps)

    @property
    def n_frames_missing(self) -> int:
        return self.stream.n_frames_missing

    @property
    def duration_s(self) -> float:
        return self.stream.n_samples / self.stream.fs_hz

    @property
    def verdict(self) -> str:
        return classify_segment(self.duration_s, len(self.barcodes))


def scan_system(system: str, system_dir: Path) -> list[RecordingScan]:
    """Extract and decode every recording of one system.

    Done once and reused by both the rate fit and the per-segment offsets: the
    rate pools every barcode in the session, and the offsets need the same
    barcodes grouped by recording, so extracting twice would double the I/O on
    the largest files in the pipeline to produce identical bytes.
    """
    from wl_sync.barcode import decode_edges

    from wl_preproc.timebase.extract import EXTRACTORS, barcode_clear_of_gaps, find_recordings

    if system == "syncbox":
        return syncbox_scans(system_dir.parent) if find_recordings(system, system_dir) else []
    scans = []
    for path in find_recordings(system, system_dir):
        stream = EXTRACTORS[system](path)
        decoded = decode_edges(list(stream.edges))
        kept = tuple(b for b in decoded if barcode_clear_of_gaps(b, stream.gaps))
        scans.append(
            RecordingScan(
                path=path,
                stream=stream,
                barcodes=kept,
                n_barcodes_dropped=len(decoded) - len(kept),
            )
        )
    return scans


def rate_fit_from_row(row: dict) -> RateFit:
    """Rebuild a `RateFit` from a stored `SystemTimebase` row.

    The offset fit needs the rate, and the rate lives in the database once it
    has been fitted -- so a segment populated in a later process, or after a
    restart, uses the SAME rate the session was fitted with rather than
    re-deriving one from whatever barcodes happen to be at hand. Re-fitting
    there would silently make the offset depend on which recordings were
    present at that moment.
    """
    from wl_preproc.timebase.fit import RateFit

    return RateFit(
        fitted_rate_hz=row["fitted_rate_hz"],
        drift_ppm=row["drift_ppm"],
        n_matched=row["n_barcodes_matched"],
        residual_us_rms=row["residual_us_rms"],
        residual_us_max=row["residual_us_max"],
    )
