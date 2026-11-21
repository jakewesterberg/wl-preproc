"""The eye tracker's analog copy on the NI's analog inputs (design spec
`2026-10-18-training-records-and-backup-clock-design.md`, amendments 77-84).

The tests read the NI file's bytes and the eye's own traces (`eye_traces`),
and re-derive the copy's timing from the recipe's nominal numbers rather than
from the writer's arrival function, so they cross the boundary.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest

from wl_preproc.contracts.events import TaskTypeCode
from wl_preproc.synth.ohdpi import OHDPI_FPS, OHDPI_PRE_ROLL_S, eye_traces
from wl_preproc.synth.recipe import EYE_RECIPE, RECIPES, BlockSpec, EyeCopy, MontageSpec, SessionRecipe
from wl_preproc.synth.session import generate_session
from wl_preproc.synth.spikeglx import (
    NIDQ_N_DIGITAL_WORDS,
    NIDQ_SAMPLE_RATE_HZ,
    SPIKEGLX_PRE_ROLL_S,
    eye_copy_frame_arrivals_s,
)
from wl_preproc.timebase._nidq_meta import read_nidq_meta

N_XA = 16
COPY_CHANNEL = 3
NOISE_V = EyeCopy(channel=0).noise_v


def _recipe(**update) -> SessionRecipe:
    """The eye recipe without a sync box (the NI keeps time), long enough to
    hold a stall: 12 trials of 3 s."""
    base = {
        **EYE_RECIPE.model_dump(),
        "systems": ("spikeglx", "ohdpi"),
        "blocks": (BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=12, trial_duration_s=3.0),),
        "montages": (MontageSpec(start_s=0.0, end_s=36.0),),
    }
    return SessionRecipe(**{**base, **update})


def _generate(root: Path, recipe: SessionRecipe) -> Path:
    generate_session(root, recipe)
    (nidq,) = root.rglob("*.nidq.bin")
    return nidq


def _volts(nidq: Path) -> np.ndarray:
    meta = read_nidq_meta(nidq.with_suffix(".meta"))
    words = np.fromfile(nidq, dtype=np.int16).reshape(-1, meta.n_channels)
    return words[:, : meta.n_analog_channels] * 5.0 / 32768.0


def _frame_starts(n_frames: int, extra_s: float = 0.0) -> np.ndarray:
    """Each eye frame's first NI sample, from the recipe's nominal numbers (no
    clock drift in these recipes): the frame's capture, on the NI's origin."""
    seconds = np.arange(n_frames) / OHDPI_FPS - OHDPI_PRE_ROLL_S + SPIKEGLX_PRE_ROLL_S + extra_s
    return np.rint(seconds * NIDQ_SAMPLE_RATE_HZ).astype(int)


def _channel_at_frames(volts: np.ndarray, channel: int, n_frames: int, delay_s: float) -> tuple[np.ndarray, np.ndarray]:
    """The channel's value `delay_s` after each frame's capture; the frames it
    exists for, and the values."""
    starts = _frame_starts(n_frames, delay_s)
    keep = (starts >= 0) & (starts < len(volts))
    return np.flatnonzero(keep), volts[starts[keep], channel]


# Computed from the writer at the branch's starting commit (fc33ebe), before
# `eye_copy` existed: sha256 of each recipe's `.nidq.bin` and `.nidq.meta`.
NIDQ_HASHES_AT_FC33EBE = {
    "ci": (
        "12d6bc498c16330aa91e505e3ee554e086080f0756e3bd631727656859a77652",
        "fdd79c4982900da115fea2af05979aedd6d5ab2d4a4f986d6d4826b24194f8f6",
    ),
    "ni-clock": (
        "d802f4cad382bfd2dca9ddb52ab09c78b0ea6d79d9ea835febcf0b50e986ee82",
        "2503e86e9b3c794b73334267e3378181d0c85e14896e07d2a95bb3c1e35cac11",
    ),
    "eye": (
        "7bce1753035fe0941c1f4adc458506be91c5b0190a43fd1b54dbf49e7a78dbbd",
        "a30a1981416189ca8ceace9d8e0c819bf5e6fb5ca9015b0e6db04aca90aa9bec",
    ),
}

# sha256 of the eye file (`OpenIris-synthetic.txt`) for the recipes that write
# one, computed at fc33ebe, before `write_ohdpi` was split into `eye_traces`.
# The file passes through sin/cos, which Linux rounds otherwise in the last
# digits, so it is compared where it was taken (macOS) and skipped elsewhere.
OHDPI_HASHES_AT_FC33EBE = {
    "eye": "4cf5a5ff117c84c02c81d01ada9a48058d9c28337ddf8036c5360ceef5229343",
    "drift": "6fa3ddc063c2f350a87c3d1b95ba7270e8269e87f2e12eaf060963c0a83e08d0",
}


@pytest.mark.parametrize("name", sorted(NIDQ_HASHES_AT_FC33EBE))
def test_a_recipe_without_a_copy_writes_the_same_bytes(tmp_path, name):
    assert RECIPES[name].eye_copy is None
    nidq = _generate_plain(tmp_path, RECIPES[name])
    bin_hash, meta_hash = NIDQ_HASHES_AT_FC33EBE[name]
    assert hashlib.sha256(nidq.read_bytes()).hexdigest() == bin_hash
    assert hashlib.sha256(nidq.with_suffix(".meta").read_bytes()).hexdigest() == meta_hash


@pytest.mark.skipif(sys.platform != "darwin", reason="trig rounds differently on Linux; hashes taken on macOS")
@pytest.mark.parametrize("name", sorted(OHDPI_HASHES_AT_FC33EBE))
def test_a_recipe_without_a_copy_writes_the_same_eye_file(tmp_path, name):
    generate_session(tmp_path, RECIPES[name])
    (eye,) = tmp_path.rglob("OpenIris-synthetic.txt")
    assert hashlib.sha256(eye.read_bytes()).hexdigest() == OHDPI_HASHES_AT_FC33EBE[name]


def _generate_plain(root: Path, recipe: SessionRecipe) -> Path:
    return _generate(root, recipe)


def test_the_copy_lands_on_its_channel(tmp_path):
    copy = EyeCopy(channel=COPY_CHANNEL)
    recipe = _recipe(eye_copy=copy)
    nidq = _generate(tmp_path / "with", recipe)
    meta = read_nidq_meta(nidq.with_suffix(".meta"))
    assert meta.n_analog_channels == N_XA
    assert meta.n_digital_words == NIDQ_N_DIGITAL_WORDS

    # The digital words are the same bytes as without a copy: the same barcodes.
    plain = _generate(tmp_path / "without", _recipe())
    with_words = np.fromfile(nidq, dtype=np.int16).reshape(-1, meta.n_channels)[:, N_XA:]
    plain_words = np.fromfile(plain, dtype=np.int16).reshape(-1, NIDQ_N_DIGITAL_WORDS)
    assert np.array_equal(with_words, plain_words)

    # The copy follows the eye's left P1-P4 X, `lag_s` late. The copy is the
    # specified 0.7 X + 0.3 Y, so against X alone it cannot reach 0.95 (X is
    # 0.7 of the mix: 0.95 is the ceiling with no noise at all, measured 0.933
    # here); the threshold is the matcher's own floor, 0.9.
    volts = _volts(nidq)
    x, _y = eye_traces(recipe).left_p1_minus_p4()
    frames, seen = _channel_at_frames(volts, COPY_CHANNEL, len(x), copy.lag_s)
    assert np.corrcoef(seen, x[frames])[0, 1] > 0.9
    for other in range(N_XA):
        if other == COPY_CHANNEL:
            continue
        _, noise = _channel_at_frames(volts, other, len(x), copy.lag_s)
        assert abs(np.corrcoef(noise, x[frames])[0, 1]) < 0.2, other


def test_the_copy_is_not_the_column_the_matcher_reads(tmp_path):
    """No linear map of any single candidate column reproduces the copy: the
    matcher's tests cannot pass on the generator and the matcher agreeing (78)."""
    copy = EyeCopy(channel=COPY_CHANNEL)
    recipe = _recipe(eye_copy=copy)
    volts = _volts(_generate(tmp_path, recipe))
    traces = eye_traces(recipe)
    x, y = traces.left_p1_minus_p4()
    candidates = {
        "P1-P4 X": x,
        "P1-P4 Y": y,
        "P1 X": traces.common_x + traces.p1x_jitter_left,
        "P1 Y": traces.common_y + traces.p1y_jitter_left,
    }
    frames, seen = _channel_at_frames(volts, COPY_CHANNEL, len(x), copy.lag_s)
    # Not "residual above the noise", which the channel's own noise and jitter
    # meet for a pure-X copy as well: the best single column must leave a good
    # part of the variance unexplained.
    # In 10-frame means, which take out the per-frame jitter and the white
    # P1 noise a pure-X copy would also lose, so that a pure-X copy explains
    # over 0.95 and only the mixing is left to fail it.
    block = 10
    usable = len(frames) // block * block
    seen_blocks = seen[:usable].reshape(-1, block).mean(axis=1)
    for name, column in candidates.items():
        column_blocks = column[frames][:usable].reshape(-1, block).mean(axis=1)
        design = np.column_stack((column_blocks, np.ones(len(seen_blocks))))
        coef, *_ = np.linalg.lstsq(design, seen_blocks, rcond=None)
        explained = 1.0 - np.var(seen_blocks - design @ coef) / np.var(seen_blocks)
        assert explained < 0.95, (name, explained)


def _best_delay_s(volts: np.ndarray, x: np.ndarray, frames: np.ndarray) -> float:
    """The delay after capture, in 1 ms steps, at which the channel best follows
    the eye over `frames`."""
    best, best_r = 0.0, -1.0
    for delay_ms in range(0, 80):
        starts = _frame_starts(len(x), delay_ms * 1e-3)[frames]
        r = np.corrcoef(volts[starts, COPY_CHANNEL], x[frames])[0, 1]
        if r > best_r:
            best, best_r = delay_ms * 1e-3, r
    return best


def test_a_stall_delays_its_stretch(tmp_path):
    copy = EyeCopy(channel=COPY_CHANNEL, stall=(20.0, 25.0, 0.04))
    recipe = _recipe(eye_copy=copy)
    volts = _volts(_generate(tmp_path, recipe))
    x, _y = eye_traces(recipe).left_p1_minus_p4()
    session_s = np.arange(len(x)) / OHDPI_FPS - OHDPI_PRE_ROLL_S
    inside = np.flatnonzero((session_s >= 20.5) & (session_s < 24.5))
    outside = np.flatnonzero((session_s >= 5.0) & (session_s < 15.0))
    # One eye frame (2 ms) and the processing jitter's own spread.
    assert _best_delay_s(volts, x, outside) == pytest.approx(copy.lag_s, abs=0.004)
    assert _best_delay_s(volts, x, inside) == pytest.approx(copy.lag_s + 0.04, abs=0.004)


def test_source_noise_and_flat_carry_no_eye(tmp_path):
    recipe = _recipe(eye_copy=EyeCopy(channel=COPY_CHANNEL, source="flat"))
    flat = _volts(_generate(tmp_path / "flat", recipe))[:, COPY_CHANNEL]
    assert np.all(flat == flat[0])
    assert flat[0] == pytest.approx(0.3, abs=5.0 / 32768)

    recipe = _recipe(eye_copy=EyeCopy(channel=COPY_CHANNEL, source="noise"))
    noise = _volts(_generate(tmp_path / "noise", recipe))[:, COPY_CHANNEL]
    assert abs(noise.mean()) < 0.001
    assert noise.std() == pytest.approx(NOISE_V, rel=0.05)


def test_the_copy_is_deterministic_and_clipped(tmp_path):
    recipe = _recipe(eye_copy=EyeCopy(channel=COPY_CHANNEL, offset_v=4.9999, gain_v=0.2, clip_v=2.0))
    first = _generate(tmp_path / "a", recipe).read_bytes()
    second = _generate(tmp_path / "b", recipe).read_bytes()
    assert first == second
    volts = _volts(_generate(tmp_path / "c", recipe))
    assert volts[:, COPY_CHANNEL].max() <= 2.0 + 5.0 / 32768


def test_the_frames_shown_are_late_as_the_instrument_is(tmp_path):
    """Each frame's delay after capture, less the DAC's lag: a median near the
    paper's 1.1 ms, and 2% of frames 10-50 ms (amendment 84), none of them
    holding the frames behind them back."""
    recipe = _recipe(eye_copy=EyeCopy(channel=COPY_CHANNEL, lag_s=0.0))
    n_frames = len(eye_traces(recipe).t)
    nominal = np.arange(n_frames) / OHDPI_FPS - OHDPI_PRE_ROLL_S + SPIKEGLX_PRE_ROLL_S
    delay = eye_copy_frame_arrivals_s(recipe, recipe.eye_copy, 0.0) - nominal
    assert 0.9e-3 < np.median(delay) < 1.3e-3
    slow = np.mean((delay >= 10e-3) & (delay <= 50e-3))
    assert 0.01 < slow < 0.04
    # No chain: the frame after a slow one arrives on its own schedule.
    after_slow = delay[1:][delay[:-1] >= 10e-3]
    assert np.median(after_slow) < 3e-3
