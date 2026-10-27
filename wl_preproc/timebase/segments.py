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
    `seg-999.log`. Each is one run of the box's process on a clock of its own,
    and a restart resumes into the open session's folder (wl-sync's
    `segment.py`; design spec `2026-10-22-syncbox-records-and-segments-design.md`
    section 4), so no tick is ever compared across two of them."""
    from wl_preproc.timebase.extract import find_recordings

    logs = [read_syncbox_log(path) for path in find_recordings("syncbox", session_dir / "syncbox")]
    return sorted(logs, key=lambda log: (log.segment_index, log.path.name))


def session_log(session_dir: Path) -> SyncBoxLog:
    """The segment session time comes from: the first holding a barcode. A box
    that crashed within its first second logged none in that segment.

    Raises if the sync box recorded nothing, or no barcode in any segment:
    without a reference there is no session time at all, and every other
    system's fit would silently become a fit against whichever device happened
    to be read first."""
    logs = syncbox_logs(session_dir)
    if not logs:
        raise FileNotFoundError(
            f"{session_dir}: no sync box log, so this session has no session "
            "time. Every other system is positioned against it, so there is "
            "nothing to align to rather than one system missing"
        )
    for log in logs:
        if log.barcodes:
            return log
    raise ValueError(
        f"{session_dir}: the sync box log decoded no barcodes, so session "
        "time cannot be established"
    )


def session_reference(session_dir: Path) -> dict[int, float]:
    """Barcode value -> session-time seconds, from the sync box's own log.

    Session time is t=0 at the sync box's FIRST barcode, on every session type,
    one code path (spec section 4.5), within the segment `session_log` names:
    a later segment, after a restart, is on another clock and gives it nothing
    (design spec `2026-10-22-syncbox-records-and-segments-design.md` section
    4). Built by decoding that segment and rebasing to its first barcode --
    never from `GroundTruth`, which exists only in the synthetic generator and
    will not exist in January.
    """
    from wl_sync.barcode import decode_edges

    from wl_preproc.timebase.extract import extract_syncbox

    decoded = decode_edges(list(extract_syncbox(session_log(session_dir).path).edges))
    if not decoded:
        raise ValueError(
            f"{session_dir}: the sync box log decoded no barcodes, so session "
            "time cannot be established"
        )

    origin_us = min(barcode.start_us for barcode in decoded)
    return {barcode.value: (barcode.start_us - origin_us) / 1e6 for barcode in decoded}


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
