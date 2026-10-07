"""U'n'Eye as a registered detector, on synthetic gaze (design spec
`2026-10-06-uneye-design.md` sections 3 and 5)."""

from __future__ import annotations

import numpy as np
import pytest

from tests.eye.detect._uneye_traces import FS_HZ, all_usable, planted
from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.uneye import DEFAULT_UNEYE_PARAMS, NETWORKS, UneyeParams, detect_uneye

AMPLITUDES_DEG = (0.5, 2.0, 5.0, 10.0) * 6


def _detect(gaze, available=None, params=DEFAULT_UNEYE_PARAMS):
    available = all_usable(len(gaze)) if available is None else available
    return detect_uneye(gaze, np.zeros_like(gaze), available, FS_HZ, params)


def test_planted_saccades_are_found_at_their_onsets():
    """Every planted saccade, 0.5 to 10 deg, and nothing else, each starting
    within 5 samples (10 ms) of its planted onset: the tolerance the database
    test holds every detector to. Measured over 840 planted saccades of 0.3
    to 15 deg, the default network put 838 within it."""
    gaze, onsets = planted(AMPLITUDES_DEG)
    runs = _detect(gaze)
    assert [run.label for run in runs] == [Label.SACCADE] * len(onsets)
    assert all(abs(run.start - onset) <= 5 for run, onset in zip(runs, onsets, strict=True))
    assert all(0.5 <= run.reliability <= 1.0 for run in runs)


def test_a_blink_splits_the_trace_and_no_saccade_crosses_it():
    gaze, onsets = planted(AMPLITUDES_DEG)
    blink = slice(onsets[3] - 20, onsets[3] + 40)
    available = all_usable(len(gaze))
    available[blink] = Label.BLINK
    runs = _detect(gaze, available)
    assert not [run for run in runs if run.start < blink.stop and run.stop > blink.start]
    assert len(runs) == len(onsets) - 1


class _Recording:
    """Stands in for U'n'Eye's classifier, recording the pieces it is given
    and calling every sample fixation."""

    def __init__(self):
        self.lengths = []

    def predict(self, x, y):
        self.lengths.append(len(x))
        return np.zeros(len(x)), np.zeros((2, len(x)))


def test_a_piece_under_25_samples_is_not_run_through_the_network(monkeypatch):
    """U'n'Eye refuses shorter input: its network pools by 5, twice."""
    from wl_preproc.eye.detect import uneye

    network = _Recording()
    monkeypatch.setattr(uneye, "_network", lambda params, fs_hz: network)
    gaze = np.zeros((400, 2))
    available = np.full(400, Label.INVALID, dtype=object)
    available[100:124] = None
    available[200:225] = None
    assert _detect(gaze, available) == []
    assert network.lengths == [25]


def test_non_finite_gaze_is_withheld_where_the_mask_offers_it(monkeypatch):
    """For NSLR's reason: the shared mask passes NaN as usable."""
    from wl_preproc.eye.detect import uneye

    network = _Recording()
    monkeypatch.setattr(uneye, "_network", lambda params, fs_hz: network)
    gaze = np.zeros((600, 2))
    gaze[300:302, 1] = np.nan
    _detect(gaze)
    assert network.lengths == [300, 298]


def test_the_network_stays_on_the_cpu_when_a_gpu_is_reported(monkeypatch):
    """U'n'Eye moves itself to any GPU torch reports; on the preprocessing
    server that could be one it cannot run on (design spec section 3)."""
    import torch

    gaze, _onsets = planted(AMPLITUDES_DEG[:4])
    expected = _detect(gaze)

    def refused(*_args, **_kwargs):
        raise AssertionError("U'n'Eye was moved to a GPU")

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.nn.Module, "cuda", refused)
    monkeypatch.setattr(torch.Tensor, "cuda", refused)
    assert _detect(gaze) == expected


def test_only_a_copied_two_class_network_can_be_chosen():
    from wl_preproc.eye.detect.uneye import _TRAINING

    assert set(NETWORKS) == {path.name for path in _TRAINING.glob("weights_*")}
    assert DEFAULT_UNEYE_PARAMS.weights == "weights_dataset3"
    with pytest.raises(ValueError, match="no network 'weights_Andersson'"):
        UneyeParams(weights="weights_Andersson", min_saccade_duration_ms=6, min_saccade_gap_ms=1)


def test_the_network_is_built_from_every_setting():
    from wl_preproc.eye.detect.uneye import _network

    network = _network(UneyeParams(weights="weights_1+2+3", min_saccade_duration_ms=30, min_saccade_gap_ms=7), FS_HZ)
    assert network.weights_name.endswith("/training/weights_1+2+3")
    assert (network.sampfreq, network.min_sacc_dur, network.min_sacc_dist, network.use_gpu) == (FS_HZ, 30, 7, False)
