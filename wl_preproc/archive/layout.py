"""What shape each bulk stream is, in bytes.

**This module is the reconstruction contract.** `store.py` writes arrays using
these shapes and `verify.py` re-derives the original bytes from them, so a wrong
answer here produces an artifact that decompresses cleanly into the wrong data --
the exact silent-corruption case design spec section 4 exists to prevent. It is
its own file so that the one thing which must be exactly right can be read in a
sitting.

Only BULK streams are arrays. Everything else in a session -- .meta, info.rhs,
time.dat, stim.dat, the ohDPI rows, camera sidecars, the sync box log, the task
file, manifests and DONE markers -- is stored verbatim by `store.py`. They are a
rounding error against ~100 GB, and a byte kept untransformed is a byte that
cannot be reconstructed wrongly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

# int16 little-endian: what both SpikeGLX and Intan write, and what every
# reconstruction in verify.py assumes. Stated as an explicit byte order rather
# than `np.int16` so the artifact does not depend on the host's endianness.
SAMPLE_DTYPE = np.dtype("<i2")


class LayoutUndetermined(ValueError):
    """A stream's byte layout could not be established with certainty.

    Raised rather than guessed. A channel count that is merely plausible
    produces an artifact that round-trips through its own wrong assumption and
    verifies clean -- see design spec section 4.
    """


@dataclass(frozen=True, slots=True)
class StreamLayout:
    path: Path
    dtype: np.dtype
    n_channels: int
    n_samples: int


def _meta_value(meta: Path, key: str) -> str:
    for line in meta.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name == key:
            return value
    raise LayoutUndetermined(f"{meta} has no {key}")


def _checked(path: Path, n_channels: int) -> StreamLayout:
    size = path.stat().st_size
    stride = SAMPLE_DTYPE.itemsize * n_channels
    if n_channels <= 0 or stride == 0 or size % stride:
        raise LayoutUndetermined(
            f"{path} is {size} bytes, which is not a whole number of "
            f"{n_channels}-channel int16 samples ({stride} bytes each)"
        )
    return StreamLayout(path, SAMPLE_DTYPE, n_channels, size // stride)


def bulk_streams(session_dir: Path) -> list[StreamLayout]:
    """Every bulk stream under `session_dir`, with its exact layout."""
    found: list[StreamLayout] = []

    # SpikeGLX: nSavedChans is authoritative and includes the SY channel, so it
    # is read rather than derived from the recipe's n_ap_channels.
    for binary in sorted(session_dir.rglob("*.bin")):
        meta = binary.with_suffix(".meta")
        if not meta.exists():
            raise LayoutUndetermined(f"{binary} has no .meta beside it")
        found.append(_checked(binary, int(_meta_value(meta, "nSavedChans"))))

    # Intan: info.rhs has no reader in this repository and needs none. time.dat
    # is int32 sample indices, one per sample, so the channel count falls out of
    # two file sizes -- derived from the data rather than parsed from a header
    # this repo would otherwise have to learn to read.
    for amplifier in sorted(session_dir.rglob("amplifier.dat")):
        time_dat = amplifier.with_name("time.dat")
        if not time_dat.exists():
            raise LayoutUndetermined(f"{amplifier} has no time.dat beside it")
        n_samples = time_dat.stat().st_size // np.dtype("<i4").itemsize
        if n_samples == 0:
            raise LayoutUndetermined(f"{time_dat} is empty")
        size = amplifier.stat().st_size
        stride = SAMPLE_DTYPE.itemsize * n_samples
        if size % stride:
            raise LayoutUndetermined(
                f"{amplifier} is {size} bytes, not a whole number of channels "
                f"over {n_samples} samples"
            )
        found.append(_checked(amplifier, size // stride))

    return found
