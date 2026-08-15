"""scan_once: one pass over a storage root.

The two manifest checks that exist nowhere else are here — schema_version, which
has been declared and never compared since 1c-1, and session_id agreeing with
the directory it sits in.
"""

from __future__ import annotations

import datetime
import os
import shutil
from pathlib import Path

import pytest

from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest import landing
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
    manifest claiming version 7 parses cleanly today.

    A dedicated subject: `manifest_schema_version` runs AFTER
    `already_ingested` (deliberately -- see `_evaluate_session`'s own
    docstring on why that check and `subject_unrepresentable`, unlike
    `session_id_mismatch`, stay below it), so this test's own outcome
    depends on "pico" not already being ingested, the same dependency
    `test_a_corrupted_file_quarantines_as_checksum_mismatch` already carries
    and is fixed the same way.
    """
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "futrekey")
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
    """Silently trusting either one files the session under a wrong identity.

    No dedicated subject needed, unlike its neighbors above and below:
    `session_id_mismatch` runs BEFORE `already_ingested` (round 3 review --
    see `_evaluate_session`'s own docstring for why, and
    `test_an_already_landed_identity_with_a_mismatched_directory_still_
    quarantines` below for the regression this specifically closes), so this
    test's outcome cannot depend on "pico"'s landed state regardless of
    which subject the manifest declares.
    """
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


def test_an_already_landed_identity_with_a_mismatched_directory_still_quarantines(root):
    """Round 3 review, Critical: round 2's fix moved `already_ingested` to
    run FIRST, immediately after the parse -- correct for `manifest_schema_
    version` and `subject_unrepresentable` (see `_evaluate_session`'s own
    docstring), wrong for `session_id_mismatch`, since moved back below it.
    `already_ingested` keys on `(subject, session_datetime)` alone, never on
    the directory, so a directory whose manifest declares an ALREADY-LANDED
    identity used to return ALREADY before `session_id_mismatch` ever ran --
    silently disabling the one check that exists specifically to catch a
    renamed or duplicated session directory.

    A genuine revert-control, not a hypothetical: land a session normally,
    then rewrite that SAME manifest's `session_id` to no longer match the
    directory it sits in, leaving `subject`/`started_at` untouched so
    `already_ingested`'s key is unaffected, and rescan. With `session_id_
    mismatch` running before `already_ingested` (this fix), this quarantines
    correctly; under round 2's order the identical probe silently returned
    ALREADY instead, with zero Quarantine rows written -- confirmed live by
    reverting just this ordering and rerunning this exact test.
    """
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "revctrl")
    scan_once(tmp_path, prefix=prefix)  # lands under session_id "2027-03-14_01"

    manifest_path = SessionLayout(tmp_path, CI_RECIPE.session_id).manifest_path
    manifest_path.write_text(
        manifest_path.read_text().replace(str(CI_RECIPE.session_id), "2027-03-14_09")
    )

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    row = (ingest.Quarantine & {"session_dir": session_dir}).fetch1()
    assert row["reason"] == "session_id_mismatch"


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
    """A dedicated subject: `verify_session`, where a checksum mismatch is
    found, runs well after `already_ingested` -- and, as of round 3 review,
    that is no longer true of every manifest-level rejection uniformly.
    `session_id_mismatch` (and the newer `session_dir_unrepresentable`) run
    BEFORE `already_ingested`, since neither depends on a global that can
    drift after landing; `manifest_schema_version` and
    `subject_unrepresentable` stay AFTER it, deliberately, since both do
    (see `_evaluate_session`'s own docstring for the full reasoning). This
    test's own check runs later still, so it depends on "pico" not already
    being ingested regardless -- a dependency on file-wide state this file
    was corrected away from elsewhere (see
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
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "ignorkey")
    (tmp_path / "some_scratch_dir").mkdir()

    result = scan_once(tmp_path, prefix=prefix)

    # Round 2 review (Important 4): asserting only that the scratch dir is
    # absent leaves this test vacuous against a mutation that breaks the
    # REAL session's own outcome (e.g. a _scan_one that always returns
    # INCOMPLETE right after the parse, never reaching topology, verification,
    # params, or landing) -- it would still pass, since it never checks what
    # actually happened to the one directory that IS a session. Asserting
    # INGESTED closes that.
    assert result.outcomes[session_dir] is Outcome.INGESTED
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

    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "noreqkey")
    request.activate(prefix=prefix)
    before = len(request.Request()), len(request.Activation())

    result = scan_once(tmp_path, prefix=prefix)

    # Round 2 review (Important 4): this test was vacuous against a mutation
    # making _scan_one return INCOMPLETE immediately after the parse, before
    # topology, verification, params, or landing ever run -- the row counts
    # below would still hold (trivially, since nothing ran), so nothing here
    # actually proved the watcher reached the code path that would call
    # submit() if it were ever going to. Asserting INGESTED first proves this
    # scan did the whole job land_session does, not none of it.
    assert result.outcomes[session_dir] is Outcome.INGESTED
    assert (len(request.Request()), len(request.Activation())) == before


# --- Beyond the brief -----------------------------------------------------
#
# Five tests below, proving three of Task 8's mandated deviations from the
# brief's own reference implementation (the unguarded `_candidate_dirs`, the
# manifest read sitting outside its own try, and `register_session_params`
# raising a bare `dj.DataJointError` on contention exhaustion, LEFT uncaught
# by `params.py`, and needing a catch at the watcher layer -- narrowed by
# round 2 review to the dedicated `paramset.ContentionExhausted` this file's
# own tests catch by name below, not the bare `dj.DataJointError` root this
# comment originally, and now stale-ly, named) rather than merely asserting
# the happy path the brief's own eleven tests above already cover.
#
# The fourth deviation — building the session key through
# `landing.manifest_session_key` instead of inline — was claimed here, in an
# earlier version of this comment, to have "no dedicated test of its own"
# because `already_ingested`'s own defensive normalization makes the two
# implementations behaviourally indistinguishable. Round 2 review (Important
# 5) confirmed the behavioural half of that but corrected the conclusion: a
# STRUCTURAL test can still tell them apart, and does, in the "Round 2"
# section below (`test_the_session_key_check_goes_through_landings_own_
# helper`). The claim that the property was untestable was simply wrong; the
# reasoning for why a behavioural test could not see it was right.


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

    `result.root_error is None` (added in round 2 review, Important 7) is
    the companion half of the proof: this is a PER-CHILD fault, not a
    root-level one, and the two must not be confused with each other --
    `root_error` exists specifically to distinguish "root itself is fine,
    one entry under it is not" (this test) from "root itself could not be
    read" (the next test).
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
    assert result.root_error is None


def test_an_unreadable_root_is_reported_distinctly_not_as_an_empty_scan(root):
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

    Round 2 review (Important 7): an empty `outcomes` dict alone does not
    distinguish this from a root that is genuinely empty -- `wlpp ingest
    --root /typo/path` and `--root <empty dir>` were byte-identical before
    `ScanResult.root_error` existed: exit 0, no output either way, silently
    reporting success for a typo, an unmounted NAS, or an ACL slip on the
    storage root. `root_error` is what a caller (the CLI, see
    `test_cli_ingest_reports_a_root_fault_and_exits_non_zero` below) checks
    to tell the two apart; see the companion test right after this one for
    the genuinely-empty case that must NOT set it.
    """
    tmp_path, prefix, _ = root
    original_mode = tmp_path.stat().st_mode
    os.chmod(tmp_path, 0o000)
    try:
        result = scan_once(tmp_path, prefix=prefix)
    finally:
        os.chmod(tmp_path, original_mode)

    assert result.outcomes == {}
    assert result.root_error is not None


def test_a_genuinely_empty_root_has_no_root_error(tmp_path, dj_conn, prefix):
    """The companion proof Important 7 asks for: an empty-but-perfectly-
    readable root must not ALSO look like a root-level fault, or every
    quiet night with nothing new to ingest would wrongly report an error.
    Deliberately does not use the `root` fixture -- that fixture generates a
    real CI_RECIPE session, and this test needs a root with nothing in it at
    all.
    """
    ingest.activate(prefix=prefix)

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes == {}
    assert result.root_error is None


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
    """`register_session_params` raises `paramset.ContentionExhausted` — not
    the `ValueError` every other rejection here raises, and (since round 2
    review) not the bare `dj.DataJointError` this docstring originally said
    either; see `test_a_datajoint_error_that_is_not_contention_quarantines_
    not_defers` below for exactly why that was narrowed — when
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
    `land_session` (the forced `ContentionExhausted` fires before it): reaching
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


# --- Round 2 review ---------------------------------------------------------
#
# Two Criticals, closed together because they share one mandate: "the
# mandate told you to close a blast-radius hole at three named sites — where
# else does that reasoning reach?" It reaches `_scan_one` itself (Critical 1
# — no outer exception boundary around per-session processing) and the
# specific catch that produces DEFERRED (Critical 2 — `except
# dj.DataJointError` is DataJoint's entire error tree, not just the one
# transient condition it was meant for). Plus all seven Important findings
# and the two named Minors.


def test_a_datajoint_error_that_is_not_contention_quarantines_not_defers(root, monkeypatch):
    """Critical 2: `except dj.DataJointError` would also swallow
    `AccessError`, `MissingTableError`, `IntegrityError`, `QuerySyntaxError`,
    `DuplicateError` — everything in DataJoint's error tree, since
    `DataJointError` is its root (confirmed against the installed 2.3.2
    package: `dj.DataJointError.__mro__` is `(DataJointError, Exception,
    BaseException, object)`).

    Proven with a permanent condition a bare-`DataJointError` catch cannot
    distinguish from genuine, self-clearing contention:
    `paramset.register` itself raises a plain `dj.DataJointError` that is
    NOT `ContentionExhausted`. This must quarantine (`unexpected_failure`),
    not defer — silently deferring a permanent condition forever, with no
    `Quarantine` row and no `Ingestion` row, means the session is visible in
    NO section of any report built from these tables: not Ingested (no row),
    not Quarantined (no row), not Stalled (`is_stalled` short-circuits False
    because the session genuinely is complete). It would just retry forever,
    invisibly, on every poll.
    """
    import datajoint as dj

    from wl_preproc.schema import paramset

    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "permkey")
    (SessionLayout(tmp_path, CI_RECIPE.session_id).dir / "session_params.yaml").write_text(
        "paramset_type: perm-fail-probe\nparams:\n  probe: true\n"
    )
    paramset.activate(prefix=prefix)

    def always_fail(paramset_type, params):
        raise dj.DataJointError("simulated permanent condition, not contention")

    monkeypatch.setattr(paramset, "register", always_fail)

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    row = (ingest.Quarantine & {"session_dir": session_dir}).fetch1()
    assert row["reason"] == "unexpected_failure"
    assert row["detail"]["error_type"] == "DataJointError"


def test_a_mixed_key_type_params_file_quarantines_the_one_session_not_its_sibling(root):
    """Critical 1(a), UPDATED by round 3 review's Important 4: `params:\\n
    0: 0.5\\n  gain: 2` — a per-channel setting written beside a named one —
    passes `SessionParams` validation cleanly, because `params` is a bare,
    deliberately unconstrained `dict` (see `SessionParams`'s own docstring).
    It then reaches `paramset.register`'s `content_hash`, whose
    `json.dumps(params, sort_keys=True)` cannot sort a dict with both int
    and str keys — confirmed directly, not assumed:
    `json.dumps({0: 0.5, "gain": 2}, sort_keys=True)` raises `TypeError:
    "'<' not supported between instances of 'str' and 'int'"`.

    At round 2, nothing caught this before `paramset.register` itself, so it
    escaped `_evaluate_session` entirely and reached `_scan_one`'s outer
    boundary, quarantining as the generic `unexpected_failure`. Round 3's
    Important 4 fix (`register_session_params` now calls
    `paramset.content_hash(declared.params)` itself, proactively, as part of
    its own validation) catches this SAME `TypeError` earlier and more
    specifically — mixed int/str keys are just as unserializable as a
    YAML-typed `datetime.date` (Important 4's own example), and both go
    through the identical `json.dumps` call — so this now quarantines as
    `params_invalid`, a strictly more informative outcome than before. The
    sibling-survives proof this test was written for is unaffected either
    way, so it stays: a genuine second, healthy session generated fresh (not
    copied) right next to the broken one, via `CI_RECIPE.model_copy` with a
    different `session_id` and a dedicated `subject` — both stamped
    automatically by `generate_session`/`write_manifest`, so the sibling's
    directory name and its own manifest's `session_id` agree by
    construction, with no separate fix-up needed. See the standalone outer-
    boundary proof below
    (`test_a_genuinely_unclassified_failure_still_reaches_the_outer_
    boundary`) for a scenario that does NOT depend on which specific checks
    happen to exist today.
    """
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "badkey")
    (SessionLayout(tmp_path, CI_RECIPE.session_id).dir / "session_params.yaml").write_text(
        "paramset_type: clustering\nparams:\n  0: 0.5\n  gain: 2\n"
    )

    sibling_recipe = CI_RECIPE.model_copy(
        update={"session_id": "2027-03-14_02", "subject": "goodsib"}
    )
    generate_session(tmp_path, sibling_recipe)
    sibling_dir = str(SessionLayout(tmp_path, sibling_recipe.session_id).dir)

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    row = (ingest.Quarantine & {"session_dir": session_dir}).fetch1()
    assert row["reason"] == "params_invalid"

    assert result.outcomes[sibling_dir] is Outcome.INGESTED


def test_a_genuinely_unclassified_failure_still_reaches_the_outer_boundary(root, monkeypatch):
    """The outer boundary's own proof, independent of any specific check:
    round 2's original proof for this
    (`test_a_mixed_key_type_params_file_quarantines_the_one_session_not_its_
    sibling`, above) stopped proving it once round 3's Important 4 fix gave
    that scenario a more specific home (`params_invalid`) -- exactly the
    kind of thing that keeps happening as this module grows more named,
    specific checks over time. A test for "the boundary catches whatever is
    LEFT OVER" needs a failure injected somewhere no check could ever name,
    not a scenario that might migrate to a specific reason next round too.
    `landing.land_session` is monkeypatched directly to raise a synthetic,
    clearly-labelled RuntimeError -- not a real failure mode this module
    anticipates at all, which is the point.

    A healthy sibling, added in round 4 review: `_scan_one`'s own docstring
    gives BLAST RADIUS -- one session's unclassified failure must not cost
    any OTHER session in the same scan -- as the reason this boundary exists
    at all, and this test, before this fix, proved only that the boundary
    catches SOMETHING, not that it protects anyone else while doing it.
    Confirmed live: narrowing the boundary's own `except Exception` clause
    to something that does not match (e.g. `except ZeroDivisionError`) fails
    every single-session boundary test in this file trivially -- `scan_once`
    itself raises uncaught, crashing the test -- but says nothing about
    blast radius, since none of them have a second session whose survival
    is even checked. `land_session` is faked to fail ONLY for the targeted
    subject and to delegate to the real implementation for anyone else, so
    the sibling lands for real, through the real code path, not a second
    fake.
    """
    from wl_preproc.ingest import landing as landing_module

    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "unclassk")

    sibling_recipe = CI_RECIPE.model_copy(
        update={"session_id": "2027-03-14_05", "subject": "boundsib"}
    )
    generate_session(tmp_path, sibling_recipe)
    sibling_dir = str(SessionLayout(tmp_path, sibling_recipe.session_id).dir)

    real_land_session = landing_module.land_session

    def boom_for_the_target_only(layout, manifest, *args, **kwargs):
        if manifest.subject == "unclassk":
            raise RuntimeError("simulated: nobody anticipated this")
        return real_land_session(layout, manifest, *args, **kwargs)

    monkeypatch.setattr(landing_module, "land_session", boom_for_the_target_only)

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    row = (ingest.Quarantine & {"session_dir": session_dir}).fetch1()
    assert row["reason"] == "unexpected_failure"
    assert row["detail"]["error_type"] == "RuntimeError"
    assert "nobody anticipated" in row["detail"]["error"]

    assert result.outcomes[sibling_dir] is Outcome.INGESTED


def test_an_overlong_paramset_type_quarantines_as_params_invalid(root):
    """Critical 1(b): the identical shape as `subject_unrepresentable`, one
    layer down. `ParamSet.paramset_type : varchar(32)`
    (`wl_preproc/schema/paramset.py`); `SessionParams.paramset_type` is an
    unconstrained `str`, so a longer value validates cleanly there and would
    otherwise fail only at `paramset.register`'s own insert, as a raw
    `pymysql.err.DataError` `_evaluate_session` does not name or expect.
    Closed at source in `params.py`, against `paramset.PARAMSET_TYPE_MAX_LEN`
    — read directly from `ParamSet`'s own declaration, not assumed.
    """
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "longtype")
    overlong = "x" * 40
    (SessionLayout(tmp_path, CI_RECIPE.session_id).dir / "session_params.yaml").write_text(
        f"paramset_type: {overlong}\nparams:\n  n_blocks: 4\n"
    )

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    row = (ingest.Quarantine & {"session_dir": session_dir}).fetch1()
    assert row["reason"] == "params_invalid"
    assert overlong in row["detail"]["error"]


def test_an_oversized_subject_with_a_bad_schema_version_does_not_crash_the_scan(root):
    """Critical 1(c): the `manifest_schema_version` branch passes
    `subject=manifest.subject` to `quarantine()` unconditionally, and runs
    BEFORE the subject-length check (`landing.SUBJECT_MAX_LEN`, 8) —  which
    rejects a subject over 8 characters, narrower than the 32-character
    limit `Quarantine.subject` itself declares. A subject between 9 and 32
    characters combined with a bad `schema_version` was already handled
    correctly (quarantined as `manifest_schema_version`, subject recorded);
    this proves the boundary above THAT — a subject over Quarantine's OWN
    32-character column, reaching `quarantine()` before element-animal's
    narrower 8-character check ever gets a chance to run — no longer raises
    a raw `pymysql.err.DataError` out of the `manifest_schema_version`
    branch. `row["subject"] is None`, not a 32-character truncation: see
    `landing.quarantine`'s own docstring for why an oversized subject is
    omitted rather than truncated (a truncated value looks like real,
    if short, provenance, and this table is not keyed on it either way).
    """
    tmp_path, prefix, session_dir = root
    oversized = "y" * 40
    manifest_path = SessionLayout(tmp_path, CI_RECIPE.session_id).manifest_path
    text = manifest_path.read_text()
    text = text.replace(f"subject: {CI_RECIPE.subject}", f"subject: {oversized}")
    text = text.replace("schema_version: 1", "schema_version: 7")
    manifest_path.write_text(text)

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    row = (ingest.Quarantine & {"session_dir": session_dir}).fetch1()
    assert row["reason"] == "manifest_schema_version"
    assert row["subject"] is None
    assert row["detail"]["declared"] == 7


def test_an_already_ingested_session_is_a_no_op_even_after_a_schema_version_bump(root):
    """Important 2: design section 8.3 says a scan over an already-ingested
    session must be a no-op. `already_ingested` used to run AFTER
    schema_version/subject-length/session_id, so a session that had already
    landed under an OLDER `SCHEMA_VERSION` wrote a fresh
    `manifest_schema_version` Quarantine row on every subsequent poll after
    a version bump, while its `Ingestion` row stayed — the same session
    listed under both Ingested and Quarantined, contradicting section 9.
    `already_ingested` now runs immediately after `session_id_mismatch` --
    the one check that genuinely belongs before it, since it reads only the
    directory's own basename, not a global that can drift -- and before
    every check that DOES read one (`schema_version`, subject length, and,
    since round 4 review, `session_dir` length too). Deliberately not
    pinned to an ordinal position ("the second check", "the third check"):
    that phrasing has already gone stale twice as checks were added and
    reordered around it, which is exactly what
    `_evaluate_session`'s own docstring exists to describe precisely instead.
    """
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "orderky")
    scan_once(tmp_path, prefix=prefix)  # lands under today's SCHEMA_VERSION

    manifest_path = SessionLayout(tmp_path, CI_RECIPE.session_id).manifest_path
    manifest_path.write_text(
        manifest_path.read_text().replace("schema_version: 1", "schema_version: 7")
    )

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.ALREADY
    assert len(ingest.Quarantine & {"session_dir": session_dir}) == 0


def test_disabling_verify_lands_with_integrity_skipped(root):
    """Important 3: `--no-verify` (`verify=False`) had zero coverage —
    surviving mutant: forcing `enabled=True` regardless of the `verify`
    parameter."""
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "noverky")

    result = scan_once(tmp_path, prefix=prefix, verify=False)

    assert result.outcomes[session_dir] is Outcome.INGESTED
    row = (ingest.Ingestion & {"session_dir": session_dir}).fetch1()
    assert row["integrity"] == "skipped"


def test_enabling_verify_lands_with_integrity_verified(root):
    """Important 3, the other direction — pins that `verify=True` (the
    default) still actually verifies, so the test above proves a real
    behavioural difference rather than `verify_session` simply always
    returning the same thing."""
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "verkey")

    result = scan_once(tmp_path, prefix=prefix, verify=True)

    assert result.outcomes[session_dir] is Outcome.INGESTED
    row = (ingest.Ingestion & {"session_dir": session_dir}).fetch1()
    assert row["integrity"] == "verified"


def test_cli_no_verify_flag_flows_through_to_skipped_integrity(root):
    """Important 3's other surviving mutant: inverting the CLI's own
    `verify=not args.no_verify` translation (`wl_preproc/cli/main.py`) is
    invisible to the two tests above, since they call `scan_once` directly
    and know nothing about `--no-verify`. Calls `main()` in-process, not via
    `subprocess.run` — the pattern every other CLI test in this suite that
    needs no live database uses — specifically so this reuses the `dj_conn`
    fixture's already-configured `dj.config` rather than needing to plumb
    connection info into a child process, which no CLI test in this project
    does today. `main()` itself never calls `sys.exit`; argparse's own
    `SystemExit` is caught internally and turned into a return value, so this
    is safe to call directly without ending the test process.
    """
    from wl_preproc.cli.main import main

    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "clivfky")

    exit_code = main(["ingest", "--root", str(tmp_path), "--prefix", prefix, "--no-verify"])

    assert exit_code == 0
    row = (ingest.Ingestion & {"session_dir": session_dir}).fetch1()
    assert row["integrity"] == "skipped"


def test_the_session_key_check_goes_through_landings_own_helper(root, monkeypatch):
    """Important 5, correcting this file's own earlier claim (see the
    updated comment above `test_a_permission_fault_on_one_sibling_does_not_
    crash_the_whole_scan`): deviation 4 built `session_key` inline in the
    brief (`{"subject": ..., "session_datetime": manifest.started_at}`);
    this watcher instead calls `landing.manifest_session_key`, so the key
    checked here and the key `land_session` writes with cannot independently
    diverge. `already_ingested` normalizes defensively on its own, so a
    BEHAVIOURAL test genuinely cannot tell the two implementations apart —
    confirmed directly, matching `test_already_ingested_survives_a_non_utc_
    session_time_zone`'s own technique of forcing a non-UTC session
    `time_zone`, both implementations still find the row.

    What CAN tell them apart is structural: spy on `manifest_session_key`
    itself during the SECOND scan specifically — the one that returns
    ALREADY and therefore never reaches `land_session`, which calls the same
    helper a second time on its own during the first, landing scan. Exactly
    one call during that second scan can only come from `_evaluate_session`'s
    own check; zero would mean it reverted to building the key inline.
    """
    from wl_preproc.ingest import landing

    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "spykey")
    scan_once(tmp_path, prefix=prefix)  # first scan: lands (helper called twice)

    real_key_fn = landing.manifest_session_key
    calls = {"n": 0}

    def spy(manifest):
        calls["n"] += 1
        return real_key_fn(manifest)

    monkeypatch.setattr(landing, "manifest_session_key", spy)

    result = scan_once(tmp_path, prefix=prefix)  # second scan: ALREADY

    assert result.outcomes[session_dir] is Outcome.ALREADY
    assert calls["n"] == 1


def test_an_invalid_params_file_quarantines_at_the_watcher_level(root):
    """Important 6: `params_invalid` had no watcher-level test — only
    `params.py`'s own unit-level tests exercise `register_session_params`
    directly. This proves the reason is actually reachable end to end
    through `scan_once`, using the same envelope-level typo
    `tests/ingest/test_params.py::test_an_unknown_top_level_key_is_rejected`
    exercises at the function level.
    """
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "pinvkey")
    (SessionLayout(tmp_path, CI_RECIPE.session_id).dir / "session_params.yaml").write_text(
        "paramset_type: clustering\nparams:\n  n_blocks: 4\nnblocks: 4\n"
    )

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    row = (ingest.Quarantine & {"session_dir": session_dir}).fetch1()
    assert row["reason"] == "params_invalid"


def test_a_relative_root_still_records_an_absolute_session_dir(root):
    """Minor: a relative `--root` used to record a `session_dir` with no
    directory information in it at all — `scan_once(Path("."))` stored just
    `"2027-03-14_01"` — and that string is `Quarantine`'s own PRIMARY KEY, so
    the identical session scanned from two different working directories
    would collect two different rows. `_candidate_dirs` now resolves `root`
    before walking it. Proven by calling with a bare relative `Path(".")`
    from inside `tmp_path` and confirming the ABSOLUTE `session_dir` this
    test already has from the fixture is the key `scan_once` actually used —
    if resolution did not happen, `result.outcomes[session_dir]` would raise
    `KeyError`, not merely disagree.
    """
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "relkey")

    original_cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        result = scan_once(Path("."), prefix=prefix)
    finally:
        os.chdir(original_cwd)

    assert result.outcomes[session_dir] is Outcome.INGESTED


def test_a_naive_now_does_not_crash_the_scan(root):
    """Minor: `is_stalled` subtracts from `last_change_at`'s always-aware
    return, so a naive `now` raised `TypeError` deep inside it before this
    fix — `landing`'s own `_now` tolerates a naive value fine (`to_naive_utc`
    returns it unchanged, since a naive value is already what DataJoint's
    columns store), but `scan_once` did not normalize before using `now` for
    this one in-memory comparison, which genuinely needs an aware value.

    The naive value is built from a UTC-sourced instant with its tzinfo
    stripped (`datetime.now(UTC).replace(tzinfo=None)`), not
    `datetime.now()` (naive local time): coercing a naive value to UTC via
    `.replace(tzinfo=UTC)` (matching `scan_once`'s own fix — see its
    docstring for why not `.astimezone()`) reconstructs the intended instant
    correctly only if the naive value's wall-clock numbers were already
    UTC's; built from local time instead, the test's own correctness would
    depend on the machine's local timezone offset, which is exactly the kind
    of environment-dependent flakiness this suite avoids elsewhere.
    """
    tmp_path, prefix, session_dir = root
    SessionLayout(tmp_path, CI_RECIPE.session_id).done_marker("spikeglx").unlink()
    naive_later = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + datetime.timedelta(
        hours=3
    )
    assert naive_later.tzinfo is None

    result = scan_once(tmp_path, prefix=prefix, now=naive_later)

    assert result.outcomes[session_dir] is Outcome.STALLED


def test_cli_ingest_reports_a_root_fault_and_exits_non_zero(tmp_path, dj_conn, prefix):
    """Important 7, at the CLI boundary: `scan_once` reporting `root_error`
    is only useful if something actually surfaces it. `wlpp ingest --root
    /typo/path` used to exit 0 with no output, identical to a genuinely
    empty root — this proves the fix prints the fault and exits non-zero
    instead. In-process `main()` call, not subprocess, for the same
    dj.config-reuse reason `test_cli_no_verify_flag_flows_through_to_
    skipped_integrity` above gives.
    """
    from wl_preproc.cli.main import main

    ingest.activate(prefix=prefix)
    unreadable = tmp_path / "nope"
    unreadable.mkdir()
    original_mode = unreadable.stat().st_mode
    os.chmod(unreadable, 0o000)
    try:
        exit_code = main(["ingest", "--root", str(unreadable), "--prefix", prefix])
    finally:
        os.chmod(unreadable, original_mode)

    assert exit_code == 1


# --- Round 3 review ----------------------------------------------------
#
# Two Criticals (the two remaining after round 2's boundary and narrowed
# catch both held under fresh attack: a real `DROP TABLE ...quarantine` and a
# double-fault case where the inner handler's own quarantine raises too),
# one review-owned mistake in round 2's own I2 fix, and the seven Important/
# also-fix items that came with them.


def test_a_session_dir_over_the_ingestion_column_limit_quarantines_cleanly(
    tmp_path, dj_conn, prefix
):
    """Critical: `Ingestion.session_dir : varchar(255)`
    (`wl_preproc/schema/ingest.py`) had no check at source, unlike
    `Quarantine.session_dir` (the identical width, but a primary key
    `landing.quarantine()` already clamps defensively -- see that
    function's own docstring). A session at a path over 255 characters --
    reachable by a deep enough storage-root layout, not a hypothetical --
    used to reach `land_session`'s own insert, raise a raw
    `pymysql.err.DataError`, get caught only by `_scan_one`'s generic outer
    boundary, and quarantine as `unexpected_failure` FOREVER: `session_dir`
    does not shorten between polls, and `already_ingested` can never say
    True for a session that has never once landed, so nothing about a later
    scan would ever change the outcome. A completely valid, complete
    session parked permanently behind an unclassified reason. Fixed with a
    dedicated, source-level check (`landing.INGESTION_SESSION_DIR_MAX_LEN`)
    and its own named reason, the identical precedent
    `subject_unrepresentable` and `PARAMSET_TYPE_MAX_LEN` already set.

    Constructs a GENUINE `session_dir` over 255 characters via a deeply
    nested root, not a synthetic string: `SessionId`'s own format is short
    and fixed, so the only way to make the FULL PATH long is to nest the
    root deeply -- which `generate_session`/`SessionLayout` are fully
    agnostic to; nothing else in this pipeline assumes a shallow storage
    root.

    Unaffected by round 4 review moving this check's position (below
    `already_ingested` now, not alongside `session_id_mismatch` -- see
    `_evaluate_session`'s own docstring): this session has never landed, so
    `already_ingested` returns False regardless and this check is reached
    either way. See `test_a_session_dir_too_long_after_the_root_moved_is_
    still_a_no_op` below for the case that DOES depend on the reordering --
    an already-landed session whose root moves.
    """
    ingest.activate(prefix=prefix)
    deep_root = tmp_path
    for _ in range(20):
        if len(str(deep_root / CI_RECIPE.session_id)) > 255:
            break
        deep_root = deep_root / ("nested_dir_" + "x" * 30)
    else:
        pytest.fail("could not construct a session_dir over 255 characters")

    generate_session(deep_root, CI_RECIPE)
    session_dir = str(SessionLayout(deep_root, CI_RECIPE.session_id).dir)
    assert len(session_dir) > 255

    result = scan_once(deep_root, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    # The stored key is landing._clamp_key(session_dir, ...), not a naive
    # session_dir[:255] slice -- round 4 review's fix for the truncation-
    # collision issue -- so the expected key is computed the same way,
    # rather than this test re-slicing and silently drifting from what
    # quarantine() actually stores.
    clamped = landing._clamp_key(session_dir, landing.QUARANTINE_SESSION_DIR_MAX_LEN)
    row = (ingest.Quarantine & {"session_dir": clamped}).fetch1()
    assert row["reason"] == "session_dir_unrepresentable"
    assert row["detail"]["untruncated_session_dir"] == session_dir


def test_a_session_dir_too_long_after_the_root_moved_is_still_a_no_op(root):
    """Critical (round 4 review): `session_dir_unrepresentable`'s length
    check used to run BEFORE `already_ingested`, alongside
    `session_id_mismatch` -- correct for `session_id_mismatch`, which
    compares basenames, a property of the directory itself fixed for as
    long as it exists, but WRONG for the length check.
    `len(str(session_dir))` reads the FULL PATH, a function of the
    operator-supplied `--root` -- a moving global exactly like
    `SCHEMA_VERSION`, not a directory-local fact like a basename. A session
    landed under a short root, then the whole storage root moved (a
    remount, a different mount point, an archive move) to a longer path
    with the session itself completely untouched, used to re-quarantine on
    every subsequent poll even though it already had a valid `Ingestion`
    row -- verbatim the same design-section-9 contradiction ("the same
    session listed under both Ingested and Quarantined") round 3's own
    Item 1 fix closed for `session_id_mismatch`'s wrong position, reopened
    by this check's own wrong position instead.

    Simulated with a real copy of an already-landed session's directory
    tree into a deeply nested path, not a synthetic scenario: the copy
    carries the identical manifest (same subject, same `started_at`) that
    is already in `Ingestion`, and only the FULL PATH it now sits under
    changed -- `shutil.copytree`, not `shutil.move`, so the original,
    already-landed copy under the short root stays put and is not scanned
    again by this test (it is not even under `deep_root`, so
    `_candidate_dirs(deep_root)` cannot see it).
    """
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "movedkey")
    scan_once(tmp_path, prefix=prefix)  # lands under the short root

    deep_root = tmp_path / "moved"
    for _ in range(20):
        if len(str(deep_root / CI_RECIPE.session_id)) > 255:
            break
        deep_root = deep_root / ("nested_dir_" + "x" * 30)
    else:
        pytest.fail("could not construct a session_dir over 255 characters")

    shutil.copytree(
        SessionLayout(tmp_path, CI_RECIPE.session_id).dir,
        SessionLayout(deep_root, CI_RECIPE.session_id).dir,
    )
    moved_session_dir = str(SessionLayout(deep_root, CI_RECIPE.session_id).dir)
    assert len(moved_session_dir) > 255

    result = scan_once(deep_root, prefix=prefix)

    assert result.outcomes[moved_session_dir] is Outcome.ALREADY
    clamped = landing._clamp_key(moved_session_dir, landing.QUARANTINE_SESSION_DIR_MAX_LEN)
    assert len(ingest.Quarantine & {"session_dir": clamped}) == 0


@pytest.mark.parametrize(
    ("line_ending", "dedicated_subject"),
    [(b"\r\n", "crlfkey"), (b"\r", "crkey")],
    ids=["crlf", "bare-cr"],
)
def test_the_manifest_hash_matches_the_files_real_bytes_with_non_lf_endings(
    root, line_ending, dedicated_subject
):
    """Critical (I1 correction): I1's original round-2 fix replaced
    `blake3_file(path)` with `blake3(text.encode("utf-8"))`, reasoning about
    the UTF-8 codec round-tripping losslessly -- true, but the wrong layer.
    `Path.read_text()`'s default `newline=None` enables universal-newline
    translation ABOVE the codec: `\\r\\n` AND bare `\\r` both collapse to
    `\\n` in the decoded string regardless of encoding validity. Confirmed
    directly, not assumed: a manifest written with either line ending, read
    back and re-encoded, produces DIFFERENT bytes -- and a different blake3
    digest -- than the file's own real bytes, identically on 3.11.15 and
    3.13.9. SpikeGLX and Intan both run on Windows, so a non-LF-authored
    manifest is not a hypothetical this pipeline can assume away. Fixed by
    hashing the raw bytes `read_bytes()` returns, decoded separately for
    parsing.

    Parametrized over both non-LF forms Python's universal-newline
    translation recognises (round 4 review: the tree only covered CRLF;
    bare CR is translated identically and was equally broken before this
    fix, confirmed independently) -- not just CRLF, which is the common
    case but not the only one.

    Reads the file's OWN bytes directly, via `Path.read_bytes()`, as an
    independent oracle -- not a re-derivation of `watcher.py`'s own hashing
    logic, which would prove nothing if that logic were what was wrong.
    """
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, dedicated_subject)
    manifest_path = SessionLayout(tmp_path, CI_RECIPE.session_id).manifest_path
    manifest_path.write_bytes(manifest_path.read_bytes().replace(b"\n", line_ending))

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.INGESTED
    stored_hash = (ingest.Ingestion & {"session_dir": session_dir}).fetch1("manifest_hash")

    import blake3

    real_bytes_hash = blake3.blake3(manifest_path.read_bytes()).hexdigest()
    assert stored_hash == real_bytes_hash


def test_a_yaml_date_typed_params_value_quarantines_as_params_invalid(root):
    """Important 4: `params:\\n  calibrated_on: 2027-03-14` is typed by
    YAML's SafeLoader as a real `datetime.date`, not a `str` -- confirmed
    directly: `yaml.safe_load("calibrated_on: 2027-03-14")` returns
    `{"calibrated_on": datetime.date(2027, 3, 14)}`. `SessionParams` accepts
    it unchanged (`params` is a bare dict on purpose), and it used to die
    inside `paramset.register`'s own `content_hash` with "Object of type
    date is not JSON serializable" -- landing in `_scan_one`'s generic
    `unexpected_failure` catch-all rather than `params_invalid`, which
    already exists to name exactly this class of problem
    (`register_session_params` already owns "validating" the params file).
    Fixed by extending `register_session_params`'s own guard to prove
    `params` is JSON-serializable, using the identical call `register()`
    itself makes, before ever calling `register()`.
    """
    tmp_path, prefix, session_dir = root
    _use_dedicated_subject(tmp_path, "datekey")
    (SessionLayout(tmp_path, CI_RECIPE.session_id).dir / "session_params.yaml").write_text(
        "paramset_type: clustering\nparams:\n  calibrated_on: 2027-03-14\n"
    )

    result = scan_once(tmp_path, prefix=prefix)

    assert result.outcomes[session_dir] is Outcome.QUARANTINED
    row = (ingest.Quarantine & {"session_dir": session_dir}).fetch1()
    assert row["reason"] == "params_invalid"


def test_cli_ingest_still_prints_outcomes_found_before_a_root_fault(monkeypatch, capsys):
    """Also-fix: `main.py` used to return before ever printing
    `result.outcomes` whenever `root_error` was set -- discarding real,
    already-written `Ingestion`/`Quarantine` rows from the one report an
    operator would actually see, exactly what a MID-WALK fault produces
    (`_candidate_dirs`' own docstring: `root.iterdir()`'s `next()` can raise
    AFTER already yielding some children, not just before yielding any).

    Reproducing a genuine mid-walk fault needs a directory entry to vanish
    between being listed and being iterated to -- not reliably
    constructible with real filesystem operations (the same difficulty
    `discover.py`'s own docstring documents for the analogous `rglob` race),
    so this monkeypatches `scan_once` itself to return a `ScanResult`
    carrying both a real outcome and a `root_error` together, proving
    `main()`'s OWN handling of that return value in isolation from
    `scan_once`'s internal mechanics. Patches
    `wl_preproc.ingest.watcher.scan_once` specifically, not some
    `wl_preproc.cli.main`-level attribute: `main()`'s own `from
    wl_preproc.ingest.watcher import scan_once` is a LOCAL import inside the
    `ingest` branch, re-resolved from the `watcher` module's own namespace
    every time that branch runs, so patching the module's attribute is what
    actually reaches it -- the identical technique
    `test_the_session_key_check_goes_through_landings_own_helper` already
    uses for `landing.manifest_session_key`.
    """
    from wl_preproc.cli.main import main
    from wl_preproc.ingest import watcher

    def fake_scan_once(root, prefix, verify):
        return watcher.ScanResult(
            outcomes={"/scratch/2027-03-14_01": watcher.Outcome.INGESTED},
            root_error="OSError: simulated mid-walk fault",
        )

    monkeypatch.setattr(watcher, "scan_once", fake_scan_once)

    exit_code = main(["ingest", "--root", "/scratch"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "2027-03-14_01" in captured.out
    assert "ingested" in captured.out
