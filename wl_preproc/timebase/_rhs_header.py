"""The Intan RHS header's one reader, for the single field extraction needs.

`info.rhs` is a sequential binary format with no field tags, so reading any
field means knowing the exact byte offset of everything before it. That is a
lot of format knowledge for one number -- but the alternative is worse: the
sample rate is what converts native samples into native microseconds, so a
rate assumed rather than read is a fit wrong by exactly the ratio nobody
checked, and the RHS controller's clock is independent of every other system's
by construction (`wl_preproc/synth/rhs.py` records why at length).

Only the two fields that precede any variable-length data are read, which is
why this stays small: the magic number identifies the format, and the sample
rate sits at a fixed offset immediately after the version pair. Everything
after the first QString is variable-length and would require a real parser.
`spikeinterface`/`neo` is that parser, and the synth tests use it as the
format oracle -- but it is a **dev-only** dependency (`pyproject.toml`), so
nothing under `wl_preproc/` may import it.

Kept in its own module, separate from `extract.py`, so the header's shape has
exactly one reader -- the same reason `_syncbox_log.py` exists.
"""

from __future__ import annotations

import struct
from pathlib import Path

# Intan's own format identifier, and the vendor document's, not this project's.
# It is restated rather than imported from `wl_preproc.synth.rhs_header` for
# the reason `_syncbox_log.py` restates its gpio key: a production reader has
# no business depending on the synthetic fixture generator. The direction of
# the check matters too -- January's real files are written by Intan, not by
# that emitter, so agreeing with the emitter is not the property under test.
_MAGIC = 0xD69127AC

# uint32 magic, then two int16 version fields, then the float32 sample rate.
_HEADER_PREFIX = "<Ihhf"
_HEADER_PREFIX_BYTES = struct.calcsize(_HEADER_PREFIX)

INFO_FILENAME = "info.rhs"


def read_sample_rate_hz(path: Path) -> float:
    """The controller's sampling rate, in Hz, out of an `info.rhs`.

    The magic number is checked first and the failure is loud: without it,
    handing this function any other file yields whatever four bytes happen to
    sit at offset 8, reinterpreted as a float32. That is not a rate that looks
    wrong -- it is a rate that looks like 3.6e-38, or like 30000.4, and only
    one of those two gets noticed.
    """
    with path.open("rb") as handle:
        prefix = handle.read(_HEADER_PREFIX_BYTES)
    if len(prefix) < _HEADER_PREFIX_BYTES:
        raise ValueError(
            f"{path}: {len(prefix)} bytes is shorter than the "
            f"{_HEADER_PREFIX_BYTES}-byte RHS header prefix; the file is "
            "truncated or is not an Intan RHS header at all"
        )
    magic, _major, _minor, sample_rate_hz = struct.unpack(_HEADER_PREFIX, prefix)
    if magic != _MAGIC:
        raise ValueError(
            f"{path}: magic number 0x{magic:08X} is not the Intan RHS "
            f"0x{_MAGIC:08X}; this is not an RHS header"
        )
    if not sample_rate_hz > 0.0:
        raise ValueError(
            f"{path}: declares a sample rate of {sample_rate_hz} Hz, which "
            "cannot time anything"
        )
    # float32 out of the file, float64 here: the widening is exact, and it
    # keeps every downstream fit in one float width.
    return float(sample_rate_hz)


def find_recording_dir(system_dir: Path) -> Path:
    """The directory holding one RHS recording's files, under a system dir.

    Intan's "One File Per Signal Type" layout puts `info.rhs` beside the `.dat`
    files in a directory the controller names itself, and the transfer drops
    that whole directory under `<session>/rhs/` -- so the files sit one level
    deeper than the caller's path, not at it. Both shapes are accepted, because
    which one January produces depends on how the transfer is configured and
    guessing costs a silent `FileNotFoundError` at extraction time.
    """
    if (system_dir / INFO_FILENAME).is_file():
        return system_dir
    candidates = sorted(
        child for child in system_dir.iterdir()
        if child.is_dir() and (child / INFO_FILENAME).is_file()
    )
    if not candidates:
        raise FileNotFoundError(
            f"{system_dir}: no {INFO_FILENAME} here or in any immediate "
            "subdirectory, so there is no RHS recording to read"
        )
    if len(candidates) > 1:
        # One acquisition run per Segment (parent spec section 8.3.1's
        # `system` keying). Two recordings under one system directory is a
        # segmentation question, and answering it by picking the first
        # alphabetically would silently drop the other one's barcodes from
        # every fit that follows.
        raise ValueError(
            f"{system_dir}: {len(candidates)} RHS recordings "
            f"({', '.join(c.name for c in candidates)}); extraction reads one "
            "recording, so which of these is the segment must be decided by "
            "the caller rather than guessed here"
        )
    return candidates[0]
