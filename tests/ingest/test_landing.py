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
    SUBJECT_MAX_LEN,
    already_ingested,
    land_session,
    quarantine,
    session_key,
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
    2027-03-14 09:00:00)` — the same naive value `session_key` derives from
    CI_RECIPE's manifest, since both ultimately stamp `SYNTH_EPOCH`. That
    module's own `test_rejected_segment_records_why` then inserts a
    `core.AcquisitionSystem` row for `system="rhs"` under that identical key.
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
    key = session_key(manifest)

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
#    key through `session_key`. That fallback has no test of its own above.


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


def test_already_ingested_tolerates_an_aware_session_datetime(tmp_path, activated):
    """`already_ingested`'s own defensive normalization (see its docstring):
    a caller that built its key from a manifest's aware `started_at` directly
    — skipping `session_key`/`to_naive_utc` — must still match what
    `land_session` actually wrote, rather than silently never matching and
    re-ingesting the same session on every scan.
    """
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, CI_RECIPE.session_id)
    manifest = SessionManifest.from_yaml(layout.manifest_path.read_text()).model_copy(
        update={"subject": "awarekey"}
    )
    land_session(
        layout,
        manifest,
        discover_topology(layout, manifest),
        Integrity.VERIFIED,
        "abc123",
        prefix=activated,
    )

    aware_key = {"subject": manifest.subject, "session_datetime": manifest.started_at}

    assert already_ingested(aware_key, prefix=activated) is True
