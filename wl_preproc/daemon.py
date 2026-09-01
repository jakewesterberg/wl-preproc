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

from pathlib import Path

import datajoint as dj

# Imported directly, by name, rather than reached through a composed helper:
# this is what makes `wl_preproc.daemon.archive_session` a real, patchable
# module attribute. `tests/ingest/test_archive_trigger.py` patches exactly
# that name (Controller ruling A's own "consequence for the tests") so its
# mocked archival stage never actually spends the hour `archive_session`
# costs -- `unittest.mock.patch` resolves a bare name inside a function body
# against the ENCLOSING MODULE's globals at call time, which only works if
# this module itself holds the name, not if `_archive_stage` reached it via
# `wl_preproc.archive.stage.archive_session` on every call. See
# `_archive_stage` below for how the two archival helpers are then combined.
from wl_preproc.archive.stage import archive_session, nas_root_for_subject, record_archive_outcome
from wl_preproc.schema import (
    DEFAULT_PREFIX,
    archive,
    core,
    coverage,
    detect,
    ephys,
    events,
    eye,
    ingest,
    paramset,
    # Imported, but deliberately NOT one of `_PROJECT_SCHEMA_MODULES` below --
    # that tuple's own comment says why `pipeline` is excepted from it.
    # `_event_stage_keys()` needs `pipeline.Session` (that stage's key source,
    # via `ingest.Ingestion`) and `pipeline.event.BehaviorRecording` (its
    # done-marker). Bound as the MODULE rather than by name, because
    # `pipeline.activate()` rebinds `pipeline.Session` (see its own
    # `global Session`) and a `from ... import Session` here would freeze the
    # pre-activation object.
    pipeline,
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

    **Not every daemon stage is in here, and one is not a table at all.**
    `run_once` runs `_populate_event_stage()` before this loop -- a plain
    function over sessions, not a `dj.Computed` -- and `TrialCoverage`'s
    `key_source` is what that stage fills. See `_populate_event_stage`.

    **Completeness is enforced by a test that discovers, not by review.**
    `tests/schema/test_daemon.py::test_every_computed_table_is_a_daemon_stage`
    fails if any `dj.Computed` declared in one of `_PROJECT_SCHEMA_MODULES`'
    modules is absent from this list and is not named in
    `_COMPUTED_TABLES_EXEMPT` below. `TrialCoverage` was missing here for the
    whole of 1c-5 -- the same shape `_PROJECT_SCHEMA_MODULES`' own comment
    below records going stale four times over, and `activate_all`'s docstring
    once more -- and the cost was not a dormant feature: with `trial.Trial`
    never filled in production, `TimingProvenance.make()` counted zero trials
    from the codes against a non-zero task file, so `trial_count_agreement`
    was False and `events.agreement.resolve_tier` returned D at its
    `trial_count_agreement is False` guard for every session.

    **`eye.EyeCalibration`/`eye.EyeQuality` were registered inert and are not
    any more.** The 2026-08-30 eye-ohdpi-calibration-and-gaze design's own
    Task 9 shipped both as schema only -- neither table defined `make()`, and
    each overrode `key_source` to stay permanently empty rather than fall
    back to DataJoint's default (the join of FK parents, here bare
    `pipeline.Session`), because leaving that default would have broken this
    very loop the moment these tables joined it -- `dj.AutoPopulate`'s own
    base `make()` raises `NotImplementedError` unconditionally, and every
    session already landed would have hit it. That reasoning is preserved in
    `wl_preproc/schema/eye.py`'s own git history (each class's former
    `key_source`), not restated in the current one, which describes the real
    restriction instead. Task 10 replaced both `key_source`s with that real
    restriction design spec section 6 names and gave both a real `make()`;
    they compute for real now. Their POSITION here is still not load-bearing
    the way the rest of this list's ordering is (nothing downstream of them
    reads their output within this same `run_once` pass), but the restriction
    itself needs an ohDPI recording and assembled events, both already
    satisfied by this point in a pass -- so placed after `TrialCoverage` and
    before `TimingProvenance` is still the right position, now for the
    reason it always should have been rather than merely because nothing yet
    depended on it.

    **`detect.EyeValidity`/`detect.EyeDetection` were registered inert and
    are not any more, and unlike the eye tables above their POSITION here IS
    load-bearing.** The 2026-08-31 saccade-detection design's own Task 6
    shipped both as schema only -- `make()` was Task 7, and each overrode
    `key_source` to stay permanently empty for the exact reason recorded
    above and in `wl_preproc/schema/eye.py`'s own git history (commit
    3a8a121): a `dj.Computed` with a real `key_source` and no `make()`
    raises `NotImplementedError` on every session already landed the moment
    it joins this list. Commit `314d82b` gave both a real `key_source` and a
    real `make()`. This paragraph, the comment beside the two entries below,
    and `_PROJECT_SCHEMA_MODULES`' own comment all went on saying the
    opposite until 2026-09-01 (whole-branch review, finding M9); the `eye`
    paragraph above WAS updated when its own tables landed, so the pattern
    was known and this one was simply missed.

    **What their position now buys, precisely.**
    `detect.EyeValidity.key_source` requires `eye.EyeCalibration` to have RUN
    (whole-branch review, finding M5 -- see that property's own docstring),
    and `detect.EyeDetection.key_source` is built from `EyeValidity`'s own
    rows. So both must stay BELOW `eye.EyeCalibration`, and in this order.
    The M5 restriction is what decides how badly a reordering hurts, and it
    is worth naming both states: WITHOUT it, `EyeValidity` placed above
    `eye.EyeCalibration` refused both eyes on the first pass with "no usable
    calibration, so gaze is undefined" and never recomputed -- a PERMANENT
    row that the very next pass makes false, with no error reported. WITH
    it, the same reordering instead costs one whole `run_once` pass per
    session, silently, because the `key_source` names no candidate until
    calibration has landed. The restriction is the guard against the first;
    this ordering is what avoids the second.
    """
    return [
        timebase.SystemTimebase,
        core.Segment,
        coverage.BlockCoverage,
        # After `Segment` for exactly `BlockCoverage`'s reason -- it intersects
        # a trial's interval with this system's segment extents -- and after
        # `_populate_event_stage()`, which is what puts rows in the
        # `pipeline.trial.Trial` half of its `key_source`. `run_once` runs that
        # stage before this whole list, so the second half is satisfied by
        # position rather than needing an entry here.
        coverage.TrialCoverage,
        # Real as of Task 10 -- see this function's own docstring above for
        # why their position here still is not what makes them correct.
        eye.EyeCalibration,
        eye.EyeQuality,
        # Real as of `314d82b`, and BELOW `eye.EyeCalibration` on purpose:
        # `EyeValidity.key_source` requires that table to have run, and
        # `EyeDetection.key_source` is built from `EyeValidity`'s own rows.
        # See this function's own docstring for what moving either of these
        # two up actually costs -- it is not the same before and after the
        # M5 restriction, and both answers are recorded there.
        detect.EyeValidity,
        detect.EyeDetection,
        # Last: it counts segments and rejections, so it must run after
        # whatever produces them or it records a session as cleaner than it is.
        timebase.TimingProvenance,
    ]


# `dj.Computed` tables deliberately kept OUT of `_computed_tables()`, as
# `{module}.{ClassName}` against `_PROJECT_SCHEMA_MODULES`' own module names.
#
# Empty, and that is the finished state rather than a placeholder: every
# `dj.Computed` this project declares today is a daemon stage. The constant
# exists so that a future one which genuinely should not be traversed has to
# SAY so here -- `test_every_computed_table_is_a_daemon_stage` reads absence
# from `_computed_tables()` as a defect, never as an intention, which is
# precisely the inference that let `TrialCoverage` sit unregistered for a
# whole phase. A name listed here that is not actually a discovered
# `dj.Computed` fails that test too, so an exemption cannot outlive the table
# it was written for.
_COMPUTED_TABLES_EXEMPT: frozenset[str] = frozenset()


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
#
# It became NINE with the 2026-08-27 archival-and-compression design's
# `archive` module, caught by the same test again rather than by a person.
# `archive` is a third case, like `ephys`: all four of its tables —
# `ArchiveArtifact`, `ArchiveVerification`, `ReclamationHold`,
# `ScratchReclamation` — are `dj.Manual`, nothing Computed or Imported, so it
# owns no `~jobs` table of its own either; it is listed here only so the
# completeness claim above stays true.
#
# It became TEN with the 2026-08-30 eye-ohdpi-calibration-and-gaze design's
# `eye` module, caught by the same test a fourth time. Unlike `ephys` and
# `archive`, `eye` DOES declare two `@schema`-decorated `dj.Computed` tables
# of its own (`EyeCalibration`, `EyeQuality`) — so it owns real `~jobs`
# tables, like `timebase`. Task 9 shipped both schema-only, with a
# permanently empty `key_source` — `_computed_tables()`'s own docstring
# named exactly what kept that safe to register at the time — and Task 10
# gave both a real `key_source` and `make()`; they populate for real now.
#
# It became ELEVEN with the 2026-08-31 saccade-detection design's `detect`
# module. A fourth case shaped like `eye`, not like `ephys`/`archive`/
# `events`: `detect` declares two real `@schema`-decorated `dj.Computed`
# tables (`EyeValidity`, `EyeDetection`), so it owns real `~jobs` tables too.
# Task 6 shipped both schema-only, with a permanently empty `key_source`, for
# the identical reason recorded above for Task 9's `eye` tables; commit
# `314d82b` gave both a real `key_source` and `make()`, and they populate for
# real now. `detect` is also the one module named in `_PARAMSET_MODULES`
# below -- its two tables are the first `dj.Computed` in this project whose
# `key_source` needs a `paramset.ParamSet` row to exist before it names any
# candidate at all.
_PROJECT_SCHEMA_MODULES: tuple[tuple[str, object], ...] = (
    ("archive", archive),
    ("core", core),
    ("coverage", coverage),
    ("detect", detect),
    ("ephys", ephys),
    ("events", events),
    ("eye", eye),
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


# Every schema module that declares a `register_default_paramsets` of its
# own, as `(name, module)` -- the same written-list-plus-discovering-test
# shape `_PROJECT_SCHEMA_MODULES` above uses, and for the same reason: a
# `pkgutil`/`getattr` sweep inside `wl_preproc/` would be the dynamic import
# the outbound guardrail bans, and a written list is what makes this
# auditable by reading. One entry today.
#
# `test_every_schema_module_that_declares_default_paramsets_is_registered`
# is the completeness claim: it DISCOVERS which modules declare such a
# function and fails if one is missing here. `detect`'s own omission -- from
# production entirely, not merely from a list -- is finding H1, and this
# tuple exists so the second such module cannot repeat it silently.
_PARAMSET_MODULES: tuple[tuple[str, object], ...] = (("detect", detect),)


def register_default_paramsets() -> dict[str, dict[str, int]]:
    """Every default paramset a `dj.Computed` stage's own `key_source`
    requires to already exist, registered before that stage is populated.
    Returns `{schema module name: that module's own return value}`.

    **This is its own step in `run_once`, deliberately not folded into
    `activate_all`.** Activation binds table declarations to a database and
    nothing else; this WRITES ROWS. Several callers activate a schema with
    no intention of populating anything -- `cli/report.py::_detection_rows`,
    `cli/main.py`'s `archive` dispatch, `ingest/watcher.py` -- and
    `count_stale_jobs`'s own docstring already records what that conflation
    costs when it goes the other way ("a diagnostic must not mutate the
    thing it inspects": `wlpp doctor` silently deleted and re-pended job
    rows). Registration behind the activation guard would make `wlpp report`
    write paramset rows as a side effect of reading, which is the same
    mistake pointed the other direction. So `activate_all` keeps its name
    honest and `run_once` calls this second.

    **Nothing in production called `detect.register_default_paramsets` at
    all until this function existed, and the whole detection subsystem was
    silently inert as a result** (whole-branch review, finding H1).
    `EyeValidity.key_source` and `EyeDetection.key_source` each join against
    `paramset.ParamSet & {"paramset_type": ...}`, so with no row registered
    both name ZERO candidate keys: `run_once` reports no error, writes no
    row, and the daily report's `## Detection` section renders `- none` and
    `blink: 0.0% / invalid: 0.0%` forever -- presented as measurements
    rather than as "not configured". Its only callers were two test
    fixtures, which is precisely why the gap was invisible; the end-to-end
    proof that production now closes it is
    `tests/schema/test_daemon.py::
    test_a_real_wlpp_daemon_invocation_registers_the_detection_paramsets`,
    which runs the real CLI against a virgin prefix in a subprocess rather
    than registering anything itself.

    **`ephys`'s precedent does not cover this.** Its three ParamSet-keyed
    tables are all `dj.Manual`, so "no rows" there honestly means "nothing
    declared yet" and a human declares it. `detect` is this project's first
    `dj.Computed` in `_computed_tables()` gated on a `ParamSet` row
    existing, so it is the first stage that can be gated shut by an absent
    row without anyone being told.

    **Idempotent, and that is a property of `paramset.register` rather than
    a claim made here**: it is keyed by content hash and returns the
    existing index for identical params (`paramset.py::register`'s own
    "reusing an identical one"), so the obvious deployment -- a cron every
    30 minutes -- re-registers nothing. It must also run OUTSIDE any
    transaction, which `run_once` satisfies: see `paramset.register`'s own
    STANDING CONSTRAINT comment for why the dedupe re-read cannot see a
    rival's commit from inside one.

    **The written list is the audit, and a discovering test is the
    completeness claim** -- the same shape `_computed_tables()` and
    `_PROJECT_SCHEMA_MODULES` already use. See `_PARAMSET_MODULES` above.

    Must run AFTER `activate_all`: `paramset.register` writes to
    `paramset.ParamSet`, which is not bound to a database until then.
    """
    return {name: module.register_default_paramsets() for name, module in _PARAMSET_MODULES}


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
    generator can still yield nothing from a schema that IS activated, and the
    reason has changed since this was written. It used to be that no schema
    here had a Computed table at all; five of them do now (see
    ``_computed_tables()``). What survives is narrower and still sufficient:
    ``dj.Schema.jobs`` yields a ``Job`` only for a Computed/Imported table
    whose ``~~`` job table *already exists in the database* (read directly
    from datajoint/schemas.py), and that table is created lazily — on the
    first ``populate(reserve_jobs=True)`` or the first ``table.jobs`` access.
    So a freshly-activated process that has not populated yet yields nothing,
    as does any schema declaring no Computed/Imported table at all -- still
    most of them, and deliberately not name-listed here: which schemas those
    are is what ``test_every_computed_table_is_a_daemon_stage`` discovers.
    Either way this generator cannot
    tell "genuinely checked, found nothing to reap" apart from "there was
    nothing activated to check in the first place". ``count_stale_jobs`` needs
    that distinction to avoid reporting the second case as if it were the
    first.

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
      `populate(reserve_jobs=True)` or first `table.jobs` access). When this
      was written no table in this project's own schemas was Computed, so
      that list was always empty and the hazard was theoretical; since 1c-4
      there are five (see `_computed_tables()` above) and `run_once` reserves
      jobs for every one, so it is non-empty in any process that has
      populated. Restricting a bare `list` with `&` raises `TypeError`
      (reproduced directly: `TypeError: unsupported operand type(s) for &:
      'list' and 'str'`), which is now reachable rather than hypothetical.
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
    (section 10's worked example) is a 4h sort. This used to say the reaper
    could not fire at all because `_computed_tables()` was empty; that has
    been false since 1c-4, and 1c-5 is what makes it exercised -- five
    computed stages, every one populated with `reserve_jobs=True`. Whoever
    adds a stage that can legitimately run longer than this default should
    raise it, deliberately, rather than inherit a number sized for a codebase
    with nothing to compute yet.

    **Scope, checked against DataJoint 2.3.2's real job lifecycle rather than
    assumed.** This frees `status='reserved'` rows and nothing else, and that
    is the correct scope: `_populate1` catches a failing `make()` and calls
    `jobs.error(...)`, so a key whose `make()` raised under
    `suppress_errors=True` ends at `status='error'`, never left reserved.
    Only an interrupted process -- one that reserved and then died before
    `complete()` or `error()` -- leaves a reservation behind, which is
    exactly what this function's first paragraph describes. An errored key is
    a different state with a different remedy and is deliberately not touched
    here. Measured consequence, since `run_once` passes
    `suppress_errors=True`: an errored key is NOT retried on the next pass --
    `_populate_distributed` draws only from `jobs.pending`, and
    `Job.refresh()` re-pends completed jobs but not errored ones -- so a
    transient failure parks that key until someone clears it by hand. Three
    consecutive passes over a probe stage that raises produced 2 errors then
    0 then 0, with `make()` called only on the first. That is a real gap and
    it is not this function's to close; naming it here is the point.

    `_populate_event_stage` leaves nothing for this function to find, and
    needs nothing: it never calls `.populate()`, so it reserves no job at
    all, and its whole call runs inside one transaction whose rollback takes
    the `BehaviorRecording` done-marker with it. A crash there re-attempts the
    session on the next pass rather than stranding it.

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


def _event_stage_keys() -> list[dict]:
    """Landed sessions whose canonical trial list has not been built yet.

    `pipeline.Session & ingest.Ingestion` is `TimingProvenance.key_source`
    verbatim, for its stated reason: `Ingestion` carries `session_dir`, the
    only record of where a session's files are, so a session with no row there
    cannot be read at all and is simply not yet due.

    `- pipeline.event.BehaviorRecording` is the done-marker, and it is needed
    because `populate_session` is a plain function rather than a
    `dj.Computed` -- DataJoint's own `key_source - target` bookkeeping does not
    apply to it, and nothing else would stop the daemon re-decoding every
    session it has ever landed on every pass (a cron every 30 minutes is the
    obvious deployment; see `reap_stale_jobs`). `BehaviorRecording` is the
    right target because `populate_session` writes exactly one row of it per
    session it processes, keyed on the session's own primary key
    (`schema/events.py`'s own "one per session (its primary key IS the
    session's)").

    That marker is only sound because `_populate_event_stage` runs the call
    inside a transaction -- `BehaviorRecording` is the FIRST thing
    `populate_session` inserts, so a half-finished session would otherwise be
    marked done with no trials in it. See that function.
    """
    return ((pipeline.Session & ingest.Ingestion) - pipeline.event.BehaviorRecording).keys()


def _populate_event_stage() -> tuple[int, list[str]]:
    """Build the canonical trial list for each session still missing one.

    Returns `(sessions built, per-session failures)` -- the same two quantities
    one iteration of `run_once`'s `_computed_tables()` loop contributes, so the
    caller accounts for both identically.

    **A failing session is caught here rather than allowed to escape, because
    `suppress_errors=True` cannot reach a plain function call.** That flag is
    what keeps one bad key from stopping a `.populate()`; `populate_session` is
    not a `.populate()`, so the equivalent has to be written out. The `except`
    below is therefore the analogue of `populate()`'s own `error_list` -- one
    entry per failing SESSION, formatted like the per-key entries the loop
    already produces -- and not of `run_once`'s outer `try`, which exists for
    the different failure `suppress_errors` genuinely cannot cover.

    **The call runs inside one transaction**, so the session's rows land
    entirely or not at all. Two reasons, both load-bearing: it is what makes
    `BehaviorRecording` a sound done-marker for `_event_stage_keys()` (that
    row is written first, before any `Trial`), and it matches what DataJoint
    already does around every `make()` in `_computed_tables()`. The decode
    happens inside the transaction as a result, which the module docstring's
    three-part-make rule would otherwise argue against -- but
    `TimingProvenance.make()` is a plain `make()` that re-decodes the same
    session's syncbox AND nidq streams inside DataJoint's own transaction
    already (`tests/schema/test_daemon.py::
    test_a_plain_make_runs_entirely_inside_the_transaction` pins that this is
    what a plain `make` does), so this is the cost this pipeline already pays
    at this stage's scale, not a new one.
    """
    built, errors = 0, []
    for key in _event_stage_keys():
        session_dir = Path((ingest.Ingestion & key).fetch1("session_dir"))
        try:
            with dj.conn().transaction:
                events.populate_session(key, session_dir)
        except Exception as exc:  # one bad session must not take down the run
            errors.append(f"populate_session {key}: {exc}")
        else:
            built += 1
    return built, errors


def _archive_stage_keys() -> list[dict]:
    """Verified sessions with no archive on record yet -- `_event_stage_
    keys()`'s own key_source-minus-done-marker shape, applied to a different
    done marker. See `_archive_stage` below for what actually runs against
    each key this returns.

    **Restricted to `integrity = "verified"` exactly, never `!= "skipped"`.**
    Controller ruling B: `Ingestion.integrity` is a THREE-valued enum
    (`schema/ingest.py`: `enum('verified','declared_only','skipped')`), and a
    negative check would silently admit `declared_only` -- some system's DONE
    marker was empty, so nothing about that system was ever actually checked
    (`ingest/verify.py`'s own "a session is verified only if everything in it
    was") -- alongside a genuine pass. Equality names exactly the one state
    the 2026-08-27 archival-and-compression design's own §3.1 means by
    "verification passes", and excludes the other two states by construction
    rather than by remembering to enumerate them. `archive/layout.py`'s own
    docstring used to name this restriction as unfinished business -- "not
    here, and not yet in the archival trigger, which is where that decision
    belongs" -- and this function is that resolution, not a second instance
    of the gap; that passage was corrected 2026-08-27 (Task 10 whole-branch
    review) to cite this restriction rather than describe its absence, with
    the one real asymmetry that survives named there precisely: this covers
    only the daemon's own automatic pass, and `wlpp archive` run by hand
    still does not consult `integrity` at all.

    **`- archive.ArchiveArtifact` is the done-marker**, mirroring
    `_event_stage_keys()`'s own `- pipeline.event.BehaviorRecording`: a
    session archived successfully is not archived again on the next pass.
    `record_archive_outcome` writes this row only when `outcome.all_matched`
    (see its own docstring), so a session whose most recent attempt FAILED
    verification has no such row and stays in this key source -- retried on
    the next pass, exactly like a raising `populate_session` call is retried
    by `_populate_event_stage`.

    Parenthesized exactly like `_event_stage_keys()`'s `(pipeline.Session &
    ingest.Ingestion) - pipeline.event.BehaviorRecording`: Python's `-`
    (`__sub__`) binds TIGHTER than `&` (`__and__`), so an unparenthesized
    `ingest.Ingestion & 'integrity = "verified"' - archive.ArchiveArtifact`
    would try to subtract a table from a STRING, not restrict-then-subtract.
    """
    return (
        (ingest.Ingestion & 'integrity = "verified"') - archive.ArchiveArtifact
    ).keys()


def _archive_stage(
    nas_root: Path, host: str, share: str, prefix: str = DEFAULT_PREFIX
) -> tuple[int, list[str]]:
    """Archive every verified, not-yet-archived session. Returns `(sessions
    archived, per-session failures)` -- the same two quantities
    `_populate_event_stage` returns, and for the identical reason: `run_once`
    accounts for every stage's failures the same way.

    **Controller ruling A: a bespoke stage here, like `_populate_event_
    stage`, not a `.populate()` call.** `archive`'s four tables are all
    `dj.Manual` (`schema/archive.py`'s own module docstring), so there is no
    `~jobs` table for this to reserve against, and
    `test_every_computed_table_is_a_daemon_stage` does not apply to it.

    **Every exception from one session's archival is caught here and turned
    into an error string, not left to propagate.** This is precisely what
    ruling A's own reasoning rules out doing inside `ingest/watcher.py`'s
    `scan_once` instead: hooking archival into the scan would put it under
    `_scan_one`'s exception boundary, which turns any unhandled exception
    into `Outcome.QUARANTINED` with reason `unexpected_failure` -- correct
    for a session-caused defect, wrong for a full NAS or a transient IO fault
    during compression, which would then quarantine an already-ingested,
    perfectly good session and blame its own files for it. Running here
    instead means this stage has NO such boundary of its own, so nothing
    else would stop one session's archival fault from crashing the rest of
    this `run_once` pass -- including every stage still to run after this
    one -- if it were not caught explicitly.

    A verification failure (`outcome.all_matched` is False) is reported the
    same way, as an error string, but is NOT the exception case above: it is
    `archive_session`'s own ordinary, non-raising outcome for genuinely bad
    bytes, and `record_archive_outcome` already leaves no `ArchiveArtifact`
    row for it -- which is what keeps the session in `_archive_stage_keys()`
    for the next pass, rather than this function needing its own retry
    bookkeeping.
    """
    archived, errors = 0, []
    for key in _archive_stage_keys():
        session_dir = Path((ingest.Ingestion & key).fetch1("session_dir"))
        try:
            outcome = archive_session(
                session_dir, nas_root_for_subject(nas_root, key), key, prefix=prefix
            )
            record_archive_outcome(key, outcome, nas_root, host, share, prefix=prefix)
        except Exception as exc:  # one bad session must not take down the run
            errors.append(f"archive_session {key}: {exc}")
            continue
        if outcome.all_matched:
            archived += 1
        else:
            n_mismatched = sum(1 for v in outcome.verdicts if not v.matched)
            errors.append(
                f"archive_session {key}: verification failed "
                f"({n_mismatched} of {len(outcome.verdicts)} files mismatched)"
            )
    return archived, errors


def run_once(
    prefix: str = DEFAULT_PREFIX,
    nas_root: Path | None = None,
    host: str | None = None,
    share: str | None = None,
) -> dict:
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

    **`_populate_event_stage()` runs FIRST, before the `_computed_tables()`
    loop.** It is not in that list because it is not a `dj.Computed` at all --
    `wl_preproc.schema.events.populate_session` is a function over
    `(key, session_dir)` -- and it goes first because it depends on no computed
    table (only on `ingest.Ingestion` for the directory; see
    `schema/events.py`'s own argument that the sync box alone suffices), while
    two later stages depend on it: `coverage.TrialCoverage.key_source` reads
    `pipeline.trial.Trial`, and `TimingProvenance.make()` counts that same
    table for `trial_count_agreement`. Until 1c-5's review this stage did not
    exist, so `trial.Trial` was empty in production, that count was 0 against
    a non-zero task file, and `resolve_tier` returned D for every session.
    The sessions it builds are counted into `populated` for the same reason
    every other key computed is.

    **`register_default_paramsets()` runs between `activate_all()` and the
    stages, and it is not optional.** `detect.EyeValidity`/
    `detect.EyeDetection` are the first stages in this list whose
    `key_source` requires a `paramset.ParamSet` row to exist before it names
    any candidate at all, so until this call existed the whole detection
    subsystem populated nothing, reported no error, and rendered zeros in
    the daily report as if they were measurements. See that function.

    **Archival is OPT IN, and skipped -- visibly -- when it is not
    configured.** Controller ruling F: `archive_session` needs `nas_root`,
    `host` and `share` to publish anywhere, and the NAS this pipeline would
    publish to does not exist yet. `report["archived"]` is `None`, not `0`,
    when any of the three is absent -- mirroring `count_stale_jobs`'s own
    `None`-means-"nothing was actually inspected" convention above, for the
    identical reason: `0` would read as "configured, and there was nothing
    new to archive", collapsing it with the genuinely-ran-and-found-nothing
    case. `cli/main.py`'s `daemon` dispatch prints this distinction rather
    than flattening it to a bare number. Every one of the three must be
    present, not just one -- `record_archive_outcome`'s own `host`/`share`
    parameters (its `ArchiveArtifact` row needs both) have no
    partial-configuration mode either, so a lone `--host` with no
    `--nas-root` is exactly as unusable as none of the three at all. (This
    used to cite `archive_session`'s own signature for the identical point;
    `archive_session` no longer takes `host`/`share` at all -- Task 10
    whole-branch review, cheap correction -- so the citation moved to the
    function that still does.)
    """
    activate_all(prefix=prefix)
    # SECOND, and before the `_computed_tables()` loop below: two of its
    # stages have a `key_source` that names no candidate at all until these
    # rows exist. See `register_default_paramsets` for what was silently
    # inert until this line was here, and for why it is not folded into
    # `activate_all`.
    register_default_paramsets()

    reaped = reap_stale_jobs(prefix=prefix)
    populated, errors = 0, []

    built, event_errors = _populate_event_stage()
    populated += built
    errors.extend(event_errors)

    for table in _computed_tables():
        try:
            result = table.populate(reserve_jobs=True, suppress_errors=True)
            populated += int(result["success_count"])
            # error_list entries are (key, error) -- datajoint/autopopulate.py.
            errors.extend(f"{table.__name__} {key}: {err}" for key, err in result["error_list"])
        except Exception as exc:  # a failing stage must not stop the others
            errors.append(f"{table.__name__}: {exc}")

    archived: int | None
    if nas_root is None or host is None or share is None:
        archived = None
    else:
        archived, archive_errors = _archive_stage(nas_root, host, share, prefix=prefix)
        errors.extend(archive_errors)

    return {
        "populated": populated,
        "errors": errors,
        "stale_jobs_reaped": reaped,
        "archived": archived,
    }
