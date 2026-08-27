"""The daily status report, per spec section 11.

Writes a dated file and prints it. Both, so a future `cron ... | mail` needs no
change here and the accumulating history answers "when did scratch start
filling up?" without anyone having planned for the question — a question this
file could not actually answer until the disk line below started measuring the
storage root it was handed instead of `/`. See the `scratch_headroom` call.

Reads and never writes.
"""

from __future__ import annotations

import dataclasses
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
# point of spec section 5.4 -- and it was not rendered at all. It is a blob,
# so it can hold anything a caller put there, including a real NumPy array:
# this project's own shared test database has exactly one such row in
# `Quarantine.detail`, planted on purpose by
# `tests/schema/test_guardrails.py::test_every_blob_attribute_round_trips_
# an_array`. `_compact`/`_detail_summary` below are written to be safe
# against it regardless -- see their own docstrings -- but no test
# currently exercises the renderer against that row: its `failed_at` is
# fixed at 2026-01-01, which ages out of `_QUARANTINE_WINDOW_DAYS` above
# long before this function ever queries `Quarantine`, in any test
# collection order. These bound what one row can do to the report either
# way: a summary an operator can act on, never the whole payload.
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


def _verified_archives(prefix: str = DEFAULT_PREFIX) -> list[dict]:
    """Every archived session whose every original file verified.

    This is design spec section 5.2 condition 2 -- "every original file's
    reconstructed bytes matched the DONE marker's blake3" -- read on its own,
    with none of the predicate's other four conditions attached (task-9
    Controller ruling E: this report section "needs no predicate at all").
    An `ArchiveArtifact` row with ZERO `ArchiveVerification` children is not
    verified -- the same `len(matched) > 0` trap `archive/reclaim.py`'s own
    `every_file_verified` condition guards against (task-7 Controller ruling
    B) -- so an artifact nothing has checked yet never reads as though it had.

    Shared by two callers that must never define "verified" two different
    ways: `cli/main.py`'s `_staged_entries` (`wlpp tape-manifest` -- a session
    staged for a cartridge and a session whose rig may clear its copy are the
    identical fact, design spec section 3.2's own words, "the same list
    section 5.2 already computes, read for a different purpose") and
    `gather_readings` below. Living here rather than in a third module: this
    file's whole job is already "read state, never write" (see this module's
    own docstring), and this predicate reads, nothing else. Underscored and
    imported across a module boundary anyway, the same as `_candidate_dirs`
    a few lines below in `gather_readings` -- package-internal, not public
    API, and this docstring is what names both callers.
    """
    from wl_preproc.schema import archive as archive_schema

    archive_schema.activate(prefix=prefix)
    verified = []
    for row in archive_schema.ArchiveArtifact.to_dicts():
        key = {"subject": row["subject"], "session_datetime": row["session_datetime"]}
        verifications = archive_schema.ArchiveVerification & key
        matched = verifications & "matched = 1"
        if len(verifications) == 0 or len(matched) != len(verifications):
            continue
        verified.append(
            {
                "subject": row["subject"],
                "session_datetime": row["session_datetime"],
                "archive_host": row["archive_host"],
                "archive_share": row["archive_share"],
                "archive_path": row["archive_path"],
                "compressed_bytes": row["compressed_bytes"],
                "manifest_digest": row["manifest_digest"],
            }
        )
    return verified


@dataclasses.dataclass(frozen=True, slots=True)
class Readings:
    """Everything both renderings need, computed once.

    `build_report` turns this into Markdown; the responder turns it into
    `protocol.Reading` rows. Neither computes anything itself -- that is the
    entire reason this type exists rather than each caller doing its own
    queries, and it is the same move that pulled `scratch_headroom` out of
    `doctor.run_checks()` when the report needed the same number.
    """

    at: datetime.datetime
    ingested: list[dict]
    quarantined: list[dict]
    stalled: list[tuple[Path, list[str]]]
    walk_error: str | None
    stale_jobs: int | None
    free_gib: int
    headroom_ok: bool
    disk_error: str | None
    # Design spec section 8.5: the reclamation gate must be surfaced in this
    # report "since an ungated session is what will eventually fill scratch".
    # One dict per archived session that `reclaim.blocking()` finds at least
    # one failing condition for -- see `gather_readings` for how each is
    # built, and Controller ruling E (task-9) for why this needs a real
    # filesystem walk that `verified_archives` below does not.
    #
    # Defaulted to empty, unlike every field above: `tests/responder/
    # test_health.py`'s own `_base_readings` constructs a `Readings` directly
    # rather than through `gather_readings` (deliberately -- its docstring:
    # "bypasses the real filesystem/database walk entirely"), so it knows
    # nothing about archival state and never will need to. A field added
    # here without a default turned every one of that file's ten tests into
    # a `TypeError` inside `build_health`'s own `except Exception` -- caught
    # by running the full suite, not anticipated -- which silently reported
    # `verdict="down"` instead of failing the way a missing constructor
    # argument should. `dataclasses` requires every field after a defaulted
    # one to be defaulted too, which is satisfied here since both archival
    # fields are declared last.
    unreclaimed: list[dict] = dataclasses.field(default_factory=list)
    # Design spec section 3.2: "somebody has to tell the rig it is safe" --
    # the rig holds its own copy until THIS report says the archive is
    # verified, because this pipeline cannot reach the rig and transport is
    # pull-only. One dict per session whose every `ArchiveVerification` row
    # matched. See `_verified_archives`. Defaulted for the identical reason
    # `unreclaimed` above is.
    verified_archives: list[dict] = dataclasses.field(default_factory=list)


def gather_readings(
    root: Path,
    prefix: str = DEFAULT_PREFIX,
    now: datetime.datetime | None = None,
) -> Readings:
    """The numbers. `build_report` and the responder's `build_health` both
    render this rather than querying anything themselves -- see `Readings`.

    Reads and never writes, the same guarantee `build_report` has always
    carried (`test_the_report_opens_no_write_transaction`,
    `test_gather_readings_does_not_write`).
    """
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
    q_window = f"failed_at > '{q_since:%Y-%m-%d %H:%M:%S}'"
    quarantined = sorted(
        (ingest.Quarantine & q_window).to_dicts(),
        key=lambda row: row["failed_at"],
        reverse=True,
    )

    candidates, root_fault = _candidate_dirs(Path(root))
    stalled: list[tuple[Path, list[str]]] = []
    # `(subject, session_datetime) -> its scratch directory`, built in the
    # SAME walk as `stalled` rather than a second pass over `candidates`:
    # every entry comes from the identical parsed manifest, via
    # `manifest_session_key` -- the same derivation `land_session` used to
    # write the row in the first place (`ingest/landing.py`), so a directory
    # found here and an `ArchiveArtifact` row queried below resolve to the
    # same key whenever they describe the same session. Feeds the
    # `unreclaimed` computation below: `reclaim_conditions` needs
    # `expected_file_count`, which comes only from the DONE markers under a
    # session's own directory (task-9 Controller ruling E), and this is the
    # one place this function already has that directory in hand.
    dir_by_key: dict[tuple, Path] = {}
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
        session_key = landing.manifest_session_key(manifest)
        dir_by_key[(session_key["subject"], session_key["session_datetime"])] = child

    # Design spec section 8.5: "an ungated session is what will eventually
    # fill scratch" -- so every archived session that `reclaim.blocking()`
    # finds at least one failing condition for is named here, with which
    # condition. Scoped to sessions that already have an `ArchiveArtifact`
    # row (not every session under `root`): a session never archived at all
    # is not a reclamation candidate in any actionable sense yet -- it has no
    # NAS copy to make scratch a mere cache of -- and `Stalled transfers`
    # above already covers a session still landing. Iterated from
    # `ArchiveArtifact` rather than `dir_by_key`, so a session whose
    # directory this walk could not find (root not fully readable, or simply
    # a different root than the one it was archived from) is skipped rather
    # than guessed at -- see the `continue` below and task-9 Controller
    # ruling E, "do not fake a count to make the section render".
    from wl_preproc.archive import reclaim as archive_reclaim
    from wl_preproc.archive.verify import _expected_digests
    from wl_preproc.schema import archive as archive_schema

    archive_schema.activate(prefix=prefix)
    unreclaimed: list[dict] = []
    for row in archive_schema.ArchiveArtifact.to_dicts():
        session_key = {"subject": row["subject"], "session_datetime": row["session_datetime"]}
        session_dir = dir_by_key.get((row["subject"], row["session_datetime"]))
        if session_dir is None:
            continue
        try:
            # Reused, not re-derived: `_expected_digests` is `archive/
            # verify.py`'s own count of a session's expected files, and
            # `reclaim_conditions` below needs the identical number
            # `verify_store` used, not a second, independently-walked one
            # that could silently disagree with it.
            expected_file_count = len(_expected_digests(session_dir))
        except Exception:
            # A session archived once can still be MID a second, not-yet-
            # complete transfer of new files, or its markers can be
            # genuinely unreadable -- either way `_expected_digests`'
            # own "nothing to check" case (see its docstring), not a reason
            # to crash a daily report over one session.
            continue
        conditions = archive_reclaim.reclaim_conditions(
            session_key, expected_file_count, prefix=prefix
        )
        blocked = archive_reclaim.blocking(conditions)
        if blocked:
            unreclaimed.append({**session_key, "session_dir": session_dir, "blocking": blocked})

    verified_archives = _verified_archives(prefix=prefix)

    # `walk_error` mirrors `ScanResult.root_error` (wl_preproc/ingest/watcher.py)
    # exactly -- same construction, `f"{type}: {message}"` or `None` -- for the
    # identical reason: "this root is genuinely empty/fine" and "this root
    # could not be read at all" must never render identically.
    #
    # Kept as its own field rather than merged with the disk fault below (an
    # earlier version of this function did merge them into one `root_error`,
    # and review caught it by direct before/after comparison): `_candidate_
    # dirs` needs to list `root`'s own entries (`iterdir`, which needs
    # execute permission on `root` itself), while `scratch_headroom`'s
    # `statvfs` needs only search permission on `root`'s PARENT to reach it
    # -- not on `root` itself. So a `chmod 000` root fails the walk while the
    # disk still reads fine, and the merged version rendered that real
    # reading as "not measured", using the WALK's `PermissionError` for a
    # disk probe that had, in fact, succeeded. That is the exact inversion
    # `scratch_headroom`'s own docstring forbids ("must never fall back to a
    # number" runs the other way here too: a measured disk must never fall
    # back to looking unmeasured because of an unrelated fault). See
    # `test_a_walk_fault_does_not_suppress_a_real_disk_reading`.
    walk_error = f"{type(root_fault).__name__}: {root_fault}" if root_fault is not None else None
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
        disk_error = None
    except OSError as exc:
        # A missing, unreadable, or unsearchable root reaches `statvfs` too,
        # and the whole point of the guarded walk above is that such a root
        # still produces a report. An unmeasurable disk says so; it must never
        # fall back to a number, and least of all to one reading "(ok)" --
        # `free_gib=0, headroom_ok=False` are placeholders `build_report`
        # never renders on their own, only ever behind `disk_error`. Set
        # independently of `walk_error`, never as a fallback for it -- see
        # the comment above.
        free_gib, headroom_ok = 0, False
        disk_error = f"{type(exc).__name__}: {exc}"

    return Readings(
        at=at,
        ingested=recent,
        quarantined=quarantined,
        stalled=stalled,
        walk_error=walk_error,
        stale_jobs=count_stale_jobs(),
        free_gib=free_gib,
        headroom_ok=headroom_ok,
        disk_error=disk_error,
        unreclaimed=unreclaimed,
        verified_archives=verified_archives,
    )


def build_report(
    root: Path,
    prefix: str = DEFAULT_PREFIX,
    now: datetime.datetime | None = None,
) -> str:
    from wl_preproc.ingest import landing
    from wl_preproc.schema import ingest

    readings = gather_readings(root, prefix=prefix, now=now)
    at = readings.at

    # Two things `gather_readings` deliberately does not compute, because
    # neither is a status number the responder needs -- both are markdown-
    # display concerns of this renderer alone: `q_new_since` decides which of
    # the ALREADY-FETCHED `readings.quarantined` rows get the "(new)" mark,
    # and `older_quarantines` counts rows the window already excluded from
    # that same list. Both are cheap and derived straight from `readings.at`
    # (the second with one small COUNT query) -- neither is a second
    # definition of anything `gather_readings` already answered.
    q_since = landing.to_naive_utc(at - datetime.timedelta(days=_QUARANTINE_WINDOW_DAYS))
    q_new_since = landing.to_naive_utc(at - datetime.timedelta(hours=_QUARANTINE_NEW_HOURS))
    older_quarantines = len(ingest.Quarantine & f"failed_at <= '{q_since:%Y-%m-%d %H:%M:%S}'")

    if readings.disk_error is not None:
        disk = f"- scratch (`{root}`): **not measured** — {readings.disk_error}"
    else:
        disk = (
            f"- scratch (`{root}`): {readings.free_gib} GiB free "
            f"{'(ok)' if readings.headroom_ok else '(LOW)'}"
        )

    lines = [f"# wl-preproc daily — {at:%Y-%m-%d}", ""]

    lines += [f"## Ingested (24 h) — {len(readings.ingested)}", ""]
    lines += [
        f"- `{row['session_dir']}` ({row['integrity']})" for row in readings.ingested
    ] or ["- none"]

    lines += ["", f"## Quarantined ({_QUARANTINE_WINDOW_DAYS} d) — {len(readings.quarantined)}", ""]
    if readings.quarantined:
        for row in readings.quarantined:
            lines += _quarantine_lines(row, q_new_since)
    else:
        lines += ["- none"]
    if older_quarantines:
        lines += [
            f"- _{older_quarantines} older row(s) not shown — "
            f"quarantine rows are history (spec section 9) and are never cleared, "
            f"so this section shows the last {_QUARANTINE_WINDOW_DAYS} days only._"
        ]

    lines += ["", f"## Stalled transfers — {len(readings.stalled)}", ""]
    if readings.walk_error is not None:
        # `_candidate_dirs` separates a per-child fault from one that
        # stopped it listing `root` at all -- only the second kind reaches
        # here (as `readings.walk_error`). The first kind is a known gap,
        # not a covered one: a session directory that cannot be read is
        # skipped silently inside `_candidate_dirs` itself, by both `wlpp
        # ingest` (via `scan_once`) and `wlpp report` (via this same walk)
        # -- no `Quarantine` row, no `Ingestion` row, no line anywhere in
        # this report naming it. That is exactly the failure design section
        # 4.3 built the whole stalled-transfer alarm to catch -- "a
        # weekend's recording simply never appears, with nothing anywhere
        # saying so" -- still true of this one path. The silence is
        # `_candidate_dirs`'s own to fix, not this function's; named here
        # rather than fixed here, so the next reader does not mistake it
        # for handled.
        #
        # The second kind -- handled below -- means this count is whatever
        # could be read, not the whole root, and saying nothing would
        # render a storage root that could not be opened identically to one
        # holding no stalled transfer -- the same silence
        # `ScanResult.root_error` exists to break for the scan.
        lines += [
            f"- **`{root}` was not fully scanned: {readings.walk_error}** — "
            "the count above covers only what could be read.",
        ]
    lines += [
        f"- `{path}` — missing: "
        + (", ".join(missing) if missing else "none (markers landed mid-scan)")
        for path, missing in readings.stalled
    ] or ["- none"]

    # Design spec section 8.5: the reclamation gate, surfaced here "since an
    # ungated session is what will eventually fill scratch". Only sessions
    # `reclaim.blocking()` finds at least one failing condition for -- a
    # fully reclaimable session (every condition already passing) has
    # nothing here to act on and is silent, on purpose, the same as the
    # sections above reading "- none" rather than listing what is FINE.
    lines += ["", f"## Unreclaimed sessions — {len(readings.unreclaimed)}", ""]
    lines += [
        f"- `{row['session_dir'].name}` — `{row['subject']}` @ "
        f"{row['session_datetime']:%Y-%m-%d %H:%M} — blocked on: "
        + ", ".join(row["blocking"])
        for row in readings.unreclaimed
    ] or ["- none"]

    # Design spec section 3.2: "somebody has to tell the rig it is safe" --
    # the rig holds its own copy until THIS line says the archive verified,
    # because this pipeline cannot reach the rig (transport is pull-only)
    # and a report nobody reads is, in that section's own words, a failure
    # that "surfaces at the rig, hours away from the pipeline that caused
    # it." Named unconditionally, the same reason `Ingested`/`Quarantined`
    # above print "- none" rather than omit the heading when empty: a
    # missing section and an empty one must never render identically.
    lines += [
        "",
        f"## Sessions whose rig may clear its copy — {len(readings.verified_archives)}",
        "",
    ]
    lines += [
        f"- `{row['subject']}` @ {row['session_datetime']:%Y-%m-%d %H:%M} — "
        f"verified at `{row['archive_host']}:{row['archive_share']}/{row['archive_path']}`"
        for row in readings.verified_archives
    ] or ["- none"]

    lines += ["", "## Deferred (transient contention)", ""]
    lines += [_DEFERRED_NOTE]

    lines += ["", "## Stuck jobs", ""]
    lines += [
        "- not checked (no schema activated in this process)"
        if readings.stale_jobs is None
        else f"- {readings.stale_jobs} stale reservation(s)"
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
