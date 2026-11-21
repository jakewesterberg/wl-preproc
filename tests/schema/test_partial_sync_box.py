"""A sync box that recorded part of an NI-timed session (part 3b-3; design spec
`2026-10-18-training-records-and-backup-clock-design.md`, amendments 54 to
64). Under the NI's clock the systems that carry the box's barcodes, the box
itself, the eye tracker and the cameras, are fitted by today's barcode path
against the NI's own barcodes. Every session here is generated, hand-landed
and taken through one daemon pass."""

from __future__ import annotations

import re

import pytest

from tests.identities import new_animal, new_serial
from tests.schema.test_ni_clock import _errors_of

# Drifting, so the eye tracker's and the camera's fits find a rate of their own.
DRIFT_PPM = (("ohdpi", 30.0), ("bcam", -25.0))
# The box stops inside the session, as a Pi that lost power.
STOP_S = 9.0
# The NI starts half a millisecond after the box's first barcode began, and
# before the first code word the run needs; the camera stops a little after
# the second barcode's lead edge, so the first barcode is the one it decodes
# (`wl_sync.barcode.decode_edges` needs an edge past a frame's end).
NI_STARTS_AT_S = 0.0005
CAMERA_STOPS_AT_S = 1.1
# A desync inside the box's part: it opens on the code word at 3.0 s, the last
# the box recorded before it, and the box lost the two words after it, the
# 3.001 s trial number escape and its first payload word, so the next word it
# recorded, at 3.003 s, closes it (wl-sync's note 8).
DESYNC_FROM_S = 3.0
DESYNC_LOST_UNTIL_S = 3.003
# The box's code word, by its number in its log, read as another: the first
# trial's first trial-number payload word, at 0.011 s.
FLIPPED_BOX_WORD = 11
# The last trial number of the box's part, by its number in the box's log: the
# third trial's payload, 3, at 7.003 s, after the escape at 7.001 s. The box's
# last barcode the NI holds is at 8 s, so the words after it in the NI's
# stream are no part of what this word is checked against.
LAST_TRIAL_NUMBER_WORD = 26
# A lost-edge window inside the box's part, on a photodiode line, over the
# code words from 2.998 s to 3.004 s: the box lost edges there, not words.
EDGE_LOSS_S = (2.9, 3.1)
# `holey_ni` (ruling R14): the NI's barcode line is dead over this stretch of
# session time, so its reference holds the barcodes at 0, 1, 2, 7 and 8 s; the
# camera's carries a barcode over the same stretch alone, those at 3 to 6 s.
# Each value lies between the reference's ends, and none is in it.
NI_HOLE_S = (2.5, 6.5)

# The eye sessions (`eyes`, and `synced_eyes` under the box's clock): the
# synthetic stream holds no fixation, so every eye stage of `partial` refuses
# for want of a calibration, and no eye sample is used inside the span or out.
# These add one calibration window every 1.5 s from 0.1 s (truth time), each
# `tests/schema/test_eye_populate.py::_fixation_words`' five code words, its
# target the hold under it at `CAL_SCALE` degrees per raw unit. The gaze holds
# at one of `_HOLDS_PX` through each window and ramps to the next between them
# (`tests/schema/test_detect_populate.py::_ramp_fixations`), so each ramp is a
# saccade. Five windows lie inside the span, which ends at the box's last
# barcode at 8 s; the sixth's fixation straddles its end, and four lie after.
CAL_SCALE = 0.005
_HOLDS_PX = ((0.0, 0.0), (500.0, 0.0), (500.0, 500.0), (0.0, 500.0), (250.0, 250.0))
_WINDOW_STARTS_S = tuple(0.1 + 1.5 * index for index in range(10))
# Each ramp starts 1.1 s into its window's hold, after the fixation's end at
# 0.8 s, and lasts 32 ms in 16 steps, as `test_detect_populate.py`'s first.
_RAMP_AFTER_S = 1.1
_RAMP_S = 0.032
_RAMP_STEPS = 16
# Blinks, both eyes, 40 ms each, clear of every fixation and ramp: two inside
# the span and three after it, so the span's share of loss and its blink rate
# differ from the whole recording's.
_BLINKS_S = (1.0, 4.0, 10.0, 12.0, 13.5)
_BLINK_ROWS = 20
_BLINK_COLUMNS = ("PupilWidth", "PupilHeight", "CR1X", "CR1Y")
# `eyes` only (fix round 1): one more ramp, across the span's end at the box's
# last barcode, 8 s, inside the sixth window's hold, which the span already
# leaves out. Its first half lies inside the span and its second after it.
_EDGE_RAMP_S = 8.0 - _RAMP_S / 2
_EDGE_HOLD_PX = (500.0, 500.0)
# `late_eyes` (fix round 1, ruling R10): only the windows wholly after the
# span, so the box's coverage ended before any calibration window.
_LATE_WINDOWS = tuple(index for index, start_s in enumerate(_WINDOW_STARTS_S) if start_s > 8.0)
# How far, in rows, a stored saccade may start from its planted ramp's first
# row: the detectors find these within a sample or two
# (`test_detect_populate.py`), and the ramps are 750 rows apart.
_RAMP_ROWS_TOLERANCE = 10


def _start_the_ni_late(session_dir, at_s: float) -> None:
    """The NI's file as one SpikeGLX started at session time `at_s`: every
    sample before it gone, at a whole sample, its `.meta` untouched, as
    `tests/timebase/test_clock.py::_cut_the_ni` leaves it."""
    from wl_preproc.synth.spikeglx import NIDQ_SAMPLE_RATE_HZ, SPIKEGLX_PRE_ROLL_S
    from wl_preproc.timebase._nidq_meta import read_nidq_meta
    from wl_preproc.timebase.clock import nidq_files

    (path,) = nidq_files(session_dir)
    n_channels = read_nidq_meta(path.with_suffix(".meta")).n_channels
    first = round((at_s + SPIKEGLX_PRE_ROLL_S) * NIDQ_SAMPLE_RATE_HZ)
    path.write_bytes(path.read_bytes()[first * n_channels * 2:])


def _stop_the_camera(session_dir, at_s: float) -> None:
    """The camera's sidecar as one whose recording stopped at session time
    `at_s`: its frames, its barcode line and its video's last frame cut
    there."""
    import yaml

    from wl_preproc.synth.peripherals import BCAM_PRE_ROLL_S, CAMERA_FPS

    path = session_dir / "bcam" / "frames.yaml"
    sidecar = yaml.safe_load(path.read_text())
    kept = int((at_s + BCAM_PRE_ROLL_S) * CAMERA_FPS)
    sidecar["digital_line"] = sidecar["digital_line"][:kept]
    sidecar["frame_count"] = kept
    sidecar["video_files"][0]["last_frame_index"] = kept - 1
    path.write_text(yaml.safe_dump(sidecar, sort_keys=False))


def _box_tick_us(time_s: float) -> int:
    """Session time `time_s` as the synthetic box's tick: undrifted, after
    `SYNCBOX_PRE_ROLL_S` (`synth/syncbox.py::_write_segment`)."""
    from wl_preproc.synth.syncbox import SYNCBOX_PRE_ROLL_S

    return int(round((time_s + SYNCBOX_PRE_ROLL_S) * 1e6))


def _box_log(session_dir):
    (path,) = sorted((session_dir / "syncbox").glob("seg-*.log"))
    return path


def _lose_the_box_words(session_dir, after_s: float, before_s: float) -> None:
    """The box's `W` records strictly between session times `after_s` and
    `before_s` gone from its log, as a box that lost them leaves it: the
    synthetic writer writes an `L` record but keeps every code word
    (`synth/syncbox.py::_write_segment`), which no box does. Every other line
    as written."""
    path = _box_log(session_dir)
    low, high = _box_tick_us(after_s), _box_tick_us(before_s)
    lines = path.read_text().splitlines(keepends=True)
    kept = [line for line in lines
            if not (line.startswith("W,") and low < int(line.split(",")[1]) < high)]
    assert len(lines) - len(kept) == 2, "the fixture's desync is meant to lose two code words"
    path.write_text("".join(kept))


def _flip_a_box_word(session_dir, word_index: int) -> None:
    """The box's code word number `word_index` read as another, as
    `tests/schema/test_ni_clock.py::_flip_a_payload_word` does the NI's: its
    `W,tick,word` record's word has its lowest bit flipped, every other line
    as written."""
    path = _box_log(session_dir)
    lines = path.read_text().splitlines(keepends=True)
    words = [number for number, line in enumerate(lines) if line.startswith("W,")]
    number = words[word_index]
    kind, tick, word = lines[number].rstrip("\n").split(",")
    lines[number] = f"{kind},{tick},{int(word) ^ 1}\n"
    path.write_text("".join(lines))


def _clear_the_ni_barcode_line(session_dir) -> None:
    """Bit 0 of the NI's digital word 0, its barcode line, cleared on every
    sample, as a dead cable leaves it; its strobe and data lines untouched."""
    import numpy as np

    from wl_preproc.timebase._nidq_meta import read_nidq_meta
    from wl_preproc.timebase.clock import nidq_files

    (path,) = nidq_files(session_dir)
    meta = read_nidq_meta(path.with_suffix(".meta"))
    samples = np.memmap(path, dtype=np.int16, mode="r+").reshape(-1, meta.n_channels)
    samples[:, meta.n_analog_channels] &= ~np.int16(1)
    samples.flush()


def _clear_the_ni_barcode_line_over(session_dir, from_s: float, to_s: float) -> None:
    """`_clear_the_ni_barcode_line`, over session times `from_s` to `to_s`
    alone, as a cable that came loose and back leaves it."""
    import numpy as np

    from wl_preproc.synth.spikeglx import NIDQ_SAMPLE_RATE_HZ, SPIKEGLX_PRE_ROLL_S
    from wl_preproc.timebase._nidq_meta import read_nidq_meta
    from wl_preproc.timebase.clock import nidq_files

    (path,) = nidq_files(session_dir)
    meta = read_nidq_meta(path.with_suffix(".meta"))
    samples = np.memmap(path, dtype=np.int16, mode="r+").reshape(-1, meta.n_channels)
    low, high = (round((at_s + SPIKEGLX_PRE_ROLL_S) * NIDQ_SAMPLE_RATE_HZ) for at_s in (from_s, to_s))
    samples[low:high, meta.n_analog_channels] &= ~np.int16(1)
    samples.flush()


def _keep_the_camera_line_over(session_dir, from_s: float, to_s: float) -> None:
    """The camera's barcode line low on every frame outside session times
    `from_s` to `to_s`, as a cable that worked over that stretch alone."""
    import yaml

    from wl_preproc.synth.peripherals import BCAM_PRE_ROLL_S, CAMERA_FPS

    path = session_dir / "bcam" / "frames.yaml"
    sidecar = yaml.safe_load(path.read_text())
    low, high = (int((at_s + BCAM_PRE_ROLL_S) * CAMERA_FPS) for at_s in (from_s, to_s))
    sidecar["digital_line"] = [bit if low <= index < high else 0
                               for index, bit in enumerate(sidecar["digital_line"])]
    path.write_text(yaml.safe_dump(sidecar, sort_keys=False))


def _kill_the_camera_cable(session_dir) -> None:
    """The camera's barcode line low on every frame, as a dead cable leaves
    it: its frames all there, none carrying a barcode."""
    import yaml

    path = session_dir / "bcam" / "frames.yaml"
    sidecar = yaml.safe_load(path.read_text())
    sidecar["digital_line"] = [0] * len(sidecar["digital_line"])
    path.write_text(yaml.safe_dump(sidecar, sort_keys=False))


def _ramp_starts_s(edge_ramp: bool = False) -> list[float]:
    """Each planted ramp's first instant, in truth time, in order."""
    starts = [start_s + _RAMP_AFTER_S for start_s in _WINDOW_STARTS_S[:-1]]
    return sorted([*starts, _EDGE_RAMP_S]) if edge_ramp else starts


def _eye_fixations(edge_ramp: bool = False) -> tuple:
    """The gaze of the eye sessions: held at `_HOLDS_PX` in turn, one hold
    per calibration window, joined by ramps, from 0 s to the session's end.
    With `edge_ramp`, the sixth window's hold ramps to `_EDGE_HOLD_PX` across
    the span's end and holds there until its own ramp."""
    from wl_preproc.synth.recipe import EyeFixationSpec

    from tests.schema.test_detect_populate import _ramp_fixations

    fixations = []
    held_from_s = 0.0
    for index, start_s in enumerate(_WINDOW_STARTS_S):
        here = _HOLDS_PX[index % len(_HOLDS_PX)]
        if index == len(_WINDOW_STARTS_S) - 1:
            fixations.append(EyeFixationSpec(start_s=held_from_s, end_s=15.0, x_px=here[0], y_px=here[1]))
            break
        ramp_s = start_s + _RAMP_AFTER_S
        if edge_ramp and held_from_s < _EDGE_RAMP_S < ramp_s:
            fixations.append(EyeFixationSpec(start_s=held_from_s, end_s=_EDGE_RAMP_S, x_px=here[0], y_px=here[1]))
            fixations.extend(_ramp_fixations(_EDGE_RAMP_S, here, _EDGE_HOLD_PX, _RAMP_S, _RAMP_STEPS))
            held_from_s, here = _EDGE_RAMP_S + _RAMP_S, _EDGE_HOLD_PX
        fixations.append(EyeFixationSpec(start_s=held_from_s, end_s=ramp_s, x_px=here[0], y_px=here[1]))
        fixations.extend(_ramp_fixations(ramp_s, here, _HOLDS_PX[(index + 1) % len(_HOLDS_PX)], _RAMP_S,
                                         _RAMP_STEPS))
        held_from_s = ramp_s + _RAMP_S
    return tuple(fixations)


def _write_calibration_windows(session_dir, recipe, truth, windows=None) -> None:
    """The session's code words with one calibration window per
    `_WINDOW_STARTS_S` (or those numbered in `windows`), written where the rig
    strobes them: the NI's data lines and the box's log, which keeps only
    what it recorded before its stop (`synth/syncbox.py::write_syncbox_log`)."""
    import dataclasses

    from wl_preproc.synth.spikeglx import write_nidq
    from wl_preproc.synth.syncbox import write_syncbox_log

    from tests.schema.test_eye_populate import _fixation_words

    extra = []
    for index, start_s in enumerate(_WINDOW_STARTS_S):
        if windows is not None and index not in windows:
            continue
        x_px, y_px = _HOLDS_PX[index % len(_HOLDS_PX)]
        extra.extend(_fixation_words(start_s, (CAL_SCALE * x_px, CAL_SCALE * y_px)))
    words = tuple(sorted((*truth.code_words, *extra), key=lambda pair: pair[0]))
    written = dataclasses.replace(truth, code_words=words)
    write_syncbox_log(session_dir / "syncbox" / "seg-000.log", recipe, written)
    write_nidq(session_dir / "spikeglx", recipe, written,
               drift_ppm=dict(recipe.system_drift_ppm).get("spikeglx", recipe.drift_ppm))


def _plant_blinks(session_dir) -> None:
    """A blink in both eyes at each `_BLINKS_S`, as OpenIrisDPI writes one
    (`tests/schema/test_eye_loss_populate.py`): the pupil and P1 at 0 for
    `_BLINK_ROWS` frames, `DataQuality` left at 100."""
    from wl_preproc.synth.ohdpi import HEADER

    from tests.schema.test_detect_populate import _first_row_at

    (path,) = (session_dir / "ohdpi").glob("*.txt")
    header, *rows = path.read_text(encoding="utf-8").splitlines()
    columns = [HEADER.index(f"{eye}{name}") for eye in ("Left", "Right") for name in _BLINK_COLUMNS]
    for time_s in _BLINKS_S:
        first = _first_row_at(time_s)
        for row in range(first, first + _BLINK_ROWS):
            fields = rows[row].split(" ")
            for column in columns:
                fields[column] = "0.0000"
            rows[row] = " ".join(fields)
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")


class _Landed(dict):
    report: dict

    def key(self, name):
        return self[name][2]

    def truth(self, name):
        return self[name][1]

    def session_dir(self, name):
        return self[name][3]


@pytest.fixture(scope="module")
def landed(dj_conn, prefix, tmp_path_factory):
    """Each session by name, `(recipe, truth, key, session_dir)`, after one
    daemon pass. Each is the NI with the sync box, the eye tracker and a
    camera: `partial`, the box stopping at `STOP_S`; `before_ni`, the same
    with the NI started after the box's first barcode and the camera stopped
    before its third, so the camera's barcodes all precede the NI; `dead_ni_line`,
    the same as `partial` with the NI's barcode line dead; `dead_cable`, the
    same as `partial` with the camera's barcode line dead; `box_loss`, the
    same as `partial` with a desync inside the box's part and the two code
    words it lost gone from the box's log; `disagree`, the same as `partial`
    with one of the box's code words read as another; `last_trial`, the same
    with the part's last trial number read as another; `edge_loss`, the same
    with a photodiode edge lost inside the box's part; `synced`, the box
    running the whole session and keeping time; and `eyes` and
    `synced_eyes`, `partial` and `synced` with calibration windows, ramps
    and blinks in the eye recording (`_eye_fixations`), `eyes` with one more
    ramp across the span's end; and `late_eyes`, `partial` with only the
    calibration windows after the span; `camera_only`, `partial` with no
    eye tracker; and `holey_ni`, `partial` with the NI's barcode line dead
    over `NI_HOLE_S` and the camera's live over it alone. Deleted
    afterwards."""
    from wl_sync.pins import GPIO_MAP

    from wl_preproc import daemon
    from wl_preproc.schema import pipeline
    from wl_preproc.synth.recipe import NI_CLOCK_RECIPE, SyncBoxLossSpec
    from wl_preproc.synth.session import generate_session

    from tests.schema.test_eye_populate import _land

    daemon.activate_all(prefix=prefix)
    serial = new_serial()
    sessions = _Landed()
    sessions.animals = {}

    def land(name: str, **update):
        animal = new_animal()
        sessions.animals[name] = animal
        session = animal.session()
        recipe = NI_CLOCK_RECIPE.model_copy(update={
            "subject": session.subject, "session_id": session.session_id, "probe_serial": serial,
            "systems": ("syncbox", "spikeglx", "ohdpi", "bcam"), "system_drift_ppm": DRIFT_PPM, **update})
        root = tmp_path_factory.mktemp(name)
        truth = generate_session(root, recipe)
        session_dir = root / recipe.session_id
        if name == "before_ni":
            _start_the_ni_late(session_dir, NI_STARTS_AT_S)
            _stop_the_camera(session_dir, CAMERA_STOPS_AT_S)
        if name == "dead_ni_line":
            _clear_the_ni_barcode_line(session_dir)
        if name == "dead_cable":
            _kill_the_camera_cable(session_dir)
        if name == "holey_ni":
            _clear_the_ni_barcode_line_over(session_dir, *NI_HOLE_S)
            _keep_the_camera_line_over(session_dir, *NI_HOLE_S)
        if name == "box_loss":
            _lose_the_box_words(session_dir, DESYNC_FROM_S, DESYNC_LOST_UNTIL_S)
        if name == "disagree":
            _flip_a_box_word(session_dir, FLIPPED_BOX_WORD)
        if name == "last_trial":
            _flip_a_box_word(session_dir, LAST_TRIAL_NUMBER_WORD)
        if name in ("eyes", "synced_eyes"):
            _write_calibration_windows(session_dir, recipe, truth)
            _plant_blinks(session_dir)
        if name == "late_eyes":
            _write_calibration_windows(session_dir, recipe, truth, windows=_LATE_WINDOWS)
        key = _land(root, recipe, session.session_datetime, acquisition_systems=recipe.systems)
        sessions[name] = (recipe, truth, key, session_dir)

    land("partial", syncbox_stop_s=STOP_S)
    land("before_ni", syncbox_stop_s=STOP_S)
    land("dead_ni_line", syncbox_stop_s=STOP_S)
    land("dead_cable", syncbox_stop_s=STOP_S)
    land("holey_ni", syncbox_stop_s=STOP_S)
    land("box_loss", syncbox_stop_s=STOP_S,
         syncbox_losses=(SyncBoxLossSpec(cause="desync", count=2, from_s=DESYNC_FROM_S),))
    land("disagree", syncbox_stop_s=STOP_S)
    land("last_trial", syncbox_stop_s=STOP_S)
    land("edge_loss", syncbox_stop_s=STOP_S, syncbox_losses=(SyncBoxLossSpec(
        cause="buffer_overflow", count=3, from_s=EDGE_LOSS_S[0], to_s=EDGE_LOSS_S[1], gpio=GPIO_MAP["pd1_comp"]),))
    land("synced")
    land("eyes", syncbox_stop_s=STOP_S, eye_fixations=_eye_fixations(edge_ramp=True))
    land("synced_eyes", eye_fixations=_eye_fixations())
    land("late_eyes", syncbox_stop_s=STOP_S, eye_fixations=_eye_fixations())
    land("camera_only", syncbox_stop_s=STOP_S, systems=("syncbox", "spikeglx", "bcam"),
         system_drift_ppm=(("bcam", dict(DRIFT_PPM)["bcam"]),))
    sessions.report = daemon.run_once(prefix=prefix)
    yield sessions
    for _recipe, _truth, key, _session_dir in sessions.values():
        (pipeline.Session & key).delete(prompt=False)


def _clock(key) -> str:
    from wl_preproc.schema import timebase

    return (timebase.SessionClock & key).fetch1("source")


def _fit(key, system) -> dict:
    from wl_preproc.schema import timebase

    return (timebase.SystemTimebase & key & {"system": system}).fetch1()


def test_the_eye_tracker_and_camera_are_fitted_through_the_nis_barcodes(landed):
    """Amendment 56: each is fitted by its own barcodes against the NI's, and
    each segment's offset carries the true time of its first barcode on the
    device onto the true time the NI sampled it, to one of the device's
    frames: a system aligned by barcode is precise to one frame period, 2 ms
    at 500 Hz (`schema/timebase.py`'s `_TIME_SOURCE_ENUM` note; design spec
    section 3.1), which is why `tests/timebase/test_fit.py` holds the
    cameras' offsets to 2.5 ms. A camera whose clock runs slow places every
    barcode after the first a frame early, so the fit's offset there is off
    by more than 100 us however well it is made."""
    from wl_preproc.schema import core
    from wl_preproc.synth.ohdpi import OHDPI_PRE_ROLL_S
    from wl_preproc.synth.peripherals import BCAM_PRE_ROLL_S
    from wl_preproc.synth.spikeglx import SPIKEGLX_PRE_ROLL_S
    from wl_preproc.synth.timeline import apply_drift
    from wl_preproc.timebase import segments

    key = landed.key("partial")
    assert _clock(key) == "nidq"
    truth_s = dict(landed.truth("partial").barcodes)
    drift_ppm = dict(DRIFT_PPM)
    for system, pre_roll_s in (("ohdpi", OHDPI_PRE_ROLL_S), ("bcam", BCAM_PRE_ROLL_S)):
        fit = _fit(key, system)
        assert (fit["fit_status"], fit["time_source"]) == ("fitted", "barcode"), system
        assert fit["n_barcodes_matched"] == len(truth_s)
        rate = segments.rate_fit_from_row(fit)
        (scan,) = segments.scan_system(system, landed.session_dir("partial") / system)
        first = min(scan.barcodes, key=lambda barcode: barcode.start_us)
        segment = (core.Segment & key & {"system": system}).fetch1()
        native_s = apply_drift(truth_s[first.value], drift_ppm[system]) + pre_roll_s
        assert native_s / rate.scale + segment["offset_s"] == pytest.approx(
            truth_s[first.value] + SPIKEGLX_PRE_ROLL_S, abs=1.0 / fit["nominal_rate_hz"]), system
    assert not len(core.RejectedSegment & key)


def test_the_sync_box_is_fitted_through_the_nis_barcodes(landed):
    """Amendment 56: under the NI the box is fitted like any system carrying
    its barcodes, not given the identity row of the system that keeps time.
    Its tick 0 is `SYNCBOX_PRE_ROLL_S` before session time's, and the NI's
    first sample `SPIKEGLX_PRE_ROLL_S` before it."""
    from wl_preproc.schema import core
    from wl_preproc.synth.spikeglx import NIDQ_SAMPLE_RATE_HZ, SPIKEGLX_PRE_ROLL_S
    from wl_preproc.synth.syncbox import SYNCBOX_PRE_ROLL_S

    key = landed.key("partial")
    fit = _fit(key, "syncbox")
    assert (fit["fit_status"], fit["time_source"]) == ("fitted", "barcode")
    assert fit["n_barcodes_matched"] == len(landed.truth("partial").barcodes)
    # Neither the box nor the NI drifts, so what is measured is the NI's
    # sampling: less than one of its samples across the box's barcodes.
    covered_s = max(truth_s for _value, truth_s in landed.truth("partial").barcodes)
    assert abs(fit["drift_ppm"]) * 1e-6 * covered_s < 1.0 / NIDQ_SAMPLE_RATE_HZ
    segment = (core.Segment & key & {"system": "syncbox"}).fetch1()
    assert segment["offset_s"] == pytest.approx(SPIKEGLX_PRE_ROLL_S - SYNCBOX_PRE_ROLL_S, abs=100e-6)
    assert segment["n_barcodes"] == len(landed.truth("partial").barcodes)
    # SpikeGLX stays the clock, on the identity map.
    assert (_fit(key, "spikeglx")["fit_status"], _fit(key, "spikeglx")["time_source"]) == ("fitted", "clock")


def test_a_partial_sync_box_session_is_tier_n_and_records_its_span(landed):
    """Amendments 55, 60 and 61: nothing failed, the box's own losses are not
    asked, and the span is stored as the clock computes it."""
    from wl_preproc.schema import timebase
    from wl_preproc.timebase.clock import clock_for

    key = landed.key("partial")
    row = (timebase.TimingProvenance & key).fetch1()
    assert row["tier"] == "N", row
    assert (row["n_systems_aligned"], row["n_rejected_segments"]) == (4, 0)
    assert row["n_barcodes_emitted"] is None
    span = clock_for(key, landed.session_dir("partial")).covered_span()
    assert span is not None
    assert row["covered_start_s"] == pytest.approx(span[0], abs=1e-9)
    assert row["covered_end_s"] == pytest.approx(span[1], abs=1e-9)


def test_a_file_whose_barcodes_precede_the_ni_is_outside_and_does_not_fail(landed):
    """Amendment 57: the camera's one barcode was sent before the NI's first
    sample, so none is in the NI's reference. Its file is
    `outside_covered_span`, its system unfittable and left out, and the
    session is still N."""
    from wl_preproc.schema import core, timebase
    from wl_preproc.timebase import segments
    from wl_preproc.timebase.clock import clock_for

    key = landed.key("before_ni")
    session_dir = landed.session_dir("before_ni")
    assert _clock(key) == "nidq"
    reference = clock_for(key, session_dir).barcode_reference()
    (scan,) = segments.scan_system("bcam", session_dir / "bcam")
    assert scan.barcodes and not any(barcode.value in reference for barcode in scan.barcodes)
    start_s, _end_s = clock_for(key, session_dir).covered_span()
    first_value = min(barcode.value for barcode in scan.barcodes)
    assert dict(landed.truth("before_ni").barcodes)[first_value] < NI_STARTS_AT_S

    fit = _fit(key, "bcam")
    assert (fit["fit_status"], fit["time_source"]) == ("unfittable", "barcode")
    assert [row["reason"] for row in (core.RejectedSegment & key).to_dicts()] == ["outside_covered_span"]
    for system in ("syncbox", "spikeglx", "ohdpi"):
        assert _fit(key, system)["fit_status"] == "fitted", system
    row = (timebase.TimingProvenance & key).fetch1()
    assert row["tier"] == "N", row
    assert (row["n_systems_aligned"], row["n_rejected_segments"]) == (3, 1)
    assert row["covered_start_s"] == pytest.approx(start_s, abs=1e-9)


def test_an_empty_reference_places_nothing_by_barcode(landed):
    """Decision 2: with the NI's barcode line dead its reference is empty, so
    nothing is placed by barcode, even though every device decoded the box's
    barcodes. Each is unfittable by no mechanism, its files an unfitted
    system's, never outside the span, and the session is D."""
    from wl_preproc.schema import core, timebase
    from wl_preproc.timebase.clock import clock_for

    key = landed.key("dead_ni_line")
    assert _clock(key) == "nidq"
    assert clock_for(key, landed.session_dir("dead_ni_line")).barcode_reference() == {}
    for system in ("syncbox", "ohdpi", "bcam"):
        fit = _fit(key, system)
        assert (fit["fit_status"], fit["time_source"]) == ("unfittable", "none"), system
        assert fit["n_barcodes_decoded"] > 0, system
        reasons = {row["reason"] for row in (core.RejectedSegment & key & {"system": system}).to_dicts()}
        assert reasons == {"unfitted_system"}, system
    row = (timebase.TimingProvenance & key).fetch1()
    assert row["tier"] == "D"
    assert row["n_systems_aligned"] == 1
    assert (row["covered_start_s"], row["covered_end_s"]) == (None, None)


def test_under_the_sync_box_nothing_changes(landed):
    """A session the box times keeps every field this part could touch: the
    box's identity row, every other system fitted by barcode, no file
    rejected, the tier it had, and no span."""
    from wl_preproc.schema import core, timebase

    key = landed.key("synced")
    assert _clock(key) == "syncbox"
    box = _fit(key, "syncbox")
    assert (box["fit_status"], box["time_source"]) == ("fitted", "barcode")
    assert (box["drift_ppm"], box["residual_us_rms"], box["residual_us_max"]) == (0.0, 0.0, 0.0)
    for system in ("spikeglx", "ohdpi", "bcam"):
        fit = _fit(key, system)
        assert (fit["fit_status"], fit["time_source"]) == ("fitted", "barcode"), system
    assert not len(core.RejectedSegment & key)
    row = (timebase.TimingProvenance & key).fetch1()
    assert row["tier"] == "A", row
    assert row["n_full_code_records"] == 2
    assert row["n_barcodes_emitted"] > 0
    assert (row["covered_start_s"], row["covered_end_s"]) == (None, None)


def test_a_dead_barcode_cable_under_the_ni_fails_the_session(landed):
    """Amendment 57: a file that decoded no barcode at all stays `no_barcode`
    and fails the session, though the NI holds barcodes. A camera whose cable
    was dead and one that ran wholly after the box stopped look the same, and
    neither is left out as outside the span."""
    from wl_preproc.schema import core, timebase
    from wl_preproc.timebase.clock import clock_for

    key = landed.key("dead_cable")
    assert _clock(key) == "nidq"
    assert clock_for(key, landed.session_dir("dead_cable")).barcode_reference()
    fit = _fit(key, "bcam")
    assert (fit["fit_status"], fit["time_source"]) == ("unfittable", "barcode")
    assert fit["n_barcodes_decoded"] == 0
    assert [row["reason"] for row in (core.RejectedSegment & key).to_dicts()] == ["no_barcode"]
    # Not left out: everything else was placed and the NI's codes are clean,
    # so the camera is the only thing D can be for.
    for system in ("syncbox", "spikeglx", "ohdpi"):
        assert _fit(key, system)["fit_status"] == "fitted", system
    row = (timebase.TimingProvenance & key).fetch1()
    assert (row["n_systems_aligned"], row["n_rejected_segments"], row["decode_errors"]) == (3, 1, 0)
    assert row["tier"] == "D", row


def test_a_file_inside_a_hole_in_the_nis_barcodes_is_not_outside_and_fails(landed):
    """Ruling R14 (amendment 76): "outside" is decided by range, not by
    membership. The camera's barcodes lie between the NI reference's first
    and last values, none of them in it, as under a hole in the NI's decode:
    the file ran inside the span, so it is not left out as outside it. It is
    `no_shared_barcode`, and fails the session."""
    from wl_preproc.schema import core, timebase
    from wl_preproc.timebase import segments
    from wl_preproc.timebase.clock import clock_for

    key = landed.key("holey_ni")
    session_dir = landed.session_dir("holey_ni")
    assert _clock(key) == "nidq"
    reference = clock_for(key, session_dir).barcode_reference()
    (scan,) = segments.scan_system("bcam", session_dir / "bcam")
    # The fixture is what it says: values between the reference's ends, none in it.
    values = [barcode.value for barcode in scan.barcodes]
    assert values and not set(values) & set(reference)
    assert all(min(reference) < value < max(reference) for value in values)

    fit = _fit(key, "bcam")
    assert (fit["fit_status"], fit["time_source"]) == ("unfittable", "barcode")
    assert [row["reason"] for row in (core.RejectedSegment & key).to_dicts()] == ["no_shared_barcode"]
    for system in ("syncbox", "spikeglx", "ohdpi"):
        assert _fit(key, system)["fit_status"] == "fitted", system
    row = (timebase.TimingProvenance & key).fetch1()
    assert (row["n_systems_aligned"], row["n_rejected_segments"]) == (3, 1)
    assert row["tier"] == "D", row


def test_the_passes_report_no_error(landed):
    """Every stage runs on these sessions without an error: the pass's report
    holds no error but those naming another session, a stage-wide one
    included."""
    for name in ("partial", "before_ni", "box_loss", "disagree", "last_trial", "edge_loss", "synced"):
        assert _errors_of(landed.report, landed.key(name)) == [], name


def test_a_partial_box_that_agrees_records_its_agreement(landed):
    """Amendment 59, as the plan's decision 3 reads it: the box's code words
    over what it recorded are all found, in order, in the NI's, so the
    containment is kept as the agreement, with two full-code records. The
    box's words after its last barcode the NI holds, and every word after it
    stopped, are no part of it: `code_agreement` over the whole of both
    streams would read the words the box never recorded as disagreement."""
    from wl_preproc.events import agreement
    from wl_preproc.schema import timebase
    from wl_preproc.timebase.clock import clock_for, nidq_codes, nidq_files

    key = landed.key("partial")
    session_dir = landed.session_dir("partial")
    row = (timebase.TimingProvenance & key).fetch1()
    assert row["tier"] == "N", row
    assert row["n_full_code_records"] == 2
    assert row["event_code_agreement"] == pytest.approx(1.0)
    assert row["decode_errors"] == 0
    # The fixture is what it says: the box recorded part of the NI's stream.
    part = timebase._box_words_recorded(session_dir, clock_for(key, session_dir).barcode_reference())
    whole = [word for _time_s, word in nidq_codes(nidq_files(session_dir)[0])]
    assert part and len(part) < len(whole)
    assert agreement.code_agreement(part, whole) < agreement.AGREEMENT_THRESHOLD


def test_a_box_loss_inside_its_part_does_not_fail_the_cross_check(landed):
    """Amendments 59 and 60: a desync the next code word closes, inside the
    box's part and inside a measured run, which under the sync box's clock
    fails the timing check. Under the NI its window is out of the
    cross-check, and the box's losses are reported, never failing: the
    session is N, the box's words agreeing."""
    from wl_preproc.schema import timebase
    from wl_preproc.timebase import segments

    key = landed.key("box_loss")
    session_dir = landed.session_dir("box_loss")
    assert _clock(key) == "nidq"
    (desync,) = [loss for loss in segments.read_syncbox_session(session_dir).losses() if loss.cause == "desync"]
    assert (desync.start_s, desync.stop_s) == pytest.approx((DESYNC_FROM_S, DESYNC_LOST_UNTIL_S), abs=1e-6)
    (stored,) = (timebase.SyncBoxSegment.Loss & key).to_dicts()
    assert stored["cause"] == "desync"
    row = (timebase.TimingProvenance & key).fetch1()
    assert row["tier"] == "N", row
    assert row["n_full_code_records"] == 2
    assert row["event_code_agreement"] == pytest.approx(1.0)


def test_a_box_whose_codes_disagree_is_d(landed):
    """Amendment 59: one of the box's words over its part is not the NI's, so
    fewer than `AGREEMENT_THRESHOLD` of them are found in the NI's stream and
    the session is D. The box's own decode error does not count: the tier's
    `decode_errors` stays the NI's, none."""
    from wl_preproc.events import agreement
    from wl_preproc.schema import timebase

    key = landed.key("disagree")
    assert _clock(key) == "nidq"
    row = (timebase.TimingProvenance & key).fetch1()
    assert row["tier"] == "D", row
    assert row["n_full_code_records"] == 2
    assert row["event_code_agreement"] < agreement.AGREEMENT_THRESHOLD
    assert row["decode_errors"] == 0
    assert (row["n_systems_aligned"], row["n_rejected_segments"]) == (4, 0)


def test_a_box_whose_last_trial_number_disagrees_is_d(landed):
    """Amendment 59's "both streams are cut" (fix round 1, review I1 and M7):
    the box's last trial number is read as another. Against the NI's whole
    session, the matcher's last segment would run on into everything the NI
    recorded after the box's part ended, and find the wrong word there. Cut to
    the box's span, the NI's stream holds nothing for it to hide in, and the
    session is D."""
    from wl_preproc.contracts.events import Escape
    from wl_preproc.events import agreement
    from wl_preproc.schema import timebase
    from wl_preproc.timebase import segments

    key = landed.key("last_trial")
    session_dir = landed.session_dir("last_trial")
    # The fixture is what it says: the flipped word is the payload of the
    # part's last trial-number escape.
    box = segments.read_syncbox_session(session_dir)
    codes = box.codes()
    high = max(box.reference().values())
    escapes = [index for index, (time_s, word) in enumerate(codes)
               if word == Escape.TRIAL_NUMBER and time_s <= high]
    assert escapes[-1] + 2 == LAST_TRIAL_NUMBER_WORD
    row = (timebase.TimingProvenance & key).fetch1()
    assert row["tier"] == "D", row
    assert row["n_full_code_records"] == 2
    assert row["event_code_agreement"] < agreement.AGREEMENT_THRESHOLD
    assert row["decode_errors"] == 0


def test_an_edge_window_does_not_cut_the_part(landed):
    """Fix round 1 (R9): only a loss of code words (`path` W, a gap among
    them) cuts the box's part. A lost photodiode edge loses no code word, so
    the part keeps every word across its window, as many as `partial`'s."""
    from wl_preproc.schema import timebase
    from wl_preproc.timebase import segments
    from wl_preproc.timebase.clock import clock_for

    key = landed.key("edge_loss")
    session_dir = landed.session_dir("edge_loss")
    box = segments.read_syncbox_session(session_dir)
    (loss,) = box.losses()
    assert loss.path == "E"
    assert (loss.start_s, loss.stop_s) == pytest.approx(EDGE_LOSS_S, abs=1e-6)
    inside = [word for time_s, word in box.codes() if EDGE_LOSS_S[0] < time_s < EDGE_LOSS_S[1]]
    assert len(inside) == 7
    part = timebase._box_words_recorded(session_dir, clock_for(key, session_dir).barcode_reference())
    honest = timebase._box_words_recorded(
        landed.session_dir("partial"), clock_for(landed.key("partial"), landed.session_dir("partial"))
        .barcode_reference())
    assert len(part) == len(honest) == 28
    row = (timebase.TimingProvenance & key).fetch1()
    assert row["tier"] == "N", row
    assert row["event_code_agreement"] == pytest.approx(1.0)


# -- Eye data only inside the span (amendment 58) ------------------------------


def _span(landed, name) -> tuple[float, float]:
    from wl_preproc.timebase.clock import clock_for

    return clock_for(landed.key(name), landed.session_dir(name)).covered_span()


def _eye_recording(landed, name):
    """The eye tracker's segment, its file, and every row's session time,
    re-derived here rather than through `eye.row_session_times`: sample `k`
    at `start_s + k * (end_s - start_s) / n_samples`, a row's sample read off
    its frame counter."""
    import numpy as np

    from wl_preproc.eye.ohdpi import read_ohdpi
    from wl_preproc.schema import core

    segment = (core.Segment & landed.key(name) & {"system": "ohdpi"}).fetch1()
    path = landed.session_dir(name) / "ohdpi" / segment["segment_id"]
    numbers = read_ohdpi(path).frame_numbers
    step_s = (segment["end_s"] - segment["start_s"]) / segment["n_samples"]
    return segment, path, segment["start_s"] + (numbers - numbers[0]).astype(np.float64) * step_s


def _windows(landed, name) -> list[tuple[float, float]]:
    """Each calibration window's `(FIXATION_ACQUIRED, FIXATION_END)` in
    session time, as the session's clock decodes its codes."""
    from wl_preproc.contracts.events import TaskEvent
    from wl_preproc.timebase.clock import clock_for

    codes = clock_for(landed.key(name), landed.session_dir(name)).codes()
    acquired = [time_s for time_s, word in codes if word == int(TaskEvent.FIXATION_ACQUIRED)]
    ended = [time_s for time_s, word in codes if word == int(TaskEvent.FIXATION_END)]
    return list(zip(acquired, ended, strict=True))


def test_no_eye_stage_uses_a_sample_past_the_span(landed):
    """Amendment 58, under the NI with a box that stopped at `STOP_S` while the
    eye tracker ran on: a calibration window outside the span is not one, the
    mask withholds every sample outside it, and so no detected event, and no
    saccade the main sequence takes, lies outside it."""
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.schema import detect, eye, main_sequence, paramset

    key = landed.key("eyes")
    assert _clock(key) == "nidq"
    start_s, end_s = _span(landed, "eyes")
    segment, _path, times = _eye_recording(landed, "eyes")
    # The fixture is what it says: the eye tracker recorded well past the span,
    # and some windows lie wholly after it.
    assert segment["end_s"] > end_s + 5.0
    windows = _windows(landed, "eyes")
    inside = [window for window in windows if start_s <= window[0] and window[1] <= end_s]
    assert len(inside) == 5 and len([window for window in windows if window[0] > end_s]) == 4

    for row in (eye.EyeCalibration & key).to_dicts():
        assert row["calibration_source"] == "fitted", row
        assert row["n_points"] == len(inside), row["eye"]
    residuals = (eye.EyeCalibration.BlockResidual & key).to_dicts()
    assert residuals and sum(row["n_points"] for row in residuals) == 2 * len(inside)

    outside = (times < start_s) | (times > end_s)
    assert outside.any() and not outside.all()
    validity = (detect.EyeValidity & key).to_dicts()
    assert validity and all(row["status"] == "computed" for row in validity)
    for row in validity:
        assert row["n_samples"] == len(times)
        for run in (detect.EyeValidity.Run & row).to_dicts():
            if outside[run["run_start"]:run["run_stop"]].any():
                assert run["label"] == Label.INVALID.value, run

    detections = (detect.EyeDetection & key & 'status = "computed"').to_dicts()
    assert detections
    masked = {Label.BLINK.value, Label.INVALID.value, Label.LOST.value}
    saccades = 0
    for row in detections:
        for run in (detect.EyeDetection.Run & row).to_dicts():
            if run["label"] in masked:
                continue
            assert start_s <= times[run["run_start"]] and times[run["run_stop"] - 1] <= end_s, run
            saccades += run["label"] == Label.SACCADE.value
    assert saccades > 0

    for row in (main_sequence.SaccadeMainSequence & key).to_dicts():
        detection = {name: row[name] for name in detect.EyeDetection.primary_key}
        params = main_sequence.MainSequenceParams(**(paramset.ParamSet & {
            "paramset_type": row["fit_paramset_type"], "paramset_idx": row["fit_paramset_idx"]}).fetch1("params"))
        runs = (detect.EyeDetection.Run & detection & 'label in ("saccade", "microsaccade")').to_dicts(
            order_by="run_index")
        taken = [run for run in main_sequence.selected_runs(runs, row["fs_hz"], params)
                 if start_s <= times[run["run_start"]] <= end_s]
        assert row["n_saccades"] == len(taken), row


def test_eye_quality_counts_only_the_span(landed):
    """Amendment 58: tracking loss is the share of the frames inside the span
    the tracker lost, and the blink rate the blinks inside it over the seconds
    of recording inside it. Two of the five planted blinks lie inside."""
    import numpy as np

    from wl_preproc.eye.ohdpi import read_tracker_loss
    from wl_preproc.schema import eye

    key = landed.key("eyes")
    start_s, end_s = _span(landed, "eyes")
    segment, path, times = _eye_recording(landed, "eyes")
    inside = (times >= start_s) & (times <= end_s)
    seconds = min(segment["end_s"], end_s) - max(segment["start_s"], start_s)
    rows = {row["eye"]: row for row in (eye.EyeQuality & key).to_dicts()}
    assert set(rows) == {"left", "right"}
    for eye_value, file_eye in (("left", "Left"), ("right", "Right")):
        loss = read_tracker_loss(path, file_eye)
        lost = loss.lost | loss.unreadable
        blinks = int(np.sum(np.diff(np.concatenate(([0], loss.blink[inside].astype(np.int8), [0]))) == 1))
        assert blinks == 2
        assert rows[eye_value]["tracking_loss_fraction"] == pytest.approx(lost[inside].sum() / inside.sum())
        assert rows[eye_value]["blink_rate_hz"] == pytest.approx(blinks / seconds)
        # The fixture is what it says: the whole recording reads otherwise.
        assert lost.sum() / lost.size != pytest.approx(lost[inside].sum() / inside.sum())
        assert 5 / (segment["end_s"] - segment["start_s"]) != pytest.approx(blinks / seconds)


def _rows_of(tables, session_key) -> dict[str, list[dict]]:
    return {table.full_table_name: (table & session_key).to_dicts() for table in tables}


def _same_rows(made: list[dict], stored: list[dict], primary_key) -> None:
    """Every row `make()` would insert, against what is stored, matched by
    primary key, floats to a tolerance."""
    def by_key(rows):
        return {tuple(str(row[name]) for name in primary_key): row for row in rows}

    made_rows, stored_rows = by_key(made), by_key(stored)
    assert made_rows.keys() == stored_rows.keys()
    for name, row in made_rows.items():
        for column, value in row.items():
            expected = stored_rows[name][column]
            if isinstance(expected, float) or isinstance(value, float):
                assert value == pytest.approx(expected, rel=1e-12, abs=1e-12, nan_ok=True), (name, column)
            else:
                assert value == expected or str(value) == str(expected), (name, column, value, expected)


def test_under_the_sync_box_the_eye_stages_are_unchanged(landed, monkeypatch):
    """Under the box's clock the span is None, and every eye table's stored
    rows equal those its `make()` gives through the span's own path with a
    span that holds the whole recording: nothing is cut, so nothing changes."""
    import math

    import datajoint as dj

    from wl_preproc.schema import detect, eye, main_sequence, pipeline
    from wl_preproc.timebase.clock import Clock

    key = landed.key("synced_eyes")
    assert _clock(key) == "syncbox"
    assert _span(landed, "synced_eyes") is None
    calibrations = (eye.EyeCalibration & key).to_dicts()
    assert calibrations and all(row["calibration_source"] == "fitted" for row in calibrations)
    assert len(detect.EyeDetection & key & 'status = "computed"')

    stages = (
        (eye.EyeCalibration, (eye.EyeCalibration, eye.EyeCalibration.BlockResidual)),
        (eye.EyeQuality, (eye.EyeQuality,)),
        (detect.EyeValidity, (detect.EyeValidity, detect.EyeValidity.Run)),
        (detect.EyeDetection, (detect.EyeDetection, detect.EyeDetection.Run, detect.EyeDetection.Source)),
        (main_sequence.SaccadeMainSequence, (main_sequence.SaccadeMainSequence,
                                             main_sequence.SaccadeMainSequence.Block,
                                             main_sequence.SaccadeMainSequence.Condition)),
    )
    session_key = {name: key[name] for name in pipeline.Session.primary_key}
    stored = {name: rows for _stage, tables in stages for name, rows in _rows_of(tables, session_key).items()}
    made: dict[str, list[dict]] = {name: [] for name in stored}

    def capture(table, rows, **_kwargs):
        made.setdefault(table.full_table_name, []).extend(dict(row) for row in rows)

    monkeypatch.setattr(Clock, "covered_span", lambda _self: (-math.inf, math.inf))
    monkeypatch.setattr(dj.Table, "insert", capture)
    # The keys each stage stored, so a key whose `make()` raised in the pass
    # is not compared against the nothing it stored.
    for stage, _tables in stages:
        for stage_key in (stage.key_source & session_key & stage.proj()).keys():
            stage().make(stage_key)
    for _stage, tables in stages:
        for table in tables:
            name = table.full_table_name
            _same_rows(made[name], stored[name], table.primary_key)


def test_coverage_stops_at_the_span(landed):
    """Ruling R5 (amendment 58): a system the NI's barcodes placed is covered
    only over the span, so a run wholly after it shows no eye or camera
    coverage, though their segments' extents run on past it. SpikeGLX, the
    clock, still covers it."""
    from wl_preproc.schema import core, coverage

    key = landed.key("partial")
    _start_s, end_s = _span(landed, "partial")
    assert (core.Segment & key & {"system": "ohdpi"}).fetch1("end_s") > end_s + 5.0
    runs = [run for run in (core.Run & key).to_dicts() if run["run_start_time"] > end_s]
    assert runs
    for run in runs:
        run_key = {name: run[name] for name in core.Run.primary_key}
        for system in ("ohdpi", "bcam", "syncbox"):
            row = (coverage.RunCoverage & run_key & {"system": system}).fetch1()
            assert (row["coverage"], row["covered_s"]) == ("absent", 0.0), system
        assert (coverage.RunCoverage & run_key & {"system": "spikeglx"}).fetch1("coverage") == "full"
    trials = [row for row in (core.TrialTime & key).to_dicts() if row["start_s"] > end_s]
    assert trials
    for trial in trials:
        row = (coverage.TrialCoverage & key & {"trial_id": trial["trial_id"], "system": "ohdpi"}).fetch1()
        assert (row["coverage"], row["covered_s"]) == ("absent", 0.0)


def test_the_eye_sessions_report_no_error(landed):
    """The eye stages run on both eye sessions without an error."""
    for name in ("eyes", "synced_eyes"):
        assert _errors_of(landed.report, landed.key(name)) == [], name


# -- Fix round 1 ------------------------------------------------------------------


def test_a_box_that_stopped_before_every_calibration_window_refuses_and_names_the_span(landed):
    """Ruling R10: every fixation epoch named a target, but all lie after the
    span, so the refusal says the box's coverage ended before any of them.
    `resolve_calibration`'s "no fixation epoch named a target position" would
    point at the task code for a fault that is the sync box's."""
    from wl_preproc.schema import eye

    key = landed.key("late_eyes")
    start_s, end_s = _span(landed, "late_eyes")
    windows = _windows(landed, "late_eyes")
    assert len(windows) == len(_LATE_WINDOWS) and all(window[0] > end_s for window in windows)
    rows = (eye.EyeCalibration & key).to_dicts()
    assert {row["eye"] for row in rows} == {"left", "right"}
    for row in rows:
        assert row["calibration_source"] == "refused", row
        assert row["n_points"] == 0
        assert "no fixation epoch named a target position" not in row["reason"], row["reason"]
        assert f"{len(windows)} fixation epoch(s) named a target" in row["reason"], row["reason"]
        assert "covered span" in row["reason"] and f"{end_s:.3f}" in row["reason"], row["reason"]


def _ramp_rows(edge_ramp: bool) -> list[int]:
    """Each planted ramp's first row in the eye file, as `synth/ohdpi.py::
    write_ohdpi` places a hold: the frame of its truth time on the tracker's
    drifted clock after its pre-roll. No frame is dropped, so frame is row."""
    from wl_preproc.synth.ohdpi import OHDPI_FPS, OHDPI_PRE_ROLL_S
    from wl_preproc.synth.timeline import apply_drift

    drift_ppm = dict(DRIFT_PPM)["ohdpi"]
    return [int((apply_drift(start_s, drift_ppm) + OHDPI_PRE_ROLL_S) * OHDPI_FPS)
            for start_s in _ramp_starts_s(edge_ramp)]


def _per_eye_traces(key):
    """Each computed per-eye detection row with its detector's name."""
    from wl_preproc.schema import detect, paramset

    for row in (detect.EyeDetection & key & 'status = "computed"' & 'trace in ("left", "right")').to_dicts():
        params = (paramset.ParamSet & {"paramset_type": row["paramset_type"],
                                       "paramset_idx": row["paramset_idx"]}).fetch1("params")
        yield params["detector"], row


def test_each_stored_saccade_lies_on_its_planted_ramp(landed):
    """Fix round 1 (review I2): a detector reads the span's rows alone, so its
    runs are placed back among the recording's rows by the span's first row,
    which on `eyes` is about 350 rows in. Each stored saccade of a degree or
    more starts within `_RAMP_ROWS_TOLERANCE` rows of a planted ramp's first
    row. Every per-eye trace holds at least one, and Engbert-Kliegl's finds
    each ramp wholly inside the span: a shift dropped or doubled misplaces
    every event by hundreds of rows. Only Engbert-Kliegl is held to every
    ramp: NSLR's right eye calls the 1.8 degree ramp at row 3900 no saccade,
    which is its classifier's business, not this test's."""
    from wl_preproc.schema import detect

    key = landed.key("eyes")
    start_s, end_s = _span(landed, "eyes")
    _segment, _path, times = _eye_recording(landed, "eyes")
    first_row, stop_row = int((times < start_s).sum()), int((times <= end_s).sum())
    assert first_row > 100
    ramps = _ramp_rows(edge_ramp=True)
    inside = [row for row in ramps if first_row < row and row + _RAMP_STEPS < stop_row]
    assert len(inside) == 5
    traces = list(_per_eye_traces(key))
    assert traces
    for detector, row in traces:
        runs = (detect.EyeDetection.Run & row & 'label in ("saccade", "microsaccade")').to_dicts()
        large = [run for run in runs if run["amplitude_deg"] is not None and run["amplitude_deg"] >= 1.0]
        assert large, (detector, row["trace"])
        for run in large:
            assert min(abs(run["run_start"] - ramp) for ramp in ramps) <= _RAMP_ROWS_TOLERANCE, (detector, run)
        if detector != "engbert_kliegl":
            continue
        for ramp in inside:
            assert any(abs(run["run_start"] - ramp) <= _RAMP_ROWS_TOLERANCE for run in runs), (detector, row["trace"],
                                                                                                ramp)


def test_a_ramp_across_the_spans_end_is_detected_on_the_span_alone(landed):
    """Fix round 1 (review I2, M3): Engbert-Kliegl reads the velocity, which is
    the estimator's over the span's rows alone, so zero on the span's last two
    rows, as `velocity` states its own edges. A saccade across the span's end
    therefore stops before them. Read over the whole recording, its velocity
    there is the ramp's, and its run reaches the span's end, where the mask
    cuts it."""
    from wl_preproc.schema import detect

    key = landed.key("eyes")
    _start_s, end_s = _span(landed, "eyes")
    _segment, _path, times = _eye_recording(landed, "eyes")
    stop_row = int((times <= end_s).sum())
    (edge,) = [row for row in _ramp_rows(edge_ramp=True) if row < stop_row < row + _RAMP_STEPS]
    assert stop_row - edge >= 6
    checked = 0
    for detector, row in _per_eye_traces(key):
        if detector != "engbert_kliegl":
            continue
        for run in (detect.EyeDetection.Run & row & 'label in ("saccade", "microsaccade")').to_dicts():
            if run["run_stop"] > edge - _RAMP_ROWS_TOLERANCE:
                assert run["run_stop"] <= stop_row - 2, run
        checked += 1
    assert checked


# -- Task 7: the NWB file (amendments 58 and 63) ----------------------------------

# The sessions whose canonical NWB file is built: `partial`, which has no
# calibration and so only pupil series and no event; `eyes`, with gaze and
# events; `before_ni`, with a camera file outside the span; `camera_only`,
# with no eye tracker; and `synced` and `synced_eyes`, under the box's clock.
_NWB_SESSIONS = ("partial", "eyes", "before_ni", "camera_only", "synced", "synced_eyes")

# The note stating the span (amendment 63; ruling R12), its two ends and the
# systems the NI's barcodes placed read back from it.
_SPAN_NOTE = re.compile(r"^the sync box's covered span is (\d+\.\d{3}) s to (\d+\.\d{3}) s; the NI's own barcodes "
                        r"placed (.+) over it$")


def _span_note(notes) -> tuple[float, float, list[str]] | None:
    """The span note's ends and systems, or None where the notes hold none;
    two would be a fault."""
    found = [match for match in map(_SPAN_NOTE.match, notes) if match]
    assert len(found) <= 1, notes
    if not found:
        return None
    (match,) = found
    return float(match[1]), float(match[2]), match[3].replace(" and ", ", ").split(", ")


@pytest.fixture(scope="module")
def nwb_files(landed, prefix, tmp_path_factory):
    """Each `_NWB_SESSIONS` session's canonical NWB file, built by the daemon
    in one pass, as `test_ni_clock.py::ni_nwb` builds one: by name, its
    `NwbFile` row."""
    from wl_preproc import daemon
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core
    from wl_preproc.schema import nwb as nwb_schema

    from tests.schema.test_nwb_build import _montage, _request

    keys = {}
    for name in _NWB_SESSIONS:
        session_key = landed.key(name)
        runs = [{"run_number": row["run_number"], "start_s": row["run_start_time"], "end_s": row["run_stop_time"],
                 "works_run_id": f"wr-{row['run_number']}"}
                for row in (core.Run & session_key).to_dicts(order_by="run_number")]
        keys[name] = accept(_request(session_key, landed.animals[name].key("canonical"), _montage(runs), runs),
                            prefix=prefix)
    report = daemon.run_once(prefix=prefix, nwb_root=tmp_path_factory.mktemp("partial-nwb"))
    files = {}
    for name, key in keys.items():
        rows = (nwb_schema.NwbFile & key).to_dicts()
        assert rows, (name, report["errors"])
        assert rows[0]["status"] == "written", (name, rows[0]["reason"] or rows[0]["inspector_findings"])
        files[name] = rows[0]
    return files


def _stored_span(landed, name) -> tuple[float, float]:
    from wl_preproc.schema import timebase

    row = (timebase.TimingProvenance & landed.key(name)).fetch1()
    return row["covered_start_s"], row["covered_end_s"]


def _session_extent(landed, name) -> tuple[float, float]:
    """Session time's extent under the NI: its first file, the one SpikeGLX
    segment, from t = 0."""
    from wl_preproc.schema import core

    segment = (core.Segment & landed.key(name) & {"system": "spikeglx"}).fetch1()
    return segment["start_s"], segment["end_s"]


def _runs(landed, name):
    """The session's measured runs, every one the canonical file holds, as
    the file's block set (`nwb/trim.py`)."""
    from wl_preproc.nwb.trim import BlockSet
    from wl_preproc.schema import core

    return BlockSet.of([{"start_s": row["run_start_time"], "end_s": row["run_stop_time"]}
                        for row in (core.Run & landed.key(name)).to_dicts()])


def test_the_nwb_eye_series_ends_inside_the_span(landed, nwb_files):
    """Amendment 58: every eye sample the file holds lies inside the span, on
    `partial` (pupil alone, no calibration) and on `eyes` (gaze and pupil).
    The raw recording has samples before the span and, inside the file's
    runs, after it, so the file's are cut by the span, not by its runs; and
    the series reaches the span's last sample, so it is not cut short of it.
    The first run starts after the span does, so only its end cuts here."""
    from pynwb import NWBHDF5IO

    for name in ("partial", "eyes"):
        start_s, end_s = _span(landed, name)
        _segment, _path, raw = _eye_recording(landed, name)
        runs = _runs(landed, name)
        in_runs = raw[runs.contains(raw)]
        assert (in_runs > end_s).any() and (raw < start_s).any(), name
        last_inside = raw[raw <= end_s].max()
        with NWBHDF5IO(nwb_files[name]["path"], "r") as handle:
            behavior = handle.read().processing["behavior"]
            series = [*behavior["PupilTracking"].time_series.values(),
                      *(behavior["EyeTracking"].spatial_series.values() if "EyeTracking" in behavior.data_interfaces
                        else ())]
            assert len(series) == (2 if name == "partial" else 6), (name, [item.name for item in series])
            for item in series:
                times = item.timestamps[:]
                assert len(times) == len(item.data), (name, item.name)
                assert len(times) > 0 and (times < end_s).any(), (name, item.name)
                assert times.min() >= start_s and times.max() <= end_s, (name, item.name)
                assert times.max() == pytest.approx(last_inside, abs=1e-9), (name, item.name)


def test_the_nwb_eye_events_lie_inside_the_span(landed, nwb_files):
    """Amendment 58, on `eyes`: every detector's event, the mask's withheld
    stretches, the glitch repairs and the both-eyes trace's sources cover
    only samples inside the span. Each stop is exclusive, one sample past the
    stretch's last, so it is no later than the first sample after the span."""
    from pynwb import NWBHDF5IO

    from wl_preproc.schema import detect

    start_s, end_s = _span(landed, "eyes")
    _segment, _path, raw = _eye_recording(landed, "eyes")
    first_after = raw[raw > end_s].min()
    with NWBHDF5IO(nwb_files["eyes"]["path"], "r") as handle:
        nwb = handle.read()
        tables = {name: table for name, table in nwb.processing["eye_events"].data_interfaces.items()
                  if name != "detector_agreement"}
        tables.update({name: table for name, table in nwb.processing["behavior"].data_interfaces.items()
                       if name.startswith(("eye_validity_", "eye_glitch_repairs_"))})
        assert any(name.endswith("_conjunction") for name in tables)
        assert any(name.endswith("_source") for name in tables)
        assert {"eye_validity_left", "eye_validity_right"} <= set(tables)
        n_events = 0
        for name, table in tables.items():
            frame = table.to_dataframe()
            if not len(frame):
                continue
            assert frame["start_time"].min() >= start_s, name
            assert frame["stop_time"].max() <= first_after, name
            n_events += len(frame) if not name.startswith("eye_") else 0
        assert n_events > 0
    # The mask withheld the whole recording after the span as `invalid`, so
    # its stretches would reach past the span were they not cut.
    after = int((raw <= end_s).sum())
    withheld = (detect.EyeValidity.Run & landed.key("eyes") & {"eye": "left"} & 'label = "invalid"').to_dicts()
    assert any(run["run_start"] >= after for run in withheld)


def test_the_nwb_file_names_what_it_left_out(landed, nwb_files):
    """Amendment 58: the notes name each stretch of session time outside the
    span, eye data in it left out, from the stored span: before it, from the
    NI's first sample, and after it, to the NI's last. The description says
    nothing of it (amendment 63)."""
    from pynwb import NWBHDF5IO

    for name in ("partial", "eyes"):
        start_s, end_s = _stored_span(landed, name)
        assert (start_s, end_s) == pytest.approx(_span(landed, name), abs=1e-9)
        session_start_s, session_end_s = _session_extent(landed, name)
        assert session_start_s < start_s and end_s < session_end_s
        notes = nwb_files[name]["description"]["notes"]
        spanned = [note for note in notes if "covered span" in note]
        assert len(spanned) == 3 and _SPAN_NOTE.match(spanned[0]), (name, spanned)
        assert _span_note(notes)[:2] == pytest.approx((start_s, end_s), abs=5e-4), name
        assert spanned[1:] == [
            f"eye data outside the sync box's covered span, {session_start_s:.3f} s to {start_s:.3f} s, is left "
            "out: no barcode placed it",
            f"eye data outside the sync box's covered span, {end_s:.3f} s to {session_end_s:.3f} s, is left "
            "out: no barcode placed it",
        ], name
        with NWBHDF5IO(nwb_files[name]["path"], "r") as handle:
            assert "span" not in handle.read().session_description, name


def test_the_nwb_file_names_each_file_outside_the_span(landed, nwb_files):
    """Amendment 57: `before_ni`'s camera file is `outside_covered_span`, and
    the notes name it."""
    from wl_preproc.schema import core

    start_s, end_s = _stored_span(landed, "before_ni")
    (rejected,) = (core.RejectedSegment & landed.key("before_ni")).to_dicts()
    assert (rejected["system"], rejected["reason"]) == ("bcam", "outside_covered_span")
    notes = nwb_files["before_ni"]["description"]["notes"]
    assert (f"bcam file {rejected['file_path']} is left out: none of its barcodes is one the NI recorded, so it "
            f"lies outside the sync box's covered span, {start_s:.3f} s to {end_s:.3f} s") in notes
    assert not any("is left out: none of its barcodes" in note for note in nwb_files["partial"]["description"]["notes"])


def test_the_nwb_file_says_barcodes_placed_the_eye_tracker(nwb_files):
    """Amendment 63: `system_clocks.time_source` reads `barcode` for each
    system the NI's barcodes placed and `clock` for SpikeGLX, and the NI's
    texts say barcodes place what they place."""
    from pynwb import NWBHDF5IO

    with NWBHDF5IO(nwb_files["partial"]["path"], "r") as handle:
        module = handle.read().processing["timebase"]
        clocks = module["system_clocks"]
        assert dict(zip(clocks["system"][:], clocks["time_source"][:], strict=True)) == {
            "spikeglx": "clock", "syncbox": "barcode", "ohdpi": "barcode", "bcam": "barcode"}
        assert clocks["time_source"].description == (
            "How the system was placed on the NI's clock: clock, SpikeGLX on the identity map; strobes, Intan "
            "fitted by its strobe edges matched to the NI's; barcode, a system the NI's own barcodes placed, over "
            "the part the sync box covered; none, not placed.")
        assert clocks["fitted_rate_hz"].description == (
            "The rate on the session's clock: SpikeGLX's nominal rate on the identity map; Intan's rate fitted "
            "against the NI's strobes; a system the NI's barcodes placed, its rate fitted against them; NaN for a "
            "system not placed on the NI's clock.")
        assert clocks["residual_us_rms"].description == (
            "RMS residual, microseconds: 0 for SpikeGLX on the identity map; Intan's RMS strobe residual; a system "
            "the NI's barcodes placed, its RMS barcode residual; NaN for a system not placed on the NI's clock.")
        assert module["timing_provenance"]["worst_residual_us"].description == (
            "Largest residual, microseconds: under the NI, 0 for SpikeGLX on the identity map, Intan's largest "
            "strobe residual where it was fitted, and the largest barcode residual of a system the NI's barcodes "
            "placed.")
        assert module["segments"]["first_sample"].description == (
            "Native index the segment's offset is anchored to: 0 for SpikeGLX, the NI's first file being session "
            "time; for Intan, the first strobe edge matched to the NI's; for a system the NI's barcodes placed, "
            "the segment's first barcode.")


def test_under_the_sync_box_the_nwb_file_says_nothing_of_a_span(landed, nwb_files):
    """A session the box times keeps its file: no time_source column, no
    note of a span, and every eye sample the recording has inside the
    file's runs."""
    from pynwb import NWBHDF5IO

    notes = nwb_files["synced_eyes"]["description"]["notes"]
    assert not any("covered span" in note for note in notes), notes
    _segment, _path, raw = _eye_recording(landed, "synced_eyes")
    runs = _runs(landed, "synced_eyes")
    with NWBHDF5IO(nwb_files["synced_eyes"]["path"], "r") as handle:
        nwb = handle.read()
        assert "time_source" not in nwb.processing["timebase"]["system_clocks"].colnames
        times = nwb.processing["behavior"]["PupilTracking"]["pupil_left"].timestamps[:]
    assert len(times) == runs.contains(raw).sum()


def test_the_nwb_file_states_the_covered_span(landed, nwb_files):
    """Amendment 63, ruling R12: under the NI the notes state the covered span
    and the systems the NI's barcodes placed over it, whether or not an eye
    tracker ran. `camera_only` has none, and no file outside the span, so no
    other note carries the span's numbers. A file under the box states none."""
    from wl_preproc.schema import core, timebase

    key = landed.key("camera_only")
    assert not (core.Segment & key & {"system": "ohdpi"})
    assert not (core.RejectedSegment & key)
    assert (timebase.TimingProvenance & key).fetch1("tier") == "N"
    placed = sorted(row["system"] for row in (timebase.SystemTimebase & key).to_dicts()
                    if (row["fit_status"], row["time_source"]) == ("fitted", "barcode"))
    assert placed == ["bcam", "syncbox"]
    notes = nwb_files["camera_only"]["description"]["notes"]
    start_s, end_s, systems = _span_note(notes)
    assert (start_s, end_s) == pytest.approx(_stored_span(landed, "camera_only"), abs=5e-4)
    assert systems == placed
    assert not any(note.startswith("eye data outside") for note in notes), notes
    # `before_ni`'s camera was left out, not placed, so it is not named.
    assert _span_note(nwb_files["before_ni"]["description"]["notes"])[2] == ["ohdpi", "syncbox"]
    for name in ("synced", "synced_eyes"):
        notes = nwb_files[name]["description"]["notes"]
        assert _span_note(notes) is None and not any("covered span" in note for note in notes), (name, notes)
