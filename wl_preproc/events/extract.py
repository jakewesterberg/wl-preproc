# wl_preproc/events/extract.py
"""Per-system extraction of the event-code stream.

This module is the ONLY per-system code in Phase 1c-5, exactly as
`timebase/extract.py` is the only per-system code in 1c-4. Everything
downstream -- decode, trial assembly, agreement, tier -- is shared, because
every system carries the same protocol (design spec section 4.2).

**The three systems do not carry the same thing, and the return types say so.**
The sync box and the NI carry 16 data lines plus a strobe and yield WORDS. The
Intan RHS carries the strobe ONLY -- its 16 digital inputs cannot fit 16 data
lines plus strobe plus barcode -- and yields a WITNESS: a count and its timing,
never content. Returning empty words for the RHS would make a correct
strobe-only recording indistinguishable from a decode failure.

Native time throughout, never session time. Converting is the fit's job
(`timebase/fit.py`), and keeping them apart is what makes the transform
reversible as spec section 4.5 requires.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from wl_sync.log import CodeWord, read_log


@dataclass(frozen=True, slots=True)
class WordStream:
    """16-bit words with their native timestamps, from a full-code recorder."""

    words: tuple[tuple[int, int], ...]
    fs_hz: float


@dataclass(frozen=True, slots=True)
class StrobeWitness:
    """Strobe edges with no word content, from a strobe-only recorder.

    A distinct type from `WordStream` on purpose: spec section 4.7's tier B is
    "1 full-code record + >=1 independent strobe witness", so a witness is a
    real contribution rather than a degraded word stream, and the type system
    should not let one be mistaken for the other.
    """

    edge_samples: tuple[int, ...]
    fs_hz: float

    @property
    def n_edges(self) -> int:
        return len(self.edge_samples)


# `timebase/extract.py` names the barcode's line as RHS_BARCODE_DIGITAL_BIT = 0.
# The strobe is the next one, and it has no constant on the READING side yet --
# only `synth/rhs.py` names it, on the emitting side.
RHS_STROBE_DIGITAL_BIT = 1


# The sync box logs at microsecond resolution and is not a sampled line at all;
# 1 MHz is nominal, matching `timebase/extract.py`'s own treatment of it.
_SYNCBOX_NOMINAL_FS_HZ = 1_000_000.0


def extract_syncbox_words(path: Path) -> WordStream:
    """Words from the sync box log.

    The Pi decodes the 16 data lines itself and writes a `CodeWord` record, so
    there is nothing to decode here -- this is a read. `Edge` records in the
    same log are the barcode and are not words; letting one through would be a
    code nobody sent.
    """
    _header, records = read_log(path)
    words = tuple(
        (record.tick_us, record.word) for record in records if isinstance(record, CodeWord)
    )
    return WordStream(words=words, fs_hz=_SYNCBOX_NOMINAL_FS_HZ)


# Mirrors the emitter's allocation in `wl_preproc/synth/spikeglx.py`, which is
# spec section 4.2's routing table made concrete. TWO digital words: 18 lines do
# not fit in 16 bits, and NI saves a 32-line port as two words -- which is what
# section 12's PXIe-6353 was chosen for.
_NIDQ_STROBE_BIT = 1  # in digital word 0, beside the barcode at bit 0


def extract_nidq_words(nidq_bin: Path) -> WordStream:
    """Words from the NI digital line.

    **Latched at the strobe's FAR edge, not its rising one.** Spec section
    4.2.1: data and strobe assert together at the start of T1, and the latching
    edge is T1's far end -- so T1 IS the setup time rather than adding to it.
    Sampling the rising edge would read data that has been valid for zero
    microseconds; sampling the falling edge reads data valid for a full T1.
    """
    from wl_preproc.timebase._nidq_meta import read_nidq_meta

    meta_path = nidq_bin.with_suffix(".meta")
    meta = read_nidq_meta(meta_path)
    if meta.n_digital_words < 2:
        raise ValueError(
            f"{nidq_bin}: the sidecar declares {meta.n_digital_words} digital "
            "word(s), so this recording carries the barcode but no code lines. "
            "Spec section 4.2 routes 16 data lines plus strobe to the NI, which "
            "needs two words"
        )

    raw = np.fromfile(nidq_bin, dtype=np.int16)
    # The same guard, and the same message, `timebase/extract.py`'s
    # `extract_spikeglx` puts in front of the identical reshape. Both call
    # sites today reach this only after that sibling has already read the same
    # file with the same divisor, so this is not covering a live gap -- it is
    # for a direct caller, and so that one module family does not describe the
    # same corruption two different ways (a bare numpy reshape error here, a
    # sentence naming the file and the sidecar there).
    if raw.size % meta.n_channels:
        raise ValueError(
            f"{nidq_bin}: {raw.size} int16 samples do not divide into "
            f"the {meta.n_channels} channels {meta_path.name} declares; the "
            "file is truncated mid-sample or the sidecar describes a different "
            "recording"
        )
    samples = raw.reshape(-1, meta.n_channels)
    # NI writes every analog channel before every digital word, so word 0 sits
    # at n_analog_channels and word 1 immediately after it. `extract_spikeglx`
    # takes word 0 for the barcode and already anticipates this second word.
    control = samples[:, meta.n_analog_channels].astype(np.uint16)
    data = samples[:, meta.n_analog_channels + 1].astype(np.uint16)

    strobe = (control >> _NIDQ_STROBE_BIT) & 1
    # Falling edges: high at i, low at i+1. The word is read at i, the last
    # sample the strobe was still asserted.
    falling = np.flatnonzero((strobe[:-1] == 1) & (strobe[1:] == 0))

    words = tuple((int(index), int(data[index])) for index in falling)
    return WordStream(words=words, fs_hz=meta.sample_rate_hz)


def extract_rhs_witness(session_dir: Path) -> StrobeWitness:
    """Strobe edges from the Intan RHS. A witness, never words.

    Spec section 4.2: RHS receives the strobe only. Its 16 digital inputs
    cannot fit 16 data lines plus the strobe plus the barcode, and the design
    permits that because the Pi is always present as a full-code recorder --
    a rule that "does not generalize back to the Pi", which is the sole
    recorder on training days.
    """
    import numpy as np

    from wl_preproc.timebase._rhs_header import (
        INFO_FILENAME,
        find_recording_dir,
        read_sample_rate_hz,
    )
    from wl_preproc.timebase.extract import RHS_DIGITAL_IN_FILENAME

    recording_dir = find_recording_dir(session_dir)
    fs_hz = read_sample_rate_hz(recording_dir / INFO_FILENAME)
    words = np.fromfile(recording_dir / RHS_DIGITAL_IN_FILENAME, dtype=np.uint16)

    strobe = (words >> RHS_STROBE_DIGITAL_BIT) & 1
    rising = np.flatnonzero((strobe[1:] == 1) & (strobe[:-1] == 0)) + 1
    return StrobeWitness(edge_samples=tuple(int(i) for i in rising), fs_hz=fs_hz)
