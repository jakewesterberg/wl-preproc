"""A session whose sync box restarted partway through (design spec
`2026-10-22-syncbox-records-and-segments-design.md` section 4): its second
segment is on another clock, so nothing in it is mixed into session time. A
daemon pass rejects it with a reason and completes, the other devices are
timed by their own fits, and the session is tier D until part 2 bridges it."""

from __future__ import annotations

import pytest

from tests.identities import new_animal


@pytest.fixture(scope="module")
def restarted(dj_conn, prefix, tmp_path_factory):
    """A `ci` session whose box restarted at 7.5 s for 1.2 s, landed by hand as
    `test_eye_populate.py` lands one, after one daemon pass; deleted after the
    module, so no later module's unrestricted populate meets it."""
    from wl_preproc import daemon
    from wl_preproc.schema import pipeline
    from wl_preproc.synth.recipe import RECIPES, SyncBoxRestartSpec
    from wl_preproc.synth.session import generate_session

    from tests.schema.test_eye_populate import _land

    daemon.activate_all(prefix=prefix)
    root = tmp_path_factory.mktemp("restarted")
    session = new_animal().session()
    recipe = RECIPES["ci"].model_copy(update={"subject": session.subject, "session_id": session.session_id,
                                              "syncbox_restart": SyncBoxRestartSpec(at_s=7.5, gap_s=1.2)})
    generate_session(root, recipe)
    key = _land(root, recipe, session.session_datetime, acquisition_systems=recipe.systems)
    report = daemon.run_once(prefix=prefix)
    yield key, root / recipe.session_id, report
    (pipeline.Session & key).delete(prompt=False)


def test_the_boxs_segment_after_a_restart_is_rejected_with_its_reason(restarted):
    from wl_preproc.schema import core

    key, _, _ = restarted
    rejected = (core.RejectedSegment & key).to_dicts()
    assert [(row["system"], row["file_path"], row["reason"]) for row in rejected] == [
        ("syncbox", "seg-001.log", "no_shared_barcode")]


def test_the_stage_completes_and_every_device_is_timed(restarted):
    """`fit_offset` raised on a segment sharing no barcode with session time,
    and nothing caught it, so the whole stage failed instead of recording why."""
    from wl_preproc.schema import core

    key, _, _ = restarted
    assert not len(core.Segment.jobs & key & "status = 'error'")
    assert {(row["system"], row["file_path"]) for row in (core.Segment & key).to_dicts()} >= {
        ("syncbox", "seg-000.log"), ("bcam", "frames.yaml")}
    assert "spikeglx" in {row["system"] for row in (core.Segment & key).to_dicts()}


def test_a_session_whose_box_restarted_is_tier_d(restarted):
    from wl_preproc.schema import timebase

    key, _, _ = restarted
    assert (timebase.TimingProvenance & key).fetch1("tier") == "D"


def test_each_segment_of_the_box_is_recorded_with_its_role(restarted):
    """`timebase.SyncBoxSegment` (section 6): what each segment's header says,
    its barcodes, and its role. The barcode delay is null, unmeasured and
    uncorrected, until wl-sync writes it into the header (section 5)."""
    from wl_preproc.schema import timebase

    key, _, _ = restarted
    rows = (timebase.SyncBoxSegment & key).to_dicts(order_by="segment_index")
    assert [(row["segment_index"], row["file_path"], row["n_barcodes"], row["first_barcode"], row["last_barcode"],
             row["role"]) for row in rows] == [
        (0, "seg-000.log", 8, 1_000_000, 1_000_007, "session_time"),
        (1, "seg-001.log", 6, 1_000_009, 1_000_014, "after_restart_unbridged")]
    assert {(row["boot_id"][:5], bool(row["clock_trusted"]), row["clock_reason"], row["barcode_delay_us"])
            for row in rows} == {("synth", True, "", None)}


def test_the_report_names_a_session_whose_box_restarted(restarted, prefix):
    from wl_preproc.cli.report import build_report

    from tests.cli.test_report import _declared_count, _line_for, _section

    key, session_dir, _ = restarted
    section = _section(build_report(session_dir.parent, prefix=prefix), "Sync box restarts")
    line = _line_for(section, key["subject"])
    assert "`seg-001.log`" in line and "wait for a bridge" in line
    assert _declared_count(section) == len([row for row in section.splitlines() if row.startswith("- ")])


def test_the_tables_role_values_are_the_segments_roles(restarted, enum_values):
    from wl_preproc.schema import timebase
    from wl_preproc.timebase.segments import SEGMENT_ROLES

    assert enum_values(timebase.SyncBoxSegment.heading.attributes["role"].type) == set(SEGMENT_ROLES)


def _land_restarted(prefix, root, *, spikeglx_restart_at_s=None, drop_first_run=False, extra_segment=None):
    """A `ci` session whose box restarted at 7.5 s for 1.2 s, as `restarted`
    lands one, with SpikeGLX restarted too when `spikeglx_restart_at_s` is
    given (its second run then begins after the box resumed), its first run
    removed when `drop_first_run`, or a further sync box segment written by
    `extra_segment(syncbox_dir)`. Returns `(key, session_dir)`."""
    from wl_preproc.synth.recipe import RECIPES, RestartSpec, SyncBoxRestartSpec
    from wl_preproc.synth.session import generate_session

    from tests.schema.test_eye_populate import _land

    session = new_animal().session()
    update = {"subject": session.subject, "session_id": session.session_id,
              "syncbox_restart": SyncBoxRestartSpec(at_s=7.5, gap_s=1.2)}
    if spikeglx_restart_at_s is not None:
        update["spikeglx_restart"] = RestartSpec(at_s=spikeglx_restart_at_s, gap_s=0.5)
    recipe = RECIPES["ci"].model_copy(update=update)
    generate_session(root, recipe)
    session_dir = root / recipe.session_id
    if drop_first_run:
        for path in (session_dir / "spikeglx").glob(f"{recipe.session_id}[._]*"):
            if "_g1_" not in path.name:
                path.unlink()
    if extra_segment is not None:
        extra_segment(session_dir / "syncbox")
    return _land(root, recipe, session.session_datetime, acquisition_systems=recipe.systems), session_dir


@pytest.fixture(scope="module")
def later_runs(dj_conn, prefix, tmp_path_factory):
    """Two sessions whose SpikeGLX run began after the box resumed (9.7 s):
    one with SpikeGLX's first run too, one with that later run alone; after
    one daemon pass, then deleted."""
    from wl_preproc import daemon
    from wl_preproc.schema import pipeline

    daemon.activate_all(prefix=prefix)
    both = _land_restarted(prefix, tmp_path_factory.mktemp("both"), spikeglx_restart_at_s=9.2)
    alone = _land_restarted(prefix, tmp_path_factory.mktemp("alone"), spikeglx_restart_at_s=9.2,
                            drop_first_run=True)
    report = daemon.run_once(prefix=prefix)
    yield both, alone, report
    for key, _ in (both, alone):
        (pipeline.Session & key).delete(prompt=False)


def test_a_device_recording_begun_after_the_restart_is_rejected_and_the_session_tiered(later_runs):
    """Spec sections 4 and 8: the run is rejected as `no_shared_barcode`, and
    the stage completes: `TimingProvenance` placed every SpikeGLX run with
    `fit_offset` and raised on this one (part 1's final review, its I1)."""
    from wl_preproc.schema import core, timebase

    (key, _), _, report = later_runs
    rejected = {(row["system"], row["reason"]) for row in (core.RejectedSegment & key).to_dicts()
                if "_g1_" in row["file_path"] or row["system"] == "syncbox"}
    assert rejected == {("syncbox", "no_shared_barcode"), ("spikeglx", "no_shared_barcode")}
    assert (timebase.TimingProvenance & key).fetch1("tier") == "D"
    assert not [error for error in report["errors"] if key["subject"] in str(error)]


def test_a_device_whose_only_recording_began_after_the_restart_names_why(later_runs):
    """Not `unfitted_system`, which names a system with too few barcodes, a
    mostly-dead cable (part 1's final review, its I2)."""
    from wl_preproc.schema import core, timebase

    _, (key, _), report = later_runs
    assert {(row["system"], row["reason"]) for row in (core.RejectedSegment & key).to_dicts()} == {
        ("syncbox", "no_shared_barcode"), ("spikeglx", "no_shared_barcode")}
    assert (timebase.TimingProvenance & key).fetch1("tier") == "D"
    assert not [error for error in report["errors"] if key["subject"] in str(error)]


def test_the_report_names_a_restart_whose_later_segment_logged_no_barcode(dj_conn, prefix, tmp_path):
    """A box that crash-looped for the rest of a session logged no barcode
    after its restart: the report still names it (spec section 6, "a line for
    every session whose box restarted"; part 1's final review, its 7)."""
    import datetime

    from wl_sync.log import SCHEMA_VERSION, CodeWord, SyncBoxLogHeader, write_log
    from wl_sync.pins import GPIO_MAP

    from wl_preproc import daemon
    from wl_preproc.cli.report import build_report
    from wl_preproc.schema import pipeline, timebase

    from tests.cli.test_report import _line_for, _section

    def crashed(syncbox_dir):
        header = SyncBoxLogHeader(schema_version=SCHEMA_VERSION, session_id=syncbox_dir.parent.name, rig="rig0",
                                  boot_id="b1", written_at=datetime.datetime(2027, 3, 14, 9, tzinfo=datetime.UTC),
                                  gpio_map=GPIO_MAP, segment_index=2)
        write_log(syncbox_dir / "seg-002.log", header, [CodeWord(tick_us=300_000, word=0x8001)])

    daemon.activate_all(prefix=prefix)
    key, session_dir = _land_restarted(prefix, tmp_path, extra_segment=crashed)
    try:
        timebase.SyncBoxSegment.populate(key)
        section = _section(build_report(session_dir.parent, prefix=prefix), "Sync box restarts")
        assert "`seg-001.log`" in _line_for(section, f"{key['subject']}` @ {key['session_datetime']:%Y-%m-%d %H:%M} — "
                                                      "the sync box restarted: `seg-001.log`")
        assert "logged no barcode" in _line_for(section, "`seg-002.log`")
    finally:
        (pipeline.Session & key).delete(prompt=False)
