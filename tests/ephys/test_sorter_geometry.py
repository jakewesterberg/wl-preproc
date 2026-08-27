import pytest

from wl_preproc.ephys.sorter_geometry import MultiShankSpacingUnsupported, kilosort_spacing


@pytest.mark.parametrize(
    "part_number,expected_dminx",
    [("NP1000", 16.0), ("NP1032", 103.0), ("NP1030", 16.0)],
)
def test_spacing_is_the_probes_own_smallest_horizontal_step(part_number, expected_dminx):
    """KS4's dminx is documented in microns as the horizontal spacing of
    template centres, defaulting to 32 because that suits Neuropixels 1 and 2.
    It is a property of the probe, so it is read from the probe."""
    assert kilosort_spacing(part_number)["dminx"] == expected_dminx


def test_max_channel_distance_spans_the_columns():
    """The derived value spans the columns; the 32 um default does not.

    **This test pins arithmetic, not a fault.** It was written believing the
    default prevented any cross-column comparison; measurement on 2026-08-27
    showed the opposite -- 158 of 252 surviving templates bridge the columns at
    the default, against 95 of 189 derived, because `nearest_chans` reaches
    across the gap from the 17.2 and 85.8 um template columns. See design spec
    section 7's 2026-08-27 amendment and this module's own docstring. What the
    assertion below still guarantees is that the derivation reports the probe's
    real column separation rather than a constant."""
    assert kilosort_spacing("NP1032")["max_channel_distance"] >= 103.0
    assert kilosort_spacing("NP1000")["max_channel_distance"] >= 48.0


def test_multi_shank_probe_raises_rather_than_pooling_columns():
    """NP2010 has 4 shanks, 250 um apart. Pooling every shank's x-coordinate
    into one dminx/max_channel_distance -- what a bare `np.unique(x)` across
    the whole probe does -- would tell KS4 to compare channels on separate
    silicon; per-shank derivation is unimplemented, so this raises rather than
    returning that silently wrong answer."""
    with pytest.raises(MultiShankSpacingUnsupported):
        kilosort_spacing("NP2010")


def test_the_32um_default_does_not_isolate_the_columns_as_section_7_claimed():
    """The measurement that falsified design spec section 7, kept as a test.

    Section 7 asserted that at `max_channel_distance=32` a channel in one of
    NP1032's columns is never compared with one in the other, 103 um away. That
    is false, and the sorting demonstration's three-seed tie is a clean null
    because of it rather than an underpowered one.

    Why this is a test and not a note in the spec: the claim is about a third
    party's internals, and `kilosort` is unpinned above nothing -- a future
    version that changes `template_centers()`'s grid, `nearest_chans()`'s
    neighbour count, or the `igood` filter could make section 7's original
    claim true after all. Then this fails, and whoever sees it knows to revisit
    the amendment rather than rediscover the whole argument. It costs
    milliseconds because it places templates and measures distances; it never
    sorts anything.
    """
    pytest.importorskip("kilosort")
    import numpy as np
    from kilosort.spikedetect import nearest_chans, template_centers

    from wl_preproc.ephys.geometry import electrode_rows

    sites = electrode_rows("NP1032")[:64]
    xc = np.array([s["x_coord"] for s in sites], dtype=float)
    yc = np.array([s["y_coord"] for s in sites], dtype=float)

    def templates_spanning_both_columns(dminx: float, max_channel_distance: float):
        ops = {
            "kcoords": np.zeros(len(xc)),
            "xc": xc,
            "yc": yc,
            "settings": {"dmin": None, "dminx": dminx, "nearest_chans": 10},
            "max_channel_distance": max_channel_distance,
        }
        ops = template_centers(ops)
        ys, xs = np.meshgrid(ops["yup"], ops["xup"])
        iC, ds = nearest_chans(ys.flatten(), yc, xs.flatten(), xc, 10, device="cpu")
        survives = ds[0, :] <= max_channel_distance**2
        near = iC.numpy()[:, survives]
        spanning = sum(
            set(xc[near[:, k]]) == {0.0, 103.0} for k in range(near.shape[1])
        )
        return spanning, int(survives.sum())

    at_default = templates_spanning_both_columns(32.0, 32.0)
    at_derived = templates_spanning_both_columns(103.0, 103.0)

    # The measured figures on 2026-08-27, kilosort 4.1.7: (158, 252) and (95, 189).
    assert at_default[0] > 0, (
        "section 7's original claim would predict zero templates spanning both "
        f"columns at the 32 um default; measured {at_default[0]} of {at_default[1]}"
    )
    assert at_default[0] > at_derived[0], (
        "the default bridged the columns on MORE templates than the derived "
        f"spacing: {at_default} vs {at_derived}. If this now fails, the "
        "relationship has changed and spec section 7 needs re-deriving."
    )
