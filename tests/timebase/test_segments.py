"""Spec section 4.1's alignment table, and what populate writes.

The table's bounds are consequences of the DECODER requiring a preceding idle
of >=400 ms to identify a lead pulse — not of frame geometry. That correction is
recorded in the parent spec because the original numbers were right about the
format and wrong about the thing that would read it.
"""

import pytest

from wl_preproc.timebase.segments import classify_segment


@pytest.mark.parametrize(
    "duration_s, n_barcodes, expected",
    [
        (3.5, 2, "alignable"),  # >=3.0s: two barcodes, local rate verification
        (2.5, 1, "alignable"),  # >=2.0s: one barcode, rate inherited
        (1.5, 0, "too_short"),  # <2.0s: may contain zero
        (5.0, 0, "no_barcode"),  # long enough, but decoded nothing
    ],
)
def test_segment_classification_follows_the_alignment_table(
    duration_s, n_barcodes, expected
):
    """`no_barcode` is distinct from `too_short`: a long file that decoded
    nothing is a different fault (wrong line, wrong bit, dead cable) from a
    file that was never long enough, and collapsing them loses the diagnosis.
    """
    assert classify_segment(duration_s, n_barcodes) == expected


def test_a_short_file_that_did_decode_a_barcode_is_alignable():
    """Section 4.1's durations are GUARANTEES, not requirements: below 2.0 s a
    file *may* contain zero barcodes, not *does*. One that happens to carry one
    can be positioned, and rejecting it for being short would discard a segment
    that is in fact alignable — which is the same error in the opposite
    direction from aligning one that cannot be."""
    assert classify_segment(1.5, 1) == "alignable"


def test_the_bounds_are_derived_from_the_barcode_interval_not_written_down():
    """Parent spec section 4.1's own derivation: a 1 Hz barcode guarantees one
    complete frame in any 1.0 s window, and the decoder's required preceding
    idle means the window must ALSO contain the end of the previous frame —
    which "adds one inter-frame interval to every bound". So 2.0 s is two
    intervals, and the two-barcode bound is three.

    Derived from `INTERVAL_US` rather than written down, so a change to
    wl-sync's barcode cadence fails a test here instead of silently
    invalidating the table. These numbers were already wrong once, and the
    correction is recorded in the parent spec: they were right about the format
    and wrong about the thing that would read it.

    The literals appear only as the values the derivation must currently
    produce, exactly as `min_sample_rate_hz`'s 400.0 does.
    """
    from wl_sync.barcode import IDLE_MIN_US, INTERVAL_US

    from wl_preproc.timebase.segments import (
        MIN_ALIGNABLE_DURATION_S,
        MIN_LOCAL_RATE_DURATION_S,
    )

    interval_s = INTERVAL_US / 1_000_000.0
    assert MIN_ALIGNABLE_DURATION_S == pytest.approx(2 * interval_s)
    assert MIN_LOCAL_RATE_DURATION_S == pytest.approx(3 * interval_s)
    assert MIN_ALIGNABLE_DURATION_S == pytest.approx(2.0)
    assert MIN_LOCAL_RATE_DURATION_S == pytest.approx(3.0)

    # The idle is why the extra interval is there at all. If it ever exceeds an
    # interval, one extra interval stops being enough and the whole table needs
    # re-deriving rather than this constant nudging.
    assert IDLE_MIN_US < INTERVAL_US


# --- Populate. These need a real MySQL, and a real generated session. ---

import datetime  # noqa: E402
from pathlib import Path  # noqa: E402


@pytest.fixture(scope="module")
def populated(dj_conn, prefix, tmp_path_factory):
    """A landed `drift` session, with both populates run.

    Module-scoped: generating the session and populating it is the expensive
    part, and every test below reads the same result rather than re-deriving
    it. Each test that asserts about WRITES takes its own snapshot instead.
    """
    from wl_preproc.schema import core, ingest, pipeline, timebase
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session

    timebase.activate(prefix=prefix)
    ingest.activate(prefix=prefix)

    root = tmp_path_factory.mktemp("populate")
    recipe = RECIPES["drift"]
    generate_session(root, recipe)
    session_dir = root / recipe.session_id

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": recipe.subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    session_key = {
        "subject": recipe.subject,
        "session_datetime": datetime.datetime(2027, 3, 16, 9, 0),
    }
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    ingest.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": datetime.datetime(2027, 3, 16, 19, 0),
            "session_dir": str(session_dir),
            "integrity": "verified",
            "topology": {system: "present" for system in recipe.systems},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    core.AcquisitionSystem.insert(
        [{**session_key, "system": system} for system in recipe.systems],
        skip_duplicates=True,
    )

    timebase.SystemTimebase.populate()
    core.Segment.populate()
    return session_key, recipe, session_dir


def test_every_present_system_gets_a_timebase(populated):
    """One row per system that recorded. A missing row reads as "this system
    was never aligned", so the set equality is the assertion — not a count."""
    from wl_preproc.schema import timebase

    session_key, recipe, _dir = populated

    rows = (timebase.SystemTimebase & session_key).to_dicts()

    assert {row["system"] for row in rows} == set(recipe.systems)


def test_each_systems_stored_drift_is_the_drift_that_was_planted(populated):
    """The populate's own end-to-end check, through the database rather than
    in memory. It is what proves `make()` reads the right files and fits the
    right reference, rather than merely that `fit_rate` works."""
    from wl_preproc.schema import timebase

    session_key, recipe, _dir = populated
    planted = dict(recipe.system_drift_ppm)

    for row in (timebase.SystemTimebase & session_key).to_dicts():
        if row["system"] == "syncbox":
            # Session time IS its timeline, so its fit against itself is
            # exactly identity — the truth, not a placeholder.
            assert row["drift_ppm"] == 0.0
            assert row["fitted_rate_hz"] == row["nominal_rate_hz"]
            continue
        if row["system"] == "ohdpi":
            # `wl_preproc/synth/ohdpi.py` still writes the pre-task-2 guessed
            # format, which the corrected `*.txt` glob correctly does not
            # match -- so `make()` cannot find a file to fit at all and
            # stores `fit_status="no_recording"` with every fit column NULL
            # (nominal_rate_hz included). Asserting a drift against a NULL
            # rate is not "the fit was wrong", it is "there was no fit" --
            # Task 3's fixture rewrite is what makes this system reachable
            # again.
            assert row["fit_status"] == "no_recording"
            continue
        tolerance_ppm = 2.0 * (1.0 / row["nominal_rate_hz"]) / 14.0 * 1e6
        assert row["drift_ppm"] == pytest.approx(
            planted[row["system"]], abs=tolerance_ppm
        ), f"{row['system']}: stored {row['drift_ppm']:.2f} ppm"


def test_a_segment_records_the_native_indices_that_reverse_its_transform(populated):
    """Spec 4.5: fit parameters, residuals and native stream timestamps are
    retained so every transform is reversible and auditable.

    The check is against the SYNC BOX'S LOG, not against the row itself.
    `first_sample` is the native sample index of the segment's first barcode,
    so applying the stored rate and the stored offset to it must land on that
    barcode's session time as the sync box recorded it. A row that merely
    agreed with its own arithmetic would pass any self-referential check while
    being anchored to the wrong instant.
    """
    from wl_preproc.schema import core, timebase
    from wl_preproc.timebase.segments import session_reference

    session_key, _recipe, session_dir = populated
    reference = session_reference(session_dir)

    rows = (core.Segment & session_key).to_dicts()
    assert rows, "no segments were populated at all"

    for row in rows:
        fit = (timebase.SystemTimebase & {**session_key, "system": row["system"]}).fetch1()
        scale = fit["fitted_rate_hz"] / fit["nominal_rate_hz"]
        native_s = row["first_sample"] / fit["nominal_rate_hz"]
        recovered_s = native_s / scale + row["offset_s"]

        expected_s = reference[row["segment_barcode"]]
        # One camera sample period at 500 Hz, the coarsest system here.
        assert recovered_s == pytest.approx(expected_s, abs=2.5e-3), (
            f"{row['system']}: barcode {row['segment_barcode']} recovered at "
            f"{recovered_s:.4f} s, sync box says {expected_s:.4f} s"
        )


def test_a_file_with_no_barcodes_cannot_be_inserted_into_segment(populated):
    """Segment is keyed on segment_barcode, "the first barcode value in the
    segment", so a file yielding zero barcodes has NO KEY and structurally
    cannot go there. An implementation that wants to invent a placeholder
    barcode has found the rule, not a limitation."""
    from wl_preproc.schema import core

    session_key, _recipe, _dir = populated

    assert "segment_barcode" in core.Segment.primary_key
    for row in (core.Segment & session_key).to_dicts():
        assert row["n_barcodes"] >= 1


def test_an_unalignable_file_lands_in_rejected_segment_with_its_reason(
    dj_conn, prefix, tmp_path
):
    """Recorded rather than dropped, so "why is this session short" has an
    answer — 1c-1's own table comment states the point.

    The unalignable file is made by TRUNCATING a real recording to under the
    2.0 s bound, rather than by writing an empty file: a truncated transfer is
    the failure this actually models, and an empty file would not exercise the
    duration rule at all.
    """
    from wl_preproc.schema import core, ingest, pipeline, timebase
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session
    from wl_preproc.timebase.extract import find_recordings

    timebase.activate(prefix=prefix)
    ingest.activate(prefix=prefix)

    recipe = RECIPES["ci"]
    generate_session(tmp_path, recipe)
    session_dir = tmp_path / recipe.session_id

    # One second of a 500 Hz camera sidecar's digital line: under the 2.0 s
    # bound, so unalignable however many barcodes it might have held.
    import yaml

    sidecar_path = find_recordings("bcam", session_dir / "bcam")[0]
    payload = yaml.safe_load(sidecar_path.read_text(encoding="utf-8"))
    keep = 500
    payload["frame_count"] = keep
    payload["digital_line"] = [0] * keep
    payload["video_files"][0]["last_frame_index"] = keep - 1
    sidecar_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": recipe.subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    session_key = {
        "subject": recipe.subject,
        "session_datetime": datetime.datetime(2027, 3, 17, 9, 0),
    }
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    ingest.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": datetime.datetime(2027, 3, 17, 19, 0),
            "session_dir": str(session_dir),
            "integrity": "verified",
            "topology": {system: "present" for system in recipe.systems},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    core.AcquisitionSystem.insert(
        [{**session_key, "system": system} for system in recipe.systems],
        skip_duplicates=True,
    )

    timebase.SystemTimebase.populate()
    core.Segment.populate()

    rejected = (core.RejectedSegment & {**session_key, "system": "bcam"}).to_dicts()
    assert len(rejected) == 1, f"expected one rejection, got {rejected}"
    assert rejected[0]["reason"] == "too_short"
    assert rejected[0]["file_path"] == sidecar_path.name
    # And it is NOT also a segment: the two are exclusive by construction.
    assert not (core.Segment & {**session_key, "system": "bcam"})


def test_populate_writes_only_the_tables_this_phase_owns(
    populated, dj_conn, prefix, table_snapshot, deep_equal
):
    """Proven by row snapshot, NOT by `in_transaction` — which is not a
    read-only check, since DataJoint's insert() calls connection.query()
    directly and never touches the transaction machinery. This project has been
    misled by that assumption five times.

    The plan named "timebase and segment rows"; `RejectedSegment` is the third,
    because `Segment.make()` writes the negative half of its own scan. That is
    a deliberate cross-table write and is exactly the kind of thing this
    snapshot exists to catch, so it is named here rather than left implicit.
    """
    from wl_preproc.schema import core, coverage, ingest, pipeline, timebase

    coverage.activate(prefix=prefix)

    written = {core.Segment, core.RejectedSegment, timebase.SystemTimebase}
    others = [
        core.Montage,
        core.Block,
        core.AcquisitionSystem,
        coverage.BlockCoverage,
        coverage.TrialCoverage,
        ingest.Ingestion,
        ingest.Quarantine,
        pipeline.Session,
    ]
    assert not (written & set(others))

    before = {table.table_name: table_snapshot(table) for table in others}
    timebase.SystemTimebase.populate()
    core.Segment.populate()
    after = {table.table_name: table_snapshot(table) for table in others}

    for name in before:
        assert deep_equal(before[name], after[name]), f"populate wrote to {name}"


def test_a_system_with_one_barcode_rejects_its_files_by_name(dj_conn, prefix, tmp_path):
    """Design spec section 10 names "a system with zero decodable barcodes" as
    a failure path to exercise. The nastier neighbour is ONE: the file itself
    is alignable — it decoded a barcode and could be positioned — but the
    system has no rate to hold fixed, because one point is a position and not a
    slope.

    So the reason is `unfitted_system`, not `no_barcode`: the one file that did
    carry a barcode must not be described as having none, or the diagnosis
    points at a dead cable instead of a nearly-dead one.

    This is also what proves `Segment` keys off `AcquisitionSystem` rather than
    off `SystemTimebase`. Keyed off the fit, a system with no fit would produce
    no rows at all — and the record of why would be the one thing missing.
    """
    import yaml

    from wl_preproc.schema import core, ingest, pipeline, timebase
    from wl_preproc.synth.peripherals import BCAM_PRE_ROLL_S, CAMERA_FPS
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session
    from wl_preproc.timebase.extract import find_recordings
    from wl_preproc.timebase.segments import MIN_ALIGNABLE_DURATION_S

    timebase.activate(prefix=prefix)
    ingest.activate(prefix=prefix)

    recipe = RECIPES["ci"]
    generate_session(tmp_path, recipe)
    session_dir = tmp_path / recipe.session_id

    # Silence the line after the first barcode, leaving the file its full
    # length so the DURATION rule cannot be what rejects it.
    #
    # The cut is the pre-roll plus `MIN_ALIGNABLE_DURATION_S` — the window
    # section 4.1 guarantees exactly one barcode in — used for precisely what
    # it means. It has to be that long: measured, silencing at 1.3 s leaves the
    # frame complete at 1.05 s and still decodes NOTHING, because the decoder
    # needs a trailing interval as well as the leading idle. That symmetry is
    # the same fact the parent spec records as adding one inter-frame interval
    # to every bound, seen from the other end.
    sidecar_path = find_recordings("bcam", session_dir / "bcam")[0]
    payload = yaml.safe_load(sidecar_path.read_text(encoding="utf-8"))
    silence_from = int((BCAM_PRE_ROLL_S + MIN_ALIGNABLE_DURATION_S) * CAMERA_FPS)
    payload["digital_line"] = payload["digital_line"][:silence_from] + [0] * (
        len(payload["digital_line"]) - silence_from
    )
    sidecar_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": recipe.subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    session_key = {
        "subject": recipe.subject,
        "session_datetime": datetime.datetime(2027, 3, 18, 9, 0),
    }
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    ingest.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": datetime.datetime(2027, 3, 18, 19, 0),
            "session_dir": str(session_dir),
            "integrity": "verified",
            "topology": {system: "present" for system in recipe.systems},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    core.AcquisitionSystem.insert(
        [{**session_key, "system": system} for system in recipe.systems],
        skip_duplicates=True,
    )

    timebase.SystemTimebase.populate()
    core.Segment.populate()

    # The premise: exactly one barcode survived, so there is no fit.
    from wl_preproc.timebase import segments as segments_module

    scans = segments_module.scan_system("bcam", session_dir / "bcam")
    assert sum(len(scan.barcodes) for scan in scans) == 1, "the fixture did not bite"
    # A row exists — recording that the fit was ATTEMPTED and failed. An absent
    # row cannot distinguish that from "not reached yet", and DataJoint would
    # re-attempt the key on every pass forever.
    fit = (timebase.SystemTimebase & {**session_key, "system": "bcam"}).fetch1()
    assert fit["fit_status"] == "unfittable"
    assert fit["drift_ppm"] is None, "a zero drift is what a flawless fit looks like"
    assert fit["n_barcodes_decoded"] == 1

    rejected = (core.RejectedSegment & {**session_key, "system": "bcam"}).to_dicts()
    assert [row["reason"] for row in rejected] == ["unfitted_system"]
    assert not (core.Segment & {**session_key, "system": "bcam"})

    # The other systems are unaffected: one bad line is not a bad session.
    assert timebase.SystemTimebase & {**session_key, "system": "spikeglx"}
    assert core.Segment & {**session_key, "system": "spikeglx"}
