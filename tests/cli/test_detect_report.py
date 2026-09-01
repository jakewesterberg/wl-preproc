# tests/cli/test_detect_report.py
"""The Detection section of the daily report.

Tasks 1-8 populate `EyeValidity`/`EyeDetection` (`wl_preproc/schema/
detect.py`). This file is only about `build_report`'s OWN rendering of rows
already sitting in those two tables -- never about how they got there.
`tests/schema/test_detect_populate.py` already covers `make()` end to end,
expensively, through a real synthetic ohDPI recording; a row inserted
directly here, the same way `tests/cli/test_eye_report.py`'s own
`_insert_quality`/`_insert_calibration` do, is all this file needs.

**Controller correction 1 (task-9 brief), read before the second test
below.** The plan's own draft of `test_two_distinct_refusal_reasons_render_
as_two_distinct_lines` asserted a reason string (`"no ohDPI recording"`) that
belongs to `EyeCalibration`, not to anything `EyeValidity`/`EyeDetection` can
produce -- a session in that state has no aligned ohDPI `core.Segment` at
all, so `EyeValidity.key_source` never names it and no `EyeValidity`/
`EyeDetection` row is ever written for it. The pair the code actually
produces, read directly from `EyeDetection.make()` after Task 7's fix round:
a one-eyed session, where the unusable eye's own trace is refused for its
own `EyeValidity` reason ("no usable calibration, so gaze is undefined" --
`EyeValidity.make()`'s only refusal reason today) and `conjunction` is
refused for a DIFFERENT, specific reason of its own (a conjunction needs
both eyes' detected spans). Two distinct reasons, one session, both
reachable -- never a collapsed "refused: N". This is the test the whole
Detection-refused subsection exists to make sure never goes vacuous.
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from wl_preproc.cli.report import build_report


def _section(body: str, heading: str) -> str:
    """The slice of the report under one `##` heading, and nothing else.

    Duplicated from `tests/cli/test_report.py`'s own helper of the same name
    (already duplicated twice more, in `tests/cli/test_archive_cli.py` and
    `tests/cli/test_eye_report.py`) rather than imported: this repository's
    test layout is deliberately `__init__.py`-free (this project's own
    CLAUDE.md), so test files do not import fixtures or helpers from one
    another.
    """
    marker = f"\n## {heading}"
    assert marker in body, f"no section headed {heading!r} in:\n{body}"
    return body.split(marker, 1)[1].split("\n## ", 1)[0]


def _subsection(section: str, heading: str) -> str:
    """The slice of a `##` section under one of ITS OWN `###` subheadings.
    Mirrors `test_eye_report.py`'s own copy exactly -- see that file's own
    docstring for why an assertion needs this rather than a bare substring
    search of the whole section."""
    marker = f"\n### {heading}"
    assert marker in section, f"no subsection headed {heading!r} in:\n{section}"
    return section.split(marker, 1)[1].split("\n### ", 1)[0]


def _line_for(section: str, needle: str) -> str:
    """The one line of `section` naming `needle`. See `test_report.py`'s own
    copy of this helper for why an assertion needs this rather than a bare
    substring search of the whole section."""
    lines = [line for line in section.splitlines() if needle in line]
    assert len(lines) == 1, f"expected exactly one line naming {needle!r}, got {lines}"
    return lines[0]


def _fraction_for(section: str, label: str) -> float:
    """The percentage `_unusable_fractions` renders for one label, parsed
    back to a bare fraction -- mirrors `test_eye_report.py`'s own
    `_breakdown_counts`, one level down (a percentage string rather than a
    bare integer)."""
    line = _line_for(section, f"{label}:")
    return float(line.rsplit(":", 1)[1].strip().rstrip("%")) / 100


@pytest.fixture(scope="module")
def detect_schema(dj_conn, prefix):
    """A small, module-scoped, locally-activated schema handle plus two real
    `paramset.ParamSet` indices -- mirrors `test_eye_report.py`'s own
    `eye_schema` fixture, extended for the one thing `EyeValidity`/
    `EyeDetection` need that `EyeQuality`/`EyeCalibration` do not: both
    tables carry a real foreign key to `paramset.ParamSet`
    (`wl_preproc/schema/detect.py`'s own `# Key:` comments), so a direct
    insert into either needs an actually-registered paramset row to satisfy
    it, not just a `(subject, session_datetime)` pair.

    `register_default_paramsets()` is idempotent by content hash
    (`paramset.register`'s own docstring), so calling it here is safe
    regardless of whether some other test file in this shared suite already
    called it first -- both calls resolve to the identical index.

    Also activates `ingest` and `timebase`, for the identical reason
    `test_eye_report.py`'s own `eye_schema` fixture does: `_land_session`
    below plants a real `ingest.Ingestion` row (`build_report`'s windowed
    lists need a real `ingested_at` to window against) alongside `pipeline.
    event.BehaviorRecording`/`timebase.TimingProvenance` "done" markers, so
    that row does not sit outstanding for some OTHER test file's own
    `daemon.run_once()` call later in this shared suite's run. See
    `_land_session`'s own docstring for the full reasoning -- copied from
    `test_eye_report.py` because it already found and fixed the real
    four-test failure this exact gap causes.
    """
    from dataclasses import asdict

    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS
    from wl_preproc.schema import detect, ingest, paramset, timebase

    detect.activate(prefix=prefix)
    ingest.activate(prefix=prefix)
    timebase.activate(prefix=prefix)

    detector_paramsets = detect.register_default_paramsets()
    validity_idx = paramset.register("eye_validity", asdict(DEFAULT_VALIDITY_PARAMS))

    return SimpleNamespace(
        module=detect,
        validity_idx=validity_idx,
        detection_idx=detector_paramsets["engbert_kliegl"],
    )


def _land_session(
    subject: str,
    session_datetime: datetime.datetime,
    *,
    ingested_at: datetime.datetime | None = None,
) -> None:
    """A bare `pipeline.Session` row plus a real `ingest.Ingestion` row --
    duplicated from `tests/cli/test_eye_report.py`'s own helper of the same
    name (this project's test layout is deliberately `__init__.py`-free, so
    test files do not import helpers from one another). See that file's own
    docstring for the full reasoning; the short version: `EyeValidity`/
    `EyeDetection` are `dj.Computed` tables whose `key_source` this file
    never exercises (rows are inserted directly below), and the `pipeline.
    event.BehaviorRecording`/`timebase.TimingProvenance` "done" markers exist
    only to keep this bare session invisible to `daemon.run_once()`'s
    event-assembly and timebase stages, which key on nothing but `(pipeline.
    Session & ingest.Ingestion)` -- confirmed the hard way in that file's own
    fix round, not re-derived here.
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


def _validity_row(subject, dt, eye_value, validity_idx, *, n_samples, frac_blink) -> dict:
    """Every `EyeValidity` column, defaulted the same way a genuinely
    `computed` row would be -- all five per-criterion fractions populated,
    since finding M6 (`eye/detect/validity.py::ValidityMask`) made them so.

    The four beside `frac_blink` are `0.0` rather than a made-up spread: no
    test in this file reads them (the report's own numbers come from the
    stored RUNS -- `_detection_rows`' own docstring says why no arithmetic
    on these five columns could produce them), and a planted number nothing
    asserts on is a number a later reader has to work out the meaning of."""
    return {
        "subject": subject,
        "session_datetime": dt,
        "eye": eye_value,
        "paramset_type": "eye_validity",
        "validity_paramset_idx": validity_idx,
        "status": "computed",
        "n_samples": n_samples,
        "frac_blink": frac_blink,
        "frac_out_of_region": 0.0,
        "frac_too_fast": 0.0,
        "frac_frame_gap": 0.0,
        "frac_short_epoch": 0.0,
        "reason": "",
    }


def _validity_run_row(subject, dt, eye_value, validity_idx, run_index, start, stop, label) -> dict:
    return {
        "subject": subject,
        "session_datetime": dt,
        "eye": eye_value,
        "paramset_type": "eye_validity",
        "validity_paramset_idx": validity_idx,
        "run_index": run_index,
        "run_start": start,
        "run_stop": stop,
        "label": label,
    }


def _detection_row(
    subject, dt, trace, validity_idx, detection_idx, *,
    status, n_samples=None, n_saccades=None, n_microsaccades=None, reason="",
) -> dict:
    """Every `EyeDetection` column, defaulted the same way a genuinely
    `refused` row would be (`schema/detect.py::EyeDetection.make()`'s own
    refusal branches: every count left `None`)."""
    return {
        "subject": subject,
        "session_datetime": dt,
        "trace": trace,
        "validity_paramset_type": "eye_validity",
        "validity_paramset_idx": validity_idx,
        "paramset_type": "eye_detection",
        "paramset_idx": detection_idx,
        "status": status,
        "n_samples": n_samples,
        "n_saccades": n_saccades,
        "n_microsaccades": n_microsaccades,
        "reason": reason,
    }


def _insert_validity(schema, row: dict) -> None:
    """`allow_direct_insert=True` is DataJoint's own guard for a bare
    `dj.Computed` table outside `populate()` -- see `test_eye_report.py`'s
    own `_insert_quality`/`_insert_calibration` for the identical reason
    this goes through a wrapper rather than repeating the keyword at each
    call site."""
    schema.EyeValidity.insert1(row, allow_direct_insert=True)


def _insert_validity_run(schema, row: dict) -> None:
    """`EyeValidity.Run` is a `dj.Part`, not itself `dj.Computed`, so
    DataJoint's `allow_direct_insert` guard (which checks `_allow_insert`,
    an attribute only `AutoPopulate` sets) does not apply to it -- confirmed
    directly against `datajoint/table.py`'s own `insert()` before writing
    this helper without the keyword, rather than assumed. Requires the
    matching master row to already exist (`-> master` is a real foreign
    key), so every caller inserts via `_insert_validity` first."""
    schema.EyeValidity.Run.insert1(row)


def _insert_detection(schema, row: dict) -> None:
    """See `_insert_validity` above -- identical reason, sibling table."""
    schema.EyeDetection.insert1(row, allow_direct_insert=True)


def _raw_totals(schema=None, run_rows: list[dict] | None = None):
    """`({"blink": n, "invalid": n}, total)` counted row by row off
    `EyeValidity.Run`, INDEPENDENTLY of `_unusable_fractions` and of the
    database aggregation that feeds it.

    This is the arithmetic the report used to do in Python on every call
    (finding M8). It is kept here, on purpose, as the reference the report's
    own numbers are checked against: an aggregation that quietly computed
    something slightly different from the scan it replaced would be worse
    than the scan, and only a comparison against the old arithmetic can
    catch that. `run_rows` restricts the reference to a subset (one trace's
    rows, say); omitted, it reads the whole part table off `schema`.
    """
    if run_rows is None:
        run_rows = schema.EyeValidity.Run.to_dicts()
    total = sum(row["run_stop"] - row["run_start"] for row in run_rows)
    counts = {"blink": 0, "invalid": 0}
    for row in run_rows:
        if row["label"] in counts:
            counts[row["label"]] += row["run_stop"] - row["run_start"]
    return counts, total


def _plant_mask(schema, subject, dt, eye_value, validity_idx, planted_runs) -> None:
    """One `EyeValidity` master row plus the runs that tile it, from a list
    of `(start, stop, label)` triples. The master's `n_samples` is the last
    run's `run_stop`, so the row and its parts cannot disagree."""
    _insert_validity(
        schema,
        _validity_row(
            subject, dt, eye_value, validity_idx,
            n_samples=planted_runs[-1][1],
            frac_blink=sum(
                stop - start for start, stop, label in planted_runs if label == "blink"
            ) / planted_runs[-1][1],
        ),
    )
    for index, (start, stop, label) in enumerate(planted_runs):
        _insert_validity_run(
            schema,
            _validity_run_row(subject, dt, eye_value, validity_idx, index, start, stop, label),
        )


def test_the_detection_section_reports_counts_per_session(detect_schema, tmp_path, prefix):
    subject = "detr0001"
    dt = datetime.datetime(2027, 6, 10, 9, 0)
    _land_session(subject, dt)
    _insert_detection(
        detect_schema.module,
        _detection_row(
            subject, dt, "left", detect_schema.validity_idx, detect_schema.detection_idx,
            status="computed", n_samples=1000, n_saccades=5, n_microsaccades=2,
        ),
    )
    root = tmp_path / "scratch"
    root.mkdir()

    section = _section(build_report(root, prefix=prefix), "Detection")

    line = _line_for(section, subject)
    # Not a bare substring check on each label alone: `n_saccades=5` and
    # `n_microsaccades=2` are deliberately DIFFERENT numbers, so a mutation
    # that swapped which count feeds which label in the f-string would still
    # satisfy "'saccades' in line and 'microsaccades' in line" -- both words
    # would still be present, just bound to the wrong figure. Checking the
    # number and its own label together is what a swap actually breaks.
    assert "5 saccades" in line, line
    assert "2 microsaccades" in line, line


def test_two_distinct_refusal_reasons_render_as_two_distinct_lines(
    detect_schema, tmp_path, prefix
):
    """Controller correction 1's own shape, carried over from the Eye
    section: a session with no detection is a first-class outcome with a
    STATED reason, and two different reasons must never collapse into one
    count. The two reasons here are copied byte-for-byte from
    `wl_preproc/schema/detect.py`'s own source -- `EyeValidity.make()`'s one
    refusal string, and `EyeDetection.make()`'s single-bad-eye conjunction
    string -- read directly from the file rather than trusted from the
    brief's own paraphrase, per that correction's own instruction.
    """
    subject = "detr0002"
    dt = datetime.datetime(2027, 6, 10, 10, 0)
    _land_session(subject, dt)

    eye_reason = "no usable calibration, so gaze is undefined"
    conjunction_reason = (
        "conjunction needs both eyes' detected spans, and the left eye is "
        "unusable -- see that eye's own trace for its reason"
    )
    _insert_detection(
        detect_schema.module,
        _detection_row(
            subject, dt, "left", detect_schema.validity_idx, detect_schema.detection_idx,
            status="refused", reason=eye_reason,
        ),
    )
    _insert_detection(
        detect_schema.module,
        _detection_row(
            subject, dt, "conjunction", detect_schema.validity_idx, detect_schema.detection_idx,
            status="refused", reason=conjunction_reason,
        ),
    )
    root = tmp_path / "scratch"
    root.mkdir()

    section = _section(build_report(root, prefix=prefix), "Detection")

    assert eye_reason in section
    assert conjunction_reason in section
    assert eye_reason != conjunction_reason
    assert "refused: 2" not in section
    # Not just substring presence: `trace: reason` in THAT order, so a
    # mutation that transposed the two fields in the rendered line (the
    # reason text would still appear verbatim, just before the trace name
    # instead of after it) is still caught.
    assert f"left: {eye_reason}" in section
    assert f"conjunction: {conjunction_reason}" in section


def test_the_invalid_and_blink_fractions_are_shown(detect_schema, tmp_path, prefix):
    """These two numbers are a LOWER bound on unusable samples -- DataQuality
    reports that detection succeeded, never that it was correct -- and the
    report says so rather than presenting them as the whole truth.

    Asserted against the RUNNING-TOTAL subsection specifically, which
    finding M7 moved out from under a bare "Unusable samples" heading and
    put beside a per-session list -- see `test_a_mostly_blink_session_is_
    visible_per_session_rather_than_only_in_the_total` below for the
    per-session half.

    Delta-based, for the identical shared-database pollution reason
    `test_eye_report.py`'s own breakdown tests give: the running total spans
    every `EyeValidity.Run` row this suite's shared database has ever
    written, so an absolute assertion would be order-dependent on whichever
    other test file happened to run first. `counts_before`/`total_before`
    come from `_raw_totals` above, which counts rows itself rather than
    calling `_unusable_fractions` -- so the predicted value below is a
    genuine recomputation from first principles, not a second call to the
    code being checked.
    """
    root = tmp_path / "scratch"
    root.mkdir()

    counts_before, total_before = _raw_totals(detect_schema.module)

    subject = "detr0003"
    dt = datetime.datetime(2027, 6, 10, 11, 0)
    _land_session(subject, dt)
    _insert_validity(
        detect_schema.module,
        _validity_row(subject, dt, "left", detect_schema.validity_idx, n_samples=1000, frac_blink=0.12),
    )
    planted_runs = [(0, 120, "blink"), (120, 350, "invalid"), (350, 1000, "fixation")]
    for index, (start, stop, label) in enumerate(planted_runs):
        _insert_validity_run(
            detect_schema.module,
            _validity_run_row(subject, dt, "left", detect_schema.validity_idx, index, start, stop, label),
        )

    total_after = total_before + 1000
    counts_after = {**counts_before, "blink": counts_before["blink"] + 120,
                    "invalid": counts_before["invalid"] + 230}

    section = _section(build_report(root, prefix=prefix), "Detection")
    unusable = _subsection(section, "Unusable samples, running total")

    assert "invalid" in unusable and "blink" in unusable
    assert "lower bound" in section
    # A distinctive phrase from the note's own prose, not just the heading's
    # "lower bound" wording (which would still be present even if the note
    # sentence itself -- the part that explains WHY it is a lower bound --
    # were dropped entirely).
    assert "signal-quality checks" in section

    for label in ("blink", "invalid"):
        rendered = _fraction_for(unusable, label)
        predicted = counts_after[label] / total_after
        assert rendered == pytest.approx(predicted, abs=1e-3), (
            f"{label} fraction: rendered {rendered}, predicted {predicted} from "
            f"raw {label} run samples over total samples, recomputed independently"
        )


def test_no_agreement_line_exists_in_this_stage(detect_schema, tmp_path, prefix):
    """One detector cannot disagree with anything. A line that always read
    1.00 would look like a measurement, which is worse than an absent one."""
    subject = "detr0004"
    dt = datetime.datetime(2027, 6, 10, 12, 0)
    _land_session(subject, dt)
    _insert_detection(
        detect_schema.module,
        _detection_row(
            subject, dt, "conjunction", detect_schema.validity_idx, detect_schema.detection_idx,
            status="computed", n_samples=500, n_saccades=1, n_microsaccades=0,
        ),
    )
    root = tmp_path / "scratch"
    root.mkdir()

    section = _section(build_report(root, prefix=prefix), "Detection")

    assert "agreement" not in section.lower()


def test_the_four_detection_subsections_state_their_own_scope(detect_schema, tmp_path, prefix):
    """Fix-round shape carried over from the Eye section's own
    `test_each_eye_subsection_states_its_own_scope`: four subsections under
    one heading are four different scopes, and a reader must not have to
    infer which is which. Pins the exact labels so a future edit cannot
    quietly drop one.

    Three became four with finding M7. The two "Unusable samples" headings
    are a per-session 24 h list and an all-time running total over the same
    rows -- the pair most able to be misread as each other, which is why
    each states its own scope in its own heading rather than relying on
    order on the page.
    """
    root = tmp_path / "scratch"
    root.mkdir()

    section = _section(build_report(root, prefix=prefix), "Detection")

    assert "\n### Events per session per trace (24 h)" in section
    assert "\n### Unusable samples per session per eye (lower bound, 24 h)" in section
    assert "\n### Unusable samples, running total across every session (lower bound)" in section
    assert "\n### Detection refused (7 d)" in section


def test_the_events_list_windows_to_24_hours(detect_schema, tmp_path, prefix):
    """`Events per session per trace` is windowed to the same 24 h
    `ingested_keys` the Eye section's own per-session-per-eye list uses --
    mirrors `test_eye_report.py`'s own `test_a_session_older_than_the_
    window_is_not_listed_but_is_still_counted`. A COMPUTED row (not refused):
    the events list filters on `status == "computed"`, so a refused row
    would never appear here regardless of windowing and would not exercise
    this filter at all.
    """
    subject = "detr0005"
    dt = datetime.datetime(2027, 6, 11, 9, 0)
    old_ingested_at = (
        datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=9)
    )
    _land_session(subject, dt, ingested_at=old_ingested_at)
    _insert_detection(
        detect_schema.module,
        _detection_row(
            subject, dt, "left", detect_schema.validity_idx, detect_schema.detection_idx,
            status="computed", n_samples=1000, n_saccades=3, n_microsaccades=1,
        ),
    )
    root = tmp_path / "scratch"
    root.mkdir()

    section = _section(build_report(root, prefix=prefix), "Detection")
    events = _subsection(section, "Events per session per trace")

    assert subject not in events, (
        f"a session ingested 9 days ago still appears in the 24h-windowed events "
        f"list: {events}"
    )


def test_the_refused_list_windows_to_7_days_independently_of_the_24h_events_window(
    detect_schema, tmp_path, prefix
):
    """`Detection refused` windows to 7 days, its OWN boundary, not the
    events list's 24 h one -- mirrors `test_eye_report.py`'s own
    `test_no_canonical_gaze_windows_to_7_days_independently_of_the_24h_
    listing`. Three tiers prove the window is genuinely its own 7-day
    boundary:

    - `fresh` (ingested moments ago): inside the 7 d window.
    - `midweek` (ingested 3 days ago): inside the 7 d window, OUTSIDE the
      events list's 24 h one -- if "Detection refused" quietly reused the
      events list's own 24h keys, this subject would wrongly disappear here
      too.
    - `old` (ingested 9 days ago): outside the 7 d window.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    dt = datetime.datetime(2027, 6, 11, 10, 0)

    subject_fresh, subject_midweek, subject_old = "detr0006", "detr0007", "detr0008"
    _land_session(subject_fresh, dt, ingested_at=now)
    _land_session(subject_midweek, dt, ingested_at=now - datetime.timedelta(days=3))
    _land_session(subject_old, dt, ingested_at=now - datetime.timedelta(days=9))
    for subject in (subject_fresh, subject_midweek, subject_old):
        _insert_detection(
            detect_schema.module,
            _detection_row(
                subject, dt, "left", detect_schema.validity_idx, detect_schema.detection_idx,
                status="refused", reason=f"synthetic refusal for {subject}",
            ),
        )

    refused = _subsection(_section(build_report(root, prefix=prefix), "Detection"), "Detection refused")

    assert subject_fresh in refused, "a session ingested moments ago is missing from the 7d list"
    assert subject_midweek in refused, (
        "a session ingested 3 days ago is missing from the 7d refused list -- the "
        "window is not really 7 days, or is accidentally reusing the events list's "
        "own 24h boundary"
    )
    assert subject_old not in refused, "a session ingested 9 days ago still appears in the 7d list"


def test_an_empty_pipeline_reports_zero_unusable_rather_than_a_wrong_number():
    """`_unusable_fractions` is a PURE function over a list of rows, so the
    `total == 0` branch is reachable without a database at all.

    Task 9's mutation testing left this branch uncovered and its report
    argued the gap needed "either a fragile assumption about collection
    order... or a fully isolated database per test". That is not so, and the
    review round found it: the helper takes a plain `list[dict]`, touches no
    schema, and the empty case is one call. `tests/cli/test_archive_cli.py`
    already imports `_expected_digests` from `wl_preproc.archive.verify` the
    same way, so reaching for a private helper here follows the file's own
    neighbours rather than departing from them.

    The branch matters on a freshly initialised deployment, which is exactly
    when someone reads this section to check the pipeline is working: with no
    `EyeValidity.Run` row written yet, a wrong fallback would render a
    confident unusable percentage out of no data whatsoever.
    """
    from wl_preproc.cli.report import _unusable_fractions

    assert _unusable_fractions([]) == {"blink": 0.0, "invalid": 0.0}


def test_a_mostly_blink_session_is_visible_per_session_rather_than_only_in_the_total(
    detect_schema, tmp_path, prefix
):
    """Finding M7, and the case the number exists for.

    Design spec section 9 asks for "the fraction of **each session's**
    samples labelled `invalid` or `blink`". What shipped was a single
    lifetime running total across every eye of every session ever masked,
    unwindowed -- and a 90%-blink session is invisible inside a year of
    that, which is precisely the session a reader needs to be shown.

    Both halves are asserted: the offending session's own line reads its own
    90%, and the running total beside it reads the DILUTED figure --
    recomputed here off `EyeValidity.Run` directly (`_raw_totals`), so the
    two are shown to be genuinely different measurements rather than the
    same one printed twice. The dilution is guaranteed by this test's own
    second, clean session rather than left to whatever else the shared
    database happens to hold.
    """
    root = tmp_path / "scratch"
    root.mkdir()

    bad, clean = "detr0020", "detr0021"
    bad_dt = datetime.datetime(2027, 6, 10, 20, 0)
    clean_dt = datetime.datetime(2027, 6, 10, 21, 0)
    _land_session(bad, bad_dt)
    _land_session(clean, clean_dt)
    _plant_mask(
        detect_schema.module, bad, bad_dt, "left", detect_schema.validity_idx,
        [(0, 900, "blink"), (900, 1000, "fixation")],
    )
    _plant_mask(
        detect_schema.module, clean, clean_dt, "left", detect_schema.validity_idx,
        [(0, 9000, "fixation")],
    )

    section = _section(build_report(root, prefix=prefix), "Detection")

    per_session = _subsection(section, "Unusable samples per session per eye")
    assert "blink 90.0%" in _line_for(per_session, bad)
    assert "blink 0.0%" in _line_for(per_session, clean)

    counts, total = _raw_totals(detect_schema.module)
    diluted = counts["blink"] / total
    assert diluted < 0.9, (
        "the fixture failed to dilute -- with the running total equal to the "
        "bad session's own fraction this test would pass on the pre-M7 code"
    )
    running = _subsection(section, "Unusable samples, running total")
    assert _fraction_for(running, "blink") == pytest.approx(diluted, abs=1e-3)


def test_the_per_session_unusable_list_windows_to_24_hours(detect_schema, tmp_path, prefix):
    """The per-session list finding M7 added is windowed to the same 24 h
    `ingested_keys` the events list above it uses, and for the same reason
    that list gives: an `EyeValidity` row is permanent once written, so an
    unwindowed per-session listing only ever grows. Without this, M7's fix
    would have traded a number that hides one bad session for a list that
    eventually hides every one of them."""
    root = tmp_path / "scratch"
    root.mkdir()

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    fresh, old = "detr0030", "detr0031"
    fresh_dt = datetime.datetime(2027, 6, 11, 9, 0)
    old_dt = datetime.datetime(2027, 6, 11, 10, 0)
    _land_session(fresh, fresh_dt)
    _land_session(old, old_dt, ingested_at=now - datetime.timedelta(days=3))
    for subject, dt in ((fresh, fresh_dt), (old, old_dt)):
        _plant_mask(
            detect_schema.module, subject, dt, "right", detect_schema.validity_idx,
            [(0, 200, "invalid"), (200, 1000, "fixation")],
        )

    section = _section(build_report(root, prefix=prefix), "Detection")
    per_session = _subsection(section, "Unusable samples per session per eye")

    assert fresh in per_session, "a session ingested moments ago is missing from the 24 h list"
    assert old not in per_session, "a session ingested 3 days ago still appears in the 24 h list"
    # The running total is the unwindowed one, and still sees both.
    running = _subsection(section, "Unusable samples, running total")
    assert _fraction_for(running, "invalid") > 0.0


def _pre_m8_fractions(run_rows: list[dict]) -> dict[str, float]:
    """`_unusable_fractions` exactly as it stood BEFORE finding M8, so the
    equivalence assertions below compare against the code that actually
    shipped rather than a fresh guess at what it did.

    Transcribed from `wl_preproc/cli/report.py` at `ba31f62`, the return
    statement wrapped here to fit and otherwise character for character:

        total = sum(row["run_stop"] - row["run_start"] for row in validity_run_rows)
        counts = {"blink": 0, "invalid": 0}
        for row in validity_run_rows:
            if row["label"] in counts:
                counts[row["label"]] += row["run_stop"] - row["run_start"]
        return {label: (counts[label] / total if total else 0.0)
                for label in ("blink", "invalid")}
    """
    counts, total = _raw_totals(run_rows=run_rows)
    return {label: (counts[label] / total if total else 0.0) for label in ("blink", "invalid")}


def test_the_database_aggregation_matches_the_full_table_scan_it_replaced(
    detect_schema, tmp_path, prefix
):
    """Finding M8, and the assertion that matters more than any speed one.

    `_detection_rows` used to fetch the whole of `EyeValidity.Run` and
    `_unusable_fractions` summed it in a Python loop on every report -- the
    full-table scan design spec section 5 chose runs-as-rows specifically to
    replace. It now aggregates in the database, grouped by key and by label.

    An optimisation that quietly computes something slightly different is
    worse than the scan it replaced, so this checks EQUIVALENCE, not merely
    that the new path returns numbers: `_pre_m8_fractions` above is the
    replaced arithmetic, run here over rows fetched the old way, and the
    aggregated answer must match it -- both for the running total over the
    whole part table and, key by key, for every trace the per-session list
    renders.

    Runs on several sessions with a mix of labels, planted below and added
    to whatever this module's earlier tests already left in the shared
    database, since a single-session single-label fixture would agree under
    almost any grouping mistake.
    """
    root = tmp_path / "scratch"
    root.mkdir()

    plantings = [
        ("detr0040", datetime.datetime(2027, 6, 12, 9, 0), "left",
         [(0, 100, "blink"), (100, 300, "invalid"), (300, 1000, "fixation")]),
        ("detr0040", datetime.datetime(2027, 6, 12, 9, 0), "right",
         [(0, 500, "fixation"), (500, 600, "blink"), (600, 1000, "invalid")]),
        ("detr0041", datetime.datetime(2027, 6, 12, 10, 0), "left",
         [(0, 900, "invalid"), (900, 1000, "blink")]),
        ("detr0041", datetime.datetime(2027, 6, 12, 10, 0), "right",
         [(0, 1000, "fixation")]),
        ("detr0042", datetime.datetime(2027, 6, 12, 11, 0), "left",
         [(0, 2000, "fixation")]),
        ("detr0042", datetime.datetime(2027, 6, 12, 11, 0), "right",
         [(0, 1800, "blink"), (1800, 2000, "fixation")]),
    ]
    for subject, dt, _eye, _runs in plantings:
        _land_session(subject, dt)
    for subject, dt, eye_value, planted_runs in plantings:
        _plant_mask(
            detect_schema.module, subject, dt, eye_value,
            detect_schema.validity_idx, planted_runs,
        )

    from wl_preproc.cli.report import (
        _detection_rows, _unusable_fractions, _unusable_per_eye,
    )

    run_rows = detect_schema.module.EyeValidity.Run.to_dicts()
    _detections, label_totals = _detection_rows(prefix=prefix)

    # The fixture is genuinely several sessions and genuinely a mix.
    by_key: dict[tuple, list[dict]] = {}
    for row in run_rows:
        by_key.setdefault(
            (row["subject"], row["session_datetime"], row["eye"],
             row["paramset_type"], row["validity_paramset_idx"]),
            [],
        ).append(row)
    assert len({key[:2] for key in by_key}) >= 3
    assert {row["label"] for row in run_rows} >= {"blink", "invalid", "fixation"}

    # Whole-table equivalence.
    assert _unusable_fractions(label_totals) == pytest.approx(_pre_m8_fractions(run_rows))

    # Per-key equivalence. The pre-M8 code never grouped, but its arithmetic
    # determines the answer exactly: hand it one trace's rows and it gives
    # that trace's fractions.
    entries = {
        (entry["subject"], entry["session_datetime"], entry["eye"],
         entry["paramset_type"], entry["validity_paramset_idx"]): entry
        for entry in _unusable_per_eye(label_totals)
    }
    assert set(entries) == set(by_key), "the aggregation lost or invented a trace"
    for key, rows in by_key.items():
        expected = _pre_m8_fractions(rows)
        assert entries[key]["blink"] == pytest.approx(expected["blink"]), key
        assert entries[key]["invalid"] == pytest.approx(expected["invalid"]), key
        assert entries[key]["n_samples"] == sum(
            row["run_stop"] - row["run_start"] for row in rows
        ), key


def test_the_aggregation_returns_one_row_per_label_not_one_per_run(
    detect_schema, tmp_path, prefix
):
    """The other half of finding M8: the numbers being right is necessary,
    and doing the work in the database is the point.

    Asserted structurally rather than by timing -- a wall-clock assertion
    would be flaky on a shared container and would not say what it meant.
    A mask planted as many runs comes back as at most one row per distinct
    label, so what `build_report` fetches is bounded by traces times labels
    and not by the number of runs a real mask holds -- which the
    whole-branch review measured at 1,941 for one eye of the reference
    recording. (That figure is the review's own; design spec section 5
    measures `EyeDetection.Run` counts, not these.)
    """
    from wl_preproc.cli.report import _detection_rows

    subject = "detr0050"
    dt = datetime.datetime(2027, 6, 13, 9, 0)
    _land_session(subject, dt)
    # 40 runs alternating blink/fixation: two labels, twenty runs each.
    planted_runs = [
        (index * 10, (index + 1) * 10, "blink" if index % 2 == 0 else "fixation")
        for index in range(40)
    ]
    _plant_mask(
        detect_schema.module, subject, dt, "left",
        detect_schema.validity_idx, planted_runs,
    )

    _detections, label_totals = _detection_rows(prefix=prefix)
    mine = [row for row in label_totals if row["subject"] == subject]

    assert len(mine) == 2, f"expected one row per label, got {len(mine)} for 40 runs"
    assert {row["label"] for row in mine} == {"blink", "fixation"}
    assert {row["samples"] for row in mine} == {200}
