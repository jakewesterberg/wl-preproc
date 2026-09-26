"""Synthetic gaze and oracle adapters for REMoDNaV's tests -- pure numpy and
the dev-only `remodnav` package, never `wl_preproc.schema`, so the 3.13
cross-check runs everything that uses them.

Not a test module (no `test_` prefix); imported as
`tests.eye.detect._remodnav_traces` (`pyproject.toml`'s `pythonpath`).
"""

from __future__ import annotations

import numpy as np

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.remodnav import Signals

#: The oracle's event labels in this vocabulary (spec section 4).
ORACLE_LABELS: dict[str, Label] = {
    "SACC": Label.SACCADE,
    "ISAC": Label.SACCADE,
    "HPSO": Label.PSO,
    "LPSO": Label.PSO,
    "IHPS": Label.PSO,
    "ILPS": Label.PSO,
    "FIXA": Label.FIXATION,
    "PURS": Label.PURSUIT,
}

#: Where the planted pursuit and the planted gap sit, in seconds.
PURSUIT_S = (8.0, 9.5)
GAP_S = (14.0, 14.06)


def gaze_trace(fs_hz: float, seed: int, duration_s: float = 20.0) -> np.ndarray:
    """`(n, 2)` gaze in degrees. Contents:
    - minimum-jerk saccades of 1-12 deg at about 3 Hz, so the 2 Hz cap stops
      the major pass and the rest are found between them;
    - a damped 25 Hz wobble after about half of them;
    - an 8 deg/s pursuit across `PURSUIT_S`;
    - a random-walk drift, slow enough that the low-passed trace stays well
      under the 2 deg/s pursuit threshold, and 0.01 deg noise;
    - NaN across `GAP_S`.

    Quiet for the first and last 0.5 s, so neither of spec 2.2's edges is
    reached. **If a fidelity test finds a case this does not exercise, change
    this generator, never the classifier**, and say why in its docstring."""
    rng = np.random.default_rng(seed)
    n = int(round(duration_s * fs_hz))
    t = np.arange(n) / fs_hz
    target = np.zeros((n, 2))
    position = np.zeros(2)
    cursor = 0.5
    while cursor < duration_s - 0.5:
        if PURSUIT_S[0] - 0.4 < cursor < PURSUIT_S[1] + 0.2 or GAP_S[0] - 0.3 < cursor < GAP_S[1] + 0.3:
            cursor += 0.05
            continue
        amplitude = rng.uniform(1.0, 12.0)
        angle = rng.uniform(0.0, 2.0 * np.pi)
        step = amplitude * np.array([np.cos(angle), np.sin(angle)])
        if np.any(np.abs(position + step) > 15.0):
            step = -step
        first = int(round(cursor * fs_hz))
        count = max(int(round((2.2 * amplitude + 21.0) / 1000.0 * fs_hz)), 2)
        tau = np.linspace(0.0, 1.0, count)
        profile = 10 * tau**3 - 15 * tau**4 + 6 * tau**5
        target[first:first + count] = position + np.outer(profile, step)
        position = position + step
        target[first + count:] = position
        if rng.random() < 0.5:
            wobble_t = np.arange(int(round(0.03 * fs_hz))) / fs_hz
            wobble = 0.03 * amplitude * np.exp(-wobble_t / 0.01) * np.sin(2 * np.pi * 25.0 * wobble_t)
            stop = min(first + count + wobble.size, n)
            target[first + count:stop] += np.outer(wobble[: stop - first - count], step / amplitude)
        cursor += rng.uniform(0.15, 0.5)
    p0, p1 = int(round(PURSUIT_S[0] * fs_hz)), int(round(PURSUIT_S[1] * fs_hz))
    ramp = np.zeros((n, 2))
    ramp[p0:p1, 0] = 8.0 * (t[p0:p1] - PURSUIT_S[0])
    ramp[p1:, 0] = 8.0 * (PURSUIT_S[1] - PURSUIT_S[0])
    # A random walk whose speed after REMoDNaV's 4 Hz low-pass is ~0.2 deg/s
    # per axis: per-step sd D / sqrt(fs) low-passes to about D * sqrt(8).
    # Faster drift reaches the 2 deg/s pursuit threshold on its own.
    drift = np.cumsum(rng.normal(0.0, 0.07 / np.sqrt(fs_hz), (n, 2)), axis=0)
    xy = target + ramp + drift + rng.normal(0.0, 0.01, (n, 2))
    xy[int(round(GAP_S[0] * fs_hz)):int(round(GAP_S[1] * fs_hz))] = np.nan
    return xy


def oracle_run(remodnav_module, xy_deg: np.ndarray, fs_hz: float):
    """The oracle, end to end, on degrees (`px2deg = 1`). Returns the
    classifier, its preprocessed record array and its events."""
    classifier = remodnav_module.EyegazeClassifier(px2deg=1.0, sampling_rate=fs_hz)
    data = np.rec.fromarrays([xy_deg[:, 0].copy(), xy_deg[:, 1].copy()], names=["x", "y"])
    preprocessed = classifier.preproc(data)
    return classifier, preprocessed, classifier(preprocessed)


def signals_from_oracle(preprocessed) -> Signals:
    """The oracle's own preprocessed signals, as `classify` reads them."""
    return Signals(
        x=np.array(preprocessed["x"], dtype=float),
        y=np.array(preprocessed["y"], dtype=float),
        speed=np.array(preprocessed["vel"], dtype=float),
        candidate_speed=np.array(preprocessed["med_vel"], dtype=float),
    )


def two_point_speed(positions: np.ndarray, fs_hz: float) -> np.ndarray:
    """The oracle's differentiator (`clf.py` 784-790): `n - 1` speeds, in its
    own arithmetic."""
    return (np.diff(positions[:, 0]) ** 2 + np.diff(positions[:, 1]) ** 2) ** 0.5 * (1.0 * fs_hz)


def oracle_labels(events, fs_hz: float, n: int) -> np.ndarray:
    """One label value per sample, `""` where the oracle labelled nothing."""
    out = np.full(n, "", dtype=object)
    for event in events:
        start = int(round(event["start_time"] * fs_hz))
        stop = int(round(event["end_time"] * fs_hz))
        out[start:stop] = ORACLE_LABELS[event["label"]].value
    return out


def intersaccadic_windows(events, fs_hz: float, min_s: float = 0.3) -> list[tuple[int, int]]:
    """The oracle's own intersaccadic intervals: from each saccade's (or its
    PSO's) end to the next saccade's start, at least `min_s` long, and clear
    of `GAP_S` by 0.1 s.

    These are windows the oracle itself classified, so neither edge sits
    inside a movement and spec 2.2's two fixes are never reached. Stage
    comparisons run on these, not on arbitrary windows."""
    saccadic = sorted(
        (e for e in events if ORACLE_LABELS[e["label"]] in (Label.SACCADE, Label.PSO)),
        key=lambda e: e["start_time"],
    )
    margin = int(round(0.1 * fs_hz))
    gap = (int(round(GAP_S[0] * fs_hz)) - margin, int(round(GAP_S[1] * fs_hz)) + margin)
    windows = []
    for before, after in zip(saccadic, saccadic[1:]):
        if ORACLE_LABELS[after["label"]] is not Label.SACCADE:
            continue
        start = int(round(before["end_time"] * fs_hz))
        end = int(round(after["start_time"] * fs_hz))
        if end - start >= min_s * fs_hz and (end <= gap[0] or start >= gap[1]):
            windows.append((start, end))
    return windows


def our_labels(runs, n: int) -> np.ndarray:
    """One label value per sample, `""` where no run lies."""
    out = np.full(n, "", dtype=object)
    for run in runs:
        out[run.start:run.stop] = run.label.value
    return out
