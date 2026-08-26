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
    """At KS4's default of 32 um, no channel in NP1032's first column is ever
    compared with the second, 103 um away -- the mechanism by which a spike
    straddling both COULD split into two units. Confirmed in Kilosort's own
    source; whether it does in practice is narrower (design spec section 7's
    2026-08-26 amendment)."""
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
