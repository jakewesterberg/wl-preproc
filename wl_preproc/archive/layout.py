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

**Byte reconstruction cannot catch a shape this module gets wrong.** On a
C-contiguous array, `.reshape()` moves no bytes -- `.tobytes()` is identical for
any valid factorization of the same element count -- so an artifact labelled
with a plausible-but-wrong (n_channels, n_samples) round-trips Task 3's own
verification clean. Design spec section 4's "byte reconstruction catches a
channel-order error, an interleaving mistake, or a dtype slip" is true of a
genuine transformation of the data and false of a mislabelling of its shape.
This module's own checks are therefore the only guard that exists.

**Known gap: Intan's channel count is derived, not declared.** SpikeGLX's shape
is checked against `nSavedChans`, a value the recording software itself wrote;
an inconsistent `.bin` is caught because it disagrees with something external
to it. `amplifier.dat` carries no such header -- its channel count is derived
by dividing its size by a sample count read out of `time.dat`, and `time.dat`
is checked only for being a whole multiple of 4 bytes (one int32 index). A
`time.dat` truncated exactly ON a 4-byte boundary -- the shape an interrupted
copy takes -- still divides evenly and reports a self-consistent but wrong
shape: confirmed against a real STIM_RECIPE session (true shape n_channels=4,
n_samples=373546) truncated to exactly half, which reports n_channels=8,
n_samples=186773 with no exception raised. The real fix is reading Intan's own
channel count out of `info.rhs`'s signal-group headers and cross-checking it
against the derived value; this module does not, because `info.rhs` has no
reader in this repository and gaining one means parsing those headers -- more
than a byte-layout oracle should carry. Named here rather than silently relied
on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

# int16 little-endian: what both SpikeGLX and Intan write, and what every
# reconstruction in verify.py assumes. Stated as an explicit byte order rather
# than `np.int16` so the artifact does not depend on the host's endianness.
SAMPLE_DTYPE = np.dtype("<i2")

# int32 little-endian: what Intan's time.dat stores, one sample index per
# entry (synth/rhs.py's write_rhs: `np.arange(n_samples,
# dtype=np.int32).tofile(...)`). Declared for the same reason as SAMPLE_DTYPE
# above -- an explicit byte order the artifact does not depend on the host's
# endianness for.
TIME_INDEX_DTYPE = np.dtype("<i4")


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


def _channel_count(meta: Path, key: str) -> int:
    """`int(_meta_value(...))`, refusing rather than crashing on a bad value.

    This module's whole public promise is a determined shape or a raised
    `LayoutUndetermined`; a non-numeric `nSavedChans` -- a hand-edited or
    corrupted `.meta` -- must not surface as a bare `ValueError` from `int()`
    that nothing downstream was written to expect.
    """
    value = _meta_value(meta, key)
    try:
        return int(value)
    except ValueError as exc:
        raise LayoutUndetermined(
            f"{meta} has {key}={value!r}, which is not an integer"
        ) from exc


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
        found.append(_checked(binary, _channel_count(meta, "nSavedChans")))

    # Intan: info.rhs has no reader in this repository and needs none. time.dat
    # is int32 sample indices, one per sample, so the channel count falls out of
    # two file sizes -- derived from the data rather than parsed from a header
    # this repo would otherwise have to learn to read. See the module
    # docstring's "known gap" paragraph for what this derivation still cannot
    # catch.
    for amplifier in sorted(session_dir.rglob("amplifier.dat")):
        time_dat = amplifier.with_name("time.dat")
        if not time_dat.exists():
            raise LayoutUndetermined(f"{amplifier} has no time.dat beside it")
        time_size = time_dat.stat().st_size
        # Defended the same way _checked defends the .bin branch: a size that
        # is not a whole number of int32 indices is refused before it is ever
        # divided into a sample count. Without this, a time.dat torn off a
        # 4-byte boundary produced a self-consistent but wrong shape that
        # nothing caught -- see the module docstring.
        if time_size % TIME_INDEX_DTYPE.itemsize:
            raise LayoutUndetermined(
                f"{time_dat} is {time_size} bytes, not a whole number of "
                f"{TIME_INDEX_DTYPE.itemsize}-byte sample indices"
            )
        n_samples = time_size // TIME_INDEX_DTYPE.itemsize
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
