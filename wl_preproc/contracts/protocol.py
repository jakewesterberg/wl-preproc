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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

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


class MetadataBundle(BaseModel):
    """Everything wl-preproc needs from the ELN, carried inbound with the request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    blocks: list[dict[str, Any]]
    montage_boundaries: list[dict[str, Any]]
    probes: list[dict[str, Any]]
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
