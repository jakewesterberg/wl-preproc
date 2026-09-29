"""How every dataset in the file is stored (design spec
`2026-09-28-nwb-builder-design.md` section 6).

**Readable over HTTP range requests** (parent spec section 8.1.2): each
dataset is compressed on its own, with gzip, which browser HDF5 readers
support; nothing is compressed whole-file. Continuous series are chunked
time-major, about `CHUNK_BYTES` a chunk, so a window of time across both
coordinates touches few chunks."""

from __future__ import annotations

import numpy as np
from hdmf.backends.hdf5 import H5DataIO
from hdmf.common import VectorData

CHUNK_BYTES = 256 * 1024


def continuous(data: np.ndarray) -> H5DataIO:
    """A continuous series, chunked time-major at about `CHUNK_BYTES`."""
    data = np.asarray(data)
    row_bytes = data.dtype.itemsize * int(np.prod(data.shape[1:], dtype=int))
    rows = max(1, min(data.shape[0], CHUNK_BYTES // max(row_bytes, 1)))
    return H5DataIO(data, compression="gzip", chunks=(rows, *data.shape[1:]))


def compressed(data):
    """A table column or any other dataset: gzip, default chunking. An empty
    one is stored plain: HDF5 cannot chunk a zero-length dataset."""
    values = np.asarray(data)
    if values.size == 0:
        return values
    return H5DataIO(values, compression="gzip")


def column(name: str, description: str, data) -> VectorData:
    """One table column, compressed. Strings are stored as text; a missing
    float is NaN and a missing integer is -1, as each column's description
    says."""
    values = np.asarray(data)
    if values.dtype.kind in "US":
        values = values.astype(object)
    return VectorData(name=name, description=description, data=compressed(values))
