"""The daily status report, per spec section 11.

Writes a dated file and prints it. Both, so a future `cron ... | mail` needs no
change here and the accumulating history answers "when did scratch start
filling up?" without anyone having planned for the question.

Reads and never writes.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from wl_preproc.cli.doctor import scratch_headroom
from wl_preproc.schema import DEFAULT_PREFIX

# Categories spec section 10 names that nothing can count yet. Listed rather
# than omitted: a silently missing section is indistinguishable from an empty
# one, and "no failures" must never render the same as "failures are not
# counted".
_NOT_YET_REPORTED = (
    ("Populated / failed", "1c-4 — nothing computes until the timebase stage exists"),
    ("Tier-D sessions", "1c-4 — the tier is derived from fit residuals"),
    ("Eye-detector outliers", "Phase 3 — the eye branch"),
)

# `Outcome.DEFERRED` (wl_preproc/ingest/watcher.py) postdates this report's own
# spec section and writes no row anywhere -- not `Ingestion`, not `Quarantine`
# -- when a session's paramset registration hits genuine database contention
# (`paramset.ContentionExhausted`: `paramset.register`'s own bounded retry
# loop exhausting every attempt to allocate a fresh index under real
# concurrent writers). It is not a stalled transfer either: the session IS
# complete, so `is_stalled` (wl_preproc/ingest/sentinel.py) reports it as not
# stalled on that basis alone, before the quiet-for-`STALL_AFTER_S` check ever
# runs -- confirmed directly from that function's own short-circuit. So a
# deferred session is invisible to every section above built from durable
# state, structurally, not by omission, and is named here for the identical
# reason `_NOT_YET_REPORTED` is: silence would read as "nothing happened", or
# worse, as "ingested", to a reader with no way to tell the difference from
# the report's own text.
#
# This is safe only because the catch that produces DEFERRED is narrow
# (`ContentionExhausted` specifically, never DataJoint's whole error tree --
# see that exception's own docstring in wl_preproc/schema/paramset.py) and
# genuine contention is transient by construction: the session is retried
# from scratch on the next `wlpp ingest` scan and ordinarily clears on its
# own. Unlike the three placeholders above, no future sub-project makes this
# section countable from what this report reads -- there is no row for one to
# eventually query, by design (spec section 13 excludes a lock, which is why
# registration can race at all) -- so this stays prose rather than a
# placeholder waiting on a task number.
_DEFERRED_NOTE = (
    "Not counted above, on purpose. `wlpp ingest` can set a session aside for "
    "one scan pass (`Outcome.DEFERRED`) when registering its parameters hits "
    "genuine database contention -- its own bounded retry loop exhausting "
    "every attempt to claim a fresh index under real concurrent writers. "
    "That is a database condition, not a defect in the session's own files, "
    "so it writes **no row anywhere**: not `Ingestion`, not `Quarantine`. It "
    "is not a stalled transfer either -- the session itself is complete, "
    "which is exactly why it reports as not stalled rather than stalled.\n\n"
    "A deferred session is not lost: it is retried from scratch on the next "
    "`wlpp ingest` scan and ordinarily clears on its own, since the "
    "condition this report can name (`paramset.ContentionExhausted`) is "
    "narrow and transient by construction. If a session seems to be missing "
    "from every section above, check that scan's own stdout for a "
    "`[deferred]` line naming it directly -- this report has no durable "
    "record of a pass that deferred rather than landed or quarantined."
)


def build_report(
    root: Path,
    prefix: str = DEFAULT_PREFIX,
    now: datetime.datetime | None = None,
) -> str:
    from wl_preproc.contracts.manifest import SessionManifest
    from wl_preproc.contracts.paths import MANIFEST_FILENAME, SessionLayout
    from wl_preproc.daemon import count_stale_jobs
    from wl_preproc.ingest.sentinel import is_stalled
    from wl_preproc.schema import ingest

    at = now or datetime.datetime.now(datetime.UTC)
    ingest.activate(prefix=prefix)

    since = at - datetime.timedelta(hours=24)
    # .to_dicts(), not .fetch(as_dict=True): DataJoint 2.3.2 deprecates bare
    # fetch() outright -- it warns on every call -- and this project's suite
    # must stay at zero warnings. `.fetch1()` is unaffected by that
    # deprecation and is used unchanged elsewhere in this codebase.
    recent = (ingest.Ingestion & f"ingested_at > '{since:%Y-%m-%d %H:%M:%S}'").to_dicts()
    quarantined = ingest.Quarantine.to_dicts()

    stalled: list[str] = []
    for child in sorted(Path(root).iterdir()):
        manifest_path = child / MANIFEST_FILENAME
        if not (child.is_dir() and manifest_path.is_file()):
            continue
        try:
            manifest = SessionManifest.from_yaml(manifest_path.read_text())
        except Exception:
            continue  # already a quarantine row; not also a stall
        layout = SessionLayout(Path(root), manifest.session_id)
        if is_stalled(layout, manifest, now=at):
            stalled.append(str(child))

    free_gib, headroom_ok = scratch_headroom()
    stale = count_stale_jobs()

    lines = [f"# wl-preproc daily — {at:%Y-%m-%d}", ""]

    lines += [f"## Ingested (24 h) — {len(recent)}", ""]
    lines += [f"- `{row['session_dir']}` ({row['integrity']})" for row in recent] or ["- none"]

    lines += ["", f"## Quarantined — {len(quarantined)}", ""]
    lines += [
        f"- `{row['session_dir']}` — **{row['reason']}**" for row in quarantined
    ] or ["- none"]

    lines += ["", f"## Stalled transfers — {len(stalled)}", ""]
    lines += [f"- `{path}`" for path in stalled] or ["- none"]

    lines += ["", "## Deferred (transient contention)", ""]
    lines += [_DEFERRED_NOTE]

    lines += ["", "## Stuck jobs", ""]
    lines += [
        "- not checked (no schema activated in this process)"
        if stale is None
        else f"- {stale} stale reservation(s)"
    ]

    lines += ["", "## Disk", ""]
    lines += [f"- scratch: {free_gib} GiB free {'(ok)' if headroom_ok else '(LOW)'}"]

    lines += ["", "## Not yet reported", ""]
    lines += [f"- **{name}** — {why}" for name, why in _NOT_YET_REPORTED]

    return "\n".join(lines) + "\n"


def write_report(
    out_dir: Path,
    root: Path,
    prefix: str = DEFAULT_PREFIX,
    now: datetime.datetime | None = None,
) -> Path:
    """Write `out_dir/YYYY-MM-DD.md` and return its path."""
    at = now or datetime.datetime.now(datetime.UTC)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{at:%Y-%m-%d}.md"
    path.write_text(build_report(root, prefix=prefix, now=at), encoding="utf-8")
    return path
