# tests/cli/test_eye_report.py
"""The Eye section of the daily report.

Task 10 populates `EyeCalibration`/`EyeQuality` (`wl_preproc/schema/eye.py`).
This file is only about `build_report`'s OWN rendering of rows already
sitting in those two tables -- never about how they got there. Task 10's own
`tests/schema/test_eye_populate.py` already covers `make()` end to end,
expensively, through a real synthetic ohDPI recording; a row inserted
directly here, the same way `tests/schema/test_eye_schema.py`'s own
`test_registering_them_does_not_break_a_clean_daemon_pass` does, is all this
file needs.

Controller ruling A (task-11 brief): the Eye section's own numbers are
computed inside `build_report`, never `gather_readings` -- see
`cli/report.py`'s own `_eye_rows` docstring for why (the responder reads
none of these values, and `gather_readings` runs on every wl.works poll
under the single global lock that also serialises job accepts).

Controller ruling B: a session with no canonical gaze is a first-class
outcome with a stated reason, and two different reasons must never render
identically -- never a single collapsed "no gaze: N" count.
`test_two_distinct_refusal_reasons_render_as_two_distinct_lines` is the test
that pins this; it is the one this whole task exists to make sure never goes
vacuous.
"""

from __future__ import annotations

import datetime

import pytest

from wl_preproc.cli.report import build_report


def _section(body: str, heading: str) -> str:
    """The slice of the report under one `##` heading, and nothing else.

    Duplicated from `tests/cli/test_report.py`'s own helper of the same
    name (also duplicated a second time already, in `tests/cli/
    test_archive_cli.py`) rather than imported: this repository's test
    layout is deliberately `__init__.py`-free (this project's own
    CLAUDE.md), so test files do not import fixtures or helpers from one
    another -- inventing a shared conftest helper for five lines is more
    machinery than it is worth.
    """
    marker = f"\n## {heading}"
    assert marker in body, f"no section headed {heading!r} in:\n{body}"
    return body.split(marker, 1)[1].split("\n## ", 1)[0]


def _subsection(section: str, heading: str) -> str:
    """The slice of a `##` section under one of ITS OWN `###` subheadings.

    The Eye section is the first in `report.py` to carry more than one list
    under a single `##` heading -- three genuinely different questions
    (per-session-per-eye numbers, an aggregate `calibration_source`
    breakdown, and the sessions with no canonical gaze at all) do not read
    as one flat list. Mirrors `_section` above exactly, one heading level
    down, so an assertion about one subsection cannot be satisfied by text
    that only appears in a different one -- the identical reasoning
    `test_report.py`'s own `_section` docstring gives for not searching the
    whole document: a mutation that moves a line from one subsection to
    another must be caught, not passed by accident.
    """
    marker = f"\n### {heading}"
    assert marker in section, f"no subsection headed {heading!r} in:\n{section}"
    return section.split(marker, 1)[1].split("\n### ", 1)[0]


def _line_for(section: str, needle: str) -> str:
    """The one line of `section` naming `needle` -- see `test_report.py`'s
    own copy of this helper for why an assertion needs this rather than a
    bare substring search of the whole section."""
    lines = [line for line in section.splitlines() if needle in line]
    assert len(lines) == 1, f"expected exactly one line naming {needle!r}, got {lines}"
    return lines[0]


def _breakdown_counts(section: str) -> dict[str, int]:
    """The four `calibration_source` counts the "Calibration source"
    subsection prints, by name -- not by position, so a mutation that
    reorders the four lines cannot fool a test that reads them back this
    way."""
    return {
        source: int(_line_for(section, f"{source}:").rsplit(":", 1)[1].strip())
        for source in ("fitted", "online", "carried_forward", "refused")
    }


def _older_no_gaze_count(section: str) -> int:
    """The `N` in "No canonical gaze"'s own "N older row(s) not shown" note
    (`report.py`'s own `older_no_gaze` rollup, second fix round -- the same
    shape as `test_report.py`'s Quarantine "older row(s) not shown" line),
    or `0` when the note is absent -- every refused row currently on record
    falls inside the 7 d window, so there is nothing to roll up."""
    lines = [line for line in section.splitlines() if "older row(s) not shown" in line]
    if not lines:
        return 0
    assert len(lines) == 1, f"expected at most one 'older row(s)' note, got {lines}"
    return int(lines[0].split("_")[1].split()[0])


@pytest.fixture(scope="module")
def eye_schema(dj_conn, prefix):
    """Mirrors `tests/schema/test_eye_schema.py`'s own `schemas` fixture:
    a small, module-scoped, locally-activated schema handle. Not shared via
    `tests/schema/conftest.py` -- nothing here is used by another test
    module, which is the bar that fixture's own docstring sets for living
    there instead.

    Also activates `ingest` and `timebase` (fix round): `_land_session` now
    plants a real `ingest.Ingestion` row alongside the bare `pipeline.
    Session` one, since `build_report`'s "Calibration and quality" list is
    windowed to the same 24 h `Ingested` uses and needs a real `Ingestion.
    ingested_at` to window against -- and, to keep that `Ingestion` row from
    leaving these sessions outstanding for `daemon.run_once()` on every
    OTHER test file's own pass (see `_land_session`'s own docstring for the
    real failure this caught), a `timebase.TimingProvenance` "done" marker
    too. `eye.activate()` cascades through `core`/`pipeline` but never
    reaches `ingest` or `timebase` -- confirmed directly by reading
    `schema/eye.py::activate` and `schema/core.py::activate`, neither
    imports either -- so both are activated here explicitly, once per
    module, the same idempotent-activation contract every `activate()` in
    this codebase carries.
    """
    from wl_preproc.schema import eye, ingest, timebase

    eye.activate(prefix=prefix)
    ingest.activate(prefix=prefix)
    timebase.activate(prefix=prefix)
    return eye


def _land_session(
    subject: str,
    session_datetime: datetime.datetime,
    *,
    ingested_at: datetime.datetime | None = None,
) -> None:
    """A bare `pipeline.Session` row plus a real `ingest.Ingestion` row --
    not a landed session in the full `scan_once` sense (no manifest, no
    files on disk), and no ohDPI recording behind it. `EyeCalibration`/
    `EyeQuality` are `dj.Computed` tables whose `key_source` this file never
    exercises (that is Task 10's `tests/schema/test_eye_populate.py`'s job)
    -- rows are inserted directly below, and all a direct insert needs is
    the `-> pipeline.Session` FK target to exist first.

    **`ingest.Ingestion` added in the fix round.** `build_report`'s
    "Calibration and quality, per session per eye" list now windows to the
    same 24 h `Ingested` uses (`report.py`'s own comment where it reuses
    `readings.ingested`), so a session with no `Ingestion` row at all would
    never appear there regardless of what `EyeCalibration`/`EyeQuality` hold
    for it -- every existing test in this file wants its own session
    VISIBLE in that list by default, which is why `ingested_at` defaults to
    real "now" (always inside any 24 h window computed off real wall-clock
    time) rather than requiring every call site to supply one. A test that
    specifically wants a session OUTSIDE the window passes `ingested_at`
    explicitly (see `test_a_session_older_than_the_window_is_not_listed_
    but_is_still_counted`).

    `session_dir`/`topology`/`manifest_hash` are provenance this file's own
    assertions never read -- placeholder values, matching the shape
    `tests/schema/test_eye_populate.py`'s own `_land` helper already uses
    for the identical columns.

    **Also plants `pipeline.event.BehaviorRecording` and `timebase.
    TimingProvenance` "done" markers -- a real bug this fix round's own
    manual full-suite run caught, not a guess.** A real `ingest.Ingestion`
    row is GLOBALLY VISIBLE to every other test file sharing this suite's
    one session-scoped database for the rest of the run, and
    `daemon.run_once()` -- which several OTHER test files call, in
    `tests/schema/test_daemon.py` and `tests/schema/test_eye_schema.py`'s
    own `test_registering_them_does_not_break_a_clean_daemon_pass` -- sweeps
    EVERY outstanding `(subject, session_datetime)` pair, not just the one
    the calling test cares about. Two stages key on nothing but `(pipeline.
    Session & ingest.Ingestion)`, with no `core.AcquisitionSystem` gate to
    exclude a session this bare: `daemon.py::_event_stage_keys()` (`(Session
    & Ingestion) - pipeline.event.BehaviorRecording`) and `timebase.
    TimingProvenance.key_source` ("Sessions that landed. Same reasoning as
    `SystemTimebase.key_source`" -- that table's own docstring). Without a
    real `session_dir` behind these sessions, both stages tried to decode a
    sync box log that does not exist and failed on every one of these
    subjects, on EVERY daemon pass any other test ran afterward --
    confirmed directly: the first full-suite run after adding the bare
    `Ingestion` insert above failed four tests in `test_daemon.py`/
    `test_eye_schema.py`, all with the identical `"no sync box log"` error,
    naming this file's own subjects. Every OTHER computed table's
    `key_source` requires `core.AcquisitionSystem` (or something that itself
    requires it), which these sessions never get, so this pair is the
    complete set -- planting their own "already done" markers directly is
    what makes a bare session look like one the daemon has already finished
    with, instead of one still outstanding and unreachable.
    """
    from wl_preproc.schema import ingest, pipeline, timebase

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
    pipeline.Session.insert1(
        {"subject": subject, "session_datetime": session_datetime}, skip_duplicates=True
    )
    ingest.Ingestion.insert1(
        {
            "subject": subject,
            "session_datetime": session_datetime,
            "ingested_at": (
                ingested_at
                if ingested_at is not None
                else datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            ),
            "session_dir": f"/synthetic/{subject}",
            "integrity": "verified",
            "topology": {},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    pipeline.event.BehaviorRecording.insert1(
        {"subject": subject, "session_datetime": session_datetime}, skip_duplicates=True
    )
    timebase.TimingProvenance.insert1(
        {
            "subject": subject,
            "session_datetime": session_datetime,
            "tier": "D",
            "n_barcodes_emitted": 0,
            "n_systems_aligned": 0,
            "n_segments": 0,
            "n_rejected_segments": 0,
            "worst_residual_us": 0.0,
            "worst_drift_ppm": 0.0,
            "pending_inputs": "",
            "event_code_agreement": None,
            "trial_count_agreement": None,
            "camera_trigger_count": None,
            "n_full_code_records": 0,
            "n_strobe_witnesses": 0,
            "decode_errors": 0,
            "block_agreement": None,
        },
        allow_direct_insert=True,
        skip_duplicates=True,
    )


def _quality_row(
    subject: str,
    session_datetime: datetime.datetime,
    eye_value: str,
    *,
    tracking_loss_fraction: float,
    blink_rate_hz: float,
) -> dict:
    return {
        "subject": subject,
        "session_datetime": session_datetime,
        "eye": eye_value,
        "tracking_loss_fraction": tracking_loss_fraction,
        "blink_rate_hz": blink_rate_hz,
    }


def _calibration_row(
    subject: str,
    session_datetime: datetime.datetime,
    eye_value: str,
    *,
    calibration_source: str,
    reason: str = "",
    **overrides,
) -> dict:
    """Every `EyeCalibration` column, defaulted the same way a genuinely
    `refused` row would be (`schema/eye.py::EyeCalibration.make()`'s own
    refusal branches: every affine parameter `None`, every count `0`) --
    `**overrides` is how a test states only the columns its own scenario
    actually needs."""
    row = {
        "subject": subject,
        "session_datetime": session_datetime,
        "eye": eye_value,
        "calibration_source": calibration_source,
        "calibration_model": None,
        "gx_const": None, "gx_dx": None, "gx_dy": None,
        "gx_dx2": None, "gx_dy2": None, "gx_dxdy": None,
        "gy_const": None, "gy_dx": None, "gy_dy": None,
        "gy_dx2": None, "gy_dy2": None, "gy_dxdy": None,
        "validation_error_deg": None,
        "n_points": 0,
        "n_from_calibration_block": 0,
        "n_from_task_fixation": 0,
        "conditioning": None,
        "residual_deg_rms": None,
        "residual_deg_max": None,
        "carried_from_session_datetime": None,
        "reason": reason,
    }
    row.update(overrides)
    return row


def _insert_quality(schema, row: dict) -> None:
    """`allow_direct_insert=True` is DataJoint's own guard for a bare
    `dj.Computed` table outside `populate()` (confirmed directly against
    `datajoint/autopopulate.py`'s `_allow_insert` class default, the same
    citation `tests/schema/test_eye_populate.py`'s own carry-forward test
    gives for the identical override) -- every direct insert into
    `EyeQuality`/`EyeCalibration` in this file goes through this pair of
    wrappers rather than repeating the keyword at each call site, so a
    forgotten copy cannot silently reintroduce `DataJointError: Inserts into
    an auto-populated table can only be done inside its make method...` at
    one call site while every other one was fixed.
    """
    schema.EyeQuality.insert1(row, allow_direct_insert=True)


def _insert_calibration(schema, row: dict) -> None:
    """See `_insert_quality` above -- identical reason, other table."""
    schema.EyeCalibration.insert1(row, allow_direct_insert=True)


def test_the_eye_section_reports_tracking_loss_blink_rate_validation_error_and_residual(
    eye_schema, tmp_path, prefix
):
    """Controller ruling C's first two bullets, both in one place: "tracking
    -loss percentage and blink rate" (`EyeQuality`) and "validation_error_deg,
    and the residual for a fitted map" (`EyeCalibration`)."""
    subject = "erpt0001"
    dt = datetime.datetime(2027, 5, 1, 9, 0)
    _land_session(subject, dt)
    _insert_quality(
        eye_schema,
        _quality_row(subject, dt, "left", tracking_loss_fraction=0.012, blink_rate_hz=0.18),
    )
    _insert_calibration(
        eye_schema,
        _calibration_row(
            subject, dt, "left",
            calibration_source="fitted",
            validation_error_deg=0.42,
            residual_deg_rms=0.30,
            residual_deg_max=0.55,
            n_points=9, n_from_calibration_block=9,
            conditioning=0.83,
        ),
    )
    root = tmp_path / "scratch"
    root.mkdir()

    body = build_report(root, prefix=prefix)

    quality = _subsection(
        _section(body, "Eye"), "Calibration and quality, per session per eye"
    )
    line = _line_for(quality, subject)
    assert "1.2%" in line, f"tracking loss fraction not rendered as a percentage: {line}"
    assert "0.18 Hz" in line, f"blink rate not rendered: {line}"
    assert "0.42" in line, f"validation_error_deg not rendered: {line}"
    assert "0.30" in line and "0.55" in line, f"fitted residual (rms/max) not rendered: {line}"
    assert "fitted" in line


def test_the_calibration_source_breakdown_counts_every_source_distinctly(
    eye_schema, tmp_path, prefix
):
    """Ruling C: "the calibration_source breakdown -- a session running on a
    carried-forward map is working, and that is worth seeing". Four
    DIFFERENT counts (3/1/2/4), deliberately not four 1s: a mutation that
    swaps two sources' counts, or that reports the same number for every
    source regardless of what is actually in the table, would still pass a
    test that used exactly one row per source.

    Computed as a DELTA around this test's own insertions, not as an
    absolute total: `EyeCalibration` carries no window of its own (see
    `report.py::_eye_rows`'s own docstring for why), and this suite's
    database is shared for the whole test session --
    `tests/schema/test_eye_populate.py` inserts real rows into the
    identical table. An absolute-count assertion would be order-dependent
    on whichever other test files happened to run first; a delta around
    this test's own, freshly-landed sessions is not.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    before = _breakdown_counts(
        _subsection(_section(build_report(root, prefix=prefix), "Eye"), "Calibration source")
    )

    dt = datetime.datetime(2027, 5, 2, 9, 0)
    plan = {
        "fitted": ["erpt1001", "erpt1002", "erpt1003"],
        "online": ["erpt1004"],
        "carried_forward": ["erpt1005", "erpt1006"],
        "refused": ["erpt1007", "erpt1008", "erpt1009", "erpt1010"],
    }
    for source, subjects in plan.items():
        for index, subject in enumerate(subjects):
            _land_session(subject, dt)
            overrides: dict = {}
            if source == "refused":
                overrides["reason"] = f"synthetic refusal {index} for the breakdown test"
            else:
                overrides["validation_error_deg"] = 0.5
                if source == "carried_forward":
                    overrides["carried_from_session_datetime"] = dt - datetime.timedelta(days=1)
            _insert_calibration(
                eye_schema,
                _calibration_row(subject, dt, "left", calibration_source=source, **overrides),
            )

    after = _breakdown_counts(
        _subsection(_section(build_report(root, prefix=prefix), "Eye"), "Calibration source")
    )

    assert after["fitted"] - before["fitted"] == 3
    assert after["online"] - before["online"] == 1
    assert after["carried_forward"] - before["carried_forward"] == 2
    assert after["refused"] - before["refused"] == 4


def test_the_online_calibration_ambiguity_is_named_not_silently_complete(eye_schema, tmp_path, prefix):
    """Controller ruling D (task-11 brief): nothing in `EyeCalibration`
    records whether a `.bhv2` was even found, so the breakdown above cannot
    separate "no log present" from "log present, map rejected by
    validation" -- both fold into whichever source a session's calibration
    actually resolved to. Closing that gap is Task 9/10's schema territory,
    not this report's; this pins that the report SAYS so, unconditionally,
    rather than rendering a breakdown that looks complete. No rows need
    inserting -- the note is not conditional on what is in the table.
    """
    root = tmp_path / "scratch"
    root.mkdir()

    body = build_report(root, prefix=prefix)

    breakdown = _subsection(_section(body, "Eye"), "Calibration source")
    assert "`.bhv2`" in breakdown
    assert "cannot separate" in breakdown


def test_a_session_with_no_gaze_names_its_specific_reason(eye_schema, tmp_path, prefix):
    """Ruling C's fourth bullet: "sessions with no canonical gaze, each with
    its specific reason". Also checks the union direction this scenario
    exercises for real: this session has an `EyeCalibration` row (refused)
    but no `EyeQuality` row at all -- exactly what `schema/eye.py::
    EyeCalibration.make()`'s own "no ohDPI recording could be aligned"
    branch produces, since `EyeQuality.key_source` requires a `core.Segment`
    row that, in that branch, does not exist either. The quality-and-
    calibration list must still show this row rather than silently omitting
    it because one of the two tables has nothing for it yet.
    """
    subject = "erpt2000"
    dt = datetime.datetime(2027, 5, 3, 9, 0)
    reason = (
        "no ohDPI recording could be aligned to session time (AcquisitionSystem "
        "recorded this system as present, but no file under it survived "
        "Segment's own alignment scan)"
    )
    _land_session(subject, dt)
    _insert_calibration(
        eye_schema,
        _calibration_row(subject, dt, "left", calibration_source="refused", reason=reason),
    )
    root = tmp_path / "scratch"
    root.mkdir()

    body = build_report(root, prefix=prefix)

    eye_section = _section(body, "Eye")
    no_gaze = _subsection(eye_section, "No canonical gaze")
    line = _line_for(no_gaze, subject)
    assert reason in line

    quality = _subsection(eye_section, "Calibration and quality, per session per eye")
    quality_line = _line_for(quality, subject)
    assert "not yet computed" in quality_line, (
        f"a session with no EyeQuality row at all must say so, not silently omit "
        f"the row: {quality_line}"
    )


def test_two_distinct_refusal_reasons_render_as_two_distinct_lines(eye_schema, tmp_path, prefix):
    """The negative Controller ruling B demands: two sessions refused for
    UNRELATED causes must render as two distinct lines, never collapsed
    into one "no gaze: N" count. This is the test the whole task exists to
    make sure never goes vacuous -- mutation-checked directly (see this
    task's own report): folding both reasons into a bare count, or
    hardcoding one reason string for every refused row, both make this
    fail.
    """
    dt = datetime.datetime(2027, 5, 3, 10, 0)
    subject_a, subject_b = "erpt2001", "erpt2002"
    reason_a = (
        "no ohDPI recording could be aligned to session time for erpt2001's own probe"
    )
    reason_b = (
        "3 fixation epoch(s) named a target, but none fell within an aligned ohDPI "
        "segment (recording coverage gap) for erpt2002"
    )
    _land_session(subject_a, dt)
    _land_session(subject_b, dt)
    _insert_calibration(
        eye_schema,
        _calibration_row(subject_a, dt, "left", calibration_source="refused", reason=reason_a),
    )
    _insert_calibration(
        eye_schema,
        _calibration_row(subject_b, dt, "right", calibration_source="refused", reason=reason_b),
    )
    root = tmp_path / "scratch"
    root.mkdir()

    body = build_report(root, prefix=prefix)

    no_gaze = _subsection(_section(body, "Eye"), "No canonical gaze")
    line_a = _line_for(no_gaze, subject_a)
    line_b = _line_for(no_gaze, subject_b)

    assert reason_a in line_a
    assert reason_b in line_b
    assert line_a != line_b, "two unrelated refusal reasons rendered as the identical line"
    assert reason_a not in line_b
    assert reason_b not in line_a
    # Ruling B's own forbidden shape, checked directly rather than trusted.
    assert "no gaze:" not in body.lower()


def test_a_session_with_quality_but_no_calibration_yet_still_appears(eye_schema, tmp_path, prefix):
    """The union direction the other way: `EyeQuality.key_source` is
    broader than `EyeCalibration.key_source` (design spec section 6 --
    quality needs only an aligned ohDPI recording, calibration also needs
    assembled events; `schema/eye.py::EyeQuality.key_source`'s own
    docstring states the difference directly), so a session can have
    tracking loss and blink rate measured well before it has any
    calibration row at all. A report built from an INTERSECTION of the two
    tables would silently drop this row entirely -- indistinguishable from
    a session with no eye data whatsoever.
    """
    subject = "erpt3001"
    dt = datetime.datetime(2027, 5, 4, 9, 0)
    _land_session(subject, dt)
    _insert_quality(
        eye_schema,
        _quality_row(subject, dt, "left", tracking_loss_fraction=0.05, blink_rate_hz=0.22),
    )
    root = tmp_path / "scratch"
    root.mkdir()

    body = build_report(root, prefix=prefix)

    quality = _subsection(
        _section(body, "Eye"), "Calibration and quality, per session per eye"
    )
    line = _line_for(quality, subject)
    assert "5.0%" in line
    assert "0.22 Hz" in line
    assert "not yet computed" in line, "a session with no EyeCalibration row must say so"


def test_a_successful_calibration_still_surfaces_a_partial_coverage_note(
    eye_schema, tmp_path, prefix
):
    """`schema/eye.py::EyeCalibration.make()`'s own `_combine_reason` stores
    a partial-coverage-drop note in `reason` even when `calibration_source`
    is NOT `refused` -- `result.reason` is `""` on a successful fit, so
    `_combine_reason("", note)` returns `note` alone, landing it in the
    `reason` column of an ordinary `fitted` row. That method's own comment
    calls this worth keeping visible "before it grows into" a real refusal.
    A report that only ever reads `reason` off `refused` rows (the
    "No canonical gaze" list) would silently drop exactly this signal,
    since this row is not refused and would never appear there.
    """
    subject = "erpt4001"
    dt = datetime.datetime(2027, 5, 5, 9, 0)
    note = "2 of 9 fixation windows had no ohDPI coverage"
    _land_session(subject, dt)
    _insert_calibration(
        eye_schema,
        _calibration_row(
            subject, dt, "left",
            calibration_source="fitted",
            validation_error_deg=0.31,
            residual_deg_rms=0.25, residual_deg_max=0.40,
            n_points=7, n_from_calibration_block=7,
            reason=note,
        ),
    )
    root = tmp_path / "scratch"
    root.mkdir()

    body = build_report(root, prefix=prefix)

    quality = _subsection(
        _section(body, "Eye"), "Calibration and quality, per session per eye"
    )
    line = _line_for(quality, subject)
    assert note in line, f"a coverage note on a SUCCESSFUL row was dropped: {line}"
    assert "fitted" in line


def test_a_session_older_than_the_window_is_not_listed_but_is_still_counted(
    eye_schema, tmp_path, prefix
):
    """Fix round (Controller review, task-11 report): the per-session
    listing used to print one line per session per eye for EVERY session
    ever calibrated -- at this lab's two sessions a week that is roughly
    400 lines within a year, in a report generated DAILY, burying a
    refusal or a bad residual under a wall of sessions that were already
    fine yesterday. Windowed to the same 24 h `Ingested` uses (literally
    `readings.ingested`, the identical row set that section itself
    renders -- `report.py`'s own comment where it is reused).

    The `calibration_source` breakdown stays a running total on purpose --
    it is four numbers, it cannot grow without bound the way a per-session
    listing does, and Controller ruling D's persistent-skip detection needs
    a ratio across ALL history, not one night's rows. This test pins BOTH
    halves of that split: the old session is invisible to the listing but
    still present in the breakdown -- proving the breakdown reads
    `EyeCalibration` directly rather than being filtered from whatever the
    (now-windowed) listing happens to show.

    Delta-based for the breakdown half, for the identical pollution reason
    `test_the_calibration_source_breakdown_counts_every_source_distinctly`
    already gives: this suite's database is shared for the whole test
    session.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    before = _breakdown_counts(
        _subsection(_section(build_report(root, prefix=prefix), "Eye"), "Calibration source")
    )

    subject = "erpt6001"
    dt = datetime.datetime(2027, 5, 7, 9, 0)
    old_ingested_at = (
        datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=9)
    )
    _land_session(subject, dt, ingested_at=old_ingested_at)
    _insert_calibration(
        eye_schema,
        _calibration_row(subject, dt, "left", calibration_source="fitted", validation_error_deg=0.2),
    )

    body = build_report(root, prefix=prefix)

    quality = _subsection(
        _section(body, "Eye"), "Calibration and quality, per session per eye"
    )
    assert subject not in quality, (
        f"a session ingested 9 days ago still appears in the 24h-windowed listing: {quality}"
    )

    after = _breakdown_counts(
        _subsection(_section(body, "Eye"), "Calibration source")
    )
    assert after["fitted"] - before["fitted"] == 1, (
        "a session outside the listing's window must still be counted in the "
        "all-time calibration_source breakdown"
    )


def test_each_eye_subsection_states_its_own_scope(eye_schema, tmp_path, prefix):
    """Fix round: a windowed listing sitting above two all-time views under
    one `## Eye` heading are three different scopes, and a reader must not
    have to infer which is which -- `report.py`'s own module docstring
    states that principle already ("a missing section and an empty one must
    never render identically"), and this file already labels scope in its
    OWN headings elsewhere ("## Ingested (24 h)", "## Quarantined (7 d)").
    The three Eye subsections now match that convention; this pins the
    exact labels so a future edit cannot quietly drop one.
    """
    root = tmp_path / "scratch"
    root.mkdir()

    body = build_report(root, prefix=prefix)

    eye_section = _section(body, "Eye")
    assert "\n### Calibration and quality, per session per eye (24 h)" in eye_section
    assert "\n### Calibration source (running total)" in eye_section
    assert "\n### No canonical gaze (7 d)" in eye_section


def test_no_canonical_gaze_windows_to_7_days_independently_of_the_24h_listing(
    eye_schema, tmp_path, prefix
):
    """Second fix round (Controller review): "No canonical gaze" used to be
    all-time. The reviewer's own structural argument is why it is not: a
    refused `EyeCalibration` row is PERMANENT once written (`EyeCalibration.
    key_source`'s own docstring, quoted in `_eye_rows`' own docstring above),
    unlike `_unreclaimed_sessions`' "Archived sessions blocked from
    reclamation" list, whose `blocking()` predicate is re-evaluated fresh
    on every report -- so this is `Quarantine`-shaped, not "Archived
    sessions"-shaped, and windows to the same 7 days `_QUARANTINE_
    WINDOW_DAYS` gives `Quarantine`, not the per-session listing's 24 h.

    Three tiers prove the window is genuinely its OWN 7-day boundary, not
    an accidental reuse of the per-session listing's 24 h one:
    - `subject_fresh` (ingested moments ago): inside both windows.
    - `subject_midweek` (ingested 3 days ago): inside the 7 d "No canonical
      gaze" window, OUTSIDE the 24 h per-session one -- if this list were
      quietly windowing off the same `ingested_keys` the per-session list
      uses, this subject would wrongly disappear here too.
    - `subject_old` (ingested 9 days ago): outside both.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    dt = datetime.datetime(2027, 5, 8, 9, 0)

    subject_fresh, subject_midweek, subject_old = "erpt7001", "erpt7002", "erpt7003"
    _land_session(subject_fresh, dt, ingested_at=now)
    _land_session(subject_midweek, dt, ingested_at=now - datetime.timedelta(days=3))
    _land_session(subject_old, dt, ingested_at=now - datetime.timedelta(days=9))
    for subject in (subject_fresh, subject_midweek, subject_old):
        _insert_calibration(
            eye_schema,
            _calibration_row(
                subject, dt, "left",
                calibration_source="refused",
                reason=f"synthetic refusal for {subject}",
            ),
        )

    body = build_report(root, prefix=prefix)
    eye_section = _section(body, "Eye")

    no_gaze = _subsection(eye_section, "No canonical gaze")
    assert subject_fresh in no_gaze, "a session ingested moments ago is missing from the 7d list"
    assert subject_midweek in no_gaze, (
        "a session ingested 3 days ago is missing from the 7d list -- the no-gaze "
        "window is not really 7 days, or is accidentally reusing the per-session "
        "listing's own 24h boundary"
    )
    assert subject_old not in no_gaze, "a session ingested 9 days ago still appears in the 7d list"

    quality = _subsection(eye_section, "Calibration and quality, per session per eye")
    assert subject_midweek not in quality, (
        "a session ingested 3 days ago appears in the 24h-windowed per-session "
        "listing -- the two windows are not actually different widths"
    )


def test_an_old_refused_session_is_still_counted_with_the_note_saying_so(
    eye_schema, tmp_path, prefix
):
    """The half of the fix request the previous test does not cover: a
    refused row outside the 7 d window must still be counted in the
    all-time `calibration_source` breakdown (nothing is lost, exactly as
    `_eye_rows`' own docstring argues: `no_gaze_all`/`source_counts
    ["refused"]` are the identical predicate over the identical unfiltered
    rows), and the list itself must say a row was hidden rather than
    silently shrinking -- the same "older row(s) not shown" shape
    `Quarantine` already uses (`test_report.py`'s own `older_quarantines`
    assertions).

    Delta-based throughout, for the identical shared-database pollution
    reason `test_the_calibration_source_breakdown_counts_every_source_
    distinctly` already gives -- including for the "older row(s)" note
    itself, which is a single rollup line with no subject in it to search
    for, so a delta is the only way to attribute the change to THIS test's
    own insert rather than to whatever else the shared suite has already
    landed outside the window.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    before_body = build_report(root, prefix=prefix)
    before_eye = _section(before_body, "Eye")
    before_breakdown = _breakdown_counts(_subsection(before_eye, "Calibration source"))
    before_older = _older_no_gaze_count(_subsection(before_eye, "No canonical gaze"))

    subject = "erpt7004"
    dt = datetime.datetime(2027, 5, 9, 9, 0)
    old_ingested_at = (
        datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=9)
    )
    _land_session(subject, dt, ingested_at=old_ingested_at)
    _insert_calibration(
        eye_schema,
        _calibration_row(subject, dt, "left", calibration_source="refused", reason="old refusal"),
    )

    body = build_report(root, prefix=prefix)
    eye_section = _section(body, "Eye")

    no_gaze = _subsection(eye_section, "No canonical gaze")
    assert subject not in no_gaze, "a session ingested 9 days ago still appears in the 7d list"

    after_breakdown = _breakdown_counts(_subsection(eye_section, "Calibration source"))
    assert after_breakdown["refused"] - before_breakdown["refused"] == 1, (
        "a refused row outside the list's window was not counted in the all-time breakdown"
    )

    after_older = _older_no_gaze_count(no_gaze)
    assert after_older - before_older == 1, (
        "the 'older row(s) not shown' rollup did not grow when a row aged out of the window"
    )
