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
Nothing else in this pipeline inspects shape at all -- ingest verification
(next paragraph) checks a file's size and hash, never what gets derived from
it, and Task 3's reconstruction-verify cannot tell factorizations apart, as
just shown -- so this module's own checks are the only place a shape
mislabelling is ever caught, by anything, in this whole pipeline.

**That "only guard" claim is about shape specifically, and it does not make
the `%4` check this module's only defense against a bad `time.dat` -- a
separate guard, upstream and ordinarily stronger, keeps a corrupted one from
arriving here at all.** A file reaching this module through the running
pipeline has ordinarily already had its exact size, and then its blake3
digest, verified against the DONE marker at landing: `ingest/verify.py`'s
`verify_session` compares the file's own size against what the DONE marker
declared before it ever hashes anything -- "a size mismatch is decisive and
cheap" -- and a mismatch on any declared file quarantines the session in
`ingest/watcher.py` before `landing.land_session` runs, which is before this
plan's archival trigger can run at all.

"Ordinarily" is carrying real weight in that paragraph. `wlpp ingest
--no-verify` (`cli/main.py`) skips the comparison outright: `verify_session`
then returns `Integrity.SKIPPED` with an empty mismatch list, which
`watcher.py`'s own `if mismatches:` cannot tell apart from a session that was
actually checked and passed, so the session lands either way.
`Integrity.SKIPPED` is recorded on that landed row (`schema/ingest.py`'s
`Ingestion.integrity`), but nothing in this repository currently branches on
it -- not here, and not yet in the archival trigger, which is where that
decision belongs rather than in this module. So: a `time.dat` whose size
disagrees with what the acquisition system declared for it -- 4-byte-aligned
or not -- cannot reach `bulk_streams` through a session that was actually
verified. It can through one that opted out of verification.

**The asymmetry with SpikeGLX is real, though, and worth naming rather than
hiding behind the ingest backstop.** SpikeGLX's shape is checked against
`nSavedChans`, a value the recording software declared for itself, so a wrong
`.bin` is caught because it disagrees with something external to it.
`amplifier.dat` carries no equivalent header -- its channel count is *derived*
from a sample count read out of `time.dat`, never declared a second time -- so
that derivation is backstopped by ingest rather than by a second declaration
inside this module. Confirmed against a real STIM_RECIPE session (true shape
n_channels=4, n_samples=373546): a `time.dat` torn exactly on a 4-byte
boundary -- half its real size -- still divides evenly, and `bulk_streams`
alone reports the self-consistent but wrong n_channels=8, n_samples=186773
with no exception. Reaching that outcome requires either calling `bulk_streams`
directly on a session that bypassed ingest verification (how it was found),
or running the real pipeline with `--no-verify` over a `time.dat` corrupted on
exactly that boundary -- narrow, but real, now that both paths are named. The
`%4` check above stays regardless: it is cheap, it
turns an off-boundary truncation into a loud failure at the point of use
rather than a mysterious one downstream, and "raise `LayoutUndetermined` when
it cannot be sure" is this module's own contract independent of what any
caller already checked.
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
    # docstring for what backstops this derivation (ingest verification,
    # ordinarily) and the one case -- `--no-verify` plus a time.dat torn
    # exactly on a 4-byte boundary -- where nothing but the check below does.
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
