"""Spec 5.1 -- the whole algorithm against the authors' own code: the same
label at every sample. Nulls first -- three deliberately broken
implementations must fail it:
- no greedy continuity;
- no per-step emission normalisation;
- no Fisher transform on the turn.

Gated on `WLPP_NSLR_REFERENCE`, which CI sets; pure numpy otherwise."""

from __future__ import annotations

import numpy as np
import pytest

from tests.eye.detect._nslr_reference import reference
from tests.eye.detect._nslr_traces import overshoot_trace
from wl_preproc.eye.detect import nslr as module
from wl_preproc.eye.detect.nslr import DEFAULT_NSLR_PARAMS, classify

#: 3 s each, because the reference takes ~5-8 s per 1,000 samples. Each of
#: these contains all four classes (`_nslr_traces.py`), and each was proven
#: identical to the reference while planning.
CASES = [(500.0, 4), (500.0, 12), (500.0, 17), (1000.0, 1), (1000.0, 2)]


def _both(fs_hz, seed):
    _, nslr_hmm = reference()
    xy = overshoot_trace(fs_hz, seed)
    ts = np.arange(len(xy)) / fs_hz
    theirs, _segmentation, _classes = nslr_hmm.classify_gaze(ts, xy)
    ours = classify([xy], fs_hz, DEFAULT_NSLR_PARAMS)[0] + 1
    return ours, np.asarray(theirs, dtype=np.int64)


def test_the_null_no_greedy_continuity_fails_the_check(monkeypatch):
    real = module.segment
    monkeypatch.setattr(module, "segment", lambda *a, **k: real(*a, **k, _continuity=False))
    ours, theirs = _both(500.0, 4)
    assert (ours != theirs).any()


def test_the_null_no_emission_normalisation_fails_the_check(monkeypatch):
    """Dividing a step's emissions by their sum shifts every candidate
    equally in log10, so skipping it only changes the path where the 1e-6
    clip bites unevenly across states. Seed 4 never exercises that; seed 12
    does (measured while fixing this null: Task 2's mutation of
    `_later_emission` diverges from the reference at segment 22 on this
    seed)."""
    monkeypatch.setattr(module, "_later_emission", lambda e: e)
    ours, theirs = _both(500.0, 12)
    assert (ours != theirs).any()


def test_the_null_no_fisher_transform_fails_the_check(monkeypatch):
    monkeypatch.setattr(module, "_fisher", lambda cos: cos)
    ours, theirs = _both(500.0, 4)
    assert (ours != theirs).any()


@pytest.mark.parametrize("fs_hz, seed", CASES)
def test_every_sample_is_labelled_as_the_reference_labels_it(fs_hz, seed):
    ours, theirs = _both(fs_hz, seed)
    differing = np.flatnonzero(ours != theirs)
    assert differing.size == 0, (
        f"{differing.size} of {ours.size} samples differ; first at "
        + ", ".join(f"{i}: ours={ours[i]} reference={theirs[i]}" for i in differing[:10])
    )


@pytest.mark.parametrize("fs_hz, seed", CASES)
def test_the_fixture_exercises_every_class(fs_hz, seed):
    """A fidelity check over traces that never reach a class proves nothing
    about it. If this fails, change the case list or `_nslr_traces.py`, never
    `classify`, and say why."""
    ours, _ = _both(fs_hz, seed)
    assert set(ours.tolist()) == {1, 2, 3, 4}


def test_a_perfectly_still_trace_is_classified_without_error():
    """Review Focus 3: zero variance -- the noise is the structural error
    alone, and nothing may raise or be called a saccade."""
    states = classify([np.zeros((600, 2))], 500.0, DEFAULT_NSLR_PARAMS)[0]
    assert states.shape == (600,)
    assert 1 not in set(states.tolist())
