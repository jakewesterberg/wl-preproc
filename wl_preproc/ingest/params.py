"""Parameters that travelled with the raw data, per spec section 5.4.

Registration only. Section 5.4 also says "and inserts the request row", and this
does not: a request means an activation, and an activation needs a montage this
component cannot measure (spec section 2). Nothing is lost, because a paramset's
identity is its content hash — whenever the request is eventually made it
resolves to this same set.
"""

from __future__ import annotations

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.schema import DEFAULT_PREFIX, paramset

PARAMS_FILENAME = "session_params.yaml"


class SessionParams(BaseModel):
    """The envelope `session_params.yaml` must satisfy.

    `extra="forbid"` is the whole point (spec section 5.4): a stray or
    misspelled top-level key — `nblocks` sitting beside `params` instead of
    inside it, or `paramset_type` misspelled as `paramset` — fails loudly here
    rather than being ignored and silently processed under lab defaults.

    `params` is deliberately a bare `dict`, not a nested model. Section 5.3
    describes one `ParamSet` table shared by preprocessing, artifact removal,
    clustering, LFP, MUA and eye detection, storing `params` as an opaque
    content-hashed blob — nothing in that table, or anywhere else in this
    codebase, declares what keys are valid for a given `paramset_type`. This
    module cannot enforce a per-type key set that does not exist yet, so a
    typo *inside* `params` (`nblocks` where a clustering consumer expects
    `n_blocks`) is not caught here — seeded that way on purpose. See
    `test_a_nested_unknown_key_inside_params_is_not_rejected` for the check
    that pins this down and explains it in place.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    paramset_type: str
    params: dict


def register_session_params(
    layout: SessionLayout, prefix: str = DEFAULT_PREFIX
) -> int | None:
    """Validate, content-hash and register. Returns None when there is no file.

    Raises ValueError on anything malformed, which the caller turns into a
    `params_invalid` quarantine. Processing under lab defaults because the
    session's own parameter file was broken is exactly the silent-default
    failure section 5.4 exists to prevent.

    `path.exists()` and `path.read_text()` are not guarded against a raw
    `OSError` the way the rest of this package guards its filesystem calls:
    an I/O failure (a permissions fault, a transient NAS read error) is not
    the same claim as "this file's content is invalid" — verify.py keeps that
    exact distinction between "unreadable" and a content mismatch — so it is
    left to propagate as itself rather than being folded into `ValueError`
    and misreported as a `params_invalid` quarantine for a file that was
    never actually read.
    """
    path = layout.dir / PARAMS_FILENAME
    if not path.exists():
        return None

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        declared = SessionParams.model_validate(loaded)
    except (yaml.YAMLError, ValidationError, TypeError) as exc:
        raise ValueError(f"{PARAMS_FILENAME} is not valid: {exc}") from exc

    paramset.activate(prefix=prefix)
    return paramset.register(declared.paramset_type, declared.params)
