"""Which clock keeps a session's time, and the one door every stage reads it
through (design spec `2026-10-18-training-records-and-backup-clock-design.md`
section 4).

A stage asks the session's clock three things: how a system carrying the sync
box's barcode is placed on session time (`barcode_reference`), the session's
codes in session seconds (`codes`), and the wall-clock time of t = 0
(`wall_reference`). `timebase.SessionClock` records which clock that is, chosen
once by `choose` when a stage first asks (`clock_for`), or by the daemon's
first stage (`timebase.SessionClock.populate`).

**Part 3b-1: the sync box, else the NI** (amendments 41-48 and 50). The sync
box keeps time unless its log ends before the session's last run does with no
later segment (amendment 42); otherwise SpikeGLX's NI does, where its codes
hold every run (`timebase/holding.py`); otherwise nothing built yet can, and
lab time is part 4."""

from __future__ import annotations

import dataclasses
import datetime
import functools
import hashlib
import json
import re
import zoneinfo
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

# A barcode's value is whole seconds since wl-sync's `BARCODE_EPOCH`, off the
# sync box's wall clock; read from here by `nwb/gather.py` too.
from wl_sync.clock import BARCODE_EPOCH

from wl_preproc.contracts.events import decode_stream
from wl_preproc.contracts.paths import XCON_DIRNAME
from wl_preproc.events import rigruns, rigtrials
from wl_preproc.events.rigruns import RigRuns, read_rig_runs
from wl_preproc.events.rigtrials import RigRecord, read_rig_trials
from wl_preproc.timebase import segments
from wl_preproc.timebase._nidq_meta import read_nidq_meta
from wl_preproc.timebase.extract import find_recordings
from wl_preproc.timebase.holding import unheld, unreached_end

# What may keep a session's time (section 4.1), as `timebase.SessionClock`
# declares it.
SOURCES = ("syncbox", "nidq", "lab")
# Wall clocks that disagree by more than this are not trusted to place t = 0
# (the NWB builder spec section 4.2). Borrowed from wl-sync's
# CLOCK_TRUST_TOLERANCE_S.
CLOCK_DISAGREEMENT_S = 60.0
# The IANA time zone of the lab's NI machine, whose local time SpikeGLX's
# `fileCreateTime` is written in, with no zone (amendment 46): a lab setting,
# given by `wlpp daemon --nidq-timezone` through `set_nidq_timezone`. Unset,
# an NI file's creation time places nothing.
NIDQ_TIMEZONE: str | None = None


def set_nidq_timezone(name: str) -> None:
    """Set `NIDQ_TIMEZONE`, once `zoneinfo` knows the zone. An unknown or
    malformed name is one `ValueError` naming it, whatever `zoneinfo`
    raised, and leaves the zone as it was."""
    global NIDQ_TIMEZONE
    try:
        zoneinfo.ZoneInfo(name)
    # IsADirectoryError: a zone folder's name ("America") on a Python whose
    # `zoneinfo` opens it from the `tzdata` package (CPython gh-85702).
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, IsADirectoryError) as error:
        raise ValueError(f"{name!r} is not a time zone: the NI machine's zone is an IANA name, such as "
                         "America/New_York") from error
    NIDQ_TIMEZONE = name


class NoClock(Exception):
    """No clock built so far can time this session; the message says why."""


class NotRecordedYet(NoClock):
    """Choosing the session's clock would decode the NI inside a transaction
    (amendment 43); a daemon pass records it first."""


@dataclasses.dataclass(frozen=True)
class Choice:
    source: str
    reason: str


# Why each clock keeps a session's time (the plan's Global Constraints), as
# `timebase.SessionClock.reason` stores it.
SYNC_BOX_HOLDS = "the sync box's codes hold every run the rig recorded"
# Rule 1 by reaching the end (amendment 42), each followed by `unheld`'s
# sentence for what its codes lack (`reaching_reason`).
SYNC_BOX_REACHES_LAST = "the sync box's codes reach the end of the rig's last run; {unheld}"
SYNC_BOX_REACHES_END = "the sync box's codes decode SESSION_END, and there is no rig record; {unheld}"
SYNC_BOX_RESTARTED = ("the sync box restarted within the session; it stays the clock, its restart bridged by wl-sync "
                      "part 2")
NI_HOLDS = "the NI's codes hold every run the rig recorded; the sync box {why}"
# What a stage asking inside its own transaction is told when choosing would
# decode the NI there (amendment 43): the daemon records every session's
# clock first, outside any stage's transaction (`daemon._first_tables`).
NOT_RECORDED_YET = "the session's clock is not recorded yet: a daemon pass records it first"
# `timebase.SessionClock.reason`'s width.
REASON_WIDTH = 255
# The version of `choose`'s rules, in `choice_inputs`' digest, so a refusal
# recorded under older rules (`timebase.ClockRefusal`) is decided again
# rather than reused (the final review's I2). Bump it whenever `choose`, or
# anything it reads a session through (`timebase/holding.py`, the NI's
# reader, the sync box's), changes which clock it chooses or why.
CHOICE_RULES = "3b-3"


def clipped(reason: str, width: int = REASON_WIDTH) -> str:
    """`reason` cut to its column, ending "…" where it was cut: a long name
    would otherwise fail the clock's insert on every pass."""
    return reason if len(reason) <= width else reason[:width - 1] + "…"


def reaching_reason(unheld_sentence: str, *, has_rig_record: bool) -> str:
    """Why the sync box keeps time when its codes do not hold every run but
    reach the end of the session's last run (with no rig record, decode
    `SESSION_END`): what they reach, then `unheld`'s own sentence, clipped."""
    reason = SYNC_BOX_REACHES_LAST if has_rig_record else SYNC_BOX_REACHES_END
    return clipped(reason.format(unheld=unheld_sentence))


# SpikeGLX names each file of a run `<run>_g<gate>_t<trigger>`; the synthetic
# NI's first file carries neither.
_GATE_TRIGGER = re.compile(r"_g(\d+)_t(\d+)\.nidq\.bin$")


def _gate_and_trigger(path: Path) -> tuple:
    found = _GATE_TRIGGER.search(path.name)
    return (0, 0, 0, path.name) if found is None else (1, int(found[1]), int(found[2]), path.name)


def nidq_files(session_dir: Path) -> list[Path]:
    """The session's NI recordings, in the order SpikeGLX wrote them: by gate,
    then trigger, as integers (text order puts `g10` before `g2`), a name
    without them first, then by name. Never by `core.Segment`'s key, the
    file's path, which is no time order."""
    return sorted(find_recordings("spikeglx", session_dir / "spikeglx"), key=_gate_and_trigger)


def nidq_codes(path: Path) -> list[tuple[float, int]]:
    """One NI file's code words, `(seconds from that file's first sample,
    code)`, in emission order, read in chunks by
    `events/extract.py::extract_nidq_words`."""
    from wl_preproc.events import extract

    stream = extract.extract_nidq_words(path)
    return [(sample / stream.fs_hz, word) for sample, word in stream.words]


def _rig_record(session_dir: Path, subject: str) -> tuple[RigRuns | None, RigRecord | None]:
    return read_rig_runs(session_dir, subject), read_rig_trials(session_dir, subject)


def _sync_box_choice(session_dir: Path, landed_without: Mapping[str, str], rig_runs: RigRuns | None,
                     rig_trials: RigRecord | None) -> tuple[Choice | None, str]:
    """Rule 1, decided from the sync box's log and the rig's record, both
    small (amendment 43): the sync box's choice, or None and why it is not,
    as the NI's reason ends. Amendment 42: the NI keeps time only when the
    log ends before the session's last run does, with no later segment, so
    a gap within the session, or a log that starts late, keeps the box."""
    if "syncbox" in landed_without:
        # Decision 8 and amendment 47: never the clock, whatever its partial
        # log holds.
        return None, f"was landed without by {landed_without['syncbox']}"
    if not find_recordings("syncbox", session_dir / "syncbox"):
        return None, "recorded no log"
    session = segments.read_syncbox_session(session_dir)
    if session.timed is None:
        return None, "does not: its log holds no barcode, so it places nothing in session time"
    decoded = decode_stream(session.codes())
    lacking = unheld(decoded, rig_runs, rig_trials)
    if lacking is None:
        return Choice(source="syncbox", reason=SYNC_BOX_HOLDS), ""
    sentence = unreached_end(decoded, rig_runs, rig_trials)
    if sentence is None:
        has_rig_record = rig_runs is not None and bool(rig_runs.runs)
        return Choice(source="syncbox", reason=reaching_reason(lacking, has_rig_record=has_rig_record)), ""
    if segments.SEGMENT_AFTER_RESTART in session.roles():
        # A restart is bridged by a device that recorded through it (wl-sync
        # part 2).
        return Choice(source="syncbox", reason=SYNC_BOX_RESTARTED), ""
    return None, f"does not: {sentence}"


def _no_clock(session_dir: Path, why: str, because: str) -> NoClock:
    return NoClock(f"{session_dir}: the sync box {why}, and {because}; lab time is part 4, so no clock built yet "
                   "can time this session")


def _ni_files(session_dir: Path, landed_without: Mapping[str, str], why: str) -> list[Path]:
    """Rule 2's NI files, or `NoClock` where there is no NI to decode: SpikeGLX
    landed without (decision 8), or no NI recording. Read without decoding."""
    if "spikeglx" in landed_without:
        raise _no_clock(session_dir, why, f"SpikeGLX was landed without by {landed_without['spikeglx']}")
    files = nidq_files(session_dir)
    if not files:
        raise _no_clock(session_dir, why, "there is no NI recording")
    return files


def _ni_choice(session_dir: Path, landed_without: Mapping[str, str], rig_runs: RigRuns | None,
               rig_trials: RigRecord | None, why: str) -> Choice:
    """Rule 2: the NI, when SpikeGLX was not landed without and its codes,
    across every NI file, hold every run. Each file is decoded on its own,
    since a payload cut at a file's end must not take the next file's
    words."""
    decoded = []
    for path in _ni_files(session_dir, landed_without, why):
        try:
            decoded += decode_stream(nidq_codes(path))
        except ValueError as error:
            # Recorded first, so the reason survives its column's width.
            unreadable = str(error).replace(f"{session_dir}/", "")
            raise NoClock(f"{session_dir}: the NI's codes cannot be read: {unreadable}; and the sync box {why}: lab "
                          "time is part 4, so no clock built yet can time this session") from error
    sentence = unheld(decoded, rig_runs, rig_trials)
    if sentence is not None:
        raise _no_clock(session_dir, why, f"the NI's codes do not: {sentence}")
    return Choice(source="nidq", reason=clipped(NI_HOLDS.format(why=why)))


# A change here, or in what it reads a session through, bumps `CHOICE_RULES`.
def choose(session_dir: Path, subject: str, landed_without: Mapping[str, str] = MappingProxyType({})) -> Choice:
    """Section 4.2's rule, as part 3b-1 builds it (the plan's Global
    Constraints). `landed_without` maps each system a person landed the
    session without (`ingest.LandedWithout`) to who did, named in the reason.

    **Rule 1:** the sync box keeps time if it has a log, was not landed
    without, and either its codes reach the end of the session's last run
    (amendment 42), or it restarted within the session. **Rule 2:**
    otherwise the NI keeps time, if SpikeGLX was not landed without and its
    codes hold every run; its codes are decoded only
    here, when rule 1 fails. **Otherwise** `NoClock`, naming both reasons:
    lab time is part 4."""
    rig_runs, rig_trials = _rig_record(session_dir, subject)
    choice, why = _sync_box_choice(session_dir, landed_without, rig_runs, rig_trials)
    if choice is not None:
        return choice
    return _ni_choice(session_dir, landed_without, rig_runs, rig_trials, why)


def choice_inputs(session_dir: Path, subject: str, landed_without: Mapping[str, str], *,
                  already_timed: bool = False) -> str:
    """A sha256 hex digest of what `choose_landed` reads: each sync box log,
    each NI file and its `.meta`, and the rig's record files `read_rig_runs`
    and `read_rig_trials` read, by path within the session, size and
    `mtime_ns` (stat only, never their contents), with what the session was
    landed without, whether it was already timed, and the rules' version
    (`CHOICE_RULES`). A recorded refusal (`timebase.ClockRefusal`) stands
    while this is unchanged, so a session no clock can time is not decoded
    again on every pass, and is decided again once the rules change."""
    xcon = session_dir / XCON_DIRNAME
    paths = [*find_recordings("syncbox", session_dir / "syncbox"),
             *(path for nidq in nidq_files(session_dir) for path in (nidq, nidq.with_suffix(".meta"))),
             xcon / rigruns.RECORD_NAME, xcon / rigruns.CONFIG_NAME, xcon / rigtrials.RECORD_NAME]

    def stat(path: Path) -> list:
        try:
            status = path.stat()
        except FileNotFoundError:
            return [str(path.relative_to(session_dir)), None, None]
        return [str(path.relative_to(session_dir)), status.st_size, status.st_mtime_ns]

    read = {"subject": subject, "files": [stat(path) for path in paths],
            "landed_without": sorted(landed_without.items()), "already_timed": already_timed,
            "rules": CHOICE_RULES}
    return hashlib.sha256(json.dumps(read, sort_keys=True).encode()).hexdigest()


def refusal_reason(error: NoClock, session_dir: Path) -> str:
    """A `NoClock`'s reason as `timebase.ClockRefusal` keeps it: without the
    session's folder, which the row's key already names, clipped to its
    column."""
    reason = str(error)
    prefix = f"{session_dir}: "
    return clipped(reason[len(prefix):] if reason.startswith(prefix) else reason)


def landed_facts(session_key: dict) -> tuple[dict[str, str], bool]:
    """What the database says the choice needs, read and never the folder:
    each system the session was landed without, to who did (amendment 33),
    and whether it was already timed (amendment 48), its events staged or its
    timing recorded."""
    from wl_preproc.schema import ingest, pipeline, timebase

    session = {field: session_key[field] for field in ("subject", "session_datetime")}
    without = {row["system"]: row["actor"]
               for row in (ingest.LandedWithout & session & [{"system": "syncbox"}, {"system": "spikeglx"}]).to_dicts()}
    timed = bool(len(pipeline.event.BehaviorRecording & session) or len(timebase.TimingProvenance & session))
    return without, timed


def choose_landed(session_dir: Path, subject: str, landed_without: Mapping[str, str], already_timed: bool, *,
                  decode_ni: bool = True) -> Choice:
    """`choose`, for a landed session with no clock recorded, from
    `landed_facts`. A session already timed is refused (amendment 48, which
    replaces amendment 30's rule): which clock timed it is not recorded, and
    choosing now could choose the other. With `decode_ni` False, as inside a
    transaction, a session rule 1 does not give the sync box is not chosen
    (amendment 43): `NotRecordedYet`, unless there is no NI to decode, when
    the reason no clock can time it is given, as a stage reports it on each
    pass (part 3a's final review, I3)."""
    if already_timed:
        raise NoClock(
            f"{session_dir}: it was timed before its clock was recorded, so which clock timed it is unknown: its "
            "events (`pipeline.event.BehaviorRecording`) and timing (`timebase.TimingProvenance`) must be deleted "
            "for it to be timed again"
        )
    if decode_ni:
        return choose(session_dir, subject, landed_without)
    choice, why = _sync_box_choice(session_dir, landed_without, *_rig_record(session_dir, subject))
    if choice is None:
        _ni_files(session_dir, landed_without, why)
        raise NotRecordedYet(NOT_RECORDED_YET)
    return choice


def choose_for(session_key: dict, session_dir: Path, *, decode_ni: bool = True) -> Choice:
    """`choose_landed`, reading `landed_facts` for the session now."""
    return choose_landed(session_dir, session_key["subject"], *landed_facts(session_key), decode_ni=decode_ni)


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


def ni_reference_from(started_at: datetime.datetime, rig_at_zero: float | None,
                      file_created: datetime.datetime | None) -> dict:
    """Amendment 46: the wall-clock time of t = 0 under the NI, in
    `reference_from`'s shape. The rig's clock fit (`rig_at_zero`, POSIX
    seconds), else the NI file's creation time (aware), each only within
    `CLOCK_DISAGREEMENT_S` of the manifest's `started_at`; else `started_at`
    itself, its difference from the first of them that disagreed, or None
    where neither gave a time."""
    started_at = aware_utc(started_at)
    offered = []
    if rig_at_zero is not None:
        offered.append(("rig_clock", datetime.datetime.fromtimestamp(rig_at_zero, tz=datetime.timezone.utc)))
    if file_created is not None:
        offered.append(("nidq_meta", file_created.astimezone(datetime.timezone.utc)))
    for source, time in offered:
        difference = (started_at - time).total_seconds()
        if abs(difference) <= CLOCK_DISAGREEMENT_S:
            return {"reference_time": time, "source": source, "manifest_started_at": started_at,
                    "started_at_difference_s": difference}
    return {
        "reference_time": started_at,
        "source": "manifest",
        "manifest_started_at": started_at,
        "started_at_difference_s": (started_at - offered[0][1]).total_seconds() if offered else None,
    }


def syncbox_codes(session_dir: Path) -> list[tuple[float, int]]:
    """Every code word of session time's clock, in session seconds and in
    emission order (`timebase/segments.py::SyncBoxSession.codes`): every
    segment of the process session time comes from, and no other, since a
    later process is on another clock (design specs
    `2026-10-22-syncbox-records-and-segments-design.md` section 4 and
    `2026-11-04-syncbox-header-design.md` section 4).

    Identity rate: session time IS the sync box's own timeline (spec section
    4.5), the special case `SystemTimebase.make()` applies to this same system
    for the same reason. The sync box's codes are the session's codes while it
    is the clock, and every stage reads them through `Clock.codes`."""
    session = segments.read_syncbox_session(session_dir)
    if session.timed is None:
        raise ValueError(
            f"{session_dir}: the sync box log decoded no barcodes, so session "
            "time cannot be established"
        )
    return session.codes()


@functools.lru_cache(maxsize=8)
def _ni_barcodes(path: str, size: int, mtime_ns: int) -> tuple[tuple[int, float], ...]:
    """One NI file's gap-clear barcodes, `(value, seconds from its first
    sample)`, decoded once per file version (the size and mtime key it, as
    `_syncbox_log` keys its own reads)."""
    scans = segments.scan_system("spikeglx", Path(path).parent)
    return tuple((barcode.value, barcode.start_us / 1e6)
                 for scan in scans if scan.path == Path(path) for barcode in scan.barcodes)


def ni_barcodes(session_dir: Path) -> dict[int, float]:
    """The first NI file's own barcodes, value -> NI seconds (amendment 54).
    The NI's first sample is session t = 0 (amendment 50), so these are session
    seconds on the identity map. Empty where the NI decoded none."""
    files = nidq_files(session_dir)
    if not files:
        raise NoClock(f"{session_dir}: the NI's clock has no NI recording")
    status = files[0].stat()
    return dict(_ni_barcodes(str(files[0]), status.st_size, status.st_mtime_ns))


@dataclasses.dataclass(frozen=True)
class Clock:
    """A session's clock, as `timebase.SessionClock` chose it: one of
    `SOURCES`. The sync box's and the NI's are built; lab time's is part 4.

    **Under the NI** (amendment 50), session t = 0 is the first NI file's
    first sample (`nidq_files`), and its codes are that file's. A later NI
    file is a restart, not on this clock (part 3c joins it)."""

    source: str
    session_dir: Path

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"{self.source!r} is not a clock: a session's clock is one of {', '.join(SOURCES)}")
        if self.source == "lab":
            raise NoClock("lab time is not built yet (part 4)")

    def barcode_reference(self) -> dict[int, float]:
        """Barcode value -> session seconds: how a system carrying the sync
        box's barcode is placed on this clock (`timebase/fit.py`). The NI's
        clock places by the NI's own barcodes (amendment 54): the first NI
        file's, gap-clear, value -> NI seconds; empty where it decoded none."""
        if self.source == "nidq":
            return ni_barcodes(self.session_dir)
        return segments.session_reference(self.session_dir)

    def covered_span(self) -> tuple[float, float] | None:
        """The session seconds the barcode reference covers (amendment 55):
        its first and last. Under the NI, None where it decoded no barcode;
        under the sync box, always None, since the whole session is covered."""
        if self.source != "nidq":
            return None
        reference = self.barcode_reference()
        return (min(reference.values()), max(reference.values())) if reference else None

    def codes(self) -> list[tuple[float, int]]:
        """The session's code words, `(session seconds, code)`, in emission
        order: the stream the events stage decodes."""
        if self.source == "nidq":
            files = nidq_files(self.session_dir)
            if not files:
                raise NoClock(f"{self.session_dir}: the NI's clock has no NI recording")
            return nidq_codes(files[0])
        return syncbox_codes(self.session_dir)

    def wall_reference(self, started_at: datetime.datetime, rig_at_zero: float | None = None) -> dict:
        """The wall-clock time of session t = 0, and where it came from.
        Under the sync box, its first barcode's own value, or `started_at`
        (`reference_from`), whatever `rig_at_zero` is. Under the NI
        (`ni_reference_from`), the rig's clock fit, `core.RigClock`'s
        `rig_at_zero` where it fitted; else the first NI file's
        `fileCreateTime`, read in `NIDQ_TIMEZONE`, where that is set; else
        `started_at`."""
        if self.source == "nidq":
            return ni_reference_from(started_at, rig_at_zero, self._ni_file_created())
        reference = self.barcode_reference()
        return reference_from(min(reference, key=reference.get), started_at)

    def _ni_file_created(self) -> datetime.datetime | None:
        """The first NI file's `fileCreateTime` in the lab's NI zone, aware;
        None with the zone unset, or no time in the file. The first file's
        first sample is t = 0 under the NI, and its creation time marks it."""
        if NIDQ_TIMEZONE is None:
            return None
        files = nidq_files(self.session_dir)
        if not files:
            return None
        created = read_nidq_meta(files[0].with_suffix(".meta")).file_created
        return None if created is None else created.replace(tzinfo=zoneinfo.ZoneInfo(NIDQ_TIMEZONE))


def clock_for(session_key: dict, session_dir: Path | None = None) -> Clock:
    """The session's clock, as `timebase.SessionClock` recorded it. A
    session not yet given one is given it now, by `choose_for`, and that choice
    is recorded and never made again (section 4.1, amendment 3). The daemon
    records every session's clock first, outside any stage's transaction
    (`daemon._first_tables`), so a stage asking inside its own transaction
    finds it already there.

    Outside any transaction the choice is computed in full, then inserted.
    Inside one, the NI is never decoded (amendment 43): the sync box is still
    chosen where rule 1 gives it, which needs only its log and the rig's
    record, and a session whose NI would have to be decoded raises the
    reason a daemon pass recorded (`timebase.ClockRefusal`), or
    `NotRecordedYet` where no pass has asked yet, unless another pass has
    committed its clock since this transaction's snapshot. Raises `NoClock`, recording nothing, when no
    clock built so far can time the session."""
    import datajoint as dj

    from wl_preproc.schema import ingest, timebase

    session = {field: session_key[field] for field in ("subject", "session_datetime")}
    if session_dir is None:
        session_dir = Path((ingest.Ingestion & session).fetch1("session_dir"))

    def committed() -> list[dict]:
        # Another pass recorded it after this transaction's snapshot was
        # taken (DataJoint starts every transaction WITH CONSISTENT
        # SNAPSHOT), so a plain read cannot see the row: read the latest
        # committed one (the final review's I1).
        return [{"source": source} for (source,) in dj.conn().query(
            f"SELECT source FROM {timebase.SessionClock.full_table_name} "
            "WHERE subject = %s AND session_datetime = %s FOR SHARE",
            args=(session["subject"], session["session_datetime"])).fetchall()]

    rows = (timebase.SessionClock & session).to_dicts()
    if not rows:
        try:
            choice = choose_for(session, session_dir, decode_ni=not dj.conn().in_transaction)
        except NotRecordedYet:
            rows = committed()
            if not rows:
                # A daemon pass that found no clock can time it recorded why
                # (`timebase.ClockRefusal`); only a session no pass has asked
                # yet is truly not recorded.
                refused = (timebase.ClockRefusal & session).to_dicts()
                if refused:
                    raise NoClock(f"{session_dir}: {refused[0]['reason']}") from None
                raise
        else:
            timebase.SessionClock.insert1({**session, "source": choice.source, "reason": choice.reason},
                                          allow_direct_insert=True, skip_duplicates=True)
            rows = (timebase.SessionClock & session).to_dicts() or committed()
    return Clock(source=rows[0]["source"], session_dir=session_dir)
