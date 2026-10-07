"""U'n'Eye's wrapper against its copied classifier called directly (design
spec `2026-10-06-uneye-design.md` section 5): the wrapper adds the pieces,
the CPU and the run encoding, and nothing to what the network decides."""

from __future__ import annotations

import numpy as np

from tests.eye.detect._uneye_traces import FS_HZ, all_usable, planted
from wl_preproc.eye.detect.labels import Label, Run, true_runs
from wl_preproc.eye.detect.uneye import DEFAULT_UNEYE_PARAMS, UneyeParams, detect_uneye


def _direct(gaze, params, offset=0):
    """`DNN.predict` on `gaze`, as upstream's README calls it, with `params`'
    settings, its saccade samples as runs starting at `offset`."""
    from wl_preproc.eye.detect.uneye import _TRAINING
    from wl_preproc.eye.vendor.uneye.classifier import DNN

    network = DNN(weights_name=str(_TRAINING / params.weights), sampfreq=FS_HZ,
                  min_sacc_dist=params.min_saccade_gap_ms, min_sacc_dur=params.min_saccade_duration_ms)
    network.use_gpu = False
    prediction, probability = network.predict(gaze[:, 0], gaze[:, 1])
    return [Run(offset + start, offset + stop, Label.SACCADE, reliability=float(np.mean(probability[1, start:stop])))
            for start, stop in true_runs(prediction == 1)]


def test_on_one_continuous_trace_the_runs_are_the_networks_own():
    gaze, _onsets = planted((0.5, 2.0, 5.0, 10.0) * 3)
    runs = detect_uneye(gaze, np.zeros_like(gaze), all_usable(len(gaze)), FS_HZ, DEFAULT_UNEYE_PARAMS)
    assert runs and runs == _direct(gaze, DEFAULT_UNEYE_PARAMS)


def test_across_a_gap_each_piece_is_the_networks_own_at_its_offset():
    gaze, onsets = planted((0.5, 2.0, 5.0, 10.0) * 3)
    gap = slice(onsets[5] - 30, onsets[5] + 30)
    available = all_usable(len(gaze))
    available[gap] = Label.INVALID
    runs = detect_uneye(gaze, np.zeros_like(gaze), available, FS_HZ, DEFAULT_UNEYE_PARAMS)
    assert runs == (_direct(gaze[:gap.start], DEFAULT_UNEYE_PARAMS)
                    + _direct(gaze[gap.stop:], DEFAULT_UNEYE_PARAMS, offset=gap.stop))


def test_every_setting_reaches_the_network():
    """Another network and a longer minimum, as a paramset might choose."""
    gaze, _onsets = planted((0.5, 2.0, 5.0, 10.0) * 3)
    params = UneyeParams(weights="weights_1+2+3", min_saccade_duration_ms=30, min_saccade_gap_ms=1)
    runs = detect_uneye(gaze, np.zeros_like(gaze), all_usable(len(gaze)), FS_HZ, params)
    assert runs == _direct(gaze, params)
    assert runs != _direct(gaze, DEFAULT_UNEYE_PARAMS)
