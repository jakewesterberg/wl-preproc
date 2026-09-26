"""Reconstruct every original file from the artifact and compare against the
digest the recording computer produced.

**Bytes, never samples.** Decoding the artifact and comparing sample arrays
against sample arrays proves the codec round-trips and nothing else: it cannot
catch a channel-order error, an interleaving mistake or a dtype slip, because
both sides of that comparison come out of the same wrong assumption.
Reconstructing the bytes catches all three. Design spec section 4.

**What this does not prove.** On a C-contiguous array, `.reshape()` moves no
bytes -- `.tobytes()` is identical for any valid factorization of the same
element count -- so an artifact whose stream arrays carry a plausible-but-wrong
`(n_samples, n_channels)` reconstructs byte-for-byte here and reports
`matched=True`. Design spec section 4 was amended 2026-08-27 to say so: "byte
reconstruction catches a channel-order error, an interleaving mistake, or a
dtype slip" is true of a genuine transformation of the data --
`tests/archive/test_verify_reconstruction.py`'s
`test_a_transposed_reconstruction_is_caught` exercises exactly that, channels
actually swapped, which does move bytes -- and false of a mislabelling of
shape. That guarantee belongs to `layout.py` alone -- see its module
docstring -- and nothing here should be read as extending to it.

**The reference is not ours.** The `DONE` marker -- `contracts/paths.py`'s
`DONE_MARKER_FILENAME`, one written per system directory, parsed as YAML by
`contracts/done.py`'s `DoneMarker.from_yaml` -- requires `blake3` on every
file entry, computed by the acquisition system at transfer time, and
`ingest/verify.py` already checks each one at landing. (`docs/schemas/
done_marker.json`, named in this repository's own `wl.yaml` under
`publishes`, is a different thing: the JSON Schema this repository exports
describing that marker's shape, for an external audience, kept current by
`wlpp schemas export` -- not a second on-disk format. What actually sits at
`<session>/<system>/DONE` and gets read below is the YAML.) So this compares
against a digest computed on another machine before this pipeline saw the
file, closing the chain rig -> landing -> archive with one hash.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import blake3 as _blake3
import zarr

from wl_preproc.archive.layout import SAMPLE_DTYPE
from wl_preproc.archive.store import ARRAY_GROUP, VERBATIM_GROUP
from wl_preproc.contracts.done import DoneMarker
from wl_preproc.contracts.paths import DONE_MARKER_FILENAME


@dataclass(frozen=True, slots=True)
class FileVerdict:
    relative_path: str
    expected: str
    actual: str
    matched: bool


def _expected_digests(session_dir: Path) -> dict[str, str]:
    """Every `(relative path -> blake3)` the DONE markers claim.

    The marker is named `DONE` and is YAML, not JSON -- see
    `contracts/paths.py::DONE_MARKER_FILENAME` and `contracts/done.py`. Parsed
    through `DoneMarker.from_yaml` rather than `yaml.safe_load` directly,
    because that model also rejects a `path` containing `..` or an absolute
    path, and this function joins `path` onto a system directory exactly as
    `ingest/verify.py` does.
    """
    digests: dict[str, str] = {}
    for marker in sorted(session_dir.rglob(DONE_MARKER_FILENAME)):
        payload = DoneMarker.from_yaml(marker.read_text(encoding="utf-8"))
        system_dir = marker.parent
        for entry in payload.files:
            resolved = (system_dir / entry.path).resolve()
            digests[str(resolved.relative_to(session_dir.resolve()))] = entry.blake3
    if not digests:
        raise ValueError(
            f"{session_dir} has no DONE marker entries; there is no reference "
            "digest to verify against, and 'nothing to check' must never read "
            "as 'verified'"
        )
    return digests


def iter_reconstruct(store_path: Path, relative_path: str) -> Iterator[bytes]:
    """The original file's exact bytes, rebuilt from the artifact one stored
    chunk at a time.

    Chunked because rehydration writes files of ~166 GB (a two-hour
    Neuropixels 1.0 AP stream: 385 channels x 30 kHz x 7,200 s x 2 bytes)
    and reclamation's proof hashes them, so holding one whole in memory is
    not an option (2026-09-26 rehydration design, section 6). A `streams`
    array yields one chunk of rows, every channel, at a time; a `verbatim`
    array yields one chunk along its only axis.

    Checked against `streams` first, `verbatim` second: a compressed stream
    and its verbatim counterpart never coexist for the same source path
    (`store.write_store` puts every bulk-stream path in exactly one of the
    two groups), so the order only matters as a lookup cost, not a
    correctness choice.

    A generator, so it raises lazily: a missing path's `KeyError` surfaces on
    the first `next()`, not at the call. `verify_against` iterates inside its
    own `try`, which is where that matters.
    """
    root = zarr.open(str(store_path), mode="r")
    arrays = root[ARRAY_GROUP]
    for name in arrays.array_keys():
        array = arrays[name]
        if array.attrs.get("source") == relative_path:
            rows = array.chunks[0]
            for start in range(0, array.shape[0], rows):
                yield array[start : start + rows].astype(SAMPLE_DTYPE).tobytes()
            return
    array = root[VERBATIM_GROUP][relative_path]
    step = array.chunks[0]
    for start in range(0, array.shape[0], step):
        yield array[start : start + step].tobytes()


def reconstruct(store_path: Path, relative_path: str) -> bytes:
    """The whole rebuilt file, in memory. For tests and small files: every
    production caller streams through `iter_reconstruct` instead."""
    return b"".join(iter_reconstruct(store_path, relative_path))


def hash_reconstruction(store_path: Path, relative_path: str) -> str:
    """blake3 of the rebuilt file, fed one chunk at a time."""
    digest = _blake3.blake3()
    for block in iter_reconstruct(store_path, relative_path):
        digest.update(block)
    return digest.hexdigest()


def _verbatim_arrays(store_path: Path) -> list[tuple[str, zarr.Array]]:
    """`(path within the verbatim group, array)` for every verbatim file.

    `visititems`, not `arrays(recurse=True)`: in zarr 2.18 the latter yields
    each array's bare name ('DONE'), dropping the directories that tell three
    systems' `DONE` markers apart -- confirmed against this venv's zarr
    before relying on it."""
    root = zarr.open(str(store_path), mode="r")
    found: list[tuple[str, zarr.Array]] = []
    root[VERBATIM_GROUP].visititems(
        lambda name, obj: found.append((name, obj)) if isinstance(obj, zarr.Array) else None
    )
    return found


def stored_paths(store_path: Path) -> list[str]:
    """Every original relative path the artifact holds, sorted: each `streams`
    array's `source` attribute, and each `verbatim` array's path within that
    group. Rehydration writes exactly these files and no others."""
    root = zarr.open(str(store_path), mode="r")
    streams = [root[ARRAY_GROUP][name].attrs["source"] for name in root[ARRAY_GROUP].array_keys()]
    return sorted(streams + [name for name, _ in _verbatim_arrays(store_path)])


def stored_size(store_path: Path) -> int:
    """Bytes the rebuilt files will occupy, from array shapes alone -- nothing
    is decompressed. Stream arrays are sized as `SAMPLE_DTYPE`, because that
    is what `iter_reconstruct` casts them to."""
    root = zarr.open(str(store_path), mode="r")
    streams = sum(
        math.prod(root[ARRAY_GROUP][name].shape) * SAMPLE_DTYPE.itemsize
        for name in root[ARRAY_GROUP].array_keys()
    )
    return streams + sum(array.shape[0] for _, array in _verbatim_arrays(store_path))


def verify_against(store_path: Path, expected: dict[str, str]) -> list[FileVerdict]:
    """One verdict per `(relative path -> expected blake3)` entry.

    A hash mismatch is reported, not raised: it is a fact about one file, and
    a caller comparing many files wants the whole report rather than the
    first failure. The one thing this function does raise on is
    `_expected_digests` finding no reference digests at all -- see that
    function's docstring for why that case is fatal rather than an empty
    report.

    Hashing is incremental -- `hash_reconstruction` feeds
    `iter_reconstruct`'s chunks to one `blake3` object -- which is not a
    second definition of this project's `blake3` field, only a chunked way of
    computing the one BLAKE3 defines: confirmed empirically (10,000,003
    pseudorandom bytes, deliberately not a multiple of `blake3_file`'s 4 MiB
    chunk size) that a single-shot hash and a chunked-`update()` hash of
    identical bytes agree. `blake3_file` itself is not called, because it
    takes a `Path` on disk and a reconstruction exists only as chunks in
    flight -- the whole point of comparing bytes rather than re-deriving a
    digest that was itself computed from a file.

    `iter_reconstruct` is allowed to raise, and this catches it -- broadly,
    deliberately, and the breadth is a decision made here, not a default left
    unexamined. Confirmed empirically against this exact zarr layout, in
    three separate checks: `test_a_corrupted_artifact_fails_verification`
    zeroes whatever file sorts first under `streams/`, which is
    `streams/.zgroup` -- a plain directory listing puts a group's own
    metadata ahead of any array's `.zarray`, `.zattrs`, or chunk files --
    and that raises `json.JSONDecodeError` (itself a `ValueError`) while
    zarr tries to parse it back. Corrupting an array chunk's compressed
    bytes directly, checked separately from that test, raises `RuntimeError`
    from blosc ("error during blosc decompression"). A path the DONE marker
    names but the store never received raises `KeyError`, also checked
    separately.

    Narrowing to exactly those three types was considered and rejected.
    `ingest/verify.py` narrows its own per-file catch to specific types for
    a specific reason stated there; the reason does not transfer here as
    cleanly as it looks. A fourth, unanticipated kind of damage raising a
    fourth exception type is exactly the case a narrow `except` would crash
    this whole function on: an uncaught exception anywhere in this loop
    means `verify_against` never reaches `return verdicts` at all, so one
    file's corruption loses every verdict, including the ones already found
    to match -- worse than the thing this function exists to avoid. And from
    the caller's side, `reconstruct` raising and `reconstruct` returning the
    wrong bytes resolve to the same action: do not trust this artifact. A
    `FileVerdict` does not need to tell those two apart to be useful; it
    needs to never claim `matched=True` when it cannot back that claim, and
    never crash instead of reporting.

    The usual risk of `except Exception` -- silently hiding a real bug in
    this module's own code behind a plausible-looking "corrupted store"
    verdict -- is bounded two ways rather than left open. `reconstruct`
    itself still raises directly, uncaught, so a suspicious verdict can be
    re-run outside this loop for a full traceback. And a `reconstruct` bug
    that broke good, uncorrupted data -- not just corrupted data -- would
    already fail `test_every_file_reconstructs_to_its_original_bytes` and
    its siblings loudly, in CI, rather than pass here disguised as a
    corruption finding.

    A verdict of `matched=False` with the exception recorded in `actual` is
    strictly more useful than a traceback, and no less honest -- and that
    claim is no longer just prose: `actual` carries the literal substring
    `"error reconstructing file"` on this path, and
    `test_a_corrupted_artifacts_verdict_explains_why` pins it, so a future
    edit that reworded or dropped it fails a test rather than passing
    silently.
    """
    verdicts = []
    for relative_path, digest in sorted(expected.items()):
        try:
            actual = hash_reconstruction(store_path, relative_path)
        except Exception as exc:
            actual = f"error reconstructing file: {type(exc).__name__}: {exc}"
        verdicts.append(FileVerdict(relative_path, digest, actual, actual == digest))
    return verdicts


def verify_store(store_path: Path, session_dir: Path) -> list[FileVerdict]:
    """One verdict per file the session's DONE markers name.

    `verify_against` with the markers on scratch as the reference, which at
    archive time is exactly what they are: the rig's own digests. Reclamation's
    proof and rehydration call `verify_against` directly, with the same digests
    read back from `ArchiveVerification`, because by then there may be no
    scratch copy to read markers from (2026-09-26 rehydration design, section
    6). Raises `ValueError` when the markers name nothing -- see
    `_expected_digests` for why that is fatal rather than an empty report.
    """
    return verify_against(store_path, _expected_digests(session_dir))
