"""The daily report.

Its hardest requirement is negative: a category that cannot be counted yet must
say so, because "no failures" and "failures are not counted" must never render
identically. `Outcome.DEFERRED` (Task 8, postdating this report's own spec
section) adds a second, structurally different negative: a session set aside
for transient database contention writes no row anywhere -- not `Ingestion`,
not `Quarantine` -- and is not stalled either, because the session itself is
complete. It is invisible to every section this report can build from durable
state, and the report says so in its own text rather than leaving that silent.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest

from wl_preproc.cli.report import build_report, write_report
from wl_preproc.contracts.paths import MANIFEST_FILENAME, SessionLayout
from wl_preproc.ingest.watcher import Outcome, scan_once
from wl_preproc.schema import ingest
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def _section(body: str, heading: str) -> str:
    """The slice of the report under one `##` heading, and nothing else.

    Every assertion below that means "this appears in section X" goes through
    this rather than searching the whole document, because searching the whole
    document is how a test stops testing anything: `test_it_counts_what_was_
    ingested` asserted a session id appeared *somewhere* in the report, and a
    mutation making every session quarantine instead of land left it green --
    the id simply moved to the Quarantined section. A report whose whole job
    is to put facts under the right heading cannot be tested by a search that
    ignores headings.
    """
    marker = f"\n## {heading}"
    assert marker in body, f"no section headed {heading!r} in:\n{body}"
    return body.split(marker, 1)[1].split("\n## ", 1)[0]


def _declared_count(section: str) -> int:
    """The number in a section's own `## ... — N` heading."""
    return int(section.split("—", 1)[1].split("\n", 1)[0].strip())


def _line_for(section: str, needle: str) -> str:
    """The one line of `section` naming `needle`, so an assertion about one
    session's rendering cannot be satisfied by a different session's line."""
    lines = [line for line in section.splitlines() if needle in line]
    assert len(lines) == 1, f"expected exactly one line naming {needle!r}, got {lines}"
    return lines[0]


@pytest.fixture
def scanned(tmp_path, dj_conn, prefix):
    """Factory: land a CI_RECIPE-shaped session under a caller-chosen subject.

    `dj_conn`/`prefix` are session-scoped (`tests/conftest.py`) and shared by
    every test in the whole suite, not just this file. CI_RECIPE's own
    `subject="pico"` lands at exactly `(subject="pico", session_datetime=
    2027-03-14 09:00:00)` -- the same key `tests/schema/test_core.py`'s
    `a_session` fixture inserts, a collision that has already bitten three
    times in this phase (see `tests/ingest/test_landing.py`'s `landed`
    fixture and `tests/ingest/test_watcher.py`'s `_use_dedicated_subject`,
    both of which land under a dedicated subject for the identical reason).

    A factory, not one fixed subject shared by every test below: this fixture
    is function-scoped but `dj_conn`/`prefix` are not, so a single baked-in
    subject would still let one test in this file see `already_ingested() ==
    True` on its very first `scan_once` because an earlier test in the same
    file already landed under it -- moving the exact fragility this exists to
    remove from "shared with test_core.py" to "shared with the next test in
    this file" is not a fix. Every caller below states its own subject.
    """

    def _land(subject: str):
        ingest.activate(prefix=prefix)
        root = tmp_path / "scratch"
        root.mkdir()
        generate_session(root, CI_RECIPE.model_copy(update={"subject": subject}))
        scan_once(root, prefix=prefix)
        return root, prefix

    return _land


def test_it_counts_what_was_ingested(scanned):
    """Ingested means ingested, not "mentioned anywhere in the report".

    This test asserted an unconditional heading plus `str(CI_RECIPE.session_id)
    in body` -- a search of the whole document -- and review proved it vacuous
    by mutation: making `_evaluate_session` quarantine instead of land, so
    nothing is ever ingested at all, left every test in this file green,
    because the path simply appeared under Quarantined instead. So the
    assertions here are against the Ingested section alone, and against the
    count rather than mere presence: this session's own directory, once, under
    that heading, with the heading's declared number matching the lines
    actually printed beneath it.
    """
    root, prefix = scanned("rptcnt1")
    # Resolved, because `_candidate_dirs` resolves `root` before the watcher
    # records a `session_dir` -- so this is the exact string that landed.
    landed = str((root / CI_RECIPE.session_id).resolve())

    body = build_report(root, prefix=prefix)

    ingested = _section(body, "Ingested")
    assert ingested.count(landed) == 1, (
        f"{landed} is not listed under Ingested -- it is somewhere else in the "
        f"report, or nowhere:\n{body}"
    )
    declared = _declared_count(ingested)
    assert declared >= 1
    bullets = [line for line in ingested.splitlines() if line.startswith("- ")]
    assert declared == len(bullets), (
        f"the heading claims {declared} ingested sessions and prints {len(bullets)}"
    )


def test_it_names_the_categories_it_cannot_yet_count(scanned):
    """The negative requirement. A silently omitted category is
    indistinguishable from an empty one."""
    root, prefix = scanned("rptcat1")

    body = build_report(root, prefix=prefix)

    assert "not yet reported" in body.lower()
    for missing in ("populated", "tier-d", "eye-detector"):
        assert missing in body.lower()


def test_a_quarantined_session_appears_with_everything_needed_to_act_on_it(scanned):
    """Path and reason alone are not enough to do anything with.

    Spec section 9 justifies `subject`/`session_dt` existing at all with "a
    quarantine report naming an animal and a date is far more useful than one
    naming a path", and this report printed neither. `detail` was not printed
    either, though for `checksum_mismatch` it holds the offending file paths,
    which is the entire point of section 5.4 -- without them the row says a
    transfer was corrupt and not which file to re-copy.

    `failed_at` is written as naive UTC here rather than `datetime.now()`'s
    naive LOCAL time: it is what the section's window is now measured against,
    so a row whose timestamp is silently offset by the test host's timezone is
    testing a different age than it looks like it is.
    """
    root, prefix = scanned("rptqar1")
    ingest.Quarantine.insert1(
        {
            "session_dir": str(root / "2027-03-14_77"),
            "failed_at": datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
            "reason": "checksum_mismatch",
            "detail": {
                "mismatches": [
                    {"system": "spikeglx", "path": "probe0.imec0.ap.bin", "problem": "blake3"},
                    {"system": "bcam", "path": "frames.mp4", "problem": "size"},
                ]
            },
            "subject": "rptqar2",
            "session_dt": datetime.datetime(2027, 3, 14, 9, 0),
        }
    )

    body = build_report(root, prefix=prefix)

    quarantined = _section(body, "Quarantined")
    line = _line_for(quarantined, "2027-03-14_77")
    assert "checksum_mismatch" in line
    assert "rptqar2" in line, "the animal is not named"
    assert "2027-03-14 09:00" in line, "the session's own date is not named"
    detail = _line_for(quarantined, "probe0.imec0.ap.bin")
    assert "frames.mp4" in detail, "only one of the two offending files was surfaced"
    assert "blake3" in detail and "size" in detail


def test_the_quarantine_section_is_windowed_and_marks_recent_rows(scanned):
    """Quarantined rendered the entire table, unrestricted and forever, while
    Ingested was windowed to 24 h -- so a `failed_at` from two years ago was
    still listed as news, and the section only ever grew.

    Section 9 rules that a quarantine row is history and that a session
    quarantined, then fixed and re-ingested, keeps its row with nothing
    marking it resolved. That is deliberate and this test does not fight it:
    it checks the two things that make history readable instead -- a bounded
    window with what falls outside it counted rather than silently dropped,
    and a visible mark on what is recent, so a week-old quarantine sitting
    beside an Ingested line for the same session reads as the repair story it
    is rather than as a contradiction.
    """
    root, prefix = scanned("rptqwn1")
    at = datetime.datetime.now(datetime.UTC)
    ages = {"30": datetime.timedelta(hours=2), "31": datetime.timedelta(days=3), "32": datetime.timedelta(days=900)}
    for suffix, age in ages.items():
        ingest.Quarantine.insert1(
            {
                "session_dir": str(root / f"2027-03-14_{suffix}"),
                "failed_at": (at - age).replace(tzinfo=None),
                "reason": "manifest_invalid",
                "detail": {"error": f"probe aged {age}"},
                "subject": None,
                "session_dt": None,
            }
        )

    body = build_report(root, prefix=prefix, now=at)

    quarantined = _section(body, "Quarantined")
    assert "2027-03-14_32" not in quarantined, "a row from 900 days ago is still listed"
    assert "older row(s) not shown" in quarantined, (
        "rows outside the window are dropped with nothing saying they exist"
    )
    fresh = _line_for(quarantined, "2027-03-14_30")
    aging = _line_for(quarantined, "2027-03-14_31")
    assert "(new)" in fresh
    assert "(new)" not in aging, "a three-day-old row is marked as new"
    assert quarantined.index(fresh) < quarantined.index(aging), "not newest first"


def test_a_stalled_transfer_appears_with_the_systems_still_missing(scanned):
    """Design section 4.3 asks for stalled transfers to be reported "with the
    systems still missing", and the report printed only the path -- so
    `sentinel.missing_systems()` had no consumer at all, and two stalled
    sessions missing different systems rendered identically. With five
    possible systems that is the difference between knowing a transfer
    stalled and knowing which rig to go look at, which is why this test uses
    two sessions stalled on different systems rather than one.

    A second, distinct subject too ("rptstl2", not "pico"): these directories
    are never landed through `scan_once` here (only `build_report`'s own
    filesystem walk reads them), so a shared subject could not collide with
    anything today -- but there is no reason to leave "pico" sitting in this
    file's tree at all when the fixture above exists precisely to remove it,
    and a future change to `build_report` that starts consulting
    `already_ingested` for the stalled check would silently reintroduce the
    exact hazard `scanned` was built to close.
    """
    root, prefix = scanned("rptstl1")
    generate_session(
        root,
        CI_RECIPE.model_copy(update={"session_id": "2027-03-14_05", "subject": "rptstl2"}),
    )
    generate_session(
        root,
        CI_RECIPE.model_copy(update={"session_id": "2027-03-14_04", "subject": "rptstl3"}),
    )
    SessionLayout(root, "2027-03-14_05").done_marker("spikeglx").unlink()
    SessionLayout(root, "2027-03-14_04").done_marker("bcam").unlink()
    later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=5)

    body = build_report(root, prefix=prefix, now=later)

    stalled = _section(body, "Stalled transfers")
    assert _declared_count(stalled) == 2
    for session_id, system in (("2027-03-14_05", "spikeglx"), ("2027-03-14_04", "bcam")):
        line = _line_for(stalled, session_id)
        assert "missing:" in line, f"{session_id} is listed with no systems at all: {line}"
        # Equality, not containment: the point is that these two lines differ,
        # so a rendering that named every expected system for both -- or the
        # same system for both -- would satisfy a substring check.
        assert line.split("missing:")[1].strip() == system


def test_it_writes_a_dated_file_and_returns_its_path(scanned, tmp_path):
    root, prefix = scanned("rptwrt1")
    # An incomplete session under `root`, alongside the naive `now` below:
    # `build_report`'s stalled-transfers walk calls `is_stalled(..., now=at)`
    # for every session directory holding a valid manifest, and `is_stalled`
    # short-circuits False only for a COMPLETE session -- an incomplete one
    # falls through to `now - last_change_at(...)`, which raises TypeError
    # when `now` is naive (`last_change_at` always returns tz-aware UTC).
    # Without this, a naive `now` alone proves nothing: this exact test
    # passed with one before `build_report` gained the coercion, because
    # nothing under `root` was ever incomplete for it to reach.
    generate_session(
        root,
        CI_RECIPE.model_copy(update={"session_id": "2027-03-14_06", "subject": "rptwrt2"}),
    )
    SessionLayout(root, "2027-03-14_06").done_marker("spikeglx").unlink()
    out = tmp_path / "reports"

    path = write_report(out, root, prefix=prefix, now=datetime.datetime(2027, 3, 15, 7, 0))

    assert path == out / "2027-03-15.md"
    assert path.read_text().startswith("# wl-preproc")


def test_the_24h_window_does_not_move_with_the_callers_utc_offset(tmp_path, dj_conn, prefix):
    """`since` was formatted `%Y-%m-%d %H:%M:%S` straight off an aware `now`,
    which drops the offset without ever applying it, and then compared against
    `ingested_at` -- stored naive UTC, like every datetime in this schema.

    So the window moved by the caller's whole offset: 11 h at +13:00, 35 h at
    -11:00. A session ingested 12 h ago simply vanished from a report
    generated in New Zealand, and "ingested in the last 24 h" -- the report's
    own first line, and the reason `Ingestion` has a timestamp column at all
    (spec section 8.2) -- quietly meant something else. Routed through
    `landing.to_naive_utc`, the one conversion every other datetime in this
    codebase goes through.
    """
    ingest.activate(prefix=prefix)
    root = tmp_path / "scratch"
    root.mkdir()
    generate_session(root, CI_RECIPE.model_copy(update={"subject": "rpttz01"}))
    landed = str((root / CI_RECIPE.session_id).resolve())
    now = datetime.datetime.now(datetime.UTC)
    scan_once(root, prefix=prefix, now=now - datetime.timedelta(hours=12))

    # The same instant, expressed at +13:00 -- the widest offset a real caller
    # can have, and the direction that shrinks the window rather than widening
    # it, so the failure is a dropped row rather than an extra one.
    body = build_report(root, prefix=prefix, now=now.astimezone(datetime.timezone(datetime.timedelta(hours=13))))

    assert landed in _section(body, "Ingested"), (
        "a session ingested 12 h ago is missing from the 24 h window because the "
        "caller's clock carried an offset"
    )


def test_a_missing_root_reports_the_fault_instead_of_crashing(scanned):
    """The four filesystem faults below are the whole reason this section
    exists, and `scan_once` already survives every one of them: review
    compared the two on identical input and found `wlpp ingest` returning 0
    where `wlpp report` raised, which means no dated file is written and the
    stalled alarm is lost -- with a genuinely stalled session under the same
    root. `build_report` now walks through the same guarded `_candidate_dirs`
    the scan does, so the two agree by construction rather than by review.

    This one also carries the disk check: `scratch_headroom()` was called with
    no argument, measuring `/` -- which always succeeds, so a root that does
    not exist still produced a confident "N GiB free (ok)" line under a
    heading this file's docstring uses to claim it answers "when did scratch
    start filling up?". Passing the root makes an unmeasurable disk say so.
    """
    root, prefix = scanned("rptmis1")
    missing = root.parent / "no-such-storage-root"

    body = build_report(missing, prefix=prefix)

    assert scan_once(missing, prefix=prefix).root_error is not None, "premise: the scan sees it too"
    assert body.startswith("# wl-preproc")
    stalled = _section(body, "Stalled transfers")
    assert "was not fully scanned" in stalled
    assert "FileNotFoundError" in stalled
    disk = _section(body, "Disk")
    assert "not measured" in disk
    assert "GiB free" not in disk, "a root that does not exist reported free space"


def test_an_unsearchable_root_still_produces_a_report(scanned):
    """Mode 0600: readable, so `iterdir()` succeeds, but not searchable, so
    every `child.is_dir()` raises EACCES. `scan_once` returns cleanly with no
    candidates and no root fault -- a per-child fault is not a root fault --
    and so must this."""
    root, prefix = scanned("rptsrc1")
    original = root.stat().st_mode

    os.chmod(root, 0o600)
    try:
        result = scan_once(root, prefix=prefix)
        body = build_report(root, prefix=prefix)
    finally:
        os.chmod(root, original)

    assert result.outcomes == {} and result.root_error is None, "premise"
    assert body.startswith("# wl-preproc")
    stalled = _section(body, "Stalled transfers")
    assert _declared_count(stalled) == 0
    assert "was not fully scanned" not in stalled, "a per-child fault is not a root fault"


def test_an_unreadable_root_reports_the_fault_instead_of_crashing(scanned):
    """Mode 000: `iterdir()` itself fails -- on 3.13 at the call, on 3.11 at
    the first `next()`, which `_candidate_dirs` handles either way. That is a
    root fault, and an empty Stalled section that does not say so is
    indistinguishable from a root holding no stalled transfer."""
    root, prefix = scanned("rptunr1")
    original = root.stat().st_mode

    os.chmod(root, 0o000)
    try:
        result = scan_once(root, prefix=prefix)
        body = build_report(root, prefix=prefix)
    finally:
        os.chmod(root, original)

    assert result.root_error is not None, "premise: the scan sees it too"
    assert body.startswith("# wl-preproc")
    stalled = _section(body, "Stalled transfers")
    assert "was not fully scanned" in stalled
    assert "PermissionError" in stalled


def test_a_walk_fault_does_not_suppress_a_real_disk_reading(scanned):
    """Mode 000 again, but checking the Disk section this time, not just
    Stalled: `_candidate_dirs.iterdir()` needs to list `root`'s own entries,
    which needs execute permission on `root` itself, but `scratch_headroom`'s
    `shutil.disk_usage` -> `os.statvfs` needs only search permission on
    `root`'s PARENT to reach it -- not on `root` itself. So a root the walk
    cannot list can still be measured for free space, and this project has
    already shipped a version of `build_report` that got that wrong: review
    found that folding the walk fault and the disk fault into one
    `Readings.root_error` field made the Disk section read "not measured"
    using the WALK's `PermissionError` for a disk probe that had, in fact,
    succeeded -- confirmed by a direct before/after comparison against
    `01f9010` (pre-extraction), which showed a real "N GiB free" line for
    this exact root. That is the precise inversion `scratch_headroom`'s own
    docstring forbids: an unmeasurable disk must never render as a number,
    and -- the direction this test guards -- a MEASURED disk must never
    render as unmeasured because of an unrelated fault.
    """
    root, prefix = scanned("rptwlk1")
    original = root.stat().st_mode

    os.chmod(root, 0o000)
    try:
        result = scan_once(root, prefix=prefix)
        body = build_report(root, prefix=prefix)
    finally:
        os.chmod(root, original)

    assert result.root_error is not None, "premise: the walk itself is faulted"
    stalled = _section(body, "Stalled transfers")
    assert "was not fully scanned" in stalled, "the walk fault must still be reported"
    disk = _section(body, "Disk")
    assert "GiB free" in disk, (
        "a root the walk cannot list can still be measured for free space -- "
        "the walk fault must not suppress a disk probe that succeeded"
    )
    assert "not measured" not in disk


def test_one_unreadable_child_does_not_take_down_the_whole_report(scanned):
    """The row that matters most: an rsync run as the wrong user, or one ACL
    slip, on ONE session directory. `(child / MANIFEST_FILENAME).is_file()`
    stats a path inside that child, so it raises EACCES even though the
    directory itself is perfectly real -- and unguarded, that one child used
    to take down the entire report, including the stalled session sitting
    beside it that the report exists to surface."""
    root, prefix = scanned("rptchd1")
    generate_session(
        root,
        CI_RECIPE.model_copy(update={"session_id": "2027-03-14_05", "subject": "rptchd2"}),
    )
    SessionLayout(root, "2027-03-14_05").done_marker("spikeglx").unlink()
    blocked = root / "2027-03-14_09"
    blocked.mkdir()
    (blocked / MANIFEST_FILENAME).write_text("session_id: 2027-03-14_09\n")
    original = blocked.stat().st_mode
    later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=5)

    os.chmod(blocked, 0o000)
    try:
        result = scan_once(root, prefix=prefix)
        body = build_report(root, prefix=prefix, now=later)
    finally:
        os.chmod(blocked, original)

    assert len(result.outcomes) == 2 and result.root_error is None, "premise"
    stalled = _section(body, "Stalled transfers")
    assert _line_for(stalled, "2027-03-14_05"), "the stalled session beside it was lost"
    assert "2027-03-14_09" not in stalled, "an unreadable child is skipped, not guessed at"


def test_a_relative_root_still_reports_absolute_paths_in_every_section(scanned, monkeypatch):
    """A relative `--root` used to make two sections disagree about the same
    storage root: Stalled printed whatever `iterdir()` yielded from the
    unresolved path, while Ingested printed the absolute `session_dir` the
    watcher recorded through `_candidate_dirs`' own `root.resolve()`. Reusing
    that function is what makes them agree."""
    root, prefix = scanned("rptrel1")
    generate_session(
        root,
        CI_RECIPE.model_copy(update={"session_id": "2027-03-14_05", "subject": "rptrel2"}),
    )
    SessionLayout(root, "2027-03-14_05").done_marker("spikeglx").unlink()
    later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=5)
    monkeypatch.chdir(root.parent)

    body = build_report(Path(root.name), prefix=prefix, now=later)

    stalled = _section(body, "Stalled transfers")
    printed = _line_for(stalled, "2027-03-14_05").split("`")[1]
    assert Path(printed).is_absolute()
    assert printed == str((root / "2027-03-14_05").resolve())
    assert str(root.resolve()) in _section(body, "Ingested")


def test_a_session_id_mismatch_is_stall_checked_against_the_directory_that_exists(scanned):
    """`SessionLayout(root, manifest.session_id)` built the layout from the
    MANIFEST's id rather than the directory's own name. Those agree for every
    session `wlpp ingest` would land -- a mismatch is a `session_id_mismatch`
    quarantine before anything else looks at it -- but this walk deliberately
    does not filter on quarantine state, so a mismatching directory was
    stall-checked against a path that does not exist. `last_change_at` falls
    back to `datetime.min` for a missing directory by design, so the session
    reported as stalled unconditionally and forever, naming a directory
    nobody could go look at, while the complete session actually on disk went
    unexamined."""
    root, prefix = scanned("rptmsm1")
    generate_session(
        root,
        CI_RECIPE.model_copy(update={"session_id": "2027-03-14_07", "subject": "rptmsm2"}),
    )
    (root / "2027-03-14_07").rename(root / "2027-03-14_08")
    later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=5)

    body = build_report(root, prefix=prefix, now=later)

    stalled = _section(body, "Stalled transfers")
    assert "2027-03-14_08" not in stalled, (
        "a complete session was reported stalled because it was checked against "
        "the path its manifest names rather than the one it sits in"
    )
    assert "2027-03-14_07" not in stalled


def test_gather_readings_returns_the_values_build_report_renders(scanned):
    """The extraction's whole point: one computation, two renderings. If these
    two disagree the responder and the daily report will report different
    numbers for the same question, which is the defect the doctor/report
    headroom extraction already caught once in this project."""
    root, prefix = scanned("rptgth1")
    from wl_preproc.cli.report import build_report, gather_readings

    readings = gather_readings(root, prefix=prefix)
    body = build_report(root, prefix=prefix)

    assert f"Ingested (24 h) — {len(readings.ingested)}" in body
    assert f"Quarantined (7 d) — {len(readings.quarantined)}" in body
    assert f"Stalled transfers — {len(readings.stalled)}" in body
    assert f"{readings.free_gib} GiB free" in body


def test_gather_readings_does_not_write(scanned, table_snapshot, deep_equal):
    """Same guarantee build_report carries. `in_transaction` cannot detect a
    write here — DataJoint's insert() never touches it — so this snapshots
    rows, exactly as test_the_report_opens_no_write_transaction does.

    `table_snapshot`/`deep_equal` come in as fixtures, not an import from
    `tests.conftest`. Importing the name works fine -- `tests.conftest` is a
    PEP 420 namespace package even with no `tests/__init__.py`, confirmed
    directly (`from tests.conftest import table_snapshot` succeeds) -- but
    calling the imported object does not: once decorated with
    `@pytest.fixture`, it is a `_pytest.fixtures.FixtureFunctionDefinition`,
    and pytest's own `__call__` on that type refuses direct invocation
    outside its fixture-resolution machinery (`Failed: Fixture "..." called
    directly...`, also confirmed directly). So the fixture form is not a
    workaround for an import limitation that isn't there; it is the only
    form that works at all, and it happens to be the same shape
    `tests/schema/conftest.py`'s `enum_values` already uses -- a
    session-scoped fixture returning a callable, taken by consuming tests as
    a parameter and called by name.
    """
    root, prefix = scanned("rptgth2")
    from wl_preproc.cli.report import gather_readings
    from wl_preproc.schema import core, ingest, pipeline

    from wl_preproc.daemon import job_tables

    # The `~jobs` tables are in the watched set from Phase 1c-4. `gather_readings`
    # calls `count_stale_jobs`, which reads them -- and the 1c-2 handoff records
    # that this snapshot did not cover them. That was harmless only while no
    # Computed table existed to create one; 1c-4 declares the first two. A
    # snapshot that silently misses a table is worse than no snapshot, because
    # it reads as coverage.
    watched = [
        ingest.Ingestion,
        ingest.Quarantine,
        pipeline.Session,
        core.AcquisitionSystem,
        *job_tables(),
    ]
    before = [table_snapshot(t) for t in watched]
    gather_readings(root, prefix=prefix)
    after = [table_snapshot(t) for t in watched]

    assert deep_equal(after, before), "gather_readings wrote or changed at least one row"


def test_the_report_opens_no_write_transaction(scanned, table_snapshot, deep_equal):
    """The same read-only guarantee `wlpp doctor` carries, so anyone can run it
    at any time without considering what else is running.

    `in_transaction is False` alone proved nothing, and review caught it by
    proof rather than argument: it put a real `ingest.Quarantine.insert1(...)`
    inside `build_report` and this test still passed. DataJoint 2.3.2's
    `insert()`/`insert1()` call `self.connection.query()` directly and never
    touch `Connection._in_transaction` -- confirmed against
    `tests/schema/test_daemon.py`'s own two-test pair, which records the
    identical shape for `populate()`: `in_transaction` is only ever `True`
    between an explicit `start_transaction()`/`commit_transaction()`, which is
    what the three-part make's own `insert` phase uses and what a plain,
    bare `insert1()` call -- every write this ingest pipeline actually makes
    -- does not. So `in_transaction is False` is equally true of a function
    that writes and one that does not, and is kept here only because it is
    still a real, if incomplete, part of the read-only claim -- not because
    it is sufficient on its own.

    What actually proves nothing was written: an exact snapshot of every row
    `build_report` could plausibly touch, taken before and compared after.
    `ingest.Ingestion`/`ingest.Quarantine` are what `build_report` itself
    queries; `pipeline.Session`/`pipeline.Subject`/`core.AcquisitionSystem`
    are what `landing.land_session` would write if a future change ever
    called it from here by mistake -- the report imports none of those
    modules today, so this also catches that specific regression shape
    before it could ship.

    `table_snapshot`/`deep_equal` come in as fixtures now (Phase 1c-3, Task
    1) rather than the module-local `_table_snapshot`/`_deep_equal` this test
    used to call -- moved to `tests/conftest.py` so `test_gather_readings_
    does_not_write` (above) and Task 5's responder tests can reach them too.
    """
    import datajoint as dj

    from wl_preproc.schema import core, pipeline

    root, prefix = scanned("rpttxn1")
    core.activate(prefix=prefix)

    from wl_preproc.daemon import job_tables

    tables = (
        ingest.Ingestion,
        ingest.Quarantine,
        pipeline.Session,
        pipeline.Subject,
        core.AcquisitionSystem,
        # See `test_gather_readings_does_not_write` above: `build_report` reaches
        # `count_stale_jobs`, which reads these, and 1c-4 is the phase that gives
        # this project a Computed table for them to exist for at all.
        *job_tables(),
    )
    before = [table_snapshot(table) for table in tables]

    build_report(root, prefix=prefix)

    assert dj.conn().in_transaction is False
    after = [table_snapshot(table) for table in tables]
    assert deep_equal(after, before), "build_report wrote or changed at least one row"


def test_a_deferred_session_is_named_as_such_rather_than_invisible(
    tmp_path, dj_conn, prefix, monkeypatch
):
    """`Outcome.DEFERRED` (Task 8) writes no row anywhere -- not `Ingestion`,
    not `Quarantine` -- when a session's paramset registration hits genuine
    database contention, and `is_stalled` reports it as not stalled because
    the session genuinely is complete. So this session is invisible to every
    section `build_report` computes from durable state: not Ingested (no
    row), not Quarantined (no row), not Stalled (short-circuits False). That
    is exactly the "NO section of any report" scenario
    `tests/ingest/test_watcher.py::test_a_datajoint_error_that_is_not_
    contention_quarantines_not_defers`'s own docstring names for the general
    (permanent-fault) case -- here it is the narrow, deliberately-accepted
    instance of it, and this test's job is to confirm the report's own text
    says so rather than leaving a reader to conclude either "ingested" or
    "nothing happened".

    Forces a REAL exhaustion of `paramset.register`'s bounded retry loop, the
    identical technique `test_watcher.py`'s own
    `test_paramset_registration_contention_defers_rather_than_quarantining`
    uses: every attempt at `paramset._insert_new` collides with a genuine,
    separately-inserted competing row (a real MySQL primary-key violation
    each time, not a fabricated exception), so the loop is driven to actual
    exhaustion rather than one recovered race standing in for it.
    """
    from wl_preproc.schema import paramset

    ingest.activate(prefix=prefix)
    root = tmp_path / "scratch"
    root.mkdir()
    generate_session(root, CI_RECIPE.model_copy(update={"subject": "rptdfr1"}))
    session_dir = str(root / CI_RECIPE.session_id)
    (root / CI_RECIPE.session_id / "session_params.yaml").write_text(
        "paramset_type: report-defer-probe\nparams:\n  probe: true\n"
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

    result = scan_once(root, prefix=prefix)

    # The premise check: if this is not DEFERRED, the probe itself is broken
    # and everything below would be testing the wrong scenario.
    assert result.outcomes[session_dir] is Outcome.DEFERRED
    assert len(ingest.Quarantine & {"session_dir": session_dir}) == 0
    assert len(ingest.Ingestion & {"session_dir": session_dir}) == 0

    body = build_report(root, prefix=prefix)

    # Not listed as ingested, quarantined, or stalled -- this session's own
    # full path (unique to this test's `tmp_path`, so no other test's landed
    # row can produce it) appears nowhere in any of those three sections.
    assert session_dir not in body
    # And the report says why, rather than leaving the reader to guess.
    assert "deferred" in body.lower()
    assert "contention" in body.lower()
    assert "not lost" in body.lower()


def test_a_stale_reservation_is_both_counted_and_visible_to_the_snapshot(
    scanned, table_snapshot, dj_conn
):
    """The `~jobs` gap 1c-2 recorded, closed and then checked from both sides.

    A failed populate leaves a reservation behind. It must be COUNTED by
    `count_stale_jobs` — which the report prints — and it must be VISIBLE in
    `daemon.job_tables()`, which is what the two write-detection tests above
    snapshot. Counted but invisible is the exact shape of the gap: the number
    on the report would be right while the snapshot proving the report wrote
    nothing silently skipped the only tables it could have written to.
    """
    import datajoint as dj

    from wl_preproc import daemon
    from wl_preproc.cli.report import gather_readings
    from wl_preproc.schema import timebase

    root, prefix = scanned("rptjobs")
    timebase.activate(prefix=prefix)

    @timebase.schema
    class ReportJobsProbeSource(dj.Manual):
        definition = """
        # throwaway source for the report's ~jobs visibility probe
        n : int
        """

    @timebase.schema
    class ReportJobsProbeDerived(dj.Computed):
        definition = """
        # throwaway computed table for the report's ~jobs visibility probe
        -> ReportJobsProbeSource
        ---
        doubled : int
        """

        def make(self, key):
            self.insert1({**key, "doubled": key["n"] * 2})

    try:
        ReportJobsProbeSource.insert1({"n": 1})
        jobs = ReportJobsProbeDerived.jobs
        jobs.refresh()
        assert jobs.reserve({"n": 1})
        # Backdate past the 24 h default, so the REAL threshold classifies it.
        # A fresh reservation is correctly not stale — measured: without this
        # the count is 0 and the test proves only that nothing crashed.
        dj_conn.query(
            f"UPDATE {jobs.full_table_name} "
            "SET reserved_time = reserved_time - INTERVAL 48 HOUR"
        )

        readings = gather_readings(root, prefix=prefix)
        assert readings.stale_jobs is not None
        assert readings.stale_jobs >= 1

        snapshots = [table_snapshot(table) for table in daemon.job_tables()]
        assert any(snapshot for snapshot in snapshots), (
            "the reservation was counted but is invisible to the snapshot"
        )
    finally:
        # `.jobs` FIRST, child before parent -- the same leak fixed in
        # `tests/schema/test_daemon.py`. `dj.Schema.jobs` resolves only while
        # the target table still exists, so dropping the probe first orphans
        # `~~report_jobs_probe_derived` in the shared database with its
        # reservation still in it, invisible to `job_tables()`,
        # `count_stale_jobs` and the reaper alike.
        ReportJobsProbeDerived.jobs.drop()
        ReportJobsProbeDerived.drop_quick()
        ReportJobsProbeSource.drop_quick()


def test_an_orphaned_archiving_directory_is_named_in_the_disk_section(scanned):
    """Task 10 whole-branch review, cheap correction: `archive/stage.py`'s
    `scratch = session_dir.parent / f".{session_dir.name}.archiving"` is
    reaped only when `all_matched` -- a session whose most recent archive
    attempt failed verification leaves this directory sitting on scratch,
    a full-size compressed copy nothing named anywhere before this. No real
    `archive_session` call is run here (this suite already covers that path
    end to end, expensively, in `tests/archive/test_stage.py`); this test's
    only claim is about `build_report`'s own rendering, so the directory is
    created directly, matching the exact name `archive_session` would have
    used for this session.
    """
    root, prefix = scanned("orph1")
    session_id = CI_RECIPE.session_id
    scratch = root / f".{session_id}.archiving"
    scratch.mkdir()
    (scratch / "some-chunk.bin").write_bytes(b"not a real zarr chunk")

    body = build_report(root, prefix=prefix)

    section = _section(body, "Disk")
    # The specific phrase, not the bare word "orphaned": pytest's own
    # `tmp_path` is named after the TEST FUNCTION, and this test's own name
    # contains "orphaned" -- a bare substring check against `section` would
    # pass on the temp path alone, proving nothing about the report line
    # (found directly: the negative test below failed on exactly this before
    # its own assertion was narrowed the identical way).
    assert "orphaned `.archiving`" in section
    assert str(scratch) in section


def test_no_orphaned_archiving_directory_is_silent_in_the_disk_section(scanned):
    """The negative direction, pinned separately rather than assumed: a
    session with no leftover `.archiving` directory must not print the
    orphaned-directory line at all -- proving it is genuinely conditional
    on what `_orphaned_archiving_dirs` finds, not a hardcoded string every
    report carries regardless.

    Checks the specific phrase, not the bare word "orphaned": this test's
    OWN name contains "orphaned", and pytest names `tmp_path` after the
    test function, so a bare substring check against `section` (which
    includes the scratch path) passed on the temp path alone before this
    fix -- proving nothing about whether the report line itself appeared,
    caught by reading the actual failure rather than assuming the first
    version was right.
    """
    root, prefix = scanned("orph2")

    body = build_report(root, prefix=prefix)

    section = _section(body, "Disk")
    assert "orphaned `.archiving`" not in section
