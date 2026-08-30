"""When the scratch copy may go.

**A named list, not a boolean.** Design spec section 5.2: each condition records
why it passed or failed, so the daily report can say WHICH one blocks a session
rather than that one does. Section 8.5 requires the gate be surfaced there
"since an ungated session is what will eventually fill scratch", and only a
named list makes that report actionable.

**Incomplete today, and it says so.** The predicate can currently see only
timing quality: tier says nothing about whether a sort is good, because section
6.5's unit QC metrics are 2b-6 and unbuilt, and the canonical NWB is Phase 3.
Both join this list when they land. Writing it as a growing list makes the
current incompleteness visible instead of implying the rule is finished.
"""

from __future__ import annotations

from dataclasses import dataclass

from wl_preproc.schema import DEFAULT_PREFIX


@dataclass(frozen=True, slots=True)
class Condition:
    name: str
    passed: bool
    detail: str


def reclaimable(conditions: list[Condition]) -> bool:
    """True only when every condition passes."""
    return all(c.passed for c in conditions)


def blocking(conditions: list[Condition]) -> list[str]:
    """Every failing condition's name, in order -- not merely the first."""
    return [c.name for c in conditions if not c.passed]


def reclaim_conditions(
    session_key: dict,
    expected_file_count: int,
    prefix: str = DEFAULT_PREFIX,
) -> list[Condition]:
    """The five conditions, each evaluated against recorded facts.

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
    from wl_preproc.schema import archive, timebase

    archive.activate(prefix=prefix)
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

    return [
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
        Condition(
            "not_tier_d",
            len(tier_rows) == 1 and tier_rows[0] != "D",
            f"tier {tier_rows[0]}" if len(tier_rows) == 1 else "no tier resolved",
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
        ),
        Condition(
            "no_hold",
            not (len(holds) and holds[0] == "hold"),
            "held" if len(holds) and holds[0] == "hold" else "",
        ),
    ]
