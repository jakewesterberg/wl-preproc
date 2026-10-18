"""Which clock keeps a session's time, and the one door every stage reads it
through (design spec `2026-10-18-training-records-and-backup-clock-design.md`
section 4).

A stage asks the session's clock three things: how a system carrying the sync
box's barcode is placed on session time (`barcode_reference`), the session's
codes in session seconds (`codes`), and the wall-clock time of t = 0
(`wall_reference`). `timebase.SessionClock` records which clock that is, chosen
once by `choose` when a stage first asks (`clock_for`).

**Part 2 of that design: the sync box is still the only clock** (its section
2). Every answer here is the sync box's, exactly as the stages computed it
before; the NI (part 3) and lab time (part 4) answer the same three questions
once they are built."""

from __future__ import annotations

import dataclasses
import datetime
from pathlib import Path

from wl_preproc.events.extract import extract_syncbox_words
from wl_preproc.timebase import segments
from wl_preproc.timebase.extract import find_recordings
from wl_preproc.timebase.fit import RateFit, fit_offset

# What may keep a session's time (section 4.1), as `timebase.SessionClock`
# declares it.
SOURCES = ("syncbox", "nidq", "lab")
# Wall clocks that disagree by more than this are not trusted to place t = 0
# (the NWB builder spec section 4.2). Borrowed from wl-sync's
# CLOCK_TRUST_TOLERANCE_S.
CLOCK_DISAGREEMENT_S = 60.0
# A barcode's value is whole seconds since this instant, off the sync box's
# wall clock: wl-sync's `wl_sync/clock.py::BARCODE_EPOCH`, since its commit
# 3ce66b9 (2026-08-17). RESTATED, not imported: the wl-sync commit this
# repository pins predates that module. `tests/nwb/test_clock.py` pins the
# two equal wherever a newer wl-sync is installed.
BARCODE_EPOCH = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)


class NoClock(Exception):
    """No clock built so far can time this session; the message says why."""


@dataclasses.dataclass(frozen=True)
class Choice:
    source: str
    reason: str


def choose(session_dir: Path) -> Choice:
    """Section 4.2's rule, as far as part 2 builds it: the sync box, when it
    recorded the session. The rest of the rule (whether its codes hold every
    run, the NI otherwise, then lab time) arrives with part 3, the first part
    that has another clock to choose."""
    if find_recordings("syncbox", session_dir / "syncbox"):
        return Choice(source="syncbox", reason="the sync box recorded this session")
    raise NoClock(
        f"{session_dir}: no sync box log, so no clock is built for this session yet: a session "
        "without the sync box is timed by the NI (part 3 of the design) or by lab time (part 4)"
    )


def aware_utc(value: datetime.datetime) -> datetime.datetime:
    return value.replace(tzinfo=datetime.timezone.utc) if value.tzinfo is None else value


def reference_from(first_barcode_value: int, started_at: datetime.datetime) -> dict:
    """The NWB builder spec section 4.2: the first barcode's own value places
    session t = 0 on the wall clock, unless it disagrees with the manifest's
    `started_at` by more than `CLOCK_DISAGREEMENT_S`, when `started_at` is
    used and the source says so."""
    barcode_time = BARCODE_EPOCH + datetime.timedelta(seconds=int(first_barcode_value))
    started_at = aware_utc(started_at)
    difference = (started_at - barcode_time).total_seconds()
    trusted = abs(difference) <= CLOCK_DISAGREEMENT_S
    return {
        "reference_time": barcode_time if trusted else started_at,
        "source": "barcode" if trusted else "manifest",
        "manifest_started_at": started_at,
        "started_at_difference_s": difference,
    }


def syncbox_codes(session_dir: Path) -> list[tuple[float, int]]:
    """Every code word the sync box recorded, native time converted to session
    time via `timebase/fit.py` -- the same fit primitives `core.Segment` and
    `timebase.SystemTimebase` use, so this does not invent a second
    conversion.

    Identity rate, not `fit_rate`: session time IS the sync box's own timeline
    (spec section 4.5), the same special case `SystemTimebase.make()` applies
    to this same system for the same reason -- a fit of it against itself is
    exactly zero drift with zero residual, the truth rather than a placeholder.

    Moved here from `schema/events.py::decode_syncbox_in_session_time`
    unchanged: the sync box's codes are the session's codes while it is the
    clock, and every stage reads them through `Clock.codes`."""
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


@dataclasses.dataclass(frozen=True)
class Clock:
    """A session's clock, as `timebase.SessionClock` chose it."""

    source: str
    session_dir: Path

    def __post_init__(self) -> None:
        if self.source != "syncbox":
            raise NoClock(f"the {self.source} clock is not built yet; only the sync box's is (part 2)")

    def barcode_reference(self) -> dict[int, float]:
        """Barcode value -> session seconds: how a system carrying the sync
        box's barcode is placed on this clock (`timebase/fit.py`)."""
        return segments.session_reference(self.session_dir)

    def codes(self) -> list[tuple[float, int]]:
        """The session's code words, `(session seconds, code)`, in emission
        order: the stream the events stage decodes."""
        return syncbox_codes(self.session_dir)

    def wall_reference(self, started_at: datetime.datetime) -> dict:
        """The wall-clock time of session t = 0, and where it came from
        (`reference_from`)."""
        reference = self.barcode_reference()
        return reference_from(min(reference, key=reference.get), started_at)


def clock_for(session_key: dict, session_dir: Path | None = None) -> Clock:
    """The session's clock, as `timebase.SessionClock` recorded it. A
    session not yet given one is given it now, by `choose`, and that choice
    is recorded and never made again (section 4.1): the first stage to ask
    is the events stage, in the daemon's order. Raises `NoClock`, recording
    nothing, when no clock built so far can time the session."""
    from wl_preproc.schema import ingest, timebase

    session = {field: session_key[field] for field in ("subject", "session_datetime")}
    if session_dir is None:
        session_dir = Path((ingest.Ingestion & session).fetch1("session_dir"))
    rows = (timebase.SessionClock & session).to_dicts()
    if not rows:
        choice = choose(session_dir)
        timebase.SessionClock.insert1({**session, "source": choice.source, "reason": choice.reason},
                                      allow_direct_insert=True, skip_duplicates=True)
        rows = (timebase.SessionClock & session).to_dicts()
    return Clock(source=rows[0]["source"], session_dir=session_dir)
