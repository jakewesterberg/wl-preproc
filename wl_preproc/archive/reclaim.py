"""When the scratch copy may go.

**A named list, not a boolean.** Design spec section 5.2: each condition records
why it passed or failed, so the daily report can say WHICH one blocks a session
rather than that one does. Section 8.5 requires the gate be surfaced there
"since an ungated session is what will eventually fill scratch", and only a
named list makes that report actionable.

**Two kinds of condition, and what a force overrides.** 2026-09-26 rehydration
design, section 2. A SAFETY condition failing means the way back is not proven:
deleting past it could lose data, and no human judgement makes that safe. A
JUDGEMENT condition failing (`overridable=True`) means the session is not ready
to be freed; freeing it anyway costs a rehydration later, never data, so a
recorded `force` may override it. The archival design's section 5.3 promised "a
force that overrides" and never said what; this is the answer. "Timing not yet
computed" (`timing_resolved`) is SAFETY, not judgement, because freeing a session
then does not merely cost a rehydration: the timebase stages treat an absent
directory as an absent device and would record the session as having no
recordings, a permanent false row -- data corrupted, which section 2's definition
of judgement excludes (the rehydration spec's amendment block, added by the
whole-branch review). "Computed" means every system's clock fit has landed, not
merely the session-level `TimingProvenance` row, which is written even when one
system's fit failed (the fix wave's re-review, reproduced end to end).

**Incomplete today, and it says so.** The predicate can see only timing quality
of what a session produced: tier says nothing about whether a sort is good,
because section 6.5's unit QC metrics are 2b-6 and unbuilt. The canonical NWB
is Phase 3, and its condition is already here, failing, because the requester's
position is that reclamation follows the NWB (rehydration design, section 0,
ruling 3). Writing it as a growing list makes the current incompleteness
visible instead of implying the rule is finished.
"""

from __future__ import annotations

from dataclasses import dataclass

from wl_preproc.schema import DEFAULT_PREFIX


@dataclass(frozen=True, slots=True)
class Condition:
    name: str
    passed: bool
    detail: str
    # False -- SAFETY -- by default, so a condition nobody classified fails
    # closed: no force can override it.
    overridable: bool = False


@dataclass(frozen=True, slots=True)
class Predicate:
    conditions: tuple[Condition, ...]
    # Whether the session's LATEST `ReclamationHold` verdict is `force`.
    forced: bool


def blocking(predicate: Predicate) -> list[str]:
    """Every condition that actually blocks, in order -- not merely the first.

    A failing safety condition always blocks. A failing judgement condition
    blocks unless the session is forced."""
    return [
        c.name
        for c in predicate.conditions
        if not c.passed and not (c.overridable and predicate.forced)
    ]


def reclaimable(predicate: Predicate) -> bool:
    """True when nothing blocks: every safety condition passes, and every
    judgement condition passes or the session is forced."""
    return not blocking(predicate)


def reclaim_conditions(
    session_key: dict,
    expected_file_count: int,
    prefix: str = DEFAULT_PREFIX,
) -> Predicate:
    """The seven conditions, evaluated against recorded facts, and whether the session is forced.

    `expected_file_count` is how many files the session's DONE markers name.
    Passed in rather than counted here so this module reads no filesystem:
    every condition below is a question about rows, and a function that also
    walks a directory would be two things.

    Activates `archive` and `timebase` itself -- the same
    `<module>.activate(prefix=prefix)` pattern `cli/report.py::gather_readings`
    already uses for `ingest` -- so a caller only has to know the prefix, not
    which schema modules this predicate happens to query. (Cited by function
    name, not line number, as of 2026-08-27: a line-number citation here
    broke twice in three commits -- corrected to a new wrong line the second
    time, because that very fix added lines above the target. A symbol
    survives edits above it; a line number cannot.)
    """
    from wl_preproc.schema import archive, core, timebase

    archive.activate(prefix=prefix)
    # Activates `core` too (`timebase.activate` -> `core.activate`).
    timebase.activate(prefix=prefix)

    artifact = archive.ArchiveArtifact & session_key
    verifications = archive.ArchiveVerification & session_key
    matched = verifications & "matched = 1"
    # `.to_arrays(attr)`, not `.fetch(attr)`: this venv's DataJoint (2.3.2,
    # matching a comment inside `cli/report.py::gather_readings` -- cited by
    # function name, not line number, for the identical reason as this
    # file's other citation above) warns on EVERY `.fetch()` call, not only
    # the bare, no-attrs form that comment describes --
    # confirmed directly, since the brief this function was drafted from
    # still wrote `.fetch("tier")` and produced a live DeprecationWarning
    # until this module's own test suite surfaced it. `to_arrays` with a
    # single attribute name returns the identical 1-D array `.fetch(attr)`
    # used to, so `tier_rows[0]` / `holds[0]` below are unchanged.
    tier_rows = (timebase.TimingProvenance & session_key).to_arrays("tier")
    holds = (archive.ReclamationHold & session_key).to_arrays(
        "verdict", order_by="held_at DESC", limit=1
    )
    # Systems whose clock fit never landed: `SystemTimebase` writes a row for
    # every attempted system, fitted or not (`schema/timebase.py::
    # SystemTimebase.make`), so a system missing here is one whose key errored,
    # crashed, or has not run yet.
    unfitted = sorted(
        ((core.AcquisitionSystem & session_key) - timebase.SystemTimebase).to_arrays("system")
    )

    forced = bool(len(holds)) and bool(holds[0] == "force")

    return Predicate(
        conditions=(
            Condition(
                "artifact_present",
                bool(artifact),
                "" if artifact else "no ArchiveArtifact row",
            ),
            Condition(
                "every_file_verified",
                len(matched) == expected_file_count and len(matched) > 0,
                f"{len(matched)} of {expected_file_count} files verified",
            ),
            # Safety-kind, so no force clears it. `timebase/extract.py::
            # find_recordings` returns `[]` for a missing directory by design
            # ("device absence never blocks"), so a timebase stage run on a
            # freed session writes `SystemTimebase.fit_status='no_recording'`
            # and `TimingProvenance.tier='D'` -- permanent, silent, and still
            # there after rehydration. So timing is resolved only when the
            # `TimingProvenance` row exists (the daemon's LAST stage,
            # `daemon.py::_computed_tables`) AND every system's clock fit has
            # landed: `TimingProvenance.key_source` is `Session & Ingestion`,
            # so its row -- tier D -- is written even when one system's
            # `SystemTimebase` key failed or crashed, and that leftover key
            # would run later (a job error cleared by hand, or
            # `daemon.reap_stale_jobs` re-pending a crashed reservation) on
            # the absent directory. The tier half is the same query as
            # `not_tier_d` below, which stays judgement.
            Condition(
                "timing_resolved",
                len(tier_rows) == 1 and not unfitted,
                ""
                if len(tier_rows) == 1 and not unfitted
                else (
                    "no tier resolved: the timing stages have not run on this "
                    "session's files, and freeing it now would let them compute "
                    "on an absent directory"
                    if len(tier_rows) != 1
                    else (
                        f"no clock fit for {', '.join(unfitted)}: its timing "
                        "stage failed or has not run, and running it after the "
                        "session is freed would record no recording for a "
                        "device that recorded"
                    )
                ),
                overridable=False,
            ),
            Condition(
                "not_tier_d",
                len(tier_rows) == 1 and tier_rows[0] != "D",
                f"tier {tier_rows[0]}" if len(tier_rows) == 1 else "no tier resolved",
                overridable=True,
            ),
            # Design spec section 5.2, from parent section 8.4's surviving clause:
            # a queued re-sort keeps its fast copy. Both halves are unbuilt --
            # paramset requests reach here in 2b-5; the warm tier has no query of
            # its own yet, and no task named here commits to when it will (fix
            # round: an earlier draft cited "the rehydration plan" as if that
            # were a document -- checked docs/superpowers/specs/ and .../plans/
            # directly, and no such document exists; the design spec treats
            # rehydration as a supported PATH, section 3.3, not a named artifact
            # -- corrected 2026-08-27, Task 10 review: this line said "section
            # 8.4" before, the easy mix-up since 8.4 IS the correct citation two
            # lines up for the paramset/warm-copy clause this comment opens
            # with, but 8.4 never itself mentions rehydration at all)
            # -- so this passes today and gains its query once each half does.
            Condition(
                "no_pending_paramset_or_warm_copy",
                True,
                "no paramset queue exists yet (2b-5); passes vacuously",
                overridable=True,
            ),
            # Fails, unlike the vacuous condition above: the requester's
            # position is that reclamation follows the canonical NWB
            # (2026-09-26 rehydration design, section 0, ruling 3), so the
            # absence of NWB export must block rather than wave through. It
            # gains a real query when Phase 3 writes one.
            Condition(
                "canonical_nwb_present",
                False,
                "NWB export is not built (Phase 3)",
                overridable=True,
            ),
            # Safety-kind: a hold must block. A force clears it by being the
            # LATEST verdict -- `holds` above is ordered `held_at DESC` and
            # limited to one row -- so "latest wins" needs nothing here.
            Condition(
                "no_hold",
                not (len(holds) and holds[0] == "hold"),
                "held" if len(holds) and holds[0] == "hold" else "",
            ),
        ),
        forced=forced,
    )
