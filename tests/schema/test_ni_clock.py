"""SpikeGLX on the NI's clock, and every other device unfittable until a later
part gives it a way onto it (design spec
`2026-10-18-training-records-and-backup-clock-design.md`, amendments 40, 50
and 51). Every session here is generated, hand-landed and taken through one
daemon pass."""

from __future__ import annotations

import datetime

import pytest

from tests.identities import new_animal, new_serial

# Mid-trial in the resting part of a CI-shaped session, where the task emits
# no code, so the NI's codes across both files still hold every run and the
# NI keeps time (`test_spikeglx_restart.py::RESTART`).
RESTART = {"at_s": 11.0, "gap_s": 0.5}


def _flip_a_payload_word(session_dir, word_index: int) -> None:
    """The NI's code word number `word_index` read as another: its data lines'
    value at the last sample its strobe is high, where the word is read
    (`timebase/channels.py::strobe_words`), has its lowest bit flipped."""
    import numpy as np

    from wl_preproc.events.extract import _NIDQ_STROBE_BIT, extract_nidq_words
    from wl_preproc.timebase._nidq_meta import read_nidq_meta
    from wl_preproc.timebase.clock import nidq_files

    (path,) = nidq_files(session_dir)
    meta = read_nidq_meta(path.with_suffix(".meta"))
    rise, _word = extract_nidq_words(path).words[word_index]
    samples = np.memmap(path, dtype=np.int16, mode="r+").reshape(-1, meta.n_channels)
    strobe = (samples[:, meta.n_analog_channels] >> _NIDQ_STROBE_BIT) & 1
    last = rise
    while strobe[last + 1] == 1:
        last += 1
    samples[last, meta.n_analog_channels + 1] ^= 1
    samples.flush()


class _Landed(dict):
    """Each session by name, with its animal (`animals`) and the daemon
    pass's report (`report`)."""

    animals: dict
    report: dict


def _errors_of(report: dict, key: dict) -> list[str]:
    """A pass's errors naming the session's animal: one module's database
    holds every module's sessions, and the tier-D sessions here may err, so
    only the session's own are read."""
    return [error for error in report["errors"] if key["subject"] in error]


@pytest.fixture(scope="module")
def landed(dj_conn, prefix, tmp_path_factory):
    """Each session by name, `(recipe, truth, key, session_dir)`, after one
    daemon pass: the NI alone, the same run with the sync box beside it, the
    NI with the eye tracker, the NI restarted, and the NI with a sync box log
    that ends inside the last run. Deleted afterwards, as
    `test_session_clock.py` deletes its own."""
    from wl_preproc import daemon
    from wl_preproc.schema import pipeline
    from wl_preproc.synth.recipe import NI_CLOCK_RECIPE, RestartSpec
    from wl_preproc.synth.session import generate_session

    from tests.schema.test_eye_populate import _land
    from tests.timebase.test_clock import _cut_the_sync_box_log

    daemon.activate_all(prefix=prefix)
    serial = new_serial()
    sessions = _Landed()
    sessions.animals = {}

    def land(name: str, **update):
        animal = new_animal()
        sessions.animals[name] = animal
        session = animal.session()
        recipe = NI_CLOCK_RECIPE.model_copy(update={"subject": session.subject, "session_id": session.session_id,
                                                    "probe_serial": serial, **update})
        root = tmp_path_factory.mktemp(name)
        truth = generate_session(root, recipe)
        session_dir = root / recipe.session_id
        if name == "partial":
            _cut_the_sync_box_log(session_dir, 12.0)
        if name == "corrupt":
            # Word 6 is a payload word of the first block's start, so its
            # checksum fails; the run, and every trial, are still there.
            _flip_a_payload_word(session_dir, 6)
        key = _land(root, recipe, session.session_datetime, acquisition_systems=recipe.systems)
        sessions[name] = (recipe, truth, key, session_dir)

    # Drifting, so a time on the NI's clock differs from the session's by
    # more than a sample by the session's end.
    land("ni", system_drift_ppm=(("spikeglx", 18.0),))
    land("synced", systems=("syncbox", "spikeglx"))
    land("ohdpi", systems=("spikeglx", "ohdpi"))
    land("restart", spikeglx_restart=RestartSpec(**RESTART))
    land("partial", systems=("syncbox", "spikeglx"))
    land("corrupt")
    sessions.report = daemon.run_once(prefix=prefix)
    yield sessions
    for _recipe, _truth, key, _session_dir in sessions.values():
        (pipeline.Session & key).delete(prompt=False)


def _clock(key) -> str:
    from wl_preproc.schema import timebase

    return (timebase.SessionClock & key).fetch1("source")


def _assert_failed_tier_d(key, *, rejected: int) -> None:
    """Tier D because something failed, not merely because it is the NI's
    default: the row counts the systems aligned and files rejected, which
    `failed` is made of (`len(aligned) < len(systems) or bool(rejected)`)."""
    from wl_preproc.schema import timebase

    row = (timebase.TimingProvenance & key).fetch1()
    assert row["tier"] == "D"
    assert rejected > 0 and row["n_rejected_segments"] == rejected
    assert row["n_systems_aligned"] == 1  # SpikeGLX, the only system on the NI's clock


def test_the_ni_files_first_sample_is_session_time_zero(landed):
    """Amendment 50: t = 0 is the first NI file's first sample, at its
    `.meta`'s rate, so SpikeGLX is on the identity map; and the sync box's
    identity row is written only where it is the clock."""
    from wl_preproc.schema import core, timebase
    from wl_preproc.timebase._nidq_meta import read_nidq_meta
    from wl_preproc.timebase.clock import nidq_files

    _recipe, _truth, key, session_dir = landed["ni"]
    assert _clock(key) == "nidq"
    (path,) = nidq_files(session_dir)
    rate = read_nidq_meta(path.with_suffix(".meta")).sample_rate_hz

    fit = (timebase.SystemTimebase & key & {"system": "spikeglx"}).fetch1()
    assert (fit["fit_status"], fit["time_source"]) == ("fitted", "clock")
    assert fit["nominal_rate_hz"] == fit["fitted_rate_hz"] == rate
    assert (fit["drift_ppm"], fit["residual_us_rms"], fit["residual_us_max"]) == (0.0, 0.0, 0.0)
    assert not len(timebase.SystemTimebase & key & {"system": "syncbox"})

    segment = (core.Segment & key & {"system": "spikeglx"}).fetch1()
    assert segment["segment_id"] == str(path.relative_to(session_dir / "spikeglx"))
    assert (segment["start_s"], segment["offset_s"], segment["residual_us"], segment["n_barcodes"]) == (
        0.0, 0.0, 0.0, 0)
    assert segment["end_s"] == segment["n_samples"] / rate
    assert not len(core.RejectedSegment & key)


def test_the_probes_are_placed_on_the_ni_clock(landed):
    """The NI's segment is SpikeGLX's run, so its probes are read from it,
    the same sites as the same run on a sync box session's."""
    from wl_preproc.schema import core, ephys

    def census(name):
        _recipe, _truth, key, _session_dir = landed[name]
        (segment_id,) = (core.Segment & key & {"system": "spikeglx"}).to_arrays("segment_id")
        assert (ephys.ProbeCensus & key & {"segment_id": segment_id}).fetch1("n_probes") == 1
        return [(part["stream"], part["probe_serial"], part["part_number"], part["probe_type"],
                 part["electrode_config_hash"], part["problem"], part["imro_table"])
                for part in (ephys.ProbeCensus.Probe & key).to_dicts(order_by="stream")]

    assert _clock(landed["synced"][2]) == "syncbox"
    placed = census("ni")
    assert placed[0][3] is not None  # its sites were placed
    assert placed == census("synced")


def test_the_runs_and_trials_are_on_the_ni_clock(landed):
    """Session time is the NI's own: an event is where the NI sampled it, its
    clock drifting and its first sample the pre-roll before the session's
    start. Truth's times are its code words' (`synth/timeline.py::_emit`
    queues a word behind the one before it), decoded as the events stage
    decodes them. The NI times a word at its strobe's rising edge, where the
    rig asserted it (`timebase/channels.py::strobe_words`)."""
    from wl_preproc.contracts.events import Escape, Marker, PayloadEvent, SimpleEvent, decode_stream
    from wl_preproc.schema import core
    from wl_preproc.synth.spikeglx import NIDQ_SAMPLE_RATE_HZ, SPIKEGLX_PRE_ROLL_S
    from wl_preproc.synth.timeline import apply_drift

    recipe, truth, key, _session_dir = landed["ni"]
    drift_ppm = dict(recipe.system_drift_ppm)["spikeglx"]
    planted = decode_stream(truth.code_words)
    run_starts = [event.time_s for event in planted
                  if isinstance(event, PayloadEvent) and event.escape == Escape.RUN_START]
    trial_starts = [event.time_s for event in planted
                    if isinstance(event, SimpleEvent) and event.code == Marker.TRIAL_START]

    sample = 1.0 / NIDQ_SAMPLE_RATE_HZ

    def on_the_ni(time_s: float) -> float:
        return apply_drift(time_s, drift_ppm) + SPIKEGLX_PRE_ROLL_S

    runs = (core.Run & key).to_dicts(order_by="run_number")
    assert len(runs) == len(run_starts) == len(truth.blocks)
    for run, start_s in zip(runs, run_starts, strict=True):
        assert run["run_start_time"] == pytest.approx(on_the_ni(start_s), abs=sample)
    trials = core.measured_trials(key)
    assert [trial["trial_id"] for trial in trials] == [trial.trial_id for trial in truth.trials]
    assert len(trial_starts) == len(trials)
    for trial, start_s in zip(trials, trial_starts, strict=True):
        assert trial["start_s"] == pytest.approx(on_the_ni(start_s), abs=sample)
    # Drift the NI's clock and the session's differ by more than a sample at
    # the last trial, so a clock read without it would fail here.
    assert on_the_ni(trial_starts[-1]) - (trial_starts[-1] + SPIKEGLX_PRE_ROLL_S) > 2 * sample


def test_a_device_with_no_way_onto_the_ni_clock_is_unfittable(landed):
    """Amendment 51: the eye tracker waits for lab time's host stamps, so
    under the NI it is unfittable, by no mechanism, and its files are
    rejected as an unfitted system's; the session is tier D."""
    from wl_preproc.schema import core, timebase

    _recipe, _truth, key, session_dir = landed["ohdpi"]
    assert _clock(key) == "nidq"
    fit = (timebase.SystemTimebase & key & {"system": "ohdpi"}).fetch1()
    assert (fit["fit_status"], fit["time_source"]) == ("unfittable", "none")
    assert (fit["fitted_rate_hz"], fit["drift_ppm"], fit["residual_us_rms"], fit["residual_us_max"]) == (
        None, None, None, None)
    assert not len(core.Segment & key & {"system": "ohdpi"})
    rejected = (core.RejectedSegment & key & {"system": "ohdpi"}).to_dicts()
    assert rejected and {row["reason"] for row in rejected} == {"unfitted_system"}
    assert (timebase.SystemTimebase & key & {"system": "spikeglx"}).fetch1("fit_status") == "fitted"
    assert len(core.Segment & key & {"system": "spikeglx"}) == 1
    _assert_failed_tier_d(key, rejected=len(rejected))


def test_a_second_ni_file_is_a_restart_and_the_session_is_tier_d(landed):
    """Amendment 40: a later NI file is a restart, not on the NI's clock;
    part 3c joins it. Until then it is rejected, saying so, and the session
    is tier D. The first file, by SpikeGLX's gate and trigger, keeps time,
    whichever its path sorts before."""
    from wl_preproc.schema import core, timebase
    from wl_preproc.timebase.clock import nidq_files

    _recipe, _truth, key, session_dir = landed["restart"]
    assert _clock(key) == "nidq"
    first, second = (str(path.relative_to(session_dir / "spikeglx")) for path in nidq_files(session_dir))
    assert (core.Segment & key & {"system": "spikeglx"}).to_arrays("segment_id").tolist() == [first]
    assert (core.Segment & key & {"system": "spikeglx"}).fetch1("start_s") == 0.0
    assert [(row["file_path"], row["reason"]) for row in (core.RejectedSegment & key).to_dicts()] == [
        (second, "ni_restarted")]
    _assert_failed_tier_d(key, rejected=1)


def test_a_partial_sync_box_log_under_the_ni_is_unfittable_until_3b_3(landed):
    """Amendments 40 and 47: a sync box that landed but whose log ends early
    is 3b-3's cross-check. Until then it has no way onto the NI's clock, and
    the session is tier D."""
    from wl_preproc.schema import core, timebase

    _recipe, _truth, key, _session_dir = landed["partial"]
    assert _clock(key) == "nidq"
    fit = (timebase.SystemTimebase & key & {"system": "syncbox"}).fetch1()
    assert (fit["fit_status"], fit["time_source"]) == ("unfittable", "none")
    assert {row["reason"] for row in (core.RejectedSegment & key & {"system": "syncbox"}).to_dicts()} == {
        "unfitted_system"}
    # SpikeGLX is on the NI's clock, the barcode its NI carries unused.
    spikeglx = (timebase.SystemTimebase & key & {"system": "spikeglx"}).fetch1()
    assert (spikeglx["fit_status"], spikeglx["time_source"]) == ("fitted", "clock")
    assert (core.Segment & key & {"system": "spikeglx"}).fetch1("offset_s") == 0.0
    _assert_failed_tier_d(key, rejected=len(core.RejectedSegment & key))


def test_an_ni_session_whose_codes_hold_every_run_is_tier_n(landed):
    """Tier N: the NI is the clock, nothing failed, its codes decode clean and
    hold every run (amendment 50). It has the one full-code record, no strobe
    witness, and none of what only the sync box measures."""
    from wl_preproc.schema import timebase

    _recipe, _truth, key, _session_dir = landed["ni"]
    assert _clock(key) == "nidq"
    row = (timebase.TimingProvenance & key).fetch1()
    assert row["tier"] == "N"
    assert (row["n_full_code_records"], row["n_strobe_witnesses"], row["decode_errors"]) == (1, 0, 0)
    assert (row["n_rejected_segments"], row["n_systems_aligned"]) == (0, 1)
    assert row["n_barcodes_emitted"] is None
    assert row["event_code_agreement"] is None
    assert row["trial_count_agreement"] is None


def test_an_ni_session_with_a_decode_error_is_tier_d(landed):
    """One flipped payload word is a decode error in the NI's codes, which
    the session carries on the row; nothing failed, so the error alone keeps
    it from N."""
    from wl_preproc.schema import timebase

    _recipe, _truth, key, _session_dir = landed["corrupt"]
    assert _clock(key) == "nidq"
    row = (timebase.TimingProvenance & key).fetch1()
    assert row["decode_errors"] > 0
    assert (row["n_rejected_segments"], row["n_systems_aligned"]) == (0, 1)
    assert row["tier"] == "D"
    assert row["n_barcodes_emitted"] is None


def test_the_listing_gives_an_ni_session_tier_n_and_clock_nidq(landed):
    from wl_preproc.listing.entry import session_entry

    _recipe, _truth, key, _session_dir = landed["ni"]
    entry = session_entry(key)
    assert (entry["tier"], entry["clock"]) == ("N", "nidq")


def test_sync_box_tiers_are_unchanged(landed):
    """A session the sync box times keeps the tier it had before N existed: A
    for the same run beside an NI that carries its own full-code record, its
    agreements measured, its barcode count the box's own."""
    from wl_preproc.schema import timebase

    _recipe, _truth, key, _session_dir = landed["synced"]
    assert _clock(key) == "syncbox"
    row = (timebase.TimingProvenance & key).fetch1()
    assert row["tier"] == "A", row
    assert row["n_barcodes_emitted"] > 0
    assert row["event_code_agreement"] is not None
    assert row["n_full_code_records"] == 2


@pytest.fixture(scope="module")
def ni_nwb(landed, prefix, tmp_path_factory):
    """The NI session's canonical NWB file, built by the daemon: wl.works'
    request over its measured runs, as `test_nwb_build.py` makes one, and a
    pass with the builder configured. Returns `(activation key, NwbFile row,
    the pass's report)`."""
    from wl_preproc import daemon
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core
    from wl_preproc.schema import nwb as nwb_schema

    from tests.schema.test_nwb_build import _montage, _request

    _recipe, _truth, session_key, _session_dir = landed["ni"]
    runs = [{"run_number": row["run_number"], "start_s": row["run_start_time"], "end_s": row["run_stop_time"],
             "works_run_id": f"wr-{row['run_number']}"}
            for row in (core.Run & session_key).to_dicts(order_by="run_number")]
    key = accept(_request(session_key, landed.animals["ni"].key("canonical"), _montage(runs), runs), prefix=prefix)
    report = daemon.run_once(prefix=prefix, nwb_root=tmp_path_factory.mktemp("ni-nwb"))
    rows = (nwb_schema.NwbFile & key).to_dicts()
    assert rows, report["errors"]
    return key, rows[0], report


def test_the_ni_sessions_passes_report_no_error(landed, ni_nwb):
    """Every stage, the NWB builder's included, runs on the NI's session
    without an error: the daemon's report names none of its animal's keys."""
    _recipe, _truth, key, _session_dir = landed["ni"]
    _activation, _row, report = ni_nwb
    assert _errors_of(landed.report, key) == []
    assert _errors_of(report, key) == []


def test_an_ni_session_builds_an_nwb_file_saying_the_ni_kept_time(landed, ni_nwb):
    """Part 3b-1 end to end: the NI-timed session's file is written, its
    timebase module says the NI kept time, its timing tier is N, and where
    its t = 0 came from is one of the four sources."""
    from pynwb import NWBHDF5IO

    from wl_preproc.schema import core

    _recipe, _truth, session_key, _session_dir = landed["ni"]
    _activation, row, _report = ni_nwb
    assert row["status"] == "written", row["reason"] or row["inspector_findings"]
    assert row["reference_source"] in ("barcode", "manifest", "rig_clock", "nidq_meta")
    # The rig's clock was read: the synthetic rig's clock runs in 2027 and the
    # session is dated years before, so the manifest places t = 0, its
    # difference the rig's clock's.
    rig_at_zero = (core.RigClock & session_key).fetch1("rig_at_zero")
    started_at = session_key["session_datetime"].replace(tzinfo=datetime.timezone.utc)
    assert row["reference_source"] == "manifest"
    assert row["started_at_difference_s"] == pytest.approx(started_at.timestamp() - rig_at_zero, abs=1e-6)
    description = row["description"]
    assert description["quality"]["clock"] == "nidq"
    assert description["quality"]["timing_tier"] == "N"
    assert description["quality"]["reference_source"] == row["reference_source"]
    with NWBHDF5IO(row["path"], "r") as handle:
        nwb = handle.read()
        module = nwb.processing["timebase"]
        assert module.description.startswith(
            "How every time in this file was placed on session time: t = 0 is the NI's first sample")
        assert module["timing_provenance"].to_dataframe()["tier"].tolist() == ["N"]
        reference = module["clock_reference"].to_dataframe()
        assert reference["source"].tolist() == [row["reference_source"]]
        assert nwb.session_start_time == row["reference_time"].replace(tzinfo=datetime.timezone.utc)
