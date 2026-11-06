"""A short loss of data inside a saccade leaves it whole, for every
registered detector (design spec `2026-11-06-eye-lost-data-and-region-design.md`
section 11): the requester's question of 2026-11-06, asked jointly with
wl-xcon, began with a dropped frame splitting an 8-degree saccade in two,
or erasing it."""

import json
from pathlib import Path

import numpy as np
import pytest

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.registry import DETECTORS
from wl_preproc.eye.detect.velocity import velocity

_V1 = json.loads((Path(__file__).parents[3] / "docs/fixtures/saccade/v1.json").read_text())


@pytest.mark.parametrize("name", sorted(DETECTORS))
def test_a_dropped_frame_inside_a_saccade_leaves_it_whole(name):
    """v1's `centre-horizontal` 8-degree step. A frame dropped after row `row`
    makes rows `row - 1` to `row + 2` lost, their velocities spanning the gap
    and their positions measured (section 2); wherever it falls inside the
    saccade, each detector's saccades are as they were."""
    detector = DETECTORS[name]
    if detector.unavailable is not None and detector.unavailable() is not None:
        pytest.skip(detector.unavailable())
    (case,) = [case for case in _V1["traces"] if case["name"] == "centre-horizontal"]
    gaze = np.array(case["gaze_deg"], dtype=float)
    fs_hz = _V1["fs_hz"]
    v = velocity(gaze, fs_hz)

    def saccades(available):
        return [(run.start, run.stop) for run in detector.detect(gaze, v, available, fs_hz, detector.defaults)
                if run.label in (Label.SACCADE, Label.MICROSACCADE)]

    whole = saccades(np.full(len(gaze), None, dtype=object))
    (start, stop), = [(start, stop) for start, stop in whole if start <= 206 < stop]
    for row in range(start + 2, stop - 3):
        available = np.full(len(gaze), None, dtype=object)
        available[row - 1:row + 3] = Label.LOST
        assert saccades(available) == whole, row
