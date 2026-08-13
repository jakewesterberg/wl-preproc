"""Emit an Intan RHS session in the "One File Per Signal Type" layout.

Flat .dat arrays rather than the traditional format's interleaved 128-sample
blocks: far easier to generate correctly. This is not a claim that a
third-party reader can open it — info.rhs is currently an identification stub
(magic number, version, sample rate, stim step size, channel count) rather
than a parseable Intan header, so e.g. spikeinterface.extractors.read_intan
cannot open these fixtures yet (verified: it raises IndexError trying to parse
the header). See _write_header below.

Files written:
    info.rhs        header, beginning with the magic number 0xD69127AC
    time.dat        int32 sample indices from zero
    amplifier.dat   int16, channel-interleaved, x 0.195 uV, NO offset
    stim.dat        uint16 stim words, one per channel per sample
    digitalin.dat   uint16, all 16 inputs bit-packed per sample

dcamplifier.dat is deliberately not written — see spec section 6.3.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
from wl_sync.barcode import encode

from wl_preproc.synth.recipe import SessionRecipe
from wl_preproc.synth.stim import SETTLE_DURATION_S, pack_stim_word
from wl_preproc.synth.timeline import apply_drift
from wl_preproc.synth.truth import GroundTruth

RHS_SAMPLE_RATE_HZ = 30_000.0
# A third distinct tick origin — see syncbox.py. wl_sync.barcode.IDLE_MIN_US is
# 400_000us, and decode_edges skips any frame whose preceding idle is shorter
# than that; a pre-roll below 0.4s drops the first barcode. 0.45 clears the
# threshold with margin rather than sitting exactly on it.
RHS_PRE_ROLL_S = 0.45
STIM_STEP_SIZE_A = 10e-6
UV_PER_BIT = 0.195
NOISE_UV = 6.0
ARTIFACT_UV = 4000.0

BARCODE_DIGITAL_BIT = 0
STROBE_DIGITAL_BIT = 1

_MAGIC = 0xD69127AC


def _write_header(path: Path, recipe: SessionRecipe) -> None:
    """An identification stub, NOT a parseable Intan header: magic number,
    version, sample rate, stim step size and channel count. Enough to identify
    the file and scale stim magnitudes, which is what the fixtures are for.

    Writing a byte-correct Standard Intan RHS header is deliberately deferred
    rather than improvised here — reverse-engineering one from a reader
    implementation would fabricate a format, the same reasoning that keeps
    dcamplifier.dat unwritten (spec section 6.3)."""
    payload = struct.pack("<IhhffI", _MAGIC, 1, 2, RHS_SAMPLE_RATE_HZ, STIM_STEP_SIZE_A, recipe.n_ap_channels)
    path.write_bytes(payload)


def write_rhs(
    dir_path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0
) -> Path:
    rng = np.random.default_rng(recipe.seed + 3)
    fs = RHS_SAMPLE_RATE_HZ
    n_channels = recipe.n_ap_channels
    n_samples = int((recipe.duration_s + RHS_PRE_ROLL_S) * fs)

    out = dir_path / f"{recipe.session_id}_rhs"
    out.mkdir(exist_ok=True)

    amplifier = rng.normal(0.0, NOISE_UV / UV_PER_BIT, (n_samples, n_channels))
    stim = np.zeros((n_samples, n_channels), dtype=np.uint16)

    settle_samples = int(SETTLE_DURATION_S * fs)
    artifact_bits = ARTIFACT_UV / UV_PER_BIT

    for event in truth.stim_events:
        onset = int((apply_drift(event.onset_s, drift_ppm) + RHS_PRE_ROLL_S) * fs)
        pulse_end = onset + max(1, int(event.duration_s * fs))
        settle_end = min(pulse_end + settle_samples, n_samples)
        if settle_end <= onset:
            continue

        sign = -1.0 if event.negative else 1.0
        amplifier[onset:pulse_end, event.channel] += sign * artifact_bits
        # Settle: a decaying tail, which is what the blanking window covers.
        tail = np.linspace(1.0, 0.0, settle_end - pulse_end, endpoint=False)
        amplifier[pulse_end:settle_end, event.channel] += sign * artifact_bits * 0.3 * tail

        during_pulse = pack_stim_word(event.magnitude, negative=event.negative)
        during_settle = pack_stim_word(0, amp_settle=True)
        stim[onset:pulse_end, event.channel] = during_pulse
        stim[pulse_end:settle_end, event.channel] = during_settle

    digital = np.zeros(n_samples, dtype=np.uint16)
    for value, start_s in truth.barcodes:
        cursor = int((apply_drift(start_s, drift_ppm) + RHS_PRE_ROLL_S) * fs)
        for level, duration_us in encode(value):
            width = int(round(duration_us * 1e-6 * fs))
            if level and cursor + width <= n_samples:
                digital[cursor : cursor + width] |= 1 << BARCODE_DIGITAL_BIT
            cursor += width

    # Strobe only, never the code words themselves: RHS has 16 digital inputs and
    # 16 data lines plus strobe plus barcode does not fit (spec section 4.2).
    for time_s, _word in truth.code_words:
        sample = int((apply_drift(time_s, drift_ppm) + RHS_PRE_ROLL_S) * fs)
        width = max(1, int(0.001 * fs))
        if sample + width <= n_samples:
            digital[sample : sample + width] |= 1 << STROBE_DIGITAL_BIT

    _write_header(out / "info.rhs", recipe)
    np.arange(n_samples, dtype=np.int32).tofile(out / "time.dat")
    amplifier.astype(np.int16).tofile(out / "amplifier.dat")
    stim.tofile(out / "stim.dat")
    digital.tofile(out / "digitalin.dat")
    return out
