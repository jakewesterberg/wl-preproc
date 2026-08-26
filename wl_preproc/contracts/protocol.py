"""wl.works <-> wl-preproc protocol. Frozen interface — see spec section 11.2.

Transport is pull-only: wl.works opens every connection and this host never
initiates, because the app binds only to a WireGuard interface and this machine
is on the lab LAN. Consequently everything wl-preproc needs from the ELN arrives
in the request payload, which is why JobRequest carries a MetadataBundle.

wl.works renders our strings as escaped plain text because a compromised host
controls its UI. We refuse to emit markup at all rather than relying on that.
"""

from __future__ import annotations

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


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: str
    selection: dict[str, Any]
    parameters: dict[str, Any]
    idempotency_key: str
    metadata: MetadataBundle
