"""Today's implementations, copied verbatim before each scaling fix (spec section 3), as test-only oracles.

Never imported by `wl_preproc`. Each new implementation must equal the copy here.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml


# -- eye/ohdpi.py::read_columns, at 7358a93 ---------------------------------------------------------------------
def read_columns(path: Path, columns: list[str]) -> dict[str, np.ndarray]:
    try:
        frame = pd.read_csv(path, sep=r"\s+", usecols=columns, engine="c")
    except ValueError as exc:
        header = pd.read_csv(path, sep=r"\s+", nrows=0, engine="c")
        missing = set(columns) - set(header.columns)
        if missing:
            raise ValueError(
                f"{path}: header is missing {sorted(missing)}. This is not an "
                "OpenIris recording, or its format has changed since 2026-08-30"
            ) from exc
        raise
    return {name: frame[name].to_numpy() for name in columns}


# -- contracts/sidecar.py::BehaviorCameraSidecar.from_yaml, at 7358a93 ------------------------------------------
def sidecar_from_yaml(text: str):
    from wl_preproc.contracts.sidecar import BehaviorCameraSidecar

    return BehaviorCameraSidecar.model_validate(yaml.safe_load(text))


# -- timebase/extract.py::_edges_from_bit, extract_rhs, extract_spikeglx, at 7358a93 ------------------------------
def edges_from_bit(words, bit, fs_hz):
    from wl_sync.barcode import edges_from_samples

    trace = ((words >> bit) & 1).astype(np.uint8)
    return tuple(edges_from_samples(trace.tolist(), fs_hz=fs_hz))


def extract_rhs(session_dir: Path):
    from wl_preproc.timebase._rhs_header import INFO_FILENAME, find_recording_dir, read_sample_rate_hz
    from wl_preproc.timebase.extract import RHS_BARCODE_DIGITAL_BIT, RHS_DIGITAL_IN_FILENAME, BitStream

    recording_dir = find_recording_dir(session_dir)
    fs_hz = read_sample_rate_hz(recording_dir / INFO_FILENAME)
    digital_path = recording_dir / RHS_DIGITAL_IN_FILENAME
    words = np.fromfile(digital_path, dtype=np.uint16)
    return BitStream(edges=edges_from_bit(words, RHS_BARCODE_DIGITAL_BIT, fs_hz), fs_hz=fs_hz,
                     n_samples=int(words.size))


def extract_spikeglx(nidq_bin: Path):
    from wl_preproc.timebase._nidq_meta import read_nidq_meta
    from wl_preproc.timebase.extract import NIDQ_BARCODE_XD_LINE, BitStream

    meta = read_nidq_meta(nidq_bin.with_suffix(".meta"))
    interleaved = np.fromfile(nidq_bin, dtype=np.int16)
    samples = interleaved.reshape(-1, meta.n_channels)
    words = samples[:, meta.n_analog_channels].astype(np.uint16)
    return BitStream(edges=edges_from_bit(words, NIDQ_BARCODE_XD_LINE, meta.sample_rate_hz),
                     fs_hz=meta.sample_rate_hz, n_samples=int(samples.shape[0]))


# -- events/extract.py::extract_nidq_words, extract_rhs_witness, at 7358a93 ---------------------------------------
def extract_nidq_words(nidq_bin: Path):
    from wl_preproc.events.extract import _NIDQ_STROBE_BIT, WordStream
    from wl_preproc.timebase._nidq_meta import read_nidq_meta

    meta = read_nidq_meta(nidq_bin.with_suffix(".meta"))
    raw = np.fromfile(nidq_bin, dtype=np.int16)
    samples = raw.reshape(-1, meta.n_channels)
    control = samples[:, meta.n_analog_channels].astype(np.uint16)
    data = samples[:, meta.n_analog_channels + 1].astype(np.uint16)
    strobe = (control >> _NIDQ_STROBE_BIT) & 1
    falling = np.flatnonzero((strobe[:-1] == 1) & (strobe[1:] == 0))
    words = tuple((int(index), int(data[index])) for index in falling)
    return WordStream(words=words, fs_hz=meta.sample_rate_hz)


def extract_rhs_witness(session_dir: Path):
    from wl_preproc.events.extract import RHS_STROBE_DIGITAL_BIT, StrobeWitness
    from wl_preproc.timebase._rhs_header import INFO_FILENAME, find_recording_dir, read_sample_rate_hz
    from wl_preproc.timebase.extract import RHS_DIGITAL_IN_FILENAME

    recording_dir = find_recording_dir(session_dir)
    fs_hz = read_sample_rate_hz(recording_dir / INFO_FILENAME)
    words = np.fromfile(recording_dir / RHS_DIGITAL_IN_FILENAME, dtype=np.uint16)
    strobe = (words >> RHS_STROBE_DIGITAL_BIT) & 1
    rising = np.flatnonzero((strobe[1:] == 1) & (strobe[:-1] == 0)) + 1
    return StrobeWitness(edge_samples=tuple(int(i) for i in rising), fs_hz=fs_hz)


# -- schema/detect.py::_overlapping, at 7358a93 ------------------------------------------------------------------
def overlapping(left, right, min_duration_samples, label_for):
    from wl_preproc.eye.detect.labels import Run

    floor = max(int(min_duration_samples), 1)
    intersections = []
    for left_run in left:
        for right_run in right:
            start = max(left_run.start, right_run.start)
            stop = min(left_run.stop, right_run.stop)
            if stop - start >= floor:
                intersections.append((start, stop))

    spans: list[list[int]] = []
    for start, stop in sorted(intersections):
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], stop)
        else:
            spans.append([start, stop])

    return [Run(start=start, stop=stop, label=label_for(start, stop)) for start, stop in spans]


# -- events/agreement.py::code_agreement, at 7358a93 -------------------------------------------------------------
def code_agreement(reference, other) -> float:
    from difflib import SequenceMatcher

    if not reference and not other:
        return 1.0
    matcher = SequenceMatcher(None, reference, other, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / max(len(reference), len(other))


# -- eye/detect/consensus.py::comparison_mask, _event_starts, at 7358a93 -----------------------------------------
def comparison_mask(a_labels, b_labels, a_vocabulary, b_vocabulary, pso_as):
    from wl_preproc.eye.detect.consensus import comparable
    from wl_preproc.eye.detect.labels import Label

    keep = np.ones(len(a_labels), dtype=bool)
    for index, (left, right) in enumerate(zip(a_labels, b_labels, strict=True)):
        keep[index] = comparable(Label(left), b_vocabulary, pso_as) and comparable(
            Label(right), a_vocabulary, pso_as
        )
    return keep


def event_starts(labels, mask):
    from wl_preproc.eye.detect.consensus import _EVENT_LABELS
    from wl_preproc.eye.detect.labels import Label

    starts: list[int] = []
    previous_was_event = False
    for index, label in enumerate(labels):
        is_event = bool(mask[index]) and Label(label) in _EVENT_LABELS
        if is_event and not previous_was_event:
            starts.append(index)
        previous_was_event = is_event
    return starts


# -- nwb/trim.py::BlockSet.contains, contains_instant, at 7358a93 ------------------------------------------------
def contains(intervals, times):
    times = np.asarray(times, dtype=float)
    inside = np.zeros(times.shape, dtype=bool)
    for start, end in intervals:
        inside |= (times >= start) & (times < end)
    return inside


def contains_instant(intervals, times):
    times = np.asarray(times, dtype=float)
    inside = np.zeros(times.shape, dtype=bool)
    for start, end in intervals:
        inside |= (times >= start) & (times <= end)
    return inside


# -- eye/detect/uneye.py::detect_uneye, at 7358a93 ---------------------------------------------------------------
def detect_uneye(gaze_deg, velocity_deg_s, available, fs_hz, params):
    from wl_preproc.eye.detect.labels import Label, Run, may_label, true_runs
    from wl_preproc.eye.detect.uneye import MIN_PIECE_SAMPLES, _network

    usable = may_label(available)
    usable &= np.isfinite(np.asarray(gaze_deg, dtype=float)).all(axis=1)
    pieces = [(int(a), int(b)) for a, b in true_runs(usable) if b - a >= MIN_PIECE_SAMPLES]
    if not pieces:
        return []
    network = _network(params, fs_hz)
    runs = []
    for offset, stop in pieces:
        piece = np.asarray(gaze_deg[offset:stop], dtype=float)
        prediction, probability = network.predict(piece[:, 0], piece[:, 1])
        for start, end in true_runs(prediction == 1):
            runs.append(Run(offset + int(start), offset + int(end), Label.SACCADE,
                            reliability=float(np.mean(probability[1, start:end]))))
    return runs
