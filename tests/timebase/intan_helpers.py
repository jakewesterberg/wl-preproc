"""Helpers for tests that read an Intan recording's strobe line."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from wl_preproc.events.extract import RHS_STROBE_DIGITAL_BIT
from wl_preproc.timebase.extract import RHS_DIGITAL_IN_FILENAME


def _clear_intan_strobes(recording_dir: Path) -> None:
    """Zero the strobe bit of every word of `recording_dir`'s `digitalin.dat`."""
    path = recording_dir / RHS_DIGITAL_IN_FILENAME
    words = np.fromfile(path, dtype=np.uint16)
    words &= np.uint16(~(1 << RHS_STROBE_DIGITAL_BIT) & 0xFFFF)
    words.tofile(path)
