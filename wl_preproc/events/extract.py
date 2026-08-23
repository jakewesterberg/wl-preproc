# wl_preproc/events/extract.py
"""Per-system extraction of the event-code stream.

This module is the ONLY per-system code in Phase 1c-5, exactly as
`timebase/extract.py` is the only per-system code in 1c-4. Everything
downstream -- decode, trial assembly, agreement, tier -- is shared, because
every system carries the same protocol (design spec section 4.2).

**The three systems do not carry the same thing, and the return types say so.**
The sync box and the NI carry 16 data lines plus a strobe and yield WORDS. The
Intan RHS carries the strobe ONLY -- its 16 digital inputs cannot fit 16 data
lines plus strobe plus barcode -- and yields a WITNESS: a count and its timing,
never content. Returning empty words for the RHS would make a correct
strobe-only recording indistinguishable from a decode failure.

Native time throughout, never session time. Converting is the fit's job
(`timebase/fit.py`), and keeping them apart is what makes the transform
reversible as spec section 4.5 requires.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wl_sync.log import CodeWord, read_log


@dataclass(frozen=True, slots=True)
class WordStream:
    """16-bit words with their native timestamps, from a full-code recorder."""

    words: tuple[tuple[int, int], ...]
    fs_hz: float


@dataclass(frozen=True, slots=True)
class StrobeWitness:
    """Strobe edges with no word content, from a strobe-only recorder.

    A distinct type from `WordStream` on purpose: spec section 4.7's tier B is
    "1 full-code record + >=1 independent strobe witness", so a witness is a
    real contribution rather than a degraded word stream, and the type system
    should not let one be mistaken for the other.
    """

    edge_samples: tuple[int, ...]
    fs_hz: float

    @property
    def n_edges(self) -> int:
        return len(self.edge_samples)


# The sync box logs at microsecond resolution and is not a sampled line at all;
# 1 MHz is nominal, matching `timebase/extract.py`'s own treatment of it.
_SYNCBOX_NOMINAL_FS_HZ = 1_000_000.0


def extract_syncbox_words(path: Path) -> WordStream:
    """Words from the sync box log.

    The Pi decodes the 16 data lines itself and writes a `CodeWord` record, so
    there is nothing to decode here -- this is a read. `Edge` records in the
    same log are the barcode and are not words; letting one through would be a
    code nobody sent.
    """
    _header, records = read_log(path)
    words = tuple(
        (record.tick_us, record.word) for record in records if isinstance(record, CodeWord)
    )
    return WordStream(words=words, fs_hz=_SYNCBOX_NOMINAL_FS_HZ)
