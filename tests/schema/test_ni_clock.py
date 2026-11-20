"""SpikeGLX on the NI's clock, and every other device unfittable until a later
part gives it a way onto it (design spec
`2026-10-18-training-records-and-backup-clock-design.md`, amendments 40, 50
and 51). Every session here is generated, hand-landed and taken through one
daemon pass."""

from __future__ import annotations

import pytest

from tests.identities import new_animal, new_serial

# Mid-trial in the resting part of a CI-shaped session, where the task emits
# no code, so the NI's codes across both files still hold every run and the
# NI keeps time (`test_spikeglx_restart.py::RESTART`).
RESTART = {"at_s": 11.0, "gap_s": 0.5}


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
    sessions = {}

    def land(name: str, **update):
        session = new_animal().session()
        recipe = NI_CLOCK_RECIPE.model_copy(update={"subject": session.subject, "session_id": session.session_id,
                                                    "probe_serial": serial, **update})
        root = tmp_path_factory.mktemp(name)
        truth = generate_session(root, recipe)
        session_dir = root / recipe.session_id
        if name == "partial":
            _cut_the_sync_box_log(session_dir, 12.0)
        key = _land(root, recipe, session.session_datetime, acquisition_systems=recipe.systems)
        sessions[name] = (recipe, truth, key, session_dir)

    # Drifting, so a time on the NI's clock differs from the session's by
    # more than a sample by the session's end.
    land("ni", system_drift_ppm=(("spikeglx", 18.0),))
    land("synced", systems=("syncbox", "spikeglx"))
    land("ohdpi", systems=("spikeglx", "ohdpi"))
    land("restart", spikeglx_restart=RestartSpec(**RESTART))
    land("partial", systems=("syncbox", "spikeglx"))
    daemon.run_once(prefix=prefix)
    yield sessions
    for _recipe, _truth, key, _session_dir in sessions.values():
        (pipeline.Session & key).delete(prompt=False)


def _clock(key) -> str:
    from wl_preproc.schema import timebase

    return (timebase.SessionClock & key).fetch1("source")


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
    decodes them. The NI reads a word where its strobe falls, the strobe's
    last high sample (`timebase/channels.py::strobe_falls`), so each is that
    far after the rig asserted it."""
    from wl_preproc.contracts.events import Escape, Marker, PayloadEvent, SimpleEvent, decode_stream
    from wl_preproc.schema import core
    from wl_preproc.synth.rhs import STROBE_WIDTH_S
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
    latched_s = (round(STROBE_WIDTH_S * NIDQ_SAMPLE_RATE_HZ) - 1) * sample

    def on_the_ni(time_s: float) -> float:
        return apply_drift(time_s, drift_ppm) + SPIKEGLX_PRE_ROLL_S + latched_s

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
    assert on_the_ni(trial_starts[-1]) - (trial_starts[-1] + SPIKEGLX_PRE_ROLL_S + latched_s) > 2 * sample


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
    assert (timebase.TimingProvenance & key).fetch1("tier") == "D"


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
    assert (timebase.TimingProvenance & key).fetch1("tier") == "D"


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
    assert (timebase.TimingProvenance & key).fetch1("tier") == "D"
