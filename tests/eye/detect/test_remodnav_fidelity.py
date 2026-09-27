"""Spec section 5.1 -- is the algorithm right?

`classify` is handed the oracle's OWN preprocessed signals (`preproc()`'s
`x`, `y`, `vel`, `med_vel`) and its two-point differentiator. It must then
label every sample exactly as the oracle's own `__call__` does. Any residual
difference is a defect in one of the two until explained. The explanation
goes into spec section 2, or the code is fixed. The tolerance is never
widened.

**Nulls first** (spec 5.1). Two deliberately broken cores must fail this
check, proving it sees the class of defect it exists for:
- on/offset searched with the peak threshold;
- a raw MAD instead of the sigma-scaled one.

Pure numpy and the dev-only oracle: runs in CI, on 3.11 and 3.13.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.eye.detect._remodnav_traces import (
    gaze_trace,
    oracle_labels,
    oracle_run,
    our_labels,
    signals_from_oracle,
    two_point_speed,
)
from wl_preproc.eye.detect import remodnav as module
from wl_preproc.eye.detect.remodnav import DEFAULT_REMODNAV_PARAMS, classify

remodnav = pytest.importorskip("remodnav")

#: Whole-number sample counts at both rates (spec 2.1), several seeds each.
CASES = [(500.0, 1), (500.0, 2), (500.0, 3), (1000.0, 4), (1000.0, 5)]


def _both(fs_hz, seed):
    xy = gaze_trace(fs_hz, seed)
    _classifier, preprocessed, events = oracle_run(remodnav, xy, fs_hz)
    runs = classify(signals_from_oracle(preprocessed), fs_hz, DEFAULT_REMODNAV_PARAMS, two_point_speed)
    return our_labels(runs, len(xy)), oracle_labels(events, fs_hz, len(xy)), events


def test_the_null_a_raw_mad_fails_the_check(monkeypatch):
    monkeypatch.setattr(module, "_MAD_C", 1.0)

    ours, theirs, _ = _both(500.0, 1)

    assert (ours != theirs).any()


def test_the_null_on_offset_at_the_peak_threshold_fails_the_check(monkeypatch):
    real = module._thresholds

    def broken(speeds, params):
        found = real(speeds, params)
        return None if found is None else module.Thresholds(peak=found.peak, onset=found.peak)

    monkeypatch.setattr(module, "_thresholds", broken)

    ours, theirs, _ = _both(500.0, 1)

    assert (ours != theirs).any()


@pytest.mark.parametrize("fs_hz, seed", CASES)
def test_the_fixture_exercises_every_path(fs_hz, seed):
    """A fidelity check over a trace that never reaches a path proves nothing
    about that path. If this fails, change `gaze_trace`, never `classify`."""
    _, _, events = _both(fs_hz, seed)
    labels = {event["label"] for event in events}

    assert {"SACC", "ISAC", "FIXA", "PURS"} <= labels
    assert labels & {"HPSO", "LPSO", "IHPS", "ILPS"}


@pytest.mark.parametrize("fs_hz, seed", CASES)
def test_every_sample_is_labelled_as_the_oracle_labels_it(fs_hz, seed):
    ours, theirs, _ = _both(fs_hz, seed)

    differing = np.flatnonzero(ours != theirs)

    assert differing.size == 0, (
        f"{differing.size} of {ours.size} samples differ; first at "
        + ", ".join(f"{i}: ours={ours[i]!r} oracle={theirs[i]!r}" for i in differing[:10])
    )
