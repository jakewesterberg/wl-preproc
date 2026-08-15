"""The schema writes, and the idempotence that stands in for a lock.

There is no lock. `daemon.py` says so in its own docstring — "nothing here
enforces the single-runner invariant ... no lock file, no advisory lock" — and
a watcher invoked from cron inherits that exactly. So every write here is
idempotent by construction, and two watchers racing one directory produce the
same rows in either order.
"""

from __future__ import annotations

import pytest

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.discover import SystemState, discover_topology
from wl_preproc.ingest.landing import (
    QUARANTINE_SESSION_DIR_MAX_LEN,
    QUARANTINE_SUBJECT_MAX_LEN,
    SUBJECT_MAX_LEN,
    _clamp_key,
    already_ingested,
    land_session,
    manifest_session_key,
    quarantine,
)
from wl_preproc.ingest.verify import Integrity
from wl_preproc.schema import core, ingest, pipeline
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


@pytest.fixture(scope="module")
def activated(dj_conn, prefix):
    ingest.activate(prefix=prefix)
    return prefix


@pytest.fixture
def landed(tmp_path, activated):
    """Lands under `subject="landkey"`, not CI_RECIPE's own `"pico"`.

    `tests/schema/test_core.py`'s `a_session` fixture inserts
    `pipeline.Session` at exactly `(subject="pico", session_datetime=
    2027-03-14 09:00:00)` — the same naive value `manifest_session_key`
    derives from CI_RECIPE's manifest, since both ultimately stamp
    `SYNTH_EPOCH`. That module's own `test_rejected_segment_records_why`
    then inserts a `core.AcquisitionSystem` row for `system="rhs"` under
    that identical key.
    Landing this fixture's session under "pico" too would be invisible when
    the full suite happens to collect `tests/ingest/` before `tests/schema/`
    (today's default alphabetical order), but reversed collection —
    `pytest tests/schema tests/ingest`, or any future change to how the suite
    is invoked — lets that "rhs" row leak into
    `test_one_acquisition_system_row_per_system_with_data`'s exact-set
    assertion below and fail it, nondeterministically from this file's own
    point of view. Confirmed live: reversing collection order reproduces
    exactly that failure against the unpatched fixture. A dedicated subject
    keyed to nothing any other test file uses removes the shared key
    entirely, rather than depending on which directory pytest happens to
    visit first.
    """
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, CI_RECIPE.session_id)
    manifest = SessionManifest.from_yaml(layout.manifest_path.read_text()).model_copy(
        update={"subject": "landkey"}
    )
    topology = discover_topology(layout, manifest)
    key = land_session(
        layout, manifest, topology, Integrity.VERIFIED, "abc123", prefix=activated
    )
    return key, layout, manifest, topology, activated


def test_it_creates_the_whole_ancestor_chain(landed):
    key, *_ = landed

    assert len(pipeline.Subject & {"subject": key["subject"]}) == 1
    assert len(pipeline.Session & key) == 1
    assert len(ingest.Ingestion & key) == 1


def test_one_acquisition_system_row_per_system_with_data(landed):
    """`.fetch("system")` is deprecated in DataJoint 2.x (warns
    DeprecationWarning, which this project's zero-warnings suite forbids);
    `.to_arrays("system")` is its documented replacement and returns the same
    single array for one requested attribute."""
    key, _, _, topology, _ = landed
    rows = (core.AcquisitionSystem & key).to_arrays("system")

    assert set(rows) == set(CI_RECIPE.systems)


def test_landing_twice_changes_nothing(landed):
    """The property that stands in for a lock."""
    key, layout, manifest, topology, prefix = landed
    before = len(ingest.Ingestion & key), len(core.AcquisitionSystem & key)

    land_session(layout, manifest, topology, Integrity.VERIFIED, "abc123", prefix=prefix)

    assert (len(ingest.Ingestion & key), len(core.AcquisitionSystem & key)) == before


def test_already_ingested_is_false_before_and_true_after(tmp_path, activated):
    """A subject distinct from every other one this file uses ("landkey",
    "pico", ...) on purpose. The `landed` fixture above is *function-scoped*,
    so several earlier tests in this module already call `land_session` for
    its key before this one runs, against the one shared, session-scoped
    database every test module uses (`tests/conftest.py`'s `dj_conn`).
    Asserting "not yet ingested" against a key some other test may already
    have landed would make the "before" half of this test pass or fail by
    accident of test order rather than by the code under test — precisely the
    kind of check-then-write question this module exists to answer correctly,
    so the test proving it should not itself be order-dependent. See the
    `landed` fixture's own docstring for a concrete case of exactly this
    (reversed collection order leaking a row into a shared key) caught live
    while writing this file.

    `model_copy` leaves every other field (`started_at`, `expected_systems`,
    ...) untouched — pydantic's frozen models still support `model_copy`,
    which builds a new instance rather than mutating the original, and does
    not re-run validators — so this is otherwise the same manifest
    `discover_topology` and `land_session` are already exercised against
    above.
    """
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, CI_RECIPE.session_id)
    manifest = SessionManifest.from_yaml(layout.manifest_path.read_text()).model_copy(
        update={"subject": "keytest"}
    )
    key = manifest_session_key(manifest)

    assert already_ingested(key, prefix=activated) is False

    land_session(
        layout,
        manifest,
        discover_topology(layout, manifest),
        Integrity.VERIFIED,
        "abc123",
        prefix=activated,
    )

    assert already_ingested(key, prefix=activated) is True


def test_the_topology_blob_round_trips_as_a_dict(landed):
    """The blob audit's whole point: a dict must come back a dict. Under a bare
    longblob this would return the string repr of one, silently."""
    key, _, _, topology, _ = landed
    stored = (ingest.Ingestion & key).fetch1("topology")

    assert stored == {system: str(state) for system, state in topology.items()}


def test_quarantine_records_a_directory_with_no_session_key(activated):
    quarantine(
        "/scratch/2027-03-14_99",
        reason="manifest_invalid",
        detail={"error": "truncated"},
        prefix=activated,
    )
    row = (ingest.Quarantine & {"session_dir": "/scratch/2027-03-14_99"}).fetch1()

    assert row["reason"] == "manifest_invalid"
    assert row["subject"] is None


def test_quarantining_twice_updates_rather_than_raising(activated):
    """A directory that fails, is half-fixed, and fails differently must end
    up describing the latest failure — not raise a duplicate-key error that
    stops the whole scan."""
    quarantine("/scratch/2027-03-14_98", reason="manifest_invalid", detail={}, prefix=activated)
    quarantine(
        "/scratch/2027-03-14_98", reason="checksum_mismatch", detail={}, prefix=activated
    )
    row = (ingest.Quarantine & {"session_dir": "/scratch/2027-03-14_98"}).fetch1()

    assert row["reason"] == "checksum_mismatch"


# --- Round 2 review (Task 8, Critical 1) ---------------------------------
#
# Task 8's watcher calls `quarantine` with `subject=manifest.subject` and
# `session_dir=str(session_dir)` from branches that run before its own
# length checks (a schema_version bump reaching a session with an oversized
# subject, for one) — a value that reaches this function has NOT necessarily
# already been checked against the columns it is about to be inserted into.
# `Quarantine.subject : varchar(32)` and `Quarantine.session_dir :
# varchar(255)` (wl_preproc/schema/ingest.py) are enforced here, at the one
# place every quarantine write funnels through, rather than by auditing every
# call site's position relative to a check that could move — which it did,
# in the very same review round that found this (Important 2 reorders
# _evaluate_session's own checks).


def test_quarantine_omits_a_subject_too_long_for_its_own_column(activated):
    """`subject` is not part of `Quarantine`'s key (see the module's own
    docstring: "NOT keyed on (subject, session_datetime)"), so an oversized
    one is OMITTED rather than truncated — the same treatment an
    unparseable manifest's missing subject already gets
    (`test_quarantine_records_a_directory_with_no_session_key` above), for
    the same reason: a truncated value looks like real, if short,
    provenance, and two different oversized subjects sharing a 32-character
    prefix would render identically. Without this fix, the insert below
    raised a raw `pymysql` data-truncation error rather than reaching this
    assertion at all — confirmed while writing this test, by temporarily
    reverting `quarantine`'s clamp.
    """
    oversized = "z" * (QUARANTINE_SUBJECT_MAX_LEN + 8)
    quarantine(
        "/scratch/2027-03-14_97",
        reason="manifest_schema_version",
        detail={},
        subject=oversized,
        prefix=activated,
    )
    row = (ingest.Quarantine & {"session_dir": "/scratch/2027-03-14_97"}).fetch1()

    assert row["subject"] is None


def test_quarantine_truncates_a_session_dir_too_long_for_the_primary_key(activated):
    """`session_dir` IS `Quarantine`'s primary key and cannot be null, so an
    oversized one is shortened rather than omitted — the only structurally
    available option for a value that must be written and cannot fit. A 265-
    character `session_dir` — one already this test's own point, not a
    hypothetical Task 8's review reproduced live — raised the identical raw
    `pymysql` error without this fix. The row is found at the SHORTENED key
    `landing._clamp_key` actually produces (round 4 review: a plain
    `session_dir[:255]` slice, which is what this test originally checked
    against, is no longer what gets stored — see that function's own
    docstring for why a naive slice was the wrong fix), proving the value
    that was actually written, not merely that no exception escaped.

    Round 3 review recorded that the full path used to be recorded NOWHERE
    once truncated, leaving an operator reading this row with a shortened
    key and no way to recover which directory it actually named.
    `detail["untruncated_session_dir"]` is what closes that; checked here
    directly rather than merely inferred from the fix existing.
    """
    oversized_dir = "/scratch/" + ("q" * (QUARANTINE_SESSION_DIR_MAX_LEN + 10))
    quarantine(
        oversized_dir, reason="manifest_invalid", detail={"error": "x"}, prefix=activated
    )

    clamped = _clamp_key(oversized_dir, QUARANTINE_SESSION_DIR_MAX_LEN)
    assert len(clamped) == QUARANTINE_SESSION_DIR_MAX_LEN
    assert clamped != oversized_dir[:QUARANTINE_SESSION_DIR_MAX_LEN], (
        "test setup bug: the digest suffix must actually change the naive-slice result "
        "for this to be testing the round 4 fix rather than the round 3 one"
    )
    row = (ingest.Quarantine & {"session_dir": clamped}).fetch1()
    assert row["reason"] == "manifest_invalid"
    assert row["detail"]["error"] == "x"  # the caller's own detail survives untouched
    assert row["detail"]["untruncated_session_dir"] == oversized_dir
    assert len(ingest.Quarantine & {"session_dir": oversized_dir}) == 0


def test_quarantine_leaves_detail_untouched_when_session_dir_fits(activated):
    """The companion proof: `untruncated_session_dir` must appear ONLY when
    clamping actually happened, not on every call — otherwise the ordinary
    case (the overwhelming majority of quarantines) would carry a redundant,
    always-identical key for no reason.
    """
    quarantine(
        "/scratch/2027-03-14_96", reason="manifest_invalid", detail={"error": "y"}, prefix=activated
    )

    row = (ingest.Quarantine & {"session_dir": "/scratch/2027-03-14_96"}).fetch1()
    assert row["detail"] == {"error": "y"}


def test_quarantine_gives_two_paths_sharing_a_long_prefix_distinct_stored_keys(activated):
    """Round 4 review, Important: the naive `session_dir[:255]` slice round 3
    shipped is made ROUTINE, not pathological, by round 3's OWN
    `session_dir_unrepresentable` check (`wl_preproc/ingest/watcher.py`),
    which fires only for a session sitting under a storage root whose own
    path is already close to or past 255 characters — and EVERY other
    session under that SAME root shares that identical over-long prefix,
    differing only in a short suffix (their own session_id) that a naive
    slice never even reaches. Confirmed directly: two paths sharing a
    290-character common prefix, differing only in their final 14
    characters, produce an IDENTICAL `[:255]` slice.

    Before this fix, `replace=True` silently collapsed both real sessions'
    quarantine records into the ONE row the shared truncated key names,
    with only the last-scanned session's `untruncated_session_dir`
    recoverable — the other's real path gone from the table entirely. Two
    distinct rows, each naming its own real path, is what this test
    requires; `_clamp_key`'s own docstring gives the mechanism (a digest of
    the full path folded into the tail).
    """
    common_prefix = "/deep_shared_root/" + ("x" * 280)
    path_a = common_prefix + "/2027-03-14_01"
    path_b = common_prefix + "/2027-03-14_02"
    assert len(path_a) > QUARANTINE_SESSION_DIR_MAX_LEN
    assert path_a[:QUARANTINE_SESSION_DIR_MAX_LEN] == path_b[:QUARANTINE_SESSION_DIR_MAX_LEN], (
        "test setup bug: the two paths must actually share the full 255-character "
        "naive-slice prefix, or this is not testing the collision case at all"
    )

    quarantine(path_a, reason="manifest_invalid", detail={"which": "a"}, prefix=activated)
    quarantine(path_b, reason="manifest_invalid", detail={"which": "b"}, prefix=activated)

    clamped_a = _clamp_key(path_a, QUARANTINE_SESSION_DIR_MAX_LEN)
    clamped_b = _clamp_key(path_b, QUARANTINE_SESSION_DIR_MAX_LEN)
    assert clamped_a != clamped_b

    row_a = (ingest.Quarantine & {"session_dir": clamped_a}).fetch1()
    row_b = (ingest.Quarantine & {"session_dir": clamped_b}).fetch1()
    assert row_a["detail"] == {"which": "a", "untruncated_session_dir": path_a}
    assert row_b["detail"] == {"which": "b", "untruncated_session_dir": path_b}


# --- Beyond the brief ---------------------------------------------------
#
# Five corrections/additions, each closing a gap the brief's own seven tests
# leave open:
#
# 1. `test_already_ingested_is_false_before_and_true_after` above was already
#    rewritten to use a dedicated subject rather than CI_RECIPE's "pico" — a
#    correctness fix, not an addition, since the brief's original version (a
#    bare `manifest.started_at`-keyed dict against the shared "pico" session
#    every other test in this module also lands) is order-dependent: it only
#    passes because of where it happens to sit in the file relative to the
#    `landed`-fixture tests above it.
#
# 2. The `landed` fixture itself was also rewritten to land under a dedicated
#    subject ("landkey") rather than CI_RECIPE's own "pico", for a sibling
#    reason to (1) one level up: `tests/schema/test_core.py` inserts a real
#    Session and an AcquisitionSystem("rhs") row under `(subject="pico",
#    session_datetime=2027-03-14 09:00:00)` — the exact key CI_RECIPE's own
#    manifest produces. Landing under "pico" here passes under this suite's
#    default collection order (`tests/ingest` sorts before `tests/schema`,
#    alphabetically) purely because `test_core.py` has not run yet by the
#    time this file's tests do — but `pytest tests/schema tests/ingest`
#    reverses that and fails
#    `test_one_acquisition_system_row_per_system_with_data` for real,
#    confirmed live. See the fixture's own docstring.
#
# 3. The task's own instructions single out one property as the one that
#    matters most here — idempotence under a *second* call whose payload
#    genuinely differs from the first, not merely a repeat of it — and ask
#    for it to be verified directly rather than assumed from
#    `skip_duplicates=True`. `test_landing_twice_changes_nothing` above
#    cannot do this: it re-lands with the exact same `topology` object and
#    the same "abc123" hash, so it would pass even if a second, *differing*
#    call silently corrupted the row, as long as an *identical* second call
#    still happened to leave the count alone. The test below re-lands with a
#    different manifest_hash, a different integrity verdict, and a topology
#    naming one more system, and checks content, not just count.
#
# 4. `SUBJECT_MAX_LEN` is a constant this module exports specifically for
#    Task 8 to import and compare a manifest's subject against before ever
#    calling `land_session` (see this module's docstring on the constant).
#    Nothing here pins it against the schema it was measured from, so a
#    future element-animal bump widening or narrowing `subject`'s column
#    could drift silently out of step with it.
#
# 5. `already_ingested` normalizes an aware `session_datetime` defensively
#    (see its docstring) rather than trusting every caller to have built the
#    key through `manifest_session_key`. That fallback's first test
#    (`test_already_ingested_tolerates_an_aware_session_datetime`, since
#    replaced) asserted `already_ingested` found an aware-keyed row under
#    this project's own testcontainers image and called that proof — but
#    that image's session time_zone resolves to UTC, and review found the
#    same assertion passes identically with the normalization removed
#    entirely. `test_already_ingested_survives_a_non_utc_session_time_zone`
#    below replaces it, forcing a non-UTC session tz to exercise the actual
#    risk (see that test's own docstring, and `already_ingested`'s, for the
#    mechanism — DataJoint's dict-restriction keeps an aware value's offset
#    suffix, unlike the insert path, and MySQL resolves it against
#    `@@session.time_zone`).


def test_landing_twice_with_different_content_is_still_idempotent(tmp_path, activated):
    """The property the task exists to prove, made permanent rather than
    checked once by hand and only described in prose.

    A dedicated subject (not CI_RECIPE's "pico") keeps this test's key from
    ever overlapping any other test's in the shared, session-scoped database,
    so "before" below is provably pristine regardless of test order — the
    same reasoning as `test_already_ingested_is_false_before_and_true_after`.

    `Ingestion` freezes at the first call: it has real secondary attributes
    (manifest_hash, integrity, topology, ...) for a second call to disagree
    about, and `skip_duplicates=True` means whichever call reaches the
    database first is the one that sticks. Two watchers racing the same
    directory and computing slightly different results (plausible — a file
    finishing transfer between one scan and the next) must not corrupt the
    row or raise a duplicate-key error; they must converge on one call's
    version, deterministically, which is what is checked directly here by
    content rather than merely by row count.

    `AcquisitionSystem` instead unions: it has no attribute besides its key
    (`system` is the only column beyond the foreign key it hangs off), so
    there is no row for two calls to disagree about the *content* of — a
    second call naming one more system than the first can only add that
    system's row, never overwrite or lose one already there.
    """
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, CI_RECIPE.session_id)
    manifest = SessionManifest.from_yaml(layout.manifest_path.read_text()).model_copy(
        update={"subject": "racekey"}
    )
    topology = discover_topology(layout, manifest)
    assert topology["rhs"] is SystemState.ABSENT  # CI_RECIPE never touches rhs

    key = land_session(
        layout, manifest, topology, Integrity.VERIFIED, "first-hash", prefix=activated
    )

    land_session(
        layout,
        manifest,
        {**topology, "rhs": SystemState.UNDECLARED},
        Integrity.DECLARED_ONLY,
        "second-hash",
        prefix=activated,
    )

    ingestion_row = (ingest.Ingestion & key).fetch1()
    assert ingestion_row["manifest_hash"] == "first-hash"
    assert ingestion_row["integrity"] == "verified"
    assert ingestion_row["topology"]["rhs"] == "absent"

    systems = set((core.AcquisitionSystem & key).to_arrays("system"))
    assert systems == set(CI_RECIPE.systems) | {"rhs"}


def test_subject_max_len_matches_the_declared_column(activated):
    """element-animal declares `subject : varchar(8)`, confirmed by reading
    the installed package directly while implementing this module rather than
    assumed. Pinned here against the live heading so a future element-animal
    bump that changes the column width cannot silently disagree with the
    constant Task 8 imports (`landing.SUBJECT_MAX_LEN`) to decide, before
    ever calling `land_session`, whether a manifest's subject needs to be
    quarantined instead of landed.
    """
    declared = pipeline.Subject.heading["subject"].type

    assert declared == f"varchar({SUBJECT_MAX_LEN})"


def test_already_ingested_survives_a_non_utc_session_time_zone(tmp_path, activated, dj_conn):
    """`already_ingested`'s defensive normalization (see its docstring) exists
    for a narrower, more specific risk than "any aware datetime" — and a first
    version of this test could not tell the difference, which review caught.

    DataJoint's dict-restriction (`table & {...}`) serializes a datetime via
    bare `str()`, which — unlike the insert path — keeps an aware value's
    UTC-offset suffix. MySQL's own literal parser resolves that suffix
    against `@@session.time_zone` before comparing to the naive column. This
    project's own testcontainers image reports session tz `SYSTEM`, which
    resolves to UTC, so a `+00:00` suffix shifts nothing there — an earlier
    version of this test built an aware key and asserted `already_ingested`
    found it, which passed identically whether or not the normalization ran
    at all (confirmed directly: calling the restriction with the
    normalization removed, under this same container, still finds the row
    every time). That is not a test of the normalization; it is a test of
    the container's default tz happening to make the normalization
    unnecessary.

    So this test forces the one condition where the two behaviours actually
    diverge: a session `time_zone` that is not UTC, a real, uncontrolled
    production possibility this module cannot rule out. `dj.conn()` is the
    ONE persistent connection this whole pytest session shares (this
    project's `dj_conn` fixture is session-scoped), so the original tz is
    captured and restored in `finally` — an unrestored `SET time_zone` here
    would leak into every test that runs afterward, in this file and beyond.
    """
    original_tz = dj_conn.query("SELECT @@session.time_zone").fetchone()[0]
    try:
        generate_session(tmp_path, CI_RECIPE)
        layout = SessionLayout(tmp_path, CI_RECIPE.session_id)
        manifest = SessionManifest.from_yaml(layout.manifest_path.read_text()).model_copy(
            update={"subject": "tzkey"}
        )
        land_session(
            layout,
            manifest,
            discover_topology(layout, manifest),
            Integrity.VERIFIED,
            "abc123",
            prefix=activated,
        )

        dj_conn.query("SET time_zone = '+05:00'")
        aware_key = {"subject": manifest.subject, "session_datetime": manifest.started_at}

        assert already_ingested(aware_key, prefix=activated) is True
    finally:
        dj_conn.query(f"SET time_zone = '{original_tz}'")
