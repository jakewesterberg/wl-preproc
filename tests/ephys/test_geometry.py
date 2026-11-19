"""Geometry comes from probeinterface's offline tables, and is checked against
published probe specifications rather than against probeinterface itself.

A test that compares a library to itself proves only that it is deterministic.
The counts below are from IMEC's published specifications: NP 1.0 has 960
electrodes; NP 1.0 NHP Long (NP1030) has 4,416 in 2 occupied columns at 20 um
row pitch spanning ~44 mm, which is the figure wl-trajectortree unit 3 section
6.4 also records; NP 2.0 MS (NP2010) has 5,120 across 4 shanks.
"""

from __future__ import annotations

import pytest

from wl_preproc.ephys import geometry


def test_np1000_matches_the_published_electrode_count():
    rows = geometry.electrode_rows("NP1000")
    assert len(rows) == 960


def test_np1030_nhp_long_spans_about_44_mm_in_two_occupied_columns():
    rows = geometry.electrode_rows("NP1030")
    assert len(rows) == 4416
    assert max(r["y_coord"] for r in rows) == pytest.approx(44140.0)
    # 4416 sites over 2208 distinct rows is two occupied contacts per row --
    # the staggered layout, not a dense 4-wide grid.
    assert len({r["shank_row"] for r in rows}) == 2208


@pytest.mark.parametrize(
    "part_number, electrode_count",
    [
        # Every NHP variant `wl_preproc/ephys/geometry.py`'s own module
        # docstring names as a reason to vendor probeinterface's offline
        # table rather than port element-array-ephys's map -- until this
        # test, only NP1030 (above) was actually pinned, so the docstring's
        # claim about the other three was asserted only in prose. Counts
        # measured directly against the installed probeinterface==0.3.2
        # before being asserted here, not copied from the docstring's own
        # unverified claim.
        ("NP1015", 960),
        ("NP1022", 2496),
        ("NP1030", 4416),
        ("NP1032", 4416),
    ],
)
def test_nhp_variant_electrode_counts_match_the_installed_library(part_number, electrode_count):
    rows = geometry.electrode_rows(part_number)
    assert len(rows) == electrode_count


def test_multi_shank_columns_are_numbered_within_a_shank_not_across_the_probe():
    """NP2010 has 4 shanks with 2 columns each. A column index computed over
    the whole probe would run 0..7 and make shank 3's electrodes look like
    columns 6 and 7 of one wide shank."""
    rows = geometry.electrode_rows("NP2010")
    assert len(rows) == 5120
    assert {r["shank"] for r in rows} == {0, 1, 2, 3}
    assert {r["shank_col"] for r in rows} == {0, 1}


def test_single_shank_probes_report_shank_zero():
    """probeinterface returns shank_ids=None for single-shank probes, which
    must become 0 rather than propagating None into a `tinyint` column."""
    rows = geometry.electrode_rows("NP1000")
    assert {r["shank"] for r in rows} == {0}


def test_electrode_numbers_are_dense_and_zero_based():
    rows = geometry.electrode_rows("NP1000")
    assert sorted(r["electrode"] for r in rows) == list(range(960))


def test_an_unknown_part_number_raises_rather_than_returning_nothing():
    """Section 4: a probe type absent from the offline table must fail loudly.
    Returning [] would declare a ProbeType with no electrodes, and every
    downstream foreign key would then resolve against an empty set."""
    with pytest.raises(geometry.UnknownProbeType, match="NP9999"):
        geometry.electrode_rows("NP9999")


def test_geometry_makes_no_network_call(monkeypatch):
    """Section 8: no network call in the geometry path. probeinterface's
    get_probe() fetches from its library repository over HTTP; this path must
    not reach it.

    Patches the names probeinterface's `library` module actually calls, not
    `urllib.request.urlopen`: `probeinterface/library.py` does `from
    urllib.request import urlopen` at import time, so that module holds its
    own reference to the original function in its own namespace, and
    patching `urllib.request.urlopen` afterward never reaches it -- monkey-
    patching the origin does not affect a name already bound by a `from ...
    import` elsewhere. Confirmed directly: before this fix, this test kept
    passing even with a live `get_probe()` call planted inside
    `electrode_rows`, which is a guard with no teeth at all. `requests.get`
    is the library's other HTTP path (its tag/manifest-listing helpers), and
    is patched too, guarded so this test still runs if `requests` is not
    importable.
    """
    import probeinterface.library

    def explode(*args, **kwargs):
        raise AssertionError("geometry attempted a network call")

    monkeypatch.setattr(probeinterface.library, "urlopen", explode)
    try:
        import requests
    except ImportError:
        pass
    else:
        monkeypatch.setattr(requests, "get", explode)

    assert len(geometry.electrode_rows("NP1000")) == 960


# (electrode, x um, y um), from probeinterface's offline table: identical under
# 0.3.2 and 0.5.0 for every part number both hold (measured 2026-11-19, the
# SpikeGLX header piece's review). probeinterface has no ceiling (wl.yaml), and
# `register_probe_type` keeps the rows a probe type was first registered with,
# so a release that renumbers or moves contacts must fail here before it can
# store a site under one numbering and place it under another.
_PINNED_SITES = {
    "NP1000": [(0, 16.0, 0.0), (1, 48.0, 0.0), (2, 0.0, 20.0), (3, 32.0, 20.0), (383, 32.0, 3820.0),
               (384, 16.0, 3840.0), (959, 32.0, 9580.0)],
    "NP1032": [(0, 0.0, 0.0), (1, 103.0, 0.0), (2, 0.0, 20.0), (3, 103.0, 20.0), (767, 103.0, 7660.0),
               (768, 0.0, 7680.0), (4415, 103.0, 44140.0)],
}


@pytest.mark.parametrize("part_number", sorted(_PINNED_SITES))
def test_an_electrode_keeps_its_number_and_place_across_probeinterface_releases(part_number):
    rows = geometry.electrode_rows(part_number)
    assert [(e, rows[e]["electrode"], rows[e]["x_coord"], rows[e]["y_coord"]) for e, _x, _y in _PINNED_SITES[part_number]] \
        == [(e, e, x, y) for e, x, y in _PINNED_SITES[part_number]]
