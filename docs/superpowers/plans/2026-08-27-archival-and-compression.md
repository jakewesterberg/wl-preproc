# Archival and Compression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compress each session's raw data into one verified, session-level artifact on the NAS, prove it reconstructs to the bytes the recording computer hashed, and decide when the scratch copy may be reclaimed.

**Architecture:** A Zarr store per session — large ephys streams as compressed arrays, everything else verbatim. Verification reconstructs each original file's exact bytes and compares against the `blake3` in its DONE marker, which `ingest/verify.py` already checked at landing. Reclamation is a derived predicate over named conditions, not a stored human verdict.

**Tech Stack:** Python 3.11, `zarr` 2.x, `numcodecs` (Blosc/zstd), `blake3`, NumPy 2.4, DataJoint, pytest.

**Spec:** `docs/superpowers/specs/2026-08-27-archival-and-compression-design.md`

## Global Constraints

- **Python `>=3.11`.** CI tests 3.11 and 3.13.
- **Run tests with `.venv/bin/python -m pytest`.** The venv is `uv`-managed and has NO `pip`; install with `VIRTUAL_ENV=.venv uv pip install <pkg>`.
- **`zarr` is 2.18.7 here and zarr 3.x changed its API** — pin `>=2,<3` and do not write code that only works on one of them.
- **The codec is `numcodecs.Blosc(cname="zstd")`.** `wavpack-numcodecs` needs a system library (spec §2.1) and is not used. The codec actually used is recorded on the artifact.
- **Verification reconstructs BYTES, never samples** (spec §4). Comparing decoded arrays cannot catch a channel-order, interleaving or dtype error, because both sides come from the same assumption.
- **The reference digest is the DONE marker's `blake3`**, computed by the acquisition system. `wl_preproc/contracts/done.py::blake3_file` computes the same digest; use it, never `hashlib`.
- **The sentinel is written last**, after every other step, or it means nothing.
- **The synthetic fixture proves losslessness and never ranks codecs** (spec §2.1).
- House style: explain *why*, citing the spec section or the incident that motivated the choice.

---

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/archive/layout.py` *(create)* | Which files in a session are bulk ephys streams, and each one's exact `(dtype, n_channels)` byte layout. The single source of the reconstruction contract. |
| `wl_preproc/archive/store.py` *(create)* | Write the Zarr store — arrays plus verbatim files — and compute its manifest digest. |
| `wl_preproc/archive/verify.py` *(create)* | Reconstruct each original file's bytes from the store and compare against the DONE marker. |
| `wl_preproc/archive/reclaim.py` *(create)* | The named reclamation conditions and the predicate over them. |
| `wl_preproc/archive/tape.py` *(create)* | List verified sessions not yet on tape; emit the manifest a human carries. |
| `wl_preproc/schema/archive.py` *(create)* | The four tables. |
| `wl_preproc/ingest/watcher.py` *(modify)* | Backpressure below the scratch high-water mark. |
| `wl_preproc/responder/health.py` *(modify)* | `degraded` when scratch is under pressure. |
| `wl_preproc/cli/report.py` *(modify)* | Blocking condition per unreclaimed session; sessions whose rig may clear. |
| `wl_preproc/cli/main.py` *(modify)* | `archive`, `reclaim`, `hold`, `tape-manifest`. |

`layout.py` is separate from `store.py` on purpose: the byte layout is the one thing that must be exactly right, it is what `verify.py` independently re-derives, and a module that only answers "what shape is this file" can be read in one sitting.

---

## Task 1: The byte-layout oracle

**Files:**
- Create: `wl_preproc/archive/__init__.py`, `wl_preproc/archive/layout.py`
- Test: `tests/archive/test_layout.py`

**Interfaces:**
- Produces: `StreamLayout` (frozen dataclass: `path: Path`, `dtype: np.dtype`, `n_channels: int`, `n_samples: int`); `bulk_streams(session_dir) -> list[StreamLayout]`; `LayoutUndetermined(ValueError)`.

- [ ] **Step 1: Write the failing test**

Create `tests/archive/test_layout.py`:

```python
import numpy as np
import pytest

from wl_preproc.archive.layout import LayoutUndetermined, bulk_streams
from wl_preproc.synth.recipe import CI_RECIPE, STIM_RECIPE
from wl_preproc.synth.session import generate_session


def test_spikeglx_streams_are_found_with_their_real_shape(tmp_path):
    """The layout must come from the recording's own sidecar, not a guess:
    reconstruction in Task 3 is exactly this shape read back."""
    generate_session(tmp_path, CI_RECIPE)
    session = tmp_path / CI_RECIPE.session_id
    found = {s.path.name: s for s in bulk_streams(session)}

    ap = found[f"{CI_RECIPE.session_id}_imec0.ap.bin"]
    assert ap.dtype == np.dtype("<i2")
    assert ap.n_channels == CI_RECIPE.n_ap_channels + 1  # + the SY channel
    assert ap.n_samples * ap.n_channels * 2 == ap.path.stat().st_size


def test_the_lf_stream_is_found_too(tmp_path):
    generate_session(tmp_path, CI_RECIPE)
    session = tmp_path / CI_RECIPE.session_id
    names = {s.path.name for s in bulk_streams(session)}
    assert f"{CI_RECIPE.session_id}_imec0.lf.bin" in names


def test_rhs_amplifier_shape_is_derived_from_time_dat(tmp_path):
    """info.rhs has no reader in this repo, and needs none: time.dat is int32,
    one entry per sample, so the channel count falls out of the two sizes."""
    generate_session(tmp_path, STIM_RECIPE)
    session = tmp_path / STIM_RECIPE.session_id
    amp = next(s for s in bulk_streams(session) if s.path.name == "amplifier.dat")
    assert amp.dtype == np.dtype("<i2")
    assert amp.n_channels == STIM_RECIPE.n_ap_channels
    assert amp.n_samples * amp.n_channels * 2 == amp.path.stat().st_size


def test_a_size_that_does_not_divide_is_refused(tmp_path):
    """A wrong channel count that still 'works' is how a silent reconstruction
    bug ships. Refuse before an artifact is ever written."""
    generate_session(tmp_path, CI_RECIPE)
    session = tmp_path / CI_RECIPE.session_id
    ap = next(session.rglob("*_imec0.ap.bin"))
    with ap.open("ab") as fh:
        fh.write(b"\x00")  # one stray byte: size no longer divides
    with pytest.raises(LayoutUndetermined):
        bulk_streams(session)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/archive/test_layout.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.archive'`.

- [ ] **Step 3: Implement**

Create `wl_preproc/archive/__init__.py` (empty) and `wl_preproc/archive/layout.py`:

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/archive/test_layout.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -q`

```bash
git add wl_preproc/archive tests/archive
git commit -m "archive: the byte-layout oracle, refusing what it cannot determine"
```

---

## Task 2: Write the Zarr store

**Files:**
- Create: `wl_preproc/archive/store.py`
- Test: `tests/archive/test_store.py`

**Interfaces:**
- Consumes: `layout.bulk_streams`, `layout.StreamLayout`, `layout.SAMPLE_DTYPE`.
- Produces: `write_store(session_dir, out_dir, codec_name="zstd", clevel=5) -> StoreResult`; `StoreResult` (frozen: `path: Path`, `codec: str`, `clevel: int`, `compressed_bytes: int`, `manifest_digest: str`); `manifest_digest(store_dir) -> str`; `ARRAY_GROUP = "streams"`, `VERBATIM_GROUP = "verbatim"`.

- [ ] **Step 1: Write the failing test**

Create `tests/archive/test_store.py`:

```python
import numpy as np
import zarr

from wl_preproc.archive.layout import bulk_streams
from wl_preproc.archive.store import manifest_digest, write_store
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def test_every_bulk_stream_becomes_an_array(tmp_path):
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    result = write_store(session, tmp_path / "out")

    root = zarr.open(str(result.path), mode="r")
    expected = {s.path.name for s in bulk_streams(session)}
    assert set(root["streams"].array_keys()) == expected


def test_a_non_stream_file_is_stored_verbatim(tmp_path):
    """Verbatim, not re-encoded: the manifest and the DONE markers are what
    verification reads its reference digests out of."""
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    result = write_store(session, tmp_path / "out")

    original = next(session.rglob("*.done.json"))
    relative = str(original.relative_to(session))
    root = zarr.open(str(result.path), mode="r")
    stored = bytes(root["verbatim"][relative][:])
    assert stored == original.read_bytes()


def test_the_store_is_smaller_than_the_session(tmp_path):
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    raw = sum(p.stat().st_size for p in session.rglob("*") if p.is_file())
    result = write_store(session, tmp_path / "out")
    assert result.compressed_bytes < raw
    assert result.codec == "zstd"


def test_the_manifest_digest_is_stable_and_order_independent(tmp_path):
    """A Zarr store is a directory tree with no single hash. The digest is over
    sorted (path, blake3) pairs, so two identical copies agree regardless of
    the order a walk happens to return."""
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    a = write_store(session, tmp_path / "a")
    b = write_store(session, tmp_path / "b")
    assert a.manifest_digest == b.manifest_digest
    assert manifest_digest(a.path) == a.manifest_digest


def test_a_changed_byte_changes_the_manifest_digest(tmp_path):
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    result = write_store(session, tmp_path / "out")
    before = manifest_digest(result.path)

    victim = next(p for p in sorted(result.path.rglob("*")) if p.is_file())
    victim.write_bytes(victim.read_bytes() + b"\x00")
    assert manifest_digest(result.path) != before
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/archive/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.archive.store'`.

- [ ] **Step 3: Implement**

Create `wl_preproc/archive/store.py`:

```python
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
    """Compress `session_dir` into a Zarr store under `out_dir`."""
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

    compressed = sum(p.stat().st_size for p in store_path.rglob("*") if p.is_file())
    return StoreResult(
        path=store_path,
        codec=codec_name,
        clevel=clevel,
        compressed_bytes=compressed,
        manifest_digest=manifest_digest(store_path),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/archive/test_store.py -v`
Expected: PASS. If `create_dataset` rejects a name containing `/`, Zarr is treating it as a path — that is intended, and the nested groups it creates are read back by the same key.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/archive/store.py tests/archive/test_store.py
git commit -m "archive: write the session store, streams compressed and the rest verbatim"
```

---

## Task 3: Verification reconstructs bytes

**Files:**
- Create: `wl_preproc/archive/verify.py`
- Test: `tests/archive/test_verify.py`

**Interfaces:**
- Consumes: `store.ARRAY_GROUP`, `store.VERBATIM_GROUP`, `layout.SAMPLE_DTYPE`, `contracts.done.blake3_file`.
- Produces: `FileVerdict` (frozen: `relative_path: str`, `expected: str`, `actual: str`, `matched: bool`); `verify_store(store_path, session_dir) -> list[FileVerdict]`; `reconstruct(store_path, relative_path) -> bytes`.

- [ ] **Step 1: Write the failing test**

Create `tests/archive/test_verify.py`:

```python
import numpy as np
import pytest
import zarr

from wl_preproc.archive.store import write_store
from wl_preproc.archive.verify import reconstruct, verify_store
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def _archived(tmp_path):
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    return session, write_store(session, tmp_path / "out")


def test_every_file_reconstructs_to_its_original_bytes(tmp_path):
    session, result = _archived(tmp_path)
    verdicts = verify_store(result.path, session)
    assert verdicts
    assert all(v.matched for v in verdicts), [v for v in verdicts if not v.matched]


def test_reconstruction_is_byte_identical_not_merely_equal_samples(tmp_path):
    session, result = _archived(tmp_path)
    original = next(session.rglob("*_imec0.ap.bin"))
    rebuilt = reconstruct(result.path, str(original.relative_to(session)))
    assert rebuilt == original.read_bytes()


def test_a_corrupted_artifact_fails_verification(tmp_path):
    """A test that only ever sees a good artifact proves the happy path and
    nothing about the guard."""
    session, result = _archived(tmp_path)
    victim = next(
        p for p in sorted((result.path / "streams").rglob("*")) if p.is_file()
    )
    victim.write_bytes(b"\x00" * victim.stat().st_size)

    verdicts = verify_store(result.path, session)
    assert any(not v.matched for v in verdicts)


def test_a_transposed_reconstruction_is_caught(tmp_path):
    """The case comparing samples to samples cannot catch: identical values,
    wrong layout. Rewrite one array transposed and assert the digest differs."""
    session, result = _archived(tmp_path)
    original = next(session.rglob("*_imec0.ap.bin"))
    relative = str(original.relative_to(session))

    root = zarr.open(str(result.path), mode="a")
    name = original.name
    data = root["streams"][name][:]
    del root["streams"][name]
    swapped = root["streams"].create_dataset(name, data=np.ascontiguousarray(data[:, ::-1]))
    swapped.attrs["source"] = relative

    assert reconstruct(result.path, relative) != original.read_bytes()
    assert any(not v.matched for v in verify_store(result.path, session))


def test_the_roundtrip_holds_for_intan_too(tmp_path):
    """Design spec section 9: the roundtrip "must run on every emitted system,
    not only SpikeGLX". Intan reaches `layout.py` by a different route -- its
    channel count is derived from time.dat rather than read from a sidecar -- so
    SpikeGLX passing says nothing about it."""
    from wl_preproc.synth.recipe import STIM_RECIPE

    generate_session(tmp_path / "in", STIM_RECIPE)
    session = tmp_path / "in" / STIM_RECIPE.session_id
    result = write_store(session, tmp_path / "out")

    verdicts = verify_store(result.path, session)
    assert verdicts
    assert all(v.matched for v in verdicts), [v for v in verdicts if not v.matched]

    amplifier = next(session.rglob("amplifier.dat"))
    rebuilt = reconstruct(result.path, str(amplifier.relative_to(session)))
    assert rebuilt == amplifier.read_bytes()


def test_a_missing_done_marker_entry_is_an_error_not_a_pass(tmp_path):
    """No reference digest must never read as 'verified'."""
    session, result = _archived(tmp_path)
    for marker in session.rglob("*.done.json"):
        marker.unlink()
    with pytest.raises(ValueError):
        verify_store(result.path, session)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/archive/test_verify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.archive.verify'`.

- [ ] **Step 3: Implement**

Create `wl_preproc/archive/verify.py`:

```python
"""Reconstruct every original file from the artifact and compare against the
digest the recording computer produced.

**Bytes, never samples.** Decoding the artifact and comparing sample arrays
against sample arrays proves the codec round-trips and nothing else: it cannot
catch a channel-order error, an interleaving mistake or a dtype slip, because
both sides of that comparison come out of the same wrong assumption.
Reconstructing the bytes catches all three. Design spec section 4.

**The reference is not ours.** `done_marker.json` requires `blake3` on every
file entry, computed by the acquisition system at transfer time, and
`ingest/verify.py` already checks each one at landing. So this compares against
a digest computed on another machine before this pipeline saw the file, closing
the chain rig -> landing -> archive with one hash.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import blake3 as _blake3
import numpy as np
import zarr

from wl_preproc.archive.layout import SAMPLE_DTYPE
from wl_preproc.archive.store import ARRAY_GROUP, VERBATIM_GROUP


@dataclass(frozen=True, slots=True)
class FileVerdict:
    relative_path: str
    expected: str
    actual: str
    matched: bool


def _expected_digests(session_dir: Path) -> dict[str, str]:
    """Every `(relative path -> blake3)` the DONE markers claim."""
    digests: dict[str, str] = {}
    for marker in sorted(session_dir.rglob("*.done.json")):
        payload = json.loads(marker.read_text(encoding="utf-8"))
        system_dir = marker.parent
        for entry in payload["files"]:
            resolved = (system_dir / entry["path"]).resolve()
            digests[str(resolved.relative_to(session_dir.resolve()))] = entry["blake3"]
    if not digests:
        raise ValueError(
            f"{session_dir} has no DONE marker entries; there is no reference "
            "digest to verify against, and 'nothing to check' must never read "
            "as 'verified'"
        )
    return digests


def reconstruct(store_path: Path, relative_path: str) -> bytes:
    """The original file's exact bytes, rebuilt from the artifact."""
    root = zarr.open(str(store_path), mode="r")
    arrays = root[ARRAY_GROUP]
    for name in arrays.array_keys():
        if arrays[name].attrs.get("source") == relative_path:
            return arrays[name][:].astype(SAMPLE_DTYPE).tobytes()
    return bytes(root[VERBATIM_GROUP][relative_path][:])


def verify_store(store_path: Path, session_dir: Path) -> list[FileVerdict]:
    """One verdict per file the DONE markers name."""
    verdicts = []
    for relative_path, expected in sorted(_expected_digests(session_dir).items()):
        rebuilt = reconstruct(store_path, relative_path)
        actual = _blake3.blake3(rebuilt).hexdigest()
        verdicts.append(
            FileVerdict(relative_path, expected, actual, actual == expected)
        )
    return verdicts
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/archive/test_verify.py -v`
Expected: PASS.

If `blake3.blake3(bytes).hexdigest()` disagrees with `contracts.done.blake3_file` on the same content, read that function before changing anything here — it defines what this contract's `blake3` field means, and matching it is the requirement.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/archive/verify.py tests/archive/test_verify.py
git commit -m "archive: verify by reconstructing bytes against the rig's own digest"
```

---

## Task 4: The four tables

**Files:**
- Create: `wl_preproc/schema/archive.py`
- Modify: `wl_preproc/schema/__init__.py`
- Test: `tests/schema/test_archive_tables.py`

**Interfaces:**
- Produces: `schema.archive` module exposing `ArchiveArtifact`, `ArchiveVerification`, `ReclamationHold`, `ScratchReclamation`, and `activate(prefix)`.

- [ ] **Step 1: Read the pattern before writing**

Read `wl_preproc/schema/coverage.py` end to end. It is the smallest module in this project that declares tables and an `activate`, and this task follows it exactly rather than inventing a second shape.

- [ ] **Step 2: Write the failing test**

Create `tests/schema/test_archive_tables.py`:

```python
import pytest

from wl_preproc.schema import archive


def test_the_four_tables_exist():
    for name in (
        "ArchiveArtifact",
        "ArchiveVerification",
        "ReclamationHold",
        "ScratchReclamation",
    ):
        assert hasattr(archive, name), name


def test_verification_is_keyed_per_file_not_per_session():
    """Design spec section 4: when it fails the question is WHICH file, and a
    per-session boolean cannot answer it."""
    assert "relative_path" in archive.ArchiveVerification.definition


def test_no_status_column_on_the_artifact():
    """Plan 10 section 1 forbids a status column; the answer is derived from
    these four tables (design spec section 8)."""
    assert "status" not in archive.ArchiveArtifact.definition
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_archive_tables.py -v`
Expected: FAIL — `ImportError: cannot import name 'archive'`.

- [ ] **Step 4: Implement**

Create `wl_preproc/schema/archive.py`, following `coverage.py`'s activate pattern exactly:

```python
"""Archival state: the artifact, its per-file verification, holds and
reclamations.

**No status column anywhere.** Plan 10 section 1 forbids one and design spec
section 8 gives the reason: a stored verdict is a second answer free to drift
from the facts it came from. Whether a session is reclaimable is COMPUTED from
these rows (`archive/reclaim.py`), never stored.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.schema import pipeline

schema = dj.Schema()


@schema
class ArchiveArtifact(dj.Manual):
    definition = """
    # One compressed artifact per session -- wl.works Plan 25 section 1.2's
    # grain, where per-block was designed, recommended and withdrawn.
    # Key: (subject, session_datetime).
    -> pipeline.Session
    ---
    # The settled triple (Plan 23 section 4.3): host + share + relative path.
    # Never an opaque string -- an agent must be able to open the file rather
    # than a human read a path out of a field.
    archive_host  : varchar(64)
    archive_share : varchar(64)
    archive_path  : varchar(255)
    codec         : varchar(32)   # what actually ran, not what the code defaults to
    clevel        : tinyint
    compressed_bytes : bigint
    # blake3 over sorted (relative path, blake3) pairs. A Zarr store is a tree
    # and has no single hash; a digest over concatenated bytes would depend on
    # walk order and differ between two identical copies.
    manifest_digest : varchar(64)
    compressed_at   : datetime
    """


@schema
class ArchiveVerification(dj.Manual):
    definition = """
    # One row per original file. Per file because when verification fails the
    # question is immediately WHICH file, and a per-session boolean cannot
    # answer it (design spec section 4).
    -> ArchiveArtifact
    relative_path : varchar(255)
    ---
    expected_blake3 : varchar(64)   # from the DONE marker, written by the rig
    actual_blake3   : varchar(64)   # from reconstructing the artifact
    matched         : tinyint
    verified_at     : datetime
    """


@schema
class ReclamationHold(dj.Manual):
    definition = """
    # A human blocking or forcing reclamation. The ONLY place a person appears
    # in this subsystem: design spec section 5.3 inverts section 8.5's gate from
    # "wait unless approved" to "proceed unless held".
    -> pipeline.Session
    held_at : datetime
    ---
    actor   : varchar(64)
    verdict : enum('hold','force')
    reason  : varchar(512)
    """


@schema
class ScratchReclamation(dj.Manual):
    definition = """
    # What was freed, and when. Key: (subject, session_datetime).
    -> pipeline.Session
    ---
    reclaimed_at : datetime
    bytes_freed  : bigint
    """


def activate(prefix: str) -> None:
    schema.activate(f"{prefix}_archive")
```

Then add `archive` to whatever `wl_preproc/schema/__init__.py` exports, matching how `coverage` is listed.

- [ ] **Step 5: Run and commit**

Run: `.venv/bin/python -m pytest tests/schema -q`

```bash
git add wl_preproc/schema/archive.py wl_preproc/schema/__init__.py tests/schema/test_archive_tables.py
git commit -m "schema: the four archival tables, and no status column"
```

---

## Task 5: The archive stage — publish, sentinel, rows

**Files:**
- Create: `wl_preproc/archive/stage.py`
- Test: `tests/archive/test_stage.py`

**Interfaces:**
- Consumes: `store.write_store`, `verify.verify_store`, `schema.archive`.
- Produces: `SENTINEL_NAME = ".wlpp-archive-complete"`; `archive_session(session_dir, nas_root, host, share) -> ArchiveOutcome`; `ArchiveOutcome` (frozen: `artifact_path: Path`, `verdicts: list[FileVerdict]`, `all_matched: bool`).

- [ ] **Step 1: Write the failing test**

Create `tests/archive/test_stage.py`:

```python
from wl_preproc.archive.stage import SENTINEL_NAME, archive_session
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def test_a_verified_session_gets_a_sentinel(tmp_path):
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    outcome = archive_session(session, tmp_path / "nas", "wl-nas", "archive")

    assert outcome.all_matched
    assert (outcome.artifact_path / SENTINEL_NAME).exists()


def test_the_sentinel_is_absent_when_verification_fails(tmp_path):
    """Its whole purpose is telling a prober whole from partial. A sentinel on
    an unverified artifact is worse than none -- wl.works reads `complete` and
    believes it."""
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    marker = next(session.rglob("*.done.json"))
    text = marker.read_text(encoding="utf-8")
    marker.write_text(text.replace('"blake3": "', '"blake3": "0'), encoding="utf-8")

    outcome = archive_session(session, tmp_path / "nas", "wl-nas", "archive")
    assert not outcome.all_matched
    assert not (outcome.artifact_path / SENTINEL_NAME).exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/archive/test_stage.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `wl_preproc/archive/stage.py`:

```python
"""compress -> verify -> publish -> confirm -> sentinel.

**The sentinel is written last and that is its entire purpose.** wl.works'
prober records `complete` per observation under the rule "positive observations
only; absence renders unknown, never 'no data'". Without a sentinel a
half-copied artifact and a finished one are the same observation -- and a
sentinel written on an artifact that failed verification is worse than none,
because the app reads it and believes it.
"""

from __future__ import annotations

import datetime
import shutil
from dataclasses import dataclass
from pathlib import Path

from wl_preproc.archive.store import manifest_digest, write_store
from wl_preproc.archive.verify import FileVerdict, verify_store

SENTINEL_NAME = ".wlpp-archive-complete"


@dataclass(frozen=True, slots=True)
class ArchiveOutcome:
    artifact_path: Path
    verdicts: list[FileVerdict]
    all_matched: bool


def archive_session(
    session_dir: Path, nas_root: Path, host: str, share: str
) -> ArchiveOutcome:
    """Compress to scratch, verify there, publish, confirm, then sentinel."""
    scratch = session_dir.parent / f".{session_dir.name}.archiving"
    result = write_store(session_dir, scratch)
    verdicts = verify_store(result.path, session_dir)
    all_matched = all(v.matched for v in verdicts)

    published = nas_root / result.path.name
    if published.exists():
        shutil.rmtree(published)
    published.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(result.path, published)
    shutil.rmtree(scratch)

    # Confirm the copy: cheaper than re-verifying, and it catches the failure
    # publishing can introduce that verification cannot see -- transfer
    # corruption between scratch and the NAS.
    if manifest_digest(published) != result.manifest_digest:
        all_matched = False

    if all_matched:
        (published / SENTINEL_NAME).write_text(
            datetime.datetime.now(datetime.UTC).isoformat(), encoding="utf-8"
        )
    return ArchiveOutcome(published, verdicts, all_matched)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/archive/test_stage.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

```bash
git add wl_preproc/archive/stage.py tests/archive/test_stage.py
git commit -m "archive: publish and sentinel, written last or not at all"
```

---

## Task 6: Backpressure at ingest

**Read this before writing anything.** The threshold, the measurement, the
health degradation and the report line **already exist**:

- `wl_preproc/cli/doctor.py` has `scratch_headroom() -> (free_gib, headroom_ok)`
  and `_MIN_SCRATCH_FREE_GIB`.
- `wl_preproc/responder/health.py::_featured_key` already returns
  `"disk_headroom"` when `not readings.headroom_ok`, and `build_health` already
  turns that into `degraded`.
- `wl_preproc/cli/report.py` already renders `free_gib`.

So this task adds **only the refusal**, and it must reuse `doctor.scratch_headroom()`
rather than define a second threshold. `_featured_key`'s own docstring gives the
reason in terms: *"Two definitions of 'is this host degraded' that could drift
apart is exactly the defect `Readings`' own docstring says this project has
already found in four separate shapes."*

**Files:**
- Modify: `wl_preproc/ingest/watcher.py`
- Test: `tests/ingest/test_backpressure.py`

**Interfaces:**
- Consumes: `wl_preproc.cli.doctor.scratch_headroom`.
- Produces: `watcher.refuses_new_sessions() -> bool`.

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/test_backpressure.py`:

```python
from unittest.mock import patch

from wl_preproc.ingest import watcher


def test_new_sessions_are_refused_when_headroom_is_low():
    """Design spec section 8.4: refuse rather than fill scratch and stall
    mid-sort."""
    with patch("wl_preproc.ingest.watcher.scratch_headroom", return_value=(3, False)):
        assert watcher.refuses_new_sessions() is True


def test_new_sessions_are_accepted_when_headroom_is_fine():
    with patch("wl_preproc.ingest.watcher.scratch_headroom", return_value=(4000, True)):
        assert watcher.refuses_new_sessions() is False


def test_the_threshold_is_not_redefined_here():
    """A second definition of "is scratch low" is the drift `_featured_key`'s
    docstring warns about. This module must own no threshold of its own."""
    source = (watcher.__file__ and open(watcher.__file__).read()) or ""
    assert "_MIN_SCRATCH_FREE_GIB" not in source
    assert "high_water" not in source
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/ingest/test_backpressure.py -v`
Expected: FAIL — `AttributeError: module 'wl_preproc.ingest.watcher' has no attribute 'refuses_new_sessions'`.

- [ ] **Step 3: Implement**

Add to `wl_preproc/ingest/watcher.py`:

```python
from wl_preproc.cli.doctor import scratch_headroom


def refuses_new_sessions() -> bool:
    """True when scratch is too tight to accept another session.

    Design spec section 8.4: the watcher "refuses new sessions below a scratch
    high-water mark and alerts, rather than filling scratch and stalling
    mid-sort".

    **The threshold is `doctor.scratch_headroom()`'s and is not redefined here.**
    `responder/health.py::_featured_key` already turns the same `headroom_ok`
    into a `degraded` verdict that wl.works sees on its next poll -- which is
    the "and alerts" half, needing no new mechanism, and matters because
    transport is pull-only. A second threshold in this module would be a second
    definition of "is this host degraded", which that function's own docstring
    names as a defect this project has already found in four separate shapes.
    """
    _free_gib, headroom_ok = scratch_headroom()
    return not headroom_ok
```

Then call it wherever the watcher decides to admit a session, skipping admission and logging the refusal when it returns `True`.

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/ingest tests/responder tests/cli -q`

```bash
git add wl_preproc/ingest/watcher.py tests/ingest/test_backpressure.py
git commit -m "ingest: refuse new sessions on the threshold doctor already owns"
```

---

## Task 7: The reclamation predicate

**Files:**
- Create: `wl_preproc/archive/reclaim.py`
- Test: `tests/archive/test_reclaim.py`

**Interfaces:**
- Produces: `Condition` (frozen: `name: str`, `passed: bool`, `detail: str`); `reclaim_conditions(session_key, prefix) -> list[Condition]`; `reclaimable(conditions) -> bool`; `blocking(conditions) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/archive/test_reclaim.py`:

```python
from wl_preproc.archive.reclaim import Condition, blocking, reclaimable

CONDITION_NAMES = (
    "artifact_present",
    "every_file_verified",
    "not_tier_d",
    "no_pending_paramset_or_warm_copy",
    "no_hold",
)


def _all_passing():
    return [Condition(n, True, "") for n in CONDITION_NAMES]


def test_all_conditions_passing_is_reclaimable():
    assert reclaimable(_all_passing()) is True


def test_each_condition_blocks_on_its_own():
    """Five conditions, five cases. A condition that never fires alone is
    indistinguishable from one that cannot fire at all."""
    for index, name in enumerate(CONDITION_NAMES):
        conditions = _all_passing()
        conditions[index] = Condition(name, False, "failed for the test")
        assert reclaimable(conditions) is False, name
        assert blocking(conditions) == [name]


def test_blocking_names_every_failure_not_just_the_first():
    """The daily report says WHICH condition blocks a session; naming only the
    first would send someone to fix one of several."""
    conditions = _all_passing()
    conditions[0] = Condition(CONDITION_NAMES[0], False, "")
    conditions[2] = Condition(CONDITION_NAMES[2], False, "")
    assert blocking(conditions) == [CONDITION_NAMES[0], CONDITION_NAMES[2]]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/archive/test_reclaim.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `wl_preproc/archive/reclaim.py`:

```python
"""When the scratch copy may go.

**A named list, not a boolean.** Design spec section 5.2: each condition records
why it passed or failed, so the daily report can say WHICH one blocks a session
rather than that one does. Section 8.5 requires the gate be surfaced there
"since an ungated session is what will eventually fill scratch", and only a
named list makes that report actionable.

**Incomplete today, and it says so.** The predicate can currently see only
timing quality: tier says nothing about whether a sort is good, because section
6.5's unit QC metrics are 2b-6 and unbuilt, and the canonical NWB is Phase 3.
Both join this list when they land. Writing it as a growing list makes the
current incompleteness visible instead of implying the rule is finished.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Condition:
    name: str
    passed: bool
    detail: str


def reclaimable(conditions: list[Condition]) -> bool:
    """True only when every condition passes."""
    return all(c.passed for c in conditions)


def blocking(conditions: list[Condition]) -> list[str]:
    """Every failing condition's name, in order -- not merely the first."""
    return [c.name for c in conditions if not c.passed]
```

Then add `reclaim_conditions` to the same module:

```python
def reclaim_conditions(session_key: dict, expected_file_count: int) -> list[Condition]:
    """The five conditions, each evaluated against recorded facts.

    `expected_file_count` is how many files the session's DONE markers name.
    Passed in rather than counted here so this module reads no filesystem:
    every condition below is a question about rows, and a function that also
    walks a directory would be two things.
    """
    from wl_preproc.schema import archive, timebase

    artifact = archive.ArchiveArtifact & session_key
    verifications = archive.ArchiveVerification & session_key
    matched = verifications & "matched = 1"
    tier_rows = (timebase.TimingProvenance & session_key).fetch("tier")
    holds = (archive.ReclamationHold & session_key).fetch(
        "verdict", order_by="held_at DESC", limit=1
    )

    return [
        Condition(
            "artifact_present",
            bool(artifact),
            "" if artifact else "no ArchiveArtifact row",
        ),
        Condition(
            "every_file_verified",
            len(matched) == expected_file_count and len(matched) > 0,
            f"{len(matched)} of {expected_file_count} files verified",
        ),
        Condition(
            "not_tier_d",
            len(tier_rows) == 1 and tier_rows[0] != "D",
            f"tier {tier_rows[0]}" if len(tier_rows) == 1 else "no tier resolved",
        ),
        # Design spec section 5.2, from parent section 8.4's surviving clause:
        # a queued re-sort keeps its fast copy. Both halves are unbuilt --
        # paramset requests reach here in 2b-5 and the warm tier in the
        # rehydration plan -- so this passes today and gains its query then.
        Condition(
            "no_pending_paramset_or_warm_copy",
            True,
            "no paramset queue exists yet (2b-5); passes vacuously",
        ),
        Condition(
            "no_hold",
            not (len(holds) and holds[0] == "hold"),
            "held" if len(holds) and holds[0] == "hold" else "",
        ),
    ]
```

**The fourth condition passes vacuously today and says so in its own `detail`.**
That is the design's point (spec §5.2): the list is a growing set of named
conditions, and a condition that cannot yet be evaluated must be visible as
such rather than silently absent. A reader of the daily report sees the
sentence.

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/archive -q`

```bash
git add wl_preproc/archive/reclaim.py tests/archive/test_reclaim.py
git commit -m "archive: reclamation is a named list of conditions, not a verdict"
```

---

## Task 8: Tape staging

**Files:**
- Create: `wl_preproc/archive/tape.py`
- Test: `tests/archive/test_tape.py`

**Interfaces:**
- Produces: `TapeEntry` (frozen: `session_id: str`, `artifact_path: str`, `bytes: int`, `manifest_digest: str`); `staging_manifest(entries) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/archive/test_tape.py`:

```python
from wl_preproc.archive.tape import TapeEntry, staging_manifest


def test_the_manifest_names_every_entry_with_its_digest():
    """A person carries this to the machine with the drive. Without the digest
    they cannot check the write, and checking the write is the point."""
    entries = [
        TapeEntry("2027-03-14_01", "/nas/archive/2027-03-14_01.zarr", 104_857_600, "ab" * 32),
        TapeEntry("2027-03-14_02", "/nas/archive/2027-03-14_02.zarr", 209_715_200, "cd" * 32),
    ]
    text = staging_manifest(entries)
    for entry in entries:
        assert entry.session_id in text
        assert entry.artifact_path in text
        assert entry.manifest_digest in text


def test_an_empty_manifest_says_so_rather_than_being_blank():
    text = staging_manifest([])
    assert text.strip()
    assert "no sessions" in text.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/archive/test_tape.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `wl_preproc/archive/tape.py`:

```python
"""What a person carries to the machine with the drive.

**No bin-packing, deliberately.** At CR ~3.59 a session is ~100 GB and at two
sessions a week that is ~10.4 TB/year, so an LTO-9 cartridge at 18 TB native
holds roughly eighteen months of output. Building a capacity-fitting algorithm
would be inventing work for a constraint that does not bind (design spec
section 7).

**No tape table either.** wl.works Plan 25 section 4 creates
`cold_storage_medium` and `animal_session_cold_copy`. Two records of one
cartridge are two records free to disagree.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TapeEntry:
    session_id: str
    artifact_path: str
    bytes: int
    manifest_digest: str


def staging_manifest(entries: list[TapeEntry]) -> str:
    """A manifest a human can act on and check a write against."""
    if not entries:
        return (
            "No sessions are staged for tape: every verified archive is already "
            "recorded as copied, or none has been verified yet.\n"
        )
    total = sum(e.bytes for e in entries)
    lines = [
        "Sessions staged for tape",
        f"{len(entries)} session(s), {total / 1e9:.1f} GB total",
        "",
        "Verify each write against its manifest digest before shelving the "
        "cartridge -- the digest is over sorted (relative path, blake3) pairs "
        "of every file in the store.",
        "",
    ]
    for entry in entries:
        lines.append(f"{entry.session_id}")
        lines.append(f"    path   {entry.artifact_path}")
        lines.append(f"    bytes  {entry.bytes}")
        lines.append(f"    digest {entry.manifest_digest}")
        lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run and commit**

```bash
git add wl_preproc/archive/tape.py tests/archive/test_tape.py
git commit -m "archive: the tape staging manifest, and no bin-packer"
```

---

## Task 9: Wire it up — CLI, report, manifest

**Files:**
- Modify: `wl_preproc/cli/main.py`, `wl_preproc/cli/report.py`, `pyproject.toml`, `wl.yaml`
- Test: `tests/cli/test_archive_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `wlpp archive`, `wlpp reclaim`, `wlpp hold`, `wlpp tape-manifest`.

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_archive_cli.py`:

```python
from wl_preproc.cli.main import main


def test_every_new_command_is_reachable(capsys):
    assert main(["--help"]) == 0
    helptext = capsys.readouterr().out
    for command in ("archive", "reclaim", "hold", "tape-manifest"):
        assert command in helptext, command
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/cli/test_archive_cli.py -v`
Expected: FAIL — the four commands are absent from `--help`.

- [ ] **Step 3: Add the commands**

In `wl_preproc/cli/main.py`, beside the existing `add_parser` calls:

```python
    archive_p = subparsers.add_parser("archive", help="compress and verify a session")
    archive_p.add_argument("--session", required=True)

    reclaim_p = subparsers.add_parser("reclaim", help="free scratch for a session")
    reclaim_p.add_argument("--session", required=True)
    reclaim_p.add_argument("--dry-run", action="store_true", default=True)

    hold_p = subparsers.add_parser("hold", help="block or force reclamation")
    hold_p.add_argument("--session", required=True)
    hold_p.add_argument("--verdict", choices=("hold", "force"), required=True)
    hold_p.add_argument("--actor", required=True)
    hold_p.add_argument("--reason", required=True)

    subparsers.add_parser("tape-manifest", help="list sessions staged for tape")
```

At the bottom of `main()`, beside the existing dispatch branches:

```python
    if args.group == "archive":
        from wl_preproc.archive.stage import archive_session

        outcome = archive_session(
            Path(args.session), Path(args.nas_root), args.host, args.share
        )
        for verdict in outcome.verdicts:
            if not verdict.matched:
                print(f"MISMATCH {verdict.relative_path}")
        print("verified" if outcome.all_matched else "NOT verified")
        return 0 if outcome.all_matched else 1

    if args.group == "tape-manifest":
        from wl_preproc.archive.tape import staging_manifest

        print(staging_manifest(_staged_entries(prefix=args.prefix)))
        return 0
```

`reclaim` follows `wlpp delete`'s guardrail from design spec section 10 —
*"prints the full cascade, defaults to `--dry-run`, requires explicit
confirmation"* — printing each blocking condition and freeing nothing unless
`--dry-run` is disabled. `hold` inserts one `ReclamationHold` row. `_staged_entries`
reads verified artifacts out of `ArchiveArtifact` joined to `ArchiveVerification`.

- [ ] **Step 4: Extend the daily report**

In `wl_preproc/cli/report.py`, add two sections, following that file's existing section style:

- **Unreclaimed sessions with their blocking condition** — `reclaim.blocking()` per session. Design spec section 8.5 requires the gate be surfaced here.
- **Sessions whose rig may clear its copy** — verified archives. Design spec section 3.2: the rigs hold their copy until this pipeline reports a verified archive, and this pipeline cannot reach a rig, so the report is the channel. If nobody reads this line, acquisition disks fill and the rig stops recording, hours away from the pipeline that caused it.

- [ ] **Step 5: Declare the dependencies**

In `pyproject.toml`, add to `dependencies`:

```toml
    # The archive is a Zarr store (2026-08-27-archival-and-compression-design
    # section 1). Pinned below 3: zarr 3.x changed the API this uses, and 2.18
    # is what the format oracle already pulls in.
    "zarr>=2,<3",
    # Blosc/zstd, the archive codec. WavPack would compress ~6% better but
    # wraps a system library that will not install from pip -- see that
    # design's section 2.1.
    "numcodecs>=0.15",
```

In `wl.yaml`, add both to `third_party` with the same `why`, then run `wl-check` — it must report `wl.yaml: no findings`.

- [ ] **Step 6: Run everything and commit**

Run: `.venv/bin/python -m pytest -q` and `wl-check`

```bash
git add wl_preproc/cli pyproject.toml wl.yaml tests/cli/test_archive_cli.py
git commit -m "cli: archive, reclaim, hold and tape-manifest, and the report's two new lines"
```

---

## Task 10: Trigger archival at ingest, and record what changed

Two loose ends the spec names and no task above closes: **nothing calls
`archive_session`** (spec §3.1 says it runs as soon as ingest verification
passes), and **§11's amendments to the parent spec are unwritten**.

**Files:**
- Modify: `wl_preproc/ingest/watcher.py` or `wl_preproc/daemon.py` — whichever owns the post-verification step
- Modify: `docs/superpowers/specs/2026-08-12-wl-preproc-design.md`
- Test: `tests/ingest/test_archive_trigger.py`

- [ ] **Step 1: Find who owns "verification just passed"**

Read `wl_preproc/ingest/watcher.py` and `wl_preproc/daemon.py` and identify the
single point after `ingest/verify.py` reports success. Archival attaches there
and nowhere else — two trigger sites would be two policies.

- [ ] **Step 2: Write the failing test**

Create `tests/ingest/test_archive_trigger.py`:

```python
from unittest.mock import patch

from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def test_a_verified_session_is_handed_to_archival(tmp_path):
    """Design spec section 3.1: archival runs as soon as ingest verification
    passes, before anything else touches the session -- not after processing."""
    generate_session(tmp_path, CI_RECIPE)
    with patch("wl_preproc.ingest.watcher.archive_session") as archived:
        _run_the_watcher_over(tmp_path)   # replace with the real entry point
    assert archived.called


def test_a_quarantined_session_is_not_archived(tmp_path):
    """Verification failing is exactly when NOT to spend an hour compressing."""
    generate_session(tmp_path, CI_RECIPE)
    marker = next(tmp_path.rglob("*.done.json"))
    marker.write_text(marker.read_text().replace('"bytes": ', '"bytes": 1'), encoding="utf-8")
    with patch("wl_preproc.ingest.watcher.archive_session") as archived:
        _run_the_watcher_over(tmp_path)
    assert not archived.called
```

- [ ] **Step 3: Wire it, run, and commit**

Run: `.venv/bin/python -m pytest tests/ingest -q`

- [ ] **Step 4: Amend the parent spec**

Per this design's §11, appending dated blocks and keeping the original claims
visible — this repository corrects rather than rewrites:

- **§3.3** — the storage arithmetic is conservative by ~1.8×: ~100 GB/session and ~10.4 TB/year at CR 3.59, not 180 GB and 15–20 TB. Keep the old figures; a purchasing decision may rest on them.
- **§8.4** — *"cold copy confirmed"* leaves the reclamation preconditions. This pipeline cannot observe it and tape is a human's step.
- **§8.5** — the human "checked good" verdict becomes a derived predicate plus a hold. A reversal, argued from §8.4's own rehydration path.
- **§8.5** — its Buccino OPEN is **discharged**; cite the paper and the CR figures.
- **§10** — the daily report gains the blocking reclamation condition and the rig-may-clear list.

- [ ] **Step 5: Verify and commit**

Run: `wl-check` and `.venv/bin/python -m pytest -q`

```bash
git add docs/superpowers/specs/2026-08-12-wl-preproc-design.md wl_preproc tests
git commit -m "archive: trigger at ingest, and record what this changed in the parent spec"
```

---

## Not in this plan

- **Writing tape.** A human writes; this pipeline prepares (spec §0).
- **The institutional online archive.** Deferred; §7 leaves the seam.
- **IO throttling** (spec §10 item 4). Archival runs immediately after ingest, and the contention it would trade against does not exist until sorting does.
- **A chosen high-water mark** (spec §10 item 3). The default is 1 TB; the real device does not exist yet.
- **Rehydration** — decompress-to-scratch. §8.4 names it as the path that makes reclamation safe, and it is the natural next plan.
