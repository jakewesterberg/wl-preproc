"""The daily status report, per spec section 11.

Writes a dated file and prints it. Both, so a future `cron ... | mail` needs no
change here and the accumulating history answers "when did scratch start
filling up?" without anyone having planned for the question — a question this
file could not actually answer until the disk line below started measuring the
storage root it was handed instead of `/`. See the `scratch_headroom` call.

Reads and never writes.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from wl_preproc.cli.doctor import scratch_headroom
from wl_preproc.schema import DEFAULT_PREFIX

# Ingested is windowed to 24 h because "what landed since yesterday's report"
# is the question that section answers. Quarantine is not the same question and
# does not get the same window.
#
# Unwindowed was wrong: the section rendered the entire table forever, so a
# `failed_at` from two years ago was still listed as though it were news, and
# the section only ever grew. 24 h is wrong in the other direction: a session
# that fails late on a Friday would drop off Monday's report -- the exact case
# where the row most needs a human, since nobody was there to act on it when it
# was fresh. Seven days covers a weekend plus the working days either side of
# it, which is the real latency of someone noticing a quarantine and doing
# something about it.
#
# Rows older than the window are counted on their own line rather than silently
# dropped, the same reason `_NOT_YET_REPORTED` exists: "the table holds nothing
# older" and "you are not being shown what is older" must never render
# identically.
_QUARANTINE_WINDOW_DAYS = 7

# Spec section 9 rules that a quarantine row is history: a session that was
# quarantined, fixed, and re-ingested keeps its row, with nothing marking it
# resolved -- deliberately, because there is nothing to clean up. That is also
# why that session legitimately appears under BOTH Ingested and Quarantined in
# one report, which without a recency mark reads as a contradiction rather than
# as the repair story it is. So this does not invent a resolution mechanism it
# was told not to invent; it makes age visible, so an entry from six days ago
# sitting beside an Ingested line for the same session reads as "that is the
# one that got fixed". Rows are printed newest first for the same reason.
_QUARANTINE_NEW_HOURS = 24

# `detail` is the column that makes a quarantine row actionable -- for
# `checksum_mismatch` it holds the offending file paths, which is the whole
# point of spec section 5.4 -- and it was not rendered at all. It is a blob, so
# it can hold anything a caller put there (including, in this project's own
# shared test database, a real 64x64 NumPy array a schema guardrail test plants
# in `Quarantine.detail` on purpose). These bound what one row can do to the
# report: a summary an operator can act on, never the whole payload.
_DETAIL_MAX_CHARS = 400
_DETAIL_MAX_ITEMS = 6

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


def _compact(value) -> str:
    """One `detail` value as a single line, bounded, never raising.

    Whitespace is collapsed rather than preserved: a `detail["error"]` holding
    a multi-line pydantic ValidationError would otherwise break out of the
    markdown bullet it sits in and silently reformat the rest of the section.

    Nothing here tests truthiness of a value or compares it -- `bool(array)`
    and `array == x` are exactly what a NumPy value in this column makes
    unsafe (see `_DETAIL_MAX_CHARS`). `str()` of an oversized array is
    summarized by NumPy itself and then bounded again here regardless.
    """
    if isinstance(value, dict):
        return "{" + " ".join(f"{k}={_compact(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list | tuple):
        shown = [_compact(item) for item in value[:_DETAIL_MAX_ITEMS]]
        rest = len(value) - len(shown)
        return "; ".join(shown) + (f" (+{rest} more)" if rest > 0 else "")
    text = " ".join(str(value).split())
    return text if len(text) <= _DETAIL_MAX_CHARS else text[: _DETAIL_MAX_CHARS - 1] + "…"


def _detail_summary(detail) -> str:
    """Enough of a `Quarantine.detail` blob to act on, on one line."""
    if detail is None:
        return ""
    if isinstance(detail, dict):
        if not detail:
            return ""
        text = ", ".join(f"{key}={_compact(value)}" for key, value in detail.items())
    else:
        text = _compact(detail)
    return text if len(text) <= _DETAIL_MAX_CHARS else text[: _DETAIL_MAX_CHARS - 1] + "…"


def _quarantine_lines(row: dict, new_since: datetime.datetime) -> list[str]:
    """One quarantine row: what failed, whose session it was, and why.

    `subject`/`session_dt` are nullable by design (spec section 9: best
    effort, since the worst failure this pipeline has is the unparseable
    manifest that would have yielded them) and are shown when they are there,
    because "a quarantine report naming an animal and a date is far more
    useful than one naming a path" -- that section's own words, and the
    justification it gives for the two columns existing at all.
    """
    who = ""
    if row.get("subject") and row.get("session_dt"):
        who = f" — `{row['subject']}` @ {row['session_dt']:%Y-%m-%d %H:%M}"
    elif row.get("subject"):
        who = f" — `{row['subject']}`"
    elif row.get("session_dt"):
        who = f" — session {row['session_dt']:%Y-%m-%d %H:%M}"
    new = " **(new)**" if row["failed_at"] > new_since else ""
    head = (
        f"- `{row['session_dir']}` — **{row['reason']}**{who} — "
        f"failed {row['failed_at']:%Y-%m-%d %H:%M}{new}"
    )
    detail = _detail_summary(row["detail"])
    return [head, f"  - {detail}"] if detail else [head]


def build_report(
    root: Path,
    prefix: str = DEFAULT_PREFIX,
    now: datetime.datetime | None = None,
) -> str:
    from wl_preproc.contracts.manifest import SessionManifest
    from wl_preproc.contracts.paths import MANIFEST_FILENAME, SessionLayout
    from wl_preproc.daemon import count_stale_jobs
    from wl_preproc.ingest import landing
    from wl_preproc.ingest.sentinel import is_stalled, missing_systems

    # The one guarded walk, imported rather than reimplemented. This function
    # had its own copy of `sorted(root.iterdir())` plus the two per-child
    # checks and none of the guards that make them survivable, so `wlpp
    # ingest` returned 0 on a root `wlpp report` then crashed on -- no dated
    # file written and the stalled alarm lost, with a genuinely stalled
    # session sitting under that same root. A second copy of a walk whose
    # every failure mode is documented in one long docstring somewhere else
    # is how that happened; importing it is how it stays fixed. Underscored
    # and imported anyway: it is package-internal, not public API, and its
    # docstring names this caller.
    from wl_preproc.ingest.watcher import _candidate_dirs
    from wl_preproc.schema import ingest

    at = now or datetime.datetime.now(datetime.UTC)
    if at.tzinfo is None or at.utcoffset() is None:
        # Identical coercion to `scan_once`'s own (wl_preproc/ingest/watcher.py),
        # and needed for the identical reason: the stalled-transfers loop below
        # passes `at` straight through to `is_stalled`, which subtracts it from
        # `last_change_at`'s always-aware return -- raising `TypeError: can't
        # subtract offset-naive and offset-aware datetimes` the moment any
        # INCOMPLETE session sits under `root`, not merely a stalled one
        # (`is_stalled` only short-circuits before that subtraction for a
        # COMPLETE session). Found in review: this report's own shipped test
        # suite already passed a naive `now`
        # (`test_it_writes_a_dated_file_and_returns_its_path`) and stayed
        # green only because that fixture's `root` happened to hold no
        # incomplete session at the time -- a gap in the fixture, not proof
        # the crash could not happen. `.replace`, not `.astimezone()`:
        # `.astimezone()` on a naive input assumes the *system* timezone, not
        # UTC, silently shifting the caller's intended instant -- the same
        # warning `landing.to_naive_utc`'s own docstring gives for the
        # opposite direction.
        at = at.replace(tzinfo=datetime.UTC)
    ingest.activate(prefix=prefix)

    # Every window boundary below goes through `landing.to_naive_utc`, the one
    # place this codebase converts an aware datetime into the naive UTC value
    # DataJoint's `datetime` columns actually store -- which is what
    # `ingested_at` and `failed_at` both hold. `%Y-%m-%d %H:%M:%S` drops the
    # offset without applying it, so an aware `now` at, say, +13:00 formatted
    # directly used to move the boundary by the caller's whole offset: the
    # "24 h" window became 11 h there and 35 h at -11:00, and a 12-hour-old
    # session simply vanished from a report generated in New Zealand. Nothing
    # about the conversion is specific to this file, which is exactly why it
    # is imported rather than re-derived here.
    since = landing.to_naive_utc(at - datetime.timedelta(hours=24))
    # .to_dicts(), not .fetch(as_dict=True): DataJoint 2.3.2 deprecates bare
    # fetch() outright -- it warns on every call -- and this project's suite
    # must stay at zero warnings. `.fetch1()` is unaffected by that
    # deprecation and is used unchanged elsewhere in this codebase.
    recent = (ingest.Ingestion & f"ingested_at > '{since:%Y-%m-%d %H:%M:%S}'").to_dicts()

    q_since = landing.to_naive_utc(at - datetime.timedelta(days=_QUARANTINE_WINDOW_DAYS))
    q_new_since = landing.to_naive_utc(at - datetime.timedelta(hours=_QUARANTINE_NEW_HOURS))
    q_window = f"failed_at > '{q_since:%Y-%m-%d %H:%M:%S}'"
    quarantined = sorted(
        (ingest.Quarantine & q_window).to_dicts(),
        key=lambda row: row["failed_at"],
        reverse=True,
    )
    older_quarantines = len(ingest.Quarantine & f"failed_at <= '{q_since:%Y-%m-%d %H:%M:%S}'")

    candidates, root_fault = _candidate_dirs(Path(root))
    stalled: list[tuple[Path, list[str]]] = []
    for child in candidates:
        try:
            manifest = SessionManifest.from_yaml((child / MANIFEST_FILENAME).read_text())
        except Exception:
            continue  # already a quarantine row; not also a stall
        # `child.name`, not `manifest.session_id`: those two are equal for
        # every session `wlpp ingest` would land, because a mismatch is a
        # `session_id_mismatch` quarantine before anything else looks at the
        # directory -- but this walk deliberately does not filter on
        # quarantine state, so a mismatching session reaching here used to be
        # stall-checked against `root / <manifest's id>`, a path that does not
        # exist. `last_change_at` on a missing directory falls back to
        # `datetime.min` by design, so that session reported as stalled
        # unconditionally, forever, naming a directory nobody could go look at.
        layout = SessionLayout(child.parent, child.name)
        if is_stalled(layout, manifest, now=at):
            # `missing_systems`' first real consumer. Design section 4.3 asks
            # for stalled transfers "with the systems still missing" and the
            # report printed only the path, so two stalled sessions missing
            # different systems rendered identically -- with five possible
            # systems that is the difference between knowing a transfer
            # stalled and knowing which rig to go look at.
            stalled.append((child, missing_systems(layout, manifest)))

    try:
        # `root`, not `/`. `doctor.py` justifies the `/` proxy with "there is
        # no scratch-root configuration to check instead", which is true of
        # `wlpp doctor` -- it is handed nothing -- and false here: this
        # function's own first argument is the storage root. Reporting `/`'s
        # free space under a heading this file's docstring uses to claim it
        # answers "when did scratch start filling up?" was measuring a
        # different disk than the one the question is about, on any host where
        # scratch is its own mount. `doctor`'s behaviour is unchanged.
        free_gib, headroom_ok = scratch_headroom(str(root))
        disk = f"- scratch (`{root}`): {free_gib} GiB free {'(ok)' if headroom_ok else '(LOW)'}"
    except OSError as exc:
        # A missing, unreadable, or unsearchable root reaches `statvfs` too,
        # and the whole point of the guarded walk above is that such a root
        # still produces a report. An unmeasurable disk says so; it must never
        # fall back to a number, and least of all to one reading "(ok)".
        disk = f"- scratch (`{root}`): **not measured** — {type(exc).__name__}: {exc}"

    stale = count_stale_jobs()

    lines = [f"# wl-preproc daily — {at:%Y-%m-%d}", ""]

    lines += [f"## Ingested (24 h) — {len(recent)}", ""]
    lines += [f"- `{row['session_dir']}` ({row['integrity']})" for row in recent] or ["- none"]

    lines += ["", f"## Quarantined ({_QUARANTINE_WINDOW_DAYS} d) — {len(quarantined)}", ""]
    if quarantined:
        for row in quarantined:
            lines += _quarantine_lines(row, q_new_since)
    else:
        lines += ["- none"]
    if older_quarantines:
        lines += [
            f"- _{older_quarantines} older row(s) not shown — "
            f"quarantine rows are history (spec section 9) and are never cleared, "
            f"so this section shows the last {_QUARANTINE_WINDOW_DAYS} days only._"
        ]

    lines += ["", f"## Stalled transfers — {len(stalled)}", ""]
    if root_fault is not None:
        # `_candidate_dirs` separates a per-child fault (skipped, and already
        # a per-session outcome for `wlpp ingest`) from one that stopped it
        # listing `root` at all. The second kind means this count is whatever
        # could be read, not the whole root, and saying nothing would render a
        # storage root that could not be opened identically to one holding no
        # stalled transfer -- the same silence `ScanResult.root_error` exists
        # to break for the scan.
        lines += [
            f"- **`{root}` was not fully scanned: "
            f"{type(root_fault).__name__}: {root_fault}** — "
            "the count above covers only what could be read.",
        ]
    lines += [
        f"- `{path}` — missing: "
        + (", ".join(missing) if missing else "none (markers landed mid-scan)")
        for path, missing in stalled
    ] or ["- none"]

    lines += ["", "## Deferred (transient contention)", ""]
    lines += [_DEFERRED_NOTE]

    lines += ["", "## Stuck jobs", ""]
    lines += [
        "- not checked (no schema activated in this process)"
        if stale is None
        else f"- {stale} stale reservation(s)"
    ]

    lines += ["", "## Disk", ""]
    lines += [disk]

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
