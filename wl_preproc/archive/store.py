"""The session artifact: one Zarr store holding compressed streams and every
other file verbatim.

**Why Zarr and not a tar.** Design spec section 10 item 5: a tar would defeat
partial reads during rehydration, and `nas_artifact_observation` carries
`fileCount` precisely because an artifact may be a tree.

**Why blosc-zstd and not WavPack.** Section 2.1: `wavpack-numcodecs` wraps a
system library and will not install from pip. The paper puts general-purpose
codecs 6% behind the audio ones on NP1 -- about 6 GB a session -- which does not
buy a deployment dependency. The codec actually used is recorded on the artifact
so a later switch is new artifacts rather than a migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numcodecs
import numpy as np
import zarr

from wl_preproc.archive.layout import SAMPLE_DTYPE, bulk_streams
from wl_preproc.contracts.done import blake3_file

ARRAY_GROUP = "streams"
VERBATIM_GROUP = "verbatim"

# One chunk is (samples, all channels): reconstruction reads whole rows and a
# chunk spanning channels keeps that a single read. 2**20 samples is ~130 MB at
# 64 channels, comfortably inside memory and large enough that zstd sees real
# redundancy.
_CHUNK_SAMPLES = 1 << 20

# Verbatim files are stored as 1-D byte arrays in chunks of this many bytes,
# written one chunk at a time. Explicit rather than zarr's automatic choice,
# because that choice was only ever made for an array already in memory --
# and Intan's `stim.dat` is one uint16 per channel per sample, as large as
# `amplifier.dat` (2026-09-26 rehydration design, section 12).
_VERBATIM_CHUNK_BYTES = 1 << 24

# No stored chunk larger than this. Blosc refuses a buffer over 2 GiB, and a
# stream chunk is rows x channels x 2 bytes: at 2**20 rows that limit is met
# at 1024 channels. Neuropixels' 385 channels stay at the full 2**20 rows.
_MAX_CHUNK_BYTES = 1 << 30


@dataclass(frozen=True, slots=True)
class StoreResult:
    path: Path
    codec: str
    clevel: int
    compressed_bytes: int
    manifest_digest: str


def manifest_digest(store_dir: Path, exclude: frozenset[str] = frozenset()) -> str:
    """blake3 over the sorted `(relative path, blake3)` pairs of every file.

    A directory tree has no single hash, and a digest over concatenated bytes
    would depend on walk order -- two identical copies would disagree. Sorting
    the pairs makes it a property of the contents alone.

    **This identifies a stored artifact, not a session** -- design spec
    section 10 item 4's own words for it: "the digest is of an artifact
    rather than of a session." Confirmed empirically 2026-08-27 building this
    module: `numcodecs.Blosc`'s default multi-threaded compression emits a
    DIFFERENT compressed byte string for identical input across separate
    calls -- same length, and every version decodes back to the identical
    bytes, but the compressed representation itself is not reproducible run
    to run. Two independent `write_store()` calls over the same session
    therefore produce two different manifest digests, and both are valid.

    That is fine for what this digest is actually used for: section 3's
    confirm step calls it against *the same bytes* twice -- `write_store()`
    once, then a plain copy to the NAS, then compare -- never against a
    second independent compression. Do not pin `numcodecs.blosc`'s thread
    count to make two archives of one session agree; that pays real
    throughput on a ~360 GB session for a property nothing in this design
    consumes (tried in an earlier version of this function; reverted per
    design spec section 10 item 4).

    **`exclude` names relative paths to leave out**, and exists for one: the
    completion sentinel. `archive/stage.py::archive_session` confirms this
    digest and only THEN writes the sentinel, so a published artifact always
    holds one file the recorded digest never covered
    (`tests/cli/test_archive_cli.py::_digest_of_published_content` found this
    first). Re-checking a published artifact against its recorded digest --
    `archive/proof.py` -- therefore passes `exclude={SENTINEL_NAME}`. Empty
    by default, which is exactly the old behaviour.
    """
    import blake3 as _blake3

    digest = _blake3.blake3()
    for path in sorted(p for p in store_dir.rglob("*") if p.is_file()):
        relative = str(path.relative_to(store_dir))
        if relative in exclude:
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(blake3_file(path).encode("ascii"))
    return digest.hexdigest()


def _stream_chunk_rows(n_channels: int) -> int:
    """Rows per stored chunk: `_CHUNK_SAMPLES`, fewer only when a chunk that
    tall would exceed `_MAX_CHUNK_BYTES`."""
    return min(_CHUNK_SAMPLES, max(1, _MAX_CHUNK_BYTES // (n_channels * SAMPLE_DTYPE.itemsize)))


def _read_exactly(handle, view: memoryview, path: Path) -> None:
    """Fill `view` from `handle`, or raise: a file that ends early shrank
    after it was sized, and storing the rest as the array's fill value would
    record bytes it never had."""
    got = 0
    while got < view.nbytes:
        n = handle.readinto(view[got:])
        if not n:
            raise OSError(f"{path} shrank while it was being archived")
        got += n


def _refuse_growth(handle, path: Path) -> None:
    if handle.read(1):
        raise OSError(f"{path} grew while it was being archived")


def _write_stream(arrays, stream, compressor) -> None:
    """One bulk stream, read into one reused chunk-sized buffer and written a
    stored chunk of rows at a time -- never the whole file, which for a
    two-hour Neuropixels 1.0 AP stream is ~166 GB.

    A plain read, not a memory map, and on purpose (found in review): a
    memory-mapped read that faults -- an I/O error, or the file shrinking --
    arrives as SIGBUS and kills the whole daemon past its per-session
    `except Exception`, where a read raises `OSError`; and every mapped page
    stays resident, so the process grew to the size of the file. The chunk
    shape is the one this writer always used, except that a very wide stream
    gets fewer rows (`_stream_chunk_rows`)."""
    rows = max(1, min(_stream_chunk_rows(stream.n_channels), stream.n_samples))
    array = arrays.create_dataset(
        stream.path.name,
        shape=(stream.n_samples, stream.n_channels),
        chunks=(rows, stream.n_channels),
        dtype=SAMPLE_DTYPE,
        compressor=compressor,
    )
    # Reused for every chunk: zarr compresses and stores the block before
    # the assignment returns.
    buffer = np.empty((rows, stream.n_channels), dtype=SAMPLE_DTYPE)
    with stream.path.open("rb", buffering=0) as handle:
        for start in range(0, stream.n_samples, rows):
            block = buffer[: min(rows, stream.n_samples - start)]
            _read_exactly(handle, memoryview(block).cast("B"), stream.path)
            array[start : start + len(block)] = block
        _refuse_growth(handle, stream.path)


def _write_verbatim(verbatim, path: Path, relative: str, compressor) -> None:
    """One verbatim file as a 1-D byte array, read into one reused
    `_VERBATIM_CHUNK_BYTES` buffer and written a block at a time. The same
    exact-length rule as `_write_stream`: a file that shrinks or grows while
    it is being written is an error, never a padded or truncated copy."""
    size = path.stat().st_size
    step = max(1, min(_VERBATIM_CHUNK_BYTES, size))
    array = verbatim.create_dataset(
        relative, shape=(size,), chunks=(step,), dtype=np.uint8, compressor=compressor
    )
    buffer = np.empty(step, dtype=np.uint8)
    with path.open("rb", buffering=0) as handle:
        for start in range(0, size, step):
            block = buffer[: min(step, size - start)]
            _read_exactly(handle, memoryview(block), path)
            array[start : start + len(block)] = block
        _refuse_growth(handle, path)


def write_store(
    session_dir: Path, out_dir: Path, codec_name: str = "zstd", clevel: int = 5
) -> StoreResult:
    """Compress `session_dir` into a Zarr store under `out_dir`, one stored
    chunk at a time through one reused buffer per file: peak memory, heap and
    resident, is a few chunk sizes -- about 2-3 GB for a 385-channel
    Neuropixels stream at 2**20-row chunks -- never a whole file
    (`_write_stream`, `_write_verbatim`)."""
    store_path = out_dir / f"{session_dir.name}.zarr"
    out_dir.mkdir(parents=True, exist_ok=True)
    root = zarr.open(str(store_path), mode="w")
    compressor = numcodecs.Blosc(
        cname=codec_name, clevel=clevel, shuffle=numcodecs.Blosc.NOSHUFFLE
    )

    streams = bulk_streams(session_dir)
    stream_paths = {s.path for s in streams}
    arrays = root.create_group(ARRAY_GROUP)
    for stream in streams:
        _write_stream(arrays, stream, compressor)
        # The relative path is stored so verification can find the original
        # again without re-deriving where a stream sat in the tree.
        arrays[stream.path.name].attrs["source"] = str(
            stream.path.relative_to(session_dir)
        )

    verbatim = root.create_group(VERBATIM_GROUP)
    for path in sorted(p for p in session_dir.rglob("*") if p.is_file()):
        if path in stream_paths:
            continue
        _write_verbatim(verbatim, path, str(path.relative_to(session_dir)), compressor)

    compressed = sum(p.stat().st_size for p in store_path.rglob("*") if p.is_file())
    return StoreResult(
        path=store_path,
        codec=codec_name,
        clevel=clevel,
        compressed_bytes=compressed,
        manifest_digest=manifest_digest(store_path),
    )
