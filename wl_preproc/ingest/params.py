"""Parameters that travelled with the raw data, per spec section 5.4.

Registration only. Section 5.4 also says "and inserts the request row", and this
does not: a request means an activation, and an activation needs a montage this
component cannot measure (spec section 2). Nothing is lost, because a paramset's
identity is its content hash — whenever the request is eventually made it
resolves to this same set.

`register_session_params`'s exception contract, precisely scoped: reading and
validating the file returns `None` only when it is verifiably absent, and
raises `ValueError` for every other way *that step* can fail, including an
I/O fault -- a permissions error, a transient NFS read error -- reading or
even stat-ing the file, not just a malformed one. See that function's
docstring for why a raw `OSError` must never be the one left uncaught there.

That guarantee covers the read-and-validate step only, not the whole
function. The registration call after it, `paramset.register`
(`wl_preproc/schema/paramset.py`), is not wrapped by it: that function's own
contention-exhaustion path -- a designed-for retry loop, not a hypothetical,
see its docstring and `_MAX_REGISTER_ATTEMPTS` -- raises a bare
`dj.DataJointError` after 10 failed attempts to allocate an index, and this
module neither catches nor converts it.
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

    `OSError` from `path.exists()` or `path.read_text()` -- a permissions
    fault, a transient NFS read error -- is folded into that same ValueError
    rather than left to propagate as itself.

    That is a correction, not the original design: an earlier version of this
    function left OSError unguarded, reasoning that "unreadable" and "invalid
    content" are different claims and citing verify.py as precedent for
    keeping them apart. Review found that reasoning backwards. verify.py
    *catches* exactly this exception class --
    `except (OSError, RuntimeError) as exc: ... f"unreadable: {exc}"` -- and
    reports it as a labelled, contained outcome; it never lets it propagate.
    Task 8's watcher (`_scan_one`) catches only `ValueError` around the call
    to this function, so an uncaught OSError here would escape `_scan_one`,
    escape `scan_once`'s dict comprehension over every session directory in
    the root, and abort the entire scan -- not just this one session. That is
    the identical unguarded-filesystem-call defect `sentinel.py` and
    `discover.py` were each fixed for earlier in this same phase.

    It is worse here than a misclassification would be there, which is why
    the fix is "raise" and not "swallow to the same answer an absent file
    gets": this function only ever runs during a session's *first* successful
    scan -- `_scan_one` short-circuits to ALREADY on every later one, before
    ever reaching this call, once `land_session` has written the `Ingestion`
    row. A swallowed-to-None OSError on that one scan would not misreport
    once and self-correct next time; it would permanently and silently drop
    that session's declared paramset, with no future scan ever retrying it.
    Raising means the session quarantines instead and is reconsidered on the
    next scan, exactly like a malformed file already does.

    The distinguishing detail (which errno, which path) is not lost by
    folding into one exception type: `str(exc)` on an `OSError` already
    carries both, and Task 8's `quarantine(detail={"error": str(exc)[:2000]},
    ...)` records that message verbatim -- the same place verify.py's own
    `f"unreadable: {exc}"` puts its errno, just inside `params_invalid`'s
    message rather than a second top-level quarantine reason. A genuinely
    absent file is unaffected: `path.exists()` returning `False` -- pathlib's
    own `_IGNORED_ERRNOS` already swallows ENOENT/ENOTDIR/EBADF/ELOOP into
    that answer, confirmed directly on both 3.11 and 3.13 -- still returns
    `None` here, same as always. EACCES is not among those ignored errnos (on
    either version), so a permissions fault on the *file* still reaches
    `read_text()` and raises there even though `exists()` itself returns
    `True`; a permissions fault on a *directory* in the path reaches
    `exists()` directly. Both are covered by the one try below, and
    `tests/ingest/test_params.py` proves each with a real `os.chmod`, not a
    `Path` monkeypatch: pathlib was substantially restructured between 3.11
    and 3.13 (confirmed directly -- the ignored-errno table `sentinel.py`'s
    and `discover.py`'s own guards already depend on moved from a
    `pathlib`-module-level tuple to a new `pathlib._abc` submodule), so a
    patch aimed at the `Path` class is not guaranteed to sit on whichever
    internal code path either version actually executes. A real filesystem
    permission has no such gap.
    """
    path = layout.dir / PARAMS_FILENAME
    try:
        if not path.exists():
            return None
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        declared = SessionParams.model_validate(loaded)
    except (OSError, yaml.YAMLError, ValidationError, TypeError) as exc:
        raise ValueError(f"{PARAMS_FILENAME} is not valid: {exc}") from exc

    paramset.activate(prefix=prefix)
    return paramset.register(declared.paramset_type, declared.params)
