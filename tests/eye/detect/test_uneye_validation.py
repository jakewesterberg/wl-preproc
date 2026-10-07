"""U'n'Eye's default network against human coders, at 500 Hz (design spec
`2026-10-06-uneye-design.md` section 3): the evidence the requester chose it
on, on 2026-10-06.

The Andersson et al. (2017) recordings, each coded by two people (MN and
RA): video-tracker data from people viewing dots, images and videos, at
500 Hz, the rig's rate. None of the copied networks was trained on them.
Gated on `WLPP_ANDERSSON_DATA`; the dataset is GPL-3.0 and is never
committed. Absolute precision is understated here: the coders label
glissades and pursuit apart from saccades, and a network trained on other
data may call part of either a saccade."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from tests.eye.detect._andersson import ANDERSSON_FILES, load_andersson
from wl_preproc.eye.detect.labels import Label, true_runs
from wl_preproc.eye.detect.uneye import DEFAULT_UNEYE_PARAMS, UneyeParams, detect_uneye

SACCADE_CODE = 2


@pytest.fixture(scope="module")
def andersson():
    """Each recording's gaze in degrees, its validity mask (missing samples,
    dilated as the pipeline's mask dilates them), its rate, and each coder's
    labels."""
    root = os.environ.get("WLPP_ANDERSSON_DATA")
    if not root:
        pytest.skip("WLPP_ANDERSSON_DATA is not set")
    from wl_preproc.eye.detect.remodnav import _dilate
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS

    recordings = []
    for stim, names in ANDERSSON_FILES.items():
        for name in names:
            _xy, mn, _p, _f = load_andersson(Path(root), stim, name.format("MN"))
            xy_px, ra, px2deg, fs = load_andersson(Path(root), stim, name.format("RA"))
            gaze = xy_px * px2deg
            unusable = _dilate(np.isnan(gaze).any(axis=1), DEFAULT_VALIDITY_PARAMS.dilate_samples)
            available = np.array([Label.INVALID if bad else None for bad in unusable], dtype=object)
            recordings.append((gaze, available, fs, {"MN": np.asarray(mn), "RA": np.asarray(ra)}))
    return recordings


def _scores(recordings, params) -> dict[str, tuple[int, int, int]]:
    """Per coder: (coded saccades a detected one overlaps, coded saccades
    none overlaps, detected saccades that overlap no coded one)."""
    detected = [detect_uneye(gaze, np.zeros_like(gaze), available, fs, params)
                for gaze, available, fs, _coders in recordings]
    scores = {}
    for coder in ("MN", "RA"):
        found = missed = extra = 0
        for runs, (_gaze, _available, _fs, coders) in zip(detected, recordings, strict=True):
            coded = true_runs(coders[coder] == SACCADE_CODE)
            hit = [any(run.start < stop and run.stop > start for run in runs) for start, stop in coded]
            found += sum(hit)
            missed += len(hit) - sum(hit)
            extra += sum(not any(run.start < stop and run.stop > start for start, stop in coded) for run in runs)
        scores[coder] = (found, missed, extra)
    return scores


def test_the_default_network_finds_nearly_every_coded_saccade(andersson):
    """Measured on 2026-10-06: 530 of MN's 541 coded saccades and 538 of
    RA's 548, 98% for each, with 886 and 872 detected saccades overlapping
    none of theirs."""
    scores = _scores(andersson, DEFAULT_UNEYE_PARAMS)
    for coder, (found, missed, _extra) in scores.items():
        assert found / (found + missed) >= 0.95, (coder, scores)


def test_it_finds_far_more_of_them_than_upstreams_general_network_at_this_rate(andersson):
    """`weights_1+2+3`, the saccade-detection spec's first default, misses
    large saccades at 500 Hz. Measured on 2026-10-06: 386 of MN's 541 and
    390 of RA's 548, 71% for each."""
    default = _scores(andersson, DEFAULT_UNEYE_PARAMS)
    general = _scores(andersson, UneyeParams(weights="weights_1+2+3", min_saccade_duration_ms=6, min_saccade_gap_ms=1))
    for coder in ("MN", "RA"):
        recall = {name: found / (found + missed) for name, (found, missed, _) in
                  (("default", default[coder]), ("general", general[coder]))}
        assert recall["default"] >= recall["general"] + 0.2, (coder, recall)
