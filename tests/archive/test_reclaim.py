"""The reclamation predicate: a named list of conditions, not a verdict.

The first three tests below build `Condition` lists by hand from
`CONDITION_NAMES`, kept from the original brief because they pin
`reclaimable`/`blocking`'s own contract cheaply -- in particular that
`blocking` names EVERY failure, not just the first. But none of the three
ever imports `reclaim_conditions`, so on their own they cannot tell a correct
predicate apart from one that returns four conditions, or the right five
under different names, or the right five in the wrong order (Controller
ruling B). Every test from `test_pins_condition_names_and_order_to_production`
onward inserts real rows and calls `reclaim_conditions` itself, which is what
makes the first three mean anything at all.
"""

from __future__ import annotations

import datetime

import pytest

from wl_preproc.archive.reclaim import Condition, blocking, reclaimable

CONDITION_NAMES = (
    "artifact_present",
    "every_file_verified",
    "not_tier_d",
    "no_pending_paramset_or_warm_copy",
    "no_hold",
)


def _all_passing():
    return [Condition(n, True, "") for n in CONDITION_NAMES]


def test_all_conditions_passing_is_reclaimable():
    assert reclaimable(_all_passing()) is True


def test_each_condition_blocks_on_its_own():
    """Five conditions, five cases. A condition that never fires alone is
    indistinguishable from one that cannot fire at all."""
    for index, name in enumerate(CONDITION_NAMES):
        conditions = _all_passing()
        conditions[index] = Condition(name, False, "failed for the test")
        assert reclaimable(conditions) is False, name
        assert blocking(conditions) == [name]


def test_blocking_names_every_failure_not_just_the_first():
    """The daily report says WHICH condition blocks a session; naming only the
    first would send someone to fix one of several."""
    conditions = _all_passing()
    conditions[0] = Condition(CONDITION_NAMES[0], False, "")
    conditions[2] = Condition(CONDITION_NAMES[2], False, "")
    assert blocking(conditions) == [CONDITION_NAMES[0], CONDITION_NAMES[2]]


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


def _condition(conditions, name):
    """The one condition named `name`, so an assertion about a single
    condition cannot be satisfied by a different one that happens to share
    its `.passed` value (same shape as `tests/cli/test_report.py`'s own
    `_line_for`)."""
    matches = [c for c in conditions if c.name == name]
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

    `TimingProvenance` is `dj.Computed`, and nothing else in this repository
    inserts into one directly -- every other Computed table this suite
    exercises goes through `.populate()`. That recipe exists
    (`tests/schema/test_timebase.py::test_block_disagreement_forces_d_even_
    with_two_agreeing_full_code_records`) and genuinely reaches tier D, but
    only by way of session generation, real event decoding across the
    recipe's `syncbox` and `spikeglx` systems, and a deliberately disagreeing
    `core.Block` row --
    none of which has anything to do with the one comparison this module
    checks (`tier_rows[0] != "D"`). Routing through it would make a failure
    here just as likely to mean "the synthetic recipe changed shape" as "the
    comparison broke". `insert1` supplying every required column reaches the
    identical stored fact -- a `tier` column that reads back `"D"` (or does
    not) -- without that unrelated machinery, confirmed to work because
    `dj.Computed` adds `.populate()`/`.make()` on top of the same
    `Table.insert` every other tier uses rather than replacing it. This is
    the trade Controller ruling D leaves to the implementer's judgment.
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


def _hold(key, *, verdict: str):
    """A `ReclamationHold` row -- "the ONLY place a person appears in this
    subsystem" (`archive.py`'s own docstring on the table)."""
    from wl_preproc.schema import archive

    archive.ReclamationHold.insert1(
        {
            **key,
            "held_at": datetime.datetime(2027, 5, 1, 11, 0),
            "actor": "reviewer",
            "verdict": verdict,
            "reason": "test probe",
        }
    )


def test_pins_condition_names_and_order_to_production(session, prefix):
    """A session set up to pass every condition returns conditions whose
    `.name`s equal `CONDITION_NAMES`, in that order -- the test that makes
    the three hand-built tests above mean something (Controller ruling B
    item 1)."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmall")
    _archive_and_verify(key, n_files=2)
    _timing(key, tier="A")

    conditions = reclaim_conditions(key, expected_file_count=2, prefix=prefix)

    assert [c.name for c in conditions] == list(CONDITION_NAMES)
    assert reclaimable(conditions) is True


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

    conditions = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    assert blocking(conditions) == ["not_tier_d"]
    assert _condition(conditions, "not_tier_d").detail == "tier D"


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

    conditions = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    assert blocking(conditions) == ["no_hold"]
    assert _condition(conditions, "no_hold").detail == "held"


def test_no_timing_provenance_row_reports_no_tier_resolved(session, prefix):
    """A session that landed but whose timing has not been populated yet is a
    real, reachable production state (`TimingProvenance.key_source` is
    sessions with an `Ingestion` row, populated separately) -- `not_tier_d`
    must fail rather than default to passing on absence (Controller ruling D
    item 1). Cheap deliberately: no archive or verification rows either,
    since this test's only claim is about the tier condition's own detail
    string on a bare session."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmnt")

    conditions = reclaim_conditions(key, expected_file_count=0, prefix=prefix)

    not_tier_d = _condition(conditions, "not_tier_d")
    assert not_tier_d.passed is False
    assert not_tier_d.detail == "no tier resolved"


def test_zero_verifications_do_not_vacuously_pass_zero_expected_files(session, prefix):
    """`len(matched) == expected_file_count` alone would pass a session with
    NO archive activity whatsoever whenever a caller happens to pass
    `expected_file_count=0` -- `0 == 0` is `True`. The `and len(matched) > 0`
    clause exists specifically to stop that; without a test pinning it, it
    reads as redundant and is exactly the kind of clause someone deletes
    (Controller ruling B item 3)."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmzv")

    conditions = reclaim_conditions(key, expected_file_count=0, prefix=prefix)

    assert _condition(conditions, "every_file_verified").passed is False


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

    conditions = reclaim_conditions(key, expected_file_count=0, prefix=prefix)

    paramset = _condition(conditions, "no_pending_paramset_or_warm_copy")
    assert paramset.passed is True
    assert "vacuous" in paramset.detail
