import numpy as np

from wl_preproc.ephys.geometry import electrode_rows
from wl_preproc.synth.waveforms import correlated_noise


def test_neighbouring_channels_share_more_noise_than_distant_ones():
    """The property the whole reference question turns on. With independent
    noise every reference mode measures as pure harm, because there is nothing
    common to remove."""
    sites = electrode_rows("NP1032")[:64]
    noise = correlated_noise(sites, n_samples=30_000, sampling_rate_hz=30_000.0,
                             noise_uv=8.0, seed=0)

    corr = np.corrcoef(noise.T)
    near = np.mean([corr[i, i + 2] for i in range(0, 60, 2)])
    far = np.mean([corr[i, i + 40] for i in range(0, 20, 2)])
    assert near > far + 0.1


def test_a_common_median_reference_removes_variance_it_could_not_if_independent():
    sites = electrode_rows("NP1032")[:64]
    noise = correlated_noise(sites, n_samples=30_000, sampling_rate_hz=30_000.0,
                             noise_uv=8.0, seed=1)

    referenced = noise - np.median(noise, axis=1, keepdims=True)
    assert referenced.var() < 0.9 * noise.var()


def test_noise_is_reproducible_from_its_seed():
    sites = electrode_rows("NP1032")[:16]
    kwargs = dict(sites=sites, n_samples=3000, sampling_rate_hz=30_000.0, noise_uv=8.0, seed=7)
    assert np.array_equal(correlated_noise(**kwargs), correlated_noise(**kwargs))


from wl_preproc.synth.units import place_units
from wl_preproc.synth.waveforms import render_traces, unit_templates


def test_a_unit_appears_on_several_channels_with_amplitude_falling_off():
    """The property that makes sorting possible at all, and the one the fixture
    has never had: a spike's spatial footprint."""
    sites = electrode_rows("NP1032")[:64]
    units = place_units(sites, n_units=1, rng=np.random.default_rng(0))
    templates = unit_templates(sites, units, sampling_rate_hz=30_000.0, seed=0)

    peak_per_channel = np.abs(templates[0]).max(axis=0)
    loud = np.flatnonzero(peak_per_channel > 0.2 * peak_per_channel.max())
    assert len(loud) > 1, "a single-channel template is what this task removes"

    unit = units[0]
    distance = np.array(
        [np.hypot(s["x_coord"] - unit.x_um, s["y_coord"] - unit.y_um) for s in sites]
    )
    # A wiring-regression guard, not the falsifiable claim above: under
    # waveforms.TEMPLATE_MODE = "sphere", the loudest channel is provably the
    # nearest one FOR THE ALPHA/SPATIAL_DECAY THIS FIXTURE SHIPS (see
    # TEMPLATE_MODE's own comment for the swept range that guarantee does and
    # does not cover), so a pass here cannot distinguish a correct footprint
    # from an incorrect one -- only catch something like transposed axes or a
    # channel/site mismatch.
    assert distance[int(np.argmax(peak_per_channel))] == distance.min()


def test_rendered_traces_carry_the_planted_spikes_above_the_noise():
    sites = electrode_rows("NP1032")[:64]
    units = place_units(sites, n_units=2, rng=np.random.default_rng(1))
    spikes = ((0.100, 0), (0.200, 1))
    traces = render_traces(
        sites, units, spikes, n_samples=30_000, sampling_rate_hz=30_000.0,
        noise_uv=8.0, seed=2, time_offset_s=0.0, drift_ppm=0.0,
    )

    assert traces.shape == (30_000, 64)
    at_spike = np.abs(traces[2_900:3_100]).max()
    quiet = np.abs(traces[20_000:20_200]).max()
    assert at_spike > 2 * quiet
