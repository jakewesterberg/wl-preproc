"""Emit a SpikeGLX imec AP stream: interleaved int16 .bin plus a .meta sidecar.

Channel layout is n_ap_channels of neural data followed by one SY channel
carrying the barcode, which is how the pipeline aligns this stream to session
time (spec section 4.5).

Field names in the .meta are what SpikeInterface's reader requires. Where this
disagrees with the reader, the reader wins — it is the thing the pipeline will
actually use.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from wl_sync.barcode import encode

from wl_preproc.synth.recipe import SessionRecipe
from wl_preproc.synth.timeline import apply_drift
from wl_preproc.synth.truth import GroundTruth

SPIKE_TEMPLATE_UV = np.array(
    [0, -10, -40, -120, -200, -140, -40, 30, 60, 45, 25, 10, 0], dtype=np.float64
)
AP_GAIN = 500.0
NOISE_UV = 8.0
UV_PER_BIT = 2.34375  # Neuropixels 1.0 at gain 500

# A different tick origin from the sync box, deliberately — see syncbox.py.
SPIKEGLX_PRE_ROLL_S = 0.7


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

    sync = np.zeros(n_samples, dtype=np.int16)
    for value, start_s in truth.barcodes:
        cursor = int((apply_drift(start_s, drift_ppm) + SPIKEGLX_PRE_ROLL_S) * fs)
        for level, duration_us in encode(value):
            width = int(round(duration_us * 1e-6 * fs))
            if cursor + width <= n_samples:
                sync[cursor : cursor + width] = level
            cursor += width
    data[:, -1] = sync

    bin_path = dir_path / f"{recipe.session_id}_imec0.ap.bin"
    data.astype(np.int16).tofile(bin_path)
    bin_path.with_suffix(".meta").write_text(
        _meta_text(recipe, n_samples, n_channels, bin_path.name), encoding="utf-8"
    )
    return bin_path
