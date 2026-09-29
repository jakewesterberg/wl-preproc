"""Writing the file so a half-written one never carries the final name
(parent spec section 11.5; design spec `2026-09-28-nwb-builder-design.md`
section 6)."""

from __future__ import annotations

import os
import warnings
from pathlib import Path

from pynwb import NWBHDF5IO, NWBFile


def write_atomically(nwb: NWBFile, path: Path) -> None:
    """Write to `{path}.partial`, then rename over `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    if partial.exists():
        partial.unlink()
    try:
        with warnings.catch_warnings():
            # pynwb asks for a `.nwb` extension; `.partial` is the point.
            warnings.filterwarnings("ignore", message="The file path provided: .* does not end in '.nwb'")
            with NWBHDF5IO(str(partial), "w") as io:
                io.write(nwb)
        os.replace(partial, path)
    finally:
        if partial.exists():
            partial.unlink()
