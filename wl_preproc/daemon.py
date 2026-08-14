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

from wl_preproc.schema import core, coverage, paramset, request


def _computed_tables() -> list:
    """The computed tables, in dependency order.

    Empty in 1c-1: nothing computes yet. The ordering lives here so that 1c-4's
    timebase and coverage stages, and Phase 2's sorting, extend one list rather
    than inventing their own traversal.
    """
    return []


def reap_stale_jobs(prefix: str = "wlpp", older_than_s: int = 3600) -> int:
    """Clear job reservations left behind by a crashed populate.

    A crashed populate leaves its key marked reserved in a table's `~~` job
    queue, and the key is skipped forever after — silently, which is what
    makes it section 10's top-four hazard rather than an annoyance.

    This project's first draft of this function reached for
    ``schema_obj.jobs`` restricted by a raw ``TIMESTAMPDIFF`` SQL string,
    unverified against DataJoint 2.3.2. None of that survived contact with
    the real API — confirmed by reading ``datajoint/schemas.py`` and
    ``datajoint/jobs.py``, and by exercising a genuinely stuck reservation
    against a live MySQL container (this version pins DataJoint 2.3.2):

    * ``dj.Schema.jobs`` is not a single restrictable query. It is a
      ``list[Job]``, one entry per Computed/Imported table that already has a
      *declared* ``~~table_name`` job table (created lazily on first
      ``populate(reserve_jobs=True)`` or first ``table.jobs`` access). Every
      table in this project's schemas is ``dj.Manual`` — see
      ``_computed_tables()`` above — so today this list is always empty and
      there is genuinely nothing to reap yet; restricting a bare ``list``
      with ``&`` raises ``TypeError`` the first time a Computed table exists
      anywhere (reproduced directly: ``TypeError: unsupported operand
      type(s) for &: 'list' and 'str'``).
    * ``dj.Schema.jobs`` also raises ``DataJointError`` outright — not
      "returns nothing" — if the schema itself has not been ``.activate()``d.
      ``run_once`` activates all four schemas before calling this function,
      but a caller may invoke it directly against a process that has only
      activated some of them (exactly what
      ``tests/schema/test_daemon.py``'s ``daemon_env`` fixture does — it
      activates ``core`` and ``request`` but not ``coverage``/``paramset``),
      so each schema is checked with ``is_activated()`` and skipped, not
      queried, if it is not yet live.

    Each ``Job`` exposes ``.refresh(orphan_timeout=...)``, which reads like
    the sanctioned tool for exactly this hazard: "Reserved jobs older than
    this are considered orphaned... deleted and re-added as pending"
    (``datajoint/jobs.py``). It is deliberately NOT used here, because it did
    not survive contact either, on two more counts found by exercising a real
    reserved-then-abandoned job (see ``prove_reaper_works.py``, run against a
    live MySQL container during development, not shipped):

    * its gate is ``if orphan_timeout is not None and orphan_timeout > 0:`` —
      so ``orphan_timeout=0`` silently no-ops instead of meaning "no grace
      period, reap anything reserved". The reaper test calls this function
      with exactly ``older_than_s=0``, so a passthrough to ``refresh()``
      would report ``freed=0`` while doing nothing, for the wrong reason.
    * its own comparison is ``reserved_time < CURRENT_TIMESTAMP - {interval}``
      using bare ``CURRENT_TIMESTAMP`` (second precision), against
      ``reserved_time``, a ``datetime(3)`` column written via
      ``CURRENT_TIMESTAMP(3)`` inside ``Job.reserve()``. Checked within the
      same wall-clock second as the reservation — plausible for any
      ``older_than_s`` small enough to matter in a test, and not otherwise
      unlikely — the millisecond remainder can sort a just-reserved row
      *after* the truncated comparison timestamp, so the row is missed even
      though it is, correctly measured, already in the past.

    So the condition below matches ``reserved_time``'s own precision
    (``CURRENT_TIMESTAMP(3)``, the same function ``Job.reserve()`` uses to
    write it) and is applied directly, unconditionally on ``older_than_s``,
    rather than through ``refresh()``'s gate. This also means "add new
    pending jobs from key_source" — ``refresh()``'s unrelated first step,
    always run whether or not ``orphan_timeout`` is set — never happens as a
    side effect of what is meant to be a narrowly-scoped reaper.
    """
    # The same default `refresh()` itself falls back to for a freshly-re-pended
    # job (datajoint/jobs.py: `priority = self.connection._config.jobs.
    # default_priority`). `dj.config` is that same object for every connection
    # this project ever opens — always via `dj.conn()`, never with a
    # per-connection override — so reading it here is equivalent and does not
    # need the underscore-prefixed attribute.
    default_priority = dj.config.jobs.default_priority

    freed = 0
    for schema_obj in (core.schema, coverage.schema, paramset.schema, request.schema):
        if not schema_obj.is_activated():
            continue
        for job in schema_obj.jobs:
            interval = job.adapter.interval_expr(max(int(older_than_s), 0), "second")
            stale = job.reserved & f"reserved_time < CURRENT_TIMESTAMP(3) - {interval}"
            keys = stale.keys()
            for key in keys:
                (job & key).delete_quick()
                job.insert1({**key, "status": "pending", "priority": default_priority})
            freed += len(keys)
    return freed


def run_once(prefix: str = "wlpp") -> dict:
    """One pass of the runner. Returns what it did, for the daily report."""
    request.activate(prefix=prefix)
    coverage.activate(prefix=prefix)
    paramset.activate(prefix=prefix)

    reaped = reap_stale_jobs(prefix=prefix)
    populated, errors = 0, []
    for table in _computed_tables():
        try:
            table.populate(reserve_jobs=True, suppress_errors=True)
            populated += 1
        except Exception as exc:  # a failing stage must not stop the others
            errors.append(f"{table.__name__}: {exc}")

    return {"populated": populated, "errors": errors, "stale_jobs_reaped": reaped}
