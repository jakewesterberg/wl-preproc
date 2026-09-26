"""The reclamation predicate: a named list of conditions, not a verdict.

The hand-built tests below pin `reclaimable`/`blocking`'s own contract
cheaply: that `blocking` names EVERY failure, not just the first, and that a
force clears judgement failures and never safety ones (2026-09-26 rehydration
design, section 2). But none of them imports `reclaim_conditions`, so on their
own they cannot tell a correct predicate apart from one that returns the
wrong names, the wrong order, or the wrong kinds (Controller ruling B).
`test_pins_condition_names_and_order_to_production` and
`test_pins_condition_kinds_to_production` insert real rows and call
`reclaim_conditions` itself, which is what makes the hand-built ones mean
anything at all.
"""

from __future__ import annotations

import datetime

import pytest

from wl_preproc.archive.reclaim import Condition, Predicate, blocking, reclaimable

CONDITION_NAMES = (
    "artifact_present",
    "every_file_verified",
    "timing_resolved",
    "not_tier_d",
    "no_pending_paramset_or_warm_copy",
    "canonical_nwb_present",
    "no_hold",
)

# The judgement conditions -- the ones a force may override. Pinned to
# production by `test_pins_condition_kinds_to_production`, so the hand-built
# predicates below cannot drift from what `reclaim_conditions` returns.
OVERRIDABLE = frozenset(
    {"not_tier_d", "no_pending_paramset_or_warm_copy", "canonical_nwb_present"}
)


def _failing(names=frozenset(), *, forced=False):
    """Every condition, passing unless named in `names`, each carrying its
    production kind."""
    return Predicate(
        tuple(
            Condition(n, n not in names, "", overridable=n in OVERRIDABLE)
            for n in CONDITION_NAMES
        ),
        forced=forced,
    )


def test_all_conditions_passing_is_reclaimable():
    assert reclaimable(_failing()) is True


def test_each_condition_blocks_on_its_own_unless_forced_and_overridable():
    """Seven conditions, each failing alone, forced and not: fourteen cases. A
    condition that never fires alone is indistinguishable from one that
    cannot fire at all, and a force that clears the wrong kind is the one
    mistake this design exists to rule out."""
    for name in CONDITION_NAMES:
        for forced in (False, True):
            predicate = _failing({name}, forced=forced)
            expected = [] if (forced and name in OVERRIDABLE) else [name]
            assert blocking(predicate) == expected, (name, forced)
            assert reclaimable(predicate) is (not expected), (name, forced)


def test_blocking_names_every_failure_not_just_the_first():
    """The daily report says WHICH condition blocks a session; naming only the
    first would send someone to fix one of several."""
    predicate = _failing({CONDITION_NAMES[0], CONDITION_NAMES[2]})
    assert blocking(predicate) == [CONDITION_NAMES[0], CONDITION_NAMES[2]]


def test_a_force_never_clears_a_safety_failure_beside_a_judgement_one():
    predicate = _failing({"artifact_present", "canonical_nwb_present"}, forced=True)
    assert blocking(predicate) == ["artifact_present"]
    assert reclaimable(predicate) is False


def test_an_unclassified_condition_is_safety_by_default():
    """A condition someone adds without stating its kind must fail closed:
    no force can clear it."""
    assert Condition("anything", False, "").overridable is False


# -- Below: real rows, real `reclaim_conditions` calls (Controller ruling B).


@pytest.fixture
def session(dj_conn, prefix):
    """Factory: a bare `pipeline.Session` row under a caller-chosen subject.

    Same reasoning as `tests/cli/test_report.py`'s own `scanned` fixture (see
    its docstring for the full history): `dj_conn`/`prefix` are session-scoped
    (`tests/conftest.py`) and shared by the whole suite, so one fixed subject
    baked in here would let a test in this file collide with a row some other
    test -- in this file or another -- already inserted under it. Every
    caller below names its own subject; `subject` is `varchar(8)`
    (`element_animal.subject.Subject`), so each one stays at or under 8
    characters.

    Activates `archive` and `timebase` rather than `pipeline` directly:
    both already activate `pipeline` themselves (`archive.activate` ->
    `pipeline.activate`; `timebase.activate` -> `core.activate` ->
    `pipeline.activate`), and every caller of this fixture needs all three
    modules' tables bound anyway to insert the rows the test itself wants.
    """
    from wl_preproc.schema import archive, pipeline, timebase

    def _make(subject: str) -> dict:
        archive.activate(prefix=prefix)
        timebase.activate(prefix=prefix)
        pipeline.lab.Lab.insert1(
            {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
            skip_duplicates=True,
        )
        pipeline.subject.Subject.insert1(
            {
                "subject": subject,
                "sex": "M",
                "subject_birth_date": datetime.date(2020, 1, 1),
                "subject_description": "",
            },
            skip_duplicates=True,
        )
        key = {"subject": subject, "session_datetime": datetime.datetime(2027, 5, 1, 9, 0)}
        pipeline.Session.insert1(key, skip_duplicates=True)
        return key

    return _make


def _condition(predicate, name):
    """The one condition named `name`, so an assertion about a single
    condition cannot be satisfied by a different one that happens to share
    its `.passed` value (same shape as `tests/cli/test_report.py`'s own
    `_line_for`)."""
    matches = [c for c in predicate.conditions if c.name == name]
    assert len(matches) == 1, f"expected exactly one condition named {name!r}, got {matches}"
    return matches[0]


def _archive_and_verify(key, *, n_files: int):
    """An `ArchiveArtifact` row plus `n_files` verified `ArchiveVerification`
    children -- satisfies `artifact_present` unconditionally, and satisfies
    `every_file_verified` for a caller who then asks `reclaim_conditions` for
    exactly `n_files` expected files."""
    from wl_preproc.schema import archive

    archive.ArchiveArtifact.insert1(
        {
            **key,
            "archive_host": "vault",
            "archive_share": "cold",
            "archive_path": f"{key['subject']}/session.zarr",
            "codec": "zstd",
            "clevel": 5,
            "compressed_bytes": 1024,
            "manifest_digest": "d" * 64,
            "compressed_at": datetime.datetime(2027, 5, 1, 10, 0),
        }
    )
    for i in range(n_files):
        archive.ArchiveVerification.insert1(
            {
                **key,
                "relative_path": f"file{i}.bin",
                "expected_blake3": f"exp{i}",
                "actual_blake3": f"exp{i}",
                "matched": 1,
                "verified_at": datetime.datetime(2027, 5, 1, 10, 5),
            }
        )


def _timing(key, *, tier: str):
    """A `TimingProvenance` row pinning `tier`, the one field `not_tier_d`
    reads.

    `TimingProvenance` is `dj.Computed`, filled by `.populate()` everywhere
    else THIS test file exercises it -- but a direct `insert1` into an
    auto-populated table is an established pattern in this codebase, not a
    novelty introduced here (fix round: a first draft of this docstring
    claimed the opposite -- "nothing else in this repository inserts into
    one directly" -- which was false, and the false premise traces back to
    Controller ruling D's own framing, taken on trust rather than checked;
    `grep -rn allow_direct_insert` surfaces the counterexamples in seconds).
    `tests/schema/test_core.py::_insert_segment` inserts directly into
    `core.Segment` (also `dj.Computed`) via `allow_direct_insert=True`,
    "asserting what a table STORES, separately from what its `make()`
    decides to store" (that helper's own docstring). Production goes
    further: `wl_preproc/schema/events.py` fills `pipeline.event.Event`,
    `pipeline.trial.Trial`, `.Block` and `.BlockTrial` (all `dj.Imported`,
    confirmed directly against the installed `element_event` package) by
    direct insert unconditionally -- not as a fallback, but because
    `Event.make()` itself raises `NotImplementedError` for these
    element-event tables, so `.populate()` is not an option for them at all.
    That module's own docstring: "element-event's Imported tables are filled
    by direct insert, not by `populate()`."

    The choice made here follows that same precedent, for a narrower reason
    specific to this test. The recipe that reaches tier D honestly
    (`tests/schema/test_timebase.py::test_block_disagreement_forces_d_even_
    with_two_agreeing_full_code_records`) pulls in session generation, real
    event decoding across the recipe's `syncbox` and `spikeglx` systems, and
    a deliberately disagreeing `core.Block` row -- none of which has
    anything to do with the one comparison this module checks
    (`tier_rows[0] != "D"`). Routing through it would make a failure here
    just as likely to mean "the synthetic recipe changed shape" as "the
    comparison broke". `insert1` supplying every required column reaches the
    identical stored fact -- a `tier` column that reads back `"D"` (or does
    not) -- without that unrelated machinery.
    """
    from wl_preproc.schema import timebase

    timebase.TimingProvenance.insert1(
        {
            **key,
            "tier": tier,
            "n_barcodes_emitted": 100,
            "n_systems_aligned": 1,
            "n_segments": 1,
            "n_rejected_segments": 0,
            "worst_residual_us": 1.0,
            "worst_drift_ppm": 0.5,
            "pending_inputs": "",
            "n_full_code_records": 1,
            "n_strobe_witnesses": 0,
            "decode_errors": 0,
        },
        # DataJoint refuses a direct insert into an auto-populated table
        # (`dj.Computed`) without this -- confirmed directly: the first run of
        # this test raised `DataJointError: Inserts into an auto-populated
        # table can only be done inside its make method during a populate
        # call.` This helper's own docstring is the record of why a direct
        # insert is the right call here anyway rather than routing through
        # `.populate()`.
        allow_direct_insert=True,
    )


def _hold(key, *, verdict: str, hour: int = 11):
    """A `ReclamationHold` row -- "the ONLY place a person appears in this
    subsystem" (`archive.py`'s own docstring on the table). `hour` orders
    several verdicts for one session: the predicate reads only the latest."""
    from wl_preproc.schema import archive

    archive.ReclamationHold.insert1(
        {
            **key,
            "held_at": datetime.datetime(2027, 5, 1, hour, 0),
            "actor": "reviewer",
            "verdict": verdict,
            "reason": "test probe",
        }
    )


def test_pins_condition_names_and_order_to_production(session, prefix):
    """A session set up to pass every condition that CAN pass today returns
    conditions whose `.name`s equal `CONDITION_NAMES`, in that order -- and
    is blocked by `canonical_nwb_present` alone, because NWB export does not
    exist yet (2026-09-26 rehydration design, section 0 ruling 3)."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmall")
    _archive_and_verify(key, n_files=2)
    _timing(key, tier="A")

    predicate = reclaim_conditions(key, expected_file_count=2, prefix=prefix)

    assert [c.name for c in predicate.conditions] == list(CONDITION_NAMES)
    assert predicate.forced is False
    assert blocking(predicate) == ["canonical_nwb_present"]


def test_tier_d_blocks_reclaim_from_a_real_row(session, prefix):
    """`not_tier_d` fails, and is the ONLY condition that fails, when every
    other condition is satisfied and `TimingProvenance.tier` is genuinely
    `'D'` -- proving the `!= "D"` comparison runs against a stored value, not
    merely that a hand-built `Condition(..., False, ...)` propagates
    correctly through `blocking` (Controller rulings B item 2 and D item 2)."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmtd")
    _archive_and_verify(key, n_files=1)
    _timing(key, tier="D")

    predicate = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    assert blocking(predicate) == ["not_tier_d", "canonical_nwb_present"]
    assert _condition(predicate, "not_tier_d").detail == "tier D"


def test_a_hold_blocks_reclaim_from_a_real_row(session, prefix):
    """`no_hold` fails, and is the ONLY condition that fails, when every other
    condition is satisfied and a genuine `ReclamationHold` row records
    `verdict='hold'`.

    Not on Controller ruling B's "at minimum" list, but added after probing
    found a real gap it left open: every other test in this file leaves
    `ReclamationHold` empty for its session, so `holds` is always `[]` and
    `not (len(holds) and ...)` is always `True` regardless of what the
    right-hand side of that `and` even says. Hardcoding `no_hold` to `True`
    outright left all eight other tests in this file green (confirmed: `-m
    pytest tests/archive/test_reclaim.py` on that mutation reported "1
    failed, 8 passed", this test the one failure) -- the identical "fires
    alone, or cannot fire at all" gap `test_each_condition_blocks_on_its_
    own`'s own docstring names for the hand-built list, reappearing one layer
    down in the tests that call real `reclaim_conditions`."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmhld")
    _archive_and_verify(key, n_files=1)
    _timing(key, tier="A")
    _hold(key, verdict="hold")

    predicate = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    assert blocking(predicate) == ["canonical_nwb_present", "no_hold"]
    assert _condition(predicate, "no_hold").detail == "held"


def test_pins_condition_kinds_to_production(session, prefix):
    """Which conditions a force may override is decided in production, not in
    this file's `OVERRIDABLE` -- this is what keeps the two equal."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmknd")

    predicate = reclaim_conditions(key, expected_file_count=0, prefix=prefix)

    assert {c.name for c in predicate.conditions if c.overridable} == OVERRIDABLE
    # Named on its own as well: "timing not yet computed" was reclassified
    # from judgement to safety by the whole-branch review, and the set
    # comparison above would read the same if the condition vanished.
    assert _condition(predicate, "timing_resolved").overridable is False


def test_canonical_nwb_present_fails_until_phase_3(session, prefix):
    """Ruling 3: reclamation follows the canonical NWB, and NWB export is not
    built. Pinned on the detail string, so that wiring the real query in
    Phase 3 breaks this test -- the reminder to update what a reader of the
    report sees."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmnwb")

    predicate = reclaim_conditions(key, expected_file_count=0, prefix=prefix)

    nwb = _condition(predicate, "canonical_nwb_present")
    assert nwb.passed is False
    assert nwb.overridable is True
    assert nwb.detail == "NWB export is not built (Phase 3)"


def test_a_force_overrides_tier_d_and_the_missing_nwb(session, prefix):
    """The archival design's section 5.3 promised "a force that overrides"
    and never said what. Ruling 4: judgement conditions."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmfrc")
    _archive_and_verify(key, n_files=1)
    _timing(key, tier="D")
    _hold(key, verdict="force")

    predicate = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    assert predicate.forced is True
    assert _condition(predicate, "no_hold").passed is True
    assert blocking(predicate) == []
    assert reclaimable(predicate) is True


def test_a_force_does_not_override_a_missing_artifact(session, prefix):
    """Ruling 4's other half: a force changes whether a session is READY,
    never whether its archive exists."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmfna")
    _timing(key, tier="A")
    _hold(key, verdict="force")

    predicate = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    assert blocking(predicate) == ["artifact_present", "every_file_verified"]
    assert reclaimable(predicate) is False


def test_a_hold_after_a_force_blocks(session, prefix):
    """Latest verdict wins, in both directions."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmhaf")
    _archive_and_verify(key, n_files=1)
    _timing(key, tier="A")
    _hold(key, verdict="force", hour=11)
    _hold(key, verdict="hold", hour=12)

    predicate = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    assert predicate.forced is False
    assert blocking(predicate) == ["canonical_nwb_present", "no_hold"]


def test_a_force_after_a_hold_clears_it(session, prefix):
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmfah")
    _archive_and_verify(key, n_files=1)
    _timing(key, tier="A")
    _hold(key, verdict="hold", hour=11)
    _hold(key, verdict="force", hour=12)

    predicate = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    assert predicate.forced is True
    assert reclaimable(predicate) is True


def test_no_timing_provenance_row_reports_no_tier_resolved(session, prefix):
    """A session that landed but whose timing has not been populated yet is a
    real, reachable production state (`TimingProvenance.key_source` is
    sessions with an `Ingestion` row, populated separately) -- `not_tier_d`
    must fail rather than default to passing on absence (Controller ruling D
    item 1), and so must `timing_resolved`, the safety condition added for
    the same absence by the whole-branch review. Cheap deliberately: no
    archive or verification rows either, since this test's only claims are
    about the two timing conditions' own detail strings on a bare session."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmnt")

    predicate = reclaim_conditions(key, expected_file_count=0, prefix=prefix)

    not_tier_d = _condition(predicate, "not_tier_d")
    assert not_tier_d.passed is False
    assert not_tier_d.detail == "no tier resolved"
    timing = _condition(predicate, "timing_resolved")
    assert timing.passed is False
    assert timing.detail.startswith("no tier resolved: the timing stages have not run")


def test_a_force_does_not_free_a_session_whose_timing_has_not_run(session, prefix):
    """The whole-branch review's Critical finding. A timebase stage run on a
    freed session sees no directory, reads that as "no recording"
    (`timebase/extract.py::find_recordings`), and writes a permanent tier D --
    so "timing not yet computed" is safety, and a force must not clear it,
    even though it clears `not_tier_d`'s failure on the identical absence
    beside it, and the missing NWB."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmntf")
    _archive_and_verify(key, n_files=1)
    _hold(key, verdict="force")

    predicate = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    assert predicate.forced is True
    assert blocking(predicate) == ["timing_resolved"]
    assert reclaimable(predicate) is False


def _system(key, system: str, *, fitted: bool):
    """An `AcquisitionSystem` row and, when `fitted`, the `SystemTimebase` row
    its clock-fit stage writes. `fitted=False` is the state a failed or
    crashed `SystemTimebase` key leaves: the device is known, its fit never
    landed. A direct insert into the `dj.Computed` table for the reason
    `_timing`'s docstring gives. `fit_status='no_recording'` because it is the
    truth for a factory session, which has no files -- and because
    `core.Segment.key_source` never reads such a row, a later module's
    `daemon.run_once()` is not handed a Segment key to fail on (this file's
    sessions have no `Ingestion` row to read a directory from)."""
    from wl_preproc.schema import core, timebase

    core.AcquisitionSystem.insert1({**key, "system": system})
    if fitted:
        timebase.SystemTimebase.insert1(
            {
                **key,
                "system": system,
                "fit_status": "no_recording",
                "time_source": "barcode",
                "n_barcodes_decoded": 0,
                "n_barcodes_matched": 0,
            },
            allow_direct_insert=True,
        )


def test_a_system_without_a_clock_fit_blocks_even_with_a_tier(session, prefix):
    """The residual the fix wave's re-review reproduced end to end.
    `TimingProvenance.key_source` is `Session & Ingestion`, so its row -- tier
    D -- is written even when one system's `SystemTimebase` key errored or its
    worker crashed. A routine force clears `not_tier_d`; freed then, the
    leftover key runs later (a job error cleared by hand, or
    `daemon.reap_stale_jobs` re-pending a crashed reservation) on an absent
    directory and records `no_recording` for a device that recorded --
    permanent, surviving rehydration. So a `TimingProvenance` row alone does
    not resolve timing; every system's clock fit must have landed."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmsys")
    _archive_and_verify(key, n_files=1)
    _timing(key, tier="D")
    _system(key, "syncbox", fitted=True)
    _system(key, "bcam", fitted=False)
    _hold(key, verdict="force")

    predicate = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    timing = _condition(predicate, "timing_resolved")
    assert timing.passed is False
    assert "bcam" in timing.detail
    assert "syncbox" not in timing.detail
    assert blocking(predicate) == ["timing_resolved"]
    assert reclaimable(predicate) is False


def test_every_system_fitted_resolves_timing(session, prefix):
    """The other side: a `TimingProvenance` row plus a clock fit for every
    system the session has -- fitted or not, since `SystemTimebase` writes a
    row for every attempted system -- resolves timing."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmsyf")
    _archive_and_verify(key, n_files=1)
    _timing(key, tier="A")
    _system(key, "syncbox", fitted=True)
    _system(key, "bcam", fitted=True)

    predicate = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    assert _condition(predicate, "timing_resolved").passed is True


def test_zero_verifications_do_not_vacuously_pass_zero_expected_files(session, prefix):
    """`len(matched) == expected_file_count` alone would pass a session with
    NO archive activity whatsoever whenever a caller happens to pass
    `expected_file_count=0` -- `0 == 0` is `True`. The `and len(matched) > 0`
    clause exists specifically to stop that; without a test pinning it, it
    reads as redundant and is exactly the kind of clause someone deletes
    (Controller ruling B item 3)."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmzv")

    predicate = reclaim_conditions(key, expected_file_count=0, prefix=prefix)

    assert _condition(predicate, "every_file_verified").passed is False


def test_the_vacuous_condition_says_so_in_its_own_detail(session, prefix):
    """`no_pending_paramset_or_warm_copy` is hardcoded `True` today -- design
    spec section 5.2's deliberate incompleteness (both halves of the real
    query, paramset requests and the warm tier, are unbuilt), not an
    oversight -- and that is only honest if a reader of the daily report can
    SEE it stated, not merely infer it from a passing condition that looks
    identical to a genuinely evaluated one. Pinned on the word itself so that
    wiring the real query in 2b-5 breaks this test, which is the reminder to
    update the sentence a reader will see (Controller ruling C)."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmvac")

    predicate = reclaim_conditions(key, expected_file_count=0, prefix=prefix)

    paramset = _condition(predicate, "no_pending_paramset_or_warm_copy")
    assert paramset.passed is True
    assert "vacuous" in paramset.detail
