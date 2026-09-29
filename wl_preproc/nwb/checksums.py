"""Checksums of the file's written-once datasets (parent spec section 8.2,
for wl.works Plan 24 section 3.3; design spec
`2026-09-28-nwb-builder-design.md` section 7), as `sha256`: wl.works' Plan 24
settles the algorithm (design spec `2026-09-29-nwb-publishing-design.md`
section 7).

**Each dataset's DECODED contents, never a group's**: `colnames` is an
attribute that changes when a column is appended, so a group checksum
breaks on exactly the accretion it must tolerate. **A ragged column is a
pair**: `x` and `x_index` are recorded together, so a change cannot hide in
the half left unnamed. Not hashed: `/specifications` (the schema the file
carries, not data) and `/file_create_date` (when it was written)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import h5py
import numpy as np

_SKIPPED = ("/specifications", "/file_create_date")


def _content_bytes(dataset: h5py.Dataset) -> bytes:
    """The decoded contents, as bytes that do not depend on HDF5's layout,
    chunking or compression."""
    values = dataset[()]
    if dataset.dtype.kind == "O" and h5py.check_string_dtype(dataset.dtype) is None:
        # Object references: hash the paths they point at.
        flat = np.asarray(values).ravel()
        return "\x00".join(dataset.file[ref].name if ref else "" for ref in flat).encode()
    if h5py.check_string_dtype(dataset.dtype) is not None or dataset.dtype.kind in "SO":
        flat = np.asarray(values, dtype=object).ravel()
        return b"\x00".join(v if isinstance(v, bytes) else str(v).encode() for v in flat)
    return np.ascontiguousarray(values).tobytes()


def dataset_checksums(path: Path) -> list[dict]:
    """One row per dataset: `dataset_path`, `dtype`, `shape`, `sha256` and
    `paired_with` (a ragged column's other half, '' otherwise)."""
    rows = []
    with h5py.File(path, "r") as handle:
        names = []
        handle.visititems(lambda name, obj: names.append("/" + name) if isinstance(obj, h5py.Dataset) else None)
        present = set(names)
        for name in sorted(names):
            if name.startswith(_SKIPPED):
                continue
            dataset = handle[name]
            if name.endswith("_index"):
                paired = name[: -len("_index")]
            elif name + "_index" in present:
                paired = name + "_index"
            else:
                paired = ""
            rows.append({
                "dataset_path": name,
                "dtype": str(dataset.dtype),
                "shape": str(tuple(dataset.shape)),
                "sha256": hashlib.sha256(_content_bytes(dataset)).hexdigest(),
                "paired_with": paired if paired in present else "",
            })
    return rows
