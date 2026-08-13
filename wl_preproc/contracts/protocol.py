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
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

SCHEMA_VERSION = 1

_MARKUP_RE = re.compile(r"[<>&]")


def contains_markup(text: str) -> bool:
    """True if text holds any character that could be interpreted as markup."""
    return _MARKUP_RE.search(text) is not None


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

    verdict: str
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
