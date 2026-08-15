"""scan_once: one pass over a storage root.

The two manifest checks that exist nowhere else are here — schema_version, which
has been declared and never compared since 1c-1, and session_id agreeing with
the directory it sits in.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest

from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.watcher import Outcome, scan_once
from wl_preproc.schema import ingest
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


@pytest.fixture
def root(tmp_path, dj_conn, prefix):
    ingest.activate(prefix=prefix)
    generate_session(tmp_path, CI_RECIPE)
    return tmp_path, prefix, str(SessionLayout(tmp_path, CI_RECIPE.session_id).dir)


def _use_dedicated_subject(tmp_path: Path, subject: str) -> None:
    """Rewrite CI_RECIPE's manifest to land under `subject` instead of "pico".

    Every test in this file shares one MySQL database under one prefix —
    `tests/conftest.py`'s `dj_conn` and `prefix` are both session-scoped, with
    no truncation between tests — so any test that actually lands a session
    must not do so under CI_RECIPE's own `subject="pico"`, `session_datetime=
    2027-03-14 09:00:00` (both ultimately stamped from `SYNTH_EPOCH`).

    Two independent collisions make that true, not one:

    - `tests/schema/test_core.py`'s `a_session` fixture inserts a real
      `pipeline.Session` — and, from several of that module's own tests, a
      `core.AcquisitionSystem` row — at exactly that key. Under this suite's
      default collection order (`tests/ingest` sorts before `tests/schema`)
      that fixture has not run yet by the time this file's tests do, so
      landing under "pico" here would appear to work — but `pytest
      tests/schema tests/ingest` reverses that, and a session landed here
      under "pico" would then be the one polluting `test_core.py`'s own
      assertions instead. This is the exact hazard named for Task 8: give
      every test that lands a session a dedicated subject, not shared with
      any fixture elsewhere in the suite. `tests/ingest/test_landing.py`'s
      `landed` fixture hit the identical collision first and fixed it the
      same way (a dedicated `subject="landkey"`); this mirrors that.
    - Within *this* file, `test_a_good_session_ingests` and
      `test_scanning_twice_reports_already_the_second_time` both land a
      session by default. Two tests landing under the same "pico" key would
      make whichever runs second see `already_ingested() == True` on its
      *first* scan_once call too — not a crash, but a test that would only
      ever prove what it claims by accident of file order. A dedicated
      subject per test removes that dependency as well.

    `subject` must be at most `landing.SUBJECT_MAX_LEN` (8) characters, or
    the manifest fails the `subject_unrepresentable` check before ever
    reaching the code this is meant to exercise.
    """
    manifest_path = SessionLayout(tmp_path, CI_RECIPE.session_id).manifest_path
    manifest_path.write_text(
        manifest_path.read_text().replace(
            f"subject: {CI_RECIPE.subject}", f"subject: {subject}"
        )
    )


def test_a_good_session_ingests(root):
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "goodkey")

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.INGESTED


def test_scanning_twice_reports_already_the_second_time(root):
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "twicekey")
    scan_once(tmp_path, prefix=prefix)

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.ALREADY


def test_an_incomplete_session_is_not_ingested(root):
    tmp_path, prefix, session_dir = root
    SessionLayout(tmp_path, CI_RECIPE.session_id).done_marker("spikeglx").unlink()

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.INCOMPLETE
    # Scoped to this test's own session_dir, not a bare `len(ingest.Ingestion())
    # == 0`: `tmp_path` is unique per test, so this restriction can only ever
    # match a row this test itself landed, regardless of what any other test —
    # in this file or another, run before or after — has already landed into
    # the same shared, session-scoped table. A global count would happen to
    # read 0 under today's default collection order (this test runs before
    # any subject-"pico"-avoiding landing test could plausibly pollute it) but
    # is not actually testing the claim this test makes.
    assert len(ingest.Ingestion & {"session_dir": session_dir}) == 0


def test_an_incomplete_and_quiet_session_reports_stalled(root):
    tmp_path, prefix, session_dir = root
    SessionLayout(tmp_path, CI_RECIPE.session_id).done_marker("spikeglx").unlink()
    later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=3)

    result = scan_once(tmp_path, prefix=prefix, now=later)

    assert result.outcomes[session_dir] is Outcome.STALLED
    assert len(ingest.Ingestion & {"session_dir": session_dir}) == 0


def test_a_future_schema_version_quarantines(root):
    """Declared since 1c-1 and never once compared against SCHEMA_VERSION. A
    manifest claiming version 7 parses cleanly today."""
    tmp_path, prefix, session_dir = root
    manifest_path = SessionLayout(tmp_path, CI_RECIPE.session_id).manifest_path
    manifest_path.write_text(
        manifest_path.read_text().replace("schema_version: 1", "schema_version: 7")
    )

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    assert (ingest.Quarantine & {"session_dir": session_dir}).fetch1(
        "reason"
    ) == "manifest_schema_version"


def test_a_subject_name_too_long_for_element_animal_quarantines(root):
    """element-animal declares `subject : varchar(8)` while the manifest's
    subject is an unconstrained str, so "Wilhelmina" validates cleanly and then
    fails at the insert. Caught here as a manifest problem rather than surfacing
    as a MySQL error halfway through landing."""
    tmp_path, prefix, session_dir = root
    manifest_path = SessionLayout(tmp_path, CI_RECIPE.session_id).manifest_path
    manifest_path.write_text(
        manifest_path.read_text().replace(
            f"subject: {CI_RECIPE.subject}", "subject: Wilhelmina"
        )
    )

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    row = (ingest.Quarantine & {"session_dir": session_dir}).fetch1()
    assert row["reason"] == "subject_unrepresentable"
    assert row["detail"]["subject"] == "Wilhelmina"


def test_a_session_id_disagreeing_with_its_directory_quarantines(root):
    """Silently trusting either one files the session under a wrong identity."""
    tmp_path, prefix, session_dir = root
    manifest_path = SessionLayout(tmp_path, CI_RECIPE.session_id).manifest_path
    manifest_path.write_text(
        manifest_path.read_text().replace(str(CI_RECIPE.session_id), "2027-03-14_09")
    )

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    assert (ingest.Quarantine & {"session_dir": session_dir}).fetch1(
        "reason"
    ) == "session_id_mismatch"


def test_an_unparseable_manifest_quarantines_with_no_session_key(root):
    """The case Quarantine's path key exists for."""
    tmp_path, prefix, session_dir = root
    SessionLayout(tmp_path, CI_RECIPE.session_id).manifest_path.write_text("{{{ nope")

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    row = (ingest.Quarantine & {"session_dir": session_dir}).fetch1()
    assert row["reason"] == "manifest_invalid"
    assert row["subject"] is None


def test_a_corrupted_file_quarantines_as_checksum_mismatch(root):
    """A dedicated subject: unlike the manifest-level rejections above (schema
    version, subject length, session_id mismatch, an unparseable file), which
    all quarantine at a check `_scan_one` runs BEFORE `already_ingested` is
    ever consulted, a checksum mismatch is only found by `verify_session`,
    which runs AFTER it. This test's own outcome therefore depends on
    "pico" not already being ingested — a dependency on file-wide state this
    file was just corrected away from elsewhere (see
    `test_a_directory_without_a_manifest_is_ignored_entirely`'s docstring),
    so it gets the same fix here rather than staying merely-currently-safe.
    """
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "sumkey")
    target = (
        SessionLayout(tmp_path, CI_RECIPE.session_id).system_dir("spikeglx")
        / f"{CI_RECIPE.session_id}_imec0.ap.meta"
    )
    target.write_bytes(target.read_bytes() + b"extra")

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    assert (ingest.Quarantine & {"session_dir": session_dir}).fetch1(
        "reason"
    ) == "checksum_mismatch"


def test_a_directory_without_a_manifest_is_ignored_entirely(root):
    """Not every directory under a storage root is a session. A scratch folder
    must not become a quarantine row.

    A dedicated subject even though this test's own assertion never mentions
    one: nothing here breaks the CI_RECIPE session's completeness or
    validity, so `scan_once` lands it under "pico" as an unexamined side
    effect of this test running at all — found empirically while adding
    `test_paramset_registration_contention_defers_rather_than_quarantining`
    below, which failed with `ALREADY` instead of `DEFERRED` because this
    test, several tests earlier, had already landed "pico" without anyone
    checking for it. Every other test in this file that leaves the session
    complete and valid either lands deliberately under its own dedicated
    subject or corrupts something first so it cannot land at all; this was
    the one that did neither.
    """
    tmp_path, prefix, _ = root
    _use_dedicated_subject(tmp_path, "ignorkey")
    (tmp_path / "some_scratch_dir").mkdir()

    result = scan_once(tmp_path, prefix=prefix)

    assert str(tmp_path / "some_scratch_dir") not in result.outcomes


def test_nothing_ever_writes_a_request_row(root):
    """Spec section 2: the watcher never calls submit(). Request.origin='ingest'
    stays reserved and unused, and this is what makes that a fact rather than
    an intention.

    Before/after counts, not a bare `== 0`: `request.Request` and
    `request.Activation` are shared, session-scoped tables, and
    `tests/schema/test_request.py` writes many real rows into both under this
    same prefix via `submit()`. Under the default collection order those rows
    do not exist yet when this test runs, so `== 0` would happen to hold
    either way — but `pytest tests/schema tests/ingest` runs that file first
    and populates both tables, which a literal `== 0` here would then fail for
    a reason unrelated to what this test actually claims. What must hold
    regardless of what any other file already wrote is that *this scan* adds
    nothing, so the count is captured before and compared after.
    """
    from wl_preproc.schema import request

    tmp_path, prefix, _ = root
    _use_dedicated_subject(tmp_path, "noreqkey")
    request.activate(prefix=prefix)
    before = len(request.Request()), len(request.Activation())

    scan_once(tmp_path, prefix=prefix)

    assert (len(request.Request()), len(request.Activation())) == before


# --- Beyond the brief -----------------------------------------------------
#
# Five tests below, proving three of Task 8's mandated deviations from the
# brief's own reference implementation (the unguarded `_candidate_dirs`, the
# manifest read sitting outside its own try, and `register_session_params`'s
# uncaught `dj.DataJointError`) rather than merely asserting the happy path
# the brief's own eleven tests above already cover. The fourth deviation —
# building the session key through `landing.manifest_session_key` instead of
# inline — has no dedicated test of its own: every test above that lands and
# then re-scans (`test_scanning_twice_reports_already_the_second_time`) or
# checks `already_ingested` indirectly already exercises the one key-building
# path there is, since `_scan_one` has no second, inline way left to diverge
# from it.


def test_a_permission_fault_on_one_sibling_does_not_crash_the_whole_scan(root):
    """`_candidate_dirs`'s per-child check used to be unguarded:
    `child.is_dir()` and `(child / MANIFEST_FILENAME).is_file()` swallow
    ENOENT/ENOTDIR/EBADF/ELOOP on their own but not EACCES, so a permission
    fault on one session directory re-raised there — and because this feeds a
    dict comprehension in `scan_once`, one bad entry killed the scan for
    every other session under the same root, the worst blast radius anywhere
    in this phase.

    A real `os.chmod`, not a `Path` monkeypatch, per this task's own
    instructions: on Python 3.13, `glob.py` captures `os.scandir` as a
    staticmethod at import, so a patch aimed at `Path` is not guaranteed to
    sit on whichever internal code path `iterdir()` actually executes.
    Permissions are restored in `finally`, before any assertion.

    Confirmed empirically before writing this test (not assumed): chmod
    0o000 on a child directory leaves `child.is_dir()` succeeding — stat()
    only needs search permission on the *parent* — and it is
    `(child / MANIFEST_FILENAME).is_file()` that actually raises, since
    resolving a path *inside* the blocked directory needs search permission
    on it. Either way, both calls sit in the same guarded block.
    """
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "sibkey")
    blocked = tmp_path / "unreadable_sibling"
    blocked.mkdir()
    original_mode = blocked.stat().st_mode
    os.chmod(blocked, 0o000)
    try:
        result = scan_once(tmp_path, prefix=prefix)
    finally:
        os.chmod(blocked, original_mode)

    assert result.outcomes[session_dir] is Outcome.INGESTED
    assert str(blocked) not in result.outcomes


def test_an_unreadable_root_yields_an_empty_scan_rather_than_raising(root):
    """`root.iterdir()` is a raw passthrough that swallows nothing on its own
    — confirmed empirically to behave differently across this project's two
    supported interpreters: on 3.11 it is a generator (the call itself never
    raises; the fault surfaces on the first `next()`), on 3.13 the call
    itself raises immediately for an unreadable root. `_candidate_dirs`
    guards both shapes, so a fault on `root` itself yields an empty result
    rather than raising out of `scan_once` — the honest answer for "nothing
    could be listed this pass," not a crash. The CI_RECIPE session `root`
    already generated is never reached at all here, by construction: nothing
    under an unlistable root can be found, so nothing lands and no dedicated
    subject is needed.
    """
    tmp_path, prefix, _ = root
    original_mode = tmp_path.stat().st_mode
    os.chmod(tmp_path, 0o000)
    try:
        result = scan_once(tmp_path, prefix=prefix)
    finally:
        os.chmod(tmp_path, original_mode)

    assert result.outcomes == {}


def test_a_manifest_with_invalid_utf8_bytes_quarantines_rather_than_crashing(root):
    """The manifest's `read_text()` used to sit outside the guard that
    catches parse failures, so bytes that fail to even decode as UTF-8 raised
    before `SessionManifest.from_yaml` was ever reached — the identical
    defect already fixed in `sentinel.py`'s `read_marker`, which this test
    mirrors (`test_a_marker_with_invalid_utf8_bytes_is_invalid_rather_than_
    crashing` in `test_sentinel.py`) applied to the session manifest instead
    of a DONE marker.
    """
    tmp_path, prefix, session_dir = root
    manifest_path = SessionLayout(tmp_path, CI_RECIPE.session_id).manifest_path
    manifest_path.write_bytes(manifest_path.read_bytes() + b"\xff\x80\xfe")

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    row = (ingest.Quarantine & {"session_dir": session_dir}).fetch1()
    assert row["reason"] == "manifest_invalid"
    assert row["subject"] is None


def test_an_unreadable_manifest_quarantines_rather_than_raising(root):
    """A permissions fault reading the manifest file itself — not just a
    malformed one — must quarantine rather than crash the scan. Chmod targets
    the *file*, not its parent directory: `_candidate_dirs`'s own
    `(child / MANIFEST_FILENAME).is_file()` check only needs search
    permission on the session directory, not read permission on the manifest
    itself, so the file is still correctly found as a candidate — this
    isolates the guard around `read_text()` specifically, the same
    distinction `tests/ingest/test_params.py`'s own unreadable-file test
    draws for `session_params.yaml`. Permissions are restored in `finally`,
    before any assertion.
    """
    tmp_path, prefix, session_dir = root
    manifest_path = SessionLayout(tmp_path, CI_RECIPE.session_id).manifest_path
    original_mode = manifest_path.stat().st_mode
    os.chmod(manifest_path, 0o000)
    try:
        result = scan_once(tmp_path, prefix=prefix)
    finally:
        os.chmod(manifest_path, original_mode)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    row = (ingest.Quarantine & {"session_dir": session_dir}).fetch1()
    assert row["reason"] == "manifest_invalid"


def test_paramset_registration_contention_defers_rather_than_quarantining(root, monkeypatch):
    """`register_session_params` raises a bare `dj.DataJointError` — not the
    `ValueError` every other rejection here raises — when
    `paramset.register`'s bounded retry loop (`_MAX_REGISTER_ATTEMPTS`, in
    `wl_preproc/schema/paramset.py`) exhausts every attempt to allocate a
    fresh `paramset_idx` under real concurrent registration. That is a
    database contention condition, not a defect in this session's own
    `session_params.yaml`, so it must become neither a `params_invalid`
    quarantine (which would blame the file) nor an uncaught exception (which
    would abort the rest of the scan) — it defers this one session for the
    next `scan_once` call to retry, exactly like `Outcome.DEFERRED`'s own
    docstring says.

    Every attempt is made to collide for real, the same technique
    `tests/schema/test_paramset.py`'s own
    `test_register_recovers_from_a_concurrent_index_collision` uses for its
    single-collision case: intercepting `_insert_new`, the one write
    `register()` uses to claim a fresh index, and inserting a genuine
    competing row under the SAME index just before `register()`'s own insert
    — except here every attempt collides, not just the first, so the retry
    loop is driven to genuine exhaustion rather than a single recovered race.
    Each collision is a real MySQL primary-key violation (a real 1062,
    translated to `dj.errors.DuplicateError` by DataJoint), not a fabricated
    exception; only the repeated interception across all
    `_MAX_REGISTER_ATTEMPTS` tries is synthetic, standing in for that many
    genuinely unlucky racing writers rather than one.

    A dedicated subject, even though this path structurally never reaches
    `land_session` (the forced `DataJointError` fires before it): reaching
    `register_session_params` at all still requires `already_ingested()` to
    say no first, which depends on nothing else in the whole file having
    landed "pico" — a dependency this test hit for real while it was being
    written, when `test_a_directory_without_a_manifest_is_ignored_entirely`,
    several tests above, turned out to land "pico" as an unexamined side
    effect (see that test's own docstring). Fixing that one test closes the
    hole this time, but coupling this test's correctness to "no other test in
    this file, now or later, ever lands pico" is fragile in exactly the way
    the rest of this file was just corrected away from — so this session
    lands under its own key too, like every other test here that needs
    `already_ingested()` to start out false.
    """
    from wl_preproc.schema import paramset

    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "defrkey")
    (SessionLayout(tmp_path, CI_RECIPE.session_id).dir / "session_params.yaml").write_text(
        "paramset_type: contention-probe\nparams:\n  probe: true\n"
    )
    paramset.activate(prefix=prefix)
    real_insert_new = paramset._insert_new
    calls = {"n": 0}

    def always_collide(row):
        calls["n"] += 1
        winner = {
            **row,
            "param_hash": paramset.content_hash({"drift": f"winner-{calls['n']}"}),
            "params": {"drift": f"winner-{calls['n']}"},
        }
        real_insert_new(winner)  # claims this attempt's idx first, for real
        return real_insert_new(row)  # collides for real, on every attempt

    monkeypatch.setattr(paramset, "_insert_new", always_collide)

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.DEFERRED
    assert calls["n"] == 10, "expected every one of _MAX_REGISTER_ATTEMPTS to collide"
    assert len(ingest.Quarantine & {"session_dir": session_dir}) == 0
