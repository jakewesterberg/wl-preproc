"""U'n'Eye's default network against human coders, at 500 Hz (design spec
`2026-10-06-uneye-design.md` amendments 1, 3 and 7): the evidence the
requester chose it and its merge gap on, on 2026-10-06 and 2026-10-07.

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
#: Coded saccades this large or larger are the ones a split would misreport
#: most: one saccade stored as several, each smaller (amendment 7).
LARGE_DEG = 6.0


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


def _splits(recordings, params) -> dict[str, tuple[int, int]]:
    """Per coder: (coded saccades of `LARGE_DEG` or more, how many of them
    two or more detected saccades overlap). Overlap alone cannot see a split,
    since every fragment overlaps the coded saccade."""
    detected = [detect_uneye(gaze, np.zeros_like(gaze), available, fs, params)
                for gaze, available, fs, _coders in recordings]
    splits = {}
    for coder in ("MN", "RA"):
        large = split = 0
        for runs, (gaze, _available, _fs, coders) in zip(detected, recordings, strict=True):
            for start, stop in true_runs(coders[coder] == SACCADE_CODE):
                if not float(np.hypot(*(gaze[stop - 1] - gaze[start]))) >= LARGE_DEG:
                    continue
                large += 1
                split += sum(run.start < stop and run.stop > start for run in runs) >= 2
        splits[coder] = (large, split)
    return splits


def test_the_default_keeps_a_large_saccade_whole(andersson):
    """A coded saccade of 6 deg or more is stored as one saccade, not as
    several smaller ones (amendment 7). Measured on 2026-10-07 at the 20 ms
    merge gap: 3 of MN's 177 and 3 of RA's 169 split, against 44 and 48 at
    upstream's 1 ms."""
    splits = _splits(andersson, DEFAULT_UNEYE_PARAMS)
    for coder, (large, split) in splits.items():
        assert split / large <= 0.05, (coder, splits)


def test_the_default_network_finds_nearly_every_coded_saccade(andersson):
    """Measured on 2026-10-07 at the 20 ms merge gap: 533 of MN's 541 coded
    saccades and 541 of RA's 548, 99% for each, with 583 and 584 detected
    saccades overlapping none of theirs (at 1 ms: 530 and 538, with 886 and
    872)."""
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
