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

# Task-11 brief, Controller ruling D: a persistent step-2 skip (design spec
# `docs/superpowers/specs/2026-08-30-eye-ohdpi-calibration-and-gaze-design.md`
# section 8, and that same document's section 10 open question 3) is worth
# noticing precisely because no individual session reveals it -- but nothing
# in `schema/eye.py::EyeCalibration` records whether a `.bhv2` was even
# found. `read_online_map`'s own `None` return (`eye/calibration.py`)
# already collapses every step-2 non-outcome -- no `.bhv2` found, one found
# but unreadable, one found but with no usable calibration inside it -- into
# a single `None` signal upstream of this report (that function's own
# docstring: absence and a present-but-unusable `Bhv2Calibration` reach
# `None` from `as_calibration_map` by two different mechanisms -- `a is None` for
# the first, a non-six-element `a` for the second, per `bhv2.py`'s own
# `present = a is not None`; "Unparseable is different in kind... caught
# here"), and
# `resolve_calibration`'s reason text never names which fallback source was
# tried and rejected versus never offered at all -- so the
# `calibration_source` breakdown below cannot tell any of those apart
# either. Named here rather than silently rendered as though the breakdown
# were complete: the same "a missing distinction must never render
# identically" principle this module's own docstring states one level up
# for a missing vs. empty section, applied to a single line within one
# instead of a whole section.
_EYE_ONLINE_GAP_NOTE = (
    "this breakdown cannot separate a session whose online calibration was "
    "tried and rejected from one where no `.bhv2` was ever found -- "
    "nothing in `EyeCalibration` records whether a `.bhv2` was found at all, "
    "so both render identically here, folded into whichever source the "
    "session actually resolved to"
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


def _verified_archives(
    prefix: str = DEFAULT_PREFIX, nas_root: Path | None = None
) -> list[dict] | None:
    """Every archived session whose every RECORDED `ArchiveVerification` row
    matched AND whose completion sentinel is confirmed present on the NAS
    itself -- an approximation of design spec section 5.2 condition 2, not
    that condition itself, and the difference is worth stating precisely
    (review round: an earlier version of this docstring overclaimed the two
    were the same thing). Condition 2, as `archive/reclaim.py`'s own
    `reclaim_conditions` implements it, compares `len(matched)` against
    `expected_file_count` -- a count read fresh from the session's DONE
    markers on every call. This function compares `len(matched)` against
    `len(verifications)`, the row count ALREADY on record, and reads no
    marker at all. The two agree whenever verification ran to completion and
    the session's file set has not changed since -- true of every session
    this codebase can currently produce -- and diverge only if a session's
    DONE markers gained files after its `ArchiveVerification` rows were
    written, a case nothing in this design currently creates. That gap is
    accepted deliberately here: this function is called from `build_report`
    (see below) and from `cli/main.py`'s `_staged_entries` (`wlpp
    tape-manifest`), and re-reading every archived session's DONE markers
    from disk for a rows-only "is this staged for a cartridge" question would
    reintroduce the exact per-session filesystem cost review moved OUT of
    the responder's hot path for `_unreclaimed_sessions` below -- see that
    function's own docstring.

    **The sentinel check is not the same accepted gap.** Found in review
    (Task 10 whole-branch pass, part of the BLOCKING finding): this function
    used to read only DATABASE rows, never the filesystem, so a partial NAS
    publish -- `copytree`/`manifest_digest` raising mid-`archive_session`,
    the exact failure this design most expects -- that left a STALE prior
    row in place (fixed separately, in `archive/stage.py::archive_session`)
    would still have been reported here as verified even after that fix,
    for a session `archive_session` had never actually gotten to insert a
    fresh row for at all: a row-only read cannot distinguish "confirmed
    whole" from "a write that never finished", which is `SENTINEL_NAME`'s
    entire declared purpose (`archive/stage.py`'s own module docstring:
    "a half-copied artifact and a finished one are the same observation"
    without it) -- and before this fix NOTHING outside tests ever read it.
    `nas_root` is `None`-able and FAILS CLOSED: absent, no row can ever
    verify (see the `is None` branch below), because "not checked" must
    never render as "checked, and it passed" -- the identical reasoning
    `daemon.run_once`'s `archived: None` already applies to the archival
    stage itself. Returns `None` outright (not `[]`) in that case, so a
    caller can render "not checked" instead of a fabricated "0 verified" --
    the same `count_stale_jobs`-style distinction, applied here.

    An `ArchiveArtifact` row with ZERO `ArchiveVerification` children is not
    verified -- the same `len(matched) > 0` trap `archive/reclaim.py`'s own
    `every_file_verified` condition guards against (task-7 Controller ruling
    B) -- so an artifact nothing has checked yet never reads as though it had.

    Shared by two callers that must never define "verified" two different
    ways: `cli/main.py`'s `_staged_entries` (`wlpp tape-manifest` -- a session
    staged for a cartridge and a session whose rig may clear its copy are the
    identical fact, design spec section 3.2's own words, "the same list
    section 5.2 already computes, read for a different purpose") and
    `build_report` below. Living here rather than in a third module: this
    file's whole job is already "read state, never write" (see this module's
    own docstring), and this predicate reads, nothing else -- reading the
    sentinel's mere presence is still reading, not writing. Underscored and
    imported across a module boundary anyway, the same as `_candidate_dirs`
    below in `gather_readings` -- package-internal, not public API, and this
    docstring is what names both callers.

    Not called from `gather_readings`/`Readings` (moved out in review, along
    with `_unreclaimed_sessions` below -- see that function's own docstring
    for the reason: this predicate is cheap on its own, but living beside
    the other one inside `gather_readings` put both on `build_health`'s
    hot, lock-held polling path for no reason, since `build_health` never
    reads either result).
    """
    if nas_root is None:
        return None

    from wl_preproc.archive.stage import SENTINEL_NAME
    from wl_preproc.schema import archive as archive_schema

    archive_schema.activate(prefix=prefix)
    verified = []
    for row in archive_schema.ArchiveArtifact.to_dicts():
        key = {"subject": row["subject"], "session_datetime": row["session_datetime"]}
        verifications = archive_schema.ArchiveVerification & key
        matched = verifications & "matched = 1"
        if len(verifications) == 0 or len(matched) != len(verifications):
            continue
        # `.is_file()`, guarded: a permission fault on a NAS mount (EACCES,
        # not swallowed by pathlib's own ignored-errno set -- see `ingest/
        # watcher.py::_candidate_dirs`'s own extensively-verified reasoning
        # for the identical guard) must read as "not confirmed", the same
        # fail-closed direction as `nas_root is None` above, not crash the
        # whole daily report over one unreadable mount.
        try:
            confirmed = (nas_root / row["archive_path"] / SENTINEL_NAME).is_file()
        except OSError:
            confirmed = False
        if not confirmed:
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


def _orphaned_archiving_dirs(root: Path) -> list[Path]:
    """`.{session_id}.archiving` scratch directories `archive_session`
    created and never reclaimed. `archive/stage.py`'s own `scratch =
    session_dir.parent / f".{session_dir.name}.archiving"` is removed only
    when `all_matched` (that module's own docstring: "Scratch is reclaimed
    only once the artifact is known-good"). A session whose most recent
    archive attempt failed verification leaves one behind -- a full-size
    compressed copy parked on the very scratch disk `refuses_new_sessions`
    (parent spec section 8.4's "Backpressure at ingest") exists to protect
    -- and before this, nothing named it anywhere (Task 10 whole-branch
    review).

    Best-effort, matching every other filesystem read in this module: a
    glob that cannot run at all (an unreadable or missing `root`) reports
    zero rather than crashing the whole report over it, and a fault
    checking one candidate is skipped rather than fatal to the rest --
    the same asymmetry `ingest/watcher.py::_candidate_dirs` documents at
    length for the identical class of call.
    """
    try:
        candidates = list(root.glob(".*.archiving"))
    except OSError:
        return []
    orphaned = []
    for path in candidates:
        try:
            if path.is_dir():
                orphaned.append(path)
        except OSError:
            continue
    return sorted(orphaned)


def _unreclaimed_sessions(
    root: Path, prefix: str = DEFAULT_PREFIX
) -> tuple[list[dict], int]:
    """`(blocked, unarchived_count)` -- parent spec §8.5, quoted by THIS
    design document's own section 5.2: "since an ungated session is what
    will eventually fill scratch". `blocked` is one dict per archived
    session that `reclaim.blocking()` finds at least one failing condition
    for, `session_dir` and all. `unarchived_count` is how many sessions this
    walk finds landed (an `ingest.Ingestion` row exists) with no
    `ArchiveArtifact` row at all -- see `build_report`'s own comment on why
    that number is rendered too, not just `blocked`.

    Called directly by `build_report`, NOT threaded through `Readings`/
    `gather_readings` (review round -- Task 9 first put this inside
    `gather_readings`, which `build_health` also calls, on every `GET
    /health` poll the responder answers, UNDER THE SAME PROCESS-WIDE LOCK
    THAT ALSO SERIALISES JOB ACCEPTS (`responder/server.py`'s own
    docstring). `build_health` never reads this function's result --
    grepped, no hit for "unreclaimed" anywhere in `responder/health.py` --
    so every poll was walking every candidate session directory (an
    `rglob` plus a YAML parse per session) and running `reclaim_conditions`
    (several more DataJoint queries) against every archived one, for
    nothing, forever, with the cost growing as archive history grows. Moved
    here so only `wlpp report` -- a periodic batch job, not a per-request
    hot path -- pays for it. This does mean `root` gets walked twice when
    `build_report` runs (`gather_readings` above walks it once already, for
    `stalled`) rather than once; accepted deliberately, since a report run
    once a day paying for a second directory walk is a complete non-issue
    next to a responder poll that blocks job acceptance.

    Scoped to sessions that already have an `ArchiveArtifact` row for
    `blocked` (not every session under `root`): a session never archived at
    all is not a reclamation candidate in any actionable sense yet -- it has
    no NAS copy to make scratch a mere cache of -- and `Stalled transfers`
    already covers a session still landing. That scoping is exactly why
    `unarchived_count` exists alongside it: `build_report`'s own module
    docstring states the principle one level up already, "a missing section
    and an empty one must never render identically" -- and in a build where
    nothing archives automatically yet (Task 10, not this one), EVERY
    landed session sits unarchived by default, so `blocked` reading `0`
    would otherwise read as "nothing is holding scratch" when the true state
    is "nothing on scratch has been checked into this list at all" (review
    round finding).
    """
    from wl_preproc.archive import reclaim as archive_reclaim
    from wl_preproc.archive.verify import _expected_digests
    from wl_preproc.contracts.manifest import SessionManifest
    from wl_preproc.contracts.paths import MANIFEST_FILENAME
    from wl_preproc.ingest import landing
    from wl_preproc.ingest.watcher import _candidate_dirs
    from wl_preproc.schema import archive as archive_schema
    from wl_preproc.schema import ingest as ingest_schema

    ingest_schema.activate(prefix=prefix)
    archive_schema.activate(prefix=prefix)

    # `(subject, session_datetime) -> its scratch directory`, the same
    # derivation `land_session` used to write the row in the first place
    # (`ingest/landing.py::manifest_session_key`), so a directory found here
    # and an `ArchiveArtifact` row queried below resolve to the same key
    # whenever they describe the same session.
    candidates, _root_fault = _candidate_dirs(Path(root))
    dir_by_key: dict[tuple, Path] = {}
    for child in candidates:
        try:
            manifest = SessionManifest.from_yaml((child / MANIFEST_FILENAME).read_text())
        except Exception:
            continue  # already a quarantine row; not this function's to report
        session_key = landing.manifest_session_key(manifest)
        dir_by_key[(session_key["subject"], session_key["session_datetime"])] = child

    archived_rows = archive_schema.ArchiveArtifact.to_dicts()
    archived_keys = {(row["subject"], row["session_datetime"]) for row in archived_rows}
    landed_keys = {
        (row["subject"], row["session_datetime"])
        for row in ingest_schema.Ingestion.to_dicts()
    }
    unarchived_count = len(landed_keys - archived_keys)

    blocked: list[dict] = []
    for row in archived_rows:
        session_key = {"subject": row["subject"], "session_datetime": row["session_datetime"]}
        session_dir = dir_by_key.get((row["subject"], row["session_datetime"]))
        if session_dir is None:
            # The archived session's own scratch directory is not among this
            # walk's candidates -- root does not hold it (or holds it under a
            # name/subject this walk could not parse). `expected_file_count`
            # comes only from the DONE markers under that directory (see
            # `archive/verify.py::_expected_digests`), and there is no second
            # way to derive it -- do not fake a count to make this row render.
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
        blocking = archive_reclaim.blocking(conditions)
        if blocking:
            blocked.append({**session_key, "session_dir": session_dir, "blocking": blocking})

    return blocked, unarchived_count


def _eye_rows(prefix: str = DEFAULT_PREFIX) -> tuple[list[dict], list[dict]]:
    """Every `EyeQuality` and `EyeCalibration` row on record: `(quality_rows,
    calibration_rows)`. Query only, no rendering -- `build_report` below
    does that itself, the same split `_unreclaimed_sessions`/`_verified_
    archives` above use.

    **Not part of `Readings`/`gather_readings` (task-11 brief, Controller
    ruling A).** `gather_readings` is called by `responder/health.py::
    build_health` on every wl.works poll, under the single global lock that
    also serialises job accepts (`responder/server.py`'s own docstring), and
    the responder reads none of the values this function returns --
    `EyeQuality`/`EyeCalibration` name nothing `contracts/protocol.py`
    defines a wire shape for. This exact mistake was made and fixed once
    already in this project, for `_unreclaimed_sessions`/`_verified_
    archives` themselves (see each one's own docstring): putting a report-
    only computation inside `gather_readings` adds its full cost -- here,
    two more `to_dicts()` calls -- to a hot, lock-held path for nothing,
    forever, growing as this pipeline's session count grows. Kept here
    instead, called directly by `build_report`, so only `wlpp report` -- a
    periodic batch job -- ever pays for it.

    **This function itself is unwindowed, and stays that way -- `build_report`
    below applies windowing, not this query.** Neither table carries a
    timestamp of when ITS OWN row was computed -- `schema/eye.py`'s
    `EyeCalibration`/`EyeQuality` are both keyed `(subject, session_datetime,
    eye)` with no such column -- so there is no column here to filter on the
    way `gather_readings` filters `ingest.Ingestion` on `ingested_at`. That
    is not a gap: the two rows this function returns feed THREE different
    renderings in `build_report`, and each rendering picks its own scope
    from the SAME unfiltered rows rather than this function guessing at one.

    **Only the `calibration_source` breakdown is a true all-time running
    total (task-11 fix round, second Controller review).** It is four
    numbers that cannot grow without bound, and Controller ruling D's
    persistent-skip detection needs a ratio across all history, not one
    night's rows or one week's. The other two renderings ARE windowed, at
    two different widths, and the first fix round's own analogy to
    `_unreclaimed_sessions`' unwindowed "Archived sessions blocked from
    reclamation" list turned out to hold for neither: that list is a LIVE,
    re-evaluated predicate -- `archive_reclaim.blocking()` runs fresh on
    every report, so a session that clears every condition simply stops
    appearing, and no row there is permanent. A refused `EyeCalibration` row
    is the opposite -- `EyeCalibration.key_source`'s own docstring: "once
    written, that row is PERMANENT: DataJoint never recomputes an already-
    populated key" -- so it is `Quarantine`-shaped, not "Archived sessions"-
    shaped, and both the per-session listing and the "No canonical gaze"
    list are windowed accordingly: the per-session-per-eye listing to the
    same 24 hours `readings.ingested` already covers (reusing that row set
    rather than computing a second, independently-derived boundary -- it
    answers "what did the pipeline calibrate overnight, and what went
    wrong"), and "No canonical gaze" to the same 7 days `_QUARANTINE_
    WINDOW_DAYS` gives `Quarantine` -- see that constant's own comment for
    why 7 beats 24 h for a list of PERMANENT rows: "a session that fails
    late on a Friday would drop off Monday's report -- the exact case where
    the row most needs a human." Windowed at 7 days rather than left
    unwindowed for a sharper reason than that comment gives, too: under the
    exact scenario ruling D exists to detect -- MonkeyLogic's map never
    validating, the refusal rate climbing toward 100% -- this list grows at
    the same unbounded rate the per-session listing's own windowing was
    written to prevent, precisely during the incident the report most needs
    to stay readable for. Nothing is lost either way: `no_gaze`/`source_
    counts["refused"]` in `build_report` below are the identical predicate
    over these same unfiltered rows, so the all-time count the breakdown
    carries is never a second, independently-computed figure that could
    silently disagree with what the windowed list is a subset of.
    """
    from wl_preproc.schema import eye as eye_schema

    eye_schema.activate(prefix=prefix)
    return eye_schema.EyeQuality.to_dicts(), eye_schema.EyeCalibration.to_dicts()


def _eye_row_line(
    subject: str,
    session_datetime: datetime.datetime,
    eye_value: str,
    quality: dict | None,
    calibration: dict | None,
) -> str:
    """One `(subject, session_datetime, eye)` line for the Eye section's
    "Calibration and quality" list -- design spec section 8's first two
    asks ("tracking-loss percentage and blink rate", "validation_error_deg,
    and the residual for a fitted map") combined into one place, the same
    "everything needed to act on it" reasoning `_quarantine_lines` gives for
    combining `who`/`detail` rather than spreading one session's own facts
    across two lists a reader has to cross-reference.

    `quality`/`calibration` are `None` when that table has no row for this
    key YET -- a real state, not a bug. The two tables' `key_source`s
    differ on purpose: `EyeQuality` needs only an aligned ohDPI recording,
    while `EyeCalibration` also needs assembled events (`schema/eye.py`'s
    own `EyeQuality.key_source` docstring states the difference directly:
    "tracking loss and blink rate need only an ohDPI recording, not
    assembled events"), and `EyeCalibration`'s own gate is coarser still in
    the OTHER direction for its "no ohDPI recording could be aligned" branch
    (that table's own `key_source` docstring) -- a session can have either
    row well before, or entirely without, the other. `build_report` below
    calls this once per key in the UNION of both tables, never their
    intersection, so a row missing from one table is rendered as "not yet
    computed" rather than silently dropping the whole line -- the same "a
    missing section and an empty one must never render identically"
    principle this module's own docstring states one level up, applied to
    one row's own two halves.
    """
    who = f"`{subject}` @ {session_datetime:%Y-%m-%d %H:%M} — {eye_value}"

    if quality is None:
        quality_bit = "tracking loss/blink rate: not yet computed"
    else:
        quality_bit = (
            f"tracking loss {quality['tracking_loss_fraction'] * 100:.1f}%, "
            f"blink rate {quality['blink_rate_hz']:.2f} Hz"
        )

    if calibration is None:
        calibration_bit = "calibration: not yet computed"
    elif calibration["calibration_source"] == "refused":
        # The reason itself is not repeated here -- it has its own section
        # below ("No canonical gaze"). Controller ruling B's "never render a
        # single 'no gaze: N' count" is about THAT list, not this one; this
        # line still names the row as refused so the per-session list stays
        # a complete UNION (every key that exists in either table), without
        # printing a possibly-long reason string twice.
        calibration_bit = 'source: refused (see "No canonical gaze" below)'
    else:
        source = calibration["calibration_source"]
        calibration_bit = f"source: {source}"
        if source == "carried_forward" and calibration["carried_from_session_datetime"] is not None:
            calibration_bit += (
                f" (from {calibration['carried_from_session_datetime']:%Y-%m-%d %H:%M})"
            )
        if calibration["validation_error_deg"] is not None:
            calibration_bit += f", validation error {calibration['validation_error_deg']:.2f} deg"
        if source == "fitted" and calibration["residual_deg_rms"] is not None:
            calibration_bit += (
                f", residual {calibration['residual_deg_rms']:.2f}/"
                f"{calibration['residual_deg_max']:.2f} deg (rms/max)"
            )
        if calibration["reason"]:
            # A session can SUCCEED and still carry a note: `schema/eye.py::
            # EyeCalibration.make()`'s own `_combine_reason` stores a
            # partial-coverage-drop note in `reason` even when
            # `calibration_source` is not `refused` -- `resolve_calibration`
            # returns `reason=""` on every successful outcome, so a non-
            # empty `reason` reaching here is purely that method's own
            # coverage note ("a coverage gap that happens not to change the
            # verdict this time is still worth knowing about before it
            # grows into one that does" -- that method's own comment).
            # Skipping this because the row is not `refused` would drop
            # exactly the signal that comment calls out.
            calibration_bit += f" (note: {calibration['reason']})"

    return f"- {who} — {quality_bit} — {calibration_bit}"


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
    # No archival-state fields here (Task 9 first added two, `unreclaimed`
    # and `verified_archives`; review moved them back out). `Readings`
    # exists so `build_report` and the responder's `build_health` never
    # compute anything twice -- but `gather_readings` is also what every
    # `GET /health` poll calls, UNDER THE SAME PROCESS-WIDE LOCK THAT
    # SERIALISES JOB ACCEPTS (`responder/server.py`'s own docstring:
    # "GET /health's reads take the same lock as POST /jobs's writes"), and
    # `build_health` never read either field -- grepped, no hit for
    # "unreclaimed" or "verified_archives" anywhere in `responder/health.py`.
    # So the two Task 9 sections' whole cost (a per-session filesystem walk
    # plus several DataJoint queries each, apiece, growing with archive
    # history) was paid on every poll for nothing every time. Both are
    # computed by `build_report` directly instead -- see `_unreclaimed_
    # sessions` and `_verified_archives` above -- the identical move this
    # function already made once, for `q_new_since`/`older_quarantines`
    # (see that comment in `build_report`: "neither is a status number the
    # responder needs").


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
    )


def build_report(
    root: Path,
    prefix: str = DEFAULT_PREFIX,
    now: datetime.datetime | None = None,
    nas_root: Path | None = None,
) -> str:
    from wl_preproc.ingest import landing
    from wl_preproc.schema import ingest

    readings = gather_readings(root, prefix=prefix, now=now)
    at = readings.at

    # Not part of `Readings` -- see `_unreclaimed_sessions`' and
    # `_verified_archives`' own docstrings for why: both are cheap enough
    # for `wlpp report` alone to pay for, but neither belongs on
    # `gather_readings`' path, since `build_health` (the responder) also
    # calls that function on every poll and reads neither result.
    unreclaimed, unarchived_count = _unreclaimed_sessions(root, prefix=prefix)
    # `None` when `nas_root` is not given -- `_verified_archives`' own
    # fail-closed contract (Task 10 whole-branch review, BLOCKING fix 2).
    # Rendered as its own, explicitly-named state below, not silently
    # treated as zero.
    verified_archives = _verified_archives(prefix=prefix, nas_root=nas_root)
    orphaned_archiving = _orphaned_archiving_dirs(root)
    # Task-11 brief, Controller ruling A: computed here, not inside
    # `gather_readings` -- see `_eye_rows`' own docstring for why.
    eye_quality_rows, eye_calibration_rows = _eye_rows(prefix=prefix)

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

    # Design spec section 5.2 (quoting parent spec §8.5): the reclamation
    # gate, surfaced here "since an ungated session is what will eventually
    # fill scratch" -- see `_unreclaimed_sessions`' own docstring for the
    # exact chain (that phrase is THIS design document's own section 5.2,
    # which is itself quoting the parent spec's §8.5; this document's own
    # `## 8` is "Schema", with no subsections, and an earlier version of
    # this comment wrongly cited "design spec section 8.5" as if that were
    # a section of THIS document -- review round). Only ARCHIVED sessions
    # `reclaim.blocking()` finds at least one failing condition for -- named
    # precisely in the heading below, because nothing archives automatically
    # in this build (Task 10, not this one) and a heading that just said
    # "Unreclaimed sessions" would read "0" for a scratch disk on which
    # NOTHING can currently be freed, for the same reason `build_report`'s
    # own module docstring gives one level up: "a missing section and an
    # empty one must never render identically" (review round finding). The
    # `unarchived_count` line below exists for the identical reason: a
    # fully reclaimable, already-archived session (every condition already
    # passing) has nothing here to act on and is silent on purpose, same as
    # the sections above reading "- none" rather than listing what is FINE
    # -- but an UN-archived session must not read the same as a fine one.
    lines += ["", f"## Archived sessions blocked from reclamation — {len(unreclaimed)}", ""]
    lines += [
        f"- `{row['session_dir'].name}` — `{row['subject']}` @ "
        f"{row['session_datetime']:%Y-%m-%d %H:%M} — blocked on: "
        + ", ".join(row["blocking"])
        for row in unreclaimed
    ] or ["- none"]
    if unarchived_count:
        lines += [
            f"- _{unarchived_count} landed session(s) have never been archived and "
            "are not counted above -- nothing on scratch is checked into this list "
            "until `wlpp archive` runs on it._"
        ]

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
        "## Sessions whose rig may clear its copy"
        + ("" if verified_archives is None else f" — {len(verified_archives)}"),
        "",
    ]
    if verified_archives is None:
        # `nas_root` was not given, so `_verified_archives` could not confirm
        # the completion sentinel on the NAS for anything and failed closed
        # (its own docstring). Named explicitly rather than rendered as "0":
        # "not checked" and "checked, zero sessions verified" must never
        # read identically, the same principle this module's own docstring
        # states one level up for the unreclaimed-sessions section.
        lines += [
            "- not checked: no `--nas-root` given to `wlpp report`, so the "
            "completion sentinel could not be confirmed on the NAS for any "
            "session -- nothing is reported safe to clear until it can be."
        ]
    else:
        lines += [
            f"- `{row['subject']}` @ {row['session_datetime']:%Y-%m-%d %H:%M} — "
            f"verified at `{row['archive_host']}:{row['archive_share']}/{row['archive_path']}`"
            for row in verified_archives
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
    if orphaned_archiving:
        # Named, not silently absent -- the same "a missing section and an
        # empty one must never render identically" principle this module's
        # own docstring states, applied to a single line rather than a
        # whole section: a report that says nothing here when one of these
        # is sitting on scratch is indistinguishable from a report that
        # checked and found none (Task 10 whole-branch review).
        noun = "directory" if len(orphaned_archiving) == 1 else "directories"
        lines += [
            f"- {len(orphaned_archiving)} orphaned `.archiving` scratch {noun} "
            "(a failed archive attempt's compressed copy, reclaimed only by "
            "a future successful archive of the same session): "
            + ", ".join(f"`{path}`" for path in orphaned_archiving)
        ]

    # Design spec section 8: per session and per eye, tracking-loss
    # percentage, blink rate, `validation_error_deg` and a fitted map's own
    # residual, the `calibration_source` breakdown, and every session with
    # no canonical gaze -- each with its own specific reason, never
    # collapsed into one count (task-11 brief, Controller rulings B and C).
    # `### ` subheadings, a first for this file: three genuinely different
    # questions (per-session numbers, an aggregate breakdown, and the
    # sessions with no canonical gaze at all) do not read as one flat list,
    # and nothing about `_section`'s own `## `-only boundary (every other
    # report test's own helper) is disturbed by a heading level it never
    # looks for.
    lines += ["", "## Eye", ""]

    quality_by_key = {
        (row["subject"], row["session_datetime"], row["eye"]): row for row in eye_quality_rows
    }
    calibration_by_key = {
        (row["subject"], row["session_datetime"], row["eye"]): row
        for row in eye_calibration_rows
    }
    # UNION, not intersection -- see `_eye_row_line`'s own docstring: the two
    # tables' `key_source`s differ, so a session can clear one gate well
    # before, or entirely without, the other.
    eye_keys_all = set(quality_by_key) | set(calibration_by_key)

    # Task-11 fix round (Controller review): this list used to print one
    # line per session per eye for EVERY session ever calibrated. Neither
    # `EyeCalibration` nor `EyeQuality` carries a "when was this row
    # computed" column to window on directly (`_eye_rows`' own docstring),
    # but `readings.ingested` is already exactly "the same 24 hours
    # `Ingested` uses" -- the identical row set that section itself renders,
    # fetched once by `gather_readings` and reused here rather than run a
    # second, independently-windowed query that could silently disagree
    # with it. `Ingested`'s own reasoning applies unchanged: the question
    # this list answers is "what did the pipeline calibrate overnight, and
    # what went wrong" -- not "what has this pipeline ever calibrated".
    ingested_keys = {(row["subject"], row["session_datetime"]) for row in readings.ingested}
    eye_keys = sorted(key for key in eye_keys_all if (key[0], key[1]) in ingested_keys)

    lines += [f"### Calibration and quality, per session per eye (24 h) — {len(eye_keys)}", ""]
    lines += [
        _eye_row_line(
            subject, session_datetime, eye_value,
            quality_by_key.get((subject, session_datetime, eye_value)),
            calibration_by_key.get((subject, session_datetime, eye_value)),
        )
        for subject, session_datetime, eye_value in eye_keys
    ] or ["- none"]

    # `calibration_source` is a MySQL enum restricted to exactly these four
    # values (`tests/schema/test_eye_schema.py::
    # test_calibration_source_names_all_four_chain_steps` pins it), so
    # pre-seeding every key at 0 rather than building the dict from whatever
    # sources happen to appear is what keeps a source with ZERO rows so far
    # (every session refused today, say) from silently vanishing from the
    # breakdown instead of reading "0".
    #
    # Deliberately UNWINDOWED, unlike the list above (task-11 fix round):
    # it is four numbers, it cannot grow without bound the way a per-session
    # listing does, and Controller ruling D's persistent-skip detection --
    # "if MonkeyLogic's calibration is never usable, something is
    # systematically wrong" -- shows up as a ratio across ALL history, not
    # in one night's rows. Windowing this to 24 h would hide the exact
    # signal it exists to surface.
    source_counts = {source: 0 for source in ("fitted", "online", "carried_forward", "refused")}
    for row in eye_calibration_rows:
        source_counts[row["calibration_source"]] += 1

    lines += ["", "### Calibration source (running total)", ""]
    lines += [
        f"- {source}: {source_counts[source]}"
        for source in ("fitted", "online", "carried_forward", "refused")
    ]
    lines += [f"- _{_EYE_ONLINE_GAP_NOTE}._"]

    # Controller ruling B: distinct causes, distinct lines -- never a single
    # "no gaze: N" count. Each row prints its OWN stored `reason` verbatim,
    # so two sessions refused for unrelated causes render as two different
    # lines by construction, not by any special-casing here.
    #
    # Windowed to 7 days (task-11 fix round, second Controller review) --
    # `_QUARANTINE_WINDOW_DAYS`, not a new constant, because this really is
    # the same list `Quarantine` already is, not merely a similarly-sized
    # one: a refused `EyeCalibration` row is PERMANENT once written
    # (`EyeCalibration.key_source`'s own docstring; `_eye_rows`' own
    # docstring above quotes it in full), unlike `_unreclaimed_sessions`'
    # "Archived sessions blocked from reclamation" list, whose predicate is
    # re-evaluated fresh on every report. `_QUARANTINE_WINDOW_DAYS`'s own
    # comment already gives the reason 7 days beats 24 h for exactly this
    # shape of list: a session that fails (here: is refused) late on a
    # Friday must not drop off Monday's report before a human sees it.
    #
    # A SEPARATE `ingest.Ingestion` query, not `readings.ingested` or
    # `ingested_keys` above -- those are windowed to 24 h, the per-session
    # listing's own width, and reusing them here would silently give this
    # list the WRONG window rather than its own.
    no_gaze_since = landing.to_naive_utc(at - datetime.timedelta(days=_QUARANTINE_WINDOW_DAYS))
    no_gaze_ingested_keys = {
        (row["subject"], row["session_datetime"])
        for row in (
            ingest.Ingestion & f"ingested_at > '{no_gaze_since:%Y-%m-%d %H:%M:%S}'"
        ).to_dicts()
    }
    # UNFILTERED -- this is the identical predicate `source_counts["refused"]`
    # above already counts, over the same `eye_calibration_rows`, so
    # `len(no_gaze_all) == source_counts["refused"]` always holds. Nothing
    # is lost by windowing the LIST below: the all-time total is the
    # breakdown, not a second copy kept here.
    no_gaze_all = sorted(
        (row for row in eye_calibration_rows if row["calibration_source"] == "refused"),
        key=lambda row: (row["subject"], row["session_datetime"], row["eye"]),
    )
    no_gaze = [
        row for row in no_gaze_all
        if (row["subject"], row["session_datetime"]) in no_gaze_ingested_keys
    ]
    older_no_gaze = len(no_gaze_all) - len(no_gaze)

    lines += ["", f"### No canonical gaze ({_QUARANTINE_WINDOW_DAYS} d) — {len(no_gaze)}", ""]
    lines += [
        f"- `{row['subject']}` @ {row['session_datetime']:%Y-%m-%d %H:%M} — "
        f"{row['eye']}: {row['reason']}"
        for row in no_gaze
    ] or ["- none"]
    if older_no_gaze:
        # Same shape as `older_quarantines` above: rows outside the window
        # are counted, not silently dropped -- the all-time figure is the
        # `refused` line in the breakdown, pointed at rather than repeated.
        lines += [
            f"- _{older_no_gaze} older row(s) not shown — a refused calibration "
            f"is a permanent row (see the `refused` count in the breakdown "
            f"above for the all-time total), so this section shows the last "
            f"{_QUARANTINE_WINDOW_DAYS} days only._"
        ]

    lines += ["", "## Not yet reported", ""]
    lines += [f"- **{name}** — {why}" for name, why in _NOT_YET_REPORTED]

    return "\n".join(lines) + "\n"


def write_report(
    out_dir: Path,
    root: Path,
    prefix: str = DEFAULT_PREFIX,
    now: datetime.datetime | None = None,
    nas_root: Path | None = None,
) -> Path:
    """Write `out_dir/YYYY-MM-DD.md` and return its path."""
    at = now or datetime.datetime.now(datetime.UTC)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{at:%Y-%m-%d}.md"
    path.write_text(build_report(root, prefix=prefix, now=at, nas_root=nas_root), encoding="utf-8")
    return path
