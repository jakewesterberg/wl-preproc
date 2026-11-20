"""An Intan recording placed on the NI's clock by the strobes (part 3b-2,
amendment 49): the reading layer.

Both sides' strobe rising edges are read, the NI's from its strobe bit and
Intan's from bit 1 of `digitalin.dat`; `strobes.match_strobes` pairs them by
their pattern; the pairs come back as `(session_s, device_s)`, the NI's seconds
from its first sample and Intan's from its own. The fit is the caller's.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from wl_preproc.timebase.strobes import Unanchored, match_strobes

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntanPlacement:
    """`pairs` are `(session_s, device_s)`, one per matched strobe, in Intan's
    order; `first_sample` is the native Intan index of the first pair. The two
    fractions are the anchor's verified best and runner-up."""

    pairs: tuple[tuple[float, float], ...]
    first_sample: int
    anchor_fraction: float
    runner_up_fraction: float


@lru_cache(maxsize=8)
def _read_edges(path: str, size: int, mtime_ns: int, kind: str) -> np.ndarray:
    """A file's strobe rising edges as sample indices. `size` and `mtime_ns` are
    in the key only so that a changed file is read again. The sample rate is not
    cached: it lives in the sidecar, which the key does not cover, so each call
    reads it afresh (`_rate`)."""
    if kind == "ni":
        from wl_preproc.events.extract import extract_nidq_strobe_rises

        samples = extract_nidq_strobe_rises(Path(path)).edge_samples
    elif kind == "intan":
        from wl_preproc.events.extract import RHS_STROBE_DIGITAL_BIT
        from wl_preproc.timebase.channels import column_chunks, strobe_rises

        samples = strobe_rises(column_chunks(Path(path), 1, 0, np.uint16), RHS_STROBE_DIGITAL_BIT)
    else:
        raise ValueError(f"unknown strobe file kind {kind!r}")
    edges = np.asarray(samples, dtype=np.int64)
    edges.flags.writeable = False
    return edges


def _edges(path: Path, kind: str) -> np.ndarray:
    stat = path.stat()
    return _read_edges(str(path), stat.st_size, stat.st_mtime_ns, kind)


def _rate(path: Path, kind: str) -> float:
    """The sample rate from the file's sidecar, read on every call."""
    if kind == "ni":
        from wl_preproc.timebase._nidq_meta import read_nidq_meta

        return float(read_nidq_meta(path.with_suffix(".meta")).sample_rate_hz)
    from wl_preproc.timebase._rhs_header import INFO_FILENAME, read_sample_rate_hz

    return read_sample_rate_hz(path.parent / INFO_FILENAME)


def place_intan(session_dir: Path, recording_dir: Path) -> IntanPlacement:
    """Place one Intan recording on the session's NI clock.

    The NI file is the session's first (`clock.nidq_files`), the one on session
    time. Raises `Unanchored`, after logging its sentence at INFO and naming the
    recording.
    """
    from wl_preproc.timebase.clock import nidq_files
    from wl_preproc.timebase.extract import RHS_DIGITAL_IN_FILENAME

    ni_path = nidq_files(session_dir)[0]
    intan_path = recording_dir / RHS_DIGITAL_IN_FILENAME
    ni_samples, ni_fs = _edges(ni_path, "ni"), _rate(ni_path, "ni")
    intan_samples, intan_fs = _edges(intan_path, "intan"), _rate(intan_path, "intan")
    try:
        match = match_strobes(ni_samples / ni_fs, intan_samples / intan_fs)
    except Unanchored as refusal:
        logger.info("%s: %s", recording_dir, refusal)
        raise
    ni_matched = ni_samples[match.reference_index]
    intan_matched = intan_samples[match.device_index]
    pairs = tuple(
        (float(n) / ni_fs, float(d) / intan_fs) for n, d in zip(ni_matched, intan_matched, strict=True)
    )
    return IntanPlacement(
        pairs=pairs,
        first_sample=int(intan_matched[0]),
        anchor_fraction=match.anchor_fraction,
        runner_up_fraction=match.runner_up_fraction,
    )
