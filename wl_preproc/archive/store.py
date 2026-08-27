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


@dataclass(frozen=True, slots=True)
class StoreResult:
    path: Path
    codec: str
    clevel: int
    compressed_bytes: int
    manifest_digest: str


def manifest_digest(store_dir: Path) -> str:
    """blake3 over the sorted `(relative path, blake3)` pairs of every file.

    A directory tree has no single hash, and a digest over concatenated bytes
    would depend on walk order -- two identical copies would disagree. Sorting
    the pairs makes it a property of the contents alone.
    """
    import blake3 as _blake3

    digest = _blake3.blake3()
    for path in sorted(p for p in store_dir.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(store_dir)).encode("utf-8"))
        digest.update(blake3_file(path).encode("ascii"))
    return digest.hexdigest()


def write_store(
    session_dir: Path, out_dir: Path, codec_name: str = "zstd", clevel: int = 5
) -> StoreResult:
    """Compress `session_dir` into a Zarr store under `out_dir`.

    **Compression is pinned to one thread for the duration of this call.**
    Confirmed empirically 2026-08-27, isolated down to a single `.encode()`
    call: `numcodecs.Blosc`'s default multi-threaded path (`nthreads` follows
    the host's core count) returns a DIFFERENT compressed byte string on
    repeated calls over the identical input array -- same length, and every
    version decodes back to the identical bytes, but the compressed
    representation itself is not stable run to run. At `nthreads=1` it is
    stable across every repeat tried.

    Design spec section 8 defines `manifest_digest` on the premise that "two
    identical copies" of a store agree: "A digest over concatenated bytes
    would depend on walk order and differ between two identical copies" names
    walk order as the one risk sorting the pairs removes -- which only makes
    sense if the underlying file bytes of "two identical copies" are already
    identical. Two independent `write_store` calls over the same session are
    "two identical copies" in exactly that sense, and with threaded
    compression they were not byte-identical: the digest genuinely differed,
    not on a walk-order technicality but for real, and this file's own
    `test_the_manifest_digest_is_stable_and_order_independent` caught it.
    Whatever section 3's confirm step turns out to compare once it is
    written, a manifest digest that is not a pure function of the session's
    contents cannot be the stable reference it is meant to be.

    `numcodecs.Blosc` takes no per-call thread count -- only the process-wide
    `numcodecs.blosc.set_nthreads()` -- so the previous value is restored in
    `finally` rather than left changed for whatever else runs in this
    process afterward.
    """
    store_path = out_dir / f"{session_dir.name}.zarr"
    out_dir.mkdir(parents=True, exist_ok=True)
    root = zarr.open(str(store_path), mode="w")
    compressor = numcodecs.Blosc(
        cname=codec_name, clevel=clevel, shuffle=numcodecs.Blosc.NOSHUFFLE
    )

    streams = bulk_streams(session_dir)
    stream_paths = {s.path for s in streams}

    previous_nthreads = numcodecs.blosc.set_nthreads(1)
    try:
        arrays = root.create_group(ARRAY_GROUP)
        for stream in streams:
            data = np.fromfile(stream.path, dtype=SAMPLE_DTYPE).reshape(
                stream.n_samples, stream.n_channels
            )
            arrays.create_dataset(
                stream.path.name,
                data=data,
                chunks=(min(_CHUNK_SAMPLES, stream.n_samples), stream.n_channels),
                compressor=compressor,
            )
            # The relative path is stored so verification can find the original
            # again without re-deriving where a stream sat in the tree.
            arrays[stream.path.name].attrs["source"] = str(
                stream.path.relative_to(session_dir)
            )

        verbatim = root.create_group(VERBATIM_GROUP)
        for path in sorted(p for p in session_dir.rglob("*") if p.is_file()):
            if path in stream_paths:
                continue
            raw = np.frombuffer(path.read_bytes(), dtype=np.uint8)
            verbatim.create_dataset(
                str(path.relative_to(session_dir)), data=raw, compressor=compressor
            )
    finally:
        numcodecs.blosc.set_nthreads(previous_nthreads)

    compressed = sum(p.stat().st_size for p in store_path.rglob("*") if p.is_file())
    return StoreResult(
        path=store_path,
        codec=codec_name,
        clevel=clevel,
        compressed_bytes=compressed,
        manifest_digest=manifest_digest(store_path),
    )
