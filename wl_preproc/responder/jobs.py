# wl_preproc/responder/jobs.py
"""`JobRequest` -> rows. Design spec section 6.1. The only module here that writes.

Two sub-projects treated montage boundaries as a blocker before this module
existed: 1c-1 narrowed `submit()` to canonical activations because it had no
block set, and 1c-2 avoided `submit()` entirely because timebase and coverage
populate from `Session` keys alone. **The answer was already in the frozen
contract** (design spec section 1): `contracts.protocol.MetadataBundle`
carries `montage_boundaries` -- and, since design spec
`2026-10-01-session-listing-and-run-requests-design.md`, `runs` -- inbound
with EVERY request, and its own docstring says why -- "everything wl-preproc
needs from the ELN arrives in the request payload." `core.Montage`'s own
comment says it is "Sourced from wl.works `item_insertion` and nothing else".
So recording a montage here is not measuring or guessing at a boundary; it is
wl.works' own authored record, arriving by the exact route its frozen
contract already describes. A run is the other way round: `core.Run` is this
host's measurement, and what arrives is wl.works' copy of it and its id for
it, checked against the measurement and kept in `core.RunAssertion`.

**Two corrections carried from Task 4's review, applied here rather than
re-derived:**

1. **`accept()` opens no transaction of its own.** `submit()`/
   `submit_derivative()` each already guard on `dj.conn().in_transaction` and
   raise -- DataJoint transactions do not nest -- and `submit()`'s own
   docstring says directly that neither the ingest watcher nor the responder
   may wrap it to bundle it with other writes. So `accept()` writes `Montage`
   and `RunAssertion` as two independently idempotent, un-transacted inserts
   (`skip_duplicates=True`, exactly `ingest/landing.py`'s own shape and
   reasoning -- "a partial run followed by a re-run converges on the same
   rows" without one), and only then calls `submit`/`submit_derivative`,
   which open and own their own transaction for the `Request`+`Activation`
   pair. See `test_accept_refuses_to_run_inside_a_transaction`.
2. **`accept()` owns the montage window.** The window between a montage's
   `[start_s, end_s)` and the runs a file holds is checked here -- a check
   `submit_derivative` itself does not make, having no `Montage`/`Run`
   timing to compare against. See `_check_runs`.

**Existing `Montage` rows are never overwritten.** The insert below uses
`skip_duplicates=True`: wl.works owns this record, and a later request naming
different boundaries for a montage already on file is wl.works correcting its
own record -- their call to make explicitly, not something to infer from
whichever payload happened to arrive most recently. A run already asserted
under one `works_run_id` and named under another is a `RunIdConflict`, a
`409`: the two disagree about which run it is.

**Review round 1 (2026-08-16) found four more things, addressed here:**

- **C1 -- validate before writing, not after.** The first draft inserted
  `Montage` and `Block` rows and only THEN checked the window, so a rejected
  request permanently planted the very row that caused its own rejection --
  `skip_duplicates=True` then discarded every later correction, so the
  request that fixed the boundary was rejected too, citing the stale values
  it refused to replace. `accept()` now builds every candidate row, checks
  montage existence and every run against those candidates PLUS whatever is
  already on record, and only writes anything once every check has passed --
  so a rejected request leaves no residue at all. See
  `test_a_rejected_request_leaves_no_montage_or_run_assertion_and_a_
  correction_then_succeeds`.
- **C2 -- `selection["session_datetime"]` is coerced, not assumed to already
  be a `datetime`.** JSON has no datetime type and `docs/schemas/
  job_request.json` declares `selection` as a bare
  `{"type": "object", "additionalProperties": true}` with no `session_
  datetime` property at all, so real wire traffic hands this function a
  plain `str`. `_coerce_session_datetime` accepts a `datetime.datetime` or
  an ISO-8601 string and raises `ValueError` for anything else -- see that
  function's own docstring for the `AttributeError` this replaces.
- **I1 -- the whole payload is normalised, not one key.** The tzinfo-drop
  finding (see `_coerce_session_datetime`'s neighbour, the payload
  construction near the bottom of `accept()`) is not scoped to `selection.
  session_datetime`: any aware `datetime` anywhere in the stored payload
  reproduces the identical defect on a retry. `request.model_dump(mode=
  "json")` -- this project's own existing convention in `contracts/
  manifest.py`, `contracts/done.py` and `synth/peripherals.py`, the only one
  of four `model_dump()` call sites that had omitted it -- serialises every
  nested datetime to a deterministic ISO-8601 string before it is ever
  packed into the blob column, so nothing in the payload can carry a live
  datetime object for the codec to round-trip incorrectly in the first
  place. Confirmed directly: `model_dump(mode="json")` on the same aware
  input produces the identical string on every call, and that string
  round-trips through `datajoint.blob.pack`/`unpack` unchanged.
- **I2/I4 -- the columns this function writes are guarded before insert,
  matching `ingest/params.py`'s own stated convention** ("checked here,
  inside the validation step, rather than left for ... insert to discover as
  a raw `pymysql.err.DataError` -- which Task 8's watcher does not
  special-case"). The `montage_id` range is checked against the real column
  bound before any insert is attempted; the montage's and each run's own
  bounds now live on `contracts.protocol.MontageBoundary` and `RunEntry`, so
  a request breaking them cannot be built.

**The whole-branch review (2026-08-16) found one more, fixed here:**

- **C1 -- a job for a session this host has never ingested was a `500`,
  which the protocol document tells wl.works to retry forever.** `accept()`
  validated montage existence, the window, subject length,
  every column bound and the DATETIME floor -- but not that `Session`
  itself exists, so `core.Montage.insert` hit the foreign key and the
  resulting `IntegrityError` became a retryable `500`. Creating the session
  is still not this function's job; DETECTING its absence now is. See
  `_require_landed_session` for the full reasoning, the measured
  before/after, and why the answer is `422` rather than `409`.

**A design gap this module once flagged is closed.** `core.Block` was
specified as wl-preproc's own measurement, and this function wrote wl.works'
asserted numbers into it, permanently. A request now asserts runs, which
`core.Run` measures from the recording; what wl.works asserts is checked
against that measurement as the request arrives and kept apart from it, in
`core.RunAssertion` (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 3).
"""

from __future__ import annotations

import datetime
import math

from wl_preproc.contracts.protocol import JobRequest, MontageBoundary, ProbeEntry, RunEntry
from wl_preproc.events import agreement
from wl_preproc.ingest import landing
from wl_preproc.schema import DEFAULT_PREFIX, core, pipeline
from wl_preproc.schema import request as schema_request

# The two keys `accept()` itself reads out of an arbitrary, wl.works-supplied
# `selection` dict. `subject` is deliberately NOT among them: session identity
# comes from `metadata.subject` (the ELN's own record), never from anything
# named "subject" inside `selection` -- see `accept()`'s own docstring.
_REQUIRED_SELECTION_KEYS = ("session_datetime", "montage_id")

# Column bounds, read directly from wl_preproc/schema/core.py's declared
# types -- `montage_id` does not declare "unsigned" (unlike
# core.Segment.segment_barcode's "int unsigned"), so its range is SIGNED.
# Checked before any insert, matching ingest/params.py's own stated
# convention for paramset_type/PARAMSET_TYPE_MAX_LEN: a value that is a
# syntactically fine Python int/str can still be too big/long for the column
# it is about to be inserted into, and finding that out from a raw
# pymysql.err.DataError leaves Task 8's handler with an exception type its
# documented ValueError/DataJointError contract does not cover -- confirmed
# with each guard removed in turn against a live column, when this module
# also wrote core.Block: 1406 "Data too long" (a string column), 1264 "Out of
# range value" (an integer one), and 1265 "Data truncated" (a non-finite
# float into a double -- not 1264, corrected after actually reproducing it
# rather than assuming the same errno as the integer case). None of the three
# appears in DataJoint's MySQL adapter's translated-error list.
_MONTAGE_ID_RANGE = (-128, 127)  # core.Montage.montage_id : tinyint
# request.Activation.activation_id : int, and a superseded one is never
# negative: the allocator starts at 0.
_ACTIVATION_ID_RANGE = (0, 2**31 - 1)
# MySQL's DATETIME floor. Python's own datetime.MINYEAR (1) is far below it,
# and DataJoint's bare `datetime` column type validates nothing on its own.
_DATETIME_MIN_YEAR = 1000


def _require_selection_keys(selection: dict) -> None:
    missing = [key for key in _REQUIRED_SELECTION_KEYS if key not in selection]
    if missing:
        raise ValueError(
            f"selection is missing required key(s) {missing}; got {sorted(selection)}"
        )


def _reject_oversized_subject(subject: str) -> None:
    if len(subject) > landing.SUBJECT_MAX_LEN:
        raise ValueError(
            f"metadata.subject {subject!r} is {len(subject)} characters, over "
            f"the {landing.SUBJECT_MAX_LEN}-character limit element-animal's "
            "Subject.subject column enforces (landing.SUBJECT_MAX_LEN)"
        )


def _coerce_session_datetime(value) -> datetime.datetime:
    """A `datetime.datetime`, or an ISO-8601 string, become the value
    `landing.to_naive_utc` accepts. Anything else raises `ValueError`.

    Real wire traffic never carries a `datetime.datetime` at all: JSON has no
    datetime type, `docs/schemas/job_request.json` declares `selection` as a
    bare `{"type": "object", "additionalProperties": true}` with no
    `session_datetime` property (let alone a `format: date-time`), and
    `selection: dict[str, Any]` makes pydantic coerce nothing inside it --
    confirmed directly against the committed schema. So
    `selection["session_datetime"]` arrives as a plain `str` over HTTP, and
    handing that straight to `to_naive_utc` (whose own signature is typed
    `datetime.datetime`) fails with `AttributeError: 'str' object has no
    attribute 'tzinfo'` -- outside this module's documented `ValueError`
    contract, and Task 8's design (a `JobRequest` that validates against
    `JobRequest`'s own schema is, by definition, not "a malformed body")
    would have to map an `AttributeError` it never expected.

    `datetime.fromisoformat` (Python 3.11+, this project's floor) parses a
    "Z" suffix, a numeric UTC offset, or no offset at all -- whatever real
    wl.works traffic sends. A value already naive (no offset in the string)
    is returned as `fromisoformat` parses it and then passed through
    `to_naive_utc` unchanged, exactly as that function's own docstring
    describes for an already-naive input.
    """
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"selection['session_datetime'] {value!r} is not a valid "
                f"ISO-8601 datetime string: {exc}"
            ) from exc
    raise ValueError(
        "selection['session_datetime'] must be a datetime.datetime or an "
        f"ISO-8601 string, got {type(value).__name__}: {value!r}"
    )


def _lifecycle_role(selection: dict, run_numbers: list[int]) -> bool:
    """Whether the request asks for a canonical, from `selection`'s optional
    `role` and `supersedes_activation_id` (design spec
    `2026-09-30-canonical-lifecycle-design.md` section 3). Without a `role`,
    `run_numbers` makes a derivative and none a canonical over the whole
    montage, as `block_ids` did (design spec
    `2026-10-01-session-listing-and-run-requests-design.md` section 3.1).
    Raises `ValueError` (a `422`) for the combinations section 3 refuses,
    before anything is written."""
    role = selection.get("role")
    if role not in (None, "canonical", "derivative"):
        raise ValueError(f"selection['role'] must be 'canonical' or 'derivative', got {role!r}")
    # A null means absent, as a null `role` does (the 2b final review's M10).
    if selection.get("supersedes_activation_id") is not None:
        if role != "canonical":
            raise ValueError(
                "selection['supersedes_activation_id'] needs selection['role'] == 'canonical': only "
                "a canonical supersedes another (parent spec section 8.3)"
            )
        _reject_out_of_range_int(selection["supersedes_activation_id"],
                                 name="selection['supersedes_activation_id']", bounds=_ACTIVATION_ID_RANGE)
    if role == "derivative" and not run_numbers:
        raise ValueError("selection['role'] 'derivative' needs run_numbers: a derivative is its run set")
    return role == "canonical" or (role is None and not run_numbers)


class RunIdConflict(Exception):
    """A run already recorded under one wl.works id, named again under
    another (design spec `2026-10-01-session-listing-and-run-requests-design.md`
    section 3.2): the two disagree about which run this is, which resending
    cannot fix. `server.py` answers it with a 409, as for a reused key."""


_REBUILD = "rebuild the request from a fresh GET /sessions"


def _run_numbers(value, *, name: str) -> list[int]:
    if not isinstance(value, list) or any(isinstance(n, bool) or not isinstance(n, int) for n in value):
        raise ValueError(f"{name} must be a list of run numbers, got {value!r}")
    return value


def _check_runs(session_key: dict, montage_row: dict, asserted: list[RunEntry], run_numbers: list[int],
                canonical: bool) -> tuple[list[int], list[dict]]:
    """The file's runs, and the `core.RunAssertion` rows to record, after
    checking every asserted run against the measured `core.Run` as the
    request arrives (design spec
    `2026-10-01-session-listing-and-run-requests-design.md` section 3.2).

    A canonical holds every measured run whose start lies in its montage's
    window, and each must be asserted here; a derivative holds the runs it
    names, each measured, in the window, and asserted here or before."""
    measured = {row["run_number"]: row for row in (core.Run & session_key).to_dicts()}
    if not measured:
        raise ValueError(
            f"session {session_key['subject']}/{session_key['session_datetime'].isoformat()} has no measured "
            "run on this host yet: its event codes are not yet read, or the rig sent no run markers. Resend "
            "once GET /sessions lists its runs."
        )
    by_number: dict[int, RunEntry] = {}
    for entry in asserted:
        if entry.run_number in by_number:
            raise ValueError(f"metadata.runs names run {entry.run_number} twice")
        by_number[entry.run_number] = entry
    rows = []
    for number, entry in sorted(by_number.items()):
        run = measured.get(number)
        if run is None:
            raise ValueError(f"run {number} is not a measured run of this session; {_REBUILD}")
        for end, asserted_s, measured_s in (("start", entry.start_s, run["run_start_time"]),
                                            ("end", entry.end_s, run["run_stop_time"])):
            if abs(asserted_s - measured_s) > agreement.RUN_AGREEMENT_TOLERANCE_S:
                raise ValueError(f"run {number}'s {end}, {asserted_s} s, is not the measured {measured_s} s; "
                                 f"{_REBUILD}")
        rows.append({**session_key, "run_number": number, "works_run_id": entry.works_run_id,
                     "start_s": entry.start_s, "end_s": entry.end_s})
    on_record = {row["run_number"]: row["works_run_id"] for row in (core.RunAssertion & session_key).to_dicts()}
    for row in rows:
        known = on_record.get(row["run_number"])
        if known is not None and known != row["works_run_id"]:
            raise RunIdConflict(f"run {row['run_number']} is recorded with works_run_id {known!r}; this request "
                                f"names {row['works_run_id']!r}")
    window = [number for number, run in sorted(measured.items())
              if montage_row["start_s"] <= run["run_start_time"] < montage_row["end_s"]]
    bounds = f"montage {montage_row['montage_id']}'s window [{montage_row['start_s']}, {montage_row['end_s']})"
    if canonical:
        if not window:
            raise ValueError(f"{bounds} holds no measured run; {_REBUILD}")
        missing = [number for number in window if number not in by_number]
        if missing:
            raise ValueError(f"measured run(s) {missing} lie in {bounds} and the request does not assert them; "
                             f"{_REBUILD}")
        return window, rows
    named = sorted(set(run_numbers))
    for problem, numbers in (("are not measured runs of this session", [n for n in named if n not in measured]),
                             (f"outside {bounds}", [n for n in named if n in measured and n not in window]),
                             ("asserted neither in metadata.runs nor before",
                              [n for n in named if n not in by_number and n not in on_record])):
        if numbers:
            raise ValueError(f"selection names run(s) {numbers} {problem}; {_REBUILD}")
    return named, rows


def _check_probe_runs(probes: list[ProbeEntry], probe_runs, file_runs: list[int]) -> None:
    """Every probe's runs are stated in full, and only the file's (design
    spec `2026-10-01-session-listing-and-run-requests-design.md` section 3.1;
    wl.works' Plan 20 rule: the record is the run set each probe resolved
    to, not the exclusions)."""
    serials = sorted({probe.serial for probe in probes})
    if probe_runs is None:
        if serials:
            raise ValueError(f"selection.probe_runs must give every probe's runs; metadata.probes names {serials}")
        return
    if not isinstance(probe_runs, dict) or any(not isinstance(serial, str) for serial in probe_runs):
        raise ValueError(f"selection.probe_runs must map each probe's serial to its runs, got {probe_runs!r}")
    if sorted(probe_runs) != serials:
        raise ValueError(f"selection.probe_runs names {sorted(probe_runs)}, and metadata.probes names {serials}: "
                         "every probe's runs are stated, and only theirs")
    for serial, numbers in sorted(probe_runs.items()):
        outside = sorted(set(_run_numbers(numbers, name=f"selection.probe_runs[{serial!r}]")) - set(file_runs))
        if outside:
            raise ValueError(f"probe {serial}'s runs {outside} are not among the file's runs {file_runs}")


def _reject_out_of_range_int(value, *, name: str, bounds: tuple[int, int]) -> None:
    low, high = bounds
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    if not (low <= value <= high):
        raise ValueError(f"{name}={value} is outside the column's range [{low}, {high}]")


def _require_landed_session(session_key: dict) -> None:
    """`ValueError` -- and so `422`, not `500` -- when no `Session` row
    exists for this request's `(subject, session_datetime)`.

    **`accept()` does not create the session, and must not.** That is
    `ingest/landing.py`'s job, and `tests/responder/test_jobs.py`'s
    `landed_session` fixture says so in as many words. What is new here is
    only that the ABSENCE is detected rather than discovered by the
    database: `core.Montage.insert` below carries a foreign key to
    `pipeline.Session`, so without this check the very next write raises
    `IntegrityError`, which `handler.py` maps -- correctly, for every other
    member of that family -- to a `500`. Measured live over a real socket
    before this check existed:

    ```
    POST /jobs, subject/session_datetime never ingested
     -> 500 {"error": "IntegrityError: Cannot add or update a child row: a
        foreign key constraint fails (`e2e_core`.`montage`, CONSTRAINT
        `montage_ibfk_1` FOREIGN KEY (`subject`, `session_datetime`)
        REFERENCES `e2e_session`.`session` ...)"}
    ```

    Three separately-correct decisions composed into one wrong answer on
    the wire: this module does not own session existence, `handler.py` maps
    every non-`KeyReuseError` DataJoint error to `500` deliberately (a lost
    connection IS retryable), and `docs/ops/lab-host-protocol.md` documents
    `500` as retryable. Together they told a conforming wl.works client to
    retry, forever, a request that cannot succeed until something outside
    the retry loop happens. The 500 body also leaked schema, table and
    constraint names to the caller.

    **The trigger is the ordinary case, not an exotic one.** wl.works knows
    a session exists from the ELN hours before its data transfer lands on
    this host, so any button pressed in that window arrives here first. The
    honest answer is "not yet", and it belongs in the status code: `422`
    (the server understood the request and cannot process the instructions
    it contains), joining `accept()`'s existing `ValueError` family and the
    protocol document's own `422` list -- no new status on the wire, no new
    branch in their client.

    `409` was the considered alternative and was ruled out: this document's
    own meaning for it is "the remedy is outside the retry loop, tell a
    human", which fits an ELN typo but not a transfer still in flight, and
    it would force wl.works to parse message text to tell key reuse from a
    session that has simply not arrived yet.

    `pipeline.Session` is read as a module attribute at call time, never
    bound by a `from ... import Session` at this module's import time:
    `pipeline.activate()` REBINDS that global to the activated table (see
    its own `global Session, Subject`), so a name bound earlier would point
    at the unactivated placeholder forever.
    """
    if not (pipeline.Session & session_key):
        raise ValueError(
            f"session {session_key['subject']}/"
            f"{session_key['session_datetime'].isoformat()} is not yet on "
            "record on this host: no Session row exists for it, so there is "
            "nothing to attach a montage, a run or a request to. wl.works "
            "knows a session exists from the ELN before its data transfer "
            "lands here; until ingest has landed it, this host cannot accept "
            "a job for it. Resend once the transfer has completed."
        )


def _build_montage_rows(
    session_key: dict, montage_boundaries: list[MontageBoundary]
) -> list[dict]:
    """`Montage` rows this request WOULD write -- not yet inserted. See
    `accept()`'s two-phase structure (review C1).

    **The three field validations this function used to perform are gone, and
    they were not dropped.** `contracts.protocol.MontageBoundary` now carries
    the tinyint bound and both finite-number checks, so a boundary violating
    any of them cannot be constructed and can never reach here. Keeping the
    calls would have been a check that cannot fire, reading as protection while
    protecting nothing. The status on the wire is unchanged: `handler.py` maps
    `pydantic.ValidationError` and this module's `ValueError` to the same `422`
    -- and `ValidationError` IS a `ValueError` subclass, so even a caller
    catching the old type still catches the new one. What the move buys is that
    `docs/schemas/job_request.json` now carries the constraints, where the
    other implementer of this protocol can read them."""
    return [
        {
            **session_key,
            "montage_id": boundary.montage_id,
            "start_s": boundary.start_s,
            "end_s": boundary.end_s,
        }
        for boundary in montage_boundaries
    ]


def _record_subject_details(subject: str, details) -> None:
    """wl.works' own record of the animal, when the request carries it,
    written into element-animal's tables: `Subject.sex`,
    `Subject.subject_birth_date` and `Subject.Species` (design spec
    `2026-09-28-nwb-builder-design.md` section 9). Absent, nothing about the
    subject changes. The latest request wins: the ELN is the authority."""
    if details is None:
        return
    from wl_preproc.schema import pipeline

    row = {"subject": subject, "sex": details.sex}
    if details.date_of_birth is not None:
        row["subject_birth_date"] = details.date_of_birth
    pipeline.subject.Subject.update1(row)
    if details.species is not None:
        pipeline.subject.Species.insert1({"species": details.species}, skip_duplicates=True)
        pipeline.subject.Subject.Species.insert1(
            {"subject": subject, "species": details.species}, replace=True
        )


def _record_probe_reports(session_key: dict, probes, prefix: str) -> None:
    """Every `metadata.probes` entry, as `ephys.InsertionReport` (the latest
    request wins) and its assignment as an `ephys.AreaAssignment`
    (append-only). Design spec `2026-09-30-nwb-probes-design.md` section
    2.2."""
    if not probes:
        return
    from wl_preproc.schema import ephys

    ephys.activate(prefix=prefix)
    for probe in probes:
        target = probe.target
        ephys.InsertionReport.insert1(
            {
                **session_key,
                "insertion_number": probe.insertion_number,
                "probe_serial": probe.serial,
                "trajectory_id": probe.trajectory_id,
                "target_area": target.area if target else None,
                "target_atlas": target.atlas if target else None,
                "target_atlas_level": target.atlas_level if target else None,
            },
            replace=True,
        )
        assignment = probe.area_assignment
        if assignment is not None:
            asserted_at = assignment.asserted_at
            if asserted_at.tzinfo is not None:
                asserted_at = asserted_at.astimezone(datetime.UTC).replace(tzinfo=None)
            ephys.AreaAssignment.insert1(
                {**session_key, "insertion_number": probe.insertion_number, "asserted_at": asserted_at,
                 "area": assignment.area, "source": assignment.source},
                skip_duplicates=True,
            )


def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
    """A validated `JobRequest` becomes `Montage`/`RunAssertion`/`Request`/
    `Activation` rows, with the activation's runs and each probe's. Design
    spec section 6.1, and design spec
    `2026-10-01-session-listing-and-run-requests-design.md` section 3.
    Returns the `Activation` primary key.

    Raises `ValueError` for a request that cannot be honoured: a `selection`
    missing `session_datetime` or `montage_id`; the retired
    `selection.block_ids`; a `metadata.subject` longer than
    `landing.SUBJECT_MAX_LEN`; a `session_datetime` that is neither a
    `datetime.datetime` nor a parseable ISO-8601 string; a
    `(subject, session_datetime)` with no `Session` row on this host yet
    (`_require_landed_session` -- the ordinary ELN-before-transfer case,
    which used to reach the database and come back as a `500` telling
    wl.works to retry forever); a `montage_id` that cannot fit its column; a
    `montage_id` with no boundary on record and none supplied in this request
    either; or any run that does not match what this host measured
    (`_check_runs`, `_check_probe_runs`). Raises `RunIdConflict` for a run
    already asserted under another `works_run_id`. All of these are checked
    against BUILT-BUT-NOT-YET-WRITTEN candidate rows before anything is
    actually inserted (review C1): a rejected request leaves no `Montage` or
    `RunAssertion` residue behind for a later, corrected request to trip
    over.

    Session identity is `metadata.subject` plus `selection["session_datetime"]`,
    normalised through `landing.to_naive_utc` -- the one conversion every
    other datetime key in this codebase goes through, so this key lands on
    the exact naive value `ingest/landing.py` would already have written for
    the same session (see that module's own docstring on why two call sites
    converting differently is how two "equal" keys stop being equal).
    `selection`'s own `subject`, if wl.works ever sends one, is not read: the
    ELN's record of who this is is `metadata.subject`.

    `selection["run_numbers"]`, when present and non-empty, makes this a
    derivative holding those runs (`submit_derivative`); its absence, or an
    empty list, makes it a canonical holding every measured run of its
    montage, with each probe's runs in `selection["probe_runs"]` (`submit`).
    """
    selection = request.selection
    _require_selection_keys(selection)
    metadata = request.metadata
    # Before anything reads the database: a malformed selection is the
    # caller's to fix, whatever this host holds.
    if selection.get("block_ids"):
        raise ValueError("selection.block_ids is retired: a derivative names its runs in selection.run_numbers, "
                         "and a canonical holds every run of its montage, each probe's in selection.probe_runs")
    run_numbers = _run_numbers(selection.get("run_numbers") or [], name="selection.run_numbers")
    probe_runs = selection.get("probe_runs")
    canonical = _lifecycle_role(selection, run_numbers)
    if canonical and run_numbers:
        raise ValueError("selection.run_numbers is a derivative's: a canonical holds every run of its montage")
    if not canonical and probe_runs is not None:
        raise ValueError("selection.probe_runs is a canonical's: a derivative's runs are its run_numbers")

    _reject_oversized_subject(metadata.subject)

    session_datetime = landing.to_naive_utc(_coerce_session_datetime(selection["session_datetime"]))
    if session_datetime.year < _DATETIME_MIN_YEAR:
        raise ValueError(
            f"selection['session_datetime'] year {session_datetime.year} is "
            f"before {_DATETIME_MIN_YEAR}, MySQL DATETIME's floor"
        )

    montage_id = selection["montage_id"]
    _reject_out_of_range_int(montage_id, name="selection['montage_id']", bounds=_MONTAGE_ID_RANGE)

    session_key = {"subject": metadata.subject, "session_datetime": session_datetime}
    montage_key = {**session_key, "montage_id": montage_id}

    # ---- Phase 1: build every candidate row and validate everything
    # against them plus what already exists, before writing anything at all
    # (review C1). ----

    montage_rows = _build_montage_rows(session_key, metadata.montage_boundaries)
    candidate_montages = {row["montage_id"]: row for row in montage_rows}

    schema_request.activate(prefix=prefix)

    _require_landed_session(session_key)

    existing_montage_row = (
        (core.Montage & montage_key).fetch1() if (core.Montage & montage_key) else None
    )
    montage_row = existing_montage_row or candidate_montages.get(montage_id)
    if montage_row is None:
        raise ValueError(
            f"no montage {montage_id} is on record for {session_key} and "
            "metadata.montage_boundaries did not supply one either"
        )

    # Correction 2: accept() owns the montage window (module docstring).
    file_runs, run_assertion_rows = _check_runs(session_key, montage_row, metadata.runs, run_numbers, canonical)
    if canonical:
        _check_probe_runs(metadata.probes, probe_runs, file_runs)

    # ---- Phase 2: every check above passed. Only now does anything get
    # written. ----

    _record_subject_details(metadata.subject, metadata.subject_details)
    _record_probe_reports(session_key, metadata.probes, prefix)
    # Step 1 (design spec section 6.1): Montage rows, insert-if-absent.
    if montage_rows:
        core.Montage.insert(montage_rows, skip_duplicates=True)
    # Step 2: wl.works' id and copy of each run it asserts, insert-if-absent;
    # `_check_runs` has already refused a run named under a second id.
    if run_assertion_rows:
        core.RunAssertion.insert(run_assertion_rows, skip_duplicates=True)

    # The payload stored as evidence ("the request as received", Request's
    # own comment). mode="json" -- this project's own existing convention in
    # contracts/manifest.py, contracts/done.py and synth/peripherals.py --
    # serialises every nested value, including any datetime anywhere in
    # `selection`/`parameters`/`metadata`, to its deterministic JSON form
    # before it is ever packed into the blob column (review I1). Left as
    # plain Python objects, an aware datetime specifically would compare
    # unequal to itself on a genuine retry: DataJoint's blob codec drops a
    # datetime's tzinfo on its very first round trip through the database
    # (confirmed directly -- pack/unpack an aware value and it comes back
    # naive), so a second accept() call for the same idempotency_key would
    # build a fresh, still-aware payload and compare it against the first
    # call's now-naive stored copy -- and Python treats an aware and a naive
    # datetime as unequal under `==`/`!=` (it never raises, it just compares
    # false), so `_reject_key_reuse`'s `stored != given` would report a
    # difference that is not real and refuse an honest retry as key reuse.
    # mode="json" sidesteps this entirely rather than patching around it:
    # confirmed directly that it renders the same aware datetime to the
    # identical ISO-8601 string on every call, and that string round-trips
    # through datajoint.blob.pack/unpack byte-for-byte, because a string
    # carries no tzinfo for the codec to drop in the first place. See
    # test_accept_is_idempotent_on_the_same_key and
    # test_accept_normalises_an_aware_datetime_anywhere_in_the_stored_payload.
    payload = request.model_dump(mode="json")

    if not canonical:
        return schema_request.submit_derivative(
            idempotency_key=request.idempotency_key,
            task_type=request.domain,
            origin="wl_works",
            selection=montage_key,
            run_numbers=file_runs,
            payload=payload,
            requested_by=metadata.experimenter,
            # A derivative is the unit a sort runs over (decision 3), so each
            # probe the request names covers all of its runs; the request
            # states no per-probe list for one (Plan B's final review, I1).
            probe_runs={probe.serial: file_runs for probe in metadata.probes},
        )

    if selection.get("supersedes_activation_id") is not None:
        return schema_request.submit_replacement(
            idempotency_key=request.idempotency_key,
            task_type=request.domain,
            origin="wl_works",
            selection=montage_key,
            payload=payload,
            requested_by=metadata.experimenter,
            supersedes_activation_id=selection["supersedes_activation_id"],
            run_numbers=file_runs,
            probe_runs=probe_runs,
        )
    return schema_request.submit(
        idempotency_key=request.idempotency_key,
        task_type=request.domain,
        origin="wl_works",
        selection=montage_key,
        payload=payload,
        requested_by=metadata.experimenter,
        run_numbers=file_runs,
        probe_runs=probe_runs,
    )
