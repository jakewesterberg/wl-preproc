"""Emit a SpikeGLX run: an imec AP stream and an NI (`nidq`) stream, each an
interleaved int16 .bin plus a .meta sidecar.

**The barcode is carried on an NI digital line, not on the imec SY channel.**
Spec section 4.5: "SpikeGLX handles imec-NI sync internally... The barcode
aligns SpikeGLX-as-a-whole to session time via one NI digital line. Fewer
moving parts; the imec SMA stays free." That last clause is the point — the
imec SY channel is emitted, because the format always carries one, but nothing
drives it. Section 12 picks the PXIe-6353 for exactly this reason: 32
hardware-timed Port 0 lines fit the 16-bit codes, the strobe and the barcode.

> This emitter drove the barcode onto imec SY from Phase 1a until Phase 1c-4,
> with a test asserting that layout. Nothing caught the divergence because no
> code extracted a SpikeGLX barcode until 1c-4 wrote one, and then its plan's
> test globbed `*.nidq.bin` and found nothing. The same class as spec section
> 4.2's impossible strobe: a spec number no code has executed yet is a
> hypothesis, and so is a fixture no reader has consumed.

Field names in the .meta are what SpikeInterface's reader requires. Where this
disagrees with the reader, the reader wins — it is the thing the pipeline will
actually use.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from wl_sync.barcode import encode

from wl_preproc.synth.recipe import SessionRecipe
from wl_preproc.synth.timeline import (
    SAMPLE_COUNT_ROUNDING_SLACK,
    apply_drift,
    code_word_span_s,
)
from wl_preproc.synth.truth import GroundTruth

SPIKE_TEMPLATE_UV = np.array(
    [0, -10, -40, -120, -200, -140, -40, 30, 60, 45, 25, 10, 0], dtype=np.float64
)
AP_GAIN = 500.0
NOISE_UV = 8.0
UV_PER_BIT = 2.34375  # Neuropixels 1.0 at gain 500

# A different tick origin from the sync box, deliberately — see syncbox.py. It
# is shared by both of this system's streams: SpikeGLX starts and stops imec
# and NI together, which is what makes "spikeglx" one segment unit rather than
# two (spec section 8.3.1's `system`, not `device`).
SPIKEGLX_PRE_ROLL_S = 0.7

# The NI card samples the barcode at 30 kHz. Spec section 4.1: "Barcode
# consumers are only 30 kHz samplers (NI, Intan)" — so this matches imec's rate
# today by that constraint rather than by sharing a clock, and it is declared
# separately because nothing requires the two to agree. `extract_spikeglx`
# reads it out of the .meta rather than assuming it, and a test writes a .meta
# at a different rate to prove that: with every fixture in the repo at 30 kHz,
# a hardcoded rate is otherwise indistinguishable from a rate that was read.
NIDQ_SAMPLE_RATE_HZ = 30_000.0

# Which Port 0 line carries the barcode. Zero-based, and this project's wiring
# convention rather than anything SpikeGLX records: the digital word's lines are
# named XD0.. and carry no semantics. `wl_preproc/timebase/extract.py` states
# the consuming half of the same convention.
NIDQ_BARCODE_XD_LINE = 0

# Spec section 4.2's routing table gives the NI 16 data lines plus strobe, and
# section 12 picks the PXIe-6353 for the 32 hardware-timed Port 0 lines that
# requires. One digital word cannot carry all of it:
#
#   word 0, bit 0        barcode          (section 4.5 -- the timebase's own line)
#   word 0, bit 1        code strobe      (T1 = 500 us, section 4.2.1)
#   word 1, bits 0..15   the 16 data lines, latched at the strobe's FAR edge
#
# Eighteen lines -- barcode, strobe, 16 data -- do not fit in one 16-bit word,
# which is why NI (and `_nidq_meta.py`'s own docstring) saves a 32-line port as
# TWO digital words rather than one. The latch is the far edge of T1, so the
# data is sampled where the strobe falls, not where it rises: section 4.2.1,
# "T1 is the strobe pulse width, and the latching edge is its far end."
NIDQ_CODE_STROBE_XD_LINE = 1
NIDQ_N_DIGITAL_WORDS = 2


def _meta_text(
    recipe: SessionRecipe, n_samples: int, n_channels: int, bin_name: str
) -> str:
    n_ap = recipe.n_ap_channels
    file_bytes = n_samples * n_channels * 2
    imro = f"({n_ap},{n_ap})" + "".join(
        f"({c} 0 0 {int(AP_GAIN)} 250 1)" for c in range(n_ap)
    )
    chan_map = (
        f"({n_ap},0,1)"
        + "".join(f"(AP{c};{c}:{c})" for c in range(n_ap))
        + f"(SY0;{n_ap}:{n_ap})"
    )
    geom = "(NP1000,1,0,70)" + "".join(
        f"(0:{16 if c % 2 else 48}:{20 * (c // 2)}:1)" for c in range(n_ap)
    )
    lines = [
        "typeThis=imec",
        # SpikeInterface's reader requires fileName — it reconstructs the original
        # path from it. Omitting it raises KeyError rather than degrading.
        f"fileName={bin_name}",
        f"imSampRate={recipe.ap_sample_rate_hz:g}",
        f"nSavedChans={n_channels}",
        f"fileSizeBytes={file_bytes}",
        f"fileTimeSecs={n_samples / recipe.ap_sample_rate_hz:.6f}",
        f"acqApLfSy={n_ap},0,1",
        f"snsApLfSy={n_ap},0,1",
        "imAiRangeMax=0.6",
        "imAiRangeMin=-0.6",
        "imMaxInt=512",
        "imDatPrb_type=0",
        # The reader looks up probe geometry by part number in ProbeInterface's
        # table and raises if it is absent. NP1000 is Neuropixels 1.0.
        "imDatPrb_pn=NP1000",
        # Which channels were saved. ProbeInterface requires it to map the
        # imroTbl onto physical sites; "all" is the whole-probe case.
        "snsSaveChanSubset=all",
        # Sample index of the first sample in this file. Zero is correct here
        # because generated sessions always start at the beginning of their run;
        # omitting it makes the reader warn and assume the same thing.
        "firstSample=0",
        f"~imroTbl={imro}",
        f"~snsChanMap={chan_map}",
        f"~snsGeomMap={geom}",
    ]
    return "\n".join(lines) + "\n"


def write_spikeglx(
    dir_path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0
) -> Path:
    rng = np.random.default_rng(recipe.seed + 1)
    fs = recipe.ap_sample_rate_hz
    n_samples = int((recipe.duration_s + SPIKEGLX_PRE_ROLL_S) * fs)
    n_channels = recipe.n_ap_channels + 1

    data = rng.normal(0.0, NOISE_UV / UV_PER_BIT, (n_samples, n_channels))

    template = SPIKE_TEMPLATE_UV / UV_PER_BIT
    for time_s, channel in truth.spikes:
        start = int((apply_drift(time_s, drift_ppm) + SPIKEGLX_PRE_ROLL_S) * fs)
        stop = start + template.size
        if stop < n_samples:
            data[start:stop, channel] += template

    # The SY channel is emitted and left at zero. `snsApLfSy` declares it, so a
    # reader expects the column and reshaping breaks without it — but nothing
    # drives it, because spec section 4.5 keeps the imec SMA free and puts the
    # barcode on NI instead. A fixture that drove a barcode here would describe
    # a rig nobody is going to build.
    data[:, -1] = 0

    bin_path = dir_path / f"{recipe.session_id}_imec0.ap.bin"
    data.astype(np.int16).tofile(bin_path)
    bin_path.with_suffix(".meta").write_text(
        _meta_text(recipe, n_samples, n_channels, bin_path.name), encoding="utf-8"
    )
    write_nidq(dir_path, recipe, truth, drift_ppm=drift_ppm)
    # The imec binary, not the NI one: it is the stream the TRUNCATED_FILE fault
    # truncates and the one every existing caller means by "the SpikeGLX file".
    return bin_path


def _nidq_meta_text(recipe: SessionRecipe, n_samples: int, bin_name: str) -> str:
    """The `.nidq.meta` fields SpikeInterface's reader actually consumes.

    `snsMnMaXaDw` is the NI channel-type census — multiplexed neural,
    multiplexed aux, non-muxed analog, digital words — and the reader slices
    gains by it, so `0,0,0,2` says this file is two digital word channels and
    nothing else: word 0 (barcode, bit 0, and strobe, bit 1) and word 1 (the 16
    data lines, bits 0-15). `snsMnMaXaDw`'s fourth field is the one
    `read_nidq_meta` actually parses; `niXDChans1` and `~snsChanMap` are
    descriptive, but without them the reader reports zero digital channels and
    the barcode line is invisible to anything that asks the reader rather than
    the bytes.
    """
    file_bytes = n_samples * NIDQ_N_DIGITAL_WORDS * 2
    return "\n".join(
        [
            "typeThis=nidq",
            f"fileName={bin_name}",
            f"niSampRate={NIDQ_SAMPLE_RATE_HZ:g}",
            f"nSavedChans={NIDQ_N_DIGITAL_WORDS}",
            f"fileSizeBytes={file_bytes}",
            f"fileTimeSecs={n_samples / NIDQ_SAMPLE_RATE_HZ:.6f}",
            f"acqMnMaXaDw=0,0,0,{NIDQ_N_DIGITAL_WORDS}",
            f"snsMnMaXaDw=0,0,0,{NIDQ_N_DIGITAL_WORDS}",
            # Gains for channel types this file has none of. The reader indexes
            # them unconditionally, so absent fields are a KeyError rather than
            # an unused default.
            "niMNGain=200",
            "niMAGain=1",
            "niAiRangeMax=5",
            "niAiRangeMin=-5",
            "firstSample=0",
            "niXDChans1=0:1",
            f"~snsChanMap=(0,0,0,{NIDQ_N_DIGITAL_WORDS},0)(XD0;0:1)(XD1;0:15)",
        ]
    ) + "\n"


def write_nidq(
    dir_path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0
) -> Path:
    """The NI stream: two digital words carrying the barcode, the code strobe
    and the 16 data lines (spec section 4.2's routing table; section 12's
    PXIe-6353).

    One int16 per word per sample, interleaved word 0 then word 1 — which is
    how NI hardware-timed digital input arrives, and why isolating any one
    line downstream is a mask rather than a stride. Word 0 carries the barcode
    (bit 0) and the strobe (bit 1); word 1 carries the 16 data lines at bits
    0-15, because 18 lines do not fit in one 16-bit word.

    The same `SPIKEGLX_PRE_ROLL_S` origin as the imec stream, because SpikeGLX
    starts them together — and a different origin from the sync box's, so a
    pipeline that never computes an offset fails.
    """
    # Deferred rather than a top-level import: `rhs.py` imports SPIKE_TEMPLATE_UV
    # from this module, so a top-level import in the other direction would be a
    # circular import whose success depends on which of the two a caller
    # imports first.
    from wl_preproc.synth.rhs import STROBE_WIDTH_S

    strobe_width = max(1, int(round(STROBE_WIDTH_S * NIDQ_SAMPLE_RATE_HZ)))

    # `code_word_span_s` -- shared with `synth/rhs.py` -- extends the buffer
    # past recipe.duration_s when SESSION_END lands after it, which
    # timeline.py's own spacing rule always does. Barcodes never need this;
    # code words do.
    session_span_s = code_word_span_s(recipe, truth, drift_ppm, STROBE_WIDTH_S)
    n_samples = (
        int((session_span_s + SPIKEGLX_PRE_ROLL_S) * NIDQ_SAMPLE_RATE_HZ)
        + SAMPLE_COUNT_ROUNDING_SLACK
    )
    control = np.zeros(n_samples, dtype=np.uint16)  # word 0: barcode + strobe
    data = np.zeros(n_samples, dtype=np.uint16)  # word 1: the 16 data lines

    for value, start_s in truth.barcodes:
        cursor = int(
            (apply_drift(start_s, drift_ppm) + SPIKEGLX_PRE_ROLL_S) * NIDQ_SAMPLE_RATE_HZ
        )
        for level, duration_us in encode(value):
            width = int(round(duration_us * 1e-6 * NIDQ_SAMPLE_RATE_HZ))
            # OR the line in rather than assigning the word: the strobe is the
            # same array, so an assignment here would clear it.
            if level and cursor + width <= n_samples:
                control[cursor : cursor + width] |= 1 << NIDQ_BARCODE_XD_LINE
            cursor += width

    # Strobe must be strictly narrower than the word spacing, or consecutive
    # strobes merge into one long high with no falling edge between them and
    # the words become uncountable. Phase 1b shipped exactly that defect: a
    # 1 ms pulse at 1 ms spacing rendered 31 words as 5 countable edges.
    for time_s, word in truth.code_words:
        sample = int(
            round((apply_drift(time_s, drift_ppm) + SPIKEGLX_PRE_ROLL_S) * NIDQ_SAMPLE_RATE_HZ)
        )
        if sample + strobe_width > n_samples:
            continue
        control[sample : sample + strobe_width] |= 1 << NIDQ_CODE_STROBE_XD_LINE
        data[sample : sample + strobe_width] = word & 0xFFFF

    bin_path = dir_path / f"{recipe.session_id}.nidq.bin"
    # NI writes every analog channel first, then digital word 0, then word 1;
    # this stream has no analog channels, so each sample is (word 0, word 1).
    # `.view`, not `.astype`: values like Escape.BLOCK_START (0x8002) exceed
    # int16's signed range, and what this file needs written to disk is the
    # 16-bit pattern, which `.view` reinterprets by definition and round-trips
    # exactly through the reader's own `.astype(np.uint16)` on the other end.
    # `.astype` is a VALUE cast, and it neither raises nor warns on those
    # out-of-range values -- checked under `-W error` against numpy 2.4.6, an
    # earlier version of this comment claimed it would. It happens to produce
    # the identical bytes, by numpy's own unsigned-to-signed wraparound, so
    # the difference here is intent rather than output: `.view` says "these
    # bits", which is what the format specifies, while `.astype` says "these
    # values" and would go silently wrong the day the two stop coinciding.
    interleaved = np.column_stack((control, data))
    interleaved.view(np.int16).tofile(bin_path)
    bin_path.with_suffix(".meta").write_text(
        _nidq_meta_text(recipe, n_samples, bin_path.name), encoding="utf-8"
    )
    return bin_path
