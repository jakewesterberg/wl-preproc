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


def reconstruct(store_path: Path, relative_path: str) -> bytes:
    """The original file's exact bytes, rebuilt from the artifact.

    Checked against `streams` first, `verbatim` second: a compressed stream
    and its verbatim counterpart never coexist for the same source path (
    `store.write_store` puts every bulk-stream path in exactly one of the two
    groups), so the order only matters as a lookup cost, not a correctness
    choice.
    """
    root = zarr.open(str(store_path), mode="r")
    arrays = root[ARRAY_GROUP]
    for name in arrays.array_keys():
        if arrays[name].attrs.get("source") == relative_path:
            return arrays[name][:].astype(SAMPLE_DTYPE).tobytes()
    return bytes(root[VERBATIM_GROUP][relative_path][:])


def verify_store(store_path: Path, session_dir: Path) -> list[FileVerdict]:
    """One verdict per file the DONE markers name.

    A hash mismatch is reported, not raised: it is a fact about one file, and
    a caller comparing many files wants the whole report rather than the
    first failure. The one thing this function does raise on is
    `_expected_digests` finding no reference digests at all -- see that
    function's docstring for why that case is fatal rather than an empty
    report.

    `_blake3.blake3(rebuilt).hexdigest()` -- hashing the whole reconstructed
    file in one call, rather than the chunked `Path.open("rb")` loop
    `contracts.done.blake3_file` uses on a file already on disk -- is not a
    second definition of this project's `blake3` field, only a second way of
    computing the one BLAKE3 defines: confirmed empirically (10,000,003
    pseudorandom bytes, deliberately not a multiple of `blake3_file`'s
    4 MiB chunk size) that a single-shot hash and a chunked-`update()` hash of
    identical bytes agree. `blake3_file` itself is not called here because it
    takes a `Path` on disk, and `reconstruct`'s result exists only in memory --
    the whole point of comparing bytes rather than re-deriving a digest that
    was itself computed from a file.

    `reconstruct` is allowed to raise, and this catches it -- broadly,
    deliberately. A corrupted store does not fail in one tidy way. Confirmed
    empirically against this exact zarr layout, in three separate checks:
    `test_a_corrupted_artifact_fails_verification` zeroes whatever file sorts
    first under `streams/`, which is `streams/.zgroup` -- a plain directory
    listing puts a group's own metadata ahead of any array's `.zarray`,
    `.zattrs`, or chunk files -- and that raises `json.JSONDecodeError`
    (itself a `ValueError`) while zarr tries to parse it back. Corrupting an
    array chunk's compressed bytes directly, checked separately from that
    test, raises `RuntimeError` from blosc ("error during blosc
    decompression"). A path the DONE marker names but the store never
    received raises `KeyError`, also checked separately. Three exception
    types from three checks, not an exhaustive list -- a fourth kind of
    damage finding a fourth exception type next is the expected shape of
    this problem, not a surprise to special-case for. The contract that
    matters is the caller's: nothing reconstructing this artifact ever
    crashes verification instead of reporting it. A verdict of
    `matched=False` with the exception recorded in `actual` is strictly more
    useful than a traceback, and no less honest.
    """
    verdicts = []
    for relative_path, expected in sorted(_expected_digests(session_dir).items()):
        try:
            rebuilt = reconstruct(store_path, relative_path)
            actual = _blake3.blake3(rebuilt).hexdigest()
        except Exception as exc:
            actual = f"error reconstructing file: {type(exc).__name__}: {exc}"
        verdicts.append(
            FileVerdict(relative_path, expected, actual, actual == expected)
        )
    return verdicts
