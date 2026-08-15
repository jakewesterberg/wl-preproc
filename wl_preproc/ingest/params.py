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
"Validating" includes a declared `paramset_type` that is too long for
`ParamSet.paramset_type : varchar(32)` -- a value `SessionParams` (an
unconstrained `str`) accepts cleanly and the database would not, the same
shape as `subject_unrepresentable` one layer up in Task 8's watcher -- and,
as of round 3 review, whether `params` itself is JSON-serializable at all: a
YAML scalar SafeLoader resolves implicitly to something `json.dumps` cannot
handle (a bare, unquoted `2027-03-14` becomes a real `datetime.date`, not a
`str`) validates against `SessionParams`'s deliberately unconstrained `dict`
just as cleanly as an oversized `paramset_type` validates against its
unconstrained `str`.

That guarantee covers the read-and-validate step only, not the whole
function. The registration call after it, `paramset.register`
(`wl_preproc/schema/paramset.py`), is not wrapped by it: that function's own
contention-exhaustion path -- a designed-for retry loop, not a hypothetical,
see its docstring and `_MAX_REGISTER_ATTEMPTS` -- raises a dedicated
`paramset.ContentionExhausted`, and this module neither catches nor converts
it. (Task 8's watcher used to catch this class by its bare DataJoint root,
`dj.DataJointError`; round 2 review narrowed that to `ContentionExhausted`
specifically, since the root catches DataJoint's entire error tree. See
`wl_preproc/ingest/watcher.py`'s `Outcome.DEFERRED` docstring.)
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
    At the time this fix was made, Task 8's watcher (`_scan_one`, since
    renamed `_evaluate_session`) caught only `ValueError` around the call to
    this function and had no outer boundary at all, so an uncaught OSError
    here would have escaped it entirely, escaped `scan_once`'s dict
    comprehension over every session directory in the root, and aborted the
    entire scan -- not just this one session. That is the identical
    unguarded-filesystem-call defect `sentinel.py` and `discover.py` were
    each fixed for earlier in this same phase.

    Round 2 review later added exactly such an outer boundary
    (`wl_preproc/ingest/watcher.py`'s `_scan_one`, wrapping the renamed
    `_evaluate_session`), so an uncaught OSError here would no longer abort
    the whole scan even without this fix -- it would quarantine this one
    session as the boundary's generic `unexpected_failure` instead. That
    downgrades the FAILURE MODE this fix originally closed, but does not
    remove the reason to keep it: `unexpected_failure` is a strictly worse
    diagnosis than `params_invalid` for a plain permissions fault or a
    transient NFS read error on a params file, exactly the same
    wrong-drawer misclassification the outer boundary itself exists to
    catch as a last resort, not to make into the ordinary path for
    something this specific and this already-named.

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
        # `params` is deliberately unconstrained (see SessionParams's own
        # docstring) -- pydantic never inspects its VALUES, only the
        # envelope's own two fields, so a value YAML's SafeLoader resolves
        # implicitly to something json.dumps cannot serialize validates
        # cleanly here. `calibrated_on: 2027-03-14` is the concrete case:
        # SafeLoader resolves that unquoted, ISO-8601-shaped scalar to a real
        # datetime.date, not a str -- confirmed directly, not assumed
        # (`yaml.safe_load("calibrated_on: 2027-03-14")` returns
        # `{"calibrated_on": datetime.date(2027, 3, 14)}`). Uncaught, this
        # died inside paramset.register's own content_hash with "Object of
        # type date is not JSON serializable" -- a plainly diagnosable
        # params-file defect landing in _scan_one's generic
        # unexpected_failure catch-all instead of this module's own
        # params_invalid, which already exists to name exactly this class of
        # problem.
        #
        # Proven here with the IDENTICAL call register() will make later
        # (`paramset.content_hash(declared.params)`), not a reimplementation
        # of json.dumps's own argument list that could quietly drift from
        # it -- the same reasoning deviation 4 (wl_preproc/ingest/watcher.py)
        # gives for going through landing.manifest_session_key instead of
        # rebuilding a session key inline. The return value is discarded;
        # only whether it raises matters here.
        paramset.content_hash(declared.params)
    except (OSError, yaml.YAMLError, ValidationError, TypeError) as exc:
        raise ValueError(f"{PARAMS_FILENAME} is not valid: {exc}") from exc

    # ParamSet.paramset_type : varchar(32) (wl_preproc/schema/paramset.py).
    # SessionParams.paramset_type is an unconstrained str, the identical shape
    # already handled for `subject` against element-animal's own column
    # (`landing.SUBJECT_MAX_LEN`, `reason="subject_unrepresentable"`): a value
    # that validates cleanly here can still be too long for the table it is
    # about to be inserted into. Checked here, inside the validation step,
    # rather than left for `paramset.register()`'s own insert to discover as a
    # raw pymysql.err.DataError -- which Task 8's watcher does not special-case
    # and would otherwise have to.
    if len(declared.paramset_type) > paramset.PARAMSET_TYPE_MAX_LEN:
        raise ValueError(
            f"{PARAMS_FILENAME}'s paramset_type {declared.paramset_type!r} is "
            f"{len(declared.paramset_type)} characters, over the "
            f"{paramset.PARAMSET_TYPE_MAX_LEN}-character column "
            "(ParamSet.paramset_type : varchar(32))"
        )

    paramset.activate(prefix=prefix)
    return paramset.register(declared.paramset_type, declared.params)
