"""wl.works <-> wl-preproc protocol. Frozen interface — see spec section 11.2.

Transport is pull-only: wl.works opens every connection and this host never
initiates, because the app binds only to a WireGuard interface and this machine
is on the lab LAN. Consequently everything wl-preproc needs from the ELN arrives
in the request payload, which is why JobRequest carries a MetadataBundle.

wl.works renders our strings as escaped plain text because a compromised host
controls its UI. We refuse to emit markup at all rather than relying on that.
"""

from __future__ import annotations

import datetime
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1

Verdict = Literal["ok", "degraded", "down", "unknown"]
"""The four values wl.works validates against, per its Plan 10 section 1.1.

This host emits only three of them. `unknown` is what *wl.works* records when
a host goes silent past its `stale_after_seconds` -- it is their word for our
absence, and we are never in a position to assert it about ourselves. See
`responder/health.py`.
"""

_MARKUP_RE = re.compile(r"[<>&]")


def contains_markup(text: str) -> bool:
    """True if text holds any character that could be interpreted as markup."""
    return _MARKUP_RE.search(text) is not None


# A distinct, literal substitute per character -- never one shared placeholder
# for all three. Collapsing `<`, `>` and `&` onto a single stand-in would make
# two different offending inputs (`A&B` and `A<B`) render identically once
# substituted, which is the exact rule `Reading`/`Action` themselves already
# argue from: two different facts must never render the same way. None of the
# three substitutes contains `<`, `>` or `&` itself, so applying them in any
# order cannot re-introduce the character it just removed, and they are named
# after the character rather than shaped like real markup (no leading `&` or
# trailing `;`) so a reader -- human or wl.works' own renderer -- can never
# mistake the substitute for the markup it stands in for.
_MARKUP_SUBSTITUTES = {"<": "(lt)", ">": "(gt)", "&": "(amp)"}


def plain_text(text: str) -> str:
    """`text`, safe to interpolate into a `Reading`/`Action` field that a
    producer does not fully control -- an exception message, a filesystem
    path -- where the content is a genuine fact worth reporting and simply
    dropping it on a markup collision would discard the one thing the field
    exists to carry.

    Not a fallback triggered by `_reject_markup`'s `ValidationError`: a
    fallback has to be constructible on its own, which means it has to be a
    constant, which means it cannot carry the very fault text it was
    handed -- and it would render a markup collision identically to a
    genuine defect (a mistyped f-string, a raw HTML fragment pasted into a
    label), which is the rule this module's own docstring gives for why
    markup is refused outright rather than escaped ("We refuse to emit
    markup at all rather than relying on that"). So this runs BEFORE
    construction, on the specific text a producer knows is untrusted, and
    the reading still carries the real fault -- spelled out, not hidden.

    Use on interpolated, producer-supplied VALUES only. Never on `label`:
    every `label` in this codebase is a hardcoded constant, and markup
    appearing in a constant is a real defect that must keep failing loudly
    at construction, exactly as `_reject_markup` already does for it.
    """
    for char, substitute in _MARKUP_SUBSTITUTES.items():
        text = text.replace(char, substitute)
    return text


def _reject_markup(value: str) -> str:
    if contains_markup(value):
        raise ValueError(f"markup is not permitted in rendered strings: {value!r}")
    return value


class Reading(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    value: str
    featured: bool

    @field_validator("label", "value")
    @classmethod
    def _plain_text_only(cls, value: str) -> str:
        return _reject_markup(value)


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    label: str

    @field_validator("label")
    @classmethod
    def _plain_text_only(cls, value: str) -> str:
        return _reject_markup(value)


class HealthResponse(BaseModel):
    """Served at the health check URL wl.works polls.

    The host publishes its own action list, so adding a sixth job type needs no
    change on the wl.works side.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: Verdict
    readings: list[Reading]
    actions: list[Action]


# Bounds that mirror a column this payload lands in, so a value that cannot be
# stored is refused on the wire rather than truncated by MySQL or raised as an
# `IntegrityError` two layers down. Each names its column: when the column
# moves, grep finds this.
_MontageId = Annotated[int, Field(ge=-128, le=127)]  # core.Montage.montage_id : tinyint
_InsertionNumber = Annotated[int, Field(ge=0, le=255)]  # ephys.ProbeInsertion : tinyint unsigned
_ProbeSerial = Annotated[str, Field(max_length=32)]  # ephys.Probe.probe_serial : varchar(32)
_TrajectoryId = Annotated[str, Field(max_length=64)]  # ephys.ProbeInsertion : varchar(64)
# `allow_inf_nan=False` does NOT survive export -- JSON Schema has no way to say
# "finite", so `docs/schemas/job_request.json` shows these as a bare `number`.
# Stated rather than left to be discovered: this is the one constraint here that
# the published artifact cannot carry, so wl.works' fake will not inherit it.
# It is still worth enforcing, because Python's own `json.loads` accepts the
# non-standard `NaN` and `Infinity` literals by default -- so a payload that no
# conforming JSON writer could produce is nonetheless one this host could parse.
_SessionSeconds = Annotated[float, Field(allow_inf_nan=False)]


class MontageBoundary(BaseModel):
    """One maximal interval with no probe movement and no bank change, as
    wl.works asserts it -- design spec section 8.3's "recording montage".

    Typed rather than `dict[str, Any]` because this payload is a frozen
    interface with a second implementer: section 11.2 records that wl.works'
    18b tests are contract tests against a *fake* wl-preproc, and a fake can
    only be built from what the contract writes down. An untyped list exports
    to `{"type": "object", "additionalProperties": true}`, which tells that
    implementer nothing at all -- while `responder/jobs.py` validated these
    fields anyway, in a module no other repository reads.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    montage_id: _MontageId
    start_s: _SessionSeconds
    end_s: _SessionSeconds


_Area = Annotated[str, Field(min_length=1, max_length=32)]  # ephys.InsertionLocation.area : varchar(32)


class InsertionTarget(BaseModel):
    """The insertion's aim, as wl.works' `item_insertion` holds it:
    `targetArea`, `atlas` and `atlasLevel`, "a copy of the plan's target"
    (its Plan 19). All three or none (design spec
    `2026-09-30-nwb-probes-design.md` section 4): an area without its atlas
    names no place."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    area: _Area
    atlas: Annotated[str, Field(min_length=1, max_length=32)]  # InsertionLocation.atlas : varchar(32)
    atlas_level: Annotated[int, Field(ge=0, le=255)]  # InsertionLocation.atlas_level : tinyint unsigned


class InsertionAreaAssignment(BaseModel):
    """The latest of wl.works' `insertion_area_assignment` rows for the
    insertion: one area, who or what assigned it, and when. Plan 19's row
    lists no atlas column, so none is carried (section 4)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    area: _Area
    source: Literal["histology", "functional_mapping", "waveform_depth", "structural_imaging", "at_rig", "other"]
    asserted_at: datetime.datetime


class ProbeEntry(BaseModel):
    """One penetration: which probe, which insertion, and which trajectory it
    ran against. Section 11.2's payload block, verbatim: *"probe serials +
    insertions, trajectory_id per insertion"*.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    serial: _ProbeSerial
    insertion_number: _InsertionNumber

    # **Optional, and the absence is not always temporary.** Ruled 2026-08-26:
    # probes are sometimes inserted along a trajectory that was never planned,
    # so at insertion time there is no trajectory resource to name -- not a
    # planned one (none was designed) and not an achieved one (no post-operative
    # scan has happened). wl-works' 2026-08-22-trajectory-identity-design.md
    # section 9 item 1 leaves this open on their side and warns against "a null
    # that means three things"; section 9 item 2 is why the case is legitimate
    # rather than an error.
    #
    # So this host records what arrived and infers nothing from its absence.
    # Null here means "no trajectory was supplied with this request" and NOT
    # which of the reasons applies -- the same discipline as `core.Block`'s
    # "recording an assertion is not authoring it".
    #
    # **It is not a quarantine condition, and must never be confused with one.**
    # Design spec section 8.3's "no insertion record -> no canonical" is about a
    # missing INSERTION, which leaves a probe move invisible and would have the
    # sort run straight across it. A present insertion naming no trajectory
    # hides nothing: the montage is still known, and only the electrode ->
    # CT/MR chain is unavailable for that penetration.
    trajectory_id: _TrajectoryId | None = None

    # Both optional, so requests without them stay valid (section 4). The
    # file carries both, each labelled for what it is (the requester's
    # decision 1 of 2026-09-30).
    target: InsertionTarget | None = None
    area_assignment: InsertionAreaAssignment | None = None


class SubjectDetails(BaseModel):
    """The animal, as wl.works' own record states it: what an NWB file's
    `subject` needs beyond an id (design spec
    `2026-09-28-nwb-builder-design.md` section 9, the requester's decision
    of 2026-09-28). Optional in the request; `responder/jobs.py::accept`
    writes it into element-animal's own tables, replacing the stub
    `ingest/landing.py` lands."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # A Latin name, as element-animal's `Species` prefers for NWB export.
    species: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    sex: Literal["M", "F", "U"] = "U"
    date_of_birth: datetime.date | None = None


class MetadataBundle(BaseModel):
    """Everything wl-preproc needs from the ELN, carried inbound with the request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # `blocks` is the third field of this shape and is deliberately still
    # untyped -- not overlooked. The 2026-08-23 handoff verified exactly two
    # holes in this payload, and this was not one of them; typing it is the same
    # small piece of work as the two below (`responder/jobs.py::_build_block_rows`
    # already carries the bounds), left as its own change rather than folded in
    # unasked. Until then the exported contract is strict about two of its three
    # list fields, which a reader of `docs/schemas/job_request.json` will notice.
    blocks: list[dict[str, Any]]
    montage_boundaries: list[MontageBoundary]
    probes: list[ProbeEntry]
    experimenter: str
    subject: str
    task_types: list[str]
    subject_details: SubjectDetails | None = None


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: str
    selection: dict[str, Any]
    parameters: dict[str, Any]
    idempotency_key: str
    metadata: MetadataBundle


# -- NWB files: GET /nwb and PUT /nwb/active (design spec
# `2026-09-29-nwb-publishing-design.md` section 6). -------------------------


class ActivationKey(BaseModel):
    """One activation, as wl.works names it back to this host. A naive
    `session_datetime` is UTC, as every one this host issues is."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: Annotated[str, Field(min_length=1, max_length=64)]
    session_datetime: datetime.datetime
    montage_id: _MontageId
    activation_id: Annotated[int, Field(ge=0)]


class ActiveSetRequest(BaseModel):
    """`PUT /nwb/active`: the WHOLE set of activations wl.works wants on the
    fast share, every time. Sending the same set twice changes nothing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    activations: list[ActivationKey]
    requested_by: Annotated[str, Field(min_length=1, max_length=64)] | None = None


class ActiveSetResponse(BaseModel):
    """`202`: how many activations the set holds, and which of them this
    host has no activation for yet (kept; they may be built later)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: int
    unknown: list[ActivationKey]


class Placement(BaseModel):
    """Where a published file is: wl.works' location triple (its Plan 23
    section 10.1), the path relative to the share, and which share tier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tier: Literal["slow", "fast"]
    host: str
    share: str
    path: str
    n_bytes: int


class NwbListingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    activation: ActivationKey
    identifier: str
    status: Literal["written", "invalid", "refused"]
    reason: str
    placement: Placement | None
    # `contracts/nwb_description.py::NwbDescription`, exported on its own as
    # `nwb_description.json`; null for a refused activation.
    description: dict[str, Any] | None
    # The replacement canonical that supersedes this activation, or null
    # (design spec `2026-09-30-canonical-lifecycle-design.md` section 4).
    superseded_by: int | None


class NwbListing(BaseModel):
    """`GET /nwb?since=<cursor>`: every file whose record changed after the
    cursor, and the cursor to send next time. The cursor only increases."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cursor: int
    files: list[NwbListingEntry]


# -- Landed sessions: GET /sessions (design spec
# `2026-10-01-session-listing-and-run-requests-design.md` section 2). --------

ListedFlagCode = Literal[
    "waiting_for_run_markers",
    "repeated_run_number",
    "repeated_block_number",
    "run_without_block",
    "bank_change_in_run",
    "block_type_unknown",
    "task_unknown",
    "block_outside_runs",
]


class ListedFlag(BaseModel):
    """A fact wl.works shows beside the session as a hint. None blocks
    anything here. `run_number` and `block_number` say where, when it is one
    run or one block."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ListedFlagCode
    message: str
    run_number: int | None = None
    block_number: int | None = None


class ListedProbe(BaseModel):
    """One probe in one SpikeGLX segment, from `ephys.ProbeCensus`. Two
    segments with the same `electrode_config_hash` for a serial share a bank
    setting. `electrodes` are SpikeGLX electrode numbers (bank x 384 +
    channel for the NP1.0 family), the saved channels only; `imro_table` is
    the segment's `~imroTbl`, verbatim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stream: str
    serial: str | None
    part_number: str | None
    probe_type: str | None
    electrode_config_hash: str | None
    n_electrodes: int | None
    electrodes: list[int] | None
    imro_table: str | None
    problem: str


class ListedSegment(BaseModel):
    """One SpikeGLX file's extent on the recording's clock, and its probes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    segment_barcode: int
    start_s: float
    end_s: float
    probes: list[ListedProbe]


class ListedBlock(BaseModel):
    """A measured block: consecutive trials under one block type, inside a
    run. `block_number` is its number in the session, as `BLOCK_START`
    strobes it; `block_in_run` its order in its run, from 1. `closed` is
    null when it was never recorded: a session event-staged before
    2026-10-01 kept no block closure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    block_number: int
    block_in_run: int
    block_type: str | None
    start_s: float
    end_s: float
    closed: bool | None
    n_trials: int


class ListedRun(BaseModel):
    """A measured run (`core.Run`) with the rig's record of it
    (`core.RunRecord`). `segments` names, by barcode, the SpikeGLX segments
    it spans; each is listed once, in the session's `segments`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_number: int
    task_code: int
    task: str | None
    start_s: float
    end_s: float
    closed: bool
    stopped_because: str | None
    stop_kind: str | None
    segments: list[int]
    blocks: list[ListedBlock]


class RejectedFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    system: str
    file_path: str
    reason: str


class SessionEntry(BaseModel):
    """One landed session, as it stands now. `tier` is the timing tier, null
    until it is computed; D is quarantined, not published automatically."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str
    session_datetime: datetime.datetime
    session_name: str
    tier: Literal["A", "B", "C", "D"] | None
    rejected_segments: list[RejectedFile]
    runs: list[ListedRun]
    segments: list[ListedSegment]
    probes: list[str]
    flags: list[ListedFlag]


class SessionListing(BaseModel):
    """`GET /sessions?since=<cursor>`: every session whose entry changed after
    the cursor, as it stands now, and the cursor to send next time. The
    cursor only increases."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cursor: int
    sessions: list[SessionEntry]
