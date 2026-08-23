"""Emit an Intan RHS session in the "One File Per Signal Type" layout.

Files written:
    info.rhs        Standard Intan RHS header, byte-correct (see rhs_header.py)
    time.dat        int32 sample indices from zero
    amplifier.dat   int16, channel-interleaved, x 0.195 uV, NO offset —
                    noise, the planted spikes, and the stim artifacts
    stim.dat        uint16 stim words, one per channel per sample
    digitalin.dat   uint16, bit 0 barcode and bit 1 strobe; no other bit is set

dcamplifier.dat is deliberately not written — see spec section 6.3.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from wl_sync.barcode import encode

from wl_preproc.synth.recipe import SessionRecipe
from wl_preproc.synth.rhs_header import write_rhs_header
# The spike waveform is imported rather than restated: one definition of the
# shape, scaled per system by that system's own uV-per-bit. A second copy here
# would be free to drift from the one write_spikeglx plants, and the two
# emitters are meant to be planting the same ground truth.
from wl_preproc.synth.spikeglx import SPIKE_TEMPLATE_UV
from wl_preproc.synth.stim import SETTLE_DURATION_S, pack_stim_word
from wl_preproc.synth.timeline import (
    SAMPLE_COUNT_ROUNDING_SLACK,
    apply_drift,
    code_word_span_s,
)
from wl_preproc.synth.truth import GroundTruth

# The RHS controller has its own clock, so recipe.ap_sample_rate_hz — which
# describes the Neuropixels stream — is deliberately not consulted here. The
# two happen to agree at 30 kHz today; nothing requires them to, and a fixture
# that silently shared one rate could not exercise the alignment stage that
# exists precisely because they are independent.
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

# The strobe pulse must be strictly narrower than timeline.CODE_WORD_SPACING_S
# (1 ms) or consecutive strobes are contiguous: they merge into a single long
# high with no falling edge between them, and there is no countable edge per
# word. That is not hypothetical — at a 1 ms width the 31 code words of
# STIM_RECIPE rendered as 5 rising edges, silently gutting the tier-B
# "independent strobe witness" (spec section 4.7) these fixtures exist to
# provide. 500 us against 1 ms spacing is 15 samples high and at least 15 low
# at 30 kHz, so every word keeps its own edge. It is also the width the spec
# derives from the actual emitter — MonkeyLogic strobes for 500 us within a
# 750 us code — so this is spec-faithful, not merely convenient.
STROBE_WIDTH_S = 0.0005


def write_rhs(
    dir_path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0
) -> Path:
    """Render one session of planted ground truth as an RHS directory.

    Precondition: **stim events must not overlap on a channel.** Amplifier
    samples accumulate (`+=`) but stim words are assigned (`=`), so two events
    sharing a channel and a sample would sum their artifacts while keeping only
    the later word — the amplifier and the stim file would then describe
    different stimulation, which is the truth/file disagreement these fixtures
    exist to rule out. `build_timeline` spaces pulses inside guard bands and
    `SessionRecipe._coherent` rejects geometry that would pack them tighter, so
    nothing shipped can reach it; a hand-built `GroundTruth` can.
    """
    rng = np.random.default_rng(recipe.seed + 3)
    fs = RHS_SAMPLE_RATE_HZ
    n_channels = recipe.n_ap_channels
    # `code_word_span_s` -- shared with `synth/spikeglx.py` -- extends the
    # buffer past recipe.duration_s when SESSION_END lands after it, which
    # timeline.py's own spacing rule always does; barcodes never need this.
    # Every "One File Per Signal Type" output shares this n_samples, so
    # amplifier.dat, stim.dat and time.dat all grow the same handful of
    # samples alongside digitalin.dat -- they must, since a reader indexes
    # all four by the same sample count. Before this fix the digital buffer
    # ended at duration_s + pre-roll and the last code word's own strobe fell
    # past it on every session -- see
    # test_every_code_word_gets_a_strobe_edge_in_the_rhs_digital_line.
    session_span_s = code_word_span_s(recipe, truth, drift_ppm, STROBE_WIDTH_S)
    n_samples = int((session_span_s + RHS_PRE_ROLL_S) * fs) + SAMPLE_COUNT_ROUNDING_SLACK

    out = dir_path / f"{recipe.session_id}_rhs"
    out.mkdir(exist_ok=True)

    amplifier = rng.normal(0.0, NOISE_UV / UV_PER_BIT, (n_samples, n_channels))
    stim = np.zeros((n_samples, n_channels), dtype=np.uint16)

    # The same planted spikes write_spikeglx renders, at this system's scale.
    # Without them GroundTruth.spikes would describe events that exist nowhere
    # in the emitted session, and artifact removal — which fails by
    # over-blanking — would have no planted signal whose survival can be
    # asserted underneath the artifacts.
    template = SPIKE_TEMPLATE_UV / UV_PER_BIT
    for time_s, channel in truth.spikes:
        start = int((apply_drift(time_s, drift_ppm) + RHS_PRE_ROLL_S) * fs)
        stop = start + template.size
        if stop < n_samples:
            amplifier[start:stop, channel] += template

    settle_samples = int(SETTLE_DURATION_S * fs)
    artifact_bits = ARTIFACT_UV / UV_PER_BIT

    for event in truth.stim_events:
        onset = int((apply_drift(event.onset_s, drift_ppm) + RHS_PRE_ROLL_S) * fs)
        # Clamp before deriving settle_end: an event straddling the end of the
        # buffer otherwise leaves pulse_end past n_samples, and the settle tail
        # is then a linspace over a negative count, which raises.
        pulse_end = min(onset + max(1, int(event.duration_s * fs)), n_samples)
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
    strobe_width = max(1, int(STROBE_WIDTH_S * fs))
    for time_s, _word in truth.code_words:
        sample = int((apply_drift(time_s, drift_ppm) + RHS_PRE_ROLL_S) * fs)
        if sample + strobe_width <= n_samples:
            digital[sample : sample + strobe_width] |= 1 << STROBE_DIGITAL_BIT

    write_rhs_header(
        out / "info.rhs",
        recipe,
        sample_rate_hz=fs,
        stim_step_size_a=STIM_STEP_SIZE_A,
        digital_input_bits=(BARCODE_DIGITAL_BIT, STROBE_DIGITAL_BIT),
    )
    np.arange(n_samples, dtype=np.int32).tofile(out / "time.dat")
    # Clip, do not let astype wrap: int16 overflow flips the sign, so an
    # out-of-range artifact would silently become a deflection of the opposite
    # polarity rather than a saturated one.
    int16 = np.iinfo(np.int16)
    np.clip(amplifier, int16.min, int16.max).astype(np.int16).tofile(
        out / "amplifier.dat"
    )
    stim.tofile(out / "stim.dat")
    digital.tofile(out / "digitalin.dat")
    return out
