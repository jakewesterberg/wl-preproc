"""U'n'Eye in windows on its 25-sample pooling grid (spec 4.2, amendment 4): labels equal the single call, its run
reliability within 1e-5, and memory flat in the stretch's length."""

from __future__ import annotations

import numpy as np
import pytest

from tests.eye.detect._uneye_traces import FS_HZ, all_usable, planted
from tests.eye.detect.test_nslr_validation import recording  # noqa: F401 -- the reference fixture
from tests.scaling import _oracles
from wl_preproc.eye.detect.uneye import DEFAULT_UNEYE_PARAMS, _network

RELIABILITY_TOLERANCE = 1e-5


@pytest.fixture(scope="module")
def network():
    return _network(DEFAULT_UNEYE_PARAMS, FS_HZ)


def _walk(n, seed):
    """A random walk with a saccadic step every 400 samples or so."""
    rng = np.random.default_rng(seed)
    x, y = np.cumsum(rng.normal(0, 0.01, n)), np.cumsum(rng.normal(0, 0.01, n))
    ramp_index = np.arange(n)
    for onset in rng.integers(100, max(n - 100, 101), max(n // 400, 1)):
        amp = rng.normal(0, 5, 2)
        ramp = np.clip((ramp_index - onset) / 15, 0, 1)
        x, y = x + amp[0] * ramp, y + amp[1] * ramp
    return x, y


def _equal_to_the_single_call(network, x, y):
    from wl_preproc.eye.detect.uneye import _predict_windowed

    labels, probability = _predict_windowed(network, x, y)
    whole_labels, whole_probability = network.predict(x, y)
    np.testing.assert_array_equal(labels, whole_labels)
    np.testing.assert_allclose(probability, whole_probability, rtol=0, atol=RELIABILITY_TOLERANCE)


@pytest.mark.parametrize("n", [60_013, 250_000])
def test_windowed_equals_whole(network, n):
    _equal_to_the_single_call(network, *_walk(n, n))


@pytest.mark.parametrize("n", [25, 49, 50_000, 50_001, 100_024])
def test_windowed_equals_whole_at_odd_lengths(network, n):
    _equal_to_the_single_call(network, *_walk(n, n + 1))


def test_the_overlap_covers_the_receptive_field(network):
    """Perturb one input sample mid-stretch: every output it changes lies within the overlap (measured at +-89)."""
    import torch

    from wl_preproc.eye.detect.uneye import OVERLAP_SAMPLES, _unet

    unet = _unet(network)
    n, mid = 5_000, 2_500
    x, y = _walk(n, 3)
    inputs = torch.zeros((1, 1, n, 2))
    inputs[0, 0, 1:, 0] = torch.tensor(np.diff(x), dtype=torch.float32)
    inputs[0, 0, 1:, 1] = torch.tensor(np.diff(y), dtype=torch.float32)
    with torch.no_grad():
        base = unet(inputs, ["out"])[0].numpy()
        inputs[0, 0, mid, 0] += 1.0
        changed = np.flatnonzero(np.abs(unet(inputs, ["out"])[0].numpy() - base).max(axis=(0, 1)) > 0)
    assert changed.min() - mid >= -OVERLAP_SAMPLES
    assert changed.max() - mid <= OVERLAP_SAMPLES


def _runs_equal(got, expected):
    assert [(run.start, run.stop, run.label) for run in got] == [(run.start, run.stop, run.label) for run in expected]
    for a, b in zip(got, expected, strict=True):
        assert abs(a.reliability - b.reliability) <= RELIABILITY_TOLERANCE


def test_detect_uneye_runs_unchanged():
    from wl_preproc.eye.detect.uneye import detect_uneye

    gaze, _onsets = planted((0.5, 2.0, 5.0, 10.0) * 40)
    available = all_usable(len(gaze))
    available[len(gaze) // 3: len(gaze) // 3 + 60] = "blink"
    args = (gaze, np.zeros_like(gaze), available, FS_HZ, DEFAULT_UNEYE_PARAMS)
    _runs_equal(detect_uneye(*args), _oracles.detect_uneye(*args))


def test_detect_uneye_runs_unchanged_on_the_reference_recording(recording):
    """The OpenIris reference recording, both eyes as the validation suites offer them (skipped without it)."""
    from wl_preproc.eye.detect.uneye import detect_uneye

    rec, eyes = recording
    for gaze, velocity, mask in eyes.values():
        args = (gaze, velocity, mask, rec.fs_hz, DEFAULT_UNEYE_PARAMS)
        _runs_equal(detect_uneye(*args), _oracles.detect_uneye(*args))


def test_the_network_never_sees_more_than_a_window(network, monkeypatch):
    """The activations, not the input, are what grew to 8.4 GB, and torch's allocator is invisible to `tracemalloc`:
    so the bound is held where it is made. Every evaluation is at most a window and both overlaps long."""
    from wl_preproc.eye.detect import uneye

    unet = uneye._unet(network)
    lengths = []

    class Recording:
        def __call__(self, inputs, outputs):
            lengths.append(inputs.size()[2])
            return unet(inputs, outputs)

    monkeypatch.setattr(uneye, "_unet", lambda _network: Recording())
    x, y = _walk(400_000, 9)
    uneye._predict_windowed(network, x, y, window=10_000, overlap=500)
    assert len(lengths) == 40
    assert max(lengths) <= 10_000 + 2 * 500
