# wl_preproc/ephys/geometry.py
"""Electrode geometry by probe part number, from probeinterface's offline table.

Phase 2a design spec section 4. Four reasons this rather than a port of
`element-array-ephys`'s `probe_geometry.py`:

1. This repository already names ProbeInterface the authority --
   `wl_preproc/synth/spikeglx.py:100`.
2. It is already installed, transitively, via the spikeinterface format oracle.
3. It covers the NHP variants offline -- NP1015, NP1022, NP1030, NP1032.
4. Upstream's map has no NP1000 at all, and its registration helper creates
   only five types, none of them NHP -- the wrong five for this lab.

`build_neuropixels_probe` reads a bundled JSON table. `probeinterface.get_probe`
is an ONLINE lookup and is deliberately not imported here.

Column and row indices are derived from the distinct coordinate values **within
a shank**, not from a hardcoded pitch. A pitch constant would be a second
definition of something the coordinates already state, and it would be wrong for
the staggered layouts: NP1030's four distinct x values are 0, 16, 87 and 103 um,
which is not one pitch.
"""

from __future__ import annotations

from probeinterface.neuropixels_tools import build_neuropixels_probe


class UnknownProbeType(ValueError):
    """A part number probeinterface's offline table does not carry.

    Raised rather than returning an empty list: a `ProbeType` declared with no
    electrodes would let every downstream foreign key resolve against an empty
    set, and nothing would report a problem.
    """


def electrode_rows(part_number: str) -> list[dict]:
    """Every electrode of `part_number`, as rows ready for `ProbeType.Electrode`.

    Returns dicts with `electrode`, `shank`, `shank_col`, `shank_row`,
    `x_coord` and `y_coord`. Coordinates are micrometres in probeinterface's
    own frame, whose origin is the centre of the bottom-most electrode row.
    """
    try:
        probe = build_neuropixels_probe(part_number)
    except KeyError as exc:
        # Measured directly: `build_neuropixels_probe` raises `KeyError` for
        # both an unknown part number and an empty one -- it looks the part
        # number up in a plain dict of its offline table. Narrowed from a
        # bare `except Exception`, which would relabel ANY future
        # probeinterface bug (a TypeError from a malformed offline JSON row,
        # an AttributeError from an API change) as "unknown probe type" --
        # exactly the one diagnosis that sends a reader to the wrong file.
        raise UnknownProbeType(
            f"{part_number!r} is not in probeinterface's offline Neuropixels "
            f"table. Adding a probe type means adding it there, or upgrading "
            f"the pin -- not widening this function to guess a layout."
        ) from exc

    positions = probe.contact_positions
    shank_ids = probe.shank_ids

    def shank_of(i: int) -> int:
        # None for single-shank probes; an array of strings when present.
        if shank_ids is None:
            return 0
        raw = shank_ids[i]
        return int(raw) if str(raw) != "" else 0

    shanks = [shank_of(i) for i in range(len(positions))]

    # Distinct coordinates per shank, so a 4-shank probe's columns are numbered
    # 0..1 within each shank rather than 0..7 across the probe.
    axes: dict[int, tuple[list[float], list[float]]] = {}
    for shank in set(shanks):
        xs = sorted({float(positions[i][0]) for i in range(len(positions)) if shanks[i] == shank})
        ys = sorted({float(positions[i][1]) for i in range(len(positions)) if shanks[i] == shank})
        axes[shank] = (xs, ys)

    rows = []
    for i, (x, y) in enumerate(positions):
        shank = shanks[i]
        xs, ys = axes[shank]
        rows.append(
            {
                "electrode": i,
                "shank": shank,
                "shank_col": xs.index(float(x)),
                "shank_row": ys.index(float(y)),
                "x_coord": float(x),
                "y_coord": float(y),
            }
        )
    return rows
