# wl_preproc/daemon.py
"""The single job runner.

Section 11.3: one job runner holds the machine, with priority expressed inside
it. Two runners each concluding the machine is free is the failure refused for
VRAM allocation, and here it is tractable because both contenders are on one
host. The DataJoint populate daemon *is* that runner — the responder and the
ingest watcher only insert rows.

Long stages use the three-part make so the expensive phase runs outside the
transaction; see section 10's hazard table.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.schema import (
    DEFAULT_PREFIX,
    core,
    coverage,
    ephys,
    events,
    ingest,
    paramset,
    request,
    timebase,
)

# `reserved_time` is stamped once, at reservation, and never heartbeated while
# a stage runs -- so this is an upper bound on how long any single stage may
# legitimately run, not a staleness ESTIMATE (how long a crash typically
# takes to become obvious). See reap_stale_jobs's docstring for the failure
# mode a value that is too low reopens.
_DEFAULT_STALE_THRESHOLD_S = 24 * 60 * 60  # 24h


def _computed_tables() -> list:
    """The computed tables, in dependency order.

    The ordering is load-bearing rather than tidy. ``Segment.make()`` needs its
    system's rate to already exist and ``BlockCoverage.make()`` needs the
    segments — and neither dependency is expressed as a ``key_source``,
    deliberately: keying ``Segment`` off ``SystemTimebase`` would mean a system
    with no fit produced no rows at all, including the ``RejectedSegment`` rows
    that say why (see that property's own docstring). So this list IS the
    ordering, and nothing else enforces it.

    Empty in 1c-1, because nothing computed yet. 1c-4 fills it; Phase 2's
    sorting extends the same list rather than inventing its own traversal.
    """
    return [
        timebase.SystemTimebase,
        core.Segment,
        coverage.BlockCoverage,
        # Last: it counts segments and rejections, so it must run after
        # whatever produces them or it records a session as cleaner than it is.
        timebase.TimingProvenance,
    ]


# Every schema module this package defines, `pipeline` excepted — its tables
# are adopted Elements rather than `@schema`-decorated classes on the module,
# and this project does not populate them.
#
# This was a tuple of FOUR until 1c-4, and the omission was not theoretical.
# `timebase`'s two tables are the first Computed tables this project has
# declared, so the one schema that can actually own `~jobs` tables was the one
# schema never swept for stale reservations — `count_stale_jobs` would have
# returned a confident zero. `tests/schema/test_guardrails.py` records the same
# shape biting once before, when `ingest` landed as a fifth module.
#
# It became SEVEN with Phase 2a's `ephys` module, and this time the omission
# was caught by the test below rather than by a person noticing —
# `test_every_schema_module_is_swept_for_job_tables` failed the moment `ephys`
# existed and this tuple did not yet name it, exactly the guarantee the
# paragraph below already claimed for it. `ephys` declares every one of its
# tables `dj.Manual` or `dj.Lookup` (probe, insertion, clustering, unit,
# waveform, quality-metric, and continuous-product provenance — nothing
# Computed or Imported), so it owns no `~jobs` table of its own; it is listed
# here only so the completeness claim below stays true, not because
# `_computed_tables()` or `reap_stale_jobs` need a new stage for it.
#
# It stays a written list rather than a `pkgutil` sweep because the outbound
# guardrail bans `importlib` inside `wl_preproc/` (its ruling is recorded in
# `tests/test_cli_guardrails.py`: banning the import closes the whole
# dynamic-import class at a node type already visited). Static imports are also
# what make this auditable by reading. **The completeness claim is enforced by
# a test that DOES discover** — `test_every_schema_module_is_swept_for_job_tables`
# — so an eighth module fails the suite rather than being silently skipped.
#
# It became EIGHT with Phase 1c-5's `events` module, predicted by name in that
# phase's own design spec (section 4.2: "Assume it will be missed a fourth
# time.") and caught here exactly as predicted, by the test rather than by a
# person. `events` is a second case unlike `ephys`'s, though: it declares no
# `@schema` of its own at all — every table it fills is element-event's,
# already swept via `pipeline` (see that module's own exclusion, above) — so
# unlike every other entry here it has no `.schema` attribute for
# `_project_schemas()` to read. `_project_schemas()` returns `None` for it
# rather than raising, and its two callers below skip a `None` schema, so this
# tuple can still name `events` — satisfying the completeness claim — without
# inventing a `dj.Schema` this module would never activate.
_PROJECT_SCHEMA_MODULES: tuple[tuple[str, object], ...] = (
    ("core", core),
    ("coverage", coverage),
    ("ephys", ephys),
    ("events", events),
    ("ingest", ingest),
    ("paramset", paramset),
    ("request", request),
    ("timebase", timebase),
)


def _project_schema_modules() -> list[tuple[str, object]]:
    """`(name, module)` for every schema module. See `_PROJECT_SCHEMA_MODULES`."""
    return list(_PROJECT_SCHEMA_MODULES)


def _project_schemas() -> list[tuple[str, dj.Schema | None]]:
    """The `dj.Schema` of each, or `None` for a module that declares no table
    of its own.

    `events` is the one entry this applies to today: its own module docstring
    explains why it has no `@schema` (every table it fills is already an
    adopted Element's, activated via `pipeline`). `None` is returned rather
    than the module being left out of this list entirely, because
    `test_every_schema_module_is_swept_for_job_tables` compares NAMES here
    against every module `pkgutil` discovers under `wl_preproc/schema/` —
    and a module missing from this list, for any reason, is exactly the shape
    this project has been bitten by three times already (see this tuple's own
    comment above).
    """
    return [(name, getattr(module, "schema", None)) for name, module in _PROJECT_SCHEMA_MODULES]


def activate_all(prefix: str = DEFAULT_PREFIX) -> None:
    """Activate every schema module this package defines.

    Driven off `_PROJECT_SCHEMA_MODULES` rather than a second list of its own:
    `run_once` named three modules explicitly, and 1c-4 added two more whose
    tables every one of its populates depends on. That omission surfaced as
    every stage failing with an unactivated-schema error — loudly, but as three
    confusing failures rather than as one missing line.
    """
    for _name, module in _project_schema_modules():
        module.activate(prefix=prefix)


def job_tables() -> list:
    """Every ``~jobs`` table this project can currently see, as query objects.

    Exported so the daily report's write-detection snapshot can cover them.
    The 1c-2 handoff records that the snapshot did not, and until 1c-4 that
    was harmless because no Computed table existed to create one. **A snapshot
    that silently misses a table is worse than no snapshot, because it reads
    as coverage.**

    A `dj.jobs.Job` IS a table -- it subclasses `dj.Table` -- so these are
    returned directly rather than unwrapped. There is no `.table` attribute on
    one, which the first version of this function assumed.
    """
    return list(_activated_job_tables())


def _any_schema_activated() -> bool:
    """True if at least one of this project's schemas has been
    ``.activate()``d in this process.

    Distinct from "``_activated_job_tables()`` yielded something": that
    generator also yields nothing when a schema IS activated but happens to
    have no Computed/Imported table yet, which is true of *every* schema in
    this project today (see ``_computed_tables()``) — so it cannot tell
    "genuinely checked, found nothing to reap" apart from "there was nothing
    activated to check in the first place". ``count_stale_jobs`` needs that
    distinction to avoid reporting the second case as if it were the first.

    `schema_obj` is `None` for a module that declares no schema of its own
    (`events` today — see `_project_schemas()`); such an entry contributes
    nothing to activation state either way, so it is skipped rather than
    read.
    """
    return any(
        schema_obj is not None and schema_obj.is_activated()
        for _name, schema_obj in _project_schemas()
    )


def _activated_job_tables():
    """Every ``Job`` this project can currently see: one per Computed/Imported
    table, across the schemas that are actually live in this process.

    ``dj.Schema.jobs`` raises ``DataJointError`` outright — not "returns
    nothing" — on a schema that has not been ``.activate()``d, so each schema
    is checked with ``is_activated()`` and skipped, not queried, if it is not
    yet live. This matters concretely: ``run_once`` activates every schema
    before reaping, but a caller may invoke ``reap_stale_jobs`` or
    ``count_stale_jobs`` directly against a process that has only activated
    some of them (exactly what ``tests/schema/test_daemon.py``'s
    ``daemon_env`` fixture does — it activates ``core`` and ``request`` but
    not ``coverage``/``paramset``).

    `schema_obj` is `None` for a module that declares no schema of its own
    (`events` today — see `_project_schemas()`); it owns no `~jobs` table
    either way, so it is skipped exactly like an unactivated one.
    """
    for _name, schema_obj in _project_schemas():
        if schema_obj is None or not schema_obj.is_activated():
            continue
        yield from schema_obj.jobs


def _stale_reserved_keys(job, older_than_s: int) -> list[dict]:
    """Primary keys of `job`'s reservations at least `older_than_s` seconds
    old — the shared "what counts as stale" condition behind both
    `reap_stale_jobs` (which acts on it) and `count_stale_jobs` (which only
    counts it), so the fix below lives in exactly one place.

    This project's first draft reached for `schema_obj.jobs` restricted by a
    raw `TIMESTAMPDIFF` SQL string, unverified against DataJoint 2.3.2, and
    separately considered delegating to `Job.refresh(orphan_timeout=...)`,
    which reads like the sanctioned tool for exactly this hazard: "Reserved
    jobs older than this are considered orphaned... deleted and re-added as
    pending" (datajoint/jobs.py). Neither survived contact with the real API
    — confirmed by reading datajoint/schemas.py and datajoint/jobs.py, and by
    exercising a genuinely stuck reservation against a live MySQL container
    (this version pins DataJoint 2.3.2):

    * `schema_obj.jobs` is not a single restrictable query. It is a
      `list[Job]`, one entry per Computed/Imported table that already has a
      *declared* `~~table_name` job table (created lazily on first
      `populate(reserve_jobs=True)` or first `table.jobs` access). Every
      table in this project's own schemas is `dj.Manual` — see
      `_computed_tables()` above — so today this list is always empty and
      there is genuinely nothing to reap yet; restricting a bare `list` with
      `&` raises `TypeError` the first time a Computed table exists anywhere
      (reproduced directly: `TypeError: unsupported operand type(s) for &:
      'list' and 'str'`).
    * `Job.refresh()`'s orphan-reaping gate is `if orphan_timeout is not None
      and orphan_timeout > 0:` — so `orphan_timeout=0` silently no-ops
      instead of meaning "no grace period, reap anything reserved". This
      module's own reaper test calls `reap_stale_jobs` with exactly
      `older_than_s=0`, so a passthrough to `refresh()` would report
      `freed=0` while doing nothing, for the wrong reason.
    * `Job.refresh()`'s own comparison is `reserved_time < CURRENT_TIMESTAMP
      - {interval}` using bare `CURRENT_TIMESTAMP` (second precision),
      against `reserved_time`, a `datetime(3)` column written via
      `CURRENT_TIMESTAMP(3)` inside `Job.reserve()`. Checked within the same
      wall-clock second as the reservation — plausible for any `older_than_s`
      small enough to matter in a test, and not otherwise unlikely — the
      millisecond remainder can sort a just-reserved row *after* the
      truncated comparison timestamp, so the row is missed even though it
      is, correctly measured, already in the past.

    So this matches `reserved_time`'s own precision (`CURRENT_TIMESTAMP(3)`,
    the same function `Job.reserve()` uses to write it) and is applied
    directly, unconditionally on `older_than_s`, rather than through
    `refresh()`'s gate. This also means "add new pending jobs from
    key_source" — `refresh()`'s unrelated first step, always run whether or
    not `orphan_timeout` is set — never happens as a side effect of what is
    meant to be a narrowly-scoped reaper.
    """
    interval = job.adapter.interval_expr(max(int(older_than_s), 0), "second")
    stale = job.reserved & f"reserved_time < CURRENT_TIMESTAMP(3) - {interval}"
    return stale.keys()


def reap_stale_jobs(
    prefix: str = DEFAULT_PREFIX, older_than_s: int = _DEFAULT_STALE_THRESHOLD_S
) -> int:
    """Clear job reservations left behind by a crashed populate.

    A crashed populate leaves its key marked reserved in a table's `~~` job
    queue, and the key is skipped forever after — silently, which is what
    makes it section 10's top-four hazard rather than an annoyance. See
    `_stale_reserved_keys` for what actually decides "stale", and why it does
    not delegate to `dj.Schema.jobs` restricted directly or to
    `Job.refresh(orphan_timeout=...)` — both were tried first, and neither
    survived contact with the real API.

    `older_than_s` is an upper bound on how long any single stage may
    legitimately run, not an estimate of how long a crash takes to become
    obvious (`reserved_time` is stamped once, at reservation, and never
    heartbeated while a stage runs — nothing in this table distinguishes "a
    4h sort still running" from "a 4h sort whose process died at minute 3").
    Set it too low and a live, legitimately-running stage gets its
    reservation stolen out from under it: a second `wlpp daemon` invocation —
    a cron every 30 minutes is the obvious deployment, and nothing here
    enforces the single-runner invariant the module docstring assumes (no
    lock file, no advisory lock) — would free the first run's reservation and
    start the same stage concurrently, which is precisely the two-runners-
    think-the-machine-is-free failure section 11.3 refuses. The default is
    24h: not a measured bound, just a rounder, more conservative one than the
    1h this shipped with first, for a pipeline whose only named long stage
    (section 10's worked example) is a 4h sort. `_computed_tables()` is empty
    today, so this cannot fire yet; it arms the moment a future task adds a
    stage there, and whoever adds one that can legitimately run longer than
    this default should raise it again, deliberately, rather than inherit a
    number sized for a codebase with nothing to compute yet.

    `prefix` is accepted for interface symmetry with `run_once` and
    `count_stale_jobs` but not read: every schema this function inspects
    (`core.schema`, `coverage.schema`, ...) is a process-wide singleton
    already bound to whichever prefix `activate()` was called with, and this
    project runs one prefix per process — there is no second prefix's tables
    for this parameter to select between.
    """
    # The same default `refresh()` itself falls back to for a freshly-re-pended
    # job (datajoint/jobs.py: `priority = self.connection._config.jobs.
    # default_priority`). `dj.config` is that same object for every connection
    # this project ever opens — always via `dj.conn()`, never with a
    # per-connection override — so reading it here is equivalent and does not
    # need the underscore-prefixed attribute.
    default_priority = dj.config.jobs.default_priority

    freed = 0
    for job in _activated_job_tables():
        keys = _stale_reserved_keys(job, older_than_s)
        for key in keys:
            (job & key).delete_quick()
            job.insert1({**key, "status": "pending", "priority": default_priority})
        freed += len(keys)
    return freed


def count_stale_jobs(
    prefix: str = DEFAULT_PREFIX, older_than_s: int = _DEFAULT_STALE_THRESHOLD_S
) -> int | None:
    """Count the reservations `reap_stale_jobs` would free, without freeing
    them.

    `wlpp doctor` uses this instead of `reap_stale_jobs` itself: a
    diagnostic must not mutate the thing it inspects — the shipped version of
    that check called the reaper directly, which meant the moment any caller
    activated a schema in-process, running `wlpp doctor` silently deleted and
    re-pended job rows.

    Returns `None`, not `0`, when none of this project's four schemas have
    been `.activate()`d in this process — which is every bare `wlpp doctor`
    invocation today, since `doctor.py` never activates one itself — so that
    "nothing was actually inspected" cannot be reported as a fabricated
    all-clear. A caller that gets `None` back has learned nothing about
    whether any job is actually stuck; only an `int` is a real answer.

    See `reap_stale_jobs`'s docstring for what `older_than_s` means (an
    upper bound on legitimate stage runtime, not a staleness estimate) and
    why `prefix` is accepted but not read.
    """
    if not _any_schema_activated():
        return None
    return sum(len(_stale_reserved_keys(job, older_than_s)) for job in _activated_job_tables())


def run_once(prefix: str = DEFAULT_PREFIX) -> dict:
    """One pass of the runner. Returns what it did, for the daily report.

    `populated` counts KEYS computed, not tables attempted, and `errors`
    carries the per-key failures `suppress_errors=True` collects rather than
    raises. Both were wrong until 2026-08-14, and wrong in the direction that
    cannot be noticed: `populate()` returns `{"success_count", "error_list"}`
    and the return value was discarded, so `errors` only ever caught
    exceptions that escaped -- which is precisely what `suppress_errors=True`
    exists to prevent -- and a stage failing on every one of its keys printed
    `errors: none`. 1c-2's daily report is specified to be built on this dict,
    so a silent all-clear here becomes a silent all-clear there.

    `suppress_errors=True` is kept, not dropped: one stage failing must not
    stop the others, which is the same reason the `try` below survives. The
    `try` now catches only what `suppress_errors` genuinely cannot -- a
    failure in `populate()` itself rather than in one of its keys (a dropped
    connection, a key_source that will not resolve).
    """
    activate_all(prefix=prefix)

    reaped = reap_stale_jobs(prefix=prefix)
    populated, errors = 0, []
    for table in _computed_tables():
        try:
            result = table.populate(reserve_jobs=True, suppress_errors=True)
            populated += int(result["success_count"])
            # error_list entries are (key, error) -- datajoint/autopopulate.py.
            errors.extend(f"{table.__name__} {key}: {err}" for key, err in result["error_list"])
        except Exception as exc:  # a failing stage must not stop the others
            errors.append(f"{table.__name__}: {exc}")

    return {"populated": populated, "errors": errors, "stale_jobs_reaped": reaped}
