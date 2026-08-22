"""Per-system extraction of a barcode bit stream.

This module is the ONLY per-system code in Phase 1c-4. Everything downstream —
decode, rate fit, offset fit, residual, rejection, tier — is shared across all
five systems, because all five carry the same barcode (design spec section 2).

A sixth system costs one function here and one synthetic emitter. It touches no
table and no fit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from wl_sync.barcode import BIT_SLOT_US, edges_from_samples, encode


def min_sample_rate_hz() -> float:
    """The lowest sampling rate that can decode a barcode, derived rather than
    written down.

    Decoding needs at least two samples per bit slot. `BIT_SLOT_US` is
    wl-sync's, so deriving from it means a change there fails a test here
    instead of silently invalidating every camera system's assumed rate. Design
    spec section 3: this project has twice shipped timing arithmetic that no
    code had executed.
    """
    return 2.0 / (BIT_SLOT_US / 1_000_000.0)


@dataclass(frozen=True)
class BitStream:
    """A digital line's edges in the device's OWN time, plus the rate that
    sampled them.

    Native time, not session time: converting to session time is the fit's job
    (`timebase/fit.py`), and keeping the two apart is what makes the transform
    reversible as design spec section 4.5 requires.
    """

    edges: tuple[tuple[int, int], ...]
    fs_hz: float
    n_samples: int

    def __post_init__(self) -> None:
        floor = min_sample_rate_hz()
        if self.fs_hz < floor:
            raise ValueError(
                f"{self.fs_hz} Hz is below the {floor} Hz floor for decoding a "
                f"{BIT_SLOT_US} us bit slot: at least two samples per bit are "
                "needed, so this stream cannot yield a barcode at all"
            )


# The sync box logs at microsecond resolution, so its "sampling rate" is
# nominal: it is not a sampled line at all. 1 MHz is stated rather than
# measured, and is only ever used to satisfy BitStream's floor check.
_SYNCBOX_NOMINAL_FS_HZ = 1_000_000.0


def extract_syncbox(path: Path) -> BitStream:
    """The sync box's own log, rendered into the same edge form every other
    system produces.

    Rendering through `wl_sync.barcode.encode` rather than writing edges by
    hand: the codec owns the frame's shape, and a second copy of it here is the
    reimplementation this phase's constraints forbid.

    A hand-built edge list has to supply two things `edges_from_samples` gives
    every *sampled* system for free (its `previous` state starts `None`, so it
    always emits an edge at its own t0): a LOW edge before the first frame, so
    that frame's preceding idle is verifiable by a caller who decodes this
    stream without passing `start_us` itself; and a LOW edge closing each
    frame's TRAIL pulse, so `decode_edges`'s own completeness check — which
    requires the edge list to extend to the frame's end — has something to see
    past the last frame. Native tick 0 is the log's own time origin and is
    known LOW there, before anything has driven the line.
    """
    from wl_preproc.timebase._syncbox_log import read_barcode_entries

    entries = read_barcode_entries(path)
    edges: list[tuple[int, int]] = []
    for value, t_us in entries:
        tick = t_us
        for level, duration_us in encode(value):
            edges.append((tick, level))
            tick += duration_us
        edges.append((tick, 0))
    if edges:
        edges.append((0, 0))
    edges.sort()
    last_us = edges[-1][0] if edges else 0
    return BitStream(
        edges=tuple(edges),
        fs_hz=_SYNCBOX_NOMINAL_FS_HZ,
        n_samples=last_us,
    )


def _edges_from_bit(
    words: np.ndarray, bit: int, fs_hz: float
) -> tuple[tuple[int, int], ...]:
    """One digital word stream, one bit, into edges.

    `edges_from_samples` is wl-sync's and does the level-change detection; this
    only isolates the line. Note `bit` is ZERO-BASED here. Intan's own
    documentation numbers bits from 1, and reading it literally keys a mask to
    the wrong signal silently -- parent spec section 6.3 records that trap.

    The trace is emitted for every sample including the first, so
    `edges_from_samples` -- whose `previous` state starts `None` -- always
    yields an edge at native tick 0. That is what makes the idle preceding the
    first barcode verifiable by a caller that decodes this stream without
    passing `start_us` itself.
    """
    trace = ((words >> bit) & 1).astype(np.uint8)
    return tuple(edges_from_samples(trace.tolist(), fs_hz=fs_hz))


# Which digital-in line carries the barcode is this project's wiring
# convention, not something the Intan header records: its digital channels are
# named DIGITAL-IN-00.. and carry no semantics. So it is stated here, the way
# `_syncbox_log.py` states the sync box's gpio_map key, rather than discovered.
# Zero-based, and it is bit 0 that `wl_preproc/synth/rhs.py` drives -- but the
# authority for January is the rig's wiring, so this constant is the one place
# to change when that is measured rather than assumed.
RHS_BARCODE_DIGITAL_BIT = 0

_RHS_DIGITAL_IN_FILENAME = "digitalin.dat"


def extract_rhs(session_dir: Path) -> BitStream:
    """The barcode line out of an Intan RHS session's digital-in file.

    `session_dir` is the session's `rhs/` system directory. The rate comes from
    that recording's own `info.rhs`, never from a constant here: the RHS
    controller's clock is independent of every other system's by construction,
    and a rate assumed rather than read is a fit wrong by exactly the ratio
    nobody checked.

    `digitalin.dat` is one uint16 word per sample -- so the whole 16-line port
    is one array and isolating the barcode is a mask, not a stride.
    """
    from wl_preproc.timebase._rhs_header import (
        INFO_FILENAME,
        find_recording_dir,
        read_sample_rate_hz,
    )

    recording_dir = find_recording_dir(session_dir)
    fs_hz = read_sample_rate_hz(recording_dir / INFO_FILENAME)
    digital_path = recording_dir / _RHS_DIGITAL_IN_FILENAME
    if not digital_path.is_file():
        raise FileNotFoundError(
            f"{digital_path}: the recording has an {INFO_FILENAME} but no "
            f"{_RHS_DIGITAL_IN_FILENAME}, so it carries no barcode line"
        )
    words = np.fromfile(digital_path, dtype=np.uint16)
    return BitStream(
        edges=_edges_from_bit(words, RHS_BARCODE_DIGITAL_BIT, fs_hz),
        fs_hz=fs_hz,
        n_samples=int(words.size),
    )


# The consuming half of `wl_preproc/synth/spikeglx.py`'s wiring convention:
# which Port 0 line the barcode is on. Zero-based. SpikeGLX records the digital
# lines' existence (`niXDChans1`) but nothing about their meaning, so this is
# stated here rather than discovered -- the same shape as the sync box's
# gpio_map key and the RHS digital bit.
NIDQ_BARCODE_XD_LINE = 0

_NIDQ_META_SUFFIX = ".meta"


def extract_spikeglx(nidq_bin: Path) -> BitStream:
    """The barcode line out of a SpikeGLX `.nidq.bin`.

    **The NI stream, not imec.** Spec section 4.5: SpikeGLX handles imec-NI sync
    internally, and the barcode aligns SpikeGLX-as-a-whole through one NI
    digital line, leaving the imec SMA free. Pointing this at an `.ap.bin`
    would find a silent SY channel.

    The rate comes from the companion `.meta`, never from a constant here: a
    rate assumed rather than read is a fit that is wrong by exactly the ratio
    nobody checked.

    NI saves interleaved int16 -- any analog channels first, then the digital
    words -- so the barcode's word is at a stride the sidecar's census
    determines. A guessed channel count does not fail here; it reads a strided
    mixture of analog channels as though it were a digital line.
    """
    from wl_preproc.timebase._nidq_meta import read_nidq_meta

    meta_path = nidq_bin.with_suffix(_NIDQ_META_SUFFIX)
    if not meta_path.is_file():
        raise FileNotFoundError(
            f"{meta_path}: a SpikeGLX binary carries its shape and its sampling "
            "rate in this sidecar, and neither can be recovered from the "
            f"binary alone, so {nidq_bin.name} cannot be read without it"
        )
    meta = read_nidq_meta(meta_path)

    interleaved = np.fromfile(nidq_bin, dtype=np.int16)
    if interleaved.size % meta.n_channels:
        raise ValueError(
            f"{nidq_bin}: {interleaved.size} int16 samples do not divide into "
            f"the {meta.n_channels} channels {meta_path.name} declares; the "
            "file is truncated mid-sample or the sidecar describes a different "
            "recording"
        )
    samples = interleaved.reshape(-1, meta.n_channels)
    # The first digital word. `n_analog_channels` is its offset because NI
    # writes every analog channel ahead of every digital one, and a second
    # word -- how a 32-line port is saved -- would follow this one.
    words = samples[:, meta.n_analog_channels].astype(np.uint16)

    return BitStream(
        edges=_edges_from_bit(words, NIDQ_BARCODE_XD_LINE, meta.sample_rate_hz),
        fs_hz=meta.sample_rate_hz,
        n_samples=int(samples.shape[0]),
    )
