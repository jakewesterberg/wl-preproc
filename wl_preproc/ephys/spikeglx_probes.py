"""Which probes a SpikeGLX run recorded, read from the run's own `.meta` files
(design spec `2026-09-30-nwb-probes-design.md` section 2.1).

**The recording names the probe** (the requester's decision 2): its serial
(`imDatPrb_sn`), its part number (`imDatPrb_pn`), and the sites its channels
recorded (`~imroTbl`). wl.works' report of an insertion joins onto the serial.

**One run is one segment.** SpikeGLX stops every imec stream and the NI stream
together (parent spec section 4.1), and the segment is the NI stream's
`.nidq.bin`. A run's probes are the `.ap.meta` files beside it named for the
same run, flat (`<run>_imec<N>.ap.meta`, as the synthetic generator writes) or
in SpikeGLX's own per-probe folders (`<run>_g0_imec<N>/<run>_g0_t0.imec<N>.ap.meta`).
The rig's real layout has not been seen yet, so both are read.

**The sites come from `probeinterface.read_spikeglx`**, a runtime dependency
(`pyproject.toml`), which maps an imroTbl onto electrodes for every Neuropixels
format it knows. Each contact is then numbered by its place in the full probe,
`ephys/geometry.py::electrode_rows`'s numbering, so a recorded site and
`ProbeType.Electrode` name the same electrode.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

_STREAM = re.compile(r"imec(\d+)\.ap\.meta$")


@dataclasses.dataclass(frozen=True)
class RecordedProbe:
    stream: str  # "imec0"
    serial: str | None
    part_number: str | None
    electrodes: tuple[int, ...] | None  # None when the sites cannot be mapped
    problem: str | None  # why something is missing, or None


def _metas(nidq_bin: Path) -> list[Path]:
    stem = nidq_bin.name.removesuffix(".nidq.bin")
    run = nidq_bin.parent
    pattern = re.compile(rf"^{re.escape(stem)}[._]imec(\d+)\.ap\.meta$")
    candidates = [*run.iterdir(), *(p for d in run.iterdir() if d.is_dir() for p in d.iterdir())]
    found = [path for path in candidates if path.is_file() and pattern.match(path.name)]
    return sorted(found, key=lambda path: int(pattern.match(path.name).group(1)))


def _fields(meta: Path) -> dict[str, str]:
    fields = {}
    for line in meta.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep:
            fields[name] = value
    return fields


def read_probe(meta: Path) -> RecordedProbe:
    """One imec stream's probe, from its `.meta`.

    Raises on nothing the file contains: what cannot be read is named in
    `problem` instead. The census records a segment once, and a landed file
    does not change, so a probe that raised would fail its segment on every
    pass and record nothing."""
    from probeinterface import read_spikeglx
    from probeinterface.neuropixels_tools import build_neuropixels_probe

    stream = f"imec{_STREAM.search(meta.name).group(1)}"
    try:
        fields = _fields(meta)
    except (OSError, UnicodeDecodeError) as exc:
        return RecordedProbe(stream=stream, serial=None, part_number=None, electrodes=None,
                             problem=f"the .meta could not be read: {exc}")
    serial = fields.get("imDatPrb_sn") or None
    part_number = fields.get("imDatPrb_pn") or None
    problems = [] if serial else ["the .meta names no serial (imDatPrb_sn)"]
    electrodes = None
    if part_number is None:
        problems.append("the .meta names no part number (imDatPrb_pn)")
    else:
        try:
            full = build_neuropixels_probe(part_number)
        except KeyError:
            problems.append(f"part number {part_number} is not in probeinterface's offline table, so its sites "
                            "cannot be placed")
        else:
            index = {contact: i for i, contact in enumerate(full.contact_ids)}
            # probeinterface asserts on a missing imroTbl and raises
            # ValueError on a malformed one (measured, 0.3.2).
            try:
                electrodes = tuple(sorted(index[contact] for contact in read_spikeglx(meta).contact_ids))
            except (AssertionError, KeyError, ValueError) as exc:
                problems.append(f"the imroTbl could not be mapped onto {part_number}'s sites: {exc}")
    return RecordedProbe(stream=stream, serial=serial, part_number=part_number, electrodes=electrodes,
                         problem="; ".join(problems) or None)


def read_run(nidq_bin: Path) -> list[RecordedProbe]:
    """Every probe the run whose NI stream is `nidq_bin` recorded, by stream."""
    return [read_probe(meta) for meta in _metas(Path(nidq_bin))]
